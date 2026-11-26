"""Klassische ROC des Filters und Parameterwahl („kalibriert“) – offline mit dem unveränderten Dashboard-Kern.

Ereignisbezogene ROC
--------------------
Fehlalarm-Episode = Vereinigung überlappender Fehlalarme der Geräte eines Patienten (Fehlalarm = berührt keine Krise
± 10 s; ``metrics.false_episodes``).  Eine Episode gilt als eskaliert, wenn ein falscher Rot-Abschnitt der Regel sie
schneidet (``metrics.escalated_episodes``).  x = eskalierte / alle Fehlalarm-Episoden (%), y = erkannte Krisen
(mild: Rot berührt [Beginn, Ende + 10 s]; streng: [Beginn, Ende]) in %.  AUC nach der Trapezregel über die nach x
sortierten Punkte mit (0, 0) und (100, 100).

Klassische ROC des Filters
--------------------------
Gerätegrenzwerte fest (h = 1,0; zusätzlich 0,8 und 1,2); die Schwelle des adaptiven Filters wird über den
klinischen Kontext verschoben: ``base_prob_P0`` ∈ P0_GRID, Context_Log_Odds = ln(P0/(1 − P0)),
Θ_target = max(ε, Θ_base·δ − Context_Log_Odds) ((3.19)/(3.24); im Kern unverändert, P0 kommt aus der Datenbank
``clinical_db``).  k-aus-4 liefert bei festem h die vier Punkte k = 1 … 4 (Linienzug + Endpunkte).

Parameterwahl (Kriterium, vor der Rechnung festgelegt)
-------------------------------------------------------
siehe ``KRITERIUM``.  Lernszenario η = 0,9 (``logs/ziel3_roc``, Seed 2), h = 1,0; Gitter T × α × δ_min, je
Konfiguration die P0-Kurve; Zielgröße AUC (ereignisbezogen, mild) der adaptiven P0-Kurve; Nebenbedingung Median der
Latenz ab erstem Überschreiten ≤ 12 s bei P0 = 0,5; Gleichstand (± 0,005) → kleinere Latenz.  Prüfung ohne Nachjustage
auf η = 0,7 (P0- und h-Kurve), auf den Netzläufen (h = 1, offline mit den Journalen) und – falls vorhanden – auf
``logs/ziel3_roc_val09`` / ``ziel3_roc_val07``.

    .venv\\Scripts\\python -m analysis.roc_ziel3.kalibrierung --stufe klassisch   # Basis: P0-Kurven bei h 0,8/1,0/1,2, beide η
    .venv\\Scripts\\python -m analysis.roc_ziel3.kalibrierung --stufe gitter --T-werte 5 6 8   # Teil des Gitters (parallel)
    .venv\\Scripts\\python -m analysis.roc_ziel3.kalibrierung --stufe auswertung  # Gitter zusammenführen, kalibriert wählen
    .venv\\Scripts\\python -m analysis.roc_ziel3.kalibrierung --stufe pruefung    # kalibriert: η 0,7, h-Kurven, Netzläufe, Bootstrap
    .venv\\Scripts\\python -m analysis.roc_ziel3.kalibrierung --stufe episoden    # ereignisbezogene Zahlen für Werkstatt-Konfigurationen
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from . import dashcore as dc
from . import journals as jn
from . import metrics as mt
from . import run_roc as rr
from . import werkstatt as ws

Config = Tuple[float, float, float]
Interval = Tuple[float, float]

P0_GRID: Tuple[float, ...] = (0.001, 0.005, 0.02, 0.05, 0.1, 0.2, 0.35, 0.5, 0.65, 0.8, 0.9, 0.95, 0.98, 0.995, 0.999)
T_GRID: Tuple[float, ...] = (5.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0, 30.0)
A_GRID: Tuple[float, ...] = (0.3, 0.5, 0.7, 0.9)
D_GRID: Tuple[float, ...] = (0.3, 0.5, 0.7, 1.0)
H_KLASSISCH: Tuple[float, ...] = (0.8, 1.0, 1.2)
H_LERN = 1.0
P0_BASIS = 0.5
LATENZ_MAX_S = 12.0
TIE_AUC = 0.005
PROTOTYP: Config = ws.BASE
SUBDIR = "kalibrierung"
OUT_DIR = rr.ROC_DIR / SUBDIR                      # alle Ergebnisdateien der Kalibrierung (Lern- und Prüfdaten)
K_RULES: Tuple[str, ...] = ("1-aus-4", "2-aus-4", "3-aus-4", "4-aus-4")
NETZ_RUNS: Dict[str, Tuple[str, str]] = {          # Lauf → (Journale PC 2, Dashboard PC 1), relativ zu logs/
    "12h": ("ziel3_roc/netz_ziel3_12h", "ziel3_12h/pc1"),
    "A": ("ziel3_roc_p07/netz_ziel3_4h_p07", "ziel3_4h_p07/pc1"),
    "B": ("ziel3_4h_p08_pc2/pc2", "ziel3_4h_p08/pc1"),
    "C": ("ziel3_4h_lang_pc2/pc2", "ziel3_4h_lang/pc1"),
    "D": ("ziel3_6h_ideal_pc2/pc2", "ziel3_6h_ideal/pc1"),
}
VAL_DIRS: Dict[str, Path] = {"0,9": rr.LOGS / "ziel3_roc_val09", "0,7": rr.LOGS / "ziel3_roc_val07"}
VAL_ETA: Dict[str, str] = {"ziel3_roc_val09": "val 0,9", "ziel3_roc_val07": "val 0,7"}   # Ordnername → η-Schlüssel der Prüfdaten
VAL_SEED: Dict[str, int] = {"val 0,9": 8, "val 0,7": 9}
VAL_ROWS_PREFIX = "roc_alle_"                     # kalibrierung/val/roc_alle_<Ordner>.csv: Zeilen im Schema von roc_alle.csv

KRITERIUM = (
    "Lernszenario: η = 0,9 (experiments/ziel3_simulation/logs/ziel3_roc, Seed 2), h = 1,0. "
    "Gitter: T ∈ {5; 6; 8; 10; 12; 15; 20; 30} s × α ∈ {0,3; 0,5; 0,7; 0,9} × δ_min ∈ {0,3; 0,5; 0,7; 1,0}; je Konfiguration "
    "die Kurve über P0 ∈ {0,001 … 0,999} (Context_Log_Odds = ln(P0/(1 − P0))). "
    "Zielgröße: AUC (ereignisbezogen: x = eskalierte Fehlalarm-Episoden in %, y = erkannte Krisen mild in %, Trapezregel mit "
    "(0, 0) und (100, 100)) der adaptiven P0-Kurve. Nebenbedingung: Median der Latenz ab dem ersten Überschreiten einer "
    "Gerätegrenze ≤ 12 s bei P0 = 0,5. Konfigurationen, die die Nebenbedingung verletzen, werden gerechnet und gezeigt, aber "
    "nicht gewählt. Gleichstand (AUC innerhalb ± 0,005): kleinere Latenz. Die gewählte Konfiguration heißt „kalibriert“, "
    "T = 10 s / α = 0,5 / δ_min = 0,5 / P0 = 0,5 „Prototyp“. Nicht variiert: Δ_eval (Takt der periodischen Auswertung, 1 s; "
    "Konstante des Dashboards), die Abbildung der BICEPS-Prioritäten auf P_j (durch die Norm vorgegeben), das Geräteprofil "
    "TPR/FPR (bei gleichen Profilen kürzen sich die Gewichte in k_min·w̄ heraus, Anhang A.5; geprüft in der ROC-Werkstatt), "
    "die Konstanten des Kerns (TTL-Cache 10 s, Gnadenfrist 10 s, größter Zeitschritt 20 s). Prüfung ohne Nachjustage: "
    "(a) η = 0,7 (logs/ziel3_roc_p07, Seed 4): P0-Kurve bei h = 1 und h-Kurve bei P0 = 0,5; (b) Netzläufe 12 h, A, B, C, D "
    "offline bei h = 1 auf ihren Alarmjournalen gegen 2-/3-aus-4; (c) logs/ziel3_roc_val09 / val07, falls vorhanden."
)

CSV_FIELDS: Tuple[str, ...] = (
    "eta", "h", "konfig", "T", "alpha", "delta_min", "P0", "clo", "regel", "ep_gesamt", "ep_eskaliert", "ep_anteil", "tpr",
    "fpr", "fpr_ii", "erkannt", "erkannt_streng", "n_krisen", "n_erkannt", "n_erkannt_streng", "n_ereignisse", "n_falsch",
    "falsch_je_bett_h", "verz_kreuz_median_s", "verz_kreuz_p90_s", "verz_median_s", "verz_p90_s", "n_rot", "bett_h", "wall_s",
)
PAT_FIELDS: Tuple[str, ...] = ("ep_gesamt", "ep_eskaliert", "n_krisen", "n_erkannt", "n_erkannt_streng", "n_falsch", "bett_h",
                               "t_krise", "t_in_krise", "t_stabil", "t_falsch")


# ─────────────────────────────────────────────────────────────────────────────
# Hilfen
# ─────────────────────────────────────────────────────────────────────────────

def clo(p0: float) -> float:
    return math.log(p0 / (1.0 - p0))


def key(T: float, a: float, d: float) -> str:
    return ws.key(T, a, d)


def grid_configs() -> List[Config]:
    return [(float(T), float(a), float(d)) for T in T_GRID for a in A_GRID for d in D_GRID]


def auc_points(points: Iterable[Tuple[float, float]]) -> float:
    """AUC (0 … 1) der Trapezregel über (x %, y %) mit den Endpunkten (0, 0) und (100, 100); nach x, dann y sortiert."""
    pts = sorted({(0.0, 0.0), (100.0, 100.0)} | {(float(x), float(y)) for x, y in points if math.isfinite(x) and math.isfinite(y)})
    x = np.array([p[0] for p in pts]) / 100.0
    y = np.array([p[1] for p in pts]) / 100.0
    return float(np.sum(np.diff(x) * (y[1:] + y[:-1]) / 2.0))


def _csv(stem: Path) -> Path:
    """<stem>.csv – nicht ``with_suffix`` (Konfigurationsnamen enthalten Punkte: T10_a0.5_d0.5)."""
    return stem.parent / (stem.name + ".csv")


def _json(stem: Path) -> Path:
    return stem.parent / (stem.name + ".json")


def _num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Kontext eines Laufordners (Journale, Wahrheit, Episoden)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Context:
    folder: Path
    eta: str
    h: float
    j: jn.Journals
    truth: Dict[str, mt.PatientTruth]
    src: Dict[str, Dict[str, List[Interval]]]
    entries: List[mt.CrisisEntry]
    episodes: List[mt.FalseEpisode]

    @property
    def bed_h(self) -> float:
        return self.j.bed_hours


def load_context(folder: Path, eta: str, h: Optional[float] = None) -> Context:
    j = jn.load_journals(folder)
    truth = mt.truth_from_journals(j)
    src = mt.source_intervals(j)
    hh = float(j.threshold_scale) if j.threshold_scale is not None else (h if h is not None else 1.0)
    return Context(folder=Path(folder), eta=eta, h=hh, j=j, truth=truth, src=src, entries=mt.crisis_entries(src, truth),
                   episodes=mt.false_episodes(src, truth))


def h_folder(roc_dir: Path, h: float) -> Path:
    for hh, f in jn.h_folders(roc_dir):
        if abs(hh - h) < 1e-9:
            return f
    raise FileNotFoundError(f"{roc_dir}: kein Ordner für h = {h:g}")


def eta_of(roc_dir: Path) -> str:
    name = Path(roc_dir).name
    return VAL_ETA.get(name, ws.ETA_OF_DIR.get(name, name))


# ─────────────────────────────────────────────────────────────────────────────
# Replay mit P0 (Zwischenspeicher)
# ─────────────────────────────────────────────────────────────────────────────

def cache_path(cache_dir: Path, folder_name: str, raw: dict, contract: dict, delta_min: float, p0: float) -> Path:
    if abs(p0 - P0_BASIS) < 1e-12:
        return ws.cache_path(cache_dir, folder_name, raw, contract, delta_min)
    T = dc.horizon_T(raw)
    alpha = float(raw.get("filter_params", {}).get("alpha", 0.5))
    return cache_dir / f"{folder_name}_T{T:g}_a{alpha:g}_d{delta_min:g}_P{p0:g}.json"


def replay(ctx: Context, contract: dict, cfg: Config, p0: float, grid_phase: float = 0.5,
           cache_dir: Optional[Path] = None, use_cache: bool = True) -> Tuple[Dict[str, List[Interval]], dict]:
    T, a, d = cfg
    cache_dir = cache_dir if cache_dir is not None else ctx.folder.parent / rr.CACHE
    if abs(p0 - P0_BASIS) < 1e-12:
        rot, meta = ws.replay_config(ctx.j, contract, T, a, d, grid_phase, cache_dir, use_cache)
        return rr.intervals(rot), meta
    raw = dc.build_raw_clinical_db(contract, horizon_T=T, alpha=a, base_prob_P0=p0)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_path(cache_dir, ctx.folder.name, raw, contract, d, p0)
    k = dict(grid_phase=grid_phase, filter_params=raw.get("filter_params"), profile=dc._first_profile(raw), delta_min=d,
             base_prob_P0=p0, n_alarms=len(ctx.j.alarms), t_start=ctx.j.t_start, t_end=ctx.j.t_end)
    if use_cache and path.exists():
        try:
            dd = json.loads(path.read_text(encoding="utf-8"))
            if dd.get("key") == k:
                return {p: [tuple(x[:2]) for x in v] for p, v in dd["rot"].items()}, dd["meta"]
        except (json.JSONDecodeError, KeyError):
            pass
    rep = dc.AdaptivReplay(dc.channels_from_journals(ctx.j, contract), raw, room=contract.get("room", "ICU-3"),
                           grid_phase=grid_phase, delta_min=None if abs(d - ws.BASE[2]) < 1e-9 else d)
    res = rep.run(ctx.j.alarms, ctx.j.t_start, ctx.j.t_end)
    meta = dict(n_rot=res.n_rot, n_events=res.n_events, n_grid=res.n_grid, n_gc=res.n_gc, n_updates=res.n_updates,
                wall_s=round(res.wall_s, 1), params=res.params)
    path.write_text(json.dumps(dict(key=k, meta=meta, rot=res.rot), ensure_ascii=False), encoding="utf-8")
    return rr.intervals(res.rot), meta


# ─────────────────────────────────────────────────────────────────────────────
# Kennzahlen eines Rot-Satzes
# ─────────────────────────────────────────────────────────────────────────────

def evaluate(ctx: Context, rot: Dict[str, Sequence[Interval]]) -> Tuple[dict, Dict[str, List[float]]]:
    """Kennzahlen (Zeit, Stück, Ereignisse, Latenz) und Patientensummen (PAT_FIELDS)."""
    r = mt.rate(rot, ctx.truth, ctx.bed_h)
    lat = mt.latencies(rot, ctx.truth, ctx.entries)
    n_ep, n_esc, per_ep = mt.escalated_episodes(rot, ctx.episodes, ctx.truth)
    per = mt.per_patient(rot, ctx.truth, ctx.j.hours)
    pat: Dict[str, List[float]] = {}
    F = {name: i for i, name in enumerate(mt.PATIENT_FIELDS)}
    for p, vals in per.items():
        ep_t, ep_e = per_ep.get(p, (0, 0))
        pat[p] = [float(ep_t), float(ep_e), vals[F["n_crises"]], vals[F["n_det"]], vals[F["n_det_strict"]], vals[F["n_false"]],
                  vals[F["bed_h"]], vals[F["t_crisis"]], vals[F["t_in_crisis"]], vals[F["t_stable"]], vals[F["t_false"]]]
    row = dict(ep_gesamt=n_ep, ep_eskaliert=n_esc, ep_anteil=(n_esc / n_ep if n_ep else float("nan")), tpr=r.tpr, fpr=r.fpr,
               fpr_ii=r.fpr_ii, erkannt=r.detected, erkannt_streng=r.detected_strict, n_krisen=r.n_crises, n_erkannt=r.n_detected,
               n_erkannt_streng=r.n_detected_strict, n_ereignisse=r.n_events, n_falsch=r.n_false, falsch_je_bett_h=r.false_per_bed_h,
               verz_kreuz_median_s=lat.median("cross"), verz_kreuz_p90_s=lat.p90("cross"), verz_median_s=r.delay_median,
               verz_p90_s=r.delay_p90, bett_h=ctx.bed_h)
    return row, pat


def k_rule_rows(ctx: Context) -> Tuple[List[dict], Dict[str, Dict[str, List[float]]]]:
    rows, pats = [], {}
    for k in (1, 2, 3, 4):
        name = f"{k}-aus-4"
        rot = mt.k_of_n_rot(ctx.src, ctx.j.patients, k)
        row, pat = evaluate(ctx, rot)
        row.update(eta=ctx.eta, h=ctx.h, konfig=name, T=None, alpha=None, delta_min=None, P0=None, clo=None, regel=name,
                   n_rot=row["n_ereignisse"], wall_s=None)
        rows.append(row)
        pats[name] = pat
    return rows, pats


def adaptiv_row(ctx: Context, cfg: Config, p0: float, rot: Dict[str, Sequence[Interval]], meta: dict) -> Tuple[dict, Dict[str, List[float]]]:
    row, pat = evaluate(ctx, rot)
    T, a, d = cfg
    row.update(eta=ctx.eta, h=ctx.h, konfig=key(T, a, d), T=T, alpha=a, delta_min=d, P0=p0, clo=clo(p0), regel="adaptiv",
               n_rot=meta.get("n_rot"), wall_s=meta.get("wall_s"))
    return row, pat


# ─────────────────────────────────────────────────────────────────────────────
# Dateien
# ─────────────────────────────────────────────────────────────────────────────

def write_rows(path: Path, rows: Sequence[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(CSV_FIELDS), delimiter=";", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: rr.fmt(r.get(k)) if k not in ("eta", "konfig", "regel") else r.get(k, "") for k in CSV_FIELDS})


def read_rows(path: Path) -> List[dict]:
    if not Path(path).exists():
        return []
    out: List[dict] = []
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            out.append({k: (v if k in ("eta", "konfig", "regel") else _num(v)) for k, v in r.items()})
    return out


def pat_key(eta: str, h: float, regel: str, p0: Optional[float]) -> str:
    return f"{eta}|{h:g}|{regel}|{'' if p0 is None else f'{p0:g}'}"


def write_pat(path: Path, pats: Dict[str, Dict[str, List[float]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(felder=list(PAT_FIELDS), daten=pats), ensure_ascii=False), encoding="utf-8")


def read_pat(path: Path) -> Dict[str, Dict[str, List[float]]]:
    if not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8")).get("daten", {})


# ─────────────────────────────────────────────────────────────────────────────
# Stufen
# ─────────────────────────────────────────────────────────────────────────────

def sweep_p0(ctx: Context, contract: dict, cfg: Config, p0s: Sequence[float], log=print, use_cache: bool = True,
             cache_dir: Optional[Path] = None) -> Tuple[List[dict], Dict[str, Dict[str, List[float]]]]:
    rows, pats = [], {}
    for p0 in p0s:
        t0 = time.perf_counter()
        rot, meta = replay(ctx, contract, cfg, p0, cache_dir=cache_dir, use_cache=use_cache)
        row, pat = adaptiv_row(ctx, cfg, p0, rot, meta)
        rows.append(row)
        pats[pat_key(ctx.eta, ctx.h, key(*cfg), p0)] = pat
        log(f"[{ctx.eta}] h = {ctx.h:g} {key(*cfg)} P0 = {p0:<6g} erk {row['erkannt']:.3f} ep {row['ep_anteil']:.3f} "
            f"falsch {row['falsch_je_bett_h']:.2f} Rot {row['n_rot']} [{time.perf_counter() - t0:.0f} s]")
    return rows, pats


def stufe_klassisch(roc_dirs: Sequence[Path], cfg: Config = PROTOTYP, hs: Sequence[float] = H_KLASSISCH,
                    p0s: Sequence[float] = P0_GRID, out_dir: Path = OUT_DIR, log=print, use_cache: bool = True) -> Path:
    """P0-Kurven von ``cfg`` bei den Grenzwerten ``hs`` je ROC-Verzeichnis + k-aus-4-Punkte → klassisch_<key>.csv/.json."""
    contract = dc.load_contract()
    rows: List[dict] = []
    pats: Dict[str, Dict[str, List[float]]] = {}
    t_all = time.perf_counter()
    for roc_dir in roc_dirs:
        eta = eta_of(roc_dir)
        for h in hs:
            ctx = load_context(h_folder(roc_dir, h), eta, h)
            kr, kp = k_rule_rows(ctx)
            rows += kr
            for name, pat in kp.items():
                pats[pat_key(eta, h, name, None)] = pat
            r2, p2 = sweep_p0(ctx, contract, cfg, p0s, log, use_cache, roc_dir / rr.CACHE)
            rows += r2
            pats.update(p2)
    stem = out_dir / f"klassisch_{key(*cfg)}"
    merged_rows = _merge_rows(read_rows(_csv(stem)), rows)
    write_rows(_csv(stem), merged_rows)
    old_p = read_pat(_json(stem))
    old_p.update(pats)
    write_pat(_json(stem), old_p)
    log(f"klassisch {key(*cfg)}: {len(rows)} Zeilen, {time.perf_counter() - t_all:.0f} s → {stem}.csv/.json")
    return _csv(stem)


def _merge_rows(old: List[dict], new: List[dict]) -> List[dict]:
    """Neue Zeilen ersetzen alte mit gleichem (eta, h, regel/konfig, P0)."""
    def k(r):
        return (r.get("eta"), f"{(r.get('h') or 0):g}", r.get("konfig"), f"{r['P0']:g}" if r.get("P0") is not None else "")
    d = {k(r): r for r in old}
    for r in new:
        d[k(r)] = r
    return list(d.values())


def stufe_gitter(roc_dir: Path = rr.ROC_DIR, h: float = H_LERN, Ts: Sequence[float] = T_GRID, p0s: Sequence[float] = P0_GRID,
                 out_dir: Path = OUT_DIR, teil: str = "", log=print, use_cache: bool = True) -> Path:
    """Gitter T × α × δ_min bei h (Lernszenario), je Konfiguration die P0-Kurve → gitter<teil>.csv/.json."""
    contract = dc.load_contract()
    ctx = load_context(h_folder(roc_dir, h), eta_of(roc_dir), h)
    rows: List[dict] = []
    pats: Dict[str, Dict[str, List[float]]] = {}
    stem = out_dir / f"gitter{teil}"
    done = {(r["konfig"], f"{r['P0']:g}") for r in read_rows(_csv(stem)) if r.get("P0") is not None}
    rows = read_rows(_csv(stem))
    pats = read_pat(_json(stem))
    t_all = time.perf_counter()
    cfgs = [(float(T), a, d) for T in Ts for a in A_GRID for d in D_GRID]
    for i, cfg in enumerate(cfgs):
        todo = [p0 for p0 in p0s if (key(*cfg), f"{p0:g}") not in done]
        if not todo:
            continue
        r2, p2 = sweep_p0(ctx, contract, cfg, todo, log, use_cache, roc_dir / rr.CACHE)
        rows += r2
        pats.update(p2)
        write_rows(_csv(stem), rows)          # Zwischenstand sichern
        write_pat(_json(stem), pats)
        log(f"gitter{teil}: Konfiguration {i + 1}/{len(cfgs)} {key(*cfg)} fertig; {time.perf_counter() - t_all:.0f} s")
    return _csv(stem)


def latenz_p05(rows: Sequence[dict], konfig: str) -> Optional[float]:
    for r in rows:
        if r.get("konfig") == konfig and r.get("P0") is not None and abs(r["P0"] - P0_BASIS) < 1e-12:
            return r.get("verz_kreuz_median_s")
    return None


def auswertung_gitter(rows: Sequence[dict], mode: str = "mild") -> List[dict]:
    """Je Konfiguration: AUC der P0-Kurve (ereignisbezogen), Latenz bei P0 = 0,5, Nebenbedingung, Zahl der Punkte."""
    ykey = "erkannt" if mode == "mild" else "erkannt_streng"
    by: Dict[str, List[dict]] = {}
    for r in rows:
        if r.get("regel") == "adaptiv" and r.get("P0") is not None:
            by.setdefault(r["konfig"], []).append(r)
    out = []
    for konfig, rs in by.items():
        pts = [(100 * r["ep_anteil"], 100 * r[ykey]) for r in rs if r.get("ep_anteil") is not None and r.get(ykey) is not None]
        T, a, d = ws.parse_key(konfig)
        lat = latenz_p05(rs, konfig)
        r05 = next((r for r in rs if abs(r["P0"] - P0_BASIS) < 1e-12), None)
        out.append(dict(konfig=konfig, T=T, alpha=a, delta_min=d, n_punkte=len(pts), auc=auc_points(pts),
                        auc_zeit=auc_points([(100 * r["fpr_ii"], 100 * r["tpr"]) for r in rs if r.get("fpr_ii") is not None]),
                        latenz_median_s=lat, nebenbedingung_ok=(lat is not None and lat <= LATENZ_MAX_S),
                        falsch_je_bett_h_p05=(r05 or {}).get("falsch_je_bett_h"), erkannt_p05=(r05 or {}).get("erkannt"),
                        erkannt_streng_p05=(r05 or {}).get("erkannt_streng"), ep_anteil_p05=(r05 or {}).get("ep_anteil"),
                        vollstaendig=(len(pts) == len(P0_GRID))))
    return out


def waehle_kalibriert(tab: Sequence[dict]) -> Optional[dict]:
    """Kriterium: größte AUC unter den vollständigen Konfigurationen mit Latenz ≤ 12 s; Gleichstand ± 0,005 → kleinere Latenz."""
    ok = [t for t in tab if t["nebenbedingung_ok"] and t["vollstaendig"] and math.isfinite(t["auc"])]
    if not ok:
        return None
    best_auc = max(t["auc"] for t in ok)
    cands = [t for t in ok if t["auc"] >= best_auc - TIE_AUC]
    cands.sort(key=lambda t: (t["latenz_median_s"], -t["auc"], t["T"], t["alpha"], t["delta_min"]))
    return cands[0]


def stufe_auswertung(out_dir: Path = OUT_DIR, log=print) -> dict:
    """Gitterteile zusammenführen, Tabelle schreiben, kalibriert wählen → gitter_auswertung.csv, kalibriert.json."""
    rows: List[dict] = []
    pats: Dict[str, Dict[str, List[float]]] = {}
    for p in sorted(Path(out_dir).glob("gitter*.csv")):
        if p.name == "gitter_auswertung.csv":
            continue
        rows = _merge_rows(rows, read_rows(p))
        pats.update(read_pat(_json(p.with_name(p.stem))))
    tab = auswertung_gitter(rows, "mild")
    tab_streng = {t["konfig"]: t["auc"] for t in auswertung_gitter(rows, "streng")}
    for t in tab:
        t["auc_streng"] = tab_streng.get(t["konfig"])
    tab.sort(key=lambda t: (-t["auc"] if math.isfinite(t["auc"]) else 1e9, t["latenz_median_s"] or 1e9))
    best = waehle_kalibriert(tab)
    proto = next((t for t in tab if t["konfig"] == key(*PROTOTYP)), None)
    path = Path(out_dir) / "gitter_auswertung.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        cols = ["konfig", "T", "alpha", "delta_min", "n_punkte", "vollstaendig", "auc", "auc_streng", "auc_zeit", "latenz_median_s",
                "nebenbedingung_ok", "falsch_je_bett_h_p05", "erkannt_p05", "erkannt_streng_p05", "ep_anteil_p05", "gewaehlt"]
        w = csv.DictWriter(fh, fieldnames=cols, delimiter=";", extrasaction="ignore")
        w.writeheader()
        for t in tab:
            w.writerow({**{k: rr.fmt(v) if isinstance(v, float) else v for k, v in t.items()},
                        "gewaehlt": "kalibriert" if best and t["konfig"] == best["konfig"] else ("Prototyp" if proto and t["konfig"] == proto["konfig"] else "")})
    kal = dict(kriterium=KRITERIUM, lernszenario=dict(roc_dir=str(rr.ROC_DIR), eta="0,9", seed=2, h=H_LERN),
               gitter=dict(T=list(T_GRID), alpha=list(A_GRID), delta_min=list(D_GRID), P0=list(P0_GRID)),
               n_konfigurationen=len(tab), n_vollstaendig=sum(1 for t in tab if t["vollstaendig"]),
               n_nebenbedingung_ok=sum(1 for t in tab if t["nebenbedingung_ok"]),
               kalibriert=best, prototyp=proto, erstellt=datetime.now().isoformat(timespec="seconds"))
    (Path(out_dir) / "kalibriert.json").write_text(json.dumps(kal, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    write_pat(Path(out_dir) / "gitter_patienten.json", pats)
    log(f"Gitter: {len(tab)} Konfigurationen ({kal['n_vollstaendig']} vollständig, {kal['n_nebenbedingung_ok']} mit Latenz ≤ 12 s); "
        f"kalibriert = {best['konfig'] if best else '–'} (AUC {best['auc']:.4f}, Latenz {best['latenz_median_s']:.1f} s)" if best else "kein Kandidat")
    return kal


def kalibriert_config(out_dir: Path = OUT_DIR) -> Optional[Config]:
    p = Path(out_dir) / "kalibriert.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    k = (d.get("kalibriert") or {}).get("konfig")
    return ws.parse_key(k) if k else None


def stufe_hkurve(roc_dirs: Sequence[Path], cfg: Config, p0: float = P0_BASIS, out_dir: Path = OUT_DIR, log=print,
                 use_cache: bool = True) -> Path:
    """Kurve über alle h bei festem P0 für ``cfg`` (FROC-Ansichten) → hkurve_<key>.csv/.json."""
    contract = dc.load_contract()
    rows: List[dict] = []
    pats: Dict[str, Dict[str, List[float]]] = {}
    for roc_dir in roc_dirs:
        eta = eta_of(roc_dir)
        for h, folder in jn.h_folders(roc_dir):
            ctx = load_context(folder, eta, h)
            r2, p2 = sweep_p0(ctx, contract, cfg, [p0], log, use_cache, roc_dir / rr.CACHE)
            rows += r2
            pats.update(p2)
            kr, kp = k_rule_rows(ctx)
            rows += kr
            for name, pat in kp.items():
                pats[pat_key(eta, h, name, None)] = pat
    stem = out_dir / f"hkurve_{key(*cfg)}"
    write_rows(_csv(stem), _merge_rows(read_rows(_csv(stem)), rows))
    old_p = read_pat(_json(stem))
    old_p.update(pats)
    write_pat(_json(stem), old_p)
    return _csv(stem)


def stufe_netz(cfg: Config, out_dir: Path = OUT_DIR, log=print, use_cache: bool = True, runs: Dict[str, Tuple[str, str]] = NETZ_RUNS) -> Path:
    """Netzläufe offline bei h = 1 mit ``cfg`` (P0 = 0,5) auf ihren Journalen; dazu k-aus-4 und das wirkliche Dashboard."""
    contract = dc.load_contract()
    rows: List[dict] = []
    for run, (netz, pc1) in runs.items():
        netz_dir, pc1_dir = rr.LOGS / netz, rr.LOGS / pc1
        if not (netz_dir / "alarms.jsonl").exists():
            log(f"[{run}] Journale fehlen: {netz_dir}")
            continue
        ctx = load_context(netz_dir, run, 1.0)
        ctx.h = 1.0
        rot, meta = replay(ctx, contract, cfg, P0_BASIS, cache_dir=netz_dir.parent / rr.CACHE, use_cache=use_cache)
        row, _ = adaptiv_row(ctx, cfg, P0_BASIS, rot, meta)
        row["regel"] = "adaptiv"
        rows.append(row)
        kr, _ = k_rule_rows(ctx)
        rows += kr
        if (pc1_dir / "decisions.jsonl").exists():
            real = rr.intervals(rr.real_rot(pc1_dir, rr.clock_offset_fn(pc1_dir)))
            rrow, _ = evaluate(ctx, real)
            rrow.update(eta=run, h=1.0, konfig="Dashboard", T=PROTOTYP[0], alpha=PROTOTYP[1], delta_min=PROTOTYP[2], P0=P0_BASIS,
                        clo=0.0, regel="Dashboard", n_rot=rrow["n_ereignisse"], wall_s=None)
            rows.append(rrow)
        log(f"[{run}] {key(*cfg)}: falsch {row['falsch_je_bett_h']:.2f}/Bett·h erkannt {row['erkannt']:.3f}/{row['erkannt_streng']:.3f} "
            f"Latenz {row['verz_kreuz_median_s']:.1f} s; 2-aus-4 {kr[1]['falsch_je_bett_h']:.2f} / {kr[1]['erkannt']:.3f}, "
            f"3-aus-4 {kr[2]['falsch_je_bett_h']:.2f} / {kr[2]['erkannt']:.3f}")
    path = out_dir / f"netz_{key(*cfg)}.csv"
    write_rows(path, rows)
    return path


def stufe_episoden(roc_dirs: Sequence[Path], out_dir: Path = OUT_DIR, log=print) -> Path:
    """Ereignisbezogene Zahlen (eskalierte Fehlalarm-Episoden) für die Werkstatt-Konfigurationen (P0 = 0,5) und k-aus-4 je h,
    aus den vorhandenen Replays → episoden.csv/.json."""
    contract = dc.load_contract()
    rows: List[dict] = []
    pats: Dict[str, Dict[str, List[float]]] = {}
    for roc_dir in roc_dirs:
        eta = eta_of(roc_dir)
        werk = ws.load(roc_dir)
        cfgs = ws.configs_in(werk["rows"]) if werk else [PROTOTYP]
        for h, folder in jn.h_folders(roc_dir):
            ctx = load_context(folder, eta, h)
            kr, kp = k_rule_rows(ctx)
            rows += kr
            for name, pat in kp.items():
                pats[pat_key(eta, h, name, None)] = pat
            for cfg in cfgs:
                rot, meta = replay(ctx, contract, cfg, P0_BASIS, cache_dir=roc_dir / rr.CACHE)
                row, pat = adaptiv_row(ctx, cfg, P0_BASIS, rot, meta)
                rows.append(row)
                pats[pat_key(eta, h, key(*cfg), P0_BASIS)] = pat
            log(f"[{eta}] h = {h:g}: Episoden {kr[1]['ep_gesamt']}, eskaliert 2-aus-4 {kr[1]['ep_anteil']:.3f}, "
                f"3-aus-4 {kr[2]['ep_anteil']:.3f}, adaptiv {rows[-1]['ep_anteil']:.3f}")
    stem = out_dir / "episoden"
    write_rows(_csv(stem), _merge_rows(read_rows(_csv(stem)), rows))
    old_p = read_pat(_json(stem))
    old_p.update(pats)
    write_pat(_json(stem), old_p)
    return _csv(stem)


# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap: AUC adaptiv (P0-Kurve) gegen AUC des k-aus-4-Linienzugs
# ─────────────────────────────────────────────────────────────────────────────

def _xy_from_pat(sums: np.ndarray, mode: str = "mild", achse: str = "ereignis") -> Tuple[np.ndarray, np.ndarray]:
    F = {n: i for i, n in enumerate(PAT_FIELDS)}
    y = 100.0 * sums[..., F["n_erkannt" if mode == "mild" else "n_erkannt_streng"]] / sums[..., F["n_krisen"]]
    if achse == "ereignis":
        x = 100.0 * sums[..., F["ep_eskaliert"]] / sums[..., F["ep_gesamt"]]
    elif achse == "zeit":
        x = 100.0 * sums[..., F["t_falsch"]] / sums[..., F["t_stabil"]]
    else:
        x = sums[..., F["n_falsch"]] / sums[..., F["bett_h"]]
    return x, y


def auc_bootstrap(pats: Dict[str, Dict[str, List[float]]], eta: str, h: float, konfig: str, p0s: Sequence[float] = P0_GRID,
                  mode: str = "mild", n_boot: int = 1000, seed: int = 11) -> dict:
    """AUC (ereignisbezogen) der adaptiven P0-Kurve und des k-aus-4-Linienzugs mit 95-%-Intervallen und Differenz
    (gepaarter Bootstrap über die Patienten)."""
    keys_a = [pat_key(eta, h, konfig, p0) for p0 in p0s if pat_key(eta, h, konfig, p0) in pats]
    keys_k = [pat_key(eta, h, r, None) for r in K_RULES if pat_key(eta, h, r, None) in pats]
    if not keys_a or not keys_k:
        return {}
    patients = sorted(pats[keys_k[0]].keys())
    P = len(patients)
    A = np.array([[pats[k][p] for p in patients] for k in keys_a], dtype=float)      # (n_p0, P, F)
    K = np.array([[pats[k][p] for p in patients] for k in keys_k], dtype=float)
    xa, ya = _xy_from_pat(A.sum(axis=1), mode)
    xk, yk = _xy_from_pat(K.sum(axis=1), mode)
    auc_a = auc_points(zip(xa, ya))
    auc_k = auc_points(zip(xk, yk))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, P, size=(n_boot, P))
    da, dk = np.empty(n_boot), np.empty(n_boot)
    for b in range(n_boot):
        sa = A[:, idx[b], :].sum(axis=1)
        sk = K[:, idx[b], :].sum(axis=1)
        xa_b, ya_b = _xy_from_pat(sa, mode)
        xk_b, yk_b = _xy_from_pat(sk, mode)
        da[b] = auc_points(zip(xa_b, ya_b))
        dk[b] = auc_points(zip(xk_b, yk_b))
    diff = da - dk
    ci = lambda v: [float(np.nanpercentile(v, 2.5)), float(np.nanpercentile(v, 97.5))]      # noqa: E731
    return dict(eta=eta, h=h, konfig=konfig, mode=mode, n_p0=len(keys_a), auc_adaptiv=auc_a, auc_adaptiv_ci=ci(da),
                auc_kausn=auc_k, auc_kausn_ci=ci(dk), differenz=auc_a - auc_k, differenz_ci=ci(diff), n_boot=n_boot,
                anteil_groesser=float(np.mean(diff > 0)))


def stufe_pruefung(out_dir: Path = OUT_DIR, log=print, n_boot: int = 1000, use_cache: bool = True) -> dict:
    """Prüfung von „kalibriert“ ohne Nachjustage: P0-Kurven (η 0,9 und 0,7, h 0,8/1,0/1,2), h-Kurven (P0 = 0,5, beide η),
    Netzläufe bei h = 1, Validierungsverzeichnisse (falls vorhanden), Bootstrap der AUC-Differenzen."""
    cfg = kalibriert_config(out_dir)
    if cfg is None:
        raise FileNotFoundError("kalibriert.json fehlt – zuerst --stufe auswertung")
    log(f"kalibriert = {key(*cfg)}")
    dirs = [rr.ROC_DIR, rr.ROC_DIR_P07]
    stufe_klassisch(dirs, cfg, H_KLASSISCH, P0_GRID, out_dir, log, use_cache)
    stufe_hkurve(dirs, cfg, P0_BASIS, out_dir, log, use_cache)
    stufe_netz(cfg, out_dir, log, use_cache)
    val: Dict[str, str] = {}
    for eta, d in VAL_DIRS.items():
        if d.exists() and jn.h_folders(d):
            stufe_klassisch([d], cfg, (H_LERN,), P0_GRID, out_dir / "val", log, use_cache)
            stufe_klassisch([d], PROTOTYP, (H_LERN,), P0_GRID, out_dir / "val", log, use_cache)
            val[eta] = str(d)
        else:
            val[eta] = "ausstehend"
    # Bootstrap der AUC-Differenzen für Prototyp und kalibriert, beide η, h = 0,8 / 1,0 / 1,2
    boot: List[dict] = []
    for name, c in (("Prototyp", PROTOTYP), ("kalibriert", cfg)):
        pats = read_pat(out_dir / f"klassisch_{key(*c)}.json")
        for eta in ("0,9", "0,7"):
            for h in H_KLASSISCH:
                for mode in ("mild", "streng"):
                    b = auc_bootstrap(pats, eta, h, key(*c), P0_GRID, mode, n_boot)
                    if b:
                        b["name"] = name
                        boot.append(b)
                        log(f"{name} η {eta} h {h:g} {mode}: AUC adaptiv {b['auc_adaptiv']:.4f} [{b['auc_adaptiv_ci'][0]:.4f}; {b['auc_adaptiv_ci'][1]:.4f}], "
                            f"k-aus-4 {b['auc_kausn']:.4f}, Δ {b['differenz']:+.4f} [{b['differenz_ci'][0]:+.4f}; {b['differenz_ci'][1]:+.4f}]")
    out = dict(kalibriert=key(*cfg), prototyp=key(*PROTOTYP), validierung=val, bootstrap=boot,
               erstellt=datetime.now().isoformat(timespec="seconds"))
    (out_dir / "pruefung.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Lesen für Anwendung und Tests
# ─────────────────────────────────────────────────────────────────────────────

def load_klassisch(cfg: Config, out_dir: Path = OUT_DIR) -> Tuple[List[dict], Dict[str, Dict[str, List[float]]]]:
    stem = Path(out_dir) / f"klassisch_{key(*cfg)}"
    return read_rows(_csv(stem)), read_pat(_json(stem))


def load_hkurve(cfg: Config, out_dir: Path = OUT_DIR) -> List[dict]:
    return read_rows(Path(out_dir) / f"hkurve_{key(*cfg)}.csv")


def load_netz(cfg: Config, out_dir: Path = OUT_DIR) -> List[dict]:
    return read_rows(Path(out_dir) / f"netz_{key(*cfg)}.csv")


def load_episoden(out_dir: Path = OUT_DIR) -> List[dict]:
    return read_rows(Path(out_dir) / "episoden.csv")


def load_kalibriert(out_dir: Path = OUT_DIR) -> Optional[dict]:
    p = Path(out_dir) / "kalibriert.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def load_pruefung(out_dir: Path = OUT_DIR) -> Optional[dict]:
    p = Path(out_dir) / "pruefung.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def load_pruefung_val(out_dir: Path = OUT_DIR) -> Optional[dict]:
    p = Path(out_dir) / "pruefung_val.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ─────────────────────────────────────────────────────────────────────────────
# Prüfung (c): unabhängige Szenarien (ziel3_roc_val09 Seed 8, ziel3_roc_val07 Seed 9) ohne Nachjustage
# ─────────────────────────────────────────────────────────────────────────────

VAL_ROW_COLUMNS: Tuple[str, ...] = tuple(rr.COLUMNS)


def val_rows(val_dir: Path, hk_rows: Sequence[dict]) -> List[dict]:
    """Zeilen im Schema von ``roc_alle.csv`` für ein Prüfverzeichnis ohne ``run_roc``: Geräte und k-aus-4 unverändert aus
    ``roc_kausn.csv`` (PC 2) mit ``fpr_j_mittel`` je h, dazu adaptiv (T = 10 s, Prototyp) aus den Zeilen der h-Kurve."""
    rows = rr.load_rows(Path(val_dir) / "roc_kausn.csv")
    dev_by_h: Dict[float, List[float]] = {}
    for r in rows:
        if r.get("art") == "Geraet" and r.get("fpr") is not None and r.get("h") is not None:
            dev_by_h.setdefault(round(float(r["h"]), 6), []).append(float(r["fpr"]))
    out: List[dict] = []
    for r in rows:
        d = {c: None for c in VAL_ROW_COLUMNS}
        d.update(r)
        d["quelle"] = "PC 2 (roc_kausn.csv)"
        fs = dev_by_h.get(round(float(r["h"]), 6)) if r.get("h") is not None else None
        d["fpr_j_mittel"] = sum(fs) / len(fs) if fs else None
        out.append(d)
    for r in sorted(hk_rows, key=lambda z: z["h"]):
        if r.get("regel") != "adaptiv":
            continue
        d = {c: None for c in VAL_ROW_COLUMNS}
        fs = dev_by_h.get(round(float(r["h"]), 6))
        d.update(h=r["h"], art="Regel", name="adaptiv", quelle="Replay Dashboard-Kern (kalibrierung)", T_s=r.get("T"),
                 tpr=r.get("tpr"), fpr=r.get("fpr"), fpr_ii=r.get("fpr_ii"), eta_oder_erkannt=r.get("erkannt"),
                 erkannt_streng=r.get("erkannt_streng"), n_krisen=r.get("n_krisen"), n_ereignisse=r.get("n_ereignisse"),
                 n_falsch=r.get("n_falsch"), falsch_je_bett_h=r.get("falsch_je_bett_h"),
                 verz_median_s=r.get("verz_median_s"), verz_p90_s=r.get("verz_p90_s"),
                 verz_kreuz_median_s=r.get("verz_kreuz_median_s"), verz_kreuz_p90_s=r.get("verz_kreuz_p90_s"),
                 fpr_j_mittel=(sum(fs) / len(fs) if fs else None))
        out.append(d)
    return out


def write_val_rows(path: Path, rows: Sequence[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(VAL_ROW_COLUMNS), delimiter=";", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if r.get(c) is None else (r[c] if isinstance(r[c], str) else rr.fmt(r[c]))) for c in VAL_ROW_COLUMNS})


def val_rows_path(val_dir: Path, out_dir: Path = OUT_DIR) -> Path:
    return Path(out_dir) / "val" / f"{VAL_ROWS_PREFIX}{Path(val_dir).name}.csv"


def load_val_dataset_rows(val_dir: Path, out_dir: Path = OUT_DIR) -> Tuple[Optional[List[dict]], Optional[dict]]:
    """(Zeilen, Tabellen) eines Prüfverzeichnisses für die ROC-Werkstatt; (None, None), wenn Stufe val nicht gerechnet."""
    p = val_rows_path(val_dir, out_dir)
    if not p.exists():
        return None, None
    rows = rr.load_rows(p)
    return rows, rr.tables(rows, None)


def hkurve_auc(rows: Sequence[dict], eta: str, mode: str = "mild") -> Dict[str, float]:
    """AUC (ereignisbezogen) der Kurve über h je Regel/Konfiguration aus Zeilen im Kalibrierungsschema."""
    ykey = "erkannt" if mode == "mild" else "erkannt_streng"
    by: Dict[str, List[Tuple[float, float]]] = {}
    for r in rows:
        if r.get("eta") != eta or r.get("ep_anteil") is None or r.get(ykey) is None or r.get("h") is None:
            continue
        if r.get("regel") == "adaptiv" and r.get("P0") is not None and not _same_f(r["P0"], P0_BASIS):
            continue
        by.setdefault(r["konfig"], []).append((100 * r["ep_anteil"], 100 * r[ykey]))
    return {k: auc_points(v) for k, v in by.items() if len(v) >= 2}


def _same_f(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) < 1e-9


def stufe_val(out_dir: Path = OUT_DIR, log=print, n_boot: int = 1000, use_cache: bool = True) -> dict:
    """Prüfung (c) auf ``ziel3_roc_val09`` / ``val07``: P0-Kurven bei h = 1 (Prototyp, kalibriert) mit Bootstrap gegen den
    k-aus-4-Linienzug, Kurven über h (P0 = 0,5) aller Regeln mit ereignisbezogener AUC, Zeilen für die Werkstatt.
    Schreibt in dieselben Dateien wie die Lernszenarien (η-Schlüssel „val 0,9“ / „val 0,7“) und ``pruefung_val.json``."""
    cfg = kalibriert_config(out_dir)
    if cfg is None:
        raise FileNotFoundError("kalibriert.json fehlt – zuerst --stufe auswertung")
    cfgs = [("Prototyp", PROTOTYP), ("kalibriert", cfg)]
    out: dict = dict(kalibriert=key(*cfg), prototyp=key(*PROTOTYP), szenarien={}, bootstrap=[], hkurve_auc={},
                     erstellt=datetime.now().isoformat(timespec="seconds"))
    t_all = time.perf_counter()
    for _eta0, d in VAL_DIRS.items():
        if not (d.exists() and jn.h_folders(d)):
            log(f"{d.name}: nicht vorhanden – ausstehend")
            continue
        eta = eta_of(d)
        seed = json.loads((h_folder(d, 1.0) / "run_config.json").read_text(encoding="utf-8")).get("seed")
        out["szenarien"][eta] = dict(ordner=str(d), seed=seed, h_werte=[h for h, _ in jn.h_folders(d)])
        # Kurven über h beider Konfigurationen (+ k-aus-4 je h) → hkurve_<key>.csv, episoden.csv
        hk_all: List[dict] = []
        for name, c in cfgs:
            stufe_hkurve([d], c, P0_BASIS, out_dir, log, use_cache)
            hk_all += [r for r in load_hkurve(c, out_dir) if r.get("eta") == eta]
        ep_path = out_dir / "episoden.csv"
        write_rows(ep_path, _merge_rows(read_rows(ep_path), hk_all))
        # P0-Kurven bei h = 1
        for name, c in cfgs:
            stufe_klassisch([d], c, (H_LERN,), P0_GRID, out_dir, log, use_cache)
        # Zeilen für die Werkstatt (Geräte + k-aus-4 von PC 2, adaptiv Prototyp aus der h-Kurve)
        proto_rows = [r for r in load_hkurve(PROTOTYP, out_dir) if r.get("eta") == eta]
        write_val_rows(val_rows_path(d, out_dir), val_rows(d, proto_rows))
        # AUC der Kurven über h (alle Regeln, beide Konfigurationen)
        for mode in ("mild", "streng"):
            out["hkurve_auc"].setdefault(eta, {})[mode] = hkurve_auc(hk_all, eta, mode)
        # Bootstrap der P0-Kurven gegen den k-aus-4-Linienzug
        for name, c in cfgs:
            _rows, pats = load_klassisch(c, out_dir)
            for mode in ("mild", "streng"):
                b = auc_bootstrap(pats, eta, H_LERN, key(*c), P0_GRID, mode, n_boot)
                if b:
                    b["name"] = name
                    out["bootstrap"].append(b)
                    log(f"{name} {eta} h 1 {mode}: AUC adaptiv {b['auc_adaptiv']:.4f} [{b['auc_adaptiv_ci'][0]:.4f}; {b['auc_adaptiv_ci'][1]:.4f}], "
                        f"k-aus-4 {b['auc_kausn']:.4f}, Δ {b['differenz']:+.4f} [{b['differenz_ci'][0]:+.4f}; {b['differenz_ci'][1]:+.4f}]")
    out["rechenzeit_s"] = round(time.perf_counter() - t_all, 1)
    (Path(out_dir) / "pruefung_val.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    return out


def load_gitter_tabelle(out_dir: Path = OUT_DIR) -> List[dict]:
    p = Path(out_dir) / "gitter_auswertung.csv"
    if not p.exists():
        return []
    out = []
    with open(p, encoding="utf-8") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            out.append({k: (v if k in ("konfig", "gewaehlt", "vollstaendig", "nebenbedingung_ok") else _num(v)) for k, v in r.items()})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Klassische ROC des Filters (P0) und Parameterwahl „kalibriert“.")
    ap.add_argument("--stufe", choices=["klassisch", "gitter", "auswertung", "pruefung", "episoden", "netz", "val"], required=True)
    ap.add_argument("--roc-dir", type=Path, nargs="+", default=[rr.ROC_DIR, rr.ROC_DIR_P07])
    ap.add_argument("--T-werte", type=float, nargs="+", default=None, help="Gitter: nur diese T (Teilprozess)")
    ap.add_argument("--teil", default="", help="Gitter: Namenszusatz der Teildatei")
    ap.add_argument("--n-boot", type=int, default=1000)
    ap.add_argument("--ohne-cache", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args(argv)
    use_cache = not args.ohne_cache
    t0 = time.perf_counter()
    if args.stufe == "klassisch":
        stufe_klassisch(args.roc_dir, PROTOTYP, H_KLASSISCH, P0_GRID, args.out, rr.log, use_cache)
    elif args.stufe == "gitter":
        stufe_gitter(rr.ROC_DIR, H_LERN, args.T_werte or T_GRID, P0_GRID, args.out, args.teil, rr.log, use_cache)
    elif args.stufe == "auswertung":
        stufe_auswertung(args.out, rr.log)
    elif args.stufe == "pruefung":
        stufe_pruefung(args.out, rr.log, args.n_boot, use_cache)
    elif args.stufe == "episoden":
        stufe_episoden(args.roc_dir, args.out, rr.log)
    elif args.stufe == "val":                       # Prüfung (c): unabhängige Szenarien val09 / val07
        stufe_val(args.out, rr.log, args.n_boot, use_cache)
    elif args.stufe == "netz":                      # Netzläufe offline (Prototyp; kalibriert, falls gewählt)
        stufe_netz(PROTOTYP, args.out, rr.log, use_cache)
        kal = kalibriert_config(args.out)
        if kal is not None and tuple(kal) != tuple(PROTOTYP):
            stufe_netz(kal, args.out, rr.log, use_cache)
    rr.log(f"Stufe {args.stufe}: {time.perf_counter() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
