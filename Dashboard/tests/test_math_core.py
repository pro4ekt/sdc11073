"""
tests/test_math_core.py
=======================
Isolated unit-test suite for the two-axis adaptive stochastic alarm core
(``Dashboard/app/alarms/``).  Verifies every canonical formula of the model:

    Confidence axis   E(t) = Σ w_j·s_j(t),   w_j = ln(TPR/FPR)   (FIR / ZOH)
    Clinical context  Context_Log_Odds = ln(O_0) + Σ R_d·ln(OR_d)
    Urgency axis      SDC_score(t), k_min(t), Θ_target(t)
    Hysteresis        Θ_current(t)  (Fast Attack / Slow Release, ρ = (1-SDC)/T)
    Escalation        E(t) ≥ Θ_current(t)

Grouped A–I per the approved design report.  Runs standalone (no Qt/SOAP stack)
via the same isolated-module bootstrap used by test_alarm_pipeline.py.

Run with:  pytest Dashboard/tests/test_math_core.py -v
"""

from __future__ import annotations

import importlib.util
import math
import sys
import time
import types
import unittest
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════════════
#  Isolated module bootstrap — load the pure-math modules without executing the
#  heavy Dashboard.app.__init__ (Qt, SOAP, WSDiscovery …).  See test_alarm_pipeline.
# ══════════════════════════════════════════════════════════════════════════════

_PROJECT_ROOT = Path(__file__).resolve().parents[2]          # Master/
_ALARMS_DIR = _PROJECT_ROOT / 'Dashboard' / 'app' / 'alarms'


def _stub_package(dotted_name: str, fs_path: Path) -> types.ModuleType:
    """Register an empty package in sys.modules without running its __init__.py."""
    if dotted_name in sys.modules:
        return sys.modules[dotted_name]
    pkg = types.ModuleType(dotted_name)
    pkg.__path__ = [str(fs_path)]
    pkg.__package__ = dotted_name
    pkg.__file__ = str(fs_path / '__init__.py')
    pkg.__spec__ = None
    sys.modules[dotted_name] = pkg
    return pkg


def _load_module(dotted_name: str, file_path: Path, package: str) -> types.ModuleType:
    """Load one .py file as a named module with an explicit __package__."""
    if dotted_name in sys.modules:
        return sys.modules[dotted_name]
    spec = importlib.util.spec_from_file_location(dotted_name, file_path)
    module = importlib.util.module_from_spec(spec)          # type: ignore[arg-type]
    module.__package__ = package
    sys.modules[dotted_name] = module
    spec.loader.exec_module(module)                          # type: ignore[union-attr]
    return module


# Step 1 — stub packages (their __init__.py are NOT executed).
_stub_package('Dashboard', _PROJECT_ROOT / 'Dashboard')
_stub_package('Dashboard.app', _PROJECT_ROOT / 'Dashboard' / 'app')
_stub_package('Dashboard.app.alarms', _ALARMS_DIR)

# Step 2 — load in dependency order (relative imports resolve via the stubs).
for _name in (
    'math_types',
    'evidence_accumulator',
    'clinical_context',
    'urgency_engine',
    'hysteresis_filter',
    'device_profile_repo',
    'adaptive_alarm_aggregator',
):
    _load_module(f'Dashboard.app.alarms.{_name}', _ALARMS_DIR / f'{_name}.py', 'Dashboard.app.alarms')

# Step 3 — ordinary imports (served from the sys.modules cache, no extra I/O).
from Dashboard.app.alarms.math_types import (  # noqa: E402
    EngineConfig,
    SensorSpec,
    TickResult,
    _clip_prob,
    _safe_ln,
)
from Dashboard.app.alarms.evidence_accumulator import EvidenceAccumulator  # noqa: E402
from Dashboard.app.alarms.clinical_context import ClinicalContext  # noqa: E402
from Dashboard.app.alarms.urgency_engine import UrgencyEngine  # noqa: E402
from Dashboard.app.alarms.hysteresis_filter import HysteresisFilter  # noqa: E402
from Dashboard.app.alarms.device_profile_repo import DeviceProfileRepository  # noqa: E402
from Dashboard.app.alarms.adaptive_alarm_aggregator import AdaptiveAlarmAggregator  # noqa: E402


# ══════════════════════════════════════════════════════════════════════════════
#  Group A — math_types / SensorSpec.w_j  (w_j = ln(TPR/FPR))
# ══════════════════════════════════════════════════════════════════════════════

class TestGroupA_SensorWeights(unittest.TestCase):
    def test_A1_reference_weight(self):
        """A1: w_j = ln(0.99/0.15) = ln(6.6) ≈ 1.8871."""
        spec = SensorSpec('HR', tpr=0.99, fpr=0.15, priority=3)
        self.assertAlmostEqual(spec.w_j, math.log(0.99 / 0.15), places=9)
        self.assertAlmostEqual(spec.w_j, 1.887069649, places=6)

    def test_A2_fpr_zero_is_finite(self):
        """A2: FPR = 0 → clipped to 1e-5, w_j finite (no div-by-zero)."""
        spec = SensorSpec('X', tpr=0.99, fpr=0.0, priority=1)
        self.assertTrue(math.isfinite(spec.w_j))
        self.assertAlmostEqual(spec.w_j, math.log(0.99 / 1e-5), places=6)

    def test_A3_tpr_zero_is_finite(self):
        """A3: TPR = 0 → clipped to 1e-5, no ln(0) domain error."""
        spec = SensorSpec('X', tpr=0.0, fpr=0.5, priority=1)
        self.assertTrue(math.isfinite(spec.w_j))
        self.assertLess(spec.w_j, 0.0)

    def test_A4_worse_than_chance_negative(self):
        """A4: TPR < FPR → w_j < 0 (channel worse than a coin flip)."""
        spec = SensorSpec('X', tpr=0.1, fpr=0.5, priority=1)
        self.assertLess(spec.w_j, 0.0)
        self.assertAlmostEqual(spec.w_j, math.log(0.2), places=9)

    def test_A5_clip_helpers(self):
        """A5: _clip_prob squeezes into [1e-5, 1-1e-5]; _safe_ln floors at 1e-5."""
        self.assertEqual(_clip_prob(0.0), 1e-5)
        self.assertEqual(_clip_prob(1.0), 1.0 - 1e-5)
        self.assertEqual(_clip_prob(0.3), 0.3)
        self.assertAlmostEqual(_safe_ln(0.0), math.log(1e-5), places=9)


