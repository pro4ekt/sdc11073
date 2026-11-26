"""Ziel 3 – ROC bei Variation der Gerätegrenzwerte (analysis/roc_ziel3).

Prüft den Offline-Kern (Dashboard-Filter mit Modellzeit), die Kennzahlen nach Zeit und Stück (x-Varianten i/ii,
streng/mild, Verzögerung), die Messgrößen der Modellerweiterungen, die Theorie (Formel (a), Modell (b), E1, E2 mit
ihren Reduktionen auf ``model_ziel3``), die Abbildungen R1a–R5 und – wenn die Daten vorliegen – die Kontrollzahlen
der Prüfung durch Wiederholung (Netzlauf ziel3_12h: 1063 Eskalationen) und ``roc_alle.csv``.
"""

import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import matplotlib.pyplot as plt

from analysis.belastungsmodell_app import model_ziel3 as mz
from analysis.roc_ziel3 import dashcore as dc
from analysis.roc_ziel3 import figures_roc as fr
from analysis.roc_ziel3 import journals as jn
from analysis.roc_ziel3 import metrics as mt
from analysis.roc_ziel3 import model_e1 as me1
from analysis.roc_ziel3 import model_e2 as me2
from analysis.roc_ziel3 import model_roc as mr
from analysis.roc_ziel3 import run_roc as rr

ROC_DIR = rr.ROC_DIR
NETZ = ROC_DIR / rr.NETZ_NAME
HAVE_NETZ = (NETZ / "alarms.jsonl").exists() and (rr.PC1_DIR / "decisions.jsonl").exists()
HAVE_CSV = (ROC_DIR / rr.CSV_OUT).exists() and (ROC_DIR / rr.TAB_JSON).exists()


# ─────────────────────────────────────────────────────────────────────────────
# Offline-Kern
# ─────────────────────────────────────────────────────────────────────────────

class DashcoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = dc.load_contract()
        cls.raw = dc.build_raw_clinical_db(cls.contract)
        cls.chs = [dc.Channel("P1", s, f"urn:uuid:{s}", f"{s}_alert_cond_x", c["loinc"], c["priority"], c["model"], "SDC Sim")
                   for s, c in zip(jn.SOURCES, cls.contract["sources"])]
        cls.rep = dc.AdaptivReplay(cls.chs, cls.raw)

    def scen(self, events, t_end=120.0, rep=None):
        rep = rep or self.rep
        r = rep.run([jn.AlarmEvent(t, "P1", s, f"urn:uuid:{s}", on) for t, s, on in events], 0.0, t_end)
        return r.rot.get("P1", []), r

    def test_core_is_loaded_from_dashboard_files_unmodified(self):
        core = dc.load_core()
        for f in core.files:
            self.assertEqual(f.parent, dc.DASH_ALARMS)
        self.assertEqual(core.saa.SmartAlertAggregator.__module__, f"{dc.PKG}.smartAlertAggregator")
        self.assertIsNot(core.saa, sys.modules.get("app.alarms.smartAlertAggregator"))

    def test_loading_opens_no_dashboard_file_logger(self):
        """In einem frischen Interpreter: Laden des Kerns legt keinen Datei-Logger des Dashboards an
        (tests/__init__.py selbst lädt das Dashboard-Paket, daher Unterprozess)."""
        code = ("import logging, sys; sys.path.insert(0, r'%s'); from analysis.roc_ziel3 import dashcore as dc; "
                "dc.load_core(); print(sum(isinstance(h, logging.FileHandler) for n in ('', 'sdc', 'sdc.consumer', "
                "'sdc.consumer.aggregator') for h in logging.getLogger(n).handlers), 'app' in sys.modules, "
                "'dashcore_alarms.smartAlertAggregator' in sys.modules)" % dc.REPO)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-500:])
        self.assertEqual(out.stdout.split(), ["0", "False", "True"])

    def test_profile_and_filter_from_contract(self):
        self.assertEqual(self.raw["filter_params"], {"horizon_T": 10.0, "alpha": 0.5, "delta_eval": 1.0})
        self.assertEqual(dc._first_profile(self.raw), {"sensitivity": 0.9, "false_alarm_rate": 0.00313})
        self.assertEqual(dc.horizon_T(dc.build_raw_clinical_db(self.contract, horizon_T=20.0)), 20.0)

    def test_channel_profiles(self):
        raw = dc.build_raw_clinical_db(self.contract, profiles={"ecg": (0.6, 0.02), "abp": (0.5, 0.03)})
        profs = dc.all_profiles(raw)
        self.assertEqual(profs["Sim ECG"], {"sensitivity": 0.6, "false_alarm_rate": 0.02})
        self.assertEqual(profs["Sim ABP"], {"sensitivity": 0.5, "false_alarm_rate": 0.03})
        self.assertEqual(profs["Sim Vent"], {"sensitivity": 0.9, "false_alarm_rate": 0.00313})

    def test_all_four_on_escalates_after_T_k_over_r(self):
        rot, r = self.scen([(0.0, s, True) for s in jn.SOURCES])
        self.assertEqual(len(rot), 1)
        self.assertAlmostEqual(rot[0][0], 5.5, places=6)       # T·k/r = 10·2/4 = 5 s + Gitterphase 0,5 s
        self.assertAlmostEqual(rot[0][1], 120.0, places=6)
        self.assertAlmostEqual(rot[0][2], 1.0, places=6)       # SDC_score

    def test_window_T_scales_the_delay(self):
        raw20 = dc.build_raw_clinical_db(self.contract, horizon_T=20.0)
        rep20 = dc.AdaptivReplay(self.chs, raw20)
        rot, _ = self.scen([(0.0, s, True) for s in jn.SOURCES], rep=rep20)
        self.assertEqual(len(rot), 1)
        self.assertAlmostEqual(rot[0][0], 10.5, places=6)      # T·k/r = 20·2/4 = 10 s + 0,5 s
        self.assertEqual(self.rep.core.saa.SmartAlertAggregator.__init__.__defaults__, (None, True))

    def test_two_high_priority_escalate_at_T(self):
        rot, _ = self.scen([(0.0, "ecg", True), (0.0, "vent", True)])
        self.assertEqual(len(rot), 1)
        self.assertAlmostEqual(rot[0][0], 10.5, places=6)      # k = 2 = r → T

    def test_single_source_never_escalates(self):
        rot, _ = self.scen([(0.0, "ecg", True), (60.0, "ecg", False)])
        self.assertEqual(rot, [])

    def test_medium_plus_low_melt(self):
        rot, _ = self.scen([(0.0, "spo2", True), (0.0, "abp", True), (100.0, "spo2", False), (100.0, "abp", False)])
        self.assertEqual(len(rot), 1)
        self.assertAlmostEqual(rot[0][0], 18.5, places=6)      # Schmelzen: T + T/sdc·ln(k/r) = 10 + 20·ln 1,5 ≈ 18,1 → Gitter 18,5
        self.assertAlmostEqual(rot[0][1], 100.0, places=6)

    def test_short_blips_do_not_escalate(self):
        rot, _ = self.scen([(0.0, "ecg", True), (1.0, "vent", True), (3.0, "ecg", False), (4.0, "vent", False)])
        self.assertEqual(rot, [])

    def test_time_is_restored_after_replay(self):
        import time as _time
        core = dc.load_core()
        self.scen([(0.0, "ecg", True)], 5.0)
        self.assertIs(core.saa.time, _time)
        self.assertIs(core.aaa.time, _time)


