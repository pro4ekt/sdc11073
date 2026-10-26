"""ziel1_experiment.py -- headless runner for the Ziel 1 experiment (TRA, avoided subscriptions).

Runs the REAL dashboard objects (SdcMyConsumer with its discovery loop, room filter and
event journal) without the QML window, and starts testbed providers in the four roles of
experiments/ziel1/README.md, one provider per trial with a FRESH endpoint reference:

    own          --room <ROOM>                  expected: accept  (scope_confirmed) + subscription
    foreign      --room <OTHER>                 expected: reject @discovery, no connection
    nolocation   (no --room)                    expected: unknown + subscription (reported separately)
    mismatch     --scope-room <ROOM> --room <OTHER>   expected: reject @context_precheck (scope_mismatch)

Every trial writes two extra records into the SAME journal as the dashboard
(``trial_start`` with the role = ground truth, ``trial_end`` with the provider-side
subscription count from its ``status`` command), so ziel1_analyze.py needs nothing else.

Usage (from the repo root, provider and dashboard on this host, mTLS from pki/):

    .venv\\Scripts\\python experiments\\ziel1\\ziel1_experiment.py --ip <adapter-ipv4> --mode filtered ^
        --room ICU-1 --trials foreign=10,mismatch=10,own=1,nolocation=1
    .venv\\Scripts\\python experiments\\ziel1\\ziel1_experiment.py --ip <adapter-ipv4> --mode baseline ^
        --trials foreign=2,mismatch=2,own=1,nolocation=1

    --dry-run prints the trial plan and exits (no network, no Qt).

Needs pki/ecg, pki/spo2, pki/abp, pki/vent (providers) and pki/dashboard (consumer); the
ports --port-base .. --port-base+N must be free.  Ziel 1 only; Ziel 2 has its own runners.
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import os
import pathlib
import queue
import subprocess
import sys
import threading
import time
import uuid

REPO = pathlib.Path(__file__).resolve().parents[2]
DASH = REPO / 'Dashboard'
PROVIDER = REPO / 'testbed' / 'providers' / 'testbed_provider.py'
LOG_DIR = pathlib.Path(__file__).resolve().parent / 'logs'

# role -> (pki folder, MDIB file, patient id, model name)
ROLES = {
    'own':        ('ecg',  'ecg_mdib_v2.xml',  'P001', 'ECG Monitor T1'),
    'foreign':    ('spo2', 'spo2_mdib_v2.xml', 'P002', 'SpO2 Monitor T1'),
    'nolocation': ('abp',  'abp_mdib_v2.xml',  'P003', 'ABP Monitor T1'),
    'mismatch':   ('vent', 'vent_mdib_v2.xml', 'P004', 'Ventilator T1'),
}
BETTFREMD = ('foreign', 'mismatch')


# ──────────────────────────────────────────────────────────────────────────────
# Plan
# ──────────────────────────────────────────────────────────────────────────────

def parse_trials(spec: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for part in spec.split(','):
        part = part.strip()
        if not part:
            continue
        role, _, n = part.partition('=')
        role = role.strip()
        if role not in ROLES:
            raise SystemExit(f'unknown role {role!r}; roles: {", ".join(ROLES)}')
        out[role] = int(n or 1)
    if not out:
        raise SystemExit('--trials is empty')
    return out


def make_plan(counts: dict[str, int], order: str) -> list[str]:
    if order == 'grouped':
        plan = []
        for role, n in counts.items():
            plan += [role] * n
        return plan
    # interleave: round-robin so no role is clustered at the start or the end
    remaining = dict(counts)
    plan = []
    while any(remaining.values()):
        for role in list(remaining):
            if remaining[role] > 0:
                plan.append(role)
                remaining[role] -= 1
    return plan


def provider_args(role: str, room: str, other_room: str, epr: str, ip: str, port: int,
                  tls: str, python: str, seed: int | None) -> list[str]:
    pki, mdib, patient, model = ROLES[role]
    args = [python, str(PROVIDER),
            '--mdib', str(REPO / 'testbed' / 'mdib' / mdib), '--epr', epr,
            '--manufacturer', 'SDC Testbed', '--model', model,
            '--friendly-name', f'ziel1 {role}', '--patient-id', patient,
            '--certs-dir', str(REPO / 'pki' / pki), '--ip', ip, '--port', str(port)]
    if role == 'own':
        args += ['--room', room, '--bed', '1']
    elif role == 'foreign':
        args += ['--room', other_room, '--bed', '1']
    elif role == 'mismatch':
        args += ['--scope-room', room, '--room', other_room, '--bed', '1']
    # nolocation: no --room at all
    if tls == 'no_tls':
        args += ['--no-tls']
    if seed is not None:
        args += ['--seed', str(seed)]
    return args


# ──────────────────────────────────────────────────────────────────────────────
# Provider subprocess
# ──────────────────────────────────────────────────────────────────────────────

class Prov:
    def __init__(self, tag: str, args: list[str], echo: bool):
        self.tag = tag
        self.echo = echo
        self.lines: list[str] = []
        self.q: queue.Queue[str] = queue.Queue()
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                     errors='replace', bufsize=1, creationflags=flags)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            line = line.rstrip('\n')
            self.lines.append(line)
            self.q.put(line)
            if self.echo and 'publish:' not in line:
                print(f'    {self.tag}| {line}', flush=True)

    def send(self, cmd: str) -> None:
        try:
            self.proc.stdin.write(cmd + '\n')
            self.proc.stdin.flush()
        except Exception:
            pass

    def wait_line(self, substring: str, timeout: float) -> str | None:
        for line in list(self.lines):
            if substring in line:
                return line
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                line = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            if substring in line:
                return line
        return None

    def wait_new_line(self, substring: str, timeout: float) -> str | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                line = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            if substring in line:
                return line
        return None

    def stop(self, timeout: float = 10.0) -> None:
        self.send('shutdown')
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


def parse_status_subscriptions(line: str | None) -> int | None:
    if not line:
        return None
    for tok in line.split():
        if tok.startswith('subscriptions='):
            try:
                return int(tok.split('=', 1)[1])
            except ValueError:
                return None
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Ziel 1 experiment runner (TRA, avoided subscriptions)')
    ap.add_argument('--ip', help='local adapter IPv4 for WS-Discovery and the providers (required unless --dry-run)')
    ap.add_argument('--mode', choices=('filtered', 'baseline'), default='filtered',
                    help='filtered = dashboard with --room (default); baseline = no filter')
    ap.add_argument('--room', default='ICU-1', help='target room of the dashboard / own room of the providers')
    ap.add_argument('--other-room', default='ICU-2', help='foreign room announced by foreign / mismatch providers')
    ap.add_argument('--trials', default='foreign=10,mismatch=10,own=1,nolocation=1',
                    help='role=count list, e.g. foreign=150,mismatch=150,own=5,nolocation=5')
    ap.add_argument('--order', choices=('interleave', 'grouped'), default='interleave')
    ap.add_argument('--log', help='journal path (default experiments/ziel1/logs/ziel1_<mode>_<timestamp>.jsonl)')
    ap.add_argument('--port-base', type=int, default=10100, help='first provider port; one port per trial')
    ap.add_argument('--trial-timeout', type=float, default=60.0, help='seconds to wait for the dashboard decision')
    ap.add_argument('--settle', type=float, default=3.0, help='seconds between the decision and the status query')
    ap.add_argument('--tls', choices=('auto', 'force_tls', 'no_tls'), default='auto')
    ap.add_argument('--seed', type=int, default=1, help='provider rest-mode noise seed')
    ap.add_argument('--echo-provider', action='store_true', help='print provider output')
    ap.add_argument('--dry-run', action='store_true', help='print the plan, start nothing')
    a = ap.parse_args(argv)
    try:  # Windows consoles default to cp1252; line-buffered so a parent process sees progress
        sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
    except Exception:
        pass

    counts = parse_trials(a.trials)
    plan = make_plan(counts, a.order)
    target_room = a.room if a.mode == 'filtered' else None
    log_path = pathlib.Path(a.log) if a.log else LOG_DIR / (
        f"ziel1_{a.mode}_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl")

    print(f'Ziel 1 runner: mode={a.mode} room={target_room!r} other={a.other_room!r} '
          f'trials={counts} order={a.order} journal={log_path}')
    if a.dry_run:
        for i, role in enumerate(plan, 1):
            epr = f'urn:uuid:{uuid.uuid4()}'
            args = provider_args(role, a.room, a.other_room, epr, a.ip or '<IP>', a.port_base + i - 1,
                                 a.tls, sys.executable, a.seed)
            print(f'  trial {i:3d} {role:10s} port {a.port_base + i - 1}  ' + ' '.join(args[2:]))
        return 0
    if not a.ip:
        ap.error('--ip is required (adapter IPv4), or use --dry-run')

    # ── dashboard objects, headless ─────────────────────────────────────────
    sys.path.insert(0, str(DASH))
    os.chdir(DASH)                       # pki/ and config/ are resolved relative to Dashboard/ by the app
    import logging
    from sdc11073.loghelper import basic_logging_setup
    basic_logging_setup(level=logging.WARNING)
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication([])
    from app.metrics import configure_event_log, get_event_log
    from app.patientOverviewModel import PatientOverviewModel
    from app.sdcMyConsumer import SdcMyConsumer
    logging.getLogger('sdc.consumer').setLevel(logging.WARNING)
    logging.getLogger('sdc.consumer.manager').setLevel(logging.INFO)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    configure_event_log(log_path)
    journal = get_event_log()

    # In-process listener on the journal: every record the dashboard writes is also
    # kept in memory so the runner can detect the decision of the current trial.
    seen: collections.deque = collections.deque()
    _orig_emit = journal.emit

    def _emit(event: str, **fields):
        rec = _orig_emit(event, **fields)
        seen.append(rec)
        return rec
    journal.emit = _emit  # type: ignore[assignment]

    overview = PatientOverviewModel()
    manager = SdcMyConsumer(target_room=target_room, override_ip=a.ip, tls_mode=a.tls,
                            overview_model=overview, event_log=str(log_path))
    manager.start()

    def pump(seconds: float):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.05)

    def events_for(epr: str) -> list[dict]:
        return [r for r in list(seen) if r.get('epr') == epr]

    def decided(epr: str) -> str | None:
        """'reject' | 'subscribed' | None - the terminal state of a trial."""
        for r in events_for(epr):
            if r['event'] == 'subscription_created':
                return 'subscribed'
            if r['event'] == 'topology_decision' and r.get('decision') == 'reject':
                return 'reject'
        return None

    journal.emit('experiment_start', goal='ziel1', mode=a.mode, target_room=target_room,
                 other_room=a.other_room, trials=counts, order=a.order, ip=a.ip, tls=a.tls)
    print(f'[{time.strftime("%H:%M:%S")}] discovery running; {len(plan)} trial(s)')
    pump(3)

    results: list[dict] = []
    try:
        for i, role in enumerate(plan, 1):
            epr = f'urn:uuid:{uuid.uuid4()}'
            port = a.port_base + i - 1
            args = provider_args(role, a.room, a.other_room, epr, a.ip, port, a.tls, sys.executable, a.seed)
            t_start = time.time()
            journal.emit('trial_start', trial=i, role=role, epr=epr, port=port, mode=a.mode,
                         expected=('reject' if role in BETTFREMD else
                                   ('unknown' if role == 'nolocation' else 'accept')) if a.mode == 'filtered'
                         else 'accept')
            print(f'[{time.strftime("%H:%M:%S")}] trial {i}/{len(plan)} {role:10s} epr=..{epr[-12:]} port={port}')
            prov = Prov(f'{role}#{i}', args, a.echo_provider)
            outcome = 'provider_failed'
            provider_subs = None
            try:
                if not prov.wait_line('ready:', 40):
                    print('    provider did not become ready')
                else:
                    end = time.monotonic() + a.trial_timeout
                    outcome = 'timeout'
                    while time.monotonic() < end:
                        pump(0.3)
                        d = decided(epr)
                        if d:
                            outcome = d
                            break
                    pump(a.settle)
                    prov.send('status')
                    provider_subs = parse_status_subscriptions(prov.wait_new_line('status:', 10))
            finally:
                elapsed = round(time.time() - t_start, 3)
                journal.emit('trial_end', trial=i, role=role, epr=epr, outcome=outcome,
                             provider_subscriptions=provider_subs, elapsed_s=elapsed)
                decisions = [(r.get('decision'), r.get('phase'), r.get('reason'))
                             for r in events_for(epr) if r['event'] == 'topology_decision']
                results.append({'trial': i, 'role': role, 'epr': epr, 'outcome': outcome,
                                'provider_subscriptions': provider_subs, 'decisions': decisions})
                print(f'    -> {outcome:10s} decisions={decisions} provider subscriptions={provider_subs} '
                      f'({elapsed:.1f} s)')
                prov.stop()
                pump(1.0)
    except KeyboardInterrupt:
        print('interrupted - stopping')
    finally:
        journal.emit('experiment_end', goal='ziel1', mode=a.mode, trials_done=len(results))
        manager.stop()                 # emits topology_summary / latency_summary / arr_summary
        pump(1.0)
        journal.close()

    # ── short on-screen summary (the analyzer does the real work) ──────────
    print('\nSummary by role (outcome counts):')
    by_role: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in results:
        by_role[r['role']][r['outcome']] += 1
    for role in counts:
        print(f'  {role:10s} {dict(by_role[role])}')
    manifest = log_path.with_suffix('.manifest.json')
    manifest.write_text(json.dumps({'mode': a.mode, 'target_room': target_room, 'other_room': a.other_room,
                                    'trials': results}, indent=2), encoding='utf-8')
    print(f'\njournal : {log_path}\nmanifest: {manifest}\n'
          f'analyze : {sys.executable} experiments/ziel1/ziel1_analyze.py "{log_path}"')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
