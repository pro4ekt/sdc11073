"""
tests/test_discovery_filter.py
==============================
Ziel 1 — room filter decided before any subscription, and the event journal.

    R1  location scopes are parsed by sdc11073 (own / foreign / none / mixed /
        malformed)
    R2  decide_room: no filter → accept; unknown → unknown; equal → accept;
        different → reject
    R3  room_from_context_states prefers the associated LocationContextState
    R4  event journal: JSON Lines with monotonic + absolute timestamps
    R5  topology recorder: one device_discovered per EPR, decisions by phase,
        avoided subscriptions, summary
    R6  fallback path (context precheck) decides without the MDIB check:
        DeviceHandler._phase_context_precheck on a stubbed consumer
    R7  2f — an accept by scope is confirmed over the authenticated connection:
        scope == context → accept (scope_confirmed); scope ≠ context, or no
        LocationContext at all → reject with scope_mismatch, no subscription

Run with:  python -m unittest Dashboard.tests.test_discovery_filter -v
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_APP_DIR = _PROJECT_ROOT / 'Dashboard' / 'app'


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
_stub_package('Dashboard.app.metrics', _APP_DIR / 'metrics')
tf = _load_module('Dashboard.app.topology_filter', _APP_DIR / 'topology_filter.py', 'Dashboard.app')
for _name in ('eventlog', 'topology_metrics'):
    _load_module(f'Dashboard.app.metrics.{_name}', _APP_DIR / 'metrics' / f'{_name}.py', 'Dashboard.app.metrics')
eventlog = sys.modules['Dashboard.app.metrics.eventlog']
topo_metrics = sys.modules['Dashboard.app.metrics.topology_metrics']

OWN = 'sdc.ctxt.loc:/sdc.ctxt.loc.detail/%2F%2F%2F%2FICU-1%2F1?rm=ICU-1&bed=1'
FOREIGN = 'sdc.ctxt.loc:/sdc.ctxt.loc.detail/%2F%2F%2F%2FICU-2%2F1?rm=ICU-2&bed=1'
PKP = 'sdc.mds.pkp:1.2.840.10004.20701.1.1'


class TestR1_ScopeParsing(unittest.TestCase):
    def test_R1a_own_room(self):
        self.assertEqual(tf.room_from_scopes([PKP, OWN]), 'ICU-1')
        loc = tf.location_from_scopes([OWN])
        self.assertEqual((loc.rm, loc.bed), ('ICU-1', '1'))

    def test_R1b_foreign_room(self):
        self.assertEqual(tf.room_from_scopes([FOREIGN]), 'ICU-2')

    def test_R1c_no_location_scope(self):
        self.assertIsNone(tf.room_from_scopes([PKP]))
        self.assertIsNone(tf.room_from_scopes([]))
        self.assertIsNone(tf.room_from_scopes(None))

    def test_R1d_malformed_scope_skipped(self):
        self.assertEqual(tf.room_from_scopes(['sdc.ctxt.loc:garbage', OWN]), 'ICU-1')
        self.assertIsNone(tf.room_from_scopes(['sdc.ctxt.loc:/sdc.ctxt.loc.detail/x?bed=1']))  # no rm


class TestR2_Decision(unittest.TestCase):
    def test_R2_decide(self):
        self.assertEqual(tf.decide_room(None, 'ICU-2'), 'accept')     # no filter: baseline mode
        self.assertEqual(tf.decide_room('', None), 'accept')
        self.assertEqual(tf.decide_room('ICU-1', None), 'unknown')
        self.assertEqual(tf.decide_room('ICU-1', 'ICU-1'), 'accept')
        self.assertEqual(tf.decide_room('ICU-1', 'ICU-2'), 'reject')


class _State:
    def __init__(self, localname, room=None, assoc='Assoc'):
        self.NODETYPE = types.SimpleNamespace(localname=localname)
        self.LocationDetail = types.SimpleNamespace(Room=room) if room is not None else None
        self.ContextAssociation = assoc


class TestR3_ContextStates(unittest.TestCase):
    def test_R3_prefers_associated(self):
        states = [_State('PatientContextState'), _State('LocationContextState', 'ICU-9', 'Dis'),
                  _State('LocationContextState', 'ICU-1', 'Assoc')]
        self.assertEqual(tf.room_from_context_states(states), 'ICU-1')
        self.assertEqual(tf.room_from_context_states(states[:2]), 'ICU-9')
        self.assertIsNone(tf.room_from_context_states([_State('PatientContextState')]))
        self.assertIsNone(tf.room_from_context_states([_State('LocationContextState', None)]))


class TestR4_EventLog(unittest.TestCase):
    def test_R4_jsonl_with_timestamps(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'ev.jsonl'
            log = eventlog.EventLog(path)
            log.emit('topology_decision', epr='urn:x', decision='reject', phase='discovery')
            log.emit('subscription_avoided', epr='urn:x', phase='discovery')
            log.close()
            lines = path.read_text(encoding='utf-8').splitlines()
            self.assertEqual(len(lines), 2)
            rec = json.loads(lines[0])
            for k in ('t_mono', 't_abs', 'iso', 'event', 'epr', 'decision', 'phase'):
                self.assertIn(k, rec)
            self.assertIsInstance(rec['t_mono'], float)
            self.assertIsInstance(rec['t_abs'], float)
            self.assertEqual(json.loads(lines[1])['event'], 'subscription_avoided')


class TestR5_TopologyRecorder(unittest.TestCase):
    def test_R5_counters_and_dedup(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'ev.jsonl'
            eventlog.configure(path)
            tm = topo_metrics.TopologyMetrics()
            tm.record_discovered('A', [OWN], 'ICU-1')
            tm.record_discovered('A', [OWN], 'ICU-1')          # same Hello again → no event
            tm.record_discovered('B', [FOREIGN], 'ICU-2')
            tm.record_discovered('C', [PKP], None)
            tm.record_decision('A', 'accept', 'discovery', 'scope_room_match', 'ICU-1', 'ICU-1')
            tm.record_decision('B', 'reject', 'discovery', 'scope_room_mismatch', 'ICU-2', 'ICU-1')
            tm.record_subscription_avoided('B', 'discovery', 'scope_room_mismatch')
            tm.record_decision('C', 'unknown', 'context_precheck', 'no_location', None, 'ICU-1')
            tm.record_subscription_created('A', 5, 'discovery')
            tm.record_subscription_created('C', 5, 'context_precheck')
            tm.record_departed('B', 'location')
            s = tm.emit_summary()
            eventlog.get_event_log().close()
            self.assertEqual(s['discovered'], 3)
            self.assertEqual(s['decisions'], {'accept': 1, 'reject': 1, 'unknown': 1})
            self.assertEqual(s['subscriptions_created'], 2)
            self.assertEqual(s['actions_subscribed'], 10)
            self.assertEqual(s['subscriptions_avoided'], 1)
            events = [json.loads(l)['event'] for l in path.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(events.count('device_discovered'), 3)
            self.assertEqual(events[-1], 'topology_summary')
            eventlog.configure(None)


class TestR6_PrecheckPath(unittest.TestCase):
    """DeviceHandler._phase_context_precheck with a stubbed consumer: the room
    decision is taken from GetContextStates, without GetMdib and before any
    subscription (the stub counts do_subscribe calls)."""

    @classmethod
    def setUpClass(cls):
        try:
            # 'app' must be imported first (device → app → device cycle), as main.py does.
            import app  # noqa: F401
            from device.handler import DeviceHandler  # needs sdc11073 + PySide6
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f'device.handler not importable here: {exc}')
        cls.DeviceHandler = DeviceHandler
        # The handler writes through the ``app.metrics`` singleton (not the
        # ``Dashboard.app.metrics`` copy loaded above), so that one is redirected.
        from app.metrics import eventlog as app_eventlog
        cls._app_eventlog = app_eventlog
        cls._tmp = tempfile.TemporaryDirectory()
        app_eventlog.configure(Path(cls._tmp.name) / 'r6.jsonl')   # keep logs/ clean

    @classmethod
    def tearDownClass(cls):
        cls._app_eventlog.get_event_log().close()
        cls._app_eventlog.configure(None)
        cls._tmp.cleanup()

    def _handler(self, target_room, states, scope_room=None):
        h = self.DeviceHandler.__new__(self.DeviceHandler)   # no thread init
        h.epr = f'urn:uuid:test-precheck-{self._testMethodName}'   # distinct per test (decision dedupe per EPR)
        h.target_room = target_room
        h.scope_room = scope_room
        h._precheck = True
        h._reject_phase = ''
        h._location_filtered = False
        h.manager = types.SimpleNamespace(_rejected_room_map={}, register_device_room=lambda e, r: None)
        import logging
        h.logger = logging.getLogger('test.precheck')
        calls = {'subscribe': 0}
        ctx = types.SimpleNamespace(get_context_states=lambda: types.SimpleNamespace(
            result=types.SimpleNamespace(ContextState=states)))
        h.consumer = types.SimpleNamespace(context_service_client=ctx)
        return h, calls

    def test_R6a_foreign_room_rejected_before_subscription(self):
        h, _ = self._handler('ICU-1', [_State('LocationContextState', 'ICU-2')])
        self.assertTrue(h._phase_context_precheck())
        self.assertTrue(h._location_filtered)
        self.assertEqual(h._reject_phase, 'context_precheck')
        self.assertEqual(h.manager._rejected_room_map[h.epr], 'ICU-2')

    def test_R6b_own_room_accepted(self):
        h, _ = self._handler('ICU-1', [_State('LocationContextState', 'ICU-1')])
        self.assertFalse(h._phase_context_precheck())
        self.assertFalse(h._location_filtered)

    def test_R6c_no_location_is_unknown_and_accepted(self):
        h, _ = self._handler('ICU-1', [_State('PatientContextState')])
        self.assertFalse(h._phase_context_precheck())
        self.assertFalse(h._location_filtered)

    # ── R7: confirmation of a scope accept (2f) ──────────────────────────────

    def _journal_events(self):
        path = self._app_eventlog.get_event_log().path
        return [json.loads(l) for l in Path(path).read_text(encoding='utf-8').splitlines()]

    def test_R7a_scope_confirmed_by_context(self):
        h, _ = self._handler('ICU-1', [_State('LocationContextState', 'ICU-1')], scope_room='ICU-1')
        self.assertFalse(h._phase_context_precheck())
        self.assertFalse(h._location_filtered)
        ev = [e for e in self._journal_events() if e.get('epr') == h.epr]
        self.assertEqual(ev[-1]['event'], 'topology_decision')
        self.assertEqual((ev[-1]['decision'], ev[-1]['reason']), ('accept', 'scope_confirmed'))

    def test_R7b_scope_mismatch_rejected_without_subscription(self):
        h, _ = self._handler('ICU-1', [_State('LocationContextState', 'ICU-2')], scope_room='ICU-1')
        self.assertTrue(h._phase_context_precheck())
        self.assertTrue(h._location_filtered)
        self.assertEqual(h._reject_phase, 'context_precheck')
        self.assertEqual(h.manager._rejected_room_map[h.epr], 'ICU-2')
        ev = [e for e in self._journal_events() if e.get('epr') == h.epr]
        kinds = [e['event'] for e in ev[-3:]]
        self.assertEqual(kinds, ['scope_mismatch', 'topology_decision', 'subscription_avoided'])
        self.assertEqual((ev[-3]['scope_room'], ev[-3]['context_room']), ('ICU-1', 'ICU-2'))
        self.assertEqual((ev[-2]['decision'], ev[-2]['phase'], ev[-2]['reason']),
                         ('reject', 'context_precheck', 'scope_mismatch'))

    def test_R7c_scope_without_any_location_context_is_mismatch(self):
        h, _ = self._handler('ICU-1', [_State('PatientContextState')], scope_room='ICU-1')
        self.assertTrue(h._phase_context_precheck())
        self.assertTrue(h._location_filtered)
        ev = [e for e in self._journal_events() if e.get('epr') == h.epr]
        self.assertEqual(ev[-3]['event'], 'scope_mismatch')
        self.assertIsNone(ev[-3]['context_room'])


if __name__ == '__main__':
    unittest.main()
