"""
tests/test_presence_truth.py
============================
Fix 11 — a_j from device truth (AlertConditionState.Presence in the consumer
MDIB) at every evaluation instant, with the TTL cache refreshed for conditions
that are still present (thesis 3.3: a source holds its state until the next
report).  Fix 12 — latched ensembles are evaluated (not routed) during the
latch (thesis 3.4.6).  Fix 13 — channel key = (device EPR, handle).

    Q1  a sustained alarm (no further reports, GC sweeps every 5 s) stays in the
        aggregation for 60 s; escalation is declared via T_P and HELD
    Q2  an OFF report clears the channel as before (forced 0, partial clear)
    Q3  a busy device (snapshot None) keeps the last known values, never 0
    Q4  Presence false on a readable device drives a_j = 0 even while cached
    Q5  during the latch E and Θ_current keep updating, the card stays Red,
        and only clearing every condition releases it (Blue)
    Q6  the event path of a latched ensemble ticks too (no routing)
    Q7  two devices with identical handles give |M| = 2 × channels
    Q8  alert_key_for / device_of_alert_key
    Q9  a departed device's keys still leave the cache (Fix 9 unchanged)

Run with:  python -m unittest Dashboard.tests.test_presence_truth -v
"""

from __future__ import annotations

import importlib.util
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

from Dashboard.app.alarms.device_profile_repo import DeviceReliabilityProfile  # noqa: E402
from Dashboard.app.alarms.alarmCoordinator import alert_key_for, device_of_alert_key  # noqa: E402
from Dashboard.app.alarms.smartAlertAggregator import SmartAlertAggregator  # noqa: E402
from Dashboard.app.alarms.ensemble_topology_manager import EnsembleTopologyManager  # noqa: E402


def _profile_with_weight(w: float, tpr: float = 0.9) -> DeviceReliabilityProfile:
    return DeviceReliabilityProfile(true_positive_rate=tpr, false_positive_rate=tpr / math.exp(w))


class _Device:
    """DeviceHandler double with a consumer-MDIB-like Presence map and the
    composite channel key (epr, handle)."""

    def __init__(self, epr: str, handles, prio: str, w: float):
        self.epr = epr
        self.prof = _profile_with_weight(w)
        self.prio = prio
        self.handles = list(handles)
        self.presence = {h: False for h in self.handles}
        self.busy = False
        self.ensemble_uuid: Optional[str] = None

    def key(self, h: str) -> str:
        return alert_key_for(self.epr, h)

    def enumerate_alert_channels(self):
        return [(self.key(h), f'MDC.{h}', self.prio, self.prof) for h in self.handles]

    def snapshot_alert_presence(self):
        if self.busy:
            return None
        return {self.key(h): v for h, v in self.presence.items()}


class _StubManager:
    def __init__(self):
        self.devices: dict = {}


class _FakeClock:
    def __init__(self, t0: float = 0.0):
        self.t = t0

    def __call__(self) -> float:
        return self.t


class _Overview:
    def __init__(self):
        self.calls: list = []

    def updateEnsemble(self, uuid, name, room, count, escalated, sdc, has_active=False):
        self.calls.append(('update', escalated, has_active))

    def removeEnsemble(self, uuid):
        self.calls.append(('remove',))


class _Base(unittest.TestCase):
    UUID = 'ens-truth-0000-0000-000000000000'

    def _setup(self, devices):
        self.clock = _FakeClock(0.0)
        self._patcher = mock.patch('time.monotonic', self.clock)
        self._patcher.start()
        self.manager = _StubManager()
        self.topology = EnsembleTopologyManager(self.manager, overview_model=None)
        self.overview = _Overview()
        self.sa = SmartAlertAggregator(self.manager, self.topology,
                                       overview_model=self.overview, periodic_eval=False)
        self.devices = devices
        for d in devices:
            self.manager.devices[d.epr] = d
        with self.topology.lock:
            self.topology._patient_to_ensemble_map[('PAT-Q', 'ICU-Q')] = self.UUID
            self.topology._ensembles_devices[self.UUID] = {d.epr for d in devices}

    def tearDown(self):
        if hasattr(self, 'sa'):
            self.sa.stop()
            self._patcher.stop()

    def _on(self, dev: _Device, h: str) -> str:
        """Simulate an ON report: MDIB first (as sdc11073 does), then the callback."""
        dev.presence[h] = True
        return self.sa.check_alert_validity(
            self.UUID, dev.key(h), f'MDC.{h}', biceps_priority=dev.prio,
            manufacturer='Stub', model='S1', device_epr=dev.epr, reliability_profile=dev.prof,
        )

    def _off(self, dev: _Device, h: str) -> None:
        dev.presence[h] = False
        self.sa.clear_alarm(self.UUID, dev.key(h))

    def _agg(self):
        return self.sa._ensemble_adaptive_aggregators[self.UUID]

    def _latched(self) -> bool:
        return self.sa.is_ensemble_escalated(self.UUID)

    def _run(self, t_from: float, t_to: float, gc_every: float = 5.0):
        """Grid passes every 1 s with GC sweeps every ``gc_every`` s."""
        t = t_from
        first_latch = None
        while t <= t_to + 1e-9:
            self.clock.t = t
            self.sa.evaluate_periodic()
            if gc_every and abs(t % gc_every) < 1e-9:
                self.sa._cleanup_stale_alarms()
            if first_latch is None and self._latched():
                first_latch = t
            t += 1.0
        return first_latch


