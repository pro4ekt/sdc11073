# Dashboard/device — SDC worker per provider

- `handler.py` — `DeviceHandler`: one background thread per provider. Connects with
  `sdc11073.consumer.SdcConsumer`, checks the LocationContext before subscribing (goal 1),
  subscribes to the reports, pings with `GetContextStates`, detects connection loss and
  reports the fallback (goal 2), forwards metrics and alarms to `Dashboard/app/qtDeviceHandler.py`.
- `alarm_manager.py` — `AlarmManager`: remote acknowledgement (`SetAlertState` with
  Presence=Ack), confirmation and timeout tracking, re-raise handling.
- `context_ops.py` — `apply_ensemble_context`, `apply_fhir_contexts`: writes the EnsembleContext / WorkflowContext to the provider.
- `ssl_builder.py` — `build_ssl_container`: mutual-TLS contexts from `pki/dashboard/`.
- `logging_setup.py` — rotating file logger `Dashboard/logs/sdc_consumer.log` and noise filters.
- `patches.py` — `apply_patches`: small run-time patches of sdc11073 3.0.0.

Created by `Dashboard/app/sdcMyConsumer.py`; covered by `tests/test_ack_watchdog.py`,
`tests/test_watchdog.py`, `tests/test_connection_lost.py`, `tests/test_discovery_filter.py`.
