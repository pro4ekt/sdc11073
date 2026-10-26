"""
tests/test_connection_lost.py
=============================
Fix 17 (Ziel 2) — a communication failure is a TECHNICAL ALARM, not a silent
removal (DIN EN 60601-1-8 §6.11.2.2.1 b; kapitel3 §3.4.6 "Wegfall einer Quelle
… wird durch die Verbindungsüberwachung als eigener Zustand gemeldet").

    L1  SdcMyConsumer.remove_device: error → deviceConnectionLost (NOT
        deviceDisconnected), the QtDeviceHandler is marked COMM_FAILURE, the
        overview gets markLost, one technical_alarm{raised} event with
        escalated_at_loss; the latch state and the patient/room are read BEFORE
        release_device; the aggregator path (release_device → discard_ensemble →
        on_member_departed) is unchanged in order and arguments (Fix 9–13)
    L2  graceful departure / room switch / location filter → silent as before
    L3  reconnect of the same EPR (on_device_connected) → technical_alarm
        {cleared}, deviceReconnected + deviceConnected; dismissal
        (dismissConnectionLoss) → technical_alarm{dismissed} + deviceDisconnected
    L4  QtDeviceHandler.markConnectionLost: COMM_FAILURE, connectionLost,
        escalatedAtLoss, and update_data() never touches the dead worker's MDIB
    L5  PatientOverviewModel: full loss → synthetic "Verbindung verloren" card
        (A1: escalatedAtLoss), partial loss → lostDevices on the live card, the
        marker survives aggregator updates and leaves only through clearLost

Run with (from Dashboard/):  ..\\.venv\\Scripts\\python -m unittest tests.test_connection_lost -v
"""

from __future__ import annotations

import json
import logging
import tempfile
import threading
import types
import unittest
from pathlib import Path


def _bootstrap():
    import app  # noqa: F401
    from PySide6.QtCore import QObject
    from app.sdcMyConsumer import SdcMyConsumer
    from app.qtDeviceHandler import QtDeviceHandler
    from app.patientOverviewModel import PatientOverviewModel
    from app.metrics import eventlog as app_eventlog
    return QObject, SdcMyConsumer, QtDeviceHandler, PatientOverviewModel, app_eventlog


class _Base(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            (cls.QObject, cls.SdcMyConsumer, cls.QtDeviceHandler, cls.PatientOverviewModel,
             cls._eventlog) = _bootstrap()
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f'production modules not importable here: {exc}')
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


class _Overview:
    """Recorder with the Fix 9 interface plus the Fix 17 markers."""

    def __init__(self):
        self.calls = []

    def updateEnsemble(self, *a):
        self.calls.append(('update', a))

    def removeEnsemble(self, uuid):
        self.calls.append(('remove', uuid))

    def markLost(self, patient, room, epr, cause, escalated, t_abs):
        self.calls.append(('markLost', patient, room, epr, cause, escalated))

    def clearLost(self, epr):
        self.calls.append(('clearLost', epr))


class _Topology:
    def __init__(self, orphaned_uuid=None, patient=('P1', 'ICU-1')):
        self.calls = []
        self.released = False
        self._orphaned = orphaned_uuid
        self._patient = patient

    def release_device(self, epr):
        self.calls.append(('release_device', epr))
        self.released = True
        return self._orphaned

    def reverse_lookup_patient_room(self, uuid):
        # After a full release the map entry is gone — the manager must read
        # it BEFORE release_device (A1).
        return ('', '') if self.released else self._patient

    def get_member_count(self, uuid):
        return 0


class _Aggregator:
    def __init__(self, escalated=True):
        self.calls = []
        self._escalated = escalated

    def is_ensemble_escalated(self, uuid):
        self.calls.append(('is_ensemble_escalated', uuid))
        return self._escalated

    def discard_ensemble(self, uuid):
        self.calls.append(('discard_ensemble', uuid))
        self._escalated = False      # the latch is gone after the discard

    def on_member_departed(self, uuid, orphaned=False, departed_alert_keys=None):
        self.calls.append(('on_member_departed', uuid, orphaned, list(departed_alert_keys or [])))


