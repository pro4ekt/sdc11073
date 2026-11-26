"""Abbildungen der Seite „Ziel 3 – vier Kurven“ und ihr Export: K1–K4 (Modell), D1–D4 (Experimente),
V1–V4 (Vergleich Modell – Simulation).

Jede ``fig_*``-Funktion liefert eine ``Figure`` ohne Titel und ohne Parameterzeile (die Frage steht als
Überschrift in der App bzw. als Bildunterschrift im Text; die graue Parameterzeile nur mit
``mit_parametern=True`` bzw. ``--mit-parametern``).  ``app_ziel3.py`` zeigt die Abbildungen an;
``export_all`` (K), ``export_experimente`` (D) und ``export_vergleich`` (V) schreiben sie als PNG (300 dpi)
und SVG nach ``figures/ziel3/`` (die Abbildungen in ``figures/`` bleiben unberührt).  Aufruf als Skript,
Standardparameter = Median-Intensivstation, Simulationsdaten aus ``docs/reports/``:

    .venv\\Scripts\\python analysis/belastungsmodell_app/export_ziel3.py [--nur modell|experimente|vergleich]
        [--outdir DIR] [--szenario NAME] [--mit-parametern] [--erkannt streng|mild] [--v4-lauf 12h|6h]

Stil (alle Reiter): adaptiv #2a78d6 durchgezogen dick, 2-aus-N #eb6834 gestrichelt, 3-aus-N #7a5fb3 gepunktet,
ohne Filter #8a969e durchgezogen; Zahlen über Säulen: < 0,05 → „0“, < 10 → eine Nachkommastelle, sonst ganz;
Rauschen stets mit Mittelwertstrich $|M|\\cdot\\overline{FPR}$; die x-Achse von K3/D3/V3 ist die
Anzeigewahrscheinlichkeit je Gerät $\\eta_j$ (im Modell η_j = TPR_j), die Läufe heißen „6 h (η_j = 0,6)“ usw.;
senkrechte gepunktete Linie „eingestellt“ = aktueller Wert der x-Achse.  Auf den V-Abbildungen fehlt der 1-h-Lauf
(nur in den Tabellen), das Modell steht als eine Linie je Regel mit den Parametern des 12-h-Laufs und
c_art = Anteil „alle 4 Kanäle“; wo Modell und Simulation nebeneinanderstehen (V4), ist das Modell ein schwarzer
Rhombus neben der Säule der Simulation.
"""

from __future__ import annotations

import argparse
import math
import textwrap
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

try:  # als Paketmodul (Tests, python -m) …
    from . import export_figures as fx
    from . import model_ziel3 as mz
    from . import sim_data as sd
except ImportError:  # … oder als Skript / unter streamlit run
    import export_figures as fx
    import model_ziel3 as mz
    import sim_data as sd

FIG_DIR = fx.FIG_DIR / "ziel3"

W = fx.W                 # Breite 15 cm
H = 0.60 * W             # Höhe der Kurven
GREY = fx.GREY
INK = fx.INK
de = fx.de

STYLE: Dict[str, dict] = {
    "adaptiv": dict(color="#2a78d6", ls="-", lw=2.6),
    "r2":      dict(color="#eb6834", ls="--", lw=1.8),
    "r3":      dict(color="#7a5fb3", ls=":", lw=2.0),
    "ohne":    dict(color="#8a969e", ls="-", lw=1.8),
}
INF_STYLE = dict(color="#2a78d6", ls=(0, (4, 3)), lw=1.0)     # adaptiv, Krise hält an (Kapitel 5)
INF_LABEL = "adaptiv, Krise hält an (Kapitel 5)"
MFPR_TEX = r"$|M|\cdot\overline{FPR}$"
ETA_TEX = sd.ETA_TEX
AXIS_LABELS: Dict[str, str] = {name: a.label for name, a in mz.X_AXES.items()}
AXIS_LABELS["MFPR"] = f"Fehlalarmbelegung je Bett {MFPR_TEX}, logarithmisch"
AXIS_LABELS["tpr"] = f"Anzeigewahrscheinlichkeit je Gerät {ETA_TEX}"      # im Modell η_j = TPR_j
AXIS_LABELS["n"] = "Alarme je Alarmkanal und Tag (n)"
AXIS_LABELS["M"] = "Alarmkanäle am Bett |M| (Prioritäten anteilig skaliert)"
AXIS_SHORT: Dict[str, str] = {name: a.short for name, a in mz.X_AXES.items()}
AXIS_SHORT["MFPR"] = "|M|·FPR̄"
AXIS_SHORT["tpr"] = "η_j (= TPR_j)"
M_LABEL = "Alarmkanäle am Bett |M|"

K_FILES: Dict[str, str] = {
    "K1": "K1_Entlastung", "K2": "K2_Vergleich_kausN", "K3": "K3_erkannte_Krisen", "K4": "K4_Fehlerbilanz",
}
D_FILES: Dict[str, str] = {
    "D1": "D1_Entlastung_Sim", "D2": "D2_Vergleich_Sim", "D3": "D3_Krisen_Sim", "D4": "D4_Bilanz_Sim",
}
V_FILES: Dict[str, str] = {
    "V1": "V1_Entlastung_Vergleich", "V2": "V2_kausN_Vergleich", "V3": "V3_Krisen_Vergleich",
    "V4": "V4_Fehlerbilanz_Vergleich",
}
FILE_NAMES = K_FILES          # Rückwärtskompatibel (Reiter Modell)


# ─────────────────────────────────────────────────────────────────────────────
# Helfer
# ─────────────────────────────────────────────────────────────────────────────

def axis_label(name: str) -> str:
    return AXIS_LABELS[name]


def bar_label(v: float) -> str:
    """Zahl über einer Säule: < 0,05 → „0“, < 10 → eine Nachkommastelle, sonst ganze Zahl."""
    v = float(v)
    if abs(v) < 0.05:
        return "0"
    if abs(v) < 10:
        return de(v, 1)
    return de(float(round(v)), 0)


def thousands(n: int) -> str:
    """Ganze Zahl mit schmalem Leerzeichen als Tausendertrenner."""
    return f"{int(n):,}".replace(",", " ")


def plot_rule(ax, x, y, rule: str, **kw):
    s = STYLE[rule]
    kw.setdefault("label", mz.RULE_NAMES[rule])
    return ax.plot(x, y, color=s["color"], linestyle=s["ls"], linewidth=s["lw"], **kw)


def footer_text(p: mz.Params) -> str:
    """Parameterzeile unter einer Modellabbildung (nur mit ``mit_parametern``)."""
    name = mz.scenario_name(p)
    nh, nm, nl = p.counts()
    parts = [f"|M| = {p.M} ({nh} hoch, {nm} mittel, {nl} niedrig)", f"n = {de(p.n)}", f"FDR = {de(p.fdr)}",
             f"d_false = {de(p.d_false)} s", f"d_true = {de(p.d_true)} s", f"d0 = {de(p.d0)} s",
             f"c_art = {de(p.c_art)}", f"TPR_j = {de(p.tpr)}", f"T = {de(p.T)} s", f"δ_min = {de(p.delta_min)}",
             f"α = {de(p.alpha)}"]
    if p.mfpr_direct is not None:
        parts.append(f"{MFPR_TEX} = {de(p.mfpr_direct)} (direkt)")
    if p.pc_direct is not None:
        parts.append(f"P(C) = {de(p.pc_direct, 4)} (direkt)")
    return f"{name or 'Parameter'}: " + ", ".join(parts)


