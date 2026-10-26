"""harness.py -- shared pieces of the experiment runners (Ziel 1 and Ziel 2).

* ``Prov``              testbed provider as a subprocess (stdin commands, stdout lines,
                        shutdown / kill / suspend / resume)
* ``provider_cmd``      command line of testbed_provider.py for one role
* ``Dashboard``         the real SdcMyConsumer headless (QCoreApplication, no window) with
                        an in-process listener on the JSON Lines journal

Nothing here touches production code; the runners import it with
``sys.path.insert(0, <experiments dir>)``.
"""
from __future__ import annotations

import collections
import ctypes
import os
import pathlib
import queue
import subprocess
import sys
import threading
import time
from typing import Callable, Iterable, Optional

REPO = pathlib.Path(__file__).resolve().parents[1]
DASH = REPO / 'Dashboard'
PROVIDER = REPO / 'testbed' / 'providers' / 'testbed_provider.py'

# pki folder, MDIB file, patient id, model name, first alarm channel
DEVICES = {
    'ecg':  ('ecg',  'ecg_mdib_v2.xml',  'P001', 'ECG Monitor T1',  'tachycardia'),
    'spo2': ('spo2', 'spo2_mdib_v2.xml', 'P002', 'SpO2 Monitor T1', 'desaturation'),
    'abp':  ('abp',  'abp_mdib_v2.xml',  'P003', 'ABP Monitor T1',  'hypotension'),
    'vent': ('vent', 'vent_mdib_v2.xml', 'P004', 'Ventilator T1',   None),
}


def provider_cmd(device: str, epr: str, ip: str, port: int, *, room: str | None = None,
                 bed: str | None = '1', scope_room: str | None = None, ack: str = 'accept',
                 tls: str = 'auto', seed: int | None = 1, friendly: str | None = None,
                 patient: str | None = None, python: str | None = None) -> list[str]:
    pki, mdib, pat, model, _ch = DEVICES[device]
    args = [python or sys.executable, str(PROVIDER),
            '--mdib', str(REPO / 'testbed' / 'mdib' / mdib), '--epr', epr,
            '--manufacturer', 'SDC Testbed', '--model', model,
            '--friendly-name', friendly or f'exp {device}', '--patient-id', patient or pat,
            '--certs-dir', str(REPO / 'pki' / pki), '--ip', ip, '--port', str(port), '--ack', ack]
    if room:
        args += ['--room', room]
        if bed:
            args += ['--bed', bed]
    if scope_room:
        args += ['--scope-room', scope_room]
    if tls == 'no_tls':
        args += ['--no-tls']
    if seed is not None:
        args += ['--seed', str(seed)]
    return args


# ──────────────────────────────────────────────────────────────────────────────
# Provider subprocess
# ──────────────────────────────────────────────────────────────────────────────

class Prov:
    def __init__(self, tag: str, args: list[str], echo: bool = False):
        self.tag = tag
        self.echo = echo
        self.lines: list[str] = []
        self.q: queue.Queue[str] = queue.Queue()
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                     errors='replace', bufsize=1, creationflags=flags)
        self._suspended = False
        threading.Thread(target=self._pump, daemon=True).start()

    @property
    def pid(self) -> int:
        return self.proc.pid

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
        return self.wait_new_line(substring, timeout)

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

    def alive(self) -> bool:
        return self.proc.poll() is None

    def shutdown(self, timeout: float = 10.0) -> None:
        """Graceful: 'shutdown' on stdin (SubscriptionEnd + Bye), then wait / kill."""
        self.send('shutdown')
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.kill()

    def kill(self) -> None:
        """Hard: TerminateProcess of the whole tree — the OS closes the sockets
        (TCP RST on the next request).  The venv ``python.exe`` is only a launcher
        whose child is the real interpreter, so the tree matters."""
        if self._suspended:
            self.resume()
        pids = _process_tree(self.pid) if os.name == 'nt' else [self.pid]
        for pid in reversed(pids):              # children first
            if os.name == 'nt':
                _terminate_pid(pid)
        try:
            self.proc.kill()
            self.proc.wait(timeout=5)
        except Exception:
            pass

    # Silent loss on ONE host: freeze the provider.  The kernel keeps the TCP connection
    # (segments are ACKed) but no application answer ever comes, so the dashboard's
    # GetContextStates runs into its socket timeout — the same path as a packet drop.
    # Every thread of every process in the tree is suspended (the venv launcher plus
    # the real interpreter); NtSuspendProcess on the launcher alone does nothing.
    def suspend(self) -> bool:
        if os.name != 'nt':
            return False
        self._suspended_threads = _suspend_tree(self.pid)
        self._suspended = bool(self._suspended_threads)
        return self._suspended

    def resume(self) -> bool:
        if os.name != 'nt' or not self._suspended:
            return False
        _resume_threads(self._suspended_threads)
        self._suspended_threads = []
        self._suspended = False
        return True