class TestL1_L3_Manager(_Base):

    def _manager(self, topology, aggregator, overview):
        m = self.SdcMyConsumer.__new__(self.SdcMyConsumer)
        self.QObject.__init__(m)
        m.lock = threading.Lock()
        m.devices = {}
        m.target_room = None
        m.discovery = None
        m._lost_devices = {}
        m._location_rejected = set()
        m._rejected_room_map = {}
        m._reconnect_cooldown = {}
        m._known_rooms = set()
        m._overview_model = overview
        m.topology = topology
        m.aggregator = aggregator
        spy = {'lost': [], 'reconnected': [], 'disconnected': [], 'connected': []}
        m.deviceConnectionLost.connect(lambda e, c: spy['lost'].append((e, c)))
        m.deviceReconnected.connect(lambda e: spy['reconnected'].append(e))
        m.deviceDisconnected.connect(lambda e: spy['disconnected'].append(e))
        m.deviceConnected.connect(lambda d: spy['connected'].append(d.epr))
        return m, spy

    def _handler(self, epr, ensemble='ens1', ui=True, cause='t_fallback'):
        qt_calls = []
        qt = types.SimpleNamespace(
            epr=epr, patientId='P1', patientRoom='ICU-1',
            markConnectionLost=lambda c, t, esc: qt_calls.append((c, esc)),
        )
        h = types.SimpleNamespace(
            epr=epr, _ui_connected=ui, ensemble_uuid=ensemble, qtDeviceHandler=qt,
            enumerate_alert_channels=lambda: [('cond1',)], fallback_cause=cause,
        )
        return h, qt_calls

    def test_L1a_error_loss_is_a_technical_alarm_not_a_removal(self):
        epr = 'urn:uuid:L1a'
        topo, agg, ov = _Topology(orphaned_uuid='ens1'), _Aggregator(escalated=True), _Overview()
        m, spy = self._manager(topo, agg, ov)
        h, qt_calls = self._handler(epr)
        m.devices[epr] = h

        m.remove_device(epr, error_occurred=True)

        self.assertEqual(spy['lost'], [(epr, 't_fallback')])
        self.assertEqual(spy['disconnected'], [])                       # before Fix 17: [epr]
        self.assertEqual(qt_calls, [('t_fallback', True)])              # COMM_FAILURE, during escalation
        self.assertIn(('markLost', 'P1', 'ICU-1', epr, 't_fallback', True), ov.calls)
        self.assertEqual(list(m._lost_devices), [epr])
        # aggregator path unchanged (Fix 9): release → discard → on_member_departed(orphaned)
        self.assertEqual(topo.calls, [('release_device', epr)])
        self.assertEqual(agg.calls[0], ('is_ensemble_escalated', 'ens1'))   # read BEFORE release
        self.assertEqual(agg.calls[1:], [('discard_ensemble', 'ens1'),
                                         ('on_member_departed', 'ens1', True, [])])
        ev = self._events(event='technical_alarm', epr=epr)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]['action'], 'raised')
        self.assertTrue(ev[0]['escalated_at_loss'])
        self.assertEqual((ev[0]['patient_id'], ev[0]['room'], ev[0]['ensemble']), ('P1', 'ICU-1', 'ens1'))
        self.assertEqual(ev[0]['cause'], 't_fallback')
        self.assertIn('lost_since_abs', ev[0])
        # departure event of Ziel 1 still written
        self.assertEqual(self._events(event='device_departed', epr=epr)[-1]['cause'], 'error')

    def test_L1d_early_notify_before_unsubscribe_then_remove_device(self):
        """Production order: the handler calls notify_connection_lost right after
        the watchdog (BEFORE its unsubscribe attempt), remove_device follows
        after it.  One alarm, latch read while the aggregator is intact, no
        duplicate, aggregator path unchanged."""
        epr = 'urn:uuid:L1d'
        topo, agg, ov = _Topology(orphaned_uuid='ens1'), _Aggregator(escalated=True), _Overview()
        m, spy = self._manager(topo, agg, ov)
        h, qt_calls = self._handler(epr)
        m.devices[epr] = h

        m.notify_connection_lost(epr, h, 't_fallback')          # ← before the unsubscribe
        self.assertEqual(spy['lost'], [(epr, 't_fallback')])
        self.assertEqual(qt_calls, [('t_fallback', True)])
        self.assertEqual(topo.calls, [])                          # nothing released yet
        self.assertEqual(agg.calls, [('is_ensemble_escalated', 'ens1')])
        m.notify_connection_lost(epr, h, 't_fallback')          # idempotent
        self.assertEqual(spy['lost'], [(epr, 't_fallback')])

        m.remove_device(epr, error_occurred=True)                # ← after the unsubscribe
        self.assertEqual(spy['lost'], [(epr, 't_fallback')])     # not raised twice
        self.assertEqual(spy['disconnected'], [])
        self.assertEqual(topo.calls, [('release_device', epr)])
        self.assertEqual(agg.calls, [('is_ensemble_escalated', 'ens1'),
                                     ('discard_ensemble', 'ens1'),
                                     ('on_member_departed', 'ens1', True, [])])
        self.assertEqual([c for c in ov.calls if c[0] == 'markLost'],
                         [('markLost', 'P1', 'ICU-1', epr, 't_fallback', True)])
        ev = self._events(event='technical_alarm', epr=epr)
        self.assertEqual([e['action'] for e in ev], ['raised'])
        self.assertTrue(ev[0]['escalated_at_loss'])

    def test_L1e_early_notify_ignores_unknown_or_hidden_devices(self):
        m, spy = self._manager(_Topology(), _Aggregator(), _Overview())
        h, qt_calls = self._handler('urn:uuid:L1e', ui=False)
        m.devices['urn:uuid:L1e'] = h
        m.notify_connection_lost('urn:uuid:L1e', h, 't_fallback')   # never shown to staff
        m.notify_connection_lost('urn:uuid:nobody', h, 't_fallback')  # not a managed device
        self.assertEqual(spy['lost'], [])
        self.assertEqual(m._lost_devices, {})

    def test_L1b_partial_loss_not_escalated(self):
        epr = 'urn:uuid:L1b'
        topo, agg, ov = _Topology(orphaned_uuid=None), _Aggregator(escalated=False), _Overview()
        m, spy = self._manager(topo, agg, ov)
        h, qt_calls = self._handler(epr, cause='is_connected')
        m.devices[epr] = h
        m.remove_device(epr, error_occurred=True)
        self.assertEqual(qt_calls, [('is_connected', False)])
        self.assertEqual(agg.calls, [('is_ensemble_escalated', 'ens1'),
                                     ('on_member_departed', 'ens1', False, ['cond1'])])
        self.assertIn(('markLost', 'P1', 'ICU-1', epr, 'is_connected', False), ov.calls)
        self.assertFalse(self._events(event='technical_alarm', epr=epr)[0]['escalated_at_loss'])

    def test_L1c_loss_before_ui_is_no_technical_alarm(self):
        epr = 'urn:uuid:L1c'
        m, spy = self._manager(_Topology(), _Aggregator(), _Overview())
        h, qt_calls = self._handler(epr, ui=False)
        m.devices[epr] = h
        m.remove_device(epr, error_occurred=True)
        self.assertEqual(spy['lost'], [])
        self.assertEqual(spy['disconnected'], [])
        self.assertEqual(m._lost_devices, {})

    def test_L2_graceful_and_location_departures_stay_silent(self):
        for kwargs in ({'error_occurred': False}, {'error_occurred': False, 'location_filtered': True}):
            epr = f'urn:uuid:L2-{len(kwargs)}'
            topo, agg, ov = _Topology(orphaned_uuid='ens1'), _Aggregator(), _Overview()
            m, spy = self._manager(topo, agg, ov)
            h, qt_calls = self._handler(epr)
            m.devices[epr] = h
            m.remove_device(epr, **kwargs)
            self.assertEqual(spy['disconnected'], [epr], kwargs)
            self.assertEqual(spy['lost'], [], kwargs)
            self.assertEqual(qt_calls, [], kwargs)
            self.assertFalse(any(c[0] == 'markLost' for c in ov.calls), kwargs)
            self.assertEqual(self._events(event='technical_alarm', epr=epr), [])

    def test_L3a_reconnect_clears_the_alarm(self):
        epr = 'urn:uuid:L3a'
        topo, agg, ov = _Topology(orphaned_uuid='ens1'), _Aggregator(), _Overview()
        m, spy = self._manager(topo, agg, ov)
        h, _ = self._handler(epr)
        m.devices[epr] = h
        m.remove_device(epr, error_occurred=True)
        self.assertIn(epr, m._lost_devices)

        # deviceConnected is typed Signal(QtDeviceHandler): use real Qt wrappers
        def _qt(e):
            dev = types.SimpleNamespace(epr=e, data_lock=threading.Lock(),
                                        logger=logging.getLogger('test.l3'), mdib=None)
            return self.QtDeviceHandler(dev)

        m.on_device_connected(_qt(epr))

        self.assertEqual(m._lost_devices, {})
        self.assertEqual(spy['reconnected'], [epr])
        self.assertEqual(spy['connected'], [epr])
        self.assertIn(('clearLost', epr), ov.calls)
        actions = [e['action'] for e in self._events(event='technical_alarm', epr=epr)]
        self.assertEqual(actions, ['raised', 'cleared'])
        # a device that was never lost connects as before
        m.on_device_connected(_qt('urn:uuid:other'))
        self.assertEqual(spy['reconnected'], [epr])
        self.assertEqual(spy['connected'], [epr, 'urn:uuid:other'])

    def test_L3b_dismissal_removes_the_device(self):
        epr = 'urn:uuid:L3b'
        topo, agg, ov = _Topology(orphaned_uuid='ens1'), _Aggregator(), _Overview()
        m, spy = self._manager(topo, agg, ov)
        h, _ = self._handler(epr)
        m.devices[epr] = h
        m.remove_device(epr, error_occurred=True)

        m.dismissConnectionLoss(epr)

        self.assertEqual(m._lost_devices, {})
        self.assertEqual(spy['disconnected'], [epr])
        self.assertIn(('clearLost', epr), ov.calls)
        actions = [e['action'] for e in self._events(event='technical_alarm', epr=epr)]
        self.assertEqual(actions, ['raised', 'dismissed'])
        m.dismissConnectionLoss(epr)                     # idempotent
        self.assertEqual(spy['disconnected'], [epr])
        self.assertEqual(m.lostDevices, [])


