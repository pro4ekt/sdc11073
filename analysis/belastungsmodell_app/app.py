"""Streamlit-Oberfläche des analytischen Belastungsmodells (Ziel 3), Gliederung nach Kapitel 5.

Start aus dem Repository-Wurzelverzeichnis:

    .venv\\Scripts\\python -m streamlit run analysis/belastungsmodell_app/app.py

Seitenleiste: Szenario, Station (Literatur), Annahmen, Filtermodell, Vergleich mit k-aus-N,
Zeitverlauf, Darstellung.  Hauptbereich: 1 „Ohne und mit Filter“ (5.3.2), 2 „Vergleich mit
k-aus-N“ (5.3.3, vier Mechanismen), 3 „Weitere Kurven“.  Rechenfunktionen in ``model.py``,
Abbildungen in ``export_figures.py``.
"""

from __future__ import annotations

import io
import math
import sys
from dataclasses import replace
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model as bm  # noqa: E402
import export_figures as fx  # noqa: E402

st.set_page_config(page_title="Belastungsmodell Ziel 3", page_icon="📉", layout="wide")

# ─────────────────────────────────────────────────────────────────────────────
# Regler
# ─────────────────────────────────────────────────────────────────────────────

# key, Beschriftung, min, max, Schritt, Standard, Hilfetext
STATION = [
    ("n", "n – gezählte Alarme je Kanal und Tag", 5.0, 300.0, 0.5, 37.5,
     "N_d = n·|M| Alarme je Bett und Tag (Median-Intensivstation: 150 bei |M| = 4)."),
    ("fdr", "FDR_cnt – Anteil irrelevanter Alarme (gezählt)", 0.50, 0.99, 0.01, 0.90, None),
    ("d_false", "d_false – mittlere Dauer eines Fehlalarms [s]", 2.0, 60.0, 1.0, 8.0, "Zugleich μ_art."),
    ("M", "|M| – Zahl der Kanäle", 2, 12, 1, 4, None),
    ("n_high", "n_H – Kanäle mit Priorität hoch (P = 3)", 0, 12, 1, 2, "Die übrigen Kanäle haben P = 1."),
]
ANNAHMEN = [
    ("c_art", "c_art – Anteil gemeinsam verursachter Artefakte", 0.0, 1.0, 0.01, 0.5, None),
    ("d_true", "d_true – mittlere Dauer eines Krisenalarms [s]", 5.0, 120.0, 1.0, 30.0, None),
    ("tpr", "TPR_j – Sensitivität je Kanal", 0.50, 0.99, 0.01, 0.90, None),
]
MODELL = [
    ("T", "T – Fenster und Zeithorizont [s]", 2.0, 30.0, 1.0, 10.0, None),
    ("delta_min", "δ_min – Untergrenze des Schmelzfaktors", 0.10, 1.00, 0.01, 0.50, None),
    ("alpha", "α – Gewicht des Spitzenterms im SDC_score", 0.0, 1.0, 0.05, 0.5, None),
    ("OR", "OR – Odds Ratio des Kontextes (L = ln OR)", 1.0, 20.0, 0.5, 1.0, None),
]
VERGLEICH = [
    ("tau", "τ – Verzögerung der Zählregel k-aus-N [s]", 0.0, 20.0, 1.0, 5.0,
     "Fairer Vergleich: das Fenster T wirkt wie eine Alarmverzögerung."),
]
ZEITVERLAUF = [
    ("t0", "t0 – Beginn des zweiten Alarms [s]", 0.0, 120.0, 1.0, 30.0, None),
]
DARSTELLUNG = [
    ("beds", "N – Betten je Station", 1, 40, 1, 10, None),
]
ALL_SLIDERS = STATION + ANNAHMEN + MODELL + VERGLEICH + ZEITVERLAUF + DARSTELLUNG
OTHER_DEFAULTS = {"noise_on": False, "mfpr": 0.0125, "prio_a": "hoch", "prio_b": "niedrig", "rule_d": "adaptiv",
                  "knn_k": 2, "units": "je Bett", "kurve": "1", "alpha_frei": 0.5}


