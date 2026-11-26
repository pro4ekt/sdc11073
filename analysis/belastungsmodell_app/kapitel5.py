"""Reiter „Kapitel 5 · Gesamtbild“: sieben Blöcke (Frage, Abbildung, Aussage, Export) aus bereits gerechneten Daten.

Datenquellen: ``sim_data`` (Läufe 1 h, 6 h, A, B, 12 h, C, D aus ``docs/reports``), ``analysis.roc_ziel3.werkstatt``
und ``figures_werkstatt`` (ROC bei variierten Gerätegrenzwerten, Konfigurationen T/α/δ_min) sowie die Latenzen aus
``results_<lauf>.json`` (Verzögerung ab dem ersten Überschreiten einer Gerätegrenze).  Kein Replay, kein Modell.
Farben: adaptiv #0F8A8A, 2-aus-4 #E07B39, 3-aus-4 #7E5BB5; Dezimalkomma; Export ohne Titel nach
``figures/ziel3/kapitel5/`` (PNG 300 dpi + SVG).
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

try:  # als Paketmodul (Tests, python -m) …
    from . import export_figures as fx
    from . import export_ziel3 as fz
    from . import sim_data as sd
except ImportError:  # … oder als Skript / unter streamlit run
    import export_figures as fx
    import export_ziel3 as fz
    import sim_data as sd

from analysis.roc_ziel3 import figures_roc as fr  # noqa: E402
from analysis.roc_ziel3 import figures_werkstatt as fw  # noqa: E402
from analysis.roc_ziel3 import kalibrierung as kb  # noqa: E402
from analysis.roc_ziel3 import metrics as mt  # noqa: E402
from analysis.roc_ziel3 import netzlauf_auswertung as na  # noqa: E402
from analysis.roc_ziel3 import run_roc as rr  # noqa: E402
from analysis.roc_ziel3 import werkstatt as ws  # noqa: E402

W = fx.W
de = fx.de
GREY = fx.GREY
INK = fx.INK
LIGHT_ALPHA = fz.LIGHT_ALPHA
FIG_DIR = fz.FIG_DIR / "kapitel5"

COLORS: Dict[str, str] = {"adaptiv": "#0F8A8A", "r2": "#E07B39", "r3": "#7E5BB5"}
NAMES: Dict[str, str] = {"r2": "2-aus-4", "adaptiv": "adaptiv", "r3": "3-aus-4"}
RULES: Tuple[str, ...] = ("r2", "adaptiv", "r3")           # Reihenfolge in Säulen
DELAY_KEYS: Dict[str, str] = {"adaptiv": "adaptiv", "r2": "2-aus-4", "r3": "3-aus-4"}
X_UNITS: Dict[str, str] = {"ereignis": "eskalierte Fehlalarm-Episoden (%)", "h": "je Bett und Stunde", "tag": "je Bett und Tag"}
PARAM_SETS: Tuple[str, ...] = fw.PARAM_SETS                 # "Prototyp" | "kalibriert"
NETZ_REGEL: Dict[str, str] = {"r2": "2-aus-4", "r3": "3-aus-4"}   # Regelname in netz_<key>.csv
HINWEIS = "Synthetische Daten: aussagekräftig ist der Vergleich der Regeln, nicht der absolute Wert."
FRAGEN: Dict[int, str] = {
    1: "Was kommt bei gleichen Geräten an?",
    2: "Was passiert in einem Lauf?",
    3: "Was, wenn weniger Geräte eine Krise anzeigen?",
    4: "Gilt das für jede Geräteeinstellung?",
    "4b": "Wie liegt die Schwelle des Filters gegen k-aus-4 (klassische ROC)?",
    5: "Was kostet der Filter?",
    6: "Hängt das an den Parametern?",
    7: "Wie hängt das System an den Geräten?",
}
FILES: Dict[int, str] = {
    1: "K5_1_Betriebspunkte", 2: "K5_2_Bilanz", 3: "K5_3_Eta", 4: "K5_4_FROC_Grenzwerte", "4b": "K5_4b_ROC_Filter",
    5: "K5_5_Kosten", 6: "K5_6_Parameter", 7: "K5_7_Geraet_System",
}
AUSSAGE_3 = ("3-aus-4 verliert Krisen, wenn wenige Geräte anzeigen; 2-aus-4 bleibt laut; der Filter bleibt nahe an "
             "2-aus-4 bei fast halber Fehllast.")


# ─────────────────────────────────────────────────────────────────────────────
# Hilfen
# ─────────────────────────────────────────────────────────────────────────────

def pct(v: float, digits: int = 1) -> str:
    return de(100.0 * v, digits) + " %"


def _point(ax, x: float, y: float, xci: Optional[Tuple[float, float]], yci: Optional[Tuple[float, float]], color: str,
           marker: str = "o", ms: float = 7.0, zorder: int = 6) -> None:
    xerr = None if xci is None else [[max(0.0, x - xci[0])], [max(0.0, xci[1] - x)]]
    yerr = None if yci is None else [[max(0.0, y - yci[0])], [max(0.0, yci[1] - y)]]
    ax.errorbar([x], [y], xerr=xerr, yerr=yerr, fmt=marker, color=color, mfc=color, mec="white", mew=0.8, ms=ms,
                ecolor=color, elinewidth=1.0, capsize=2.5, zorder=zorder)


def chord_gap_points(pts: Dict[str, dict]) -> Optional[dict]:
    """adaptiv gegen die Sehne 3-aus-4 → 2-aus-4 aus Punkten {r2, r3, adaptiv: {x, y}}: x, y, y_Sehne, Abstand in pp."""
    if not all(r in pts for r in ("adaptiv", "r2", "r3")):
        return None
    x2, y2 = pts["r2"]["x"], pts["r2"]["y"]
    x3, y3 = pts["r3"]["x"], pts["r3"]["y"]
    xa, ya = pts["adaptiv"]["x"], pts["adaptiv"]["y"]
    if x2 == x3:
        return None
    yc = y3 + (y2 - y3) * (xa - x3) / (x2 - x3)
    return dict(x=xa, y=ya, y_sehne=yc, gap_pp=ya - yc, x2=x2, y2=y2, x3=x3, y3=y3, innerhalb=min(x2, x3) <= xa <= max(x2, x3))


def chord_gap(run: sd.RunData, mode: str = "mild", x_unit: str = "h") -> Optional[dict]:
    """adaptiv gegen die Sehne 3-aus-4 → 2-aus-4 desselben Laufs (Zahlen des Laufs, nach Stück)."""
    pts = run_points(run, x_unit if x_unit in ("h", "tag") else "h", mode)
    return chord_gap_points(pts) if pts else None


def kal_config() -> Optional[Tuple[float, float, float]]:
    return kb.kalibriert_config()


def netz_rows(params: str = "Prototyp") -> Dict[str, List[dict]]:
    """Zeilen der Netzläufe (offline bei h = 1 auf den Journalen) je Lauf: netz_<Prototyp|kalibriert>.csv."""
    cfg = ws.BASE if params == "Prototyp" else kal_config()
    if cfg is None:
        return {}
    out: Dict[str, List[dict]] = {}
    for r in kb.load_netz(cfg):
        out.setdefault(str(r.get("eta")), []).append(r)
    return out


def run_points(run: sd.RunData, x_unit: str = "ereignis", mode: str = "mild", params: str = "Prototyp",
               netz: Optional[Dict[str, List[dict]]] = None) -> Optional[Dict[str, dict]]:
    """Betriebspunkte {r3, r2, adaptiv: {x, y, xci, yci}} eines Laufs.  Nach Stück mit Prototyp: Zahlen des Laufs
    (PC 2 bzw. Auswertung der Journale).  Ereignisbezogen oder mit „kalibriert“: aus den offline nachgerechneten Journalen
    (netz_<key>.csv; adaptiv = wirkliches Dashboard beim Prototyp, Replay bei kalibriert).  None, wenn Daten fehlen."""
    if not all(k in run.rules for k in ("adaptiv", "r2", "r3")):
        return None
    if x_unit in ("h", "tag") and params == "Prototyp":
        f = 24.0 if x_unit == "tag" else 1.0
        out = {}
        for rule in ("r3", "r2", "adaptiv"):
            r = run.rules[rule]
            ci = r.caught_ci(mode)
            out[rule] = dict(x=r.false_per_bed_h * f, y=100.0 * r.caught_frac(mode), xci=(r.false_ci[0] * f, r.false_ci[1] * f),
                             yci=(100 * ci[0], 100 * ci[1]))
        return out
    rows = (netz if netz is not None else netz_rows(params)).get(run.key)
    if not rows:
        return None
    out = {}
    for rule in ("r3", "r2", "adaptiv"):
        regel = ("adaptiv" if params == "kalibriert" else "Dashboard") if rule == "adaptiv" else NETZ_REGEL[rule]
        r = next((z for z in rows if z.get("regel") == regel), None)
        if r is None or not r.get("n_krisen"):
            return None
        n_k = int(r["n_krisen"])
        nd = int(r["n_erkannt" if mode == "mild" else "n_erkannt_streng"])
        yci = na.clopper_pearson(nd, n_k)
        if x_unit == "ereignis":
            if not r.get("ep_gesamt"):
                return None
            n, k = int(r["ep_gesamt"]), int(r["ep_eskaliert"])
            xci = na.clopper_pearson(k, n)
            out[rule] = dict(x=100.0 * k / n, y=100.0 * nd / n_k, xci=(100 * xci[0], 100 * xci[1]), yci=(100 * yci[0], 100 * yci[1]))
        else:
            f = 24.0 if x_unit == "tag" else 1.0
            lo, hi = sd.poisson_ci(int(r["n_falsch"]), float(r["bett_h"]))
            out[rule] = dict(x=float(r["falsch_je_bett_h"]) * f, y=100.0 * nd / n_k, xci=(lo * f, hi * f), yci=(100 * yci[0], 100 * yci[1]))
    return out


def delays_by_rule(run: sd.RunData, data_dir: Path = sd.DATA_DIR) -> Dict[str, List[float]]:
    """Verzögerungen ab dem ersten Überschreiten je Regel aus results_<lauf>.json (leer, wenn nicht vorhanden)."""
    _mw, res_path = sd.run_files(run.key, data_dir)
    if not Path(res_path).exists():
        return {}
    d = json.loads(Path(res_path).read_text(encoding="utf-8"))
    out: Dict[str, List[float]] = {}
    for rule, key in DELAY_KEYS.items():
        vals = d.get("rules", {}).get(key, {}).get("delays")
        if vals:
            out[rule] = [float(v) for v in vals]
    return out


def _q(values: Sequence[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))] if s else float("nan")


def _layout(fig: Figure, bottom: float, top: float = 0.97, left: float = 0.09, right: float = 0.985,
            wspace: float = 0.28, hspace: float = 0.35) -> None:
    fig.subplots_adjust(left=left, right=right, bottom=bottom, top=top, wspace=wspace, hspace=hspace)


def _legend_bottom(fig: Figure, handles: Sequence, ncol: int, fontsize: float = 7.2, y: float = 0.01,
                   extra_in: float = 0.46) -> float:
    """Legende unten; Rückgabe: benötigter unterer Rand (Anteil der Figurhöhe); ``extra_in`` = Platz für Teilstriche
    und x-Beschriftung in Zoll (zweizeilige Beschriftung: ≈ 0,65)."""
    leg = fig.legend(handles=handles, loc="lower center", ncol=ncol, frameon=False, fontsize=fontsize,
                     bbox_to_anchor=(0.5, y), handlelength=2.4, columnspacing=1.2)
    fig.canvas.draw()
    bb = leg.get_window_extent().transformed(fig.transFigure.inverted())
    return bb.y1 + extra_in / fig.get_figheight()


def _line_runs(runs: Sequence[sd.RunData]) -> List[sd.RunData]:
    return sorted([r for r in runs if r.spec.on_line and r.key in sd.D3_LINE_RUNS], key=lambda r: r.spec.tpr)


# ─────────────────────────────────────────────────────────────────────────────
# Block 1 – Betriebspunkte der Netzläufe
# ─────────────────────────────────────────────────────────────────────────────

def runs_with_points(runs: Sequence[sd.RunData], x_unit: str = "ereignis", mode: str = "mild", params: str = "Prototyp"
                     ) -> List[Tuple[sd.RunData, Dict[str, dict]]]:
    netz = netz_rows(params) if (x_unit == "ereignis" or params == "kalibriert") else {}
    out = []
    for r in runs:
        pts = run_points(r, x_unit, mode, params, netz)
        if pts:
            out.append((r, pts))
    return out


def fig_betriebspunkte(runs: Sequence[sd.RunData], x_unit: str = "ereignis", mode: str = "mild", params: str = "Prototyp") -> Figure:
    """Je Lauf ein Feld: 3-aus-4, 2-aus-4, adaptiv mit 95-%-Intervallen (x Poisson bzw. Clopper-Pearson, y Clopper-Pearson),
    Sehne zwischen 3-aus-4 und 2-aus-4 (Mischung der festen Regeln), Abstand von adaptiv zur Sehne in pp.  Standard-x:
    eskalierte Fehlalarm-Episoden in Prozent (nur Läufe mit Journalen auf PC 1); ``params`` = „kalibriert“ zeigt den offline
    nachgerechneten Filter."""
    sel = runs_with_points(runs, x_unit, mode, params)
    n = max(1, len(sel))
    ncol = min(n, 4)
    nrow = math.ceil(n / ncol)
    fig, axes = plt.subplots(nrow, ncol, figsize=(W, (0.62 if n == 1 else 0.42 * nrow + 0.22) * W), squeeze=False)
    if not sel:
        axes[0][0].text(0.5, 0.5, "keine Läufe mit den nötigen Daten", ha="center", va="center", transform=axes[0][0].transAxes,
                        fontsize=8.5, color=GREY)
    for i, (run, rp) in enumerate(sel):
        ax = axes[i // ncol][i % ncol]
        g = chord_gap_points(rp)
        pts = {}
        for rule in ("r3", "r2", "adaptiv"):
            q = rp[rule]
            x, y = q["x"], q["y"]
            _point(ax, x, y, q.get("xci"), q.get("yci"), COLORS[rule], ms=8.0 if rule == "adaptiv" else 6.5,
                   zorder=7 if rule == "adaptiv" else 6)
            pts[rule] = (x, y)
        if g is not None:
            lo = min(g["x3"], g["x"])
            hi = max(g["x2"], g["x"])
            yl = g["y3"] + (g["y2"] - g["y3"]) * (lo - g["x3"]) / (g["x2"] - g["x3"])
            yh = g["y3"] + (g["y2"] - g["y3"]) * (hi - g["x3"]) / (g["x2"] - g["x3"])
            ax.plot([lo, hi], [yl, yh], color=GREY, lw=1.0, ls=(0, (2, 2)), zorder=3)
            ax.plot([g["x"], g["x"]], [g["y_sehne"], g["y"]], color=COLORS["adaptiv"], lw=1.0, ls=":", zorder=4)
            ax.annotate(f"{g['gap_pp']:+.1f} pp\n{'über' if g['gap_pp'] >= 0 else 'unter'} der Linie".replace(".", ","),
                        xy=(g["x"], g["y"]), xytext=(8, -4), textcoords="offset points", fontsize=7.0,
                        color=COLORS["adaptiv"], ha="left", va="top")
        ys = [p[1] for p in pts.values()]
        ax.set_ylim(max(0.0, min(ys) - 18.0), 101.5)
        xs = [p[0] for p in pts.values()]
        ax.set_xlim(0, max(xs) * 1.25)
        ax.set_title(run.spec.label_tex, fontsize=8.5)
        if x_unit == "ereignis":
            ax.set_xlim(0, min(100.0, max(xs) * 1.25))
        fx.de_axes(ax)
        fr._grid(ax)
        if i % ncol == 0:
            ax.set_ylabel(f"erkannte Krisen (%), {mode}", fontsize=8.5)
        if i // ncol == nrow - 1 and i % ncol == min(ncol - 1, (min(n - (nrow - 1) * ncol, ncol) - 1) // 2):
            ax.set_xlabel(X_UNITS[x_unit] if x_unit == "ereignis" else "Fehleskalationen " + X_UNITS[x_unit], fontsize=8.5)
    for k in range(n, nrow * ncol):
        axes[k // ncol][k % ncol].axis("off")
    ad_label = "adaptiv" + (" kalibriert (offline, h = 1)" if params == "kalibriert" else "")
    handles = [Line2D([], [], marker="o", color=COLORS[r], ls="none", ms=6.5, label=ad_label if r == "adaptiv" else NAMES[r])
               for r in ("r2", "adaptiv", "r3")]
    handles.append(Line2D([], [], color=GREY, lw=1.0, ls=(0, (2, 2)), label="Mischung aus 3-aus-4 und 2-aus-4 (Sehne desselben Laufs)"))
    handles.append(Line2D([], [], color=INK, lw=1.0, label="Fehlerbalken: 95 % (" + ("Clopper-Pearson | Clopper-Pearson" if x_unit == "ereignis"
                                                                                       else "Poisson | Clopper-Pearson") + ")"))
    bottom = _legend_bottom(fig, handles, ncol=3)
    _layout(fig, bottom=min(bottom, 0.5), top=0.92, wspace=0.3, hspace=0.45)
    return fig


def aussage_1(runs: Sequence[sd.RunData], mode: str = "mild", x_unit: str = "ereignis", params: str = "Prototyp") -> str:
    parts, fehlt = [], []
    for r, rp in runs_with_points(runs, x_unit, mode, params):
        g = chord_gap_points(rp)
        if g is not None:
            parts.append(f"{r.spec.label} {g['gap_pp']:+.1f} pp".replace(".", ","))
    have = {r.key for r, _ in runs_with_points(runs, x_unit, mode, params)}
    fehlt = [r.spec.label for r in runs if r.key not in have]
    if not parts:
        return "Keine Läufe mit allen drei Regeln." + (f" Ohne Journale auf PC 1: {', '.join(fehlt)}." if fehlt else "")
    pos = all(p.split(" ")[-2].startswith("+") for p in parts)
    txt = ("Bei gleichen Geräten liegt adaptiv" + (" kalibriert" if params == "kalibriert" else "")
           + (" in jedem Lauf" if pos else " nicht in jedem Lauf") + " über der Sehne zwischen 3-aus-4 und 2-aus-4 ("
           + ("erkannte Krisen bei gleichem Anteil eskalierter Fehlalarm-Episoden" if x_unit == "ereignis"
              else "erkannte Krisen bei gleicher Zahl Fehlmeldungen") + f", {mode}): " + ", ".join(parts) + ".")
    if fehlt:
        txt += f" Ohne Journale auf PC 1 (keine ereignisbezogene Auswertung): {', '.join(fehlt)}."
    return txt


# ─────────────────────────────────────────────────────────────────────────────
# Block 2 – Bilanz eines Laufs in Stück
# ─────────────────────────────────────────────────────────────────────────────

def fig_bilanz(run: sd.RunData) -> Figure:
    """Links Fehlmeldungen (Gerätealarme ohne Krise; Fehleskalationen 2-aus-4, adaptiv, 3-aus-4), rechts die Krisen je
    Regel: rechtzeitig erkannt | erst nach Krisenende | verpasst."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(W, 0.5 * W), gridspec_kw=dict(width_ratios=[1.15, 1.0], wspace=0.55))
    labels = ["Gerätealarme\nohne Krise"] + [NAMES[r] for r in RULES]
    vals = [run.m.n_false_alarms] + [run.rules[r].n_false for r in RULES]
    cols = [GREY] + [COLORS[r] for r in RULES]
    ypos = np.arange(len(vals))[::-1]
    ax1.barh(ypos, vals, color=cols, height=0.62)
    for y, v in zip(ypos, vals):
        ax1.text(v + 0.012 * max(vals), y, fz.thousands(v), va="center", ha="left", fontsize=7.5, color=INK)
    ax1.set_yticks(ypos)
    ax1.set_yticklabels(labels, fontsize=8)
    ax1.set_xlim(0, max(vals) * 1.22)
    ax1.set_xlabel("Fehlmeldungen im Lauf (Anzahl)", fontsize=8.5)
    ax1.set_title("Fehlmeldungen", fontsize=9)
    ax1.yaxis.grid(False)
    fx.de_axes(ax1, y=False)
    fr._grid(ax1)
    ax1.yaxis.grid(False)
    K = run.m.n_crises
    for i, rule in enumerate(RULES):
        r = run.rules[rule]
        y = len(RULES) - 1 - i
        left = 0.0
        outside: List[Tuple[str, str]] = []
        for val, col, alpha in ((r.caught_strict, COLORS[rule], 1.0), (r.caught_after_end, COLORS[rule], LIGHT_ALPHA),
                                (r.missed, GREY, 0.7)):
            if val <= 0:
                continue
            ax2.barh(y, val, left=left, height=0.62, color=col, alpha=alpha, edgecolor="white", linewidth=0.4)
            if K and val / K >= 0.12:
                ax2.text(left + val / 2, y, str(val), ha="center", va="center", fontsize=7.5,
                         color="white" if alpha == 1.0 else INK)
            else:
                outside.append((str(val), col if col != GREY else INK))
            left += val
        if outside:
            ax2.text(K * 1.03, y, " · ".join(t for t, _c in outside), ha="left", va="center", fontsize=7.0, color=outside[0][1])
    ax2.set_yticks([len(RULES) - 1 - i for i in range(len(RULES))])
    ax2.set_yticklabels([NAMES[r] for r in RULES], fontsize=8)
    ax2.set_xlim(0, max(K, 1) * 1.32)
    ax2.set_xlabel(f"Krisen (K = {K})", fontsize=8.5)
    ax2.set_title(f"{K} Krisen je Regel", fontsize=9)
    fx.de_axes(ax2, y=False)
    fr._grid(ax2)
    ax2.yaxis.grid(False)
    handles = [Patch(facecolor=COLORS["adaptiv"], label="rechtzeitig erkannt (Rot beginnt vor Krisenende)"),
               Patch(facecolor=COLORS["adaptiv"], alpha=LIGHT_ALPHA, label="erst nach Krisenende (bis Ende + 10 s)"),
               Patch(facecolor=GREY, alpha=0.7, label="verpasst")]
    bottom = _legend_bottom(fig, handles, ncol=3, fontsize=7.0)
    _layout(fig, bottom=min(bottom, 0.5), top=0.90, left=0.16, wspace=0.55)
    return fig