def _footer(fig: Figure, text: str, top: float) -> None:
    fig.tight_layout(rect=(0, 0.10, 1, top))
    fig.text(0.01, 0.008, "\n".join(textwrap.wrap(text, 125)), fontsize=7.5, color=GREY, ha="left", va="bottom")


def finish(fig: Figure, p: mz.Params, mit_parametern: bool = False, top: float = 1.0) -> None:
    """Layout abschließen; die graue Parameterzeile nur auf Wunsch."""
    if mit_parametern:
        _footer(fig, footer_text(p), top)
    else:
        fig.tight_layout(rect=(0, 0, 1, top))


def mark_current(ax, x: float, label: str = "eingestellt", y_frac: float = 1.0, va: str = "top") -> None:
    """Senkrechte gepunktete Linie am eingestellten Wert; Beschriftung in Achsenhöhe y_frac (0 … 1)."""
    if x is None or not np.isfinite(x):
        return
    ax.axvline(x, color=GREY, linestyle=":", linewidth=1.2, zorder=1)
    ax.text(x, y_frac, f" {label}", transform=ax.get_xaxis_transform(), ha="left", va=va, fontsize=7.5, color=GREY)


def legend(ax, loc: str = "best", **kw):
    """Legende mit weißem, leicht durchsichtigem Hintergrund, damit sie über Linien und Markierung lesbar bleibt."""
    return ax.legend(loc=loc, frameon=True, facecolor="white", edgecolor="none", framealpha=0.85, **kw)


def _x_axis(ax, name: str, xs: np.ndarray) -> None:
    a = mz.X_AXES[name]
    ax.set_xlabel(axis_label(name))
    fx.de_axes(ax)
    if a.log:
        ax.set_xscale("log")
        fx.log_ticks(ax, "x")
    elif a.integer:
        ax.set_xticks(xs)


def _marker(name: str) -> dict:
    return dict(marker="o", ms=3.5) if mz.X_AXES[name].integer else {}


def _label_above(ax, x: float, y: float, text: str, rotation: int = 0, color: str = INK, fontsize: float = 7.0,
                 pad: float = 0.0) -> None:
    ax.text(x, y + pad, text, ha="center", va="bottom", fontsize=fontsize, rotation=rotation, color=color)


def _bars_grid(ax) -> None:
    ax.set_axisbelow(True)
    ax.xaxis.grid(False)


# ─────────────────────────────────────────────────────────────────────────────
# K1  Wie stark entlastet der Filter das Personal?
# ─────────────────────────────────────────────────────────────────────────────

def fig_K1(p: mz.Params, x_name: str = "c_art", unit: str = mz.DEFAULT_UNIT, n_points: int = 60,
           mit_parametern: bool = False) -> Figure:
    """Fehlalarme ohne Filter und adaptiv über c_art | |M|·FPR̄ | n | |M|."""
    f = mz.UNITS[unit]
    xs = mz.x_grid(x_name, n_points)
    fig, ax = plt.subplots(figsize=(W, H))
    for rule in ("ohne", "adaptiv"):
        ys = np.array([r.false_day for r in mz.sweep(p, rule, x_name, xs)]) * f
        plot_rule(ax, xs, ys, rule, **_marker(x_name))
    _x_axis(ax, x_name, xs)
    ax.set_ylabel(f"Fehlalarme {unit}")
    ax.set_ylim(bottom=0)
    mark_current(ax, mz.current_value(p, x_name))
    legend(ax)
    finish(fig, p, mit_parametern)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# K2  Wie viele Fehlalarme bleiben im Vergleich zu 2-aus-N und 3-aus-N?
# ─────────────────────────────────────────────────────────────────────────────

def fig_K2(p: mz.Params, x_name: str = "MFPR", y_mode: str = "prozent", unit: str = mz.DEFAULT_UNIT,
           n_points: int = 60, mit_parametern: bool = False) -> Figure:
    """2-aus-N, 3-aus-N, adaptiv: verbleibende Fehlalarme in %, absolut oder als Differenz zu adaptiv."""
    f = mz.UNITS[unit]
    xs = mz.x_grid(x_name, n_points)
    rules = ("r2", "r3", "adaptiv")
    res = {rule: mz.sweep(p, rule, x_name, xs) for rule in rules}
    adaptiv = np.array([r.false_day for r in res["adaptiv"]])
    fig, ax = plt.subplots(figsize=(W, H))
    for rule in rules:
        if y_mode == "prozent":
            ys = 100.0 * np.array([r.remaining for r in res[rule]])
        elif y_mode == "absolut":
            ys = np.array([r.false_day for r in res[rule]]) * f
        else:
            ys = (np.array([r.false_day for r in res[rule]]) - adaptiv) * f
        plot_rule(ax, xs, ys, rule, **_marker(x_name))
    _x_axis(ax, x_name, xs)
    if y_mode == "prozent":
        ax.set_ylabel("verbleibende Fehlalarme [% von ohne Filter, Anzahl]")
    elif y_mode == "absolut":
        ax.set_ylabel(f"Fehlalarme {unit}")
        ax.set_ylim(bottom=0)
    else:
        ax.set_ylabel(f"Differenz zu adaptiv ({unit})")
        ax.axhline(0, color=GREY, linewidth=0.8, zorder=1)
    mark_current(ax, mz.current_value(p, x_name))
    legend(ax)
    finish(fig, p, mit_parametern)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# K3  Welcher Anteil der Krisen wird erkannt?
# ─────────────────────────────────────────────────────────────────────────────

def fig_K3(p: mz.Params, x_name: str = "tpr", y_mode: str = "erkannt", n_points: int = 60,
           mit_parametern: bool = False) -> Figure:
    """Erkannte Krisen (Kaltstart, tpr_T) aller Regeln; dünn gestrichelt adaptiv bei anhaltender Krise."""
    xs = mz.x_grid(x_name, n_points)
    conv = (lambda t: 100.0 * t) if y_mode == "erkannt" else (lambda t: 1000.0 * (1.0 - t))
    fig, ax = plt.subplots(figsize=(W, H))
    for rule in ("ohne", "r2", "r3", "adaptiv"):
        rs = mz.sweep(p, rule, x_name, xs)
        plot_rule(ax, xs, conv(np.array([r.tpr_T for r in rs])), rule)
        if rule == "adaptiv":
            ax.plot(xs, conv(np.array([r.tpr_inf for r in rs])), label=INF_LABEL, **INF_STYLE)
    _x_axis(ax, x_name, xs)
    if y_mode == "erkannt":
        ax.set_ylabel("erkannte Krisen [%]")
        legend(ax, "lower right")
        y_label = 0.62          # Beschriftung oberhalb der Legende
    else:
        ax.set_ylabel("verpasste Krisen je 1000")
        ax.set_ylim(bottom=0)
        legend(ax, "upper right")
        y_label = 0.38          # Beschriftung unterhalb der Legende
    mark_current(ax, mz.current_value(p, x_name), y_frac=y_label, va="center")
    finish(fig, p, mit_parametern)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# K4  Was geht bei jeder Regel schief?
