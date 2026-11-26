# SPEC_C — night run on two PCs without a control channel (variant C)

Date: 22.10.2026. Files: `schedule_c.json` (shared schedule), `pc2_scheduler.py` (PC 2),
`pc1_listener.py` + `netmode.py` + `campaign.py --listen/--merge` (PC 1), folder `pc2_c/` (everything for PC 2),
`HANDOFF_PC2.md` (instructions for the session on PC 2).

## 1. Principle

- PC 2 (192.168.0.105) executes the scenario from `schedule_c.json` **by itself**: it starts and stops
  `testbed_provider.py`, sends `alarm on/off`, produces the connection losses and writes its own journal. There are no commands from PC 1.
- PC 1 (192.168.0.106) only **observes**: headless Dashboard with journal (the real `SdcMyConsumer`),
  automatic acknowledgement when an alarm appears, tshark on the network adapter, CPU/memory, polling of the PC 2 clock over UDP.
- After the run `campaign.py --merge` matches the journals by EPR and time and generates events in the format
  of the existing runners, after which the **unchanged** analyzers `ziel1_analyze.py` and `ziel2_analyze.py` work.
- `testbed_provider.py`, `harness.py`, the runners and the Dashboard are not changed.

## 2. Addresses and ports

| What | Value |
|---|---|
| PC 1 Dashboard | 192.168.0.106 (Wi-Fi), WS-Discovery UDP 3702, HTTP event server on an ephemeral TCP port |
| PC 2 providers | 192.168.0.105, TCP 12100–12409 (Ziel 1 filtered), 12600 (ack), 12700–12804 (connection losses), 13700–13804 (reconnect = port + 1000), 12500–12543 (baseline), 12999 (selftest) |
| Clock exchange | UDP 5558 on PC 2 (PC 1 → PC 2 request, PC 2 → PC 1 reply), once every 10 s |
| Firewall PC 1 | inbound for `python.exe` (UDP 3702, any TCP — the notifications arrive on an ephemeral port) |
| Firewall PC 2 | inbound for `python.exe` (UDP 3702, TCP 12000–13999, UDP 5558) |
| netdown | on PC 2 the rules `SDC_C_NETDOWN_OUT/IN`: `action=block remoteip=192.168.0.106` |

The range 12xxx is chosen so that it does not overlap with the local campaign (10xxx/11xxx) when the pipeline is checked on one PC.

## 3. Schedule (full run / `--pilot`)

Order of the phases and windows of PC 1:

| Phase | Window of PC 1 | Kind | n full | n pilot | Port base | Estimate |
|---|---|---|---|---|---|---|
| z1_filtered | filtered (`--room ICU-1`) | Ziel 1 | foreign 150, mismatch 150, own 5, nolocation 5 (interleave) | 3/3/1/1 | 12100 | ≈ 145 min |
| z2_ack | filtered | acknowledgement | accept 300, refuse 20 | 3 / 2 | 12600 | ≈ 32 min |
| z2_fault | filtered | connection losses | kill 30, shutdown 30, suspend 10, reconnect 5, netdown 30 (interleave) | 1 each | 12700 | ≈ 112 min |
| z1_baseline | baseline (no room) | Ziel 1 | foreign 20, mismatch 20, own 2, nolocation 2 | 2/2/1/1 | 12500 | ≈ 21 min |

In total ≈ 5.2 h + pauses (`phase_gap_s` 30, `window_switch_s` 90); the pilot ≈ 15 min with pauses.
Timings (`timing`): `ready_timeout_s` 40, `z1_window_s` 18, `z1_gap_s` 4, `bind_timeout_s` 90, `bind_settle_s` 3,
`ack_settle_s` 5, `ack_hold_s` 4, `ack_pause_s` 2, `suspend_hold_s` 45, `netdown_hold_s` 45, `reconnect_wait_s` 20,
`reconnect_hold_s` 40, pauses after a connection loss kill 25 / shutdown 15 / suspend 10 / netdown 15, `linger_s` 600.
Roles → devices as in `ziel1_experiment.py`: own = ecg, foreign = spo2, nolocation = abp, mismatch = vent;
provider arguments: own `--room ICU-1 --bed 1`; foreign `--room ICU-2 --bed 1`; mismatch `--scope-room ICU-1 --room ICU-2 --bed 1`; nolocation without `--room`.

## 4. Protocol of a trial

**Ziel 1 (both phases).** Fresh EPR, own port → wait for `ready:` (≤ 40 s, otherwise `trial_end outcome=provider_failed`) →
hold for 18 s (window for Hello/Probe, the decision and the precheck of PC 1) → `status` → `subscriptions=N` into the journal →
`trial_end` → graceful `shutdown` (Bye + SubscriptionEnd) → pause 4 s.

