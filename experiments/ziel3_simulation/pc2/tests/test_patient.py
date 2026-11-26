"""Tests of the patient model: hysteresis, directional thresholds, seeds, additivity of crises."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from patient import AlarmEvent, Hysteresis, Patient, PatientParams, SOURCES, make_seed_sequences  # noqa: E402


def quiet_params() -> PatientParams:
    """No randomness at all: values sit exactly on the base levels."""
    return PatientParams(drift_sigma=0.0, noise_sigma=0.0, burst_rate_per_s=0.0, move_rate_per_s=0.0)


def run_events(pt: Patient, steps: int, disturb=None):
    """Step a patient; ``disturb(t)`` may return {j: du} to apply before the step. Returns (events, free_u history)."""
    events, free = [], []
    for k in range(steps):
        t = float(k)
        if disturb is not None:
            for j, du in (disturb(t) or {}).items():
                pt.set_disturbance(j, du)
        events.extend(pt.step(t))
        free.append(tuple(float(x) for x in pt.free_u))
    return events, free


class HysteresisTest(unittest.TestCase):
    def test_upward_with_hysteresis_and_min_on(self):
        h = Hysteresis(on=120.0, off=115.0, direction=+1, min_on_s=2.0)
        self.assertIsNone(h.update(100.0, 0.0))
        self.assertTrue(h.update(121.0, 1.0))            # OFF -> ON
        self.assertIsNone(h.update(117.0, 2.0))           # between off and on: stays ON
        self.assertIsNone(h.update(114.0, 2.5))           # below off but min ON time not reached
        self.assertFalse(h.update(114.0, 3.0))            # ON -> OFF after >= 2 s
        self.assertIsNone(h.update(118.0, 4.0))           # between: stays OFF (needs >= on)
        self.assertTrue(h.update(120.0, 5.0))             # exactly on -> ON

    def test_downward_direction(self):
        h = Hysteresis(on=90.0, off=92.0, direction=-1, min_on_s=2.0)
        self.assertIsNone(h.update(95.0, 0.0))
        self.assertIsNone(h.update(91.0, 1.0))            # not yet <= 90
        self.assertTrue(h.update(89.0, 2.0))
        self.assertIsNone(h.update(91.0, 3.0))            # between: stays ON
        self.assertIsNone(h.update(93.0, 3.5))            # min ON time
        self.assertFalse(h.update(93.0, 4.0))

    def test_invalid_configuration(self):
        with self.assertRaises(ValueError):
            Hysteresis(on=120.0, off=125.0, direction=+1)
        with self.assertRaises(ValueError):
            Hysteresis(on=90.0, off=88.0, direction=-1)
        with self.assertRaises(ValueError):
            Hysteresis(on=90.0, off=92.0, direction=0)


class PatientThresholdTest(unittest.TestCase):
    def test_quiet_patient_has_no_alarms_and_sits_on_base(self):
        seqs, _ = make_seed_sequences(7, 1)
        pt = Patient(1, seqs[0], quiet_params())
        events, _ = run_events(pt, 300)
        self.assertEqual(events, [])
        for s in SOURCES:
            self.assertAlmostEqual(pt.get_value(s.j), s.base)

    def test_directional_thresholds(self):
        seqs, _ = make_seed_sequences(7, 1)
        for s in SOURCES:
            pt = Patient(1, seqs[0], quiet_params())
            # push in the WRONG direction (away from the alarm side): no alarm
            events, _ = run_events(pt, 20, disturb=lambda t: {s.j: -1.5})
            self.assertEqual([e for e in events if isinstance(e, AlarmEvent)], [], s.key)
            v = pt.get_value(s.j)
            if s.direction > 0:
                self.assertLess(v, s.base)
            else:
                self.assertGreater(v, s.base)
            # push towards the alarm side: alarm ON, value beyond the ON threshold
            pt2 = Patient(1, seqs[0], quiet_params())
            events, _ = run_events(pt2, 20, disturb=lambda t: {s.j: 1.5})
            ons = [e for e in events if isinstance(e, AlarmEvent) and e.on]
            self.assertEqual(len(ons), 1, s.key)
            self.assertEqual(ons[0].j, s.j)
            if s.direction > 0:
                self.assertGreaterEqual(ons[0].value, s.on)
            else:
                self.assertLessEqual(ons[0].value, s.on)
            # other sources untouched
            self.assertEqual([e for e in events if isinstance(e, AlarmEvent) and e.j != s.j], [])

    def test_minimum_on_time_and_hysteresis_in_patient(self):
        seqs, _ = make_seed_sequences(7, 1)
        pt = Patient(1, seqs[0], quiet_params())
        # one-second push beyond the threshold, then back to base
        events, _ = run_events(pt, 10, disturb=lambda t: {1: 1.5 if t == 3.0 else 0.0})
        al = [e for e in events if isinstance(e, AlarmEvent)]
        self.assertEqual([(e.on, e.t) for e in al], [(True, 3.0), (False, 5.0)])   # OFF not before 2 s
        # value between OFF and ON thresholds keeps the alarm ON
        pt = Patient(1, seqs[0], quiet_params())
        s = SOURCES[0]
        between = ((s.off + s.on) / 2.0 - s.base) / s.distance   # dimensionless level between the two thresholds
        events, _ = run_events(pt, 30, disturb=lambda t: {1: 1.5 if t < 3.0 else between})
        al = [e for e in events if isinstance(e, AlarmEvent)]
        self.assertEqual([e.on for e in al], [True])

    def test_values_are_bounded_and_rounded(self):
        seqs, _ = make_seed_sequences(3, 1)
        pt = Patient(1, seqs[0], PatientParams(drift_sigma=0.8, noise_sigma=0.8, burst_amp_median=3.0))
        for k in range(2000):
            pt.step(float(k))
            for s in SOURCES:
                v = pt.get_value(s.j)
                self.assertGreaterEqual(v, s.lo)
                self.assertLessEqual(v, s.hi)
                self.assertAlmostEqual(v, round(v, s.decimals))


class SeedTest(unittest.TestCase):
    def test_same_seed_same_result_different_seed_different(self):
        a_seqs, _ = make_seed_sequences(42, 3)
        b_seqs, _ = make_seed_sequences(42, 3)
        c_seqs, _ = make_seed_sequences(43, 3)
        ev_a, free_a = run_events(Patient(2, a_seqs[1]), 3000)
        ev_b, free_b = run_events(Patient(2, b_seqs[1]), 3000)
        ev_c, free_c = run_events(Patient(2, c_seqs[1]), 3000)
        self.assertEqual(ev_a, ev_b)
        self.assertEqual(free_a, free_b)
        self.assertNotEqual(free_a, free_c)
        # patients of one run differ from each other
        ev_other, free_other = run_events(Patient(1, a_seqs[0]), 3000)
        self.assertNotEqual(free_a, free_other)
        self.assertGreater(len([e for e in ev_a if isinstance(e, AlarmEvent)]), 0)


class CrisisAdditivityTest(unittest.TestCase):
    def test_crisis_does_not_erase_the_free_process(self):
        """A disturbance on one source changes only that source's alarms; the free process and the
        other sources' events are identical to the twin patient without the disturbance."""
        seqs, _ = make_seed_sequences(11, 1)
        twin_a = Patient(1, seqs[0])
        twin_b = Patient(1, seqs[0])
        ev_a, free_a = run_events(twin_a, 4000)
        ev_b, free_b = run_events(twin_b, 4000, disturb=lambda t: {1: 1.6 if 1000.0 <= t < 1040.0 else 0.0})
        self.assertEqual(free_a, free_b)                                       # free process untouched
        self.assertEqual([e for e in ev_a if not (isinstance(e, AlarmEvent) and e.j == 1)],
                         [e for e in ev_b if not (isinstance(e, AlarmEvent) and e.j == 1)])
        ons_b = [e for e in ev_b if isinstance(e, AlarmEvent) and e.j == 1 and e.on and 1000.0 <= e.t <= 1045.0]
        self.assertGreaterEqual(len(ons_b), 1)                                 # the crisis did raise the alarm
        # false alarms of the other sources still happen during the crisis window of source 1
        others_b = [e for e in ev_b if isinstance(e, AlarmEvent) and e.j != 1]
        self.assertEqual(others_b, [e for e in ev_a if isinstance(e, AlarmEvent) and e.j != 1])


if __name__ == '__main__':
    unittest.main()
