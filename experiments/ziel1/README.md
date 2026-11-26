# Ziel 1 — Kontextbezogene Topologiefilterung (TRA, vermiedene Subskriptionen)

Goal 1 of the thesis: providers not assigned to this bed are identified via the
SDC LocationContext and rejected during discovery, BEFORE any WS-Eventing
subscription. Metric: TRA ≈ 100 % (≥ 99 % in the testbed), measured through
the avoided subscriptions; FF1 compares with the unfiltered connection.

## 1. Implementation map (Dashboard/)

| File : function | What it does |
|---|---|
| `main.py` (`--room ROOM_ID`, `--event-log PATH`) | Target room of this dashboard; `None` = baseline mode (no filter). |
| `app/sdcMyConsumer.py : SdcMyConsumer.__init__` | Holds `target_room`, the session set `_location_rejected`, `_rejected_room_map`, the 15 s reconnect cooldown. |
| `app/sdcMyConsumer.py : _discovery_loop` | Every 2 s `search_services`; per Hello/ProbeMatch reads the `sdc.ctxt.loc` scope (`room_from_scopes`), records `device_discovered`, decides with `decide_room`; a foreign room → `topology_decision{reject, discovery, scope_room_mismatch}` + `subscription_avoided`, no DeviceHandler, no TLS, no metadata. A matching scope is PROVISIONAL (`accept@discovery scope_room_match`). Rejected EPRs are skipped silently on later Hellos. |
| `app/sdcMyConsumer.py : switchRoom` | Runtime room switch; stops handlers of other rooms, un-bans EPRs of the new room. |
| `app/topology_filter.py : location_from_scopes / room_from_scopes` | Parse the location scope with `sdc11073.location.SdcLocation` (no own URL parsing). |
| `app/topology_filter.py : room_from_context_states` | Room of the (associated) LocationContextState from GetContextStates / MDIB. |
| `app/topology_filter.py : decide_room(target, room)` | `accept` / `reject` / `unknown` (no location → accepted, counted separately). |
| `device/handler.py : _phase_connect` | With a filter `start_all(not_subscribed_actions=ALL)`: connect + metadata, ZERO subscriptions; then the precheck; subscriptions only via `_subscribe_reports`. Without a filter (baseline) `start_all` subscribes as before → `topology_decision{accept, no_filter}` + `subscription_created{n_actions}`. |
| `device/handler.py : _phase_context_precheck` | GetContextStates over the authenticated connection; no scope → decides (`context_room_mismatch` reject / `no_location` unknown / `context_room_match`); with a scope → `_confirm_scope_room`. |
| `device/handler.py : _confirm_scope_room` | 2f: scope room must equal the LocationContext room, otherwise `scope_mismatch` + `reject@context_precheck` + `subscription_avoided`. |
| `device/handler.py : _subscribe_reports` | Creates the WS-Eventing subscriptions after the decision (all actions except periodic reports). |
| `device/handler.py : _phase_check_location` | Legacy MDIB check after GetMdib (`mdib` phase), confirmation only. |
| `app/metrics/topology_metrics.py : TopologyMetrics` | Recorder: `record_discovered`, `record_decision`, `record_subscription_created/avoided`, `record_scope_mismatch`, `record_departed`, `emit_summary`. |
| `app/metrics/eventlog.py : EventLog` | JSON Lines journal, one object per line, `t_mono`, `t_abs`, `iso`, `event` + fields. |

Order with `--room`: discovery → scope filter → connect (TLS, GetMetadata,
no subscription) → GetContextStates → decision / confirmation → subscribe →
GetMdib → MDIB confirmation → UI.

## 2. Event-log events (JSON Lines, `--event-log`, default `Dashboard/logs/events.jsonl`)