# ── Win32 helpers (Toolhelp32) ──────────────────────────────────────────────────

if os.name == 'nt':
    import ctypes.wintypes as _wt

    _k32 = ctypes.windll.kernel32
    _TH32CS_SNAPPROCESS, _TH32CS_SNAPTHREAD = 0x2, 0x4
    _THREAD_SUSPEND_RESUME, _PROCESS_TERMINATE = 0x0002, 0x0001

    class _PE32(ctypes.Structure):
        _fields_ = [('dwSize', _wt.DWORD), ('cntUsage', _wt.DWORD), ('th32ProcessID', _wt.DWORD),
                    ('th32DefaultHeapID', ctypes.c_void_p), ('th32ModuleID', _wt.DWORD), ('cntThreads', _wt.DWORD),
                    ('th32ParentProcessID', _wt.DWORD), ('pcPriClassBase', _wt.LONG), ('dwFlags', _wt.DWORD),
                    ('szExeFile', ctypes.c_char * 260)]

    class _TE32(ctypes.Structure):
        _fields_ = [('dwSize', _wt.DWORD), ('cntUsage', _wt.DWORD), ('th32ThreadID', _wt.DWORD),
                    ('th32OwnerProcessID', _wt.DWORD), ('tpBasePri', _wt.LONG), ('tpDeltaPri', _wt.LONG),
                    ('dwFlags', _wt.DWORD)]


def _process_tree(pid: int) -> list[int]:
    """``pid`` and all of its descendants (parents before children)."""
    snap = _k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    pe = _PE32()
    pe.dwSize = ctypes.sizeof(_PE32)
    children: dict[int, list[int]] = {}
    if _k32.Process32First(snap, ctypes.byref(pe)):
        while True:
            children.setdefault(pe.th32ParentProcessID, []).append(pe.th32ProcessID)
            if not _k32.Process32Next(snap, ctypes.byref(pe)):
                break
    _k32.CloseHandle(snap)
    out, todo = [], [pid]
    while todo:
        x = todo.pop(0)
        out.append(x)
        todo += children.get(x, [])
    return out


def _suspend_tree(pid: int) -> list[int]:
    pids = set(_process_tree(pid))
    snap = _k32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    te = _TE32()
    te.dwSize = ctypes.sizeof(_TE32)
    handles: list[int] = []
    if _k32.Thread32First(snap, ctypes.byref(te)):
        while True:
            if te.th32OwnerProcessID in pids:
                h = _k32.OpenThread(_THREAD_SUSPEND_RESUME, False, te.th32ThreadID)
                if h:
                    _k32.SuspendThread(h)
                    handles.append(h)
            if not _k32.Thread32Next(snap, ctypes.byref(te)):
                break
    _k32.CloseHandle(snap)
    return handles


def _resume_threads(handles: Iterable[int]) -> None:
    for h in handles:
        _k32.ResumeThread(h)
        _k32.CloseHandle(h)


def _terminate_pid(pid: int) -> None:
    h = _k32.OpenProcess(_PROCESS_TERMINATE, False, pid)
    if h:
        _k32.TerminateProcess(h, 1)
        _k32.CloseHandle(h)


def status_subscriptions(line: str | None) -> int | None:
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
# Headless dashboard
# ──────────────────────────────────────────────────────────────────────────────

