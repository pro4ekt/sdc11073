"""
alarmCoordinator.py — IHE-PCD ACM Alarm Coordinator (Pipeline pattern).

Pipeline:
  Stage 1 ─ HardwareArtifactFilter    (deterministic dx/dt RoC gate; per-device)
  Stage 2 ─ AdaptiveAlarmAggregator   (continuous-time stochastic Bayesian filter; per-ensemble)
  Facade  ─ AlarmCoordinator          (orchestrator; single entry point for SmartAlertAggregator)

Architectural shift (Stage 2)
-----------------------------
The former *static* ``ClinicalRiskFilter`` (a single-shot Bayesian sensor-fusion
that multiplied Likelihood Ratios and compared a scalar posterior against a fixed
5.0 threshold) has been **fully replaced** by ``AdaptiveAlarmAggregator`` — a
continuous-time stochastic process that:

  * weights each sensor by its hardware reliability   w_j = ln(Se_j / FAR_j);
  * smooths transient activations with a sliding window s_j(t) ∈ [0, 1];
  * decays the escalation boundary Θ(t) as an alarm persists (λ·Δt);
  * shifts Θ(t) by the FHIR-derived clinical prior  ln(P(C|D)/(1−P(C|D))).

Because the adaptive filter is *stateful per patient ensemble*, its instances are
owned and life-cycled by ``SmartAlertAggregator`` (one aggregator per
``ensemble_uuid``).  ``AlarmCoordinator`` remains a stateless facade: it receives
the already-constructed aggregator plus the pre-computed ``sensor_states`` and
``dt_step`` and simply drives one ``tick()``.

Replaces:
  app/signalProcessor.py  (SignalProcessor.validate_alert → HardwareArtifactFilter.validate)
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass  # still needed for DeviceAlertEvidence and AlarmDecision
from typing import Optional

from .adaptive_alarm_aggregator import AdaptiveAlarmAggregator
from .device_profile_repo import DeviceReliabilityProfile


# ── Shared Data Structures ────────────────────────────────────────────────────

@dataclass(frozen=True)
class DeviceAlertEvidence:
    """
    Immutable input ticket representing one device's contribution to the pipeline.

    Created per-device in on_alert_update() (triggering_evidence) and
    assembled per-ensemble in SmartAlertAggregator (ensemble_evidences list).
    frozen=True → safe to pass across threads without copying or locking.

    reliability_profile and roc_limit are pre-fetched by DeviceHandler at MDIB
    init time via DeviceProfileRepository.  The pipeline filters use these directly
    and never touch the database.  None → fail-safe defaults (fail-open / LR+=1.0).
    """
    alert_key:       str    # MDIB AlertCondition handle (logging only)
    metric_concept:  str    # LOINC/MDC code of the triggering metric
    manufacturer:    str    # DPWS ThisDevice.Manufacturer (retained for logging)
    model:           str    # DPWS ThisDevice.ModelName    (retained for logging)
    ensemble_uuid:   str    # UUID of the patient ensemble this device belongs to
    biceps_priority: str    # BICEPS AlertCondition.Priority: 'Hi'|'Me'|'Lo'|'None'
    # Pre-fetched calibration — populated by DeviceHandler, consumed by pipeline filters.
    reliability_profile: Optional[DeviceReliabilityProfile] = None  # for Stage 2 LR+
    roc_limit:           Optional[float]                    = None  # for Stage 1 dx/dt gate


@dataclass(frozen=True)
class AlarmDecision:
    """
    Immutable output ticket representing the ensemble-level routing decision.

    risk_score is a UI-facing projection of the adaptive Stage-2 decision onto
    the legacy [0.0, 10.0] axis, computed from the log-odds margin of the
    continuous-time filter:

        margin      = logit_sum(t) − Θ(t)
        risk_score  = 10 · σ(margin) = 10 / (1 + e^(−margin))       ∈ [0.0, 10.0]

    The mapping is calibrated so that risk_score = 5.0 corresponds exactly to the
    escalation boundary (margin = 0 ⇔ logit_sum = Θ ⇔ is_escalated flips), which
    preserves backward compatibility with the previous fixed-threshold semantics.
    -1.0 signals that Stage 2 was not reached (suppressed at Stage 1).

    The raw stochastic telemetry (logit_sum, theta, delta_t) is exposed for
    traceability, dashboards and unit-test assertions.
    """
    escalate:            bool          # True → forward to UI/log; False → suppress
    risk_score:          float         # Posterior risk ∈ [0.0, 10.0]; -1.0 if N/A
    contributing_devices: int          # Number of ensemble devices used in Stage 2
    suppression_stage:   Optional[str] # 'HardwareArtifactFilter' | 'AdaptiveAlarmAggregator' | None
    suppression_reason:  Optional[str]
    # ── Adaptive Stage-2 telemetry (continuous-time filter internals) ─────────
    logit_sum:           float = 0.0   # Σ w_j · s_j(t) — weighted evidence sum
    theta:               float = 0.0   # Θ(t) — dynamic decision boundary
    delta_t:             float = 0.0   # persistence duration (s) since first activation


# ── Stage 1 ───────────────────────────────────────────────────────────────────

class HardwareArtifactFilter:
    """
    Stage 1 of the alarm pipeline.
    Deterministic Rate-of-Change (dx/dt) gate — evaluated per triggering device.
    Stateless: no locks; all state is in the caller-supplied buffer snapshot.

    RoC limit is supplied via DeviceAlertEvidence.roc_limit (pre-fetched by
    DeviceHandler at MDIB init time).  None → fail-open (concept unknown / not
    yet calibrated).
    """

    def __init__(self) -> None:
        self._logger = logging.getLogger('sdc.consumer.dsp.stage1')

    def validate(
        self,
        evidence: DeviceAlertEvidence,
        metric_buffer: deque,           # snapshot: deque[(value: float, ts: float)]
    ) -> bool:
        """
        Returns True  → RoC is physiologically plausible → proceed to Stage 2.
        Returns False → hardware artifact detected       → suppress pipeline.
        Fail-open on insufficient history or unknown concept code (roc_limit is None).
        """
        # ── 1. Not enough history → fail-open ─────────────────────────────────
        if len(metric_buffer) < 2:
            self._logger.debug(
                f'[Stage1] concept={evidence.metric_concept!r}: buffer too short '
                f'(len={len(metric_buffer)}) — pass-through (fail-open).'
            )
            return True

        v_curr, t_curr = metric_buffer[-1]
        v_prev, t_prev = metric_buffer[-2]
        dt = t_curr - t_prev

        # ── 2. Guard against duplicate timestamps (monotonicity violation) ─────
        if dt <= 0.0:
            return True

        # ── 3. RoC limit not pre-fetched (unknown concept) → fail-open ────────
        if evidence.roc_limit is None:
            self._logger.debug(
                f'[Stage1] concept={evidence.metric_concept!r}: roc_limit not set '
                f'(DeviceHandler did not pre-fetch or concept unknown) — '
                f'pass-through (fail-open).'
            )
            return True

        max_roc = evidence.roc_limit

        # ── 4. D-component: Rate-of-Change ────────────────────────────────────
        rate_of_change = abs(v_curr - v_prev) / dt

        if rate_of_change > max_roc:
            self._logger.info(
                f'[Stage1] ARTIFACT (RoC) — alert={evidence.alert_key!r} '
                f'concept={evidence.metric_concept!r}: '
                f'RoC={rate_of_change:.4f} > limit={max_roc:.4f} u/s '
                f'(Δv={abs(v_curr - v_prev):.4g}, dt={dt:.3f}s). Suppressed.'
            )
            return False

        self._logger.debug(
            f'[Stage1] VALID — alert={evidence.alert_key!r} '
            f'concept={evidence.metric_concept!r}: '
            f'RoC={rate_of_change:.4f} u/s ≤ limit={max_roc:.4f} u/s.'
        )
        return True


# ── Stage 2 ───────────────────────────────────────────────────────────────────

class ClinicalRiskFilter:
    """
    LEGACY — retired from the runtime pipeline (2026-07-31).

    This *static* single-shot Bayesian sensor-fusion filter was the former Stage 2.
    It has been superseded in ``AlarmCoordinator`` by the continuous-time
    ``AdaptiveAlarmAggregator``.  The class is retained **only** so that the offline
    stochastic-validation script ``scripts/monte_carlo_ward.py`` can keep importing
    ``ClinicalRiskFilter.compute_risk`` to reproduce the original convergence study.
    It is no longer instantiated by any production code path.

    Bayesian Sensor Fusion across all devices in a patient ensemble.

    Mathematics (Odds form):
        Prior_Odds     = prior / (1 − prior)
        LR+_i          = sensitivity_i / false_alarm_rate_i      (per device i)
        Posterior_Odds = Prior_Odds × ∏ LR+_i                    (product over ensemble)
        Posterior_P    = Posterior_Odds / (1 + Posterior_Odds)   ∈ [0.0, 1.0]
        P_total        = max(_PRIORITY_WEIGHTS[e.biceps_priority] for e in evidences)
        risk_score     = Posterior_P × P_total                   ∈ [0.0, 10.0]

    Device profiles are pre-fetched by DeviceHandler and embedded in each
    DeviceAlertEvidence.reliability_profile field.  This class never reads the
    database — it only consumes DTO objects delivered by the caller.

    Fail-safe: reliability_profile is None → _FAIL_SAFE_PROFILE (LR+ = 1.0, neutral).
    Stateless: no locks needed.
    """

    _FAIL_SAFE_PROFILE: DeviceReliabilityProfile = DeviceReliabilityProfile(
        sensitivity=0.5,
        false_alarm_rate=0.5,   # LR+ = 1.0 → neutral; does not shift posterior
    )

    # BICEPS ISO/IEEE 11073-10207 AlertCondition.Priority weights.
    # Scales the Bayesian posterior onto a clinically meaningful risk axis [0.0, 10.0].
    _PRIORITY_WEIGHTS: dict[str, float] = {
        'Hi':   10.0,
        'Me':    6.0,
        'Lo':    3.0,
        'None':  0.0,
    }

    def __init__(self) -> None:
        self._logger = logging.getLogger('sdc.consumer.dsp.stage2')

    def _get_profile(
        self,
        evidence: DeviceAlertEvidence,
    ) -> DeviceReliabilityProfile:
        """
        Returns the pre-fetched reliability profile embedded in the evidence DTO.
        Falls back to _FAIL_SAFE_PROFILE (LR+ = 1.0, neutral update) when absent.
        """
        if evidence.reliability_profile is not None:
            return evidence.reliability_profile
        self._logger.debug(
            f'[Stage2] No pre-fetched profile for alert={evidence.alert_key!r} '
            f'mfr={evidence.manufacturer!r} model={evidence.model!r} '
            f'concept={evidence.metric_concept!r} — using fail-safe (LR+=1.0).'
        )
        return self._FAIL_SAFE_PROFILE

    def compute_risk(
        self,
        evidences: list[DeviceAlertEvidence],   # ALL devices in the ensemble
        prior: float = 0.01,                   # Prior: 0.5% baseline ICU crisis prevalence
    ) -> float:
        """
        Computes ensemble risk score via BICEPS-scaled Bayesian Sensor Fusion.

        Step a — P_total:
            P_total = max(_PRIORITY_WEIGHTS[e.biceps_priority] for e in evidences)
            If evidences is empty or all priorities are 'None' → return 0.0 (short-circuit).

        Step b — Posterior_P (Bayesian Product of Likelihood Ratios):
            Prior_Odds     = prior / (1 − prior)
            LR+_i          = sensitivity_i / false_alarm_rate_i
            Posterior_Odds = Prior_Odds × ∏ LR+_i
            Posterior_P    = Posterior_Odds / (1 + Posterior_Odds)  ∈ [0.0, 1.0]

        Step c — risk_score:
            risk_score = Posterior_P × P_total                      ∈ [0.0, 10.0]

        Returns risk_score ∈ [0.0, 10.0].
        """
        if not evidences:
            self._logger.debug('[Stage2] evidences list is empty — risk_score = 0.0')
            return 0.0

        # ── Step a: BICEPS priority weight ─────────────────────────────────────
        p_total = max(
            self._PRIORITY_WEIGHTS.get(e.biceps_priority, 0.0)
            for e in evidences
        )
        if p_total == 0.0:
            self._logger.debug(
                f'[Stage2] All priorities are "None" ({len(evidences)} device(s)) '
                f'— risk_score = 0.0'
            )
            return 0.0

        # ── Step b: Bayesian Product of Likelihood Ratios ──────────────────────
        prior_odds: float = prior / (1.0 - prior)
        posterior_odds: float = prior_odds

        for ev in evidences:
            profile = self._get_profile(ev)
            lr_plus = profile.sensitivity / profile.false_alarm_rate  # always > 0 by construction
            posterior_odds *= lr_plus
            self._logger.debug(
                f'[Stage2]   device={ev.alert_key!r} mfr={ev.manufacturer!r} '
                f'model={ev.model!r} concept={ev.metric_concept!r} '
                f'LR+={lr_plus:.4f} → running_odds={posterior_odds:.6f}'
            )

        posterior_p: float = posterior_odds / (1.0 + posterior_odds)

        # ── Step c: BICEPS-scaled risk score ───────────────────────────────────
        risk_score: float = posterior_p * p_total

        self._logger.debug(
            f'[Stage2] Posterior_P={posterior_p:.4f}, P_total={p_total:.1f}, '
            f'risk_score={risk_score:.4f}, devices={len(evidences)}'
        )
        return risk_score


# ── Facade ────────────────────────────────────────────────────────────────────

class AlarmCoordinator:
    """
    Facade / Orchestrator implementing the IHE-PCD ACM Alarm Coordinator node.
    Single stateless entry point for SmartAlertAggregator.

    Pipeline contract (2026-07-31, adaptive Stage 2):
        1. Stage 1 — HardwareArtifactFilter evaluates `triggering_evidence` against
           its per-device RoC limit.
               False → AlarmDecision(escalate=False, risk_score=-1.0,
                                     suppression_stage='HardwareArtifactFilter')
        2. Stage 2 — AdaptiveAlarmAggregator.tick() drives ONE step of the
           continuous-time stochastic filter that the caller owns per ensemble:
               result = aggregator.tick(sensor_states, dt_step)
           The escalation boundary is internal to the aggregator (Θ(t)); the facade
           does NOT compare against a fixed scalar threshold anymore.
               result.is_escalated == True  → AlarmDecision(escalate=True)
               result.is_escalated == False → AlarmDecision(escalate=False,
                                     suppression_stage='AdaptiveAlarmAggregator')

    UI-facing risk projection
    -------------------------
    The continuous log-odds margin is projected back onto the legacy [0.0, 10.0]
    axis with a calibrated logistic squashing function:

        margin      = logit_sum(t) − Θ(t)
        risk_score  = 10 · σ(margin) = 10 / (1 + e^(−margin))       ∈ [0.0, 10.0]

    margin = 0 (escalation boundary) maps exactly to risk_score = 5.0, preserving
    backward compatibility with dashboards calibrated on the old fixed threshold.

    Statelessness
    -------------
    AlarmCoordinator holds NO per-ensemble mutable state.  All stochastic state
    (sliding windows, persistence timer, patient prior) lives inside the
    AdaptiveAlarmAggregator instance supplied by the caller.  The facade only owns
    the stateless Stage-1 filter.  Thread-safety of the aggregator is the caller's
    responsibility (SmartAlertAggregator serialises tick() per ensemble).
    """

    # Reference midpoint of the UI risk axis.  risk_score == 5.0 ⇔ margin == 0
    # ⇔ logit_sum == Θ(t) ⇔ is_escalated flips.  Kept for dashboard calibration.
    ESCALATION_THRESHOLD: float = 5.0

    # Clamp for the logistic argument to prevent math.exp overflow on extreme margins.
    _MARGIN_CLAMP: float = 60.0

    def __init__(self) -> None:
        self._logger = logging.getLogger('sdc.consumer.alarm_coordinator')
        self._stage1 = HardwareArtifactFilter()
        # Stage 2 is no longer a filter owned here — it is the per-ensemble
        # AdaptiveAlarmAggregator passed into evaluate() by SmartAlertAggregator.

    def _risk_from_margin(self, margin: float) -> float:
        """Project the log-odds margin onto [0.0, 10.0] via 10·σ(margin)."""
        m = max(-self._MARGIN_CLAMP, min(self._MARGIN_CLAMP, margin))
        return 10.0 / (1.0 + math.exp(-m))

    def evaluate(
        self,
        aggregator:          AdaptiveAlarmAggregator,     # per-ensemble stochastic filter (caller-owned)
        triggering_evidence: DeviceAlertEvidence,         # device whose alarm fired
        metric_buffer:       deque,                       # snapshot of that device's metric history
        sensor_states:       dict[str, int],              # {sensor_id → 0|1} for this tick
        dt_step:             float,                        # wall-clock seconds since previous tick
        ensemble_evidences:  list[DeviceAlertEvidence],   # all devices in the patient ensemble
    ) -> AlarmDecision:
        """
        Executes the full two-stage pipeline for one incoming alarm event.

        Stage 1 is a deterministic per-device RoC gate.  Stage 2 advances the
        caller-owned AdaptiveAlarmAggregator by exactly one tick and reads back its
        escalation verdict and stochastic telemetry.

        Logging contract (traceability):
          INFO  "Received Alert for evaluation: alert=..., priority=...,
                 ensemble=..., devices=N"
          INFO  "Stage 1 (HardwareArtifactFilter) finished: alarm suppressed / valid
                 — concept=..."
          DEBUG "Starting Stage 2 (AdaptiveAlarmAggregator) — N sensor(s),
                 dt_step=..., active=..."
          INFO  "Stage 2 result: logit_sum=X.XXX vs Θ(t)=X.XXX (Δt=Xs),
                 Risk=X.XXX, Routing = ESCALATE / SUPPRESS"
        """
        n = len(ensemble_evidences)
        self._logger.info(
            f'Received Alert for evaluation: '
            f'alert={triggering_evidence.alert_key!r}, '
            f'priority={triggering_evidence.biceps_priority!r}, '
            f'concept={triggering_evidence.metric_concept!r}, '
            f'ensemble={triggering_evidence.ensemble_uuid[:8]}, '
            f'devices={n}'
        )

        # ── Stage 1: HardwareArtifactFilter ───────────────────────────────────
        stage1_valid = self._stage1.validate(triggering_evidence, metric_buffer)

        if not stage1_valid:
            self._logger.info(
                f'Stage 1 (HardwareArtifactFilter) finished: alarm suppressed '
                f'— concept={triggering_evidence.metric_concept!r}'
            )
            return AlarmDecision(
                escalate=False,
                risk_score=-1.0,
                contributing_devices=0,
                suppression_stage='HardwareArtifactFilter',
                suppression_reason='RoC exceeded',
            )

        self._logger.info(
            f'Stage 1 (HardwareArtifactFilter) finished: valid '
            f'— concept={triggering_evidence.metric_concept!r}'
        )

        # ── Stage 2: AdaptiveAlarmAggregator (continuous-time stochastic tick) ──
        active_now = sum(1 for v in sensor_states.values() if v)
        self._logger.debug(
            f'Starting Stage 2 (AdaptiveAlarmAggregator) — {len(sensor_states)} sensor(s), '
            f'dt_step={dt_step:.3f}s, active={active_now}'
        )

        result = aggregator.tick(sensor_states, dt_step)
        margin = result.current_logit_sum - result.current_theta
        risk_score = self._risk_from_margin(margin)

        routing = 'ESCALATE' if result.is_escalated else 'SUPPRESS'
        self._logger.info(
            f'Stage 2 result: logit_sum={result.current_logit_sum:.3f} '
            f'vs Θ(t)={result.current_theta:.3f} (Δt={result.active_delta_t:.1f}s), '
            f'Risk={risk_score:.3f}, Routing = {routing}'
        )

        if not result.is_escalated:
            return AlarmDecision(
                escalate=False,
                risk_score=risk_score,
                contributing_devices=n,
                suppression_stage='AdaptiveAlarmAggregator',
                suppression_reason=(
                    f'logit_sum {result.current_logit_sum:.3f} '
                    f'< Θ(t) {result.current_theta:.3f}'
                ),
                logit_sum=result.current_logit_sum,
                theta=result.current_theta,
                delta_t=result.active_delta_t,
            )

        return AlarmDecision(
            escalate=True,
            risk_score=risk_score,
            contributing_devices=n,
            suppression_stage=None,
            suppression_reason=None,
            logit_sum=result.current_logit_sum,
            theta=result.current_theta,
            delta_t=result.active_delta_t,
        )