def _init_state() -> None:
    for key, *_rest in ALL_SLIDERS:
        st.session_state.setdefault(key, _rest[4])
    for key, val in OTHER_DEFAULTS.items():
        st.session_state.setdefault(key, val)


def _apply_scenario(name: str) -> None:
    for key, val in bm.SCENARIOS[name].items():
        st.session_state[key] = val
    st.session_state["noise_on"] = False


def _clamp_n_high() -> None:
    st.session_state["n_high"] = min(int(st.session_state["n_high"]), int(st.session_state["M"]))


def _reset_all() -> None:
    for key, *_rest in ALL_SLIDERS:
        st.session_state[key] = _rest[4]
    for key, val in OTHER_DEFAULTS.items():
        if key != "kurve":
            st.session_state[key] = val


def _slider(key: str, label: str, lo, hi, step, default, help_text) -> None:
    st.slider(label, min_value=lo, max_value=hi, step=step, key=key, help=help_text)


def params_from_state() -> bm.Params:
    s = st.session_state
    return bm.Params(
        n=float(s["n"]), fdr=float(s["fdr"]), d_false=float(s["d_false"]), d_true=float(s["d_true"]),
        M=int(s["M"]), n_high=int(s["n_high"]), c_art=float(s["c_art"]), tpr=float(s["tpr"]),
        T=float(s["T"]), delta_min=float(s["delta_min"]), alpha=float(s["alpha"]), OR=float(s["OR"]),
        noise_direct=float(s["mfpr"]) if s["noise_on"] else None,
    )


def pct(x: float, digits: int = 2) -> str:
    return "–" if x != x else fx.de(100 * x, digits) + " %"


def num(x: float, digits: int = 2) -> str:
    if x is None or x != x:
        return "–"
    if math.isinf(x):
        return "∞"
    return fx.de(x, digits)


# ─────────────────────────────────────────────────────────────────────────────
# Seitenleiste
# ─────────────────────────────────────────────────────────────────────────────

_init_state()

with st.sidebar:
    with st.expander("Szenario", expanded=True):
        st.caption("Stationen aus Tabelle 5.7")
        cols = st.columns(2)
        for i, name in enumerate(bm.SCENARIOS):
            cols[i % 2].button(name, on_click=_apply_scenario, args=(name,), key=f"btn_{name}")
        st.button("alle Regler zurücksetzen", on_click=_reset_all)

    with st.expander("Station (Literatur)"):
        for key, label, lo, hi, step, default, help_text in STATION:
            if key == "M":
                st.slider(label, min_value=lo, max_value=hi, step=step, key=key, help=help_text,
                          on_change=_clamp_n_high)
                _clamp_n_high()
            elif key == "n_high":
                st.slider(label, min_value=0, max_value=int(st.session_state["M"]), step=1, key=key, help=help_text)
            else:
                _slider(key, label, lo, hi, step, default, help_text)

    with st.expander("Annahmen"):
        st.caption("nicht aus der Literatur belegt; als Bandbreite lesen")
        for row in ANNAHMEN:
            _slider(*row)

    with st.expander("Filtermodell (Prototyp)"):
        for row in MODELL:
            _slider(*row)

    with st.expander("Vergleich mit k-aus-N"):
        st.radio("k der Zählregel", [2, 3], key="knn_k", horizontal=True)
        for row in VERGLEICH:
            _slider(*row)
        st.checkbox("Rauschen direkt vorgeben (|M|·FPR; P(C) bleibt kalibriert)", key="noise_on")
        st.slider("|M|·FPR", min_value=0.005, max_value=1.2, step=0.005, key="mfpr", format="%.3f",
                  disabled=not st.session_state["noise_on"])

    with st.expander("Zeitverlauf"):
        st.selectbox("Priorität Kanal A (ab 0 s)", ["hoch", "niedrig"], key="prio_a")
        st.selectbox("Priorität Kanal B (ab t0)", ["hoch", "niedrig"], key="prio_b")
        for row in ZEITVERLAUF:
            _slider(*row)
        st.selectbox("Regel des Zeitverlaufs", ["adaptiv", "Basis"], key="rule_d")

    with st.expander("Darstellung"):
        st.radio("Einheiten", ["je Bett", "je Station mit N Betten"], key="units")
        for row in DARSTELLUNG:
            _slider(*row)

