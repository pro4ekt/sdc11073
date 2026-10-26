"""
tests/test_alignment_integration.py
===================================
Integration tests for the docs/model/SPEC_DELTA.md fixes that live ABOVE the pure math
core — in ``SmartAlertAggregator`` (fast path) and ``EnsembleTopologyManager``
(slow path):

    Fix 5 — Gateway Admission Rule  (0 < FPR_j < TPR_j, docs/model/math_part1.md §2.1)
    Fix 6 — clear_alarm drives a tick so the OFF edge reaches the ZOH integral
    Fix 8 — ensemble membership shrinks when a device departs

The SDC / Qt / FHIR stack is NOT imported: the same isolated-module bootstrap as
``test_math_core.py`` is used, plus a stub for ``Dashboard.app.fhirData`` (which
would otherwise pull in ``fhirpy``).  Device handlers and the consumer manager
are replaced by minimal duck-typed stubs.

Run with:  python -m unittest Dashboard.tests.test_alignment_integration -v
"""

from __future__ import annotations

import importlib.util
import math
import sys
import threading
import types
import unittest
from pathlib import Path
from typing import Optional
from unittest import mock

# ══════════════════════════════════════════════════════════════════════════════
#  Isolated module bootstrap (mirrors test_math_core.py)
# ══════════════════════════════════════════════════════════════════════════════

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
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

# ``ensemble_topology_manager`` imports ``..fhirData`` (→ fhirpy).  Provide a
# stub module exposing a FHIRPatientData class that is never instantiated here.
if 'Dashboard.app.fhirData' not in sys.modules:
    _fhir_stub = types.ModuleType('Dashboard.app.fhirData')

    class _StubFHIRPatientData:                                # pragma: no cover
        def fetch(self, *_a, **_k):
            raise RuntimeError('FHIR is stubbed in tests')

    _fhir_stub.FHIRPatientData = _StubFHIRPatientData          # type: ignore[attr-defined]
    sys.modules['Dashboard.app.fhirData'] = _fhir_stub

for _name in (
    'math_types',
    'evidence_accumulator',
    'clinical_context',
    'urgency_engine',
    'hysteresis_filter',
    'device_profile_repo',
    'adaptive_alarm_aggregator',
    'alarmCoordinator',
    'smartAlertAggregator',
    'ensemble_topology_manager',
):
    _load_module(f'Dashboard.app.alarms.{_name}', _ALARMS_DIR / f'{_name}.py', 'Dashboard.app.alarms')

from Dashboard.app.alarms.math_types import EngineConfig, SensorSpec  # noqa: E402
from Dashboard.app.alarms.device_profile_repo import DeviceReliabilityProfile  # noqa: E402
from Dashboard.app.alarms.alarmCoordinator import DeviceAlertEvidence  # noqa: E402
from Dashboard.app.alarms.smartAlertAggregator import SmartAlertAggregator  # noqa: E402
from Dashboard.app.alarms.ensemble_topology_manager import EnsembleTopologyManager  # noqa: E402


# ══════════════════════════════════════════════════════════════════════════════
#  Duck-typed stubs for the SDC layer
# ══════════════════════════════════════════════════════════════════════════════

def _profile_with_weight(w: float, tpr: float = 0.9) -> DeviceReliabilityProfile:
    """Profile whose w_j = ln(TPR/FPR) equals ``w``."""
    return DeviceReliabilityProfile(true_positive_rate=tpr, false_positive_rate=tpr / math.exp(w))


class _StubHandler:
    """Minimal DeviceHandler double: only what the topology manager touches."""

    def __init__(self, epr: str, channels):
        self.epr = epr
        # list of (alert_key, metric_concept, biceps_priority, profile | None)
        self._channels = list(channels)
        self.ensemble_uuid: Optional[str] = None

    def enumerate_alert_channels(self):
        return list(self._channels)


class _StubManager:
    """SdcMyConsumer double: exposes ``.devices`` (epr → handler)."""

    def __init__(self):
        self.devices: dict = {}


def _evidence(uuid: str, key: str, prof, prio: str = 'Hi') -> DeviceAlertEvidence:
    return DeviceAlertEvidence(
        alert_key=key, metric_concept=f'MDC.{key}', manufacturer='Stub', model='S1',
        ensemble_uuid=uuid, biceps_priority=prio, reliability_profile=prof,
    )


