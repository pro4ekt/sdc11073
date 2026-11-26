"""Netzlauf-Auswertung aus Journalen (analysis/roc_ziel3/netzlauf_auswertung.py) und die Läufe A–D in sim_data.

Prüft das Clopper-Pearson-Intervall, dass die Auswertung aus Journal A/B + decisions.jsonl die Zahlen der
PC-2-Auswertung des 12-h-Laufs wiedergibt, die Kontrollzahlen der Übersicht von PC 2 (ziel3_uebersicht) für die
vorhandenen Läufe A–D, die Laufdefinitionen (Marker, Linie in D3) und die Abbildungen D1–D4 mit den neuen Läufen.
"""

import json
import math
import tempfile
import unittest
from pathlib import Path

import matplotlib.pyplot as plt

from analysis.belastungsmodell_app import export_ziel3 as fz
from analysis.belastungsmodell_app import sim_data as sd
from analysis.roc_ziel3 import netzlauf_auswertung as na
from analysis.roc_ziel3 import run_roc as rr

NETZ_12H = rr.ROC_DIR / rr.NETZ_NAME
NETZ_A = rr.ROC_DIR_P07 / rr.NETZ_P07
HAVE_12H = (NETZ_12H / "alarms.jsonl").exists() and (rr.PC1_DIR / "decisions.jsonl").exists()
HAVE_A = (NETZ_A / "alarms.jsonl").exists() and (rr.PC1_DIR_P07 / "decisions.jsonl").exists()
RUNS = sd.load_runs()

# Kontrollzahlen der Übersicht von PC 2 (Aufgabenstellung): Fehleskalationen je Bett·h und erkannt mild (%) für
# adaptiv / 2-aus-4 / 3-aus-4
UEBERSICHT = {
    "A": dict(false=(3.34, 5.90, 2.15), mild=(90.7, 95.3, 75.7)),
    "B": dict(false=(3.16, 5.94, 2.25), mild=(94.4, 98.9, 90.0)),
    "C": dict(false=(3.21, 6.01, 2.11), mild=(100.0, 100.0, 98.2)),
    "D": dict(false=(1.27, 3.84, 1.12), mild=(98.6, 98.6, 95.9)),
}


class ClopperPearsonTests(unittest.TestCase):
    def test_against_pc2_intervals(self):
        lo, hi = na.clopper_pearson(304, 311)
        self.assertAlmostEqual(lo, 0.95418, places=4)
        self.assertAlmostEqual(hi, 0.99090, places=4)
        lo, hi = na.clopper_pearson(283, 311)
        self.assertAlmostEqual(lo, 0.87251, places=4)
        self.assertAlmostEqual(hi, 0.93934, places=4)

    def test_edges(self):
        self.assertEqual(na.clopper_pearson(0, 10)[0], 0.0)
        self.assertEqual(na.clopper_pearson(10, 10)[1], 1.0)
        self.assertAlmostEqual(na.clopper_pearson(0, 10)[1], 1 - 0.025 ** (1 / 10), places=6)
        self.assertTrue(all(math.isnan(v) for v in na.clopper_pearson(0, 0)))
        self.assertAlmostEqual(na.binom_cdf(5, 10, 0.5), 0.623046875, places=9)


