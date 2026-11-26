"""Ziel 3 – ROC-Kurven: Prüfung durch Wiederholung, Sweep über h (und Fenster T), ``roc_alle.csv``,
Tabellen, Abbildungen – für einen ROC-Ordner (η = 0,9: ``ziel3_roc``; η = 0,7: ``ziel3_roc_p07``).

Aufruf (aus dem Repository-Wurzelverzeichnis):

    .venv\\Scripts\\python -m analysis.roc_ziel3.run_roc [--nur pruefung|sweep|tabellen|abbildungen]
        [--T 10 20 30] [--grid-phase 0.5] [--ohne-cache] [--e1-n 100000] [--roc-dir DIR] [--pc1-dir DIR]
        [--netz NAME] [--suffix _p07] [--eta 0,7] [--vergleich-dir DIR] [--alle-abbildungen] [--figdir DIR]

    η = 0,7:  python -m analysis.roc_ziel3.run_roc --roc-dir experiments/ziel3_simulation/logs/ziel3_roc_p07
                  --pc1-dir experiments/ziel3_simulation/logs/ziel3_4h_p07/pc1 --netz netz_ziel3_4h_p07
                  --suffix _p07 --eta 0,7 --T 10

Schritte
--------
1. **Prüfung durch Wiederholung**: Journal B des Netzlaufs (PC 2) wird in das Offline-Ensemble des Dashboards
   gespielt; die Rot-Abschnitte werden mit ``decisions.jsonl`` des wirklichen Dashboard-Laufs (PC 1) verglichen;
   dazu Latenzen (ab Krisenbeginn, ab erstem Überschreiten) und Eingangsraten des Netzlaufs.
2. **Sweep**: für jeden Ordner ``h_*`` Geräte und k-aus-4 (verriegelt, wie PC 2), adaptiv (Replay des
   Dashboard-Kerns) je Fenster T, Formel (a), Modell (b), Erweiterungen E1 (versetzter Einstieg, mild und
   streng) und E2 (Episoden nach m); Kennzahlen je Patient für den Bootstrap (``roc_patienten.json``).
3. **roc_alle.csv** = ``roc_kausn.csv`` (Zeilen unverändert) + Spalten + Zeilen adaptiv (je T), Formel, Modell,
   ModellE (E1+E2), Netz.  ``fpr`` = Variante (i), ``fpr_ii`` = Variante (ii); Latenz ``verz_*`` ab Krisenbeginn,
   ``verz_kreuz_*`` ab dem ersten Überschreiten einer Gerätegrenze.
4. **Tabellen** (``roc_tabellen.json`` / ``.md``) einschließlich oberer konvexer Hülle der festen Regeln
   (``hull.py``), Latenztabelle und Eingangsprüfung.
5. **Abbildungen** nach ``analysis/belastungsmodell_app/figures/ziel3/`` (Namen mit ``--suffix``).
"""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from analysis.roc_ziel3 import dashcore as dc  # noqa: E402
from analysis.roc_ziel3 import hull as hl  # noqa: E402
from analysis.roc_ziel3 import journals as jn  # noqa: E402
from analysis.roc_ziel3 import metrics as mt  # noqa: E402
from analysis.roc_ziel3 import model_e1 as me1  # noqa: E402
from analysis.roc_ziel3 import model_e2 as me2  # noqa: E402
from analysis.roc_ziel3 import model_roc as mr  # noqa: E402

LOGS = REPO / "experiments" / "ziel3_simulation" / "logs"
ROC_DIR = LOGS / "ziel3_roc"
PC1_DIR = LOGS / "ziel3_12h" / "pc1"
NETZ_NAME = "netz_ziel3_12h"
ROC_DIR_P07 = LOGS / "ziel3_roc_p07"
PC1_DIR_P07 = LOGS / "ziel3_4h_p07" / "pc1"
NETZ_P07 = "netz_ziel3_4h_p07"
FIG_DIR = REPO / "analysis" / "belastungsmodell_app" / "figures" / "ziel3"
CSV_IN = "roc_kausn.csv"
CSV_OUT = "roc_alle.csv"
TAB_JSON = "roc_tabellen.json"
TAB_MD = "roc_tabellen.md"
PATIENTS_JSON = "roc_patienten.json"
CACHE = "adaptiv_replay"
TS: Tuple[float, ...] = (10.0, 20.0, 30.0)
T_MAIN = 10.0

ORIG_COLUMNS = ["h", "art", "name", "on_schwelle", "tpr", "fpr", "eta_oder_erkannt", "n_krisen", "n_ereignisse",
                "n_falsch", "falsch_je_bett_h", "ereignisse_je_bett_tag", "alle4_anteil_stabil", "auc"]
PC2_EXTRA_COLUMNS = ["fpr_ii", "auc_ii", "erkannt_streng", "n_erkannt_weich", "n_erkannt_streng"]   # ab p07 von PC 2
NEW_COLUMNS = ["quelle", "phi", "d_false_s", "c_art_alle4", "c_art_ge2", "tpr_j_mittel", "fpr_j_mittel",
               "erkannt_T", "bemerkung", "T_s", "fpr_ii", "erkannt_streng", "verz_median_s", "verz_p90_s", "hvl_anteil",
               "verz_kreuz_median_s", "verz_kreuz_p90_s"]
COLUMNS = ORIG_COLUMNS + NEW_COLUMNS
NUMERIC = {"h", "on_schwelle", "tpr", "fpr", "eta_oder_erkannt", "n_krisen", "n_ereignisse", "n_falsch",
           "falsch_je_bett_h", "ereignisse_je_bett_tag", "alle4_anteil_stabil", "auc", "phi", "d_false_s",
           "c_art_alle4", "c_art_ge2", "tpr_j_mittel", "fpr_j_mittel", "erkannt_T", "T_s", "fpr_ii",
           "erkannt_streng", "verz_median_s", "verz_p90_s", "hvl_anteil", "verz_kreuz_median_s", "verz_kreuz_p90_s"}
RULE_NAMES: Tuple[str, ...] = ("1-aus-4", "2-aus-4", "3-aus-4", "4-aus-4", "adaptiv")
K_NAMES: Dict[int, str] = {k: f"{k}-aus-4" for k in (1, 2, 3, 4)}
FPR_GRID: Tuple[float, ...] = (0.002, 0.005, 0.01, 0.02, 0.03, 0.05)
FALSE_GRID: Tuple[float, ...] = (0.5, 1.0, 2.0, 4.0, 8.0)
PAUC_MAX = 0.03
LOW_NOISE = 0.03
H_TARGETS: Tuple[float, ...] = (0.8, 1.0, 1.2)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def fmt(x) -> str:
    if x is None:
        return ""
    if isinstance(x, bool):
        return "1" if x else "0"
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        if not math.isfinite(x):
            return ""
        return f"{x:.6g}"
    return str(x)


# ─────────────────────────────────────────────────────────────────────────────
# Uhr und Rot des wirklichen Dashboard-Laufs
# ─────────────────────────────────────────────────────────────────────────────

def clock_offset_fn(pc1_dir: Path, rtt_max_ms: float = 30.0) -> Callable[[float], float]:
    """offset(t_pc1) = Uhr PC 2 − Uhr PC 1, linear interpoliert aus ``export/clock.csv`` (rtt-gefiltert)."""
    ts: List[float] = []
    offs: List[float] = []
    path = pc1_dir / "export" / "clock.csv"
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            for row in csv.reader(fh, delimiter=";"):
                if not row or row[0].startswith("#") or row[0] == "t_pc1":
                    continue
                try:
                    t, off, rtt = float(row[0]), float(row[1]), float(row[2])
                except ValueError:
                    continue
                if rtt <= rtt_max_ms:
                    ts.append(t)
                    offs.append(off)
    if not ts:
        return lambda t: 0.0

    def offset(t: float) -> float:
        i = bisect.bisect_left(ts, t)
        if i == 0:
            return offs[0]
        if i >= len(ts):
            return offs[-1]
        t0, t1 = ts[i - 1], ts[i]
        w = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
        return offs[i - 1] * (1 - w) + offs[i] * w

    return offset


