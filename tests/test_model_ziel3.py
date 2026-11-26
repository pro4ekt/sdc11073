"""Kontrollzahlen der Seite „Ziel 3 – vier Kurven“ (analysis/belastungsmodell_app/model_ziel3.py).

Sollwerte aus Kapitel 5 (5.3) und der Aufgabenstellung, alle am Median (Standardparameter);
Toleranz ±0,01 Prozentpunkte bzw. ±0,01 Stück.
"""

import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from analysis.belastungsmodell_app import model_ziel3 as mz


def pct(x):
    return 100.0 * x


class RoundingTests(unittest.TestCase):
    def test_rhu_rounds_half_up(self):
        self.assertEqual(mz.rhu(2.5), 3)
        self.assertEqual(mz.rhu(0.5), 1)
        self.assertEqual(mz.rhu(1.5), 2)
        self.assertEqual(mz.rhu(2.4999), 2)
        self.assertEqual(mz.rhu(3.0), 3)

    def test_rhu_differs_from_builtin_round(self):
        self.assertEqual(round(2.5), 2)
        self.assertEqual(mz.rhu(2.5), 3)


class CalibrationTests(unittest.TestCase):
    def test_median(self):
        c = mz.calibrate(mz.Params())
        self.assertAlmostEqual(c.N_d, 150.0)
        self.assertAlmostEqual(c.PC, 0.00145, places=5)
        self.assertAlmostEqual(c.FPR, 0.00313, places=5)
        self.assertAlmostEqual(c.MFPR, 0.0125, places=4)
        self.assertAlmostEqual(c.w, math.log(0.9 / c.FPR))
        self.assertAlmostEqual(c.ceiling, 0.875)
        self.assertAlmostEqual(c.crises_day, 4.1667, delta=0.0001)
        self.assertTrue(c.valid)

    def test_noise_direct_keeps_prevalence(self):
        c = mz.calibrate(mz.Params(mfpr_direct=0.8))
        self.assertAlmostEqual(c.FPR, 0.2)
        self.assertAlmostEqual(c.PC, mz.calibrate(mz.Params()).PC)

    def test_params_validation(self):
        with self.assertRaises(ValueError):
            mz.Params(P=(3,))
        with self.assertRaises(ValueError):
            mz.Params(P=(3, 4))
        with self.assertRaises(ValueError):
            mz.Params(tpr=1.0)
        self.assertEqual(mz.Params().M, 4)
        self.assertEqual(mz.Params(P=(3, 3, 2, 1)).counts(), (2, 1, 1))


