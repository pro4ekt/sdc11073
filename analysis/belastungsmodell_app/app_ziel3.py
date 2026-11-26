"""Streamlit-Seite „Ziel 3 – vier Kurven“: drei Reiter mit denselben vier Fragen.

Start aus dem Repository-Wurzelverzeichnis:

    .venv\\Scripts\\python -m streamlit run analysis/belastungsmodell_app/app_ziel3.py

Reiter „Modell (Literatur)“ – K1–K4 aus dem Rechenkern ``model_ziel3.py`` (Übertragung des bestätigten
Prototyps); die Seitenleiste (Szenario, Station, Prioritäten, Annahmen, Filtermodell, Darstellung, Abgeleitet)
wirkt nur hier.  Reiter „Experimente (Simulation)“ – D1–D4 nur aus den Daten der drei Simulationsläufe
(``sim_data.py``, Dateien in ``docs/reports/``).  Reiter „Vergleich Modell – Simulation“ – V1–V4: Linie = Modell
mit den Parametern des 12-h-Laufs, Punkt mit Fehlerbalken = Simulation (12 h und 6 h; 1 h nur in den Tabellen).
Abbildungen und Export in ``export_ziel3.py``.  Reiter „ROC (Gerätegrenzwerte)“ – R1a–R5 aus ``analysis/roc_ziel3``.
Reiter „ROC-Werkstatt“ – alle Kurven zu Ziel 3 in einem Bild mit den adaptiv-Konfigurationen (T, α, δ_min) aus
``python -m analysis.roc_ziel3.werkstatt``; Regler in der Seitenleiste „8  ROC-Werkstatt“; nur gerechnete Daten.
"""

from __future__ import annotations

import io
import json
import math
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model_ziel3 as mz  # noqa: E402
import export_ziel3 as fz  # noqa: E402
import sim_data as sd  # noqa: E402
from export_figures import de  # noqa: E402

st.set_page_config(page_title="Ziel 3 – vier Kurven", page_icon="📉", layout="wide")

MFPR = "|M|·FPR̄"          # Rauschen mit Mittelwertstrich (Streamlit-Text)
ETA = sd.ETA               # η_j

# ─────────────────────────────────────────────────────────────────────────────
# Regler
# ─────────────────────────────────────────────────────────────────────────────

MAX_M = 12
PRIOS = ["hoch", "mittel", "niedrig"]

# key, Beschriftung, min, max, Schritt, Standard, Anzeigeformat
STATION = [
    ("n", "n – Alarme je Alarmkanal und Tag", 5.0, 300.0, 0.5, 37.5, "%.1f"),
    ("fdr", "FDR – Anteil nicht relevanter Alarme", 0.50, 0.99, 0.01, 0.90, "%.2f"),
    ("d_false", "d_false – Dauer eines Fehlalarms (s)", 2.0, 60.0, 0.1, 8.0, "%.1f"),
    ("M", "Alarmkanäle am Bett |M|", 2, 12, 1, 4, None),
]
ANNAHMEN = [
    ("c_art", "c_art – gemeinsame Artefakte", 0.0, 1.0, 0.01, 0.50, "%.2f"),
    ("tpr", f"TPR_j je Kanal (= {ETA} im Modell)", 0.50, 0.99, 0.01, 0.90, "%.2f"),
    ("d_true", "d_true – mittlere Dauer einer Krise (s)", 5.0, 120.0, 0.1, 30.0, "%.1f"),
    ("d0", "d0 – kürzeste Krise (s)", 0.0, 60.0, 1.0, 15.0, "%.0f"),
]
MODELL = [
    ("T", "T – Fenster (s)", 2.0, 30.0, 1.0, 10.0, "%.0f"),
    ("delta_min", "δ_min – Untergrenze der Schwelle", 0.10, 1.00, 0.01, 0.50, "%.2f"),
    ("alpha", "α – Gewicht im Schweregrad", 0.0, 1.0, 0.05, 0.50, "%.2f"),
]
SLIDERS = STATION + ANNAHMEN + MODELL
DEFAULT_P = mz.Params().P
CHOICE_DEFAULTS = {
    "unit": mz.DEFAULT_UNIT, "k1_x": "c_art", "k2_x": "MFPR", "k2_y": "prozent", "k3_x": "tpr", "k3_y": "erkannt",
    "k4_F": mz.DEFAULT_K4,
    # Reiter Experimente (d_*) und Vergleich (v_*)
    "d_run_1h": True, "d_run_6h": True, "d_run_12h": True, "d_mode": sd.DEFAULT_MODE, "d2_y": "prozent",
    "v_run_1h": True, "v_run_6h": True, "v_run_12h": True, "v_mode": sd.DEFAULT_MODE, "v2_y": "absolut",
    "v3_view": "zoom", "v4_run": sd.MAIN_RUN, "v4_y": "relativ",
}


def _prio_defaults(P) -> dict:
    """prio_1 … prio_12 aus einem Prioritätsvektor; Kanäle jenseits von |M| niedrig."""
    return {f"prio_{i + 1}": mz.PRIO_NAMES[P[i]] if i < len(P) else "niedrig" for i in range(MAX_M)}


def _init_state() -> None:
    for key, *_rest in SLIDERS:
        st.session_state.setdefault(key, _rest[4])
    for key, val in _prio_defaults(DEFAULT_P).items():
        st.session_state.setdefault(key, val)
    for key, val in CHOICE_DEFAULTS.items():
        st.session_state.setdefault(key, val)
    st.session_state.setdefault("M_prev", int(st.session_state["M"]))


def _reset_sliders() -> None:
    for key, *_rest in SLIDERS:
        st.session_state[key] = _rest[4]
    for key, val in _prio_defaults(DEFAULT_P).items():
        st.session_state[key] = val
    st.session_state["M_prev"] = int(st.session_state["M"])


def _apply_scenario(name: str) -> None:
    """Alle Regler auf Standard, dann die Werte des Szenarios."""
    _reset_sliders()
    for key, val in mz.SCENARIOS[name].items():
        if key == "P":
            st.session_state["M"] = len(val)
            for k, v in _prio_defaults(val).items():
                st.session_state[k] = v
        else:
            st.session_state[key] = val
    st.session_state["M_prev"] = int(st.session_state["M"])


def _on_M_change() -> None:
    """Beim Vergrößern von |M| erhalten die neuen Kanäle niedrige Priorität."""
    new, old = int(st.session_state["M"]), int(st.session_state["M_prev"])
    for i in range(old + 1, new + 1):
        st.session_state[f"prio_{i}"] = "niedrig"
    st.session_state["M_prev"] = new


def params_from_state() -> mz.Params:
    s = st.session_state
    M = int(s["M"])
    return mz.Params(
        n=float(s["n"]), fdr=float(s["fdr"]), d_false=float(s["d_false"]), d_true=float(s["d_true"]),
        P=tuple(mz.PRIO_LABELS[s[f"prio_{i}"]] for i in range(1, M + 1)),
        c_art=float(s["c_art"]), tpr=float(s["tpr"]), d0=float(s["d0"]),
        T=float(s["T"]), delta_min=float(s["delta_min"]), alpha=float(s["alpha"]),
    )


def num(x: float, digits: int = 2) -> str:
    if x is None or x != x:
        return "–"
    if math.isinf(x):
        return "∞"
    return de(x, digits)


def pct(x: float, digits: int = 2) -> str:
    return "–" if x != x else num(100.0 * x, digits) + " %"


def cnt(x: float) -> str:
    """Stückzahl: zwei Nachkommastellen unter 10, sonst eine."""
    return num(x, 2 if abs(x) < 10 else 1)


def ci_text(lo: float, hi: float, digits: int = 2, suffix: str = "") -> str:
    return f"[{num(lo, digits)}; {num(hi, digits)}{suffix}]"


def ja(flag: bool) -> str:
    return "ja" if flag else "nein"


# ─────────────────────────────────────────────────────────────────────────────
# Seitenleiste (nur Reiter „Modell“)
# ─────────────────────────────────────────────────────────────────────────────

_init_state()

with st.sidebar:
    st.caption("Die Seitenleiste gehört zum Reiter „Modell (Literatur)“.")
    with st.expander("1  Szenario", expanded=True):
        st.caption("Jede Schaltfläche setzt alle Regler auf Standard (Median-Intensivstation) und stellt "
                   "ihre Werte ein.")
        cols = st.columns(2)
        for i, name in enumerate(mz.SCENARIOS):
            cols[i % 2].button(name, on_click=_apply_scenario, args=(name,), key=f"btn_{i}", width="stretch")

    with st.expander("2  Station (Literatur)"):
        for key, label, lo, hi, step, _default, fmt in STATION:
            if key == "M":
                st.slider(label, lo, hi, step=step, key=key, on_change=_on_M_change)
            else:
                st.slider(label, lo, hi, step=step, key=key, format=fmt)

    with st.expander("3  Prioritäten der Alarmkanäle"):
        st.caption("hoch = 3, mittel = 2, niedrig = 1. Neue Kanäle beginnen mit niedrig.")
        cols = st.columns(3)
        for i in range(1, int(st.session_state["M"]) + 1):
            cols[(i - 1) % 3].selectbox(f"Kanal {i}", PRIOS, key=f"prio_{i}")

    with st.expander("4  Annahmen"):
        st.caption("nicht aus der Literatur belegt, als Bandbreite lesen")
        for key, label, lo, hi, step, _default, fmt in ANNAHMEN:
            st.slider(label, lo, hi, step=step, key=key, format=fmt)

    with st.expander("5  Filtermodell (Prototyp)"):
        for key, label, lo, hi, step, _default, fmt in MODELL:
            st.slider(label, lo, hi, step=step, key=key, format=fmt)

    with st.expander("6  Darstellung"):
        st.radio("Einheit der Fehlalarme (Tabelle, K1, K2)", list(mz.UNITS), key="unit")

P = params_from_state()
CAL = mz.calibrate(P)
UNIT = st.session_state["unit"]
F_UNIT = mz.UNITS[UNIT]

with st.sidebar:
    with st.expander("7  Abgeleitet", expanded=True):
        st.table(pd.DataFrame({
            "Größe": ["Alarme je Bett und Tag", "P(C)", "Krisen je Bett und Tag", "FPR je Kanal", MFPR,
                      "w = ln(TPR/FPR)", "Obergrenze 1 − c_art/|M|"],
            "Wert": [num(CAL.N_d, 1), pct(CAL.PC, 3), num(CAL.crises_day, 2), num(CAL.FPR, 5), num(CAL.MFPR, 4),
                     num(CAL.w, 2), pct(CAL.ceiling, 1)],
        }).set_index("Größe"))


# ─────────────────────────────────────────────────────────────────────────────
# Abbildungen (gecacht als PNG)
# ─────────────────────────────────────────────────────────────────────────────

def _to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return buf.getvalue()


@st.cache_data(show_spinner=False, max_entries=300)
def _png(name: str, p: mz.Params, extra: tuple) -> bytes:
    return _to_png(fz.BUILDERS[name](p, *extra))


def figure(name: str, *extra) -> None:
    st.image(_png(name, P, tuple(extra)), width="stretch")


def x_select(container, key: str, options) -> str:
    return container.selectbox("x-Achse", list(options), format_func=lambda k: fz.AXIS_SHORT[k], key=key)


@st.cache_data(show_spinner=False)
def _runs() -> dict:
    return sd.load_runs()


@st.cache_data(show_spinner=False, max_entries=100)
def _png_sim(name: str, keys: tuple, extra: tuple) -> bytes:
    runs = _runs()
    if name == "V4":
        return _to_png(fz.fig_V4(runs[keys[0]], *extra))
    return _to_png(fz.SIM_BUILDERS[name]([runs[k] for k in keys], *extra))


def figure_sim(name: str, keys: tuple, *extra) -> None:
    st.image(_png_sim(name, keys, tuple(extra)), width="stretch")


def run_controls(prefix: str, runs_all: dict, with_v4: bool = False, runs_in_sidebar: bool = False):
    """Läufe, Definition „erkannt“ (und Lauf für V4) oben auf den Reitern 2 und 3; ``runs_in_sidebar``: die
    Lauf-Schalter stehen in der Seitenleiste („9  Experimente: Läufe“)."""
    c1, c2, c3 = st.columns([1.2, 2.2, 1.4])
    with c1:
        if runs_in_sidebar:
            st.markdown("**Läufe**")
            st.caption("Auswahl in der Seitenleiste „9  Experimente: Läufe“ (Standard: alle).")
        else:
            st.markdown("**Läufe**")
            for k in sd.RUN_ORDER:
                if k in runs_all:
                    st.checkbox(runs_all[k].spec.label_eta, key=f"{prefix}_run_{k}")
    with c2:
        mode = st.radio("Definition „erkannt“", list(sd.MODES), format_func=lambda k: sd.MODES[k], key=f"{prefix}_mode")
    v4 = None
    if with_v4:
        with c3:
            v4 = st.radio("Lauf für V4", [k for k in sd.E4_RUNS if k in runs_all],
                          format_func=lambda k: runs_all[k].spec.label_eta, key="v4_run")
    keys = tuple(k for k in sd.RUN_ORDER if k in runs_all and st.session_state.get(f"{prefix}_run_{k}"))
    return keys, mode, v4