# ─────────────────────────────────────────────────────────────────────────────

K4_MS_FIG: Tuple[int, ...] = (4, 5, 6, 8, 10, 12)


def k4_left_top(tab: Dict[str, Tuple[np.ndarray, np.ndarray]]) -> float:
    """Obergrenze der linken Achse: ceil(1,25 · max(2-aus-N bei |M| = 4, alle adaptiv, alle 3-aus-N))."""
    vals = [float(tab["r2"][0][0]), float(np.max(tab["adaptiv"][0])), float(np.max(tab["r3"][0]))]
    return float(math.ceil(1.25 * max(vals)))


def fig_K4(p: mz.Params, F_name: str = mz.DEFAULT_K4, mit_parametern: bool = False) -> Figure:
    """Links unnötige Eskalationen (abgeschnitten, ↑), rechts verpasste Krisen; Säulen 2-aus-N / adaptiv / 3-aus-N
    über |M| = 4, 5, 6, 8, 10, 12 (P anteilig skaliert), Zahlen über jeder Säule."""
    F = mz.K4_FACTORS[F_name]
    tab = mz.k4_table(p, F, Ms=K4_MS_FIG)
    x = np.arange(len(K4_MS_FIG), dtype=float)
    width = 0.26
    offset = {"r2": -width, "adaptiv": 0.0, "r3": width}
    top_left = k4_left_top(tab)
    top_right = 1.15 * max(float(np.max(tab[rule][1])) for rule in mz.K4_RULES)
    fig, (axl, axr) = plt.subplots(1, 2, figsize=(W, 0.58 * W))
    for rule in mz.K4_RULES:
        u, v = tab[rule]
        color = STYLE[rule]["color"]
        for i in range(len(K4_MS_FIG)):
            xi = x[i] + offset[rule]
            if u[i] > top_left:                      # abgeschnitten: Zahl mit Pfeil innen am oberen Rand
                axl.bar(xi, top_left, width, color=color, label=mz.RULE_NAMES[rule] if i == 0 else None)
                axl.text(xi, 0.985 * top_left, f"{bar_label(u[i])} →", ha="center", va="top", fontsize=6.5,
                         rotation=90, color=INK)
            else:
                axl.bar(xi, u[i], width, color=color, label=mz.RULE_NAMES[rule] if i == 0 else None)
                _label_above(axl, xi, u[i], bar_label(u[i]), rotation=90, fontsize=6.5, pad=0.012 * top_left)
            axr.bar(xi, v[i], width, color=color)
            _label_above(axr, xi, v[i], bar_label(v[i]), rotation=90, fontsize=6.5, pad=0.012 * top_right)
    axl.set_ylim(0, top_left)
    axr.set_ylim(0, top_right)
    axl.set_ylabel(f"unnötige Eskalationen\n{F_name}")
    axr.set_ylabel(f"verpasste Krisen\n{F_name}")
    for ax in (axl, axr):
        ax.set_xlabel(M_LABEL)
        ax.set_xticks(x)
        ax.set_xticklabels([str(m) for m in K4_MS_FIG])
        _bars_grid(ax)
        fx.de_axes(ax, x=False)
    handles, labels = axl.get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=3, frameon=False)
    finish(fig, p, mit_parametern, top=0.93)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# Gemeinsames für D und V: Marker der Läufe, Fußzeile
# ─────────────────────────────────────────────────────────────────────────────

LIGHT_ALPHA = 0.45
V_HIDDEN = ("1h",)            # Läufe, die auf den V-Abbildungen fehlen (nur in den Tabellen)
DIAMOND = dict(marker="D", color="black", ms=6.0, ls="none", zorder=8)


def _vruns(runs: Sequence[sd.RunData]) -> List[sd.RunData]:
    """Läufe der V-Abbildungen: ohne 1 h."""
    return [r for r in runs if r.key not in V_HIDDEN]


def _main_run(runs: Sequence[sd.RunData]) -> Optional[sd.RunData]:
    """Lauf, dessen Messwerte das Modell parametrieren: 12 h (größte Stichprobe), sonst der erste."""
    keys = [r.key for r in runs]
    if sd.MAIN_RUN in keys:
        return next(r for r in runs if r.key == sd.MAIN_RUN)
    return runs[0] if runs else None


def param_label(main: Optional[sd.RunData]) -> str:
    """„Parameter des 12-h-Laufs“."""
    return f"Parameter des {main.label.replace(' ', '-')}-Laufs" if main is not None else "Parameter"


def _point_kw(spec: sd.RunSpec, color: str) -> dict:
    return dict(marker=spec.marker, mfc=color if spec.filled else "white", mec=color, ecolor=color, color=color,
                ms=6.5, mew=1.3, ls="none", capsize=2.5, elinewidth=1.0, zorder=6)


def _point(ax, x: float, y: float, ci: Tuple[float, float], spec: sd.RunSpec, color: str,
           xerr: Optional[Tuple[float, float]] = None, label: Optional[str] = None) -> None:
    yerr = [[max(0.0, y - ci[0])], [max(0.0, ci[1] - y)]]
    xe = None if xerr is None else [[xerr[0]], [xerr[1]]]
    ax.errorbar([x], [y], yerr=yerr, xerr=xe, label=label, **_point_kw(spec, color))


def _run_handles(runs: Sequence[sd.RunData], prefix: str = "Simulation ") -> List[Line2D]:
    return [Line2D([], [], marker=r.spec.marker, mfc=INK if r.spec.filled else "white", mec=INK, color=INK,
                   ls="none", ms=6.5, mew=1.3, label=f"{prefix}{r.spec.label_tex}") for r in runs]


def _run_legend(ax, runs: Sequence[sd.RunData], loc: str = "upper left") -> None:
    """Zweite Legende: Marker der Läufe (Simulation)."""
    handles = _run_handles(runs)
    if handles:
        first = ax.get_legend()
        if first is not None:
            ax.add_artist(first)
        ax.legend(handles=handles, loc=loc, frameon=True, facecolor="white", edgecolor="none", framealpha=0.85,
                  fontsize=7.5)


def _model_lines(ax, main: Optional[sd.RunData], rules: Sequence[str], x_name: str, xs: np.ndarray,
                 attr: str = "false_day", scale: float = 1.0, solid: bool = False) -> List[Line2D]:
    """Eine Modelllinie je Regel (Parameter des Hauptlaufs, c_art = alle 4); liefert die Legenden-Handles."""
    handles: List[Line2D] = []
    if main is None:
        return handles
    for rule in rules:
        ys = scale * sd.curve(main, rule, x_name, xs, attr)
        s = STYLE[rule]
        (line,) = ax.plot(xs, ys, color=s["color"], linestyle="-" if solid else s["ls"], linewidth=s["lw"],
                          label=f"{mz.RULE_NAMES[rule]} – Modell ({param_label(main)})")
        handles.append(line)
    return handles


def _errorbar_count(ax, x: float, y: float, ci: Tuple[float, float]) -> None:
    ax.errorbar([x], [y], yerr=[[max(0.0, y - ci[0])], [max(0.0, ci[1] - y)]], color=INK, capsize=3,
                elinewidth=1.0, ls="none", zorder=5)


