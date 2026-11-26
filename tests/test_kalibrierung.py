"""Klassische ROC des Filters und Parameterwahl (analysis/roc_ziel3/kalibrierung.py), ereignisbezogene Achse und Ansicht
„klassisch“ (figures_werkstatt.py), Blöcke 1/4/4b des Reiters Kapitel 5 mit Parameter Prototyp | kalibriert.

Spielzeugdaten: eskalierte Fehlalarm-Episoden, AUC der Trapezregel mit (0, 0)/(100, 100), Kriterium der Parameterwahl
(Nebenbedingung Latenz, Gleichstand ± 0,005), Bootstrap der AUC-Differenz, Dateien lesen/schreiben, Abbildungen und
Tabellen mit synthetischen Kalibrierungsdateien.  Mit echten Dateien: Episoden bei h = 1 (η 0,9), P0-Monotonie der Rot-
Zahl, Latenz der gewählten Konfiguration ≤ 12 s, Netzläufe B/C/D offline.
"""

import json
import math
import tempfile
import unittest
from pathlib import Path

import matplotlib.pyplot as plt

from analysis.belastungsmodell_app import kapitel5 as k5
from analysis.belastungsmodell_app import sim_data as sd
from analysis.roc_ziel3 import dashcore as dc
from analysis.roc_ziel3 import figures_roc as fr
from analysis.roc_ziel3 import figures_werkstatt as fw
from analysis.roc_ziel3 import kalibrierung as kb
from analysis.roc_ziel3 import metrics as mt
from analysis.roc_ziel3 import werkstatt as ws
from tests.test_kapitel5 import _synthetic_runs
from tests.test_werkstatt import _datasets as _ws_datasets

HAVE_EPISODEN = (kb.OUT_DIR / "episoden.csv").exists()
HAVE_KLASSISCH = (kb.OUT_DIR / f"klassisch_{ws.key(*kb.PROTOTYP)}.csv").exists()
HAVE_KAL = (kb.OUT_DIR / "kalibriert.json").exists()
HAVE_NETZ = (kb.OUT_DIR / f"netz_{ws.key(*kb.PROTOTYP)}.csv").exists()
KAL = kb.PROTOTYP if not HAVE_KAL else (kb.kalibriert_config() or kb.PROTOTYP)


# ─────────────────────────────────────────────────────────────────────────────
# Spielzeugdaten
# ─────────────────────────────────────────────────────────────────────────────

def _toy_truth():
    return {"P1": mt.PatientTruth(patient="P1", window=(0.0, 1000.0), crises=[(500.0, 530.0)], open_crises=[],
                                  stable=[(0.0, 490.0), (540.0, 1000.0)])}


def _toy_src():
    src = {s: {"P1": []} for s in mt.SOURCES}
    src[mt.SOURCES[0]]["P1"] = [(100.0, 110.0), (300.0, 305.0), (505.0, 520.0)]     # zwei Fehlalarme + einer in der Krise
    src[mt.SOURCES[1]]["P1"] = [(105.0, 120.0)]                                       # überlappt den ersten → eine Episode
    return src


def _adaptiv_row(eta, h, cfg, p0, ep_n, ep_k, n_k, n_det, n_det_s, n_false, bett_h, lat):
    T, a, d = cfg
    return dict(eta=eta, h=h, konfig=ws.key(T, a, d), T=T, alpha=a, delta_min=d, P0=p0, clo=kb.clo(p0), regel="adaptiv",
                ep_gesamt=ep_n, ep_eskaliert=ep_k, ep_anteil=ep_k / ep_n, tpr=0.5 + 0.4 * p0, fpr=0.01 * (0.5 + p0),
                fpr_ii=0.008 * (0.5 + p0), erkannt=n_det / n_k, erkannt_streng=n_det_s / n_k, n_krisen=n_k, n_erkannt=n_det,
                n_erkannt_streng=n_det_s, n_ereignisse=n_false + 100, n_falsch=n_false, falsch_je_bett_h=n_false / bett_h,
                verz_kreuz_median_s=lat, verz_kreuz_p90_s=lat + 3.0, verz_median_s=lat + 6.0, verz_p90_s=lat + 9.0,
                n_rot=n_false + 100, bett_h=bett_h, wall_s=7.0)


def _k_row(eta, h, name, ep_n, ep_k, n_k, n_det, n_det_s, n_false, bett_h):
    r = _adaptiv_row(eta, h, (10.0, 0.5, 0.5), 0.5, ep_n, ep_k, n_k, n_det, n_det_s, n_false, bett_h, 5.0)
    r.update(konfig=name, regel=name, T=None, alpha=None, delta_min=None, P0=None, clo=None, wall_s=None)
    return r


