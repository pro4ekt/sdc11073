"""ROC-Werkstatt (analysis/roc_ziel3/werkstatt.py, figures_werkstatt.py).

Prüft die Konfigurationen und ihre Namen, dass α und δ_min im unveränderten Dashboard-Kern wirken (α über die
Datenbank, δ_min über das Parameterobjekt EngineConfig), die Δ-Rechnung mit Bootstrap auf Spielzeugdaten, alle
Darstellungen der Werkstatt-Abbildung mit synthetischen Zeilen, den Export und – wenn die Werkstatt-Dateien vorliegen –
dass die Basis-Konfiguration dieselben Zahlen liefert wie ``roc_alle.csv`` / ``roc_tabellen.json`` (R1a, R2, R4b).
"""

import json
import math
import tempfile
import unittest
from pathlib import Path

import matplotlib.pyplot as plt

from analysis.roc_ziel3 import dashcore as dc
from analysis.roc_ziel3 import figures_roc as fr
from analysis.roc_ziel3 import figures_werkstatt as fw
from analysis.roc_ziel3 import journals as jn
from analysis.roc_ziel3 import metrics as mt
from analysis.roc_ziel3 import run_roc as rr
from analysis.roc_ziel3 import werkstatt as ws
from tests.test_roc_ziel3 import _synthetic_rows

HAVE_WS = {eta: ws.available(d) and (d / rr.CSV_OUT).exists() and (d / rr.TAB_JSON).exists()
           for eta, d in (("0,9", rr.ROC_DIR), ("0,7", rr.ROC_DIR_P07))}


# ─────────────────────────────────────────────────────────────────────────────
# Konfigurationen
# ─────────────────────────────────────────────────────────────────────────────

class ConfigTests(unittest.TestCase):
    def test_configs(self):
        cfgs = ws.configs()
        self.assertEqual(len(cfgs), 10)
        self.assertEqual(cfgs[0], ws.BASE)
        self.assertEqual(len(set(cfgs)), 10)
        for T, a, d in cfgs:
            self.assertLessEqual(sum(1 for v, b in zip((T, a, d), ws.BASE) if abs(v - b) > 1e-9), 1)
        self.assertEqual([c[0] for c in cfgs if c[1:] == (0.5, 0.5)], [10.0, 5.0, 20.0, 30.0])

    def test_key_label_varied(self):
        self.assertEqual(ws.key(10.0, 0.5, 0.5), "T10_a0.5_d0.5")
        self.assertEqual(ws.parse_key("T20_a0.3_d1"), (20.0, 0.3, 1.0))
        self.assertIsNone(ws.varied(*ws.BASE))
        self.assertEqual(ws.varied(10.0, 0.5, 1.0), "delta_min")
        self.assertEqual(ws.label(10.0, 0.5, 0.5), "adaptiv (T = 10 s, α = 0,5, δ_min = 0,5)")
        self.assertEqual(ws.label(10.0, 0.5, 1.0, short=True), "adaptiv, δ_min = 1,0")
        self.assertEqual(ws.label(5.0, 0.5, 0.5, short=True), "adaptiv, T = 5 s")
        self.assertEqual(ws.label(*ws.BASE, short=True), "adaptiv (Basis)")

    def test_cache_names(self):
        contract = dc.load_contract()
        raw10 = dc.build_raw_clinical_db(contract)
        self.assertEqual(ws.cache_path(Path("c"), "h_1.00", raw10, contract, 0.5).name, "h_1.00.json")
        raw20 = dc.build_raw_clinical_db(contract, horizon_T=20.0)
        self.assertEqual(ws.cache_path(Path("c"), "h_1.00", raw20, contract, 0.5).name, "h_1.00_T20.json")
        self.assertEqual(ws.cache_path(Path("c"), "h_1.00", raw10, contract, 1.0).name, "h_1.00_T10_a0.5_d1.json")
        raw_a = dc.build_raw_clinical_db(contract, alpha=0.9)
        self.assertEqual(ws.cache_path(Path("c"), "h_0.50", raw_a, contract, 0.5).name, "h_0.50_T10_a0.9_d0.5.json")


