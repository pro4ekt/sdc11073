# Dashboard — Architecture Reference

> IEEE 11073 SDC Consumer Application with Qt/QML UI, HL7 FHIR integration, and a
> two-axis **adaptive stochastic alarm filter**.

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
   - [Adaptive Math Core (`app/alarms/`)](#adaptive-math-core-appalarms)
   - [DeviceProfileRepository](#deviceprofilerepository)
   - [PatientOverviewModel](#patientoverviewmodel)
   - [OperationLogger](#operationlogger)
   - [FHIRPatientData](#fhirpatientdata)
5. [Package `device/`](#5-package-device)
6. [QML UI (`qml/`)](#6-qml-ui-qml)
7. [Configuration (`config/`)](#7-configuration-config)
8. [Workflow](#8-workflow)
9. [Threading Model](#9-threading-model)
10. [Data Flow Diagram](#10-data-flow-diagram)
11. [The Adaptive Stochastic Alarm Model](#11-the-adaptive-stochastic-alarm-model)

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
│   ├── patientOverviewModel.py # QObject model backing PatientOverview.qml
│   ├── operationLogger.py      # OR session file logger
│   ├── fhirData.py             # HL7 FHIR REST client
│   │
│   └── alarms/                 # Adaptive stochastic alarm sub-package
│       ├── __init__.py               # Package exports (facade + math core)
│       ├── alarmCoordinator.py       # Stateless facade/router; DTOs
│       ├── ensemble_topology_manager.py  # SLOW path: ensemble topology + FHIR
│       ├── smartAlertAggregator.py   # FAST path: alarm processor + per-ensemble aggregators
│       ├── adaptive_alarm_aggregator.py  # Per-ensemble orchestrator (tick)
│       ├── math_types.py             # SensorSpec, EngineConfig, TickResult, helpers
│       ├── evidence_accumulator.py   # Confidence axis E(t) — FIR window s_j(t)
│       ├── urgency_engine.py         # Urgency axis — SDC_score, k_min, Θ_target
│       ├── hysteresis_filter.py      # Asymmetric IIR hysteresis Θ_current(t)
│       ├── clinical_context.py       # Context_Log_Odds from P_0 + odds ratios
│       └── device_profile_repo.py    # Lazy DAO over config/clinical_db.json
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
│   ├── OperationPage.qml
│   └── PatientOverview.qml
│
├── config/                     # Runtime configuration
│   ├── rules.json              # Alert rule definitions
│   ├── clinical_db.json        # Calibration DB (device profiles + math-core params)
│   └── *.xml                   # MDIB fixture files
│
├── tools/                      # Utility scripts
├── tests/                      # Unit tests (test_math_core.py — 51 cases)
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
| `app.patientOverviewModel` | QObject exposing ensemble summaries to `PatientOverview.qml` |
| `app.alarms` | **Adaptive stochastic alarm sub-package** — public API |
| `app.alarms.ensemble_topology_manager` | **SLOW path**: forms/joins/releases patient ensembles; owns the full sensor registry, FHIR caches + danger codes; performs EnsembleContext/FHIR SOAP binding. Own mutex |
| `app.alarms.smartAlertAggregator` | **FAST path**: alarm processor; owns one `AdaptiveAlarmAggregator` per ensemble; assembles the per-tick activation vector. Runs without the topology mutex |
| `app.alarms.alarmCoordinator` | **Stateless facade/router**: drives one `tick()` and maps the verdict onto `AlarmDecision`. Defines the shared `DeviceAlertEvidence` / `AlarmDecision` DTOs |
| `app.alarms.adaptive_alarm_aggregator` | Per-ensemble orchestrator: fuses the Confidence and Urgency axes into a binary escalation verdict |
| `app.alarms.math_types` | Frozen value types (`SensorSpec`, `EngineConfig`, `TickResult`) + numerical helpers |
| `app.alarms.evidence_accumulator` | Confidence axis `E(t) = Σ w_j·s_j(t)` with a ZOH FIR window |
| `app.alarms.urgency_engine` | Urgency axis: `SDC_score`, `k_min`, `Θ_target` |
| `app.alarms.hysteresis_filter` | Asymmetric IIR hysteresis producing `Θ_current(t)` |
| `app.alarms.clinical_context` | `Context_Log_Odds` from baseline `P_0` + FHIR odds ratios |
| `app.alarms.device_profile_repo` | **Lazy-loading DAO** over `config/clinical_db.json`; process-wide singleton; exports `DeviceReliabilityProfile` |
| `app.operationLogger` | Thread-safe OR-session file logger |
| `app.fhirData` | Fetches Patient/Condition/Observation from a FHIR R4 server |
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
5. Create `QQmlApplicationEngine`, expose `SdcMyConsumer` + `PatientOverviewModel` as QML context properties
6. Load `qml/Main.qml`
7. Enter Qt event loop with `app.exec()`

---

## 4. Package `app/`

### SdcMyConsumer

**File:** `app/sdcMyConsumer.py` · **Base:** `QObject`

Top-level manager. Runs the WSDiscovery loop in a background thread and maintains the map of active `DeviceHandler` workers. Exposes Qt properties and signals for QML bindings.

| Signal | Payload | Fired When |
|---|---|---|
| `deviceConnected` | `QtDeviceHandler` | New device handler ready in main thread |
| `deviceDisconnected` | `str` (epr) | Device worker stopped |
| `roomChanged` | — | `currentRoom` property changed |
| `availableRoomsChanged` | — | Set of known rooms updated |

| Method | Purpose |
|---|---|
| `start()` | Spawn the discovery thread and begin scanning |
| `stop()` | Stop all active `DeviceHandler` workers, join thread |
| `_discovery_loop()` | Async: WSDiscovery scan → spawn `DeviceHandler` per new device |
| `remove_device(epr, …)` | Called by a `DeviceHandler` on exit; cleans up maps |
| `switchRoom(new_room)` | Qt Slot: changes `currentRoom`, stops devices outside the new room |

---

### QtDeviceHandler

**File:** `app/qtDeviceHandler.py` · **Base:** `QObject`

Live QML-facing mirror of a single SDC device. Created in the worker thread and moved to the Qt main thread via `moveToThread()`. Reads happen in the main thread; updates are scheduled thread-safely via `updateTick`.

Alarm rendering is driven by the two device-side routing sets (see `DeviceHandler`):

- Handles in `_artifact_suppressed` (**SUPPRESS**-routed) are hidden entirely.
- Handles in `_warning_handles` (**WARN**-routed) are shown Yellow.
- Everything else that is active and escalated is shown Red.

| Property | Type | Description |
|---|---|---|
| `patientName` | `str` | Full name from PatientContext |
| `patientRoom` | `str` | Location from LocationContext |
| `epr` | `str` | Device endpoint reference (unique ID) |
| `deviceName` | `str` | DPWS FriendlyName |
| `metrics` | `list[dict]` | All NumericMetric states: `{handle, value, unit, label}` |
| `alarmStatus` | `str` | `Off / On / Ack / Latch / Warning / COMM_FAILURE` |
| `priority` | `str` | Alert priority: `Low / Medium / High` |
| `operations` | `list[dict]` | Available operations: `{handle, name, type}` |

---

### EnsembleTopologyManager (slow path) & SmartAlertAggregator (fast path)

The former God-Object `SmartAlertAggregator` was split into **two collaborating
components** with **separate mutexes**, so slow topology churn never blocks fast
alarm processing (and vice-versa).

| Component | File | Path | Responsibility |
|---|---|---|---|
| **EnsembleTopologyManager** | `app/alarms/ensemble_topology_manager.py` | **SLOW** | Topology mutations (form/join/release ensembles), MDIB parsing, FHIR caches + danger codes, EnsembleContext/FHIR SOAP binding. Guarded by its own `self.lock` (+ `_fhir_lock`). |
| **SmartAlertAggregator** | `app/alarms/smartAlertAggregator.py` | **FAST** | Alarm processor: TTL alarm cache, per-ensemble two-axis Bayesian math core, escalation routing. Runs **without ever taking the topology mutex**. |

#### EnsembleTopologyManager (SLOW path)

Owns *who belongs to which patient ensemble* and all network-bound work. Slow I/O
(FHIR HTTP, SOAP) is always performed **outside** `self.lock`.

| Attribute | Type | Description |
|---|---|---|
| `self.lock` | `threading.Lock` | Guards the topology maps below (slow path only) |
| `_fhir_lock` | `threading.Lock` | Serialises FHIR HTTP fetches (double-checked) |
| `_patient_to_ensemble_map` | `Dict[(patient_id, room), uuid]` | Patient+room key → ensemble UUID |
| `_ensembles_devices` | `Dict[uuid, Set[epr]]` | Member devices per ensemble |
| `_ensembles_channel_specs` | `Dict[uuid, {alert_key: SensorSpec}]` | FULL sensor registry (every channel, alarming or silent → correct \|M\|) |
| `_fhir_cache` / `_fhir_focus_cache` | `Dict[patient_id, …]` | Per-patient FHIR data + clinical focus |

Read-only snapshot getters consumed by the Alert Processor (each takes `self.lock`
briefly and returns a copy): `get_member_specs`, `get_members`, `get_member_count`,
`reverse_lookup_patient_room`, `collect_patient_danger_codes`, `get_fhir_focus`.
Entry point: `evaluate_and_bind_device(handler)`; teardown:
`release_device(epr) -> Optional[released_ensemble_uuid]`.

#### SmartAlertAggregator (FAST path)

Holds a **read-only reference** to the topology manager and pulls the snapshots
above **before** taking any of its own locks — so the two objects' locks are never
nested. It is **database-agnostic**: each device's calibration
(`reliability_profile`) is pre-fetched by `DeviceHandler` and embedded in the
`DeviceAlertEvidence` DTO, so the hot alarm path never touches `clinical_db.json`.

| Attribute | Type | Description |
|---|---|---|
| `self.lock` | `threading.Lock` | Mutex protecting the alarm/adaptive maps (fast path only) |
| `_adaptive_lock` | `threading.Lock` | Serialises aggregator lifecycle (build/tick/discard) |
| `_topology` | `EnsembleTopologyManager` | Source of membership / specs / danger codes (read-only) |
| `_ensemble_adaptive_aggregators` | `Dict[uuid, AdaptiveAlarmAggregator]` | One filter per ensemble |
| `_last_tick_ts` | `Dict[uuid, float]` | Monotonic timestamp of the last tick (for Δt) |
| `_active_alarms` | `Dict[uuid, Dict[alert_handle, (DeviceAlertEvidence, ts)]]` | **TTL alarm cache**, GC'd after `ALARM_TTL_SEC` |
| `_escalated_ensembles` | `Set[uuid]` | Latch keeping an escalation stable across ticks |
| `ALARM_TTL_SEC` | `float` = 10.0 | Age after which a cached alarm is treated as inactive |

#### Lock Hierarchy (strict order **A → B → C**)

| Level | Lock | Guards |
|---|---|---|
| **A** | `EnsembleTopologyManager.lock` | topology maps, sensor registry, FHIR caches |
| **B** | `SmartAlertAggregator.lock` | TTL alarm cache, escalation latch, adaptive maps |
| **C** | `AdaptiveAlarmAggregator._adaptive_lock` | per-instance tick/FIR/hysteresis state |

The Alert Processor takes an **A-snapshot** (released) **before** acquiring **B**,
and **B** before the per-ensemble **C** — so topology (A) is never nested inside a
processor lock. No I/O is performed under any lock; `tick()` is pure CPU
(microseconds), well inside the real-time budget.

#### Selected Methods

| Method | Component | Purpose |
|---|---|---|
| `evaluate_and_bind_device(handler)` | Topology | Extract (patient, room); form/join the ensemble; refresh the full sensor registry; fetch FHIR; send EnsembleContext SOAP; apply FHIR contexts |
| `collect_patient_danger_codes(uuid)` | Topology | Reverse-lookup ensemble → patient → normalised FHIR danger codes (feeds per-ensemble `Context_Log_Odds`) |
| `check_alert_validity(...)` | Processor | Ensemble gate. Pulls member specs + danger codes from topology; registers the alarm in the TTL cache; builds/updates the per-ensemble aggregator; injects the per-patient clinical shift; delegates to `AlarmCoordinator.evaluate()`; returns tri-state `"ESCALATE" / "WARN"` |
| `_build_specs_from_evidences(evidences)` | Processor | Build `{alert_key → SensorSpec}` from active evidence (fallback): `tpr/fpr` from `reliability_profile` (neutral 0.5/0.5 if absent), `priority = repo.get_priority(biceps_priority)` |
| `_get_math_core_params()` | Processor | Lazily build the shared `EngineConfig` + static `ClinicalContext` (baseline P₀ + OR table) from the repository (fail-open defaults) |
| `_notify_overview(...)` | Both | Push an ensemble summary to `PatientOverviewModel`. Continuous UI intensity is `sdc_score ∈ [0,1]`; colour is the orthogonal tri-state |
| `_propagate_escalation_to_devices(uuid)` | Processor | On first crisis, clear device suppression sets so every member device shows Red |

---

### AlarmCoordinator

**File:** `app/alarms/alarmCoordinator.py`

A thin, **stateless router** between the SDC layer and the math core. It owns no
per-ensemble state; every stochastic quantity lives inside the caller-owned
`AdaptiveAlarmAggregator`. On each alarm event it drives exactly one `tick()`,
logs the full two-axis telemetry as a structured audit record, and returns an
immutable `AlarmDecision`.

The escalation verdict is strictly binary — **no** logistic/sigmoid, **no** `[0,10]`
risk projection, **no** suppression "stages":

```
Escalate  ⇔  E(t) ≥ Θ_current(t)
```

| Method | Purpose |
|---|---|
| `evaluate(aggregator, sensor_states, dt_step, contributing_devices)` | Drive one `tick()`; emit a structured audit line; return `AlarmDecision` |

#### `DeviceAlertEvidence` (frozen input DTO)

| Field | Meaning |
|---|---|
| `alert_key` | MDIB AlertCondition handle (logging / sensor id) |
| `metric_concept` | LOINC/MDC code of the triggering metric |
| `manufacturer`, `model` | DPWS ThisModel identifiers (logging + profile lookup) |
| `ensemble_uuid` | UUID of the patient ensemble |
| `biceps_priority` | BICEPS `AlertCondition.Priority`: `'Hi' / 'Me' / 'Lo' / 'None'` |
| `reliability_profile` | `DeviceReliabilityProfile | None` — pre-fetched TPR/FPR; the aggregator turns it into `SensorSpec` (`w_j`, `P_j`). `None` → neutral channel (`w_j = 0`) |

#### `AlarmDecision` (frozen output DTO)

| Field | Meaning |
|---|---|
| `escalate: bool` | `True ⇔ E(t) ≥ Θ_current(t)` |
| `contributing_devices: int` | Devices used in the tick |
| `evidence: float` | `E(t) = Σ w_j·s_j(t)` — confidence axis (LHS) |
| `theta_current: float` | `Θ_current(t)` — hysteresis-smoothed barrier (RHS) |
| `delta_t: float` | `Δt` used by the hysteresis kinetics |
| `sdc_score: float` | `SDC_score(t) ∈ [0,1]` — normalised severity (UI intensity) |
| `k_min: int` | `k_min(t) ∈ [2,|M|]` — dynamic consensus quorum |
| `theta_target: float` | `Θ_target(t)` — threshold before IIR smoothing |
| `rho_decay: float` | `ρ(t) = (1−SDC)/T` — hysteresis relaxation rate |

---

### Adaptive Math Core (`app/alarms/`)

Six collaborating modules implement the model in [Section 11](#11-the-adaptive-stochastic-alarm-model).
All math is **string-agnostic**: BICEPS priority strings are mapped to integers
exactly once, at the SDC↔core boundary.

| Module | Class | Responsibility |
|---|---|---|
| `math_types.py` | `SensorSpec` | Frozen per-channel spec: `sensor_id, tpr, fpr, priority: int`, property `w_j = ln(TPR/FPR)` |
| | `EngineConfig` | Frozen tuning: `horizon_T = 10.0`, `alpha = 0.7` |
| | `TickResult` | Verdict + telemetry: `is_escalated, current_theta, evidence, active_delta_t, sdc_score, k_min, theta_target, rho_decay` |
| `evidence_accumulator.py` | `EvidenceAccumulator` | Confidence axis. ZOH FIR window `s_j(t)`; `E(t) = Σ w_j·s_j(t)`; `w̄`; raw activations `a_j(t)` (BICEPS `bool`) |
| `urgency_engine.py` | `UrgencyEngine` | Urgency axis. `SDC_score(t)`, `k_min(t)`, `Θ_target(t)`. `theta_target()` returns a `UrgencyResult` NamedTuple |
| `hysteresis_filter.py` | `HysteresisFilter` | Asymmetric IIR: Fast Attack / Context-Aware Slow Release; `ρ(t) = (1−SDC)/T` |
| `clinical_context.py` | `ClinicalContext` | `Context_Log_Odds = ln(O_0) + Σ R_d·ln(OR_d)`, with `O_0 = P_0/(1−P_0)` |
| `adaptive_alarm_aggregator.py` | `AdaptiveAlarmAggregator` | Per-ensemble orchestrator composing all four collaborators; owns the recursive hysteresis state and FIR buffers |

**`AdaptiveAlarmAggregator` — per-tick pipeline** (all under its own `_adaptive_lock`):

1. record activations `a_j(t)` (ZOH: an absent known sensor holds its prior state) and push a sample into the FIR buffers;
2. Confidence: `E(t)`, `w̄`, raw `a_j(t)` from `EvidenceAccumulator`;
3. Urgency: `Θ_target, k_min, SDC_score` from priorities `P_j` + raw activations;
4. Hysteresis: `Θ_current = IIR(Θ_target, SDC_score, Δt)`;
5. verdict `E(t) ≥ Θ_current(t)` + full telemetry → `TickResult`.

Notable behaviours: an empty ensemble (`|M| = 0`) returns a safe, non-escalating
default; a missing `ClinicalContext` falls back to the canonical low-prior
baseline `P_0 = 0.005` (never a neutral `0.0`, which would mean `P = 50 %`);
`sensor_ids` is ordered by **descending priority** for UI/logs; `update_specs()`
rebuilds the ensemble (preserving FIR history for known channels) and cold-restarts
the hysteresis because the `|M|`-dependent threshold scale changed.

---

### DeviceProfileRepository

**File:** `app/alarms/device_profile_repo.py`

Lazy-loading **Repository / DAO** for `config/clinical_db.json`. The file is read
on the **first query** (double-checked locking), memoised thereafter, and shared
via a process-wide singleton (`get_repository()`), so the JSON is parsed **at most
once per process**.

**Fail-open contract:** a missing *or corrupt* file (invalid JSON / bad encoding)
logs `CRITICAL` and degrades to an empty in-memory DB (`_raw_clinical_db = {}`) —
every lookup then returns a conservative default and never raises, so calibration
problems can never *suppress* an alarm.

#### `DeviceReliabilityProfile` (frozen DTO)

| Field | Meaning |
|---|---|
| `true_positive_rate` | `P(alarm | true event)` — TPR |
| `false_positive_rate` | `P(alarm | no event)` — FPR;  `w_j = ln(TPR/FPR)` |

> The JSON on disk still uses the historical keys `sensitivity` / `false_alarm_rate`;
> the repository translates them into the TPR/FPR fields at read time.

#### Methods

| Method | Purpose |
|---|---|
| `get_profile(manufacturer, model, metric_code)` | Point-query → `DeviceReliabilityProfile | None` (miss → caller uses neutral 0.5/0.5). Memoised |
| `get_base_prob()` | Baseline crisis probability `P_0` (default `0.005`) → fed to `ClinicalContext` |
| `get_odds_ratios()` / `get_odds_ratio(code)` | `{diagnosis_code → OR_d}` map; unknown code → `1.0` (neutral) |
| `get_priority_map()` / `get_priority(biceps_priority)` | BICEPS→`P_j` map (default `None/Lo/Me/Hi = 0/1/2/3`) |
| `get_filter_params()` | `(horizon_T, alpha)` for the math core (defaults `10.0`, `0.7`) |
| `get_repository()` | Process-wide singleton; thread-safe; defers I/O to first query |

---

### PatientOverviewModel

**File:** `app/patientOverviewModel.py` · **Base:** `QObject`

Backs `PatientOverview.qml`. Worker threads call `updateEnsemble()` /
`removeEnsemble()`; commands are queued and marshalled to the Qt main thread via a
`Signal`, where `_applyPending()` rebuilds the model and emits `patientsChanged`.

Each ensemble dict exposes: `ensembleUuid, patientName, room, deviceCount,
isEscalated, isWarning, sdcScore`. `sdcScore ∈ [0,1]` (the model's `SDC_score(t)`)
is rendered by QML as a **percentage** intensity; the tri-state colour is driven by
the orthogonal `isEscalated` / `isWarning` booleans.

---

### OperationLogger

**File:** `app/operationLogger.py`

Thread-safe structured logger for operative-room sessions. Writes a timestamped
`.txt` recording device events, alarms, metrics, and context changes.

| Method | Purpose |
|---|---|
| `__init__(patient_ctx, ensemble_uuid, output_dir)` | Open `OR_session_{Family}_{Given}_{ts}.txt`; write header |
| `finalize()` | Write footer with end timestamp; return the file path |
| `log(device_epr, event_type, data)` | Thread-safe append |
| `log_metric / log_alarm / log_context_applied / log_device_event / log_ensemble` | Typed helpers |

---

### FHIRPatientData

**File:** `app/fhirData.py`

HL7 FHIR R4 REST client. Fetches a single Bundle with Patient demographics, active
Conditions, and recent Observations for a patient ID.

| Method | Purpose |
|---|---|
| `fetch(patient_id)` | Query Patient + Condition + Observation resources |
| `get_name()` | Formatted full name (Family, Given) |
| `get_danger_codes()` | `list[dict]` of `{code, system, display}` from active Conditions — feed the `OR_d` lookup in `ClinicalContext` |
| `get_clinical_focus()` | Monitoring-focus rules from Condition Extensions |
| `get_vital_measurements()` | `{weight, height}` from Observations (LOINC 29463-7, 8302-2) |

---

## 5. Package `device/`

### DeviceHandler

**File:** `device/handler.py` · **Base:** `threading.Thread`

One background worker per SDC device, running its own asyncio loop. Manages the
full lifecycle: discovery → TLS → MDIB init → subscription → monitoring → disconnect.

#### Key Attributes

| Attribute | Type | Description |
|---|---|---|
| `consumer` / `mdib` | `SdcConsumer` / `ConsumerMdib` | Live connection + MDIB mirror |
| `data_lock` | `threading.Lock` | Protects `consumer` / `mdib` |
| `ensemble_uuid` | `str | None` | Ensemble this device is bound to |
| `manufacturer` / `model` | `str` | DPWS ThisModel (profile lookup) |
| `_handle_to_concept` | `dict[str, str]` | MDIB handle → LOINC concept |
| `_device_calibration` | `dict[str, DeviceReliabilityProfile | None]` | Per-concept profile pre-fetched from the repository |
| `_artifact_suppressed` | `set[str]` | **SUPPRESS**-routed handles — hidden from UI |
| `_warning_handles` | `set[str]` | **WARN**-routed handles — shown Yellow |
| `_suppression_lock` | `threading.Lock` | Guards the two routing sets |

#### Selected Methods

| Method | Purpose |
|---|---|
| `_phase_init_mdib()` | Init `ConsumerMdib`; call `_build_semantic_map()`, `_prefetch_device_calibration()`, `_log_alert_map()` |
| `_build_semantic_map()` | Map each `NumericMetricDescriptor` handle → LOINC concept |
| `_prefetch_device_calibration()` | Repository pattern: one `get_profile()` point-query per concept; cache the profile so the hot path never hits the DB |
| `on_metric_update(...)` | Push new values to `SmartAlertAggregator`; rate-limited UI refresh (≤1 Hz) |
| `on_alert_update(...)` | Adaptive alarm filter: `check_alert_validity()` → map tri-state verdict onto the routing sets → log transition → ack tracking |
| `acknowledge_alarm(...)` | Delegate to `AlarmManager` |
| `apply_ensemble_context / apply_fhir_contexts` | Delegate to `context_ops` |

The `on_alert_update` routing maps the aggregator verdict to the device sets:
`SUPPRESS → _artifact_suppressed` (hide), `WARN → _warning_handles` (Yellow),
`ESCALATE → clear both` (Red).

### AlarmManager

**File:** `device/alarm_manager.py` — alarm acknowledgement (DEV-31) and
ack-timeout re-raising per device. `ACK_TIMEOUT_SEC = 30`; a held Ack older than
that is re-raised to `On`.

### context_ops · ssl_builder · logging_setup · patches

- **`context_ops.py`** — `apply_ensemble_context()` and `apply_fhir_contexts()`, both two-phase (build state under `data_lock`, send SOAP without the lock).
- **`ssl_builder.py`** — `build_ssl_container()` tries cert candidates in priority order; returns `certloader.SSLContextContainer`.
- **`logging_setup.py`** — rotating file + console handlers; suppresses repetitive `GetContextStates HTTP 400` spam.
- **`patches.py`** — idempotent monkey-patches for sdc11073 deserialization bugs.

---

## 6. QML UI (`qml/`)

| File | Purpose |
|---|---|
| `Main.qml` | Root window; `StackView` navigation |
| `LoginPage.qml` | Room / adapter selection |
| `MainPage.qml` | ICU overview grid of device cards |
| `DevicePage.qml` | Single-device detail view + silence button |
| `MetricPage.qml` | Full metric list for one device |
| `OperationPage.qml` | Operations list + SDC operation invocation |
| `PatientOverview.qml` | Per-patient ensemble cards. Tri-state colour from `isEscalated` / `isWarning`; **Severity %** badge from `sdcScore` (blinking border/dot only when escalated) |

---

## 7. Configuration (`config/`)

| File | Purpose |
|---|---|
| `rules.json` | Alert rule definitions (thresholds, LOINC codes, priority mappings) |
| `clinical_db.json` | **Calibration DB** (see below). Read lazily by `DeviceProfileRepository` |
| `*.xml` | MDIB fixture files for testing / offline development |

### `clinical_db.json` schema

| Top-level key | Meaning |
|---|---|
| `base_prob_P0` | Baseline ICU crisis probability `P_0` (e.g. `0.005`) → `O_0 = P_0/(1−P_0)` |
| `filter_params` | `{ horizon_T, alpha }` for the math core |
| `priority_map` | BICEPS→`P_j`: `{ None:0, Lo:1, Me:2, Hi:3 }` |
| `odds_ratios` | `{ diagnosis_code → OR_d }` for `Context_Log_Odds` |
| `device_profiles` | `manufacturer → model → metric_code → { sensitivity, false_alarm_rate }` (legacy keys, translated to TPR/FPR at read time) |

Keys beginning with `_` are treated as comments and skipped by the DAO.

---

## 8. Workflow

### 8.1 Application Startup

```
main.py
  ├─ argparse (--room, --tls, --no_tls, --ip)
  ├─ basic_logging_setup()
  ├─ QGuiApplication()
  ├─ SdcMyConsumer(room, tls_mode, adapter_ip)
  │     └─ SmartAlertAggregator()  →  PatientOverviewModel()
  ├─ SdcMyConsumer.start()             ← spawns discovery thread
  ├─ QQmlApplicationEngine() → load("qml/Main.qml")
  └─ app.exec()                        ← Qt event loop
```

### 8.2 Device Connection Lifecycle

```
DeviceHandler.run() → _worker_logic()
  ├─ patches.apply_patches(); logging setup
  ├─ _phase_connect()            ← TLS auto-detect/fallback; GetMdib
  ├─ _phase_init_mdib()          ← semantic map + calibration prefetch
  ├─ _phase_check_location()     ← room filter
  ├─ _phase_subscribe()          ← bind on_metric_update / on_alert_update
  ├─ _phase_aggregate_on_connect() ← ensemble binding + FHIR context
  ├─ _phase_snapshot_initial_alerts()
  ├─ _phase_setup_qt()           ← create QtDeviceHandler; emit deviceConnected
  └─ _monitoring_loop()          ← ping + ack-timeout processing
        └─ [finally] _graceful_shutdown(); manager.remove_device(epr)
```

### 8.3 Alarm Handling

```
on_alert_update(alert_by_handle)                      [worker thread]
  └─ for (handle, state):
       ├─ (metric_concept, biceps_priority) = _lookup_alert_concepts(handle, state)
       │     reliability_profile = _device_calibration[metric_concept]   (pre-fetched)
       │
       ├─ routing = aggregator.check_alert_validity(
       │       ensemble_uuid, handle, metric_concept, biceps_priority,
       │       manufacturer, model, device_epr, reliability_profile)
       │     │  [A] register/refresh TTL cache; GC stale; assemble evidence set
       │     │  [B] build/refresh per-ensemble AdaptiveAlarmAggregator
       │     │  [C] AlarmCoordinator.evaluate → aggregator.tick(sensor_states, Δt)
       │     │        E(t) = Σ w_j·s_j(t)
       │     │        Θ_current(t) = IIR(Θ_target, SDC_score, Δt)
       │     │        escalate ⇔ E(t) ≥ Θ_current(t)
       │     └─ tri-state: 'ESCALATE' (Red) | 'WARN' (Yellow)
       │
       ├─ routing → device sets:
       │     SUPPRESS → _artifact_suppressed ;  WARN → _warning_handles ;
       │     ESCALATE → clear both (+ propagate to ensemble devices)
       │
       ├─ AlertConditionState → _log_condition_transition(...)   (Presence bool)
       └─ AlertSignalState    → _log_signal_transition(...)      (Presence enum)
```

### 8.4 UI Update Pipeline

```
on_metric_update / on_alert_update  → rate limiter (≤1 Hz)
  └─ QtDeviceHandler.scheduleUpdate() → emit updateTick   (cross-thread)
        └─ [main thread] handleUpdateTick() → update_data()
              ├─ read Location/Patient/DeviceName
              ├─ read AlertSignal priority matrix → alarmStatus
              │     (skip _artifact_suppressed; mark _warning_handles as Yellow)
              └─ rebuild metrics[] / operations[]

SmartAlertAggregator._notify_overview(...)   → PatientOverviewModel.updateEnsemble()
  └─ queue + Signal → _applyPending() → patientsChanged → PatientOverview.qml rebuild
```

### 8.5 Ensemble & FHIR Binding

```
evaluate_and_bind_device(handler)
  ├─ _extract_patient_and_room(handler)     WorkflowContext → PatientContext (fallback)
  ├─ key=(patient_id, room) → reuse/create ensemble uuid; register device
  ├─ _get_or_fetch_fhir_data(patient_id)    [no lock — slow HTTP]  → danger codes
  ├─ apply_ensemble_context(uuid)           [two-phase SOAP]
  └─ apply_fhir_contexts(fhir_data)         [two-phase SOAP] → DangerCode → CodedValue
```

FHIR `DangerCode`s drive `ClinicalContext.Context_Log_Odds` for the ensemble via
`SmartAlertAggregator`, which injects the shift into the per-ensemble aggregator.

---

## 9. Threading Model

| Thread | Creator | What Runs There |
|---|---|---|
| **Qt main thread** | `QGuiApplication` | Qt event loop, all `QObject` signal/slot dispatch, QML, `update_data()`, `PatientOverviewModel._applyPending()` |
| **Discovery thread** | `SdcMyConsumer.start()` | asyncio `_discovery_loop()`; spawns `DeviceHandler` threads |
| **DeviceHandler thread** (×N) | `DeviceHandler.start()` | Per-device asyncio loop: connect, subscribe, monitor, ping; runs `on_alert_update` (drives one `tick()`) |

**Cross-thread communication**

- Worker → Qt: `scheduleUpdate()` / `PatientOverviewModel._pendingUpdate` emit thread-safe Qt signals delivered on the main thread (QueuedConnection).
- MDIB reads in `update_data()` use a non-blocking `data_lock.acquire(blocking=False)` to avoid stalling the UI.
- Alarm evaluation acquires locks in the strict order **A → B → C** and performs **no I/O** while holding any of them; `tick()` completes in microseconds.

---

## 10. Data Flow Diagram

```
                             SDC NETWORK (LAN)
    Provider A          Provider B          Provider C      (SDC devices)
       │  WS-Discovery / MDPWS / WS-Eventing push  │
       ▼                    ▼                    ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                     CONSUMER PROCESS (single OS process)                     │
│  Discovery thread → spawn DeviceHandler (1 asyncio thread per device)        │
│      │                                                                       │
│      │ on connect: evaluate_and_bind_device(handler)                         │
│      ▼                                                                       │
│  ╔═════════════════════════════════════════════╗   SLOW PATH (own mutex)    │
│  ║        EnsembleTopologyManager               ║                            │
│  ║  • form/join/release ensembles               ║                            │
│  ║    (patient_id, room) → ensemble_uuid        ║   HTTP $everything         │
│  ║  • full sensor registry (all channels →|M|)  ║──────────────┐            │
│  ║  • FHIR fetch + danger-code cache            ║              ▼            │
│  ║  • EnsembleContext / FHIR SOAP write-back    ║   ┌────────────────────┐   │
│  ╚═══════════════╤══════════════════════╤═══════╝   │ External FHIR R4   │   │
│    snapshots:    │                      │ SetContextState  REST server   │   │
│    get_member_specs / get_members /      │ (SOAP)   └────────────────────┘   │
│    collect_patient_danger_codes /        │            back to Provider MDIB  │
│    reverse_lookup_patient_room           ▼                                   │
│      │                          (WorkflowContext DangerCodes)                │
│      │ on alarm: check_alert_validity(...)                                   │
│      ▼                                                                       │
│  ┌──────────────────────┐        ┌──────────────────────┐                   │
│  │ SmartAlertAggregator  │        │  QtDeviceHandler ×N  │                   │
│  │  FAST PATH (own mutex)│        │  (main Qt thread)    │                   │
│  │  • TTL alarm cache     │       └───────────┬──────────┘                   │
│  │  • per-ensemble         │                  │ Qt bindings                  │
│  │    AdaptiveAggregator   │                  ▼                              │
│  └───────────┬───────────┘           ┌──────────────┐                       │
│              │ evaluate()             │  QML Engine  │                       │
│              ▼                        │  MainPage /  │                       │
│  ┌──────────────────────┐            │  Patient-    │                       │
│  │   AlarmCoordinator    │──tick()──► │  Overview    │                       │
│  │  (stateless router)   │ TickResult └──────────────┘                       │
│  └──────────┬───────────┘                                                    │
│             │ E(t) ≥ Θ_current(t)                                            │
│             ▼                                                                │
│  ┌───────────────────────┐   DTO   ┌───────────────────────────────┐        │
│  │ Adaptive math core:    │◄────────│ DeviceProfileRepository        │       │
│  │ Evidence / Urgency /    │        │ (singleton, lazy DAO)          │       │
│  │ Hysteresis / Context    │        │ reads config/clinical_db.json  │       │
│  └───────────────────────┘        └───────────────────────────────┘        │
└────────────────────────────────────────────────────────────────────────────┘
```

**Path split.** The **SLOW path** (`EnsembleTopologyManager`, own mutex) does all
network-bound work: ensemble formation, the full sensor registry that fixes `|M|`,
the FHIR `$everything` fetch + danger-code cache, and the EnsembleContext / FHIR
`SetContextState` SOAP write-back to the provider's MDIB. The **FAST path**
(`SmartAlertAggregator`, own mutex) never takes the topology lock: on each alarm it
pulls read-only snapshots (member specs, danger codes, patient/room) from the
topology manager, then runs only the pure-CPU math loop (Evidence / Urgency /
Hysteresis / per-ensemble Context) and routes the binary verdict.

**Calibration path (once per process):** `clinical_db.json` → `DeviceProfileRepository`
(lazy, memoised) → `DeviceHandler._prefetch_device_calibration()` (one point-query
per concept at init) → cached in `_device_calibration` → embedded into each
`DeviceAlertEvidence` / read by the topology registry → turned into `SensorSpec`
(`w_j`, `P_j`) inside the aggregator. The hot alarm path performs **no** disk I/O.

---

## 11. The Adaptive Stochastic Alarm Model

The core aggregates fragmented physiological metrics in log-odds space so that
independent evidence adds linearly. It decouples the problem into **two orthogonal
axes** and compares them with a single binary condition.

### 11.1 Clinical context (baseline shift)

Baseline crisis probability `P_0` → base odds, individualised by the patient's
active diagnoses `D` (FHIR danger codes) with odds ratios `OR_d`, gated by a
relevance indicator `R_d(M) ∈ {0,1}` (does the ensemble monitor a parameter
related to `d`?):

```
O_0 = P_0 / (1 − P_0)
Context_Log_Odds = ln(O_0) + Σ_{d∈D} R_d(M) · ln(OR_d)
```

### 11.2 Confidence axis — E(t)  (left-hand side)

Each VMD channel `j` has a static reliability weight from its DataSheet, and a
smoothed activation density over a sliding window `T` (FIR boxcar):

```
w_j    = ln(TPR_j / FPR_j)
s_j(t) = (1/T) Σ_{k=0}^{T-1} a_j(t−k)            ∈ [0,1]
E(t)   = Σ_{j∈M} w_j · s_j(t)
```

`a_j(t) ∈ {0,1}` is the raw binary alarm (BICEPS boolean `Presence`).

### 11.3 Urgency axis — Θ_current(t)  (right-hand side)

Static SDC priorities `P_j ∈ {0,1,2,3}` (None/Lo/Me/Hi) give an instantaneous
threat `v_j(t) = P_j · a_j(t)` and a normalised severity index:

```
SDC_score(t) = α · ( max_j v_j / P_max )  +  (1−α) · ( Σ_j v_j / Σ_j P_j )   ∈ [0,1]
```

Higher severity lowers the required cross-validating quorum (bounded to `[2,|M|]`
— the topological fail-safe against single-sensor artifacts):

```
k_min(t)     = ⌊ |M| − (|M| − 2) · SDC_score(t) ⌋
w̄            = (1/|M|) Σ_{j∈M} w_j
Θ_target(t)  = k_min(t) · w̄ − Context_Log_Odds
```

An asymmetric first-order IIR filter turns `Θ_target` into the applied barrier
`Θ_current`, giving zero-latency response to deterioration and hysteretic
resistance to chatter:

```
                 ⎧ Θ_target(t),                                   Θ_target ≤ Θ_current(t−1)   (Fast Attack)
Θ_current(t) =   ⎨
                 ⎩ Θ_target(t) + (Θ_current(t−1) − Θ_target(t))·e^(−ρ(t)·Δt),  otherwise    (Slow Release)

ρ(t) = (1 − SDC_score(t)) / T
```

Under high threat `ρ → 0` (memory freezes, preventing premature de-escalation);
as threat clears `ρ → 1/T` (relaxation re-aligns with the window `T`). `Δt` makes
the filter invariant to variable polling rates.

### 11.4 Escalation condition

```
Escalate  ⇔  E(t) ≥ Θ_current(t)
```

A pure comparison of two log-odds quantities — **no** logistic function, **no**
normalised-margin projection, **no** `[0,10]` risk score. The verdict is binary;
`SDC_score ∈ [0,1]` is the first-class quantity used for the continuous UI
intensity indicator.

### 11.5 Unit tests

`tests/test_math_core.py` (51 cases, groups A–I) covers numerical helpers,
`SensorSpec`/`w_j`, the FIR window, `ClinicalContext` (P→O conversion and
clipping), `SDC_score`/`k_min`, hysteresis kinetics, full integration ticks
(including hot-plug and priority ordering), and the repository fail-open paths.

---

*Rewritten 2026-08-31 — aligned with the two-axis adaptive stochastic alarm model.*

