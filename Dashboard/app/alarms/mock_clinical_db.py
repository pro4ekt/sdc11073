"""
mock_clinical_db.py — JSON Loader / Data Access Object for the IHE-PCD ACM pipeline.

Role (v2)
---------
This module is a DAO (Data Access Object): it reads calibration data from an
external JSON file at import time and exposes typed Python constants.
The data itself lives in  Dashboard/config/clinical_db.json  — the single
source of truth for all device profiles and physiological limits.

To update calibration values: edit  config/clinical_db.json  only.
No Python code needs to change.

Architecture
------------
  config/clinical_db.json          — raw data (JSON)
         │
         ▼  (read once at import, via pathlib / json)
  mock_clinical_db.py              — DAO: parses JSON → typed Python objects
         │
         ▼  (imported by)
  alarmCoordinator.py              — uses MOCK_ROC_LIMITS + MOCK_DEVICE_PROFILES

Exports
-------
  _DeviceReliabilityProfile  — frozen dataclass (sensitivity, false_alarm_rate)
  MOCK_ROC_LIMITS            — dict[str, float]  for Stage 1 HardwareArtifactFilter
  MOCK_DEVICE_PROFILES       — nested dict[…, _DeviceReliabilityProfile]  for Stage 2

NOTE: Provider-side scripts (provider_ensemble.py, correct_provider.py) must NOT
import this module — it contains Consumer-internal calibration data only.
"""

from __future__ import annotations

import json
import logging
import pathlib
from dataclasses import dataclass

_logger = logging.getLogger('sdc.consumer.clinical_db')

# Absolute path: Dashboard/config/clinical_db.json
# __file__  = Dashboard/app/alarms/mock_clinical_db.py
# parents[2]= Dashboard/
_DB_PATH: pathlib.Path = (
    pathlib.Path(__file__).resolve().parents[2] / 'config' / 'clinical_db.json'
)


# ── Device reliability profile (dataclass) ───────────────────────────────────

@dataclass(frozen=True)
class _DeviceReliabilityProfile:
    """
    Bayesian calibration priors for one (manufacturer, model, concept) triple.

    sensitivity      = P(alarm | true clinical event)  — True-Positive rate
    false_alarm_rate = P(alarm | no clinical event)    — False-Positive rate
    LR+              = sensitivity / false_alarm_rate
    """
    sensitivity:      float
    false_alarm_rate: float


# ── JSON load ─────────────────────────────────────────────────────────────────

def _load() -> tuple[dict[str, float], dict[str, dict[str, dict[str, _DeviceReliabilityProfile]]]]:
    """
    Read clinical_db.json, validate keys, and convert raw dicts to typed objects.
    Returns (roc_limits, device_profiles).
    Raises RuntimeError on missing file or malformed data — fail-fast at startup.
    """
    if not _DB_PATH.exists():
        raise RuntimeError(
            f'[ClinicalDB] clinical_db.json not found at {_DB_PATH}. '
            f'Copy Dashboard/config/clinical_db.json into the project.'
        )

    with _DB_PATH.open(encoding='utf-8') as fh:
        raw: dict = json.load(fh)

    # ── roc_limits ───────────────────────────────────────────────────────────
    roc_raw: dict = raw.get('roc_limits', {})
    roc_limits: dict[str, float] = {
        k: float(v)
        for k, v in roc_raw.items()
        if not k.startswith('_')   # skip _comment keys
    }

    # ── device_profiles ──────────────────────────────────────────────────────
    profiles_raw: dict = raw.get('device_profiles', {})
    device_profiles: dict[str, dict[str, dict[str, _DeviceReliabilityProfile]]] = {}

    for mfr, models in profiles_raw.items():
        if mfr.startswith('_'):
            continue
        device_profiles[mfr] = {}
        for mdl, concepts in models.items():
            if mdl.startswith('_'):
                continue
            device_profiles[mfr][mdl] = {}
            for code, priors in concepts.items():
                if code.startswith('_'):
                    continue
                device_profiles[mfr][mdl][code] = _DeviceReliabilityProfile(
                    sensitivity=float(priors['sensitivity']),
                    false_alarm_rate=float(priors['false_alarm_rate']),
                )

    _logger.info(
        f'[ClinicalDB] Loaded {_DB_PATH.name}: '
        f'{len(roc_limits)} RoC limits, '
        f'{sum(len(m) for m in device_profiles.values())} device model(s).'
    )
    return roc_limits, device_profiles


# ── Module-level constants (loaded once at import) ───────────────────────────

MOCK_ROC_LIMITS: dict[str, float]
MOCK_DEVICE_PROFILES: dict[str, dict[str, dict[str, _DeviceReliabilityProfile]]]

MOCK_ROC_LIMITS, MOCK_DEVICE_PROFILES = _load()
