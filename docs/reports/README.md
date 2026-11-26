# docs/reports — analysis report and result files

## MDIB analysis

- `MDIB_ANALYSIS.md` — read-only analysis made before the testbed MDIBs were written: the
  BICEPS schemas shipped with sdc11073, the structural template
  (`testbed/mdib/template/correct_mdib.xml`), what the Dashboard requires from a provider
  MDIB (parts A–C), and the rules that `tools/check_mdib_rules.py` checks. The design
  notes in `testbed/mdib/README.md` refer to its sections. It is a historical document and is
  not updated when the code changes.

## Result files of the goal 3 simulation

One pair of files per network run: `results_<run>.json` (escalations of every rule, recognised
crises, delays, confidence intervals) and `messwerte_<run>.json` (measured parameters of the
alarm stream: FPR and TPR per source, crisis share, mean durations). They are computed from the
journals of the run, which are not part of the repository. The application
`analysis/belastungsmodell_app/app_ziel3.py` and the tests read the files from this folder.

| Run | Name in the thesis | Computed with |
|---|---|---|
| `ziel3_1h` | 1 h | `experiments/ziel3_simulation/auswertung/ziel3_analyze.py` on PC 2 |
| `ziel3_12h` | 12 h | the same |
| `ziel3_6h_p06` | 6 h (η = 0.6) | the same |
| `ziel3_4h_p07` | A (η = 0.7) | `python -m analysis.roc_ziel3.netzlauf_auswertung` on PC 1, from the journals A/B of PC 2 and `decisions.jsonl` of PC 1 |
| `ziel3_4h_p08` | B | the same |
| `ziel3_4h_lang` | C | the same |
| `ziel3_6h_ideal` | D | the same |

Both programs use the same processing; `netzlauf_auswertung.py` was checked on the 12 h run
against the files of PC 2 (`tests/test_netzlauf_auswertung.py`).

- `checks_ziel3_12h.md` — protocol of the checks of the 12 h run, written by `ziel3_analyze.py`:
  alarm events of journal B against the alarm lines of the Dashboard log (the log covers the run
  from 31 minutes after the beginning of the evaluation window, because the first log file had
  been removed by the log rotation), conspicuous Rot episodes, crises missed by the filter.
