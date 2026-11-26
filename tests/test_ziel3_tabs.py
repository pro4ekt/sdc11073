"""Kontrollzahlen der drei Reiter von app_ziel3.py: K4 (Modell), D1–D4 (Experimente), V4 (Vergleich).

Sollwerte aus der Aufgabenstellung und aus docs/reports/*.json.
"""

import tempfile
import unittest
from pathlib import Path

import matplotlib.pyplot as plt

from analysis.belastungsmodell_app import export_ziel3 as fz
from analysis.belastungsmodell_app import model_ziel3 as mz
from analysis.belastungsmodell_app import sim_data as sd

RUNS = sd.load_runs()


class BarLabelTests(unittest.TestCase):
    def test_rule(self):
        self.assertEqual(fz.bar_label(0.0116), "0")
        self.assertEqual(fz.bar_label(0.049), "0")
        self.assertEqual(fz.bar_label(0.63), "0,6")
        self.assertEqual(fz.bar_label(2.015), "2,0")
        self.assertEqual(fz.bar_label(9.96), "10,0")
        self.assertEqual(fz.bar_label(11.07), "11")
        self.assertEqual(fz.bar_label(120.73), "121")
        self.assertEqual(fz.bar_label(1408), "1408")

    def test_thousands(self):
        self.assertEqual(fz.thousands(12058), "12 058")
        self.assertEqual(fz.thousands(311), "311")

    def test_axis_label_with_mean_bar(self):
        self.assertIn(r"\overline{FPR}", fz.axis_label("MFPR"))
        self.assertEqual(fz.AXIS_SHORT["MFPR"], "|M|·FPR̄")
        self.assertEqual(fz.axis_label("c_art"), mz.X_AXES["c_art"].label)

    def test_eta_labels(self):
        self.assertEqual(fz.axis_label("tpr"), r"Anzeigewahrscheinlichkeit je Gerät $\eta_j$")
        self.assertEqual(sd.RUNS["6h"].label, "6 h")
        self.assertEqual(sd.RUNS["6h"].label_eta, "6 h (η_j = 0,6)")
        self.assertEqual(sd.RUNS["12h"].label_tex, r"12 h ($\eta_j$ = 0,9)")
        self.assertNotIn("p = 0", sd.RUNS["6h"].label_eta)
        self.assertIn(r"\eta_j", fz.run_title(RUNS["12h"]))

    def test_measured_tpr(self):
        m = RUNS["12h"].m
        self.assertEqual(set(m.TPR), {"ECG", "Vent", "SpO2", "ABP"})
        self.assertAlmostEqual(m.tpr_mean, 0.59, delta=0.005)
        lo, hi = m.tpr_range
        self.assertTrue(0.58 <= lo <= hi <= 0.62)
        self.assertAlmostEqual(RUNS["6h"].m.tpr_mean, 0.45, delta=0.005)


class K4Tests(unittest.TestCase):
    """Median, je Station (10 Betten) und Woche."""

    def setUp(self):
        self.tab = mz.k4_table(mz.Params(), 70.0, Ms=fz.K4_MS_FIG)

    def test_control_values(self):
        u = {rule: self.tab[rule][0] for rule in mz.K4_RULES}
        v = {rule: self.tab[rule][1] for rule in mz.K4_RULES}
        self.assertEqual(fz.K4_MS_FIG, (4, 5, 6, 8, 10, 12))
        # |M| = 4 (Episoden): links 22,11 / 4,04 / 0,03, rechts 1,1 / 2,4 / 15 (15,25 nach der Formatregel ganzzahlig)
        self.assertEqual([fz.bar_label(u[r][0]) for r in ("r2", "adaptiv", "r3")], ["22", "4,0", "0"])
        self.assertAlmostEqual(u["r2"][0], 22.11, delta=0.01)
        self.assertAlmostEqual(u["adaptiv"][0], 4.04, delta=0.01)
        self.assertEqual([fz.bar_label(v[r][0]) for r in ("r2", "adaptiv", "r3")], ["1,1", "2,4", "15"])
        self.assertAlmostEqual(v["r3"][0], 15.25, delta=0.01)
        # |M| = 12: links 240 ↑ / 0 / 1,9
        self.assertEqual([fz.bar_label(u[r][-1]) for r in ("r2", "adaptiv", "r3")], ["240", "0", "1,9"])
        self.assertAlmostEqual(u["r2"][-1], 240.19, delta=0.01)

    def test_left_axis_clip(self):
        top = fz.k4_left_top(self.tab)
        self.assertEqual(top, 28.0)                       # ceil(1,25 · 22,11)
        self.assertTrue(all(self.tab["r2"][0][1:] > top))  # 2-aus-N ab |M| = 5 abgeschnitten
        self.assertTrue(all(self.tab["adaptiv"][0] <= top))

    def test_figure_builds(self):
        plt.close(fz.fig_K4(mz.Params(), mit_parametern=True))
        plt.close(fz.fig_K4(mz.Params(P=(3, 1))))