# ══════════════════════════════════════════════════════════════════════════════
#  Group B — EvidenceAccumulator (FIR window, ZOH integral, E(t), w̄)
# ══════════════════════════════════════════════════════════════════════════════

class TestGroupB_EvidenceAccumulator(unittest.TestCase):
    def _one_sensor(self, T=4.0, tpr=0.99, fpr=0.15, prio=3):
        specs = {'s': SensorSpec('s', tpr, fpr, prio)}
        return EvidenceAccumulator(T, specs)

    def test_B1_uniform_ramp_to_one(self):
        """B1: constant activation ramps s_j from 0 → 1 over the horizon T."""
        acc = self._one_sensor(T=4.0)
        expected = [0.0, 0.25, 0.5, 0.75, 1.0]
        for t in range(5):
            acc.push({'s': 1}, float(t))
            self.assertAlmostEqual(acc.smoothed('s', float(t)), expected[t], places=9)

    def test_B2_eviction_beyond_window(self):
        """B2: samples older than T fall out; a lone old spike decays to 0."""
        acc = self._one_sensor(T=4.0)
        acc.push({'s': 1}, 0.0)                 # single 1-spike at t=0
        acc.push({'s': 0}, 1.0)                 # off thereafter
        # By t=6 the ON segment [0,1] is fully outside the [2,6] window.
        for t in (2.0, 3.0, 4.0, 5.0, 6.0):
            acc.push({'s': 0}, t)
        self.assertAlmostEqual(acc.smoothed('s', 6.0), 0.0, places=9)

    def test_B3_zoh_irregular_sampling(self):
        """B3: 2 s of ON in a 10 s window → s_j = 0.2 regardless of sample spacing."""
        acc = self._one_sensor(T=10.0)
        acc.push({'s': 1}, 0.0)                 # ON at t=0
        acc.push({'s': 0}, 2.0)                 # OFF at t=2  → 2 s of ON held by ZOH
        acc.push({'s': 0}, 5.0)                 # irregular later sample
        self.assertAlmostEqual(acc.smoothed('s', 5.0), 0.2, places=9)

    def test_B4_clamped_unit_interval(self):
        """B4: s_j never leaves [0, 1]."""
        acc = self._one_sensor(T=2.0)
        for t in range(10):
            acc.push({'s': 1}, float(t))
            s = acc.smoothed('s', float(t))
            self.assertGreaterEqual(s, 0.0)
            self.assertLessEqual(s, 1.0)

    def test_B5_evidence_is_weighted_sum(self):
        """B5: E(t) = Σ w_j·s_j(t) across channels."""
        specs = {
            'a': SensorSpec('a', 0.99, 0.15, 3),   # w ≈ 1.887
            'b': SensorSpec('b', 0.90, 0.30, 2),   # w = ln(3) ≈ 1.0986
        }
        acc = EvidenceAccumulator(4.0, specs)
        for t in range(5):
            acc.push({'a': 1, 'b': 1}, float(t))   # both saturate to s=1 at t=4
        e = acc.evidence(4.0)
        self.assertAlmostEqual(e, specs['a'].w_j + specs['b'].w_j, places=9)

    def test_B6_w_avg_and_update_specs(self):
        """B6: w̄ = mean w_j; update_specs preserves history of known channels."""
        specs = {
            'a': SensorSpec('a', 0.99, 0.15, 3),
            'b': SensorSpec('b', 0.90, 0.30, 2),
        }
        acc = EvidenceAccumulator(4.0, specs)
        self.assertAlmostEqual(acc.w_avg(), (specs['a'].w_j + specs['b'].w_j) / 2, places=9)
        for t in range(5):
            acc.push({'a': 1, 'b': 1}, float(t))
        # Add a third channel; 'a' keeps its saturated history.
        specs['c'] = SensorSpec('c', 0.80, 0.20, 1)
        acc.update_specs(specs)
        self.assertEqual(acc.ensemble_size(), 3)
        self.assertAlmostEqual(acc.smoothed('a', 4.0), 1.0, places=9)   # history kept
        self.assertAlmostEqual(acc.smoothed('c', 4.0), 0.0, places=9)   # fresh channel

    def test_B7_empty_ensemble(self):
        """B7: |M| = 0 → E = 0, w̄ = 0."""
        acc = EvidenceAccumulator(10.0, {})
        self.assertEqual(acc.ensemble_size(), 0)
        self.assertEqual(acc.evidence(1.0), 0.0)
        self.assertEqual(acc.w_avg(), 0.0)


