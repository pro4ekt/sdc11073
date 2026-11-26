# SDC ICU Dashboard and testbed

An SDC consumer ("Dashboard", PySide6/QML) and a simulated-device testbed written for a
master's thesis at THWS. The Dashboard connects to ISO/IEEE 11073 SDC providers on an ICU
network and implements three things the thesis evaluates: an adaptive stochastic alarm
filter across the devices of one patient, a topology filter that admits only the devices
of the Dashboard's own room, and remote acknowledgement of alarms together with
connection-loss detection and a safe fallback state. The testbed provides configurable
Python SDC providers, experiment runners for the two measured goals and a two-PC
measurement campaign. For the third goal, the reduction of false alarms, the repository
contains a two-PC simulation with 20 virtual patients and the evaluation of its runs
against an analytical load model. The SDC protocol stack is the open-source library
[sdc11073](https://github.com/Draegerwerk/sdc11073) (version 3.0.0, MIT licence).
Interoperability was additionally checked with two C++-based SDC stacks; their sources
are not part of this repository.

**For readers of the thesis.** The appendix of the thesis links into this repository at two
tagged states. `stand-messungen` is the state in which the measurements of goals 1 and 2 were
made; all line numbers quoted in the thesis refer to it. `abgabe` is the submitted state; it
adds the simulation and evaluation of goal 3 (`experiments/ziel3_simulation/`, `analysis/`).
`Dashboard/`, `testbed/` and the programs in `experiments/campaign_2pc/` are identical in both
states; in that folder only the two documents `SPEC_C.md` and `HANDOFF_PC2.md` were translated
into English. The
parts of the appendix that were shortened for print are in
[`docs/Anhang_ausfuehrlich.md`](docs/Anhang_ausfuehrlich.md).

## Quick start

Windows, Python 3.13, from the repository root (on Linux replace `.venv\Scripts\python` by
`.venv/bin/python`):

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt -r requirements-app.txt
.venv\Scripts\python -m unittest discover -s tests -t .
.venv\Scripts\python -m unittest discover -s experiments\ziel3_simulation\pc2\tests
```

The first test command ends with `Ran 430 tests … OK (skipped=28)`, the second with
`Ran 38 tests … OK`. The tests need no network, no certificates and no running device.

To see the Dashboard working with simulated devices on one PC and without certificates, start
it and then the demo scenario (two beds, four providers) in a second console; replace the
address by the IPv4 address of your network adapter:

```
.venv\Scripts\python -m Dashboard --no_tls --ip 192.168.0.106
.venv\Scripts\python experiments/screenshots/demo_scenario.py --ip 192.168.0.106
```

## Repository layout

```
.
├── Dashboard/            the consumer application: python -m Dashboard
│   ├── app/              manager, Qt/QML bridge, alarm model (app/alarms), metrics journal (app/metrics)
│   ├── device/           one SDC worker thread per provider: connect, subscribe, ping, acknowledge
│   ├── qml/              user interface
│   ├── config/           clinical_db.json (device profiles, model parameters), MDC→LOINC concept map
│   └── img/              UI images
├── testbed/
│   ├── providers/        testbed_provider.py — configurable SDC provider, one script for every device
│   └── mdib/             MDIB files of the four testbed devices (+ template/ with the structural template)
├── experiments/          evaluation of the thesis goals
│   ├── harness.py        shared runner pieces: provider subprocess, headless Dashboard
│   ├── ziel1/            goal 1: topology filter (TRA, avoided subscriptions)
│   ├── ziel2/            goal 2: acknowledgement success/RTT, fault injection, fallback timing
│   ├── campaign_2pc/     one-command campaign on one PC and the two-PC variant C
│   ├── ziel3_simulation/ goal 3: simulation on two PCs — the Dashboard side (PC 1)
│   │   ├── pc2/          patient model, crisis scenario, provider pool, threshold sweep (PC 2)
│   │   └── auswertung/   evaluation of the runs as written on PC 2
│   └── screenshots/      demo scenario: two beds, four providers on one PC
├── analysis/             evaluation of goal 3
│   ├── belastungsmodell_app/  analytical load model and the application that displays it
│   └── roc_ziel3/        curves over the device thresholds, computed with the unchanged filter core
├── tests/                unit tests of the Dashboard and of the evaluation, run from the repository root
├── tools/                PKI generator, live provider check, MDIB validators
├── docs/
│   ├── Anhang_ausfuehrlich.md  parts of the thesis appendix shortened for print (German)
│   ├── model/            normative text of the alarm model and the deviation protocol
│   └── reports/          MDIB analysis, result files of the goal 3 runs
├── requirements.txt      pinned dependencies of Dashboard, testbed and tools
├── requirements-app.txt  pinned dependencies of the evaluation (analysis/, part of the tests)
└── LICENSE
```

Not in the repository: every `logs/` folder (`Dashboard/logs`, `experiments/*/logs`; created
at run time, several gigabytes for the thesis runs), the keys and certificates in `pki/`
(generated locally by `tools/make_testbed_pki.py`), the bundle folder `pc2_c/` for the second PC
(built by `experiments/campaign_2pc/build_pc2_c.py`) and the exported figures.

## Installation

Windows and Linux, Python 3.13 (the measurements were made with 3.13.12).

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt -r requirements-app.txt    # Windows
.venv/bin/python   -m pip install -r requirements.txt -r requirements-app.txt      # Linux
```

`requirements.txt` is enough for the Dashboard, the testbed, the tools and the experiments of
goals 1 and 2. `requirements-app.txt` (numpy, pandas, scipy, matplotlib, streamlit) is needed
for `analysis/`, for `experiments/ziel3_simulation/auswertung/` and for the tests of the
evaluation; without it `unittest discover -s tests` reports import
errors for these test files.

`requirements.txt` pins `sdc11073==3.0.0`, `PySide6`, `lxml`, `cryptography` (PKI tool) and
`fhirpy` (optional FHIR binding). From sdc11073 the project uses `sdc11073.consumer`
(`SdcConsumer`), `sdc11073.provider` (`SdcProvider`, `BaseProduct`, operations, role provider
components, scopes factory), `sdc11073.mdib` (`ConsumerMdib`, `ProviderMdib`),
`sdc11073.wsdiscovery`, `sdc11073.certloader`, `sdc11073.location`, `sdc11073.loghelper`,
`sdc11073.network`, `sdc11073.observableproperties`, `sdc11073.httpserver`,
`sdc11073.pysoap` and `sdc11073.xml_types` (`pm_types`, `pm_qnames`, `msg_types`,
`dpws_types`, `eventing_types`, `actions`).

Optional: Wireshark with `tshark` on the PATH (or under `C:\Program Files\Wireshark`) for
packet captures during the experiment runs; the runs work without it.

All commands below are given for Windows from the repository root. On Linux replace
`.venv\Scripts\python` by `.venv/bin/python`.

## Running the Dashboard

```
.venv\Scripts\python -m Dashboard
.venv\Scripts\python -m Dashboard --room ICU-1 --ip 192.168.0.106
.venv\Scripts\python -m Dashboard --no_tls
```

| Flag | Meaning | Default |
|---|---|---|
| `--room ROOM_ID` | Room filter: connect only to providers whose LocationContext room equals this value (goal 1). | none — filter off, every provider accepted |
| `--ip IP_ADDRESS` | Network adapter used for WS-Discovery. | auto-detected via the route to 8.8.8.8 |
| `--event-log PATH` | JSON Lines event journal (topology decisions, subscriptions, acknowledgement RTT, fallbacks). | `Dashboard/logs/events.jsonl` |
| `--tls` | Force TLS for every connection (certificates from `pki/dashboard/`). | auto: `https://` → TLS, `http://` → plain with TLS fallback |
| `--no_tls` | Plain HTTP only, no TLS fallback; for providers started with `--no-tls`. | — |

Qt arguments such as `-platform offscreen` are passed through. Running `python main.py`
from inside `Dashboard/` is equivalent. A headless Dashboard without the QML window is
provided by the class `Dashboard` in `experiments/harness.py`; the experiment runners and
`experiments/screenshots/demo_scenario.py --headless` use it.

## Running a testbed provider

```
.venv\Scripts\python testbed/providers/testbed_provider.py --mdib testbed/mdib/ecg_mdib_v2.xml --epr urn:uuid:11111111-1111-1111-1111-111111111111 --manufacturer "SDC Testbed" --model "ECG Monitor T1" --friendly-name "Testbed ECG monitor, bed 1" --patient-id P001 --room ICU-1 --bed 1 --certs-dir pki/ecg --ip 192.168.0.105 --port 10010
```

The device is defined by the MDIB file; the four testbed devices are
`testbed/mdib/{ecg,spo2,abp,vent}_mdib_v2.xml` (see `testbed/mdib/README.md`).

| Flag | Meaning | Default |
|---|---|---|
| `--mdib MDIB` | MDIB file (`GetMdibResponse` wrapper). | required |
| `--epr EPR` | Endpoint reference, e.g. `urn:uuid:…`. | required |
| `--manufacturer`, `--model`, `--friendly-name` | DPWS `ThisModel` / `ThisDevice` strings. | required |
| `--patient-id ID` | Patient identifier (`InstanceIdentifier/@Extension`). | required |
| `--patient-root ROOT` | `InstanceIdentifier/@Root`. | fixed `urn:uuid`, same for every device |
| `--room ROOM` | LocationContext room; without it no location state is created. | none |
| `--bed BED` | LocationContext bed (needs `--room`). | none |
| `--scope-room ROOM` | Test only: announce this room in the WS-Discovery scope while the context keeps `--room` (scope/context mismatch). | none |
| `--serial SERIAL` | DPWS serial number. | `0001` |
| `--certs-dir DIR` | Participant folder from `pki/`, e.g. `pki/ecg` → mutual TLS. | none |
| `--no-tls` | Plain HTTP (debugging only). | off |
| `--ip IP` | Local IPv4 to bind. | first adapter IPv4 |
| `--port PORT` | Fixed HTTPS port (lets you create one firewall rule). | `0` = chosen by the OS |
| `--ack accept\|refuse` | Remote acknowledgement policy; changeable at run time. | `accept` |
| `--seed N` | Seed of the rest-mode noise (reproducible runs). | none |
| `--ctx-patient`, `--ctx-location`, `--ctx-ensemble`, `--ctx-workflow HANDLE` | Override the context descriptor handles read from the MDIB. | from the MDIB |

Commands on standard input, one per line: `value <channel> <number>`, `rest <channel>`,
`alarm <channel> on|off`, `ack accept|refuse`, `selfcheck off|on`, `status`, `shutdown`.

## Running the experiments on one PC

Close the Dashboard window first: every run starts its own headless Dashboard. Journals,
console output and analyses go to `experiments/ziel1/logs/` and `experiments/ziel2/logs/`.
The `--dry-run` form prints the commands and starts nothing.

### Goal 1 — `experiments/ziel1/ziel1_run_all.py`

```
.venv\Scripts\python experiments/ziel1/ziel1_run_all.py --ip 192.168.0.106
.venv\Scripts\python experiments/ziel1/ziel1_run_all.py --ip 192.168.0.106 --dry-run
```

| Flag | Meaning | Default |
|---|---|---|
| `--ip IP` | Adapter IPv4 of this host. | required |
| `--room ROOM` | Target room of the Dashboard / own room of the providers. | `ICU-1` |
| `--trials-filtered LIST` | Trials per role for the filtered run. | `foreign=150,mismatch=150,own=5,nolocation=5` |
| `--trials-baseline LIST` | Trials per role for the unfiltered baseline run. | `foreign=20,mismatch=20,own=2,nolocation=2` |
| `--skip-baseline` | Do not run the baseline. | off |
| `--capture auto\|off` | Packet capture with tshark if available. | `auto` |
| `--tshark PATH` | Path to `tshark.exe`. | searched on PATH and in Program Files |
| `--name NAME` | Label of the output files. | timestamp |
| `--dry-run` | Print the commands, run nothing. | off |

The script runs `ziel1_experiment.py` twice (`--mode filtered`, `--mode baseline`) and then
`ziel1_analyze.py`. Flags of `experiments/ziel1/ziel1_experiment.py` for a single run:
`--ip IP`, `--mode filtered|baseline` (`filtered`), `--room` (`ICU-1`), `--other-room`
(`ICU-2`), `--trials LIST` (`foreign=10,mismatch=10,own=1,nolocation=1`), `--order
interleave|grouped`, `--log PATH`, `--port-base N` (`10100`), `--trial-timeout S` (`60`),
`--settle S` (`3`), `--tls auto|force_tls|no_tls`, `--seed N` (`1`), `--echo-provider`,
`--dry-run`.

### Goal 2 — `experiments/ziel2/ziel2_run_all.py`

```
.venv\Scripts\python experiments/ziel2/ziel2_run_all.py --ip 192.168.0.106
.venv\Scripts\python experiments/ziel2/ziel2_run_all.py --ip 192.168.0.106 --dry-run
```

| Flag | Meaning | Default |
|---|---|---|
| `--ip IP` | Adapter IPv4 of this host. | required |
| `--room ROOM` | Room of Dashboard and providers. | `ICU-1` |
| `--accept N` | Acknowledgement trials with provider policy `accept`. | `300` |
| `--refuse N` | Acknowledgement trials with provider policy `refuse`. | `20` |
| `--methods LIST` | Fault-injection methods and counts. | `shutdown=30,kill=30,suspend=30,reconnect=5` |
| `--skip-ack`, `--skip-fault` | Skip one of the two parts. | off |
| `--name NAME` | Label of the output files. | timestamp |
| `--dry-run` | Print the commands, run nothing. | off |

Underlying runners: `experiments/ziel2/ziel2_ack.py` (`--ip`, `--room`, `--device
abp|ecg|spo2|vent` (`ecg`), `--channel`, `--accept` (`300`), `--refuse` (`20`), `--port`
(`10600`), `--log`, `--confirm-timeout S` (`5`), `--settle S` (`0.5`), `--tls`,
`--echo-provider`, `--dry-run`) and `experiments/ziel2/ziel2_fault.py` (`--ip`, `--room`,
`--device`, `--methods`, `--order interleave|grouped`, `--port-base` (`10700`), `--log`,
`--fallback-timeout S` (`90`), `--reconnect-timeout S` (`120`), `--settle S` (`3`),
`--tls`, `--echo-provider`, `--dry-run`).

### Both goals in one run — `experiments/campaign_2pc/campaign.py`

```
.venv\Scripts\python experiments/campaign_2pc/campaign.py --pilot      # ~10 min, ends with PILOT: OK | PILOT: PROBLEME
.venv\Scripts\python experiments/campaign_2pc/campaign.py              # full run, ends with GESAMT: OK | GESAMT: FEHLER
.venv\Scripts\python experiments/campaign_2pc/campaign.py --dry-run
```

| Flag | Meaning | Default |
|---|---|---|
| `--config PATH` | Campaign configuration. | `experiments/campaign_2pc/campaign.json` |
| `--pilot` | Small trial counts for every phase. | off |
| `--resume [RUN_DIR]` | Continue the newest (or given) run; finished phases are skipped. | — |
| `--report-only [RUN_DIR]` | No phases: redo capture analysis, analyzers, report and dossier of an existing run. | — |
| `--run-dir PATH` | Run directory. | `experiments/campaign_2pc/logs/<run>_<timestamp>` |
| `--phases LIST` | Subset of `smoke,ziel1_filtered,ziel1_baseline,ziel2_ack,ziel2_fault`. | all |
| `--no-capture` | No tshark capture. | capture if tshark is found |
| `--dry-run` | Print the runner commands, start nothing. | off |
| `--listen` | Variant C, PC 1: Dashboard windows and capture until PC 2 reports done. | — |
| `--merge [RUN_DIR]` | Variant C: merge a `--listen` run with the PC 2 journal and analyse. | — |
| `--pc2 PATH` | PC 2 journal (`pc2_journal.jsonl`) or the folder that holds it. | — |
| `--selftest-merge` | Synthetic PC 1 + PC 2 journals through `--merge`. | — |

Phases, trial counts, timeouts and output files are described in
`experiments/campaign_2pc/README.md`.

## Running the two-PC campaign (variant C)

PC 1 runs the Dashboard and captures traffic; PC 2 runs the providers by itself according to
`experiments/campaign_2pc/schedule_c.json`. There is no control channel between the PCs,
only a clock exchange. Specification: `experiments/campaign_2pc/SPEC_C.md`; step-by-step
instructions for PC 2: `experiments/campaign_2pc/HANDOFF_PC2.md`.

1. On PC 1 generate the PKI with both IP addresses (see TLS / PKI below) and build the bundle for PC 2:
   ```
   .venv\Scripts\python experiments/campaign_2pc/build_pc2_c.py
   ```
   This script has no flags. It assembles the bundle folder `pc2_c` inside `experiments/campaign_2pc/` (harness, scheduler,
   schedule, provider script, the four MDIBs and the provider certificates). The folder is
   ignored by git because it contains private keys; copy it to PC 2.
2. On PC 2, from the copied folder, in an administrator console (the `netdown` scenario adds a
   firewall rule):
   ```
   python experiments/campaign_2pc/pc2_scheduler.py --selftest
   python experiments/campaign_2pc/pc2_scheduler.py --ip 192.168.0.105 --pc1 192.168.0.106
   ```

   | Flag | Meaning | Default |
   |---|---|---|
   | `--schedule PATH` | Shared schedule. | `schedule_c.json` next to the script |
   | `--ip IP` | Adapter IPv4 of PC 2. | `pc2_ip` from the schedule |
   | `--pc1 IP` | IPv4 of PC 1. | `pc1_ip` from the schedule |
   | `--log-dir PATH` | Journal folder. | `<root>/logs/<scenario_id>` |
   | `--pilot` | Short pilot schedule. | off |
   | `--selftest` | Local self-test of the scheduler. | off |
   | `--dry-run` | Print the plan, start nothing. | off |
   | `--phases LIST` | Subset of phase names. | all |
   | `--netdown-mode firewall\|skip` | How the `netdown` loss type is produced. | `firewall` |
   | `--wait-pc1-min N` | Wait up to N minutes for the first clock request of PC 1 (0 = start at once). | `30` |
   | `--no-echo-provider` | Do not print provider output. | off |
   | `--netdown-clear` | Remove leftover firewall rules and exit. | — |
   | `--netdown-test SECONDS` | Add the rules for N seconds (with watchdog), verify removal. | — |
3. On PC 1 (Dashboard window closed):
   ```
   .venv\Scripts\python experiments/campaign_2pc/campaign.py --listen [--pilot]
   ```
   `--listen` starts `experiments/campaign_2pc/pc1_listener.py` for each observation window.
   Its flags, if run by hand: `--window filtered|baseline`, `--until baseline|done`, `--ip IP`,
   `--room` (`ICU-1`), `--pc2 IP`, `--clock-port` (`5558`), `--clock-poll-s` (`10`), `--log PATH`
   (absolute), `--tls`, `--confirm-timeout` (`5`), `--wait-start-min` (`60`),
   `--idle-exit-min` (`30`), `--max-hours` (`10`), `--dry-run`.
4. Copy the PC 2 run folder back to PC 1 and merge:
   ```
   .venv\Scripts\python experiments/campaign_2pc/campaign.py --merge --pc2 <PC 2 run folder>
   ```

## Goal 3 — simulation on two PCs (`experiments/ziel3_simulation/`)

PC 2 runs 20 virtual patients with four providers each and knows when a crisis takes place;
PC 1 runs the Dashboard without a window and only sees SDC reports. The contract of both sides
is `experiments/ziel3_simulation/ziel3_contract.json`. PC 1 starts first:

```
.venv\Scripts\python experiments\ziel3_simulation\pc1_dashboard.py --name ziel3_1h --hours 2 --ip 192.168.0.106 --pc2-ip 192.168.0.100     # PC 1
.venv\Scripts\python experiments\ziel3_simulation\pc2\pc2_run.py --name ziel3_1h --patients 20 --hours 1 --seed 1 --ip 192.168.0.100       # PC 2
.venv\Scripts\python experiments\ziel3_simulation\pc1_dashboard.py --name trocken --dry-run                                                # PC 1 alone, about 30 s
```

| Script | Flags |
|---|---|
| `pc1_dashboard.py` | `--name NAME` (required, the same on both PCs), `--hours H` (`12`, time limit), `--ip IP`, `--pc2-ip IP` (from the contract), `--contract PATH`, `--dry-run`, `--dry-run-seconds S` (`30`) |
| `pc2/pc2_run.py` | `--name NAME`, `--hours H` (both required), `--patients N` (`20`), `--seed N` (`1`), `--p-cross P` (`0.9`), `--threshold-scale H` (`1.0`), `--generator prozess\|alarmebene`, `--ip IP`, `--dry-run`; the remaining timing flags are listed in `experiments/ziel3_simulation/pc2/README.md` |
| `pc2/roc_sweep.py` | no network: repeats the scenario of a finished run for a grid of device thresholds. `--net-run RUN` (`ziel3_12h`), `--out FOLDER` (`ziel3_roc`), `--grid LIST`, `--hours H`, `--seed S`, `--no-check` |
| `ziel3_export_pc1.py` | CSV digest of the PC 1 journals of one run: `--name RUN`, `--contract PATH` |

The scripts in `experiments/ziel3_simulation/auswertung/` computed the result files of the runs
on PC 2. They expect the data layout of PC 2 and are included for reading, see the README of
that folder. Procedure, journals and acceptance checks: `experiments/ziel3_simulation/README.md`.

## Goal 3 — evaluation (`analysis/`)

```
.venv\Scripts\python -m streamlit run analysis/belastungsmodell_app/app.py          # load model of thesis chapter 5
.venv\Scripts\python -m streamlit run analysis/belastungsmodell_app/app_ziel3.py    # curves of appendix G, simulation runs, curves over the device thresholds
```

Both applications open in the browser. The model pages need no data files; the pages on the
simulation read the result files in `docs/reports/`. The programs in `analysis/roc_ziel3/`
(`python -m analysis.roc_ziel3.run_roc` and others) recompute the filter decisions with the
unchanged core of the Dashboard from the journals of the runs; these journals are not part of
the repository, so the programs run only where the `logs/` folders exist. Commands and modules:
`analysis/roc_ziel3/README.md`, `analysis/belastungsmodell_app/README.md`.

## Analysing results of goals 1 and 2

```
.venv\Scripts\python experiments/ziel1/ziel1_analyze.py experiments/ziel1/logs/<filtered>.jsonl --baseline experiments/ziel1/logs/<baseline>.jsonl --json report.json
.venv\Scripts\python experiments/ziel2/ziel2_analyze.py experiments/ziel2/logs/<ack>.jsonl experiments/ziel2/logs/<fault>.jsonl --json report.json
.venv\Scripts\python experiments/campaign_2pc/campaign.py --report-only <run dir>
```

| Script | Flags |
|---|---|
| `ziel1_analyze.py JOURNAL` | `--baseline JOURNAL` (unfiltered run for the traffic comparison), `--json PATH` (machine-readable summary), `--per-trial` (list every EPR), `--confidence P` (`0.95`) |
| `ziel2_analyze.py JOURNAL [JOURNAL …]` | `--json PATH`, `--per-trial`, `--confidence P` (`0.95`) |
| `campaign.py --report-only [RUN_DIR]` | see the campaign table above |

## Demo scenario

The worked example of the thesis on one PC: two beds, four providers, no TLS. Start the
Dashboard with `--no_tls --ip <IP>` first.

```
.venv\Scripts\python experiments/screenshots/demo_scenario.py --ip 192.168.0.106
```

Flags: `--ip IP` (required), `--auto SEC` (advance the phases automatically), `--headless`
(phases 0–2 against a headless Dashboard, prints the patient cards), `--hold1 SEC` (`7`),
`--echo` (print provider output). See `experiments/screenshots/README.md`.

## TLS / PKI

No keys or certificates are included in this repository. Generate your own testbed PKI
(one CA, one certificate per participant) once:

```
.venv\Scripts\python tools/make_testbed_pki.py --ip 192.168.0.106 192.168.0.105
.venv\Scripts\python tools/make_testbed_pki.py --verify
.venv\Scripts\python tools/make_testbed_pki.py --selftest
```

| Flag | Meaning | Default |
|---|---|---|
| `--out DIR` | PKI folder. | `pki/` |
| `--ip IP [IP …]` | IP addresses written into every certificate's SAN (plus name, localhost, 127.0.0.1). | none |
| `--force` | Overwrite an existing CA / participant. | off |
| `--participants NAME[:ROLE] …` | Participant set. | `dashboard testconsumer spo2 abp ecg vent` |
| `--add NAME` | Issue one more participant with the existing CA. | — |
| `--verify [NAME …]` | Chain / EKU / SAN check against `pki/ca/ca.pem`. | — |
| `--selftest` | Mutual-TLS handshake over localhost; must pass, and must fail with a foreign CA. | — |
| `--role provider\|consumer` | Role for `--add`. | — |
| `--server NAME`, `--client NAME` | Participants for `--selftest`. | `spo2`, `dashboard` |

Result: `pki/ca/` (CA certificate and key), `pki/manifest.json`, and one folder per participant
with key, certificate and CA under every file name the stacks expect. The Dashboard loads
`pki/dashboard/` automatically; providers take `--certs-dir pki/<name>`. The whole `pki/`
folder is ignored by git.

End-to-end check of one provider over mutual TLS with a test consumer:

```
.venv\Scripts\python tools/live_check.py --mdib testbed/mdib/ecg_mdib_v2.xml --certs pki/ecg --ip 192.168.0.106 --port 10002
```

Flags: `--mdib`, `--certs`, `--ip`, `--port` (required), `--epr` (random `urn:uuid`),
`--room` (`ICU-1`), `--no-room`.

## MDIB tools

```
.venv\Scripts\python tools/validate_mdib.py testbed/mdib/ecg_mdib_v2.xml       # XSD validation; --xsd-dir DIR overrides the schema folder
.venv\Scripts\python tools/check_mdib_rules.py testbed/mdib/*_v2.xml          # cross-file rules R1–R9; --quiet-notes hides notes
```

Exit code 0 means valid / no failure.

## Tests

```
.venv\Scripts\python -m unittest discover -s tests -t .
.venv\Scripts\python -m unittest discover -s experiments\ziel3_simulation\pc2\tests
.venv\Scripts\python -m unittest discover -s experiments\ziel3_simulation\tests
```

| Suite | Tests | State |
|---|---:|---|
| Dashboard (`tests/`, ten files) | 194 | `stand-messungen` — the number given in the thesis (section 3.2.6, appendix C.2) |
| Evaluation of goal 3 (`tests/`, ten files) | 236 | added for `abgabe` |
| `tests/` in total | 430 | `abgabe`: `Ran 430 tests … OK` |
| Generator of the simulation (`pc2/tests`) | 38 | `abgabe`, second command |
| PC 1 side of the simulation | 12 | `abgabe`, third command |

In a fresh clone 28 of the 430 tests are skipped, because they compare against the journals of
the simulation runs, which are not part of the repository; the result is then
`OK (skipped=28)`. The tests need no network, no PKI and no running provider. The 194 Dashboard
tests take a few seconds, the whole of `tests/` a few minutes. Both requirement files have to be
installed. The Dashboard package uses the bare imports `app.*` and `device.*`, which
`tests/__init__.py` makes importable from any working directory. The files are listed in
`tests/README.md`.

## Results and logs

The journals and packet captures of the thesis runs are not part of the repository. They are
kept separately; runs of goals 1 and 2 can be repeated with the commands above. At run time the
components write to `Dashboard/logs/` (`sdc_consumer.log`, `events.jsonl`),
`experiments/ziel1/logs/`, `experiments/ziel2/logs/`, `experiments/campaign_2pc/logs/<run>/`,
`experiments/ziel3_simulation/logs/<run>/` and `experiments/screenshots/logs/`; all of these are
ignored by git. Included are the result files computed from the goal 3 runs, in `docs/reports/`.

## Documentation

- `Dashboard/ARCHITECTURE.md` — architecture reference of the consumer (packages, threads, data flow, alarm model).
- `docs/Anhang_ausfuehrlich.md` — parts of the thesis appendix shortened for print (German).
- `docs/model/` — the normative model text and the deviation protocol of the implementation.
- `docs/reports/` — MDIB analysis and the result files of the goal 3 runs.
- `experiments/README.md`, `experiments/ziel1/README.md`, `experiments/ziel2/README.md`, `experiments/campaign_2pc/README.md` — goal maps, metrics and run procedures.
- `experiments/ziel3_simulation/README.md` (with `pc2/` and `auswertung/`), `analysis/belastungsmodell_app/README.md`, `analysis/roc_ziel3/README.md` — simulation and evaluation of goal 3.
- `testbed/mdib/README.md` — design notes of the testbed MDIBs.
- Every component folder has a short `README.md`.
