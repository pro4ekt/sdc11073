# experiments/ziel3_simulation — goal 3: checking the load model against the prototype

Two PCs as for goals 1 and 2, with no control channel between them. **PC 2** (the "ward") runs
N virtual patients with four SDC providers each (`testbed_provider.py`, unchanged; contract:
N = 20, i.e. 80 providers), a patient model and the crisis scenario. **PC 1** (the "central
station") runs only the Dashboard without a window (the decision core of thesis chapter 3,
unchanged). PC 1 knows nothing about the crises; it sees SDC reports only. Truth (PC 2) and
decisions (PC 1) meet after the run, when the journals are merged. The run does not measure a
clinical alarm reduction; it checks whether the prototype behaves as the load model of thesis
chapter 4 (`analysis/belastungsmodell_app/model.py`) predicts.

| Path | Content |
|---|---|
| `ziel3_contract.json` | the contract of both sides: room, patients, sources (model, MDIB, channel, LOINC, priority), profiles, context, ports, clock protocol, runs, IP addresses |
| `pc1_dashboard.py` | start program of PC 1: `harness.Dashboard` without window and without TLS, profiles from the contract, FHIR off, observer, clock polling, stop, log copies, `summary.json` |
| `pc1_observer.py` | `RotObserver`: stands in for the `PatientOverviewModel` and writes journal C (`decisions.jsonl`) |
| `ziel3_export_pc1.py` | CSV digest of one run (read-only): `--name <run>` → `logs/<name>/pc1/export/` with `rot.csv`, `clock.csv`, `devices.csv`, `ttl_expired.csv`, `alarm_in.csv`, `summary.json`; separator `;`, times = epoch seconds of the PC 1 clock (three decimals), `offset_s` = clock PC 2 − clock PC 1 |
| `tests/test_pc1.py` | tests without network: observer, contract (N, ports), profiles from the contract (12 tests) |
| [`pc2/`](pc2/README.md) | PC 2: patient model, crisis scenario, provider pool, runner, threshold sweep without network (38 tests) |
| [`auswertung/`](auswertung/README.md) | evaluation of the runs as written on PC 2; expects the data layout of PC 2 |
| `logs/<name>/pc1/` | created at run time, not in git: `events.jsonl` (Dashboard journal + `run_config` / `run_end` / `clock_offset`), `decisions.jsonl`, `summary.json`, copies of `sdc_consumer.log*`, `export/` |

The result files computed from the journals are in `docs/reports/`; the evaluation over the
device thresholds is `analysis/roc_ziel3/`.

## Contract (summary; binding: `ziel3_contract.json`)

| Parameter | Value |
|---|---|
| Room | `ICU-3` (Dashboard with room filter `ICU-3`) |
| Patients | `SIM-P01` … `SIM-P20` (N = 20); patient i: `--patient-id SIM-Pii`, `--bed i` (the Dashboard ignores the bed); all four providers of one patient: the same `--patient-id`, the same room, different EPRs |
| Manufacturer | `SDC Sim` |
| Source 1 | `Sim ECG`, `ecg_mdib_v2.xml`, `tachycardia`, LOINC 8867-4, **Hi** |
| Source 2 | `Sim Vent`, `vent_mdib_v2.xml`, `mv_low`, LOINC 76008-2, **Hi** |
| Source 3 | `Sim SpO2`, `spo2_mdib_v2.xml`, `desaturation`, LOINC 59408-5, **Me** |
| Source 4 | `Sim ABP`, `abp_mdib_v2.xml`, `dia_low`, LOINC 8462-4, **Lo** |
| Profiles | exactly four entries `SDC Sim` → model → LOINC → TPR 0.90 / FPR 0.00313; the other channels of the MDIBs have no profile → \|M\| = 4, P = (3, 3, 2, 1) |
| Context | `odds_ratios: {}`, P0 = 0.5, FHIR off → Context_Log_Odds = 0 |
| Filter | T = 10 s, α = 0.5, Δ_eval = 1 s (as in `clinical_db.json`), δ_min = 0.5 (default of the code) |
| Transport | no TLS (`--no-tls` at the providers, `no_tls` in the Dashboard) |
| Network | PC 1 `192.168.0.106`, PC 2 `192.168.0.100` |
| Provider ports (PC 2) | 13000–13079; port = 13000 + 4·(i−1) + (j−1), i = patient 1…20, j = source 1…4 |
| Clocks | PC 1 asks PC 2 every 10 s over UDP 5558: `{"t1", "seq"}` → `{"t1", "seq", "t2", "t3", "state", …}` (further fields allowed), `state` = `running` / `done`; PC 1 ends on `done` |
| Acknowledgement | none |
| Provider crash | PC 2 restarts with the same EPR and port; PC 1 only writes `device_lost` / `device_back` |

