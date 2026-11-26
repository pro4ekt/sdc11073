"""Theorie je Schwellenfaktor h: (a) Formel des Betreuers (binomial) und (b) das Belastungsmodell.

(a) Unabhängige, gleiche Geräte mit p = TPR̄ (im Krisenfall) bzw. p = FPR̄ (stabil):
        P_k(p) = Σ_{i ≥ k} C(4, i) p^i (1 − p)^{4−i},  k = 1 … 4
    → System-TPR_k = P_k(TPR̄), System-FPR_k = P_k(FPR̄).

(b) ``analysis/belastungsmodell_app/model_ziel3.py`` (Kapitel 4/5) mit den gemessenen Geräteraten des
    jeweiligen h als Eingänge: TPR_j := TPR̄ (Zeitanteil), |M|·FPR := Σ FPR_j (Zeitanteil), c_art, d_false,
    P(C) direkt, P = (3, 3, 2, 1), d0, T, δ_min, α wie im Vergleich Modell – Simulation (``sim_data``).
    Nach Zeit: FPR_sys = noise_f / (1 − P(C)) (Fehlalarmbelegung mit Regel bezogen auf die stabile Zeit),
    TPR_sys = tpr_inf (Krise hält an).  Nach Stück: Fehleskalationen je Bett·h = false_day / 24,
    erkannte Krisen = tpr_T (Kaltstart, endliche Krisendauer).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from analysis.belastungsmodell_app import model_ziel3 as mz
from analysis.belastungsmodell_app import sim_data as sd

N_DEV = 4
RULE_OF_K: Dict[int, str] = {2: "r2", 3: "r3"}
MODEL_RULES: Tuple[str, ...] = ("r2", "r3", "adaptiv")
MODEL_NAMES: Dict[str, str] = {"r2": "2-aus-4", "r3": "3-aus-4", "adaptiv": "adaptiv"}


def binom_k_of_n(p: float, k: int, n: int = N_DEV) -> float:
    """P(mindestens k von n unabhängigen Geräten aktiv) bei Einzelwahrscheinlichkeit p."""
    if not (0.0 <= p <= 1.0):
        return float("nan")
    return float(sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1)))


@dataclass(frozen=True)
class DeviceSummary:
    """Mittel der vier Geräte bei einem h (Eingänge der Theorie)."""

    h: float
    tpr_mean: float            # TPR̄ (Zeitanteil)
    fpr_mean: float            # FPR̄ (Zeitanteil)
    mfpr: float                # Σ FPR_j
    c_art: float               # Anteil „alle 4 Quellen“ (c_art a)
    c_art_ge2: float
    d_false: float             # mittlere Dauer eines Fehlalarms [s]
    d_true: float              # mittlere Krisendauer [s]
    pc: float                  # P(C) = Krisenzeit / (Patienten · Fenster)
    n_per_source_day: float    # Alarme je Quelle und Tag
    fdr: float                 # Anteil falscher Gerätealarme


def model_params(ds: DeviceSummary, c_art: Optional[float] = None) -> mz.Params:
    tpr = min(max(ds.tpr_mean, 1e-6), 1 - 1e-6)
    return mz.Params(n=max(ds.n_per_source_day, 1e-9), fdr=min(max(ds.fdr, 0.0), 1 - 1e-9),
                     d_false=max(ds.d_false, 1e-6), d_true=max(ds.d_true, 1e-6), P=sd.P_SIM,
                     c_art=min(max(ds.c_art if c_art is None else c_art, 0.0), 1.0), tpr=tpr,
                     d0=sd.D0_SIM, T=sd.T_SIM, delta_min=sd.DMIN_SIM, alpha=sd.ALPHA_SIM,
                     mfpr_direct=max(ds.mfpr, 1e-12), pc_direct=min(max(ds.pc, 1e-9), 1 - 1e-9))


@dataclass(frozen=True)
class ModelPoint:
    rule: str
    tpr: float                 # tpr_inf
    fpr: float                 # noise_f / (1 − P(C))
    detected: float            # tpr_T
    false_per_bed_h: float     # false_day / 24
    false_day: float
    w: float                   # ln(TPR̄/FPR̄) der Kalibrierung


def model_point(ds: DeviceSummary, rule: str, c_art: Optional[float] = None) -> ModelPoint:
    p = model_params(ds, c_art)
    cal = mz.calibrate(p)
    res = mz.evaluate(p, rule, cal)
    fpr = res.noise_f / (1 - res.PC) if res.PC < 1 else float("nan")
    return ModelPoint(rule=rule, tpr=res.tpr_inf, fpr=fpr, detected=res.tpr_T,
                      false_per_bed_h=res.false_day / 24.0, false_day=res.false_day, w=cal.w)


def formula_point(ds: DeviceSummary, k: int) -> Tuple[float, float]:
    """(FPR_sys, TPR_sys) der Formel (a) für k-aus-4."""
    return binom_k_of_n(ds.fpr_mean, k), binom_k_of_n(ds.tpr_mean, k)