# ─────────────────────────────────────────────────────────────────────────────
# Kennzahlen
# ─────────────────────────────────────────────────────────────────────────────

def _truth(window=(0.0, 1000.0), crises=((100.0, 130.0), (500.0, 520.0))):
    tr = mt.PatientTruth(patient="P", window=window, crises=list(crises), open_crises=[],
                         stable=mt.complement(mt.pad(crises, 10.0, 10.0), *window), crossed=[])
    return {"P": tr}


class IntervalTests(unittest.TestCase):
    def test_union_intersect_complement(self):
        self.assertEqual(mt.union([(0, 1), (1, 2), (3, 4), (3.5, 5)]), [(0, 2), (3, 5)])
        self.assertEqual(mt.intersect([(0, 10)], [(5, 15), (20, 30)]), [(5, 10)])
        self.assertEqual(mt.complement([(2, 3), (5, 6)], 0, 10), [(0, 2), (3, 5), (6, 10)])
        self.assertAlmostEqual(mt.total(mt.pad([(10, 20)], 10, 10)), 30.0)

    def test_k_of_n_latch_and_plain(self):
        a, b, c = [(0, 10)], [(5, 20)], [(30, 40)]
        self.assertEqual(mt.k_of_n([a, b, c], 2, latch=False), [(5, 10)])
        self.assertEqual(mt.k_of_n([a, b, c], 2, latch=True), [(5, 20)])
        self.assertEqual(mt.k_of_n([a, b, c], 1, latch=True), [(0, 20), (30, 40)])
        self.assertEqual(mt.k_of_n([[(0, 5)], [(5, 8)]], 1, latch=True), [(0, 5), (5, 8)])

    def test_rate_time_shares_and_counts(self):
        truth = _truth()
        rot = {"P": [(110.0, 120.0), (300.0, 310.0), (515.0, 540.0)]}
        r = mt.rate(rot, truth, bed_hours=1000.0 / 3600.0)
        self.assertAlmostEqual(r.t_crisis, 50.0)
        self.assertAlmostEqual(r.t_in_crisis, 15.0)
        self.assertAlmostEqual(r.tpr, 0.3)
        self.assertAlmostEqual(r.t_stable, 1000.0 - 50.0 - 40.0)
        self.assertAlmostEqual(r.t_in_stable, 20.0)              # (300–310) und der Hals (530–540)
        self.assertEqual((r.n_crises, r.n_detected, r.n_detected_strict, r.n_events, r.n_false), (2, 2, 2, 3, 1))
        self.assertAlmostEqual(r.false_per_bed_h, 3.6)
        self.assertAlmostEqual(r.d_false, 10.0)
        # Variante (ii): nur die falsche Episode (300–310) → 10 s; Hals-Anteil = 1 − 10/20
        self.assertAlmostEqual(r.fpr_ii, 10.0 / 910.0)
        self.assertAlmostEqual(r.tail_share, 0.5)
        self.assertEqual(r.delays, (10.0, 15.0))
        self.assertAlmostEqual(r.delay_median, 15.0)

    def test_detection_window_end_plus_guard(self):
        truth = _truth()
        r = mt.rate({"P": [(135.0, 150.0)]}, truth, 1.0)      # beginnt 5 s nach Krisenende → mild erkannt, nicht streng
        self.assertEqual((r.n_detected, r.n_detected_strict, r.n_false), (1, 0, 0))
        self.assertAlmostEqual(r.late, 0.5)
        r = mt.rate({"P": [(141.0, 150.0)]}, truth, 1.0)      # 11 s nach Ende → nicht erkannt, falsch
        self.assertEqual((r.n_detected, r.n_false), (0, 1))
        r = mt.rate({"P": [(95.0, 105.0)]}, truth, 1.0)       # Rot schon vor Beginn → Verzögerung 0
        self.assertEqual(r.delays, (0.0,))

    def test_eta_touch(self):
        truth = _truth()
        self.assertAlmostEqual(mt.eta_touch({"P": [(90.0, 105.0)]}, truth), 0.5)
        self.assertAlmostEqual(mt.eta_touch({"P": [(131.0, 140.0)]}, truth), 0.0)

    def test_artefact_share_and_false_episodes(self):
        truth = _truth()
        src = {s: {"P": []} for s in jn.SOURCES}
        src["ecg"]["P"] = [(200.0, 210.0), (300.0, 310.0)]
        src["vent"]["P"] = [(205.0, 215.0)]
        src["spo2"]["P"] = [(205.0, 215.0)]
        src["abp"]["P"] = [(205.0, 215.0)]
        a = mt.artefact_share(src, truth)
        self.assertEqual(a.n_episodes, 2)
        self.assertEqual(a.by_n_sources, {4: 1, 1: 1})
        self.assertAlmostEqual(a.c_art_all, 40.0 / 50.0)
        eps = sorted(mt.false_episodes(src, truth), key=lambda e: e.a)
        self.assertEqual([(e.m, e.m_max, e.L) for e in eps], [(4, 4, 15.0), (1, 1, 10.0)])
        self.assertEqual(eps[0].L_ge, (15.0, 10.0, 10.0, 5.0))    # ≥1: 15 s, ≥2 und ≥3: 10 s (205–215), ≥4: 5 s (205–210)
        self.assertAlmostEqual(eps[0].t_sources, 40.0)

    def test_crisis_entries(self):
        truth = _truth()
        src = {s: {"P": []} for s in jn.SOURCES}
        src["ecg"]["P"] = [(95.0, 120.0)]          # schon aktiv bei Beginn → u = 0, Hals −10
        src["vent"]["P"] = [(112.0, 140.0)]        # Einstieg 12 s nach Beginn, Hals +10
        src["spo2"]["P"] = [(505.0, 515.0)]        # zweite Krise
        ents = mt.crisis_entries(src, truth)
        self.assertEqual(len(ents), 2)
        c1, c2 = ents
        self.assertEqual(c1.participates, {"ecg": True, "vent": True, "spo2": False, "abp": False})
        self.assertAlmostEqual(c1.u["ecg"], 0.0)
        self.assertAlmostEqual(c1.u["vent"], 12.0)
        self.assertAlmostEqual(c1.tail["vent"], 10.0)
        self.assertEqual(c2.participates["spo2"], True)
        inp = me1.e1_inputs(ents, 1.0)
        self.assertAlmostEqual(inp.eta["ecg"], 0.5)
        self.assertEqual(inp.delays["vent"], (12.0,))
        self.assertAlmostEqual(inp.d_true, 25.0)

    def test_auc_and_partial(self):
        self.assertAlmostEqual(mt.auc([0.5], [0.5]), 0.5)
        self.assertAlmostEqual(mt.auc([0.0, 1.0], [1.0, 1.0]), 1.0)
        self.assertAlmostEqual(mt.partial_auc([0.03, 0.5], [0.6, 0.9], 0.03), 0.3)
        self.assertTrue(math.isnan(mt.partial_auc([0.01], [0.5], 0.03)))

    def test_interpolation(self):
        v, inside = mt.tpr_at([0.01, 0.03], [0.4, 0.8], 0.02)
        self.assertAlmostEqual(v, 0.6)
        self.assertTrue(inside)
        v, inside = mt.tpr_at([0.01, 0.03], [0.4, 0.8], 0.005)
        self.assertAlmostEqual(v, 0.2)
        self.assertFalse(inside)
        v, inside = mt.y_at_logx([1.0, 100.0], [0.0, 1.0], 10.0)
        self.assertAlmostEqual(v, 0.5)
        self.assertTrue(math.isnan(mt.y_at_logx([1.0, 100.0], [0.0, 1.0], 0.5)[0]))

    def test_loglog_slope(self):
        x = [0.001, 0.003, 0.01, 0.03, 0.1]
        s, n = mt.loglog_slope(x, [v ** 2 for v in x])
        self.assertAlmostEqual(s, 2.0, places=9)
        self.assertEqual(n, 5)
        s, n = mt.loglog_slope(x, [v ** 2 for v in x], 0.01)
        self.assertEqual(n, 3)
        s, n = mt.loglog_slope([None, 0.01], [0.1, None])
        self.assertEqual(n, 0)


