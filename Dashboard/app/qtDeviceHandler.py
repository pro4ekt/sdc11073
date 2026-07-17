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

import time
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

    # Internal signal for cross-thread update delivery.
    # Worker thread emits updateTick -> Qt queues the call into the main thread
    # -> handleUpdateTick() executes in the main thread -> update_data() reads MDIB.
    updateTick = Signal()

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

        # Connect the internal signal: any emit() from any thread will automatically
        # call handleUpdateTick() in the thread that owns this object.
        self.updateTick.connect(self.handleUpdateTick)

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

    @Slot()
    def silenceAlarm(self):
        """
        Called from QML when the Bell (Silence) button is pressed.
        Automatically locates the SetAlertStateOperation and AlertSignalState
        handles in the MDIB and calls acknowledge_alarm() on DeviceHandler,
        which sends SetAlertState(Presence=Ack) to the provider.

        IMPORTANT: the SetAlertStateOperationDescriptor has an OperationTarget
        that points to a specific AlertSignal handle.  We must use the operation
        whose OperationTarget matches the active signal — otherwise the provider
        rejects the call (wrong operation for the wrong target).
        """
        if not self._device:
            return
        try:
            with self._device.data_lock:
                if not self._device.mdib:
                    return

                # Step 1 — find the active (Presence=On) AlertSignalState.
                signal_handle = None
                alert_signals = [s for s in self._device.mdib.states.objects
                                 if s.NODETYPE == pm.AlertSignalState]
                for sig in alert_signals:
                    if str(sig.Presence) == str(pm_types.AlertSignalPresence.ON):
                        signal_handle = sig.DescriptorHandle
                        break
                # Fallback: first signal if none is active
                if not signal_handle and alert_signals:
                    signal_handle = alert_signals[0].DescriptorHandle

                # Step 2 — find the SetAlertStateOperationDescriptor whose
                # OperationTarget == signal_handle.  This guarantees we call the
                # correct operation entry point for the active signal.
                op_handle = None
                if signal_handle:
                    op_descs = self._device.mdib.descriptions.NODETYPE.get(
                        pm.SetAlertStateOperationDescriptor, []
                    )
                    for op in op_descs:
                        if op.OperationTarget == signal_handle:
                            op_handle = op.Handle
                            break
                    # Fallback: first available operation
                    if not op_handle and op_descs:
                        op_handle = op_descs[0].Handle

            if op_handle and signal_handle:
                self._device.logger.info(
                    f'silenceAlarm: op={op_handle}  signal={signal_handle}'
                )
                self._device.acknowledge_alarm(op_handle, signal_handle)
            else:
                self._device.logger.warning(
                    f'silenceAlarm: could not find handles '
                    f'(op={op_handle}, signal={signal_handle})'
                )
        except Exception as e:
            self._device.logger.error(f'silenceAlarm error: {e}', exc_info=True)

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

            # Alarms suppressed by the AlarmCoordinator pipeline.
            # _pipeline_suppressed contains AlertCondition DescriptorHandles.
            # Both the condition and its paired signal must be hidden from the UI.
            pipeline_suppressed: set = getattr(self._device, '_pipeline_suppressed', set())

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

            for s in alert_signals:
                # Skip signals whose parent condition was suppressed by the pipeline
                sig_desc = self._device.mdib.descriptions.handle.get_one(
                    s.DescriptorHandle, allow_none=True
                )
                cond_handle = getattr(sig_desc, 'ConditionSignaled', None) if sig_desc else None
                if cond_handle and str(cond_handle) in pipeline_suppressed:
                    continue  # pipeline-suppressed — hide from UI

                presence_str = str(s.Presence)
                priority = _ALARM_PRIORITY.get(presence_str, 0)
                if priority > highest_priority:
                    highest_priority = priority
                    new_alarm_status = presence_str

            # ------------------------------------------------------------------
            # 4b. Alert Conditions -> alarm sources (for metric highlighting)
            # ------------------------------------------------------------------
            alert_condition_types = [pm.AlertConditionState, pm.LimitAlertConditionState]
            active_conditions = [
                s for s in self._device.mdib.states.objects
                if s.NODETYPE in alert_condition_types and getattr(s, 'Presence', False)
            ]

            for alert in active_conditions:
                # Skip conditions suppressed by the pipeline
                if alert.DescriptorHandle in pipeline_suppressed:
                    continue

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
