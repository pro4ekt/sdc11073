"""
qtDeviceHandler.py -- Qt/QML wrapper over DeviceHandler for one SDC device.

ARCHITECTURE (bridge between the worker thread and the UI):
  DeviceHandler lives in a worker thread and owns the device MDIB.
  QML cannot directly access objects from other threads.

  Solution -- QtDeviceHandler (QObject):
    1. Created in the worker thread (together with DeviceHandler).
    2. Moved to the main UI thread via moveToThread().
    3. Worker thread calls scheduleUpdate() -> Qt signal -> handleUpdateTick()
       executes in the main thread -> update_data() reads the MDIB.

  Result: QML sees only clean properties (str, list) with no threading knowledge.

HOW Qt SIGNALS WORK (for reference):
  Signal() -- class-level signal declaration.
  emit()   -- fire the signal (thread-safe).
  When crossing a thread boundary Qt automatically queues the call into the
  event loop of the thread that owns the receiver object (Qt::QueuedConnection).
"""

import threading
import time
import uuid
from decimal import Decimal, ROUND_HALF_UP

from PySide6.QtCore import QObject, Signal, Slot, Property

# pm -- BICEPS/SDC QName type identifiers for filtering MDIB objects
from sdc11073.xml_types import pm_qnames as pm

# pm_types -- Python classes with BICEPS enums and data structures.
# Needed for comparing Presence (AlertSignalPresence.ON/ACK/LATCH/OFF).
from sdc11073.xml_types import pm_types

from typing import TYPE_CHECKING

# TYPE_CHECKING is True only during static analysis (mypy/PyCharm).
# Allows using DeviceHandler in annotations without a circular import.
if TYPE_CHECKING:
    from .deviceHandler import DeviceHandler


