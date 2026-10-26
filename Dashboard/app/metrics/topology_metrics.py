"""
topology_metrics.py — Ziel 1 (room filter / TRA) recorder over the event journal.

Events:
    device_discovered      epr, scopes, room (from scope; None if no location scope)
    topology_decision      epr, decision (accept|reject|unknown), phase
                           (discovery|context_precheck|mdib), reason, room, target_room
    subscription_created   epr, phase, n_actions
    subscription_avoided   epr, phase, reason
    scope_mismatch         epr, scope_room, context_room, target_room (2f: the
                           unauthenticated scope was not confirmed by the
                           LocationContext read over the authenticated connection)
    device_departed        epr, cause
    topology_summary       counters (emitted by emit_summary at shutdown)

Analysis rule: the LAST topology_decision per EPR is the final decision of
that connection attempt; subscription_created / subscription_avoided give
the "avoided subscriptions" figure of Ziel 1.
"""
from __future__ import annotations

import logging
import threading
from typing import Dict, Iterable, Optional

from .eventlog import get_event_log

_log = logging.getLogger('sdc.consumer.metrics.topology')


class TopologyMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._discovered: Dict[str, Optional[str]] = {}       # epr → room from scope
        self._decisions: Dict[str, tuple] = {}                # epr → (decision, phase, reason)
        self.n_subscriptions_created = 0
        self.n_actions_subscribed = 0
        self.n_subscriptions_avoided = 0
        self.n_scope_mismatch = 0
        self.n_departed = 0

    # ── discovery ────────────────────────────────────────────────────────────

    def record_discovered(self, epr: str, scopes: Optional[Iterable[str]] = None,
                          room: Optional[str] = None) -> None:
        """One event per EPR (the discovery loop sees the same Hello every 2 s);
        re-emitted only if the room named by the scopes changes."""
        scopes = list(scopes or ())
        with self._lock:
            if epr in self._discovered and self._discovered[epr] == room:
                return
            self._discovered[epr] = room
        get_event_log().emit('device_discovered', epr=epr, scopes=scopes, room=room)

    def record_decision(self, epr: str, decision: str, phase: str, reason: str = '',
                        room: Optional[str] = None, target_room: Optional[str] = None) -> None:
        with self._lock:
            # The discovery loop sees the same Hello every cycle: an identical
            # decision for a still-connected EPR is not a new event.
            if self._decisions.get(epr) == (decision, phase, reason):
                return
            self._decisions[epr] = (decision, phase, reason)
        get_event_log().emit('topology_decision', epr=epr, decision=decision, phase=phase,
                             reason=reason, room=room, target_room=target_room)

    # legacy entry points (kept for the existing call sites)
    def record_rejected(self, epr: str, reason: str = 'location', phase: str = 'mdib',
                        room: Optional[str] = None, target_room: Optional[str] = None) -> None:
        self.record_decision(epr, 'reject', phase, reason, room, target_room)

    def record_accepted(self, epr: str, subscriptions: int = 0, phase: str = 'mdib') -> None:
        get_event_log().emit('device_accepted', epr=epr, phase=phase, subscriptions=subscriptions)

    # ── subscriptions ────────────────────────────────────────────────────────

    def record_subscription_created(self, epr: str, n_actions: int, phase: str = 'subscribe') -> None:
        with self._lock:
            self.n_subscriptions_created += 1
            self.n_actions_subscribed += int(n_actions)
        get_event_log().emit('subscription_created', epr=epr, phase=phase, n_actions=int(n_actions))

    def record_subscription_avoided(self, epr: str, phase: str, reason: str = '') -> None:
        with self._lock:
            self.n_subscriptions_avoided += 1
        get_event_log().emit('subscription_avoided', epr=epr, phase=phase, reason=reason)

    def record_scope_mismatch(self, epr: str, scope_room: Optional[str], context_room: Optional[str],
                              target_room: Optional[str] = None) -> None:
        with self._lock:
            self.n_scope_mismatch += 1
        get_event_log().emit('scope_mismatch', epr=epr, scope_room=scope_room,
                             context_room=context_room, target_room=target_room)

    def record_departed(self, epr: str, cause: str) -> None:
        with self._lock:
            self.n_departed += 1
            # a reconnect after departure is a new connection attempt → new decision
            if cause != 'location':
                self._decisions.pop(epr, None)
        get_event_log().emit('device_departed', epr=epr, cause=cause)

    # ── summary ──────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        with self._lock:
            by = {'accept': 0, 'reject': 0, 'unknown': 0}
            phases: Dict[str, int] = {}
            for decision, phase, _r in self._decisions.values():
                by[decision] = by.get(decision, 0) + 1
                key = f'{decision}@{phase}'
                phases[key] = phases.get(key, 0) + 1
            return {
                'discovered': len(self._discovered),
                'decisions': by,
                'decisions_by_phase': phases,
                'subscriptions_created': self.n_subscriptions_created,
                'actions_subscribed': self.n_actions_subscribed,
                'subscriptions_avoided': self.n_subscriptions_avoided,
                'scope_mismatch': self.n_scope_mismatch,
                'departed': self.n_departed,
            }

    def emit_summary(self) -> dict:
        s = self.summary()
        get_event_log().emit('topology_summary', **s)
        _log.info(f'[METRICS-Z1] {s}')
        return s


_lock = threading.Lock()
_singleton: Optional[TopologyMetrics] = None


def get_topology_metrics() -> TopologyMetrics:
    global _singleton
    if _singleton is None:
        with _lock:
            if _singleton is None:
                _singleton = TopologyMetrics()
    return _singleton
