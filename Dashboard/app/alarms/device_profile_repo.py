"""
device_profile_repo.py — Lazy-loading Repository for device calibration profiles.

Problem solved
--------------
The previous architecture (mock_clinical_db.py) read the entire clinical_db.json
at Python *import time*, loading ALL device profiles into memory before a single
device connected.  In a hospital with thousands of registered devices this is
wasteful and does not scale.

New contract
------------
* The JSON file is read from disk on the **first query**, not at import time.
* Results are cached per (manufacturer, model, concept) key — O(1) after first hit.
* A module-level singleton (get_repository()) is shared across all DeviceHandler
  instances → the JSON file is parsed **at most once per process**.
* DeviceHandler calls the repo immediately after _build_semantic_map() and caches
  the fetched profiles in its own _device_calibration dict.  The Aggregator and
  Pipeline filters receive only pre-built DTO objects — they never touch this module.

Usage
-----
    from app.alarms.device_profile_repo import get_repository, DeviceReliabilityProfile

    repo    = get_repository()
    profile = repo.get_profile(manufacturer, model, metric_code)   # DeviceReliabilityProfile | None
"""
from __future__ import annotations

import json
import logging
import pathlib
import threading
from dataclasses import dataclass
from typing import Optional

_logger = logging.getLogger('sdc.consumer.device_profile_repo')

# Resolve path relative to this file:
#   __file__        = Dashboard/app/alarms/device_profile_repo.py
#   parents[2]      = Dashboard/
_DB_PATH: pathlib.Path = (
    pathlib.Path(__file__).resolve().parents[2] / 'config' / 'clinical_db.json'
)


# ── DTO ───────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DeviceReliabilityProfile:
    """
    Bayesian calibration priors for one (manufacturer, model, metric_code) triple.

    true_positive_rate  = P(alarm | true clinical event)  — True-Positive Rate  (TPR)
    false_positive_rate = P(alarm | no clinical event)    — False-Positive Rate (FPR)
    LR+                 = true_positive_rate / false_positive_rate

    Field names mirror the math-core terminology (TPR/FPR) so the reliability
    weight w_j = ln(TPR_j / FPR_j) reads directly off the profile.  The JSON on
    disk still uses the legacy keys ``sensitivity`` / ``false_alarm_rate``; the
    repository translates them into these fields at read time.
    """
    true_positive_rate:  float
    false_positive_rate: float


# ── Repository ────────────────────────────────────────────────────────────────