class Dashboard:
    """The real SdcMyConsumer without the QML window, plus a journal listener."""

    def __init__(self, ip: str, room: str | None, log_path: pathlib.Path, tls: str = 'auto',
                 quiet: bool = True):
        sys.path.insert(0, str(DASH))
        os.chdir(DASH)
        import logging
        from sdc11073.loghelper import basic_logging_setup
        basic_logging_setup(level=logging.WARNING)
        from PySide6.QtCore import QCoreApplication
        self.app = QCoreApplication.instance() or QCoreApplication([])
        from app.metrics import configure_event_log, get_event_log
        from app.patientOverviewModel import PatientOverviewModel
        from app.sdcMyConsumer import SdcMyConsumer
        logging.getLogger('sdc.consumer').setLevel(logging.WARNING if quiet else logging.INFO)
        logging.getLogger('sdc.consumer.manager').setLevel(logging.INFO)
        if quiet:
            # The library logs every failed request WITH a traceback at WARNING level
            # (expected during fault injection: read timeout, renew on a closed
            # connection, RST) — console only, it has no file handler here.
            logging.getLogger('sdc.client').setLevel(logging.ERROR)
            # The dashboard's own loggers keep their levels so that
            # Dashboard/logs/sdc_consumer.log stays complete (DEBUG file handler);
            # only the CONSOLE handler of 'sdc.consumer' drops the per-device chatter
            # (ALARM OFF, ack-timeout, ping warnings) below ERROR.  TECH-ALARM lines
            # are ERROR and stay visible.
            _noisy = ('sdc.consumer.worker', 'sdc.consumer.aggregator', 'sdc.consumer.topology')

            class _ConsoleQuiet(logging.Filter):
                def filter(self, record: logging.LogRecord) -> bool:
                    return record.levelno >= logging.ERROR or not record.name.startswith(_noisy)
            for h in logging.getLogger('sdc.consumer').handlers:
                if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                    h.addFilter(_ConsoleQuiet())

        log_path.parent.mkdir(parents=True, exist_ok=True)
        configure_event_log(log_path)
        self.journal = get_event_log()
        self.seen: collections.deque = collections.deque()
        _orig = self.journal.emit

        def _emit(event: str, **fields):
            rec = _orig(event, **fields)
            self.seen.append(rec)
            return rec
        self.journal.emit = _emit  # type: ignore[assignment]

        self.overview = PatientOverviewModel()
        self.manager = SdcMyConsumer(target_room=room, override_ip=ip, tls_mode=tls,
                                     overview_model=self.overview, event_log=str(log_path))
        self.manager.start()

    # -- event loop ------------------------------------------------------------
    def pump(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.05)

    def wait_for(self, pred: Callable[[], object], timeout: float, step: float = 0.2):
        """Pump until ``pred()`` is truthy; returns its value or None on timeout."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            v = pred()
            if v:
                return v
            time.sleep(step)
        return None

    # -- journal helpers -------------------------------------------------------
    def events(self, epr: str | None = None, event: str | None = None,
               since: int = 0, **match) -> list[dict]:
        out = []
        for i, r in enumerate(list(self.seen)):
            if i < since:
                continue
            if epr is not None and r.get('epr') != epr:
                continue
            if event is not None and r.get('event') != event:
                continue
            if all(r.get(k) == v for k, v in match.items()):
                out.append(r)
        return out

    def mark(self) -> int:
        return len(self.seen)

    # -- device helpers --------------------------------------------------------
    def handler(self, epr: str):
        with self.manager.lock:
            return self.manager.devices.get(epr)

    def wait_bound(self, epr: str, timeout: float):
        """Handler exists, is in the UI and has a QtDeviceHandler."""
        def ready():
            h = self.handler(epr)
            if h is not None and getattr(h, '_ui_connected', False) and getattr(h, 'qtDeviceHandler', None):
                return h
            return None
        return self.wait_for(ready, timeout)

    def stop(self) -> None:
        self.manager.stop()
        self.pump(1.0)
        self.journal.close()


def setup_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
    except Exception:
        pass


def ts() -> str:
    return time.strftime('%H:%M:%S')
