"""
math_types.py — Shared types & numerical primitives for the adaptive alarm core.
================================================================================
Foundation layer for the two-axis adaptive stochastic alarm filter (IEEE 11073
SDC + HL7 FHIR).  This module deliberately holds NO business logic — only:

  * numerical-safety constants and helpers (``_clip_prob`` / ``_safe_ln``),
  * the immutable per-sensor calibration record ``SensorSpec`` (carries w_j, P_j),
  * the global tuning record ``EngineConfig`` (single time-horizon T, weight α),
  * the immutable output ticket ``TickResult`` (verdict + full dissertation-grade
    telemetry).

Clinical vocabulary used throughout the core
--------------------------------------------
  * **VMD**   — Virtual Medical Device: one physiological alarm channel (sensor).
  * **TPR/FPR** — True/False Positive Rate of a VMD channel (from the DataSheet).
  * **P_j**   — SDC static alert priority ∈ {0=None, 1=Lo, 2=Me, 3=Hi}.
  * **a_j(t)** — binary activation of channel j at time t (alarm ON = 1).
  * **ZOH**   — Zero-Order-Hold: a sample holds its value until the next sample
                (used for time-weighted FIR integration under irregular sampling).
  * **SPOF**  — Single Point Of Failure: a lone noisy channel; the consensus
                threshold k_min defends against escalating on one SPOF.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

# ── Numerical-Safety Constants ────────────────────────────────────────────────
# A single global epsilon guards every logarithm and division in the core.  Any
# probability is squeezed into [_EPS, 1-_EPS] so that neither ln(0) (→ -inf) nor
# a divide-by-zero (FPR = 0) can ever raise a math-domain error at runtime.
_EPS: Final[float] = 1e-5
_PROB_MAX: Final[float] = 1.0 - _EPS


def _clip_prob(p: float) -> float:
    """Clamp a probability into the open interval [1e-5, 1 - 1e-5].

    Rationale (fail-open numerical safety):
        * FPR = 0  → w_j = ln(TPR / FPR) would divide by zero;
        * TPR = 0  → w_j = ln(0)         would be -inf;
        * p = 1    → 1 / (1 - p) logit transforms would blow up.
    Squeezing p away from {0, 1} keeps every downstream logit finite.
    """
    return max(_EPS, min(_PROB_MAX, p))


def _safe_ln(x: float) -> float:
    """Compute ln(x) with a hard floor of 1e-5 on the argument.

    Guarantees a finite result for x ≤ 0 (returns ln(1e-5) ≈ -11.51) instead of
    raising ``ValueError: math domain error``.  Used for ln(O_0), ln(OR_d) and
    ln(TPR/FPR).
    """
    return math.log(max(_EPS, x))


# ── Per-Sensor Calibration Record ─────────────────────────────────────────────

"""Immutable calibration of a single VMD alarm channel.

    One ``SensorSpec`` is built per MDIB ``AlertCondition`` (``sensor_id`` = the
    BICEPS alert handle).  ``tpr``/``fpr`` come from the device DataSheet
    (``DeviceReliabilityProfile``); ``priority`` is the SDC static priority P_j,
    already decoded from its on-the-wire BICEPS string into an integer (see the
    note on ``priority`` below).

    SDC-string ⇒ int boundary (Separation of Concerns):
        BICEPS transmits ``AlertCondition.Priority`` as a STRING
        ("Hi"/"Me"/"Lo"/"None").  That string is mapped to the integer P_j
        exactly once, at the SDC↔core boundary, by
        ``DeviceProfileRepository.get_priority()`` (via the ``priority_map`` in
        ``clinical_db.json``), invoked from
        ``SmartAlertAggregator._build_specs_from_evidences()``.  By the time a
        ``SensorSpec`` exists, the value is a pure ``int`` — the math core never
        sees, parses, or knows about SDC priority strings.

    Attributes:
        sensor_id: MDIB AlertCondition handle — the channel's stable identity.
        tpr:       True-Positive Rate  TPR_j = P(alarm | true event).
        fpr:       False-Positive Rate FPR_j = P(alarm | no event).
        priority:  Static SDC priority P_j ∈ {0=None, 1=Lo, 2=Me, 3=Hi}.
                   Pre-mapped from the BICEPS priority STRING via ``priority_map``;
                   see the SoC note above.
    """

@dataclass(frozen=True)
class SensorSpec:
    """Immutable calibration of a single VMD alarm channel.

    One ``SensorSpec`` is built per MDIB ``AlertCondition`` (``sensor_id`` = the
    BICEPS alert handle).  ``tpr``/``fpr`` come from the device DataSheet
    (``DeviceReliabilityProfile``); ``priority`` is the SDC static priority P_j.

    Attributes:
        sensor_id: MDIB AlertCondition handle — the channel's stable identity.
        tpr:       True-Positive Rate  TPR_j = P(alarm | true event).
        fpr:       False-Positive Rate FPR_j = P(alarm | no event).
        priority:  Static SDC priority P_j ∈ {0=None, 1=Lo, 2=Me, 3=Hi}.
    """

    sensor_id: str
    tpr: float
    fpr: float
    # P_j ∈ {0,1,2,3}. NOT the raw BICEPS string: "Hi"/"Me"/"Lo"/"None" is decoded
    # to this int at the SDC↔core boundary by DeviceProfileRepository.get_priority()
    # (priority_map in clinical_db.json). The math core is string-agnostic (SoC).
    priority: int

    @property
    def w_j(self) -> float:
        """Static VMD reliability weight (log-likelihood-ratio of a positive test).

        Formula:
            w_j = ln(LR+_j) = ln(TPR_j / FPR_j)

        Clinical meaning: how many *nats* of evidence one activation of this
        channel contributes.  A highly specific sensor (low FPR) earns a large
        positive weight; a channel no better than a coin (TPR = FPR) earns 0; a
        channel worse than chance (TPR < FPR) earns a negative weight.

        Numerical safety: both rates pass through ``_clip_prob`` so FPR = 0 and
        TPR = 0 can never cause a divide-by-zero or ln(0).
        """
        return _safe_ln(_clip_prob(self.tpr) / _clip_prob(self.fpr))


# ── Global Engine Configuration ───────────────────────────────────────────────

@dataclass(frozen=True)
class EngineConfig:
    """Global tuning parameters shared by both axes of the filter.

    Attributes:
        horizon_T: Single unified time horizon T [s].  It governs BOTH the FIR
                   smoothing window s_j(t) (Confidence axis) AND the hysteresis
                   relaxation rate ρ(t) = (1 - SDC)/T (Urgency axis).  Coupling
                   them through one constant keeps the two axes time-consistent.
        alpha:     Blend weight α ∈ [0, 1] in SDC_score(t).  α = 0.7 (default)
                   biases the severity index toward the single most-urgent active
                   channel (peak threat, max v_j) over the diffuse ensemble sum.
    """

    horizon_T: float = 10.0
    alpha: float = 0.7


# ── Output Ticket ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TickResult:
    """Immutable verdict + telemetry produced by ``AdaptiveAlarmAggregator.tick()``.

    Core decision:
        is_escalated:      True ⇔ E(t) ≥ Θ_current(t)  (escalation condition).
        current_theta:     Θ_current(t) — the hysteresis-smoothed applied boundary.
        evidence:          E(t) = Σ w_j · s_j(t) — accumulated weighted evidence [nats].
        active_delta_t:    Δt [s] used by the hysteresis kinetics on this tick.

    Extended telemetry (exported for dissertation analysis / dashboards):
        sdc_score:   SDC_score(t) ∈ [0, 1] — normalised ensemble severity index.
        k_min:       k_min(t) ∈ [2, |M|] — dynamic VMD consensus quorum.
        theta_target: Θ_target(t) — raw target threshold *before* IIR smoothing.
        rho_decay:   ρ(t) = (1 - SDC_score)/T [s⁻¹] — hysteresis relaxation rate.
    """

    is_escalated: bool = False
    current_theta: float = 0.0
    evidence: float = 0.0
    active_delta_t: float = 0.0
    # ── Extended two-axis telemetry ───────────────────────────────────────────
    sdc_score: float = 0.0
    k_min: int = 0
    theta_target: float = 0.0
    rho_decay: float = 0.0

