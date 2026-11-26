"""modell_g7.py -- the analytic load model of chapter 3 / appendix G (KONTEXT_Belastungsmodell_fuer_PC2.md) in
Python, and its comparison with one network run (read-only on results_<name>.json / messwerte_<name>.json).

* decision per subset F of simultaneously active channels: SDC(F), k, branch "sofort" / "schmelzen" / "nie", t(F)
* expected false escalations per bed*h, corrected count over transitions (formula G.7); for adaptiv an upper bound
* expected strictly caught crises TPR_sys = sum_F P(F|C) * P(D >= t(F)), D = d0 + Exp(d_true - d0)
* per-channel FPR is allowed (q_j = (1 - c_art) * FPR_j); with one FPR for all channels the numbers of the
  KONTEXT are reproduced (2-aus-4 3.94, 3-aus-4 1.22, adaptiv <= 2.61 per bed*h for FPR 0.026, c_art 0.10,
  d_false 8 s, P(C) 0.01157)

    python modell_g7.py                                     # reference values only
    python modell_g7.py --name ziel3_6h_ideal --c-art 0.108 --d-false 8.31
        -> modell_<name>.md, modell_<name>.json (nominal model, Monte Carlo of the KONTEXT, model with the
           measured parameters of the run, TPR_sys, crises missed by adaptiv)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import LOGS, SOURCES  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
P_DEFAULT = (3, 3, 2, 1)                      # priorities ECG, Vent, SpO2, ABP (Hi, Hi, Me, Lo)
PRIO_NAME = {3: 'Hi', 2: 'Me', 1: 'Lo'}
NOMINAL = {'fpr': 0.026, 'c_art': 0.10, 'd_false': 8.0, 'eta': 0.9, 'd0': 15.0, 'd_true': 30.0,
           'T': 10.0, 'dmin': 0.5, 'alpha': 0.5}
# calib() of the KONTEXT with n = 300, FDR = 0.9, dT = 30 s, TPR = 0.9: P(C) = n (1 - FDR) dT / (86400 TPR)
P_C_NOMINAL = 300 * (1 - 0.9) * 30.0 / (86400.0 * 0.9)
# section 4 of the KONTEXT: Monte Carlo exactly after section 1, 2000 bed hours, decision model of chapter 3
MONTE_CARLO = {'2-aus-4': 3.74, '3-aus-4': 1.25, 'adaptiv': 1.39}
RULES = ('adaptiv', '2-aus-4', '3-aus-4')


def rhu(x: float) -> int:
    return int(math.floor(x + 0.5 + 1e-12))


def sdc_of(prs, P=P_DEFAULT, alpha: float = 0.5) -> float:
    return alpha * max(prs) / max(P) + (1 - alpha) * sum(prs) / sum(P)


def outcome(prs, rule: str, P=P_DEFAULT, T: float = 10.0, dmin: float = 0.5, alpha: float = 0.5) -> dict:
    """Decision of a rule for the set of active channels with priorities prs (cold start, all begin together):
    {k, sdc, branch: 'sofort' | 'schmelzen' | 'nie', t}."""
    M, r = len(P), len(prs)
    if rule == 'adaptiv':
        sdc = sdc_of(prs, P, alpha)
        k = rhu(M - (M - 2) * sdc)
        if r >= k:
            return {'k': k, 'sdc': sdc, 'branch': 'sofort', 't': T * k / r}
        if r >= dmin * k - 1e-12 and sdc > 0:
            return {'k': k, 'sdc': sdc, 'branch': 'schmelzen', 't': T + T / sdc * math.log(k / r)}
        return {'k': k, 'sdc': sdc, 'branch': 'nie', 't': math.inf}
    k = min(int(rule.split('-')[0]), M)
    return {'k': k, 'sdc': None, 'branch': 'sofort' if r >= k else 'nie', 't': 0.0 if r >= k else math.inf}


def surv_crisis(t: float, d0: float, d_true: float) -> float:
    """Share of crises lasting at least t: D = d0 + Exp(d_true - d0)."""
    if not math.isfinite(t):
        return 0.0
    d0 = min(d0, d_true - 1.0)
    return 1.0 if t <= d0 else math.exp(-(t - d0) / (d_true - d0))


def _subsets(P):
    M = len(P)
    for mask in range(1, 1 << M):
        yield mask, [j for j in range(M) if mask & (1 << j)]


def false_escalations_g7(rule: str, fpr, c_art: float, d_false: float, p_c: float = P_C_NOMINAL,
                         P=P_DEFAULT, T: float = 10.0, dmin: float = 0.5, alpha: float = 0.5) -> dict:
    """Expected false escalations per bed*h, corrected count over transitions (G.7): a subset F is entered from
    F without j when channel j switches on (rate P(F)/d_false per such j); counted are the transitions from a
    non-escalating F without j into an escalating F; 'schmelzen' subsets additionally with exp(-r t / d_false);
    common bursts c_art * FPR / d_false. fpr: one value or one per channel (q_j = (1 - c_art) FPR_j).
    For adaptiv this is an upper bound (window T not considered)."""
    M = len(P)
    fprs = [float(fpr)] * M if isinstance(fpr, (int, float)) else [float(x) for x in fpr]
    q = [(1.0 - c_art) * f for f in fprs]
    esc = {mask: outcome([P[j] for j in js], rule, P, T, dmin, alpha) for mask, js in _subsets(P)}
    rate = 0.0
    detail = []
    for mask, js in _subsets(P):
        o = esc[mask]
        if o['branch'] == 'nie':
            continue
        p_n = 1.0
        for j in range(M):
            p_n *= q[j] if j in js else (1.0 - q[j])
        entries = sum(1 for j in js if (mask & ~(1 << j)) == 0 or esc[mask & ~(1 << j)]['branch'] != 'sofort')
        s = 1.0 if o['branch'] == 'sofort' else math.exp(-len(js) * o['t'] / d_false)
        contrib = entries * p_n / d_false * s
        rate += contrib
        detail.append({'F': '+'.join(PRIO_NAME[P[j]] for j in js), 'r': len(js), 'branch': o['branch'], 't_s': o['t'],
                       'entries': entries, 'per_bed_h': contrib * 3600.0 * (1.0 - p_c)})
    burst = c_art * (sum(fprs) / M) / d_false
    scale = 3600.0 * (1.0 - p_c)
    return {'rule': rule, 'per_bed_h': (rate + burst) * scale, 'transitions_per_bed_h': rate * scale,
            'bursts_per_bed_h': burst * scale, 'raw_per_bed_h': sum(fprs) / d_false * scale,
            'fpr': fprs, 'c_art': c_art, 'd_false': d_false, 'p_c': p_c, 'detail': detail}


def tpr_sys(rule: str, eta: float, d0: float, d_true: float, P=P_DEFAULT, T: float = 10.0, dmin: float = 0.5,
            alpha: float = 0.5) -> dict:
    """Expected share of strictly caught crises: sum over subsets F of P(F|C) * P(D >= t(F)); every channel shows
    the crisis independently with probability eta from its beginning to its end."""
    M = len(P)
    total, total_inf = 0.0, 0.0
    by_r: dict[int, float] = {}
    for mask, js in _subsets(P):
        o = outcome([P[j] for j in js], rule, P, T, dmin, alpha)
        p_f = eta ** len(js) * (1.0 - eta) ** (M - len(js))
        s = surv_crisis(o['t'], d0, d_true) if o['branch'] != 'nie' else 0.0
        if o['branch'] != 'nie':
            total_inf += p_f
        total += p_f * s
        by_r[len(js)] = by_r.get(len(js), 0.0) + p_f * s
    return {'rule': rule, 'tpr_strict': total, 'tpr_unlimited': total_inf, 'by_r': by_r, 'eta': eta, 'd0': d0, 'd_true': d_true}


def nominal_table() -> dict:
    n = NOMINAL
    out = {}
    for rule in RULES:
        out[rule] = {'g7_per_bed_h': false_escalations_g7(rule, n['fpr'], n['c_art'], n['d_false'])['per_bed_h'],
                     'monte_carlo_per_bed_h': MONTE_CARLO[rule],
                     'tpr_strict': tpr_sys(rule, n['eta'], n['d0'], n['d_true'])['tpr_strict']}
    return out


def fmt_ci(x, ci, nd=2):
    return f'{x:.{nd}f} [{ci[0]:.{nd}f}; {ci[1]:.{nd}f}]'


def compare_run(name: str, c_art: float | None, d_false: float | None, eta: float | None) -> dict:
    res = json.loads((HERE / f'results_{name}.json').read_text(encoding='utf-8'))
    mw = json.loads((HERE / f'messwerte_{name}.json').read_text(encoding='utf-8'))
    cfg = json.loads((LOGS / name / 'pc2' / 'run_config.json').read_text(encoding='utf-8'))
    cp = cfg['crisis_params']
    eta_run = cp['p_cross'] if eta is None else eta
    d0, d_mean = cp['dur_min_s'], cp['dur_min_s'] + cp['dur_extra_mean_s']
    fpr_meas = [mw['FPR'][s] for s in SOURCES]
    c_art_used = mw['c_art_a_all4_time_share'] if c_art is None else c_art
    d_false_used = mw['d_false_s'] if d_false is None else d_false
    p_c_meas = mw['P_C']
    rows = {}
    for rule in RULES:
        r = res['rules'][rule]
        nom = false_escalations_g7(rule, NOMINAL['fpr'], NOMINAL['c_art'], NOMINAL['d_false'])
        meas = false_escalations_g7(rule, fpr_meas, c_art_used, d_false_used, p_c_meas)
        meas_mean = false_escalations_g7(rule, sum(fpr_meas) / 4, c_art_used, d_false_used, p_c_meas)
        t_nom = tpr_sys(rule, eta_run, d0, d_mean)
        t_meas = tpr_sys(rule, eta_run, d0, mw['d_true_s'])
        lo, hi = r['false_per_bed_h_ci']
        rows[rule] = {
            'observed_n_false': r['n_false'], 'observed_per_bed_h': r['false_per_bed_h'], 'observed_ci': [lo, hi],
            'g7_nominal': nom['per_bed_h'], 'monte_carlo': MONTE_CARLO[rule],
            'g7_measured': meas['per_bed_h'], 'g7_measured_mean_fpr': meas_mean['per_bed_h'],
            'g7_measured_transitions': meas['transitions_per_bed_h'], 'g7_measured_bursts': meas['bursts_per_bed_h'],
            'nominal_in_ci': lo <= nom['per_bed_h'] <= hi, 'monte_carlo_in_ci': lo <= MONTE_CARLO[rule] <= hi,
            'measured_in_ci': lo <= meas['per_bed_h'] <= hi,
            'tpr_model_nominal': t_nom['tpr_strict'], 'tpr_model_measured_d_true': t_meas['tpr_strict'],
            'caught_strict': r['caught_strict'], 'caught': r['caught'], 'n_crises': r['n_crises'],
            'caught_strict_frac': r['caught_strict_frac'], 'caught_strict_ci': r['caught_strict_frac_ci'],
            'caught_frac': r['caught_frac'], 'caught_ci': r['caught_frac_ci'],
            'tpr_in_ci': r['caught_strict_frac_ci'][0] <= t_nom['tpr_strict'] <= r['caught_strict_frac_ci'][1],
            'median_delay_s': r['median_delay_s'],
        }
    missed = res.get('checks', {}).get('missed_by_adaptiv', [])
    cr = {c['cid']: c for c in res['crises']}
    for m in missed:
        c = cr.get(m['cid'])
        if c is not None:
            m['t_start_pc1'] = c['t_start']
            m['t_start_local'] = dt.datetime.fromtimestamp(c['t_start']).strftime('%Y-%m-%d %H:%M:%S')
    return {'name': name, 'eta': eta_run, 'd0_s': d0, 'd_true_nominal_s': d_mean, 'd_true_measured_s': mw['d_true_s'],
            'fpr_nominal': NOMINAL['fpr'], 'fpr_measured': dict(zip(SOURCES, fpr_meas)),
            'c_art_nominal': NOMINAL['c_art'], 'c_art_used': c_art_used, 'c_art_measured_analyze_a': mw['c_art_a_all4_time_share'],
            'd_false_nominal_s': NOMINAL['d_false'], 'd_false_used_s': d_false_used, 'd_false_measured_analyze_s': mw['d_false_s'],
            'p_c_nominal': P_C_NOMINAL, 'p_c_measured': p_c_meas, 'exposure_bed_h': res['exposure_bed_h'],
            'rules': rows, 'missed_by_adaptiv': missed}


def write_md(cmp: dict) -> pathlib.Path:
    n = cmp['name']
    L = [f'# Modell (Kap. 3 / Anhang G) gegen Lauf {n}', '',
         f"Fenster {cmp['exposure_bed_h']:.1f} Bett·h; η = {cmp['eta']:g}; Krisendauer Soll d0 = {cmp['d0_s']:g} s, "
         f"Mittel {cmp['d_true_nominal_s']:g} s (gemessen {cmp['d_true_measured_s']:.1f} s). "
         'Formel G.7 (Zählung über Übergänge, gemeinsame Stöße c_art·FPR/d_false); für adaptiv obere Grenze (Fenster T nicht berücksichtigt). '
         'Monte-Carlo-Werte aus KONTEXT_Belastungsmodell_fuer_PC2.md, Abschnitt 4.', '',
         '## Falsche Eskalationen je Bett·h', '',
         f"Modell nominal: FPR {cmp['fpr_nominal']} je Kanal, c_art {cmp['c_art_nominal']}, d_false {cmp['d_false_nominal_s']:g} s, P(C) {cmp['p_c_nominal']:.4f}. "
         'Modell mit gemessenen Parametern: FPR ' + ', '.join(f"{s} {cmp['fpr_measured'][s]:.4f}" for s in SOURCES)
         + f", c_art {cmp['c_art_used']:.3f}, d_false {cmp['d_false_used_s']:.2f} s, P(C) {cmp['p_c_measured']:.4f}.", '',
         '| Regel | beobachtet n | beobachtet je Bett·h [95 %] | Modell G.7 nominal | Monte-Carlo | Modell G.7 gemessene Parameter | davon Übergänge / Stöße | nominal im Intervall | MC im Intervall | gemessen im Intervall |',
         '|---|---|---|---|---|---|---|---|---|---|']
    for rule, r in cmp['rules'].items():
        pre = '≤ ' if rule == 'adaptiv' else ''
        L.append(f"| {rule} | {r['observed_n_false']} | {fmt_ci(r['observed_per_bed_h'], r['observed_ci'])} | {pre}{r['g7_nominal']:.2f} | "
                 f"{r['monte_carlo']:.2f} | {pre}{r['g7_measured']:.2f} | {r['g7_measured_transitions']:.2f} / {r['g7_measured_bursts']:.2f} | "
                 f"{'ja' if r['nominal_in_ci'] else 'nein'} | {'ja' if r['monte_carlo_in_ci'] else 'nein'} | {'ja' if r['measured_in_ci'] else 'nein'} |")
    L += ['', '## Erkannte Krisen (streng: Eskalation beginnt vor dem Krisenende)', '',
          f"Modell: TPR_sys = Σ_F P(F|C)·P(D ≥ t(F)); jeder Kanal zeigt die Krise unabhängig mit η = {cmp['eta']:g} vom Beginn bis zum Ende; "
          f"D = d0 + Exp(d_true − d0) mit Soll-Mittel {cmp['d_true_nominal_s']:g} s bzw. gemessenem Mittel {cmp['d_true_measured_s']:.1f} s.", '',
          '| Regel | Modell (Soll-Dauer) | Modell (gemessene Dauer) | beobachtet streng [95 %] | beobachtet weich (Rot bis Ende + 10 s) | Median Latenz | Modell im Intervall |',
          '|---|---|---|---|---|---|---|']
    for rule, r in cmp['rules'].items():
        L.append(f"| {rule} | {100 * r['tpr_model_nominal']:.1f} % | {100 * r['tpr_model_measured_d_true']:.1f} % | "
                 f"{r['caught_strict']}/{r['n_crises']} = {100 * r['caught_strict_frac']:.1f} % [{100 * r['caught_strict_ci'][0]:.1f}; {100 * r['caught_strict_ci'][1]:.1f}] | "
                 f"{r['caught']}/{r['n_crises']} = {100 * r['caught_frac']:.1f} % | {r['median_delay_s']:.1f} s | {'ja' if r['tpr_in_ci'] else 'nein'} |")
    L += ['', '## Von adaptiv verpasste Krisen', '']
    if cmp['missed_by_adaptiv']:
        L += ['| Krise | Patient | Beginn (PC 1, lokal) | Klasse | Dauer s | Quellen über der Schwelle (t − Beginn) | 2-aus-4 | 3-aus-4 | nächstes Rot des Patienten |',
              '|---|---|---|---|---|---|---|---|---|']
        for m in cmp['missed_by_adaptiv']:
            cross = ', '.join(f"{s} {m['cross_rel_start'][s]:+.1f} s" for s in SOURCES if m['cross_rel_start'].get(s) is not None) or '—'
            L.append(f"| {m['cid']} | {m['patient']} | {m.get('t_start_local', '—')} | {m['klass']} | {m['duration_s']:.1f} | {cross} | "
                     f"{'ja' if m['caught_2'] else 'nein'} | {'ja' if m['caught_3'] else 'nein'} | {m['nearest_rot']} |")
    else:
        L.append('keine')
    L.append('')
    p = HERE / f'modell_{n}.md'
    p.write_text('\n'.join(L), encoding='utf-8')
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--name', help='run to compare (results_<name>.json, messwerte_<name>.json must exist)')
    ap.add_argument('--c-art', type=float, default=None, help='measured c_art to use (default: c_art_a of messwerte)')
    ap.add_argument('--d-false', type=float, default=None, help='measured d_false in s (default: messwerte)')
    ap.add_argument('--eta', type=float, default=None, help='eta of the model (default: p_cross of the run)')
    a = ap.parse_args(argv)
    print('reference values of the KONTEXT (FPR 0.026, c_art 0.10, d_false 8 s, eta 0.9, d0 15 s, d_true 30 s):')
    for rule, v in nominal_table().items():
        print(f"  {rule:8s} G.7 {v['g7_per_bed_h']:.2f} per bed*h   Monte Carlo {v['monte_carlo_per_bed_h']:.2f}   TPR_sys strict {100 * v['tpr_strict']:.1f} %")
    if not a.name:
        return 0
    cmp = compare_run(a.name, a.c_art, a.d_false, a.eta)
    (HERE / f'modell_{a.name}.json').write_text(json.dumps(cmp, indent=1, ensure_ascii=False), encoding='utf-8')
    p = write_md(cmp)
    print(f'\nrun {a.name}: eta {cmp["eta"]:g}, c_art used {cmp["c_art_used"]:.3f}, d_false used {cmp["d_false_used_s"]:.2f} s, P(C) {cmp["p_c_measured"]:.4f}')
    for rule, r in cmp['rules'].items():
        print(f"  {rule:8s} observed {r['observed_per_bed_h']:.2f} [{r['observed_ci'][0]:.2f}; {r['observed_ci'][1]:.2f}]  "
              f"G.7 nominal {r['g7_nominal']:.2f}  MC {r['monte_carlo']:.2f}  G.7 measured {r['g7_measured']:.2f}  |  "
              f"TPR model {100 * r['tpr_model_nominal']:.1f} % / {100 * r['tpr_model_measured_d_true']:.1f} %  observed strict "
              f"{r['caught_strict']}/{r['n_crises']} = {100 * r['caught_strict_frac']:.1f} %")
    print(f'written: {p.name}, modell_{a.name}.json')
    return 0


if __name__ == '__main__':
    sys.exit(main())