class _Boom:
    def __getattr__(self, name):
        raise AssertionError(f'MDIB of the dead worker must not be read ({name})')

    def __bool__(self):
        return True


class TestL4_QtDeviceHandler(_Base):

    def test_L4_mark_connection_lost(self):
        device = types.SimpleNamespace(epr='urn:uuid:L4', data_lock=threading.Lock(),
                                       logger=logging.getLogger('test.l4'), mdib=None)
        qt = self.QtDeviceHandler(device)
        changes = []
        qt.alarmStatusChanged.connect(lambda: changes.append('alarm'))
        qt.connectedChanged.connect(lambda: changes.append('connected'))

        qt.markConnectionLost('t_fallback', 1700000000.0, True)   # main thread → applied directly

        self.assertEqual(qt.alarmStatus, 'COMM_FAILURE')
        self.assertTrue(qt.connectionLost)
        self.assertFalse(qt.connected)
        self.assertTrue(qt.escalatedAtLoss)
        self.assertEqual(qt.lostCause, 't_fallback')
        self.assertEqual(qt.lostSince, 1700000000.0)
        self.assertEqual(qt.deviceValue, 'VERBINDUNG VERLOREN')
        self.assertIn('alarm', changes)
        self.assertIn('connected', changes)
        self.assertNotEqual(qt.lostSinceText, '')
        # the worker is dead: update_data must not touch its MDIB any more
        device.mdib = _Boom()
        qt.update_data()
        qt.handleUpdateTick()
        self.assertEqual(qt.alarmStatus, 'COMM_FAILURE')