P = params_from_state()
CAL = bm.calibrate(P)
BEDS = int(st.session_state["beds"]) if st.session_state["units"] == "je Station mit N Betten" else 1
UNIT = fx.unit_label(BEDS)
TAU = float(st.session_state["tau"])
KNN = bm.rein(int(st.session_state["knn_k"]))

with st.sidebar:
    st.markdown("**Abgeleitete Größen**")
    st.table(pd.DataFrame({
        "Größe": ["N_d (Alarme je Bett und Tag)", "P(C)", "FPR", "|M|·FPR", "w = ln(TPR/FPR)", "Decke 1 − c_art/|M|",
                  "Prioritäten P_j"],
        "Wert": [num(CAL.N_d, 1), num(CAL.PC, 5), num(CAL.FPR, 5), num(CAL.MFPR, 4), num(CAL.w, 3),
                 pct(CAL.ceiling, 1), " ".join(str(x) for x in P.priorities)],
    }).set_index("Größe"))


# ─────────────────────────────────────────────────────────────────────────────
# Abbildungen (gecacht als PNG)
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False, max_entries=400)
def _png(name: str, p: bm.Params, extra: tuple) -> bytes:
    fig = fx.BUILDERS[name](p, *extra)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100)
    plt.close(fig)
    return buf.getvalue()


def figure(name: str, *extra, title: str = "", text: str = "") -> None:
    if title:
        st.markdown(f"**{title}**")
    if text:
        st.markdown(text)
    st.image(_png(name, P, tuple(extra)), width="stretch")


# ─────────────────────────────────────────────────────────────────────────────
# Hauptbereich
# ─────────────────────────────────────────────────────────────────────────────

st.title("Belastungsmodell – Ziel 3: Senkung der Fehlalarme")
st.caption(
    "Analytisches Modell der Alarmlast (Kapitel 4, 4.4.2; Kapitel 5, 5.3). Alle Kanäle tragen w = ln(TPR_j/FPR); "
    "die Schwelle ist Θ = max(ε, k·w·δ − L) mit k aus dem SDC_score (adaptiv) bzw. k = 2 (Basis). "
    "„rein k-aus-N“ ist die klassische Zählregel aus Kapitel 3 (ohne Schmelzen und Kontext), "
    "„ohne Filter“ meldet jede Quelle selbst."
)