# ─────────────────────────────────────────────────────────────────────────────
# α und δ_min wirken im unveränderten Kern
# ─────────────────────────────────────────────────────────────────────────────

class CoreOverrideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = dc.load_contract()
        cls.chs = [dc.Channel("P1", s, f"urn:uuid:{s}", f"{s}_alert_cond_x", c["loinc"], c["priority"], c["model"], "SDC Sim")
                   for s, c in zip(jn.SOURCES, cls.contract["sources"])]

    def scen(self, events, alpha=0.5, delta_min=None, T=10.0, t_end=120.0):
        raw = dc.build_raw_clinical_db(self.contract, horizon_T=T, alpha=alpha)
        rep = dc.AdaptivReplay(self.chs, raw, delta_min=delta_min)
        r = rep.run([jn.AlarmEvent(t, "P1", s, f"urn:uuid:{s}", on) for t, s, on in events], 0.0, t_end)
        return r.rot.get("P1", []), r

    MELT = [(0.0, "spo2", True), (0.0, "abp", True), (100.0, "spo2", False), (100.0, "abp", False)]
    MIXED = [(0.0, "ecg", True), (0.0, "abp", True), (100.0, "ecg", False), (100.0, "abp", False)]

    def test_delta_min_one_disables_melting(self):
        rot, r = self.scen(self.MELT)                       # Standard: Schmelzen → Rot bei 18,5 s
        self.assertEqual(len(rot), 1)
        self.assertAlmostEqual(rot[0][0], 18.5, places=6)
        self.assertEqual(r.params["delta_min"], 0.5)
        rot1, r1 = self.scen(self.MELT, delta_min=1.0)      # δ_min = 1: Schwelle sinkt nicht → nie
        self.assertEqual(rot1, [])
        self.assertEqual(r1.params["delta_min"], 1.0)
        rot5, _ = self.scen(self.MELT, delta_min=0.5)       # ausdrücklich 0,5 = Standard
        self.assertEqual(rot5, rot)

    def test_alpha_changes_quorum(self):
        rot, _ = self.scen(self.MIXED)                      # α = 0,5: SDC 0,722 → k = 3 → Schmelzen (16,5 s)
        self.assertAlmostEqual(rot[0][0], 16.5, places=6)
        rot9, r9 = self.scen(self.MIXED, alpha=0.9)         # α = 0,9: SDC 0,944 → k = 2 → sofort nach T
        self.assertAlmostEqual(rot9[0][0], 10.5, places=6)
        self.assertEqual(r9.params["filter_params"]["alpha"], 0.9)
        rot3, _ = self.scen(self.MIXED, alpha=0.3)          # α = 0,3: SDC 0,611 → k = 3, längeres Schmelzen
        self.assertGreater(rot3[0][0], rot[0][0])

    def test_dashboard_core_untouched(self):
        core = dc.load_core()
        self.assertEqual(core.saa.SmartAlertAggregator.__init__.__defaults__, (None, True))
        self.assertEqual(core.mt.EngineConfig().delta_min, 0.5)
        self.assertTrue(all(str(p).startswith(str(dc.DASH_ALARMS)) for p in core.files))


# ─────────────────────────────────────────────────────────────────────────────
# Δ zur Sehne und Hülle (Spielzeugdaten)
# ─────────────────────────────────────────────────────────────────────────────

def _sums(t_crisis, t_in, t_stable, t_in_s, t_false, n_cr, n_det, n_strict, n_ev, n_false, hours=1.0):
    return [t_crisis, t_in, t_stable, t_in_s, t_false, n_cr, n_det, n_strict, n_ev, n_false, hours]


