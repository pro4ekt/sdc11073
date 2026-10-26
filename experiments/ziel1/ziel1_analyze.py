"""ziel1_analyze.py -- Topology Rejection Accuracy and avoided subscriptions from a Ziel 1 journal.

Input: one JSON Lines journal written by Dashboard (``--event-log`` or ziel1_experiment.py).
Ground truth per endpoint reference comes from the ``trial_start`` records of the runner
(role); journals without them (e.g. Dashboard/logs/ziel1/*.jsonl) are analysed by decision
only.

Rules (experiments/ziel1/README.md):
  * the LAST ``topology_decision`` per EPR is the final decision of that connection attempt;
  * bettfremde trials = roles foreign + mismatch;
    TRA = correctly rejected bettfremde trials / all bettfremde trials;
    correct = final decision ``reject`` AND no ``subscription_created`` AND (if known)
    provider-side ``subscriptions = 0``;
  * ``nolocation`` (decision ``unknown``) is excluded from the denominator and reported
    separately; false rejects of ``own`` providers are reported separately;
  * avoided subscriptions = ``subscription_avoided`` records; with ``--baseline`` the
    difference of ``subscription_created`` between the baseline and the filtered run.

Usage:
    python experiments/ziel1/ziel1_analyze.py experiments/ziel1/logs/ziel1_filtered_<ts>.jsonl
        [--baseline experiments/ziel1/logs/ziel1_baseline_<ts>.jsonl] [--json out.json] [--per-trial]
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import sys

BETTFREMD = ('foreign', 'mismatch')


# ──────────────────────────────────────────────────────────────────────────────
# Binomial confidence bounds (no scipy)
# ──────────────────────────────────────────────────────────────────────────────

def _binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Bin(n, p), computed in log space."""
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 1.0 if k >= n else 0.0
    total = 0.0
    lp, lq = math.log(p), math.log1p(-p)
    for i in range(0, k + 1):
        total += math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
                          + i * lp + (n - i) * lq)
    return min(1.0, total)


def clopper_pearson_lower(k: int, n: int, alpha: float) -> float:
    """Lower bound p_L with P(X >= k | p_L) = alpha  (one-sided; alpha/2 for two-sided)."""
    if n == 0:
        return float('nan')
    if k == 0:
        return 0.0
    if k == n:
        return alpha ** (1.0 / n)
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        # P(X >= k) = 1 - P(X <= k-1)
        if 1.0 - _binom_cdf(k - 1, n, mid) < alpha:
            lo = mid
        else:
            hi = mid
    return lo


# ──────────────────────────────────────────────────────────────────────────────
# Journal reading
# ──────────────────────────────────────────────────────────────────────────────

def read_journal(path: pathlib.Path) -> list[dict]:
    out = []
    with path.open(encoding='utf-8') as fh:
        for ln, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f'warning: {path}:{ln}: {exc}', file=sys.stderr)
    return out


def per_epr(records: list[dict]) -> dict[str, dict]:
    """Collect everything the analysis needs per endpoint reference."""
    eprs: dict[str, dict] = {}

    def slot(epr: str) -> dict:
        return eprs.setdefault(epr, {
            'epr': epr, 'role': None, 'trial': None, 'expected': None, 'mode': None,
            'discovered': None, 'decisions': [], 'created': 0, 'actions': 0, 'avoided': 0,
            'scope_mismatch': 0, 'provider_subscriptions': None, 'outcome': None,
            't_first': None, 't_last': None,
        })

    for r in records:
        epr = r.get('epr')
        ev = r.get('event')
        if not epr:
            continue
        s = slot(epr)
        t = r.get('t_abs')
        if t is not None:
            s['t_first'] = t if s['t_first'] is None else min(s['t_first'], t)
            s['t_last'] = t if s['t_last'] is None else max(s['t_last'], t)
        if ev == 'trial_start':
            s['role'] = r.get('role')
            s['trial'] = r.get('trial')
            s['expected'] = r.get('expected')
            s['mode'] = r.get('mode')
        elif ev == 'trial_end':
            s['provider_subscriptions'] = r.get('provider_subscriptions')
            s['outcome'] = r.get('outcome')
        elif ev == 'device_discovered':
            s['discovered'] = r.get('room')
        elif ev == 'topology_decision':
            s['decisions'].append((r.get('decision'), r.get('phase'), r.get('reason')))
        elif ev == 'subscription_created':
            s['created'] += 1
            s['actions'] += int(r.get('n_actions') or 0)
        elif ev == 'subscription_avoided':
            s['avoided'] += 1
        elif ev == 'scope_mismatch':
            s['scope_mismatch'] += 1
    for s in eprs.values():
        s['final'] = s['decisions'][-1] if s['decisions'] else (None, None, None)
    return eprs


# ──────────────────────────────────────────────────────────────────────────────
# Verdict per trial
# ──────────────────────────────────────────────────────────────────────────────

