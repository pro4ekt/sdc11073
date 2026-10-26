# Ziel 2 — Verteilte normative Alarmsteuerung und funktionale Sicherheit

Kapitel1.md §1.4: remote acknowledgement (Alert User Acknowledgement) over
standard SDC services (IEC 60601-1-8, IHE SDPi-A, BICEPS), continuous
connection monitoring and a deterministic transition to a safe fallback state
on communication loss. Metrics: ack success rate ≥ 99 %, latency as p95 of the
round-trip time, fallback entry within a defined upper bound (connection
timeout + fallback interval) shown by fault injection.

## 1. Implementation map (Dashboard/)

### Acknowledgement (Fix 16)

| File : function | What it does |
|---|---|
| QML `DevicePage.qml` / `MetricPage.qml` (bell `MouseArea`) | Bell press → `currentDevice.silenceAlarm()`. The local silence tint follows the RESULT (`onAckStateChanged`), not the press. A text under the bell shows the state of the operator response (`ackStateText`). |
| `app/qtDeviceHandler.py : _select_ack_targets` | Candidates = AlertSignalStates with Presence On or Latch whose `ConditionSignaled` condition is not artifact-suppressed and (for On) still present; each needs a `SetAlertStateOperationDescriptor` with exactly that `OperationTarget`. No fallback to the first signal / first operation. |
| `app/qtDeviceHandler.py : silenceAlarm` | One `press_id` per press; one SetAlertState transaction per signal, sequential, in a worker thread (`_run_ack_press`); result via the queued signal `_ackResult` → `ackState` / `ackMessage` / `ackPressId` properties (idle, pending, accepted, confirmed, failed, timeout, error, no_active_alarm, not_remote_ackable, unavailable). |
| `app/qtDeviceHandler.py : on_ack_confirmed` | Provider reported Presence=Ack → `accepted`/`timeout` becomes `confirmed`. |
| `device/alarm_manager.py : AlarmManager._set_alert_presence` | The transaction: proposed AlertSignalState (Presence Ack or On) → `set_alert_state` → `future.result(timeout=ACK_WAIT_SEC = 5 s)` → InvocationState evaluated: Fin/FinMod = accepted, Fail/Cnclld/CnclldMan = failed, no final OperationInvokedReport = timeout, exception = error, no SetService = unavailable (nothing sent). Writes `ack_rtt`. |
| `device/alarm_manager.py : acknowledge_alarm / acknowledge_alarm_detailed` | Presence=Ack; returns the outcome (and state + message for the UI). |
| `device/alarm_manager.py : reactivate_alarm` | Ack-timeout re-raise (Presence=On after 30 s), same evaluation (`purpose = reraise`). |
| `device/alarm_manager.py : find_operation_handle` | Exact `OperationTarget` match only. |
| `device/handler.py : _log_signal_transition` (ACK branch) | Provider's EpisodicAlertReport shows Presence=Ack → `ack_confirmed` event + `qtDeviceHandler.on_ack_confirmed`; starts the 30 s ack-timeout countdown. |

### Connection monitoring and fallback (Fix 18 constants, Fix 17 technical alarm)