with st.expander("Bedeutung der Größen"):
    st.markdown(
        "- **n** – gezählte Alarme je Quelle und Tag (Alarme je Bett und Tag aus der Literatur, geteilt durch |M|).\n"
        "- **FDR_cnt** – Anteil der gezählten Alarme, die klinisch irrelevant sind (Fehlalarme).\n"
        "- **d_false** – mittlere Dauer eines Fehlalarms; zugleich μ_art, die mittlere Dauer eines Artefakts.\n"
        "- **d_true** – mittlere Dauer eines Krisenalarms.\n"
        "- **|M|** – Zahl der alarmierenden Quellen (Kanäle) am Bett.\n"
        "- **n_H** – Zahl der Quellen mit hoher Priorität (P = 3); die übrigen haben niedrige Priorität (P = 1).\n"
        "- **c_art** – Anteil der Fehlalarme mit gemeinsamer Ursache, die alle Quellen zugleich treffen.\n"
        "- **TPR_j** – Sensitivität einer Quelle: Wahrscheinlichkeit, dass sie in einer Krise alarmiert.\n"
        "- **T** – Fenster, über das die Belegung s_j gemittelt wird; zugleich Zeithorizont von Schmelzen und "
        "Hysterese.\n"
        "- **δ_min** – Untergrenze des Schmelzfaktors: tiefer als δ_min·k·w sinkt die Schwelle nie "
        "(0,5 = höchstens auf die Hälfte).\n"
        "- **α** – Gewicht der höchsten Priorität gegenüber der Summe der Prioritäten im SDC_score.\n"
        "- **OR** – Odds Ratio des klinischen Kontextes; L = ln(OR) senkt die Schwelle.\n"
        "- **τ** – angenommene Verzögerung der klassischen Zählregel k-aus-N (fairer Vergleich zum Fenster T).\n"
        "- **k** – Quorum: Zahl der Quellen, deren Evidenz zusammen die Schwelle erreicht (adaptiv aus dem "
        "SDC_score, bei k-aus-N fest).\n"
        "- **t_esc** – Zeit, nach der eine anhaltende Kombination unterhalb des Quorums durch Schmelzen eskaliert.\n"
        "- **t_need** – Zeit, die eine gleichzeitig beginnende Episode bis zur Eskalation braucht "
        "(T·k/r im Zweig „sofort“, sonst t_esc).\n"
        "- **System-TPR** – Anteil der Krisen, die der Filter eskaliert.\n"
        "- **ARR** – Alarmreduktionsrate: Anteil der Fehlalarmbelegung, den der Filter entfernt "
        "(1 − Noise_f/Noise_r).\n"
        "- **PPV** – positiver Vorhersagewert: Anteil der Eskalationen, die Krisen sind (gezählt nach Episoden "
        "oder zeitgewichtet)."
    )

if not CAL.valid:
    st.error(
        f"Unzulässige Kalibrierung: FPR = {num(CAL.FPR, 4)} muss kleiner als TPR_j = {num(P.tpr)} sein und "
        f"P(C) = {num(CAL.PC, 4)} kleiner als 1 (Zulassungsregel w > 0). Bitte Regler anpassen."
    )
    st.stop()

TABS = {"1": "1  Ohne und mit Filter", "2": "2  Vergleich mit k-aus-N", "3": "3  Weitere Kurven"}
kurve = st.segmented_control("Abschnitt", list(TABS), format_func=lambda k: TABS[k], key="kurve", required=True,
                             label_visibility="collapsed") or "1"

R_OHNE = bm.evaluate(P, bm.OHNE, CAL)
R_AD = bm.evaluate(P, bm.ADAPTIV, CAL)