def verdict(s: dict, mode: str) -> tuple[bool | None, str]:
    """(correct, note) for one EPR with a known role; (None, ..) without role."""
    role = s['role']
    dec, phase, reason = s['final']
    created = s['created']
    psubs = s['provider_subscriptions']
    if role is None:
        return None, 'no role (journal without trial_start)'
    if mode == 'baseline':
        ok = dec == 'accept' and created >= 1 and (psubs is None or psubs >= 1)
        return ok, 'baseline: accepted and subscribed' if ok else f'baseline: dec={dec} created={created} subs={psubs}'
    if role in BETTFREMD:
        ok = dec == 'reject' and created == 0 and (psubs is None or psubs == 0)
        if ok:
            if role == 'foreign' and phase != 'discovery':
                # correct for TRA, but the discovery path was missed: the first Hello
                # carried no location scope (provider announced before its context
                # was set), so a TLS connection + GetContextStates were spent.
                return True, f'LATE: rejected @{phase} ({reason}) - connection made, scope path missed'
            return True, f'rejected @{phase} ({reason})'
        if dec == 'reject' and (created > 0 or (psubs or 0) > 0):
            return False, f'rejected but subscribed (created={created}, provider={psubs})'
        return False, f'NOT rejected: dec={dec} phase={phase} reason={reason} created={created}'
    if role == 'own':
        ok = dec == 'accept' and created >= 1
        return ok, ('accepted, subscribed' if ok else f'dec={dec} phase={phase} reason={reason} created={created}')
    if role == 'nolocation':
        ok = dec == 'unknown' and created >= 1
        return ok, ('unknown, subscribed' if ok else f'dec={dec} phase={phase} reason={reason} created={created}')
    return None, f'unknown role {role!r}'