# ─────────────────────────────────────────────────────────────────────────────
# Theorie: Formel (a), Modell (b), E1, E2
# ─────────────────────────────────────────────────────────────────────────────

class ModelTests(unittest.TestCase):
    def test_binomial(self):
        self.assertAlmostEqual(mr.binom_k_of_n(0.5, 2), 11 / 16)
        self.assertAlmostEqual(mr.binom_k_of_n(0.5, 1), 15 / 16)
        self.assertAlmostEqual(mr.binom_k_of_n(0.5, 4), 1 / 16)
        self.assertAlmostEqual(mr.binom_k_of_n(0.0, 2), 0.0)
        self.assertAlmostEqual(mr.binom_k_of_n(1.0, 4), 1.0)

    def test_model_point_is_finite_and_ordered(self):
        ds = mr.DeviceSummary(h=1.0, tpr_mean=0.6, fpr_mean=0.026, mfpr=0.104, c_art=0.06, c_art_ge2=0.48,
                              d_false=8.3, d_true=30.2, pc=0.0109, n_per_source_day=290.0, fdr=0.9)
        pts = {rule: mr.model_point(ds, rule) for rule in mr.MODEL_RULES}
        for p in pts.values():
            for v in (p.tpr, p.fpr, p.detected, p.false_per_bed_h):
                self.assertTrue(math.isfinite(v))
        self.assertGreater(pts["r2"].fpr, pts["r3"].fpr)
        f2, t2 = mr.formula_point(ds, 2)
        self.assertAlmostEqual(t2, mr.binom_k_of_n(0.6, 2))
        self.assertAlmostEqual(pts["r2"].tpr, t2, places=9)


