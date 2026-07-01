import logging
import threading
import uuid
from typing import Optional, Tuple, Set, Dict

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

        # Network SOAP call -- executed outside self.lock to avoid holding the
        # aggregator mutex during a potentially slow round-trip to the device.
        success = device_handler.apply_ensemble_context(ensemble_uuid)
        if success:
            print()
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
