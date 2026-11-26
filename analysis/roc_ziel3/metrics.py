"""Kennzahlen nach Zeitanteil und nach Stück für Geräte, k-aus-N und adaptiv; AUC und Interpolation.

Definitionen (Vorgabe des Betreuers / PC-2-Auswertung ``roc_kausn.csv``)
--------------------------------------------------------------------------
* Krise = [Beginn, Ende]; stabil = außerhalb [Beginn − G, Ende + G] mit Schutzsaum G = 10 s;
  die Säume selbst zählen weder als Krise noch als stabil.
* TPR (Zeit) = Σ |Rot ∩ Krise| / Σ |Krise|;   FPR (Zeit) = Σ |Rot ∩ stabil| / Σ |stabil|.
* Ereignis = zusammenhängender Rot-Abschnitt (Episode).  Falsch = Episode, die keine Krise
  [Beginn, Ende + G] berührt.  Erkannt = Krise, die von einer Episode berührt wird (Rot bis Ende + G).
* AUC: Trapezregel über die nach FPR sortierten Punkte mit den Ankern (0, 0) und (1, 1);
  partieller AUC: Fläche über FPR ∈ [0, b] (linear interpoliert, Anker (0, 0)), geteilt durch b.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .journals import Journals, SOURCES

Interval = Tuple[float, float]
GUARD_S = 10.0


# ─────────────────────────────────────────────────────────────────────────────
# Intervallrechnung
# ─────────────────────────────────────────────────────────────────────────────

def union(ivs: Iterable[Interval]) -> List[Interval]:
    """Sortieren und überlappende/berührende Abschnitte verschmelzen; leere entfernen."""
    s = sorted((a, b) for a, b in ivs if b > a)
    out: List[Interval] = []
    for a, b in s:
        if out and a <= out[-1][1]:
            if b > out[-1][1]:
                out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def total(ivs: Iterable[Interval]) -> float:
    return float(sum(b - a for a, b in ivs))


def clip(ivs: Iterable[Interval], lo: float, hi: float) -> List[Interval]:
    return [(max(a, lo), min(b, hi)) for a, b in ivs if min(b, hi) > max(a, lo)]


def intersect(a: Sequence[Interval], b: Sequence[Interval]) -> List[Interval]:
    """Schnitt zweier verschmolzener, sortierter Listen."""
    out: List[Interval] = []
    i = j = 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if hi > lo:
            out.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def complement(ivs: Sequence[Interval], lo: float, hi: float) -> List[Interval]:
    out: List[Interval] = []
    t = lo
    for a, b in union(clip(ivs, lo, hi)):
        if a > t:
            out.append((t, a))
        t = max(t, b)
    if hi > t:
        out.append((t, hi))
    return out


def pad(ivs: Iterable[Interval], before: float, after: float) -> List[Interval]:
    return union((a - before, b + after) for a, b in ivs)


def touches(ivs: Sequence[Interval], lo: float, hi: float) -> bool:
    """Berührt eine der (sortierten) Abschnitte das Fenster [lo, hi]?"""
    for a, b in ivs:
        if a > hi:
            break
        if b >= lo and a <= hi:
            return True
    return False


def k_of_n(lists: Sequence[Sequence[Interval]], k: int, latch: bool = True) -> List[Interval]:
    """Eskalationsabschnitte der Regel k-aus-N aus den ON-Abschnitten der Quellen (Sweep über die Kanten).

    ``latch=True`` (Dashboard und PC-2-Auswertung ``roc_kausn.csv``): ein Abschnitt beginnt, sobald
    mindestens k Quellen aktiv sind, und endet erst, wenn keine Quelle mehr aktiv ist (die Eskalation
    hält, bis alle Alarme des Bettes gelöscht sind).  ``latch=False``: Abschnitt genau solange ≥ k aktiv
    sind.  Bei gleicher Zeit werden OFF-Kanten vor ON-Kanten verarbeitet (so zählt PC 2 die Ereignisse).
    """
    edges: List[Tuple[float, int]] = []
    for ivs in lists:
        for a, b in ivs:
            if b > a:
                edges.append((a, 1))
                edges.append((b, -1))
    edges.sort(key=lambda e: (e[0], e[1]))         # bei gleicher Zeit zuerst −1 (OFF), dann +1 (ON)
    out: List[Interval] = []
    n = 0
    t_on: Optional[float] = None
    for t, d in edges:
        n += d
        if n >= k and t_on is None:
            t_on = t
        elif t_on is not None and (n == 0 if latch else n < k):
            if t > t_on:
                out.append((t_on, t))
            t_on = None
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Wahrheit je Patient
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PatientTruth:
    patient: str
    window: Interval
    crises: List[Interval]            # geschlossene Krisen [Beginn, Ende] (für TPR, Erkennung)
    open_crises: List[Interval]       # am Laufende offene (nur Ausschluss aus „stabil“)
    stable: List[Interval]            # außerhalb aller Krisen ± G
    crossed: List[Dict[str, bool]] = field(default_factory=list)   # je geschlossener Krise: Quelle -> Schwelle erreicht

    @property
    def crisis_time(self) -> float:
        return total(self.crises)

    @property
    def stable_time(self) -> float:
        return total(self.stable)


def truth_from_journals(j: Journals, guard: float = GUARD_S) -> Dict[str, PatientTruth]:
    out: Dict[str, PatientTruth] = {}
    lo, hi = j.t_start, j.t_end
    for p in j.patients:
        closed = [(c.t_start, c.t_end) for c in j.crises if c.patient == p and c.closed]
        crossed = [dict(c.crossed) for c in j.crises if c.patient == p and c.closed]
        opened = [(c.t_start, hi) for c in j.crises if c.patient == p and not c.closed]
        closed_c = clip(closed, lo, hi)
        stable = complement(pad(closed + opened, guard, guard), lo, hi)
        out[p] = PatientTruth(patient=p, window=(lo, hi), crises=closed_c, open_crises=clip(opened, lo, hi),
                              stable=stable, crossed=crossed)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Kennzahlen einer Regel / eines Geräts
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RateResult:
    tpr: float
    fpr: float                        # Variante (i): alle Rot-Zeit im stabilen Zustand / stabile Zeit
    t_crisis: float
    t_stable: float
    t_in_crisis: float
    t_in_stable: float
    n_crises: int
    n_detected: int                   # mild: Rot berührt [Beginn, Ende + G]
    n_events: int
    n_false: int
    bed_hours: float
    t_false_total: float = 0.0        # Dauer aller falschen Episoden [s]
    n_detected_strict: int = 0        # streng: Rot berührt [Beginn, Ende]
    delays: Tuple[float, ...] = ()    # Verzögerung je mild erkannter Krise [s] (0, wenn Rot schon vor Beginn)

    @property
    def detected(self) -> float:
        return self.n_detected / self.n_crises if self.n_crises else float("nan")

    @property
    def detected_strict(self) -> float:
        return self.n_detected_strict / self.n_crises if self.n_crises else float("nan")

    @property
    def late(self) -> float:
        """verspätet erkannt = mild, aber nicht streng (Rot erst nach Krisenende, binnen G)."""
        return (self.n_detected - self.n_detected_strict) / self.n_crises if self.n_crises else float("nan")

    @property
    def fpr_ii(self) -> float:
        """Variante (ii): nur Zeit der falschen Episoden (berühren keine Krise ± G) / stabile Zeit."""
        return self.t_false_total / self.t_stable if self.t_stable else float("nan")

    @property
    def tail_share(self) -> float:
        """Anteil der stabilen Rot-Zeit (Variante i), der NICHT aus falschen Episoden stammt (Hälse, Frühstarts)."""
        return 1.0 - self.t_false_total / self.t_in_stable if self.t_in_stable > 0 else float("nan")

    def delay_quantile(self, q: float) -> float:
        if not self.delays:
            return float("nan")
        d = sorted(self.delays)
        return d[min(len(d) - 1, int(q * len(d)))]

    @property
    def delay_median(self) -> float:
        return self.delay_quantile(0.5)

    @property
    def delay_p90(self) -> float:
        return self.delay_quantile(0.9)

    @property
    def false_per_bed_h(self) -> float:
        return self.n_false / self.bed_hours if self.bed_hours else float("nan")

    @property
    def events_per_bed_day(self) -> float:
        return 24.0 * self.n_events / self.bed_hours if self.bed_hours else float("nan")

    @property
    def d_false(self) -> float:
        """mittlere Dauer einer falschen Episode [s]"""
        return self.t_false_total / self.n_false if self.n_false else float("nan")


def rate(rot: Dict[str, Sequence[Interval]], truth: Dict[str, PatientTruth], bed_hours: float,
         guard: float = GUARD_S) -> RateResult:
    """TPR/FPR nach Zeit, erkannte Krisen und falsche Episoden über alle Patienten.

    Wie die PC-2-Auswertung: die Krisenzeit für TPR enthält auch eine am Laufende offene Krise (bis zum
    Fensterende); erkannt/n_krisen zählen nur geschlossene Krisen; eine Episode ist falsch, wenn sie keine
    Krise ± G berührt.  Episoden werden nicht verschmolzen (berührende Abschnitte zählen einzeln).
    """
    t_crisis = t_stable = t_in_c = t_in_s = t_false = 0.0
    n_crises = n_det = n_strict = n_ev = n_false = 0
    delays: List[float] = []
    for p, tr in truth.items():
        ivs = sorted(clip(rot.get(p, []), *tr.window))
        merged = union(ivs)
        crises_t = union(tr.crises + tr.open_crises)
        t_crisis += total(crises_t)
        t_stable += tr.stable_time
        t_in_c += total(intersect(merged, crises_t))
        t_in_s += total(intersect(merged, tr.stable))
        n_crises += len(tr.crises)
        for s, e in tr.crises:
            if touches(merged, s, e + guard):
                n_det += 1
                first = next(a for a, b in merged if b >= s and a <= e + guard)
                delays.append(max(0.0, first - s))
            if touches(merged, s, e):
                n_strict += 1
        windows = pad(tr.crises + tr.open_crises, guard, guard)
        for a, b in ivs:
            n_ev += 1
            if not touches(windows, a, b):
                n_false += 1
                t_false += b - a
    return RateResult(tpr=t_in_c / t_crisis if t_crisis else float("nan"),
                      fpr=t_in_s / t_stable if t_stable else float("nan"),
                      t_crisis=t_crisis, t_stable=t_stable, t_in_crisis=t_in_c, t_in_stable=t_in_s,
                      n_crises=n_crises, n_detected=n_det, n_events=n_ev, n_false=n_false,
                      bed_hours=bed_hours, t_false_total=t_false, n_detected_strict=n_strict,
                      delays=tuple(delays))


# ─────────────────────────────────────────────────────────────────────────────
# Messgrößen für die Modellerweiterungen E1 (versetzter Einstieg) und E2 (teilweise gemeinsame Artefakte)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CrisisEntry:
    """Eine geschlossene Krise: je Quelle Teilnahme und Eintrittsverzögerung."""

    patient: str
    t_start: float
    t_end: float
    participates: Dict[str, bool]     # ON-Abschnitt berührt [Beginn, Ende]
    u: Dict[str, float]               # Verzögerung ab Krisenbeginn bis zum ersten Einschalten (0 = schon aktiv)
    tail: Dict[str, float]            # Ausschalten minus Krisenende (negativ = vor dem Ende aus)

    @property
    def duration(self) -> float:
        return self.t_end - self.t_start


def crisis_entries(src: Dict[str, Dict[str, List[Interval]]], truth: Dict[str, PatientTruth]) -> List[CrisisEntry]:
    out: List[CrisisEntry] = []
    for p, tr in truth.items():
        per_src = {s: union(src[s].get(p, [])) for s in SOURCES}
        for s_, e_ in tr.crises:
            part: Dict[str, bool] = {}
            u: Dict[str, float] = {}
            tail: Dict[str, float] = {}
            for s in SOURCES:
                hits = [(a, b) for a, b in per_src[s] if b >= s_ and a <= e_]
                part[s] = bool(hits)
                if hits:
                    a0, _ = hits[0]
                    u[s] = max(0.0, a0 - s_)
                    tail[s] = hits[-1][1] - e_
            out.append(CrisisEntry(patient=p, t_start=s_, t_end=e_, participates=part, u=u, tail=tail))
    return out


@dataclass(frozen=True)
class FalseEpisode:
    """Vereinigung überlappender Fehlalarme eines Patienten (Fehlalarm = ON-Abschnitt ohne Berührung einer Krise ± G)."""

    patient: str
    a: float
    b: float
    sources: Tuple[str, ...]          # beteiligte Quellen (verschieden)
    m_max: int                        # größte Zahl gleichzeitig aktiver Quellen
    t_sources: float                  # Summe der Fehlalarmzeit aller Quellen in der Episode
    L_ge: Tuple[float, ...] = ()      # Dauer mit ≥ 1, ≥ 2, ≥ 3, ≥ 4 gleichzeitig aktiven Quellen [s]

    @property
    def L(self) -> float:
        return self.b - self.a

    @property
    def m(self) -> int:
        return len(self.sources)

    def co_active(self, k: int) -> float:
        """Dauer, in der mindestens k Quellen gleichzeitig aktiv sind."""
        return self.L_ge[k - 1] if 0 < k <= len(self.L_ge) else 0.0


def false_episodes(src: Dict[str, Dict[str, List[Interval]]], truth: Dict[str, PatientTruth],
                   guard: float = GUARD_S) -> List[FalseEpisode]:
    out: List[FalseEpisode] = []
    for p, tr in truth.items():
        windows = pad(tr.crises + tr.open_crises, guard, guard)
        false_by_src = {s: union(iv for iv in src[s].get(p, []) if not touches(windows, iv[0], iv[1]))
                        for s in SOURCES}
        for a, b in union(iv for ivs in false_by_src.values() for iv in ivs):
            parts = [s for s in SOURCES if touches(false_by_src[s], a, b)]
            lists = [intersect(false_by_src[s], [(a, b)]) for s in parts]
            t_src = sum(total(x) for x in lists)
            L_ge = tuple(total(k_of_n(lists, k, latch=False)) for k in range(1, len(SOURCES) + 1))
            m_max = max((k for k in range(1, len(SOURCES) + 1) if L_ge[k - 1] > 0), default=0)
            out.append(FalseEpisode(patient=p, a=a, b=b, sources=tuple(parts), m_max=m_max, t_sources=t_src,
                                    L_ge=L_ge))
    return out


def eta_crossed(truth: Dict[str, PatientTruth], source: str) -> float:
    """Anteil der geschlossenen Krisen, in denen die Quelle laut Journal A die Schwelle erreicht hat."""
    n = hit = 0
    for tr in truth.values():
        for cr in tr.crossed:
            n += 1
            if cr.get(source, False):
                hit += 1
    return hit / n if n else float("nan")


def eta_touch(on: Dict[str, Sequence[Interval]], truth: Dict[str, PatientTruth]) -> float:
    """η_j wie in ``roc_kausn.csv``: Anteil der geschlossenen Krisen [Beginn, Ende], die ein ON-Abschnitt
    der Quelle berührt (auch ein schon vor der Krise aktiver Alarm zählt)."""
    n = hit = 0
    for p, tr in truth.items():
        u = union(on.get(p, []))
        for s, e in tr.crises:
            n += 1
            if touches(u, s, e):
                hit += 1
    return hit / n if n else float("nan")


def source_intervals(j: Journals) -> Dict[str, Dict[str, List[Interval]]]:
    """ON-Abschnitte je Quelle und Patient: {Quelle: {Patient: [(t_on, t_off)]}}."""
    return {s: {p: j.on_intervals(p, s) for p in j.patients} for s in SOURCES}


def k_of_n_rot(src: Dict[str, Dict[str, List[Interval]]], patients: Sequence[str], k: int) -> Dict[str, List[Interval]]:
    return {p: k_of_n([src[s].get(p, []) for s in SOURCES], k) for p in patients}


@dataclass(frozen=True)
class ArtefactShare:
    c_art_all: float       # Zeitanteil der Fehlalarme in Episoden mit allen 4 Quellen (c_art „a“)
    c_art_ge2: float       # … in Episoden mit ≥ 2 Quellen (c_art „b“)
    n_episodes: int
    by_n_sources: Dict[int, int]


def artefact_share(src: Dict[str, Dict[str, List[Interval]]], truth: Dict[str, PatientTruth],
                   guard: float = GUARD_S) -> ArtefactShare:
    """Fehlalarm-Episoden eines Patienten = Vereinigung überlappender Fehlalarme aller Quellen
    (Fehlalarm = ON-Abschnitt, der keine Krise ± G berührt), wie in ``messwerte_ziel3_*.json``."""
    t_all = t_ge2 = t_sum = 0.0
    n_ep = 0
    by_n: Dict[int, int] = {}
    for p, tr in truth.items():
        windows = pad(tr.crises + tr.open_crises, guard, guard)
        false_by_src = {s: [iv for iv in src[s].get(p, []) if not touches(windows, iv[0], iv[1])] for s in SOURCES}
        episodes = union(iv for ivs in false_by_src.values() for iv in ivs)
        for a, b in episodes:
            n_src = sum(1 for s in SOURCES if touches(false_by_src[s], a, b))
            dur = sum(total(intersect(union(false_by_src[s]), [(a, b)])) for s in SOURCES)   # Fehlalarmzeit je Quelle
            t_sum += dur
            n_ep += 1
            by_n[n_src] = by_n.get(n_src, 0) + 1
            if n_src >= 2:
                t_ge2 += dur
            if n_src >= len(SOURCES):
                t_all += dur
    return ArtefactShare(c_art_all=t_all / t_sum if t_sum else float("nan"),
                         c_art_ge2=t_ge2 / t_sum if t_sum else float("nan"), n_episodes=n_ep, by_n_sources=by_n)


# ─────────────────────────────────────────────────────────────────────────────
# AUC, Interpolation
# ─────────────────────────────────────────────────────────────────────────────

def roc_points(fpr: Sequence[float], tpr: Sequence[float], anchors: bool = True) -> Tuple[np.ndarray, np.ndarray]:
    """Nach FPR sortierte Punkte, optional mit Ankern (0, 0) und (1, 1)."""
    pts = sorted((float(x), float(y)) for x, y in zip(fpr, tpr) if math.isfinite(x) and math.isfinite(y))
    if anchors:
        pts = [(0.0, 0.0)] + pts + [(1.0, 1.0)]
    xs = np.array([p[0] for p in pts])
    ys = np.array([p[1] for p in pts])
    return xs, ys


def auc(fpr: Sequence[float], tpr: Sequence[float]) -> float:
    xs, ys = roc_points(fpr, tpr)
    return float(np.trapezoid(ys, xs)) if hasattr(np, "trapezoid") else float(np.trapz(ys, xs))


def partial_auc(fpr: Sequence[float], tpr: Sequence[float], fmax: float = 0.03) -> float:
    """Fläche über FPR ∈ [0, fmax], normiert auf fmax (= mittlere TPR in diesem Bereich)."""
    measured = [float(x) for x in fpr if math.isfinite(x)]
    if not measured or max(measured) < fmax:
        return float("nan")                     # Messpunkte reichen nicht bis fmax → keine Extrapolation
    xs, ys = roc_points(fpr, tpr)
    grid = np.unique(np.concatenate([xs[xs < fmax], [fmax]]))
    vals = np.interp(grid, xs, ys)
    area = float(np.trapezoid(vals, grid)) if hasattr(np, "trapezoid") else float(np.trapz(vals, grid))
    return area / fmax


def tpr_at(fpr: Sequence[float], tpr: Sequence[float], f: float) -> Tuple[float, bool]:
    """TPR bei vorgegebenem FPR, linear interpoliert (Anker (0, 0)); zweiter Wert: innerhalb der Messpunkte."""
    xs, ys = roc_points(fpr, tpr, anchors=False)
    if len(xs) == 0:
        return float("nan"), False
    inside = bool(xs[0] <= f <= xs[-1])
    xs2 = np.concatenate([[0.0], xs])
    ys2 = np.concatenate([[0.0], ys])
    if f > xs2[-1]:
        return float("nan"), False
    return float(np.interp(f, xs2, ys2)), inside


def y_at_logx(x: Sequence[float], y: Sequence[float], x0: float) -> Tuple[float, bool]:
    """Interpolation in log(x) (Stückzahlen je Bett·h); außerhalb der Punkte → NaN."""
    pts = sorted((float(a), float(b)) for a, b in zip(x, y) if a > 0 and math.isfinite(b))
    if not pts or x0 <= 0:
        return float("nan"), False
    xs = np.log([p[0] for p in pts])
    ys = np.array([p[1] for p in pts])
    lx = math.log(x0)
    if lx < xs[0] or lx > xs[-1]:
        return float("nan"), False
    return float(np.interp(lx, xs, ys)), True


def loglog_slope(x: Sequence[float], y: Sequence[float], xmax: Optional[float] = None) -> Tuple[float, int]:
    """Steigung d ln y / d ln x (kleinste Quadrate) über die Punkte mit 0 < x ≤ xmax und y > 0."""
    pts = [(math.log(a), math.log(b)) for a, b in zip(x, y)
           if a is not None and b is not None and a > 0 and b > 0 and (xmax is None or a <= xmax)]
    if len(pts) < 2:
        return float("nan"), len(pts)
    lx = np.array([p[0] for p in pts])
    ly = np.array([p[1] for p in pts])
    slope = float(np.polyfit(lx, ly, 1)[0])
    return slope, len(pts)


# ─────────────────────────────────────────────────────────────────────────────
# Latenz (zwei Bezugspunkte) und Kennzahlen je Patient (Bootstrap)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Latency:
    """Verzögerung der ersten Rot-Episode je mild erkannter Krise: ab Krisenbeginn und ab dem ersten
    Überschreiten einer Gerätegrenze (u_min der teilnehmenden Quellen; 0, wenn ein Alarm schon aktiv war)."""

    from_start: Tuple[float, ...]
    from_cross: Tuple[float, ...]

    @staticmethod
    def _q(values: Sequence[float], q: float) -> float:
        if not values:
            return float("nan")
        d = sorted(values)
        return d[min(len(d) - 1, int(q * len(d)))]

    def median(self, which: str = "cross") -> float:
        return self._q(self.from_cross if which == "cross" else self.from_start, 0.5)

    def p90(self, which: str = "cross") -> float:
        return self._q(self.from_cross if which == "cross" else self.from_start, 0.9)

    @property
    def n(self) -> int:
        return len(self.from_start)


def latencies(rot: Dict[str, Sequence[Interval]], truth: Dict[str, PatientTruth], entries: Sequence[CrisisEntry],
              guard: float = GUARD_S) -> Latency:
    ent = {(c.patient, round(c.t_start, 3)): c for c in entries}
    from_start: List[float] = []
    from_cross: List[float] = []
    for p, tr in truth.items():
        merged = union(clip(rot.get(p, []), *tr.window))
        for s, e in tr.crises:
            if not touches(merged, s, e + guard):
                continue
            first = next(a for a, b in merged if b >= s and a <= e + guard)
            c = ent.get((p, round(s, 3)))
            u_min = min((c.u[k] for k, part in c.participates.items() if part), default=0.0) if c else 0.0
            from_start.append(max(0.0, first - s))
            from_cross.append(max(0.0, first - (s + u_min)))
    return Latency(from_start=tuple(from_start), from_cross=tuple(from_cross))


PATIENT_FIELDS: Tuple[str, ...] = ("t_crisis", "t_in_crisis", "t_stable", "t_in_stable", "t_false", "n_crises",
                                   "n_det", "n_det_strict", "n_events", "n_false", "bed_h")


def per_patient(rot: Dict[str, Sequence[Interval]], truth: Dict[str, PatientTruth], hours: float,
                guard: float = GUARD_S) -> Dict[str, List[float]]:
    """Kennzahlen je Patient in der Reihenfolge ``PATIENT_FIELDS`` (Summen, die sich über Patienten addieren lassen)."""
    out: Dict[str, List[float]] = {}
    for p, tr in truth.items():
        r = rate(rot, {p: tr}, hours, guard)
        out[p] = [r.t_crisis, r.t_in_crisis, r.t_stable, r.t_in_stable, r.t_false_total, float(r.n_crises),
                  float(r.n_detected), float(r.n_detected_strict), float(r.n_events), float(r.n_false), hours]
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Ereignisbezogene ROC: eskalierte Fehlalarm-Episoden
# ─────────────────────────────────────────────────────────────────────────────

def false_rot(rot: Dict[str, Sequence[Interval]], truth: Dict[str, PatientTruth],
              guard: float = GUARD_S) -> Dict[str, List[Interval]]:
    """Je Patient die falschen Rot-Abschnitte (berühren keine Krise ± guard), verschmolzen, im Fenster."""
    out: Dict[str, List[Interval]] = {}
    for p, tr in truth.items():
        windows = pad(tr.crises + tr.open_crises, guard, guard)
        out[p] = union(iv for iv in clip(rot.get(p, []), *tr.window) if not touches(windows, iv[0], iv[1]))
    return out


def escalated_episodes(rot: Dict[str, Sequence[Interval]], episodes: Sequence[FalseEpisode],
                       truth: Dict[str, PatientTruth], guard: float = GUARD_S) -> Tuple[int, int, Dict[str, Tuple[int, int]]]:
    """Fehlalarm-Episoden (``false_episodes``) und davon eskalierte: eine Episode [a, b] gilt als eskaliert, wenn ein
    falscher Rot-Abschnitt der Regel sie schneidet.  Rückgabe (gesamt, eskaliert, je Patient (gesamt, eskaliert))."""
    fr_ = false_rot(rot, truth, guard)
    by_p: Dict[str, List[Interval]] = {}
    for e in episodes:
        by_p.setdefault(e.patient, []).append((e.a, e.b))
    per: Dict[str, Tuple[int, int]] = {}
    for p in truth:
        eps = by_p.get(p, [])
        n_esc = sum(1 for a, b in eps if touches(fr_.get(p, []), a, b))
        per[p] = (len(eps), n_esc)
    return sum(v[0] for v in per.values()), sum(v[1] for v in per.values()), per
