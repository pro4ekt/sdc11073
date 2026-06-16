from __future__ import annotations
import asyncio
import socket
import threading
import uuid
from typing import TYPE_CHECKING
from qtDeviceHandler import QtDeviceHandler
from deviceHandler import DeviceHandler
from PySide6.QtCore import QObject, Signal, Slot, Property
from sdc11073.wsdiscovery import WSDiscovery
from fhirData import FHIRPatientData

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

    def __init__(self, fhir_data: FHIRPatientData = None):
        super().__init__()
        self.fhir_data = fhir_data  # Данные пациента из FHIR для создания контекстов
        self.running = True
        self.devices = {}  # Registry: { UUID (epr): DeviceHandler_Object }
        self.ensemble_devices = {}  # { epr: ensemble_uuid }
        self.orchestrator_room = "OR-1"  # Жестко зашитая операционная Оркестратора по умолчанию
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

    def get_patient_context_data(self) -> dict:
        """
        Возвращает только нужные для SDC-контекста данные пациента:
        имя (given/family), диагнозы, рост и вес с единицами измерения.
        """
        if not self.fhir_data:
            return {}

        full_name = self.fhir_data.get_name()
        name_parts = full_name.split(' ', 1)
        given_name = name_parts[0] if name_parts else ''
        family_name = name_parts[1] if len(name_parts) > 1 else ''

        conditions = self.fhir_data.get_condition_names()
        birth_date = self.fhir_data.get_birth_date()

        weight_value, weight_unit = None, 'kg'
        height_value, height_unit = None, 'cm'

        for obs in self.fhir_data.get_observation_summaries():
            name_lower = obs['name'].lower()
            try:
                parts = obs['value'].split()
                val = float(parts[0])
                unit = parts[1] if len(parts) > 1 else ''
                if 'weight' in name_lower or 'вес' in name_lower:
                    weight_value, weight_unit = val, unit or 'kg'
                elif 'height' in name_lower or 'length' in name_lower or 'рост' in name_lower:
                    height_value, height_unit = val, unit or 'cm'
            except (ValueError, IndexError):
                pass

        return {
            'given_name':   given_name,
            'family_name':  family_name,
            'birth_date':   birth_date,
            'conditions':   conditions,
            'weight_value': weight_value,
            'weight_unit':  weight_unit,
            'height_value': height_value,
            'height_unit':  height_unit,
        }

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

    async def _ensemble_formation_task(self):
        # Ждем 10 секунд после запуска сети
        await asyncio.sleep(10)
        print("\n[Ensemble Manager] Checking connected devices...")

        with self.lock:
            # Копируем список устройств, чтобы безопасно итерировать
            devices_snapshot = list(self.devices.values())

        if not devices_snapshot:
            print("[Ensemble Manager] No devices found to form an ensemble.")
            return

        from sdc11073.xml_types import pm_qnames as pm

        # Фильтруем устройства: если Room не совпадает с операционной Оркестратора
        # или ActivationState == FAILURE, устройство должно удаляться из массива devices_snapshot
        valid_devices = []
        for dev in devices_snapshot:
            with dev.data_lock:
                if not dev.mdib:
                    continue

                # 1. Считываем LocationContext
                room_str = "Unknown"
                loc_states = dev.mdib.context_states.NODETYPE.get(pm.LocationContextState, [])
                if loc_states and loc_states[0].LocationDetail:
                    room_str = loc_states[0].LocationDetail.Room or "Unknown"

                # 2. Проверяем ActivationState == FAILURE во всех MdsState и VmdState
                has_failure = False
                vmd_states = dev.mdib.states.NODETYPE.get(pm.VmdState, [])
                for s in vmd_states:
                    act_state = getattr(s, 'ActivationState', None)
                    if act_state and str(act_state).lower() in ("fail", "failure"):
                        has_failure = True
                        break

                mds_states = dev.mdib.states.NODETYPE.get(pm.MdsState, [])
                for s in mds_states:
                    act_state = getattr(s, 'ActivationState', None)
                    if act_state and str(act_state).lower() in ("fail", "failure"):
                        has_failure = True
                        break

                # Если комната совпадает с операционной Оркестратора и нет FAILURE
                if room_str == self.orchestrator_room and not has_failure:
                    valid_devices.append(dev)
                else:
                    reason = []
                    if room_str != self.orchestrator_room:
                        reason.append(f"Room mismatch ('{room_str}' != '{self.orchestrator_room}')")
                    if has_failure:
                        reason.append("ActivationState is FAILURE")
                    print(f"[Ensemble Manager] Filtered out device {dev.epr}. Reason: {', '.join(reason)}")

        devices_snapshot = valid_devices

        if not devices_snapshot:
            print("[Ensemble Manager] No valid devices remaining to form an ensemble.")
            return

        # Сбор данных с устройств
        print("-" * 40)
        for dev in devices_snapshot:
            with dev.data_lock:
                # 1. Считываем LocationContext
                loc_str = "Unknown"
                loc_states = dev.mdib.context_states.NODETYPE.get(pm.LocationContextState, [])
                if loc_states and loc_states[0].LocationDetail:
                    detail = loc_states[0].LocationDetail
                    loc_str = f"Facility:{getattr(detail, 'Facility', '')} Room:{getattr(detail, 'Room', '')} Bed:{getattr(detail, 'Bed', '')}"

                # 2. Считываем device_health (из states по Handle)
                health = "Unknown"

                # Ищем стейт с нужным DescriptorHandle через NODETYPE для NumericMetricState
                metric_state = None
                numeric_states = dev.mdib.states.NODETYPE.get(pm.NumericMetricState, [])
                for state in numeric_states:
                    if getattr(state, 'DescriptorHandle', None) == "device_health":
                        metric_state = state
                        break

                if metric_state and getattr(metric_state, 'MetricValue', None) is not None:
                    health = metric_state.MetricValue.Value

                print(f"[Device] EPR: {dev.epr} | Location: {loc_str} | Health: {health}")
        print("-" * 40)

        # Важно: запускаем синхронный input() в отдельном потоке, чтобы не заблочить цикл asyncio
        ans = await asyncio.to_thread(input, "Create Ensemble for these devices? (y/n): ")

        if ans.strip().lower() == 'y':
            ensemble_uuid = str(uuid.uuid4())
            print(f"\n[Ensemble Manager] Creating Ensemble with UUID: {ensemble_uuid}")

            # Сохраняем в память
            for dev in devices_snapshot:
                self.ensemble_devices[dev.epr] = ensemble_uuid

            # Рассылаем UUID на устройства
            for dev in devices_snapshot:
                dev.apply_ensemble_context(ensemble_uuid)
        else:
            print("[Ensemble Manager] Ensemble creation aborted.")

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

        self.manager_loop.create_task(self._ensemble_formation_task())

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
