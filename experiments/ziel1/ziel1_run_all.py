"""ziel1_run_all.py -- ONE command for the whole Ziel 1 evaluation.

Runs, in this order, each as its own process:
    1. filtered run   (dashboard with --room, default 150 foreign + 150 mismatch + 5 own + 5 nolocation)
    2. baseline run   (no room filter, default 20 + 20 + 2 + 2, enough for the per-provider comparison)
    3. ziel1_analyze.py on both journals -> report.txt + report.json

Optional packet capture: if tshark (Wireshark) is installed and the interface of --ip can be
found, every run is captured to a .pcapng and the TCP/UDP conversation tables are saved next
to it.  Without tshark the runs proceed without capture (the subscription counts in the
journal and the provider-side ``status`` are the primary FF1 figures anyway).

Usage (repo root, dashboard window closed):
    .venv\\Scripts\\python experiments\\ziel1\\ziel1_run_all.py --ip 192.168.0.105
    .venv\\Scripts\\python experiments\\ziel1\\ziel1_run_all.py --ip 192.168.0.105 --dry-run
Options: --room, --trials-filtered, --trials-baseline, --skip-baseline, --capture auto|off,
         --tshark <path>, --name <label>.
Expected duration with the defaults: about 35 min (filtered) + 6 min (baseline).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import re
import shutil
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
LOG_DIR = HERE / 'logs'
RUNNER = HERE / 'ziel1_experiment.py'
ANALYZER = HERE / 'ziel1_analyze.py'

PORT_BASE_FILTERED = 10100
PORT_BASE_BASELINE = 10500
CAPTURE_FILTER = 'udp port 3702 or tcp portrange 10100-10999'


def find_tshark(explicit: str | None) -> str | None:
    if explicit:
        return explicit if pathlib.Path(explicit).exists() else None
    found = shutil.which('tshark')
    if found:
        return found
    for cand in (r'C:\Program Files\Wireshark\tshark.exe', r'C:\Program Files (x86)\Wireshark\tshark.exe'):
        if pathlib.Path(cand).exists():
            return cand
    return None


def find_interface(tshark: str, ip: str) -> str | None:
    """tshark interface id for the adapter that owns ``ip`` (Windows: via the InterfaceAlias)."""
    alias = None
    try:
        out = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             f"Get-NetIPAddress -AddressFamily IPv4 | Where-Object {{ $_.IPAddress -eq '{ip}' }} "
             f"| Select-Object -ExpandProperty InterfaceAlias"],
            capture_output=True, text=True, timeout=20).stdout.strip().splitlines()
        alias = out[0].strip() if out else None
    except Exception:
        alias = None
    try:
        listing = subprocess.run([tshark, '-D'], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return None
    for line in listing.splitlines():
        # "3. \Device\NPF_{...} (Ethernet)"
        m = re.match(r'\s*(\d+)\.\s+(\S+)(?:\s+\((.*)\))?', line)
        if not m:
            continue
        if alias and m.group(3) and m.group(3).strip() == alias:
            return m.group(1)
    return None


def run_capture(tshark: str, iface: str, out: pathlib.Path) -> subprocess.Popen:
    return subprocess.Popen([tshark, '-i', iface, '-f', CAPTURE_FILTER, '-w', str(out), '-q'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop_capture(proc: subprocess.Popen | None, tshark: str | None, pcap: pathlib.Path | None) -> None:
    if proc is None:
        return
    time.sleep(2)
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
    if tshark and pcap and pcap.exists():
        conv = pcap.with_suffix('.conv.txt')
        with conv.open('w', encoding='utf-8') as fh:
            subprocess.run([tshark, '-r', str(pcap), '-q', '-z', 'conv,tcp', '-z', 'conv,udp'],
                           stdout=fh, stderr=subprocess.STDOUT, text=True)
        print(f'   capture: {pcap.name}  conversations: {conv.name}')


def run_experiment(mode: str, ip: str, room: str, trials: str, port_base: int, journal: pathlib.Path,
                   console: pathlib.Path, dry: bool) -> int:
    # -u: unbuffered child stdout, otherwise nothing shows up on the screen until the
    # pipe buffer fills (the 2026-10-16 run looked "empty" for that reason).
    cmd = [sys.executable, '-u', str(RUNNER), '--ip', ip, '--mode', mode, '--room', room, '--trials', trials,
           '--port-base', str(port_base), '--log', str(journal)]
    print(f'\n== {mode} run: {trials}  ->  {journal.name}', flush=True)
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
                print('   ' + line.rstrip(), flush=True)      # everything, live
            proc.wait()
        except KeyboardInterrupt:
            print('\n   Ctrl-C: stopping the run (journal is closed by the runner) ...', flush=True)
            try:
                proc.terminate()
                proc.wait(timeout=20)
            except Exception:
                proc.kill()
            raise
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    ap = argparse.ArgumentParser(description='Ziel 1: filtered run + baseline run + analysis in one command')
    ap.add_argument('--ip', required=True, help='adapter IPv4 of this host')
    ap.add_argument('--room', default='ICU-1')
    ap.add_argument('--trials-filtered', default='foreign=150,mismatch=150,own=5,nolocation=5')
    ap.add_argument('--trials-baseline', default='foreign=20,mismatch=20,own=2,nolocation=2')
    ap.add_argument('--skip-baseline', action='store_true')
    ap.add_argument('--capture', choices=('auto', 'off'), default='auto', help='packet capture with tshark if available')
    ap.add_argument('--tshark', help='path to tshark.exe (default: search PATH and Program Files)')
    ap.add_argument('--name', default=None, help='label for the output files (default: timestamp)')
    ap.add_argument('--dry-run', action='store_true', help='print the commands, run nothing')
    a = ap.parse_args(argv)

    label = a.name or _dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    j_filtered = LOG_DIR / f'ziel1_filtered_{label}.jsonl'
    j_baseline = LOG_DIR / f'ziel1_baseline_{label}.jsonl'
    report_txt = LOG_DIR / f'ziel1_report_{label}.txt'
    report_json = LOG_DIR / f'ziel1_report_{label}.json'

    tshark = find_tshark(a.tshark) if a.capture == 'auto' else None
    iface = find_interface(tshark, a.ip) if tshark else None
    if a.capture == 'auto':
        if tshark and iface:
            print(f'capture: tshark={tshark} interface={iface} filter="{CAPTURE_FILTER}"')
        elif tshark:
            print('capture: tshark found but the interface of --ip could not be identified -> no capture')
        else:
            print('capture: tshark not installed -> no packet capture (journal + provider status only)')

    t0 = time.monotonic()
    steps = [('filtered', a.trials_filtered, PORT_BASE_FILTERED, j_filtered)]
    if not a.skip_baseline:
        steps.append(('baseline', a.trials_baseline, PORT_BASE_BASELINE, j_baseline))

    for mode, trials, port_base, journal in steps:
        cap = None
        pcap = journal.with_suffix('.pcapng')
        if tshark and iface and not a.dry_run:
            cap = run_capture(tshark, iface, pcap)
            time.sleep(2)
        try:
            rc = run_experiment(mode, a.ip, a.room, trials, port_base, journal,
                                journal.with_suffix('.console.txt'), a.dry_run)
        except KeyboardInterrupt:
            stop_capture(cap, tshark, pcap if cap else None)
            print(f'interrupted during the {mode} run; partial journal: {journal}')
            return 130
        stop_capture(cap, tshark, pcap if cap else None)
        if rc != 0 and not a.dry_run:
            print(f'!! {mode} run exited with code {rc} - see {journal.with_suffix(".console.txt")}')
            return rc

    cmd = [sys.executable, str(ANALYZER), str(j_filtered), '--per-trial', '--json', str(report_json)]
    if not a.skip_baseline:
        cmd += ['--baseline', str(j_baseline)]
    print('\n== analysis')
    print('   ' + ' '.join(cmd))
    if a.dry_run:
        return 0
    res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    report_txt.write_text(res.stdout + (('\n' + res.stderr) if res.stderr else ''), encoding='utf-8')
    # key lines on screen; the full report is in the file
    for line in res.stdout.splitlines():
        if line.startswith('Per EPR'):
            break
        print('   ' + line)
    print(f'\nDone in {(time.monotonic() - t0) / 60:.1f} min.\n'
          f'report : {report_txt}\n'
          f'json   : {report_json}\n'
          f'journals: {j_filtered.name}' + ('' if a.skip_baseline else f', {j_baseline.name}'))
    return res.returncode


if __name__ == '__main__':
    raise SystemExit(main())
