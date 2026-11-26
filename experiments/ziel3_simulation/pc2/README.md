# experiments/ziel3_simulation/pc2 — goal 3: PC 2 (the "ward")

Simulation for the blind check of the adaptive alarm filter of the Dashboard (PC 1) against the
load model. PC 2 runs N virtual patients with four SDC providers each
(`testbed/providers/testbed_provider.py`, unchanged), knows the truth about the crises and
writes the journals A and B. PC 1 sees SDC reports only.

Start from the repository root (on PC 2 this is the root of the copied bundle, which has the same
layout: `experiments/`, `testbed/`):

```
.venv\Scripts\python experiments\ziel3_simulation\pc2\pc2_run.py --name ziel3_1h --patients 20 --hours 1 --seed 1 --ip 192.168.0.100
```

| File | Content |
|---|---|
| `patient.py` | `SourceSpec` / `SOURCES` (contract), `Hysteresis`, `PatientParams`, `Patient`, `make_seed_sequences` |
| `szenario.py` | `CrisisParams`, `Szenario` (crises only), `MemoryJournal` for the tests |
| `alarmebene.py` | second generator: alarms drawn directly as the load model assumes (`--generator alarmebene`) |
| `provider_pool.py` | `ProviderSpec`, `ProviderProc` (one provider process), `ProviderPool` |
| `pc2_run.py` | runner: clock → providers → patients / scenario → shutdown |
| `offline_check.py` | offline check of the parameters without network (20 patients × 12 h in about 25 s) |
| `roc_sweep.py` | sweep of the threshold scale h in model time: journals A/B for every h in `logs/ziel3_roc/h_<h>/` (see below) |
| `export_logs.py` | CSV digest of the journals of one run |
| `check_provider_local.py` | check of the provider wrapper with one provider on 127.0.0.1 |
| `tests/` | unit tests, no network: `.venv\Scripts\python -m unittest discover -s experiments\ziel3_simulation\pc2\tests` (38 tests) |

## Flags of `pc2_run.py`

| Flag | Meaning | Default |
|---|---|---|
| `--name NAME` | Run name, the same on both PCs; journals go to `logs/<name>/pc2/`. | required |
| `--hours H` | Duration of the running phase. | required |
| `--patients N` | Number of patients (four providers each). | `20` |
| `--seed N` | Seed of patients and scenario. | `1` |
| `--p-cross P` | Probability that a source is pushed beyond its threshold in a crisis. | `0.9` |
| `--crisis-min S`, `--crisis-extra-mean S` | Minimum crisis duration and mean of its exponential part. | `15`, `15` |
| `--generator prozess\|alarmebene` | Random value processes with thresholds, or alarms drawn as the load model assumes. | `prozess` |
| `--fpr`, `--c-art`, `--d-false` | `alarmebene` only: false-alarm time share per channel, share of common artefacts, mean false-alarm length in s. | `0.026`, `0.10`, `8` |
| `--threshold-scale H` | `prozess` only: scale h of the alarm thresholds of all four sources. | `1.0` = contract |
| `--ip IP` | IPv4 of PC 2 for the providers. | auto-detected |
| `--start-gap S` | Seconds between provider starts (minimum 3). | `3` |
| `--ready-timeout S` | Wait for the `ready:` line of one provider. | `40` |
| `--wait-pc1-min MIN` | Wait this long for the first clock request of PC 1 (0 = do not wait). | `30` |
| `--status-every S` | Status poll period per provider. | `60` |
| `--off-to-shutdown S` | Seconds between `state=done` (alarms off) and provider shutdown (minimum 20). | `60` |
| `--linger S` | Keep answering the clock after `done`. | `600` |
| `--log-dir PATH` | Override `logs/<name>/pc2`. | — |
| `--python PATH` | Interpreter used for the provider processes. | the running interpreter |
| `--dry-run` | Print the plan (ports, EPRs) and exit; nothing is started. | off |

## Contract (literally the same as on PC 1)

| | ECG (j=1) | Vent (j=2) | SpO2 (j=3) | ABP (j=4) |
|---|---|---|---|---|
| `--model` | `Sim ECG` | `Sim Vent` | `Sim SpO2` | `Sim ABP` |
| MDIB | `ecg_mdib_v2.xml` | `vent_mdib_v2.xml` | `spo2_mdib_v2.xml` | `abp_mdib_v2.xml` |
| channel (`alarm <ch>`, `value <ch>`) | `tachycardia` | `mv_low` | `desaturation` | `dia_low` |
| metric, LOINC | `ecg_metric_hr`, 8867-4 | `vent_metric_mv`, 76008-2 | `spo2_metric_spo2`, 59408-5 | `abp_metric_dia`, 8462-4 |
| priority in the MDIB | Hi | Hi | Me | Lo |
| base / threshold ON / threshold OFF | 72 / ≥120 / ≤115 | 7.0 / ≤4.0 / ≥4.4 | 97 / ≤90 / ≥92 | 70 / ≤50 / ≥53 |

