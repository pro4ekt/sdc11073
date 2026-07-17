"""
sdcMyConsumer.py -- Manager (The "Manager") of the SDC device network (ICU mode).
"""
from __future__ import annotations
import asyncio
import logging
import socket
import threading
import time
from .qtDeviceHandler import QtDeviceHandler
from .deviceHandler import DeviceHandler
from .alarms.smartAlertAggregator import SmartAlertAggregator
from PySide6.QtCore import QObject, Signal, Slot, Property
from sdc11073.wsdiscovery import WSDiscovery
_mgr_log = logging.getLogger('sdc.consumer.manager')
def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip
class SdcMyConsumer(QObject):
    deviceConnected = Signal(QtDeviceHandler, arguments=['device'])
    deviceDisconnected = Signal(str, arguments=['epr'])
    roomChanged = Signal(str, arguments=['room'])
    availableRoomsChanged = Signal()
    def __init__(self, target_room: str | None = None, override_ip: str | None = None,
                 tls_mode: str = 'auto', overview_model=None):
        super().__init__()
        self.tls_mode: str = tls_mode
        self.target_room: str | None = target_room
        self.running = True
        self.override_ip: str | None = override_ip
        self.devices = {}
        self.lock = threading.Lock()
        self.aggregator = SmartAlertAggregator(self, overview_model=overview_model)
        self.discovery = None
        self._location_rejected: set[str] = set()
        self._rejected_room_map: dict[str, str] = {}
        self._known_rooms: set[str] = set()
        self._reconnect_cooldown: dict[str, float] = {}
        self.RECONNECT_COOLDOWN_SEC: float = 15.0
        self.discovery_thread = threading.Thread(target=self._run_discovery, daemon=True)
    def start(self):
        self.discovery_thread.start()
        _mgr_log.info('System started. Discovery loop active.')
    def stop(self):
        self.running = False
        _mgr_log.info('Stopping...')
        with self.lock:
            for epr, handler in self.devices.items():
                _mgr_log.info(f'Stopping worker for: {epr[-12:]}')
                handler.stop()
    def _run_discovery(self):
        asyncio.run(self._discovery_loop())
    async def _discovery_loop(self):
        self.manager_loop = asyncio.get_running_loop()
        local_ip = self.override_ip if self.override_ip else get_local_ip()
        _mgr_log.info(f'Network scan | IP={local_ip} | TLS={self.tls_mode}')
        self.discovery = WSDiscovery(local_ip)
        self.discovery.start()
        while self.running:
            try:
                services = await asyncio.to_thread(self.discovery.search_services, timeout=2)
                for service in services:
                    try:
                        epr = str(service.epr).strip()
                        if epr in self._location_rejected:
                            continue
                        if epr in self._reconnect_cooldown:
                            elapsed = time.monotonic() - self._reconnect_cooldown[epr]
                            if elapsed < self.RECONNECT_COOLDOWN_SEC:
                                remaining = int(self.RECONNECT_COOLDOWN_SEC - elapsed)
                                if int(elapsed) % 5 == 0:
                                    _mgr_log.debug(f'Device {epr[-12:]} in cooldown -- retry in {remaining}s.')
                                continue
                            else:
                                del self._reconnect_cooldown[epr]
                                _mgr_log.info(f'Cooldown expired for {epr[-12:]}. Reconnecting...')
                        with self.lock:
                            if epr in self.devices and not self.devices[epr].is_alive():
                                _mgr_log.debug(f'Dead worker for {epr[-12:]}. Cleaning up.')
                                del self.devices[epr]
                            if epr not in self.devices:
                                _mgr_log.info(f'New device: {epr[-12:]}. Spawning worker.')
                                device = DeviceHandler(
                                    service, self,
                                    target_room=self.target_room,
                                    tls_mode=self.tls_mode,
                                )
                                self.devices[epr] = device
                                device.start()
                    except Exception as loop_err:
                        _mgr_log.error(f'Error processing discovered service: {loop_err}')
                await asyncio.sleep(2)
            except Exception as e:
                _mgr_log.error(f'Discovery loop error: {e}')
                await asyncio.sleep(5)
        self.discovery.stop()
    def remove_device(self, epr: str, error_occurred: bool = False,
                      location_filtered: bool = False):
        epr = str(epr).strip()
        if location_filtered:
            self._location_rejected.add(epr)
            _mgr_log.info(
                f'Device {epr[-12:]} location-rejected '
                f"(target room: '{self.target_room}'). Will not reconnect this session."
            )
        with self.lock:
            if epr in self.devices:
                handler = self.devices[epr]
                _mgr_log.info(f'Removing handler for {epr[-12:]}.')
                del self.devices[epr]
                if getattr(handler, '_ui_connected', False):
                    self.deviceDisconnected.emit(epr)
            if error_occurred and self.discovery:
                _mgr_log.info(f'Device {epr[-12:]} had error -- clearing WSDiscovery cache.')
                self._reconnect_cooldown[epr] = time.monotonic()
                try:
                    cleared = False
                    if hasattr(self.discovery, '_services') and epr in self.discovery._services:
                        del self.discovery._services[epr]
                        cleared = True
                    if hasattr(self.discovery, '_remote_services') and epr in self.discovery._remote_services:
                        del self.discovery._remote_services[epr]
                        cleared = True
                    if cleared:
                        _mgr_log.debug(f'WSDiscovery cache cleared for {epr[-12:]}.')
                except Exception as e:
                    _mgr_log.warning(f'Error clearing WSDiscovery cache for {epr[-12:]}: {e}')
    def register_device_room(self, epr: str, room: str) -> None:
        if not room:
            return
        if room not in self._known_rooms:
            self._known_rooms.add(room)
            _mgr_log.info(f'New room: {room!r}. Known rooms: {sorted(self._known_rooms)}')
            self.availableRoomsChanged.emit()
    @Slot(str)
    def switchRoom(self, new_room: str) -> None:
        effective_room: str | None = new_room if new_room else None
        if effective_room == self.target_room:
            return
        old_room = self.target_room
        _mgr_log.info(f'switchRoom: {old_room!r} -> {effective_room!r}')
        self.target_room = effective_room
        with self.lock:
            handlers_snapshot = list(self.devices.values())
        if effective_room is not None:
            stopped = 0
            for handler in handlers_snapshot:
                handler_room = handler._get_device_room()
                if handler_room != effective_room:
                    if handler_room:
                        self._rejected_room_map[handler.epr] = handler_room
                        self._location_rejected.add(handler.epr)
                    handler.stop()
                    stopped += 1
            if stopped:
                _mgr_log.info(f'switchRoom: stopping {stopped} device(s) from other rooms.')
        if effective_room is not None:
            to_unban = [e for e, r in list(self._rejected_room_map.items()) if r == effective_room]
        else:
            to_unban = list(self._rejected_room_map.keys())
        for epr in to_unban:
            self._location_rejected.discard(epr)
            self._rejected_room_map.pop(epr, None)
            self._reconnect_cooldown.pop(epr, None)
        if to_unban:
            _mgr_log.info(f'switchRoom: un-banned {len(to_unban)} device(s) for {effective_room!r}.')
        self.roomChanged.emit(new_room)
    @Property(str, notify=roomChanged)
    def currentRoom(self) -> str:
        return self.target_room if self.target_room else ''
    @Property(list, notify=availableRoomsChanged)
    def availableRooms(self) -> list:
        return sorted(self._known_rooms)
