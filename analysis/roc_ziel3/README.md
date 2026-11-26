# analysis/roc_ziel3 — goal 3: curves over the device thresholds

The fusion rule is fixed and the alarm thresholds of the four devices are scaled by a factor h
(h = 1.0 = the network runs). PC 2 delivers for every h the journals A (crises) and B (device
alarms) and the figures for the single devices and k-aus-4
(`experiments/ziel3_simulation/logs/ziel3_roc/`, `roc_kausn.csv`). This package recomputes the
rule *adaptiv* **with the unchanged filter core of the Dashboard** (also for other windows T and
channel profiles), adds the formula, the model and the model extensions E1/E2, and writes tables
and figures.

Most commands read the journals under `experiments/ziel3_simulation/logs/`, which are not part
of the repository (several gigabytes). Without them the package can be imported and its tests
run; tests that need the journals are skipped. The result files in `docs/reports/` and
`roc_dicht.json` in this folder are included.

```
.venv\Scripts\python -m analysis.roc_ziel3.run_roc                      # η = 0.9: check, sweep (T = 10, 20, 30), CSV, tables, figures
.venv\Scripts\python -m analysis.roc_ziel3.run_roc --roc-dir experiments/ziel3_simulation/logs/ziel3_roc_p07 ^
    --pc1-dir experiments/ziel3_simulation/logs/ziel3_4h_p07/pc1 --netz netz_ziel3_4h_p07 --suffix _p07 --eta 0,7 --T 10 ^
    --vergleich-dir experiments/ziel3_simulation/logs/ziel3_roc        # η = 0.7 (scenario of network run A) + comparison figure
.venv\Scripts\python -m analysis.roc_ziel3.run_roc --nur pruefung | sweep | tabellen | abbildungen  [--T 10 20 30] [--e1-n 100000]
.venv\Scripts\python -m analysis.roc_ziel3.werkstatt                          # all filter configurations, both η (about 80 min)
.venv\Scripts\python -m analysis.roc_ziel3.werkstatt --nur-deltas             # differences and bootstrap only
.venv\Scripts\python -m analysis.roc_ziel3.netzlauf_auswertung --netz-dir <pc2> --pc1-dir <pc1> --out docs/reports   # one network run from the journals (schema of PC 2)
.venv\Scripts\python -m analysis.roc_ziel3.kalibrierung --stufe episoden      # event-based figures (escalated false-alarm episodes) per h
.venv\Scripts\python -m analysis.roc_ziel3.kalibrierung --stufe klassisch     # P0 curves of the prototype (h 0.8 / 1.0 / 1.2, both η)
.venv\Scripts\python -m analysis.roc_ziel3.kalibrierung --stufe gitter --T-werte 5 --teil _T5   # grid T × α × δ_min × P0 (one process per T, about 30 min)
.venv\Scripts\python -m analysis.roc_ziel3.kalibrierung --stufe auswertung    # merge the grid, choose "kalibriert" by the fixed criterion
.venv\Scripts\python -m analysis.roc_ziel3.kalibrierung --stufe pruefung      # kalibriert: η 0.7, curves over h, network runs, bootstrap (about 25 min)
.venv\Scripts\python -m analysis.roc_ziel3.kalibrierung --stufe netz          # network runs offline at h = 1 (prototype, kalibriert)
.venv\Scripts\python -m analysis.roc_ziel3.kalibrierung --stufe val           # validation sets (seeds 8 / 9) without readjustment
.venv\Scripts\python -m analysis.roc_ziel3.zeit_roc                           # time-based ROC per window T (5/8/10/15/20/30 s, both η)
.venv\Scripts\python -m unittest tests.test_roc_ziel3 tests.test_werkstatt tests.test_kalibrierung tests.test_zeit_roc tests.test_netzlauf_auswertung
```

Calculation:

