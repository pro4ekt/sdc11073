"""patient.py -- one virtual ICU patient of the Ziel 3 simulation (PC 2, "the ward").

A patient has four values, one per alarm source of the contract (ECG heart rate, ventilator
minute volume, SpO2, diastolic ABP).  Every value is a random process with a 1 s step:

    value_j(t) = base_j + direction_j * distance_j * ( drift_j + noise_j + bursts_j + movement_j + disturbance_j )

where ``distance_j = |on_threshold_j - base_j|`` so that the dimensionless sum reaches 1.0 exactly
at the alarm threshold.  The pieces are

* drift       -- slow Ornstein-Uhlenbeck process (tau ~ 5-10 min),
* noise       -- AR(1) coloured noise (tau ~ 10-30 s),
* bursts      -- rare random artefacts of ONE source (Poisson stream, lognormal amplitude,
                 fast rise and exponential decay, 5-15 s),
* movement    -- "the patient moves": a Poisson stream of the PATIENT; one movement hits all
                 four sources at once, each with its own random gain, so 1, 2, 3 or 4 sources may
                 cross their thresholds together,
* disturbance -- set from outside by ``Szenario`` (crises only); additive, never touches the
                 free process, so false alarms keep happening during a crisis.

The threshold comparison is done HERE (not in the provider): directional thresholds with
hysteresis and a minimum ON time.  The patient reports alarm transitions and movements as
events; the runner forwards the transitions to the SDC providers.

False alarms are NOT scheduled anywhere: they only happen when the random value crosses the
threshold.  Reproducibility comes from ``numpy.random.Generator`` seeded per patient from a
``SeedSequence`` derived from the run seed.

``PatientParams.threshold_scale`` (h, default 1.0) scales the thresholds of all four sources at once
(threshold sweep / ROC): alarm at x >= h instead of x >= 1 (x = dimensionless sum), OFF at the contract's
OFF margin scaled by h (HR 0.896 h, MV 0.867 h, SpO2 0.714 h, DIA 0.85 h).  h = 1.0 keeps the contract
values bit for bit; the random numbers never depend on h (the thresholds do not feed back).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np


# ──────────────────────────────────────────────────────────────────────────────
# Contract: the four alarm sources (order = j = 1..4 = port offset j-1)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SourceSpec:
    j: int              # 1..4, contract order (port = 13000 + 4*(i-1) + (j-1))
    key: str            # short key used in journals and file names
    short: str          # human name used in the provider friendly name ("SIM-P01 ECG")
    model: str          # DPWS ThisModel/ModelName (--model)
    mdib: str           # file in testbed/mdib
    channel: str        # testbed_provider channel: "alarm <channel> on|off", "value <channel> <n>"
    loinc: str          # concept code of the source metric (information only)
    priority: str       # AlertCondition priority in the MDIB (information only)
    unit: str
    base: float         # resting level
    on: float           # alarm ON threshold
    off: float          # alarm OFF threshold (hysteresis)
    direction: int      # +1: alarm when value >= on;  -1: alarm when value <= on
    decimals: int       # metric Resolution of the MDIB
    lo: float           # physical clamp
    hi: float

    @property
    def distance(self) -> float:
        """|on - base| = 1.0 in dimensionless units."""
        return abs(self.on - self.base)


SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec(1, 'ecg',  'ECG',  'Sim ECG',  'ecg_mdib_v2.xml',  'tachycardia',  '8867-4',  'Hi', '1/min',
               base=72.0, on=120.0, off=115.0, direction=+1, decimals=0, lo=20.0, hi=250.0),
    SourceSpec(2, 'vent', 'Vent', 'Sim Vent', 'vent_mdib_v2.xml', 'mv_low',       '76008-2', 'Hi', 'L/min',
               base=7.0, on=4.0, off=4.4, direction=-1, decimals=1, lo=0.0, hi=25.0),
    SourceSpec(3, 'spo2', 'SpO2', 'Sim SpO2', 'spo2_mdib_v2.xml', 'desaturation', '59408-5', 'Me', '%',
               base=97.0, on=90.0, off=92.0, direction=-1, decimals=0, lo=40.0, hi=100.0),
    SourceSpec(4, 'abp',  'ABP',  'Sim ABP',  'abp_mdib_v2.xml',  'dia_low',      '8462-4',  'Lo', 'mmHg',
               base=70.0, on=50.0, off=53.0, direction=-1, decimals=0, lo=10.0, hi=150.0),
)

ROOM = 'ICU-3'
MANUFACTURER = 'SDC Sim'


def patient_id(index: int) -> str:
    return f'SIM-P{index:02d}'


def format_value(v: float, decimals: int) -> str:
    return f'{v:.{decimals}f}'


def scaled_thresholds(s: SourceSpec, h: float) -> tuple[float, float]:
    """Physical (on, off) thresholds of source ``s`` for the threshold scale ``h``: in dimensionless units the
    alarm starts at x >= h instead of x >= 1, the OFF margin of the contract scales with h.  h = 1.0 returns
    the contract values unchanged (bit for bit, no arithmetic)."""
    if h == 1.0:
        return s.on, s.off
    on = s.base + s.direction * s.distance * h
    off = s.base + s.direction * abs(s.off - s.base) * h
    return on, off


# ──────────────────────────────────────────────────────────────────────────────
# Threshold with hysteresis and minimum ON time (directional)
# ──────────────────────────────────────────────────────────────────────────────

class Hysteresis:
    """ON when the value is beyond ``on`` (direction-aware); OFF only when it is back beyond
    ``off`` AND the alarm has been ON for at least ``min_on_s`` seconds."""

    def __init__(self, on: float, off: float, direction: int, min_on_s: float = 2.0):
        if direction not in (+1, -1):
            raise ValueError('direction must be +1 or -1')
        if direction > 0 and not off < on:
            raise ValueError('for direction +1 the OFF threshold must be below the ON threshold')
        if direction < 0 and not off > on:
            raise ValueError('for direction -1 the OFF threshold must be above the ON threshold')
        self.on, self.off, self.direction, self.min_on_s = on, off, direction, min_on_s
        self.active = False
        self.t_on: Optional[float] = None

    def beyond_on(self, v: float) -> bool:
        return v >= self.on if self.direction > 0 else v <= self.on

    def back_off(self, v: float) -> bool:
        return v <= self.off if self.direction > 0 else v >= self.off

    def update(self, value: float, t: float) -> Optional[bool]:
        """Return True on an OFF->ON transition, False on ON->OFF, None when nothing changed."""
        if not self.active:
            if self.beyond_on(value):
                self.active = True
                self.t_on = t
                return True
            return None
        if self.back_off(value) and (t - self.t_on) >= self.min_on_s:
            self.active = False
            self.t_on = None
            return False
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Parameters of the random processes (dimensionless, shared by the four sources)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class PatientParams:
    dt_s: float = 1.0
    # slow drift (Ornstein-Uhlenbeck), stationary std in threshold distances
    drift_tau_s: float = 480.0
    drift_sigma: float = 0.10
    # coloured noise (AR(1))
    noise_tau_s: float = 20.0
    noise_sigma: float = 0.09
    # single-source artefacts ("bursts"): Poisson stream per source
    # (defaults calibrated offline on 20 patients x 12 h against "sehr hohe Last", see README)
    burst_rate_per_s: float = 0.0054        # one artefact per ~3 min and source
    burst_amp_median: float = 1.0           # median amplitude = exactly the threshold distance
    burst_amp_sigma: float = 0.45           # lognormal sigma
    burst_rise_s: tuple = (1.0, 2.0)        # uniform
    burst_tau_s: tuple = (6.0, 13.0)        # uniform decay time constant
    burst_toward_alarm_p: float = 0.85      # sign of the artefact: towards the alarm side
    # patient movement: Poisson stream per patient, hits all sources with random gains
    move_rate_per_s: float = 1.0 / 480.0    # one movement per ~8 min and patient
    move_dur_median_s: float = 12.0
    move_dur_sigma: float = 0.35
    move_gain_median: float = 0.9
    move_gain_sigma: float = 0.55
    move_toward_alarm_p: float = 0.85
    move_decay_tau_s: float = 2.0
    source_susceptibility: tuple = (1.0, 1.0, 1.0, 1.0)   # per-source factor on the movement gain
    # threshold logic
    min_on_s: float = 2.0
    threshold_scale: float = 1.0            # h: alarm at x >= h for all four sources (1.0 = contract thresholds)

    def to_dict(self) -> dict:
        return asdict(self)


# ──────────────────────────────────────────────────────────────────────────────
# Events reported by a patient
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AlarmEvent:
    t: float          # model time [s]
    j: int            # source index 1..4
    on: bool
    value: float      # physical value at the transition (already rounded to the MDIB resolution)


@dataclass(frozen=True)
class MovementEvent:
    t: float
    duration_s: float
    gains: tuple      # dimensionless gain per source (positive = towards the alarm side)


@dataclass
class _Pulse:
    t0: float
    amp: float
    rise: float
    hold: float
    tau: float

    def value(self, t: float) -> float:
        dt = t - self.t0
        if dt <= 0.0:
            return 0.0
        if dt < self.rise:
            return self.amp * dt / self.rise
        dt -= self.rise
        if dt < self.hold:
            return self.amp
        return self.amp * math.exp(-(dt - self.hold) / self.tau)

    def expired(self, t: float) -> bool:
        return (t - self.t0) > self.rise + self.hold + 6.0 * self.tau


@dataclass
class _Movement:
    pulse: _Pulse
    gains: np.ndarray


# ──────────────────────────────────────────────────────────────────────────────
# Patient
# ──────────────────────────────────────────────────────────────────────────────

class Patient:
    def __init__(self, index: int, seed_seq: np.random.SeedSequence,
                 params: PatientParams | None = None, sources: tuple[SourceSpec, ...] = SOURCES):
        self.index = index
        self.patient_id = patient_id(index)
        self.rng = np.random.default_rng(seed_seq)
        self.p = params or PatientParams()
        self.sources = sources
        n = len(sources)
        self.n = n
        self._base = np.array([s.base for s in sources], dtype=float)
        self._dir = np.array([s.direction for s in sources], dtype=float)
        self._dist = np.array([s.distance for s in sources], dtype=float)
        self._lo = np.array([s.lo for s in sources], dtype=float)
        self._hi = np.array([s.hi for s in sources], dtype=float)
        self._dec = [s.decimals for s in sources]
        dt = self.p.dt_s
        self._a_d = math.exp(-dt / self.p.drift_tau_s)
        self._s_d = self.p.drift_sigma * math.sqrt(1.0 - self._a_d ** 2)
        self._a_n = math.exp(-dt / self.p.noise_tau_s)
        self._s_n = self.p.noise_sigma * math.sqrt(1.0 - self._a_n ** 2)
        self.bursts: list[list[_Pulse]] = [[] for _ in range(n)]
        self.moves: list[_Movement] = []
        self.disturbance = np.zeros(n)      # set by Szenario (dimensionless), never by the patient
        self.free_u = np.zeros(n)           # dimensionless sum WITHOUT the disturbance
        self.u = np.zeros(n)                # with the disturbance
        self.value = self._base.copy()      # physical, rounded
        h = float(self.p.threshold_scale)
        if not h > 0.0:
            raise ValueError('threshold_scale must be > 0')
        self.thresholds = [scaled_thresholds(s, h) for s in sources]          # physical (on, off) per source
        self.hyst = [Hysteresis(on, off, s.direction, self.p.min_on_s) for (on, off), s in zip(self.thresholds, sources)]
        self.t = 0.0
        # start the stationary processes at a random stationary sample (no cold start)
        self.drift = self.rng.standard_normal(n) * self.p.drift_sigma
        self.noise = self.rng.standard_normal(n) * self.p.noise_sigma

    # ---- setters / getters used by Szenario, the runner and the tests ------------------------
    def set_disturbance(self, j: int, du: float) -> None:
        """Additive dimensionless offset for source j (1..4); used by Szenario for crises."""
        self.disturbance[j - 1] = du

    def get_disturbance(self, j: int) -> float:
        return float(self.disturbance[j - 1])

    def get_value(self, j: int) -> float:
        return float(self.value[j - 1])

    def alarm_active(self, j: int) -> bool:
        return self.hyst[j - 1].active

    def value_text(self, j: int) -> str:
        return format_value(float(self.value[j - 1]), self.sources[j - 1].decimals)

    # ---- one step of 1 s ---------------------------------------------------------------------
    def step(self, t: float) -> list:
        p, rng, n = self.p, self.rng, self.n
        dt = p.dt_s
        events: list = []
        self.t = t

        # stationary background: exact discretisation of OU / AR(1)
        self.drift = self._a_d * self.drift + self._s_d * rng.standard_normal(n)
        self.noise = self._a_n * self.noise + self._s_n * rng.standard_normal(n)

        # new single-source artefacts (Bernoulli per step approximates the Poisson stream)
        hits = rng.random(n) < p.burst_rate_per_s * dt
        for j0 in np.flatnonzero(hits):
            sign = 1.0 if rng.random() < p.burst_toward_alarm_p else -1.0
            amp = sign * rng.lognormal(math.log(p.burst_amp_median), p.burst_amp_sigma)
            rise = rng.uniform(*p.burst_rise_s)
            tau = rng.uniform(*p.burst_tau_s)
            self.bursts[int(j0)].append(_Pulse(t, float(amp), float(rise), 0.0, float(tau)))

        # new patient movement: all sources at once, each with its own random gain
        if rng.random() < p.move_rate_per_s * dt:
            dur = rng.lognormal(math.log(p.move_dur_median_s), p.move_dur_sigma)
            signs = np.where(rng.random(n) < p.move_toward_alarm_p, 1.0, -1.0)
            gains = signs * rng.lognormal(math.log(p.move_gain_median), p.move_gain_sigma, size=n) \
                * np.asarray(p.source_susceptibility, dtype=float)
            self.moves.append(_Movement(_Pulse(t, 1.0, 1.0, float(dur), p.move_decay_tau_s), gains))
            events.append(MovementEvent(t, float(dur), tuple(float(g) for g in gains)))

        # sum of all pieces
        u = self.drift + self.noise
        for j0 in range(n):
            lst = self.bursts[j0]
            if lst:
                u[j0] += sum(pl.value(t) for pl in lst)
                self.bursts[j0] = [pl for pl in lst if not pl.expired(t)]
        if self.moves:
            for mv in self.moves:
                u = u + mv.gains * mv.pulse.value(t)
            self.moves = [mv for mv in self.moves if not mv.pulse.expired(t)]
        self.free_u = u
        tot = u + self.disturbance
        self.u = tot

        vals = self._base + self._dir * self._dist * tot
        vals = np.clip(vals, self._lo, self._hi)
        for j0 in range(n):
            vals[j0] = round(float(vals[j0]), self._dec[j0])
        self.value = vals

        # threshold logic (directional, hysteresis, minimum ON time)
        for j0 in range(n):
            r = self.hyst[j0].update(float(vals[j0]), t)
            if r is not None:
                events.append(AlarmEvent(t, j0 + 1, r, float(vals[j0])))
        return events


def make_seed_sequences(run_seed: int, n_patients: int):
    """Per-patient seed sequences plus one for the Szenario, all derived from the run seed."""
    root = np.random.SeedSequence(run_seed)
    children = root.spawn(n_patients + 1)
    return children[:n_patients], children[n_patients]