# ─────────────────────────────────────────────────────────────────────────────
# Hauptbereich
# ─────────────────────────────────────────────────────────────────────────────

st.title("Ziel 3 – vier Kurven")
st.caption(
    "Analytisches Belastungsmodell (Kapitel 4/5) für den adaptiven Alarmfilter: vier Fragen, auf jedem Reiter in "
    "derselben Reihenfolge. „ohne Filter“ meldet jeder Alarmkanal selbst, 2-aus-N und 3-aus-N sind die klassischen "
    "Zählregeln, „adaptiv“ ist der Prototyp mit Prioritäten, Fenster T und schmelzender Schwelle. "
    "Reiter „Modell“: Literaturwerte und Regler links. Reiter „Experimente“: nur die Daten der Simulationsläufe. "
    "Reiter „Vergleich“: Linie = Modell mit den Parametern des 12-h-Laufs, Punkt = Simulation."
)

tab_modell, tab_exp, tab_vgl, tab_roc, tab_ws, tab_k5 = st.tabs(["Modell (Literatur)", "Experimente (Simulation)",
                                                                 "Vergleich Modell – Simulation", "ROC (Gerätegrenzwerte)",
                                                                 "ROC-Werkstatt", "Kapitel 5 · Gesamtbild"])

# ═════════════════════════════════════════════════════════════════════════════
with tab_modell:
    if not CAL.valid:
        st.warning(f"Kalibrierung außerhalb des Modells: FPR = {num(CAL.FPR, 4)} sollte kleiner als TPR_j = "
                   f"{num(P.tpr)} sein (w > 0). Die Zahlen werden trotzdem gerechnet.")

    RES = mz.evaluate_all(P, cal=CAL)
    R_OHNE, R_2, R_3, R_AD = (RES[k] for k in ("ohne", "r2", "r3", "adaptiv"))

    # Tabelle ─────────────────────────────────────────────────────────────────
    st.subheader("Was beim Personal ankommt")
    st.table(pd.DataFrame([{
        "Regel": mz.RULE_NAMES[rule],
        f"Fehlalarme ({UNIT})": cnt(r.false_day * F_UNIT),
        "ARR (Zeitanteil, Kap. 5)": "–" if rule == "ohne" else pct(r.ARR, 1),
        "Reduktion (Anzahl)": "–" if rule == "ohne" else pct(r.reduction_count, 1),
        "erkannte Krisen": f"{pct(r.tpr_T)} ({pct(r.tpr_inf)} Kap. 5)",
        "verpasste Krisen je 1000": num(r.missed_per_1000, 1),
    } for rule, r in RES.items()]).set_index("Regel"))
    st.caption("Fehlalarme: als Episoden gezählt (Übergänge in eine eskalierende Teilmenge); Reduktion (Anzahl) = "
               "1 − Fehlalarme/Fehlalarme ohne Filter, ARR = Zeitanteil wie in Kapitel 5. "
               "erkannte Krisen: Kaltstart – alle Kanäle beginnen gleichzeitig, die Krise dauert "
               "D = d0 + Exp(d_true − d0) und zählt nur, wenn die Eskalation vor ihrem Ende beginnt. "
               "In Klammern der Wert aus Kapitel 5, bei dem die Krise anhält und die Schwelle schmelzen kann.")

    # K1 ──────────────────────────────────────────────────────────────────────
    st.subheader("K1  Wie stark entlastet der Filter das Personal?")
    st.markdown("*Lesehilfe:* Ohne Filter meldet jeder Alarmkanal selbst; der Filter lässt nur durch, was zusammen "
                "die Schwelle erreicht. Der Abstand der Linien ist die Entlastung, die senkrechte Linie der "
                "eingestellte Wert.")
    c1, _ = st.columns([1, 3])
    k1_x = x_select(c1, "k1_x", mz.K1_AXES)
    figure("K1", k1_x, UNIT)
    st.markdown(
        f"**Eingestellt:** {cnt(R_OHNE.false_day * F_UNIT)} Fehlalarme {UNIT} ohne Filter, "
        f"{cnt(R_AD.false_day * F_UNIT)} mit Filter (−{de(100 * R_AD.reduction_count, 1)} % nach Anzahl, "
        f"ARR nach Zeitanteil {pct(R_AD.ARR, 1)}). "
        f"Die Reduktion ist durch gemeinsame Artefakte begrenzt, die alle Kanäle zugleich treffen: "
        f"Obergrenze 1 − c_art/|M| = {pct(CAL.ceiling, 1)}."
    )

    # K2 ──────────────────────────────────────────────────────────────────────
    st.subheader("K2  Wie viele Fehlalarme bleiben im Vergleich zu 2-aus-N und 3-aus-N?")
    st.markdown("*Lesehilfe:* 100 % wären alle Fehlalarme ohne Filter; weniger ist besser. Die Zählregeln "
                "kennen keine Prioritäten und kein Schmelzen, adaptiv bewegt sich zwischen 2-aus-N und 3-aus-N.")
    c1, c2, _ = st.columns([1, 1, 2])
    k2_x = x_select(c1, "k2_x", mz.K2_AXES)
    k2_y = c2.selectbox("y-Achse", list(mz.K2_Y), format_func=lambda k: mz.K2_Y[k], key="k2_y")
    figure("K2", k2_x, k2_y, UNIT)
    st.markdown(
        f"**Eingestellt:** verbleibende Fehlalarme (Anzahl) 2-aus-N {pct(R_2.remaining, 1)}, 3-aus-N "
        f"{pct(R_3.remaining, 1)}, adaptiv {pct(R_AD.remaining, 1)} von ohne Filter ({cnt(R_2.false_day * F_UNIT)}, "
        f"{cnt(R_3.false_day * F_UNIT)} und {cnt(R_AD.false_day * F_UNIT)} {UNIT}). "
        f"Differenz zu adaptiv: 2-aus-N {'+' if R_2.false_day >= R_AD.false_day else ''}"
        f"{cnt((R_2.false_day - R_AD.false_day) * F_UNIT)}, 3-aus-N "
        f"{'+' if R_3.false_day >= R_AD.false_day else ''}{cnt((R_3.false_day - R_AD.false_day) * F_UNIT)} {UNIT}."
    )

    # K3 ──────────────────────────────────────────────────────────────────────
    st.subheader("K3  Welcher Anteil der Krisen wird erkannt?")
    st.markdown("*Lesehilfe:* Eine Krise zählt als erkannt, wenn die Eskalation vor ihrem Ende beginnt (Kaltstart, "
                "alle Kanäle zugleich); die dünne gestrichelte Linie ist der Wert aus Kapitel 5 bei anhaltender Krise. "
                "Höher ist besser.")
    c1, c2, _ = st.columns([1, 1, 2])
    k3_x = x_select(c1, "k3_x", mz.K3_AXES)
    k3_y = c2.selectbox("y-Achse", list(mz.K3_Y), format_func=lambda k: mz.K3_Y[k], key="k3_y")
    figure("K3", k3_x, k3_y)
    st.markdown(
        f"**Eingestellt:** erkannte Krisen ohne Filter {pct(R_OHNE.tpr_T)}, 2-aus-N {pct(R_2.tpr_T)}, "
        f"3-aus-N {pct(R_3.tpr_T)}, adaptiv {pct(R_AD.tpr_T)} (hält die Krise an: {pct(R_AD.tpr_inf)}, Kapitel 5); "
        f"verpasste Krisen je 1000: {num(R_OHNE.missed_per_1000, 1)}, {num(R_2.missed_per_1000, 1)}, "
        f"{num(R_3.missed_per_1000, 1)} und {num(R_AD.missed_per_1000, 1)}. "
        f"Was das an unnötigen Eskalationen kostet, zeigt K4. "
        f"Im Modell gilt {ETA} = TPR_j: Ein Gerät, das eine Krise anzeigt, alarmiert bis zu ihrem Ende."
    )

    # K4 ──────────────────────────────────────────────────────────────────────
    st.subheader("K4  Was geht bei jeder Regel schief?")
    st.markdown("*Lesehilfe:* Links die unnötigen Eskalationen (Fehlalarme ohne die gemeinsamen Artefakte aller "
                "Kanäle), rechts die verpassten Krisen, je Regel über der Zahl der Alarmkanäle |M| = 4, 5, 6, 8, 10, 12 "
                "mit den eingestellten Anteilen hoch/mittel/niedrig. **In beiden Bildern ist weniger besser.**")
    k4_F = st.radio("Zeitraum", list(mz.K4_FACTORS), key="k4_F", horizontal=True)
    figure("K4", k4_F)
    F4 = mz.K4_FACTORS[k4_F]
    k4_now = {rule: mz.k4_values(RES[rule], F4) for rule in mz.K4_RULES}
    k4_text = "; ".join(f"{mz.RULE_NAMES[rule]} {cnt(u)} unnötige Eskalationen und {cnt(v)} verpasste Krisen"
                        for rule, (u, v) in k4_now.items())
    st.markdown(
        "Säulen mit ↑ sind abgeschnitten, die Zahl gibt den Wert an. Adaptiv hat fast so wenige unnötige "
        "Eskalationen wie 3-aus-N und fast so wenige verpasste Krisen wie 2-aus-N. "
        f"**Bei |M| = {P.M}, {k4_F}:** {k4_text}. Krisen im Zeitraum: {cnt(CAL.crises_day * F4)}. "
        f"Nicht im Bild: {cnt(R_AD.common_day * F4)} Eskalationen durch gemeinsame Artefakte aller Kanäle, "
        f"die jede Regel gleich durchlässt."
    )
    if P.M < 4:
        st.markdown("Bei |M| = 2–3 eskaliert adaptiv nach dem Absinken der Schwelle auch ein einzelner, langer Kanal "
                    "hoher Priorität (3.17), mit mehr unnötigen Eskalationen und weniger verpassten Krisen als 2-aus-N.")

    # Annahmen ───────────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown(
        "**Annahmen des Modells**\n"
        "- Alarmkanäle bedingt unabhängig gegeben Krise bzw. keine Krise, alle mit gleichem Gewicht w = ln(TPR_j/FPR).\n"
        "- Kaltstart: alle beteiligten Kanäle beginnen gleichzeitig.\n"
        "- Fehlalarme wie in Kapitel 5, ohne Fenstereffekt T (konservativ für den Filter).\n"
        "- Krisen: alle Kanäle beginnen gleichzeitig, Dauer D = d0 + Exp(d_true − d0) (konservativ für den Filter).\n"
        "- k-aus-N: klassische Zählregel ohne Fenster und ohne Schmelzen.\n"
        "- Fehleskalationen als Episoden gezählt (Übergänge in eine eskalierende Teilmenge); für adaptiv obere "
        "Grenze ohne Wirkung des Fensters T (Monte-Carlo: tatsächlich etwa halb so viele)."
    )

    # Export ─────────────────────────────────────────────────────────────────
    st.subheader("Export")
    st.markdown(f"Schreibt K1–K4 mit den **aktuellen** Reglern und Achsenwahlen als PNG (300 dpi) und SVG nach "
                f"`{fz.FIG_DIR}` (ohne Titel und Parameterzeile; die Abbildungen in `figures/` bleiben unberührt). "
                f"Skript: `python analysis/belastungsmodell_app/export_ziel3.py --nur modell [--mit-parametern]`.")
    if st.button("K1–K4 exportieren"):
        with st.spinner("Abbildungen werden erzeugt …"):
            written = fz.export_all(P, choices={"K1": (k1_x, UNIT), "K2": (k2_x, k2_y, UNIT), "K3": (k3_x, k3_y),
                                                "K4": (k4_F,)})
        st.success(f"{len(written)} Dateien geschrieben:")
        st.code("\n".join(str(path) for path in written))

# ═════════════════════════════════════════════════════════════════════════════
RUNS_ALL = _runs()
NO_DATA = (f"Keine Simulationsdaten gefunden (erwartet messwerte_*.json und results_*.json in {sd.DATA_DIR}).")
for _k in sd.RUN_ORDER:                      # Reiter Experimente: alle Läufe standardmäßig an, Vergleich: die drei ersten
    st.session_state.setdefault(f"d_run_{_k}", True)
    st.session_state.setdefault(f"v_run_{_k}", _k in sd.ORIGINAL_RUNS)
MISSING_RUNS = [k for k in sd.RUN_ORDER if k not in RUNS_ALL]

with st.sidebar:
    with st.expander("9  Experimente: Läufe", expanded=False):
        st.caption("Läufe auf dem Reiter „Experimente (Simulation)“ (D1–D4 und Tabellen).")
        for _k in sd.RUN_ORDER:
            if _k in RUNS_ALL:
                st.checkbox(RUNS_ALL[_k].spec.label_eta, key=f"d_run_{_k}")
        if MISSING_RUNS:
            st.caption("Noch ohne Daten: " + ", ".join(f"{k} ({sd.RUNS[k].name})" for k in MISSING_RUNS))