class TestL5_PatientOverviewModel(_Base):

    def _cards(self, model):
        return {c['ensembleUuid']: c for c in model.patients}

    def test_L5a_full_loss_synthetic_card_with_escalation(self):
        model = self.PatientOverviewModel()
        model.updateEnsemble('e1', 'P1', 'ICU-1', 1, True, 0.9, True)
        model.removeEnsemble('e1')                        # aggregator: last member released
        model.markLost('P1', 'ICU-1', 'urn:uuid:A', 't_fallback', True, 1700000000.0)
        cards = self._cards(model)
        self.assertEqual(list(cards), ['lost:P1|ICU-1'])
        c = cards['lost:P1|ICU-1']
        self.assertTrue(c['connectionLost'])
        self.assertTrue(c['escalatedAtLoss'])
        self.assertEqual((c['patientName'], c['room'], c['deviceCount'], c['lostDevices']),
                         ('P1', 'ICU-1', 0, 1))
        self.assertFalse(c['isEscalated'])                # no E(t) behind a lost card
        self.assertEqual(c['lostEprs'], ['urn:uuid:A'])
        model.clearLost('urn:uuid:A')
        self.assertEqual(model.patients, [])

    def test_L5b_partial_loss_marks_the_live_card(self):
        model = self.PatientOverviewModel()
        model.updateEnsemble('e1', 'P1', 'ICU-1', 2, False, 0.2, True)
        model.markLost('P1', 'ICU-1', 'urn:uuid:B', 'is_connected', False, 1700000001.0)
        model.updateEnsemble('e1', 'P1', 'ICU-1', 1, False, 0.2, True)   # Fix 9 re-push
        cards = self._cards(model)
        self.assertEqual(list(cards), ['e1'])
        c = cards['e1']
        self.assertFalse(c['connectionLost'])
        self.assertEqual(c['lostDevices'], 1)
        self.assertEqual(c['deviceCount'], 1)
        self.assertFalse(c['escalatedAtLoss'])
        self.assertEqual(c['lostCause'], 'is_connected')
        # other patients are untouched
        model.updateEnsemble('e2', 'P2', 'ICU-1', 1, False, 0.0, False)
        self.assertEqual(self._cards(model)['e2']['lostDevices'], 0)
        model.clearLost('urn:uuid:B')
        self.assertEqual(self._cards(model)['e1']['lostDevices'], 0)

    def test_L5c_marker_survives_rebind_until_cleared(self):
        model = self.PatientOverviewModel()
        model.markLost('P1', 'ICU-1', 'urn:uuid:C', 't_fallback', False, 1.0)
        self.assertTrue(self._cards(model)['lost:P1|ICU-1']['connectionLost'])
        # the patient re-binds with a NEW ensemble uuid (another device of P1)
        model.updateEnsemble('e9', 'P1', 'ICU-1', 1, False, 0.0, False)
        cards = self._cards(model)
        self.assertEqual(list(cards), ['e9'])             # synthetic card superseded
        self.assertEqual(cards['e9']['lostDevices'], 1)   # marker still shown
        model.clearLost('urn:uuid:C')
        self.assertEqual(self._cards(model)['e9']['lostDevices'], 0)


if __name__ == '__main__':
    unittest.main()
