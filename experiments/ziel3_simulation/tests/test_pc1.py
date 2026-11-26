"""Tests fuer PC 1 der Ziel-3-Simulation (ohne Netz, ohne Provider, ohne PC 2).

Aufruf (Repository-Wurzel):
    .venv\\Scripts\\python -m unittest discover -s experiments/ziel3_simulation/tests -v
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import threading
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SIM = HERE.parent
REPO = SIM.parents[1]
for _p in (SIM, REPO / 'Dashboard'):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from pc1_observer import RotObserver  # noqa: E402
import pc1_dashboard as pc1  # noqa: E402


def _read(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class ObserverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / 'decisions.jsonl'
        self.clock = FakeClock()
        self.obs = RotObserver(self.path, clock=self.clock, mono=self.clock)

    def tearDown(self) -> None:
        self.obs.close()
        self.tmp.cleanup()

    def test_one_rot_pair_despite_periodic_calls(self):
        u = 'ens-1'
        self.obs.updateEnsemble(u, 'SIM-P01', 'ICU-3', 4, False, 0.0, False)      # Blau
        self.obs.updateEnsemble(u, 'SIM-P01', 'ICU-3', 4, False, 0.69, True)      # Gelb
        self.clock.t = 1010.0
        self.obs.updateEnsemble(u, 'SIM-P01', 'ICU-3', 4, True, 0.88, True)       # Rot
        for _ in range(5):                                                        # periodisch, unveraendert
            self.obs.updateEnsemble(u, 'SIM-P01', 'ICU-3', 4, True, 0.9, True)
        self.clock.t = 1040.0
        self.obs.updateEnsemble(u, 'SIM-P01', 'ICU-3', 4, False, 0.0, False)      # alle Alarme aus
        self.obs.updateEnsemble(u, 'SIM-P01', 'ICU-3', 4, False, 0.0, False)
        recs = _read(self.path)
        self.assertEqual([r['event'] for r in recs], ['rot_start', 'rot_end'])
        self.assertEqual(recs[0]['patient_id'], 'SIM-P01')
        self.assertEqual(recs[0]['t_abs'], 1010.0)
        self.assertAlmostEqual(recs[0]['sdc_score'], 0.88)
        self.assertEqual(recs[1]['duration_s'], 30.0)
        self.assertEqual(recs[1]['cause'], 'alarms_cleared')
        self.assertEqual(self.obs.rot_count, 1)
        self.assertEqual(self.obs.snapshot()['rot_active'], 0)

    def test_two_patients_independent(self):
        self.obs.updateEnsemble('a', 'SIM-P01', 'ICU-3', 4, True, 0.9)
        self.obs.updateEnsemble('b', 'SIM-P02', 'ICU-3', 4, True, 0.7)
        self.obs.updateEnsemble('a', 'SIM-P01', 'ICU-3', 4, False, 0.0)
        recs = _read(self.path)
        self.assertEqual([(r['event'], r['patient_id']) for r in recs],
                         [('rot_start', 'SIM-P01'), ('rot_start', 'SIM-P02'), ('rot_end', 'SIM-P01')])
        self.assertEqual(self.obs.snapshot()['rot_active_patients'], ['SIM-P02'])
        self.assertEqual(self.obs.patients['b'], ('SIM-P02', 'ICU-3'))

    def test_remove_while_rot_ends_it(self):
        self.obs.updateEnsemble('a', 'SIM-P03', 'ICU-3', 4, True, 0.9)
        self.obs.removeEnsemble('a')
        self.obs.removeEnsemble('a')                       # kein zweites Ende
        recs = _read(self.path)
        self.assertEqual([r['event'] for r in recs], ['rot_start', 'rot_end'])
        self.assertEqual(recs[1]['cause'], 'ensemble_removed')

    def test_lost_and_back(self):
        self.obs.markLost('SIM-P04', 'ICU-3', 'urn:uuid:dev-1', 'is_connected', False, 999.5)
        self.obs.clearLost('urn:uuid:dev-1')
        recs = _read(self.path)
        self.assertEqual([r['event'] for r in recs], ['device_lost', 'device_back'])
        self.assertEqual(recs[0]['t_abs_loss'], 999.5)
        self.assertEqual(recs[1]['patient_id'], 'SIM-P04')
        self.assertEqual(self.obs.snapshot()['devices_lost'], 0)

    def test_thread_safety(self):
        def worker(uuid: str, n: int) -> None:
            for i in range(n):                                   # gerade i: Rot an, ungerade i: Rot aus
                self.obs.updateEnsemble(uuid, f'P-{uuid}', 'ICU-3', 4, i % 2 == 0, 0.5)
        threads = [threading.Thread(target=worker, args=(f'e{k}', 200)) for k in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        recs = _read(self.path)
        for k in range(4):
            evs = [r['event'] for r in recs if r['ensemble'] == f'e{k}']
            self.assertEqual(evs, ['rot_start', 'rot_end'] * 100)
        self.assertEqual(self.obs.rot_count, 400)


class ContractTests(unittest.TestCase):
    """Alles Patientenzahl-abhaengige kommt aus dem Vertrag (N = 20, 4 Quellen, 80 Provider)."""

    def setUp(self) -> None:
        self.contract = pc1.load_contract(SIM / 'ziel3_contract.json')

    def test_patients(self):
        patients = self.contract['patients']
        self.assertEqual(len(patients), 20)
        self.assertEqual(len(set(patients)), 20)
        self.assertEqual(patients, [f'SIM-P{i:02d}' for i in range(1, 21)])

    def test_expected_counts_and_ports(self):
        exp = pc1.expected_counts(self.contract)
        self.assertEqual(exp['n_patients'], 20)
        self.assertEqual(exp['n_sources'], 4)
        self.assertEqual(exp['expected_devices'], 80)
        self.assertEqual((exp['port_first'], exp['port_last']), (13000, 13079))
        self.assertTrue(exp['ports_consistent'])
        self.assertEqual(pc1.provider_port(self.contract, 1, 1), 13000)
        self.assertEqual(pc1.provider_port(self.contract, 1, 4), 13003)
        self.assertEqual(pc1.provider_port(self.contract, 2, 1), 13004)
        self.assertEqual(pc1.provider_port(self.contract, 20, 4), 13079)
        ports = {pc1.provider_port(self.contract, i, j) for i in range(1, 21) for j in range(1, 5)}
        self.assertEqual(ports, set(range(13000, 13080)))                     # alle 80 verschieden, luckenlos

    def test_inconsistent_ports_are_flagged(self):
        c = json.loads(json.dumps(self.contract))
        c['patients'] = c['patients'][:4]
        self.assertFalse(pc1.expected_counts(c)['ports_consistent'])         # 13000-13079 fuer 16 Provider
        c['network']['provider_ports']['last'] = 13015
        self.assertTrue(pc1.expected_counts(c)['ports_consistent'])

    def test_network_and_clock(self):
        net = self.contract['network']
        self.assertEqual(net['pc2_ip'], '192.168.0.100')
        self.assertEqual(net['pc1_ip'], '192.168.0.106')
        self.assertEqual(net['tls'], 'no_tls')
        self.assertEqual((net['clock_port'], net['clock_poll_s']), (5558, 10))
        self.assertEqual(self.contract['clock']['reply']['state'], 'running | done')


class ProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = pc1.load_contract(SIM / 'ziel3_contract.json')
        self.raw = pc1.build_raw_clinical_db(self.contract)

    def test_exactly_four_profiles(self):
        entries = pc1.profile_entries(self.raw)
        self.assertEqual(len(entries), 4)                                         # |M| = 4
        self.assertEqual({e[1] for e in entries}, {s['model'] for s in self.contract['sources']})
        self.assertEqual({e[2] for e in entries}, {s['loinc'] for s in self.contract['sources']})
        self.assertTrue(all(e[0] == 'SDC Sim' for e in entries))
        for _m, _model, code in entries:
            prof = self.raw['device_profiles']['SDC Sim'][_model][code]
            self.assertTrue(0.0 < prof['false_alarm_rate'] < prof['sensitivity'])  # Zulassungsregel

    def test_context_and_filter_params(self):
        self.assertEqual(self.raw['odds_ratios'], {})
        self.assertEqual(self.raw['base_prob_P0'], 0.5)
        self.assertEqual(self.raw['filter_params'], {'horizon_T': 10.0, 'alpha': 0.5, 'delta_eval': 1.0})
        self.assertEqual(self.raw['priority_map'], {'None': 0, 'Lo': 1, 'Me': 2, 'Hi': 3})
        self.assertEqual([s['priority'] for s in self.contract['sources']], ['Hi', 'Hi', 'Me', 'Lo'])

    def test_repository_resolves_only_the_four_channels(self):
        from app.alarms.device_profile_repo import DeviceProfileRepository
        repo = DeviceProfileRepository()
        repo._raw_clinical_db = self.raw
        ok = repo.get_profile('SDC Sim', 'Sim ECG', '8867-4')
        self.assertIsNotNone(ok)
        self.assertAlmostEqual(ok.true_positive_rate, 0.90)
        self.assertAlmostEqual(ok.false_positive_rate, 0.00313)
        self.assertIsNone(repo.get_profile('SDC Sim', 'Sim ECG', '76270-8'))      # zweiter Kanal der ecg-MDIB
        self.assertIsNone(repo.get_profile('SDC Sim', 'Sim Vent', '76007-4'))     # vt_low ohne Profil
        self.assertIsNone(repo.get_profile('SDC Testbed', 'ECG Monitor T1', '8867-4'))
        self.assertEqual(repo.get_priority('Hi'), 3)
        self.assertEqual(repo.get_priority('Lo'), 1)
        self.assertEqual(repo.get_odds_ratios(), {})
        self.assertAlmostEqual(repo.get_base_prob(), 0.5)
        self.assertEqual(repo.get_filter_params(), (10.0, 0.5))
        self.assertAlmostEqual(repo.get_eval_interval(), 1.0)


if __name__ == '__main__':
    unittest.main()
