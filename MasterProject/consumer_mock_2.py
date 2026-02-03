from __future__ import annotations

import asyncio
import socket
import threading
import time
# import winsound
from copy import deepcopy
from decimal import Decimal

from sdc11073 import observableproperties
from sdc11073.consumer import SdcConsumer
from sdc11073.mdib import ConsumerMdib
from sdc11073.wsdiscovery import WSDiscovery
# from sdc11073.xml_types.pm_types import AlertSignalPresence, MeasurementValidity
# from myDbClass.dbworker import DBWorker


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
        self.epr = wsd_service.epr
        self.manager = manager
        self.running = True
        self.consumer = None
        self.mdib = None

    def run(self):
        # 1. Isolation: Create a new asyncio event loop for this thread.
        # This ensures network delays on this device don't affect others.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(self._worker_logic())
        finally:
            loop.close()
            # 2. Self-cleanup: When the thread dies, remove self from Manager's registry.
            self.manager.remove_device(self.epr)

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

            print(f"[Worker {self.epr}] Connection established. Monitoring...")

            # 5. Lifecycle Loop: Keep running as long as connected
            while self.running:
                if not self.consumer.is_connected:
                    print(f"[Worker {self.epr}] Connection lost reported by SDC stack.")
                    break
                await asyncio.sleep(1)

        except Exception as e:
            print(f"[Worker {self.epr}] Critical Error: {e}")
        finally:
            if self.consumer:
                print(f"[Worker {self.epr}] Stopping consumer resources...")
                try:
                    self.consumer.stop_all()
                except:
                    pass

    def stop(self):
        self.running = False


class SdcMyConsumer:
    """
    Manager class (The "Manager").
    Scans the network and spawns a Worker thread for every unique device found.
    """
    def __init__(self):
        self.running = True
        self.devices = {}  # Registry: { UUID (epr): DeviceHandler_Object }
        self.lock = threading.Lock() # Ensures safe access to self.devices dictionary

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

        discovery = WSDiscovery(local_ip)
        discovery.start()

        while self.running:
            try:
                # 1. Search for services (2 second timeout)
                services = await asyncio.to_thread(discovery.search_services, timeout=2)

                # 2. Process results
                for service in services:
                    #Вот тут вот может быть в теории проблема с epr
                    epr = service.epr

                    with self.lock:
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

        discovery.stop()

    def remove_device(self, epr):
        """
        Callback used by Worker threads to remove themselves from the list
        when connections fail or threads stop.
        """
        with self.lock:
            if epr in self.devices:
                print(f"[Manager] Removing handler for {epr} from registry.")
                del self.devices[epr]


if __name__ == '__main__':
    app = SdcMyConsumer()
    app.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        app.stop()