"""offline_check.py -- parameter check of the patient model WITHOUT network or providers.

Runs N patients and the Szenario for H hours of model time (seconds, not real time) and prints
the alarm load against the "sehr hohe Last" reference of chapter 5:

    ~273 false alarms per source and day, source in false alarm ~2.3 % of the time,
    false alarm ~8 s, ~30 crises per bed and day, crisis ~30 s.

Usage (from anywhere):
    python offline_check.py --patients 20 --hours 12 --seed 1 [--json out.json] [--set burst_rate_per_s=0.004 ...]
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import statistics
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from patient import AlarmEvent, MovementEvent, Patient, PatientParams, SOURCES, make_seed_sequences  # noqa: E402
from szenario import CrisisParams, MemoryJournal, Szenario  # noqa: E402
from alarmebene import AlarmLevelParams, make_patients, make_szenario  # noqa: E402

TARGET = {'alarms_per_source_day': 273.0, 'pct_time_in_alarm': 2.3, 'alarm_duration_s': 8.0,
          'crises_per_bed_day': 30.0, 'crisis_duration_s': 30.0}
CRISIS_TAIL_S = 25.0   # alarms starting inside [crisis start, crisis end + tail] count as crisis alarms


def simulate(n: int, hours: float, seed: int, pparams: PatientParams | None = None,
             cparams: CrisisParams | None = None, progress: bool = False, generator: str = 'prozess', aparams=None):
    steps = int(round(hours * 3600.0))
    seqs, sseq = make_seed_sequences(seed, n)
    patients = make_patients(generator, seqs, pparams, aparams)
    journal = MemoryJournal()
    sz = make_szenario(generator, patients, sseq, journal, cparams, t0_abs=0.0)
    intervals = {pt.index: {s.j: [] for s in SOURCES} for pt in patients}   # (t_on, t_off)
    open_on = {pt.index: {s.j: None for s in SOURCES} for pt in patients}
    movements = {pt.index: [] for pt in patients}
    t_wall = time.time()
    for k in range(steps):
        t = float(k)
        for pt in patients:
            for ev in pt.step(t):
                if isinstance(ev, AlarmEvent):
                    if ev.on:
                        open_on[pt.index][ev.j] = t
                    else:
                        t_on = open_on[pt.index][ev.j]
                        if t_on is not None:
                            intervals[pt.index][ev.j].append((t_on, t))
                            open_on[pt.index][ev.j] = None
                elif isinstance(ev, MovementEvent):
                    movements[pt.index].append((t, ev.duration_s))
        sz.step(t)
        if progress and k % 3600 == 3599:
            print(f'  model hour {(k + 1) // 3600}/{int(hours)} done ({time.time() - t_wall:.1f} s wall)', flush=True)
    t_end = float(steps)
    for pt in patients:
        for j, t_on in open_on[pt.index].items():
            if t_on is not None:
                intervals[pt.index][j].append((t_on, t_end))
    crises = {pt.index: [] for pt in patients}
    for rec in journal.records:
        if rec['event'] == 'crisis_end':
            crises[rec['patient_index']].append(rec)
    return {'patients': patients, 'journal': journal, 'intervals': intervals, 'movements': movements,
            'crises': crises, 'steps': steps, 'wall_s': time.time() - t_wall}


def _pct(xs, q):
    if not xs:
        return float('nan')
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    f = math.floor(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def analyse(sim: dict, n: int, hours: float) -> dict:
    days = hours / 24.0
    total_s = sim['steps']
    per_source = {}
    false_by_patient = {}
    concurrency = {1: 0, 2: 0, 3: 0, 4: 0}
    gaps_all = []
    for idx in sim['intervals']:
        cr_windows = [(c['t_model_start'], c['t_model_end'] + CRISIS_TAIL_S) for c in sim['crises'][idx]]
        false_iv = []   # (t_on, t_off, j)
        for j, ivs in sim['intervals'][idx].items():
            for (a, b) in ivs:
                in_crisis = any(ws <= a <= we for (ws, we) in cr_windows)
                if not in_crisis:
                    false_iv.append((a, b, j))
        false_by_patient[idx] = len(false_iv) / days if days > 0 else float('nan')
        for j in (s.j for s in SOURCES):
            ps = per_source.setdefault(j, {'count': 0, 'time': 0.0, 'durs': []})
            starts = sorted(a for (a, b, jj) in false_iv if jj == j)
            for (a, b, jj) in false_iv:
                if jj == j:
                    ps['count'] += 1
                    ps['time'] += (b - a)
                    ps['durs'].append(b - a)
            gaps_all.extend(b - a for a, b in zip(starts, starts[1:]))
        # clusters of overlapping false alarms of one patient -> how many sources together
        false_iv.sort()
        cur_end, cur_src = None, set()
        for (a, b, j) in false_iv:
            if cur_end is None or a > cur_end:
                if cur_src:
                    concurrency[min(4, len(cur_src))] += 1
                cur_end, cur_src = b, {j}
            else:
                cur_end = max(cur_end, b)
                cur_src.add(j)
        if cur_src:
            concurrency[min(4, len(cur_src))] += 1
    table = {}
    for s in SOURCES:
        ps = per_source[s.j]
        table[s.key] = {
            'alarms_per_source_day': ps['count'] / n / days,
            'pct_time_in_alarm': 100.0 * ps['time'] / (n * total_s),
            'alarm_duration_s': statistics.fmean(ps['durs']) if ps['durs'] else float('nan'),
            'alarm_duration_p50_s': _pct(ps['durs'], 0.5),
            'n': ps['count'],
        }
    cr_all = [c for lst in sim['crises'].values() for c in lst]
    cr_durs = [c['duration_s'] for c in cr_all]
    n_crossed = {k: 0 for k in (0, 1, 2, 3, 4)}
    for c in cr_all:
        n_crossed[sum(1 for v in c['sources'].values() if v['crossed'])] += 1
    planned_cross = sum(1 for c in cr_all for v in c['sources'].values() if v['planned_cross'])
    crossed_planned = sum(1 for c in cr_all for v in c['sources'].values() if v['planned_cross'] and v['crossed'])
    crossed_any = sum(1 for c in cr_all for v in c['sources'].values() if v['crossed'])
    n_src = sum(len(c['sources']) for c in cr_all)
    cross_delays = [v['t_cross_rel_s'] for c in cr_all for v in c['sources'].values() if v['crossed']]
    n_moves = sum(len(v) for v in sim['movements'].values())
    pat_rates = sorted(false_by_patient.values())
    tot_clusters = sum(concurrency.values()) or 1
    all_durs = [d for j in per_source for d in per_source[j]['durs']]
    dur_mean = statistics.fmean(all_durs) if all_durs else float('nan')
    dur_cv = (statistics.pstdev(all_durs) / dur_mean) if len(all_durs) > 1 and dur_mean > 0 else float('nan')
    # time with all 4 sources in false alarm at once / time with at least one source in false alarm (union)
    four_time, union_time = 0.0, 0.0
    for idx in sim['intervals']:
        cr_windows = [(c['t_model_start'], c['t_model_end'] + CRISIS_TAIL_S) for c in sim['crises'][idx]]
        ev = []
        for j, ivs in sim['intervals'][idx].items():
            for (a, b) in ivs:
                if not any(ws <= a <= we for (ws, we) in cr_windows):
                    ev.append((a, 1))
                    ev.append((b, -1))
        ev.sort()
        cnt, last_t = 0, None
        for t, d in ev:
            if last_t is not None:
                if cnt >= 1:
                    union_time += t - last_t
                if cnt >= 4:
                    four_time += t - last_t
            cnt += d
            last_t = t
    all4_time_share = four_time / union_time if union_time > 0 else float('nan')

    return {
        'n_patients': n, 'hours': hours, 'wall_s': sim['wall_s'],
        'sources': table,
        'false_alarm_duration_mean_s': dur_mean, 'false_alarm_duration_cv': dur_cv, 'all4_time_share': all4_time_share,
        'crises': {'per_bed_day': len(cr_all) / n / days, 'duration_s': statistics.fmean(cr_durs) if cr_durs else float('nan'),
                   'n': len(cr_all), 'sources_crossed_frac': crossed_any / n_src if n_src else float('nan'),
                   'planned_cross_hit_frac': crossed_planned / planned_cross if planned_cross else float('nan'),
                   'cross_delay_p50_s': _pct(cross_delays, 0.5), 'cross_delay_p90_s': _pct(cross_delays, 0.9),
                   'cross_delay_mean_s': statistics.fmean(cross_delays) if cross_delays else float('nan'),
                   'by_n_crossed': n_crossed,
                   'by_n_crossed_pct': {k: (100.0 * v / len(cr_all) if cr_all else float('nan')) for k, v in n_crossed.items()}},
        'movements_per_patient_day': n_moves / n / days,
        'concurrency': {k: v for k, v in concurrency.items()},
        'concurrency_pct': {k: 100.0 * v / tot_clusters for k, v in concurrency.items()},
        'per_patient_false_alarms_day': {'min': pat_rates[0], 'p25': _pct(pat_rates, 0.25), 'median': _pct(pat_rates, 0.5),
                                         'p75': _pct(pat_rates, 0.75), 'max': pat_rates[-1]},
        'inter_alarm_gap_s': {'p10': _pct(gaps_all, 0.1), 'p25': _pct(gaps_all, 0.25), 'p50': _pct(gaps_all, 0.5),
                              'p75': _pct(gaps_all, 0.75), 'p90': _pct(gaps_all, 0.9),
                              'mean': statistics.fmean(gaps_all) if gaps_all else float('nan'),
                              'share_below_30s_pct': 100.0 * sum(1 for g in gaps_all if g < 30.0) / len(gaps_all) if gaps_all else float('nan')},
    }


def print_report(r: dict) -> None:
    print(f"offline check: {r['n_patients']} patients x {r['hours']:g} h model time, {r['wall_s']:.1f} s wall, generator {r.get('generator', 'prozess')}")
    print()
    print(f"{'source':8s} {'alarms/src/day':>16s} {'% time in alarm':>16s} {'mean dur [s]':>13s} {'p50 dur [s]':>12s} {'n':>6s}")
    print(f"{'target':8s} {TARGET['alarms_per_source_day']:16.0f} {TARGET['pct_time_in_alarm']:16.1f} {TARGET['alarm_duration_s']:13.1f} {'':>12s}")
    for key, v in r['sources'].items():
        print(f"{key:8s} {v['alarms_per_source_day']:16.1f} {v['pct_time_in_alarm']:16.2f} {v['alarm_duration_s']:13.1f} {v['alarm_duration_p50_s']:12.1f} {v['n']:6d}")
    c = r['crises']
    print()
    print(f"crises: {c['per_bed_day']:.1f} per bed and day (target {TARGET['crises_per_bed_day']:.0f}), "
          f"mean duration {c['duration_s']:.1f} s (target {TARGET['crisis_duration_s']:.0f}), n={c['n']}")
    print(f"        sources crossed: {100 * c['sources_crossed_frac']:.0f} % of all, {100 * c['planned_cross_hit_frac']:.0f} % of the planned ones; "
          f"crossing delay mean {c['cross_delay_mean_s']:.1f} s, p50 {c['cross_delay_p50_s']:.1f} s, p90 {c['cross_delay_p90_s']:.1f} s")
    print(f"false alarm duration: mean {r['false_alarm_duration_mean_s']:.2f} s, coefficient of variation {r['false_alarm_duration_cv']:.2f}; "
          f"time with all 4 sources at once: {100 * r['all4_time_share']:.1f} % of the false-alarm time")
    bn, bp = c['by_n_crossed'], c['by_n_crossed_pct']
    print('        crises by number of sources that crossed:  ' + '  '.join(f'{k}: {bn[k]} ({bp[k]:.0f} %)' for k in (4, 3, 2, 1, 0)))
    print(f"movements: {r['movements_per_patient_day']:.1f} per patient and day")
    print()
    cc, cp = r['concurrency'], r['concurrency_pct']
    print('false episodes by number of sources together:  ' + '  '.join(f'{k}: {cc[k]} ({cp[k]:.0f} %)' for k in (1, 2, 3, 4)))
    pp = r['per_patient_false_alarms_day']
    print(f"false alarms per patient and day (all sources): min {pp['min']:.0f}  p25 {pp['p25']:.0f}  median {pp['median']:.0f}  "
          f"p75 {pp['p75']:.0f}  max {pp['max']:.0f}")
    g = r['inter_alarm_gap_s']
    print(f"interval between false alarms of one source [s]: p10 {g['p10']:.0f}  p25 {g['p25']:.0f}  p50 {g['p50']:.0f}  "
          f"p75 {g['p75']:.0f}  p90 {g['p90']:.0f}  mean {g['mean']:.0f};  {g['share_below_30s_pct']:.1f} % shorter than 30 s")


def apply_overrides(params: PatientParams, items: list[str]) -> PatientParams:
    for it in items or []:
        key, _, val = it.partition('=')
        if not hasattr(params, key):
            raise SystemExit(f'unknown PatientParams field: {key}')
        cur = getattr(params, key)
        if isinstance(cur, tuple):
            setattr(params, key, tuple(float(x) for x in val.split(',')))
        else:
            setattr(params, key, type(cur)(float(val)) if isinstance(cur, (int, float)) else val)
    return params


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--patients', type=int, default=20)
    ap.add_argument('--hours', type=float, default=12.0)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--set', action='append', default=[], metavar='KEY=VALUE', help='override a PatientParams field')
    ap.add_argument('--p-cross', type=float, default=CrisisParams().p_cross,
                    help='probability that a source is pushed beyond its threshold in a crisis (default %(default)s)')
    ap.add_argument('--crisis-min', type=float, default=CrisisParams().dur_min_s, help='minimum crisis duration [s]')
    ap.add_argument('--crisis-extra-mean', type=float, default=CrisisParams().dur_extra_mean_s, help='mean of the exponential part [s]')
    ap.add_argument('--threshold-scale', type=float, default=PatientParams().threshold_scale,
                    help='prozess: scale h of the alarm thresholds of all four sources, alarm at x >= h (default %(default)s)')
    ap.add_argument('--generator', choices=('prozess', 'alarmebene'), default='prozess')
    ap.add_argument('--fpr', type=float, default=AlarmLevelParams().fpr)
    ap.add_argument('--c-art', type=float, default=AlarmLevelParams().c_art)
    ap.add_argument('--d-false', type=float, default=AlarmLevelParams().d_false_s)
    ap.add_argument('--json', type=pathlib.Path, help='write the statistics to this file')
    ap.add_argument('--quiet-progress', action='store_true')
    a = ap.parse_args(argv)
    pparams = apply_overrides(PatientParams(threshold_scale=a.threshold_scale), a.set)
    cparams = CrisisParams(p_cross=a.p_cross, dur_min_s=a.crisis_min, dur_extra_mean_s=a.crisis_extra_mean)
    aparams = AlarmLevelParams(fpr=a.fpr, c_art=a.c_art, d_false_s=a.d_false)
    sim = simulate(a.patients, a.hours, a.seed, pparams, cparams, progress=not a.quiet_progress, generator=a.generator, aparams=aparams)
    r = analyse(sim, a.patients, a.hours)
    r['generator'] = a.generator
    r['patient_params'] = pparams.to_dict() if a.generator == 'prozess' else None
    r['alarmlevel_params'] = aparams.to_dict() if a.generator == 'alarmebene' else None
    r['crisis_params'] = cparams.to_dict()
    r['seed'] = a.seed
    print_report(r)
    if a.json:
        a.json.write_text(json.dumps(r, indent=2, default=str), encoding='utf-8')
        print(f'\nstatistics written to {a.json}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
