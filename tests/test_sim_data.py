"""Kontrollzahlen der Vorlage „Experimente (Simulation)“ (analysis/belastungsmodell_app/sim_data.py).

Sollwerte aus der Zusammenfassung von PC 2 (Aufgabenstellung) und aus docs/reports/*.json;
Fehleskalationen ±0,01 je Bett und Stunde, Anteile als Zählungen, Modell ±0,01.
"""

import tempfile
import unittest
from pathlib import Path

from analysis.belastungsmodell_app import model_ziel3 as mz
from analysis.belastungsmodell_app import sim_data as sd

RUNS = sd.load_runs()


def r2(x):
    return round(x, 2)


class LoadTests(unittest.TestCase):
    def test_all_three_runs(self):
        avail = sd.available()
        self.assertTrue(set(["1h", "6h", "12h"]).issubset(avail))
        self.assertEqual(avail, [k for k in sd.RUN_ORDER if k in avail])          # Reihenfolge wie RUN_ORDER
        self.assertEqual(list(RUNS), avail)
        for run in RUNS.values():
            self.assertEqual(set(run.rules), {"adaptiv", "r2", "r3", "r1"})
            self.assertEqual(run.m.patients, 20)
        self.assertEqual(RUNS["1h"].notes, ("device without acceptance on PC 1: SIM-P01/Sim ABP",))
        self.assertEqual(RUNS["12h"].notes, ())

    def test_messwerte_12h(self):
        m = RUNS["12h"].m
        self.assertAlmostEqual(m.FPR["ECG"], 0.0237, delta=0.0001)
        self.assertAlmostEqual(m.FPR["Vent"], 0.0249, delta=0.0001)
        self.assertAlmostEqual(m.FPR["SpO2"], 0.0298, delta=0.0001)
        self.assertAlmostEqual(m.FPR["ABP"], 0.0261, delta=0.0001)
        self.assertAlmostEqual(m.P_C, 0.0109, delta=0.0001)
        self.assertAlmostEqual(m.d_false, 8.3, delta=0.05)
        self.assertAlmostEqual(m.d_true, 30.2, delta=0.05)
        self.assertAlmostEqual(m.c_art_a, 0.058, delta=0.001)
        self.assertAlmostEqual(m.c_art_b, 0.482, delta=0.001)
        self.assertEqual(m.n_crises, 311)
        self.assertAlmostEqual(m.hours, 12.0, delta=0.01)
        self.assertAlmostEqual(m.crises_per_bed_day, 31.1, delta=0.05)
        self.assertAlmostEqual(m.mfpr, 0.1044, delta=0.0001)

    def test_messwerte_6h(self):
        m = RUNS["6h"].m
        self.assertAlmostEqual(m.fpr_mean, 0.027, delta=0.0005)
        self.assertAlmostEqual(m.d_false, 8.4, delta=0.05)
        self.assertAlmostEqual(m.d_true, 32.1, delta=0.05)
        self.assertAlmostEqual(m.c_art_a, 0.072, delta=0.001)
        self.assertAlmostEqual(m.c_art_b, 0.493, delta=0.001)
        self.assertEqual(m.n_crises, 146)
        self.assertEqual(RUNS["6h"].spec.tpr, 0.6)

    def test_missing_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(sd.available(Path(tmp)), [])
            self.assertEqual(sd.load_runs(Path(tmp)), {})


