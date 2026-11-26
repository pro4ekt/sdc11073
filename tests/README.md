# tests — unit tests of the Dashboard and of the evaluation (unittest)

Run from the repository root (both requirement files installed):

```
.venv\Scripts\python -m unittest discover -s tests -t .
.venv\Scripts\python -m unittest tests.test_math_core -v        # one suite
.venv\Scripts\python -m unittest discover -s experiments\ziel3_simulation\pc2\tests   # generator of the simulation
```

No network, PKI or provider is needed.

| Suite | Tests | Where the number comes from |
|---|---:|---|
| Dashboard | **194** | state `stand-messungen` (commit 5e81e55); this is the number given in the thesis (section 3.2.6, appendix C.2) |
| Evaluation of goal 3 (`analysis/`) | 236 | added for state `abgabe` |
| **`tests/` in total** | **430** | state `abgabe`: `Ran 430 tests … OK` |
| Generator of the simulation (`experiments/ziel3_simulation/pc2/tests`) | 38 | separate command, state `abgabe` |
| PC 1 side of the simulation (`experiments/ziel3_simulation/tests`) | 12 | separate command, state `abgabe` |

In a fresh clone 28 of the 430 tests are skipped: they compare against the journals of the
simulation runs under `experiments/ziel3_simulation/logs/`, which are not part of the repository.
The result is then `OK (skipped=28)`.

Dashboard (194):

| File | Tests | What it covers |
|---|---:|---|
| `test_math_core.py` | 86 | math core: evidence window, context odds, quorum `k_min`, hysteresis, full ticks, repository fail-open paths |
| `test_periodic_eval.py` | 15 | periodic ticks and time-in-alarm behaviour |
| `test_presence_truth.py` | 9 | device alarm presence as the single source of truth |
| `test_alignment_integration.py` | 13 | `SmartAlertAggregator` + `EnsembleTopologyManager` with stubbed devices: admission rule, OFF edge, ensemble shrinkage |
| `test_departure_overview.py` | 6 | device departure, TTL cache, patient overview |
| `test_concept_map.py` | 9 | MDC → LOINC concept map |
| `test_discovery_filter.py` | 14 | goal 1: scope parsing, room decision table, context precheck, journal format |
| `test_ack_watchdog.py` | 20 | goal 2: acknowledgement transactions, confirmation, timeouts |
| `test_watchdog.py` | 10 | goal 2: keep-alive ping and fallback |
| `test_connection_lost.py` | 12 | goal 2: technical alarm and UI state on connection loss |

Evaluation of goal 3 (236):

| File | Tests | What it covers |
|---|---:|---|
| `test_model.py` | 41 | analytical load model (`analysis/belastungsmodell_app/model.py`): calibration, control numbers of thesis chapter 5, rules, curves, priorities, device-count sweep |
| `test_model_ziel3.py` | 34 | load model of appendix G (`model_ziel3.py`): control values of the median ICU, enumeration of all subsets, export |
| `test_sim_data.py` | 23 | reading of the result files in `docs/reports/` (`sim_data.py`), intervals, comparison model – simulation |
| `test_ziel3_tabs.py` | 22 | tabs of the application `app_ziel3.py`: data, model against simulation, export |
| `test_roc_ziel3.py` | 47 | curves over the device thresholds: journals, replay of the unchanged Dashboard core, metrics, hull, model extensions E1/E2 |
| `test_werkstatt.py` | 27 | filter configurations T/α/δ_min through the unchanged Dashboard core, bootstrap of the differences, all views, export |
| `test_netzlauf_auswertung.py` | 11 | network runs A–D from the journals of PC 2 (schema of PC 2, control numbers) |
| `test_kalibrierung.py` | 16 | event-based ROC, classical ROC of the filter over P0, selection criterion, AUC bootstrap |
| `test_zeit_roc.py` | 8 | time-based ROC per window T, decomposition of the crisis time |
| `test_kapitel5.py` | 7 | overview tab of the application: blocks, summary sentences, export |

`__init__.py` puts `Dashboard/` first on `sys.path` (the package uses the bare imports
`app.*` / `device.*`) and redirects the event journal to a temporary file.
