"""Kontrollzahlen des analytischen Belastungsmodells (analysis/belastungsmodell_app/model.py).

Die Sollwerte stammen aus Kapitel 5 (5.3) und der Aufgabenstellung; Toleranz
±0,01 Prozentpunkte (Kurve D: ±0,1 s, Stückzahlen je Tag: ±0,05).
"""

import math
import unittest

from analysis.belastungsmodell_app import model as bm


def pct(x):
    return 100.0 * x


class CalibrationTests(unittest.TestCase):
    def test_median_calibration(self):
        c = bm.calibrate(bm.Params())
        self.assertAlmostEqual(c.N_d, 150.0)
        self.assertAlmostEqual(c.PC, 0.00145, places=5)
        self.assertAlmostEqual(c.FPR, 0.00313, places=5)
        self.assertAlmostEqual(c.MFPR, 0.0125, places=4)
        self.assertAlmostEqual(c.w, math.log(0.9 / c.FPR))
        self.assertAlmostEqual(c.ceiling, 0.875)
        self.assertTrue(c.valid)

    def test_noise_direct_keeps_prevalence(self):
        c = bm.calibrate(bm.Params(noise_direct=0.8))
        self.assertAlmostEqual(c.FPR, 0.2)
        self.assertAlmostEqual(c.PC, bm.calibrate(bm.Params()).PC)

    def test_scenarios_match_table_5_7(self):
        for name, nd in (("geringe Last", 100), ("hohe Last", 400), ("sehr hohe Last", 1092),
                         ("stark instrumentiert", 400)):
            p = bm.Params(**bm.SCENARIOS[name])
            self.assertAlmostEqual(bm.calibrate(p).N_d, nd, delta=0.5, msg=name)

    def test_priorities(self):
        self.assertEqual(bm.Params(M=4, n_high=2).priorities, (3, 3, 1, 1))
        self.assertEqual(bm.Params(M=3, n_high=0).priorities, (1, 1, 1))


class MedianScenarioTests(unittest.TestCase):
    def setUp(self):
        self.p = bm.Params()

    def test_adaptiv(self):
        r = bm.evaluate(self.p, bm.ADAPTIV)
        self.assertAlmostEqual(pct(r.ARR), 87.48, delta=0.01)
        self.assertAlmostEqual(pct(r.TPR_sys), 99.63, delta=0.01)
        self.assertAlmostEqual(r.false_per_day, 16.9, delta=0.05)
        self.assertAlmostEqual(pct(r.PPV_cnt), 19.7, delta=0.05)
        self.assertAlmostEqual(pct(r.PPV_zeit), 47.9, delta=0.05)
        self.assertAlmostEqual(r.reports_per_day, 4.15, delta=0.005)

    def test_basis(self):
        r = bm.evaluate(self.p, bm.BASIS)
        self.assertAlmostEqual(pct(r.ARR), 85.20, delta=0.01)
        self.assertAlmostEqual(pct(r.TPR_sys), 99.99, delta=0.01)
        self.assertAlmostEqual(r.false_per_day, 20.0, delta=0.05)

    def test_rein_k_aus_4(self):
        expected = {1: (37.62, 99.99), 2: (87.38, 99.63), 3: (87.50, 94.77), 4: (87.50, 65.61)}
        for k, (arr, tpr) in expected.items():
            r = bm.evaluate(self.p, bm.rein(k))
            self.assertAlmostEqual(pct(r.ARR), arr, delta=0.01, msg=f"k={k}")
            self.assertAlmostEqual(pct(r.TPR_sys), tpr, delta=0.01, msg=f"k={k}")

    def test_adaptiv_delta_min_1(self):
        r = bm.evaluate(bm.Params(delta_min=1.0), bm.ADAPTIV)
        self.assertAlmostEqual(pct(r.ARR), 87.48, delta=0.01)
        self.assertAlmostEqual(pct(r.TPR_sys), 95.58, delta=0.01)

    def test_ohne_filter(self):
        r = bm.evaluate(self.p, bm.OHNE)
        self.assertAlmostEqual(r.false_per_day, 135.0, delta=0.05)
        self.assertAlmostEqual(pct(r.PPV_cnt), 10.0, delta=0.05)
        self.assertAlmostEqual(pct(r.PPV_zeit), 29.4, delta=0.05)
        self.assertAlmostEqual(r.ARR, 0.0)
        self.assertAlmostEqual(r.reports_per_day, 15.0, delta=0.005)

    def test_table_order(self):
        labels = [r.rule.label for r in bm.evaluate_table(self.p)]
        self.assertEqual(labels, ["ohne Filter", "rein 2-aus-|M|", "rein 3-aus-|M|", "Basis", "adaptiv"])


