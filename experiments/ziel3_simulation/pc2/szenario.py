"""szenario.py -- crises only.

``Szenario`` is the only planned thing in the simulation: for every patient it draws a Poisson
stream of crises (random start, random duration ~30 s, minimum 15 s).  Inside a crisis it pushes
the patient's values towards the alarm side through ``Patient.set_disturbance`` -- additive, on top
of the living random process, so false alarms keep happening.  Every source gets its own random
delay (0-15 s) and is pushed beyond its threshold with probability ~0.9, but never guaranteed: the
threshold comparison stays in the patient.

Szenario writes the TRUTH journal (A): ``crisis_start`` with the plan and ``crisis_end`` with what
was observed (did the source cross, when).  It creates no false alarms and sets no disturbance
outside a crisis and its short recovery tail.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np

from patient import Patient


@dataclass
class CrisisParams:
    rate_per_s: float = 30.0 / 86400.0     # ~30 crises per bed and day
    dur_min_s: float = 15.0
    dur_extra_mean_s: float = 15.0         # duration = dur_min + Exp(mean)  -> mean 30 s
    delay_max_s: float = 15.0              # per-source delay, uniform 0..max
    p_cross: float = 0.9                   # probability that a source is pushed beyond its threshold
    ramp_s: float = 4.0                    # rise time of the push after the delay
    recover_tau_s: float = 8.0             # exponential recovery after the crisis end
    amp_cross: tuple = (1.25, 1.8)         # dimensionless push when the source is meant to cross
    amp_nocross: tuple = (0.3, 0.75)       # push when it is not (may still cross with noise)
    release_eps: float = 0.02              # tail is released when every push is below this

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class _SourcePlan:
    j: int
    key: str
    channel: str
    delay_s: float
    planned_cross: bool
    amp: float
    already_on: bool = False
    t_cross: Optional[float] = None        # model time of the first OFF->ON seen during the crisis


@dataclass
class Crisis:
    crisis_id: str
    patient_index: int
    patient_id: str
    t_start: float
    duration_s: float
    plans: list
    ended: bool = False

    @property
    def t_end(self) -> float:
        return self.t_start + self.duration_s


class Szenario:
    """Crisis scheduler for all patients of the run."""

    def __init__(self, patients: list[Patient], seed_seq: np.random.SeedSequence, journal,
                 params: CrisisParams | None = None, t0_abs: float = 0.0, dt_s: float = 1.0):
        self.patients = patients
        self.rng = np.random.default_rng(seed_seq)
        self.journal = journal                      # object with .emit(event, **fields)
        self.p = params or CrisisParams()
        self.t0_abs = t0_abs                        # wall time of model time 0 (0.0 offline)
        self.dt = dt_s
        self.active: dict[int, Crisis] = {}         # patient_index -> running crisis (incl. tail)
        self.count: dict[int, int] = {pt.index: 0 for pt in patients}
        self.crises_done: list[Crisis] = []

    # ---- planning ----------------------------------------------------------------------------
    def _start_crisis(self, pt: Patient, t: float) -> Crisis:
        p, rng = self.p, self.rng
        self.count[pt.index] += 1
        dur = p.dur_min_s + rng.exponential(p.dur_extra_mean_s)
        plans = []
        for s in pt.sources:
            cross = bool(rng.random() < p.p_cross)
            amp = float(rng.uniform(*(p.amp_cross if cross else p.amp_nocross)))
            plans.append(_SourcePlan(s.j, s.key, s.channel, float(rng.uniform(0.0, p.delay_max_s)),
                                     cross, amp, already_on=pt.alarm_active(s.j)))
        c = Crisis(f'{pt.patient_id}-C{self.count[pt.index]:03d}', pt.index, pt.patient_id, t, float(dur), plans)
        self.journal.emit('crisis_start', crisis_id=c.crisis_id, patient=c.patient_id, patient_index=c.patient_index,
                          t_model=t, t_start_abs=self.t0_abs + t, duration_planned_s=round(c.duration_s, 3),
                          sources={pl.key: {'j': pl.j, 'channel': pl.channel, 'planned_cross': pl.planned_cross,
                                            'delay_s': round(pl.delay_s, 3), 'amp': round(pl.amp, 3),
                                            'already_on': pl.already_on} for pl in plans})
        return c

    def _push(self, c: Crisis, pl: _SourcePlan, t: float) -> float:
        """Dimensionless push of one source at model time t."""
        p = self.p
        t_on = c.t_start + pl.delay_s
        if t < t_on:
            return 0.0
        if t <= c.t_end:
            if t < t_on + p.ramp_s:
                return pl.amp * (t - t_on) / p.ramp_s
            return pl.amp
        # recovery tail after the crisis end
        if c.t_end >= t_on + p.ramp_s:
            level_at_end = pl.amp
        else:
            level_at_end = pl.amp * max(0.0, c.t_end - t_on) / p.ramp_s
        return level_at_end * math.exp(-(t - c.t_end) / p.recover_tau_s)

    def _end_crisis(self, c: Crisis, pt: Patient, t: float) -> None:
        c.ended = True
        self.journal.emit('crisis_end', crisis_id=c.crisis_id, patient=c.patient_id, patient_index=c.patient_index,
                          t_model_start=c.t_start, t_model_end=t,
                          t_start_abs=self.t0_abs + c.t_start, t_end_abs=self.t0_abs + t,
                          duration_s=round(t - c.t_start, 3),
                          sources={pl.key: {'j': pl.j, 'channel': pl.channel,
                                            'crossed': pl.t_cross is not None,
                                            't_cross_rel_s': None if pl.t_cross is None else round(pl.t_cross - c.t_start, 3),
                                            't_cross_abs': None if pl.t_cross is None else self.t0_abs + pl.t_cross,
                                            'already_on': pl.already_on,
                                            'planned_cross': pl.planned_cross, 'delay_s': round(pl.delay_s, 3),
                                            'alarm_on_at_end': pt.alarm_active(pl.j)} for pl in c.plans})
        self.crises_done.append(c)

    # ---- one step, called every model second AFTER Patient.step of the same second -------------
    def step(self, t: float) -> None:
        p = self.p
        for pt in self.patients:
            c = self.active.get(pt.index)
            if c is None:
                if self.rng.random() < p.rate_per_s * self.dt:
                    c = self._start_crisis(pt, t)
                    self.active[pt.index] = c
                else:
                    continue
            # observe crossings while the crisis is running
            if not c.ended:
                for pl in c.plans:
                    if pl.t_cross is None and not pl.already_on and pt.alarm_active(pl.j):
                        pl.t_cross = t
                    elif pl.already_on and not pt.alarm_active(pl.j):
                        pl.already_on = False          # it went off; a later ON counts as a crossing
                if t >= c.t_end:
                    self._end_crisis(c, pt, t)
            # push for the NEXT patient step (additive, never touches the free process)
            max_push = 0.0
            for pl in c.plans:
                du = self._push(c, pl, t + self.dt)
                pt.set_disturbance(pl.j, du)
                max_push = max(max_push, du)
            if c.ended and max_push < p.release_eps:
                for pl in c.plans:
                    pt.set_disturbance(pl.j, 0.0)
                del self.active[pt.index]


class MemoryJournal:
    """Journal stand-in for tests and the offline check: keeps the records in a list."""

    def __init__(self):
        self.records: list[dict] = []
        self.seq = 0

    def emit(self, event: str, **fields) -> dict:
        rec = {'seq': self.seq, 'event': event, **fields}
        self.seq += 1
        self.records.append(rec)
        return rec

    def close(self) -> None:
        pass
