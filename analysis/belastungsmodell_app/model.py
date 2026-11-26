"""Analytisches Belastungsmodell für Ziel 3 (Kapitel 4, 4.4.2; Kapitel 5, 5.3).

Reine Rechenfunktionen ohne Streamlit. Die Referenz ist ``docs/thesis/kap5_model.py``
(Nachbau der Zahlen aus Kapitel 5); dieses Modul erweitert sie um

* die Kalibrierung aus Literaturgrößen (n, FDR_cnt, d_false, d_true, TPR_j),
* die Kontextverschiebung L = ln(OR) (Schwelle Θ = max(ε, k·w·δ − L)),
* die Regeln ``rein k-aus-M`` (k fest, δ_min = 1, L = 0) und ``ohne Filter``,
* die Umrechnung in Stückzahlen je Tag,
* den Anteil eskalierter Doppel-Artefakte (Kurve C) und
* den Zeitverlauf einer Zweikanal-Eskalation mit Hysterese (Kurve D).

Alle Kanäle tragen dasselbe Gewicht w = ln(TPR/FPR); die Prioritäten sind
P_j = 3 für die ersten n_H Quellen und P_j = 1 für die übrigen. Eine Teilmenge F
der aktiven Kanäle ist dadurch vollständig durch (r, h) beschrieben: r = |F| und
h = Zahl der hochprioren Kanäle in F. Die Summe über alle nichtleeren F wird
deshalb exakt als Summe über (r, h) mit Binomialzählern gebildet (O(|M|²) statt
O(2^|M|)); ``evaluate_bruteforce`` enumeriert zur Kontrolle alle Teilmengen.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

SEC_PER_DAY = 86400.0
P_HIGH = 3
P_LOW = 1
_TOL = 1e-12          # Toleranz der Schwellenvergleiche (wie in der Referenz)

PRIO_LABELS = {"hoch": P_HIGH, "niedrig": P_LOW}


def round_half_up(x: float) -> int:
    """Kaufmännisches Runden (2,5 → 3), wie ``docs/thesis/kap5_model.py``."""
    return int(math.floor(x + 0.5 + 1e-12))


# ─────────────────────────────────────────────────────────────────────────────
# Parameter und Kalibrierung
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Params:
    """Alle Eingänge des Modells (Standard = Median-Intensivstation, Tabelle 5.7)."""

    n: float = 37.5          # gezählte Alarme je Kanal und Tag (N_d = n·|M| je Bett)
    fdr: float = 0.90        # FDR_cnt: Anteil irrelevanter Alarme (gezählt)
    d_false: float = 8.0     # mittlere Dauer eines Fehlalarms [s] (= μ_art)
    d_true: float = 30.0     # mittlere Dauer eines Krisenalarms [s]
    M: int = 4               # Zahl der Kanäle |M|
    n_high: int = 2          # Zahl der Kanäle mit P_j = 3 (die übrigen P_j = 1)
    c_art: float = 0.5       # Anteil gemeinsam verursachter Artefakte
    tpr: float = 0.90        # TPR_j je Kanal
    T: float = 10.0          # Fenster / Zeithorizont [s]
    delta_min: float = 0.5   # Untergrenze des Schmelzfaktors δ(t)
    alpha: float = 0.5       # Gewicht des Spitzenterms im SDC_score
    OR: float = 1.0          # Odds Ratio des Kontextes, L = ln(OR)
    noise_direct: Optional[float] = None   # |M|·FPR direkt vorgeben (P(C) bleibt kalibriert)

    def __post_init__(self) -> None:
        if self.M < 2:
            raise ValueError("|M| muss mindestens 2 sein")
        if not 0 <= self.n_high <= self.M:
            raise ValueError("n_high muss zwischen 0 und |M| liegen")
        if not 0 < self.tpr < 1:
            raise ValueError("TPR_j muss in (0, 1) liegen")
        if not 0 <= self.fdr < 1:
            raise ValueError("FDR_cnt muss in [0, 1) liegen")
        if not 0 <= self.c_art <= 1:
            raise ValueError("c_art muss in [0, 1] liegen")
        if not 0 < self.delta_min <= 1:
            raise ValueError("δ_min muss in (0, 1] liegen")
        if not 0 <= self.alpha <= 1:
            raise ValueError("α muss in [0, 1] liegen")
        if self.OR <= 0 or self.T <= 0 or self.d_false <= 0 or self.d_true <= 0 or self.n <= 0:
            raise ValueError("OR, T, d_false, d_true und n müssen positiv sein")

    @property
    def priorities(self) -> Tuple[int, ...]:
        return (P_HIGH,) * self.n_high + (P_LOW,) * (self.M - self.n_high)

    @property
    def L(self) -> float:
        """Kontextverschiebung L = ln(OR)."""
        return math.log(self.OR)


@dataclass(frozen=True)
class Calibration:
    """Aus den Stationsgrößen zurückgerechnete Modelleingänge (5.1), (5.2)."""

    N_d: float        # Alarme je Bett und Tag = n·|M|
    PC: float         # Krisenprävalenz P(C) (Zeitanteil)
    FPR: float        # Fehlalarmbelegung je Kanal
    w: float          # Gewicht w = ln(TPR/FPR) = w̄ = ε
    MFPR: float       # aggregierte Fehlalarmbelegung |M|·FPR
    ceiling: float    # obere Grenze der ARR: 1 − c_art/|M|
    mu_art: float     # mittlere Artefaktdauer μ_art = d_false [s]

    @property
    def valid(self) -> bool:
        return 0 < self.PC < 1 and 0 < self.FPR < 1 and self.w > 0


def calibrate(p: Params) -> Calibration:
    """Kalibrierung auf die Quelle (Weg A): P(C) aus echten, FPR aus falschen Alarmen."""
    PC = p.n * (1 - p.fdr) * p.d_true / (SEC_PER_DAY * p.tpr)
    if p.noise_direct is not None:
        FPR = p.noise_direct / p.M
    else:
        FPR = p.n * p.fdr * p.d_false / (SEC_PER_DAY * (1 - PC)) if PC < 1 else float("nan")
    w = math.log(p.tpr / FPR) if 0 < FPR else float("nan")
    return Calibration(N_d=p.n * p.M, PC=PC, FPR=FPR, w=w, MFPR=p.M * FPR,
                       ceiling=1 - p.c_art / p.M, mu_art=p.d_false)


# ─────────────────────────────────────────────────────────────────────────────
# Regeln
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Rule:
    """Entscheidungsregel.

    kind = 'adaptiv': k aus SDC_score, Schmelzen bis δ_min, Kontext L.
    kind = 'basis':   k = 2 fest, Schmelzen bis δ_min, Kontext L (statische 2-aus-|M|-Basis).
    kind = 'rein':    k fest, δ_min = 1, L = 0 (reine Zählregel k-aus-|M|).
    kind = 'ohne':    kein Filter, jede Quelle meldet selbst.
    """

    kind: str
    k: int = 0

    @property
    def label(self) -> str:
        if self.kind == "rein":
            return f"rein {self.k}-aus-|M|"
        return {"adaptiv": "adaptiv", "basis": "Basis", "ohne": "ohne Filter"}[self.kind]

    @property
    def display(self) -> str:
        """Anzeigename in Oberfläche und Abbildungen (klassische Regel: k-aus-N wie in Kapitel 3)."""
        if self.kind == "rein":
            return f"rein {self.k}-aus-N"
        return self.label

    @property
    def uses_window(self) -> bool:
        return self.kind in ("adaptiv", "basis")


def effective_k(rule: Rule, M: int) -> int:
    """k der Zählregel, auf |M| begrenzt: k > |M| wäre nie erfüllbar, gemeint ist dann |M|-aus-|M|."""
    return min(rule.k, M)


ADAPTIV = Rule("adaptiv")
BASIS = Rule("basis")
OHNE = Rule("ohne")


def rein(k: int) -> Rule:
    return Rule("rein", k)


def table_rules() -> List[Rule]:
    """Regeln der Ergebnistabelle: ohne, rein 2, rein 3, Basis, adaptiv."""
    return [OHNE, rein(2), rein(3), BASIS, ADAPTIV]


# ─────────────────────────────────────────────────────────────────────────────
# Teilmengenklassen (r, h)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SubsetClass:
    """Alle Teilmengen F mit r aktiven Kanälen, davon h hochprior (count Stück)."""

    r: int
    h: int
    count: int
    sdc: float
    k: int
    sum_w: float
    theta0: float        # Θ0 = max(ε, k·w − L): Schwelle ohne Schmelzen
    theta_min: float     # Θmin = max(ε, δ_min·k·w − L): vollständig geschmolzen
    branch: str          # 'sofort' | 'verzoegert' | 'nie'
    t_esc: float         # Eskalationszeit eines anhaltenden Artefakts [s] (0 = sofort, inf = nie)
    p_crisis: float      # P(genau F aktiv | Krise)   für EINE Teilmenge
    p_noise: float       # P(genau F aktiv | keine Krise, unabhängiger Anteil)

    def share(self, mu: float) -> float:
        """Anteil der Artefakte dieser Klasse, die eskalieren (Dauer ~ Exp(μ))."""
        if self.branch == "sofort":
            return 1.0
        if self.branch == "verzoegert":
            return math.exp(-self.r * self.t_esc / mu)
        return 0.0


def _k_for(rule: Rule, M: int, sdc: float) -> int:
    if rule.kind == "rein":
        return effective_k(rule, M)
    if rule.kind == "basis":
        return 2
    return round_half_up(M - (M - 2) * sdc)


def subset_classes(p: Params, rule: Rule, cal: Optional[Calibration] = None) -> List[SubsetClass]:
    """Alle Klassen (r, h) nichtleerer Teilmengen mit Schwellen und Zweig."""
    cal = cal or calibrate(p)
    if rule.kind == "ohne" or not cal.valid:
        return []
    M, nh = p.M, p.n_high
    nl = M - nh
    P = p.priorities
    Pmax, Psum = max(P), sum(P)
    w = cal.w
    eps = w
    if rule.kind == "rein":
        dmin, L = 1.0, 0.0
    else:
        dmin, L = p.delta_min, p.L
    q = (1 - p.c_art) * cal.FPR
    out: List[SubsetClass] = []
    for r in range(1, M + 1):
        for h in range(max(0, r - nl), min(r, nh) + 1):
            count = math.comb(nh, h) * math.comb(nl, r - h)
            maxP = P_HIGH if h > 0 else P_LOW
            sumP = P_HIGH * h + P_LOW * (r - h)
            sdc = p.alpha * maxP / Pmax + (1 - p.alpha) * sumP / Psum
            k = _k_for(rule, M, sdc)
            sw = r * w
            theta0 = max(eps, k * w - L)
            theta_min = max(eps, dmin * k * w - L)
            if sw >= theta0 - _TOL:
                branch, t_esc = "sofort", 0.0
            elif sw >= theta_min - _TOL and sdc > 0:
                branch = "verzoegert"
                t_esc = p.T + p.T / sdc * math.log(k * w / (sw + L))
            else:
                branch, t_esc = "nie", float("inf")
            pC = p.tpr ** r * (1 - p.tpr) ** (M - r)
            pN = q ** r * (1 - q) ** (M - r)
            out.append(SubsetClass(r, h, count, sdc, k, sw, theta0, theta_min, branch, t_esc, pC, pN))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Stationäre Auswertung
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Result:
    rule: Rule
    params: Params
    cal: Calibration
    TPR_sys: float
    coinc: float
    delayed: float
    corr: float
    noise_f: float
    noise_r: float
    ARR: float
    PPV_zeit: float
    false_per_day: float
    crises_per_day: float
    reports_per_day: float
    PPV_cnt: float

    def metric(self, name: str) -> float:
        return METRICS[name](self)


METRICS = {
    "Fehlalarme/Tag": lambda r: r.false_per_day,
    "ARR": lambda r: r.ARR,
    "System-TPR": lambda r: r.TPR_sys,
    "PPV gezählt": lambda r: r.PPV_cnt,
    "PPV zeitgewichtet": lambda r: r.PPV_zeit,
}


def _nan_result(p: Params, rule: Rule, cal: Calibration) -> Result:
    nan = float("nan")
    return Result(rule, p, cal, nan, nan, nan, nan, nan, nan, nan, nan, nan, nan, nan, nan)


def evaluate(p: Params, rule: Rule = ADAPTIV, cal: Optional[Calibration] = None) -> Result:
    """Stationäre Kenngrößen einer Regel (Belegungen, ARR, PPV, Stückzahlen je Tag)."""
    cal = cal or calibrate(p)
    if not cal.valid:
        return _nan_result(p, rule, cal)
    PC, FPR, M = cal.PC, cal.FPR, p.M
    noise_r = (1 - PC) * M * FPR
    if rule.kind == "ohne":
        TPR_sys = 1 - (1 - p.tpr) ** M
        coinc = delayed = float("nan")
        corr = float("nan")
        noise_f = noise_r
        signal = PC * M * p.tpr
        PPV_zeit = signal / (signal + noise_f)
        reports_factor = M * p.tpr
    else:
        sig = coinc = delayed = 0.0
        for c in subset_classes(p, rule, cal):
            if c.branch == "sofort":
                sig += c.count * c.p_crisis
                coinc += c.count * c.p_noise
            elif c.branch == "verzoegert":
                sig += c.count * c.p_crisis
                delayed += c.count * c.p_noise * c.share(cal.mu_art)
        TPR_sys = sig
        corr = p.c_art * FPR
        noise_f = (1 - PC) * (coinc + delayed + corr)
        signal = PC * sig
        PPV_zeit = signal / (signal + noise_f) if signal + noise_f > 0 else float("nan")
        reports_factor = TPR_sys
    ARR = 1 - noise_f / noise_r
    false_per_day = noise_f * SEC_PER_DAY / p.d_false
    crises_per_day = PC * SEC_PER_DAY / p.d_true
    reports = crises_per_day * reports_factor
    PPV_cnt = reports / (reports + false_per_day) if reports + false_per_day > 0 else float("nan")
    return Result(rule, p, cal, TPR_sys, coinc, delayed, corr, noise_f, noise_r, ARR, PPV_zeit,
                  false_per_day, crises_per_day, reports, PPV_cnt)


def evaluate_table(p: Params) -> List[Result]:
    cal = calibrate(p)
    return [evaluate(p, r, cal) for r in table_rules()]


def evaluate_bruteforce(p: Params, rule: Rule = ADAPTIV, mu: Optional[float] = None) -> Dict[str, float]:
    """Kontrollrechnung: Enumeration aller Teilmengen wie in ``kap5_model.py``.

    Liefert dieselben Belegungen wie ``evaluate`` (TPR_sys, coinc, delayed, noise_f, ARR).
    """
    cal = calibrate(p)
    M, P = p.M, p.priorities
    w = [cal.w] * M
    wbar = sum(w) / M
    eps = min(w)
    Pmax, Psum = max(P), sum(P)
    if rule.kind == "rein":
        dmin, L = 1.0, 0.0
    else:
        dmin, L = p.delta_min, p.L
    mu = cal.mu_art if mu is None else mu
    q = (1 - p.c_art) * cal.FPR
    sig = coinc = delayed = 0.0
    for r in range(1, M + 1):
        for F in itertools.combinations(range(M), r):
            sw = sum(w[j] for j in F)
            sdc = p.alpha * max(P[j] for j in F) / Pmax + (1 - p.alpha) * sum(P[j] for j in F) / Psum
            k = _k_for(rule, M, sdc)
            theta0 = max(eps, k * wbar - L)
            theta_min = max(eps, dmin * k * wbar - L)
            pC = math.prod(p.tpr if j in F else 1 - p.tpr for j in range(M))
            pN = math.prod(q if j in F else 1 - q for j in range(M))
            if sw >= theta_min - _TOL:
                sig += pC
            if sw >= theta0 - _TOL:
                coinc += pN
            elif sw >= theta_min - _TOL and sdc > 0:
                tesc = p.T + p.T / sdc * math.log(k * wbar / (sw + L))
                delayed += pN * math.exp(-r * tesc / mu)
    noise_f = (1 - cal.PC) * (coinc + delayed + p.c_art * cal.FPR)
    noise_r = (1 - cal.PC) * M * cal.FPR
    return dict(TPR_sys=sig, coinc=coinc, delayed=delayed, noise_f=noise_f, noise_r=noise_r,
                ARR=1 - noise_f / noise_r)


# ─────────────────────────────────────────────────────────────────────────────
# Kurve C: Anteil eskalierter Doppel-Artefakte
# ─────────────────────────────────────────────────────────────────────────────

PAIR_KINDS = {"hoch+hoch": 2, "hoch+niedrig": 1, "niedrig+niedrig": 0}


def pair_class(p: Params, rule: Rule, h: int, cal: Optional[Calibration] = None) -> Optional[SubsetClass]:
    """Klasse der Zweierteilmengen mit h hochprioren Kanälen (None, falls es sie nicht gibt)."""
    for c in subset_classes(p, rule, cal):
        if c.r == 2 and c.h == h:
            return c
    return None


def pair_share(p: Params, rule: Rule, h: int, mu: float, cal: Optional[Calibration] = None) -> float:
    """Anteil eskalierender Doppel-Artefakte der Dauer ~ Exp(μ) (Kurve C)."""
    if rule.kind == "rein":
        return 1.0 if effective_k(rule, p.M) <= 2 else 0.0
    c = pair_class(p, rule, h, cal)
    return float("nan") if c is None else c.share(mu)


# ─────────────────────────────────────────────────────────────────────────────
# Kurve D: Zeitverlauf einer Zweikanal-Eskalation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Trace:
    t: np.ndarray
    E: np.ndarray
    theta_target: np.ndarray
    theta_current: np.ndarray
    sdc: np.ndarray
    k: np.ndarray
    delta: np.ndarray
    escalated: np.ndarray
    t0: float
    latency: float          # Eskalationslatenz nach t0 [s] (inf = keine Eskalation)
    t_escalation: float     # absolute Eskalationszeit [s] (inf = keine)


def _hysteresis(theta_target: np.ndarray, rho: np.ndarray, dt: float) -> np.ndarray:
    """Θ_current nach (3.22): Fast Attack / Slow Release mit ρ = (1 − SDC)/T."""
    if np.all(np.diff(theta_target) <= 1e-12):
        return theta_target.copy()          # nur Absenkungen → sofort übernommen
    out = np.empty_like(theta_target)
    prev = theta_target[0]
    out[0] = prev
    tt = theta_target.tolist()
    rr = rho.tolist()
    for i in range(1, len(tt)):
        tgt = tt[i]
        if tgt <= prev:
            cur = tgt
        else:
            cur = tgt + (prev - tgt) * math.exp(-rr[i] * dt)
        out[i] = cur
        prev = cur
    return out


def simulate_pair(p: Params, prio_a: str, prio_b: str, t0: float, rule: Rule = ADAPTIV,
                  dt: float = 0.01, cal: Optional[Calibration] = None,
                  t_end: Optional[float] = None) -> Optional[Trace]:
    """Kanal A alarmiert ab 0 s, Kanal B ab t0; beide bleiben aktiv.

    Belegung s_j nach (3.6) steigt linear über T; E = w·(s_A + s_B); SDC_score und k
    aus den gerade aktiven Kanälen; δ(t) = max(δ_min, exp(−SDC/T·max(0, Δt − T))) mit
    Δt ab dem ersten Alarm; Θ_target = max(ε, k·w·δ − L); Θ_current mit Hysterese (3.22).
    Für ``rein k`` gilt die reine Zählregel ohne Fenster (Latenz 0, falls k ≤ 2).
    Liefert None, wenn das Ensemble die beiden Prioritäten nicht enthält.
    """
    cal = cal or calibrate(p)
    if not cal.valid:
        return None
    pa, pb = PRIO_LABELS[prio_a], PRIO_LABELS[prio_b]
    need_high = (pa == P_HIGH) + (pb == P_HIGH)
    need_low = 2 - need_high
    if need_high > p.n_high or need_low > p.M - p.n_high:
        return None
    P = p.priorities
    Pmax, Psum = max(P), sum(P)
    w, T = cal.w, p.T
    if rule.kind == "rein":
        dmin, L = 1.0, 0.0
    else:
        dmin, L = p.delta_min, p.L

    sdc1 = p.alpha * pa / Pmax + (1 - p.alpha) * pa / Psum
    sdc2 = p.alpha * max(pa, pb) / Pmax + (1 - p.alpha) * (pa + pb) / Psum
    k1, k2 = _k_for(rule, p.M, sdc1), _k_for(rule, p.M, sdc2)

    if t_end is None:
        # Nach max(t0 + T, t_floor) ändern sich E und Θ_target nicht mehr; Reserve für die Hysterese.
        t_floor = T + (T / sdc2) * math.log(1 / dmin) if (dmin < 1 and sdc2 > 0) else T
        t_end = max(t0 + T, t_floor) + 3 * T
    n = int(round(t_end / dt)) + 1
    t = np.arange(n) * dt
    sA = np.clip(t / T, 0.0, 1.0)
    sB = np.clip((t - t0) / T, 0.0, 1.0)
    E = w * (sA + sB)
    after = t >= t0 - 1e-9
    sdc = np.where(after, sdc2, sdc1)
    k = np.where(after, k2, k1).astype(float)
    delta = np.maximum(dmin, np.exp(-(sdc / T) * np.maximum(0.0, t - T)))
    theta_target = np.maximum(w, k * w * delta - L)
    rho = (1 - sdc) / T
    theta_current = _hysteresis(theta_target, rho, dt)
    if rule.kind == "rein":
        # reine Zählregel: eskaliert, sobald k Kanäle aktiv sind (ohne Fenster)
        active = 1 + after.astype(int)
        escalated = active >= rule.k
    else:
        escalated = E >= theta_current - 1e-9
    hits = np.flatnonzero(escalated & after)
    if len(hits):
        t_esc = float(t[hits[0]])
        latency = t_esc - t0
    else:
        t_esc = latency = float("inf")
    return Trace(t, E, theta_target, theta_current, sdc, k, delta, escalated, t0, latency, t_esc)


def escalation_latency(p: Params, prio_a: str, prio_b: str, t0: float, rule: Rule = ADAPTIV,
                       dt: float = 0.01, cal: Optional[Calibration] = None) -> float:
    """Eskalationslatenz nach t0 [s]; nan, falls die Paarung im Ensemble fehlt; inf, falls nie."""
    if rule.kind == "rein":
        return 0.0 if effective_k(rule, p.M) <= 2 else float("inf")
    tr = simulate_pair(p, prio_a, prio_b, t0, rule, dt, cal)
    return float("nan") if tr is None else tr.latency


PAIR_SEQUENCES = [("hoch", "hoch"), ("hoch", "niedrig"), ("niedrig", "hoch"), ("niedrig", "niedrig")]


# ─────────────────────────────────────────────────────────────────────────────
# Parameterentfaltung, Szenarien, Tornado
# ─────────────────────────────────────────────────────────────────────────────

# Anzeigename, Einheit, Bereich (min, max), Standardwert, Schritt
PARAM_INFO: Dict[str, dict] = {
    "n":         dict(label="n (Alarme je Kanal und Tag)", unit="", lo=5.0, hi=300.0, step=0.5, default=37.5),
    "fdr":       dict(label="FDR_cnt", unit="", lo=0.50, hi=0.99, step=0.01, default=0.90),
    "d_false":   dict(label="d_false", unit="s", lo=2.0, hi=60.0, step=1.0, default=8.0),
    "d_true":    dict(label="d_true", unit="s", lo=5.0, hi=120.0, step=1.0, default=30.0),
    "M":         dict(label="|M|", unit="", lo=2, hi=12, step=1, default=4),
    "c_art":     dict(label="c_art", unit="", lo=0.0, hi=1.0, step=0.01, default=0.5),
    "tpr":       dict(label="TPR_j", unit="", lo=0.50, hi=0.99, step=0.01, default=0.90),
    "T":         dict(label="T", unit="s", lo=2.0, hi=30.0, step=1.0, default=10.0),
    "delta_min": dict(label="δ_min", unit="", lo=0.10, hi=1.00, step=0.01, default=0.5),
    "alpha":     dict(label="α", unit="", lo=0.0, hi=1.0, step=0.05, default=0.5),
    "OR":        dict(label="OR", unit="", lo=1.0, hi=20.0, step=0.5, default=1.0),
    "MFPR":      dict(label="|M|·FPR (Rauschen direkt)", unit="", lo=0.005, hi=1.2, step=0.005, default=0.0125),
}

FAMILY_VALUES: Dict[str, Sequence[float]] = {
    "c_art": (0.0, 0.25, 0.5, 0.75, 1.0),
    "d_true": (10.0, 30.0, 60.0, 120.0),
    "tpr": (0.7, 0.8, 0.9, 0.97),
    "delta_min": (0.3, 0.5, 0.7, 1.0),
}


def set_param(p: Params, name: str, value: float) -> Params:
    """Parameter setzen; |M| zieht n_H = |M|//2 nach, 'MFPR' setzt das Rauschen direkt."""
    if name == "M":
        M = int(round(value))
        return replace(p, M=M, n_high=M // 2)
    if name == "MFPR":
        return replace(p, noise_direct=float(value))
    return replace(p, **{name: value})


def current_value(p: Params, name: str) -> float:
    if name == "MFPR":
        return calibrate(p).MFPR
    return getattr(p, name)


def sweep(p: Params, name: str, values: Sequence[float], rule: Rule, metric: str) -> np.ndarray:
    """Kennzahl ``metric`` über ``values`` des Parameters ``name`` für eine Regel."""
    return np.array([evaluate(set_param(p, name, v), rule).metric(metric) for v in values], dtype=float)


SCENARIOS: Dict[str, dict] = {
    "Median":                dict(n=37.5, d_false=8.0, d_true=30.0, M=4, n_high=2),
    "geringe Last":          dict(n=25.0, d_false=8.0, d_true=30.0, M=4, n_high=2),
    "hohe Last":             dict(n=100.0, d_false=8.0, d_true=30.0, M=4, n_high=2),
    "sehr hohe Last":        dict(n=273.0, d_false=8.0, d_true=30.0, M=4, n_high=2),
    "lange Dauern":          dict(n=37.5, d_false=30.0, d_true=60.0, M=4, n_high=2),
    "stark instrumentiert":  dict(n=33.3, d_false=8.0, d_true=30.0, M=12, n_high=6),
    "Grenzfall":             dict(n=273.0, d_false=30.0, d_true=60.0, M=4, n_high=2),
}


@dataclass(frozen=True)
class TornadoRow:
    name: str
    low_label: str
    high_label: str
    low: Result
    high: Result


def tornado(p: Params, rule: Rule = ADAPTIV) -> Tuple[Result, List[TornadoRow]]:
    """Einzelvariation jedes Parameters um den Arbeitspunkt (Kurve H)."""
    spec = [
        ("n",         "×0,5", "×1,5", replace(p, n=p.n * 0.5), replace(p, n=p.n * 1.5)),
        ("FDR_cnt",   "0,80", "0,95", replace(p, fdr=0.80), replace(p, fdr=0.95)),
        ("d_false",   "4 s", "16 s", replace(p, d_false=4.0), replace(p, d_false=16.0)),
        ("d_true",    "15 s", "60 s", replace(p, d_true=15.0), replace(p, d_true=60.0)),
        ("TPR_j",     "0,80", "0,97", replace(p, tpr=0.80), replace(p, tpr=0.97)),
        ("c_art",     "0,25", "0,75", replace(p, c_art=0.25), replace(p, c_art=0.75)),
        ("|M|",       "3", "6", set_param(p, "M", 3), set_param(p, "M", 6)),
        ("δ_min",     "0,3", "0,7", replace(p, delta_min=0.3), replace(p, delta_min=0.7)),
        ("α",         "0,25", "0,75", replace(p, alpha=0.25), replace(p, alpha=0.75)),
        ("T",         "5 s", "20 s", replace(p, T=5.0), replace(p, T=20.0)),
    ]
    base = evaluate(p, rule)
    rows = [TornadoRow(name, lo_l, hi_l, evaluate(lo, rule), evaluate(hi, rule))
            for name, lo_l, hi_l, lo, hi in spec]
    return base, rows


# ─────────────────────────────────────────────────────────────────────────────
# Erweiterungen für die Gliederung 5.3.2 / 5.3.3: Fenstereffekt (C2), Prioritäten,
# Gerätezahl (G2), Vergleichstabelle, Stückzahlen je Station
# ─────────────────────────────────────────────────────────────────────────────

def t_need(c: SubsetClass, p: Params, rule: Rule, tau: float = 0.0) -> float:
    """Zeit, die eine Episode mit r gleichzeitig beginnenden Kanälen bis zur Eskalation braucht.

    adaptiv/Basis: Zweig 'sofort' → T·Θ0/Σw (= T·k/r bei L = 0, lineare Belegung nach (3.6));
    Zweig 'verzoegert' → t_esc; 'nie' → ∞.  Zählregel k-aus-N ohne Fenster: τ, falls r ≥ k, sonst ∞.
    """
    if rule.kind == "rein":
        return tau if c.r >= effective_k(rule, p.M) else float("inf")
    if c.branch == "sofort":
        return p.T * c.theta0 / c.sum_w
    if c.branch == "verzoegert":
        return c.t_esc
    return float("inf")


def share_after(t: float, mean: float, n: int = 1) -> float:
    """Anteil der Episoden, die den Zeitpunkt t erreichen: n unabhängige Exp(mean)-Dauern > t."""
    if not math.isfinite(t):
        return 0.0
    return math.exp(-n * t / mean)


def find_class(classes: Sequence[SubsetClass], r: int, h: int) -> Optional[SubsetClass]:
    for c in classes:
        if c.r == r and c.h == h:
            return c
    return None


def window_share(p: Params, rule: Rule, r: int, h: int, mean: float, common: bool,
                 tau: float = 0.0, cal: Optional[Calibration] = None) -> float:
    """Anteil eskalierter Episoden (r Kanäle, h hoch) mit Fenster: Dauer(n) ~ Exp(mean).

    common=True: eine gemeinsame Dauer (Stoß, Krise); common=False: r unabhängige Dauern.
    nan, falls die Kombination im Ensemble nicht vorkommt.
    """
    c = find_class(subset_classes(p, rule, cal), r, h)
    if c is None:
        return float("nan")
    return share_after(t_need(c, p, rule, tau), mean, 1 if common else r)


def stoss_share(p: Params, rule: Rule, mu: float, tau: float = 0.0, cal: Optional[Calibration] = None) -> float:
    """Gemeinsames Artefakt (Stoß): alle |M| Kanäle, eine Dauer ~ Exp(μ)."""
    return window_share(p, rule, p.M, p.n_high, mu, True, tau, cal)


def pair_window_share(p: Params, rule: Rule, h: int, mu: float, tau: float = 0.0,
                      cal: Optional[Calibration] = None) -> float:
    """Unabhängiges Paar (h hochpriore Kanäle), zwei Dauern ~ Exp(μ), gleichzeitiger Beginn."""
    return window_share(p, rule, 2, h, mu, False, tau, cal)


def crisis_share(p: Params, rule: Rule, r: int, h: int, d_true: Optional[float] = None, tau: float = 0.0,
                 cal: Optional[Calibration] = None) -> float:
    """Krisenepisode mit r Kanälen (h hoch), eine Dauer ~ Exp(d_true): Anteil der eskalierten Episoden."""
    return window_share(p, rule, r, h, p.d_true if d_true is None else d_true, True, tau, cal)


def knn_share(tau: float, mean: float, n: int = 1) -> float:
    """Zählregel k-aus-N mit Verzögerung τ: exp(−n·τ/mean)."""
    return share_after(tau, mean, n)


def missed_per_1000(res: Result) -> float:
    """Verpasste Krisen je 1000 = 1000·(1 − System-TPR)."""
    return 1000.0 * (1.0 - res.TPR_sys)


def typical_latency(p: Params, rule: Rule, tau: float = 0.0, cal: Optional[Calibration] = None) -> float:
    """Typische Eskalationslatenz: krisengewichtetes Mittel von t_need über die eskalierenden Teilmengen.

    Zählregel ohne Fenster: τ. ohne Filter: 0.
    """
    if rule.kind == "ohne":
        return 0.0
    num = den = 0.0
    for c in subset_classes(p, rule, cal):
        t = t_need(c, p, rule, tau)
        if math.isfinite(t):
            wgt = c.count * c.p_crisis
            num += wgt * t
            den += wgt
    return num / den if den > 0 else float("inf")


@dataclass(frozen=True)
class Counts:
    """Stückzahlen je Tag, auf ``beds`` Betten hochgerechnet."""

    beds: int
    false_per_day: float
    reports_per_day: float
    crises_per_day: float


def scaled_counts(res: Result, beds: int = 1) -> Counts:
    return Counts(beds, res.false_per_day * beds, res.reports_per_day * beds, res.crises_per_day * beds)


def class_label(c: SubsetClass, M: int) -> str:
    """Lesbare Bezeichnung einer Klasse (r, h): '1 × hoch', 'hoch + niedrig', 'alle 4', …"""
    if c.r == M:
        return f"alle {M}"
    if c.r == 1:
        return "1 × hoch" if c.h == 1 else "1 × niedrig"
    if c.r <= 3:
        return " + ".join(["hoch"] * c.h + ["niedrig"] * (c.r - c.h))
    parts = []
    if c.h:
        parts.append(f"{c.h} × hoch")
    if c.r - c.h:
        parts.append(f"{c.r - c.h} × niedrig")
    return " + ".join(parts)


BRANCH_TEXT = {"sofort": "sofort", "verzoegert": "nach Schmelzen", "nie": "nie"}


def combination_rows(p: Params, cal: Optional[Calibration] = None, ks: Sequence[int] = (2, 3)) -> List[dict]:
    """Alle Kombinationen aktiver Quellen (adaptiv) mit Schwellen, Ausgang und dem Ausgang der Zählregeln."""
    cal = cal or calibrate(p)
    rows = []
    for c in subset_classes(p, ADAPTIV, cal):
        row = dict(label=class_label(c, p.M), r=c.r, h=c.h, count=c.count, sdc=c.sdc, k=c.k, theta0=c.theta0,
                   theta_min=c.theta_min, sum_w=c.sum_w, branch=BRANCH_TEXT[c.branch], t_esc=c.t_esc,
                   t_need=t_need(c, p, ADAPTIV))
        for k in ks:
            row[f"rein {k}-aus-N"] = "sofort" if c.r >= min(k, p.M) else "nie"
        rows.append(row)
    return rows


def priority_bars(p: Params, mu: Optional[float] = None, cal: Optional[Calibration] = None,
                  max_r: int = 3) -> List[dict]:
    """Daten der Prioritätsabbildung: Kombinationen mit r ≤ max_r und 'alle |M|'.

    Eskalationszeit einer anhaltenden Episode (adaptiv: t_need; rein 2-aus-N: 0 ab r ≥ 2) und Anteil
    eskalierter Episoden bei Dauer ~ Exp(μ): r unabhängige Dauern, bei 'alle |M|' eine gemeinsame (Stoß).
    """
    cal = cal or calibrate(p)
    mu = cal.mu_art if mu is None else mu
    rows = []
    for c in subset_classes(p, ADAPTIV, cal):
        if c.r > max_r and c.r != p.M:
            continue
        t_ad = t_need(c, p, ADAPTIV)
        t_knn = 0.0 if c.r >= min(2, p.M) else float("inf")
        n_dur = 1 if c.r == p.M else c.r
        rows.append(dict(label=class_label(c, p.M), r=c.r, h=c.h, k=c.k, branch=BRANCH_TEXT[c.branch],
                         t_adaptiv=t_ad, t_knn2=t_knn,
                         share_adaptiv=share_after(t_ad, mu, n_dur), share_knn2=share_after(t_knn, mu, n_dur)))
    return rows


G2_RULES = (ADAPTIV, rein(2), rein(3))


def geraetezahl(p: Params, Ms: Sequence[int] = tuple(range(2, 13)),
                rules: Sequence[Rule] = G2_RULES) -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    """Fehlalarme/Tag und verpasste Krisen je 1000 über |M| (n_H = |M| div 2) je Regel (Anzeigename)."""
    out = {}
    for rule in rules:
        res = [evaluate(set_param(p, "M", M), rule) for M in Ms]
        out[rule.display] = (np.array([r.false_per_day for r in res]), np.array([missed_per_1000(r) for r in res]))
    return out


def comparison_rows(p: Params, tau: float = 0.0, cal: Optional[Calibration] = None,
                    beds: int = 1) -> List[dict]:
    """Vergleichstabelle 5.3.3: adaptiv, rein 2-aus-N, rein 3-aus-N, Basis."""
    cal = cal or calibrate(p)
    base = evaluate(p, ADAPTIV, cal)
    rows = []
    for rule in (ADAPTIV, rein(2), rein(3), BASIS):
        r = evaluate(p, rule, cal)
        rows.append(dict(rule=rule.display, false_per_day=r.false_per_day * beds,
                         delta=(r.false_per_day - base.false_per_day) * beds, TPR_sys=r.TPR_sys,
                         missed=missed_per_1000(r), latency=typical_latency(p, rule, tau, cal)))
    return rows


def kmin_over_alpha(p: Params, alphas: Sequence[float], max_r: int = 3) -> Dict[str, np.ndarray]:
    """k_min je Kombination (r ≤ max_r und 'alle |M|') als Funktion von α (3.16)/(3.17)."""
    out: Dict[str, np.ndarray] = {}
    base = subset_classes(p, ADAPTIV)
    for c in base:
        if c.r > max_r and c.r != p.M:
            continue
        ks = []
        for a in alphas:
            cls = find_class(subset_classes(replace(p, alpha=float(a)), ADAPTIV), c.r, c.h)
            ks.append(cls.k if cls else np.nan)
        out[class_label(c, p.M)] = np.array(ks, dtype=float)
    return out
