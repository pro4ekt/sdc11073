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
