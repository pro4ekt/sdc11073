"""
tests/test_departure_overview.py
================================
Fix 9 — a device departure must reach the Patient Overview and purge the
departed device's alarm keys from the TTL cache (docs/model/SPEC_DELTA.md SD-2 residual).

Diagnosis (2026-09-30): the departure chain

    DeviceHandler.run()  finally → SdcMyConsumer.remove_device()
        → EnsembleTopologyManager.release_device()   (membership + registry shrink — Fix 8)
        → SmartAlertAggregator.discard_ensemble()      (processor state, full release)

never touched ``PatientOverviewModel``: it keeps its own copy of the ensembles
(``_ensembles``), fed only by ``_notify_overview`` at bind time and on alarm
events, and ``removeEnsemble`` had no caller at all.  The model (registry,
|M|, k_min) was correct after the departure; the defect was interface-level.

Fix 9 adds ``SmartAlertAggregator.on_member_departed(ensemble_uuid, orphaned,
departed_alert_keys)``, called from ``remove_device`` after the topology
release: full release → ``removeEnsemble``; partial → purge of the departed
keys from ``_active_alarms`` and an immediate re-push of the card with the
current member count / status.

These tests failed before Fix 9 and pass with it.  Same isolated-module
bootstrap as test_alignment_integration.py (no SDC / Qt / FHIR import); the
overview model is a recorder with the same two-method interface.

Run with:  python -m unittest Dashboard.tests.test_departure_overview -v
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

# ══════════════════════════════════════════════════════════════════════════════
#  Isolated module bootstrap (mirrors test_alignment_integration.py)
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

from Dashboard.app.alarms.device_profile_repo import DeviceReliabilityProfile  # noqa: E402
from Dashboard.app.alarms.smartAlertAggregator import SmartAlertAggregator  # noqa: E402
from Dashboard.app.alarms.ensemble_topology_manager import EnsembleTopologyManager  # noqa: E402


# ══════════════════════════════════════════════════════════════════════════════
#  Stubs
# ══════════════════════════════════════════════════════════════════════════════

def _profile_with_weight(w: float, tpr: float = 0.9) -> DeviceReliabilityProfile:
    return DeviceReliabilityProfile(true_positive_rate=tpr, false_positive_rate=tpr / math.exp(w))


class _StubHandler:
    def __init__(self, epr: str, channels):
        self.epr = epr
        self._channels = list(channels)
        self.ensemble_uuid: Optional[str] = None

    def enumerate_alert_channels(self):
        return list(self._channels)

    # used only by the re-bind scenario (evaluate_and_bind_device)
    def apply_ensemble_context(self, ensemble_uuid: str) -> bool:
        return True

    def apply_fhir_contexts(self, fhir_data) -> None:
        pass


class _StubManager:
    def __init__(self):
        self.devices: dict = {}


class _RecordingOverview:
    """Duck-typed PatientOverviewModel: applies the same commands to the same
    kind of registry (ensemble_uuid → card dict), without Qt."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.cards: dict[str, dict] = {}

    def updateEnsemble(self, ensemble_uuid, patient_name, room, device_count,
                       is_escalated, sdc_score, has_active_alarms=False):
        self.calls.append(('update', ensemble_uuid, device_count, is_escalated, has_active_alarms))
        self.cards[ensemble_uuid] = {
            'patientName': patient_name, 'room': room, 'deviceCount': device_count,
            'isEscalated': is_escalated, 'hasActiveAlarms': has_active_alarms,
        }

    def removeEnsemble(self, ensemble_uuid):
        self.calls.append(('remove', ensemble_uuid))
        self.cards.pop(ensemble_uuid, None)


class _FakeClock:
    def __init__(self, t0: float = 0.0):
        self.t = t0

    def __call__(self) -> float:
        return self.t


def _bind(topology: EnsembleTopologyManager, uuid: str, patient: str, room: str, *eprs):
    with topology.lock:
        topology._patient_to_ensemble_map[(patient, room)] = uuid
        topology._ensembles_devices[uuid] = set(eprs)


# ══════════════════════════════════════════════════════════════════════════════
#  Departure: model shrinks, overview is never told
# ══════════════════════════════════════════════════════════════════════════════

