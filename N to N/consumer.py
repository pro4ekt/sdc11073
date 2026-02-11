from __future__ import annotations

import sys
import sdc11073
import asyncio
import socket
import threading
import time
from copyreg import constructor

import PySide6

from sdc11073.consumer import SdcConsumer
from sdc11073.mdib import ConsumerMdib
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

class DeviceHandler(threading.Thread):
    """
    Worker class (The "Worker").
    Responsible for maintaining a connection to a SINGLE specific device (Provider).
    Runs in its own system thread with its own independent asyncio event loop.
    """
    def __init__(self, wsd_service, manager):
        super().__init__(daemon=True)
        self.wsd_service = wsd_service
        self.epr = str(wsd_service.epr)  # Explicitly convert to string to ensure consistent key usage
        self.manager = manager
        self.running = True
        self.consumer = None
        self.mdib = None
        self.error_occurred = False  # Track if the session ended with an error

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

            self.mdib = ConsumerMdib(self.consumer)
            self.mdib.init_mdib()

            # 4. Subscription (Placeholder for future functionality)
            # observableproperties.bind(self.mdib, metrics_by_handle=self.on_metric_update)
            # observableproperties.bind(self.mdib, alert_by_handle=self.on_alert_update)

            a = self.consumer.mdib.metrics_by_handle
            print(f"[Worker {self.epr}] Connection established. Monitoring...")

            # 5. Lifecycle Loop: Keep running as long as connected
            while self.running:
                if not self.consumer.is_connected:
                    print(f"[Worker {self.epr}] Connection lost reported by SDC stack.")
                    self.error_occurred = True
                    break
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

class QtDeviceHandler(DeviceHandler):
    """
    Specialized Worker class for Qt integration.
    In a real implementation, this would include signals/slots to communicate with the Qt UI thread.
    For this example, it behaves the same as DeviceHandler but is structured for future UI integration.
    """
    def __init__(self, device : DeviceHandler) :
        self.patientName = {}
        self.patienRoom = {}
        self.alarm = {}
        self.metrics = {}
        self.operations = {}

class SdcMyConsumer:
    """
    Manager class (The "Manager").
    Scans the network and spawns a Worker thread for every unique device found.

    ARCHITECTURE NOTE:
    To prevent "Zombie Loops" where a disconnected device is immediately re-discovered
    via the WSDiscovery cache (leading to infinite connect->fail->retry cycles),
    we must explicitly clear the specific device from the WSDiscovery cache in 'remove_device'
    if an error occurred. This forces a fresh network Probe.
    """
    def __init__(self):
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

if __name__ == '__main__':
    manager = SdcMyConsumer()
    manager.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Interrupted by user, stopping...")
        manager.stop()