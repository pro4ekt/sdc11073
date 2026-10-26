"""
tests/test_periodic_eval.py
===========================
Periodic evaluation grid T_P (thesis 3.4.5): the model is evaluated at
T = T_E ∪ T_P, where T_E are the alarm events and T_P = {t_0 + k·Δ_eval} a grid
with Δ_eval ≤ T.  Without T_P an ensemble whose sources switch on together and
then stay unchanged is only ever evaluated with empty windows (s_j = 0 at a
source's own switch-on instant) and never escalates.

Covered here (SmartAlertAggregator.evaluate_periodic, driven by hand with a
deterministic clock; the background thread is disabled with periodic_eval=False):

    P1  three Hi channels switch on together, no further events → escalation
        at t = 7 s (E = 3w·s ≥ 2w ⇔ s ≥ 2/3), i.e. no later than T + Δ_eval
    P1b two channels (|M| = 2, k_min ≡ 2) → escalation no later than T + Δ_eval
        and not before the window is full
    P2  a single alarm behaves per the model: never escalates below the
        δ_min bound, τ accumulates, δ reaches its floor, Yellow throughout
    P3  scope: no active alarms → nothing evaluated; latched ensembles evaluated but not routed;
        a pass without clock advance is a no-op
    P4  Δt bookkeeping: periodic ticks book _last_tick_ts and apply the 20 s clamp
    P5  repository: delta_eval default, configured value, rejection of values
        outside 0 < Δ_eval ≤ T; the shipped clinical_db.json is valid

Run with:  python -m unittest Dashboard.tests.test_periodic_eval -v
"""

from __future__ import annotations

import importlib.util
import logging
import math
import sys
import types
import unittest
from pathlib import Path
from typing import Optional
from unittest import mock

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_APP_DIR = _PROJECT_ROOT / 'Dashboard' / 'app'
_ALARMS_DIR = _APP_DIR / 'alarms'


def _stub_package(dotted_name: str, fs_path: Path) -> types.ModuleType:
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
    if dotted_name in sys.modules:
        return sys.modules[dotted_name]
    spec = importlib.util.spec_from_file_location(dotted_name, file_path)
    module = importlib.util.module_from_spec(spec)          # type: ignore[arg-type]
    module.__package__ = package
    sys.modules[dotted_name] = module
    spec.loader.exec_module(module)                          # type: ignore[union-attr]
    return module


_stub_package('Dashboard', _PROJECT_ROOT / 'Dashboard')
_stub_package('Dashboard.app', _APP_DIR)
_stub_package('Dashboard.app.alarms', _ALARMS_DIR)

if 'Dashboard.app.fhirData' not in sys.modules:
    _fhir_stub = types.ModuleType('Dashboard.app.fhirData')

    class _StubFHIRPatientData:                                # pragma: no cover
        def fetch(self, *_a, **_k):
            raise RuntimeError('FHIR is stubbed in tests')

    _fhir_stub.FHIRPatientData = _StubFHIRPatientData          # type: ignore[attr-defined]
    sys.modules['Dashboard.app.fhirData'] = _fhir_stub

for _name in (
    'math_types', 'evidence_accumulator', 'clinical_context', 'urgency_engine',
    'hysteresis_filter', 'device_profile_repo', 'adaptive_alarm_aggregator',
    'alarmCoordinator', 'smartAlertAggregator', 'ensemble_topology_manager',
):
    _load_module(f'Dashboard.app.alarms.{_name}', _ALARMS_DIR / f'{_name}.py', 'Dashboard.app.alarms')

from Dashboard.app.alarms.device_profile_repo import (  # noqa: E402
    DeviceReliabilityProfile, DeviceProfileRepository, get_repository,
)
from Dashboard.app.alarms.smartAlertAggregator import SmartAlertAggregator  # noqa: E402
from Dashboard.app.alarms.ensemble_topology_manager import EnsembleTopologyManager  # noqa: E402


# ── stubs ─────────────────────────────────────────────────────────────────────