# ══════════════════════════════════════════════════════════════════════════════
#  Group C — ClinicalContext (Context_Log_Odds)
# ══════════════════════════════════════════════════════════════════════════════

class TestGroupC_ClinicalContext(unittest.TestCase):
    def setUp(self):
        # ClinicalContext takes the baseline crisis PROBABILITY P_0 and converts
        # it internally to baseline odds O_0 = P_0 / (1 - P_0).  The baseline
        # log-odds is therefore ln(P_0 / (1 - P_0)), not ln(P_0).
        self.ctx = ClinicalContext(base_prob_P0=0.005, odds_ratios={'D1': 2.5, 'D2': 4.0})
        self._ln_o0 = math.log(0.005 / (1.0 - 0.005))   # ln(O_0) for P_0 = 0.005

    def test_C1_empty_is_baseline(self):
        """C1: no danger codes → Context_Log_Odds = ln(O_0) = ln(P_0/(1-P_0))."""
        self.assertAlmostEqual(self.ctx.log_odds([]), self._ln_o0, places=9)

    def test_C2_single_code(self):
        """C2: ln(O_0) + ln(OR_D1)."""
        self.assertAlmostEqual(
            self.ctx.log_odds(['D1']), self._ln_o0 + math.log(2.5), places=9
        )

    def test_C3_unknown_code_neutral(self):
        """C3: unknown code contributes ln(OR=1) = 0."""
        self.assertAlmostEqual(self.ctx.log_odds(['ZZZ']), self._ln_o0, places=9)

    def test_C4_relevance_weight(self):
        """C4: R_d(M) scales ln(OR_d)."""
        val = self.ctx.log_odds(['D1'], r_indicator={'D1': 0.5})
        self.assertAlmostEqual(val, self._ln_o0 + 0.5 * math.log(2.5), places=9)

    def test_C5_zero_base_prob_clipped(self):
        """C5: P_0 = 0 → clipped to 1e-5 → O_0 = 1e-5/(1-1e-5) (no ln(0) domain error)."""
        ctx0 = ClinicalContext(base_prob_P0=0.0, odds_ratios={})
        self.assertAlmostEqual(ctx0.log_odds([]), math.log(1e-5 / (1.0 - 1e-5)), places=9)


# ══════════════════════════════════════════════════════════════════════════════
#  Group D — UrgencyEngine.sdc_score
# ══════════════════════════════════════════════════════════════════════════════

class TestGroupD_SdcScore(unittest.TestCase):
    def setUp(self):
        self.eng = UrgencyEngine(alpha=0.7)

    def test_D1_all_peak_active_is_one(self):
        """D1: every channel active at P_max → SDC_score = 1.0."""
        prio = {'a': 3, 'b': 3, 'c': 3}
        act = {'a': 1, 'b': 1, 'c': 1}
        self.assertAlmostEqual(self.eng.sdc_score(prio, act), 1.0, places=9)

    def test_D2_all_inactive_is_zero(self):
        """D2: no channel active → SDC_score = 0.0."""
        prio = {'a': 3, 'b': 2}
        act = {'a': 0, 'b': 0}
        self.assertAlmostEqual(self.eng.sdc_score(prio, act), 0.0, places=9)

    def test_D3_all_none_priority_no_div_zero(self):
        """D3: P_sum = P_max = 0 → SDC_score = 0 (guarded division)."""
        prio = {'a': 0, 'b': 0}
        act = {'a': 1, 'b': 1}
        self.assertEqual(self.eng.sdc_score(prio, act), 0.0)

    def test_D4_mixed_manual(self):
        """D4: prio {3,1}, active {0,1}, α=0.7 → 0.7·(1/3)+0.3·(1/4) = 0.308333."""
        prio = {'a': 3, 'b': 1}
        act = {'a': 0, 'b': 1}
        expected = 0.7 * (1 / 3) + 0.3 * (1 / 4)
        self.assertAlmostEqual(self.eng.sdc_score(prio, act), expected, places=9)

    def test_D5_alpha_extremes(self):
        """D5: α=0 → diffuse term only; α=1 → peak term only."""
        prio = {'a': 3, 'b': 1}
        act = {'a': 0, 'b': 1}
        e0 = UrgencyEngine(alpha=0.0)
        e1 = UrgencyEngine(alpha=1.0)
        self.assertAlmostEqual(e0.sdc_score(prio, act), 1 / 4, places=9)   # Σv/Σp = 1/4
        self.assertAlmostEqual(e1.sdc_score(prio, act), 1 / 3, places=9)   # max v/P_max = 1/3


# ══════════════════════════════════════════════════════════════════════════════
#  Group E — UrgencyEngine.k_min  (dynamic consensus)
# ══════════════════════════════════════════════════════════════════════════════

