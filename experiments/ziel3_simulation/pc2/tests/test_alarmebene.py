"""Tests of the generator "alarmebene" (run D) and of the crisis-duration options."""
import inspect
import pathlib
import statistics
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from alarmebene import AlarmLevelParams, AlarmLevelPatient, AlarmLevelSzenario, make_patients, make_szenario  # noqa: E402
from patient import AlarmEvent, MovementEvent, Patient, make_seed_sequences  # noqa: E402
from szenario import CrisisParams, MemoryJournal, Szenario  # noqa: E402


def run(patients, sz, steps):
    events = {pt.index: [] for pt in patients}
    for k in range(steps):
        t = float(k)
        for pt in patients:
            events[pt.index].extend(pt.step(t))
        if sz is not None:
            sz.step(t)
    return events


def intervals(events, j):
    """(t_on, t_off) of source j from a list of AlarmEvents (open interval closed at the end with None)."""
    out, t_on = [], None
    for e in events:
        if isinstance(e, AlarmEvent) and e.j == j:
            if e.on:
                t_on = e.t
            elif t_on is not None:
                out.append((t_on, e.t))
                t_on = None
    if t_on is not None:
        out.append((t_on, None))
    return out


class ReproducibilityTest(unittest.TestCase):
    def test_same_seed_same_events_different_seed_different(self):
        a, _ = make_seed_sequences(31, 2)
        b, _ = make_seed_sequences(31, 2)
        c, _ = make_seed_sequences(32, 2)
        ev_a = run([AlarmLevelPatient(1, a[0])], None, 20000)[1]
        ev_b = run([AlarmLevelPatient(1, b[0])], None, 20000)[1]
        ev_c = run([AlarmLevelPatient(1, c[0])], None, 20000)[1]
        self.assertEqual(ev_a, ev_b)
        self.assertNotEqual(ev_a, ev_c)
        self.assertGreater(len([e for e in ev_a if isinstance(e, AlarmEvent)]), 50)

    def test_default_generator_is_unchanged(self):
        seqs, sseq = make_seed_sequences(1, 2)
        pts = make_patients('prozess', seqs)
        self.assertTrue(all(isinstance(p, Patient) for p in pts))
        self.assertIsInstance(make_szenario('prozess', pts, sseq, MemoryJournal(), None), Szenario)
        self.assertNotIsInstance(make_szenario('prozess', pts, sseq, MemoryJournal(), None), AlarmLevelSzenario)
        import pc2_run
        src = inspect.getsource(pc2_run.main)
        self.assertIn("choices=('prozess', 'alarmebene'), default='prozess'", src)
        self.assertIn("'--crisis-min', type=float, default=CrisisParams().dur_min_s", src)
        self.assertIn("'--crisis-extra-mean', type=float, default=CrisisParams().dur_extra_mean_s", src)
        self.assertEqual((CrisisParams().dur_min_s, CrisisParams().dur_extra_mean_s, CrisisParams().p_cross), (15.0, 15.0, 0.9))


class TimeShareTest(unittest.TestCase):
    def test_fpr_durations_and_bursts(self):
        """10 patients x 6 h without crises: FPR per channel ~ 0.026, mean length ~ 8 s, CV ~ 1, Stoss share ~ c_art*FPR."""
        n, hours = 10, 6
        seqs, sseq = make_seed_sequences(41, n)
        pts = [AlarmLevelPatient(i + 1, seqs[i]) for i in range(n)]
        sz = AlarmLevelSzenario(pts, sseq, MemoryJournal(), CrisisParams(rate_per_s=0.0))
        steps = hours * 3600
        events = run(pts, sz, steps)
        p = AlarmLevelParams()
        total = n * steps
        durs_all = []
        for j in (1, 2, 3, 4):
            on_time, durs = 0.0, []
            for idx, evs in events.items():
                for a, b in intervals(evs, j):
                    b = b if b is not None else float(steps)
                    on_time += b - a
                    durs.append(b - a)
            share = on_time / total
            self.assertTrue(abs(share - p.fpr) / p.fpr < 0.15, (j, share))
            durs_all += durs
        mean = statistics.fmean(durs_all)
        cv = statistics.pstdev(durs_all) / mean
        self.assertTrue(7.0 <= mean <= 9.2, mean)
        self.assertTrue(0.85 <= cv <= 1.15, cv)
        burst_time = sum(e.duration_s for evs in events.values() for e in evs if isinstance(e, MovementEvent))
        burst_share = burst_time / total
        self.assertTrue(abs(burst_share - p.q_burst) / p.q_burst < 0.4, burst_share)
        # a Stoss switches all four channels on at the same step
        for idx, evs in events.items():
            for e in evs:
                if isinstance(e, MovementEvent):
                    ons = {x.j for x in evs if isinstance(x, AlarmEvent) and x.on and x.t == e.t}
                    active_before = set()
                    for j in (1, 2, 3, 4):
                        if any(a <= e.t and (b is None or b > e.t) for a, b in intervals(evs, j)):
                            active_before.add(j)
                    self.assertEqual(ons | active_before, {1, 2, 3, 4}, (idx, e))