def _profile_with_weight(w: float, tpr: float = 0.9) -> DeviceReliabilityProfile:
    return DeviceReliabilityProfile(true_positive_rate=tpr, false_positive_rate=tpr / math.exp(w))


class _StubHandler:
    def __init__(self, epr: str, channels):
        self.epr = epr
        self._channels = list(channels)
        self.ensemble_uuid: Optional[str] = None

    def enumerate_alert_channels(self):
        return list(self._channels)


class _StubManager:
    def __init__(self):
        self.devices: dict = {}


class _FakeClock:
    def __init__(self, t0: float = 0.0):
        self.t = t0

    def __call__(self) -> float:
        return self.t


class _Overview:
    """Records updateEnsemble pushes (is_escalated, has_active)."""

    def __init__(self):
        self.calls: list = []

    def updateEnsemble(self, uuid, name, room, count, escalated, sdc, has_active=False):
        self.calls.append((escalated, has_active, sdc))

    def removeEnsemble(self, uuid):
        self.calls.append(('remove', uuid))


class _Base(unittest.TestCase):
    UUID = 'ens-tp-0000-0000-000000000000'

    def _setup(self, channels):
        """channels: list of (key, priority, w)."""
        self.clock = _FakeClock(0.0)
        self._patcher = mock.patch('time.monotonic', self.clock)
        self._patcher.start()
        self.manager = _StubManager()
        self.topology = EnsembleTopologyManager(self.manager, overview_model=None)
        self.overview = _Overview()
        self.sa = SmartAlertAggregator(self.manager, self.topology,
                                       overview_model=self.overview, periodic_eval=False)
        self.profiles = {k: _profile_with_weight(w) for k, _p, w in channels}
        self.prios = {k: p for k, p, _w in channels}
        self.handler = _StubHandler(
            'urn:uuid:dev-TP',
            [(k, f'MDC.{k}', p, self.profiles[k]) for k, p, _w in channels],
        )
        self.manager.devices[self.handler.epr] = self.handler
        with self.topology.lock:
            self.topology._patient_to_ensemble_map[('PAT-TP', 'ICU-TP')] = self.UUID
            self.topology._ensembles_devices[self.UUID] = {self.handler.epr}

    def tearDown(self):
        self.sa.stop()
        self._patcher.stop()

    def _fire(self, key: str) -> str:
        return self.sa.check_alert_validity(
            self.UUID, key, f'MDC.{key}', biceps_priority=self.prios[key],
            manufacturer='Stub', model='S1', device_epr=self.handler.epr,
            reliability_profile=self.profiles[key],
        )

    def _agg(self):
        return self.sa._ensemble_adaptive_aggregators[self.UUID]

    def _latched(self) -> bool:
        return self.sa.is_ensemble_escalated(self.UUID)

    def _run_grid(self, until: float, step: float = 1.0):
        """Advance the fake clock on the grid and return the first instant at
        which the ensemble is latched (None if never)."""
        t = step
        while t <= until + 1e-9:
            self.clock.t = t
            self.sa.evaluate_periodic()
            if self._latched():
                return t
            t += step
        return None


# ── P1: simultaneous switch-on escalates via T_P ──────────────────────────────

