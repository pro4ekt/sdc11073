"""Tests of the provider pool without starting any process: ports, command lines, echo parsing."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from patient import SOURCES  # noqa: E402
from provider_pool import MDIB_DIR, ProviderProc, epr_of, make_specs, port_of  # noqa: E402
from szenario import MemoryJournal  # noqa: E402


class PortsAndSpecsTest(unittest.TestCase):
    def test_port_formula(self):
        self.assertEqual(port_of(1, 1), 13000)
        self.assertEqual(port_of(1, 4), 13003)
        self.assertEqual(port_of(2, 1), 13004)
        self.assertEqual(port_of(20, 4), 13079)

    def test_specs_follow_the_contract(self):
        specs = make_specs('ziel3_1h', 20, run_seed=1)
        self.assertEqual(len(specs), 80)
        self.assertEqual(len({s.port for s in specs}), 80)
        self.assertEqual(len({s.epr for s in specs}), 80)
        self.assertEqual(len({s.serial for s in specs}), 80)
        self.assertEqual(len({s.seed for s in specs}), 80)
        first = specs[0]
        self.assertEqual(first.friendly, 'SIM-P01 ECG')
        self.assertEqual(first.patient_id, 'SIM-P01')
        argv = first.argv('python.exe', '192.168.0.100')
        for flag, val in (('--manufacturer', 'SDC Sim'), ('--model', 'Sim ECG'), ('--room', 'ICU-3'), ('--bed', '1'),
                          ('--patient-id', 'SIM-P01'), ('--ip', '192.168.0.100'), ('--port', '13000'),
                          ('--friendly-name', 'SIM-P01 ECG')):
            self.assertEqual(argv[argv.index(flag) + 1], val, flag)
        self.assertIn('--no-tls', argv)
        self.assertNotIn('--certs-dir', argv)
        self.assertEqual(argv[argv.index('--mdib') + 1], str(MDIB_DIR / 'ecg_mdib_v2.xml'))
        self.assertTrue(argv[argv.index('--epr') + 1].startswith('urn:uuid:'))
        models = [s.source.model for s in specs[:4]]
        self.assertEqual(models, ['Sim ECG', 'Sim Vent', 'Sim SpO2', 'Sim ABP'])
        channels = [s.source.channel for s in specs[:4]]
        self.assertEqual(channels, ['tachycardia', 'mv_low', 'desaturation', 'dia_low'])
        self.assertEqual([s.port for s in specs[:5]], [13000, 13001, 13002, 13003, 13004])
        self.assertEqual(specs[79].friendly, 'SIM-P20 ABP')
        self.assertEqual(specs[79].argv('py', 'x')[specs[79].argv('py', 'x').index('--bed') + 1], '20')

    def test_epr_stable_within_run_and_different_between_runs(self):
        self.assertEqual(epr_of('ziel3_1h', 3, 2), epr_of('ziel3_1h', 3, 2))
        self.assertNotEqual(epr_of('ziel3_1h', 3, 2), epr_of('ziel3_12h', 3, 2))
        self.assertNotEqual(epr_of('ziel3_1h', 3, 2), epr_of('ziel3_1h', 3, 3))

    def test_mdib_files_exist(self):
        for s in SOURCES:
            self.assertTrue((MDIB_DIR / s.mdib).exists(), s.mdib)


class EchoParsingTest(unittest.TestCase):
    def setUp(self):
        self.journal = MemoryJournal()
        self.proc = ProviderProc(make_specs('t', 1, 1)[0], 'python', '127.0.0.1', self.journal)

    def events(self, kind):
        return [r for r in self.journal.records if r['event'] == kind]

    def test_ready_alarm_status_and_publish_lines(self):
        p = self.proc
        p._handle('[1792625248249 01:27:28.249] publish: tachycardia=72(fixed)')
        self.assertEqual(len(p.lines), 0)                                   # publish lines are never kept
        p._handle("[1792625248249 01:27:28.249] ready: type 'help' for commands (channels: tachycardia, tachypnea)")
        self.assertTrue(p.ready.is_set())
        self.assertEqual(len(self.events('provider_ready')), 1)
        p._pending_alarm.append((1792625248.000, True, '131'))
        p._handle('[1792625248249 01:27:28.249] value tachycardia = 131 (fixed; rest noise off)')
        self.assertEqual(p.last_value_echo, '131')
        p._handle('[1792625248300 01:27:28.300] alarm tachycardia ON (condition ecg_alert_cond_tachycardia, '
                  'signal ecg_alert_signal_tachycardia, DeterminationTime=now)')
        al = self.events('alarm')
        self.assertEqual(len(al), 1)
        self.assertTrue(al[0]['on'])
        self.assertEqual(al[0]['t_prov_ms'], 1792625248300)
        self.assertEqual(al[0]['value'], '131')
        self.assertEqual(al[0]['patient'], 'SIM-P01')
        self.assertEqual(al[0]['source'], 'ecg')
        self.assertAlmostEqual(al[0]['latency_ms'], 300.0, places=1)
        p._handle('[1792625248400 01:27:28.400] status: ack=accept selfcheck=on ensemble=(none) subscriptions=2')
        st = self.events('status')
        self.assertEqual(len(st), 1)
        self.assertEqual(st[0]['subscriptions'], 2)
        self.assertEqual(st[0]['selfcheck'], 'on')
        p._handle("[1792625248500 01:27:28.500] error: unknown channel 'foo' (known: tachycardia); no change")
        self.assertEqual(len(self.events('provider_msg')), 1)

    def test_alarm_when_provider_not_running_is_dropped_but_remembered(self):
        p = self.proc
        ok = p.set_alarm(True, '131')
        self.assertFalse(ok)
        self.assertEqual(len(self.events('alarm_dropped')), 1)
        self.assertTrue(p.alarm_desired)                                     # resync after a restart uses this
        self.assertEqual(p.last_value_text, '131')


if __name__ == '__main__':
    unittest.main()