def _bind(topology: EnsembleTopologyManager, uuid: str, patient: str, room: str, *eprs):
    """Register an ensemble with member devices directly (no SOAP/FHIR)."""
    with topology.lock:
        topology._patient_to_ensemble_map[(patient, room)] = uuid
        topology._ensembles_devices[uuid] = set(eprs)


# ══════════════════════════════════════════════════════════════════════════════
#  Fix 5 — Gateway Admission Rule
# ══════════════════════════════════════════════════════════════════════════════

class TestFix5_GatewayAdmission(unittest.TestCase):
    """Only sources with 0 < FPR_j < TPR_j are admitted to M (§2.1)."""

    UUID = 'ens-fix5-0000-0000-000000000000'

    def setUp(self):
        self.manager = _StubManager()
        self.topology = EnsembleTopologyManager(self.manager, overview_model=None)
        self.sa = SmartAlertAggregator(self.manager, self.topology, overview_model=None)
        # 2 profiled channels at w = 5, 6 unprofiled channels.
        self.prof = _profile_with_weight(5.0)
        chans = [(f'p{i}', f'MDC.p{i}', 'Hi', self.prof) for i in range(2)]
        chans += [(f'u{i}', f'MDC.u{i}', 'Hi', None) for i in range(6)]
        self.handler = _StubHandler('urn:uuid:dev-A', chans)
        self.manager.devices[self.handler.epr] = self.handler
        _bind(self.topology, self.UUID, 'PAT-1', 'ICU-1', self.handler.epr)

    def tearDown(self):
        self.sa.stop()

    def test_5a_registry_admits_only_profiled(self):
        """|M| = 2 in the topology registry; w̄ = 5."""
        specs = self.topology.get_member_specs(self.UUID)
        self.assertEqual(set(specs), {'p0', 'p1'})
        for s in specs.values():
            self.assertAlmostEqual(s.w_j, 5.0, places=6)

    def test_5b_evidence_specs_admit_only_profiled(self):
        """_build_specs_from_evidences applies the same rule to active alarms."""
        evs = [_evidence(self.UUID, f'p{i}', self.prof) for i in range(2)]
        evs += [_evidence(self.UUID, f'u{i}', None) for i in range(6)]
        specs = self.sa._build_specs_from_evidences(evs)
        self.assertEqual(set(specs), {'p0', 'p1'})

    def test_5c_rule_boundaries(self):
        """FPR = 0, FPR = TPR and FPR > TPR are all rejected; 0 < FPR < TPR admitted."""
        cases = {
            'fpr_zero':  DeviceReliabilityProfile(0.9, 0.0),
            'equal':     DeviceReliabilityProfile(0.5, 0.5),
            'inverted':  DeviceReliabilityProfile(0.2, 0.6),
            'ok':        DeviceReliabilityProfile(0.9, 0.1),
        }
        evs = [_evidence(self.UUID, k, p) for k, p in cases.items()]
        self.assertEqual(set(self.sa._build_specs_from_evidences(evs)), {'ok'})
        h = _StubHandler('urn:uuid:dev-B', [(k, f'MDC.{k}', 'Hi', p) for k, p in cases.items()])
        self.manager.devices[h.epr] = h
        self.assertEqual(set(self.topology._enumerate_member_specs({h.epr})), {'ok'})

    def test_5d_single_channel_held_while_delta_above_floor(self):
        """|M| = 2, w̄ = 5 → Θ_base = 10; one channel at E = 5 stays below the
        barrier for as long as δ(t) > δ_min.

        Scope is deliberately limited to the melting phase: at |M| = 2 the
        factor (|M| − 2) is zero, k_min ≡ 2, and the melted barrier
        δ_min·k_min·w̄ = w̄ = 5 coincides with E — the §8.1 knife edge recorded
        in docs/model/SPEC_DELTA.md.  What happens after δ reaches the floor is NOT a
        guarantee of the model and is not asserted here."""
        member_specs = self.topology.get_member_specs(self.UUID)
        evs = [_evidence(self.UUID, 'p0', self.prof)]
        evs += [_evidence(self.UUID, f'u{i}', None) for i in range(6)]
        with self.sa._adaptive_lock:
            agg = self.sa._get_or_build_adaptive_aggregator_locked(
                self.UUID, evs, member_specs, set()
            )
        self.assertEqual(sorted(agg.sensor_ids), ['p0', 'p1'])
        self.assertAlmostEqual(agg._evidence.w_avg(), 5.0, places=6)

        r = None
        for t in range(19):                           # τ ≤ 18 < τ_floor ≈ 19.2
            r = agg.tick({'p0': 1, 'p1': 0}, 1.0, t_now=float(t))
            self.assertFalse(r.is_escalated, f'escalated at t={t}')
        self.assertEqual(r.k_min, 2)
        self.assertAlmostEqual(r.evidence, 5.0, places=6)        # E = w·s, s = 1
        self.assertGreater(r.delta_penalty, 0.5)
        self.assertGreater(r.current_theta, r.evidence)
        # Θ_base = k·w̄ = 10 (visible while δ = 1, inside the grace window).
        r0 = agg.tick({'p0': 1, 'p1': 0}, 0.0, t_now=18.0)
        self.assertGreater(r0.theta_target, 5.0)

    def test_5e_rejected_channel_keeps_yellow_path(self):
        """An unprofiled channel still raises the local bedside warning (WARN)
        through the ttl_active branch, and never enters M."""
        routing = self.sa.check_alert_validity(
            self.UUID, 'u3', 'MDC.u3', biceps_priority='Hi',
            manufacturer='Stub', model='S1', device_epr=self.handler.epr,
            reliability_profile=None,
        )
        self.assertEqual(routing, 'WARN')
        self.assertTrue(self.sa.is_alarm_active(self.UUID, 'u3'))
        agg = self.sa._ensemble_adaptive_aggregators[self.UUID]
        self.assertNotIn('u3', agg.sensor_ids)
        self.assertEqual(sorted(agg.sensor_ids), ['p0', 'p1'])


