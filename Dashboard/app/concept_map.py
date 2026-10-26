"""
concept_map.py — HL7 FHIR R4 ConceptMap-backed translation of device-native
metric codes (IEEE 11073-10101 "MDC") to LOINC.

Why this exists
---------------
BICEPS describes every metric with a ``CodedValue`` (``pm:Type``).  When the
``CodingSystem`` attribute is omitted the code is, by definition of the
participant model, an IEEE 11073-10101 nomenclature code
(``urn:oid:1.2.840.10004.1.1.1.0.0.1``).  Real devices therefore deliver MDC
codes such as ``147842`` (MDC_ECG_HEART_RATE), while the Dashboard's
calibration database ``clinical_db.json`` is keyed by LOINC (``8867-4``).

``DeviceHandler._build_semantic_map()`` calls :func:`normalize_concept` once per
NumericMetricDescriptor.  The function

* passes LOINC codes through unchanged,
* translates MDC codes via ``config/conceptmap_mdc_to_loinc.json`` (a FHIR R4
  ``ConceptMap`` resource) when the mapping is ``equivalent``, ``equal``,
  ``wider``, ``subsumes``, ``narrower`` or ``specializes``,
* and otherwise returns the device code as-is (fail-open: an unknown or
  ``unmatched`` code never blocks a channel, it merely misses its profile).

The map is parsed lazily, once per process, mirroring ``device_profile_repo``.
"""
from __future__ import annotations

import json
import logging
import pathlib
import threading
from dataclasses import dataclass
from typing import Optional

_logger = logging.getLogger('sdc.consumer.concept_map')

_MAP_PATH: pathlib.Path = (
    pathlib.Path(__file__).resolve().parents[1] / 'config' / 'conceptmap_mdc_to_loinc.json'
)

# Coding-system URIs the Dashboard recognises.
LOINC_URI = 'http://loinc.org'
LOINC_SYSTEMS: frozenset[str] = frozenset({
    LOINC_URI,
    'urn:oid:2.16.840.1.113883.6.1',          # LOINC OID (used by the provider MDIB fixtures)
})
MDC_URI = 'urn:oid:1.2.840.10004.1.1.1.0.0.1'   # IEEE 11073-10101 nomenclature OID
MDC_SYSTEMS: frozenset[str] = frozenset({
    MDC_URI,
    'urn:iso:std:iso:11073:10101',
    'http://terminology.hl7.org/CodeSystem/MDC',   # legacy HL7 alias, not canonical
})

# FHIR R4 ConceptMapEquivalence values that justify using the target code.
_USABLE_EQUIVALENCE: frozenset[str] = frozenset({
    'relatedto', 'equivalent', 'equal', 'wider', 'subsumes', 'narrower', 'specializes',
})


@dataclass(frozen=True)
class Translation:
    """One resolved ConceptMap target."""
    source_code: str
    source_display: str
    target_code: str
    target_display: str
    equivalence: str


@dataclass(frozen=True)
class ConceptResolution:
    """Outcome of :func:`normalize_concept` for one descriptor."""
    concept: str           # code the Dashboard will use as channel key
    action: str            # 'loinc' | 'translated' | 'unmapped' | 'unmatched' | 'no_code'
    translation: Optional[Translation] = None