Everything that depends on N (expected devices N × 4, ensembles N, port range) is read by
`pc1_dashboard.py` from the contract (`expected_counts`); if the port range does not fit N × 4,
it warns at the start.

## Starting PC 1

The IP addresses are in the contract (`network.pc1_ip`, `network.pc2_ip`); `--ip` / `--pc2-ip`
override them. **PC 1 starts first**; PC 2 waits for the first clock request and then starts the
providers. One line from the repository root:

```
.venv\Scripts\python experiments\ziel3_simulation\pc1_dashboard.py --name ziel3_1h --hours 2 --ip 192.168.0.106 --pc2-ip 192.168.0.100
```

| Flag | Meaning | Default |
|---|---|---|
| `--name NAME` | Run name, the same on both PCs; output in `logs/<name>/pc1`. | required |
| `--hours H` | Time limit (safety net); the regular end is `state = done` from PC 2. | `12` |
| `--ip IP` | IPv4 of the adapter of PC 1. | from the contract |
| `--pc2-ip IP` | IPv4 of PC 2 for the clock requests. | from the contract |
| `--contract PATH` | Contract file. | `ziel3_contract.json` next to the script |
| `--dry-run` | Without PC 2: run the Dashboard for about 30 s without devices and check `run_config`, the NEUTRAL line and a clean stop. | off |
| `--dry-run-seconds S` | Duration of the dry run. | `30` |

The program ends when PC 2 reports `state = done` in the clock reply (regular stop), at the
latest after `--hours`, or on Ctrl-C; in every case `manager.stop()`, `aggregator.stop()`, the
copy of `Dashboard/logs/sdc_consumer.log*` and `summary.json` are carried out. Once per minute
an `alive` line appears (devices / expected, bound devices, ensembles / N, Rot total / active,
clock state).

Dry run without PC 2 and without providers (about 30 s; PC 2 is **not** asked, so that a waiting
PC 2 is not woken up — unless `--pc2-ip` is given explicitly on the command line):

```
.venv\Scripts\python experiments\ziel3_simulation\pc1_dashboard.py --name trocken --dry-run
```

It checks that `run_config` and `run_end` are in the journal, that `sdc_consumer.log` contains
the line `mode = NEUTRAL (Context_Log_Odds = 0)` since the start and that the stop is clean
(exit code 0).

Tests without network:

```
.venv\Scripts\python -m unittest discover -s experiments\ziel3_simulation\tests
.venv\Scripts\python -m unittest discover -s experiments\ziel3_simulation\pc2\tests
```

## What the start program replaces in the Dashboard (at run time, no code changed)

1. `get_repository()._raw_clinical_db` ← database built from the contract, **before** the
   `SdcMyConsumer` is constructed (it reads `filter_params` at construction; pattern
   `tests/test_periodic_eval.py:315`).
2. `app.patientOverviewModel.PatientOverviewModel` returns the `RotObserver` while the Dashboard
   is constructed, so that `harness.Dashboard` passes it on as `overview_model`.
3. `topology._get_or_fetch_fhir_data` ← `lambda patient_id: None` (pattern
   `tests/test_departure_overview.py:385`); in addition `odds_ratios: {}`.
4. The second `basic_logging_setup` call in `harness.Dashboard` removes every handler below
   `sdc.*`, including the file logger `Dashboard/logs/sdc_consumer.log`; `setup_module_logger()`
   creates it again afterwards.
