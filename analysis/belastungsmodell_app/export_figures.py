"""Abbildungen des Belastungsmodells nach der Gliederung von Kapitel 5 (5.3.2 / 5.3.3).

Jede ``fig_*``-Funktion liefert eine ``Figure`` ohne Titel (die Bildunterschrift steht im
Text der Arbeit); ``app.py`` zeigt sie an, ``export_all`` löscht die alten Dateien in
``figures/`` und schreibt den Satz neu als PNG (300 dpi) und SVG.  Aufruf als Skript:

    .venv\\Scripts\\python analysis/belastungsmodell_app/export_figures.py [--outdir DIR] [--szenario NAME]

Gestaltung: Breite 15 cm, Schrift 10 pt (Achsen 10, Teilstriche 9, Beschriftungen 8,5);
adaptiv blau durchgezogen (dick), ohne Filter dunkelgrau, rein 2-aus-N grün gestrichelt,
rein 3-aus-N violett strichpunktiert; bei höchstens vier Linien stehen die Namen am rechten
Linienende statt in einer Legende; senkrechte gepunktete Linie = aktueller Wert; unten eine
graue Zeile mit den Parametern der Rechnung.  Symbole werden in Worten erklärt
(``delta_text``, ``k_text``, ``AXIS``).
"""

from __future__ import annotations

import argparse
import math
import textwrap
from dataclasses import replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter  # noqa: E402

try:  # als Paketmodul (Tests, python -m) …
    from . import model as bm
except ImportError:  # … oder als Skript / unter streamlit run
    import model as bm

FIG_DIR = Path(__file__).resolve().parent / "figures"

W = 15.0 / 2.54          # Breite 15 cm
ANN = 8.5                # Beschriftungen
STYLE: Dict[str, dict] = {
    "adaptiv":      dict(color="#2a78d6", ls="-", lw=2.6),
    "ohne Filter":  dict(color="#3d3d3a", ls="-", lw=1.8),
    "rein 2-aus-N": dict(color="#1baf7a", ls="--", lw=1.8),
    "rein 3-aus-N": dict(color="#6250d6", ls="-.", lw=1.8),
    "Basis":        dict(color="#eb6834", ls="-", lw=1.6),
}
GREY = "#9a9a94"
INK = "#52514e"
PAIR_COLORS = {"hoch+hoch": "#2a78d6", "hoch+niedrig": "#eb6834", "niedrig+niedrig": "#1baf7a"}
PAIR_TEXT = {"hoch+hoch": "zwei Quellen hoher Priorität", "hoch+niedrig": "hohe + niedrige Priorität",
             "niedrig+niedrig": "zwei Quellen niedriger Priorität"}
SEQ_COLORS = {"hoch→hoch": "#2a78d6", "gemischt": "#eb6834", "hoch→niedrig": "#eb6834", "niedrig→hoch": "#6250d6",
              "niedrig→niedrig": "#1baf7a"}
SEQ_STYLES = {"hoch→hoch": "-", "gemischt": "--", "hoch→niedrig": "--", "niedrig→hoch": "-.", "niedrig→niedrig": "-"}
SEQ_TEXT = {"hoch→hoch": "hohe → hohe Priorität", "gemischt": "gemischt (hohe + niedrige Priorität)",
            "hoch→niedrig": "hohe → niedrige Priorität", "niedrig→hoch": "niedrige → hohe Priorität",
            "niedrig→niedrig": "niedrige → niedrige Priorität"}
AXIS = {
    "mu": "mittlere Dauer eines Artefakts (μ_art) [s]",
    "d_true": "mittlere Dauer eines Krisenalarms (d_true) [s]",
    "c_art": "Anteil gemeinsamer Artefakte (c_art)",
    "MFPR": "Fehlalarmbelegung je Bett (|M|·FPR)",
    "t0": "Beginn der zweiten Quelle (t0) [s]",
    "M": "Zahl der Quellen |M| (n_H = |M| div 2)",
    "tpr": "Sensitivität je Quelle (TPR_j)",
    "alpha": "Gewicht der höchsten Priorität (α)",
}
CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
FAMILY_RAMP = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#0d366b"]
LINESTYLES = ["-", "--", "-.", ":"]

plt.rcParams.update({
    "font.size": 10,
    "axes.titlesize": 10,
    "axes.labelsize": 10,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "legend.fontsize": ANN,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.15,
    "grid.linestyle": ":",
    "lines.linewidth": 1.8,
    "figure.dpi": 110,
    "savefig.bbox": "tight",
    "legend.frameon": False,
})


# ─────────────────────────────────────────────────────────────────────────────
# Hilfsfunktionen: Zahlen, Achsen, Texte
# ─────────────────────────────────────────────────────────────────────────────

def de(x: float, digits: Optional[int] = None) -> str:
    """Zahl mit Dezimalkomma."""
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "∞" if x == float("inf") else "–"
    s = f"{x:.{digits}f}" if digits is not None else f"{x:g}"
    return s.replace(".", ",")


_DE_FMT = FuncFormatter(lambda v, _pos: de(v))


def de_axes(ax, x: bool = True, y: bool = True) -> None:
    """Dezimalkomma auf den Achsen."""
    if x:
        ax.xaxis.set_major_formatter(_DE_FMT)
        ax.xaxis.set_minor_formatter(NullFormatter())
    if y:
        ax.yaxis.set_major_formatter(_DE_FMT)
        ax.yaxis.set_minor_formatter(NullFormatter())


def log_ticks(ax, axis: str = "x") -> None:
    """Logarithmische Achse mit Hauptteilstrichen bei 1, 2, 5 je Dekade und Dezimalkomma."""
    a = ax.xaxis if axis == "x" else ax.yaxis
    a.set_major_locator(LogLocator(base=10.0, subs=(1.0, 2.0, 5.0), numticks=20))
    a.set_major_formatter(_DE_FMT)
    a.set_minor_formatter(NullFormatter())


def mark_current(ax, x: float, label: str = "aktuell", pos: str = "top") -> None:
    """Senkrechte gepunktete Linie am aktuellen Parameterwert."""
    if x is None or not np.isfinite(x):
        return
    ax.axvline(x, color=GREY, linestyle=":", linewidth=1.2, zorder=1)
    y, va = (1.0, "top") if pos == "top" else (0.02, "bottom")
    ax.text(x, y, f" {label}", transform=ax.get_xaxis_transform(), ha="left", va=va, fontsize=7.5, color=GREY)


def _finite(y) -> np.ndarray:
    y = np.asarray(y, dtype=float)
    return np.where(np.isfinite(y), y, np.nan)


def _pct(y) -> np.ndarray:
    return 100.0 * _finite(y)


def plot_rule(ax, x, y, name: str, **kw):
    s = STYLE[name]
    kw.setdefault("label", name)
    return ax.plot(x, y, color=s["color"], linestyle=s["ls"], linewidth=s["lw"], **kw)


def unit_label(beds: int) -> str:
    return "je Bett und Tag" if beds <= 1 else f"je Station ({beds} Betten) und Tag"


def delta_text(d: float) -> str:
    """δ_min in Worten."""
    if abs(d - 1.0) < 1e-9:
        return "ohne Schmelzen (δ_min = 1)"
    if abs(d - 0.5) < 1e-9:
        return "Schwelle sinkt höchstens auf die Hälfte (δ_min = 0,5)"
    if abs(d - 0.1) < 1e-9:
        return "Schwelle sinkt höchstens auf ein Zehntel (δ_min = 0,1)"
    return f"Schwelle sinkt höchstens auf {de(100 * d)} % (δ_min = {de(d)})"


