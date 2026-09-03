"""
urgency_engine.py — Urgency axis Θ_target(t) with dynamic VMD consensus.
========================================================================
Implements the *urgency / severity* half of the two-axis model.  It converts the
static SDC priorities P_j and the live activations a_j(t) into a normalised
severity index SDC_score(t), derives a self-consistent consensus quorum k_min(t),
and finally produces the raw target escalation threshold Θ_target(t).

Canonical mathematics
---------------------
    Instantaneous per-channel threat (priority-gated activation):
        v_j(t) = P_j · a_j(t)                    P_j ∈ {0,1,2,3},  a_j ∈ {0,1}

    Ensemble priority capacity:
        P_max,M = max_j P_j        P_sum,M = Σ_j P_j

    Normalised severity index (α biases toward the single peak threat):
        SDC_score(t) = α · (max_j v_j / P_max,M) + (1-α) · (Σ_j v_j / P_sum,M)  ∈ [0,1]

    Self-consistent dynamic consensus quorum, strictly in [2, |M|]:
        k_min(t) = ⌊ |M| − (|M| − 2) · SDC_score(t) ⌋

    Target escalation threshold (raw, pre-hysteresis):
        Θ_target(t) = k_min(t) · w̄ − Context_Log_Odds

Clinical intuition
------------------
When severity is low, k_min → |M|: we demand near-unanimous agreement across
channels before escalating (defends against a single noisy SPOF).  When severity
spikes (a Hi-priority channel fires), k_min → 2: two corroborating channels are
enough — the system becomes fast and sensitive exactly when it matters.
"""

from __future__ import annotations

import math
from typing import Mapping, NamedTuple


class UrgencyResult(NamedTuple):
    """Structured result of one ``UrgencyEngine.theta_target`` evaluation.

    A ``NamedTuple`` so callers may still unpack it positionally
    ``theta, k, score = engine.theta_target(...)`` while gaining named,
    self-documenting field access (``result.theta_target`` etc.).

    Fields (positional order preserved for backward-compatible unpacking):
        theta_target: Θ_target(t) = k_min·w̄ − Context_Log_Odds (raw, pre-hysteresis).
        k_min:        k_min(t) ∈ [2, |M|] — dynamic VMD consensus quorum.
        sdc_score:    SDC_score(t) ∈ [0, 1] — normalised ensemble severity index.
    """

    theta_target: float
    k_min: int
    sdc_score: float


