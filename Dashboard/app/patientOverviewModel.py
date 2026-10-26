"""
patientOverviewModel.py — QObject controller for PatientOverview.qml.

Thread-safety contract:
  SmartAlertAggregator runs in SDC worker threads and calls updateEnsemble() /
  removeEnsemble() from those threads.  Direct property mutation from a non-Qt
  thread is undefined behaviour in Qt.

  Solution (mirrors QtDeviceHandler.scheduleUpdate pattern):
    1. Worker thread puts a command into _queue (queue.Queue — lock-free).
    2. Worker thread emits _pendingUpdate Signal (safe from any thread).
    3. Qt marshals the signal into the main thread (QueuedConnection).
    4. _applyPending() drains the queue and emits patientsChanged → QML rebuilds.

Fix 17 (Ziel 2): technical alarm "Verbindung verloren".  ``markLost`` /
``clearLost`` are called by SdcMyConsumer (NOT by the aggregator) when a
device's connection was lost / came back / was dismissed.  Lost markers are
kept per EPR and MERGED into the card list on read:

  * a card that still exists (partial loss, other members live) gets
    ``lostDevices`` / ``lostEprs`` / ``escalatedAtLoss``;
  * a patient whose ensemble was fully released (last member lost) gets a
    SYNTHETIC card ``connectionLost = True`` (keyed by patient + room, because a
    later re-bind creates a new ensemble UUID), showing "Verbindung verloren"
    and — A1 — "während Eskalation" if the ensemble was Red / latched at the
    loss.  The marker survives updateEnsemble / removeEnsemble of the
    aggregator and leaves only through clearLost (reconnect or dismissal).

Nothing here feeds back into the aggregator, M, E(t) or SDC_score.
"""
from __future__ import annotations

import queue
import logging
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot, Property

_log = logging.getLogger('sdc.consumer.patient_overview')


