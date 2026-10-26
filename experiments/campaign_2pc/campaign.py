"""campaign.py -- ONE command for the Ziel 1 + Ziel 2 measurement campaign on PC 1.

Runs the existing runners (experiments/ziel1/ziel1_experiment.py, experiments/ziel2/ziel2_ack.py,
experiments/ziel2/ziel2_fault.py) phase by phase, each as its OWN process with its OWN headless
dashboard and its OWN journal, under one run directory:

    experiments/campaign_2pc/logs/<run>/
        campaign_status.json      state of every phase (updated after each phase; --resume reads it)
        resources.jsonl           CPU / memory of PC 1, once a minute
        campaign.log              what the orchestrator did
        <phase>/events.jsonl      dashboard journal (--event-log) of that phase
        <phase>/console.txt       runner output incl. provider output (--echo-provider)
        <phase>/capture*.pcapng   tshark ring buffer, if tshark is available
        <phase>/capture_bytes.json  bytes per provider port / role from the capture
        <phase>/analysis.json     analyzer output
        campaign_report.md        morning check: first line GESAMT: OK | FEHLER, plan/done/ok table
        dossier.md                German results dossier (copied to docs/thesis/ for a full run)

Phases (in this order): smoke -> ziel1_filtered -> ziel1_baseline -> ziel2_ack -> ziel2_fault.
A failed trial never stops a phase (the runners handle that); a failed phase never stops the
campaign.  Providers are LOCAL in step 1 (harness.Prov inside the runners); step 2 switches
``providers.source`` to ``remote`` (agent on PC 2) without changing this script's flow.
C++ providers (VirtualBox, bridged) are background only: discovered by every phase's dashboard,
noted in the report, never controlled.

Usage (repo root, dashboard window closed):
    .venv\\Scripts\\python experiments\\campaign_2pc\\campaign.py --pilot          # ~10 min, ends with PILOT: OK | PROBLEME
    .venv\\Scripts\\python experiments\\campaign_2pc\\campaign.py                  # full night run
    .venv\\Scripts\\python experiments\\campaign_2pc\\campaign.py --resume         # continue the newest run
    .venv\\Scripts\\python experiments\\campaign_2pc\\campaign.py --dry-run        # print the commands only
Options: --config PATH, --run-dir PATH, --phases a,b,c (subset), --no-capture.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import ctypes
import datetime as _dt
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
EXPERIMENTS = HERE.parent
REPO = EXPERIMENTS.parent
LOG_DIR = HERE / 'logs'
ZIEL1 = EXPERIMENTS / 'ziel1'
ZIEL2 = EXPERIMENTS / 'ziel2'
PY = sys.executable
PHASE_ORDER = ('smoke', 'ziel1_filtered', 'ziel1_baseline', 'ziel2_ack', 'ziel2_fault')
CAP_KEYS = ('size_bytes', 'bytes', 'frames', 'udp_bytes', 'udp_3702_bytes', 'tcp_provider_bytes', 'tcp_notify_bytes')

sys.path.insert(0, str(EXPERIMENTS))   # harness helpers (process-tree kill)


def now_ts() -> str:
    return _dt.datetime.now().strftime('%Y%m%d_%H%M%S')


def hms() -> str:
    return time.strftime('%H:%M:%S')


def pct(x) -> str:
    return '-' if x is None else f'{100 * x:.2f} %'


def fmt(x, unit='', nd=1) -> str:
    if x is None:
        return '-'
    return f'{x:.{nd}f}{unit}' if isinstance(x, float) else f'{x}{unit}'


# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

def load_config(path: pathlib.Path, pilot: bool) -> dict:
    cfg = json.loads(path.read_text(encoding='utf-8'))
    if pilot:
        for phase, over in cfg.get('pilot', {}).items():
            base = cfg['phases'].setdefault(phase, {})
            if phase == 'smoke':
                for sub, ov in over.items():
                    base.setdefault(sub, {}).update(ov)
            else:
                base.update(over)
    return cfg


def parse_counts(spec: str) -> dict[str, int]:
    out = {}
    for part in spec.split(','):
        part = part.strip()
        if part:
            k, _, v = part.partition('=')
            out[k.strip()] = int(v or 1)
    return out


def planned_seconds(phase: str, cfg: dict) -> float:
    """Rough duration estimate for the phase timeout (2x) from measured per-trial times."""
    p = cfg['phases'][phase]
    per = {'shutdown': 14, 'kill': 19, 'suspend': 28, 'reconnect': 45, 'netdown': 70}
    if phase == 'smoke':
        return (12 * sum(parse_counts(p['ziel1']['trials']).values()) + 3 * (p['ack']['accept'] + p['ack']['refuse'])
                + sum(per.get(m, 30) * n for m, n in parse_counts(p['fault']['methods']).items()) + 90)
    if phase.startswith('ziel1'):
        return 12 * sum(parse_counts(p['trials']).values()) + 30
    if phase == 'ziel2_ack':
        return 3 * (p['accept'] + p['refuse']) + 60
    if phase == 'ziel2_fault':
        return sum(per.get(m, 30) * n for m, n in parse_counts(p['methods']).items()) + 30
    return 600


def planned_trials(phase: str, cfg: dict) -> int:
    p = cfg['phases'][phase]
    if phase == 'smoke':
        return (sum(parse_counts(p['ziel1']['trials']).values()) + p['ack']['accept'] + p['ack']['refuse']
                + sum(parse_counts(p['fault']['methods']).values()))
    if phase.startswith('ziel1'):
        return sum(parse_counts(p['trials']).values())
    if phase == 'ziel2_ack':
        return p['accept'] + p['refuse']
    if phase == 'ziel2_fault':
        return sum(parse_counts(p['methods']).values())
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# Logging / status
# ──────────────────────────────────────────────────────────────────────────────

class Campaign:
    def __init__(self, cfg: dict, run_dir: pathlib.Path, pilot: bool, dry: bool, capture: bool):
        self.cfg = cfg
        self.run_dir = run_dir
        self.pilot = pilot
        self.dry = dry
        self.capture_wanted = capture and cfg.get('capture', {}).get('enabled', 'auto') != 'off'
        self.status_path = run_dir / 'campaign_status.json'
        self.status: dict = {'run_dir': str(run_dir), 'pilot': pilot, 'started': _dt.datetime.now().isoformat(timespec='seconds'),
                             'config': str(cfg.get('_config_path', '')), 'phases': {}}
        if self.status_path.exists():
            try:
                self.status = json.loads(self.status_path.read_text(encoding='utf-8'))
            except Exception:
                pass
        self.warnings: list[str] = list(self.status.get('warnings') or [])
        self.tshark = self._find_tshark() if self.capture_wanted else None
        self.iface = self._find_interface() if self.tshark else None
        self._res_stop = threading.Event()

    # -- log ----------------------------------------------------------------
    def log(self, msg: str) -> None:
        line = f'[{hms()}] {msg}'
        print(line, flush=True)
        if not self.dry:
            with (self.run_dir / 'campaign.log').open('a', encoding='utf-8') as fh:
                fh.write(line + '\n')

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        self.log('WARNUNG: ' + msg)

    def save_status(self) -> None:
        if self.dry:
            return
        self.status['updated'] = _dt.datetime.now().isoformat(timespec='seconds')
        self.status['warnings'] = self.warnings
        self.status_path.write_text(json.dumps(self.status, indent=2, default=str), encoding='utf-8')

    # -- tshark -------------------------------------------------------------
    def _find_tshark(self) -> str | None:
        explicit = self.cfg.get('capture', {}).get('tshark')
        if explicit:
            return explicit if pathlib.Path(explicit).exists() else None
        found = shutil.which('tshark')
        if found:
            return found
        for cand in (r'C:\Program Files\Wireshark\tshark.exe', r'C:\Program Files (x86)\Wireshark\tshark.exe'):
            if pathlib.Path(cand).exists():
                return cand
        return None

    def _tshark_interfaces(self) -> list[tuple[str, str, str]]:
        try:
            out = subprocess.run([self.tshark, '-D'], capture_output=True, text=True, timeout=30).stdout
        except Exception:
            return []
        res = []
        for line in out.splitlines():
            m = re.match(r'\s*(\d+)\.\s+(\S+)(?:\s+\((.*)\))?', line)
            if m:
                res.append((m.group(1), m.group(2), (m.group(3) or '').strip()))
        return res

    def _find_interface(self) -> str | None:
        want = self.cfg.get('capture', {}).get('interface', 'auto')
        ifaces = self._tshark_interfaces()
        if not ifaces:
            return None
        if want and want != 'auto':
            w = str(want)
            for idx, dev, alias in ifaces:
                if w == idx or w == dev or (alias and w.lower() in alias.lower()):
                    return dev
            return None
        local = self.cfg.get('providers', {}).get('source', 'local') == 'local'
        if local:
            for idx, dev, alias in ifaces:
                if 'loopback' in dev.lower() or 'loopback' in alias.lower():
                    return dev
        # adapter that owns the experiment IP
        alias_of_ip = None
        try:
            out = subprocess.run(
                ['powershell', '-NoProfile', '-Command',
                 f"Get-NetIPAddress -AddressFamily IPv4 | Where-Object {{ $_.IPAddress -eq '{self.cfg['ip']}' }} "
                 f"| Select-Object -ExpandProperty InterfaceAlias"],
                capture_output=True, text=True, timeout=30).stdout.strip().splitlines()
            alias_of_ip = out[0].strip() if out else None
        except Exception:
            pass
        if alias_of_ip:
            for idx, dev, alias in ifaces:
                if alias == alias_of_ip:
                    return dev
        return None

    def capture_filter(self) -> str:
        """Capture filter; 'auto' = all traffic with this host (local providers: exactly the SDC traffic on
        the loopback adapter) or, with remote providers, all traffic with PC 2 plus discovery multicast."""
        cap = self.cfg.get('capture', {})
        f = cap.get('filter') or 'auto'
        if f == 'auto':
            local = self.cfg.get('providers', {}).get('source', 'local') == 'local'
            f = 'host {ip}' if local else 'host {provider_host} or udp port 3702'
        return f.format(ip=self.cfg['ip'], provider_host=self.cfg.get('providers', {}).get('host', ''))

    def start_capture(self, phase_dir: pathlib.Path) -> subprocess.Popen | None:
        if not self.tshark or not self.iface or self.dry:
            return None
        cap = self.cfg.get('capture', {})
        out = phase_dir / 'capture.pcapng'
        cmd = [self.tshark, '-i', self.iface, '-f', self.capture_filter(),
               '-b', f"filesize:{int(cap.get('ring_file_kb', 200000))}", '-b', f"files:{int(cap.get('ring_files', 3))}",
               '-w', str(out), '-q']
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=(phase_dir / 'tshark.err').open('w'),
                                    creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
            time.sleep(2.0)
            if proc.poll() is not None:
                self.warn(f'tshark beendete sich sofort (rc={proc.returncode}); siehe {phase_dir / "tshark.err"}')
                return None
            (phase_dir / 'capture_filter.txt').write_text(f'{self.iface}\n{self.capture_filter()}\n', encoding='utf-8')
            self.log(f'tshark: capture on {self.iface} filter "{self.capture_filter()}" -> {out.name}*')
            return proc
        except Exception as exc:
            self.warn(f'tshark konnte nicht gestartet werden: {exc}')
            return None

    def stop_capture(self, proc: subprocess.Popen | None) -> None:
        if proc is None:
            return
        time.sleep(2.0)
        try:                                   # Ctrl-Break = clean stop: tshark and dumpcap close the file
            proc.send_signal(getattr(signal, 'CTRL_BREAK_EVENT', signal.SIGTERM))
            proc.wait(timeout=20)
        except Exception:
            from harness import _process_tree, _terminate_pid
            for pid in reversed(_process_tree(proc.pid)):
                _terminate_pid(pid)

    def analyse_capture(self, phase_dir: pathlib.Path, journal: pathlib.Path | None) -> dict | None:
        """Bytes of the phase capture: UDP (discovery), TCP at provider ports (request/response) and other TCP
        (notifications to the dashboard's event port); TCP attributed to the trial role by time window
        (trials run one after another, so the latest trial_start before a frame owns it)."""
        files = sorted(phase_dir.glob('capture*.pcapng'))
        if not files or not self.tshark:
            return None
        lo, hi = self.cfg.get('capture', {}).get('provider_ports', [10000, 11999])
        starts: list[tuple[float, str]] = []
        if journal is not None:
            for rec in read_jsonl(journal):
                if rec.get('event') == 'trial_start' and rec.get('t_abs') is not None:
                    starts.append((float(rec['t_abs']), rec.get('role') or 'trial'))
        starts.sort()
        keys = [t for t, _ in starts]

        def role_at(t: float) -> str:
            if not starts:
                return 'all'
            i = bisect.bisect_right(keys, t) - 1
            return starts[i][1] if i >= 0 else 'pre'      # 'pre' = before the first trial (dashboard start-up)

        tot = {'bytes': 0, 'frames': 0, 'udp_bytes': 0, 'udp_frames': 0, 'udp_3702_bytes': 0,
               'tcp_provider_bytes': 0, 'tcp_notify_bytes': 0, 'tcp_frames': 0}
        by_role: dict[str, dict] = {role: {'bytes': 0, 'frames': 0, 'provider_port_bytes': 0, 'notify_bytes': 0}
                                    for _, role in starts}          # roles without traffic (foreign) stay listed
        for f in files:
            try:
                out = subprocess.run([self.tshark, '-r', str(f), '-T', 'fields', '-e', 'frame.time_epoch', '-e', 'frame.len',
                                      '-e', 'tcp.srcport', '-e', 'tcp.dstport', '-e', 'udp.srcport', '-e', 'udp.dstport',
                                      '-E', 'separator=,'], capture_output=True, text=True, timeout=900).stdout
            except Exception as exc:
                self.warn(f'tshark -r {f.name} fehlgeschlagen: {exc}')
                continue
            for line in out.splitlines():
                q = line.split(',')
                if len(q) < 6 or not q[1]:
                    continue
                t, n = float(q[0]), int(q[1])
                tot['bytes'] += n
                tot['frames'] += 1
                if q[4] or q[5]:                                   # UDP = discovery (multicast + unicast replies)
                    tot['udp_bytes'] += n
                    tot['udp_frames'] += 1
                    if '3702' in (q[4], q[5]):
                        tot['udp_3702_bytes'] += n
                    continue
                if not (q[2] or q[3]):
                    continue                                        # neither TCP nor UDP: total only
                prov = any(lo <= int(x) <= hi for x in (q[2], q[3]) if x.isdigit())
                r = by_role.setdefault(role_at(t), {'bytes': 0, 'frames': 0, 'provider_port_bytes': 0, 'notify_bytes': 0})
                r['bytes'] += n
                r['frames'] += 1
                tot['tcp_frames'] += 1
                if prov:
                    r['provider_port_bytes'] += n
                    tot['tcp_provider_bytes'] += n
                else:
                    r['notify_bytes'] += n
                    tot['tcp_notify_bytes'] += n
        per_role_trials = collections.Counter(role for _, role in starts)
        for role, r in by_role.items():
            k = per_role_trials.get(role)
            r['trials'] = k
            r['bytes_per_trial'] = round(r['bytes'] / k) if k else None
            r['provider_port_per_trial'] = round(r['provider_port_bytes'] / k) if k else None
            r['notify_per_trial'] = round(r['notify_bytes'] / k) if k else None
        iface, filt = self.iface, self.capture_filter()
        meta = phase_dir / 'capture_filter.txt'
        if meta.exists():                                   # what this capture was really taken with
            parts = meta.read_text(encoding='utf-8').splitlines()
            if len(parts) >= 2:
                iface, filt = parts[0], parts[1]
        res = {'files': [f.name for f in files], 'size_bytes': sum(f.stat().st_size for f in files),
               'filter': filt, 'interface': iface, **tot, 'by_role': by_role}
        (phase_dir / 'capture_bytes.json').write_text(json.dumps(res, indent=2), encoding='utf-8')
        return res

    def reanalyse(self) -> None:
        """--report-only: recompute journal summaries, phase states and capture_bytes.json of every phase."""
        for phase, st in self.status['phases'].items():
            pdir = self.run_dir / phase
            if not pdir.is_dir():
                continue
            for name, sub in st.get('subruns', {}).items():
                journal = pdir / ('events.jsonl' if name == phase else f'{name}/events.jsonl')
                sub.update(summarise_journal(journal, phase if name == phase else f'smoke_{name}', self.cfg))
            subs = st.get('subruns', {}).values()
            st['done_trials'] = sum(x.get('done', 0) for x in subs)
            st['ok_trials'] = sum(x.get('ok', 0) for x in subs)
            st['failures'] = sum(x.get('failures', 0) for x in subs)
            if st.get('state') in ('ok', 'incomplete'):          # rc != 0 / timeout stays 'failed'
                st['state'] = 'ok' if st['done_trials'] >= st.get('planned_trials', 0) else 'incomplete'
            res = self.analyse_capture(pdir, None if phase == 'smoke' else pdir / 'events.jsonl')
            if res:
                st['capture'] = {phase: {k: res[k] for k in CAP_KEYS}}
        self.save_status()

    # -- resources ----------------------------------------------------------
    def start_resources(self) -> None:
        if self.dry:
            return
        interval = float(self.cfg.get('resources_interval_s', 60))
        path = self.run_dir / 'resources.jsonl'

        def loop():
            prev = _system_times()
            while not self._res_stop.wait(interval):
                cur = _system_times()
                cpu = None
                if prev and cur:
                    idle = cur[0] - prev[0]
                    total = (cur[1] - prev[1]) + (cur[2] - prev[2])
                    cpu = round(100.0 * (1 - idle / total), 1) if total else None
                prev = cur
                mem = _memory_status()
                rec = {'t_abs': time.time(), 'iso': _dt.datetime.now().isoformat(timespec='seconds'),
                       'cpu_percent': cpu, **mem}
                try:
                    with path.open('a', encoding='utf-8') as fh:
                        fh.write(json.dumps(rec) + '\n')
                except Exception:
                    pass
        threading.Thread(target=loop, daemon=True, name='resources').start()

    def stop_resources(self) -> None:
        self._res_stop.set()

    # -- phases -------------------------------------------------------------
    def runner_cmds(self, phase: str) -> list[tuple[str, list[str], pathlib.Path]]:
        """(sub-name, command, journal path) for the phase; smoke has three sub-runs."""
        cfg, p = self.cfg, self.cfg['phases'][phase]
        base = ['--ip', cfg['ip'], '--tls', cfg.get('tls', 'auto')]
        pdir = self.run_dir / phase
        if self.cfg.get('providers', {}).get('source', 'local') == 'remote':
            # step 2: --provider-host for the runners (not implemented in step 1)
            base += ['--provider-host', f"{cfg['providers']['host']}:{cfg['providers']['port']}"]

        def z1(sub: dict, mode: str, journal: pathlib.Path) -> list[str]:
            return [PY, '-u', str(ZIEL1 / 'ziel1_experiment.py'), *base, '--mode', mode, '--room', cfg['room'],
                    '--other-room', cfg.get('other_room', 'ICU-2'), '--trials', sub['trials'],
                    '--port-base', str(sub.get('port_base', 10100)), '--log', str(journal), '--echo-provider']

        def ack(sub: dict, journal: pathlib.Path) -> list[str]:
            return [PY, '-u', str(ZIEL2 / 'ziel2_ack.py'), *base, '--room', cfg['room'],
                    '--accept', str(sub['accept']), '--refuse', str(sub['refuse']),
                    '--port', str(sub.get('port', 10600)), '--log', str(journal), '--echo-provider']

        def fault(sub: dict, journal: pathlib.Path) -> list[str]:
            methods = sub['methods']
            nd = sub.get('netdown', {})
            if nd.get('enabled'):
                methods += f",netdown={nd.get('n', 30)}"   # step 2
            return [PY, '-u', str(ZIEL2 / 'ziel2_fault.py'), *base, '--room', cfg['room'],
                    '--methods', methods, '--port-base', str(sub.get('port_base', 10700)),
                    '--log', str(journal), '--echo-provider']

        if phase == 'smoke':
            return [('ziel1', z1(p['ziel1'], 'filtered', pdir / 'ziel1' / 'events.jsonl'), pdir / 'ziel1' / 'events.jsonl'),
                    ('ack', ack(p['ack'], pdir / 'ack' / 'events.jsonl'), pdir / 'ack' / 'events.jsonl'),
                    ('fault', fault(p['fault'], pdir / 'fault' / 'events.jsonl'), pdir / 'fault' / 'events.jsonl')]
        if phase == 'ziel1_filtered':
            return [('', z1(p, 'filtered', pdir / 'events.jsonl'), pdir / 'events.jsonl')]
        if phase == 'ziel1_baseline':
            return [('', z1(p, 'baseline', pdir / 'events.jsonl'), pdir / 'events.jsonl')]
        if phase == 'ziel2_ack':
            return [('', ack(p, pdir / 'events.jsonl'), pdir / 'events.jsonl')]
        if phase == 'ziel2_fault':
            return [('', fault(p, pdir / 'events.jsonl'), pdir / 'events.jsonl')]
        raise KeyError(phase)

    def run_phase(self, phase: str) -> dict:
        pdir = self.run_dir / phase
        st = {'state': 'running', 'start': _dt.datetime.now().isoformat(timespec='seconds'),
              'planned_trials': planned_trials(phase, self.cfg), 'subruns': {}}
        self.status['phases'][phase] = st
        self.save_status()
        timeout = max(600.0, 2.0 * planned_seconds(phase, self.cfg))
        self.log(f'== Phase {phase}: planned {st["planned_trials"]} trial(s), timeout {timeout / 60:.0f} min')
        if not self.dry:
            if pdir.exists() and any(pdir.iterdir()):      # resume of a failed phase: keep the old attempt aside
                prev = self.run_dir / f'{phase}_prev_{now_ts()}'
                pdir.rename(prev)
                self.log(f'   alter Versuch nach {prev.name} verschoben')
            pdir.mkdir(parents=True, exist_ok=True)
        cap = self.start_capture(pdir) if not self.dry else None
        ok_all = True
        try:
            for sub, cmd, journal in self.runner_cmds(phase):
                name = sub or phase
                self.log(f'   run {name}: ' + ' '.join(cmd[2:]))
                if self.dry:
                    continue
                journal.parent.mkdir(parents=True, exist_ok=True)
                console = journal.parent / 'console.txt'
                rc, timed_out = run_subprocess(cmd, console, timeout)
                summary = summarise_journal(journal, phase if not sub else f'smoke_{sub}', self.cfg)
                st['subruns'][name] = {'rc': rc, 'timed_out': timed_out, **summary}
                if rc != 0 or timed_out:
                    ok_all = False
                    self.warn(f'{phase}/{name}: rc={rc} timed_out={timed_out}')
                self.log(f'   {name}: done={summary.get("done")} ok={summary.get("ok")} '
                         f'failures={summary.get("failures")} background={summary.get("background_count")}')
        finally:
            self.stop_capture(cap)
        if not self.dry:
            res = self.analyse_capture(pdir, None if phase == 'smoke' else pdir / 'events.jsonl')
            if res:
                st['capture'] = {phase: {k: res[k] for k in CAP_KEYS}}
            if self.capture_wanted and not st.get('capture'):
                self.warn(f'{phase}: kein Mitschnitt (tshark fehlt oder Interface nicht gefunden)')
        done = sum(s.get('done', 0) for s in st['subruns'].values())
        st['done_trials'] = done
        st['ok_trials'] = sum(s.get('ok', 0) for s in st['subruns'].values())
        st['failures'] = sum(s.get('failures', 0) for s in st['subruns'].values())
        exp_bg = int(self.cfg.get('background', {}).get('expect', 0))
        bg = max((s.get('background_count', 0) for s in st['subruns'].values()), default=0)
        if exp_bg and bg < exp_bg and not self.dry:
            self.warn(f'{phase}: nur {bg} von {exp_bg} Hintergrund-Provider (C++) entdeckt')
        st['end'] = _dt.datetime.now().isoformat(timespec='seconds')
        if self.dry:
            st['state'] = 'dry'
        elif not ok_all:
            st['state'] = 'failed'
        elif done < st['planned_trials']:
            st['state'] = 'incomplete'
        else:
            st['state'] = 'ok'
        self.save_status()
        self.log(f'== Phase {phase}: {st["state"]}')
        return st


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def read_jsonl(path: pathlib.Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    with path.open(encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def run_subprocess(cmd: list[str], console: pathlib.Path, timeout: float) -> tuple[int, bool]:
    """Run a runner, tee its output to console.txt, kill the process tree on timeout."""
    from harness import _process_tree, _terminate_pid  # Windows helpers (experiments/harness.py)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding='utf-8', errors='replace', bufsize=1)
    timed_out = False
    end = time.monotonic() + timeout
    with console.open('w', encoding='utf-8') as fh:
        def pump():
            for line in proc.stdout:
                fh.write(line)
                fh.flush()
                if line.startswith('[') or line.startswith('    ->') or line.startswith('  trial') or 'TECH-ALARM' in line:
                    print('      ' + line.rstrip()[:160], flush=True)
        t = threading.Thread(target=pump, daemon=True)
        t.start()
        while proc.poll() is None:
            if time.monotonic() > end:
                timed_out = True
                for pid in reversed(_process_tree(proc.pid)):
                    _terminate_pid(pid)
                break
            time.sleep(0.5)
        t.join(timeout=10)
    return (proc.returncode if proc.returncode is not None else -1), timed_out


def summarise_journal(journal: pathlib.Path, kind: str, cfg: dict) -> dict:
    """plan/done/ok/failures + background devices from one phase journal."""
    recs = read_jsonl(journal)
    trial_eprs = {r['epr'] for r in recs if r.get('event') in ('trial_start', 'ack_trial_start', 'fault_injected') and r.get('epr')}
    res: dict = {'done': 0, 'ok': 0, 'failures': 0, 'records': len(recs)}
    if kind.endswith('ziel1') or kind.startswith('ziel1'):
        ends = [r for r in recs if r.get('event') == 'trial_end']
        starts = {r['epr']: r for r in recs if r.get('event') == 'trial_start'}
        res['done'] = len(ends)
        ok = 0
        for e in ends:
            s = starts.get(e['epr'], {})
            exp = s.get('expected')
            dec = [r for r in recs if r.get('epr') == e['epr'] and r.get('event') == 'topology_decision']
            final = dec[-1]['decision'] if dec else None
            created = sum(1 for r in recs if r.get('epr') == e['epr'] and r.get('event') == 'subscription_created')
            psubs = e.get('provider_subscriptions')
            if exp == 'reject':
                good = final == 'reject' and created == 0 and (psubs in (None, 0))
            elif exp == 'unknown':
                good = final == 'unknown' and created >= 1
            else:
                good = final == 'accept' and created >= 1
            ok += 1 if good else 0
        res['ok'] = ok
        res['failures'] = res['done'] - ok
    elif kind.endswith('ack') or kind == 'ziel2_ack':
        ends = [r for r in recs if r.get('event') == 'ack_trial_end']
        res['done'] = len(ends)
        res['ok'] = sum(1 for e in ends if (e.get('policy') == 'accept' and e.get('ack_state') in ('accepted', 'confirmed'))
                        or (e.get('policy') == 'refuse' and e.get('ack_state') == 'failed'))
        res['failures'] = res['done'] - res['ok']
    elif kind.endswith('fault') or kind == 'ziel2_fault':
        ends = [r for r in recs if r.get('event') == 'fault_trial_end']
        res['done'] = len(ends)
        bounds = cfg.get('bounds_s', {})
        ok = 0
        for e in ends:
            b = bounds.get(e.get('method'))
            within = (e.get('dt_alarm_s') is not None) and (b is None or e['dt_alarm_s'] <= b)
            ok += 1 if (e.get('outcome') == 'ok' and within) else 0
        res['ok'] = ok
        res['failures'] = res['done'] - ok
    # background devices: discovered but not a trial device
    bg = {}
    for r in recs:
        epr = r.get('epr')
        if not epr or epr in trial_eprs:
            continue
        if r.get('event') == 'device_discovered':
            bg.setdefault(epr, {'room': r.get('room'), 'decision': None, 'subscribed': False})
        elif r.get('event') == 'topology_decision' and epr in bg:
            bg[epr]['decision'] = f"{r.get('decision')}@{r.get('phase')}"
        elif r.get('event') == 'subscription_created' and epr in bg:
            bg[epr]['subscribed'] = True
    res['background_count'] = len(bg)
    res['background'] = {e[-12:]: v for e, v in bg.items()}
    return res


# -- Win32 resource sampling (no psutil) -------------------------------------

def _system_times():
    if os.name != 'nt':
        return None
    import ctypes.wintypes as wt

    class FT(ctypes.Structure):
        _fields_ = [('lo', wt.DWORD), ('hi', wt.DWORD)]
    idle, kern, user = FT(), FT(), FT()
    if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
        return None
    f = lambda x: (x.hi << 32) | x.lo  # noqa: E731
    return f(idle), f(kern), f(user)


def _memory_status() -> dict:
    if os.name != 'nt':
        return {}
    import ctypes.wintypes as wt

    class MS(ctypes.Structure):
        _fields_ = [('dwLength', wt.DWORD), ('dwMemoryLoad', wt.DWORD), ('ullTotalPhys', ctypes.c_ulonglong),
                    ('ullAvailPhys', ctypes.c_ulonglong), ('ullTotalPageFile', ctypes.c_ulonglong),
                    ('ullAvailPageFile', ctypes.c_ulonglong), ('ullTotalVirtual', ctypes.c_ulonglong),
                    ('ullAvailVirtual', ctypes.c_ulonglong), ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
    ms = MS()
    ms.dwLength = ctypes.sizeof(MS)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
        return {}
    return {'mem_percent': int(ms.dwMemoryLoad), 'mem_used_mb': int((ms.ullTotalPhys - ms.ullAvailPhys) / 2**20),
            'mem_total_mb': int(ms.ullTotalPhys / 2**20)}


# ──────────────────────────────────────────────────────────────────────────────
# Analysis, report, dossier
# ──────────────────────────────────────────────────────────────────────────────

def run_analyzers(c: Campaign) -> dict:
    """Call the existing analyzers; returns {'ziel1': json, 'ziel2': json} (missing parts -> None)."""
    out: dict = {'ziel1': None, 'ziel2': None}
    rd = c.run_dir
    jf, jb = rd / 'ziel1_filtered' / 'events.jsonl', rd / 'ziel1_baseline' / 'events.jsonl'
    if jf.exists():
        cmd = [PY, str(ZIEL1 / 'ziel1_analyze.py'), str(jf), '--per-trial', '--json', str(rd / 'ziel1_filtered' / 'analysis.json')]
        if jb.exists():
            cmd += ['--baseline', str(jb)]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        (rd / 'ziel1_filtered' / 'analysis.txt').write_text(res.stdout, encoding='utf-8')
        try:
            out['ziel1'] = json.loads((rd / 'ziel1_filtered' / 'analysis.json').read_text(encoding='utf-8'))
        except Exception as exc:
            c.warn(f'ziel1_analyze: {exc}')
    ja, jfault = rd / 'ziel2_ack' / 'events.jsonl', rd / 'ziel2_fault' / 'events.jsonl'
    js = [str(p) for p in (ja, jfault) if p.exists()]
    if js:
        cmd = [PY, str(ZIEL2 / 'ziel2_analyze.py'), *js, '--per-trial', '--json', str(rd / 'ziel2_analysis.json')]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        (rd / 'ziel2_analysis.txt').write_text(res.stdout, encoding='utf-8')
        try:
            out['ziel2'] = json.loads((rd / 'ziel2_analysis.json').read_text(encoding='utf-8'))
        except Exception as exc:
            c.warn(f'ziel2_analyze: {exc}')
    return out


def trial_only_subscriptions(z1: dict | None) -> dict:
    """created/avoided over trial EPRs only (background C++ devices excluded)."""
    if not z1:
        return {}
    per = z1.get('per_epr', {})
    tr = [s for s in per.values() if s.get('role')]
    return {'created': sum(s.get('created', 0) for s in tr), 'avoided': sum(s.get('avoided', 0) for s in tr),
            'provider_checked': sum(1 for s in tr if s.get('provider_subscriptions') is not None),
            'eprs': len(tr)}


def build_report(c: Campaign, ana: dict, problems: list[str] | None = None, phases: tuple = PHASE_ORDER) -> str:
    """problems/phases default to the local campaign rules; netmode passes its own."""
    st = c.status['phases']
    states = {p: s.get('state') for p, s in st.items()}
    if problems is None:
        problems = [f'{p}: {s}' for p, s in states.items() if s not in ('ok', 'dry')]
        for p, s in st.items():
            if s.get('failures'):
                problems.append(f"{p}: {s['failures']} fehlgeschlagene Versuche")
        if c.pilot:
            problems = pilot_verdict(c, ana)
    if c.pilot:
        head = 'PILOT: OK' if not problems else 'PILOT: PROBLEME'
    else:
        head = 'GESAMT: OK' if not problems else 'GESAMT: FEHLER'
    lines = [head, '', f"Lauf: {c.run_dir}   gestartet {c.status.get('started')}   Pilot: {c.pilot}", '']
    if problems:
        lines += ['Probleme:'] + [f'- {x}' for x in problems] + ['']
    if c.warnings:
        lines += ['Warnungen:'] + [f'- {w}' for w in c.warnings] + ['']
    lines += ['| Phase | Status | geplant | gemacht | ok | Fehler | Dauer | Hintergrund (C++) |', '|---|---|---|---|---|---|---|---|']
    for p in phases:
        s = st.get(p)
        if not s:
            lines.append(f'| {p} | - | | | | | | |')
            continue
        dur = ''
        try:
            dur = str(_dt.datetime.fromisoformat(s['end']) - _dt.datetime.fromisoformat(s['start'])).split('.')[0]
        except Exception:
            pass
        bg = max((r.get('background_count', 0) for r in s.get('subruns', {}).values()), default=0)
        lines.append(f"| {p} | {s.get('state')} | {s.get('planned_trials')} | {s.get('done_trials')} | {s.get('ok_trials')} | "
                     f"{s.get('failures')} | {dur} | {bg} |")
    z1, z2 = ana.get('ziel1'), ana.get('ziel2')
    lines.append('')
    if z1:
        sub = trial_only_subscriptions(z1)
        lines += ['Ziel 1 (filtered):',
                  f"- TRA = {z1['bettfremd_correct']}/{z1['bettfremd_n']} = {pct(z1['tra'])}, untere 95 %-Grenze einseitig {pct(z1['tra_lower_95_one_sided'])}",
                  f"- Fehlabweisungen own: {z1['false_rejects_own']}, nolocation (unknown): {z1['nolocation_n']}, "
                  f"foreign spät (Precheck): {z1['by_role'].get('foreign', {}).get('late', 0)}",
                  f"- Subskriptionen (nur Versuchsgeräte): erzeugt {sub.get('created')}, vermieden {sub.get('avoided')}, "
                  f"Provider-status geprüft {sub.get('provider_checked')}/{sub.get('eprs')}, Abweichungen {len(z1['provider_disagreements'])}"]
        if z1.get('baseline'):
            b = z1['baseline']
            lines.append(f"- Baseline: {b['eprs']} Geräte, {b['subscriptions_created']} Subskriptionen erzeugt")
        lines.append('')
    if z2 and z2.get('ack'):
        a = z2['ack']['accept']
        lines += ['Ziel 2 Quittierung:',
                  f"- accept: {a['ok']}/{a['transactions']} = {pct(a['success_rate'])}, untere 95 %-Grenze {pct(a['success_lower_95_one_sided'])}; "
                  f"RTT median {fmt(a['rtt_ms_median'], ' ms')} p95 {fmt(a['rtt_ms_p95'], ' ms')} max {fmt(a['rtt_ms_max'], ' ms')}",
                  f"- refuse: {z2['ack']['refuse']['failed']}/{z2['ack']['refuse']['transactions']} als Fail erkannt; bestätigt {z2['ack']['accept_confirmed']}/{z2['ack']['trials_accept']}; late success {z2['ack']['late_success']}",
                  '']
    if z2 and z2.get('fault'):
        lines.append('Ziel 2 Verbindungsverlust (loss -> technical alarm):')
        for m, v in z2['fault']['methods'].items():
            lines.append(f"- {m}: n={v['measured']}/{v['n']} median {fmt(v['alarm_s']['median'], ' s')} max {fmt(v['alarm_s']['max'], ' s')}"
                         + (f", Schranke {v['bound_s']} s: {v['within_bound']}/{v['measured']} innerhalb, untere 95 %-Grenze {pct(v['within_bound_lower_95_one_sided'])}"
                            if v.get('bound_s') is not None else '')
                         + (f"; reconnect ok {v['reconnect_s']['ok']}/{v['reconnect_s']['n']} median {fmt(v['reconnect_s']['median'], ' s')}" if 'reconnect_s' in v else ''))
        lines.append('')
    # capture
    caps = [(p, k, v) for p, s in st.items() for k, v in (s.get('capture') or {}).items()]
    if caps:
        lines.append('Mitschnitt (tshark):')
        for p, k, v in caps:
            lines.append(f"- {p if k == p else p + '/' + k}: {v['size_bytes'] / 2**20:.1f} MB Datei, {v['bytes']} B gesamt; UDP/Discovery {v['udp_bytes']} B "
                         f"(davon Port 3702 {v['udp_3702_bytes']} B); TCP Provider-Port {v['tcp_provider_bytes']} B; TCP Benachrichtigungen {v['tcp_notify_bytes']} B")
        lines.append('')
    # background
    for p, s in st.items():
        for k, r in s.get('subruns', {}).items():
            if r.get('background'):
                lines.append(f"Hintergrundgeräte in {p}/{k}: " + ', '.join(f"..{e} room={v['room']} {v['decision']} subscribed={v['subscribed']}" for e, v in r['background'].items()))
    return '\n'.join(lines) + '\n'


def build_dossier(c: Campaign, ana: dict) -> str:
    cfg, st = c.cfg, c.status['phases']
    z1, z2 = ana.get('ziel1'), ana.get('ziel2')
    src = cfg.get('providers', {}).get('source', 'local')
    where = ('Dashboard und Python-Provider auf PC 1 (ein Host, Loopback)' if src == 'local'
             else f"Dashboard auf PC 1 ({cfg['ip']}), Python-Provider auf PC 2 ({cfg['providers'].get('host')}) über das Netz")
    phases_row = ', '.join(f"{p} ({st[p].get('state')})" for p in PHASE_ORDER if p in st)
    L = [f"# Kampagne {'(PILOT) ' if c.pilot else ''}— Ergebnisdossier Ziel 1 + Ziel 2",
         '', f"Lauf `{c.run_dir.name}`, gestartet {c.status.get('started')}. Alle Zahlen aus den Journalen und Analysen dieses Laufs "
         "(`campaign_status.json`, `*/analysis.json`, `ziel2_analysis.json`); Grenzen = untere einseitige 95 %-Grenze nach "
         "Clopper–Pearson. Absätze unter „Entwurf“ sind Textvorschläge.", '',
         '## 1. Messaufbau', '', '| Element | Wert |', '|---|---|',
         f'| Aufbau | {where} |',
         f"| Dashboard | `SdcMyConsumer` headless, `--room {cfg['room']}` (Baseline: kein Raum), TLS `{cfg.get('tls', 'auto')}`, PKI `pki/dashboard`; ein Prozess und ein Journal je Phase |",
         f"| Provider (gemessen) | `testbed_provider.py` (sdc11073 3.0.0), frische EPR je Versuch, mTLS aus `pki/<gerät>`; Rollen own/foreign/nolocation/mismatch wie `experiments/ziel1/README.md` |",
         f"| Hintergrund (nicht gemessen) | {cfg.get('background', {}).get('expect', 0)} C++-Provider (zweiter SDC-Stack, VirtualBox bridged), `--room {cfg.get('background', {}).get('room')}` / `{cfg.get('background', {}).get('patient')}`, nur Discovery/Entscheidung im Journal |",
         f"| Mitschnitt | {'tshark auf ' + str(c.iface) + ', Filter `' + c.capture_filter() + '`' if c.tshark and c.iface else 'keiner'} |",
         f"| Phasen | {phases_row} |", '']
    L += ['## 2. Messgrößen', '',
          'TRA = korrekt abgewiesene bettfremde Versuche (foreign + mismatch) / alle bettfremden Versuche; korrekt = letzte Entscheidung `reject`, '
          'keine `subscription_created`, Provider `subscriptions = 0`. `unknown` (nolocation) nicht im Nenner. Quittierung: Erfolg = InvocationState Fin/FinMod je Transaktion; '
          'p95 der RTT über erfolgreiche Transaktionen. Rückfall: Δt = `technical_alarm{raised}` − t_loss gegen die Schranke der Verlustart '
          f"({', '.join(f'{k} {v:.0f} s' for k, v in cfg.get('bounds_s', {}).items())}). Alle Zeitstempel auf PC 1.", '']
    if z1:
        sub = trial_only_subscriptions(z1)
        L += ['## 3. Ergebnisse Ziel 1', '', '**Tabelle — Entscheidungen je Rolle (gefiltert)**', '',
              '| Rolle | n | korrekt | falsch | letzte Entscheidung |', '|---|---|---|---|---|']
        for r, b in z1['by_role'].items():
            L.append(f"| {r} | {b['n']} | {b['correct']} | {b['wrong']} | {', '.join(f'{k}={v}' for k, v in b.get('final_phase', {}).items())} |")
        L += ['', '**Tabelle — TRA**', '', '| Größe | Wert |', '|---|---|',
              f"| bettfremde Versuche | {z1['bettfremd_n']} |", f"| korrekt abgewiesen | {z1['bettfremd_correct']} |",
              f"| TRA | {pct(z1['tra'])} |", f"| untere 95 %-Grenze (einseitig) | {pct(z1['tra_lower_95_one_sided'])} |",
              f"| Fehlabweisungen own | {z1['false_rejects_own']} |", f"| nolocation (unknown) | {z1['nolocation_n']} |",
              f"| Subskriptionen erzeugt / vermieden (Versuchsgeräte) | {sub.get('created')} / {sub.get('avoided')} |",
              f"| Provider-status geprüft / Abweichungen | {sub.get('provider_checked')} / {len(z1['provider_disagreements'])} |"]
        if z1.get('baseline'):
            b = z1['baseline']
            L += [f"| Baseline (ohne Filter) | {b['eprs']} Geräte, {b['subscriptions_created']} Subskriptionen |"]
        caps = {k: v for p in ('ziel1_filtered', 'ziel1_baseline') for k, v in (st.get(p, {}).get('capture') or {}).items()}
        if caps:
            L += ['', '**Tabelle — Mitschnitt (Bytes je Lauf)**', '',
                  '| Lauf | gesamt | UDP (Discovery) | TCP Provider-Port (Anfrage/Antwort) | TCP Benachrichtigungen |', '|---|---|---|---|---|']
            for k, v in caps.items():
                L.append(f"| {k} | {v['bytes']} | {v['udp_bytes']} | {v['tcp_provider_bytes']} | {v['tcp_notify_bytes']} |")
            for p in ('ziel1_filtered', 'ziel1_baseline'):
                f = c.run_dir / p / 'capture_bytes.json'
                if f.exists():
                    cb = json.loads(f.read_text(encoding='utf-8'))
                    L += ['', f'**Tabelle — TCP-Bytes je Rolle ({p}; Zuordnung über das Zeitfenster des Versuchs)**', '',
                          '| Rolle | Versuche | Bytes | je Versuch | Provider-Port | Benachrichtigungen |', '|---|---|---|---|---|---|']
                    for r, v in cb.get('by_role', {}).items():
                        L.append(f"| {r} | {v.get('trials') or '-'} | {v['bytes']} | {fmt(v.get('bytes_per_trial'))} | {v['provider_port_bytes']} | {v['notify_bytes']} |")
        L.append('')
    if z2 and z2.get('ack'):
        a, rf, k = z2['ack']['accept'], z2['ack']['refuse'], z2['ack']
        L += ['## 4. Ergebnisse Ziel 2 — Fernquittierung', '', '| Block | Transaktionen | Fin | Fail | Timeout | Erfolgsquote | untere 95 %-Grenze |', '|---|---|---|---|---|---|---|',
              f"| ack accept | {a['transactions']} | {a['ok']} | {a['by_outcome'].get('failed', 0)} | {a['by_outcome'].get('timeout', 0)} | {pct(a['success_rate'])} | {pct(a['success_lower_95_one_sided'])} |",
              f"| ack refuse (Kontrolle) | {rf['transactions']} | {rf['ok']} | {rf['failed']} | 0 | {pct(rf['success_rate'])} (erwartet 0) | — |", '',
              f"RTT (ok, n = {a['ok']}): median {fmt(a['rtt_ms_median'], ' ms')}, p95 {fmt(a['rtt_ms_p95'], ' ms')}, max {fmt(a['rtt_ms_max'], ' ms')}, min {fmt(a['rtt_ms_min'], ' ms')}. "
              f"Bestätigt (Presence=Ack): {k['accept_confirmed']}/{k['trials_accept']}; Bestätigungslatenz median {fmt(k['confirm_latency_ms_median'], ' ms')}, p95 {fmt(k['confirm_latency_ms_p95'], ' ms')}; late success {k['late_success']}.", '']
    if z2 and z2.get('fault'):
        L += ['## 5. Ergebnisse Ziel 2 — Verbindungsverlust', '', '| Verlustart | n | Ursache | median | p95 | max | Schranke | innerhalb | untere 95 %-Grenze |', '|---|---|---|---|---|---|---|---|---|']
        for m, v in z2['fault']['methods'].items():
            L.append(f"| {m} | {v['measured']}/{v['n']} | {', '.join(f'{a}={b}' for a, b in v['causes'].items())} | {fmt(v['alarm_s']['median'], ' s', 2)} | {fmt(v['alarm_s']['p95'], ' s', 2)} | {fmt(v['alarm_s']['max'], ' s', 2)} | "
                     f"{fmt(v['bound_s'], ' s', 0) if v.get('bound_s') is not None else '—'} | {v['within_bound'] if v.get('bound_s') is not None else '—'} | {pct(v['within_bound_lower_95_one_sided']) if v.get('bound_s') is not None else '—'} |")
        rc = next((v['reconnect_s'] for v in z2['fault']['methods'].values() if 'reconnect_s' in v), None)
        if rc:
            L.append(f"\nRückkehr derselben EPR: {rc['ok']}/{rc['n']} wieder gebunden, Alarm aufgehoben nach median {fmt(rc['median'], ' s')} (max {fmt(rc['max'], ' s')}).")
        L.append('')
    bgs = {}
    for p, s in st.items():
        for k, r in s.get('subruns', {}).items():
            for e, v in (r.get('background') or {}).items():
                bgs.setdefault(e, []).append(f"{p}: {v['decision']}{' +sub' if v['subscribed'] else ''}")
    L += ['## 6. Hintergrundgeräte (C++)', '', ('keine entdeckt' if not bgs else '\n'.join(f'- ..{e}: ' + '; '.join(v) for e, v in bgs.items())), '']
    L += ['## 7. Grenzen', '',
          f"- {'Ein Host: Provider und Dashboard auf PC 1, Verkehr über Loopback (Npcap-Loopback-Mitschnitt).' if src == 'local' else 'Zwei Hosts: Python-Provider auf PC 2; C++-Provider nur als Hintergrund.'}",
          '- Mitschnitt: TCP-Verkehr wird über das Zeitfenster des jeweiligen Versuchs der Rolle zugeordnet (Versuche laufen nacheinander); '
          '„Provider-Port“ = Anfrage/Antwort (Metadaten/WSDL beim Verbinden, GetContextStates, GetMdib, Subscribe, Renew, Set*), '
          '„Benachrichtigungen“ = Verbindungen des Providers zum Ereignis-Port des Dashboards; UDP = Discovery (Multicast unter Port 3702, Unicast-Antworten auf flüchtigen Ports). '
          'Die Verbindungsaufnahme vor dem Precheck (Metadaten, WSDL) fällt auch bei abgewiesenen mismatch-Geräten an; die Einsparung liegt in den vermiedenen Subskriptionen (Benachrichtigungen, Renew).',
          '- C++-Provider: nicht gesteuert, nicht gemessen (kein `--scope-room`, kein `subscriptions=` im `status`); nur Discovery und Entscheidung protokolliert.',
          '- `netdown` (realer Netzverlust) ' + ('nicht Teil dieses Laufs (Schritt 2).' if not cfg['phases']['ziel2_fault'].get('netdown', {}).get('enabled') else 'enthalten.'),
          '- Grenzen bei n = 30 je Verlustart: „alle innerhalb der Schranke“ mit 90,5 % Konfidenz; n = 300 für 99 %.',
          '', '## 8. Dateien', '', f'`{c.run_dir}`: `campaign_status.json`, `campaign_report.md`, `resources.jsonl`, je Phase `events.jsonl`, `console.txt`, `analysis.*`, `capture*.pcapng`, `capture_bytes.json`.', '']
    if z1 and z2 and z2.get('ack') and z2.get('fault') and not c.pilot:
        a = z2['ack']['accept']
        L += ['## 9. Entwurf — Kapitel 5 (ein Absatz)', '',
              f"Im Kampagnenlauf wurden {z1['bettfremd_correct']} von {z1['bettfremd_n']} bettfremden Geräten abgewiesen (TRA {pct(z1['tra'])}, untere 95 %-Grenze {pct(z1['tra_lower_95_one_sided'])}), "
              f"{a['ok']} von {a['transactions']} Quittierungen angenommen (untere Grenze {pct(a['success_lower_95_one_sided'])}, RTT p95 {fmt(a['rtt_ms_p95'], ' ms')}), und der Verbindungsverlust lag in allen Verlustarten innerhalb der hergeleiteten Schranken "
              + '; '.join(f"{m} max {fmt(v['alarm_s']['max'], ' s')} (Schranke {fmt(v['bound_s'], ' s', 0)})" for m, v in z2['fault']['methods'].items() if v.get('bound_s') is not None) + '.', '']
    return '\n'.join(L)


def pilot_verdict(c: Campaign, ana: dict) -> list[str]:
    problems = []
    for p in PHASE_ORDER:
        s = c.status['phases'].get(p)
        if not s:
            problems.append(f'{p}: nicht gelaufen')
            continue
        if s.get('state') != 'ok':
            problems.append(f"{p}: Status {s.get('state')}")
        if s.get('done_trials', 0) < s.get('planned_trials', 0):
            problems.append(f"{p}: {s.get('done_trials')} von {s.get('planned_trials')} Versuchen")
        if s.get('failures'):
            problems.append(f"{p}: {s['failures']} fehlgeschlagene Versuche")
        if c.capture_wanted and not s.get('capture'):
            problems.append(f'{p}: kein Mitschnitt')
    z1, z2 = ana.get('ziel1'), ana.get('ziel2')
    if not z1:
        problems.append('Ziel-1-Analyse fehlt')
    elif z1['bettfremd_n'] and z1['tra'] < 1.0:
        problems.append(f"Ziel 1: TRA {pct(z1['tra'])} im Pilot")
    if not z2 or not z2.get('ack') or not z2.get('fault'):
        problems.append('Ziel-2-Analyse unvollständig')
    return problems   # missing C++ background devices are a warning (c.warnings), not a problem


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def newest_run_dir() -> pathlib.Path | None:
    runs = sorted((p for p in LOG_DIR.glob('*') if p.is_dir() and (p / 'campaign_status.json').exists()),
                  key=lambda p: p.stat().st_mtime)
    return runs[-1] if runs else None


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
    except Exception:
        pass
    ap = argparse.ArgumentParser(description='Ziel 1 + Ziel 2 campaign on PC 1')
    ap.add_argument('--config', type=pathlib.Path, default=HERE / 'campaign.json')
    ap.add_argument('--pilot', action='store_true', help='small n for every phase, verdict PILOT: OK | PROBLEME')
    ap.add_argument('--resume', nargs='?', const='newest', help='continue a run (newest or given run dir)')
    ap.add_argument('--report-only', nargs='?', const='newest',
                    help='no phases: re-run capture analysis, analyzers, report and dossier of an existing run')
    ap.add_argument('--run-dir', type=pathlib.Path)
    ap.add_argument('--phases', help='comma list subset of ' + ','.join(PHASE_ORDER))
    ap.add_argument('--no-capture', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--listen', action='store_true', help='variant C, PC 1: dashboard windows + capture until PC 2 reports done (netmode.py)')
    ap.add_argument('--merge', nargs='?', const='newest', help='variant C: merge a --listen run with the PC 2 journal (--pc2) and analyse')
    ap.add_argument('--pc2', help='PC 2 journal (pc2_journal.jsonl) or the folder that holds it')
    ap.add_argument('--selftest-merge', action='store_true', help='synthetic PC 1 + PC 2 journals through --merge')
    a = ap.parse_args(argv)
    a.config = a.config.resolve()                 # the runners' dashboard chdirs into Dashboard/:
    if a.run_dir:                                # every path handed to them must be absolute
        a.run_dir = a.run_dir.resolve()
    for opt in ('resume', 'report_only', 'merge'):
        v = getattr(a, opt)
        if v and v != 'newest':
            setattr(a, opt, str(pathlib.Path(v).resolve()))

    cfg = load_config(a.config, a.pilot)
    cfg['_config_path'] = str(a.config)
    if a.listen or a.merge or a.selftest_merge:          # variant C (two PCs) lives in netmode.py
        import netmode
        if a.selftest_merge:
            return netmode.selftest_merge(cfg, a)
        return netmode.listen(cfg, a) if a.listen else netmode.merge(cfg, a)
    if a.resume or a.report_only:
        sel = a.resume or a.report_only
        run_dir = pathlib.Path(sel) if sel != 'newest' else newest_run_dir()
        if not run_dir or not (run_dir / 'campaign_status.json').exists():
            print('nothing to resume')
            return 2
        prev = json.loads((run_dir / 'campaign_status.json').read_text(encoding='utf-8'))
        a.pilot = bool(prev.get('pilot', a.pilot))
        cfg = load_config(a.config, a.pilot)
        cfg['_config_path'] = str(a.config)
    else:
        run_dir = a.run_dir or LOG_DIR / (('pilot_' if a.pilot else 'run_') + now_ts())
    if not a.dry_run:
        run_dir.mkdir(parents=True, exist_ok=True)
    c = Campaign(cfg, run_dir, a.pilot, a.dry_run, capture=not a.no_capture)
    c.log(f"Kampagne {'PILOT ' if a.pilot else ''}{'DRY-RUN ' if a.dry_run else ''}run_dir={run_dir} providers={cfg['providers'].get('source')} "
          f"tshark={'ja (' + str(c.iface) + ')' if c.tshark and c.iface else 'nein'}")
    if c.capture_wanted and not c.tshark:
        c.warn('tshark nicht gefunden - kein Mitschnitt')
    elif c.capture_wanted and not c.iface:
        c.warn('tshark gefunden, aber kein Capture-Interface bestimmt - kein Mitschnitt')
    phases = [] if a.report_only else [p for p in PHASE_ORDER if not a.phases or p in a.phases.split(',')]
    c.start_resources()
    try:
        for phase in phases:
            prev = c.status['phases'].get(phase)
            if a.resume and prev and prev.get('state') == 'ok':
                c.log(f'== Phase {phase}: bereits ok (resume) - übersprungen')
                continue
            c.run_phase(phase)
    except KeyboardInterrupt:
        c.log('Ctrl-C: Kampagne abgebrochen; --resume setzt fort')
        c.save_status()
        return 130
    finally:
        c.stop_resources()
    if a.dry_run:
        return 0
    if a.report_only:
        c.reanalyse()
    ana = run_analyzers(c)
    report = build_report(c, ana)
    (run_dir / 'campaign_report.md').write_text(report, encoding='utf-8')
    dossier = build_dossier(c, ana)
    (run_dir / 'dossier.md').write_text(dossier, encoding='utf-8')
    if not a.pilot:
        target = REPO / cfg.get('dossier_target', 'docs/thesis/campaign_2pc_ergebnisse.md')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(dossier, encoding='utf-8')
        c.log(f'Dossier: {target}')
    c.status['finished'] = _dt.datetime.now().isoformat(timespec='seconds')
    c.save_status()
    print('\n' + report)
    if a.pilot:
        problems = pilot_verdict(c, ana)
        print('PILOT: OK' if not problems else 'PILOT: PROBLEME\n- ' + '\n- '.join(problems))
        if c.warnings:
            print('Warnungen (kein Abbruchgrund):\n- ' + '\n- '.join(c.warnings))
    print(f'\nReport : {run_dir / "campaign_report.md"}\nDossier: {run_dir / "dossier.md"}\nStatus : {run_dir / "campaign_status.json"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
