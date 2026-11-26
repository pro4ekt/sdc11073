# experiments/ziel3_simulation/auswertung — evaluation of the network runs (written on PC 2)

These scripts turn the journals of one simulation run into the result files in
`docs/reports/` (`results_<run>.json`, `messwerte_<run>.json`, `checks_<run>.md`). They were
written and run on PC 2 and are published as they were used, so that the counting rules of the
thesis (appendix E) can be read in the code.

**The scripts expect the data layout of PC 2 and do not run unchanged in a clone of this
repository:** they read the journals from `experiments/ziel3_simulation/logs/<run>/pc2/` and the
digest of PC 1 from `logs/<run>_pc1/pc1/export/`, read and write `results_*.json` /
`messwerte_*.json` in this folder (not in `docs/reports/`). The journals themselves are not part
of the repository (several gigabytes). The modules can be imported with the packages of
`requirements-app.txt` (numpy, scipy).

| File | Content |
|---|---|
| `common.py` | shared pieces: loading of the PC 2 export and of the PC 1 digest, clock mapping PC 2 → PC 1 (`ClockMap`), escalation rules on the alarm stream (`k_of_n`, k-of-4 with hold-over, delayed counting rule `k_of_n_tau`), crisis matching and crisis classes, false episodes, exact confidence intervals (Poisson, Clopper-Pearson) |
| `ziel3_analyze.py` | one run: analysis window, rules (*adaptiv* = Rot of the Dashboard, 1/2/3-aus-4, 2-aus-4 with 10 s hold-over), caught crises (mild and strict), delays, false escalations, measured stream parameters, checks. `--name RUN`, `--exclude PATIENT/SOURCE`, `--patients N`, `--pc1-dir DIR` |
| `modell_g7.py` | the analytical load model of appendix G in Python and its comparison with one run (expected false escalations per bed and hour, strictly caught crises); line 36 holds the Monte Carlo control values. `--name RUN`, `--c-art`, `--d-false`, `--eta` |
| `bootstrap_patienten.py` | patient bootstrap of the key figures (20 patients drawn with replacement, 10 000 repetitions) and paired differences *adaptiv* − k-aus-4. `--runs LIST`, `--reps N`, `--seed N` |
| `ziel3_roc.py` | metrics of the threshold sweep written by `../pc2/roc_sweep.py`: TPR/FPR by time for the single devices and for k-aus-4, AUC. `--dir FOLDER` |

The evaluation that runs in a clone is `analysis/roc_ziel3/` (it reads the result files in
`docs/reports/` and, where present, the journals under `logs/`); see `analysis/roc_ziel3/README.md`.
