"""
adaptive_alarm_aggregator.py — Two-axis adaptive stochastic alarm core.
========================================================================
Per-patient-ensemble orchestrator that fuses the two independent axes of the
IEEE 11073 SDC + HL7 FHIR adaptive alarm model into a single escalation verdict:

    Confidence axis  →  EvidenceAccumulator   E(t) = Σ w_j · s_j(t)
    Urgency axis     →  UrgencyEngine          Θ_target = k_min·w̄ − Context_Log_Odds
    Hysteresis       →  HysteresisFilter       Θ_current(t) (Fast Attack / Slow Release)

    Escalation condition:
        Escalate ⇔ E(t) ≥ Θ_current(t)

Per-tick pipeline (all under ``_adaptive_lock``)
------------------------------------------------
    1. record activations a_j(t) and push a ZOH sample into the FIR buffers;
    2. E(t) = Σ w_j·s_j(t),  w̄ = mean w_j,  a_raw = {a_j(t)}   (EvidenceAccumulator);
    3. Θ_target, k_min, SDC_score  from priorities P_j + a_raw    (UrgencyEngine);
    4. Θ_current = IIR(Θ_target, SDC_score, Δt)                   (HysteresisFilter);
    5. verdict + full telemetry → TickResult.

Thread-safety & lock hierarchy
------------------------------
A per-instance ``_adaptive_lock`` serialises all state mutation; it is DISTINCT
from ``SmartAlertAggregator._adaptive_lock`` (which serialises lifecycle ops).
tick() is pure CPU (no I/O), so it completes in microseconds — well within the
<100 ms real-time budget.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional

from .clinical_context import ClinicalContext
from .evidence_accumulator import EvidenceAccumulator
from .hysteresis_filter import HysteresisFilter
from .math_types import (
    EngineConfig,
    SensorSpec,
    TickResult,
    _clip_prob,    # noqa: F401  (retained public numerical utilities)
    _safe_ln,      # noqa: F401
)
from .urgency_engine import UrgencyEngine


class AdaptiveAlarmAggregator:
    """Two-axis adaptive stochastic alarm aggregator for one patient ensemble.

    Construction:
        specs = {'HR.alert': SensorSpec('HR.alert', 0.99, 0.15, 3), ...}
        agg = AdaptiveAlarmAggregator(specs, EngineConfig(), ClinicalContext(0.005, {}))
        result = agg.tick({'HR.alert': 1}, dt_step=1.0)

    All three collaborators are composed here; the aggregator owns the recursive
    hysteresis state and the FIR buffers via those collaborators.
    """

    def __init__(
        self,
        specs: Optional[Dict[str, SensorSpec]] = None,
        config: Optional[EngineConfig] = None,
        clinical_context: Optional[ClinicalContext] = None,
    ) -> None:
        # Per-instance mutex — serialises tick()/state mutation. Distinct from
        # SmartAlertAggregator._adaptive_lock (which serialises lifecycle ops).
        self._adaptive_lock: threading.Lock = threading.Lock()

        specs = dict(specs or {})
        self._config: EngineConfig = config or EngineConfig()

        # ── Confidence axis ────────────────────────────────────────────────────
        self._evidence = EvidenceAccumulator(self._config.horizon_T, specs)
        # ── Urgency axis ───────────────────────────────────────────────────────
        self._urgency = UrgencyEngine(self._config.alpha)
        # ── Hysteresis kinetics ────────────────────────────────────────────────
        self._hysteresis = HysteresisFilter(self._config.horizon_T)

        # Static SDC priorities P_j per channel (drives v_j and SDC_score).
        self._priorities: Dict[str, int] = {sid: s.priority for sid, s in specs.items()}
        # Last binary activation a_j per channel (foundation state map, BICEPS bool).
        self._sensor_states: Dict[str, bool] = {sid: False for sid in specs}

        # Clinical-context shift Context_Log_Odds.  Always hold a VALID context: if
        # the caller supplies none, fall back to the canonical low-prior baseline
        # (P_0 = 0.005) rather than a neutral 0.0.  A raw 0.0 in log-odds space means
        # O_0 = 1 ⇔ P_0 = 50 %, which would collapse Θ_target by ≈ 5.3 nats and make
        # the filter hypersensitive.  An empty odds_ratios map is mathematically the
        # identity (Π OR = 1 ⇒ Σ ln OR = 0), so it preserves the baseline exactly.
        self._context: ClinicalContext = clinical_context or ClinicalContext(
            base_prob_P0=0.005, odds_ratios={}
        )
        # Baseline shift ln(O_0) — the log-odds when no FHIR danger codes apply.
        self._ctx_log_odds: float = self._context.baseline_log_odds

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def sensor_ids(self) -> list[str]:
        """Registered sensor IDs, ordered by descending SDC priority P_j.

        The most clinically critical channels come first (P_j = 3 → 0); ties are
        broken by ``sensor_id`` for a stable, deterministic order.  This ordering
        is for external consumers only (UI, logs); the per-tick math depends on the
        SET of channels, not their order.
        """
        with self._adaptive_lock:
            return sorted(
                self._priorities.keys(),
                key=lambda sid: (-self._priorities[sid], sid),
            )

    def tick(
        self,
        sensor_states: Dict[str, int],
        dt_step: float,
        *,
        t_now: Optional[float] = None,
    ) -> TickResult:
        """Advance the filter one step and return the escalation verdict + telemetry.

        Args:
            sensor_states: mapping sensor_id → binary a_j (0/1).  Unknown ids are
                           ignored; absent known ids hold their previous state (ZOH).
            dt_step:       wall-clock seconds since the previous tick (≥ 0).
            t_now:         optional explicit timestamp [s] (for deterministic tests);
                           defaults to ``time.monotonic()``.

        Returns:
            TickResult with is_escalated ⇔ E(t) ≥ Θ_current(t), plus the full
            two-axis telemetry (sdc_score, k_min, theta_target, rho_decay).
        """
        t = time.monotonic() if t_now is None else t_now

        with self._adaptive_lock:
            m_size = self._evidence.ensemble_size()
            # Empty ensemble (|M| = 0) → safe, non-escalating default.
            if m_size == 0:
                return TickResult()

            # (1) Record activations (ZOH: absent known sensor holds prior state).
            # a_j follows the BICEPS boolean Presence type across the whole core.
            for sid in self._priorities:
                a = bool(sensor_states.get(sid, self._sensor_states.get(sid, False)))
                self._sensor_states[sid] = a
            activations = dict(self._sensor_states)
            self._evidence.push(activations, t)

            # (2) Confidence axis: E(t), w̄, a_raw.
            e_t = self._evidence.evidence(t)
            w_avg = self._evidence.w_avg()
            a_raw = self._evidence.raw_activations()

            # (3) Urgency axis: Θ_target, k_min, SDC_score.
            theta_target, k_min, sdc = self._urgency.theta_target(
                m_size, self._priorities, a_raw, w_avg, self._ctx_log_odds
            )

            # (4) Hysteresis kinetics: Θ_current(t).
            theta_current = self._hysteresis.step(theta_target, sdc, dt_step)
            rho = self._hysteresis.last_rho

            # (5) Escalation condition: E(t) ≥ Θ_current(t).
            escalate = e_t >= theta_current

            return TickResult(
                is_escalated=escalate,
                current_theta=theta_current,
                evidence=e_t,
                active_delta_t=dt_step,
                sdc_score=sdc,
                k_min=k_min,
                theta_target=theta_target,
                rho_decay=rho,
            )

    def update_specs(self, specs: Dict[str, SensorSpec]) -> None:
        """Adopt a new sensor composition (ensemble rebuild), preserving FIR history.

        Priorities and evidence buffers of known channels are kept; new channels
        are added with zero history; departed channels are dropped.  The recursive
        hysteresis state is reset because the |M|-dependent threshold scale changed.
        """
        specs = dict(specs)
        with self._adaptive_lock:
            self._priorities = {sid: s.priority for sid, s in specs.items()}
            for sid in specs:
                self._sensor_states.setdefault(sid, False)
            for sid in list(self._sensor_states.keys()):
                if sid not in specs:
                    self._sensor_states.pop(sid, None)
            self._evidence.update_specs(specs)
            # |M| changed → Θ scale changed → cold-restart the hysteresis cleanly.
            self._hysteresis.reset()

    def set_clinical_context(self, ctx_log_odds: float) -> None:
        """Set the additive clinical shift Context_Log_Odds used in Θ_target.

        Called by SmartAlertAggregator when FHIR danger codes change.  Replaces the
        decommissioned PatientContext prior heuristic.
        """
        with self._adaptive_lock:
            self._ctx_log_odds = float(ctx_log_odds)

    def update_patient_context(self, *_args, **_kwargs) -> None:
        """Backward-compatible no-op retained for API stability during migration.

        The clinical prior is now injected explicitly via ``set_clinical_context``.
        """
        return None