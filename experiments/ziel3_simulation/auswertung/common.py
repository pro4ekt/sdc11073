"""common.py -- shared pieces of the Ziel 3 evaluation (read-only on the logs).

* loading of the PC 2 export (alarms.csv, crises.csv, run_config.json, a few records of alarms.jsonl)
  and of the PC 1 digest (rot_pc1.csv, clock_pc1.csv, devices_pc1.csv, alarm_in_pc1.csv, summary_pc1.json)
* clock mapping PC 2 -> PC 1 (rtt filter, rolling median, piecewise-linear interpolation)
* escalation rules on the alarm stream (k-of-4, k-of-4 with hold-over, Rot of the Dashboard)
* crisis matching, crisis classes, false episodes
* exact confidence intervals (Poisson / Clopper-Pearson)
"""
from __future__ import annotations

import csv
import json
import pathlib
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy import stats

HERE = pathlib.Path(__file__).resolve().parent
LOGS = HERE.parent / 'logs'

SOURCES = ('ECG', 'Vent', 'SpO2', 'ABP')            # contract order
PRIORITY = {'ECG': 'Hi', 'Vent': 'Hi', 'SpO2': 'Me', 'ABP': 'Lo'}
MODEL_TO_SOURCE = {'Sim ECG': 'ECG', 'Sim Vent': 'Vent', 'Sim SpO2': 'SpO2', 'Sim ABP': 'ABP'}
CLASSES = ('Hi+Hi', 'Hi+Me', 'Hi+Lo', 'Me+Lo', '3', '4', '1 Quelle')
RULES = ('adaptiv', '1-aus-4', '2-aus-4', '3-aus-4', '2-aus-4+10s')

CRISIS_CATCH_TAIL_S = 10.0      # crisis is caught if an escalation intersects [start, end + 10 s]
CRISIS_MARGIN_S = 10.0          # an escalation / alarm is "false" if it misses every crisis +- 10 s
HOLD_S = 10.0                   # 2-aus-4 variant: a source stays "active" 10 s after its alarm end


# ──────────────────────────────────────────────────────────────────────────────
# data classes
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Alarm:
    patient: str
    source: str
    prio: str
    t_on: float
    t_off: Optional[float]
    value: str


@dataclass
class Crisis:
    patient: str
    cid: str
    t_start: float
    t_end: Optional[float]
    cross: dict                                   # source -> time of the first threshold crossing or None

    @property
    def crossed(self) -> list[str]:
        return [s for s in SOURCES if self.cross.get(s) is not None]

    @property
    def first_cross(self) -> Optional[float]:
        ts = [t for t in self.cross.values() if t is not None]
        return min(ts) if ts else None

    @property
    def klass(self) -> str:
        return crisis_class(self.crossed)


@dataclass
class Esc:
    patient: str
    t_start: float
    t_end: float
    open: bool = False
    score: Optional[float] = None


def crisis_class(sources: list[str]) -> str:
    n = len(sources)
    if n == 0:
        return 'keine'
    if n == 1:
        return '1 Quelle'
    if n >= 4:
        return '4'
    if n == 3:
        return '3'
    pr = sorted(PRIORITY[s] for s in sources)
    return {('Hi', 'Hi'): 'Hi+Hi', ('Hi', 'Me'): 'Hi+Me', ('Hi', 'Lo'): 'Hi+Lo', ('Lo', 'Me'): 'Me+Lo'}[tuple(pr)]


# ──────────────────────────────────────────────────────────────────────────────
# loading
# ──────────────────────────────────────────────────────────────────────────────

def _rows(path: pathlib.Path) -> list[dict]:
    with path.open(encoding='utf-8', newline='') as fh:
        lines = [ln for ln in fh if not ln.startswith('#')]
    rd = csv.DictReader(lines, delimiter=';')
    return [dict(r) for r in rd]


def _f(x) -> Optional[float]:
    return None if x is None or x == '' else float(x)