def aussage_2(run: sd.RunData) -> str:
    ad, r2, r3 = run.rules["adaptiv"], run.rules["r2"], run.rules["r3"]
    K = run.m.n_crises
    return (f"{run.spec.label_eta}: {fz.thousands(run.m.n_false_alarms)} Gerätealarme ohne Krise werden zu "
            f"{fz.thousands(ad.n_false)} Fehleskalationen (adaptiv) statt {fz.thousands(r2.n_false)} (2-aus-4) bzw. "
            f"{fz.thousands(r3.n_false)} (3-aus-4); rechtzeitig erkannt {ad.caught_strict}/{K} Krisen "
            f"(2-aus-4 {r2.caught_strict}, 3-aus-4 {r3.caught_strict}), erst nach Krisenende {ad.caught_after_end}, "
            f"verpasst {ad.missed} (2-aus-4 {r2.missed}, 3-aus-4 {r3.missed}).")


# ─────────────────────────────────────────────────────────────────────────────
# Block 3 – erkannte Krisen und Fehleskalationen über η
# ─────────────────────────────────────────────────────────────────────────────

ETA_DODGE = {"r2": -0.008, "adaptiv": 0.0, "r3": 0.008}


def fig_eta(runs: Sequence[sd.RunData], mode: str = "mild") -> Figure:
    """Links erkannte Krisen (mode) über η_j, rechts Fehleskalationen je Bett·h über η_j; je Regel eine Linie durch die
    Läufe 6 h, A, B, 12 h (η 0,6 · 0,7 · 0,8 · 0,9) mit 95-%-Intervallen."""
    sel = _line_runs(runs)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(W, 0.5 * W), gridspec_kw=dict(wspace=0.32))
    for rule in RULES:
        rs = [r for r in sel if rule in r.rules]
        xs = [r.spec.tpr + ETA_DODGE[rule] for r in rs]
        ys = [100.0 * r.rules[rule].caught_frac(mode) for r in rs]
        fs = [r.rules[rule].false_per_bed_h for r in rs]
        if len(rs) >= 2:
            ax1.plot(xs, ys, color=COLORS[rule], lw=1.8, zorder=3)
            ax2.plot(xs, fs, color=COLORS[rule], lw=1.8, zorder=3)
        for r, x, y, fv in zip(rs, xs, ys, fs):
            ci = r.rules[rule].caught_ci(mode)
            _point(ax1, x, y, None, (100 * ci[0], 100 * ci[1]), COLORS[rule], marker=r.spec.marker, ms=7.0)
            _point(ax2, x, fv, None, r.rules[rule].false_ci, COLORS[rule], marker=r.spec.marker, ms=7.0)
    for ax in (ax1, ax2):
        ax.set_xlim(0.5, 1.0)
        fx.de_axes(ax)
        fr._grid(ax)
    ax1.set_xlabel(fz.axis_label("tpr"), fontsize=8.5)
    ax2.set_xlabel("dasselbe", fontsize=8.5)
    ax1.set_ylabel(f"erkannte Krisen (%), {mode}", fontsize=8.5)
    ax1.set_ylim(0, 102)
    ax2.set_ylabel("Fehleskalationen je Bett und Stunde", fontsize=8.5)
    ax2.set_ylim(0, None)
    handles = [Line2D([], [], color=COLORS[r], lw=1.8, marker="o", ms=5, label=NAMES[r]) for r in ("r2", "adaptiv", "r3")]
    handles += [Line2D([], [], marker=r.spec.marker, color=INK, mfc=INK if r.spec.filled else "white", ls="none", ms=6.5,
                       label=f"Simulation {r.spec.label_tex}") for r in sel]
    bottom = _legend_bottom(fig, handles, ncol=4, fontsize=7.0)
    _layout(fig, bottom=min(bottom, 0.5), top=0.95, wspace=0.32)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# Block 4, 6, 7 – aus der ROC-Werkstatt
