"""
smartAlertAggregator.py — Alert Processor: the FAST path of Smart Alerting.
==========================================================================
After the God-Object split, this class does ONE thing: process alarm events for
already-formed ensembles and route a tri-state verdict (ESCALATE / WARN /
SUPPRESS) using the two-axis adaptive stochastic math core:

    Confidence axis  E(t)         = Σ_j w_j · s_j(t)
    Urgency axis     Θ_current(t)                          (hysteresis barrier)
    Escalation       ⇔  E(t) ≥ Θ_current(t)                (binary verdict)

All SLOW / network-bound topology work (ensemble formation, MDIB enumeration,
FHIR HTTP, SOAP binding) lives in ``EnsembleTopologyManager``.  This processor
holds a read-only reference to it and pulls thread-safe snapshots
(``get_member_specs`` / ``get_members`` / ``collect_patient_danger_codes`` /
``reverse_lookup_patient_room``) BEFORE taking its own locks.

Locks — ordering is acyclic: topology.lock (released) → self.lock →
self._adaptive_lock.  No topology lock is ever nested inside a processor lock.
"""

import logging
import threading
import time
from typing import Iterable, Optional, Tuple, Set, Dict, TYPE_CHECKING

from .alarmCoordinator import AlarmCoordinator, DeviceAlertEvidence, device_of_alert_key
from .adaptive_alarm_aggregator import AdaptiveAlarmAggregator
from .clinical_context import ClinicalContext
from .device_profile_repo import DeviceReliabilityProfile, get_repository
from .math_types import EngineConfig, SensorSpec

if TYPE_CHECKING:
    from .ensemble_topology_manager import EnsembleTopologyManager
    from app.sdcMyConsumer import SdcMyConsumer
    from app.patientOverviewModel import PatientOverviewModel


