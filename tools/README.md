# tools — PKI and MDIB utilities

All are stand-alone scripts, run from the repository root with
`.venv\Scripts\python tools/<script> …` (Linux: `.venv/bin/python`).

## `make_testbed_pki.py` — testbed PKI

One CA and one certificate per participant into `pki/`. No keys or certificates are part of the
repository; the whole `pki/` folder is ignored by git. The script never prints key material.

```
.venv\Scripts\python tools/make_testbed_pki.py --ip 192.168.0.106 192.168.0.105
.venv\Scripts\python tools/make_testbed_pki.py --verify
.venv\Scripts\python tools/make_testbed_pki.py --selftest
```

| Flag | Meaning | Default |
|---|---|---|
| `--out DIR` | PKI folder. | `pki/` in the repository root |
| `--ip IP [IP …]` | IP addresses written into every certificate's SAN (plus name, `localhost`, `127.0.0.1`). | none |
| `--force` | Overwrite an existing CA / participant. | off |
| `--participants NAME[:ROLE] …` | Participant set. | `dashboard testconsumer spo2 abp ecg vent` |
| `--add NAME` | Issue one more participant with the existing CA. | — |
| `--role provider\|consumer` | Role for `--add`. | `provider` |
| `--verify [NAME …]` | Chain / EKU / SAN check against `pki/ca/ca.pem`. | — |
| `--selftest` | Mutual-TLS handshake over localhost; must pass, and must fail with a foreign CA. | — |
| `--server NAME`, `--client NAME` | Participants for `--selftest`. | `spo2`, `dashboard` |

Result: `pki/ca/` (CA certificate and key), `pki/manifest.json` (issued participants, IPs,
fingerprints), and one folder per participant with key, certificate and CA under every file name
the stacks expect. The Dashboard loads `pki/dashboard/` automatically; providers take
`--certs-dir pki/<name>`.

## `live_check.py` — end-to-end check of one provider

Starts one `testbed_provider.py` as a subprocess, connects with an sdc11073 consumer over mutual
TLS (`pki/testconsumer`), reads MDIB and contexts, raises an alarm, acknowledges with both
policies, writes the EnsembleContext, sends `shutdown` and waits for the WS-Discovery Bye.

```
.venv\Scripts\python tools/live_check.py --mdib testbed/mdib/ecg_mdib_v2.xml --certs pki/ecg --ip 192.168.0.106 --port 10002
```

| Flag | Meaning | Default |
|---|---|---|
| `--mdib FILE` | MDIB file of the provider. | required |
| `--certs DIR` | Participant folder of the provider, e.g. `pki/ecg`. | required |
| `--ip IP` | Adapter IPv4 of this host. | required |
| `--port PORT` | Port of the provider. | required |
| `--epr EPR` | Endpoint reference of the provider. | random `urn:uuid` |
| `--room ROOM` | Room of the provider. | `ICU-1` |
| `--no-room` | Start the provider without a location. | off |

## `validate_mdib.py` and `check_mdib_rules.py` — MDIB checks

```
.venv\Scripts\python tools/validate_mdib.py testbed/mdib/ecg_mdib_v2.xml
.venv\Scripts\python tools/check_mdib_rules.py testbed/mdib/ecg_mdib_v2.xml testbed/mdib/spo2_mdib_v2.xml testbed/mdib/abp_mdib_v2.xml testbed/mdib/vent_mdib_v2.xml
```

- `validate_mdib.py FILE [FILE …]` — XSD validation against the BICEPS schemas shipped with
  sdc11073 (offline). `--xsd-dir DIR` overrides the schema folder.
- `check_mdib_rules.py FILE [FILE …]` — cross-file rules R1–R9 (handle uniqueness,
  state/descriptor consistency, LOINC check digits, …) for the testbed MDIBs.
  `--quiet-notes` hides notes.

Exit code 0 means valid / no failure.