class VariationTests(unittest.TestCase):
    def test_noise_0_8(self):
        p = bm.Params(noise_direct=0.8)
        for rule, arr in ((bm.ADAPTIV, 85.94), (bm.BASIS, 79.36), (bm.rein(2), 80.96), (bm.rein(3), 87.04)):
            self.assertAlmostEqual(pct(bm.evaluate(p, rule).ARR), arr, delta=0.01, msg=rule.label)

    def test_c_art_extremes(self):
        for c, arr_ad, arr_r2 in ((0.0, 99.91, 99.53), (1.0, 75.00, 75.00)):
            p = bm.Params(c_art=c)
            self.assertAlmostEqual(pct(bm.evaluate(p, bm.ADAPTIV).ARR), arr_ad, delta=0.01, msg=f"c={c}")
            self.assertAlmostEqual(pct(bm.evaluate(p, bm.rein(2)).ARR), arr_r2, delta=0.01, msg=f"c={c}")

    def test_odds_ratio(self):
        for OR, arr_basis in ((2.0, 84.58), (4.0, 83.84)):
            p = bm.Params(OR=OR)
            self.assertAlmostEqual(pct(bm.evaluate(p, bm.BASIS).ARR), arr_basis, delta=0.01, msg=f"OR={OR}")
            self.assertAlmostEqual(pct(bm.evaluate(p, bm.ADAPTIV).ARR), 87.48, delta=0.01, msg=f"OR={OR}")

    def test_arr_never_exceeds_ceiling(self):
        for c in (0.0, 0.25, 0.5, 0.75, 1.0):
            p = bm.Params(c_art=c)
            cal = bm.calibrate(p)
            for rule in (bm.ADAPTIV, bm.BASIS, bm.rein(2), bm.rein(3)):
                self.assertLessEqual(bm.evaluate(p, rule, cal).ARR, cal.ceiling + 1e-12)

    def test_bruteforce_equivalence(self):
        cases = (bm.Params(), bm.Params(OR=4.0, M=6, n_high=3, c_art=0.3),
                 bm.Params(delta_min=0.2, alpha=0.1, M=5, n_high=1), bm.Params(M=3, n_high=0, T=5.0))
        for p in cases:
            for rule in (bm.ADAPTIV, bm.BASIS, bm.rein(2), bm.rein(3)):
                a = bm.evaluate(p, rule)
                b = bm.evaluate_bruteforce(p, rule)
                for key in ("TPR_sys", "coinc", "delayed", "noise_f", "ARR"):
                    self.assertAlmostEqual(getattr(a, key), b[key], places=12, msg=f"{rule.label} {key}")

    def test_invalid_calibration_yields_nan(self):
        # |M|·FPR = 1,2 bei |M| = 2 ergibt FPR = 0,6 > TPR = 0,5: kein zulässiges Gewicht.
        r = bm.evaluate(bm.Params(M=2, n_high=1, tpr=0.5, noise_direct=1.2), bm.ADAPTIV)
        self.assertTrue(math.isnan(r.ARR))


class CurveCTests(unittest.TestCase):
    def test_pair_shares_at_mu_8(self):
        p = bm.Params()
        hh = bm.pair_class(p, bm.ADAPTIV, 2)
        hn = bm.pair_class(p, bm.ADAPTIV, 1)
        nn = bm.pair_class(p, bm.ADAPTIV, 0)
        self.assertAlmostEqual(pct(hh.share(8.0)), 100.0, delta=0.01)
        self.assertAlmostEqual(pct(hn.share(8.0)), 2.12, delta=0.01)
        self.assertAlmostEqual(hn.t_esc, 15.41, delta=0.01)
        self.assertAlmostEqual(pct(nn.share(8.0)), 0.25, delta=0.01)
        self.assertAlmostEqual(nn.t_esc, 23.90, delta=0.01)
        self.assertAlmostEqual(bm.pair_share(p, bm.rein(2), 0, 8.0), 1.0)

    def test_missing_pairing(self):
        self.assertIsNone(bm.pair_class(bm.Params(M=4, n_high=0), bm.ADAPTIV, 2))
        self.assertTrue(math.isnan(bm.pair_share(bm.Params(M=4, n_high=0), bm.ADAPTIV, 2, 8.0)))


