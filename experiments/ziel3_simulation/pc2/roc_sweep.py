r"""roc_sweep.py -- threshold sweep of the generator "prozess" in model time (no network, no providers).

Re-runs the scenario of a finished network run (same seed, patients, hours, crisis and patient parameters) for a
grid of threshold scales h and writes, per h, journals in the format of the network run:

    logs/<out>/h_<h>/alarms.jsonl     journal B: run_config, provider_ready, run_start, alarm ON/OFF, run_end
    logs/<out>/h_<h>/truth.jsonl      journal A: run_config, run_start, crisis_start/crisis_end, movement, run_end
    logs/<out>/h_<h>/run_config.json  run_config of the network run + threshold_scale, grid, source run

plus a copy of truth.jsonl / alarms.jsonl / run_config.json of the network run in logs/<out>/netz_<run>/ and a
sweep_summary.json.  The random numbers are the same for every h (thresholds do not feed back into the
processes), so the alarm sets differ only through h; t_abs = t_start_abs of the network run + model time.
Check: for h = 1.0 the alarm counts per channel must agree with journal B of the network run within +-1 %
(and the crisis ids must be identical); otherwise the sweep stops with exit code 2.

Option --hours H runs the scenario for H hours of model time instead of the hours of the network run (same
seed, so the first hours are identical to the network run); the check then compares the alarms within the span of
the network run and requires its crisis ids to be a subset of the offline ones.

Option --seed S takes the scenario (patients, parameters, p_cross, crisis durations) of --net-run but draws new
random numbers from seed S: an independent validation data set without a network run.  No network journals are
copied and the h = 1.0 check is skipped (there is nothing to compare with); t_abs still counts from t_start_abs of
the source run so that the journals keep their format.

    python roc_sweep.py --net-run ziel3_12h --out ziel3_roc [--grid 0.1,0.2,...,1.0,...]
    python roc_sweep.py --net-run ziel3_4h_p07 --out ziel3_roc_p07 --hours 12
    python roc_sweep.py --net-run ziel3_12h --out ziel3_roc_val09 --seed 8
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import pathlib
import platform
import shutil
import socket
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from patient import (AlarmEvent, MovementEvent, Patient, PatientParams, SOURCES, format_value,  # noqa: E402
                     make_seed_sequences, scaled_thresholds)
from szenario import CrisisParams, Szenario  # noqa: E402

LOGS = HERE.parent / 'logs'
DEFAULT_GRID = '0.05,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,1.0,1.2,1.4,1.7,2.2'   # FPR of one device ~0.45 ... ~0.002
CHECK_TOL = 0.01


class ModelJournal:
    """JSON Lines journal in the format of the runner's journals, but on the model clock:
    t_abs = t0_abs + t_model (set ``t_model`` before emitting)."""

    def __init__(self, path: pathlib.Path, t0_abs: float):
        self.path = path
        self.t0_abs = t0_abs
        self.t_model = 0.0
        self.seq = 0
        self._fh = path.open('w', encoding='utf-8')

    def emit(self, event: str, **fields) -> dict:
        t_abs = self.t0_abs + self.t_model
        rec = {'seq': self.seq, 't_abs': t_abs,
               'iso': _dt.datetime.fromtimestamp(t_abs).isoformat(timespec='milliseconds'), 'event': event}
        rec.update(fields)
        self.seq += 1
        self._fh.write(json.dumps(rec, default=str) + '\n')
        return rec

    def close(self) -> None:
        self._fh.close()


def read_jsonl(path: pathlib.Path):
    with path.open(encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_net_run(name: str) -> dict:
    run_dir = LOGS / name / 'pc2'
    cfg = json.loads((run_dir / 'run_config.json').read_text(encoding='utf-8'))
    if cfg.get('generator', 'prozess') != 'prozess':
        raise SystemExit(f'{name}: generator {cfg.get("generator")!r} is not "prozess"')
    t0 = None
    on_counts = collections.Counter()
    for r in read_jsonl(run_dir / 'alarms.jsonl'):
        if r['event'] == 'run_start':
            t0 = r['t_start_abs']
        elif r['event'] == 'alarm' and r['on']:
            on_counts[r['source']] += 1
    crisis_ids = sorted(r['crisis_id'] for r in read_jsonl(run_dir / 'truth.jsonl') if r['event'] == 'crisis_end')
    if t0 is None:
        raise SystemExit(f'{name}: no run_start in alarms.jsonl')
    return {'name': name, 'dir': run_dir, 'config': cfg, 't0_abs': t0, 'on_counts': dict(on_counts), 'crisis_ids': crisis_ids}


def apply_seed(net: dict, seed: int) -> dict:
    """Same scenario as the network run, new random numbers: copy of ``net`` with the seed replaced; the
    network counts are dropped (no check possible) and the original seed is kept as ``seed_source_run``."""
    out = dict(net)
    out['config'] = dict(net['config'], seed=int(seed))
    out['seed_source_run'] = net['config']['seed']
    out['seed_override'] = True
    out['on_counts'] = {}
    out['crisis_ids'] = []
    return out


def params_from_config(cfg: dict, h: float) -> tuple[PatientParams, CrisisParams]:
    pp = {k: (tuple(v) if isinstance(v, list) else v) for k, v in cfg['patient_params'].items()}
    pp['threshold_scale'] = h
    cp = {k: (tuple(v) if isinstance(v, list) else v) for k, v in cfg['crisis_params'].items()}
    return PatientParams(**pp), CrisisParams(**cp)


def sweep_one(net: dict, h: float, out_dir: pathlib.Path, grid: list[float], progress: bool = True,
              hours: float | None = None) -> dict:
    cfg = net['config']
    n = cfg['patients']
    hours_net = float(cfg['hours'])
    hours = hours_net if hours is None else float(hours)
    steps = int(round(hours * 3600.0))
    net_steps = int(round(hours_net * 3600.0))
    partial = steps != net_steps
    seed = cfg['seed']
    t0 = net['t0_abs']
    pparams, cparams = params_from_config(cfg, h)
    folder = out_dir / f'h_{h:.2f}'
    folder.mkdir(parents=True, exist_ok=True)
    prov = {(p['patient_index'], p['j']): p for p in cfg['providers']}
    run_cfg = dict(cfg)
    run_cfg.update({
        'name': f'{out_dir.name}/h_{h:.2f}', 'threshold_scale': h, 'generator': 'prozess', 'offline': True,
        'hours': hours,
        'patient_params': pparams.to_dict(), 'crisis_params': cparams.to_dict(),
        'sources': [dict(s_cfg, on_h=scaled_thresholds(s, h)[0], off_h=scaled_thresholds(s, h)[1])
                    for s_cfg, s in zip(cfg['sources'], SOURCES)],
        'roc': {'source_run': net['name'], 'source_run_dir': str(net['dir']), 't0_abs': t0, 'grid': grid,
                'hours_source_run': hours_net, 'hours': hours,
                'seed': seed, 'seed_source_run': net.get('seed_source_run', seed), 'seed_override': bool(net.get('seed_override', False)),
                'definition': 'alarm at x >= h (x = dimensionless sum, 1.0 = contract threshold), OFF at the contract '
                              'OFF margin scaled by h, minimum ON 2 s; identical random numbers for every h; '
                              't_abs = t_start_abs of the source run + model time'
                              + ('; model time runs longer than the source run (same seed: its span is identical)' if partial else '')},
        'run_dir': str(folder), 'created': _dt.datetime.now().isoformat(timespec='seconds'),
        'host': socket.gethostname(), 'python': platform.python_version(),
    })
    (folder / 'run_config.json').write_text(json.dumps(run_cfg, indent=2, ensure_ascii=False), encoding='utf-8')

    seqs, sseq = make_seed_sequences(seed, n)
    patients = [Patient(i + 1, seqs[i], pparams) for i in range(n)]
    truth = ModelJournal(folder / 'truth.jsonl', t0)
    alarms = ModelJournal(folder / 'alarms.jsonl', t0)
    for j in (truth, alarms):
        j.t_model = -1.0
        j.emit('run_config', **run_cfg)
    for pt in patients:
        for s in SOURCES:
            p = prov[(pt.index, s.j)]
            alarms.emit('provider_ready', patient=pt.patient_id, patient_index=pt.index, source=s.key, j=s.j,
                        channel=s.channel, port=p['port'], epr=p['epr'], after_s=0.0, offline=True)
    truth.t_model = alarms.t_model = 0.0
    alarms.emit('run_start', t_start_abs=t0, providers=4 * n)
    truth.emit('run_start', t_start_abs=t0)
    sz = Szenario(patients, sseq, truth, cparams, t0_abs=t0)

    counts = collections.Counter()
    counts_span = collections.Counter()                 # alarms ON inside the span of the network run
    n_off = 0
    t_wall = time.time()
    for k in range(steps):
        t = float(k)
        truth.t_model = alarms.t_model = t
        for pt in patients:
            for ev in pt.step(t):
                if isinstance(ev, AlarmEvent):
                    s = SOURCES[ev.j - 1]
                    p = prov[(pt.index, ev.j)]
                    alarms.emit('alarm', patient=pt.patient_id, patient_index=pt.index, source=s.key, j=s.j,
                                channel=s.channel, port=p['port'], epr=p['epr'], on=ev.on,
                                t_prov_ms=int(round((t0 + t) * 1000.0)), t_prov_abs=t0 + t, t_cmd_abs=t0 + t,
                                value=format_value(ev.value, s.decimals), latency_ms=0.0, t_model=t)
                    if ev.on:
                        counts[s.key] += 1
                        if k < net_steps:
                            counts_span[s.key] += 1
                    else:
                        n_off += 1
                elif isinstance(ev, MovementEvent):
                    truth.emit('movement', patient=pt.patient_id, patient_index=pt.index, t_model=t,
                               duration_s=round(ev.duration_s, 2), gains=[round(g, 3) for g in ev.gains])
        sz.step(t)
        if progress and k % 7200 == 7199:
            print(f'    h={h:.2f}: {(k + 1) / 3600.0:.0f}/{hours:g} h ({time.time() - t_wall:.0f} s)', flush=True)
    t_end = float(steps - 1)
    truth.t_model = alarms.t_model = t_end
    open_alarms = [(pt, s) for pt in patients for s in SOURCES if pt.alarm_active(s.j)]
    alarms.emit('run_end', aborted=False, t_end_abs=t0 + t_end, alarms_switched_off=len(open_alarms))
    truth.emit('run_end', aborted=False, t_end_abs=t0 + t_end)
    alarms.t_model = t_end + 0.5
    for pt, s in open_alarms:                       # like the runner: state=done switches every alarm off
        p = prov[(pt.index, s.j)]
        alarms.emit('alarm', patient=pt.patient_id, patient_index=pt.index, source=s.key, j=s.j, channel=s.channel,
                    port=p['port'], epr=p['epr'], on=False, t_prov_ms=int(round((t0 + t_end + 0.5) * 1000.0)),
                    t_prov_abs=t0 + t_end + 0.5, t_cmd_abs=t0 + t_end + 0.5, value=pt.value_text(s.j),
                    latency_ms=0.0, t_model=t_end, at_done=True)
    truth.close()
    alarms.close()
    crisis_ids = sorted(c.crisis_id for c in sz.crises_done)
    return {'h': h, 'folder': str(folder), 'thresholds': {s.key: list(scaled_thresholds(s, h)) for s in SOURCES},
            'alarms_on': {s.key: counts[s.key] for s in SOURCES}, 'alarms_off': n_off,
            'alarms_on_net_span': {s.key: counts_span[s.key] for s in SOURCES}, 'hours': hours, 'partial': partial,
            'open_at_end': len(open_alarms), 'crises_ended': len(crisis_ids), 'crisis_ids': crisis_ids,
            'wall_s': round(time.time() - t_wall, 1)}


def check_against_net(res: dict, net: dict) -> list[str]:
    """h = 1.0 against journal B of the network run: alarm counts per channel within CHECK_TOL and identical
    crisis ids. When the offline run is longer than the network run (res['partial']), the alarms inside the span
    of the network run are compared and the crisis ids of the network run must be a subset."""
    problems = []
    partial = res.get('partial', False)
    on = res.get('alarms_on_net_span', res['alarms_on']) if partial else res['alarms_on']
    for s in SOURCES:
        a, b = on[s.key], net['on_counts'].get(s.key, 0)
        rel = abs(a - b) / b if b else float('inf')
        if rel > CHECK_TOL:
            problems.append(f'{s.key}: offline {a} vs network {b} ({100 * rel:.2f} %)')
    if partial:
        missing = sorted(set(net['crisis_ids']) - set(res['crisis_ids']))
        if missing:
            problems.append(f'{len(missing)} crisis ids of the network run missing offline, e.g. {missing[:3]}')
    elif res['crisis_ids'] != net['crisis_ids']:
        problems.append(f'crisis ids differ: offline {len(res["crisis_ids"])} vs network {len(net["crisis_ids"])}')
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--net-run', default='ziel3_12h', help='finished network run whose scenario is re-run (default %(default)s)')
    ap.add_argument('--out', default='ziel3_roc', help='output folder under logs/ (default %(default)s)')
    ap.add_argument('--grid', default=DEFAULT_GRID, help='comma-separated threshold scales h; 1.0 is added if missing')
    ap.add_argument('--no-check', action='store_true', help='do not stop when h = 1.0 differs from the network run')
    ap.add_argument('--hours', type=float, default=None,
                    help='model hours to run (default: hours of the network run); longer runs keep the span of the '
                         'network run identical (same seed) and the check compares that span')
    ap.add_argument('--seed', type=int, default=None,
                    help='new seed for the scenario of --net-run (independent data set, no network run): the check '
                         'against the network journals is skipped and nothing is copied')
    ap.add_argument('--quiet-progress', action='store_true')
    a = ap.parse_args(argv)
    if a.hours is not None and not a.hours > 0:
        ap.error('--hours must be > 0')

    grid = sorted({round(float(x), 4) for x in a.grid.split(',') if x.strip()} | {1.0})
    net = load_net_run(a.net_run)
    if a.seed is not None:
        net = apply_seed(net, a.seed)
    out_dir = LOGS / a.out
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = net['config']
    hours = float(cfg['hours']) if a.hours is None else a.hours
    print(f'roc sweep of {a.net_run}: seed {cfg["seed"]}'
          + (f' (new seed; scenario of the run with seed {net["seed_source_run"]})' if a.seed is not None else '')
          + f', {cfg["patients"]} patients, {cfg["hours"]:g} h'
          + (f' (offline {hours:g} h)' if hours != float(cfg['hours']) else '')
          + f', p_cross {cfg["crisis_params"]["p_cross"]}, t0_abs {net["t0_abs"]:.3f}; grid {grid}')
    if a.seed is None:
        print(f'network run: alarms ON per channel {net["on_counts"]}, crises {len(net["crisis_ids"])}')
        netz = out_dir / f'netz_{a.net_run}'
        netz.mkdir(exist_ok=True)
        for fn in ('truth.jsonl', 'alarms.jsonl', 'run_config.json'):
            shutil.copyfile(net['dir'] / fn, netz / fn)
        print(f'copied truth.jsonl, alarms.jsonl, run_config.json of {a.net_run} -> {netz}')
    else:
        print('new seed: no network journals to compare with, check at h = 1.0 skipped, nothing copied')

    results = []
    order = [1.0] + [h for h in grid if h != 1.0]          # the check first
    for h in order:
        print(f'h = {h:.2f} ...', flush=True)
        res = sweep_one(net, h, out_dir, grid, progress=not a.quiet_progress, hours=a.hours)
        results.append(res)
        print(f'  alarms ON {res["alarms_on"]}, crises {res["crises_ended"]}, open at end {res["open_at_end"]}, '
              f'{res["wall_s"]} s; thresholds ' + ' '.join(f'{k}={v[0]:g}/{v[1]:g}' for k, v in res['thresholds'].items()))
        if h == 1.0 and a.seed is not None:
            print(f'  h = 1.0 with the new seed {a.seed}: alarms ON {res["alarms_on"]}, crises {res["crises_ended"]} (no network check)')
        elif h == 1.0:
            problems = check_against_net(res, net)
            if problems:
                print('CHECK h = 1.0 FAILED: ' + '; '.join(problems))
                if not a.no_check:
                    return 2
            else:
                on = res['alarms_on_net_span'] if res['partial'] else res['alarms_on']
                diffs = {s.key: on[s.key] - net['on_counts'].get(s.key, 0) for s in SOURCES}
                span = f' within the first {cfg["hours"]:g} h' if res['partial'] else ''
                ids = (f'all {len(net["crisis_ids"])} crisis ids of the network run present offline ({len(res["crisis_ids"])} in {hours:g} h)'
                       if res['partial'] else f'crisis ids identical ({len(res["crisis_ids"])})')
                print(f'  check h = 1.0 against journal B of {a.net_run}{span}: alarm counts within {100 * CHECK_TOL:g} % '
                      f'(differences {diffs}), {ids}')
    summary = {'source_run': a.net_run, 'grid': grid, 'net_alarms_on': net['on_counts'], 'net_crises': len(net['crisis_ids']),
               'hours_source_run': float(cfg['hours']), 'hours': hours, 'p_cross': cfg['crisis_params']['p_cross'], 'seed': cfg['seed'],
               'seed_source_run': net.get('seed_source_run', cfg['seed']), 'seed_override': bool(net.get('seed_override', False)),
               'check_h1': 'skipped (new seed, no network run)' if a.seed is not None else 'against journal B of the source run',
               't0_abs': net['t0_abs'], 'created': _dt.datetime.now().isoformat(timespec='seconds'),
               'results': [{k: v for k, v in r.items() if k != 'crisis_ids'} for r in sorted(results, key=lambda r: r['h'])]}
    (out_dir / 'sweep_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(f'done: {len(results)} threshold scales -> {out_dir}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