def load_pc2(run_dir: pathlib.Path) -> dict:
    """PC 2 export of one run: alarms, crises, run_config and the run_start / run_end / provider_ready times."""
    exp = run_dir / 'export'
    alarms = [Alarm(r['patient'], r['source'], r['priority'], float(r['t_on']), _f(r['t_off']), r['value_on'])
              for r in _rows(exp / 'alarms.csv')]
    crises = [Crisis(r['patient'], r['crisis_id'], float(r['t_start']), _f(r['t_end']),
                     {s: _f(r.get(s)) for s in SOURCES}) for r in _rows(exp / 'crises.csv')]
    cfg = json.loads((run_dir / 'run_config.json').read_text(encoding='utf-8'))
    marks = {'provider_ready': [], 'run_start': None, 'run_end': None}
    with (run_dir / 'alarms.jsonl').open(encoding='utf-8') as fh:
        for line in fh:
            if '"provider_ready"' in line or '"run_start"' in line or '"run_end"' in line:
                r = json.loads(line)
                if r['event'] == 'provider_ready':
                    marks['provider_ready'].append(r['t_abs'])
                elif r['event'] == 'run_start':
                    marks['run_start'] = r['t_start_abs']
                elif r['event'] == 'run_end':
                    marks['run_end'] = r['t_end_abs']
    return {'alarms': alarms, 'crises': crises, 'config': cfg, 'marks': marks}


PC1_FILES = ('rot', 'clock', 'devices', 'alarm_in', 'summary', 'ttl_expired')     # digest of PC 1


def pc1_paths(run_dir: pathlib.Path, pc1_dir: Optional[pathlib.Path] = None) -> dict[str, pathlib.Path]:
    """Where the PC 1 digest lives: export/<name>_pc1.csv|json beside the PC 2 export (default, runs 1h/12h/6h_p06),
    or <pc1_dir>/<name>.csv|json when the export/ folder of the PC 1 digest was copied as a whole
    (logs/<run>_pc1/pc1/export, runs A-D)."""
    if pc1_dir is None:
        exp = run_dir / 'export'
        return {n: exp / (f'{n}_pc1.json' if n == 'summary' else f'{n}_pc1.csv') for n in PC1_FILES}
    return {n: pc1_dir / (f'{n}.json' if n == 'summary' else f'{n}.csv') for n in PC1_FILES}


def find_pc1_dir(name: str) -> Optional[pathlib.Path]:
    """export/ folder of a PC 1 digest copied as a whole: logs/<name>_pc1/pc1/export (None when absent)."""
    p = LOGS / f'{name}_pc1' / 'pc1' / 'export'
    return p if p.is_dir() else None


def load_pc1(run_dir: pathlib.Path, pc1_dir: Optional[pathlib.Path] = None) -> Optional[dict]:
    """PC 1 digest (files *_pc1.* in export/, or the files of pc1_dir, see pc1_paths), or None when any of
    rot, clock, devices, alarm_in, summary is missing."""
    paths = pc1_paths(run_dir, pc1_dir)
    missing = [n for n in ('rot', 'clock', 'devices', 'alarm_in', 'summary') if not paths[n].exists()]
    if missing:
        return None
    rot = [Esc(r['patient'], float(r['t_start']), float(r['t_end']), score=_f(r['sdc_score']))
           for r in _rows(paths['rot'])]
    rot_rows = _rows(paths['rot'])
    clock = [(float(r['t_pc1']), float(r['offset_s']), float(r['rtt_ms'])) for r in _rows(paths['clock'])]
    devices = _rows(paths['devices'])
    alarm_in = [(float(r['t']), r['patient'], MODEL_TO_SOURCE.get(r['source'], r['source']), r['on_off'], r['t_source'])
                for r in _rows(paths['alarm_in'])]
    ttl = _rows(paths['ttl_expired']) if paths['ttl_expired'].exists() else []
    summary = json.loads(paths['summary'].read_text(encoding='utf-8'))
    return {'rot': rot, 'rot_rows': rot_rows, 'clock': clock, 'devices': devices, 'alarm_in': alarm_in,
            'ttl': ttl, 'summary': summary, 'source_dir': str(paths['rot'].parent)}


# ──────────────────────────────────────────────────────────────────────────────
# clock mapping PC 2 -> PC 1
# ──────────────────────────────────────────────────────────────────────────────