class TestGroupE_KMin(unittest.TestCase):
    def setUp(self):
        self.eng = UrgencyEngine(alpha=0.7)

    def test_E1_zero_score_full_consensus(self):
        """E1: SDC_score = 0 → k_min = |M|."""
        self.assertEqual(self.eng.k_min(5, 0.0), 5)

    def test_E2_full_score_lower_bound(self):
        """E2: SDC_score = 1 → k_min = 2."""
        self.assertEqual(self.eng.k_min(5, 1.0), 2)

    def test_E3_size_two_pinned(self):
        """E3: |M| = 2 → k_min = 2 for any score."""
        for s in (0.0, 0.3, 0.7, 1.0):
            self.assertEqual(self.eng.k_min(2, s), 2)

    def test_E4_size_one_hard_floor_two(self):
        """E4: |M| = 1 → topological fail-safe pins k_min = 2 (single-VMD consensus
        is forbidden), which exceeds |M| and is therefore unreachable → the lone
        sensor can never escalate in isolation."""
        for s in (0.0, 0.5, 1.0):
            self.assertEqual(self.eng.k_min(1, s), 2)

    def test_E5_monotone_non_increasing(self):
        """E5: k_min is non-increasing in SDC_score."""
        prev = self.eng.k_min(6, 0.0)
        for s in (0.2, 0.4, 0.6, 0.8, 1.0):
            cur = self.eng.k_min(6, s)
            self.assertLessEqual(cur, prev)
            prev = cur

    def test_E6_empty_ensemble(self):
        """E6: |M| = 0 → k_min = 0 (degenerate, safe)."""
        self.assertEqual(self.eng.k_min(0, 0.5), 0)


# ══════════════════════════════════════════════════════════════════════════════
#  Group F — UrgencyEngine.theta_target
# ══════════════════════════════════════════════════════════════════════════════

class TestGroupF_ThetaTarget(unittest.TestCase):
    def setUp(self):
        self.eng = UrgencyEngine(alpha=0.7)

    def test_F1_reference_arithmetic(self):
        """F1: Θ_target = k_min·w̄ − Context_Log_Odds."""
        prio = {'a': 2, 'b': 2, 'c': 2}
        act = {'a': 1, 'b': 1, 'c': 1}          # SDC_score = 1 → k_min = 2
        theta, k, score = self.eng.theta_target(3, prio, act, w_avg=2.0, context_log_odds=0.5)
        self.assertEqual(k, 2)
        self.assertAlmostEqual(score, 1.0, places=9)
        self.assertAlmostEqual(theta, 2 * 2.0 - 0.5, places=9)   # 3.5

    def test_F2_context_sensitises(self):
        """F2: higher Context_Log_Odds lowers Θ_target (clinical sensitisation)."""
        prio = {'a': 3, 'b': 3}
        act = {'a': 1, 'b': 0}
        low, _, _ = self.eng.theta_target(2, prio, act, w_avg=1.5, context_log_odds=0.0)
        high, _, _ = self.eng.theta_target(2, prio, act, w_avg=1.5, context_log_odds=2.0)
        self.assertLess(high, low)


# ══════════════════════════════════════════════════════════════════════════════
#  Group G — HysteresisFilter  (Fast Attack / Slow Release, ρ = (1-SDC)/T)
# ══════════════════════════════════════════════════════════════════════════════

class TestGroupG_Hysteresis(unittest.TestCase):
    def test_G1_cold_start(self):
        """G1: first step returns Θ_target unchanged."""
        h = HysteresisFilter(horizon_T=10.0)
        self.assertAlmostEqual(h.step(5.0, sdc_score=0.0, dt=1.0), 5.0, places=9)

    def test_G2_fast_attack(self):
        """G2: target ≤ prev → snap immediately."""
        h = HysteresisFilter(horizon_T=10.0)
        h.step(5.0, 0.0, 1.0)
        self.assertAlmostEqual(h.step(3.0, 0.0, 1.0), 3.0, places=9)

    def test_G3_slow_release_formula(self):
        """G3: target > prev → target + (prev-target)·exp(-ρ·Δt)."""
        h = HysteresisFilter(horizon_T=10.0)
        h.step(3.0, 0.0, 1.0)                    # prev = 3
        rho = (1 - 0.0) / 10.0                    # 0.1
        expected = 7.0 + (3.0 - 7.0) * math.exp(-rho * 10.0)
        self.assertAlmostEqual(h.step(7.0, 0.0, 10.0), expected, places=9)

    def test_G4_rho_severity_coupling(self):
        """G4: SDC=1 → ρ=0 → release frozen (threshold stays at prev)."""
        h = HysteresisFilter(horizon_T=10.0)
        h.step(3.0, 1.0, 1.0)                    # prev = 3
        self.assertAlmostEqual(h.step(7.0, 1.0, 5.0), 3.0, places=9)  # frozen
        self.assertAlmostEqual(h.last_rho, 0.0, places=9)

    def test_G5_zero_dt_no_change(self):
        """G5: Δt = 0 on the release branch → threshold unchanged."""
        h = HysteresisFilter(horizon_T=10.0)
        h.step(3.0, 0.0, 1.0)
        self.assertAlmostEqual(h.step(7.0, 0.0, 0.0), 3.0, places=9)

    def test_G6_large_dt_reaches_target(self):
        """G6: Δt → ∞ → Θ_current → target."""
        h = HysteresisFilter(horizon_T=10.0)
        h.step(3.0, 0.0, 1.0)
        self.assertAlmostEqual(h.step(7.0, 0.0, 1e6), 7.0, places=6)

    def test_G7_reset_cold_start(self):
        """G7: reset() returns the filter to cold start."""
        h = HysteresisFilter(horizon_T=10.0)
        h.step(5.0, 0.0, 1.0)
        h.reset()
        self.assertIsNone(h.current)
        self.assertAlmostEqual(h.step(2.0, 0.0, 1.0), 2.0, places=9)


# ══════════════════════════════════════════════════════════════════════════════
#  Group H — AdaptiveAlarmAggregator.tick  (end-to-end integration)
# ══════════════════════════════════════════════════════════════════════════════