# ─────────────────────────────────────────────────────────────────────────────

def _params_cfg(datasets: Dict[str, fw.Dataset], params: str) -> Tuple[Tuple[float, float, float], Optional[Tuple[float, float, float]]]:
    kal = next((ds.kal for ds in datasets.values() if ds.kal is not None), None)
    cfg = kal if (params == "kalibriert" and kal is not None) else ws.BASE
    return cfg, kal


def fig_froc(datasets: Dict[str, fw.Dataset], x_unit: str = "ereignis", mode: str = "mild", params: str = "Prototyp") -> Figure:
    cfg, kal = _params_cfg(datasets, params)
    return fw.fig_werkstatt(datasets, fw.Options(eta="beide" if len(datasets) > 1 else next(iter(datasets)), chord=True, hull=True,
                                                 netz=True, x_unit=x_unit, mode=mode, T=cfg[0], alpha=cfg[1], delta_min=cfg[2], kal=kal))


def fig_klassisch(datasets: Dict[str, fw.Dataset], x_unit: str = "ereignis", mode: str = "mild", h: float = 1.0,
                  compare: bool = True, params: str = "Prototyp") -> Figure:
    """Block 4b: klassische ROC des Filters (Schwelle über P0) gegen den k-aus-4-Linienzug bei festem h."""
    cfg, kal = _params_cfg(datasets, params)
    return fw.fig_werkstatt(datasets, fw.Options(view="klassisch", eta="beide" if len(datasets) > 1 else next(iter(datasets)),
                                                 x_unit=x_unit, mode=mode, h=h, compare_params=compare, T=cfg[0], alpha=cfg[1],
                                                 delta_min=cfg[2], kal=kal))