class SmartAlertAggregator:
    """Alert Processor — per-ensemble two-axis adaptive alarm routing.

    ``manager``        — the SdcMyConsumer (used only to reach ``.devices`` for
                         cross-thread UI refresh in the GC / escalation paths).
    ``topology``       — the EnsembleTopologyManager providing membership, the
                         full sensor registry and FHIR danger codes.
    ``overview_model`` — Qt/QML PatientOverviewModel bridge (status/severity
                         updates); may be None (headless / tests).
    ``periodic_eval``  — start the T_P evaluation thread (default True); tests
                         that drive ``evaluate_periodic`` by hand pass False.
    """

    def __init__(
        self,
        manager: 'SdcMyConsumer',
        topology: 'EnsembleTopologyManager',
        overview_model: Optional['PatientOverviewModel'] = None,
        periodic_eval: bool = True,
    ) -> None:
        self.logger = logging.getLogger('sdc.consumer.aggregator')
        self._manager = manager
        # EnsembleTopologyManager — ensemble membership, full sensor registry and
        # FHIR danger codes (slow path). Read-only snapshots consumed from here.
        self._topology = topology
        # Qt/QML PatientOverviewModel bridge (thread-safe queue + Signal) that
        # updates the patient cards on the dashboard. May be None (headless/tests).
        self._overview_model = overview_model

        # Mutex protecting the alarm/adaptive maps below (fast path only).
        self.lock : threading.Lock = threading.Lock()


        # Active alarm cache (TTL = ALARM_TTL_SEC).
        # Structure: ensemble_uuid -> alert_handle -> (DeviceAlertEvidence, timestamp)
        # Each fired alarm is registered here so the ensemble evidence set passed
        # to the adaptive core contains ALL currently active alarms across ALL
        # devices in the ensemble, not just the triggering alarm.  Entries older
        # than ALARM_TTL_SEC are garbage-collected by the background Watchdog GC.
        self._active_alarms: Dict[str, Dict[str, Tuple[DeviceAlertEvidence, float]]] = {}
        # How long (s) an alarm stays "active" in the cache without a refresh before
        # the Watchdog GC expires it (device went silent / alarm cleared implicitly).
        self.ALARM_TTL_SEC: float = 10.0

        # Ensemble-level escalation state.
        # When the Bayesian pipeline returns ESCALATE for any alarm in an ensemble,
        # the ensemble UUID is added here.  While present, ALL alarms in the ensemble
        # bypass individual suppression and show as ON in the UI.
        # Cleared automatically when _active_alarms[ensemble_uuid] becomes empty
        # (all alarms expired from the TTL cache → crisis resolved).
        self._escalated_ensembles: Set[str] = set()

        # ── Adaptive alarm state (per-ensemble aggregator) ────────────
        # One AdaptiveAlarmAggregator instance PER patient ensemble runs the
        # two-axis adaptive stochastic math core; escalation is declared
        # when E(t) >= Theta_current(t).  No legacy artifact gate or static
        # Bayesian fusion remains.
        #
        # Thread-safety: tick() mutates the aggregator's internal state and is NOT
        # re-entrant.  All aggregator lifecycle operations (build, tick, dt
        # bookkeeping) are serialised by _adaptive_lock, which is DISTINCT from
        # self.lock.  Lock ordering rule to avoid deadlock: never hold self.lock
        # while acquiring _adaptive_lock in a nested/cyclic way (the two are only
        # ever taken sequentially, A → B).
        self._adaptive_lock : threading.Lock = threading.Lock()
        self._ensemble_adaptive_aggregators: Dict[str, AdaptiveAlarmAggregator] = {}
        # Sensor composition each aggregator was built with (alert_key set).  Used
        # to detect when a new device's alarm requires a rebuild.
        self._ensemble_sensor_ids: Dict[str, frozenset] = {}
        # Per-ensemble SensorSpec map (alert_key → SensorSpec) — carries w_j/P_j so
        # the aggregator can be rebuilt over the UNION of all seen channels.
        self._ensemble_specs: Dict[str, Dict[str, SensorSpec]] = {}
        # Monotonic timestamp of the previous tick() per ensemble → dt_step source.
        self._last_tick_ts: Dict[str, float] = {}
        # V2 — grace-period bookkeeping: ensemble_uuid → monotonic resolution time.
        # Adaptive state is retained for ADAPTIVE_DISCARD_GRACE_SEC after an ensemble
        # resolves so a rapidly re-firing (jittering) alarm reuses the SAME aggregator
        # instead of rebuilding on every ON/OFF cycle.
        self._ensemble_resolved_ts: Dict[str, float] = {}
        # Fix 9 — last SDC_score pushed to the patient card per ensemble, so a
        # departure-driven re-push (on_member_departed) keeps the severity shown
        # until the next tick recomputes it.
        self._last_sdc_score: Dict[str, float] = {}

        # V4 — cap the per-tick persistence credit so a starved/paused notification
        # thread cannot advance the tick clock by an arbitrarily large jump.
        self.ADAPTIVE_MAX_DT_STEP: float = 20.0
        # V2 — how long an aggregator survives after its ensemble resolves, so a fast
        # OFF→ON jitter reuses the same instance instead of rebuilding.
        self.ADAPTIVE_DISCARD_GRACE_SEC: float = 10.0

        # DSP signal processor — stateless, no extra locking needed.
        self.alarm_coordinator : AlarmCoordinator = AlarmCoordinator()

        # Math-core shared config/context — built lazily from clinical_db.json on
        # first aggregator construction, then reused across all ensembles.
        self._engine_config: Optional[EngineConfig] = None
        self._clinical_context: Optional[ClinicalContext] = None

        # Background Watchdog GC — runs every ALARM_TTL_SEC/2 seconds.
        # Independently clears stale _active_alarms entries and notifies UI
        # even when no SDC alarm packets arrive (device went silent).
        self._stop_gc = threading.Event()
        self._gc_thread = threading.Thread(
            target=self._gc_loop, daemon=True, name='AggregatorGC'
        )
        self._gc_thread.start()

        # ── Periodic evaluation grid T_P (thesis 3.4.5) ───────────────────────
        # Δ_eval from clinical_db.json (filter_params.delta_eval, default 1 s,
        # validated 0 < Δ_eval ≤ T by the repository).  One pass every Δ_eval over
        # all ensembles with active alarms; see evaluate_periodic().  Disabled
        # only for deterministic tests that drive evaluate_periodic() themselves.
        self.DELTA_EVAL_SEC: float = get_repository().get_eval_interval()
        self._last_periodic_ts: Optional[float] = None
        self._eval_thread: Optional[threading.Thread] = None
        if periodic_eval:
            self._eval_thread = threading.Thread(
                target=self._eval_loop, daemon=True, name='AggregatorEval'
            )
            self._eval_thread.start()


    def check_alert_validity(
        self,
        ensemble_uuid: str,
        alert_key: str,
        metric_concept: str,
        biceps_priority: str = 'Hi',
        manufacturer: str = '',
        model: str = '',
        device_epr: str = '',
        reliability_profile: Optional[DeviceReliabilityProfile] = None,
    ) -> str:
        """
        Ensemble-level alarm gate — returns a tri-state routing decision.

        AdaptiveAlarmAggregator (per-ensemble): one aggregator instance per
        ensemble is advanced by exactly one ``tick()`` on each alarm event.  The
        core evaluates two orthogonal axes — Confidence E(t) and Urgency
        Theta_current(t) — and declares escalation when E(t) >= Theta_current(t).
        No legacy artifact gate or static Bayesian fusion remains.
        The tick verdict is mapped to routing:
            decision.escalate == True  → ``"ESCALATE"``  (Red)
            decision.escalate == False → ``"WARN"``      (Yellow)

        Fail-open paths (no metric history, unknown concept) → ``"ESCALATE"``
        so real alarms are never silently swallowed.

        Returns
        -------
        str
            ``"ESCALATE"`` — forward alarm; device + patient card Red.
            ``"WARN"``     — real alarm below the escalation boundary; card Yellow.
            ``"SUPPRESS"`` — reserved (no suppression stage currently active).
        """
        if not metric_concept:
            return 'ESCALATE'  # no metric mapping → fail-open

        now = time.monotonic()

        # Topology snapshots taken BEFORE any processor lock — keeps topology.lock
        # and self.lock un-nested (acyclic ordering → deadlock-free).
        member_specs = self._topology.get_member_specs(ensemble_uuid)
        danger_codes = self._topology.collect_patient_danger_codes(ensemble_uuid)
        # Device truth for a_j (Fix 11): AlertConditionState.Presence of every
        # member MDIB, read non-blocking BEFORE any processor lock.
        presence = self._snapshot_presence(ensemble_uuid)

        with self.lock:
            # ── Early return: ensemble already escalated ──────────────────────────
            # Once a crisis is confirmed, every subsequent alarm in the same
            # ensemble must also show as ON — do not re-run the pipeline until all
            # alarms clear.
            #
            # INVARIANT:  latched  ⟹  _active_alarms[ensemble] is non-empty
            #             ⟹  the ensemble's AdaptiveAlarmAggregator exists.
            # The latch is only ever set by a tick verdict (which builds the
            # aggregator first) and is cleared in clear_alarm() and
            # _cleanup_stale_alarms() the moment the cache empties.  The grace
            # sweep discards an aggregator ONLY for ensembles that are absent
            # from _active_alarms.  Hence "latched but no aggregator" is
            # unreachable, and clear_alarm's OFF-edge tick (Fix 6) can rely on
            # finding the aggregator for a latched ensemble.  Any future change
            # to the grace / discard logic must preserve this ordering.
            if ensemble_uuid in self._escalated_ensembles:
                self._active_alarms.setdefault(ensemble_uuid, {})
                ev = DeviceAlertEvidence(
                    alert_key=alert_key,
                    metric_concept=metric_concept,
                    manufacturer=manufacturer,
                    model=model,
                    ensemble_uuid=ensemble_uuid,
                    biceps_priority=biceps_priority,
                    reliability_profile=reliability_profile,
                )
                self._active_alarms[ensemble_uuid][alert_key] = (ev, now)
                # Latched hot path runs UNDER self.lock → counters only, no CSV I/O.
                self._record_arr_metric(ensemble_uuid, alert_key, True, 1.0,
                                        write_row=False)
                # Fix 12 — the filter keeps tracking during the latch (3.4.6):
                # the same tick as on the normal path, verdict NOT routed; the
                # telemetry line is written by AlarmCoordinator.evaluate.
                self._drive_tick_locked(ensemble_uuid, member_specs, danger_codes,
                                        now, presence, forced={alert_key: 1})
                return 'ESCALATE'

            # ── Active alarm cache (TTL) ──────────────────────────────────────
            self._active_alarms.setdefault(ensemble_uuid, {})

            triggering_evidence = DeviceAlertEvidence(
                alert_key=alert_key,
                metric_concept=metric_concept,
                manufacturer=manufacturer,
                model=model,
                ensemble_uuid=ensemble_uuid,
                biceps_priority=biceps_priority,
                reliability_profile=reliability_profile,
            )
            self._active_alarms[ensemble_uuid][alert_key] = (triggering_evidence, now)

            # TTL-GC is handled exclusively by the background _gc_loop.

            # ── Adaptive drive (T_E: alarm event) — shared with the periodic
            # evaluation T_P (evaluate_periodic); see _drive_tick_locked.
            decision = self._drive_tick_locked(
                ensemble_uuid, member_specs, danger_codes, now, presence,
                forced={alert_key: 1},          # the ON report itself is device truth
            )

        return self._route_decision(ensemble_uuid, alert_key, decision)

    # ── Device truth for a_j (Fix 11) ─────────────────────────────────────────

    def _snapshot_presence(
        self, ensemble_uuid: str,
    ) -> Tuple[Dict[str, bool], Set[str], Set[str]]:
        """Read ``AlertConditionState.Presence`` of every member device.

        Returns ``(presence, readable, busy)``: ``presence`` maps the channel
        key ``(epr, handle)`` to the current Presence; ``readable`` holds the
        EPRs whose MDIB was read; ``busy`` the EPRs whose MDIB lock was taken
        (non-blocking read failed) — their channels keep their last known value.
        Devices without ``snapshot_alert_presence`` (stubs) appear in neither
        set and fall back to the TTL-cache rule.  Takes only the topology lock
        (snapshot of the member set) and each device's ``data_lock``
        non-blocking; MUST be called before ``self.lock`` (Tabelle 3.2 order).
        """
        presence: Dict[str, bool] = {}
        readable: Set[str] = set()
        busy: Set[str] = set()
        manager_devices: dict = getattr(self._manager, 'devices', {}) or {}
        for epr in self._topology.get_members(ensemble_uuid):
            handler = manager_devices.get(epr)
            fn = getattr(handler, 'snapshot_alert_presence', None)
            if fn is None:
                continue
            try:
                snap = fn()
            except Exception:
                snap = None
            if snap is None:
                busy.add(epr)
            else:
                readable.add(epr)
                presence.update(snap)
        return presence, readable, busy

    @staticmethod
    def _activation_vector(
        sensor_ids,
        active_keys: Set[str],
        presence: Optional[Tuple[Dict[str, bool], Set[str], Set[str]]],
        forced: Optional[Dict[str, int]] = None,
    ) -> Dict[str, int]:
        """Build ``{sensor_id → a_j}`` for one tick.

        Precedence per channel: (1) ``forced`` — the report that triggered this
        evaluation (ON → 1, OFF → 0) is device truth by definition; (2) the
        Presence snapshot of a readable device; (3) a channel of a busy device
        is omitted so the aggregator holds its last known value (ZOH, thesis
        3.3), except that a cached report still counts as 1; (4) a channel of
        a device without snapshot support follows the TTL-cache rule (1 iff
        cached).  A readable device whose MDIB lacks the channel is treated
        like a busy one (hold).
        """
        present, readable, busy = presence if presence is not None else ({}, set(), set())
        forced = forced or {}
        states: Dict[str, int] = {}
        for sid in sensor_ids:
            if sid in forced:
                states[sid] = 1 if forced[sid] else 0
            elif sid in present:
                states[sid] = 1 if present[sid] else 0
            else:
                epr = device_of_alert_key(sid)
                if epr is not None and (epr in busy or epr in readable):
                    if sid in active_keys:
                        states[sid] = 1
                    # else: hold last known value (key omitted → ZOH in tick)
                else:
                    states[sid] = 1 if sid in active_keys else 0
        return states

    # ── Evaluation drive and routing (shared by T_E and T_P) ────────────────

    def _drive_tick_locked(
        self,
        ensemble_uuid: str,
        member_specs: Dict[str, SensorSpec],
        danger_codes: Set[str],
        now: float,
        presence: Optional[Tuple[Dict[str, bool], Set[str], Set[str]]] = None,
        forced: Optional[Dict[str, int]] = None,
    ):
        """Drive exactly one tick() for ``ensemble_uuid`` and return the decision.

        MUST be called with ``self.lock`` held and with a non-empty
        ``_active_alarms[ensemble_uuid]``.  Acquires ``_adaptive_lock`` (B → B')
        while still holding ``self.lock`` — the same nesting as before the
        refactoring (the adaptive lock never re-acquires ``self.lock``, so the
        order stays acyclic).  tick() is pure CPU.

        This is the single evaluation path of the model: an alarm event
        (``check_alert_validity``, T_E) and the periodic grid
        (``evaluate_periodic``, T_P) both end here, so Δt bookkeeping, the
        registry-authoritative composition and the clinical context are applied
        identically.
        """
        ensemble_evidences: list[DeviceAlertEvidence] = [
            ev for ev, _ts in self._active_alarms[ensemble_uuid].values()
        ]
        # Snapshot everything the tick needs while still under self.lock so the
        # adaptive drive below never touches self.lock-protected structures.
        active_keys: Set[str] = set(self._active_alarms[ensemble_uuid].keys())
        contributing_devices: int = len(ensemble_evidences)

        with self._adaptive_lock:
            last_ts = self._last_tick_ts.get(ensemble_uuid)
            if last_ts is None:
                dt_step = 0.0                  # first tick of this ensemble
            else:
                dt_step = now - last_ts
                if dt_step < 0.0:
                    dt_step = 0.0              # guard against clock non-monotonicity
            # V4 — cap the per-tick credit so thread starvation cannot inject an
            # arbitrarily large dt jump into the aggregator (docs/model/SPEC_DELTA.md SD-7).
            dt_step = min(dt_step, self.ADAPTIVE_MAX_DT_STEP)
            self._last_tick_ts[ensemble_uuid] = now

            aggregator = self._get_or_build_adaptive_aggregator_locked(
                ensemble_uuid, ensemble_evidences, member_specs, danger_codes
            )
            # Binary activation vector for this tick from device truth (Fix 11);
            # see _activation_vector for the precedence rules.
            sensor_states: Dict[str, int] = self._activation_vector(
                aggregator.sensor_ids, active_keys, presence, forced
            )

            return self.alarm_coordinator.evaluate(
                aggregator,
                sensor_states,
                dt_step,
                contributing_devices,
            )

    def _route_decision(self, ensemble_uuid: str, alert_key: str, decision) -> str:
        """Map a tick decision onto the tri-state routing and the patient card.

        Called WITHOUT ``self.lock`` (takes it briefly for the latch and the
        TTL-active check; topology reverse-lookup and the overview push take
        their own locks).  Identical for T_E and T_P.
        """
        # ── Route based on tri-state decision ────────────────────────────────────
        if decision.escalate:
            with self.lock:
                self._escalated_ensembles.add(ensemble_uuid)
            self._propagate_escalation_to_devices(ensemble_uuid)
            _patient_id, _room = self._topology.reverse_lookup_patient_room(ensemble_uuid)
            self._notify_overview(ensemble_uuid, _patient_id, _room,
                                  is_escalated=True, sdc_score=decision.sdc_score,
                                  has_active_alarms=True)
            self._record_arr_metric(ensemble_uuid, alert_key, True, decision.sdc_score)
            return 'ESCALATE'

        # Non-escalating decision → Local Bedside Notification (Yellow).
        # Fail-safe (Decision 1A): show Yellow whenever ANY local signal is active —
        # either the math core sees evidence (E(t) > 0 → decision.has_active_alarms)
        # OR the TTL cache still holds an active alarm (covers neutral-weight
        # channels whose w_j = 0 would leave E(t) = 0). We never silence a local
        # bedside signal.
        with self.lock:
            ttl_active = bool(self._active_alarms.get(ensemble_uuid))
        has_active = decision.has_active_alarms or ttl_active
        if has_active:
            _patient_id, _room = self._topology.reverse_lookup_patient_room(ensemble_uuid)
            self._notify_overview(ensemble_uuid, _patient_id, _room,
                                  is_escalated=False, sdc_score=decision.sdc_score,
                                  has_active_alarms=True)
        self._record_arr_metric(ensemble_uuid, alert_key, False, decision.sdc_score)
        return 'WARN'

    # ── Periodic evaluation grid T_P ──────────────────────────────────────────

    def evaluate_periodic(self, now: Optional[float] = None) -> int:
        """One pass of the periodic evaluation grid T_P (thesis 3.4.5).

        The model is evaluated at T = T_E ∪ T_P: at every alarm event
        (``check_alert_validity`` / ``clear_alarm``) and on a periodic grid with
        step ``DELTA_EVAL_SEC`` (``filter_params.delta_eval`` in
        ``clinical_db.json``, Δ_eval ≤ T).  The grid is required by the model: a
        source contributes s_j = 0 at the instant it switches on, so an ensemble
        whose sources switch on together and then stay unchanged would never be
        evaluated with a filled window — and never escalate — without it.

        Scope: every ensemble that currently holds at least one active alarm in
        the TTL cache and is not latched.  Latched ensembles are skipped exactly
        as the event path skips them (the latch holds the routed decision until
        the cache empties); ensembles without active alarms are not evaluated.
        Each candidate takes the SAME path as an alarm event: topology snapshots
        before any processor lock, then ``self.lock`` → ``_adaptive_lock`` →
        ``_drive_tick_locked`` → ``_route_decision`` (Δt from ``_last_tick_ts``,
        20 s clamp, latch set on an escalating verdict, Yellow push otherwise).

        A pass is a no-op when the clock has not advanced since the previous
        pass (``now`` ≤ last pass): the grid is a set of distinct instants, and
        this also keeps a frozen test clock from producing spurious ticks.

        Returns the number of ensembles evaluated.
        """
        now = time.monotonic() if now is None else now
        with self.lock:
            if self._last_periodic_ts is not None and now <= self._last_periodic_ts:
                return 0
            self._last_periodic_ts = now
            candidates = [eid for eid, cache in self._active_alarms.items() if cache]
        evaluated = 0
        for ensemble_uuid in candidates:
            # Topology and device snapshots BEFORE any processor lock (A never
            # nested in B; device data_locks non-blocking).
            member_specs = self._topology.get_member_specs(ensemble_uuid)
            danger_codes = self._topology.collect_patient_danger_codes(ensemble_uuid)
            presence = self._snapshot_presence(ensemble_uuid)
            present = presence[0]
            with self.lock:
                cache = self._active_alarms.get(ensemble_uuid)
                # Re-check under the lock: the cache may have emptied meanwhile
                # (clear_alarm, GC, departure).
                if not cache:
                    continue
                # Fix 11 — a cached report whose condition is still present on
                # the device is refreshed, so the TTL never expires an active
                # alarm of a reachable device.  Entries whose Presence cannot be
                # confirmed (device busy / unreadable / Presence false without
                # an OFF report) are left to the TTL as a safety net.
                for key, (ev, _ts) in list(cache.items()):
                    if present.get(key) is True:
                        cache[key] = (ev, now)
                latched = ensemble_uuid in self._escalated_ensembles
                decision = self._drive_tick_locked(
                    ensemble_uuid, member_specs, danger_codes, now, presence
                )
            if not latched:
                self._route_decision(ensemble_uuid, '<periodic>', decision)
            # Fix 12 — a latched ensemble is evaluated (telemetry written by the
            # coordinator) but not routed: the latch holds the decision (3.4.6).
            evaluated += 1
        return evaluated

    def _eval_loop(self) -> None:
        """Daemon thread: one ``evaluate_periodic`` pass every DELTA_EVAL_SEC."""
        while not self._stop_gc.wait(timeout=self.DELTA_EVAL_SEC):
            try:
                self.evaluate_periodic()
            except Exception as exc:
                self.logger.warning(f'[AggregatorEval] Unhandled exception in periodic evaluation: {exc}')

    # ── Adaptive aggregator support ───────────────────────────────────────────

    def _record_arr_metric(
        self,
        ensemble_uuid: str,
        alert_key: str,
        escalated: bool,
        sdc_score: float = 0.0,
        write_row: bool = True,
    ) -> None:
        """Ziel 3 metric (best-effort): record a raw alarm event + escalation verdict.

        Ground truth (Crisis/Noise) is provider-side (scripted simulator); it is
        left None here and correlated offline, or injected by a test harness.

        write_row=False updates only in-memory counters (no CSV I/O) — used by the
        latched hot path which calls this WHILE holding self.lock, so that disk I/O
        never happens under the aggregator mutex.
        """
        try:
            from app.metrics.arr_metrics import get_arr_metrics
            get_arr_metrics().record_verdict(
                ensemble_uuid, alert_key, escalated=escalated, sdc_score=sdc_score,
                write_row=write_row,
            )
        except Exception:
            pass

    def _get_math_core_params(self) -> Tuple[EngineConfig, ClinicalContext]:
        """Lazily build (and cache) the shared EngineConfig + ClinicalContext.

        Both are derived once from clinical_db.json via the DeviceProfileRepository
        singleton (fail-open defaults if sections are missing).  Called only under
        _adaptive_lock.
        """
        if self._engine_config is None or self._clinical_context is None:
            repo = get_repository()
            horizon_t, alpha = repo.get_filter_params()
            self._engine_config = EngineConfig(horizon_T=horizon_t, alpha=alpha)
            base_prob = repo.get_base_prob()
            self._clinical_context = ClinicalContext(
                base_prob_P0=base_prob,
                odds_ratios=repo.get_odds_ratios(),
                source=f'clinical_db.json ({repo.base_prob_source})',
            )
        # Narrow Optional → concrete for the type checker (both are set above).
        assert self._engine_config is not None and self._clinical_context is not None
        return self._engine_config, self._clinical_context

    def _build_specs_from_evidences(
        self,
        ensemble_evidences: list[DeviceAlertEvidence],
    ) -> Dict[str, SensorSpec]:
        """Translate DeviceAlertEvidence DTOs into per-channel SensorSpec records.

        w_j comes from the pre-fetched reliability_profile (TPR/FPR); P_j from
        the BICEPS priority via the repository priority_map.

        Gateway Admission Rule (docs/model/math_part1.md §2.1): a channel is admitted to
        the ensemble M only if ``0 < FPR_j < TPR_j``.  A channel without a
        reliability profile has no TPR/FPR and is therefore rejected — it would
        contribute w_j = 0 (no evidence) while still diluting w̄ and lowering
        Θ_base = k_min·w̄, which opens the door to single-source escalation.
        Rejected channels still raise the local bedside warning through the
        ``ttl_active`` branch of ``check_alert_validity`` (Yellow path intact).
        """
        repo = get_repository()
        specs: Dict[str, SensorSpec] = {}
        for ev in ensemble_evidences:
            prof = ev.reliability_profile
            if prof is None:
                self.logger.info(
                    f'[Admission] Channel {ev.alert_key!r} rejected: no reliability '
                    f'profile (TPR/FPR unknown) — excluded from M, bedside only.'
                )
                continue
            tpr = prof.true_positive_rate
            fpr = prof.false_positive_rate
            if not (0.0 < fpr < tpr):
                self.logger.info(
                    f'[Admission] Channel {ev.alert_key!r} rejected: requires '
                    f'0 < FPR < TPR, got FPR={fpr}, TPR={tpr} — excluded from M, '
                    f'bedside only.'
                )
                continue
            specs[ev.alert_key] = SensorSpec(
                sensor_id=ev.alert_key,
                tpr=tpr,
                fpr=fpr,
                priority=repo.get_priority(ev.biceps_priority),
            )
        return specs

    def _get_or_build_adaptive_aggregator_locked(
        self,
        ensemble_uuid: str,
        ensemble_evidences: list[DeviceAlertEvidence],
        member_specs: Dict[str, SensorSpec],
        danger_codes: Set[str],
    ) -> AdaptiveAlarmAggregator:
        """
        Return the AdaptiveAlarmAggregator for ``ensemble_uuid``, building it lazily.

        The FULL member registry (``member_specs``, supplied by the topology
        manager) defines |M| so SILENT (non-alarming) sensors are part of it
        (fix for |M|=1 / SDC_score=1.0).  The registry is AUTHORITATIVE: while it
        exists, channels found only in the TTL alarm cache are NOT admitted.
        Evidence-derived specs are used solely as a fallback while the registry
        has not been built yet (empty).  Otherwise a departed device whose alarm
        is still cached would re-enter M through the cache with a_j = 1 and keep
        contributing evidence for up to ALARM_TTL_SEC (docs/model/SPEC_DELTA.md SD-2).

        Membership follows the registry in BOTH directions (Fix 8): the current
        composition is compared with the required one for EQUALITY, and on any
        difference the aggregator adopts ``new_specs`` as-is — a union would let
        departed channels survive in |M| and w̄.  ``update_specs`` preserves the
        FIR history of channels that stay and drops the ones that left.

        FHIR fix: the per-patient ``Context_Log_Odds`` is recomputed from
        ``danger_codes`` and injected via ``set_clinical_context`` on every
        build/update, so each ensemble gets its OWN threshold shift instead of one
        global value.  MUST be called with self._adaptive_lock held.
        """
        # Registry entries are authoritative (they cover the whole ensemble).  The
        # evidence-derived specs are a fallback ONLY while no registry exists;
        # once it does, a cache-only channel is a stale entry (departed device
        # or a channel rejected by the admission rule) and must not enter M.
        registry = member_specs or {}
        if registry:
            new_specs: Dict[str, SensorSpec] = dict(registry)
        else:
            new_specs = self._build_specs_from_evidences(ensemble_evidences)
        required: frozenset = frozenset(new_specs.keys())
        existing: frozenset = self._ensemble_sensor_ids.get(ensemble_uuid, frozenset())
        agg = self._ensemble_adaptive_aggregators.get(ensemble_uuid)

        # V2 — the ensemble is active again: cancel any pending grace-period discard
        # so the aggregator is preserved across a brief OFF→ON jitter.
        self._ensemble_resolved_ts.pop(ensemble_uuid, None)

        # Reuse path — composition unchanged (equality, not inclusion: a shrunk
        # ensemble must NOT be treated as "already covered").
        if agg is not None and required == existing:
            return agg

        config, context = self._get_math_core_params()

        if agg is None:
            # First build for this ensemble.
            agg = AdaptiveAlarmAggregator(new_specs, config, context)
            self._ensemble_adaptive_aggregators[ensemble_uuid] = agg
        else:
            # Composition changed (channel joined OR departed) — adopt new_specs,
            # not a union: update_specs keeps FIR history of survivors and drops
            # departed channels.  Known consequence: the hysteresis cold-restarts,
            # so Θ_current steps on a device disconnect (docs/model/math_part1.md §8.1
            # declares hot-plug transitions outside the validated scope).
            agg.update_specs(new_specs)

        # ── FHIR individualisation: per-patient Context_Log_Odds ──────────────
        # context is the shared static calculator (baseline P_0 + OR table); the
        # per-patient shift depends on THIS ensemble's active danger codes.
        try:
            ctx_log_odds = context.log_odds(danger_codes)
            agg.set_clinical_context(ctx_log_odds)
        except Exception as exc:
            self.logger.debug(f'[Adaptive] set_clinical_context failed: {exc}')

        self._ensemble_specs[ensemble_uuid] = new_specs
        self._ensemble_sensor_ids[ensemble_uuid] = required
        self.logger.info(
            f'[Adaptive] Built aggregator for ensemble={ensemble_uuid[:8]} — '
            f'{len(new_specs)} sensor(s), {len(danger_codes)} danger code(s).'
        )
        return agg

    def _discard_adaptive_only(self, ensemble_uuid: str) -> None:
        """Drop ONLY the adaptive math-core state for an ensemble (grace sweep).

        MUST NOT be called while holding self.lock — it acquires _adaptive_lock,
        and the lock-ordering rule forbids nesting the two.
        """
        with self._adaptive_lock:
            self._ensemble_adaptive_aggregators.pop(ensemble_uuid, None)
            self._ensemble_sensor_ids.pop(ensemble_uuid, None)
            self._ensemble_specs.pop(ensemble_uuid, None)
            self._last_tick_ts.pop(ensemble_uuid, None)

    def _reset_adaptive_state(self, ensemble_uuid: str) -> None:
        """Flush the per-ensemble math-core buffers on full resolution (ZOH fix).

        When an ensemble fully resolves (TTL cache empty / escalation latch
        cleared) the aggregator instance is KEPT for the discard grace window (so a
        fast OFF→ON jitter reuses it without a costly rebuild).  But its stale FIR
        history must be flushed, otherwise a re-fire inside the grace window would
        let the ZOH integral back-fill the old ``True`` samples across the silent
        gap and cause a FALSE re-escalation.

        Lock discipline: acquires SmartAlertAggregator._adaptive_lock (B) to read
        the instance map, then calls ``agg.reset()`` which takes the aggregator's
        own lock (C) — the same B → C order as the tick path.  MUST be called
        WITHOUT holding self.lock (A/B), so the ordering stays acyclic.
        """
        with self._adaptive_lock:
            agg = self._ensemble_adaptive_aggregators.get(ensemble_uuid)
        if agg is not None:
            agg.reset()

    def discard_ensemble(self, ensemble_uuid: str) -> None:
        """Tear down ALL processor state for a fully-released ensemble.

        Called by SdcMyConsumer.remove_device() (via the topology manager returning
        the orphaned ensemble UUID) when the last member device disconnects.  The
        topology / FHIR state is torn down separately by the topology manager.
        Acquires _adaptive_lock then self.lock sequentially (never nested).
        """
        if not ensemble_uuid:
            return
        self._discard_adaptive_only(ensemble_uuid)
        with self.lock:
            self._active_alarms.pop(ensemble_uuid, None)
            self._escalated_ensembles.discard(ensemble_uuid)
            self._ensemble_resolved_ts.pop(ensemble_uuid, None)
            self._last_sdc_score.pop(ensemble_uuid, None)
        self.logger.debug(
            f'[Adaptive] Discarded processor state for ensemble {ensemble_uuid[:8]}...'
        )

    def on_member_departed(
        self,
        ensemble_uuid: str,
        orphaned: bool,
        departed_alert_keys: Optional[Iterable[str]] = None,
    ) -> None:
        """Propagate a device departure to the patient card and the TTL cache (Fix 9).

        Called by ``SdcMyConsumer.remove_device()`` AFTER the topology manager has
        released the device (membership / registry already shrunk, Fix 8) and —
        for a full release — after ``discard_ensemble()``.

        ``orphaned`` (last member left):
            the ensemble no longer exists anywhere in the model; the card is
            removed from ``PatientOverviewModel`` (``removeEnsemble``).  Without
            this call the card stayed on the Patient Overview for ever, with its
            last colour.

        Partial departure (``orphaned`` False):
            1. the departed device's alert keys are purged from
               ``_active_alarms[ensemble]`` (docs/model/SPEC_DELTA.md SD-2 residual): a departed
               channel is not in the registry-derived ``M`` any more, but its
               cache entry kept ``ttl_active`` True (card Yellow for up to
               ALARM_TTL_SEC) and inflated ``contributing_devices``;
            2. if the purge emptied the cache the ensemble is resolved exactly as
               in ``clear_alarm`` (latch cleared, grace stamp, FIR flush, Blue);
            3. the card is re-pushed with the CURRENT status (member count from
               the topology, latch, TTL-active flag, last SDC_score) so
               ``deviceCount`` and the colour update immediately instead of at
               the next alarm event.

        No tick is driven: the departed channel leaves the aggregator's sensor
        set on the next event through the registry-authoritative rebuild
        (``_get_or_build_adaptive_aggregator_locked``), which also resets the
        hysteresis (documented hot-plug step, docs/model/math_part1.md §8.1).

        Lock order: self.lock (A) released before ``_reset_adaptive_state``
        (B → C) and before the topology reverse-lookup — identical to
        ``clear_alarm``.
        """
        if not ensemble_uuid:
            return
        if orphaned:
            with self.lock:
                self._last_sdc_score.pop(ensemble_uuid, None)
            if self._overview_model is not None:
                try:
                    self._overview_model.removeEnsemble(ensemble_uuid)
                except Exception as exc:
                    self.logger.debug(f'[PatientOverview] removeEnsemble failed: {exc}')
            self.logger.info(
                f'[Departure] ensemble={ensemble_uuid[:8]} fully released — patient card removed.'
            )
            return

        keys = set(departed_alert_keys or ())
        resolved = False
        purged: list[str] = []
        with self.lock:
            cache = self._active_alarms.get(ensemble_uuid)
            if cache:
                purged = [k for k in keys if k in cache]
                for k in purged:
                    del cache[k]
                if not cache:
                    # Purge emptied the cache → resolve like clear_alarm's full clear.
                    del self._active_alarms[ensemble_uuid]
                    self._escalated_ensembles.discard(ensemble_uuid)
                    self._ensemble_resolved_ts[ensemble_uuid] = time.monotonic()
                    resolved = True
            is_escalated = ensemble_uuid in self._escalated_ensembles
            has_active = bool(self._active_alarms.get(ensemble_uuid))
            sdc_score = 0.0 if resolved else self._last_sdc_score.get(ensemble_uuid, 0.0)
        if purged:
            self.logger.info(
                f'[Departure] ensemble={ensemble_uuid[:8]}: purged {len(purged)} stale alarm '
                f'key(s) of the departed device from the TTL cache: {purged}'
            )
        if resolved:
            self._reset_adaptive_state(ensemble_uuid)
        patient_id, room = self._topology.reverse_lookup_patient_room(ensemble_uuid)
        self._notify_overview(ensemble_uuid, patient_id, room,
                              is_escalated=is_escalated, sdc_score=sdc_score,
                              has_active_alarms=has_active)

    def is_ensemble_escalated(self, ensemble_uuid: Optional[str]) -> bool:
        """Return True if the ensemble is currently in an escalated (crisis) state."""
        if not ensemble_uuid:
            return False
        with self.lock:
            return ensemble_uuid in self._escalated_ensembles

    def is_alarm_active(self, ensemble_uuid: str, alert_key: str) -> bool:
        """
        Return True only if alert_key is present in the TTL-active alarms cache
        for the given ensemble.

        Used by QtDeviceHandler.update_data() to cross-reference stale MDIB state:
        if the MDIB still shows Presence=On but this method returns False, the
        alarm has already been TTL-expired by the Watchdog GC and must be ignored.

        Thread safety: protected by self.lock.
        """
        with self.lock:
            return alert_key in self._active_alarms.get(ensemble_uuid, {})

    def clear_alarm(self, ensemble_uuid: Optional[str], alert_key: str) -> None:
        """
        Explicitly remove a cleared alarm from the TTL cache.

        Called by DeviceHandler.on_alert_update() when an AlertConditionState
        transitions to Presence=False (alarm OFF).

        Without this, a suppressed alarm that goes OFF would stay in
        _active_alarms for up to ALARM_TTL_SEC (10 s).  During that window, a
        new alarm on another device would wrongly include the dead alarm in the
        Bayesian ensemble fusion, artificially inflating the risk score.

        The OFF edge must also reach the math core (Fix 6).  The evidence
        accumulator applies a zero-order hold, so a channel that is merely
        removed from the TTL cache would keep integrating as ACTIVE until the
        next alarm event anywhere in the ensemble — a 10 ms spike would count
        as lasting until the next tick.  Therefore:

          * PARTIAL clear (other alarms still active): one ``tick()`` is driven
            here with the updated activation vector so the ZOH sample a_j = 0
            is recorded now.  The tick's verdict is deliberately NOT used for
            routing (an alarm going OFF is a state update, not a decision).
          * FULL clear (cache now empty): handled by ``_reset_adaptive_state``
            as before — the FIR buffers are flushed, no tick is needed.

        Side-effect: if removing this alarm empties _active_alarms for the
        ensemble, the ensemble is removed from _escalated_ensembles (crisis
        fully resolved).

        Thread safety / lock order: self.lock → self._adaptive_lock →
        AdaptiveAlarmAggregator._adaptive_lock, exactly the nesting used by
        ``check_alert_validity``.  No topology lock is taken while any of
        these is held; reverse-lookup + notify run after self.lock is released.
        """
        if not ensemble_uuid:
            return

        # Resolve + notify OUTSIDE the lock: topology.reverse_lookup_patient_room
        # and _notify_overview take locks internally — calling them inside self.lock
        # would nest/re-enter. threading.Lock() is NOT reentrant.
        crisis_resolved = False
        removed = False
        # Device truth for the surviving channels of a partial clear (Fix 11);
        # read before self.lock.
        presence = self._snapshot_presence(ensemble_uuid)

        with self.lock:
            ensemble_cache = self._active_alarms.get(ensemble_uuid)
            if ensemble_cache and alert_key in ensemble_cache:
                del ensemble_cache[alert_key]
                removed = True
                self.logger.debug(
                    f'[ActiveAlarms] Cleared (alarm OFF): ensemble={ensemble_uuid[:8]} '
                    f'alert={alert_key!r}'
                )
            # If all alarms for this ensemble are now gone → reset to Normal (Blue).
            # This covers BOTH the Escalated (Red) and Warning (Yellow) cases:
            # a Warning ensemble is NOT in _escalated_ensembles, so the old inner
            # `if ensemble_uuid in self._escalated_ensembles` guard would silently
            # skip the notify call, leaving the card stuck Yellow.
            if not self._active_alarms.get(ensemble_uuid):
                crisis_resolved = True  # always notify Blue when alarm cache is empty
                # V2 — defer adaptive teardown: stamp the resolution time instead of
                # discarding the aggregator immediately, so a fast re-fire (jitter)
                # reuses the SAME persistence timer.  _cleanup_stale_alarms() discards
                # only after the ensemble stays quiet past ADAPTIVE_DISCARD_GRACE_SEC.
                self._ensemble_resolved_ts[ensemble_uuid] = time.monotonic()
                if ensemble_uuid in self._escalated_ensembles:
                    self._escalated_ensembles.discard(ensemble_uuid)
                    self.logger.info(
                        f'[Escalation] Crisis resolved: ensemble={ensemble_uuid[:8]} '
                        f'— all alarms cleared, escalation state reset.'
                    )
                else:
                    self.logger.info(
                        f'[Alarm] Warning resolved: ensemble={ensemble_uuid[:8]} '
                        f'— all alarms cleared, returning to Normal.'
                    )
                # Inline reverse-lookup removed — membership now lives in the
                # topology manager and is fetched outside the lock (below).
            elif removed:
                # ── Partial clear: push the OFF edge into the math core ────────
                # Same lock nesting as check_alert_validity (self.lock held →
                # acquire _adaptive_lock; tick() then takes the aggregator's own
                # lock and releases it).  _adaptive_lock never re-acquires
                # self.lock, so the order stays acyclic.  tick() is pure CPU.
                active_keys: Set[str] = set(self._active_alarms[ensemble_uuid].keys())
                now = time.monotonic()
                with self._adaptive_lock:
                    aggregator = self._ensemble_adaptive_aggregators.get(ensemble_uuid)
                    if aggregator is not None:
                        last_ts = self._last_tick_ts.get(ensemble_uuid)
                        if last_ts is None:
                            dt_step = 0.0
                        else:
                            dt_step = now - last_ts
                            if dt_step < 0.0:
                                dt_step = 0.0          # clock non-monotonicity guard
                        dt_step = min(dt_step, self.ADAPTIVE_MAX_DT_STEP)
                        self._last_tick_ts[ensemble_uuid] = now

                        # Activation vector built exactly as in check_alert_validity;
                        # the cleared channel is forced to 0 — the OFF report is
                        # device truth and is the OFF edge itself.
                        sensor_states: Dict[str, int] = self._activation_vector(
                            aggregator.sensor_ids, active_keys, presence,
                            forced={alert_key: 0},
                        )
                        # The verdict is intentionally ignored: routing, the
                        # _escalated_ensembles latch and the UI are left as they
                        # are.  An alarm going OFF must never produce an
                        # escalation by itself.  Known consequence: tick() still
                        # overwrites the aggregator's internal _last_escalated,
                        # which gates the τ (Time-in-Alarm) freeze.  If this
                        # partial clear drops E below Θ, the math core stops
                        # considering itself escalated and RESUMES accumulating
                        # τ while the routing latch stays set.  That is correct —
                        # docs/model/math_part1.md has no latch at all; the latch is an
                        # interface-level behaviour — but it is not obvious.
                        result = aggregator.tick(sensor_states, dt_step)
                        self.logger.debug(
                            f'[ActiveAlarms] OFF-edge tick: ensemble={ensemble_uuid[:8]} '
                            f'alert={alert_key!r} dt={dt_step:.3f}s '
                            f'E={result.evidence:.3f} theta={result.current_theta:.3f} '
                            f'(verdict not routed)'
                        )

        # Notify PatientOverview outside the lock — reverse-lookup + notify both
        # take locks internally; calling them inside self.lock would re-enter and
        # deadlock.
        if crisis_resolved:
            # V2 — adaptive state is NOT discarded here; the grace-period sweep in
            # _cleanup_stale_alarms() tears it down once the ensemble stays quiet
            # past ADAPTIVE_DISCARD_GRACE_SEC (survives a brief OFF→ON jitter).
            # ZOH fix — flush the FIR buffers NOW so a re-fire within the grace
            # window starts cold (no stale back-fill → no false re-escalation).
            self._reset_adaptive_state(ensemble_uuid)
            _patient_id, _room = self._topology.reverse_lookup_patient_room(ensemble_uuid)
            self._notify_overview(ensemble_uuid, _patient_id, _room,
                                  is_escalated=False, sdc_score=0.0, has_active_alarms=False)

    def _propagate_escalation_to_devices(self, ensemble_uuid: str) -> None:
        """
        When the Bayesian pipeline first confirms a crisis, propagate the
        escalation state to every device in the ensemble.

        Problem solved:
          Alarms arrive sequentially (device A → B → C).  A and B are evaluated
          before the ensemble is complete and get suppressed.  C triggers ESCALATE
          but A and B still have their handles in _pipeline_suppressed, so only
          device C shows red in the UI.

        Fix:
          Clear _pipeline_suppressed on all member devices and schedule a UI
          refresh.  After the refresh, update_data() sees an empty suppressed set
          and shows all alarms as ON.

        Thread safety:
          Called outside self.lock (propagation happens after evaluate()).
          _pipeline_suppressed.clear() is GIL-safe (CPython set operation).
          scheduleUpdate() is a Qt cross-thread signal emit — always safe.
        """
        eprs: Set[str] = self._topology.get_members(ensemble_uuid)

        manager_devices: dict = getattr(self._manager, 'devices', {})
        self.logger.info(
            f'[Escalation] Propagating to {len(eprs)} device(s) in '
            f'ensemble {ensemble_uuid[:8]}... — clearing pipeline suppression.'
        )
        for epr in eprs:
            handler = manager_devices.get(epr)
            if handler is None:
                continue
            # Clear BOTH suppression sets under the handler's _suppression_lock so that
            # update_data() in the Qt thread never sees a torn state during escalation.
            supp_lock = getattr(handler, '_suppression_lock', None)
            artifact_sup: Optional[set] = getattr(handler, '_artifact_suppressed', None)
            warning_h: Optional[set] = getattr(handler, '_warning_handles', None)
            if supp_lock is not None:
                with supp_lock:
                    if artifact_sup is not None:
                        artifact_sup.clear()
                    if warning_h is not None:
                        warning_h.clear()
            else:
                if artifact_sup is not None:
                    artifact_sup.clear()
                if warning_h is not None:
                    warning_h.clear()
            self.logger.debug(
                f'[Escalation]   Cleared suppression sets on device {epr[-12:]}'
            )
            qt_h = getattr(handler, 'qtDeviceHandler', None)
            if qt_h is not None:
                try:
                    qt_h.scheduleUpdate()
                except Exception as exc:
                    self.logger.debug(
                        f'[Escalation]   scheduleUpdate failed for {epr[-12:]}: {exc}'
                    )

    # ── PatientOverview bridge ────────────────────────────────────────────────

    def _notify_overview(
        self,
        ensemble_uuid: str,
        patient_id: str,
        room: str,
        is_escalated: bool,
        sdc_score: float,
        has_active_alarms: bool = False,
    ) -> None:
        """
        Push an ensemble summary to PatientOverviewModel (thread-safe via its
        internal queue + Signal bridge).  No-op when _overview_model is None.

        The continuous UI intensity indicator is driven by ``sdc_score`` ∈ [0, 1]
        (SDC_score(t) — the model's normalised ensemble severity index), NOT by a
        logistic risk projection.  The tri-state colour is orthogonal to it:

          is_escalated=True                     → Red    (E(t) ≥ Θ_current(t))
          is_escalated=False, has_active_alarms  → Yellow (local bedside signal)
          both False                            → Blue   (no active alarms)
        """
        with self.lock:
            self._last_sdc_score[ensemble_uuid] = float(sdc_score)
        if self._overview_model is None:
            return
        device_count = self._topology.get_member_count(ensemble_uuid)
        try:
            self._overview_model.updateEnsemble(
                ensemble_uuid,
                patient_id,
                room,
                device_count,
                is_escalated,
                sdc_score,
                has_active_alarms,
            )
        except Exception as exc:
            self.logger.debug(f'[PatientOverview] updateEnsemble failed: {exc}')

    # ── Background Watchdog GC ────────────────────────────────────────────────

    def _gc_loop(self) -> None:
        """
        Daemon thread: wakes every ALARM_TTL_SEC/2 seconds and calls
        _cleanup_stale_alarms(). Runs until _stop_gc is set (on shutdown).
        """
        interval = self.ALARM_TTL_SEC / 2.0
        while not self._stop_gc.wait(timeout=interval):
            try:
                self._cleanup_stale_alarms()
            except Exception as exc:
                self.logger.warning(f'[AggregatorGC] Unhandled exception in GC loop: {exc}')

    def _cleanup_stale_alarms(self) -> None:
        """
        Scans ALL ensembles in _active_alarms and removes entries whose
        timestamp is older than ALARM_TTL_SEC.

        If removing stale entries leaves an ensemble empty:
          - deletes the key from _active_alarms
          - removes the UUID from _escalated_ensembles
          - calls _notify_overview(..., False, 0.0) to reset the UI card

        Thread safety: protected by self.lock throughout.
        Called from both the GC daemon thread and (legacy) check_alert_validity.
        """
        now = time.monotonic()
        resolved_ensembles: list[str] = []
        # Ensembles where ≥1 alarm was TTL-removed (possibly still non-empty).
        # Used to trigger scheduleUpdate() on member devices so update_data()
        # re-evaluates against the now-smaller _active_alarms cache.
        stale_ensembles: list[str] = []
        # V2 — ensembles whose grace period has elapsed → adaptive state may be torn
        # down now (discarded outside self.lock, honouring the A → B lock order).
        grace_expired: list[str] = []

        with self.lock:
            for ensemble_uuid, alarm_cache in list(self._active_alarms.items()):
                stale_keys = [
                    k for k, (_, ts) in alarm_cache.items()
                    if now - ts > self.ALARM_TTL_SEC
                ]
                for k in stale_keys:
                    del alarm_cache[k]
                    self.logger.debug(
                        f'[AggregatorGC] TTL expired: ensemble={ensemble_uuid[:8]} '
                        f'alert={k!r} — removed.'
                    )

                if stale_keys:
                    stale_ensembles.append(ensemble_uuid)

                if not alarm_cache:
                    del self._active_alarms[ensemble_uuid]
                    if ensemble_uuid in self._escalated_ensembles:
                        self._escalated_ensembles.discard(ensemble_uuid)
                        self.logger.info(
                            f'[AggregatorGC] Crisis resolved: ensemble={ensemble_uuid[:8]} '
                            f'— all alarms TTL-expired, escalation cleared.'
                        )
                    else:
                        self.logger.info(
                            f'[AggregatorGC] Warning resolved: ensemble={ensemble_uuid[:8]} '
                            f'— all alarms TTL-expired, returning to Normal.'
                        )
                    # V2 — defer adaptive teardown via a grace stamp (see clear_alarm).
                    # A re-fire within the grace window reuses the persistence timer.
                    self._ensemble_resolved_ts.setdefault(ensemble_uuid, now)
                    # Always notify Blue, regardless of prior state (Red or Yellow).
                    resolved_ensembles.append(ensemble_uuid)

            # V2 — grace sweep: discard adaptive state for ensembles that have stayed
            # quiet (no active alarms) past ADAPTIVE_DISCARD_GRACE_SEC.
            for eid, ts in list(self._ensemble_resolved_ts.items()):
                if now - ts > self.ADAPTIVE_DISCARD_GRACE_SEC and eid not in self._active_alarms:
                    grace_expired.append(eid)
                    del self._ensemble_resolved_ts[eid]

        # Notify PatientOverview for fully-resolved ensembles (outside self.lock).
        for ensemble_uuid in resolved_ensembles:
            # ZOH fix — flush FIR buffers on resolution so a re-fire within the
            # grace window cannot back-fill stale samples (false re-escalation).
            self._reset_adaptive_state(ensemble_uuid)
            patient_id, room = self._topology.reverse_lookup_patient_room(ensemble_uuid)
            self._notify_overview(ensemble_uuid, patient_id, room,
                                  is_escalated=False, sdc_score=0.0, has_active_alarms=False)

        # V2 — tear down adaptive aggregator state for grace-expired ensembles.
        # Done outside self.lock (acquires _adaptive_lock — A → B ordering).
        for eid in grace_expired:
            self._discard_adaptive_only(eid)

        # Force UI refresh on every device whose alarm cache changed (partially or fully).
        # update_data() will cross-check against is_alarm_active() and skip stale MDIB entries.
        manager_devices: dict = getattr(self._manager, 'devices', {})
        for ensemble_uuid in stale_ensembles:
            eprs: Set[str] = self._topology.get_members(ensemble_uuid)
            for epr in eprs:
                handler = manager_devices.get(epr)
                if handler is None:
                    continue
                qt_h = getattr(handler, 'qtDeviceHandler', None)
                if qt_h is not None:
                    try:
                        qt_h.scheduleUpdate()
                    except Exception as _exc:
                        self.logger.debug(
                            f'[AggregatorGC] scheduleUpdate failed for {epr[-12:]}: {_exc}'
                        )

    def stop(self) -> None:
        """Signal the GC and periodic-evaluation daemon threads to exit."""
        self._stop_gc.set()
