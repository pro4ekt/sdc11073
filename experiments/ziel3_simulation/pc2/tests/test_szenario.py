"""Tests of the Szenario: it creates nothing but crises, is additive and reproducible."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from patient import AlarmEvent, Patient, PatientParams, SOURCES, make_seed_sequences  # noqa: E402
from szenario import CrisisParams, MemoryJournal, Szenario  # noqa: E402


def quiet_params() -> PatientParams:
    return PatientParams(drift_sigma=0.0, noise_sigma=0.0, burst_rate_per_s=0.0, move_rate_per_s=0.0)


def run(patients, sz, steps):
    """Run patients + Szenario; return alarm events per patient and the disturbance seen outside crises."""
    events = {pt.index: [] for pt in patients}
    outside_nonzero = 0
    for k in range(steps):
        t = float(k)
        for pt in patients:
            events[pt.index].extend(e for e in pt.step(t) if isinstance(e, AlarmEvent))
        sz.step(t)
        for pt in patients:
            if pt.index not in sz.active and any(abs(d) > 0.0 for d in pt.disturbance):
                outside_nonzero += 1
    return events, outside_nonzero


class SzenarioTest(unittest.TestCase):
    def setUp(self):
        self.seqs, self.sseq = make_seed_sequences(5, 3)
        self.fast = CrisisParams(rate_per_s=1.0 / 240.0)      # many crises in a short model time

    def test_only_crises_in_the_truth_journal_and_no_disturbance_outside(self):
        patients = [Patient(i + 1, self.seqs[i], quiet_params()) for i in range(3)]
        journal = MemoryJournal()
        sz = Szenario(patients, self.sseq, journal, self.fast)
        events, outside_nonzero = run(patients, sz, 4 * 3600)
        kinds = {r['event'] for r in journal.records}
        self.assertEqual(kinds, {'crisis_start', 'crisis_end'})
        starts = [r for r in journal.records if r['event'] == 'crisis_start']
        ends = [r for r in journal.records if r['event'] == 'crisis_end']
        self.assertGreater(len(ends), 20)
        self.assertGreaterEqual(len(starts), len(ends))
        self.assertEqual(outside_nonzero, 0)                       # Szenario touches nothing outside a crisis
        for r in ends:
            self.assertGreaterEqual(r['duration_s'], 15.0)
            for key, v in r['sources'].items():
                self.assertTrue(0.0 <= v['delay_s'] <= 15.0, key)
        for r in starts:
            for key, v in r['sources'].items():
                lo, hi = (self.fast.amp_cross if v['planned_cross'] else self.fast.amp_nocross)
                self.assertTrue(lo <= v['amp'] <= hi, key)

    def test_quiet_patients_alarm_only_inside_crises_and_as_planned(self):
        patients = [Patient(i + 1, self.seqs[i], quiet_params()) for i in range(3)]
        journal = MemoryJournal()
        sz = Szenario(patients, self.sseq, journal, self.fast)
        events, _ = run(patients, sz, 4 * 3600)
        ends = [r for r in journal.records if r['event'] == 'crisis_end']
        for r in ends:
            for key, v in r['sources'].items():
                if v['planned_cross'] and v['delay_s'] + self.fast.ramp_s <= r['duration_s']:
                    self.assertTrue(v['crossed'], (r['crisis_id'], key))
                    self.assertGreaterEqual(v['t_cross_rel_s'], v['delay_s'])
                if not v['planned_cross']:
                    self.assertFalse(v['crossed'], (r['crisis_id'], key))   # quiet patient: push < 1 never crosses
        windows = {}
        for r in ends:
            windows.setdefault(r['patient_index'], []).append((r['t_model_start'], r['t_model_end'] + 60.0))
        for idx, evs in events.items():
            for e in evs:
                if e.on:
                    self.assertTrue(any(a <= e.t <= b for a, b in windows.get(idx, [])), (idx, e))

    def test_crisis_is_additive_and_false_alarms_survive(self):
        """Twin patients with full randomness: one with Szenario, one without. The free process is
        identical, and the patient with crises still has alarms outside the crisis windows."""
        seqs, sseq = make_seed_sequences(9, 2)
        twins_a = [Patient(i + 1, seqs[i]) for i in range(2)]
        twins_b = [Patient(i + 1, seqs[i]) for i in range(2)]
        journal = MemoryJournal()
        sz = Szenario(twins_b, sseq, journal, self.fast)
        free_a, free_b = [], []
        events_b = {1: [], 2: []}
        steps = 3 * 3600
        for k in range(steps):
            t = float(k)
            for pt in twins_a:
                pt.step(t)
                free_a.append(tuple(float(x) for x in pt.free_u))
            for pt in twins_b:
                events_b[pt.index].extend(e for e in pt.step(t) if isinstance(e, AlarmEvent))
                free_b.append(tuple(float(x) for x in pt.free_u))
            sz.step(t)
        self.assertEqual(free_a, free_b)
        ends = [r for r in journal.records if r['event'] == 'crisis_end']
        self.assertGreater(len(ends), 10)
        windows = {}
        for r in ends:
            windows.setdefault(r['patient_index'], []).append((r['t_model_start'], r['t_model_end'] + 60.0))
        outside = [e for idx, evs in events_b.items() for e in evs
                   if e.on and not any(a <= e.t <= b for a, b in windows.get(idx, []))]
        self.assertGreater(len(outside), 20)

    def test_p_cross_controls_the_planned_crossings(self):
        """With p_cross = 0.6 about 60 % of the sources are planned to cross; the default stays 0.9."""
        self.assertEqual(CrisisParams().p_cross, 0.9)
        seqs, sseq = make_seed_sequences(21, 4)
        patients = [Patient(i + 1, seqs[i], quiet_params()) for i in range(4)]
        journal = MemoryJournal()
        sz = Szenario(patients, sseq, journal, CrisisParams(rate_per_s=1.0 / 200.0, p_cross=0.6))
        run(patients, sz, 6 * 3600)
        starts = [r for r in journal.records if r['event'] == 'crisis_start']
        planned = [v['planned_cross'] for r in starts for v in r['sources'].values()]
        self.assertGreater(len(planned), 300)
        share = sum(planned) / len(planned)
        self.assertTrue(0.52 <= share <= 0.68, share)
        ends = [r for r in journal.records if r['event'] == 'crisis_end']
        n_crossed = [sum(1 for v in r['sources'].values() if v['crossed']) for r in ends]
        self.assertLess(sum(1 for n in n_crossed if n == 4) / len(n_crossed), 0.3)   # quiet patients: crossings = plan

    def test_reproducible_by_seed(self):
        def make(seed):
            seqs, sseq = make_seed_sequences(seed, 2)
            pts = [Patient(i + 1, seqs[i], quiet_params()) for i in range(2)]
            j = MemoryJournal()
            run(pts, Szenario(pts, sseq, j, self.fast), 2 * 3600)
            return j.records
        self.assertEqual(make(1), make(1))
        self.assertNotEqual(make(1), make(2))


if __name__ == '__main__':
    unittest.main()