def klassisch_opt(datasets: Dict[str, fw.Dataset], mode: str = "mild", h: float = 1.0) -> fw.Options:
    return fw.Options(view="klassisch", eta="beide" if len(datasets) > 1 else next(iter(datasets)), x_unit="ereignis", mode=mode, h=h,
                      compare_params=True, kal=_params_cfg(datasets, "Prototyp")[1])


def auc_rows(datasets: Dict[str, fw.Dataset], mode: str = "mild", h: float = 1.0) -> List[dict]:
    return fw.auc_rows(datasets, klassisch_opt(datasets, mode, h))


def aussage_4b(datasets: Dict[str, fw.Dataset], mode: str = "mild", h: float = 1.0) -> str:
    parts = []
    for e, ds in datasets.items():
        for b in ds.boot:
            if abs(float(b.get("h", -1)) - h) > 1e-9 or b.get("mode") != mode:
                continue
            parts.append(f"η = {e}, {b.get('name')}: AUC {fw._auc_text(b.get('auc_adaptiv'), None)} gegen "
                         f"{fw._auc_text(b.get('auc_kausn'), None)} (k-aus-4), Δ {fw._auc_text(b.get('differenz'), b.get('differenz_ci'), sign=True)}")
    if not parts:
        return ("Die P0-Kurve des Filters und der k-aus-4-Linienzug liegen bei festem h nahe beieinander; Zahlen erscheinen, "
                "sobald `python -m analysis.roc_ziel3.kalibrierung --stufe pruefung` gerechnet ist (ausstehend).")
    return (f"Bei festem Grenzwert h = {de(h)} ist die ereignisbezogene AUC der adaptiven P0-Kurve ({mode}) gegen den "
            "k-aus-4-Linienzug (Patienten-Bootstrap, 95 %): " + "; ".join(parts) + ".")


