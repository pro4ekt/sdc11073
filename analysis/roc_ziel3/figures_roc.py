"""Abbildungen R1a, R1b, R2, R3, R4a, R4b, R5 (Ziel 3, ROC bei Variation der Gerätegrenzwerte).

Eingabe sind die Zeilen von ``roc_alle.csv`` (``run_roc.load_rows``) und die Tabellen aus ``roc_tabellen.json``.
Achsen nach Zeit: y = „Anteil der Krisenzeit mit Eskalation (Rot)“, x = „Anteil der krisenfreien Zeit mit
Eskalation (Rot)“ (Hauptvariante ii: nur falsche Episoden; Variante i wählbar).  Stil wie die übrigen
Abbildungen der Anwendung: 15 cm breit, Dezimalkomma, adaptiv blau dick, 2-aus-4 orange, 3-aus-4 violett,
1-/4-aus-4 dünn; Geräte dünn grau; Rhombus = Arbeitspunkt h = 1,0; Stern = Netzlauf ziel3_12h;
Linienart kodiert die Quelle: Messung durchgezogen, Formel (a) gestrichelt, Modell (b) gepunktet,
Modell E1+E2 strichpunktiert.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch, Rectangle  # noqa: E402
from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter  # noqa: E402

from analysis.belastungsmodell_app import export_figures as fx  # noqa: E402

W = fx.W
de = fx.de
GREY = fx.GREY
INK = fx.INK
DEV_COLOR = "#b4b4ae"
GRID = "#e6e6e2"

RULES: Tuple[str, ...] = ("1-aus-4", "2-aus-4", "3-aus-4", "4-aus-4", "adaptiv")
MAIN_RULES: Tuple[str, ...] = ("2-aus-4", "3-aus-4", "adaptiv")
DEVICES: Tuple[str, ...] = ("ECG", "Vent", "SpO2", "ABP")
STYLE: Dict[str, dict] = {
    "1-aus-4": dict(color="#1baf7a", lw=1.1),
    "2-aus-4": dict(color="#eb6834", lw=2.2),
    "3-aus-4": dict(color="#7a5fb3", lw=2.2),
    "4-aus-4": dict(color="#e34948", lw=1.1),
    "adaptiv": dict(color="#2a78d6", lw=2.8),
}
T_SHADES: Dict[float, str] = {10.0: "#2a78d6", 20.0: "#6fa3e6", 30.0: "#0d366b"}   # adaptiv nach Fenster T
T_MAIN = 10.0
H_WORK = 1.0
NET_RULES: Tuple[str, ...] = ("adaptiv", "2-aus-4", "3-aus-4")
FILES: Dict[str, str] = {
    "R1a": "R1a_ROC_Geraetegrenzwerte", "R1b": "R1b_Theorie_Messung", "R1b_log": "R1b_Theorie_Messung_log",
    "R1b_stueck": "R1b_Theorie_Messung_Stueck",
    "R2": "R2_ROC_Stueck", "R2_v2": "R2_v2_ROC_Stueck", "R2_vergleich": "R2_vergleich_ROC_Stueck",
    "R3": "R3_Uebertragung_Geraet_System", "R4a": "R4a_Fenster_T_Zeit", "R4b": "R4b_Fenster_T_Stueck",
    "R5": "R5_Fehlerbilanz_Grenzwerte",
}
Y_TIME = "Anteil der Krisenzeit mit Eskalation (Rot)"
X_TIME = "Anteil der krisenfreien Zeit mit Eskalation (Rot)"
X_TIME_I = "Anteil der krisenfreien Zeit mit Rot (Variante i)"
X_SAME_LOG = "dasselbe, logarithmisch"
Y_COUNT = "erkannte Krisen (%)"
X_COUNT = "Fehleskalationen je Bett und Stunde"
TITLE_TIME = "Gesamtsystem nach Alarmfusion: Eskalationszeit in und außerhalb von Krisen (Gerätegrenzwerte variiert)"
TITLE_COUNT = "Gesamtsystem nach Alarmfusion: erkannte Krisen und Fehleskalationen"
DEV_LABEL = "Einzelgeräte (Anteil der Zeit mit Gerätealarm)"
FOOTNOTE_R1A = ("Krisenzeit ohne Rot = verpasste bzw. verspätete Eskalation (1 − y); "
                "krisenfreie Zeit mit Rot = Fehleskalation (x).")
FOOTNOTE_ADAPTIV = ("adaptiv: Die Wartezeit des Fensters T (≈ 10 s) kostet Eskalationszeit in kurzen Krisen (Ø 30 s); "
                    "erkannt werden 97,7 % der Krisen (siehe R2).")
XMAX_LIN = 0.05
XMAX_COUNT = 10.0


# ─────────────────────────────────────────────────────────────────────────────
# Hilfen
# ─────────────────────────────────────────────────────────────────────────────

def _de_pos(v: float, _pos=None) -> str:
    """Logarithmische Achse: positionsgenaue Zahl mit Dezimalkomma (0,0001 statt 1e-04)."""
    if v is None or v <= 0 or not np.isfinite(v):
        return ""
    s = np.format_float_positional(float(f"{v:.6g}"), trim="-")
    return s.replace(".", ",")


def log_axis(ax, axis: str, lo: float, hi: float, max_labels: int = 40) -> None:
    """Logarithmische Achse mit Teilstrichdichte nach Dekadenzahl (1-2-5 | 1-3 | 1) und Dezimalkomma;
    ``max_labels`` begrenzt die Zahl beschrifteter Dekaden (schmale Panels)."""
    a = ax.xaxis if axis == "x" else ax.yaxis
    decades = math.log10(hi / lo) if lo > 0 and hi > lo else 10.0
    subs = (1.0, 2.0, 5.0) if decades <= 2.6 else ((1.0, 3.0) if decades <= 4.6 else (1.0,))
    if decades > max_labels:
        subs = (1.0,)
    a.set_major_locator(LogLocator(base=10.0, subs=subs, numticks=max_labels))
    a.set_major_formatter(FuncFormatter(_de_pos))
    a.set_minor_locator(LogLocator(base=10.0, subs=(2.0, 3.0, 5.0), numticks=40))
    a.set_minor_formatter(NullFormatter())


def row_T(r: dict) -> Optional[float]:
    return r.get("T_s") if r.get("T_s") is not None else None


def curve(rows: Iterable[dict], art: str, name: str, T: Optional[float] = T_MAIN) -> List[dict]:
    """Zeilen einer Kurve (art, name), nach h sortiert; Zeilen ohne T_s gelten für jedes T."""
    sel = [r for r in rows if r.get("art") == art and r.get("name") == name and r.get("h") is not None
           and (row_T(r) is None or T is None or abs(row_T(r) - T) < 1e-9)]
    return sorted(sel, key=lambda r: r["h"])


def xy(sel: Sequence[dict], xkey: str, ykey: str, yscale: float = 1.0) -> Tuple[np.ndarray, np.ndarray]:
    xs = np.array([r.get(xkey) if r.get(xkey) is not None else np.nan for r in sel], dtype=float)
    ys = np.array([r.get(ykey) if r.get(ykey) is not None else np.nan for r in sel], dtype=float) * yscale
    ok = np.isfinite(xs) & np.isfinite(ys)
    return xs[ok], ys[ok]


def _work(sel: Sequence[dict]) -> Optional[dict]:
    for r in sel:
        if r.get("h") is not None and abs(r["h"] - H_WORK) < 1e-9:
            return r
    return None


def _net(rows: Iterable[dict], name: str, quelle_prefix: str = "Netzlauf") -> Optional[dict]:
    for r in rows:
        if r.get("art") == "Netz" and r.get("name") == name and str(r.get("quelle", "")).startswith(quelle_prefix):
            return r
    return None


def _auc_key(name: str, T: float) -> str:
    return name if (name != "adaptiv" or abs(T - T_MAIN) < 1e-9) else f"adaptiv T={T:g}"


def net_label(rows: Iterable[dict]) -> str:
    """Legendentext des Netzlaufs aus der Spalte ``quelle`` der Netz-Zeilen (z. B. „Netzlauf ziel3_12h“)."""
    for r in rows:
        q = str(r.get("quelle", ""))
        if r.get("art") == "Netz" and q.startswith("Netzlauf"):
            return q.split(" (")[0]
    return "Netzlauf"


def _pauc_label(name: str, tables: Optional[dict], T: float = T_MAIN, variant: str = "ii") -> str:
    try:
        v = tables["auc"][_auc_key(name, T)][f"pauc_003_{variant}"]
        return f"{name} (pAUC {de(v, 2)})"
    except (KeyError, TypeError):
        return name


def _positive(vals: Iterable[float]) -> List[float]:
    return [v for v in vals if v is not None and np.isfinite(v) and v > 0]


def _xkey(variant: str) -> str:
    return "fpr_ii" if variant == "ii" else "fpr"


def _marker_work(ax, x, y, color, zorder=7, size=5.5):
    ax.plot([x], [y], marker="D", ms=size, color=color, mec="white", mew=0.9, zorder=zorder)


def _marker_net(ax, x, y, color, zorder=6):
    ax.plot([x], [y], marker="*", ms=12, color=color, mec="black", mew=0.7, zorder=zorder)


def _grid(ax):
    ax.grid(True, which="major", color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    ax.tick_params(labelsize=8)


def _layout(fig: Figure, bottom: float, top: float = 0.92, left: float = 0.09, right: float = 0.985,
            wspace: float = 0.25, hspace: float = 0.12) -> None:
    """Feste Ränder statt tight_layout (Legende und Fußnote liegen unter den Achsen)."""
    fig.subplots_adjust(left=left, right=right, bottom=bottom, top=top, wspace=wspace, hspace=hspace)


# ─────────────────────────────────────────────────────────────────────────────
# R1a – ROC nach Zeit (Messung + Formel (a)); Hauptpanel linear, Nebenpanel log
# ─────────────────────────────────────────────────────────────────────────────

def _draw_roc_measured(ax, rows: Sequence[dict], tables: Optional[dict], log_x: bool, variant: str,
                       with_formula: bool = True, T: float = T_MAIN) -> List[Line2D]:
    xk = _xkey(variant)
    handles: List[Line2D] = []
    for dev in DEVICES:
        x, y = xy(curve(rows, "Geraet", dev), xk, "tpr")
        ax.plot(x, y, color=DEV_COLOR, lw=0.9, zorder=2)
    handles.append(Line2D([], [], color=DEV_COLOR, lw=0.9, label=DEV_LABEL))
    for name in RULES:
        st = STYLE[name]
        sel = curve(rows, "Regel", name, T)
        x, y = xy(sel, xk, "tpr")
        ax.plot(x, y, color=st["color"], lw=st["lw"], ls="-", marker="o", ms=2.4, mew=0, zorder=4)
        handles.append(Line2D([], [], color=st["color"], lw=st["lw"], marker="o", ms=2.4, mew=0,
                              label=_pauc_label(name, tables, T, variant)))
        n = _net(rows, name)
        if n is not None and name in NET_RULES and n.get(xk) is not None:
            _marker_net(ax, n[xk], n["tpr"], st["color"])
        w = _work(sel)
        if w is not None and w.get(xk) is not None:
            _marker_work(ax, w[xk], w["tpr"], st["color"])
    if with_formula:
        for k in (2, 3):
            name = f"{k}-aus-4"
            x, y = xy(curve(rows, "Formel", name), "fpr", "tpr")
            ax.plot(x, y, color=STYLE[name]["color"], lw=1.1, ls="--", zorder=3)
        handles.append(Line2D([], [], color=INK, lw=1.1, ls="--", label="Formel (a), binomial mit p = TPR_j bzw. FPR_j nach Zeit, k = 2 und 3"))
    handles += [
        Line2D([], [], color="white", marker="D", ms=5.5, mec=INK, mew=0.9, ls="none", label="Arbeitspunkt h = 1,0"),
        Line2D([], [], color="white", marker="*", ms=12, mec="black", mew=0.7, ls="none", label=net_label(rows)),
    ]
    _finish_time_axes(ax, rows, log_x, variant, xk)
    return handles


def _finish_time_axes(ax, rows: Sequence[dict], log_x: bool, variant: str, xk: str) -> None:
    ax.set_ylim(0, 1.0)
    ax.set_ylabel(Y_TIME, fontsize=8.5)
    xlabel = X_TIME if variant == "ii" else X_TIME_I
    if log_x:
        ax.set_xscale("log")
        xs_all = _positive(r.get(xk) for r in rows if r.get("art") in ("Regel", "Geraet"))
        lo = min(xs_all) * 0.7 if xs_all else 1e-4
        ax.set_xlim(lo, 1.0)
        log_axis(ax, "x", lo, 1.0, max_labels=4)
        ax.set_xlabel(xlabel + ", logarithmisch", fontsize=8.5)
        fx.de_axes(ax, x=False, y=True)
    else:
        ax.set_xlim(0, XMAX_LIN)
        ax.set_xlabel(xlabel + f", linear bis {de(XMAX_LIN)}", fontsize=8.5)
        fx.de_axes(ax, x=True, y=True)
    _grid(ax)


def _two_panels(height: float = 0.66):
    """Hauptpanel links (breit, linear) und Nebenpanel rechts (schmaler, logarithmisch)."""
    fig, axes = plt.subplots(1, 2, figsize=(W, height * W), gridspec_kw=dict(width_ratios=[1.75, 1.0], wspace=0.28))
    return fig, axes


def fig_R1a(rows: Sequence[dict], tables: Optional[dict] = None, variant: str = "ii",
            x_mode: str = "beide", title: bool = True) -> Figure:
    """ROC nach Zeit: Messung (Geräte, k-aus-4, adaptiv T = 10) und Formel (a); ohne Modell (b)."""
    if x_mode == "beide":
        fig, axes = _two_panels()
        handles = _draw_roc_measured(axes[0], rows, tables, False, variant, with_formula=False)
        _draw_roc_measured(axes[1], rows, tables, True, variant, with_formula=False)
        axes[1].set_ylabel("")
        axes[1].set_xlabel(X_SAME_LOG, fontsize=8.5)
    else:
        fig, ax = plt.subplots(figsize=(W, 0.62 * W))
        handles = _draw_roc_measured(ax, rows, tables, x_mode == "log", variant, with_formula=False)
    if title:
        fig.suptitle(TITLE_TIME, fontsize=8.5, y=0.995)
    fig.legend(handles=handles, loc="lower center", ncol=3, fontsize=6.6, frameon=False,
               bbox_to_anchor=(0.5, 0.07), handlelength=2.6, columnspacing=1.1)
    fig.text(0.5, 0.008, FOOTNOTE_R1A + "\n" + FOOTNOTE_ADAPTIV, ha="center", va="bottom", fontsize=6.6, color=INK)
    _layout(fig, bottom=0.34, top=0.93)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# R1b – Theorie gegen Messung (3 Panels, x log)
# ─────────────────────────────────────────────────────────────────────────────

FOOTNOTE_R1B = ("Gemessen und eingesetzt: η_j (Teilnahme), u_j (Eintrittsverzögerung), d_true, Episoden nach Größe m "
                "(Anteile, Raten, Dauern), FPR_j, P(C);\nBinomialformel mit p = TPR_j bzw. FPR_j nach Zeit.")
TITLE_R1B = "Theorie gegen Messung (Gesamtsystem nach Alarmfusion)"
BINOM_LABEL = "Binomialformel (unabhängige Geräte)"


def fig_R1b(rows: Sequence[dict], tables: Optional[dict] = None, variant: str = "ii", T: float = T_MAIN,
            log_x: bool = False) -> Figure:
    """Drei Panels (2-aus-4, 3-aus-4, adaptiv T): Messung (dick, Regelfarbe), Modell E1+E2 (strichpunktiert,
    Regelfarbe), Binomialformel (dünn grau gestrichelt; nicht für adaptiv), Rhombus h = 1,0.
    Hauptansicht linear (x 0 … 0,05, y 0 … 1); ``log_x=True`` = logarithmische Variante."""
    xk = _xkey(variant)
    fig, axes = plt.subplots(1, 3, figsize=(W, 0.5 * W), sharey=True, gridspec_kw=dict(wspace=0.12))
    xs_all = _positive(r.get(xk) for r in rows if r.get("art") == "Regel")   # Messbereich; Theorie darunter wird abgeschnitten
    lo = min(xs_all) * 0.7 if xs_all else 1e-5
    for ax, name in zip(axes, MAIN_RULES):
        st = STYLE[name]
        x, y = xy(curve(rows, "Regel", name, T), xk, "tpr")
        ax.plot(x, y, color=st["color"], lw=st["lw"], ls="-", marker="o", ms=2.4, mew=0, zorder=4)
        x, y = xy(curve(rows, "ModellE", name, T), xk, "tpr")
        ax.plot(x, y, color=st["color"], lw=1.5, ls="-.", zorder=3)
        if name != "adaptiv":
            x, y = xy(curve(rows, "Formel", name), "fpr", "tpr")
            ax.plot(x, y, color=GREY, lw=1.0, ls="--", zorder=2)
        w = _work(curve(rows, "Regel", name, T))
        if w is not None and w.get(xk) is not None:
            _marker_work(ax, w[xk], w["tpr"], st["color"])
        if log_x:
            ax.set_xscale("log")
            ax.set_xlim(lo, 1.0)
            log_axis(ax, "x", lo, 1.0, max_labels=4)
            fx.de_axes(ax, x=False, y=True)
        else:
            ax.set_xlim(0, XMAX_LIN)
            fx.de_axes(ax, x=True, y=True)
        ax.set_ylim(0, 1.0)
        ax.set_title(name if name != "adaptiv" else f"adaptiv (T = {T:g} s)", fontsize=9)
        _grid(ax)
    axes[0].set_ylabel(Y_TIME, fontsize=8.5)
    xlabel = X_TIME if variant == "ii" else X_TIME_I
    axes[1].set_xlabel(xlabel + (", logarithmisch" if log_x else f", linear bis {de(XMAX_LIN)}"), fontsize=8.5)
    handles = [Line2D([], [], color=INK, lw=2.0, ls="-", marker="o", ms=2.4, mew=0, label="Messung (Simulation, Dashboard-Kern)"),
               Line2D([], [], color=INK, lw=1.5, ls="-.", label="Modell E1+E2 (versetzter Einstieg; Episoden nach m)"),
               Line2D([], [], color=GREY, lw=1.0, ls="--", label=BINOM_LABEL),
               Line2D([], [], color="white", marker="D", ms=5.5, mec=INK, mew=0.9, ls="none", label="h = 1,0")]
    fig.suptitle(TITLE_R1B, fontsize=8.5, y=0.995)
    fig.legend(handles=handles, loc="lower center", ncol=2, fontsize=6.8, frameon=False, bbox_to_anchor=(0.5, 0.115))
    fig.text(0.5, 0.008, FOOTNOTE_R1B + "\n" + FOOTNOTE_ADAPTIV, ha="center", va="bottom", fontsize=6.4, color=INK)
    _layout(fig, bottom=0.40, top=0.86, wspace=0.12)
    return fig


TITLE_R1B_STUECK = "Theorie gegen Messung, erkannte Krisen und Fehleskalationen (Gesamtsystem nach Alarmfusion)"
FOOTNOTE_R1B_STUECK = ("Modell E1+E2: erkannte Krisen aus E1 (η_j, u_j, d_true), Fehleskalationen aus E2 (Episoden nach m).\n"
                       "Binomialformel: P(mindestens k der 4 Geräte nehmen teil) mit den gemessenen η_j,\n"
                       "aufgetragen bei den gemessenen Fehleskalationen des jeweiligen h.")


def binom_participation(etas: Sequence[float], k: int) -> float:
    """P(mindestens k der Geräte nehmen an der Krise teil) bei unabhängiger Teilnahme mit η_j (Poisson-Binomial)."""
    etas = [float(e) for e in etas]
    n = len(etas)
    total = 0.0
    for mask in range(1 << n):
        cnt = bin(mask).count("1")
        if cnt < k:
            continue
        pr = 1.0
        for j, e in enumerate(etas):
            pr *= e if mask & (1 << j) else (1.0 - e)
        total += pr
    return total


def _etas_by_h(rows: Sequence[dict]) -> Dict[float, List[float]]:
    """η_j je h aus den Gerätezeilen (Spalte eta_oder_erkannt der PC-2-Tabelle)."""
    out: Dict[float, List[float]] = {}
    for dev in DEVICES:
        for r in curve(rows, "Geraet", dev):
            if r.get("eta_oder_erkannt") is not None:
                out.setdefault(round(r["h"], 6), []).append(r["eta_oder_erkannt"])
    return {h: v for h, v in out.items() if len(v) == len(DEVICES)}


def fig_R1b_stueck(rows: Sequence[dict], tables: Optional[dict] = None, T: float = T_MAIN) -> Figure:
    """Drei Panels nach Stück: x = Fehleskalationen je Bett·h (0 … 10), y = erkannte Krisen (mild, %).  Messung
    dick (Regelfarbe), Modell E1+E2 strichpunktiert (Regelfarbe), Binomialformel der Teilnahme (k-aus-4) dünn grau
    gestrichelt bei den gemessenen Fehleskalationen, Rhombus h = 1,0."""
    fig, axes = plt.subplots(1, 3, figsize=(W, 0.5 * W), sharey=True, gridspec_kw=dict(wspace=0.12))
    etas = _etas_by_h(rows)
    for ax, name in zip(axes, MAIN_RULES):
        st = STYLE[name]
        sel = curve(rows, "Regel", name, T)
        x, y = xy(sel, "falsch_je_bett_h", "eta_oder_erkannt", 100.0)
        ax.plot(x, y, color=st["color"], lw=st["lw"], ls="-", marker="o", ms=2.4, mew=0, zorder=4)
        x, y = xy(curve(rows, "ModellE", name, T), "falsch_je_bett_h", "eta_oder_erkannt", 100.0)
        ax.plot(x, y, color=st["color"], lw=1.5, ls="-.", zorder=3)
        if name != "adaptiv":
            k = int(name[0])
            pts = [(r["falsch_je_bett_h"], 100.0 * binom_participation(etas[round(r["h"], 6)], k)) for r in sel
                   if r.get("falsch_je_bett_h") is not None and round(r["h"], 6) in etas]
            if pts:
                pts.sort()
                ax.plot([a for a, _ in pts], [b for _, b in pts], color=GREY, lw=1.0, ls="--", zorder=2)
        w = _work(sel)
        if w is not None and w.get("falsch_je_bett_h") is not None:
            _marker_work(ax, w["falsch_je_bett_h"], 100 * w["eta_oder_erkannt"], st["color"])
        ax.set_xlim(0, XMAX_COUNT)
        ax.set_ylim(0, 102)
        ax.set_title(name if name != "adaptiv" else f"adaptiv (T = {T:g} s)", fontsize=9)
        _grid(ax)
        fx.de_axes(ax, x=True, y=True)
    axes[0].set_ylabel(Y_COUNT, fontsize=8.5)
    axes[1].set_xlabel(X_COUNT + f", linear bis {de(XMAX_COUNT)}", fontsize=8.5)
    handles = [Line2D([], [], color=INK, lw=2.0, ls="-", marker="o", ms=2.4, mew=0, label="Messung (Simulation, Dashboard-Kern)"),
               Line2D([], [], color=INK, lw=1.5, ls="-.", label="Modell E1+E2 (versetzter Einstieg; Episoden nach m)"),
               Line2D([], [], color=GREY, lw=1.0, ls="--", label="Binomialformel der Teilnahme (unabhängige Geräte, η_j)"),
               Line2D([], [], color="white", marker="D", ms=5.5, mec=INK, mew=0.9, ls="none", label="h = 1,0")]
    fig.suptitle(TITLE_R1B_STUECK, fontsize=8.2, y=0.995)
    fig.legend(handles=handles, loc="lower center", ncol=2, fontsize=6.8, frameon=False, bbox_to_anchor=(0.5, 0.115))
    fig.text(0.5, 0.008, FOOTNOTE_R1B_STUECK, ha="center", va="bottom", fontsize=6.4, color=INK)
    _layout(fig, bottom=0.40, top=0.86, wspace=0.12)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# R2 – nach Stück (Hauptpanel linear bis 10, Nebenpanel Ausschnitt), mit Modell E1+E2
# ─────────────────────────────────────────────────────────────────────────────

ZOOM_X = (1.0, 6.0)
ZOOM_Y = (88.0, 100.0)
ZOOM_COLOR = "#2a78d6"
HULL_LABEL = "beste Mischung fester k-aus-4-Regeln (obere konvexe Hülle aller k und h)"
MODEL_E_LABEL = "Modell E1+E2: erkannte Krisen aus E1 (η_j, u_j),\nFehleskalationen = Episodenteil von E2 (ohne G.7)"


def _e2_episode_part(tables: Optional[dict], h: float, key: str) -> Optional[float]:
    """Episodenteil der E2-Fehleskalationen je Bett·h (ohne das Zufallsglied G.7) aus roc_tabellen.json."""
    try:
        return float(tables["e2"][f"{h:g}"]["ergebnisse"][key]["episoden"])
    except (KeyError, TypeError, ValueError):
        return None


def model_e_count_curve(rows: Sequence[dict], tables: Optional[dict], name: str, T: float = T_MAIN
                        ) -> Tuple[np.ndarray, np.ndarray]:
    """(x, y) der erweiterten Modelle nach Stück: x = Episodenteil E2, y = erkannte Krisen E1 (%) – nur, wenn der
    Episodenteil für jedes h vorliegt, sonst leer."""
    sel = curve(rows, "ModellE", name, T)
    key = name if name != "adaptiv" else f"adaptiv_T{T:g}"
    xs, ys = [], []
    for r in sel:
        x = _e2_episode_part(tables, r["h"], key)
        if x is None or r.get("eta_oder_erkannt") is None:
            return np.array([]), np.array([])
        xs.append(x)
        ys.append(100.0 * r["eta_oder_erkannt"])
    return np.array(xs), np.array(ys)


def adaptiv_ahead(rows: Sequence[dict], T: float = T_MAIN, x_range: Tuple[float, float] = ZOOM_X,
                  n: int = 1001) -> List[Tuple[float, float]]:
    """Bereiche von x (Fehleskalationen je Bett·h), in denen adaptiv nach Interpolation der Kurven über
    2-aus-4 UND 3-aus-4 liegt."""
    def interp(name: str, Tc: Optional[float]):
        sel = curve(rows, "Regel", name, Tc if Tc is not None else T)
        x, y = xy(sel, "falsch_je_bett_h", "eta_oder_erkannt", 100.0)
        order = np.argsort(x)
        x, y = x[order], y[order]
        if x.size < 2:
            return None
        grid = np.linspace(*x_range, n)
        out = np.interp(grid, x, y)
        out[(grid < x[0]) | (grid > x[-1])] = np.nan
        return grid, out
    a = interp("adaptiv", T)
    b = interp("2-aus-4", None)
    c = interp("3-aus-4", None)
    if a is None or b is None or c is None:
        return []
    grid = a[0]
    ahead = (a[1] > b[1]) & (a[1] > c[1])
    spans: List[Tuple[float, float]] = []
    start = None
    for i, flag in enumerate(ahead):
        if flag and start is None:
            start = grid[i]
        elif not flag and start is not None:
            spans.append((start, grid[i - 1]))
            start = None
    if start is not None:
        spans.append((start, grid[-1]))
    return [(s, e) for s, e in spans if e > s]


def hull_points(tables: Optional[dict], view: str = "stueck") -> List[Tuple[float, float]]:
    """Eckpunkte der oberen konvexen Hülle der festen Regeln aus roc_tabellen.json (leer, wenn nicht vorhanden)."""
    try:
        return [(float(x), float(y)) for x, y in tables["huelle"][view]["huelle"]]
    except (KeyError, TypeError, ValueError):
        return []


def _draw_count(ax, rows: Sequence[dict], tables: Optional[dict] = None, log_x: bool = False, T: float = T_MAIN,
                with_model: bool = True, adaptiv_Ts: Optional[Sequence[float]] = None, zoom: bool = False,
                hull: Optional[Sequence[Tuple[float, float]]] = None) -> List[Line2D]:
    handles: List[Line2D] = []
    if hull:
        hx = np.array([p[0] for p in hull])
        hy = 100.0 * np.array([p[1] for p in hull])
        ax.plot(hx, hy, color=GREY, lw=1.3, ls="--", zorder=2)
    for name in RULES:
        st = STYLE[name]
        Ts = adaptiv_Ts if (name == "adaptiv" and adaptiv_Ts) else [T]
        for Tc in Ts:
            sel = curve(rows, "Regel", name, Tc)
            x, y = xy(sel, "falsch_je_bett_h", "eta_oder_erkannt", 100.0)
            color = T_SHADES.get(Tc, st["color"]) if (name == "adaptiv" and adaptiv_Ts) else st["color"]
            ax.plot(x, y, color=color, lw=st["lw"], ls="-", marker="o", ms=2.4, mew=0, zorder=4)
            lab = name if not (name == "adaptiv" and adaptiv_Ts) else f"adaptiv, T = {Tc:g} s"
            handles.append(Line2D([], [], color=color, lw=st["lw"], marker="o", ms=2.4, mew=0, label=lab))
            w = _work(sel)
            if w is not None and w.get("falsch_je_bett_h") is not None:
                _marker_work(ax, w["falsch_je_bett_h"], 100 * w["eta_oder_erkannt"], color)
        n = _net(rows, name)
        if n is not None and name in NET_RULES and n.get("falsch_je_bett_h") is not None:
            _marker_net(ax, n["falsch_je_bett_h"], 100 * n["eta_oder_erkannt"], st["color"])
    if with_model:
        drawn = False
        for name in MAIN_RULES:
            x, y = model_e_count_curve(rows, tables, name, T)
            if x.size:
                order = np.argsort(x)
                ax.plot(x[order], y[order], color=STYLE[name]["color"], lw=1.4, ls="-.", zorder=3)
                drawn = True
        if drawn:
            handles.append(Line2D([], [], color=INK, lw=1.4, ls="-.", label=MODEL_E_LABEL))
    handles += [Line2D([], [], color="white", marker="D", ms=5.5, mec=INK, mew=0.9, ls="none", label="Arbeitspunkt h = 1,0"),
                Line2D([], [], color="white", marker="*", ms=12, mec="black", mew=0.7, ls="none", label=net_label(rows))]
    if hull:
        handles.append(Line2D([], [], color=GREY, lw=1.3, ls="--", label=HULL_LABEL))
    xs_all = _positive(r.get("falsch_je_bett_h") for r in rows if r.get("art") == "Regel")
    if zoom:
        ax.set_xlim(*ZOOM_X)
        ax.set_ylim(*ZOOM_Y)
        ax.set_xlabel(f"dasselbe, Ausschnitt {de(ZOOM_X[0])} … {de(ZOOM_X[1])}", fontsize=8.5)
        fx.de_axes(ax, x=True, y=True)
    elif log_x:
        ax.set_xscale("log")
        lo, hi = (min(xs_all) * 0.7, max(xs_all) * 1.4) if xs_all else (0.01, 100.0)
        ax.set_xlim(lo, hi)
        log_axis(ax, "x", lo, hi)
        ax.set_xlabel(X_COUNT + ", logarithmisch", fontsize=8.5)
        fx.de_axes(ax, x=False, y=True)
        ax.set_ylim(0, 102)
    else:
        ax.set_xlim(0, XMAX_COUNT)
        ax.set_xlabel(X_COUNT + f", linear bis {de(XMAX_COUNT)}", fontsize=8.5)
        fx.de_axes(ax, x=True, y=True)
        ax.set_ylim(0, 102)
    ax.set_ylabel(Y_COUNT, fontsize=8.5)
    _grid(ax)
    return handles


def _zoom_rectangle(ax) -> None:
    ax.add_patch(Rectangle((ZOOM_X[0], ZOOM_Y[0]), ZOOM_X[1] - ZOOM_X[0], ZOOM_Y[1] - ZOOM_Y[0], fill=False,
                           edgecolor=GREY, lw=0.9, ls="--", zorder=8))
    ax.annotate("Ausschnitt rechts", xy=(ZOOM_X[1], ZOOM_Y[0]), xytext=(3, -8), textcoords="offset points",
                fontsize=6.8, color=GREY, ha="left", va="top")


def fig_R2(rows: Sequence[dict], tables: Optional[dict] = None, x_mode: str = "beide", T: float = T_MAIN,
           title: bool = True) -> Figure:
    """Nach Stück: links linear bis 10, rechts Ausschnitt x 1 … 6, y 88 … 100 %; gestrichelt grau die obere konvexe
    Hülle der festen k-aus-4-Regeln (aus roc_tabellen.json); Modell E1+E2 strichpunktiert (erkannte Krisen mild,
    Fehleskalationen = Episodenteil von E2), nur wenn der Episodenteil für jedes h vorliegt."""
    hull = hull_points(tables, "stueck")
    if x_mode == "beide":
        fig, axes = _two_panels(0.62)
        handles = _draw_count(axes[0], rows, tables, False, T, hull=hull)
        _zoom_rectangle(axes[0])
        handles = _draw_count(axes[1], rows, tables, False, T, zoom=True, hull=hull)
        axes[1].set_ylabel("")
    else:
        fig, ax = plt.subplots(figsize=(W, 0.6 * W))
        handles = _draw_count(ax, rows, tables, x_mode == "log", T, hull=hull)
    if title:
        fig.suptitle(TITLE_COUNT, fontsize=8.5, y=0.995)
    fig.legend(handles=handles, loc="lower center", ncol=3, fontsize=6.6, frameon=False, bbox_to_anchor=(0.5, 0.01),
               handlelength=2.6, columnspacing=1.1)
    _layout(fig, bottom=0.30, top=0.93 if title else 0.97)
    return fig


def fig_R2_vergleich(rows_a: Sequence[dict], tables_a: Optional[dict], rows_b: Sequence[dict], tables_b: Optional[dict],
                     labels: Tuple[str, str] = ("η = 0,9", "η = 0,7"), T: float = T_MAIN) -> Figure:
    """Zwei Panels mit gleichen Achsen (linear bis 10, 0 … 100 %): links η = 0,9, rechts η = 0,7; je Panel Messung,
    Modell E1+E2 und die Hülle der festen Regeln des jeweiligen Datensatzes; ohne Titel."""
    fig, axes = plt.subplots(1, 2, figsize=(W, 0.6 * W), sharey=True, gridspec_kw=dict(wspace=0.12))
    handles: List[Line2D] = []
    for ax, rows, tb, lab in zip(axes, (rows_a, rows_b), (tables_a, tables_b), labels):
        handles = _draw_count(ax, rows, tb, False, T, hull=hull_points(tb, "stueck"))
        ax.set_title(lab, fontsize=9)
    axes[1].set_ylabel("")
    axes[1].set_xlabel("dasselbe", fontsize=8.5)
    fig.legend(handles=handles, loc="lower center", ncol=3, fontsize=6.6, frameon=False, bbox_to_anchor=(0.5, 0.01),
               handlelength=2.6, columnspacing=1.1)
    _layout(fig, bottom=0.30, top=0.93, wspace=0.12)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# R3 – Übertragung Gerät → System
# ─────────────────────────────────────────────────────────────────────────────

def fig_R3(rows: Sequence[dict], tables: Optional[dict] = None, variant: str = "ii", T: float = T_MAIN) -> Figure:
    xk = _xkey(variant)
    fig, ax = plt.subplots(figsize=(W, 0.72 * W))
    handles: List[Line2D] = []
    slopes = (tables or {}).get("steigungen", {})
    anchor = None
    xs_all: List[float] = []
    ys_all: List[float] = []
    for name in RULES:
        st = STYLE[name]
        sel = curve(rows, "Regel", name, T)
        x, y = xy(sel, "fpr_j_mittel", xk)
        ok = (x > 0) & (y > 0)
        x, y = x[ok], y[ok]
        xs_all += list(x)
        ys_all += list(y)
        ax.plot(x, y, color=st["color"], lw=st["lw"], ls="-", marker="o", ms=2.4, mew=0, zorder=4)
        s = slopes.get(name, {}).get("niedrig")
        lab = f"{name} (Steigung {de(s, 2)} bei FPR̄ ≤ 0,03)" if s is not None and np.isfinite(s) else name
        handles.append(Line2D([], [], color=st["color"], lw=st["lw"], marker="o", ms=2.4, mew=0, label=lab))
        w = _work(sel)
        if w is not None and (w.get(xk) or 0) > 0:
            _marker_work(ax, w["fpr_j_mittel"], w[xk], st["color"])
            if name == "2-aus-4":
                anchor = (w["fpr_j_mittel"], w[xk])
    if xs_all and ys_all:
        lo, hi = min(xs_all) * 0.8, max(xs_all) * 1.25
        ylo, yhi = min(ys_all) * 0.6, max(ys_all) * 1.8
        if anchor is not None:
            x0, y0 = anchor
            xx = np.geomspace(lo, hi * 1.5, 200)
            for s in (1, 2, 3):
                yy = y0 * (xx / x0) ** s
                m = (yy >= ylo) & (yy <= yhi)
                if m.any():
                    ax.plot(xx[m], yy[m], color=GREY, lw=0.9, ls="--", zorder=2)
                    ax.annotate(f"Steigung {s}", xy=(xx[m][0], yy[m][0]), xytext=(4, 3), textcoords="offset points",
                                fontsize=7, color=GREY, ha="left", va="bottom")
        ax.set_xlim(lo, hi * 1.9)
        ax.set_ylim(ylo, yhi)
        ax.set_xscale("log")
        ax.set_yscale("log")
        log_axis(ax, "x", lo, hi * 1.9)
        log_axis(ax, "y", ylo, yhi)
    handles += [Line2D([], [], color=GREY, lw=0.9, ls="--", label="Orientierung: Steigung 1, 2, 3 durch den Arbeitspunkt von 2-aus-4"),
                Line2D([], [], color="white", marker="D", ms=5.5, mec=INK, mew=0.9, ls="none", label="h = 1,0")]
    ax.set_xlabel("Anteil der Zeit mit Gerätealarm außerhalb von Krisen, Mittel der Geräte FPR̄, logarithmisch", fontsize=8.5)
    ax.set_ylabel(X_TIME + ", logarithmisch", fontsize=8.5)
    _grid(ax)
    fig.suptitle("Übertragung Gerät → System: Fehlalarmzeit der Geräte und Fehleskalationszeit des Systems", fontsize=8.5, y=0.995)
    fig.legend(handles=handles, loc="lower center", ncol=2, fontsize=6.8, frameon=False, bbox_to_anchor=(0.5, 0.01),
               handlelength=2.6, columnspacing=1.2)
    _layout(fig, bottom=0.27, top=0.93, left=0.11)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# R4a / R4b – Fenster T
# ─────────────────────────────────────────────────────────────────────────────

def _draw_T_time(ax, rows: Sequence[dict], tables: Optional[dict], log_x: bool, variant: str,
                 Ts: Sequence[float]) -> List[Line2D]:
    xk = _xkey(variant)
    handles: List[Line2D] = []
    for name in ("2-aus-4", "3-aus-4"):
        st = STYLE[name]
        sel = curve(rows, "Regel", name)
        x, y = xy(sel, xk, "tpr")
        ax.plot(x, y, color=st["color"], lw=1.6, ls="-", marker="o", ms=2.2, mew=0, zorder=3)
        handles.append(Line2D([], [], color=st["color"], lw=1.6, marker="o", ms=2.2, mew=0,
                              label=_pauc_label(name, tables, T_MAIN, variant)))
        w = _work(sel)
        if w is not None and w.get(xk) is not None:
            _marker_work(ax, w[xk], w["tpr"], st["color"], size=5.0)
    for T in Ts:
        sel = curve(rows, "Regel", "adaptiv", T)
        if not sel:
            continue
        color = T_SHADES.get(T, STYLE["adaptiv"]["color"])
        x, y = xy(sel, xk, "tpr")
        ax.plot(x, y, color=color, lw=2.6, ls="-", marker="o", ms=2.4, mew=0, zorder=5)
        lab = _pauc_label("adaptiv", tables, T, variant).replace("adaptiv", f"adaptiv, T = {T:g} s", 1)
        handles.append(Line2D([], [], color=color, lw=2.6, marker="o", ms=2.4, mew=0, label=lab))
        w = _work(sel)
        if w is not None and w.get(xk) is not None:
            _marker_work(ax, w[xk], w["tpr"], color)
    handles.append(Line2D([], [], color="white", marker="D", ms=5.5, mec=INK, mew=0.9, ls="none", label="Arbeitspunkt h = 1,0"))
    _finish_time_axes(ax, rows, log_x, variant, xk)
    return handles


def fig_R4a(rows: Sequence[dict], tables: Optional[dict] = None, variant: str = "ii",
            Ts: Sequence[float] = (10.0, 20.0, 30.0), x_mode: str = "beide") -> Figure:
    if x_mode == "beide":
        fig, axes = _two_panels()
        handles = _draw_T_time(axes[0], rows, tables, False, variant, Ts)
        _draw_T_time(axes[1], rows, tables, True, variant, Ts)
        axes[1].set_ylabel("")
        axes[1].set_xlabel(X_SAME_LOG, fontsize=8.5)
    else:
        fig, ax = plt.subplots(figsize=(W, 0.62 * W))
        handles = _draw_T_time(ax, rows, tables, x_mode == "log", variant, Ts)
    fig.suptitle(TITLE_TIME.replace("Gerätegrenzwerte variiert", "Gerätegrenzwerte variiert; Fenster T des adaptiven Filters"),
                 fontsize=8.0, y=0.995)
    fig.legend(handles=handles, loc="lower center", ncol=3, fontsize=6.8, frameon=False, bbox_to_anchor=(0.5, 0.01),
               handlelength=2.6, columnspacing=1.2)
    _layout(fig, bottom=0.29, top=0.93)
    return fig


def fig_R4b(rows: Sequence[dict], Ts: Sequence[float] = (10.0, 20.0, 30.0), x_mode: str = "beide") -> Figure:
    if x_mode == "beide":
        fig, axes = _two_panels(0.62)
        handles = _draw_count(axes[0], rows, None, False, T_MAIN, with_model=False, adaptiv_Ts=Ts)
        _draw_count(axes[1], rows, None, True, T_MAIN, with_model=False, adaptiv_Ts=Ts)
        axes[1].set_ylabel("")
        axes[1].set_xlabel(X_SAME_LOG, fontsize=8.5)
    else:
        fig, ax = plt.subplots(figsize=(W, 0.6 * W))
        handles = _draw_count(ax, rows, None, x_mode == "log", T_MAIN, with_model=False, adaptiv_Ts=Ts)
    fig.suptitle(TITLE_COUNT + " (Fenster T des adaptiven Filters)", fontsize=8.2, y=0.995)
    fig.legend(handles=handles, loc="lower center", ncol=3, fontsize=6.8, frameon=False, bbox_to_anchor=(0.5, 0.01),
               handlelength=2.6, columnspacing=1.2)
    _layout(fig, bottom=0.29, top=0.93)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# R5 – Fehlerbilanz über die Gerätegrenzwerte
# ─────────────────────────────────────────────────────────────────────────────

def fig_R5(rows: Sequence[dict], T: float = T_MAIN) -> Figure:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(W, 0.95 * W), sharex=True, gridspec_kw=dict(hspace=0.12))
    handles: List[Line2D] = []
    for name in RULES:
        st = STYLE[name]
        sel = curve(rows, "Regel", name, T)
        h, f = xy(sel, "h", "falsch_je_bett_h")
        ok = f > 0
        ax1.plot(h[ok], f[ok], color=st["color"], lw=st["lw"], ls="-", marker="o", ms=2.4, mew=0, zorder=4)
        h, miss = xy(sel, "h", "eta_oder_erkannt")
        ax2.plot(h, 100 * (1 - miss), color=st["color"], lw=st["lw"], ls="-", marker="o", ms=2.4, mew=0, zorder=4)
        late = [(r["h"], 100 * (r["eta_oder_erkannt"] - r["erkannt_streng"])) for r in sel
                if r.get("erkannt_streng") is not None and r.get("eta_oder_erkannt") is not None]
        if late:
            ax2.plot([a for a, _ in late], [b for _, b in late], color=st["color"], lw=max(0.9, st["lw"] - 0.8), ls="--", zorder=3)
        lab = name if name != "adaptiv" else f"adaptiv (T = {T:g} s)"
        handles.append(Line2D([], [], color=st["color"], lw=st["lw"], marker="o", ms=2.4, mew=0, label=lab))
    handles += [Line2D([], [], color=INK, lw=1.4, ls="-", label="unten durchgezogen: nicht erkannte Krisen (kein Rot bis Ende + 10 s)"),
                Line2D([], [], color=INK, lw=1.0, ls="--", label="unten gestrichelt: verspätet erkannt (Rot erst nach Krisenende, binnen 10 s)")]
    for ax in (ax1, ax2):
        ax.axvline(H_WORK, color=GREY, ls=":", lw=1.2, zorder=1)
        _grid(ax)
    ax1.text(H_WORK, 1.0, " h = 1,0", transform=ax1.get_xaxis_transform(), ha="left", va="top", fontsize=7.5, color=GREY)
    fs = _positive(r.get("falsch_je_bett_h") for r in rows if r.get("art") == "Regel")
    if fs:
        ax1.set_yscale("log")
        ax1.set_ylim(min(fs) * 0.7, max(fs) * 1.4)
        log_axis(ax1, "y", min(fs) * 0.7, max(fs) * 1.4)
    ax1.set_ylabel("Fehleskalationen je Bett und Stunde", fontsize=8.5)
    ax2.set_ylabel("Krisen (%): nicht erkannt / verspätet", fontsize=8.5)
    ax2.set_ylim(0, 100)
    ax2.set_xlabel("Gerätegrenzwert (Vielfaches des Normalabstands)", fontsize=8.5)
    hs = sorted({r["h"] for r in rows if r.get("art") == "Regel" and r.get("h") is not None})
    if hs:
        ax2.set_xlim(0, max(hs) * 1.04)
    fx.de_axes(ax1, x=True, y=False)
    fx.de_axes(ax2, x=True, y=True)
    fig.suptitle("Fehlerbilanz über die Gerätegrenzwerte: beide Fehler getrennt, nach Stück", fontsize=8.5, y=0.995)
    fig.legend(handles=handles, loc="lower center", ncol=2, fontsize=6.8, frameon=False, bbox_to_anchor=(0.5, 0.01),
               handlelength=2.6, columnspacing=1.2)
    _layout(fig, bottom=0.19, top=0.95, left=0.11, hspace=0.10)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────────────────────────

def save(fig: Figure, outdir: Path, stem: str) -> List[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    out = []
    for ext, kw in (("png", dict(dpi=300)), ("svg", {})):
        p = outdir / f"{stem}.{ext}"
        fig.savefig(p, **kw)
        out.append(p)
    plt.close(fig)
    return out


def available_T(rows: Sequence[dict]) -> List[float]:
    return sorted({r["T_s"] for r in rows if r.get("art") == "Regel" and r.get("name") == "adaptiv" and r.get("T_s") is not None}) or [T_MAIN]


def export_all(rows: Sequence[dict], tables: Optional[dict], outdir: Path, variant: str = "ii",
               x_mode: str = "beide", suffix: str = "", only_new: bool = False) -> List[Path]:
    """``suffix`` (z. B. ``_p07``) wird in den Dateinamen eingefügt (R1a_p07_…); ``only_new`` schreibt nur die
    neuen Abbildungen (R1a/R2 bei Suffix, sonst R2_v2) und lässt die bisherigen Dateien unangetastet."""
    Ts = available_T(rows)
    written: List[Path] = []

    def stem(key: str) -> str:
        base = FILES[key]
        if not suffix:
            return base
        head, _, rest = base.partition("_")
        return f"{head}{suffix}_{rest}"
    if suffix:
        written += save(fig_R1a(rows, tables, variant, x_mode, title=False), outdir, stem("R1a"))
        written += save(fig_R2(rows, tables, x_mode, title=False), outdir, stem("R2"))
    else:
        written += save(fig_R2(rows, tables, x_mode, title=False), outdir, FILES["R2_v2"])
    if only_new:
        return written
    if not suffix:
        written += save(fig_R1a(rows, tables, variant, x_mode), outdir, FILES["R1a"])
    written += save(fig_R1b(rows, tables, variant), outdir, stem("R1b"))
    written += save(fig_R1b(rows, tables, variant, log_x=True), outdir, stem("R1b_log"))
    written += save(fig_R1b_stueck(rows, tables), outdir, stem("R1b_stueck"))
    written += save(fig_R3(rows, tables, variant), outdir, stem("R3"))
    if len(Ts) > 1:
        written += save(fig_R4a(rows, tables, variant, Ts, x_mode), outdir, stem("R4a"))
        written += save(fig_R4b(rows, Ts, x_mode), outdir, stem("R4b"))
    for T in Ts:
        written += save(fig_R5(rows, T), outdir, f"{stem('R5')}_T{T:g}")
    return written