def sim_footer_text(run: sd.RunData) -> str:
    m = run.m
    return (f"Lauf {run.label}: |M| = 4 (3, 3, 2, 1), {m.patients} Betten, {MFPR_TEX} = {de(m.mfpr, 4)}, "
            f"P(C) = {de(m.P_C, 4)}, d_false = {de(m.d_false, 1)} s, d_true = {de(m.d_true, 1)} s, d0 = 15 s, "
            f"TPR_j = {ETA_TEX} = {de(run.spec.tpr)}, T = 10 s, δ_min = 0,5, α = 0,5, c_art = {de(m.c_art_a, 3)} "
            f"(alle 4 Kanäle)")


def _finish_sim(fig: Figure, run: Optional[sd.RunData], mit_parametern: bool, top: float = 1.0) -> None:
    if mit_parametern and run is not None:
        _footer(fig, sim_footer_text(run), top)
    else:
        fig.tight_layout(rect=(0, 0, 1, top))


def run_title(run: sd.RunData) -> str:
    return f"{run.label} · {run.m.patients} Betten · {ETA_TEX} = {de(run.spec.tpr)}"


def run_subtitle(run: sd.RunData) -> str:
    return (f"Gerätealarme: {thousands(run.n_alarms)} (davon falsch {thousands(run.n_false_alarms)}) · "
            f"Krisen: {run.m.n_crises}")


def run_title_lines(run: sd.RunData, compact: bool = False) -> str:
    """Spaltenüberschrift in D4: Lauf, Gerätealarme = außerhalb von Krisen + in Krisen, Krisen.

    ``compact`` (mehrere Spalten nebeneinander) bricht die Alarmzeile um, damit sie in die Spalte passt.
    """
    in_crises = run.n_alarms - run.n_false_alarms
    if compact:
        # mehrere Spalten nebeneinander: kurze Zeilen, damit die Überschriften nicht ineinanderlaufen
        return (f"{run.label} · {ETA_TEX} = {de(run.spec.tpr)}\nAlarme {thousands(run.n_alarms)} = "
                f"{thousands(run.n_false_alarms)}\naußerhalb + {thousands(in_crises)} in Krisen\n{run.m.n_crises} Krisen")
    alarms = (f"Gerätealarme {thousands(run.n_alarms)} = {thousands(run.n_false_alarms)} "
              f"außerhalb von Krisen + {thousands(in_crises)} in Krisen")
    return f"{run_title(run)}\n{alarms}\n{run.m.n_crises} Krisen (je Krise mehrere Geräte)"


# ─────────────────────────────────────────────────────────────────────────────
# D1  Wie stark entlastet der Filter das Personal?  (nur Daten)
# ─────────────────────────────────────────────────────────────────────────────

def fig_D1(runs: Sequence[sd.RunData], mit_parametern: bool = False) -> Figure:
    """Je Lauf: Fehlalarme der Geräte (ohne Filter) und Fehleskalationen adaptiv je Bett und Stunde, Senkung darüber."""
    x = np.arange(len(runs), dtype=float)
    width = 0.36
    fig, ax = plt.subplots(figsize=(W, H))
    ymax = 0.0
    for i, run in enumerate(runs):
        m, r = run.m, run.rules["adaptiv"]
        ohne, ohne_ci = m.false_alarms_per_bed_h, m.false_alarms_ci
        ax.bar(x[i] - width / 2, ohne, width, color=STYLE["ohne"]["color"],
               label="ohne Filter: Fehlalarme der Geräte" if i == 0 else None)
        _errorbar_count(ax, x[i] - width / 2, ohne, ohne_ci)
        _label_above(ax, x[i] - width / 2, ohne_ci[1], bar_label(ohne), fontsize=7.5, pad=0.3)
        ax.bar(x[i] + width / 2, r.false_per_bed_h, width, color=STYLE["adaptiv"]["color"],
               label="adaptiv: Fehleskalationen" if i == 0 else None)
        _errorbar_count(ax, x[i] + width / 2, r.false_per_bed_h, r.false_ci)
        _label_above(ax, x[i] + width / 2, r.false_ci[1], bar_label(r.false_per_bed_h), fontsize=7.5, pad=0.3)
        ax.text(x[i], ohne_ci[1] + 0.09 * ohne, f"−{de(100 * sd.reduction(run), 0)} %", ha="center", va="bottom",
                fontsize=9, color=STYLE["adaptiv"]["color"], fontweight="bold")
        ymax = max(ymax, ohne_ci[1])
    ax.set_xticks(x)
    ax.set_xticklabels([run.spec.label_tex for run in runs])
    ax.set_ylabel("Fehlalarme / Fehleskalationen je Bett und Stunde")
    ax.set_ylim(0, 1.25 * ymax)
    _bars_grid(ax)
    fx.de_axes(ax, x=False)
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, frameon=False, fontsize=8)
    _finish_sim(fig, _main_run(runs), mit_parametern, top=0.93)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# D2  Wie viele Fehlalarme bleiben im Vergleich zu 2-aus-N und 3-aus-N?  (nur Daten)
# ─────────────────────────────────────────────────────────────────────────────

D2_Y: Dict[str, str] = {
    "prozent": "verbleibende Fehlalarme (% der Gerätefehlalarme)",
    "absolut": "Fehleskalationen je Bett und Stunde",
}


def fig_D2(runs: Sequence[sd.RunData], y_mode: str = "prozent", mit_parametern: bool = False) -> Figure:
    """Je Lauf Säulen 2-aus-N / adaptiv / 3-aus-N: Fehleskalationen in % der Gerätefehlalarme oder je Bett und Stunde."""
    x = np.arange(len(runs), dtype=float)
    width = 0.26
    offset = {"r2": -width, "adaptiv": 0.0, "r3": width}
    fig, ax = plt.subplots(figsize=(W, H))
    ymax = 0.0
    for i, run in enumerate(runs):
        for rule in sd.SIM_RULES:
            if rule not in run.rules:
                continue
            if y_mode == "prozent":
                y, ci = sd.remaining_pct(run, rule)
            else:
                y, ci = run.rules[rule].false_per_bed_h, run.rules[rule].false_ci
            xi = x[i] + offset[rule]
            ax.bar(xi, y, width, color=STYLE[rule]["color"], label=mz.RULE_NAMES[rule] if i == 0 else None)
            _errorbar_count(ax, xi, y, ci)
            _label_above(ax, xi, ci[1], bar_label(y), fontsize=7.5, pad=0.01 * y)
            ymax = max(ymax, ci[1])
    ax.set_xticks(x)
    ax.set_xticklabels([run.spec.label_tex for run in runs])
    ax.set_ylabel(D2_Y[y_mode].replace(" (", "\n[").replace(")", "]") if y_mode == "prozent" else D2_Y[y_mode])
    ax.set_ylim(0, 1.2 * ymax)
    _bars_grid(ax)
    fx.de_axes(ax, x=False)
    legend(ax, "upper right", fontsize=7.5)
    _finish_sim(fig, _main_run(runs), mit_parametern)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# D3  Welcher Anteil der Krisen wird erkannt?  (nur Daten)
# ─────────────────────────────────────────────────────────────────────────────

