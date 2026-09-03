"""
evidence_accumulator.py — Confidence axis E(t) of the adaptive alarm filter.
============================================================================
Implements the *evidence accumulation* half of the two-axis model: it turns the
raw binary activation stream a_j(t) ∈ {0, 1} of every VMD channel into a
smoothed, reliability-weighted scalar of accumulated evidence E(t).

Canonical mathematics
---------------------
    FIR smoothing (single-spike suppression), window length T seconds:
        s_j(t) = (1/T) · ∫_{t-T}^{t} a_j(τ) dτ                 ∈ [0, 1]

    Accumulated weighted evidence:
        E(t) = Σ_{j ∈ M} w_j · s_j(t)         with   w_j = ln(TPR_j / FPR_j)

    Mean connected-ensemble hardware weight:
        w̄ = (1/|M|) · Σ_{j ∈ M} w_j

Why a Zero-Order-Hold (ZOH) integral instead of a plain tick average?
--------------------------------------------------------------------
SDC notifications are event-driven and irregular — ticks do NOT arrive on a
fixed 1 Hz grid.  A naïve mean of the last N samples would weight a 0.01 s blip
the same as a 5 s sustained alarm.  We therefore treat each sample (t_i, a_i) as
holding its value a_i until the next sample (Zero-Order-Hold) and integrate that
step function over the trailing window [t-T, t].  For a uniform 1 Hz stream this
reduces *exactly* to the canonical discrete sum (1/T)·Σ a_j(t-k).
"""

from __future__ import annotations

from collections import deque
from typing import Deque, Dict, Tuple

from .math_types import _EPS, SensorSpec


class EvidenceAccumulator:
    """Maintains per-channel ZOH activation buffers and computes E(t), s_j(t), w̄.

    State:
        _T:        FIR / integration horizon T [s] (floored at _EPS).
        _specs:    sensor_id → SensorSpec (carries w_j).
        _buffers:  sensor_id → deque[(timestamp, a_j)] — the ZOH sample history,
                   pruned to just outside the trailing window [t-T, t].
        _last_raw: sensor_id → last observed a_j(t) (for v_j(t) = P_j·a_j(t)).
    """

    def __init__(self, horizon_T: float, specs: Dict[str, SensorSpec]) -> None:
        # Horizon is floored at _EPS so the 1/T normalisation can never divide by 0.
        self._T: float = max(horizon_T, _EPS)
        self._specs: Dict[str, SensorSpec] = dict(specs)
        self._buffers: Dict[str, Deque[Tuple[float, bool]]] = {
            sid: deque() for sid in self._specs
        }
        self._last_raw: Dict[str, bool] = {sid: False for sid in self._specs}

    # ── Membership management ─────────────────────────────────────────────────

    def update_specs(self, specs: Dict[str, SensorSpec]) -> None:
        """Adopt a new sensor set, preserving history of already-known channels.

        New channels get an empty buffer and a_j = False; existing channels keep
        their ZOH history so E(t) does not glitch when a device joins/leaves.
        Channels no longer present are dropped (their evidence must not linger).
        """
        self._specs = dict(specs)
        for sid in self._specs:
            self._buffers.setdefault(sid, deque())
            self._last_raw.setdefault(sid, False)
        # Drop buffers/state for channels that left the ensemble.
        for sid in list(self._buffers.keys()):
            if sid not in self._specs:
                self._buffers.pop(sid, None)
                self._last_raw.pop(sid, None)

    def reset(self) -> None:
        """Flush ALL ZOH history, keeping the current sensor composition.

        Clears every channel's sample buffer and resets its last activation to
        False WITHOUT touching ``_specs`` (so |M| and w_j are preserved).  Used
        when an ensemble fully resolves (TTL cache empty / escalation latch
        cleared): the stale ``True`` samples must not survive a silent gap, or a
        later re-fire would let the ZOH integral back-fill them across the whole
        window and cause a false re-escalation.
        """
        for sid in self._specs:
            self._buffers[sid] = deque()
            self._last_raw[sid] = False

    # ── Ingestion ─────────────────────────────────────────────────────────────

    def push(self, activations: Dict[str, bool], t_now: float) -> None:
        """Append one ZOH sample per channel at time ``t_now`` and prune old ones.

        For every known channel we record (t_now, a_j).  Channels absent from
        ``activations`` inherit their previous a_j (a genuine ZOH — a silent
        channel keeps its last state until a new report arrives).  After
        appending we evict samples that are fully older than the window so the
        deque length stays bounded by the report rate over T seconds.

        The activation values follow the BICEPS ``AlertConditionState.Presence``
        boolean type (IEEE 11073-10207); they are normalised to ``bool`` here so
        the whole confidence-axis chain stays type-consistent.
        """
        window_start = t_now - self._T
        for sid in self._specs:
            # Absent channel → hold previous activation (ZOH), not an implicit False.
            a = bool(activations.get(sid, self._last_raw.get(sid, False)))
            self._last_raw[sid] = a
            buf = self._buffers[sid]
            buf.append((t_now, a))
            # Evict samples fully to the left of the window, but keep the one
            # sample that is still "active" at window_start (needed for the ZOH
            # segment that straddles the window's left edge).
            while len(buf) >= 2 and buf[1][0] <= window_start:
                buf.popleft()

    # ── Confidence-axis outputs ───────────────────────────────────────────────

    def smoothed(self, sensor_id: str, t_now: float) -> float:
        """Return s_j(t) ∈ [0, 1] — the ZOH time-weighted FIR average of a_j.

        s_j(t) = (1/T) · ∫_{t-T}^{t} a_j(τ) dτ.  Each stored sample (t_i, a_i)
        contributes a_i × (segment length ∩ [t-T, t]); the last sample holds
        until t_now.  The result is clamped to [0, 1] to absorb any tiny
        floating-point overshoot at the window boundary.
        """
        buf = self._buffers.get(sensor_id)
        if not buf:
            return 0.0
        window_start = t_now - self._T
        integral = 0.0
        n = len(buf)
        for i in range(n):
            t_i, a_i = buf[i]
            # This ZOH segment runs from t_i until the next sample (or t_now).
            seg_end = buf[i + 1][0] if i + 1 < n else t_now
            seg_end = min(seg_end, t_now)          # never integrate into the future
            seg_start = max(t_i, window_start)      # clip to the trailing window
            if seg_end > seg_start and a_i:
                integral += seg_end - seg_start
        s_j = integral / self._T
        return max(0.0, min(1.0, s_j))

    def evidence(self, t_now: float) -> float:
        """Return E(t) = Σ_{j ∈ M} w_j · s_j(t) — total accumulated evidence [nats]."""
        return sum(
            spec.w_j * self.smoothed(sid, t_now)
            for sid, spec in self._specs.items()
        )

    def w_avg(self) -> float:
        """Return w̄ = (1/|M|) Σ w_j — mean reliability weight of the ensemble.

        Empty ensemble (|M| = 0) → 0.0 (fail-open; no channels ⇒ no scale).
        """
        if not self._specs:
            return 0.0
        return sum(spec.w_j for spec in self._specs.values()) / len(self._specs)

    def raw_activations(self) -> Dict[str, bool]:
        """Return a copy of the latest a_j(t) map — the input to v_j(t) = P_j·a_j(t)."""
        return dict(self._last_raw)

    def ensemble_size(self) -> int:
        """Return |M| — the number of connected VMD channels."""
        return len(self._specs)