class D1Tests(unittest.TestCase):
    def test_reduction(self):
        self.assertAlmostEqual(100 * sd.reduction(RUNS["12h"]), 93.0, delta=0.05)
        self.assertAlmostEqual(100 * sd.reduction(RUNS["1h"]), 92.6, delta=0.05)
        self.assertAlmostEqual(100 * sd.reduction(RUNS["6h"]), 92.7, delta=0.05)

    def test_bars(self):
        m = RUNS["12h"].m
        self.assertAlmostEqual(m.false_alarms_per_bed_h, 45.02, delta=0.01)
        self.assertAlmostEqual(RUNS["12h"].rules["adaptiv"].false_per_bed_h, 3.14, delta=0.01)


class D2Tests(unittest.TestCase):
    def test_remaining_pct_12h(self):
        for rule, expected in (("r2", 13.0), ("adaptiv", 7.0), ("r3", 4.5)):
            val, ci = sd.remaining_pct(RUNS["12h"], rule)
            self.assertAlmostEqual(val, expected, delta=0.05, msg=rule)
            self.assertTrue(ci[0] < val < ci[1])

    def test_remaining_pct_other_runs(self):
        self.assertAlmostEqual(sd.remaining_pct(RUNS["1h"], "adaptiv")[0], 7.4, delta=0.05)
        self.assertAlmostEqual(sd.remaining_pct(RUNS["6h"], "adaptiv")[0], 7.3, delta=0.05)


class D4Tests(unittest.TestCase):
    def test_bilanz_12h(self):
        expected = {"adaptiv": (1063, 754, 283, 21, 7), "r2": (1725, 1408, 307, 2, 2), "r3": (782, 485, 292, 2, 17)}
        for rule, (esc, falsch, erkannt, nach_ende, verpasst) in expected.items():
            b = sd.bilanz_row(RUNS["12h"], rule)
            self.assertEqual((b["Eskalationen gesamt"], b["davon falsch_esk"], b["erkannt"], b["nach Ende"],
                              b["verpasst"]), (esc, falsch, erkannt, nach_ende, verpasst), msg=rule)
            self.assertEqual(b["Krisen"], 311)
            self.assertEqual(b["Gerätealarme"], 12058)       # Summe der Stundenwerte (hourly)
            self.assertEqual(b["davon falsch"], 10802)       # messwerte.n_false_alarms = außerhalb von Krisen
            self.assertEqual(b["in Krisen"], 1256)           # Gerätealarme − außerhalb von Krisen
            self.assertEqual(erkannt + nach_ende + verpasst, 311)
        title = fz.run_title_lines(RUNS["12h"])
        self.assertEqual(title.count("\n"), 2)
        self.assertIn("12 058 = 10 802 außerhalb von Krisen + 1 256 in Krisen", title)
        self.assertTrue(title.endswith("311 Krisen (je Krise mehrere Geräte)"))
        self.assertEqual(fz.run_title_lines(RUNS["12h"], compact=True).count("\n"), 3)

    def test_other_runs(self):
        self.assertEqual(RUNS["1h"].n_alarms, 977)
        self.assertEqual(RUNS["6h"].n_alarms, 5985)
        b = sd.bilanz_row(RUNS["6h"], "adaptiv")
        self.assertEqual((b["erkannt"], b["nach Ende"], b["verpasst"]), (109, 11, 26))
        self.assertEqual(sd.count_ci(0)[0], 0.0)
        lo, hi = sd.count_ci(754)
        self.assertTrue(lo < 754 < hi)


