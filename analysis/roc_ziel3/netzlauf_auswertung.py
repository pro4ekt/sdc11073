"""Auswertung eines Netzlaufs (Ziel 3) aus den Journalen A/B von PC 2 und ``decisions.jsonl`` von PC 1.

Erzeugt ``results_<lauf>.json`` und ``messwerte_<lauf>.json`` im Schema der PC-2-Auswertung (``docs/reports``), damit
die Reiter „Experimente“ und „Vergleich“ (``sim_data``) weitere Läufe lesen können – dieselbe Verarbeitung wie für
1 h / 6 h / 12 h: adaptiv aus dem Dashboard-Journal (Rot-Abschnitte, Uhr PC 1 → PC 2 über ``export/clock.csv``),
k-aus-4 aus dem Alarmjournal B (verriegelt), erkannt mild (Rot berührt [Beginn, Ende + 10 s]) und streng
([Beginn, Ende]), Fehleskalationen je Bett·h mit Poisson-Intervall, erkannte Anteile mit Clopper-Pearson-Intervall.

Messwerte wie PC 2: FPR_j = Zeit der Fehlalarme der Quelle j (berühren keine Krise ± 10 s) / Zeit außerhalb von
Krisen; TPR_j = Alarmzeit in Krisen / Krisenzeit; P(C) = Krisenzeit / (Patienten · Fenster); d_false, d_true;
Episoden = Vereinigung überlappender Fehlalarme eines Patienten; c_art_a/b = Anteil der Fehlalarmzeit in Episoden
mit allen 4 bzw. ≥ 2 Quellen.

    .venv\\Scripts\\python -m analysis.roc_ziel3.netzlauf_auswertung --netz-dir experiments/ziel3_simulation/logs/ziel3_roc_p07/netz_ziel3_4h_p07 ^
        --pc1-dir experiments/ziel3_simulation/logs/ziel3_4h_p07/pc1 --out docs/reports
    .venv\\Scripts\\python -m analysis.roc_ziel3.netzlauf_auswertung --netz-dir …/ziel3_roc/netz_ziel3_12h --pc1-dir …/ziel3_12h/pc1 ^
        --vergleich docs/reports/results_ziel3_12h.json --nicht-schreiben        # Kontrolle gegen die Auswertung von PC 2
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from analysis.belastungsmodell_app import sim_data as sd

from . import journals as jn
from . import metrics as mt
from . import run_roc as rr

GUARD = mt.GUARD_S
RULE_KEYS: Dict[int, str] = {1: "1-aus-4", 2: "2-aus-4", 3: "3-aus-4", 4: "4-aus-4"}
SRC_LABEL = jn.SOURCE_LABEL                       # ecg → ECG, …
Interval = Tuple[float, float]


# ─────────────────────────────────────────────────────────────────────────────
# Statistik
# ─────────────────────────────────────────────────────────────────────────────

def binom_cdf(k: int, n: int, p: float) -> float:
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    # logarithmisch summieren (n bis einige Tausend)
    lp, lq = math.log(p), math.log1p(-p)
    total = 0.0
    for i in range(k + 1):
        total += math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq)
    return min(1.0, total)


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> Tuple[float, float]:
    """Exaktes zweiseitiges Intervall des Anteils k/n (Bisektion über die Binomialverteilung)."""
    if n <= 0:
        return float("nan"), float("nan")

    def solve(target_fn, lo: float, hi: float) -> float:
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            if target_fn(mid) > 0:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    lo = 0.0 if k == 0 else solve(lambda p: (1.0 - binom_cdf(k - 1, n, p)) - alpha / 2, 0.0, 1.0)
    # für die untere Grenze: P(X ≥ k | p) steigt mit p; gesucht p mit P(X ≥ k) = α/2 → Funktion fällt mit kleinerem p
    if k > 0:
        lo = solve(lambda p: alpha / 2 - (1.0 - binom_cdf(k - 1, n, p)), 0.0, 1.0)
    hi = 1.0 if k == n else solve(lambda p: binom_cdf(k, n, p) - alpha / 2, 0.0, 1.0)
    return lo, hi


# ─────────────────────────────────────────────────────────────────────────────
# Auswertung
# ─────────────────────────────────────────────────────────────────────────────

def _clock_stats(pc1_dir: Path, rtt_max_ms: float = 30.0) -> dict:
    path = Path(pc1_dir) / "export" / "clock.csv"
    offs_all: List[float] = []
    offs_used: List[float] = []
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            for row in csv.reader(fh, delimiter=";"):
                if not row or row[0].startswith("#") or row[0] == "t_pc1":
                    continue
                try:
                    off, rtt = float(row[1]), float(row[2])
                except (ValueError, IndexError):
                    continue
                offs_all.append(off)
                if rtt <= rtt_max_ms:
                    offs_used.append(off)
    return dict(points_all=len(offs_all), points_used=len(offs_used),
                offset_min=min(offs_used) if offs_used else None, offset_max=max(offs_used) if offs_used else None)


def _false_device_alarms(src: Dict[str, Dict[str, List[Interval]]], truth: Dict[str, mt.PatientTruth],
                         guard: float = GUARD) -> Dict[str, List[Tuple[str, float, float]]]:
    """Je Quelle die Fehlalarme (ganze ON-Abschnitte, die keine Krise ± guard berühren): (Patient, a, b)."""
    out: Dict[str, List[Tuple[str, float, float]]] = {s: [] for s in jn.SOURCES}
    for p, tr in truth.items():
        windows = mt.pad(tr.crises + tr.open_crises, guard, guard)
        for s in jn.SOURCES:
            for a, b in src[s].get(p, []):
                if b > a and not mt.touches(windows, a, b):
                    out[s].append((p, a, b))
    return out


def _false_rot(rot: Dict[str, Sequence[Interval]], truth: Dict[str, mt.PatientTruth], guard: float = GUARD) -> List[float]:
    """Startzeiten der falschen Rot-Abschnitte (keine Krise ± guard berührt), im Fenster."""
    starts: List[float] = []
    for p, tr in truth.items():
        windows = mt.pad(tr.crises + tr.open_crises, guard, guard)
        for a, b in sorted(mt.clip(rot.get(p, []), *tr.window)):
            if not mt.touches(windows, a, b):
                starts.append(a)
    return starts


def _rule_dict(r: mt.RateResult, lat: mt.Latency, bed_h: float) -> dict:
    n_det, n_strict, n_cr = r.n_detected, r.n_detected_strict, r.n_crises
    ci_m = clopper_pearson(n_det, n_cr)
    ci_s = clopper_pearson(n_strict, n_cr)
    delays = list(lat.from_cross)
    return dict(
        n_escalations_window=r.n_events, n_false=r.n_false, caught=n_det, n_crises=n_cr, caught_strict=n_strict,
        caught_after_end=n_det - n_strict, delays=delays, delays_from_start=list(lat.from_start),
        median_delay_s=(statistics.median(delays) if delays else None),
        false_per_bed_h=r.false_per_bed_h, false_per_bed_h_ci=list(sd.poisson_ci(r.n_false, bed_h)),
        missed=n_cr - n_det, caught_frac=(n_det / n_cr if n_cr else None), caught_frac_ci=list(ci_m),
        missed_pct_ci=[100.0 * (1.0 - ci_m[1]), 100.0 * (1.0 - ci_m[0])],
        caught_strict_frac=(n_strict / n_cr if n_cr else None), caught_strict_frac_ci=list(ci_s),
        tpr_time=r.tpr, fpr_time=r.fpr, fpr_ii_time=r.fpr_ii, tail_share=r.tail_share,
    )


def auswerten(netz_dir: Path, pc1_dir: Path, name: Optional[str] = None, guard: float = GUARD) -> Tuple[dict, dict]:
    """(results, messwerte) eines Netzlaufs im Schema von docs/reports."""
    netz_dir, pc1_dir = Path(netz_dir), Path(pc1_dir)
    j = jn.load_journals(netz_dir)
    name = name or j.name
    truth = mt.truth_from_journals(j, guard)
    src = mt.source_intervals(j)
    entries = mt.crisis_entries(src, truth)
    hours, patients, bed_h = j.hours, len(j.patients), j.bed_hours
    t0, t1 = j.t_start, j.t_end

    # Rot je Regel
    offset = rr.clock_offset_fn(pc1_dir)
    rot_ad = rr.intervals(rr.real_rot(pc1_dir, offset))
    rots: Dict[str, Dict[str, List[Interval]]] = {RULE_KEYS[k]: mt.k_of_n_rot(src, j.patients, k) for k in (1, 2, 3, 4)}
    rots["adaptiv"] = {p: list(v) for p, v in rot_ad.items()}
    rules: Dict[str, dict] = {}
    false_starts: Dict[str, List[float]] = {}
    for rname, rot in rots.items():
        r = mt.rate(rot, truth, bed_h, guard)
        lat = mt.latencies(rot, truth, entries, guard)
        rules[rname] = _rule_dict(r, lat, bed_h)
        false_starts[rname] = _false_rot(rot, truth, guard)

    # Geräte: Fehlalarme, FPR_j, TPR_j
    false_dev = _false_device_alarms(src, truth, guard)
    crisis_time = sum(mt.total(mt.union(tr.crises + tr.open_crises)) for tr in truth.values())
    outside = sum((tr.window[1] - tr.window[0]) - mt.total(mt.union(tr.crises + tr.open_crises)) for tr in truth.values())
    fpr = {SRC_LABEL[s]: (sum(b - a for _p, a, b in false_dev[s]) / outside if outside else float("nan")) for s in jn.SOURCES}
    tpr = {SRC_LABEL[s]: mt.rate(src[s], truth, bed_h, guard).tpr for s in jn.SOURCES}
    all_false = [(s, p, a, b) for s in jn.SOURCES for p, a, b in false_dev[s]]
    n_false_alarms = len(all_false)
    d_false = statistics.fmean(b - a for _s, _p, a, b in all_false) if all_false else float("nan")
    closed = [e for tr in truth.values() for e in tr.crises]
    n_crises = len(closed)
    d_true = statistics.fmean(e - s for s, e in closed) if closed else float("nan")
    n_open = sum(len(tr.open_crises) for tr in truth.values())
    episodes = mt.false_episodes(src, truth, guard)
    t_eps = sum(e.t_sources for e in episodes)
    by_m = {str(m): sum(1 for e in episodes if e.m == m) for m in (1, 2, 3, 4)}
    n_eps = len(episodes)
    messwerte = dict(
        name=name, time_base="PC 2 (Journale A/B; Dashboard-Zeiten über export/clock.csv umgerechnet)",
        window=[t0, t1], hours=hours, patients=patients, exposure_bed_h=bed_h,
        FPR=fpr, TPR=tpr, P_C=(crisis_time / (patients * (t1 - t0)) if t1 > t0 else float("nan")),
        d_false_s=d_false, d_true_s=d_true, n_false_alarms=n_false_alarms, n_crises=n_crises,
        crises_per_bed_day=(n_crises / bed_h * 24.0 if bed_h else float("nan")),
        false_alarms_per_source_day={SRC_LABEL[s]: len(false_dev[s]) / (patients * hours) * 24.0 for s in jn.SOURCES},
        episodes_by_n_sources=by_m,
        episodes_by_n_sources_share={m: (v / n_eps if n_eps else float("nan")) for m, v in by_m.items()},
        c_art_a_all4_time_share=(sum(e.t_sources for e in episodes if e.m == 4) / t_eps if t_eps else float("nan")),
        c_art_b_ge2_time_share=(sum(e.t_sources for e in episodes if e.m >= 2) / t_eps if t_eps else float("nan")),
        c_art_a_all4_episode_share=(by_m["4"] / n_eps if n_eps else float("nan")),
        c_art_b_ge2_episode_share=(sum(by_m[m] for m in ("2", "3", "4")) / n_eps if n_eps else float("nan")),
        definitions={
            "FPR_j": "time of false alarms of source j (not intersecting any crisis +-10 s) / time outside crises, window only",
            "TPR_j": "time inside crises during which source j is in alarm / total crisis time",
            "P_C": "total crisis time / (patients x window)", "d_false_s": "mean duration of a false alarm",
            "d_true_s": "mean crisis duration",
            "episodes": "union of overlapping false alarms of one patient; n_sources = distinct sources in the episode",
            "c_art_a": "share of false alarm time that lies in episodes with all 4 sources",
            "c_art_b": "share of false alarm time that lies in episodes with >= 2 sources",
        },
        quelle="analysis/roc_ziel3/netzlauf_auswertung.py (PC 1) aus Journal A/B und decisions.jsonl",
        erstellt=datetime.now().isoformat(timespec="seconds"),
    )

    # Stundenwerte
    on_times = sorted(e.t for e in j.alarms if e.on and t0 <= e.t <= t1)
    false_dev_starts = sorted(a for _s, _p, a, _b in all_false)
    crisis_starts = sorted(s for s, _e in closed)
    hourly: List[dict] = []
    h = 0
    while t0 + 3600.0 * h < t1 - 1e-9:
        a, b = t0 + 3600.0 * h, min(t0 + 3600.0 * (h + 1), t1)
        hourly.append(dict(hour=h + 1, **{"from": a, "to": b}, duration_h=(b - a) / 3600.0,
                           alarms=sum(1 for t in on_times if a <= t < b),
                           false_alarms=sum(1 for t in false_dev_starts if a <= t < b),
                           crises=sum(1 for t in crisis_starts if a <= t < b),
                           false_esc={rn: sum(1 for t in fs if a <= t < b) for rn, fs in false_starts.items()}))
        h += 1
    results = dict(
        name=name, time_base=messwerte["time_base"], window=[t0, t1], hours=hours, patients=patients,
        exposure_bed_h=bed_h, excluded=[], n_alarms_excluded=0, n_alarms=len(on_times), n_crises_window=n_crises,
        n_crises_open=n_open, clock=_clock_stats(pc1_dir), notes=[], rules=rules, hourly=hourly,
        messwerte={k: messwerte[k] for k in ("hours", "patients", "exposure_bed_h", "FPR", "TPR", "P_C", "d_false_s",
                                             "d_true_s", "n_false_alarms", "n_crises")},
        quelle=messwerte["quelle"], erstellt=messwerte["erstellt"],
    )
    return results, messwerte


# ─────────────────────────────────────────────────────────────────────────────
# Schreiben und Vergleich
# ─────────────────────────────────────────────────────────────────────────────

def write(out_dir: Path, results: dict, messwerte: dict) -> Tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    p_res = out_dir / f"results_{results['name']}.json"
    p_mw = out_dir / f"messwerte_{messwerte['name']}.json"
    p_res.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    p_mw.write_text(json.dumps(messwerte, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    return p_res, p_mw


COMPARE_FIELDS: Tuple[str, ...] = ("n_escalations_window", "n_false", "caught", "caught_strict", "false_per_bed_h")


def compare(results: dict, ref_path: Path) -> List[dict]:
    """Abweichungen zu einer vorhandenen results-Datei (PC 2) je Regel und Feld."""
    ref = json.loads(Path(ref_path).read_text(encoding="utf-8"))
    out: List[dict] = []
    for rule, mine in results["rules"].items():
        theirs = ref.get("rules", {}).get(rule)
        if theirs is None:
            continue
        for f in COMPARE_FIELDS:
            a, b = mine.get(f), theirs.get(f)
            if a is None or b is None:
                continue
            out.append(dict(rule=rule, field=f, mine=a, ref=b, diff=a - b))
    out.append(dict(rule="-", field="n_alarms", mine=results["n_alarms"], ref=ref.get("n_alarms"),
                    diff=results["n_alarms"] - (ref.get("n_alarms") or 0)))
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Netzlauf aus Journalen A/B (PC 2) und decisions.jsonl (PC 1) auswerten.")
    ap.add_argument("--netz-dir", type=Path, required=True, help="Ordner mit alarms.jsonl / truth.jsonl (PC 2)")
    ap.add_argument("--pc1-dir", type=Path, required=True, help="Ordner mit decisions.jsonl und export/clock.csv (PC 1)")
    ap.add_argument("--name", default=None, help="Laufname (Standard: aus run_config)")
    ap.add_argument("--out", type=Path, default=sd.DATA_DIR, help="Zielordner der JSON-Dateien (Standard: docs/reports)")
    ap.add_argument("--vergleich", type=Path, default=None, help="results-Datei von PC 2 zum Vergleich")
    ap.add_argument("--nicht-schreiben", action="store_true")
    args = ap.parse_args(argv)
    results, messwerte = auswerten(args.netz_dir, args.pc1_dir, args.name)
    rr.log(f"{results['name']}: {results['hours']:.2f} h, {results['patients']} Patienten, {results['n_alarms']} Gerätealarme, "
           f"{results['n_crises_window']} Krisen")
    for rule in ("adaptiv", "2-aus-4", "3-aus-4", "1-aus-4", "4-aus-4"):
        r = results["rules"][rule]
        rr.log(f"  {rule:8s} falsch {r['false_per_bed_h']:.2f}/Bett·h  erkannt mild {100 * r['caught_frac']:.1f} % "
               f"streng {100 * r['caught_strict_frac']:.1f} %  Rot {r['n_escalations_window']}")
    if args.vergleich is not None:
        for d in compare(results, args.vergleich):
            flag = "" if abs(d["diff"]) <= (0.01 if d["field"] == "false_per_bed_h" else 0) else "  <-- Abweichung"
            rr.log(f"  Vergleich {d['rule']:8s} {d['field']:22s} {d['mine']:>10.4f} PC2 {d['ref']:>10.4f} Δ {d['diff']:+.4f}{flag}")
    if not args.nicht_schreiben:
        p_res, p_mw = write(args.out, results, messwerte)
        rr.log(f"geschrieben: {p_res}, {p_mw}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
