"""Modellerweiterung E1 „versetzter Einstieg“ (Monte-Carlo, Kapitel-3-Regel).

Gemessen je h (``metrics.crisis_entries``): Teilnahmeanteil η_j jeder Quelle an den Krisen, die
Eintrittsverzögerung u_j (Krisenbeginn → erstes Überschreiten der Schwelle; 0, wenn der Alarm schon vor dem
Beginn aktiv war) und der Nachlauf v_j (Ausschalten minus Krisenende; negativ, wenn der Alarm vor dem Ende
ausging).  Modell: eine teilnehmende Quelle ist von u_j bis D + v_j aktiv, D = d0 + Exp(d_true − d0)
(Generator: 15 s + Exp(15 s)); Teilnahme unabhängig je Quelle mit η_j, u_j und v_j als Bootstrap aus den
gemessenen Stichproben der Quelle.

Ausgaben je Regel: Anteil der Krisenzeit unter Eskalation y = E[max(0, D − t_esc)] / E[D], erkannte Krisen
streng P(t_esc ≤ D) und mild P(t_esc ≤ D + G) mit G = 10 s (die Eskalation nach dem Krisenende setzt
voraus, dass noch Geräte alarmieren – dafür der gemessene Nachlauf), und die Verteilung von t_esc.
* k-aus-4: t_esc = t_(k), die k-te Verzögerung der teilnehmenden Quellen (weniger als k → nie).
* adaptiv (Kapitel 3, gleiche Gewichte): s_j(t) = Anteil des Fensters [t − T, t], in dem die Quelle aktiv ist
  (ZOH), E = Σ s_j, SDC-Score aus den Prioritäten P = (3, 3, 2, 1) der aktiven Quellen (α),
  k_min = rhu(|M| − (|M| − 2)·SDC) ∈ [2, |M|], δ(t) nach (3.20) mit λ = SDC/T und Karenz T,
  Θ_target = max(1, k_min·δ), Hysterese Θ_current (schneller Anstieg der Empfindlichkeit, langsame Rückkehr mit
  ρ = (1 − SDC)/T), Eskalation bei E ≥ Θ_current, solange mindestens eine Quelle aktiv ist (der Kern wertet nur
  Ensembles mit aktivem Alarm aus und setzt nach dem letzten Ausschalten zurück); Zeitschritt dt.

Kontrolle: u_j ≡ 0, v_j ≡ 0 und η_j = TPR_j reproduzieren ``model_ziel3``: k-aus-N y = tpr_inf, adaptiv
erkannt (streng) = tpr_T.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

import numpy as np

from .journals import SOURCES
from .metrics import CrisisEntry

P_SIM: Tuple[int, ...] = (3, 3, 2, 1)
D0 = 15.0
GUARD = 10.0
T_MAX = 240.0


@dataclass(frozen=True)
class E1Inputs:
    h: float
    eta: Dict[str, float]                  # Teilnahmeanteil je Quelle
    delays: Dict[str, Tuple[float, ...]]   # Stichprobe u_j [s] der teilnehmenden Quellen
    d_true: float                          # mittlere Krisendauer [s]
    d0: float = D0
    n_crises: int = 0
    tails: Optional[Dict[str, Tuple[float, ...]]] = None   # Stichprobe v_j [s] (Ausschalten − Krisenende)

    def delay_quantiles(self, src: str, qs: Sequence[float] = (0.5, 0.9)) -> Tuple[float, ...]:
        d = sorted(self.delays.get(src, ()))
        if not d:
            return tuple(float("nan") for _ in qs)
        return tuple(d[min(len(d) - 1, int(q * len(d)))] for q in qs)

    def tail_quantiles(self, src: str, qs: Sequence[float] = (0.5, 0.9)) -> Tuple[float, ...]:
        d = sorted((self.tails or {}).get(src, ()))
        if not d:
            return tuple(float("nan") for _ in qs)
        return tuple(d[min(len(d) - 1, int(q * len(d)))] for q in qs)

    def already_on_share(self, src: str) -> float:
        d = self.delays.get(src, ())
        return sum(1 for x in d if x <= 0.0) / len(d) if d else float("nan")


def e1_inputs(entries: Sequence[CrisisEntry], h: float, d_true: Optional[float] = None, d0: float = D0) -> E1Inputs:
    eta: Dict[str, float] = {}
    delays: Dict[str, Tuple[float, ...]] = {}
    tails: Dict[str, Tuple[float, ...]] = {}
    n = len(entries)
    for s in SOURCES:
        part = [c for c in entries if c.participates.get(s)]
        eta[s] = len(part) / n if n else float("nan")
        delays[s] = tuple(c.u[s] for c in part)
        tails[s] = tuple(c.tail[s] for c in part if s in c.tail)
    if d_true is None:
        d_true = float(np.mean([c.duration for c in entries])) if entries else 30.0
    return E1Inputs(h=h, eta=eta, delays=delays, d_true=d_true, d0=d0, n_crises=n, tails=tails)


@dataclass(frozen=True)
class E1Result:
    rule: str
    T: float
    share: float            # y: Anteil der Krisenzeit unter Eskalation
    caught: float           # streng: P(t_esc ≤ D)
    caught_mild: float      # mild: P(t_esc ≤ D + G)
    t_esc_median: float
    t_esc_p90: float
    n: int


def _sample(inp: E1Inputs, n: int, rng: np.random.Generator) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(u (n, 4) mit inf für nicht teilnehmende Quellen, v (n, 4) Nachlauf, D (n,))."""
    u = np.full((n, len(SOURCES)), np.inf)
    v = np.zeros((n, len(SOURCES)))
    for j, s in enumerate(SOURCES):
        eta = inp.eta.get(s, 0.0)
        d = np.asarray(inp.delays.get(s, ()), dtype=float)
        if not (eta > 0) or d.size == 0:
            continue
        part = rng.random(n) < eta
        k = int(part.sum())
        u[part, j] = d[rng.integers(0, d.size, k)]
        tails = np.asarray((inp.tails or {}).get(s, ()), dtype=float)
        if tails.size:
            v[part, j] = tails[rng.integers(0, tails.size, k)]
    d0 = min(inp.d0, inp.d_true - 1e-6)
    D = d0 + rng.exponential(max(inp.d_true - d0, 1e-9), n)
    return u, v, D