class TestGroupH_Integration(unittest.TestCase):
    def _agg(self, n=3, T=10.0):
        specs = {
            f's{i}': SensorSpec(f's{i}', 0.99, 0.15, 3)   # w ≈ 1.887, P = 3
            for i in range(n)
        }
        # Neutral clinical context (P_0 = 0.5 → O_0 = 1 → ln O_0 = 0) isolates the
        # consensus/evidence mechanics under test from the clinical baseline shift.
        # Passing None would (correctly) fall back to the low-prior baseline
        # (P_0 = 0.005 → ln O_0 ≈ −5.29), which is validated separately in Group C.
        neutral = ClinicalContext(base_prob_P0=0.5, odds_ratios={})
        return AdaptiveAlarmAggregator(specs, EngineConfig(horizon_T=T, alpha=0.7), neutral)

    def test_H1_escalation_condition_is_ge(self):
        """H1: verdict flips exactly at E(t) ≥ Θ_current(t)."""
        agg = self._agg(n=2, T=10.0)
        result = None
        for t in range(13):                       # saturate both channels
            result = agg.tick({'s0': 1, 's1': 1}, dt_step=1.0, t_now=float(t))
        assert result is not None
        # 2 saturated channels: E = 2·1.887 = Θ_target = 2·w̄ → E ≥ Θ ⇒ escalate.
        self.assertTrue(result.is_escalated)
        self.assertGreaterEqual(result.evidence, result.current_theta)

    def test_H2_consensus_quorum(self):
        """H2: a lone SPOF never escalates; a quorum does."""
        # One channel active out of three → below k_min → no escalation.
        solo = self._agg(n=3, T=10.0)
        r_solo = None
        for t in range(13):
            r_solo = solo.tick({'s0': 1, 's1': 0, 's2': 0}, 1.0, t_now=float(t))
        self.assertFalse(r_solo.is_escalated)

        # All three active → quorum reached → escalation.
        full = self._agg(n=3, T=10.0)
        r_full = None
        for t in range(13):
            r_full = full.tick({'s0': 1, 's1': 1, 's2': 1}, 1.0, t_now=float(t))
        self.assertTrue(r_full.is_escalated)

    def test_H3_telemetry_populated(self):
        """H3: extended telemetry (sdc_score, k_min, theta_target, rho_decay) is exported."""
        agg = self._agg(n=3, T=10.0)
        r = None
        for t in range(6):
            r = agg.tick({'s0': 1, 's1': 1, 's2': 1}, 1.0, t_now=float(t))
        self.assertAlmostEqual(r.sdc_score, 1.0, places=9)     # all P=3 active
        self.assertEqual(r.k_min, 2)                            # score=1 → k_min=2
        self.assertGreater(r.theta_target, 0.0)
        self.assertAlmostEqual(r.rho_decay, 0.0, places=9)      # ρ=(1-1)/T=0

    def test_H4_empty_ensemble_safe_default(self):
        """H4: |M| = 0 → safe non-escalating default."""
        agg = AdaptiveAlarmAggregator({}, EngineConfig(), None)
        r = agg.tick({}, dt_step=1.0, t_now=0.0)
        self.assertIsInstance(r, TickResult)
        self.assertFalse(r.is_escalated)
        self.assertEqual(r.k_min, 0)

    def test_H5_jitter_slow_release_holds(self):
        """H5: after escalation, a brief OFF tick keeps evidence high (ZOH) — no instant reset."""
        agg = self._agg(n=3, T=10.0)
        for t in range(13):
            agg.tick({'s0': 1, 's1': 1, 's2': 1}, 1.0, t_now=float(t))
        # One OFF tick immediately after saturation: E stays high via ZOH history.
        r = agg.tick({'s0': 0, 's1': 0, 's2': 0}, 1.0, t_now=13.0)
        self.assertTrue(r.is_escalated)

    def test_H6_latency_under_budget(self):
        """H6: a tick on |M|=10 completes well under the 100 ms real-time budget."""
        agg = self._agg(n=10, T=10.0)
        states = {f's{i}': 1 for i in range(10)}
        start = time.perf_counter()
        for t in range(200):
            agg.tick(states, 1.0, t_now=float(t))
        avg_ms = (time.perf_counter() - start) / 200 * 1000.0
        self.assertLess(avg_ms, 100.0)

    def test_H7_clinical_context_lowers_threshold(self):
        """H7: set_clinical_context sensitises the filter (lower Θ_target)."""
        agg = self._agg(n=3, T=10.0)
        r_base = agg.tick({'s0': 1, 's1': 1, 's2': 1}, 1.0, t_now=0.0)
        agg.set_clinical_context(3.0)             # strong positive shift
        r_ctx = agg.tick({'s0': 1, 's1': 1, 's2': 1}, 1.0, t_now=1.0)
        self.assertLess(r_ctx.theta_target, r_base.theta_target)

    def test_H8_none_context_uses_low_prior_baseline(self):
        """H8: no context → low-prior baseline ln(O_0), NOT the buggy 0.0.

        A raw 0.0 baseline (O_0 = 1 ⇔ P_0 = 50 %) would collapse Θ_target by the
        baseline magnitude and make the filter hypersensitive.  The fallback must
        instead be ln(0.005/0.995), i.e. Θ_target is HIGHER (harder to escalate)
        than under a neutral (0.0-baseline) context by exactly that amount.
        """
        specs = {
            f's{i}': SensorSpec(f's{i}', 0.99, 0.15, 3) for i in range(3)
        }
        cfg = EngineConfig(horizon_T=10.0, alpha=0.7)
        default_agg = AdaptiveAlarmAggregator(specs, cfg, None)                 # fallback baseline
        neutral_agg = AdaptiveAlarmAggregator(
            specs, cfg, ClinicalContext(base_prob_P0=0.5, odds_ratios={})        # ln O_0 = 0
        )
        r_default = default_agg.tick({'s0': 1, 's1': 1, 's2': 1}, 1.0, t_now=0.0)
        r_neutral = neutral_agg.tick({'s0': 1, 's1': 1, 's2': 1}, 1.0, t_now=0.0)
        expected_gap = -math.log(0.005 / (1.0 - 0.005))   # −ln(O_0) > 0
        self.assertAlmostEqual(
            r_default.theta_target - r_neutral.theta_target, expected_gap, places=6
        )
        self.assertGreater(r_default.theta_target, r_neutral.theta_target)

    def test_H9_sensor_ids_ordered_by_priority(self):
        """H9: sensor_ids is ordered by descending P_j, ties broken by id."""
        specs = {
            'lo':  SensorSpec('lo', 0.9, 0.1, 1),
            'hi':  SensorSpec('hi', 0.9, 0.1, 3),
            'me':  SensorSpec('me', 0.9, 0.1, 2),
            'hi2': SensorSpec('hi2', 0.9, 0.1, 3),
        }
        agg = AdaptiveAlarmAggregator(specs, EngineConfig(), None)
        # P_j: hi=3, hi2=3, me=2, lo=1 → ['hi', 'hi2', 'me', 'lo'] (ties by id).
        self.assertEqual(agg.sensor_ids, ['hi', 'hi2', 'me', 'lo'])