| event | fields |
|---|---|
| `device_discovered` | epr, scopes, room (from the scope, `null` without location scope) — once per EPR unless the scope room changes |
| `topology_decision` | epr, decision (accept / reject / unknown), phase (discovery / context_precheck / mdib / no_filter), reason (scope_room_mismatch, scope_room_match, scope_confirmed, scope_mismatch, context_room_mismatch, context_room_match, mdib_room_match, mdib_room_mismatch, no_location, filter_disabled), room, target_room |
| `subscription_created` | epr, phase, n_actions |
| `subscription_avoided` | epr, phase, reason |
| `scope_mismatch` | epr, scope_room, context_room, target_room |
| `device_accepted` | epr, phase, subscriptions (legacy) |
| `device_departed` | epr, cause (location / error / graceful) |
| `topology_summary` | discovered, decisions, decisions_by_phase, subscriptions_created, actions_subscribed, subscriptions_avoided, scope_mismatch, departed (at window close / `manager.stop()`) |

Analysis rule: the LAST `topology_decision` per EPR is the final decision of
that connection attempt. A rejected EPR produces no further events in the same
dashboard session — use a fresh `--epr` per trial or restart the dashboard.

## 3. Testbed provider options (`testbed/providers/testbed_provider.py`)

**Design decision: the filter works at ROOM level, not at bed level.** The
dashboard compares only the room (`rm` of the location scope /
`LocationDetail.Room`); patients within a room are separated later by the
ensemble grouping (patient + room), so a bed filter would add nothing but a
second configuration value that can be wrong. The `--bed` option of the
provider only shapes the scope string.

| Role | Options |
|---|---|
| own room | `--room ICU-1 --bed 1` (location scope `sdc.ctxt.loc:…?rm=ICU-1&bed=1` + LocationContextState) |
| foreign room | `--room ICU-2 --bed 1` |
| no location | omit `--room` (no LocationContextState, no location scope) |
| mismatch (scope lies) | `--scope-room ICU-1 --room ICU-2` (scope says ICU-1, context says ICU-2) |

A foreign scope is rejected at discovery WITHOUT checking the context: a
device that stands in the own room but announces a wrong scope is rejected
as well (its local alarms stay at the bedside, nothing is lost to the
patient); the authenticated context is only read for a scope that claims the
own room or for a missing scope.

Independent subscription count on the provider: stdin command `status` →
`status: … subscriptions=N`.

## 4. Unit tests

Working directory `Dashboard/`:

    ..\.venv\Scripts\python -m unittest tests.test_discovery_filter -v

R1 scope parsing (own / foreign / none / malformed), R2 `decide_room`, R3
associated LocationContextState preferred, R4 journal format, R5 recorder
counters and summary, R6 precheck on a stubbed consumer (reject / accept /
unknown without a subscription), R7 scope confirmation and `scope_mismatch`.
From the repo root R6/R7 are skipped (`app` not importable) — run from
`Dashboard/`.

## 5. Manual check (two hosts or one host, mTLS from `pki/`)

    # provider, own room (fresh EPR per trial)
    .venv\Scripts\python testbed\providers\testbed_provider.py --mdib testbed/mdib/ecg_mdib_v2.xml ^
        --epr urn:uuid:<uuid> --manufacturer "SDC Testbed" --model "ECG Monitor T1" --friendly-name ecg1 ^
        --patient-id P001 --room ICU-1 --bed 1 --certs-dir pki/ecg --ip <IP> --port 10010
    # foreign room: --room ICU-2 ; no location: omit --room ; mismatch: --scope-room ICU-1 --room ICU-2
    # dashboard, filtered
    .venv\Scripts\python Dashboard\main.py --room ICU-1 --event-log experiments/ziel1/logs/filtered.jsonl
    # dashboard, baseline
    .venv\Scripts\python Dashboard\main.py --event-log experiments/ziel1/logs/baseline.jsonl

Expected: foreign → `reject@discovery` + `subscription_avoided`, provider
`status` shows `subscriptions=0`; mismatch → `scope_mismatch` +
`reject@context_precheck`, `subscriptions=0`; own → `scope_confirmed` +
`subscription_created`; no location → `unknown` + `subscription_created`.
Baseline: every provider `subscription_created{phase=no_filter}`.

