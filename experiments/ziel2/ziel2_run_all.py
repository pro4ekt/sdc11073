"""ziel2_run_all.py -- ONE command for the whole Ziel 2 evaluation.

    1. ziel2_ack.py    (default 300 accept + 20 refuse trials, one ECG provider)   ~ 15-20 min
    2. ziel2_fault.py  (default shutdown=30, kill=30, suspend=30, reconnect=5)     ~ 40-45 min
    3. ziel2_analyze.py on both journals -> report.txt + report.json

Usage (repo root, dashboard window closed):
    .venv\\Scripts\\python experiments\\ziel2\\ziel2_run_all.py --ip 192.168.0.106
    .venv\\Scripts\\python experiments\\ziel2\\ziel2_run_all.py --ip 192.168.0.106 --dry-run
Options: --accept, --refuse, --methods, --skip-ack, --skip-fault, --name.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
LOG_DIR = HERE / 'logs'


def run(cmd: list[str], console: pathlib.Path, dry: bool) -> int:
    print('   ' + ' '.join(cmd), flush=True)
    if dry:
        return 0
    with console.open('w', encoding='utf-8') as fh:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding='utf-8', errors='replace', bufsize=1)
        try:
            for line in proc.stdout:
                fh.write(line)
                fh.flush()
                print('   ' + line.rstrip(), flush=True)
            proc.wait()
        except KeyboardInterrupt:
            print('\n   Ctrl-C: stopping ...', flush=True)
            try:
                proc.terminate()
                proc.wait(timeout=20)
            except Exception:
                proc.kill()
            raise
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
    except Exception:
        pass
    ap = argparse.ArgumentParser(description='Ziel 2: ack run + fault injection + analysis in one command')
    ap.add_argument('--ip', required=True)
    ap.add_argument('--room', default='ICU-1')
    ap.add_argument('--accept', type=int, default=300)
    ap.add_argument('--refuse', type=int, default=20)
    ap.add_argument('--methods', default='shutdown=30,kill=30,suspend=30,reconnect=5')
    ap.add_argument('--skip-ack', action='store_true')
    ap.add_argument('--skip-fault', action='store_true')
    ap.add_argument('--name', default=None)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)

    label = a.name or _dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    j_ack = LOG_DIR / f'ziel2_ack_{label}.jsonl'
    j_fault = LOG_DIR / f'ziel2_fault_{label}.jsonl'
    report_txt = LOG_DIR / f'ziel2_report_{label}.txt'
    report_json = LOG_DIR / f'ziel2_report_{label}.json'
    py = sys.executable
    t0 = time.monotonic()
    journals: list[pathlib.Path] = []
    # a skipped step whose journal of the same --name already exists is still analysed
    if a.skip_ack and j_ack.exists():
        print(f'ack run skipped; using the existing {j_ack.name} for the analysis')
        journals.append(j_ack)
    if a.skip_fault and j_fault.exists():
        print(f'fault run skipped; using the existing {j_fault.name} for the analysis')
        journals.append(j_fault)
    try:
        if not a.skip_ack:
            print(f'\n== ack run: accept={a.accept} refuse={a.refuse} -> {j_ack.name}')
            rc = run([py, '-u', str(HERE / 'ziel2_ack.py'), '--ip', a.ip, '--room', a.room,
                      '--accept', str(a.accept), '--refuse', str(a.refuse), '--log', str(j_ack)],
                     j_ack.with_suffix('.console.txt'), a.dry_run)
            if rc != 0 and not a.dry_run:
                print(f'!! ack run exited with code {rc}')
                return rc
            journals.append(j_ack)
        if not a.skip_fault:
            print(f'\n== fault run: {a.methods} -> {j_fault.name}')
            rc = run([py, '-u', str(HERE / 'ziel2_fault.py'), '--ip', a.ip, '--room', a.room,
                      '--methods', a.methods, '--log', str(j_fault)],
                     j_fault.with_suffix('.console.txt'), a.dry_run)
            if rc != 0 and not a.dry_run:
                print(f'!! fault run exited with code {rc}')
                return rc
            journals.append(j_fault)
    except KeyboardInterrupt:
        print('interrupted; partial journals: ' + ', '.join(str(j) for j in journals))
        return 130

    cmd = [py, str(HERE / 'ziel2_analyze.py'), *map(str, journals), '--per-trial', '--json', str(report_json)]
    print('\n== analysis')
    print('   ' + ' '.join(cmd))
    if a.dry_run:
        return 0
    res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    report_txt.write_text(res.stdout + (('\n' + res.stderr) if res.stderr else ''), encoding='utf-8')
    for line in res.stdout.splitlines():
        if line.startswith('Per fault trial'):
            break
        print('   ' + line)
    print(f'\nDone in {(time.monotonic() - t0) / 60:.1f} min.\nreport : {report_txt}\njson   : {report_json}\n'
          f'journals: ' + ', '.join(j.name for j in journals))
    return res.returncode


if __name__ == '__main__':
    raise SystemExit(main())
