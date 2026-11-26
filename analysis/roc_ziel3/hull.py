"""Obere konvexe Hülle der festen Regeln k-aus-4 (alle k, alle h) und Abstand der adaptiven Punkte dazu.

Zwei Sichten: nach Stück (x = Fehleskalationen je Bett·h, y = erkannte Krisen mild) und nach Zeit
(x = Fehleskalationszeit Variante (ii), y = Anteil der Krisenzeit mit Eskalation).  Die Hülle enthält den
Ursprung (0, 0).  Δ = y_adaptiv − y_Hülle(x_adaptiv) in Prozentpunkten; Δ_Sehne = y_adaptiv − y der Sehne
zwischen 2-aus-4 und 3-aus-4 desselben h (gleiche Gerätegrenzwerte).  95-%-Intervalle durch Bootstrap über
die Patienten (gepaart: Hülle, Sehne und adaptiver Punkt aus denselben gezogenen Patienten; Hülle in jedem
Zug neu).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .metrics import PATIENT_FIELDS

Point = Tuple[float, float]
K_RULES: Tuple[str, ...] = ("1-aus-4", "2-aus-4", "3-aus-4", "4-aus-4")
VIEWS: Tuple[str, ...] = ("stueck", "zeit")
F = {name: i for i, name in enumerate(PATIENT_FIELDS)}


def upper_hull(points: Sequence[Point], with_origin: bool = True) -> List[Point]:
    """Obere konvexe Hülle (Andrew), nach x sortiert; endliche Punkte, Duplikate entfernt."""
    pts = sorted({(float(x), float(y)) for x, y in points if math.isfinite(x) and math.isfinite(y)})
    if with_origin:
        pts = sorted(set(pts) | {(0.0, 0.0)})
    if len(pts) <= 2:
        return pts
    hull: List[Point] = []
    for p in pts:
        while len(hull) >= 2:
            (x1, y1), (x2, y2) = hull[-2], hull[-1]
            cross = (x2 - x1) * (p[1] - y1) - (y2 - y1) * (p[0] - x1)
            if cross >= 0:            # letzter Punkt liegt unter oder auf der Verbindung → entfernen
                hull.pop()
            else:
                break
        hull.append(p)
    return hull


def hull_value(hull: Sequence[Point], x: float) -> float:
    """Wert der Hülle bei x (linear zwischen den Eckpunkten); außerhalb der Hülle NaN."""
    if not hull or not math.isfinite(x) or x < hull[0][0] or x > hull[-1][0]:
        return float("nan")
    xs = [p[0] for p in hull]
    ys = [p[1] for p in hull]
    return float(np.interp(x, xs, ys))


def chord_value(a: Point, b: Point, x: float) -> float:
    """Gerade durch a und b bei x (auch außerhalb der Strecke, linear verlängert)."""
    if a[0] == b[0]:
        return float("nan")
    return a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0])


# ─────────────────────────────────────────────────────────────────────────────
# Kennzahlen aus Patientensummen
# ─────────────────────────────────────────────────────────────────────────────

def xy_from_sums(s: np.ndarray, view: str) -> Tuple[np.ndarray, np.ndarray]:
    """s: (..., len(PATIENT_FIELDS)) Summen über Patienten → (x, y) der Sicht."""
    if view == "stueck":
        x = s[..., F["n_false"]] / s[..., F["bed_h"]]
        y = s[..., F["n_det"]] / s[..., F["n_crises"]]
    else:
        x = s[..., F["t_false"]] / s[..., F["t_stable"]]
        y = s[..., F["t_in_crisis"]] / s[..., F["t_crisis"]]
    return x, y


def _arr(stats: Dict[str, List[float]], patients: Sequence[str]) -> np.ndarray:
    return np.array([stats[p] for p in patients], dtype=float)


@dataclass
class HullResult:
    view: str
    hull: List[Point]
    rows: List[dict] = field(default_factory=list)       # je h: x, y_adaptiv, y_huelle, delta, y_sehne, delta_sehne (+ CI)


def analyse(patient_stats: Dict[str, Dict[str, Dict[str, List[float]]]], hs: Sequence[float],
            h_targets: Sequence[float] = (0.8, 1.0, 1.2), n_boot: int = 2000, seed: int = 7,
            adaptiv_key: str = "adaptiv", views: Sequence[str] = VIEWS) -> Dict[str, HullResult]:
    """patient_stats[Regel][h als 'g'-String][Patient] = Werte in PATIENT_FIELDS."""
    rng = np.random.default_rng(seed)
    patients = sorted(next(iter(patient_stats[adaptiv_key].values())).keys())
    P = len(patients)
    idx = rng.integers(0, P, size=(n_boot, P))
    arrays: Dict[Tuple[str, str], np.ndarray] = {}
    for rule in list(K_RULES) + [adaptiv_key]:
        for h in hs:
            key = f"{h:g}"
            if key in patient_stats.get(rule, {}):
                arrays[(rule, key)] = _arr(patient_stats[rule][key], patients)
    out: Dict[str, HullResult] = {}
    for view in views:
        # Punktschätzung
        pts = [xy_from_sums(arrays[(r, f"{h:g}")].sum(axis=0), view) for r in K_RULES for h in hs if (r, f"{h:g}") in arrays]
        hull = upper_hull([(float(x), float(y)) for x, y in pts])
        res = HullResult(view=view, hull=hull)
        boot_cache: Dict[str, np.ndarray] = {}
        for h in hs:
            key = f"{h:g}"
            if (adaptiv_key, key) not in arrays:
                continue
            xa, ya = xy_from_sums(arrays[(adaptiv_key, key)].sum(axis=0), view)
            x2, y2 = xy_from_sums(arrays[("2-aus-4", key)].sum(axis=0), view)
            x3, y3 = xy_from_sums(arrays[("3-aus-4", key)].sum(axis=0), view)
            yh = hull_value(hull, float(xa))
            yc = chord_value((float(x3), float(y3)), (float(x2), float(y2)), float(xa))
            row = dict(h=h, x=float(xa), y_adaptiv=float(ya), y_huelle=yh, delta_pp=100 * (float(ya) - yh),
                       x_2=float(x2), y_2=float(y2), x_3=float(x3), y_3=float(y3), y_sehne=yc,
                       delta_sehne_pp=100 * (float(ya) - yc),
                       sehne_innerhalb=bool(min(x2, x3) <= xa <= max(x2, x3)))
            if any(abs(h - t) < 1e-9 for t in h_targets):
                d_h, d_c = _bootstrap(arrays, hs, key, idx, view, adaptiv_key)
                row.update(delta_ci=[float(np.nanpercentile(d_h, 2.5)), float(np.nanpercentile(d_h, 97.5))],
                           delta_sehne_ci=[float(np.nanpercentile(d_c, 2.5)), float(np.nanpercentile(d_c, 97.5))],
                           n_boot=int(idx.shape[0]), boot_nan=int(np.isnan(d_h).sum()))
            res.rows.append(row)
        out[view] = res
    return out


def _bootstrap(arrays, hs, key, idx, view, adaptiv_key) -> Tuple[np.ndarray, np.ndarray]:
    B = idx.shape[0]
    sums = {k: a[idx].sum(axis=1) for k, a in arrays.items()}              # (B, Felder)
    xy = {k: xy_from_sums(s, view) for k, s in sums.items()}
    xa, ya = xy[(adaptiv_key, key)]
    x2, y2 = xy[("2-aus-4", key)]
    x3, y3 = xy[("3-aus-4", key)]
    d_h = np.full(B, np.nan)
    d_c = np.full(B, np.nan)
    kpts = [(r, f"{h:g}") for r in K_RULES for h in hs if (r, f"{h:g}") in arrays]
    for b in range(B):
        hull = upper_hull([(float(xy[k][0][b]), float(xy[k][1][b])) for k in kpts])
        yh = hull_value(hull, float(xa[b]))
        d_h[b] = 100 * (ya[b] - yh) if math.isfinite(yh) else np.nan
        yc = chord_value((float(x3[b]), float(y3[b])), (float(x2[b]), float(y2[b])), float(xa[b]))
        d_c[b] = 100 * (ya[b] - yc) if math.isfinite(yc) else np.nan
    return d_h, d_c


def summary_sentence(res: HullResult, eta_label: str) -> str:
    """Ein Satz: in welchem x-Bereich adaptiv über der Hülle, auf ihr (|Δ| < 1 pp) oder darunter liegt; Sehne."""
    above = [r for r in res.rows if math.isfinite(r["delta_pp"]) and r["delta_pp"] > 1.0]
    on = [r for r in res.rows if math.isfinite(r["delta_pp"]) and abs(r["delta_pp"]) <= 1.0]
    below = [r for r in res.rows if math.isfinite(r["delta_pp"]) and r["delta_pp"] < -1.0]
    chord_above = [r for r in res.rows if math.isfinite(r["delta_sehne_pp"]) and r["delta_sehne_pp"] > 0]

    def rng(rows):
        xs = [r["x"] for r in rows]
        return f"x = {min(xs):.3g} … {max(xs):.3g}" if xs else "–"
    unit = "Fehleskalationen je Bett·h" if res.view == "stueck" else "Zeitanteil"
    return (f"{eta_label}, {('nach Stück' if res.view == 'stueck' else 'nach Zeit')} ({unit}): über der Hülle bei "
            f"{rng(above)} ({len(above)} h-Punkte), auf der Hülle (|Δ| ≤ 1 pp) bei {rng(on)} ({len(on)}), darunter bei "
            f"{rng(below)} ({len(below)}); über der Sehne 2-/3-aus-4 des eigenen h bei {len(chord_above)} von {len(res.rows)} h.")
