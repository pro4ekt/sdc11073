"""Zeit-ROC je Fenster T (analysis/roc_ziel3/zeit_roc.py): Zerlegung der Krisenzeit (i)–(iv) auf Spielzeugdaten und
gegen ``metrics.rate`` (TPR_sys), Farbskala der Fenster, pAUC, Abbildungen Z1–Z3 mit synthetischen Zeilen, Ansicht
„Zeit-ROC × Fenster T“ der ROC-Werkstatt; mit echten Daten: (iv) = TPR_sys und Summe = 100 % in jeder Zeile.
"""

import math
import tempfile
import unittest
from pathlib import Path

import matplotlib.pyplot as plt

from analysis.roc_ziel3 import figures_werkstatt as fw
from analysis.roc_ziel3 import metrics as mt
from analysis.roc_ziel3 import zeit_roc as zr

ROWS = zr.load()


def _truth(crises, open_crises=()):
    return {"P1": mt.PatientTruth(patient="P1", window=(0.0, 1000.0), crises=list(crises), open_crises=list(open_crises),
                                  stable=[(0.0, 90.0), (170.0, 490.0), (610.0, 1000.0)])}


def _row(eta, h, regel, T, tpr, fpr, erk=0.95, lat=8.0, falsch=3.0):
    d_iv = tpr
    d_ii = (1 - tpr) * 0.6
    d_iii = (1 - tpr) * 0.3
    return dict(eta=eta, h=h, regel=regel, T=T, tpr=tpr, fpr_ii=fpr, fpr=fpr * 1.1, erkannt=erk, erkannt_streng=erk - 0.05,
                n_krisen=300, n_falsch=700, falsch_je_bett_h=falsch, verz_kreuz_median_s=lat, verz_kreuz_p90_s=lat + 4,
                t_krise_s=9000.0, d_i=1 - d_iv - d_ii - d_iii, d_ii=d_ii, d_iii=d_iii, d_iv=d_iv, krise_mittel_s=30.0, n_rot=1000, bett_h=240.0)


def _rows():
    out = []
    for eta, off in (("0,9", 0.0), ("0,7", -0.1)):
        for i, h in enumerate((0.5, 1.0, 2.0)):
            f = 0.08 / (i + 1) ** 2
            out.append(_row(eta, h, "2-aus-4", None, 0.8 + off - 0.1 * i, f, 0.99, 3.0, 6.0 / (i + 1)))
            out.append(_row(eta, h, "3-aus-4", None, 0.7 + off - 0.1 * i, f / 3, 0.94, 6.0, 2.0 / (i + 1)))
            out.append(_row(eta, h, "1-aus-4", None, 0.9 + off - 0.1 * i, f * 5, 1.0, 0.0, 30.0))
            out.append(_row(eta, h, "4-aus-4", None, 0.3 + off - 0.1 * i, f / 10, 0.7, 9.0, 0.3))
            for T in zr.T_WERTE:
                out.append(_row(eta, h, "adaptiv", T, 0.75 + off - 0.1 * i - 0.004 * T, f / 2 * (10 / T) ** 0.5, 0.97, T + 0.5, 3.0 * 10 / T))
    return out