with tab_exp:
    st.subheader("Experimente: Simulationsläufe")
    st.markdown(
        "20 virtuelle Patienten × 4 SDC-Provider auf PC 2 (P = (3, 3, 2, 1): ECG hoch, Vent hoch, SpO2 mittel, "
        "ABP niedrig) gegen das Dashboard auf PC 1; 2-aus-4 und 3-aus-4 offline auf demselben Alarmstrom. "
        "Hier nur die Daten der Läufe, ohne Modell. Fehlerbalken 95 %: Poisson für Zählungen, Clopper–Pearson "
        "für Anteile."
    )
    if not RUNS_ALL:
        st.error(NO_DATA)
    else:
        D_KEYS, D_MODE, _ = run_controls("d", RUNS_ALL, runs_in_sidebar=True)
        if MISSING_RUNS:
            st.info("Noch ohne Daten: " + "; ".join(
                f"{sd.RUNS[k].label_eta} – erwartet `results_{sd.RUNS[k].name}.json` und `messwerte_{sd.RUNS[k].name}.json` "
                f"in `{sd.DATA_DIR}` (aus dem Paket von PC 2 oder mit `python -m analysis.roc_ziel3.netzlauf_auswertung` "
                f"aus den Journalen A/B und decisions.jsonl)" for k in MISSING_RUNS) + ".")
        st.table(pd.DataFrame([{
            "Lauf": r.spec.label_eta, "Art": sd.KIND_TEXT.get(r.spec.kind, r.spec.kind),
            "Dauer (h)": num(r.m.hours, 2), "Betten": r.m.patients,
            f"{ETA} (Szenario)": de(r.spec.tpr),
            "TPR_j gemessen": f"{num(r.m.tpr_mean, 2)} ({num(r.m.tpr_range[0], 2)}–{num(r.m.tpr_range[1], 2)})",
            "Gerätealarme (davon falsch)": f"{fz.thousands(r.n_alarms)} ({fz.thousands(r.n_false_alarms)})",
            "Krisen": r.m.n_crises, "Krisen je Bett und Tag": num(r.m.crises_per_bed_day, 1),
            f"Fehlalarmbelegung je Bett {MFPR}": num(r.m.mfpr, 4),
        } for r in RUNS_ALL.values()]).set_index("Lauf"))
        st.markdown(f"{ETA} = Anteil der Krisen, in denen ein Gerät überhaupt alarmiert (Szenario). "
                    "TPR_j = Anteil der Krisenzeit, in der es alarmiert (gemessen, Mittel der vier Kanäle und "
                    "Spanne). Die Krisenhäufigkeit war in allen Läufen gleich. "
                    "In der Simulation traten rund 31 Krisen je Bett und Tag auf (Literatur: 4,2), damit genug "
                    "Krisen für die Statistik anfallen.")
        st.markdown(f"|M| = Alarmkanäle eines Bettes (hier 4); {MFPR} = mittlere Zahl gleichzeitig fälschlich "
                    "aktiver Kanäle eines Bettes, z. B. 0,104 ≈ 10 % der Zeit. Die Simulation entspricht dem "
                    "Szenario ›sehr hohe Last‹ (Tabelle 5.7).")
        notes = [f"{r.label}: {n}" for r in RUNS_ALL.values() for n in r.notes]
        if notes:
            st.caption("Hinweise der Auswertung – " + "; ".join(notes) + ".")
        if not D_KEYS:
            st.info("Mindestens einen Lauf wählen.")
        else:
            D_SEL = [RUNS_ALL[k] for k in D_KEYS]

            # D1 ─────────────────────────────────────────────────────────────
            st.subheader("D1  Wie stark entlastet der Filter das Personal?")
            figure_sim("D1", D_KEYS)
            st.markdown(
                "Je Lauf die Fehlalarme der Geräte (ohne Filter, Fehlalarme je Bett und Stunde) neben den "
                "Fehleskalationen des adaptiven Filters; über jedem Paar die Senkung. "
                + " ".join(f"{r.label}: {cnt(r.m.false_alarms_per_bed_h)} → {cnt(r.rules['adaptiv'].false_per_bed_h)} "
                           f"(−{de(100 * sd.reduction(r), 1)} %)." for r in D_SEL)
            )

            # D2 ─────────────────────────────────────────────────────────────
            st.subheader("D2  Wie viele Fehlalarme bleiben im Vergleich zu 2-aus-N und 3-aus-N?")
            c1, _ = st.columns([1.4, 2.6])
            d2_y = c1.selectbox("y-Achse", list(fz.D2_Y), format_func=lambda k: fz.D2_Y[k], key="d2_y")
            figure_sim("D2", D_KEYS, d2_y)
            st.markdown(
                "Fehleskalationen je Regel und Lauf, als Anteil der Fehlalarme der Geräte oder je Bett und Stunde; "
                "eine Fehleskalation schneidet keine Krise mit 10 s Rand. "
                + " ".join(f"{r.label}: " + ", ".join(
                    f"{mz.RULE_NAMES[rule]} {num(sd.remaining_pct(r, rule)[0], 1)} %" for rule in sd.SIM_RULES) + "."
                    for r in D_SEL)
            )

            # D3 ─────────────────────────────────────────────────────────────
            st.subheader("D3  Welcher Anteil der Krisen wird erkannt?")
            figure_sim("D3", D_KEYS, D_MODE)
            st.markdown(
                f"Anteil erkannter Krisen ({sd.MODES[D_MODE]}) über {ETA}: die Linie je Regel verbindet 6 h (0,6), "
                f"A (0,7), B (0,8) und 12 h (0,9); 1 h als hohler Kreis bei 0,9; C (lange Krisen, Raute) und D (Idealfall, "
                f"Quadrat) als eigene Marker bei 0,9, nicht auf der Linie; Punkte eines {ETA} zur Lesbarkeit leicht versetzt. "
                "**Je weniger Geräte eine Krise anzeigen, desto deutlicher trennen sich die Regeln.**"
            )

            # D4 ─────────────────────────────────────────────────────────────
            st.subheader("D4  Was geht bei jeder Regel schief? – Bilanz der Läufe")
            figure_sim("D4", D_KEYS)
            st.markdown(
                "Fehleskalationen entstehen, wenn sich Fehlalarme mehrerer Geräte eines Patienten zeitlich "
                "überlappen; die Regeln unterscheiden sich darin, wie viele solcher Überlappungen sie eskalieren. "
                "Unten je Regel ein Balken der Länge K = Krisen: erkannt (Rot beginnt vor Krisenende), Rot erst "
                "nach Krisenende (bis Ende + 10 s), verpasst."
            )
            bil = [{"Lauf": r.label, "Regel": mz.RULE_NAMES[rule], **sd.bilanz_row(r, rule)}
                   for r in D_SEL for rule in sd.SIM_RULES if rule in r.rules]
            st.table(pd.DataFrame([{
                "Lauf": b["Lauf"], "Regel": b["Regel"], "Gerätealarme": fz.thousands(b["Gerätealarme"]),
                "außerhalb von Krisen": fz.thousands(b["davon falsch"]), "in Krisen": fz.thousands(b["in Krisen"]),
                "Krisen": b["Krisen"],
                "Eskalationen gesamt": fz.thousands(b["Eskalationen gesamt"]),
                "davon falsch": fz.thousands(b["davon falsch_esk"]), "erkannt": b["erkannt"],
                "nach Ende": b["nach Ende"], "verpasst": b["verpasst"],
            } for b in bil]).set_index(["Lauf", "Regel"]))

            # Export ─────────────────────────────────────────────────────────
            st.subheader("Export")
            st.markdown(f"Schreibt D1–D4 mit den gewählten Läufen und der Definition „{D_MODE}“ als PNG (300 dpi) und "
                        f"SVG nach `{fz.FIG_DIR}` mit dem Namenszusatz `_v2` (die alten Dateien bleiben). Skript: "
                        f"`python analysis/belastungsmodell_app/export_ziel3.py --nur experimente --suffix _v2`.")
            if st.button("D1–D4 exportieren"):
                with st.spinner("Abbildungen werden erzeugt …"):
                    written = fz.export_experimente(RUNS_ALL, mode=D_MODE, d2_y=d2_y, keys=D_KEYS, suffix="_v2")
                st.success(f"{len(written)} Dateien geschrieben:")
                st.code("\n".join(str(path) for path in written))

