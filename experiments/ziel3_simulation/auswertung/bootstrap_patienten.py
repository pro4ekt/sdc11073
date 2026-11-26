"""bootstrap_patienten.py -- patient bootstrap of the Ziel 3 key figures (read-only on the logs and results).

For every run the escalation rules are rebuilt exactly as in ziel3_analyze.py (same window, clock mapping,
latch; adaptiv = Rot of the Dashboard) and counted per patient: false escalations and caught crises (weich:
escalation intersects [begin, end + 10 s]) of adaptiv, 2-aus-4, 3-aus-4; the totals are checked against
results_<name>.json. Bootstrap: the 20 patients are drawn with replacement, 10 000 repetitions (numpy
Generator, seed 1): false escalations per bed*h, share of caught crises, and the paired differences
adaptiv - 2-aus-4 and adaptiv - 3-aus-4 on the same drawn patients. Percentile intervals (2.5 / 97.5 %) are
compared with the Poisson / Clopper-Pearson intervals of results_<name>.json.

    python bootstrap_patienten.py [--runs ziel3_12h,...] [--reps 10000] [--seed 1]
      -> bootstrap_patienten.md, bootstrap_patienten.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import (CRISIS_MARGIN_S, LOGS, Alarm, ClockMap, Crisis, by_patient, evaluate_rule, find_pc1_dir,  # noqa: E402
                    intersects, k_of_n, load_pc1, load_pc2)

HERE = pathlib.Path(__file__).resolve().parent
RUNS = [('ziel3_12h', '12 h'), ('ziel3_6h_p06', '6 h p06'), ('ziel3_4h_p07', 'A'), ('ziel3_4h_p08', 'B'),
        ('ziel3_4h_lang', 'C'), ('ziel3_6h_ideal', 'D')]
RULES = ('adaptiv', '2-aus-4', '3-aus-4')
DIFFS = (('adaptiv', '2-aus-4'), ('adaptiv', '3-aus-4'))


def per_patient_counts(name: str) -> dict:
    """Per patient: false escalations and caught crises per rule, crises; same pipeline as ziel3_analyze.py."""
    run_dir = LOGS / name / 'pc2'
    pc2 = load_pc2(run_dir)
    pc1 = load_pc1(run_dir)
    if pc1 is None and find_pc1_dir(name) is not None:
        pc1 = load_pc1(run_dir, find_pc1_dir(name))
    if pc1 is None:
        raise SystemExit(f'{name}: no PC 1 digest, adaptiv not available')
    cm = ClockMap(pc1['clock'])
    accepted = [float(d['t_accepted']) for d in pc1['devices'] if d.get('t_accepted')]
    w0 = max(accepted) + 5.0
    w1 = float(pc1['summary']['run']['t_end_abs']) - 2.0
    hours = (w1 - w0) / 3600.0
    alarms = [Alarm(x.patient, x.source, x.prio, cm.to_pc1(x.t_on), cm.to_pc1(x.t_off), x.value) for x in pc2['alarms']]
    crises_all = [Crisis(c.patient, c.cid, cm.to_pc1(c.t_start), cm.to_pc1(c.t_end),
                         {s: cm.to_pc1(t) for s, t in c.cross.items()}) for c in pc2['crises']]
    crises = [c for c in crises_all if c.t_end is not None and w0 <= c.t_start <= w1]
    rules = {'2-aus-4': k_of_n(alarms, 2, stream_end=w1), '3-aus-4': k_of_n(alarms, 3, stream_end=w1),
             'adaptiv': sorted(pc1['rot'], key=lambda e: (e.t_start, e.patient))}
    patients = sorted({a.patient for a in alarms} | {c.patient for c in crises_all})
    idx = {p: i for i, p in enumerate(patients)}
    cr_p = by_patient(crises_all)
    out = {'name': name, 'patients': patients, 'hours': hours, 'exposure_bed_h': len(patients) * hours,
           'n_crises': np.zeros(len(patients), dtype=int), 'false': {}, 'caught': {}, 'totals': {}}
    for c in crises:
        out['n_crises'][idx[c.patient]] += 1
    for rule, escs in rules.items():
        r = evaluate_rule(escs, crises, crises_all, (w0, w1))
        f = np.zeros(len(patients), dtype=int)
        for e in escs:
            if not (w0 <= e.t_start <= w1):
                continue
            if not any(intersects(e.t_start, e.t_end, c.t_start - CRISIS_MARGIN_S, (c.t_end if c.t_end is not None else w1) + CRISIS_MARGIN_S)
                       for c in cr_p.get(e.patient, [])):
                f[idx[e.patient]] += 1
        g = np.zeros(len(patients), dtype=int)
        for p in r['per_crisis']:
            if p['caught']:
                g[idx[p['patient']]] += 1
        assert int(f.sum()) == r['n_false'] and int(g.sum()) == r['caught'], (name, rule, f.sum(), r['n_false'], g.sum(), r['caught'])
        out['false'][rule], out['caught'][rule] = f, g
        out['totals'][rule] = {'n_false': r['n_false'], 'caught': r['caught'], 'n_crises': r['n_crises']}
    return out


def bootstrap(d: dict, reps: int, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    P = len(d['patients'])
    draw = rng.integers(0, P, size=(reps, P))
    n_cr = d['n_crises'][draw].sum(axis=1)
    fr, cf = {}, {}
    for rule in RULES:
        fr[rule] = d['false'][rule][draw].sum(axis=1) / (P * d['hours'])
        cg = d['caught'][rule][draw].sum(axis=1)
        cf[rule] = np.where(n_cr > 0, cg / np.maximum(n_cr, 1), np.nan)
    res = {'reps': reps, 'seed': seed, 'n_draws_without_crises': int((n_cr == 0).sum()), 'stats': {}, 'diffs': {}}
    for rule in RULES:
        res['stats'][rule] = {
            'false_per_bed_h': {'point': float(d['false'][rule].sum() / d['exposure_bed_h']),
                                'ci': [float(np.percentile(fr[rule], 2.5)), float(np.percentile(fr[rule], 97.5))],
                                'per_patient': (d['false'][rule] / d['hours']).tolist()},
            'caught_frac': {'point': float(d['caught'][rule].sum() / d['n_crises'].sum()),
                            'ci': [float(np.nanpercentile(cf[rule], 2.5)), float(np.nanpercentile(cf[rule], 97.5))],
                            'per_patient': [(int(c), int(n)) for c, n in zip(d['caught'][rule], d['n_crises'])]}}
    for a, b in DIFFS:
        key = f'{a} - {b}'
        df = fr[a] - fr[b]
        dc = cf[a] - cf[b]
        res['diffs'][key] = {
            'false_per_bed_h': {'point': float((d['false'][a].sum() - d['false'][b].sum()) / d['exposure_bed_h']),
                                'ci': [float(np.percentile(df, 2.5)), float(np.percentile(df, 97.5))],
                                'share_negative': float((df < 0).mean()), 'share_positive': float((df > 0).mean())},
            'caught_frac': {'point': float((d['caught'][a].sum() - d['caught'][b].sum()) / d['n_crises'].sum()),
                            'ci': [float(np.nanpercentile(dc, 2.5)), float(np.nanpercentile(dc, 97.5))],
                            'share_negative': float(np.nanmean(dc < 0)), 'share_positive': float(np.nanmean(dc > 0))}}
    return res


def fmt(x, nd=2):
    return f'{x:.{nd}f}'


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--runs', default=','.join(n for n, _ in RUNS))
    ap.add_argument('--reps', type=int, default=10000)
    ap.add_argument('--seed', type=int, default=1)
    a = ap.parse_args(argv)
    wanted = a.runs.split(',')
    rows_csv = []
    L = ['# Bootstrap über Patienten (Ziel 3)', '',
         f'20 Patienten mit Zurücklegen gezogen, {a.reps} Wiederholungen (numpy Generator, Seed {a.seed}); Perzentilintervalle 2,5 / 97,5 %. '
         'Je Patient gezählt: falsche Eskalationen (keine Krise im Bereich [Beginn − 10 s, Ende + 10 s]) und erkannte Krisen weich '
         '(Eskalation schneidet [Beginn, Ende + 10 s]); Regeln und Fenster wie in ziel3_analyze.py (Zeitbasis PC 1), Summen gegen results_<Lauf>.json geprüft. '
         'Rate = Summe der gezogenen Patienten / (20 × Fensterstunden); Anteil = erkannte / Krisen der gezogenen Patienten. '
         'Differenzen gepaart: adaptiv − 2-aus-4 und adaptiv − 3-aus-4 auf denselben gezogenen Patienten. '
         '„klassisch“ = Poisson (Rate) bzw. Clopper-Pearson (Anteil) aus results_<Lauf>.json; Breite = obere − untere Grenze.', '']
    summary = []
    for name, short in RUNS:
        if name not in wanted:
            continue
        res_json = json.loads((HERE / f'results_{name}.json').read_text(encoding='utf-8'))
        d = per_patient_counts(name)
        b = bootstrap(d, a.reps, a.seed)
        eta = json.loads((LOGS / name / 'pc2' / 'run_config.json').read_text(encoding='utf-8'))['crisis_params']['p_cross']
        L += [f"## {short} ({name}, η = {eta:g}, {d['exposure_bed_h']:.1f} Bett·h, {int(d['n_crises'].sum())} Krisen)", '',
              '| Größe | Regel | Punkt | Bootstrap 95 % | Breite | klassisch 95 % | Breite | Verhältnis Breite Bootstrap / klassisch | je Patient min … max |',
              '|---|---|---|---|---|---|---|---|---|']
        for rule in RULES:
            rj = res_json['rules'][rule]
            s = b['stats'][rule]['false_per_bed_h']
            cl = rj['false_per_bed_h_ci']
            wb, wc = s['ci'][1] - s['ci'][0], cl[1] - cl[0]
            pp = s['per_patient']
            L.append(f"| falsche Eskalationen je Bett·h | {rule} | {fmt(s['point'])} | [{fmt(s['ci'][0])}; {fmt(s['ci'][1])}] | {fmt(wb)} | "
                     f"[{fmt(cl[0])}; {fmt(cl[1])}] | {fmt(wc)} | {fmt(wb / wc)} | {min(pp):.2f} … {max(pp):.2f} |")
            rows_csv.append([name, short, eta, 'falsch_je_bett_h', rule, s['point'], s['ci'][0], s['ci'][1], wb, cl[0], cl[1], wc, wb / wc, '', '', ''])
            s = b['stats'][rule]['caught_frac']
            cl = rj['caught_frac_ci']
            wb, wc = s['ci'][1] - s['ci'][0], cl[1] - cl[0]
            pp = [c / n for c, n in s['per_patient'] if n > 0]
            L.append(f"| erkannte Krisen weich (Anteil) | {rule} | {100 * s['point']:.1f} % | [{100 * s['ci'][0]:.1f}; {100 * s['ci'][1]:.1f}] | {100 * wb:.1f} | "
                     f"[{100 * cl[0]:.1f}; {100 * cl[1]:.1f}] | {100 * wc:.1f} | {fmt(wb / wc)} | {100 * min(pp):.0f} … {100 * max(pp):.0f} % |")
            rows_csv.append([name, short, eta, 'erkannt_weich_anteil', rule, s['point'], s['ci'][0], s['ci'][1], wb, cl[0], cl[1], wc, wb / wc, '', '', ''])
        L += ['', '| Differenz (gepaart) | Größe | Punkt | Bootstrap 95 % | 0 ausgeschlossen | Anteil Wiederholungen < 0 / > 0 |', '|---|---|---|---|---|---|']
        for key, dd in b['diffs'].items():
            s = dd['false_per_bed_h']
            excl = s['ci'][1] < 0 or s['ci'][0] > 0
            L.append(f"| {key} | falsche Eskalationen je Bett·h | {s['point']:+.2f} | [{s['ci'][0]:+.2f}; {s['ci'][1]:+.2f}] | {'ja' if excl else 'nein'} | "
                     f"{100 * s['share_negative']:.1f} % / {100 * s['share_positive']:.1f} % |")
            rows_csv.append([name, short, eta, 'differenz_falsch_je_bett_h', key, s['point'], s['ci'][0], s['ci'][1], s['ci'][1] - s['ci'][0], '', '', '', '',
                             'ja' if excl else 'nein', s['share_negative'], s['share_positive']])
            s = dd['caught_frac']
            excl = s['ci'][1] < 0 or s['ci'][0] > 0
            L.append(f"| {key} | erkannte Krisen weich (Prozentpunkte) | {100 * s['point']:+.1f} | [{100 * s['ci'][0]:+.1f}; {100 * s['ci'][1]:+.1f}] | {'ja' if excl else 'nein'} | "
                     f"{100 * s['share_negative']:.1f} % / {100 * s['share_positive']:.1f} % |")
            rows_csv.append([name, short, eta, 'differenz_erkannt_weich', key, s['point'], s['ci'][0], s['ci'][1], s['ci'][1] - s['ci'][0], '', '', '', '',
                             'ja' if excl else 'nein', s['share_negative'], s['share_positive']])
            summary.append((short, eta, key, s, dd['false_per_bed_h']))
        if b['n_draws_without_crises']:
            L.append(f"Ziehungen ohne Krise (Anteil nicht definiert): {b['n_draws_without_crises']}")
        L.append('')
        print(f"{short}: " + '; '.join(f"{r} {b['stats'][r]['false_per_bed_h']['point']:.2f} [{b['stats'][r]['false_per_bed_h']['ci'][0]:.2f}; {b['stats'][r]['false_per_bed_h']['ci'][1]:.2f}] "
                                       f"{100 * b['stats'][r]['caught_frac']['point']:.1f} % [{100 * b['stats'][r]['caught_frac']['ci'][0]:.1f}; {100 * b['stats'][r]['caught_frac']['ci'][1]:.1f}]" for r in RULES))
    # ---- conclusion ------------------------------------------------------------------------------
    L += ['## Schlussfolgerung', '',
          '| Lauf | η | adaptiv − 2-aus-4: falsche je Bett·h [95 %] | signifikant (< 0) | adaptiv − 3-aus-4: erkannt weich [Prozentpunkte, 95 %] | signifikant (> 0) |',
          '|---|---|---|---|---|---|']
    by_run: dict[str, dict] = {}
    for short, eta, key, sc, sf in summary:
        by_run.setdefault(short, {'eta': eta})[key] = (sc, sf)
    for short, v in by_run.items():
        sf2 = v['adaptiv - 2-aus-4'][1]
        sc3 = v['adaptiv - 3-aus-4'][0]
        L.append(f"| {short} | {v['eta']:g} | {sf2['point']:+.2f} [{sf2['ci'][0]:+.2f}; {sf2['ci'][1]:+.2f}] | {'ja' if sf2['ci'][1] < 0 else 'nein'} | "
                 f"{100 * sc3['point']:+.1f} [{100 * sc3['ci'][0]:+.1f}; {100 * sc3['ci'][1]:+.1f}] | {'ja' if sc3['ci'][0] > 0 else 'nein'} |")
    L.append('')
    (HERE / 'bootstrap_patienten.md').write_text('\n'.join(L), encoding='utf-8')
    with (HERE / 'bootstrap_patienten.csv').open('w', encoding='utf-8', newline='') as fh:
        w = csv.writer(fh, delimiter=';', lineterminator='\n')
        w.writerow(['lauf', 'kurz', 'eta', 'groesse', 'regel_oder_differenz', 'punkt', 'boot_lo', 'boot_hi', 'boot_breite',
                    'klassisch_lo', 'klassisch_hi', 'klassisch_breite', 'verhaeltnis_breite', 'null_ausgeschlossen', 'anteil_reps_negativ', 'anteil_reps_positiv'])
        for r in rows_csv:
            w.writerow([f'{x:.5f}' if isinstance(x, float) else x for x in r])
    print('written: bootstrap_patienten.md, bootstrap_patienten.csv')
    return 0


if __name__ == '__main__':
    sys.exit(main())
