"""
app.alarms — IHE-PCD Alarm Management Reference Implementation.

Sub-package of the SDC Consumer Dashboard application.
Encapsulates the IHE-PCD ACM Alarm Coordinator (transparent facade), the
per-ensemble AdaptiveAlarmAggregator (clean stub awaiting the new two-axis
math core), and the Smart Alert Aggregator (ensemble management + physiological
graph).

Public API
----------
    from app.alarms import AlarmCoordinator, DeviceAlertEvidence, AlarmDecision
    from app.alarms import AdaptiveAlarmAggregator, TickResult
    from app.alarms import SmartAlertAggregator
    from app.alarms import DeviceReliabilityProfile, DeviceProfileRepository, get_repository
"""

from .alarmCoordinator import (
    AlarmCoordinator,
    DeviceAlertEvidence,
    AlarmDecision,
)
from .adaptive_alarm_aggregator import (
    AdaptiveAlarmAggregator,
)
from .math_types import (
    EngineConfig,
    SensorSpec,
    TickResult,
)
from .evidence_accumulator import EvidenceAccumulator
from .clinical_context import ClinicalContext
from .urgency_engine import UrgencyEngine
from .hysteresis_filter import HysteresisFilter
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
    'AdaptiveAlarmAggregator',
    'TickResult',
    'EngineConfig',
    'SensorSpec',
    'EvidenceAccumulator',
    'ClinicalContext',
    'UrgencyEngine',
    'HysteresisFilter',
    'SmartAlertAggregator',
    'DeviceReliabilityProfile',
    'DeviceProfileRepository',
    'get_repository',
]