# ═════════════════════════════════════════════════════════════════════════════
if kurve == "1":
    st.subheader("Belastung des Personals ohne und mit Filter")
    co, ca = bm.scaled_counts(R_OHNE, BEDS), bm.scaled_counts(R_AD, BEDS)
    m = st.columns(4)
    m[0].metric(f"Fehlalarme/Tag ({UNIT})", f"{num(co.false_per_day, 1)} → {num(ca.false_per_day, 1)}",
                delta=f"{fx.de(100 * (ca.false_per_day / co.false_per_day - 1), 1)} %", delta_color="inverse",
                help="ohne Filter → adaptives Modell")
    m[1].metric(f"Krisenmeldungen/Tag ({UNIT})", f"{num(co.reports_per_day, 2)} → {num(ca.reports_per_day, 2)}",
                delta=f"{fx.de(100 * (ca.reports_per_day / co.reports_per_day - 1), 1)} %", delta_color="off",
                help=f"ohne Filter meldet jeder Kanal einzeln (|M|·TPR_j je Krise); Krisen je Tag: "
                     f"{num(ca.crises_per_day, 2)}")
    m[2].metric("PPV gezählt", f"{pct(R_OHNE.PPV_cnt, 1)} → {pct(R_AD.PPV_cnt, 1)}",
                delta=f"{fx.de(100 * (R_AD.PPV_cnt - R_OHNE.PPV_cnt), 1)} pp")
    m[3].metric("System-TPR", f"{pct(R_OHNE.TPR_sys, 2)} → {pct(R_AD.TPR_sys, 2)}",
                delta=f"{fx.de(100 * (R_AD.TPR_sys - R_OHNE.TPR_sys), 2)} pp")
    st.caption("Werte: ohne Filter → adaptives Modell. Fehlalarme je Tag = Noise·86400/d_false, "
               "Krisenmeldungen = Krisen je Tag · (|M|·TPR_j ohne Filter bzw. System-TPR mit Filter).")

    figure("1a", BEDS, title="1a  Fehlalarme über der Zahl der Kanäle",
           text="Ohne Filter wächst die Last linear mit jedem angeschlossenen Gerät (n·FDR je Kanal). "
                "Das adaptive Modell hält sie nahe der Untergrenze, die die gemeinsam verursachten Artefakte "
                "setzen; n_H wird dabei als |M| div 2 mitgeführt.")
    figure("1b", BEDS, title="1b  Fehlalarme über dem Rauschen |M|·FPR",
           text="Links beide Regeln auf logarithmischer Achse: Die Lücke bleibt über zwei Größenordnungen des "
                "Rauschens erhalten. Rechts nur das adaptive Modell in seinem eigenen Maßstab; erst nahe "
                "|M|·FPR ~ 1 steigt die Restlast, weil zufällige Koinzidenzen häufig werden.")
    figure("1c", BEDS, title="1c  Fehlalarme über dem korrelierten Artefaktanteil c_art",
           text="Gemeinsam verursachte Artefakte treffen alle Kanäle zugleich und können von keinem "
                "Ensemble-Filter unterschieden werden: Die Restlast folgt der Untergrenze ohne Filter · c_art/|M| "
                "(Decke der ARR 1 − c_art/|M|). c_art ist eine Annahme, deshalb die ganze Spanne 0 … 1.")
    figure("1d", BEDS, title="1d  Einzelvariation der Parameter (Tornado)",
           text="Jeder Parameter wird einzeln vom Arbeitspunkt aus nach unten und oben gesetzt (n ×0,5/×1,5; "
                "FDR 0,80/0,95; d_false 4/16 s; d_true 15/60 s; TPR_j 0,80/0,97; c_art 0,25/0,75; |M| 3/6; "
                "δ_min 0,3/0,7; α 0,25/0,75; T 5/20 s). Die Last hängt vor allem an n und c_art, die Sensitivität "
                "an TPR_j, δ_min und |M|.")

