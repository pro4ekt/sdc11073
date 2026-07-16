"""
alarmCoordinator.py — IHE-PCD ACM Alarm Coordinator (Pipeline pattern).

Pipeline:
  Stage 1 ─ HardwareArtifactFilter    (deterministic dx/dt RoC gate; per-device)
  Stage 2 ─ ClinicalRiskFilter         (Bayesian Sensor Fusion; per-ensemble)
  Facade  ─ AlarmCoordinator           (orchestrator; single entry point for SmartAlertAggregator)

Replaces:
  app/signalProcessor.py  (SignalProcessor.validate_alert → HardwareArtifactFilter.validate)
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass  # still needed for DeviceAlertEvidence and AlarmDecision
from typing import Optional

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

    risk_score is the Posterior Probability for the patient ensemble,
    computed as the normalised product of all device Likelihood Ratios,
    scaled by the maximum BICEPS priority weight (range: [0.0, 10.0]).
    -1.0 signals that Stage 2 was not reached (suppressed at Stage 1).
    """
    escalate:            bool          # True → forward to UI/log; False → suppress
    risk_score:          float         # Posterior risk ∈ [0.0, 10.0]; -1.0 if N/A
    contributing_devices: int          # Number of ensemble devices used in Stage 2
    suppression_stage:   Optional[str] # 'HardwareArtifactFilter' | 'ClinicalRiskFilter' | None
    suppression_reason:  Optional[str]


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
    Stage 2 of the alarm pipeline.
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
        prior: float = 0.005,                   # Prior: 0.5% baseline ICU crisis prevalence
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

    Pipeline contract:
        1. Stage 1 evaluates `triggering_evidence` against its per-device RoC limit.
           False → AlarmDecision(escalate=False, risk_score=-1.0,
                                 suppression_stage='HardwareArtifactFilter')
        2. Stage 2 computes ensemble risk_score ∈ [0.0, 10.0]:
               risk_score = Posterior_P × P_total
           risk_score < ESCALATION_THRESHOLD (5.0)
               → AlarmDecision(escalate=False, suppression_stage='ClinicalRiskFilter')
        3. Both gates passed → AlarmDecision(escalate=True, risk_score=<value ≥ 5.0>)

    Risk scale interpretation (with default fail-safe profiles, prior=0.5):
        [0.0,  3.0) — Lo priority: always suppressed without calibrated profiles
        [3.0,  5.0) — Me priority / moderate posterior: below escalation threshold
        [5.0,  6.0) — Hi priority (5.0 = threshold) or Me + high posterior
        [6.0, 10.0] — Hi priority + high posterior: strong clinical signal

    Thread-safety: stateless; mutable state lives only in caller-supplied arguments.
    """

    # Escalation threshold on the BICEPS-scaled risk axis [0.0, 10.0].
    # With fail-safe profiles (LR+=1.0) and prior=0.5:
    #   'Hi'  → risk_score = 5.0 (exactly at threshold → escalates, since check is `<`)
    #   'Me'  → risk_score = 3.0 (below threshold → suppressed without real profiles)
    ESCALATION_THRESHOLD: float = 5.0

    def __init__(self) -> None:
        self._logger = logging.getLogger('sdc.consumer.alarm_coordinator')
        self._stage1 = HardwareArtifactFilter()
        self._stage2 = ClinicalRiskFilter()

    def evaluate(
        self,
        triggering_evidence: DeviceAlertEvidence,        # device whose alarm fired
        metric_buffer:       deque,                      # snapshot of that device's metric history
        ensemble_evidences:  list[DeviceAlertEvidence],  # all devices in the patient ensemble
    ) -> AlarmDecision:
        """
        Executes the full two-stage pipeline for one incoming alarm event.

        Logging contract (traceability):
          INFO  "Received Alert for evaluation: alert=..., priority=...,
                 ensemble=..., devices=N"
          INFO  "Stage 1 (HardwareArtifactFilter) finished: alarm suppressed / valid
                 — concept=..."
          DEBUG "Starting Stage 2 (ClinicalRiskFilter) — N device(s) in ensemble,
                 P_total candidate = X"
          INFO  "Stage 2 result: Risk Score = X.XXX (Posterior=X.XXXX × P_total=X.X),
                 threshold = 5.0, Routing = ESCALATE / SUPPRESS"
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

        # ── Stage 2: ClinicalRiskFilter (Bayesian Sensor Fusion) ──────────────
        p_total_candidate = max(
            (self._stage2._PRIORITY_WEIGHTS.get(e.biceps_priority, 0.0) for e in ensemble_evidences),
            default=0.0,
        )
        self._logger.debug(
            f'Starting Stage 2 (ClinicalRiskFilter) — {n} device(s) in ensemble, '
            f'P_total candidate = {p_total_candidate:.1f}'
        )

        risk_score = self._stage2.compute_risk(ensemble_evidences)

        # Derive Posterior_P and P_total for the traceability log line
        posterior_p = risk_score / p_total_candidate if p_total_candidate > 0.0 else 0.0

        if risk_score < self.ESCALATION_THRESHOLD:
            routing = 'SUPPRESS'
            self._logger.info(
                f'Stage 2 result: Risk Score = {risk_score:.3f} '
                f'(Posterior={posterior_p:.4f} × P_total={p_total_candidate:.1f}), '
                f'threshold = {self.ESCALATION_THRESHOLD:.1f}, Routing = {routing}'
            )
            return AlarmDecision(
                escalate=False,
                risk_score=risk_score,
                contributing_devices=n,
                suppression_stage='ClinicalRiskFilter',
                suppression_reason=(
                    f'risk_score {risk_score:.3f} < threshold {self.ESCALATION_THRESHOLD:.1f}'
                ),
            )

        routing = 'ESCALATE'
        self._logger.info(
            f'Stage 2 result: Risk Score = {risk_score:.3f} '
            f'(Posterior={posterior_p:.4f} × P_total={p_total_candidate:.1f}), '
            f'threshold = {self.ESCALATION_THRESHOLD:.1f}, Routing = {routing}'
        )
        return AlarmDecision(
            escalate=True,
            risk_score=risk_score,
            contributing_devices=n,
            suppression_stage=None,
            suppression_reason=None,
        )