def delta_rows(datasets: Dict[str, fw.Dataset]) -> List[dict]:
    """Δ Sehne / Δ Hülle (nach Stück) bei h = 1 je η für die Basis."""
    out = []
    for e, ds in datasets.items():
        d = fw._delta_source(ds, ws.BASE, "stueck")
        if d is None:
            continue
        out.append({"η": e, "Fehleskalationen je Bett·h": de(d.get("x"), 2),
                    "erkannte Krisen (mild)": pct(d.get("y_adaptiv"), 1),
                    "Δ Sehne 2-/3-aus-4": fw._ci_text(d.get("delta_sehne_pp"), d.get("delta_sehne_ci")),
                    "Δ obere Hülle": fw._ci_text(d.get("delta_pp"), d.get("delta_ci"))})
    return out


def aussage_4(datasets: Dict[str, fw.Dataset]) -> str:
    parts = []
    for e, ds in datasets.items():
        d = fw._delta_source(ds, ws.BASE, "stueck")
        if d is None:
            continue
        parts.append(f"η = {e}: Δ Sehne {fw._ci_text(d.get('delta_sehne_pp'), d.get('delta_sehne_ci'))}, "
                     f"Δ Hülle {fw._ci_text(d.get('delta_pp'), d.get('delta_ci'))}")
    return ("Über alle Gerätegrenzwerte h bleibt adaptiv nach Stück über der Sehne 2-/3-aus-4 desselben h und auf oder "
            "nahe der Hülle der festen Regeln; bei h = 1: " + "; ".join(parts) + ".") if parts else "Keine Werkstatt-Daten."


def sens_rows(datasets: Dict[str, fw.Dataset]) -> List[dict]:
    """Empfindlichkeit bei h = 1: alle Konfigurationen (T, α, δ_min) je η."""
    out: List[dict] = []
    for e, ds in datasets.items():
        if ds.werk is None:
            continue
        for r in ws.h1_table(ds.werk):
            out.append({"η": e, "Konfiguration": r["label"], "Fehleskalationen je Bett·h": de(r["falsch_je_bett_h"], 2),
                        "erkannt mild": pct(r["erkannt"]), "streng": pct(r["erkannt_streng"]),
                        "Latenz Median / p90 [s]": f"{de(r['verz_kreuz_median_s'], 1)} / {de(r['verz_kreuz_p90_s'], 1)}",
                        "Δ Sehne": fw._ci_text(r["delta_sehne_pp"], r["delta_sehne_ci"]),
                        "Δ Hülle": fw._ci_text(r["delta_pp"], r["delta_ci"])})
    return out