# ══════════════════════════════════════════════════════════════════════════════
#  Group I — DeviceProfileRepository (config parsing + fail-open defaults)
# ══════════════════════════════════════════════════════════════════════════════

class TestGroupI_Repository(unittest.TestCase):
    def test_I1_failopen_defaults_on_empty_db(self):
        """I1: missing sections → conservative defaults (fail-open)."""
        r = DeviceProfileRepository()
        r._raw_clinical_db = {}                     # simulate empty / missing file
        self.assertEqual(r.get_base_prob(), 0.005)
        self.assertEqual(r.get_odds_ratio('anything'), 1.0)
        self.assertEqual(r.get_filter_params(), (10.0, 0.7))
        # Default priority map is used when the section is absent.
        self.assertEqual(r.get_priority('Hi'), 3)

    def test_I2_priority_mapping(self):
        """I2: Hi/Me/Lo/None → 3/2/1/0."""
        r = DeviceProfileRepository()
        r._raw_clinical_db = {'priority_map': {'None': 0, 'Lo': 1, 'Me': 2, 'Hi': 3}}
        self.assertEqual(r.get_priority('Hi'), 3)
        self.assertEqual(r.get_priority('Me'), 2)
        self.assertEqual(r.get_priority('Lo'), 1)
        self.assertEqual(r.get_priority('None'), 0)
        self.assertEqual(r.get_priority('???'), 0)   # unknown → 0

    def test_I3_unknown_odds_ratio_neutral(self):
        """I3: unknown diagnosis code → OR = 1.0."""
        r = DeviceProfileRepository()
        r._raw_clinical_db = {'odds_ratios': {'D1': 2.5}}
        self.assertEqual(r.get_odds_ratio('D1'), 2.5)
        self.assertEqual(r.get_odds_ratio('ZZZ'), 1.0)

    def test_I4_comment_keys_skipped(self):
        """I4: ``_``-prefixed comment keys are ignored by the parsers."""
        r = DeviceProfileRepository()
        r._raw_clinical_db = {'odds_ratios': {'_comment': 'x', 'D1': 3.0}}
        ratios = r.get_odds_ratios()
        self.assertIn('D1', ratios)
        self.assertNotIn('_comment', ratios)

    def test_I5_real_db_loads(self):
        """I5: the shipped clinical_db.json parses with the new sections present."""
        r = DeviceProfileRepository()
        self.assertEqual(r.get_priority('Hi'), 3)
        horizon_t, alpha = r.get_filter_params()
        self.assertAlmostEqual(horizon_t, 10.0, places=6)
        self.assertAlmostEqual(alpha, 0.7, places=6)
        self.assertGreater(r.get_odds_ratio('59621000'), 1.0)


# ══════════════════════════════════════════════════════════════════════════════
#  Group J — Time-in-Alarm penalty δ(t)  (SPOF fail-safe threshold erosion)
# ══════════════════════════════════════════════════════════════════════════════

