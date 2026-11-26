"""Ziel 3 – vier Kurven: Rechenkern des Belastungsmodells (ohne Streamlit).

Eins-zu-eins-Übertragung des bestätigten JavaScript-Prototyps (Kapitel 4/5; dieselbe
Kalibrierung wie ``model.py``).  Gegenüber ``model.py`` kommen hinzu

* der volle Prioritätsvektor P (hoch = 3, mittel = 2, niedrig = 1; die Simulation hatte
  eine Quelle mittlerer Priorität),
* die Kaltstart-Sensitivität ``tpr_T``: eine Krise zählt nur, wenn die Eskalation vor
  ihrem Ende beginnt (alle Quellen beginnen gleichzeitig, Dauer D = d0 + Exp(d_true − d0)),
* ``avoid_day``: unnötige Eskalationen ohne die gemeinsamen Artefakte aller Quellen
  (c_art), die jede Regel gleich durchlässt,
* ``scale_P``: Prioritätsvektor für eine andere Quellenzahl mit denselben Anteilen.

Die Summe über die aktiven Quellenmengen läuft wie im Prototyp als Bitmaske über alle
nichtleeren Teilmengen (``subsets``); Rundung mit ``rhu`` (floor(x + 0,5), kein
Banker's Rounding).

Stückzahlen (Fehleskalationen je Tag) werden seit der Monte-Carlo-Prüfung als **Episoden**
gezählt, d. h. als Übergänge in eine eskalierende Teilmenge (``episode_rate``): ein Zufalls-
Zusammentreffen von r unabhängigen Fehlalarmen dauert im Mittel nur d_false/r, die alte
Umrechnung Zeitanteil·86400/d_false unterschätzte solche Episoden etwa um den Faktor r
(2-aus-N um 20–30 %).  Die alte Umrechnung bleibt als ``Result.false_day_zeitanteil`` erhalten;
ARR und System-TPR (Zeitanteile, Kontrollzahlen aus Kapitel 5) ändern sich nicht.  Für adaptiv
ist die Episodenzahl eine obere Grenze, weil das Fenster T wie in Kapitel 5 nicht wirkt.

Namen gegenüber dem Prototyp:  n → n, FDR → fdr, dF → d_false, dT → d_true, P → P,
c → c_art, TPR → tpr, d0 → d0, T → T, dmin → delta_min, alpha → alpha;
``over.FPR`` → ``mfpr_direct`` (|M|·FPR direkt vorgegeben, FPR = mfpr_direct/|M|,
P(C) bleibt kalibriert).

Regeln: ``"ohne"`` (ohne Filter), ``"r2"`` (2-aus-N), ``"r3"`` (3-aus-N), ``"adaptiv"``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

DAY = 86400.0
INF = float("inf")
NAN = float("nan")
_TOL = 1e-12

HOCH, MITTEL, NIEDRIG = 3, 2, 1
PRIO_LABELS: Dict[str, int] = {"hoch": HOCH, "mittel": MITTEL, "niedrig": NIEDRIG}
PRIO_NAMES: Dict[int, str] = {HOCH: "hoch", MITTEL: "mittel", NIEDRIG: "niedrig"}

RULES: Tuple[str, ...] = ("ohne", "r2", "r3", "adaptiv")
RULE_NAMES: Dict[str, str] = {"ohne": "ohne Filter", "r2": "2-aus-N", "r3": "3-aus-N", "adaptiv": "adaptiv"}
TABLE_RULES: Tuple[str, ...] = ("ohne", "r2", "r3", "adaptiv")      # Reihenfolge der Tabelle
K4_RULES: Tuple[str, ...] = ("r2", "adaptiv", "r3")                   # Reihenfolge der Säulen in K4


def rhu(x: float) -> int:
    """Kaufmännisches Runden floor(x + 0,5 + 1e-12), wie ``rhu`` im Prototyp (2,5 → 3)."""
    return int(math.floor(x + 0.5 + 1e-12))


# ─────────────────────────────────────────────────────────────────────────────
# Parameter, Kalibrierung, Szenarien
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Params:
    """Alle Eingänge (Standard = Median-Intensivstation)."""

    n: float = 37.5                 # Alarme je Quelle und Tag
    fdr: float = 0.90               # Anteil nicht relevanter Alarme
    d_false: float = 8.0            # Dauer eines Fehlalarms [s] (= μ im Prototyp)
    d_true: float = 30.0            # mittlere Dauer einer Krise [s]
    P: Tuple[int, ...] = (3, 3, 1, 1)   # Prioritäten der Quellen, |M| = len(P)
    c_art: float = 0.5              # gemeinsame Artefakte
    tpr: float = 0.90               # TPR_j je Quelle
    d0: float = 15.0                # kürzeste Krise [s]
    T: float = 10.0                 # Fenster [s]
    delta_min: float = 0.5          # Untergrenze der Schwelle
    alpha: float = 0.5              # Gewicht im Schweregrad
    mfpr_direct: Optional[float] = None   # |M|·FPR direkt (P(C) bleibt kalibriert)
    pc_direct: Optional[float] = None     # P(C) direkt (Messwert der Simulation) statt Kalibrierung

    def __post_init__(self) -> None:
        if len(self.P) < 2:
            raise ValueError("|M| muss mindestens 2 sein")
        if any(x not in (HOCH, MITTEL, NIEDRIG) for x in self.P):
            raise ValueError("Prioritäten müssen 3 (hoch), 2 (mittel) oder 1 (niedrig) sein")
        if not 0 < self.tpr < 1:
            raise ValueError("TPR_j muss in (0, 1) liegen")
        if not 0 <= self.fdr < 1:
            raise ValueError("FDR muss in [0, 1) liegen")
        if not 0 <= self.c_art <= 1:
            raise ValueError("c_art muss in [0, 1] liegen")
        if not 0 < self.delta_min <= 1:
            raise ValueError("δ_min muss in (0, 1] liegen")
        if not 0 <= self.alpha <= 1:
            raise ValueError("α muss in [0, 1] liegen")
        if self.n <= 0 or self.d_false <= 0 or self.d_true <= 0 or self.T <= 0 or self.d0 < 0:
            raise ValueError("n, d_false, d_true, T müssen positiv und d0 ≥ 0 sein")
        if self.mfpr_direct is not None and self.mfpr_direct <= 0:
            raise ValueError("|M|·FPR muss positiv sein")
        if self.pc_direct is not None and not 0 < self.pc_direct < 1:
            raise ValueError("P(C) direkt muss in (0, 1) liegen")

    @property
    def M(self) -> int:
        return len(self.P)

    def counts(self) -> Tuple[int, int, int]:
        """(hoch, mittel, niedrig)."""
        return (self.P.count(HOCH), self.P.count(MITTEL), self.P.count(NIEDRIG))


@dataclass(frozen=True)
class Calibration:
    """Aus den Stationsgrößen abgeleitete Größen (Seitenleiste „Abgeleitet“)."""

    N_d: float          # Alarme je Bett und Tag = n·|M|
    PC: float           # Krisenprävalenz P(C)
    FPR: float          # Fehlalarmbelegung je Quelle
    MFPR: float         # |M|·FPR
    w: float            # w = ln(TPR/FPR)
    ceiling: float      # Obergrenze 1 − c_art/|M|
    crises_day: float   # Krisen je Bett und Tag = P(C)·86400/d_true

    @property
    def valid(self) -> bool:
        return 0 < self.PC < 1 and 0 < self.FPR < 1 and self.w > 0


def calibrate(p: Params) -> Calibration:
    """``calib`` des Prototyps: P(C) aus echten, FPR aus falschen Alarmen (Weg A).

    ``pc_direct`` (Messwert) ersetzt die kalibrierte Prävalenz, ``mfpr_direct`` die kalibrierte Belegung.
    """
    PC = p.pc_direct if p.pc_direct is not None else p.n * (1 - p.fdr) * p.d_true / (DAY * p.tpr)
    if p.mfpr_direct is not None:
        FPR = p.mfpr_direct / p.M
    else:
        FPR = p.n * p.fdr * p.d_false / (DAY * (1 - PC)) if PC < 1 else NAN
    w = math.log(p.tpr / FPR) if FPR > 0 else NAN
    return Calibration(N_d=p.n * p.M, PC=PC, FPR=FPR, MFPR=p.M * FPR, w=w,
                       ceiling=1 - p.c_art / p.M, crises_day=PC * DAY / p.d_true)


SCENARIOS: Dict[str, dict] = {
    "Median-Intensivstation":   dict(n=37.5, d_false=8.0, d_true=30.0, P=(3, 3, 1, 1)),
    "geringe Last":             dict(n=25.0),
    "hohe Last":                dict(n=100.0),
    "sehr hohe Last":           dict(n=273.0),
    "lange Dauern":             dict(d_false=30.0, d_true=60.0),
    "stark instrumentiert":     dict(n=33.3, P=(3,) * 6 + (1,) * 6),
    "Grenzfall":                dict(n=273.0, d_false=30.0, d_true=60.0),
    "Parameter der Simulation": dict(n=300.0, d_false=8.3, d_true=30.2, P=(3, 3, 2, 1), c_art=0.06),
}
DEFAULT_SCENARIO = "Median-Intensivstation"


def scenario_params(name: str) -> Params:
    """Standardwerte plus die Abweichungen des Szenarios."""
    return Params(**SCENARIOS[name])


def scenario_name(p: Params) -> Optional[str]:
    """Name des Szenarios, dessen Parameter p exakt entsprechen (sonst None)."""
    for name in SCENARIOS:
        if replace(scenario_params(name), mfpr_direct=p.mfpr_direct, pc_direct=p.pc_direct) == p:
            return name
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Prioritäten
# ─────────────────────────────────────────────────────────────────────────────

def scale_P(P: Sequence[int], M: int) -> Tuple[int, ...]:
    """``scaleP``: Prioritätsvektor für |M| Quellen mit denselben Anteilen hoch/mittel/niedrig."""
    n = len(P)
    h = sum(1 for x in P if x == HOCH) / n
    m = sum(1 for x in P if x == MITTEL) / n
    nh, nm = rhu(h * M), rhu(m * M)
    return tuple(HOCH if j < nh else MITTEL if j < nh + nm else NIEDRIG for j in range(M))


def sdc_of(prs: Sequence[int], P: Sequence[int], alpha: float) -> float:
    """SDC_score einer Quellenmenge (3.16): α·max/Pmax + (1 − α)·Summe/Psum."""
    Pmax, Psum = max(P), sum(P)
    return alpha * max(prs) / Pmax + (1 - alpha) * sum(prs) / Psum


@lru_cache(maxsize=256)
def subsets(P: Tuple[int, ...]) -> Tuple[Tuple[int, ...], ...]:
    """Alle nichtleeren Teilmengen als Bitmaske 1 … 2^|M| − 1: je Maske die Prioritäten der aktiven Quellen."""
    M = len(P)
    out: List[Tuple[int, ...]] = []
    for mask in range(1, 1 << M):
        out.append(tuple(P[j] for j in range(M) if mask & (1 << j)))
    return tuple(out)


# ─────────────────────────────────────────────────────────────────────────────
# Ausgang einer Quellenmenge, Krisendauer
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Outcome:
    """Ausgang einer Quellenmenge für eine Regel (Kaltstart: alle beginnen gleichzeitig).

    branch: "sofort" (t = T·k/r bei adaptiv, 0 bei k-aus-N) | "schmelzen" (t = t_esc) | "nie" (t = ∞).
    """

    k: int
    sdc: float
    branch: str
    t: float


def outcome(prs: Sequence[int], p: Params, rule: str) -> Outcome:
    """``outcome`` des Prototyps."""
    M, r = p.M, len(prs)
    if rule == "adaptiv":
        sdc = sdc_of(prs, p.P, p.alpha)
        k = rhu(M - (M - 2) * sdc)
        if r >= k:
            return Outcome(k, sdc, "sofort", p.T * k / r)
        if r >= p.delta_min * k - _TOL and sdc > 0:
            return Outcome(k, sdc, "schmelzen", p.T + p.T / sdc * math.log(k / r))
        return Outcome(k, sdc, "nie", INF)
    if rule not in ("r2", "r3"):
        raise ValueError(f"unbekannte Regel {rule!r}")
    k = min(2 if rule == "r2" else 3, M)
    return Outcome(k, NAN, "sofort", 0.0) if r >= k else Outcome(k, NAN, "nie", INF)


def surv_crisis(t: float, p: Params) -> float:
    """Anteil der Krisen, die mindestens t dauern: D = d0 + Exp(d_true − d0)."""
    if not math.isfinite(t):
        return 0.0
    d0 = min(p.d0, p.d_true - 1)
    return 1.0 if t <= d0 else math.exp(-(t - d0) / (p.d_true - d0))


# ─────────────────────────────────────────────────────────────────────────────
# Auswertung einer Regel
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Result:
    rule: str
    PC: float
    FPR: float
    noise_f: float      # Fehlalarmbelegung mit Regel (Zeitanteil)
    noise_r: float      # Fehlalarmbelegung ohne Filter (Zeitanteil)
    ARR: float          # 1 − noise_f/noise_r (Zeitanteil, Kapitel 5)
    false_day: float    # Fehleskalationen je Bett und Tag, als Episoden gezählt (``episode_rate``)
    avoid_day: float    # unnötige Eskalationen je Tag (Episoden ohne gemeinsame Artefakte aller Kanäle)
    common_day: float   # Eskalationen durch gemeinsame Artefakte aller Kanäle je Tag (= false_day − avoid_day)
    raw_day: float      # Fehlalarme je Tag ohne Filter = n·|M|·FDR
    tpr_inf: float      # System-TPR, Krise hält an (Kapitel 5)
    tpr_T: float        # Anteil erkannter Krisen bei endlicher Dauer (Kaltstart)
    crises_day: float   # Krisen je Bett und Tag
    false_day_zeitanteil: float = NAN   # alte Umrechnung noise_f·86400/d_false (vor der Monte-Carlo-Prüfung)
    avoid_day_zeitanteil: float = NAN   # dito ohne gemeinsame Artefakte

    @property
    def remaining(self) -> float:
        """verbleibende Fehlalarme als Anteil von ohne Filter, nach Anzahl = false_day/raw_day."""
        return self.false_day / self.raw_day if self.raw_day > 0 else NAN

    @property
    def reduction_count(self) -> float:
        """Reduktion nach Anzahl = 1 − false_day/raw_day (ARR bleibt der Zeitanteil)."""
        return 1.0 - self.remaining

    @property
    def missed_per_1000(self) -> float:
        return 1000.0 * (1 - self.tpr_T)


def _entries(key: Tuple[int, ...], branch_of) -> int:
    """Zahl der Kanäle j ∈ F, über die F aus einem nicht sofort eskalierenden Zustand betreten wird.

    F ist durch die sortierte Prioritätenmenge ``key`` gegeben; F \\ {j} hängt nur von der Priorität von j ab.
    Zählt j, wenn F \\ {j} leer ist oder dort nicht „sofort“ eskaliert wird.
    """
    n = 0
    for v in set(key):
        sub = list(key)
        sub.remove(v)
        if not sub or branch_of(tuple(sub)) != "sofort":
            n += key.count(v)
    return n


def _subset_sums(p: Params, rule: str, cal: Calibration) -> Tuple[float, float, float, float, float]:
    """Summen über alle nichtleeren Teilmengen: (sig, sig_T, coinc, delayed, rate_avoid).

    sig/sig_T: Krisenanteile (tpr_inf, tpr_T); coinc/delayed: Zeitanteile der Fehlalarme (ARR);
    rate_avoid: Episoden je Sekunde ohne gemeinsame Artefakte und ohne den Faktor (1 − P(C)):
    Σ_F entries(F)·p_N(F)/μ · [1 bzw. exp(−r·t_esc/μ) beim Schmelzen].
    """
    M, FPR = p.M, cal.FPR
    TPR, c, mu = p.tpr, p.c_art, p.d_false
    q = (1 - c) * FPR
    sig = sig_T = coinc = delayed = rate = 0.0
    seen: Dict[Tuple[int, ...], Outcome] = {}      # Ausgang hängt nur von der Prioritätenmenge ab
    entries: Dict[Tuple[int, ...], int] = {}

    def out(key: Tuple[int, ...]) -> Outcome:
        o = seen.get(key)
        if o is None:
            o = seen[key] = outcome(key, p, rule)
        return o

    for prs in subsets(p.P):
        r = len(prs)
        pC = TPR ** r * (1 - TPR) ** (M - r)
        pN = q ** r * (1 - q) ** (M - r)
        key = tuple(sorted(prs))
        o = out(key)
        if o.branch == "nie":
            continue
        sig += pC
        sig_T += pC * surv_crisis(o.t, p)
        e = entries.get(key)
        if e is None:
            e = entries[key] = _entries(key, lambda k: out(k).branch)
        if o.branch == "sofort":
            coinc += pN
            rate += e * pN / mu
        else:
            share = math.exp(-r * o.t / mu)
            delayed += pN * share
            rate += e * pN / mu * share
    return sig, sig_T, coinc, delayed, rate


def episode_rate(p: Params, rule: str = "adaptiv", cal: Optional[Calibration] = None) -> Tuple[float, float]:
    """Fehleskalationen je Sekunde als Episoden, ohne den Faktor (1 − P(C)): (ohne gemeinsame Artefakte, gemeinsame).

    Übergänge in eine eskalierende Teilmenge F: entries(F)·p_N(F)/μ (beim Schmelzen × exp(−r·t_esc/μ)) mit
    q = (1 − c_art)·FPR, μ = d_false; gemeinsame Artefakte aller Kanäle: c_art·FPR/μ.  Ohne Filter:
    (|M| − c_art)·FPR/μ und c_art·FPR/μ.
    """
    if rule not in RULES:
        raise ValueError(f"unbekannte Regel {rule!r}")
    cal = cal or calibrate(p)
    common = p.c_art * cal.FPR / p.d_false
    if rule == "ohne":
        return p.M * cal.FPR / p.d_false - common, common
    _sig, _sig_T, _coinc, _delayed, rate = _subset_sums(p, rule, cal)
    return rate, common


def _pack(p: Params, rule: str, PC: float, FPR: float, noise_f: float, noise_r: float,
          tpr_inf: float, tpr_T: float, rate_avoid: float) -> Result:
    """``pack`` des Prototyps, Stückzahlen jedoch als Episoden (``rate_avoid`` je Sekunde ohne (1 − P(C)))."""
    per_day = DAY / p.d_false
    common = (1 - PC) * p.c_art * FPR * per_day
    false_zeit = noise_f * per_day
    avoid_day = rate_avoid * DAY * (1 - PC)
    return Result(rule=rule, PC=PC, FPR=FPR, noise_f=noise_f, noise_r=noise_r,
                  ARR=1 - noise_f / noise_r if noise_r > 0 else NAN,
                  false_day=avoid_day + common, avoid_day=avoid_day, common_day=common,
                  raw_day=noise_r * per_day, tpr_inf=tpr_inf, tpr_T=tpr_T,
                  crises_day=PC * DAY / p.d_true,
                  false_day_zeitanteil=false_zeit, avoid_day_zeitanteil=max(0.0, false_zeit - common))


def evaluate(p: Params, rule: str = "adaptiv", cal: Optional[Calibration] = None,
             pc_direct: Optional[float] = None) -> Result:
    """``evaluate`` des Prototyps: Belegungen, ARR (Zeitanteil), Episoden je Tag, tpr_inf, tpr_T.

    ``pc_direct``: gemessene Prävalenz P(C) statt der Kalibrierung (gleichwertig zu ``Params.pc_direct``).
    """
    if rule not in RULES:
        raise ValueError(f"unbekannte Regel {rule!r}")
    if pc_direct is not None:
        p, cal = replace(p, pc_direct=pc_direct), None
    cal = cal or calibrate(p)
    M, PC, FPR = p.M, cal.PC, cal.FPR
    TPR, c, mu = p.tpr, p.c_art, p.d_false
    noise_r = (1 - PC) * M * FPR
    if rule == "ohne":
        tpr = 1 - (1 - TPR) ** M
        return _pack(p, rule, PC, FPR, noise_r, noise_r, tpr, tpr, (M - c) * FPR / mu)
    sig, sig_T, coinc, delayed, rate = _subset_sums(p, rule, cal)
    noise_f = (1 - PC) * (coinc + delayed + c * FPR)
    return _pack(p, rule, PC, FPR, noise_f, noise_r, sig, sig_T, rate)


def evaluate_all(p: Params, rules: Iterable[str] = TABLE_RULES, cal: Optional[Calibration] = None) -> Dict[str, Result]:
    cal = cal or calibrate(p)
    return {rule: evaluate(p, rule, cal) for rule in rules}


# ─────────────────────────────────────────────────────────────────────────────
# Einheiten
# ─────────────────────────────────────────────────────────────────────────────

# Fehlalarme in Tabelle, K1 und K2: Faktor auf „je Bett und Tag“
UNITS: Dict[str, float] = {
    "je Bett und Tag": 1.0,
    "je Bett und Stunde": 1.0 / 24.0,
    "je Station (10 Betten) und Tag": 10.0,
    "je Station (10 Betten) und Monat": 300.0,
}
DEFAULT_UNIT = "je Bett und Tag"

# K4: Faktor F auf „je Bett und Tag“
K4_FACTORS: Dict[str, float] = {
    "je Station (10 Betten) und Tag": 10.0,
    "je Station (10 Betten) und Woche": 70.0,
    "je Station (10 Betten) und Monat": 300.0,
}
DEFAULT_K4 = "je Station (10 Betten) und Woche"


# ─────────────────────────────────────────────────────────────────────────────
# Kurven: x-Achsen und Sweeps
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Axis:
    short: str          # Kurzname in der Auswahl
    label: str          # Achsenbeschriftung
    lo: float
    hi: float
    log: bool = False
    integer: bool = False


X_AXES: Dict[str, Axis] = {
    "c_art": Axis("c_art", "Anteil gemeinsamer Artefakte (c_art)", 0.0, 1.0),
    "MFPR": Axis("|M|·FPR", "Fehlalarmbelegung je Bett (|M|·FPR), logarithmisch", 0.005, 1.2, log=True),
    "n": Axis("n", "Alarme je Quelle und Tag (n)", 5.0, 300.0),
    "M": Axis("|M|", "Zahl der Alarmquellen |M| (Prioritäten anteilig skaliert)", 2, 12, integer=True),
    "tpr": Axis("TPR_j", "Sensitivität je Quelle (TPR_j)", 0.5, 0.99),
    "d_true": Axis("d_true", "mittlere Dauer einer Krise (d_true) [s]", 10.0, 120.0),
    "d0": Axis("d0", "kürzeste Krise (d0) [s]", 0.0, 30.0),
}
K1_AXES: Tuple[str, ...] = ("c_art", "MFPR", "n", "M")
K2_AXES: Tuple[str, ...] = ("MFPR", "c_art", "n", "M")
K3_AXES: Tuple[str, ...] = ("tpr", "d_true", "d0")

K2_Y: Dict[str, str] = {
    "prozent": "verbleibende Fehlalarme (% von ohne Filter)",
    "absolut": "Fehlalarme absolut",
    "differenz": "Differenz zu adaptiv",
}
K3_Y: Dict[str, str] = {
    "erkannt": "erkannte Krisen (%)",
    "verpasst": "verpasste Krisen je 1000",
}


def x_grid(name: str, n_points: int = 60) -> np.ndarray:
    a = X_AXES[name]
    if a.integer:
        return np.arange(int(a.lo), int(a.hi) + 1, dtype=float)
    if a.log:
        return np.geomspace(a.lo, a.hi, n_points)
    return np.linspace(a.lo, a.hi, n_points)


def set_param(p: Params, name: str, value: float) -> Params:
    """Parameter der x-Achse setzen (|M| skaliert P, |M|·FPR gibt das Rauschen direkt vor)."""
    if name == "M":
        return replace(p, P=scale_P(p.P, int(round(value))))
    if name == "MFPR":
        return replace(p, mfpr_direct=float(value))
    return replace(p, **{name: float(value)})


def current_value(p: Params, name: str, cal: Optional[Calibration] = None) -> float:
    """Eingestellter Wert des Parameters der x-Achse."""
    if name == "M":
        return float(p.M)
    if name == "MFPR":
        return (cal or calibrate(p)).MFPR
    return float(getattr(p, name))


def sweep(p: Params, rule: str, name: str, xs: Sequence[float]) -> List[Result]:
    return [evaluate(set_param(p, name, x), rule) for x in xs]


# ─────────────────────────────────────────────────────────────────────────────
# K4: Fehlerbilanz über |M|
# ─────────────────────────────────────────────────────────────────────────────

K4_MS: Tuple[int, ...] = tuple(range(4, 13))


def k4_values(res: Result, F: float) -> Tuple[float, float]:
    """(unnötige Eskalationen, verpasste Krisen) je Station und Zeitraum F."""
    return res.avoid_day * F, (1 - res.tpr_T) * res.crises_day * F


def k4_table(p: Params, F: float, Ms: Sequence[int] = K4_MS,
             rules: Sequence[str] = K4_RULES) -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    """Je Regel zwei Reihen über Ms (P = scale_P(P, M))."""
    out: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
    for rule in rules:
        u, v = [], []
        for M in Ms:
            a, b = k4_values(evaluate(replace(p, P=scale_P(p.P, M)), rule), F)
            u.append(a)
            v.append(b)
        out[rule] = (np.array(u), np.array(v))
    return out
