# experiments/ — evaluation of the thesis goals

One folder per goal. Production code stays in `Dashboard/` (layered
architecture, see `Dashboard/ARCHITECTURE.md`); the folders here hold the
goal maps (`README.md`), the experiment runners and analyzers of each goal and,
at run time, the journals of the runs (`logs/`, not in git). `harness.py` holds
the pieces shared by all runners (provider subprocess, headless Dashboard);
`campaign_2pc/` runs goals 1 and 2 in one campaign on one or two PCs;
`screenshots/` is a demo scenario and not an experiment.

| Ziel | Production code (file : function) | Tests (run from the repository root) | Experiment folder |
|---|---|---|---|
| **Ziel 1** Topologiefilterung | `Dashboard/app/sdcMyConsumer.py : _discovery_loop` (scope filter, `topology_decision@discovery`); `Dashboard/app/topology_filter.py : room_from_scopes / room_from_context_states / decide_room`; `Dashboard/device/handler.py : _phase_connect / _phase_context_precheck / _confirm_scope_room / _subscribe_reports / _phase_check_location`; `Dashboard/app/metrics/topology_metrics.py : TopologyMetrics` | `tests/test_discovery_filter.py` (R1–R7) | [`ziel1/`](ziel1/README.md) |
| **Ziel 2** Alarmsteuerung + Fallback | `Dashboard/device/alarm_manager.py : AlarmManager._set_alert_presence / acknowledge_alarm / reactivate_alarm`; `Dashboard/app/qtDeviceHandler.py : silenceAlarm / _select_ack_targets / markConnectionLost`; `Dashboard/device/handler.py : _monitoring_loop / _ping / _record_fallback_metric / _on_subscription_end`; `Dashboard/app/sdcMyConsumer.py : remove_device / _raise_technical_alarm / on_device_connected / dismissConnectionLoss`; `Dashboard/app/patientOverviewModel.py : markLost / clearLost`; `Dashboard/app/metrics/latency_metrics.py : LatencyMetrics` | `tests/test_ack_watchdog.py` (K1–K4), `tests/test_connection_lost.py` (L1–L5), `tests/test_watchdog.py` (W1–W4) | [`ziel2/`](ziel2/README.md) |
| **Ziel 3** Reduktion von Fehlalarmen | `Dashboard/app/alarms/` (decision core: `smartAlertAggregator.py`, `adaptive_alarm_aggregator.py`, `evidence_accumulator.py`, `urgency_engine.py`, `hysteresis_filter.py`), run unchanged in the simulation and replayed offline by `analysis/roc_ziel3/dashcore.py` | `tests/test_math_core.py` and the other Dashboard tests of the alarm model; evaluation: `tests/test_model*.py`, `tests/test_roc_ziel3.py` and the files listed in `tests/README.md`; generator: `ziel3_simulation/pc2/tests` | [`ziel3_simulation/`](ziel3_simulation/README.md) (two-PC simulation), evaluation in `analysis/` |

Metrics per goal:

- **Ziel 1** — Topology Rejection Accuracy. Bettfremde trials = roles
  *foreign* + *mismatch*; TRA = correctly rejected bettfremde trials / all
  bettfremde trials (target ≥ 99 %). Providers without location (decision
  `unknown`) are excluded from the denominator and reported separately;
  false rejects of own-room providers are reported separately. Avoided
  subscriptions: `subscription_avoided` vs `subscription_created` in the event
  journal, provider side `status: … subscriptions=N`, and network traffic for
  FF1 (tshark/Wireshark on the dashboard host, bytes per provider port,
  filtered vs. baseline without `--room`); a mismatch provider gets one
  connection for the context check but no subscription.
- **Ziel 2** — acknowledgement success rate counted per transaction
  (`ack_rtt`; several transactions of one bell press are grouped by
  `press_id`; `ok = true` = InvocationState Fin/FinMod; `timeout` counts as a
  failure, a later `ack_confirmed` is reported separately as "late success";
  target ≥ 99 %), p95 of the round-trip time (`rtt_ms`, nearest rank, over
  successful transactions only — a deliberate choice), and fallback timing:
  main value t_loss → `technical_alarm{raised}` (the safe state visible to
  staff), intermediate value t_loss → `fallback`, each against the derived
  bound per loss type (graceful shutdown, killed process, silent packet loss;
  see `ziel2/README.md`).
- **Ziel 3** — no clinical measurement: the alarm reduction is derived
  analytically (`analysis/belastungsmodell_app/`), and the simulation checks
  whether the prototype behaves as the model predicts. Counted per run and
  rule (*adaptiv*, 2-aus-4, 3-aus-4): false escalations per bed and hour,
  recognised crises (mild: escalation until the end of the crisis + 10 s;
  strict: until its end) and the delay from the first crossing of a device
  threshold; result files in `docs/reports/`.

Shared tools stay in `tools/`: `live_check.py` (provider end-to-end check used
by both goals), `make_testbed_pki.py`, `validate_mdib.py`, `check_mdib_rules.py`.
Every run writes its journal with `python Dashboard/main.py --event-log
experiments/ziel<N>/logs/<name>.jsonl`.

## Running the runners on one PC

Close the Dashboard window first: every run starts its own headless Dashboard. Journals,
console output and analyses go to `experiments/ziel1/logs/` and `experiments/ziel2/logs/`.
The `--dry-run` form prints the commands and starts nothing.
The options of every runner and analyzer are listed in the README of its folder: [`ziel1/`](ziel1/README.md), [`ziel2/`](ziel2/README.md), [`campaign_2pc/`](campaign_2pc/README.md), [`ziel3_simulation/`](ziel3_simulation/README.md), [`screenshots/`](screenshots/README.md).

## Journals, captures and the headless Dashboard

The journals and packet captures of the thesis runs are not part of the repository. They are
kept separately; runs of goals 1 and 2 can be repeated with the runners. At run time the
components write to `Dashboard/logs/` (`sdc_consumer.log`, `events.jsonl`),
`experiments/ziel1/logs/`, `experiments/ziel2/logs/`, `experiments/campaign_2pc/logs/<run>/`,
`experiments/ziel3_simulation/logs/<run>/` and `experiments/screenshots/logs/`; all of these are
ignored by git. Included are the result files computed from the goal 3 runs, in `docs/reports/`.

Optional: Wireshark with `tshark` on the PATH (or under `C:\Program Files\Wireshark`) for
packet captures during the experiment runs; the runs work without it.

A headless Dashboard without the QML window is provided by the class `Dashboard` in
`experiments/harness.py`; the experiment runners and
`experiments/screenshots/demo_scenario.py --headless` use it.