class Kapitel5Tests(unittest.TestCase):
    """Übereinstimmung mit Kapitel 5 (Tabelle der Aufgabenstellung)."""

    def setUp(self):
        self.p = mz.Params()

    def test_table(self):
        # ARR und System-TPR: Zeitanteile (Kapitel 5); Fehleskalationen je Tag als Episoden (Monte-Carlo-Prüfung),
        # daneben die alte Umrechnung Zeitanteil·86400/d_false (false_day_zeitanteil)
        expected = {"adaptiv": (87.48, 99.63, 16.93, 16.90), "r2": (87.38, 99.63, 17.19, 17.03),
                    "r3": (87.50, 94.77, 16.88, 16.88)}
        for rule, (arr, tpr, fd, fd_zeit) in expected.items():
            r = mz.evaluate(self.p, rule)
            self.assertAlmostEqual(pct(r.ARR), arr, delta=0.01, msg=rule)
            self.assertAlmostEqual(pct(r.tpr_inf), tpr, delta=0.01, msg=rule)
            self.assertAlmostEqual(r.false_day, fd, delta=0.01, msg=rule)
            self.assertAlmostEqual(r.false_day_zeitanteil, fd_zeit, delta=0.01, msg=rule)
            self.assertAlmostEqual(r.reduction_count, 1 - r.false_day / 135.0, places=9)

    def test_episode_rate(self):
        rate, common = mz.episode_rate(self.p, "r2")
        cal = mz.calibrate(self.p)
        self.assertAlmostEqual(common, 0.5 * cal.FPR / 8.0)
        q = 0.5 * cal.FPR
        self.assertAlmostEqual(rate, 6 * 2 * q ** 2 * (1 - q) ** 2 / 8.0)     # 6 Paare, je 2 Eintritte
        rate_ohne, common_ohne = mz.episode_rate(self.p, "ohne")
        self.assertAlmostEqual(rate_ohne + common_ohne, 4 * cal.FPR / 8.0)
        r = mz.evaluate(self.p, "r2")
        self.assertAlmostEqual(r.avoid_day, rate * 86400 * (1 - cal.PC))
        self.assertAlmostEqual(mz.evaluate(mz.Params(P=(3, 1)), "adaptiv").false_day, 18.97, delta=0.01)

    def test_ohne_filter(self):
        r = mz.evaluate(self.p, "ohne")
        self.assertAlmostEqual(r.false_day, 135.00, delta=0.01)
        self.assertAlmostEqual(r.ARR, 0.0)
        self.assertAlmostEqual(r.tpr_inf, 1 - 0.1 ** 4)
        self.assertEqual(r.tpr_T, r.tpr_inf)

    def test_adaptiv_delta_min_1(self):
        r = mz.evaluate(mz.Params(delta_min=1.0), "adaptiv")
        self.assertAlmostEqual(pct(r.ARR), 87.48, delta=0.01)
        self.assertAlmostEqual(pct(r.tpr_inf), 95.58, delta=0.01)

    def test_noise_direct(self):
        for rule, arr in (("adaptiv", 85.94), ("r2", 80.96), ("r3", 87.04)):
            r = mz.evaluate(mz.Params(mfpr_direct=0.8), rule)
            self.assertAlmostEqual(r.FPR, 0.2)
            self.assertAlmostEqual(pct(r.ARR), arr, delta=0.01, msg=rule)

    def test_c_art_extremes(self):
        for c, arr_ad, arr_r2 in ((0.0, 99.91, 99.53), (1.0, 75.00, 75.00)):
            p = mz.Params(c_art=c)
            self.assertAlmostEqual(pct(mz.evaluate(p, "adaptiv").ARR), arr_ad, delta=0.01, msg=f"c_art={c}")
            self.assertAlmostEqual(pct(mz.evaluate(p, "r2").ARR), arr_r2, delta=0.01, msg=f"c_art={c}")

    def test_escalation_times_adaptiv(self):
        o = mz.outcome((3, 1), self.p, "adaptiv")
        self.assertEqual(o.branch, "schmelzen")
        self.assertAlmostEqual(o.t, 15.41, delta=0.01)
        o = mz.outcome((1, 1), self.p, "adaptiv")
        self.assertEqual(o.branch, "schmelzen")
        self.assertAlmostEqual(o.t, 23.90, delta=0.01)
        o = mz.outcome((3, 3, 1), self.p, "adaptiv")
        self.assertEqual(o.branch, "sofort")
        self.assertAlmostEqual(o.t, 6.67, delta=0.01)
        self.assertEqual(mz.outcome((3,), self.p, "adaptiv").branch, "nie")

    def test_outcome_k_aus_n(self):
        self.assertEqual(mz.outcome((3,), self.p, "r2").branch, "nie")
        o = mz.outcome((3, 1), self.p, "r2")
        self.assertEqual((o.k, o.branch, o.t), (2, "sofort", 0.0))
        o = mz.outcome((3, 1), self.p, "r3")
        self.assertEqual((o.k, o.branch), (3, "nie"))
        self.assertEqual(mz.outcome((3, 1), mz.Params(P=(3, 1)), "r3").k, 2)   # k auf |M| begrenzt

    def test_invariants(self):
        for rule in mz.RULES:
            r = mz.evaluate(self.p, rule)
            self.assertAlmostEqual(r.avoid_day + r.common_day, r.false_day)
            self.assertLessEqual(r.tpr_T, r.tpr_inf + 1e-12)
            if rule in ("r2", "r3"):
                self.assertEqual(r.tpr_T, r.tpr_inf)   # k-aus-N eskaliert sofort (t = 0)
        with self.assertRaises(ValueError):
            mz.evaluate(self.p, "r4")


class KaltstartTests(unittest.TestCase):
    """Neu: Anteil erkannter Krisen bei endlicher Krisendauer (tpr_T)."""

    def test_tpr_T_d0_15(self):
        p = mz.Params(d0=15.0)
        for rule, tpr in (("adaptiv", 99.18), ("r2", 99.63), ("r3", 94.77)):
            self.assertAlmostEqual(pct(mz.evaluate(p, rule).tpr_T), tpr, delta=0.01, msg=rule)

    def test_tpr_T_d0_0(self):
        self.assertAlmostEqual(pct(mz.evaluate(mz.Params(d0=0.0), "adaptiv").tpr_T), 81.77, delta=0.01)

    def test_surv_crisis(self):
        p = mz.Params(d0=15.0, d_true=30.0)
        self.assertEqual(mz.surv_crisis(10.0, p), 1.0)
        self.assertEqual(mz.surv_crisis(15.0, p), 1.0)
        self.assertAlmostEqual(mz.surv_crisis(30.0, p), math.exp(-1.0))
        self.assertEqual(mz.surv_crisis(float("inf"), p), 0.0)
        # d0 wird auf d_true − 1 begrenzt
        self.assertAlmostEqual(mz.surv_crisis(10.0, mz.Params(d0=60.0, d_true=10.0)), math.exp(-1.0))


