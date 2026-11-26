"""Ziel 3 – Experimente: Messwerte und Ergebnisse der drei Simulationsläufe lesen und für E1–E4 aufbereiten.

Ohne Streamlit.  Die Dateien der Auswertung von PC 2 liegen in ``docs/reports/``:
``messwerte_<lauf>.json`` (gemessene Modelleingänge: FPR_j, P(C), d_false, d_true, c_art, Krisen) und
``results_<lauf>.json`` (Regeln adaptiv / 2-aus-4 / 3-aus-4 / 1-aus-4 mit Fehleskalationen je Bett und
Stunde samt 95-%-Intervall nach Poisson und erkannten Krisen samt 95-%-Intervall nach Clopper–Pearson).

Läufe: ``ziel3_1h`` (1 h, Seed 1, η_j = 0,9), ``ziel3_12h`` (12 h, Seed 2, η_j = 0,9),
``ziel3_6h_p06`` (6 h, Seed 3, η_j = 0,6); 20 Patienten × 4 Quellen, P = (3, 3, 2, 1).
η_j = Anteil der Krisen, in denen ein Gerät überhaupt alarmiert (Szenario); der gemessene TPR_j in den
Messwerten ist dagegen der Anteil der Krisenzeit, in der das Gerät alarmiert.

Das Modell wird je Lauf mit dessen Messwerten gerechnet (``model_params``): |M|·FPR̄ = Σ FPR_j direkt,
P(C) direkt (``pc_direct``), d_false und d_true gemessen, d0 = 15 s, TPR_j = η_j aus dem Szenario
(im Modell alarmiert ein Gerät, das eine Krise anzeigt, bis zu ihrem Ende), T = 10 s, δ_min = 0,5, α = 0,5.
c_art ist zweifach gemessen (Anteil der
Fehlalarmzeit in Episoden mit allen vier bzw. mit ≥ 2 Quellen); das Modell wird als Band zwischen beiden gezeigt.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from statistics import NormalDist
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

try:  # als Paketmodul (Tests, python -m) …
    from . import model_ziel3 as mz
except ImportError:  # … oder als Skript / unter streamlit run
    import model_ziel3 as mz

DATA_DIR = Path(__file__).resolve().parents[2] / "docs" / "reports"

BEDS = 10                    # Betten je Station
WEEK_H = 168.0               # Stunden je Woche
WEEK_BED_DAYS = 70.0         # Bett-Tage je Station und Woche
P_SIM: Tuple[int, ...] = (3, 3, 2, 1)       # ECG hoch, Vent hoch, SpO2 mittel, ABP niedrig
D0_SIM, T_SIM, DMIN_SIM, ALPHA_SIM = 15.0, 10.0, 0.5, 0.5
SOURCES = ("ECG", "Vent", "SpO2", "ABP")

SIM_RULE_KEYS: Dict[str, str] = {"adaptiv": "adaptiv", "r2": "2-aus-4", "r3": "3-aus-4", "r1": "1-aus-4"}
SIM_RULES: Tuple[str, ...] = ("r2", "adaptiv", "r3")       # Reihenfolge wie K4
MODES: Dict[str, str] = {
    "streng": "streng (Rot beginnt vor Krisenende)",
    "mild": "mild (Rot schneidet [Beginn, Ende + 10 s])",
}
DEFAULT_MODE = "streng"


ETA = "η_j"                  # Anzeigewahrscheinlichkeit je Gerät (Streamlit-Text)
ETA_TEX = r"$\eta_j$"        # dieselbe Größe in matplotlib (mathtext)


KIND_TEXT: Dict[str, str] = {
    "standard": "Standard",
    "lang": "lange Krisen (30 s + Exp(30 s))",
    "ideal": "Idealfall (Alarmebene)",
}
KIND_SHORT: Dict[str, str] = {"standard": "", "lang": "lange Krisen", "ideal": "Idealfall"}


@dataclass(frozen=True)
class RunSpec:
    key: str
    name: str          # Dateiname-Stamm
    label: str         # kurz: „12 h“
    seed: int          # Seed des Szenarios (run_config.json der PC-2-Journale)
    tpr: float         # η_j des Szenarios: Anteil der Krisen, in denen ein Gerät alarmiert (= TPR_j im Modell)
    marker: str
    filled: bool
    kind: str = "standard"      # standard | lang (lange Krisen) | ideal (Idealfall, Alarmebene)
    on_line: bool = True        # D3: Punkt gehört zur Linie über η (6 h, A, B, 12 h)

    def _eta(self, sym: str) -> str:
        extra = KIND_SHORT.get(self.kind, "")
        return f"{self.label} ({sym} = {str(self.tpr).replace('.', ',')}{', ' + extra if extra else ''})"

    @property
    def label_eta(self) -> str:
        """„6 h (η_j = 0,6)“ für Tabellen und Texte."""
        return self._eta(ETA)

    @property
    def label_tex(self) -> str:
        """„6 h ($\\eta_j$ = 0,6)“ für Abbildungen."""
        return self._eta(ETA_TEX)


RUNS: Dict[str, RunSpec] = {
    "1h": RunSpec("1h", "ziel3_1h", "1 h", 1, 0.9, "o", False),
    "6h": RunSpec("6h", "ziel3_6h_p06", "6 h", 3, 0.6, "^", True),
    "A": RunSpec("A", "ziel3_4h_p07", "A 4 h", 4, 0.7, "v", True),
    "B": RunSpec("B", "ziel3_4h_p08", "B 4 h", 5, 0.8, "P", True),
    "12h": RunSpec("12h", "ziel3_12h", "12 h", 2, 0.9, "o", True),
    "C": RunSpec("C", "ziel3_4h_lang", "C 4 h", 6, 0.9, "D", True, kind="lang", on_line=False),
    "D": RunSpec("D", "ziel3_6h_ideal", "D 6 h", 7, 0.9, "s", True, kind="ideal", on_line=False),
}
RUN_ORDER: Tuple[str, ...] = ("1h", "6h", "A", "B", "12h", "C", "D")
ORIGINAL_RUNS: Tuple[str, ...] = ("1h", "6h", "12h")          # die drei ersten Läufe (Reiter Vergleich: Standardauswahl)
D3_LINE_RUNS: Tuple[str, ...] = ("6h", "A", "B", "12h")       # D3: Linie über η 0,6 · 0,7 · 0,8 · 0,9 je Regel
MAIN_RUN = "12h"
E4_RUNS: Tuple[str, ...] = ("12h", "6h")


# ─────────────────────────────────────────────────────────────────────────────
# Statistik
# ─────────────────────────────────────────────────────────────────────────────

def chi2_ppf(p: float, df: float) -> float:
    """Quantil der χ²-Verteilung nach Wilson–Hilferty (für df ≥ 30 auf < 0,1 % genau)."""
    z = NormalDist().inv_cdf(p)
    a = 2.0 / (9.0 * df)
    return df * (1.0 - a + z * math.sqrt(a)) ** 3


def poisson_ci(n: int, exposure: float, alpha: float = 0.05) -> Tuple[float, float]:
    """Exaktes 95-%-Intervall (Garwood) der Rate n/exposure."""
    lo = 0.5 * chi2_ppf(alpha / 2, 2 * n) if n > 0 else 0.0
    hi = 0.5 * chi2_ppf(1 - alpha / 2, 2 * n + 2)
    return lo / exposure, hi / exposure


# ─────────────────────────────────────────────────────────────────────────────
# Daten
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Messwerte:
    hours: float
    patients: int
    exposure_bed_h: float
    FPR: Dict[str, float]           # je Quelle
    P_C: float
    d_false: float
    d_true: float
    n_false_alarms: int
    n_crises: int
    crises_per_bed_day: float
    c_art_a: float                  # Zeitanteil in Episoden mit allen 4 Quellen
    c_art_b: float                  # Zeitanteil in Episoden mit ≥ 2 Quellen
    TPR: Dict[str, float] = field(default_factory=dict)   # gemessen je Quelle: Anteil der Krisenzeit im Alarm

    @property
    def tpr_mean(self) -> float:
        """TPR_j gemessen, Mittel der vier Quellen."""
        return sum(self.TPR.values()) / len(self.TPR) if self.TPR else float("nan")

    @property
    def tpr_range(self) -> Tuple[float, float]:
        return (min(self.TPR.values()), max(self.TPR.values())) if self.TPR else (float("nan"), float("nan"))

    @property
    def mfpr(self) -> float:
        """|M|·FPR = Σ FPR_j (Rauschen direkt)."""
        return sum(self.FPR.values())

    @property
    def fpr_mean(self) -> float:
        return self.mfpr / len(self.FPR)

    @property
    def false_alarms_per_bed_h(self) -> float:
        """Fehlalarme der Geräte je Bett und Stunde (= ohne Filter)."""
        return self.n_false_alarms / self.exposure_bed_h

    @property
    def false_alarms_ci(self) -> Tuple[float, float]:
        return poisson_ci(self.n_false_alarms, self.exposure_bed_h)


@dataclass(frozen=True)
class RuleResult:
    key: str                        # Schlüssel in results_*.json
    n_false: int
    false_per_bed_h: float
    false_ci: Tuple[float, float]
    n_crises: int
    caught_mild: int
    caught_strict: int
    caught_mild_ci: Optional[Tuple[float, float]]
    caught_strict_ci: Optional[Tuple[float, float]]
    n_escalations: int = 0          # Eskalationen gesamt im Fenster (n_escalations_window)
    caught_after_end: int = 0       # Rot erst nach Krisenende (= caught − caught_strict)

    @property
    def missed(self) -> int:
        """verpasst (auch mild): Krisen ohne Rot bis Ende + 10 s."""
        return self.n_crises - self.caught_mild

    def missed_n(self, mode: str = DEFAULT_MODE) -> int:
        return self.n_crises - self.caught(mode)

    def caught(self, mode: str = DEFAULT_MODE) -> int:
        return self.caught_strict if mode == "streng" else self.caught_mild

    def caught_frac(self, mode: str = DEFAULT_MODE) -> float:
        return self.caught(mode) / self.n_crises

    def caught_ci(self, mode: str = DEFAULT_MODE) -> Tuple[float, float]:
        ci = self.caught_strict_ci if mode == "streng" else self.caught_mild_ci
        return ci if ci is not None else (self.caught_frac(mode), self.caught_frac(mode))

    def missed_frac(self, mode: str = DEFAULT_MODE) -> float:
        return 1.0 - self.caught_frac(mode)

    def missed_ci(self, mode: str = DEFAULT_MODE) -> Tuple[float, float]:
        lo, hi = self.caught_ci(mode)
        return 1.0 - hi, 1.0 - lo


@dataclass(frozen=True)
class RunData:
    spec: RunSpec
    m: Messwerte
    rules: Dict[str, RuleResult]    # Schlüssel: Modellregel ("adaptiv", "r2", "r3", "r1")
    notes: Tuple[str, ...]
    n_alarms: int = 0               # Gerätealarme im Fenster (Summe der Stundenwerte; results.hourly)

    @property
    def key(self) -> str:
        return self.spec.key

    @property
    def label(self) -> str:
        return self.spec.label

    @property
    def n_false_alarms(self) -> int:
        """Gerätealarme, davon falsch (messwerte.n_false_alarms)."""
        return self.m.n_false_alarms


def _ci(v) -> Optional[Tuple[float, float]]:
    return (float(v[0]), float(v[1])) if v is not None else None


def load_messwerte(path: Path) -> Messwerte:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return Messwerte(
        hours=float(d["hours"]), patients=int(d["patients"]), exposure_bed_h=float(d["exposure_bed_h"]),
        FPR={k: float(v) for k, v in d["FPR"].items()}, P_C=float(d["P_C"]),
        d_false=float(d["d_false_s"]), d_true=float(d["d_true_s"]),
        n_false_alarms=int(d["n_false_alarms"]), n_crises=int(d["n_crises"]),
        crises_per_bed_day=float(d["crises_per_bed_day"]),
        c_art_a=float(d["c_art_a_all4_time_share"]), c_art_b=float(d["c_art_b_ge2_time_share"]),
        TPR={k: float(v) for k, v in d.get("TPR", {}).items()},
    )


def load_results(path: Path) -> Tuple[Dict[str, RuleResult], Tuple[str, ...]]:
    rules, notes, _n = _load_results_full(path)
    return rules, notes


def _load_results_full(path: Path) -> Tuple[Dict[str, RuleResult], Tuple[str, ...], int]:
    """Regeln, Hinweise und Gerätealarme im Fenster (Summe der Stundenwerte, sonst n_alarms)."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    rules: Dict[str, RuleResult] = {}
    for rule, key in SIM_RULE_KEYS.items():
        r = d["rules"].get(key)
        if r is None:
            continue
        rules[rule] = RuleResult(
            key=key, n_false=int(r["n_false"]), false_per_bed_h=float(r["false_per_bed_h"]),
            false_ci=_ci(r["false_per_bed_h_ci"]), n_crises=int(r["n_crises"]),
            caught_mild=int(r["caught"]), caught_strict=int(r["caught_strict"]),
            caught_mild_ci=_ci(r.get("caught_frac_ci")), caught_strict_ci=_ci(r.get("caught_strict_frac_ci")),
            n_escalations=int(r.get("n_escalations_window", 0)),
            caught_after_end=int(r.get("caught_after_end", int(r["caught"]) - int(r["caught_strict"]))),
        )
    hourly = d.get("hourly") or []
    n_alarms = sum(int(h.get("alarms", 0)) for h in hourly) if hourly else int(d.get("n_alarms", 0))
    return rules, tuple(str(n) for n in d.get("notes", [])), n_alarms