DODGE_RULE = {"r2": -0.012, "adaptiv": 0.0, "r3": 0.012}
DODGE_RUN = {"1h": -0.008, "12h": 0.0, "6h": 0.0, "A": 0.0, "B": 0.0, "C": 0.008, "D": 0.016}
DODGE_ZOOM = {"r2": -0.01, "adaptiv": 0.0, "r3": 0.01}


def _d3_x(run: sd.RunData, rule: str) -> float:
    return run.spec.tpr + DODGE_RULE[rule] + DODGE_RUN.get(run.key, 0.0)


def _d3_points(ax, runs: Sequence[sd.RunData], mode: str, connect: bool,
               dodge: Optional[Dict[str, float]] = None) -> None:
    by_key = {r.key: r for r in runs}

    def xpos(run: sd.RunData, rule: str) -> float:
        return run.spec.tpr + dodge[rule] if dodge is not None else _d3_x(run, rule)

    for rule in sd.SIM_RULES:
        line_runs = sorted((by_key[k] for k in sd.D3_LINE_RUNS if k in by_key and rule in by_key[k].rules),
                           key=lambda r: r.spec.tpr)
        if connect and len(line_runs) >= 2:
            ax.plot([xpos(r, rule) for r in line_runs],
                    [100 * r.rules[rule].caught_frac(mode) for r in line_runs],
                    color=STYLE[rule]["color"], linestyle="-", linewidth=1.1, zorder=2)
        for run in runs:
            if rule in run.rules:
                r = run.rules[rule]
                ci = r.caught_ci(mode)
                _point(ax, xpos(run, rule), 100.0 * r.caught_frac(mode), (100.0 * ci[0], 100.0 * ci[1]), run.spec,
                       STYLE[rule]["color"])


def _rule_legend_lines(ax, rules: Sequence[str], loc: str, **kw) -> None:
    handles = [Line2D([], [], color=STYLE[r]["color"], linestyle="-", linewidth=2.2, label=mz.RULE_NAMES[r])
               for r in rules]
    ax.legend(handles=handles, loc=loc, frameon=True, facecolor="white", edgecolor="none", framealpha=0.85, **kw)


def fig_D3(runs: Sequence[sd.RunData], mode: str = sd.DEFAULT_MODE, mit_parametern: bool = False) -> Figure:
    """Erkannte Krisen (%) über η_j: Punkte der Läufe mit Clopper–Pearson-Intervall; die Läufe 6 h, A, B, 12 h
    (η 0,6 · 0,7 · 0,8 · 0,9) je Regel durch eine Linie verbunden; 1 h hohler Kreis bei 0,9; C (Raute) und D (Quadrat)
    eigene Marker bei 0,9, nicht auf der Linie."""
    fig, ax = plt.subplots(figsize=(W, H * 1.12))
    _d3_points(ax, runs, mode, connect=True)
    ax.set_xlim(0.5, 1.0)
    ax.set_xlabel(axis_label("tpr"))
    ax.set_ylabel(f"erkannte Krisen [%], {mode}")
    fx.de_axes(ax)
    _rule_legend_lines(ax, sd.SIM_RULES, "lower right", fontsize=7.5)
    # Marker der Läufe unter der Abbildung (im Feld überdeckten sie bei vielen Läufen die Punkte)
    handles = _run_handles(runs)
    ncol = min(4, max(1, len(handles)))
    rows = math.ceil(len(handles) / ncol) if handles else 0
    fig.legend(handles=handles, loc="lower center", ncol=ncol, frameon=False, fontsize=7.5,
               bbox_to_anchor=(0.5, 0.0), handletextpad=0.5, columnspacing=1.4)
    bottom = 0.03 + 0.055 * rows
    main = _main_run(runs)
    if mit_parametern and main is not None:
        fig.tight_layout(rect=(0, bottom + 0.09, 1, 1.0))
        fig.text(0.01, bottom + 0.005, "\n".join(textwrap.wrap(sim_footer_text(main), 125)), fontsize=7.5, color=GREY,
                 ha="left", va="bottom")
    else:
        fig.tight_layout(rect=(0, bottom, 1, 1.0))
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# D4  Was geht bei jeder Regel schief?  = Bilanz der Läufe (nur Daten)
# ─────────────────────────────────────────────────────────────────────────────

def fig_D4(runs: Sequence[sd.RunData], mit_parametern: bool = False) -> Figure:
    """Je Lauf eine Spalte: oben Fehleskalationen (Anzahl, Poisson-Intervall), unten je Regel ein waagerechter
    Balken der Länge K: erkannt | Rot erst nach Krisenende | verpasst."""
    n = max(1, len(runs))
    fig, axes = plt.subplots(2, n, figsize=(W, 0.66 * W), squeeze=False,
                             gridspec_kw=dict(height_ratios=[1.3, 1.0]))
    x = np.arange(len(sd.SIM_RULES), dtype=float)
    for j, run in enumerate(runs):
        rules = [r for r in sd.SIM_RULES if r in run.rules]
        ax = axes[0][j]
        ymax = 0.0
        for i, rule in enumerate(rules):
            r = run.rules[rule]
            ci = sd.count_ci(r.n_false)
            ax.bar(x[i], r.n_false, 0.62, color=STYLE[rule]["color"])
            _errorbar_count(ax, x[i], r.n_false, ci)
            _label_above(ax, x[i], ci[1], bar_label(r.n_false), fontsize=7.5, pad=0.01 * r.n_false)
            ymax = max(ymax, ci[1])
        ax.set_ylim(0, 1.22 * ymax)
        ax.set_xticks(x[:len(rules)])
        ax.set_xticklabels([mz.RULE_NAMES[r] for r in rules], fontsize=7 if n <= 3 else 6,
                           rotation=0 if n <= 3 else 30, ha="center" if n <= 3 else "right")
        ax.set_title(run_title_lines(run, compact=n > 1), fontsize=(7.5 if n == 1 else 6.0 if n <= 3 else 5.4), color=INK, pad=5)
        if j == 0:
            ax.set_ylabel("Fehleskalationen\n(Rot ohne Krise),\nAnzahl im Lauf", fontsize=8)
        _bars_grid(ax)
        fx.de_axes(ax, x=False)

        ax2 = axes[1][j]
        for i, rule in enumerate(rules):
            r = run.rules[rule]
            K = r.n_crises
            y = len(rules) - 1 - i
            color = STYLE[rule]["color"]
            parts = [(r.caught_strict, color, 1.0, "erkannt"), (r.caught_after_end, color, LIGHT_ALPHA, "nach Ende"),
                     (r.missed, GREY, 0.7, "verpasst")]
            left = 0.0
            outside: List[Tuple[str, str]] = []
            for val, col, alpha, _name in parts:
                if val <= 0:
                    continue
                ax2.barh(y, val, left=left, height=0.62, color=col, alpha=alpha, edgecolor="white", linewidth=0.4)
                if val / K >= 0.12:
                    ax2.text(left + val / 2, y, str(val), ha="center", va="center", fontsize=7.5,
                             color="white" if alpha == 1.0 and col != GREY else INK)
                else:
                    outside.append((str(val), col if col != GREY else INK))
                left += val
            if outside:
                ax2.text(K * 1.03, y, " · ".join(t for t, _c in outside), ha="left", va="center", fontsize=7.0,
                         color=outside[0][1])
            ax2.set_xlim(0, K * 1.32)
        ax2.set_yticks([len(rules) - 1 - i for i in range(len(rules))])
        ax2.set_yticklabels([mz.RULE_NAMES[r] for r in rules], fontsize=7)
        ax2.set_xlabel("Krisen")
        ax2.yaxis.grid(False)
        ax2.set_axisbelow(True)
        fx.de_axes(ax2, y=False)
    blue = STYLE["adaptiv"]["color"]
    handles = [Patch(facecolor=blue, label="erkannt (Rot beginnt vor Krisenende)"),
               Patch(facecolor=blue, alpha=LIGHT_ALPHA, label="Rot erst nach Krisenende"),
               Patch(facecolor=GREY, alpha=0.7, label="verpasst")]
    fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False, fontsize=7.5)
    main = _main_run(runs)
    if mit_parametern and main is not None:
        fig.tight_layout(rect=(0, 0.14, 1, 1))
        fig.text(0.01, 0.008, "\n".join(textwrap.wrap(sim_footer_text(main), 125)), fontsize=7.5, color=GREY,
                 ha="left", va="bottom")
    else:
        fig.tight_layout(rect=(0, 0.06, 1, 1))
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# V1  Wie stark entlastet der Filter das Personal?  (Modell gegen Simulation)
# ─────────────────────────────────────────────────────────────────────────────

