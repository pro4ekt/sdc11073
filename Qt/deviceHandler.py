"""
deviceHandler.py -- Worker thread for a single SDC device.

ARCHITECTURE:
  Each discovered device gets its own DeviceHandler object, which runs as a
  dedicated system thread (threading.Thread). An isolated asyncio event loop
  is created inside that thread -- this is the key design decision: network
  timeouts or a hanging device do not affect the others.

  The thread lives exactly as long as the connection to the device lives.
  On disconnect (graceful or error) the thread exits and notifies the Manager
  via remove_device(), which decides whether to flush the WSDiscovery cache.

UI INTERACTION:
  A QtDeviceHandler (QObject) bridges this worker to QML. It is created in the
  worker thread but immediately moved to the main thread via moveToThread() so
  that Qt signals and properties work correctly and thread-safely.
"""

import threading
import asyncio
import ssl
import time
import logging
import logging.handlers
import pathlib
from decimal import Decimal

# =============================================================================
# LOGGING SETUP
# =============================================================================
# Log directory: Qt/logs/  (created automatically if absent)
_LOG_DIR = pathlib.Path(__file__).parent / 'logs'
_LOG_DIR.mkdir(exist_ok=True)

class _SuppressGetContextStates400(logging.Filter):
    """
    Suppress the repetitive 'GetContextStates HTTP 400' ERROR spam from sdc11073's
    soap client / mdib logger. sdcX returns HTTP 400 for GetContextStates when the
    consumer is not authorized (no mTLS). We already handle this in the ping loop
    and log a one-time warning -- the sdc11073 internal ERROR is redundant and noisy.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno != logging.ERROR:
            return True
        msg = record.getMessage()
        # Drop "GetContextStates: POST ... HTTP response=400" from sdc.client.soap
        if 'GetContextStates' in msg and '400' in msg:
            return False
        # Drop the traceback from sdc.client.mdib that follows the soap error
        # (it contains HTTPReturnCodeError and _get_context_states in the traceback)
        if 'HTTPReturnCodeError' in msg or '_get_context_states' in msg:
            return False
        return True


def _setup_module_logger() -> logging.Logger:
    """
    Configure the root logger for the deviceHandler module.

    Levels:
      - Console (StreamHandler): INFO  -- short format HH:MM:SS [LEVEL] msg
      - File (RotatingFileHandler): DEBUG -- full format with logger name
        File: Qt/logs/sdc_consumer.log
        Rotation: 5 MB x 5 backup copies -> sdc_consumer.log.1 ... .5

    NOTE: sdc11073's basic_logging_setup() may reconfigure the 'sdc' logger
    hierarchy after this function runs.  main.py calls
    logging.getLogger('sdc.consumer').setLevel(logging.DEBUG) afterwards to
    guarantee our level is preserved.
    """
    logger = logging.getLogger('sdc.consumer')

    # Always enforce DEBUG on this logger so that basic_logging_setup()
    # called later in main.py cannot raise the effective level to WARNING.
    logger.setLevel(logging.DEBUG)

    # Avoid adding handlers more than once (if module is imported multiple times)
    if logger.handlers:
        return logger


    # -- Console (INFO+) -------------------------------------------------------
    # Short format: time + level + message. INFO and above only, to avoid
    # flooding the output with DEBUG details (cooldown spam, cache, etc.).
    _con_handler = logging.StreamHandler()
    _con_handler.setLevel(logging.INFO)
    _con_handler.setFormatter(logging.Formatter(
        fmt='%(asctime)s [%(levelname)-5s] %(message)s',
        datefmt='%H:%M:%S',
    ))
    logger.addHandler(_con_handler)

    # -- File (DEBUG+, rotation) -----------------------------------------------
    # Full format with logger name -- for detailed post-session analysis.
    _file_handler = logging.handlers.RotatingFileHandler(
        filename=str(_LOG_DIR / 'sdc_consumer.log'),
        maxBytes=5 * 1024 * 1024,   # 5 MB per file
        backupCount=5,
        encoding='utf-8',
    )
    _file_handler.setLevel(logging.DEBUG)
    _file_handler.setFormatter(logging.Formatter(
        fmt='%(asctime)s [%(levelname)-8s] %(name)s -- %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    ))
    logger.addHandler(_file_handler)

    # Do not propagate to the root logger (prevents duplicate log entries)
    logger.propagate = False

    return logger

# Module-level logger -- used for messages outside of DeviceHandler
_module_log = _setup_module_logger()

# Suppress repetitive GetContextStates HTTP 400 ERROR spam from sdc11073 internals.
# sdcX blocks context queries from unauthorized participants -- this is expected when
# running without mTLS. We handle it in the ping loop already. Apply filter once.
logging.getLogger('sdc.client.soap').addFilter(_SuppressGetContextStates400())
logging.getLogger('sdc.client.mdib').addFilter(_SuppressGetContextStates400())

# Qt wrapper -- created per device and lives in the UI thread
from qtDeviceHandler import QtDeviceHandler

# Core sdc11073 classes for the consumer side (Consumer = SDC device client)
from sdc11073.consumer import SdcConsumer
from sdc11073.mdib import ConsumerMdib
from sdc11073.pysoap.soapclient import HTTPReturnCodeError

# periodic_actions -- actions that must NOT be subscribed via WS-Eventing;
# they are delivered periodically and handled differently.
from sdc11073.xml_types.actions import periodic_actions

# pm -- XML QNames for BICEPS/SDC types (descriptors, states, etc.)
from sdc11073.xml_types import pm_qnames as pm

# pm_types -- Python classes for BICEPS value types (enums, data structures)
from sdc11073.xml_types import pm_types

# Specific types used for building measurement values
from sdc11073.xml_types.pm_types import Measurement, CodedValue

# observableproperties -- subscription mechanism for ConsumerMdib changes;
# allows registering callbacks that fire when metrics or alerts are updated.
from sdc11073 import observableproperties

# Needed to obtain a reference to the main Qt UI thread for moveToThread().
# QCoreApplication is the base class of QGuiApplication.
from PySide6.QtCore import QCoreApplication

# RelatedMeasurement has a bug in from_node(): it calls cls(...) which requires
# the 'value' argument in __init__, but during XML deserialization that value is
# not yet known. Work around it with the monkey-patch below.
from sdc11073.xml_types.pm_types import Measurement, RelatedMeasurement


# =============================================================================
# MONKEY-PATCH: Fix RelatedMeasurement deserialization bug
# =============================================================================
# Problem:  the default from_node() tries cls(...) which needs 'value' in
#           __init__ -- but during XML parsing it is not available yet.
# Solution: replace from_node() with a version that first creates the object
#           with a placeholder (None, None), then populates it via
#           update_from_node(). Classic monkey-patch -- modifying a third-party
#           library's behaviour at runtime.
@classmethod
def _related_measurement_from_node(cls, node):
    # Create an "empty" object bypassing the __init__ requirement
    obj = cls(Measurement(None, None))
    # Populate it from the real XML node data
    obj.update_from_node(node)
    return obj

# Replace the class method with our fixed version
RelatedMeasurement.from_node = _related_measurement_from_node


# =============================================================================
# DeviceHandler -- worker thread for one SDC device
# =============================================================================
class DeviceHandler(threading.Thread):
    """
    Worker class (The "Worker").

    Maintains the connection to ONE specific SDC device (Provider).
    Runs in its own system thread with an independent asyncio event loop.

    Lifecycle:
      1. Manager creates DeviceHandler and calls start().
      2. Thread connects to the device, initialises the MDIB, starts monitoring.
      3. On disconnect (or error) the thread exits and calls
         manager.remove_device() to remove itself from the registry.
    """

    def __init__(self, wsd_service, manager,
                 target_room: str | None = None, tls_mode: str = 'auto'):
        """
        Parameters:
          wsd_service -- WSDiscovery service object containing EPR and address.
          manager     -- reference to SdcMyConsumer (Manager).
          target_room -- LocationContext.Room filter (string or None).
          tls_mode    -- TLS strategy: 'auto' | 'force_tls' | 'no_tls'.
        """
        threading.Thread.__init__(self, daemon=True)

        # TLS strategy: 'auto' | 'force_tls' | 'no_tls'
        # Set from CLI (--tls / --no_tls) via Manager.
        self.tls_mode: str = tls_mode

        # LocationContext.Room filter.
        # Checked in _worker_logic() immediately after init_mdib().
        # If not None and device room does not match -> worker exits without error
        # (WSDiscovery cache is NOT cleared, deviceDisconnected is NOT emitted).
        self.target_room: str | None = target_room

        # WSD service -- needed to create the SdcConsumer connection
        self.wsd_service = wsd_service

        # EPR (Endpoint Reference) -- unique UUID of the device on the network.
        # Explicitly cast to str to guarantee correct dict key comparisons.
        self.epr = str(wsd_service.epr)

        # Reference to the Manager
        self.manager = manager

        # Flag controlling the main monitoring loop
        self.running = True

        # SDC Consumer -- object that communicates with the device over HTTP/SOAP/WS.
        # Initialised in _worker_logic(); None here.
        self.consumer = None

        # ConsumerMdib -- local mirror of the device's MDIB.
        # Automatically updated when push notifications arrive from the device.
        self.mdib = None

        # Timestamp of the last scheduleUpdate() call (monotonic, seconds).
        # Used in on_metric_update / on_alert_update for rate-limiting:
        # at most 1 UI refresh per second during high-frequency waveform data.
        self._last_ui_update_ts: float = 0.0

        # Qt wrapper through which QML reads this device's data
        self.qtDeviceHandler = None

        # Flag: True means deviceConnected.emit() has already been sent.
        # Used in Manager.remove_device() to decide whether to emit deviceDisconnected.
        # Devices filtered by target_room before emit() stay False.
        self._ui_connected: bool = False

        # Flag: did the thread exit due to an error (vs a clean stop)?
        # If True -- Manager will flush the WSDiscovery cache for this EPR.
        self.error_occurred = False

        # Lock protecting access to self.mdib.
        # RULE: any thread reading or writing MDIB MUST hold this lock.
        # Exception: network calls (set_context_state etc.) are made WITHOUT
        # the lock to avoid blocking the UI thread during a network round-trip.
        self.data_lock = threading.Lock()

        # Placeholder for a future OPC UA server (unused at the moment)
        self.opcua_server = None

        # -- Worker logger ----------------------------------------------------
        # Logger name includes the last 12 chars of the UUID so log lines
        # immediately identify which device they came from.
        _short_epr = self.epr[-12:] if len(self.epr) > 12 else self.epr
        self.logger = logging.getLogger(f'sdc.consumer.worker.{_short_epr}')

        # Flag: True means the worker exited due to LocationContext filtering.
        # Set in _worker_logic() when target_room does not match.
        # Used in run() to call manager.remove_device(location_filtered=True),
        # which adds the EPR to a session ban-list so _discovery_loop never
        # creates a new DeviceHandler for this device until process restart.
        self._location_filtered: bool = False

        # Flag: True means a graceful shutdown is in progress (DEV-49).
        # Used in _graceful_shutdown() to distinguish planned stop from crash.
        self._intentional_shutdown = False

        # UUID of the ensemble this device belongs to.
        # Assigned by SmartAlertAggregator.evaluate_and_bind_device() via
        # apply_ensemble_context() after a successful EnsembleContextState send.
        # None -- device has not been bound to any ensemble yet.
        self.ensemble_uuid: str | None = None

        # Ack-timeout: maps AlertSignal descriptor handle -> monotonic time when
        # Presence transitioned to Ack.  If the signal is still Ack after
        # ACK_TIMEOUT_SEC seconds (i.e. the condition did not clear), the consumer
        # automatically re-raises the alarm by sending SetAlertState(Presence=On).
        # This prevents a silenced alarm from being forgotten indefinitely.
        self._ack_timestamps: dict[str, float] = {}
        self.ACK_TIMEOUT_SEC: float = 30.0

        # Semantic mapping: descriptor Handle -> BICEPS concept code.
        # Populated once in _worker_logic() after init_mdib(), under data_lock.
        # Used in on_metric_update() for cross-device physiological graph updates
        # without acquiring data_lock (states are already delivered as arguments).
        self._handle_to_concept: dict[str, str] = {}


    # =========================================================================
    # Thread entry point (called by threading.Thread on start())
    # =========================================================================
    def run(self):
        """
        Invoked automatically when DeviceHandler.start() is called.
        Creates an isolated asyncio event loop and runs the main logic in it.

        IMPORTANT: each thread creates its own event loop -- this provides
        isolation. A network timeout on one device does not freeze the others.
        """
        # Create a new event loop dedicated to this thread
        loop = asyncio.new_event_loop()
        # Register it as the "current" loop for this thread
        asyncio.set_event_loop(loop)

        try:
            # Run the main async logic and block the thread until it finishes
            loop.run_until_complete(self._worker_logic())
        finally:
            # Guaranteed event loop cleanup regardless of outcome
            try:
                loop.close()
            except Exception:
                pass

            # Self-removal from the Manager's registry.
            # error_occurred=True  -> Manager flushes WSDiscovery cache.
            # location_filtered=True -> Manager adds EPR to ban-list,
            #   preventing a new DeviceHandler from being created for this device.
            self.manager.remove_device(
                self.epr,
                self.error_occurred,
                location_filtered=self._location_filtered,
            )
            self.logger.info("Thread Exiting (Dead).")

    # =========================================================================
    # Main async connection and monitoring logic
    # =========================================================================
    async def _worker_logic(self):
        """
        Runs inside this thread's isolated asyncio event loop.

        Phases:
          1. Connect to the SDC device (SdcConsumer)
          2. Initialise the local MDIB copy
          3. Subscribe to metric and alert updates
          4. Create the Qt object and move it to the main thread
          5. Main monitoring loop with T_fallback mechanism (IHE SDPi)
        """
        self.logger.info("Connecting...")
        try:
            # ------------------------------------------------------------------
            # STEP 1: Connect to the SDC device
            # ------------------------------------------------------------------
            # from_wsd_service() creates SdcConsumer from WSDiscovery data --
            # no manual IP/port specification required.
            # ssl_context_container=None = unencrypted connection.
            x_addrs = getattr(self.wsd_service, 'x_addrs', 'unknown')
            self.logger.debug(f"Transport addresses (x_addrs): {x_addrs}")

            # ------------------------------------------------------------------
            # _build_ssl_container() -- builds SSLContextContainer with a client
            # certificate from pat/certs/ (IHE test PKI).
            # Used for explicit https:// AND for TLS-fallback on http://.
            # ------------------------------------------------------------------
            def _build_ssl_container():
                _qt_dir = pathlib.Path(__file__).parent        # Master/Qt/
                _base   = _qt_dir.parent                       # Master/
                _cert_candidates = [
                    # (1) certs_out/ inside Qt/ -- our generated certificates
                    (_qt_dir / 'certs_out' / 'consumer.pem',
                     _qt_dir / 'certs_out' / 'consumer.key',
                     _qt_dir / 'certs_out' / 'ca1.pem',
                     [None]),
                    (_qt_dir / 'certs_out' / 'consumer.pem',
                     _qt_dir / 'certs_out' / 'consumer.key',
                     _qt_dir / 'certs_out' / 'ca.pem',
                     [None]),
                    # (2) pat/certs/ -- our certificates copied there
                    (_base / 'pat' / 'certs' / 'consumer.pem',
                     _base / 'pat' / 'certs' / 'consumer.key',
                     _base / 'pat' / 'certs' / 'ca1.pem',
                     [None]),
                    # (3) IHE PAT test PKI (pat/certs/) -- fallback if ours are absent
                    (_base / 'pat' / 'certs' / 'user_certificate_root_signed.pem',
                     _base / 'pat' / 'certs' / 'user_private_key_encrypted.pem',
                     _base / 'pat' / 'certs' / 'root_certificate.pem',
                     ['dummypassword', 'password', 'sdcX', '12345']),
                    # (4) sdc11073 unit-test PKI (tests/certificates/)
                    (_base / 'tests' / 'certificates' / 'test_certificate.pem',
                     _base / 'tests' / 'certificates' / 'test_private_key.pem',
                     None,
                     ['password', 'dummypassword']),
                ]

                # -- CLIENT context: consumer -> provider (standard SSL client) --
                _client_ctx = ssl.create_default_context()
                _client_ctx.check_hostname = False
                _client_ctx.verify_mode = ssl.CERT_NONE

                # -- SERVER context: provider -> consumer (WS-Eventing notifications) --
                # IMPORTANT: PROTOCOL_TLS_SERVER is required -- create_default_context()
                # returns a CLIENT context which cannot accept incoming TLS connections.
                _server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                _server_ctx.check_hostname = False
                _server_ctx.verify_mode = ssl.CERT_NONE

                _loaded = False
                for _cert_file, _key_file, _ca_file, _passwords in _cert_candidates:
                    if not (_cert_file.exists() and _key_file.exists()):
                        continue
                    for _pwd in _passwords:
                        try:
                            _kw = dict(certfile=str(_cert_file),
                                       keyfile=str(_key_file),
                                       password=_pwd or None)
                            _client_ctx.load_cert_chain(**_kw)
                            _server_ctx.load_cert_chain(**_kw)
                            if _ca_file and _ca_file.exists():
                                _client_ctx.load_verify_locations(cafile=str(_ca_file))
                                _server_ctx.load_verify_locations(cafile=str(_ca_file))
                            self.logger.info(
                                f"mTLS: cert loaded from {_cert_file.parent} "
                                f"(password={'<empty>' if not _pwd else repr(_pwd)})"
                            )
                            _loaded = True
                            break
                        except Exception:
                            continue
                    if _loaded:
                        break

                if not _loaded:
                    self.logger.warning(
                        "Could not load any client cert. "
                        "Attempting TLS without client certificate (mTLS will fail if required)."
                    )

                try:
                    from sdc11073 import certloader
                    return certloader.SSLContextContainer(
                        client_context=_client_ctx,
                        server_context=_server_ctx,
                    )
                except Exception as ssl_err:
                    self.logger.warning(
                        f"Could not build SSLContextContainer ({ssl_err}). "
                        f"Using raw ssl_context as fallback."
                    )
                    return _ssl_ctx  # type: ignore[return-value]

            # ------------------------------------------------------------------
            # Connection strategy -- controlled by self.tls_mode:
            #   'no_tls'    -- plain HTTP forced (--no_tls), no TLS fallback.
            #   'force_tls' -- TLS forced (--tls), regardless of URL scheme.
            #   'auto'      -- auto-detect: https:// -> TLS immediately;
            #                  http:// -> plain with TLS-fallback on
            #                  ConnectionResetError (winerror 10054).
            # ------------------------------------------------------------------
            ssl_container = None

            if self.tls_mode == 'no_tls':
                self.logger.info('TLS disabled (--no_tls) -- plain HTTP, no fallback.')

            elif self.tls_mode == 'force_tls':
                self.logger.info('TLS forced (--tls) -- building SSL context...')
                ssl_container = _build_ssl_container()

            else:  # 'auto'
                if x_addrs and any(str(addr).startswith('https://') for addr in x_addrs):
                    self.logger.info('HTTPS detected -- building mTLS SSL context...')
                    ssl_container = _build_ssl_container()
                else:
                    self.logger.info('HTTP announced -- connecting without SSL (will retry if reset).')

            self.consumer = SdcConsumer.from_wsd_service(
                wsd_service=self.wsd_service,
                ssl_context_container=ssl_container,
            )

            try:
                self.consumer.start_all(not_subscribed_actions=periodic_actions)
            except Exception as _connect_err:
                _cause = _connect_err.__cause__ or _connect_err
                _is_reset = (
                    isinstance(_cause, ConnectionResetError)
                    or (isinstance(_cause, OSError) and getattr(_cause, 'winerror', None) == 10054)
                    or 'NotConnected' in type(_connect_err).__name__
                )
                # TLS fallback only in 'auto' mode and only on connection reset.
                # In 'no_tls' mode fallback is forbidden -- user explicitly disabled TLS.
                if _is_reset and ssl_container is None and self.tls_mode != 'no_tls':
                    self.logger.warning(
                        'HTTP connection reset -- provider likely requires TLS. '
                        'Retrying with mTLS SSL context...'
                    )
                    ssl_container = _build_ssl_container()
                    self.consumer = SdcConsumer.from_wsd_service(
                        wsd_service=self.wsd_service,
                        ssl_context_container=ssl_container,
                    )
                    self.consumer.start_all(not_subscribed_actions=periodic_actions)
                else:
                    raise

            # ------------------------------------------------------------------
            # STEP 2: Initialise MDIB (under lock!)
            # ------------------------------------------------------------------
            # init_mdib() performs a GetMdib network request and loads the full
            # device description (descriptors + initial states) into local memory.
            # The MDIB is then kept up to date via push notifications.
            # Hold the lock until MDIB is ready -- otherwise QtDeviceHandler may
            # read None.
            with self.data_lock:
                self.mdib = ConsumerMdib(self.consumer)
                self.mdib.init_mdib()

                # -- Diagnostics: what GetMdib returned, is ContextService present --
                _ctx_states = list(self.mdib.context_states.objects)
                _n_ctx = len(_ctx_states)
                if _n_ctx > 0:
                    self.logger.info(
                        f'[DIAG] GetMdib returned {_n_ctx} context state(s) -- '
                        f'GetContextStates call NOT needed.'
                    )
                    for _s in _ctx_states:
                        self.logger.debug(
                            f'[DIAG]   context_state: type={_s.NODETYPE.localname}, '
                            f'handle={_s.Handle}, descriptor={_s.DescriptorHandle}'
                        )
                else:
                    self.logger.warning(
                        f'[DIAG] GetMdib returned 0 context states -- '
                        f'device may require GetContextStates separately.'
                    )

                _has_ctx_svc = self.consumer.context_service_client is not None
                self.logger.info(
                    f'[DIAG] context_service_client available: {_has_ctx_svc}'
                )

                # ------------------------------------------------------------------
                # STEP 2a: Build handle -> semantic concept code mapping
                # ------------------------------------------------------------------
                # Iterates all NumericMetricDescriptors in the MDIB and extracts
                # the BICEPS/LOINC concept code from Type.Code (primary) or from
                # Type.ConceptDescription[0].text (fallback).
                # The resulting dict is used in on_metric_update() to feed the
                # physiological state graph by semantic code, not by local handle.
                # Must be inside `with self.data_lock:` because MDIB is being read.
                for _desc in self.mdib.descriptions.NODETYPE.get(
                    pm.NumericMetricDescriptor, []
                ):
                    try:
                        _concept_code: str | None = None
                        _type = getattr(_desc, 'Type', None)
                        if _type is not None:
                            _concept_code = getattr(_type, 'Code', None)
                            if not _concept_code:
                                _cd_list = getattr(_type, 'ConceptDescription', None) or []
                                if _cd_list:
                                    _concept_code = getattr(_cd_list[0], 'text', None)
                        # Last resort: use the Handle itself as concept code.
                        # Happens when the descriptor has no <pm:Type> element.
                        # Guarantees the graph is populated even with incomplete MDIBs.
                        if not _concept_code:
                            _concept_code = _desc.Handle
                        if _concept_code:
                            self._handle_to_concept[_desc.Handle] = str(_concept_code)
                            self.logger.debug(
                                f'[SemanticMap]   handle={_desc.Handle!r} '
                                f'→ concept={_concept_code!r}'
                            )
                    except Exception:
                        pass
                self.logger.info(
                    f'[SemanticMap] Mapped {len(self._handle_to_concept)} '
                    f'NumericMetricDescriptor handle(s) to concept codes.'
                )
                # Log all alert descriptors available in MDIB for reference
                _al_cond_descs = self.mdib.descriptions.NODETYPE.get(pm.AlertConditionDescriptor, [])
                _al_sig_descs  = self.mdib.descriptions.NODETYPE.get(pm.AlertSignalDescriptor, [])
                self.logger.info(
                    f'[AlarmMap] MDIB contains {len(_al_cond_descs)} AlertCondition '
                    f'and {len(_al_sig_descs)} AlertSignal descriptor(s).'
                )
                for _acd in _al_cond_descs:
                    _code = getattr(getattr(_acd, 'Type', None), 'Code', 'N/A')
                    self.logger.debug(
                        f'[AlarmMap]   Condition: handle={_acd.Handle!r} '
                        f'code={_code!r}'
                    )
                for _asd in _al_sig_descs:
                    _code = getattr(getattr(_asd, 'Type', None), 'Code', 'N/A')
                    _mani = getattr(_asd, 'Manifestation', 'N/A')
                    self.logger.debug(
                        f'[AlarmMap]   Signal:    handle={_asd.Handle!r} '
                        f'code={_code!r} manifestation={_mani!r}'
                    )

                # ------------------------------------------------------------------
                # STEP 2b: Location filter (if target_room is set)
                # ------------------------------------------------------------------
                # WSDiscovery discovers all SDC providers on the network regardless
                # of their physical location. LocationContext is only available
                # AFTER init_mdib() -- it is stored in context_states in the MDIB.
                #
                # NOTE: _get_device_room() cannot be called here -- it acquires
                # data_lock internally, which would deadlock (Lock is not reentrant).
                # Instead read LocationContext directly (data_lock already held).
                #
                # If target_room is set and device room does not match ->
                # return from _worker_logic() cleanly (return, not exception).

                _device_room_here = ''
                try:
                    _loc_states = [s for s in self.mdib.context_states.objects
                                   if s.NODETYPE == pm.LocationContextState]
                    if _loc_states and _loc_states[0].LocationDetail:
                        _device_room_here = _loc_states[0].LocationDetail.Room or ''
                except Exception as _loc_err:
                    self.logger.error(f'Error reading LocationContext: {_loc_err}')

                # Register the room with Manager for the room-switcher dropdown.
                # Call even if no filter is set -- lets room buttons appear
                # as devices from different rooms connect.
                if _device_room_here and hasattr(self.manager, 'register_device_room'):
                    self.manager.register_device_room(self.epr, _device_room_here)

                if self.target_room:
                    if _device_room_here == '':
                        # No LocationContext -> warn but accept the device (no filter)
                        self.logger.warning(
                            f'No LocationContext found in MDIB. '
                            f"Room filter (target='{self.target_room}') skipped -- accepting device."
                        )
                    elif _device_room_here != self.target_room:
                        self.logger.info(
                            f"Location filter: device room '{_device_room_here}' "
                            f"!= target '{self.target_room}'. Disconnecting (not an error)."
                        )
                        # Save EPR->room in Manager for switchRoom() un-ban logic.
                        # _rejected_room_map allows targeted un-banning when switching
                        # to this room without a new TCP connection.
                        if hasattr(self.manager, '_rejected_room_map'):
                            self.manager._rejected_room_map[self.epr] = _device_room_here
                        self._location_filtered = True   # -> ban-list in Manager
                        return  # exit cleanly -- finally calls stop_all() via _graceful_shutdown
                    else:
                        self.logger.info(f"Location filter: room '{_device_room_here}' matches. Accepting.")

            # ------------------------------------------------------------------
            # STEP 2c: Subscribe to SDPi-A R1030/R1031 (session change detection)
            # ------------------------------------------------------------------
            # HOW ObservableProperty WORKS (sdc11073 mechanism):
            # ----------------------------------------------------------------
            # ObservableProperty is a Python descriptor declared at the class level
            # of ConsumerMdib. It intercepts assignment (=) and automatically notifies
            # all registered subscribers on every value change.
            # sdc11073 does the assignment when it processes an incoming SOAP report:
            #
            #   def _on_episodic_metric_report(self, report):
            #       states = {s.DescriptorHandle: s for s in report.states}
            #       self.metrics_by_handle = states   # <- ObservableProperty fires
            #       # -> all subscribed callbacks are invoked automatically
            #
            # Subscribe via:  observableproperties.bind(obj, field_name=callback)
            #
            # Observable fields in ConsumerMdib:
            #   sequence_or_instance_id_changed_event -- device restart / session change
            #   metrics_by_handle     -- EpisodicMetricReport
            #   alert_by_handle       -- EpisodicAlertReport
            #   operation_by_handle   -- EpisodicOperationalStateReport
            #   context_by_handle     -- EpisodicContextReport
            #   component_by_handle   -- EpisodicComponentReport
            #   waveform_by_handle    -- WaveformStream
            #   description_modifications -- DescriptionModificationReport
            #
            # WHY we do NOT poll mdib_version for R1030/R1031:
            # SDC runs over TCP which guarantees delivery and ordering.
            # sdc11073 also validates MdibVersion internally.
            # Polling once per 5 seconds is architecturally wrong: N legitimate
            # reports arrive per interval -> gap = N -> false alarm.
            # Event-driven detection via sequence_or_instance_id_changed_event
            # (subscribed below) is the correct approach.
            observableproperties.bind(
                self.mdib,
                sequence_or_instance_id_changed_event=self._on_sequence_id_changed
            )

            # ------------------------------------------------------------------
            # STEP 3: Subscribe to metric and alert push notifications
            # ------------------------------------------------------------------
            observableproperties.bind(self.mdib, metrics_by_handle=self.on_metric_update)
            observableproperties.bind(self.mdib, alert_by_handle=self.on_alert_update)

            self.logger.info("Connection established. Monitoring...")

            # ------------------------------------------------------------------
            # STEP 3b: Ensemble context binding via SmartAlertAggregator
            # ------------------------------------------------------------------
            # IMPORTANT: called OUTSIDE any with self.data_lock: block.
            # evaluate_and_bind_device() acquires data_lock internally.
            # threading.Lock is NOT reentrant -- calling it while already holding
            # the lock would cause an immediate deadlock.
            aggregator = getattr(self.manager, 'aggregator', None)
            if aggregator is not None:
                self.logger.debug('[Aggregator] Calling evaluate_and_bind_device...')
                try:
                    # ИСПОЛЬЗУЕМ to_thread, так как внутри работает синхронный requests (FHIR)
                    # и синхронный SOAP-клиент (apply_ensemble_context)
                    await asyncio.to_thread(aggregator.evaluate_and_bind_device, self)
                except Exception as _agg_err:
                    self.logger.error(
                        f'[Aggregator] evaluate_and_bind_device raised an unexpected '
                        f'exception -- ensemble binding skipped: {_agg_err}',
                        exc_info=True,
                    )

            # ------------------------------------------------------------------
            # STEP 4: Create Qt object and move it to the UI thread
            # ------------------------------------------------------------------
            self.qtDeviceHandler = QtDeviceHandler(self)

            main_thread = QCoreApplication.instance().thread()
            if main_thread:
                self.qtDeviceHandler.moveToThread(main_thread)
            else:
                self.logger.warning("Could not find Main Thread!")

            self._ui_connected = True
            self.manager.deviceConnected.emit(self.qtDeviceHandler)

            # ------------------------------------------------------------------
            # STEP 5: Main monitoring loop with T_fallback (IHE SDPi)
            # ------------------------------------------------------------------
            # T_fallback: the consumer must detect a connection loss within
            # T_fallback seconds of the last successful data exchange.
            # Implementation: active ping (GetContextStates) every SLEEP_INTERVAL s.
            # If N consecutive pings fail -> declare the connection lost.

            SLEEP_INTERVAL = 5.0        # T_keepalive: ping interval (seconds)
            T_FALLBACK = 15.0           # Max time before declaring disconnect (seconds)
            MAX_MISSED = int(T_FALLBACK / SLEEP_INTERVAL)  # = 3 missed intervals

            missed_heartbeats = 0  # consecutive failed ping counter

            while self.running:
                # Check sdc11073's is_connected flag (reacts to TCP disconnect)
                if not self.consumer.is_connected:
                    self.logger.warning("Connection lost reported by SDC stack.")
                    self.error_occurred = True
                    break

                # Trigger a UI refresh in the main thread
                if self.qtDeviceHandler:
                    self.qtDeviceHandler.scheduleUpdate()

                # Active ping: attempt a lightweight network request.
                # GetContextStates is one of the lightest requests available.
                # asyncio.to_thread() runs it in the thread pool so the event loop
                # is not blocked, allowing incoming WS-Eventing notifications
                # (alarms, metrics) to continue processing even if the device is slow.
                try:
                    if self.consumer and self.consumer.is_connected:
                        if self.consumer.context_service_client:
                            await asyncio.to_thread(
                                self.consumer.context_service_client.get_context_states
                            )
                            # Ping succeeded -- context queries are allowed
                            if not getattr(self, '_ctx_ping_ok_logged', False):
                                self.logger.info(
                                    '[DIAG] GetContextStates ping: SUCCESS -- '
                                    'device allows context queries without authorization.'
                                )
                                self._ctx_ping_ok_logged = True
                        else:
                            # ContextService not advertised by the device
                            if not getattr(self, '_no_ctx_svc_logged', False):
                                self.logger.warning(
                                    '[DIAG] No context_service_client -- '
                                    'device did not advertise ContextService in metadata.'
                                )
                                self._no_ctx_svc_logged = True
                    missed_heartbeats = 0  # ping succeeded -- reset counter

                except HTTPReturnCodeError as e:
                    # HTTP 4xx means TCP connection is alive -- provider replied but
                    # rejected the request (e.g. HTTP 400 "unauthorized participant"
                    # from sdcX with --no_tls). This is NOT a disconnect -- reset counter.
                    missed_heartbeats = 0
                    # Log once to avoid console spam.
                    if not getattr(self, '_auth_warn_logged', False):
                        self.logger.warning(
                            f'[DIAG] Ping: provider returned HTTP {e.status} ({e.reason}) -- '
                            f'connection alive but GetContextStates is blocked '
                            f'(unauthorized). Context data came via GetMdib only. '
                            f'Suppressing further warnings.'
                        )
                        self._auth_warn_logged = True

                except Exception as e:
                    missed_heartbeats += 1
                    self.logger.warning(f"Ping failed ({missed_heartbeats}/{MAX_MISSED}): {e}")
                    # Check whether T_fallback threshold has been exceeded
                    if missed_heartbeats * SLEEP_INTERVAL >= T_FALLBACK:
                        self.logger.error("T_fallback exceeded. Disconnecting.")
                        self.error_occurred = True
                        break  # exit loop -> thread finishes -> Manager removes device

                # ------------------------------------------------------------------
                # Ack-timeout: re-raise alarms that have been silenced too long
                # ------------------------------------------------------------------
                now_mono = time.monotonic()
                expired = [
                    h for h, t in list(self._ack_timestamps.items())
                    if now_mono - t >= self.ACK_TIMEOUT_SEC
                ]
                for sig_handle in expired:
                    self.logger.warning(
                        f'[Ack-timeout] {sig_handle}: Ack held for '
                        f'{self.ACK_TIMEOUT_SEC:.0f}s — re-raising alarm (Presence=On).'
                    )
                    op_handle = None
                    try:
                        with self.data_lock:
                            if self.mdib:
                                op_descs = self.mdib.descriptions.NODETYPE.get(
                                    pm.SetAlertStateOperationDescriptor, []
                                )
                                for op in op_descs:
                                    if op.OperationTarget == sig_handle:
                                        op_handle = op.Handle
                                        break
                                if not op_handle and op_descs:
                                    op_handle = op_descs[0].Handle
                    except Exception as _e:
                        self.logger.error(f'[Ack-timeout] Could not find op_handle: {_e}')

                    if op_handle:
                        try:
                            await asyncio.to_thread(
                                self._reactivate_alarm, op_handle, sig_handle
                            )
                        except Exception as _e:
                            self.logger.error(f'[Ack-timeout] Re-raise failed: {_e}')
                    self._ack_timestamps.pop(sig_handle, None)

                # Yield control back to the event loop, allowing other async tasks
                # (e.g. incoming notifications) to be processed.
                await asyncio.sleep(SLEEP_INTERVAL)

        except Exception as e:
            # Critical error during connection or initialisation phase.
            self.logger.error(f"Critical Error ({type(e).__name__}): {e}", exc_info=True)
            self.error_occurred = True
        finally:
            # DEV-49: Graceful session termination.
            # _graceful_shutdown() sends WS-Eventing Unsubscribe on all active
            # subscriptions with a 5-second timeout. This tells the bedside monitor
            # that the Orchestrator is shutting down cleanly, not crashing.
            # Without this the device may activate a fallback alarm (60 dBA).
            if self.consumer:
                self.logger.info("Stopping consumer resources (DEV-49 graceful)...")
                try:
                    await self._graceful_shutdown()
                except Exception as e:
                    # Last resort: force-close synchronously
                    self.logger.error(f"Graceful shutdown failed ({e}), forcing stop.")
                    try:
                        self.consumer.stop_all()
                    except Exception:
                        pass

    # =========================================================================
    # Helper: read device room from MDIB LocationContext
    # =========================================================================
    def _get_device_room(self) -> str:
        """
        Reads the device's room identifier from its MDIB LocationContextState.

        Returns:
            The Room string from LocationDetail (e.g. "ICU-3").
            Empty string "" if no LocationContextState is present, or if
            LocationDetail / Room is None.

        Thread safety: MUST be called WITHOUT data_lock held -- this method
        acquires data_lock internally for the read and releases it immediately.
        The lock is needed because _worker_logic() may run concurrently with
        sdc11073 notification threads that update context_states.
        """
        try:
            with self.data_lock:
                if not self.mdib:
                    return ""
                loc_states = [
                    s for s in self.mdib.context_states.objects
                    if s.NODETYPE == pm.LocationContextState
                ]
                if loc_states and loc_states[0].LocationDetail:
                    return loc_states[0].LocationDetail.Room or ""
        except Exception as e:
            self.logger.error(f"_get_device_room error: {e}")
        return ""

    # =========================================================================
    # Send ensemble UUID to the provider via EnsembleContextState
    # =========================================================================
    def apply_ensemble_context(self, ensemble_uuid: str) -> bool:
        """
        Builds an EnsembleContextState with the given UUID and sends it to the
        SDC Provider via a SetContextState SOAP call.

        Called from SmartAlertAggregator.evaluate_and_bind_device() once the
        aggregator has decided which ensemble this device belongs to.

        On success: stores the UUID in self.ensemble_uuid.

        Returns:
          True  -- EnsembleContext successfully applied on the provider.
          False -- operation skipped (no descriptor, no connection, error).

        TWO-PHASE PATTERN (thread-safe):
          Phase 1 (under data_lock):  read MDIB, build proposed state.
          Phase 2 (lock released):    send SetContextState over the network.
        """
        from sdc11073.xml_types import pm_qnames as _pm
        from sdc11073.xml_types import pm_types as _pm_types

        operation_handle: str | None = None
        proposed_ens = None

        # ------------------------------------------------------------------
        # Phase 1: Build EnsembleContextState under data_lock
        # ------------------------------------------------------------------
        try:
            with self.data_lock:
                if not self.mdib or not self.consumer:
                    self.logger.warning('apply_ensemble_context: MDIB or consumer not ready.')
                    return False

                # Locate EnsembleContextDescriptor -- indicates device supports ensemble membership
                ens_descriptors = self.mdib.descriptions.NODETYPE.get(
                    _pm.EnsembleContextDescriptor, []
                )
                if not ens_descriptors:
                    self.logger.warning(
                        'apply_ensemble_context: no EnsembleContextDescriptor in MDIB -- '
                        'device does not support ensemble binding.'
                    )
                    return False
                descriptor = ens_descriptors[0]

                # Find the SetContextState operation targeting EnsembleContextDescriptor.
                # Filtering by OperationTarget is essential: a device may expose several
                # SetContextState operations (one per context type). Using the wrong handle
                # causes the provider to respond with OperationNotAllowed.
                set_ctx_ops = self.mdib.descriptions.NODETYPE.get(
                    _pm.SetContextStateOperationDescriptor, []
                )
                for op in set_ctx_ops:
                    if op.OperationTarget == descriptor.Handle:
                        operation_handle = op.Handle
                        break

                if not operation_handle:
                    self.logger.warning(
                        'apply_ensemble_context: no SetContextState operation '
                        'targeting EnsembleContextDescriptor found.'
                    )
                    return False

                # Build proposed EnsembleContextState -- update existing or create new
                existing_ens = self.mdib.context_states.NODETYPE.get(
                    _pm.EnsembleContextState, []
                )
                if existing_ens:
                    proposed_ens = existing_ens[0].mk_copy()
                    # Clear stale identifiers to avoid accumulating duplicate UUIDs
                    # if this method is called more than once for the same device.
                    if getattr(proposed_ens, 'Identification', None) is not None:
                        proposed_ens.Identification.clear()
                else:
                    proposed_ens = self.consumer.context_service_client.mk_proposed_context_object(
                        descriptor.Handle
                    )

                proposed_ens.ContextAssociation = _pm_types.ContextAssociation.ASSOCIATED

                # InstanceIdentifier carries two pieces of information:
                #   root      -- fixed UUID identifying this Orchestrator system.
                #   extension -- session-specific ensemble UUID from the aggregator.
                identifier = _pm_types.InstanceIdentifier(
                    root='bce837e3-0c46-4e52-af32-15bb36cfd746',
                    extension_string=ensemble_uuid,
                )
                identifier.IdentifierName = [_pm_types.LocalizedText(ensemble_uuid)]

                if getattr(proposed_ens, 'Identification', None) is None:
                    proposed_ens.Identification = []
                proposed_ens.Identification.append(identifier)

        except Exception as exc:
            self.logger.error(f'apply_ensemble_context: failed to build state -- {exc}')
            return False

        # ------------------------------------------------------------------
        # Phase 2: Send SetContextState (lock released -- network call)
        # ------------------------------------------------------------------
        try:
            if not self.consumer.context_service_client:
                self.logger.warning('apply_ensemble_context: context_service_client not available.')
                return False

            self.logger.info(
                f'Sending EnsembleContext {ensemble_uuid[:8]}... '
                f'to provider (op={operation_handle}).'
            )
            self.consumer.context_service_client.set_context_state(
                operation_handle=operation_handle,
                proposed_context_states=[proposed_ens],
            )

            # Store locally -- DeviceHandler now knows its ensemble membership
            self.ensemble_uuid = ensemble_uuid
            self.logger.info(
                f'EnsembleContext applied successfully. '
                f'Device {self.epr[-12:]} bound to ensemble {ensemble_uuid[:8]}...'
            )
            return True

        except Exception as exc:
            self.logger.error(f'apply_ensemble_context: SOAP call failed -- {exc}')
            return False

    # =========================================================================
    # Apply FHIR patient / clinical context
    # =========================================================================
    def apply_fhir_contexts(self, fhir_data) -> None:
        """
        Receives enriched FHIR patient data from SmartAlertAggregator and
        writes DangerCodes into the device's WorkflowContextState via
        SetContextState SOAP call.

        Called by SmartAlertAggregator.evaluate_and_bind_device() after FHIR
        data has been fetched (or None if the FHIR server was unreachable).

        TWO-PHASE PATTERN (thread-safe):
          Phase 1 (under data_lock):  read MDIB, build proposed WorkflowContextState.
          Phase 2 (lock released):    send SetContextState over the network.

        Parameters:
          fhir_data -- FHIRPatientData instance, or None on fetch failure.
        """
        if fhir_data is None:
            self.logger.warning(
                'apply_fhir_contexts: fhir_data is None '
                '(fetch failed or FHIR server unreachable) -- skipping.'
            )
            return

        from sdc11073.xml_types import pm_qnames as _pm
        from sdc11073.xml_types import pm_types as _pm_types

        # Fetch danger codes from FHIR result
        raw_danger_codes = fhir_data.get_danger_codes()
        if not raw_danger_codes:
            self.logger.info(
                f'apply_fhir_contexts: FHIR returned no DangerCodes for patient '
                f'{fhir_data.get_patient_id()!r} -- nothing to write.'
            )
            return

        operation_handle: str | None = None
        proposed_wf = None

        # ------------------------------------------------------------------
        # Phase 1: Build proposed WorkflowContextState under data_lock
        # ------------------------------------------------------------------
        try:
            with self.data_lock:
                if not self.mdib or not self.consumer:
                    self.logger.warning('apply_fhir_contexts: MDIB or consumer not ready.')
                    return

                # Locate WorkflowContextDescriptor
                wf_descriptors = self.mdib.descriptions.NODETYPE.get(
                    _pm.WorkflowContextDescriptor, []
                )
                if not wf_descriptors:
                    self.logger.warning(
                        'apply_fhir_contexts: no WorkflowContextDescriptor in MDIB -- '
                        'device does not support WorkflowContext.'
                    )
                    return
                wf_descriptor = wf_descriptors[0]

                # Find a registered SetContextState operation handle.
                # sdc11073's GenericContextProvider registers handlers for
                # PatientContext and EnsembleContext ops, but NOT WorkflowContext.
                # Any registered SetContextState handle accepts any context state
                # type -- we prefer the patient-context op (opSetPatCtx) since
                # WorkflowContext is patient-related, falling back to the first
                # available op.
                set_ctx_ops = self.mdib.descriptions.NODETYPE.get(
                    _pm.SetContextStateOperationDescriptor, []
                )
                # Preferred: op targeting PatientContextDescriptor (has registered handler)
                pat_descriptors = self.mdib.descriptions.NODETYPE.get(
                    _pm.PatientContextDescriptor, []
                )
                pat_handle = pat_descriptors[0].Handle if pat_descriptors else None
                for op in set_ctx_ops:
                    if pat_handle and op.OperationTarget == pat_handle:
                        operation_handle = op.Handle
                        break
                # Fallback: any SetContextState op
                if not operation_handle and set_ctx_ops:
                    operation_handle = set_ctx_ops[0].Handle

                if not operation_handle:
                    self.logger.warning(
                        'apply_fhir_contexts: no SetContextState operation found in MDIB.'
                    )
                    return

                # Get existing WorkflowContextState to copy, or create new
                wf_states = self.mdib.context_states.NODETYPE.get(
                    _pm.WorkflowContextState, []
                )
                if wf_states:
                    proposed_wf = wf_states[0].mk_copy()
                else:
                    proposed_wf = self.consumer.context_service_client.mk_proposed_context_object(
                        wf_descriptor.Handle
                    )

                proposed_wf.ContextAssociation = _pm_types.ContextAssociation.ASSOCIATED

                # Build CodedValue list from FHIR danger codes
                coded_danger_codes = []
                for dc in raw_danger_codes:
                    try:
                        coded_value = _pm_types.CodedValue(dc['code'])
                        if dc.get('system'):
                            coded_value.CodingSystem = dc['system']
                        if dc.get('display'):
                            coded_value.ConceptDescription = [
                                _pm_types.LocalizedText(dc['display'])
                            ]
                        coded_danger_codes.append(coded_value)
                    except Exception as _cv_err:
                        self.logger.warning(
                            f'apply_fhir_contexts: could not build CodedValue '
                            f'for {dc!r}: {_cv_err}'
                        )

                if not coded_danger_codes:
                    self.logger.warning('apply_fhir_contexts: all DangerCode conversions failed.')
                    return

                # Assign to WorkflowDetail.DangerCode
                if proposed_wf.WorkflowDetail is None:
                    self.logger.warning(
                        'apply_fhir_contexts: WorkflowDetail is None -- cannot set DangerCode.'
                    )
                    return
                proposed_wf.WorkflowDetail.DangerCode = coded_danger_codes

        except Exception as exc:
            self.logger.error(f'apply_fhir_contexts: failed to build state -- {exc}', exc_info=True)
            return

        # ------------------------------------------------------------------
        # Phase 2: Send SetContextState (lock released -- network call)
        # ------------------------------------------------------------------
        try:
            if not self.consumer.context_service_client:
                self.logger.warning('apply_fhir_contexts: context_service_client not available.')
                return

            self.logger.info(
                f'apply_fhir_contexts: sending {len(coded_danger_codes)} DangerCode(s) '
                f'to WorkflowContext (op={operation_handle}).'
            )
            self.consumer.context_service_client.set_context_state(
                operation_handle=operation_handle,
                proposed_context_states=[proposed_wf],
            )
            self.logger.info(
                f'apply_fhir_contexts: WorkflowContext DangerCodes applied successfully '
                f'on device {self.epr[-12:]}.'
            )
        except Exception as exc:
            self.logger.error(f'apply_fhir_contexts: SOAP call failed -- {exc}')

    # =========================================================================
    # Callback: metric updates
    # =========================================================================
    def on_metric_update(self, metrics_by_handle):
        """
        Called by sdc11073 from its notification thread on EpisodicMetricReport.

        Phase 1 — Physiological graph update (no data_lock):
          The updated state objects are already delivered as arguments, so
          acquiring data_lock here is FORBIDDEN (would cause a deadlock with
          the lock held in _worker_logic during MDIB initialisation).
          Instead, _handle_to_concept (built once under data_lock at startup)
          is used for lock-free handle -> concept_code lookup.

        Phase 2 — Rate-limited UI refresh (1 Hz):
          Calls QtDeviceHandler.scheduleUpdate() at most once per second to
          avoid flooding the main thread with repaints during waveform data.
        """
        # -- Phase 1: Update physiological state graph ----------------------------
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
                    concept_code = self._handle_to_concept.get(
                        getattr(state, 'DescriptorHandle', '')
                    )
                    if concept_code:
                        aggregator.update_metric_state(
                            self.ensemble_uuid, concept_code, float(value)
                        )
                        self.logger.debug(
                            f'[PhysGraph] {concept_code}={float(value):.4g} '
                            f'handle={state.DescriptorHandle!r} '
                            f'ensemble={self.ensemble_uuid[:8]}'
                        )
                except Exception:
                    pass

        # -- Phase 2: Rate-limited UI refresh -------------------------------------
        if not self.qtDeviceHandler:
            return
        now = time.monotonic()
        if now - self._last_ui_update_ts >= 1.0:
            self._last_ui_update_ts = now
            self.qtDeviceHandler.scheduleUpdate()

    # =========================================================================
    # Callback: alert updates
    # =========================================================================
    def on_alert_update(self, alert_by_handle):
        """
        Called by sdc11073 from its notification thread on EpisodicAlertReport.
        Triggers an immediate UI refresh (0.2 s anti-spam cooldown).

        Handles two fundamentally different Presence types:
          AlertConditionState.Presence  -> Python bool  (True / False)
          AlertSignalState.Presence     -> AlertSignalPresence enum (On / Off / Ack / Latch)

        All alarm transitions (ON, OFF, ACK) are logged at WARNING so they
        are always visible regardless of log-level filter.
        """
        now = time.monotonic()

        for handle, state in alert_by_handle.items():
            raw_presence = getattr(state, 'Presence', None)

            # -- Alert type label -------------------------------------------------
            node_type  = getattr(state, 'NODETYPE', None)
            node_name  = getattr(node_type, 'localname', '') if node_type else ''
            type_label = ('Condition' if 'Condition' in node_name
                          else 'Signal' if 'Signal' in node_name
                          else 'Alert')

            # -- Non-blocking concept-code hint (safe in notification thread) -----
            _desc_handle  = getattr(state, 'DescriptorHandle', handle)
            _concept_hint = ''
            if self.data_lock.acquire(blocking=False):
                try:
                    if self.mdib:
                        _desc = self.mdib.descriptions.handle.get_one(
                            _desc_handle, allow_none=True
                        )
                        if _desc:
                            _code = getattr(getattr(_desc, 'Type', None), 'Code', None)
                            if _code:
                                _concept_hint = f' code={_code!r}'
                except Exception:
                    pass
                finally:
                    self.data_lock.release()

            _ens = f' ensemble={self.ensemble_uuid[:8]}' if self.ensemble_uuid else ''
            _dev = self.epr[-12:]

            # ==================================================================
            # BRANCH A: AlertConditionState — Presence is Python bool
            # ==================================================================
            if isinstance(raw_presence, bool):
                if raw_presence:
                    self.logger.warning(
                        f'[ALARM] 🔴 ON  | {type_label} handle={handle!r}'
                        f'{_concept_hint}{_ens} | device={_dev}'
                    )
                else:
                    self.logger.warning(
                        f'[ALARM] 🟢 OFF | {type_label} handle={handle!r}'
                        f'{_concept_hint}{_ens} | device={_dev}'
                    )

            # ==================================================================
            # BRANCH B: AlertSignalState — Presence is AlertSignalPresence enum
            # ==================================================================
            else:
                ack_str  = str(pm_types.AlertSignalPresence.ACK)
                off_str  = str(pm_types.AlertSignalPresence.OFF)
                on_str   = str(pm_types.AlertSignalPresence.ON)
                presence = str(raw_presence) if raw_presence is not None else ''

                if presence == on_str:
                    self.logger.warning(
                        f'[ALARM] 🔴 ON  | {type_label} handle={handle!r}'
                        f'{_concept_hint}{_ens} | device={_dev}'
                    )
                elif presence == off_str:
                    self.logger.warning(
                        f'[ALARM] 🟢 OFF | {type_label} handle={handle!r}'
                        f'{_concept_hint}{_ens} | device={_dev}'
                    )
                elif presence == ack_str:
                    self.logger.warning(
                        f'[ALARM] 🔕 ACK | {type_label} handle={handle!r}'
                        f'{_concept_hint}{_ens} | device={_dev}'
                    )
                else:
                    # Latch or unknown — still log at INFO so it is always visible
                    self.logger.info(
                        f'[ALARM] ❓ {presence!r} | {type_label} handle={handle!r}'
                        f'{_concept_hint}{_ens} | device={_dev}'
                    )

                # -- Ack-timeout tracking (signals only) ----------------------
                if presence == ack_str:
                    if handle not in self._ack_timestamps:
                        self._ack_timestamps[handle] = now
                        self.logger.warning(
                            f'[Ack-timeout] {handle}: countdown started — '
                            f'will re-raise in {self.ACK_TIMEOUT_SEC:.0f}s if not cleared.'
                        )
                elif presence in (off_str, on_str):
                    if handle in self._ack_timestamps:
                        held_sec = now - self._ack_timestamps.pop(handle)
                        self.logger.warning(
                            f'[Ack-timeout] {handle}: timer cleared '
                            f'(Presence={presence}, held Ack for {held_sec:.1f}s).'
                        )

        if not self.qtDeviceHandler:
            return
        if now - self._last_ui_update_ts >= 0.2:
            self._last_ui_update_ts = now
            self.qtDeviceHandler.scheduleUpdate()

    # =========================================================================
    # DEV-31: Remote alarm acknowledgement
    # =========================================================================
    # Ack-timeout helper: re-raise a previously silenced alarm
    # =========================================================================
    def _reactivate_alarm(self, operation_handle: str, alert_signal_handle: str):
        """
        Sends SetAlertState(Presence=On) to the provider for a signal that has
        been in Ack state beyond ACK_TIMEOUT_SEC.  Called from asyncio.to_thread()
        inside the monitoring loop so it does not block the event loop.
        """
        try:
            with self.data_lock:
                if not self.consumer or not self.mdib:
                    return
                proposed = self.mdib.xtra.mk_proposed_state(alert_signal_handle)
                proposed.Presence = pm_types.AlertSignalPresence.ON

            if self.consumer.set_service_client:
                future = self.consumer.set_service_client.set_alert_state(
                    operation_handle, proposed
                )
                future.result(timeout=5)
                self.logger.info(
                    f'[Ack-timeout] {alert_signal_handle}: successfully re-raised to On.'
                )
        except Exception as e:
            self.logger.error(f'_reactivate_alarm error: {e}')

    # =========================================================================
    # DEV-31: Remote alarm acknowledgement
    # =========================================================================
    def acknowledge_alarm(self, operation_handle: str, alert_signal_handle: str):
        """
        Acknowledges an active alarm signal on the device (DEV-31 from IHE SDPi).

        Acknowledgement transitions AlertSignalPresence from On -> Ack:
          On    -- alarm active, audio and visual indication enabled
          Ack   -- audio suppressed (user acknowledged), visual indication remains
          Latch -- parameter returned to normal but manual reset required
          Off   -- alarm inactive

        Parameters:
          operation_handle    -- handle of the SetAlertState operation (from MDIB)
          alert_signal_handle -- handle of the specific AlertSignalState to acknowledge

        TWO-PHASE PATTERN:
          Phase 1 (under data_lock): read state from MDIB, build proposed state.
          Phase 2 (lock released):   send SetAlertState over the network.
        """
        proposed_state = None

        # ------------------------------------------------------------------
        # Phase 1: Prepare proposed state under lock
        # ------------------------------------------------------------------
        try:
            with self.data_lock:
                if not self.consumer or not self.mdib:
                    self.logger.warning("acknowledge_alarm: consumer or mdib not available.")
                    return
                if not self.consumer.set_service_client:
                    self.logger.warning("acknowledge_alarm: set_service_client not available.")
                    return

                # mk_proposed_state() lives on mdib.xtra (ConsumerMdibMethods).
                # Creates a copy of the current state for the given handle.
                proposed_state = self.mdib.xtra.mk_proposed_state(alert_signal_handle)

                # Set new Presence value = ACK
                proposed_state.Presence = pm_types.AlertSignalPresence.ACK

        except Exception as e:
            self.logger.error(f"Failed to prepare alarm acknowledgement: {e}")
            return

        # ------------------------------------------------------------------
        # Phase 2: Network call SetAlertState (without data_lock)
        # ------------------------------------------------------------------
        try:
            # set_alert_state() returns a Future -- operation result from the device
            future = self.consumer.set_service_client.set_alert_state(
                operation_handle,
                proposed_state
            )
            # Wait for device confirmation (5-second timeout)
            future.result(timeout=5)
            self.logger.info(f"Alarm '{alert_signal_handle}' acknowledged successfully.")
        except Exception as e:
            self.logger.error(f"Failed to acknowledge alarm: {e}")

    # =========================================================================
    # DEV-49: Graceful monitoring session termination
    # =========================================================================
    async def _graceful_shutdown(self):
        """
        Implements DEV-49 (IHE SDPi) -- graceful end of monitoring session.

        Logic:
          1. Send WS-Eventing Unsubscribe on all active subscriptions.
          2. Wait for confirmation (timeout 5 seconds).
          3. If device does not respond -- force-close the connection.

        Why this matters:
          If a TCP socket is closed without Unsubscribe the bedside monitor
          treats it as an abnormal disconnect and activates a fallback alarm
          (60 dBA). A graceful Unsubscribe tells the device the observer left
          intentionally, and the device resumes its own alarm management.

        asyncio.to_thread() is needed because stop_all() is a synchronous
        blocking call -- it runs in the thread pool so the event loop is free.
        """
        self._intentional_shutdown = True
        try:
            # Give stop_all() up to 5 seconds to send Unsubscribe and get a reply.
            await asyncio.wait_for(
                asyncio.to_thread(self.consumer.stop_all),
                timeout=5.0
            )
            self.logger.info("DEV-49: Unsubscribe completed -- device notified.")
        except asyncio.TimeoutError:
            self.logger.warning("DEV-49: Unsubscribe timed out (5s). Forcing close.")
        except Exception as e:
            self.logger.error(f"DEV-49: Error during graceful shutdown: {e}")

    # =========================================================================
    # SDPi-A R1030/R1031: Device restart / session change detection
    # =========================================================================
    def _on_sequence_id_changed(self, sequence_or_instance_id_changed_event: bool):
        """
        ObservableProperty callback: fires when the device changes its
        SequenceId or InstanceId in SOAP report headers.

        SequenceId/InstanceId changes on:
          - Device reboot
          - SDC session reset (software reset)
          - Active network interface change

        In any of these cases the local MDIB copy is stale: the device has
        started a new "life" with a new MDIB. The only correct action is to
        disconnect and reconnect, calling GetMdib again to get the current
        alarm state.

        THREAD SAFETY: called from the sdc11073 notification thread (not the
        asyncio loop). Writing bool to self.running/self.error_occurred is
        atomic thanks to Python's GIL.
        """
        if not sequence_or_instance_id_changed_event:
            return  # False value -- ignore (ObservableProperty may reset to False)

        self.logger.warning(
            "SDPi-A: SequenceId/InstanceId changed! "
            "Device may have restarted -- forcing reconnect to resync MDIB."
        )
        self.error_occurred = True
        self.running = False  # exit loop on next iteration

    # =========================================================================
    # Stop signal
    # =========================================================================
    def stop(self):
        """
        Graceful worker stop.
        Sets self.running = False, which causes the monitoring loop to exit
        on its next iteration (after the current asyncio.sleep()).
        """
        self.running = False