class E1Tests(unittest.TestCase):
    """E1 reduziert sich bei u_j ≡ 0 und η_j = TPR_j auf model_ziel3 (tpr_inf / tpr_T)."""

    @classmethod
    def setUpClass(cls):
        cls.tpr = 0.6
        cls.inp0 = me1.E1Inputs(h=1.0, eta={s: cls.tpr for s in jn.SOURCES}, delays={s: (0.0,) for s in jn.SOURCES},
                                d_true=30.2)
        cls.p = mz.Params(tpr=cls.tpr, d_true=30.2, P=(3, 3, 2, 1), T=10.0)

    def test_k_of_n_reduces_to_tpr_inf(self):
        for k in (2, 3):
            r = me1.e1_k_of_n(self.inp0, k, n=200_000)
            ref = mz.evaluate(self.p, f"r{k}")
            self.assertAlmostEqual(r.share, ref.tpr_inf, delta=0.004)
            self.assertAlmostEqual(r.caught, ref.tpr_T, delta=0.004)

    def test_adaptiv_reduces_to_tpr_T(self):
        r = me1.e1_adaptiv(self.inp0, 10.0, n=200_000, dt=0.25)
        ref = mz.evaluate(self.p, "adaptiv")
        self.assertAlmostEqual(r.caught, ref.tpr_T, delta=0.005)
        self.assertLess(r.share, ref.tpr_inf)                  # Zeitanteil liegt unter der Trefferwahrscheinlichkeit
        self.assertGreater(r.t_esc_median, 0.0)

    def test_adaptiv_delay_and_window(self):
        inp = me1.E1Inputs(h=1.0, eta={s: 1.0 for s in jn.SOURCES}, delays={s: (0.0,) for s in jn.SOURCES}, d_true=60.0)
        r10 = me1.e1_adaptiv(inp, 10.0, n=20_000, dt=0.25)
        r20 = me1.e1_adaptiv(inp, 20.0, n=20_000, dt=0.25)
        self.assertAlmostEqual(r10.t_esc_median, 5.0, delta=0.3)     # alle 4 sofort: T·k/r = 5 s
        self.assertAlmostEqual(r20.t_esc_median, 10.0, delta=0.3)
        self.assertGreater(r10.share, r20.share)
        inp_u = me1.E1Inputs(h=1.0, eta={s: 1.0 for s in jn.SOURCES}, delays={s: (8.0,) for s in jn.SOURCES}, d_true=60.0)
        r_u = me1.e1_adaptiv(inp_u, 10.0, n=20_000, dt=0.25)
        self.assertAlmostEqual(r_u.t_esc_median, 13.0, delta=0.3)    # versetzter Einstieg verschiebt um u


class E2Tests(unittest.TestCase):
    def test_reduction_to_old_formula(self):
        """Nur m = 1 und m = 4: E2 = bisherige Formel mit c_art = q_4 (Rate der 4er-Episoden = c_art·FPR/μ)."""
        p = mz.Params(d_false=8.3, P=(3, 3, 2, 1), c_art=0.06, T=10.0, mfpr_direct=0.104, pc_direct=0.0109)
        cal = mz.calibrate(p)
        for rule in ("r2", "r3", "adaptiv"):
            ref = mz.evaluate(p, rule, cal).false_day
            red = me2.e2_from_shares(cal.FPR, 8.3, 0.0109, 1 - 0.06, 0.06, 0.06 * cal.FPR / 8.3, rule, 10.0)
            self.assertAlmostEqual(red, ref, places=9)

    def test_episode_escalation_rule(self):
        p = mz.Params(P=(3, 3, 2, 1), T=10.0)
        ep = lambda srcs, L, L_ge: mt.FalseEpisode("P", 0.0, L, tuple(srcs), len(srcs), L, L_ge)   # noqa: E731
        # zwei hohe Prioritäten 12 s gleichzeitig aktiv → t_esc = T·k/r = 10 s → eskaliert
        self.assertTrue(me2.episode_escalates(ep(("ecg", "vent"), 12.0, (12.0, 12.0, 0.0, 0.0)), p)[0])
        # dieselben nur 8 s gleichzeitig → nein
        self.assertFalse(me2.episode_escalates(ep(("ecg", "vent"), 20.0, (20.0, 8.0, 0.0, 0.0)), p)[0])
        # Einzelalarm → nie
        self.assertFalse(me2.episode_escalates(ep(("ecg",), 60.0, (60.0, 0.0, 0.0, 0.0)), p)[0])
        # mittel + niedrig: Schmelzen bei ≈ 18,1 s → 20 s gleichzeitig eskaliert, 15 s nicht
        self.assertTrue(me2.episode_escalates(ep(("spo2", "abp"), 20.0, (20.0, 20.0, 0.0, 0.0)), p)[0])
        self.assertFalse(me2.episode_escalates(ep(("spo2", "abp"), 15.0, (15.0, 15.0, 0.0, 0.0)), p)[0])

    def test_e2_k_of_n_counts_episodes(self):
        eps = (mt.FalseEpisode("P", 0.0, 10.0, ("ecg",), 1, 10.0, (10.0, 0, 0, 0)),
               mt.FalseEpisode("P", 100.0, 120.0, ("ecg", "vent"), 2, 30.0, (20.0, 10.0, 0, 0)),
               mt.FalseEpisode("P", 200.0, 230.0, ("ecg", "vent", "spo2"), 2, 60.0, (30.0, 20.0, 0.0, 0)))
        inp = me2.E2Inputs(h=1.0, episodes=eps, bed_hours=10.0, t_stable=36000.0, pc=0.01, fpr_mean=0.02)
        self.assertAlmostEqual(inp.share(1), 10.0 / 100.0)
        self.assertAlmostEqual(inp.rate(2), 0.1)
        self.assertAlmostEqual(inp.mean_L(3), 30.0)
        r2 = me2.e2_k_of_n(inp, 2)
        self.assertAlmostEqual(r2.part_common, 0.2)                # zwei Episoden mit m ≥ 2
        r3 = me2.e2_k_of_n(inp, 3)
        self.assertAlmostEqual(r3.part_common, 0.1)                # eine mit m ≥ 3 …
        self.assertAlmostEqual(me2.e2_k_of_n(inp, 3, by_mmax=True).part_common, 0.0)   # … aber nie 3 gleichzeitig
        self.assertGreaterEqual(r2.false_per_bed_h, r2.part_common)