class ConceptMapRepository:
    """Thread-safe, lazy loader for the MDC → LOINC ConceptMap."""

    def __init__(self, path: pathlib.Path = _MAP_PATH) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._loaded = False
        # (source_system, source_code) → Translation | None  (None = explicit 'unmatched')
        self._index: dict[tuple[str, str], Optional[Translation]] = {}
        self.resource_type: str = ''
        self.version: str = ''

    # ── loading ──────────────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            try:
                with open(self._path, 'r', encoding='utf-8') as fh:
                    raw = json.load(fh)
            except FileNotFoundError:
                _logger.warning(f'[ConceptMap] {self._path} not found — MDC codes will not be translated.')
                raw = {}
            except (OSError, json.JSONDecodeError) as exc:
                _logger.error(f'[ConceptMap] cannot read {self._path}: {exc!r} — translation disabled.')
                raw = {}
            self._index = self._build_index(raw)
            self.resource_type = str(raw.get('resourceType', ''))
            self.version = str(raw.get('version', ''))
            self._loaded = True
            _logger.info(
                f'[ConceptMap] loaded {self.resource_type or "nothing"} v{self.version or "?"} '
                f'with {sum(1 for v in self._index.values() if v is not None)} usable element(s).'
            )

    @staticmethod
    def _build_index(raw: dict) -> dict[tuple[str, str], Optional[Translation]]:
        index: dict[tuple[str, str], Optional[Translation]] = {}
        if raw.get('resourceType') != 'ConceptMap':
            return index
        default_source = str(raw.get('sourceUri', '') or '')
        for group in raw.get('group', []) or []:
            source_system = str(group.get('source') or default_source)
            for element in group.get('element', []) or []:
                code = element.get('code')
                if not code:
                    continue
                chosen: Optional[Translation] = None
                for target in element.get('target', []) or []:
                    eq = str(target.get('equivalence', '')).lower()
                    if eq in _USABLE_EQUIVALENCE and target.get('code'):
                        chosen = Translation(
                            source_code=str(code),
                            source_display=str(element.get('display', '') or ''),
                            target_code=str(target['code']),
                            target_display=str(target.get('display', '') or ''),
                            equivalence=eq,
                        )
                        break
                index[(source_system, str(code))] = chosen
        return index

    # ── queries ──────────────────────────────────────────────────────────────

    def translate(self, code: str, system: Optional[str]) -> Optional[Translation]:
        """Return the LOINC translation for (system, code) or None."""
        self._ensure_loaded()
        sys_uri = system or MDC_URI          # BICEPS default when CodingSystem is absent
        hit = self._index.get((sys_uri, str(code)))
        if hit is None and sys_uri in MDC_SYSTEMS:
            # any recognised MDC alias resolves against the canonical OID group
            hit = self._index.get((MDC_URI, str(code)))
        return hit

    def is_explicitly_unmatched(self, code: str, system: Optional[str]) -> bool:
        self._ensure_loaded()
        sys_uri = system or MDC_URI
        for key in ((sys_uri, str(code)), (MDC_URI, str(code))):
            if key in self._index and self._index[key] is None:
                return True
        return False


_repo_singleton: Optional[ConceptMapRepository] = None
_singleton_lock = threading.Lock()


def get_concept_map() -> ConceptMapRepository:
    global _repo_singleton
    if _repo_singleton is None:
        with _singleton_lock:
            if _repo_singleton is None:
                _repo_singleton = ConceptMapRepository()
    return _repo_singleton


def normalize_concept(
    code: Optional[str],
    coding_system: Optional[str],
    fallback: str,
    repo: Optional[ConceptMapRepository] = None,
) -> ConceptResolution:
    """
    Decide which concept key the Dashboard uses for one NumericMetricDescriptor.

    code           Type/@Code as delivered by the device (may be None)
    coding_system  Type/@CodingSystem as delivered (None ⇒ IEEE 11073-10101 per BICEPS)
    fallback       value to use when there is no code at all (descriptor handle)
    """
    if not code:
        return ConceptResolution(concept=fallback, action='no_code')
    code = str(code)
    if coding_system in LOINC_SYSTEMS:
        return ConceptResolution(concept=code, action='loinc')
    r = repo or get_concept_map()
    tr = r.translate(code, coding_system)
    if tr is not None:
        return ConceptResolution(concept=tr.target_code, action='translated', translation=tr)
    if r.is_explicitly_unmatched(code, coding_system):
        return ConceptResolution(concept=code, action='unmatched')
    return ConceptResolution(concept=code, action='unmapped')
