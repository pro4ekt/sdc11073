"""Zeit-ROC je Fenster T: was das Fenster des adaptiven Filters in der zeitbezogenen Metrik kostet.

TPR_sys (Zeit) = Zeit mit Rot in Krisen / Krisenzeit (Krisen einschließlich einer am Laufende offenen);
FPR_sys (Zeit) = Zeit falscher Rot-Episoden (berühren keine Krise ± 10 s) / stabile Zeit – Variante (ii), die
Hauptvariante der Anwendung (``metrics.RateResult.fpr_ii``).

Zerlegung der Krisenzeit (je Regel, h): jede (verschmolzene) Krise [s, e] fällt in genau einen der Teile
  (i)   nicht erfasst:            kein Rot in [s, e]                     → e − s
  (ii)  Wartezeit:                Rot in [s, e], erstes Rot bei f        → f − s
  (iii) Lücken nach erstem Rot:   (e − f) − Rot-Zeit in [f, e]
  (iv)  Rot:                      Rot-Zeit in [s, e]
Summe (i) + (ii) + (iii) + (iv) = Krisenzeit; (iv) / Krisenzeit = TPR_sys (geprüft in den Tests).

adaptiv: T ∈ {5; 8; 10; 15; 20; 30} s, α = 0,5, δ_min = 0,5, P0 = 0,5 (sonst Prototyp); Replays aus dem
Zwischenspeicher (``adaptiv_replay``), fehlende mit dem unveränderten Dashboard-Kern (``dashcore``).

    .venv\\Scripts\\python -m analysis.roc_ziel3.zeit_roc                 # rechnen (beide η) + Abbildungen Z1–Z3
    .venv\\Scripts\\python -m analysis.roc_ziel3.zeit_roc --nur abbildungen
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from analysis.belastungsmodell_app import export_figures as fx  # noqa: E402
from analysis.belastungsmodell_app.export_figures import de  # noqa: E402

from . import dashcore as dc  # noqa: E402
from . import figures_roc as fr  # noqa: E402
from . import journals as jn  # noqa: E402
from . import kalibrierung as kb  # noqa: E402
from . import metrics as mt  # noqa: E402
from . import run_roc as rr  # noqa: E402

Interval = Tuple[float, float]

T_WERTE: Tuple[float, ...] = (5.0, 8.0, 10.0, 15.0, 20.0, 30.0)
T_Z3: Tuple[float, ...] = (5.0, 10.0, 20.0)
K_RULES: Tuple[str, ...] = ("2-aus-4", "3-aus-4")
ALL_K: Tuple[str, ...] = ("1-aus-4", "2-aus-4", "3-aus-4", "4-aus-4")
ALPHA, DMIN, P0 = 0.5, 0.5, 0.5
H_WORK = 1.0
PAUC_MAX = 0.03
ETA_DIRS: Dict[str, Path] = {"0,9": rr.ROC_DIR, "0,7": rr.ROC_DIR_P07}
OUT_DIR = rr.ROC_DIR / "zeit_roc"
CSV_NAME = "zeit_roc.csv"
META_NAME = "zeit_roc.json"
FIG_DIR = rr.FIG_DIR / "zeit_roc"
FILES: Dict[str, str] = {"z1": "Z1_Zeit_ROC_T", "z1_zoom": "Z1_Zeit_ROC_T_zoom", "z2": "Z2_Fenster_Kosten", "z3": "Z3_Zerlegung_TPR"}
BASE = "#0F8A8A"
COLORS: Dict[str, str] = {"2-aus-4": "#E07B39", "3-aus-4": "#7E5BB5", "1-aus-4": "#1baf7a", "4-aus-4": "#e34948"}
GREY = fx.GREY
INK = fx.INK
FPR_VARIANTE = "ii"
X_LABEL = "FPR_sys nach Zeit (%), Variante ii"
Y_LABEL = "TPR_sys nach Zeit (%)"
WIE_LESEN = "Die Zeit-ROC misst Erkennung und Latenz zugleich: ein kürzeres Fenster schiebt den Filter nach oben, aber auch nach rechts."
FRAGE = "Was misst die Zeit-ROC?"
PARTS: Tuple[str, ...] = ("d_i", "d_ii", "d_iii", "d_iv")
PART_LABELS: Dict[str, str] = {"d_iv": "(iv) Rot während der Krise (= TPR_sys)", "d_ii": "(ii) Wartezeit bis zum ersten Rot",
                               "d_iii": "(iii) Lücken nach dem ersten Rot", "d_i": "(i) Krise ohne Rot"}
PART_COLORS: Dict[str, str] = {"d_ii": "#c9c9c4", "d_iii": "#8f8f8a", "d_i": "#4a4a46"}
CSV_FIELDS: Tuple[str, ...] = ("eta", "h", "regel", "T", "tpr", "fpr_ii", "fpr", "erkannt", "erkannt_streng", "n_krisen",
                               "n_falsch", "falsch_je_bett_h", "verz_kreuz_median_s", "verz_kreuz_p90_s", "t_krise_s",
                               "t_i_s", "t_ii_s", "t_iii_s", "t_iv_s", "d_i", "d_ii", "d_iii", "d_iv", "krise_mittel_s",
                               "n_rot", "bett_h")


# ─────────────────────────────────────────────────────────────────────────────
# Farben und Namen
# ─────────────────────────────────────────────────────────────────────────────

def _blend(c: str, other: Tuple[float, float, float], f: float) -> str:
    c = c.lstrip("#")
    r, g, b = (int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    rr_, gg, bb = (r + (other[0] - r) * f, g + (other[1] - g) * f, b + (other[2] - b) * f)
    return "#%02x%02x%02x" % (round(rr_ * 255), round(gg * 255), round(bb * 255))


def t_color(T: float) -> str:
    """Eine Skala von #0F8A8A: T = 10 s = Grundfarbe, kürzer heller (bis 55 % Weiß bei 5 s), länger dunkler (bis 60 % Schwarz bei 30 s)."""
    if abs(T - 10.0) < 1e-9:
        return BASE
    if T < 10.0:
        return _blend(BASE, (1.0, 1.0, 1.0), min(0.55, 0.55 * (10.0 - T) / 5.0))
    return _blend(BASE, (0.0, 0.0, 0.0), min(0.6, 0.6 * math.log(T / 10.0) / math.log(3.0)))