# ─────────────────────────────────────────────────────────────────────────────
# Abbildungen (synthetische Zeilen)
# ─────────────────────────────────────────────────────────────────────────────

def _synthetic_rows():
    rows = []
    hs = [0.5, 1.0, 2.0]
    for i, h in enumerate(hs):
        f = 0.05 / (i + 1)
        for dev in fr.DEVICES:
            rows.append(dict(h=h, art="Geraet", name=dev, tpr=0.6 - 0.1 * i, fpr=f, fpr_ii=f, fpr_j_mittel=f, T_s=None))
        for j, name in enumerate(fr.RULES):
            Ts = (10.0, 20.0, 30.0) if name == "adaptiv" else (None,)
            for T in Ts:
                rows.append(dict(h=h, art="Regel", name=name, tpr=0.8 - 0.1 * j - 0.1 * i, fpr=f ** (1 + j / 4),
                                 fpr_ii=0.9 * f ** (1 + j / 4), eta_oder_erkannt=0.9 - 0.1 * i, erkannt_streng=0.85 - 0.1 * i,
                                 falsch_je_bett_h=5.0 / (i + 1) / (j + 1), fpr_j_mittel=f, T_s=T, verz_median_s=5.0, verz_p90_s=9.0,
                                 hvl_anteil=0.05, n_ereignisse=100, n_falsch=50))
        for name in ("2-aus-4", "3-aus-4"):
            rows.append(dict(h=h, art="Formel", name=name, tpr=0.7 - 0.1 * i, fpr=f ** 2, fpr_ii=f ** 2, fpr_j_mittel=f, T_s=None))
        for name in ("2-aus-4", "3-aus-4", "adaptiv"):
            rows.append(dict(h=h, art="Modell", name=name, tpr=0.75 - 0.1 * i, fpr=f ** 1.5, fpr_ii=f ** 1.5, eta_oder_erkannt=0.8,
                             falsch_je_bett_h=3.0 / (i + 1), fpr_j_mittel=f, T_s=10.0 if name == "adaptiv" else None))
            rows.append(dict(h=h, art="ModellE", name=name, tpr=0.72 - 0.1 * i, fpr=f ** 1.4, fpr_ii=f ** 1.4, eta_oder_erkannt=0.82,
                             falsch_je_bett_h=3.2 / (i + 1), fpr_j_mittel=f, T_s=10.0 if name == "adaptiv" else None))
    rows.append(dict(h=1.0, art="Netz", name="adaptiv", quelle="Netzlauf ziel3_12h", tpr=0.5, fpr=0.007, fpr_ii=0.0066,
                     eta_oder_erkannt=0.97, falsch_je_bett_h=3.1, T_s=10.0))
    return rows


