"""provider_pool.py -- N x 4 testbed providers as subprocesses (SDC "transport" of the ward).

Every patient has four processes of ``testbed/providers/testbed_provider.py`` (unchanged), one per
source of the contract, with the same ``--patient-id`` and ``--room ICU-3`` but its own EPR and
port ``13000 + 4*(i-1) + (j-1)``.  The pool

* starts them with a gap (so PC 1 is not hit by 80 Hellos at once) and waits for ``ready:``,
* forwards alarm transitions of the patients as ``value <ch> <n>`` + ``alarm <ch> on|off``
  (only on transitions; the provider republishes on its own),
* parses the provider echo (``[epoch_ms HH:MM:SS.mmm] alarm <ch> ON (...)``) and writes journal B,
* polls ``status`` once a minute per provider (subscription count -> journal B),
* restarts a dead provider with the same EPR and port and re-sends its alarm state,
* shuts everything down gracefully (``shutdown`` on stdin, then kill of the process tree).

Buffers are bounded (``deque(maxlen)``); the per-second ``publish:`` lines are dropped.  From the
Ziel 1/2 harness only the process-tree helpers are imported.
"""
from __future__ import annotations

import collections
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Callable, Optional

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]                                   # pc2_c (holds experiments/, testbed/)
EXPERIMENTS = ROOT / 'experiments'
PROVIDER = ROOT / 'testbed' / 'providers' / 'testbed_provider.py'
MDIB_DIR = ROOT / 'testbed' / 'mdib'
if str(EXPERIMENTS) not in sys.path:
    sys.path.insert(0, str(EXPERIMENTS))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from harness import _process_tree, _terminate_pid  # noqa: E402  (only these two helpers)
from patient import MANUFACTURER, ROOM, SOURCES, SourceSpec, format_value, patient_id  # noqa: E402

BASE_PORT = 13000
EPR_NAMESPACE = uuid.UUID('6f0b9c1e-5a7d-4c2b-9e1f-3d8a2b4c5e60')   # uuid5 namespace: EPRs stable within a run

_STAMP_RE = re.compile(r'^\[(\d{13}) \d\d:\d\d:\d\d\.\d{3}\] (.*)$')
_ALARM_RE = re.compile(r'^alarm (\S+) (ON|OFF) \(')
_VALUE_RE = re.compile(r'^value (\S+) = (\S+) \(')
_STATUS_RE = re.compile(r'^status: ack=(\S+) selfcheck=(\S+) ensemble=(\S+) subscriptions=(\d+)')


def port_of(patient_index: int, j: int) -> int:
    return BASE_PORT + 4 * (patient_index - 1) + (j - 1)


def epr_of(run_name: str, patient_index: int, j: int) -> str:
    return 'urn:uuid:' + str(uuid.uuid5(EPR_NAMESPACE, f'ziel3/{run_name}/{patient_id(patient_index)}/{j}'))


@dataclass(frozen=True)
class ProviderSpec:
    patient_index: int
    patient_id: str
    source: SourceSpec
    port: int
    epr: str
    serial: str
    seed: int
    friendly: str

    @property
    def tag(self) -> str:
        return f'{self.patient_id}/{self.source.key}'

    def argv(self, python: str, ip: str) -> list[str]:
        s = self.source
        return [python, str(PROVIDER),
                '--mdib', str(MDIB_DIR / s.mdib), '--epr', self.epr,
                '--manufacturer', MANUFACTURER, '--model', s.model, '--friendly-name', self.friendly,
                '--patient-id', self.patient_id, '--room', ROOM, '--bed', str(self.patient_index),
                '--no-tls', '--ip', ip, '--port', str(self.port),
                '--seed', str(self.seed), '--serial', self.serial]

    def as_dict(self) -> dict:
        return {'patient': self.patient_id, 'patient_index': self.patient_index, 'source': self.source.key, 'j': self.source.j,
                'model': self.source.model, 'mdib': self.source.mdib, 'channel': self.source.channel,
                'loinc': self.source.loinc, 'priority': self.source.priority, 'port': self.port, 'epr': self.epr,
                'serial': self.serial, 'seed': self.seed, 'friendly_name': self.friendly}


def make_specs(run_name: str, n_patients: int, run_seed: int) -> list[ProviderSpec]:
    specs = []
    for i in range(1, n_patients + 1):
        pid = patient_id(i)
        for s in SOURCES:
            specs.append(ProviderSpec(i, pid, s, port_of(i, s.j), epr_of(run_name, i, s.j),
                                      serial=f'{pid}-{s.j}', seed=run_seed * 1000 + 4 * (i - 1) + s.j,
                                      friendly=f'{pid} {s.short}'))
    return specs


