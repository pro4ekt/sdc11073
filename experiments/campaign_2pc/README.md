# campaign_2pc — one-command measurement campaign (Ziel 1 + Ziel 2) on PC 1

`campaign.py` runs the existing runners (`experiments/ziel1/ziel1_experiment.py`,
`experiments/ziel2/ziel2_ack.py`, `experiments/ziel2/ziel2_fault.py`) phase by phase under one run
directory, each phase as its own process with its own headless dashboard and
its own journal, with a tshark capture around every phase, a resource log
(CPU / memory of PC 1 once a minute), a status file updated after every phase,
the analyzers at the end, a one-page report for the morning check and a German
results dossier. The runners and `harness.py` are unchanged; they still work on
their own (`ziel1_run_all.py`, `ziel2_run_all.py`, see the goal READMEs).

Step 1 (this version): providers are **local** (started on PC 1 by the runners
via `harness.Prov`). Step 2 adds `providers.source = "remote"` (agent on PC 2)
and the `netdown` loss type; the phase flow stays the same.

## 1. Prerequisites on PC 1

- Dashboard window **closed** (each phase starts its own headless dashboard;
  two dashboards on one host would both discover the same providers).
- `.venv` of the repo (sdc11073 3.0.0), PKI under `pki/` (never printed).
- Optional but recommended: Wireshark/Npcap with `tshark` (auto-detected in
  `C:\Program Files\Wireshark`). The Npcap loopback adapter is used when the
  providers are local; no administrator rights are needed for that. With remote
  providers the adapter that owns `ip` is used (may need Npcap "admin only"
  turned off or an elevated console).
- Firewall: inbound UDP 3702 and TCP for the venv's real `python.exe`
  (see `experiments/ziel2/README.md` §4); irrelevant for the loopback-only step 1.
- Background load (optional): the two C++ providers in VirtualBox (bridged),
  started by hand with `--room ICU-1 --patient-id P009`. They are discovered by
  every phase's dashboard and noted in the report; fewer than
  `background.expect` discovered = warning, not an error.

## 2. Usage (repo root)

```
.venv\Scripts\python experiments\campaign_2pc\campaign.py --pilot        # ~10 min, ends with PILOT: OK | PILOT: PROBLEME
.venv\Scripts\python experiments\campaign_2pc\campaign.py                # full run (night), ends with GESAMT: OK | FEHLER
.venv\Scripts\python experiments\campaign_2pc\campaign.py --resume       # continue the newest run (phases with state ok are skipped)
.venv\Scripts\python experiments\campaign_2pc\campaign.py --dry-run      # print the runner commands, start nothing
```

Options: `--config PATH` (default `campaign.json`), `--run-dir PATH`,
`--phases smoke,ziel2_ack` (subset), `--no-capture`, `--resume [RUN_DIR]`.
Ctrl-C stops the running phase (the runner handles it) and leaves the status
file; `--resume` re-runs the unfinished phases, the old attempt of such a phase
is kept as `<phase>_prev_<ts>/`.

## 3. Phases and planned n (`campaign.json`)

| Phase | Runner | Full run | `--pilot` |
|---|---|---|---|
| `smoke` | ziel1 (filtered) + ack + fault | 4 + 3 + 2 trials | same |
| `ziel1_filtered` | `ziel1_experiment.py --mode filtered` | foreign 150, mismatch 150, own 5, nolocation 5 | 3/3/1/1 |
| `ziel1_baseline` | `ziel1_experiment.py --mode baseline` | 20/20/2/2 | 2/2/1/1 |
| `ziel2_ack` | `ziel2_ack.py` | accept 300, refuse 20 | 3 / 2 |
| `ziel2_fault` | `ziel2_fault.py` | kill 30, shutdown 30, suspend 10, reconnect 5 (`netdown` reserved) | 1 each |

Every phase gets its own provider port range (`port_base` / `port`), so
journals and captures can be attributed by port. Phase timeout =
max(10 min, 2 × estimate); on timeout the runner's process tree is killed and
the phase is marked `failed`.

Phase states: `ok` (runner rc 0 and all planned trials done), `incomplete`
(fewer trials than planned), `failed` (rc ≠ 0 or timeout), `dry`.
A failed trial never stops a phase (runner behaviour); a failed phase never
stops the campaign.

## 4. Output