class FigureTests(unittest.TestCase):
    def test_curve_selects_by_T(self):
        rows = _synthetic_rows()
        self.assertEqual(len(fr.curve(rows, "Regel", "adaptiv", 20.0)), 3)
        self.assertEqual(len(fr.curve(rows, "Regel", "2-aus-4", 20.0)), 3)      # ohne T_s gilt für jedes T
        self.assertEqual(fr.available_T(rows), [10.0, 20.0, 30.0])

    def test_figures_build_and_export(self):
        rows = _synthetic_rows()
        tb = rr.tables(rows, None)
        self.assertIn("adaptiv", tb["auc"])
        self.assertIn("adaptiv T=20", tb["auc"])
        self.assertIn("2-aus-4 [Modell E1+E2]", tb["auc"])
        self.assertEqual(sorted(tb["fenster_T"]), ["10", "20", "30"])
        for variant in ("ii", "i"):
            for x_mode in ("beide", "log", "linear"):
                fig = fr.fig_R1a(rows, tb, variant, x_mode)
                self.assertEqual(len(fig.axes), 2 if x_mode == "beide" else 1)
                plt.close(fig)
        for log_x in (False, True):
            fig = fr.fig_R1b(rows, tb, log_x=log_x)
            self.assertEqual(len(fig.axes), 3)
            self.assertEqual(fig.axes[0].get_xscale(), "log" if log_x else "linear")
            self.assertTrue(any(t.get_text() == fr.BINOM_LABEL for t in fig.legends[0].get_texts()))
            self.assertEqual(fig._suptitle.get_text(), "Theorie gegen Messung (Gesamtsystem nach Alarmfusion)")
            plt.close(fig)
        fig = fr.fig_R1a(rows, tb)
        self.assertFalse(any("Formel" in t.get_text() or "Binomial" in t.get_text() for t in fig.legends[0].get_texts()))
        self.assertTrue(any(fr.FOOTNOTE_ADAPTIV in t.get_text() for t in fig.texts))
        plt.close(fig)
        fig = fr.fig_R1b_stueck(rows, tb)
        self.assertEqual(len(fig.axes), 3)
        self.assertEqual(fig.axes[0].get_xlim(), (0.0, 10.0))
        self.assertTrue(any("Binomialformel der Teilnahme" in t.get_text() for t in fig.legends[0].get_texts()))
        plt.close(fig)
        self.assertAlmostEqual(fr.binom_participation([0.5, 0.5, 0.5, 0.5], 2), 11 / 16)
        self.assertAlmostEqual(fr.binom_participation([1.0, 1.0, 0.0, 0.0], 2), 1.0)
        self.assertAlmostEqual(fr.binom_participation([1.0, 1.0, 0.0, 0.0], 3), 0.0)
        plt.close(fr.fig_R2(rows, tb, "linear"))
        fig = fr.fig_R2(rows, None)                        # ohne Episodenteil → kein Modell, Ausschnitt rechts
        self.assertEqual(len(fig.axes), 2)
        self.assertEqual(fig.axes[1].get_xlim(), fr.ZOOM_X)
        self.assertEqual(fig.axes[1].get_ylim(), fr.ZOOM_Y)
        self.assertFalse(any("Modell" in t.get_text() for t in fig.legends[0].get_texts()))
        plt.close(fig)
        tb_e2 = dict(e2={f"{h:g}": {"ergebnisse": {k: {"episoden": 2.0 / (i + 1)} for k in ("2-aus-4", "3-aus-4", "adaptiv_T10")}}
                         for i, h in enumerate((0.5, 1.0, 2.0))})
        x, y = fr.model_e_count_curve(rows, tb_e2, "adaptiv", 10.0)
        self.assertEqual(len(x), 3)
        self.assertAlmostEqual(float(x.max()), 2.0)
        fig = fr.fig_R2(rows, tb_e2)
        self.assertTrue(any(t.get_text() == fr.MODEL_E_LABEL for t in fig.legends[0].get_texts()))
        plt.close(fig)
        plt.close(fr.fig_R3(rows, tb))
        plt.close(fr.fig_R4a(rows, tb, "ii", (10.0, 20.0, 30.0)))
        plt.close(fr.fig_R4b(rows, (10.0, 20.0, 30.0), "log"))
        plt.close(fr.fig_R5(rows, 20.0))
        with tempfile.TemporaryDirectory() as d:
            written = fr.export_all(rows, tb, Path(d))
            names = sorted(p.name for p in written)
            self.assertIn("R1a_ROC_Geraetegrenzwerte.png", names)
            self.assertIn("R1b_Theorie_Messung_log.png", names)
            self.assertIn("R1b_Theorie_Messung_Stueck.png", names)
            self.assertIn("R4a_Fenster_T_Zeit.svg", names)
            self.assertIn("R5_Fehlerbilanz_Grenzwerte_T30.png", names)
            self.assertEqual(len(written), 2 * (8 + 3))

    def test_adaptiv_ahead_spans(self):
        rows = []
        for h, x, ya, y2, y3 in ((2.0, 1.0, 90.0, 95.0, 96.0), (1.0, 3.0, 99.0, 97.0, 96.5), (0.5, 6.0, 99.5, 99.8, 99.0)):
            rows.append(dict(h=h, art="Regel", name="adaptiv", T_s=10.0, falsch_je_bett_h=x, eta_oder_erkannt=ya / 100))
            rows.append(dict(h=h, art="Regel", name="2-aus-4", T_s=None, falsch_je_bett_h=x, eta_oder_erkannt=y2 / 100))
            rows.append(dict(h=h, art="Regel", name="3-aus-4", T_s=None, falsch_je_bett_h=x, eta_oder_erkannt=y3 / 100))
        spans = fr.adaptiv_ahead(rows, 10.0)
        self.assertEqual(len(spans), 1)
        lo, hi = spans[0]
        self.assertAlmostEqual(lo, 2.43, delta=0.02)       # adaptiv überholt 2-aus-4 bei x = 1 + 5/3,5 (lineare Interpolation)
        self.assertAlmostEqual(hi, 5.61, delta=0.02)       # … und fällt bei x = 3 + 2/0,7667 hinter 2-aus-4 zurück
        self.assertEqual(fr.adaptiv_ahead([], 10.0), [])

    def test_axis_texts(self):
        self.assertEqual(fr.Y_TIME, "Anteil der Krisenzeit mit Eskalation (Rot)")
        self.assertEqual(fr.X_TIME, "Anteil der krisenfreien Zeit mit Eskalation (Rot)")
        self.assertIn("Gesamtsystem nach Alarmfusion", fr.TITLE_TIME)
        self.assertEqual(fr.DEV_LABEL, "Einzelgeräte (Anteil der Zeit mit Gerätealarm)")
        self.assertEqual(fr._de_pos(0.0001), "0,0001")
        self.assertEqual(fr._de_pos(0.3), "0,3")


# ─────────────────────────────────────────────────────────────────────────────
# Daten (nur wenn vorhanden)
# ─────────────────────────────────────────────────────────────────────────────