class K4Tests(unittest.TestCase):
    def test_M4_week(self):
        tab = mz.k4_table(mz.Params(), 70.0)
        expected = {"r2": (22.11, 1.08), "adaptiv": (4.04, 2.39), "r3": (0.03, 15.25)}      # Episoden
        for rule, (u, v) in expected.items():
            self.assertAlmostEqual(tab[rule][0][0], u, delta=0.01, msg=rule)
            self.assertAlmostEqual(tab[rule][1][0], v, delta=0.01, msg=rule)
        self.assertAlmostEqual(mz.calibrate(mz.Params()).crises_day * 70, 291.7, delta=0.05)

    def test_M12_week(self):
        tab = mz.k4_table(mz.Params(), 70.0)
        self.assertEqual(mz.K4_MS[-1], 12)
        self.assertAlmostEqual(tab["r2"][0][-1], 240.19, delta=0.01)
        self.assertAlmostEqual(tab["adaptiv"][0][-1], 0.00, delta=0.01)
        self.assertAlmostEqual(tab["r3"][0][-1], 1.88, delta=0.01)

    def test_k4_values_matches_table(self):
        p = mz.Params()
        u, v = mz.k4_values(mz.evaluate(p, "adaptiv"), 70.0)
        tab = mz.k4_table(p, 70.0)
        self.assertAlmostEqual(u, tab["adaptiv"][0][0])
        self.assertAlmostEqual(v, tab["adaptiv"][1][0])


class ScalePTests(unittest.TestCase):
    def test_examples(self):
        self.assertEqual(mz.scale_P((3, 3, 2, 1), 12), (3, 3, 3, 3, 3, 3, 2, 2, 2, 1, 1, 1))
        self.assertEqual(mz.scale_P((3, 3, 1, 1), 3), (3, 3, 1))

    def test_same_length_keeps_counts(self):
        for P in ((3, 3, 1, 1), (3, 3, 2, 1), (1, 3, 3, 1), (3,) * 6 + (1,) * 6):
            self.assertEqual(sorted(mz.scale_P(P, len(P)), reverse=True), sorted(P, reverse=True))

    def test_set_param_M(self):
        p = mz.set_param(mz.Params(), "M", 12)
        self.assertEqual(p.P, (3,) * 6 + (1,) * 6)
        self.assertEqual(mz.current_value(p, "M"), 12.0)


class ScenarioTests(unittest.TestCase):
    def test_simulation_parameters(self):
        p = mz.scenario_params("Parameter der Simulation")
        self.assertEqual(p.P, (3, 3, 2, 1))
        self.assertAlmostEqual(p.c_art, 0.06)
        for rule, per_hour in (("r2", 3.65), ("r3", 0.75), ("adaptiv", 2.23)):           # Episoden je Bett·h
            self.assertAlmostEqual(mz.evaluate(p, rule).false_day / 24, per_hour, delta=0.01, msg=rule)
        for rule, per_hour in (("r2", 2.19), ("r3", 0.70), ("adaptiv", 1.46)):           # alte Umrechnung
            self.assertAlmostEqual(mz.evaluate(p, rule).false_day_zeitanteil / 24, per_hour, delta=0.01, msg=rule)
        self.assertAlmostEqual(pct(mz.evaluate(p, "adaptiv").tpr_T), 99.42, delta=0.01)

    def test_12h_parameters_rounded(self):
        """Aufgabenstellung: FPR 0,0261 direkt, c_art 0,06, d_false 8,3, d_true 30,2, P(C) des 12-h-Laufs."""
        p = mz.Params(P=(3, 3, 2, 1), c_art=0.06, d_false=8.3, d_true=30.2, mfpr_direct=4 * 0.0261, pc_direct=0.0109)
        for rule, per_hour in (("r2", 3.62), ("adaptiv", 2.21), ("r3", 0.75)):
            self.assertAlmostEqual(mz.evaluate(p, rule).false_day / 24, per_hour, delta=0.01, msg=rule)