def fig_V1(runs: Sequence[sd.RunData], mit_parametern: bool = False, n_points: int = 40) -> Figure:
    """Fehlalarme ohne Filter und Fehleskalationen adaptiv je Bett und Stunde über c_art.

    Eine Modelllinie je Regel (Parameter des 12-h-Laufs); Punkte = Simulation bei c_art = Anteil „alle 4 Kanäle“
    (die Definition von c_art im Modell), ohne 1 h; eine gemeinsame Legende.
    """
    runs = _vruns(runs)
    main = _main_run(runs)
    xs = mz.x_grid("c_art", n_points)
    fig, ax = plt.subplots(figsize=(W, H))
    handles: List[Line2D] = []
    if main is not None:
        handles += _model_lines(ax, main, ["ohne"], "c_art", xs, attr="raw_day", scale=1 / 24.0)
        handles += _model_lines(ax, main, ["adaptiv"], "c_art", xs, attr="false_day", scale=1 / 24.0)
    for run in runs:
        m = run.m
        _point(ax, m.c_art_a, m.false_alarms_per_bed_h, m.false_alarms_ci, run.spec, STYLE["ohne"]["color"])
        if "adaptiv" in run.rules:
            r = run.rules["adaptiv"]
            _point(ax, m.c_art_a, r.false_per_bed_h, r.false_ci, run.spec, STYLE["adaptiv"]["color"])
    handles += _run_handles(runs)
    ax.set_xlabel(axis_label("c_art"))
    ax.set_ylabel("Fehlalarme / Fehleskalationen je Bett und Stunde")
    ax.set_ylim(bottom=0)
    fx.de_axes(ax)
    ax.legend(handles=handles, loc="center right", frameon=True, facecolor="white", edgecolor="none",
              framealpha=0.85, fontsize=7.5)
    _finish_sim(fig, main, mit_parametern)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# V2  Fehleskalationen im Vergleich zu 2-aus-N und 3-aus-N  (Modell gegen Simulation)
# ─────────────────────────────────────────────────────────────────────────────

V2_Y: Dict[str, str] = {
    "absolut": "je Bett und Stunde",
    "prozent": "% der Gerätefehlalarme",
}
V2_X_RANGE: Tuple[float, float] = (0.05, 0.2)
V2_Y_TOP: Dict[str, float] = {"absolut": 8.0, "prozent": 16.0}
V2_DODGE: Dict[str, float] = {"r2": 0.97, "adaptiv": 1.0, "r3": 1.03}     # ±3 % auf der x-Achse


def fig_V2(runs: Sequence[sd.RunData], y_mode: str = "absolut", mit_parametern: bool = False, n_points: int = 50,
           x_range: Tuple[float, float] = V2_X_RANGE) -> Figure:
    """Fehleskalationen über |M|·FPR̄ nahe an den Messpunkten: eine durchgezogene Modelllinie je Regel
    (c_art = alle 4 Kanäle, Parameter des 12-h-Laufs), Punkte der Läufe (ohne 1 h, je Regel ±3 % in x versetzt)."""
    runs = _vruns(runs)
    main = _main_run(runs)
    xs = np.geomspace(x_range[0], x_range[1], n_points)
    fig, ax = plt.subplots(figsize=(W, H))
    if y_mode == "prozent":
        handles = _model_lines(ax, main, sd.SIM_RULES, "MFPR", xs, attr="remaining", scale=100.0, solid=True)
    else:
        handles = _model_lines(ax, main, sd.SIM_RULES, "MFPR", xs, attr="false_day", scale=1 / 24.0, solid=True)
    for run in runs:
        for rule in sd.SIM_RULES:
            if rule in run.rules:
                if y_mode == "prozent":
                    y, ci = sd.remaining_pct(run, rule)
                else:
                    y, ci = run.rules[rule].false_per_bed_h, run.rules[rule].false_ci
                _point(ax, run.m.mfpr * V2_DODGE[rule], y, ci, run.spec, STYLE[rule]["color"])
    handles += _run_handles(runs)
    ax.set_xscale("log")
    ax.set_xlim(*x_range)
    ax.set_ylim(0, V2_Y_TOP[y_mode])
    ax.set_xlabel(axis_label("MFPR"))
    ax.set_ylabel("verbleibende Fehlalarme [% der Gerätefehlalarme]" if y_mode == "prozent"
                  else "Fehleskalationen je Bett und Stunde")
    fx.de_axes(ax)
    fx.log_ticks(ax, "x")
    ax.legend(handles=handles, loc="upper left", frameon=True, facecolor="white", edgecolor="none",
              framealpha=0.85, fontsize=7.5)
    _finish_sim(fig, main, mit_parametern)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# V3  Welcher Anteil der Krisen wird erkannt?  (Modell gegen Simulation)
# ─────────────────────────────────────────────────────────────────────────────

V3_VIEWS: Dict[str, str] = {"zoom": "Zoom", "uebersicht": "Übersicht"}
V3_PANELS = (        # Lauf, η_j, x-Bereich, y-Bereich
    ("6h", 0.6, (0.55, 0.65), (40.0, 100.0)),
    ("12h", 0.9, (0.85, 0.95), (85.0, 100.0)),
)


