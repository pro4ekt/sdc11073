# SDC ICU Dashboard and testbed

An SDC consumer ("Dashboard", PySide6/QML) and a simulated-device testbed written for a
master's thesis at THWS. The Dashboard connects to ISO/IEEE 11073 SDC providers on an ICU
network and implements an adaptive alarm filter across the devices of one patient, a topology
filter that admits only the devices of its own room, and remote acknowledgement of alarms with
connection-loss detection and a safe fallback state. The testbed provides configurable Python
SDC providers, the experiment runners of the thesis and a two-PC simulation with its evaluation.
The SDC protocol stack is the open-source library
[sdc11073](https://github.com/Draegerwerk/sdc11073) (version 3.0.0, MIT licence). The C++-based
SDC stacks used for an additional interoperability check are not part of this repository.

## For readers of the thesis

The measurements of goals 1 and 2 were made at commit `5e81e55` (27.10.2026). All line numbers
quoted in the thesis refer to that commit.

The branch `Dashboard` is the submitted state. It adds the simulation and evaluation of goal 3.
`Dashboard/`, `testbed/` and the programs in `experiments/campaign_2pc/` are unchanged since
`5e81e55`.

The parts of the thesis appendix that were shortened for print are in
[`docs/Anhang_ausfuehrlich.md`](docs/Anhang_ausfuehrlich.md).

## Installation and tests

Windows or Linux, Python 3.13. All commands are given for Windows from the repository root; on
Linux replace `.venv\Scripts\python` by `.venv/bin/python`.

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt -r requirements-app.txt
.venv\Scripts\python -m unittest discover -s tests -t .
.venv\Scripts\python -m unittest discover -s experiments\ziel3_simulation\pc2\tests
```

Expected: `Ran 430 tests … OK (skipped=28)` and `Ran 38 tests … OK`. The 28 skipped tests
compare against the journals of the simulation runs, which are not part of the repository. Of
the 430 tests, 194 test the Dashboard (the number given in the thesis); the others test the
evaluation of goal 3. The tests need no network, no certificates and no running device. Both
requirement files are needed: without `requirements-app.txt` the tests of the evaluation fail
on import. Details: [`tests/README.md`](tests/README.md).

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

Qt arguments such as `-platform offscreen` are passed through. Running `python main.py` from
inside `Dashboard/` is equivalent.

## Running a testbed provider

```
.venv\Scripts\python testbed/providers/testbed_provider.py --mdib testbed/mdib/ecg_mdib_v2.xml --epr urn:uuid:11111111-1111-1111-1111-111111111111 --manufacturer "SDC Testbed" --model "ECG Monitor T1" --friendly-name "Testbed ECG monitor, bed 1" --patient-id P001 --room ICU-1 --bed 1 --certs-dir pki/ecg --ip 192.168.0.105 --port 10010
```

The device is defined by the MDIB file; the four testbed devices are
`testbed/mdib/{ecg,spo2,abp,vent}_mdib_v2.xml` (see [`testbed/mdib/README.md`](testbed/mdib/README.md)).

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
| `--certs-dir DIR` | Participant folder from `pki/`, e.g. `pki/ecg` → mutual TLS. | required unless `--no-tls` |
| `--no-tls` | Plain HTTP (debugging only); excludes `--certs-dir`. | off |
| `--ip IP` | Local IPv4 to bind. | first adapter IPv4 |
| `--port PORT` | Fixed HTTPS port (lets you create one firewall rule). | `0` = chosen by the OS |
| `--ack accept\|refuse` | Remote acknowledgement policy; changeable at run time. | `accept` |
| `--seed N` | Seed of the rest-mode noise (reproducible runs). | none |
| `--ctx-patient`, `--ctx-location`, `--ctx-ensemble`, `--ctx-workflow HANDLE` | Override the context descriptor handles read from the MDIB. | from the MDIB |

Commands on standard input, one per line: `value <channel> <number>`, `rest <channel>`,
`alarm <channel> on|off`, `ack accept|refuse`, `selfcheck off|on`, `status`, `shutdown`.

## Demo on one PC

Two beds with a monitor and a ventilator each, no certificates needed. Start the Dashboard,
then the demo scenario in a second console; replace the address by the IPv4 address of your
network adapter. Phases and options: [`experiments/screenshots/README.md`](experiments/screenshots/README.md).

```
.venv\Scripts\python -m Dashboard --no_tls --ip 192.168.0.106
.venv\Scripts\python experiments/screenshots/demo_scenario.py --ip 192.168.0.106
```

## TLS / PKI

No keys or certificates are part of this repository, and the folder `pki/` is ignored by git.
Generate your own testbed PKI (one CA, one certificate per participant) once:

```
.venv\Scripts\python tools/make_testbed_pki.py --ip 192.168.0.106 192.168.0.105
.venv\Scripts\python tools/make_testbed_pki.py --verify
.venv\Scripts\python tools/make_testbed_pki.py --selftest
```

The Dashboard loads `pki/dashboard/` automatically; providers take `--certs-dir pki/<name>`.
All flags: [`tools/README.md`](tools/README.md).

## Experiments and evaluation

- **Goal 1, topology filter** — runners, metrics and options: [`experiments/ziel1/README.md`](experiments/ziel1/README.md)
- **Goal 2, remote acknowledgement and fallback** — runners, fault injection and options: [`experiments/ziel2/README.md`](experiments/ziel2/README.md)
- **Two-PC campaign for goals 1 and 2 (variant C)** — procedure, options, journals: [`experiments/campaign_2pc/README.md`](experiments/campaign_2pc/README.md)
- **Goal 3, simulation on two PCs** — Dashboard side, generator and its options: [`experiments/ziel3_simulation/README.md`](experiments/ziel3_simulation/README.md)
- **Goal 3, evaluation** — load model and its application: [`analysis/belastungsmodell_app/README.md`](analysis/belastungsmodell_app/README.md); curves over the device thresholds: [`analysis/roc_ziel3/README.md`](analysis/roc_ziel3/README.md)
- **MDIB tools and provider check** — [`tools/README.md`](tools/README.md)

Overview of all experiments, journals and the headless Dashboard: [`experiments/README.md`](experiments/README.md).

## Repository layout

```
Dashboard/            the consumer application (python -m Dashboard); architecture in Dashboard/ARCHITECTURE.md
testbed/              simulated SDC provider and the MDIB files of the four testbed devices
experiments/          runners and analyzers for goals 1 to 3, two-PC campaign, demo scenario
analysis/             evaluation of goal 3: load model, curves over the device thresholds
tests/                unit tests of the Dashboard and of the evaluation
tools/                PKI generator, provider check, MDIB validators
docs/                 shortened appendix parts, model text, result files of the goal 3 runs
requirements.txt      pinned dependencies of Dashboard, testbed and tools
requirements-app.txt  pinned dependencies of the evaluation (analysis/, part of the tests)
```

Not in the repository: `logs/` (journals and captures, created at run time), `pki/` (keys and
certificates, generated locally) and `pc2_c/` (bundle for the second PC, built by
`experiments/campaign_2pc/build_pc2_c.py`). More in [`docs/README.md`](docs/README.md).

## License

MIT, see [`LICENSE`](LICENSE).
