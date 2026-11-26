"""ziel3_roc.py -- ROC metrics of the threshold sweep (logs/<dir>/h_*/): single devices and k-aus-4 fusion.

Definitions (model time of the offline journals, read-only):
* crisis = [start, end] of journal A; the margins [start-10 s, start) and (end, end+10 s] count neither as crisis
  nor as stable; stable = everything else inside the measuring window run_start .. run_end;
* device j: TPR_j = alarm time of j inside crises / total crisis time, FPR_j = alarm time inside stable time /
  total stable time, eta_j = share of (ended) crises during which j alarmed at least once;
* k-aus-4: escalation as soon as >= k sources of the patient are active, held until all are off (latch of Rot);
  TPR / FPR by time as above, caught = escalation meets [start, end+10 s], false escalation = meets no crisis
  +-10 s (per bed*h);
* AUC by trapezoids over the h grid, points (0,0) and (1,1) added;
* "all 4 at once" = share of the stable time in which all four sources alarm together (for c_art);
* variant (ii) of the x axis: FPR_ii = total duration of the false episodes (alarms / escalations that meet no
  crisis +-10 s) / stable time, i.e. whole episodes instead of time slices (columns fpr_ii, auc_ii);
* caught strict = the escalation that meets [start, end+10 s] begins before the crisis end (column erkannt_streng).

    python ziel3_roc.py [--dir ziel3_roc]  ->  logs/<dir>/roc_kausn.csv, logs/<dir>/roc_summary.json
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import Alarm, Crisis, SOURCES, CRISIS_MARGIN_S, CRISIS_CATCH_TAIL_S, k_of_n, by_patient, intersects  # noqa: E402

LOGS = HERE.parent / 'logs'
KEY = {'ecg': 'ECG', 'vent': 'Vent', 'spo2': 'SpO2', 'abp': 'ABP'}
PRIO = {'ECG': 'Hi', 'Vent': 'Hi', 'SpO2': 'Me', 'ABP': 'Lo'}
RULES = (1, 2, 3, 4)
FPR_LEVELS = (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.3)


def read_jsonl(path: pathlib.Path):
    with path.open(encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_h(folder: pathlib.Path) -> dict:
    cfg = json.loads((folder / 'run_config.json').read_text(encoding='utf-8'))
    w0 = w1 = None
    open_on, alarms = {}, []
    for r in read_jsonl(folder / 'alarms.jsonl'):
        ev = r['event']
        if ev == 'run_start':
            w0 = r['t_start_abs']
        elif ev == 'run_end':
            w1 = r['t_end_abs']
        elif ev == 'alarm':
            t = r.get('t_prov_abs') or r['t_abs']
            key = (r['patient'], KEY[r['source']])
            if r['on']:
                open_on[key] = t
            else:
                t_on = open_on.pop(key, None)
                if t_on is not None:
                    alarms.append(Alarm(key[0], key[1], PRIO[key[1]], t_on, t, r.get('value')))
    for key, t_on in open_on.items():
        alarms.append(Alarm(key[0], key[1], PRIO[key[1]], t_on, None, None))
    starts, ends, n_moves = {}, {}, 0
    for r in read_jsonl(folder / 'truth.jsonl'):
        if r['event'] == 'crisis_start':
            starts[r['crisis_id']] = r
        elif r['event'] == 'crisis_end':
            ends[r['crisis_id']] = r
        elif r['event'] == 'movement':
            n_moves += 1
    crises = []
    for cid, s in starts.items():
        e = ends.get(cid)
        cross = {KEY[k]: (v.get('t_cross_abs') if e else None) for k, v in (e['sources'] if e else s['sources']).items()}
        crises.append(Crisis(s['patient'], cid, s['t_start_abs'], e['t_end_abs'] if e else None, cross))
    return {'h': float(cfg['threshold_scale']), 'config': cfg, 'w0': w0, 'w1': w1, 'alarms': alarms, 'crises': crises,
            'n_moves': n_moves, 'patients': cfg['patients'], 'hours': cfg['hours']}


# ---- interval arithmetic -------------------------------------------------------------------------------------

def clip(ivs, w0, w1):
    out = []
    for a, b in ivs:
        a2, b2 = max(a, w0), min(b, w1)
        if b2 > a2:
            out.append((a2, b2))
    return out


def union(ivs):
    ivs = sorted(ivs)
    out = []
    for a, b in ivs:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def overlap(ivs_a, ivs_b) -> float:
    """Total overlap of two interval lists (each list internally non-overlapping)."""
    total = 0.0
    for a0, a1 in ivs_a:
        for b0, b1 in ivs_b:
            lo, hi = max(a0, b0), min(a1, b1)
            if hi > lo:
                total += hi - lo
    return total


def length(ivs) -> float:
    return sum(b - a for a, b in ivs)


def all4_intervals(by_source: dict) -> list:
    ev = []
    for ivs in by_source.values():
        for a, b in ivs:
            ev.append((a, 1))
            ev.append((b, -1))
    ev.sort()
    active, out, last = 0, [], None
    for t, d in ev:
        if active == 4 and last is not None and t > last:
            out.append((last, t))
        active += d
        last = t
    return union(out)


class PatientTimes:
    """Crisis / margin / stable intervals of one patient inside the window."""

    def __init__(self, crises: list, w0: float, w1: float):
        self.w0, self.w1 = w0, w1
        self.crisis = clip([(c.t_start, c.t_end if c.t_end is not None else w1) for c in crises], w0, w1)
        self.margin = union(clip([(c.t_start - CRISIS_MARGIN_S, (c.t_end if c.t_end is not None else w1) + CRISIS_MARGIN_S)
                                  for c in crises], w0, w1))
        self.crisis_total = length(self.crisis)
        self.margin_total = length(self.margin)
        self.stable_total = (w1 - w0) - self.margin_total
        self.ended = [c for c in crises if c.t_end is not None and c.t_end <= w1]

    def split(self, ivs) -> tuple[float, float]:
        """(time inside crises, time inside stable) of an interval list."""
        ivs = clip(ivs, self.w0, self.w1)
        in_crisis = overlap(ivs, self.crisis)
        in_margin = overlap(ivs, self.margin)
        return in_crisis, length(ivs) - in_margin


def metrics_for_h(data: dict) -> dict:
    w0, w1 = data['w0'], data['w1']
    n_pat, hours = data['patients'], data['hours']
    bed_h = n_pat * hours
    days = hours / 24.0
    cr_p = by_patient(data['crises'])
    al_p = by_patient(data['alarms'])
    patients = sorted(set(cr_p) | set(al_p) | {c.patient for c in data['crises']})
    times = {p: PatientTimes(cr_p.get(p, []), w0, w1) for p in patients}
    crisis_total = sum(t.crisis_total for t in times.values())
    stable_total = sum(t.stable_total for t in times.values())
    n_crises = sum(len(t.ended) for t in times.values())

    dev = {s: {'in_crisis': 0.0, 'in_stable': 0.0, 'n_on': 0, 'n_false': 0, 'false_time': 0.0, 'eta_hits': 0} for s in SOURCES}
    all4_stable = 0.0
    esc_by_rule = {k: k_of_n(data['alarms'], k, stream_end=w1) for k in RULES}
    rule = {k: {'in_crisis': 0.0, 'in_stable': 0.0, 'n_esc': 0, 'n_false': 0, 'false_time': 0.0, 'caught': 0, 'caught_strict': 0}
            for k in RULES}

    def false_episodes(ivs, t):
        """episodes starting in the window that meet no crisis +-10 s: (count, total duration inside the window)"""
        sel = [(a, b) for a, b in ivs if w0 <= a <= w1 and not any(intersects(a, b, m0, m1) for m0, m1 in t.margin)]
        return len(sel), length(clip(sel, w0, w1))

    for p in patients:
        t = times[p]
        by_source = {}
        for a in al_p.get(p, []):
            by_source.setdefault(a.source, []).append((a.t_on, a.t_off if a.t_off is not None else w1))
        for s in SOURCES:
            ivs = by_source.get(s, [])
            c, st = t.split(ivs)
            d = dev[s]
            d['in_crisis'] += c
            d['in_stable'] += st
            d['n_on'] += sum(1 for a, b in ivs if w0 <= a <= w1)
            n_f, t_f = false_episodes(ivs, t)
            d['n_false'] += n_f
            d['false_time'] += t_f
            d['eta_hits'] += sum(1 for c_ in t.ended if any(intersects(a, b, c_.t_start, c_.t_end) for a, b in ivs))
        if len(by_source) == 4:
            _, st4 = t.split(all4_intervals(by_source))
            all4_stable += st4
        for k in RULES:
            ivs = [(e.t_start, e.t_end) for e in esc_by_rule[k] if e.patient == p]
            c, st = t.split(ivs)
            r = rule[k]
            r['in_crisis'] += c
            r['in_stable'] += st
            r['n_esc'] += sum(1 for a, b in ivs if w0 <= a <= w1)
            n_f, t_f = false_episodes(ivs, t)
            r['n_false'] += n_f
            r['false_time'] += t_f
            r['caught'] += sum(1 for c_ in t.ended if any(intersects(a, b, c_.t_start, c_.t_end + CRISIS_CATCH_TAIL_S) for a, b in ivs))
            r['caught_strict'] += sum(1 for c_ in t.ended
                                      if any(intersects(a, b, c_.t_start, c_.t_end + CRISIS_CATCH_TAIL_S) and a <= c_.t_end for a, b in ivs))

    out = {'h': data['h'], 'bed_h': bed_h, 'n_crises': n_crises, 'crisis_total_s': crisis_total, 'stable_total_s': stable_total,
           'all4_share_stable': all4_stable / stable_total if stable_total else float('nan'),
           'thresholds': {s['key']: [s.get('on_h', s['on']), s.get('off_h', s['off'])] for s in data['config']['sources']},
           'devices': {}, 'rules': {}}
    for s in SOURCES:
        d = dev[s]
        out['devices'][s] = {'tpr': d['in_crisis'] / crisis_total if crisis_total else float('nan'),
                             'fpr': d['in_stable'] / stable_total if stable_total else float('nan'),
                             'fpr_ii': d['false_time'] / stable_total if stable_total else float('nan'),
                             'eta': d['eta_hits'] / n_crises if n_crises else float('nan'),
                             'n_alarms': d['n_on'], 'n_false': d['n_false'], 'false_time_s': d['false_time'],
                             'alarms_per_bed_day': d['n_on'] / n_pat / days, 'false_per_bed_day': d['n_false'] / n_pat / days,
                             'false_per_bed_h': d['n_false'] / bed_h}
    for k in RULES:
        r = rule[k]
        out['rules'][f'{k}-aus-4'] = {'tpr': r['in_crisis'] / crisis_total if crisis_total else float('nan'),
                                      'fpr': r['in_stable'] / stable_total if stable_total else float('nan'),
                                      'fpr_ii': r['false_time'] / stable_total if stable_total else float('nan'),
                                      'caught': r['caught'] / n_crises if n_crises else float('nan'), 'n_caught': r['caught'],
                                      'caught_strict': r['caught_strict'] / n_crises if n_crises else float('nan'),
                                      'n_caught_strict': r['caught_strict'],
                                      'n_esc': r['n_esc'], 'n_false': r['n_false'], 'false_time_s': r['false_time'],
                                      'false_per_bed_h': r['n_false'] / bed_h, 'esc_per_bed_day': r['n_esc'] / n_pat / days}
    return out


def auc(points) -> float:
    pts = sorted(set(points) | {(0.0, 0.0), (1.0, 1.0)})
    x = np.array([p[0] for p in pts])
    y = np.array([p[1] for p in pts])
    return float(np.trapezoid(y, x)) if hasattr(np, 'trapezoid') else float(np.trapz(y, x))


def tpr_at(points, fpr: float):
    pts = sorted(set(points) | {(0.0, 0.0), (1.0, 1.0)})
    x = np.array([p[0] for p in pts])
    y = np.array([p[1] for p in pts])
    return float(np.interp(fpr, x, y))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dir', default='ziel3_roc', help='sweep folder under logs/ (default %(default)s)')
    a = ap.parse_args(argv)
    root = LOGS / a.dir
    folders = sorted(p for p in root.glob('h_*') if (p / 'alarms.jsonl').exists())
    if not folders:
        raise SystemExit(f'no h_* folders in {root}')
    per_h = []
    for f in folders:
        data = load_h(f)
        m = metrics_for_h(data)
        m['folder'] = f.name
        m['n_alarms'] = len(data['alarms'])
        m['n_moves'] = data['n_moves']
        per_h.append(m)
        print(f"h = {m['h']:.2f}: alarms {m['n_alarms']:6d}, crises {m['n_crises']}, "
              + '  '.join(f"{s} TPR {m['devices'][s]['tpr']:.3f} FPR {m['devices'][s]['fpr']:.4f}" for s in SOURCES)
              + '  |  ' + '  '.join(f"{k}: TPR {v['tpr']:.3f} FPR {v['fpr']:.4f} falsch/Bett*h {v['false_per_bed_h']:.2f}"
                                     for k, v in m['rules'].items()), flush=True)
    per_h.sort(key=lambda m: m['h'])

    curves, curves_ii = {}, {}
    for s in SOURCES:
        curves[s] = [(m['devices'][s]['fpr'], m['devices'][s]['tpr']) for m in per_h]
        curves_ii[s] = [(m['devices'][s]['fpr_ii'], m['devices'][s]['tpr']) for m in per_h]
    for k in RULES:
        curves[f'{k}-aus-4'] = [(m['rules'][f'{k}-aus-4']['fpr'], m['rules'][f'{k}-aus-4']['tpr']) for m in per_h]
        curves_ii[f'{k}-aus-4'] = [(m['rules'][f'{k}-aus-4']['fpr_ii'], m['rules'][f'{k}-aus-4']['tpr']) for m in per_h]
    aucs = {name: auc(pts) for name, pts in curves.items()}
    aucs_ii = {name: auc(pts) for name, pts in curves_ii.items()}

    rows = []
    for m in per_h:
        for s in SOURCES:
            d = m['devices'][s]
            rows.append({'h': m['h'], 'art': 'Geraet', 'name': s, 'on_schwelle': m['thresholds'][[k for k, v in KEY.items() if v == s][0]][0],
                         'tpr': d['tpr'], 'fpr': d['fpr'], 'eta_oder_erkannt': d['eta'], 'n_krisen': m['n_crises'],
                         'n_ereignisse': d['n_alarms'], 'n_falsch': d['n_false'], 'falsch_je_bett_h': d['false_per_bed_h'],
                         'ereignisse_je_bett_tag': d['alarms_per_bed_day'], 'alle4_anteil_stabil': m['all4_share_stable'],
                         'auc': aucs[s], 'fpr_ii': d['fpr_ii'], 'auc_ii': aucs_ii[s], 'erkannt_streng': '',
                         'n_erkannt_weich': '', 'n_erkannt_streng': ''})
        for k in RULES:
            name = f'{k}-aus-4'
            r = m['rules'][name]
            rows.append({'h': m['h'], 'art': 'Regel', 'name': name, 'on_schwelle': '',
                         'tpr': r['tpr'], 'fpr': r['fpr'], 'eta_oder_erkannt': r['caught'], 'n_krisen': m['n_crises'],
                         'n_ereignisse': r['n_esc'], 'n_falsch': r['n_false'], 'falsch_je_bett_h': r['false_per_bed_h'],
                         'ereignisse_je_bett_tag': r['esc_per_bed_day'], 'alle4_anteil_stabil': m['all4_share_stable'],
                         'auc': aucs[name], 'fpr_ii': r['fpr_ii'], 'auc_ii': aucs_ii[name], 'erkannt_streng': r['caught_strict'],
                         'n_erkannt_weich': r['n_caught'], 'n_erkannt_streng': r['n_caught_strict']})
    csv_path = root / 'roc_kausn.csv'
    with csv_path.open('w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), delimiter=';')
        w.writeheader()
        for r in rows:
            w.writerow({k: (f'{v:.6g}' if isinstance(v, float) else v) for k, v in r.items()})

    # ranking of the rules at fixed FPR levels (interpolated along the h grid)
    ranking, ranking_ii = {}, {}
    for lvl in FPR_LEVELS:
        vals = {f'{k}-aus-4': tpr_at(curves[f'{k}-aus-4'], lvl) for k in RULES}
        ranking[str(lvl)] = {'tpr': vals, 'order': sorted(vals, key=lambda n: -vals[n])}
        vals2 = {f'{k}-aus-4': tpr_at(curves_ii[f'{k}-aus-4'], lvl) for k in RULES}
        ranking_ii[str(lvl)] = {'tpr': vals2, 'order': sorted(vals2, key=lambda n: -vals2[n])}
    summary = {'dir': a.dir, 'grid': [m['h'] for m in per_h], 'auc': aucs, 'ranking_at_fpr': ranking,
               'auc_ii': aucs_ii, 'ranking_at_fpr_ii': ranking_ii,
               'per_h': per_h, 'definitions': __doc__}
    (root / 'roc_summary.json').write_text(json.dumps(summary, indent=2, default=float), encoding='utf-8')

    print('\nAUC (trapezoids, with (0,0) and (1,1)): ' + '  '.join(f'{n} {v:.4f}' for n, v in aucs.items()))
    print('AUC variant (ii), x = false episodes only: ' + '  '.join(f'{n} {v:.4f}' for n, v in aucs_ii.items()))
    print('rule ranking at fixed FPR (interpolated TPR):')
    for lvl, r in ranking.items():
        print(f'  FPR {float(lvl):.3f}: ' + ' > '.join(f"{n} ({r['tpr'][n]:.3f})" for n in r['order']))
    print(f'written: {csv_path} ({len(rows)} rows), {root / "roc_summary.json"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
