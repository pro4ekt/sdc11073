# Dashboard/app/metrics — event journal and goal metrics

- `eventlog.py` — `EventLog`, `get_event_log()`, `configure(path)`: the JSON Lines journal
  (`Dashboard/logs/events.jsonl` by default, `--event-log` to change it). Every topology
  decision, subscription, acknowledgement round trip and fallback is one line.
- `topology_metrics.py` — `TopologyMetrics`: goal 1 counters (accepted / rejected providers, avoided subscriptions).
- `latency_metrics.py` — `LatencyMetrics`: goal 2 acknowledgement RTT and fallback timing.
- `arr_metrics.py` — `ArrMetrics`: alarm-reduction counters of the alarm model.

Written by `Dashboard/app/sdcMyConsumer.py` and `Dashboard/device/handler.py`; read by the analyzers in
`experiments/ziel1/ziel1_analyze.py` and `experiments/ziel2/ziel2_analyze.py`.
The unit tests redirect the journal to a temporary file (`tests/__init__.py`).