class ControlValueTests(unittest.TestCase):
    """Tabelle der Aufgabenstellung (Zusammenfassung PC 2)."""

    def test_12h_false_escalations(self):
        expected = {"adaptiv": (3.14, 2.92, 3.37), "r2": (5.87, 5.57, 6.18), "r3": (2.02, 1.85, 2.21)}
        for rule, (v, lo, hi) in expected.items():
            r = RUNS["12h"].rules[rule]
            self.assertAlmostEqual(r.false_per_bed_h, v, delta=0.01, msg=rule)
            self.assertEqual((r2(r.false_ci[0]), r2(r.false_ci[1])), (lo, hi), msg=rule)
        self.assertAlmostEqual(RUNS["12h"].rules["r1"].false_per_bed_h, 36.53, delta=0.01)

    def test_12h_caught(self):
        expected = {"adaptiv": (283, 304), "r2": (307, 309), "r3": (292, 294)}
        for rule, (strict, mild) in expected.items():
            r = RUNS["12h"].rules[rule]
            self.assertEqual((r.caught("streng"), r.caught("mild"), r.n_crises), (strict, mild, 311), msg=rule)

    def test_1h(self):
        expected = {"adaptiv": (3.30, 2.56, 4.20, 20), "r2": (5.66, 4.66, 6.80, 21), "r3": (2.05, 1.47, 2.78, 20)}
        for rule, (v, lo, hi, mild) in expected.items():
            r = RUNS["1h"].rules[rule]
            self.assertAlmostEqual(r.false_per_bed_h, v, delta=0.01, msg=rule)
            self.assertEqual((r2(r.false_ci[0]), r2(r.false_ci[1])), (lo, hi), msg=rule)
            self.assertEqual((r.caught("mild"), r.n_crises), (mild, 21), msg=rule)
            self.assertEqual(r.caught("streng"), mild, msg=rule)      # 1 h: streng vorhanden und gleich mild

    def test_6h(self):
        expected = {"adaptiv": (3.35, 3.03, 3.69, 25.3), "r2": (6.10, 5.67, 6.56, 13.0), "r3": (2.11, 1.86, 2.38, 41.1)}
        for rule, (v, lo, hi, missed_pct) in expected.items():
            r = RUNS["6h"].rules[rule]
            self.assertAlmostEqual(r.false_per_bed_h, v, delta=0.01, msg=rule)
            self.assertEqual((r2(r.false_ci[0]), r2(r.false_ci[1])), (lo, hi), msg=rule)
            self.assertAlmostEqual(100 * r.missed_frac("streng"), missed_pct, delta=0.05, msg=rule)

    def test_missed_ci_is_reversed_caught_ci(self):
        r = RUNS["12h"].rules["adaptiv"]
        lo, hi = r.caught_ci("streng")
        self.assertEqual(r.missed_ci("streng"), (1 - hi, 1 - lo))
        self.assertAlmostEqual(100 * r.caught_frac("streng"), 91.0, delta=0.05)

    def test_ohne_filter_points(self):
        m = RUNS["12h"].m
        self.assertAlmostEqual(m.false_alarms_per_bed_h, 45.02, delta=0.01)
        lo, hi = m.false_alarms_ci
        self.assertTrue(lo < m.false_alarms_per_bed_h < hi)
        self.assertAlmostEqual(hi - lo, 1.70, delta=0.02)


class PoissonTests(unittest.TestCase):
    def test_reproduces_json_intervals(self):
        for key, rule in (("12h", "r2"), ("1h", "r3"), ("6h", "adaptiv"), ("12h", "r1")):
            run = RUNS[key]
            r = run.rules[rule]
            lo, hi = sd.poisson_ci(r.n_false, run.m.exposure_bed_h)
            self.assertAlmostEqual(lo, r.false_ci[0], delta=0.001, msg=f"{key} {rule}")
            self.assertAlmostEqual(hi, r.false_ci[1], delta=0.001, msg=f"{key} {rule}")

    def test_chi2_quantile(self):
        self.assertAlmostEqual(sd.chi2_ppf(0.975, 100), 129.56, delta=0.05)
        self.assertAlmostEqual(sd.chi2_ppf(0.025, 100), 74.22, delta=0.05)
        self.assertEqual(sd.poisson_ci(0, 10.0)[0], 0.0)


