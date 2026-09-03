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
from typing import Optional, Tuple, Set, Dict, TYPE_CHECKING

from .alarmCoordinator import AlarmCoordinator, DeviceAlertEvidence
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
    """

    def __init__(
        self,
        manager: 'SdcMyConsumer',
        topology: 'EnsembleTopologyManager',
        overview_model: Optional['PatientOverviewModel'] = None,
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

        with self.lock:
            # ── Early return: ensemble already escalated ──────────────────────────
            # Once a crisis is confirmed, every subsequent alarm in the same
            # ensemble must also show as ON — do not re-run the pipeline until all
            # alarms clear.
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

            ensemble_evidences: list[DeviceAlertEvidence] = [
                ev for ev, _ts in self._active_alarms[ensemble_uuid].values()
            ]

            # Snapshot everything the tick needs while still under self.lock so the
            # adaptive drive below never touches self.lock-protected structures.
            active_keys: Set[str] = set(self._active_alarms[ensemble_uuid].keys())
            contributing_devices: int = len(ensemble_evidences)

            # ── Adaptive drive — executed while STILL holding self.lock to close
            # the TOCTOU gap with the GC daemon.  Lock hierarchy A → B: acquiring
            # _adaptive_lock while holding self.lock is permitted; _adaptive_lock
            # never re-acquires self.lock, so the order is acyclic.  tick() is pure
            # CPU, so holding self.lock for the microseconds it takes is safe.
            with self._adaptive_lock:
                last_ts = self._last_tick_ts.get(ensemble_uuid)
                if last_ts is None:
                    dt_step = 0.0                  # first tick of this ensemble
                else:
                    dt_step = now - last_ts
                    if dt_step < 0.0:
                        dt_step = 0.0              # guard against clock non-monotonicity
                # V4 — cap the per-tick credit so thread starvation cannot inject an
                # arbitrarily large dt jump into the aggregator.
                dt_step = min(dt_step, self.ADAPTIVE_MAX_DT_STEP)
                self._last_tick_ts[ensemble_uuid] = now

                aggregator = self._get_or_build_adaptive_aggregator_locked(
                    ensemble_uuid, ensemble_evidences, member_specs, danger_codes
                )
                # Binary activation vector for this tick: 1 if the sensor's alarm is
                # currently in the TTL cache, else 0 (sensor fell silent → decays).
                sensor_states: Dict[str, int] = {
                    sid: (1 if sid in active_keys else 0)
                    for sid in aggregator.sensor_ids
                }

                decision = self.alarm_coordinator.evaluate(
                    aggregator,
                    sensor_states,
                    dt_step,
                    contributing_devices,
                )

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
            self._clinical_context = ClinicalContext(
                base_prob_P0=repo.get_base_prob(),
                odds_ratios=repo.get_odds_ratios(),
            )
        # Narrow Optional → concrete for the type checker (both are set above).
        assert self._engine_config is not None and self._clinical_context is not None
        return self._engine_config, self._clinical_context

    def _build_specs_from_evidences(
        self,
        ensemble_evidences: list[DeviceAlertEvidence],
    ) -> Dict[str, SensorSpec]:
        """Translate DeviceAlertEvidence DTOs into per-channel SensorSpec records.

        w_j comes from the pre-fetched reliability_profile (Se/FAR); a missing
        profile yields a neutral channel (Se = FAR = 0.5 → w_j = 0, fail-open).
        P_j comes from the BICEPS priority via the repository priority_map.
        """
        repo = get_repository()
        specs: Dict[str, SensorSpec] = {}
        for ev in ensemble_evidences:
            prof = ev.reliability_profile
            tpr = prof.true_positive_rate if prof is not None else 0.5
            fpr = prof.false_positive_rate if prof is not None else 0.5
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
        manager) is folded in so SILENT (non-alarming) sensors are part of |M|
        (fix for |M|=1 / SDC_score=1.0).  Evidence-derived specs act as a fallback
        for any channel not yet enumerated.  A new channel triggers ``update_specs``
        (union, preserving FIR history of known channels).

        FHIR fix: the per-patient ``Context_Log_Odds`` is recomputed from
        ``danger_codes`` and injected via ``set_clinical_context`` on every
        build/update, so each ensemble gets its OWN threshold shift instead of one
        global value.  MUST be called with self._adaptive_lock held.
        """
        new_specs = self._build_specs_from_evidences(ensemble_evidences)
        # Registry entries are authoritative (they cover the whole ensemble); the
        # evidence-derived specs fill any gap for a channel not yet enumerated.
        registry = member_specs or {}
        new_specs = {**new_specs, **registry}
        required: frozenset = frozenset(new_specs.keys())
        existing: frozenset = self._ensemble_sensor_ids.get(ensemble_uuid, frozenset())
        existing_specs = self._ensemble_specs.get(ensemble_uuid, {})
        agg = self._ensemble_adaptive_aggregators.get(ensemble_uuid)

        # V2 — the ensemble is active again: cancel any pending grace-period discard
        # so the aggregator is preserved across a brief OFF→ON jitter.
        self._ensemble_resolved_ts.pop(ensemble_uuid, None)

        # Reuse path — current aggregator already covers every active sensor.
        if agg is not None and required <= existing:
            return agg

        config, context = self._get_math_core_params()
        # Merge specs: known channels keep their (possibly updated) calibration.
        merged: Dict[str, SensorSpec] = {**existing_specs, **new_specs}

        if agg is None:
            # First build for this ensemble.
            agg = AdaptiveAlarmAggregator(merged, config, context)
            self._ensemble_adaptive_aggregators[ensemble_uuid] = agg
        else:
            # A new channel joined — adopt the union, preserving FIR history.
            agg.update_specs(merged)

        # ── FHIR individualisation: per-patient Context_Log_Odds ──────────────
        # context is the shared static calculator (baseline P_0 + OR table); the
        # per-patient shift depends on THIS ensemble's active danger codes.
        try:
            ctx_log_odds = context.log_odds(danger_codes)
            agg.set_clinical_context(ctx_log_odds)
        except Exception as exc:
            self.logger.debug(f'[Adaptive] set_clinical_context failed: {exc}')

        self._ensemble_specs[ensemble_uuid] = merged
        self._ensemble_sensor_ids[ensemble_uuid] = frozenset(merged.keys())
        self.logger.info(
            f'[Adaptive] Built aggregator for ensemble={ensemble_uuid[:8]} — '
            f'{len(merged)} sensor(s), {len(danger_codes)} danger code(s).'
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
        self.logger.debug(
            f'[Adaptive] Discarded processor state for ensemble {ensemble_uuid[:8]}...'
        )

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

        Side-effect: if removing this alarm empties _active_alarms for the
        ensemble, the ensemble is removed from _escalated_ensembles (crisis
        fully resolved).

        Thread safety: protected by self.lock.
        """
        if not ensemble_uuid:
            return

        # Resolve + notify OUTSIDE the lock: topology.reverse_lookup_patient_room
        # and _notify_overview take locks internally — calling them inside self.lock
        # would nest/re-enter. threading.Lock() is NOT reentrant.
        crisis_resolved = False

        with self.lock:
            ensemble_cache = self._active_alarms.get(ensemble_uuid)
            if ensemble_cache and alert_key in ensemble_cache:
                del ensemble_cache[alert_key]
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
        """Signal the GC daemon thread to exit. Call on application shutdown."""
        self._stop_gc.set()
