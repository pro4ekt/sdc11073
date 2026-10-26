"""ziel2_analyze.py -- acknowledgement success rate, p95 RTT and fallback timing from Ziel 2 journals.

Input: one or more JSON Lines journals (ziel2_ack.py and/or ziel2_fault.py output).

Rules (experiments/ziel2/README.md):
  * success rate per TRANSACTION (``ack_rtt`` with purpose ``ack``); ``ok`` = InvocationState
    Fin / FinMod; several transactions of one bell press share a ``press_id``;
    ``timeout`` counts as a failure, a later ``ack_confirmed`` for the same (epr, handle) is
    reported separately as "late success";
  * p95 of ``rtt_ms`` over successful transactions only (nearest rank);
  * fallback timing: main value t_loss -> ``technical_alarm{raised}``, intermediate value
    t_loss -> ``fallback``; each compared with the bound of its loss type.

Usage:
    python experiments/ziel2/ziel2_analyze.py experiments/ziel2/logs/ziel2_ack_<ts>.jsonl \\
        experiments/ziel2/logs/ziel2_fault_<ts>.jsonl [--json out.json] [--per-trial]
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import statistics
import sys


# ── binomial bounds (copied from ziel1_analyze to keep the folders independent) ───────────

def _binom_cdf(k: int, n: int, p: float) -> float:
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 1.0 if k >= n else 0.0
    lp, lq = math.log(p), math.log1p(-p)
    return min(1.0, sum(math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
                                 + i * lp + (n - i) * lq) for i in range(0, k + 1)))


def cp_lower(k: int, n: int, alpha: float) -> float:
    if n == 0:
        return float('nan')
    if k == 0:
        return 0.0
    if k == n:
        return alpha ** (1.0 / n)
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if 1.0 - _binom_cdf(k - 1, n, mid) < alpha:
            lo = mid
        else:
            hi = mid
    return lo


def p95(values: list[float]) -> float | None:
    v = sorted(values)
    return v[min(len(v) - 1, int(round(0.95 * len(v) + 0.5)) - 1)] if v else None


def read(paths: list[pathlib.Path]) -> list[dict]:
    out = []
    for p in paths:
        with p.open(encoding='utf-8') as fh:
            for ln, line in enumerate(fh, 1):
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        print(f'warning: {p}:{ln}: {exc}', file=sys.stderr)
    return out


# ── acknowledgement ───────────────────────────────────────────────────────────────────────

def analyse_ack(recs: list[dict], alpha: float) -> dict | None:
    rtts = [r for r in recs if r.get('event') == 'ack_rtt' and r.get('purpose', 'ack') == 'ack']
    if not rtts:
        return None
    starts = {r['trial']: r for r in recs if r.get('event') == 'ack_trial_start'}
    ends = {r['trial']: r for r in recs if r.get('event') == 'ack_trial_end'}
    confirmed = [r for r in recs if r.get('event') == 'ack_confirmed']
    # policy per press_id (from trial_end), default 'accept'
    policy_of_press = {e.get('press_id'): e.get('policy') for e in ends.values() if e.get('press_id')}

    by_outcome = collections.Counter(r.get('outcome') for r in rtts)
    by_state = collections.Counter(r.get('invocation_state') for r in rtts)
    presses = collections.defaultdict(list)
    for r in rtts:
        presses[r.get('press_id')].append(r)

    # late success: timeout transaction followed by ack_confirmed for the same (epr, handle)
    late = 0
    for r in rtts:
        if r.get('outcome') == 'timeout':
            t_req = r.get('t_request_abs') or r['t_abs']
            if any(c.get('epr') == r.get('epr') and c.get('handle') == r.get('handle')
                   and c['t_abs'] >= t_req for c in confirmed):
                late += 1

    def block(policy: str | None) -> dict:
        tx = [r for r in rtts if policy is None or policy_of_press.get(r.get('press_id'), 'accept') == policy]
        ok = [r for r in tx if r.get('ok')]
        rtt_ok = [float(r['rtt_ms']) for r in ok if r.get('rtt_ms') is not None]
        return {
            'transactions': len(tx), 'ok': len(ok), 'failed': len(tx) - len(ok),
            'success_rate': (len(ok) / len(tx)) if tx else None,
            'success_lower_95_one_sided': cp_lower(len(ok), len(tx), alpha) if tx else None,
            'by_outcome': dict(collections.Counter(r.get('outcome') for r in tx)),
            'by_state': dict(collections.Counter(r.get('invocation_state') for r in tx)),
            'rtt_ms_p95': p95(rtt_ok), 'rtt_ms_median': statistics.median(rtt_ok) if rtt_ok else None,
            'rtt_ms_max': max(rtt_ok) if rtt_ok else None, 'rtt_ms_min': min(rtt_ok) if rtt_ok else None,
            'presses': len({r.get('press_id') for r in tx}),
        }

    conf_lat = [float(e['confirm_latency_ms']) for e in ends.values() if e.get('confirm_latency_ms') is not None]
    acc_trials = [e for e in ends.values() if e.get('policy') == 'accept']
    ref_trials = [e for e in ends.values() if e.get('policy') == 'refuse']
    return {
        'all': block(None), 'accept': block('accept'), 'refuse': block('refuse'),
        'by_outcome': dict(by_outcome), 'by_invocation_state': dict(by_state),
        'late_success': late, 'ack_confirmed_events': len(confirmed),
        'trials': len(ends), 'trials_accept': len(acc_trials), 'trials_refuse': len(ref_trials),
        'accept_confirmed': sum(1 for e in acc_trials if e.get('confirmed')),
        'accept_state_counts': dict(collections.Counter(e.get('ack_state') for e in acc_trials)),
        'refuse_state_counts': dict(collections.Counter(e.get('ack_state') for e in ref_trials)),
        'confirm_latency_ms_p95': p95(conf_lat), 'confirm_latency_ms_median': statistics.median(conf_lat) if conf_lat else None,
        'confirm_latency_ms_max': max(conf_lat) if conf_lat else None,
        'presses_multi_tx': sum(1 for v in presses.values() if len(v) > 1),
    }


# ── fault injection ───────────────────────────────────────────────────────────────────────

def analyse_fault(recs: list[dict], alpha: float) -> dict | None:
    trials = [r for r in recs if r.get('event') == 'fault_trial_end']
    if not trials:
        return None
    out: dict = {'methods': {}, 'trials': len(trials)}
    for method in sorted({t['method'] for t in trials}):
        ts_ = [t for t in trials if t['method'] == method]
        okt = [t for t in ts_ if t.get('dt_fallback_s') is not None]
        fb = [float(t['dt_fallback_s']) for t in okt]
        al = [float(t['dt_alarm_s']) for t in okt if t.get('dt_alarm_s') is not None]
        dp = [float(t['dt_departed_s']) for t in okt if t.get('dt_departed_s') is not None]
        bound = next((t.get('bound_s') for t in ts_ if t.get('bound_s') is not None), None)
        within = sum(1 for t in okt if bound is None or (t.get('dt_alarm_s') is not None and t['dt_alarm_s'] <= bound))
        n = len(okt)
        m = {
            'n': len(ts_), 'measured': n, 'outcomes': dict(collections.Counter(t.get('outcome') for t in ts_)),
            'causes': dict(collections.Counter(t.get('cause') for t in okt)),
            'bound_s': bound,
            'fallback_s': {'median': statistics.median(fb) if fb else None, 'p95': p95(fb), 'max': max(fb) if fb else None},
            'alarm_s': {'median': statistics.median(al) if al else None, 'p95': p95(al), 'max': max(al) if al else None},
            'departed_s': {'median': statistics.median(dp) if dp else None, 'max': max(dp) if dp else None},
            'within_bound': within if bound is not None else None,
            'within_bound_lower_95_one_sided': cp_lower(within, n, alpha) if (bound is not None and n) else None,
            'over_bound_trials': [t['trial'] for t in okt if bound is not None and t.get('dt_alarm_s') is not None and t['dt_alarm_s'] > bound],
        }
        if method == 'reconnect':
            rc = [float(t['dt_reconnect_s']) for t in ts_ if t.get('dt_reconnect_s') is not None]
            m['reconnect_s'] = {'n': len(rc), 'median': statistics.median(rc) if rc else None, 'max': max(rc) if rc else None,
                                'ok': sum(1 for t in ts_ if t.get('reconnect_ok'))}
        out['methods'][method] = m
    return out


# ── report ────────────────────────────────────────────────────────────────────────────────

def f(x, unit='', nd=1):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return '-'
    return f'{x:.{nd}f}{unit}' if isinstance(x, float) else f'{x}{unit}'


def pct(x):
    return '-' if x is None or (isinstance(x, float) and math.isnan(x)) else f'{100 * x:.2f} %'


def print_report(ack: dict | None, fault: dict | None, trials_list: list[dict], per_trial: bool) -> None:
    if ack:
        print('ACKNOWLEDGEMENT (Ziel 2, SetAlertState)')
        for name in ('all', 'accept', 'refuse'):
            b = ack[name]
            if not b['transactions']:
                continue
            print(f"  [{name}] transactions={b['transactions']} (presses={b['presses']})  ok={b['ok']}  failed={b['failed']}"
                  f"  success={pct(b['success_rate'])}  95% lower={pct(b['success_lower_95_one_sided'])}")
            print(f"        outcomes={b['by_outcome']}  states={b['by_state']}")
            print(f"        RTT ok: median {f(b['rtt_ms_median'],' ms')}  p95 {f(b['rtt_ms_p95'],' ms')}  "
                  f"max {f(b['rtt_ms_max'],' ms')}  min {f(b['rtt_ms_min'],' ms')}")
        print(f"  accept trials: {ack['trials_accept']} states={ack['accept_state_counts']} confirmed={ack['accept_confirmed']}")
        print(f"  refuse trials: {ack['trials_refuse']} states={ack['refuse_state_counts']}")
        print(f"  late success (timeout then ack_confirmed): {ack['late_success']};  presses with >1 transaction: {ack['presses_multi_tx']}")
        print(f"  request -> Presence=Ack report: median {f(ack['confirm_latency_ms_median'],' ms')}  "
              f"p95 {f(ack['confirm_latency_ms_p95'],' ms')}  max {f(ack['confirm_latency_ms_max'],' ms')}")
        n = ack['accept']['transactions']
        need = math.ceil(math.log(0.05) / math.log(0.99))
        print(f"  note: success >= 99 % at 95 % needs >= {need} accept transactions with 0 failures (have {n})")
    if fault:
        print('\nFAULT INJECTION (Ziel 2, fallback entry)')
        for method, m in fault['methods'].items():
            print(f"  [{method}] n={m['n']} measured={m['measured']} outcomes={m['outcomes']} causes={m['causes']}")
            print(f"        loss -> fallback:        median {f(m['fallback_s']['median'],' s')}  p95 {f(m['fallback_s']['p95'],' s')}  max {f(m['fallback_s']['max'],' s')}")
            print(f"        loss -> technical alarm: median {f(m['alarm_s']['median'],' s')}  p95 {f(m['alarm_s']['p95'],' s')}  max {f(m['alarm_s']['max'],' s')}"
                  + (f"   bound {m['bound_s']} s: within {m['within_bound']}/{m['measured']}  95% lower={pct(m['within_bound_lower_95_one_sided'])}"
                     if m['bound_s'] is not None else ''))
            print(f"        loss -> device removed:  median {f(m['departed_s']['median'],' s')}  max {f(m['departed_s']['max'],' s')}")
            if m['over_bound_trials']:
                print(f"        ! over bound: trials {m['over_bound_trials']}")
            if 'reconnect_s' in m:
                r = m['reconnect_s']
                print(f"        restart -> alarm cleared: n={r['n']} ok={r['ok']} median {f(r['median'],' s')} max {f(r['max'],' s')}")
    if per_trial and trials_list:
        print('\nPer fault trial:')
        for t in trials_list:
            print(f"  {t['trial']:4d} {t['method']:9s} {t.get('outcome'):10s} cause={t.get('cause')} "
                  f"fallback={t.get('dt_fallback_s')} alarm={t.get('dt_alarm_s')} departed={t.get('dt_departed_s')}"
                  + (f" reconnect={t.get('dt_reconnect_s')}" if t.get('method') == 'reconnect' else ''))


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    ap = argparse.ArgumentParser(description='Ziel 2 analyzer')
    ap.add_argument('journals', nargs='+', type=pathlib.Path)
    ap.add_argument('--json', type=pathlib.Path)
    ap.add_argument('--per-trial', action='store_true')
    ap.add_argument('--confidence', type=float, default=0.95)
    a = ap.parse_args(argv)
    recs = read(a.journals)
    alpha = 1 - a.confidence
    ack = analyse_ack(recs, alpha)
    fault = analyse_fault(recs, alpha)
    print(f"Journals: {', '.join(str(p) for p in a.journals)}   records={len(recs)}")
    if not ack and not fault:
        print('no ack_rtt and no fault_trial_end records found')
        return 1
    print_report(ack, fault, [r for r in recs if r.get('event') == 'fault_trial_end'], a.per_trial)
    if a.json:
        a.json.write_text(json.dumps({'journals': [str(p) for p in a.journals], 'ack': ack, 'fault': fault},
                                     indent=2, default=str), encoding='utf-8')
        print(f'\nJSON summary: {a.json}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