class TestDepartureReachesPatientOverview(unittest.TestCase):
    """Two devices (A: w={3,5}, B: w={1,1}) in one ensemble; B departs, then A."""

    UUID = 'ens-depart-0000-0000-000000000000'

    def setUp(self):
        self.clock = _FakeClock(0.0)
        self._patcher = mock.patch('time.monotonic', self.clock)
        self._patcher.start()
        self.manager = _StubManager()
        self.overview = _RecordingOverview()
        self.topology = EnsembleTopologyManager(self.manager, overview_model=self.overview)
        self.sa = SmartAlertAggregator(self.manager, self.topology, overview_model=self.overview)
        self.prof_a0 = _profile_with_weight(3.0)
        self.prof_a1 = _profile_with_weight(5.0)
        self.prof_b = _profile_with_weight(1.0)
        self.dev_a = _StubHandler('urn:uuid:dev-A', [
            ('a0', 'MDC.a0', 'Hi', self.prof_a0), ('a1', 'MDC.a1', 'Hi', self.prof_a1)])
        self.dev_b = _StubHandler('urn:uuid:dev-B', [
            ('b0', 'MDC.b0', 'Hi', self.prof_b), ('b1', 'MDC.b1', 'Hi', self.prof_b)])
        for h in (self.dev_a, self.dev_b):
            self.manager.devices[h.epr] = h
        _bind(self.topology, self.UUID, 'PAT-D', 'ICU-D', self.dev_a.epr, self.dev_b.epr)
        self.topology._refresh_ensemble_channel_specs(self.UUID)
        # what evaluate_and_bind_device() pushes after a successful SOAP bind
        self.topology._notify_overview(self.UUID, 'PAT-D', 'ICU-D', False, 0.0, False)

    def tearDown(self):
        self.sa.stop()
        self._patcher.stop()

    # ── helpers ────────────────────────────────────────────────────────────────

    def _fire(self, key: str, prof) -> str:
        return self.sa.check_alert_validity(
            self.UUID, key, f'MDC.{key}', biceps_priority='Hi',
            manufacturer='Stub', model='S1', device_epr='', reliability_profile=prof,
        )

    def _disconnect(self, handler: _StubHandler):
        """Mirror SdcMyConsumer.remove_device(): drop the handler, release the
        topology, discard processor state if orphaned, then on_member_departed."""
        self.manager.devices.pop(handler.epr, None)
        departed_keys = [ch[0] for ch in handler.enumerate_alert_channels()]
        orphaned = self.topology.release_device(handler.epr)
        if orphaned:
            self.sa.discard_ensemble(orphaned)
            self.sa.on_member_departed(orphaned, orphaned=True)
        else:
            self.sa.on_member_departed(self.UUID, orphaned=False, departed_alert_keys=departed_keys)
        return orphaned

    def _filter_view(self, label: str) -> dict:
        """Membership, |M| and k_min exactly as the filter sees them, printed.

        k_min is read from a TickResult, so one probe alarm event is fired; the
        overview cards are snapshotted BEFORE that event because the event itself
        refreshes the card (that refresh is the only thing that ever corrects it)."""
        members = self.topology.get_members(self.UUID)
        specs = self.topology.get_member_specs(self.UUID)
        cards = dict(self.overview.cards)          # snapshot BEFORE the probe event below
        agg = self.sa._ensemble_adaptive_aggregators.get(self.UUID)
        k_min = None
        if agg is not None:
            results = []
            real_tick = agg.tick
            with mock.patch.object(agg, 'tick', side_effect=lambda *a, **k: results.append(real_tick(*a, **k)) or results[-1]):
                self.clock.t += 1.0
                self._fire('a0', self.prof_a0)
            k_min = results[-1].k_min
        view = {'members': sorted(members), 'M': len(specs), 'channels': sorted(specs),
                'k_min': k_min, 'overview_cards': cards}
        print(f'\n[{label}] members={view["members"]} |M|={view["M"]} channels={view["channels"]} '
              f'k_min={view["k_min"]} overview={view["overview_cards"]}')
        return view

    # ── tests ──────────────────────────────────────────────────────────────────

    def test_partial_departure_updates_overview_device_count(self):
        """B leaves: model |M| 4 → 2, members 2 → 1, and the overview card follows
        immediately (deviceCount 2 → 1) through on_member_departed.  Before Fix 9
        the card kept deviceCount=2 until the next alarm event."""
        self.clock.t = 0.0; self._fire('a0', self.prof_a0)
        self.clock.t = 1.0; self._fire('b0', self.prof_b)
        before = self._filter_view('before departure')
        self.assertEqual(before['M'], 4)
        self.assertEqual(before['overview_cards'][self.UUID]['deviceCount'], 2)

        self.clock.t = 5.0; self.sa.clear_alarm(self.UUID, 'b0')
        self.clock.t = 6.0
        n_calls_before = len(self.overview.calls)
        self.assertIsNone(self._disconnect(self.dev_b))                 # partial departure
        departure_calls = self.overview.calls[n_calls_before:]

        after = self._filter_view('after B departed')
        # ── model level (a): correct ───────────────────────────────────────────
        self.assertEqual(after['members'], [self.dev_a.epr])
        self.assertEqual(after['channels'], ['a0', 'a1'])                 # |M| = 2
        self.assertEqual(after['k_min'], 2)                               # quorum over survivors
        # ── interface level (b): broken ────────────────────────────────────────
        self.assertEqual(after['overview_cards'][self.UUID]['deviceCount'], 1,
                         msg=f'card still says deviceCount=2 right after the departure: '
                             f'{after["overview_cards"][self.UUID]}')
        self.assertTrue(
            any(c[0] == 'update' and c[1] == self.UUID and c[2] == 1 for c in departure_calls),
            msg=f'no updateEnsemble(device_count=1) after the departure; calls={departure_calls}')

    def test_full_departure_removes_overview_card(self):
        """Both devices leave: topology tears the ensemble down, the processor
        discards it, and PatientOverviewModel.removeEnsemble takes the card off
        the screen.  Before Fix 9 the card stayed for ever, with its last colour."""
        self.clock.t = 0.0; self._fire('a0', self.prof_a0)
        self.clock.t = 1.0; self._fire('b0', self.prof_b)
        self._filter_view('before departure')
        self.clock.t = 6.0
        self.assertIsNone(self._disconnect(self.dev_b))
        self.clock.t = 7.0
        orphaned = self._disconnect(self.dev_a)                           # last member
        self.assertEqual(orphaned, self.UUID)
        # ── model level (a): correct ───────────────────────────────────────────
        self.assertEqual(self.topology.get_members(self.UUID), set())
        self.assertEqual(self.topology.get_member_specs(self.UUID), {})
        self.assertNotIn(self.UUID, self.sa._active_alarms)
        self.assertNotIn(self.UUID, self.sa._ensemble_adaptive_aggregators)
        print(f'\n[after A departed] members=set() |M|=0 overview={self.overview.cards} '
              f'calls={self.overview.calls}')
        # ── interface level (b): broken ────────────────────────────────────────
        self.assertIn(('remove', self.UUID), self.overview.calls,
                      msg='removeEnsemble never called on full release')
        self.assertNotIn(self.UUID, self.overview.cards)

    def test_partial_departure_purges_departed_alarm_keys(self):
        """SD-2 residual: B leaves while b0 is still in the TTL cache.  The purge
        removes b0 (is_alarm_active False), a0 stays; the re-pushed card keeps
        Yellow (a0 active) with deviceCount=1.  On the next event the cache —
        and hence contributing_devices — counts survivors only."""
        self.sa.ALARM_TTL_SEC = 1000.0
        self.clock.t = 0.0; self._fire('a0', self.prof_a0)
        self.clock.t = 1.0; self._fire('b0', self.prof_b)
        self.assertTrue(self.sa.is_alarm_active(self.UUID, 'b0'))
        self.assertTrue(self.overview.cards[self.UUID]['hasActiveAlarms'])
        self.clock.t = 6.0
        self.assertIsNone(self._disconnect(self.dev_b))
        self.assertFalse(self.sa.is_alarm_active(self.UUID, 'b0'))          # purged
        self.assertTrue(self.sa.is_alarm_active(self.UUID, 'a0'))           # survivor kept
        card = self.overview.cards[self.UUID]
        self.assertEqual((card['deviceCount'], card['hasActiveAlarms'], card['isEscalated']),
                         (1, True, False))
        self.assertEqual(self.overview.calls[-1][0], 'update')
        self.clock.t = 7.0; self._fire('a0', self.prof_a0)
        self.assertEqual(set(self.sa._active_alarms[self.UUID]), {'a0'})

    def test_partial_departure_that_empties_cache_resolves_ensemble(self):
        """Only the departed device was alarming: the purge empties the cache →
        latch cleared, grace stamp set, card pushed Blue with deviceCount=1."""
        self.sa.ALARM_TTL_SEC = 1000.0
        self.clock.t = 0.0; self._fire('b0', self.prof_b)
        self.assertTrue(self.overview.cards[self.UUID]['hasActiveAlarms'])
        self.clock.t = 6.0
        self.assertIsNone(self._disconnect(self.dev_b))
        self.assertNotIn(self.UUID, self.sa._active_alarms)
        self.assertIn(self.UUID, self.sa._ensemble_resolved_ts)
        card = self.overview.cards[self.UUID]
        self.assertEqual((card['deviceCount'], card['hasActiveAlarms'], card['isEscalated']),
                         (1, False, False))

    def test_full_departure_while_alarming_leaves_card_yellow(self):
        """A device leaves while its alarm is active: discard_ensemble() pops the
        TTL cache, so the GC never sees the ensemble again and never pushes the
        Blue reset.  Before Fix 9 the stale card kept hasActiveAlarms=True
        (Yellow) — or Red if it was escalated — indefinitely; now it is removed."""
        self.clock.t = 0.0; self._fire('a0', self.prof_a0)              # card → Yellow
        self.assertTrue(self.overview.cards[self.UUID]['hasActiveAlarms'])
        self.clock.t = 1.0; self._disconnect(self.dev_b)
        self.clock.t = 2.0; self.assertEqual(self._disconnect(self.dev_a), self.UUID)
        self.clock.t = 30.0; self.sa._cleanup_stale_alarms()              # GC sweep, nothing to do
        print(f'\n[GC after full departure] overview={self.overview.cards}')
        self.assertNotIn(self.UUID, self.overview.cards,
                         msg=f'stale card after full departure: {self.overview.cards.get(self.UUID)}')


