"""
handler.py — DeviceHandler: worker thread for one SDC device.

ARCHITECTURE:
  Each discovered device gets its own DeviceHandler (threading.Thread) with
  an isolated asyncio event loop.  Network timeouts on one device do not
  affect others.

  Responsibilities are delegated to focused sub-modules:
    AlarmManager  — alarm ack, ack-timeout tracking        (alarm_manager.py)
    context_ops   — apply_ensemble_context / apply_fhir_contexts (context_ops.py)
    ssl_builder   — build_ssl_container                    (ssl_builder.py)

LIFECYCLE:
  1. Manager creates DeviceHandler and calls start().
  2. Thread connects → initialises MDIB → subscribes → starts monitoring loop.
  3. On disconnect (or error) thread exits → calls manager.remove_device().
"""

from __future__ import annotations

import threading
import asyncio
import time
import logging
from typing import Any, Optional

from sdc11073.consumer import SdcConsumer
from sdc11073.mdib import ConsumerMdib
from sdc11073.pysoap.soapclient import HTTPReturnCodeError
from sdc11073.xml_types.actions import periodic_actions
from sdc11073.xml_types import pm_qnames as pm
from sdc11073.xml_types import pm_types
from sdc11073 import observableproperties

from PySide6.QtCore import QCoreApplication

from .alarm_manager import AlarmManager
from . import context_ops
from .ssl_builder import build_ssl_container
from app.alarms.device_profile_repo import get_repository, DeviceReliabilityProfile