class DeviceProfileRepository:
    """
    Thread-safe, lazy-loading DAO for clinical_db.json.

    The JSON file is parsed once on the first query (double-checked locking).
    Subsequent lookups hit an in-memory dict cache — no I/O after the first call.
    """

    # Fail-open defaults — used when clinical_db.json is missing a section entirely.
    _DEFAULT_BASE_PROB: float = 0.005
    _DEFAULT_PRIORITY_MAP: dict[str, int] = {'None': 0, 'Lo': 1, 'Me': 2, 'Hi': 3}
    _DEFAULT_HORIZON_T: float = 10.0
    _DEFAULT_ALPHA: float = 0.7

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        # Full in-memory representation of clinical_db.json — the ENTIRE file
        # (device_profiles, base_prob_P0, odds_ratios, priority_map, filter_params),
        # NOT just device profiles.  Tri-state:
        #   None  → not yet loaded (lazy load pending);
        #   {}    → loaded-but-empty fail-open fallback (file missing or corrupt);
        #   dict  → parsed JSON.  Each getter reads its own section from this dict.
        self._raw_clinical_db: Optional[dict] = None
        self._sensor_profile_cache: dict[tuple[str, str, str], Optional[DeviceReliabilityProfile]] = {}
        # Caches for the two-axis math-core parameters (parsed once, then reused).
        self._odds_cache: Optional[dict[str, float]] = None
        self._priority_map: Optional[dict[str, int]] = None
        self._filter_params: Optional[tuple[float, float]] = None

    # ── private ───────────────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        """Load JSON from disk on the first call (double-checked locking)."""
        if self._raw_clinical_db is not None:
            return
        with self._lock:
            if self._raw_clinical_db is not None:
                return
            if not _DB_PATH.exists():
                _logger.warning(
                    f'[DeviceProfileRepo] {_DB_PATH.name} not found at {_DB_PATH}. '
                    f'All profile lookups will return None (fail-open / LR+=1.0).'
                )
                self._raw_clinical_db = {}
                return
            try:
                with _DB_PATH.open(encoding='utf-8') as fh:
                    self._raw_clinical_db = json.load(fh)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                # The file exists but is corrupt (bad JSON syntax or encoding).
                # Raising here would kill the caller's thread; instead we log a
                # CRITICAL alert and degrade to the same fail-open contract used
                # for a missing file (self._raw_clinical_db = {} → every lookup
                # returns the conservative default, never suppresses an alarm).
                # It is set to {} (not None) so the double-checked load is NOT
                # retried on every subsequent query.
                _logger.critical(
                    f'[DeviceProfileRepo] {_DB_PATH.name} is present but could not '
                    f'be parsed ({type(exc).__name__}: {exc}). Falling back to an '
                    f'EMPTY calibration DB (fail-open / LR+=1.0 for all devices).'
                )
                self._raw_clinical_db = {}
                return
            _logger.info(
                f'[DeviceProfileRepo] Lazy-loaded {_DB_PATH.name} '
                f'({_DB_PATH.stat().st_size:,} bytes) on first profile query.'
            )

    # ── public API ────────────────────────────────────────────────────────────

    def get_profile(
        self,
        manufacturer: str,
        model: str,
        metric_code: str,
    ) -> Optional[DeviceReliabilityProfile]:
        """
        Point-query: returns the calibration profile for (manufacturer, model, metric_code).
        Returns None if no matching entry exists in the database.
        Result is memoised after the first lookup.
        """
        key = (manufacturer, model, metric_code)

        # Fast path: already in cache (no lock needed for reads after initial write)
        if key in self._sensor_profile_cache:
            return self._sensor_profile_cache[key]

        self._ensure_loaded()

        with self._lock:
            if key in self._sensor_profile_cache:   # re-check after acquiring lock
                return self._sensor_profile_cache[key]

            raw_profile_data = (
                (self._raw_clinical_db or {})
                .get('device_profiles', {})
                .get(manufacturer, {})
                .get(model, {})
                .get(metric_code)
            )
            profile: Optional[DeviceReliabilityProfile]
            if raw_profile_data and not metric_code.startswith('_'):
                # JSON on disk still uses the legacy keys sensitivity/false_alarm_rate;
                # translate them into the TPR/FPR math-core field names.
                profile = DeviceReliabilityProfile(
                    true_positive_rate=float(raw_profile_data['sensitivity']),
                    false_positive_rate=float(raw_profile_data['false_alarm_rate']),
                )
            else:
                profile = None

            self._sensor_profile_cache[key] = profile
            _logger.debug(
                f'[DeviceProfileRepo] get_profile({manufacturer!r}, {model!r}, {metric_code!r}) '
                f'→ {"FOUND" if profile else "MISS → fail-safe (LR+=1.0)"}'
            )
            return profile


    # ── Two-axis math-core parameters ─────────────────────────────────────────

    def get_base_prob(self) -> float:
        """Return the baseline crisis probability P_0 (default 0.005 if unspecified).

        Feeds ClinicalContext, which converts P_0 → baseline odds O_0 = P_0/(1-P_0)
        before Context_Log_Odds = ln(O_0) + Σ R_d·ln(OR_d).
        Fail-open: a missing key returns the conservative default, never raises.
        """
        self._ensure_loaded()
        raw = (self._raw_clinical_db or {}).get('base_prob_P0', self._DEFAULT_BASE_PROB)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return self._DEFAULT_BASE_PROB

    def get_odds_ratios(self) -> dict[str, float]:
        """Return the full {diagnosis_code → OR_d} map (``_``-comment keys skipped).

        Memoised after the first call.  Missing section → empty map (all codes
        neutral, OR = 1.0 → ln 0), so an absent DataSheet never sensitises/suppresses.
        """
        if self._odds_cache is not None:
            return self._odds_cache
        self._ensure_loaded()
        with self._lock:
            if self._odds_cache is not None:
                return self._odds_cache
            section = (self._raw_clinical_db or {}).get('odds_ratios', {}) or {}
            parsed: dict[str, float] = {}
            for code, value in section.items():
                if code.startswith('_'):
                    continue
                try:
                    parsed[code] = float(value)
                except (TypeError, ValueError):
                    continue
            self._odds_cache = parsed
            return parsed

    def get_odds_ratio(self, code: str) -> float:
        """Return OR_d for one diagnosis code; unknown code → 1.0 (neutral)."""
        return self.get_odds_ratios().get(code, 1.0)

    def get_priority_map(self) -> dict[str, int]:
        """Return the BICEPS-priority → P_j map (default None/Lo/Me/Hi = 0/1/2/3).

        Memoised.  Missing/invalid section falls back to the canonical default map.
        """
        if self._priority_map is not None:
            return self._priority_map
        self._ensure_loaded()
        with self._lock:
            if self._priority_map is not None:
                return self._priority_map
            section = (self._raw_clinical_db or {}).get('priority_map', {}) or {}
            parsed_pm: dict[str, int] = {}
            for label, value in section.items():
                if label.startswith('_'):
                    continue
                try:
                    parsed_pm[label] = int(value)
                except (TypeError, ValueError):
                    continue
            result_pm = parsed_pm or dict(self._DEFAULT_PRIORITY_MAP)
            self._priority_map = result_pm
            return result_pm

    def get_priority(self, biceps_priority: str) -> int:
        """Map a BICEPS AlertCondition.Priority string to P_j; unknown → 0 (None)."""
        return self.get_priority_map().get(biceps_priority, 0)

    def get_filter_params(self) -> tuple[float, float]:
        """Return (horizon_T, alpha) for the math core (defaults 10.0 s, 0.7).

        Memoised.  Any missing/invalid value degrades to its individual default.
        """
        if self._filter_params is not None:
            return self._filter_params
        self._ensure_loaded()
        with self._lock:
            if self._filter_params is not None:
                return self._filter_params
            section = (self._raw_clinical_db or {}).get('filter_params', {}) or {}
            try:
                horizon_t = float(section.get('horizon_T', self._DEFAULT_HORIZON_T))
            except (TypeError, ValueError):
                horizon_t = self._DEFAULT_HORIZON_T
            try:
                alpha = float(section.get('alpha', self._DEFAULT_ALPHA))
            except (TypeError, ValueError):
                alpha = self._DEFAULT_ALPHA
            result_fp = (horizon_t, alpha)
            self._filter_params = result_fp
            return result_fp


# ── Process-wide singleton ────────────────────────────────────────────────────

_singleton_lock: threading.Lock = threading.Lock()
_singleton: Optional[DeviceProfileRepository] = None


def get_repository() -> DeviceProfileRepository:
    """
    Return the process-wide singleton DeviceProfileRepository.
    Thread-safe via double-checked locking.
    The JSON file is NOT read here — it is deferred until the first actual query.
    """
    global _singleton
    if _singleton is not None:
        return _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = DeviceProfileRepository()
    assert _singleton is not None
    return _singleton


