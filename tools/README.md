# tools — PKI and MDIB utilities

All are stand-alone scripts, run from the repository root with `.venv\Scripts\python tools/<script> …`.

- `make_testbed_pki.py` — one CA and one certificate per participant (`dashboard`,
  `testconsumer`, `spo2`, `abp`, `ecg`, `vent`) into `pki/`; modes `--ip … [--force]`
  (issue), `--add NAME`, `--verify`, `--selftest`. Never prints key material. Flags in the
  root README, section "TLS / PKI".
- `live_check.py` — end-to-end check of one `testbed_provider.py` instance: starts it as a
  subprocess, connects with an sdc11073 consumer over mutual TLS (`pki/testconsumer`),
  reads MDIB and contexts, raises an alarm, acknowledges with both policies, writes the
  EnsembleContext, sends `shutdown` and waits for the WS-Discovery Bye.
- `validate_mdib.py` — XSD validation of MDIB files against the BICEPS schemas shipped with sdc11073 (offline).
- `check_mdib_rules.py` — cross-file rule checker (handle uniqueness, state/descriptor consistency, LOINC check digits, …) for the testbed MDIBs; exit code 0 = no failure.