def real_rot(pc1_dir: Path, offset: Callable[[float], float]) -> Dict[str, List[Tuple[float, float, float]]]:
    """Rot-Abschnitte des wirklichen Laufs aus ``decisions.jsonl`` (rot_start/rot_end je Ensemble), in Uhr PC 2."""
    open_: Dict[str, Tuple[float, str, float]] = {}
    out: Dict[str, List[Tuple[float, float, float]]] = {}
    with open(pc1_dir / "decisions.jsonl", encoding="utf-8") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ev = d.get("event")
            if ev == "rot_start":
                t = d["t_abs"]
                open_[d["ensemble"]] = (t + offset(t), d["patient_id"], float(d.get("sdc_score", float("nan"))))
            elif ev == "rot_end":
                s = open_.pop(d["ensemble"], None)
                if s is not None:
                    t = d["t_abs"]
                    out.setdefault(s[1], []).append((s[0], t + offset(t), s[2]))
    for eid, (t0, p, sdc) in open_.items():
        out.setdefault(p, []).append((t0, t0, sdc))
    return {p: sorted(v) for p, v in out.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Replay mit Zwischenspeicher
# ─────────────────────────────────────────────────────────────────────────────

def cache_name(folder_name: str, raw: dict, contract: dict) -> str:
    """Dateiname des Replay-Zwischenspeichers: alter Name für T = 10 mit Vertragsprofil, sonst mit T und Profil-Hash."""
    T = dc.horizon_T(raw)
    profs = dc.all_profiles(raw)
    base = {"sensitivity": float(contract["profile"]["sensitivity"]),
            "false_alarm_rate": float(contract["profile"]["false_alarm_rate"])}
    standard = all(p == base for p in profs.values())
    if abs(T - 10.0) < 1e-9 and standard:
        return f"{folder_name}.json"
    tag = f"_T{T:g}"
    if not standard:
        tag += "_prof" + hashlib.md5(json.dumps(profs, sort_keys=True).encode()).hexdigest()[:8]
    return f"{folder_name}{tag}.json"


def replay_cached(j: jn.Journals, contract: dict, raw: dict, grid_phase: float, cache_dir: Path,
                  use_cache: bool = True) -> Tuple[Dict[str, List[Tuple[float, float, float]]], dict]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / cache_name(j.folder.name, raw, contract)
    key = dict(grid_phase=grid_phase, filter_params=raw.get("filter_params"), profile=dc._first_profile(raw),
               n_alarms=len(j.alarms), t_start=j.t_start, t_end=j.t_end)
    if not all(p == dc._first_profile(raw) for p in dc.all_profiles(raw).values()):
        key["profiles"] = dc.all_profiles(raw)
    if use_cache and path.exists():
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            if d.get("key") == key:
                return {p: [tuple(x) for x in v] for p, v in d["rot"].items()}, d["meta"]
        except (json.JSONDecodeError, KeyError):
            pass
    res = dc.replay_journals(j, contract, raw, grid_phase=grid_phase)
    meta = dict(n_rot=res.n_rot, n_events=res.n_events, n_grid=res.n_grid, n_gc=res.n_gc,
                n_updates=res.n_updates, wall_s=round(res.wall_s, 1), params=res.params)
    path.write_text(json.dumps(dict(key=key, meta=meta, rot=res.rot), ensure_ascii=False), encoding="utf-8")
    return res.rot, meta


def intervals(rot: Dict[str, List[Tuple[float, float, float]]]) -> Dict[str, List[Tuple[float, float]]]:
    return {p: [(a, b) for a, b, _ in v] for p, v in rot.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Schritt 1: Prüfung durch Wiederholung
# ─────────────────────────────────────────────────────────────────────────────

def match_starts(rep: Dict[str, List[Tuple[float, float, float]]], real: Dict[str, List[Tuple[float, float, float]]],
                 tol: float = 5.0) -> dict:
    diffs: List[float] = []
    unmatched_rep = 0
    used: Dict[str, set] = {p: set() for p in real}
    for p, ivs in rep.items():
        starts = [a for a, _, _ in real.get(p, [])]
        for a, _, _ in ivs:
            i = bisect.bisect_left(starts, a)
            best = None
            for k in (i - 1, i):
                if 0 <= k < len(starts) and k not in used.setdefault(p, set()) and abs(starts[k] - a) <= tol:
                    if best is None or abs(starts[k] - a) < abs(starts[best] - a):
                        best = k
            if best is None:
                unmatched_rep += 1
            else:
                used[p].add(best)
                diffs.append(a - starts[best])
    n_real = sum(len(v) for v in real.values())
    n_rep = sum(len(v) for v in rep.values())
    diffs.sort()
    q = (lambda f: diffs[min(len(diffs) - 1, int(f * len(diffs)))]) if diffs else (lambda f: float("nan"))
    return dict(n_real=n_real, n_replay=n_rep, matched=len(diffs), unmatched_replay=unmatched_rep,
                unmatched_real=n_real - len(diffs), diff_median=statistics.median(diffs) if diffs else float("nan"),
                diff_p10=q(0.10), diff_p90=q(0.90), diff_min=diffs[0] if diffs else float("nan"),
                diff_max=diffs[-1] if diffs else float("nan"),
                rel_count_diff=(n_rep - n_real) / n_real if n_real else float("nan"), tol_s=tol)


def rate_dict(r: mt.RateResult, lat: Optional[mt.Latency] = None) -> dict:
    d = dict(tpr=r.tpr, fpr=r.fpr, fpr_ii=r.fpr_ii, hvl_anteil=r.tail_share, erkannt=r.detected,
             erkannt_streng=r.detected_strict, verspaetet=r.late, n_krisen=r.n_crises, n_erkannt=r.n_detected,
             n_erkannt_streng=r.n_detected_strict, n_ereignisse=r.n_events, n_falsch=r.n_false,
             falsch_je_bett_h=r.false_per_bed_h, d_false_s=r.d_false, verz_median_s=r.delay_median,
             verz_p90_s=r.delay_p90)
    if lat is not None:
        d.update(verz_kreuz_median_s=lat.median("cross"), verz_kreuz_p90_s=lat.p90("cross"), n_latenz=lat.n)
    return d


def poisson_ci(n: int, exposure: float) -> Tuple[float, float]:
    from analysis.belastungsmodell_app import sim_data as sd
    return sd.poisson_ci(n, exposure)


def wilson_ci(k: int, n: int) -> Tuple[float, float]:
    if n == 0:
        return float("nan"), float("nan")
    z = 1.959964
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    hw = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return c - hw, c + hw


def input_rates(j: jn.Journals) -> dict:
    """Eingangsraten eines Journals: Fehlalarme je Quelle und Stunde, η_j, Krisen je Bett·h (mit 95-%-Intervallen)."""
    truth = mt.truth_from_journals(j)
    src = mt.source_intervals(j)
    n_cr = sum(len(t.crises) for t in truth.values())
    out: dict = dict(stunden=j.hours, bett_h=j.bed_hours, n_krisen=n_cr, krisen_je_bett_h=n_cr / j.bed_hours,
                     krisen_ci=poisson_ci(n_cr, j.bed_hours), quellen={})
    for s in jn.SOURCES:
        r = mt.rate(src[s], truth, j.bed_hours)
        eta = mt.eta_touch(src[s], truth)
        out["quellen"][s] = dict(fehlalarme_je_h=r.n_false / j.hours, n_falsch=r.n_false, ci=poisson_ci(r.n_false, j.hours),
                                 eta=eta, eta_ci=wilson_ci(round(eta * n_cr), n_cr), fpr=r.fpr, tpr=r.tpr)
    return out


def pruefung(roc_dir: Path, pc1_dir: Path, contract: dict, raw: dict, grid_phase: float, use_cache: bool,
             netz_name: str = NETZ_NAME, phases: Sequence[float] = (0.0, 0.25, 0.75)) -> dict:
    j = jn.load_journals(roc_dir / netz_name)
    truth = mt.truth_from_journals(j)
    src = mt.source_intervals(j)
    entries = mt.crisis_entries(src, truth)
    offset = clock_offset_fn(pc1_dir)
    real = real_rot(pc1_dir, offset)
    rot, meta = replay_cached(j, contract, raw, grid_phase, roc_dir / CACHE, use_cache)
    m = match_starts(rot, real)
    r_rep = mt.rate(intervals(rot), truth, j.bed_hours)
    r_real = mt.rate(intervals(real), truth, j.bed_hours)
    lat_rep = mt.latencies(intervals(rot), truth, entries)
    lat_real = mt.latencies(intervals(real), truth, entries)
    phase_counts = {str(grid_phase): meta["n_rot"]}
    for ph in phases:
        if abs(ph - grid_phase) < 1e-9:
            continue
        res = dc.replay_journals(j, contract, raw, grid_phase=ph)
        phase_counts[str(ph)] = res.n_rot
    kausn_netz = {}
    for k in (1, 2, 3, 4):
        rot_k = mt.k_of_n_rot(src, j.patients, k)
        kausn_netz[K_NAMES[k]] = rate_dict(mt.rate(rot_k, truth, j.bed_hours), mt.latencies(rot_k, truth, entries))
    return dict(netz=netz_name, journal=f"B (alarms.jsonl von PC 2, {netz_name}; Zeit = t_prov_abs)",
                vergleich=f"decisions.jsonl des Dashboard-Laufs ({pc1_dir.parent.name}, PC 1), Uhr PC 1 → PC 2 über export/clock.csv",
                filter=dict(raw.get("filter_params", {})), profile=dc._first_profile(raw), grid_phase=grid_phase,
                replay=meta, zuordnung=m, raten_replay=rate_dict(r_rep, lat_rep), raten_dashboard=rate_dict(r_real, lat_real),
                rot_je_gitterphase=phase_counts, kausn_netz=kausn_netz, n_alarme=len(j.alarms), stunden=j.hours,
                bett_h=j.bed_hours, offset_beispiel=dict(anfang=offset(j.t_start), ende=offset(j.t_end)),
                eingang_netz=input_rates(j))


# ─────────────────────────────────────────────────────────────────────────────
# Schritt 2: Sweep über h (und T), Modellerweiterungen, Profilprüfung
# ─────────────────────────────────────────────────────────────────────────────

def device_summary(j: jn.Journals, truth: Dict[str, mt.PatientTruth], dev_rates: Dict[str, mt.RateResult],
                   art: mt.ArtefactShare, h: float) -> mr.DeviceSummary:
    n_ev = sum(r.n_events for r in dev_rates.values())
    n_f = sum(r.n_false for r in dev_rates.values())
    t_f = sum(r.t_false_total for r in dev_rates.values())
    bed_days = j.bed_hours / 24.0
    crisis_t = sum(mt.total(mt.union(t.crises + t.open_crises)) for t in truth.values())
    closed = [e - s for t in truth.values() for s, e in t.crises]
    return mr.DeviceSummary(
        h=h, tpr_mean=statistics.fmean(r.tpr for r in dev_rates.values()),
        fpr_mean=statistics.fmean(r.fpr for r in dev_rates.values()),
        mfpr=sum(r.fpr for r in dev_rates.values()), c_art=art.c_art_all, c_art_ge2=art.c_art_ge2,
        d_false=t_f / n_f if n_f else float("nan"), d_true=statistics.fmean(closed) if closed else float("nan"),
        pc=crisis_t / (len(j.patients) * (j.t_end - j.t_start)),
        n_per_source_day=n_ev / len(jn.SOURCES) / bed_days if bed_days else float("nan"),
        fdr=n_f / n_ev if n_ev else float("nan"))


def e1_summary(inp: me1.E1Inputs) -> dict:
    return dict(h=inp.h, n_krisen=inp.n_crises, d_true=inp.d_true, d0=inp.d0,
                eta={s: inp.eta[s] for s in jn.SOURCES},
                u_median={s: inp.delay_quantiles(s)[0] for s in jn.SOURCES},
                u_p90={s: inp.delay_quantiles(s)[1] for s in jn.SOURCES},
                u_mean={s: (statistics.fmean(inp.delays[s]) if inp.delays[s] else float("nan")) for s in jn.SOURCES},
                v_median={s: inp.tail_quantiles(s)[0] for s in jn.SOURCES},
                v_p90={s: inp.tail_quantiles(s)[1] for s in jn.SOURCES},
                schon_aktiv={s: inp.already_on_share(s) for s in jn.SOURCES},
                n_u={s: len(inp.delays[s]) for s in jn.SOURCES})


def sweep_one(folder: Path, contract: dict, raws: Dict[float, dict], grid_phase: float, cache_dir: Path,
              use_cache: bool, e1_n: int) -> dict:
    j = jn.load_journals(folder)
    truth = mt.truth_from_journals(j)
    src = mt.source_intervals(j)
    bed_h = j.bed_hours
    h = float(j.threshold_scale) if j.threshold_scale is not None else float(folder.name[2:])
    entries = mt.crisis_entries(src, truth)
    dev_rates = {s: mt.rate(src[s], truth, bed_h) for s in jn.SOURCES}
    etas = {s: mt.eta_touch(src[s], truth) for s in jn.SOURCES}
    art = mt.artefact_share(src, truth)
    ds = device_summary(j, truth, dev_rates, art, h)
    rules: Dict[str, mt.RateResult] = {}
    lat: Dict[str, mt.Latency] = {}
    patients: Dict[str, Dict[str, List[float]]] = {}
    for k in (1, 2, 3, 4):
        rot_k = mt.k_of_n_rot(src, j.patients, k)
        rules[K_NAMES[k]] = mt.rate(rot_k, truth, bed_h)
        lat[K_NAMES[k]] = mt.latencies(rot_k, truth, entries)
        patients[K_NAMES[k]] = mt.per_patient(rot_k, truth, j.hours)
    plain = {K_NAMES[k]: mt.rate({p: mt.k_of_n([src[s].get(p, []) for s in jn.SOURCES], k, latch=False)
                                  for p in j.patients}, truth, bed_h) for k in (2, 3, 4)}
    adaptiv: Dict[float, mt.RateResult] = {}
    lat_ad: Dict[float, mt.Latency] = {}
    replay_meta: Dict[float, dict] = {}
    for T, raw in raws.items():
        rot, meta = replay_cached(j, contract, raw, grid_phase, cache_dir, use_cache)
        ivs = intervals(rot)
        adaptiv[T] = mt.rate(ivs, truth, bed_h)
        lat_ad[T] = mt.latencies(ivs, truth, entries)
        replay_meta[T] = meta
        if abs(T - T_MAIN) < 1e-9:
            patients["adaptiv"] = mt.per_patient(ivs, truth, j.hours)
    formula = {k: mr.formula_point(ds, k) for k in (1, 2, 3, 4)}
    model: Dict[float, Dict[str, mr.ModelPoint]] = {}
    for T in raws:
        model[T] = {}
        for rule in mr.MODEL_RULES:
            p = mr.model_params(ds)
            p = p.__class__(**{**p.__dict__, "T": T})
            cal = mr.mz.calibrate(p)
            res = mr.mz.evaluate(p, rule, cal)
            model[T][rule] = mr.ModelPoint(rule=rule, tpr=res.tpr_inf,
                                          fpr=res.noise_f / (1 - res.PC) if res.PC < 1 else float("nan"),
                                          detected=res.tpr_T, false_per_bed_h=res.false_day / 24.0,
                                          false_day=res.false_day, w=cal.w)
    inp1 = me1.e1_inputs(entries, h, d_true=ds.d_true)
    e1 = me1.e1_all(inp1, Ts=tuple(raws), n=e1_n)
    eps = mt.false_episodes(src, truth)
    t_stable = sum(t.stable_time for t in truth.values())
    inp2 = me2.E2Inputs(h=h, episodes=tuple(eps), bed_hours=bed_h, t_stable=t_stable, pc=ds.pc, fpr_mean=ds.fpr_mean)
    e2: Dict[str, me2.E2Result] = {}
    e2_mmax: Dict[str, me2.E2Result] = {}
    for k in (1, 2, 3, 4):
        e2[K_NAMES[k]] = me2.e2_k_of_n(inp2, k)
        e2_mmax[K_NAMES[k]] = me2.e2_k_of_n(inp2, k, by_mmax=True)
    for T in raws:
        e2[f"adaptiv_T{T:g}"] = me2.e2_adaptiv(inp2, T)
        e2_mmax[f"adaptiv_T{T:g}"] = me2.e2_adaptiv(inp2, T, co_active=False)
    return dict(h=h, bed_hours=bed_h, hours=j.hours, n_patients=len(j.patients), dev_rates=dev_rates, etas=etas,
                art=art, ds=ds, rules=rules, lat=lat, plain=plain, adaptiv=adaptiv, lat_ad=lat_ad,
                replay_meta=replay_meta, formula=formula, model=model, e1_inputs=e1_summary(inp1), e1=e1,
                e2_inputs=inp2.summary(), e2=e2, e2_grob=e2_mmax, t_stable=t_stable, patients=patients)


def sweep(roc_dir: Path, contract: dict, raws: Dict[float, dict], grid_phase: float, use_cache: bool,
          e1_n: int) -> List[dict]:
    out = []
    for h, folder in jn.h_folders(roc_dir):
        t0 = time.perf_counter()
        one = sweep_one(folder, contract, raws, grid_phase, roc_dir / CACHE, use_cache, e1_n)
        parts = []
        for T, a in one["adaptiv"].items():
            parts.append(f"T={T:g}: y {a.tpr:.3f} x(ii) {a.fpr_ii:.5f} erk {a.detected:.3f}/{a.detected_strict:.3f} "
                         f"falsch {a.false_per_bed_h:.2f} (Rot {a.n_events}; Latenz ab Kreuzung {one['lat_ad'][T].median():.1f} s)")
        log(f"h = {h:<5g} adaptiv " + " | ".join(parts) + f"  [{time.perf_counter() - t0:.0f} s]")
        out.append(one)
    return out


def profile_check(roc_dir: Path, contract: dict, grid_phase: float, use_cache: bool) -> dict:
    """2a: Profilvarianten bei h = 1, T = 10 – (i) TPR_j/FPR_j nach Zeit, (ii) η_j/FPR_j – gegen das Vertragsprofil."""
    folder = roc_dir / "h_1.00"
    j = jn.load_journals(folder)
    truth = mt.truth_from_journals(j)
    src = mt.source_intervals(j)
    dev = {s: mt.rate(src[s], truth, j.bed_hours) for s in jn.SOURCES}
    eta = {s: mt.eta_touch(src[s], truth) for s in jn.SOURCES}
    variants = {
        "Vertrag (0,9 / 0,00313)": None,
        "(i) TPR_j, FPR_j nach Zeit": {s: (dev[s].tpr, dev[s].fpr) for s in jn.SOURCES},
        "(ii) η_j, FPR_j": {s: (eta[s], dev[s].fpr) for s in jn.SOURCES},
    }
    out: Dict[str, dict] = {}
    for name, prof in variants.items():
        raw = dc.build_raw_clinical_db(contract, profiles=prof)
        rot, meta = replay_cached(j, contract, raw, grid_phase, roc_dir / CACHE, use_cache)
        r = mt.rate(intervals(rot), truth, j.bed_hours)
        profs = dc.all_profiles(raw)
        w = {model: math.log(p["sensitivity"] / p["false_alarm_rate"]) for model, p in profs.items()}
        out[name] = dict(profile={model: dict(tpr=p["sensitivity"], fpr=p["false_alarm_rate"]) for model, p in profs.items()},
                         w=w, n_rot=meta["n_rot"], raten=rate_dict(r))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Schritt 3: roc_alle.csv
# ─────────────────────────────────────────────────────────────────────────────

def read_csv(path: Path) -> List[dict]:
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def load_rows(path: Path) -> List[dict]:
    """``roc_alle.csv`` als Zeilen mit Zahlen (leer → None)."""
    rows = []
    for r in read_csv(path):
        d: dict = {}
        for k, v in r.items():
            if k in NUMERIC:
                try:
                    d[k] = float(v) if v not in ("", None) else None
                except ValueError:
                    d[k] = None
            else:
                d[k] = v
        rows.append(d)
    return rows


def _row(h, art, name, quelle, **kw) -> dict:
    d = {c: "" for c in COLUMNS}
    d.update(h=h, art=art, name=name, quelle=quelle)
    d.update(kw)
    return d


def _rate_cols(r: mt.RateResult, lat: Optional[mt.Latency] = None) -> dict:
    d = dict(fpr_ii=fmt(r.fpr_ii), erkannt_streng=fmt(r.detected_strict), verz_median_s=fmt(r.delay_median),
             verz_p90_s=fmt(r.delay_p90), hvl_anteil=fmt(r.tail_share), d_false_s=fmt(r.d_false))
    if lat is not None:
        d.update(verz_kreuz_median_s=fmt(lat.median("cross")), verz_kreuz_p90_s=fmt(lat.p90("cross")))
    return d


def build_rows(original: List[dict], sw: List[dict], pr: Optional[dict]) -> List[dict]:
    """Originalzeilen (unverändert in ORIG_COLUMNS, ergänzt um die neuen Spalten) + adaptiv je T, Formel, Modell,
    ModellE, Netz.  Liefert PC 2 selbst ``fpr_ii``/``erkannt_streng`` (ab p07), bleiben diese Werte stehen."""
    by_h = {round(o["h"], 6): o for o in sw}
    Ts = sorted({T for o in sw for T in o["adaptiv"]})
    rows: List[dict] = []
    lab = {v: k for k, v in jn.SOURCE_LABEL.items()}
    for r in original:
        d = {c: r.get(c, "") for c in ORIG_COLUMNS}
        for c in NEW_COLUMNS:
            d[c] = r.get(c, "") if c in ("fpr_ii", "erkannt_streng") else ""
        o = by_h.get(round(float(r["h"]), 6))
        d["quelle"] = "Simulation (PC 2)"
        if o is not None:
            ds = o["ds"]
            d["c_art_alle4"], d["c_art_ge2"] = fmt(ds.c_art), fmt(ds.c_art_ge2)
            d["tpr_j_mittel"], d["fpr_j_mittel"] = fmt(ds.tpr_mean), fmt(ds.fpr_mean)
            if r["art"] == "Geraet" and r["name"] in lab:
                s = lab[r["name"]]
                rr = o["dev_rates"][s]
                eta = o["etas"][s]
                d["phi"] = fmt(rr.tpr / eta if eta else float("nan"))
                own = _rate_cols(rr)
                for c, v in own.items():
                    if not d.get(c):
                        d[c] = v
            elif r["art"] == "Regel" and r["name"] in o["rules"]:
                own = _rate_cols(o["rules"][r["name"]], o["lat"].get(r["name"]))
                for c, v in own.items():
                    if not d.get(c):
                        d[c] = v
        rows.append(d)
    for T in Ts:
        auc_a = mt.auc([o["adaptiv"][T].fpr for o in sw], [o["adaptiv"][T].tpr for o in sw])
        for o in sw:
            ds, a = o["ds"], o["adaptiv"][T]
            fp = o["replay_meta"][T].get("params", {}).get("filter_params", {})
            rows.append(_row(fmt(o["h"]), "Regel", "adaptiv", "Simulation (Replay Dashboard-Kern, PC 1)",
                             tpr=fmt(a.tpr), fpr=fmt(a.fpr), eta_oder_erkannt=fmt(a.detected), n_krisen=a.n_crises,
                             n_ereignisse=a.n_events, n_falsch=a.n_false, falsch_je_bett_h=fmt(a.false_per_bed_h),
                             ereignisse_je_bett_tag=fmt(a.events_per_bed_day),
                             alle4_anteil_stabil=_orig_value(original, o["h"], "alle4_anteil_stabil"), auc=fmt(auc_a),
                             c_art_alle4=fmt(ds.c_art), c_art_ge2=fmt(ds.c_art_ge2),
                             tpr_j_mittel=fmt(ds.tpr_mean), fpr_j_mittel=fmt(ds.fpr_mean), T_s=fmt(T),
                             bemerkung=f"T={fp.get('horizon_T', T):g} s, alpha={fp.get('alpha', 0.5):g}, delta_min=0.5, "
                                       f"Gitter {fp.get('delta_eval', 1.0):g} s; TTL 10 s, Gnadenfrist 10 s unverändert",
                             **_rate_cols(a, o["lat_ad"][T])))
    for k in (1, 2, 3, 4):
        name = K_NAMES[k]
        pts = [o["formula"][k] for o in sw]
        auc_f = mt.auc([p[0] for p in pts], [p[1] for p in pts])
        for o, (f, t) in zip(sw, pts):
            ds = o["ds"]
            rows.append(_row(fmt(o["h"]), "Formel", name, "Formel (a), binomial, p = TPR̄ bzw. FPR̄",
                             tpr=fmt(t), fpr=fmt(f), fpr_ii=fmt(f), auc=fmt(auc_f), tpr_j_mittel=fmt(ds.tpr_mean),
                             fpr_j_mittel=fmt(ds.fpr_mean), c_art_alle4=fmt(ds.c_art), c_art_ge2=fmt(ds.c_art_ge2)))
    for T in Ts:
        for rule in mr.MODEL_RULES:
            if rule != "adaptiv" and T != Ts[0]:
                continue
            name = mr.MODEL_NAMES[rule]
            pts = [o["model"][T][rule] for o in sw]
            auc_m = mt.auc([p.fpr for p in pts], [p.tpr for p in pts])
            for o, m in zip(sw, pts):
                ds = o["ds"]
                rows.append(_row(fmt(o["h"]), "Modell", name, "Modell (b), model_ziel3 mit Geräteraten des h",
                                 tpr=fmt(m.tpr), fpr=fmt(m.fpr), fpr_ii=fmt(m.fpr), eta_oder_erkannt=fmt(m.detected),
                                 falsch_je_bett_h=fmt(m.false_per_bed_h), auc=fmt(auc_m), erkannt_T=fmt(m.detected),
                                 d_false_s=fmt(ds.d_false), c_art_alle4=fmt(ds.c_art), c_art_ge2=fmt(ds.c_art_ge2),
                                 tpr_j_mittel=fmt(ds.tpr_mean), fpr_j_mittel=fmt(ds.fpr_mean),
                                 T_s=fmt(T) if rule == "adaptiv" else "",
                                 bemerkung=f"P(C)={ds.pc:.5g}, |M|·FPR={ds.mfpr:.5g}, w={m.w:.3f}"))

    def e_rows(name: str, key1: str, key2: str, T: Optional[float]):
        pts = [(o["e2"][key2].fpr_ii, o["e1"][key1].share) for o in sw]
        auc_e = mt.auc([p[0] for p in pts], [p[1] for p in pts])
        for o in sw:
            r1, r2 = o["e1"][key1], o["e2"][key2]
            rows.append(_row(fmt(o["h"]), "ModellE", name, "Modell E1+E2 (versetzter Einstieg mit Nachlauf, Episoden nach m)",
                             tpr=fmt(r1.share), fpr=fmt(r2.fpr_ii), fpr_ii=fmt(r2.fpr_ii),
                             eta_oder_erkannt=fmt(r1.caught_mild), erkannt_streng=fmt(r1.caught),
                             falsch_je_bett_h=fmt(r2.false_per_bed_h), auc=fmt(auc_e),
                             verz_median_s=fmt(r1.t_esc_median), verz_p90_s=fmt(r1.t_esc_p90),
                             tpr_j_mittel=fmt(o["ds"].tpr_mean), fpr_j_mittel=fmt(o["ds"].fpr_mean),
                             T_s=fmt(T) if T is not None else "",
                             bemerkung=f"erkannt mild (bis Ende + 10 s) / streng {r1.caught:.4f}; E2: zufällig {r2.part_indep:.3f} + "
                                       f"Episoden {r2.part_common:.3f} je Bett·h ({r2.n_common} Episoden); E1 n={r1.n}"))
    for k in (1, 2, 3, 4):
        e_rows(K_NAMES[k], K_NAMES[k], K_NAMES[k], None)
    for T in Ts:
        e_rows("adaptiv", f"adaptiv_T{T:g}", f"adaptiv_T{T:g}", T)
    if pr is not None:
        netz_label = f"Netzlauf {pr.get('netz', NETZ_NAME).replace('netz_', '')}"
        for name, src_name, rd in (("adaptiv", f"{netz_label} (Dashboard PC 1)", pr["raten_dashboard"]),
                                   ("adaptiv", "Replay Netzjournal (Dashboard-Kern)", pr["raten_replay"])):
            rows.append(_row("1", "Netz", name, src_name, tpr=fmt(rd["tpr"]), fpr=fmt(rd["fpr"]), fpr_ii=fmt(rd.get("fpr_ii")),
                             eta_oder_erkannt=fmt(rd["erkannt"]), erkannt_streng=fmt(rd.get("erkannt_streng")),
                             n_krisen=rd["n_krisen"], n_ereignisse=rd["n_ereignisse"], n_falsch=rd["n_falsch"],
                             falsch_je_bett_h=fmt(rd["falsch_je_bett_h"]), d_false_s=fmt(rd["d_false_s"]), T_s="10",
                             verz_median_s=fmt(rd.get("verz_median_s")), verz_p90_s=fmt(rd.get("verz_p90_s")),
                             verz_kreuz_median_s=fmt(rd.get("verz_kreuz_median_s")), verz_kreuz_p90_s=fmt(rd.get("verz_kreuz_p90_s")),
                             hvl_anteil=fmt(rd.get("hvl_anteil"))))
        for name, rd in pr["kausn_netz"].items():
            rows.append(_row("1", "Netz", name, f"{netz_label} (Journal B, verriegelt)", tpr=fmt(rd["tpr"]),
                             fpr=fmt(rd["fpr"]), fpr_ii=fmt(rd.get("fpr_ii")), eta_oder_erkannt=fmt(rd["erkannt"]),
                             erkannt_streng=fmt(rd.get("erkannt_streng")), n_krisen=rd["n_krisen"],
                             n_ereignisse=rd["n_ereignisse"], n_falsch=rd["n_falsch"],
                             falsch_je_bett_h=fmt(rd["falsch_je_bett_h"]), d_false_s=fmt(rd["d_false_s"]),
                             verz_kreuz_median_s=fmt(rd.get("verz_kreuz_median_s")), verz_kreuz_p90_s=fmt(rd.get("verz_kreuz_p90_s")),
                             hvl_anteil=fmt(rd.get("hvl_anteil"))))
    return rows


def _orig_value(original: List[dict], h: float, col: str) -> str:
    for r in original:
        if abs(float(r["h"]) - h) < 1e-9:
            return r.get(col, "")
    return ""


def write_csv(rows: List[dict], path: Path) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, delimiter=";", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLUMNS})