class CurveDTests(unittest.TestCase):
    def test_niedrig_niedrig(self):
        p = bm.Params()
        for t0, lat in ((0, 23.9), (10, 13.9), (20, 7.8), (30, 5.0)):
            self.assertAlmostEqual(bm.escalation_latency(p, "niedrig", "niedrig", t0), lat, delta=0.1, msg=f"t0={t0}")

    def test_hoch_hoch(self):
        p = bm.Params()
        for t0, lat in ((0, 10.0), (10, 4.0)):
            self.assertAlmostEqual(bm.escalation_latency(p, "hoch", "hoch", t0), lat, delta=0.1, msg=f"t0={t0}")

    def test_rein_2_is_immediate(self):
        self.assertEqual(bm.escalation_latency(bm.Params(), "hoch", "niedrig", 30.0, bm.rein(2)), 0.0)
        self.assertTrue(math.isinf(bm.escalation_latency(bm.Params(), "hoch", "niedrig", 30.0, bm.rein(3))))

    def test_trace_consistency(self):
        tr = bm.simulate_pair(bm.Params(), "hoch", "niedrig", 30.0)
        self.assertIsNotNone(tr)
        self.assertTrue(math.isfinite(tr.latency))
        self.assertAlmostEqual(tr.t_escalation, tr.t0 + tr.latency)
        i = int(round(tr.t_escalation / 0.01))
        self.assertGreaterEqual(tr.E[i], tr.theta_current[i] - 1e-9)
        self.assertTrue((tr.theta_current >= tr.theta_target - 1e-12).all())

    def test_never_escalates(self):
        # δ_min = 1 und |M| = 4: zwei niedrige Kanäle (k = 3) erreichen 3·w nie.
        self.assertTrue(math.isinf(bm.escalation_latency(bm.Params(delta_min=1.0), "niedrig", "niedrig", 0.0)))

    def test_hysteresis_slow_release(self):
        import numpy as np
        target = np.array([3.0, 1.0, 3.0, 3.0])
        rho = np.full(4, 0.5)
        cur = bm._hysteresis(target, rho, 1.0)
        self.assertAlmostEqual(cur[1], 1.0)                                   # Fast Attack
        self.assertAlmostEqual(cur[2], 3.0 + (1.0 - 3.0) * math.exp(-0.5))     # Slow Release
        self.assertLess(cur[3], 3.0)


class SweepTests(unittest.TestCase):
    def test_set_param_M_updates_n_high(self):
        p = bm.set_param(bm.Params(), "M", 7)
        self.assertEqual((p.M, p.n_high), (7, 3))

    def test_tornado_rows(self):
        base, rows = bm.tornado(bm.Params())
        self.assertEqual(len(rows), 10)
        self.assertAlmostEqual(pct(base.ARR), 87.48, delta=0.01)
        n_row = rows[0]
        self.assertLess(n_row.low.false_per_day, n_row.high.false_per_day)

    def test_sweep_shape(self):
        y = bm.sweep(bm.Params(), "c_art", [0.0, 0.5, 1.0], bm.ADAPTIV, "ARR")
        self.assertEqual(y.shape, (3,))
        self.assertAlmostEqual(pct(y[2]), 75.0, delta=0.01)


