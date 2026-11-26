# analysis/belastungsmodell_app — analytical load model of goal 3 (Streamlit)

Interactive version of the analytical alarm-load model used to evaluate goal 3
(reduction of false alarms; thesis chapter 4, 4.4.2 and chapter 5, 5.3). Every model
input is a slider, tables and curves are recomputed immediately, and the figures can be
exported. All labels inside the app are German.

There are two applications. `app.py` is the load model of thesis chapter 5 and is described
below. `app_ziel3.py` shows the four curves of appendix G (`model_ziel3.py`), the simulation
runs from `docs/reports/` (`sim_data.py`), the comparison of model and simulation, and the
curves over the device thresholds from `analysis/roc_ziel3/`.

The main area of `app.py` follows the structure of chapter 5:

| Section | Content |
|---|---|
| 1 *Ohne und mit Filter* (5.3.2) | four metrics (ohne Filter → adaptiv), figures 1a–1d: false alarms over the number of channels, over the noise, over `c_art`, tornado |
| 2 *Vergleich mit k-aus-N* (5.3.3) | comparison table (Δ false alarms, missed crises per 1000, typical latency) and the four mechanisms: 2.1 priorities (combination table, figures P and B with inset), 2.2 melting of the threshold (D, D time course), 2.3 build-up of the evidence (C, C2 window effect), 2.4 adaptation to the number of devices (G2) |
| 3 *Weitere Kurven* | E, F, A (all rules), free sweep, `k_min` over α, export |

The sidebar groups the controls: Szenario, Station (Literatur), Annahmen, Filtermodell
(Prototyp), Vergleich mit k-aus-N (k, delay τ, direct noise), Zeitverlauf, Darstellung
(units per bed or per station with N beds).

| File | Content |
|---|---|
| `model.py` | the model only (no Streamlit): calibration from literature values, enumeration of the active channel subsets, rules *adaptiv* / *Basis* / *rein k-aus-N* / *ohne Filter*, counts per day, curve C, time simulation with hysteresis (curve D), tornado, sweeps; window effect (`t_need`, C2), combination table, device-count sweep (G2), comparison table, scaling per station |
| `app.py` | the Streamlit interface of the chapter 5 model |
| `export_figures.py` | matplotlib figures (shared by the apps) and the command-line export; exported files carry no title |
| `model_ziel3.py` | the load model of appendix G (no Streamlit): the four curves, control values of the median ICU |
| `sim_data.py` | reads the result files of the simulation runs in `docs/reports/`, confidence intervals, model with the measured parameters of a run |
| `app_ziel3.py` | the Streamlit interface for appendix G, the simulation runs and the curves over the device thresholds |
| `export_ziel3.py` | matplotlib figures of `app_ziel3.py` and their export |
| `kapitel5.py` | the overview tab of `app_ziel3.py`: blocks, key figures and summary sentences from the data |
| `mc_check.py` | Monte Carlo check of the counting rules of the appendix G model |
| `figures/` | exported PNG (300 dpi) and SVG files, created on export, not in git |
| `../../tests/` | `test_model.py` (41 tests, control numbers of chapter 5), `test_model_ziel3.py`, `test_sim_data.py`, `test_ziel3_tabs.py`, `test_kapitel5.py` |

## Installation and start

```
.venv\Scripts\python -m pip install -r requirements-app.txt
.venv\Scripts\python -m streamlit run analysis/belastungsmodell_app/app.py
.venv\Scripts\python -m streamlit run analysis/belastungsmodell_app/app_ziel3.py
```

Export of all figures with the default parameters (Median-Intensivstation). The old
PNG/SVG files in `figures/` are deleted first (other target folders are only overwritten
unless `--clean` is given):

```
.venv\Scripts\python analysis/belastungsmodell_app/export_figures.py
.venv\Scripts\python analysis/belastungsmodell_app/export_figures.py --szenario Grenzfall --outdir out
```

Exported names: `5-3-2_1a_Last_Kanaele`, `5-3-2_1b_Last_Rauschen`, `5-3-2_1c_Last_c_art`,
`5-3-2_1d_Tornado`, `5-3-3_B_Betriebskennlinie`, `5-3-3_C_Doppelartefakte`, `5-3-3_C2_Fenster`,
`5-3-3_D_Latenz`, `5-3-3_D_Zeitverlauf`, `5-3-3_G2_Geraetezahl`, `5-3-3_P_Prioritaeten`,
`G_A_alle_Regeln`, `G_E_c_art`, `G_F_PPV_TPR`.

Tests (from the repository root, together with the Dashboard tests):

```
.venv\Scripts\python -m unittest discover -s tests -t .
.venv\Scripts\python -m unittest tests.test_model -v
```

## Model in one paragraph

The station is described by `n` counted alarms per channel and day, the counted false
discovery rate `FDR_cnt`, the mean durations `d_false` and `d_true`, `|M|` channels of
which `n_H` have priority *hoch* (P = 3, the others P = 1), the common-cause share
`c_art` and the channel sensitivity `TPR_j`. From these the prevalence `P(C)` and the
occupancy `FPR` are calibrated (path A), all channels carry `w = ln(TPR/FPR)`, and
every non-empty subset `F` of active channels is classified against the threshold
`Θ = max(ε, k·w·δ − L)` as *sofort* (escalates at once), *nach Schmelzen* (escalates after
`t_esc` if the episode lasts that long) or *nie*. `k` comes from the `SDC_score`
(adaptiv) or is 2 (Basis); `rein k-aus-N` is the classical counting rule without melting,
context and window (k is clamped to |M|); `ohne Filter` lets every channel report. The
window effect (C2, P) is an additional idealised calculation: evidence builds up linearly
over `T`, so an episode escalates only if it outlasts `t_need = T·k/r` (or `t_esc`); it is
not part of the conservative tables. `model.evaluate_bruteforce` repeats the calculation
by enumerating every subset, and `tests/test_model.py` holds the control numbers of
chapter 5.