def fig_V3(runs: Sequence[sd.RunData], mode: str = sd.DEFAULT_MODE, view: str = "zoom",
           mit_parametern: bool = False, n_points: int = 50) -> Figure:
    """Erkannte Krisen in % über η_j: drei Modelllinien (Parameter des 12-h-Laufs, Kaltstart, d0 = 15 s),
    Punkte der Läufe 12 h und 6 h.  ``view = "zoom"``: zwei Ausschnitte nebeneinander (η_j = 0,6 und 0,9);
    ``"uebersicht"``: eine Achse 0,5 … 1."""
    runs = _vruns(runs)
    main = _main_run(runs)
    by_key = {r.key: r for r in runs}
    if view != "zoom":
        xs = mz.x_grid("tpr", n_points)
        fig, ax = plt.subplots(figsize=(W, H))
        handles = _model_lines(ax, main, ("r2", "r3", "adaptiv"), "tpr", xs, attr="tpr_T", scale=100.0)
        _d3_points(ax, runs, mode, connect=False)
        handles += _run_handles(runs, prefix="")
        ax.set_xlabel(axis_label("tpr"))
        ax.set_ylabel(f"erkannte Krisen [%], {mode}")
        fx.de_axes(ax)
        ax.legend(handles=handles, loc="lower right", frameon=True, facecolor="white", edgecolor="none",
                  framealpha=0.85, fontsize=7.5)
        _finish_sim(fig, main, mit_parametern)
        return fig
    fig, axes = plt.subplots(1, 2, figsize=(W, 0.55 * W))
    handles: List[Line2D] = []
    for ax, (key, eta, xr, yr) in zip(axes, V3_PANELS):
        xs = np.linspace(xr[0], xr[1], n_points)
        lines = _model_lines(ax, main, ("r2", "r3", "adaptiv"), "tpr", xs, attr="tpr_T", scale=100.0)
        if not handles:
            handles = lines
        if key in by_key:
            _d3_points(ax, [by_key[key]], mode, connect=False, dodge=DODGE_ZOOM)
        ax.set_xlim(*xr)
        ax.set_ylim(*yr)
        ax.set_title(f"{ETA_TEX} = {de(eta)} (Lauf {key.replace('h', ' h')})", fontsize=9, color=INK)
        ax.set_xlabel(axis_label("tpr"))
        fx.de_axes(ax)
    handles += _run_handles([by_key[k] for k, *_rest in V3_PANELS if k in by_key], prefix="")
    fig.supylabel(f"erkannte Krisen (%), {mode}", fontsize=10)
    fig.legend(handles=handles, loc="upper center", ncol=3, frameon=False, fontsize=7.0)
    _finish_sim(fig, main, mit_parametern, top=0.88)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# V4  Was geht bei jeder Regel schief?  (Modell gegen Simulation, |M| = 4)
# ─────────────────────────────────────────────────────────────────────────────

V4_Y: Dict[str, str] = {
    "relativ": "relativ zu 2-aus-N (%)",
    "absolut": "je Bett und Stunde",
}


def v4_split_label(missed: float, after_end: float) -> str:
    """„90 = 23 + 67“: Gesamt (streng) = verpasst + Rot erst nach Krisenende, je 1000 Krisen, Summe stimmig gerundet."""
    total = int(round(missed + after_end))
    a = int(round(missed))
    return f"{total} = {a} + {total - a}"


def _diamond(ax, x: float, y: float, text: str, fontsize: float = 7.0) -> None:
    ax.plot([x], [y], **DIAMOND)
    ax.text(x + 0.07, y, f" {text}", ha="left", va="center", fontsize=fontsize, color="black", zorder=8)


def fig_V4(run: sd.RunData, y_mode: str = "relativ", mit_parametern: bool = False) -> Figure:
    """Je Regel eine Säule der Simulation (Farbe der Regel) und ein schwarzer Rhombus für das Modell
    (c_art = alle 4 Kanäle).  Links Fehleskalationen relativ zu 2-aus-N oder je Bett und Stunde; rechts verpasste
    Krisen je 1000 Krisen, Säule zweiteilig (verpasst auch mild | schraffiert Rot erst nach Krisenende)."""
    rules = [r for r in sd.SIM_RULES if r in run.rules]
    vals = {rule: sd.v4_values(run, rule) for rule in rules}
    rel = sd.v4_relative(run, rules) if y_mode == "relativ" else None
    x = np.arange(len(rules), dtype=float)
    width = 0.5
    fig, (axl, axr) = plt.subplots(1, 2, figsize=(W, 0.56 * W))
    # links ──────────────────────────────────────────────────────────────
    left_vals = []
    for rule in rules:
        v = vals[rule]
        if rel is not None:
            left_vals.append((rel[rule].sim_pct, rel[rule].sim_ci_pct, rel[rule].model_pct))
        else:
            left_vals.append((v.sim_false, v.sim_false_ci, v.model_false_a))
    top_l = 1.25 * max(max(ci[1], m) for _s, ci, m in left_vals)
    for i, rule in enumerate(rules):
        s, ci, m = left_vals[i]
        color = STYLE[rule]["color"]
        axl.bar(x[i], s, width, color=color)
        _errorbar_count(axl, x[i], s, ci)
        _label_above(axl, x[i], ci[1], bar_label(s), fontsize=7.5, pad=0.012 * top_l)
        _diamond(axl, x[i] + 0.3, m, bar_label(m))
    axl.set_ylim(0, top_l)
    axl.set_ylabel("Fehleskalationen relativ zu 2-aus-N [%]" if rel is not None
                   else "Fehleskalationen je Bett und Stunde")
    # rechts ─────────────────────────────────────────────────────────────
    top_r = 1.25 * max(max(v.sim_missed_strict_ci_1000[1], v.model_missed_1000) for v in vals.values())
    for i, rule in enumerate(rules):
        v, color = vals[rule], STYLE[rule]["color"]
        axr.bar(x[i], v.sim_missed_1000, width, color=color)
        axr.bar(x[i], v.sim_after_end_1000, width, bottom=v.sim_missed_1000, facecolor="white", edgecolor=color,
                hatch="///", linewidth=0.8)
        total = v.sim_missed_1000 + v.sim_after_end_1000
        _errorbar_count(axr, x[i], total, v.sim_missed_strict_ci_1000)
        _label_above(axr, x[i], v.sim_missed_strict_ci_1000[1], v4_split_label(v.sim_missed_1000, v.sim_after_end_1000),
                     fontsize=7.0, pad=0.012 * top_r)
        _diamond(axr, x[i] + 0.3, v.model_missed_1000, bar_label(v.model_missed_1000))
    axr.set_ylim(0, top_r)
    axr.set_ylabel("verpasste Krisen je 1000 Krisen (streng)")
    for ax in (axl, axr):
        ax.set_xticks(x)
        ax.set_xticklabels([mz.RULE_NAMES[r] for r in rules])
        ax.set_xlim(-0.5, len(rules) - 0.3)
        _bars_grid(ax)
        fx.de_axes(ax, x=False)
    handles = [Patch(facecolor=GREY, label=f"Simulation {run.spec.label_tex} [95 %]"),
               Line2D([], [], label="Modell", **DIAMOND),
               Patch(facecolor="white", edgecolor=GREY, hatch="///", label="Rot erst nach Krisenende")]
    fig.legend(handles=handles, loc="upper center", ncol=3, frameon=False, fontsize=7.5)
    _finish_sim(fig, run, mit_parametern, top=0.93)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────────────────────────

BUILDERS: Dict[str, Callable[..., Figure]] = {"K1": fig_K1, "K2": fig_K2, "K3": fig_K3, "K4": fig_K4}
SIM_BUILDERS: Dict[str, Callable[..., Figure]] = {
    "D1": fig_D1, "D2": fig_D2, "D3": fig_D3, "D4": fig_D4, "V1": fig_V1, "V2": fig_V2, "V3": fig_V3,
}
DEFAULT_CHOICES: Dict[str, tuple] = {
    "K1": ("c_art", mz.DEFAULT_UNIT),
    "K2": ("MFPR", "prozent", mz.DEFAULT_UNIT),
    "K3": ("tpr", "erkannt"),
    "K4": (mz.DEFAULT_K4,),
}