class DeltaTests(unittest.TestCase):
    def setUp(self):
        pts = ["A", "B", "C"]
        # je Regel und h gleiche Patienten; 2-aus-4 viele falsche, hohe Erkennung; 3-aus-4 wenige falsche, geringere
        k = {"1-aus-4": {}, "2-aus-4": {}, "3-aus-4": {}, "4-aus-4": {}}
        for hk, f in (("0.5", 1.5), ("1", 1.0), ("2", 0.5)):
            k["1-aus-4"][hk] = {p: _sums(100, 90, 900, 90, 80, 10, 10, 10, 60, 50 * f) for p in pts}
            k["2-aus-4"][hk] = {p: _sums(100, 70, 900, 20, 18, 10, 9, 9, 20, 10 * f) for p in pts}
            k["3-aus-4"][hk] = {p: _sums(100, 50, 900, 5, 4, 10, 7, 6, 8, 3 * f) for p in pts}
            k["4-aus-4"][hk] = {p: _sums(100, 20, 900, 1, 1, 10, 4, 3, 3, 1 * f) for p in pts}
        self.k = k
        self.cfg = {"T10_a0.5_d0.5": {"1": dict(k["2-aus-4"]["1"])},                         # = 2-aus-4
                    "T10_a0.5_d1": {"1": {p: _sums(100, 60, 900, 8, 7, 10, 9, 8, 12, 5) for p in pts}}}   # zwischen 2 und 3

    def test_point_on_rule_has_zero_chord_delta(self):
        d = ws.deltas_h1(self.cfg, self.k, n_boot=50, seed=1)
        base = d["T10_a0.5_d0.5"]["stueck"]
        self.assertAlmostEqual(base["delta_sehne_pp"], 0.0, places=9)
        self.assertLessEqual(base["delta_pp"], 1e-9)                   # auf oder unter der Hülle
        self.assertEqual(base["n_boot"], 50)
        self.assertEqual(len(base["delta_ci"]), 2)
        self.assertLessEqual(base["delta_ci"][0], base["delta_ci"][1])
        self.assertTrue(base["sehne_innerhalb"])
        self.assertIn("zeit", d["T10_a0.5_d0.5"])

    def test_between_rules_is_above_chord(self):
        d = ws.deltas_h1(self.cfg, self.k, n_boot=50, seed=1)
        mid = d["T10_a0.5_d1"]["stueck"]
        # x = 5, y = 0,9; Sehne zwischen (3; 0,7) und (10; 0,9): 0,7571 → Δ_Sehne = +14,3 pp
        self.assertAlmostEqual(mid["x"], 5.0)
        self.assertAlmostEqual(mid["delta_sehne_pp"], 100 * (0.9 - (0.7 + 0.2 * 2 / 7)), places=6)
        self.assertTrue(mid["sehne_innerhalb"])
        self.assertTrue(math.isfinite(mid["delta_pp"]))

    def test_missing_h_gives_empty(self):
        self.assertEqual(ws.deltas_h1(self.cfg, {"2-aus-4": {"0.5": {}}}, n_boot=5), {})


# ─────────────────────────────────────────────────────────────────────────────
# Abbildung (synthetische Zeilen)
# ─────────────────────────────────────────────────────────────────────────────

