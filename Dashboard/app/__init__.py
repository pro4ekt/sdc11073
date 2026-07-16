"""
app — Core application modules for the SDC Consumer.

Public exports:
  SdcMyConsumer       — Manager: WSDiscovery loop + device registry
  QtDeviceHandler     — Qt/QML bridge object (per device)
  DeviceHandler       — SDC worker thread (re-exported from device/)
  SmartAlertAggregator
  AlarmCoordinator
  OperationLogger
  FHIRPatientData
"""

from .sdcMyConsumer import SdcMyConsumer
from .qtDeviceHandler import QtDeviceHandler
from .deviceHandler import DeviceHandler
from .alarms.smartAlertAggregator import SmartAlertAggregator
from .alarms.alarmCoordinator import AlarmCoordinator
from .operationLogger import OperationLogger
from .fhirData import FHIRPatientData

__all__ = [
    'SdcMyConsumer',
    'QtDeviceHandler',
    'DeviceHandler',
    'SmartAlertAggregator',
    'AlarmCoordinator',
    'OperationLogger',
    'FHIRPatientData',
]
