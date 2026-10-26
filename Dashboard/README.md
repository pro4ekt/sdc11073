# Dashboard — the SDC consumer application

PySide6/QML desktop application that discovers SDC providers, subscribes to the devices of its
own room, aggregates their alarms per patient and lets the user acknowledge alarms remotely.

- `main.py` — entry point (argument parsing, logging, Qt application, QML engine). Run with
  `python -m Dashboard` from the repository root (see `__main__.py`) or `python main.py` from here.
- `app/` — the manager `SdcMyConsumer`, the Qt bridge per device, the alarm model (`app/alarms/`) and the metrics journal (`app/metrics/`).
- `device/` — one `DeviceHandler` worker thread per provider: connect, context check, subscribe, keep-alive ping, acknowledgement.
- `qml/` — the user interface; `config/` — device profiles and the MDC→LOINC concept map; `img/` — UI images.
- `logs/` — created at run time (`sdc_consumer.log`, `events.jsonl`), ignored by git.
- `ARCHITECTURE.md` — the full architecture reference (packages, threads, data flow, alarm model).

The package uses the bare imports `app.*` and `device.*`, so this folder must be first on
`sys.path`; `__main__.py`, `experiments/harness.py` and `tests/__init__.py` take care of that.
Flags: `--room`, `--ip`, `--event-log`, `--tls`, `--no_tls` (details in the root README).
