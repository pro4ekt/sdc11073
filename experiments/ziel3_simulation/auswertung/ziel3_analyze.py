"""ziel3_analyze.py -- steps 1, 2 and 4 of the Ziel 3 evaluation for one run (read-only on the logs).

    python ziel3_analyze.py --name ziel3_12h [--exclude SIM-P01/ABP ...]

Step 1: clock mapping PC 2 -> PC 1, analysis window, escalation rules (adaptiv = Rot of the Dashboard,
        1/2/3-aus-4, 2-aus-4 with 10 s hold-over) on the alarm stream, caught crises, delays, false
        escalations with exact confidence intervals, crisis classes.
Step 2: measured stream parameters -> messwerte_<name>.json
Step 4: checks (alarms of journal B without a line in alarm_in_pc1.csv, Rot with sdc_score <= 0.5 and
        Rot shorter than 0.5 s, crises missed by adaptiv) -> checks_<name>.md
Everything needed by the figures goes to results_<name>.json.

Without the PC 1 digest (files *_pc1.* in export/) the run is evaluated in the PC 2 time base: the window is
[last provider ready + 5 s, run end - 2 s], the k-aus-4 rules are computed, adaptiv is not available.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import (CLASSES, CRISIS_MARGIN_S, HOLD_S, LOGS, PAIR_CLASSES, PRIORITY, SOURCES, Alarm, ClockMap,  # noqa: E402
                    Crisis, Esc, IdentityMap, alarm_is_false, by_patient, clopper_pearson, episodes, evaluate_rule,
                    find_pc1_dir, intersects, k_of_n, k_of_n_tau, load_pc1, load_pc2, median_ci, poisson_ci, t_need_pair)

HERE = pathlib.Path(__file__).resolve().parent
TAU_GRID = (0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 15.0)
TAU_K = (2, 3)


def fmt(x, nd=1):
    return '-' if x is None else f'{x:.{nd}f}'


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--name', required=True)
    ap.add_argument('--exclude', action='append', default=[], metavar='PATIENT/SOURCE',
                    help='drop these alarms from the rule input (e.g. SIM-P01/ABP: not connected by the Dashboard)')
    ap.add_argument('--patients', type=int, default=20)
    ap.add_argument('--pc1-dir', default=None, metavar='DIR',
                    help='export/ folder of the PC 1 digest (rot.csv, clock.csv, ...); default: export/*_pc1.* of the run, '
                         'else logs/<name>_pc1/pc1/export when that folder exists')
    a = ap.parse_args(argv)
    run_dir = LOGS / a.name / 'pc2'
    pc2 = load_pc2(run_dir)
    pc1_dir = pathlib.Path(a.pc1_dir) if a.pc1_dir else None
    pc1 = load_pc1(run_dir, pc1_dir)
    if pc1 is None and pc1_dir is None and find_pc1_dir(a.name) is not None:
        pc1 = load_pc1(run_dir, find_pc1_dir(a.name))
    n_pat = a.patients
    notes = []
    if pc1 is not None:
        notes.append(f"PC 1 digest: {pc1['source_dir']}")

    # ---- clock and window ------------------------------------------------------------------
    if pc1 is not None:
        cm = ClockMap(pc1['clock'])
        accepted = [float(d['t_accepted']) for d in pc1['devices'] if d.get('t_accepted')]
        w0 = max(accepted) + 5.0
        w1 = float(pc1['summary']['run']['t_end_abs']) - 2.0
        time_base = 'PC 1'
        not_accepted = [f"{d['patient']}/{d['model']}" for d in pc1['devices'] if not d.get('t_accepted')]
        for d in not_accepted:
            notes.append(f'device without acceptance on PC 1: {d}')
    else:
        cm = IdentityMap()
        w0 = max(pc2['marks']['provider_ready']) + 5.0
        w1 = float(pc2['marks']['run_end']) - 2.0
        time_base = 'PC 2 (no PC 1 digest found)'
        notes.append('PC 1 digest missing: window from PC 2 marks, adaptiv not available, times in the PC 2 base')
    hours = (w1 - w0) / 3600.0
    exposure_bed_h = n_pat * hours

    # ---- streams in the analysis time base ---------------------------------------------------
    excl = {tuple(x.split('/')) for x in a.exclude}
    alarms_all = [Alarm(x.patient, x.source, x.prio, cm.to_pc1(x.t_on), cm.to_pc1(x.t_off), x.value) for x in pc2['alarms']]
    alarms = [x for x in alarms_all if (x.patient, x.source) not in excl]
    n_excluded = len(alarms_all) - len(alarms)
    crises_all = [Crisis(c.patient, c.cid, cm.to_pc1(c.t_start), cm.to_pc1(c.t_end),
                         {s: cm.to_pc1(t) for s, t in c.cross.items()}) for c in pc2['crises']]
    crises = [c for c in crises_all if c.t_end is not None and w0 <= c.t_start <= w1]
    n_open = sum(1 for c in crises_all if c.t_end is None)

    # ---- rules -----------------------------------------------------------------------------
    rules: dict[str, list[Esc]] = {
        '1-aus-4': k_of_n(alarms, 1, stream_end=w1),
        '2-aus-4': k_of_n(alarms, 2, stream_end=w1),
        '3-aus-4': k_of_n(alarms, 3, stream_end=w1),
        '2-aus-4+10s': k_of_n(alarms, 2, hold_s=HOLD_S, stream_end=w1),
    }
    if pc1 is not None:
        rules['adaptiv'] = sorted(pc1['rot'], key=lambda e: (e.t_start, e.patient))
    results = {}
    for name, escs in rules.items():
        r = evaluate_rule(escs, crises, crises_all, (w0, w1))
        lo, hi = poisson_ci(r['n_false'])
        r['false_per_bed_h'] = r['n_false'] / exposure_bed_h
        r['false_per_bed_h_ci'] = [lo / exposure_bed_h, hi / exposure_bed_h]
        if name != '1-aus-4':
            r['missed'] = r['n_crises'] - r['caught']
            clo, chi = clopper_pearson(r['caught'], r['n_crises'])
            r['caught_frac'] = r['caught'] / r['n_crises'] if r['n_crises'] else None
            r['caught_frac_ci'] = [clo, chi]
            r['missed_pct_ci'] = [100 * (1 - chi), 100 * (1 - clo)]
            slo, shi = clopper_pearson(r['caught_strict'], r['n_crises'])
            r['caught_strict_frac'] = r['caught_strict'] / r['n_crises'] if r['n_crises'] else None
            r['caught_strict_frac_ci'] = [slo, shi]
            r['median_delay_ci'] = list(median_ci(r['delays'])) if r['delays'] else None
        results[name] = r

    # ---- k-aus-4 with confirmation delay tau (grid) ------------------------------------------------
    tau_rules = {}
    for k in TAU_K:
        tau_rules[str(k)] = {}
        for tau in TAU_GRID:
            escs = k_of_n_tau(alarms, k, tau, stream_end=w1)
            r = evaluate_rule(escs, crises, crises_all, (w0, w1))
            lo, hi = poisson_ci(r['n_false'])
            slo, shi = clopper_pearson(r['caught_strict'], r['n_crises'])
            tau_rules[str(k)][f'{tau:g}'] = {
                'k': k, 'tau_s': tau, 'n_false': r['n_false'], 'false_per_bed_h': r['n_false'] / exposure_bed_h,
                'false_per_bed_h_ci': [lo / exposure_bed_h, hi / exposure_bed_h],
                'caught': r['caught'], 'caught_strict': r['caught_strict'], 'caught_after_end': r['caught_after_end'],
                'n_crises': r['n_crises'], 'caught_strict_frac': r['caught_strict'] / r['n_crises'] if r['n_crises'] else None,
                'caught_strict_frac_ci': [slo, shi],
                'median_delay_s': r['median_delay_s'], 'median_delay_ci': list(median_ci(r['delays'])) if r['delays'] else None,
                'n_delays': len(r['delays']), 'n_pre_cross': r['n_pre_cross'],
                'by_class': {kk: {'n': v['n'], 'caught': v['caught'], 'caught_strict': v['caught_strict']} for kk, v in r['by_class'].items()},
            }

    # ---- step 2: measured parameters ------------------------------------------------------------
    cr_p = by_patient(crises_all)
    false_alarms = [x for x in alarms_all if w0 <= x.t_on <= w1 and alarm_is_false(x, cr_p.get(x.patient, []), w1)]
    crisis_time = sum(c.t_end - c.t_start for c in crises)
    outside = exposure_bed_h * 3600.0 - crisis_time
    fpr, tpr = {}, {}
    for s in SOURCES:
        fa_time = sum(min(x.t_off if x.t_off is not None else w1, w1) - x.t_on for x in false_alarms if x.source == s)
        fpr[s] = fa_time / outside
        al_s = by_patient([x for x in alarms_all if x.source == s])
        on_time = 0.0
        for c in crises:
            for x in al_s.get(c.patient, []):
                end = x.t_off if x.t_off is not None else w1
                on_time += max(0.0, min(end, c.t_end) - max(x.t_on, c.t_start))
        tpr[s] = on_time / crisis_time if crisis_time else None
    d_false = statistics.fmean((x.t_off if x.t_off is not None else w1) - x.t_on for x in false_alarms)
    d_true = crisis_time / len(crises)
    eps = episodes(false_alarms, w1)
    n_src_dist = {k: sum(1 for e in eps if e['n_sources'] == k) for k in (1, 2, 3, 4)}
    total_false_time = sum(e['alarm_time'] for e in eps)
    c_art_a = sum(e['alarm_time'] for e in eps if e['n_sources'] == 4) / total_false_time
    c_art_b = sum(e['alarm_time'] for e in eps if e['n_sources'] >= 2) / total_false_time
    messwerte = {
        'name': a.name, 'time_base': time_base, 'window': [w0, w1], 'hours': hours, 'patients': n_pat,
        'exposure_bed_h': exposure_bed_h,
        'FPR': fpr, 'TPR': tpr, 'P_C': crisis_time / (exposure_bed_h * 3600.0),
        'd_false_s': d_false, 'd_true_s': d_true, 'n_false_alarms': len(false_alarms), 'n_crises': len(crises),
        'crises_per_bed_day': len(crises) / n_pat / (hours / 24.0),
        'false_alarms_per_source_day': {s: sum(1 for x in false_alarms if x.source == s) / n_pat / (hours / 24.0) for s in SOURCES},
        'episodes_by_n_sources': n_src_dist,
        'episodes_by_n_sources_share': {k: v / len(eps) for k, v in n_src_dist.items()},
        'c_art_a_all4_time_share': c_art_a, 'c_art_b_ge2_time_share': c_art_b,
        'c_art_a_all4_episode_share': n_src_dist[4] / len(eps), 'c_art_b_ge2_episode_share': sum(n_src_dist[k] for k in (2, 3, 4)) / len(eps),
        'definitions': {
            'FPR_j': 'time of false alarms of source j (not intersecting any crisis +-10 s) / time outside crises, window only',
            'TPR_j': 'time inside crises during which source j is in alarm / total crisis time',
            'P_C': 'total crisis time / (patients x window)',
            'd_false_s': 'mean duration of a false alarm', 'd_true_s': 'mean crisis duration',
            'episodes': 'union of overlapping false alarms of one patient; n_sources = distinct sources in the episode',
            'c_art_a': 'share of false alarm time that lies in episodes with all 4 sources',
            'c_art_b': 'share of false alarm time that lies in episodes with >= 2 sources',
        },
    }
    (HERE / f'messwerte_{a.name}.json').write_text(json.dumps(messwerte, indent=2, ensure_ascii=False), encoding='utf-8')

    # ---- episodes with Rot (figure 5) ----------------------------------------------------------
    ep_rows = []
    if pc1 is not None:
        rot_p = by_patient(rules['adaptiv'])
        for e in eps:
            if e['n_sources'] < 2:
                continue
            has_rot = any(intersects(r.t_start, r.t_end, e['t_start'], e['t_end']) for r in rot_p.get(e['patient'], []))
            ep_rows.append({'klass': e['klass'], 'overlap_s': e['overlap_s'], 'duration_s': e['duration_s'],
                            'n_sources': e['n_sources'], 'has_rot': has_rot})

    # ---- model of chapter 3 vs simulation: false episodes with exactly two sources ------------------
    modell_paare = {}
    if pc1 is not None:
        for k in PAIR_CLASSES:
            sub = [e for e in ep_rows if e['n_sources'] == 2 and e['klass'] == k]
            tn = t_need_pair(k)
            agree = sum(1 for e in sub if e['has_rot'] == (e['overlap_s'] >= tn['t_need_s'])) if tn['t_need_s'] is not None else None
            modell_paare[k] = {'n': len(sub), 'sdc': tn['sdc'], 'k': tn['k'], 't_need_s': tn['t_need_s'],
                               'n_rot': sum(1 for e in sub if e['has_rot']),
                               'agreement': None if not sub or agree is None else agree / len(sub),
                               'agreement_ci': list(clopper_pearson(agree, len(sub))) if sub and agree is not None else None}

    # ---- timelines (figure 6): (a) typical 4-source crisis, Rot inside the crisis; (b) SIM-P15-C020 ----
    timelines = []
    if pc1 is not None:
        ad = {p['cid']: p for p in results['adaptiv']['per_crisis']}
        k3 = {p['cid']: p for p in results['3-aus-4']['per_crisis']}

        def timeline_of(c: Crisis, label: str) -> dict:
            t0, t1 = c.t_start - 60.0, c.t_end + 60.0
            sel = lambda lst: [(x.t_start, x.t_end) for x in lst if x.patient == c.patient and intersects(x.t_start, x.t_end, t0, t1)]
            return {'label': label, 'patient': c.patient, 'cid': c.cid, 'klass': c.klass, 't0': t0, 't1': t1,
                    'crisis': [c.t_start, c.t_end], 'cross': c.cross,
                    'alarms': [(x.source, x.t_on, x.t_off if x.t_off is not None else w1) for x in alarms
                               if x.patient == c.patient and intersects(x.t_on, x.t_off if x.t_off is not None else w1, t0, t1)],
                    'rules': {k: sel(rules[k]) for k in ('2-aus-4', '3-aus-4', 'adaptiv')},
                    'adaptiv_delay_s': ad[c.cid]['delay_s']}

        c_by_id = {c.cid: c for c in crises}
        typ = [c for c in crises if c.klass == '4' and ad[c.cid]['caught'] and ad[c.cid]['delay_s'] is not None
               and ad[c.cid]['delay_s'] >= 0.0 and c.t_start <= ad[c.cid]['esc_start'] <= c.t_end]
        if typ:
            med = float(np.median([ad[c.cid]['delay_s'] for c in typ]))
            c_a = min(typ, key=lambda c: abs(ad[c.cid]['delay_s'] - med))
            timelines.append(timeline_of(c_a, 'a'))
        c_b = c_by_id.get('SIM-P15-C020')
        if c_b is None:                                   # fallback: any Hi+Me crisis with Rot inside, 3-aus-4 missed
            cands = [c for c in crises if c.klass == 'Hi+Me' and ad[c.cid]['caught'] and not k3[c.cid]['caught']
                     and c.t_start <= ad[c.cid]['esc_start'] <= c.t_end]
            c_b = cands[0] if cands else None
        if c_b is not None:
            tb = timeline_of(c_b, 'b')
            tb['k3_caught'] = k3[c_b.cid]['caught']
            tb['rot_inside'] = bool(c_b.t_start <= ad[c_b.cid]['esc_start'] <= c_b.t_end) if ad[c_b.cid]['esc_start'] else False
            timelines.append(tb)

        # (c) a false episode without crisis: exactly two sources (pair with Me or Lo) together 5-9 s,
        #     2-aus-4 escalates, adaptiv and 3-aus-4 do not; the most typical one (overlap closest to the median)
        rot_p = by_patient(rules['adaptiv'])
        k2_p, k3_p = by_patient(rules['2-aus-4']), by_patient(rules['3-aus-4'])

        def window_sel(patient, t0, t1):
            return {k: [(x.t_start, x.t_end) for x in rules[k] if x.patient == patient and intersects(x.t_start, x.t_end, t0, t1)]
                    for k in ('2-aus-4', '3-aus-4', 'adaptiv')}

        cands_c = []
        for e in eps:
            if e['n_sources'] != 2 or e['klass'] not in ('Hi+Me', 'Hi+Lo', 'Me+Lo') or not (5.0 <= e['overlap_s'] <= 9.0):
                continue
            if any(intersects(r.t_start, r.t_end, e['t_start'], e['t_end']) for r in rot_p.get(e['patient'], [])):
                continue
            if any(intersects(r.t_start, r.t_end, e['t_start'], e['t_end']) for r in k3_p.get(e['patient'], [])):
                continue
            if not any(intersects(r.t_start, r.t_end, e['t_start'], e['t_end']) for r in k2_p.get(e['patient'], [])):
                continue
            cands_c.append(e)
        if cands_c:
            med_ov = float(np.median([e['overlap_s'] for e in cands_c]))
            e = min(cands_c, key=lambda x: (abs(x['overlap_s'] - med_ov), x['t_start']))
            t0, t1 = e['t_start'] - 30.0, e['t_end'] + 30.0
            timelines.append({'label': 'c', 'patient': e['patient'], 'cid': None, 'klass': e['klass'], 't0': t0, 't1': t1,
                              'crisis': None, 'cross': {}, 'episode': [e['t_start'], e['t_end']], 'overlap_s': e['overlap_s'],
                              'sources': e['sources'], 't_need_s': t_need_pair(e['klass'])['t_need_s'],
                              'median_overlap_s': med_ov, 'n_candidates': len(cands_c),
                              'alarms': [(x.source, x.t_on, x.t_off if x.t_off is not None else w1) for x in alarms
                                         if x.patient == e['patient'] and intersects(x.t_on, x.t_off if x.t_off is not None else w1, t0, t1)],
                              'rules': window_sel(e['patient'], t0, t1)})

        # (d) one patient, 60 min without crisis, escalation counts per rule closest to the run-wide mean per
        #     patient and hour (windows on a 60-s grid; crises of the patient must stay 60 s away)
        WIN_D = 3600.0
        means = {k: results[k]['n_escalations_window'] / (n_pat * hours) for k in ('2-aus-4', '3-aus-4', 'adaptiv')}
        starts_p = {k: {p: sorted(x.t_start for x in lst) for p, lst in by_patient(rules[k]).items()} for k in means}
        cr_by_p = by_patient(crises_all)
        best = None
        import bisect
        for pat in sorted({x.patient for x in alarms}):
            t = w0
            while t + WIN_D <= w1:
                if not any(intersects(c.t_start, (c.t_end if c.t_end is not None else w1), t - 60.0, t + WIN_D + 60.0) for c in cr_by_p.get(pat, [])):
                    counts = {}
                    for k in means:
                        s = starts_p[k].get(pat, [])
                        counts[k] = bisect.bisect_left(s, t + WIN_D) - bisect.bisect_left(s, t)
                    score = sum((counts[k] - means[k]) ** 2 for k in means)
                    if best is None or score < best[0] - 1e-9:
                        best = (score, pat, t, counts)
                t += 60.0
        if best is not None:
            score, pat, t, counts = best
            timelines.append({'label': 'd', 'patient': pat, 'cid': None, 'klass': None, 't0': t, 't1': t + WIN_D, 'crisis': None,
                              'cross': {}, 'counts': counts, 'means': means, 'score': score, 'window_s': WIN_D,
                              'alarms': [(x.source, x.t_on, x.t_off if x.t_off is not None else w1) for x in alarms
                                         if x.patient == pat and intersects(x.t_on, x.t_off if x.t_off is not None else w1, t, t + WIN_D)],
                              'rules': window_sel(pat, t, t + WIN_D)})
    timeline = timelines[-1] if timelines else None

    # ---- hourly table (alarms, false alarms, crises, false escalations per rule) -------------------
    hourly = []
    for h in range(int(np.ceil(hours - 1e-9))):
        a0, a1 = w0 + 3600.0 * h, min(w0 + 3600.0 * (h + 1), w1)
        hourly.append({'hour': h + 1, 'from': a0, 'to': a1, 'duration_h': (a1 - a0) / 3600.0,
                       'alarms': sum(1 for x in alarms_all if a0 <= x.t_on < a1),
                       'false_alarms': sum(1 for x in false_alarms if a0 <= x.t_on < a1),
                       'crises': sum(1 for c in crises if a0 <= c.t_start < a1),
                       'false_esc': {name: sum(1 for t in r['false_starts'] if a0 <= t < a1) for name, r in results.items()}})

    # ---- step 4: checks -------------------------------------------------------------------------
    checks = {}
    if pc1 is not None:
        checks = run_checks(a.name, alarms_all, crises, results, rules, pc1, cm, w0, w1)

    # ---- outputs ---------------------------------------------------------------------------------
    out = {'name': a.name, 'time_base': time_base, 'window': [w0, w1], 'hours': hours, 'patients': n_pat,
           'exposure_bed_h': exposure_bed_h, 'excluded': sorted('/'.join(x) for x in excl), 'n_alarms_excluded': n_excluded,
           'n_alarms': len(alarms_all), 'n_crises_window': len(crises), 'n_crises_open': n_open,
           'clock': {'points_all': cm.n_all, 'points_used': cm.n_used, 'offset_min': cm.off_min, 'offset_max': cm.off_max},
           'notes': notes,
           'rules': {k: {kk: vv for kk, vv in v.items() if kk != 'per_crisis'} for k, v in results.items()},
           'per_crisis': {k: v['per_crisis'] for k, v in results.items()},
           'crises': [{'cid': c.cid, 'patient': c.patient, 't_start': c.t_start, 't_end': c.t_end, 'klass': c.klass,
                       'cross': c.cross} for c in crises],
           'episodes': ep_rows, 'timeline': timeline, 'timelines': timelines, 'modell_paare': modell_paare,
           'tau_rules': tau_rules, 'messwerte': messwerte, 'checks': checks, 'hourly': hourly}
    (HERE / f'results_{a.name}.json').write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str), encoding='utf-8')

    # ---- console report --------------------------------------------------------------------------
    print(f'run {a.name}: time base {time_base}; window {w0:.3f} .. {w1:.3f} = {hours:.3f} h x {n_pat} = {exposure_bed_h:.1f} bed*h')
    if pc1 is not None:
        print(f'clock: {cm.n_used}/{cm.n_all} points (rtt < 20 ms), offset {cm.off_min:+.3f} .. {cm.off_max:+.3f} s')
    print(f'alarms: {len(alarms_all)} (excluded {n_excluded}: {", ".join(out["excluded"]) or "-"}); crises in window with end: {len(crises)} (open at end: {n_open})')
    for n in notes:
        print('note:', n)
    print()
    print(f"{'rule':13s} {'false esc.':>10s} {'per bed*h [95%]':>22s} {'caught':>10s} {'strict':>8s} {'after end':>9s} {'median delay':>13s} {'pre-cross':>9s}")
    for name in ('adaptiv', '2-aus-4', '3-aus-4', '2-aus-4+10s', '1-aus-4'):
        if name not in results:
            continue
        r = results[name]
        ci = r['false_per_bed_h_ci']
        caught = f"{r['caught']}/{r['n_crises']}" if name != '1-aus-4' else '-'
        strict = f"{r['caught_strict']}" if name != '1-aus-4' else '-'
        after = f"{r['caught_after_end']}" if name != '1-aus-4' else '-'
        print(f"{name:13s} {r['n_false']:10d} {r['false_per_bed_h']:6.2f} [{ci[0]:.2f}, {ci[1]:.2f}]   {caught:>10s} {strict:>8s} {after:>9s} {fmt(r['median_delay_s']):>13s} {r['n_pre_cross']:9d}")
    if modell_paare:
        print()
        print('model vs simulation, false episodes with exactly two sources (Rot <=> overlap >= t_need):')
        for k, v in modell_paare.items():
            ag = '-' if v['agreement'] is None else f"{100 * v['agreement']:.1f} % [{100 * v['agreement_ci'][0]:.1f}; {100 * v['agreement_ci'][1]:.1f}]"
            print(f"  {k:6s} n={v['n']:4d}  Rot={v['n_rot']:3d}  SDC={v['sdc']:.4f} k={v['k']} t_need={v['t_need_s']:.1f} s  agreement {ag}")
    for tl in timelines:
        if tl['label'] in ('a', 'b'):
            print(f"timeline ({tl['label']}): {tl['cid']} {tl['klass']} adaptiv delay {fmt(tl['adaptiv_delay_s'])} s")
        elif tl['label'] == 'c':
            print(f"timeline (c): {tl['patient']} {tl['klass']} overlap {tl['overlap_s']:.1f} s (median of {tl['n_candidates']} candidates "
                  f"{tl['median_overlap_s']:.1f} s), t_need {tl['t_need_s']:.1f} s")
        else:
            print(f"timeline (d): {tl['patient']} {tl['window_s'] / 60:.0f} min from {tl['t0']:.0f}: counts {tl['counts']} vs means per hour "
                  + ', '.join(f'{k} {v:.2f}' for k, v in tl['means'].items()) + f" (score {tl['score']:.3f})")
    print()
    print('k-aus-4 with confirmation delay tau (strict: escalation begins before the crisis end):')
    print(f"{'k':>2s} {'tau':>4s} {'false':>6s} {'per bed*h [95%]':>22s} {'strict':>8s} {'missed%':>8s} {'after end':>9s} {'median delay [95%]':>22s}")
    for k in TAU_K:
        for tau in TAU_GRID:
            r = tau_rules[str(k)][f'{tau:g}']
            ci_ = r['false_per_bed_h_ci']
            mci = r['median_delay_ci'] or [float('nan'), float('nan')]
            print(f"{k:2d} {tau:4g} {r['n_false']:6d} {r['false_per_bed_h']:6.2f} [{ci_[0]:.2f}, {ci_[1]:.2f}]   {r['caught_strict']:3d}/{r['n_crises']:<4d} "
                  f"{100 * (1 - r['caught_strict_frac']):7.1f} {r['caught_after_end']:9d} {fmt(r['median_delay_s']):>7s} [{mci[0]:.1f}, {mci[1]:.1f}]")
    print()
    print('caught by crisis class (caught/n):')
    print(f"{'rule':13s} " + ' '.join(f'{k:>9s}' for k in CLASSES))
    for name in ('adaptiv', '2-aus-4', '3-aus-4', '2-aus-4+10s'):
        if name in results:
            bc = results[name]['by_class']
            print(f'{name:13s} ' + ' '.join(f"{bc[k]['caught']:>4d}/{bc[k]['n']:<4d}" for k in CLASSES))
    print()
    print(f"measured: FPR " + ' '.join(f'{s}={fpr[s]:.4f}' for s in SOURCES) + f"; TPR " + ' '.join(f'{s}={tpr[s]:.3f}' for s in SOURCES))
    print(f"          P(C)={messwerte['P_C']:.4f}  d_false={d_false:.1f} s  d_true={d_true:.1f} s  episodes 1/2/3/4 = "
          f"{n_src_dist[1]}/{n_src_dist[2]}/{n_src_dist[3]}/{n_src_dist[4]}  c_art(a)={c_art_a:.3f} c_art(b)={c_art_b:.3f}")
    print(f'written: results_{a.name}.json, messwerte_{a.name}.json' + (f', checks_{a.name}.md' if checks else ''))
    return 0


def run_checks(name, alarms_all, crises, results, rules, pc1, cm, w0, w1) -> dict:
    """Step 4: three checks, written to checks_<name>.md and returned as a dict."""
    lines = [f'# Prüfungen {name}', '']
    rot = rules['adaptiv']
    rot_p = by_patient(rot)
    # ---- 4.1 alarms of B without a line in alarm_in_pc1 ------------------------------------------
    log_first = min(t for t, *_ in pc1['alarm_in'])
    log_last = max(t for t, *_ in pc1['alarm_in'])
    import bisect
    idx: dict[tuple, list[tuple[float, str]]] = {}
    for t, pat, src, oo, kind in pc1['alarm_in']:
        idx.setdefault((pat, src, oo), []).append((t, kind))
    for v in idx.values():
        v.sort()
    idx_t = {k: [c[0] for c in v] for k, v in idx.items()}
    used: dict[tuple, set] = {}
    tol = 2.5
    events = []
    for x in alarms_all:
        events.append((x.t_on, x.patient, x.source, 'on'))
        if x.t_off is not None:
            events.append((x.t_off, x.patient, x.source, 'off'))
    events = [e for e in events if log_first <= e[0] <= log_last]
    unmatched = []
    dts: dict[str, list[float]] = {'verdict': [], 'log': []}
    for t, pat, src, oo in sorted(events):
        cand = idx.get((pat, src, oo), [])
        times = idx_t.get((pat, src, oo), [])
        best, best_d = None, None
        i = bisect.bisect_left(times, t - tol)
        while i < len(cand) and cand[i][0] <= t + tol:
            if i not in used.setdefault((pat, src, oo), set()):
                d = abs(cand[i][0] - t)
                if best is None or d < best_d:
                    best, best_d = i, d
            i += 1
        if best is None:
            unmatched.append((t, pat, src, oo))
        else:
            used[(pat, src, oo)].add(best)
            dts.setdefault(cand[best][1], []).append(cand[best][0] - t)
    ttl_p: dict[str, list[float]] = {}
    for r in pc1['ttl']:
        ttl_p.setdefault(r['patient'], []).append(float(r['t']))
    cat = {'during_rot': 0, 'ttl_expired_near': 0, 'other': 0}
    cat_onoff = {'on': 0, 'off': 0}
    other_examples = []
    for t, pat, src, oo in unmatched:
        cat_onoff[oo] += 1
        if any(r.t_start - 0.5 <= t <= r.t_end + 0.5 for r in rot_p.get(pat, [])):
            cat['during_rot'] += 1
        elif any(abs(tt - t) <= 2.5 for tt in ttl_p.get(pat, [])):
            cat['ttl_expired_near'] += 1
        else:
            cat['other'] += 1
            if len(other_examples) < 8:
                other_examples.append((t, pat, src, oo))
    dt_stats = {}
    for kind, vals in dts.items():
        if vals:
            v = np.array(vals)
            dt_stats[kind] = {'n': int(len(v)), 'p50_ms': float(np.median(v) * 1000), 'p90_ms': float(np.percentile(v, 90) * 1000),
                              'min_ms': float(v.min() * 1000), 'max_ms': float(v.max() * 1000)}
    c1 = {'events_in_log_span': len(events), 'unmatched': len(unmatched), 'unmatched_pct': 100.0 * len(unmatched) / len(events),
          'by_on_off': cat_onoff, 'categories': cat, 'log_span': [log_first, log_last], 'tolerance_s': tol, 'dt_pc1_minus_b': dt_stats}
    lines += ['## 4.1 Alarme aus Journal B ohne Zeile in alarm_in_pc1.csv', '',
              f'Log-Abdeckung PC 1: {log_first:.3f} .. {log_last:.3f} (PC-1-Uhr). Ereignisse von B in dieser Spanne: {len(events)} '
              f'(ON und OFF, Zeiten nach PC 1 umgerechnet). Zuordnung: gleicher Patient, Quelle, on/off, |dt| <= {tol} s, jede Logzeile höchstens einmal.', '',
              f"Ohne Zuordnung: {len(unmatched)} ({c1['unmatched_pct']:.2f} %), davon ON {cat_onoff['on']}, OFF {cat_onoff['off']}.", '',
              'Zeitdifferenz Logzeile PC 1 − Echo Provider (nach Uhrabgleich): '
              + '; '.join(f"{k}: n={s['n']}, Median {s['p50_ms']:.0f} ms, p90 {s['p90_ms']:.0f} ms, min {s['min_ms']:.0f} ms, max {s['max_ms']:.0f} ms"
                          for k, s in dt_stats.items()), '',
              f"- während eines laufenden Rot desselben Patienten: {cat['during_rot']}",
              f"- innerhalb 2,5 s eines TTL-Expired-Eintrags des Patienten (Log nennt dann keine Einzelalarme): {cat['ttl_expired_near']}",
              f"- sonst (Verlust nicht auszuschließen): {cat['other']}", '']
    if other_examples:
        lines.append('Beispiele der Kategorie „sonst“ (t PC 1; Patient; Quelle; on/off):')
        lines += [f'- {t:.3f}; {p}; {s}; {o}' for t, p, s, o in other_examples]
        lines.append('')
    # ---- 4.2 Rot with sdc_score <= 0.5 and Rot shorter than 0.5 s -----------------------------------
    al_p = by_patient(alarms_all)

    def context(r: Esc) -> dict:
        als = al_p.get(r.patient, [])
        active = [x.source for x in als if x.t_on <= r.t_start + 0.05 and (x.t_off is None or x.t_off >= r.t_start - 0.05)]
        starts_near = [(x.source, round(x.t_on - r.t_start, 2)) for x in als if -15.0 <= x.t_on - r.t_start <= 2.0]
        prev_rot = [p for p in rot_p.get(r.patient, []) if p.t_end <= r.t_start and p is not r]
        gap_rot = (r.t_start - max(p.t_end for p in prev_rot)) if prev_rot else None
        prev_off = [x.t_off for x in als if x.t_off is not None and x.t_off <= r.t_start]
        gap_alarm = (r.t_start - max(prev_off)) if prev_off else None
        in_crisis = any(intersects(r.t_start, r.t_end, c.t_start - CRISIS_MARGIN_S, c.t_end + CRISIS_MARGIN_S)
                        for c in crises if c.patient == r.patient)
        return {'patient': r.patient, 't_start': r.t_start, 'duration_s': r.t_end - r.t_start, 'score': r.score,
                'active_at_start': active, 'alarm_starts_last_15s': starts_near, 'gap_prev_rot_s': gap_rot,
                'gap_prev_alarm_off_s': gap_alarm, 'in_crisis': in_crisis}

    low = [context(r) for r in rot if r.score is not None and r.score <= 0.5]
    short = [context(r) for r in rot if (r.t_end - r.t_start) < 0.5]

    def summarize(rows, title):
        n = len(rows)
        warm10 = sum(1 for r in rows if r['gap_prev_rot_s'] is not None and r['gap_prev_rot_s'] <= 10.0)
        warm30 = sum(1 for r in rows if r['gap_prev_rot_s'] is not None and r['gap_prev_rot_s'] <= 30.0)
        alarm10 = sum(1 for r in rows if r['gap_prev_alarm_off_s'] is not None and r['gap_prev_alarm_off_s'] <= 10.0)
        act = {k: sum(1 for r in rows if len(r['active_at_start']) == k) for k in (0, 1, 2, 3, 4)}
        crisis = sum(1 for r in rows if r['in_crisis'])
        dur = sorted(r['duration_s'] for r in rows)
        out = [f'## {title}', '', f'n = {n}; davon in/nahe einer Krise: {crisis}; aktive B-Alarme beim Rot-Start 0/1/2/3/4 Quellen: '
               + '/'.join(str(act[k]) for k in (0, 1, 2, 3, 4)) + ';',
               f'vorheriges Rot desselben Patienten endete <= 10 s davor: {warm10}, <= 30 s davor: {warm30}; '
               f'letzter Alarm-OFF des Patienten <= 10 s davor: {alarm10}; Dauer min/median/max = '
               + (f'{dur[0]:.2f}/{dur[len(dur)//2]:.2f}/{dur[-1]:.2f} s' if dur else '—'), '',
               '| t_start (PC 1) | Patient | Dauer s | sdc_score | aktiv beim Start | Alarmstarts in den 15 s davor (Quelle, dt) | Abstand zum vorigen Rot s | Krise |',
               '|---|---|---|---|---|---|---|---|']
        for r in rows:
            out.append(f"| {r['t_start']:.3f} | {r['patient']} | {r['duration_s']:.2f} | {fmt(r['score'], 3)} | {','.join(r['active_at_start']) or '—'} | "
                       f"{' '.join(f'{s}:{d:+.1f}' for s, d in r['alarm_starts_last_15s']) or '—'} | {fmt(r['gap_prev_rot_s'])} | {'ja' if r['in_crisis'] else 'nein'} |")
        out.append('')
        return out, {'n': n, 'in_crisis': crisis, 'active_at_start': act, 'prev_rot_within_10s': warm10,
                     'prev_rot_within_30s': warm30, 'prev_alarm_off_within_10s': alarm10}

    l2, s_low = summarize(low, 'Rot mit sdc_score <= 0,5')
    l3, s_short = summarize(short, 'Rot kürzer als 0,5 s')
    lines += ['## 4.2 Auffällige Rot-Episoden', ''] + l2 + l3
    # ---- 4.3 crises missed by adaptiv -------------------------------------------------------------
    missed = [p for p in results['adaptiv']['per_crisis'] if not p['caught']]
    cr = {c.cid: c for c in crises}
    rows = []
    lines += ['## 4.3 Von adaptiv verpasste Krisen', '',
              '| Krise | Patient | Klasse | Dauer s | Quellen: Schwelle überschritten bei t − Start (t − Ende) | 2-aus-4 | 3-aus-4 | nächstes Rot des Patienten (Abstand zum Krisenstart) |',
              '|---|---|---|---|---|---|---|---|']
    k2 = {p['cid']: p for p in results['2-aus-4']['per_crisis']}
    k3 = {p['cid']: p for p in results['3-aus-4']['per_crisis']}
    for p in missed:
        c = cr[p['cid']]
        cross = ', '.join(f'{s} {c.cross[s] - c.t_start:+.1f} ({c.cross[s] - c.t_end:+.1f})' for s in SOURCES if c.cross.get(s) is not None) or '—'
        near = [r for r in rot_p.get(c.patient, [])]
        nearest = min(near, key=lambda r: min(abs(r.t_start - c.t_start), abs(r.t_end - c.t_start))) if near else None
        near_txt = '—' if nearest is None else f'{nearest.t_start - c.t_start:+.0f} s (Dauer {nearest.t_end - nearest.t_start:.1f} s)'
        rows.append({'cid': c.cid, 'patient': c.patient, 'klass': c.klass, 'duration_s': c.t_end - c.t_start,
                     'cross_rel_start': {s: (None if c.cross.get(s) is None else c.cross[s] - c.t_start) for s in SOURCES},
                     'caught_2': k2[c.cid]['caught'], 'caught_3': k3[c.cid]['caught'], 'nearest_rot': near_txt})
        lines.append(f"| {c.cid} | {c.patient} | {c.klass} | {c.t_end - c.t_start:.1f} | {cross} | "
                     f"{'ja' if k2[c.cid]['caught'] else 'nein'} | {'ja' if k3[c.cid]['caught'] else 'nein'} | {near_txt} |")
    lines.append('')
    (HERE / f'checks_{name}.md').write_text('\n'.join(lines), encoding='utf-8')
    return {'alarm_in_match': c1, 'rot_low_score': s_low, 'rot_short': s_short, 'missed_by_adaptiv': rows}


if __name__ == '__main__':
    sys.exit(main())
