"""
alarmCoordinator.py — IHE-PCD ACM Alarm Coordinator (stateless facade / router).

Role
----
``AlarmCoordinator`` is a thin, **stateless router** sitting between the SDC
network layer and the two-axis adaptive stochastic math core.  It owns NO
per-ensemble mutable state: every stochastic quantity lives inside the
per-ensemble ``AdaptiveAlarmAggregator`` supplied by the caller
(``SmartAlertAggregator``, which serialises ``tick()`` per ensemble).

On each alarm event the coordinator:
    1. receives the caller-owned aggregator, the pre-computed ``sensor_states``
       (binary a_j for this tick), and ``dt_step`` (wall-clock seconds elapsed);
    2. drives exactly one ``tick()``;
    3. logs the raw two-axis telemetry as a structured audit record;
    4. returns an immutable ``AlarmDecision`` carrying the binary verdict plus
       the full telemetry vector.

Reference model (single source of truth)
----------------------------------------
The core decouples the problem into two orthogonal axes in LOG-ODDS space:

  Confidence axis (LHS):   E(t) = Σ_{j∈M} w_j · s_j(t)
      w_j    = ln(TPR_j / FPR_j)                   — static DataSheet reliability
      s_j(t) = (1/T) Σ_{k=0}^{T-1} a_j(t-k)        — FIR boxcar over window T

  Urgency axis (RHS):      Θ_current(t)            — hysteresis-smoothed barrier
      v_j(t)       = P_j · a_j(t)                  — instantaneous threat (raw)
      SDC_score(t) = α·(max v_j / P_max) + (1-α)·(Σ v_j / Σ P_j)   ∈ [0,1]
      k_min(t)     = ⌊|M| − (|M|−2)·SDC_score(t)⌋  clamped to [2,|M|]
      Θ_target(t)  = k_min(t)·w̄ − Context_Log_Odds
      Θ_current(t) = asymmetric IIR (Fast Attack / Context-Aware Slow Release),
                     ρ(t) = (1 − SDC_score(t)) / T

Escalation condition (binary — NO logistic/sigmoid, NO risk projection):

        Escalate  ⇔  E(t) ≥ Θ_current(t)

Shared DTOs
-----------
``DeviceAlertEvidence`` (input ticket) and ``AlarmDecision`` (output ticket) are
the stable contract between the SDC network layer, the aggregator core, and the
Qt/QML UI bridge.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass  # DTOs: DeviceAlertEvidence and AlarmDecision
from typing import Optional

from .adaptive_alarm_aggregator import AdaptiveAlarmAggregator
from .device_profile_repo import DeviceReliabilityProfile


# ── Shared Data Structures ────────────────────────────────────────────────────

@dataclass(frozen=True)
class DeviceAlertEvidence:
    """
    Immutable input ticket representing one device's contribution to the ensemble.

    Created per-device in on_alert_update() (triggering_evidence) and assembled
    per-ensemble in SmartAlertAggregator (ensemble_evidences list).
    frozen=True → safe to pass across threads without copying or locking.

    reliability_profile is pre-fetched by DeviceHandler at MDIB init time via
    DeviceProfileRepository.  It carries the DataSheet TPR/FPR; SmartAlertAggregator
    consumes it to build a SensorSpec (w_j = ln(TPR/FPR), P_j from biceps_priority)
    for the math core.  None → neutral fail-safe channel (TPR = FPR = 0.5 → w_j = 0).
    """
    alert_key:       str    # MDIB AlertCondition handle (logging only)
    metric_concept:  str    # LOINC/MDC code of the triggering metric
    manufacturer:    str    # DPWS ThisDevice.Manufacturer (retained for logging)
    model:           str    # DPWS ThisDevice.ModelName    (retained for logging)
    ensemble_uuid:   str    # UUID of the patient ensemble this device belongs to
    biceps_priority: str    # BICEPS AlertCondition.Priority: 'Hi'|'Me'|'Lo'|'None'
    # Pre-fetched calibration — populated by DeviceHandler, consumed by the core.
    reliability_profile: Optional[DeviceReliabilityProfile] = None


@dataclass(frozen=True)
class AlarmDecision:
    """
    Immutable output ticket representing the ensemble-level routing decision.

    The verdict is strictly binary: ``escalate`` is True iff the core's escalation
    condition E(t) ≥ Θ_current(t) holds.  There is NO logistic projection and NO
    [0,10] risk score — the model operates purely in log-odds space.

    The raw two-axis telemetry is exposed verbatim for structured audit logging,
    dashboards and unit-test assertions.  A continuous UI "intensity" indicator, if
    desired, should be driven by ``sdc_score`` ∈ [0,1] (a first-class model
    quantity), never by the unbounded log-odds ``evidence``.
    """
    escalate:             bool    # True ⇔ E(t) ≥ Θ_current(t) → forward to UI/log
    contributing_devices: int     # Number of ensemble devices used in the tick
    # ── Two-axis telemetry (filter internals) ─────────────────────────────────
    evidence:             float = 0.0   # E(t) = Σ w_j · s_j(t) — confidence axis (LHS)
    theta_current:        float = 0.0   # Θ_current(t) — hysteresis-smoothed barrier (RHS)
    delta_t:              float = 0.0   # Δt (s) used by the hysteresis kinetics
    sdc_score:            float = 0.0   # SDC_score(t) ∈ [0,1] — normalised severity
    k_min:                int   = 0     # k_min(t) ∈ [2,|M|] — dynamic consensus quorum
    theta_target:         float = 0.0   # Θ_target(t) — raw threshold before IIR smoothing
    rho_decay:            float = 0.0   # ρ(t) = (1-SDC)/T — hysteresis relaxation rate


# ── Facade ────────────────────────────────────────────────────────────────────

class AlarmCoordinator:
    """
    Stateless facade / router implementing the IHE-PCD ACM Alarm Coordinator node.
    Single entry point for SmartAlertAggregator.

    Contract
    --------
    ``evaluate()`` advances the caller-owned per-ensemble AdaptiveAlarmAggregator
    by exactly one ``tick()`` and maps its verdict onto an AlarmDecision:

        result = aggregator.tick(sensor_states, dt_step)
        AlarmDecision(escalate=result.is_escalated, ...telemetry...)

    The verdict is the binary escalation condition E(t) ≥ Θ_current(t); the
    coordinator applies no additional thresholding, smoothing or projection.

    Statelessness
    -------------
    AlarmCoordinator holds NO per-ensemble mutable state.  All stochastic state
    lives inside the AdaptiveAlarmAggregator instance supplied by the caller.
    Thread-safety of the aggregator is the caller's responsibility
    (SmartAlertAggregator serialises tick() per ensemble).
    """

    def __init__(self) -> None:
        self._logger = logging.getLogger('sdc.consumer.alarm_coordinator')

    def evaluate(
        self,
        aggregator:           AdaptiveAlarmAggregator,   # per-ensemble filter (caller-owned)
        sensor_states:        dict[str, int],            # {sensor_id → a_j (0|1)} for this tick
        dt_step:              float,                      # wall-clock seconds since previous tick
        contributing_devices: int,                       # number of devices in the ensemble
    ) -> AlarmDecision:
        """
        Drive one adaptive tick for an incoming alarm event and route the verdict.

        Emits a single structured audit line (INFO) carrying the full two-axis
        telemetry vector, so any escalation decision is fully reconstructable from
        the logs:  escalate, evidence, theta_current, theta_target, sdc_score,
        k_min, rho_decay, delta_t (plus the derived margin = evidence − Θ_current).
        """
        active_now = sum(1 for v in sensor_states.values() if v)
        self._logger.debug(
            f'Driving aggregator tick — {len(sensor_states)} sensor(s), '
            f'dt_step={dt_step:.3f}s, active={active_now}'
        )

        result = aggregator.tick(sensor_states, dt_step)

        routing = 'ESCALATE' if result.is_escalated else 'SUPPRESS'
        self._logger.info(
            f'Tick result: routing={routing} escalate={result.is_escalated} '
            f'evidence={result.evidence:.3f} theta_current={result.current_theta:.3f} '
            f'margin={result.evidence - result.current_theta:+.3f} '
            f'theta_target={result.theta_target:.3f} sdc_score={result.sdc_score:.3f} '
            f'k_min={result.k_min} rho_decay={result.rho_decay:.4f} '
            f'delta_t={result.active_delta_t:.1f}s'
        )

        return AlarmDecision(
            escalate=result.is_escalated,
            contributing_devices=contributing_devices,
            evidence=result.evidence,
            theta_current=result.current_theta,
            delta_t=result.active_delta_t,
            sdc_score=result.sdc_score,
            k_min=result.k_min,
            theta_target=result.theta_target,
            rho_decay=result.rho_decay,
        )

