"""Reiter „Kapitel 5 · Gesamtbild“ (analysis/belastungsmodell_app/kapitel5.py).

Sehne/Abstand auf Spielzeugdaten, die Abbildungen der Blöcke 1–3 und 5 mit synthetischen Läufen, die Zusammenfassung
mit und ohne Daten; mit echten Daten: Kennzahlen des 12-h-Laufs (−46 % Fehlmeldungen, −1,6 pp erkannte Krisen,
Latenz +7,1 s), 6-h-Lauf (82 statt 62 %), Export aller Blöcke.
"""

import math
import tempfile
import unittest
from pathlib import Path

import matplotlib.pyplot as plt

from analysis.belastungsmodell_app import kapitel5 as k5
from analysis.belastungsmodell_app import sim_data as sd
from analysis.roc_ziel3 import figures_werkstatt as fw
from tests.test_werkstatt import _datasets as _ws_datasets

RUNS = sd.load_runs()
HAVE_12H = "12h" in RUNS and "6h" in RUNS and "A" in RUNS
WS = k5.load_datasets()


def _rule(n_false, bed_h, caught_mild, caught_strict, n_crises, n_esc):
    f = n_false / bed_h
    return sd.RuleResult(key="x", n_false=n_false, false_per_bed_h=f, false_ci=(f * 0.9, f * 1.1), n_crises=n_crises,
                         caught_mild=caught_mild, caught_strict=caught_strict,
                         caught_mild_ci=(caught_mild / n_crises - 0.03, min(1.0, caught_mild / n_crises + 0.03)),
                         caught_strict_ci=(caught_strict / n_crises - 0.03, min(1.0, caught_strict / n_crises + 0.03)),
                         n_escalations=n_esc, caught_after_end=caught_mild - caught_strict)


def _run(key: str, eta: float, hours: float, n_cr: int, false: dict, mild: dict, strict: dict) -> sd.RunData:
    spec = sd.RUNS[key]
    bed_h = 20 * hours
    m = sd.Messwerte(hours=hours, patients=20, exposure_bed_h=bed_h, FPR={"ECG": 0.024, "Vent": 0.025, "SpO2": 0.03, "ABP": 0.026},
                     P_C=0.011, d_false=8.3, d_true=30.0, n_false_alarms=int(45 * bed_h), n_crises=n_cr,
                     crises_per_bed_day=n_cr / bed_h * 24, c_art_a=0.06, c_art_b=0.48, TPR={"ECG": 0.6, "Vent": 0.6, "SpO2": 0.6, "ABP": 0.6})
    rules = {r: _rule(int(false[r] * bed_h), bed_h, mild[r], strict[r], n_cr, int(false[r] * bed_h * 1.4)) for r in ("adaptiv", "r2", "r3")}
    return sd.RunData(spec=spec, m=m, rules=rules, notes=(), n_alarms=int(50 * bed_h))


def _synthetic_runs():
    return {
        "6h": _run("6h", 0.6, 6, 146, dict(adaptiv=3.35, r2=6.1, r3=2.11), dict(adaptiv=120, r2=130, r3=90), dict(adaptiv=109, r2=127, r3=86)),
        "A": _run("A", 0.7, 4, 107, dict(adaptiv=3.34, r2=5.9, r3=2.15), dict(adaptiv=97, r2=102, r3=81), dict(adaptiv=91, r2=102, r3=80)),
        "12h": _run("12h", 0.9, 12, 311, dict(adaptiv=3.14, r2=5.87, r3=2.02), dict(adaptiv=304, r2=309, r3=294), dict(adaptiv=283, r2=307, r3=292)),
    }


class ChordTests(unittest.TestCase):
    def test_gap_toy(self):
        runs = _synthetic_runs()
        g = k5.chord_gap(runs["12h"], "mild")
        self.assertIsNotNone(g)
        # Sehne zwischen 3-aus-4 (2,02; 94,5) und 2-aus-4 (5,87; 99,4) bei x = 3,14
        x2, y2, x3, y3 = g["x2"], g["y2"], g["x3"], g["y3"]
        yc = y3 + (y2 - y3) * (g["x"] - x3) / (x2 - x3)
        self.assertAlmostEqual(g["y_sehne"], yc, places=9)
        self.assertAlmostEqual(g["gap_pp"], g["y"] - yc, places=9)
        self.assertTrue(g["innerhalb"])
        self.assertGreater(g["gap_pp"], 0)
        g24 = k5.chord_gap(runs["12h"], "mild", "tag")
        self.assertAlmostEqual(g24["x"], 24 * g["x"], places=9)
        self.assertAlmostEqual(g24["gap_pp"], g["gap_pp"], places=9)

    def test_missing_rule(self):
        runs = _synthetic_runs()
        r = runs["A"]
        r2 = sd.RunData(spec=r.spec, m=r.m, rules={"adaptiv": r.rules["adaptiv"]}, notes=(), n_alarms=1)
        self.assertIsNone(k5.chord_gap(r2))