class QtDeviceHandler(QObject):
    """
    Qt wrapper over DeviceHandler for one SDC device.

    Exposes to QML:
      - Properties: patientName, patientRoom, deviceName, deviceValue,
                    alarmStatus, priority, metrics, operations, epr
      - Change signals for each property (notify signals for QML bindings)
      - Slot acknowledgeAlarm() for acknowledging alarms from the UI

    Thread safety is ensured by:
      - Reading MDIB only through data_lock (non-blocking acquire)
      - Delivering updates via the updateTick Signal (auto-marshalled by Qt)
    """

    # =========================================================================
    # Property change signals (notify signals for QML Property bindings)
    # =========================================================================
    # Each signal corresponds to one @Property and is emitted in update_data()
    # when the property value changes. QML elements are auto-subscribed via binding.

    patientNameChanged  = Signal()   # Patient name changed
    patientRoomChanged  = Signal()   # Patient room/ward changed
    patientIdChanged    = Signal()   # Patient identifier changed (Identification.Extension)
    deviceNameChanged   = Signal()   # Device name changed
    deviceValueChanged  = Signal()   # Displayed metric value changed
    alarmStatusChanged  = Signal()   # Alarm status changed (Off/On/Ack/Latch/COMM_FAILURE)
    priorityChanged     = Signal()   # Device priority changed
    metricsChanged      = Signal()   # Metrics list updated
    operationsChanged   = Signal()   # Available operations list updated
    eprChanged          = Signal()   # EPR (device ID) changed (theoretically never changes)
    connectedChanged    = Signal()   # Connection status changed (reserved)
    ackStateChanged     = Signal()   # Fix 16 / Q2: result of the last bell press changed

    # Internal signal for cross-thread update delivery.
    # Worker thread emits updateTick -> Qt queues the call into the main thread
    # -> handleUpdateTick() executes in the main thread -> update_data() reads MDIB.
    updateTick = Signal()
    # Fix 16 (A3): the ack worker thread reports (press_id, verdict, detail);
    # Qt queues it into the main thread → _applyAckResult.
    _ackResult = Signal(str, str, str)
    # Fix 16: DeviceHandler's notification thread saw Presence=Ack for a handle.
    _ackConfirmed = Signal(str)
    # Fix 17: the manager (worker thread) declares the connection lost.
    _connectionLostTick = Signal(str, float, bool)

    def __init__(self, device: 'DeviceHandler'):
        """
        Parameters:
          device -- reference to the DeviceHandler (device worker thread).
                    Used to access MDIB and consumer in update_data().
        """
        super().__init__()

        # Keep a reference to DeviceHandler.
        # In production code a weakref would be preferable to avoid keeping
        # DeviceHandler alive in memory after it has stopped.
        self._device = device

        # Initial property values (shown before the first successful update_data())
        self._patientRoom  = "Unknown"
        self._patientName  = "Unknown"
        self._patientId    = ""          # Identification.Extension (same key as SmartAlertAggregator)
        self._deviceName   = "SDC Device"
        self._deviceValue  = "---"
        self._alarmStatus  = ""
        self._priority     = "3"
        self._metrics      = []   # list of dicts: {metricname, value, samples, alarm, timestamp_ms}
        self._operations   = []   # list of dicts: {name, handle, mode, type}

        # Fix 16 / Q2: state of the operator's acknowledgement (IEC 60601-1-8
        # §6.11.2.4 a): idle | pending | accepted | confirmed | failed | timeout |
        # error | no_active_alarm | not_remote_ackable | unavailable
        self._ackState   = 'idle'
        self._ackMessage = ''
        self._ackPressId = ''
        self._ack_synchronous = False   # tests: run the transactions inline

        # Fix 17: technical alarm "Verbindung verloren" (DIN EN 60601-1-8
        # §6.11.2.2.1 b).  While True the card shows COMM_FAILURE, update_data()
        # no longer touches the (dead) worker's MDIB, and the state stays until
        # the manager replaces this object (reconnect) or staff dismiss it.
        self._connectionLost = False
        self._lostCause = ''
        self._lostSince = 0.0            # time.time() of the loss
        self._escalatedAtLoss = False    # A1: ensemble was Red / latched at the loss

        # Connect the internal signal: any emit() from any thread will automatically
        # call handleUpdateTick() in the thread that owns this object.
        self.updateTick.connect(self.handleUpdateTick)
        self._ackResult.connect(self._applyAckResult)
        self._ackConfirmed.connect(self._applyAckConfirmed)
        self._connectionLostTick.connect(self._applyConnectionLost)

        # Initial synchronous read (directly at object creation time).
        # At this point the object is still in the worker thread, but MDIB is ready.
        self.update_data()

    # =========================================================================
    # Thread-safe update request from the worker thread
    # =========================================================================
    def scheduleUpdate(self):
        """
        Called from the DeviceHandler worker thread on every monitoring loop iteration.
        Emits updateTick -- Qt automatically marshals it to the main thread.
        Does NOT perform any work directly -- only signals.
        """
        self.updateTick.emit()

    @Slot()
    def handleUpdateTick(self):
        """
        Slot invoked in the MAIN THREAD when updateTick is received.
        @Slot() decorator explicitly registers this as a Qt slot (required for
        correct cross-thread marshalling).
        """
        self.update_data()

    # =========================================================================
    # Fix 17: technical alarm "Verbindung verloren"
    # =========================================================================

    def markConnectionLost(self, cause: str, t_abs: float, escalated_at_loss: bool = False):
        """
        Called by SdcMyConsumer (worker thread) when the watchdog declared the
        connection lost.  Applied in the thread that owns this object: directly
        when already there (headless / tests), otherwise queued via the signal.
        """
        # The object lives in the main (GUI) thread after moveToThread; a call
        # from the main thread (headless runner, tests) is applied directly.
        if threading.current_thread() is threading.main_thread():
            self._applyConnectionLost(cause, float(t_abs), bool(escalated_at_loss))
        else:
            self._connectionLostTick.emit(cause, float(t_abs), bool(escalated_at_loss))

    @Slot(str, float, bool)
    def _applyConnectionLost(self, cause: str, t_abs: float, escalated_at_loss: bool):
        self._connectionLost = True
        self._lostCause = cause
        self._lostSince = t_abs
        self._escalatedAtLoss = escalated_at_loss
        if self._alarmStatus != "COMM_FAILURE":
            self._alarmStatus = "COMM_FAILURE"
            self.alarmStatusChanged.emit()
        self._deviceValue = "VERBINDUNG VERLOREN"
        self.deviceValueChanged.emit()
        self.connectedChanged.emit()
        try:
            self._device.logger.error(
                f'[TECH-ALARM] card → COMM_FAILURE (cause={cause}'
                f"{', during escalation' if escalated_at_loss else ''})"
            )
        except Exception:
            pass

    # =========================================================================
    # Acknowledgement (bell) — Fix 16: only ACTIVE signals, exact operation,
    # one transaction per signal, press_id groups a bell press (A4).
    # =========================================================================

    def _select_ack_targets(self) -> tuple[list[tuple[str, str]], str, list[str]]:
        """
        Select the AlertSignals to acknowledge for one bell press.

        A signal is a candidate when its Presence is On or Latch, its
        AlertCondition (``ConditionSignaled``) is not artifact-suppressed, and —
        for Presence=On — that condition is still present in the MDIB.  Each
        candidate needs a SetAlertStateOperationDescriptor whose
        ``OperationTarget`` is exactly that signal; there is NO fallback to the
        first signal or the first operation any more (an ack of an inactive or
        foreign signal is a wrong transaction).

        Returns (targets, reason, not_ackable):
          targets     list of (operation_handle, signal_handle)
          reason      'ok' | 'no_active_alarm' | 'not_remote_ackable' | 'unavailable'
          not_ackable candidate signals without a matching operation
        """
        device = self._device
        if not device or not hasattr(device, 'data_lock'):
            return [], 'unavailable', []
        with device.data_lock:
            mdib = device.mdib
            if not mdib:
                return [], 'unavailable', []

            suppressed: frozenset
            if hasattr(device, 'get_suppression_snapshot'):
                suppressed, _warn = device.get_suppression_snapshot()
            else:
                suppressed = frozenset(getattr(device, '_artifact_suppressed', ()))

            on_str = str(pm_types.AlertSignalPresence.ON)
            latch_str = str(pm_types.AlertSignalPresence.LATCH)

            candidates: list[str] = []
            for sig in [s for s in mdib.states.objects if s.NODETYPE == pm.AlertSignalState]:
                presence = str(getattr(sig, 'Presence', ''))
                if presence not in (on_str, latch_str):
                    continue
                desc = mdib.descriptions.handle.get_one(sig.DescriptorHandle, allow_none=True)
                cond = getattr(desc, 'ConditionSignaled', None) if desc is not None else None
                if cond and str(cond) in suppressed:
                    continue
                if presence == on_str and cond:
                    cond_state = None
                    try:
                        cond_state = mdib.states.descriptor_handle.get_one(str(cond), allow_none=True)
                    except Exception:
                        cond_state = None
                    if cond_state is not None and not getattr(cond_state, 'Presence', True):
                        continue  # signal On but its condition already ended → nothing to ack
                candidates.append(sig.DescriptorHandle)

            op_by_target: dict[str, str] = {}
            for op in mdib.descriptions.NODETYPE.get(pm.SetAlertStateOperationDescriptor, []):
                target = getattr(op, 'OperationTarget', None)
                if target and target not in op_by_target:
                    op_by_target[str(target)] = op.Handle

        targets = [(op_by_target[h], h) for h in candidates if h in op_by_target]
        not_ackable = [h for h in candidates if h not in op_by_target]
        if not candidates:
            return [], 'no_active_alarm', []
        if not targets:
            return [], 'not_remote_ackable', not_ackable
        return targets, 'ok', not_ackable

    def _run_ack_press(self, press_id: str, targets: list[tuple[str, str]]) -> None:
        """Send one SetAlertState transaction per target (sequentially) and
        report the combined result through the queued ``_ackResult`` signal."""
        device = self._device
        outcomes: list[tuple[str, str, str | None, str]] = []
        for op_handle, sig_handle in targets:
            try:
                outcome, state, msg = device.acknowledge_alarm_detailed(
                    op_handle, sig_handle, press_id=press_id)
            except Exception as e:  # pragma: no cover — defensive
                outcome, state, msg = 'error', None, f'{type(e).__name__}: {e}'
            outcomes.append((sig_handle, outcome, state, msg))
        # Combined verdict for the bell: accepted only if every transaction was.
        if all(o[1] == 'accepted' for o in outcomes):
            verdict = 'accepted'
        else:
            # Most severe first: failed > timeout > error
            kinds = [o[1] for o in outcomes]
            verdict = next((k for k in ('failed', 'timeout', 'error', 'unavailable') if k in kinds), 'error')
        detail = '; '.join(
            f'{sig}: {out}' + (f' ({state})' if state else '') + (f' {msg}' if msg and out != 'accepted' else '')
            for sig, out, state, msg in outcomes
        )
        self._ackResult.emit(press_id, verdict, detail)

    @Slot()
    def silenceAlarm(self):
        """
        Called from QML when the Bell (Silence) button is pressed.

        Selects every ACTIVE AlertSignal that has its own
        SetAlertStateOperation (``_select_ack_targets``) and sends one
        SetAlertState(Presence=Ack) transaction per signal, all tagged with the
        same ``press_id``.  The transactions run OFF the Qt main thread (A3);
        the result arrives through ``_ackResult`` → ``_applyAckResult`` and is
        shown as ``ackState`` / ``ackMessage`` (Q2, IEC 60601-1-8 §6.11.2.4 a).
        """
        if not self._device:
            return
        try:
            targets, reason, not_ackable = self._select_ack_targets()
        except Exception as e:
            self._device.logger.error(f'silenceAlarm error: {e}', exc_info=True)
            self._set_ack_state('error', str(e), '')
            return

        press_id = uuid.uuid4().hex[:8]
        if reason != 'ok':
            self._device.logger.warning(
                f'silenceAlarm: nothing sent — {reason}'
                + (f' (signals without SetAlertStateOperation: {not_ackable})' if not_ackable else '')
            )
            self._set_ack_state(reason, ', '.join(not_ackable), press_id)
            return

        self._device.logger.info(
            f'silenceAlarm: press={press_id} targets={[s for _o, s in targets]}'
            + (f' not_ackable={not_ackable}' if not_ackable else '')
        )
        self._set_ack_state('pending', f'{len(targets)} transaction(s)', press_id)
        if getattr(self, '_ack_synchronous', False):
            self._run_ack_press(press_id, targets)          # tests / headless
            return
        threading.Thread(
            target=self._run_ack_press, args=(press_id, targets),
            name=f'ack-{press_id}', daemon=True,
        ).start()

    def _set_ack_state(self, state: str, message: str, press_id: str) -> None:
        """Set the acknowledgement state shown by the UI (main thread or tests)."""
        changed = (state != self._ackState) or (message != self._ackMessage)
        self._ackState = state
        self._ackMessage = message
        self._ackPressId = press_id
        if changed:
            self.ackStateChanged.emit()

    @Slot(str, str, str)
    def _applyAckResult(self, press_id: str, verdict: str, detail: str) -> None:
        """Main-thread slot (queued from the ack worker thread)."""
        if press_id != self._ackPressId:
            return  # a newer press superseded this result
        self._set_ack_state(verdict, detail, press_id)

    def on_ack_confirmed(self, handle: str) -> None:
        """Called by DeviceHandler (notification thread) when the provider's
        report shows Presence=Ack for ``handle``: accepted → confirmed."""
        self._ackConfirmed.emit(handle)

    @Slot(str)
    def _applyAckConfirmed(self, handle: str) -> None:
        if self._ackState in ('accepted', 'timeout'):
            self._set_ack_state('confirmed', f'{handle}: Presence=Ack reported', self._ackPressId)

    # =========================================================================
    # Main method: read MDIB and update all properties
    # =========================================================================
    def update_data(self):
        """
        Reads current data from the device MDIB and updates all Qt properties.

        CALLED IN: the main thread (via the updateTick Signal).
        LOCKING:   uses non-blocking acquire(blocking=False) on data_lock.
                   If the worker thread holds the lock -- skip this frame to
                   avoid stalling the UI thread.

        UPDATE ORDER:
          1. LocationContext  -> patientRoom
          2. PatientContext   -> patientName
          3. Device name      -> deviceName
          4. Alarm matrix     -> alarmStatus (On > Ack > Latch > Off)
          5. SelfCheckPeriod  -> COMM_FAILURE if AlertSystem is silent too long
          6. EpochSupport     -> clock_offset_sec from ClockState for timestamp correction
          7. Metrics          -> metrics (rounded by StepWidth, with timestamp_ms)
          8. Operations       -> operations
          9. Main value       -> deviceValue (first alarming metric or last metric)
        """
        if not self._device:
            return
        if self._connectionLost:
            return  # Fix 17: the worker is gone; the card holds COMM_FAILURE

        # Try to acquire the lock without blocking.
        # If the worker thread is writing to MDIB -- return; the next updateTick will refresh.
        if hasattr(self._device, 'data_lock'):
            if not self._device.data_lock.acquire(blocking=False):
                return  # Skip this frame -- MDIB is busy
        else:
            # data_lock not yet created (init race condition) -- skip
            return

        try:
            if not self._device.mdib:
                return  # MDIB not yet initialised (init_mdib not complete)

            # ------------------------------------------------------------------
            # 1. LOCATION CONTEXT
            # ------------------------------------------------------------------
            # LocationContextState contains ward, building, bed data.
            # Using list comprehension instead of NODETYPE.get() for compatibility
            # across sdc11073 versions (some do not index context_states by NODETYPE).
            locations = [l for l in self._device.mdib.context_states.objects
                         if l.NODETYPE == pm.LocationContextState]
            patients  = [p for p in self._device.mdib.context_states.objects
                         if p.NODETYPE == pm.PatientContextState]

            if locations and locations[0].LocationDetail:
                # Room can be None or empty string -- replace with "Unknown"
                self._patientRoom = locations[0].LocationDetail.Room or "Unknown"

            # ------------------------------------------------------------------
            # 2. PATIENT CONTEXT
            # ------------------------------------------------------------------
            # CoreData.Givenname = first name, CoreData.Familyname = last name.
            # NOTE: Birthname (maiden name) is NOT the same as Familyname!
            if patients and patients[0].CoreData:
                given  = patients[0].CoreData.Givenname  or ""
                family = patients[0].CoreData.Familyname or ""
                # strip() removes extra spaces when one field is empty
                self._patientName = f"{given} {family}".strip() or "Unknown"

            # patientId — canonical identifier used by SmartAlertAggregator for ensemble
            # keying. Priority chain mirrors _extract_patient_and_room():
            #   1. PatientContextState.Identification[0].Extension
            #   2. Fallback: CoreData Givenname+Familyname (same as patientName)
            # This guarantees filterPatient in QML matches modelData.patientName in
            # PatientOverview (which comes from the same chain in the aggregator).
            if patients:
                _pid = ""
                _ids = getattr(patients[0], 'Identification', None) or []
                for _ident in _ids:
                    _ext = getattr(_ident, 'Extension', None)
                    if _ext:
                        _pid = str(_ext)
                        break
                if not _pid:
                    # Fallback: same value as patientName so the key always resolves
                    _pid = self._patientName
                if _pid != self._patientId:
                    self._patientId = _pid
                    self.patientIdChanged.emit()

            # ------------------------------------------------------------------
            # 3. DEVICE NAME (priority chain)
            # ------------------------------------------------------------------
            # Attempt 1: DPWS FriendlyName -- most human-readable
            # Attempt 2: MDIB MdsDescriptor.ModelName -- device model
            # Attempt 3: MDIB MdsDescriptor.Type.localname -- technical type

            name_candidate = "SDC Device"  # Default fallback

            try:
                # host_description -- DPWS provider metadata
                # this_device.FriendlyName -- list of localised strings; take the first
                name_candidate = self._device.consumer.host_description.this_device.FriendlyName[0].text
            except Exception:
                pass  # FriendlyName may be absent -- that's fine

            if name_candidate == "SDC Device":
                # MdsDescriptor -- root device descriptor in the BICEPS hierarchy
                mds_descriptors = [d for d in self._device.mdib.descriptions.objects
                                   if d.NODETYPE == pm.MdsDescriptor]
                if mds_descriptors:
                    mds = mds_descriptors[0]
                    if mds.ModelName:
                        name_candidate = mds.ModelName[0].text
                    elif mds.Type:
                        # localname -- local part of the XML QName (without namespace)
                        name_candidate = mds.Type.localname

            # Update property and emit signal only on change (optimisation)
            if self._deviceName != name_candidate:
                self._deviceName = name_candidate
                self.deviceNameChanged.emit()

            # ------------------------------------------------------------------
            # 4. ALARM LOGIC
            # ------------------------------------------------------------------
            # active_alert_handles -- set of metric handles that have an active alarm.
            # Used later when building the metrics list to flag alarming metrics.
            active_alert_handles = set()
            new_alarm_status = "Off"  # Initial status -- no alarms

            # Thread-safe snapshot of suppression sets from DeviceHandler.
            # get_suppression_snapshot() acquires _suppression_lock internally,
            # preventing torn reads while sdc11073 notification thread updates them.
            _artifact_suppressed: frozenset
            _warning_handles: frozenset
            if hasattr(self._device, 'get_suppression_snapshot'):
                _artifact_suppressed, _warning_handles = self._device.get_suppression_snapshot()
            else:
                _artifact_suppressed = frozenset(getattr(self._device, '_artifact_suppressed', ()))
                _warning_handles = frozenset(getattr(self._device, '_warning_handles', ()))

            # Cache aggregator reference and ensemble UUID once for the signal loop.
            _aggregator = getattr(getattr(self._device, 'manager', None), 'aggregator', None)
            _ens_uuid: str | None = getattr(self._device, 'ensemble_uuid', None)

            # Collect all AlertSignalState objects from MDIB
            alert_signals = [
                s for s in self._device.mdib.states.objects
                if s.NODETYPE == pm.AlertSignalState
            ]

            # ------------------------------------------------------------------
            # 4a. Alarm priority matrix (BICEPS AlertSignalPresence)
            # ------------------------------------------------------------------
            _ALARM_PRIORITY = {
                str(pm_types.AlertSignalPresence.ON):    3,
                str(pm_types.AlertSignalPresence.ACK):   2,
                str(pm_types.AlertSignalPresence.LATCH): 1,
                str(pm_types.AlertSignalPresence.OFF):   0,
            }
            highest_priority = 0
            has_warning_signal = False  # True when ≥1 Warning-level signal is present

            for s in alert_signals:
                sig_desc = self._device.mdib.descriptions.handle.get_one(
                    s.DescriptorHandle, allow_none=True
                )
                cond_handle = getattr(sig_desc, 'ConditionSignaled', None) if sig_desc else None

                # SUPPRESS-routed condition — hidden completely from the UI
                if cond_handle and str(cond_handle) in _artifact_suppressed:
                    continue

                presence_str = str(s.Presence)
                priority = _ALARM_PRIORITY.get(presence_str, 0)

                # TTL cross-reference: skip stale MDIB entries already expired by Watchdog GC
                if priority > 0 and cond_handle and _aggregator is not None and _ens_uuid:
                    from app.alarms.alarmCoordinator import alert_key_for
                    if not _aggregator.is_alarm_active(_ens_uuid, alert_key_for(self._device.epr, str(cond_handle))):
                        continue  # stale MDIB state — alarm TTL-expired in aggregator

                # WARN-routed condition (E(t) < Theta_current) — record but do not inflate priority
                if priority > 0 and cond_handle and str(cond_handle) in _warning_handles:
                    has_warning_signal = True
                    continue  # shown as Yellow only when no On/Ack/Latch is present

                if priority > highest_priority:
                    highest_priority = priority
                    new_alarm_status = presence_str

            # Apply Warning only when no higher-priority alarm is active
            if highest_priority == 0 and has_warning_signal:
                new_alarm_status = 'Warning'

            # ------------------------------------------------------------------
            # 4b. Alert Conditions -> alarm sources (for metric highlighting)
            # ------------------------------------------------------------------
            alert_condition_types = [pm.AlertConditionState, pm.LimitAlertConditionState]
            active_conditions = [
                s for s in self._device.mdib.states.objects
                if s.NODETYPE in alert_condition_types and getattr(s, 'Presence', False)
            ]

            for alert in active_conditions:
                # Skip SUPPRESS-routed conditions — they are invisible to the user
                if alert.DescriptorHandle in _artifact_suppressed:
                    continue
                # WARN-routed conditions are intentionally included so the
                # metric row is highlighted in the DevicePage even at Warning level.

                alert_desc = self._device.mdib.descriptions.handle.get_one(
                    alert.DescriptorHandle, allow_none=True
                )
                if alert_desc and hasattr(alert_desc, 'Source') and alert_desc.Source:
                    for source_handle in alert_desc.Source:
                        active_alert_handles.add(source_handle)

            # ------------------------------------------------------------------
            # EpochSupport: compensate for clock drift on the device
            # ------------------------------------------------------------------
            # Computed BEFORE SelfCheck so we can determine timestamp reliability.
            clock_offset_sec = 0.0
            try:
                clock_states_list = [s for s in self._device.mdib.states.objects
                                     if s.NODETYPE == pm.ClockState]
                if clock_states_list:
                    clock_state = clock_states_list[0]

                    remote_sync = getattr(clock_state, 'RemoteSync', True)
                    if not remote_sync and not getattr(self, '_clock_nosync_warned', False):
                        self._clock_nosync_warned = True
                        self._device.logger.warning(
                            "Device clock NOT NTP-synced (RemoteSync=False). "
                            "Timestamp correction may be inaccurate."
                        )

                    device_time = getattr(clock_state, 'DateAndTime', None)
                    if device_time is not None:
                        clock_offset_sec = float(device_time) - time.time()
                        # Offset > 60s -- warn ONCE (suppress spam)
                        if abs(clock_offset_sec) > 60.0:
                            if not getattr(self, '_clock_offset_warned', False):
                                self._clock_offset_warned = True
                                self._device.logger.warning(
                                    f"Large clock offset detected ({clock_offset_sec:+.1f}s). "
                                    f"Device NTP may be misconfigured. "
                                    f"SelfCheck validation disabled. Suppressing further warnings."
                                )
                            # If the offset is enormous -- do not apply timestamp correction
                            if abs(clock_offset_sec) >= 3600.0:
                                clock_offset_sec = 0.0
            except Exception:
                pass  # ClockState unavailable -- offset=0, work without correction

            # ------------------------------------------------------------------
            # 4c. SelfCheckPeriod validation (IHE SDPi / BICEPS)
            # ------------------------------------------------------------------
            # AlertSystemDescriptor.SelfCheckPeriod -- expected self-check interval (seconds).
            # AlertSystemState.LastSelfCheck -- Unix timestamp of the last self-check (seconds).
            #
            # If current time > LastSelfCheck + SelfCheckPeriod * 1.5 ->
            # the AlertSystem has been silent too long -> set device to COMM_FAILURE.
            # Multiplier 1.5 gives tolerance for network delays (50% over the expected period).
            #
            # IMPORTANT: all comparisons are in SECONDS (not milliseconds).
            # LastSelfCheck is pm:Timestamp = float (Unix seconds) in sdc11073.
            _SELF_CHECK_MULTIPLIER = 1.5
            # If the clock skew with the device exceeds this threshold -- skip SelfCheck
            # (device timestamps are unreliable; do not declare COMM_FAILURE falsely).
            _CLOCK_SKEW_SKIP_THRESHOLD = 3600.0  # 1 hour -- treat device clock as unreliable
            alert_system_state_list = [
                s for s in self._device.mdib.states.objects
                if s.NODETYPE == pm.AlertSystemState
            ]
            for als in alert_system_state_list:
                als_desc = self._device.mdib.descriptions.handle.get_one(
                    als.DescriptorHandle, allow_none=True
                )
                if als_desc and als_desc.SelfCheckPeriod and als.LastSelfCheck:
                    # Skip validation if device clock skew is too large
                    if abs(clock_offset_sec) >= _CLOCK_SKEW_SKIP_THRESHOLD:
                        break
                    # Compare in seconds (LastSelfCheck is already in Unix seconds)
                    deadline_s = als_desc.SelfCheckPeriod * _SELF_CHECK_MULTIPLIER
                    current_time_s = time.time()
                    if current_time_s - float(als.LastSelfCheck) > deadline_s:
                        new_alarm_status = "COMM_FAILURE"
                        break  # One violation is enough

            # Update alarmStatus property only on real change
            if self._alarmStatus != new_alarm_status:
                self._alarmStatus = new_alarm_status
                self.alarmStatusChanged.emit()
            # --- ALARM LOGIC END ---

            # ------------------------------------------------------------------
            # 5. METRICS LIST
            # ------------------------------------------------------------------
            # Collect all NumericMetricState, StringMetricState, RealTimeSampleArrayMetricState.
            # For each one build a dict with data for QML.
            metric_types = [
                pm.NumericMetricState,
                pm.StringMetricState,
                pm.RealTimeSampleArrayMetricState
            ]
            metric_states = [m for m in self._device.mdib.states.objects
                             if m.NODETYPE in metric_types]

            new_metrics_list = []

            for state in metric_states:
                # Descriptor contains metadata: handle, unit, range, etc.
                descriptor = self._device.mdib.descriptions.handle.get_one(state.DescriptorHandle)

                # Descriptor handle is used as the metric name in the UI
                metric_name    = descriptor.Handle
                metric_value   = "---"   # Default value (no data)
                metric_samples = []      # For waveform metrics: list of float values
                # Timestamp of the last data batch, corrected for clock_offset_sec.
                # None if DeterminationTime is absent in MetricValue.
                metric_timestamp_ms = None

                # Check whether this metric is a source of an active alarm
                if descriptor.Handle in active_alert_handles:
                    # Pass the current signal status (On, Ack or Latch) instead of hardcoding "On".
                    # If the global status is COMM_FAILURE or Off, still use "On"
                    # because the physiological condition (AlertCondition) is violated.
                    metric_alarm = new_alarm_status if new_alarm_status in ["On", "Ack", "Latch"] else "On"
                else:
                    metric_alarm = "Off"

                try:
                    if state.NODETYPE == pm.RealTimeSampleArrayMetricState:
                        # RealTime waveform -- no scalar Value, only a Samples array
                        metric_value = "Waveform"
                        if state.MetricValue and state.MetricValue.Samples:
                            # Samples is a list of Decimal -- convert to float for QML
                            metric_samples = [float(x) for x in state.MetricValue.Samples]
                        # EpochSupport: correct DeterminationTime for clock offset.
                        # DeterminationTime -- timestamp of the last sample in seconds (Unix).
                        if state.MetricValue:
                            det_time = getattr(state.MetricValue, 'DeterminationTime', None)
                            if det_time is not None:
                                metric_timestamp_ms = (float(det_time) + clock_offset_sec) * 1000

                    elif state.MetricValue:
                        # NumericMetric and StringMetric have a Value field.
                        # getattr with default None guards against AttributeError.
                        val = getattr(state.MetricValue, 'Value', None)
                        if val is not None:
                            # --------------------------------------------------
                            # ROUNDING by StepWidth (NumericMetric only)
                            # --------------------------------------------------
                            # NumericMetricDescriptor.TechnicalRange[0].StepWidth --
                            # the step of allowed values (e.g. Decimal('0.1') for one decimal place).
                            # quantize() rounds val to the required number of decimal places.
                            # ROUND_HALF_UP: 120.005 with step=0.1 -> 120.0 (standard medical rounding)
                            try:
                                if (state.NODETYPE == pm.NumericMetricState and
                                        hasattr(descriptor, 'TechnicalRange') and
                                        descriptor.TechnicalRange and
                                        descriptor.TechnicalRange[0].StepWidth is not None):
                                    step = descriptor.TechnicalRange[0].StepWidth
                                    val = Decimal(str(val)).quantize(step, rounding=ROUND_HALF_UP)
                            except Exception:
                                pass  # If rounding failed -- use original value
                            metric_value = str(val)
                        # EpochSupport: apply clock correction to scalar metrics too
                        det_time = getattr(state.MetricValue, 'DeterminationTime', None)
                        if det_time is not None:
                            metric_timestamp_ms = (float(det_time) + clock_offset_sec) * 1000

                except Exception:
                    pass  # Conversion error -- keep "---"

                new_metrics_list.append({
                    "metricname":    metric_name,        # str: metric handle (for display)
                    "value":         metric_value,       # str: current value (or "---"/"Waveform")
                    "samples":       metric_samples,     # list[float]: waveform samples for chart
                    "alarm":         metric_alarm,       # "On" / "Off": active alarm on this metric
                    "timestamp_ms":  float(metric_timestamp_ms) if metric_timestamp_ms is not None else 0.0,
                })

            self._metrics = new_metrics_list
            self.metricsChanged.emit()

            # ------------------------------------------------------------------
            # 6. OPERATIONS LIST
            # ------------------------------------------------------------------
            # Operations are actions the Consumer can execute on the Provider:
            # SetValue, SetString, Activate, SetContextState, etc.
            # Search by operation states (not descriptors) -- more reliable,
            # since a state exists only for operations that are currently active.
            op_state_types = [
                pm.SetValueOperationState,         # Set a numeric value
                pm.SetStringOperationState,        # Set a string value
                pm.ActivateOperationState,         # Activate a command (no parameters)
                pm.SetContextStateOperationState,  # Change a context state
                pm.SetMetricStateOperationState,   # Change a metric state
                pm.SetAlertStateOperationState,    # Change an alert state
                pm.SetComponentStateOperationState # Change a component state
            ]

            op_states = [s for s in self._device.mdib.states.objects
                         if s.NODETYPE in op_state_types]

            new_ops = []
            for state in op_states:
                d = self._device.mdib.descriptions.handle.get_one(
                    state.DescriptorHandle, allow_none=True
                )
                if not d:
                    continue  # Descriptor disappeared (e.g. dynamic MDIB update)

                op_name = d.Handle  # Fallback: use handle as name

                # Look for a human-readable name in Type.ConceptDescription or Type.Code
                if d.Type:
                    txt = None
                    if hasattr(d.Type, 'ConceptDescription') and d.Type.ConceptDescription:
                        txt = d.Type.ConceptDescription[0].text  # Preferred
                    if not txt and hasattr(d.Type, 'Code'):
                        txt = d.Type.Code  # Technical code as fallback
                    if txt:
                        op_name = txt

                # OperatingMode: Enabled / Disabled / NA -- is the operation available now?
                mode = "Enabled"
                if state.OperatingMode:
                    mode = str(state.OperatingMode)

                new_ops.append({
                    "name":   op_name,                    # Human-readable operation name
                    "handle": d.Handle,                   # Handle for invoking the operation
                    "mode":   mode,                       # Availability: Enabled/Disabled
                    "type":   str(d.NODETYPE.localname)   # Type: SetValueOperation etc.
                })

            self._operations = new_ops
            self.operationsChanged.emit()

            # ------------------------------------------------------------------
            # 7. MAIN DISPLAY VALUE (deviceValue)
            # ------------------------------------------------------------------
            # Selection logic:
            #   If there is an alarming metric -> show the first one (most critical)
            #   Otherwise -> show the last metric in the list (most recent)
            display_val = "---"

            alarming_metric = next(
                (m for m in new_metrics_list if m["alarm"] == "On"), None
            )

            if alarming_metric:
                display_val = f"{alarming_metric['metricname']}: {alarming_metric['value']}"
            elif new_metrics_list:
                last_mt = new_metrics_list[-1]
                display_val = f"{last_mt['metricname']}: {last_mt['value']}"

            if self._deviceValue != display_val:
                self._deviceValue = display_val
                self.deviceValueChanged.emit()

        except Exception as e:
            self._device.logger.error(f"update_data error: {e}", exc_info=True)
        finally:
            # MUST release the lock even if an exception occurred.
            # Without this the worker thread will deadlock on its next attempt
            # to acquire data_lock.
            if hasattr(self._device, 'data_lock'):
                self._device.data_lock.release()

    # =========================================================================
    # Qt Properties -- accessible from QML
    # =========================================================================
    # Syntax: @Property(type, notify=signal)
    # notify= tells the QML engine which signal means "value changed".
    # QML automatically re-evaluates all bindings when the signal fires.

    @Property(str, notify=patientNameChanged)
    def patientName(self):
        """Patient name: '{Givenname} {Familyname}' or 'Unknown'."""
        return self._patientName

    @Property(str, notify=patientIdChanged)
    def patientId(self):
        """
        Canonical patient identifier — mirrors SmartAlertAggregator._extract_patient_and_room().
        Priority: PatientContextState.Identification[0].Extension → fallback to patientName.
        Used by MainPage.qml as the foreign key for drill-down filtering.
        """
        return self._patientId

    @Property(str, notify=patientRoomChanged)
    def patientRoom(self):
        """Patient room/ward from LocationContextState."""
        return self._patientRoom

    @Property(str, notify=eprChanged)
    def epr(self):
        """
        EPR (Endpoint Reference) -- unique device UUID on the network.
        Used by QML as a unique key to identify the device in the list.
        """
        return self._device.epr if self._device else ""

    @Property(str, notify=deviceNameChanged)
    def deviceName(self):
        """Device name (DPWS FriendlyName -> ModelName -> Type -> 'SDC Device')."""
        return self._deviceName

    @Property(str, notify=deviceValueChanged)
    def deviceValue(self):
        """
        Main value to display on the device card.
        Format: 'handle: value'. Priority: first alarming metric.
        """
        return self._deviceValue

    @Property(list, notify=metricsChanged)
    def metrics(self):
        """
        List of device metrics.
        Each element: dict {metricname, value, samples, alarm, timestamp_ms}.
          timestamp_ms -- timestamp of the last batch in milliseconds, corrected
                          for the device clock offset (EpochSupport).
                          0.0 if MetricValue.DeterminationTime is absent.
        QML can iterate this list to render a metrics table and waveform charts.
        """
        return self._metrics

    @Property(str, notify=alarmStatusChanged)
    def alarmStatus(self):
        """
        Global alarm status of the device.
        Possible values: 'Off', 'On', 'Ack', 'Latch', 'COMM_FAILURE'.
        Used by QML for colour coding the device card.
        """
        return self._alarmStatus

    @Property(str, notify=priorityChanged)
    def priority(self):
        """
        Device priority in the list (number as string, '1' = highest).
        Currently always '3' -- reserved for future sorting.
        """
        return self._priority

    @Property(list, notify=operationsChanged)
    def operations(self):
        """
        List of available operations on the device.
        Each element: dict {name, handle, mode, type}.
        QML renders them as control buttons.
        """
        return self._operations

    @Property(str, notify=ackStateChanged)
    def ackState(self):
        """
        State of the last acknowledgement (bell press) — Fix 16 / Q2:
          idle, pending, accepted (Fin/FinMod), confirmed (Presence=Ack reported
          back by the provider), failed (Fail/Cnclld/CnclldMan), timeout,
          error, no_active_alarm, not_remote_ackable, unavailable.
        """
        return self._ackState

    @Property(str, notify=ackStateChanged)
    def ackMessage(self):
        """Human-readable detail of ``ackState`` (provider error text, handles)."""
        return self._ackMessage

    @Property(str, notify=ackStateChanged)
    def ackPressId(self):
        """press_id of the last bell press (groups its ack_rtt journal events)."""
        return self._ackPressId

    @Property(bool, notify=connectedChanged)
    def connectionLost(self):
        """Fix 17: True while the device is in the technical-alarm state."""
        return self._connectionLost

    @Property(bool, notify=connectedChanged)
    def connected(self):
        return not self._connectionLost

    @Property(str, notify=connectedChanged)
    def lostCause(self):
        """t_fallback | is_connected | metric_silence | error — see device/handler.py."""
        return self._lostCause

    @Property(float, notify=connectedChanged)
    def lostSince(self):
        """time.time() of the loss (0.0 while connected)."""
        return self._lostSince

    @Property(str, notify=connectedChanged)
    def lostSinceText(self):
        if not self._connectionLost or not self._lostSince:
            return ""
        return time.strftime('%H:%M:%S', time.localtime(self._lostSince))

    @Property(bool, notify=connectedChanged)
    def escalatedAtLoss(self):
        """A1: the patient's ensemble was escalated (Red / latched) at the loss."""
        return self._escalatedAtLoss