# ═════════════════════════════════════════════════════════════════════════════
with tab_vgl:
    st.subheader("Vergleich Modell – Simulation")
    st.markdown(
        "**Linie = Modell** mit den Parametern des 12-h-Laufs (größte Stichprobe): FPR = Mittel der gemessenen FPR_j, "
        "P(C) gemessen, d_false und d_true gemessen, d0 = 15 s, T = 10 s, δ_min = 0,5, α = 0,5, "
        f"TPR_j = {ETA} aus dem Szenario, P = (3, 3, 2, 1), c_art = Anteil der Fehlalarmzeit in Episoden mit allen "
        "vier Kanälen (die Definition im Modell; die Tabelle zeigt zusätzlich den Wert für ≥ 2 Kanäle). "
        "**Punkt mit Fehlerbalken = Simulation** (12 h und 6 h; 1 h nur in den Tabellen; 95 %: Poisson für "
        "Zählungen, Clopper–Pearson für Anteile). Fehleskalationen des Modells als Episoden gezählt (Übergänge in "
        "eine eskalierende Teilmenge); für adaptiv obere Grenze ohne Wirkung des Fensters T (Monte-Carlo: "
        "tatsächlich etwa halb so viele)."
    )
    if not RUNS_ALL:
        st.error(NO_DATA)
    else:
        V_KEYS, V_MODE, V4_RUN = run_controls("v", RUNS_ALL, with_v4=True)
        if not V_KEYS:
            st.info("Mindestens einen Lauf wählen.")
        else:
            V_SEL = [RUNS_ALL[k] for k in V_KEYS]
            rows = sd.compare(V_SEL, V_MODE)
            st.table(pd.DataFrame([{
                "Lauf": RUNS_ALL[row.run].spec.label_eta,
                "Regel": mz.RULE_NAMES[row.rule],
                "Fehleskalationen je Bett·h – Modell (c_art alle 4 … ≥ 2)": f"{num(row.model_false[0])} … {num(row.model_false[1])}",
                "Fehleskalationen je Bett·h – Simulation [95 %]": f"{num(row.sim_false)} {ci_text(*row.sim_false_ci)}",
                "erkannte Krisen – Modell": pct(row.model_caught, 1),
                f"erkannte Krisen – Simulation ({V_MODE}) [95 %]":
                    f"{pct(row.sim_caught, 1)} ({row.sim_caught_n}/{row.n_crises}) "
                    f"{ci_text(100 * row.sim_caught_ci[0], 100 * row.sim_caught_ci[1], 1, ' %')}",
                "liegt im Bereich? (Fehleskalationen / Krisen)": f"{ja(row.false_ok)} / {ja(row.caught_ok)}",
            } for row in rows]).set_index(["Lauf", "Regel"]))
            st.caption("„liegt im Bereich“: Fehleskalationen – das 95-%-Intervall der Simulation schneidet den "
                       "Modellbereich über c_art (alle 4 … ≥ 2); erkannte Krisen – der Modellwert (Kaltstart, d0 = 15 s) "
                       "liegt im 95-%-Intervall der Simulation.")

            FIG_KEYS = tuple(k for k in V_KEYS if k not in fz.V_HIDDEN)
            main = RUNS_ALL[sd.MAIN_RUN] if sd.MAIN_RUN in FIG_KEYS else (RUNS_ALL[FIG_KEYS[0]] if FIG_KEYS else V_SEL[0])
            mm = main.m
            r_main = {row.rule: row for row in rows if row.run == main.key}
            model_text = (f"Modell mit den Parametern des Laufs {main.label}: {MFPR} = {num(mm.mfpr, 4)}, "
                          f"P(C) = {num(mm.P_C, 4)}, d_false = {num(mm.d_false, 1)} s, d_true = {num(mm.d_true, 1)} s, "
                          f"d0 = 15 s, TPR_j = {ETA} = {de(main.spec.tpr)}, c_art = {num(mm.c_art_a, 3)} (alle 4 Kanäle)")
            if not FIG_KEYS:
                st.info("Für die Abbildungen V1–V4 den Lauf 12 h oder 6 h wählen (1 h erscheint nur in der Tabelle).")
            else:
                # V1 ─────────────────────────────────────────────────────────
                st.subheader("V1  Wie stark entlastet der Filter das Personal?")
                figure_sim("V1", FIG_KEYS)
                ad = r_main.get("adaptiv")
                st.markdown(
                    f"{model_text}. Punkte = Simulation bei c_art (alle 4 Kanäle) des jeweiligen Laufs; 1 h nur in der "
                    f"Tabelle. "
                    + (f"**Befund:** ohne Filter liegt das Modell auf den Punkten, weil es auf die gemessenen Fehlalarme "
                       f"kalibriert ist; adaptiv: Simulation {num(ad.sim_false)} je Bett·h gegen Modell "
                       f"{num(ad.model_false[0])} bei c_art (alle 4) – das Modell unterschätzt die Fehleskalationen "
                       f"etwa um den Faktor {num(ad.sim_false / ad.model_false[0], 1)}, weil es teilweise gemeinsame "
                       f"Artefakte (2–3 Geräte zugleich) nicht kennt." if ad else "")
                )

                # V2 ─────────────────────────────────────────────────────────
                st.subheader("V2  Wie viele Fehlalarme bleiben im Vergleich zu 2-aus-N und 3-aus-N?")
                c1, _ = st.columns([1.4, 2.6])
                v2_y = c1.selectbox("y-Achse", list(fz.V2_Y), format_func=lambda k: fz.V2_Y[k], key="v2_y")
                figure_sim("V2", FIG_KEYS, v2_y)
                st.markdown(
                    f"Ausschnitt um die Messpunkte ({MFPR} = {de(fz.V2_X_RANGE[0])} … {de(fz.V2_X_RANGE[1])}): "
                    f"je Regel eine Modelllinie bei c_art (alle 4 Kanäle) mit den Parametern des Laufs {main.label}, "
                    f"Punkte = Simulation beim gemessenen {MFPR} (je Regel ±3 % versetzt). "
                    f"**Befund:** die Reihenfolge 2-aus-N > adaptiv > 3-aus-N stimmt mit dem Modell überein; absolut "
                    f"liegen die Punkte über den Linien (teilweise gemeinsame Artefakte)."
                )

                # V3 ─────────────────────────────────────────────────────────
                st.subheader("V3  Welcher Anteil der Krisen wird erkannt?")
                c1, _ = st.columns([1.4, 2.6])
                v3_view = c1.radio("Ansicht", list(fz.V3_VIEWS), format_func=lambda k: fz.V3_VIEWS[k], key="v3_view",
                                   horizontal=True)
                figure_sim("V3", FIG_KEYS, V_MODE, v3_view)
                bad = [row for row in rows if not row.caught_ok and row.run in FIG_KEYS]
                n_fig = sum(1 for row in rows if row.run in FIG_KEYS)
                bad_text = "; ".join(
                    f"{mz.RULE_NAMES[row.rule]} {RUNS_ALL[row.run].label}: Modell {pct(row.model_caught, 1)} gegen "
                    f"{pct(row.sim_caught, 1)} {ci_text(100 * row.sim_caught_ci[0], 100 * row.sim_caught_ci[1], 1, ' %')}"
                    for row in bad)
                st.markdown(
                    f"Anteil erkannter Krisen ({sd.MODES[V_MODE]}) über {ETA}: drei Linien = Modell (2-aus-N, 3-aus-N, "
                    f"adaptiv) mit Kaltstart, d0 = 15 s und den Parametern des Laufs {main.label}; im Modell gilt "
                    f"{ETA} = TPR_j. "
                    + ("Zoom: links {0} = 0,6 (Lauf 6 h), rechts {0} = 0,9 (Lauf 12 h), Punkte je Regel um ±0,01 "
                       "versetzt. ".format(ETA) if v3_view == "zoom" else "Übersicht 0,5 … 1. ")
                    + (f"**Befund:** bei {n_fig - len(bad)} von {n_fig} Punkten liegt das Modell im 95-%-Intervall; "
                       f"außerhalb: {bad_text}." if bad else
                       f"**Befund:** bei allen {n_fig} Punkten liegt das Modell im 95-%-Intervall.")
                )

                # V4 ─────────────────────────────────────────────────────────
                st.subheader("V4  Was geht bei jeder Regel schief?")
                c1, _ = st.columns([1.4, 2.6])
                v4_y = c1.selectbox("y-Achse links", list(fz.V4_Y), format_func=lambda k: fz.V4_Y[k], key="v4_y")
                v4 = RUNS_ALL[V4_RUN]
                figure_sim("V4", (V4_RUN,), v4_y)
                v4_vals = {rule: sd.v4_values(v4, rule) for rule in sd.SIM_RULES if rule in v4.rules}
                v4_rel = sd.v4_relative(v4)
                rel_text = "; ".join(f"{mz.RULE_NAMES[rule]} Modell {num(v4_rel[rule].model_pct, 0)} % / Simulation "
                                     f"{num(v4_rel[rule].sim_pct, 0)} %" for rule in v4_rel)
                abs_text = "; ".join(f"{mz.RULE_NAMES[rule]} {num(v.sim_false)} gegen {num(v.model_false_a)}"
                                     for rule, v in v4_vals.items())
                missed_text = "; ".join(
                    f"{mz.RULE_NAMES[rule]} {fz.v4_split_label(v.sim_missed_1000, v.sim_after_end_1000)} gegen Modell "
                    f"{fz.bar_label(v.model_missed_1000)}" for rule, v in v4_vals.items())
                factor_text = ", ".join(f"{mz.RULE_NAMES[rule]} {num(v.sim_false / v.model_false_a, 1)}"
                                        for rule, v in v4_vals.items())
                st.markdown(
                    "Modell mit c_art = Anteil der Fehlalarmzeit, in der alle Kanäle zugleich alarmieren. Teilweise "
                    "gemeinsame Artefakte (Bewegung trifft 2–3 Geräte) kennt das Modell nicht; absolut unterschätzt es "
                    f"die Fehleskalationen deshalb (Faktor Simulation/Modell: {factor_text}), das Verhältnis der Regeln "
                    "gibt es wieder. Fehleskalationen des Modells als Episoden gezählt; für adaptiv obere Grenze ohne "
                    "Wirkung des Fensters T. "
                    f"Lauf {v4.spec.label_eta}, |M| = 4: Säule = Simulation [95 %], ◆ = Modell. Links Fehleskalationen "
                    f"relativ zu 2-aus-N ({rel_text}); je Bett und Stunde Simulation gegen Modell: {abs_text}. "
                    f"Rechts verpasste Krisen je 1000 Krisen = verpasst (auch mild) + Rot erst nach Krisenende "
                    f"(schraffiert), Fehlerbalken für streng: {missed_text}."
                )

                # Export ─────────────────────────────────────────────────────
                st.subheader("Export")
                st.markdown(f"Schreibt V1–V4 mit den gewählten Läufen, der Definition „{V_MODE}“, dem V4-Lauf "
                            f"{v4.label} und V3 als Zoom als PNG (300 dpi) und SVG nach `{fz.FIG_DIR}`. Skript: "
                            f"`python analysis/belastungsmodell_app/export_ziel3.py --nur vergleich [--mit-parametern]`.")
                if st.button("V1–V4 exportieren"):
                    with st.spinner("Abbildungen werden erzeugt …"):
                        written = fz.export_vergleich(RUNS_ALL, mode=V_MODE, v2_y=v2_y, v4_run=V4_RUN, keys=V_KEYS,
                                                      v4_y=v4_y)
                    st.success(f"{len(written)} Dateien geschrieben:")
                    st.code("\n".join(str(path) for path in written))

# ═════════════════════════════════════════════════════════════════════════════
# Reiter 4: ROC bei Variation der Gerätegrenzwerte (analysis/roc_ziel3)
# ═════════════════════════════════════════════════════════════════════════════

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from analysis.roc_ziel3 import figures_roc as fr  # noqa: E402
from analysis.roc_ziel3 import run_roc as rr  # noqa: E402

ROC_RULES = fr.RULES
ROC_CMD = "python -m analysis.roc_ziel3.run_roc"
X_VARIANTS = {"ii": "(ii) nur falsche Episoden", "i": "(i) alle Rot-Zeit außerhalb von Krisen"}
X_MODES = {"beide": "linear | log", "log": "logarithmisch", "linear": "linear"}
ROC_SETS = {"0,9": (str(rr.ROC_DIR), ""), "0,7": (str(rr.ROC_DIR_P07), "_p07")}     # η → (Verzeichnis, Namenszusatz)


@st.cache_data(show_spinner=False)
def _roc_data(roc_dir: str, csv_mtime: float, json_mtime: float):
    """Zeilen von roc_alle.csv und Tabellen aus roc_tabellen.json eines ROC-Verzeichnisses (Zeitstempel nur für den Cache)."""
    csv_path = Path(roc_dir) / rr.CSV_OUT
    tab_path = Path(roc_dir) / rr.TAB_JSON
    if not csv_path.exists() or not tab_path.exists():
        return None, None
    return rr.load_rows(csv_path), json.loads(tab_path.read_text(encoding="utf-8"))


@st.cache_data(show_spinner=False, max_entries=80)
def _png_roc(name: str, extra: tuple, roc_dir: str, csv_mtime: float, json_mtime: float) -> bytes:
    rows, tb = _roc_data(roc_dir, csv_mtime, json_mtime)
    if name == "R2_vergleich":
        other = ROC_SETS["0,7"][0] if roc_dir == ROC_SETS["0,9"][0] else ROC_SETS["0,9"][0]
        rows_b, tb_b = _roc_data(other, *_roc_mtimes(other))
        a_first = roc_dir == ROC_SETS["0,9"][0]
        ra, ta, rb, tbb = (rows, tb, rows_b, tb_b) if a_first else (rows_b, tb_b, rows, tb)
        return _to_png(fr.fig_R2_vergleich(ra, ta, rb, tbb, ("η = 0,9", "η = 0,7"), *extra))
    builders = {
        "R1a": lambda: fr.fig_R1a(rows, tb, *extra),
        "R1b": lambda: fr.fig_R1b(rows, tb, *extra),
        "R1b_stueck": lambda: fr.fig_R1b_stueck(rows, tb, *extra),
        "R2": lambda: fr.fig_R2(rows, tb, *extra),
        "R3": lambda: fr.fig_R3(rows, tb, *extra),
        "R4a": lambda: fr.fig_R4a(rows, tb, *extra),
        "R4b": lambda: fr.fig_R4b(rows, *extra),
        "R5": lambda: fr.fig_R5(rows, *extra),
    }
    return _to_png(builders[name]())


def _roc_mtimes(roc_dir: str) -> tuple:
    out = []
    for p in (Path(roc_dir) / rr.CSV_OUT, Path(roc_dir) / rr.TAB_JSON):
        out.append(p.stat().st_mtime if p.exists() else 0.0)
    return tuple(out)


def _fmt_cell(v, digits=3, pct=False, flag: str = "") -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "–"
    return (f"{100 * v:.{digits}f} %" if pct else f"{v:.{digits}f}").replace(".", ",") + flag