def k_text(k: int, M: int) -> str:
    """Quorum in Worten: '2 von 4 Quellen (k = 2)'."""
    return f"{k} von {M} Quellen (k = {k})"


def rule_text(rule: bm.Rule, M: int) -> str:
    """Regelname mit Erklärung für Beschriftungen."""
    if rule.kind == "rein":
        k = bm.effective_k(rule, M)
        return f"{rule.display} ({k} von {M} Quellen)"
    if rule.kind == "basis":
        return "Basis (k = 2 mit Schmelzen)"
    return rule.display


def combo_text(r: int, h: int) -> str:
    """Kombination in Worten: '2 × hohe, 1 × niedrige Priorität'."""
    parts = []
    if h:
        parts.append(f"{h} × hohe")
    if r - h:
        parts.append(f"{r - h} × niedrige")
    return ", ".join(parts) + " Priorität"


SCENARIO_TEXT = {"Median": "Median-Intensivstation"}


def footer_text(p: bm.Params) -> str:
    """Parameterzeile unter jeder Abbildung, aus den tatsächlichen Parametern."""
    name = None
    for key, vals in bm.SCENARIOS.items():
        if all(abs(float(getattr(p, a)) - float(b)) < 1e-9 for a, b in vals.items()):
            name = key
            break
    parts = [f"|M| = {p.M} ({p.n_high} hoch, {p.M - p.n_high} niedrig)", f"T = {de(p.T)} s",
             f"δ_min = {de(p.delta_min)}", f"α = {de(p.alpha)}", f"c_art = {de(p.c_art)}", f"μ_art = {de(p.d_false)} s"]
    if name is None:
        head = "Parameter"
        parts = [f"n = {de(p.n)}", f"FDR_cnt = {de(p.fdr)}", f"d_true = {de(p.d_true)} s"] + parts
    else:
        head = SCENARIO_TEXT.get(name, name)
    if abs(p.tpr - 0.9) > 1e-9:
        parts.append(f"TPR_j = {de(p.tpr)}")
    if abs(p.fdr - 0.9) > 1e-9 and name is not None:
        parts.append(f"FDR_cnt = {de(p.fdr)}")
    if abs(p.OR - 1.0) > 1e-9:
        parts.append(f"OR = {de(p.OR)}")
    if p.noise_direct is not None:
        parts.append(f"|M|·FPR = {de(p.noise_direct)} (direkt)")
    return f"{head}: " + ", ".join(parts)


def finish(fig: Figure, p: bm.Params, bottom: float = 0.065) -> None:
    """Layout abschließen und die Parameterzeile unten anbringen."""
    fig.tight_layout(rect=(0, bottom, 1, 1))
    fig.text(0.01, 0.008, footer_text(p), fontsize=7.5, color=GREY, ha="left", va="bottom")


def end_labels(axes: Sequence, items_per_ax: Sequence[Sequence[tuple]], fontsize: float = ANN,
               gap_pt: float = 9.5, pad_pt: float = 4.0) -> None:
    """Namen am rechten Linienende statt Legende.

    ``axes``: Achsen mit gemeinsamer x-Achse; ``items_per_ax``: je Achse (x, y, Text, Farbe).
    Die Texte werden senkrecht auseinandergeschoben; die x-Achse wird nach rechts verlängert,
    damit die Texte in der Zeichenfläche liegen (Teilstriche bleiben im Datenbereich).
    Aufruf nach ``finish`` (Layout muss feststehen).
    """
    fig = axes[0].figure
    fig.draw_without_rendering()   # Layout und Textmaße ohne Rasterung
    renderer = fig.canvas.get_renderer()
    px_per_pt = fig.dpi / 72.0
    line_px = fontsize * 1.25 * px_per_pt          # Zeilenhöhe eines Textes
    placed = []
    for ax, items in zip(axes, items_per_ax):
        pts = []
        for x, y, text, color in items:
            if x is None or y is None or not (np.isfinite(x) and np.isfinite(y)):
                continue
            xd, yd = ax.transData.transform((float(x), float(y)))
            pts.append([xd, yd, float(x), float(y), text, color, (text.count("\n") + 1) * line_px])
        if not pts:
            continue
        pts.sort(key=lambda r: r[1])
        for i in range(1, len(pts)):
            need = 0.5 * (pts[i - 1][6] + pts[i][6]) + 2.0 * px_per_pt   # Mitte zu Mitte, mehrzeilig berücksichtigt
            if pts[i][1] - pts[i - 1][1] < need:
                pts[i][1] = pts[i - 1][1] + need
        bb = ax.get_window_extent(renderer)
        over = (pts[-1][1] + 0.5 * pts[-1][6]) - bb.y1
        if over > 0:
            for r in pts:
                r[1] -= over
        for _xd, yd_new, x, y, text, color, _h in pts:
            dy_pt = (yd_new - ax.transData.transform((x, y))[1]) / px_per_pt
            t = ax.annotate(text, (x, y), xytext=(pad_pt, dy_pt), textcoords="offset points", ha="left",
                            va="center", fontsize=fontsize, color=color, annotation_clip=False)
            placed.append((ax, t))
    if not placed:
        return
    fig.draw_without_rendering()   # Layout und Textmaße ohne Rasterung
    renderer = fig.canvas.get_renderer()
    frac = 0.0
    for ax, t in placed:
        bb = ax.get_window_extent(renderer)
        tb = t.get_window_extent(renderer)
        frac = max(frac, (tb.x1 + 3.0 - bb.x1) / bb.width)
    if frac > 0:
        frac = min(frac, 0.6)
        for ax in axes:
            x0, x1 = ax.get_xlim()
            if ax.get_xscale() == "log":
                l0, l1 = np.log10(x0), np.log10(x1)
                new = 10 ** (l1 + (l1 - l0) * frac / (1 - frac))
            else:
                new = x1 + (x1 - x0) * frac / (1 - frac)
            ticks = [t for t in ax.get_xticks() if x0 - 1e-9 <= t <= x1 + 1e-9]
            ax.set_xlim(x0, new)
            ax.set_xticks(ticks)
    for ax in axes:
        _thin_xticks(ax)


def _thin_xticks(ax, attempts: int = 3) -> None:
    """Teilstrichbeschriftungen ausdünnen, bis sie sich nicht mehr überlappen."""
    fig = ax.figure
    for attempt in range(attempts):
        fig.draw_without_rendering()
        renderer = fig.canvas.get_renderer()
        labels = [t for t in ax.get_xticklabels() if t.get_visible() and t.get_text()]
        boxes = sorted((t.get_window_extent(renderer) for t in labels), key=lambda b: b.x0)
        boxes = [b for b in boxes if b.width > 0]
        if not any(boxes[i].x1 + 2.0 > boxes[i + 1].x0 for i in range(len(boxes) - 1)):
            return
        ticks = sorted(t for t in ax.get_xticks() if ax.get_xlim()[0] - 1e-9 <= t <= ax.get_xlim()[1] + 1e-9)
        if ax.get_xscale() == "log":
            def mant(v):
                return round(v / 10 ** math.floor(math.log10(v)), 6)
            keep = {1.0, 5.0} if attempt == 0 else {1.0}
            new = [t for t in ticks if mant(t) in keep]
        else:
            new = ticks[::2]
        if len(new) < 2 or len(new) >= len(ticks):
            return
        ax.set_xticks(new)


