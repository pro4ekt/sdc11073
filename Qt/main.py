from __future__ import annotations

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
from sdc11073.mdib.statecontainers import LocationContextStateContainer
from sdc11073.wsdiscovery import WSDiscovery
from sdc11073.xml_types import pm_qnames as pm
from sdc11073.xml_types.pm_qnames import LocationContextState

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
    # Add other signals as needed
    deviceValueChanged = Signal()
    alarmStatusChanged = Signal()
    priorityChanged = Signal()
    metricsChanged = Signal() # ADDED: Signal for metrics list

    # Add signal for EPR if needed, though usually constant
    eprChanged = Signal()

    connectedChanged = Signal()

    def __init__(self, device : DeviceHandler) :
        super().__init__()
        self._device = device # Keep reference (WeakRef recommended in production)

        # Initialize defaults
        self._patientRoom = "Unknown"
        self._patientName = "Unknown"

        # Placeholder data for UI - these would come from MDIB in real app
        self._deviceValue = "---"
        self._alarmStatus = "Off"
        self._priority = "3"
        self._metrics = [] # ADDED: Initialize list

        # Initial Data Fetch (Snapshot)
        self.update_data()

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

            # 2. Metrics (Dynamic)
            # Find all NumericMetricStates
            metric_states = [m for m in self._device.mdib.states.objects if m.NODETYPE == pm.NumericMetricState]

            new_metrics_list = []

            for state in metric_states:
                # Find corresponding descriptor to get the Name/Label
                descriptor = self._device.mdib.descriptions.handle.get_one(state.DescriptorHandle)

                # Determine Name
                metric_name = "Unknown Metric"
                if descriptor and descriptor.Type:
                    # Try to get a readable name (Coding System or CodeId)
                    metric_name = descriptor.Type.CodeId or descriptor.Handle

                # Determine Value
                metric_value = "---"
                if state.MetricValue and state.MetricValue.Value is not None:
                    metric_value = str(state.MetricValue.Value)

                new_metrics_list.append({
                    "metricname": metric_name,
                    "value": metric_value,
                    "alarm": "Off", # Placeholder
                    "timeout": 0
                })

            # Simple diff check or just emit (optimization: equality check on list content)
            self._metrics = new_metrics_list
            self.metricsChanged.emit()

            # 3. Main Page Value (Just take the first one found)
            if self._metrics:
                new_val = str(self._metrics[0]['value'])
                if self._deviceValue != new_val:
                    self._deviceValue = new_val
                    self.deviceValueChanged.emit()
            else:
                self._deviceValue = "---"
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

class DeviceHandler(QObject, threading.Thread):
    """
    Worker class (The "Worker").
    Responsible for maintaining a connection to a SINGLE specific device (Provider).
    Runs in its own system thread with its own independent asyncio event loop.
    """
    # Define a signal to trigger updates on the Qt object safely across threads
    updateTick = Signal()

    def __init__(self, wsd_service, manager):
        # Initialize both QObject and Thread
        QObject.__init__(self)
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
            self.consumer.start_all()

            with self.data_lock:
                self.mdib = ConsumerMdib(self.consumer)
                self.mdib.init_mdib()

            # 4. Subscription (Placeholder for future functionality)
            # observableproperties.bind(self.mdib, metrics_by_handle=self.on_metric_update)
            # observableproperties.bind(self.mdib, alert_by_handle=self.on_alert_update)

            print(f"[Worker {self.epr}] Connection established. Monitoring...")

            # 1. Создаем Qt-обертку. Сейчас она "принадлежит" этому рабочему потоку.
            self.qtDeviceHandler = QtDeviceHandler(self)

            # 2. ВАЖНО: Перемещаем объект в главный поток UI.
            # Без этого QML может ругаться при попытке доступа к свойствам/слотам.
            main_thread = QGuiApplication.instance().thread()
            if main_thread:
                self.qtDeviceHandler.moveToThread(main_thread)

                # Connect the worker's signal to the handler's slot
                # This ensures update_data() runs in the Main Thread when triggered
                self.updateTick.connect(self.qtDeviceHandler.handleUpdateTick)
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

                # Trigger update on UI thread safely
                self.updateTick.emit()

                await asyncio.sleep(1)

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

        # Start the Discovery Loop in a background thread
        self.discovery_thread = threading.Thread(target=self._run_discovery, daemon=True)

    def start(self):
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
        local_ip = get_local_ip()
        print(f"[Manager] Network Scan on IP: {local_ip}")

        self.discovery = WSDiscovery(local_ip)
        self.discovery.start()

        while self.running:
            try:
                # 1. Search for services (2 second timeout)
                services = await asyncio.to_thread(self.discovery.search_services, timeout=2)

                # 2. Process results
                for service in services:
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
                            # We already emit the signal in the worker thread when the device is connected
                        else:
                            # We already have a worker for this device, ignore it.
                            pass

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