def _finish(rule: str, T: float, t_esc: np.ndarray, D: np.ndarray, guard: float = GUARD) -> E1Result:
    share = float(np.mean(np.maximum(0.0, D - t_esc)) / np.mean(D))
    fin = t_esc[np.isfinite(t_esc)]
    q = (lambda p: float(np.quantile(fin, p))) if fin.size else (lambda p: float("nan"))
    return E1Result(rule=rule, T=T, share=share, caught=float(np.mean(t_esc <= D)),
                    caught_mild=float(np.mean(t_esc <= D + guard)), t_esc_median=q(0.5), t_esc_p90=q(0.9),
                    n=int(D.size))


def e1_k_of_n(inp: E1Inputs, k: int, n: int = 100_000, seed: int = 1) -> E1Result:
    rng = np.random.default_rng(seed)
    u, v, D = _sample(inp, n, rng)
    end = np.where(np.isfinite(u), np.maximum(D[:, None] + v, u + 1e-9), np.inf)
    # k-te Eintrittszeit; sie zählt nur, wenn die früher eingetretenen Quellen dann noch aktiv sind
    order = np.sort(u, axis=1)
    t_k = order[:, k - 1].copy()
    active_at = (u <= t_k[:, None]) & (end > t_k[:, None])
    t_k[(active_at.sum(axis=1) < k)] = np.inf
    return _finish(f"{k}-aus-4", float("nan"), t_k, D)


def rhu(x: np.ndarray) -> np.ndarray:
    return np.floor(x + 0.5 + 1e-12)


def e1_adaptiv(inp: E1Inputs, T: float, n: int = 100_000, dt: float = 0.25, seed: int = 1,
               delta_min: float = 0.5, alpha: float = 0.5, P: Sequence[int] = P_SIM) -> E1Result:
    rng = np.random.default_rng(seed)
    u, v, D = _sample(inp, n, rng)
    end = np.where(np.isfinite(u), np.maximum(D[:, None] + v, u + 1e-9), -np.inf)   # Ende der Aktivität
    P = np.asarray(P, dtype=float)
    M = len(P)
    p_max, p_sum = P.max(), P.sum()
    t_esc = np.full(n, np.inf)
    theta_prev = np.full(n, np.nan)              # Kaltstart der Hysterese
    tau = np.zeros(n)
    esc_prev = np.zeros(n, dtype=bool)
    t = 0.0
    t_end = min(float(np.nanmax(np.where(np.isfinite(end), end, 0.0))), T_MAX)
    while t <= t_end:
        open_ = ~np.isfinite(t_esc)
        if not open_.any():
            break
        a = (t >= u) & (t < end)                                      # aktive Quellen
        # s_j(t) = Anteil des Fensters [t − T, t], in dem die Quelle aktiv war (ZOH), auch nach dem Ausschalten
        lo = np.maximum(u, t - T)
        hi = np.minimum(end, t)
        s = np.clip((hi - lo) / T, 0.0, 1.0)
        s[~np.isfinite(u)] = 0.0
        E = s.sum(axis=1)
        v_ = a * P[None, :]
        sdc = alpha * v_.max(axis=1) / p_max + (1 - alpha) * v_.sum(axis=1) / p_sum
        k_min = np.clip(rhu(M - (M - 2) * sdc), 2, M)
        tau = np.where(E <= 0.0, 0.0, np.where(esc_prev, tau, tau + dt))
        lam = sdc / T
        delta = np.maximum(delta_min, np.exp(-lam * np.maximum(0.0, tau - T)))
        theta_t = np.maximum(1.0, k_min * delta)
        rho = (1.0 - sdc) / T
        cold = np.isnan(theta_prev)
        attack = theta_t <= theta_prev
        theta_cur = np.where(cold | attack, theta_t, theta_t + (theta_prev - theta_t) * np.exp(-rho * dt))
        theta_prev = theta_cur
        esc = (E >= theta_cur) & (E > 0.0) & a.any(axis=1)              # nur solange ein Alarm aktiv ist
        newly = esc & open_
        t_esc[newly] = t
        esc_prev = esc
        t += dt
    return _finish("adaptiv", T, t_esc, D)


def e1_all(inp: E1Inputs, Ts: Sequence[float] = (10.0, 20.0, 30.0), n: int = 100_000, seed: int = 1,
           dt: float = 0.25) -> Dict[str, E1Result]:
    out: Dict[str, E1Result] = {}
    for k in (1, 2, 3, 4):
        out[f"{k}-aus-4"] = e1_k_of_n(inp, k, n, seed)
    for T in Ts:
        out[f"adaptiv_T{T:g}"] = e1_adaptiv(inp, T, n, dt, seed)
    return out