| File : function | What it does |
|---|---|
| `Dashboard/device/handler.py` class attributes | `SLEEP_INTERVAL = 5 s`, `T_FALLBACK = 15 s`, `MAX_MISSED = 3`, `METRIC_SILENCE_SEC = 120 s` (values unchanged; class attributes so tests can shrink them). Request timeout τ_sock = 5 s = sdc11073 `SdcConsumer(socket_timeout=5)` default. |
| `device/handler.py : _monitoring_loop` | Per iteration: `consumer.is_connected` check → `_ping` → ack-timeout processing → sleep. Three detectors: `is_connected` (subscription ended / renew failed), `t_fallback` (MAX_MISSED failed pings), `metric_silence` (no EpisodicMetricReport for 120 s). On exit `error_occurred = True`, `fallback_cause` set, `fallback` event. |
| `device/handler.py : _ping` | GetContextStates in a thread; exception → miss, HTTP 4xx → no miss, success → `mark_rx`. |
| `device/handler.py : _on_subscription_end` | WS-Eventing SubscriptionEnd from the provider: status/reason kept for the `fallback` event. |
| `device/handler.py : _worker_logic` (finally) | On an error exit of a device shown to staff: `manager.notify_connection_lost(epr, handler, cause)` FIRST (technical alarm raised at the watchdog decision), THEN the graceful-unsubscribe attempt (`_graceful_shutdown`, ≤ 5 s). |
| `device/handler.py : run` (finally) | → `manager.remove_device(epr, error_occurred, location_filtered)` after the unsubscribe. |
| `app/sdcMyConsumer.py : notify_connection_lost` | Reads patient/room and the latch (`_lost_context`: `reverse_lookup_patient_room`, `aggregator.is_ensemble_escalated`) while the aggregator still holds the ensemble, then `_raise_technical_alarm`. Idempotent; ignores devices never shown or not managed. |
| `app/sdcMyConsumer.py : remove_device` | Aggregator path unchanged (release → discard → on_member_departed, Fix 9–13). If the handler did not pass through `notify_connection_lost`, reads the latch BEFORE `topology.release_device` and raises the alarm itself AFTER the aggregator path (`already_raised` guard, no duplicate). Graceful exits and room switches emit `deviceDisconnected` as before. |
| `app/sdcMyConsumer.py : _raise_technical_alarm` | Keeps the QtDeviceHandler in `_lost_devices`, `qt.markConnectionLost(cause, t_abs, escalated_at_loss)`, `overview.markLost(...)`, `technical_alarm{raised}` event, signal `deviceConnectionLost(epr, cause)`. |
| `app/sdcMyConsumer.py : on_device_connected` | Reconnect of the EPR: `technical_alarm{cleared}`, `deviceReconnected`, then `deviceConnected`. |
| `app/sdcMyConsumer.py : dismissConnectionLoss` (Slot) | Staff dismissal: `technical_alarm{dismissed}`, `deviceDisconnected` (device leaves the list). |
| `app/qtDeviceHandler.py : markConnectionLost` | Card → `alarmStatus = COMM_FAILURE`, `connectionLost`, `lostCause`, `lostSince`, `lostSinceText`, `escalatedAtLoss`; `update_data` no longer reads the dead worker's MDIB. |
| `app/patientOverviewModel.py : markLost / clearLost` | Partial loss: live card gets `lostDevices` / `lostEprs` / `escalatedAtLoss`; last member lost: synthetic card `connectionLost = true` keyed by patient + room ("Verbindung verloren", A1 "während Eskalation"), survives aggregator updates, leaves only via `clearLost`. |
| QML `MainPage.qml` | Lost device stays listed: dark-red COMM_FAILURE card, "⚠ VERBINDUNG VERLOREN seit hh:mm:ss (während Eskalation)", cause, "Quittieren ✕" → `sdcManager.dismissConnectionLoss`. Reconnect replaces the entry. |
| QML `PatientOverview.qml` | "⚠ VERBINDUNG VERLOREN [WÄHREND ESKALATION] — seit …" / "n Gerät(e) ohne Verbindung", dismiss button for all lost EPRs of the card. |
| `app/metrics/latency_metrics.py : LatencyMetrics` | Recorder for all Ziel 2 events and `latency_summary`. |

The technical-alarm state lives only in `SdcMyConsumer`, `QtDeviceHandler`
and `PatientOverviewModel`; it never enters the aggregator, `M`, `E(t)`,
`SDC_score` or the latch (tests L1a/L1b assert the unchanged aggregator calls).

## 2. Derived upper bound for fallback entry

Constants: `SLEEP_INTERVAL` = 5 s, `T_FALLBACK` = 15 s, `MAX_MISSED` = 3,
τ_sock = 5 s (sdc11073 socket timeout), unsubscribe timeout 5 s.

    Δt_fallback ≤ MAX_MISSED · (SLEEP_INTERVAL + τ_sock) = T_FALLBACK + MAX_MISSED · τ_sock = 15 + 3·5 = 30 s

measured from the loss to the `fallback` event (`t_abs`). The technical
alarm (`technical_alarm{raised}`) is raised by the handler right after the
watchdog decision and BEFORE its graceful-unsubscribe attempt
(`DeviceHandler._worker_logic` → `SdcMyConsumer.notify_connection_lost`), so
the same bound holds for the visible safe state; only `device_departed` and
the aggregator release follow after the unsubscribe (≤ 5 s later).

**What to measure.** Main value: t_loss → `technical_alarm{raised}` — the
safe state visible to staff. Intermediate value: t_loss → `fallback` — the
watchdog decision (a few ms earlier). Both from the journal (`t_abs`), t_loss
stamped by the runner on the same clock.

| Loss type | Detector | Bound loss → `fallback` = loss → `technical_alarm{raised}` |
|---|---|---|
| graceful shutdown (provider `shutdown`: SubscriptionEnd + Bye) | `is_connected` | ≤ SLEEP_INTERVAL + one ping ≈ 10 s |
| killed process (TCP RST on the next ping) | `t_fallback` | ≈ MAX_MISSED · SLEEP_INTERVAL = 15 s |
| silent packet loss (firewall drop, cable) | `t_fallback` | ≤ T_FALLBACK + MAX_MISSED · τ_sock = 30 s |
| subscription silently dead, TCP alive | `metric_silence` | ≤ 120 s + 5 s |