@unittest.skipUnless(HAVE_NETZ, "Netzjournale / decisions.jsonl fehlen")
class ReplayCheckTests(unittest.TestCase):
    """Prüfung durch Wiederholung: 12-h-Netzlauf, Dashboard 1063 Rot."""

    @classmethod
    def setUpClass(cls):
        cls.j = jn.load_journals(NETZ)
        cls.contract = dc.load_contract()
        cls.raw = dc.build_raw_clinical_db(cls.contract)
        cls.res = dc.replay_journals(cls.j, cls.contract, cls.raw, grid_phase=0.5)
        cls.real = rr.real_rot(rr.PC1_DIR, rr.clock_offset_fn(rr.PC1_DIR))

    def test_journal_counts(self):
        self.assertEqual(len(self.j.patients), 20)
        self.assertEqual(sum(1 for e in self.j.alarms if e.on), 12061)
        self.assertEqual(sum(c.closed for c in self.j.crises), 311)
        self.assertAlmostEqual(self.j.hours, 12.0, places=2)

    def test_replay_matches_dashboard_within_two_percent(self):
        n_real = sum(len(v) for v in self.real.values())
        self.assertEqual(n_real, 1063)
        self.assertLessEqual(abs(self.res.n_rot - n_real) / n_real, 0.02)
        self.assertAlmostEqual(self.res.n_rot, 1058, delta=3)
        m = rr.match_starts(self.res.rot, self.real)
        self.assertGreaterEqual(m["matched"], 1040)
        self.assertLess(abs(m["diff_median"]), 0.5)


@unittest.skipUnless(HAVE_CSV, "roc_alle.csv / roc_tabellen.json fehlen (python -m analysis.roc_ziel3.run_roc)")
class CsvTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.orig = rr.read_csv(ROC_DIR / rr.CSV_IN)
        cls.alle = rr.read_csv(ROC_DIR / rr.CSV_OUT)
        cls.rows = rr.load_rows(ROC_DIR / rr.CSV_OUT)
        cls.tb = json.loads((ROC_DIR / rr.TAB_JSON).read_text(encoding="utf-8"))

    def test_original_rows_unchanged(self):
        self.assertGreater(len(self.alle), len(self.orig))
        for o, a in zip(self.orig, self.alle):
            for c in rr.ORIG_COLUMNS:
                self.assertEqual(o[c], a[c], f"{c} in Zeile h={o['h']} {o['name']}")

    def test_adaptiv_rows_for_every_h_and_T(self):
        hs = sorted({float(r["h"]) for r in self.orig})
        self.assertEqual(len(hs), 15)
        for T in fr.available_T(self.rows):
            ad = {r["h"] for r in fr.curve(self.rows, "Regel", "adaptiv", T)}
            self.assertEqual(ad, set(hs), f"T = {T}")

    def test_operating_point_h1(self):
        wp = self.tb["arbeitspunkt_h1"]
        self.assertEqual(wp["2-aus-4"]["n_ereignisse"], 1714)
        self.assertEqual(wp["2-aus-4"]["n_falsch"], 1397)
        self.assertAlmostEqual(wp["2-aus-4"]["fpr"], 0.021888, places=5)
        self.assertAlmostEqual(wp["2-aus-4"]["fpr_ii"], 0.02165, delta=0.0002)
        a = wp["adaptiv T=10"]
        self.assertAlmostEqual(a["n_ereignisse"], 1060, delta=5)
        self.assertAlmostEqual(a["falsch_je_bett_h"], 3.13, delta=0.05)
        self.assertAlmostEqual(a["erkannt"], 0.9775, delta=0.005)
        self.assertAlmostEqual(a["erkannt_streng"], 0.913, delta=0.01)
        self.assertAlmostEqual(a["tpr"], 0.477, delta=0.005)
        self.assertAlmostEqual(a["fpr"], 0.00674, delta=0.0002)
        self.assertAlmostEqual(a["fpr_ii"], 0.00656, delta=0.0002)

    def test_auc_matches_pc2(self):
        for r in self.orig:
            if r["art"] == "Regel" and float(r["h"]) == 1.0:
                self.assertAlmostEqual(self.tb["auc"][r["name"]]["auc_i"], float(r["auc"]), places=5)

    def test_replay_check_recorded(self):
        pr = self.tb["pruefung"]
        self.assertEqual(pr["zuordnung"]["n_real"], 1063)
        self.assertLessEqual(abs(pr["zuordnung"]["rel_count_diff"]), 0.02)

    def test_window_T_table(self):
        ft = self.tb.get("fenster_T", {})
        if len(ft) < 2:
            self.skipTest("nur ein T im Sweep")
        wp = self.tb["arbeitspunkt_h1"]
        self.assertGreater(wp["adaptiv T=10"]["falsch_je_bett_h"], wp["adaptiv T=20"]["falsch_je_bett_h"])
        self.assertGreater(wp["adaptiv T=10"]["erkannt"], wp["adaptiv T=30"]["erkannt"])

    def test_e1_e2_recorded(self):
        e1 = self.tb.get("e1", {}).get("1")
        e2 = self.tb.get("e2", {}).get("1")
        if not (e1 and e2):
            self.skipTest("E1/E2 nicht im Sweep")
        self.assertAlmostEqual(e1["ergebnisse"]["2-aus-4"]["y"], 0.694, delta=0.01)
        self.assertAlmostEqual(e1["ergebnisse"]["adaptiv_T10"]["erkannt"], 0.91, delta=0.02)
        self.assertAlmostEqual(sum(e2["eingaben"]["anteil"][k] for k in e2["eingaben"]["anteil"]), 1.0, places=6)


if __name__ == "__main__":
    unittest.main()


# ─────────────────────────────────────────────────────────────────────────────
# Hülle, Latenz, E1 mit Nachlauf (η = 0,7-Erweiterung)
# ─────────────────────────────────────────────────────────────────────────────

from analysis.roc_ziel3 import hull as hl  # noqa: E402