class TestP1_SimultaneousSwitchOn(_Base):
    def test_P1_three_hi_channels_escalate_at_7s(self):
        """Three Hi channels (equal w) switch on at t = 0 and send no further
        reports.  SDC = 1 ⇒ k_min = 2 ⇒ Θ = 2w; E = 3w·s ⇒ escalation once
        s ≥ 2/3, i.e. at the first grid point t = 7 s — well inside T + Δ_eval."""
        self._setup([('a', 'Hi', 2.0), ('b', 'Hi', 2.0), ('c', 'Hi', 2.0)])
        for k in ('a', 'b', 'c'):
            self.assertEqual(self._fire(k), 'WARN')        # windows still empty
        self.assertFalse(self._latched())
        t_esc = self._run_grid(until=15.0)
        self.assertEqual(t_esc, 7.0)
        self.assertLessEqual(t_esc, 10.0 + 1.0)            # T + Δ_eval
        self.assertTrue(any(c[0] is True for c in self.overview.calls if len(c) == 3),
                        'no Red push to the patient card')

    def test_P1b_two_channels_escalate_no_later_than_T_plus_delta(self):
        """|M| = 2: k_min ≡ 2, Θ_base = 2w̄ = w_a + w_b = E at saturation (knife
        edge at t = T); at t = T + Δ_eval the multiplier δ < 1 strictly and the
        barrier is below E.  Escalation therefore no later than T + Δ_eval and
        not before the windows are full."""
        self._setup([('a', 'Hi', 2.4), ('b', 'Hi', 1.1)])
        self._fire('a'); self._fire('b')
        t_esc = self._run_grid(until=15.0)
        self.assertIsNotNone(t_esc, 'never escalated')
        self.assertGreaterEqual(t_esc, 10.0)
        self.assertLessEqual(t_esc, 11.0)

    def test_P1c_without_grid_never_escalates(self):
        """Control: the same three alarms with NO periodic passes stay WARN —
        the event path alone only ever sees empty windows."""
        self._setup([('a', 'Hi', 2.0), ('b', 'Hi', 2.0), ('c', 'Hi', 2.0)])
        for k in ('a', 'b', 'c'):
            self._fire(k)
        self.clock.t = 60.0
        self.assertFalse(self._latched())
        self.assertEqual(self.sa._last_tick_ts[self.UUID], 0.0)


# ── P2: single alarm follows the model ───────────────────────────────────────

class TestP2_SingleAlarm(_Base):
    def test_P2_single_alarm_below_floor_bound_never_escalates(self):
        """One active channel (w = 1) next to a silent one (w = 3): w̄ = 2,
        melted barrier δ_min·2·w̄ = 2 > E = 1 (3.4.5, single-source note).
        Over 60 s of periodic evaluation: Yellow throughout, never latched,
        τ accumulates, δ reaches its floor, Θ_current melts to exactly 2."""
        self._setup([('a', 'Hi', 1.0), ('b', 'Hi', 3.0)])
        self._fire('a')
        t_esc = self._run_grid(until=60.0)
        self.assertIsNone(t_esc)
        agg = self._agg()
        self.assertAlmostEqual(agg.active_alarm_duration, 60.0, places=6)
        self.assertAlmostEqual(agg._hysteresis.current, 2.0, places=6)
        self.assertAlmostEqual(agg._evidence.evidence(60.0), 1.0, places=6)
        pushes = [c for c in self.overview.calls if len(c) == 3]
        self.assertGreaterEqual(len(pushes), 60)
        self.assertTrue(all(c[0] is False and c[1] is True for c in pushes))


# ── P3: scope of the periodic pass ───────────────────────────────────────────

class TestP3_Scope(_Base):
    def test_P3a_no_active_alarms_nothing_evaluated(self):
        self._setup([('a', 'Hi', 2.0), ('b', 'Hi', 2.0)])
        self.clock.t = 1.0
        self.assertEqual(self.sa.evaluate_periodic(), 0)
        self.assertNotIn(self.UUID, self.sa._ensemble_adaptive_aggregators)

    def test_P3b_latched_ensemble_is_evaluated_but_not_routed(self):
        """Fix 12: a latched ensemble is still evaluated on the grid (3.4.6: the
        filter keeps tracking), but the decision is not routed — no card push,
        latch untouched."""
        self._setup([('a', 'Hi', 2.0), ('b', 'Hi', 2.0), ('c', 'Hi', 2.0)])
        for k in ('a', 'b', 'c'):
            self._fire(k)
        self.assertEqual(self._run_grid(until=15.0), 7.0)
        n_calls = len(self.overview.calls)
        self.clock.t = 30.0
        self.assertEqual(self.sa.evaluate_periodic(), 1)
        self.assertEqual(self.sa._last_tick_ts[self.UUID], 30.0)
        self.assertTrue(self._latched())
        self.assertEqual(len(self.overview.calls), n_calls)

    def test_P3c_pass_without_clock_advance_is_noop(self):
        self._setup([('a', 'Hi', 2.0), ('b', 'Hi', 2.0)])
        self._fire('a')
        self.clock.t = 1.0
        self.assertEqual(self.sa.evaluate_periodic(), 1)
        self.assertEqual(self.sa.evaluate_periodic(), 0)      # same instant → no-op
        self.clock.t = 0.5
        self.assertEqual(self.sa.evaluate_periodic(), 0)      # clock went back → no-op

    def test_P3d_ensemble_resolved_after_full_clear_is_not_evaluated(self):
        self._setup([('a', 'Hi', 2.0), ('b', 'Hi', 2.0)])
        self._fire('a')
        self.clock.t = 1.0
        self.sa.clear_alarm(self.UUID, 'a')
        self.clock.t = 2.0
        self.assertEqual(self.sa.evaluate_periodic(), 0)