class TestQ1_SustainedAlarmHeld(_Base):
    def test_Q1_sustained_alarm_stays_and_escalation_holds_60s(self):
        a = _Device('urn:uuid:A', ['hr'], 'Hi', 2.0)
        b = _Device('urn:uuid:B', ['hr'], 'Hi', 2.0)
        c = _Device('urn:uuid:C', ['hr'], 'Hi', 2.0)
        self._setup([a, b, c])
        for d in (a, b, c):
            self.assertEqual(self._on(d, 'hr'), 'WARN')
        t_esc = self._run(1.0, 60.0)
        self.assertEqual(t_esc, 7.0)
        self.assertTrue(self._latched(), 'latch released although all conditions are still present')
        self.assertEqual(sorted(self.sa._active_alarms[self.UUID]), sorted(d.key('hr') for d in (a, b, c)))
        # the cache stamps follow the periodic passes (refreshed), not the ON report
        self.assertTrue(all(ts == 60.0 for _ev, ts in self.sa._active_alarms[self.UUID].values()))
        self.assertAlmostEqual(self._agg()._evidence.evidence(60.0), 6.0, places=6)   # 3 · w · 1
        reds = [i for i, c in enumerate(self.overview.calls) if c == ('update', True, True)]
        self.assertTrue(reds, 'no Red push')
        self.assertEqual(self.overview.calls[reds[0]:], [('update', True, True)],
                         'a push after the latch (latched ensembles are not routed)')


class TestQ2_OffReportClears(_Base):
    def test_Q2_off_reports_clear_channels_and_resolve(self):
        a = _Device('urn:uuid:A', ['hr'], 'Hi', 2.0)
        b = _Device('urn:uuid:B', ['hr'], 'Hi', 2.0)
        c = _Device('urn:uuid:C', ['hr'], 'Hi', 2.0)
        self._setup([a, b, c])
        for d in (a, b, c):
            self._on(d, 'hr')
        self._run(1.0, 20.0)
        self.assertTrue(self._latched())
        self.clock.t = 21.0
        self._off(a, 'hr')                                   # partial: forced 0 for A
        agg = self._agg()
        self.assertFalse(agg._sensor_states[a.key('hr')])
        self.assertTrue(agg._sensor_states[b.key('hr')])
        self.assertTrue(self._latched())                     # still two contributing
        self.clock.t = 22.0
        self._off(b, 'hr')
        self.clock.t = 23.0
        self._off(c, 'hr')                                   # last one → resolved
        self.assertFalse(self._latched())
        self.assertFalse(self.sa._active_alarms.get(self.UUID))
        self.assertEqual(self.overview.calls[-1], ('update', False, False))   # Blue


class TestQ3_BusyDeviceHoldsLastKnown(_Base):
    def test_Q3_busy_mdib_keeps_last_known_value(self):
        a = _Device('urn:uuid:A', ['hr'], 'Hi', 2.0)
        b = _Device('urn:uuid:B', ['hr'], 'Hi', 2.0)
        self._setup([a, b])
        self._on(a, 'hr'); self._on(b, 'hr')
        self._run(1.0, 3.0, gc_every=0)
        agg = self._agg()
        self.assertTrue(agg._sensor_states[a.key('hr')] and agg._sensor_states[b.key('hr')])
        a.busy = True                                        # MDIB lock taken
        self._run(4.0, 6.0, gc_every=0)
        self.assertTrue(agg._sensor_states[a.key('hr')], 'busy device was reset to 0')
        # not refreshed while busy: A's stamp stays at the last readable pass
        stamps = {k: ts for k, (_e, ts) in self.sa._active_alarms[self.UUID].items()}
        self.assertEqual(stamps[a.key('hr')], 3.0)
        self.assertEqual(stamps[b.key('hr')], 6.0)


class TestQ4_PresenceFalseWins(_Base):
    def test_Q4_presence_false_drives_zero_even_while_cached(self):
        a = _Device('urn:uuid:A', ['hr'], 'Hi', 2.0)
        b = _Device('urn:uuid:B', ['hr'], 'Hi', 2.0)
        self._setup([a, b])
        self._on(a, 'hr'); self._on(b, 'hr')
        self._run(1.0, 3.0, gc_every=0)
        a.presence['hr'] = False                             # device cleared, OFF report lost
        self.clock.t = 4.0
        self.sa.evaluate_periodic()
        self.assertFalse(self._agg()._sensor_states[a.key('hr')])
        self.assertIn(a.key('hr'), self.sa._active_alarms[self.UUID])   # left to the TTL
        self._run(5.0, 14.0, gc_every=0)                     # b refreshed every pass, a not
        self.sa._cleanup_stale_alarms()                      # t = 14: a (stamp 3) expired
        self.assertNotIn(a.key('hr'), self.sa._active_alarms[self.UUID])
        self.assertIn(b.key('hr'), self.sa._active_alarms[self.UUID])   # stamp 14, kept