def run_files(key: str, data_dir: Path = DATA_DIR) -> Tuple[Path, Path]:
    spec = RUNS[key]
    return Path(data_dir) / f"messwerte_{spec.name}.json", Path(data_dir) / f"results_{spec.name}.json"


def available(data_dir: Path = DATA_DIR) -> List[str]:
    return [k for k in RUN_ORDER if all(p.exists() for p in run_files(k, data_dir))]


def load_run(key: str, data_dir: Path = DATA_DIR) -> RunData:
    mw_path, res_path = run_files(key, data_dir)
    rules, notes, n_alarms = _load_results_full(res_path)
    return RunData(spec=RUNS[key], m=load_messwerte(mw_path), rules=rules, notes=notes, n_alarms=n_alarms)


def load_runs(data_dir: Path = DATA_DIR, keys: Sequence[str] = RUN_ORDER) -> Dict[str, RunData]:
    return {k: load_run(k, data_dir) for k in keys if all(p.exists() for p in run_files(k, data_dir))}


# ─────────────────────────────────────────────────────────────────────────────
# Modell mit Messwerten
# ─────────────────────────────────────────────────────────────────────────────

def model_params(run: RunData, c_art: Optional[float] = None, tpr: Optional[float] = None) -> mz.Params:
    """Modelleingänge aus den Messwerten des Laufs; c_art standardmäßig der Wert „alle 4 Quellen“, TPR_j = η_j."""
    m = run.m
    return mz.Params(d_false=m.d_false, d_true=m.d_true, P=P_SIM,
                     c_art=m.c_art_a if c_art is None else c_art,
                     tpr=run.spec.tpr if tpr is None else tpr,
                     d0=D0_SIM, T=T_SIM, delta_min=DMIN_SIM, alpha=ALPHA_SIM,
                     mfpr_direct=m.mfpr, pc_direct=m.P_C)


