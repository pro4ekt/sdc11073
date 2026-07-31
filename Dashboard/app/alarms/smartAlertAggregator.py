import logging
import math
import threading
import time
import uuid
from collections import deque
from typing import Optional, Tuple, Set, Dict

from ..fhirData import FHIRPatientData
from .alarmCoordinator import AlarmCoordinator, DeviceAlertEvidence
from .adaptive_alarm_aggregator import (
    AdaptiveAlarmAggregator,
    AggregatorConfig,
    PatientContext,
    SensorHardwareConfig,
)
from .device_profile_repo import DeviceReliabilityProfile

# TYPE_CHECKING guard to avoid circular imports when annotating DeviceHandler
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.deviceHandler import DeviceHandler

class SmartAlertAggregator:
    """
    Core of the Smart Alerting System for the SDC Orchestrator.
    Phase 1: Dynamic EnsembleContext formation based on device topology and patient data.
    Phase 2: (Future) Cross-device alarm validation within an ensemble.
    """

    def __init__(self, manager, overview_model=None):
        self.logger = logging.getLogger('sdc.consumer.aggregator')
        self._manager = manager
        self._overview_model = overview_model

        # Mutex protecting the aggregator's internal data structures
        self.lock = threading.Lock()

        # Dedicated lock for FHIR cache — prevents duplicate HTTP requests when
        # multiple devices for the same patient connect simultaneously.
        # self.lock must NOT be held when performing HTTP fetches (blocks all devices).
        self._fhir_lock = threading.Lock()

        # State Table: (patient_id, room_id) -> ensemble_uuid
        self._active_ensembles: Dict[Tuple[str, str], str] = {}

        # Device registry per ensemble: ensemble_uuid -> set(epr)
        self._ensemble_devices: Dict[str, Set[str]] = {}

        # FHIR cache: patient_id -> FHIRPatientData (populated on first access)
        self._fhir_cache: Dict[str, FHIRPatientData] = {}

        # FHIR clinical focus cache: patient_id -> list of clinical focus entries
        # extracted from Condition Extension elements.  Populated alongside
        # _fhir_cache and used as a per-patient override of rules.json.
        self._fhir_focus_cache: Dict[str, list] = {}

        # In-Memory physiological state graph, indexed by semantic concept codes.
        # Structure: ensemble_uuid -> concept_code -> deque[(value, timestamp), ...]
        # Each deque is a sliding window of the last 15 readings (maxlen=15).
        # The SignalProcessor reads snapshots of this graph to compute dx/dt.
        self._physiological_graph: Dict[str, Dict[str, deque]] = {}

        # Active alarm cache (TTL = ALARM_TTL_SEC).
        # Structure: ensemble_uuid -> alert_key -> (DeviceAlertEvidence, timestamp)
        # Each fired alarm is registered here so ensemble_evidences for Stage 2
        # contains ALL currently active alarms across ALL devices in the ensemble,
        # not just the triggering alarm.  Entries older than ALARM_TTL_SEC are
        # garbage-collected on each check_alert_validity call.
        self._active_alarms: Dict[str, Dict[str, Tuple[DeviceAlertEvidence, float]]] = {}
        self.ALARM_TTL_SEC: float = 10.0

        # Ensemble-level escalation state.
        # When the Bayesian pipeline returns ESCALATE for any alarm in an ensemble,
        # the ensemble UUID is added here.  While present, ALL alarms in the ensemble
        # bypass individual suppression and show as ON in the UI.
        # Cleared automatically when _active_alarms[ensemble_uuid] becomes empty
        # (all alarms expired from the TTL cache → crisis resolved).
        self._escalated_ensembles: Set[str] = set()

        # Timestamp of the last console graph dump (monotonic).
        # Used to rate-limit dump output: at most once per GRAPH_DUMP_INTERVAL_SEC.
        self._last_graph_dump_ts: float = 0.0
        self.GRAPH_DUMP_INTERVAL_SEC: float = 2.0

        # ── Adaptive Stage-2 state (continuous-time Bayesian filter) ──────────
        # The former static ClinicalRiskFilter (single-shot LR-product) is
        # replaced by one AdaptiveAlarmAggregator instance PER patient ensemble.
        # Each instance is a stateful stochastic process (sliding windows +
        # persistence timer), so its lifecycle is owned here, not in the
        # stateless AlarmCoordinator facade.
        #
        # Thread-safety: tick() mutates the aggregator's internal windows/timer and
        # is NOT re-entrant.  All aggregator lifecycle operations (build, tick,
        # patient-context update, dt bookkeeping) are serialised by _adaptive_lock,
        # which is DISTINCT from self.lock.  Lock ordering rule to avoid deadlock:
        # never hold self.lock while acquiring _adaptive_lock (the two are only ever
        # taken sequentially, never nested).
        self._adaptive_lock = threading.Lock()
        self._ensemble_adaptive_aggregators: Dict[str, AdaptiveAlarmAggregator] = {}
        # Sensor composition each aggregator was built with (alert_key set).  Used
        # to detect when a new device's alarm requires a rebuild.
        self._ensemble_sensor_ids: Dict[str, frozenset] = {}
        # Monotonic timestamp of the previous tick() per ensemble → dt_step source.
        self._last_tick_ts: Dict[str, float] = {}
        # V2 — grace-period bookkeeping: ensemble_uuid → monotonic resolution time.
        # Adaptive state is retained for ADAPTIVE_DISCARD_GRACE_SEC after an ensemble
        # resolves so a rapidly re-firing (jittering) alarm reuses the SAME persistence
        # timer instead of resetting Δt on every ON/OFF cycle.
        self._ensemble_resolved_ts: Dict[str, float] = {}

        # Adaptive filter tuning (see AggregatorConfig / SensorHardwareConfig).
        # tau_base is set as a FRACTION of the ensemble's W_max so the structural
        # Base_Logit stays scale-invariant to the number of devices:
        #   TAU_BASE_FRACTION = 0.5 → tau = 0.5 → Base_Logit = ln(1) = 0.0.
        self.ADAPTIVE_TAU_FRACTION: float = 0.5
        self.ADAPTIVE_DECAY_RATE:   float = 0.05   # λ — Θ(t) descent per second of persistence
        self.ADAPTIVE_THETA_MIN:    float = -3.0   # hard floor for Θ(t)
        self.ADAPTIVE_WINDOW_SIZE:  int   = 5      # T — sliding-window length (ticks)
        # FHIR prior mapping: prior = clip(BASE + PER_CODE·|danger_codes|, [0.01, 0.5]).
        self.ADAPTIVE_PRIOR_BASE:     float = 0.01
        self.ADAPTIVE_PRIOR_PER_CODE: float = 0.08
        # V4 — cap the per-tick persistence credit so a starved/paused notification
        # thread cannot advance Δt by an arbitrarily large jump and force escalation
        # by collapsing Θ(t) to theta_min in a single step.
        self.ADAPTIVE_MAX_DT_STEP: float = 20.0
        # V2 — how long an aggregator survives after its ensemble resolves, so a fast
        # OFF→ON jitter reuses the same persistence timer instead of resetting Δt.
        self.ADAPTIVE_DISCARD_GRACE_SEC: float = 10.0

        # DSP signal processor — stateless, no extra locking needed.
        self.alarm_coordinator = AlarmCoordinator()

        # Background Watchdog GC — runs every ALARM_TTL_SEC/2 seconds.
        # Independently clears stale _active_alarms entries and notifies UI
        # even when no SDC alarm packets arrive (device went silent).
        self._stop_gc = threading.Event()
        self._gc_thread = threading.Thread(
            target=self._gc_loop, daemon=True, name='AggregatorGC'
        )
        self._gc_thread.start()

    def update_metric_state(self, ensemble_uuid: str, concept_code: str, value: float) -> None:
        """
        Appends a metric reading into the sliding-window deque for the given
        ensemble and semantic concept code.

        Each entry in the deque is a (value, timestamp) tuple.
        The deque is bounded to 15 elements (maxlen=15) — older readings are
        automatically discarded, keeping memory usage constant.

        Thread safety: protected by self.lock.
        Called from DeviceHandler.on_metric_update() (sdc11073 notification thread).
        """
        with self.lock:
            if ensemble_uuid not in self._physiological_graph:
                self._physiological_graph[ensemble_uuid] = {}
                # self.logger.info(
                #     f'[PhysGraph] New ensemble entry created: {ensemble_uuid[:8]}...'
                # )
            if concept_code not in self._physiological_graph[ensemble_uuid]:
                self._physiological_graph[ensemble_uuid][concept_code] = deque(maxlen=15)
            self._physiological_graph[ensemble_uuid][concept_code].append(
                (value, time.monotonic())
            )
        # self.logger.debug(
        #     f'[PhysGraph] Write: ensemble={ensemble_uuid[:8]} '
        #     f'concept={concept_code!r} value={value:.4g}'
        # )

        # -- Rate-limited graph dump (DEBUG only — not shown at INFO level) ----
        now = time.monotonic()
        if now - self._last_graph_dump_ts >= self.GRAPH_DUMP_INTERVAL_SEC:
            self._last_graph_dump_ts = now
            # self.logger.debug(self.dump_physiological_graph())

    def get_metric_state(
        self,
        ensemble_uuid: str,
        concept_code: str,
        max_age_sec: float = 15.0,
    ) -> Optional[float]:
        """
        Returns the most recent cached value for the given ensemble and concept
        code (last element of the deque), or None if stale / absent.

        Thread safety: protected by self.lock.
        """
        with self.lock:
            buf: Optional[deque] = (
                self._physiological_graph
                .get(ensemble_uuid, {})
                .get(concept_code)
            )
            if not buf:
                # self.logger.debug(
                #     f'[PhysGraph] Miss: ensemble={ensemble_uuid[:8]} '
                #     f'concept={concept_code!r} (no entry)'
                # )
                return None
            value, timestamp = buf[-1]
            age = time.monotonic() - timestamp
            if age > max_age_sec:
                # self.logger.warning(
                #     f'[PhysGraph] Stale: ensemble={ensemble_uuid[:8]} '
                #     f'concept={concept_code!r} age={age:.1f}s > max={max_age_sec}s — returning None'
                # )
                return None
            # self.logger.debug(
            #     f'[PhysGraph] Hit:  ensemble={ensemble_uuid[:8]} '
            #     f'concept={concept_code!r} value={value:.4g} age={age:.1f}s'
            # )
            return value

    def dump_physiological_graph(self) -> str:
        """
        Returns a human-readable snapshot of the physiological graph.
        Each row shows the latest reading from the deque (newest element).

        Thread safety: protected by self.lock.
        """
        lines: list[str] = ['[PhysGraph] ── Snapshot ──────────────────────────────']
        with self.lock:
            if not self._physiological_graph:
                lines.append('[PhysGraph]   (empty)')
            for ens_uuid, concepts in self._physiological_graph.items():
                now = time.monotonic()   # must match the clock used in update_metric_state
                lines.append(f'[PhysGraph]   Ensemble {ens_uuid[:8]}...')
                for code, buf in sorted(concepts.items()):
                    if not buf:
                        lines.append(f'[PhysGraph]     {code:<30} = (empty buffer)')
                        continue
                    value, timestamp = buf[-1]
                    age = now - timestamp
                    stale = ' ⚠ STALE' if age > 15.0 else ''
                    lines.append(
                        f'[PhysGraph]     {code:<30} = {value:>10.4g}'
                        f'  (age={age:.1f}s, buf={len(buf)}/15{stale})'
                    )
        lines.append('[PhysGraph] ────────────────────────────────────────────────')
        return '\n'.join(lines)

    def _collect_patient_danger_codes(self, ensemble_uuid: str) -> Set[str]:
        """
        Helper: collect and normalise FHIR danger codes for the patient bound to
        ensemble_uuid.  MUST be called with self.lock already held.

        Returns an empty set if the patient or FHIR data is unavailable.
        """
        patient_danger_codes: Set[str] = set()

        # Reverse-lookup: ensemble_uuid → patient_id
        patient_id: Optional[str] = None
        for (pid, _room), eid in self._active_ensembles.items():
            if eid == ensemble_uuid:
                patient_id = pid
                break

        if patient_id and patient_id in self._fhir_cache:
            fhir_data = self._fhir_cache[patient_id]
            try:
                raw_codes = fhir_data.get_danger_codes() or []
                for dc in raw_codes:
                    code   = dc.get('code', '') or ''
                    system = dc.get('system', '') or ''
                    if not code:
                        continue
                    patient_danger_codes.add(code)
                    if system:
                        patient_danger_codes.add(f'{system}:{code}')
                    if 'snomed' in system.lower():
                        patient_danger_codes.add(f'SNOMED:{code}')
            except Exception as _exc:
                self.logger.debug(
                    f'[Aggregator] _collect_patient_danger_codes: '
                    f'FHIR code extraction failed: {_exc}'
                )

        return patient_danger_codes

    def _collect_fhir_focus(self, ensemble_uuid: str) -> list:
        """
        Helper: returns FHIR clinical focus rules for the patient bound to
        ensemble_uuid, from _fhir_focus_cache.
        MUST be called with self.lock already held.

        Returns empty list if no FHIR focus was loaded (rules.json used as fallback).
        """
        patient_id: Optional[str] = None
        for (pid, _room), eid in self._active_ensembles.items():
            if eid == ensemble_uuid:
                patient_id = pid
                break
        if patient_id:
            return self._fhir_focus_cache.get(patient_id, [])
        return []

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
        roc_limit: Optional[float] = None,
    ) -> str:
        """
        Two-stage alarm pipeline gate — returns a tri-state routing decision.

        Stage 1 — HardwareArtifactFilter (dx/dt RoC gate, per-device):
            If the metric jump is physiologically impossible → ``"SUPPRESS"``.
            The device-level UI hides the alarm completely (noise/artifact).

        Stage 2 — AdaptiveAlarmAggregator (continuous-time Bayesian filter, per-ensemble):
            One AdaptiveAlarmAggregator instance per ensemble is advanced by exactly
            one ``tick()`` on each alarm event.  It weights each sensor by its
            hardware reliability w_j = ln(Se_j/FAR_j), smooths activations over a
            sliding window s_j(t), decays the escalation boundary Θ(t) the longer any
            alarm persists (λ·Δt), and shifts Θ(t) by the FHIR-derived clinical prior.
            The tick verdict is mapped to routing:
                result.is_escalated == True  → ``"ESCALATE"``  (Red)
                result.is_escalated == False → ``"WARN"``      (Yellow)

        Fail-open paths (no metric history, unknown concept) → ``"ESCALATE"``
        so real alarms are never silently swallowed.

        Returns
        -------
        str
            ``"ESCALATE"`` — forward alarm; device + patient card Red.
            ``"WARN"``     — real alarm below Θ(t); device card Yellow.
            ``"SUPPRESS"`` — Stage 1 artifact; hide completely from UI.
        """
        if not metric_concept:
            return 'ESCALATE'  # no metric mapping → fail-open

        now = time.monotonic()

        with self.lock:
            # ── Early return: ensemble already escalated ──────────────────────────
            # Once a crisis is confirmed, every subsequent alarm in the same
            # ensemble must also show as ON — do not re-run the suppression
            # pipeline until all alarms clear.
            if ensemble_uuid in self._escalated_ensembles:
                if ensemble_uuid not in self._active_alarms:
                    self._active_alarms[ensemble_uuid] = {}
                ev = DeviceAlertEvidence(
                    alert_key=alert_key,
                    metric_concept=metric_concept,
                    manufacturer=manufacturer,
                    model=model,
                    ensemble_uuid=ensemble_uuid,
                    biceps_priority=biceps_priority,
                    reliability_profile=reliability_profile,
                    roc_limit=roc_limit,
                )
                self._active_alarms[ensemble_uuid][alert_key] = (ev, now)
                return 'ESCALATE'

            buf: Optional[deque] = (
                self._physiological_graph
                .get(ensemble_uuid, {})
                .get(metric_concept)
            )
            if buf is None:
                return 'ESCALATE'  # no metric history yet → fail-open
            buf_snapshot = deque(buf, maxlen=buf.maxlen)

            # ── Active alarm cache (TTL) ──────────────────────────────────────
            if ensemble_uuid not in self._active_alarms:
                self._active_alarms[ensemble_uuid] = {}

            triggering_evidence = DeviceAlertEvidence(
                alert_key=alert_key,
                metric_concept=metric_concept,
                manufacturer=manufacturer,
                model=model,
                ensemble_uuid=ensemble_uuid,
                biceps_priority=biceps_priority,
                reliability_profile=reliability_profile,
                roc_limit=roc_limit,
            )
            self._active_alarms[ensemble_uuid][alert_key] = (triggering_evidence, now)

            # TTL-GC is now handled exclusively by the background _gc_loop.
            # Removing stale entries here was unreachable for escalated ensembles
            # (early-return above) and caused Bug A + Bug B. Watchdog fixes both.

            ensemble_evidences: list[DeviceAlertEvidence] = [
                ev for ev, _ts in self._active_alarms[ensemble_uuid].values()
            ]

            # Snapshot everything Stage 2 needs while still under self.lock so the
            # adaptive drive below never touches self.lock-protected structures.
            active_keys: Set[str] = set(self._active_alarms[ensemble_uuid].keys())
            patient_prior: float = self._derive_patient_prior_locked(ensemble_uuid)

            # ── Stage 2 drive (adaptive aggregator) — V3: executed while STILL
            # holding self.lock to close the TOCTOU gap with the GC daemon (the
            # snapshot + tick are now atomic w.r.t. _cleanup_stale_alarms).
            # Lock hierarchy A → B: acquiring _adaptive_lock while holding self.lock
            # is permitted; _adaptive_lock never re-acquires self.lock, so the order
            # is acyclic.  tick() is pure CPU (no network/FHIR I/O), so holding
            # self.lock for the microseconds it takes is safe.
            with self._adaptive_lock:
                last_ts = self._last_tick_ts.get(ensemble_uuid)
                # dt_step must track TRUE wall-clock so the persistence timer Δt
                # equals real elapsed seconds since first activation, independent of
                # how many ticks occur.  A ~0 delta (two devices re-asserting at the
                # same instant) must contribute 0.0, not a spurious full step.
                if last_ts is None:
                    dt_step = 0.0                  # first tick of this ensemble
                else:
                    dt_step = now - last_ts
                    if dt_step < 0.0:
                        dt_step = 0.0              # guard against clock non-monotonicity
                # V4 — cap the per-tick credit so thread starvation cannot force
                # escalation by collapsing Θ(t) to theta_min in a single jump.
                dt_step = min(dt_step, self.ADAPTIVE_MAX_DT_STEP)
                self._last_tick_ts[ensemble_uuid] = now

                aggregator = self._get_or_build_adaptive_aggregator_locked(
                    ensemble_uuid, ensemble_evidences, patient_prior
                )
                # Binary activation vector for this tick: 1 if the sensor's alarm is
                # currently in the TTL cache, else 0 (sensor fell silent → decays).
                sensor_states: Dict[str, int] = {
                    sid: (1 if sid in active_keys else 0)
                    for sid in aggregator.sensor_ids
                }

                decision = self.alarm_coordinator.evaluate(
                    aggregator,
                    triggering_evidence,
                    buf_snapshot,
                    sensor_states,
                    dt_step,
                    ensemble_evidences,
                )

        # ── Route based on tri-state decision ────────────────────────────────────
        if decision.escalate:
            with self.lock:
                self._escalated_ensembles.add(ensemble_uuid)
            self._propagate_escalation_to_devices(ensemble_uuid)
            _patient_id, _room = self._reverse_lookup_patient_room(ensemble_uuid)
            self._notify_overview(ensemble_uuid, _patient_id, _room,
                                  is_escalated=True, risk_score=decision.risk_score,
                                  is_warning=False)
            return 'ESCALATE'

        if decision.suppression_stage == 'HardwareArtifactFilter':
            # Stage 1: physiological artifact (RoC exceeded) — suppress silently.
            # Do NOT emit a PatientOverview Warning: noise must not trigger UI state.
            return 'SUPPRESS'

        # Stage 2: alarm is real but risk < 5.0 → Warning (Yellow).
        # Only notify when there are active alarms in the cache to avoid spurious
        # Yellow flash on fail-open paths (empty buffer / fresh device).
        with self.lock:
            has_active = bool(self._active_alarms.get(ensemble_uuid))
        if has_active:
            _patient_id, _room = self._reverse_lookup_patient_room(ensemble_uuid)
            self._notify_overview(ensemble_uuid, _patient_id, _room,
                                  is_escalated=False, risk_score=decision.risk_score,
                                  is_warning=True)
        return 'WARN'

    # ── Adaptive Stage-2 support ──────────────────────────────────────────────

    def _derive_patient_prior_locked(self, ensemble_uuid: str) -> float:
        """
        Derive the clinical crisis prior P(C|D_i) for the patient bound to
        ``ensemble_uuid`` from cached FHIR data.

        Heuristic mapping (monotone in comorbidity burden):
            prior = clip(BASE + PER_CODE · |danger_codes|, [0.01, 0.5])

        A patient with more active FHIR Conditions (danger codes) carries a higher
        pre-test probability of decompensation, which raises Prior_Logit, lowers the
        adaptive Θ(t), and makes escalation easier — the clinical-context
        sensitisation the specification calls for.  Returns ADAPTIVE_PRIOR_BASE when
        no FHIR data is available (fail-safe: neutral, minimally-sensitising prior).

        MUST be called with self.lock already held (reads _active_ensembles / _fhir_cache).
        """
        patient_id: Optional[str] = None
        for (pid, _room), eid in self._active_ensembles.items():
            if eid == ensemble_uuid:
                patient_id = pid
                break

        if patient_id and patient_id in self._fhir_cache:
            try:
                n_codes = len(self._fhir_cache[patient_id].get_danger_codes() or [])
            except Exception as _exc:
                self.logger.debug(
                    f'[Adaptive] danger-code prior extraction failed for '
                    f'patient_id={patient_id!r}: {_exc}'
                )
                n_codes = 0
            prior = self.ADAPTIVE_PRIOR_BASE + self.ADAPTIVE_PRIOR_PER_CODE * n_codes
            return max(0.01, min(0.5, prior))

        return self.ADAPTIVE_PRIOR_BASE

    def _get_or_build_adaptive_aggregator_locked(
        self,
        ensemble_uuid: str,
        ensemble_evidences: list[DeviceAlertEvidence],
        patient_prior: float,
    ) -> AdaptiveAlarmAggregator:
        """
        Return the AdaptiveAlarmAggregator for ``ensemble_uuid``, building it lazily.

        Sensor identity: each device's alarm is one sensor keyed by its MDIB
        ``alert_key``.  Its SensorHardwareConfig is derived from the pre-fetched
        DeviceReliabilityProfile carried in the evidence DTO (Se, FAR); absent
        profiles fall back to the neutral (0.5, 0.5) → LR+ = 1.0.

        Rebuild policy: if a *new* sensor (alert_key not seen before) joins the
        ensemble, the aggregator is rebuilt over the UNION of old and new sensors.
        This is a deliberate PoC simplification — the rebuild resets the per-sensor
        sliding windows and the persistence timer.  In the reference scenario the
        sensor set stabilises within the first few hundred milliseconds (all devices
        fire early), so the reset window is negligible.  A production build would
        support incremental sensor insertion without discarding state.

        Always refreshes the patient context so a freshly-fetched FHIR prior takes
        effect on the very next tick.

        MUST be called with self._adaptive_lock held.
        """
        required: frozenset = frozenset(ev.alert_key for ev in ensemble_evidences)
        existing: frozenset = self._ensemble_sensor_ids.get(ensemble_uuid, frozenset())
        agg = self._ensemble_adaptive_aggregators.get(ensemble_uuid)

        # V2 — the ensemble is active again: cancel any pending grace-period discard
        # so the persistence timer Δt is preserved across a brief OFF→ON jitter.
        # (Safe: this runs under self.lock via check_alert_validity's Stage-2 block.)
        self._ensemble_resolved_ts.pop(ensemble_uuid, None)

        # Reuse path — current aggregator already covers every active sensor.
        if agg is not None and required <= existing:
            agg.update_patient_context(
                PatientContext(prior_crisis_probability=patient_prior)
            )
            return agg

        # (Re)build path — compose SensorHardwareConfig for the UNION of sensors.
        union: frozenset = existing | required
        ev_by_key = {ev.alert_key: ev for ev in ensemble_evidences}

        configs: list[SensorHardwareConfig] = []
        for sid in union:
            ev = ev_by_key.get(sid)
            if ev is not None and ev.reliability_profile is not None:
                se  = ev.reliability_profile.sensitivity
                far = ev.reliability_profile.false_alarm_rate
            else:
                # Fail-safe neutral profile (LR+ = 1.0 → w_j = 0, no vote weight).
                se, far = 0.5, 0.5
            configs.append(
                SensorHardwareConfig(
                    sensor_id=sid,
                    sensitivity=se,
                    far=far,
                    window_size=self.ADAPTIVE_WINDOW_SIZE,
                )
            )

        # tau_base as a fraction of the ensemble's W_max keeps Base_Logit scale-
        # invariant to device count (see ADAPTIVE_TAU_FRACTION).
        w_max = sum(
            math.log(
                max(1e-5, min(1.0 - 1e-5, c.sensitivity))
                / max(1e-5, min(1.0 - 1e-5, c.far))
            )
            for c in configs
        )
        tau_base = self.ADAPTIVE_TAU_FRACTION * w_max if w_max > 0.0 else 0.5

        agg = AdaptiveAlarmAggregator(
            sensor_configs=configs,
            aggregator_config=AggregatorConfig(
                tau_base=tau_base,
                decay_rate=self.ADAPTIVE_DECAY_RATE,
                theta_min=self.ADAPTIVE_THETA_MIN,
            ),
            patient_context=PatientContext(prior_crisis_probability=patient_prior),
        )
        self._ensemble_adaptive_aggregators[ensemble_uuid] = agg
        self._ensemble_sensor_ids[ensemble_uuid] = union
        self.logger.info(
            f'[Adaptive] Built aggregator for ensemble={ensemble_uuid[:8]} — '
            f'{len(configs)} sensor(s), W_max={w_max:.3f}, tau_base={tau_base:.3f}, '
            f'prior={patient_prior:.3f}.'
        )
        return agg

    def _discard_adaptive_state(self, ensemble_uuid: str) -> None:
        """
        Drop all adaptive Stage-2 state for a fully-resolved ensemble.

        Called when the TTL cache for an ensemble becomes empty (crisis / warning
        resolved).  MUST NOT be called while holding self.lock — it acquires
        _adaptive_lock, and the lock-ordering rule forbids nesting the two.
        """
        with self._adaptive_lock:
            self._ensemble_adaptive_aggregators.pop(ensemble_uuid, None)
            self._ensemble_sensor_ids.pop(ensemble_uuid, None)
            self._last_tick_ts.pop(ensemble_uuid, None)

    def release_device(self, epr: str) -> None:
        """
        V1 — memory-safety teardown for a disconnected device.

        Removes ``epr`` from ensemble bookkeeping; if it was the LAST member of its
        ensemble, fully tears down every per-ensemble structure to prevent unbounded
        growth across admit/discharge/reconnect churn:
          _ensemble_devices, _physiological_graph, _active_alarms,
          _escalated_ensembles, _ensemble_resolved_ts, _active_ensembles, and the
          per-ensemble adaptive state.  FHIR caches for the patient are evicted only
          when the patient has no remaining ensemble.

        Called from SdcMyConsumer.remove_device() when a device thread dies.

        Lock discipline: all shared-map mutations happen under self.lock; the
        adaptive-state discard (which acquires _adaptive_lock) runs AFTER releasing
        self.lock, honouring the A → B ordering.
        """
        if not epr:
            return

        orphaned: Optional[str] = None
        dead_patient: Optional[str] = None

        with self.lock:
            # Locate the ensemble this device belongs to and drop the EPR.
            for eid, eprs in list(self._ensemble_devices.items()):
                if epr in eprs:
                    eprs.discard(epr)
                    if not eprs:
                        # Last device gone → the ensemble is dead. Tear it all down.
                        orphaned = eid
                        del self._ensemble_devices[eid]
                        self._physiological_graph.pop(eid, None)
                        self._active_alarms.pop(eid, None)
                        self._escalated_ensembles.discard(eid)
                        self._ensemble_resolved_ts.pop(eid, None)
                        for key, mapped in list(self._active_ensembles.items()):
                            if mapped == eid:
                                dead_patient = key[0]
                                del self._active_ensembles[key]
                                break
                    break

            # Evict FHIR caches only if the patient has no other active ensemble.
            if dead_patient and not any(
                key[0] == dead_patient for key in self._active_ensembles
            ):
                self._fhir_cache.pop(dead_patient, None)
                self._fhir_focus_cache.pop(dead_patient, None)

        # Adaptive discard outside self.lock (acquires _adaptive_lock — A → B order).
        if orphaned is not None:
            self._discard_adaptive_state(orphaned)
            self.logger.info(
                f'[Aggregator] release_device: ensemble {orphaned[:8]}... fully '
                f'released (last device {epr[-12:]} disconnected).'
            )
        else:
            self.logger.debug(
                f'[Aggregator] release_device: {epr[-12:]} removed; ensemble still '
                f'has other members (or device was never bound).'
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

        # Capture resolution data outside the lock so that _reverse_lookup_patient_room
        # and _notify_overview (both acquire self.lock internally) are called after
        # the with-block exits.  threading.Lock() is NOT reentrant — calling them
        # inside the lock causes a deadlock that silently swallows the notify call.
        crisis_resolved = False
        _patient_id = ''
        _room = ''

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
                # Inline reverse-lookup while lock is held (avoids re-entrant acquire).
                for (pid, room), eid in self._active_ensembles.items():
                    if eid == ensemble_uuid:
                        _patient_id, _room = pid, room
                        break

        # Notify PatientOverview outside the lock — _notify_overview acquires self.lock
        # for device_count; calling it inside would re-enter and deadlock.
        if crisis_resolved:
            # V2 — adaptive state is NOT discarded here anymore; the grace-period
            # sweep in _cleanup_stale_alarms() tears it down once the ensemble has
            # stayed quiet past ADAPTIVE_DISCARD_GRACE_SEC.  This preserves the
            # persistence timer across a brief OFF→ON jitter cycle.
            self._notify_overview(ensemble_uuid, _patient_id, _room,
                                  is_escalated=False, risk_score=0.0, is_warning=False)

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
        with self.lock:
            eprs: set[str] = set(self._ensemble_devices.get(ensemble_uuid, set()))

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

    # ── PatientOverviewModel bridge ───────────────────────────────────────────

    def _reverse_lookup_patient_room(self, ensemble_uuid: str) -> tuple[str, str]:
        """Return (patient_id, room) for an ensemble UUID. Thread-safe."""
        with self.lock:
            for (pid, room), eid in self._active_ensembles.items():
                if eid == ensemble_uuid:
                    return pid, room
        return '', ''

    def _notify_overview(
        self,
        ensemble_uuid: str,
        patient_id: str,
        room: str,
        is_escalated: bool,
        risk_score: float,
        is_warning: bool = False,
    ) -> None:
        """
        Push an ensemble summary to PatientOverviewModel (thread-safe via its
        internal queue + Signal bridge).  No-op when _overview_model is None.

        Tri-state semantics:
          is_escalated=True,  is_warning=False  → Red   (risk ≥ 5.0)
          is_escalated=False, is_warning=True   → Yellow (risk > 0 but < 5.0)
          is_escalated=False, is_warning=False  → Blue   (no active alarms)
        """
        if self._overview_model is None:
            return
        with self.lock:
            device_count = len(self._ensemble_devices.get(ensemble_uuid, set()))
        try:
            self._overview_model.updateEnsemble(
                ensemble_uuid,
                patient_id,
                room,
                device_count,
                is_escalated,
                risk_score,
                is_warning,
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

        # Notify PatientOverview for fully-resolved ensembles.
        for ensemble_uuid in resolved_ensembles:
            patient_id, room = self._reverse_lookup_patient_room(ensemble_uuid)
            self._notify_overview(ensemble_uuid, patient_id, room,
                                  is_escalated=False, risk_score=0.0, is_warning=False)

        # V2 — tear down adaptive Stage-2 state for grace-expired ensembles.
        # Done outside self.lock (acquires _adaptive_lock — A → B ordering).
        for eid in grace_expired:
            self._discard_adaptive_state(eid)

        # Force UI refresh on every device whose alarm cache changed (partially or fully).
        # update_data() will cross-check against is_alarm_active() and skip stale MDIB entries.
        manager_devices: dict = getattr(self._manager, 'devices', {})
        for ensemble_uuid in stale_ensembles:
            with self.lock:
                eprs: set[str] = set(self._ensemble_devices.get(ensemble_uuid, set()))
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

    def check_alert_priority(
        self,
        ensemble_uuid: Optional[str],
        alert_concept: Optional[str],
    ) -> bool:
        """
        Rule-engine priority check — disabled (ruleEvaluator removed).
        Returns False; use the DSP validity gate (check_alert_validity) instead.
        """
        return False

    def audit_topology(self, ensemble_uuid: str) -> list[str]:
        """
        Topology audit — disabled (ruleEvaluator removed).
        Returns empty list.
        """
        return []

    def log_clinical_focus_summary(self, ensemble_uuid: str) -> None:
        """
        Clinical focus summary — disabled (ruleEvaluator removed).
        """
        self.logger.info(
            f'[ClinicalFocus] ensemble={ensemble_uuid[:8]}... — '
            f'rule engine removed; DSP SignalProcessor active.'
        )

    def _extract_patient_and_room(self, device_handler: 'DeviceHandler') -> Tuple[Optional[str], Optional[str]]:
        """
        Extracts the patient identifier and room from the device's MDIB.
        Executed while holding device_handler.data_lock.

        Algorithm:
          1. Room       -- from LocationContextState.LocationDetail.Room
          2. Patient ID -- primary:  WorkflowContextState.WorkflowDetail.
                                     Patient.Identification[0].Extension
                          fallback:  PatientContextState.Identification[0].Extension

        Returns:
          (patient_id, room) -- either element may be None if data is absent.
        """
        from sdc11073.xml_types import pm_qnames as pm

        patient_id: Optional[str] = None
        room: Optional[str] = None

        with device_handler.data_lock:
            if not device_handler.mdib:
                self.logger.debug(
                    f'[Aggregator] _extract_patient_and_room: MDIB not ready for {device_handler.epr[-12:]}'
                )
                return None, None

            # -- 1. Room ----------------------------------------------------------
            try:
                loc_states = device_handler.mdib.context_states.NODETYPE.get(
                    pm.LocationContextState, []
                )
                if loc_states and loc_states[0].LocationDetail:
                    raw_room = loc_states[0].LocationDetail.Room
                    room = str(raw_room) if raw_room else None
            except Exception as exc:
                self.logger.warning(
                    f'[Aggregator] Error reading LocationContextState '
                    f'for {device_handler.epr[-12:]}: {exc}'
                )

            # -- 2. Patient ID -- primary: WorkflowContextState -------------------
            try:
                wf_states = device_handler.mdib.context_states.NODETYPE.get(
                    pm.WorkflowContextState, []
                )
                for wf_state in wf_states:
                    wd = getattr(wf_state, 'WorkflowDetail', None)
                    if not wd:
                        continue
                    pat = getattr(wd, 'Patient', None)
                    if not pat:
                        continue
                    identifications = getattr(pat, 'Identification', None) or []
                    for ident in identifications:
                        # Primary: Extension XML attribute
                        ext = getattr(ident, 'Extension', None)
                        if ext:
                            patient_id = str(ext)
                            break
                        # Fallback: IdentifierName child element
                        id_names = getattr(ident, 'IdentifierName', None) or []
                        if id_names:
                            raw = id_names[0] if isinstance(id_names, list) else id_names
                            text = getattr(raw, 'text', None) or str(raw)
                            if text:
                                patient_id = text
                                break
                    if patient_id:
                        break
            except Exception as exc:
                self.logger.warning(
                    f'[Aggregator] Error reading WorkflowContextState '
                    f'for {device_handler.epr[-12:]}: {exc}'
                )

            # -- 3. Patient ID -- fallback: PatientContextState -------------------
            if not patient_id:
                try:
                    pat_states = device_handler.mdib.context_states.NODETYPE.get(
                        pm.PatientContextState, []
                    )
                    for pat_state in pat_states:
                        identifications = getattr(pat_state, 'Identification', None) or []
                        for ident in identifications:
                            ext = getattr(ident, 'Extension', None)
                            if ext:
                                patient_id = str(ext)
                                break
                            id_names = getattr(ident, 'IdentifierName', None) or []
                            if id_names:
                                raw = id_names[0] if isinstance(id_names, list) else id_names
                                text = getattr(raw, 'text', None) or str(raw)
                                if text:
                                    patient_id = text
                                    break
                        if patient_id:
                            break
                    # Last resort: Givenname + Familyname from CoreData
                    if not patient_id:
                        for pat_state in pat_states:
                            core = getattr(pat_state, 'CoreData', None)
                            if not core:
                                continue
                            given = getattr(core, 'Givenname', None) or ''
                            family = getattr(core, 'Familyname', None) or ''
                            name = f'{given} {family}'.strip()
                            if name:
                                patient_id = name
                                break
                except Exception as exc:
                    self.logger.warning(
                        f'[Aggregator] Error reading PatientContextState '
                        f'for {device_handler.epr[-12:]}: {exc}'
                    )

        self.logger.debug(
            f'[Aggregator] Extracted for {device_handler.epr[-12:]}: '
            f'patient_id={patient_id!r}, room={room!r}'
        )
        return patient_id, room

    def _get_or_fetch_fhir_data(self, patient_id: str) -> Optional[FHIRPatientData]:
        """
        Returns a cached FHIRPatientData instance for patient_id, or fetches
        it from the FHIR server on the first call.

        Thread safety: uses double-checked locking via _fhir_lock to prevent
        duplicate HTTP requests when multiple devices for the same patient
        connect simultaneously (e.g., at exhibition power-on).
        self.lock must NOT be held by the caller (HTTP fetch may take seconds).
        """
        # Fast path: GIL-safe dict read (no lock needed for read in CPython)
        if patient_id in self._fhir_cache:
            self.logger.debug(
                f'[Aggregator] FHIR cache hit for patient_id={patient_id!r}.'
            )
            return self._fhir_cache[patient_id]

        # Slow path: acquire dedicated FHIR lock to serialize concurrent fetches
        with self._fhir_lock:
            # Re-check inside the lock (another thread may have fetched while we waited)
            if patient_id in self._fhir_cache:
                self.logger.debug(
                    f'[Aggregator] FHIR cache hit (after lock) for patient_id={patient_id!r}.'
                )
                return self._fhir_cache[patient_id]

            self.logger.info(
                f'[Aggregator] FHIR cache miss -- fetching data for patient_id={patient_id!r}...'
            )
            try:
                fhir_data = FHIRPatientData()
                fhir_data.fetch(patient_id)
                self._fhir_cache[patient_id] = fhir_data

                try:
                    fhir_focus = fhir_data.get_clinical_focus()
                    self._fhir_focus_cache[patient_id] = fhir_focus
                    if fhir_focus:
                        self.logger.info(
                            f'[Aggregator] FHIR clinical focus: {len(fhir_focus)} rule(s) '
                            f'loaded from Condition Extensions for patient_id={patient_id!r}.'
                        )
                    else:
                        self.logger.debug(
                            f'[Aggregator] No FHIR clinical focus extensions found for '
                            f'patient_id={patient_id!r} -- using rules.json only.'
                        )
                except Exception as _focus_exc:
                    self._fhir_focus_cache[patient_id] = []
                    self.logger.warning(
                        f'[Aggregator] FHIR clinical focus extraction failed for '
                        f'patient_id={patient_id!r}: {_focus_exc}'
                    )

                self.logger.info(
                    f'[Aggregator] FHIR data fetched and cached for patient_id={patient_id!r}.'
                )
                return fhir_data
            except Exception as exc:
                self.logger.error(
                    f'[Aggregator] FHIR fetch failed for patient_id={patient_id!r}: {exc}. '
                    f'Proceeding without FHIR data.'
                )
                return None

    def evaluate_and_bind_device(self, device_handler: 'DeviceHandler') -> None:
        """
        Entry point for a newly connected device.
        Evaluates the device context and either creates a new ensemble or
        joins the device to an existing one.

        Algorithm:
          1. Extract (patient_id, room) from the MDIB.
          2. If both values are present, form the key (patient_id, room).
          3. Under aggregator.lock check _active_ensembles:
               - key exists  -> use the existing ensemble_uuid
               - key absent  -> generate a new UUID, register the ensemble
          4. Add the device EPR to _ensemble_devices[ensemble_uuid].
          5. OUTSIDE the lock call device_handler.apply_ensemble_context(ensemble_uuid)
             (the SOAP network call must not hold the aggregator mutex).
        """
        patient_id, room = self._extract_patient_and_room(device_handler)

        if not patient_id or not room:
            self.logger.info(
                f'[Aggregator] Device {device_handler.epr[-12:]} skipped -- '
                f'incomplete context: patient_id={patient_id!r}, room={room!r}. '
                f'No ensemble will be formed until both values are available.'
            )
            return

        key: Tuple[str, str] = (patient_id, room)
        ensemble_uuid: str

        with self.lock:
            if key in self._active_ensembles:
                # -- Existing ensemble ----------------------------------------
                ensemble_uuid = self._active_ensembles[key]
                self.logger.info(
                    f'[Aggregator] Device {device_handler.epr[-12:]} joining existing '
                    f'ensemble {ensemble_uuid[:8]}... '
                    f'(patient={patient_id}, room={room})'
                )
            else:
                # -- New ensemble ---------------------------------------------
                ensemble_uuid = str(uuid.uuid4())
                self._active_ensembles[key] = ensemble_uuid
                self._ensemble_devices[ensemble_uuid] = set()
                self.logger.info(
                    f'[Aggregator] New ensemble {ensemble_uuid[:8]}... created '
                    f'for patient={patient_id}, room={room}'
                )

            self._ensemble_devices[ensemble_uuid].add(device_handler.epr)
            member_count = len(self._ensemble_devices[ensemble_uuid])

            # Assign the UUID to the worker here, under the aggregator lock.
            # This guarantees that device_handler.ensemble_uuid is set before
            # apply_ensemble_context() is called, even if the SOAP request fails.
            # apply_ensemble_context() will overwrite it again on success --
            # this is idempotent and safe.
            device_handler.ensemble_uuid = ensemble_uuid

        self.logger.debug(
            f'[Aggregator] Ensemble {ensemble_uuid[:8]}... now has '
            f'{member_count} member(s). Sending context to {device_handler.epr[-12:]}...'
        )

        # FHIR fetch -- executed outside self.lock (HTTP round-trip; may be slow).
        # Returns None gracefully if the FHIR server is unreachable.
        fhir_data = self._get_or_fetch_fhir_data(patient_id)

        # Network SOAP call -- executed outside self.lock to avoid holding the
        # aggregator mutex during a potentially slow round-trip to the device.
        success = device_handler.apply_ensemble_context(ensemble_uuid)
        if success:
            self.logger.info(
                f'[Aggregator] EnsembleContext {ensemble_uuid[:8]}... '
                f'successfully applied to {device_handler.epr[-12:]}.'
            )
            # Notify PatientOverview: new/updated ensemble, not yet escalated
            self._notify_overview(ensemble_uuid, patient_id, room or '',
                                  is_escalated=False, risk_score=0.0, is_warning=False)
        else:
            # SOAP failed -- roll back the local assignment so the device
            # is not considered "bound" until the next successful attempt.
            device_handler.ensemble_uuid = None
            with self.lock:
                self._ensemble_devices[ensemble_uuid].discard(device_handler.epr)
            self.logger.warning(
                f'[Aggregator] Failed to apply EnsembleContext to '
                f'{device_handler.epr[-12:]}. Rolled back local binding.'
            )

        # Apply FHIR patient/clinical context to the device (demographics,
        # danger codes, vital measurements). Called regardless of ensemble
        # binding outcome -- FHIR data is independent of SDC ensemble state.
        # apply_fhir_contexts() is implemented on DeviceHandler.
        device_handler.apply_fhir_contexts(fhir_data)