# ─────────────────────────────────────────────────────────────────────────────
# 5.3.2  Belastung ohne und mit Filter (nur ohne Filter und adaptiv)
# ─────────────────────────────────────────────────────────────────────────────

def fig_1a(p: bm.Params, beds: int = 1) -> Figure:
    """Fehlalarme je Tag über der Zahl der Quellen |M| (n_H = |M| div 2)."""
    Ms = np.arange(2, 13)
    fig, ax = plt.subplots(figsize=(W, 3.4))
    items = []
    for rule in (bm.OHNE, bm.ADAPTIV):
        ys = beds * _finite(bm.sweep(p, "M", Ms, rule, "Fehlalarme/Tag"))
        plot_rule(ax, Ms, ys, rule.display, marker="o", markersize=3.5)
        items.append((Ms[-1], ys[-1], rule.display, STYLE[rule.display]["color"]))
    mark_current(ax, p.M, f"|M| = {p.M}")
    ax.set_xlabel(AXIS["M"])
    ax.set_ylabel(f"Fehlalarme {unit_label(beds)}")
    ax.set_yscale("log")
    ax.set_xticks(Ms)
    de_axes(ax, y=False)
    log_ticks(ax, "y")
    finish(fig, p)
    end_labels([ax], [items])
    return fig


def fig_1b(p: bm.Params, beds: int = 1) -> Figure:
    """Fehlalarme je Tag über der Fehlalarmbelegung |M|·FPR (beide Achsen logarithmisch)."""
    xs = np.logspace(np.log10(0.005), np.log10(1.2), 60)
    cal = bm.calibrate(p)
    fig, ax = plt.subplots(figsize=(W, 3.5))
    ax.axvspan(0.008, 0.35, color=GREY, alpha=0.15, linewidth=0, zorder=0)
    ax.text(math.sqrt(0.008 * 0.35), 0.97, "Szenarien aus Tabelle 5.7", transform=ax.get_xaxis_transform(),
            ha="center", va="top", fontsize=ANN, color=INK)
    items = []
    for rule in (bm.OHNE, bm.ADAPTIV):
        ys = beds * _finite(bm.sweep(p, "MFPR", xs, rule, "Fehlalarme/Tag"))
        plot_rule(ax, xs, ys, rule.display)
        items.append((xs[-1], ys[-1], rule.display, STYLE[rule.display]["color"]))
    mark_current(ax, cal.MFPR, f"aktuell {de(cal.MFPR, 4)}", pos="bottom")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel(AXIS["MFPR"])
    ax.set_ylabel(f"Fehlalarme {unit_label(beds)}")
    log_ticks(ax, "x")
    log_ticks(ax, "y")
    finish(fig, p)
    end_labels([ax], [items])
    return fig


def fig_1c(p: bm.Params, beds: int = 1) -> Figure:
    """Fehlalarme je Tag (adaptiv) über c_art mit der Linie ohne Filter und der Untergrenze aus der Decke."""
    cs = np.linspace(0.0, 1.0, 101)
    cal = bm.calibrate(p)
    fig, ax = plt.subplots(figsize=(W, 3.4))
    ohne = beds * bm.evaluate(p, bm.OHNE, cal).false_per_day
    plot_rule(ax, cs, np.full_like(cs, ohne), "ohne Filter")
    floor = ohne * cs / p.M
    ax.plot(cs, floor, color=GREY, linewidth=5, alpha=0.45)
    ys = beds * _finite(bm.sweep(p, "c_art", cs, bm.ADAPTIV, "Fehlalarme/Tag"))
    plot_rule(ax, cs, ys, "adaptiv")
    mark_current(ax, p.c_art, f"c_art = {de(p.c_art)}")
    ax.set_xlabel(AXIS["c_art"])
    ax.set_ylabel(f"Fehlalarme {unit_label(beds)}")
    ax.set_ylim(0, ohne * 1.12)
    de_axes(ax)
    finish(fig, p)
    end_labels([ax], [[(1.0, ohne, "ohne Filter", STYLE["ohne Filter"]["color"]),
                       (1.0, floor[-1], "Untergrenze: ohne Filter · c_art/|M|\n(Decke der ARR 1 − c_art/|M|)", INK),
                       (1.0, ys[-1], "adaptiv", STYLE["adaptiv"]["color"])]])
    return fig


def fig_1d(p: bm.Params, beds: int = 1, rule: bm.Rule = bm.ADAPTIV) -> Figure:
    """Tornado: Einzelvariation der Parameter um den Arbeitspunkt (Fehlalarme je Tag, System-TPR)."""
    base, rows = bm.tornado(p, rule)
    fig, axes = plt.subplots(1, 2, figsize=(W, 4.4))
    panels = ((f"Fehlalarme {unit_label(beds)}", lambda r: r.false_per_day, float(beds)),
              ("System-TPR [%]", lambda r: r.TPR_sys, 100.0))
    swing = [abs(r.high.false_per_day - r.low.false_per_day) for r in rows]
    order = np.argsort(swing)
    for ax, (label, getter, scale) in zip(axes, panels):
        b = scale * getter(base)
        for pos, idx in enumerate(order):
            row = rows[idx]
            lo, hi = scale * getter(row.low), scale * getter(row.high)
            ax.barh(pos, lo - b, left=b, color="#2a78d6", height=0.6, edgecolor="white", linewidth=1)
            ax.barh(pos, hi - b, left=b, color="#eb6834", height=0.6, edgecolor="white", linewidth=1)
            ax.text(lo, pos, f" {row.low_label} ", ha="right" if lo <= b else "left", va="center", fontsize=7,
                    color=INK)
            ax.text(hi, pos, f" {row.high_label} ", ha="left" if hi >= b else "right", va="center", fontsize=7,
                    color=INK)
        ax.axvline(b, color="#0b0b0b", linewidth=1)
        ax.set_yticks(range(len(rows)))
        ax.set_yticklabels([rows[i].name for i in order], fontsize=9)
        ax.set_xlabel(label)
        ax.grid(axis="y", visible=False)
        de_axes(ax, y=False)
        xmin, xmax = ax.get_xlim()
        pad = 0.14 * (xmax - xmin)
        ax.set_xlim(xmin - pad, xmax + pad)
    axes[0].set_title(f"Arbeitspunkt {de(float(beds) * base.false_per_day, 1)}", fontsize=ANN)
    axes[1].set_title(f"Arbeitspunkt {de(100 * base.TPR_sys, 2)} %", fontsize=ANN)
    fig.legend(handles=[Patch(color="#2a78d6", label="niedriger Wert des Parameters"),
                        Patch(color="#eb6834", label="höherer Wert des Parameters")],
               loc="lower center", ncol=2, bbox_to_anchor=(0.5, 0.045))
    finish(fig, p, bottom=0.12)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# 5.3.3  Vergleich mit k-aus-N
# ─────────────────────────────────────────────────────────────────────────────