class ModelWithMesswerteTests(unittest.TestCase):
    """Modell mit den Messwerten des 12-h-Laufs (|M|·FPR, P(C), d_false, d_true direkt), TPR_j = 0,9."""

    def test_params(self):
        p = sd.model_params(RUNS["12h"], 0.06)
        self.assertEqual(p.P, (3, 3, 2, 1))
        self.assertEqual((p.T, p.delta_min, p.alpha, p.d0, p.tpr), (10.0, 0.5, 0.5, 15.0, 0.9))
        self.assertAlmostEqual(p.mfpr_direct, 0.1044, delta=0.0001)
        self.assertAlmostEqual(p.pc_direct, 0.0109, delta=0.0001)
        cal = mz.calibrate(p)
        self.assertAlmostEqual(cal.PC, p.pc_direct)
        self.assertAlmostEqual(cal.FPR, p.mfpr_direct / 4)

    def test_false_escalations_c_art_006(self):
        p = sd.model_params(RUNS["12h"], 0.06)
        # Episoden mit gemessenem d_false = 8,26 s (Aufgabenstellung mit 8,3 s: 3,62 / 2,21 / 0,75)
        for rule, per_hour in (("r2", 3.64), ("adaptiv", 2.22), ("r3", 0.75)):
            self.assertAlmostEqual(mz.evaluate(p, rule).false_day / 24, per_hour, delta=0.01, msg=rule)
        # alte Umrechnung (Zeitanteil): 2,18 / 1,46 / 0,70
        for rule, per_hour in (("r2", 2.18), ("adaptiv", 1.46), ("r3", 0.70)):
            self.assertAlmostEqual(mz.evaluate(p, rule).false_day_zeitanteil / 24, per_hour, delta=0.01, msg=rule)

    def test_tpr_T_adaptiv(self):
        p = sd.model_params(RUNS["12h"])
        self.assertAlmostEqual(100 * mz.evaluate(p, "adaptiv").tpr_T, 99.42, delta=0.01)
        self.assertAlmostEqual(100 * mz.evaluate(p, "r3").tpr_T, 94.77, delta=0.01)

    def test_pc_direct(self):
        self.assertAlmostEqual(mz.calibrate(mz.Params()).PC, 0.00145, places=5)     # Standard unverändert
        self.assertAlmostEqual(mz.calibrate(mz.Params(pc_direct=0.0109)).PC, 0.0109)
        via_kw = mz.evaluate(mz.Params(), "adaptiv", pc_direct=0.0109)
        via_field = mz.evaluate(mz.Params(pc_direct=0.0109), "adaptiv")
        self.assertEqual(via_kw, via_field)
        self.assertNotEqual(via_kw.PC, mz.evaluate(mz.Params(), "adaptiv").PC)
        with self.assertRaises(ValueError):
            mz.Params(pc_direct=1.5)

    def test_ohne_filter_matches_measurement(self):
        for run in RUNS.values():
            self.assertAlmostEqual(sd.model_result(run, "ohne").raw_day / 24, run.m.false_alarms_per_bed_h, places=6)

    def test_band_and_curve(self):
        run = RUNS["12h"]
        lo, hi = sd.band(run, "adaptiv", "MFPR", [0.05, 0.1, 0.2])
        self.assertTrue(all(lo < hi))
        self.assertTrue(all(lo[i] < lo[i + 1] for i in range(2)))
        a, b = sd.model_false_range(run, "adaptiv")
        self.assertAlmostEqual(a, 2.21, delta=0.01)
        self.assertAlmostEqual(b, 5.90, delta=0.01)
        ys = sd.curve(run, "adaptiv", "c_art", [0.0, 0.5, 1.0], "false_day")
        self.assertTrue(ys[0] < ys[1] < ys[2])