def patients_json(sw: List[dict]) -> dict:
    """{Regel: {h: {Patient: Werte}}} für den Bootstrap der Hülle."""
    out: Dict[str, Dict[str, Dict[str, List[float]]]] = {}
    for o in sw:
        for rule, per in o["patients"].items():
            out.setdefault(rule, {})[f"{o['h']:g}"] = per
    return dict(felder=list(mt.PATIENT_FIELDS), daten=out)


# ─────────────────────────────────────────────────────────────────────────────
# Schritt 4: Tabellen
# ─────────────────────────────────────────────────────────────────────────────

def row_T(r: dict) -> Optional[float]:
    return r.get("T_s") if r.get("T_s") is not None else None


def curve(rows: Sequence[dict], art: str, name: str, T: Optional[float] = T_MAIN) -> List[dict]:
    sel = [r for r in rows if r.get("art") == art and r.get("name") == name and r.get("h") is not None
           and (row_T(r) is None or T is None or abs(row_T(r) - T) < 1e-9)]
    return sorted(sel, key=lambda r: r["h"])


def _xy(sel: Sequence[dict], xkey: str, ykey: str) -> Tuple[List[float], List[float]]:
    xs = [r[xkey] for r in sel if r.get(xkey) is not None and r.get(ykey) is not None]
    ys = [r[ykey] for r in sel if r.get(xkey) is not None and r.get(ykey) is not None]
    return xs, ys