def _draw_B(ax, p: bm.Params, cal: bm.Calibration, box: Tuple[float, float, float, float], inset: bool) -> None:
    """Betriebskennlinie zeichnen; Beschriftungen nur für Punkte innerhalb (inset) bzw. außerhalb (main) der Box."""
    x0, x1, y0, y1 = box
    M = p.M

    def inside(x, y):
        return x0 <= x <= x1 and y0 <= y <= y1

    def annot(x, y, text, color, dx, dy, ha="left"):
        if inside(x, y) == inset:
            ax.annotate(text, (x, y), textcoords="offset points", xytext=(dx, dy), ha=ha, fontsize=ANN, color=color)

    ks = list(range(1, M + 1))
    rr = [bm.evaluate(p, bm.rein(k), cal) for k in ks]
    xr, yr = [r.false_per_day for r in rr], [100 * r.TPR_sys for r in rr]
    g = STYLE["rein 2-aus-N"]["color"]
    ax.plot(xr, yr, "-o", color=g, markersize=5, zorder=3)
    for k, x, y in zip(ks, xr, yr):
        if k == 1:
            annot(x, y, f"rein k-aus-N: {k_text(1, M)}", g, 6, -11)
        elif k == M:
            annot(x, y, k_text(k, M), g, 10, -3)
        else:
            annot(x, y, k_text(k, M), g, 12, -9 if k == 2 else -4)
    dms = np.round(np.arange(1.0, 0.1 - 1e-9, -0.05), 2)
    ra = [bm.evaluate(replace(p, delta_min=d), bm.ADAPTIV, cal) for d in dms]
    xa, ya = [r.false_per_day for r in ra], [100 * r.TPR_sys for r in ra]
    b = STYLE["adaptiv"]["color"]
    ax.plot(xa, ya, "-", color=b, marker=".", markersize=4, linewidth=2.0, zorder=4)
    annot(xa[0], ya[0], "adaptiv:\n" + delta_text(1.0), b, 12, 10)
    annot(xa[-1], ya[-1], "höchstens auf\nein Zehntel (δ_min = 0,1)", b, 6, 14)
    rc = bm.evaluate(p, bm.ADAPTIV, cal)
    ax.plot([rc.false_per_day], [100 * rc.TPR_sys], "o", markersize=11, markerfacecolor="none", markeredgecolor=b,
            markeredgewidth=1.8, zorder=5)
    ro = bm.evaluate(p, bm.OHNE, cal)
    ax.plot([ro.false_per_day], [100 * ro.TPR_sys], "^", color=STYLE["ohne Filter"]["color"], markersize=8, zorder=5)
    annot(ro.false_per_day, 100 * ro.TPR_sys, "ohne Filter", STYLE["ohne Filter"]["color"], 6, 7)


def fig_B(p: bm.Params) -> Figure:
    """Betriebskennlinie mit vergrößertem Ausschnitt um den Arbeitspunkt."""
    cal = bm.calibrate(p)
    rc = bm.evaluate(p, bm.ADAPTIV, cal)
    xa, ya = rc.false_per_day, 100 * rc.TPR_sys
    box = (0.88 * xa, 1.5 * xa, min(90.0, ya - 2.0), 102.5)
    fig, ax = plt.subplots(figsize=(W, 4.3))
    _draw_B(ax, p, cal, box, inset=False)
    ax.set_xscale("log")
    allx = [bm.evaluate(p, r, cal).false_per_day for r in (bm.OHNE, bm.rein(1), bm.rein(p.M), bm.ADAPTIV)]
    ax.set_xlim(min(allx) / 1.7, max(allx) * 4.4)
    log_ticks(ax, "x")
    ax.set_xlabel("Fehlalarme je Bett und Tag")
    ax.set_ylabel("System-TPR [%]")
    ax.set_ylim(0, 107)
    de_axes(ax, x=False)
    axins = ax.inset_axes([0.48, 0.22, 0.49, 0.56])   # rechts der Beschriftung von k = |M|
    _draw_B(axins, p, cal, box, inset=True)
    axins.set_xlim(box[0], box[1])
    axins.set_ylim(box[2], box[3])
    axins.tick_params(labelsize=8)
    axins.set_xlabel("Ausschnitt um den Arbeitspunkt", fontsize=ANN, color=INK)
    note = "Ring = adaptiv, aktuell:\n" + "\n".join(textwrap.wrap(delta_text(p.delta_min), 30))
    axins.text(0.97, 0.05, note, transform=axins.transAxes, ha="right", va="bottom", fontsize=ANN,
               color=STYLE["adaptiv"]["color"])
    de_axes(axins)
    ax.indicate_inset_zoom(axins, edgecolor=GREY)
    finish(fig, p)
    return fig


def fig_C(p: bm.Params) -> Figure:
    """Anteil eskalierter Doppel-Artefakte über μ_art (konservatives Modell ohne Fenster)."""
    mus = np.linspace(2.0, 60.0, 117)
    cal = bm.calibrate(p)
    fig, ax = plt.subplots(figsize=(W, 3.5))
    items = []
    for name, h in bm.PAIR_KINDS.items():
        cls = bm.pair_class(p, bm.ADAPTIV, h, cal)
        if cls is None:
            continue
        ys = _pct([cls.share(m) for m in mus])
        extra = {"sofort": "", "verzoegert": f"\n(t_esc = {de(cls.t_esc, 1)} s)", "nie": " (nie)"}[cls.branch]
        ax.plot(mus, ys, color=PAIR_COLORS[name], linewidth=2.2)
        items.append((mus[-1], ys[-1], f"adaptiv: {PAIR_TEXT[name]}{extra}", PAIR_COLORS[name]))
    plot_rule(ax, mus, np.full_like(mus, 100.0), "rein 2-aus-N")
    items.append((mus[-1], 100.0, rule_text(bm.rein(2), p.M), STYLE["rein 2-aus-N"]["color"]))
    mark_current(ax, cal.mu_art, f"μ_art = d_false = {de(cal.mu_art)} s")
    ax.set_xlabel(AXIS["mu"])
    ax.set_ylabel("eskalierte Doppel-Artefakte [%]")
    ax.set_ylim(-2, 105)
    de_axes(ax)
    finish(fig, p)
    end_labels([ax], [items])
    return fig


def _crisis_class(p: bm.Params) -> Tuple[int, int]:
    """Krisenepisode mit drei Quellen (hoch, hoch, niedrig), sonst die nächstliegende Kombination."""
    r = min(3, p.M)
    h = min(2, p.n_high, r)
    if r - h > p.M - p.n_high:
        h = r - (p.M - p.n_high)
    return r, h