def _synthetic_werk(eta="0,9"):
    rows = []
    hs = [0.5, 1.0, 2.0]
    for i, h in enumerate(hs):
        for (T, a, d) in ws.configs():
            fac = 1.0 + 0.02 * (T - 10) + 0.3 * (a - 0.5) - 0.2 * (d - 0.5)
            rows.append(dict(eta=eta, h=h, konfig=ws.key(T, a, d), T=T, alpha=a, delta_min=d, tpr=0.5 * fac - 0.1 * i,
                             fpr=0.01 / (i + 1), fpr_ii=0.009 / (i + 1) * fac, hvl_anteil=0.03, erkannt=min(0.99, 0.9 * fac - 0.1 * i),
                             erkannt_streng=min(0.95, 0.85 * fac - 0.1 * i), verspaetet=0.05, n_krisen=300, n_erkannt=280,
                             n_erkannt_streng=270, n_ereignisse=1000, n_falsch=700, falsch_je_bett_h=3.0 / (i + 1) * fac,
                             d_false_s=7.0, verz_median_s=16.0, verz_p90_s=19.5, verz_kreuz_median_s=10.5, verz_kreuz_p90_s=14.0,
                             n_latenz=280, n_rot=1000, bett_h=240.0, wall_s=6.0))
    h1 = {ws.key(*c): {v: dict(h=1.0, x=3.0, y_adaptiv=0.9, y_huelle=0.91, delta_pp=-1.0, delta_ci=[-2.0, 0.5], x_2=5.0, y_2=0.95,
                                x_3=2.0, y_3=0.8, y_sehne=0.85, delta_sehne_pp=5.0, delta_sehne_ci=[3.0, 7.0], sehne_innerhalb=True,
                                n_boot=1000, boot_nan=0, huelle=[[0, 0], [2, 0.8], [5, 0.95]]) for v in ws.VIEWS}
          for c in ws.configs()}
    return dict(rows=rows, h1=h1, meta=dict(eta=eta, n_boot=1000, konfigurationen=[ws.key(*c) for c in ws.configs()]))


def _datasets():
    rows = _synthetic_rows()
    for r in rows:
        if r.get("art") == "Geraet":
            r.setdefault("eta_oder_erkannt", 0.95 - 0.2 * (r["h"] - 0.5))
            r.setdefault("falsch_je_bett_h", 40.0 * r["fpr"] / 0.05)
            r.setdefault("n_krisen", 300)
    tb = rr.tables(rows, None)
    return {"0,9": fw.Dataset("0,9", rows, tb, _synthetic_werk("0,9")), "0,7": fw.Dataset("0,7", rows, tb, _synthetic_werk("0,7"))}


class GeraetSystemTests(unittest.TestCase):
    def test_figure_three_panels_and_h_legend(self):
        ds = _datasets()
        for eta, rows in (("0,9", 1), ("beide", 2)):
            for log_x in (False, True):
                opt = fw.Options(view="geraet_system", eta=eta, h=0.5, log_x=log_x, x_unit="tag",
                                 rules=("adaptiv", "2-aus-4", "3-aus-4", "1-aus-4", "4-aus-4"))
                fig = fw.fig_werkstatt(ds, opt)
                self.assertEqual(len(fig.axes), 3 * rows)
                self.assertEqual(len(fig.legends), 2)
                labels = [t.get_text() for t in fig.legends[1].get_texts()]
                self.assertIn("h = 1", labels)
                self.assertIn("gewählt: h = 0,5", labels)
                self.assertEqual(fig.axes[1].get_xscale(), "log")
                self.assertEqual(fig.axes[1].get_yscale(), "log")
                self.assertIn("je Bett und Tag", fig.axes[1].get_ylabel())
                self.assertEqual(fig.axes[0].get_xscale(), "log")          # FPR_j über zwei Dekaden: immer logarithmisch
                self.assertEqual([ax.get_title() for ax in fig.axes[:3]], ["Gerät", "Fehlmeldungen", "Krisen"])
                plt.close(fig)
        self.assertEqual(fw.export_name(fw.Options(view="geraet_system", eta="0,7")), "Werkstatt_geraet_system_eta07_T10_a05_d05_h1")
        self.assertIn("geraet_system", fw.HOWTO)

    def test_points_and_binomial(self):
        ds = _datasets()["0,9"]
        pts = fw.device_points(ds, "ECG")
        self.assertEqual([p[0] for p in pts], [0.5, 1.0, 2.0])
        self.assertTrue(all(0 <= p[1] <= 1 and 0 <= p[2] <= 1 for p in pts))
        em = fw.eta_mean_by_h(ds)
        self.assertEqual(sorted(em), [0.5, 1.0, 2.0])
        bc2 = fw.binom_curve(ds, 2)
        bc3 = fw.binom_curve(ds, 3)
        self.assertEqual(len(bc2), 3)
        self.assertTrue(all(b2 >= b3 for (_, _, b2), (_, _, b3) in zip(bc2, bc3)))      # P(≥2) ≥ P(≥3)
        # gleiche η_j für alle Geräte → Binomialformel exakt
        etas = [0.95 - 0.2 * (0.5 - 0.5)] * 4
        self.assertAlmostEqual(fr.binom_participation(etas, 4), 0.95 ** 4)
        m, h, rows = fw.binom_deviation(ds, 2)
        self.assertTrue(math.isfinite(m) and h in (0.5, 1.0, 2.0) and len(rows) == 3)
        cols = fw.h_colors([0.5, 1.0, 2.0])
        self.assertEqual(len({c for c in cols.values()}), 3)

    def test_tables(self):
        ds = _datasets()
        dev = fw.device_table_rows(ds, fw.Options(view="geraet_system", eta="beide", h=1.0))
        self.assertEqual(len(dev), 8)
        self.assertEqual(dev[0]["Gerät"], "ECG")
        rules = fw.rule_table_rows(ds, fw.Options(view="geraet_system", h=2.0))
        self.assertEqual([r["Regel"] for r in rules][:2], ["2-aus-4", "3-aus-4"])
        self.assertTrue(rules[0]["erkannt mild"].endswith("%"))
        self.assertEqual(fw.rule_table_rows(ds, fw.Options(view="geraet_system", h=0.3)), [])


