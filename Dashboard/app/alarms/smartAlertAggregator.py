import logging
import threading
import time
import uuid
from collections import deque
from typing import Optional, Tuple, Set, Dict

from ..fhirData import FHIRPatientData
from .alarmCoordinator import AlarmCoordinator, DeviceAlertEvidence
from .device_profile_repo import DeviceReliabilityProfile

# TYPE_CHECKING guard to avoid circular imports when annotating DeviceHandler
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.deviceHandler import DeviceHandler

class SmartAlertAggregator:
    """
    Core of the Smart Alerting System for the SDC Orchestrator.
    Phase 1: Dynamic EnsembleContext formation based on device topology and patient data.
    Phase 2: (Future) Cross-device alarm validation within an ensemble.
    """

    def __init__(self, manager, overview_model=None):
        self.logger = logging.getLogger('sdc.consumer.aggregator')
        self._manager = manager  # Reference to SdcMyConsumer
        # PatientOverviewModel (QObject) — optional; None in unit-test / CLI contexts.
        self._overview_model = overview_model

        # Mutex protecting the aggregator's internal data structures
        self.lock = threading.Lock()

        # State Table: (patient_id, room_id) -> ensemble_uuid
        self._active_ensembles: Dict[Tuple[str, str], str] = {}

        # Device registry per ensemble: ensemble_uuid -> set(epr)
        self._ensemble_devices: Dict[str, Set[str]] = {}

        # FHIR cache: patient_id -> FHIRPatientData (populated on first access)
        self._fhir_cache: Dict[str, FHIRPatientData] = {}

        # FHIR clinical focus cache: patient_id -> list of clinical focus entries
        # extracted from Condition Extension elements.  Populated alongside
        # _fhir_cache and used as a per-patient override of rules.json.
        self._fhir_focus_cache: Dict[str, list] = {}

        # In-Memory physiological state graph, indexed by semantic concept codes.
        # Structure: ensemble_uuid -> concept_code -> deque[(value, timestamp), ...]
        # Each deque is a sliding window of the last 15 readings (maxlen=15).
        # The SignalProcessor reads snapshots of this graph to compute dx/dt.
        self._physiological_graph: Dict[str, Dict[str, deque]] = {}

        # Active alarm cache (TTL = ALARM_TTL_SEC).
        # Structure: ensemble_uuid -> alert_key -> (DeviceAlertEvidence, timestamp)
        # Each fired alarm is registered here so ensemble_evidences for Stage 2
        # contains ALL currently active alarms across ALL devices in the ensemble,
        # not just the triggering alarm.  Entries older than ALARM_TTL_SEC are
        # garbage-collected on each check_alert_validity call.
        self._active_alarms: Dict[str, Dict[str, Tuple[DeviceAlertEvidence, float]]] = {}
        self.ALARM_TTL_SEC: float = 10.0

        # Ensemble-level escalation state.
        # When the Bayesian pipeline returns ESCALATE for any alarm in an ensemble,
        # the ensemble UUID is added here.  While present, ALL alarms in the ensemble
        # bypass individual suppression and show as ON in the UI.
        # Cleared automatically when _active_alarms[ensemble_uuid] becomes empty
        # (all alarms expired from the TTL cache → crisis resolved).
        self._escalated_ensembles: Set[str] = set()

        # Timestamp of the last console graph dump (monotonic).
        # Used to rate-limit dump output: at most once per GRAPH_DUMP_INTERVAL_SEC.
        self._last_graph_dump_ts: float = 0.0
        self.GRAPH_DUMP_INTERVAL_SEC: float = 2.0

        # DSP signal processor — stateless, no extra locking needed.
        self.alarm_coordinator = AlarmCoordinator()

        # Background Watchdog GC — runs every ALARM_TTL_SEC/2 seconds.
        # Independently clears stale _active_alarms entries and notifies UI
        # even when no SDC alarm packets arrive (device went silent).
        self._stop_gc = threading.Event()
        self._gc_thread = threading.Thread(
            target=self._gc_loop, daemon=True, name='AggregatorGC'
        )
        self._gc_thread.start()

    def update_metric_state(self, ensemble_uuid: str, concept_code: str, value: float) -> None:
        """
        Appends a metric reading into the sliding-window deque for the given
        ensemble and semantic concept code.

        Each entry in the deque is a (value, timestamp) tuple.
        The deque is bounded to 15 elements (maxlen=15) — older readings are
        automatically discarded, keeping memory usage constant.

        Thread safety: protected by self.lock.
        Called from DeviceHandler.on_metric_update() (sdc11073 notification thread).
        """
        with self.lock:
            if ensemble_uuid not in self._physiological_graph:
                self._physiological_graph[ensemble_uuid] = {}
                # self.logger.info(
                #     f'[PhysGraph] New ensemble entry created: {ensemble_uuid[:8]}...'
                # )
            if concept_code not in self._physiological_graph[ensemble_uuid]:
                self._physiological_graph[ensemble_uuid][concept_code] = deque(maxlen=15)
            self._physiological_graph[ensemble_uuid][concept_code].append(
                (value, time.time())
            )
        # self.logger.debug(
        #     f'[PhysGraph] Write: ensemble={ensemble_uuid[:8]} '
        #     f'concept={concept_code!r} value={value:.4g}'
        # )

        # -- Rate-limited graph dump (DEBUG only — not shown at INFO level) ----
        now = time.monotonic()
        if now - self._last_graph_dump_ts >= self.GRAPH_DUMP_INTERVAL_SEC:
            self._last_graph_dump_ts = now
            # self.logger.debug(self.dump_physiological_graph())

    def get_metric_state(
        self,
        ensemble_uuid: str,
        concept_code: str,
        max_age_sec: float = 15.0,
    ) -> Optional[float]:
        """
        Returns the most recent cached value for the given ensemble and concept
        code (last element of the deque), or None if stale / absent.

        Thread safety: protected by self.lock.
        """
        with self.lock:
            buf: Optional[deque] = (
                self._physiological_graph
                .get(ensemble_uuid, {})
                .get(concept_code)
            )
            if not buf:
                # self.logger.debug(
                #     f'[PhysGraph] Miss: ensemble={ensemble_uuid[:8]} '
                #     f'concept={concept_code!r} (no entry)'
                # )
                return None
            value, timestamp = buf[-1]
            age = time.time() - timestamp
            if age > max_age_sec:
                # self.logger.warning(
                #     f'[PhysGraph] Stale: ensemble={ensemble_uuid[:8]} '
                #     f'concept={concept_code!r} age={age:.1f}s > max={max_age_sec}s — returning None'
                # )
                return None
            # self.logger.debug(
            #     f'[PhysGraph] Hit:  ensemble={ensemble_uuid[:8]} '
            #     f'concept={concept_code!r} value={value:.4g} age={age:.1f}s'
            # )
            return value

    def dump_physiological_graph(self) -> str:
        """
        Returns a human-readable snapshot of the physiological graph.
        Each row shows the latest reading from the deque (newest element).

        Thread safety: protected by self.lock.
        """
        lines: list[str] = ['[PhysGraph] ── Snapshot ──────────────────────────────']
        with self.lock:
            if not self._physiological_graph:
                lines.append('[PhysGraph]   (empty)')
            for ens_uuid, concepts in self._physiological_graph.items():
                now = time.time()
                lines.append(f'[PhysGraph]   Ensemble {ens_uuid[:8]}...')
                for code, buf in sorted(concepts.items()):
                    if not buf:
                        lines.append(f'[PhysGraph]     {code:<30} = (empty buffer)')
                        continue
                    value, timestamp = buf[-1]
                    age = now - timestamp
                    stale = ' ⚠ STALE' if age > 15.0 else ''
                    lines.append(
                        f'[PhysGraph]     {code:<30} = {value:>10.4g}'
                        f'  (age={age:.1f}s, buf={len(buf)}/15{stale})'
                    )
        lines.append('[PhysGraph] ────────────────────────────────────────────────')
        return '\n'.join(lines)

    def _collect_patient_danger_codes(self, ensemble_uuid: str) -> Set[str]:
        """
        Helper: collect and normalise FHIR danger codes for the patient bound to
        ensemble_uuid.  MUST be called with self.lock already held.

        Returns an empty set if the patient or FHIR data is unavailable.
        """
        patient_danger_codes: Set[str] = set()

        # Reverse-lookup: ensemble_uuid → patient_id
        patient_id: Optional[str] = None
        for (pid, _room), eid in self._active_ensembles.items():
            if eid == ensemble_uuid:
                patient_id = pid
                break

        if patient_id and patient_id in self._fhir_cache:
            fhir_data = self._fhir_cache[patient_id]
            try:
                raw_codes = fhir_data.get_danger_codes() or []
                for dc in raw_codes:
                    code   = dc.get('code', '') or ''
                    system = dc.get('system', '') or ''
                    if not code:
                        continue
                    patient_danger_codes.add(code)
                    if system:
                        patient_danger_codes.add(f'{system}:{code}')
                    if 'snomed' in system.lower():
                        patient_danger_codes.add(f'SNOMED:{code}')
            except Exception as _exc:
                self.logger.debug(
                    f'[Aggregator] _collect_patient_danger_codes: '
                    f'FHIR code extraction failed: {_exc}'
                )

        return patient_danger_codes

    def _collect_fhir_focus(self, ensemble_uuid: str) -> list:
        """
        Helper: returns FHIR clinical focus rules for the patient bound to
        ensemble_uuid, from _fhir_focus_cache.
        MUST be called with self.lock already held.

        Returns empty list if no FHIR focus was loaded (rules.json used as fallback).
        """
        patient_id: Optional[str] = None
        for (pid, _room), eid in self._active_ensembles.items():
            if eid == ensemble_uuid:
                patient_id = pid
                break
        if patient_id:
            return self._fhir_focus_cache.get(patient_id, [])
        return []

    def check_alert_validity(
        self,
        ensemble_uuid: str,
        alert_key: str,
        metric_concept: str,
        biceps_priority: str = 'Hi',
        manufacturer: str = '',
        model: str = '',
        device_epr: str = '',
        reliability_profile: Optional[DeviceReliabilityProfile] = None,
        roc_limit: Optional[float] = None,
    ) -> bool:
        """
        Two-stage alarm pipeline gate for an active alarm (IHE-PCD ACM Alarm Coordinator).

        Stage 1 — HardwareArtifactFilter:
            Retrieves the metric sliding-window buffer for *metric_concept* from
            _physiological_graph, copies it under the lock, and delegates to
            HardwareArtifactFilter.validate() for dx/dt analysis.
            The RoC limit is taken from *roc_limit* (pre-fetched by DeviceHandler).

        Stage 2 — ClinicalRiskFilter (Bayesian Sensor Fusion):
            Constructs DeviceAlertEvidence for every device in the ensemble and
            computes a BICEPS-scaled risk score via the product of Likelihood Ratios.
            The LR+ values come from *reliability_profile* embedded in each evidence
            object — this class never queries the database directly.
            Escalates only when risk_score ≥ ESCALATION_THRESHOLD (5.0).

        Parameters
        ----------
        ensemble_uuid        : UUID of the ensemble containing the device.
        alert_key            : Alarm handle (for log messages).
        metric_concept       : LOINC/MDC code of the metric associated with this alarm.
                               If empty the filter always passes through (fail-open).
        biceps_priority      : BICEPS AlertCondition.Priority — 'Hi'|'Me'|'Lo'|'None'.
        manufacturer         : DPWS Manufacturer string (retained for logging).
        model                : DPWS ModelName string (retained for logging).
        device_epr           : EPR of the triggering device.
        reliability_profile  : Pre-fetched DeviceReliabilityProfile (for Stage 2 LR+).
                               None → fail-safe profile used (LR+ = 1.0, neutral).
        roc_limit            : Pre-fetched dx/dt limit (for Stage 1).
                               None → fail-open (concept not calibrated).

        Returns
        -------
        bool
            True  — AlarmDecision.escalate → forward alarm.
            False — pipeline suppressed the alarm (artifact or low risk score).
        """
        if not metric_concept:
            return True  # no metric mapping → fail-open

        now = time.time()

        with self.lock:
            # ── Early return: ensemble already escalated ──────────────────────────
            # Once a crisis is confirmed, every subsequent alarm in the same
            # ensemble must also show as ON — do not re-run the suppression
            # pipeline until all alarms clear.
            if ensemble_uuid in self._escalated_ensembles:
                if ensemble_uuid not in self._active_alarms:
                    self._active_alarms[ensemble_uuid] = {}
                ev = DeviceAlertEvidence(
                    alert_key=alert_key,
                    metric_concept=metric_concept,
                    manufacturer=manufacturer,
                    model=model,
                    ensemble_uuid=ensemble_uuid,
                    biceps_priority=biceps_priority,
                    reliability_profile=reliability_profile,
                    roc_limit=roc_limit,
                )
                self._active_alarms[ensemble_uuid][alert_key] = (ev, now)
                return True

            buf: Optional[deque] = (
                self._physiological_graph
                .get(ensemble_uuid, {})
                .get(metric_concept)
            )
            if buf is None:
                return True  # no metric history yet → fail-open
            buf_snapshot = deque(buf, maxlen=buf.maxlen)

            # ── Active alarm cache (TTL) ──────────────────────────────────────
            if ensemble_uuid not in self._active_alarms:
                self._active_alarms[ensemble_uuid] = {}

            triggering_evidence = DeviceAlertEvidence(
                alert_key=alert_key,
                metric_concept=metric_concept,
                manufacturer=manufacturer,
                model=model,
                ensemble_uuid=ensemble_uuid,
                biceps_priority=biceps_priority,
                reliability_profile=reliability_profile,
                roc_limit=roc_limit,
            )
            self._active_alarms[ensemble_uuid][alert_key] = (triggering_evidence, now)

            # TTL-GC is now handled exclusively by the background _gc_loop.
            # Removing stale entries here was unreachable for escalated ensembles
            # (early-return above) and caused Bug A + Bug B. Watchdog fixes both.

            ensemble_evidences: list[DeviceAlertEvidence] = [
                ev for ev, _ts in self._active_alarms[ensemble_uuid].values()
            ]

        decision = self.alarm_coordinator.evaluate(
            triggering_evidence, buf_snapshot, ensemble_evidences
        )

        # ── Escalation propagation ────────────────────────────────────────────
        # When the pipeline first confirms a crisis, mark the ensemble escalated
        # and immediately clear pipeline-suppression on ALL member devices so the
        # UI shows all 3 alarms as ON, not just the last-evaluated one.
        if decision.escalate:
            with self.lock:
                self._escalated_ensembles.add(ensemble_uuid)
            self._propagate_escalation_to_devices(ensemble_uuid)
            # Notify PatientOverview: crisis confirmed
            _patient_id, _room = self._reverse_lookup_patient_room(ensemble_uuid)
            self._notify_overview(ensemble_uuid, _patient_id, _room, True, decision.risk_score)
        else:
            # If the ensemble was previously escalated and is now suppressing again,
            # this path is reached only when ensemble is NOT in _escalated_ensembles
            # (early-return above would have fired). No state change needed here.
            pass

        return decision.escalate

    def is_ensemble_escalated(self, ensemble_uuid: Optional[str]) -> bool:
        """Return True if the ensemble is currently in an escalated (crisis) state."""
        if not ensemble_uuid:
            return False
        with self.lock:
            return ensemble_uuid in self._escalated_ensembles

    def clear_alarm(self, ensemble_uuid: Optional[str], alert_key: str) -> None:
        """
        Explicitly remove a cleared alarm from the TTL cache.

        Called by DeviceHandler.on_alert_update() when an AlertConditionState
        transitions to Presence=False (alarm OFF).

        Without this, a suppressed alarm that goes OFF would stay in
        _active_alarms for up to ALARM_TTL_SEC (10 s).  During that window, a
        new alarm on another device would wrongly include the dead alarm in the
        Bayesian ensemble fusion, artificially inflating the risk score.

        Side-effect: if removing this alarm empties _active_alarms for the
        ensemble, the ensemble is removed from _escalated_ensembles (crisis
        fully resolved).

        Thread safety: protected by self.lock.
        """
        if not ensemble_uuid:
            return

        # Capture resolution data outside the lock so that _reverse_lookup_patient_room
        # and _notify_overview (both acquire self.lock internally) are called after
        # the with-block exits.  threading.Lock() is NOT reentrant — calling them
        # inside the lock causes a deadlock that silently swallows the notify call.
        crisis_resolved = False
        _patient_id = ''
        _room = ''

        with self.lock:
            ensemble_cache = self._active_alarms.get(ensemble_uuid)
            if ensemble_cache and alert_key in ensemble_cache:
                del ensemble_cache[alert_key]
                self.logger.debug(
                    f'[ActiveAlarms] Cleared (alarm OFF): ensemble={ensemble_uuid[:8]} '
                    f'alert={alert_key!r}'
                )
            # If all alarms for this ensemble are now gone → crisis resolved
            if not self._active_alarms.get(ensemble_uuid):
                if ensemble_uuid in self._escalated_ensembles:
                    self._escalated_ensembles.discard(ensemble_uuid)
                    crisis_resolved = True
                    self.logger.info(
                        f'[Escalation] Crisis resolved: ensemble={ensemble_uuid[:8]} '
                        f'— all alarms cleared, escalation state reset.'
                    )
                    # Inline reverse-lookup while lock is held (avoids re-entrant acquire).
                    for (pid, room), eid in self._active_ensembles.items():
                        if eid == ensemble_uuid:
                            _patient_id, _room = pid, room
                            break

        # Notify PatientOverview outside the lock — _notify_overview acquires self.lock
        # for device_count; calling it inside would re-enter and deadlock.
        if crisis_resolved:
            self._notify_overview(ensemble_uuid, _patient_id, _room, False, 0.0)

    def _propagate_escalation_to_devices(self, ensemble_uuid: str) -> None:
        """
        When the Bayesian pipeline first confirms a crisis, propagate the
        escalation state to every device in the ensemble.

        Problem solved:
          Alarms arrive sequentially (device A → B → C).  A and B are evaluated
          before the ensemble is complete and get suppressed.  C triggers ESCALATE
          but A and B still have their handles in _pipeline_suppressed, so only
          device C shows red in the UI.

        Fix:
          Clear _pipeline_suppressed on all member devices and schedule a UI
          refresh.  After the refresh, update_data() sees an empty suppressed set
          and shows all alarms as ON.

        Thread safety:
          Called outside self.lock (propagation happens after evaluate()).
          _pipeline_suppressed.clear() is GIL-safe (CPython set operation).
          scheduleUpdate() is a Qt cross-thread signal emit — always safe.
        """
        with self.lock:
            eprs: set[str] = set(self._ensemble_devices.get(ensemble_uuid, set()))

        manager_devices: dict = getattr(self._manager, 'devices', {})
        self.logger.info(
            f'[Escalation] Propagating to {len(eprs)} device(s) in '
            f'ensemble {ensemble_uuid[:8]}... — clearing pipeline suppression.'
        )
        for epr in eprs:
            handler = manager_devices.get(epr)
            if handler is None:
                continue
            suppressed: Optional[set] = getattr(handler, '_pipeline_suppressed', None)
            if suppressed is not None:
                suppressed.clear()
                self.logger.debug(
                    f'[Escalation]   Cleared _pipeline_suppressed on device {epr[-12:]}'
                )
            qt_h = getattr(handler, 'qtDeviceHandler', None)
            if qt_h is not None:
                try:
                    qt_h.scheduleUpdate()
                except Exception as exc:
                    self.logger.debug(
                        f'[Escalation]   scheduleUpdate failed for {epr[-12:]}: {exc}'
                    )

    # ── PatientOverviewModel bridge ───────────────────────────────────────────

    def _reverse_lookup_patient_room(self, ensemble_uuid: str) -> tuple[str, str]:
        """Return (patient_id, room) for an ensemble UUID. Thread-safe."""
        with self.lock:
            for (pid, room), eid in self._active_ensembles.items():
                if eid == ensemble_uuid:
                    return pid, room
        return '', ''

    def _notify_overview(
        self,
        ensemble_uuid: str,
        patient_id: str,
        room: str,
        is_escalated: bool,
        risk_score: float,
    ) -> None:
        """
        Push an ensemble summary to PatientOverviewModel (thread-safe via its
        internal queue + Signal bridge).  No-op when _overview_model is None.
        """
        if self._overview_model is None:
            return
        with self.lock:
            device_count = len(self._ensemble_devices.get(ensemble_uuid, set()))
        try:
            self._overview_model.updateEnsemble(
                ensemble_uuid,
                patient_id,
                room,
                device_count,
                is_escalated,
                risk_score,
            )
        except Exception as exc:
            self.logger.debug(f'[PatientOverview] updateEnsemble failed: {exc}')

    # ── Background Watchdog GC ────────────────────────────────────────────────

    def _gc_loop(self) -> None:
        """
        Daemon thread: wakes every ALARM_TTL_SEC/2 seconds and calls
        _cleanup_stale_alarms(). Runs until _stop_gc is set (on shutdown).
        """
        interval = self.ALARM_TTL_SEC / 2.0
        while not self._stop_gc.wait(timeout=interval):
            try:
                self._cleanup_stale_alarms()
            except Exception as exc:
                self.logger.debug(f'[AggregatorGC] Unhandled exception: {exc}')

    def _cleanup_stale_alarms(self) -> None:
        """
        Scans ALL ensembles in _active_alarms and removes entries whose
        timestamp is older than ALARM_TTL_SEC.

        If removing stale entries leaves an ensemble empty:
          - deletes the key from _active_alarms
          - removes the UUID from _escalated_ensembles
          - calls _notify_overview(..., False, 0.0) to reset the UI card

        Thread safety: protected by self.lock throughout.
        Called from both the GC daemon thread and (legacy) check_alert_validity.
        """
        now = time.time()
        resolved_ensembles: list[str] = []

        with self.lock:
            for ensemble_uuid, alarm_cache in list(self._active_alarms.items()):
                stale_keys = [
                    k for k, (_, ts) in alarm_cache.items()
                    if now - ts > self.ALARM_TTL_SEC
                ]
                for k in stale_keys:
                    del alarm_cache[k]
                    self.logger.debug(
                        f'[AggregatorGC] TTL expired: ensemble={ensemble_uuid[:8]} '
                        f'alert={k!r} — removed.'
                    )

                if not alarm_cache:
                    del self._active_alarms[ensemble_uuid]
                    if ensemble_uuid in self._escalated_ensembles:
                        self._escalated_ensembles.discard(ensemble_uuid)
                        resolved_ensembles.append(ensemble_uuid)
                        self.logger.info(
                            f'[AggregatorGC] Crisis resolved: ensemble={ensemble_uuid[:8]} '
                            f'— all alarms TTL-expired, escalation cleared.'
                        )

        # Notify UI outside the lock (updateEnsemble enqueues into queue.Queue).
        for ensemble_uuid in resolved_ensembles:
            patient_id, room = self._reverse_lookup_patient_room(ensemble_uuid)
            self._notify_overview(ensemble_uuid, patient_id, room, False, 0.0)

    def stop(self) -> None:
        """Signal the GC daemon thread to exit. Call on application shutdown."""
        self._stop_gc.set()

    def check_alert_priority(
        self,
        ensemble_uuid: Optional[str],
        alert_concept: Optional[str],
    ) -> bool:
        """
        Rule-engine priority check — disabled (ruleEvaluator removed).
        Returns False; use the DSP validity gate (check_alert_validity) instead.
        """
        return False

    def audit_topology(self, ensemble_uuid: str) -> list[str]:
        """
        Topology audit — disabled (ruleEvaluator removed).
        Returns empty list.
        """
        return []

    def log_clinical_focus_summary(self, ensemble_uuid: str) -> None:
        """
        Clinical focus summary — disabled (ruleEvaluator removed).
        """
        self.logger.info(
            f'[ClinicalFocus] ensemble={ensemble_uuid[:8]}... — '
            f'rule engine removed; DSP SignalProcessor active.'
        )

    def _extract_patient_and_room(self, device_handler: 'DeviceHandler') -> Tuple[Optional[str], Optional[str]]:
        """
        Extracts the patient identifier and room from the device's MDIB.
        Executed while holding device_handler.data_lock.

        Algorithm:
          1. Room       -- from LocationContextState.LocationDetail.Room
          2. Patient ID -- primary:  WorkflowContextState.WorkflowDetail.
                                     Patient.Identification[0].Extension
                          fallback:  PatientContextState.Identification[0].Extension

        Returns:
          (patient_id, room) -- either element may be None if data is absent.
        """
        from sdc11073.xml_types import pm_qnames as pm

        patient_id: Optional[str] = None
        room: Optional[str] = None

        with device_handler.data_lock:
            if not device_handler.mdib:
                self.logger.debug(
                    f'[Aggregator] _extract_patient_and_room: MDIB not ready for {device_handler.epr[-12:]}'
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
                    f'[Aggregator] Error reading LocationContextState '
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
                        # Primary: Extension XML attribute
                        ext = getattr(ident, 'Extension', None)
                        if ext:
                            patient_id = str(ext)
                            break
                        # Fallback: IdentifierName child element
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
                    f'[Aggregator] Error reading WorkflowContextState '
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
                    # Last resort: Givenname + Familyname from CoreData
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
                        f'[Aggregator] Error reading PatientContextState '
                        f'for {device_handler.epr[-12:]}: {exc}'
                    )

        self.logger.debug(
            f'[Aggregator] Extracted for {device_handler.epr[-12:]}: '
            f'patient_id={patient_id!r}, room={room!r}'
        )
        return patient_id, room

    def _get_or_fetch_fhir_data(self, patient_id: str) -> Optional[FHIRPatientData]:
        """
        Returns a cached FHIRPatientData instance for patient_id, or fetches
        it from the FHIR server on the first call.

        Cache policy: one FHIRPatientData object per patient_id for the
        lifetime of the aggregator (session-scoped cache).

        Thread safety: self.lock must NOT be held by the caller -- the HTTP
        request may take several seconds and would block other devices.
        """
        # -- Cache hit ---------------------------------------------------------
        if patient_id in self._fhir_cache:
            self.logger.debug(
                f'[Aggregator] FHIR cache hit for patient_id={patient_id!r}.'
            )
            return self._fhir_cache[patient_id]

        # -- Cache miss: fetch from FHIR server --------------------------------
        self.logger.info(
            f'[Aggregator] FHIR cache miss -- fetching data for patient_id={patient_id!r}...'
        )
        try:
            fhir_data = FHIRPatientData()
            fhir_data.fetch(patient_id)
            self._fhir_cache[patient_id] = fhir_data

            # Extract and cache FHIR clinical focus rules (from Condition Extensions).
            # These override rules.json entries for this patient specifically.
            try:
                fhir_focus = fhir_data.get_clinical_focus()
                self._fhir_focus_cache[patient_id] = fhir_focus
                if fhir_focus:
                    self.logger.info(
                        f'[Aggregator] FHIR clinical focus: {len(fhir_focus)} rule(s) '
                        f'loaded from Condition Extensions for patient_id={patient_id!r}. '
                        f'These override rules.json entries.'
                    )
                    for entry in fhir_focus:
                        self.logger.debug(
                            f'[Aggregator]   FHIR rule: danger_code={entry["danger_code"]!r} '
                            f'sensor={entry.get("critical_sensor_concepts")} '
                            f'alerts={entry.get("priority_alert_concepts")}'
                        )
                else:
                    self.logger.debug(
                        f'[Aggregator] No FHIR clinical focus extensions found for '
                        f'patient_id={patient_id!r} -- using rules.json only.'
                    )
            except Exception as _focus_exc:
                self._fhir_focus_cache[patient_id] = []
                self.logger.warning(
                    f'[Aggregator] FHIR clinical focus extraction failed for '
                    f'patient_id={patient_id!r}: {_focus_exc}'
                )

            self.logger.info(
                f'[Aggregator] FHIR data fetched and cached for patient_id={patient_id!r}.'
            )
            return fhir_data
        except Exception as exc:
            self.logger.error(
                f'[Aggregator] FHIR fetch failed for patient_id={patient_id!r}: {exc}. '
                f'Proceeding without FHIR data.'
            )
            return None

    def evaluate_and_bind_device(self, device_handler: 'DeviceHandler') -> None:
        """
        Entry point for a newly connected device.
        Evaluates the device context and either creates a new ensemble or
        joins the device to an existing one.

        Algorithm:
          1. Extract (patient_id, room) from the MDIB.
          2. If both values are present, form the key (patient_id, room).
          3. Under aggregator.lock check _active_ensembles:
               - key exists  -> use the existing ensemble_uuid
               - key absent  -> generate a new UUID, register the ensemble
          4. Add the device EPR to _ensemble_devices[ensemble_uuid].
          5. OUTSIDE the lock call device_handler.apply_ensemble_context(ensemble_uuid)
             (the SOAP network call must not hold the aggregator mutex).
        """
        patient_id, room = self._extract_patient_and_room(device_handler)

        if not patient_id or not room:
            self.logger.info(
                f'[Aggregator] Device {device_handler.epr[-12:]} skipped -- '
                f'incomplete context: patient_id={patient_id!r}, room={room!r}. '
                f'No ensemble will be formed until both values are available.'
            )
            return

        key: Tuple[str, str] = (patient_id, room)
        ensemble_uuid: str

        with self.lock:
            if key in self._active_ensembles:
                # -- Existing ensemble ----------------------------------------
                ensemble_uuid = self._active_ensembles[key]
                self.logger.info(
                    f'[Aggregator] Device {device_handler.epr[-12:]} joining existing '
                    f'ensemble {ensemble_uuid[:8]}... '
                    f'(patient={patient_id}, room={room})'
                )
            else:
                # -- New ensemble ---------------------------------------------
                ensemble_uuid = str(uuid.uuid4())
                self._active_ensembles[key] = ensemble_uuid
                self._ensemble_devices[ensemble_uuid] = set()
                self.logger.info(
                    f'[Aggregator] New ensemble {ensemble_uuid[:8]}... created '
                    f'for patient={patient_id}, room={room}'
                )

            self._ensemble_devices[ensemble_uuid].add(device_handler.epr)
            member_count = len(self._ensemble_devices[ensemble_uuid])

            # Assign the UUID to the worker here, under the aggregator lock.
            # This guarantees that device_handler.ensemble_uuid is set before
            # apply_ensemble_context() is called, even if the SOAP request fails.
            # apply_ensemble_context() will overwrite it again on success --
            # this is idempotent and safe.
            device_handler.ensemble_uuid = ensemble_uuid

        self.logger.debug(
            f'[Aggregator] Ensemble {ensemble_uuid[:8]}... now has '
            f'{member_count} member(s). Sending context to {device_handler.epr[-12:]}...'
        )

        # FHIR fetch -- executed outside self.lock (HTTP round-trip; may be slow).
        # Returns None gracefully if the FHIR server is unreachable.
        fhir_data = self._get_or_fetch_fhir_data(patient_id)

        # Network SOAP call -- executed outside self.lock to avoid holding the
        # aggregator mutex during a potentially slow round-trip to the device.
        success = device_handler.apply_ensemble_context(ensemble_uuid)
        if success:
            self.logger.info(
                f'[Aggregator] EnsembleContext {ensemble_uuid[:8]}... '
                f'successfully applied to {device_handler.epr[-12:]}.'
            )
            # Notify PatientOverview: new/updated ensemble, not yet escalated
            self._notify_overview(ensemble_uuid, patient_id, room or '', False, 0.0)
        else:
            # SOAP failed -- roll back the local assignment so the device
            # is not considered "bound" until the next successful attempt.
            device_handler.ensemble_uuid = None
            with self.lock:
                self._ensemble_devices[ensemble_uuid].discard(device_handler.epr)
            self.logger.warning(
                f'[Aggregator] Failed to apply EnsembleContext to '
                f'{device_handler.epr[-12:]}. Rolled back local binding.'
            )

        # Apply FHIR patient/clinical context to the device (demographics,
        # danger codes, vital measurements). Called regardless of ensemble
        # binding outcome -- FHIR data is independent of SDC ensemble state.
        # apply_fhir_contexts() is implemented on DeviceHandler.
        device_handler.apply_fhir_contexts(fhir_data)