def fig_schar(datasets: Dict[str, fw.Dataset], param: str = "T") -> Figure:
    return fw.fig_werkstatt(datasets, fw.Options(eta="beide" if len(datasets) > 1 else next(iter(datasets)), schar=param,
                                                 hull=True))


def _param_span(ds: fw.Dataset, param: str) -> Optional[Tuple[float, float, float, float]]:
    """(min, max) der Fehleskalationen je Bett·h und der erkannten Krisen (mild, %) über die Werte eines Parameters."""
    if ds.werk is None:
        return None
    rows = [r for r in ws.h1_table(ds.werk) if ws.varied(r["T"], r["alpha"], r["delta_min"]) in (None, param)]
    if not rows:
        return None
    f = [r["falsch_je_bett_h"] for r in rows]
    m = [100 * r["erkannt"] for r in rows]
    return min(f), max(f), min(m), max(m)


def aussage_6(datasets: Dict[str, fw.Dataset]) -> str:
    ds = datasets.get("0,9") or next(iter(datasets.values()), None)
    if ds is None or ds.werk is None:
        return "Keine Werkstatt-Daten."
    parts = []
    for param, name in (("alpha", "α"), ("delta_min", "δ_min"), ("T", "T")):
        sp = _param_span(ds, param)
        if sp:
            parts.append(f"{name}: {de(sp[0], 2)} … {de(sp[1], 2)} Fehleskalationen je Bett·h, {de(sp[2], 1)} … {de(sp[3], 1)} % erkannt")
    return (f"Bei h = 1 (η = {ds.eta}) verschieben α und δ_min den Arbeitspunkt wenig, das Fenster T stark – "
            + "; ".join(parts) + ".")


def fig_geraet(datasets: Dict[str, fw.Dataset]) -> Figure:
    return fw.fig_werkstatt(datasets, fw.Options(view="geraet_system",
                                                 eta="beide" if len(datasets) > 1 else next(iter(datasets))))


def aussage_7(datasets: Dict[str, fw.Dataset]) -> str:
    ds = datasets.get("0,9") or next(iter(datasets.values()), None)
    if ds is None:
        return "Keine ROC-Daten."
    cfgs = fw.configs_for(fw.Options(), [ds])
    slopes = []
    for s in fw.visible_series(ds, fw.Options(rules=("2-aus-4", "adaptiv", "3-aus-4")), cfgs):
        x = [p["fpr_dev"] for p in s.pts if p.get("fpr_dev") and p.get("x_count")]
        y = [p["x_count"] for p in s.pts if p.get("fpr_dev") and p.get("x_count")]
        sl, _n = mt.loglog_slope(x, y, xmax=rr.LOW_NOISE)
        slopes.append(f"{s.name} {de(sl, 2)}")
    devs = [f"{k}-aus-4 bis {de(fw.binom_deviation(ds, k)[0], 1)} pp" for k in (2, 3)]
    return (f"Fehleskalationen folgen dem Geräterauschen überproportional (Steigung bei FPR̄ ≤ 0,03, η = {ds.eta}: "
            + ", ".join(slopes) + "); die erkannten Krisen folgen der Binomialformel mit den gemessenen η_j ("
            + ", ".join(devs) + " Abweichung).")


# ─────────────────────────────────────────────────────────────────────────────
# Block 5 – Latenz und ROC nach Zeit
# ─────────────────────────────────────────────────────────────────────────────

def latency_rows(runs: Sequence[sd.RunData]) -> List[dict]:
    out = []
    for run in runs:
        dl = delays_by_rule(run)
        if not dl:
            continue
        for rule in RULES:
            if rule in dl:
                out.append(dict(run=run.key, label=run.spec.label_eta, rule=rule, median=statistics.median(dl[rule]),
                                p90=_q(dl[rule], 0.9), n=len(dl[rule])))
    return out


def fig_kosten(runs: Sequence[sd.RunData], datasets: Dict[str, fw.Dataset], lat_runs: Sequence[str] = ("12h", "A")) -> Figure:
    """Links Latenz ab dem ersten Überschreiten (Säule Median, Strich p90) je Regel für die gewählten Läufe; rechts ROC
    nach Zeit (η 0,9, Basis), in der die Wartezeit des Fensters als verpasste Krisenzeit zählt."""
    sel = [r for r in runs if r.key in lat_runs and delays_by_rule(r)]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(W, 0.52 * W), gridspec_kw=dict(width_ratios=[1.0, 1.25], wspace=0.35))
    width = 0.26
    offs = {"r2": -width, "adaptiv": 0.0, "r3": width}
    ymax = 0.0
    for i, run in enumerate(sel):
        dl = delays_by_rule(run)
        for rule in RULES:
            if rule not in dl:
                continue
            med, p90 = statistics.median(dl[rule]), _q(dl[rule], 0.9)
            x = i + offs[rule]
            ax1.bar(x, med, width, color=COLORS[rule], zorder=3)
            ax1.plot([x - width * 0.42, x + width * 0.42], [p90, p90], color=INK, lw=1.4, zorder=5)
            ax1.text(x, p90 + 0.4, de(p90, 1), ha="center", va="bottom", fontsize=6.6, color=INK)
            ax1.text(x, med / 2, de(med, 1), ha="center", va="center", fontsize=7.0, color="white")
            ymax = max(ymax, p90)
    ax1.set_xticks(range(len(sel)))
    ax1.set_xticklabels([r.spec.label_tex for r in sel], fontsize=8)
    ax1.set_ylim(0, ymax * 1.25 if ymax else 1)
    ax1.set_ylabel("Latenz ab erstem Überschreiten\neiner Gerätegrenze [s]", fontsize=8.5)
    ax1.set_title("Latenz: Säule Median, Strich p90", fontsize=9)
    fz._bars_grid(ax1)
    fx.de_axes(ax1, x=False)
    handles = [Patch(facecolor=COLORS[r], label=NAMES[r]) for r in RULES]
    handles.append(Line2D([], [], color=INK, lw=1.4, label="p90"))
    ds = datasets.get("0,9") or (next(iter(datasets.values())) if datasets else None)
    if ds is not None:
        opt = fw.Options(view="roc", eta=ds.eta, hull=False, netz=True, chord=False)
        cfgs = fw.configs_for(opt, [ds])
        h2 = fw._draw_time(ax2, ds, opt, cfgs)
        handles += [h for h in h2 if h.get_label().startswith(("Netzlauf", "Grenzwertfaktor"))]
        ax2.set_title(f"ROC nach Zeit (η = {ds.eta})", fontsize=9)
        ax2.set_ylabel("Anteil der Krisenzeit\nmit Eskalation (Rot)", fontsize=8.5)
        ax2.set_xlabel("Anteil der krisenfreien Zeit mit Eskalation (Rot),\nlinear bis 0,05", fontsize=8.5)
        ax2.text(0.98, 0.04, "y = Anteil der Krisenzeit mit Rot;\ndie Wartezeit des Fensters T fehlt adaptiv hier,\n"
                 "nicht bei den erkannten Krisen (Block 1, 3)", transform=ax2.transAxes, ha="right", va="bottom",
                 fontsize=6.6, color=INK, bbox=dict(fc="white", ec="none", alpha=0.85))
    else:
        ax2.axis("off")
    bottom = _legend_bottom(fig, handles, ncol=3, fontsize=6.8, extra_in=0.68)
    _layout(fig, bottom=min(bottom, 0.5), top=0.92, wspace=0.35)
    return fig