def rule_key(regel: str, T: Optional[float]) -> str:
    return f"adaptiv T{T:g}" if regel == "adaptiv" else regel


def rule_label(regel: str, T: Optional[float]) -> str:
    return f"adaptiv, T = {de(T)} s" if regel == "adaptiv" else regel


# ─────────────────────────────────────────────────────────────────────────────
# Rechnung
# ─────────────────────────────────────────────────────────────────────────────

def decompose(rot: Dict[str, Sequence[Interval]], truth: Dict[str, mt.PatientTruth]) -> Dict[str, float]:
    """Zerlegung der Krisenzeit in (i)–(iv), Zeiten in s; Krisen wie bei ``metrics.rate`` (geschlossene und offene, verschmolzen)."""
    t = dict(t_krise_s=0.0, t_i_s=0.0, t_ii_s=0.0, t_iii_s=0.0, t_iv_s=0.0)
    for p, tr in truth.items():
        merged = mt.union(mt.clip(rot.get(p, []), *tr.window))
        for s, e in mt.union(tr.crises + tr.open_crises):
            dur = e - s
            t["t_krise_s"] += dur
            inside = mt.intersect(merged, [(s, e)])
            if not inside:
                t["t_i_s"] += dur
                continue
            f = min(a for a, _ in inside)
            red = mt.total(inside)
            t["t_ii_s"] += f - s
            t["t_iv_s"] += red
            t["t_iii_s"] += (e - f) - red
    tc = t["t_krise_s"]
    for k in ("i", "ii", "iii", "iv"):
        t[f"d_{k}"] = t[f"t_{k}_s"] / tc if tc else float("nan")
    return t


