"""
app.alarms — IHE-PCD Alarm Management Reference Implementation.

Sub-package of the SDC Consumer Dashboard application.
Encapsulates the IHE-PCD ACM Alarm Coordinator (transparent facade), the
per-ensemble AdaptiveAlarmAggregator (two-axis stochastic math core), the
EnsembleTopologyManager (slow path: membership, sensor registry, FHIR) and the
SmartAlertAggregator (fast path: alarm processing / routing).

Public API
----------
    from app.alarms import AlarmCoordinator, DeviceAlertEvidence, AlarmDecision
    from app.alarms import AdaptiveAlarmAggregator, TickResult
    from app.alarms import EnsembleTopologyManager, SmartAlertAggregator
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
from .ensemble_topology_manager import EnsembleTopologyManager
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
    'EnsembleTopologyManager',
    'SmartAlertAggregator',
    'DeviceReliabilityProfile',
    'DeviceProfileRepository',
    'get_repository',
]