class TestGroupJ_TimeInAlarmPenalty(unittest.TestCase):
    """δ(t) = exp(−λ·max(0, τ−T)), λ = SDC/T, applied as Θ_target·δ(t).

    Stateless multiplier (J1–J5) is validated directly on
    ``UrgencyEngine.decay_multiplier``; the stateful τ tracking + end-to-end SPOF
    escalation (J6–J10) is validated through ``AdaptiveAlarmAggregator.tick``.
    """

    # ── Stateless decay multiplier ─────────────────────────────────────────────

    def test_J1_grace_period_no_decay(self):
        """J1: while τ ≤ T the multiplier is exactly 1.0 (one-horizon grace)."""
        for tau in (0.0, 1.0, 5.0, 10.0):
            self.assertAlmostEqual(
                UrgencyEngine.decay_multiplier(0.8, tau, 10.0), 1.0, places=12
            )

    def test_J2_monotonic_decrease_after_grace(self):
        """J2: past the grace horizon δ(t) strictly decreases and stays in (0, 1]."""
        T, sdc = 10.0, 0.8
        vals = [UrgencyEngine.decay_multiplier(sdc, tau, T)
                for tau in (10.0, 12.0, 14.0, 20.0, 30.0)]
        self.assertAlmostEqual(vals[0], 1.0, places=12)     # τ = T → no decay yet
        for a, b in zip(vals, vals[1:]):
            self.assertLess(b, a)                            # strictly decreasing
        self.assertTrue(all(0.0 < v <= 1.0 for v in vals))

    def test_J3_zero_severity_no_decay(self):
        """J3: SDC_score = 0 ⇒ λ = 0 ⇒ δ = 1.0 regardless of duration."""
        self.assertEqual(
            UrgencyEngine.decay_multiplier(0.0, 1000.0, 10.0), 1.0
        )

    def test_J4_lambda_equals_sdc_over_T(self):
        """J4: symmetry λ(t) = SDC/T = 1/T − ρ(t)  (recovered from δ at τ = T+1)."""
        T, sdc = 10.0, 0.6
        delta = UrgencyEngine.decay_multiplier(sdc, T + 1.0, T)   # time_over_grace = 1
        self.assertAlmostEqual(-math.log(delta), sdc / T, places=12)

    def test_J5_horizon_floor_safe(self):
        """J5: T ≤ 0 is floored (no div-by-zero / no exception); δ stays finite in
        [0, 1] (it may underflow to 0.0 for a huge effective λ — that is safe)."""
        d = UrgencyEngine.decay_multiplier(1.0, 5.0, 0.0)
        self.assertTrue(math.isfinite(d))
        self.assertTrue(0.0 <= d <= 1.0)

    # ── Stateful τ tracking through the aggregator ─────────────────────────────

    def _agg(self, n=3, T=10.0):
        specs = {f's{i}': SensorSpec(f's{i}', 0.99, 0.15, 3) for i in range(n)}
        neutral = ClinicalContext(base_prob_P0=0.5, odds_ratios={})   # ln O_0 = 0
        return AdaptiveAlarmAggregator(specs, EngineConfig(horizon_T=T, alpha=0.7), neutral)

    def test_J6_duration_accumulates_while_active(self):
        """J6: τ accumulates (Δt per tick) while E(t) > 0 and NOT escalated."""
        agg = self._agg(n=3, T=10.0)
        durs = []
        r = None
        for t in range(5):   # a lone channel: below k_min → never escalates this fast
            r = agg.tick({'s0': 1, 's1': 0, 's2': 0}, 1.0, t_now=float(t))
            durs.append(r.active_alarm_duration)
        self.assertFalse(r.is_escalated)
        self.assertEqual(durs, sorted(durs))          # non-decreasing
        self.assertGreater(durs[-1], durs[0])         # actually grew
        self.assertGreater(durs[-1], 0.0)

    def test_J7_duration_resets_when_evidence_zero(self):
        """J7: once E(t) returns to 0 (window washed out), τ resets to 0."""
        agg = self._agg(n=3, T=10.0)
        for t in range(5):
            agg.tick({'s0': 1, 's1': 0, 's2': 0}, 1.0, t_now=float(t))
        r = None
        for t in range(5, 40):     # all OFF; ZOH window empties after ~T seconds
            r = agg.tick({'s0': 0, 's1': 0, 's2': 0}, 1.0, t_now=float(t))
            if r.evidence <= 0.0:
                break
        self.assertLessEqual(r.evidence, 0.0)
        self.assertEqual(r.active_alarm_duration, 0.0)

    def test_J8_duration_held_during_escalation(self):
        """J8: once escalated, τ is FROZEN (no overflow while the crisis persists)."""
        agg = self._agg(n=3, T=10.0)
        r = None
        for t in range(15):
            r = agg.tick({'s0': 1, 's1': 1, 's2': 1}, 1.0, t_now=float(t))
        self.assertTrue(r.is_escalated)
        held = r.active_alarm_duration
        for t in range(15, 25):
            r = agg.tick({'s0': 1, 's1': 1, 's2': 1}, 1.0, t_now=float(t))
        self.assertTrue(r.is_escalated)
        self.assertEqual(r.active_alarm_duration, held)   # frozen, not growing

    def test_J9_spof_single_channel_eventually_escalates(self):
        """J9: a persistent lone alarm (blocked by k_min ≥ 2) eventually escalates
        as δ(t) erodes Θ_target — the SPOF fail-safe."""
        agg = self._agg(n=3, T=10.0)
        early = None
        escalated_any = False
        for t in range(40):
            r = agg.tick({'s0': 1, 's1': 0, 's2': 0}, 1.0, t_now=float(t))
            if t == 5:
                early = r
            if r.is_escalated:
                escalated_any = True
        self.assertIsNotNone(early)
        self.assertFalse(early.is_escalated)   # within grace → still suppressed
        self.assertTrue(escalated_any)         # erosion crosses the barrier later

    def test_J10_has_active_alarms_flag(self):
        """J10: has_active_alarms mirrors E(t) > 0 (local bedside / Yellow gate)."""
        agg = self._agg(n=3, T=10.0)
        agg.tick({'s0': 1, 's1': 0, 's2': 0}, 1.0, t_now=0.0)          # prime window
        r = agg.tick({'s0': 1, 's1': 0, 's2': 0}, 1.0, t_now=1.0)      # E(t) > 0 now
        self.assertTrue(r.has_active_alarms)
        r_off = None
        for t in range(2, 40):
            r_off = agg.tick({'s0': 0, 's1': 0, 's2': 0}, 1.0, t_now=float(t))
            if r_off.evidence <= 0.0:
                break
        self.assertFalse(r_off.has_active_alarms)

    def test_J11_within_grace_theta_target_unchanged(self):
        """J11: inside the grace window δ = 1 ⇒ Θ_target equals the raw k·w̄−ctx."""
        agg = self._agg(n=3, T=10.0)
        r = None
        for t in range(3):   # τ ≤ 3 < T ⇒ δ = 1.0
            r = agg.tick({'s0': 1, 's1': 1, 's2': 1}, 1.0, t_now=float(t))
        self.assertAlmostEqual(r.delta_penalty, 1.0, places=12)
        # sdc = 1 (all P=3 active) → k_min = 2; w̄ = ln(0.99/0.15); ctx = 0.
        expected = 2 * math.log(0.99 / 0.15)
        self.assertAlmostEqual(r.theta_target, expected, places=6)