@unittest.skipUnless(HAVE_12H, "Journale des 12-h-Netzlaufs fehlen")
class Pipeline12hTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res, cls.mw = na.auswerten(NETZ_12H, rr.PC1_DIR)
        cls.ref = json.loads((sd.DATA_DIR / "results_ziel3_12h.json").read_text(encoding="utf-8"))
        cls.ref_mw = json.loads((sd.DATA_DIR / "messwerte_ziel3_12h.json").read_text(encoding="utf-8"))

    def test_rules_match_pc2(self):
        for rule in ("adaptiv", "2-aus-4", "3-aus-4"):
            a, b = self.res["rules"][rule], self.ref["rules"][rule]
            for f in ("n_escalations_window", "n_false", "caught", "caught_strict"):
                self.assertEqual(a[f], b[f], f"{rule} {f}")
            self.assertAlmostEqual(a["false_per_bed_h"], b["false_per_bed_h"], delta=0.01, msg=rule)
            for f in ("caught_frac_ci", "caught_strict_frac_ci"):
                self.assertAlmostEqual(a[f][0], b[f][0], places=4, msg=f"{rule} {f}")
                self.assertAlmostEqual(a[f][1], b[f][1], places=4, msg=f"{rule} {f}")
        a, b = self.res["rules"]["1-aus-4"], self.ref["rules"]["1-aus-4"]
        self.assertLessEqual(abs(a["n_escalations_window"] - b["n_escalations_window"]), 5)      # Fensterrand
        self.assertEqual(self.res["n_alarms"], 12061)
        self.assertEqual(self.res["n_crises_window"], 311)
        self.assertEqual(len(self.res["hourly"]), 12)
        self.assertEqual(sum(h["alarms"] for h in self.res["hourly"]), 12061)

    def test_messwerte_match_pc2(self):
        for s in ("ECG", "Vent", "SpO2", "ABP"):
            self.assertAlmostEqual(self.mw["FPR"][s], self.ref_mw["FPR"][s], delta=0.0005, msg=s)
            self.assertAlmostEqual(self.mw["TPR"][s], self.ref_mw["TPR"][s], delta=0.005, msg=s)
        self.assertAlmostEqual(self.mw["P_C"], self.ref_mw["P_C"], delta=0.0003)
        self.assertAlmostEqual(self.mw["d_false_s"], self.ref_mw["d_false_s"], delta=0.1)
        self.assertAlmostEqual(self.mw["d_true_s"], self.ref_mw["d_true_s"], delta=0.1)
        self.assertLessEqual(abs(self.mw["n_false_alarms"] - self.ref_mw["n_false_alarms"]), 15)
        self.assertAlmostEqual(self.mw["c_art_a_all4_time_share"], self.ref_mw["c_art_a_all4_time_share"], delta=0.005)
        self.assertAlmostEqual(self.mw["c_art_b_ge2_time_share"], self.ref_mw["c_art_b_ge2_time_share"], delta=0.01)
        self.assertEqual(self.mw["n_crises"], 311)

    def test_written_files_load_through_sim_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = dict(self.res, name="ziel3_12h")
            mw = dict(self.mw, name="ziel3_12h")
            na.write(Path(tmp), res, mw)
            runs = sd.load_runs(Path(tmp), keys=("12h",))
            self.assertEqual(list(runs), ["12h"])
            r = runs["12h"]
            self.assertEqual(r.rules["adaptiv"].caught_mild, 304)
            self.assertEqual(r.n_alarms, 12061)
            self.assertAlmostEqual(r.m.mfpr, self.ref_mw["FPR"]["ECG"] + self.ref_mw["FPR"]["Vent"]
                                   + self.ref_mw["FPR"]["SpO2"] + self.ref_mw["FPR"]["ABP"], delta=0.002)


@unittest.skipUnless(HAVE_A, "Journale des Netzlaufs A (ziel3_4h_p07) fehlen")
class PipelineATests(unittest.TestCase):
    def test_control_numbers_of_pc2_overview(self):
        res, mw = na.auswerten(NETZ_A, rr.PC1_DIR_P07)
        ctl = UEBERSICHT["A"]
        for rule, f, m in zip(("adaptiv", "2-aus-4", "3-aus-4"), ctl["false"], ctl["mild"]):
            r = res["rules"][rule]
            self.assertAlmostEqual(r["false_per_bed_h"], f, delta=0.01, msg=rule)
            self.assertAlmostEqual(100 * r["caught_frac"], m, delta=0.1, msg=rule)
        self.assertEqual(res["n_crises_window"], 107)
        self.assertEqual(res["rules"]["adaptiv"]["n_escalations_window"], 367)
        self.assertAlmostEqual(res["hours"], 4.0, delta=0.05)


