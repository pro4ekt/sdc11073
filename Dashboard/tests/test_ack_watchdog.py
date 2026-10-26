"""
tests/test_ack_watchdog.py
==========================
Ziel 2 — remote acknowledgement (Fix 16), technical alarm on connection loss
(Fix 17) and the watchdog (Fix 18).  Needs sdc11073 + PySide6 and the
``Dashboard/`` working directory (the handler imports ``app`` and ``device``).

    K1  AlarmManager.acknowledge_alarm evaluates the final InvocationState:
        Fin / FinMod → accepted (ok), Fail / Cnclld / CnclldMan → failed,
        no final report → timeout, exception → error, no SetService →
        unavailable (nothing sent, no ack_rtt); every ack_rtt carries epr,
        operation_handle, invocation_state, transaction_id, press_id (A4)
    K2  reactivate_alarm (ack-timeout re-raise) uses the same evaluation (A2);
        find_operation_handle has no fallback to a foreign operation
    K3  DeviceHandler reports Presence=Ack from the provider as ack_confirmed
        and forwards it to the Qt handler
    K4  QtDeviceHandler.silenceAlarm: only active signals with their own
        SetAlertStateOperation; one transaction per signal, one press_id per
        press; no fallback to the first signal / operation; ackState for the UI

Run with (from Dashboard/):  ..\\.venv\\Scripts\\python -m unittest tests.test_ack_watchdog -v
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import tempfile
import threading
import types
import unittest
from pathlib import Path


def _bootstrap():
    """Import the production modules the way main.py does (app first)."""
    import app  # noqa: F401  (device → app → device cycle)
    from device.alarm_manager import AlarmManager
    from device.handler import DeviceHandler
    from app.qtDeviceHandler import QtDeviceHandler
    from app.metrics import eventlog as app_eventlog
    from sdc11073.xml_types.msg_types import InvocationState
    from sdc11073.xml_types import pm_qnames as pm
    from sdc11073.xml_types import pm_types
    return AlarmManager, DeviceHandler, QtDeviceHandler, app_eventlog, InvocationState, pm, pm_types


class _JournalMixin:
    """Redirect the app.metrics journal to a temp file and read it back."""

    @classmethod
    def _journal_setup(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._eventlog.configure(Path(cls._tmp.name) / f'{cls.__name__}.jsonl')

    @classmethod
    def _journal_teardown(cls):
        cls._eventlog.get_event_log().close()
        cls._eventlog.configure(None)
        cls._tmp.cleanup()

    def _events(self, **match):
        path = self._eventlog.get_event_log().path
        if not Path(path).exists():
            return []
        out = []
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            rec = json.loads(line)
            if all(rec.get(k) == v for k, v in match.items()):
                out.append(rec)
        return out


def _result(InvocationState, state, tid=42, error=None, messages=()):
    info = types.SimpleNamespace(
        InvocationState=state, TransactionId=tid, InvocationError=error,
        InvocationErrorMessage=[types.SimpleNamespace(text=m) for m in messages],
    )
    return types.SimpleNamespace(InvocationInfo=info)


def _stub_handler(set_alert_state, epr='urn:uuid:test-ack', with_set_service=True):
    """Minimal DeviceHandler stand-in for AlarmManager (two-phase pattern)."""
    h = types.SimpleNamespace()
    h.epr = epr
    h.data_lock = threading.Lock()
    h.logger = logging.getLogger('test.ack')
    h.mdib = types.SimpleNamespace(
        xtra=types.SimpleNamespace(
            mk_proposed_state=lambda handle: types.SimpleNamespace(DescriptorHandle=handle, Presence=None)),
        descriptions=types.SimpleNamespace(NODETYPE={}),
    )
    client = types.SimpleNamespace(set_alert_state=set_alert_state) if with_set_service else None
    h.consumer = types.SimpleNamespace(set_service_client=client)
    return h


class TestK1_AckInvocationState(_JournalMixin, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            (cls.AlarmManager, cls.DeviceHandler, cls.QtDeviceHandler, cls._eventlog,
             cls.InvocationState, cls.pm, cls.pm_types) = _bootstrap()
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f'production modules not importable here: {exc}')
        cls._journal_setup()

    @classmethod
    def tearDownClass(cls):
        cls._journal_teardown()

    def _manager_with_future(self, future, epr=None, with_set_service=True):
        calls = []

        def set_alert_state(op_handle, proposed):
            calls.append((op_handle, proposed.DescriptorHandle, str(proposed.Presence)))
            return future

        epr = epr or f'urn:uuid:{self._testMethodName}'
        handler = _stub_handler(set_alert_state, epr=epr, with_set_service=with_set_service)
        mgr = self.AlarmManager(handler)
        mgr.ACK_WAIT_SEC = 0.05
        return mgr, calls, epr

    def _done(self, state, **kw):
        f = concurrent.futures.Future()
        f.set_result(_result(self.InvocationState, state, **kw))
        return f

    def test_K1a_fin_is_accepted(self):
        mgr, calls, epr = self._manager_with_future(self._done(self.InvocationState.FINISHED, tid=7))
        out = mgr.acknowledge_alarm('op1', 'sig1', press_id='p1')
        self.assertEqual(out, 'accepted')
        self.assertEqual(calls, [('op1', 'sig1', str(self.pm_types.AlertSignalPresence.ACK))])
        ev = self._events(event='ack_rtt', epr=epr)
        self.assertEqual(len(ev), 1)
        rec = ev[0]
        self.assertTrue(rec['ok'])
        self.assertEqual(rec['outcome'], 'accepted')
        self.assertEqual(rec['invocation_state'], 'Fin')
        self.assertEqual(rec['transaction_id'], 7)
        self.assertEqual(rec['operation_handle'], 'op1')
        self.assertEqual(rec['handle'], 'sig1')
        self.assertEqual(rec['press_id'], 'p1')
        self.assertEqual(rec['purpose'], 'ack')
        self.assertIn('t_request_abs', rec)
        self.assertLessEqual(rec['t_request_abs'], rec['t_abs'] + 0.001)   # rounded to ms

    def test_K1b_finmod_is_accepted(self):
        mgr, _calls, epr = self._manager_with_future(self._done(self.InvocationState.FINISHED_MOD))
        self.assertEqual(mgr.acknowledge_alarm('op1', 'sig1'), 'accepted')
        rec = self._events(event='ack_rtt', epr=epr)[-1]
        self.assertTrue(rec['ok'])
        self.assertEqual(rec['invocation_state'], 'FinMod')

    def test_K1c_fail_is_failed_not_success(self):
        """The 27.09 defect: a provider Fail was logged as success."""
        mgr, _calls, epr = self._manager_with_future(self._done(
            self.InvocationState.FAILED, tid=9, error='Oth', messages=['refused by policy']))
        with self.assertLogs('test.ack', level='ERROR') as cm:
            out = mgr.acknowledge_alarm('op1', 'sig1')
        self.assertEqual(out, 'failed')
        self.assertTrue(any('REFUSED' in m and 'Fail' in m for m in cm.output), cm.output)
        rec = self._events(event='ack_rtt', epr=epr)[-1]
        self.assertFalse(rec['ok'])
        self.assertEqual(rec['outcome'], 'failed')
        self.assertEqual(rec['invocation_state'], 'Fail')
        self.assertEqual(rec['transaction_id'], 9)

    def test_K1d_cancelled_states_are_failed(self):
        for state in (self.InvocationState.CANCELLED, self.InvocationState.CANCELLED_MANUALLY):
            mgr, _c, _e = self._manager_with_future(self._done(state))
            self.assertEqual(mgr.acknowledge_alarm('op1', 'sig1'), 'failed', state)

    def test_K1e_no_final_report_is_timeout(self):
        pending = concurrent.futures.Future()          # never resolved: Wait/Start only
        mgr, _calls, epr = self._manager_with_future(pending)
        out = mgr.acknowledge_alarm('op1', 'sig1')
        self.assertEqual(out, 'timeout')
        rec = self._events(event='ack_rtt', epr=epr)[-1]
        self.assertFalse(rec['ok'])
        self.assertEqual(rec['outcome'], 'timeout')
        self.assertIsNone(rec['invocation_state'])

    def test_K1f_exception_is_error(self):
        def boom(_op, _state):
            raise ConnectionError('socket closed')
        handler = _stub_handler(boom, epr=f'urn:uuid:{self._testMethodName}')
        mgr = self.AlarmManager(handler)
        self.assertEqual(mgr.acknowledge_alarm('op1', 'sig1'), 'error')
        rec = self._events(event='ack_rtt', epr=handler.epr)[-1]
        self.assertEqual(rec['outcome'], 'error')
        self.assertFalse(rec['ok'])

    def test_K1g_no_set_service_sends_nothing(self):
        mgr, calls, epr = self._manager_with_future(self._done(self.InvocationState.FINISHED),
                                                    with_set_service=False)
        self.assertEqual(mgr.acknowledge_alarm('op1', 'sig1'), 'unavailable')
        self.assertEqual(calls, [])
        self.assertEqual(self._events(event='ack_rtt', epr=epr), [])

    def test_K1h_summary_counts_by_outcome(self):
        from app.metrics.latency_metrics import LatencyMetrics
        lm = LatencyMetrics()
        t0 = lm.ack_start('h')
        lm.ack_stop('h', t0, ok=True, outcome='accepted', epr='e')
        lm.ack_stop('h', t0, ok=False, outcome='failed', epr='e')
        lm.ack_stop('h', t0, ok=False, outcome='timeout', epr='e')
        lm.record_ack_confirmed('e', 'h')
        s = lm.summary()
        self.assertEqual((s['ack_ok'], s['ack_fail'], s['ack_confirmed']), (1, 2, 1))
        self.assertEqual(s['ack_by_outcome'], {'accepted': 1, 'failed': 1, 'timeout': 1})
        self.assertIsNotNone(s['rtt_ms_p95'])


class TestK2_Reactivate(_JournalMixin, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            (cls.AlarmManager, cls.DeviceHandler, cls.QtDeviceHandler, cls._eventlog,
             cls.InvocationState, cls.pm, cls.pm_types) = _bootstrap()
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f'production modules not importable here: {exc}')
        cls._journal_setup()

    @classmethod
    def tearDownClass(cls):
        cls._journal_teardown()

    def test_K2a_reraise_fail_is_failed(self):
        f = concurrent.futures.Future()
        f.set_result(_result(self.InvocationState, self.InvocationState.FAILED))
        calls = []
        handler = _stub_handler(lambda op, st: (calls.append((op, st.DescriptorHandle, str(st.Presence))), f)[1],
                                epr=f'urn:uuid:{self._testMethodName}')
        mgr = self.AlarmManager(handler)
        self.assertEqual(mgr.reactivate_alarm('op1', 'sig1'), 'failed')
        self.assertEqual(calls, [('op1', 'sig1', str(self.pm_types.AlertSignalPresence.ON))])
        rec = self._events(event='ack_rtt', epr=handler.epr)[-1]
        self.assertEqual((rec['purpose'], rec['outcome'], rec['ok']), ('reraise', 'failed', False))

    def test_K2b_reraise_fin_is_accepted(self):
        f = concurrent.futures.Future()
        f.set_result(_result(self.InvocationState, self.InvocationState.FINISHED))
        handler = _stub_handler(lambda op, st: f, epr=f'urn:uuid:{self._testMethodName}')
        self.assertEqual(self.AlarmManager(handler).reactivate_alarm('op1', 'sig1'), 'accepted')

    def test_K2c_find_operation_handle_has_no_fallback(self):
        handler = _stub_handler(lambda op, st: None)
        ops = [types.SimpleNamespace(Handle='opA', OperationTarget='sigA'),
               types.SimpleNamespace(Handle='opB', OperationTarget='sigB')]
        handler.mdib.descriptions = types.SimpleNamespace(
            NODETYPE={self.pm.SetAlertStateOperationDescriptor: ops})
        mgr = self.AlarmManager(handler)
        self.assertEqual(mgr.find_operation_handle('sigB'), 'opB')
        self.assertIsNone(mgr.find_operation_handle('sigZ'))   # before Fix 16: 'opA'


class TestK3_AckConfirmed(_JournalMixin, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            (cls.AlarmManager, cls.DeviceHandler, cls.QtDeviceHandler, cls._eventlog,
             cls.InvocationState, cls.pm, cls.pm_types) = _bootstrap()
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f'production modules not importable here: {exc}')
        cls._journal_setup()

    @classmethod
    def tearDownClass(cls):
        cls._journal_teardown()

    def test_K3_presence_ack_emits_ack_confirmed(self):
        h = self.DeviceHandler.__new__(self.DeviceHandler)
        h.epr = f'urn:uuid:{self._testMethodName}'
        h.logger = logging.getLogger('test.k3')
        h.alarm_manager = self.AlarmManager(h)
        confirmed = []
        h.qtDeviceHandler = types.SimpleNamespace(on_ack_confirmed=confirmed.append)
        h._log_signal_transition('sig1', self.pm_types.AlertSignalPresence.ACK, 'Signal',
                                 '', '', 'dev', '', now=100.0)
        ev = self._events(event='ack_confirmed', epr=h.epr)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]['handle'], 'sig1')
        self.assertEqual(confirmed, ['sig1'])
        self.assertTrue(h.alarm_manager.is_tracked('sig1'))   # ack-timeout countdown unchanged


class _Desc:
    def __init__(self, handle, cond=None, target=None):
        self.Handle = handle
        self.ConditionSignaled = cond
        self.OperationTarget = target


class _State:
    def __init__(self, nodetype, handle, presence):
        self.NODETYPE = nodetype
        self.DescriptorHandle = handle
        self.Presence = presence


class _Mdib:
    """Enough of ConsumerMdib for _select_ack_targets."""

    def __init__(self, pm, states, descs, ops):
        self.states = types.SimpleNamespace(
            objects=states,
            descriptor_handle=types.SimpleNamespace(
                get_one=lambda h, allow_none=False: next((s for s in states if s.DescriptorHandle == h), None)),
        )
        self.descriptions = types.SimpleNamespace(
            handle=types.SimpleNamespace(
                get_one=lambda h, allow_none=False: next((d for d in descs if d.Handle == h), None)),
            NODETYPE={pm.SetAlertStateOperationDescriptor: ops},
        )


class TestK4_SilenceAlarmSelection(_JournalMixin, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            (cls.AlarmManager, cls.DeviceHandler, cls.QtDeviceHandler, cls._eventlog,
             cls.InvocationState, cls.pm, cls.pm_types) = _bootstrap()
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f'production modules not importable here: {exc}')
        cls._journal_setup()

    @classmethod
    def tearDownClass(cls):
        cls._journal_teardown()

    def _qt(self, states, descs, ops, suppressed=(), outcome='accepted'):
        pm = self.pm
        device = types.SimpleNamespace()
        device.epr = f'urn:uuid:{self._testMethodName}'
        device.data_lock = threading.Lock()
        device.logger = logging.getLogger('test.k4')
        device.mdib = None                       # __init__'s update_data returns early
        device.get_suppression_snapshot = lambda: (frozenset(suppressed), frozenset())
        calls = []

        def ack_detailed(op, sig, press_id=None):
            calls.append((op, sig, press_id))
            return outcome, ('Fin' if outcome == 'accepted' else 'Fail'), ''
        device.acknowledge_alarm_detailed = ack_detailed
        qt = self.QtDeviceHandler(device)
        qt._ack_synchronous = True
        device.mdib = _Mdib(pm, states, descs, ops)
        return qt, calls

    def _on_signal(self, handle, cond, cond_present=True):
        pm, pt = self.pm, self.pm_types
        return ([_State(pm.AlertSignalState, handle, pt.AlertSignalPresence.ON),
                 _State(pm.AlertConditionState, cond, cond_present)],
                [_Desc(handle, cond=cond), _Desc(cond)])

    def test_K4a_active_signal_with_own_operation(self):
        states, descs = self._on_signal('sig1', 'cond1')
        qt, calls = self._qt(states, descs, [_Desc('op1', target='sig1')])
        qt.silenceAlarm()
        self.assertEqual([(c[0], c[1]) for c in calls], [('op1', 'sig1')])
        self.assertEqual(qt.ackState, 'accepted')
        self.assertEqual(qt.ackPressId, calls[0][2])

    def test_K4b_no_active_signal_sends_nothing(self):
        """Before Fix 16: fallback to the first signal / first operation."""
        pm, pt = self.pm, self.pm_types
        states = [_State(pm.AlertSignalState, 'sig1', pt.AlertSignalPresence.OFF)]
        qt, calls = self._qt(states, [_Desc('sig1', cond='cond1')], [_Desc('op1', target='sig1')])
        qt.silenceAlarm()
        self.assertEqual(calls, [])
        self.assertEqual(qt.ackState, 'no_active_alarm')

    def test_K4c_signal_on_but_condition_ended_is_skipped(self):
        states, descs = self._on_signal('sig1', 'cond1', cond_present=False)
        qt, calls = self._qt(states, descs, [_Desc('op1', target='sig1')])
        qt.silenceAlarm()
        self.assertEqual(calls, [])
        self.assertEqual(qt.ackState, 'no_active_alarm')

    def test_K4d_suppressed_condition_is_skipped(self):
        states, descs = self._on_signal('sig1', 'cond1')
        qt, calls = self._qt(states, descs, [_Desc('op1', target='sig1')], suppressed={'cond1'})
        qt.silenceAlarm()
        self.assertEqual(calls, [])

    def test_K4e_no_matching_operation_is_not_remote_ackable(self):
        states, descs = self._on_signal('sig1', 'cond1')
        qt, calls = self._qt(states, descs, [_Desc('opX', target='sigX')])   # foreign operation only
        qt.silenceAlarm()
        self.assertEqual(calls, [])                                           # before: 'opX' was used
        self.assertEqual(qt.ackState, 'not_remote_ackable')
        self.assertIn('sig1', qt.ackMessage)

    def test_K4f_two_signals_two_transactions_one_press_id(self):
        pm, pt = self.pm, self.pm_types
        states = [_State(pm.AlertSignalState, 'sig1', pt.AlertSignalPresence.ON),
                  _State(pm.AlertConditionState, 'cond1', True),
                  _State(pm.AlertSignalState, 'sig2', pt.AlertSignalPresence.LATCH),
                  _State(pm.AlertConditionState, 'cond2', False)]
        descs = [_Desc('sig1', cond='cond1'), _Desc('sig2', cond='cond2'), _Desc('cond1'), _Desc('cond2')]
        ops = [_Desc('op1', target='sig1'), _Desc('op2', target='sig2')]
        qt, calls = self._qt(states, descs, ops)
        qt.silenceAlarm()
        self.assertEqual(sorted((c[0], c[1]) for c in calls), [('op1', 'sig1'), ('op2', 'sig2')])
        self.assertEqual(len({c[2] for c in calls}), 1)          # one press_id for both
        self.assertEqual(qt.ackState, 'accepted')

    def test_K4g_failed_transaction_is_shown(self):
        states, descs = self._on_signal('sig1', 'cond1')
        qt, calls = self._qt(states, descs, [_Desc('op1', target='sig1')], outcome='failed')
        qt.silenceAlarm()
        self.assertEqual(qt.ackState, 'failed')
        self.assertIn('sig1', qt.ackMessage)
        # the provider's episodic Ack does not turn a refused ack into 'confirmed'
        qt._applyAckConfirmed('sig1')
        self.assertEqual(qt.ackState, 'failed')

    def test_K4h_accepted_then_confirmed(self):
        states, descs = self._on_signal('sig1', 'cond1')
        qt, _calls = self._qt(states, descs, [_Desc('op1', target='sig1')])
        qt.silenceAlarm()
        qt._applyAckConfirmed('sig1')
        self.assertEqual(qt.ackState, 'confirmed')


if __name__ == '__main__':
    unittest.main()
