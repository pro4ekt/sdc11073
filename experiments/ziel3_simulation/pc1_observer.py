"""pc1_observer.py -- Ersatz fuer das PatientOverviewModel des Dashboards: Journal C.

Das Dashboard ruft an seinem ``overview_model`` vier Methoden auf (Dashboard/app/alarms/
smartAlertAggregator.py:1055-1088, ensemble_topology_manager.py:336-349,
app/sdcMyConsumer.py:416-438):

    updateEnsemble(uuid, patient_id, room, device_count, is_escalated, sdc_score, has_active_alarms)
    removeEnsemble(uuid)
    markLost(patient_id, room, epr, cause, escalated_at_loss, t_abs)
    clearLost(epr)

``RotObserver`` nimmt diese Aufrufe aus den Arbeitsthreads entgegen und schreibt nur
Uebergaenge nach ``decisions.jsonl`` (eine JSON-Zeile je Ereignis, Felder wie das
Dashboard-Journal: ``t_abs``, ``t_mono``, ``iso``, ``event``):

    rot_start    patient_id, room, ensemble, device_count, sdc_score   (erste Eskalation)
    rot_end      patient_id, room, ensemble, device_count, sdc_score, duration_s, cause
    device_lost  patient_id, room, epr, cause, escalated_at_loss, t_abs_loss
    device_back  epr

Wiederholte Aufrufe mit unveraendertem Zustand (periodische Auswertung) erzeugen keine
Zeile.  Gelb wird nicht geschrieben; es folgt aus dem Alarmjournal B von PC 2.
"""
from __future__ import annotations

import datetime as _dt
import json
import pathlib
import threading
import time
from typing import Callable, Dict, Optional, Tuple


class RotObserver:
    def __init__(self, path: str | pathlib.Path, clock: Callable[[], float] = time.time,
                 mono: Callable[[], float] = time.monotonic) -> None:
        self._path = pathlib.Path(path)
        self._clock = clock
        self._mono = mono
        self._lock = threading.Lock()
        self._fh = None
        self.count = 0                                   # geschriebene Zeilen
        self.patients: Dict[str, Tuple[str, str]] = {}   # ensemble uuid -> (patient_id, room)
        self.rot_since: Dict[str, float] = {}            # ensemble uuid -> t_abs des rot_start
        self.rot_count = 0                               # rot_start seit Beginn
        self.lost: Dict[str, dict] = {}                  # epr -> letzter device_lost-Datensatz
        try:                                             # Datei immer anlegen, auch ohne Ereignisse
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.touch()
        except OSError:
            pass

    # ── Schnittstelle des overview_model ─────────────────────────────────────

    def updateEnsemble(self, ensemble_uuid: str, patient_id: str, room: str, device_count: int,
                       is_escalated: bool, sdc_score: float, has_active_alarms: bool = False) -> None:
        with self._lock:
            self.patients[ensemble_uuid] = (patient_id, room)
            was_rot = ensemble_uuid in self.rot_since
            if is_escalated and not was_rot:
                t = self._clock()
                self.rot_since[ensemble_uuid] = t
                self.rot_count += 1
                self._write_locked('rot_start', t, patient_id=patient_id, room=room, ensemble=ensemble_uuid,
                                   device_count=int(device_count), sdc_score=round(float(sdc_score), 4))
            elif not is_escalated and was_rot:
                self._end_locked(ensemble_uuid, 'alarms_cleared', device_count, sdc_score)

    def removeEnsemble(self, ensemble_uuid: str) -> None:
        with self._lock:
            if ensemble_uuid in self.rot_since:
                self._end_locked(ensemble_uuid, 'ensemble_removed', None, None)

    def markLost(self, patient_name: str, room: str, epr: str, cause: str,
                 escalated_at_loss: bool, t_abs: float) -> None:
        with self._lock:
            rec = self._write_locked('device_lost', self._clock(), patient_id=patient_name, room=room, epr=epr,
                                     cause=cause, escalated_at_loss=bool(escalated_at_loss),
                                     t_abs_loss=float(t_abs) if t_abs is not None else None)
            self.lost[epr] = rec

    def clearLost(self, epr: str) -> None:
        with self._lock:
            rec = self.lost.pop(epr, None)
            self._write_locked('device_back', self._clock(), epr=epr,
                               patient_id=rec.get('patient_id') if rec else None,
                               room=rec.get('room') if rec else None)

    # ── Auskunft und Abschluss ───────────────────────────────────────────────

    def snapshot(self) -> dict:
        with self._lock:
            return {
                'ensembles_seen': len(self.patients),
                'rot_count': self.rot_count,
                'rot_active': len(self.rot_since),
                'rot_active_patients': sorted(self.patients[u][0] for u in self.rot_since),
                'devices_lost': len(self.lost),
                'lines': self.count,
            }

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.close()
                finally:
                    self._fh = None

    # ── intern (unter self._lock) ────────────────────────────────────────────

    def _end_locked(self, ensemble_uuid: str, cause: str, device_count, sdc_score) -> None:
        t = self._clock()
        since = self.rot_since.pop(ensemble_uuid)
        patient_id, room = self.patients.get(ensemble_uuid, (None, None))
        self._write_locked('rot_end', t, patient_id=patient_id, room=room, ensemble=ensemble_uuid,
                           device_count=int(device_count) if device_count is not None else None,
                           sdc_score=round(float(sdc_score), 4) if sdc_score is not None else None,
                           duration_s=round(t - since, 3), cause=cause)

    def _write_locked(self, event: str, t_abs: float, **fields) -> dict:
        rec = {
            't_abs': t_abs,
            't_mono': self._mono(),
            'iso': _dt.datetime.fromtimestamp(t_abs, _dt.timezone.utc).isoformat(timespec='milliseconds'),
            'event': event,
        }
        rec.update(fields)
        try:
            if self._fh is None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._fh = open(self._path, 'a', encoding='utf-8')
            self._fh.write(json.dumps(rec, ensure_ascii=False, default=str) + '\n')
            self._fh.flush()
            self.count += 1
        except Exception:
            pass
        return rec
