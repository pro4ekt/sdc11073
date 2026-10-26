"""
sdcMyConsumer.py -- Manager (The "Manager") of the SDC device network (ICU mode).
"""
from __future__ import annotations

import asyncio
import logging
import socket
import threading
import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .qtDeviceHandler import QtDeviceHandler
from .deviceHandler import DeviceHandler
from .alarms.ensemble_topology_manager import EnsembleTopologyManager
from .alarms.smartAlertAggregator import SmartAlertAggregator
from .topology_filter import room_from_scopes, decide_room

from PySide6.QtCore import QObject, Signal, Slot, Property
from sdc11073.wsdiscovery import WSDiscovery

# Import only for static type checking (avoids any runtime import cost/cycle).
if TYPE_CHECKING:
    from .patientOverviewModel import PatientOverviewModel

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
    # Fix 17 (Ziel 2, DIN EN 60601-1-8 §6.11.2.2.1 b, kapitel3 §3.4.6): a device
    # lost through a communication failure does NOT disappear — it stays in the
    # device list as a technical alarm ("Verbindung verloren") until the same EPR
    # reconnects (deviceReconnected) or staff dismiss it (dismissConnectionLoss →
    # deviceDisconnected).  The state lives ONLY in this manager, the
    # QtDeviceHandler and the PatientOverviewModel — never in the aggregator.
    deviceConnectionLost = Signal(str, str, arguments=['epr', 'cause'])
    deviceReconnected = Signal(str, arguments=['epr'])
    lostDevicesChanged = Signal()
    roomChanged = Signal(str, arguments=['room'])
    availableRoomsChanged = Signal()

    def __init__(
        self,
        target_room: Optional[str] = None,
        override_ip: Optional[str] = None,
        tls_mode: str = 'auto',
        overview_model: Optional['PatientOverviewModel'] = None,
        event_log: Optional[str] = None,
    ) -> None:
        super().__init__()

        # Event journal (JSON Lines) for the Ziel 1–3 recorders; best-effort.
        try:
            from app.metrics import configure_event_log
            _mgr_log.info(f'Event log: {configure_event_log(event_log).path}')
        except Exception as exc:  # pragma: no cover
            _mgr_log.warning(f'event log not configured: {exc}')

        self.tls_mode: str = tls_mode
        self.target_room: Optional[str] = target_room
        self.running: bool = True
        self.override_ip: Optional[str] = override_ip

        self.devices: Dict[str, 'DeviceHandler'] = {}
        self.lock: threading.Lock = threading.Lock()

        # SLOW path: ensemble topology, sensor registry, FHIR (own mutex).
        self.topology = EnsembleTopologyManager(self, overview_model=overview_model)
        # FAST path: alarm processing / two-axis math core (own mutex). Holds a
        # read-only reference to the topology manager for membership snapshots.
        self.aggregator = SmartAlertAggregator(self, self.topology,
                                               overview_model=overview_model)

        self.discovery: Optional[WSDiscovery] = None
        self._overview_model = overview_model
        # Fix 17: epr → {qt, cause, t_abs, patient_id, room, ensemble, escalated_at_loss}
        self._lost_devices: Dict[str, dict] = {}
        self._location_rejected: set[str] = set()
        self._rejected_room_map: Dict[str, str] = {}
        self._known_rooms: set[str] = set()
        self._reconnect_cooldown: Dict[str, float] = {}
        self.RECONNECT_COOLDOWN_SEC: float = 15.0

        self.discovery_thread: threading.Thread = threading.Thread(
            target=self._run_discovery, daemon=True
        )

    def start(self) -> None:
        self.discovery_thread.start()
        _mgr_log.info('System started. Discovery loop active.')

    def stop(self) -> None:
        self.running = False
        _mgr_log.info('Stopping...')

        with self.lock:
            for epr, handler in self.devices.items():
                _mgr_log.info(f'Stopping worker for: {epr[-12:]}')
                handler.stop()

        # Emit end-of-run KPI summaries (best-effort; conditional on SDC_METRICS).
        try:
            from app.metrics import (get_topology_metrics, get_latency_metrics,
                                     get_arr_metrics)
            get_topology_metrics().emit_summary()
            get_latency_metrics().emit_summary()
            get_arr_metrics().emit_summary()
        except Exception:
            pass

    def _run_discovery(self) -> None:
        asyncio.run(self._discovery_loop())

    async def _discovery_loop(self) -> None:
        self.manager_loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
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

                        # ── Ziel 1, discovery phase ──────────────────────────────
                        # Location from the WS-Discovery scopes (sdc.ctxt.loc, parsed
                        # by sdc11073.location.SdcLocation).  A foreign room is
                        # rejected HERE: no TLS, no metadata, no subscription.
                        scopes = [] if service.scopes is None else [str(x) for x in service.scopes.text]
                        scope_room = room_from_scopes(scopes)
                        try:
                            from app.metrics.topology_metrics import get_topology_metrics
                            get_topology_metrics().record_discovered(epr, scopes=scopes, room=scope_room)
                        except Exception:
                            pass
                        if scope_room:
                            self.register_device_room(epr, scope_room)

                        with self.lock:
                            is_rejected = epr in self._location_rejected
                        if is_rejected:
                            continue

                        decision = decide_room(self.target_room, scope_room)
                        if self.target_room and decision == 'reject':
                            with self.lock:
                                self._location_rejected.add(epr)
                                self._rejected_room_map[epr] = scope_room
                            _mgr_log.info(
                                f"Device {epr[-12:]} rejected at discovery: scope room "
                                f"'{scope_room}' != target '{self.target_room}' — no connection, "
                                f"no subscription."
                            )
                            try:
                                from app.metrics.topology_metrics import get_topology_metrics
                                tm = get_topology_metrics()
                                tm.record_decision(epr, 'reject', 'discovery', 'scope_room_mismatch',
                                                   room=scope_room, target_room=self.target_room)
                                tm.record_subscription_avoided(epr, 'discovery', 'scope_room_mismatch')
                            except Exception:
                                pass
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
                                if self.target_room and decision == 'accept':
                                    # provisional: the scope is unauthenticated, the handler
                                    # confirms it over TLS before subscribing (2f)
                                    try:
                                        from app.metrics.topology_metrics import get_topology_metrics
                                        get_topology_metrics().record_decision(
                                            epr, 'accept', 'discovery', 'scope_room_match',
                                            room=scope_room, target_room=self.target_room)
                                    except Exception:
                                        pass
                                device = DeviceHandler(
                                    service, self,
                                    target_room=self.target_room,
                                    tls_mode=self.tls_mode,
                                    scope_room=scope_room,
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
                      location_filtered: bool = False) -> None:
        epr = str(epr).strip()

        if location_filtered:
            with self.lock:
                self._location_rejected.add(epr)
            _mgr_log.info(
                f'Device {epr[-12:]} location-rejected '
                f"(target room: '{self.target_room}'). Will not reconnect this session."
            )
        handler = None
        # Fix 17: a communication failure of a device that was shown to staff
        # becomes a technical alarm instead of a silent removal.
        technical_alarm = False
        with self.lock:
            if epr in self.devices:
                handler = self.devices[epr]
                _mgr_log.info(f'Removing handler for {epr[-12:]}.')
                del self.devices[epr]
                if getattr(handler, '_ui_connected', False):
                    technical_alarm = bool(error_occurred) and not location_filtered
                    if not technical_alarm:
                        self.deviceDisconnected.emit(epr)
        # Ziel 1/2 events (best-effort): the decision itself was recorded by the
        # handler at its phase; here only the departure.
        try:
            from app.metrics.topology_metrics import get_topology_metrics
            cause = 'location' if location_filtered else ('error' if error_occurred else 'graceful')
            get_topology_metrics().record_departed(epr, cause)
        except Exception:
            pass
        # Fix 9 — what the departed device contributed: its ensemble and its alert
        # keys (AlertCondition handles), read from the handler's MDIB copy before
        # the reference is dropped.  Best-effort: [] if the MDIB lock is busy.
        departed_ensemble: Optional[str] = getattr(handler, 'ensemble_uuid', None) if handler else None
        departed_keys: List[str] = []
        if handler is not None:
            try:
                departed_keys = [ch[0] for ch in handler.enumerate_alert_channels()]
            except Exception as exc:
                _mgr_log.debug(f'enumerate_alert_channels failed for {epr[-12:]} at removal: {exc}')
        # Fix 17 / A1 — read BEFORE release_device (the ensemble and its latch may
        # be torn down below): was the patient's ensemble escalated (Red / latched)
        # at the moment the connection was lost, and which patient / room is it?
        # Normally the handler already raised the alarm through
        # notify_connection_lost (before its unsubscribe attempt); then nothing
        # is raised again here.
        with self.lock:
            already_raised = epr in self._lost_devices
        escalated_at_loss = False
        lost_patient_id, lost_room = '', ''
        if technical_alarm and not already_raised:
            lost_patient_id, lost_room, escalated_at_loss = self._lost_context(handler, departed_ensemble)
        # Release per-ensemble state so a discharged/disconnected device does not
        # leak topology (membership / registry / FHIR) or processor (alarm /
        # adaptive) state.  Called outside self.lock (SdcMyConsumer.lock) — each
        # component manages its own internal locks.
        #   1. Topology teardown returns the orphaned ensemble UUID (or None if the
        #      ensemble still has other members).
        #   2. If orphaned, tell the Alert Processor to discard that ensemble.
        #   3. Fix 9: tell the Alert Processor about the departure so the patient
        #      card is removed (orphaned) or re-pushed with the current member
        #      count / status, and the departed device's alarm keys leave the
        #      TTL cache (docs/model/SPEC_DELTA.md SD-2).
        topo = getattr(self, 'topology', None)
        orphaned = None
        if topo is not None:
            try:
                orphaned = topo.release_device(epr)
            except Exception as exc:
                _mgr_log.warning(f'topology.release_device failed for {epr[-12:]}: {exc}')
        agg = getattr(self, 'aggregator', None)
        if agg is not None and orphaned:
            try:
                agg.discard_ensemble(orphaned)
            except Exception as exc:
                _mgr_log.warning(
                    f'aggregator.discard_ensemble failed for {orphaned[:8]}: {exc}'
                )
        if agg is not None and (orphaned or departed_ensemble):
            try:
                if orphaned:
                    agg.on_member_departed(orphaned, orphaned=True)
                else:
                    agg.on_member_departed(departed_ensemble, orphaned=False,
                                           departed_alert_keys=departed_keys)
            except Exception as exc:
                _mgr_log.warning(
                    f'aggregator.on_member_departed failed for {epr[-12:]}: {exc}'
                )
        # Fix 17: fallback for an error exit that did not pass through
        # notify_connection_lost — raised AFTER the aggregator / topology path, so
        # nothing of the lost state can reach M, E(t), SDC_score or the latch.
        if technical_alarm and not already_raised:
            self._raise_technical_alarm(
                epr, handler,
                cause=str(getattr(handler, 'fallback_cause', None) or 'error'),
                patient_id=lost_patient_id, room=lost_room,
                ensemble=departed_ensemble, escalated_at_loss=escalated_at_loss,
            )
        with self.lock:
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

    # =========================================================================
    # Fix 17 — technical alarm "Verbindung verloren" (Ziel 2)
    # =========================================================================

    def _lost_context(self, handler: Any, ensemble: Optional[str]) -> tuple[str, str, bool]:
        """(patient_id, room, escalated_at_loss) for a lost device — read from
        the topology / aggregator while they still hold the ensemble (A1),
        i.e. BEFORE release_device; QtDeviceHandler values as fallback."""
        patient_id, room, escalated = '', '', False
        topo = getattr(self, 'topology', None)
        agg = getattr(self, 'aggregator', None)
        if ensemble:
            try:
                if topo is not None:
                    patient_id, room = topo.reverse_lookup_patient_room(ensemble)
            except Exception as exc:
                _mgr_log.debug(f'reverse_lookup_patient_room failed: {exc}')
            try:
                if agg is not None:
                    escalated = bool(agg.is_ensemble_escalated(ensemble))
            except Exception as exc:
                _mgr_log.debug(f'is_ensemble_escalated failed: {exc}')
        qt = getattr(handler, 'qtDeviceHandler', None)
        if not patient_id and qt is not None:
            patient_id = str(getattr(qt, 'patientId', '') or '')
        if not room and qt is not None:
            room = str(getattr(qt, 'patientRoom', '') or '')
        return patient_id, room, escalated

    def notify_connection_lost(self, epr: str, handler: Any, cause: str) -> None:
        """Called by DeviceHandler (worker thread) right after its watchdog
        declared the loss and BEFORE its graceful-unsubscribe attempt, so the
        technical alarm does not wait for the unsubscribe timeout.  The
        aggregator is still intact here (latch read before release_device);
        the aggregator path itself runs later in remove_device.  Idempotent."""
        epr = str(epr).strip()
        with self.lock:
            if epr in self._lost_devices or epr not in self.devices:
                return
        if not getattr(handler, '_ui_connected', False):
            return
        ensemble = getattr(handler, 'ensemble_uuid', None)
        patient_id, room, escalated = self._lost_context(handler, ensemble)
        self._raise_technical_alarm(epr, handler, cause=cause, patient_id=patient_id, room=room,
                                    ensemble=ensemble, escalated_at_loss=escalated)

    def _raise_technical_alarm(self, epr: str, handler: Any, cause: str,
                               patient_id: str, room: str, ensemble: Optional[str],
                               escalated_at_loss: bool) -> None:
        """Keep the lost device visible: COMM_FAILURE on its card, "Verbindung
        verloren" on the patient card, one ``technical_alarm raised`` event."""
        t_abs = time.time()
        qt = getattr(handler, 'qtDeviceHandler', None)
        with self.lock:
            self._lost_devices[epr] = {
                'qt': qt, 'cause': cause, 't_abs': t_abs, 'patient_id': patient_id,
                'room': room, 'ensemble': ensemble, 'escalated_at_loss': bool(escalated_at_loss),
            }
        _mgr_log.error(
            f'[TECH-ALARM] Verbindung verloren: {epr[-12:]} cause={cause} patient={patient_id!r} '
            f"room={room!r}{' DURING ESCALATION' if escalated_at_loss else ''} — stays visible until "
            f'reconnect or dismissal.'
        )
        if qt is not None and hasattr(qt, 'markConnectionLost'):
            try:
                qt.markConnectionLost(cause, t_abs, bool(escalated_at_loss))
            except Exception as exc:
                _mgr_log.warning(f'markConnectionLost failed for {epr[-12:]}: {exc}')
        ov = self._overview_model
        if ov is not None and hasattr(ov, 'markLost'):
            try:
                ov.markLost(patient_id, room, epr, cause, bool(escalated_at_loss), t_abs)
            except Exception as exc:
                _mgr_log.warning(f'overview.markLost failed for {epr[-12:]}: {exc}')
        self._emit_technical_alarm_event(epr, 'raised', cause, patient_id, room, ensemble,
                                         escalated_at_loss, t_abs)
        self.deviceConnectionLost.emit(epr, cause)
        self.lostDevicesChanged.emit()

    def _clear_technical_alarm(self, epr: str, action: str) -> Optional[dict]:
        """``action`` ∈ {cleared (reconnect), dismissed (staff)}.  Returns the
        lost-record, or None if the EPR was not in the lost state."""
        with self.lock:
            rec = self._lost_devices.pop(epr, None)
        if rec is None:
            return None
        ov = self._overview_model
        if ov is not None and hasattr(ov, 'clearLost'):
            try:
                ov.clearLost(epr)
            except Exception as exc:
                _mgr_log.warning(f'overview.clearLost failed for {epr[-12:]}: {exc}')
        self._emit_technical_alarm_event(epr, action, rec['cause'], rec['patient_id'], rec['room'],
                                         rec['ensemble'], rec['escalated_at_loss'], rec['t_abs'])
        _mgr_log.info(f'[TECH-ALARM] {action}: {epr[-12:]} (lost since {rec["t_abs"]:.3f}).')
        self.lostDevicesChanged.emit()
        return rec

    @staticmethod
    def _emit_technical_alarm_event(epr: str, action: str, cause: str, patient_id: str, room: str,
                                    ensemble: Optional[str], escalated_at_loss: bool,
                                    t_abs: float) -> None:
        try:
            from app.metrics.latency_metrics import get_latency_metrics
            get_latency_metrics().record_technical_alarm(
                epr, action, cause=cause, patient_id=patient_id or None, room=room or None,
                ensemble=ensemble, escalated_at_loss=bool(escalated_at_loss), lost_since_abs=t_abs)
        except Exception:
            pass

    def on_device_connected(self, device: QtDeviceHandler) -> None:
        """Called by DeviceHandler._phase_setup_qt (worker thread) once the device
        is ready for the UI.  Clears a pending technical alarm of the same EPR
        (reconnect) and emits ``deviceConnected``."""
        epr = str(getattr(device, 'epr', '') or '').strip()
        if epr and self._clear_technical_alarm(epr, 'cleared') is not None:
            self.deviceReconnected.emit(epr)
        self.deviceConnected.emit(device)

    @Slot(str)
    def dismissConnectionLoss(self, epr: str) -> None:
        """Staff dismiss the technical alarm: the device leaves the list, the
        patient card loses its "Verbindung verloren" marker.  A reconnect of the
        EPR later is an ordinary new connection."""
        epr = str(epr).strip()
        if self._clear_technical_alarm(epr, 'dismissed') is not None:
            self.deviceDisconnected.emit(epr)

    @Property(list, notify=lostDevicesChanged)
    def lostDevices(self) -> List[dict]:
        """EPRs in the technical-alarm state with cause and timestamp (QML)."""
        with self.lock:
            return [
                {'epr': e, 'cause': r['cause'], 'since': r['t_abs'], 'patientId': r['patient_id'],
                 'room': r['room'], 'escalatedAtLoss': r['escalated_at_loss']}
                for e, r in self._lost_devices.items()
            ]

    def register_device_room(self, epr: str, room: str) -> None:
        if not room:
            return

        if room not in self._known_rooms:
            self._known_rooms.add(room)
            _mgr_log.info(f'New room: {room!r}. Known rooms: {sorted(self._known_rooms)}')
            self.availableRoomsChanged.emit()

    @Slot(str)
    def switchRoom(self, new_room: str) -> None:
        effective_room: Optional[str] = new_room if new_room else None
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
    def availableRooms(self) -> List[str]:
        return sorted(self._known_rooms)