# ══════════════════════════════════════════════════════════════════════════════
#  Restart with the same EPR and another room
# ══════════════════════════════════════════════════════════════════════════════

class TestRestartSameEprOtherRoom(unittest.TestCase):
    """Device A (P, ICU-1) is the only member of E1.  It is restarted with the
    same EPR in ICU-2.  Discovery ignores the new instance while the old worker
    is alive (sdcMyConsumer.py:145-155, keyed by EPR), so the order is always:
    old worker dies → remove_device → release E1 → cooldown → new worker → E2.

    Model: E1 is torn down, E2 = (P, ICU-2) is created — one ensemble, correct.
    Overview: before Fix 9 card E1 was never removed and card E2 was added → the
    SAME patient was shown twice (the 'split' seen on the testbed)."""

    def setUp(self):
        self.manager = _StubManager()
        self.overview = _RecordingOverview()
        self.topology = EnsembleTopologyManager(self.manager, overview_model=self.overview)
        self.sa = SmartAlertAggregator(self.manager, self.topology, overview_model=self.overview)
        self.prof = _profile_with_weight(3.0)
        self.dev = _StubHandler('urn:uuid:dev-A', [('a0', 'MDC.a0', 'Hi', self.prof)])
        self.manager.devices[self.dev.epr] = self.dev

    def tearDown(self):
        self.sa.stop()

    def _bind_via_manager(self, room: str) -> str:
        with mock.patch.object(self.topology, '_extract_patient_and_room', return_value=('P', room)), \
             mock.patch.object(self.topology, '_get_or_fetch_fhir_data', return_value=None):
            self.topology.evaluate_and_bind_device(self.dev)
        return self.dev.ensemble_uuid

    def test_restart_other_room_shows_patient_once(self):
        e1 = self._bind_via_manager('ICU-1')
        self.assertEqual(self.topology.get_members(e1), {self.dev.epr})
        # old worker exits → remove_device
        self.manager.devices.pop(self.dev.epr)
        orphaned = self.topology.release_device(self.dev.epr)
        self.assertEqual(orphaned, e1)
        self.sa.discard_ensemble(orphaned)
        self.sa.on_member_departed(orphaned, orphaned=True)
        # new worker (same EPR), other room
        self.dev = _StubHandler('urn:uuid:dev-A', [('a0', 'MDC.a0', 'Hi', self.prof)])
        self.manager.devices[self.dev.epr] = self.dev
        e2 = self._bind_via_manager('ICU-2')
        self.assertNotEqual(e1, e2)
        # ── model level (a): exactly one ensemble, the old one is gone ─────────
        with self.topology.lock:
            ensembles = dict(self.topology._ensembles_devices)
            key_map = dict(self.topology._patient_to_ensemble_map)
        print(f'\n[restart] ensembles={ensembles} map={key_map} overview={self.overview.cards}')
        self.assertEqual(set(ensembles), {e2})
        self.assertEqual(key_map, {('P', 'ICU-2'): e2})
        # ── interface level (b): the old card survives → patient shown twice ───
        rooms = sorted(c['room'] for c in self.overview.cards.values() if c['patientName'] == 'P')
        self.assertEqual(rooms, ['ICU-2'],
                         msg=f'patient P appears in rooms {rooms}: stale card of released ensemble {e1[:8]}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