def fig_C2(p: bm.Params, tau: float = 5.0) -> Figure:
    """Fenstereffekt: eskalierte Artefakt-Episoden (oben) und Krisen-Episoden (unten)."""
    cal = bm.calibrate(p)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(W, 6.2))
    mus = np.linspace(2.0, 60.0, 117)
    g = STYLE["rein 2-aus-N"]["color"]
    b = STYLE["adaptiv"]["color"]
    ax1.plot(mus, _pct([bm.stoss_share(p, bm.ADAPTIV, m, cal=cal) for m in mus]), color=b, linewidth=2.6,
             label=f"adaptiv: Stoß (alle {p.M} Quellen, eine Dauer)")
    h_pair = 2 if p.n_high >= 2 else (1 if p.n_high == 1 and p.M - p.n_high >= 1 else 0)
    pair_name = {2: "hoch+hoch", 1: "hoch+niedrig", 0: "niedrig+niedrig"}[h_pair]
    ax1.plot(mus, _pct([bm.pair_window_share(p, bm.ADAPTIV, h_pair, m, cal=cal) for m in mus]), color=b,
             linewidth=2.0, linestyle="--", label=f"adaptiv: Paar {PAIR_TEXT[pair_name]} (zwei Dauern)")
    ax1.plot(mus, np.full_like(mus, 100.0), color=g, linestyle="--", linewidth=1.8, label="rein 2-aus-N: sofort")
    ax1.plot(mus, _pct([bm.knn_share(tau, m) for m in mus]), color=g, linestyle="-", linewidth=1.6,
             label=f"rein 2-aus-N mit Verzögerung τ = {de(tau)} s: Stoß")
    ax1.plot(mus, _pct([bm.knn_share(tau, m, 2) for m in mus]), color=g, linestyle=":", linewidth=1.6,
             label=f"rein 2-aus-N mit Verzögerung τ = {de(tau)} s: Paar")
    mark_current(ax1, cal.mu_art, f"μ_art = {de(cal.mu_art)} s", pos="bottom")
    ax1.set_xlabel(AXIS["mu"])
    ax1.set_ylabel("eskalierte Episoden [%]")
    ax1.set_ylim(-2, 108)
    ax1.set_title("Artefakte")
    ax1.legend(loc="lower right")
    de_axes(ax1)

    ds = np.linspace(5.0, 120.0, 116)
    r, h = _crisis_class(p)
    cls = bm.find_class(bm.subset_classes(p, bm.ADAPTIV, cal), r, h)
    label_c = combo_text(r, h) if cls else f"r = {r}"
    y_ad = _pct([bm.crisis_share(p, bm.ADAPTIV, r, h, d, cal=cal) for d in ds])
    y_kn = _pct([bm.knn_share(tau, d) for d in ds])
    ax2.plot(ds, y_ad, color=b, linewidth=2.6)
    ax2.plot(ds, np.full_like(ds, 100.0), color=g, linestyle="--", linewidth=1.8)
    ax2.plot(ds, y_kn, color=g, linestyle="-", linewidth=1.6)
    mark_current(ax2, p.d_true, f"d_true = {de(p.d_true)} s", pos="bottom")
    ax2.set_xlabel(AXIS["d_true"])
    ax2.set_ylabel("eskalierte Krisen-Episoden [%]")
    ax2.set_ylim(-2, 108)
    ax2.set_title("Krisen (Preis)")
    de_axes(ax2)
    finish(fig, p, bottom=0.04)
    t_need_txt = de(bm.t_need(cls, p, bm.ADAPTIV), 2) if cls else "–"
    end_labels([ax2], [[(ds[-1], y_ad[-1], f"adaptiv: Krise mit\n{label_c}\n(t_need = {t_need_txt} s)", b),
                        (ds[-1], 100.0, "rein 2-aus-N: sofort", g),
                        (ds[-1], y_kn[-1], f"rein 2-aus-N mit\nVerzögerung τ = {de(tau)} s", g)]])
    return fig


def latency_curves(p: bm.Params, t0s: Sequence[float], rule: bm.Rule = bm.ADAPTIV) -> Dict[str, np.ndarray]:
    cal = bm.calibrate(p)
    out = {}
    for a, b in bm.PAIR_SEQUENCES:
        out[f"{a}→{b}"] = np.array([bm.escalation_latency(p, a, b, t0, rule, cal=cal) for t0 in t0s])
    return out


def _same_curve(a: np.ndarray, b: np.ndarray) -> bool:
    fa, fb = np.isfinite(a), np.isfinite(b)
    return bool(np.array_equal(fa, fb) and np.allclose(a[fa], b[fb], atol=1e-6))


def _kink_index(ys: np.ndarray) -> Optional[int]:
    """Erster Index, ab dem die Kurve ihren Endwert hält (Knick); None bei konstanter Kurve."""
    fin = np.isfinite(ys)
    if not fin.any():
        return None
    last = ys[fin][-1]
    idx = len(ys) - 1
    while idx > 0 and np.isfinite(ys[idx - 1]) and abs(ys[idx - 1] - last) < 1e-6:
        idx -= 1
    return idx if idx > 0 else None


def fig_D(p: bm.Params, t0_current: Optional[float] = None, rule: bm.Rule = bm.ADAPTIV) -> Figure:
    """Eskalationslatenz zweier anhaltender Alarme über dem Beginn t0 der zweiten Quelle."""
    t0s = np.arange(0.0, 60.0 + 1e-9, 0.5)
    raw = latency_curves(p, t0s, rule)
    curves: Dict[str, np.ndarray] = {"hoch→hoch": raw["hoch→hoch"]}
    if _same_curve(raw["hoch→niedrig"], raw["niedrig→hoch"]):
        curves["gemischt"] = raw["hoch→niedrig"]
    else:
        curves["hoch→niedrig"] = raw["hoch→niedrig"]
        curves["niedrig→hoch"] = raw["niedrig→hoch"]
    curves["niedrig→niedrig"] = raw["niedrig→niedrig"]
    fig, ax = plt.subplots(figsize=(W, 3.7))
    items = []
    kinks = []
    for name, ys in curves.items():
        if np.all(np.isnan(ys)):
            continue
        never = np.isinf(ys)
        yy = _finite(ys)
        ax.plot(t0s, yy, color=SEQ_COLORS[name], linestyle=SEQ_STYLES[name], linewidth=2.0, solid_joinstyle="miter")
        items.append((t0s[-1], yy[-1], SEQ_TEXT[name] + (" (teils keine Eskalation)" if never.any() else ""),
                      SEQ_COLORS[name]))
        ki = _kink_index(ys)
        if ki is not None and np.isfinite(ys[ki]):
            ax.plot([t0s[ki]], [ys[ki]], "o", color=SEQ_COLORS[name], markersize=5, zorder=5)
            kinks.append((t0s[ki], ys[ki]))
    if kinks:
        # Hinweis links unten zwischen Nulllinie und Plateau, Pfeile zu allen Knickpunkten
        tx, ty = 20.0, 2.5
        ax.text(tx, ty, "Schwelle hat ihre Untergrenze erreicht", ha="left", va="center", fontsize=ANN, color=INK,
                zorder=6)
        for kx, ky in kinks:
            sx = min(max(kx, tx + 1.0), tx + 28.0)
            sy = ty + 1.0 if ky > ty else ty - 1.0
            ax.annotate("", xy=(kx, ky), xytext=(sx, sy), textcoords="data",
                        arrowprops=dict(arrowstyle="-", color=GREY, linewidth=0.8, shrinkA=0, shrinkB=4))
    plot_rule(ax, t0s, np.zeros_like(t0s), "rein 2-aus-N")
    items.append((t0s[-1], 0.0, "rein 2-aus-N: sofort, ohne Zeitbezug", STYLE["rein 2-aus-N"]["color"]))
    if t0_current is not None:
        mark_current(ax, t0_current, f"t0 = {de(t0_current)} s")
    ax.set_xlabel(AXIS["t0"])
    ax.set_ylabel("Latenz nach Hinzukommen\nder zweiten Quelle [s]")
    ax.set_ylim(bottom=-1)
    de_axes(ax)
    finish(fig, p)
    end_labels([ax], [items])
    return fig


