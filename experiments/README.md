# experiments/ — evaluation of the thesis goals (Kapitel1.md §1.4)

One folder per goal. Production code stays in `Dashboard/` (layered
architecture, see `Dashboard/ARCHITECTURE.md`); the folders here hold the
goal maps (`README.md`), goal-specific helper scripts and the journals of
experiment runs (`logs/`, empty until a run is made). Experiment runners and
analyzers are not part of these folders yet — the folders are prepared for them.

| Ziel | Production code (file : function) | Tests (run from `Dashboard/`) | Experiment folder |
|---|---|---|---|
| **Ziel 1** Topologiefilterung | `Dashboard/app/sdcMyConsumer.py : _discovery_loop` (scope filter, `topology_decision@discovery`); `Dashboard/app/topology_filter.py : room_from_scopes / room_from_context_states / decide_room`; `Dashboard/device/handler.py : _phase_connect / _phase_context_precheck / _confirm_scope_room / _subscribe_reports / _phase_check_location`; `Dashboard/app/metrics/topology_metrics.py : TopologyMetrics` | `tests/test_discovery_filter.py` (R1–R7) | [`ziel1/`](ziel1/README.md) |
| **Ziel 2** Alarmsteuerung + Fallback | `Dashboard/device/alarm_manager.py : AlarmManager._set_alert_presence / acknowledge_alarm / reactivate_alarm`; `Dashboard/app/qtDeviceHandler.py : silenceAlarm / _select_ack_targets / markConnectionLost`; `Dashboard/device/handler.py : _monitoring_loop / _ping / _record_fallback_metric / _on_subscription_end`; `Dashboard/app/sdcMyConsumer.py : remove_device / _raise_technical_alarm / on_device_connected / dismissConnectionLoss`; `Dashboard/app/patientOverviewModel.py : markLost / clearLost`; `Dashboard/app/metrics/latency_metrics.py : LatencyMetrics` | `tests/test_ack_watchdog.py` (K1–K4), `tests/test_connection_lost.py` (L1–L5), `tests/test_watchdog.py` (W1–W4) | [`ziel2/`](ziel2/README.md) |

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

Shared tools stay in `tools/`: `live_check.py` (provider end-to-end check used
by both goals), `make_testbed_pki.py`, `validate_mdib.py`, `check_mdib_rules.py`.
Every run writes its journal with `python Dashboard/main.py --event-log
experiments/ziel<N>/logs/<name>.jsonl`.