class CompareTests(unittest.TestCase):
    def test_rows_streng(self):
        rows = {(r.run, r.rule): r for r in sd.compare(list(RUNS.values()), "streng")}
        self.assertEqual(len(rows), 3 * len(RUNS))
        # Modellbereich der Fehleskalationen gilt für Standardszenarien; Lauf D (Idealfall, Alarmebene) liegt für adaptiv
        # und 3-aus-4 darunter (1,27 bzw. 1,13 je Bett·h gegen 2,70–4,23 bzw. 1,34–3,34) – Eigenschaft des Szenarios
        self.assertTrue(all(r.false_ok for r in rows.values() if RUNS[r.run].spec.kind == "standard"))
        if ("D", "adaptiv") in rows:
            self.assertFalse(rows[("D", "adaptiv")].false_ok)
        self.assertFalse(rows[("12h", "adaptiv")].caught_ok)       # 91,0 % [87,3; 93,9] gegen Modell 99,4 %
        self.assertTrue(rows[("12h", "r2")].caught_ok)
        self.assertTrue(rows[("12h", "r3")].caught_ok)
        self.assertFalse(rows[("6h", "r3")].caught_ok)             # 58,9 % gegen Modell 47,5 %
        self.assertAlmostEqual(100 * rows[("6h", "adaptiv")].model_caught, 80.72, delta=0.01)
        self.assertEqual(rows[("12h", "adaptiv")].sim_caught_n, 283)

    def test_rows_mild(self):
        rows = {(r.run, r.rule): r for r in sd.compare(list(RUNS.values()), "mild")}
        self.assertEqual(rows[("12h", "adaptiv")].sim_caught_n, 304)
        self.assertFalse(rows[("12h", "adaptiv")].caught_ok)       # 97,7 % [95,4; 99,1] gegen 99,4 %
        self.assertTrue(rows[("6h", "adaptiv")].caught_ok)

    def test_overlaps(self):
        self.assertTrue(sd.overlaps((1, 2), (2, 3)))
        self.assertFalse(sd.overlaps((1, 2), (2.1, 3)))


class E4Tests(unittest.TestCase):
    def test_12h_week(self):
        run = RUNS["12h"]
        self.assertAlmostEqual(sd.crises_per_station_week(run), 2177.4, delta=0.1)
        v = sd.e4_values(run, "r2", "streng")
        self.assertAlmostEqual(v.sim_false, 5.8678 * 1680, delta=0.5)
        self.assertAlmostEqual(v.model_false_a, 3.630 * 1680, delta=2)
        self.assertAlmostEqual(v.model_false_b, 6.349 * 1680, delta=2)
        self.assertAlmostEqual(v.sim_missed, (4 / 311) * 2177.4, delta=0.1)
        v = sd.e4_values(run, "adaptiv", "streng")
        self.assertAlmostEqual(v.model_missed, (1 - 0.99416) * 2177.4, delta=0.1)
        self.assertAlmostEqual(v.sim_missed, (28 / 311) * 2177.4, delta=0.1)
        self.assertTrue(v.sim_missed_ci[0] < v.sim_missed < v.sim_missed_ci[1])


class FigureSmokeTests(unittest.TestCase):
    def test_figures_and_export(self):
        import matplotlib.pyplot as plt
        from analysis.belastungsmodell_app import export_ziel3 as fz
        sel = list(RUNS.values())
        plt.close(fz.fig_V1(sel, n_points=8))
        plt.close(fz.fig_V2(sel[:1], n_points=8))
        plt.close(fz.fig_V3(sel, "mild", mit_parametern=True, n_points=8))
        plt.close(fz.fig_V4(RUNS["6h"]))
        with tempfile.TemporaryDirectory() as tmp:
            written = fz.export_vergleich(RUNS, Path(tmp), keys=("12h",))
            self.assertEqual(sorted(p.stem for p in written), sorted(list(fz.V_FILES.values()) * 2))
            self.assertTrue(all(p.stat().st_size > 0 for p in written))
            self.assertEqual(fz.export_vergleich({}, Path(tmp)), [])


if __name__ == "__main__":
    unittest.main()
