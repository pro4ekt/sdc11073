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
    profile = repo.get_profile(manufacturer, model, concept)   # DeviceReliabilityProfile | None
    limit   = repo.get_roc_limit(concept)                      # float | None
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
    Bayesian calibration priors for one (manufacturer, model, concept) triple.

    sensitivity      = P(alarm | true clinical event)  — True-Positive Rate  (TPR)
    false_alarm_rate = P(alarm | no clinical event)    — False-Positive Rate (FPR)
    LR+              = sensitivity / false_alarm_rate
    """
    sensitivity:      float
    false_alarm_rate: float


# ── Repository ────────────────────────────────────────────────────────────────

class DeviceProfileRepository:
    """
    Thread-safe, lazy-loading DAO for clinical_db.json.

    The JSON file is parsed once on the first query (double-checked locking).
    Subsequent lookups hit an in-memory dict cache — no I/O after the first call.
    """

    def __init__(self) -> None:
        self._lock  = threading.Lock()
        self._raw:          Optional[dict]                                           = None
        self._profile_cache: dict[tuple[str, str, str], Optional[DeviceReliabilityProfile]] = {}
        self._roc_cache:     dict[str, Optional[float]]                              = {}

    # ── private ───────────────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        """Load JSON from disk on the first call (double-checked locking)."""
        if self._raw is not None:
            return
        with self._lock:
            if self._raw is not None:
                return
            if not _DB_PATH.exists():
                _logger.warning(
                    f'[DeviceProfileRepo] {_DB_PATH.name} not found at {_DB_PATH}. '
                    f'All profile lookups will return None (fail-open / LR+=1.0).'
                )
                self._raw = {}
                return
            with _DB_PATH.open(encoding='utf-8') as fh:
                self._raw = json.load(fh)
            _logger.info(
                f'[DeviceProfileRepo] Lazy-loaded {_DB_PATH.name} '
                f'({_DB_PATH.stat().st_size:,} bytes) on first profile query.'
            )

    # ── public API ────────────────────────────────────────────────────────────

    def get_profile(
        self,
        manufacturer: str,
        model: str,
        concept: str,
    ) -> Optional[DeviceReliabilityProfile]:
        """
        Point-query: returns the calibration profile for (manufacturer, model, concept).
        Returns None if no matching entry exists in the database.
        Result is memoised after the first lookup.
        """
        key = (manufacturer, model, concept)

        # Fast path: already in cache (no lock needed for reads after initial write)
        if key in self._profile_cache:
            return self._profile_cache[key]

        self._ensure_loaded()

        with self._lock:
            if key in self._profile_cache:       # re-check after acquiring lock
                return self._profile_cache[key]

            raw = (
                (self._raw or {})
                .get('device_profiles', {})
                .get(manufacturer, {})
                .get(model, {})
                .get(concept)
            )
            profile: Optional[DeviceReliabilityProfile]
            if raw and not concept.startswith('_'):
                profile = DeviceReliabilityProfile(
                    sensitivity=float(raw['sensitivity']),
                    false_alarm_rate=float(raw['false_alarm_rate']),
                )
            else:
                profile = None

            self._profile_cache[key] = profile
            _logger.debug(
                f'[DeviceProfileRepo] get_profile({manufacturer!r}, {model!r}, {concept!r}) '
                f'→ {"FOUND" if profile else "MISS → fail-safe (LR+=1.0)"}'
            )
            return profile

    def get_roc_limit(self, concept: str) -> Optional[float]:
        """
        Point-query: returns the physiological dx/dt limit (units/s) for *concept*.
        Returns None if the concept is not registered — caller should treat as fail-open.
        Result is memoised after the first lookup.
        """
        if concept in self._roc_cache:
            return self._roc_cache[concept]

        self._ensure_loaded()

        with self._lock:
            if concept in self._roc_cache:
                return self._roc_cache[concept]

            raw = (self._raw or {}).get('roc_limits', {}).get(concept)
            limit: Optional[float] = (
                float(raw)
                if raw is not None and not concept.startswith('_')
                else None
            )
            self._roc_cache[concept] = limit
            _logger.debug(
                f'[DeviceProfileRepo] get_roc_limit({concept!r}) '
                f'→ {limit if limit is not None else "MISS → fail-open"}'
            )
            return limit


# ── Process-wide singleton ────────────────────────────────────────────────────

_singleton_lock = threading.Lock()
_singleton:      Optional[DeviceProfileRepository] = None


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