5. `aggregator._get_math_core_params()` is called once in advance (read-only), so that the line
   `[ClinicalContext] … mode = NEUTRAL (Context_Log_Odds = 0)` is at the beginning of the log and
   not only at the first ensemble.
6. `dash.seen` (journal mirror of `harness.Dashboard`) is limited to 1000 lines.

## Journals of PC 1

`events.jsonl` (Dashboard journal, `Dashboard/app/metrics/eventlog.py`; fields `t_abs`,
`t_mono`, `iso`, `event`): all Dashboard events (`device_discovered`, `topology_decision`,
`device_accepted`, `subscription_created`, `device_departed`, `technical_alarm`, `verdict`
{ensemble, alert_key, escalated, sdc_score}, summaries) and, added by PC 1, `run_config`
(contract, database, profiles, expected counts, git state), `clock_offset` / `pc2_phase`
(`ClockPoller`) and `run_end`.

`decisions.jsonl` (journal C, `RotObserver`): transitions only —
`rot_start` {patient_id, room, ensemble, device_count, sdc_score},
`rot_end` {…, duration_s, cause = alarms_cleared | ensemble_removed},
`device_lost` {patient_id, room, epr, cause, escalated_at_loss, t_abs_loss}, `device_back` {epr}.
Yellow is not written; it follows from the alarm journal B of PC 2. Both journals carry the clock
of PC 1; the clock of PC 2 is converted over `clock_offset` when the journals are merged.

`summary.json`: reason of the end, duration, expected counts from the contract, devices / bound /
ensembles, `device_accepted` and `accepted_ok`, ensembles with an aggregator and of these the
ones that last had four sources (`aggregator_ensembles`, `aggregator_ensembles_full`,
`aggregators_ok`), number of lines `[Precheck] GetContextStates failed` (`precheck_failures`),
Rot total, counters of all events, last clock sample and clock statistics (rtt min / median /
max, share below 50 ms, offset first / last / min / max), whether the NEUTRAL line is in the log
since the start, copied log files. The counters refer to lines since the program start. The
copied `sdc_consumer.log*` also contain older runs (one shared, rotating file); lines of this run
are recognised by their time stamp.

## Checklist before a run (both PCs)

- PC 1: inbound for the `python.exe` of the venv any TCP (event server on an ephemeral port),
  UDP 3702 and 5558.
- PC 2: inbound TCP 13000–13079, UDP 3702 and 5558.
- Fixed IP addresses on both PCs; sleep, standby and Windows updates off.
- Both PCs with the same `.venv` (sdc11073 3.0.0) and the same git state; identical
  `ziel3_contract.json`.
- No second Dashboard runs on PC 1 (shared `Dashboard/logs/sdc_consumer.log`).
- Order: start PC 1 → start PC 2 (waits for the first clock request, then starts 80 providers
  3 s apart, about 4 min).

## Acceptance (with PC 2, N = 20)

- `events.jsonl`: **80** × `device_accepted` (`summary.json` → `accepted_ok: true`).
- `sdc_consumer.log`: **20** ensembles whose last line `[Adaptive] Built aggregator for
  ensemble=… — 4 sensor(s), 0 danger code(s).` names four sources (`aggregator_ensembles_full: 20`,
  `aggregators_ok: true`). The line appears whenever the aggregator is rebuilt (657 times for
  20 ensembles in the run `ziel3_1h`); counting lines would be wrong.
- `sdc_consumer.log`: no line `[Precheck] GetContextStates failed` (`precheck_failures: 0`).
  Every such line is a provider that the Dashboard rejects for the whole session
  (`scope_mismatch`); a restart with the same EPR does not help.
- `sdc_consumer.log`: one line `mode = NEUTRAL (Context_Log_Odds = 0)` (`neutral_line_in_log: true`).
- `events.jsonl`: `clock_offset` with `rtt_s` < 0.05 (`clock_stats.rtt_under_50ms`).
- `decisions.jsonl`: at least one `rot_start` of a patient.

## What PC 2 has to deliver

- 80 providers with `--manufacturer "SDC Sim"`, `--model` per source as in the contract,
  `--patient-id SIM-P01` … `SIM-P20`, `--bed i`, `--room ICU-3`, an own EPR, port
  13000 + 4·(i−1) + (j−1), `--no-tls`, `--ip 192.168.0.100`; the contexts exist before the first
  Hello (default of the provider).