def mean_crisis_s(truth: Dict[str, mt.PatientTruth]) -> float:
    d = [e - s for tr in truth.values() for s, e in tr.crises]
    return statistics.mean(d) if d else float("nan")


def evaluate(ctx: kb.Context, rot: Dict[str, Sequence[Interval]], regel: str, T: Optional[float], n_rot: Optional[int]) -> dict:
    r = mt.rate(rot, ctx.truth, ctx.bed_h)
    lat = mt.latencies(rot, ctx.truth, ctx.entries)
    row = dict(eta=ctx.eta, h=ctx.h, regel=regel, T=T, tpr=r.tpr, fpr_ii=r.fpr_ii, fpr=r.fpr, erkannt=r.detected,
               erkannt_streng=r.detected_strict, n_krisen=r.n_crises, n_falsch=r.n_false, falsch_je_bett_h=r.false_per_bed_h,
               verz_kreuz_median_s=lat.median("cross"), verz_kreuz_p90_s=lat.p90("cross"), krise_mittel_s=mean_crisis_s(ctx.truth),
               n_rot=n_rot if n_rot is not None else r.n_events, bett_h=ctx.bed_h)
    row.update(decompose(rot, ctx.truth))
    return row


def compute(etas: Sequence[str] = ("0,9", "0,7"), Ts: Sequence[float] = T_WERTE, log=rr.log, use_cache: bool = True) -> List[dict]:
    contract = dc.load_contract()
    rows: List[dict] = []
    for eta in etas:
        roc_dir = ETA_DIRS[eta]
        for h, folder in jn.h_folders(roc_dir):
            t0 = time.perf_counter()
            ctx = kb.load_context(folder, eta, h)
            for k in (1, 2, 3, 4):
                rot = mt.k_of_n_rot(ctx.src, ctx.j.patients, k)
                rows.append(evaluate(ctx, rot, f"{k}-aus-4", None, None))
            for T in Ts:
                rot, meta = kb.replay(ctx, contract, (float(T), ALPHA, DMIN), P0, cache_dir=roc_dir / rr.CACHE, use_cache=use_cache)
                rows.append(evaluate(ctx, rot, "adaptiv", float(T), meta.get("n_rot")))
            log(f"[{eta}] h = {h:g}: {len(Ts) + 4} Regeln, {time.perf_counter() - t0:.0f} s")
    return rows