The sdc11073 Renew is NOT a timely detector (sent at 50 % of the granted
expiry, request default 3600 s).

## 3. Event-log events

| event | fields |
|---|---|
| `ack_rtt` | epr, handle, operation_handle, press_id, purpose (ack / reraise), rtt_ms, ok (Fin/FinMod), outcome (accepted / failed / timeout / error), invocation_state, transaction_id, t_request_abs |
| `ack_confirmed` | epr, handle (provider reported Presence=Ack) |
| `fallback` | epr, cause (t_fallback / is_connected / metric_silence), since_rx_s, end_status, end_reason |
| `technical_alarm` | epr, action (raised / cleared / dismissed), cause, patient_id, room, ensemble, escalated_at_loss, lost_since_abs |
| `device_departed` | epr, cause = error (Ziel 1 recorder) |
| `latency_summary` | ack_ok, ack_fail, ack_by_outcome, ack_confirmed, rtt_ms_p95, rtt_ms_max, fallbacks, technical_alarms |

Analysis rules (deliberate choices): the success rate is counted per
transaction, i.e. per `ack_rtt` with purpose `ack`; several transactions of
one bell press are grouped by `press_id` (a press is reported as accepted
only if all its transactions were). `timeout` counts as a failure; a later
`ack_confirmed` for the same (epr, handle) is reported separately as "late
success", never as a retroactive success. p95 of `rtt_ms` is computed over
successful transactions only (nearest rank), because a failed or timed-out
transaction has no meaningful round trip. Fallback timing: main value
`technical_alarm{raised}.t_abs` − t_loss, intermediate value
`fallback.t_abs` − t_loss (t_loss stamped by the runner on the same clock).

## 4. Testbed provider options

