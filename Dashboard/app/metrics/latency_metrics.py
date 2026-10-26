"""
latency_metrics.py — Ziel 2 recorder (acknowledgement RTT, fallback entry,
technical alarm "connection lost").

Events:
    ack_rtt          epr, handle, operation_handle, press_id, rtt_ms, ok, outcome,
                     invocation_state, transaction_id, t_request_abs
                     (one SetAlertState transaction, consumer side; ``ok`` is True
                     only for the final InvocationStates Fin / FinMod — Fix 16)
    ack_confirmed    epr, handle            (the provider's EpisodicAlertReport showed
                                             Presence=Ack for that signal; matched to
                                             the preceding ack_rtt by (epr, handle))
    fallback         epr, cause, since_rx_s, end_status, end_reason
                     (watchdog fired; time since the last inbound report / ping on
                     that device; SubscriptionEnd status/reason if the provider
                     ended the subscription itself)
    technical_alarm  epr, action (raised|cleared|dismissed), cause, patient_id,
                     room, ensemble, escalated_at_loss, lost_since_abs
                     (the "Verbindung verloren" state of the UI — Fix 17)
    latency_summary  (emit_summary)

``mark_rx`` is called for every inbound report and ping; it only updates an
in-memory timestamp (no event per call).

Analysis rules:
    * one ``ack_rtt`` per transaction; several transactions of one bell press
      share a ``press_id``;
    * success rate = ok / (ok + not ok) over ``ack_rtt``; a ``timeout`` that is
      followed by an ``ack_confirmed`` for the same (epr, handle) is a *late
      success* and reported in its own column, never as a retroactive Fin;
    * p95 of ``rtt_ms`` over the ok transactions only (nearest rank).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional

from .eventlog import get_event_log

_log = logging.getLogger('sdc.consumer.metrics.latency')


class LatencyMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_rx: Dict[str, float] = {}
        self._rtts_ms: List[float] = []
        self.n_ack_ok = 0
        self.n_ack_fail = 0
        self.n_ack_confirmed = 0
        self.n_fallback = 0
        self.n_technical_alarm = 0
        self.ack_by_outcome: Dict[str, int] = {}

    def mark_rx(self, epr: str) -> None:
        with self._lock:
            self._last_rx[epr] = time.monotonic()

    # ── fallback / technical alarm ───────────────────────────────────────────

    def record_fallback(self, epr: str, cause: str,
                        end_status: Optional[str] = None,
                        end_reason: Optional[str] = None) -> None:
        now = time.monotonic()
        with self._lock:
            last = self._last_rx.get(epr)
            self.n_fallback += 1
        get_event_log().emit('fallback', epr=epr, cause=cause,
                             since_rx_s=(None if last is None else round(now - last, 3)),
                             end_status=end_status, end_reason=end_reason)

    def record_technical_alarm(self, epr: str, action: str, cause: Optional[str] = None,
                               patient_id: Optional[str] = None, room: Optional[str] = None,
                               ensemble: Optional[str] = None,
                               escalated_at_loss: Optional[bool] = None,
                               lost_since_abs: Optional[float] = None) -> None:
        """``action`` ∈ {raised, cleared, dismissed} — the UI state of Fix 17."""
        with self._lock:
            if action == 'raised':
                self.n_technical_alarm += 1
        get_event_log().emit('technical_alarm', epr=epr, action=action, cause=cause,
                             patient_id=patient_id, room=room, ensemble=ensemble,
                             escalated_at_loss=escalated_at_loss, lost_since_abs=lost_since_abs)

    # ── acknowledgement ──────────────────────────────────────────────────────

    def ack_start(self, handle: str) -> float:
        return time.monotonic()

    def ack_stop(self, handle: str, t0: float, ok: bool = True, *,
                 epr: Optional[str] = None,
                 operation_handle: Optional[str] = None,
                 invocation_state: Optional[str] = None,
                 transaction_id: Optional[int] = None,
                 press_id: Optional[str] = None,
                 outcome: Optional[str] = None,
                 purpose: str = 'ack') -> float:
        """Close one SetAlertState transaction.  ``ok`` is the success verdict of
        the caller (Fin / FinMod); ``outcome`` is the finer vocabulary
        (accepted | failed | timeout | error) used by the analysis."""
        rtt_ms = (time.monotonic() - t0) * 1000.0
        t_request_abs = time.time() - rtt_ms / 1000.0
        key = outcome or ('accepted' if ok else 'failed')
        with self._lock:
            if ok:
                self.n_ack_ok += 1
                self._rtts_ms.append(rtt_ms)
            else:
                self.n_ack_fail += 1
            self.ack_by_outcome[key] = self.ack_by_outcome.get(key, 0) + 1
        get_event_log().emit('ack_rtt', epr=epr, handle=handle, operation_handle=operation_handle,
                             press_id=press_id, purpose=purpose, rtt_ms=round(rtt_ms, 3),
                             ok=bool(ok), outcome=key, invocation_state=invocation_state,
                             transaction_id=transaction_id, t_request_abs=round(t_request_abs, 3))
        return rtt_ms

    def record_ack_confirmed(self, epr: str, handle: str) -> None:
        """The provider reported Presence=Ack for ``handle`` (EpisodicAlertReport)."""
        with self._lock:
            self.n_ack_confirmed += 1
        get_event_log().emit('ack_confirmed', epr=epr, handle=handle)

    # ── summary ──────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        with self._lock:
            rtts = sorted(self._rtts_ms)
            p95 = rtts[min(len(rtts) - 1, int(round(0.95 * len(rtts) + 0.5)) - 1)] if rtts else None
            return {
                'ack_ok': self.n_ack_ok,
                'ack_fail': self.n_ack_fail,
                'ack_by_outcome': dict(self.ack_by_outcome),
                'ack_confirmed': self.n_ack_confirmed,
                'rtt_ms_p95': (None if p95 is None else round(p95, 3)),
                'rtt_ms_max': (round(rtts[-1], 3) if rtts else None),
                'fallbacks': self.n_fallback,
                'technical_alarms': self.n_technical_alarm,
            }

    def emit_summary(self) -> dict:
        s = self.summary()
        get_event_log().emit('latency_summary', **s)
        _log.info(f'[METRICS-Z2] {s}')
        return s


_lock = threading.Lock()
_singleton: Optional[LatencyMetrics] = None


def get_latency_metrics() -> LatencyMetrics:
    global _singleton
    if _singleton is None:
        with _lock:
            if _singleton is None:
                _singleton = LatencyMetrics()
    return _singleton
