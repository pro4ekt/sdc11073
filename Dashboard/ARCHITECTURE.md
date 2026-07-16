# Dashboard — Architecture Reference

> IEEE 11073 SDC Consumer Application with Qt/QML UI, FHIR integration, and smart alert aggregation.

---

## Table of Contents

1. [Project Structure](#1-project-structure)
2. [Package Overview](#2-package-overview)
3. [Entry Point — `main.py`](#3-entry-point--mainpy)
4. [Package `app/`](#4-package-app)
   - [SdcMyConsumer](#sdcmyconsumer)
   - [QtDeviceHandler](#qtdevicehandler)
   - [SmartAlertAggregator](#smartalertaggregator)
   - [AlarmCoordinator](#alarmcoordinator)
   - [DeviceProfileRepository](#deviceprofilerepository)
   - [OperationLogger](#operationlogger)
   - [FHIRPatientData](#fhirpatientdata)
5. [Package `device/`](#5-package-device)
   - [DeviceHandler](#devicehandler)
   - [AlarmManager](#alarmmanager)
   - [context_ops](#context_ops)
   - [ssl_builder](#ssl_builder)
   - [logging_setup](#logging_setup)
   - [patches](#patches)
6. [QML UI (`qml/`)](#6-qml-ui-qml)
7. [Configuration (`config/`)](#7-configuration-config)
8. [Workflow](#8-workflow)
   - [8.1 Application Startup](#81-application-startup)
   - [8.2 WSDiscovery Loop](#82-wsdiscovery-loop)
   - [8.3 Device Connection Lifecycle](#83-device-connection-lifecycle)
   - [8.4 MDIB Initialisation](#84-mdib-initialisation)
   - [8.5 Monitoring Loop](#85-monitoring-loop)
   - [8.6 Alarm Handling](#86-alarm-handling)
   - [8.7 UI Update Pipeline](#87-ui-update-pipeline)
   - [8.8 Ensemble & FHIR Binding](#88-ensemble--fhir-binding)
   - [8.9 Room Switching](#89-room-switching)
   - [8.10 Graceful Shutdown](#810-graceful-shutdown)
9. [Threading Model](#9-threading-model)
10. [Data Flow Diagram](#10-data-flow-diagram)
11. [Test Harness — 3-Node ICU Ensemble](#11-test-harness--3-node-icu-ensemble-mytestscorrect_provider)

---

## 1. Project Structure

```
Dashboard/
├── main.py                     # Entry point
├── ARCHITECTURE.md             # This file
├── __init__.py
│
├── app/                        # Application-layer Python modules
│   ├── __init__.py
│   ├── sdcMyConsumer.py        # Manager: WSDiscovery + device lifecycle
│   ├── qtDeviceHandler.py      # Qt/QML bridge for one device
│   ├── operationLogger.py      # OR session file logger
│   ├── fhirData.py             # HL7 FHIR REST client
│   │
│   └── alarms/                 # IHE-PCD Alarm Management Reference Implementation
│       ├── __init__.py         # Package: exports AlarmCoordinator + SmartAlertAggregator
│       ├── alarmCoordinator.py # Two-stage pipeline: HardwareArtifactFilter → ClinicalRiskFilter (prior=0.005)
│       ├── smartAlertAggregator.py  # Ensemble management + physiological graph + 10-second TTL alarm cache
│       └── device_profile_repo.py   # Lazy-loading Repository (DAO): 1 point-query per (mfr, model, concept); reads config/clinical_db.json on first query
│
├── device/                     # SDC worker (background threads)
│   ├── __init__.py
│   ├── handler.py              # DeviceHandler — full SDC connection lifecycle
│   ├── alarm_manager.py        # DEV-31 ack + ack-timeout tracking
│   ├── context_ops.py          # EnsembleContext + FHIR WorkflowContext writes
│   ├── ssl_builder.py          # mTLS SSLContextContainer builder
│   ├── logging_setup.py        # Logger configuration for SDC worker
│   └── patches.py              # Monkey-patches for sdc11073 bugs
│
├── qml/                        # QML UI files
│   ├── Main.qml
│   ├── LoginPage.qml
│   ├── MainPage.qml
│   ├── DevicePage.qml
│   ├── MetricPage.qml
│   └── OperationPage.qml
│
├── config/                     # Runtime configuration
│   ├── rules.json              # Alert rule definitions
│   ├── clinical_db.json        # IHE-PCD calibration DB: RoC limits + device Bayesian profiles (Draeger/BBraun)
│   └── *.xml                   # MDIB fixture files
│
├── tools/                      # Utility scripts
│   └── gen_certs.py            # Certificate generation helper
│
├── certs_out/                  # Generated TLS certificates
├── logs/                       # Rotating log files (sdc_consumer.log)
└── data/                       # Persistent data (FHIR cache, etc.)
```

---

## 2. Package Overview

| Package / Module | Role |
|---|---|
| `main.py` | CLI argument parsing, Qt app bootstrap, QML engine |
| `app.sdcMyConsumer` | Orchestrates all device connections; WSDiscovery loop |
| `app.qtDeviceHandler` | Qt-thread-safe mirror of one device's live data for QML |
| `app.alarms` | **IHE-PCD Alarm Management sub-package** — public API for the pipeline |
| `app.alarms.smartAlertAggregator` | Groups devices into ensembles; physiological graph; 10-second TTL alarm cache; routes DSP checks |
| `app.alarms.alarmCoordinator` | Two-stage IHE-PCD ACM pipeline: RoC gate (Stage 1) + Bayesian Sensor Fusion (Stage 2, prior=0.005) |
| `app.alarms.device_profile_repo` | **Lazy-loading Repository (DAO)** — reads `config/clinical_db.json` on the *first query* (not at import); process-wide singleton; memoises per `(manufacturer, model, concept)`; exports `DeviceReliabilityProfile` DTO |
| `app.operationLogger` | Thread-safe OR-session file logger |
| `app.fhirData` | Fetches Patient/Condition/Observation from FHIR R4 server |
| `device.handler` | Background thread: full SDC connect → monitor → disconnect |
| `device.alarm_manager` | Alarm Ack and Ack-timeout tracking per device |
| `device.context_ops` | SOAP SetContextState calls (EnsembleContext, WorkflowContext) |
| `device.ssl_builder` | Builds mTLS `SSLContextContainer` from cert files |
| `device.logging_setup` | Configures rotating log + console handler; attaches spam filters |
| `device.patches` | One-time monkey-patches for sdc11073 deserialization bugs |

---

## 3. Entry Point — `main.py`

**File:** `Dashboard/main.py`

Parses CLI arguments, bootstraps the Qt application, loads QML, and starts the SDC consumer.

### CLI Arguments

| Argument | Default | Purpose |
|---|---|---|
| `--room` | `""` | Target room filter (empty = all rooms) |
| `--tls` | flag | Force TLS on; skip auto-detect |
| `--no_tls` | flag | Force TLS off |
| `--ip` | `""` | Bind to a specific local network adapter IP |

### Startup Sequence

1. Parse args with `argparse`
2. Call `sdc11073.loghelper.basic_logging_setup()`
3. Create `QGuiApplication`
4. Instantiate `SdcMyConsumer` and call `.start()`
5. Create `QQmlApplicationEngine`, expose `SdcMyConsumer` as a QML context property
6. Load `qml/Main.qml`
7. Enter Qt event loop with `app.exec()`

---

## 4. Package `app/`

### SdcMyConsumer

**File:** `app/sdcMyConsumer.py`  
**Base:** `QObject`

Top-level manager. Runs the WSDiscovery loop in a background thread and maintains the map of active `DeviceHandler` workers. Exposes Qt properties and signals for QML bindings.

#### Signals

| Signal | Payload | Fired When |
|---|---|---|
| `deviceConnected` | `QtDeviceHandler` | New device handler ready in main thread |
| `deviceDisconnected` | `str` (epr) | Device worker stopped |
| `roomChanged` | — | `currentRoom` property changed |
| `availableRoomsChanged` | — | Set of known rooms updated |

#### Qt Properties

| Property | Type | Description |
|---|---|---|
| `currentRoom` | `str` | Active room filter; QML-writable via `switchRoom()` |
| `availableRooms` | `list[str]` | All rooms seen during this session |

#### Methods (`SdcMyConsumer`)

| Method | Purpose |
|---|---|
| `start()` | Spawn the discovery thread and begin scanning |
| `stop()` | Set `running=False`, stop all active `DeviceHandler` workers, join thread |
| `_run_discovery()` | Thread target: creates asyncio event loop and runs `_discovery_loop()` |
| `_discovery_loop()` | Async: WSDiscovery scan → spawn `DeviceHandler` for each new device; manages per-EPR cooldown timers to avoid duplicate connects |
| `remove_device(epr, error_occurred, location_filtered)` | Called by a `DeviceHandler` on exit; cleans up maps, emits `deviceDisconnected` |
| `register_device_room(epr, room)` | Called by `DeviceHandler` after location check; populates known rooms |
| `switchRoom(new_room)` | Qt Slot: changes `currentRoom`, stops devices outside the new room, removes EPR bans for devices in the new room |

---

### QtDeviceHandler

**File:** `app/qtDeviceHandler.py`  
**Base:** `QObject`

Live QML-facing mirror of a single SDC device. Created in the worker thread but immediately moved to the Qt main thread via `moveToThread()`. All property reads happen in the main thread, updates are scheduled thread-safely via `updateTick`.

#### Signals

| Signal | Payload | Fired When |
|---|---|---|
| `patientNameChanged` | — | Patient name updated |
| `patientRoomChanged` | — | Location context changed |
| `deviceNameChanged` | — | DPWS FriendlyName changed |
| `deviceValueChanged` | — | Primary metric value changed |
| `alarmStatusChanged` | — | Alarm status string changed |
| `priorityChanged` | — | Alert priority level changed |
| `metricsChanged` | — | Metrics list rebuilt |
| `operationsChanged` | — | Operations list rebuilt |
| `eprChanged` | — | EPR string set |
| `connectedChanged` | — | Connection state toggled |
| `updateTick` | — | Cross-thread trigger to run `handleUpdateTick()` in main thread |

#### Qt Properties

| Property | Type | Description |
|---|---|---|
| `patientName` | `str` | Full name from PatientContext |
| `patientRoom` | `str` | Location from LocationContext |
| `epr` | `str` | Device endpoint reference (unique ID) |
| `deviceName` | `str` | DPWS FriendlyName |
| `deviceValue` | `str` | Primary numeric metric value as string |
| `metrics` | `list[dict]` | All NumericMetric states: `{handle, value, unit, label}` |
| `alarmStatus` | `str` | One of `Off / On / Ack / Latch / COMM_FAILURE` |
| `priority` | `str` | Alert priority: `Low / Medium / High` |
| `operations` | `list[dict]` | Available operations: `{handle, name, type}` |

#### Methods (`QtDeviceHandler`)

| Method | Purpose |
|---|---|
| `__init__(device)` | Store `DeviceHandler` reference; wire `updateTick` → `handleUpdateTick` |
| `scheduleUpdate()` | Called from worker thread; emits `updateTick` to trigger main-thread refresh |
| `handleUpdateTick()` | Qt Slot (main thread): calls `update_data()` |
| `update_data()` | Non-blocking MDIB read; updates all Qt properties: location, patient, device name, metrics, alarms, operations, clock-offset correction, SelfCheckPeriod validation |
| `silenceAlarm()` | Qt Slot: finds active `AlertSignalState` + matching `SetAlertStateOperation`, delegates to `device.acknowledge_alarm()` |

---

### SmartAlertAggregator

**File:** `app/alarms/smartAlertAggregator.py`

Manages device ensembles (groups of devices treating the same patient), maintains a physiological data graph, and gates the two-stage alarm pipeline. **Database-agnostic**: it receives pre-fetched calibration (`reliability_profile`, `roc_limit`) as arguments and never queries `clinical_db.json` itself.

#### Key Internal State (`SmartAlertAggregator`)

| Attribute | Type | Description |
|---|---|---|
| `lock` | `threading.Lock` | Mutex protecting all internal maps below |
| `_active_ensembles` | `Dict[(patient_id, room), uuid]` | Maps patient+room key to ensemble UUID |
| `_ensemble_devices` | `Dict[uuid, Set[epr]]` | Devices belonging to each ensemble |
| `_fhir_cache` | `Dict[patient_id, FHIRPatientData]` | Cached FHIR responses (session-scoped) |
| `_fhir_focus_cache` | `Dict[patient_id, list]` | Cached FHIR clinical-focus rules (Condition Extensions) |
| `_physiological_graph` | `Dict[uuid, Dict[concept_code, deque[(value, ts)]]]` | Sliding window of metric values per ensemble (maxlen=15) |
| `_active_alarms` | `Dict[uuid, Dict[alert_key, (DeviceAlertEvidence, ts)]]` | **TTL alarm cache** — every fired alarm, GC'd after `ALARM_TTL_SEC` |
| `ALARM_TTL_SEC` | `float` = 10.0 | Age after which a cached alarm is considered inactive and dropped |

#### Methods (`SmartAlertAggregator`)

| Method | Purpose |
|---|---|
| `update_metric_state(ensemble_uuid, concept_code, value)` | Append a new `(value, timestamp)` sample to the sliding window deque (maxlen=15) |
| `get_metric_state(ensemble_uuid, concept_code, max_age_sec)` | Return the latest value for a concept code, or `None` if stale/absent |
| `dump_physiological_graph()` | Return a human-readable snapshot of all sliding-window data |
| `check_alert_validity(ensemble_uuid, alert_key, metric_concept, biceps_priority, manufacturer, model, device_epr, reliability_profile, roc_limit)` | **Two-stage pipeline gate.** Snapshots the metric buffer; registers the alarm in `_active_alarms` (TTL); GCs stale entries; assembles `ensemble_evidences` from ALL active alarms; delegates to `AlarmCoordinator.evaluate()`; returns `AlarmDecision.escalate`. `reliability_profile`/`roc_limit` are pre-fetched by `DeviceHandler` and embedded in each `DeviceAlertEvidence` |
| `check_alert_priority(...)` | Stub; returns `False` (priority escalation disabled) |
| `audit_topology(ensemble_uuid)` | Stub; returns `[]` (topology validation removed) |
| `log_clinical_focus_summary(ensemble_uuid)` | Log that the rule engine was removed |
| `_collect_patient_danger_codes(ensemble_uuid)` | Reverse-lookup: ensemble UUID → patient → FHIR danger codes |
| `_collect_fhir_focus(ensemble_uuid)` | Return FHIR clinical focus rules for the patient in an ensemble |
| `_extract_patient_and_room(device_handler)` | Read MDIB context states (WorkflowContext → PatientContext fallback) to get `(patient_id, room)` |
| `_get_or_fetch_fhir_data(patient_id)` | Return cached `FHIRPatientData`, or fetch fresh and cache it |
| `evaluate_and_bind_device(device_handler)` | Entry point for a new device: extract context → find/create ensemble → call `apply_ensemble_context` + `apply_fhir_contexts` |

---

### AlarmCoordinator

**File:** `app/alarms/alarmCoordinator.py`

IHE-PCD ACM Alarm Coordinator node. Implements a two-stage **stateless** pipeline for clinical alarm validation, replacing the former `SignalProcessor`. All calibration data (`roc_limit`, `reliability_profile`) is supplied *inside* each `DeviceAlertEvidence` — the filter classes never query a database and hold no mutable instance state.

#### Stage 1 — `HardwareArtifactFilter`

Deterministic Rate-of-Change (dx/dt) gate, evaluated per triggering device.

| Method | Purpose |
|---|---|
| `validate(evidence, metric_buffer)` | Compute `\|Δvalue / Δtime\|` over the buffer's last two samples; compare against `evidence.roc_limit` (pre-fetched by `DeviceHandler`). Return `True` (pass) if RoC ≤ limit, `False` (suppress) if RoC exceeds limit. **Fail-open**: returns `True` if the buffer is too short (< 2 samples), `dt ≤ 0`, or `evidence.roc_limit is None` (concept not calibrated) |

#### Stage 2 — `ClinicalRiskFilter`

Bayesian Sensor Fusion across all devices in a patient ensemble.

| Attribute | Description |
|---|---|
| `_PRIORITY_WEIGHTS` | `Dict[str, float]` — BICEPS `AlertCondition.Priority` weights: `{'Hi': 10.0, 'Me': 6.0, 'Lo': 3.0, 'None': 0.0}` |
| `_FAIL_SAFE_PROFILE` | `DeviceReliabilityProfile(sensitivity=0.5, false_alarm_rate=0.5)` — neutral LR+ = 1.0; used when `evidence.reliability_profile is None` |

| Method | Purpose |
|---|---|
| `_get_profile(evidence)` | Return `evidence.reliability_profile`, or `_FAIL_SAFE_PROFILE` if it is `None` (neutral LR+ = 1.0) |
| `compute_risk(evidences, prior=0.005)` | **Step a** — P_total = max priority weight in ensemble. **Step b** — Posterior_P = normalised product of all LR+. **Step c** — `risk_score = Posterior_P × P_total ∈ [0.0, 10.0]` |

**Mathematics:**
```
LR+_i          = sensitivity_i / false_alarm_rate_i
Posterior_Odds = (prior / (1−prior)) × ∏ LR+_i         # prior = 0.005
Posterior_P    = Posterior_Odds / (1 + Posterior_Odds)
risk_score     = Posterior_P × P_total
```

#### `AlarmCoordinator` (Facade)

| Attribute | Value | Description |
|---|---|---|
| `ESCALATION_THRESHOLD` | `5.0` | Escalate when `risk_score ≥ 5.0` (= Hi priority × Posterior_P ≥ 0.5) |

| Method | Purpose |
|---|---|
| `evaluate(triggering_evidence, metric_buffer, ensemble_evidences)` | Run Stage 1 on the triggering device; if valid, run Stage 2 on all ensemble evidences; return `AlarmDecision` |

**`DeviceAlertEvidence`** — frozen dataclass passed through the pipeline: `alert_key`, `metric_concept`, `manufacturer`, `model`, `ensemble_uuid`, `biceps_priority`, **`reliability_profile: DeviceReliabilityProfile | None`** (Stage 2 LR+), **`roc_limit: float | None`** (Stage 1 dx/dt gate). The last two fields carry the calibration pre-fetched by `DeviceHandler`, so the pipeline is fully database-decoupled.

**`AlarmDecision`** — frozen dataclass returned by `evaluate()`: `escalate: bool`, `risk_score: float`, `contributing_devices: int`, `suppression_stage: str | None`, `suppression_reason: str | None`.

---

### DeviceProfileRepository

**File:** `app/alarms/device_profile_repo.py`

Lazy-loading **Repository / DAO** for `config/clinical_db.json`. Introduced to replace the previous `mock_clinical_db.py`, which read the *entire* database into memory at Python import time — an approach that does not scale to a hospital with thousands of registered devices.

#### Contract

- The JSON file is read from disk **on the first query**, not at import time (`_ensure_loaded()`, double-checked locking).
- Results are memoised per `(manufacturer, model, concept)` key and per `concept` (RoC) — O(1) after first hit.
- A process-wide singleton (`get_repository()`) is shared across all `DeviceHandler` instances, so the JSON is parsed **at most once per process**.
- `DeviceHandler` calls the repo once per concept right after `_build_semantic_map()` and caches the results in its own `_device_calibration` dict. The Aggregator and pipeline filters receive only pre-built DTOs — they never touch this module.

#### `DeviceReliabilityProfile` (DTO — frozen dataclass)

| Field | Meaning |
|---|---|
| `sensitivity` | `P(alarm \| true event)` — True-Positive Rate (TPR) |
| `false_alarm_rate` | `P(alarm \| no event)` — False-Positive Rate (FPR); LR+ = sensitivity / false_alarm_rate |

#### Methods (`DeviceProfileRepository`)

| Method | Purpose |
|---|---|
| `get_profile(manufacturer, model, concept)` | Point-query → `DeviceReliabilityProfile` or `None` (miss → caller uses fail-safe LR+=1.0). Memoised |
| `get_roc_limit(concept)` | Point-query → dx/dt limit (units/s) or `None` (miss → Stage 1 fail-open). Memoised |
| `_ensure_loaded()` | Load and parse the JSON on first use; if the file is missing, logs a warning and treats the DB as empty (all lookups → `None`) |

| Module Function | Purpose |
|---|---|
| `get_repository()` | Return the process-wide singleton; thread-safe via double-checked locking. Does **not** read the file — I/O is deferred to the first actual query |

---

### OperationLogger

**File:** `app/operationLogger.py`

Thread-safe structured logger for operative room sessions. Writes a timestamped `.txt` file recording all device events, alarms, metrics, and context changes during a surgical session.

#### Methods (`OperationLogger`)

| Method | Purpose |
|---|---|
| `__init__(patient_ctx, ensemble_uuid, output_dir)` | Open output file `OR_session_{Family}_{Given}_{ts}.txt`; write session header |
| `_write_header(ctx, ensemble_uuid)` | Write patient demographics and session metadata to the file |
| `finalize()` | Write session footer with end timestamp; return the file path |
| `log(device_epr, event_type, data)` | Core thread-safe append: acquire lock → write `[timestamp] [epr] [type] data` |
| `log_metric(epr, handle, value, alarm)` | Typed helper: log a metric value update |
| `log_alarm(epr, handle, presence)` | Typed helper: log an alarm presence transition |
| `log_context_applied(epr, context_type)` | Typed helper: log a successful context write (Ensemble / FHIR) |
| `log_device_event(epr, message)` | Typed helper: log a generic device lifecycle event |
| `log_ensemble(message)` | Typed helper: log an ensemble-level event |

---

### FHIRPatientData

**File:** `app/fhirData.py`

HL7 FHIR R4 REST client. Fetches a single Bundle containing Patient demographics, active Conditions, and recent Observations for a given patient ID.

#### Methods (`FHIRPatientData`)

| Method | Purpose |
|---|---|
| `fetch(patient_id)` | Issue a single FHIR Bundle query for Patient + Condition + Observation resources; populate internal state |
| `get_patient_id()` | Return the FHIR patient resource ID |
| `get_name()` | Return the formatted full name string (Family, Given) |
| `get_danger_codes()` | Return `list[dict]` of `{code, system, display}` from active Condition resources |
| `get_clinical_focus()` | Extract clinical monitoring focus rules from FHIR Condition Extension fields (`criticalSensorConcepts`, `priorityAlertConcepts`) |
| `get_vital_measurements()` | Return `{weight, height}` from Observation resources (LOINC 29463-7 and 8302-2) |

---

## 5. Package `device/`

### DeviceHandler

**File:** `device/handler.py`  
**Base:** `threading.Thread`

One background worker per SDC device. Runs its own asyncio event loop. Manages the full lifecycle: discovery → TLS negotiation → MDIB init → subscription → monitoring → disconnect.

#### Key Attributes (`DeviceHandler`)

| Attribute | Type | Description |
|---|---|---|
| `consumer` | `SdcConsumer` | Live SDC consumer connection |
| `mdib` | `ConsumerMdib` | Mirror of the device's MDIB (live-updating) |
| `data_lock` | `threading.Lock` | Protects `consumer` and `mdib` references |
| `ensemble_uuid` | `str \| None` | UUID of the ensemble this device is bound to |
| `manufacturer` | `str` | DPWS `ThisModel/Manufacturer` (used to look up calibration) |
| `model` | `str` | DPWS `ThisModel/ModelName` (used to look up calibration) |
| `alarm_manager` | `AlarmManager` | Handles alarm Ack and timeout logic |
| `_handle_to_concept` | `dict[str, str]` | Maps MDIB descriptor handle → LOINC concept code |
| `_device_calibration` | `dict[str, (roc_limit, DeviceReliabilityProfile\|None)]` | Per-concept calibration pre-fetched from `DeviceProfileRepository` (Repository pattern) |
| `_pipeline_suppressed` | `set[str]` | Condition handles suppressed by Stage 1/2; consulted by the UI layer to hide the red indicator |

#### Methods (`DeviceHandler`)

| Method | Purpose |
|---|---|
| `run()` | Thread entry point: creates asyncio loop, runs `_worker_logic()`, calls `manager.remove_device()` on exit |
| `_worker_logic()` | Async orchestrator: calls each `_phase_*` function in sequence; catches exceptions and triggers shutdown |
| `_phase_connect()` | Create `SdcConsumer` with TLS auto-detect/fallback; establish HTTPS connection to the provider; call `_extract_dpws_metadata()` |
| `_extract_dpws_metadata()` | Read DPWS `ThisModel` from `consumer.host_description`; populate `self.manufacturer` / `self.model` (fail-safe: leaves `''` → fail-safe profile) |
| `_resolve_ssl_container(x_addrs)` | Return `SSLContextContainer` based on current `tls_mode` setting |
| `_phase_init_mdib()` | Initialise `ConsumerMdib`; call `_build_semantic_map()`, `_prefetch_device_calibration()`, and `_log_alert_map()` |
| `_log_mdib_diagnostics()` | Log counts of context states for diagnostics |
| `_build_semantic_map()` | Walk all `NumericMetricDescriptor` handles; populate `_handle_to_concept` using LOINC codes from the descriptor |
| `_prefetch_device_calibration()` | **Repository pattern**: one point-query per concept via `get_repository()`; caches `(roc_limit, reliability_profile)` in `_device_calibration` so the pipeline never hits the DB at event time |
| `_log_alert_map()` | Log counts of `AlertConditionDescriptor` and `AlertSignalDescriptor` found in MDIB |
| `_phase_check_location()` | Read `LocationContext`; if a target room is configured, filter devices not in that room |
| `_phase_subscribe()` | Bind `on_metric_update` and `on_alert_update` callbacks via `sdc11073.observableproperties` |
| `_phase_aggregate_on_connect()` | Async: call `aggregator.evaluate_and_bind_device(self)` to assign ensemble and write FHIR context |
| `_phase_snapshot_initial_alerts()` | Replay current MDIB alert states into `on_alert_update()` to populate alarm state on first connect |
| `_phase_setup_qt()` | Create `QtDeviceHandler`, move it to Qt main thread via `moveToThread()`, emit `deviceConnected` signal |
| `_monitoring_loop()` | Async: run ping loop at SDPi-specified intervals; schedule UI updates; process ack timeouts |
| `_ping(missed, max_missed, interval, t_fallback)` | Issue `GetContextStates` to verify device is still reachable; count consecutive misses |
| `_process_ack_timeouts()` | Ask `AlarmManager.get_expired_handles()` and re-raise each via `reactivate_alarm()` |
| `on_metric_update(metrics_by_handle)` | Observable callback: push new values to `SmartAlertAggregator`; rate-limited UI refresh (≤1 Hz) |
| `on_alert_update(alert_by_handle)` | Observable callback: DSP filter → priority matrix → log transition → ack tracking |
| `_alert_type_label(state)` | Static helper: return `'Condition'`, `'Signal'`, or `'Alert'` based on state type |
| `_lookup_alert_concepts(handle, state)` | Non-blocking MDIB lookup for alert concept codes (uses `data_lock`) |
| `_log_condition_transition(...)` | Log boolean `Presence` changes for `AlertConditionState` |
| `_log_signal_transition(...)` | Log `AlertSignalPresence` enum changes; update `AlarmManager` ack tracking |
| `_is_alert_active(raw_presence)` | Return `True` if presence value represents an active (not off/unknown) alarm |
| `_get_device_room()` | Thread-safe read of current `LocationContext` room |
| `_graceful_shutdown()` | Async: gracefully stop the `SdcConsumer` connection (DEV-49 pattern) |
| `acknowledge_alarm(op_handle, signal_handle)` | Delegate to `AlarmManager.acknowledge_alarm()` |
| `apply_ensemble_context(ensemble_uuid)` | Delegate to `context_ops.apply_ensemble_context()` |
| `apply_fhir_contexts(fhir_data)` | Delegate to `context_ops.apply_fhir_contexts()` |
| `stop()` | Set `running = False` to request worker termination |

---

### AlarmManager

**File:** `device/alarm_manager.py`

Handles alarm acknowledgement (DEV-31) and ack-timeout re-raising for a single device. Shares `DeviceHandler.data_lock`.

#### Key Attributes (`AlarmManager`)

| Attribute | Type | Description |
|---|---|---|
| `ACK_TIMEOUT_SEC` | `float` | Class constant — 30 s; after this duration an Ack is re-raised |
| `_ack_timestamps` | `dict[str, float]` | Maps AlertSignal descriptor handle → monotonic time of Ack transition |

#### Methods (`AlarmManager`)

| Method | Purpose |
|---|---|
| `set_ack(handle, timestamp)` | Record the time an alarm signal was acknowledged |
| `clear_ack(handle)` | Remove ack tracking for a signal (alarm cleared or re-raised to On) |
| `is_tracked(handle)` | Return `True` if the ack timer is currently running for this handle |
| `get_expired_handles(now)` | Return all handles whose Ack has been held longer than `ACK_TIMEOUT_SEC` |
| `acknowledge_alarm(operation_handle, alert_signal_handle)` | Two-phase: read MDIB under lock → send `SetAlertState(Presence=Ack)` over network |
| `reactivate_alarm(operation_handle, alert_signal_handle)` | Two-phase: read MDIB under lock → send `SetAlertState(Presence=On)` to re-raise timed-out ack |
| `find_operation_handle(sig_handle)` | Search MDIB for the `SetAlertStateOperationDescriptor` targeting `sig_handle`; returns `op.Handle` or `None` |

---

### context_ops

**File:** `device/context_ops.py`  
Module-level functions (no class). Both follow the **two-phase pattern**: build the proposed context state under `data_lock`, then send via SOAP without the lock.

#### Functions

| Function | Purpose |
|---|---|
| `apply_ensemble_context(handler, ensemble_uuid)` | Build an `EnsembleContextState` with the given UUID; send `SetContextState` to the SDC Provider; set `handler.ensemble_uuid` on success. Returns `True`/`False` |
| `apply_fhir_contexts(handler, fhir_data)` | Convert FHIR `DangerCode` entries to BICEPS `CodedValue` objects; write into `WorkflowContextState` via `SetContextState`; trigger topology audit via `aggregator.audit_topology()` |

---

### ssl_builder

**File:** `device/ssl_builder.py`

#### Functions

| Function | Purpose |
|---|---|
| `build_ssl_container(logger)` | Try certificate candidates in priority order (`certs_out/` → `pat/certs/` → `tests/certificates/`). Build a client SSL context (outgoing HTTPS) and a server SSL context (`PROTOCOL_TLS_SERVER` for incoming WS-Eventing push). Return `certloader.SSLContextContainer`. Logs warning if no cert is found. |

---

### logging_setup

**File:** `device/logging_setup.py`

#### Classes

| Class | Purpose |
|---|---|
| `_SuppressGetContextStates400` | `logging.Filter` — drop repetitive `GetContextStates HTTP 400` ERROR spam from sdc11073 internals |

#### Functions

| Function | Purpose |
|---|---|
| `setup_module_logger()` | Configure `sdc.consumer` logger with a `StreamHandler` (INFO) and a `RotatingFileHandler` (DEBUG, 5 MB × 5 backups, `logs/sdc_consumer.log`) |
| `apply_sdc_log_filters()` | Attach `_SuppressGetContextStates400` to `sdc.client.soap` and `sdc.client.mdib` loggers |

---

### patches

**File:** `device/patches.py`

#### Functions

| Function | Purpose |
|---|---|
| `apply_patches()` | Public entry point — idempotent, calls all patch functions |
| `_patch_related_measurement()` | Fix `RelatedMeasurement.from_node()` deserialization bug in sdc11073: replace with a version that creates an empty object via `cls(Measurement(None, None))` and then calls `update_from_node()` |

---

## 6. QML UI (`qml/`)

| File | Purpose |
|---|---|
| `Main.qml` | Root window. Hosts the `StackView` navigation stack. Checks for saved room on startup; routes to `LoginPage` or `MainPage` |
| `LoginPage.qml` | Room / adapter selection screen. User picks a room from `availableRooms`; calls `SdcMyConsumer.switchRoom()` |
| `MainPage.qml` | ICU overview grid. Shows all connected devices as cards; taps navigate to `DevicePage` |
| `DevicePage.qml` | Single device detail view: patient name/room, primary value, alarm status, silence button |
| `MetricPage.qml` | Full metric list for one device. Displays all `metrics[]` with labels, values, and units |
| `OperationPage.qml` | Operations list for one device. Allows invocation of available SDC operations |

---

## 7. Configuration (`config/`)

| File | Purpose |
|---|---|
| `rules.json` | Alert rule definitions (thresholds, LOINC codes, priority mappings) |
| `clinical_db.json` | **IHE-PCD calibration DB** — two top-level keys: `roc_limits` (concept → dx/dt limit) and `device_profiles` (manufacturer → model → concept → `{sensitivity, false_alarm_rate}`). Read lazily by `DeviceProfileRepository` |
| `*.xml` | MDIB fixture files used for testing and offline development |

---

## 8. Workflow

### 8.1 Application Startup

```
main.py
  │
  ├─ argparse (--room, --tls, --no_tls, --ip)
  ├─ basic_logging_setup()
  ├─ QGuiApplication()
  ├─ SdcMyConsumer(room, tls_mode, adapter_ip)
  │     └─ SmartAlertAggregator()
  ├─ SdcMyConsumer.start()         ← spawns discovery thread
  ├─ QQmlApplicationEngine()
  │     └─ load("qml/Main.qml")
  └─ app.exec()                    ← Qt event loop
```

---

### 8.2 WSDiscovery Loop

```
SdcMyConsumer._run_discovery()           (background thread, asyncio loop)
  │
  └─ _discovery_loop()
        │
        ├─ WSDiscoverySingleAdapter(adapter_ip).start()
        │
        └─ loop:
              ├─ wsd.search_services(types=[SDC_v1_type])
              ├─ for each new EPR not in active_devices:
              │     ├─ check cooldown timer
              │     └─ DeviceHandler(epr, x_addrs, manager, ...).start()
              └─ sleep(SCAN_INTERVAL)
```

---

### 8.3 Device Connection Lifecycle

```
DeviceHandler.run()
  │
  └─ _worker_logic()
        │
        ├─ patches.apply_patches()
        ├─ logging_setup.setup_module_logger()
        ├─ logging_setup.apply_sdc_log_filters()
        │
        ├─ _phase_connect()
        │     ├─ _resolve_ssl_container(x_addrs) → SSLContextContainer | None
        │     └─ SdcConsumer(x_addrs, ssl=...).start_all()
        │
        ├─ _phase_init_mdib()          ← see §8.4
        ├─ _phase_check_location()     ← room filter
        ├─ _phase_subscribe()          ← bind callbacks
        ├─ _phase_aggregate_on_connect() ← ensemble + FHIR
        ├─ _phase_snapshot_initial_alerts()
        ├─ _phase_setup_qt()           ← create QtDeviceHandler, emit deviceConnected
        │
        └─ _monitoring_loop()          ← see §8.5
              │
              └─ [finally] _graceful_shutdown()
                           manager.remove_device(epr)
```

---

### 8.4 MDIB Initialisation

```
_phase_init_mdib()   (called while data_lock is held)
  │
  ├─ ConsumerMdib(consumer).init_mdib()     ← full MDIB snapshot over HTTPS (GetMdib)
  ├─ _log_mdib_diagnostics()                ← count context states
  ├─ _build_semantic_map()
  │     └─ for each NumericMetricDescriptor:
  │           map handle → LOINC/BICEPS code (Type.Code, fallback Handle)
  ├─ _prefetch_device_calibration()         ← Repository pattern (lazy DB)
  │     └─ repo = get_repository()          ← process-wide singleton
  │        for concept in set(_handle_to_concept.values()):
  │           roc_limit = repo.get_roc_limit(concept)               ← 1 point-query
  │           profile   = repo.get_profile(manufacturer, model, concept) ← 1 point-query
  │           _device_calibration[concept] = (roc_limit, profile)
  │        (JSON parsed from disk ONLY on the very first query, then memoised)
  └─ _log_alert_map()
        └─ count AlertConditionDescriptor + AlertSignalDescriptor
```

---

### 8.5 Monitoring Loop

```
_monitoring_loop()
  │
  └─ loop (while running):
        ├─ await asyncio.sleep(PING_INTERVAL)
        ├─ _ping(missed, max_missed, interval, t_fallback)
        │     ├─ consumer.context_service_client.get_context_states()
        │     ├─ success → missed = 0
        │     └─ failure → missed += 1 → if missed >= max_missed → raise
        └─ _process_ack_timeouts()
              ├─ alarm_manager.get_expired_handles(now)
              └─ for each expired:
                    alarm_manager.find_operation_handle(sig_handle)
                    await asyncio.to_thread(alarm_manager.reactivate_alarm, ...)
```

---

### 8.6 Alarm Handling

```
on_alert_update(alert_by_handle)            ← called by sdc11073 observable
  │
  ├─ for each (handle, state):
  │     ├─ _lookup_alert_concepts(handle, state) → (metric_concept, biceps_priority)
  │     │     roc_limit, reliability_profile = _device_calibration[metric_concept]
  │     │                                       (pre-fetched at init — no DB hit here)
  │     │
  │     ├─ [Alarm Pipeline] aggregator.check_alert_validity(
  │     │       ensemble_uuid, alert_key, metric_concept, biceps_priority,
  │     │       manufacturer, model, device_epr,
  │     │       reliability_profile, roc_limit)
  │     │     │
  │     │     ├─ if not metric_concept → return True (fail-open)
  │     │     │
  │     │     ├─ [lock] read buf_snapshot from
  │     │     │         _physiological_graph[ensemble_uuid][metric_concept]
  │     │     │         (absent → return True, fail-open)
  │     │     │
  │     │     ├─ [lock] build triggering_evidence = DeviceAlertEvidence(
  │     │     │           alert_key, metric_concept, manufacturer, model,
  │     │     │           ensemble_uuid, biceps_priority,
  │     │     │           reliability_profile, roc_limit)
  │     │     │
  │     │     ├─ [lock] register / refresh trigger in TTL cache:
  │     │     │         _active_alarms[ensemble_uuid][alert_key] = (evidence, now)
  │     │     │
  │     │     ├─ [lock] garbage-collect stale entries:
  │     │     │         remove keys where (now − ts) > ALARM_TTL_SEC (10 s)
  │     │     │
  │     │     ├─ [lock] assemble ensemble_evidences:
  │     │     │         [ev for ev, _ts in _active_alarms[ensemble_uuid].values()]
  │     │     │         (all currently active alarms across every device in ensemble)
  │     │     │
  │     │     └─ AlarmCoordinator.evaluate(
  │     │             triggering_evidence, buf_snapshot, ensemble_evidences)
  │     │           │
  │     │           ├─ Stage 1: HardwareArtifactFilter.validate(evidence, metric_buffer)
  │     │           │     compute |Δv/Δt| over last two samples of buf_snapshot
  │     │           │     |Δv/Δt| > ROC_LIMIT →
  │     │           │           AlarmDecision(escalate=False, risk_score=-1.0,
  │     │           │                         suppression_stage='HardwareArtifactFilter',
  │     │           │                         suppression_reason='RoC exceeded')
  │     │           │     |Δv/Δt| ≤ ROC_LIMIT (or unknown concept / short buffer) →
  │     │           │           proceed to Stage 2
  │     │           │
  │     │           └─ Stage 2: ClinicalRiskFilter.compute_risk(
  │     │                         ensemble_evidences, prior=0.005)
  │     │                 P_total        = max(PRIORITY_WEIGHTS[e.biceps_priority])
  │     │                 Posterior_Odds = (prior/(1−prior)) × ∏ LR+_i
  │     │                 Posterior_P    = Posterior_Odds / (1 + Posterior_Odds)
  │     │                 risk_score     = Posterior_P × P_total  ∈ [0.0, 10.0]
  │     │                 risk_score < 5.0 →
  │     │                       AlarmDecision(escalate=False,
  │     │                                     suppression_stage='ClinicalRiskFilter')
  │     │                 risk_score ≥ 5.0 →
  │     │                       AlarmDecision(escalate=True, risk_score=<value>)
  │     │
  │     │     → returns decision.escalate (bool)
  │     │
  │     ├─ if not escalate → add condition handle to _pipeline_suppressed
  │     │        (QtDeviceHandler.update_data() skips these → no red UI indicator)
  │     │   if escalate     → discard handle from _pipeline_suppressed
  │     │
  │     ├─ if AlertConditionState → _log_condition_transition(...)
  │     │
  │     └─ if AlertSignalState   → _log_signal_transition(...)
  │           ├─ On  → alarm_manager.clear_ack(handle)
  │           ├─ Ack → alarm_manager.set_ack(handle, time.monotonic())
  │           └─ Off → alarm_manager.clear_ack(handle)
  │
  └─ scheduleUpdate() → QtDeviceHandler.scheduleUpdate()
```

---

### 8.7 UI Update Pipeline

```
DeviceHandler.on_metric_update() / on_alert_update()
  │
  └─ rate_limiter: if now - last_update < 1.0s → skip
        │
        └─ qt_handler.scheduleUpdate()
              │
              └─ emit updateTick          ← cross-thread Qt signal
                    │
                    └─ [main thread] handleUpdateTick()
                          │
                          └─ update_data()
                                ├─ acquire mdib (non-blocking trylock)
                                ├─ read LocationContext → patientRoom
                                ├─ read PatientContext  → patientName
                                ├─ read DPWS FriendlyName → deviceName
                                ├─ read AlertSignal priority matrix → alarmStatus
                                ├─ read SelfCheckPeriod → COMM_FAILURE check
                                ├─ read ClockState     → time offset correction
                                ├─ rebuild metrics[]   → emit metricsChanged
                                └─ rebuild operations[] → emit operationsChanged
```

---

### 8.8 Ensemble & FHIR Binding

```
_phase_aggregate_on_connect()
  │
  └─ aggregator.evaluate_and_bind_device(handler)
        │
        ├─ _extract_patient_and_room(handler)
        │     └─ read WorkflowContext → PatientContext (fallback)
        │
        ├─ _get_or_fetch_fhir_data(patient_id)
        │     └─ FHIRPatientData.fetch(patient_id)
        │           └─ GET /Patient/{id}/$everything
        │
        ├─ find or create ensemble UUID for (patient_id, room)
        │
        ├─ context_ops.apply_ensemble_context(handler, ensemble_uuid)
        │     ├─ [lock] build EnsembleContextState
        │     └─ [no lock] consumer.context_service_client.set_context_state(...)
        │
        └─ context_ops.apply_fhir_contexts(handler, fhir_data)
              ├─ [lock] build WorkflowContextState with DangerCodes
              ├─ [no lock] consumer.context_service_client.set_context_state(...)
              └─ aggregator.audit_topology(ensemble_uuid)
```

---

### 8.9 Room Switching

```
QML: LoginPage → SdcMyConsumer.switchRoom(new_room)    [Qt Slot]
  │
  ├─ currentRoom = new_room
  ├─ emit roomChanged
  │
  ├─ for each active DeviceHandler whose room ≠ new_room:
  │     handler.stop()
  │     add epr to banned set
  │
  └─ remove ban for devices whose room == new_room
        (they will be re-discovered and connected on next scan)
```

---

### 8.10 Graceful Shutdown

```
SdcMyConsumer.stop()
  │
  ├─ running = False
  ├─ for each DeviceHandler: handler.stop()
  └─ discovery_thread.join()

DeviceHandler._graceful_shutdown()    (DEV-49)
  │
  ├─ consumer.stop_all()
  └─ consumer.unsubscribe_all()
```

---

## 9. Threading Model

| Thread | Who Creates It | What Runs There |
|---|---|---|
| **Qt main thread** | `QGuiApplication` | Qt event loop, all `QObject` signal-slot dispatch, QML engine, `QtDeviceHandler.update_data()` |
| **Discovery thread** | `SdcMyConsumer.start()` | asyncio loop with `_discovery_loop()`; spawns `DeviceHandler` threads |
| **DeviceHandler thread** (×N) | `DeviceHandler.start()` | Per-device asyncio loop: connect, subscribe, monitor, ping |

**Cross-thread communication:**
- Worker → Qt: `QtDeviceHandler.scheduleUpdate()` emits `updateTick` via Qt's thread-safe signal mechanism (`QMetaObject::invokeMethod` equivalent in PySide6)
- Qt → Worker: `silenceAlarm()` calls `device.acknowledge_alarm()` directly (the call completes quickly; actual SOAP send is done with `future.result(timeout=5)` which blocks the main thread briefly)
- All MDIB accesses in `update_data()` use a non-blocking `data_lock.acquire(blocking=False)` to avoid stalling the UI

---

## 10. Data Flow Diagram

This section traces every significant data path in the application, from raw
network packets to pixels on screen. It is split into six complementary views:

- **10.1** — Top-level component & transport map
- **10.2** — Live metric data path (device → physiological graph → UI)
- **10.3** — Alarm data path (EpisodicAlertReport → two-stage pipeline → UI/ack)
- **10.4** — Calibration data path (clinical_db.json → Repository → evidence)
- **10.5** — Ensemble & FHIR enrichment path
- **10.6** — Legend & data structures reference

---

### 10.1 Top-Level Component & Transport Map

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                                SDC NETWORK (LAN)                               ║
║                                                                                ║
║   ┌────────────┐        ┌────────────┐        ┌────────────┐                   ║
║   │ Provider A │        │ Provider B │        │ Provider C │   (SDC devices)   ║
║   │ Monitor    │        │ Ventilator │        │ Perfusor   │                   ║
║   └─────┬──────┘        └─────┬──────┘        └─────┬──────┘                   ║
║         │  WS-Discovery (UDP multicast :3702)       │                          ║
║         │  Hello / Bye / ProbeMatch                 │                          ║
║         │  MDPWS: SOAP/HTTP(S) GetMdib, Subscribe   │                          ║
║         │  EpisodicMetricReport / EpisodicAlertReport (WS-Eventing push)       ║
╚═════════╪══════════════════════╪═════════════════════╪═════════════════════════╝
          │                      │                     │
          ▼                      ▼                     ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                      CONSUMER PROCESS  (single OS process)                     │
│                                                                                │
│  ┌───────────────────────────── Discovery thread ───────────────────────────┐ │
│  │ SdcMyConsumer._discovery_loop()                                           │ │
│  │   WSDiscoverySingleAdapter.search_services(SDC_v1)                        │ │
│  │   → new EPR? → spawn DeviceHandler(wsd_service).start()                   │ │
│  └───────────────────────────────┬──────────────────────────────────────────┘ │
│                                   │ 1 thread per device                        │
│         ┌─────────────────────────┼─────────────────────────┐                  │
│         ▼                         ▼                         ▼                  │
│  ┌────────────┐            ┌────────────┐            ┌────────────┐            │
│  │DeviceHandler│           │DeviceHandler│           │DeviceHandler│  (thread×N)│
│  │  A (asyncio)│           │  B (asyncio)│           │  C (asyncio)│            │
│  │  ConsumerMdib│          │  ConsumerMdib│          │  ConsumerMdib│           │
│  │  AlarmManager│          │  AlarmManager│          │  AlarmManager│           │
│  │  _device_    │          │  _device_    │          │  _device_    │           │
│  │  calibration │          │  calibration │          │  calibration │           │
│  └──────┬───────┘          └──────┬───────┘          └──────┬───────┘           │
│         │  observable callbacks (on_metric_update / on_alert_update)           │
│         └──────────────┬───────────┴───────────┬──────────────┘                │
│                        ▼                       ▼                               │
│              ┌───────────────────┐   ┌───────────────────────┐                 │
│              │ SmartAlertAggregator│  │  QtDeviceHandler ×N   │                 │
│              │  (shared, 1 inst.)  │  │  (main Qt thread)     │                 │
│              │  self.lock guards:  │  └──────────┬────────────┘                 │
│              │   _physiological_   │             │ Qt property bindings         │
│              │     graph           │             ▼                              │
│              │   _active_alarms    │  ┌───────────────────────┐                 │
│              │   _active_ensembles │  │   QML Engine (UI)     │                 │
│              │   _fhir_cache       │  │   MainPage/DevicePage │                 │
│              └─────────┬───────────┘  └───────────────────────┘                 │
│                        │ evaluate()                                            │
│                        ▼                                                       │
│              ┌───────────────────────┐      ┌───────────────────────────────┐  │
│              │   AlarmCoordinator     │      │  DeviceProfileRepository      │  │
│              │  Stage 1 RoC gate      │◄─────│  (singleton, lazy DAO)        │  │
│              │  Stage 2 Bayes fusion  │ DTO  │  reads config/clinical_db.json│  │
│              └───────────────────────┘      └───────────────────────────────┘  │
│                                                                                │
│              ┌───────────────────────┐      ┌───────────────────────────────┐  │
│              │   FHIRPatientData      │─────▶│  External FHIR R4 REST server │  │
│              │   (HTTP GET $everything)│      │  (HTTPS, off-box)             │  │
│              └───────────────────────┘      └───────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

### 10.2 Live Metric Data Path

How a single numeric measurement travels from the device to the screen and into
the DSP sliding window that Stage 1 later reads.

```
Provider (device)
   │  EpisodicMetricReport  (WS-Eventing HTTP POST push)
   ▼
sdc11073 internals  →  ConsumerMdib applies the delta to its state tree
   │  fires observable:  metrics_by_handle
   ▼
DeviceHandler.on_metric_update(metrics_by_handle)          [worker thread]
   │
   ├─ for handle, state in metrics_by_handle.items():
   │     concept = _handle_to_concept.get(handle)           ← semantic map
   │     value   = state.MetricValue.Value                  ← Decimal → float
   │     │
   │     └─ aggregator.update_metric_state(ensemble_uuid, concept, value)
   │           │  [aggregator.lock]
   │           └─ _physiological_graph[ensemble_uuid][concept]
   │                   .append((value, time.time()))         ← deque(maxlen=15)
   │                                                            (sliding window)
   │
   └─ rate limiter: if now − _last_ui_update_ts ≥ 1.0 s      (≤ 1 Hz)
         └─ qt_handler.scheduleUpdate()  ── emit updateTick ──►  [main thread]
                                                                     │
                          QtDeviceHandler.handleUpdateTick()  ◄──────┘
                             └─ update_data()   (non-blocking data_lock trylock)
                                   ├─ rebuild metrics[]  → emit metricsChanged
                                   └─ QML re-renders MetricPage / DevicePage
```

**Data structures touched:**
`_handle_to_concept` (read) → `_physiological_graph[uuid][concept]` (append, maxlen=15)
→ Qt `metrics` property (list[dict]) → QML `ListView`.

---

### 10.3 Alarm Data Path (Two-Stage Pipeline)

The critical path: an alarm state change fans out into the Bayesian pipeline,
UI suppression bookkeeping, and DEV-31 acknowledgement tracking.

```
Provider (device)
   │  EpisodicAlertReport  (AlertCondition.Presence / AlertSignal.Presence change)
   ▼
ConsumerMdib  →  fires observable:  alert_by_handle
   ▼
DeviceHandler.on_alert_update(alert_by_handle)              [worker thread]
   │
   └─ for handle, state in alert_by_handle.items():
        │
        ├─ (metric_concept, biceps_priority) = _lookup_alert_concepts(handle, state)
        │       roc_limit, reliability_profile = _device_calibration[metric_concept]
        │            ▲ pre-fetched at init — NO database access on this hot path
        │
        ├─ escalate = aggregator.check_alert_validity(          ── PIPELINE GATE ──
        │       ensemble_uuid, alert_key=handle, metric_concept, biceps_priority,
        │       manufacturer, model, device_epr,
        │       reliability_profile, roc_limit)
        │     │
        │     │  [aggregator.lock] ─────────────────────────────────────────────┐
        │     │   1. buf_snapshot = copy of _physiological_graph[uuid][concept]  │
        │     │        (None → return True, fail-open)                           │
        │     │   2. evidence = DeviceAlertEvidence(... , reliability_profile,    │
        │     │                                     roc_limit)                    │
        │     │   3. _active_alarms[uuid][alert_key] = (evidence, now)   ← TTL reg│
        │     │   4. GC: drop entries where now − ts > ALARM_TTL_SEC (10 s)       │
        │     │   5. ensemble_evidences = [ev for ev,_ts in                       │
        │     │                            _active_alarms[uuid].values()]         │
        │     │        (ALL active alarms across ALL devices in the ensemble)     │
        │     │  ─────────────────────────────────────────────────────────────── ┘
        │     │
        │     └─ AlarmCoordinator.evaluate(evidence, buf_snapshot, ensemble_evidences)
        │           │
        │           ├─ STAGE 1  HardwareArtifactFilter.validate(evidence, buf)
        │           │     dv/dt = |v[-1]−v[-2]| / (t[-1]−t[-2])
        │           │     dv/dt > evidence.roc_limit  → SUPPRESS (artifact)
        │           │         AlarmDecision(escalate=False, risk_score=−1.0,
        │           │              suppression_stage='HardwareArtifactFilter')
        │           │     else (or roc_limit None / buf<2 / dt≤0) → fall through
        │           │
        │           └─ STAGE 2  ClinicalRiskFilter.compute_risk(ensemble_evidences)
        │                 P_total  = max(_PRIORITY_WEIGHTS[e.biceps_priority])
        │                 odds     = (0.005/0.995)
        │                 for e in ensemble_evidences:
        │                     p    = e.reliability_profile or _FAIL_SAFE_PROFILE
        │                     odds *= p.sensitivity / p.false_alarm_rate   (LR+)
        │                 Posterior_P = odds / (1 + odds)
        │                 risk_score  = Posterior_P × P_total     ∈ [0.0, 10.0]
        │                 risk ≥ 5.0 → AlarmDecision(escalate=True,  risk_score)
        │                 risk < 5.0 → AlarmDecision(escalate=False,
        │                                  suppression_stage='ClinicalRiskFilter')
        │           returns decision.escalate  (bool)
        │
        ├─ UI SUPPRESSION BOOKKEEPING
        │     not escalate → _pipeline_suppressed.add(condition_handle)
        │     escalate     → _pipeline_suppressed.discard(condition_handle)
        │        │
        │        └─ QtDeviceHandler.update_data() SKIPS handles in
        │           _pipeline_suppressed → red indicator hidden in QML
        │
        ├─ AlertConditionState → _log_condition_transition(...)   (Presence bool)
        │
        └─ AlertSignalState    → _log_signal_transition(...)      (Presence enum)
              On  → alarm_manager.clear_ack(handle)
              Ack → alarm_manager.set_ack(handle, time.monotonic())   ← DEV-31 timer
              Off → alarm_manager.clear_ack(handle)

   ── ACK TIMEOUT (separate, in _monitoring_loop) ──────────────────────────────
   _process_ack_timeouts()
     expired = alarm_manager.get_expired_handles(now)   (held > ACK_TIMEOUT_SEC 30 s)
     for sig in expired:
        op = alarm_manager.find_operation_handle(sig)
        alarm_manager.reactivate_alarm(op, sig)  → SOAP SetAlertState(Presence=On)
```

**Manual acknowledgement (UI → device):**
```
QML "Silence" button → QtDeviceHandler.silenceAlarm()   [main thread]
   └─ device.acknowledge_alarm(op_handle, sig_handle)
         └─ AlarmManager.acknowledge_alarm(...)
               [data_lock] build proposed AlertSignalState(Presence=Ack)
               [no lock]   SOAP SetAlertState → Provider   (future.result timeout=5)
```

---

### 10.4 Calibration Data Path (Repository Pattern)

Shows how the *lazy* Repository decouples the hot alarm path from disk I/O.
The JSON file is read at most **once per process**, on the first query, and the
alarm hot path (§10.3) never touches disk.

```
config/clinical_db.json  (on disk)
   { "roc_limits":     { "8867-4": 10.0, "20053-5": 50.0, ... },
     "device_profiles":{ "Draeger": { "Infinity Monitor":
                          { "8867-4": {sensitivity:0.99, false_alarm_rate:0.15} }}}}
   │
   │  read ONCE, lazily, on first get_profile()/get_roc_limit()
   ▼
DeviceProfileRepository  (process-wide singleton via get_repository())
   _ensure_loaded()  → json.load  → self._raw
   _profile_cache : (mfr, model, concept) → DeviceReliabilityProfile | None   (memoised)
   _roc_cache     : concept → float | None                                     (memoised)
   ▲
   │  called ONCE per concept, at device init (NOT per alarm)
   │
DeviceHandler._prefetch_device_calibration()               [worker thread, init]
   for concept in set(_handle_to_concept.values()):
       roc_limit = repo.get_roc_limit(concept)
       profile   = repo.get_profile(manufacturer, model, concept)
       _device_calibration[concept] = (roc_limit, profile)
   │
   │  read at alarm time (in-memory dict lookup, O(1), no I/O, no lock)
   ▼
on_alert_update()  →  roc_limit, profile = _device_calibration[concept]
   │
   ▼
DeviceAlertEvidence(reliability_profile=profile, roc_limit=roc_limit)
   │  travels through the pipeline as a self-contained DTO
   ▼
HardwareArtifactFilter.validate()  reads evidence.roc_limit
ClinicalRiskFilter._get_profile()  reads evidence.reliability_profile
   (neither filter imports device_profile_repo — full DB decoupling)
```

---

### 10.5 Ensemble & FHIR Enrichment Path

Runs once per device connection, binding the device into a patient ensemble and
writing FHIR-derived clinical context back to the provider.

```
DeviceHandler._phase_aggregate_on_connect()      [worker thread]
   └─ asyncio.to_thread(aggregator.evaluate_and_bind_device, self)
        │
        ├─ _extract_patient_and_room(handler)          [handler.data_lock]
        │     WorkflowContextState → Patient.Identification.Extension   (primary)
        │     PatientContextState  → Identification / CoreData name     (fallback)
        │     LocationContextState → LocationDetail.Room
        │     → (patient_id, room)
        │
        ├─ key = (patient_id, room)                     [aggregator.lock]
        │     key in _active_ensembles ? reuse uuid : new uuid4()
        │     _ensemble_devices[uuid].add(epr)
        │     handler.ensemble_uuid = uuid
        │
        ├─ _get_or_fetch_fhir_data(patient_id)          [NO lock — slow HTTP]
        │     cache hit? return _fhir_cache[patient_id]
        │     miss → FHIRPatientData.fetch() → GET FHIR $everything
        │            _fhir_cache[patient_id]       = data
        │            _fhir_focus_cache[patient_id] = data.get_clinical_focus()
        │
        ├─ handler.apply_ensemble_context(uuid)         [context_ops, two-phase]
        │     [data_lock] build EnsembleContextState(uuid)
        │     [no lock]   SOAP SetContextState → Provider
        │     success? keep binding : roll back _ensemble_devices + ensemble_uuid
        │
        └─ handler.apply_fhir_contexts(fhir_data)        [context_ops, two-phase]
              [data_lock] build WorkflowContextState with FHIR DangerCodes→CodedValue
              [no lock]   SOAP SetContextState → Provider
```

---

### 10.6 Legend & Data Structures Reference

```
[worker thread]   code runs on a per-device DeviceHandler asyncio thread
[main thread]     code runs on the Qt GUI thread (QObject signal/slot dispatch)
[aggregator.lock] guarded by SmartAlertAggregator.lock (shared mutex)
[data_lock]       guarded by DeviceHandler.data_lock (per-device mutex)
[no lock]         deliberately outside any mutex (slow network round-trips)
──►  data flow / call direction
◄──  return value / DTO handed back
```

| Structure | Owner | Guard | Shape |
|---|---|---|---|
| `_handle_to_concept` | DeviceHandler | (built once, read-only) | `handle → concept` |
| `_device_calibration` | DeviceHandler | (built once, read-only) | `concept → (roc_limit, profile)` |
| `_pipeline_suppressed` | DeviceHandler | worker thread | `set[condition_handle]` |
| `_physiological_graph` | Aggregator | `aggregator.lock` | `uuid → concept → deque[(value, ts)]` (maxlen 15) |
| `_active_alarms` | Aggregator | `aggregator.lock` | `uuid → alert_key → (evidence, ts)` (TTL 10 s) |
| `_active_ensembles` | Aggregator | `aggregator.lock` | `(patient_id, room) → uuid` |
| `_ensemble_devices` | Aggregator | `aggregator.lock` | `uuid → set[epr]` |
| `_fhir_cache` | Aggregator | (session-scoped) | `patient_id → FHIRPatientData` |
| `_profile_cache` / `_roc_cache` | DeviceProfileRepository | `repo._lock` | memoised point-query results |
| `DeviceAlertEvidence` | (immutable DTO) | — | carries calibration through the pipeline |
| `AlarmDecision` | (immutable DTO) | — | `escalate`, `risk_score`, `suppression_stage/reason` |
| Qt properties | QtDeviceHandler | main thread | `metrics[]`, `alarmStatus`, `priority`, … |

---

## 11. Test Harness — 3-Node ICU Ensemble (`MyTests/correct_provider/`)

Integration test-bed for the IHE-PCD ACM pipeline.  Simulates a real ICU room
with three concurrent SDC devices sharing an identical `(patient_id, room)` key
so the consumer's `SmartAlertAggregator` binds them into one ensemble.

### Devices

| File | DPWS Manufacturer | DPWS Model | Metric | LOINC | Alert | Priority |
|---|---|---|---|---|---|---|
| `mdib_monitor.xml` | Draeger | Infinity Monitor | HR | 8867-4 | `al_monitor_hi` | Hi |
| `mdib_vent.xml` | Draeger | Evita Ventilator | Airway Pressure | 20053-5 | `al_vent_hi` | Hi |
| `mdib_pump.xml` | BBraun | Space Perfusor | Line Pressure | 8775-2 | `al_pump_occ` | Hi |

### 45-Second Simulation Phases (`provider_ensemble.py`)

| Phase | t (s) | Active Alarms | Bayesian ∏ LR⁺ | risk score | Decision |
|---|---|---|---|---|---|
| 0 BASELINE | 0–4 | none | — | — | — |
| 1 MONO ALARM | 5–14 | Monitor | 6.60 | 0.32 | **SUPPRESS** (Stage 1) |
| 2 DUAL ALARM | 15–24 | Monitor + Vent | 6.60 × 9.80 = 64.68 | 2.45 | **SUPPRESS** (Stage 2) |
| 3 TRIPLE CRISIS | 25–34 | Monitor + Vent + Pump | 6.60 × 9.80 × 11.875 = 768 | **7.94** | **ESCALATE** ✓ |
| 4 RECOVERY | 35–44 | none | — | — | — |

Calibration profiles live in `config/clinical_db.json`.
`DeviceProfileRepository` lazy-loads them on the first point-query and each
`DeviceHandler` caches its own concepts in `_device_calibration`; the profiles
are then embedded into every `DeviceAlertEvidence` and consumed by
`ClinicalRiskFilter`.  Bayesian `prior = 0.005`
(0.5 % baseline ICU crisis prevalence).

---

*Generated on 2026-07-16*
