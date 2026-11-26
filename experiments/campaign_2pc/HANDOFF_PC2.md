# HANDOFF_PC2 — instructions for the Claude Code session on PC 2 (192.168.0.105)

Date: 22.10.2026. Author: the session on PC 1. Your role on PC 2: **only preparing the environment and starting the run**.
Do not change code, do not delete files, never print the contents of `pki/` or copy them into the chat, do not use git.

## 1. What this is

Master's thesis: ICU Dashboard on SDC/BICEPS (sdc11073 3.0.0). A night measurement run on two PCs:
PC 1 (192.168.0.106) starts a headless Dashboard and observes; PC 2 (this PC, 192.168.0.105) starts the test providers
(`testbed_provider.py`) **by itself** according to a schedule, raises alarms, produces connection losses (including `netdown`
by a firewall rule) and writes a journal. There is no control channel between the PCs, only a time exchange over UDP.
Details: `experiments/campaign_2pc/SPEC_C.md` in this folder.

## 2. What is in the folder (root = the folder that contains `experiments/`, `testbed/`, `pki/`)

```
experiments/harness.py                      Prov (provider as a subprocess), provider_cmd, DEVICES
experiments/campaign_2pc/pc2_scheduler.py   scheduler (this PC)
experiments/campaign_2pc/schedule_c.json    schedule (shared by both PCs, do not change)
experiments/campaign_2pc/SPEC_C.md          specification
experiments/campaign_2pc/HANDOFF_PC2.md     this file
testbed/providers/testbed_provider.py       test SDC provider
testbed/mdib/{ecg,spo2,abp,vent}_mdib_v2.xml
pki/{ecg,spo2,abp,vent}/userkey.pem sdccert.pem cacert.pem   (mTLS; do not open)
logs/                                       appears at the start: logs/<scenario_id>/pc2_journal.jsonl, console.txt
```

## 3. Checking the environment

Required: Python ≥ 3.10 (checked with 3.13), the package `sdc11073` 3.0.x with its dependencies (lxml etc.).

```
python --version
python -c "import sdc11073, importlib.metadata as m; print(m.version('sdc11073'))"
python -c "import sys; print(sys.executable); print(sys.base_prefix)"
ipconfig
```

The address 192.168.0.105 must be on the adapter that faces the same network as PC 1 (Ethernet or Wi-Fi).
If the address is different, start with `--ip <address>`; PC 1 then has to be told the new address (it is written in its configuration).

## 4. Firewall of PC 2 (an administrator console is needed, ask the user before executing)

Inbound connections from PC 1 must reach the Python interpreter that actually listens on the sockets:
UDP 3702 (WS-Discovery), TCP 12000–13999 (providers), UDP 5558 (time exchange). A rule by program
for **both** paths from section 3 (`sys.executable` and, if this is a venv, the base `python.exe` in `sys.base_prefix`):

```
netsh advfirewall firewall add rule name="SDC_C_python_in" dir=in action=allow program="<path to python.exe>" enable=yes profile=any
```

Check that the firewall is switched on (needed for `netdown`): `netsh advfirewall show currentprofile` → `State ON`.
If it is switched off, `netdown` does not work (the blocking rule blocks nothing); the selftest reports this.

## 5. Start (from the root of the folder, administrator console because of `netdown`)

```
python experiments\campaign_2pc\pc2_scheduler.py --selftest
```
Expected: `SELFTEST: OK`. Checked are: Python, sdc11073, MDIB, PKI files (by name), address, UDP 5558,
administrator rights, state of the firewall, start of one provider with mTLS on port 12999 (≈ 10 s).

Optional, 10 s: `python experiments\campaign_2pc\pc2_scheduler.py --netdown-test 5` — a blocking rule
towards PC 1 for 5 s, then a check that it has been removed (and that the watchdog process would have removed it by itself). During the test `ping 192.168.0.106` does not answer.

Pilot (≈ 15 min) — only after the user has said that `campaign.py --listen --pilot` is running on PC 1:
```
python experiments\campaign_2pc\pc2_scheduler.py --pilot
```
Full run (≈ 5.5 h) — after `PILOT: OK` on PC 1 and the user's command; `campaign.py --listen` must be running on PC 1:
```
python experiments\campaign_2pc\pc2_scheduler.py
```

What is visible in the console: the scheduler waits for the first time request from PC 1 (up to 30 min), then prints the phases and trials;
at the end "scenario finished", and it answers PC 1 for another 10 minutes (this is normal, do not interrupt). `--dry-run` prints the plan without starting anything.

Stopping: Ctrl-C — the provider is killed, the firewall rules are removed, the journal is closed. After Ctrl-C the scheduler reports the state `aborted` to PC 1 for another 60 s; PC 1 keeps waiting for up to 30 min, so a restart (if necessary with `--phases <remaining phases>`) can be made without restarting PC 1. If after a crash
a leftover rule is suspected: `python experiments\campaign_2pc\pc2_scheduler.py --netdown-clear`.

## 6. After the run

Copy **the whole folder** `logs\<scenario_id>\` to PC 1 (it contains `pc2_journal.jsonl`, `console.txt`, `scenario.json`,
`netdown_watchdog.log`). On PC 1 `campaign.py --merge … --pc2 <this folder>` is executed; the verdict is given by PC 1.

## 7. If something is wrong

- `SELFTEST: PROBLEME` with a text — pass it on to the user verbatim; typical causes: not an administrator console,
  firewall switched off, no `sdc11073`, a different IP.
- The provider does not print `ready:` — look at `logs\<id>\console.txt` (errors loading the MDIB or the certificates).
- `PC 1 did not ask for the clock` — PC 1 has not started `--listen`, or UDP 5558 is closed by the firewall of PC 2; the scenario
  starts anyway, but a merge without time samples is impossible → better to abort (Ctrl-C) and fix it.
- Port in use (`Address already in use`) — a provider is left over from the previous start: `tasklist | findstr python`, terminate it.

None of this requires changes to the code. If it seems that it cannot be done without a change, stop and ask the user.