def _save_with_retry(fig: Figure, path: Path, dpi: Optional[int], attempts: int = 5) -> None:
    """savefig mit Wiederholung: Windows (Sync-/Virenscanner) sperrt frisch geschriebene Dateien kurz."""
    for i in range(attempts):
        try:
            fig.savefig(path, dpi=dpi)
            return
        except OSError:
            if i == attempts - 1:
                raise
            time.sleep(0.4 * (i + 1))


def _write(figs: Dict[str, Callable[[], Figure]], names: Dict[str, str], outdir: Path, formats: Sequence[str],
           dpi: int) -> List[Path]:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    for key, make in figs.items():
        fig = make()
        for ext in formats:
            path = outdir / f"{names[key]}.{ext}"
            _save_with_retry(fig, path, dpi if ext == "png" else None)
            written.append(path)
        plt.close(fig)
    return written


def export_all(p: mz.Params, outdir: Path = FIG_DIR, choices: Optional[Dict[str, tuple]] = None,
               formats: Sequence[str] = ("png", "svg"), dpi: int = 300, mit_parametern: bool = False) -> List[Path]:
    """K1–K4 als PNG (300 dpi) und SVG schreiben; nur die vier Dateinamen werden überschrieben."""
    chosen = {**DEFAULT_CHOICES, **(choices or {})}
    figs = {key: (lambda key=key, builder=builder: builder(p, *chosen[key], mit_parametern=mit_parametern))
            for key, builder in BUILDERS.items()}
    return _write(figs, K_FILES, outdir, formats, dpi)


def _select(runs: Dict[str, sd.RunData], keys: Optional[Sequence[str]]) -> List[sd.RunData]:
    return [runs[k] for k in sd.RUN_ORDER if k in runs and (keys is None or k in keys)]


def export_experimente(runs: Dict[str, sd.RunData], outdir: Path = FIG_DIR, mode: str = sd.DEFAULT_MODE,
                       d2_y: str = "prozent", keys: Optional[Sequence[str]] = None, mit_parametern: bool = False,
                       formats: Sequence[str] = ("png", "svg"), dpi: int = 300, suffix: str = "") -> List[Path]:
    """D1–D4 (nur Daten) schreiben; ``suffix`` (z. B. ``_v2``) wird an die Dateinamen gehängt, die alten Dateien
    bleiben unberührt."""
    sel = _select(runs, keys)
    if not sel:
        return []
    figs = {
        "D1": lambda: fig_D1(sel, mit_parametern),
        "D2": lambda: fig_D2(sel, d2_y, mit_parametern),
        "D3": lambda: fig_D3(sel, mode, mit_parametern),
        "D4": lambda: fig_D4(sel, mit_parametern),
    }
    return _write(figs, {k: v + suffix for k, v in D_FILES.items()}, outdir, formats, dpi)


def export_vergleich(runs: Dict[str, sd.RunData], outdir: Path = FIG_DIR, mode: str = sd.DEFAULT_MODE,
                     v2_y: str = "absolut", v4_run: str = sd.MAIN_RUN, keys: Optional[Sequence[str]] = None,
                     mit_parametern: bool = False, formats: Sequence[str] = ("png", "svg"),
                     dpi: int = 300, v4_y: str = "relativ", v3_view: str = "zoom") -> List[Path]:
    """V1–V4 (Modell gegen Simulation) schreiben; V3 als Zoom."""
    sel = _select(runs, keys)
    if not sel:
        return []
    v4 = runs.get(v4_run) or sel[0]
    figs = {
        "V1": lambda: fig_V1(sel, mit_parametern),
        "V2": lambda: fig_V2(sel, v2_y, mit_parametern),
        "V3": lambda: fig_V3(sel, mode, v3_view, mit_parametern),
        "V4": lambda: fig_V4(v4, v4_y, mit_parametern),
    }
    return _write(figs, V_FILES, outdir, formats, dpi)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Abbildungen K1–K4, D1–D4, V1–V4 (Ziel 3) exportieren, PNG 300 dpi + SVG.")
    ap.add_argument("--outdir", type=Path, default=FIG_DIR, help=f"Zielordner (Standard: {FIG_DIR})")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--szenario", choices=list(mz.SCENARIOS), default=mz.DEFAULT_SCENARIO,
                    help="Szenario der Seitenleiste für K1–K4 (Standard: Median-Intensivstation)")
    ap.add_argument("--mit-parametern", action="store_true", help="graue Parameterzeile unter die Abbildungen setzen")
    ap.add_argument("--erkannt", choices=list(sd.MODES), default=sd.DEFAULT_MODE,
                    help="Definition „erkannt“ für D3/D4/V3 (Standard: streng)")
    ap.add_argument("--v4-lauf", choices=list(sd.E4_RUNS), default=sd.MAIN_RUN, help="Lauf für V4 (Standard: 12h)")
    ap.add_argument("--v4-y", choices=list(V4_Y), default="relativ")
    ap.add_argument("--d2-y", choices=list(D2_Y), default="prozent")
    ap.add_argument("--v2-y", choices=list(V2_Y), default="absolut")
    ap.add_argument("--daten", type=Path, default=sd.DATA_DIR, help=f"Ordner der Simulationsdaten (Standard: {sd.DATA_DIR})")
    ap.add_argument("--suffix", default="", help="Namenszusatz für D1–D4 (z. B. _v2; alte Dateien bleiben)")
    ap.add_argument("--laeufe", nargs="+", default=None, help="Läufe für D1–D4 / V1–V4 (Schlüssel; Standard: alle vorhandenen)")
    ap.add_argument("--nur", choices=["modell", "experimente", "vergleich"], default=None,
                    help="nur K1–K4 (modell), D1–D4 (experimente) oder V1–V4 (vergleich); Standard: alle")
    args = ap.parse_args(argv)
    written: List[Path] = []
    if args.nur in (None, "modell"):
        written += export_all(mz.scenario_params(args.szenario), args.outdir, dpi=args.dpi,
                              mit_parametern=args.mit_parametern)
    if args.nur in (None, "experimente", "vergleich"):
        runs = sd.load_runs(args.daten)
        if not runs:
            print(f"keine Simulationsdaten in {args.daten}: D1–D4 und V1–V4 übersprungen")
        else:
            if args.nur in (None, "experimente"):
                written += export_experimente(runs, args.outdir, mode=args.erkannt, d2_y=args.d2_y, keys=args.laeufe,
                                              mit_parametern=args.mit_parametern, dpi=args.dpi, suffix=args.suffix)
            if args.nur in (None, "vergleich"):
                written += export_vergleich(runs, args.outdir, mode=args.erkannt, v2_y=args.v2_y, v4_run=args.v4_lauf,
                                            keys=args.laeufe or list(sd.ORIGINAL_RUNS),
                                            mit_parametern=args.mit_parametern, dpi=args.dpi, v4_y=args.v4_y)
    for path in written:
        print("geschrieben:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