def hull_tables(patients: Optional[dict], hs: Sequence[float], eta_label: str, n_boot: int = 2000) -> Optional[dict]:
    if not patients or "adaptiv" not in patients.get("daten", {}):
        return None
    res = hl.analyse(patients["daten"], hs, H_TARGETS, n_boot=n_boot)
    return {view: dict(huelle=r.hull, zeilen=r.rows, satz=hl.summary_sentence(r, eta_label)) for view, r in res.items()}


def tables(rows: List[dict], pr: Optional[dict], sw: Optional[List[dict]] = None, extra: Optional[dict] = None) -> dict:
    extra = extra or {}
    Ts = sorted({r["T_s"] for r in rows if r.get("T_s") is not None}) or [T_MAIN]
    auc_t: Dict[str, dict] = {}
    for art, label in (("Geraet", "Gerät"), ("Regel", "Regel"), ("Formel", "Formel (a)"), ("Modell", "Modell (b)"),
                       ("ModellE", "Modell E1+E2")):
        names = sorted({r["name"] for r in rows if r.get("art") == art})
        for name in names:
            for T in (Ts if name == "adaptiv" else [None]):
                sel = curve(rows, art, name, T if T is not None else T_MAIN)
                if not sel:
                    continue
                key = name if art in ("Geraet", "Regel") else f"{name} [{label}]"
                if T is not None and abs(T - T_MAIN) > 1e-9:
                    key += f" T={T:g}"
                for variant, xkey in (("i", "fpr"), ("ii", "fpr_ii")):
                    f, t = _xy(sel, xkey, "tpr")
                    if not f:
                        continue
                    auc_t.setdefault(key, {}).update({
                        "art": art, "name": name, "T": T,
                        f"auc_{variant}": mt.auc(f, t), f"pauc_003_{variant}": mt.partial_auc(f, t, PAUC_MAX),
                        "n_punkte": len(sel), f"fpr_min_{variant}": min(f), f"fpr_max_{variant}": max(f)})
    for key, v in auc_t.items():
        v["auc"] = v.get("auc_ii", v.get("auc_i"))
        v["pauc_003"] = v.get("pauc_003_ii", v.get("pauc_003_i"))

    def rule_rows(name: str, T: float) -> List[dict]:
        return curve(rows, "Regel", name, T)

    tpr_at: Dict[str, dict] = {}
    for fgrid in FPR_GRID:
        col: Dict[str, dict] = {}
        for name in RULE_NAMES:
            for T in (Ts if name == "adaptiv" else [T_MAIN]):
                sel = rule_rows(name, T)
                f, t = _xy(sel, "fpr_ii", "tpr")
                v, inside = mt.tpr_at(f, t, fgrid)
                col[name if name != "adaptiv" else f"adaptiv T={T:g}"] = dict(tpr=v, innerhalb=inside)
        main = {k: v for k, v in col.items() if k in RULE_NAMES or k == f"adaptiv T={T_MAIN:g}"}
        best = max((k for k in main if math.isfinite(main[k]["tpr"])), key=lambda k: main[k]["tpr"], default=None)
        tpr_at[f"{fgrid:g}"] = dict(werte=col, beste=best)
    det_at: Dict[str, dict] = {}
    for fg in FALSE_GRID:
        col = {}
        for name in RULE_NAMES:
            for T in (Ts if name == "adaptiv" else [T_MAIN]):
                sel = rule_rows(name, T)
                x, y = _xy(sel, "falsch_je_bett_h", "eta_oder_erkannt")
                v, inside = mt.y_at_logx(x, y, fg)
                col[name if name != "adaptiv" else f"adaptiv T={T:g}"] = dict(erkannt=v, innerhalb=inside)
        main = {k: v for k, v in col.items() if k in RULE_NAMES or k == f"adaptiv T={T_MAIN:g}"}
        best = max((k for k in main if math.isfinite(main[k]["erkannt"])), key=lambda k: main[k]["erkannt"], default=None)
        det_at[f"{fg:g}"] = dict(werte=col, beste=best)
    work: Dict[str, dict] = {}
    for name in RULE_NAMES:
        for T in (Ts if name == "adaptiv" else [T_MAIN]):
            for r in rule_rows(name, T):
                if abs(r["h"] - 1.0) < 1e-9:
                    work[name if name != "adaptiv" else f"adaptiv T={T:g}"] = dict(
                        tpr=r.get("tpr"), fpr=r.get("fpr"), fpr_ii=r.get("fpr_ii"), erkannt=r.get("eta_oder_erkannt"),
                        erkannt_streng=r.get("erkannt_streng"), falsch_je_bett_h=r.get("falsch_je_bett_h"),
                        n_ereignisse=r.get("n_ereignisse"), n_falsch=r.get("n_falsch"),
                        verz_median_s=r.get("verz_median_s"), verz_p90_s=r.get("verz_p90_s"),
                        verz_kreuz_median_s=r.get("verz_kreuz_median_s"), verz_kreuz_p90_s=r.get("verz_kreuz_p90_s"),
                        hvl_anteil=r.get("hvl_anteil"), T=T)
    slopes: Dict[str, dict] = {}
    for name in RULE_NAMES:
        sel = [r for r in rule_rows(name, T_MAIN) if r.get("fpr_j_mittel") is not None]
        x = [r["fpr_j_mittel"] for r in sel]
        y = [r["fpr_ii"] if r.get("fpr_ii") is not None else r.get("fpr") for r in sel]
        lo, n_lo = mt.loglog_slope(x, y, LOW_NOISE)
        al, n_al = mt.loglog_slope(x, y)
        slopes[name] = dict(niedrig=lo, n_niedrig=n_lo, gesamt=al, n_gesamt=n_al)
    for k in (2, 3):
        name = K_NAMES[k]
        sel = [r for r in curve(rows, "Formel", name) if r.get("fpr_j_mittel") is not None]
        lo, n_lo = mt.loglog_slope([r["fpr_j_mittel"] for r in sel], [r.get("fpr") for r in sel], LOW_NOISE)
        slopes[f"{name} [Formel (a)]"] = dict(niedrig=lo, n_niedrig=n_lo)
    vergleich: Dict[str, List[dict]] = {}
    for name in ("2-aus-4", "3-aus-4", "adaptiv"):
        for T in (Ts if name == "adaptiv" else [T_MAIN]):
            sim = {round(r["h"], 6): r for r in rule_rows(name, T)}
            for art in ("Formel", "Modell", "ModellE"):
                for r in curve(rows, art, name, T):
                    s = sim.get(round(r["h"], 6))
                    if s is None:
                        continue
                    key = f"{name} [{art}]" + (f" T={T:g}" if name == "adaptiv" and abs(T - T_MAIN) > 1e-9 else "")
                    xs = s.get("fpr_ii") if s.get("fpr_ii") is not None else s.get("fpr")
                    xt = r.get("fpr_ii") if r.get("fpr_ii") is not None else r.get("fpr")
                    vergleich.setdefault(key, []).append(dict(
                        h=r["h"], fpr_sim=xs, fpr_theorie=xt, fpr_faktor=(xt / xs) if xs else float("nan"),
                        tpr_sim=s["tpr"], tpr_theorie=r["tpr"], tpr_diff=r["tpr"] - s["tpr"],
                        falsch_sim=s.get("falsch_je_bett_h"), falsch_theorie=r.get("falsch_je_bett_h"),
                        erkannt_sim=s.get("eta_oder_erkannt"), erkannt_streng_sim=s.get("erkannt_streng"),
                        erkannt_theorie=r.get("eta_oder_erkannt"), erkannt_streng_theorie=r.get("erkannt_streng")))
    latch: Dict[str, List[dict]] = {}
    e1_t: Dict[str, dict] = {}
    e2_t: Dict[str, dict] = {}
    if sw:
        for o in sw:
            for name, rr in o["plain"].items():
                lat = o["rules"][name]
                latch.setdefault(name, []).append(dict(h=o["h"], fpr_ohne_latch=rr.fpr, fpr_mit_latch=lat.fpr,
                                                       tpr_ohne_latch=rr.tpr, tpr_mit_latch=lat.tpr,
                                                       n_ohne=rr.n_events, n_mit=lat.n_events))
            e1_t[f"{o['h']:g}"] = dict(eingaben=o["e1_inputs"],
                                       ergebnisse={k: dict(y=v.share, erkannt=v.caught, erkannt_mild=v.caught_mild,
                                                           t_esc_median=v.t_esc_median, t_esc_p90=v.t_esc_p90, T=v.T)
                                                   for k, v in o["e1"].items()})
            e2_t[f"{o['h']:g}"] = dict(eingaben=o["e2_inputs"],
                                       ergebnisse={k: dict(falsch_je_bett_h=v.false_per_bed_h, zufaellig=v.part_indep,
                                                           episoden=v.part_common, fpr_ii=v.fpr_ii, n=v.n_common)
                                                   for k, v in o["e2"].items()},
                                       grob={k: dict(falsch_je_bett_h=v.false_per_bed_h, episoden=v.part_common)
                                             for k, v in o["e2_grob"].items()})
    T_tab: Dict[str, dict] = {}
    for T in Ts:
        key = f"adaptiv T={T:g}"
        wp = work.get(key, {})
        au = auc_t.get("adaptiv" if abs(T - T_MAIN) < 1e-9 else f"adaptiv T={T:g}", {})
        sel = rule_rows("adaptiv", T)
        over: Dict[str, dict] = {}
        for fg in FALSE_GRID:
            v = det_at[f"{fg:g}"]["werte"]
            a = v.get(key, {}).get("erkannt", float("nan"))
            over[f"{fg:g}"] = {k: (a - v[k]["erkannt"]) if (math.isfinite(a) and math.isfinite(v[k]["erkannt"])) else float("nan")
                               for k in ("2-aus-4", "3-aus-4")}
        catch: Dict[str, dict] = {}
        for fgrid in FPR_GRID:
            v = tpr_at[f"{fgrid:g}"]["werte"]
            a = v.get(key, {}).get("tpr", float("nan"))
            catch[f"{fgrid:g}"] = {k: (a - v[k]["tpr"]) if (math.isfinite(a) and math.isfinite(v[k]["tpr"])) else float("nan")
                                   for k in ("2-aus-4", "3-aus-4")}
        T_tab[f"{T:g}"] = dict(arbeitspunkt=wp, pauc_i=au.get("pauc_003_i"), pauc_ii=au.get("pauc_003_ii"),
                               auc_ii=au.get("auc_ii"), n_punkte=len(sel), vorsprung_stueck=over, vorsprung_zeit=catch)
    # Latenz: T-Reihe bei h = 1 (ab Krisenbeginn / ab erstem Überschreiten) und Netzlauf
    latenz: Dict[str, dict] = {}
    for name in ("2-aus-4", "3-aus-4"):
        w = work.get(name, {})
        latenz[name] = dict(start=(w.get("verz_median_s"), w.get("verz_p90_s")),
                            kreuz=(w.get("verz_kreuz_median_s"), w.get("verz_kreuz_p90_s")))
    for T in Ts:
        w = work.get(f"adaptiv T={T:g}", {})
        latenz[f"adaptiv T={T:g}"] = dict(start=(w.get("verz_median_s"), w.get("verz_p90_s")),
                                         kreuz=(w.get("verz_kreuz_median_s"), w.get("verz_kreuz_p90_s")))
    if pr:
        rd = pr["raten_dashboard"]
        latenz["Netzlauf adaptiv (Dashboard)"] = dict(start=(rd.get("verz_median_s"), rd.get("verz_p90_s")),
                                                     kreuz=(rd.get("verz_kreuz_median_s"), rd.get("verz_kreuz_p90_s")))
        for name in ("2-aus-4", "3-aus-4"):
            rk = pr["kausn_netz"].get(name, {})
            latenz[f"Netzlauf {name} (Journal B)"] = dict(start=(rk.get("verz_median_s"), rk.get("verz_p90_s")),
                                                          kreuz=(rk.get("verz_kreuz_median_s"), rk.get("verz_kreuz_p90_s")))
    hs = sorted({r["h"] for r in rows if r.get("art") == "Regel" and r.get("name") == "adaptiv" and r.get("h") is not None})
    huelle = hull_tables(extra.get("patienten"), hs, extra.get("eta_label", "η = 0,9"), extra.get("n_boot", 2000))
    return dict(auc=auc_t, tpr_bei_fpr=tpr_at, erkannt_bei_falsch=det_at, arbeitspunkt_h1=work,
                steigungen=slopes, vergleich_theorie=vergleich, latch=latch, e1=e1_t, e2=e2_t, fenster_T=T_tab,
                Ts=Ts, pruefung=pr, profil_check=extra.get("profil_check"), huelle=huelle, latenz=latenz,
                eingang=extra.get("eingang"), eta_label=extra.get("eta_label", "η = 0,9"), pauc_max=PAUC_MAX,
                low_noise_max=LOW_NOISE, x_hauptvariante="ii")


