"""pc2_scheduler.py -- autonomous provider-side scenario on PC 2 (variant C of the two-PC night run).

PC 2 runs this script, PC 1 runs ``campaign.py --listen``.  There is NO control channel: PC 2
executes the schedule of ``schedule_c.json`` on its own and writes its own journal; PC 1 only
observes with the headless dashboard and acknowledges alarms.  The journals are matched after
the run by ``campaign.py --merge`` (EPR + time, clock offset from the UDP exchange below).

What it does, phase by phase (see SPEC_C.md):
    z1_filtered / z1_baseline   one fresh provider per trial (role own / foreign / mismatch /
                                nolocation, fresh EPR, own port), held for a fixed window,
                                'status' (subscriptions=N) queried, then graceful shutdown
    z2_ack                      one ECG provider; cycles of 'alarm <ch> on' / 'alarm <ch> off'
                                (PC 1 acknowledges), first with 'ack accept', then 'ack refuse'
    z2_fault                    one fresh provider per trial, injected only when the provider
                                itself reports subscriptions >= 1 (PC 1 is bound):
                                kill | shutdown | suspend | reconnect | netdown (firewall rule
                                "block 192.168.0.106" in + out, removed by this script AND by
                                a detached watchdog process even if this script dies)
Clock exchange: UDP responder on ``clock_port``; PC 1 sends {"t1": ..}, PC 2 answers with
t2 (receive) / t3 (send) and the current phase / state, so PC 1 learns the clock offset AND
when the scenario switches to the baseline window or ends.

Journal (PC 2 clock, JSON Lines, <bundle>/logs/<scenario_id>/pc2_journal.jsonl):
    scenario_start, phase_start, trial_start, provider_ready, bound, status, alarm,
    ack_policy, fault_injected{t_loss}, fault_restore, reconnect_started, trial_end,
    netdown_rule, clock_reply, phase_end, window_switch, scenario_end
Usage (bundle root, i.e. the folder that holds experiments/, testbed/, pki/):
    python experiments\\campaign_2pc\\pc2_scheduler.py --selftest
    python experiments\\campaign_2pc\\pc2_scheduler.py --pilot           (~10 min)
    python experiments\\campaign_2pc\\pc2_scheduler.py                   (full night)
    python experiments\\campaign_2pc\\pc2_scheduler.py --netdown-clear   (remove leftover rules)
    python experiments\\campaign_2pc\\pc2_scheduler.py --netdown-test 5  (rule on for 5 s, check removal)
Options: --ip 192.168.0.105 --pc1 192.168.0.106 --schedule PATH --log-dir PATH --phases a,b
         --netdown-mode firewall|skip --wait-pc1-min N --dry-run --no-echo-provider
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as _dt
import json
import os
import pathlib
import platform
import socket
import subprocess
import sys
import threading
import time
import uuid

HERE = pathlib.Path(__file__).resolve().parent            # <root>/experiments/campaign_2pc
ROOT = HERE.parents[1]                                    # bundle root (pc2_c) or the repo
sys.path.insert(0, str(HERE.parent))                      # experiments/ -> harness.py
from harness import DEVICES, Prov, provider_cmd, status_subscriptions, ts  # noqa: E402

RULE_OUT = 'SDC_C_NETDOWN_OUT'
RULE_IN = 'SDC_C_NETDOWN_IN'
BETTFREMD = ('foreign', 'mismatch')


# ──────────────────────────────────────────────────────────────────────────────
# Small helpers
# ──────────────────────────────────────────────────────────────────────────────

class Journal:
    def __init__(self, path: pathlib.Path):
        self.path = path
        self._fh = path.open('a', encoding='utf-8')
        self._lock = threading.Lock()
        self.seq = 0

    def emit(self, event: str, **fields) -> dict:
        now = time.time()
        rec = {'seq': self.seq, 't_abs': now, 't_mono': time.monotonic(),
               'iso': _dt.datetime.fromtimestamp(now).isoformat(timespec='milliseconds'), 'event': event, **fields}
        with self._lock:
            self.seq += 1
            self._fh.write(json.dumps(rec, default=str) + '\n')
            self._fh.flush()
        return rec

    def close(self) -> None:
        self._fh.close()


class Tee:
    """stdout -> console AND console.txt (provider echo lines included)."""
    def __init__(self, path: pathlib.Path, orig):
        self._fh = path.open('a', encoding='utf-8')
        self._orig = orig

    def write(self, s: str) -> None:
        self._orig.write(s)
        self._fh.write(s)

    def flush(self) -> None:
        self._orig.flush()
        self._fh.flush()


def log(msg: str) -> None:
    print(f'[{ts()}] {msg}', flush=True)


def parse_counts(spec: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for part in spec.split(','):
        part = part.strip()
        if part:
            k, _, v = part.partition('=')
            out[k.strip()] = int(v or 1)
    return out


def make_plan(counts: dict[str, int]) -> list[str]:
    """round-robin interleave (same as ziel1_experiment.make_plan / ziel2_fault)"""
    remaining = dict(counts)
    plan: list[str] = []
    while any(remaining.values()):
        for k in list(remaining):
            if remaining[k] > 0:
                plan.append(k)
                remaining[k] -= 1
    return plan


def load_schedule(path: pathlib.Path, pilot: bool) -> dict:
    sch = json.loads(path.read_text(encoding='utf-8'))
    if pilot:
        for name, over in sch.get('pilot', {}).items():
            if name == 'timing':
                sch['timing'].update(over)
            else:
                for ph in sch['phases']:
                    if ph['name'] == name:
                        ph.update(over)
    sch['_pilot'] = pilot
    return sch


# ── firewall (netdown) ───────────────────────────────────────────────────────

def netsh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(['netsh', 'advfirewall', 'firewall', *args], capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=60)


def netdown_add(pc1: str) -> bool:
    r1 = netsh('add', 'rule', f'name={RULE_OUT}', 'dir=out', 'action=block', f'remoteip={pc1}', 'enable=yes')
    r2 = netsh('add', 'rule', f'name={RULE_IN}', 'dir=in', 'action=block', f'remoteip={pc1}', 'enable=yes')
    return r1.returncode == 0 and r2.returncode == 0


def netdown_clear() -> None:
    netsh('delete', 'rule', f'name={RULE_OUT}')
    netsh('delete', 'rule', f'name={RULE_IN}')


def netdown_exists() -> bool:
    out = netsh('show', 'rule', f'name={RULE_OUT}').stdout + netsh('show', 'rule', f'name={RULE_IN}').stdout
    return RULE_OUT in out or RULE_IN in out


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def firewall_state() -> str | None:
    """'ON' / 'OFF' of the current profile, None if unknown."""
    try:
        out = subprocess.run(['netsh', 'advfirewall', 'show', 'currentprofile'], capture_output=True, text=True,
                             encoding='utf-8', errors='replace', timeout=30).stdout
    except Exception:
        return None
    for line in out.splitlines():
        if line.strip().lower().startswith('state'):
            return 'ON' if 'ON' in line.upper().split()[-1] else 'OFF'
    return None


def start_watchdog(seconds: float, log_path: pathlib.Path) -> int | None:
    """Detached process that deletes the netdown rules after `seconds`, whatever happens here."""
    try:
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        p = subprocess.Popen([sys.executable, str(pathlib.Path(__file__).resolve()), '--netdown-watchdog',
                              str(seconds), '--watchdog-log', str(log_path)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=flags, close_fds=True)
        return p.pid
    except Exception:
        return None


def watchdog_main(seconds: float, log_path: pathlib.Path | None) -> int:
    time.sleep(seconds)
    netdown_clear()
    left = netdown_exists()
    if log_path:
        try:
            with log_path.open('a', encoding='utf-8') as fh:
                fh.write(f'{_dt.datetime.now().isoformat(timespec="seconds")} watchdog after {seconds:.0f}s: '
                         f'rules cleared, still present={left}\n')
        except Exception:
            pass
    return 0


# ── clock responder ──────────────────────────────────────────────────────────

def udp_no_reset(sock: socket.socket) -> None:
    """Windows: a UDP socket raises ConnectionResetError on recv after an ICMP 'port unreachable'
    answered one of its earlier sends (e.g. the peer closed its socket); switch that off."""
    try:
        sock.ioctl(socket.SIO_UDP_CONNRESET, False)
    except (AttributeError, OSError):
        pass


class ClockResponder(threading.Thread):
    """UDP: request {"t1": pc1_time, "seq": n} -> reply with t2/t3 and the scenario state."""
    def __init__(self, ip: str, port: int, state: dict, journal: Journal | None):
        super().__init__(daemon=True, name='clock')
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('0.0.0.0', port))
        udp_no_reset(self.sock)
        self.sock.settimeout(1.0)
        self.state = state
        self.errors = 0
        self.journal = journal
        self.stop = threading.Event()
        self.requests = 0
        self.last_peer: str | None = None
        self.last_request_abs: float | None = None

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                data, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError as exc:                  # never die: log, pause, go on
                self.errors += 1
                if self.journal is not None and self.errors <= 20:
                    self.journal.emit('clock_error', error=repr(exc), errors=self.errors)
                time.sleep(0.2)
                continue
            t2 = time.time()
            try:
                req = json.loads(data.decode('utf-8'))
            except Exception:
                continue
            reply = {'t1': req.get('t1'), 'seq': req.get('seq'), 't2': t2, 't3': time.time(), **self.state}
            try:
                self.sock.sendto(json.dumps(reply).encode('utf-8'), addr)
            except OSError:
                pass
            self.requests += 1
            self.last_peer = addr[0]
            self.last_request_abs = t2
            if self.journal is not None:
                self.journal.emit('clock_reply', peer=addr[0], t1=req.get('t1'), t2=t2, t3=reply['t3'], seq=req.get('seq'))

    def close(self) -> None:
        self.stop.set()
        try:
            self.sock.close()
        except OSError:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# Scheduler
# ──────────────────────────────────────────────────────────────────────────────

class Scheduler:
    def __init__(self, sch: dict, a: argparse.Namespace, run_dir: pathlib.Path, journal: Journal, state: dict,
                 responder: ClockResponder | None):
        self.sch, self.a, self.run_dir, self.journal, self.state, self.responder = sch, a, run_dir, journal, state, responder
        self.T = sch['timing']
        self.ip, self.pc1 = a.ip, a.pc1
        self.room, self.other = sch['room'], sch['other_room']
        self.tls = sch.get('tls', 'auto')
        self.echo = not a.no_echo_provider
        self.netdown_mode = a.netdown_mode
        self.trials_done = 0

    # -- helpers --------------------------------------------------------------
    def sleep(self, s: float) -> None:
        end = time.monotonic() + s
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(0.5, left))

    def query_status(self, prov: Prov) -> int | None:
        if not prov.alive():
            return None
        prov.send('status')
        return status_subscriptions(prov.wait_new_line('status:', 10))

    def wait_bound(self, prov: Prov, epr: str, trial: int) -> int | None:
        """Poll 'status' until the provider itself reports subscriptions >= 1 (PC 1 is bound)."""
        t0 = time.time()
        end = time.monotonic() + float(self.T['bind_timeout_s'])
        while time.monotonic() < end:
            subs = self.query_status(prov)
            if subs:
                self.journal.emit('bound', trial=trial, epr=epr, subscriptions=subs, after_s=round(time.time() - t0, 3))
                return subs
            self.sleep(2.0)
        self.journal.emit('bound', trial=trial, epr=epr, subscriptions=None, after_s=round(time.time() - t0, 3), timeout=True)
        return None

    def set_state(self, **kw) -> None:
        self.state.update(kw)

    # -- Ziel 1 -----------------------------------------------------------------
    def run_ziel1(self, ph: dict) -> None:
        counts = parse_counts(ph['trials'])
        plan = make_plan(counts)
        mode = ph.get('mode', 'filtered')
        base = int(self.sch['ports'][ph['name']])
        T = self.T
        for i, role in enumerate(plan, 1):
            self.set_state(trial=i, trials_total=len(plan))
            device = self.sch['roles'][role]
            epr = f'urn:uuid:{uuid.uuid4()}'
            port = base + i - 1
            if role == 'own':
                kw = dict(room=self.room, bed='1')
            elif role == 'foreign':
                kw = dict(room=self.other, bed='1')
            elif role == 'mismatch':
                kw = dict(room=self.other, bed='1', scope_room=self.room)
            else:
                kw = dict(room=None, bed=None)
            cmd = provider_cmd(device, epr, self.ip, port, tls=self.tls, friendly=f'netc {role}', **kw)
            expected = (('reject' if role in BETTFREMD else 'unknown' if role == 'nolocation' else 'accept')
                        if mode == 'filtered' else 'accept')
            self.journal.emit('trial_start', phase=ph['name'], kind='ziel1', trial=i, role=role, device=device,
                              epr=epr, port=port, mode=mode, expected=expected)
            log(f'trial {i}/{len(plan)} {role:10s} epr=..{epr[-12:]} port={port}')
            prov = Prov(f'{role}#{i}', cmd, self.echo)
            t0 = time.time()
            outcome, subs = 'provider_failed', None
            try:
                if prov.wait_line('ready:', float(T['ready_timeout_s'])):
                    self.journal.emit('provider_ready', trial=i, epr=epr, after_s=round(time.time() - t0, 3))
                    self.sleep(float(T['z1_window_s']))
                    subs = self.query_status(prov)
                    self.journal.emit('status', trial=i, epr=epr, subscriptions=subs)
                    outcome = 'window_done'
                else:
                    log('    provider did not become ready')
            finally:
                self.journal.emit('trial_end', phase=ph['name'], kind='ziel1', trial=i, role=role, epr=epr, port=port,
                                  outcome=outcome, provider_subscriptions=subs, elapsed_s=round(time.time() - t0, 3))
                prov.shutdown(10)
                self.trials_done += 1
                self.set_state(trials_done=self.trials_done)
            log(f'    -> {outcome} provider subscriptions={subs}')
            self.sleep(float(T['z1_gap_s']))

    # -- Ziel 2 ack ---------------------------------------------------------------
    def run_ack(self, ph: dict) -> None:
        device = ph.get('device', 'ecg')
        channel = DEVICES[device][4]
        port = int(self.sch['ports'][ph['name']])
        epr = f'urn:uuid:{uuid.uuid4()}'
        cmd = provider_cmd(device, epr, self.ip, port, room=self.room, ack='accept', tls=self.tls, friendly='netc ack')
        cond, sig = f'{device}_alert_cond_{channel}', f'{device}_alert_signal_{channel}'
        self.journal.emit('trial_start', phase=ph['name'], kind='ack', trial=0, role='ack', device=device, epr=epr,
                          port=port, channel=channel, cond_handle=cond, signal_handle=sig,
                          accept=int(ph['accept']), refuse=int(ph['refuse']))
        log(f'ack provider epr=..{epr[-12:]} port={port} channel={channel}')
        prov = Prov('ack', cmd, self.echo)
        cycle = 0
        T = self.T
        try:
            if not prov.wait_line('ready:', float(T['ready_timeout_s'])):
                self.journal.emit('phase_abort', phase=ph['name'], reason='provider not ready')
                return
            self.journal.emit('provider_ready', trial=0, epr=epr)
            if self.wait_bound(prov, epr, 0) is None:
                self.journal.emit('phase_abort', phase=ph['name'], reason='PC 1 not bound (subscriptions stayed 0)')
                return
            self.sleep(float(T['ack_settle_s']))
            for policy, n in (('accept', int(ph['accept'])), ('refuse', int(ph['refuse']))):
                if n <= 0:
                    continue
                prov.send(f'ack {policy}')
                prov.wait_new_line('ack policy', 5)
                self.journal.emit('ack_policy', epr=epr, policy=policy, n=n)
                log(f'block policy={policy} n={n}')
                for _ in range(n):
                    cycle += 1
                    self.set_state(trial=cycle, trials_total=int(ph['accept']) + int(ph['refuse']))
                    prov.send(f'alarm {channel} on')
                    self.journal.emit('alarm', trial=cycle, epr=epr, channel=channel, on=True, policy=policy,
                                      cond_handle=cond, signal_handle=sig)
                    self.sleep(float(T['ack_hold_s']))
                    prov.send(f'alarm {channel} off')
                    self.journal.emit('alarm', trial=cycle, epr=epr, channel=channel, on=False, policy=policy)
                    self.sleep(float(T['ack_pause_s']))
                    self.trials_done += 1
                    self.set_state(trials_done=self.trials_done)
                    if cycle % 25 == 0:
                        log(f'  cycle {cycle}')
        finally:
            self.journal.emit('trial_end', phase=ph['name'], kind='ack', trial=0, epr=epr, cycles=cycle,
                              provider_subscriptions=self.query_status(prov))
            prov.shutdown(10)

    # -- Ziel 2 faults ---------------------------------------------------------------
    def run_fault(self, ph: dict) -> None:
        device = ph.get('device', 'ecg')
        plan = make_plan(parse_counts(ph['methods']))
        base = int(self.sch['ports'][ph['name']])
        off = int(self.sch['ports'].get('reconnect_offset', 1000))
        bounds = self.sch.get('bounds_s', {})
        T = self.T
        for i, method in enumerate(plan, 1):
            self.set_state(trial=i, trials_total=len(plan))
            if method == 'netdown' and self.netdown_mode != 'firewall':
                self.journal.emit('trial_skipped', phase=ph['name'], trial=i, method=method, reason=f'netdown-mode={self.netdown_mode}')
                log(f'trial {i}/{len(plan)} netdown skipped (--netdown-mode {self.netdown_mode})')
                continue
            epr = f'urn:uuid:{uuid.uuid4()}'
            port = base + i - 1
            cmd = provider_cmd(device, epr, self.ip, port, room=self.room, tls=self.tls, friendly=f'netc {method}')
            self.journal.emit('trial_start', phase=ph['name'], kind='fault', trial=i, role=method, method=method,
                              device=device, epr=epr, port=port, bound_s=bounds.get(method))
            log(f'trial {i}/{len(plan)} {method:9s} epr=..{epr[-12:]} port={port}')
            prov = Prov(f'{method}#{i}', cmd, self.echo)
            rec = {'phase': ph['name'], 'kind': 'fault', 'trial': i, 'method': method, 'epr': epr, 'port': port,
                   'outcome': 'provider_failed'}
            gap = float(T.get(f'gap_after_{method}_s', T['gap_after_shutdown_s']))
            try:
                if not prov.wait_line('ready:', float(T['ready_timeout_s'])):
                    log('    provider did not become ready')
                    continue
                self.journal.emit('provider_ready', trial=i, epr=epr)
                if self.wait_bound(prov, epr, i) is None:
                    rec['outcome'] = 'not_bound'
                    log('    PC 1 not bound (provider subscriptions stayed 0)')
                    continue
                self.sleep(float(T['bind_settle_s']))
                rec['outcome'] = 'injected'
                if method == 'shutdown':
                    t_loss = time.time()
                    self.journal.emit('fault_injected', trial=i, method=method, epr=epr, t_loss=t_loss, pid=prov.pid,
                                      bound_s=bounds.get(method))
                    prov.send('shutdown')
                    try:
                        prov.proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        prov.kill()
                elif method == 'kill':
                    t_loss = time.time()
                    self.journal.emit('fault_injected', trial=i, method=method, epr=epr, t_loss=t_loss, pid=prov.pid,
                                      bound_s=bounds.get(method))
                    prov.kill()
                elif method == 'suspend':
                    t_loss = time.time()
                    self.journal.emit('fault_injected', trial=i, method=method, epr=epr, t_loss=t_loss, pid=prov.pid,
                                      bound_s=bounds.get(method))
                    if not prov.suspend():
                        rec['outcome'] = 'suspend_failed'
                        self.journal.emit('fault_restore', trial=i, method=method, epr=epr, ok=False)
                        prov.kill()
                        continue
                    self.sleep(float(T['suspend_hold_s']))
                    self.journal.emit('fault_restore', trial=i, method=method, epr=epr, ok=True)
                    prov.resume()
                    prov.kill()
                elif method == 'reconnect':
                    t_loss = time.time()
                    self.journal.emit('fault_injected', trial=i, method=method, epr=epr, t_loss=t_loss, pid=prov.pid,
                                      bound_s=bounds.get(method))
                    prov.kill()
                    self.sleep(float(T['reconnect_wait_s']))
                    port2 = port + off
                    prov2 = Prov(f'{method}#{i}b', provider_cmd(device, epr, self.ip, port2, room=self.room, tls=self.tls,
                                                                 friendly='netc reconnect'), self.echo)
                    t_restart = time.time()
                    self.journal.emit('reconnect_started', trial=i, epr=epr, t_restart=t_restart, port=port2)
                    try:
                        if prov2.wait_line('ready:', float(T['ready_timeout_s'])):
                            self.sleep(float(T['reconnect_hold_s']))
                            self.journal.emit('reconnect_status', trial=i, epr=epr, subscriptions=self.query_status(prov2))
                        else:
                            rec['outcome'] = 'reconnect_provider_failed'
                    finally:
                        prov2.shutdown(10)
                elif method == 'netdown':
                    hold = float(T['netdown_hold_s'])
                    wd = start_watchdog(hold + float(T.get('netdown_watchdog_extra_s', 15)), self.run_dir / 'netdown_watchdog.log')
                    ok = netdown_add(self.pc1)
                    t_loss = time.time()
                    self.journal.emit('netdown_rule', trial=i, action='add', ok=ok, watchdog_pid=wd)
                    self.journal.emit('fault_injected', trial=i, method=method, epr=epr, t_loss=t_loss, pid=prov.pid,
                                      bound_s=bounds.get(method), rule_ok=ok)
                    if not ok:
                        rec['outcome'] = 'netdown_rule_failed'
                        netdown_clear()
                        prov.kill()
                        continue
                    self.sleep(hold)
                    netdown_clear()
                    left = netdown_exists()
                    self.journal.emit('netdown_rule', trial=i, action='delete', ok=not left)
                    self.journal.emit('fault_restore', trial=i, method=method, epr=epr, ok=not left)
                    self.sleep(float(T['reconnect_hold_s']))
                    self.journal.emit('reconnect_status', trial=i, epr=epr, subscriptions=self.query_status(prov))
                    prov.shutdown(10)
                log(f'    -> {method} injected at {_dt.datetime.fromtimestamp(t_loss).strftime("%H:%M:%S.%f")[:-3]}')
            finally:
                self.journal.emit('trial_end', **rec)
                if prov.alive():
                    prov.kill()
                self.trials_done += 1
                self.set_state(trials_done=self.trials_done)
            self.sleep(gap)

    # -- whole scenario -------------------------------------------------------------
    def run(self, phases: list[dict]) -> None:
        T = self.T
        current_window = None
        for ph in phases:
            if ph['window'] != current_window and current_window is not None:
                self.set_state(phase=ph['name'], window=ph['window'], state='waiting', trial=0)
                self.journal.emit('window_switch', to_window=ph['window'], next_phase=ph['name'],
                                  pause_s=float(T['window_switch_s']))
                log(f'window switch -> {ph["window"]}: pause {T["window_switch_s"]} s so PC 1 can restart its dashboard')
                self.sleep(float(T['window_switch_s']))
            current_window = ph['window']
            self.set_state(phase=ph['name'], window=ph['window'], state='running', trial=0)
            self.journal.emit('phase_start', phase=ph['name'], window=ph['window'], kind=ph['kind'],
                              spec={k: v for k, v in ph.items() if k not in ('name', 'window', 'kind')})
            log(f'== phase {ph["name"]} ({ph["kind"]}, window {ph["window"]})')
            try:
                if ph['kind'] == 'ziel1':
                    self.run_ziel1(ph)
                elif ph['kind'] == 'ack':
                    self.run_ack(ph)
                elif ph['kind'] == 'fault':
                    self.run_fault(ph)
            except KeyboardInterrupt:
                raise
            except Exception as exc:                      # one broken phase never stops the scenario
                self.journal.emit('phase_error', phase=ph['name'], error=repr(exc))
                log(f'   phase {ph["name"]} failed: {exc!r}')
            self.journal.emit('phase_end', phase=ph['name'])
            log(f'== phase {ph["name"]} done')
            self.sleep(float(T['phase_gap_s']))


# ──────────────────────────────────────────────────────────────────────────────
# selftest / dry-run / main
# ──────────────────────────────────────────────────────────────────────────────

def estimate_seconds(sch: dict, phases: list[dict]) -> dict[str, float]:
    T = sch['timing']
    est = {}
    for ph in phases:
        if ph['kind'] == 'ziel1':
            n = sum(parse_counts(ph['trials']).values())
            est[ph['name']] = n * (5 + T['z1_window_s'] + 1 + T['z1_gap_s'])
        elif ph['kind'] == 'ack':
            est[ph['name']] = 30 + (ph['accept'] + ph['refuse']) * (T['ack_hold_s'] + T['ack_pause_s'])
        elif ph['kind'] == 'fault':
            per = {'kill': 15 + T['gap_after_kill_s'], 'shutdown': 15 + T['gap_after_shutdown_s'],
                   'suspend': 15 + T['suspend_hold_s'] + T['gap_after_suspend_s'],
                   'reconnect': 15 + T['reconnect_wait_s'] + 5 + T['reconnect_hold_s'] + T['gap_after_shutdown_s'],
                   'netdown': 15 + T['netdown_hold_s'] + T['reconnect_hold_s'] + T['gap_after_netdown_s']}
            est[ph['name']] = sum(per.get(m, 60) * n for m, n in parse_counts(ph['methods']).items())
    return est


def selftest(sch: dict, a: argparse.Namespace) -> int:
    problems: list[str] = []
    warns: list[str] = []
    print(f'PC 2 selftest  root={ROOT}  python={platform.python_version()}  host={socket.gethostname()}')
    if sys.version_info < (3, 10):
        problems.append(f'Python {platform.python_version()} < 3.10 (harness uses X | None annotations)')
    try:
        import sdc11073
        try:
            from importlib.metadata import version as _pkgver
            v = _pkgver('sdc11073')
        except Exception:
            v = getattr(sdc11073, '__version__', '?')
        print(f'  sdc11073 {v}  ({pathlib.Path(sdc11073.__file__).parent})')
        if not str(v).startswith('3.0'):
            warns.append(f'sdc11073 version {v} (expected 3.0.x)')
    except Exception as exc:
        problems.append(f'import sdc11073 failed: {exc}')
    try:
        import harness  # noqa: F401
        from harness import PROVIDER
        if not PROVIDER.exists():
            problems.append(f'testbed provider missing: {PROVIDER}')
    except Exception as exc:
        problems.append(f'import harness failed: {exc}')
    for dev, (pki, mdib, _p, _m, _c) in DEVICES.items():
        if not (ROOT / 'testbed' / 'mdib' / mdib).exists():
            problems.append(f'MDIB missing: testbed/mdib/{mdib}')
        d = ROOT / 'pki' / pki
        for f in ('userkey.pem', 'sdccert.pem', 'cacert.pem'):
            if not (d / f).exists():
                problems.append(f'PKI file missing: pki/{pki}/{f}')
    # adapter IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((a.ip, 0))
        s.close()
        print(f'  adapter IP {a.ip}: ok')
    except OSError as exc:
        problems.append(f'--ip {a.ip} is not an address of this PC ({exc})')
    # clock port
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('0.0.0.0', int(sch['clock_port'])))
        s.close()
        print(f'  UDP clock port {sch["clock_port"]}: free')
    except OSError as exc:
        problems.append(f'UDP port {sch["clock_port"]} not bindable ({exc})')
    # PC 1 reachable (ICMP may be blocked; informational)
    try:
        r = subprocess.run(['ping', '-n', '1', '-w', '1500', a.pc1], capture_output=True, text=True, timeout=10)
        print(f'  ping {a.pc1}: {"reply" if r.returncode == 0 else "no reply (ICMP may be blocked - not fatal)"}')
    except Exception:
        pass
    # netdown prerequisites
    needs_netdown = any('netdown' in ph.get('methods', '') for ph in sch['phases'] if ph['kind'] == 'fault')
    if needs_netdown and a.netdown_mode == 'firewall':
        adm = is_admin()
        fw = firewall_state()
        print(f'  admin: {adm}   firewall profile: {fw}')
        if not adm:
            problems.append('netdown needs an ADMINISTRATOR console (netsh advfirewall), or run with --netdown-mode skip')
        if fw != 'ON':
            problems.append(f'Windows firewall of the current profile is {fw}: the netdown block rule would have no effect')
        if netdown_exists():
            warns.append('leftover netdown rules found - removing them now')
            netdown_clear()
    # provider start (mTLS) on a spare port
    if not problems:
        epr = f'urn:uuid:{uuid.uuid4()}'
        port = int(sch['ports']['capture_range'][1])
        cmd = provider_cmd('ecg', epr, a.ip, port, room=sch['room'], tls=sch.get('tls', 'auto'), friendly='selftest')
        print(f'  starting one ECG provider on {a.ip}:{port} ...')
        prov = Prov('selftest', cmd, echo=False)
        try:
            line = prov.wait_line('ready:', float(sch['timing']['ready_timeout_s']))
            if line:
                prov.send('status')
                st = prov.wait_new_line('status:', 10)
                print(f'  provider ready; {st}')
            else:
                problems.append('provider did not print "ready:" within the timeout; last lines: '
                                + ' | '.join(prov.lines[-5:]))
        finally:
            prov.shutdown(10)
    for w in warns:
        print(f'  WARN: {w}')
    if problems:
        print('SELFTEST: PROBLEME')
        for p in problems:
            print(f'- {p}')
        return 1
    print('SELFTEST: OK')
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
    except Exception:
        pass
    ap = argparse.ArgumentParser(description='PC 2 autonomous scenario (variant C)')
    ap.add_argument('--schedule', type=pathlib.Path, default=HERE / 'schedule_c.json')
    ap.add_argument('--ip', help='adapter IPv4 of PC 2 (default schedule pc2_ip)')
    ap.add_argument('--pc1', help='IPv4 of PC 1 (default schedule pc1_ip)')
    ap.add_argument('--log-dir', type=pathlib.Path, help='default <root>/logs/<scenario_id>')
    ap.add_argument('--pilot', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--phases', help='comma subset of phase names')
    ap.add_argument('--netdown-mode', choices=('firewall', 'skip'), default='firewall')
    ap.add_argument('--wait-pc1-min', type=float, default=30.0,
                    help='wait up to N min for the first clock request of PC 1 before starting (0 = start at once)')
    ap.add_argument('--no-echo-provider', action='store_true')
    ap.add_argument('--netdown-clear', action='store_true', help='remove leftover netdown rules and exit')
    ap.add_argument('--netdown-test', type=float, metavar='SECONDS', help='add the rules for N s (with watchdog), verify removal')
    ap.add_argument('--netdown-watchdog', type=float, help=argparse.SUPPRESS)
    ap.add_argument('--watchdog-log', type=pathlib.Path, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)

    if a.netdown_watchdog is not None:
        return watchdog_main(a.netdown_watchdog, a.watchdog_log)
    if a.netdown_clear:
        netdown_clear()
        print(f'netdown rules present after clear: {netdown_exists()}')
        return 0

    sch = load_schedule(a.schedule.resolve(), a.pilot)
    a.ip = a.ip or sch['pc2_ip']
    a.pc1 = a.pc1 or sch['pc1_ip']

    if a.netdown_test is not None:
        if not is_admin():
            print('needs an administrator console')
            return 1
        wd = start_watchdog(a.netdown_test + 3, ROOT / 'logs' / 'netdown_watchdog.log')
        ok = netdown_add(a.pc1)
        print(f'rules added: {ok} (watchdog pid {wd}); ping {a.pc1} should fail now for {a.netdown_test:.0f} s')
        time.sleep(a.netdown_test)
        netdown_clear()
        print(f'rules cleared by script; present={netdown_exists()}; watchdog clears again in ~3 s')
        time.sleep(5)
        print(f'present after watchdog={netdown_exists()}')
        return 0
    if a.selftest:
        return selftest(sch, a)

    phases = [ph for ph in sch['phases'] if not a.phases or ph['name'] in a.phases.split(',')]
    est = estimate_seconds(sch, phases)
    scenario_id = ('pilot_' if a.pilot else 'run_') + _dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    if a.dry_run:
        print(f'scenario {scenario_id} ip={a.ip} pc1={a.pc1} room={sch["room"]} other={sch["other_room"]} '
              f'tls={sch.get("tls")} clock_port={sch["clock_port"]} netdown={a.netdown_mode}')
        for ph in phases:
            spec = ph.get('trials') or ph.get('methods') or f"accept={ph.get('accept')} refuse={ph.get('refuse')}"
            print(f'  {ph["name"]:12s} window={ph["window"]:8s} port_base={sch["ports"][ph["name"]]}  {spec}  ~{est[ph["name"]] / 60:.0f} min')
        print(f'  total ~{sum(est.values()) / 3600:.1f} h + gaps')
        return 0

    run_dir = (a.log_dir or ROOT / 'logs' / scenario_id).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    sys.stdout = Tee(run_dir / 'console.txt', sys.stdout)
    journal = Journal(run_dir / 'pc2_journal.jsonl')
    (run_dir / 'scenario.json').write_text(json.dumps({'scenario_id': scenario_id, 'ip': a.ip, 'pc1': a.pc1,
                                                       'netdown_mode': a.netdown_mode, 'schedule': sch}, indent=2),
                                           encoding='utf-8')
    state = {'scenario_id': scenario_id, 'pilot': a.pilot, 'phase': 'idle', 'window': phases[0]['window'] if phases else None,
             'state': 'waiting', 'trial': 0, 'trials_done': 0, 'trials_total': 0}
    if a.netdown_mode == 'firewall' and needs_admin(sch) and not is_admin():
        print('netdown is in the schedule but this console is not elevated: run as administrator or use --netdown-mode skip')
        return 1
    netdown_clear()                                    # never start with a leftover block rule
    responder = ClockResponder(a.ip, int(sch['clock_port']), state, journal)
    responder.start()
    journal.emit('scenario_start', scenario_id=scenario_id, pc2_ip=a.ip, pc1_ip=a.pc1, room=sch['room'],
                 other_room=sch['other_room'], tls=sch.get('tls'), pilot=a.pilot, netdown_mode=a.netdown_mode,
                 phases=[ph['name'] for ph in phases], estimate_s=est, schedule=sch,
                 host=socket.gethostname(), python=platform.python_version())
    log(f'scenario {scenario_id} run_dir={run_dir} est ~{sum(est.values()) / 3600:.1f} h; clock responder on UDP {sch["clock_port"]}')
    if a.wait_pc1_min > 0:
        log(f'waiting for the first clock request of PC 1 ({a.pc1}) up to {a.wait_pc1_min:.0f} min ...')
        end = time.monotonic() + a.wait_pc1_min * 60
        while responder.requests == 0 and time.monotonic() < end:
            time.sleep(1.0)
        if responder.requests == 0:
            log('PC 1 did not ask for the clock - starting anyway (the merge needs at least one exchange later)')
        else:
            log(f'PC 1 is listening (first request from {responder.last_peer})')
        journal.emit('pc1_seen', requests=responder.requests, peer=responder.last_peer)
    sched = Scheduler(sch, a, run_dir, journal, state, responder)
    aborted = False
    try:
        sched.run(phases)
    except KeyboardInterrupt:
        aborted = True
        log('Ctrl-C: scenario aborted')
    finally:
        netdown_clear()
        state.update(phase='done', state='done' if not aborted else 'aborted')   # aborted: PC 1 keeps waiting for a restart
        journal.emit('scenario_end', aborted=aborted, trials_done=sched.trials_done, netdown_rules_left=netdown_exists())
        linger = float(sch['timing'].get('linger_s', 600)) if not aborted else 60.0
        log(f'scenario {"ABORTED" if aborted else "finished"}; clock responder reports done for {linger:.0f} s more (Ctrl-C to quit)')
        try:
            time.sleep(linger)
        except KeyboardInterrupt:
            pass
        responder.close()
        journal.close()
    print(f'journal: {run_dir / "pc2_journal.jsonl"}\ncopy the whole folder {run_dir} to PC 1 for campaign.py --merge')
    return 130 if aborted else 0


def needs_admin(sch: dict) -> bool:
    return any('netdown' in ph.get('methods', '') for ph in sch['phases'] if ph.get('kind') == 'fault')


if __name__ == '__main__':
    raise SystemExit(main())
