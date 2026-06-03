from PySide6.QtCore import QObject, Signal, Slot, Property
from sdc11073.xml_types import pm_qnames as pm
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deviceHandler import DeviceHandler


class QtDeviceHandler(QObject):
    """
    Specialized Worker class for Qt integration.
    """

    # Signals to notify UI of changes
    patientNameChanged = Signal()
    patientRoomChanged = Signal()
    deviceNameChanged = Signal() # RESTORED: Signal for Device Name
    # Add other signals as needed
    deviceValueChanged = Signal()
    alarmStatusChanged = Signal()
    priorityChanged = Signal()
    metricsChanged = Signal() # ADDED: Signal for metrics list
    operationsChanged = Signal() # ADDED: Signal for operations list

    # Internal signal to bridge threads
    # This signal is emitted from the Worker thread context but connected to a slot in Main thread
    updateTick = Signal()

    # Add signal for EPR if needed, though usually constant
    eprChanged = Signal()

    connectedChanged = Signal()

    def __init__(self, device: 'DeviceHandler'):
        super().__init__()
        self._device = device # Keep reference (WeakRef recommended in production)

        # Initialize defaults
        self._patientRoom = "Unknown"
        self._patientName = "Unknown"
        self._deviceName = "SDC Device" # RESTORED: Default Initialization

        # Placeholder data for UI - these would come from MDIB in real app
        self._deviceValue = "---"
        self._alarmStatus = ""
        self._priority = "3"
        self._metrics = [] # ADDED: Initialize list
        self._operations = [] # ADDED: Initialize operations list

        # Connect internal signal for thread-hopping
        # When updateTick is emitted (from any thread), handleUpdateTick runs in the thread this object lives in (Main)
        self.updateTick.connect(self.handleUpdateTick)

        # Initial Data Fetch (Snapshot)
        self.update_data()

    def scheduleUpdate(self):
        """
        Thread-safe method to be called from the Worker Thread.
        Emits a signal which Qt automatically marshals to the Main Thread event loop.
        """
        self.updateTick.emit()

    @Slot()
    def handleUpdateTick(self):
        """Slot called from Worker thread via Signal to ensure updates run on Main Thread."""
        self.update_data()

    def update_data(self):
        """Reads data from the device MDIB and updates properties."""
        if not self._device:
            return

        # Attempt to acquire lock non-blocking to avoid freezing UI if worker is busy
        if hasattr(self._device, 'data_lock'):
            if not self._device.data_lock.acquire(blocking=False):
                return # Skip this update frame if locked
        else:
            # Fallback if lock doesn't exist yet (initialization race)
            return

        try:
            if not self._device.mdib:
                return

            locations = [l for l in self._device.mdib.context_states.objects if l.NODETYPE == pm.LocationContextState]
            patients = [p for p in self._device.mdib.context_states.objects if p.NODETYPE == pm.PatientContextState]

            # Safely access data
            if locations and locations[0].LocationDetail:
                self._patientRoom = locations[0].LocationDetail.Room or "Unknown"

            if patients and patients[0].CoreData:
                 self._patientName = patients[0].CoreData.Birthname or "Unknown"

            # --- DEVICE NAME LOGIC ---
            # Priority 1: DPWS FriendlyName (provider.device.FriendlyName)
            # Priority 2: MDIB MdsDescriptor ModelName
            # Priority 3: MDIB MdsDescriptor Type

            name_candidate = "SDC Device"

            # 1. Try DPWS FriendlyName
            try:
                name_candidate = self._device.consumer.host_description.this_device.FriendlyName[0].text
            except Exception:
                pass

            # 2. If still default, try MDIB MdsDescriptor
            if name_candidate == "SDC Device":
                # Usually found in the root MDS descriptor
                mds_descriptors = [d for d in self._device.mdib.descriptions.objects if d.NODETYPE == pm.MdsDescriptor]
                if mds_descriptors:
                    mds = mds_descriptors[0] # Use the first MDS found
                    if mds.ModelName:
                        name_candidate = mds.ModelName[0].text
                    elif mds.Type:
                        name_candidate = mds.Type.localname

            if self._deviceName != name_candidate:
                self._deviceName = name_candidate
                self.deviceNameChanged.emit()

            # --- ALARM LOGIC START ---
            active_alert_handles = set()
            new_alarm_status = "Off"

            # 1. Alert Signals (Global Alarm Status)
            # Find signals that are ON and Active (not suppressed/paused) to set the Device's global alarm state.
            alert_signals = [
                s for s in self._device.mdib.states.objects
                if s.NODETYPE == pm.AlertSignalState
            ]

            for s in alert_signals:
                # Robust check for 'On' state (handles both Enum and String representation)
                is_present = str(s.Presence) == 'On'
                #is_active = str(s.ActivationState) == 'On'

                if is_present:
                    new_alarm_status = "On"
                    break

            # 2. Alert Conditions (Metric Associations)
            # Find active physiological alarms (Conditions) to highlight specific metrics.
            alert_condition_types = [pm.AlertConditionState, pm.LimitAlertConditionState]
            active_conditions = [
                s for s in self._device.mdib.states.objects
                if s.NODETYPE in alert_condition_types and getattr(s, 'Presence', False)
            ]

            for alert in active_conditions:
                # Find the descriptor to check for sources
                alert_desc = self._device.mdib.descriptions.handle.get_one(alert.DescriptorHandle, allow_none=True)

                # The 'Source' field contains a list of Handles (metrics) that this alert monitors
                if alert_desc and hasattr(alert_desc, 'Source') and alert_desc.Source:
                    for source_handle in alert_desc.Source:
                        active_alert_handles.add(source_handle)

            # Update Global Status property if changed
            if self._alarmStatus != new_alarm_status:
                self._alarmStatus = new_alarm_status
                self.alarmStatusChanged.emit()
            # --- ALARM LOGIC END ---

            # 3. Metrics (Dynamic)
            # Find all NumericMetricStates, String, RealTime
            metric_types = [pm.NumericMetricState, pm.StringMetricState, pm.RealTimeSampleArrayMetricState]
            metric_states = [m for m in self._device.mdib.states.objects if m.NODETYPE in metric_types]

            new_metrics_list = []

            for state in metric_states:
                # Find corresponding descriptor to get the Name/Label
                descriptor = self._device.mdib.descriptions.handle.get_one(state.DescriptorHandle)

                # Prepare QML helper strings
                metric_name = descriptor.Handle

                metric_value = "---"
                metric_samples = []

                # Check if this metric is causing an alarm
                metric_alarm = "On" if descriptor.Handle in active_alert_handles else "Off"

                # FIXED logic: Safely handle types that don't have a scalar 'Value' field (like RealTime Waveforms)
                try:
                    if state.NODETYPE == pm.RealTimeSampleArrayMetricState:
                        metric_value = "Waveform"
                        # Extract samples specifically for graphing
                        if state.MetricValue and state.MetricValue.Samples:
                            metric_samples = [float(x) for x in state.MetricValue.Samples]
                    elif state.MetricValue:
                        # Use getattr to safely try accessing 'Value'.
                        # This prevents crash if the property doesn't exist on this metric type.
                        val = getattr(state.MetricValue, 'Value', None)
                        if val is not None:
                            metric_value = str(val)
                except Exception:
                    # If conversion fails, keep default "---"
                    pass

                # Store raw descriptor and state as requested, plus QML strings
                new_metrics_list.append({
                    "descriptor": descriptor,
                    "state": state,
                    "metricname": metric_name,
                    "value": metric_value,
                    "samples": metric_samples, # New field containing list of floats for graph
                    "alarm": metric_alarm
                })

            # Simple diff check or just emit (optimization: equality check on list content)
            self._metrics = new_metrics_list
            self.metricsChanged.emit()

            # 4. Operations (Dynamic)
            # CHANGED: Find operation states directly instead of descriptors.
            # This covers SetValue, Activate, SetString, etc. more reliably.
            op_state_types = [
                pm.SetValueOperationState,
                pm.SetStringOperationState,
                pm.ActivateOperationState,
                pm.SetContextStateOperationState,
                pm.SetMetricStateOperationState,
                pm.SetAlertStateOperationState,
                pm.SetComponentStateOperationState
            ]

            op_states = [s for s in self._device.mdib.states.objects if s.NODETYPE in op_state_types]

            new_ops = []
            for state in op_states:
                # Find corresponding descriptor to get the Name/Label
                d = self._device.mdib.descriptions.handle.get_one(state.DescriptorHandle, allow_none=True)
                if not d:
                    continue

                op_name = d.Handle
                # Try to get a human-readable name from ConceptDescription or Code
                if d.Type:
                    txt = None
                    if hasattr(d.Type, 'ConceptDescription') and d.Type.ConceptDescription:
                         txt = d.Type.ConceptDescription[0].text

                    if not txt and hasattr(d.Type, 'Code'):
                        txt = d.Type.Code

                    if txt:
                        op_name = txt

                # Check Operating Mode (Enabled/Disabled) from the State directly
                mode = "Enabled"
                if state.OperatingMode:
                    mode = str(state.OperatingMode)

                new_ops.append({
                    "name": op_name,
                    "handle": d.Handle,
                    "mode": mode,
                    "type": str(d.NODETYPE.localname)
                })

            self._operations = new_ops
            self.operationsChanged.emit()

            # 5. Determine Main Page Value (Alarm Priority)
            # Logic: If alarm, show the first alarming metric. Else, show the last metric in the list.
            display_val = "---"

            # Find first alarming metric
            alarming_metric = next((m for m in new_metrics_list if m["alarm"] == "On"), None)

            if alarming_metric:
                display_val = f"{alarming_metric['metricname']}: {alarming_metric['value']}"
            elif new_metrics_list:
                # No alarm, show last metric in the list as requested
                last_mt = new_metrics_list[-1]
                display_val = f"{last_mt['metricname']}: {last_mt['value']}"

            if self._deviceValue != display_val:
                self._deviceValue = display_val
                self.deviceValueChanged.emit()

        except Exception as e:
            print(f"Error reading data: {e}")
        finally:
            if hasattr(self._device, 'data_lock'):
                self._device.data_lock.release()

        """
        self.value_to_show = "10"
        self.alert = "None"
        self.priority = "3"

        self._alerts_descriptors = [a for a in device.mdib.descriptions.objects if a.NODETYPE == pm.AlertSystemDescriptor]
        self._alerts_states = [a for a in device.mdib.states.objects if a.NODETYPE == pm.AlertSystemState]
        self._metrics_descriptors = [m for m in device.mdib.descriptions.objects if m.NODETYPE == pm.NumericMetricDescriptor]
        self._metrics_states = [m for m in device.mdib.states.objects if m.NODETYPE == pm.NumericMetricState]
        #self.operations = [o for o in device.mdib.descriptions.objects if o.NODETYPE == pm.OperationDescriptor]

        """
    @Property(str, notify=patientNameChanged)
    def patientName(self):
        return self._patientName

    @Property(str, notify=patientRoomChanged)
    def patientRoom(self):
        return self._patientRoom

    @Property(str, notify=eprChanged)
    def epr(self):
        # Expose the unique ID (EPR) so QML knows which device this is
        return self._device.epr if self._device else ""

    @Property(str, notify=deviceNameChanged) # RESTORED: Property getter
    def deviceName(self):
        return self._deviceName

    @Property(str, notify=deviceValueChanged)
    def deviceValue(self):
        return self._deviceValue

    @Property(list, notify=metricsChanged)
    def metrics(self):
        return self._metrics

    @Property(str, notify=alarmStatusChanged)
    def alarmStatus(self):
        return self._alarmStatus

    @Property(str, notify=priorityChanged)
    def priority(self):
        return self._priority

    @Property(list, notify=operationsChanged)
    def operations(self):
        return self._operations