# ══════════════════════════════════════════════════════════════════════════════
#  Group K — ZOH-backfill reset  (false re-escalation fail-safe on resolution)
# ══════════════════════════════════════════════════════════════════════════════

class TestGroupK_ZohResetOnResolution(unittest.TestCase):
    """Regression guard for the ZOH-backfill bug.

    When an ensemble fully resolves (TTL cache empty / escalation latch cleared),
    SmartAlertAggregator calls ``aggregator.reset()`` to flush the FIR buffers.
    Without that flush, a re-fire arriving after a silent gap would let the ZOH
    integral back-fill the stale ``True`` samples across the whole window and
    trigger a FALSE re-escalation.  These tests pin both the raw (buggy) behaviour
    and the reset() mitigation at the math-core level.
    """

    def _agg(self, n=3, T=10.0):
        specs = {f's{i}': SensorSpec(f's{i}', 0.99, 0.15, 3) for i in range(n)}
        neutral = ClinicalContext(base_prob_P0=0.5, odds_ratios={})   # ln O_0 = 0
        return AdaptiveAlarmAggregator(specs, EngineConfig(horizon_T=T, alpha=0.7), neutral)

    def test_K1_backfill_without_reset_reescalates(self):
        """K1 (control): WITHOUT reset, a single re-fire after a silent gap is
        back-filled by ZOH → false re-escalation (documents the bug)."""
        agg = self._agg(n=3, T=10.0)
        r = None
        for t in range(13):                                  # saturate → escalate
            r = agg.tick({'s0': 1, 's1': 1, 's2': 1}, 1.0, t_now=float(t))
        self.assertTrue(r.is_escalated)

        # No reset. Silence, then ONE channel re-fires far in the future.
        r2 = agg.tick({'s0': 1, 's1': 0, 's2': 0}, dt_step=15.0, t_now=28.0)
        # Stale True held across [18, 28] for EVERY channel → E ≈ full ensemble.
        self.assertTrue(r2.is_escalated)                     # ← the bug we fix
        self.assertGreaterEqual(r2.evidence, r2.current_theta)

    def test_K2_reset_prevents_backfill(self):
        """K2 (fix): reset() before the re-fire flushes the buffers → the lone
        channel cannot back-fill → NO false re-escalation."""
        agg = self._agg(n=3, T=10.0)
        r = None
        for t in range(13):
            r = agg.tick({'s0': 1, 's1': 1, 's2': 1}, 1.0, t_now=float(t))
        self.assertTrue(r.is_escalated)

        # Resolution flush (what clear_alarm / _cleanup_stale_alarms now do).
        agg.reset()

        # Same single re-fire after the same gap — now starts cold.
        r2 = agg.tick({'s0': 1, 's1': 0, 's2': 0}, dt_step=15.0, t_now=28.0)
        self.assertFalse(r2.is_escalated)
        self.assertLess(r2.evidence, r2.current_theta)

    def test_K3_reset_clears_dynamic_state_keeps_composition(self):
        """K3: reset() zeroes τ / escalation memory but preserves |M| and w̄."""
        agg = self._agg(n=3, T=10.0)
        for t in range(13):
            agg.tick({'s0': 1, 's1': 1, 's2': 1}, 1.0, t_now=float(t))
        self.assertGreater(agg.active_alarm_duration, 0.0)

        ids_before = list(agg.sensor_ids)
        agg.reset()

        self.assertEqual(agg.active_alarm_duration, 0.0)      # τ reset
        self.assertEqual(agg.sensor_ids, ids_before)          # composition intact
        # First tick after reset with fresh evidence starts from E≈0 (no backfill).
        r = agg.tick({'s0': 1, 's1': 1, 's2': 1}, 1.0, t_now=14.0)
        self.assertFalse(r.is_escalated)                      # window must refill

    def test_K4_evidence_accumulator_reset(self):
        """K4: EvidenceAccumulator.reset() empties buffers (E→0) but keeps specs."""
        specs = {'a': SensorSpec('a', 0.99, 0.15, 3)}
        ea = EvidenceAccumulator(10.0, specs)
        for t in range(12):
            ea.push({'a': True}, float(t))
        self.assertGreater(ea.evidence(11.0), 0.0)

        ea.reset()

        self.assertEqual(ea.evidence(11.0), 0.0)              # buffers flushed
        self.assertEqual(ea.ensemble_size(), 1)              # specs preserved
        self.assertFalse(ea.raw_activations()['a'])          # last_raw reset


if __name__ == '__main__':
    unittest.main(verbosity=2)