def aussage_5(runs: Sequence[sd.RunData]) -> str:
    rows = latency_rows([r for r in runs if r.key in ("12h", "A")])
    if not rows:
        return "Keine Latenzdaten."
    by = {}
    for r in rows:
        by.setdefault(r["label"], {})[r["rule"]] = r
    parts = []
    for lab, d in by.items():
        parts.append(lab + ": " + ", ".join(f"{NAMES[k]} {de(d[k]['median'], 1)} / {de(d[k]['p90'], 1)} s"
                                            for k in RULES if k in d))
    return ("Latenz ab dem ersten Überschreiten einer Gerätegrenze (Median / p90) – " + "; ".join(parts)
            + ". Nach Zeit zählt die Wartezeit des Fensters als verpasste Krisenzeit, deshalb liegt adaptiv dort unter 2-/3-aus-4, "
              "nach Stück nicht.")


# ─────────────────────────────────────────────────────────────────────────────
# Kennzahlen und Zusammenfassung
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Kennzahlen:
    gap07_pp: Optional[float] = None          # adaptiv über der Sehne (η 0,7: Lauf A, sonst Werkstatt)
    gap07_quelle: str = ""
    gap09_pp: Optional[float] = None          # 12 h
    red_false_pct: Optional[float] = None     # 12 h: 1 − adaptiv/2-aus-4
    diff_mild_pp: Optional[float] = None
    diff_strict_pp: Optional[float] = None
    eta06_adaptiv: Optional[float] = None     # 6 h mild
    eta06_r3: Optional[float] = None
    lat_diff_s: Optional[float] = None        # 12 h Median adaptiv − 2-aus-4
    lat_adaptiv: Optional[float] = None
    lat_r2: Optional[float] = None
    delta_alpha: Tuple[Optional[float], Optional[float]] = (None, None)   # Δ Hülle über α (η 0,9)
    delta_dmin: Tuple[Optional[float], Optional[float]] = (None, None)
    false_T: Tuple[Optional[float], Optional[float]] = (None, None)       # Fehleskalationen T 30 … T 5
    kal_konfig: Optional[str] = None                                      # „kalibriert“ (T/α/δ_min) oder None
    kal_auc: Dict[str, Tuple[Optional[float], Optional[float], Optional[float]]] = None   # η → (AUC Prototyp, AUC kalibriert, AUC k-aus-4), h = 1, mild
    kal_latenz_s: Optional[float] = None                                  # Median-Latenz kalibriert bei P0 = 0,5 (Lernszenario)


def kennzahlen(runs: Dict[str, sd.RunData], datasets: Dict[str, fw.Dataset]) -> Kennzahlen:
    k = Kennzahlen()
    k.kal_auc = {}
    kd = kb.load_kalibriert()
    if kd and kd.get("kalibriert"):
        k.kal_konfig = kd["kalibriert"]["konfig"]
        k.kal_latenz_s = kd["kalibriert"].get("latenz_median_s")
    for e, ds in datasets.items():
        bs = [b for b in ds.boot if abs(float(b.get("h", -1)) - 1.0) < 1e-9 and b.get("mode") == "mild"]
        if bs:
            pr = next((b for b in bs if b.get("name") == "Prototyp"), None)
            ka = next((b for b in bs if b.get("name") == "kalibriert"), None)
            k.kal_auc[e] = ((pr or {}).get("auc_adaptiv"), (ka or {}).get("auc_adaptiv"), (pr or ka or {}).get("auc_kausn"))
    if "A" in runs:
        g = chord_gap(runs["A"])
        if g:
            k.gap07_pp, k.gap07_quelle = g["gap_pp"], "Netzlauf A"
    if k.gap07_pp is None and "0,7" in datasets:
        d = fw._delta_source(datasets["0,7"], ws.BASE, "stueck")
        if d:
            k.gap07_pp, k.gap07_quelle = d["delta_sehne_pp"], "Werkstatt h = 1"
    if "12h" in runs:
        g = chord_gap(runs["12h"])
        if g:
            k.gap09_pp = g["gap_pp"]
        ad, r2 = runs["12h"].rules["adaptiv"], runs["12h"].rules["r2"]
        k.red_false_pct = 100.0 * (1.0 - ad.false_per_bed_h / r2.false_per_bed_h)
        k.diff_mild_pp = 100.0 * (ad.caught_frac("mild") - r2.caught_frac("mild"))
        k.diff_strict_pp = 100.0 * (ad.caught_frac("streng") - r2.caught_frac("streng"))
        dl = delays_by_rule(runs["12h"])
        if "adaptiv" in dl and "r2" in dl:
            k.lat_adaptiv, k.lat_r2 = statistics.median(dl["adaptiv"]), statistics.median(dl["r2"])
            k.lat_diff_s = k.lat_adaptiv - k.lat_r2
    if "6h" in runs:
        k.eta06_adaptiv = 100.0 * runs["6h"].rules["adaptiv"].caught_frac("mild")
        k.eta06_r3 = 100.0 * runs["6h"].rules["r3"].caught_frac("mild")
    ds = datasets.get("0,9")
    if ds is not None and ds.werk is not None:
        rows = ws.h1_table(ds.werk)
        for param, attr in (("alpha", "delta_alpha"), ("delta_min", "delta_dmin")):
            vals = [r["delta_pp"] for r in rows if ws.varied(r["T"], r["alpha"], r["delta_min"]) in (None, param)
                    and r["delta_pp"] is not None]
            if vals:
                setattr(k, attr, (min(vals), max(vals)))
        tv = {r["T"]: r["falsch_je_bett_h"] for r in rows if ws.varied(r["T"], r["alpha"], r["delta_min"]) in (None, "T")}
        if tv:
            k.false_T = (tv.get(max(tv)), tv.get(min(tv)))
    return k