with tab_roc:
    st.subheader("ROC bei Variation der Gerätegrenzwerte")
    st.markdown(
        "Die Fusionsregel ist fest, die Grenzwerte der vier Geräte werden gemeinsam mit dem Faktor h skaliert "
        "(h = 1,0 = Netzläufe; 15 Werte 0,05 … 2,2, identische Zufallszahlen). PC 2 liefert je h die Journale A "
        "(Krisen) und B (Gerätealarme); **adaptiv** wird mit dem unveränderten Filterkern des Dashboards offline "
        "nachgerechnet (`analysis/roc_ziel3/dashcore.py`), auch für die Fenster T = 10, 20, 30 s (TTL-Cache 10 s, "
        "Gnadenfrist 10 s und Gitter 1 s sind im Dashboard feste Konstanten und bleiben). "
        "**Achsen nach Zeit:** y = Anteil der Krisenzeit mit Eskalation (Rot), x = Anteil der krisenfreien Zeit mit "
        "Eskalation; stabil = außerhalb [Beginn − 10 s, Ende + 10 s]. x-Variante (ii) zählt nur falsche Episoden "
        "(berühren keine Krise ± 10 s), Variante (i) alle Rot-Zeit außerhalb von Krisen (enthält Hälse von Rot, das in "
        "einer Krise begann). k-aus-4 und adaptiv halten die Eskalation, bis alle Alarme des Bettes aus sind. "
        "**Formel (a)**: P = Σ_{i≥k} C(4,i) p^i (1−p)^{4−i} mit p = TPR̄ bzw. FPR̄ der Geräte nach Zeit; **Modell (b)**: "
        "`model_ziel3` mit den Geräteraten des h; **Modell E1+E2**: versetzter Einstieg (gemessene η_j und u_j) und "
        "Episoden nach Größe m (gemessene Anteile, Raten, Dauern)."
    )
    roc_eta = st.radio("Szenario η (Anzeigewahrscheinlichkeit je Gerät)", list(ROC_SETS), horizontal=True, key="roc_eta",
                       format_func=lambda k: f"η = {k}" + (" (p_cross 0,9, Netzlauf 12 h)" if k == "0,9" else " (p_cross 0,7, Netzlauf A 4 h)"))
    ROC_DIR_STR, ROC_SUFFIX = ROC_SETS[roc_eta]
    _mt = _roc_mtimes(ROC_DIR_STR)
    ROC_ROWS, ROC_TB = _roc_data(ROC_DIR_STR, *_mt)
    BOTH = all(_roc_data(d, *_roc_mtimes(d))[0] is not None for d, _ in ROC_SETS.values())
    if ROC_ROWS is None:
        st.info(f"Daten fehlen (`{Path(ROC_DIR_STR) / rr.CSV_OUT}`). Erzeugen mit `{ROC_CMD}` (für η = 0,7 mit "
                f"`--roc-dir … ziel3_roc_p07 --pc1-dir … ziel3_4h_p07/pc1 --netz netz_ziel3_4h_p07 --suffix _p07 --eta 0,7 --T 10`).")
    else:
        pr = ROC_TB.get("pruefung") or {}
        if pr:
            z = pr["zuordnung"]
            st.caption(
                f"Prüfung durch Wiederholung (Netzlauf ziel3_12h): Dashboard {z['n_real']} Eskalationen, Replay "
                f"{z['n_replay']} ({100 * z['rel_count_diff']:+.2f} %), zugeordnet {z['matched']}, Beginn Replay − "
                f"Dashboard Median {z['diff_median']:+.3f} s (p10 {z['diff_p10']:+.2f} s, p90 {z['diff_p90']:+.2f} s)."
            )
        ROC_TS = fr.available_T(ROC_ROWS)
        c1, c2, c3 = st.columns([1.3, 2.0, 1.7])
        roc_T = c1.radio("Fenster T (adaptiv)", ROC_TS, horizontal=True, key="roc_T", format_func=lambda t: f"{t:g} s")
        roc_var = c2.radio("x-Achse: Fehleskalationszeit", list(X_VARIANTS), horizontal=True, key="roc_var",
                           format_func=lambda k: X_VARIANTS[k])
        roc_x = c3.radio("x-Achse: Skala", list(X_MODES), horizontal=True, key="roc_x", format_func=lambda k: X_MODES[k])
        auc_t = ROC_TB.get("auc", {})

        def _pauc(name: str, T: float = fr.T_MAIN) -> str:
            key = name if (name != "adaptiv" or abs(T - fr.T_MAIN) < 1e-9) else f"adaptiv T={T:g}"
            return _fmt_cell(auc_t.get(key, {}).get(f"pauc_003_{roc_var}"), 2)

        col_a, col_b = st.columns(2)
        with col_a:
            st.subheader("R1a  ROC bei Variation der Gerätegrenzwerte (nur Messungen)")
            st.image(_png_roc("R1a", (roc_var, roc_x), ROC_DIR_STR, *_mt), width="stretch")
            st.caption(f"Grau: Einzelgeräte (Anteil der Zeit mit Gerätealarm); dick: 2-aus-4, 3-aus-4, adaptiv (T = 10 s); dünn: 1- und "
                       f"4-aus-4; Rhombus h = 1,0; Stern Netzlauf. "
                       f"pAUC (x ≤ 0,03, normiert, Variante {roc_var}): " + ", ".join(f"{n} {_pauc(n)}" for n in ROC_RULES) + ". "
                       f"{fr.FOOTNOTE_R1A} {fr.FOOTNOTE_ADAPTIV}")
        with col_b:
            st.subheader("R2  Nach Stück: erkannte Krisen und Fehleskalationen")
            st.image(_png_roc("R2", (roc_x, roc_T), ROC_DIR_STR, *_mt), width="stretch")
            st.caption("Erkannt = Rot berührt die Krise bis Ende + 10 s; Fehleskalation = Rot-Episode, die keine Krise ± 10 s "
                       "berührt. Rechts Ausschnitt x 1 … 6, y 88 … 100 % (Rahmen links). Grau gestrichelt: beste Mischung fester "
                       "k-aus-4-Regeln (obere konvexe Hülle aller k und h, mit Ursprung). Strichpunktiert: Modell E1+E2 – erkannte "
                       "Krisen mild aus E1 (gemessene η_j, u_j und Nachlauf), Fehleskalationen = Episodenteil von E2 ohne das "
                       "Zufallsglied G.7.")
        if BOTH:
            st.subheader("R2 Vergleich  η = 0,9 | η = 0,7 (gleiche Achsen)")
            st.image(_png_roc("R2_vergleich", (), ROC_DIR_STR, *_mt), width="stretch")
            st.caption("Links η = 0,9 (Netzlauf 12 h), rechts η = 0,7 (Szenario des Netzlaufs A); je Panel Messung, Modell E1+E2 "
                       "und die Hülle der festen Regeln des jeweiligen Datensatzes.")

        st.subheader("R1b  Theorie gegen Messung")
        st.image(_png_roc("R1b", (roc_var, roc_T, roc_x == "log"), ROC_DIR_STR, *_mt), width="stretch")
        st.caption("Durchgezogen (Regelfarbe): Messung; strichpunktiert: Modell E1+E2; dünn grau gestrichelt: "
                   "Binomialformel (unabhängige Geräte), nicht für adaptiv; Rhombus h = 1,0. " + fr.FOOTNOTE_R1B + " "
                   + fr.FOOTNOTE_ADAPTIV)
        st.image(_png_roc("R1b_stueck", (roc_T,), ROC_DIR_STR, *_mt), width="stretch")
        st.caption("Nach Stück: x = Fehleskalationen je Bett und Stunde (0 … 10), y = erkannte Krisen (mild). "
                   + fr.FOOTNOTE_R1B_STUECK)

        st.subheader("R3  Übertragung Gerät → System")
        st.image(_png_roc("R3", (roc_var, roc_T), ROC_DIR_STR, *_mt), width="stretch")
        sl = ROC_TB.get("steigungen", {})
        sl_text = "; ".join(f"{n} {_fmt_cell(sl[n]['niedrig'], 2)}" for n in ROC_RULES if n in sl)
        st.caption(f"Steigung d ln x / d ln FPR̄ bei FPR̄ ≤ 0,03 (kleinste Quadrate): {sl_text}; Formel (a) ergibt "
                   f"für 2-aus-4 {_fmt_cell(sl.get('2-aus-4 [Formel (a)]', {}).get('niedrig'), 2)} und für 3-aus-4 "
                   f"{_fmt_cell(sl.get('3-aus-4 [Formel (a)]', {}).get('niedrig'), 2)} (unabhängiges Rauschen, (4.10)).")

        ft = ROC_TB.get("fenster_T", {})
        if len(ROC_TS) > 1:
            st.subheader("R4a / R4b  Fenster T des adaptiven Filters")
            st.image(_png_roc("R4a", (roc_var, tuple(ROC_TS), roc_x), ROC_DIR_STR, *_mt), width="stretch")
            st.image(_png_roc("R4b", (tuple(ROC_TS), roc_x), ROC_DIR_STR, *_mt), width="stretch")
            st.caption("Familie adaptiv nach T (Blautöne) neben 2-aus-4 und 3-aus-4; Rhombus h = 1,0. "
                       "Tabelle „Fenster T“ unten: Arbeitspunkt je T, pAUC und Vorsprung gegenüber k-aus-N.")

        st.subheader(f"R5  Fehlerbilanz über die Gerätegrenzwerte (T = {roc_T:g} s)")
        st.image(_png_roc("R5", (roc_T,), ROC_DIR_STR, *_mt), width="stretch")
        st.caption("Oben: Fehleskalationen je Bett und Stunde (log). Unten: nicht erkannte Krisen (durchgezogen) und "
                   "verspätet erkannte Krisen (mild, aber nicht streng; gestrichelt). Senkrechte Linie h = 1,0.")

        st.subheader("Tabellen")
        if ft:
            st.markdown("**Fenster T: Arbeitspunkt h = 1,0 (adaptiv je T neben 2-/3-aus-4)**")
            wp = ROC_TB.get("arbeitspunkt_h1", {})
            order = ["2-aus-4", "3-aus-4"] + [f"adaptiv T={T:g}" for T in ROC_TS]
            st.table(pd.DataFrame([{
                "Regel": n, "y (Zeit)": _fmt_cell(wp[n]["tpr"]), "x(i)": _fmt_cell(wp[n]["fpr"], 5),
                "x(ii)": _fmt_cell(wp[n]["fpr_ii"], 5), "erkannt mild": _fmt_cell(wp[n]["erkannt"], 1, pct=True),
                "streng": _fmt_cell(wp[n]["erkannt_streng"], 1, pct=True),
                "Fehleskalationen je Bett·h": _fmt_cell(wp[n]["falsch_je_bett_h"], 2),
                "Verzögerung Median / p90 [s]": f"{_fmt_cell(wp[n]['verz_median_s'], 1)} / {_fmt_cell(wp[n]['verz_p90_s'], 1)}",
                "Hals-Anteil an x(i)": _fmt_cell(wp[n]["hvl_anteil"], 1, pct=True),
                "pAUC(ii)": _fmt_cell((ft.get(n.split("=")[-1], {}).get("pauc_ii") if n.startswith("adaptiv")
                                       else auc_t.get(n, {}).get("pauc_003_ii")), 3),
            } for n in order if n in wp]).set_index("Regel"))
            st.markdown("**Vorsprung adaptiv gegenüber k-aus-N** (nach Stück: Differenz der erkannten Krisen in Prozentpunkten bei "
                        "gleicher Zahl Fehleskalationen je Bett·h; nach Zeit: Differenz y bei gleichem x(ii); positiv = adaptiv besser)")
            rows_v = []
            for T, v in ft.items():
                for fg, d in v["vorsprung_stueck"].items():
                    rows_v.append({"T [s]": T, "Vergleich": f"{fg} Fehleskalationen je Bett·h",
                                   "gegen 2-aus-4": _fmt_cell(100 * d["2-aus-4"], 1) + " pp", "gegen 3-aus-4": _fmt_cell(100 * d["3-aus-4"], 1) + " pp"})
                for fg, d in v["vorsprung_zeit"].items():
                    rows_v.append({"T [s]": T, "Vergleich": f"x(ii) = {fg.replace('.', ',')}",
                                   "gegen 2-aus-4": _fmt_cell(d["2-aus-4"]), "gegen 3-aus-4": _fmt_cell(d["3-aus-4"])})
            st.table(pd.DataFrame(rows_v).set_index(["T [s]", "Vergleich"]))
        hu = ROC_TB.get("huelle")
        if hu:
            st.markdown("**Obere konvexe Hülle der festen Regeln k-aus-4 und Abstand der adaptiven Punkte** (Δ = y_adaptiv − y_Hülle "
                        "bei gleichem x; Δ_Sehne gegen die Sehne 2-aus-4 ↔ 3-aus-4 desselben h; 95-%-Intervalle: Bootstrap über die "
                        "20 Patienten, 2000 Züge, bei h = 0,8 / 1,0 / 1,2; * = x außerhalb der Sehne)")
            for view, v in hu.items():
                st.markdown(("*nach Stück* (x = Fehleskalationen je Bett·h, y = erkannte Krisen mild)" if view == "stueck"
                             else "*nach Zeit* (x = Fehleskalationszeit (ii), y = Anteil der Krisenzeit mit Rot)"))
                xd = 2 if view == "stueck" else 5
                st.table(pd.DataFrame([{
                    "h": f"{r['h']:g}", "x": _fmt_cell(r["x"], xd), "y adaptiv": _fmt_cell(r["y_adaptiv"], 1, pct=True),
                    "y Hülle": _fmt_cell(r["y_huelle"], 1, pct=True), "Δ [pp]": _fmt_cell(r["delta_pp"], 1),
                    "95 %": (f"[{_fmt_cell(r['delta_ci'][0], 1)}; {_fmt_cell(r['delta_ci'][1], 1)}]" if r.get("delta_ci") else "–"),
                    "y Sehne": _fmt_cell(r["y_sehne"], 1, pct=True, flag="" if r["sehne_innerhalb"] else "*"),
                    "Δ_Sehne [pp]": _fmt_cell(r["delta_sehne_pp"], 1),
                    "95 % ": (f"[{_fmt_cell(r['delta_sehne_ci'][0], 1)}; {_fmt_cell(r['delta_sehne_ci'][1], 1)}]" if r.get("delta_sehne_ci") else "–"),
                } for r in v["zeilen"]]).set_index("h"))
                st.caption(v["satz"])
        lat = ROC_TB.get("latenz")
        if lat:
            st.markdown("**Latenz bei h = 1,0** (Median / p90 in s; Hauptdefinition: ab dem ersten Überschreiten einer Gerätegrenze; "
                        "zum Vergleich ab Krisenbeginn)")
            st.table(pd.DataFrame([{"Regel": n, "ab Überschreiten": f"{_fmt_cell(v['kreuz'][0], 1)} / {_fmt_cell(v['kreuz'][1], 1)}",
                                    "ab Krisenbeginn": f"{_fmt_cell(v['start'][0], 1)} / {_fmt_cell(v['start'][1], 1)}"}
                                   for n, v in lat.items()]).set_index("Regel"))
        ein = ROC_TB.get("eingang")
        if ein:
            st.markdown("**Eingangsprüfung** (h = 1,0 offline gegen den Netzlauf; 95-%-Intervalle des Netzlaufs)")
            rows_e = [{"Größe": f"Fehlalarme/h {s}", "offline": _fmt_cell(v["offline"], 1), "Netzlauf": _fmt_cell(v["netz"], 1),
                       "95 % Netzlauf": f"[{_fmt_cell(v['netz_ci'][0], 1)}; {_fmt_cell(v['netz_ci'][1], 1)}]", "innerhalb": "ja" if v["ok"] else "nein"}
                      for s, v in ein["quellen"].items()]
            rows_e += [{"Größe": f"η {s}", "offline": _fmt_cell(v["eta_offline"]), "Netzlauf": _fmt_cell(v["eta_netz"]),
                        "95 % Netzlauf": f"[{_fmt_cell(v['eta_ci'][0])}; {_fmt_cell(v['eta_ci'][1])}]", "innerhalb": "ja" if v["eta_ok"] else "nein"}
                       for s, v in ein["quellen"].items()]
            k = ein["krisen"]
            rows_e.append({"Größe": "Krisen je Bett·h", "offline": _fmt_cell(k["offline"]), "Netzlauf": _fmt_cell(k["netz"]),
                           "95 % Netzlauf": f"[{_fmt_cell(k['netz_ci'][0])}; {_fmt_cell(k['netz_ci'][1])}]", "innerhalb": "ja" if k["ok"] else "nein"})
            st.table(pd.DataFrame(rows_e).set_index("Größe"))
        st.markdown("**AUC (voll) und partieller AUC (x ≤ 0,03, normiert auf 0,03), Variante (ii) | (i)**")
        st.table(pd.DataFrame([{"Kurve": k, "AUC (ii)": _fmt_cell(v.get("auc_ii")), "pAUC (ii)": _fmt_cell(v.get("pauc_003_ii")),
                                "AUC (i)": _fmt_cell(v.get("auc_i")), "pAUC (i)": _fmt_cell(v.get("pauc_003_i"))}
                               for k, v in auc_t.items()]).set_index("Kurve"))
        names_main = list(ROC_RULES[:-1]) + [f"adaptiv T={T:g}" for T in ROC_TS]
        st.markdown("**Welche Regel ist bei vorgegebenem x(ii) am besten?** (y nach Zeit, linear interpoliert; "
                    "* = unterhalb des kleinsten Messpunkts, Anker (0, 0))")
        tf = ROC_TB.get("tpr_bei_fpr", {})
        st.table(pd.DataFrame([{"x(ii)": f.replace(".", ","),
                                **{n: _fmt_cell(v["werte"].get(n, {}).get("tpr"), 3,
                                                flag="" if v["werte"].get(n, {}).get("innerhalb", True) else "*") for n in names_main},
                                "beste (T = 10)": v["beste"] or "–"} for f, v in tf.items()]).set_index("x(ii)"))
        st.markdown("**Nach Stück: erkannte Krisen bei vorgegebenen Fehleskalationen je Bett·h** (Interpolation in log x)")
        ef = ROC_TB.get("erkannt_bei_falsch", {})
        st.table(pd.DataFrame([{"je Bett·h": f.replace(".", ","),
                                **{n: _fmt_cell(v["werte"].get(n, {}).get("erkannt"), 1, pct=True) for n in names_main},
                                "beste (T = 10)": v["beste"] or "–"} for f, v in ef.items()]).set_index("je Bett·h"))
        st.markdown("**Arbeitspunkt h = 1,0** (wie in den Netzläufen)")
        wp = ROC_TB.get("arbeitspunkt_h1", {})
        st.table(pd.DataFrame([{"Regel": n, "y (Zeit)": _fmt_cell(v["tpr"]), "x(i)": _fmt_cell(v["fpr"], 5), "x(ii)": _fmt_cell(v["fpr_ii"], 5),
                                "erkannt mild": _fmt_cell(v["erkannt"], 1, pct=True), "streng": _fmt_cell(v["erkannt_streng"], 1, pct=True),
                                "Fehleskalationen je Bett·h": _fmt_cell(v["falsch_je_bett_h"], 2),
                                "Ereignisse": int(v["n_ereignisse"] or 0), "davon falsch": int(v["n_falsch"] or 0)}
                               for n, v in wp.items()]).set_index("Regel"))
        pc = ROC_TB.get("profil_check")
        if pc:
            st.markdown("**Prüfung der Profilkalibrierung (h = 1,0, T = 10 s)**: Kanalprofile aus den Messwerten statt 0,9 / 0,00313")
            st.table(pd.DataFrame([{
                "Profil": n, "w_j (ECG, Vent, SpO2, ABP)": ", ".join(_fmt_cell(v["w"][m], 2) for m in ("Sim ECG", "Sim Vent", "Sim SpO2", "Sim ABP") if m in v["w"]),
                "Rot": v["n_rot"], "erkannt mild / streng": f"{_fmt_cell(v['raten']['erkannt'], 1, pct=True)} / {_fmt_cell(v['raten']['erkannt_streng'], 1, pct=True)}",
                "Fehleskalationen je Bett·h": _fmt_cell(v["raten"]["falsch_je_bett_h"], 2), "y (Zeit)": _fmt_cell(v["raten"]["tpr"]),
                "x(ii)": _fmt_cell(v["raten"]["fpr_ii"], 5)} for n, v in pc.items()]).set_index("Profil"))
        e1 = ROC_TB.get("e1", {}).get("1")
        e2 = ROC_TB.get("e2", {}).get("1")
        if e1 and e2:
            st.markdown("**Messgrößen der Modellerweiterungen bei h = 1,0**")
            ein = e1["eingaben"]
            st.table(pd.DataFrame([{"Quelle": s, "η_j (Teilnahme)": _fmt_cell(ein["eta"][s], 3),
                                    "u_j Median [s]": _fmt_cell(ein["u_median"][s], 1), "u_j p90 [s]": _fmt_cell(ein["u_p90"][s], 1),
                                    "schon aktiv bei Beginn": _fmt_cell(ein["schon_aktiv"][s], 1, pct=True)} for s in ein["eta"]]).set_index("Quelle"))
            e2i = e2["eingaben"]
            st.table(pd.DataFrame([{"Episodengröße m": m, "Anteil der Fehlalarmzeit": _fmt_cell(e2i["anteil"][str(m)] if str(m) in e2i["anteil"] else e2i["anteil"].get(m), 1, pct=True),
                                    "Episoden je Bett·h": _fmt_cell(e2i["rate_je_bett_h"].get(str(m), e2i["rate_je_bett_h"].get(m)), 2),
                                    "mittlere Dauer [s]": _fmt_cell(e2i["dauer_s"].get(str(m), e2i["dauer_s"].get(m)), 1)} for m in (1, 2, 3, 4)]).set_index("Episodengröße m"))
        st.markdown(f"Vollständige Tabellen (Theorie gegen Messung je h, E1/E2 je h, Verriegelung): "
                    f"`{Path(ROC_DIR_STR) / rr.TAB_MD}`; Daten: `{Path(ROC_DIR_STR) / rr.CSV_OUT}`.")

        st.subheader("Export")
        st.markdown(f"Schreibt R1a, R1b, R2, R3, R4a/b, R5 (je T) mit der gewählten x-Variante und Skala als PNG (300 dpi) "
                    f"und SVG nach `{fz.FIG_DIR}`. Skript: `{ROC_CMD} --nur abbildungen`.")
        if st.button("ROC-Abbildungen exportieren"):
            with st.spinner("Abbildungen werden erzeugt …"):
                written = fr.export_all(ROC_ROWS, ROC_TB, fz.FIG_DIR, variant=roc_var, x_mode=roc_x, suffix=ROC_SUFFIX)
            st.success(f"{len(written)} Dateien geschrieben:")
            st.code("\n".join(str(path) for path in written))