# ── P4: Δt bookkeeping on the periodic path ──────────────────────────────────

class TestP4_DeltaT(_Base):
    def test_P4_periodic_tick_books_timestamp_and_clamps(self):
        """The periodic tick uses the same Δt source and clamp as the event
        path: after a 100 s gap the credited τ increase is ADAPTIVE_MAX_DT_STEP."""
        self._setup([('a', 'Hi', 1.0), ('b', 'Hi', 3.0)])
        self._fire('a')
        self.clock.t = 100.0
        self.assertEqual(self.sa.evaluate_periodic(), 1)
        self.assertEqual(self.sa._last_tick_ts[self.UUID], 100.0)
        self.assertAlmostEqual(self._agg().active_alarm_duration,
                               self.sa.ADAPTIVE_MAX_DT_STEP, places=6)


# ── P5: Δ_eval configuration ─────────────────────────────────────────────────

class TestP5_EvalIntervalConfig(unittest.TestCase):
    def _repo(self, raw: dict) -> DeviceProfileRepository:
        repo = DeviceProfileRepository()
        repo._raw_clinical_db = raw
        return repo

    def test_P5a_default_is_one_second(self):
        self.assertEqual(self._repo({}).get_eval_interval(), 1.0)
        self.assertEqual(self._repo({'filter_params': {'horizon_T': 10.0}}).get_eval_interval(), 1.0)

    def test_P5b_configured_value_honoured(self):
        repo = self._repo({'filter_params': {'horizon_T': 10.0, 'delta_eval': 2.5}})
        self.assertEqual(repo.get_eval_interval(), 2.5)

    def test_P5c_values_outside_bounds_rejected(self):
        for bad in (0.0, -1.0, 10.5, 'x', None):
            repo = self._repo({'filter_params': {'horizon_T': 10.0, 'delta_eval': bad}})
            with self.assertLogs('sdc.consumer.device_profile_repo', level=logging.CRITICAL):
                self.assertEqual(repo.get_eval_interval(), 1.0, msg=f'bad={bad!r}')
        repo = self._repo({'filter_params': {'horizon_T': 10.0, 'delta_eval': 10.0}})
        self.assertEqual(repo.get_eval_interval(), 10.0)          # Δ_eval = T allowed

    def test_P5d_fallback_never_exceeds_horizon(self):
        repo = self._repo({'filter_params': {'horizon_T': 0.5}})
        self.assertEqual(repo.get_eval_interval(), 0.5)

    def test_P5e_shipped_config_is_valid(self):
        repo = get_repository()
        horizon_t, _ = repo.get_filter_params()
        d = repo.get_eval_interval()
        self.assertEqual(d, 1.0)
        self.assertLessEqual(d, horizon_t)

    def test_P5f_aggregator_reads_delta_eval(self):
        sa = SmartAlertAggregator(_StubManager(), EnsembleTopologyManager(_StubManager(), None),
                                  overview_model=None, periodic_eval=False)
        try:
            self.assertEqual(sa.DELTA_EVAL_SEC, 1.0)
            self.assertIsNone(sa._eval_thread)
        finally:
            sa.stop()


if __name__ == '__main__':
    unittest.main()