class ProviderProc:
    """One testbed_provider.py subprocess with a bounded line buffer and journal B writing."""

    def __init__(self, spec: ProviderSpec, python: str, ip: str, journal, max_lines: int = 100):
        self.spec = spec
        self.python = python
        self.ip = ip
        self.journal = journal
        self.proc: Optional[subprocess.Popen] = None
        self.lines: collections.deque[str] = collections.deque(maxlen=max_lines)
        self.ready = threading.Event()
        self.exited = threading.Event()
        self.returncode: Optional[int] = None
        self.started_at: float = 0.0
        self.ready_at: Optional[float] = None
        self.restarts = 0
        self.next_restart_at = 0.0
        self.alarm_desired = False                 # alarm state the patient wants (for resync after restart)
        self.last_value_text: Optional[str] = None
        self.last_value_echo: Optional[str] = None
        self.last_status: Optional[dict] = None
        self._pending_alarm: collections.deque = collections.deque(maxlen=50)   # (t_cmd_abs, on, value_text)
        self._lock = threading.Lock()
        self._generation = 0

    # ---- lifecycle ---------------------------------------------------------------------------
    def start(self) -> None:
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        self.ready.clear()
        self.exited.clear()
        self.returncode = None
        self.ready_at = None
        self._generation += 1
        self.started_at = time.time()
        self.proc = subprocess.Popen(self.spec.argv(self.python, self.ip), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                                     bufsize=1, creationflags=flags, env=env, cwd=str(ROOT))
        threading.Thread(target=self._reader, args=(self.proc, self._generation), daemon=True,
                         name=f'rd-{self.spec.tag}').start()
        self.journal.emit('provider_start', **self._ident(), pid=self.proc.pid, restarts=self.restarts)

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _ident(self) -> dict:
        s = self.spec
        return {'patient': s.patient_id, 'patient_index': s.patient_index, 'source': s.source.key, 'j': s.source.j,
                'channel': s.source.channel, 'port': s.port, 'epr': s.epr}

    def _reader(self, proc: subprocess.Popen, generation: int) -> None:
        try:
            for raw in proc.stdout:
                self._handle(raw.rstrip('\r\n'))
        except Exception as exc:  # noqa: BLE001
            self.journal.emit('provider_msg', **self._ident(), text=f'reader error: {exc!r}')
        rc = proc.wait()
        if generation == self._generation:
            self.returncode = rc
            self.exited.set()
            self.journal.emit('provider_exit', **self._ident(), returncode=rc, last_lines=list(self.lines)[-5:])

    # ---- echo parsing ------------------------------------------------------------------------
    def _handle(self, raw: str) -> None:
        if not raw:
            return
        m = _STAMP_RE.match(raw)
        if m:
            t_ms = int(m.group(1))
            text = m.group(2)
        else:
            t_ms = None
            text = raw
        if text.startswith('publish:'):
            return                                   # once per second per provider: never kept
        self.lines.append(raw)
        if text.startswith('ready:'):
            self.ready_at = time.time()
            self.ready.set()
            self.journal.emit('provider_ready', **self._ident(), after_s=round(self.ready_at - self.started_at, 3),
                              t_prov_ms=t_ms)
            return
        if text.startswith('alarm '):
            am = _ALARM_RE.match(text)
            if am:
                with self._lock:
                    pend = self._pending_alarm.popleft() if self._pending_alarm else None
                rec = dict(self._ident(), on=(am.group(2) == 'ON'), t_prov_ms=t_ms,
                           t_prov_abs=None if t_ms is None else t_ms / 1000.0)
                if pend is not None:
                    rec.update(t_cmd_abs=pend[0], value=pend[2],
                               latency_ms=None if t_ms is None else round(t_ms - pend[0] * 1000.0, 1))
                else:
                    rec.update(t_cmd_abs=None, value=self.last_value_text, latency_ms=None)
                self.journal.emit('alarm', **rec)
            return
        if text.startswith('value '):
            vm = _VALUE_RE.match(text)
            if vm:
                self.last_value_echo = vm.group(2)
            return
        if text.startswith('status:'):
            sm = _STATUS_RE.match(text)
            if sm:
                self.last_status = {'ack': sm.group(1), 'selfcheck': sm.group(2), 'ensemble': sm.group(3),
                                    'subscriptions': int(sm.group(4))}
                self.journal.emit('status', **self._ident(), **self.last_status, t_prov_ms=t_ms, restarts=self.restarts)
            return
        if text.startswith('error:'):
            if text.startswith('error: alarm'):
                with self._lock:
                    pend = self._pending_alarm.popleft() if self._pending_alarm else None
                self.journal.emit('alarm_failed', **self._ident(), text=text[:300],
                                  t_cmd_abs=None if pend is None else pend[0], on=None if pend is None else pend[1])
            else:
                self.journal.emit('provider_msg', **self._ident(), text=text[:300])
            return
        if 'WARNING' in text or 'ERROR' in text or text.startswith('Traceback') or 'shutting down' in text \
                or 'TestbedProvider stopped' in text:
            self.journal.emit('provider_msg', **self._ident(), text=text[:300])

    # ---- commands ----------------------------------------------------------------------------
    def send(self, cmd: str) -> bool:
        p = self.proc
        if p is None or p.stdin is None or p.poll() is not None:
            return False
        try:
            p.stdin.write(cmd + '\n')
            p.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def set_alarm(self, on: bool, value_text: str) -> bool:
        """``value <ch> <n>`` then ``alarm <ch> on|off``; the echo of the alarm line becomes a journal record."""
        ch = self.spec.source.channel
        self.alarm_desired = on
        self.last_value_text = value_text
        if not (self.ready.is_set() and self.alive()):
            self.journal.emit('alarm_dropped', **self._ident(), on=on, value=value_text, t_cmd_abs=time.time(),
                              reason='provider not ready' if self.alive() else 'provider not running')
            return False
        t_cmd = time.time()
        with self._lock:
            self._pending_alarm.append((t_cmd, on, value_text))
        ok = self.send(f'value {ch} {value_text}') and self.send(f'alarm {ch} {"on" if on else "off"}')
        if not ok:
            with self._lock:
                if self._pending_alarm:
                    self._pending_alarm.pop()
            self.journal.emit('alarm_dropped', **self._ident(), on=on, value=value_text, t_cmd_abs=t_cmd, reason='stdin write failed')
        return ok

    def set_value(self, value_text: str) -> bool:
        self.last_value_text = value_text
        return self.send(f'value {self.spec.source.channel} {value_text}')

    def request_status(self) -> bool:
        return self.send('status')

    def shutdown(self, timeout: float = 10.0) -> None:
        if self.proc is None:
            return
        self.send('shutdown')
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.kill()

    def kill(self) -> None:
        if self.proc is None:
            return
        try:
            pids = _process_tree(self.proc.pid) if os.name == 'nt' else [self.proc.pid]
            for pid in reversed(pids):
                if os.name == 'nt':
                    _terminate_pid(pid)
            self.proc.kill()
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass


class ProviderPool:
    def __init__(self, run_name: str, n_patients: int, ip: str, journal, run_seed: int = 1,
                 python: str = sys.executable, start_gap_s: float = 3.0, ready_timeout_s: float = 40.0,
                 status_every_s: float = 60.0, log: Callable[[str], None] = print):
        self.run_name = run_name
        self.n_patients = n_patients
        self.ip = ip
        self.journal = journal
        self.python = python
        self.start_gap_s = start_gap_s
        self.ready_timeout_s = ready_timeout_s
        self.status_every_s = status_every_s
        self.log = log
        self.specs = make_specs(run_name, n_patients, run_seed)
        self.procs: dict[tuple[int, int], ProviderProc] = {
            (sp.patient_index, sp.source.j): ProviderProc(sp, python, ip, journal) for sp in self.specs}
        self.order = [(sp.patient_index, sp.source.j) for sp in self.specs]
        self.stopping = False
        self._t0 = time.time()
        n = max(1, len(self.order))
        self._slot = {key: int(k * status_every_s / n) for k, key in enumerate(self.order)}
        self._last_status_second: Optional[int] = None

    # ---- information -------------------------------------------------------------------------
    def plan(self) -> list[dict]:
        return [sp.as_dict() for sp in self.specs]

    def count_alive(self) -> int:
        return sum(1 for p in self.procs.values() if p.alive())

    def count_ready(self) -> int:
        return sum(1 for p in self.procs.values() if p.alive() and p.ready.is_set())

    def subscriptions_total(self) -> Optional[int]:
        vals = [p.last_status['subscriptions'] for p in self.procs.values() if p.last_status]
        return sum(vals) if vals else None

    # ---- start -------------------------------------------------------------------------------
    def start_all(self, stop_event: Optional[threading.Event] = None) -> list[str]:
        """Start all providers with a gap and wait for ready. Returns the tags that never became ready."""
        self._t0 = time.time()
        for n, key in enumerate(self.order):
            if stop_event is not None and stop_event.is_set():
                break
            self.procs[key].start()
            if n < len(self.order) - 1 and self.start_gap_s > 0:
                time.sleep(self.start_gap_s)
        failed = self._wait_ready(self.order, stop_event)
        if failed and not (stop_event is not None and stop_event.is_set()):
            self.log(f'{len(failed)} provider(s) not ready, retrying once: {", ".join(failed)}')
            retry = []
            for key in self.order:
                p = self.procs[key]
                if p.spec.tag in failed:
                    p.kill()
                    p.restarts += 1
                    self.journal.emit('provider_restart', **p._ident(), attempt=p.restarts, reason='not ready at start',
                                      last_lines=list(p.lines)[-5:])
                    p.start()
                    retry.append(key)
                    time.sleep(self.start_gap_s)
            failed = self._wait_ready(retry, stop_event)
        return failed

    def _wait_ready(self, keys, stop_event: Optional[threading.Event]) -> list[str]:
        failed = []
        for key in keys:
            p = self.procs[key]
            deadline = p.started_at + self.ready_timeout_s
            while not p.ready.is_set() and time.time() < deadline:
                if p.exited.is_set() or (stop_event is not None and stop_event.is_set()):
                    break
                time.sleep(0.1)
            if not p.ready.is_set():
                failed.append(p.spec.tag)
        return failed

    # ---- patient transitions -----------------------------------------------------------------
    def set_alarm(self, patient_index: int, j: int, on: bool, value: float) -> bool:
        p = self.procs[(patient_index, j)]
        return p.set_alarm(on, format_value(value, p.spec.source.decimals))

    def set_value(self, patient_index: int, j: int, value: float) -> bool:
        p = self.procs[(patient_index, j)]
        return p.set_value(format_value(value, p.spec.source.decimals))

    # ---- periodic maintenance (call once per second) -------------------------------------------
    def tick(self, now: Optional[float] = None) -> None:
        now = now or time.time()
        if self.stopping:
            return
        second = int(now - self._t0)
        if second != self._last_status_second:
            self._last_status_second = second
            slot = second % int(self.status_every_s)
            for key in self.order:
                if self._slot[key] == slot:
                    p = self.procs[key]
                    if p.alive() and p.ready.is_set():
                        p.request_status()
        for key in self.order:
            p = self.procs[key]
            if not p.alive() and now >= p.next_restart_at and p.exited.is_set():
                self._restart(p, now)

    def _restart(self, p: ProviderProc, now: float) -> None:
        p.restarts += 1
        p.next_restart_at = now + min(60.0, 5.0 * (2 ** min(p.restarts - 1, 4)))
        self.journal.emit('provider_restart', **p._ident(), attempt=p.restarts, reason=f'exit code {p.returncode}',
                          last_lines=list(p.lines)[-5:])
        self.log(f'restarting provider {p.spec.tag} (attempt {p.restarts}, exit code {p.returncode})')
        try:
            p.start()
        except OSError as exc:
            self.journal.emit('provider_msg', **p._ident(), text=f'restart failed: {exc!r}')
            return

        def resync():
            if p.ready.wait(self.ready_timeout_s):
                if p.alarm_desired and p.last_value_text is not None:
                    p.set_alarm(True, p.last_value_text)
                self.journal.emit('provider_resynced', **p._ident(), alarm_on=p.alarm_desired,
                                  value=p.last_value_text, attempt=p.restarts)
            else:
                self.journal.emit('provider_msg', **p._ident(), text=f'restart attempt {p.restarts}: not ready in time',
                                  last_lines=list(p.lines)[-5:])
        threading.Thread(target=resync, daemon=True, name=f'resync-{p.spec.tag}').start()

    # ---- end of run --------------------------------------------------------------------------
    def all_alarms_off(self) -> int:
        n = 0
        for p in self.procs.values():
            if p.alarm_desired:
                base = format_value(p.spec.source.base, p.spec.source.decimals)
                if p.set_alarm(False, p.last_value_text or base):
                    n += 1
        return n

    def shutdown_all(self, timeout: float = 20.0) -> dict:
        self.stopping = True
        for p in self.procs.values():
            if p.alive():
                p.send('shutdown')
        deadline = time.time() + timeout
        while time.time() < deadline and any(p.alive() for p in self.procs.values()):
            time.sleep(0.2)
        killed = 0
        for p in self.procs.values():
            if p.alive():
                p.kill()
                killed += 1
        res = {'killed': killed, 'total': len(self.procs)}
        self.journal.emit('pool_shutdown', **res)
        return res

    def kill_all(self) -> None:
        self.stopping = True
        for p in self.procs.values():
            p.kill()
