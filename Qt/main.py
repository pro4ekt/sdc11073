from __future__ import annotations

import sys
import os
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from sdcMyConsumer import SdcMyConsumer
"""
import sdc11073
import asyncio
import socket
import threading
import time
from PySide6.QtCore import QObject, Signal, Slot, Property
from GateWay import sdc_opc_gateway
from sdc11073.consumer import SdcConsumer
from sdc11073.mdib import ConsumerMdib
from sdc11073.xml_types.actions import periodic_actions
from sdc11073.mdib.statecontainers import LocationContextStateContainer
from sdc11073.wsdiscovery import WSDiscovery
from sdc11073.xml_types import pm_qnames as pm
from sdc11073.xml_types.pm_qnames import LocationContextState
from sdc11073 import observableproperties # ADDED: For event bindings
# ADDED: Essential enums for robust alarm checking
from sdc11073.xml_types.pm_types import AlertSignalPresence, AlertActivation
"""

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