class ClockMap:
    """offset_s = clock PC 2 - clock PC 1, sampled against t_pc1. Points with rtt >= 20 ms are dropped, a
    rolling median over 5 points removes single outliers, between the points the offset is interpolated
    linearly (constant beyond the ends). t_pc1 = t_pc2 - offset(t_pc1), solved by two fixed-point steps."""

    def __init__(self, points, rtt_max_ms: float = 20.0, window: int = 5):
        pts = sorted(p for p in points if p[2] < rtt_max_ms)
        self.n_all, self.n_used = len(points), len(pts)
        t = np.array([p[0] for p in pts])
        off = np.array([p[1] for p in pts])
        half = window // 2
        med = np.array([np.median(off[max(0, i - half):i + half + 1]) for i in range(len(off))])
        self.t, self.off = t, med
        self.off_min, self.off_max = float(med.min()), float(med.max())

    def offset_at_pc1(self, t_pc1):
        return np.interp(t_pc1, self.t, self.off)

    def to_pc1(self, t_pc2):
        if t_pc2 is None:
            return None
        t1 = t_pc2 - self.offset_at_pc1(t_pc2)
        return float(t_pc2 - self.offset_at_pc1(t1))

    def to_pc2(self, t_pc1):
        return float(t_pc1 + self.offset_at_pc1(t_pc1))


class IdentityMap:
    """No PC 1 clock data: everything stays in the PC 2 time base."""
    n_all = n_used = 0
    off_min = off_max = 0.0

    def to_pc1(self, t):
        return t

    def to_pc2(self, t):
        return t


# ──────────────────────────────────────────────────────────────────────────────
# escalation rules
# ──────────────────────────────────────────────────────────────────────────────

def by_patient(items):
    out: dict[str, list] = {}
    for it in items:
        out.setdefault(it.patient, []).append(it)
    return out


def k_of_n(alarms: list[Alarm], k: int, hold_s: float = 0.0, stream_end: Optional[float] = None) -> list[Esc]:
    """Event-driven k-aus-4 on one alarm stream: escalation as soon as >= k sources are active, held until
    every alarm of the patient is off (the latch of Rot). With hold_s > 0 a source still counts as active
    for hold_s seconds after its alarm ended (only for the trigger; the latch uses the real alarms)."""
    escs: list[Esc] = []
    for patient, als in by_patient(alarms).items():
        events = []
        for a in als:
            events.append((a.t_on, 1, a.source))
            if a.t_off is not None:
                events.append((a.t_off, -1, a.source))
        events.sort(key=lambda e: (e[0], e[1]))           # at equal times OFF (-1) before ON (+1)
        active: set[str] = set()
        last_off: dict[str, float] = {}
        start: Optional[float] = None
        t_last = events[-1][0] if events else 0.0
        for t, typ, src in events:
            if typ > 0:
                active.add(src)
                cnt = len(active)
                if hold_s > 0:
                    cnt += sum(1 for s, to in last_off.items() if s not in active and t - to <= hold_s)
                if start is None and cnt >= k:
                    start = t
            else:
                active.discard(src)
                last_off[src] = t
                if start is not None and not active:
                    escs.append(Esc(patient, start, t))
                    start = None
        if start is not None:
            escs.append(Esc(patient, start, stream_end if stream_end is not None else t_last, open=True))
    escs.sort(key=lambda e: (e.t_start, e.patient))
    return escs


def k_of_n_tau(alarms: list[Alarm], k: int, tau_s: float, stream_end: Optional[float] = None) -> list[Esc]:
    """k-aus-4 with a confirmation delay: escalation only when >= k sources have been active WITHOUT
    interruption for tau_s seconds (escalation begins at that moment); then the same latch as Rot
    (held until every alarm of the patient is off). tau_s = 0 is identical to k_of_n."""
    escs: list[Esc] = []
    for patient, als in by_patient(alarms).items():
        events = []
        for a in als:
            events.append((a.t_on, 1, a.source))
            if a.t_off is not None:
                events.append((a.t_off, -1, a.source))
        events.sort(key=lambda e: (e[0], e[1]))
        active: set[str] = set()
        start: Optional[float] = None          # running escalation
        cand: Optional[float] = None           # moment since which >= k sources are active
        t_last = events[-1][0] if events else 0.0
        for t, typ, src in events:
            if start is None and cand is not None and t >= cand + tau_s:
                start = cand + tau_s           # the candidate matured before this event
            if typ > 0:
                active.add(src)
                if cand is None and len(active) >= k:
                    cand = t
                    if tau_s <= 0.0 and start is None:
                        start = t
            else:
                active.discard(src)
                if len(active) < k:
                    cand = None
                if start is not None and not active:
                    escs.append(Esc(patient, start, t))
                    start = None
        if start is not None:
            escs.append(Esc(patient, start, stream_end if stream_end is not None else t_last, open=True))
    escs.sort(key=lambda e: (e.t_start, e.patient))
    return escs


