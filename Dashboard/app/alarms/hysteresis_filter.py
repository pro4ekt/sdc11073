"""
hysteresis_filter.py — Asymmetric IIR hysteresis kinetics Θ_current(t).
=======================================================================
Smooths the raw target threshold Θ_target(t) into the *applied* threshold
Θ_current(t) with deliberately asymmetric dynamics:

  * **Fast Attack**  — when the situation worsens (Θ_target drops, i.e. it becomes
    easier to escalate), the threshold snaps down *immediately*.  Patient safety
    must never be delayed by a filter time-constant.
  * **Slow Release** — when the situation improves (Θ_target rises), the threshold
    relaxes back only gradually, exponentially, so a brief dip in severity does
    not prematurely desensitise the alarm (anti-flapping / alarm-fatigue guard).

Canonical mathematics
---------------------
    Severity-coupled relaxation rate (bound to the unified horizon T):
        ρ(t) = (1 - SDC_score(t)) / T          [s⁻¹]

    Recursive applied threshold:
        Θ_current(t) =
            Θ_target(t),                                             if Θ_target ≤ Θ_current(t-1)   (Fast Attack)
            Θ_target(t) + (Θ_current(t-1) - Θ_target(t))·e^(-ρ·Δt),  if Θ_target >  Θ_current(t-1)   (Slow Release)

Note on ρ: high severity (SDC_score → 1) ⇒ ρ → 0 ⇒ release almost frozen (stay
sensitive); low severity (SDC_score → 0) ⇒ ρ = 1/T ⇒ threshold relaxes over ~T.
"""

from __future__ import annotations

import math
from typing import Optional

from .math_types import _EPS


class HysteresisFilter:
    """Stateful asymmetric IIR filter mapping Θ_target(t) → Θ_current(t).

    State:
        _T:          unified time horizon T [s] (floored at _EPS).
        _last_theta: Θ_current(t-1); ``None`` marks a cold start (no history).
        _last_rho:   most recent ρ(t) — exported as telemetry.
    """

    def __init__(self, horizon_T: float) -> None:
        # T is floored at _EPS so ρ = (1 - SDC)/T can never divide by zero.
        self._T: float = max(horizon_T, _EPS)
        self._last_theta: Optional[float] = None
        self._last_rho: float = 0.0

    def step(self, theta_target: float, sdc_score: float, dt: float) -> float:
        """Advance the hysteresis by one tick and return Θ_current(t).

        Args:
            theta_target: raw target threshold Θ_target(t) from the UrgencyEngine.
            sdc_score:    SDC_score(t) ∈ [0, 1] — drives ρ(t) = (1 - SDC)/T.
            dt:           wall-clock seconds Δt since the previous tick.

        Behaviour:
            * Cold start (no previous Θ) → Θ_current = Θ_target (no false attack).
            * Fast Attack (target ≤ prev) → snap to target instantly.
            * Slow Release (target > prev) → exponential relaxation toward target.
        """
        # ρ(t) = (1 - SDC_score)/T — recorded for telemetry regardless of branch.
        rho = (1.0 - max(0.0, min(1.0, sdc_score))) / self._T
        self._last_rho = rho

        if self._last_theta is None:
            # Cold start: initialise Θ_current(0) = Θ_target(0).
            theta_current = theta_target
        elif theta_target <= self._last_theta:
            # Fast Attack: worsening situation → immediate, un-damped descent.
            theta_current = theta_target
        else:
            # Slow Release: improving situation → exponential decay of the gap.
            # exp argument is ≤ 0 (ρ, dt ≥ 0) ⇒ decay ∈ (0, 1], no overflow.
            decay = math.exp(-rho * max(0.0, dt))
            theta_current = theta_target + (self._last_theta - theta_target) * decay

        self._last_theta = theta_current
        return theta_current

    def reset(self) -> None:
        """Clear the recursive state (return to cold start).

        Called when the ensemble is rebuilt so a new composition of channels does
        not inherit a stale Θ from a different sensor set.
        """
        self._last_theta = None
        self._last_rho = 0.0

    @property
    def current(self) -> Optional[float]:
        """Return Θ_current(t-1), or ``None`` before the first ``step()``."""
        return self._last_theta

    @property
    def last_rho(self) -> float:
        """Return the most recent ρ(t) — exported to TickResult.rho_decay."""
        return self._last_rho