class FigureTests(unittest.TestCase):
    def test_all_views_and_options(self):
        ds = _datasets()
        all_rules = ("adaptiv", "2-aus-4", "3-aus-4", "1-aus-4", "4-aus-4")
        n = 0
        for view in fw.VIEWS:
            for eta in ("0,9", "beide"):
                for schar in (None, "T", "alpha", "delta_min"):
                    for log_x in (False, True):
                        opt = fw.Options(view=view, eta=eta, schar=schar, rules=all_rules, devices=True, hull=True, chord=True,
                                         model_e=True, binom=True, netz=True, ci=True, log_x=log_x, h=0.5, title=True)
                        fig = fw.fig_werkstatt(ds, opt)
                        self.assertGreaterEqual(len(fig.axes), 2 if eta == "beide" else 1)
                        self.assertTrue(fig.legends)
                        plt.close(fig)
                        n += 1
        self.assertEqual(n, len(fw.VIEWS) * 2 * 4 * 2)

    def test_schar_colors_and_configs(self):
        ds = _datasets()
        cfgs = fw.configs_for(fw.Options(schar="alpha"), [ds["0,9"]])
        self.assertEqual([c[0][1] for c in cfgs], [0.3, 0.5, 0.7, 0.9])
        self.assertEqual(len({c[1] for c in cfgs}), 4)
        self.assertEqual(fw.shades(1), [fw.COLORS["adaptiv"]])
        self.assertEqual(len(fw.shades(5)), 5)
        single = fw.configs_for(fw.Options(T=20.0, alpha=0.5, delta_min=0.5), [ds["0,9"]])
        self.assertEqual(single[0][0], (20.0, 0.5, 0.5))
        self.assertEqual(single[0][1], fw.COLORS["adaptiv"])

    def test_adaptiv_series_falls_back_to_roc_alle(self):
        rows = _synthetic_rows()
        ds = fw.Dataset("0,9", rows, rr.tables(rows, None), None)
        s = fw.adaptiv_series(ds, 20.0, 0.5, 0.5)
        self.assertIsNotNone(s)
        self.assertEqual([p["h"] for p in s.pts], [0.5, 1.0, 2.0])
        self.assertIsNone(fw.adaptiv_series(ds, 10.0, 0.9, 0.5))     # α ≠ Basis ohne Werkstatt-Datei

    def test_zoom_and_units(self):
        ds = _datasets()
        fig = fw.fig_werkstatt(ds, fw.Options(x_unit="tag", zoom=(0.0, 50.0, 80.0, 100.0)))
        ax = fig.axes[0]
        self.assertEqual(ax.get_xlim(), (0.0, 50.0))
        self.assertEqual(ax.get_ylim(), (80.0, 100.0))
        self.assertIn("je Bett und Tag", ax.get_xlabel())
        plt.close(fig)
        fig = fw.fig_werkstatt(ds, fw.Options(view="roc", zoom=(None, 0.02, None, None)))
        self.assertEqual(fig.axes[0].get_xlim()[1], 0.02)
        plt.close(fig)

    def test_table_and_names(self):
        ds = _datasets()
        tbl = fw.table_rows(ds, fw.Options(eta="beide", h=1.0))
        self.assertEqual(len(tbl), 6)
        ad = [r for r in tbl if r["Regel"].startswith("adaptiv")]
        self.assertEqual(len(ad), 2)
        self.assertTrue(ad[0]["Δ Sehne"].startswith("+5,0 [+3,0; +7,0] pp"))
        self.assertEqual([r["Δ Sehne"] for r in tbl if r["Regel"] == "2-aus-4"], ["—", "—"])
        tbl2 = fw.table_rows(ds, fw.Options(h=0.5))
        self.assertTrue(all(r["Δ Hülle"] == "—" for r in tbl2))
        self.assertEqual(fw.export_name(fw.Options()), "Werkstatt_froc_eta09_T10_a05_d05_h1")
        self.assertEqual(fw.export_name(fw.Options(view="roc", eta="beide", schar="T", log_x=True, h=0.5)),
                         "Werkstatt_roc_beide_Schar_T_h05_log")

    def test_export(self):
        ds = _datasets()
        with tempfile.TemporaryDirectory() as tmp:
            paths = fw.save(fw.fig_werkstatt(ds, fw.Options()), Path(tmp), "probe")
            self.assertEqual([p.suffix for p in paths], [".png", ".svg"])
            self.assertTrue(all(p.exists() and p.stat().st_size > 1000 for p in paths))

    def test_werkstatt_csv_roundtrip(self):
        w = _synthetic_werk()
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            ws.write(d, w["rows"], w["h1"], w["meta"], {ws.key(*ws.BASE): {"1": {"A": [1.0] * 11}}})
            self.assertTrue(ws.available(d))
            back = ws.load(d)
            self.assertEqual(len(back["rows"]), len(w["rows"]))
            self.assertEqual(ws.configs_in(back["rows"]), ws.configs())
            self.assertEqual(ws.values_of(back["rows"], "alpha"), [0.3, 0.5, 0.7, 0.9])
            self.assertEqual(ws.values_of(back["rows"], "T"), [5.0, 10.0, 20.0, 30.0])
            self.assertEqual(len(ws.rows_for(back["rows"], 10.0, 0.5, 1.0)), 3)
            self.assertEqual(len(ws.h1_table(back)), 10)


