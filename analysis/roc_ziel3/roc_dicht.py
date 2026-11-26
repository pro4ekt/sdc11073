"""Abb. 5.5 mit dichterem Raster über h: 15 alte h (ziel3_roc, ziel3_roc_p07) + 25 neue (ziel3_roc_dicht, ziel3_roc_p07_dicht).

Je h: adaptiver Filter über den Entscheidungskern (dashcore, Prototyp T = 10 s, α = 0,5, δ_min = 0,5, P0 = 0,5) und k-aus-4;
x = 100 % · eskalierte Fehlalarm-Episoden / alle Fehlalarm-Episoden (metrics.escalated_episodes), y = erkannte Krisen (mild).
Alle Replays werden neu gerechnet (kein Zwischenspeicher gelesen oder geschrieben); in logs/ wird nichts geschrieben.
Die Patientensummen stehen in roc_dicht.json neben diesem Skript (fortsetzbar: vorhandene Punkte werden nicht neu gerechnet).

Schritte: (1) rechnen, (2) Kontrolle gegen Tabelle 5.9 und h = 1,0 neu gegen alt, (3) Abbildung
doku_kap5/figures/Abb_5-5_ROC_Zoom_e.{png,svg} (wie _d, alle Punkte von 0,5 bis 2,2, kleinere Marker),
(4) Zählung „über der Mischung 2-/3-aus-4“ und Monotonie auf dem zusammengeführten Raster 0,5 … 2,2.

    .venv\\Scripts\\python -m analysis.roc_ziel3.roc_dicht
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

from . import dashcore as dc
from . import hull as hl
from . import journals as jn
from . import kalibrierung as kb
from . import run_roc as rr

HERE = Path(__file__).resolve().parent
FIG_DIR = HERE / "doku_kap5" / "figures"
DATEN = HERE / "roc_dicht.json"
ALT = {"0,9": rr.ROC_DIR, "0,7": rr.ROC_DIR_P07}
DICHT = {"0,9": rr.LOGS / "ziel3_roc_dicht", "0,7": rr.LOGS / "ziel3_roc_p07_dicht"}
ETAS = ("0,9", "0,7")
ADAPTIV = "adaptiv"
REGELN = ("2-aus-4", "3-aus-4", ADAPTIV)
ALLE = ("1-aus-4", "2-aus-4", "3-aus-4", "4-aus-4", ADAPTIV)
T, ALPHA, DELTA_MIN = kb.PROTOTYP
H_MIN, H_MAX = 0.5, 2.2
F = {n: i for i, n in enumerate(kb.PAT_FIELDS)}
# Tabelle 5.9: η → h → (x adaptiv in %, erkannt adaptiv in %)
TABELLE_5_9 = {"0,9": {0.8: (10.40, 99.0), 1.0: (8.62, 97.7), 1.2: (6.85, 95.2)},
               "0,7": {0.8: (10.24, 89.5), 1.0: (8.74, 86.2), 1.2: (7.18, 80.3)}}


def de(v: float, d: int = 2, sign: bool = False) -> str:
    return (f"{v:+.{d}f}" if sign else f"{v:.{d}f}").replace(".", ",")


def hs_(h: float) -> str:
    return f"{h:g}".replace(".", ",")


def pkey(eta: str, quelle: str, h: float, regel: str) -> str:
    return f"{eta}|{quelle}|{h:g}|{regel}"


# ─────────────────────────────────────────────────────────────────────────────
# (1) Rechnen
# ─────────────────────────────────────────────────────────────────────────────

def laden() -> Dict[str, Dict[str, List[float]]]:
    if DATEN.exists():
        return json.loads(DATEN.read_text(encoding="utf-8")).get("punkte", {})
    return {}


def speichern(punkte: Dict[str, Dict[str, List[float]]]) -> None:
    DATEN.write_text(json.dumps(dict(felder=list(kb.PAT_FIELDS), prototyp=dict(T=T, alpha=ALPHA, delta_min=DELTA_MIN, P0=kb.P0_BASIS),
                                     punkte=punkte), ensure_ascii=False), encoding="utf-8")


def rechnen(punkte: Dict[str, Dict[str, List[float]]], log=print) -> Dict[str, Dict[str, List[float]]]:
    contract = dc.load_contract()
    raw = dc.build_raw_clinical_db(contract, horizon_T=float(T), alpha=float(ALPHA))
    for eta in ETAS:
        for quelle, roc_dir in (("alt", ALT[eta]), ("dicht", DICHT[eta])):
            if not roc_dir.exists():
                raise FileNotFoundError(roc_dir)
            for h, folder in jn.h_folders(roc_dir):
                if all(pkey(eta, quelle, h, r) in punkte for r in ALLE):
                    continue
                t0 = time.perf_counter()
                ctx = kb.load_context(folder, eta, h)
                if abs(ctx.h - h) > 1e-9:
                    raise ValueError(f"{folder}: threshold_scale {ctx.h:g} ≠ Ordnername {h:g}")
                _rows, kp = kb.k_rule_rows(ctx)
                for name, pat in kp.items():
                    punkte[pkey(eta, quelle, h, name)] = pat
                res = dc.replay_journals(ctx.j, contract, raw, grid_phase=0.5)          # Kern, ohne Zwischenspeicher
                row, pat = kb.evaluate(ctx, rr.intervals(res.rot))
                punkte[pkey(eta, quelle, h, ADAPTIV)] = pat
                speichern(punkte)
                log(f"[η {eta} {quelle:>5}] h = {h:<5g} Episoden {row['ep_gesamt']:>6} eskaliert {100 * row['ep_anteil']:6.2f} % "
                    f"erkannt {100 * row['erkannt']:5.1f} %  ({time.perf_counter() - t0:.0f} s)")
    return punkte


# ─────────────────────────────────────────────────────────────────────────────
# Punkte
# ─────────────────────────────────────────────────────────────────────────────

def summe(pat: Dict[str, List[float]]) -> np.ndarray:
    return np.array([pat[p] for p in sorted(pat)], dtype=float).sum(axis=0)


def xy(pat: Dict[str, List[float]]) -> Tuple[float, float]:
    s = summe(pat)
    return 100.0 * s[F["ep_eskaliert"]] / s[F["ep_gesamt"]], 100.0 * s[F["n_erkannt"]] / s[F["n_krisen"]]


def raster(punkte: dict, eta: str) -> Dict[float, str]:
    """Zusammengeführtes Raster: alle alten h, dazu die neuen ohne h = 1,0 (das ist nur Kontrolle) → h: Quelle."""
    out: Dict[float, str] = {}
    for k in punkte:
        e, quelle, h, regel = k.split("|")
        if e != eta or regel != ADAPTIV:
            continue
        h = float(h)
        if quelle == "alt" or (h not in out and abs(h - 1.0) > 1e-9):
            out[h] = quelle
    return dict(sorted(out.items()))


def kurven(punkte: dict, eta: str) -> dict:
    """{'hs': [...], 'pt': {(Regel, h): (x, y)}, 'ep': {h: Episoden}} auf dem zusammengeführten Raster."""
    ra = raster(punkte, eta)
    pt = {(r, h): xy(punkte[pkey(eta, q, h, r)]) for h, q in ra.items() for r in ALLE}
    ep = {h: float(summe(punkte[pkey(eta, q, h, ADAPTIV)])[F["ep_gesamt"]]) for h, q in ra.items()}
    return dict(hs=list(ra), pt=pt, ep=ep, quelle=ra)


# ─────────────────────────────────────────────────────────────────────────────
# (2) Kontrolle
# ─────────────────────────────────────────────────────────────────────────────

def kontrolle(punkte: dict, log=print) -> bool:
    ok = True
    log("\n== Kontrolle gegen Tabelle 5.9 (alte Punkte, neu gerechnet)")
    for eta in ETAS:
        for h, (x_soll, y_soll) in TABELLE_5_9[eta].items():
            x, y = xy(punkte[pkey(eta, "alt", h, ADAPTIV)])
            gut = round(x, 2) == x_soll and round(y, 1) == y_soll
            ok &= gut
            log(f"η {eta} h {hs_(h):<3}: x {de(x)} % (Tabelle {de(x_soll)}), erkannt {de(y, 1)} % (Tabelle {de(y_soll, 1)})"
                f"  {'OK' if gut else 'ABWEICHUNG'}")
    log("\n== Kontrolle h = 1,0: neuer Ordner gegen alten (Patientensummen aller Regeln)")
    for eta in ETAS:
        for r in ALLE:
            a, b = punkte[pkey(eta, "alt", 1.0, r)], punkte[pkey(eta, "dicht", 1.0, r)]
            gleich = sorted(a) == sorted(b) and all(np.allclose(a[p], b[p], rtol=0, atol=1e-6) for p in a)
            ok &= gleich
            xa, ya = xy(a)
            xb, yb = xy(b)
            log(f"η {eta} {r:<8}: alt x {de(xa, 3)} % y {de(ya, 2)} % | neu x {de(xb, 3)} % y {de(yb, 2)} %  "
                f"{'identisch' if gleich else 'VERSCHIEDEN'}")
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# (4) Mischung 2-/3-aus-4 und Monotonie
# ─────────────────────────────────────────────────────────────────────────────

def auswertung(punkte: dict, log=print) -> dict:
    out = {}
    for eta in ETAS:
        d = kurven(punkte, eta)
        zeilen = []
        for h in d["hs"]:
            xa, ya = d["pt"][(ADAPTIV, h)]
            x2, y2 = d["pt"][("2-aus-4", h)]
            x3, y3 = d["pt"][("3-aus-4", h)]
            yc = hl.chord_value((x3, y3), (x2, y2), xa)
            zeilen.append(dict(h=h, quelle=d["quelle"][h], ep=d["ep"][h], xa=xa, ya=ya, x2=x2, y2=y2, x3=x3, y3=y3, d=ya - yc,
                               innerhalb=min(x2, x3) <= xa <= max(x2, x3)))
        out[eta] = zeilen
        log(f"\n== η {eta}: zusammengeführtes Raster ({len(zeilen)} Werte von h)")
        log("   h  Quelle Episoden | adaptiv x     y | 2-aus-4 x     y | 3-aus-4 x     y | Abstand zur Mischung")
        for z in zeilen:
            log(f"{hs_(z['h']):>5} {z['quelle']:>6} {int(z['ep']):>8} | {de(z['xa']):>6} {de(z['ya'], 1):>6} | {de(z['x2']):>6} "
                f"{de(z['y2'], 1):>6} | {de(z['x3']):>6} {de(z['y3'], 1):>6} | {de(z['d'], 1, True):>6}"
                f"{'' if z['innerhalb'] else '  (x außerhalb der Strecke)'}")

        def zaehle(sel, name):
            zz = [z for z in zeilen if sel(z)]
            oben = [z for z in zz if z["d"] > 1e-9]
            rest = [f"{hs_(z['h'])} ({de(z['d'], 1, True)})" for z in zz if z["d"] <= 1e-9]
            log(f"   {name}: über der Mischung auf {len(oben)} von {len(zz)} Werten von h; nicht darüber: {', '.join(rest) or '–'}")
            return len(oben), len(zz)

        log("")
        zaehle(lambda z: H_MIN - 1e-9 <= z["h"] <= H_MAX + 1e-9, f"Raster {hs_(H_MIN)} … {hs_(H_MAX)} (alt + neu)")
        zaehle(lambda z: H_MIN - 1e-9 <= z["h"] <= H_MAX + 1e-9 and z["quelle"] == "alt", f"nur alte Punkte {hs_(H_MIN)} … {hs_(H_MAX)}")
        zaehle(lambda z: z["quelle"] == "alt", "altes Raster 0,05 … 2,2 (15 Werte)")
        zaehle(lambda z: True, "alle 40 Werte")
        # Monotonie: mit sinkendem h soll x steigen (kein „Haken“) und y nicht fallen
        hs = [h for h in d["hs"] if H_MIN - 1e-9 <= h <= H_MAX + 1e-9]
        for r in REGELN:
            hx, hy = [], []
            for h_klein, h_gross in zip(hs[:-1], hs[1:]):
                xk, yk = d["pt"][(r, h_klein)]
                xg, yg = d["pt"][(r, h_gross)]
                if xk <= xg:
                    hx.append(f"h {hs_(h_gross)} → {hs_(h_klein)}: x {de(xg)} → {de(xk)} %")
                if yk < yg - 1e-9:
                    hy.append(f"h {hs_(h_gross)} → {hs_(h_klein)}: y {de(yg, 1)} → {de(yk, 1)} %")
            log(f"   Monotonie {r:<8} ({len(hs)} Punkte): x {'monoton' if not hx else 'NICHT monoton: ' + '; '.join(hx)} | "
                f"y {'monoton' if not hy else 'nicht monoton: ' + '; '.join(hy)}")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# (3) Abbildung (auf der Grundlage von doku_kap5/figures/abbildung_5_5_zoom_d.py)
# ─────────────────────────────────────────────────────────────────────────────

def abbildung(punkte: dict, neu: bool = False, log=print) -> None:
    sys.path.insert(0, str(FIG_DIR))
    import abbildung_5_5_zoom_b as b            # Aufbau 2 × 2, Achsen, Beschriftung (unverändert)
    import abbildung_5_5_zoom_d as dd           # Strichmuster der Ergänzung

    z, plt, Line2D, C, INK = b.z, b.plt, b.Line2D, b.C, b.INK
    name_adaptiv = z.rp.ADAPTIV                 # Schlüssel der Regel adaptiv in den alten Skripten

    def zeichne(ax, d, hmin, zoom):
        hs = [h for h in d["hs"] if hmin - 1e-9 <= h <= H_MAX + 1e-9]
        for r in z.REGELN:
            n = z.NAME[r]
            lw = 2.4 if n == "adaptiv" else 1.8
            zo = 5 if n == "adaptiv" else 3
            xs = [d["pt"][(r, h)][0] for h in hs]
            ys = [d["pt"][(r, h)][1] for h in hs]
            ax.plot(xs + [0.0], ys + [0.0], color=C[n], lw=lw, zorder=zo)
            ax.plot([xs[0], 100.0], [ys[0], 100.0], color=C[n], lw=1.0 if n == "adaptiv" else 0.8, ls=dd.STRICH,
                    zorder=4 if n == "adaptiv" else 2)
            ms = (2.6 if zoom else 1.7) + (0.4 if n == "adaptiv" else 0.0)          # kleiner als in _d, mit hellem Rand
            ax.plot(xs, ys, ls="none", marker="o", ms=ms, mfc=C[n], mec="white", mew=0.35 if zoom else 0.25, zorder=zo + 1)
            x1, y1 = d["pt"][(r, 1.0)]
            ax.plot([x1], [y1], marker="D", ms=6 if n == "adaptiv" else 5, color=C[n], mec="white", mew=0.8, zorder=8)

    D = {}
    for eta in ETAS:
        k = kurven(punkte, eta)
        pt = {((name_adaptiv if r == ADAPTIV else r), h): v for (r, h), v in k["pt"].items()}
        D[eta] = dict(hs=k["hs"], pt=pt)
    b.zeichne = zeichne
    fig = b.figur(D, H_MIN)
    old = fig.legends[0]
    handles = list(old.legend_handles)[:5]
    old.remove()
    handles.append(Line2D([], [], color=INK, lw=0.8, ls=dd.STRICH, label="Ergänzung bis (100 %, 100 %)"))
    fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False, fontsize=7, bbox_to_anchor=(0.5, -0.005))
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    for ext, kw in (("png", dict(dpi=300)), ("svg", {})):
        p = FIG_DIR / f"Abb_5-5_ROC_Zoom_e.{ext}"
        if p.exists() and not neu:
            log(f"vorhanden, nicht überschrieben: {p}")
            continue
        fig.savefig(p, bbox_inches="tight", **kw)
        log(f"geschrieben: {p}")
    plt.close(fig)


def main() -> int:
    punkte = rechnen(laden())
    ok = kontrolle(punkte)
    if not ok:
        print("\nKontrolle NICHT bestanden – keine Abbildung, keine Auswertung.")
        return 1
    auswertung(punkte)
    abbildung(punkte, neu="--neu" in sys.argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