class MonteCarloControlTests(unittest.TestCase):
    """Formelwerte der Monte-Carlo-Gegenprobe: P (3, 3, 2, 1), c_art 0,1, d_false 8, FPR direkt, P(C) des 12-h-Laufs."""

    def test_table(self):
        expected = {0.008: (0.629, 0.358, 0.496), 0.026: (3.944, 1.223, 2.607)}
        for fpr, (r2, r3, ad) in expected.items():
            p = mz.Params(P=(3, 3, 2, 1), c_art=0.1, d_false=8.0, mfpr_direct=4 * fpr, pc_direct=0.0109)
            self.assertAlmostEqual(mz.evaluate(p, "r2").false_day / 24, r2, delta=0.01, msg=f"r2 {fpr}")
            self.assertAlmostEqual(mz.evaluate(p, "r3").false_day / 24, r3, delta=0.01, msg=f"r3 {fpr}")
            self.assertAlmostEqual(mz.evaluate(p, "adaptiv").false_day / 24, ad, delta=0.01, msg=f"adaptiv {fpr}")

    def test_scenarios_build_on_defaults(self):
        self.assertEqual(mz.scenario_params("Median-Intensivstation"), mz.Params())
        self.assertEqual(mz.scenario_params("geringe Last"), mz.Params(n=25.0))
        self.assertEqual(mz.scenario_params("stark instrumentiert").M, 12)
        self.assertEqual(mz.scenario_name(mz.Params()), "Median-Intensivstation")
        self.assertIsNone(mz.scenario_name(mz.Params(n=26.0)))


class SweepTests(unittest.TestCase):
    def test_grids(self):
        self.assertEqual(len(mz.x_grid("c_art", 60)), 60)
        self.assertEqual(list(mz.x_grid("M")), list(range(2, 13)))
        g = mz.x_grid("MFPR", 10)
        self.assertAlmostEqual(g[0], 0.005)
        self.assertAlmostEqual(g[-1], 1.2)

    def test_current_values(self):
        p = mz.Params()
        self.assertAlmostEqual(mz.current_value(p, "MFPR"), mz.calibrate(p).MFPR)
        self.assertAlmostEqual(mz.current_value(p, "c_art"), 0.5)
        self.assertAlmostEqual(mz.current_value(mz.set_param(p, "MFPR", 0.8), "MFPR"), 0.8)

    def test_k1_grows_with_c_art(self):
        ys = [r.false_day for r in mz.sweep(mz.Params(), "adaptiv", "c_art", [0.0, 0.5, 1.0])]
        self.assertTrue(ys[0] < ys[1] < ys[2])
        ohne = [r.false_day for r in mz.sweep(mz.Params(), "ohne", "c_art", [0.0, 0.5, 1.0])]
        self.assertTrue(all(abs(y - 135.0) < 0.01 for y in ohne))

    def test_k2_r2_degrades_with_noise(self):
        arr = [r.ARR for r in mz.sweep(mz.Params(), "r2", "MFPR", [0.0125, 0.1, 0.8])]
        self.assertTrue(arr[0] > arr[1] > arr[2])

    def test_k3_adaptiv_improves_with_d_true(self):
        t = [r.tpr_T for r in mz.sweep(mz.Params(), "adaptiv", "d_true", [10.0, 30.0, 120.0])]
        self.assertTrue(t[0] < t[1] < t[2])


class FigureSmokeTests(unittest.TestCase):
    def test_export_writes_eight_files(self):
        from analysis.belastungsmodell_app import export_ziel3 as fz
        with tempfile.TemporaryDirectory() as tmp:
            written = fz.export_all(mz.Params(), Path(tmp))
            self.assertEqual(len(written), 8)
            self.assertTrue(all(p.exists() and p.stat().st_size > 0 for p in written))
            self.assertEqual(sorted(p.stem for p in written),
                             sorted(list(fz.FILE_NAMES.values()) * 2))

    def test_axis_variants_build(self):
        import matplotlib.pyplot as plt
        from analysis.belastungsmodell_app import export_ziel3 as fz
        p = mz.Params(P=(3, 3, 2, 1))
        for x in mz.K1_AXES:
            plt.close(fz.fig_K1(p, x, "je Bett und Stunde", n_points=8))
        for y in mz.K2_Y:
            plt.close(fz.fig_K2(p, "M", y, n_points=8))
        for x in mz.K3_AXES:
            for y in mz.K3_Y:
                plt.close(fz.fig_K3(p, x, y, n_points=8))
        plt.close(fz.fig_K4(mz.Params(P=(3, 1)), "je Station (10 Betten) und Tag"))


if __name__ == "__main__":
    unittest.main()
