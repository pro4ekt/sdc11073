"""
ensemble_topology_manager.py — SLOW path of the Smart Alerting System.
======================================================================
Owns everything about *who belongs to which patient ensemble* and the *slow,
network-bound* work of forming ensembles:

    * ensemble membership          (patient_id, room) → ensemble_uuid → {epr}
    * the FULL per-ensemble sensor registry (every AlertCondition channel,
      alarming or silent) so |M| spans the whole bedside, not just the channels
      currently "shouting";
    * FHIR patient data + danger codes (HL7 FHIR Condition) per patient;
    * the SDC EnsembleContext / WorkflowContext SOAP binding.

This class is deliberately SEPARATE from ``SmartAlertAggregator`` (the fast alarm
processor).  The split solves the God-Object / SRP problem and — crucially — gives
each concern its OWN mutex, so slow topology churn (device bind, MDIB enumeration,
FHIR HTTP) no longer blocks fast alarm ticks for other patients.

Lock discipline
---------------
``self.lock`` (topology lock) guards the membership / registry / FHIR maps ONLY.
It is DISTINCT from ``SmartAlertAggregator.lock`` (processing) and from the
per-aggregator ``_adaptive_lock``.  Slow I/O (FHIR HTTP, SOAP) is always performed
OUTSIDE ``self.lock``.  The Alert Processor calls the read-only snapshot getters
here (``get_member_specs`` / ``get_members`` / ``collect_patient_danger_codes`` /
``reverse_lookup_patient_room``) BEFORE taking its own locks, so the two objects'
locks are never nested → the ordering stays acyclic and deadlock-free.
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Optional, Tuple, Set, Dict, TYPE_CHECKING

from ..fhirData import FHIRPatientData
from .device_profile_repo import get_repository
from .math_types import SensorSpec

# TYPE_CHECKING guard to avoid circular imports when annotating collaborators.
if TYPE_CHECKING:
    from device.handler import DeviceHandler
    from app.sdcMyConsumer import SdcMyConsumer
    from app.patientOverviewModel import PatientOverviewModel


class EnsembleTopologyManager:
    """Manages patient-ensemble topology, the full sensor registry and FHIR data.

    ``overview_model`` is the Qt/QML bridge (``PatientOverviewModel``): a
    thread-safe queue + Signal that updates the patient cards on the dashboard.
    The topology manager pushes a card when a NEW ensemble is formed; the Alert
    Processor pushes status/severity changes.  May be ``None`` (headless / tests).
    """

    def __init__(
        self,
        manager: 'SdcMyConsumer',
        overview_model: Optional['PatientOverviewModel'] = None,
    ) -> None:
        self.logger = logging.getLogger('sdc.consumer.topology')
        self._manager = manager
        self._overview_model = overview_model

        # Single mutex guarding ALL topology structures below (slow path only).
        self.lock: threading.Lock = threading.Lock()

        # Membership: (patient_id, room) → ensemble_uuid
        self._patient_to_ensemble_map: Dict[Tuple[str, str], str] = {}
        # Device registry per ensemble: ensemble_uuid → set(epr)
        self._ensembles_devices: Dict[str, Set[str]] = {}
        # FULL per-ensemble sensor registry: ensemble_uuid → {alert_key → SensorSpec}
        # (every AlertCondition channel of every member device, alarming or not).
        self._ensembles_channel_specs: Dict[str, Dict[str, SensorSpec]] = {}

        # FHIR caches (populated on first access, per patient).
        self._fhir_cache: Dict[str, FHIRPatientData] = {}
        self._fhir_focus_cache: Dict[str, list] = {}
        # Dedicated lock for FHIR fetch — never held during self.lock, and self.lock
        # is never held during an HTTP fetch (may take seconds).
        self._fhir_lock: threading.Lock = threading.Lock()

    # ── Read-only snapshots for the Alert Processor ───────────────────────────

    def get_member_specs(self, ensemble_uuid: str) -> Dict[str, SensorSpec]:
        """Return a snapshot {alert_key → SensorSpec} for EVERY member channel.

        Lazily (re)enumerates member devices if the registry is empty (e.g. the
        bind-time refresh was skipped/raced), so |M| always spans the whole
        ensemble.  Enumeration runs OUTSIDE self.lock (device data_lock is a lower,
        non-blocking lock) and the result is published atomically under self.lock.
        """
        with self.lock:
            specs = self._ensembles_channel_specs.get(ensemble_uuid)
            if specs:
                return dict(specs)
            eprs: Set[str] = set(self._ensembles_devices.get(ensemble_uuid, set()))
        if not eprs:
            return {}
        specs = self._enumerate_member_specs(eprs)
        if specs:
            with self.lock:
                self._ensembles_channel_specs[ensemble_uuid] = specs
        return dict(specs)

    def get_members(self, ensemble_uuid: str) -> Set[str]:
        """Return a snapshot set of member device EPRs for an ensemble."""
        with self.lock:
            return set(self._ensembles_devices.get(ensemble_uuid, set()))

    def get_member_count(self, ensemble_uuid: str) -> int:
        """Return the number of member devices bound to an ensemble."""
        with self.lock:
            return len(self._ensembles_devices.get(ensemble_uuid, set()))

    def reverse_lookup_patient_room(self, ensemble_uuid: str) -> Tuple[str, str]:
        """Return (patient_id, room) for an ensemble UUID, or ('', '')."""
        with self.lock:
            for (pid, room), eid in self._patient_to_ensemble_map.items():
                if eid == ensemble_uuid:
                    return pid, room
        return '', ''

    def collect_patient_danger_codes(self, ensemble_uuid: str) -> Set[str]:
        """Return the normalised FHIR danger codes for this ensemble's patient.

        Feeds the per-ensemble ``Context_Log_Odds`` in the Alert Processor
        (``ClinicalContext.log_odds``).  Empty set if the patient / FHIR data is
        unavailable (fail-open: no clinical sensitisation, never suppresses).
        """
        patient_id: Optional[str] = None
        with self.lock:
            for (pid, _room), eid in self._patient_to_ensemble_map.items():
                if eid == ensemble_uuid:
                    patient_id = pid
                    break

        codes: Set[str] = set()
        if patient_id and patient_id in self._fhir_cache:
            fhir_data = self._fhir_cache[patient_id]
            try:
                for dc in (fhir_data.get_danger_codes() or []):
                    code = dc.get('code', '') or ''
                    system = dc.get('system', '') or ''
                    if not code:
                        continue
                    codes.add(code)
                    if system:
                        codes.add(f'{system}:{code}')
                    if 'snomed' in system.lower():
                        codes.add(f'SNOMED:{code}')
            except Exception as exc:
                self.logger.debug(
                    f'[Topology] collect_patient_danger_codes failed: {exc}'
                )
        return codes

    def get_fhir_focus(self, ensemble_uuid: str) -> list:
        """Return the FHIR clinical-focus rules for this ensemble's patient."""
        patient_id: Optional[str] = None
        with self.lock:
            for (pid, _room), eid in self._patient_to_ensemble_map.items():
                if eid == ensemble_uuid:
                    patient_id = pid
                    break
        if patient_id:
            return self._fhir_focus_cache.get(patient_id, [])
        return []

    # ── Sensor registry construction ──────────────────────────────────────────

    def _enumerate_member_specs(self, eprs: Set[str]) -> Dict[str, SensorSpec]:
        """Build {alert_key → SensorSpec} from EVERY alert channel of the given
        member devices (alarming or silent).

        w_j comes from the pre-fetched reliability profile (neutral 0.5/0.5 →
        w_j = 0 if absent), P_j from the BICEPS priority.  The per-device MDIB read
        uses a NON-BLOCKING lock, so this is safe to call inside or outside
        self.lock (device data_lock is a distinct lower lock → acyclic).
        """
        manager_devices: dict = getattr(self._manager, 'devices', {})
        repo = get_repository()
        specs: Dict[str, SensorSpec] = {}
        for epr in eprs:
            handler = manager_devices.get(epr)
            # ``enumerate_method`` (not ``enum``) to avoid shadowing the stdlib
            # ``enum`` module.
            enumerate_method = getattr(handler, 'enumerate_alert_channels', None)
            if enumerate_method is None:
                continue
            try:
                channels = enumerate_method()
            except Exception as exc:  # best-effort; a busy device must not break it
                self.logger.debug(
                    f'[Topology] enumerate_alert_channels failed for {epr[-12:]}: {exc}'
                )
                continue
            for alert_key, _metric_concept, biceps_priority, prof in channels:
                tpr = prof.true_positive_rate if prof is not None else 0.5
                fpr = prof.false_positive_rate if prof is not None else 0.5
                specs[alert_key] = SensorSpec(
                    sensor_id=alert_key,
                    tpr=tpr,
                    fpr=fpr,
                    priority=repo.get_priority(biceps_priority),
                )
        return specs

    def _refresh_ensemble_channel_specs(self, ensemble_uuid: str) -> None:
        """Rebuild the FULL {alert_key → SensorSpec} registry for an ensemble.

        Enumerates every AlertCondition channel of every member device so |M|
        reflects the true number of connected sensors (fix for the |M|=1 /
        SDC_score=1.0 bug).  Member EPRs read under self.lock; enumeration OUTSIDE
        self.lock; registry published atomically under self.lock.
        """
        with self.lock:
            eprs: Set[str] = set(self._ensembles_devices.get(ensemble_uuid, set()))
        if not eprs:
            return
        specs = self._enumerate_member_specs(eprs)
        if not specs:
            return
        with self.lock:
            self._ensembles_channel_specs[ensemble_uuid] = specs
        self.logger.info(
            f'[Topology] Registered {len(specs)} sensor channel(s) for ensemble '
            f'{ensemble_uuid[:8]} — |M| now spans all connected devices.'
        )

    # ── Teardown ──────────────────────────────────────────────────────────────

    def release_device(self, epr: str) -> Optional[str]:
        """Remove ``epr`` from ensemble bookkeeping.

        If it was the LAST member of its ensemble, tears down the ensemble's
        topology state (membership, registry) and evicts the patient's FHIR caches
        when the patient has no other ensemble.

        Returns the released ensemble UUID (so the caller can discard the matching
        Alert-Processor state), or ``None`` if the ensemble still has members.

        Lock discipline: all mutations under self.lock; no adaptive/processor lock
        is taken here (that is the Alert Processor's responsibility via
        ``discard_ensemble``).
        """
        if not epr:
            return None

        released_ensemble_uuid: Optional[str] = None
        inactive_patient_id: Optional[str] = None

        with self.lock:
            for eid, eprs in list(self._ensembles_devices.items()):
                if epr in eprs:
                    eprs.discard(epr)
                    if not eprs:
                        released_ensemble_uuid = eid
                        del self._ensembles_devices[eid]
                        self._ensembles_channel_specs.pop(eid, None)
                        for key, mapped in list(self._patient_to_ensemble_map.items()):
                            if mapped == eid:
                                inactive_patient_id = key[0]
                                del self._patient_to_ensemble_map[key]
                                break
                    break

            if inactive_patient_id and not any(
                key[0] == inactive_patient_id for key in self._patient_to_ensemble_map
            ):
                self._fhir_cache.pop(inactive_patient_id, None)
                self._fhir_focus_cache.pop(inactive_patient_id, None)

        if released_ensemble_uuid is not None:
            self.logger.info(
                f'[Topology] release_device: ensemble {released_ensemble_uuid[:8]}... '
                f'fully released (last device {epr[-12:]} disconnected).'
            )
        else:
            self.logger.debug(
                f'[Topology] release_device: {epr[-12:]} removed; ensemble still '
                f'has other members (or device was never bound).'
            )
        return released_ensemble_uuid

    # ── PatientOverview bridge ────────────────────────────────────────────────

    def _notify_overview(
        self,
        ensemble_uuid: str,
        patient_id: str,
        room: str,
        is_escalated: bool,
        sdc_score: float,
        is_warning: bool = False,
    ) -> None:
        """Push an ensemble summary to PatientOverviewModel (thread-safe). No-op
        when ``_overview_model`` is None."""
        if self._overview_model is None:
            return
        device_count = self.get_member_count(ensemble_uuid)
        try:
            self._overview_model.updateEnsemble(
                ensemble_uuid, patient_id, room, device_count,
                is_escalated, sdc_score, is_warning,
            )
        except Exception as exc:
            self.logger.debug(f'[PatientOverview] updateEnsemble failed: {exc}')

    # ── MDIB context extraction ───────────────────────────────────────────────

    def _extract_patient_and_room(
        self, device_handler: 'DeviceHandler'
    ) -> Tuple[Optional[str], Optional[str]]:
        """Extract (patient_id, room) from the device's MDIB.

        Room from LocationContextState; patient id primarily from
        WorkflowContextState, falling back to PatientContextState.  Executed while
        holding ``device_handler.data_lock``.
        """
        from sdc11073.xml_types import pm_qnames as pm

        patient_id: Optional[str] = None
        room: Optional[str] = None

        with device_handler.data_lock:
            if not device_handler.mdib:
                self.logger.debug(
                    f'[Topology] _extract_patient_and_room: MDIB not ready for '
                    f'{device_handler.epr[-12:]}'
                )
                return None, None

            # -- 1. Room ----------------------------------------------------------
            try:
                loc_states = device_handler.mdib.context_states.NODETYPE.get(
                    pm.LocationContextState, []
                )
                if loc_states and loc_states[0].LocationDetail:
                    raw_room = loc_states[0].LocationDetail.Room
                    room = str(raw_room) if raw_room else None
            except Exception as exc:
                self.logger.warning(
                    f'[Topology] Error reading LocationContextState '
                    f'for {device_handler.epr[-12:]}: {exc}'
                )

            # -- 2. Patient ID -- primary: WorkflowContextState -------------------
            try:
                wf_states = device_handler.mdib.context_states.NODETYPE.get(
                    pm.WorkflowContextState, []
                )
                for wf_state in wf_states:
                    wd = getattr(wf_state, 'WorkflowDetail', None)
                    if not wd:
                        continue
                    pat = getattr(wd, 'Patient', None)
                    if not pat:
                        continue
                    identifications = getattr(pat, 'Identification', None) or []
                    for ident in identifications:
                        ext = getattr(ident, 'Extension', None)
                        if ext:
                            patient_id = str(ext)
                            break
                        id_names = getattr(ident, 'IdentifierName', None) or []
                        if id_names:
                            raw = id_names[0] if isinstance(id_names, list) else id_names
                            text = getattr(raw, 'text', None) or str(raw)
                            if text:
                                patient_id = text
                                break
                    if patient_id:
                        break
            except Exception as exc:
                self.logger.warning(
                    f'[Topology] Error reading WorkflowContextState '
                    f'for {device_handler.epr[-12:]}: {exc}'
                )

            # -- 3. Patient ID -- fallback: PatientContextState -------------------
            if not patient_id:
                try:
                    pat_states = device_handler.mdib.context_states.NODETYPE.get(
                        pm.PatientContextState, []
                    )
                    for pat_state in pat_states:
                        identifications = getattr(pat_state, 'Identification', None) or []
                        for ident in identifications:
                            ext = getattr(ident, 'Extension', None)
                            if ext:
                                patient_id = str(ext)
                                break
                            id_names = getattr(ident, 'IdentifierName', None) or []
                            if id_names:
                                raw = id_names[0] if isinstance(id_names, list) else id_names
                                text = getattr(raw, 'text', None) or str(raw)
                                if text:
                                    patient_id = text
                                    break
                        if patient_id:
                            break
                    if not patient_id:
                        for pat_state in pat_states:
                            core = getattr(pat_state, 'CoreData', None)
                            if not core:
                                continue
                            given = getattr(core, 'Givenname', None) or ''
                            family = getattr(core, 'Familyname', None) or ''
                            name = f'{given} {family}'.strip()
                            if name:
                                patient_id = name
                                break
                except Exception as exc:
                    self.logger.warning(
                        f'[Topology] Error reading PatientContextState '
                        f'for {device_handler.epr[-12:]}: {exc}'
                    )

        self.logger.debug(
            f'[Topology] Extracted for {device_handler.epr[-12:]}: '
            f'patient_id={patient_id!r}, room={room!r}'
        )
        return patient_id, room

    # ── FHIR fetch ────────────────────────────────────────────────────────────

    def _get_or_fetch_fhir_data(self, patient_id: str) -> Optional[FHIRPatientData]:
        """Return cached FHIRPatientData for ``patient_id`` or fetch it once.

        Double-checked locking via ``_fhir_lock`` prevents duplicate HTTP requests
        when several devices for the same patient connect simultaneously.
        ``self.lock`` MUST NOT be held by the caller (the fetch may take seconds).
        """
        if patient_id in self._fhir_cache:
            return self._fhir_cache[patient_id]

        with self._fhir_lock:
            if patient_id in self._fhir_cache:
                return self._fhir_cache[patient_id]

            self.logger.info(
                f'[Topology] FHIR cache miss -- fetching for patient_id={patient_id!r}...'
            )
            try:
                fhir_data = FHIRPatientData()
                fhir_data.fetch(patient_id)
                self._fhir_cache[patient_id] = fhir_data

                try:
                    fhir_focus = fhir_data.get_clinical_focus()
                    self._fhir_focus_cache[patient_id] = fhir_focus
                    if fhir_focus:
                        self.logger.info(
                            f'[Topology] FHIR clinical focus: {len(fhir_focus)} rule(s) '
                            f'for patient_id={patient_id!r}.'
                        )
                except Exception as focus_exc:
                    self._fhir_focus_cache[patient_id] = []
                    self.logger.warning(
                        f'[Topology] FHIR clinical focus extraction failed for '
                        f'patient_id={patient_id!r}: {focus_exc}'
                    )

                self.logger.info(
                    f'[Topology] FHIR data cached for patient_id={patient_id!r}.'
                )
                return fhir_data
            except Exception as exc:
                self.logger.error(
                    f'[Topology] FHIR fetch failed for patient_id={patient_id!r}: {exc}. '
                    f'Proceeding without FHIR data.'
                )
                return None

    # ── Device binding (entry point) ──────────────────────────────────────────

    def evaluate_and_bind_device(self, device_handler: 'DeviceHandler') -> None:
        """Entry point for a newly connected device: form or join an ensemble.

        1. Extract (patient_id, room) from the MDIB.
        2. Under self.lock, look up / create the ensemble and add the device EPR.
        3. OUTSIDE self.lock: refresh the full sensor registry, fetch FHIR data,
           send the EnsembleContext SOAP call, then apply FHIR contexts.
        """
        patient_id, room = self._extract_patient_and_room(device_handler)

        if not patient_id or not room:
            self.logger.info(
                f'[Topology] Device {device_handler.epr[-12:]} skipped -- '
                f'incomplete context: patient_id={patient_id!r}, room={room!r}.'
            )
            return

        key: Tuple[str, str] = (patient_id, room)
        ensemble_uuid: str

        with self.lock:
            if key in self._patient_to_ensemble_map:
                ensemble_uuid = self._patient_to_ensemble_map[key]
                self.logger.info(
                    f'[Topology] Device {device_handler.epr[-12:]} joining existing '
                    f'ensemble {ensemble_uuid[:8]}... (patient={patient_id}, room={room})'
                )
            else:
                ensemble_uuid = str(uuid.uuid4())
                self._patient_to_ensemble_map[key] = ensemble_uuid
                self._ensembles_devices[ensemble_uuid] = set()
                self.logger.info(
                    f'[Topology] New ensemble {ensemble_uuid[:8]}... created '
                    f'for patient={patient_id}, room={room}'
                )

            self._ensembles_devices[ensemble_uuid].add(device_handler.epr)
            member_count = len(self._ensembles_devices[ensemble_uuid])

            # Set the UUID on the worker under the lock so it is present before the
            # SOAP call, even if that call fails (idempotent, overwritten on success).
            device_handler.ensemble_uuid = ensemble_uuid

        self.logger.debug(
            f'[Topology] Ensemble {ensemble_uuid[:8]}... now has {member_count} '
            f'member(s). Sending context to {device_handler.epr[-12:]}...'
        )

        # Register the FULL sensor ensemble (all channels, alarming or silent) so
        # the math core's |M| spans every connected channel. Runs outside self.lock.
        self._refresh_ensemble_channel_specs(ensemble_uuid)

        # FHIR fetch — outside self.lock (HTTP round-trip; may be slow).
        fhir_data = self._get_or_fetch_fhir_data(patient_id)

        # Network SOAP call — outside self.lock.
        success = device_handler.apply_ensemble_context(ensemble_uuid)
        if success:
            self.logger.info(
                f'[Topology] EnsembleContext {ensemble_uuid[:8]}... applied to '
                f'{device_handler.epr[-12:]}.'
            )
            # Notify PatientOverview: new/updated ensemble, not yet escalated.
            self._notify_overview(ensemble_uuid, patient_id, room or '',
                                  is_escalated=False, sdc_score=0.0, is_warning=False)
        else:
            device_handler.ensemble_uuid = None
            with self.lock:
                self._ensembles_devices[ensemble_uuid].discard(device_handler.epr)
            self.logger.warning(
                f'[Topology] Failed to apply EnsembleContext to '
                f'{device_handler.epr[-12:]}. Rolled back local binding.'
            )

        # Apply FHIR patient/clinical context (demographics, danger codes) — always,
        # regardless of ensemble binding outcome (FHIR is independent of SDC state).
        device_handler.apply_fhir_contexts(fhir_data)