import asyncio
import socket
import threading
from qtDeviceHandler import QtDeviceHandler
from deviceHandler import DeviceHandler
from PySide6.QtCore import QObject, Signal, Slot, Property
from sdc11073.wsdiscovery import WSDiscovery

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
        self.lock = threading.Lock()  # Ensures safe access to self.devices dictionary
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
        self.manager_loop = asyncio.get_running_loop()  # Сохраняем ссылку на цикл для воркеров

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

                await asyncio.sleep(2)  # Wait before next scan

            except Exception as e:
                print(f"[Manager] Discovery Loop Error: {e}")
                await asyncio.sleep(5)

        self.discovery.stop()

    def remove_device(self, epr, error_occurred=False):
        """
        Callback used by Worker threads to remove themselves from the list
        when connections fail or threads stop.
        """
        epr = str(epr).strip()  # Ensure consistent formatting
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
