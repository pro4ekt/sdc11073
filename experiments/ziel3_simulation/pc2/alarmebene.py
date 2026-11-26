"""alarmebene.py -- generator "alarmebene": alarms are drawn directly as the analytical load model assumes
(KONTEXT_Belastungsmodell_fuer_PC2.md, section 1).  Used by run D; the default generator ("prozess",
patient.py / szenario.py) is untouched.

Per patient and 1 s step:

* every channel j has its own ON/OFF process: while OFF, a false alarm starts with probability
  lambda_ind * dt per step; its length is Exp(d_false) (rounded to whole steps, at least 1).  lambda_ind
  is chosen so that the time share of these independent alarms is q = (1 - c_art) * FPR:
  lambda_ind = q / (d_false * (1 - q))  (alternating renewal process: mean ON d_false, mean OFF 1/lambda);
* common "Stoesse" of the patient: a Poisson stream with rate lambda_burst = c_art*FPR / (d_false*(1-c_art*FPR))
  and length Exp(d_false); a Stoss switches ALL four channels ON at once (reported as a ``MovementEvent``
  with gains (1, 1, 1, 1) so that journal A keeps its schema);
* crises come from ``AlarmLevelSzenario``: every channel takes part independently with probability
  p_cross; a participating channel is ON from the first to the last step of the crisis (no delay, no ramp);
* no hysteresis, no minimum ON time: the alarm state IS the drawn interval (step precision);
* the device value is near the base level outside an alarm (small noise) and beyond the threshold inside.

The interface is that of ``Patient`` (step, set_disturbance, alarm_active, get_value, value_text, ...), so
the runner, the provider pool and the offline check work unchanged.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from patient import SOURCES, AlarmEvent, MovementEvent, SourceSpec, format_value, patient_id
from szenario import Crisis, CrisisParams, Szenario, _SourcePlan


@dataclass
class AlarmLevelParams:
    dt_s: float = 1.0
    fpr: float = 0.026              # time share of false alarm per channel outside crises
    c_art: float = 0.10             # share of it caused by common Stoesse hitting all channels
    d_false_s: float = 8.0          # mean length of a false alarm and of a Stoss (exponential)
    alarm_level: float = 1.3        # device value inside an alarm, in threshold distances beyond the base
    alarm_noise_sigma: float = 0.08
    normal_noise_sigma: float = 0.05

    @property
    def q_ind(self) -> float:
        return (1.0 - self.c_art) * self.fpr

    @property
    def q_burst(self) -> float:
        return self.c_art * self.fpr

    @property
    def lambda_ind(self) -> float:
        return self.q_ind / (self.d_false_s * (1.0 - self.q_ind))

    @property
    def lambda_burst(self) -> float:
        return self.q_burst / (self.d_false_s * (1.0 - self.q_burst))

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(q_ind=self.q_ind, q_burst=self.q_burst, lambda_ind_per_s=self.lambda_ind, lambda_burst_per_s=self.lambda_burst)
        return d


class AlarmLevelPatient:
    """Patient whose alarm intervals are drawn directly (see module docstring)."""

    generator = 'alarmebene'

    def __init__(self, index: int, seed_seq: np.random.SeedSequence, params: AlarmLevelParams | None = None,
                 sources: tuple[SourceSpec, ...] = SOURCES):
        self.index = index
        self.patient_id = patient_id(index)
        self.rng = np.random.default_rng(seed_seq)
        self.p = params or AlarmLevelParams()
        self.sources = sources
        n = len(sources)
        self.n = n
        self._base = np.array([s.base for s in sources], dtype=float)
        self._dir = np.array([s.direction for s in sources], dtype=float)
        self._dist = np.array([s.distance for s in sources], dtype=float)
        self._lo = np.array([s.lo for s in sources], dtype=float)
        self._hi = np.array([s.hi for s in sources], dtype=float)
        self._dec = [s.decimals for s in sources]
        self.own_until = [-1.0] * n          # first step at which the channel's own false alarm is OFF again
        self.burst_until = -1.0              # same for the common Stoss of the patient
        self.disturbance = np.zeros(n)       # >= 0.5 means: the crisis forces the channel ON (set by the Szenario)
        self.alarm_state = [False] * n
        self.value = self._base.copy()
        self.u = np.zeros(n)
        self.free_u = np.zeros(n)
        self.t = 0.0
        self.n_own_alarms = 0
        self.n_bursts = 0

    # ---- interface shared with Patient -------------------------------------------------------
    def set_disturbance(self, j: int, du: float) -> None:
        self.disturbance[j - 1] = du

    def get_disturbance(self, j: int) -> float:
        return float(self.disturbance[j - 1])

    def get_value(self, j: int) -> float:
        return float(self.value[j - 1])

    def alarm_active(self, j: int) -> bool:
        return self.alarm_state[j - 1]

    def value_text(self, j: int) -> str:
        return format_value(float(self.value[j - 1]), self.sources[j - 1].decimals)

    def _length_steps(self) -> int:
        return max(1, int(round(self.rng.exponential(self.p.d_false_s) / self.p.dt_s)))

    # ---- one step ----------------------------------------------------------------------------
    def step(self, t: float) -> list:
        p, rng, n = self.p, self.rng, self.n
        dt = p.dt_s
        events: list = []
        self.t = t
        # common Stoss of the patient (all channels at once)
        if self.burst_until <= t and rng.random() < p.lambda_burst * dt:
            k = self._length_steps()
            self.burst_until = t + k * dt
            self.n_bursts += 1
            events.append(MovementEvent(t, float(k * dt), tuple(1.0 for _ in range(n))))
        # independent false alarms per channel
        for j0 in range(n):
            if self.own_until[j0] <= t and rng.random() < p.lambda_ind * dt:
                k = self._length_steps()
                self.own_until[j0] = t + k * dt
                self.n_own_alarms += 1
        noise = rng.standard_normal(n)
        alarm_now = [bool(t < self.own_until[j0] or t < self.burst_until or self.disturbance[j0] >= 0.5) for j0 in range(n)]
        u = np.where(alarm_now, p.alarm_level + p.alarm_noise_sigma * noise, p.normal_noise_sigma * noise)
        vals = np.clip(self._base + self._dir * self._dist * u, self._lo, self._hi)
        for j0 in range(n):
            vals[j0] = round(float(vals[j0]), self._dec[j0])
        self.value = vals
        self.u = u
        self.free_u = np.where([t < self.own_until[j0] or t < self.burst_until for j0 in range(n)], p.alarm_level, 0.0)
        for j0 in range(n):
            if alarm_now[j0] != self.alarm_state[j0]:
                self.alarm_state[j0] = alarm_now[j0]
                events.append(AlarmEvent(t, j0 + 1, alarm_now[j0], float(vals[j0])))
        return events


class AlarmLevelSzenario(Szenario):
    """Crises as the model assumes: participating channels (probability p_cross each) are ON from the
    first to the last step of the crisis, without delay or ramp; nothing else is pushed.  Journal A keeps
    the schema of ``Szenario`` (delay_s = 0, amp = 1 for participants, 0 otherwise)."""

    def _start_crisis(self, pt, t: float) -> Crisis:
        p, rng = self.p, self.rng
        self.count[pt.index] += 1
        dur = p.dur_min_s + rng.exponential(p.dur_extra_mean_s)
        t_start = t + self.dt                     # the forced alarms appear with the next patient step
        plans = []
        for s in pt.sources:
            cross = bool(rng.random() < p.p_cross)
            plans.append(_SourcePlan(s.j, s.key, s.channel, 0.0, cross, 1.0 if cross else 0.0, already_on=pt.alarm_active(s.j)))
        c = Crisis(f'{pt.patient_id}-C{self.count[pt.index]:03d}', pt.index, pt.patient_id, t_start, float(dur), plans)
        self.journal.emit('crisis_start', crisis_id=c.crisis_id, patient=c.patient_id, patient_index=c.patient_index,
                          t_model=t_start, t_start_abs=self.t0_abs + t_start, duration_planned_s=round(c.duration_s, 3),
                          generator='alarmebene',
                          sources={pl.key: {'j': pl.j, 'channel': pl.channel, 'planned_cross': pl.planned_cross,
                                            'delay_s': 0.0, 'amp': pl.amp, 'already_on': pl.already_on} for pl in plans})
        return c

    def _push(self, c: Crisis, pl: _SourcePlan, t: float) -> float:
        if pl.planned_cross and c.t_start <= t <= c.t_end:
            return 1.0
        return 0.0


def make_patients(generator: str, seqs, patient_params=None, alarm_params=None):
    """Patients for the chosen generator ('prozess' = patient.Patient, 'alarmebene' = AlarmLevelPatient)."""
    if generator == 'alarmebene':
        return [AlarmLevelPatient(i + 1, seqs[i], alarm_params) for i in range(len(seqs))]
    from patient import Patient
    return [Patient(i + 1, seqs[i], patient_params) for i in range(len(seqs))]


def make_szenario(generator: str, patients, sseq, journal, crisis_params: CrisisParams | None, t0_abs: float = 0.0):
    cls = AlarmLevelSzenario if generator == 'alarmebene' else Szenario
    return cls(patients, sseq, journal, crisis_params, t0_abs=t0_abs)
