# Dashboard/app/alarms — adaptive stochastic alarm model

Two-axis alarm filter per patient ensemble: a confidence axis built from the alarm evidence
of all admitted devices and an urgency axis that sets the escalation threshold. The normative
text is thesis chapter 3 (`docs/model/math_part1.md`), deviations are listed in `docs/model/SPEC_DELTA.md`.

- `alarmCoordinator.py` — `AlarmCoordinator`, `DeviceAlertEvidence`, `AlarmDecision`: stateless facade between the device handlers and the aggregator; `alert_key_for` names a channel.
- `ensemble_topology_manager.py` — `EnsembleTopologyManager` (slow path): which devices form one patient ensemble, FHIR binding.
- `smartAlertAggregator.py` — `SmartAlertAggregator` (fast path): processes every alarm event, owns one `AdaptiveAlarmAggregator` per ensemble, drives periodic ticks.
- `adaptive_alarm_aggregator.py` — `AdaptiveAlarmAggregator`: one tick of the model.
- `evidence_accumulator.py` (`EvidenceAccumulator`, FIR window), `urgency_engine.py` (`UrgencyEngine`, quorum `k_min`, target threshold), `hysteresis_filter.py` (`HysteresisFilter`, asymmetric IIR), `clinical_context.py` (`ClinicalContext`, prior odds) — the math core, pure Python without Qt.
- `device_profile_repo.py` — `DeviceProfileRepository`: reliability profiles (TPR/FPR, priorities) from `Dashboard/config/clinical_db.json`.
- `math_types.py` — `SensorSpec`, `EngineConfig`, `TickResult`.

Covered by `tests/test_math_core.py` (86 tests) and the integration suites in `tests/`.