class V4Tests(unittest.TestCase):
    def test_values_12h(self):
        v = sd.v4_values(RUNS["12h"], "adaptiv")
        self.assertAlmostEqual(v.sim_false, 3.14, delta=0.01)
        self.assertAlmostEqual(v.model_false_a, 2.21, delta=0.01)          # Episoden, c_art alle 4
        self.assertAlmostEqual(v.model_false_b, 5.90, delta=0.01)
        self.assertAlmostEqual(v.model_missed_1000, 5.8, delta=0.1)              # (1 − 0,9942)·1000
        self.assertAlmostEqual(v.sim_missed_1000, 1000 * 7 / 311, delta=0.01)
        self.assertAlmostEqual(v.sim_after_end_1000, 1000 * 21 / 311, delta=0.01)
        self.assertAlmostEqual(v.sim_missed_1000 + v.sim_after_end_1000, 1000 * 28 / 311, delta=0.01)
        self.assertTrue(v.sim_missed_strict_ci_1000[0] < 1000 * 28 / 311 < v.sim_missed_strict_ci_1000[1])

    def test_model_remaining_range(self):
        lo, hi = sd.model_remaining_range(RUNS["12h"], "adaptiv")
        self.assertTrue(0 < lo < hi < 100)
        self.assertAlmostEqual(lo, 100 * 2.207 / 45.02, delta=0.1)      # nach Anzahl: false_day/raw_day


class V4RelativeTests(unittest.TestCase):
    def test_relative_12h(self):
        rel = sd.v4_relative(RUNS["12h"])
        self.assertEqual([round(rel[r].model_pct) for r in ("r2", "adaptiv", "r3")], [100, 61, 20])    # Episoden
        # Aufgabenstellung: 100 / 53 / 34 %; exakt 100 / 53,5 / 34,4 % (3,142/5,868 bzw. 2,021/5,868)
        self.assertAlmostEqual(rel["r2"].sim_pct, 100.0)
        self.assertAlmostEqual(rel["adaptiv"].sim_pct, 53.5, delta=0.1)
        self.assertAlmostEqual(rel["r3"].sim_pct, 34.4, delta=0.1)
        lo, hi = rel["adaptiv"].sim_ci_pct
        self.assertTrue(lo < rel["adaptiv"].sim_pct < hi)
        self.assertAlmostEqual(rel["r2"].sim_ci_pct[0], 100 * 5.5652 / 5.8678, delta=0.05)

    def test_split_label(self):
        v = sd.v4_values(RUNS["12h"], "adaptiv")
        self.assertEqual(fz.v4_split_label(v.sim_missed_1000, v.sim_after_end_1000), "90 = 23 + 67")
        self.assertEqual(fz.v4_split_label(0.0, 0.0), "0 = 0 + 0")

    def test_v_figures_without_1h(self):
        sel = list(RUNS.values())
        self.assertEqual([r.key for r in fz._vruns(sel)], [k for k in RUNS if k != "1h"])      # nur 1 h fehlt
        self.assertEqual(fz._main_run(fz._vruns(sel)).key, "12h")
        self.assertEqual(fz.param_label(RUNS["12h"]), "Parameter des 12-h-Laufs")
        self.assertEqual(fz.AXIS_LABELS["M"], "Alarmkanäle am Bett |M| (Prioritäten anteilig skaliert)")


class FigureAndExportTests(unittest.TestCase):
    def test_d_figures_and_export(self):
        sel = list(RUNS.values())
        plt.close(fz.fig_D1(sel))
        plt.close(fz.fig_D2(sel, "absolut"))
        plt.close(fz.fig_D3(sel, "mild"))
        plt.close(fz.fig_D4(sel[:1], mit_parametern=True))
        with tempfile.TemporaryDirectory() as tmp:
            written = fz.export_experimente(RUNS, Path(tmp))
            self.assertEqual(sorted(p.stem for p in written), sorted(list(fz.D_FILES.values()) * 2))
            self.assertTrue(all(p.stat().st_size > 0 for p in written))
            self.assertEqual(fz.export_experimente({}, Path(tmp)), [])

    def test_v2_prozent_and_v4_runs(self):
        plt.close(fz.fig_V2(list(RUNS.values()), "prozent", n_points=8))
        for key in ("12h", "6h", "1h"):
            plt.close(fz.fig_V4(RUNS[key]))
            plt.close(fz.fig_V4(RUNS[key], "absolut"))
        plt.close(fz.fig_V3(list(RUNS.values()), "streng", "zoom", n_points=8))
        plt.close(fz.fig_V3(list(RUNS.values()), "mild", "uebersicht", n_points=8))
        plt.close(fz.fig_V3([RUNS["6h"]], "streng", "zoom", n_points=8))      # nur ein Lauf: rechte Tafel ohne Punkte

    def test_main_without_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(fz.main(["--nur", "experimente", "--daten", tmp, "--outdir", tmp]), 0)
            self.assertEqual(list(Path(tmp).glob("*.png")), [])


if __name__ == "__main__":
    unittest.main()