class SyntheticFigureTests(unittest.TestCase):
    def test_blocks_1_2_3(self):
        runs = _synthetic_runs()
        rl = [runs[k] for k in sd.RUN_ORDER if k in runs]
        for x_unit in ("h", "tag"):
            for mode in ("mild", "streng"):
                fig = k5.fig_betriebspunkte(rl, x_unit, mode)
                self.assertEqual(sum(1 for ax in fig.axes if ax.axison), 3)
                self.assertTrue(any(k5.X_UNITS[x_unit] in ax.get_xlabel() for ax in fig.axes))
                plt.close(fig)
        fig = k5.fig_betriebspunkte(rl[:1], "h")
        self.assertEqual(len(fig.axes), 1)
        plt.close(fig)
        fig = k5.fig_betriebspunkte(rl)                      # Standard ereignisbezogen: nur Läufe mit Netzdatei (12 h, A), 6 h nie
        self.assertLessEqual(sum(1 for ax in fig.axes if ax.axison), 2)
        plt.close(fig)
        fig = k5.fig_bilanz(runs["12h"])
        self.assertEqual(len(fig.axes), 2)
        self.assertIn("K = 311", fig.axes[1].get_xlabel())
        plt.close(fig)
        fig = k5.fig_eta(rl, "mild")
        self.assertEqual(len(fig.axes), 2)
        lines = [ln for ln in fig.axes[0].get_lines() if len(ln.get_xdata()) == 3]
        self.assertEqual(len(lines), 3)                     # eine Linie je Regel durch 6 h, A, 12 h
        plt.close(fig)
        self.assertIn("3-aus-4", k5.aussage_1(rl, "mild", "h"))
        self.assertIn("Ohne Journale", k5.aussage_1(rl))
        self.assertIn("12 h", k5.aussage_2(runs["12h"]))

    def test_block_5_without_latency_files(self):
        runs = _synthetic_runs()
        ds = _ws_datasets()
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(k5.delays_by_rule(runs["12h"], Path(tmp)), {})
        fig = k5.fig_kosten([runs["12h"]], ds, lat_runs=("12h",))
        self.assertEqual(len(fig.axes), 2)
        plt.close(fig)
        self.assertTrue(k5.aussage_7(ds))
        self.assertTrue(k5.aussage_6(ds))
        self.assertTrue(k5.aussage_4(ds))
        self.assertEqual(len(k5.delta_rows(ds)), 2)
        self.assertEqual(len(k5.sens_rows(ds)), 20)
        for make in (lambda: k5.fig_froc(ds), lambda: k5.fig_schar(ds, "alpha"), lambda: k5.fig_geraet(ds)):
            plt.close(make())

    def test_summary_without_data(self):
        k = k5.kennzahlen({}, {})
        saetze = k5.zusammenfassung(k)
        self.assertEqual(len(saetze), 4)
        self.assertTrue(all("–" in s for s in saetze))
        self.assertEqual(k5._d(1.234, 1, True), "+1,2")
        self.assertEqual(k5._d(None), "–")
        self.assertEqual(k5.FILES[1], "K5_1_Betriebspunkte")
        self.assertEqual(len(k5.FRAGEN), 8)


@unittest.skipUnless(HAVE_12H, "Läufe 12 h / 6 h / A fehlen in docs/reports")
class RealDataTests(unittest.TestCase):
    def test_kennzahlen(self):
        k = k5.kennzahlen(RUNS, WS)
        self.assertAlmostEqual(k.red_false_pct, 46.5, delta=0.3)
        self.assertAlmostEqual(k.diff_mild_pp, -1.6, delta=0.1)
        self.assertAlmostEqual(k.diff_strict_pp, -7.7, delta=0.1)
        self.assertAlmostEqual(k.eta06_adaptiv, 82.2, delta=0.1)
        self.assertAlmostEqual(k.eta06_r3, 61.6, delta=0.1)
        self.assertAlmostEqual(k.lat_diff_s, 7.1, delta=0.1)
        self.assertAlmostEqual(k.gap09_pp, 1.8, delta=0.1)
        self.assertAlmostEqual(k.gap07_pp, 8.7, delta=0.1)
        self.assertEqual(k.gap07_quelle, "Netzlauf A")
        saetze = k5.zusammenfassung(k)
        self.assertIn("+8,7 pp", saetze[0])
        self.assertIn("46 %", saetze[1])
        self.assertIn("82 statt 62 %", saetze[2])
        self.assertIn("7,1 s", saetze[3])
        if WS.get("0,9") and WS["0,9"].werk:
            self.assertTrue(all(math.isfinite(v) for v in k.delta_alpha + k.delta_dmin + k.false_T))

    def test_latency_rows_and_export(self):
        rows = k5.latency_rows([RUNS["12h"], RUNS["A"]])
        self.assertEqual(len(rows), 6)
        med = {(r["run"], r["rule"]): r["median"] for r in rows}
        self.assertAlmostEqual(med[("12h", "adaptiv")], 10.2, delta=0.1)
        self.assertAlmostEqual(med[("12h", "r2")], 3.1, delta=0.1)
        with tempfile.TemporaryDirectory() as tmp:
            written = k5.export_all(RUNS, WS, Path(tmp), x_unit="h")
            expected = 2 * ((7 + (1 if k5.has_klassisch(WS) else 0)) if WS else 4)
            self.assertEqual(len(written), expected)
            self.assertTrue(all(p.exists() for p in written))


if __name__ == "__main__":
    unittest.main()