def fig_D_trace(p: bm.Params, prio_a: str = "hoch", prio_b: str = "niedrig", t0: float = 30.0,
                rule: bm.Rule = bm.ADAPTIV) -> Figure:
    """Zeitverlauf von Evidenz E(t) und angewendeter Schwelle Θ_current(t)."""
    tr = bm.simulate_pair(p, prio_a, prio_b, t0, rule)
    fig, ax = plt.subplots(figsize=(W, 3.5))
    if tr is None:
        ax.text(0.5, 0.5, "Paarung im Ensemble nicht vorhanden", ha="center", va="center", transform=ax.transAxes)
        return fig
    t_show = min(tr.t[-1], (tr.t_escalation if np.isfinite(tr.t_escalation) else tr.t[-1]) + 2 * p.T)
    m = tr.t <= t_show
    b, o = STYLE["adaptiv"]["color"], STYLE["Basis"]["color"]
    ax.plot(tr.t[m], tr.E[m], color=b, linewidth=2.4)
    items = [(t_show, tr.E[m][-1], "Evidenz E(t)", b), (t_show, tr.theta_current[m][-1], "angewendete Schwelle Θ_current(t)", o)]
    if not np.allclose(tr.theta_target, tr.theta_current, atol=1e-9):
        ax.plot(tr.t[m], tr.theta_target[m], color=GREY, linestyle=":")
        items.append((t_show, tr.theta_target[m][-1], "Zielschwelle Θ_target(t)", GREY))
    ax.plot(tr.t[m], tr.theta_current[m], color=o, linestyle="--", linewidth=2.0)
    ax.fill_between(tr.t[m], 0, tr.E[m], where=tr.escalated[m], color=o, alpha=0.12)
    ax.axvline(t0, color=GREY, linestyle=":", linewidth=1.2)
    ax.text(t0, 1.0, f" zweite Quelle ab t0 = {de(t0)} s", transform=ax.get_xaxis_transform(), ha="left", va="top",
            fontsize=7.5, color=GREY)
    if np.isfinite(tr.t_escalation):
        ax.axvline(tr.t_escalation, color=o, linestyle="-", linewidth=0.8, alpha=0.6)
        ax.text(tr.t_escalation, 0.02, f" Eskalation bei {de(tr.t_escalation, 2)} s\n (Latenz {de(tr.latency, 2)} s)",
                transform=ax.get_xaxis_transform(), ha="left", va="bottom", fontsize=7.5, color=o)
    else:
        ax.text(0.98, 0.05, "keine Eskalation", transform=ax.transAxes, ha="right", fontsize=ANN)
    ax.set_xlabel("Zeit t [s]")
    ax.set_ylabel("Log-Odds [nat]")
    ax.set_xlim(0, t_show)
    ax.set_ylim(bottom=0)
    de_axes(ax)
    finish(fig, p)
    end_labels([ax], [items])
    return fig


def fig_G2(p: bm.Params) -> Figure:
    """Anpassung an die Zahl der Geräte: Fehlalarme je Tag und verpasste Krisen je 1000 über |M|."""
    Ms = np.arange(2, 13)
    g = bm.geraetezahl(p, Ms)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(W, 3.4))
    markers = {"adaptiv": "o", "rein 2-aus-N": "s", "rein 3-aus-N": "^"}
    items1, items2 = [], []
    for name, (fa, miss) in g.items():
        plot_rule(ax1, Ms, _finite(fa), name, marker=markers[name], markersize=3.5)
        plot_rule(ax2, Ms, _finite(miss), name, marker=markers[name], markersize=3.5)
        items1.append((Ms[-1], fa[-1], name, STYLE[name]["color"]))
        items2.append((Ms[-1], miss[-1], name, STYLE[name]["color"]))
    for ax in (ax1, ax2):
        mark_current(ax, p.M, f"|M| = {p.M}")
        ax.set_xticks(Ms)
        ax.set_xlabel(AXIS["M"])
    ax1.set_ylabel("Fehlalarme je Bett und Tag")
    ax1.set_yscale("log")
    de_axes(ax1, y=False)
    log_ticks(ax1, "y")
    ax2.set_ylabel("verpasste Krisen je 1000 (symlog)")
    ax2.set_yscale("symlog", linthresh=1.0)
    ax2.set_ylim(-0.1, 1000)
    ax2.set_yticks([0, 1, 10, 100, 1000])
    de_axes(ax2)
    finish(fig, p)
    end_labels([ax1], [items1])
    end_labels([ax2], [items2])
    return fig


def fig_P(p: bm.Params, mu: Optional[float] = None) -> Figure:
    """Einfluss der Prioritäten: Eskalationszeit und eskalierter Anteil je Kombination der Quellen."""
    cal = bm.calibrate(p)
    mu = cal.mu_art if mu is None else mu
    rows = bm.priority_bars(p, mu, cal)
    labels = [r["label"] for r in rows]
    x = np.arange(len(rows))
    wdt = 0.38
    b, g = STYLE["adaptiv"]["color"], STYLE["rein 2-aus-N"]["color"]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(W, 5.8), sharex=True)
    t_ad = np.array([r["t_adaptiv"] for r in rows])
    t_kn = np.array([r["t_knn2"] for r in rows])
    ymax = max(np.nanmax(_finite(t_ad)) if np.isfinite(t_ad).any() else 0.0, 1.0)
    ax1.bar(x - wdt / 2, np.where(np.isfinite(t_ad), t_ad, 0.0), wdt, color=b, label="adaptiv")
    ax1.bar(x + wdt / 2, np.where(np.isfinite(t_kn), t_kn, 0.0), wdt, color=g, label=rule_text(bm.rein(2), p.M))
    for i, r in enumerate(rows):
        if np.isfinite(r["t_adaptiv"]):
            ax1.text(x[i] - wdt / 2, r["t_adaptiv"] + 0.01 * ymax, de(r["t_adaptiv"], 1), ha="center", va="bottom",
                     fontsize=7.5, color=INK)
        else:
            ax1.text(x[i] - wdt / 2, 0.01 * ymax, "nie", ha="center", va="bottom", fontsize=ANN, color=b)
        if np.isfinite(r["t_knn2"]):
            ax1.text(x[i] + wdt / 2, r["t_knn2"] + 0.01 * ymax, de(r["t_knn2"]), ha="center", va="bottom",
                     fontsize=7.5, color=INK)
        else:
            ax1.text(x[i] + wdt / 2, 0.01 * ymax, "nie", ha="center", va="bottom", fontsize=ANN, color=g)
    ax1.set_ylabel("Zeit bis zur Eskalation einer\nanhaltenden Episode [s]")
    ax1.set_ylim(0, ymax * 1.18)
    ax1.legend(loc="upper right")
    s_ad = _pct([r["share_adaptiv"] for r in rows])
    s_kn = _pct([r["share_knn2"] for r in rows])
    ax2.bar(x - wdt / 2, s_ad, wdt, color=b, label="adaptiv")
    ax2.bar(x + wdt / 2, s_kn, wdt, color=g, label=f"{rule_text(bm.rein(2), p.M)}: sofort")
    for i in range(len(rows)):
        ax2.text(x[i] - wdt / 2, s_ad[i] + 1, de(s_ad[i], 1), ha="center", va="bottom", fontsize=7.5, color=INK)
        ax2.text(x[i] + wdt / 2, s_kn[i] + 1, de(s_kn[i], 0), ha="center", va="bottom", fontsize=7.5, color=INK)
    ax2.set_ylabel(f"eskalierte Episoden bei\nmittlerer Artefaktdauer {de(mu)} s [%]")
    ax2.set_ylim(0, 115)
    ax2.legend(loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=2, borderaxespad=0.2)   # über der Tafel
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, rotation=30, ha="right", fontsize=ANN)
    ax2.set_xlabel("Kombination der alarmierenden Quellen (hoch / niedrig = Priorität)")
    for ax in (ax1, ax2):
        ax.grid(axis="x", visible=False)
        de_axes(ax, x=False)
    finish(fig, p, bottom=0.045)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# Weitere Kurven (A, E, F, k_min über α, freie Entfaltung)
# ─────────────────────────────────────────────────────────────────────────────

RULES_A = (bm.ADAPTIV, bm.rein(2), bm.rein(3), bm.BASIS)