```
experiments/campaign_2pc/logs/<pilot_|run_><ts>/
  campaign_status.json     state, counts, capture sizes per phase (also read by --resume)
  campaign.log             orchestrator log
  resources.jsonl          {iso, cpu_percent, mem_percent, mem_used_mb, mem_total_mb} once a minute
  campaign_report.md       first line GESAMT: OK | GESAMT: FEHLER (pilot: PILOT: OK | PILOT: PROBLEME),
                           problems, warnings, plan/done/ok/failures table, key numbers, capture bytes
  dossier.md               German results dossier (full run: also copied to docs/thesis/campaign_2pc_ergebnisse.md)
  ziel2_analysis.json/.txt ziel2_analyze over ack + fault journals
  <phase>/events.jsonl     dashboard journal of the phase (runner --log)
  <phase>/console.txt      runner output incl. provider output (--echo-provider)
  <phase>/capture_*.pcapng tshark ring buffer (3 × 200 MB max, filter udp 3702 / tcp 10000-11999)
  <phase>/capture_bytes.json  bytes per provider port and per role (from trial_start.port)
  <phase>/analysis.json/.txt  ziel1_analyze (filtered phase, with --baseline when present)
  smoke/{ziel1,ack,fault}/ the three smoke sub-runs
```

Verdict rules: `PILOT: PROBLEME` / `GESAMT: FEHLER` when a phase is not `ok`,
a trial failed, an analysis is missing or (pilot) the Ziel 1 TRA is below
100 %, or a capture was wanted but missing. Missing C++ background devices and
tshark problems are listed under *Warnungen* and do not change the verdict.

## 5. What the capture counts

Capture filter `auto` = `host <ip>` with local providers: on the Npcap loopback
adapter that is exactly the SDC traffic between the dashboard and the providers
(WS-Discovery multicast and unicast replies, TLS connections in both
directions). `capture_bytes.json` per phase splits it into

- UDP = discovery (multicast Hello/Probe/Bye at port 3702, ProbeMatch /
  ResolveMatch replies on ephemeral ports),
- TCP at a provider port (`capture.provider_ports`) = request/response channel:
  metadata/WSDL at connect, GetContextStates (precheck, ping), GetMdib,
  Subscribe, Renew, Unsubscribe, Set*,
- other TCP = the provider's connections to the dashboard's event port
  (notifications),

and attributes TCP to the trial role by time window (the runners start one
provider at a time, so the latest `trial_start` before a frame owns it). A role
without traffic (foreign, rejected at discovery) is listed with 0 bytes.
Note: the connect step before the precheck (metadata + WSDL, roughly 450 kB
from the provider) is paid for mismatch devices too; the saving of the filter
is in the avoided subscriptions (notifications + renew), visible in the
notification column and growing with the observation window. With remote
providers (step 2) the default filter is `host <provider_host> or udp port 3702`
on the NIC that owns `ip`, so the C++ background devices stay out of the byte
counts. The interface and filter really used are stored in
`<phase>/capture_filter.txt`.

## 6. Network mode (variant C, two PCs)

The night run with the providers on PC 2 uses no control channel: PC 2 runs `pc2_scheduler.py`
(bundle folder `pc2_c` built by `build_pc2_c.py`, see `HANDOFF_PC2.md`), PC 1 runs

```
.venv\Scripts\python experiments\campaign_2pc\campaign.py --listen [--pilot]      # dashboard windows + capture until PC 2 reports done
.venv\Scripts\python experiments\campaign_2pc\campaign.py --merge [RUN] --pc2 <PC 2 run folder>[;<older folder>]
.venv\Scripts\python experiments\campaign_2pc\campaign.py --selftest-merge         # synthetic journals through the merge
```

Schedule, journal format, clock exchange and merge rules: `SPEC_C.md`. Config: `campaign.json` section
`network` (PC 2 IP, capture filter, dossier target `docs/thesis/campaign_netz_ergebnisse.md`) and
`schedule_c.json`. Results of 2026-10-22: `logs/net_run_20261022_025050/` (PC 1) + `logs/run_20261022_025344/` (PC 2).

## Command-line options of `campaign.py`

All commands are run from the repository root; on Linux replace `.venv\Scripts\python` by `.venv/bin/python`.

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

## Two-PC campaign (variant C), step by step

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

`pc2_scheduler.py` has two further options that it uses only for its own watchdog subprocess: `--netdown-watchdog` and `--watchdog-log`.