class DecompositionTests(unittest.TestCase):
    def test_toy(self):
        truth = _truth([(100.0, 160.0), (500.0, 600.0)])
        rot = {"P1": [(110.0, 120.0), (130.0, 170.0)]}                     # zweite Krise ohne Rot
        d = zr.decompose(rot, truth)
        self.assertAlmostEqual(d["t_krise_s"], 160.0)
        self.assertAlmostEqual(d["t_i_s"], 100.0)
        self.assertAlmostEqual(d["t_ii_s"], 10.0)
        self.assertAlmostEqual(d["t_iv_s"], 40.0)
        self.assertAlmostEqual(d["t_iii_s"], 10.0)
        self.assertAlmostEqual(sum(d[k] for k in zr.PARTS), 1.0)
        r = mt.rate(rot, truth, 1.0)
        self.assertAlmostEqual(d["d_iv"], r.tpr)
        self.assertAlmostEqual(zr.mean_crisis_s(truth), 80.0)

    def test_open_and_overlapping_crises(self):
        truth = _truth([(100.0, 160.0), (150.0, 200.0)], open_crises=[(950.0, 1000.0)])
        rot = {"P1": [(90.0, 105.0), (960.0, 1200.0)]}                     # Rot vor Krisenbeginn, Rot über das Fensterende
        d = zr.decompose(rot, truth)
        self.assertAlmostEqual(d["t_krise_s"], 150.0)                        # (100, 200) verschmolzen + (950, 1000)
        self.assertAlmostEqual(d["t_ii_s"], 0.0 + 10.0)                      # erste Krise sofort, offene nach 10 s
        self.assertAlmostEqual(d["t_iv_s"], 5.0 + 40.0)
        self.assertAlmostEqual(d["t_iii_s"], 95.0)
        self.assertAlmostEqual(d["d_iv"], mt.rate(rot, truth, 1.0).tpr)
        self.assertTrue(math.isnan(zr.decompose({}, {"P1": mt.PatientTruth("P1", (0, 10), [], [], [(0, 10)])})["d_iv"]))


class HelperTests(unittest.TestCase):
    def test_colors(self):
        self.assertEqual(zr.t_color(10.0), "#0F8A8A")
        lum = lambda c: sum(int(c.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))      # noqa: E731
        base = lum("#0F8A8A")
        self.assertGreater(lum(zr.t_color(5.0)), lum(zr.t_color(8.0)))
        self.assertGreater(lum(zr.t_color(8.0)), base)
        self.assertLess(lum(zr.t_color(15.0)), base)
        self.assertLess(lum(zr.t_color(30.0)), lum(zr.t_color(20.0)))
        self.assertEqual(zr.rule_label("adaptiv", 8.0), "adaptiv, T = 8 s")
        self.assertEqual(zr.rule_key("2-aus-4", None), "2-aus-4")

    def test_selection_pauc_tables(self):
        rows = _rows()
        sel = zr.curve(rows, "0,9", "adaptiv", 10.0)
        self.assertEqual([r["h"] for r in sel], [0.5, 1.0, 2.0])
        self.assertEqual(zr.at_h(rows, "0,7", "3-aus-4")["h"], 1.0)
        p = zr.pauc(sel)
        self.assertTrue(0 < p < 1)
        self.assertTrue(math.isnan(zr.pauc(zr.curve(rows, "0,9", "4-aus-4"))))   # reicht nicht bis 3 %
        tbl = zr.table_rows(rows, ["0,9", "0,7"])
        self.assertEqual(len(tbl), 2 * (2 + len(zr.T_WERTE)))
        self.assertTrue(all("%" in r["TPR_sys bei h = 1"] for r in tbl))
        z = zr.zerlegung_rows(rows, ["0,9"], zr.T_Z3)
        self.assertEqual(len(z), 4 + 3)
        self.assertTrue(all(r["Summe"] == "100,0 %" for r in z))
        self.assertEqual(zr.catch_up_T(rows, "0,9", "3-aus-4"), 10.0)       # 0,75 − 0,004·T ≥ 0,7 → T ≤ 12,5
        self.assertIsNone(zr.catch_up_T(rows, "0,9", "1-aus-4"))