# ═════════════════════════════════════════════════════════════════════════════
elif kurve == "2":
    st.subheader("Vergleich mit dem klassischen k-aus-N")
    rows = bm.comparison_rows(P, TAU, CAL, BEDS)
    df = pd.DataFrame({
        "Regel": [r["rule"] for r in rows],
        f"Fehlalarme/Tag ({UNIT})": [r["false_per_day"] for r in rows],
        "Δ gegenüber adaptiv": [r["delta"] for r in rows],
        "System-TPR [%]": [100 * r["TPR_sys"] for r in rows],
        "verpasste Krisen je 1000": [r["missed"] for r in rows],
        "Latenz (typisch) [s]": [r["latency"] for r in rows],
    }).set_index("Regel")

    def _grey_basis(row):
        return ["color: #9a9a94"] * len(row) if row.name == "Basis" else [""] * len(row)

    styler = (df.style
              .format({f"Fehlalarme/Tag ({UNIT})": lambda v: num(v, 1),
                       "Δ gegenüber adaptiv": lambda v: ("+" if v > 0.05 else "") + num(v, 1),
                       "System-TPR [%]": lambda v: num(v, 2),
                       "verpasste Krisen je 1000": lambda v: num(v, 1),
                       "Latenz (typisch) [s]": lambda v: num(v, 1)})
              .apply(_grey_basis, axis=1)
              .set_properties(subset=["Δ gegenüber adaptiv"], **{"background-color": "#fdebd0", "font-weight": "bold"}))
    st.dataframe(styler)
    st.caption("Latenz (typisch): krisengewichtetes Mittel der Zeit bis zur Eskalation einer anhaltenden Episode "
               "(adaptiv/Basis: Aufbau der Evidenz über das Fenster T bzw. t_esc; k-aus-N: die eingestellte "
               f"Verzögerung τ = {fx.de(TAU)} s). Basis = k = 2 fest mit Schmelzen und Kontext (grau).")

    # 2.1 ────────────────────────────────────────────────────────────────────
    st.subheader("2.1  Prioritäten")
    st.markdown("Bei k-aus-N zählt nur die Zahl der Quellen; im adaptiven Modell bestimmt die Priorität der "
                "alarmierenden Quellen über SDC_score und k_min (3.16)/(3.17), ob und wann eskaliert wird. "
                f"Die Tabelle zeigt alle Kombinationen bei |M| = {P.M}, n_H = {P.n_high}.")
    comb = bm.combination_rows(P, CAL)
    st.dataframe(pd.DataFrame([{
        "Quellen": r["label"], "Anzahl": r["count"], "SDC_score": num(r["sdc"], 3), "k_min": r["k"],
        "Θ0 [nat]": num(r["theta0"], 2), "Θmin [nat]": num(r["theta_min"], 2), "Σw [nat]": num(r["sum_w"], 2),
        "adaptiv": r["branch"], "t_esc [s]": num(r["t_esc"], 1) if r["branch"] == "nach Schmelzen" else "–",
        "t_need [s]": num(r["t_need"], 1), "rein 2-aus-N": r["rein 2-aus-N"], "rein 3-aus-N": r["rein 3-aus-N"],
    } for r in comb]).set_index("Quellen"))
    figure("P", title="P  Eskalationszeit und eskalierter Anteil je Kombination",
           text="Links die Zeit, die eine anhaltende Episode bis zur Eskalation braucht (adaptiv: T·k/r im Zweig "
                "„sofort“, t_esc nach Schmelzen, „nie“ unter der Untergrenze; rein 2-aus-N: 0 ab zwei Quellen). "
                f"Rechts der Anteil eskalierter Episoden bei μ_art = {fx.de(CAL.mu_art)} s: r unabhängige Dauern, "
                f"bei „alle {P.M}“ eine gemeinsame (Stoß).")
    figure("B", title="B  Betriebskennlinie",
           text="Sensitivität gegen Fehlalarmlast. Die reine Zählregel liegt auf einer Treppe k = 1 … |M|; das "
                "adaptive Modell bewegt sich mit δ_min zwischen k = 3 und k = 2 (Ring = aktueller Wert). "
                "Der Ausschnitt vergrößert die Umgebung des Arbeitspunkts.")

    # 2.2 ────────────────────────────────────────────────────────────────────
    st.subheader("2.2  Schmelzen der Schwelle")
    rule_d = bm.ADAPTIV if st.session_state["rule_d"] == "adaptiv" else bm.BASIS
    st.markdown("Hält ein Alarm an, sinkt die Schwelle über δ(t) bis δ_min: Kombinationen unterhalb des Quorums "
                "eskalieren verzögert statt nie. Kanal A alarmiert ab 0 s, Kanal B ab t0; Belegung nach (3.6), "
                "Hysterese nach (3.22). Die Linie δ_min = 1 → 0,1 in Abbildung B zeigt, was das Schmelzen die "
                "Sensitivität kostet bzw. bringt.")
    c1, c2 = st.columns(2)
    with c1:
        figure("D", float(st.session_state["t0"]), rule_d, title="D  Eskalationslatenz über t0")
    with c2:
        figure("Dtrace", st.session_state["prio_a"], st.session_state["prio_b"], float(st.session_state["t0"]),
               rule_d, title=f"D  Zeitverlauf {st.session_state['prio_a']} → {st.session_state['prio_b']}")
    lat = bm.escalation_latency(P, st.session_state["prio_a"], st.session_state["prio_b"],
                                float(st.session_state["t0"]), rule_d, cal=CAL)
    st.caption(f"Latenz für {st.session_state['prio_a']}→{st.session_state['prio_b']} bei t0 = "
               f"{num(st.session_state['t0'], 0)} s ({rule_d.display}): "
               + ("keine Eskalation" if lat == float("inf") else "Paarung nicht im Ensemble" if lat != lat
                  else f"{num(lat, 2)} s") + ". rein 2-aus-N: 0 s, sobald beide Quellen aktiv sind.")

    # 2.3 ────────────────────────────────────────────────────────────────────
    st.subheader("2.3  Allmählicher Aufbau der Evidenz")
    figure("C", title="C  Verzögerte Eskalation gleichzeitiger Doppel-Artefakte",
           text="Konservatives Modell aus Kapitel 5 (s_j = 1): Ein Doppel-Artefakt unterhalb des Quorums eskaliert "
                "nur, wenn es bis t_esc anhält; bei exponentialverteilter Dauer (Mittel μ_art) ist das der Anteil "
                "exp(−2·t_esc/μ_art). Die reine Zählregel eskaliert jedes Paar.")
    figure("C2", TAU, title="C2  Fenstereffekt: Artefakte und Krisen",
           text="Im Prototyp baut sich die Evidenz über das Fenster T linear auf (3.6); eine Episode eskaliert erst, "
                "wenn sie t_need = T·k/r (bzw. t_esc) überdauert. Links der Anteil eskalierter Artefakt-Episoden, "
                "rechts der Preis: der Anteil eskalierter Krisen-Episoden. "
                "*Idealisierung: Kanäle beginnen gleichzeitig, Dauern exponentialverteilt. Das Fenster wirkt wie "
                "eine Alarmverzögerung; fairer Vergleich ist k-aus-N mit gleicher Verzögerung τ.*")

    # 2.4 ────────────────────────────────────────────────────────────────────
    st.subheader("2.4  Anpassung an die Zahl der Geräte")
    figure("G2", title="G2  Fehlalarme und verpasste Krisen über |M|",
           text="Ein festes k passt nur zu einer Gerätezahl: 2-aus-N wird mit wachsendem |M| durchlässig, "
                "3-aus-N verpasst bei |M| = 3 mehr als jede vierte Krise. Das adaptive Modell leitet k aus |M| und "
                "den Prioritäten ab (3.17) und bleibt auf beiden Achsen im Zielbereich (n_H = |M| div 2). "
                "Bei |M| = 6–7 verpasst das adaptive Modell etwas mehr Krisen als 2-aus-N (≈ 0,2 statt 0,06 je "
                "1000), weil zwei Quellen niedriger Priorität das Quorum dort nicht mehr erreichen.")

    st.info("An der kalibrierten Station erreichen adaptiv und rein 2-aus-N dieselbe Decke 1 − c_art/|M|. "
            "Der Unterschied liegt in Priorität, Zeit und Robustheit, nicht in der Höhe der Reduktion.")