def fig_A(p: bm.Params, n_points: int = 60) -> Figure:
    """ARR (oben) und System-TPR (unten) über |M|·FPR für alle Regeln; Achsen 75–90 % und 94–100 %."""
    xs = np.logspace(np.log10(0.005), np.log10(1.2), n_points)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(W, 5.4), sharex=True)
    arr_all, tpr_all, items1, items2 = [], [], [], []
    for rule in RULES_A:
        arr = _pct(bm.sweep(p, "MFPR", xs, rule, "ARR"))
        tpr = _pct(bm.sweep(p, "MFPR", xs, rule, "System-TPR"))
        arr_all.append(arr)
        tpr_all.append(tpr)
        plot_rule(ax1, xs, arr, rule.display)
        plot_rule(ax2, xs, tpr, rule.display)
        items1.append((xs[-1], arr[-1], rule_text(rule, p.M), STYLE[rule.display]["color"]))
        items2.append((xs[-1], tpr[-1], rule_text(rule, p.M), STYLE[rule.display]["color"]))
    cur = bm.calibrate(p).MFPR
    mark_current(ax1, cur, f"aktuell {de(cur, 4)}", pos="bottom")
    mark_current(ax2, cur, f"aktuell {de(cur, 4)}", pos="bottom")
    ax1.set_xscale("log")
    log_ticks(ax2, "x")
    lo = np.nanmin(np.concatenate(arr_all)) if np.isfinite(np.concatenate(arr_all)).any() else 75.0
    ax1.set_ylim(min(75.0, math.floor(lo)), 90.0)
    lo = np.nanmin(np.concatenate(tpr_all)) if np.isfinite(np.concatenate(tpr_all)).any() else 94.0
    ax2.set_ylim(min(94.0, math.floor(lo)), 100.2)
    ax1.set_ylabel("ARR [%]")
    ax2.set_ylabel("System-TPR [%]")
    ax2.set_xlabel(AXIS["MFPR"])
    de_axes(ax1)
    de_axes(ax2, x=False)
    finish(fig, p, bottom=0.045)
    end_labels([ax1, ax2], [items1, items2])
    return fig


def fig_E(p: bm.Params) -> Figure:
    """ARR über c_art: adaptiv, rein 2-aus-N und die Decke 1 − c_art/|M|."""
    cs = np.linspace(0.0, 1.0, 101)
    fig, ax = plt.subplots(figsize=(W, 3.5))
    deck = 100 * (1 - cs / p.M)
    ax.plot(cs, deck, color=GREY, linewidth=5, alpha=0.45)
    items = [(1.0, deck[-1], "Decke 1 − c_art/|M|", INK)]
    for rule in (bm.ADAPTIV, bm.rein(2)):
        ys = _pct(bm.sweep(p, "c_art", cs, rule, "ARR"))
        plot_rule(ax, cs, ys, rule.display)
        items.append((1.0, ys[-1], rule_text(rule, p.M), STYLE[rule.display]["color"]))
    mark_current(ax, p.c_art, f"c_art = {de(p.c_art)}")
    ax.set_xlabel(AXIS["c_art"])
    ax.set_ylabel("ARR [%]")
    ax.set_ylim(0, 102)
    de_axes(ax)
    finish(fig, p)
    end_labels([ax], [items])
    return fig


def fig_F(p: bm.Params) -> Figure:
    """PPV (gezählt / zeitgewichtet) und System-TPR über d_true und TPR_j für adaptiv und rein 2-aus-N."""
    d_trues = np.linspace(5.0, 120.0, 50)
    tprs = np.linspace(0.50, 0.99, 50)
    fig, axes = plt.subplots(2, 2, figsize=(W, 5.6), sharex="col")
    rules = (bm.ADAPTIV, bm.rein(2))
    for col, (name, xs, cur, xlabel) in enumerate((
            ("d_true", d_trues, p.d_true, AXIS["d_true"]),
            ("tpr", tprs, p.tpr, AXIS["tpr"]))):
        top, bot = [], []
        for ri, rule in enumerate(rules):
            s = STYLE[rule.display]
            lw = 2.6 if ri == 0 else 1.4
            y1 = _pct(bm.sweep(p, name, xs, rule, "PPV gezählt"))
            y2 = _pct(bm.sweep(p, name, xs, rule, "PPV zeitgewichtet"))
            y3 = _pct(bm.sweep(p, name, xs, rule, "System-TPR"))
            axes[0, col].plot(xs, y1, color=s["color"], linewidth=lw)
            axes[0, col].plot(xs, y2, color=s["color"], linestyle="--", linewidth=lw)
            axes[1, col].plot(xs, y3, color=s["color"], linewidth=lw)
            top.append((xs[-1], y1[-1], f"{rule.display},\ngezählt", s["color"]))
            top.append((xs[-1], y2[-1], f"{rule.display},\nzeitgewichtet", s["color"]))
            bot.append((xs[-1], y3[-1], rule.display, s["color"]))
        mark_current(axes[0, col], cur, f"aktuell {de(cur)}")
        mark_current(axes[1, col], cur, f"aktuell {de(cur)}", pos="bottom")
        de_axes(axes[0, col])
        de_axes(axes[1, col])
        axes[1, col].set_xlabel(xlabel)
        axes[1, col].set_ylim(0, 102)
        setattr(axes[0, col], "_end_items", top)
        setattr(axes[1, col], "_end_items", bot)
    axes[0, 0].set_ylabel("PPV [%]")
    axes[1, 0].set_ylabel("System-TPR [%]")
    finish(fig, p, bottom=0.045)
    for col in (0, 1):
        end_labels([axes[0, col], axes[1, col]], [axes[0, col]._end_items, axes[1, col]._end_items], fontsize=7.5)
    return fig


def fig_kmin_alpha(p: bm.Params, alpha_current: Optional[float] = None) -> Figure:
    """Quorum k_min je Kombination der Quellen als Funktion von α (3.16)/(3.17)."""
    alphas = np.linspace(0.0, 1.0, 201)
    curves = bm.kmin_over_alpha(p, alphas)
    fig, ax = plt.subplots(figsize=(W, 3.5))
    n = len(curves)
    for i, (label, ks) in enumerate(curves.items()):
        off = (i - (n - 1) / 2) * 0.05
        ax.step(alphas, ks + off, where="post", color=CATEGORICAL[i % len(CATEGORICAL)], linewidth=1.8, label=label)
    a_cur = p.alpha if alpha_current is None else alpha_current
    mark_current(ax, a_cur, f"α = {de(a_cur)}")
    ax.set_yticks(range(2, p.M + 1))
    ax.set_ylim(1.6, p.M + 0.5)
    ax.set_xlabel(AXIS["alpha"] + " gegenüber der Summe der Prioritäten")
    ax.set_ylabel("Quorum k_min")
    ax.legend(loc="upper right", fontsize=7.5, ncol=2, title="alarmierende Quellen", title_fontsize=7.5)
    de_axes(ax)
    finish(fig, p)
    return fig


def x_grid(name: str, n_points: int = 60) -> np.ndarray:
    info = bm.PARAM_INFO[name]
    if name == "M":
        return np.arange(2, 13, dtype=float)
    if name == "MFPR":
        return np.logspace(np.log10(info["lo"]), np.log10(info["hi"]), n_points)
    return np.linspace(info["lo"], info["hi"], n_points)