class RunSpecTests(unittest.TestCase):
    def test_order_and_labels(self):
        self.assertEqual(sd.RUN_ORDER, ("1h", "6h", "A", "B", "12h", "C", "D"))
        self.assertEqual(sd.D3_LINE_RUNS, ("6h", "A", "B", "12h"))
        self.assertEqual([sd.RUNS[k].tpr for k in sd.D3_LINE_RUNS], [0.6, 0.7, 0.8, 0.9])
        self.assertEqual(sd.RUNS["A"].label_eta, "A 4 h (η_j = 0,7)")
        self.assertEqual(sd.RUNS["C"].label_eta, "C 4 h (η_j = 0,9, lange Krisen)")
        self.assertEqual(sd.RUNS["D"].label_eta, "D 6 h (η_j = 0,9, Idealfall)")
        self.assertEqual(sd.RUNS["12h"].label_eta, "12 h (η_j = 0,9)")          # unverändert
        self.assertEqual((sd.RUNS["C"].marker, sd.RUNS["D"].marker), ("D", "s"))
        self.assertFalse(sd.RUNS["C"].on_line or sd.RUNS["D"].on_line)
        self.assertTrue(all(sd.RUNS[k].on_line for k in sd.D3_LINE_RUNS))
        self.assertFalse(sd.RUNS["1h"].filled)
        self.assertEqual(sd.RUNS["A"].name, "ziel3_4h_p07")
        self.assertEqual(sd.RUNS["B"].name, "ziel3_4h_p08")
        self.assertEqual(sd.RUNS["C"].name, "ziel3_4h_lang")
        self.assertEqual(sd.RUNS["D"].name, "ziel3_6h_ideal")
        self.assertEqual(sd.ORIGINAL_RUNS, ("1h", "6h", "12h"))

    def test_available_follows_files(self):
        avail = sd.available()
        self.assertEqual(avail, [k for k in sd.RUN_ORDER if all(p.exists() for p in sd.run_files(k))])


@unittest.skipUnless("A" in RUNS, "Lauf A fehlt in docs/reports")
class RunATests(unittest.TestCase):
    def test_values(self):
        r = RUNS["A"]
        ctl = UEBERSICHT["A"]
        for rule, f, m in zip(("adaptiv", "r2", "r3"), ctl["false"], ctl["mild"]):
            self.assertAlmostEqual(r.rules[rule].false_per_bed_h, f, delta=0.01, msg=rule)
            self.assertAlmostEqual(100 * r.rules[rule].caught_frac("mild"), m, delta=0.1, msg=rule)
        self.assertEqual(r.m.n_crises, 107)
        self.assertEqual(r.spec.tpr, 0.7)
        self.assertEqual(set(r.rules), {"adaptiv", "r2", "r3", "r1"})

    def test_figures_with_a(self):
        runs = [RUNS[k] for k in sd.RUN_ORDER if k in RUNS]
        self.assertGreaterEqual(len(runs), 4)
        for mode in sd.MODES:
            fig = fz.fig_D3(runs, mode)
            lines = [ln for ln in fig.axes[0].get_lines() if ln.get_linestyle() == "-" and len(ln.get_xdata()) >= 3]
            self.assertGreaterEqual(len(lines), 3)                      # eine Linie je Regel durch 6 h, A, 12 h
            plt.close(fig)
        for make in (lambda: fz.fig_D1(runs), lambda: fz.fig_D2(runs, "prozent"), lambda: fz.fig_D4(runs)):
            plt.close(make())
        with tempfile.TemporaryDirectory() as tmp:
            written = fz.export_experimente(RUNS, Path(tmp), suffix="_v2")
            self.assertEqual(sorted(p.name for p in written),
                             sorted(f"{n}_v2.{ext}" for n in fz.D_FILES.values() for ext in ("png", "svg")))


class OverviewTests(unittest.TestCase):
    def test_present_runs_match_overview(self):
        """Alle vorhandenen Läufe B–D gegen die Übersicht von PC 2 (A separat oben)."""
        for key in ("B", "C", "D"):
            if key not in RUNS:
                continue
            r = RUNS[key]
            ctl = UEBERSICHT[key]
            tol = 0.005 + 1.0 / r.m.exposure_bed_h          # Rundung der Übersicht + eine Episode je Bett·h
            for rule, f, m in zip(("adaptiv", "r2", "r3"), ctl["false"], ctl["mild"]):
                self.assertAlmostEqual(r.rules[rule].false_per_bed_h, f, delta=tol, msg=f"{key} {rule}")
                self.assertAlmostEqual(100 * r.rules[rule].caught_frac("mild"), m, delta=0.1, msg=f"{key} {rule}")


if __name__ == "__main__":
    unittest.main()