# ═════════════════════════════════════════════════════════════════════════════
else:
    st.subheader("Weitere Kurven")
    figure("E", title="E  ARR über c_art",
           text="Die Decke 1 − c_art/|M| kann kein Filter überschreiten, weil gemeinsam verursachte Artefakte alle "
                "Kanäle zugleich treffen. Adaptiv und rein 2-aus-N liegen an der kalibrierten Station beide auf "
                "der Decke.")
    figure("F", title="F  PPV und System-TPR über d_true und TPR_j",
           text="Vorhersagewert (gezählt: durchgezogen, zeitgewichtet: gestrichelt) und Sensitivität über der "
                "Krisendauer und der Kanalsensitivität; P(C) wird jeweils neu kalibriert.")
    figure("A", title="A  ARR und System-TPR über |M|·FPR, alle Regeln",
           text="Achsen auf 75–90 % (ARR) und 94–100 % (TPR) gespreizt, damit die kleinen Unterschiede zwischen "
                "den Regeln sichtbar werden.")

    st.subheader("Frei")
    st.markdown("Beliebiger Parameter auf der x-Achse, Kennzahl auf der y-Achse; optional eine Parameterfamilie "
                "als Linienschar oder als Band (min–max) um die aktuelle Kurve.")
    c1, c2, c3 = st.columns(3)
    x_name = c1.selectbox("x-Achse", list(bm.PARAM_INFO), format_func=lambda k: bm.PARAM_INFO[k]["label"],
                          index=list(bm.PARAM_INFO).index("c_art"))
    metric = c2.selectbox("y-Achse", list(bm.METRICS), index=0)
    family = c3.selectbox("Familie", ["keine"] + list(bm.FAMILY_VALUES),
                          format_func=lambda k: k if k == "keine" else
                          f"{bm.PARAM_INFO[k]['label']} ∈ {{{', '.join(fx.de(v) for v in bm.FAMILY_VALUES[k])}}}")
    c4, c5 = st.columns([2, 1])
    rule_labels = {"adaptiv": bm.ADAPTIV, "rein 2-aus-N": bm.rein(2), "rein 3-aus-N": bm.rein(3),
                   "Basis": bm.BASIS, "ohne Filter": bm.OHNE}
    chosen = c4.multiselect("Regeln", list(rule_labels), default=["adaptiv", "rein 2-aus-N", "ohne Filter"])
    band = c5.checkbox("Band min–max statt Linienschar", value=False)
    fam = None if family == "keine" else family
    if fam == x_name:
        st.warning("Familie und x-Achse sind derselbe Parameter; die Familie wird ignoriert.")
        fam = None
    if chosen:
        figure("sweep", x_name, metric, tuple(rule_labels[c] for c in chosen), fam, band)
    else:
        st.info("Mindestens eine Regel wählen.")

    st.markdown("**Quorum k_min über α**")
    st.markdown("α gewichtet den Spitzenterm (höchste Priorität) gegen die Summe der Prioritäten im SDC_score "
                "(3.16). Der Regler unten wirkt nur auf diese Tabelle und Abbildung, nicht auf das übrige Modell.")
    st.slider("α für die Kombinationstabelle", 0.0, 1.0, step=0.05, key="alpha_frei")
    a_frei = float(st.session_state["alpha_frei"])
    comb_a = bm.combination_rows(replace(P, alpha=a_frei), CAL)
    st.dataframe(pd.DataFrame([{"Quellen": r["label"], "SDC_score": num(r["sdc"], 3), "k_min": r["k"],
                                "adaptiv": r["branch"], "t_need [s]": num(r["t_need"], 1)}
                               for r in comb_a]).set_index("Quellen"))
    figure("kmin", a_frei)

    st.subheader("Export")
    st.markdown(f"Löscht die alten PNG/SVG in `{fx.FIG_DIR}` und schreibt den Satz mit den **aktuellen** "
                f"Reglerwerten neu (PNG 300 dpi + SVG, ohne Titel im Bild). Der Skriptaufruf "
                f"`python analysis/belastungsmodell_app/export_figures.py` tut dasselbe mit den Standardparametern.")
    if st.button("Abbildungen exportieren"):
        with st.spinner("Abbildungen werden erzeugt …"):
            removed, written = fx.export_all(P)
        st.success(f"{len(removed)} Dateien gelöscht, {len(written)} Dateien geschrieben:")
        st.code("\n".join(str(p) for p in written))