- Only the contract channel of each provider is switched (`alarm <channel> on|off`, with
  `value …`).
- Clock responder on UDP 5558 with `state = running` during the run and `done` at the end
  (protocol as in `experiments/campaign_2pc/pc2_scheduler.py:212-264`; further fields allowed).
- Journals A (truth) and B (alarms) under `logs/<name>/pc2/`, times from `time.time()` of PC 2.
- After a provider crash: restart with the same EPR and port, entry in the own journal.
- **Start (lesson of `ziel3_1h`):** wait 1–2 s after `start_all` before `publish` (Hello) is
  sent, and keep a gap of at least 3 s between the providers. On 2026-11-02 the
  `GetContextStates` call of the Dashboard failed for one of 80 providers (SIM-P01, Sim ABP,
  port 13003) 0.4 s after the Hello (timeout in the start storm, three providers within one
  second); the device was lost for the session.
- **End:** report `state = done` first, then wait **at least 20 s** before the providers are
  stopped. PC 1 asks every 10 s and needs a few seconds to stop; if Bye messages and reports
  arrive during the stop, the console of PC 1 shows tracebacks `RuntimeError: Signal source has
  been deleted` (Qt objects already released). The journals are not affected and the exit code
  stays 0.

## Runs

| Run | Date | Result (PC 1) |
|---|---|---|
| `ziel3_1h` | 2026-11-02, 02:38–03:44 | 79/80 devices (SIM-P01 without ABP, see above), 20/20 ensembles, 86 Rot at all 20 patients, 373/373 clock replies (offset −0.61 → −0.66 s, drift −0.82 ms/min, rtt median 4.1 ms), no `device_lost`, no COMM_FAILURE, no ERROR line |
| `ziel3_12h` | 2026-11-02, 04:26–16:32 (12.1 h, ended by `state = done`) | 80/80 devices (ramp 04:28–04:32, `precheck_failures: 0`), 20/20 ensembles with four sources, 1063 Rot at all 20 patients (46–63 per patient, median 8.8 s, max 85 s), 4340 clock replies without a gap (offset −0.70 → −0.38 s, three corrections by the time service at about 05:24, 09:40, 14:10; rtt median 4.0 ms, 16 single values ≥ 50 ms, max 245 ms), no `device_lost`, no COMM_FAILURE, no ERROR or WARNING line after the start, no traceback |
| `ziel3_6h_p06` | 2026-11-03, 01:14–07:19 (6.1 h, ended by `state = done`); PC 2: crises with crossing probability 0.6 per source instead of 0.9, otherwise the same | 80/80 devices (ramp 01:15–01:19, `precheck_failures: 0`), 20/20 ensembles with four sources, 526 Rot at all 20 patients (16–37 per patient, median 8.8 s, max 85 s), 2183 clock replies without a gap (offset −0.28 → −0.41 s, one correction by the time service from 05:50; rtt median 3.9 ms, one value ≥ 50 ms), no `device_lost`, no COMM_FAILURE, no ERROR or WARNING line after the start, no traceback; log complete |

The later runs (4 h runs A, B, C and the 6 h run D) are listed with their result files in
`docs/reports/README.md`.

**Log rotation in long runs:** `Dashboard/logs/sdc_consumer.log` rotates at 5 MB and keeps only
five backups (about 5.7 h with 80 devices). What `pc1_dashboard.py` copies at the end therefore
covers only the last hours. In the run `ziel3_12h` the backups were saved hourly to
`logs/ziel3_12h/pc1/log_archive/` (file name = rotation time); the first file (04:26–05:03, start
lines `x_addrs`, DPWS, topology, NEUTRAL) had already been deleted, hence
`neutral_line_in_log: false` in `summary.json`. `ziel3_export_pc1.py` also reads
`log_archive/*.log` (identical files only once) and, when the start lines are missing, takes the
patient from the discovery scope and the model from the alarm handles. The check of the alarm
lines against journal B for this run is `docs/reports/checks_ziel3_12h.md`.