def _write_synthetic_kalibrierung(out: Path, kal=(6.0, 0.5, 0.7)):
    """Synthetische Dateien der Kalibrierung für beide η: Episoden (h 0,5/1/2), P0-Kurven (h 1) für Prototyp und kalibriert,
    h-Kurve kalibriert, Netzläufe, kalibriert.json, pruefung.json."""
    out.mkdir(parents=True, exist_ok=True)
    ep_rows, cl_rows, hk_rows, netz_rows, pats = [], [], [], [], {}
    for eta in ("0,9", "0,7"):
        for i, h in enumerate((0.5, 1.0, 2.0)):
            ep_n = 8000 // (i + 1)
            for k, name in enumerate(fw.K_RULES, start=1):
                ep_rows.append(_k_row(eta, h, name, ep_n, int(ep_n * 0.9 ** (k * 4)), 300, 300 - 20 * k - 10 * i, 290 - 25 * k - 10 * i,
                                      int(2000 / k / (i + 1)), 240.0))
            for cfg in ws.configs():
                ep_rows.append(_adaptiv_row(eta, h, cfg, 0.5, ep_n, int(ep_n * 0.08), 300, 290 - 10 * i, 275 - 10 * i, 700 // (i + 1), 240.0, 10.5))
        # P0-Kurven bei h = 1
        for cfg in (ws.BASE, kal):
            for name_k, name in enumerate(fw.K_RULES, start=1):
                cl_rows.append(_k_row(eta, 1.0, name, 8000, int(8000 * 0.9 ** (name_k * 4)), 300, 300 - 20 * name_k, 290 - 25 * name_k,
                                      int(2000 / name_k), 240.0))
            for p0 in kb.P0_GRID:
                sens = 0.70 + 0.29 * p0 + (0.02 if cfg == kal else 0.0)
                cl_rows.append(_adaptiv_row(eta, 1.0, cfg, p0, 8000, int(8000 * (0.01 + 0.3 * p0 ** 2)), 300, int(300 * sens),
                                            int(300 * (sens - 0.06)), int(2000 * (0.05 + 0.5 * p0 ** 2)), 240.0, 12.0 - 3.0 * p0))
        for h in (0.5, 1.0, 2.0):
            hk_rows.append(_adaptiv_row(eta, h, kal, 0.5, 8000, 600, 300, 291, 277, 650, 240.0, 9.0))
    for run in ("12h", "A", "B", "C", "D"):
        netz_rows.append(_adaptiv_row(run, 1.0, ws.BASE, 0.5, 5000, 400, 300, 293, 274, 700, 240.0, 10.2))
        d = dict(netz_rows[-1])
        d.update(konfig="Dashboard", regel="Dashboard", ep_eskaliert=410, ep_anteil=410 / 5000)
        netz_rows.append(d)
        for k, name in enumerate(fw.K_RULES, start=1):
            netz_rows.append(_k_row(run, 1.0, name, 5000, int(5000 * 0.9 ** (k * 4)), 300, 300 - 20 * k, 290 - 25 * k, int(2000 / k), 240.0))
    kb.write_rows(out / "episoden.csv", ep_rows)
    for cfg in (ws.BASE, kal):
        kb.write_rows(out / f"klassisch_{ws.key(*cfg)}.csv", [r for r in cl_rows if r["konfig"] == ws.key(*cfg) or r["regel"] != "adaptiv"])
        kb.write_pat(out / f"klassisch_{ws.key(*cfg)}.json", pats)
    kb.write_rows(out / f"hkurve_{ws.key(*kal)}.csv", hk_rows)
    kb.write_rows(out / f"netz_{ws.key(*ws.BASE)}.csv", netz_rows)
    kal_rows = [r for r in netz_rows if r["regel"] == "adaptiv"]
    for r in kal_rows:
        r = dict(r)
        r.update(konfig=ws.key(*kal), T=kal[0], alpha=kal[1], delta_min=kal[2], ep_eskaliert=350, ep_anteil=350 / 5000, n_erkannt=292)
    kb.write_rows(out / f"netz_{ws.key(*kal)}.csv", [dict(r, konfig=ws.key(*kal), T=kal[0], alpha=kal[1], delta_min=kal[2],
                                                           ep_eskaliert=350, ep_anteil=350 / 5000) for r in kal_rows])
    (out / "kalibriert.json").write_text(json.dumps(dict(
        kriterium=kb.KRITERIUM, kalibriert=dict(konfig=ws.key(*kal), T=kal[0], alpha=kal[1], delta_min=kal[2], auc=0.9612,
                                                 latenz_median_s=9.0, nebenbedingung_ok=True),
        prototyp=dict(konfig=ws.key(*ws.BASE), auc=0.9500, latenz_median_s=10.5), n_konfigurationen=128, n_vollstaendig=128,
        n_nebenbedingung_ok=100)), encoding="utf-8")
    boot = []
    for eta in ("0,9", "0,7"):
        for name, cfg in (("Prototyp", ws.BASE), ("kalibriert", kal)):
            for h in kb.H_KLASSISCH:
                for mode in ("mild", "streng"):
                    boot.append(dict(eta=eta, h=h, konfig=ws.key(*cfg), mode=mode, name=name, n_p0=15, auc_adaptiv=0.95 if name == "Prototyp" else 0.961,
                                     auc_adaptiv_ci=[0.94, 0.97], auc_kausn=0.98, auc_kausn_ci=[0.97, 0.985],
                                     differenz=-0.03 if name == "Prototyp" else -0.019, differenz_ci=[-0.04, -0.01], n_boot=1000, anteil_groesser=0.0))
    (out / "pruefung.json").write_text(json.dumps(dict(kalibriert=ws.key(*kal), prototyp=ws.key(*ws.BASE),
                                                       validierung={"0,9": "ausstehend", "0,7": "ausstehend"}, bootstrap=boot)), encoding="utf-8")
    return kal


def _datasets_with_kal(tmp: Path):
    kal = _write_synthetic_kalibrierung(tmp)
    ds = _ws_datasets()
    for d in ds.values():
        fw.attach_kalibrierung(d, tmp)
    return ds, kal


# ─────────────────────────────────────────────────────────────────────────────
# Kennzahlen und Kriterium
# ─────────────────────────────────────────────────────────────────────────────

class MetricTests(unittest.TestCase):
    def test_escalated_episodes_toy(self):
        truth, src = _toy_truth(), _toy_src()
        eps = mt.false_episodes(src, truth)
        self.assertEqual(len(eps), 2)                                   # (100, 120) und (300, 305); der Krisenalarm zählt nicht
        rot = {"P1": [(102.0, 108.0), (506.0, 525.0)]}                  # eskaliert Episode 1; Rot in der Krise ist kein Fehlalarm
        n, k, per = mt.escalated_episodes(rot, eps, truth)
        self.assertEqual((n, k), (2, 1))
        self.assertEqual(per["P1"], (2, 1))
        n2, k2, _ = mt.escalated_episodes({"P1": [(495.0, 535.0)]}, eps, truth)      # Rot berührt Krise ± 10 s → kein Fehlalarm
        self.assertEqual((n2, k2), (2, 0))
        n3, k3, _ = mt.escalated_episodes({"P1": [(119.0, 125.0), (304.0, 306.0)]}, eps, truth)
        self.assertEqual((n3, k3), (2, 2))

    def test_auc_points(self):
        self.assertAlmostEqual(kb.auc_points([]), 0.5)
        self.assertAlmostEqual(kb.auc_points([(0.0, 100.0)]), 1.0)
        self.assertAlmostEqual(kb.auc_points([(50.0, 100.0)]), 0.75)
        self.assertAlmostEqual(kb.auc_points([(20.0, 80.0), (10.0, 50.0)]), kb.auc_points([(10.0, 50.0), (20.0, 80.0)]))
        self.assertAlmostEqual(kb.auc_points([(10.0, 50.0), (20.0, 80.0)]),
                               0.5 * (0.1 * 0.5 + 0.1 * (0.5 + 0.8) + 0.8 * (0.8 + 1.0)))
        self.assertAlmostEqual(kb.auc_points([(float("nan"), 1.0), (50.0, 100.0)]), 0.75)

    def test_clo_and_cache_names(self):
        self.assertAlmostEqual(kb.clo(0.5), 0.0)
        self.assertAlmostEqual(kb.clo(0.98), math.log(49))
        contract = dc.load_contract()
        raw = dc.build_raw_clinical_db(contract, horizon_T=20.0, alpha=0.5, base_prob_P0=0.9)
        self.assertAlmostEqual(raw["base_prob_P0"], 0.9)
        self.assertEqual(kb.cache_path(Path("c"), "h_1.00", raw, contract, 0.5, 0.9).name, "h_1.00_T20_a0.5_d0.5_P0.9.json")
        self.assertEqual(kb.cache_path(Path("c"), "h_1.00", raw, contract, 0.5, 0.5).name, "h_1.00_T20.json")   # Werkstatt-Datei
        raw2 = dc.build_raw_clinical_db(contract)
        self.assertAlmostEqual(raw2["base_prob_P0"], float(contract["context"]["base_prob_P0"]))
        self.assertEqual(len(kb.grid_configs()), 128)
        self.assertEqual(len(kb.P0_GRID), 15)

    def test_waehle_kalibriert(self):
        tab = [
            dict(konfig="A", T=10.0, alpha=0.5, delta_min=0.5, auc=0.950, latenz_median_s=10.0, nebenbedingung_ok=True, vollstaendig=True),
            dict(konfig="B", T=12.0, alpha=0.5, delta_min=0.5, auc=0.953, latenz_median_s=11.5, nebenbedingung_ok=True, vollstaendig=True),
            dict(konfig="C", T=20.0, alpha=0.5, delta_min=0.5, auc=0.970, latenz_median_s=13.0, nebenbedingung_ok=False, vollstaendig=True),
            dict(konfig="D", T=5.0, alpha=0.5, delta_min=0.5, auc=0.990, latenz_median_s=4.0, nebenbedingung_ok=True, vollstaendig=False),
        ]
        self.assertEqual(kb.waehle_kalibriert(tab)["konfig"], "A")             # Gleichstand ± 0,005 → kleinere Latenz
        tab[1]["auc"] = 0.960
        self.assertEqual(kb.waehle_kalibriert(tab)["konfig"], "B")             # klarer Vorsprung
        self.assertIsNone(kb.waehle_kalibriert([tab[2], tab[3]]))              # keiner zulässig
        self.assertIn("≤ 12 s", kb.KRITERIUM)
        self.assertIn("± 0,005", kb.KRITERIUM)

    def test_auswertung_gitter_and_merge(self):
        rows = []
        for p0 in (0.05, 0.5, 0.95):
            rows.append(_adaptiv_row("0,9", 1.0, (10.0, 0.5, 0.5), p0, 1000, int(1000 * 0.2 * p0), 300, int(300 * (0.8 + 0.19 * p0)),
                                     int(300 * (0.7 + 0.2 * p0)), 500, 240.0, 11.0))
        rows.append(_adaptiv_row("0,9", 1.0, (20.0, 0.5, 0.5), 0.5, 1000, 50, 300, 270, 250, 300, 240.0, 14.0))
        tab = kb.auswertung_gitter(rows)
        t10 = next(t for t in tab if t["konfig"] == "T10_a0.5_d0.5")
        t20 = next(t for t in tab if t["konfig"] == "T20_a0.5_d0.5")
        self.assertEqual(t10["n_punkte"], 3)
        self.assertTrue(t10["nebenbedingung_ok"])
        self.assertFalse(t20["nebenbedingung_ok"])
        self.assertFalse(t10["vollstaendig"])
        self.assertAlmostEqual(t10["auc"], kb.auc_points([(100 * r["ep_anteil"], 100 * r["erkannt"]) for r in rows[:3]]))
        merged = kb._merge_rows(rows, [dict(rows[1], n_rot=999)])
        self.assertEqual(len(merged), 4)
        self.assertEqual(next(r for r in merged if r["P0"] == 0.5 and r["konfig"] == "T10_a0.5_d0.5")["n_rot"], 999)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.csv"
            kb.write_rows(p, rows)
            back = kb.read_rows(p)
            self.assertEqual(len(back), 4)
            self.assertEqual(back[0]["eta"], "0,9")
            self.assertAlmostEqual(back[0]["ep_anteil"], rows[0]["ep_anteil"], places=5)
            self.assertIsNone(kb.latenz_p05(back, "T5_a0.5_d0.5"))
            self.assertAlmostEqual(kb.latenz_p05(back, "T10_a0.5_d0.5"), 11.0)

    def test_auc_bootstrap_toy(self):
        pats = {}
        F = len(kb.PAT_FIELDS)
        patients = [f"P{i}" for i in range(6)]
        for p0 in (0.05, 0.5, 0.95):
            pats[kb.pat_key("0,9", 1.0, "T10_a0.5_d0.5", p0)] = {
                p: [40.0, 40.0 * (0.05 + 0.2 * p0) * (1 + 0.1 * i), 15.0, 15.0 * (0.8 + 0.19 * p0), 14.0 * (0.8 + 0.19 * p0), 30.0, 12.0,
                    900.0, 600.0, 40000.0, 300.0] for i, p in enumerate(patients)}
        for k, name in enumerate(fw.K_RULES, start=1):
            pats[kb.pat_key("0,9", 1.0, name, None)] = {
                p: [40.0, 40.0 * 0.9 ** (4 * k), 15.0, 15.0 - k, 14.0 - k, 30.0 / k, 12.0, 900.0, 600.0, 40000.0, 300.0 / k] for p in patients}
        for v in pats.values():
            for row in v.values():
                self.assertEqual(len(row), F)
        b = kb.auc_bootstrap(pats, "0,9", 1.0, "T10_a0.5_d0.5", (0.05, 0.5, 0.95), "mild", n_boot=200, seed=3)
        self.assertEqual(b["n_p0"], 3)
        self.assertAlmostEqual(b["differenz"], b["auc_adaptiv"] - b["auc_kausn"])
        self.assertLessEqual(b["auc_adaptiv_ci"][0], b["auc_adaptiv"] + 1e-9)
        self.assertGreaterEqual(b["auc_adaptiv_ci"][1], b["auc_adaptiv"] - 1e-9)
        self.assertLessEqual(b["differenz_ci"][0], b["differenz_ci"][1])
        self.assertEqual(kb.auc_bootstrap(pats, "0,7", 1.0, "T10_a0.5_d0.5"), {})


# ─────────────────────────────────────────────────────────────────────────────
# Abbildungen und Tabellen (synthetische Kalibrierungsdateien)
# ─────────────────────────────────────────────────────────────────────────────

class FigureTests(unittest.TestCase):
    def test_attach_and_event_axis(self):
        with tempfile.TemporaryDirectory() as tmp:
            ds, kal = _datasets_with_kal(Path(tmp))
            d = ds["0,9"]
            self.assertEqual(d.kal, kal)
            self.assertEqual(set(d.klassisch), {ws.key(*ws.BASE), ws.key(*kal)})
            self.assertEqual(len(d.netz), 6)
            self.assertEqual(len(d.netz_kal), 1)
            self.assertEqual(len(d.boot), 12)
            s2 = fw.rule_series(d, "2-aus-4")
            self.assertTrue(all(p["x_ep"] is not None for p in s2.pts))
            self.assertAlmostEqual(s2.at(1.0)["x_ep"], 100 * int(4000 * 0.9 ** 8) / 4000, places=6)
            sa = fw.adaptiv_series(d, *ws.BASE)
            self.assertAlmostEqual(sa.at(1.0)["x_ep"], 8.0, places=6)
            sk = fw.adaptiv_series(d, *kal)                                    # Kurve über h aus hkurve_<kal>.csv
            self.assertIsNotNone(sk)
            self.assertEqual([p["h"] for p in sk.pts], [0.5, 1.0, 2.0])
            self.assertAlmostEqual(sk.at(2.0)["x_ep"], 7.5, places=6)
            for eta in ("0,9", "beide"):
                for mode in ("mild", "streng"):
                    opt = fw.Options(view="froc", eta=eta, x_unit="ereignis", mode=mode, chord=True, ci=True, netz=True, kal=kal)
                    fig = fw.fig_werkstatt(ds, opt)
                    labels = [t.get_text() for t in fig.legends[0].get_texts()]
                    self.assertTrue(any("AUC" in lab for lab in labels))
                    self.assertEqual(fig.axes[0].get_xlim(), (0.0, 100.0))
                    self.assertIn("eskalierte Fehlalarm-Episoden", fig.axes[0].get_xlabel())
                    self.assertEqual("streng" in fig.axes[0].get_ylabel(), mode == "streng")
                    plt.close(fig)
            opt = fw.Options(view="froc", eta="0,9", x_unit="ereignis", log_x=True, schar="T")
            fig = fw.fig_werkstatt(ds, opt)
            self.assertEqual(fig.axes[0].get_xscale(), "log")
            plt.close(fig)
            rows = fw.table_rows(ds, fw.Options(view="froc", eta="0,9", x_unit="ereignis"))
            self.assertTrue(all("eskalierte Fehlalarm-Episoden" in r for r in rows))
            self.assertIn("%", rows[0]["eskalierte Fehlalarm-Episoden"])
            # Konfiguration kalibriert als Einzelkurve nach Stück (Beschriftung)
            opt = fw.Options(view="froc", eta="0,9", x_unit="h", T=kal[0], alpha=kal[1], delta_min=kal[2], kal=kal)
            fig = fw.fig_werkstatt(ds, opt)
            labels = [t.get_text() for t in fig.legends[0].get_texts()]
            self.assertTrue(any("kalibriert" in lab for lab in labels))
            plt.close(fig)

    def test_klassisch_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            ds, kal = _datasets_with_kal(Path(tmp))
            for x_unit in ("ereignis", "zeit", "h"):
                for compare in (False, True):
                    opt = fw.Options(view="klassisch", eta="beide", x_unit=x_unit, compare_params=compare, kal=kal, h=1.0)
                    fig = fw.fig_werkstatt(ds, opt)
                    labels = [t.get_text() for t in fig.legends[0].get_texts()]
                    self.assertEqual(len(fig.axes), 2)
                    self.assertTrue(any("k-aus-4-Linienzug" in lab for lab in labels))
                    self.assertEqual(sum(1 for lab in labels if lab.startswith("adaptiv")), 2 if compare else 1)
                    if x_unit != "h":
                        self.assertTrue(all("AUC" in lab for lab in labels if lab.startswith("adaptiv")))
                        self.assertEqual(fig.axes[0].get_xlim(), (0.0, 100.0))
                    self.assertIn(fw.X_UNITS_KLASSISCH[x_unit].split(" (")[0].split(",")[0], fig.axes[0].get_xlabel())
                    plt.close(fig)
            # ohne P0-Kurven: Hinweis statt Kurve, kein Fehler
            leer = _ws_datasets()
            fig = fw.fig_werkstatt(leer, fw.Options(view="klassisch", eta="0,9"))
            self.assertTrue(any("keine P0-Kurven" in t.get_text() for t in fig.axes[0].texts))
            plt.close(fig)
            opt = fw.Options(view="klassisch", eta="0,9", x_unit="ereignis", compare_params=True, kal=kal)
            rows = fw.klassisch_table_rows(ds, opt)
            p0s = [r["P0"] for r in rows if r["Regel"].startswith("adaptiv Prototyp")]
            self.assertEqual(len(p0s), 15)
            self.assertEqual(sum(1 for r in rows if r["Regel"] in fw.K_RULES), 4)
            auc = fw.auc_rows(ds, opt)
            self.assertEqual(len(auc), 2)                                      # Prototyp und kalibriert bei h = 1, mild
            self.assertTrue(all("[" in r["Δ AUC"] for r in auc))
            self.assertEqual(fw.export_name(opt), "Werkstatt_klassisch_eta09_T10_a05_d05_h1_Ereignis_beide")
            self.assertEqual(fw.export_name(fw.Options(view="klassisch", x_unit="zeit", mode="streng")),
                             "Werkstatt_klassisch_eta09_T10_a05_d05_h1_Zeit_streng")
            self.assertEqual(fw.export_name(fw.Options(view="froc", x_unit="ereignis")), "Werkstatt_froc_eta09_T10_a05_d05_h1_Ereignis")
            self.assertEqual(fw.export_name(fw.Options(view="froc", x_unit="tag")), "Werkstatt_froc_eta09_T10_a05_d05_h1_Tag")
            self.assertIn("klassisch", fw.HOWTO)
            self.assertEqual(fw.config_label(kal, kal), ws.label(*kal).replace("adaptiv (", "adaptiv kalibriert ("))
            self.assertEqual(fw.config_label(ws.BASE, kal), ws.label(*ws.BASE))

    def test_kapitel5_blocks(self):
        runs = _synthetic_runs()
        rl = [runs[k] for k in sd.RUN_ORDER if k in runs]
        with tempfile.TemporaryDirectory() as tmp:
            ds, kal = _datasets_with_kal(Path(tmp))
            netz = {}
            for r in kb.read_rows(Path(tmp) / f"netz_{ws.key(*ws.BASE)}.csv"):
                netz.setdefault(r["eta"], []).append(r)
            pts = k5.run_points(runs["12h"], "ereignis", "mild", "Prototyp", netz)
            self.assertEqual(set(pts), {"r3", "r2", "adaptiv"})
            self.assertAlmostEqual(pts["adaptiv"]["x"], 100 * 410 / 5000)          # Dashboard-Zeile
            self.assertAlmostEqual(pts["r2"]["x"], 100 * int(5000 * 0.9 ** 8) / 5000)
            g = k5.chord_gap_points(pts)
            self.assertIsNotNone(g)
            self.assertAlmostEqual(g["gap_pp"], g["y"] - g["y_sehne"])
            self.assertIsNone(k5.run_points(runs["12h"], "ereignis", "mild", "Prototyp", {}))
            pts_h = k5.run_points(runs["12h"], "h", "mild", "Prototyp", netz)        # nach Stück: Zahlen des Laufs
            self.assertAlmostEqual(pts_h["adaptiv"]["x"], runs["12h"].rules["adaptiv"].false_per_bed_h)
            self.assertAlmostEqual(k5.chord_gap(runs["12h"], "mild")["gap_pp"], k5.chord_gap_points(pts_h)["gap_pp"])
            old = k5.netz_rows
            try:
                k5.netz_rows = lambda params="Prototyp": netz
                fig = k5.fig_betriebspunkte(rl, "ereignis", "mild")          # 12 h und A haben Netzzeilen, 6 h nicht
                self.assertEqual(sum(1 for ax in fig.axes if ax.axison), 2)
                self.assertTrue(any("eskalierte" in ax.get_xlabel() for ax in fig.axes))
                plt.close(fig)
                fig = k5.fig_betriebspunkte(rl, "h", "streng", "kalibriert")       # adaptiv-Zeile der Netzdatei
                self.assertEqual(sum(1 for ax in fig.axes if ax.axison), 2)
                plt.close(fig)
                txt = k5.aussage_1(rl, "mild", "ereignis")
                self.assertIn("eskalierter Fehlalarm-Episoden", txt)
                self.assertIn("12 h", txt)
            finally:
                k5.netz_rows = old
            fig = k5.fig_betriebspunkte(rl, "ereignis", "mild", "Prototyp") if False else None
            fig = k5.fig_froc(ds, "ereignis", "mild", "kalibriert")
            labels = [t.get_text() for t in fig.legends[0].get_texts()]
            self.assertTrue(any("kalibriert" in lab for lab in labels))
            plt.close(fig)
            fig = k5.fig_klassisch(ds, "ereignis", "mild", 1.0, True)
            self.assertEqual(len(fig.axes), 2)
            plt.close(fig)
            self.assertIn("Δ", k5.aussage_4b(ds))
            self.assertIn("ausstehend", k5.aussage_4b(_ws_datasets()))
            self.assertEqual(len(k5.auc_rows(ds, "streng", 0.8)), 4)
            self.assertTrue(k5.has_klassisch(ds))
            self.assertEqual(len(k5.FRAGEN), 8)
            self.assertEqual(k5.FILES["4b"], "K5_4b_ROC_Filter")
            k = k5.kennzahlen({}, ds)
            saetze = k5.zusammenfassung(k)
            self.assertEqual(len(saetze), 4 if k.kal_konfig is None else 5)       # fünfter Satz nur mit kalibriert.json des Repos
            written = k5.export_all(runs, ds, Path(tmp) / "fig", suffix="_t")
            self.assertTrue(all(p.name.endswith(("_t.png", "_t.svg")) for p in written))
            self.assertEqual(len(written), 16)


class ValidationTests(unittest.TestCase):
    def test_eta_of_and_labels(self):
        self.assertEqual(kb.eta_of(kb.VAL_DIRS["0,9"]), "val 0,9")
        self.assertEqual(kb.eta_of(kb.VAL_DIRS["0,7"]), "val 0,7")
        self.assertEqual(kb.eta_of(Path("irgendwo") / "ziel3_roc"), "0,9")
        self.assertIn("val 0,9", fw.ETAS)
        self.assertIn("beide val", fw.ETAS)
        self.assertEqual(fw.export_name(fw.Options(view="froc", eta="val 0,7", x_unit="ereignis")),
                         "Werkstatt_froc_etaval07_T10_a05_d05_h1_Ereignis")
        self.assertEqual(fw.export_name(fw.Options(view="klassisch", eta="beide val")), "Werkstatt_klassisch_beide_val_T10_a05_d05_h1")
        ds = _ws_datasets()
        ds["val 0,9"] = ds["0,9"]
        self.assertEqual(fw._etas(fw.Options(eta="beide val"), ds), ["val 0,9"])
        self.assertEqual(fw._etas(fw.Options(eta="beide"), ds), ["0,9", "0,7"])

    def test_val_rows_and_hkurve_auc(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "ziel3_roc_val09"
            d.mkdir()
            lines = ["h;art;name;on_schwelle;tpr;fpr;eta_oder_erkannt;n_krisen;n_ereignisse;n_falsch;falsch_je_bett_h;"
                     "ereignisse_je_bett_tag;alle4_anteil_stabil;auc;fpr_ii;auc_ii;erkannt_streng;n_erkannt_weich;n_erkannt_streng"]
            for h in (0.5, 1.0):
                for dev, f in (("ECG", 0.02), ("Vent", 0.04)):
                    lines.append(f"{h};Geraet;{dev};;0.8;{f};1;300;1000;900;4;96;0.1;0.9;{f};0.9;;;")
                for k in (2, 3):
                    lines.append(f"{h};Regel;{k}-aus-4;;0.7;0.01;0.95;300;500;400;{6 / k};;;;0.009;;0.9;285;270")
            (d / "roc_kausn.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
            hk = [_adaptiv_row("val 0,9", h, ws.BASE, 0.5, 8000, 600, 300, 291, 277, 650, 240.0, 9.0) for h in (0.5, 1.0)]
            hk.append(_k_row("val 0,9", 1.0, "2-aus-4", 8000, 1300, 300, 298, 296, 1400, 240.0))
            rows = kb.val_rows(d, hk)
            self.assertEqual(sum(1 for r in rows if r["art"] == "Geraet"), 4)
            self.assertEqual(sum(1 for r in rows if r["name"] == "adaptiv"), 2)
            ad = next(r for r in rows if r["name"] == "adaptiv" and abs(r["h"] - 1.0) < 1e-9)
            self.assertEqual(ad["T_s"], 10.0)
            self.assertAlmostEqual(ad["eta_oder_erkannt"], 291 / 300)
            self.assertAlmostEqual(ad["fpr_j_mittel"], 0.03)
            self.assertAlmostEqual(next(r for r in rows if r["name"] == "ECG")["fpr_j_mittel"], 0.03)
            out = Path(tmp) / "kal"
            kb.write_val_rows(kb.val_rows_path(d, out), rows)
            back, tb = kb.load_val_dataset_rows(d, out)
            self.assertEqual(len(back), len(rows))
            self.assertEqual(len(fr.curve(back, "Regel", "adaptiv", 10.0)), 2)
            self.assertIn("auc", tb)
            self.assertEqual(kb.load_val_dataset_rows(Path(tmp) / "fehlt", out), (None, None))
            dset = fw.val_dataset(d, out)
            self.assertIsNotNone(dset)
            self.assertEqual(dset.eta, "val 0,9")
            self.assertIsNotNone(fw.adaptiv_series(dset, *ws.BASE))
            auc = kb.hkurve_auc(hk, "val 0,9", "mild")
            self.assertIn(ws.key(*ws.BASE), auc)
            self.assertNotIn("2-aus-4", auc)                                   # nur ein h → keine Kurve
            self.assertAlmostEqual(auc[ws.key(*ws.BASE)], kb.auc_points([(7.5, 97.0), (7.5, 97.0)]))


# ─────────────────────────────────────────────────────────────────────────────
# Echte Dateien
# ─────────────────────────────────────────────────────────────────────────────


@unittest.skipUnless((kb.OUT_DIR / "pruefung_val.json").exists(), "pruefung_val.json fehlt")
class RealValTests(unittest.TestCase):
    def test_val_entries(self):
        pv = kb.load_pruefung_val()
        etas = {b["eta"] for b in pv["bootstrap"]}
        self.assertTrue(etas <= {"val 0,9", "val 0,7"})
        for eta in etas:
            names = {(b["name"], b["mode"]) for b in pv["bootstrap"] if b["eta"] == eta}
            self.assertEqual(names, {("Prototyp", "mild"), ("Prototyp", "streng"), ("kalibriert", "mild"), ("kalibriert", "streng")})
            self.assertEqual(pv["szenarien"][eta]["seed"], kb.VAL_SEED[eta])
            self.assertIn("2-aus-4", pv["hkurve_auc"][eta]["mild"])
            self.assertIn(pv["kalibriert"], pv["hkurve_auc"][eta]["mild"])
        for d in kb.VAL_DIRS.values():
            if d.exists():
                rows, tb = kb.load_val_dataset_rows(d)
                self.assertIsNotNone(rows)
                self.assertEqual(len(fr.curve(rows, "Regel", "adaptiv", 10.0)), 15)

@unittest.skipUnless(HAVE_EPISODEN, "episoden.csv fehlt")
class RealEpisodeTests(unittest.TestCase):
    def test_h1_eta09(self):
        rows = [r for r in kb.load_episoden() if r["eta"] == "0,9" and abs(r["h"] - 1.0) < 1e-9]
        by = {r["konfig"]: r for r in rows}
        self.assertEqual(by["2-aus-4"]["ep_gesamt"], 8715)
        self.assertEqual(by["2-aus-4"]["ep_eskaliert"], 1397)
        self.assertEqual(by["3-aus-4"]["ep_eskaliert"], 483)
        self.assertEqual(by[ws.key(*ws.BASE)]["ep_eskaliert"], 751)
        self.assertGreater(by["1-aus-4"]["ep_anteil"], 0.99)
        self.assertLess(by["4-aus-4"]["ep_anteil"], 0.02)
        # adaptiv liegt zwischen 3-aus-4 und 2-aus-4
        self.assertLess(by["3-aus-4"]["ep_anteil"], by[ws.key(*ws.BASE)]["ep_anteil"])
        self.assertLess(by[ws.key(*ws.BASE)]["ep_anteil"], by["2-aus-4"]["ep_anteil"])


@unittest.skipUnless(HAVE_KLASSISCH, "klassisch_<Prototyp>.csv fehlt")
class RealKlassischTests(unittest.TestCase):
    def test_p0_monotone(self):
        rows, pats = kb.load_klassisch(kb.PROTOTYP)
        for eta in ("0,9", "0,7"):
            ad = sorted([r for r in rows if r["eta"] == eta and r["regel"] == "adaptiv" and abs(r["h"] - 1.0) < 1e-9], key=lambda r: r["P0"])
            if not ad:
                continue
            self.assertEqual(len(ad), 15)
            rot = [r["n_rot"] for r in ad]
            self.assertEqual(rot, sorted(rot))                                   # mehr Kontext → niedrigere Schwelle → mehr Rot
            self.assertLessEqual(ad[0]["ep_anteil"], ad[-1]["ep_anteil"])
            self.assertLessEqual(ad[0]["erkannt"], ad[-1]["erkannt"])
            mid = next(r for r in ad if abs(r["P0"] - 0.5) < 1e-9)
            self.assertAlmostEqual(mid["clo"], 0.0)
            self.assertTrue(all(kb.pat_key(eta, 1.0, ws.key(*kb.PROTOTYP), r["P0"]) in pats for r in ad))


@unittest.skipUnless(HAVE_KAL, "kalibriert.json fehlt")
class RealKalTests(unittest.TestCase):
    def test_criterion_holds(self):
        d = kb.load_kalibriert()
        k = d["kalibriert"]
        self.assertTrue(k["nebenbedingung_ok"])
        self.assertLessEqual(k["latenz_median_s"], kb.LATENZ_MAX_S)
        self.assertTrue(k["vollstaendig"])
        tab = kb.load_gitter_tabelle()
        self.assertEqual(len(tab), d["n_konfigurationen"])
        ok = [t for t in tab if t["nebenbedingung_ok"] == "True" and t["vollstaendig"] == "True"]
        best = max(t["auc"] for t in ok)
        self.assertGreaterEqual(k["auc"], best - kb.TIE_AUC - 1e-9)
        self.assertEqual(sum(1 for t in tab if t["gewaehlt"] == "kalibriert"), 1)
        self.assertEqual(d["kriterium"], kb.KRITERIUM)


@unittest.skipUnless(HAVE_NETZ, "netz_<Prototyp>.csv fehlt")
class RealNetzTests(unittest.TestCase):
    def test_runs_present(self):
        rows = kb.load_netz(kb.PROTOTYP)
        runs = {r["eta"] for r in rows}
        self.assertTrue({"12h", "A", "B", "C", "D"} <= runs)
        for run in ("12h", "A", "B", "C", "D"):
            regeln = {r["regel"] for r in rows if r["eta"] == run}
            self.assertTrue({"adaptiv", "Dashboard", "2-aus-4", "3-aus-4"} <= regeln)
        d = next(r for r in rows if r["eta"] == "D" and r["regel"] == "Dashboard")
        self.assertAlmostEqual(d["falsch_je_bett_h"], 1.27, delta=0.02)      # Kontrolle: Übersicht PC 2 1,27


if __name__ == "__main__":
    unittest.main()
