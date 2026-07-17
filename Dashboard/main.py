"""
main.py -- Entry point for the SDC Consumer application (ICU mode).

ROOM FILTER (--room):
  If specified, the Consumer subscribes ONLY to SDC Providers whose
  LocationContext.Room matches the given value.

TLS MODE (--tls / --no_tls):
  --tls      Force TLS for all connections (loads certificates from
             certs_out/ or pat/certs/). Use when a Provider announces
             http:// but actually requires TLS.
  --no_tls   Disable TLS entirely -- plain HTTP, no fallback. Useful for
             testing Providers without certificates.
  (no flag)  Auto mode: https:// -> TLS immediately; http:// -> plain with
             TLS-fallback if ConnectionResetError is received.

Examples:
  python main.py
  python main.py --room="Room_1"
  python main.py --no_tls
  python main.py --tls --room="ICU-3"
"""

from __future__ import annotations

import sys
import os
import argparse
import logging
import datetime

from sdc11073.loghelper import basic_logging_setup

# Manager -- coordinator of the SDC device network
from app.sdcMyConsumer import SdcMyConsumer

if __name__ == "__main__":

    # Import module logger from device package (configured with file handler)
    from app.deviceHandler import _module_log as _log

    # ------------------------------------------------------------------
    # STEP 0: Parse command-line arguments
    # ------------------------------------------------------------------
    parser = argparse.ArgumentParser(
        description="SDC Consumer -- medical device orchestrator (ICU)"
    )
    parser.add_argument(
        "--room",
        default=None,
        metavar="ROOM_ID",
        help=(
            "Room filter (LocationContext.Room). "
            "If set, the Consumer connects ONLY to devices in this room. "
            "Example: --room=\"ICU-3\". "
            "Default: None (filter disabled, all devices accepted)."
        ),
    )
    parser.add_argument(
        "--ip",
        default=None,
        metavar="IP_ADDRESS",
        help=(
            "Network adapter IP address for WSDiscovery. "
            "Use if devices are in a different VMware/network segment. "
            "Example: --ip=192.168.242.100. "
            "Default: auto-detected via route to 8.8.8.8."
        ),
    )

    # -- TLS strategy (mutually exclusive group) ------------------------------
    _tls_group = parser.add_mutually_exclusive_group()
    _tls_group.add_argument(
        "--tls",
        action="store_true",
        default=False,
        help=(
            "Force TLS for ALL connections. "
            "Loads certificates from certs_out/ or pat/certs/. "
            "Use when Provider announces http:// but requires TLS."
        ),
    )
    _tls_group.add_argument(
        "--no_tls",
        action="store_true",
        default=False,
        help=(
            "Disable TLS -- plain HTTP, no TLS-fallback. "
            "Useful for testing Providers without certificates. "
            "Equivalent to running sdcProvider/sdcX with --no_tls."
        ),
    )

    # parse_known_args allows Qt arguments (-platform, -style) without errors
    args, qt_argv = parser.parse_known_args()
    target_room: str | None = args.room
    override_ip: str | None = args.ip
    tls_mode: str = 'force_tls' if args.tls else ('no_tls' if args.no_tls else 'auto')

    # ------------------------------------------------------------------
    # STEP 0b: Configure sdc11073 logging
    # ------------------------------------------------------------------
    # basic_logging_setup configures sdc11073's internal loggers.
    # We pass WARNING to suppress the verbose sdc11073 internals (SOAP,
    # discovery, subscription details) on the console.
    # IMPORTANT: after this call we explicitly restore our own
    # 'sdc.consumer' logger to DEBUG so that deviceHandler.py / smartAlertAggregator.py
    # INFO and DEBUG messages are still written to sdc_consumer.log.
    basic_logging_setup(level=logging.WARNING)

    # Restore our consumer logger level: basic_logging_setup may have
    # reconfigured the 'sdc' hierarchy and overridden our DEBUG level.
    logging.getLogger('sdc.consumer').setLevel(logging.DEBUG)

    # -- Session separator in the log file --------------------------------
    _ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    _sep = '=' * 72
    _log.info(_sep)
    _log.info(f'SESSION START  {_ts}')
    _log.info(
        (f'Room: {target_room}' if target_room else 'No room filter')
        + f'  |  TLS: {tls_mode}'
    )
    _log.info(_sep)

    # ------------------------------------------------------------------
    # STEP 1: Initialise Qt application (BEFORE the Manager)
    # ------------------------------------------------------------------
    effective_argv = [sys.argv[0]] + qt_argv

    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication(effective_argv)

    # ------------------------------------------------------------------
    # STEP 1b: Create PatientOverviewModel (must exist before Manager so
    #          the aggregator can call updateEnsemble from worker threads)
    # ------------------------------------------------------------------
    from app.patientOverviewModel import PatientOverviewModel
    overview_model = PatientOverviewModel()

    # ------------------------------------------------------------------
    # STEP 2: Create Manager and start network discovery
    # ------------------------------------------------------------------
    manager = SdcMyConsumer(
        target_room=target_room,
        override_ip=override_ip,
        tls_mode=tls_mode,
        overview_model=overview_model,
    )
    manager.start()

    # ------------------------------------------------------------------
    # STEP 3: QML engine
    # ------------------------------------------------------------------
    from PySide6.QtQml import QQmlApplicationEngine

    engine = QQmlApplicationEngine()

    engine.rootContext().setContextProperty("sdcManager", manager)
    engine.rootContext().setContextProperty("fhirData", None)
    # PatientOverview.qml reads patientOverview_model.patients
    engine.rootContext().setContextProperty("patientOverview_model", overview_model)

    qml_file = os.path.join(os.path.dirname(__file__), "qml", "Main.qml")
    engine.load(qml_file)

    if not engine.rootObjects():
        sys.exit(-1)

    # ------------------------------------------------------------------
    # STEP 4: Start Qt event loop
    # ------------------------------------------------------------------
    sys.exit(app.exec())