class PatientOverviewModel(QObject):
    """
    Exposes a list of patient-ensemble summaries to QML.

    QML context property name: ``patientOverview_model``
    QML reads:  patientOverview_model.patients  (list of dicts)
    QML listens: patientsChanged signal
    """

    # ── Public signals (visible to QML) ───────────────────────────────────────
    patientsChanged = Signal()

    # ── Internal cross-thread bridge ──────────────────────────────────────────
    # Emitted from any thread; Qt delivers it to the main thread automatically.
    _pendingUpdate = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        # Master registry: ensemble_uuid → dict with display fields
        self._ensembles: dict[str, dict[str, Any]] = {}
        # Fix 17: epr → {patientName, room, cause, escalatedAtLoss, since}
        self._lost: dict[str, dict[str, Any]] = {}

        # Thread-safe command queue: each item is a tuple (cmd, *args)
        # cmd = 'update' | 'remove' | 'lost' | 'unlost'
        self._queue: queue.Queue[tuple] = queue.Queue()

        # Wire internal signal to slot — Qt guarantees QueuedConnection across threads
        self._pendingUpdate.connect(self._applyPending)

    # ── QML-facing Property ────────────────────────────────────────────────────

    @Property(list, notify=patientsChanged)
    def patients(self) -> list[dict]:
        """
        Returns a snapshot list of ensemble dicts for QML Repeater.
        Each dict keys: ensembleUuid, patientName, room, deviceCount,
                        isEscalated, hasActiveAlarms, sdcScore,
                        connectionLost, lostDevices, lostEprs, escalatedAtLoss,
                        lostSince, lostCause        (Fix 17)
        """
        return self._merged_cards()

    def _merged_cards(self) -> list[dict]:
        cards: list[dict] = []
        covered: set[tuple[str, str]] = set()
        for entry in self._ensembles.values():
            key = (entry['patientName'], entry['room'])
            lost = [(e, r) for e, r in self._lost.items()
                    if (r['patientName'], r['room']) == key]
            card = dict(entry)
            card['connectionLost'] = False
            card['lostDevices'] = len(lost)
            card['lostEprs'] = [e for e, _r in lost]
            card['escalatedAtLoss'] = any(r['escalatedAtLoss'] for _e, r in lost)
            card['lostSince'] = min((r['since'] for _e, r in lost), default=0.0)
            card['lostCause'] = lost[0][1]['cause'] if lost else ''
            cards.append(card)
            covered.add(key)
        # Synthetic cards: patients whose ensemble is gone (last member lost)
        groups: dict[tuple[str, str], list[tuple[str, dict]]] = {}
        for e, r in self._lost.items():
            key = (r['patientName'], r['room'])
            if key in covered:
                continue
            groups.setdefault(key, []).append((e, r))
        for (name, room), lost in groups.items():
            cards.append({
                'ensembleUuid': f'lost:{name}|{room}',
                'patientName': name,
                'room': room,
                'deviceCount': 0,
                'isEscalated': False,
                'hasActiveAlarms': False,
                'sdcScore': 0.0,
                'connectionLost': True,
                'lostDevices': len(lost),
                'lostEprs': [e for e, _r in lost],
                'escalatedAtLoss': any(r['escalatedAtLoss'] for _e, r in lost),
                'lostSince': min(r['since'] for _e, r in lost),
                'lostCause': lost[0][1]['cause'],
            })
        return cards

    # ── Public API (called from worker threads) ────────────────────────────────

    def updateEnsemble(
        self,
        ensemble_uuid: str,
        patient_name: str,
        room: str,
        device_count: int,
        is_escalated: bool,
        sdc_score: float,
        has_active_alarms: bool = False,
    ) -> None:
        """
        Called from SmartAlertAggregator / EnsembleTopologyManager (worker thread).
        Enqueues an update command and triggers main-thread processing.

        Two-tier status (fail-safe): ``is_escalated`` → Red (central escalation);
        else ``has_active_alarms`` → Yellow (local bedside notification — ANY active
        signal); else Blue (idle).  ``sdc_score`` ∈ [0, 1] is the model's normalised
        severity index (SDC_score(t)); QML renders it as a percentage.
        """
        self._queue.put(('update', ensemble_uuid, patient_name, room,
                         device_count, is_escalated, sdc_score, has_active_alarms))
        self._pendingUpdate.emit()

    def removeEnsemble(self, ensemble_uuid: str) -> None:
        """
        Called from SmartAlertAggregator when an ensemble has no more devices.
        Enqueues a removal command and triggers main-thread processing.
        """
        self._queue.put(('remove', ensemble_uuid))
        self._pendingUpdate.emit()

    def markLost(self, patient_name: str, room: str, epr: str, cause: str,
                 escalated_at_loss: bool, t_abs: float) -> None:
        """Fix 17: called by SdcMyConsumer when ``epr`` lost its connection."""
        self._queue.put(('lost', epr, patient_name or '', room or '', cause,
                         bool(escalated_at_loss), float(t_abs)))
        self._pendingUpdate.emit()

    def clearLost(self, epr: str) -> None:
        """Fix 17: called by SdcMyConsumer on reconnect or dismissal of ``epr``."""
        self._queue.put(('unlost', epr))
        self._pendingUpdate.emit()

    # ── Main-thread slot ───────────────────────────────────────────────────────

    @Slot()
    def _applyPending(self) -> None:
        """
        Drains the queue and applies all pending commands.
        Always executes in the main Qt thread (QueuedConnection guarantee).
        """
        changed = False
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break

            cmd = item[0]

            if cmd == 'update':
                _, uuid, name, room, count, escalated, sdc, has_active = item
                entry = {
                    'ensembleUuid': uuid,
                    'patientName':  name,
                    'room':         room,
                    'deviceCount':  count,
                    'isEscalated':  escalated,
                    'hasActiveAlarms': has_active,
                    'sdcScore':     float(sdc),
                }
                if self._ensembles.get(uuid) != entry:
                    self._ensembles[uuid] = entry
                    changed = True
                    _log.debug(
                        f'[PatientOverview] update: uuid={uuid[:8]} '
                        f'name={name!r} escalated={escalated} sdc={sdc:.2f}'
                    )

            elif cmd == 'remove':
                uuid = item[1]
                if uuid in self._ensembles:
                    del self._ensembles[uuid]
                    changed = True
                    _log.debug(f'[PatientOverview] removed: uuid={uuid[:8]}')

            elif cmd == 'lost':
                _, epr, name, room, cause, escalated, since = item
                rec = {'patientName': name, 'room': room, 'cause': cause,
                       'escalatedAtLoss': escalated, 'since': since}
                if self._lost.get(epr) != rec:
                    self._lost[epr] = rec
                    changed = True
                    _log.info(
                        f'[PatientOverview] Verbindung verloren: {epr[-12:]} patient={name!r} '
                        f"room={room!r}{' während Eskalation' if escalated else ''}"
                    )

            elif cmd == 'unlost':
                epr = item[1]
                if epr in self._lost:
                    del self._lost[epr]
                    changed = True
                    _log.info(f'[PatientOverview] Verbindung-verloren-Marker entfernt: {epr[-12:]}')

        if changed:
            self.patientsChanged.emit()