class CrisisTest(unittest.TestCase):
    def test_participants_alarm_from_start_to_end_without_delay(self):
        """Without false alarms (fpr = 0): participating channels are ON exactly for the crisis, non-participants never."""
        n = 3
        seqs, sseq = make_seed_sequences(51, n)
        pts = [AlarmLevelPatient(i + 1, seqs[i], AlarmLevelParams(fpr=0.0)) for i in range(n)]
        journal = MemoryJournal()
        sz = AlarmLevelSzenario(pts, sseq, journal, CrisisParams(rate_per_s=1.0 / 300.0, p_cross=0.7))
        events = run(pts, sz, 4 * 3600)
        starts = {r['crisis_id']: r for r in journal.records if r['event'] == 'crisis_start'}
        ends = [r for r in journal.records if r['event'] == 'crisis_end']
        self.assertGreater(len(ends), 25)
        planned = [v['planned_cross'] for r in starts.values() for v in r['sources'].values()]
        self.assertTrue(0.6 <= sum(planned) / len(planned) <= 0.8)
        for r in ends:
            idx = r['patient_index']
            for key, v in r['sources'].items():
                j = v['j']
                ivs = [(a, b) for a, b in intervals(events[idx], j) if a >= r['t_model_start'] - 1 and a <= r['t_model_end'] + 1]
                if starts[r['crisis_id']]['sources'][key]['planned_cross']:
                    self.assertTrue(v['crossed'], (r['crisis_id'], key))
                    self.assertEqual(v['t_cross_rel_s'], 0.0, (r['crisis_id'], key))
                    self.assertEqual(len(ivs), 1, (r['crisis_id'], key, ivs))
                    a, b = ivs[0]
                    self.assertEqual(a, r['t_model_start'])
                    self.assertIsNotNone(b)
                    # journal: t_model_end = first step >= true end; alarm: OFF at the first step > true end
                    self.assertTrue(r['t_model_end'] <= b <= r['t_model_end'] + 1.0, (a, b, r['t_model_end']))
                else:
                    self.assertFalse(v['crossed'], (r['crisis_id'], key))
                    self.assertEqual(ivs, [], (r['crisis_id'], key))
        # the only alarm events are those of the crises
        n_on = sum(1 for evs in events.values() for e in evs if isinstance(e, AlarmEvent) and e.on)
        n_planned = sum(planned)
        self.assertEqual(n_on, n_planned)

    def test_crisis_duration_options(self):
        seqs, sseq = make_seed_sequences(61, 2)
        pts = [AlarmLevelPatient(i + 1, seqs[i], AlarmLevelParams(fpr=0.0)) for i in range(2)]
        journal = MemoryJournal()
        sz = AlarmLevelSzenario(pts, sseq, journal, CrisisParams(rate_per_s=1.0 / 400.0, dur_min_s=30.0, dur_extra_mean_s=30.0))
        run(pts, sz, 8 * 3600)
        durs = [r['duration_s'] for r in journal.records if r['event'] == 'crisis_end']
        self.assertGreater(len(durs), 40)
        self.assertGreaterEqual(min(durs), 30.0)
        self.assertTrue(50.0 <= statistics.fmean(durs) <= 72.0, statistics.fmean(durs))


if __name__ == '__main__':
    unittest.main()
