"""
tests/test_watchdog.py
======================
Fix 18 (Ziel 2) — connection monitoring and fallback entry of
``DeviceHandler`` with the constants unchanged (SLEEP_INTERVAL 5 s,
T_FALLBACK 15 s, MAX_MISSED 3, METRIC_SILENCE_SEC 120 s, τ_sock 5 s) but
shrunk per instance so the loop runs in milliseconds.

    W1  _ping: a failing GetContextStates counts a miss (1, 2) and returns −1 at
        MAX_MISSED; HTTP 4xx keeps the connection (0); success resets and marks
        the last inbound round trip (mark_rx)
    W2  _monitoring_loop: is_connected False → fallback{cause=is_connected};
        three failed pings → fallback{cause=t_fallback}; metric silence →
        fallback{cause=metric_silence}; error_occurred is set in every case
    W3  the derived upper bound (A6): MAX_MISSED · (SLEEP_INTERVAL + τ_sock)
        = T_FALLBACK + MAX_MISSED · τ_sock = 30 s with the production constants
    W4  a SubscriptionEnd from the provider is carried into the fallback event
        (end_status / end_reason)

Run with (from the repository root):  .venv\\Scripts\\python -m unittest tests.test_watchdog -v
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import tempfile
import threading
import types
import unittest
from pathlib import Path


def _bootstrap():
    import app  # noqa: F401
    from device.handler import DeviceHandler
    from device.alarm_manager import AlarmManager
    from app.metrics import eventlog as app_eventlog
    from app.metrics.latency_metrics import get_latency_metrics
    from sdc11073.pysoap.soapclient import HTTPReturnCodeError
    from sdc11073.consumer.consumerimpl import SdcConsumer
    return DeviceHandler, AlarmManager, app_eventlog, get_latency_metrics, HTTPReturnCodeError, SdcConsumer


class _Base(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            (cls.DeviceHandler, cls.AlarmManager, cls._eventlog, cls.get_latency_metrics,
             cls.HTTPReturnCodeError, cls.SdcConsumer) = _bootstrap()
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f'production modules not importable here: {exc}')
        from app.metrics.latency_metrics import get_latency_metrics
        cls._lm = get_latency_metrics()          # the singleton the handler writes to
        cls._tmp = tempfile.TemporaryDirectory()
        cls._eventlog.configure(Path(cls._tmp.name) / f'{cls.__name__}.jsonl')

    @classmethod
    def tearDownClass(cls):
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

    def _handler(self, ping):
        """DeviceHandler without thread / network: only what the watchdog touches."""
        h = self.DeviceHandler.__new__(self.DeviceHandler)
        h.epr = f'urn:uuid:{self._testMethodName}'
        h.logger = logging.getLogger('test.watchdog')
        h.running = True
        h.error_occurred = False
        h.qtDeviceHandler = None
        h._last_metric_ts = 0.0
        h._subscription_end_info = (None, None)
        h.fallback_cause = None
        h.data_lock = threading.Lock()
        h.mdib = None
        h.alarm_manager = self.AlarmManager(h)
        h.consumer = types.SimpleNamespace(
            is_connected=True,
            context_service_client=types.SimpleNamespace(get_context_states=ping),
        )
        return h

    @staticmethod
    def _run(coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


class TestW1_Ping(_Base):

    def test_W1a_misses_then_fallback(self):
        def failing():
            raise ConnectionRefusedError('provider gone')
        h = self._handler(failing)
        args = (self.DeviceHandler.MAX_MISSED, self.DeviceHandler.SLEEP_INTERVAL, self.DeviceHandler.T_FALLBACK)
        self.assertEqual(self._run(h._ping(0, *args)), 1)
        self.assertEqual(self._run(h._ping(1, *args)), 2)
        self.assertFalse(h.error_occurred)
        self.assertEqual(self._run(h._ping(2, *args)), -1)       # 3 · 5 s ≥ 15 s
        self.assertTrue(h.error_occurred)

    def test_W1b_http_4xx_is_not_a_miss(self):
        def rejected():
            raise self.HTTPReturnCodeError(400, 'Bad Request', '')
        h = self._handler(rejected)
        args = (3, 5.0, 15.0)
        self.assertEqual(self._run(h._ping(2, *args)), 0)
        self.assertFalse(h.error_occurred)

    def test_W1c_success_resets_and_marks_rx(self):
        h = self._handler(lambda: None)
        lm = self._lm
        lm._last_rx.pop(h.epr, None)
        self.assertEqual(self._run(h._ping(2, 3, 5.0, 15.0)), 0)
        self.assertIn(h.epr, lm._last_rx)


class TestW2_MonitoringLoop(_Base):

    def _fast(self, h):
        h.SLEEP_INTERVAL = 0.001
        h.T_FALLBACK = 0.003
        h.MAX_MISSED = 3
        h.METRIC_SILENCE_SEC = 120.0

    def test_W2a_is_connected_false(self):
        h = self._handler(lambda: None)
        self._fast(h)
        h.consumer.is_connected = False
        self._run(h._monitoring_loop())
        self.assertTrue(h.error_occurred)
        self.assertEqual(h.fallback_cause, 'is_connected')
        ev = self._events(event='fallback', epr=h.epr)
        self.assertEqual([e['cause'] for e in ev], ['is_connected'])

    def test_W2b_three_failed_pings(self):
        calls = []

        def failing():
            calls.append(1)
            raise TimeoutError('silent loss')
        h = self._handler(failing)
        self._fast(h)
        self._run(h._monitoring_loop())
        self.assertTrue(h.error_occurred)
        self.assertEqual(len(calls), 3)                            # MAX_MISSED pings, then fallback
        self.assertEqual(h.fallback_cause, 't_fallback')
        ev = self._events(event='fallback', epr=h.epr)
        self.assertEqual([e['cause'] for e in ev], ['t_fallback'])
        self.assertIn('since_rx_s', ev[0])

    def test_W2c_metric_silence(self):
        h = self._handler(lambda: None)
        self._fast(h)
        h.METRIC_SILENCE_SEC = 0.0005
        h._last_metric_ts = 1.0                                     # a report was once received
        self._run(h._monitoring_loop())
        self.assertTrue(h.error_occurred)
        self.assertEqual(h.fallback_cause, 'metric_silence')
        self.assertEqual([e['cause'] for e in self._events(event='fallback', epr=h.epr)], ['metric_silence'])

    def test_W2d_stop_exits_without_fallback(self):
        h = self._handler(lambda: None)
        self._fast(h)

        async def stopper():
            await asyncio.sleep(0.01)
            h.stop()
        self._run(asyncio.wait([asyncio.ensure_future(h._monitoring_loop()), asyncio.ensure_future(stopper())])
                  if False else self._run_both(h, stopper))
        self.assertFalse(h.error_occurred)
        self.assertEqual(self._events(event='fallback', epr=h.epr), [])

    def _run_both(self, h, stopper):
        async def both():
            await asyncio.gather(h._monitoring_loop(), stopper())
        return both()


class TestW3_UpperBound(_Base):

    def test_W3_constants_and_bound(self):
        DH = self.DeviceHandler
        self.assertEqual((DH.SLEEP_INTERVAL, DH.T_FALLBACK, DH.MAX_MISSED, DH.METRIC_SILENCE_SEC),
                         (5.0, 15.0, 3, 120.0))
        tau_sock = inspect.signature(self.SdcConsumer.__init__).parameters['socket_timeout'].default
        self.assertEqual(tau_sock, 5)
        bound = DH.MAX_MISSED * (DH.SLEEP_INTERVAL + tau_sock)
        self.assertEqual(bound, DH.T_FALLBACK + DH.MAX_MISSED * tau_sock)
        self.assertEqual(bound, 30.0)
        # killed process (TCP RST): pings fail instantly → MAX_MISSED · SLEEP_INTERVAL
        self.assertEqual(DH.MAX_MISSED * DH.SLEEP_INTERVAL, 15.0)

    def test_W3b_technical_alarm_raised_before_unsubscribe(self):
        """loss → technical_alarm{raised} must not wait for the graceful
        unsubscribe (≤ 5 s): in _worker_logic the manager is notified BEFORE
        _graceful_shutdown, so the same bound holds for the visible safe state."""
        src = inspect.getsource(self.DeviceHandler._worker_logic)
        i_notify = src.index('notify_connection_lost')
        i_unsub = src.index('_graceful_shutdown()')
        self.assertLess(i_notify, i_unsub)
        self.assertLess(src.index('finally:'), i_notify)      # on every exit path


class TestW4_SubscriptionEnd(_Base):

    def test_W4_end_status_in_fallback_event(self):
        h = self._handler(lambda: None)
        sub = types.SimpleNamespace(end_status='http://schemas.xmlsoap.org/ws/2004/08/eventing/SourceShuttingDown',
                                    end_reason=types.SimpleNamespace(text='provider stopped'))
        h._on_subscription_end(types.SimpleNamespace(subscription=sub))
        self.assertEqual(h._subscription_end_info[1], 'provider stopped')
        h._record_fallback_metric('is_connected')
        ev = self._events(event='fallback', epr=h.epr)[-1]
        self.assertEqual(ev['end_reason'], 'provider stopped')
        self.assertIn('SourceShuttingDown', ev['end_status'])
        h._on_subscription_end(None)                                # ignored
        self.assertEqual(h._subscription_end_info[1], 'provider stopped')


if __name__ == '__main__':
    unittest.main()
