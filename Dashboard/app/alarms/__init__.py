"""
app.alarms — IHE-PCD Alarm Management Reference Implementation.

Sub-package of the SDC Consumer Dashboard application.
Encapsulates the two-stage IHE-PCD ACM Alarm Coordinator pipeline and
the Smart Alert Aggregator (ensemble management + physiological graph).

Public API
----------
    from app.alarms import AlarmCoordinator, DeviceAlertEvidence, AlarmDecision
    from app.alarms import SmartAlertAggregator
    from app.alarms import DeviceReliabilityProfile, DeviceProfileRepository, get_repository
"""

from .alarmCoordinator import (
    AlarmCoordinator,
    DeviceAlertEvidence,
    AlarmDecision,
    HardwareArtifactFilter,
    ClinicalRiskFilter,
)
from .smartAlertAggregator import SmartAlertAggregator
from .device_profile_repo import (
    DeviceReliabilityProfile,
    DeviceProfileRepository,
    get_repository,
)

__all__ = [
    'AlarmCoordinator',
    'DeviceAlertEvidence',
    'AlarmDecision',
    'HardwareArtifactFilter',
    'ClinicalRiskFilter',
    'SmartAlertAggregator',
    'DeviceReliabilityProfile',
    'DeviceProfileRepository',
    'get_repository',
]

