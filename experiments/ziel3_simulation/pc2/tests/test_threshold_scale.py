"""Tests of the threshold scale h (PatientParams.threshold_scale, option --threshold-scale):
h = 1.0 is the contract bit for bit, other h move the thresholds of all four sources together,
the values (random numbers) never depend on h."""
import inspect
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from patient import AlarmEvent, Patient, PatientParams, SOURCES, make_seed_sequences, scaled_thresholds  # noqa: E402
from szenario import CrisisParams, MemoryJournal, Szenario  # noqa: E402


def simulate(h, n=3, hours=2, seed=7):
    """Events per patient, all rounded values, crisis journal of n patients for `hours` with threshold scale h."""
    seqs, sseq = make_seed_sequences(seed, n)
    params = PatientParams() if h is None else PatientParams(threshold_scale=h)
    pts = [Patient(i + 1, seqs[i], params) for i in range(n)]
    journal = MemoryJournal()
    sz = Szenario(pts, sseq, journal, CrisisParams(rate_per_s=1.0 / 600.0))
    events = {pt.index: [] for pt in pts}
    values = []
    for k in range(hours * 3600):
        t = float(k)
        for pt in pts:
            events[pt.index].extend(pt.step(t))
            values.append(tuple(float(v) for v in pt.value))
        sz.step(t)
    crises = [(r['crisis_id'], r['t_model_start'], r['t_model_end']) for r in journal.records if r['event'] == 'crisis_end']
    return events, values, crises


def n_on(events):
    return sum(1 for evs in events.values() for e in evs if isinstance(e, AlarmEvent) and e.on)


class ThresholdScaleTest(unittest.TestCase):
    def test_default_is_one_and_contract_values_are_bit_identical(self):
        self.assertEqual(PatientParams().threshold_scale, 1.0)
        self.assertEqual(PatientParams().to_dict()['threshold_scale'], 1.0)
        for s in SOURCES:
            self.assertEqual(scaled_thresholds(s, 1.0), (s.on, s.off))
        pt = Patient(1, make_seed_sequences(1, 1)[0][0])
        self.assertEqual([(hy.on, hy.off) for hy in pt.hyst], [(s.on, s.off) for s in SOURCES])
        self.assertEqual(pt.thresholds, [(s.on, s.off) for s in SOURCES])

    def test_scaled_values(self):
        self.assertEqual(scaled_thresholds(SOURCES[0], 1.2), (72.0 + 48.0 * 1.2, 72.0 + 43.0 * 1.2))   # HR 129.6 / 123.6
        on, off = scaled_thresholds(SOURCES[1], 0.8)                                                  # MV 4.6 / 4.92
        self.assertAlmostEqual(on, 4.6)
        self.assertAlmostEqual(off, 4.92)
        for s in SOURCES:
            for h in (0.05, 0.5, 0.8, 1.2, 1.6, 2.0):
                on, off = scaled_thresholds(s, h)
                self.assertAlmostEqual(abs(on - s.base) / s.distance, h)                                # alarm at x >= h
                self.assertAlmostEqual(abs(off - s.base) / s.distance, h * abs(s.off - s.base) / s.distance)
                self.assertTrue((off < on) if s.direction > 0 else (off > on))
        pt = Patient(1, make_seed_sequences(1, 1)[0][0], PatientParams(threshold_scale=1.5))
        self.assertEqual([hy.on for hy in pt.hyst], [on for on, _ in pt.thresholds])
        for h in (0.0, -1.0):
            with self.assertRaises(ValueError):
                Patient(1, make_seed_sequences(1, 1)[0][0], PatientParams(threshold_scale=h))

    def test_h_one_equals_default_and_values_do_not_depend_on_h(self):
        e_def, v_def, c_def = simulate(None)
        e1, v1, c1 = simulate(1.0)
        self.assertEqual(e1, e_def)
        self.assertEqual(v1, v_def)
        self.assertEqual(c1, c_def)
        e12, v12, c12 = simulate(1.2)
        e08, v08, c08 = simulate(0.8)
        self.assertEqual(v12, v1)             # same random numbers -> identical values for every h
        self.assertEqual(v08, v1)
        self.assertEqual(c12, c1)             # the crises (Szenario) do not depend on h either
        self.assertEqual(c08, c1)
        self.assertGreater(n_on(e1), 50)
        self.assertLess(n_on(e12), n_on(e1))  # higher threshold: fewer alarms
        self.assertGreater(n_on(e08), n_on(e1))

    def test_runner_and_offline_check_have_the_option(self):
        import pc2_run
        import offline_check
        src = inspect.getsource(pc2_run.main)
        self.assertIn("'--threshold-scale', type=float, default=PatientParams().threshold_scale", src)
        self.assertIn('pparams = PatientParams(threshold_scale=a.threshold_scale)', src)
        self.assertIn("'threshold_scale': a.threshold_scale", src)
        self.assertIn("'--threshold-scale'", inspect.getsource(offline_check.main))


if __name__ == '__main__':
    unittest.main()