# ═════════════════════════════════════════════════════════════════════════════
# Reiter ROC-Werkstatt: alle Kurven zu Ziel 3 in einem Bild, adaptiv-Konfigurationen (T, α, δ_min) aus der
# Werkstatt-Datei (python -m analysis.roc_ziel3.werkstatt); Regler in der Seitenleiste „8  ROC-Werkstatt“.
# Nur gerechnete Daten, kein Replay.
# ═════════════════════════════════════════════════════════════════════════════
from dataclasses import asdict, replace as _dc_replace  # noqa: E402

from analysis.roc_ziel3 import figures_werkstatt as fw  # noqa: E402
from analysis.roc_ziel3 import kalibrierung as kb  # noqa: E402
from analysis.roc_ziel3 import zeit_roc as zr  # noqa: E402
from analysis.roc_ziel3 import werkstatt as wsm  # noqa: E402

WS_FIG_DIR = fz.FIG_DIR / "werkstatt"
WS_CMD = "python -m analysis.roc_ziel3.werkstatt"


def _kal_mtimes() -> tuple:
    """Zeitstempel der Kalibrierungsdateien (Episoden, P0-Kurven, h-Kurven, Netzläufe, kalibriert.json, pruefung.json)."""
    files = sorted(kb.OUT_DIR.glob("*.csv")) + [kb.OUT_DIR / "kalibriert.json", kb.OUT_DIR / "pruefung.json"] if kb.OUT_DIR.exists() else []
    return tuple((p.name, p.stat().st_mtime) for p in files if p.exists())


def _ws_mtimes(roc_dir: str) -> tuple:
    out = list(_roc_mtimes(roc_dir))
    for p in wsm.files(Path(roc_dir))[:2]:
        out.append(p.stat().st_mtime if p.exists() else 0.0)
    return tuple(out) + _kal_mtimes()


@st.cache_data(show_spinner=False)
def _ws_dataset(eta: str, roc_dir: str, mtimes: tuple):
    rows, tb = _roc_data(roc_dir, mtimes[0], mtimes[1])
    if rows is None:
        return None
    return fw.attach_kalibrierung(fw.Dataset(eta, rows, tb, wsm.load(Path(roc_dir))))


VAL_SETS = {eta: str(d) for eta, d in ((kb.eta_of(d), d) for d in kb.VAL_DIRS.values())}    # „val 0,9“ → Verzeichnis


def _val_mtimes(val_dir: str) -> tuple:
    p = kb.val_rows_path(Path(val_dir))
    return (p.stat().st_mtime if p.exists() else 0.0,) + _kal_mtimes()


@st.cache_data(show_spinner=False)
def _val_dataset(val_dir: str, mtimes: tuple):
    return fw.val_dataset(Path(val_dir))


def _ws_datasets() -> dict:
    out = {}
    for eta, (d, _suffix) in ROC_SETS.items():
        ds = _ws_dataset(eta, d, _ws_mtimes(d))
        if ds is not None:
            out[eta] = ds
    for eta, d in VAL_SETS.items():
        ds = _val_dataset(d, _val_mtimes(d))
        if ds is not None:
            out[eta] = ds
    return out


@st.cache_data(show_spinner=False, max_entries=60)
def _png_ws(opt_items: tuple, mt_key: tuple) -> bytes:
    opt = fw.Options(**dict(opt_items))
    return _to_png(fw.fig_werkstatt(_ws_datasets(), opt))


def _ws_values(datasets: dict, param: str) -> list:
    vals: list = []
    for ds in datasets.values():
        if ds.werk is not None:
            vals = wsm.values_of(ds.werk["rows"], param)
            if vals:
                break
    if not vals:
        base = {"T": fr.available_T(next(iter(datasets.values())).rows) if datasets else [10.0],
                "alpha": [0.5], "delta_min": [0.5]}
        vals = base[param]
    return vals


def _ws_opt_items(opt: fw.Options) -> tuple:
    d = asdict(opt)
    return tuple((k, tuple(v) if isinstance(v, (list, tuple)) else v) for k, v in d.items())


WS_DS = _ws_datasets()
WS_H = sorted({r["h"] for ds in WS_DS.values() for r in ds.rows if r.get("art") == "Regel" and r.get("h") is not None}) or [1.0]
WS_ETA_OPTIONS = ([e for e in ("0,9", "0,7") if e in WS_DS] + (["beide"] if ("0,9" in WS_DS and "0,7" in WS_DS) else [])
                  + [e for e in ("val 0,9", "val 0,7") if e in WS_DS] + (["beide val"] if ("val 0,9" in WS_DS and "val 0,7" in WS_DS) else []))
ZR_CMD = "python -m analysis.roc_ziel3.zeit_roc"


@st.cache_data(show_spinner=False)
def _zr_rows(mtime: float) -> list:
    return zr.load()


def _zr_mtime() -> float:
    p = zr.OUT_DIR / zr.CSV_NAME
    return p.stat().st_mtime if p.exists() else 0.0


ZR_ROWS = _zr_rows(_zr_mtime())
KAL_INFO = kb.load_kalibriert()
KAL_CFG = wsm.parse_key(KAL_INFO["kalibriert"]["konfig"]) if KAL_INFO and KAL_INFO.get("kalibriert") else None
KAL_CMD = "python -m analysis.roc_ziel3.kalibrierung --stufe gitter | auswertung | pruefung"
WS_DEFAULTS = {"ws_view": "froc", "ws_eta": WS_ETA_OPTIONS[0] if WS_ETA_OPTIONS else "0,9", "ws_schar": "keine",
               "ws_r_adaptiv": True, "ws_r_2": True, "ws_r_3": True, "ws_r_1": False, "ws_r_4": False, "ws_devices": False,
               "ws_hull": True, "ws_chord": False, "ws_model": False, "ws_binom": False, "ws_netz": True, "ws_ci": False,
               "ws_xunit": "ereignis", "ws_xk": "ereignis", "ws_mode": "mild", "ws_params": "Prototyp", "ws_compare": True,
               "ws_scale": "linear", "ws_h": 1.0 if 1.0 in WS_H else WS_H[0]}