Common to all providers: `--manufacturer "SDC Sim"`, `--room ICU-3`, `--bed <i>`,
`--patient-id SIM-Pii`, `--no-tls`, `--friendly-name "SIM-Pii ECG"`, `--serial SIM-Pii-j`, an own
`--seed`, port `13000 + 4·(i−1) + (j−1)`, EPR = uuid5 of `ziel3/<name>/<patient>/<j>` (stable
within a run, different for another run name). The other channels of the MDIBs are not touched.
Clock: UDP 5558, reply `{t1, seq, t2, t3, state, …}` (`ClockResponder` from
`experiments/campaign_2pc/pc2_scheduler.py`), `state` = `waiting` → `running` → `done`.

## Patient model (`patient.py`)

Step 1 s. Value of source j:

`value = base + direction · distance · (drift + noise + bursts + movement + disturbance)` with
`distance = |threshold ON − base|`, so that a dimensionless sum of 1.0 is exactly the threshold.

* `drift` — Ornstein–Uhlenbeck process, τ = 480 s, σ = 0.10;
* `noise` — AR(1), τ = 20 s, σ = 0.09;
* `bursts` — artefacts of one source: Poisson stream 0.0054 1/s, log-normal amplitude (median 1.0,
  σ 0.45), sign towards the alarm with probability 0.85, rise 1–2 s, exponential decay τ 6–13 s;
* `movement` — "patient movement": stream 1/480 s per patient, log-normal duration (median 12 s),
  an own gain per source (median 0.9, σ 0.55, sign towards the alarm with probability 0.85);
* `disturbance` — from `Szenario` only, additive.

The thresholds are directed (heart rate upwards, the others downwards), with hysteresis and at
least 2 s in the ON state. The comparison is made by the patient; the provider only receives
`value` and `alarm on|off` at the transitions.

Threshold scale `h` (`PatientParams.threshold_scale`, option `--threshold-scale` of `pc2_run.py`
and `offline_check.py`, default 1.0 = contract, bit for bit): alarm at `x ≥ h` for all four
sources at once (`x` is the dimensionless sum, 1.0 = threshold of the contract), switch-off when
the value returns beyond the OFF threshold of the contract multiplied by `h` (HR 0.896·h,
MV 0.867·h, SpO2 0.714·h, DIA 0.85·h), the 2 s minimum unchanged. Physical thresholds:
`scaled_thresholds(source, h)`. The thresholds do not influence the random numbers, so for one
seed the values of the patients are the same for every `h`. The generator `alarmebene` ignores
the option.

Random numbers: `numpy.random.Generator`, `SeedSequence(--seed).spawn(N+1)` — one stream per
patient and one for `Szenario` (`make_seed_sequences`). False alarms are not scheduled anywhere.

## Scenario (`szenario.py`)

Crises only: Poisson stream of 30 per day and bed, duration 15 s + Exp(15 s) (mean 30 s). Every
source gets a delay U(0, 15 s) and "has to cross" with probability `--p-cross` (default 0.9,
written to `run_config` as `p_cross`): push U(1.25, 1.8) of the threshold, otherwise
U(0.3, 0.75); rise 4 s, decay with τ = 8 s after the end. Journal A: `crisis_start` (plan) and
`crisis_end` (what was observed: whether and when each source crossed its threshold). The runner
writes the movements to journal A as `movement`.

The crisis duration is set by `--crisis-min` (minimum, default 15 s) and `--crisis-extra-mean`
(mean of the exponential addition, 15 s); both are written to `run_config`
(`crisis_dur_min_s`, `crisis_dur_extra_mean_s` and inside `crisis_params`).

## Generator "alarmebene" (`alarmebene.py`, option `--generator alarmebene`)

The alarms are drawn exactly as the analytical load model assumes (appendix G of the thesis,
`analysis/belastungsmodell_app/model_ziel3.py`): every channel switches a false alarm on
independently as an alternating process (time share (1 − c_art)·FPR, duration Exp(d_false));
common pushes of the patient (share c_art·FPR, duration Exp(d_false)) switch all four channels on
at once and are written to journal A as `movement` with the gains (1, 1, 1, 1); in a crisis every
channel takes part with probability `--p-cross` and is on from the first to the last step of the
crisis without delay; there is no hysteresis and no 2 s minimum. Parameters `--fpr 0.026`,
`--c-art 0.10`, `--d-false 8`. The device value is near the base outside an alarm and beyond the
threshold during an alarm (1.3 distances to the threshold). Contract, providers and journals are
the same. With the default (`--generator prozess`) nothing changes.

## Journals (`logs/<name>/pc2/`, JSON Lines, `t_abs` on the clock of PC 2)

Every record: `seq, t_abs, t_mono, iso, event, …` (format of `Journal` from goals 1 and 2). The
first record of every file is `run_config` (all parameters, table of the providers with ports and
EPRs).