# ─────────────────────────────────────────────────────────────────────────────
# Gerechnete Daten: Basis = roc_alle.csv / roc_tabellen.json (R1a, R2, R4b)
# ─────────────────────────────────────────────────────────────────────────────

class _DataBase:
    eta = "0,9"
    roc_dir = rr.ROC_DIR

    @classmethod
    def setUpClass(cls):
        cls.rows = rr.load_rows(cls.roc_dir / rr.CSV_OUT)
        cls.tb = json.loads((cls.roc_dir / rr.TAB_JSON).read_text(encoding="utf-8"))
        cls.w = ws.load(cls.roc_dir)

    def test_all_configs_all_h(self):
        hs = sorted({r["h"] for r in self.rows if r.get("art") == "Regel"})
        self.assertEqual(len(hs), 15)
        for c in ws.configs():
            sel = ws.rows_for(self.w["rows"], *c)
            self.assertEqual([r["h"] for r in sel], hs, ws.key(*c))
        self.assertEqual(set(self.w["h1"]), {ws.key(*c) for c in ws.configs()})
        for v in self.w["h1"].values():
            self.assertEqual(v["stueck"]["n_boot"], 1000)

    def _compare(self, T):
        base = {round(r["h"], 6): r for r in fr.curve(self.rows, "Regel", "adaptiv", T)}
        self.assertEqual(len(base), 15, f"T = {T}")
        for r in ws.rows_for(self.w["rows"], T, 0.5, 0.5):
            b = base[round(r["h"], 6)]
            for a, bcol in (("tpr", "tpr"), ("fpr_ii", "fpr_ii"), ("erkannt", "eta_oder_erkannt"), ("erkannt_streng", "erkannt_streng"),
                            ("falsch_je_bett_h", "falsch_je_bett_h"), ("verz_kreuz_median_s", "verz_kreuz_median_s"),
                            ("verz_kreuz_p90_s", "verz_kreuz_p90_s"), ("hvl_anteil", "hvl_anteil")):
                self.assertAlmostEqual(r[a], b[bcol], places=5, msg=f"T = {T}, h = {r['h']}, {a}")

    def test_base_equals_roc_alle(self):
        for T in fr.available_T(self.rows):
            self._compare(T)

    def test_base_deltas_equal_tables(self):
        for view in ("stueck", "zeit"):
            z = next(z for z in self.tb["huelle"][view]["zeilen"] if abs(z["h"] - 1.0) < 1e-9)
            d = self.w["h1"][ws.key(*ws.BASE)][view]
            self.assertAlmostEqual(d["delta_pp"], z["delta_pp"], places=6, msg=view)
            self.assertAlmostEqual(d["delta_sehne_pp"], z["delta_sehne_pp"], places=6, msg=view)
            self.assertAlmostEqual(d["x"], z["x"], places=6)
            self.assertLessEqual(d["delta_ci"][0], d["delta_pp"] + 1e-9)
            self.assertGreaterEqual(d["delta_ci"][1], d["delta_pp"] - 1e-9)

    def test_figure_with_real_data(self):
        ds = {self.eta: fw.Dataset(self.eta, self.rows, self.tb, self.w)}
        for view in fw.VIEWS:
            fig = fw.fig_werkstatt(ds, fw.Options(view=view, eta=self.eta, schar="delta_min", chord=True, ci=True))
            plt.close(fig)
        tbl = fw.table_rows(ds, fw.Options(eta=self.eta, schar="alpha"))
        self.assertEqual(len(tbl), 2 + 4)
        self.assertTrue(all(r["Δ Hülle"] != "—" for r in tbl if r["Regel"].startswith("adaptiv")))
        fig = fw.fig_werkstatt(ds, fw.Options(view="geraet_system", eta=self.eta))
        self.assertEqual(len(fig.axes), 3)
        plt.close(fig)
        self.assertEqual(len(fw.device_table_rows(ds, fw.Options(view="geraet_system", eta=self.eta))), 4)
        for k in (2, 3):
            m, h, rows = fw.binom_deviation(ds[self.eta], k)
            self.assertEqual(len(rows), 15)
            self.assertTrue(0 <= m <= 100)


@unittest.skipUnless(HAVE_WS["0,9"], "Werkstatt-Daten η = 0,9 fehlen (python -m analysis.roc_ziel3.werkstatt)")
class Data09Tests(_DataBase, unittest.TestCase):
    pass


@unittest.skipUnless(HAVE_WS["0,7"], "Werkstatt-Daten η = 0,7 fehlen (python -m analysis.roc_ziel3.werkstatt)")
class Data07Tests(_DataBase, unittest.TestCase):
    eta = "0,7"
    roc_dir = rr.ROC_DIR_P07


if __name__ == "__main__":
    unittest.main()