def _d(x, digits=3) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "–"
    return f"{x:.{digits}f}".replace(".", ",")


def _ci(ci) -> str:
    return f"[{_d(ci[0], 1)}; {_d(ci[1], 1)}]" if ci else "–"


def tables_markdown(tb: dict) -> str:
    L: List[str] = [f"# Ziel 3 – ROC bei Variation der Gerätegrenzwerte ({tb.get('eta_label', 'η = 0,9')}): Tabellen", "",
                    "x-Hauptvariante (ii): nur Zeit der falschen Episoden (berühren keine Krise ± 10 s) / stabile Zeit; "
                    "Variante (i): alle Rot-Zeit im stabilen Zustand / stabile Zeit (enthält Nachläufe von Rot, das in einer "
                    "Krise begann).  Latenz ab Krisenbeginn und ab dem ersten Überschreiten einer Gerätegrenze.", ""]
    pr = tb.get("pruefung")
    if pr:
        z = pr["zuordnung"]
        L += [f"## Prüfung durch Wiederholung ({pr.get('netz', '')})", "",
              f"- Eingabe: {pr['journal']}; Vergleich: {pr['vergleich']}",
              f"- Filter: T = {pr['filter'].get('horizon_T')} s, α = {pr['filter'].get('alpha')}, δ_min = 0,5, "
              f"Gitter Δ_eval = {pr['filter'].get('delta_eval')} s (Phase {pr['grid_phase']} s); Profil TPR {pr['profile'].get('sensitivity')}, "
              f"FPR {pr['profile'].get('false_alarm_rate')}",
              f"- Eskalationen: Dashboard {z['n_real']}, Replay {z['n_replay']} ({100 * z['rel_count_diff']:+.2f} %); "
              f"zugeordnet (±{z['tol_s']:g} s) {z['matched']}, ohne Partner Replay {z['unmatched_replay']}, Dashboard {z['unmatched_real']}",
              f"- Beginn Replay − Dashboard (Uhr PC 2): Median {z['diff_median']:+.3f} s, p10 {z['diff_p10']:+.3f} s, "
              f"p90 {z['diff_p90']:+.3f} s, min {z['diff_min']:+.2f} s, max {z['diff_max']:+.2f} s",
              f"- Erkannte Krisen (mild/streng): Dashboard {pr['raten_dashboard']['n_erkannt']}/{pr['raten_dashboard']['n_erkannt_streng']} von "
              f"{pr['raten_dashboard']['n_krisen']}, Replay {pr['raten_replay']['n_erkannt']}/{pr['raten_replay']['n_erkannt_streng']}; "
              f"TPR/FPR (Zeit) Dashboard {_d(pr['raten_dashboard']['tpr'])}/{_d(pr['raten_dashboard']['fpr'], 5)}, "
              f"Replay {_d(pr['raten_replay']['tpr'])}/{_d(pr['raten_replay']['fpr'], 5)}",
              f"- Latenz ab erstem Überschreiten (Median / p90): Dashboard {_d(pr['raten_dashboard'].get('verz_kreuz_median_s'), 1)} / "
              f"{_d(pr['raten_dashboard'].get('verz_kreuz_p90_s'), 1)} s, Replay {_d(pr['raten_replay'].get('verz_kreuz_median_s'), 1)} / "
              f"{_d(pr['raten_replay'].get('verz_kreuz_p90_s'), 1)} s",
              f"- Rot je Gitterphase: " + ", ".join(f"{k} s → {v}" for k, v in pr['rot_je_gitterphase'].items()), ""]
    ein = tb.get("eingang")
    if ein:
        L += ["## Eingangsprüfung (h = 1,0 offline gegen Netzlauf)", "",
              "| Größe | offline h = 1,0 (12 h) | Netzlauf | 95-%-Intervall Netzlauf | innerhalb |", "|---|---|---|---|---|"]
        for s, v in ein["quellen"].items():
            L.append(f"| Fehlalarme/h {s} | {_d(v['offline'], 1)} | {_d(v['netz'], 1)} | {_ci(v['netz_ci'])} | {'ja' if v['ok'] else 'nein'} |")
            L.append(f"| η {s} | {_d(v['eta_offline'])} | {_d(v['eta_netz'])} | [{_d(v['eta_ci'][0])}; {_d(v['eta_ci'][1])}] | {'ja' if v['eta_ok'] else 'nein'} |")
        k = ein["krisen"]
        L.append(f"| Krisen je Bett·h | {_d(k['offline'])} | {_d(k['netz'])} | [{_d(k['netz_ci'][0])}; {_d(k['netz_ci'][1])}] | {'ja' if k['ok'] else 'nein'} |")
        L.append("")
    hu = tb.get("huelle")
    if hu:
        L += ["## Obere konvexe Hülle der festen Regeln k-aus-4 (alle k, alle h, mit Ursprung) und Abstand der adaptiven Punkte", "",
              "Δ = y_adaptiv − y_Hülle bei gleichem x (pp); Δ_Sehne = y_adaptiv − Sehne 2-aus-4 ↔ 3-aus-4 desselben h "
              "(gleiche Gerätegrenzwerte; * = x außerhalb der Strecke, linear verlängert); 95-%-Intervalle: Bootstrap über "
              "die 20 Patienten (2000 Züge, gepaart, Hülle je Zug neu) bei h = 0,8 / 1,0 / 1,2.", ""]
        for view, v in hu.items():
            L += [f"### {'nach Stück (x = Fehleskalationen je Bett·h, y = erkannte Krisen mild)' if view == 'stueck' else 'nach Zeit (x = Fehleskalationszeit (ii), y = Anteil der Krisenzeit mit Rot)'}", "",
                  "| h | x | y adaptiv | y Hülle | Δ [pp] | 95-%-Intervall | y Sehne | Δ_Sehne [pp] | 95-%-Intervall |", "|---|---|---|---|---|---|---|---|---|"]
            for r in v["zeilen"]:
                xd = 2 if view == "stueck" else 5
                L.append(f"| {r['h']:g} | {_d(r['x'], xd)} | {_d(100 * r['y_adaptiv'], 1)} % | {_d(100 * r['y_huelle'], 1) if r['y_huelle'] == r['y_huelle'] else '–'} % | "
                         f"{_d(r['delta_pp'], 1)} | {_ci(r.get('delta_ci'))} | {_d(100 * r['y_sehne'], 1)} %{'' if r['sehne_innerhalb'] else '*'} | "
                         f"{_d(r['delta_sehne_pp'], 1)} | {_ci(r.get('delta_sehne_ci'))} |")
            L += ["", f"**{v['satz']}**", ""]
    lat = tb.get("latenz")
    if lat:
        L += ["## Latenz bei h = 1,0: Median / p90 [s], ab erstem Überschreiten einer Gerätegrenze (Hauptdefinition) und ab Krisenbeginn", "",
              "| Regel | ab Überschreiten | ab Krisenbeginn |", "|---|---|---|"]
        for name, v in lat.items():
            L.append(f"| {name} | {_d(v['kreuz'][0], 1)} / {_d(v['kreuz'][1], 1)} | {_d(v['start'][0], 1)} / {_d(v['start'][1], 1)} |")
        L.append("")
    pc = tb.get("profil_check")
    if pc:
        L += ["## Prüfung der Profilkalibrierung (h = 1,0, T = 10 s)", "",
              "| Profil | w_j (ECG, Vent, SpO2, ABP) | Rot | erkannt mild / streng | Fehleskalationen je Bett·h | y (Zeit) | x(ii) |",
              "|---|---|---|---|---|---|---|"]
        for name, v in pc.items():
            w = ", ".join(_d(v["w"][m], 2) for m in ("Sim ECG", "Sim Vent", "Sim SpO2", "Sim ABP") if m in v["w"])
            r = v["raten"]
            L.append(f"| {name} | {w} | {v['n_rot']} | {_d(100 * r['erkannt'], 1)} % / {_d(100 * r['erkannt_streng'], 1)} % | "
                     f"{_d(r['falsch_je_bett_h'], 2)} | {_d(r['tpr'])} | {_d(r['fpr_ii'], 5)} |")
        L.append("")
    ft = tb.get("fenster_T", {})
    if ft:
        L += ["## Fenster T: Arbeitspunkt h = 1,0 (adaptiv je T neben 2-/3-aus-4)", "",
              "| Regel | y (Zeit) | x(i) | x(ii) | erkannt mild | streng | Fehleskalationen je Bett·h | Latenz ab Überschreiten Median / p90 [s] | ab Beginn | Nachlauf-Anteil an x(i) | pAUC(ii) ≤ 0,03 |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        wp = tb["arbeitspunkt_h1"]
        order = ["2-aus-4", "3-aus-4"] + [f"adaptiv T={T:g}" for T in tb.get("Ts", [])]
        for name in order:
            v = wp.get(name)
            if not v:
                continue
            pa = ft.get(name.split("=")[-1], {}).get("pauc_ii") if name.startswith("adaptiv") else tb["auc"].get(name, {}).get("pauc_003_ii")
            L.append(f"| {name} | {_d(v['tpr'])} | {_d(v['fpr'], 5)} | {_d(v['fpr_ii'], 5)} | {_d(100 * (v['erkannt'] or 0), 1)} % | "
                     f"{_d(100 * (v['erkannt_streng'] or 0), 1)} % | {_d(v['falsch_je_bett_h'], 2)} | "
                     f"{_d(v.get('verz_kreuz_median_s'), 1)} / {_d(v.get('verz_kreuz_p90_s'), 1)} | {_d(v['verz_median_s'], 1)} / {_d(v['verz_p90_s'], 1)} | "
                     f"{_d(100 * (v['hvl_anteil'] or 0), 1)} % | {_d(pa, 3)} |")
        L += ["", "Vorsprung adaptiv gegenüber k-aus-N (Differenz der Anteile; positiv = adaptiv besser):", ""]
        for T, v in ft.items():
            s1 = "; ".join(f"{fg}/Bett·h: 2-aus-4 {_d(100 * d['2-aus-4'], 1)} pp, 3-aus-4 {_d(100 * d['3-aus-4'], 1)} pp"
                           for fg, d in v["vorsprung_stueck"].items())
            s2 = "; ".join(f"FPR {fg}: 2-aus-4 {_d(d['2-aus-4'])}, 3-aus-4 {_d(d['3-aus-4'])}"
                           for fg, d in v["vorsprung_zeit"].items())
            L += [f"- T = {T} s, nach Stück (erkannte Krisen): {s1}", f"- T = {T} s, nach Zeit (TPR bei FPR(ii)): {s2}"]
        L.append("")
    L += ["## AUC (voll) und partieller AUC (FPR ≤ 0,03, normiert auf 0,03); x-Variante (ii) | (i)", "",
          "| Kurve | AUC (ii) | pAUC (ii) | AUC (i) | pAUC (i) | Punkte |", "|---|---|---|---|---|---|"]
    for key, v in tb["auc"].items():
        L.append(f"| {key} | {_d(v.get('auc_ii'))} | {_d(v.get('pauc_003_ii'))} | {_d(v.get('auc_i'))} | {_d(v.get('pauc_003_i'))} | {v['n_punkte']} |")
    names_main = list(RULE_NAMES[:-1]) + [f"adaptiv T={T:g}" for T in tb.get("Ts", [T_MAIN])]
    L += ["", "## TPR bei vorgegebenem FPR(ii) (linear interpoliert; * = unterhalb des kleinsten Messpunkts, Anker (0, 0))", "",
          "| FPR | " + " | ".join(names_main) + " | beste (T = 10) |", "|---|" + "---|" * (len(names_main) + 1)]
    for f, v in tb["tpr_bei_fpr"].items():
        cells = []
        for name in names_main:
            w = v["werte"].get(name, {})
            cells.append(_d(w.get("tpr")) + ("" if w.get("innerhalb", True) else "*"))
        L.append(f"| {f.replace('.', ',')} | " + " | ".join(cells) + f" | {v['beste'] or '–'} |")
    L += ["", "## Erkannte Krisen (mild) bei vorgegebenen Fehleskalationen je Bett·h (Interpolation in log x; – = außerhalb)", "",
          "| je Bett·h | " + " | ".join(names_main) + " | beste (T = 10) |", "|---|" + "---|" * (len(names_main) + 1)]
    for f, v in tb["erkannt_bei_falsch"].items():
        cells = []
        for name in names_main:
            w = v["werte"].get(name, {})
            e = w.get("erkannt", float("nan"))
            cells.append(_d(100 * e, 1) + " %" if math.isfinite(e) else "–")
        L.append(f"| {f.replace('.', ',')} | " + " | ".join(cells) + f" | {v['beste'] or '–'} |")
    L += ["", "## Arbeitspunkt h = 1,0 (wie in den Netzläufen)", "",
          "| Regel | y (Zeit) | x(i) | x(ii) | Nachlauf-Anteil | erkannt mild | streng | Fehleskalationen je Bett·h | Ereignisse | davon falsch | Latenz ab Überschreiten Median / p90 [s] | ab Beginn |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, v in tb["arbeitspunkt_h1"].items():
        L.append(f"| {name} | {_d(v['tpr'])} | {_d(v['fpr'], 5)} | {_d(v['fpr_ii'], 5)} | {_d(100 * (v['hvl_anteil'] or 0), 1)} % | "
                 f"{_d(100 * (v['erkannt'] or 0), 1)} % | {_d(100 * (v['erkannt_streng'] or 0), 1)} % | {_d(v['falsch_je_bett_h'], 2)} | "
                 f"{int(v['n_ereignisse'] or 0)} | {int(v['n_falsch'] or 0)} | {_d(v.get('verz_kreuz_median_s'), 1)} / {_d(v.get('verz_kreuz_p90_s'), 1)} | "
                 f"{_d(v['verz_median_s'], 1)} / {_d(v['verz_p90_s'], 1)} |")
    L += ["", "## Steigungen in R3 (d ln FPR_sys(ii) / d ln FPR̄, kleinste Quadrate)", "",
          "| Regel | FPR̄ ≤ 0,03 (Punkte) | alle h (Punkte) |", "|---|---|---|"]
    for name, v in tb["steigungen"].items():
        g = f"{_d(v['gesamt'], 2)} ({v['n_gesamt']})" if "gesamt" in v else "–"
        L.append(f"| {name} | {_d(v['niedrig'], 2)} ({v['n_niedrig']}) | {g} |")
    if tb.get("e1"):
        L += ["", "## E1 – versetzter Einstieg: Messung je h (η_j Teilnahme, u_j Median / p90 [s], Nachlauf v_j Median [s], Anteil schon aktiv)", "",
              "| h | η ECG / Vent / SpO2 / ABP | u Median | u p90 | v Median | schon aktiv | d_true [s] | y 2-aus-4 | y 3-aus-4 | y adaptiv je T | erkannt adaptiv streng je T | erkannt adaptiv mild je T |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for h, v in tb["e1"].items():
            e = v["eingaben"]
            r = v["ergebnisse"]
            ad = [r.get(f"adaptiv_T{T:g}", {}) for T in tb.get("Ts", [T_MAIN])]
            L.append(f"| {h} | " + " / ".join(_d(e['eta'][s], 2) for s in jn.SOURCES) + " | " +
                     " / ".join(_d(e['u_median'][s], 1) for s in jn.SOURCES) + " | " +
                     " / ".join(_d(e['u_p90'][s], 1) for s in jn.SOURCES) + " | " +
                     " / ".join(_d(e.get('v_median', {}).get(s), 1) for s in jn.SOURCES) + " | " +
                     " / ".join(_d(e['schon_aktiv'][s], 2) for s in jn.SOURCES) + f" | {_d(e['d_true'], 1)} | "
                     f"{_d(r['2-aus-4']['y'])} | {_d(r['3-aus-4']['y'])} | " + " / ".join(_d(a.get('y')) for a in ad) + " | " +
                     " / ".join(_d(a.get('erkannt')) for a in ad) + " | " + " / ".join(_d(a.get('erkannt_mild')) for a in ad) + " |")
    if tb.get("e2"):
        L += ["", "## E2 – Episoden nach Größe m: Anteil der Fehlalarmzeit, Episoden je Bett·h, mittlere Dauer [s]", "",
              "| h | Anteil m=1/2/3/4 | Episoden je Bett·h m=1/2/3/4 | Dauer m=1/2/3/4 | E2 2-aus-4 (zufällig + Episoden) | E2 3-aus-4 | E2 adaptiv je T |",
              "|---|---|---|---|---|---|---|"]
        for h, v in tb["e2"].items():
            e = v["eingaben"]
            r = v["ergebnisse"]

            def cell(k):
                x = r.get(k)
                return f"{_d(x['falsch_je_bett_h'], 2)} ({_d(x['zufaellig'], 2)} + {_d(x['episoden'], 2)})" if x else "–"
            L.append(f"| {h} | " + "/".join(_d(e['anteil'][m], 2) for m in (1, 2, 3, 4)) + " | " +
                     "/".join(_d(e['rate_je_bett_h'][m], 2) for m in (1, 2, 3, 4)) + " | " +
                     "/".join(_d(e['dauer_s'][m], 1) for m in (1, 2, 3, 4)) + f" | {cell('2-aus-4')} | {cell('3-aus-4')} | " +
                     " / ".join(cell(f"adaptiv_T{T:g}") for T in tb.get("Ts", [T_MAIN])) + " |")
    L += ["", "## Theorie gegen Messung je h (x = Variante ii; Faktor = Theorie/Simulation; ΔTPR = Theorie − Simulation; erkannt mild/streng)", ""]
    for key, lst in tb["vergleich_theorie"].items():
        L += [f"### {key}", "", "| h | x sim | x Theorie | Faktor | y sim | y Theorie | Δy | falsch/Bett·h sim | Theorie | erkannt sim | Theorie |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in lst:
            L.append(f"| {r['h']:g} | {_d(r['fpr_sim'], 5)} | {_d(r['fpr_theorie'], 5)} | {_d(r['fpr_faktor'], 2)} | {_d(r['tpr_sim'])} | "
                     f"{_d(r['tpr_theorie'])} | {_d(r['tpr_diff'], 3)} | {_d(r['falsch_sim'], 2)} | {_d(r['falsch_theorie'], 2)} | "
                     f"{_d(r['erkannt_sim'])}/{_d(r.get('erkannt_streng_sim'))} | {_d(r['erkannt_theorie'])}/{_d(r.get('erkannt_streng_theorie'))} |")
        L.append("")
    if tb.get("latch"):
        L += ["## Wirkung der Verriegelung bei k-aus-4 (Rot hält, bis alle Quellen aus sind; x = Variante i)", "",
              "| Regel | h | FPR ohne | FPR mit | TPR ohne | TPR mit | Ereignisse ohne | mit |", "|---|---|---|---|---|---|---|---|"]
        for name, lst in tb["latch"].items():
            for r in lst:
                L.append(f"| {name} | {r['h']:g} | {_d(r['fpr_ohne_latch'], 5)} | {_d(r['fpr_mit_latch'], 5)} | {_d(r['tpr_ohne_latch'])} | "
                         f"{_d(r['tpr_mit_latch'])} | {r['n_ohne']} | {r['n_mit']} |")
    return "\n".join(L) + "\n"


def input_check(sw: List[dict], pr: Optional[dict]) -> Optional[dict]:
    """Eingangsprüfung: h = 1,0 des Sweeps gegen die Eingangsraten des Netzlaufs (aus ``pruefung``)."""
    if not pr or "eingang_netz" not in pr:
        return None
    one = next((o for o in sw if abs(o["h"] - 1.0) < 1e-9), None)
    if one is None:
        return None
    netz = pr["eingang_netz"]
    out: dict = dict(quellen={}, krisen={})
    for s in jn.SOURCES:
        r = one["dev_rates"][s]
        off = r.n_false / one["hours"]
        nq = netz["quellen"][s]
        out["quellen"][s] = dict(offline=off, netz=nq["fehlalarme_je_h"], netz_ci=nq["ci"],
                                 ok=bool(nq["ci"][0] <= off <= nq["ci"][1]), eta_offline=one["etas"][s],
                                 eta_netz=nq["eta"], eta_ci=nq["eta_ci"],
                                 eta_ok=bool(nq["eta_ci"][0] <= one["etas"][s] <= nq["eta_ci"][1]))
    n_cr = one["rules"]["2-aus-4"].n_crises
    off = n_cr / one["bed_hours"]
    out["krisen"] = dict(offline=off, netz=netz["krisen_je_bett_h"], netz_ci=netz["krisen_ci"],
                         ok=bool(netz["krisen_ci"][0] <= off <= netz["krisen_ci"][1]))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Ziel 3 – ROC bei Variation der Gerätegrenzwerte")
    ap.add_argument("--nur", choices=["pruefung", "sweep", "tabellen", "abbildungen", "alles"], default="alles")
    ap.add_argument("--T", type=float, nargs="+", default=list(TS), help="Fenster T [s] für adaptiv")
    ap.add_argument("--grid-phase", type=float, default=0.5)
    ap.add_argument("--ohne-cache", action="store_true")
    ap.add_argument("--e1-n", type=int, default=100_000, help="Monte-Carlo-Krisen je Punkt (E1)")
    ap.add_argument("--n-boot", type=int, default=2000, help="Bootstrap-Züge für die Hülle")
    ap.add_argument("--roc-dir", type=Path, default=ROC_DIR)
    ap.add_argument("--pc1-dir", type=Path, default=PC1_DIR)
    ap.add_argument("--netz", default=NETZ_NAME, help="Ordner des Netzlaufs im ROC-Verzeichnis")
    ap.add_argument("--suffix", default="", help="Namenszusatz der Abbildungen (z. B. _p07)")
    ap.add_argument("--eta", default="0,9", help="η-Kennzeichnung für Tabellen und Legenden")
    ap.add_argument("--figdir", type=Path, default=FIG_DIR)
    ap.add_argument("--vergleich-dir", type=Path, default=None, help="zweites ROC-Verzeichnis für R2_vergleich (η = 0,7)")
    ap.add_argument("--alle-abbildungen", action="store_true", help="auch die unveränderten Abbildungen neu schreiben")
    args = ap.parse_args(argv)
    roc_dir: Path = args.roc_dir
    contract = dc.load_contract()
    raws = {float(T): dc.build_raw_clinical_db(contract, horizon_T=float(T)) for T in args.T}
    raw10 = raws.get(T_MAIN) or dc.build_raw_clinical_db(contract)
    use_cache = not args.ohne_cache
    tab_path = roc_dir / TAB_JSON
    csv_path = roc_dir / CSV_OUT
    pat_path = roc_dir / PATIENTS_JSON
    eta_label = f"η = {args.eta}"
    old_tb: dict = json.loads(tab_path.read_text(encoding="utf-8")) if tab_path.exists() else {}

    pr: Optional[dict] = old_tb.get("pruefung")
    if args.nur in ("pruefung", "alles", "sweep"):
        log(f"Prüfung durch Wiederholung ({args.netz}) …")
        pr = pruefung(roc_dir, args.pc1_dir, contract, raw10, args.grid_phase, use_cache, args.netz)
        z = pr["zuordnung"]
        log(f"Dashboard {z['n_real']} Rot, Replay {z['n_replay']} ({100 * z['rel_count_diff']:+.2f} %), "
            f"zugeordnet {z['matched']}, Median Δt {z['diff_median']:+.3f} s; Latenz ab Überschreiten Dashboard "
            f"{pr['raten_dashboard']['verz_kreuz_median_s']:.1f} s, Replay {pr['raten_replay']['verz_kreuz_median_s']:.1f} s")
        if abs(z["rel_count_diff"]) > 0.02:
            log("ABBRUCH: Abweichung > 2 % – siehe Aufgabe (anhalten und berichten).")
            (roc_dir / "pruefung_abbruch.json").write_text(json.dumps(pr, ensure_ascii=False, indent=1), encoding="utf-8")
            return 3
        if args.nur == "pruefung":
            (roc_dir / "pruefung.json").write_text(json.dumps(pr, ensure_ascii=False, indent=1), encoding="utf-8")
            return 0

    sw: Optional[List[dict]] = None
    extra: dict = {"profil_check": old_tb.get("profil_check"), "eta_label": eta_label, "n_boot": args.n_boot,
                   "eingang": old_tb.get("eingang")}
    if pat_path.exists():
        extra["patienten"] = json.loads(pat_path.read_text(encoding="utf-8"))
    if args.nur in ("sweep", "alles"):
        log(f"Sweep über h für T = {', '.join(f'{T:g}' for T in raws)} …")
        sw = sweep(roc_dir, contract, raws, args.grid_phase, use_cache, args.e1_n)
        original = read_csv(roc_dir / CSV_IN)
        rows = build_rows(original, sw, pr)
        write_csv(rows, csv_path)
        log(f"{csv_path.name}: {len(rows)} Zeilen ({len(original)} Originalzeilen unverändert)")
        extra["patienten"] = patients_json(sw)
        pat_path.write_text(json.dumps(extra["patienten"], ensure_ascii=False), encoding="utf-8")
        extra["eingang"] = input_check(sw, pr)
        log("Prüfung der Profilkalibrierung (h = 1, T = 10) …")
        extra["profil_check"] = profile_check(roc_dir, contract, args.grid_phase, use_cache)
        for name, v in extra["profil_check"].items():
            r = v["raten"]
            log(f"  {name}: Rot {v['n_rot']}, erkannt {r['erkannt']:.3f}/{r['erkannt_streng']:.3f}, "
                f"falsch {r['falsch_je_bett_h']:.2f}/Bett·h, y {r['tpr']:.3f}, x(ii) {r['fpr_ii']:.5f}")
    if not csv_path.exists():
        log(f"FEHLER: {csv_path} fehlt – zuerst --nur sweep oder alles.")
        return 2
    rows_num = load_rows(csv_path)
    if args.nur == "abbildungen" and old_tb:
        tb = old_tb
    else:
        log(f"Tabellen (Hülle mit {args.n_boot} Bootstrap-Zügen) …")
        tb = tables(rows_num, pr, sw, extra)
        if sw is None:
            for k in ("latch", "e1", "e2"):
                if old_tb.get(k):
                    tb[k] = old_tb[k]
        tab_path.write_text(json.dumps(tb, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
        (roc_dir / TAB_MD).write_text(tables_markdown(tb), encoding="utf-8")
        log(f"Tabellen: {tab_path.name}, {TAB_MD}")
        if tb.get("huelle"):
            for view, v in tb["huelle"].items():
                log("  " + v["satz"])
    if args.nur in ("abbildungen", "alles", "sweep"):
        from analysis.roc_ziel3 import figures_roc as fr
        written = fr.export_all(rows_num, tb, args.figdir, suffix=args.suffix, only_new=not args.alle_abbildungen)
        if args.vergleich_dir is not None:
            rows_b = load_rows(args.vergleich_dir / CSV_OUT)
            tb_b = json.loads((args.vergleich_dir / TAB_JSON).read_text(encoding="utf-8"))
            pair = [(rows_num, tb, eta_label), (rows_b, tb_b, tb_b.get("eta_label", "η = 0,7"))]
            pair.sort(key=lambda x: x[2], reverse=True)              # η = 0,9 links, η = 0,7 rechts
            written += fr.save(fr.fig_R2_vergleich(pair[0][0], pair[0][1], pair[1][0], pair[1][1], (pair[0][2], pair[1][2])),
                               args.figdir, fr.FILES["R2_vergleich"])
        log("Abbildungen: " + ", ".join(p.name for p in written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