class HullTests(unittest.TestCase):
    def test_upper_hull_toy_points(self):
        h = hl.upper_hull([(1, 0.5), (2, 0.9), (3, 0.7), (4, 0.95), (0.5, 0.2)])
        self.assertEqual(h, [(0.0, 0.0), (1.0, 0.5), (2.0, 0.9), (4.0, 0.95)])
        self.assertAlmostEqual(hl.hull_value(h, 2.5), 0.9125)
        self.assertAlmostEqual(hl.hull_value(h, 2.0), 0.9)                # Punkt auf der Hülle → Δ = 0
        self.assertTrue(math.isnan(hl.hull_value(h, 5.0)))
        self.assertAlmostEqual(hl.chord_value((1, 0.5), (3, 0.7), 2.0), 0.6)
        self.assertAlmostEqual(hl.chord_value((1, 0.5), (3, 0.7), 4.0), 0.8)   # verlängert

    def test_analyse_delta_zero_on_hull(self):
        """Adaptiv-Punkt identisch mit einem 2-aus-4-Punkt → Δ = 0 (und Δ_Sehne = 0), Bootstrap-Intervall enthält 0."""
        patients = [f"P{i:02d}" for i in range(20)]
        hs = [0.5, 1.0, 2.0]

        def stats(n_false, n_det, n_crises=5.0, t_false=100.0):
            return {p: [600.0, 300.0, 3000.0, 150.0, t_false, n_crises, n_det, n_det, n_false, n_false, 1.0] for p in patients}
        data = {
            "1-aus-4": {"0.5": stats(40, 5), "1": stats(30, 5), "2": stats(20, 5)},
            "2-aus-4": {"0.5": stats(10, 5), "1": stats(6, 4.9), "2": stats(3, 4.5)},
            "3-aus-4": {"0.5": stats(4, 4.6), "1": stats(2, 4.4), "2": stats(1, 4.0)},
            "4-aus-4": {"0.5": stats(1, 3.5), "1": stats(0.5, 3.0), "2": stats(0.2, 2.0)},
            "adaptiv": {"0.5": stats(10, 5), "1": stats(6, 4.9), "2": stats(3, 4.5)},
        }
        res = hl.analyse(data, hs, h_targets=(1.0,), n_boot=50, seed=1)
        row = next(r for r in res["stueck"].rows if r["h"] == 1.0)
        self.assertAlmostEqual(row["delta_pp"], 0.0, places=9)
        self.assertAlmostEqual(row["delta_sehne_pp"], 0.0, places=9)
        self.assertLessEqual(row["delta_ci"][0], 1e-9)
        self.assertGreaterEqual(row["delta_ci"][1], -1e-9)
        self.assertTrue(res["stueck"].hull[0] == (0.0, 0.0))
        self.assertIn("nach Stück", hl.summary_sentence(res["stueck"], "η = 0,9"))


class LatencyTests(unittest.TestCase):
    def test_latencies_two_reference_points(self):
        truth = _truth()
        src = {s: {"P": []} for s in jn.SOURCES}
        src["ecg"]["P"] = [(106.0, 125.0)]          # erste Überschreitung 6 s nach Krisenbeginn
        src["vent"]["P"] = [(110.0, 125.0)]
        ents = mt.crisis_entries(src, truth)
        lat = mt.latencies({"P": [(112.0, 130.0)]}, truth, ents)
        self.assertEqual(lat.from_start, (12.0,))
        self.assertEqual(lat.from_cross, (6.0,))
        self.assertEqual(lat.n, 1)
        lat0 = mt.latencies({"P": [(95.0, 130.0)]}, truth, ents)      # Rot vor Beginn → 0 / 0
        self.assertEqual((lat0.from_start, lat0.from_cross), ((0.0,), (0.0,)))

    def test_per_patient_sums_match_total(self):
        truth = _truth()
        truth["Q"] = mt.PatientTruth(patient="Q", window=(0.0, 1000.0), crises=[(200.0, 240.0)], open_crises=[],
                                     stable=mt.complement(mt.pad([(200.0, 240.0)], 10.0, 10.0), 0.0, 1000.0), crossed=[])
        rot = {"P": [(110.0, 120.0), (300.0, 310.0)], "Q": [(230.0, 260.0), (600.0, 605.0)]}
        per = mt.per_patient(rot, truth, 1000.0 / 3600.0)
        total = mt.rate(rot, truth, 2 * 1000.0 / 3600.0)
        F = {name: i for i, name in enumerate(mt.PATIENT_FIELDS)}
        self.assertAlmostEqual(sum(v[F["t_in_crisis"]] for v in per.values()), total.t_in_crisis)
        self.assertAlmostEqual(sum(v[F["t_false"]] for v in per.values()), total.t_false_total)
        self.assertEqual(sum(v[F["n_false"]] for v in per.values()), total.n_false)
        self.assertEqual(sum(v[F["n_det"]] for v in per.values()), total.n_detected)


class E1TailTests(unittest.TestCase):
    def test_mild_at_least_strict_and_tails_matter(self):
        inp = me1.E1Inputs(h=1.0, eta={s: 0.9 for s in jn.SOURCES}, delays={s: (10.0,) for s in jn.SOURCES}, d_true=30.2,
                           tails={s: (8.0,) for s in jn.SOURCES})
        r = me1.e1_adaptiv(inp, 10.0, n=20_000)
        self.assertGreaterEqual(r.caught_mild, r.caught)
        self.assertGreater(r.caught_mild - r.caught, 0.02)         # Nachlauf 8 s erlaubt Eskalation nach dem Ende
        inp0 = me1.E1Inputs(h=1.0, eta={s: 0.9 for s in jn.SOURCES}, delays={s: (10.0,) for s in jn.SOURCES}, d_true=30.2)
        r0 = me1.e1_adaptiv(inp0, 10.0, n=20_000)
        self.assertAlmostEqual(r0.caught_mild, r0.caught, delta=0.01)  # ohne Nachlauf: nach dem Ende keine Eskalation
        self.assertIn("v_median", rr.e1_summary(inp))
