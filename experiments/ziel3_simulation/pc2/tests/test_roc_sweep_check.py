"""Tests of roc_sweep.check_against_net: same length -> full counts and identical crisis ids; longer offline run
(--hours) -> counts inside the span of the network run and the network crisis ids as a subset."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import roc_sweep  # noqa: E402

NET = {'on_counts': {'ecg': 1000, 'vent': 1000, 'spo2': 1000, 'abp': 1000}, 'crisis_ids': ['SIM-P01-C001', 'SIM-P02-C001']}


def result(on, on_span=None, ids=None, partial=False):
    return {'alarms_on': dict(on), 'alarms_on_net_span': dict(on_span if on_span is not None else on),
            'crisis_ids': ids if ids is not None else list(NET['crisis_ids']), 'partial': partial}


class CheckAgainstNetTest(unittest.TestCase):
    def test_same_length_uses_full_counts_and_identical_ids(self):
        ok = {k: 1005 for k in NET['on_counts']}                       # 0.5 % off: inside the tolerance
        self.assertEqual(roc_sweep.check_against_net(result(ok), NET), [])
        bad = dict(ok, spo2=1020)                                      # 2 %: outside
        problems = roc_sweep.check_against_net(result(bad), NET)
        self.assertEqual(len(problems), 1)
        self.assertIn('spo2', problems[0])
        problems = roc_sweep.check_against_net(result(ok, ids=['SIM-P01-C001']), NET)
        self.assertTrue(any('crisis ids differ' in p for p in problems))

    def test_longer_offline_run_compares_the_span_and_subset(self):
        full = {k: 3000 for k in NET['on_counts']}                      # 12 h offline vs 4 h network
        span = {k: 1000 for k in NET['on_counts']}
        more_ids = NET['crisis_ids'] + ['SIM-P01-C002', 'SIM-P03-C001']
        self.assertEqual(roc_sweep.check_against_net(result(full, span, more_ids, partial=True), NET), [])
        # without the partial flag the full counts would fail
        self.assertTrue(roc_sweep.check_against_net(result(full, span, more_ids, partial=False), NET))
        # a network crisis missing offline is a problem
        problems = roc_sweep.check_against_net(result(full, span, ['SIM-P01-C001', 'SIM-P01-C002'], partial=True), NET)
        self.assertTrue(any('missing offline' in p for p in problems))

    def test_main_has_the_hours_option(self):
        import inspect
        src = inspect.getsource(roc_sweep.main)
        self.assertIn("'--hours'", src)
        self.assertIn('hours=a.hours', src)
        self.assertEqual(inspect.signature(roc_sweep.sweep_one).parameters['hours'].default, None)

    def test_seed_option_replaces_only_the_seed_and_disables_the_check(self):
        import inspect
        src = inspect.getsource(roc_sweep.main)
        self.assertIn("'--seed'", src)
        self.assertIn('apply_seed(net, a.seed)', src)
        net = {'name': 'ziel3_12h', 'config': {'seed': 2, 'patients': 20, 'hours': 12.0, 'crisis_params': {'p_cross': 0.9}},
               'on_counts': {'ecg': 1}, 'crisis_ids': ['SIM-P01-C001'], 't0_abs': 1.0}
        out = roc_sweep.apply_seed(net, 8)
        self.assertEqual(out['config']['seed'], 8)
        self.assertEqual(out['seed_source_run'], 2)
        self.assertTrue(out['seed_override'])
        self.assertEqual(out['config']['patients'], 20)
        self.assertEqual(out['config']['crisis_params'], {'p_cross': 0.9})
        self.assertEqual((out['on_counts'], out['crisis_ids']), ({}, []))
        self.assertEqual(net['config']['seed'], 2)                     # the input is not changed
        # the random numbers of a new seed differ from the source seed, the scenario parameters do not
        from patient import make_seed_sequences
        a, _ = make_seed_sequences(2, 2)
        b, _ = make_seed_sequences(8, 2)
        self.assertNotEqual(a[0].generate_state(2).tolist(), b[0].generate_state(2).tolist())


if __name__ == '__main__':
    unittest.main()
