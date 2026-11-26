"""ROC-Werkstatt: adaptiv-Konfigurationen (Fenster T, α, δ_min) offline mit dem unveränderten Dashboard-Kern.

Je ROC-Verzeichnis (``logs/ziel3_roc`` = η 0,9, ``logs/ziel3_roc_p07`` = η 0,7) und je Grenzwertfaktor h wird die
Regel *adaptiv* für jede Konfiguration nachgerechnet, die sich in genau einem Parameter von der Basis
(T = 10 s, α = 0,5, δ_min = 0,5) unterscheidet: α ∈ {0,3; 0,5; 0,7; 0,9}, δ_min ∈ {0,3; 0,5; 0,7; 1,0},
T ∈ {5; 10; 20; 30} s.  T und α gehen über die Datenbank (``filter_params``) in den Kern, δ_min über das
Parameterobjekt ``EngineConfig`` (``dashcore.AdaptivReplay(delta_min=…)``); der Dashboard-Code bleibt unverändert.

Je Konfiguration, η und h werden gespeichert (``werkstatt/werkstatt.csv``): erkannte Krisen mild/streng,
Fehleskalationen je Bett·h, TPR_sys und FPR_sys (Variante ii) nach Zeit, Latenz Median/p90 ab erstem Überschreiten
einer Gerätegrenze (und ab Krisenbeginn), Nachlauf-Anteil, Zahl der Rot-Abschnitte.  Bei h = 1 zusätzlich
(``werkstatt/werkstatt_h1.json``): Δ zur Sehne 2-/3-aus-4 desselben h und Δ zur oberen konvexen Hülle aller
k-aus-4-Punkte (nach Stück und nach Zeit) mit 95-%-Intervallen aus einem gepaarten Bootstrap über die Patienten
(1000 Züge; Hülle, Sehne und adaptiver Punkt aus denselben gezogenen Patienten).  Die Patientensummen liegen in
``werkstatt/werkstatt_patienten.json``.  Replays werden in ``adaptiv_replay/`` zwischengespeichert
(Name mit T, α, δ_min; Basis- und reine T-Konfigurationen benutzen die vorhandenen Dateien von ``run_roc``).

    .venv\\Scripts\\python -m analysis.roc_ziel3.werkstatt                       # beide η, alle Konfigurationen
    .venv\\Scripts\\python -m analysis.roc_ziel3.werkstatt --roc-dir experiments/ziel3_simulation/logs/ziel3_roc_p07
    .venv\\Scripts\\python -m analysis.roc_ziel3.werkstatt --nur-deltas          # nur Δ/Bootstrap aus gespeicherten Summen
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import dashcore as dc
from . import hull as hl
from . import journals as jn
from . import metrics as mt
from . import run_roc as rr

Config = Tuple[float, float, float]          # (T, alpha, delta_min)

BASE: Config = (10.0, 0.5, 0.5)
ALPHAS: Tuple[float, ...] = (0.3, 0.5, 0.7, 0.9)
DMINS: Tuple[float, ...] = (0.3, 0.5, 0.7, 1.0)
TS: Tuple[float, ...] = (5.0, 10.0, 20.0, 30.0)
PARAM_NAMES: Dict[str, str] = {"T": "T", "alpha": "α", "delta_min": "δ_min"}
SUBDIR = "werkstatt"
CSV_NAME = "werkstatt.csv"
H1_NAME = "werkstatt_h1.json"
PAT_NAME = "werkstatt_patienten.json"
ETA_OF_DIR: Dict[str, str] = {rr.ROC_DIR.name: "0,9", rr.ROC_DIR_P07.name: "0,7"}
K_RULES: Tuple[str, ...] = ("1-aus-4", "2-aus-4", "3-aus-4", "4-aus-4")
VIEWS: Tuple[str, ...] = ("stueck", "zeit")
H_TARGET = 1.0

CSV_FIELDS: Tuple[str, ...] = (
    "eta", "h", "konfig", "T", "alpha", "delta_min", "tpr", "fpr", "fpr_ii", "hvl_anteil", "erkannt", "erkannt_streng",
    "verspaetet", "n_krisen", "n_erkannt", "n_erkannt_streng", "n_ereignisse", "n_falsch", "falsch_je_bett_h",
    "d_false_s", "verz_median_s", "verz_p90_s", "verz_kreuz_median_s", "verz_kreuz_p90_s", "n_latenz", "n_rot",
    "bett_h", "wall_s",
)
NUMERIC: Tuple[str, ...] = tuple(f for f in CSV_FIELDS if f not in ("eta", "konfig"))


# ─────────────────────────────────────────────────────────────────────────────
# Konfigurationen
# ─────────────────────────────────────────────────────────────────────────────

def _same(a: float, b: float) -> bool:
    return abs(a - b) < 1e-9


def configs() -> List[Config]:
    """Basis plus alle Konfigurationen, die sich in genau einem Parameter unterscheiden (ohne Doppelte)."""
    out: List[Config] = [BASE]
    for T in TS:
        c = (float(T), BASE[1], BASE[2])
        if c not in out:
            out.append(c)
    for a in ALPHAS:
        c = (BASE[0], float(a), BASE[2])
        if c not in out:
            out.append(c)
    for d in DMINS:
        c = (BASE[0], BASE[1], float(d))
        if c not in out:
            out.append(c)
    return out


def key(T: float, alpha: float, delta_min: float) -> str:
    return f"T{T:g}_a{alpha:g}_d{delta_min:g}"


def parse_key(k: str) -> Config:
    parts = k.split("_")
    return float(parts[0][1:]), float(parts[1][1:]), float(parts[2][1:])


def varied(T: float, alpha: float, delta_min: float) -> Optional[str]:
    """Name des Parameters, der von der Basis abweicht (None = Basis)."""
    diff = [n for n, v, b in (("T", T, BASE[0]), ("alpha", alpha, BASE[1]), ("delta_min", delta_min, BASE[2]))
            if not _same(v, b)]
    return diff[0] if len(diff) == 1 else (None if not diff else "+".join(diff))


def label(T: float, alpha: float, delta_min: float, short: bool = False) -> str:
    """„adaptiv (T = 10 s, α = 0,5, δ_min = 0,5)“ bzw. kurz nur der abweichende Parameter."""
    de = lambda v: f"{v:g}".replace(".", ",")        # noqa: E731  (T: 5, 10, 20, 30)
    de1 = lambda v: f"{v:.1f}".replace(".", ",")     # noqa: E731  (α, δ_min: eine Nachkommastelle)
    if short:
        v = varied(T, alpha, delta_min)
        if v is None:
            return "adaptiv (Basis)"
        val = {"T": f"T = {de(T)} s", "alpha": f"α = {de1(alpha)}", "delta_min": f"δ_min = {de1(delta_min)}"}
        return "adaptiv, " + ", ".join(val[p] for p in v.split("+"))
    return f"adaptiv (T = {de(T)} s, α = {de1(alpha)}, δ_min = {de1(delta_min)})"


# ─────────────────────────────────────────────────────────────────────────────
# Replay je Konfiguration (mit Zwischenspeicher)
# ─────────────────────────────────────────────────────────────────────────────

def cache_path(cache_dir: Path, folder_name: str, raw: dict, contract: dict, delta_min: float) -> Path:
    """Basis in α und δ_min → Dateiname von ``run_roc`` (vorhandene Replays werden weiterverwendet), sonst eigener."""
    T = dc.horizon_T(raw)
    alpha = float(raw.get("filter_params", {}).get("alpha", 0.5))
    if _same(alpha, BASE[1]) and _same(delta_min, BASE[2]):
        return cache_dir / rr.cache_name(folder_name, raw, contract)
    return cache_dir / f"{folder_name}_T{T:g}_a{alpha:g}_d{delta_min:g}.json"


def replay_config(j: jn.Journals, contract: dict, T: float, alpha: float, delta_min: float, grid_phase: float,
                  cache_dir: Path, use_cache: bool = True) -> Tuple[Dict[str, List[Tuple[float, float, float]]], dict]:
    raw = dc.build_raw_clinical_db(contract, horizon_T=float(T), alpha=float(alpha))
    if _same(alpha, BASE[1]) and _same(delta_min, BASE[2]):
        return rr.replay_cached(j, contract, raw, grid_phase, cache_dir, use_cache)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_path(cache_dir, j.folder.name, raw, contract, delta_min)
    k = dict(grid_phase=grid_phase, filter_params=raw.get("filter_params"), profile=dc._first_profile(raw),
             delta_min=float(delta_min), n_alarms=len(j.alarms), t_start=j.t_start, t_end=j.t_end)
    if use_cache and path.exists():
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            if d.get("key") == k:
                return {p: [tuple(x) for x in v] for p, v in d["rot"].items()}, d["meta"]
        except (json.JSONDecodeError, KeyError):
            pass
    rep = dc.AdaptivReplay(dc.channels_from_journals(j, contract), raw, room=contract.get("room", "ICU-3"),
                           grid_phase=grid_phase, delta_min=None if _same(delta_min, BASE[2]) else float(delta_min))
    res = rep.run(j.alarms, j.t_start, j.t_end)
    meta = dict(n_rot=res.n_rot, n_events=res.n_events, n_grid=res.n_grid, n_gc=res.n_gc,
                n_updates=res.n_updates, wall_s=round(res.wall_s, 1), params=res.params)
    path.write_text(json.dumps(dict(key=k, meta=meta, rot=res.rot), ensure_ascii=False), encoding="utf-8")
    return res.rot, meta


# ─────────────────────────────────────────────────────────────────────────────
# Δ zur Sehne und zur Hülle bei h = 1 (gepaarter Bootstrap über die Patienten)
# ─────────────────────────────────────────────────────────────────────────────

def deltas_h1(cfg_patients: Dict[str, Dict[str, Dict[str, List[float]]]],
              k_patients: Dict[str, Dict[str, Dict[str, List[float]]]], n_boot: int = 1000, seed: int = 7,
              h_target: float = H_TARGET) -> Dict[str, dict]:
    """cfg_patients[konfig][h][Patient], k_patients[Regel][h][Patient] → je Konfiguration und Sicht:
    x, y_adaptiv, y_huelle, delta_pp (+ CI), y_sehne, delta_sehne_pp (+ CI), sehne_innerhalb, n_boot, boot_nan."""
    hk = f"{h_target:g}"
    if hk not in k_patients.get("2-aus-4", {}):
        return {}
    patients = sorted(k_patients["2-aus-4"][hk].keys())
    P = len(patients)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, P, size=(n_boot, P))
    arr_k = {(r, h): np.array([k_patients[r][h][p] for p in patients], dtype=float)
             for r in K_RULES for h in k_patients.get(r, {}) if all(p in k_patients[r][h] for p in patients)}
    kkeys = sorted(arr_k)
    out: Dict[str, dict] = {}
    for view in VIEWS:
        pts = [hl.xy_from_sums(arr_k[k].sum(axis=0), view) for k in kkeys]
        hull = hl.upper_hull([(float(x), float(y)) for x, y in pts])
        x2, y2 = hl.xy_from_sums(arr_k[("2-aus-4", hk)].sum(axis=0), view)
        x3, y3 = hl.xy_from_sums(arr_k[("3-aus-4", hk)].sum(axis=0), view)
        bsum = {k: a[idx].sum(axis=1) for k, a in arr_k.items()}                 # (B, Felder)
        bxy = {k: hl.xy_from_sums(s, view) for k, s in bsum.items()}
        hulls = [hl.upper_hull([(float(bxy[k][0][b]), float(bxy[k][1][b])) for k in kkeys]) for b in range(n_boot)]
        x2b, y2b = bxy[("2-aus-4", hk)]
        x3b, y3b = bxy[("3-aus-4", hk)]
        for cfg, per_h in cfg_patients.items():
            if hk not in per_h or not all(p in per_h[hk] for p in patients):
                continue
            a = np.array([per_h[hk][p] for p in patients], dtype=float)
            xa, ya = hl.xy_from_sums(a.sum(axis=0), view)
            yh = hl.hull_value(hull, float(xa))
            yc = hl.chord_value((float(x3), float(y3)), (float(x2), float(y2)), float(xa))
            xab, yab = hl.xy_from_sums(a[idx].sum(axis=1), view)
            d_h = np.full(n_boot, np.nan)
            d_c = np.full(n_boot, np.nan)
            for b in range(n_boot):
                yhb = hl.hull_value(hulls[b], float(xab[b]))
                if math.isfinite(yhb):
                    d_h[b] = 100.0 * (yab[b] - yhb)
                ycb = hl.chord_value((float(x3b[b]), float(y3b[b])), (float(x2b[b]), float(y2b[b])), float(xab[b]))
                if math.isfinite(ycb):
                    d_c[b] = 100.0 * (yab[b] - ycb)
            out.setdefault(cfg, {})[view] = dict(
                h=h_target, x=float(xa), y_adaptiv=float(ya), y_huelle=float(yh), delta_pp=100.0 * (float(ya) - yh),
                delta_ci=[float(np.nanpercentile(d_h, 2.5)), float(np.nanpercentile(d_h, 97.5))],
                x_2=float(x2), y_2=float(y2), x_3=float(x3), y_3=float(y3), y_sehne=float(yc),
                delta_sehne_pp=100.0 * (float(ya) - yc),
                delta_sehne_ci=[float(np.nanpercentile(d_c, 2.5)), float(np.nanpercentile(d_c, 97.5))],
                sehne_innerhalb=bool(min(x2, x3) <= xa <= max(x2, x3)), n_boot=int(n_boot),
                boot_nan=int(np.isnan(d_h).sum()), huelle=[list(p) for p in hull])
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Rechnung über alle h
# ─────────────────────────────────────────────────────────────────────────────

def files(roc_dir: Path) -> Tuple[Path, Path, Path]:
    d = Path(roc_dir) / SUBDIR
    return d / CSV_NAME, d / H1_NAME, d / PAT_NAME


def available(roc_dir: Path) -> bool:
    c, h1, _ = files(roc_dir)
    return c.exists() and h1.exists()


def _k_patients(roc_dir: Path) -> Dict[str, Dict[str, Dict[str, List[float]]]]:
    """Patientensummen der festen Regeln je h aus ``roc_patienten.json`` (von ``run_roc``), sonst leer."""
    p = Path(roc_dir) / rr.PATIENTS_JSON
    if not p.exists():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))
    return {r: v for r, v in d.get("daten", {}).items() if r in K_RULES}


def compute(roc_dir: Path, cfgs: Optional[Sequence[Config]] = None, n_boot: int = 1000, seed: int = 7,
            grid_phase: float = 0.5, use_cache: bool = True, log=print) -> dict:
    roc_dir = Path(roc_dir)
    cfgs = list(cfgs or configs())
    contract = dc.load_contract()
    eta = ETA_OF_DIR.get(roc_dir.name, roc_dir.name)
    t0 = time.perf_counter()
    rows: List[dict] = []
    cfg_patients: Dict[str, Dict[str, Dict[str, List[float]]]] = {}
    k_patients: Dict[str, Dict[str, Dict[str, List[float]]]] = {r: {} for r in K_RULES}
    wall_replay = 0.0
    n_new = 0
    for h, folder in jn.h_folders(roc_dir):
        th = time.perf_counter()
        j = jn.load_journals(folder)
        truth = mt.truth_from_journals(j)
        src = mt.source_intervals(j)
        entries = mt.crisis_entries(src, truth)
        hk = f"{h:g}"
        for k in (1, 2, 3, 4):
            k_patients[f"{k}-aus-4"][hk] = mt.per_patient(mt.k_of_n_rot(src, j.patients, k), truth, j.hours)
        parts = []
        for (T, a, d) in cfgs:
            ck = key(T, a, d)
            rot, meta = replay_config(j, contract, T, a, d, grid_phase, roc_dir / rr.CACHE, use_cache)
            ivs = rr.intervals(rot)
            r = mt.rate(ivs, truth, j.bed_hours)
            lat = mt.latencies(ivs, truth, entries)
            cfg_patients.setdefault(ck, {})[hk] = mt.per_patient(ivs, truth, j.hours)
            row = dict(eta=eta, h=h, konfig=ck, T=T, alpha=a, delta_min=d)
            row.update(rr.rate_dict(r, lat))
            row.update(n_rot=meta.get("n_rot"), bett_h=j.bed_hours, wall_s=meta.get("wall_s"))
            rows.append(row)
            wall_replay += float(meta.get("wall_s") or 0.0)
            parts.append(f"{ck}: erk {r.detected:.3f}/{r.detected_strict:.3f} falsch {r.false_per_bed_h:.2f}")
        log(f"[{eta}] h = {h:<5g} {' | '.join(parts[:3])} … [{time.perf_counter() - th:.0f} s]")
    hs = sorted({r["h"] for r in rows})
    log(f"[{eta}] Δ bei h = 1 (Bootstrap {n_boot} Züge) …")
    tb = time.perf_counter()
    h1 = deltas_h1(cfg_patients, k_patients, n_boot=n_boot, seed=seed)
    meta = dict(eta=eta, roc_dir=str(roc_dir), konfigurationen=[key(*c) for c in cfgs],
                parameter=dict(T=list(TS), alpha=list(ALPHAS), delta_min=list(DMINS), basis=list(BASE)),
                hs=hs, n_boot=n_boot, seed=seed, grid_phase=grid_phase, rechenzeit_s=round(time.perf_counter() - t0, 1),
                replay_wall_s=round(wall_replay, 1), bootstrap_s=round(time.perf_counter() - tb, 1),
                erstellt=datetime.now().isoformat(timespec="seconds"))
    write(roc_dir, rows, h1, meta, cfg_patients)
    log(f"[{eta}] fertig: {len(rows)} Zeilen, {len(h1)} Konfigurationen mit Δ; {meta['rechenzeit_s']} s "
        f"(Replays zusammen {meta['replay_wall_s']} s laut Zwischenspeicher, Bootstrap {meta['bootstrap_s']} s)")
    return dict(rows=rows, h1=h1, meta=meta)


def recompute_deltas(roc_dir: Path, n_boot: int = 1000, seed: int = 7, log=print) -> dict:
    """Nur Δ/Bootstrap aus den gespeicherten Patientensummen (ohne Replays)."""
    roc_dir = Path(roc_dir)
    c_path, h1_path, pat_path = files(roc_dir)
    data = load(roc_dir)
    if data is None or not pat_path.exists():
        raise FileNotFoundError(f"{roc_dir}: werkstatt.csv / werkstatt_patienten.json fehlen")
    pat = json.loads(pat_path.read_text(encoding="utf-8"))
    cfg_patients = pat["konfigurationen"]
    k_patients = pat.get("k_aus_4") or _k_patients(roc_dir)
    t0 = time.perf_counter()
    h1 = deltas_h1(cfg_patients, k_patients, n_boot=n_boot, seed=seed)
    meta = dict(data["meta"])
    meta.update(n_boot=n_boot, seed=seed, bootstrap_s=round(time.perf_counter() - t0, 1),
                erstellt=datetime.now().isoformat(timespec="seconds"))
    write(roc_dir, data["rows"], h1, meta, cfg_patients, k_patients)
    log(f"[{meta.get('eta')}] Δ neu: {len(h1)} Konfigurationen, {meta['bootstrap_s']} s")
    return dict(rows=data["rows"], h1=h1, meta=meta)


def write(roc_dir: Path, rows: List[dict], h1: Dict[str, dict], meta: dict,
          cfg_patients: Dict[str, Dict[str, Dict[str, List[float]]]],
          k_patients: Optional[Dict[str, Dict[str, Dict[str, List[float]]]]] = None) -> None:
    c_path, h1_path, pat_path = files(roc_dir)
    c_path.parent.mkdir(parents=True, exist_ok=True)
    with open(c_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(CSV_FIELDS), delimiter=";", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: rr.fmt(r.get(k)) if k in NUMERIC else r.get(k, "") for k in CSV_FIELDS})
    h1_path.write_text(json.dumps(dict(meta=meta, deltas=h1), ensure_ascii=False, indent=1, default=float),
                       encoding="utf-8")
    pat = dict(felder=list(mt.PATIENT_FIELDS), konfigurationen=cfg_patients)
    if k_patients:
        pat["k_aus_4"] = k_patients
    pat_path.write_text(json.dumps(pat, ensure_ascii=False), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Lesen
# ─────────────────────────────────────────────────────────────────────────────

def _num(s: str) -> Optional[float]:
    if s is None or s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def load(roc_dir: Path) -> Optional[dict]:
    """rows (Zahlen als float, fehlende als None), h1 (Δ je Konfiguration und Sicht), meta; None, wenn nicht gerechnet."""
    c_path, h1_path, _ = files(roc_dir)
    if not c_path.exists() or not h1_path.exists():
        return None
    rows: List[dict] = []
    with open(c_path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            rows.append({k: (_num(v) if k in NUMERIC else v) for k, v in r.items()})
    d = json.loads(h1_path.read_text(encoding="utf-8"))
    return dict(rows=rows, h1=d.get("deltas", {}), meta=d.get("meta", {}))


def configs_in(rows: Sequence[dict]) -> List[Config]:
    seen: List[Config] = []
    for r in rows:
        c = (float(r["T"]), float(r["alpha"]), float(r["delta_min"]))
        if c not in seen:
            seen.append(c)
    return seen


def values_of(rows: Sequence[dict], param: str) -> List[float]:
    """Vorhandene Werte eines Parameters bei Basis der beiden anderen."""
    others = {n: b for n, b in zip(("T", "alpha", "delta_min"), BASE) if n != param}
    vals = sorted({float(r[param]) for r in rows if all(_same(float(r[o]), b) for o, b in others.items())})
    return vals


def rows_for(rows: Sequence[dict], T: float, alpha: float, delta_min: float) -> List[dict]:
    sel = [r for r in rows if _same(float(r["T"]), T) and _same(float(r["alpha"]), alpha)
           and _same(float(r["delta_min"]), delta_min) and r.get("h") is not None]
    return sorted(sel, key=lambda r: r["h"])


def h1_table(data: dict) -> List[dict]:
    """Eine Zeile je Konfiguration bei h = 1 (für Bericht und Reiter)."""
    out: List[dict] = []
    for r in rows_at(data["rows"], H_TARGET):
        ck = r["konfig"]
        d = data["h1"].get(ck, {})
        st = d.get("stueck", {})
        zt = d.get("zeit", {})
        out.append(dict(konfig=ck, T=r["T"], alpha=r["alpha"], delta_min=r["delta_min"], label=label(r["T"], r["alpha"], r["delta_min"], short=True),
                        falsch_je_bett_h=r["falsch_je_bett_h"], erkannt=r["erkannt"], erkannt_streng=r["erkannt_streng"],
                        verz_kreuz_median_s=r["verz_kreuz_median_s"], verz_kreuz_p90_s=r["verz_kreuz_p90_s"],
                        tpr=r["tpr"], fpr_ii=r["fpr_ii"], hvl_anteil=r["hvl_anteil"],
                        delta_sehne_pp=st.get("delta_sehne_pp"), delta_sehne_ci=st.get("delta_sehne_ci"),
                        delta_pp=st.get("delta_pp"), delta_ci=st.get("delta_ci"),
                        delta_zeit_pp=zt.get("delta_pp"), delta_zeit_ci=zt.get("delta_ci"),
                        delta_zeit_sehne_pp=zt.get("delta_sehne_pp"), delta_zeit_sehne_ci=zt.get("delta_sehne_ci")))
    return out


def rows_at(rows: Sequence[dict], h: float) -> List[dict]:
    return [r for r in rows if r.get("h") is not None and _same(float(r["h"]), h)]


def above_hull(data: dict, view: str = "stueck", tol_pp: float = 1.0) -> List[dict]:
    """Konfigurationen, deren adaptiver Punkt bei h = 1 über (Δ > tol), auf (|Δ| ≤ tol) oder unter der Hülle liegt."""
    out = []
    for ck, d in data["h1"].items():
        v = d.get(view)
        if not v:
            continue
        dp = v["delta_pp"]
        lage = "über" if dp > tol_pp else ("auf" if abs(dp) <= tol_pp else "unter")
        out.append(dict(konfig=ck, delta_pp=dp, delta_ci=v["delta_ci"], lage=lage, x=v["x"]))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ROC-Werkstatt: adaptiv-Konfigurationen (T, α, δ_min) offline nachrechnen.")
    ap.add_argument("--roc-dir", type=Path, nargs="+", default=[rr.ROC_DIR, rr.ROC_DIR_P07])
    ap.add_argument("--n-boot", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--grid-phase", type=float, default=0.5)
    ap.add_argument("--ohne-cache", action="store_true")
    ap.add_argument("--nur-deltas", action="store_true", help="nur Δ/Bootstrap aus gespeicherten Patientensummen")
    args = ap.parse_args(argv)
    for d in args.roc_dir:
        if args.nur_deltas:
            recompute_deltas(d, n_boot=args.n_boot, seed=args.seed, log=rr.log)
        else:
            compute(d, n_boot=args.n_boot, seed=args.seed, grid_phase=args.grid_phase, use_cache=not args.ohne_cache,
                    log=rr.log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