class FigureTests(unittest.TestCase):
    def test_figures(self):
        rows = _rows()
        for zoom in (False, True):
            fig = zr.fig_z1(rows, zoom=zoom)
            self.assertEqual(len(fig.axes), 2)
            self.assertTrue(fig.legends)
            labels = [t.get_text() for t in fig.legends[0].get_texts()]
            self.assertEqual(sum(1 for lab in labels if lab.startswith("adaptiv, T")), len(zr.T_WERTE))
            if zoom:
                self.assertEqual(fig.axes[0].get_xlim(), (0.0, 3.0))
            plt.close(fig)
        fig = zr.fig_z2(rows)
        self.assertEqual(len(fig.axes), 4)
        self.assertEqual(len(fig.axes[0].get_lines()), 2 + 2 * len(zr.T_WERTE) + 4)   # zwei Linien + Punkte + 4 Waagrechte
        plt.close(fig)
        fig = zr.fig_z3(rows)
        self.assertEqual(len(fig.axes), 2)
        self.assertIn("mittlere Krise 30,0 s", fig.axes[0].get_title())
        plt.close(fig)
        fig = zr.fig_z1([], ["0,9"])
        self.assertTrue(any("keine Zeit-ROC-Daten" in t.get_text() for t in fig.axes[0].texts))
        plt.close(fig)
        with tempfile.TemporaryDirectory() as tmp:
            w = zr.export_all(rows, Path(tmp))
            self.assertEqual(sorted(p.name for p in w), sorted(f"{n}.{e}" for n in zr.FILES.values() for e in ("png", "svg")))
            self.assertEqual(zr.export_all(rows, Path(tmp)), [])                 # vorhandene Dateien bleiben
        with tempfile.TemporaryDirectory() as tmp:
            zr.write(rows, Path(tmp), meta=dict(fpr_variante="ii"))
            back = zr.load(Path(tmp))
            self.assertEqual(len(back), len(rows))
            self.assertAlmostEqual(back[5]["tpr"], rows[5]["tpr"], places=5)
            self.assertEqual(back[5]["regel"], rows[5]["regel"])

    def test_werkstatt_view(self):
        self.assertIn("zeit_T", fw.VIEWS)
        self.assertIn("nach oben, aber auch nach rechts", fw.HOWTO["zeit_T"])
        self.assertEqual(fw.zeit_etas(fw.Options(eta="beide")), ["0,9", "0,7"])
        self.assertEqual(fw.zeit_rules(fw.Options(rules=("2-aus-4",), zeit_Ts=(5.0,))), ((), ("2-aus-4",)))
        self.assertEqual(fw.zeit_rules(fw.Options(zeit_Ts=(5.0, 10.0)))[0], (5.0, 10.0))
        self.assertEqual(fw.export_name(fw.Options(view="zeit_T", eta="0,7", zeit_zoom=True, zeit_Ts=(5.0, 10.0))),
                         "Werkstatt_zeit_T_eta07_T5-10_h1_zoom")
        fig = fw.fig_werkstatt({}, fw.Options(view="zeit_T", eta="val 0,9"))
        self.assertEqual(len(fig.axes), 1)
        plt.close(fig)


@unittest.skipUnless(ROWS, "zeit_roc.csv fehlt")
class RealDataTests(unittest.TestCase):
    def test_parts_sum_and_tpr(self):
        for r in ROWS:
            self.assertAlmostEqual(r["d_iv"], r["tpr"], places=5, msg=(r["eta"], r["h"], r["regel"], r["T"]))
            self.assertAlmostEqual(sum(r[k] for k in zr.PARTS), 1.0, places=5)
            self.assertGreaterEqual(min(r[k] for k in zr.PARTS), -1e-6)
        self.assertEqual({r["eta"] for r in ROWS}, {"0,9", "0,7"})
        self.assertEqual(len({r["h"] for r in ROWS}), 15)
        self.assertEqual({r["T"] for r in ROWS if r["regel"] == "adaptiv"}, set(zr.T_WERTE))

    def test_t10_matches_roc_alle(self):
        from analysis.roc_ziel3 import figures_roc as fr
        from analysis.roc_ziel3 import run_roc as rr
        rows = rr.load_rows(rr.ROC_DIR / rr.CSV_OUT)
        ref = next(r for r in fr.curve(rows, "Regel", "adaptiv", 10.0) if abs(r["h"] - 1.0) < 1e-9)
        z = zr.at_h(ROWS, "0,9", "adaptiv", 10.0)
        self.assertAlmostEqual(z["tpr"], ref["tpr"], places=4)
        self.assertAlmostEqual(z["fpr_ii"], ref["fpr_ii"], places=5)


if __name__ == "__main__":
    unittest.main()
