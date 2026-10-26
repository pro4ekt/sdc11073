"""
arr_metrics.py — Ziel 3 recorder (routing verdicts for the alarm reduction rate).

Events:
    verdict        ensemble, alert_key, escalated, sdc_score   (one per routed
                   evaluation; the latched hot path passes write_row=False and
                   only updates the counters, because it runs under the
                   aggregator lock)
    arr_summary    (emit_summary)
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from .eventlog import get_event_log

_log = logging.getLogger('sdc.consumer.metrics.arr')


class ArrMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.n_escalate = 0
        self.n_warn = 0
        self.n_latched = 0

    def record_verdict(self, ensemble_uuid: str, alert_key: str, escalated: bool,
                       sdc_score: float = 0.0, write_row: bool = True) -> None:
        with self._lock:
            if not write_row:
                self.n_latched += 1
            elif escalated:
                self.n_escalate += 1
            else:
                self.n_warn += 1
        if write_row:
            get_event_log().emit('verdict', ensemble=ensemble_uuid, alert_key=alert_key,
                                 escalated=bool(escalated), sdc_score=round(float(sdc_score), 4))

    def summary(self) -> dict:
        with self._lock:
            total = self.n_escalate + self.n_warn
            return {
                'verdicts': total,
                'escalate': self.n_escalate,
                'warn': self.n_warn,
                'latched_events': self.n_latched,
                'warn_share': (round(self.n_warn / total, 4) if total else None),
            }

    def emit_summary(self) -> dict:
        s = self.summary()
        get_event_log().emit('arr_summary', **s)
        _log.info(f'[METRICS-Z3] {s}')
        return s


_lock = threading.Lock()
_singleton: Optional[ArrMetrics] = None


def get_arr_metrics() -> ArrMetrics:
    global _singleton
    if _singleton is None:
        with _lock:
            if _singleton is None:
                _singleton = ArrMetrics()
    return _singleton