def model_result(run: RunData, rule: str, c_art: Optional[float] = None) -> mz.Result:
    return mz.evaluate(model_params(run, c_art), rule)


def model_false_range(run: RunData, rule: str) -> Tuple[float, float]:
    """Fehleskalationen je Bett und Stunde zwischen c_art (alle 4) und c_art (≥ 2)."""
    a = model_result(run, rule, run.m.c_art_a).false_day / 24.0
    b = model_result(run, rule, run.m.c_art_b).false_day / 24.0
    return min(a, b), max(a, b)


def curve(run: RunData, rule: str, x_name: str, xs: Sequence[float], attr: str = "false_day",
          c_art: Optional[float] = None) -> np.ndarray:
    """Modellkurve über einen Parameter der x-Achse (Messwerte des Laufs, c_art fest)."""
    base = model_params(run, c_art)
    return np.array([getattr(mz.evaluate(mz.set_param(base, x_name, x), rule), attr) for x in xs], dtype=float)


def band(run: RunData, rule: str, x_name: str, xs: Sequence[float],
         attr: str = "false_day") -> Tuple[np.ndarray, np.ndarray]:
    """Band des Modells zwischen c_art (alle 4) und c_art (≥ 2): (untere, obere Kurve)."""
    a = curve(run, rule, x_name, xs, attr, run.m.c_art_a)
    b = curve(run, rule, x_name, xs, attr, run.m.c_art_b)
    return np.minimum(a, b), np.maximum(a, b)


