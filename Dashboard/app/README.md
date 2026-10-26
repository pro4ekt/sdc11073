# Dashboard/app — application layer

- `sdcMyConsumer.py` — `SdcMyConsumer`: WS-Discovery loop, room filter at discovery time (goal 1), one `DeviceHandler` per accepted provider, technical alarm on connection loss (goal 2), start/stop of everything. Created by `main.py` and by the headless `Dashboard` class in `experiments/harness.py`.
- `qtDeviceHandler.py` — `QtDeviceHandler`: Qt/QML bridge of one device (metrics, alarms, `silenceAlarm` for remote acknowledgement, connection-lost state).
- `patientOverviewModel.py` — `PatientOverviewModel`: QObject model behind the patient overview page (one card per ensemble).
- `topology_filter.py` — `room_from_scopes`, `room_from_context_states`, `decide_room`: pure functions of the topology filter.
- `concept_map.py` — `ConceptMapRepository`, `normalize_concept`: MDC → LOINC translation via `Dashboard/config/conceptmap_mdc_to_loinc.json`, used by `Dashboard/device/handler.py`.
- `fhirData.py` — `FHIRPatientData`: optional HL7 FHIR R4 client (patient and ensemble binding).
- `operationLogger.py` — `OperationLogger`: session file logger for the OR page.
- `deviceHandler.py` — backward-compatible re-export of `device.DeviceHandler`.
- `alarms/` — the adaptive stochastic alarm model; `metrics/` — the JSON Lines event journal and the goal metrics.