**Acknowledgement.** One ECG provider (`--room ICU-1 --ack accept`) → `ready:` → poll `status` every 2 s until
`subscriptions ≥ 1` (PC 1 has bound; ≤ 90 s, otherwise `phase_abort`) → 5 s → block accept: 300 × [`alarm tachycardia on`,
4 s, `alarm tachycardia off`, 2 s] → `ack refuse` → block refuse: 20 × the same. PC 1 sees the alarm condition (polling of
`snapshot_alert_presence` every 0.25 s) and presses `silenceAlarm()` once per appearance; RTT, InvocationState,
Presence=Ack entirely on PC 1 (`ack_rtt`, `ack_confirmed`, `auto_ack`).

**Connection losses.** Fresh ECG provider per trial → `ready:` → `status` until `subscriptions ≥ 1` (≤ 90 s, otherwise
`trial_end outcome=not_bound`) → 3 s → `fault_injected{t_loss}` → method:
- `shutdown`: stdin `shutdown`; `kill`: TerminateProcess of the process tree; `suspend`: SuspendThread of all threads,
  45 s, `fault_restore`, resume, kill; `reconnect`: kill, 20 s, the same EPR on port + 1000 (`reconnect_started`), 40 s, shutdown;
- `netdown`: start of a **watchdog process** (a separate detached process, removes the rules after 45 + 15 s
  in any case) → `netsh advfirewall ... add rule ... action=block remoteip=192.168.0.106` (out and in) →
  `t_loss` = the moment `netsh` returns → 45 s → removal of the rules (`netdown_rule delete`, `fault_restore`) → the provider
  lives for another 40 s (PC 1 reconnects, `technical_alarm cleared`) → shutdown. At the start and at any exit
  of the scheduler the rules are removed; `--netdown-clear` removes them manually; `--netdown-mode skip` skips the method.

**Condition for the start of a connection loss:** `subscriptions ≥ 1` in the reply of the provider itself (the equivalent of `wait_bound` of the local
runner) + 3 s.

## 5. Journal of PC 2 (`logs/<scenario_id>/pc2_journal.jsonl`, time of PC 2)

Every record: `seq, t_abs (time.time()), t_mono, iso, event` + fields:

| event | fields |
|---|---|
| scenario_start | scenario_id, pc2_ip, pc1_ip, room, other_room, tls, pilot, netdown_mode, phases, estimate_s, schedule (full copy), host, python |
| pc1_seen | requests, peer (first clock request from PC 1) |
| phase_start / phase_end | phase, window, kind, spec |
| window_switch | to_window, next_phase, pause_s |
| trial_start | phase, kind (ziel1/ack/fault), trial, role, device, epr, port; ziel1: mode, expected; ack: channel, cond_handle, signal_handle, accept, refuse; fault: method, bound_s |
| provider_ready | trial, epr, after_s |
| bound | trial, epr, subscriptions, after_s (timeout=true on failure) |
| status | trial, epr, subscriptions |
| ack_policy | epr, policy, n |
| alarm | trial (= number of the cycle), epr, channel, on (true/false), policy, cond_handle, signal_handle |
| fault_injected | trial, method, epr, **t_loss**, pid, bound_s, rule_ok (netdown) |
| fault_restore | trial, method, epr, ok |
| reconnect_started | trial, epr, t_restart, port |
| reconnect_status | trial, epr, subscriptions |
| netdown_rule | trial, action add/delete, ok, watchdog_pid |
| trial_end | phase, kind, trial, role/method, epr, port, outcome, provider_subscriptions, elapsed_s / cycles |
| trial_skipped, phase_abort, phase_error | reason |
| clock_reply | peer, t1, t2, t3, seq |
| scenario_end | aborted, trials_done, netdown_rules_left |

Next to it: `console.txt` (output of the scheduler and of all providers), `scenario.json` (the resolved schedule),
`netdown_watchdog.log`.

## 6. Exchange of the clock offset

UDP, JSON. PC 1 every 10 s: `{"t1": <time.time() of PC 1>, "seq": n}` → PC 2 replies
`{"t1","seq","t2": <receive>, "t3": <send>, "scenario_id","pilot","phase","window","state","trial","trials_done","trials_total"}`.
PC 1 on receiving at `t4`: `offset = ((t2 − t1) + (t3 − t4)) / 2` (PC 2 − PC 1), `rtt = (t4 − t1) − (t3 − t2)`,
record `clock_offset{offset_s, rtt_s, t1, t4, phase, window, state, …}` in the journal of PC 1, and `pc2_phase` when the phase changes.
The exchange runs all night and not only at the beginning and at the end: during the merge the time of PC 2 is converted using the nearest samples
with linear interpolation (the drift is taken into account), the uncertainty of each conversion is ≤ rtt/2, samples with rtt > 250 ms
are discarded. In the dossier: offset at the beginning and at the end, drift, maximum uncertainty. During `netdown`
(45 s) the requests do not get through — this is expected and is ignored.

## 7. Order of starting