# ══════════════════════════════════════════════════════════════════════════════
#  Fix 6 — clear_alarm drives a tick on a partial clear (OFF edge → ZOH)
# ══════════════════════════════════════════════════════════════════════════════

class _FakeClock:
    """Deterministic replacement for time.monotonic() (patched module-wide)."""

    def __init__(self, t0: float = 0.0):
        self.t = t0

    def __call__(self) -> float:
        return self.t


class TestFix6_ClearAlarmDrivesTick(unittest.TestCase):
    UUID = 'ens-fix6-0000-0000-000000000000'

    def setUp(self):
        self.clock = _FakeClock(0.0)
        self._patcher = mock.patch('time.monotonic', self.clock)
        self._patcher.start()
        self.manager = _StubManager()
        self.topology = EnsembleTopologyManager(self.manager, overview_model=None)
        self.sa = SmartAlertAggregator(self.manager, self.topology, overview_model=None)
        self.prof = _profile_with_weight(2.0)
        self.handler = _StubHandler(
            'urn:uuid:dev-A',
            [('A', 'MDC.A', 'Hi', self.prof), ('B', 'MDC.B', 'Hi', self.prof)],
        )
        self.manager.devices[self.handler.epr] = self.handler
        _bind(self.topology, self.UUID, 'PAT-6', 'ICU-6', self.handler.epr)

    def tearDown(self):
        self.sa.stop()
        self._patcher.stop()

    def _fire(self, key: str) -> str:
        return self.sa.check_alert_validity(
            self.UUID, key, f'MDC.{key}', biceps_priority='Hi',
            manufacturer='Stub', model='S1', device_epr=self.handler.epr,
            reliability_profile=self.prof,
        )

    def _agg(self):
        return self.sa._ensemble_adaptive_aggregators[self.UUID]

    def test_6a_partial_clear_records_off_edge(self):
        """A fires for 10 ms and clears while B stays active; the next ensemble
        event is 5 s later.  s_A must be ≈ 0.001 (10 ms / 10 s), not ≈ 0.5."""
        self.clock.t = 0.000;  self._fire('B')
        self.clock.t = 0.005;  self._fire('A')
        self.clock.t = 0.015;  self.sa.clear_alarm(self.UUID, 'A')      # partial
        self.assertTrue(self.sa.is_alarm_active(self.UUID, 'B'))
        self.assertFalse(self.sa.is_alarm_active(self.UUID, 'A'))
        self.clock.t = 5.0;    self._fire('B')                          # next event
        agg = self._agg()
        s_a = agg._evidence.smoothed('A', 5.0)
        s_b = agg._evidence.smoothed('B', 5.0)
        self.assertAlmostEqual(s_a, 0.001, places=6)                     # not ≈ 0.5
        self.assertAlmostEqual(s_b, 0.5, places=6)
        self.assertFalse(agg._sensor_states['A'])                        # a_A = 0 held
        self.assertTrue(agg._sensor_states['B'])
        self.assertAlmostEqual(self.sa._last_tick_ts[self.UUID], 5.0, places=9)

    def test_6b_partial_clear_updates_last_tick_ts_with_clamp(self):
        """The OFF-edge tick books its own timestamp and clamps dt_step."""
        self.clock.t = 0.0;   self._fire('A'); self._fire('B')
        self.clock.t = 100.0                                              # > MAX_DT
        self.sa.clear_alarm(self.UUID, 'A')
        self.assertAlmostEqual(self.sa._last_tick_ts[self.UUID], 100.0, places=9)
        # τ accumulated by the OFF-edge tick is bounded by ADAPTIVE_MAX_DT_STEP.
        self.assertLessEqual(self._agg().active_alarm_duration, self.sa.ADAPTIVE_MAX_DT_STEP)

    def test_6c_full_clear_path_unchanged(self):
        """Clearing the LAST alarm takes the existing reset path: buffers flushed,
        no OFF-edge tick, resolution timestamp stamped."""
        self.clock.t = 0.0;  self._fire('A')
        self.clock.t = 1.0;  self._fire('A')
        agg = self._agg()
        self.assertGreater(agg._evidence.smoothed('A', 1.0), 0.0)
        self.clock.t = 1.5
        self.sa.clear_alarm(self.UUID, 'A')                              # full clear
        self.assertEqual(agg._evidence.smoothed('A', 1.5), 0.0)           # reset()
        self.assertIn(self.UUID, self.sa._ensemble_resolved_ts)
        self.assertAlmostEqual(self.sa._last_tick_ts[self.UUID], 1.0, places=9)  # untouched

    def test_6d_verdict_not_routed_and_latch_untouched(self):
        """On a latched (escalated) ensemble the partial clear still ticks the
        core (state update) but leaves the routing latch alone."""
        with self.sa.lock:
            self.sa._escalated_ensembles.add(self.UUID)
        self.clock.t = 0.0;  self._fire('A'); self._fire('B')
        # Latched path in check_alert_validity does not build an aggregator, so
        # build it explicitly (as an earlier non-latched tick would have).
        with self.sa.lock:
            evs = [ev for ev, _ in self.sa._active_alarms[self.UUID].values()]
        specs = self.topology.get_member_specs(self.UUID)
        with self.sa._adaptive_lock:
            agg = self.sa._get_or_build_adaptive_aggregator_locked(self.UUID, evs, specs, set())
        agg.tick({'A': 1, 'B': 1}, 0.0, t_now=0.0)
        self.clock.t = 2.0
        self.sa.clear_alarm(self.UUID, 'A')                              # partial
        self.assertTrue(self.sa.is_ensemble_escalated(self.UUID))         # latch kept
        self.assertFalse(agg._sensor_states['A'])                         # edge recorded
        self.assertTrue(agg._sensor_states['B'])

    def test_6e_no_aggregator_yet_is_a_noop(self):
        """A partial clear before any aggregator exists must not raise."""
        with self.sa.lock:
            self.sa._active_alarms[self.UUID] = {
                'A': (_evidence(self.UUID, 'A', self.prof), 0.0),
                'B': (_evidence(self.UUID, 'B', self.prof), 0.0),
            }
        self.sa.clear_alarm(self.UUID, 'A')                              # no aggregator
        self.assertNotIn(self.UUID, self.sa._ensemble_adaptive_aggregators)

    def test_6f_no_deadlock_under_concurrency(self):
        """Interleaved check_alert_validity / clear_alarm / GC sweeps from several
        threads must all terminate (lock order self.lock → _adaptive_lock → agg)."""
        self._patcher.stop()                     # real clock for this test
        try:
            stop = threading.Event()
            errors: list = []

            def firer(key):
                while not stop.is_set():
                    try:
                        self._fire(key)
                    except Exception as exc:     # pragma: no cover
                        errors.append(exc)

            def clearer(key):
                while not stop.is_set():
                    try:
                        self.sa.clear_alarm(self.UUID, key)
                    except Exception as exc:     # pragma: no cover
                        errors.append(exc)

            def sweeper():
                while not stop.is_set():
                    try:
                        self.sa._cleanup_stale_alarms()
                    except Exception as exc:     # pragma: no cover
                        errors.append(exc)

            threads = [
                threading.Thread(target=firer, args=('A',), daemon=True),
                threading.Thread(target=firer, args=('B',), daemon=True),
                threading.Thread(target=clearer, args=('A',), daemon=True),
                threading.Thread(target=clearer, args=('B',), daemon=True),
                threading.Thread(target=sweeper, daemon=True),
            ]
            for th in threads:
                th.start()
            threading.Event().wait(0.5)
            stop.set()
            for th in threads:
                th.join(timeout=5.0)
            self.assertTrue(all(not th.is_alive() for th in threads), 'deadlock suspected')
            self.assertEqual(errors, [])
        finally:
            self._patcher.start()


