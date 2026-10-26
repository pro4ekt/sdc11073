# SPEC_DELTA.md — discrepancies the code cannot resolve

Records where the implementation and the normative model diverge in a way that
is not a code fix within the scope of `MODEL_ALIGNMENT.md`. Each entry states
the observation, its direction (safe or unsafe), the conditions under which it
manifests, and what a fix would take. The normative text is never edited;
entries here are the record.

**Normative text (since 2026-10-01).** `Dashboard/math_part1.md` now holds the
German thesis chapter 3.1–3.4 (the model); the former English specification is
superseded. Section references of the form `§n` in the entries below refer to
the former English text and map as follows: §1 → 3.4.1, §2.1 → 3.4.2,
§2.2 → 3.3, §3 → 3.4.3, §4/§5 → 3.4.4, §6 → 3.4.5, §7 → 3.4.6, §8.1 → 3.4.2
(constant ensemble, |M| ≥ 2) and 3.4.5 (evaluation instants).

**Classification (2026-10-01), model = norm, implementation = proof of concept.**

| Entry | Class | Model clause |
|---|---|---|
| SD-1 | property of the formulas, not a deviation | 3.4.5, single-source note |
| SD-2, SD-3 (and Fixes 8, 9) | outside the validated domain: 3.4.2 assumes a constant, pre-admitted ensemble; hot-plug is future work | 3.4.2 |
| SD-4 | defined behaviour without clinical context | 3.4.1 |
| SD-5 | deviation (latch released on departure, or when no cached entry's Presence could be confirmed for 10 s — TTL safety net after Fix 11) | 3.4.6 |
| SD-6 | deviation (Yellow at E = 0 for rejected channels) | 3.4.2, 3.4.6 |
| SD-7 | deviation from the integral definition of Δt_active and from Δt in the Θ_current recursion (credited step bounded by 20 s) | 3.4.5 |
| SD-8 | **withdrawn** — the clamp k_min ≥ 2 implements the minimum ensemble size of 3.4.2, it is not a deviation | 3.4.2 |
| SD-9 | **closed** by Fix 11 (a_j from device truth, cache refreshed for present conditions) | 3.3 (ZOH), 3.4.6 |
| SD-10 | deviation (ensemble state reset when the last alarm clears, while 3.4.6 keeps Yellow until E = 0) | 3.4.6 |
| SD-11 | **closed** by Fix 13 (channel key = (EPR, handle)) | 3.4.2 |
| SD-12 | topology (Ziel 1): foreign scope rejected at discovery without a connection; every other provider connects (TLS + metadata + GetContextStates) but never subscribes before the room is decided — a scope accept is confirmed over the authenticated connection (2f, `scope_mismatch`); I9 kept as category `unknown`; I4 closed by Fix 15 (event journal) | Kapitel 1 §1.4 Ziel 1; Tabelle 3.6 I2/I4/I9 |
| latched ensemble not evaluated | **closed** by Fix 12 (evaluated on T, not routed) | 3.4.6 |
| event-driven-only evaluation | **closed** by Fix 10 (periodic grid T_P, `CHANGELOG_ALIGNMENT.md`) | 3.4.5 |

---

## SD-1 — δ_min does not protect against single-source escalation at |M| = 2

**Where:** `math_part1.md` §5 (k_min), §6 (δ_min), §8.1 (single-source bound).

**Observation.** The quorum is

    k_min(t) = round_half_up(|M| − (|M| − 2) · SDC_score(t))

At |M| = 2 the factor (|M| − 2) is zero, so k_min ≡ 2 for every SDC_score.
The melted barrier is therefore

    δ_min · k_min · w̄ = 0.5 · 2 · w̄ = w̄

which is exactly the mean weight of the ensemble. Any channel with w_j ≥ w̄
escalates alone once δ(t) has reached δ_min, that is, after the grace horizon
T plus the melting time. In a homogeneous two-channel ensemble both channels
satisfy w_j = w̄, so either one escalates alone; the outcome sits on the
non-strict ≥ of §7 applied to two floats that are equal by construction.

For comparison, at |M| = 4 with one active channel (SDC ≈ 0.69 at α = 0.5)
k_min = 3 and the melted barrier is 1.5·w̄ > w̄, so the bound of §8.1
(w_max > δ_min·k_min·w̄) is not met by a single channel and the protection
holds.

**Conclusion.** The δ_min protection is effective from k_min ≥ 3 onwards.
Two-channel ensembles are structurally unprotected against single-source
escalation. This is consistent with `results_part3.md` §4.3, where the
adaptive quorum yields ΔARR = 0 for M = 2 and M = 3.

**Direction.** Conservative (escalation more likely). No code change; the
formula in §5 is normative. A cardinality gate is listed as future work in
§8.1.

**Tests.** `test_5d_single_channel_held_while_delta_above_floor` and
`test_J9_knife_edge_documented` record the boundary without asserting the
verdict on it.

---

## SD-2 — a departed device's alarm lingers in the TTL cache (closed by Fix 9)

**Where:** `smartAlertAggregator.py` `_get_or_build_adaptive_aggregator_locked`,
`check_alert_validity` and (Fix 9) `on_member_departed`;
`ensemble_topology_manager.py` `release_device`; `app/sdcMyConsumer.py`
`remove_device`; `math_part1.md` §8.1 (hot-plug transitions declared outside
the validated scope).

**Original observation (resolved in Phase 3).** The per-tick spec set used to
be built as the union of the TTL alarm cache and the topology registry. Once
Fix 8 made the registry shrink on a partial departure, a departed device
whose alarm was still in `_active_alarms` re-entered M through the
cache-derived fallback with a_j = 1 held by the cache. Reproduced in
`test_8b_stale_cache_entry_does_not_reset_repeatedly` before the rule below:
after device B left, `sensor_ids` was `{a0, a1, b0}`, and at t = 13 the
ensemble escalated on E = 3·s_a0 + 1·s_b0 = 4.0 ≥ Θ = 3.95, i.e. the barrier
was crossed with evidence from a disconnected device. Repeated
`update_specs()` resets did NOT occur (composition stabilised at
`{a0, a1, b0}`, so `required == existing` on the following events); a second
reset would have followed once b0's TTL expired.

**Rule now in force.** The registry is authoritative. While it exists,
`new_specs = dict(member_specs)`; the cache-derived specs are used only while
the registry is empty (not yet built). A cache-only channel is therefore never
admitted to M. Consequence: a channel whose device failed enumeration at bind
time (non-blocking MDIB read busy) is not admitted through its alarms either;
it stays bedside-only until the registry is rebuilt. `get_member_specs`
re-enumerates lazily only when the registry is empty.

*Accepted as is.* The direction is conservative: a channel that could not be
enumerated carries no reliability profile either, so under the Gateway
Admission Rule (Fix 5, §2.1) it would have been rejected from M anyway. The
Yellow path is untouched, so the patient keeps the local bedside indication.

**Residual (closed 2026-09-30, Fix 9).** The stale cache entry itself used to
survive until `ALARM_TTL_SEC`. It never affected E(t), |M|, w̄ or k_min (the
registry is authoritative and the activation vector is built over
`aggregator.sensor_ids`), but it (a) kept `ttl_active` true, so the patient
card stayed Yellow for up to 10 s after the device left, and (b) inflated
`contributing_devices` in the audit log. `SmartAlertAggregator.on_member_departed`
now purges `_active_alarms[ensemble]` of the departed device's alert keys at
release time; the keys come from the departed handler's own MDIB copy
(`enumerate_alert_channels`), so `release_device` keeps its signature. If the
purge empties the cache the ensemble is resolved exactly as in `clear_alarm`
(latch cleared, grace stamp, FIR flush, card Blue).

One window remains and is accepted: between the departure and the next alarm
event the aggregator instance still carries the old sensor set (it is rebuilt
lazily on the next event). A partial clear of a *surviving* channel in that
window ticks the core over the old set with the departed key already purged
(a_j = 0), so the departed channel decays instead of holding; the verdict of
that tick is not routed (Fix 6) and the composition change on the next event
resets the hysteresis anyway (§8.1). The same window applied before Fix 9 with
a_j = 1 for the departed key — Fix 9 removes that evidence too.

**Direction.** Conservative (UI only, never suppresses). Manifests only on
device disconnection, which §8.1 declares unvalidated.

**Tests.** `test_8a_departure_shrinks_M_and_keeps_survivor_history`,
`test_8b_stale_cache_entry_does_not_reset_repeatedly` (aggregator robustness
against a stale entry, unchanged); Fix 9:
`test_partial_departure_purges_departed_alarm_keys`,
`test_partial_departure_that_empties_cache_resolves_ensemble`,
`test_partial_departure_updates_overview_device_count`,
`test_full_departure_removes_overview_card`,
`test_full_departure_while_alarming_leaves_card_yellow`,
`test_restart_other_room_shows_patient_once`
(`tests/test_departure_overview.py`).

---

## SD-3 — registry keeps a departed device's channels if re-enumeration fails

**Where:** `ensemble_topology_manager.py` `release_device` →
`_refresh_ensemble_channel_specs`; `math_part1.md` §8.1.

**Observation.** On a partial departure `release_device` rebuilds the
registry by calling `_refresh_ensemble_channel_specs(ensemble_uuid)`, which
enumerates the surviving devices with a non-blocking MDIB read and publishes
the result only if it is non-empty. If enumeration fails for every survivor
at that moment (MDIB lock busy, device mid-update), nothing is published and
the registry keeps the departed device's channels until the next successful
refresh (next bind of a device to this ensemble, or a later partial
departure). During that time |M| and Σ P_j are overstated and k_min is
computed from the wrong |M|.

**Direction.** Conservative: |M| too large ⇒ k_min too large ⇒ Θ_base too
high ⇒ escalation harder, never easier. The stale channels are silent
(a_j = 0 after their TTL entries expire), so they add no evidence.

**Fix (out of scope).** Publish an empty registry on a failed partial-
departure refresh so `get_member_specs` re-enumerates lazily on the next
alarm, or retry the refresh. Both are outside `MODEL_ALIGNMENT.md` Fix 8 as
specified ("rebuild the registry by calling
`_refresh_ensemble_channel_specs`"), and the situation is a hot-plug
transition that §8.1 declares unvalidated.

**Tests.** None.

---

## SD-4 — clinical-context layer operates in neutral mode

**Where:** `clinical_context.py`, `device_profile_repo.py`,
`config/clinical_db.json`; `math_part1.md` §1.

**Statement.** The clinical-context layer implements the FHIR side of the
SDC/FHIR coupling and operates in neutral mode: `P_0 = 0.5`, `CLO = 0`, the
threshold is determined by the device ensemble alone. This is the defined
behaviour when clinical context is unavailable (§1, note), not missing
functionality. The relevance indicator `R_d(M)` of §1 is not computed from
the ensemble composition and equals 1 for every code; at `CLO = 0` it scales
nothing. Activating the layer requires clinically calibrated `OR_d` and a
mapping from diagnoses to the monitored parameters, which is outside the
scope of the prototype.

**Observability.** Every fallback path resolves to the same neutral point
(`_DEFAULT_BASE_PROB = 0.5`, aggregator default `ClinicalContext(0.5, {})`),
so a missing configuration key cannot silently shift the threshold. The mode
is logged at INFO on context construction (`P_0`, provenance `config` /
`fallback`, `ln(O_0)`, `NEUTRAL` / `SHIFTED`); a non-zero `Context_Log_Odds`
logs its composition at DEBUG. Telemetry therefore confirms, rather than
assumes, that runs were performed at `CLO = 0`.

**Direction.** None (neutral). Supersedes any earlier note treating `R_d(M)`
as a separate defect.

**Tests.** `test_L13_missing_config_key_gives_clo_zero`,
`test_L14_empty_danger_codes_at_neutral_prior_is_zero`,
`test_L15_aggregator_fallback_is_neutral`,
`test_L16_neutral_mode_is_logged_at_info`,
`test_L17_nonzero_shift_logs_composition_at_debug`,
`test_H8_none_context_uses_neutral_prior`.

---

## SD-5 — the latch is also released by cache expiry and by device departure

**Where:** `smartAlertAggregator.py` `_cleanup_stale_alarms`, `on_member_departed`;
`math_part1.md` §7 (latch).

**Observation.** §7 releases the latch when every contributing alarm
condition has cleared. The implementation releases it on three paths: the
explicit OFF of the last cached alarm (`clear_alarm`, this is §7), the TTL
expiry of every cached entry in the GC daemon (`ALARM_TTL_SEC = 10 s`,
`_cleanup_stale_alarms`), and the purge of the departed device's keys when
the last alarming member leaves (`on_member_departed`). The two extra paths
replace a clearing that can no longer be reported (device gone) or that was
never reported (no further EpisodicAlertReport for the condition).

**Direction.** Conservative for the display (a Red card cannot stay Red
without a reporting source); the consequence for a sustained alarm that is
not re-reported is SD-9.

**Tests.** `test_full_departure_while_alarming_leaves_card_yellow`,
`test_partial_departure_that_empties_cache_resolves_ensemble`
(`tests/test_departure_overview.py`); GC path without a dedicated test.

---

## SD-6 — Yellow at E = 0 for channels rejected by the admission rule

**Where:** `smartAlertAggregator.py` `check_alert_validity`
(`has_active = decision.has_active_alarms or ttl_active`); `math_part1.md`
§7 (Yellow for `0 < E(t) < Θ_current(t)`), §8.1 (rejected sources are
annunciated by the bedside device only).

**Observation.** A channel without a reliability profile (or with
`FPR ≥ TPR`) is not in M and contributes nothing to E(t). Its alarm is still
registered in the TTL cache, so the patient card is set to Yellow although
`E(t) = 0`. The model assigns the annunciation of such channels exclusively
to the bedside device.

**Direction.** Conservative (an additional local indication at the central
station, never a suppression and never an escalation).

**Tests.** `test_5e_rejected_channel_keeps_yellow_path`.

---

## SD-7 — the elapsed interval credited to one evaluation is bounded (20 s)

**Where:** `smartAlertAggregator.py` `ADAPTIVE_MAX_DT_STEP`,
`check_alert_validity`, `clear_alarm`; `adaptive_alarm_aggregator.py` `tick`;
`hysteresis_filter.py` `step`; `math_part1.md` §6 (recursion of
`Δt_active`, recursion of `Θ_current`), §8.1.

**Observation.** The recursion credits the full interval `t_n − t_{n−1}`.
The implementation credits `min(t_n − t_{n−1}, 20 s)`, and the same bounded
value enters the hysteresis exponent `e^{−ρ·Δt}`. After a gap longer than
20 s, `Δt_active` grows by less than the true time (melting slower,
conservative) and the Slow-Release relaxation catches up less, so
`Θ_current` stays below `Θ_target` longer (not conservative). The first
tick of an ensemble is credited `Δt = 0`, which matches
`Δt_active(t_0) = 0`. The bound protects against a notification thread that
resumes after a stall and would otherwise inject an arbitrarily large time
credit.

**Direction.** Mixed, bounded by one gap per event; only relevant when
alarm events are more than 20 s apart.

**Tests.** `test_6b_partial_clear_updates_last_tick_ts_with_clamp`.

---

## SD-8 — k_min is clamped to at least 2 for |M| = 1 (WITHDRAWN 2026-10-01: implements 3.4.2)

**Where:** `urgency_engine.py` `k_min` (`max(2, min(m_size, raw))`);
`math_part1.md` §5; thesis 3.5 (degenerate ensembles).

**Observation.** For `|M| = 1` the formula of §5 gives `k_min = 1` when
`SDC_score < 0.5` (the model text analyses this inversion and requires
`|M| ≥ 2`). The implementation clamps `k_min` to a hard minimum of 2, so a
single-channel ensemble can never escalate (`Θ_base = 2·w_1 > E`).

**Direction.** Conservative. The case is outside the model's domain of
definition.

**Reclassification (2026-10-01).** The thesis text 3.4.2 ("Mindestgröße des
Ensembles") states that no central escalation is produced while fewer than two
sources are admitted. The hard clamp is the implementation of that clause, not
a deviation from it. Entry kept for the record only.

**Tests.** `test_E4_size_one_hard_floor_two`.

---

## SD-9 — a sustained alarm leaves the aggregation after ALARM_TTL_SEC without a new report (CLOSED 2026-10-01, Fix 11)

**Where:** `smartAlertAggregator.py` `check_alert_validity` (cache stamp),
`_cleanup_stale_alarms`, `_discard_adaptive_only`; `device/handler.py`
`on_alert_update`, `_phase_connect` (`not_subscribed_actions=periodic_actions`);
`app/qtDeviceHandler.py` `update_data` (TTL cross-check); `math_part1.md`
§2.2 (ZOH: a source holds its state until the next report), §7.

**Observation.** The only writer of a cache entry's timestamp is
`check_alert_validity`, which runs once per `AlertConditionState` with
`Presence = true` contained in an `EpisodicAlertReport`. A provider reports a
condition only when its state changes; reports about other states
(`AlertSignalState`, `AlertSystemState` self-check) do not contain it, and
`on_alert_update` skips `AlertSystemState` anyway. Periodic reports are not
subscribed. Hence an alarm condition that stays active for more than
`ALARM_TTL_SEC = 10 s` without a further report expires in the GC: the
cache empties, the latch is cleared, the card turns Blue, the FIR buffers are
flushed and, 10 s later, the aggregator instance is discarded. The device
card hides the alarm too, because `update_data` ignores MDIB alarm states
that are no longer in the cache. The model (§2.2) holds `a_j = 1` until the
next report; the implementation drops it after 10 s.

Reproduced live on 2026-10-01 with `testbed_provider.py` (ECG, `alarm
tachycardia on`, no further reports): cache entry present at +5 s and
+10 s, GC log `Warning resolved … all alarms TTL-expired` at +14 s, card Blue
and device card `Off` from +15 s while the provider MDIB still showed
`Presence = true`; `selfcheck off` afterwards changed only the device card
(`COMM_FAILURE` after 1.5 × SelfCheckPeriod) and nothing in the aggregation.

**Direction.** Unsafe for the central station: a sustained crisis that the
device reports once is not escalated after its first 10 s and cannot be
escalated by a later corroborating channel either, because its `a_j` is
already 0 (and the ensemble has no aggregator state left). The bedside
annunciation on the device is unaffected. For the PoC this means that
sustained-crisis behaviour of the model (melting of the barrier, latch over
minutes) cannot be demonstrated unless the provider re-reports active
conditions or the consumer refreshes the cache from the MDIB.

**Status after Fix 10 (periodic grid T_P, 2026-10-01).** The periodic
evaluation now re-evaluates every ensemble with cached alarms every Δ_eval, so
a simultaneous switch-on escalates with a filled window (live: three Hi
channels, escalation at +7 s). The cache TTL is untouched: the entries of a
sustained alarm still expire 10–15 s after their last report, the latch is
then released by the GC (SD-5) and the aggregator is discarded; an escalation
can therefore be *declared* but not *held* for longer than the TTL without
re-reports (live after Fix 10: Red at +7.6 s, Blue again at +13.4 s with
`Presence = true` on all three providers; identical with `selfcheck off`).

**Closed by Fix 11.** `DeviceHandler.snapshot_alert_presence` reads
`Presence` of every `AlertConditionState` non-blocking; `_activation_vector`
builds a_j from it (triggering report > Presence > last known value for a
busy device); `evaluate_periodic` refreshes the stamp of every cached entry
whose condition is still present, so the TTL only expires entries whose
Presence could not be confirmed for 10 s (safety net). Live 2026-10-01 17:01:
escalation at +7.3 s held for the full 60 s with no further reports; `alarm
off` ×3 → Blue; identical with `selfcheck off`. Tests Q1–Q4.

**Tests.** None (the unit tests drive `check_alert_validity` explicitly and
therefore refresh the cache themselves).

---

## SD-10 — the ensemble state is reset when the last alarm condition clears

**Where:** `smartAlertAggregator.py` `clear_alarm` (full clear →
`crisis_resolved`), `_reset_adaptive_state`; `adaptive_alarm_aggregator.py`
`reset`; thesis 3.4.6 (Yellow for `0 < E(t) < Θ_current(t)`), 3.3 (window).

**Observation.** By 3.4.6 the bedside notification (Yellow) persists while
`0 < E(t)`, i.e. for up to `T` seconds after the last contributing condition
has cleared, while the occupancies decay in the sliding window; the model has
no rule that ends it earlier. The implementation resets the ensemble state
(Yellow and Red alike) at the instant the last cached alarm clears: the card
turns Blue, the latch is released, the FIR buffers, the hysteresis and
`Δt_active` are flushed (`reset`), and the next alarm starts with an empty
window. The flush exists because a re-fire inside the 10 s grace window would
otherwise back-fill the old `a_j = 1` samples across the silent gap (tests
K1/K2).

**Direction.** Conservative for the central station (no after-effect of a
cleared alarm; no escalation carried by the occupancy of an alarm that has
already cleared); it shortens the Yellow phase relative to the model by up to
`T`.

**Tests.** `test_6c_full_clear_path_unchanged`, `test_K2_reset_prevents_backfill`.

---

## SD-11 — channels are keyed by AlertCondition handle, not by (device, handle) (CLOSED 2026-10-01, Fix 13)

**Where:** `ensemble_topology_manager.py` `_enumerate_member_specs` (registry
`{alert_key → SensorSpec}`), `smartAlertAggregator.py` `_active_alarms`
(`ensemble → alert_key → …`), `device/handler.py` `enumerate_alert_channels`
(`alert_key = desc.Handle`); thesis 3.4.2 (M = set of admitted sources).

**Observation.** Two devices of one ensemble whose MDIBs use the same
`AlertConditionDescriptor` handle (identical device type and MDIB, e.g. two
monitors of the same model) collapse into ONE channel: the registry keeps one
`SensorSpec`, the cache one entry, and |M| counts one source. Observed live
on 2026-10-01 with three testbed ECG providers sharing `ecg_mdib_v2.xml`:
`members = 3`, `|M| = 1`. With distinct handles (copies of the MDIB with
prefixed handles) `|M| = 3`.

**Direction.** Unsafe for the quorum: identical devices do not corroborate
each other, |M|, w̄ and k_min are understated. BICEPS handles are unique
only within one MDIB, so the key must include the device EPR.

**Closed by Fix 13.** `alert_key_for(epr, handle)` (`alarmCoordinator.py`)
is the channel key everywhere: `enumerate_alert_channels`, `on_alert_update`
(check / clear), `snapshot_alert_presence`, and the device card's TTL
cross-check. Live 2026-10-01 17:01: three providers → `|M| = 3` with keys
`<epr>|ecg_alert_cond_tachycardia`. Tests Q7, Q8 (`test_presence_truth.py`).

---

## SD-12 — room filter: devices without a location scope are connected before the room is known; devices without any location are accepted (Ziel 1, I2/I4/I9)

**Where:** `app/sdcMyConsumer.py` `_discovery_loop` (discovery-phase filter),
`device/handler.py` `_phase_connect` / `_phase_context_precheck` /
`_subscribe_reports` (fallback), `_phase_check_location` (MDIB check);
`app/topology_filter.py` (decision vocabulary); thesis Kapitel 1 §1.4, Ziel 1
("bereits während der initialen Netzwerk-Erkennungsphase … bevor
bandbreitenintensive Daten-Abonnements aufgebaut werden").

**Status of the three items of Tabelle 3.6 after Fix 14 (2026-10-01).**

- **I2 (subscription before room check) — closed.** A provider whose
  WS-Discovery location scope (`sdc.ctxt.loc`, IEEE 11073-20701 §9.4) names a
  foreign room is rejected from the Hello/ProbeMatch alone, without TLS
  handshake, GetMetadata or any WS-Eventing subscription (`phase =
  discovery`); a rejection by scope needs no confirmation (nothing is lost
  but a foreign device). Every other provider with the filter active connects
  and reads the context states (GetContextStates) BEFORE the first
  subscription (`phase = context_precheck`): without a location scope the room
  is decided there; with a matching scope the scope is only PROVISIONAL,
  because WS-Discovery is unauthenticated (2f) — the LocationContext read over
  the mTLS connection must name the same room (`scope_confirmed`), any
  discrepancy (other room, or no LocationContext at all) is a `scope_mismatch`
  and rejected without a subscription. The TLS connection and the DPWS
  metadata exchange of the precheck are the residual deviation from the
  Ziel-1 wording ("ohne Verbindungsaufbau"); they are inherent to BICEPS,
  since an authenticated room is only readable over the Context Service.
- **I4 (no counters) — closed** by Fix 15: every discovery, decision,
  created / avoided subscription and departure is one line of the JSON Lines
  event journal (`--event-log`), with the subscription count on the provider
  side (`status: … subscriptions=N`) as the independent reference.
- **I9 (device without location accepted) — kept, now visible.** A provider
  that names no room in the scopes AND has no LocationContextState is accepted
  with `decision = unknown` (discovery → context_precheck → mdib each record
  `no_location`). The alternative (reject) would silence a correctly located
  but unconfigured monitor; the thesis treats the category separately in the
  TRA evaluation (an `unknown` is neither a hit nor a miss of the filter).

**Direction.** Conservative for the clinical station (no foreign-room data
reaches the display or the aggregation; nothing in the own room is lost).
For the bandwidth claim of Ziel 1 the discovery-phase path is exact; the
precheck path still costs one TLS handshake and two SOAP round trips
(GetMetadata, GetContextStates) per foreign device, zero subscriptions.

**Observed (live, 2026-10-01 23:57, four Python testbed providers, filter
`ICU-1`).** A (`rm=ICU-1` in scope, context ICU-1) → `accept@discovery`
(provisional) → `accept@context_precheck scope_confirmed`, 9 actions
subscribed; B (`rm=ICU-2` in scope) → `reject@discovery`,
`subscription_avoided`, provider B reports `subscriptions=0`; C (no `--room`)
→ `unknown@context_precheck`, subscribed after the precheck; D (scope claims
ICU-1 via the test option `--scope-room`, LocationContext ICU-2) →
`scope_mismatch`, `reject@context_precheck`, `subscription_avoided`, provider
D reports `subscriptions=0`. Without filter (23:32): all providers
subscribed, `phase = no_filter`. Journals in `Dashboard/logs/ziel1/`.

**Tests.** `test_discovery_filter.py` R1–R6 (scope parsing via
`SdcLocation.from_scope_string`, decision table, associated-state preference,
journal format, recorder counters, precheck on a stubbed consumer).

## SD-13 — Ziel 2 after Fix 16–18: remaining deviations from Kapitel1.md §1.4 and kapitel3.md §3.4.6 (2026-10-11)

**Where:** `device/alarm_manager.py` (`_set_alert_presence`),
`device/handler.py` (`_monitoring_loop`, `_ping`), `app/sdcMyConsumer.py`
(`remove_device`, `_raise_technical_alarm`), `app/qtDeviceHandler.py`,
`app/patientOverviewModel.py`, QML `MainPage.qml` / `PatientOverview.qml` /
`DevicePage.qml`; thesis Kapitel 1 §1.4 Ziel 2, kapitel3 §3.4.6.

**Closed by Fix 16–18.** A provider Fail is a failure (InvocationState
evaluated, Fin / FinMod = success); the acknowledgement state is shown to the
operator (IEC 60601-1-8 §6.11.2.4 a); only active signals with their own
operation are acknowledged; a communication failure is a technical alarm
that stays visible until reconnect or dismissal, with the escalation state at
the moment of loss (§3.4.6 "als eigener Zustand gemeldet"); the journal
carries EPR, operation, InvocationState, transaction id, press_id,
request-to-report confirmation and the SubscriptionEnd reason; the watchdog
constants are class attributes with the bound Δt ≤ T_FALLBACK + MAX_MISSED ·
τ_sock = 30 s.

**Still deviating / to be stated in the thesis.**

- **Upper bound wording.** Kapitel1 names "Verbindungs-Timeout +
  Rückfall-Intervall". The implemented bound is T_FALLBACK + MAX_MISSED ·
  τ_sock (15 s + 3 · 5 s = 30 s): the request timeout enters MAX_MISSED times
  because every failed ping waits for it. The thesis formula should be
  written this way; the +5 s unsubscribe attempt before the UI reaction is
  an additional term for the "visible" fallback.
- **Timeout vs. late success.** A transaction without a final
  OperationInvokedReport within ACK_WAIT_SEC (5 s) is counted as a failure
  for the ≥ 99 % metric even if the provider executes it later; the journal
  records the later `ack_confirmed` so the analysis can report late
  successes separately, never as retroactive successes.
- **FinMod.** Counted as success (the provider executed with modification);
  whether Presence really became Ack is only known from `ack_confirmed`.
- **Technical alarm without audio, fixed priority.** The dashboard has no
  audible signal; the COMM_FAILURE card ranks below On / Warning and above
  Ack in the list (`MainPage._alarmRank`), not derived from a risk analysis
  as IEC 60601-1-8 would require for a real alarm system.
- **Scope of the technical alarm.** Raised only for devices that reached
  the UI (`_ui_connected`); a provider that fails during connect, or a
  rejected / foreign-room provider, never appears. A graceful provider
  shutdown (SubscriptionEnd `SourceShuttingDown`) is also shown as a
  technical alarm (cause `is_connected`); the end status is in the journal
  so the thesis can distinguish it from a transmission failure.
- **Dismissal is not persisted.** Staff dismissal removes the card; only
  the journal (`technical_alarm{dismissed}`) keeps the record.
- **Renew is not a detector.** sdc11073 renews at 50 % of the granted
  expiry (request default 3600 s); only the ping watchdog, the
  SubscriptionEnd and the metric silence detect a loss in time.
- **Aggregator latch after a loss (SD-5 unchanged).** A latched
  escalation whose last contributing device is lost is released by the Fix 9
  purge; the technical alarm (with `escalated_at_loss = true`) is the "nicht
  stillschweigend" report of §3.4.6, but the Red state itself is not held
  by the lost device.

---

## SD-14 — a change of the admitted ensemble M is not an evaluation point (accepted limitation, not fixed for time reasons)

**Where:** `ensemble_topology_manager.py` `release_device`,
`_refresh_ensemble_channel_specs`, `evaluate_and_bind_device`;
`smartAlertAggregator.py` `on_member_departed`,
`_get_or_build_adaptive_aggregator_locked`, `evaluate_periodic`;
`kapitel3.md` §3.4.2 ("Jede Änderung von M ist zugleich ein
Auswertungszeitpunkt, damit eine durch sie abgesenkte Schwelle unverzüglich
wirksam wird"), §3.4.5 (changes of M belong to `T_E`).

**Observation.** The model places every change of M in `T_E`. The
implementation drives no tick when M changes: a departure
(`release_device` rebuilds the registry, `on_member_departed` purges the
departed keys and re-pushes the card — "No tick is driven"), and a join
(`evaluate_and_bind_device` rebuilds the registry) only change the registry.
The new composition is adopted by the next tick, where
`_get_or_build_adaptive_aggregator_locked` compares the registry with the
current sensor set and calls `update_specs`. With active alarms the next
tick is at the latest the next grid point of `T_P`, i.e. ≤ Δ_eval = 1 s
(`evaluate_periodic` runs for every ensemble with a non-empty alarm cache);
without active alarms no evaluation happens until the next alarm event, which
has no consequence because then E = 0 and nothing can escalate.

**Direction.** A threshold lowered by the change (smaller |M|, smaller
k_min) becomes effective with a delay of at most Δ_eval instead of
immediately; a threshold raised by it is likewise applied one grid point
late. Bounded by Δ_eval; invisible for the stationary figures of Kapitel 5.

**Status.** Accepted limitation, not fixed for time reasons. A tick in
`on_member_departed` / after `evaluate_and_bind_device` would close it.

**Tests.** None for the delay itself; the adoption at the next tick is
covered by 8a, 8b (`tests/test_alignment_integration.py`) and the departure
tests (`tests/test_departure_overview.py`).

---

## SD-15 — cold restart of the hysteresis and of Δt_active on any change of the channel set (accepted limitation, not fixed for time reasons)

**Where:** `adaptive_alarm_aggregator.py` `update_specs`
(`self._hysteresis.reset()`, `self.active_alarm_duration = 0.0`,
`self._last_escalated = False`); `hysteresis_filter.py` `reset`
(`_last_theta = None`, so the next `step` starts at Θ_target);
`smartAlertAggregator.py` `_get_or_build_adaptive_aggregator_locked`;
`kapitel3.md` §3.4.5 (Δt_active is reset only at E = 0; an increase of
Θ_target is followed by the Slow Release of the hysteresis).

**Observation.** `update_specs` is called whenever the required sensor set
differs from the current one — also when only the membership of the SAME
ensemble changes (a device joins or leaves). It keeps the FIR history of the
surviving channels but resets Θ_current to Θ_target (no Slow Release towards
a raised target, no Fast Attack memory) and sets Δt_active to 0 and the
stored verdict to "not escalated". The model resets Δt_active only when E
reaches 0 and lets a raised Θ_target be taken over gradually.

**Direction.** Conservative: after the change the threshold stands at
δ = 1 (no melting credit), so a sustained evidence level below the unmelted
threshold has to accumulate Δt_active again before it can escalate; a
declared escalation is unaffected because the latch (SD-5, §3.4.6) holds
the routed decision and the reset verdict only restarts the accumulation.
Same mechanism as the documented hot-plug cold restart (3.9.4, 3.9.6).

**Status.** Accepted limitation, not fixed for time reasons. Preserving the
hysteresis state and Δt_active across a membership change of the same
ensemble (reset only on E = 0) would close it.

**Tests.** The reset itself is exercised by 8a, 8b
(`tests/test_alignment_integration.py`); no test asserts the model
behaviour (continuity of Δt_active across a change of M).

---

## SD-16 — the EnsembleContext / WorkflowContext write-back does not read the InvocationState (accepted limitation, not fixed for time reasons)

**Where:** `device/context_ops.py` `apply_ensemble_context` (Phase 2:
`context_service_client.set_context_state(...)`, then
`handler.ensemble_uuid = ensemble_uuid` and `return True`),
`apply_fhir_contexts` (same pattern for the `WorkflowContextState`
DangerCodes); `ensemble_topology_manager.py` `evaluate_and_bind_device`
(rollback of the membership only when `apply_ensemble_context` returns
False); thesis 3.5.4 / 3.7.2 ("Erst wenn das Gerät den Aufruf bestätigt,
gilt es als gebunden"); BICEPS InvocationState semantics (Fin / FinMod
final success, Fail / Cnclld / CnclldMan final failure) — the same defect
that Fix 16 closed for `SetAlertState`.

**Observation.** `set_context_state` returns a Future that resolves with
the OperationResult (never with an exception) for Fail / Cnclld /
CnclldMan. Both write-backs neither wait for that Future nor read
`InvocationInfo.InvocationState`: the call is treated as successful as soon
as the SOAP request returned without a transport exception. A write that
the device refuses (InvocationState Fail, or a later cancellation) therefore
still counts as bound — `handler.ensemble_uuid` is set, the EPR stays in
the ensemble, the device takes part in the fusion, and the patient card is
updated — although its `EnsembleContextState` may not carry the ensemble
UUID (and its `WorkflowContextState` no DangerCodes). Only a transport
error (HTTP / socket exception) rolls the membership back.

**Direction.** No effect on E(t), Θ or the escalation decision: the
membership is the dashboard's own decision (3.5.4), and the write-back is
its publication in the SDC network, not its source. The deviation is one of
visibility: another SDC participant reading the device's EnsembleContext
would not see the ensemble that the dashboard uses, and the thesis sentence
"erst wenn das Gerät den Aufruf bestätigt" holds only for the receipt of
the request, not for its execution.

**Status.** Accepted limitation, not fixed for time reasons. Closing it
means waiting for the Future with a bounded timeout, treating Fin / FinMod
as bound and everything else as not bound (rollback as for a transport
error), and logging the InvocationState — the `_set_alert_presence` pattern
of Fix 16 applied to `context_ops`.

**Tests.** None; the testbed provider accepts every SetContextState
(`testbed_provider.py` `_set_context_state`, InvocationState Fin), so the
refused case has not been exercised live.