def fig_sweep(p: bm.Params, x_name: str, metric: str, rules: Sequence[bm.Rule],
              family: Optional[str] = None, band: bool = False) -> Figure:
    """Freie Entfaltung: Kennzahl über einem Parameter, optional mit Parameterfamilie oder Band."""
    xs = x_grid(x_name)
    info = bm.PARAM_INFO[x_name]
    pct_metric = metric != "Fehlalarme/Tag"
    scale = 100.0 if pct_metric else 1.0
    fig, ax = plt.subplots(figsize=(W, 3.7))
    items = []
    for ri, rule in enumerate(rules):
        s = STYLE[rule.display]
        ls = LINESTYLES[ri % len(LINESTYLES)] if family else s["ls"]
        if family and family != x_name:
            fam_vals = bm.FAMILY_VALUES[family]
            ys = np.array([scale * _finite(bm.sweep(bm.set_param(p, family, fv), x_name, xs, rule, metric))
                           for fv in fam_vals])
            if band:
                ax.fill_between(xs, np.nanmin(ys, axis=0), np.nanmax(ys, axis=0), color=s["color"], alpha=0.15,
                                linewidth=0, label=f"{rule_text(rule, p.M)}: Band {bm.PARAM_INFO[family]['label']}")
                y_cur = scale * _finite(bm.sweep(p, x_name, xs, rule, metric))
                ax.plot(xs, y_cur, color=s["color"], linestyle=ls, linewidth=s["lw"],
                        label=f"{rule_text(rule, p.M)}: aktuell")
                items.append((xs[-1], y_cur[-1], f"{rule_text(rule, p.M)}: aktuell", s["color"]))
            else:
                for fi, (fv, y) in enumerate(zip(fam_vals, ys)):
                    lab = f"{rule.display}: {bm.PARAM_INFO[family]['label']} = {de(fv)}"
                    ax.plot(xs, y, color=FAMILY_RAMP[fi % len(FAMILY_RAMP)], linestyle=ls, label=lab)
                    items.append((xs[-1], y[-1], lab, FAMILY_RAMP[fi % len(FAMILY_RAMP)]))
        else:
            y = scale * _finite(bm.sweep(p, x_name, xs, rule, metric))
            plot_rule(ax, xs, y, rule.display, label=rule_text(rule, p.M))
            items.append((xs[-1], y[-1], rule_text(rule, p.M), s["color"]))
    mark_current(ax, bm.current_value(p, x_name), f"aktuell {de(bm.current_value(p, x_name), 4)}")
    unit = f" [{info['unit']}]" if info["unit"] else ""
    ax.set_xlabel(AXIS.get(x_name, info["label"] + unit))
    ax.set_ylabel(metric + (" [%]" if pct_metric else ""))
    de_axes(ax)
    if x_name == "MFPR":
        ax.set_xscale("log")
        log_ticks(ax, "x")
    if x_name == "M":
        ax.set_xticks(xs)
    if metric == "Fehlalarme/Tag":
        ax.set_yscale("log")
        log_ticks(ax, "y")
    use_legend = len(items) > 4 or (family and band)
    if use_legend:
        ax.legend(fontsize=7.5)
    finish(fig, p)
    if not use_legend:
        end_labels([ax], [items])
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# Registrierung und Export
# ─────────────────────────────────────────────────────────────────────────────

# Bausteine für die App (Name → Funktion(p, *extra))
BUILDERS: Dict[str, Callable[..., Figure]] = {
    "1a": fig_1a, "1b": fig_1b, "1c": fig_1c, "1d": fig_1d,
    "B": fig_B, "C": fig_C, "C2": fig_C2, "D": fig_D, "Dtrace": fig_D_trace, "G2": fig_G2, "P": fig_P,
    "A": fig_A, "E": fig_E, "F": fig_F, "kmin": fig_kmin_alpha, "sweep": fig_sweep,
}

# Exportsatz (Dateiname → Funktion(p)); Standardwerte: t0 = 30 s, τ = 5 s, μ_art = d_false
FIGURES: Dict[str, Callable[[bm.Params], Figure]] = {
    "5-3-2_1a_Last_Kanaele": fig_1a,
    "5-3-2_1b_Last_Rauschen": fig_1b,
    "5-3-2_1c_Last_c_art": fig_1c,
    "5-3-2_1d_Tornado": fig_1d,
    "5-3-3_B_Betriebskennlinie": fig_B,
    "5-3-3_C_Doppelartefakte": fig_C,
    "5-3-3_C2_Fenster": lambda p: fig_C2(p, 5.0),
    "5-3-3_D_Latenz": lambda p: fig_D(p, 30.0),
    "5-3-3_D_Zeitverlauf": lambda p: fig_D_trace(p, "hoch", "niedrig", 30.0),
    "5-3-3_G2_Geraetezahl": fig_G2,
    "5-3-3_P_Prioritaeten": fig_P,
    "G_A_alle_Regeln": fig_A,
    "G_E_c_art": fig_E,
    "G_F_PPV_TPR": fig_F,
}


def _save_with_retry(fig: Figure, path: Path, dpi: Optional[int], attempts: int = 5) -> None:
    """savefig mit Wiederholung: Windows (Sync-/Virenscanner) sperrt frisch geschriebene Dateien kurz."""
    import time
    for i in range(attempts):
        try:
            fig.savefig(path, dpi=dpi)
            return
        except OSError:
            if i == attempts - 1:
                raise
            time.sleep(0.4 * (i + 1))


def clean_figures(outdir: Path) -> List[Path]:
    """Alle PNG/SVG-Dateien im Zielordner löschen (nur für den Abbildungsordner gedacht)."""
    removed = []
    for path in sorted(Path(outdir).glob("*")):
        if path.is_file() and path.suffix.lower() in (".png", ".svg"):
            path.unlink()
            removed.append(path)
    return removed


def export_all(p: bm.Params, outdir: Path = FIG_DIR, formats: Sequence[str] = ("png", "svg"),
               dpi: int = 300, names: Optional[Sequence[str]] = None,
               clean: Optional[bool] = None) -> Tuple[List[Path], List[Path]]:
    """Alten Satz löschen (standardmäßig nur im Ordner ``figures/``) und neuen Satz schreiben.

    Liefert (gelöschte Pfade, geschriebene Pfade).
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    if clean is None:
        clean = outdir.resolve() == FIG_DIR.resolve()
    removed = clean_figures(outdir) if clean else []
    written: List[Path] = []
    for name, builder in FIGURES.items():
        if names and name not in names:
            continue
        fig = builder(p)
        for ext in formats:
            path = outdir / f"{name}.{ext}"
            _save_with_retry(fig, path, dpi=dpi if ext == "png" else None)
            written.append(path)
        plt.close(fig)
    return removed, written


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Abbildungen des Belastungsmodells exportieren (PNG 300 dpi + SVG).")
    ap.add_argument("--outdir", type=Path, default=FIG_DIR, help=f"Zielordner (Standard: {FIG_DIR})")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--szenario", choices=sorted(bm.SCENARIOS), default="Median",
                    help="Stationsszenario aus Tabelle 5.7 (Standard: Median)")
    ap.add_argument("--clean", action="store_true",
                    help="PNG/SVG im Zielordner vorher löschen (im Standardordner figures/ immer)")
    args = ap.parse_args(argv)
    p = bm.Params(**bm.SCENARIOS[args.szenario])
    removed, written = export_all(p, args.outdir, dpi=args.dpi, clean=True if args.clean else None)
    for path in removed:
        print("gelöscht:", path.name)
    for path in written:
        print("geschrieben:", path.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
