"""
eventlog.py — JSON Lines event journal shared by all metric recorders.

One line per event::

    {"t_mono": 123.456, "t_abs": 1790859017.787, "iso": "2026-10-01T14:50:17.787+00:00",
     "event": "topology_decision", ...fields...}

``t_mono`` is ``time.monotonic()`` (for intervals), ``t_abs`` ``time.time()``
(for correlation with provider logs).  The path comes from ``--event-log``
(``configure``); the default is ``Dashboard/logs/events.jsonl``.  Writing is
best-effort: a failure never reaches the caller.
"""
from __future__ import annotations

import datetime as _dt
import json
import pathlib
import threading
import time
from typing import Any, Optional

_DEFAULT_PATH = pathlib.Path(__file__).resolve().parents[2] / 'logs' / 'events.jsonl'


class EventLog:
    def __init__(self, path: Optional[str | pathlib.Path] = None) -> None:
        self._lock = threading.Lock()
        self._path: pathlib.Path = pathlib.Path(path) if path else _DEFAULT_PATH
        self._fh = None
        self.count = 0

    @property
    def path(self) -> pathlib.Path:
        return self._path

    def configure(self, path: Optional[str | pathlib.Path]) -> None:
        with self._lock:
            self._close_locked()
            self._path = pathlib.Path(path) if path else _DEFAULT_PATH

    def emit(self, event: str, **fields: Any) -> dict:
        record = {
            't_mono': time.monotonic(),
            't_abs': time.time(),
            'iso': _dt.datetime.now(_dt.timezone.utc).isoformat(timespec='milliseconds'),
            'event': event,
        }
        record.update(fields)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            try:
                if self._fh is None:
                    self._path.parent.mkdir(parents=True, exist_ok=True)
                    self._fh = open(self._path, 'a', encoding='utf-8')
                self._fh.write(line + '\n')
                self._fh.flush()
                self.count += 1
            except Exception:
                pass
        return record

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None


_singleton_lock = threading.Lock()
_singleton: Optional[EventLog] = None


def get_event_log() -> EventLog:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = EventLog()
    return _singleton


def configure(path: Optional[str | pathlib.Path]) -> EventLog:
    """Set the journal path (``--event-log``); returns the singleton."""
    log = get_event_log()
    log.configure(path)
    return log