def intersects(a0: float, a1: float, b0: float, b1: float) -> bool:
    return a0 <= b1 and a1 >= b0


# ──────────────────────────────────────────────────────────────────────────────
# crises: caught / delay / false escalations
# ──────────────────────────────────────────────────────────────────────────────

def evaluate_rule(escs: list[Esc], crises: list[Crisis], all_crises: list[Crisis], window: tuple[float, float]) -> dict:
    """Catch / delay per crisis and the false escalations inside the window."""
    w0, w1 = window
    esc_p = by_patient(escs)
    cr_p = by_patient(all_crises)
    per_crisis = []
    for c in crises:
        hit = None
        for e in esc_p.get(c.patient, []):
            if intersects(e.t_start, e.t_end, c.t_start, c.t_end + CRISIS_CATCH_TAIL_S):
                hit = e
                break
        fc = c.first_cross
        delay = None if (hit is None or fc is None) else hit.t_start - fc
        per_crisis.append({'cid': c.cid, 'patient': c.patient, 'klass': c.klass, 'caught': hit is not None,
                           'delay_s': delay, 'esc_start': None if hit is None else hit.t_start,
                           'esc_before_crisis': None if hit is None else bool(hit.t_start < c.t_start),
                           'esc_before_cross': None if delay is None else bool(delay < 0.0),
                           'esc_after_end': None if hit is None else bool(hit.t_start > c.t_end),
                           'caught_strict': hit is not None and hit.t_start <= c.t_end})
    false_escs = []
    in_window = [e for e in escs if w0 <= e.t_start <= w1]
    for e in in_window:
        if not any(intersects(e.t_start, e.t_end, c.t_start - CRISIS_MARGIN_S,
                              (c.t_end if c.t_end is not None else w1) + CRISIS_MARGIN_S)
                   for c in cr_p.get(e.patient, [])):
            false_escs.append(e)
    caught = sum(1 for p in per_crisis if p['caught'])
    caught_strict = sum(1 for p in per_crisis if p['caught_strict'])
    caught_after_end = sum(1 for p in per_crisis if p['esc_after_end'])
    delays_all = [p['delay_s'] for p in per_crisis if p['delay_s'] is not None]
    delays = [d for d in delays_all if d >= 0.0]                      # escalation began after the first crossing
    n_pre_cross = sum(1 for d in delays_all if d < 0.0)               # Rot was already running before the crossing
    by_class = {}
    for k in CLASSES:
        sub = [p for p in per_crisis if p['klass'] == k]
        ds = [p['delay_s'] for p in sub if p['delay_s'] is not None]
        by_class[k] = {'n': len(sub), 'caught': sum(1 for p in sub if p['caught']),
                       'caught_strict': sum(1 for p in sub if p['caught_strict']),
                       'delays': [d for d in ds if d >= 0.0], 'n_pre_cross': sum(1 for d in ds if d < 0.0)}
    return {'n_escalations_window': len(in_window), 'n_false': len(false_escs), 'caught': caught, 'n_crises': len(crises),
            'caught_strict': caught_strict, 'caught_after_end': caught_after_end,
            'delays': delays, 'delays_all': delays_all, 'n_pre_cross': n_pre_cross,
            'median_delay_s': float(np.median(delays)) if delays else None,
            'median_delay_all_s': float(np.median(delays_all)) if delays_all else None,
            'by_class': by_class, 'per_crisis': per_crisis,
            'false_starts': [e.t_start for e in false_escs],
            'false_durations': [e.t_end - e.t_start for e in false_escs]}


# ──────────────────────────────────────────────────────────────────────────────
# false alarms, episodes, measured parameters
# ──────────────────────────────────────────────────────────────────────────────

def alarm_is_false(a: Alarm, crises_of_patient: list[Crisis], w1: float) -> bool:
    end = a.t_off if a.t_off is not None else w1
    return not any(intersects(a.t_on, end, c.t_start - CRISIS_MARGIN_S,
                              (c.t_end if c.t_end is not None else w1) + CRISIS_MARGIN_S) for c in crises_of_patient)


