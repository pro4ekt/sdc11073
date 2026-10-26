# tests — unit tests of the Dashboard (unittest, 194 tests)

Run from the repository root:

```
.venv\Scripts\python -m unittest discover -s tests -t .
.venv\Scripts\python -m unittest tests.test_math_core -v        # one suite
```

Expected: `Ran 194 tests … OK`. No network, PKI or provider is needed.

| File | Tests | What it covers |
|---|---|---|
| `test_math_core.py` | 86 | math core: evidence window, context odds, quorum `k_min`, hysteresis, full ticks, repository fail-open paths |
| `test_alignment_integration.py` | 13 | `SmartAlertAggregator` + `EnsembleTopologyManager` with stubbed devices: admission rule, OFF edge, ensemble shrinkage |
| `test_periodic_eval.py` | 15 | periodic ticks and time-in-alarm behaviour |
| `test_presence_truth.py` | 9 | device alarm presence as the single source of truth |
| `test_departure_overview.py` | 6 | device departure, TTL cache, patient overview |
| `test_discovery_filter.py` | 14 | goal 1: scope parsing, room decision table, context precheck, journal format |
| `test_ack_watchdog.py` | 20 | goal 2: acknowledgement transactions, confirmation, timeouts |
| `test_watchdog.py` | 10 | goal 2: keep-alive ping and fallback |
| `test_connection_lost.py` | 12 | goal 2: technical alarm and UI state on connection loss |
| `test_concept_map.py` | 9 | MDC → LOINC concept map |

`__init__.py` puts `Dashboard/` first on `sys.path` (the package uses the bare imports
`app.*` / `device.*`) and redirects the event journal to a temporary file.