**TRA definition.** Bettfremde trials = roles *foreign room* + *mismatch*.
TRA = correctly rejected bettfremde trials / all bettfremde trials. Providers
without location (decision `unknown`) are excluded from the denominator and
reported separately (docs/model/SPEC_DELTA.md SD-12, I9); false rejects of own-room
providers are reported separately as well. Sample size for TRA ≥ 99 % at
95 %: 300 bettfremde trials with 0 misses (rule of three), split 150 foreign
room + 150 mismatch.

**Network traffic (FF1).** Capture on the dashboard host with tshark or
Wireshark during the filtered and the baseline run, same providers and
duration: `udp.port == 3702` for the discovery overhead and
`tcp.port in {10010 10011 10012 10013}` (the provider ports) for the
connection traffic; compare bytes per provider port (Statistics →
Conversations) filtered vs. baseline. Expected: a foreign provider has zero
TCP bytes in the filtered run; a mismatch provider gets ONE connection for
the context check (TLS handshake, GetMetadata, GetContextStates) but no
subscription and no notification traffic; own and no-location providers
carry the subscription traffic in both runs.

Earlier live journals (2026-10-01) are in `Dashboard/logs/ziel1/`.

## 6. Runner and analyzer (experiments/ziel1/)

`ziel1_experiment.py` runs the real dashboard objects headless (no window) and
starts one testbed provider per trial with a fresh EPR; roles map to the PKI
folders own=ecg, foreign=spo2, nolocation=abp, mismatch=vent. Each trial adds
`trial_start` (role = ground truth) and `trial_end` (provider `status`,
`subscriptions=N`) to the same journal. `ziel1_analyze.py` computes TRA with
Clopper-Pearson bounds, false rejects, the `unknown` count, avoided vs.
created subscriptions and the provider-side cross-check.

    # plan only (no network)
    .venv\Scripts\python experiments\ziel1\ziel1_experiment.py --dry-run --trials foreign=2,mismatch=2
    # pilot, filtered (N = 20 bettfremde)
    .venv\Scripts\python experiments\ziel1\ziel1_experiment.py --ip <IP> --mode filtered --room ICU-1 ^
        --trials foreign=10,mismatch=10,own=2,nolocation=2
    # pilot, baseline (same providers, no filter)
    .venv\Scripts\python experiments\ziel1\ziel1_experiment.py --ip <IP> --mode baseline ^
        --trials foreign=10,mismatch=10,own=2,nolocation=2
    # analysis
    .venv\Scripts\python experiments\ziel1\ziel1_analyze.py experiments\ziel1\logs\ziel1_filtered_<ts>.jsonl ^
        --baseline experiments\ziel1\logs\ziel1_baseline_<ts>.jsonl --per-trial --json experiments\ziel1\logs\pilot.json
    # full run for TRA >= 99 % (0 misses needed)
    .venv\Scripts\python experiments\ziel1\ziel1_experiment.py --ip <IP> --mode filtered --room ICU-1 ^
        --trials foreign=150,mismatch=150,own=5,nolocation=5

One command for everything (filtered 150+150+5+5, then baseline 20+20+2+2,
then the analysis; packet capture only if tshark is installed):

    .venv\Scripts\python experiments\ziel1\ziel1_run_all.py --ip <IP>

writes `ziel1_filtered_<label>.jsonl`, `ziel1_baseline_<label>.jsonl`, the
console outputs, `ziel1_report_<label>.txt/.json` into `experiments/ziel1/logs/`.

Options: `--order grouped` (roles in blocks instead of round-robin),
`--port-base` (one port per trial, default 10100 upwards), `--trial-timeout`
(default 60 s), `--tls no_tls` (plain HTTP on both sides), `--echo-provider`.
Journals and the `.manifest.json` land in `experiments/ziel1/logs/`.