# ─────────────────────────────────────────────────────────────────────────────
# Tabelle „Modell gegen Simulation“ und E4
# ─────────────────────────────────────────────────────────────────────────────

def overlaps(a: Tuple[float, float], b: Tuple[float, float]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


@dataclass(frozen=True)
class CompareRow:
    run: str
    rule: str
    model_false: Tuple[float, float]        # je Bett und Stunde, Bereich über c_art
    sim_false: float
    sim_false_ci: Tuple[float, float]
    model_caught: float                     # tpr_T (Kaltstart, d0 = 15 s)
    sim_caught: float
    sim_caught_ci: Tuple[float, float]
    sim_caught_n: int
    n_crises: int
    false_ok: bool                          # Intervall der Simulation schneidet den Modellbereich
    caught_ok: bool                         # Modellwert liegt im Intervall der Simulation


def compare(runs: Sequence[RunData], mode: str = DEFAULT_MODE, rules: Sequence[str] = SIM_RULES) -> List[CompareRow]:
    out: List[CompareRow] = []
    for run in runs:
        for rule in rules:
            if rule not in run.rules:
                continue
            r = run.rules[rule]
            mf = model_false_range(run, rule)
            mc = model_result(run, rule).tpr_T
            sc = r.caught_ci(mode)
            out.append(CompareRow(run.key, rule, mf, r.false_per_bed_h, r.false_ci, mc, r.caught_frac(mode), sc,
                                  r.caught(mode), r.n_crises, overlaps(mf, r.false_ci), sc[0] <= mc <= sc[1]))
    return out


@dataclass(frozen=True)
class E4Bar:
    """Säulen in E4 je Regel, je Station (10 Betten) und Woche."""

    model_false_a: float        # bei c_art (alle 4)
    model_false_b: float        # bei c_art (≥ 2)
    sim_false: float
    sim_false_ci: Tuple[float, float]
    model_missed: float
    sim_missed: float
    sim_missed_ci: Tuple[float, float]


def e4_values(run: RunData, rule: str, mode: str = DEFAULT_MODE) -> E4Bar:
    r = run.rules[rule]
    per_week = BEDS * WEEK_H                                   # je Bett und Stunde → je Station und Woche
    crises_week = run.m.crises_per_bed_day * WEEK_BED_DAYS       # gemessene Krisen je Station und Woche
    fa = model_result(run, rule, run.m.c_art_a)
    fb = model_result(run, rule, run.m.c_art_b)
    lo, hi = r.missed_ci(mode)
    return E4Bar(
        model_false_a=fa.false_day / 24.0 * per_week, model_false_b=fb.false_day / 24.0 * per_week,
        sim_false=r.false_per_bed_h * per_week, sim_false_ci=(r.false_ci[0] * per_week, r.false_ci[1] * per_week),
        model_missed=(1.0 - fa.tpr_T) * crises_week,
        sim_missed=r.missed_frac(mode) * crises_week, sim_missed_ci=(lo * crises_week, hi * crises_week),
    )


def crises_per_station_week(run: RunData) -> float:
    return run.m.crises_per_bed_day * WEEK_BED_DAYS


# ─────────────────────────────────────────────────────────────────────────────
# Reiter „Experimente“: D1–D4 (nur Daten)
# ─────────────────────────────────────────────────────────────────────────────

def count_ci(n: int) -> Tuple[float, float]:
    """95-%-Intervall (Poisson) einer Zählung."""
    return poisson_ci(n, 1.0)


def reduction(run: RunData) -> float:
    """D1: Senkung der Fehlalarmlast durch adaptiv = 1 − Fehleskalationen / Gerätefehlalarme (je Bett und Stunde)."""
    return 1.0 - run.rules["adaptiv"].false_per_bed_h / run.m.false_alarms_per_bed_h


def remaining_pct(run: RunData, rule: str) -> Tuple[float, Tuple[float, float]]:
    """D2: verbleibende Fehlalarme in % der Gerätefehlalarme (Wert, 95-%-Intervall)."""
    r = run.rules[rule]
    f = 100.0 / run.m.false_alarms_per_bed_h
    return r.false_per_bed_h * f, (r.false_ci[0] * f, r.false_ci[1] * f)


def bilanz_row(run: RunData, rule: str) -> Dict[str, int]:
    """D4-Tabelle: eine Zeile Lauf × Regel (Zählungen)."""
    r = run.rules[rule]
    return {
        "Gerätealarme": run.n_alarms,
        "davon falsch": run.n_false_alarms,                  # außerhalb von Krisen
        "in Krisen": run.n_alarms - run.n_false_alarms,
        "Krisen": r.n_crises,
        "Eskalationen gesamt": r.n_escalations,
        "davon falsch_esk": r.n_false,
        "erkannt": r.caught_strict,
        "nach Ende": r.caught_after_end,
        "verpasst": r.missed,
    }


def model_remaining_range(run: RunData, rule: str) -> Tuple[float, float]:
    """V2 (%): verbleibende Fehlalarme des Modells 100·(1 − ARR) zwischen c_art (alle 4) und (≥ 2)."""
    a = 100.0 * model_result(run, rule, run.m.c_art_a).remaining
    b = 100.0 * model_result(run, rule, run.m.c_art_b).remaining
    return min(a, b), max(a, b)


@dataclass(frozen=True)
class V4Bar:
    """V4: Modell gegen Simulation bei |M| = 4, je Bett und Stunde bzw. je 1000 Krisen."""

    model_false_a: float
    model_false_b: float
    sim_false: float
    sim_false_ci: Tuple[float, float]
    model_missed_1000: float            # (1 − tpr_T)·1000
    sim_missed_1000: float              # verpasst (auch mild) je 1000
    sim_after_end_1000: float           # Rot erst nach Krisenende je 1000
    sim_missed_strict_ci_1000: Tuple[float, float]


@dataclass(frozen=True)
class V4Relative:
    """V4 links, relativ zu 2-aus-N (%): Modell bei c_art (alle 4) und Simulation getrennt normiert."""

    model_pct: float
    sim_pct: float
    sim_ci_pct: Tuple[float, float]


def v4_relative(run: RunData, rules: Sequence[str] = SIM_RULES, ref: str = "r2") -> Dict[str, V4Relative]:
    vals = {rule: v4_values(run, rule) for rule in rules if rule in run.rules}
    m_ref, s_ref = vals[ref].model_false_a, vals[ref].sim_false
    return {rule: V4Relative(100.0 * v.model_false_a / m_ref, 100.0 * v.sim_false / s_ref,
                             (100.0 * v.sim_false_ci[0] / s_ref, 100.0 * v.sim_false_ci[1] / s_ref))
            for rule, v in vals.items()}


def v4_values(run: RunData, rule: str) -> V4Bar:
    r = run.rules[rule]
    fa = model_result(run, rule, run.m.c_art_a)
    fb = model_result(run, rule, run.m.c_art_b)
    lo, hi = r.missed_ci("streng")
    per = 1000.0 / r.n_crises
    return V4Bar(model_false_a=fa.false_day / 24.0, model_false_b=fb.false_day / 24.0,
                 sim_false=r.false_per_bed_h, sim_false_ci=r.false_ci,
                 model_missed_1000=(1.0 - fa.tpr_T) * 1000.0,
                 sim_missed_1000=r.missed * per, sim_after_end_1000=r.caught_after_end * per,
                 sim_missed_strict_ci_1000=(1000.0 * lo, 1000.0 * hi))