* `truth.jsonl` (A): `run_config`, `run_start`, `crisis_start`, `crisis_end`, `movement`, `run_end`.
* `alarms.jsonl` (B): `run_config`, `pc1_seen`, `provider_start`, `provider_ready`, `run_start`,
  `alarm` (patient, source, `on`, `value`, `t_prov_ms` from the echo of the provider, `t_cmd_abs`,
  `latency_ms`), `alarm_dropped`, `alarm_failed`, `status` (every 60 s per provider:
  `subscriptions`, `selfcheck`, `ack`), `provider_msg`, `provider_exit`, `provider_restart`,
  `provider_resynced`, `run_end`, `pool_shutdown`.
* `clock.jsonl`: `clock_reply` for every request of PC 1.
* `console.txt`, `run_config.json`.

## Sequence of the runner

1. Clock responder, `state=waiting`; wait for the first request of PC 1 (up to `--wait-pc1-min`,
   default 30 min).
2. Providers with the gap `--start-gap` (minimum and default 3 s: the Dashboard rejected a device
   when three providers of one patient appeared within one second; 80 providers take about
   4 min), wait for every `ready:` line (40 s each), one retry.
3. `state=running`: every second `Patient.step` → transitions to the pool, `Szenario.step`,
   `ProviderPool.tick` (status poll, restart of crashed providers with the same EPR and port and
   replay of the alarm state).
4. After `--hours`: `state=done`, all alarms off, after `--off-to-shutdown` (minimum 20 s,
   default 60 s) `shutdown` to every provider; the clock keeps answering for another 600 s.
   Ctrl-C: the same with a pause of 20 s and a linger of 60 s, exit code 130.

CSV digest of the journals: `python export_logs.py --name <name>` → `logs/<name>/pc2/export/`
(`crises.csv`, `alarms.csv`, `movements.csv`, `status.csv`, `run_config.json`; separator `;`,
times as epoch seconds with three decimals).

## Threshold sweep (ROC, `roc_sweep.py`, no network)

`python roc_sweep.py --net-run ziel3_12h --out ziel3_roc [--grid 0.05,…,1.0,…,2.2]` repeats the
scenario of a network run (same seed, patients, hours, crisis and patient parameters) in model
time for a grid of `h`. For every `h` it writes to `logs/ziel3_roc/h_<h>/` the alarm journal
`alarms.jsonl` in the format of journal B (`run_config`, `provider_ready`, `run_start`, `alarm`,
`run_end`; `t_abs` = `t_start_abs` of the network run + model time, `latency_ms` 0), the journal
`truth.jsonl` in the format of journal A (crises, movements) and `run_config.json`
(`threshold_scale`, grid, thresholds `on_h` / `off_h`). A copy of the journals of the network run
is put into `logs/ziel3_roc/netz_<run>/`. Check (`check_against_net`): at `h = 1.0` the number of
alarms per channel agrees with journal B of the network run (±1 %) and the crisis identifiers are
the same, otherwise exit code 2.

The metrics (TPR/FPR by time for the devices and for k-aus-4, η, AUC, "all four at once") are
computed by `../auswertung/ziel3_roc.py` → `logs/ziel3_roc/roc_kausn.csv`, `roc_summary.json`;
in addition variant (ii) of the x axis (`fpr_ii` = duration of the false episodes without a
crisis within ±10 s / stable time, `auc_ii`) and the strictly recognised crises
(`erkannt_streng`, escalation starts before the end of the crisis).

| Flag | Meaning | Default |
|---|---|---|
| `--net-run RUN` | Finished network run whose scenario is repeated. | `ziel3_12h` |
| `--out FOLDER` | Output folder under `logs/`. | `ziel3_roc` |
| `--grid LIST` | Comma-separated threshold scales h; 1.0 is added if missing. | 15 values from 0.05 to 2.2 |
| `--hours H` | Model hours, independent of the network run. With the same seed the span of the network run stays identical and the check compares that span. | hours of the network run |
| `--seed S` | New seed for the scenario of `--net-run`: an independent data set without a network run; the check is skipped and no network journals are copied. | seed of the network run |
| `--no-check` | Do not stop when `h = 1.0` differs from the network run. | off |
| `--quiet-progress` | No progress lines. | off |

Examples: `python roc_sweep.py --net-run ziel3_4h_p07 --out ziel3_roc_p07 --hours 12` (seed and
scenario of run A with `p_cross` 0.7, 12 h; the first 4 h agree with the network run bit for bit),
`python roc_sweep.py --net-run ziel3_12h --out ziel3_roc_val09 --seed 8` and
`python roc_sweep.py --net-run ziel3_4h_p07 --out ziel3_roc_val07 --seed 9 --hours 12`
(validation sets).

## Notes on the provider

* The main loop of the provider republishes the values of all channels once per second and the
  self-check touches every alert system every 5 s; with N = 20 this is about 80 + 48 reports per
  second to PC 1, independent of the alarms.
* `SdcProvider` of sdc11073 3.0.0 grants a subscription for at most 15 s; the consumer renews at
  half of that time.
* `--no-tls` prints a warning; this is expected.