class WindowEffectTests(unittest.TestCase):
    """Kurve C2 (Fenstereffekt) und Prioritätsabbildung bei den Standardparametern."""

    def setUp(self):
        self.p = bm.Params()
        self.classes = bm.subset_classes(self.p, bm.ADAPTIV)

    def test_stoss_adaptiv(self):
        c = bm.find_class(self.classes, 4, 2)
        self.assertAlmostEqual(bm.t_need(c, self.p, bm.ADAPTIV), 5.0, delta=0.01)
        self.assertAlmostEqual(pct(bm.stoss_share(self.p, bm.ADAPTIV, 8.0)), 53.53, delta=0.01)

    def test_pair_hoch_hoch_adaptiv(self):
        c = bm.find_class(self.classes, 2, 2)
        self.assertAlmostEqual(bm.t_need(c, self.p, bm.ADAPTIV), 10.0, delta=0.01)
        self.assertAlmostEqual(pct(bm.pair_window_share(self.p, bm.ADAPTIV, 2, 8.0)), 8.21, delta=0.01)

    def test_pair_hoch_niedrig_matches_curve_c(self):
        c = bm.find_class(self.classes, 2, 1)
        self.assertAlmostEqual(bm.t_need(c, self.p, bm.ADAPTIV), 15.41, delta=0.01)
        self.assertAlmostEqual(pct(bm.pair_window_share(self.p, bm.ADAPTIV, 1, 8.0)), 2.12, delta=0.01)
        self.assertAlmostEqual(bm.pair_window_share(self.p, bm.ADAPTIV, 1, 8.0),
                               bm.pair_share(self.p, bm.ADAPTIV, 1, 8.0), places=12)

    def test_knn_with_delay(self):
        self.assertAlmostEqual(pct(bm.stoss_share(self.p, bm.rein(2), 8.0, tau=5.0)), 53.53, delta=0.01)
        self.assertAlmostEqual(pct(bm.pair_window_share(self.p, bm.rein(2), 2, 8.0, tau=5.0)), 28.65, delta=0.01)
        self.assertAlmostEqual(pct(bm.knn_share(5.0, 8.0)), 53.53, delta=0.01)
        self.assertAlmostEqual(bm.stoss_share(self.p, bm.rein(2), 8.0), 1.0)

    def test_crisis_price(self):
        c = bm.find_class(self.classes, 3, 2)
        self.assertAlmostEqual(bm.t_need(c, self.p, bm.ADAPTIV), 6.67, delta=0.01)
        self.assertAlmostEqual(pct(bm.crisis_share(self.p, bm.ADAPTIV, 3, 2)), 80.07, delta=0.01)
        self.assertAlmostEqual(bm.crisis_share(self.p, bm.rein(2), 3, 2), 1.0)

    def test_priority_bars(self):
        rows = {r["label"]: r for r in bm.priority_bars(self.p, 8.0)}
        self.assertEqual(list(rows), ["1 × niedrig", "1 × hoch", "niedrig + niedrig", "hoch + niedrig",
                                      "hoch + hoch", "hoch + niedrig + niedrig", "hoch + hoch + niedrig", "alle 4"])
        self.assertEqual((rows["1 × niedrig"]["k"], rows["1 × niedrig"]["branch"]), (4, "nie"))
        self.assertEqual((rows["1 × hoch"]["k"], rows["1 × hoch"]["branch"]), (3, "nie"))
        self.assertTrue(math.isinf(rows["1 × hoch"]["t_adaptiv"]))
        self.assertAlmostEqual(rows["niedrig + niedrig"]["t_adaptiv"], 23.90, delta=0.01)
        self.assertAlmostEqual(rows["hoch + niedrig"]["t_adaptiv"], 15.41, delta=0.01)
        self.assertAlmostEqual(rows["hoch + hoch"]["t_adaptiv"], 10.0, delta=0.01)
        self.assertAlmostEqual(rows["hoch + hoch + niedrig"]["t_adaptiv"], 6.67, delta=0.01)
        self.assertAlmostEqual(rows["alle 4"]["t_adaptiv"], 5.0, delta=0.01)
        self.assertEqual(rows["alle 4"]["k"], 2)
        self.assertAlmostEqual(pct(rows["niedrig + niedrig"]["share_adaptiv"]), 0.25, delta=0.01)
        self.assertAlmostEqual(pct(rows["hoch + niedrig"]["share_adaptiv"]), 2.12, delta=0.01)
        self.assertAlmostEqual(pct(rows["hoch + hoch"]["share_adaptiv"]), 8.21, delta=0.01)
        self.assertAlmostEqual(pct(rows["hoch + hoch + niedrig"]["share_adaptiv"]), 8.21, delta=0.01)
        self.assertAlmostEqual(pct(rows["alle 4"]["share_adaptiv"]), 53.53, delta=0.01)
        self.assertEqual(rows["1 × hoch"]["share_knn2"], 0.0)
        self.assertEqual(rows["hoch + hoch"]["share_knn2"], 1.0)
        self.assertEqual(rows["hoch + hoch"]["t_knn2"], 0.0)