def _d(v: Optional[float], digits: int = 1, sign: bool = False) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "–"
    s = f"{v:+.{digits}f}" if sign else f"{v:.{digits}f}"
    return s.replace(".", ",")


def zusammenfassung(k: Kennzahlen) -> List[str]:
    s1 = (f"Bei gleichen Geräten liegt der adaptive Filter über der Mischung aus 2-aus-4 und 3-aus-4 "
          f"(η = 0,7: {_d(k.gap07_pp, 1, True)} pp erkannte Krisen bei gleicher Zahl Fehlmeldungen"
          + (f"; η = 0,9: {_d(k.gap09_pp, 1, True)} pp" if k.gap09_pp is not None else "") + ").")
    s2 = (f"Gegenüber 2-aus-4 bleiben {_d(k.red_false_pct, 0)} % weniger Fehlmeldungen bei {_d(k.diff_mild_pp, 1, True)} pp "
          f"erkannten Krisen (mild; streng {_d(k.diff_strict_pp, 1, True)} pp; 12-h-Lauf).")
    s3 = (f"Gegenüber 3-aus-4 ist der Filter robuster, wenn wenige Geräte eine Krise anzeigen "
          f"(η = 0,6: {_d(k.eta06_adaptiv, 0)} statt {_d(k.eta06_r3, 0)} % erkannte Krisen).")
    s4 = (f"Preis: Die Latenz ab dem ersten Überschreiten einer Gerätegrenze ist im Median {_d(k.lat_diff_s, 1)} s länger "
          f"als bei 2-aus-4 ({_d(k.lat_adaptiv, 1)} statt {_d(k.lat_r2, 1)} s); das Ergebnis hängt kaum von α und δ_min ab "
          f"(Δ zur Hülle bei h = 1: α {_d(k.delta_alpha[0], 1, True)} … {_d(k.delta_alpha[1], 1, True)} pp, "
          f"δ_min {_d(k.delta_dmin[0], 1, True)} … {_d(k.delta_dmin[1], 1, True)} pp), das Fenster T ist der Stellhebel "
          f"({_d(k.false_T[0], 2)} … {_d(k.false_T[1], 2)} Fehleskalationen je Bett·h für T = 30 … 5 s).")
    out = [s1, s2, s3, s4]
    if k.kal_konfig and k.kal_auc:
        T, a, dmin = ws.parse_key(k.kal_konfig)
        parts = [f"η = {e}: {_d(v[1], 3)} gegen {_d(v[0], 3)} (Prototyp) und {_d(v[2], 3)} (k-aus-4-Linienzug)" for e, v in k.kal_auc.items()]
        out.append(f"Kalibrierung: Die nach festem Kriterium gewählte Konfiguration „kalibriert“ (T = {de(T)} s, α = {de(a, 1)}, "
                   f"δ_min = {de(dmin, 1)}; Median-Latenz {_d(k.kal_latenz_s, 1)} s) erreicht bei h = 1 die ereignisbezogene AUC "
                   + "; ".join(parts) + ".")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────────────────────────

def save(fig: Figure, block, outdir: Path = FIG_DIR, suffix: str = "") -> List[Path]:
    return fr.save(fig, Path(outdir), FILES[block] + suffix)


def has_klassisch(datasets: Dict[str, fw.Dataset]) -> bool:
    return any(ds.klassisch for ds in datasets.values())


def export_all(runs: Dict[str, sd.RunData], datasets: Dict[str, fw.Dataset], outdir: Path = FIG_DIR, x_unit: str = "ereignis",
               run_key: str = "12h", mode: str = "mild", param: str = "T", params: str = "Prototyp", suffix: str = "") -> List[Path]:
    """Alle Blöcke; ``suffix`` an jeden Dateinamen (z. B. „_v2“ – vorhandene Dateien bleiben erhalten)."""
    rl = [runs[k] for k in sd.RUN_ORDER if k in runs]
    written: List[Path] = []
    written += save(fig_betriebspunkte(rl, x_unit, mode, params), 1, outdir, suffix)
    if run_key in runs:
        written += save(fig_bilanz(runs[run_key]), 2, outdir, suffix)
    written += save(fig_eta(rl, mode), 3, outdir, suffix)
    if datasets:
        written += save(fig_froc(datasets, x_unit, mode, params), 4, outdir, suffix)
        if has_klassisch(datasets):
            written += save(fig_klassisch(datasets, "ereignis", mode, 1.0, True, params), "4b", outdir, suffix)
    written += save(fig_kosten(rl, datasets), 5, outdir, suffix)
    if datasets:
        written += save(fig_schar(datasets, param), 6, outdir, suffix)
        written += save(fig_geraet(datasets), 7, outdir, suffix)
    return written


def load_datasets() -> Dict[str, fw.Dataset]:
    out: Dict[str, fw.Dataset] = {}
    for eta, d in (("0,9", rr.ROC_DIR), ("0,7", rr.ROC_DIR_P07)):
        csv_path, tab_path = d / rr.CSV_OUT, d / rr.TAB_JSON
        if csv_path.exists() and tab_path.exists():
            out[eta] = fw.attach_kalibrierung(fw.Dataset(eta, rr.load_rows(csv_path), json.loads(tab_path.read_text(encoding="utf-8")), ws.load(d)))
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Kapitel 5 · Gesamtbild: sieben Abbildungen exportieren (PNG 300 dpi + SVG).")
    ap.add_argument("--outdir", type=Path, default=FIG_DIR)
    ap.add_argument("--x", choices=list(X_UNITS), default="ereignis")
    ap.add_argument("--lauf", default="12h", help="Lauf für Block 2")
    ap.add_argument("--erkannt", choices=list(sd.MODES), default="mild")
    ap.add_argument("--parameter", choices=["T", "alpha", "delta_min"], default="T")
    ap.add_argument("--params", choices=list(PARAM_SETS), default="Prototyp", help="Filterparameter Prototyp | kalibriert")
    ap.add_argument("--suffix", default="", help="Namenszusatz der Dateien (z. B. _v2)")
    args = ap.parse_args(argv)
    runs = sd.load_runs()
    datasets = load_datasets()
    written = export_all(runs, datasets, args.outdir, args.x, args.lauf, args.erkannt, args.parameter, args.params, args.suffix)
    for p in written:
        print("geschrieben:", p)
    k = kennzahlen(runs, datasets)
    for s in zusammenfassung(k):
        print(s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