def analyse(path: pathlib.Path, alpha: float) -> dict:
    records = read_journal(path)
    exp = next((r for r in records if r.get('event') == 'experiment_start'), None)
    eprs = per_epr(records)
    mode = (exp or {}).get('mode') or next((s['mode'] for s in eprs.values() if s['mode']), None)
    if mode is None:
        # infer: any no_filter decision -> baseline
        mode = 'baseline' if any(d[1] == 'no_filter' for s in eprs.values() for d in s['decisions']) else 'filtered'

    by_role: dict[str, dict] = collections.defaultdict(
        lambda: {'n': 0, 'correct': 0, 'wrong': 0, 'late': 0, 'final_phase': collections.Counter(), 'notes': []})
    decisions_by_phase: collections.Counter = collections.Counter()
    n_created = n_avoided = n_actions = 0
    untagged = 0
    for s in eprs.values():
        n_created += s['created']
        n_avoided += s['avoided']
        n_actions += s['actions']
        for d in s['decisions']:
            decisions_by_phase[f'{d[0]}@{d[1]}:{d[2]}'] += 1
        ok, note = verdict(s, mode)
        if ok is None:
            untagged += 1
            continue
        b = by_role[s['role']]
        b['n'] += 1
        b['correct' if ok else 'wrong'] += 1
        b['final_phase'][f"{s['final'][0]}@{s['final'][1]}"] += 1
        if ok and note.startswith('LATE'):
            b['late'] += 1
        if not ok or note.startswith('LATE'):
            b['notes'].append(f"trial {s['trial']} ..{s['epr'][-12:]}: {note}")

    n_bf = sum(by_role[r]['n'] for r in BETTFREMD if r in by_role)
    k_bf = sum(by_role[r]['correct'] for r in BETTFREMD if r in by_role)
    tra = (k_bf / n_bf) if n_bf else None
    provider_checked = sum(1 for s in eprs.values() if s['provider_subscriptions'] is not None)
    provider_disagree = [
        f"trial {s['trial']} ..{s['epr'][-12:]}: journal created={s['created']} vs provider subscriptions={s['provider_subscriptions']}"
        for s in eprs.values()
        if s['provider_subscriptions'] is not None
        and ((s['created'] > 0) != (s['provider_subscriptions'] > 0))
    ]
    return {
        'journal': str(path), 'mode': mode, 'records': len(records), 'eprs': len(eprs),
        'untagged_eprs': untagged,
        'by_role': {r: {k: (dict(v) if isinstance(v, collections.Counter) else v) for k, v in b.items()}
                    for r, b in by_role.items()},
        'bettfremd_n': n_bf, 'bettfremd_correct': k_bf, 'tra': tra,
        'tra_lower_95_one_sided': clopper_pearson_lower(k_bf, n_bf, 1 - 0.95) if n_bf else None,
        'tra_lower_95_two_sided': clopper_pearson_lower(k_bf, n_bf, (1 - 0.95) / 2) if n_bf else None,
        'false_rejects_own': by_role['own']['wrong'] if 'own' in by_role else 0,
        'nolocation_n': by_role['nolocation']['n'] if 'nolocation' in by_role else 0,
        'subscriptions_created': n_created, 'actions_subscribed': n_actions,
        'subscriptions_avoided': n_avoided,
        'provider_status_checked': provider_checked, 'provider_disagreements': provider_disagree,
        'decisions_by_phase': dict(decisions_by_phase),
        'alpha': alpha,
        'per_epr': eprs,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Report
# ──────────────────────────────────────────────────────────────────────────────

def fmt_pct(x: float | None) -> str:
    return '-' if x is None or (isinstance(x, float) and math.isnan(x)) else f'{100 * x:.2f} %'


def print_report(res: dict, baseline: dict | None, per_trial: bool) -> None:
    print(f"Journal : {res['journal']}")
    print(f"Mode    : {res['mode']}   records={res['records']}   EPRs={res['eprs']}"
          + (f"   (without role: {res['untagged_eprs']})" if res['untagged_eprs'] else ''))
    print('\nDecisions (decision@phase:reason -> count):')
    for k, v in sorted(res['decisions_by_phase'].items()):
        print(f'  {k:50s} {v}')
    if res['by_role']:
        print('\nPer role:')
        for role, b in res['by_role'].items():
            phases = ', '.join(f'{k}={v}' for k, v in sorted(b['final_phase'].items()))
            late = f"  late(precheck)={b['late']}" if b.get('late') else ''
            print(f"  {role:10s} n={b['n']:4d}  correct={b['correct']:4d}  wrong={b['wrong']:4d}{late}   final: {phases}")
            for note in b['notes']:
                print(f'      ! {note}')
        if res['mode'] == 'filtered' and res['by_role'].get('foreign', {}).get('late'):
            print("  note: 'late' foreign rejections count as correct for TRA but missed the discovery path "
                  "(a connection was made); see the provider start-up order.")
    if res['mode'] == 'filtered' and res['bettfremd_n']:
        print('\nTopology Rejection Accuracy (bettfremd = foreign + mismatch):')
        print(f"  TRA = {res['bettfremd_correct']} / {res['bettfremd_n']} = {fmt_pct(res['tra'])}")
        print(f"  95 % lower bound (one-sided, rule-of-three family): {fmt_pct(res['tra_lower_95_one_sided'])}")
        print(f"  95 % lower bound (two-sided Clopper-Pearson):        {fmt_pct(res['tra_lower_95_two_sided'])}")
        print(f"  false rejects of own-room providers: {res['false_rejects_own']}   "
              f"nolocation (unknown, excluded): {res['nolocation_n']}")
        need = math.ceil(math.log(0.05) / math.log(0.99))
        print(f'  note: TRA >= 99 % at 95 % needs >= {need} bettfremde trials with 0 misses')
    print('\nSubscriptions (journal):')
    print(f"  created={res['subscriptions_created']}  (actions={res['actions_subscribed']})   "
          f"avoided={res['subscriptions_avoided']}")
    print(f"  provider-side status checked for {res['provider_status_checked']} trial(s)"
          + (';  DISAGREEMENTS:' if res['provider_disagreements'] else ';  no disagreement'))
    for d in res['provider_disagreements']:
        print(f'      ! {d}')
    if baseline:
        print('\nBaseline comparison:')
        print(f"  baseline : created={baseline['subscriptions_created']} over {baseline['eprs']} EPR(s)")
        print(f"  filtered : created={res['subscriptions_created']} over {res['eprs']} EPR(s), "
              f"avoided={res['subscriptions_avoided']}")
        bf_base = sum(baseline['by_role'].get(r, {}).get('n', 0) for r in BETTFREMD)
        if bf_base:
            print(f"  per bettfremd provider: baseline 1 subscription each ({bf_base} trial(s)), "
                  f"filtered 0 - avoided subscriptions = {res['subscriptions_avoided']} of "
                  f"{res['bettfremd_n']} bettfremde trials")
    if per_trial:
        print('\nPer EPR:')
        for s in sorted(res['per_epr'].values(), key=lambda x: (x['trial'] or 0, x['t_first'] or 0)):
            print(f"  {str(s['trial'] or '-'):>4} {str(s['role'] or '-'):10s} ..{s['epr'][-12:]} "
                  f"scope_room={s['discovered']!r:10} final={s['final']} created={s['created']} "
                  f"avoided={s['avoided']} provider={s['provider_subscriptions']} outcome={s['outcome']}")


def main(argv: list[str] | None = None) -> int:
    try:  # Windows consoles default to cp1252; keep the report printable anywhere
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    ap = argparse.ArgumentParser(description='Ziel 1 analyzer (TRA, avoided subscriptions)')
    ap.add_argument('journal', type=pathlib.Path)
    ap.add_argument('--baseline', type=pathlib.Path, help='journal of the unfiltered run for the traffic comparison')
    ap.add_argument('--json', type=pathlib.Path, help='write the machine-readable summary here')
    ap.add_argument('--per-trial', action='store_true', help='list every EPR')
    ap.add_argument('--confidence', type=float, default=0.95)
    a = ap.parse_args(argv)
    res = analyse(a.journal, 1 - a.confidence)
    base = analyse(a.baseline, 1 - a.confidence) if a.baseline else None
    print_report(res, base, a.per_trial)
    if a.json:
        out = {k: v for k, v in res.items() if k != 'per_epr'}
        out['per_epr'] = {e: {k: v for k, v in s.items()} for e, s in res['per_epr'].items()}
        if base:
            out['baseline'] = {k: v for k, v in base.items() if k != 'per_epr'}
        a.json.write_text(json.dumps(out, indent=2, default=str), encoding='utf-8')
        print(f'\nJSON summary: {a.json}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