def write(rows: Sequence[dict], out_dir: Path = OUT_DIR, meta: Optional[dict] = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / CSV_NAME
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(CSV_FIELDS), delimiter=";", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (r.get(k) if k in ("eta", "regel") else rr.fmt(r.get(k))) for k in CSV_FIELDS})
    if meta is not None:
        (out_dir / META_NAME).write_text(json.dumps(meta, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    return p


def load(out_dir: Path = OUT_DIR) -> List[dict]:
    p = Path(out_dir) / CSV_NAME
    if not p.exists():
        return []
    out = []
    with open(p, encoding="utf-8") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            out.append({k: (v if k in ("eta", "regel") else kb._num(v)) for k, v in r.items()})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Auswahl und Kennzahlen
# ─────────────────────────────────────────────────────────────────────────────

def _same(a: Optional[float], b: Optional[float]) -> bool:
    return a is not None and b is not None and abs(float(a) - float(b)) < 1e-9


def curve(rows: Sequence[dict], eta: str, regel: str, T: Optional[float] = None) -> List[dict]:
    sel = [r for r in rows if r.get("eta") == eta and r.get("regel") == regel and r.get("h") is not None
           and (regel != "adaptiv" or _same(r.get("T"), T))]
    return sorted(sel, key=lambda r: r["h"])


def at_h(rows: Sequence[dict], eta: str, regel: str, T: Optional[float] = None, h: float = H_WORK) -> Optional[dict]:
    return next((r for r in curve(rows, eta, regel, T) if _same(r["h"], h)), None)


def pauc(sel: Sequence[dict], fmax: float = PAUC_MAX) -> float:
    """pAUC über FPR_sys ∈ [0, fmax] (Trapez, Anker (0, 0)), normiert auf fmax = mittlere TPR_sys im Bereich (``metrics.partial_auc``)."""
    return mt.partial_auc([r["fpr_ii"] for r in sel if r.get("fpr_ii") is not None and r.get("tpr") is not None],
                          [r["tpr"] for r in sel if r.get("fpr_ii") is not None and r.get("tpr") is not None], fmax)


def series(Ts: Sequence[float] = T_WERTE, rules: Sequence[str] = K_RULES) -> List[Tuple[str, Optional[float]]]:
    return [(r, None) for r in rules] + [("adaptiv", float(T)) for T in Ts]


def table_rows(rows: Sequence[dict], etas: Sequence[str], Ts: Sequence[float] = T_WERTE, rules: Sequence[str] = K_RULES) -> List[dict]:
    """Je η und Kurve: pAUC (FPR_sys ≤ 3 %) und bei h = 1 TPR_sys, FPR_sys, erkannte Krisen, Fehleskalationen, Latenz."""
    out = []
    for eta in etas:
        for regel, T in series(Ts, rules):
            sel = curve(rows, eta, regel, T)
            if not sel:
                continue
            p1 = at_h(rows, eta, regel, T)
            out.append({"η": eta, "Kurve": rule_label(regel, T),
                        "pAUC (FPR_sys ≤ 3 %)": de(pauc(sel), 3),
                        "TPR_sys bei h = 1": de(100 * p1["tpr"], 1) + " %" if p1 else "—",
                        "FPR_sys bei h = 1": de(100 * p1["fpr_ii"], 2) + " %" if p1 else "—",
                        "erkannt mild": de(100 * p1["erkannt"], 1) + " %" if p1 else "—",
                        "Fehlesk. je Bett·h": de(p1["falsch_je_bett_h"], 2) if p1 else "—",
                        "Latenz Median [s]": de(p1["verz_kreuz_median_s"], 1) if p1 else "—"})
    return out


def zerlegung_rows(rows: Sequence[dict], etas: Sequence[str], Ts: Sequence[float] = T_WERTE, h: float = H_WORK) -> List[dict]:
    out = []
    for eta in etas:
        for regel, T in series(Ts, ALL_K):
            r = at_h(rows, eta, regel, T, h)
            if r is None:
                continue
            out.append({"η": eta, "Regel": rule_label(regel, T),
                        **{PART_LABELS[k]: de(100 * r[k], 1) + " %" for k in ("d_iv", "d_ii", "d_iii", "d_i")},
                        "TPR_sys": de(100 * r["tpr"], 1) + " %",
                        "Summe": de(100 * sum(r[k] for k in PARTS), 1) + " %",
                        "mittlere Krise [s]": de(r["krise_mittel_s"], 1)})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Abbildungen
# ─────────────────────────────────────────────────────────────────────────────

def _legend_below(fig: Figure, handles: Sequence, ncol: int, fontsize: float = 7.0, extra_in: float = 0.55, top: float = 0.93,
                  left: float = 0.09, right: float = 0.985, wspace: float = 0.18, hspace: float = 0.35) -> None:
    leg = fig.legend(handles=handles, loc="lower center", ncol=ncol, frameon=False, fontsize=fontsize, bbox_to_anchor=(0.5, 0.01),
                     handlelength=2.4, columnspacing=1.2)
    fig.subplots_adjust(left=left, right=right, top=top, bottom=0.3, wspace=wspace, hspace=hspace)
    fig.canvas.draw()
    y1 = leg.get_window_extent().transformed(fig.transFigure.inverted()).y1
    fig.subplots_adjust(left=left, right=right, top=top, bottom=min(0.6, y1 + extra_in / fig.get_figheight()), wspace=wspace, hspace=hspace)


def _empty(ax, text: str) -> None:
    ax.text(0.5, 0.5, text, ha="center", va="center", transform=ax.transAxes, fontsize=8, color=GREY)


def fig_z1(rows: Sequence[dict], etas: Sequence[str] = ("0,9", "0,7"), Ts: Sequence[float] = T_WERTE,
           rules: Sequence[str] = K_RULES, zoom: bool = False, h_mark: float = H_WORK) -> Figure:
    """Zeit-ROC je Fenster T: TPR_sys über FPR_sys (%), Kurve über h je T und für 2-/3-aus-4; Raute bei h = h_mark."""
    etas = list(etas) or ["0,9"]
    fig, axes = plt.subplots(1, len(etas), figsize=(fx.W, (0.62 if len(etas) == 1 else 0.52) * fx.W), squeeze=False, sharey=False)
    for i, eta in enumerate(etas):
        ax = axes[0][i]
        drawn = False
        ys_vis: List[float] = []
        for regel, T in series(Ts, rules):
            sel = curve(rows, eta, regel, T)
            if not sel:
                continue
            drawn = True
            c = t_color(T) if regel == "adaptiv" else COLORS[regel]
            x = np.array([100 * r["fpr_ii"] for r in sel])
            y = np.array([100 * r["tpr"] for r in sel])
            ax.plot(x, y, color=c, lw=2.2 if regel == "adaptiv" else 1.9, marker="o", ms=2.4, mew=0, zorder=4 if regel == "adaptiv" else 3)
            ys_vis += [yy for xx, yy in zip(x, y) if xx <= 3.0]
            p = next((r for r in sel if _same(r["h"], h_mark)), None)
            if p is not None:
                ax.plot([100 * p["fpr_ii"]], [100 * p["tpr"]], marker="D", ms=6.0, color=c, mec="white", mew=0.9, zorder=8, ls="none")
        if not drawn:
            _empty(ax, "keine Zeit-ROC-Daten\n(python -m analysis.roc_ziel3.zeit_roc)")
        if zoom:
            ax.set_xlim(0.0, 3.0)
            lo = max(0.0, (min(ys_vis) if ys_vis else 0.0) - 5.0)
            ax.set_ylim(lo, 100.0)
        else:
            ax.set_xlim(0.0, None)
            ax.set_ylim(0.0, 100.0)
        ax.set_title(f"η = {eta}", fontsize=9)
        fx.de_axes(ax)
        fr._grid(ax)
        ax.set_xlabel(X_LABEL if i == 0 else "dasselbe", fontsize=8.0)
        if i == 0:
            ax.set_ylabel(Y_LABEL, fontsize=8.0)
    handles = [Line2D([], [], color=t_color(T), lw=2.2, marker="o", ms=2.4, mew=0, label=f"adaptiv, T = {de(T)} s") for T in Ts]
    handles += [Line2D([], [], color=COLORS[r], lw=1.9, marker="o", ms=2.4, mew=0, label=r) for r in rules]
    handles.append(Line2D([], [], color="white", marker="D", ms=6.0, mec=INK, mew=0.9, ls="none", label=f"Grenzwertfaktor h = {de(h_mark)}"))
    _legend_below(fig, handles, ncol=4, top=0.92 if len(etas) > 1 else 0.96)
    return fig


Z2_PANELS: Tuple[Tuple[str, str, float], ...] = (
    ("tpr", "(a) TPR_sys\nnach Zeit (%)", 100.0), ("erkannt", "(b) erkannte\nKrisen, mild (%)", 100.0),
    ("verz_kreuz_median_s", "(c) Latenz Median ab\nerstem Überschreiten (s)", 1.0), ("falsch_je_bett_h", "(d) Fehleskalationen\nje Bett·h", 1.0))
ETA_STYLE: Dict[str, Tuple[str, str]] = {"0,9": ("-", "o"), "0,7": ("--", "s")}


def fig_z2(rows: Sequence[dict], etas: Sequence[str] = ("0,9", "0,7"), Ts: Sequence[float] = T_WERTE, h: float = H_WORK) -> Figure:
    """Was das Fenster kostet (h = 1): vier Felder über T; adaptiv je η als Linie, 2-/3-aus-4 als waagrechte Linien."""
    fig, axes = plt.subplots(1, 4, figsize=(fx.W, 0.42 * fx.W))
    for ax, (key, title, sc) in zip(axes, Z2_PANELS):
        for eta in etas:
            ls, mk = ETA_STYLE.get(eta, ("-", "o"))
            pts = [(T, at_h(rows, eta, "adaptiv", T, h)) for T in Ts]
            pts = [(T, r[key] * sc) for T, r in pts if r is not None and r.get(key) is not None]
            if pts:
                ax.plot([p[0] for p in pts], [p[1] for p in pts], color=BASE, lw=1.6, ls=ls, zorder=3)
                for T, v in pts:
                    ax.plot([T], [v], marker=mk, ms=5.0, color=t_color(T), mec=INK if abs(T - 10) < 1e-9 else "white", mew=0.7, zorder=5)
            for regel in K_RULES:
                r = at_h(rows, eta, regel, None, h)
                if r is not None and r.get(key) is not None:
                    ax.axhline(r[key] * sc, color=COLORS[regel], lw=1.2, ls=ls, zorder=2)
        ax.set_title(title, fontsize=7.6)
        major = [T for T in Ts if abs(T - 8.0) > 1e-9] or list(Ts)      # 8 s nur als kleiner Teilstrich (neben 10 s zu eng)
        ax.set_xticks(major)
        ax.set_xticklabels([de(T) for T in major], fontsize=6.6)
        ax.set_xticks([T for T in Ts if T not in major], minor=True)
        ax.tick_params(axis="y", labelsize=6.8)
        ax.set_xlabel("Fenster T (s)", fontsize=7.6)
        fx.de_axes(ax, x=False, y=True)
        fr._grid(ax)
    handles = [Line2D([], [], color=BASE, lw=1.6, ls=ETA_STYLE[e][0], marker=ETA_STYLE[e][1], ms=5, label=f"adaptiv, η = {e}") for e in etas]
    handles += [Line2D([], [], color=COLORS[r], lw=1.2, ls=ETA_STYLE[e][0], label=f"{r}, η = {e}") for r in K_RULES for e in etas]
    handles += [Line2D([], [], color="white", marker="o", ms=5, mfc=t_color(T), mec="white", ls="none", label=f"T = {de(T)} s") for T in Ts]
    _legend_below(fig, handles, ncol=5, fontsize=6.6, extra_in=0.62, top=0.84, left=0.06, wspace=0.5)
    return fig


def fig_z3(rows: Sequence[dict], etas: Sequence[str] = ("0,9", "0,7"), Ts: Sequence[float] = T_Z3, h: float = H_WORK) -> Figure:
    """Zerlegung der Krisenzeit (h = 1): je Regel eine Säule (iv) Rot | (ii) Wartezeit | (iii) Lücken | (i) nicht erfasst."""
    fig, axes = plt.subplots(1, len(etas), figsize=(fx.W, 0.46 * fx.W), squeeze=False, sharey=True)
    labels = [rule_label(r, T) for r, T in series(Ts, K_RULES)]
    for i, eta in enumerate(etas):
        ax = axes[0][i]
        ypos = np.arange(len(labels))[::-1]
        krise = None
        for y, (regel, T) in zip(ypos, series(Ts, K_RULES)):
            r = at_h(rows, eta, regel, T, h)
            if r is None:
                continue
            krise = r.get("krise_mittel_s")
            left = 0.0
            for k in ("d_iv", "d_ii", "d_iii", "d_i"):
                v = 100.0 * r[k]
                c = (t_color(T) if regel == "adaptiv" else COLORS[regel]) if k == "d_iv" else PART_COLORS[k]
                ax.barh([y], [v], left=[left], color=c, height=0.66, edgecolor="white", linewidth=0.5)
                if v >= 6.0:
                    ax.text(left + v / 2, y, de(v, 0 if v >= 10 else 1), ha="center", va="center", fontsize=6.4,
                            color="white" if k in ("d_iv", "d_i", "d_iii") else INK)
                left += v
        ax.set_yticks(ypos)
        ax.set_yticklabels(labels, fontsize=7.4)
        ax.set_xlim(0, 100)
        ax.set_title(f"η = {eta}" + (f" (mittlere Krise {de(krise, 1)} s)" if krise else ""), fontsize=8.4)
        ax.set_xlabel(f"Anteil der Krisenzeit (%), h = {de(h)}", fontsize=8.0)
        fx.de_axes(ax, y=False)
        ax.yaxis.grid(False)
    handles = [Patch(color=BASE, label=PART_LABELS["d_iv"] + " – Farbe der Regel")]
    handles += [Patch(color=PART_COLORS[k], label=PART_LABELS[k]) for k in ("d_ii", "d_iii", "d_i")]
    _legend_below(fig, handles, ncol=2, fontsize=7.0, extra_in=0.5, top=0.9, left=0.15, wspace=0.08)
    return fig


def save(fig: Figure, stem: str, outdir: Path = FIG_DIR) -> List[Path]:
    return fr.save(fig, Path(outdir), stem)


def export_all(rows: Sequence[dict], outdir: Path = FIG_DIR, overwrite: bool = False) -> List[Path]:
    """Z1 (ganz, Zoom), Z2, Z3 nach ``outdir``; vorhandene Dateien bleiben, wenn ``overwrite`` False ist."""
    written: List[Path] = []
    for key, make in (("z1", lambda: fig_z1(rows)), ("z1_zoom", lambda: fig_z1(rows, zoom=True)),
                      ("z2", lambda: fig_z2(rows)), ("z3", lambda: fig_z3(rows))):
        if not overwrite and (Path(outdir) / f"{FILES[key]}.png").exists():
            continue
        fig = make()
        written += save(fig, FILES[key], outdir)
        plt.close(fig)
    return written


# ─────────────────────────────────────────────────────────────────────────────
# Kennzahlen für Bericht und Text
# ─────────────────────────────────────────────────────────────────────────────

def catch_up_T(rows: Sequence[dict], eta: str, ref: str = "3-aus-4", key: str = "tpr", Ts: Sequence[float] = T_WERTE) -> Optional[float]:
    """Kleinstes T-Intervall-Ende: größtes T, bei dem adaptiv (h = 1) den Wert von ``ref`` erreicht (None, wenn keines)."""
    r0 = at_h(rows, eta, ref)
    if r0 is None:
        return None
    ok = [T for T in Ts if (r := at_h(rows, eta, "adaptiv", T)) is not None and r[key] >= r0[key]]
    return max(ok) if ok else None


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Zeit-ROC je Fenster T (Z1–Z3).")
    ap.add_argument("--nur", choices=["rechnen", "abbildungen"], default=None)
    ap.add_argument("--ueberschreiben", action="store_true", help="vorhandene Z-Abbildungen ersetzen")
    args = ap.parse_args(argv)
    t0 = time.perf_counter()
    if args.nur in (None, "rechnen"):
        rows = compute()
        write(rows, meta=dict(T=list(T_WERTE), alpha=ALPHA, delta_min=DMIN, P0=P0, fpr_variante=FPR_VARIANTE,
                              pauc_fmax=PAUC_MAX, pauc_norm="auf fmax normiert (mittlere TPR_sys im Bereich)",
                              rechenzeit_s=round(time.perf_counter() - t0, 1), erstellt=datetime.now().isoformat(timespec="seconds")))
    rows = load()
    for p in export_all(rows, overwrite=args.ueberschreiben):
        print("geschrieben:", p)
    rr.log(f"zeit_roc: {time.perf_counter() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