1. PC 1: `campaign.py --listen --pilot` (or without `--pilot`). The listener of the window `filtered` starts and polls the clock of PC 2.
2. PC 2 (administrator console): `pc2_scheduler.py --selftest` → `SELFTEST: OK`; then `--pilot` (or the full run).
   The scheduler waits for the first clock request from PC 1 for up to 30 min (`--wait-pc1-min`), then starts the phases.
3. Transition to the window baseline: PC 2 sets `phase=z1_baseline, state=waiting` and waits 90 s; the listener `filtered`
   sees `window=baseline` in the reply and exits; `campaign.py` starts the listener `baseline` (no room).
4. End: PC 2 sets `state=done` and answers the requests for another 10 min (`linger_s`); the listener `baseline`
   (or `filtered`, if there is no baseline) sees `done` and exits; `--listen` prints the command for `--merge`.
5. PC 2: copy the folder `logs/<scenario_id>/` to PC 1. PC 1: `campaign.py --merge <run_dir> --pc2 <folder>`.

The order "first PC 1, then PC 2" is not mandatory: the scheduler waits for PC 1 for up to 30 min, the listener waits for PC 2 for up to
60 min (`wait_start_min`). Safety nets of PC 1: `max_hours` 10; no replies for 30 min after at least one — exit.

## 8. How PC 1 recognises the end of the scenario

Only from the replies to the clock request: `state == "done"` (PC 2 keeps it for `linger_s` = 600 s after `scenario_end`).
If PC 2 has crashed, there are no replies → after `idle_exit_min` = 30 min the listener exits with the reason
`no reply from PC 2`; `--merge` then marks `PC-2-Journal ohne scenario_end` as a problem (`GESAMT: FEHLER`).

## 9. Merge (`campaign.py --merge RUN --pc2 PATH`)

- Phases of PC 2 → directories `ziel1_filtered/`, `ziel2_ack/`, `ziel2_fault/`, `ziel1_baseline/` with `events.jsonl`:
  synthetic records in the format of the runners + all records of PC 1 with the same EPR.
- Ziel 1: `trial_start{trial, role, epr, port, mode, expected}` (time PC 2 → PC 1), `trial_end{outcome,
  provider_subscriptions}`; outcome: `not_discovered` if PC 1 has no `device_discovered` for the EPR (counted
  **against TRA** — the analyzer sees "no decision"), otherwise `reject` / `subscribed` / the decision.
- Acknowledgement: cycle = a record `alarm on` of PC 2; the `auto_ack` of PC 1 with `t_seen` in
  [t_on − 0.5 s; t_off + 1 s] is matched to it; `ack_trial_start/ack_trial_end{policy, press_id, ack_state, confirmed, …}`;
  without a press → `ack_state = no_press` (failure). The analyzer takes the transactions (`ack_rtt`) as they are.
  The dossier has two tables: by cycles (n = number of alarms raised) and by transactions.
- Connection losses: `fault_injected{t_loss}` (converted, + `uncertainty_s`), `fault_trial_end{dt_fallback_s, dt_alarm_s,
  dt_departed_s, cause, bound_s, outcome, dt_reconnect_s, reconnect_ok}` from `fallback` / `technical_alarm raised|cleared`
  / `device_departed` of PC 1 after t_loss; `netdown` gets `bound_s` 30 and its own statistics of the return.
- Capture: `filtered/trials_all.jsonl` (all `trial_start` of the window) → bytes per role through the time windows of the trials.
- Background devices: EPRs with `device_discovered` on PC 1 that do not belong to any trial.
- Verdict `GESAMT: OK` (pilot: `PILOT: OK`) if every phase made all planned trials, there are no
  failed trials (by the same rules as locally), all three analyses exist, there is at least one clock sample and
  `scenario_end` without `aborted`. Otherwise `FEHLER`/`PROBLEME` with a list. A missing C++ background and coarse clocks are warnings.
- Dossier `dossier.md` (for the full run also `docs/thesis/campaign_netz_ergebnisse.md`): the clock offset and its change;
  "nicht entdeckt" separately and against TRA; a schedule instead of event-driven control in "Grenzen"; n and the one-sided
  95 % Clopper–Pearson bound for every metric; the network series replaces TRA and acknowledgement and supplements the connection losses
  (netdown); the local series is the Voruntersuchung; C++ is background only.

## 10. What is checked where before the night

- PC 2: `--selftest` (Python ≥ 3.10, sdc11073 3.0.x, MDIB, PKI files by name, IP, UDP 5558, administrator, firewall ON,
  start of one provider with mTLS on port 12999), `--netdown-test 5`.
- PC 1: `campaign.py --selftest-merge` (synthetic journals through the merge, analyzers, report, dossier),
  `--listen --dry-run`, capture on Wi-Fi without administrator rights.
- A two-machine pilot `--pilot` + `--merge` → `PILOT: OK` is a mandatory condition for the night run.
