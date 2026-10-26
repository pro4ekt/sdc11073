# Testbed provider MDIBs — design notes

Four MDIB files, one per test device, derived from `docs/reports/MDIB_ANALYSIS.md` (Part C is binding) with `testbed/mdib/template/correct_mdib.xml` as the structural template. Validation: `tools/validate_mdib.py` (schema, 0 violations each) and `tools/check_mdib_rules.py` (cross-file rules, 0 failures).

All four files have the same shape: `msg:GetMdibResponse` → `msg:Mdib` → one `pm:Mds` with `Type`, `AlertSystem` (one condition, one signal), `Sco` (three operations), `SystemContext` (four context descriptors), `Clock`, one `Vmd` → one `Channel` → one `NumericMetric`; and a complete `pm:MdState`. Every handle starts with the device prefix, so the 72 handles are unique across all files (B2). Every `CodedValue` carries an explicit `CodingSystem` (C3).

## Device tables

Common to all: `Kind="Phy"`, `Manifestation="Aud"`, `AcknowledgementSupported="true"`, `SelfCheckPeriod="PT10S"`, `MetricCategory="Msrmt"`, `MetricAvailability="Cont"`, `Resolution="1"`, `DeterminationPeriod="PT1S"`, `MetricQuality/@Validity="Vld"`, all `OperatingMode="En"`, `ClockState/@RemoteSync="false"`.
**Adjustable:** `Priority` and `Latching` (currently the brief's defaults and `Latching="false"`), `SelfCheckPeriod`, `TechnicalRange`, `RemoteSync`.

> **Stack assignment (updated 2026-09-30):** during development the pulse oximeter (`spo2`) and ventilator (`vent`) were additionally run on a second, C++-based SDC stack; ECG (`ecg`) and arterial blood pressure (`abp`) on Python sdc11073. Current files for all four devices: `*_mdib_v2.xml` (the single-VMD first versions were removed).

### ecg — ECG monitor — `ecg_mdib_v2.xml`
| Item | Handle / value |
|---|---|
| MDS / VMD / Channel | `ecg_mds` (Type `DEV_ECG_MONITOR`, private system, see below) / `ecg_vmd` / `ecg_channel` |
| Metric | `ecg_metric_hr`, LOINC `8867-4` Heart rate, UCUM `/min`, TechnicalRange 0–300, initial **75** |
| Alert system | `ecg_alert_system`, SelfCheckPeriod PT10S |
| Alert condition | `ecg_alert_cond_tachycardia`, Type SNOMED CT `3424008` Tachycardia, **Priority Hi**, Source `ecg_metric_hr` |
| Alert signal | `ecg_alert_signal_tachycardia`, ConditionSignaled → condition, **Latching false** |
| Operations | `ecg_op_ack_tachycardia` (SetAlertState → signal, ModifiableData Presence), `ecg_op_set_ensemble_ctx` (→ `ecg_ctx_ensemble`), `ecg_op_set_workflow_ctx` (→ `ecg_ctx_workflow`) |
| Contexts | `ecg_system_context`: `ecg_ctx_patient`, `ecg_ctx_location`, `ecg_ctx_ensemble` (state `ecg_ctx_ensemble_state`, ContextAssociation No), `ecg_ctx_workflow` |
| Clock | `ecg_clock` |

### vent — ventilator — `vent_mdib_v2.xml` (four VMDs)
| Item | Handle / value |
|---|---|
| MDS / VMD / Channel | `vent_mds` (Type `DEV_VENTILATOR`) / `vent_vmd` / `vent_channel` |
| Metric | `vent_metric_rr`, LOINC `9279-1` Respiratory rate, UCUM `/min`, TechnicalRange 0–120, initial **16** |
| Alert system | `vent_alert_system`, PT10S |
| Alert condition | `vent_alert_cond_tachypnoea`, SNOMED CT `271823003` Tachypnea, **Priority Me**, Source `vent_metric_rr` |
| Alert signal | `vent_alert_signal_tachypnoea`, **Latching false** |
| Operations | `vent_op_ack_tachypnoea`, `vent_op_set_ensemble_ctx`, `vent_op_set_workflow_ctx` |
| Contexts | `vent_system_context`: `vent_ctx_patient`, `vent_ctx_location`, `vent_ctx_ensemble` (state `vent_ctx_ensemble_state`), `vent_ctx_workflow` |
| Clock | `vent_clock` |

### spo2 — pulse oximeter — `spo2_mdib_v2.xml` (two VMDs)
| Item | Handle / value |
|---|---|
| MDS / VMD / Channel | `spo2_mds` (Type `DEV_PULSE_OXIMETER`) / `spo2_vmd` / `spo2_channel` |
| Metric | `spo2_metric_spo2`, LOINC `59408-5` SpO2 by pulse oximetry, UCUM `%`, TechnicalRange 0–100, initial **97** |
| Alert system | `spo2_alert_system`, PT10S |
| Alert condition | `spo2_alert_cond_desaturation`, SNOMED CT `449171008` Oxygen saturation below reference range, **Priority Me**, Source `spo2_metric_spo2` |
| Alert signal | `spo2_alert_signal_desaturation`, **Latching false** |
| Operations | `spo2_op_ack_desaturation`, `spo2_op_set_ensemble_ctx`, `spo2_op_set_workflow_ctx` |
| Contexts | `spo2_system_context`: `spo2_ctx_patient`, `spo2_ctx_location`, `spo2_ctx_ensemble` (state `spo2_ctx_ensemble_state`), `spo2_ctx_workflow` |
| Clock | `spo2_clock` |

### abp — invasive arterial blood pressure — `abp_mdib_v2.xml`
| Item | Handle / value |
|---|---|
| MDS / VMD / Channel | `abp_mds` (Type `DEV_ART_BP_MONITOR`) / `abp_vmd` / `abp_channel` |
| Metric | `abp_metric_map`, LOINC `8478-0` Mean blood pressure, UCUM `mm[Hg]`, TechnicalRange 0–300, initial **85** |
| Alert system | `abp_alert_system`, PT10S |
| Alert condition | `abp_alert_cond_hypotension`, SNOMED CT `45007003` Low blood pressure, **Priority Hi**, Source `abp_metric_map` |
| Alert signal | `abp_alert_signal_hypotension`, **Latching false** |
| Operations | `abp_op_ack_hypotension`, `abp_op_set_ensemble_ctx`, `abp_op_set_workflow_ctx` |
| Contexts | `abp_system_context`: `abp_ctx_patient`, `abp_ctx_location`, `abp_ctx_ensemble` (state `abp_ctx_ensemble_state`), `abp_ctx_workflow` |
| Clock | `abp_clock` |

### Code systems used
| Purpose | CodingSystem URI | Notes |
|---|---|---|
| Metric type | `http://loinc.org` | LOINC codes; all four pass the LOINC mod-10 check digit (checker R7). |
| Unit | `http://unitsofmeasure.org` | UCUM case-sensitive codes `/min`, `%`, `mm[Hg]`. |
| Alert condition type | `http://snomed.info/sct` | SNOMED CT clinical findings (Tachycardia 3424008, Tachypnea 271823003, Oxygen saturation below reference range 449171008, Low blood pressure 45007003). |
| MDS device kind | `https://github.com/pro4ekt/sdc11073/blob/master/testbed/mdib/README.md` | **Private code system defined by this README** (real, resolvable URI of the repository). Codes: `DEV_ECG_MONITOR`, `DEV_VENTILATOR`, `DEV_PULSE_OXIMETER`, `DEV_ART_BP_MONITOR`. See open question 1. |

## DPWS identity (runtime arguments of each provider)

Manufacturer, ModelName and FriendlyName are DPWS `ThisModel`/`ThisDevice` elements, not MDIB content, and are mandatory (MDIB_ANALYSIS A5). Example values, **not tied to `config/clinical_db.json`**:

| Device | Manufacturer | ModelName | FriendlyName (example, per instance) |
|---|---|---|---|
| ecg | `SDC Testbed` | `ECG Monitor T1` | `Testbed ECG monitor, bed <bed>` |
| vent | `SDC Testbed` | `Ventilator T1` | `Testbed ventilator, bed <bed>` |
| spo2 | `SDC Testbed` | `Pulse Oximeter T1` | `Testbed pulse oximeter, bed <bed>` |
| abp | `SDC Testbed` | `Arterial BP Monitor T1` | `Testbed arterial BP monitor, bed <bed>` |

FriendlyName must always be present: the Dashboard's fallback path for a missing FriendlyName raises and aborts its UI update (MDIB_ANALYSIS B6). Manufacturer + ModelName + metric LOINC code form the key of the Dashboard's reliability-profile lookup; a device without a matching `clinical_db.json` entry is still displayed and alarms locally (Yellow) but is excluded from the ensemble evidence set (B6, C-5/6). Whether and how to add entries is a Dashboard-side decision (open question 2).

## Runtime behaviour the provider code must implement

The files deliberately contain nothing per-run. For them to work with the Dashboard, each provider (Python or C++) must:

1. **Create three context states at startup from its arguments** (descriptors are in the file, states are not):
   - `<p>_ctx_patient` → `PatientContextState`, `ContextAssociation="Assoc"`, `Identification` with `Root` = a URI of the patient-id namespace (e.g. `urn:uuid:…` or `urn:oid:…`, never a bare OID) and `Extension` = **patient identifier argument**; `CoreData` names optional (shown in the UI).
   - `<p>_ctx_location` → `LocationContextState`, `Assoc`, `LocationDetail` with `Room` = **room argument** and `Bed` = **bed argument** (`Facility`/`PoC` optional). Omit this state entirely for the "provider without LocationContext" topology case; the Dashboard then accepts the device but never forms an ensemble for it (C-b).
   - `<p>_ctx_workflow` → `WorkflowContextState`, `Assoc`, `WorkflowDetail/Patient/Identification` with the **same Root/Extension as the patient context**. The Dashboard reads the patient id from here first (B1) and needs this state to exist to write FHIR DangerCodes (B5); a workflow state without a patient identification is schema-invalid (`Patient` and its `Identification` are mandatory).
   - All devices of one bed must use byte-identical patient-id and room strings (ensemble key, C-7).
2. **Ensemble context**: leave `<p>_ctx_ensemble_state` as loaded; accept the Dashboard's `SetContextState` on `<p>_op_set_ensemble_ctx`, which updates that state in place (see decision R4). Accept `SetContextState` on `<p>_op_set_workflow_ctx` (replaces `DangerCode` list, keeps `Patient`).
3. **Alarm**: set `AlertConditionState/@Presence` and `AlertSignalState/@Presence` (`On`/`Off`) together on each edge, with `DeterminationTime` on the condition. Accept or (for the "refusing" role) reject `SetAlertState` on `<p>_op_ack_*`; the Dashboard sends a copy of the current `AlertSignalState` with `Presence="Ack"` and, 30 s later if still Ack, `Presence="On"`.
4. **Metric re-publishing**: update `MetricValue/@Value` and `@DeterminationTime` every `DeterminationPeriod` (1 s). Hard limit: once the Dashboard has seen one metric report it reconnects after 120 s of silence (B6).
5. **Self-check**: set `AlertSystemState/@LastSelfCheck` (ms since epoch) at startup and refresh it at least every `SelfCheckPeriod` = 10 s; the Dashboard raises COMM_FAILURE when it is older than 1.5 × 10 s = 15 s (B3). `SelfCheckCount` may be incremented but is never read. A stopped self-check loop is the intended way to simulate "silent death" without killing the process.
6. **Clock**: keep `ClockState/@DateAndTime` absent or current (see decision R3). sdc11073 does this automatically (`CurrentTimestampAttributeProperty`); a C++ provider must not serve a stale value.
7. **Identity**: pass distinct EPRs per instance; sdc11073 replaces the file's `SequenceId` with its own per start (`ProviderMdib` generates it), a C++ stack may not (open question 4).
8. **Services**: host a Context service (GetContextStates is the Dashboard's keep-alive ping) and a Set service; report changes via `EpisodicMetricReport`, `EpisodicAlertReport`, `EpisodicContextReport`. In sdc11073 the `ExtendedProduct` role provider already services `SetContextState` and `SetAlertState` generically; other stacks may require explicit handler registration.

## Decisions

**Rule 3 — complete MdState, ClockState without DateAndTime.** Every one of the 14 single-state descriptors per file has exactly one explicit state (checker R2); nothing depends on sdc11073's `mk_state_containers_for_all_descriptors` (A6, C-g), so a C++ loader sees the same MDIB. `DateAndTime` is **omitted**: it is optional in the schema, and a static value in the file would be read by the Dashboard as `device_time − now`; within an hour of file creation the offset exceeds 3600 s, at which point the Dashboard disables both timestamp correction and the COMM_FAILURE self-check (`qtDeviceHandler.py:441-442, 472-473`). With the attribute absent the offset is 0 and the self-check stays active. `LastSelfCheck` and metric `DeterminationTime` are omitted for the same reason (a stale `LastSelfCheck` would trigger COMM_FAILURE at connect). `RemoteSync="false"` is an honest default (the Dashboard only logs one warning); set it to `true` if the test hosts are NTP-synchronised.

**Rule 4 — an EnsembleContextState IS in the file.** The Dashboard takes two different paths (B5): if an `EnsembleContextState` exists it copies it (`mk_copy`, handle preserved → provider-side *update*); if none exists it builds a new state whose `Handle` equals the descriptor handle, which is sdc11073's private convention for *insert* (C-f) and whose interpretation by the C++ stack is unknown. A pre-existing, unassociated slot (`ContextAssociation="No"`, no `Identification`) forces the well-defined update path on both stacks, carries no per-run identity, and avoids template defect A8-15 (association No with an identifier). Patient, location and workflow states are not in the file (rule 4).

**Rule 10 — wrapper.** All files use the same `msg:GetMdibResponse` root. **Resolved 2026-09-29:** verified live with the second stack (`spo2_mdib_v2.xml`) and the Dashboard connects; `vent_mdib_v2.xml` has the identical structure (only more VMDs/operations).

Other choices: alert system and SCO sit at MDS level (one alert system per device, one self-check); `ModifiableData` lists only `Presence`, the one attribute the Dashboard changes; no `SetValue`/`SetString`/`Activate`/patient-context operations (A8-19); `Resolution="1"` is correct for these four integer-valued displays (unlike A8-18); `Retriggerable="true"` kept from the template; metric `DeterminationPeriod` is declarative only.

## Open questions (with recommendation)

1. **MDS Type code system.** IEEE 11073-10101 MDS codes (`MDC_DEV_ECG_MDS` etc.) exist but their numeric values could not be verified offline, so a documented private system is used. *Recommendation:* keep the private codes; if MDC codes are wanted, take them from RTMMS and keep `CodingSystem="urn:oid:1.2.840.10004.1.1.1.0.0.1"` explicit. Note that sdc11073's default discovery scope factory derives a scope `sdc.cdc.type:/…` from this Type, which is harmless with either URI.
2. **Reliability profiles.** Without `clinical_db.json` entries for `SDC Testbed`/`<ModelName>`/`<LOINC>` every channel is rejected from the ensemble set M and the aggregation experiment degrades to Yellow-only. *Recommendation:* decide the DPWS strings first, then add matching entries on the Dashboard side (out of scope here).
3. **Loader behaviour of other stacks.** Whether another stack accepts `GetMdibResponse`, keeps the file's `SequenceId`, or synthesises anything was verified only for the stack used in the testbed. *Recommendation:* capture the provider's `GetMdibResponse` and run `tools/validate_mdib.py` and `tools/check_mdib_rules.py` on it.
4. **SequenceId per start.** A constant `SequenceId` defeats the Dashboard's restart detection. *Recommendation:* every provider should overwrite it with a fresh `urn:uuid:` at each start; the Python provider already does.
5. **SelfCheckPeriod value.** PT10S gives a 15 s COMM_FAILURE deadline, aligned with the Dashboard's 15 s T_fallback. *Recommendation:* keep; if alert-report traffic (four devices × one report per 10 s) matters, PT30S is the next sensible value.
6. **Latching and priority** are the brief's defaults. *Recommendation:* keep `Latching="false"` while the acknowledgement flow (On → Ack → 30 s re-raise) is tested, since latching adds a `Latch` presence the Dashboard treats as third priority only.
7. **Alert type codes.** SNOMED CT findings were chosen because they are verifiable and consistent with the Dashboard's SNOMED danger codes; the Dashboard only logs them. *Recommendation:* keep unless the thesis wants IEEE 11073-10101 event codes (`MDC_EVT_HI`/`MDC_EVT_LO`), which would also need RTMMS verification.
8. **RemoteSync.** *Recommendation:* set to `true` only if the provider hosts are NTP-synchronised and the C++ stack can report `DateAndTime` live.