class DeviceHandler(threading.Thread):
    """
    Worker thread that manages the full lifecycle of ONE SDC device connection.

    Public API (called by Manager / QtDeviceHandler):
      start()                   — inherited from Thread; kicks off run()
      stop()                    — request graceful exit
      acknowledge_alarm(...)    — DEV-31: send Ack to the device
      apply_ensemble_context()  — bind this device to an ensemble UUID
      apply_fhir_contexts()     — write FHIR DangerCodes to WorkflowContext
      _get_device_room()        — read LocationContext from MDIB (thread-safe)
    """

    def __init__(
        self,
        wsd_service: Any,
        manager: Any,
        target_room: str | None = None,
        tls_mode: str = 'auto',
    ) -> None:
        """
        Parameters:
          wsd_service  — WSDiscovery service object (EPR + x_addrs).
          manager      — SdcMyConsumer (Manager) reference.
          target_room  — LocationContext.Room filter; None = accept all rooms.
          tls_mode     — 'auto' | 'force_tls' | 'no_tls'
        """
        threading.Thread.__init__(self, daemon=True)

        self.tls_mode: str = tls_mode
        self.target_room: str | None = target_room
        self.wsd_service = wsd_service
        self.epr: str = str(wsd_service.epr)
        self.manager = manager

        # --- Runtime state ---
        self.running: bool = True
        self.consumer: Any = None
        # DPWS device identity — populated in _phase_connect after start_all().
        # Used by ClinicalRiskFilter to look up calibrated _DEVICE_PROFILES.
        self.manufacturer: str = ''
        self.model: str = ''
        self.mdib: Any = None
        self.qtDeviceHandler: Any = None
        self.opcua_server: Any = None
        self.ensemble_uuid: str | None = None

        # --- Flags ---
        self._ui_connected: bool = False       # True after deviceConnected.emit()
        self.error_occurred: bool = False      # True on abnormal exit
        self._location_filtered: bool = False  # True when rejected by room filter
        self._intentional_shutdown: bool = False

        # --- Shared state ---
        self._last_ui_update_ts: float = 0.0
        # handle → BICEPS/LOINC concept code (built once after init_mdib)
        self._handle_to_concept: dict[str, str] = {}
        # Per-device calibration cache: concept → (roc_limit, reliability_profile)
        # Pre-fetched from DeviceProfileRepository right after _build_semantic_map().
        # Passed to SmartAlertAggregator.check_alert_validity() on each alarm event
        # so pipeline filters never query the database at event time.
        self._device_calibration: dict[str, tuple[Any, Optional[DeviceReliabilityProfile]]] = {}
        # AlarmCoordinator suppressed condition handles.
        # When Stage 1 or Stage 2 suppresses an alarm, its condition DescriptorHandle
        # is added here so the UI layer and paired signal can also be hidden.
        # Cleared automatically when the alarm goes inactive.
        self._pipeline_suppressed: set[str] = set()

        # --- Synchronisation ---
        self.data_lock = threading.Lock()

        # --- Sub-modules ---
        self.alarm_manager = AlarmManager(self)

        # --- Logger (last 12 chars of UUID for quick identification) ---
        _short = self.epr[-12:] if len(self.epr) > 12 else self.epr
        self.logger = logging.getLogger(f'sdc.consumer.worker.{_short}')

    # =========================================================================
    # Backward-compat properties / delegation
    # =========================================================================

    @property
    def ACK_TIMEOUT_SEC(self) -> float:
        return self.alarm_manager.ACK_TIMEOUT_SEC

    @property
    def _ack_timestamps(self) -> dict[str, float]:
        return self.alarm_manager._ack_timestamps

    def acknowledge_alarm(self, operation_handle: str, alert_signal_handle: str) -> None:
        """Delegate to AlarmManager (DEV-31)."""
        self.alarm_manager.acknowledge_alarm(operation_handle, alert_signal_handle)

    def _reactivate_alarm(self, operation_handle: str, alert_signal_handle: str) -> None:
        """Delegate to AlarmManager (ack-timeout re-raise)."""
        self.alarm_manager.reactivate_alarm(operation_handle, alert_signal_handle)

    def apply_ensemble_context(self, ensemble_uuid: str) -> bool:
        """Delegate to context_ops."""
        return context_ops.apply_ensemble_context(self, ensemble_uuid)

    def apply_fhir_contexts(self, fhir_data: Any) -> None:
        """Delegate to context_ops."""
        context_ops.apply_fhir_contexts(self, fhir_data)

    # =========================================================================
    # Thread entry point
    # =========================================================================

    def run(self) -> None:
        """
        Called automatically by threading.Thread.start().
        Creates an isolated asyncio event loop and runs _worker_logic() inside it.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._worker_logic())
        finally:
            try:
                loop.close()
            except Exception:
                pass
            self.manager.remove_device(
                self.epr,
                self.error_occurred,
                location_filtered=self._location_filtered,
            )
            self.logger.info('Thread Exiting (Dead).')

    # =========================================================================
    # Async orchestrator
    # =========================================================================

    async def _worker_logic(self) -> None:
        """
        Top-level async logic — connects to the device and runs the monitoring loop.

        Phases:
          1. Connect (SdcConsumer, TLS auto-detect)
          2. Init MDIB (GetMdib, semantic map, alert map)
          2b. Location filter
          2c+3. Subscribe to push notifications
          3b. Ensemble context binding (SmartAlertAggregator)
          3c. Snapshot initial alert states
          4. Create QtDeviceHandler, move to UI thread
          5. Main monitoring loop (T_fallback / IHE SDPi)
        """
        self.logger.info('Connecting...')
        try:
            await self._phase_connect()

            with self.data_lock:
                self._phase_init_mdib()
                if self._phase_check_location():
                    return  # location-filtered → exit cleanly

            self._phase_subscribe()
            self.logger.info('Connection established. Monitoring...')

            await self._phase_aggregate_on_connect()
            self._phase_snapshot_initial_alerts()
            self._phase_setup_qt()

            await self._monitoring_loop()

        except Exception as e:
            self.logger.error(f'Critical Error ({type(e).__name__}): {e}', exc_info=True)
            self.error_occurred = True
        finally:
            if self.consumer:
                self.logger.info('Stopping consumer resources (DEV-49 graceful)...')
                try:
                    await self._graceful_shutdown()
                except Exception as e:
                    self.logger.error(f'Graceful shutdown failed ({e}), forcing stop.')
                    try:
                        self.consumer.stop_all()
                    except Exception:
                        pass

    # =========================================================================
    # Phase 1: Connect — SdcConsumer + TLS auto-detect / fallback
    # =========================================================================

    async def _phase_connect(self) -> None:
        """Create SdcConsumer, start subscriptions.  TLS fallback on ConnectionReset."""
        x_addrs = getattr(self.wsd_service, 'x_addrs', 'unknown')
        self.logger.debug(f'Transport addresses (x_addrs): {x_addrs}')

        ssl_container = self._resolve_ssl_container(x_addrs)
        self.consumer = SdcConsumer.from_wsd_service(
            wsd_service=self.wsd_service,
            ssl_context_container=ssl_container,
        )

        try:
            self.consumer.start_all(not_subscribed_actions=periodic_actions)
            self._extract_dpws_metadata()
        except Exception as connect_err:
            cause = connect_err.__cause__ or connect_err
            is_reset = (
                isinstance(cause, ConnectionResetError)
                or (isinstance(cause, OSError) and getattr(cause, 'winerror', None) == 10054)
                or 'NotConnected' in type(connect_err).__name__
            )
            # TLS fallback: 'auto' mode only, connection-reset errors only
            if is_reset and ssl_container is None and self.tls_mode != 'no_tls':
                self.logger.warning(
                    'HTTP connection reset — provider likely requires TLS. '
                    'Retrying with mTLS SSL context...'
                )
                ssl_container = build_ssl_container(self.logger)
                self.consumer = SdcConsumer.from_wsd_service(
                    wsd_service=self.wsd_service,
                    ssl_context_container=ssl_container,
                )
                self.consumer.start_all(not_subscribed_actions=periodic_actions)
                self._extract_dpws_metadata()
            else:
                raise

    def _extract_dpws_metadata(self) -> None:
        """
        Reads DPWS ThisModel metadata from consumer.host_description (fetched
        during start_all → _get_metadata).  Populates self.manufacturer and
        self.model so ClinicalRiskFilter can look up calibrated _DEVICE_PROFILES.

        Fail-safe: any exception leaves manufacturer/model as '' (fail-open:
        ClinicalRiskFilter falls back to _FAIL_SAFE_PROFILE, LR+ = 1.0).
        """
        try:
            host_desc = getattr(self.consumer, 'host_description', None)
            this_model = getattr(host_desc, 'this_model', None) if host_desc else None
            if this_model:
                mfrs  = getattr(this_model, 'Manufacturer', []) or []
                mdls  = getattr(this_model, 'ModelName', []) or []
                self.manufacturer = mfrs[0].text if mfrs else ''
                self.model        = mdls[0].text if mdls else ''
                self.logger.info(
                    f'DPWS metadata: manufacturer={self.manufacturer!r}, '
                    f'model={self.model!r}'
                )
            else:
                self.logger.debug('DPWS host_description has no this_model — using fail-safe profiles.')
        except Exception as exc:
            self.logger.debug(f'DPWS metadata extraction failed (fail-safe): {exc}')

    def _resolve_ssl_container(self, x_addrs: Any) -> Any:
        """Return an SSLContextContainer (or None) based on tls_mode and announced addresses."""
        if self.tls_mode == 'no_tls':
            self.logger.info('TLS disabled (--no_tls) — plain HTTP, no fallback.')
            return None
        if self.tls_mode == 'force_tls':
            self.logger.info('TLS forced (--tls) — building SSL context...')
            return build_ssl_container(self.logger)
        # 'auto': detect from announced address scheme
        if x_addrs and any(str(a).startswith('https://') for a in x_addrs):
            self.logger.info('HTTPS detected — building mTLS SSL context...')
            return build_ssl_container(self.logger)
        self.logger.info('HTTP announced — connecting without SSL (will retry if reset).')
        return None

    # =========================================================================
    # Phase 2: Init MDIB  (must be called while data_lock is held)
    # =========================================================================

    def _phase_init_mdib(self) -> None:
        """Init ConsumerMdib via GetMdib, build semantic + alert maps."""
        self.mdib = ConsumerMdib(self.consumer)
        self.mdib.init_mdib()
        self._log_mdib_diagnostics()
        self._build_semantic_map()
        self._prefetch_device_calibration()   # Repository pattern: 1 point-query per concept
        self._log_alert_map()

    def _log_mdib_diagnostics(self) -> None:
        ctx_states = list(self.mdib.context_states.objects)
        n = len(ctx_states)
        if n > 0:
            self.logger.info(f'[DIAG] GetMdib returned {n} context state(s).')
            for s in ctx_states:
                self.logger.debug(
                    f'[DIAG]   context_state: type={s.NODETYPE.localname}, '
                    f'handle={s.Handle}, descriptor={s.DescriptorHandle}'
                )
        else:
            self.logger.warning('[DIAG] GetMdib returned 0 context states.')
        self.logger.info(
            f'[DIAG] context_service_client available: '
            f'{self.consumer.context_service_client is not None}'
        )

    def _build_semantic_map(self) -> None:
        """Map NumericMetricDescriptor handles → BICEPS/LOINC concept codes."""
        for desc in self.mdib.descriptions.NODETYPE.get(pm.NumericMetricDescriptor, []):
            try:
                code: str | None = None
                t = getattr(desc, 'Type', None)
                if t is not None:
                    code = getattr(t, 'Code', None)
                    if not code:
                        cd = getattr(t, 'ConceptDescription', None) or []
                        if cd:
                            code = getattr(cd[0], 'text', None)
                if not code:
                    code = desc.Handle   # last resort: use handle as the code
                if code:
                    self._handle_to_concept[desc.Handle] = str(code)
                    self.logger.debug(f'[SemanticMap]   handle={desc.Handle!r} → concept={code!r}')
            except Exception:
                pass
        self.logger.info(
            f'[SemanticMap] Mapped {len(self._handle_to_concept)} '
            f'NumericMetricDescriptor handle(s) to concept codes.'
        )

    def _prefetch_device_calibration(self) -> None:
        """
        Repository pattern: make exactly one point-query per concept for this device.

        Called immediately after _build_semantic_map() so that self._handle_to_concept
        is already populated.  Results are stored in self._device_calibration:
            concept → (roc_limit: float | None, reliability_profile: DeviceReliabilityProfile | None)

        The aggregator and pipeline filters read these pre-fetched values from the
        DeviceAlertEvidence DTO — they never touch the database at alarm event time.
        """
        repo = get_repository()
        self._device_calibration = {}
        concepts = set(self._handle_to_concept.values())
        for concept in concepts:
            roc_limit = repo.get_roc_limit(concept)
            profile   = repo.get_profile(self.manufacturer, self.model, concept)
            self._device_calibration[concept] = (roc_limit, profile)
        self.logger.info(
            f'[DeviceCalibration] Pre-fetched {len(self._device_calibration)} concept(s) '
            f'for {self.manufacturer!r}/{self.model!r} — '
            f'{sum(1 for _, (_, p) in self._device_calibration.items() if p is not None)} '
            f'profile(s) found, rest use fail-safe (LR+=1.0).'
        )

    def _log_alert_map(self) -> None:
        cond_descs = self.mdib.descriptions.NODETYPE.get(pm.AlertConditionDescriptor, [])
        sig_descs  = self.mdib.descriptions.NODETYPE.get(pm.AlertSignalDescriptor, [])
        self.logger.info(
            f'[AlarmMap] MDIB contains {len(cond_descs)} AlertCondition '
            f'and {len(sig_descs)} AlertSignal descriptor(s).'
        )
        for d in cond_descs:
            self.logger.debug(
                f'[AlarmMap]   Condition: handle={d.Handle!r} '
                f'code={getattr(getattr(d, "Type", None), "Code", "N/A")!r}'
            )
        for d in sig_descs:
            self.logger.debug(
                f'[AlarmMap]   Signal:    handle={d.Handle!r} '
                f'code={getattr(getattr(d, "Type", None), "Code", "N/A")!r} '
                f'manifestation={getattr(d, "Manifestation", "N/A")!r}'
            )

    # =========================================================================
    # Phase 2b: Location filter  (must be called while data_lock is held)
    # =========================================================================

    def _phase_check_location(self) -> bool:
        """
        Read LocationContext directly from MDIB (lock already held) and
        apply the target_room filter.

        Returns True if the device should be rejected (caller must return).
        """
        device_room = ''
        try:
            loc_states = [s for s in self.mdib.context_states.objects
                          if s.NODETYPE == pm.LocationContextState]
            if loc_states and loc_states[0].LocationDetail:
                device_room = loc_states[0].LocationDetail.Room or ''
        except Exception as e:
            self.logger.error(f'Error reading LocationContext: {e}')

        # Register room with Manager for the room-switcher dropdown
        if device_room and hasattr(self.manager, 'register_device_room'):
            self.manager.register_device_room(self.epr, device_room)

        if not self.target_room:
            return False  # no filter active → accept all

        if device_room == '':
            self.logger.warning(
                f'No LocationContext found in MDIB. '
                f"Room filter (target='{self.target_room}') skipped — accepting device."
            )
            return False

        if device_room != self.target_room:
            self.logger.info(
                f"Location filter: device room '{device_room}' "
                f"!= target '{self.target_room}'. Disconnecting (not an error)."
            )
            if hasattr(self.manager, '_rejected_room_map'):
                self.manager._rejected_room_map[self.epr] = device_room
            self._location_filtered = True
            return True  # → caller returns from _worker_logic

        self.logger.info(f"Location filter: room '{device_room}' matches. Accepting.")
        return False

    # =========================================================================
    # Phase 2c + 3: Subscribe to push notifications
    # =========================================================================

    def _phase_subscribe(self) -> None:
        """Bind ObservableProperty callbacks for metrics, alerts, and session changes."""
        # SDPi-A R1030/R1031: session change detection
        observableproperties.bind(
            self.mdib,
            sequence_or_instance_id_changed_event=self._on_sequence_id_changed,
        )
        observableproperties.bind(self.mdib, metrics_by_handle=self.on_metric_update)
        observableproperties.bind(self.mdib, alert_by_handle=self.on_alert_update)

    # =========================================================================
    # Phase 3b: Ensemble context binding (SmartAlertAggregator)
    # =========================================================================

    async def _phase_aggregate_on_connect(self) -> None:
        """
        Let the SmartAlertAggregator decide ensemble membership and call
        apply_ensemble_context() + apply_fhir_contexts().

        Uses asyncio.to_thread() because evaluate_and_bind_device() is
        synchronous (FHIR HTTP + SOAP calls).
        """
        aggregator = getattr(self.manager, 'aggregator', None)
        if aggregator is not None:
            self.logger.debug('[Aggregator] Calling evaluate_and_bind_device...')
            try:
                await asyncio.to_thread(aggregator.evaluate_and_bind_device, self)
            except Exception as e:
                self.logger.error(
                    f'[Aggregator] evaluate_and_bind_device raised an unexpected '
                    f'exception — ensemble binding skipped: {e}',
                    exc_info=True,
                )

    # =========================================================================
    # Phase 3c: Snapshot initial alert states
    # =========================================================================

    def _phase_snapshot_initial_alerts(self) -> None:
        """
        Feed initial alert states from MDIB into on_alert_update() as a
        synthetic "initial snapshot" report.

        Without this, alarms that were already ON at init_mdib() time would be
        silently missed: on_alert_update fires only on EpisodicAlertReport
        (state change), not on the initial MDIB load.
        """
        try:
            initial_alerts: dict = {}
            with self.data_lock:
                if self.mdib:
                    alert_nodetypes = (
                        pm.AlertConditionState,
                        pm.AlertSignalState,
                        pm.AlertSystemState,
                    )
                    for s in self.mdib.states.objects:
                        if s.NODETYPE in alert_nodetypes:
                            initial_alerts[s.DescriptorHandle] = s
            if initial_alerts:
                self.logger.info(
                    f'[InitSnapshot] Replaying {len(initial_alerts)} '
                    f'initial alert state(s) from MDIB...'
                )
                self.on_alert_update(initial_alerts)
            else:
                self.logger.debug('[InitSnapshot] No alert states found in MDIB after init_mdib().')
        except Exception as e:
            self.logger.warning(f'[InitSnapshot] Failed to read initial alert states: {e}')

    # =========================================================================
    # Phase 4: Create QtDeviceHandler and move it to the UI thread
    # =========================================================================

    def _phase_setup_qt(self) -> None:
        """
        Create QtDeviceHandler in the worker thread, then immediately move it
        to the main Qt thread so that QML property bindings work correctly.
        """
        from app.qtDeviceHandler import QtDeviceHandler  # local import avoids circular dep
        self.qtDeviceHandler = QtDeviceHandler(self)

        main_thread = QCoreApplication.instance().thread()
        if main_thread:
            self.qtDeviceHandler.moveToThread(main_thread)
        else:
            self.logger.warning('Could not find Main Thread!')

        self._ui_connected = True
        self.manager.deviceConnected.emit(self.qtDeviceHandler)

    # =========================================================================
    # Phase 5: Main monitoring loop  (T_fallback / IHE SDPi)
    # =========================================================================

    async def _monitoring_loop(self) -> None:
        """
        Keep the connection alive and trigger UI refreshes.

        T_fallback implementation (IHE SDPi):
          Active ping (GetContextStates) every SLEEP_INTERVAL seconds.
          If MAX_MISSED consecutive pings fail → declare the connection lost.
        """
        SLEEP_INTERVAL = 5.0
        T_FALLBACK     = 15.0
        MAX_MISSED     = int(T_FALLBACK / SLEEP_INTERVAL)
        missed         = 0

        while self.running:
            if not self.consumer.is_connected:
                self.logger.warning('Connection lost reported by SDC stack.')
                self.error_occurred = True
                break

            if self.qtDeviceHandler:
                self.qtDeviceHandler.scheduleUpdate()

            missed = await self._ping(missed, MAX_MISSED, SLEEP_INTERVAL, T_FALLBACK)
            if missed < 0:
                break  # T_fallback exceeded — exit requested by _ping

            await self._process_ack_timeouts()
            await asyncio.sleep(SLEEP_INTERVAL)

    async def _ping(self, missed: int, max_missed: int, interval: float, t_fallback: float) -> int:
        """
        Perform one active GetContextStates ping.

        Returns the updated missed-ping counter.
        Returns -1 if T_fallback was exceeded (caller should break the loop).
        """
        try:
            if self.consumer and self.consumer.is_connected:
                if self.consumer.context_service_client:
                    await asyncio.to_thread(
                        self.consumer.context_service_client.get_context_states
                    )
                    if not getattr(self, '_ctx_ping_ok_logged', False):
                        self.logger.info(
                            '[DIAG] GetContextStates ping: SUCCESS — '
                            'device allows context queries without authorization.'
                        )
                        self._ctx_ping_ok_logged = True
                else:
                    if not getattr(self, '_no_ctx_svc_logged', False):
                        self.logger.warning(
                            '[DIAG] No context_service_client — '
                            'device did not advertise ContextService in metadata.'
                        )
                        self._no_ctx_svc_logged = True
            return 0  # ping succeeded

        except HTTPReturnCodeError as e:
            # HTTP 4xx: TCP is alive; provider rejected the request (e.g. HTTP 400).
            # Log once, do not increment missed counter.
            if not getattr(self, '_auth_warn_logged', False):
                self.logger.warning(
                    f'[DIAG] Ping: provider returned HTTP {e.status} ({e.reason}) — '
                    f'connection alive but GetContextStates is blocked. '
                    f'Suppressing further warnings.'
                )
                self._auth_warn_logged = True
            return 0

        except Exception as e:
            missed += 1
            self.logger.warning(f'Ping failed ({missed}/{max_missed}): {e}')
            if missed * interval >= t_fallback:
                self.logger.error('T_fallback exceeded. Disconnecting.')
                self.error_occurred = True
                return -1  # signal loop to break
            return missed

    async def _process_ack_timeouts(self) -> None:
        """Re-raise alarm signals that have been in Ack state beyond ACK_TIMEOUT_SEC."""
        now = time.monotonic()
        for sig_handle in self.alarm_manager.get_expired_handles(now):
            self.logger.warning(
                f'[Ack-timeout] {sig_handle}: Ack held for '
                f'{self.alarm_manager.ACK_TIMEOUT_SEC:.0f}s — re-raising alarm (Presence=On).'
            )
            op_handle = self.alarm_manager.find_operation_handle(sig_handle)
            if op_handle:
                try:
                    await asyncio.to_thread(
                        self.alarm_manager.reactivate_alarm, op_handle, sig_handle
                    )
                except Exception as e:
                    self.logger.error(f'[Ack-timeout] Re-raise failed: {e}')
            self.alarm_manager.clear_ack(sig_handle)

    # =========================================================================
    # Callback: metric updates (EpisodicMetricReport)
    # =========================================================================

    def on_metric_update(self, metrics_by_handle: dict) -> None:
        """
        Called by sdc11073 from its notification thread on EpisodicMetricReport.

        Phase 1 — update physiological state graph (no data_lock needed;
          state objects are already delivered as arguments).
        Phase 2 — rate-limited UI refresh (max 1 Hz).
        """
        aggregator = getattr(self.manager, 'aggregator', None)
        if aggregator is not None and self.ensemble_uuid:
            for state in metrics_by_handle.values():
                try:
                    mv = getattr(state, 'MetricValue', None)
                    if mv is None:
                        continue
                    value = getattr(mv, 'Value', None)
                    if value is None:
                        continue
                    concept = self._handle_to_concept.get(
                        getattr(state, 'DescriptorHandle', '')
                    )
                    if concept:
                        aggregator.update_metric_state(self.ensemble_uuid, concept, float(value))
                        # self.logger.debug(
                        #     f'[PhysGraph] {concept}={float(value):.4g} '
                        #     f'handle={state.DescriptorHandle!r} '
                        #     f'ensemble={self.ensemble_uuid[:8]}'
                        # )
                except Exception:
                    pass

        if not self.qtDeviceHandler:
            return
        now = time.monotonic()
        if now - self._last_ui_update_ts >= 1.0:
            self._last_ui_update_ts = now
            self.qtDeviceHandler.scheduleUpdate()

    # =========================================================================
    # Callback: alert updates (EpisodicAlertReport)
    # =========================================================================

    def on_alert_update(self, alert_by_handle: dict) -> None:
        """
        Called by sdc11073 from its notification thread on EpisodicAlertReport.

        Handles:
          AlertConditionState — Presence is Python bool (True / False)
          AlertSignalState    — Presence is AlertSignalPresence enum (On/Off/Ack/Latch)

        Pipeline per alert:
          1. Classify alert type (Condition / Signal / Alert)
          2. Look up concept code and monitored metric code from MDIB
          3. Skip AlertSystemState (no meaningful Presence)
          4. DSP filter: suppress physiologically impossible metric jumps
          5. Priority check: mark if clinically critical for patient's focus
          6. Log the transition
          7. Update ack-timeout tracking (signals only)
        """
        now = time.monotonic()

        for handle, state in alert_by_handle.items():
            raw_presence = getattr(state, 'Presence', None)
            type_label = self._alert_type_label(state)

            # Skip AlertSystemState — no meaningful Presence field
            if type_label == 'Alert' and raw_presence is None:
                continue

            alert_concept, metric_concept, concept_hint, biceps_priority, condition_signaled = \
                self._lookup_alert_concepts(handle, state)
            ens = f' ensemble={self.ensemble_uuid[:8]}' if self.ensemble_uuid else ''
            dev = self.epr[-12:]
            is_active = self._is_alert_active(raw_presence)
            aggregator = getattr(self.manager, 'aggregator', None)

            # ── Signal suppression propagation ────────────────────────────────
            # If this is a Signal whose parent Condition was suppressed by the
            # pipeline, suppress the signal too (prevents log + UI indicator).
            if type_label == 'Signal' and condition_signaled in self._pipeline_suppressed:
                if is_active:
                    continue  # parent condition suppressed → hide the signal as well

            # ── Condition suppression tracking ────────────────────────────────
            # When a Condition clears, always remove from _pipeline_suppressed so
            # the next ON event gets a fresh evaluation.
            # Also explicitly remove from the aggregator's TTL cache (_active_alarms)
            # so the dead alarm is not included in Bayesian fusion for the next
            # alarm that fires in the same ensemble.
            if type_label == 'Condition' and not is_active:
                self._pipeline_suppressed.discard(handle)
                if aggregator is not None and self.ensemble_uuid:
                    try:
                        aggregator.clear_alarm(self.ensemble_uuid, handle)
                    except Exception as _e:
                        self.logger.debug(f'[DSP FILTER] clear_alarm failed: {_e}')

            # DSP filter (IHE-PCD ACM Alarm Coordinator — Stage 1 + Stage 2)
            if is_active and type_label == 'Condition' and aggregator is not None and self.ensemble_uuid:
                try:
                    # Retrieve the pre-fetched calibration for this concept.
                    # Tuple (roc_limit, reliability_profile) was populated at MDIB init time
                    # by _prefetch_device_calibration() via DeviceProfileRepository.
                    _roc_limit, _rel_profile = self._device_calibration.get(
                        metric_concept, (None, None)
                    )
                    if not aggregator.check_alert_validity(
                        self.ensemble_uuid, handle, metric_concept,
                        biceps_priority=biceps_priority,
                        manufacturer=self.manufacturer,
                        model=self.model,
                        device_epr=self.epr,
                        roc_limit=_roc_limit,
                        reliability_profile=_rel_profile,
                    ):
                        self._pipeline_suppressed.add(handle)   # suppress condition + its signal
                        self.logger.info(
                            f'[DSP FILTER] Pipeline suppressed alarm: '
                            f'handle={handle!r} metric={metric_concept!r} '
                            f'concept={alert_concept!r} priority={biceps_priority!r}'
                        )
                        continue
                    else:
                        # Alarm passed the pipeline — ensure it is not in suppressed set
                        self._pipeline_suppressed.discard(handle)
                except Exception as e:
                    self.logger.warning(f'[DSP FILTER] check_alert_validity raised: {e}')

            # Priority check
            is_priority = False
            if is_active and aggregator is not None and self.ensemble_uuid and alert_concept:
                try:
                    is_priority = aggregator.check_alert_priority(self.ensemble_uuid, alert_concept)
                except Exception as e:
                    self.logger.warning(f'[PRIORITY CHECK] check_alert_priority raised: {e}')

            prefix = '[PRIORITY CLINICAL FOCUS] ' if is_priority else ''

            if isinstance(raw_presence, bool):
                self._log_condition_transition(handle, raw_presence, type_label, concept_hint, ens, dev, prefix)
            else:
                self._log_signal_transition(handle, raw_presence, type_label, concept_hint, ens, dev, prefix, now)

        if not self.qtDeviceHandler:
            return
        if now - self._last_ui_update_ts >= 0.2:
            self._last_ui_update_ts = now
            self.qtDeviceHandler.scheduleUpdate()

    # -- Alert helpers ---------------------------------------------------------

    @staticmethod
    def _alert_type_label(state: Any) -> str:
        node_type = getattr(state, 'NODETYPE', None)
        name = getattr(node_type, 'localname', '') if node_type else ''
        if 'Condition' in name:
            return 'Condition'
        if 'Signal' in name:
            return 'Signal'
        return 'Alert'

    def _lookup_alert_concepts(self, handle: str, state: Any) -> tuple[str | None, str, str, str, str | None]:
        """
        Non-blocking MDIB lookup for alert and metric concept codes.

        Returns (alert_concept, metric_concept, concept_hint, biceps_priority, condition_signaled).
        biceps_priority defaults to 'Hi' (fail-open) when the descriptor is unavailable.
        condition_signaled is the ConditionSignaled handle for AlertSignal descriptors
        (None for AlertCondition descriptors and other types).
        Uses non-blocking lock acquire — skips gracefully if MDIB is busy.
        """
        desc_handle = getattr(state, 'DescriptorHandle', handle)
        alert_concept: str | None = None
        metric_concept: str = ''
        concept_hint = ''
        biceps_priority: str = 'Hi'   # default fail-open: 'Hi' always reaches threshold
        condition_signaled: str | None = None

        if self.data_lock.acquire(blocking=False):
            try:
                if self.mdib:
                    desc = self.mdib.descriptions.handle.get_one(desc_handle, allow_none=True)
                    if desc:
                        code = getattr(getattr(desc, 'Type', None), 'Code', None)
                        if code:
                            alert_concept = str(code)
                            concept_hint  = f' code={code!r}'
                        for src_handle in (getattr(desc, 'Source', None) or []):
                            mc = self._handle_to_concept.get(str(src_handle))
                            if mc:
                                metric_concept = mc
                                break
                        # BICEPS AlertCondition.Priority ('Hi'/'Me'/'Lo'/'None')
                        prio = getattr(desc, 'Priority', None)
                        if prio is not None:
                            prio_str = str(prio)
                            if prio_str in ('Hi', 'Me', 'Lo', 'None'):
                                biceps_priority = prio_str
                        # AlertSignal.ConditionSignaled — back-reference to the condition
                        cs = getattr(desc, 'ConditionSignaled', None)
                        if cs:
                            condition_signaled = str(cs)
            except Exception:
                pass
            finally:
                self.data_lock.release()

        return alert_concept, metric_concept, concept_hint, biceps_priority, condition_signaled

    @staticmethod
    def _is_alert_active(raw_presence: Any) -> bool:
        return (
            raw_presence is True
            or str(raw_presence) == str(pm_types.AlertSignalPresence.ON)
            or str(raw_presence) == str(pm_types.AlertSignalPresence.ACK)
        )

    def _log_condition_transition(
        self, handle: str, presence: bool, type_label: str,
        concept_hint: str, ens: str, dev: str, prefix: str,
    ) -> None:
        if presence:
            self.logger.warning(
                f'{prefix}[ALARM] 🔴 ON  | {type_label} handle={handle!r}'
                f'{concept_hint}{ens} | device={dev}'
            )
        else:
            self.logger.warning(
                f'[ALARM] 🟢 OFF | {type_label} handle={handle!r}'
                f'{concept_hint}{ens} | device={dev}'
            )

    def _log_signal_transition(
        self, handle: str, raw_presence: Any, type_label: str,
        concept_hint: str, ens: str, dev: str, prefix: str, now: float,
    ) -> None:
        ack_str = str(pm_types.AlertSignalPresence.ACK)
        off_str = str(pm_types.AlertSignalPresence.OFF)
        on_str  = str(pm_types.AlertSignalPresence.ON)
        presence = str(raw_presence) if raw_presence is not None else ''

        if presence == on_str:
            self.logger.warning(
                f'{prefix}[ALARM] 🔴 ON  | {type_label} handle={handle!r}'
                f'{concept_hint}{ens} | device={dev}'
            )
        elif presence == off_str:
            self.logger.warning(
                f'[ALARM] 🟢 OFF | {type_label} handle={handle!r}'
                f'{concept_hint}{ens} | device={dev}'
            )
        elif presence == ack_str:
            self.logger.warning(
                f'{prefix}[ALARM] 🔕 ACK | {type_label} handle={handle!r}'
                f'{concept_hint}{ens} | device={dev}'
            )
        else:
            # Latch or genuinely unknown non-empty state — DEBUG to avoid noise
            self.logger.debug(
                f'[ALARM] ❓ {presence!r} | {type_label} handle={handle!r}'
                f'{concept_hint}{ens} | device={dev}'
            )

        # Ack-timeout tracking (signals only)
        if presence == ack_str:
            if not self.alarm_manager.is_tracked(handle):
                self.alarm_manager.set_ack(handle, now)
                self.logger.warning(
                    f'[Ack-timeout] {handle}: countdown started — '
                    f'will re-raise in {self.alarm_manager.ACK_TIMEOUT_SEC:.0f}s if not cleared.'
                )
        elif presence in (off_str, on_str):
            if self.alarm_manager.is_tracked(handle):
                held = now - self.alarm_manager._ack_timestamps[handle]
                self.alarm_manager.clear_ack(handle)
                self.logger.warning(
                    f'[Ack-timeout] {handle}: timer cleared '
                    f'(Presence={presence}, held Ack for {held:.1f}s).'
                )

    # =========================================================================
    # Utility: read device room from MDIB LocationContext
    # =========================================================================

    def _get_device_room(self) -> str:
        """
        Returns the Room from LocationContextState, or '' if absent.
        Thread-safe: acquires data_lock internally.
        MUST be called without holding data_lock (Lock is not reentrant).
        """
        try:
            with self.data_lock:
                if not self.mdib:
                    return ''
                loc_states = [s for s in self.mdib.context_states.objects
                              if s.NODETYPE == pm.LocationContextState]
                if loc_states and loc_states[0].LocationDetail:
                    return loc_states[0].LocationDetail.Room or ''
        except Exception as e:
            self.logger.error(f'_get_device_room error: {e}')
        return ''

    # =========================================================================
    # SDPi-A R1030/R1031: Session change detection
    # =========================================================================

    def _on_sequence_id_changed(self, sequence_or_instance_id_changed_event: bool) -> None:
        """
        Fires when the device changes its SequenceId or InstanceId (reboot,
        software reset, NIC change).  Forces a reconnect to resync the MDIB.
        """
        if not sequence_or_instance_id_changed_event:
            return
        self.logger.warning(
            'SDPi-A: SequenceId/InstanceId changed! '
            'Device may have restarted — forcing reconnect to resync MDIB.'
        )
        self.error_occurred = True
        self.running = False

    # =========================================================================
    # DEV-49: Graceful monitoring session termination
    # =========================================================================

    async def _graceful_shutdown(self) -> None:
        """
        Send WS-Eventing Unsubscribe on all active subscriptions (DEV-49).

        Without this the bedside monitor treats the disconnect as a crash
        and may activate a fallback alarm (60 dBA).  A graceful Unsubscribe
        tells the device the observer left intentionally.
        """
        self._intentional_shutdown = True
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self.consumer.stop_all),
                timeout=5.0,
            )
            self.logger.info('DEV-49: Unsubscribe completed — device notified.')
        except asyncio.TimeoutError:
            self.logger.warning('DEV-49: Unsubscribe timed out (5 s). Forcing close.')
        except Exception as e:
            self.logger.error(f'DEV-49: Error during graceful shutdown: {e}')

    # =========================================================================
    # Stop signal
    # =========================================================================

    def stop(self) -> None:
        """Signal the monitoring loop to exit on its next iteration."""
        self.running = False