for _k, _v in WS_DEFAULTS.items():
    st.session_state.setdefault(_k, _v)

with st.sidebar:
    with st.expander("8  ROC-Werkstatt", expanded=False):
        st.caption("Diese Regler gelten für den Reiter „ROC-Werkstatt“ (nur gerechnete Daten).")
        st.radio("Darstellung", list(fw.VIEWS), format_func=lambda k: fw.VIEWS[k], key="ws_view")
        st.radio("Szenario", WS_ETA_OPTIONS or ["0,9"], format_func=lambda k: fw.ETAS.get(k, k), key="ws_eta")
        st.markdown("**Filter (adaptiv)**")
        if KAL_CFG is not None:
            st.radio("Parameter", list(fw.PARAM_SETS), key="ws_params", horizontal=True,
                     help="Prototyp: T = 10 s, α = 0,5, δ_min = 0,5, P0 = 0,5. kalibriert: nach dem vorab festgelegten Kriterium "
                          "im Lernszenario η = 0,9 (h = 1) gewählt; siehe Reiter Kapitel 5, Block 4b.")
        else:
            st.session_state["ws_params"] = "Prototyp"
            st.caption(f"Parameter „kalibriert“: ausstehend (`{KAL_CMD}`).")
        ws_T_vals = _ws_values(WS_DS, "T")
        ws_a_vals = _ws_values(WS_DS, "alpha")
        ws_d_vals = _ws_values(WS_DS, "delta_min")
        st.session_state.setdefault("ws_T", 10.0 if 10.0 in ws_T_vals else ws_T_vals[0])
        st.session_state.setdefault("ws_alpha", 0.5 if 0.5 in ws_a_vals else ws_a_vals[0])
        st.session_state.setdefault("ws_dmin", 0.5 if 0.5 in ws_d_vals else ws_d_vals[0])
        if st.session_state.get("ws_params") == "kalibriert" and KAL_CFG is not None:
            st.caption(f"kalibriert: T = {de(KAL_CFG[0])} s, α = {de(KAL_CFG[1], 1)}, δ_min = {de(KAL_CFG[2], 1)} "
                       f"(Regler für T/α/δ_min gelten nur für den Prototyp).")
        else:
            st.select_slider("Fenster T [s]", options=ws_T_vals, format_func=lambda v: de(v), key="ws_T")
            st.select_slider("α (Gewicht im Schweregrad)", options=ws_a_vals, format_func=lambda v: de(v, 1), key="ws_alpha")
            st.select_slider("δ_min (Untergrenze der Schwelle)", options=ws_d_vals, format_func=lambda v: de(v, 1), key="ws_dmin")
        st.selectbox("Kurvenschar (alle Werte eines Parameters, Abstufungen von #0F8A8A)", ["keine", "T", "alpha", "delta_min"],
                     format_func=lambda k: "keine" if k == "keine" else fw.PARAMS[k], key="ws_schar")
        if st.session_state.get("ws_view") == "klassisch":
            st.checkbox("Prototyp und kalibriert gemeinsam (klassische ROC)", key="ws_compare", disabled=KAL_CFG is None)
        if st.session_state.get("ws_view") == "zeit_T":
            st.markdown("**Fenster T (Zeit-ROC)**")
            _zc = st.columns(3)
            for _i, _T in enumerate(zr.T_WERTE):
                _zc[_i % 3].checkbox(f"{de(_T)} s", value=True, key=f"ws_zT_{_T:g}")
            st.checkbox("Zoom FPR_sys ≤ 3 %", key="ws_zzoom")
        st.markdown("**Regeln**")
        cA, cB = st.columns(2)
        cA.checkbox("adaptiv", key="ws_r_adaptiv")
        cA.checkbox("2-aus-4", key="ws_r_2")
        cA.checkbox("3-aus-4", key="ws_r_3")
        cB.checkbox("1-aus-4", key="ws_r_1")
        cB.checkbox("4-aus-4", key="ws_r_4")
        cB.checkbox("einzelne Geräte", key="ws_devices")
        st.markdown("**Ergänzungen**")
        cC, cD = st.columns(2)
        cC.checkbox("obere Hülle", key="ws_hull")
        cC.checkbox("Linie 2-/3-aus-4 desselben h", key="ws_chord")
        cC.checkbox("Modell E1+E2", key="ws_model")
        cD.checkbox("Binomialformel", key="ws_binom")
        cD.checkbox("Netzläufe (Sterne)", key="ws_netz")
        cD.checkbox("95-%-Intervalle (h-Punkt, nach Stück)", key="ws_ci")
        st.markdown("**Achsen**")
        if st.session_state.get("ws_view") == "klassisch":
            st.radio("x (klassische ROC)", list(fw.X_UNITS_KLASSISCH), format_func=lambda k: fw.X_UNITS_KLASSISCH[k], key="ws_xk")
        else:
            st.radio("x (nach Stück / Ereignis)", list(fw.X_UNITS), format_func=lambda k: fw.X_UNITS[k], key="ws_xunit")
        st.radio("Definition „erkannt“", list(fw.MODES), format_func=lambda k: fw.MODES[k], key="ws_mode")
        st.radio("Skala x", ["linear", "log"], key="ws_scale", horizontal=True)
        z1, z2 = st.columns(2)
        z1.number_input("x von", value=None, key="ws_xmin", format="%.4f")
        z2.number_input("x bis", value=None, key="ws_xmax", format="%.4f")
        z1.number_input("y von", value=None, key="ws_ymin", format="%.2f")
        z2.number_input("y bis", value=None, key="ws_ymax", format="%.2f")
        st.caption("Zoom leer = automatisch; nach Stück ist y in Prozent, nach Zeit 0 … 1.")
        st.select_slider("Grenzwertfaktor h (Raute auf allen Kurven)", options=WS_H, format_func=lambda v: de(v), key="ws_h")

_ws_rules = tuple(n for n, k in (("adaptiv", "ws_r_adaptiv"), ("2-aus-4", "ws_r_2"), ("3-aus-4", "ws_r_3"),
                                 ("1-aus-4", "ws_r_1"), ("4-aus-4", "ws_r_4")) if st.session_state.get(k))
_ws_kal = st.session_state.get("ws_params") == "kalibriert" and KAL_CFG is not None
_ws_cfg = KAL_CFG if _ws_kal else (float(st.session_state.get("ws_T", 10.0)), float(st.session_state.get("ws_alpha", 0.5)),
                                   float(st.session_state.get("ws_dmin", 0.5)))
WS_OPT = fw.Options(
    view=st.session_state["ws_view"], eta=st.session_state["ws_eta"],
    T=_ws_cfg[0], alpha=_ws_cfg[1], delta_min=_ws_cfg[2], kal=KAL_CFG,
    mode=st.session_state.get("ws_mode", "mild"), compare_params=bool(st.session_state.get("ws_compare", True)),
    schar=None if (st.session_state["ws_schar"] == "keine" or _ws_kal) else st.session_state["ws_schar"],
    rules=_ws_rules, devices=bool(st.session_state["ws_devices"]), hull=bool(st.session_state["ws_hull"]),
    chord=bool(st.session_state["ws_chord"]), model_e=bool(st.session_state["ws_model"]), binom=bool(st.session_state["ws_binom"]),
    netz=bool(st.session_state["ws_netz"]), ci=bool(st.session_state["ws_ci"]),
    x_unit=st.session_state.get("ws_xk", "ereignis") if st.session_state["ws_view"] == "klassisch" else st.session_state["ws_xunit"],
    log_x=st.session_state.get("ws_scale", "linear") == "log",
    zoom=tuple(None if v is None else float(v) for v in (st.session_state.get("ws_xmin"), st.session_state.get("ws_xmax"),
                                                           st.session_state.get("ws_ymin"), st.session_state.get("ws_ymax"))),
    h=float(st.session_state["ws_h"]), title=False,
    zeit_Ts=tuple(T for T in zr.T_WERTE if st.session_state.get(f"ws_zT_{T:g}", True)),
    zeit_zoom=bool(st.session_state.get("ws_zzoom", False)))

with tab_ws:
    st.subheader("ROC-Werkstatt – alle Kurven zu Ziel 3 in einem Bild")
    if not WS_DS:
        st.info(f"Keine ROC-Daten (`{rr.CSV_OUT}`). Erzeugen mit `{ROC_CMD}`, Konfigurationen mit `{WS_CMD}`.")
    else:
        werk_meta = {e: (ds.werk or {}).get("meta", {}) for e, ds in WS_DS.items()}
        fehlt = [e for e, ds in WS_DS.items() if ds.werk is None]
        if fehlt:
            st.warning(f"Werkstatt-Daten fehlen für η = {', '.join(fehlt)} (nur Basis T aus roc_alle.csv). Rechnen mit `{WS_CMD}`.")
        else:
            st.caption("Gerechnet offline mit dem unveränderten Dashboard-Kern (T und α über die Datenbank, δ_min über das "
                       "Parameterobjekt EngineConfig); " + "; ".join(
                           f"η = {e}: {len(m.get('konfigurationen', []))} Konfigurationen, Bootstrap {m.get('n_boot')} Züge, "
                           f"{m.get('rechenzeit_s', 0) / 60:.0f} min ({str(m.get('erstellt', ''))[:10]})" for e, m in werk_meta.items()))
        if KAL_INFO and KAL_INFO.get("kalibriert"):
            _kk = KAL_INFO["kalibriert"]
            st.caption(f"Parameterwahl: {KAL_INFO.get('n_konfigurationen')} Konfigurationen T × α × δ_min im Lernszenario η = 0,9, h = 1 "
                       f"({KAL_INFO.get('n_nebenbedingung_ok')} mit Median-Latenz ≤ 12 s); kalibriert = {_kk['konfig']} "
                       f"(AUC ereignisbezogen {de(_kk['auc'], 3)}, Latenz {de(_kk['latenz_median_s'], 1)} s); Kriterium in "
                       f"`{kb.OUT_DIR / 'kalibriert.json'}`.")
        else:
            st.caption(f"Parameterwahl „kalibriert“: ausstehend (`{KAL_CMD}`); ereignisbezogene Achse aus `{kb.OUT_DIR / 'episoden.csv'}`.")
        _ws_key = tuple(_ws_mtimes(d) for d, _ in ROC_SETS.values()) + tuple(_val_mtimes(d) for d in VAL_SETS.values()) + (_zr_mtime(),)
        if any(e.startswith("val") for e in WS_DS):
            st.caption("Validierungsszenarien (val 0,9 Seed 8, val 0,7 Seed 9): Geräte und k-aus-4 von PC 2, adaptiv offline nachgerechnet "
                       "(Prototyp und kalibriert, ohne Nachjustage); keine Werkstatt-Konfigurationen, keine Hülle, keine Netzläufe.")
        try:
            st.image(_png_ws(_ws_opt_items(WS_OPT), _ws_key), width="stretch")
        except ValueError as exc:
            st.error(f"Abbildung nicht möglich: {exc}")
        st.markdown("**Wie zu lesen:** " + fw.HOWTO[WS_OPT.view])
        if WS_OPT.view == "zeit_T":
            _zt = zr.table_rows(ZR_ROWS, fw.zeit_etas(WS_OPT), *fw.zeit_rules(WS_OPT))
            if _zt:
                st.markdown(f"**pAUC (FPR_sys ≤ 3 %, Trapez, normiert auf 3 % = mittlere TPR_sys) und Werte bei h = 1** "
                            f"(Quelle `{zr.OUT_DIR / zr.CSV_NAME}`)")
                st.table(pd.DataFrame(_zt).set_index(["η", "Kurve"]))
            else:
                st.info(f"Keine Zeit-ROC-Daten für dieses Szenario (`{ZR_CMD}`; nur η = 0,9 und 0,7).")
        elif WS_OPT.view == "klassisch":
            auc_tbl = fw.auc_rows(WS_DS, WS_OPT)
            if auc_tbl:
                st.markdown(f"**AUC bei h = {de(WS_OPT.h)}, erkannt {WS_OPT.mode}** (ereignisbezogen; Patienten-Bootstrap)")
                st.table(pd.DataFrame(auc_tbl).set_index(["η", "Parameter"]))
            else:
                st.caption("AUC-Intervalle: ausstehend (`python -m analysis.roc_ziel3.kalibrierung --stufe pruefung`).")
            k_tbl = fw.klassisch_table_rows(WS_DS, WS_OPT)
            if k_tbl:
                st.markdown(f"**Punkte bei h = {de(WS_OPT.h)}** (x und y in der gewählten Achse)")
                st.table(pd.DataFrame(k_tbl).set_index(["η", "Regel", "P0"]))
        elif WS_OPT.view == "geraet_system":
            dev_tbl = fw.device_table_rows(WS_DS, WS_OPT)
            if dev_tbl:
                st.markdown(f"**Geräte bei h = {de(WS_OPT.h)}**")
                st.table(pd.DataFrame(dev_tbl).set_index(["η", "Gerät"]))
            rule_tbl = fw.rule_table_rows(WS_DS, WS_OPT)
            if rule_tbl:
                st.markdown(f"**Regeln bei h = {de(WS_OPT.h)}**")
                st.table(pd.DataFrame(rule_tbl).set_index(["η", "Regel"]))
        else:
            tbl = fw.table_rows(WS_DS, WS_OPT)
            if tbl:
                st.markdown(f"**Tabelle bei h = {de(WS_OPT.h)}** (Δ Sehne / Δ Hülle mit 95-%-Intervall nur bei h = 1,0; "
                            f"Sicht {'nach Stück' if WS_OPT.view == 'froc' else 'nach Zeit'})")
                st.table(pd.DataFrame(tbl).set_index(["η", "Regel"]))
        st.subheader("Export")
        ws_name = st.text_input("Dateiname (ohne Endung)", value=fw.export_name(WS_OPT), key="ws_name")
        st.caption(f"PNG 300 dpi + SVG nach `{WS_FIG_DIR}`; ohne Titel.")
        if st.button("Exportieren", key="ws_export"):
            with st.spinner("Abbildung wird erzeugt …"):
                written = fw.save(fw.fig_werkstatt(WS_DS, _dc_replace(WS_OPT, title=False)), WS_FIG_DIR,
                                  ws_name or fw.export_name(WS_OPT))
            st.success(f"{len(written)} Dateien geschrieben:")
            st.code("\n".join(str(path) for path in written))


