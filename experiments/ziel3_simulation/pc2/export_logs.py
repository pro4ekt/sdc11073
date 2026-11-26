"""export_logs.py -- CSV digest of one Ziel 3 run of PC 2 (read-only on the journals).

Reads logs/<name>/pc2/{truth,alarms}.jsonl and writes logs/<name>/pc2/export/:

  crises.csv     patient;crisis_id;t_start;t_end;ECG;Vent;SpO2;ABP     (crossing time per source or empty)
  alarms.csv     patient;source;priority;t_on;t_off;value_on           (one row per alarm, provider echo times)
  movements.csv  patient;t_start;duration;gain_ECG;gain_Vent;gain_SpO2;gain_ABP
  status.csv     t;patient;source;subscriptions
  run_config.json (copy)

Separator ';', times = epoch seconds of the PC 2 clock with 3 decimals.

    python export_logs.py --name ziel3_1h            (or --run-dir <folder with the jsonl files>)
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
LOGS = ROOT / 'experiments' / 'ziel3_simulation' / 'logs'
sys.path.insert(0, str(HERE))
from patient import SOURCES  # noqa: E402

SHORT = {s.key: s.short for s in SOURCES}          # ecg -> ECG ...
PRIORITY = {s.key: s.priority for s in SOURCES}
ORDER = [s.key for s in SOURCES]                   # contract order


def t3(x) -> str:
    return '' if x is None else f'{float(x):.3f}'


def read_jsonl(path: pathlib.Path) -> list[dict]:
    out = []
    with path.open(encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def export(run_dir: pathlib.Path) -> dict[str, int]:
    truth = read_jsonl(run_dir / 'truth.jsonl')
    alarms = read_jsonl(run_dir / 'alarms.jsonl')
    out = run_dir / 'export'
    out.mkdir(exist_ok=True)
    sizes: dict[str, int] = {}

    def writer(name: str):
        fh = (out / name).open('w', encoding='utf-8', newline='')
        return fh, csv.writer(fh, delimiter=';', lineterminator='\n')

    # ---- crises.csv -------------------------------------------------------------------------
    run_start = next((r['t_start_abs'] for r in truth if r['event'] == 'run_start'), None)
    starts = {r['crisis_id']: r for r in truth if r['event'] == 'crisis_start'}
    ends = {r['crisis_id']: r for r in truth if r['event'] == 'crisis_end'}
    fh, w = writer('crises.csv')
    w.writerow(['patient', 'crisis_id', 't_start', 't_end'] + [SHORT[k] for k in ORDER])
    for cid in sorted(starts, key=lambda c: starts[c]['t_start_abs']):
        s, e = starts[cid], ends.get(cid)
        row = [s['patient'], cid, t3(s['t_start_abs']), t3(e['t_end_abs']) if e else '']
        for key in ORDER:
            tc = e['sources'][key].get('t_cross_abs') if e else None
            row.append(t3(tc))
        w.writerow(row)
    fh.close()
    sizes['crises.csv'] = (out / 'crises.csv').stat().st_size

    # ---- alarms.csv (pair ON/OFF per patient+source in echo order) ------------------------------
    open_on: dict[tuple, dict] = {}
    rows = []
    for r in alarms:
        if r['event'] != 'alarm':
            continue
        key = (r['patient'], r['source'])
        t = r.get('t_prov_abs')
        if t is None and r.get('t_prov_ms') is not None:
            t = r['t_prov_ms'] / 1000.0
        if t is None:
            t = r['t_abs']
        if r['on']:
            if key in open_on:                       # ON without OFF before: close the previous one open-ended
                prev = open_on.pop(key)
                rows.append((prev['t'], key, prev, None))
            open_on[key] = {'t': t, 'value': r.get('value')}
        else:
            prev = open_on.pop(key, None)
            if prev is None:
                rows.append((t, key, {'t': None, 'value': None}, t))
            else:
                rows.append((prev['t'], key, prev, t))
    for key, prev in open_on.items():
        rows.append((prev['t'], key, prev, None))
    rows.sort(key=lambda x: (x[0] if x[0] is not None else x[3] or 0.0, x[1]))
    fh, w = writer('alarms.csv')
    w.writerow(['patient', 'source', 'priority', 't_on', 't_off', 'value_on'])
    for _, (patient, source), prev, t_off in rows:
        w.writerow([patient, SHORT.get(source, source), PRIORITY.get(source, ''), t3(prev['t']), t3(t_off),
                    '' if prev['value'] is None else prev['value']])
    fh.close()
    sizes['alarms.csv'] = (out / 'alarms.csv').stat().st_size

    # ---- movements.csv ------------------------------------------------------------------------
    fh, w = writer('movements.csv')
    w.writerow(['patient', 't_start', 'duration'] + [f'gain_{SHORT[k]}' for k in ORDER])
    for r in truth:
        if r['event'] != 'movement':
            continue
        t_start = (run_start + r['t_model']) if run_start is not None else r['t_abs']
        w.writerow([r['patient'], t3(t_start), f"{r['duration_s']:.2f}"] + [f'{g:.3f}' for g in r['gains']])
    fh.close()
    sizes['movements.csv'] = (out / 'movements.csv').stat().st_size

    # ---- status.csv ---------------------------------------------------------------------------
    fh, w = writer('status.csv')
    w.writerow(['t', 'patient', 'source', 'subscriptions'])
    for r in alarms:
        if r['event'] != 'status':
            continue
        t = r['t_prov_ms'] / 1000.0 if r.get('t_prov_ms') is not None else r['t_abs']
        w.writerow([t3(t), r['patient'], SHORT.get(r['source'], r['source']), r['subscriptions']])
    fh.close()
    sizes['status.csv'] = (out / 'status.csv').stat().st_size

    # ---- run_config.json ----------------------------------------------------------------------
    src = run_dir / 'run_config.json'
    if src.exists():
        shutil.copyfile(src, out / 'run_config.json')
    else:
        cfg = next((r for r in alarms if r['event'] == 'run_config'), None)
        (out / 'run_config.json').write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding='utf-8')
    sizes['run_config.json'] = (out / 'run_config.json').stat().st_size
    return sizes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--name', help='run name -> logs/<name>/pc2')
    g.add_argument('--run-dir', type=pathlib.Path, help='folder with truth.jsonl / alarms.jsonl')
    a = ap.parse_args(argv)
    run_dir = (a.run_dir or LOGS / a.name / 'pc2').resolve()
    if not (run_dir / 'alarms.jsonl').exists():
        raise SystemExit(f'no alarms.jsonl in {run_dir}')
    sizes = export(run_dir)
    print(f'export -> {run_dir / "export"}')
    for name, size in sizes.items():
        print(f'  {name:16s} {size:>10,d} bytes')
    return 0


if __name__ == '__main__':
    sys.exit(main())