class TestQ5_EvaluationDuringLatch(_Base):
    def test_Q5_latched_ensemble_keeps_computing_card_stays_red(self):
        a = _Device('urn:uuid:A', ['hr'], 'Hi', 2.0)
        b = _Device('urn:uuid:B', ['hr'], 'Hi', 2.0)
        c = _Device('urn:uuid:C', ['hr'], 'Hi', 2.0)
        self._setup([a, b, c])
        for d in (a, b, c):
            self._on(d, 'hr')
        t_esc = self._run(1.0, 7.0, gc_every=0)
        self.assertEqual(t_esc, 7.0)
        agg = self._agg()
        e7, th7 = agg._evidence.evidence(7.0), agg._hysteresis.current
        n_calls = len(self.overview.calls)
        self._run(8.0, 30.0, gc_every=0)
        self.assertEqual(self.sa._last_tick_ts[self.UUID], 30.0)        # ticks continued
        self.assertGreater(agg._evidence.evidence(30.0), e7)             # window filled: 6.0
        self.assertAlmostEqual(agg._evidence.evidence(30.0), 6.0, places=6)
        # Θ_target = 2w̄ = 4 is constant here (SDC = 1, δ = 1 because Δt_active is
        # held at 7 s while the core is escalated, 3.4.5) → Θ_current stays 4.0.
        self.assertAlmostEqual(agg._hysteresis.current, 4.0, places=6)
        self.assertAlmostEqual(agg.active_alarm_duration, 7.0, places=6)
        self.assertTrue(self._latched())
        self.assertEqual(len(self.overview.calls), n_calls, 'latched ensemble was routed')
        for d in (a, b, c):
            self.clock.t += 1.0
            self._off(d, 'hr')
        self.assertFalse(self._latched())
        self.assertEqual(self.overview.calls[-1], ('update', False, False))


class TestQ6_EventPathDuringLatch(_Base):
    def test_Q6_event_on_latched_ensemble_ticks_without_routing(self):
        a = _Device('urn:uuid:A', ['hr', 'rr'], 'Hi', 2.0)
        b = _Device('urn:uuid:B', ['hr'], 'Hi', 2.0)
        c = _Device('urn:uuid:C', ['hr'], 'Hi', 2.0)
        self._setup([a, b, c])
        for d in (a, b, c):
            self._on(d, 'hr')
        self._run(1.0, 9.0, gc_every=0)
        self.assertTrue(self._latched())
        n_calls = len(self.overview.calls)
        self.clock.t = 12.0
        self.assertEqual(self._on(a, 'rr'), 'ESCALATE')
        self.assertEqual(self.sa._last_tick_ts[self.UUID], 12.0)
        self.assertTrue(self._agg()._sensor_states[a.key('rr')])
        self.assertEqual(len(self.overview.calls), n_calls)


class TestQ7_CompositeKeys(_Base):
    def test_Q7_identical_mdibs_give_separate_channels(self):
        a = _Device('urn:uuid:A', ['hr', 'rr'], 'Hi', 2.0)
        b = _Device('urn:uuid:B', ['hr', 'rr'], 'Hi', 2.0)
        self._setup([a, b])
        specs = self.topology.get_member_specs(self.UUID)
        self.assertEqual(len(specs), 4)
        self.assertEqual(set(specs), {a.key('hr'), a.key('rr'), b.key('hr'), b.key('rr')})
        self._on(a, 'hr'); self._on(b, 'hr')
        self.assertEqual(len(self.sa._active_alarms[self.UUID]), 2)
        self.assertEqual(len(self._agg().sensor_ids), 4)

    def test_Q8_key_helpers(self):
        k = alert_key_for('urn:uuid:X', 'ecg_alert_cond_tachycardia')
        self.assertEqual(k, 'urn:uuid:X|ecg_alert_cond_tachycardia')
        self.assertEqual(device_of_alert_key(k), 'urn:uuid:X')
        self.assertIsNone(device_of_alert_key('bare_handle'))


class TestQ9_DepartureStillClears(_Base):
    def test_Q9_departed_keys_leave_cache(self):
        a = _Device('urn:uuid:A', ['hr'], 'Hi', 2.0)
        b = _Device('urn:uuid:B', ['hr'], 'Hi', 2.0)
        self._setup([a, b])
        self._on(a, 'hr'); self._on(b, 'hr')
        self._run(1.0, 3.0, gc_every=0)
        departed = [k for k, *_ in b.enumerate_alert_channels()]
        del self.manager.devices[b.epr]
        self.topology.release_device(b.epr)
        self.sa.on_member_departed(self.UUID, orphaned=False, departed_alert_keys=departed)
        self.assertEqual(set(self.sa._active_alarms[self.UUID]), {a.key('hr')})


if __name__ == '__main__':
    unittest.main()
