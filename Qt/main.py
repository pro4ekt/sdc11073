from __future__ import annotations

# from GateWay import sdc_opc_gateway

import sys
import sdc11073
import asyncio
import socket
import threading
import time
import os
from copyreg import constructor

from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtCore import QObject, Signal, Slot, Property

from sdc11073.consumer import SdcConsumer
from sdc11073.mdib import ConsumerMdib
from sdc11073.xml_types.actions import periodic_actions
from sdc11073.mdib.statecontainers import LocationContextStateContainer
from sdc11073.wsdiscovery import WSDiscovery
from sdc11073.xml_types import pm_qnames as pm
from sdc11073.xml_types.pm_qnames import LocationContextState
from sdc11073 import observableproperties # ADDED: For event bindings
# ADDED: Essential enums for robust alarm checking
from sdc11073.xml_types.pm_types import AlertSignalPresence, AlertActivation

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Doesn't have to be reachable
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

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

    def __init__(self, device : DeviceHandler) :
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

class DeviceHandler(threading.Thread):
    """
    Worker class (The "Worker").
    Responsible for maintaining a connection to a SINGLE specific device (Provider).
    Runs in its own system thread with its own independent asyncio event loop.
    """
    # Removed QObject inheritance and Signal definition

    def __init__(self, wsd_service, manager):
        # Initialize only Thread
        threading.Thread.__init__(self, daemon=True)

        self.wsd_service = wsd_service
        self.epr = str(wsd_service.epr)  # Explicitly convert to string to ensure consistent key usage
        self.manager = manager
        self.running = True
        self.consumer = None
        self.mdib = None
        self.qtDeviceHandler = None
        self.error_occurred = False  # Track if the session ended with an error
        self.data_lock = threading.Lock() # Lock for MDIB access
        self.opcua_server = None # Placeholder for OPC UA Server instance if needed

    def run(self):
        # 1. Isolation: Create a new asyncio event loop for this thread.
        # This ensures network delays on this device don't affect others.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(self._worker_logic())
        finally:
            try:
                loop.close()
            except Exception:
                pass
            # 2. Self-cleanup: When the thread dies, remove self from Manager's registry.
            # We pass 'error_occurred' so the manager knows if it should invalidate the cache.
            self.manager.remove_device(self.epr, self.error_occurred)
            print(f"[Worker {self.epr}] Thread Exiting (Dead).")

    async def _worker_logic(self):
        print(f"[Worker {self.epr}] Connecting...")
        try:
            # 3. Connection: Create SDC Consumer for this specific service
            self.consumer = SdcConsumer.from_wsd_service(wsd_service=self.wsd_service, ssl_context_container=None)
            self.consumer.start_all(not_subscribed_actions=periodic_actions)

            with self.data_lock:
                self.mdib = ConsumerMdib(self.consumer)
                self.mdib.init_mdib()

            # Регистрируем устройство в OPC UA Gateway только ПОСЛЕ инициализации MDIB
            # ВАЖНО: Делаем вызов потокобезопасным, перекидывая задачу в event loop Менеджера!
            # if self.manager.opcua_gateway is not None and hasattr(self.manager, 'manager_loop'):
            #     print(f"[Worker {self.epr}] Registering in Async Central OPC UA Server...")
            #     future = asyncio.run_coroutine_threadsafe(
            #         self.manager.opcua_gateway.add_device(self.mdib, self.epr),
            #         self.manager.manager_loop
            #     )
            #     future.result() # Ожидаем завершения добавления нод

            # 4. Subscription (Bindings for real-time updates)
            observableproperties.bind(self.mdib, metrics_by_handle=self.on_metric_update)
            observableproperties.bind(self.mdib, alert_by_handle=self.on_alert_update)

            print(f"[Worker {self.epr}] Connection established. Monitoring...")

            # 1. Создаем Qt-обертку. Сейчас она "принадлежит" этому рабочему потоку.
            self.qtDeviceHandler = QtDeviceHandler(self)

            # 2. ВАЖНО: Перемещаем объект в главный поток UI.
            # Без этого QML может ругаться при попытке доступа к свойствам/слотам.
            main_thread = QGuiApplication.instance().thread()
            if main_thread:
                self.qtDeviceHandler.moveToThread(main_thread)
                # No connect needed here anymore, the QtDeviceHandler connects its own signal in __init__
            else:
                print(f"[Worker {self.epr}] Warning: Could not find Main Thread!")

            # 3. Уведомляем UI (сигнал уйдет в главный поток через очередь событий)
            # Мы вызываем emit у менеджера, который сам потокобезопасен (Qt Signals thread-safe)
            self.manager.deviceConnected.emit(self.qtDeviceHandler)

            # 5. Lifecycle Loop: Keep running as long as connected
            while self.running:
                if not self.consumer.is_connected:
                    print(f"[Worker {self.epr}] Connection lost reported by SDC stack.")
                    self.error_occurred = True
                    break

                # Trigger update on UI thread safely via method call
                if self.qtDeviceHandler:
                    self.qtDeviceHandler.scheduleUpdate()

                # АКТИВНАЯ ПРОВЕРКА (После бага с Vector Provider)
                try:
                    # Пытаемся сделать легкий запрос с коротким тайм-аутом
                    if self.consumer and self.consumer.is_connected:
                        # ИСПРАВЛЕНИЕ: Обращаемся к context_service_client напрямую (это свойство, а не функция)
                        if self.consumer.context_service_client:
                            self.consumer.context_service_client.get_context_states()
                        else:
                            # Если ContextService нет (редко, но бывает), можно дернуть GetService
                            # self.consumer.get_service_client.get_md_state()
                            pass
                except Exception as e:
                    print(f"[Worker {self.epr}] Ping failed: {e}")
                    self.error_occurred = True
                    break

                # CHANGED: Reverted to 1.0 second standard update rate (cancels smooth scrolling idea)
                await asyncio.sleep(1.0)

        except Exception as e:
            print(f"[Worker {self.epr}] Critical Error: {e}")
            self.error_occurred = True
        finally:
            if self.consumer:
                print(f"[Worker {self.epr}] Stopping consumer resources...")
                try:
                    self.consumer.stop_all()
                except:
                    pass

    def on_metric_update(self, metrics_by_handle):
        """Callback invoked by SDC library when metrics change."""
        # if not self.manager.opcua_gateway or not hasattr(self.manager, 'manager_loop'):
        #     return
            
        updates = {}
        for handle, state in metrics_by_handle.items():
            if state.NODETYPE == pm.NumericMetricState:
                val = getattr(state.MetricValue, 'Value', None)
                if val is not None:
                    try:
                        updates[handle] = float(val)
                    except ValueError:
                        pass
            elif state.NODETYPE in [pm.StringMetricState, pm.EnumStringMetricState]:
                val = getattr(state.MetricValue, 'Value', None)
                if val is not None:
                    updates[handle] = str(val)

        # if updates:
            # Передаем обновление в асинхронный цикл менеджера для безопасной записи в OPC
            # asyncio.run_coroutine_threadsafe(
            #     self.manager.opcua_gateway.update_values(self.epr, updates),
            #     self.manager.manager_loop
            # )

    def on_alert_update(self, alert_by_handle):
        """Callback invoked by SDC library when alerts change."""
        # if not self.manager.opcua_gateway or not hasattr(self.manager, 'manager_loop'):
        #     return

        updates = {}
        for handle, state in alert_by_handle.items():
            if state.NODETYPE in [pm.AlertConditionState, pm.LimitAlertConditionState]:
                presence = getattr(state, 'Presence', False)
                updates[f"Condition_{handle}_Presence"] = presence
            elif state.NODETYPE == pm.AlertSignalState:
                signal_presence = str(getattr(state, 'Presence', 'Unknown'))
                updates[f"Signal_{handle}"] = signal_presence

        # if updates:
            # Передаем обновление в асинхронный цикл менеджера для безопасной записи в OPC
            # asyncio.run_coroutine_threadsafe(
            #     self.manager.opcua_gateway.update_values(self.epr, updates),
            #     self.manager.manager_loop
            # )

    def stop(self):
        self.running = False

