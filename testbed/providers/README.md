# testbed/providers — the Python testbed provider

`testbed_provider.py` is a complete SDC provider built on sdc11073 3.0.0: it loads an MDIB,
publishes the device over WS-Discovery (optionally with a location scope), serves the
metrics with rest-mode noise, raises and clears alarms, answers remote acknowledgements
(`SetAlertState`, policy `accept` or `refuse`) and EnsembleContext writes
(`SetContextState`), and refreshes `LastSelfCheck` of every alert system.

Main pieces: `parse_args` (flags), `derive_from_mdib` (channels, alert systems and context
handles read from the MDIB), `ChannelRuntime`, a product/role provider that handles the two
operations, `FixedPortHttpServerThread` (fixed `--port`), and the stdin command interpreter
(`value`, `rest`, `alarm`, `ack`, `selfcheck`, `status`, `shutdown`).

Started by hand (root README) or as a subprocess by `experiments/harness.py` (`Prov`),
`tools/live_check.py` and `experiments/campaign_2pc/pc2_scheduler.py`.