class UrgencyEngine:
    """Stateless computer of SDC_score(t), k_min(t) and Θ_target(t).

    State (immutable after construction):
        _alpha: blend weight α ∈ [0, 1] between peak-threat and diffuse-sum terms.
    """

    def __init__(self, alpha: float) -> None:
        # α is clamped to [0, 1]; α = 0.7 (default) favours the single peak threat.
        self._alpha: float = min(1.0, max(0.0, alpha))

    # ── Severity index ────────────────────────────────────────────────────────

    def sdc_score(
        self,
        priorities: Mapping[str, int],
        activations: Mapping[str, bool],
    ) -> float:
        """Return SDC_score(t) ∈ [0, 1] — normalised ensemble severity.

        SDC_score = α·(max v_j / P_max) + (1-α)·(Σ v_j / P_sum),  v_j = P_j·a_j.

        Edge cases (fail-safe divisions):
            * P_max = 0 (every channel priority None) → peak term = 0.
            * P_sum = 0                                → diffuse term = 0.
            Both zero ⇒ SDC_score = 0 (an all-``None`` ensemble is never urgent).
        """
        if not priorities:
            return 0.0

        # v_j(t) = P_j · a_j(t): threat contributed by each channel right now.
        # a_j is a BICEPS boolean Presence; cast to the int {0,1} it multiplies so
        # v_j stays a numeric threat magnitude (P_j when active, 0 when silent).
        v = {
            sid: p * (1 if activations.get(sid, False) else 0)
            for sid, p in priorities.items()
        }

        p_max = max(priorities.values())
        p_sum = sum(priorities.values())
        max_v = max(v.values())
        sum_v = sum(v.values())

        # Guarded divisions — an all-None ensemble contributes 0, never NaN.
        peak_term = (max_v / p_max) if p_max > 0 else 0.0
        diffuse_term = (sum_v / p_sum) if p_sum > 0 else 0.0

        score = self._alpha * peak_term + (1.0 - self._alpha) * diffuse_term
        return max(0.0, min(1.0, score))

    # ── Consensus quorum ──────────────────────────────────────────────────────

    def k_min(self, m_size: int, sdc_score: float) -> int:
        """Return the dynamic consensus quorum k_min(t), strictly clamped to [2, |M|].

        k_min = clamp( ⌊ |M| − (|M| − 2)·SDC_score ⌋,  2,  |M| ).

        * SDC_score = 0 → k_min = |M| (near-unanimous consensus required).
        * SDC_score = 1 → k_min = 2   (two corroborating channels suffice).

        Topological fail-safe (Reference Architecture §5): the lower bound is a
        HARD 2 — consensus from a single VMD is forbidden for stationary ICU
        monitoring, so a lone sensor can never trigger a central escalation in
        isolation.  If |M| < 2 the floor still pins k_min to 2, which exceeds the
        ensemble size and is therefore unreachable → the single channel is held
        back (SUPPRESS), exactly as intended.
        """
        if m_size <= 0:
            return 0
        raw = math.floor(m_size - (m_size - 2) * sdc_score)
        # HARD lower bound of 2 (never min(2, |M|)): forbids single-VMD consensus.
        return max(2, min(m_size, raw))

    # ── Target threshold ──────────────────────────────────────────────────────

    def theta_target(
        self,
        m_size: int,
        priorities: Mapping[str, int],
        activations: Mapping[str, bool],
        w_avg: float,
        context_log_odds: float,
    ) -> UrgencyResult:
        """Return an ``UrgencyResult`` (Θ_target, k_min, SDC_score) for this tick.

        Θ_target(t) = k_min(t) · w̄ − Context_Log_Odds.

        SDC_score is returned alongside because the HysteresisFilter needs it to
        set the relaxation rate ρ(t) = (1 - SDC_score)/T.  The result is a
        ``NamedTuple``: callers may unpack it positionally
        ``theta, k, score = engine.theta_target(...)`` or read named fields.
        """
        score = self.sdc_score(priorities, activations)
        k = self.k_min(m_size, score)
        theta = k * w_avg - context_log_odds
        return UrgencyResult(theta_target=theta, k_min=k, sdc_score=score)

    # ── Time-in-Alarm decay multiplier ────────────────────────────────────────

    @staticmethod
    def decay_multiplier(
        sdc_score: float,
        active_alarm_duration: float,
        horizon_T: float,
    ) -> float:
        """Return the Time-in-Alarm decay multiplier δ(t) ∈ (0, 1].

        Rationale (SPOF fail-safe):
            A persistent alarm that cannot break the hard topological quorum
            (k_min ≥ 2) — e.g. a single continuously-firing channel — would never
            escalate under the static threshold.  To honour patient safety, the
            target threshold is *exponentially eroded* the longer evidence persists
            without escalation, so a genuinely sustained condition eventually
            crosses the barrier.

        Canonical mathematics:
            λ(t) = 1/T − ρ(t) = SDC_score(t) / T        (decay rate, symmetric to ρ)
            δ(t) = exp( −λ(t) · max(0, τ − T) )         (τ = active_alarm_duration)

        Properties:
            * A grace period of one horizon T: while τ ≤ T, ``max(0, τ−T) = 0`` ⇒
              δ = 1.0 (no erosion) — short/transient alarms are unaffected.
            * SDC_score = 0 ⇒ λ = 0 ⇒ δ = 1.0 — no erosion without any threat.
            * Higher severity ⇒ larger λ ⇒ faster erosion once past the grace period.
            * δ is applied as ``Θ_target · δ`` BEFORE the (untouched) hysteresis.

        This method is intentionally STATELESS: the duration τ is owned and tracked
        by the per-ensemble ``AdaptiveAlarmAggregator``; the engine only maps the
        inputs to the multiplier.
        """
        effective_t = max(horizon_T, 1e-6)
        # λ(t) = SDC/T = 1/T − ρ(t); clamp SDC into [0,1] for numerical safety.
        lambda_rate = max(0.0, min(1.0, sdc_score)) / effective_t
        time_over_grace = max(0.0, active_alarm_duration - effective_t)
        return math.exp(-lambda_rate * time_over_grace)

