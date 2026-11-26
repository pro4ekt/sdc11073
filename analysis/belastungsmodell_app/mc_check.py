"""Monte-Carlo-Gegenprobe der Episodenzählung in ``model_ziel3`` (Fehleskalationen je Bett und Stunde).

Simuliert genau die Annahmen des Modells: je Alarmkanal ein Wechsel aus/ein mit exponentialverteilten
Dauern (Zeitanteil q = (1 − c_art)·FPR, mittlere Ein-Dauer d_false), dazu gemeinsame Artefakte aller vier
Kanäle als eigener Prozess (Zeitanteil c_art·FPR, Dauer Exp(d_false)); Schritt 0,2 s, 2000 Bett-Stunden je
Punkt; k-aus-N eskaliert bei ≥ k aktiven Kanälen und hält, bis alle Alarme enden.  Ausgabe: Tabelle
Monte-Carlo / Formel für k = 2, 3 bei FPR 0,008 / 0,015 / 0,026 (erwartet: Abweichung ≤ 10 %).

    .venv\\Scripts\\python analysis/belastungsmodell_app/mc_check.py [--stunden 2000] [--seed 1] [--dt 0.2]
"""

from __future__ import annotations

import argparse
import time
from typing import Dict, List, Optional, Sequence

import numpy as np

try:
    from . import model_ziel3 as mz
except ImportError:
    import model_ziel3 as mz

P_SIM = (3, 3, 2, 1)
RULES = {"r2": 2, "r3": 3}


def on_series(rng: np.random.Generator, share: float, mu: float, n: int, dt: float) -> np.ndarray:
    """Ein-Zustand eines Kanals auf dem Zeitraster (alternierender Erneuerungsprozess, Exp-Dauern)."""
    if share <= 0:
        return np.zeros(n, dtype=bool)
    mean_off = mu * (1.0 - share) / share
    horizon = n * dt
    m = int(horizon / (mean_off + mu) * 3) + 20
    offs = rng.exponential(mean_off, m)
    ons = rng.exponential(mu, m)
    start_on = rng.random() < share                      # Gleichgewicht: Ein mit Wahrscheinlichkeit share
    if start_on:
        durations = np.empty(2 * m)
        durations[0::2] = ons
        durations[1::2] = offs
        on_slots = slice(0, None, 2)
    else:
        durations = np.empty(2 * m)
        durations[0::2] = offs
        durations[1::2] = ons
        on_slots = slice(1, None, 2)
    t = np.concatenate(([0.0], np.cumsum(durations)))
    starts = t[:-1][on_slots]
    ends = t[1:][on_slots]
    keep = starts < horizon
    starts, ends = starts[keep], np.minimum(ends[keep], horizon)
    diff = np.zeros(n + 1, dtype=np.int32)
    np.add.at(diff, np.floor(starts / dt).astype(int), 1)
    np.add.at(diff, np.ceil(ends / dt).astype(int), -1)
    return np.cumsum(diff[:-1]) > 0


def episodes(count: np.ndarray, k: int) -> int:
    """Eskalationsepisoden: Beginn bei ≥ k aktiven Kanälen, Ende erst, wenn kein Kanal mehr aktiv ist."""
    idx = np.arange(len(count))
    last_reset = np.maximum.accumulate(np.where(count == 0, idx, -1))
    trig = count >= k
    return int(len(np.unique(last_reset[trig]))) if trig.any() else 0


def simulate(fpr: float, c_art: float, mu: float, hours: int, dt: float, rng: np.random.Generator,
             ks: Sequence[int] = (2, 3)) -> Dict[int, float]:
    """Episoden je Bett und Stunde aus ``hours`` simulierten Bett-Stunden."""
    n = int(round(3600.0 / dt))
    q = (1.0 - c_art) * fpr
    totals = {k: 0 for k in ks}
    for _ in range(hours):
        count = np.zeros(n, dtype=np.int32)
        for _ch in range(4):
            count += on_series(rng, q, mu, n, dt)
        push = on_series(rng, c_art * fpr, mu, n, dt)
        count = np.where(push, 4, count)
        for k in ks:
            totals[k] += episodes(count, k)
    return {k: totals[k] / hours for k in ks}


def formula(fpr: float, c_art: float, mu: float) -> Dict[str, float]:
    """Episoden je Bett und Stunde nach ``model_ziel3`` (ohne Krisen, P(C) → 0)."""
    p = mz.Params(P=P_SIM, c_art=c_art, d_false=mu, mfpr_direct=4 * fpr, pc_direct=1e-9)
    return {rule: mz.evaluate(p, rule).false_day / 24.0 for rule in ("r2", "r3", "adaptiv")}


def run(fprs: Sequence[float] = (0.008, 0.015, 0.026), c_art: float = 0.1, mu: float = 8.0, hours: int = 2000,
        dt: float = 0.2, seed: int = 1) -> List[dict]:
    rng = np.random.default_rng(seed)
    rows = []
    for fpr in fprs:
        t0 = time.perf_counter()
        mc = simulate(fpr, c_art, mu, hours, dt, rng)
        f = formula(fpr, c_art, mu)
        for rule, k in RULES.items():
            rows.append(dict(fpr=fpr, rule=rule, k=k, mc=mc[k], formel=f[rule],
                             abweichung=100.0 * (f[rule] / mc[k] - 1.0) if mc[k] > 0 else float("nan"),
                             se=np.sqrt(mc[k] / hours), sekunden=time.perf_counter() - t0))
        rows.append(dict(fpr=fpr, rule="adaptiv", k=None, mc=float("nan"), formel=f["adaptiv"],
                         abweichung=float("nan"), se=float("nan"), sekunden=0.0))
    return rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Monte-Carlo-Gegenprobe der Episodenzählung (k-aus-N).")
    ap.add_argument("--stunden", type=int, default=2000, help="Bett-Stunden je Punkt (Standard 2000)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--dt", type=float, default=0.2, help="Zeitschritt in s (Standard 0,2)")
    ap.add_argument("--c-art", type=float, default=0.1)
    ap.add_argument("--d-false", type=float, default=8.0)
    args = ap.parse_args(argv)
    rows = run(c_art=args.c_art, mu=args.d_false, hours=args.stunden, dt=args.dt, seed=args.seed)
    print(f"Monte-Carlo ({args.stunden} Bett-Stunden je Punkt, dt = {args.dt} s, c_art = {args.c_art}, "
          f"d_false = {args.d_false} s, P = {P_SIM}); Episoden je Bett und Stunde")
    print(f"{'FPR':>6} {'Regel':>8} {'Monte-Carlo':>12} {'±SE':>6} {'Formel':>8} {'Abw. %':>7}")
    for r in rows:
        if r["k"] is None:
            print(f"{r['fpr']:>6} {'adaptiv':>8} {'–':>12} {'':>6} {r['formel']:>8.3f} {'(obere Grenze)':>7}")
        else:
            print(f"{r['fpr']:>6} {r['rule']:>8} {r['mc']:>12.3f} {r['se']:>6.3f} {r['formel']:>8.3f} "
                  f"{r['abweichung']:>+7.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