# ═════════════════════════════════════════════════════════════════════════════
# Reiter Kapitel 5 · Gesamtbild: sieben Blöcke (Frage, Abbildung, Aussage, Export) aus gerechneten Daten
# (Läufe aus docs/reports, ROC-Werkstatt); kein Replay, kein Modell.
# ═════════════════════════════════════════════════════════════════════════════
import kapitel5 as k5  # noqa: E402

K5_RUNS = [RUNS_ALL[k] for k in sd.RUN_ORDER if k in RUNS_ALL]


def _k5_key() -> tuple:
    mts = [Path(p).stat().st_mtime if Path(p).exists() else 0.0 for k in RUNS_ALL for p in sd.run_files(k)]
    return tuple(mts) + tuple(_ws_mtimes(d) for d, _ in ROC_SETS.values()) + tuple(_val_mtimes(d) for d in VAL_SETS.values())


def _k5_fig(block, params: tuple):
    if block == 1:
        return k5.fig_betriebspunkte(K5_RUNS, *params)
    if block == 2:
        return k5.fig_bilanz(RUNS_ALL[params[0]])
    if block == 3:
        return k5.fig_eta(K5_RUNS, *params)
    if block == 4:
        return k5.fig_froc(WS_DS, *params)
    if block == "4b":
        return k5.fig_klassisch(WS_DS, *params)
    if block == 5:
        return k5.fig_kosten(K5_RUNS, WS_DS)
    if block == 6:
        return k5.fig_schar(WS_DS, *params)
    return k5.fig_geraet(WS_DS)


@st.cache_data(show_spinner=False, max_entries=60)
def _png_k5(block, params: tuple, key: tuple) -> bytes:
    return _to_png(_k5_fig(block, params))


@st.cache_data(show_spinner=False, max_entries=10)
def _png_zr(key: str, mtime: float) -> bytes:
    make = {"z1": lambda: zr.fig_z1(ZR_ROWS), "z1_zoom": lambda: zr.fig_z1(ZR_ROWS, zoom=True),
            "z2": lambda: zr.fig_z2(ZR_ROWS), "z3": lambda: zr.fig_z3(ZR_ROWS)}[key]
    return _to_png(make())


def _k5_block(block, params: tuple, aussage: str) -> None:
    st.image(_png_k5(block, params, _k5_key()), width="stretch")
    st.markdown("**Aussage:** " + aussage)
    if st.button("Exportieren", key=f"k5_export_{block}"):
        with st.spinner("Abbildung wird erzeugt …"):
            written = k5.save(_k5_fig(block, params), block)
        st.success(f"{len(written)} Dateien geschrieben:")
        st.code("\n".join(str(path) for path in written))


with tab_k5:
    st.subheader("Kapitel 5 · Gesamtbild")
    st.markdown(f"**{k5.HINWEIS}**")
    if not RUNS_ALL:
        st.error(NO_DATA)
    else:
        if MISSING_RUNS:
            st.caption("Läufe ohne Daten (erscheinen automatisch, sobald ihre Dateien in docs/reports liegen): "
                       + ", ".join(sd.RUNS[k].label_eta for k in MISSING_RUNS) + ".")
        st.caption(f"Export: PNG 300 dpi + SVG nach `{k5.FIG_DIR}` (ohne Titel); Skript: "
                   f"`python -m analysis.belastungsmodell_app.kapitel5`.")
        if KAL_CFG is not None:
            k5_params = st.radio("Parameter des Filters", list(k5.PARAM_SETS), key="k5_params", horizontal=True,
                                 help="Gilt für die Blöcke 1, 4 und 4b. kalibriert: Netzläufe offline bei h = 1 nachgerechnet "
                                      "(adaptiv), Prototyp: wirklicher Dashboard-Lauf.")
        else:
            k5_params = "Prototyp"
            st.caption(f"Parameter „kalibriert“: ausstehend (`{KAL_CMD}`).")

        # 1 ───────────────────────────────────────────────────────────────────
        st.subheader(f"1  {k5.FRAGEN[1]}")
        c1, c2 = st.columns(2)
        k5_x = c1.radio("x-Achse", list(k5.X_UNITS), format_func=lambda k: k5.X_UNITS[k], key="k5_x", horizontal=True)
        k5_m1 = c2.radio("Definition „erkannt“", list(sd.MODES), format_func=lambda k: sd.MODES[k], key="k5_m1", horizontal=True)
        if k5_x == "ereignis" or k5_params == "kalibriert":
            st.caption("Ereignisbezogen bzw. kalibriert: nur Läufe mit Journalen auf PC 1 (12 h, A, B, C, D; 1 h und 6 h haben "
                       "keine); adaptiv beim Prototyp = wirklicher Dashboard-Lauf, bei kalibriert = Replay bei h = 1.")
        _k5_block(1, (k5_x, k5_m1, k5_params), k5.aussage_1(K5_RUNS, k5_m1, k5_x, k5_params))

        # 2 ───────────────────────────────────────────────────────────────────
        st.subheader(f"2  {k5.FRAGEN[2]}")
        k5_run = st.selectbox("Lauf", [r.key for r in K5_RUNS], format_func=lambda k: RUNS_ALL[k].spec.label_eta,
                              index=[r.key for r in K5_RUNS].index("12h") if "12h" in RUNS_ALL else 0, key="k5_run")
        _k5_block(2, (k5_run,), k5.aussage_2(RUNS_ALL[k5_run]))

        # 3 ───────────────────────────────────────────────────────────────────
        st.subheader(f"3  {k5.FRAGEN[3]}")
        k5_m3 = st.radio("Definition „erkannt“ ", list(sd.MODES), format_func=lambda k: sd.MODES[k], key="k5_m3", horizontal=True)
        _k5_block(3, (k5_m3,), k5.AUSSAGE_3)

        # 4 ───────────────────────────────────────────────────────────────────
        st.subheader(f"4  {k5.FRAGEN[4]}")
        if not WS_DS:
            st.info(f"Keine ROC-Daten (`{ROC_CMD}`).")
        else:
            c41, c42 = st.columns(2)
            k5_x4 = c41.radio("x-Achse ", list(fw.X_UNITS), format_func=lambda k: fw.X_UNITS[k], key="k5_x4", horizontal=True)
            k5_m4 = c42.radio("Definition „erkannt“  ", list(sd.MODES), format_func=lambda k: sd.MODES[k], key="k5_m4", horizontal=True)
            _k5_block(4, (k5_x4, k5_m4, k5_params), k5.aussage_4(WS_DS))
            rows4 = k5.delta_rows(WS_DS)
            if rows4:
                st.caption("Δ Sehne / Δ Hülle nach Stück (je Bett·h) bei h = 1, Basis-Konfiguration.")
                st.table(pd.DataFrame(rows4).set_index("η"))

        # 4b ──────────────────────────────────────────────────────────────────
        st.subheader(f"4b  {k5.FRAGEN['4b']}")
        if not WS_DS or not k5.has_klassisch(WS_DS):
            st.info("Keine P0-Kurven (`python -m analysis.roc_ziel3.kalibrierung --stufe klassisch`).")
        else:
            c4a, c4b, c4c = st.columns(3)
            k5_x4b = c4a.radio("x-Achse  ", list(fw.X_UNITS_KLASSISCH), format_func=lambda k: fw.X_UNITS_KLASSISCH[k], key="k5_x4b")
            k5_m4b = c4b.radio("Definition „erkannt“   ", list(sd.MODES), format_func=lambda k: sd.MODES[k], key="k5_m4b")
            k5_h4b = c4c.radio("Grenzwertfaktor h", list(kb.H_KLASSISCH), format_func=lambda v: de(v, 1), index=1, key="k5_h4b")
            k5_cmp = st.checkbox("Prototyp und kalibriert gemeinsam", value=True, key="k5_cmp", disabled=KAL_CFG is None)
            _k5_block("4b", (k5_x4b, k5_m4b, float(k5_h4b), bool(k5_cmp), k5_params), k5.aussage_4b(WS_DS, k5_m4b, float(k5_h4b)))
            rows4b = k5.auc_rows(WS_DS, k5_m4b, float(k5_h4b))
            if rows4b:
                st.table(pd.DataFrame(rows4b).set_index(["η", "Parameter"]))

        # 5 ───────────────────────────────────────────────────────────────────
        st.subheader(f"5  {k5.FRAGEN[5]}")
        _k5_block(5, (), k5.aussage_5(K5_RUNS))

        # 6 ───────────────────────────────────────────────────────────────────
        st.subheader(f"6  {k5.FRAGEN[6]}")
        if not any(ds.werk for ds in WS_DS.values()):
            st.info(f"Keine Werkstatt-Daten (`{WS_CMD}`).")
        else:
            k5_param = st.radio("Kurvenschar", ["T", "alpha", "delta_min"], format_func=lambda k: fw.PARAMS[k],
                                key="k5_param", horizontal=True)
            rows6 = k5.sens_rows(WS_DS)
            if rows6:
                st.table(pd.DataFrame(rows6).set_index(["η", "Konfiguration"]))
            _k5_block(6, (k5_param,), k5.aussage_6(WS_DS))

        # 7 ───────────────────────────────────────────────────────────────────
        st.subheader(f"7  {k5.FRAGEN[7]}")
        if not WS_DS:
            st.info(f"Keine ROC-Daten (`{ROC_CMD}`).")
        else:
            _k5_block(7, (), k5.aussage_7(WS_DS))

        # 8 ───────────────────────────────────────────────────────────────────
        st.subheader(f"8  {zr.FRAGE}")
        if not ZR_ROWS:
            st.info(f"Keine Zeit-ROC-Daten (`{ZR_CMD}`).")
        else:
            st.markdown("**Wie zu lesen:** " + zr.WIE_LESEN)
            k5_zzoom = st.radio("Z1", ["ganz", "Zoom FPR_sys ≤ 3 %"], key="k5_zzoom", horizontal=True)
            for _zk, _zmake in (("z1_zoom" if k5_zzoom != "ganz" else "z1", (lambda z=(k5_zzoom != "ganz"): zr.fig_z1(ZR_ROWS, zoom=z))),
                                ("z2", lambda: zr.fig_z2(ZR_ROWS)), ("z3", lambda: zr.fig_z3(ZR_ROWS))):
                st.image(_png_zr(_zk, _zr_mtime()), width="stretch")
                if st.button("Exportieren", key=f"k5_export_{_zk}"):
                    _fig = _zmake()
                    _w = zr.save(_fig, zr.FILES[_zk])
                    plt.close(_fig)
                    st.success(f"{len(_w)} Dateien geschrieben:")
                    st.code("\n".join(str(path) for path in _w))
                if _zk.startswith("z1"):
                    st.table(pd.DataFrame(zr.table_rows(ZR_ROWS, ["0,9", "0,7"])).set_index(["η", "Kurve"]))
                if _zk == "z3":
                    st.table(pd.DataFrame(zr.zerlegung_rows(ZR_ROWS, ["0,9", "0,7"], zr.T_Z3)).set_index(["η", "Regel"]))

        # Zusammenfassung ─────────────────────────────────────────────────────
        st.subheader("Zusammenfassung")
        for satz in k5.zusammenfassung(k5.kennzahlen(RUNS_ALL, WS_DS)):
            st.markdown(f"- {satz}")
