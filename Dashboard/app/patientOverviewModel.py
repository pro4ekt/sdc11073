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

        # Thread-safe command queue: each item is a tuple (cmd, *args)
        # cmd = 'update' | 'remove'
        self._queue: queue.Queue[tuple] = queue.Queue()

        # Wire internal signal to slot — Qt guarantees QueuedConnection across threads
        self._pendingUpdate.connect(self._applyPending)

    # ── QML-facing Property ────────────────────────────────────────────────────

    @Property(list, notify=patientsChanged)
    def patients(self) -> list[dict]:
        """
        Returns a snapshot list of ensemble dicts for QML Repeater.
        Each dict keys: ensembleUuid, patientName, room, deviceCount,
                        isEscalated, riskScore
        """
        return list(self._ensembles.values())

    # ── Public API (called from worker threads) ────────────────────────────────

    def updateEnsemble(
        self,
        ensemble_uuid: str,
        patient_name: str,
        room: str,
        device_count: int,
        is_escalated: bool,
        risk_score: float,
    ) -> None:
        """
        Called from SmartAlertAggregator (worker thread).
        Enqueues an update command and triggers main-thread processing.
        """
        self._queue.put(('update', ensemble_uuid, patient_name, room,
                         device_count, is_escalated, risk_score))
        self._pendingUpdate.emit()

    def removeEnsemble(self, ensemble_uuid: str) -> None:
        """
        Called from SmartAlertAggregator when an ensemble has no more devices.
        Enqueues a removal command and triggers main-thread processing.
        """
        self._queue.put(('remove', ensemble_uuid))
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
                _, uuid, name, room, count, escalated, score = item
                entry = {
                    'ensembleUuid': uuid,
                    'patientName':  name,
                    'room':         room,
                    'deviceCount':  count,
                    'isEscalated':  escalated,
                    'riskScore':    float(score),
                }
                if self._ensembles.get(uuid) != entry:
                    self._ensembles[uuid] = entry
                    changed = True
                    _log.debug(
                        f'[PatientOverview] update: uuid={uuid[:8]} '
                        f'name={name!r} escalated={escalated} score={score:.2f}'
                    )

            elif cmd == 'remove':
                uuid = item[1]
                if uuid in self._ensembles:
                    del self._ensembles[uuid]
                    changed = True
                    _log.debug(f'[PatientOverview] removed: uuid={uuid[:8]}')

        if changed:
            self.patientsChanged.emit()