def episodes(alarms: list[Alarm], w1: float) -> list[dict]:
    """Union of overlapping alarms of one patient -> episodes with the set of sources, the total time with
    >= 2 sources active ('overlap') and the per-source alarm time inside the episode."""
    out = []
    for patient, als in by_patient(alarms).items():
        als = sorted(als, key=lambda a: a.t_on)
        cur = None
        for a in als:
            end = a.t_off if a.t_off is not None else w1
            if cur is None or a.t_on > cur['t_end']:
                if cur is not None:
                    out.append(cur)
                cur = {'patient': patient, 't_start': a.t_on, 't_end': end, 'alarms': [(a.source, a.t_on, end)]}
            else:
                cur['t_end'] = max(cur['t_end'], end)
                cur['alarms'].append((a.source, a.t_on, end))
        if cur is not None:
            out.append(cur)
    for ep in out:
        srcs = sorted({s for s, _, _ in ep['alarms']}, key=SOURCES.index)
        ep['sources'] = srcs
        ep['n_sources'] = len(srcs)
        ep['klass'] = crisis_class(srcs) if len(srcs) <= 2 else '3+'
        ep['alarm_time'] = sum(b - a for _, a, b in ep['alarms'])
        # time with >= 2 sources active (sweep)
        ev = sorted([(a, 1) for _, a, _ in ep['alarms']] + [(b, -1) for _, _, b in ep['alarms']])
        cnt, last_t, overlap = 0, None, 0.0
        for t, d in ev:
            if last_t is not None and cnt >= 2:
                overlap += t - last_t
            cnt += d
            last_t = t
        ep['overlap_s'] = overlap
        ep['duration_s'] = ep['t_end'] - ep['t_start']
    return out


# ──────────────────────────────────────────────────────────────────────────────
# model of chapter 3: required overlap time of a pair of sources
# ──────────────────────────────────────────────────────────────────────────────

PRIO_VALUE = {'Hi': 3, 'Me': 2, 'Lo': 1}
MODEL_T_S = 10.0
PAIR_CLASSES = ('Hi+Hi', 'Hi+Me', 'Hi+Lo', 'Me+Lo')


def round_half_up(x: float) -> int:
    import math
    return int(math.floor(x + 0.5))


def t_need_pair(klass: str, T: float = MODEL_T_S):
    """Required joint alarm time of two sources: SDC = 0.5*max(P)/3 + 0.5*sum(P)/9, k = round_half_up(4 - 2*SDC),
    r = 2 active sources: t = T*k/r if r >= k, t = T + (T/SDC)*ln(k/r) if r >= k/2, else None."""
    import math
    pr = [PRIO_VALUE[p] for p in klass.split('+')]
    sdc = 0.5 * max(pr) / 3.0 + 0.5 * sum(pr) / 9.0
    k = round_half_up(4.0 - 2.0 * sdc)
    r = 2
    if r >= k:
        t = T * k / r
    elif r >= 0.5 * k:
        t = T + (T / sdc) * math.log(k / r)
    else:
        t = None
    return {'sdc': sdc, 'k': k, 't_need_s': t}


# ──────────────────────────────────────────────────────────────────────────────
# confidence intervals
# ──────────────────────────────────────────────────────────────────────────────

def poisson_ci(n: int, level: float = 0.95) -> tuple[float, float]:
    """Exact (Garwood) interval for a Poisson count."""
    a = 1.0 - level
    lo = 0.0 if n == 0 else stats.chi2.ppf(a / 2, 2 * n) / 2.0
    hi = stats.chi2.ppf(1 - a / 2, 2 * (n + 1)) / 2.0
    return float(lo), float(hi)


def median_ci(values, level: float = 0.95) -> tuple[float, float]:
    """Distribution-free interval for the median from order statistics (binomial ranks)."""
    xs = sorted(values)
    n = len(xs)
    if n == 0:
        return float('nan'), float('nan')
    a = 1.0 - level
    lo = int(stats.binom.ppf(a / 2, n, 0.5))            # rank of the lower bound (1-based -> index lo-1)
    hi = int(stats.binom.ppf(1 - a / 2, n, 0.5)) + 1
    lo = min(max(lo, 1), n)
    hi = min(max(hi, 1), n)
    return float(xs[lo - 1]), float(xs[hi - 1])


def clopper_pearson(k: int, n: int, level: float = 0.95) -> tuple[float, float]:
    if n == 0:
        return float('nan'), float('nan')
    a = 1.0 - level
    lo = 0.0 if k == 0 else stats.beta.ppf(a / 2, k, n - k + 1)
    hi = 1.0 if k == n else stats.beta.ppf(1 - a / 2, k + 1, n - k)
    return float(lo), float(hi)
