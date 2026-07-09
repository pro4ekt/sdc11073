import logging
import threading
import time
import uuid
from typing import Any, Optional, Tuple, Set, Dict

from fhirData import FHIRPatientData
from ruleEvaluator import RuleEvaluator

# TYPE_CHECKING guard to avoid circular imports when annotating DeviceHandler
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from deviceHandler import DeviceHandler

class SmartAlertAggregator:
    """
    Core of the Smart Alerting System for the SDC Orchestrator.
    Phase 1: Dynamic EnsembleContext formation based on device topology and patient data.
    Phase 2: (Future) Cross-device alarm validation within an ensemble.
    """

    def __init__(self, manager):
        self.logger = logging.getLogger('sdc.consumer.aggregator')
        self._manager = manager  # Reference to SdcMyConsumer

        # Mutex protecting the aggregator's internal data structures
        self.lock = threading.Lock()

        # State Table: (patient_id, room_id) -> ensemble_uuid
        self._active_ensembles: Dict[Tuple[str, str], str] = {}

        # Device registry per ensemble: ensemble_uuid -> set(epr)
        self._ensemble_devices: Dict[str, Set[str]] = {}

        # FHIR cache: patient_id -> FHIRPatientData (populated on first access)
        self._fhir_cache: Dict[str, FHIRPatientData] = {}

        # In-Memory physiological state graph, indexed by semantic concept codes.
        # Structure: ensemble_uuid -> concept_code -> {"value": float, "timestamp": float}
        # This allows cross-device alarm validation without relying on local handles.
        self._physiological_graph: Dict[str, Dict[str, Dict[str, Any]]] = {}

        # Timestamp of the last console graph dump (monotonic).
        # Used to rate-limit dump output: at most once per GRAPH_DUMP_INTERVAL_SEC.
        self._last_graph_dump_ts: float = 0.0
        self.GRAPH_DUMP_INTERVAL_SEC: float = 2.0

        # Clinical suppression rule engine.
        # Loaded once from rules.json; read-only → no extra locking needed.
        self.rule_engine = RuleEvaluator('rules.json')

    def update_metric_state(self, ensemble_uuid: str, concept_code: str, value: float) -> None:
        """
        Writes a metric reading into the physiological graph under the given
        ensemble and semantic concept code.

        Thread safety: protected by self.lock.
        Called from DeviceHandler.on_metric_update() (sdc11073 notification thread).
        """
        with self.lock:
            if ensemble_uuid not in self._physiological_graph:
                self._physiological_graph[ensemble_uuid] = {}
                self.logger.info(
                    f'[PhysGraph] New ensemble entry created: {ensemble_uuid[:8]}...'
                )
            self._physiological_graph[ensemble_uuid][concept_code] = {
                'value': value,
                'timestamp': time.time(),
            }
        self.logger.debug(
            f'[PhysGraph] Write: ensemble={ensemble_uuid[:8]} '
            f'concept={concept_code!r} value={value:.4g}'
        )

        # -- Rate-limited console dump ----------------------------------------
        # Print the full graph snapshot to console at most once per
        # GRAPH_DUMP_INTERVAL_SEC to keep output readable during high-freq updates.
        now = time.monotonic()
        if now - self._last_graph_dump_ts >= self.GRAPH_DUMP_INTERVAL_SEC:
            self._last_graph_dump_ts = now
            self.logger.info(self.dump_physiological_graph())

    def get_metric_state(
        self,
        ensemble_uuid: str,
        concept_code: str,
        max_age_sec: float = 15.0,
    ) -> Optional[float]:
        """
        Returns the most recent cached value for the given ensemble and concept code,
        or None if the cached value is older than max_age_sec (stale data guard).

        Thread safety: protected by self.lock.

        Parameters:
          ensemble_uuid -- UUID of the ensemble to query.
          concept_code  -- BICEPS/LOINC semantic code of the metric.
          max_age_sec   -- maximum acceptable age in seconds (default 15 s).

        Returns:
          float -- the cached metric value.
          None  -- entry absent or older than max_age_sec.
        """
        with self.lock:
            entry = (
                self._physiological_graph
                .get(ensemble_uuid, {})
                .get(concept_code)
            )
            if entry is None:
                self.logger.debug(
                    f'[PhysGraph] Miss: ensemble={ensemble_uuid[:8]} '
                    f'concept={concept_code!r} (no entry)'
                )
                return None
            age = time.time() - entry['timestamp']
            if age > max_age_sec:
                self.logger.warning(
                    f'[PhysGraph] Stale: ensemble={ensemble_uuid[:8]} '
                    f'concept={concept_code!r} age={age:.1f}s > max={max_age_sec}s — returning None'
                )
                return None
            self.logger.debug(
                f'[PhysGraph] Hit:  ensemble={ensemble_uuid[:8]} '
                f'concept={concept_code!r} value={entry["value"]:.4g} age={age:.1f}s'
            )
            return entry['value']

    def dump_physiological_graph(self) -> str:
        """
        Returns a human-readable snapshot of the physiological graph.
        Useful for diagnostic logging and debugging cross-device validation.

        Thread safety: protected by self.lock.
        """
        lines: list[str] = ['[PhysGraph] ── Snapshot ──────────────────────────────']
        with self.lock:
            if not self._physiological_graph:
                lines.append('[PhysGraph]   (empty)')
            for ens_uuid, concepts in self._physiological_graph.items():
                now = time.time()
                lines.append(f'[PhysGraph]   Ensemble {ens_uuid[:8]}...')
                for code, entry in sorted(concepts.items()):
                    age = now - entry['timestamp']
                    stale = ' ⚠ STALE' if age > 15.0 else ''
                    lines.append(
                        f'[PhysGraph]     {code:<30} = {entry["value"]:>10.4g}'
                        f'  (age={age:.1f}s{stale})'
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

    def check_alert_priority(
        self,
        ensemble_uuid: Optional[str],
        alert_concept: Optional[str],
    ) -> bool:
        """
        Returns True if alert_concept is a clinical priority for the patient
        bound to ensemble_uuid (based on FHIR danger codes and clinical_focus rules).

        Thread safety: collects danger codes under self.lock, evaluates outside.

        Parameters:
          ensemble_uuid -- UUID of the ensemble the alerting device belongs to.
          alert_concept -- BICEPS/LOINC/MDC concept code of the triggered alert.

        Returns:
          True  -- alert is a clinical priority → escalate with [PRIORITY] prefix.
          False -- not a priority (or insufficient context).
        """
        if not ensemble_uuid or not alert_concept:
            return False

        with self.lock:
            patient_danger_codes = self._collect_patient_danger_codes(ensemble_uuid)

        # Evaluate outside lock (pure computation)
        return self.rule_engine.is_priority_alert(alert_concept, patient_danger_codes)

    def audit_topology(
        self,
        ensemble_uuid: str,
    ) -> list[str]:
        """
        Checks whether the ensemble has all required sensors for the patient's
        clinical focus diagnoses.

        FIX — Race condition avoidance:
          _physiological_graph is populated lazily (only when the first metric
          value arrives via on_metric_update). At the moment audit_topology is
          called (right after apply_fhir_contexts), the graph may still be empty
          even though the device is connected and its descriptors are known.

          Solution: collect available concept codes from DeviceHandler._handle_to_concept
          (populated immediately at MDIB load time) instead of from the graph.
          This reflects the DECLARED sensor capabilities of all devices in the
          ensemble, not just what has been received so far.

        Algorithm:
          Under self.lock:
            1. Look up all EPRs for this ensemble in _ensemble_devices.
            2. For each EPR, find the DeviceHandler in _manager.devices.
            3. Collect all values from handler._handle_to_concept (concept codes).
            4. Collect patient danger codes from FHIR cache.
          Outside lock:
            5. Call rule_engine.get_missing_concepts().

        Returns:
          list[str] -- missing concept codes (empty = topology complete).
        """
        with self.lock:
            # -- 1-3. Collect declared concept codes from all ensemble devices ----
            available_concepts: Set[str] = set()
            eprs = self._ensemble_devices.get(ensemble_uuid, set())
            manager_devices: dict = getattr(self._manager, 'devices', {})

            for epr in eprs:
                handler = manager_devices.get(epr)
                if handler is None:
                    continue
                handle_to_concept: dict = getattr(handler, '_handle_to_concept', {})
                available_concepts.update(handle_to_concept.values())

            # -- 4. Patient danger codes from FHIR cache --------------------------
            patient_danger_codes = self._collect_patient_danger_codes(ensemble_uuid)

        self.logger.debug(
            f'[Aggregator] audit_topology: ensemble={ensemble_uuid[:8]}... '
            f'available_concepts={sorted(available_concepts)}'
        )

        # -- 5. Evaluate outside lock (pure computation) -------------------------
        return self.rule_engine.get_missing_concepts(available_concepts, patient_danger_codes)

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