class ComparisonTests(unittest.TestCase):
    def test_missed_per_1000(self):
        p = bm.Params()
        rows = {r["rule"]: r for r in bm.comparison_rows(p, tau=5.0)}
        self.assertEqual(list(rows), ["adaptiv", "rein 2-aus-N", "rein 3-aus-N", "Basis"])
        self.assertAlmostEqual(rows["adaptiv"]["missed"], 3.7, delta=0.05)
        self.assertAlmostEqual(rows["rein 3-aus-N"]["missed"], 52.3, delta=0.05)
        self.assertAlmostEqual(rows["rein 2-aus-N"]["missed"], 3.7, delta=0.05)
        self.assertAlmostEqual(rows["adaptiv"]["delta"], 0.0)
        self.assertAlmostEqual(rows["rein 2-aus-N"]["latency"], 5.0)
        self.assertTrue(0.0 < rows["adaptiv"]["latency"] < 10.0)

    def test_station_scaling(self):
        r = bm.evaluate(bm.Params(), bm.ADAPTIV)
        c = bm.scaled_counts(r, 10)
        self.assertAlmostEqual(c.false_per_day, 169.0, delta=0.5)
        self.assertAlmostEqual(c.false_per_day, 10 * r.false_per_day, places=9)

    def test_display_names(self):
        self.assertEqual(bm.rein(2).display, "rein 2-aus-N")
        self.assertEqual(bm.rein(3).display, "rein 3-aus-N")
        self.assertEqual(bm.ADAPTIV.display, "adaptiv")

    def test_geraetezahl_control_numbers(self):
        p = bm.Params()
        g = bm.geraetezahl(p, Ms=(2, 3, 12))
        expected = {
            "adaptiv":      ((18.9, 99.00), (18.6, 98.10), (16.9, 100.0)),
            "rein 2-aus-N": ((16.9, 81.00), (17.0, 97.20), (18.6, 100.0)),
            "rein 3-aus-N": ((16.9, 81.00), (16.9, 72.90), (16.9, 100.0)),
        }
        for name, cases in expected.items():
            false, missed = g[name]
            for i, (fa, tpr) in enumerate(cases):
                self.assertAlmostEqual(false[i], fa, delta=0.05, msg=f"{name} M-index {i}")
                self.assertAlmostEqual(100 - missed[i] / 10, tpr, delta=0.01, msg=f"{name} M-index {i}")

    def test_k_clamped_to_M(self):
        # rein 3-aus-N bei |M| = 2 verhält sich wie 2-aus-2
        p = bm.Params(M=2, n_high=1)
        a, b = bm.evaluate(p, bm.rein(3)), bm.evaluate(p, bm.rein(2))
        self.assertAlmostEqual(a.TPR_sys, b.TPR_sys, places=12)
        self.assertAlmostEqual(a.false_per_day, b.false_per_day, places=9)
        self.assertEqual(bm.escalation_latency(p, "hoch", "niedrig", 10.0, bm.rein(3)), 0.0)

    def test_combination_rows_knn_depends_on_count_only(self):
        rows = bm.combination_rows(bm.Params())
        for row in rows:
            self.assertEqual(row["rein 2-aus-N"], "sofort" if row["r"] >= 2 else "nie")
            self.assertEqual(row["rein 3-aus-N"], "sofort" if row["r"] >= 3 else "nie")

    def test_kmin_over_alpha(self):
        curves = bm.kmin_over_alpha(bm.Params(), [0.0, 0.5, 1.0])
        self.assertEqual(list(curves["hoch + niedrig"]), [3.0, 3.0, 2.0])   # SDC 0,5 / 0,75 / 1
        self.assertEqual(list(curves["1 × hoch"]), [3.0, 3.0, 2.0])         # SDC 0,375 / 0,6875 / 1
        self.assertEqual(list(curves["1 × niedrig"]), [4.0, 4.0, 3.0])      # SDC 0,125 / 0,229 / 0,333


class LatencySymmetryTests(unittest.TestCase):
    """Kurve D: hoch→niedrig und niedrig→hoch haben dieselbe Latenz (in der Abbildung als 'gemischt' zusammengefasst)."""

    def test_mixed_sequences_coincide(self):
        for p in (bm.Params(), bm.Params(delta_min=0.3), bm.Params(alpha=0.25, T=5.0)):
            cal = bm.calibrate(p)
            for t0 in (0.0, 5.0, 10.0, 20.0, 30.0, 45.0, 60.0):
                a = bm.escalation_latency(p, "hoch", "niedrig", t0, cal=cal)
                b = bm.escalation_latency(p, "niedrig", "hoch", t0, cal=cal)
                self.assertEqual(a, b, msg=f"{p} t0={t0}")


if __name__ == "__main__":
    unittest.main()
