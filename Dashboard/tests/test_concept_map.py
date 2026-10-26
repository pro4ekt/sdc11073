"""
tests/test_concept_map.py
=========================
FHIR R4 ConceptMap (MDC → LOINC) and the consumer-side concept normalisation
used by DeviceHandler._build_semantic_map().

    C1  the shipped ConceptMap is a structurally valid R4 ConceptMap
        (resourceType, status, group/source/target, element/code, target/equivalence)
    C2  LOINC codes pass through unchanged, for both the URI and the OID system
    C3  MDC codes are translated: explicit OID, ISO URN alias, and the BICEPS
        default (CodingSystem omitted ⇒ IEEE 11073-10101)
    C4  'unmatched' elements are NOT translated and are reported as such
    C5  unknown codes are kept as-is (fail-open) and reported as 'unmapped'
    C6  a descriptor without any code falls back to its handle
    C7  every translated target in the map is a LOINC code with a valid mod-10 check digit
    C8  a missing map file disables translation without raising

Run with:  python -m unittest Dashboard.tests.test_concept_map -v
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MODULE_PATH = _PROJECT_ROOT / 'Dashboard' / 'app' / 'concept_map.py'
_MAP_PATH = _PROJECT_ROOT / 'Dashboard' / 'config' / 'conceptmap_mdc_to_loinc.json'


def _load_concept_map_module():
    spec = importlib.util.spec_from_file_location('concept_map_under_test', _MODULE_PATH)
    module = importlib.util.module_from_spec(spec)          # type: ignore[arg-type]
    sys.modules['concept_map_under_test'] = module
    spec.loader.exec_module(module)                          # type: ignore[union-attr]
    return module


cm = _load_concept_map_module()


def _loinc_check_digit_ok(code: str) -> bool:
    """LOINC uses the mod-10 (Luhn) algorithm over the digits left of the hyphen."""
    body, _, check = code.partition('-')
    if not body.isdigit() or len(check) != 1 or not check.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10 == int(check)


class ConceptMapStructureTests(unittest.TestCase):
    def setUp(self) -> None:
        with open(_MAP_PATH, encoding='utf-8') as fh:
            self.raw = json.load(fh)

    def test_c1_is_r4_conceptmap(self) -> None:
        self.assertEqual(self.raw['resourceType'], 'ConceptMap')
        self.assertIn(self.raw['status'], {'draft', 'active', 'retired', 'unknown'})
        self.assertEqual(self.raw['targetUri'], 'http://loinc.org')
        self.assertTrue(self.raw['group'])
        allowed_eq = {
            'relatedto', 'equivalent', 'equal', 'wider', 'subsumes', 'narrower',
            'specializes', 'inexact', 'unmatched', 'disjoint',
        }
        for group in self.raw['group']:
            self.assertIn('source', group)
            self.assertIn('target', group)
            self.assertTrue(group['element'])
            for el in group['element']:
                self.assertTrue(el.get('code'))
                self.assertTrue(el.get('target'))
                for tgt in el['target']:
                    self.assertIn(tgt['equivalence'], allowed_eq)
                    if tgt['equivalence'] in {'unmatched', 'disjoint'}:
                        self.assertNotIn('code', tgt, 'unmatched target must not carry a code')
                    else:
                        self.assertTrue(tgt.get('code'))

    def test_c7_targets_are_loinc_with_valid_check_digit(self) -> None:
        for group in self.raw['group']:
            for el in group['element']:
                for tgt in el['target']:
                    if 'code' in tgt:
                        self.assertTrue(
                            _loinc_check_digit_ok(tgt['code']),
                            f"{el['display']} → {tgt['code']} fails LOINC mod-10",
                        )


class NormalizeConceptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = cm.ConceptMapRepository(_MAP_PATH)

    def test_c2_loinc_passthrough(self) -> None:
        for system in ('http://loinc.org', 'urn:oid:2.16.840.1.113883.6.1'):
            r = cm.normalize_concept('8867-4', system, fallback='h', repo=self.repo)
            self.assertEqual((r.concept, r.action), ('8867-4', 'loinc'))

    def test_c3_mdc_translated(self) -> None:
        cases = {
            '147842': '8867-4',    # MDC_ECG_HEART_RATE
            '150456': '59408-5',   # MDC_PULS_OXIM_SAT_O2
            '150037': '8478-0',    # MDC_PRESS_BLD_ART_ABP_MEAN
            '152616': '76008-2',   # MDC_VOL_MINUTE_AWAY
        }
        for system in (cm.MDC_URI, 'urn:iso:std:iso:11073:10101', None):
            for mdc, loinc in cases.items():
                r = cm.normalize_concept(mdc, system, fallback='h', repo=self.repo)
                self.assertEqual(r.action, 'translated', f'{mdc} under {system!r}')
                self.assertEqual(r.concept, loinc)
                self.assertIsNotNone(r.translation)
                self.assertEqual(r.translation.source_code, mdc)

    def test_c3_wider_equivalence_is_usable(self) -> None:
        r = cm.normalize_concept('150033', None, fallback='h', repo=self.repo)
        self.assertEqual(r.concept, '8480-6')
        self.assertEqual(r.translation.equivalence, 'wider')

    def test_c4_unmatched_kept_as_is(self) -> None:
        r = cm.normalize_concept('MDC_PRESS_AWAY_INSP_PEAK', None, fallback='h', repo=self.repo)
        self.assertEqual((r.concept, r.action), ('MDC_PRESS_AWAY_INSP_PEAK', 'unmatched'))

    def test_c5_unknown_code_fail_open(self) -> None:
        r = cm.normalize_concept('999999', None, fallback='h', repo=self.repo)
        self.assertEqual((r.concept, r.action), ('999999', 'unmapped'))
        r = cm.normalize_concept('ABC', 'http://example.org/private', fallback='h', repo=self.repo)
        self.assertEqual((r.concept, r.action), ('ABC', 'unmapped'))

    def test_c6_no_code_uses_handle(self) -> None:
        r = cm.normalize_concept(None, None, fallback='ecg_metric_hr', repo=self.repo)
        self.assertEqual((r.concept, r.action), ('ecg_metric_hr', 'no_code'))

    def test_c8_missing_file_disables_translation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = cm.ConceptMapRepository(pathlib.Path(tmp) / 'absent.json')
            r = cm.normalize_concept('147842', None, fallback='h', repo=repo)
            self.assertEqual((r.concept, r.action), ('147842', 'unmapped'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
