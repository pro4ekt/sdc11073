"""ROC-Werkstatt: eine Abbildung aus Optionen (Darstellung, Szenario η, Filterkonfiguration(en), Regeln, Ergänzungen,
Achsen, Grenzwertfaktor h) – nur aus gerechneten Daten (``roc_alle.csv``, ``roc_tabellen.json`` und
``werkstatt/werkstatt.csv`` + ``werkstatt_h1.json``), ohne Replay.

Darstellungen: ``froc`` (erkannte Krisen über Fehleskalationen, wie R2/R4b), ``roc`` (ROC nach Zeitanteil, wie R1a/R4a),
``theorie`` (Theorie gegen Messung je Regel, wie R1b), ``uebertragung`` (FPR_sys über FPR̄ der Geräte, log-log, wie R3).
Farben: adaptiv #0F8A8A (Kurvenschar: Abstufungen), 2-aus-4 #E07B39, 3-aus-4 #7E5BB5; Dezimalkomma; Titel nur auf Wunsch.
Mit der Basis (T = 10 s, α = 0,5, δ_min = 0,5) und denselben Schaltern zeigen die Darstellungen dieselben Punkte wie
R1a, R2_v2/R2_p07, R1b, R3 und R4b (Kurvenschar T).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

from analysis.belastungsmodell_app import export_figures as fx  # noqa: E402
from analysis.belastungsmodell_app import sim_data as sd  # noqa: E402

from . import figures_roc as fr  # noqa: E402
from . import metrics as mt  # noqa: E402
from . import run_roc as rr  # noqa: E402
from . import werkstatt as ws  # noqa: E402
from . import kalibrierung as kb  # noqa: E402

W = fx.W
de = fx.de
GREY = fx.GREY
INK = fx.INK
DEV_COLOR = fr.DEV_COLOR
CHORD_COLOR = "#6b6b66"
HULL_LABEL2 = "beste Mischung fester k-aus-4-Regeln\n(obere konvexe Hülle aller k und h)"

COLORS: Dict[str, str] = {"adaptiv": "#0F8A8A", "2-aus-4": "#E07B39", "3-aus-4": "#7E5BB5",
                          "1-aus-4": "#1baf7a", "4-aus-4": "#e34948"}
LW: Dict[str, float] = {"adaptiv": 2.8, "2-aus-4": 2.2, "3-aus-4": 2.2, "1-aus-4": 1.1, "4-aus-4": 1.1}
K_RULES: Tuple[str, ...] = ("1-aus-4", "2-aus-4", "3-aus-4", "4-aus-4")
ALL_RULES: Tuple[str, ...] = ("adaptiv", "2-aus-4", "3-aus-4", "1-aus-4", "4-aus-4")
VIEWS: Dict[str, str] = {
    "froc": "erkannte Krisen über Fehlmeldungen",
    "klassisch": "klassische ROC des Filters (Schwelle über P0)",
    "roc": "ROC nach Zeitanteil",
    "theorie": "Theorie gegen Messung",
    "uebertragung": "Übertragung Gerät → System (Steigungen)",
    "geraet_system": "Gerät → System",
    "zeit_T": "Zeit-ROC × Fenster T",
}
ETAS: Dict[str, str] = {"0,9": "η = 0,9", "0,7": "η = 0,7", "val 0,9": "η = 0,9 (Validierung, Seed 8)",
                        "val 0,7": "η = 0,7 (Validierung, Seed 9)", "beide": "beide nebeneinander", "beide val": "beide Validierungen"}
X_UNITS: Dict[str, str] = {"ereignis": "eskalierte Fehlalarm-Episoden (%)", "h": "je Bett und Stunde", "tag": "je Bett und Tag"}
X_UNITS_KLASSISCH: Dict[str, str] = {"ereignis": "eskalierte Fehlalarm-Episoden (%)", "zeit": "krisenfreie Zeit mit Eskalation (%)",
                                     "h": "Fehleskalationen je Bett und Stunde"}
MODES: Dict[str, str] = {"mild": "mild (Rot berührt die Krise bis Ende + 10 s)", "streng": "streng (Rot innerhalb der Krise)"}
PARAM_SETS: Tuple[str, ...] = ("Prototyp", "kalibriert")
X_EVENT = "eskalierte Fehlalarm-Episoden (%)"
NETZ_RUN_OF_ETA: Dict[str, str] = {"0,9": "12h", "0,7": "A"}       # Netzlauf, dessen Journale zum Szenario gehören
P0_ANNOT: Tuple[float, ...] = (0.001, 0.5, 0.999)
PARAMS: Dict[str, str] = {"T": "Fenster T", "alpha": "α", "delta_min": "δ_min"}
HOWTO: Dict[str, str] = {
    "froc": "Nach Stück: x = Fehleskalationen (Rot-Episoden ohne Krise ± 10 s), y = erkannte Krisen (Rot berührt die Krise bis "
            "Ende + 10 s). Jeder Punkt einer Kurve ist ein Grenzwertfaktor h; die Raute markiert das gewählte h. Je weiter links "
            "oben, desto besser; die graue Hülle ist die beste Mischung fester k-aus-4-Regeln, die Sehne verbindet 2- und 3-aus-4 "
            "desselben h. Standard-x ist der Anteil eskalierter Fehlalarm-Episoden in Prozent (Fehlalarm-Episode = Vereinigung "
            "überlappender Fehlalarme der Geräte eines Patienten ohne Krise ± 10 s; eskaliert, wenn ein falscher Rot-Abschnitt der "
            "Regel sie schneidet); die Kurven werden mit (0, 0) und (100, 100) ergänzt, die AUC steht in der Legende (Trapezregel). "
            "Je Bett·h / je Tag bleiben umschaltbar.",
    "zeit_T": ("Die Zeit-ROC misst Erkennung und Latenz zugleich: ein kürzeres Fenster schiebt den Filter nach oben, aber auch "
               "nach rechts. x = FPR_sys nach Zeit (falsche Rot-Zeit / stabile Zeit, Variante ii), y = TPR_sys nach Zeit (Rot-Zeit "
               "in Krisen / Krisenzeit); je Fenster T eine Kurve über h (Abstufungen von #0F8A8A: T = 10 s Grundfarbe, kürzer heller, "
               "länger dunkler), Raute = gewähltes h. adaptiv mit α = 0,5, δ_min = 0,5, P0 = 0,5; Daten nur für η = 0,9 und 0,7."),
    "klassisch": "Gerätegrenzwerte fest (Faktor h); die Schwelle des adaptiven Filters wandert über den klinischen Kontext: "
                 "P0 ∈ {0,001 … 0,999}, Context_Log_Odds = ln(P0/(1 − P0)), Θ_target = max(ε, Θ_base·δ − Context_Log_Odds). Jeder "
                 "Punkt der Kurve ist ein P0, die Raute P0 = 0,5 (Prototyp-Kontext). k-aus-4 hat bei festem h keinen Regler und "
                 "liefert vier Punkte k = 1 … 4, die zum Linienzug verbunden sind. Beide enthalten (0, 0) und (100, 100); AUC nach der "
                 "Trapezregel; Intervalle aus dem Patienten-Bootstrap in der Tabelle.",
    "roc": "Nach Zeit: x = Anteil der krisenfreien Zeit mit Fehleskalation (Variante ii), y = Anteil der Krisenzeit mit Eskalation. "
           "Die Wartezeit des Fensters T kostet adaptiv Krisenzeit (y), nicht erkannte Krisen (siehe Darstellung nach Stück).",
    "theorie": "Je Regel ein Feld: Messung (Simulation, Dashboard-Kern) durchgezogen, Modell E1+E2 strichpunktiert, Binomialformel "
               "mit p = TPR_j bzw. FPR_j nach Zeit grau gestrichelt (nur k-aus-4). Modell E1+E2 liegt nur für die Basis α = 0,5, "
               "δ_min = 0,5 vor.",
    "uebertragung": "x = mittlere Fehlalarmzeit der Geräte FPR̄, y = Fehleskalationszeit des Systems (Variante ii), beide "
                    "logarithmisch. Die Steigung bei FPR̄ ≤ 0,03 (kleinste Quadrate) sagt, in welcher Potenz Geräterauschen "
                    "durchschlägt: 1 = linear, 2 = quadratisch (Paare), 3 = Tripel; Orientierungslinien durch den Punkt von 2-aus-4.",
    "geraet_system": "h verschiebt die Kenngrößen der Geräte (links); die beiden rechten Bilder zeigen, was davon je Regel an der "
                     "Zentrale ankommt.",
}
H_CMAP = "viridis"
Y_TIME, X_TIME, Y_COUNT = fr.Y_TIME, fr.X_TIME, fr.Y_COUNT
LOW_NOISE = rr.LOW_NOISE


# ─────────────────────────────────────────────────────────────────────────────
# Optionen und Datensätze
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Options:
    view: str = "froc"
    eta: str = "0,9"                      # "0,9" | "0,7" | "beide"
    T: float = 10.0
    alpha: float = 0.5
    delta_min: float = 0.5
    schar: Optional[str] = None           # None | "T" | "alpha" | "delta_min"
    rules: Tuple[str, ...] = ("adaptiv", "2-aus-4", "3-aus-4")
    devices: bool = False
    hull: bool = True
    chord: bool = False
    model_e: bool = False
    binom: bool = False
    netz: bool = True
    ci: bool = False
    x_unit: str = "h"                     # "h" | "tag"  (nur froc)
    log_x: bool = False
    zoom: Tuple[Optional[float], Optional[float], Optional[float], Optional[float]] = (None, None, None, None)
    h: float = 1.0
    title: bool = False
    mode: str = "mild"                    # "mild" | "streng" (y nach Stück / Ereignis)
    compare_params: bool = False          # klassisch: Prototyp und kalibriert gemeinsam
    kal: Optional[Tuple[float, float, float]] = None   # Konfiguration „kalibriert“ (Beschriftung)
    zeit_Ts: Tuple[float, ...] = (5.0, 8.0, 10.0, 15.0, 20.0, 30.0)   # Zeit-ROC × Fenster T: gezeigte Fenster
    zeit_zoom: bool = False                                          # Zeit-ROC × Fenster T: FPR_sys ≤ 3 %


@dataclass
class Dataset:
    eta: str
    rows: List[dict]                       # roc_alle.csv (run_roc.load_rows)
    tables: dict                           # roc_tabellen.json
    werk: Optional[dict] = None            # werkstatt.load(...)
    episoden: List[dict] = field(default_factory=list)              # kalibrierung/episoden.csv (dieses η)
    klassisch: Dict[str, List[dict]] = field(default_factory=dict)  # Konfiguration → Zeilen klassisch_<key>.csv (dieses η)
    hkurve: Dict[str, List[dict]] = field(default_factory=dict)     # Konfiguration → adaptiv-Zeilen hkurve_<key>.csv
    netz: List[dict] = field(default_factory=list)                  # netz_<Prototyp>.csv des zugehörigen Netzlaufs
    netz_kal: List[dict] = field(default_factory=list)              # netz_<kalibriert>.csv (adaptiv offline)
    boot: List[dict] = field(default_factory=list)                  # pruefung.json → bootstrap (dieses η)
    kal: Optional[Tuple[float, float, float]] = None


@dataclass
class Series:
    """Eine Kurve über h in einheitlichen Feldern."""
    name: str
    label: str
    color: str
    lw: float
    pts: List[dict] = field(default_factory=list)     # je h: h, x_count, y_count, y_strict, tpr, fpr_ii, fpr_dev, n_krisen, n_falsch, bett_h, lat_med, lat_p90, hvl
    config: Optional[Tuple[float, float, float]] = None

    def at(self, h: float) -> Optional[dict]:
        for p in self.pts:
            if abs(p["h"] - h) < 1e-9:
                return p
        return None

    def arr(self, xkey: str, ykey: str, xscale: float = 1.0, yscale: float = 1.0) -> Tuple[np.ndarray, np.ndarray]:
        x = np.array([p.get(xkey) if p.get(xkey) is not None else np.nan for p in self.pts], dtype=float) * xscale
        y = np.array([p.get(ykey) if p.get(ykey) is not None else np.nan for p in self.pts], dtype=float) * yscale
        ok = np.isfinite(x) & np.isfinite(y)
        return x[ok], y[ok]


def _hex_to_rgb(c: str) -> Tuple[float, float, float]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))     # type: ignore[return-value]


def _blend(c: str, other: Tuple[float, float, float], f: float) -> str:
    r, g, b = _hex_to_rgb(c)
    rr_, gg, bb = (r + (other[0] - r) * f, g + (other[1] - g) * f, b + (other[2] - b) * f)
    return "#%02x%02x%02x" % (round(rr_ * 255), round(gg * 255), round(bb * 255))


def shades(n: int, base: str = COLORS["adaptiv"]) -> List[str]:
    """n Abstufungen der adaptiv-Farbe von hell (kleinster Wert) nach dunkel (größter Wert)."""
    if n <= 1:
        return [base]
    out = []
    for i in range(n):
        f = i / (n - 1)
        out.append(_blend(base, (1.0, 1.0, 1.0), (0.5 - f) * 1.1) if f < 0.5 else _blend(base, (0.0, 0.0, 0.0), (f - 0.5) * 0.8))
    return out


def _fpr_dev_by_h(rows: Sequence[dict]) -> Dict[float, float]:
    out: Dict[float, float] = {}
    for r in rows:
        if r.get("art") == "Regel" and r.get("fpr_j_mittel") is not None and r.get("h") is not None:
            out.setdefault(round(float(r["h"]), 6), float(r["fpr_j_mittel"]))
    return out


def _pt_from_rule_row(r: dict, fpr_dev: Dict[float, float]) -> dict:
    n_k = r.get("n_krisen")
    erk = r.get("eta_oder_erkannt")
    return dict(h=float(r["h"]), x_count=r.get("falsch_je_bett_h"), y_count=erk, y_strict=r.get("erkannt_streng"),
                tpr=r.get("tpr"), fpr_ii=r.get("fpr_ii"), fpr_dev=fpr_dev.get(round(float(r["h"]), 6), r.get("fpr_j_mittel")),
                n_krisen=n_k, n_falsch=r.get("n_falsch"), n_det=(round(erk * n_k) if erk is not None and n_k else None),
                n_det_strict=(round(r["erkannt_streng"] * n_k) if r.get("erkannt_streng") is not None and n_k else None),
                bett_h=None, lat_med=r.get("verz_kreuz_median_s"), lat_p90=r.get("verz_kreuz_p90_s"), hvl=r.get("hvl_anteil"),
                x_ep=None, ep_n=None, ep_k=None)


def _pt_from_ws_row(r: dict, fpr_dev: Dict[float, float]) -> dict:
    return dict(h=float(r["h"]), x_count=r.get("falsch_je_bett_h"), y_count=r.get("erkannt"), y_strict=r.get("erkannt_streng"),
                tpr=r.get("tpr"), fpr_ii=r.get("fpr_ii"), fpr_dev=fpr_dev.get(round(float(r["h"]), 6)),
                n_krisen=r.get("n_krisen"), n_falsch=r.get("n_falsch"), n_det=r.get("n_erkannt"), bett_h=r.get("bett_h"),
                n_det_strict=r.get("n_erkannt_streng"),
                lat_med=r.get("verz_kreuz_median_s"), lat_p90=r.get("verz_kreuz_p90_s"), hvl=r.get("hvl_anteil"),
                x_ep=None, ep_n=None, ep_k=None)


def rule_series(ds: Dataset, name: str) -> Series:
    fpr_dev = _fpr_dev_by_h(ds.rows)
    pts = [_pt_from_rule_row(r, fpr_dev) for r in fr.curve(ds.rows, "Regel", name, None)]
    _attach_ep(pts, ds, name)
    return Series(name=name, label=name, color=COLORS[name], lw=LW[name], pts=pts)


def device_series(ds: Dataset) -> List[Series]:
    fpr_dev = _fpr_dev_by_h(ds.rows)
    out = []
    for dev in fr.DEVICES:
        pts = [_pt_from_rule_row(r, fpr_dev) for r in fr.curve(ds.rows, "Geraet", dev)]
        out.append(Series(name=dev, label=dev, color=DEV_COLOR, lw=0.9, pts=pts))
    return out


def adaptiv_series(ds: Dataset, T: float, alpha: float, delta_min: float, color: str = COLORS["adaptiv"],
                   label: Optional[str] = None) -> Optional[Series]:
    """Kurve einer adaptiv-Konfiguration: aus der Werkstatt-Datei, ersatzweise (Basis α/δ_min) aus roc_alle.csv."""
    fpr_dev = _fpr_dev_by_h(ds.rows)
    pts: List[dict] = []
    if ds.werk is not None:
        pts = [_pt_from_ws_row(r, fpr_dev) for r in ws.rows_for(ds.werk["rows"], T, alpha, delta_min)]
    if not pts and abs(alpha - ws.BASE[1]) < 1e-9 and abs(delta_min - ws.BASE[2]) < 1e-9:
        pts = [_pt_from_rule_row(r, fpr_dev) for r in fr.curve(ds.rows, "Regel", "adaptiv", T)]
    if not pts and ds.hkurve.get(ws.key(T, alpha, delta_min)):            # Kurve über h der Konfiguration „kalibriert“
        pts = [_pt_from_ws_row(r, fpr_dev) for r in ws.rows_for(ds.hkurve[ws.key(T, alpha, delta_min)], T, alpha, delta_min)]
    if not pts:
        return None
    _attach_ep(pts, ds, ws.key(T, alpha, delta_min))
    return Series(name="adaptiv", label=label or ws.label(T, alpha, delta_min), color=color, lw=LW["adaptiv"], pts=pts,
                  config=(T, alpha, delta_min))


def configs_for(opt: Options, datasets: Sequence[Dataset]) -> List[Tuple[Tuple[float, float, float], str, str]]:
    """[(Konfiguration, Farbe, Legendentext)] – eine Konfiguration oder die Kurvenschar eines Parameters."""
    if opt.schar is None:
        return [((opt.T, opt.alpha, opt.delta_min), COLORS["adaptiv"], config_label((opt.T, opt.alpha, opt.delta_min), opt.kal))]
    vals: List[float] = []
    for ds in datasets:
        if ds.werk is not None:
            vals = ws.values_of(ds.werk["rows"], opt.schar)
            if vals:
                break
    if not vals:
        vals = fr.available_T(datasets[0].rows) if opt.schar == "T" else [ws.BASE[("T", "alpha", "delta_min").index(opt.schar)]]
    cols = shades(len(vals))
    out = []
    sym = {"T": "T", "alpha": "α", "delta_min": "δ_min"}[opt.schar]
    for v, c in zip(vals, cols):
        cfg = tuple(v if n == opt.schar else b for n, b in zip(("T", "alpha", "delta_min"), ws.BASE))
        val = f"{de(v)} s" if opt.schar == "T" else de(v, 1)
        out.append((cfg, c, f"adaptiv, {sym} = {val}"))
    return out


def visible_series(ds: Dataset, opt: Options, cfgs: Sequence[Tuple[Tuple[float, float, float], str, str]]) -> List[Series]:
    out: List[Series] = []
    for name in K_RULES:
        if name in opt.rules:
            out.append(rule_series(ds, name))
    if "adaptiv" in opt.rules:
        for cfg, color, lab in cfgs:
            s = adaptiv_series(ds, *cfg, color=color, label=lab)
            if s is not None:
                out.append(s)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Zeichnen
# ─────────────────────────────────────────────────────────────────────────────

def _h_marker(ax, x: float, y: float, color: str, size: float = 6.0) -> None:
    ax.plot([x], [y], marker="D", ms=size, color=color, mec="white", mew=0.9, zorder=8)


def _ci_count(ax, p: dict, xscale: float, color: str, ykey: str = "y_count") -> None:
    """Fehlerbalken am h-Punkt (nach Stück): Poisson für Fehleskalationen, Wilson für erkannte Krisen."""
    if p.get("x_count") is None or p.get(ykey) is None:
        return
    xlo = xhi = None
    if p.get("n_falsch") is not None and p.get("bett_h"):
        xlo, xhi = sd.poisson_ci(int(p["n_falsch"]), float(p["bett_h"]))
    elif p.get("n_falsch") is not None and p.get("x_count"):
        exposure = p["n_falsch"] / p["x_count"] if p["x_count"] > 0 else None
        if exposure:
            xlo, xhi = sd.poisson_ci(int(p["n_falsch"]), exposure)
    ylo = yhi = None
    nd = p.get("n_det") if ykey == "y_count" else p.get("n_det_strict")
    if nd is not None and p.get("n_krisen"):
        ylo, yhi = rr.wilson_ci(int(nd), int(p["n_krisen"]))
    x, y = p["x_count"] * xscale, 100 * p[ykey]
    xerr = None if xlo is None else [[max(0.0, x - xlo * xscale)], [max(0.0, xhi * xscale - x)]]
    yerr = None if ylo is None else [[max(0.0, y - 100 * ylo)], [max(0.0, 100 * yhi - y)]]
    ax.errorbar([x], [y], xerr=xerr, yerr=yerr, fmt="none", ecolor=color, elinewidth=1.0, capsize=2.5, zorder=7)


def _apply_zoom(ax, opt: Options, xdef: Tuple[float, float], ydef: Tuple[float, float], log_x: bool) -> None:
    xmin, xmax, ymin, ymax = opt.zoom
    lo = xdef[0] if xmin is None else xmin
    hi = xdef[1] if xmax is None else xmax
    if log_x:
        lo = max(lo, 1e-9) if lo and lo > 0 else xdef[0]
    ax.set_xlim(lo, hi)
    ax.set_ylim(ydef[0] if ymin is None else ymin, ydef[1] if ymax is None else ymax)


def _chord(ax, s2: Optional[Series], s3: Optional[Series], h: float, xkey: str, ykey: str, xscale: float, yscale: float,
           xa: Optional[float] = None) -> bool:
    if s2 is None or s3 is None:
        return False
    p2, p3 = s2.at(h), s3.at(h)
    if not p2 or not p3 or p2.get(xkey) is None or p3.get(xkey) is None or p2.get(ykey) is None or p3.get(ykey) is None:
        return False
    x2, y2, x3, y3 = p2[xkey] * xscale, p2[ykey] * yscale, p3[xkey] * xscale, p3[ykey] * yscale
    xs = [x3, x2] + ([xa * xscale] if xa is not None else [])
    lo, hi = min(xs), max(xs)
    if x2 == x3:
        return False
    f = lambda x: y3 + (y2 - y3) * (x - x3) / (x2 - x3)   # noqa: E731
    ax.plot([lo, hi], [f(lo), f(hi)], color=CHORD_COLOR, lw=1.0, ls=(0, (1.5, 2.0)), zorder=3)
    ax.plot([x2, x3], [y2, y3], ls="none", marker="o", ms=3.0, color=CHORD_COLOR, zorder=3)
    return True


def _draw_count(ax, ds: Dataset, opt: Options, cfgs) -> List[Line2D]:
    xkey, ykey, xs = _xy_keys(opt)
    ev = opt.x_unit == "ereignis"
    handles: List[Line2D] = []
    series = visible_series(ds, opt, cfgs)
    if opt.hull and not ev:
        hp = fr.hull_points(ds.tables, "stueck")
        if hp:
            ax.plot([p[0] * xs for p in hp], [100 * p[1] for p in hp], color=GREY, lw=1.3, ls="--", zorder=2)
            handles.append(Line2D([], [], color=GREY, lw=1.3, ls="--", label=HULL_LABEL2))
    if opt.devices and not ev:
        for s in device_series(ds):
            x, y = s.arr("x_count", ykey, xs, 100.0)
            ax.plot(x, y, color=DEV_COLOR, lw=0.9, zorder=2)
        handles.append(Line2D([], [], color=DEV_COLOR, lw=0.9, label="Einzelgeräte\n(Fehlalarme je Bett·h, Teilnahme η_j)"))
    for s in series:
        x, y = s.arr(xkey, ykey, xs, 100.0)
        lab = s.label
        if ev and x.size:
            order = np.argsort(x, kind="stable")
            x, y = x[order], y[order]
            _endpoints(ax, x, y, s.color)
            lab += f"\n(AUC {de(kb.auc_points(zip(x, y)), 3)})"
        ax.plot(x, y, color=s.color, lw=s.lw, ls="-", marker="o", ms=2.4, mew=0, zorder=4)
        handles.append(Line2D([], [], color=s.color, lw=s.lw, marker="o", ms=2.4, mew=0, label=lab))
        p = s.at(opt.h)
        if p and p.get(xkey) is not None and p.get(ykey) is not None:
            _h_marker(ax, p[xkey] * xs, 100 * p[ykey], s.color)
            if opt.ci:
                if ev:
                    _ci_event(ax, p, s.color, ykey)
                else:
                    _ci_count(ax, p, xs, s.color, ykey)
    if opt.chord:
        s2 = next((s for s in series if s.name == "2-aus-4"), None) or rule_series(ds, "2-aus-4")
        s3 = next((s for s in series if s.name == "3-aus-4"), None) or rule_series(ds, "3-aus-4")
        xa = next((s.at(opt.h)[xkey] for s in series if s.name == "adaptiv" and s.at(opt.h)), None)
        if _chord(ax, s2, s3, opt.h, xkey, ykey, xs, 100.0, xa):
            handles.append(Line2D([], [], color=CHORD_COLOR, lw=1.0, ls=(0, (1.5, 2.0)), marker="o", ms=3.0,
                                  label=f"Sehne 2-/3-aus-4 bei h = {de(opt.h)}"))
    if opt.model_e and not ev:
        drawn = False
        for s in series:
            if s.name in ("2-aus-4", "3-aus-4"):
                x, y = fr.model_e_count_curve(ds.rows, ds.tables, s.name)
            elif s.config and abs(s.config[1] - ws.BASE[1]) < 1e-9 and abs(s.config[2] - ws.BASE[2]) < 1e-9:
                x, y = fr.model_e_count_curve(ds.rows, ds.tables, "adaptiv", s.config[0])
            else:
                continue
            if x.size:
                order = np.argsort(x)
                ax.plot(x[order] * xs, y[order], color=s.color, lw=1.4, ls="-.", zorder=3)
                drawn = True
        if drawn:
            handles.append(Line2D([], [], color=INK, lw=1.4, ls="-.", label=fr.MODEL_E_LABEL))
    if opt.binom and not ev:
        etas = fr._etas_by_h(ds.rows)
        drawn = False
        for s in series:
            if s.name in K_RULES:
                k = int(s.name[0])
                pts = sorted((p["x_count"] * xs, 100.0 * fr.binom_participation(etas[round(p["h"], 6)], k)) for p in s.pts
                             if p.get("x_count") is not None and round(p["h"], 6) in etas)
                if pts:
                    ax.plot([a for a, _ in pts], [b for _, b in pts], color=GREY, lw=1.0, ls="--", zorder=2)
                    drawn = True
        if drawn:
            handles.append(Line2D([], [], color=GREY, lw=1.0, ls="--", label="Binomialformel der Teilnahme\n(unabhängige Geräte, η_j)"))
    if opt.netz:
        ycol = "erkannt" if ykey == "y_count" else "erkannt_streng"
        for s in series:
            if s.name not in fr.NET_RULES:
                continue
            base = s.name != "adaptiv" or (s.config and all(abs(a - b) < 1e-9 for a, b in zip(s.config, ws.BASE)))
            is_kal = s.name == "adaptiv" and s.config is not None and ds.kal is not None and tuple(s.config) == tuple(ds.kal)
            if ev:
                n = _netz_row(ds, "Dashboard" if s.name == "adaptiv" else s.name) if base else (_netz_row(ds, "adaptiv", True) if is_kal else None)
                if n is not None and n.get("ep_anteil") is not None and n.get(ycol) is not None:
                    fr._marker_net(ax, 100 * n["ep_anteil"], 100 * n[ycol], s.color)
            elif base:
                n = fr._net(ds.rows, s.name)
                if n is not None and n.get("falsch_je_bett_h") is not None and n.get("eta_oder_erkannt") is not None and ykey == "y_count":
                    fr._marker_net(ax, n["falsch_je_bett_h"] * xs, 100 * n["eta_oder_erkannt"], s.color)
            elif is_kal:
                n = _netz_row(ds, "adaptiv", True)
                if n is not None and n.get("falsch_je_bett_h") is not None and n.get(ycol) is not None:
                    fr._marker_net(ax, n["falsch_je_bett_h"] * xs, 100 * n[ycol], s.color)
        handles.append(Line2D([], [], color="white", marker="*", ms=12, mec="black", mew=0.7, ls="none",
                              label=fr.net_label(ds.rows) + ("; kalibriert offline" if ds.netz_kal else "")))
    handles.append(Line2D([], [], color="white", marker="D", ms=6.0, mec=INK, mew=0.9, ls="none", label=f"Grenzwertfaktor h = {de(opt.h)}"))
    ylabel = Y_COUNT if opt.mode == "mild" else "erkannte Krisen (%), streng"
    if ev:
        if opt.log_x:
            ax.set_xscale("log")
            xs_all = [p["x_ep"] for s in series for p in s.pts if p.get("x_ep")]
            lo = max(0.05, min(xs_all) * 0.7) if xs_all else 0.1
            _apply_zoom(ax, opt, (lo, 100.0), (0.0, 102.0), True)
            fr.log_axis(ax, "x", *ax.get_xlim())
            ax.set_xlabel(X_EVENT + ", logarithmisch", fontsize=8.5)
            fx.de_axes(ax, x=False, y=True)
        else:
            _apply_zoom(ax, opt, (0.0, 100.0), (0.0, 102.0), False)
            ax.set_xlabel(X_EVENT, fontsize=8.5)
            fx.de_axes(ax, x=True, y=True)
    else:
        xlabel = "Fehleskalationen " + X_UNITS[opt.x_unit]
        if opt.log_x:
            ax.set_xscale("log")
            xs_all = [p["x_count"] * xs for s in series for p in s.pts if p.get("x_count")]
            lo, hi = (min(xs_all) * 0.7, max(xs_all) * 1.4) if xs_all else (0.01, 100.0)
            _apply_zoom(ax, opt, (lo, hi), (0.0, 102.0), True)
            fr.log_axis(ax, "x", *ax.get_xlim())
            ax.set_xlabel(xlabel + ", logarithmisch", fontsize=8.5)
            fx.de_axes(ax, x=False, y=True)
        else:
            _apply_zoom(ax, opt, (0.0, fr.XMAX_COUNT * xs), (0.0, 102.0), False)
            ax.set_xlabel(xlabel + f", linear bis {de(ax.get_xlim()[1])}", fontsize=8.5)
            fx.de_axes(ax, x=True, y=True)
    ax.set_ylabel(ylabel, fontsize=8.5)
    fr._grid(ax)
    return handles


def _draw_time(ax, ds: Dataset, opt: Options, cfgs) -> List[Line2D]:
    handles: List[Line2D] = []
    series = visible_series(ds, opt, cfgs)
    if opt.hull:
        hp = fr.hull_points(ds.tables, "zeit")
        if hp:
            ax.plot([p[0] for p in hp], [p[1] for p in hp], color=GREY, lw=1.3, ls="--", zorder=2)
            handles.append(Line2D([], [], color=GREY, lw=1.3, ls="--", label=HULL_LABEL2))
    if opt.devices:
        for s in device_series(ds):
            x, y = s.arr("fpr_ii", "tpr")
            ax.plot(x, y, color=DEV_COLOR, lw=0.9, zorder=2)
        handles.append(Line2D([], [], color=DEV_COLOR, lw=0.9, label="Einzelgeräte\n(Anteil der Zeit mit Gerätealarm)"))
    for s in series:
        x, y = s.arr("fpr_ii", "tpr")
        ax.plot(x, y, color=s.color, lw=s.lw, ls="-", marker="o", ms=2.4, mew=0, zorder=4)
        handles.append(Line2D([], [], color=s.color, lw=s.lw, marker="o", ms=2.4, mew=0, label=s.label))
        p = s.at(opt.h)
        if p and p.get("fpr_ii") is not None and p.get("tpr") is not None:
            _h_marker(ax, p["fpr_ii"], p["tpr"], s.color)
    if opt.chord:
        s2 = next((s for s in series if s.name == "2-aus-4"), None) or rule_series(ds, "2-aus-4")
        s3 = next((s for s in series if s.name == "3-aus-4"), None) or rule_series(ds, "3-aus-4")
        xa = next((s.at(opt.h)["fpr_ii"] for s in series if s.name == "adaptiv" and s.at(opt.h)), None)
        if _chord(ax, s2, s3, opt.h, "fpr_ii", "tpr", 1.0, 1.0, xa):
            handles.append(Line2D([], [], color=CHORD_COLOR, lw=1.0, ls=(0, (1.5, 2.0)), marker="o", ms=3.0,
                                  label=f"Sehne 2-/3-aus-4 bei h = {de(opt.h)}"))
    if opt.model_e:
        drawn = False
        for s in series:
            if s.name in ("2-aus-4", "3-aus-4"):
                x, y = fr.xy(fr.curve(ds.rows, "ModellE", s.name), "fpr_ii", "tpr")
            elif s.config and abs(s.config[1] - ws.BASE[1]) < 1e-9 and abs(s.config[2] - ws.BASE[2]) < 1e-9:
                x, y = fr.xy(fr.curve(ds.rows, "ModellE", "adaptiv", s.config[0]), "fpr_ii", "tpr")
            else:
                continue
            if x.size:
                ax.plot(x, y, color=s.color, lw=1.4, ls="-.", zorder=3)
                drawn = True
        if drawn:
            handles.append(Line2D([], [], color=INK, lw=1.4, ls="-.", label="Modell E1+E2\n(versetzter Einstieg; Episoden nach m)"))
    if opt.binom:
        drawn = False
        for s in series:
            if s.name in K_RULES:
                x, y = fr.xy(fr.curve(ds.rows, "Formel", s.name), "fpr", "tpr")
                if x.size:
                    ax.plot(x, y, color=GREY, lw=1.0, ls="--", zorder=2)
                    drawn = True
        if drawn:
            handles.append(Line2D([], [], color=GREY, lw=1.0, ls="--", label=fr.BINOM_LABEL + "\nmit p = TPR_j bzw. FPR_j nach Zeit"))
    if opt.netz:
        for s in series:
            if s.name in fr.NET_RULES and (s.name != "adaptiv" or (s.config and all(abs(a - b) < 1e-9 for a, b in zip(s.config, ws.BASE)))):
                n = fr._net(ds.rows, s.name)
                if n is not None and n.get("fpr_ii") is not None and n.get("tpr") is not None:
                    fr._marker_net(ax, n["fpr_ii"], n["tpr"], s.color)
        handles.append(Line2D([], [], color="white", marker="*", ms=12, mec="black", mew=0.7, ls="none", label=fr.net_label(ds.rows)))
    handles.append(Line2D([], [], color="white", marker="D", ms=6.0, mec=INK, mew=0.9, ls="none", label=f"Grenzwertfaktor h = {de(opt.h)}"))
    if opt.log_x:
        ax.set_xscale("log")
        xs_all = [p["fpr_ii"] for s in series for p in s.pts if p.get("fpr_ii")]
        lo = min(xs_all) * 0.7 if xs_all else 1e-4
        _apply_zoom(ax, opt, (lo, 1.0), (0.0, 1.0), True)
        fr.log_axis(ax, "x", *ax.get_xlim(), max_labels=6 if opt.eta != "beide" else 4)
        ax.set_xlabel(X_TIME + ", logarithmisch", fontsize=8.5)
        fx.de_axes(ax, x=False, y=True)
    else:
        _apply_zoom(ax, opt, (0.0, fr.XMAX_LIN), (0.0, 1.0), False)
        ax.set_xlabel(X_TIME + f", linear bis {de(ax.get_xlim()[1])}", fontsize=8.5)
        fx.de_axes(ax, x=True, y=True)
    ax.set_ylabel(Y_TIME, fontsize=8.5)
    fr._grid(ax)
    return handles


def _draw_theorie_panel(ax, ds: Dataset, opt: Options, s: Series) -> None:
    x, y = s.arr("fpr_ii", "tpr")
    ax.plot(x, y, color=s.color, lw=s.lw, ls="-", marker="o", ms=2.4, mew=0, zorder=4)
    if s.name in K_RULES:
        xm, ym = fr.xy(fr.curve(ds.rows, "ModellE", s.name), "fpr_ii", "tpr")
        xf, yf = fr.xy(fr.curve(ds.rows, "Formel", s.name), "fpr", "tpr")
        if xf.size:
            ax.plot(xf, yf, color=GREY, lw=1.0, ls="--", zorder=2)
    elif s.config and abs(s.config[1] - ws.BASE[1]) < 1e-9 and abs(s.config[2] - ws.BASE[2]) < 1e-9:
        xm, ym = fr.xy(fr.curve(ds.rows, "ModellE", "adaptiv", s.config[0]), "fpr_ii", "tpr")
    else:
        xm, ym = np.array([]), np.array([])
    if xm.size:
        ax.plot(xm, ym, color=s.color, lw=1.5, ls="-.", zorder=3)
    p = s.at(opt.h)
    if p and p.get("fpr_ii") is not None and p.get("tpr") is not None:
        _h_marker(ax, p["fpr_ii"], p["tpr"], s.color)
    xs_all = [q["fpr_ii"] for q in s.pts if q.get("fpr_ii")]
    if opt.log_x:
        ax.set_xscale("log")
        lo = min(xs_all) * 0.7 if xs_all else 1e-5
        _apply_zoom(ax, opt, (lo, 1.0), (0.0, 1.0), True)
        fr.log_axis(ax, "x", *ax.get_xlim(), max_labels=4)
        fx.de_axes(ax, x=False, y=True)
    else:
        _apply_zoom(ax, opt, (0.0, fr.XMAX_LIN), (0.0, 1.0), False)
        fx.de_axes(ax, x=True, y=True)
    ax.set_title(s.label if s.name != "adaptiv" else s.label.replace("adaptiv (", "adaptiv\n(", 1), fontsize=8.5)
    fr._grid(ax)


def _draw_uebertragung(ax, ds: Dataset, opt: Options, cfgs) -> List[Line2D]:
    handles: List[Line2D] = []
    series = visible_series(ds, opt, cfgs)
    xs_all: List[float] = []
    ys_all: List[float] = []
    anchor = None
    for s in series:
        x, y = s.arr("fpr_dev", "fpr_ii")
        ok = (x > 0) & (y > 0)
        x, y = x[ok], y[ok]
        xs_all += list(x)
        ys_all += list(y)
        ax.plot(x, y, color=s.color, lw=s.lw, ls="-", marker="o", ms=2.4, mew=0, zorder=4)
        slope, n = mt.loglog_slope(list(x), list(y), xmax=LOW_NOISE)
        lab = f"{s.label}\n(Steigung {de(slope, 2)} bei FPR̄ ≤ {de(LOW_NOISE)}, {n} Punkte)" if np.isfinite(slope) else s.label
        handles.append(Line2D([], [], color=s.color, lw=s.lw, marker="o", ms=2.4, mew=0, label=lab))
        p = s.at(opt.h)
        if p and p.get("fpr_dev") and p.get("fpr_ii"):
            _h_marker(ax, p["fpr_dev"], p["fpr_ii"], s.color)
            if s.name == "2-aus-4" or (anchor is None and s.name == "adaptiv"):
                anchor = (p["fpr_dev"], p["fpr_ii"]) if s.name == "2-aus-4" or anchor is None else anchor
    if xs_all and ys_all:
        lo, hi = min(xs_all) * 0.8, max(xs_all) * 1.25
        ylo, yhi = min(ys_all) * 0.6, max(ys_all) * 1.8
        if anchor is not None:
            x0, y0 = anchor
            xx = np.geomspace(lo, hi * 1.5, 200)
            for sl in (1, 2, 3):
                yy = y0 * (xx / x0) ** sl
                m = (yy >= ylo) & (yy <= yhi)
                if m.any():
                    ax.plot(xx[m], yy[m], color=GREY, lw=0.9, ls="--", zorder=2)
                    ax.annotate(f"Steigung {sl}", xy=(xx[m][0], yy[m][0]), xytext=(4, 3), textcoords="offset points",
                                fontsize=7, color=GREY, ha="left", va="bottom")
            handles.append(Line2D([], [], color=GREY, lw=0.9, ls="--", label=f"Orientierung: Steigung 1, 2, 3\ndurch den Punkt h = {de(opt.h)}"))
        ax.set_xscale("log")
        ax.set_yscale("log")
        _apply_zoom(ax, opt, (lo, hi * 1.9), (ylo, yhi), True)
        fr.log_axis(ax, "x", *ax.get_xlim())
        fr.log_axis(ax, "y", *ax.get_ylim())
    handles.append(Line2D([], [], color="white", marker="D", ms=6.0, mec=INK, mew=0.9, ls="none", label=f"Grenzwertfaktor h = {de(opt.h)}"))
    ax.set_xlabel("Anteil der Zeit mit Gerätealarm außerhalb von Krisen, Mittel der Geräte FPR̄, logarithmisch", fontsize=8.5)
    ax.set_ylabel("Anteil der krisenfreien Zeit mit\nEskalation (Rot), logarithmisch", fontsize=8.5)
    fr._grid(ax)
    return handles


# ─────────────────────────────────────────────────────────────────────────────
# Abbildung
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Ansicht „Gerät → System“: drei Felder, Punktfarbe = h
# ─────────────────────────────────────────────────────────────────────────────

def h_values(ds: Dataset) -> List[float]:
    return sorted({round(float(r["h"]), 6) for r in ds.rows if r.get("art") == "Regel" and r.get("h") is not None})


def h_colors(hs: Sequence[float]) -> Dict[float, tuple]:
    """Eine Farbskala für alle Felder: kleines h hell, großes h dunkel (viridis, 0,05 … 0,92)."""
    cmap = plt.get_cmap(H_CMAP)
    if len(hs) == 1:
        return {hs[0]: cmap(0.5)}
    return {h: cmap(v) for h, v in zip(hs, np.linspace(0.05, 0.92, len(hs)))}


def device_points(ds: Dataset, dev: str) -> List[Tuple[float, float, float]]:
    """(h, FPR_j, η_j) eines Geräts: FPR_j = Anteil der stabilen Zeit mit Alarm, η_j = Anteil der Krisen mit Schwellenüberschreitung."""
    out = []
    for r in fr.curve(ds.rows, "Geraet", dev):
        if r.get("fpr") is not None and r.get("eta_oder_erkannt") is not None:
            out.append((round(float(r["h"]), 6), float(r["fpr"]), float(r["eta_oder_erkannt"])))
    return out


def eta_mean_by_h(ds: Dataset) -> Dict[float, float]:
    """Mittel der η_j der vier Geräte je h."""
    etas = fr._etas_by_h(ds.rows)
    return {h: float(np.mean(v)) for h, v in etas.items()}


def binom_curve(ds: Dataset, k: int) -> List[Tuple[float, float, float]]:
    """(h, mittleres η_j, 100·P(mindestens k von 4 Geräten nehmen teil)) mit den gemessenen η_j je h."""
    etas = fr._etas_by_h(ds.rows)
    pts = [(h, float(np.mean(v)), 100.0 * fr.binom_participation(v, k)) for h, v in etas.items()]
    return sorted(pts)


def binom_deviation(ds: Dataset, k: int) -> Tuple[float, Optional[float], List[Tuple[float, float, float]]]:
    """Abweichung Binomialformel − Messung (erkannt mild, pp) für k-aus-4 je h: (max |Δ|, h des Maximums, [(h, binom, gemessen)])."""
    meas = {round(float(r["h"]), 6): 100.0 * float(r["eta_oder_erkannt"]) for r in fr.curve(ds.rows, "Regel", f"{k}-aus-4", None)
            if r.get("eta_oder_erkannt") is not None}
    rows = [(h, b, meas[h]) for h, _x, b in binom_curve(ds, k) if h in meas]
    if not rows:
        return float("nan"), None, []
    worst = max(rows, key=lambda t: abs(t[1] - t[2]))
    return abs(worst[1] - worst[2]), worst[0], rows


def _h_points(ax, xs: Sequence[float], ys: Sequence[float], hs: Sequence[float], colors: Dict[float, tuple],
              h_sel: float, size: float = 22.0) -> None:
    """Punkte in h-Farbe; Raute bei h = 1,0; Ring am gewählten h."""
    for x, y, h in zip(xs, ys, hs):
        c = colors.get(round(h, 6), GREY)
        if abs(h - 1.0) < 1e-9:
            ax.plot([x], [y], marker="D", ms=6.5, color=c, mec=INK, mew=0.9, ls="none", zorder=7)
        else:
            ax.scatter([x], [y], s=size, color=c, edgecolors="white", linewidths=0.5, zorder=6)
        if abs(h - h_sel) < 1e-9:
            ax.plot([x], [y], marker="o", ms=11, mfc="none", mec=INK, mew=1.3, ls="none", zorder=8)


def _draw_geraet_system(axes3, ds: Dataset, opt: Options, cfgs, colors: Dict[float, tuple], with_slopes: bool = True,
                        xlabels: bool = True) -> Tuple[List[Line2D], Dict[str, float]]:
    """Drei Felder: Gerät (η_j über FPR_j), Fehlmeldungen (Fehleskalationen über FPR̄, log-log), Krisen (erkannt über η̄).
    FPR_j und FPR̄ umfassen zwei Dekaden (0,003 … 0,5) und werden deshalb in beiden linken Feldern logarithmisch
    aufgetragen; der Schalter „Skala x“ wirkt hier nicht."""
    ax1, ax2, ax3 = axes3
    xs_f = 24.0 if opt.x_unit == "tag" else 1.0
    handles: List[Line2D] = []
    slopes: Dict[str, float] = {}
    # ── Feld 1: Geräte ─────────────────────────────────────────────────────
    xmax1 = 0.0
    ends: List[Tuple[float, float, str]] = []
    for dev in fr.DEVICES:
        pts = device_points(ds, dev)
        if not pts:
            continue
        pts = sorted(pts, key=lambda q: q[1])
        hs = [q[0] for q in pts]
        x = [q[1] for q in pts]
        y = [q[2] for q in pts]
        xmax1 = max(xmax1, max(x))
        ax1.plot(x, y, color=DEV_COLOR, lw=0.9, zorder=3)
        _h_points(ax1, x, y, hs, colors, opt.h, size=16.0)
        ends.append((x[0], y[0], dev))
    for i, (x0, y0, dev) in enumerate(sorted(ends, key=lambda t: t[1])):       # Namen gestaffelt am linken Ende
        ax1.annotate(dev, xy=(x0, y0), xytext=(-4, (i - (len(ends) - 1) / 2) * 7.5), textcoords="offset points",
                     fontsize=6.6, color=INK, ha="right", va="center")
    ax1.set_xscale("log")
    lo = min((q[1] for dev in fr.DEVICES for q in device_points(ds, dev) if q[1] > 0), default=1e-3) * 0.45
    ax1.set_xlim(lo, max(xmax1 * 1.6, lo * 10))
    fr.log_axis(ax1, "x", *ax1.get_xlim(), max_labels=2)
    fx.de_axes(ax1, x=False, y=True)
    if xlabels:
        ax1.set_xlabel("FPR_j: Anteil der stabilen Zeit\nmit Gerätealarm, log.", fontsize=7.8)
    ax1.set_ylim(0, 1.04)
    ax1.set_ylabel("η_j: Anteil der Krisen\nmit Schwellenüberschreitung", fontsize=7.8)
    ax1.set_title("Gerät", fontsize=9)
    handles.append(Line2D([], [], color=DEV_COLOR, lw=0.9, marker="o", ms=3.5, mfc=GREY, mec="white",
                          label="Gerät: ein Punkt je h, Punkte eines Geräts verbunden"))
    # ── Feld 2: Fehlmeldungen ──────────────────────────────────────────────
    series = visible_series(ds, opt, cfgs)
    anchor = None
    xs_all: List[float] = []
    ys_all: List[float] = []
    for sr in series:
        pts = [(p["h"], p["fpr_dev"], p["x_count"] * xs_f) for p in sr.pts
               if p.get("fpr_dev") and p.get("x_count") and p["fpr_dev"] > 0 and p["x_count"] > 0]
        if not pts:
            continue
        hs = [q[0] for q in pts]
        x = [q[1] for q in pts]
        y = [q[2] for q in pts]
        xs_all += x
        ys_all += y
        ax2.plot(x, y, color=sr.color, lw=sr.lw, zorder=4)
        _h_points(ax2, x, y, hs, colors, opt.h)
        slope, n_pts = mt.loglog_slope(x, [v / xs_f for v in y], xmax=LOW_NOISE)
        slopes[sr.label] = slope
        lab = f"{sr.label}\n(Steigung {de(slope, 2)} bei FPR̄ ≤ {de(LOW_NOISE)})" if (with_slopes and np.isfinite(slope)) else sr.label
        handles.append(Line2D([], [], color=sr.color, lw=sr.lw, label=lab))
        p1 = sr.at(1.0)
        if p1 and p1.get("fpr_dev") and p1.get("x_count") and (sr.name == "2-aus-4" or anchor is None):
            anchor = (p1["fpr_dev"], p1["x_count"] * xs_f) if sr.name == "2-aus-4" or anchor is None else anchor
    if xs_all and ys_all:
        lo, hi = min(xs_all) * 0.8, max(xs_all) * 1.25
        ylo, yhi = min(ys_all) * 0.6, max(ys_all) * 1.8
        if anchor is not None:
            x0, y0 = anchor
            xx = np.geomspace(lo, hi * 1.3, 200)
            for sl in (1, 2, 3):
                yy = y0 * (xx / x0) ** sl
                m = (yy >= ylo) & (yy <= yhi)
                if m.any():
                    ax2.plot(xx[m], yy[m], color=GREY, lw=0.8, ls="--", zorder=2)
                    ax2.annotate(f"Steigung {sl}", xy=(xx[m][0], yy[m][0]), xytext=(3, 2), textcoords="offset points",
                                 fontsize=6.5, color=GREY, ha="left", va="bottom")
            handles.append(Line2D([], [], color=GREY, lw=0.8, ls="--", label="Steigung 1, 2, 3 durch den Punkt 2-aus-4 bei h = 1,0"))
        ax2.set_xscale("log")
        ax2.set_yscale("log")
        ax2.set_xlim(lo, hi * 1.3)
        ax2.set_ylim(ylo, yhi)
        fr.log_axis(ax2, "x", lo, hi * 1.3, max_labels=2)
        fr.log_axis(ax2, "y", ylo, yhi, max_labels=3)
    if xlabels:
        ax2.set_xlabel("FPR̄: Mittel der FPR_j\nder vier Geräte, log.", fontsize=7.8)
    ax2.set_ylabel("Fehleskalationen\n" + X_UNITS[opt.x_unit] + ", log.", fontsize=7.8)
    ax2.set_title("Fehlmeldungen", fontsize=9)
    # ── Feld 3: Krisen ─────────────────────────────────────────────────────
    eta_m = eta_mean_by_h(ds)
    for sr in series:
        pts = [(p["h"], eta_m.get(round(p["h"], 6)), 100.0 * p["y_count"]) for p in sr.pts
               if p.get("y_count") is not None and round(p["h"], 6) in eta_m]
        if not pts:
            continue
        pts.sort(key=lambda q: q[1])
        ax3.plot([q[1] for q in pts], [q[2] for q in pts], color=sr.color, lw=sr.lw, zorder=4)
        _h_points(ax3, [q[1] for q in pts], [q[2] for q in pts], [q[0] for q in pts], colors, opt.h)
    for k in (2, 3):
        bc = sorted(binom_curve(ds, k), key=lambda q: q[1])
        if bc:
            ax3.plot([q[1] for q in bc], [q[2] for q in bc], color=GREY, lw=1.0, ls="--", zorder=2)
            mid = min(bc, key=lambda q: abs(q[1] - 0.5))             # Beschriftung nahe η̄ = 0,5, versetzt nach k
            ax3.annotate(f"k = {k}", xy=(mid[1], mid[2]), xytext=(6 if k == 2 else -6, -8 if k == 2 else 8),
                         textcoords="offset points", fontsize=6.5, color=GREY, ha="left" if k == 2 else "right", va="center")
    handles.append(Line2D([], [], color=GREY, lw=1.0, ls="--", label="Binomialformel P(≥ k von 4) mit gemessenen η_j, k = 2 und 3"))
    ax3.set_xlim(0, 1.04)
    ax3.set_ylim(0, 102)
    if xlabels:
        ax3.set_xlabel("η̄: Mittel der η_j\nder vier Geräte", fontsize=7.8)
    ax3.set_ylabel("erkannte Krisen, mild (%)", fontsize=7.8)
    ax3.set_title("Krisen", fontsize=9)
    fx.de_axes(ax3, x=True, y=True)
    for ax in (ax1, ax2, ax3):
        fr._grid(ax)
    return handles, slopes


def h_legend_handles(hs: Sequence[float], colors: Dict[float, tuple], h_sel: float) -> List[Line2D]:
    out = []
    for h in hs:
        out.append(Line2D([], [], marker="D" if abs(h - 1.0) < 1e-9 else "o", ms=5.5 if abs(h - 1.0) < 1e-9 else 4.5,
                          color=colors[h], mec=INK if abs(h - 1.0) < 1e-9 else "white", mew=0.8, ls="none",
                          label=f"h = {de(h)}"))
    out.append(Line2D([], [], marker="o", ms=8, mfc="none", mec=INK, mew=1.3, ls="none", label=f"gewählt: h = {de(h_sel)}"))
    return out


def device_table_rows(datasets: Dict[str, Dataset], opt: Options) -> List[dict]:
    """Geräte bei h: η_j, FPR_j, Fehlalarme je Bett·h."""
    out: List[dict] = []
    for e in _etas(opt, datasets):
        ds = datasets[e]
        for dev in fr.DEVICES:
            r = next((r for r in fr.curve(ds.rows, "Geraet", dev) if abs(float(r["h"]) - opt.h) < 1e-9), None)
            if r is None:
                continue
            out.append({"η": e, "Gerät": dev, "h": de(opt.h), "η_j (Krisen mit Überschreitung)": de(r.get("eta_oder_erkannt"), 3),
                        "FPR_j (Zeitanteil)": de(r.get("fpr"), 5), "Fehlalarme je Bett·h": de(r.get("falsch_je_bett_h"), 2)})
    return out


def rule_table_rows(datasets: Dict[str, Dataset], opt: Options) -> List[dict]:
    """Regeln bei h: Fehleskalationen je Bett·h und Tag, erkannt mild/streng."""
    etas = _etas(opt, datasets)
    cfgs = configs_for(opt, [datasets[e] for e in etas])
    out: List[dict] = []
    for e in etas:
        for sr in visible_series(datasets[e], opt, cfgs):
            p = sr.at(opt.h)
            if p is None:
                continue
            out.append({"η": e, "Regel": sr.label, "h": de(opt.h), "Fehleskalationen je Bett·h": de(p.get("x_count"), 2),
                        "je Bett und Tag": de(p["x_count"] * 24, 1) if p.get("x_count") is not None else "—",
                        "erkannt mild": de(100 * p["y_count"], 1) + " %" if p.get("y_count") is not None else "—",
                        "erkannt streng": de(100 * p["y_strict"], 1) + " %" if p.get("y_strict") is not None else "—"})
    return out


def _etas(opt: Options, datasets: Dict[str, Dataset]) -> List[str]:
    if opt.eta == "beide":
        return [e for e in ("0,9", "0,7") if e in datasets]
    if opt.eta == "beide val":
        return [e for e in ("val 0,9", "val 0,7") if e in datasets]
    return [opt.eta] if opt.eta in datasets else list(datasets)[:1]


def zeit_etas(opt: Options) -> List[str]:
    if opt.eta == "beide":
        return ["0,9", "0,7"]
    if opt.eta == "beide val":
        return ["val 0,9", "val 0,7"]
    return [opt.eta]


def zeit_rules(opt: Options) -> Tuple[Tuple[float, ...], Tuple[str, ...]]:
    from . import zeit_roc as zr
    Ts = tuple(opt.zeit_Ts) if "adaptiv" in opt.rules else ()
    return Ts, tuple(r for r in zr.ALL_K if r in opt.rules)


def fig_werkstatt(datasets: Dict[str, Dataset], opt: Options) -> Figure:
    if opt.view == "zeit_T":
        from . import zeit_roc as zr
        Ts, rules = zeit_rules(opt)
        return zr.fig_z1(zr.load(), zeit_etas(opt), Ts, rules, zoom=opt.zeit_zoom,
                         h_mark=opt.h)
    etas = _etas(opt, datasets)
    if not etas:
        raise ValueError("keine Daten")
    cfgs = configs_for(opt, [datasets[e] for e in etas])
    n = len(etas)
    if opt.view == "theorie":
        panel_series = [visible_series(datasets[e], opt, cfgs) for e in etas]
        m = max(len(ps) for ps in panel_series) or 1
        fig, axes = plt.subplots(n, m, figsize=(W, (0.5 if m >= 3 else 0.62) * W * (1 if n == 1 else 1.7)), squeeze=False,
                                 sharey=True, gridspec_kw=dict(wspace=0.12, hspace=0.30))
        for i, e in enumerate(etas):
            for k in range(m):
                ax = axes[i][k]
                if k < len(panel_series[i]):
                    _draw_theorie_panel(ax, datasets[e], opt, panel_series[i][k])
                    ax.set_title(ax.get_title(), fontsize=8.5 if m <= 3 else 7.5)
                else:
                    ax.axis("off")
            ylab = Y_TIME if m <= 3 else "Anteil der Krisenzeit\nmit Eskalation (Rot)"
            axes[i][0].set_ylabel((f"{ETAS[e]}\n" if n > 1 else "") + ylab, fontsize=8.5)
            if i == n - 1:
                axes[i][min(m - 1, m // 2)].set_xlabel(X_TIME + (", logarithmisch" if opt.log_x else f", linear bis {de(fr.XMAX_LIN)}"), fontsize=8.5)
        handles = [Line2D([], [], color=INK, lw=2.0, ls="-", marker="o", ms=2.4, mew=0, label="Messung (Simulation, Dashboard-Kern)"),
                   Line2D([], [], color=INK, lw=1.5, ls="-.", label="Modell E1+E2 (versetzter Einstieg; Episoden nach m;\nnur Basis α = 0,5, δ_min = 0,5)"),
                   Line2D([], [], color=GREY, lw=1.0, ls="--", label=fr.BINOM_LABEL + ", nur k-aus-4"),
                   Line2D([], [], color="white", marker="D", ms=6.0, mec=INK, mew=0.9, ls="none", label=f"Grenzwertfaktor h = {de(opt.h)}")]
        bottom = 0.32 if n == 1 else 0.22
        ncol = 2
    elif opt.view == "geraet_system":
        fig, axes = plt.subplots(n, 3, figsize=(W, 0.52 * W * (1 if n == 1 else 1.75)), squeeze=False,
                                 gridspec_kw=dict(wspace=0.5, hspace=0.32))
        hs_all = sorted({h for e in etas for h in h_values(datasets[e])})
        colors = h_colors(hs_all)
        handles = []
        for i, e in enumerate(etas):
            h, _sl = _draw_geraet_system(axes[i], datasets[e], opt, cfgs, colors, with_slopes=(n == 1), xlabels=(i == n - 1))
            if i == 0:
                handles = h
            else:
                for hh in h:
                    if hh.get_label() not in [x.get_label() for x in handles]:
                        handles.append(hh)
            if n > 1:
                axes[i][0].set_ylabel(f"{ETAS[e]}\n" + axes[i][0].get_ylabel(), fontsize=7.8)
        if opt.title:
            fig.suptitle(title_text(opt), fontsize=8.5, y=0.995)
        leg1 = fig.legend(handles=handles, loc="lower left", ncol=1, fontsize=6.4, frameon=False, bbox_to_anchor=(0.02, 0.01),
                          handlelength=2.6)
        leg2 = fig.legend(handles=h_legend_handles(hs_all, colors, opt.h), loc="lower right", ncol=4, fontsize=6.2, frameon=False,
                          bbox_to_anchor=(0.99, 0.01), title="Grenzwertfaktor h (Punktfarbe)", title_fontsize=6.8,
                          handlelength=1.2, columnspacing=0.9, handletextpad=0.4)
        top = 0.90 if (opt.title or n > 1) else 0.93
        fr._layout(fig, bottom=0.34, top=top, left=0.10, right=0.985, wspace=0.5, hspace=0.32)
        fig.canvas.draw()
        y1 = max(leg.get_window_extent().transformed(fig.transFigure.inverted()).y1 for leg in (leg1, leg2))
        need = y1 + 0.16 * (0.52 * W / fig.get_figheight())
        fr._layout(fig, bottom=min(max(need, 0.30), 0.6), top=top, left=0.10, right=0.985, wspace=0.5, hspace=0.32)
        return fig
    else:
        height = (0.62 if opt.view != "uebertragung" else 0.72) * W
        fig, axes1 = plt.subplots(1, n, figsize=(W, height if n == 1 else height * 0.85), squeeze=False, sharey=True,
                                  gridspec_kw=dict(wspace=0.12))
        handles = []
        for i, e in enumerate(etas):
            ax = axes1[0][i]
            draw = {"froc": _draw_count, "roc": _draw_time, "uebertragung": _draw_uebertragung, "klassisch": _draw_klassisch}[opt.view]
            h = draw(ax, datasets[e], opt, cfgs)
            if i == 0:
                handles = h
            else:
                ax.set_ylabel("")
                ax.set_xlabel("dasselbe", fontsize=8.5)
                for hh in h:
                    if hh.get_label() not in [x.get_label() for x in handles]:
                        handles.append(hh)
            if n > 1:
                ax.set_title(ETAS[e], fontsize=9)
        ncol = 3 if opt.view != "uebertragung" else 2
        n_lines = sum(h.get_label().count("\n") + 1 for h in handles)
        bottom = 0.14 + 0.028 * math.ceil(n_lines / ncol)
    if opt.title:
        fig.suptitle(title_text(opt), fontsize=8.5, y=0.995)
    leg = fig.legend(handles=handles, loc="lower center", ncol=ncol, fontsize=6.4, frameon=False,
                     bbox_to_anchor=(0.5, 0.01), handlelength=2.6, columnspacing=1.1)
    top = 0.90 if (opt.title or n > 1 or opt.view == "theorie") else 0.97
    fr._layout(fig, bottom=min(bottom, 0.45), top=top, left=0.11, wspace=0.12)
    # Legendenhöhe messen und die Achsen darüber setzen (Achsenbeschriftung braucht ≈ 0,09 der Höhe)
    fig.canvas.draw()
    bb = leg.get_window_extent().transformed(fig.transFigure.inverted())
    need = bb.y1 + (0.12 if n == 1 else 0.15) * (0.62 * W / fig.get_figheight())   # Platz für Teilstriche und x-Beschriftung
    if need > bottom:
        fr._layout(fig, bottom=min(need, 0.6), top=top, left=0.11, wspace=0.12)
    return fig


def title_text(opt: Options) -> str:
    base = VIEWS[opt.view]
    if opt.schar:
        return f"{base} – Kurvenschar {PARAMS[opt.schar]} ({ETAS[opt.eta]})"
    return f"{base} – {ws.label(opt.T, opt.alpha, opt.delta_min)} ({ETAS[opt.eta]})"


def export_name(opt: Options) -> str:
    """Dateiname aus den Einstellungen, z. B. Werkstatt_froc_eta09_T10_a05_d05_h1."""
    num = lambda v: f"{v:g}".replace(".", "")      # noqa: E731
    eta = opt.eta.replace(" ", "_") if opt.eta.startswith("beide") else "eta" + opt.eta.replace(",", "").replace(" ", "")
    cfg = f"Schar_{opt.schar}" if opt.schar else f"T{num(opt.T)}_a{num(opt.alpha)}_d{num(opt.delta_min)}"
    extra = ("_log" if opt.log_x else "") + ("_Tag" if (opt.view == "froc" and opt.x_unit == "tag") else "")
    extra += "_Ereignis" if (opt.view in ("froc", "klassisch") and opt.x_unit == "ereignis") else ""
    extra += "_Zeit" if (opt.view == "klassisch" and opt.x_unit == "zeit") else ""
    extra += "_streng" if (opt.view in ("froc", "klassisch") and opt.mode == "streng") else ""
    extra += "_beide" if (opt.view == "klassisch" and opt.compare_params) else ""
    if opt.view == "zeit_T":
        extra = "_zoom" if opt.zeit_zoom else ""
        cfg = "T" + "-".join(f"{T:g}" for T in opt.zeit_Ts)
    return f"Werkstatt_{opt.view}_{eta}_{cfg}_h{num(opt.h)}{extra}"


def save(fig: Figure, outdir: Path, stem: str) -> List[Path]:
    return fr.save(fig, outdir, stem)


# ─────────────────────────────────────────────────────────────────────────────
# Tabelle unter der Abbildung
# ─────────────────────────────────────────────────────────────────────────────

def _ci_text(v: Optional[float], ci: Optional[Sequence[float]]) -> str:
    if v is None or not math.isfinite(v):
        return "—"
    s = f"{v:+.1f}".replace(".", ",")
    if ci and all(c is not None and math.isfinite(c) for c in ci):
        s += f" [{ci[0]:+.1f}; {ci[1]:+.1f}]".replace(".", ",")
    return s + " pp"


def _delta_source(ds: Dataset, cfg: Tuple[float, float, float], view: str) -> Optional[dict]:
    """Δ-Werte bei h = 1 einer Konfiguration: aus der Werkstatt-Datei, ersatzweise (Basis) aus roc_tabellen.json."""
    if ds.werk is not None:
        d = ds.werk.get("h1", {}).get(ws.key(*cfg), {}).get(view)
        if d:
            return d
    if all(abs(a - b) < 1e-9 for a, b in zip(cfg, ws.BASE)):
        try:
            for z in ds.tables["huelle"][view]["zeilen"]:
                if abs(z["h"] - 1.0) < 1e-9:
                    return z
        except (KeyError, TypeError):
            pass
    return None


def table_rows(datasets: Dict[str, Dataset], opt: Options) -> List[dict]:
    etas = _etas(opt, datasets)
    cfgs = configs_for(opt, [datasets[e] for e in etas])
    view = "zeit" if opt.view in ("roc", "theorie", "uebertragung") else "stueck"
    out: List[dict] = []
    for e in etas:
        ds = datasets[e]
        for s in visible_series(ds, opt, cfgs):
            p = s.at(opt.h)
            if p is None:
                continue
            row = {"η": e, "Regel": s.label, "h": de(opt.h),
                   "Fehleskalationen je Bett·h": de(p.get("x_count"), 2),
                   "je Bett und Tag": de(p["x_count"] * 24, 1) if p.get("x_count") is not None else "—",
                   "erkannt mild": de(100 * p["y_count"], 1) + " %" if p.get("y_count") is not None else "—",
                   "erkannt streng": de(100 * p["y_strict"], 1) + " %" if p.get("y_strict") is not None else "—",
                   "eskalierte Fehlalarm-Episoden": (f"{de(p['x_ep'], 1)} % ({int(p['ep_k'])}/{int(p['ep_n'])})"
                                                     if p.get("x_ep") is not None and p.get("ep_n") else "—"),
                   "Latenz Median / p90 [s]": f"{de(p.get('lat_med'), 1)} / {de(p.get('lat_p90'), 1)}",
                   "TPR_sys (Zeit)": de(p.get("tpr"), 3), "FPR_sys (ii)": de(p.get("fpr_ii"), 5),
                   "Δ Sehne": "—", "Δ Hülle": "—"}
            if s.name == "adaptiv" and s.config is not None and abs(opt.h - 1.0) < 1e-9:
                d = _delta_source(ds, s.config, view)
                if d:
                    row["Δ Sehne"] = _ci_text(d.get("delta_sehne_pp"), d.get("delta_sehne_ci"))
                    row["Δ Hülle"] = _ci_text(d.get("delta_pp"), d.get("delta_ci"))
            out.append(row)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Ereignisbezogene Achse, Kalibrierung („kalibriert“, P0-Kurven), klassische ROC des Filters
# ─────────────────────────────────────────────────────────────────────────────

def _same(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) < 1e-9


def config_label(cfg: Tuple[float, float, float], kal: Optional[Tuple[float, float, float]] = None) -> str:
    """„adaptiv kalibriert (T = …)“ für die Konfiguration kalibriert, sonst ``werkstatt.label``."""
    lab = ws.label(*cfg)
    if kal is not None and all(_same(a, b) for a, b in zip(cfg, kal)) and not all(_same(a, b) for a, b in zip(cfg, ws.BASE)):
        return lab.replace("adaptiv (", "adaptiv kalibriert (", 1)
    return lab


def _xy_keys(opt: Options) -> Tuple[str, str, float]:
    """(x-Feld, y-Feld, x-Faktor) der Sicht nach Stück bzw. Ereignis."""
    ykey = "y_count" if opt.mode == "mild" else "y_strict"
    if opt.x_unit == "ereignis":
        return "x_ep", ykey, 1.0
    return "x_count", ykey, (24.0 if opt.x_unit == "tag" else 1.0)


def _endpoints(ax, x: np.ndarray, y: np.ndarray, color: str) -> None:
    """Punktierte Verlängerung einer Kurve in Prozentachsen zu (0, 0) und (100, 100)."""
    if not x.size:
        return
    ax.plot([0.0, x[0]], [0.0, y[0]], color=color, lw=0.9, ls=(0, (1.0, 2.0)), zorder=3)
    ax.plot([x[-1], 100.0], [y[-1], 100.0], color=color, lw=0.9, ls=(0, (1.0, 2.0)), zorder=3)


def _ci_event(ax, p: dict, color: str, ykey: str = "y_count") -> None:
    """Fehlerbalken am h-Punkt (ereignisbezogen): Wilson für eskalierte Episoden und erkannte Krisen."""
    if p.get("x_ep") is None or p.get(ykey) is None:
        return
    xlo = xhi = None
    if p.get("ep_n"):
        xlo, xhi = rr.wilson_ci(int(p["ep_k"]), int(p["ep_n"]))
    ylo = yhi = None
    nd = p.get("n_det") if ykey == "y_count" else p.get("n_det_strict")
    if nd is not None and p.get("n_krisen"):
        ylo, yhi = rr.wilson_ci(int(nd), int(p["n_krisen"]))
    x, y = p["x_ep"], 100 * p[ykey]
    xerr = None if xlo is None else [[max(0.0, x - 100 * xlo)], [max(0.0, 100 * xhi - x)]]
    yerr = None if ylo is None else [[max(0.0, y - 100 * ylo)], [max(0.0, 100 * yhi - y)]]
    ax.errorbar([x], [y], xerr=xerr, yerr=yerr, fmt="none", ecolor=color, elinewidth=1.0, capsize=2.5, zorder=7)


def _ep_lookup(ds: Dataset) -> Dict[Tuple[float, str], dict]:
    """(h, Regel bzw. Konfiguration) → Zeile mit ep_gesamt / ep_eskaliert / ep_anteil (P0 = 0,5)."""
    out: Dict[Tuple[float, str], dict] = {}

    def add(r: dict) -> None:
        name = r.get("konfig")
        if name and r.get("h") is not None and r.get("ep_anteil") is not None:
            out.setdefault((round(float(r["h"]), 6), name), r)

    for r in ds.episoden:
        add(r)
    for rows in ds.hkurve.values():
        for r in rows:
            add(r)
    for rows in ds.klassisch.values():
        for r in rows:
            if r.get("regel") != "adaptiv" or (r.get("P0") is not None and _same(r["P0"], kb.P0_BASIS)):
                add(r)
    return out


def _attach_ep(pts: List[dict], ds: Dataset, name: str) -> None:
    look = _ep_lookup(ds)
    for p in pts:
        e = look.get((round(float(p["h"]), 6), name))
        if e is not None:
            p["x_ep"] = 100.0 * float(e["ep_anteil"])
            p["ep_n"] = e.get("ep_gesamt")
            p["ep_k"] = e.get("ep_eskaliert")


def _netz_row(ds: Dataset, regel: str, kal: bool = False) -> Optional[dict]:
    rows = ds.netz_kal if kal else ds.netz
    return next((r for r in rows if r.get("regel") == regel), None)


def attach_kalibrierung(ds: Dataset, out_dir: Optional[Path] = None) -> Dataset:
    """Ergänzt einen Datensatz um die Dateien der Kalibrierung (``analysis.roc_ziel3.kalibrierung``), gefiltert auf sein η:
    Episoden (ereignisbezogene x-Achse), P0-Kurven (klassisch), h-Kurve und Netzläufe der Konfiguration „kalibriert“,
    Bootstrap der AUC.  Fehlende Dateien lassen die Felder leer."""
    out = Path(out_dir) if out_dir is not None else kb.OUT_DIR
    ds.episoden = [r for r in kb.load_episoden(out) if r.get("eta") == ds.eta]
    kal_d = kb.load_kalibriert(out)
    k = (kal_d or {}).get("kalibriert") or {}
    ds.kal = ws.parse_key(k["konfig"]) if k.get("konfig") else None
    cfgs = [ws.BASE] + ([ds.kal] if ds.kal is not None and not all(_same(a, b) for a, b in zip(ds.kal, ws.BASE)) else [])
    for c in cfgs:
        rows, _pats = kb.load_klassisch(c, out)
        rows = [r for r in rows if r.get("eta") == ds.eta]
        if rows:
            ds.klassisch[ws.key(*c)] = rows
        hk = [r for r in kb.load_hkurve(c, out) if r.get("eta") == ds.eta and r.get("regel") == "adaptiv"]
        if hk:
            ds.hkurve[ws.key(*c)] = hk
    run = NETZ_RUN_OF_ETA.get(ds.eta)
    ds.netz = [r for r in kb.load_netz(ws.BASE, out) if r.get("eta") == run] if run else []
    ds.netz_kal = [r for r in kb.load_netz(ds.kal, out) if r.get("eta") == run and r.get("regel") == "adaptiv"] if (run and ds.kal) else []
    pr = kb.load_pruefung(out)
    pv = kb.load_pruefung_val(out)
    ds.boot = [b for b in (pr or {}).get("bootstrap", []) + (pv or {}).get("bootstrap", []) if b.get("eta") == ds.eta]
    return ds


def val_dataset(val_dir: Path, out_dir: Optional[Path] = None) -> Optional[Dataset]:
    """Datensatz eines Prüfverzeichnisses (ohne run_roc): Zeilen aus kalibrierung/val, Tabellen daraus, Kalibrierungsdateien."""
    rows, tables = kb.load_val_dataset_rows(val_dir, out_dir if out_dir is not None else kb.OUT_DIR)
    if rows is None:
        return None
    return attach_kalibrierung(Dataset(kb.eta_of(val_dir), rows, tables, None), out_dir)


def klassisch_rows(ds: Dataset, cfg: Tuple[float, float, float], h: float) -> Tuple[List[dict], List[dict]]:
    """(adaptiv-Zeilen über P0, k-aus-4-Zeilen) bei festem h aus den Dateien klassisch_<key>.csv des Datensatzes."""
    rows = ds.klassisch.get(ws.key(*cfg), [])
    ad = sorted([r for r in rows if r.get("regel") == "adaptiv" and r.get("h") is not None and _same(r["h"], h) and r.get("P0") is not None],
                key=lambda r: r["P0"])
    kr: List[dict] = []
    for name in K_RULES:
        r = next((z for z in rows if z.get("regel") == name and z.get("h") is not None and _same(z["h"], h)), None)
        if r is None:
            for other in ds.klassisch.values():
                r = next((z for z in other if z.get("regel") == name and z.get("h") is not None and _same(z["h"], h)), None)
                if r is not None:
                    break
        if r is not None:
            kr.append(r)
    return ad, kr


def _klassisch_xy(r: dict, opt: Options) -> Tuple[Optional[float], Optional[float]]:
    if opt.x_unit == "zeit":
        x, y = r.get("fpr_ii"), r.get("tpr")
        return (None if x is None else 100.0 * x), (None if y is None else 100.0 * y)
    y = r.get("erkannt" if opt.mode == "mild" else "erkannt_streng")
    y = None if y is None else 100.0 * y
    if opt.x_unit == "ereignis":
        x = r.get("ep_anteil")
        return (None if x is None else 100.0 * x), y
    return r.get("falsch_je_bett_h"), y


def klassisch_sets(ds: Dataset, opt: Options, cfgs) -> List[Tuple[Tuple[float, float, float], str, str]]:
    """Konfigurationen der klassischen Ansicht: eine, oder Prototyp (hell) und kalibriert (voll) gemeinsam."""
    if opt.compare_params and ds.kal is not None and not all(_same(a, b) for a, b in zip(ds.kal, ws.BASE)):
        return [(ws.BASE, _blend(COLORS["adaptiv"], (1.0, 1.0, 1.0), 0.55), "adaptiv Prototyp " + ws.label(*ws.BASE)[8:]),
                (ds.kal, COLORS["adaptiv"], "adaptiv kalibriert " + ws.label(*ds.kal)[8:])]
    cfg, color, lab = cfgs[0]
    return [(cfg, color, lab)]


def _draw_klassisch(ax, ds: Dataset, opt: Options, cfgs) -> List[Line2D]:
    handles: List[Line2D] = []
    pct = opt.x_unit in ("ereignis", "zeit")
    sets = klassisch_sets(ds, opt, cfgs)
    k_rows: List[dict] = []
    drawn_any = False
    for cfg, color, lab in sets:
        ad, kr = klassisch_rows(ds, cfg, opt.h)
        if kr and not k_rows:
            k_rows = kr
        pts = [(x, y, float(r["P0"])) for r in ad for x, y in [_klassisch_xy(r, opt)] if x is not None and y is not None]
        if not pts:
            continue
        drawn_any = True
        pts.sort(key=lambda t: (t[0], t[1]))
        x = np.array([t[0] for t in pts])
        y = np.array([t[1] for t in pts])
        if pct:
            _endpoints(ax, x, y, color)
        ax.plot(x, y, color=color, lw=LW["adaptiv"], marker="o", ms=2.6, mew=0, zorder=4)
        auc = kb.auc_points(zip(x, y)) if pct else None
        handles.append(Line2D([], [], color=color, lw=LW["adaptiv"], marker="o", ms=2.6, mew=0,
                              label=lab + (f"\n(AUC {de(auc, 3)}, P0 = 0,001 … 0,999)" if auc is not None else "")))
        for xx, yy, p0 in pts:
            if _same(p0, kb.P0_BASIS):
                _h_marker(ax, xx, yy, color)
            if any(_same(p0, q) for q in P0_ANNOT):
                ax.annotate(de(p0), (xx, yy), xytext=(4, -9 if p0 >= 0.5 else 4), textcoords="offset points", fontsize=5.8,
                            color=color, zorder=9)
    if k_rows:
        kp = [(x, y, r["regel"]) for r in k_rows for x, y in [_klassisch_xy(r, opt)] if x is not None and y is not None]
        kp.sort(key=lambda t: (t[0], t[1]))
        x = np.array([t[0] for t in kp])
        y = np.array([t[1] for t in kp])
        if pct:
            _endpoints(ax, x, y, INK)
        ax.plot(x, y, color=INK, lw=1.3, ls="-", zorder=3)
        auc_k = kb.auc_points(zip(x, y)) if pct else None
        handles.append(Line2D([], [], color=INK, lw=1.3, label="k-aus-4-Linienzug (k = 1 … 4, gleiches h)"
                              + (f"\n(AUC {de(auc_k, 3)})" if auc_k is not None else "")))
        for xx, yy, name in kp:
            ax.plot([xx], [yy], marker="s", ms=5.0, color=COLORS[name], mec="white", mew=0.6, ls="none", zorder=6)
            handles.append(Line2D([], [], marker="s", ms=5.0, color=COLORS[name], mec="white", mew=0.6, ls="none", label=name))
        drawn_any = True
    if not drawn_any:
        ax.text(0.5, 0.5, "keine P0-Kurven gerechnet\n(python -m analysis.roc_ziel3.kalibrierung --stufe klassisch)",
                ha="center", va="center", transform=ax.transAxes, fontsize=8, color=GREY)
    handles.append(Line2D([], [], color="white", marker="D", ms=6.0, mec=INK, mew=0.9, ls="none",
                          label=f"P0 = 0,5 (Kontext des Prototyps); Grenzwertfaktor h = {de(opt.h)}"))
    if pct:
        if opt.log_x:
            ax.set_xscale("log")
            xs_all = [t for t in ax.get_lines() for t in t.get_xdata() if t and t > 0]
            lo = max(0.05, min(xs_all) * 0.7) if xs_all else 0.1
            _apply_zoom(ax, opt, (lo, 100.0), (0.0, 102.0), True)
            fr.log_axis(ax, "x", *ax.get_xlim())
            ax.set_xlabel(X_UNITS_KLASSISCH[opt.x_unit] + ", logarithmisch", fontsize=8.5)
            fx.de_axes(ax, x=False, y=True)
        else:
            _apply_zoom(ax, opt, (0.0, 100.0), (0.0, 102.0), False)
            ax.set_xlabel(X_UNITS_KLASSISCH[opt.x_unit], fontsize=8.5)
            fx.de_axes(ax, x=True, y=True)
        ax.set_ylabel("Krisenzeit mit Eskalation (%)" if opt.x_unit == "zeit" else f"erkannte Krisen (%), {opt.mode}", fontsize=8.5)
    else:
        xs_all = [v for ln in ax.get_lines() for v in ln.get_xdata() if v is not None]
        hi = max(xs_all) * 1.15 if xs_all else fr.XMAX_COUNT
        _apply_zoom(ax, opt, (0.0, hi), (0.0, 102.0), False)
        ax.set_xlabel(X_UNITS_KLASSISCH["h"], fontsize=8.5)
        ax.set_ylabel(f"erkannte Krisen (%), {opt.mode}", fontsize=8.5)
        fx.de_axes(ax, x=True, y=True)
    fr._grid(ax)
    return handles


def klassisch_table_rows(datasets: Dict[str, Dataset], opt: Options) -> List[dict]:
    """Tabelle der klassischen Ansicht: je η die Punkte der adaptiven P0-Kurve(n) und die vier k-aus-4-Punkte bei h."""
    out: List[dict] = []
    for e in _etas(opt, datasets):
        ds = datasets[e]
        cfgs = configs_for(opt, [ds])
        for cfg, _c, lab in klassisch_sets(ds, opt, cfgs):
            ad, kr = klassisch_rows(ds, cfg, opt.h)
            for r in ad:
                x, y = _klassisch_xy(r, opt)
                out.append({"η": e, "Regel": lab.split(" (")[0], "P0": de(r["P0"]), "Context_Log_Odds": de(r.get("clo"), 2),
                            "x": de(x, 2) if x is not None else "—", "y (%)": de(y, 1) if y is not None else "—",
                            "Fehleskalationen je Bett·h": de(r.get("falsch_je_bett_h"), 2),
                            "eskalierte Episoden": f"{int(r['ep_eskaliert'])}/{int(r['ep_gesamt'])}" if r.get("ep_gesamt") else "—",
                            "erkannt mild / streng (%)": f"{de(100 * r['erkannt'], 1)} / {de(100 * r['erkannt_streng'], 1)}"
                            if r.get("erkannt") is not None else "—",
                            "Latenz Median [s]": de(r.get("verz_kreuz_median_s"), 1)})
            if cfg is klassisch_sets(ds, opt, cfgs)[-1][0] or len(klassisch_sets(ds, opt, cfgs)) == 1:
                for r in kr:
                    x, y = _klassisch_xy(r, opt)
                    out.append({"η": e, "Regel": r["regel"], "P0": "—", "Context_Log_Odds": "—",
                                "x": de(x, 2) if x is not None else "—", "y (%)": de(y, 1) if y is not None else "—",
                                "Fehleskalationen je Bett·h": de(r.get("falsch_je_bett_h"), 2),
                                "eskalierte Episoden": f"{int(r['ep_eskaliert'])}/{int(r['ep_gesamt'])}" if r.get("ep_gesamt") else "—",
                                "erkannt mild / streng (%)": f"{de(100 * r['erkannt'], 1)} / {de(100 * r['erkannt_streng'], 1)}"
                                if r.get("erkannt") is not None else "—",
                                "Latenz Median [s]": de(r.get("verz_kreuz_median_s"), 1)})
    return out


def _auc_text(v: Optional[float], ci: Optional[Sequence[float]], digits: int = 3, sign: bool = False) -> str:
    if v is None or not math.isfinite(v):
        return "—"
    s = (f"{v:+.{digits}f}" if sign else f"{v:.{digits}f}").replace(".", ",")
    if ci and all(c is not None and math.isfinite(c) for c in ci):
        s += (f" [{ci[0]:+.{digits}f}; {ci[1]:+.{digits}f}]" if sign else f" [{ci[0]:.{digits}f}; {ci[1]:.{digits}f}]").replace(".", ",")
    return s


def auc_rows(datasets: Dict[str, Dataset], opt: Options) -> List[dict]:
    """AUC (ereignisbezogen) der adaptiven P0-Kurve gegen den k-aus-4-Linienzug mit 95-%-Intervallen aus dem Patienten-Bootstrap
    (``pruefung.json``) für Prototyp und kalibriert bei h und Definition „erkannt“ der Optionen."""
    out: List[dict] = []
    for e in _etas(opt, datasets):
        ds = datasets[e]
        for b in ds.boot:
            if not (_same(b.get("h", -1), opt.h) and b.get("mode") == opt.mode):
                continue
            out.append({"η": e, "Parameter": b.get("name", ""), "Konfiguration": b.get("konfig", ""), "h": de(opt.h),
                        "erkannt": opt.mode, "AUC adaptiv (P0-Kurve)": _auc_text(b.get("auc_adaptiv"), b.get("auc_adaptiv_ci")),
                        "AUC k-aus-4-Linienzug": _auc_text(b.get("auc_kausn"), b.get("auc_kausn_ci")),
                        "Δ AUC": _auc_text(b.get("differenz"), b.get("differenz_ci"), sign=True),
                        "Anteil Züge Δ > 0": de(100 * b["anteil_groesser"], 1) + " %" if b.get("anteil_groesser") is not None else "—",
                        "Bootstrap": f"{b.get('n_boot', 0)} Züge über Patienten"})
    return out