# ══════════════════════════════════════════════════════════════════════════════
#  Fix 8 — ensemble membership must shrink when a device departs
# ══════════════════════════════════════════════════════════════════════════════

class TestFix8_MembershipShrinks(unittest.TestCase):
    """Two devices, two channels each.  Device A: w = {3, 5}, device B: w = {1, 1}.

    A is heterogeneous on purpose: with homogeneous survivors at |M| = 2 the
    melted barrier δ_min·2·w̄ equals the lone active weight (docs/model/SPEC_DELTA.md SD-1)
    and every post-departure assertion would sit on a float knife edge."""

    UUID = 'ens-fix8-0000-0000-000000000000'

    def setUp(self):
        self.clock = _FakeClock(0.0)
        self._patcher = mock.patch('time.monotonic', self.clock)
        self._patcher.start()
        self.manager = _StubManager()
        self.topology = EnsembleTopologyManager(self.manager, overview_model=None)
        self.sa = SmartAlertAggregator(self.manager, self.topology, overview_model=None)
        self.prof_a = _profile_with_weight(3.0)
        self.prof_a1 = _profile_with_weight(5.0)
        self.prof_b = _profile_with_weight(1.0)
        self.dev_a = _StubHandler('urn:uuid:dev-A', [
            ('a0', 'MDC.a0', 'Hi', self.prof_a), ('a1', 'MDC.a1', 'Hi', self.prof_a1)])
        self.dev_b = _StubHandler('urn:uuid:dev-B', [
            ('b0', 'MDC.b0', 'Hi', self.prof_b), ('b1', 'MDC.b1', 'Hi', self.prof_b)])
        for h in (self.dev_a, self.dev_b):
            self.manager.devices[h.epr] = h
        _bind(self.topology, self.UUID, 'PAT-8', 'ICU-8', self.dev_a.epr, self.dev_b.epr)
        self.topology._refresh_ensemble_channel_specs(self.UUID)     # bind-time registry

    def tearDown(self):
        self.sa.stop()
        self._patcher.stop()

    def _fire(self, key: str, prof) -> str:
        return self.sa.check_alert_validity(
            self.UUID, key, f'MDC.{key}', biceps_priority='Hi',
            manufacturer='Stub', model='S1', device_epr='', reliability_profile=prof,
        )

    def _disconnect(self, handler: _StubHandler):
        """Mirror SdcMyConsumer.remove_device(): drop the handler, then release."""
        self.manager.devices.pop(handler.epr, None)
        return self.topology.release_device(handler.epr)

    def _agg(self):
        return self.sa._ensemble_adaptive_aggregators[self.UUID]

    def test_8a_departure_shrinks_M_and_keeps_survivor_history(self):
        """|M| 4 → 2, w̄ 2.5 → 4, FIR history of a0 preserved across the departure."""
        self.clock.t = 0.0;  self._fire('a0', self.prof_a)
        self.clock.t = 1.0;  self._fire('b0', self.prof_b)
        agg = self._agg()
        self.assertEqual(sorted(agg.sensor_ids), ['a0', 'a1', 'b0', 'b1'])
        self.assertAlmostEqual(agg._evidence.w_avg(), 2.5, places=6)
        for t in (2.0, 3.0, 4.0, 5.0):
            self.clock.t = t; self._fire('a0', self.prof_a)
        self.clock.t = 5.5; self.sa.clear_alarm(self.UUID, 'b0')          # B goes quiet
        self.clock.t = 6.0
        released = self._disconnect(self.dev_b)                            # partial departure
        self.assertIsNone(released)                                        # ensemble survives
        self.assertEqual(set(self.topology.get_member_specs(self.UUID)), {'a0', 'a1'})
        self.clock.t = 7.0;  self._fire('a0', self.prof_a)
        agg2 = self._agg()
        self.assertIs(agg2, agg)                                           # same instance
        self.assertEqual(sorted(agg2.sensor_ids), ['a0', 'a1'])            # |M| = 2
        self.assertAlmostEqual(agg2._evidence.w_avg(), 4.0, places=6)      # survivors only
        # a0 has been ON since t = 0: window [−3, 7] ⇒ s_a0 = 7/10 (history kept).
        self.assertAlmostEqual(agg2._evidence.smoothed('a0', 7.0), 0.7, places=6)

    def test_8b_stale_cache_entry_does_not_reset_repeatedly(self):
        """Device B departs while b0 is STILL in the TTL cache.  The registry is
        now {a0, a1}; the cache must not pull b0 back into M as a fallback.

        Observed BEFORE the registry-authoritative rule (Fix 8 alone): b0 came
        back into M with a_j = 1 held by the cache.  Composition then stayed
        {a0, a1, b0}, so update_specs() ran once (not per event), but the dead
        device's evidence (E = 3 + 1 = 4) crossed the melting barrier at t = 13
        (Θ = 3.95) — an escalation driven by a disconnected device.  A second
        reset would have followed when b0's TTL expired.

        Expected now: update_specs() exactly once after the departure; b0 never
        in M; τ grows; δ reaches δ_min; Θ_current melts to δ_min·Θ_base = 4 by
        Fast Attack while E = 3 stays below it (survivors w = {3, 5} → w̄ = 4)."""
        self.sa.ALARM_TTL_SEC = 1000.0            # keep b0 in the cache for the whole trace
        self.clock.t = 0.0;  self._fire('a0', self.prof_a)
        self.clock.t = 1.0;  self._fire('b0', self.prof_b)                 # b0 stays cached
        agg = self._agg()
        self.clock.t = 6.0
        self._disconnect(self.dev_b)
        self.assertTrue(self.sa.is_alarm_active(self.UUID, 'b0'))          # stale entry
        self.assertEqual(set(self.topology.get_member_specs(self.UUID)), {'a0', 'a1'})

        results = []
        real_tick = agg.tick

        def recording_tick(*args, **kwargs):
            r = real_tick(*args, **kwargs)
            results.append(r)
            return r

        with mock.patch.object(agg, 'update_specs', wraps=agg.update_specs) as spy_update, \
             mock.patch.object(agg, 'tick', side_effect=recording_tick):
            for t in range(7, 46):
                self.clock.t = float(t)
                self._fire('a0', self.prof_a)
            n_updates = spy_update.call_count

        trace = [(t, round(r.active_alarm_duration, 1), round(r.delta_penalty, 3),
                  round(r.current_theta, 3)) for t, r in zip(range(7, 46), results)]
        self.assertEqual(sorted(agg.sensor_ids), ['a0', 'a1'], msg=f'trace={trace}')
        self.assertEqual(n_updates, 1, msg=f'update_specs calls={n_updates} trace={trace}')
        self.assertEqual(len(results), 46 - 7, msg=f'trace={trace}')   # every event ticked
        self.assertTrue(all(not r.is_escalated for r in results), msg=f'trace={trace}')
        taus = [r.active_alarm_duration for r in results]
        self.assertEqual(taus, sorted(taus), msg=f'trace={trace}')      # τ never reset
        last = results[-1]
        self.assertGreater(last.active_alarm_duration, 20.0, msg=f'trace={trace}')
        self.assertEqual(last.delta_penalty, 0.5, msg=f'trace={trace}')
        self.assertAlmostEqual(agg._evidence.w_avg(), 4.0, places=6)
        theta_base = last.k_min * agg._evidence.w_avg()                 # 2 · 4 = 8
        self.assertAlmostEqual(last.current_theta, 0.5 * theta_base, places=6,
                               msg=f'trace={trace}')
        self.assertAlmostEqual(last.evidence, 3.0, places=6)            # a0 alone, no b0
        self.assertLess(last.evidence, last.current_theta)


if __name__ == '__main__':
    unittest.main(verbosity=2)