| Purpose | Option / command |
|---|---|
| accept acknowledgements (Fin) | `--ack accept` or stdin `ack accept` |
| refuse acknowledgements (Fail) | `--ack refuse` or stdin `ack refuse` |
| raise / clear an alarm | stdin `alarm <channel> on` / `off` (channels printed at start) |
| graceful loss | stdin `shutdown` (SubscriptionEnd + WS-Discovery Bye) |
| killed process | `taskkill /F /PID <pid>` on the provider host |
| silent loss | on the dashboard host, block the provider in BOTH directions by IP (an outgoing-only rule lets the provider's reports still arrive): `netsh advfirewall firewall add rule name=z2block_in dir=in action=block remoteip=<provider IP>` and `netsh advfirewall firewall add rule name=z2block_out dir=out action=block remoteip=<provider IP>`; afterwards `netsh advfirewall firewall delete rule name=z2block_in` and `netsh advfirewall firewall delete rule name=z2block_out`. Does NOT work when provider and dashboard run on the same host (loopback traffic bypasses the firewall) — use a second host or a VM for this loss type. |
| application liveness only | stdin `selfcheck off` (LastSelfCheck freezes → COMM_FAILURE via SelfCheckPeriod while connected) |
| provider-side subscription count | stdin `status` |

## 5. Unit tests

Working directory `Dashboard/`:

    ..\.venv\Scripts\python -m unittest tests.test_ack_watchdog tests.test_connection_lost tests.test_watchdog -v

- `test_ack_watchdog.py` K1 InvocationState handling (Fin, FinMod, Fail,
  Cnclld, CnclldMan, timeout, error, unavailable, journal fields), K2 re-raise
  and `find_operation_handle` without fallback, K3 `ack_confirmed`, K4
  `silenceAlarm` selection / press_id / ackState.
- `test_connection_lost.py` L1a–c error → technical alarm (latch read before
  release, aggregator calls unchanged, event fields; partial loss; loss
  before the UI → no alarm), L1d production order `notify_connection_lost`
  then `remove_device` (one alarm, no duplicate, aggregator order unchanged),
  L1e hidden / unmanaged devices ignored, L2 graceful / location departures
  silent, L3 reconnect clears / dismissal removes, L4 `markConnectionLost`,
  L5 overview cards.
- `test_watchdog.py` W1 `_ping`, W2 `_monitoring_loop` causes, W3 constants
  and the 30 s bound, W3b the alarm is raised before `_graceful_shutdown`
  (source order), W4 SubscriptionEnd in the `fallback` event.

## 6. Manual checks (testbed, mTLS from `pki/`)

    .venv\Scripts\python testbed\providers\testbed_provider.py --mdib testbed/mdib/ecg_mdib_v2.xml ^
        --epr urn:uuid:<uuid> --manufacturer "SDC Testbed" --model "ECG Monitor T1" --friendly-name ecg1 ^
        --patient-id P001 --room ICU-1 --bed 1 --certs-dir pki/ecg --ip <IP> --port 10010 --ack accept
    .venv\Scripts\python Dashboard\main.py --room ICU-1 --event-log experiments/ziel2/logs/manual.jsonl

1. `alarm tachycardia on`, bell → provider prints `SetAlertState … ACCEPT (Fin)`,
   dashboard log `[Ack] 'sig…' InvocationState=Fin`, bell text "Quittierung
   angenommen (Fin)" then "bestätigt (Presence=Ack)"; journal `ack_rtt{ok:true}`
   then `ack_confirmed`.
2. `ack refuse`, bell → provider prints `REFUSE (Fail)`, dashboard
   `[Ack] … REFUSED: InvocationState=Fail`, bell text "Quittierung ABGELEHNT
   (Fail)", `ack_rtt{ok:false, outcome:failed, invocation_state:Fail}`; no
   silence tint.
3. Kill the provider (`taskkill /F /PID`) → within ≈15 s `fallback{cause:
   t_fallback}`, the device card turns dark red "VERBINDUNG VERLOREN seit …",
   the patient card shows the loss (bold "WÄHREND ESKALATION" if it was Red),
   `technical_alarm{raised}`. Nothing disappears.
4. `shutdown` instead → `fallback{cause: is_connected, end_status:
   …SourceShuttingDown}` within ≈5–10 s, same technical alarm.
5. Restart the provider with the SAME `--epr` → after the 15 s cooldown and
   rediscovery: `technical_alarm{cleared}`, card back to normal, lost marker
   gone from the patient card. Alternatively press "Quittieren ✕" →
   `technical_alarm{dismissed}`, device leaves the list.

`live_departure.py` (moved here from `tools/`) drives phases 3–6 of this list
headlessly with two providers: `.venv\Scripts\python experiments\ziel2\live_departure.py <IP>`.

## 7. Runners and analyzer (experiments/ziel2/, shared code in experiments/harness.py)

One command for everything (ack 300 accept + 20 refuse, then fault injection
shutdown=30, kill=30, suspend=30, reconnect=5, then the analysis; ≈ 40 min):

    .venv\Scripts\python experiments\ziel2\ziel2_run_all.py --ip <IP>

- `ziel2_ack.py` — headless dashboard + one ECG provider; per trial `alarm
  tachycardia on` → `QtDeviceHandler.silenceAlarm()` (the real bell path, run
  synchronously) → wait for the provider's Presence=Ack (`ack_confirmed`) →
  `alarm off`. Block 1 with provider policy `ack accept`, block 2 with `ack
  refuse`. Adds `ack_trial_start` / `ack_trial_end` (press_id, ackState,
  outcomes, confirm latency) to the journal.
- `ziel2_fault.py` — one fresh provider per trial, loss injected when the device
  is shown: `shutdown` (SubscriptionEnd + Bye), `kill` (process tree
  terminated → TCP RST), `suspend` (process tree frozen → silent loss on one
  host: TCP stays open, nothing answers), `reconnect` (kill, then the same EPR
  comes back → `technical_alarm{cleared}`). Writes `fault_injected` (t_loss)
  and `fault_trial_end` (Δt to `fallback`, `technical_alarm{raised}`,
  `device_departed`, bound per method: 10 / 15 / 30 s).
- `ziel2_analyze.py` — success rate per transaction with 95 % lower bound,
  outcomes and InvocationStates, p95/median/max RTT over successful
  transactions, late successes, request→Presence=Ack latency; per loss type
  median/p95/max of both deltas, trials over the bound, 95 % lower bound for
  "within the bound".

Single runs: `ziel2_ack.py --ip <IP> --accept 20 --refuse 5`,
`ziel2_fault.py --ip <IP> --methods shutdown=3,kill=3,suspend=3,reconnect=1`,
`ziel2_analyze.py <journal(s)> --per-trial --json out.json`; `--dry-run` on the
runners prints the plan. Observed on 2026-10-16 (smoke): ack RTT ≈ 16–21 ms,
Presence=Ack report ≈ 15 ms after the request; shutdown ≈ 2 s, kill ≈ 7 s,
suspend ≈ 12 s — after the first failed ping the library's Renew fails as well,
so `is_connected` drops before the third missed ping; all within the bounds.