| Module | Content |
|---|---|
| `journals.py` | read the journals A/B (`t_prov_abs` of the alarms, crises, ON intervals per source) |
| `dashcore.py` | load `Dashboard/app/alarms` as the package `dashcore_alarms` (files unchanged, without `app/__init__.py`, i.e. without Qt and without the file logger) and drive `SmartAlertAggregator` with model time: alarm ON → `check_alert_validity`, OFF → `clear_alarm`, grid Δ_eval → `evaluate_periodic(now)`, every 5 s → `_cleanup_stale_alarms()`; Rot = `is_escalated` in the `updateEnsemble` call. Window T through `horizon_T` of the database, channel profiles through `profiles` |
| `metrics.py` | TPR/FPR by time (stable = outside a crisis ± 10 s; x variant (i) all Rot time, (ii) false episodes only), recognised crises mild (Rot until end + 10 s) and strict (until the end), delay, false episodes, k-aus-N **with latch** (as on PC 2 and in the Dashboard), c_art, crisis entries (η_j, u_j), false-alarm episodes by size m, AUC / partial AUC, interpolation, log-log slope |
| `hull.py` | upper convex hull of all k-aus-4 points (all k, all h, with the origin) by count and by time; Δ = y_adaptiv − y_hull and Δ to the chord 2-aus-4 ↔ 3-aus-4 of the same h, 95 % intervals from a paired bootstrap over the patients (`roc_patienten.json`) |
| `model_roc.py` | (a) binomial formula with p = mean TPR or mean FPR; (b) `model_ziel3` with the device rates of the h |
| `model_e1.py` | E1 "staggered entry": Monte Carlo of the chapter 3 rule with the measured η_j and entry delays u_j, D = 15 s + Exp; k-aus-4 and adaptiv per T |
| `model_e2.py` | E2 "partly common artefacts": false-alarm episodes by size m (shares, rates, durations); chance coincidences of the single alarms after formula (G.7) plus escalating episodes |
| `run_roc.py` | sequence of the evaluation, `roc_alle.csv` (= `roc_kausn.csv` unchanged + columns + rows adaptiv per T / formula / model / extended model / network), `roc_tabellen.json` / `.md`, replay cache `adaptiv_replay/`, profile check |
| `werkstatt.py` | filter configurations (T ∈ {5, 10, 20, 30} s, α ∈ {0.3, 0.5, 0.7, 0.9}, δ_min ∈ {0.3, 0.5, 0.7, 1.0}, one parameter away from the base at a time) recomputed offline with the unchanged core; key figures per η and h, at h = 1 the differences to the chord and to the hull with 95 % intervals from 1000 paired bootstrap draws |
| `kalibrierung.py` | event-based ROC (false-alarm episode = union of overlapping false alarms of one patient; escalated when a false Rot intersects it); classical ROC of the filter at fixed h over P0; k-aus-4 as a polyline k = 1 … 4; choice of the parameter set "kalibriert" by a criterion fixed in advance (`KRITERIUM`), check without readjustment, paired patient bootstrap of the AUC |
| `netzlauf_auswertung.py` | evaluate a network run from the journals of PC 2 (`alarms.jsonl`, `truth.jsonl`, `run_config.json`) and `decisions.jsonl` of PC 1 in the schema of PC 2 → `docs/reports/results_<run>.json`, `messwerte_<run>.json` (runs A–D) |
| `zeit_roc.py` | time-based ROC per window T and the decomposition of the crisis time; contains its own figures |
| `roc_dicht.py` | refined grid of 35 values of h between 0.5 and 2.2 → `roc_dicht.json`. Its figure step imports scripts that are not part of the repository; the calculation step is independent of them |

Presentation (used by the application `analysis/belastungsmodell_app/app_ziel3.py` and by the tests):

| Module | Content |
|---|---|
| `figures_roc.py` | ROC by time (linear and logarithmic), theory against measurement, ROC by count, device → system, window T, error balance over h |
| `figures_werkstatt.py` | one figure built from options (event-based, by count, classical ROC over P0, by time, theory against measurement) for the tab "ROC-Werkstatt"; table at a chosen h; export as PNG and SVG |

Results per ROC folder under `logs/` (`ziel3_roc` = η 0.9, `ziel3_roc_p07` = η 0.7): `roc_alle.csv`,
`roc_tabellen.md` / `.json`, `roc_patienten.json`. Exported figures go to
`analysis/belastungsmodell_app/figures/ziel3/` (created on export, not in git).

Latency: columns `verz_*` count from the beginning of the crisis, `verz_kreuz_*` from the first
crossing of a device threshold (main definition, thesis chapter 4). E1 counts recognised crises
strictly (until the end of the crisis) and mildly (until end + 10 s, with the measured tail of the
device alarms).

Check by repetition (network run `ziel3_12h`, journal B → offline core against `decisions.jsonl`
of the real Dashboard): 1058 against 1063 escalations (−0.47 %), 1056 matched, start differing by
+0.06 s in the median (p10 / p90 ± 0.5 s = phase of the evaluation grid). The profile of the
devices (TPR 0.9 / FPR 0.00313 → w_j) stays that of the contract for every h; the TTL cache of
10 s, the grace period of 10 s and the grid of 1 s are constants of the Dashboard and do not
depend on T.