class SdcMyConsumer(QObject):
    """
    Manager class (The "Manager").
    Scans the network and spawns a Worker thread for every unique device found.

    ARCHITECTURE NOTE:
    To prevent "Zombie Loops" where a disconnected device is immediately re-discovered
    via the WSDiscovery cache (leading to infinite connect->fail->retry cycles),
    we must explicitly clear the specific device from the WSDiscovery cache in 'remove_device'
    if an error occurred. This forces a fresh network Probe.
    """

    # CHANGED: Signal now passes the Qt object directly
    deviceConnected = Signal(QtDeviceHandler, arguments=['device'])
    # NEW: Signal when a device is removed (passes the UUID string)
    deviceDisconnected = Signal(str, arguments=['epr'])

    def __init__(self):
        super().__init__()
        self.running = True
        self.devices = {}  # Registry: { UUID (epr): DeviceHandler_Object }
        self.lock = threading.Lock() # Ensures safe access to self.devices dictionary
        self.discovery = None  # Reference to WSDiscovery instance

        # OPC UA Server инициализируется позже, чтобы отвязать от старта Qt
        self.opcua_gateway = None

        # Start the Discovery Loop in a background thread
        self.discovery_thread = threading.Thread(target=self._run_discovery, daemon=True)

    def start(self):
        # Перенесли запуск OPC UA сервера в асинхронный цикл discovery_loop
        self.discovery_thread.start()
        print("[Manager] System started. Discovery loop active.")

    def stop(self):
        self.running = False
        print("[Manager] Stopping...")
        with self.lock:
            # Stop all workers
            for epr, handler in self.devices.items():
                print(f"[Manager] Stopping worker for: {epr}")
                handler.stop()

    def _run_discovery(self):
        # Entry point for the discovery thread
        asyncio.run(self._discovery_loop())

    async def _discovery_loop(self):
        self.manager_loop = asyncio.get_running_loop() # Сохраняем ссылку на цикл для воркеров
        
        local_ip = get_local_ip()
        print(f"[Manager] Network Scan on IP: {local_ip}")

        # Initialize Central Async OPC UA Server & PubSub here
        # print(f"[Manager] Starting Central Async OPC UA Server on IP: {local_ip}")
        # pubsub_url = f"opc.udp://{local_ip}:4840" 
        # self.opcua_gateway = sdc_opc_gateway.SdcOpcGateway(bind_ip=local_ip, pubsub_url=pubsub_url)
        # await self.opcua_gateway.init()
        # await self.opcua_gateway.start()

        self.discovery = WSDiscovery(local_ip)
        self.discovery.start()

        while self.running:
            try:
                # 1. Search for services (2 second timeout)
                services = await asyncio.to_thread(self.discovery.search_services, timeout=2)

                # 2. Process results
                for service in services:
                    try:
                        # Fix: Ensure strict string comparison for EPR (UUID) and trim whitespace
                        epr = str(service.epr).strip()

                        with self.lock:
                            # Cleanup check: If we have a record, but the thread is dead, clean it up.
                            if epr in self.devices and not self.devices[epr].is_alive():
                                print(f"[Manager] Found dead worker thread for {epr}. Cleaning up registry.")
                                del self.devices[epr]

                            # 3. Filtering: If we don't know this device, spawn a worker
                            if epr not in self.devices:
                                print(f"[Manager] Found NEW device: {epr}. Spawning Worker.")
                                device = DeviceHandler(service, self)
                                self.devices[epr] = device
                                device.start()


                    except Exception as loop_err:
                        print(f"[Manager] Error processing a discovered service: {loop_err}")

                await asyncio.sleep(2) # Wait before next scan

            except Exception as e:
                print(f"[Manager] Discovery Loop Error: {e}")
                await asyncio.sleep(5)

        self.discovery.stop()

    def remove_device(self, epr, error_occurred=False):
        """
        Callback used by Worker threads to remove themselves from the list
        when connections fail or threads stop.
        """
        epr = str(epr).strip() # Ensure consistent formatting
        with self.lock:
            if epr in self.devices:
                print(f"[Manager] Removing handler for {epr} from registry.")
                del self.devices[epr]
                # NOTIFY UI: Tell QML to remove this device from the view
                self.deviceDisconnected.emit(epr)

            # CRITICAL FIX: If the device crashed/disconnected, we MUST clear it from the WSDiscovery cache.
            # Otherwise, WSDiscovery keeps returning the old (broken) IP address in search_services(),
            # leading to a loop of spawning threads that fail to connect.
            if error_occurred and self.discovery:
                print(f"[Manager] Device {epr} had error. Clearing WSDiscovery cache to force fresh probe.")
                try:
                    # Access internal cache maps if they exist (common in python-sdc11073)
                    cleared = False
                    if hasattr(self.discovery, '_services') and epr in self.discovery._services:
                        del self.discovery._services[epr]
                        cleared = True
                    if hasattr(self.discovery, '_remote_services') and epr in self.discovery._remote_services:
                        del self.discovery._remote_services[epr]
                        cleared = True

                    if cleared:
                        print(f"[Manager] Cache for {epr} cleared successfully.")
                except Exception as e:
                    print(f"[Manager] Error clearing cache for {epr}: {e}")

if __name__ == "__main__":

    manager = SdcMyConsumer()
    manager.start()

    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()

    # Expose the manager to QML context
    engine.rootContext().setContextProperty("sdcManager", manager)

    # Load the QML file
    qml_file = os.path.join(os.path.dirname(__file__), "Main.qml")
    engine.load(qml_file)

    if not engine.rootObjects():
        sys.exit(-1)

    sys.exit(app.exec())
