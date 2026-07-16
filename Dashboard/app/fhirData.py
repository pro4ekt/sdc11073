from fhirpy import SyncFHIRClient
from typing import Optional, List, Dict, Any


class FHIRPatientData:
    """
    Изолированный клиент для интеграции HL7 FHIR.
    Выполняет извлечение и семантическую нормализацию данных пациента
    для последующей трансляции в структуры IEEE 11073 SDC.
    """

    def __init__(self, server_url: str = 'http://hapi.fhir.org/baseR4'):
        self._client = SyncFHIRClient(server_url)
        self._patient: Optional[Dict[str, Any]] = None
        self._conditions: List[Dict[str, Any]] = []
        self._observations: List[Dict[str, Any]] = []

    def fetch(self, patient_id: str) -> None:
        """
        Выполняет атомарный запрос (Single Bundle) к FHIR-серверу.
        """
        bundle = self._client.resources('Patient') \
            .search(_id=patient_id) \
            .revinclude('Condition', 'patient') \
            .revinclude('Observation', 'patient') \
            .fetch_raw()

        self._patient = None
        self._conditions = []
        self._observations = []

        for entry in bundle.get('entry', []):
            resource = entry.get('resource', {})
            res_type = resource.get('resourceType')

            if res_type == 'Patient':
                self._patient = resource
            elif res_type == 'Condition':
                self._conditions.append(resource)
            elif res_type == 'Observation':
                self._observations.append(resource)

    # ── Patient Base Data ───────────────────────────────────────────────────

    def get_patient_id(self) -> str:
        return self._patient.get('id', '') if self._patient else ''

    def get_name(self) -> str:
        if not self._patient:
            return ''
        names = self._patient.get('name', [])
        if not names:
            return ''
        n = names[0]
        return n.get('text') or \
            ' '.join(n.get('given', []) + [n.get('family', '')]).strip()

    # ── Semantic Extraction (SDC Alignment) ───────────────────────────────

    def get_danger_codes(self) -> List[Dict[str, str]]:
        """
        Возвращает стандартизированные коды заболеваний (DangerCode)
        для интеграции в BICEPS WorkflowContextState.
        """
        danger_codes = []
        for condition in self._conditions:
            codings = condition.get('code', {}).get('coding', [])

            for coding in codings:
                code = coding.get('code')
                system = coding.get('system')
                display = coding.get('display', '')

                # BICEPS CodedValue требует обязательного наличия Code и System
                if code and system:
                    danger_codes.append({
                        'code': code,
                        'system': system,
                        'display': display
                    })
                    break  # Берем первый валидный код (например, SNOMED CT) для данного диагноза
        return danger_codes

    def get_clinical_focus(self) -> List[Dict[str, Any]]:
        """
        Extracts clinical monitoring focus rules from FHIR Condition Extensions.

        For each Condition that carries the SDC Orchestrator extension, the method
        determines which vital-sign metric codes (LOINC / IEEE 11073 MDC) are
        considered critical for that disease, and which alert concept codes should
        trigger a priority escalation.

        Extension architecture
        ──────────────────────
        Main URL:
          http://sdc-orchestrator.local/fhir/StructureDefinition/clinical-monitoring-focus

        Sub-extensions (comma-separated valueString lists):
          • criticalSensorConcepts  — LOINC / MDC metric codes
                                      e.g. "8310-5,MDC_PULS_OXIM_SAT_O2"
          • priorityAlertConcepts   — MDC alert concept codes
                                      e.g. "MDC_ALERT_TEMP_HIGH"

        Coding system preference (per FHIR spec):
          SNOMED CT > ICD-10/11 > other.  Only the first code per preferred system
          is taken for the danger_code key.

        Returns
        ───────
        List of dicts compatible with the rules.json ``clinical_focus`` schema:
        [
          {
            "danger_code":              str,   # e.g. "44054006"
            "disease_description":      str,   # display label + "[FHIR Extension]"
            "critical_sensor_concepts": list,  # metric/sensor codes
            "priority_alert_concepts":  list,  # alert concept codes
            "source":                   "fhir",
            "_system":                  str,   # original coding system URI
          },
          ...
        ]
        Returns an empty list when no conditions carry the extension.
        """
        FOCUS_EXT_URL: str = (
            'http://sdc-orchestrator.local/fhir/StructureDefinition/'
            'clinical-monitoring-focus'
        )

        # Coding-system preference order (higher index = higher priority).
        # Conditions may carry both SNOMED and ICD codes; we prefer SNOMED CT
        # because it is the standard for semantic interoperability with BICEPS.
        SYSTEM_PREFERENCE: List[str] = [
            'http://hl7.org/fhir/sid/icd-10',
            'http://hl7.org/fhir/sid/icd-10-cm',
            'http://snomed.info/sct',   # preferred
        ]

        focus_rules: List[Dict[str, Any]] = []

        for condition in self._conditions:
            # ── Step 1: look for our custom extension ──────────────────────────
            extensions: List[Dict[str, Any]] = condition.get('extension', [])
            focus_ext: Optional[Dict[str, Any]] = next(
                (e for e in extensions if e.get('url') == FOCUS_EXT_URL),
                None,
            )
            if focus_ext is None:
                continue  # this condition has no monitoring focus — skip

            # ── Step 2: select preferred coding for the disease ────────────────
            # FHIR coding is always an array; verify .system for each entry.
            codings: List[Dict[str, Any]] = condition.get('code', {}).get('coding', [])
            if not codings:
                continue  # malformed Condition — no codes at all

            selected_coding: Optional[Dict[str, Any]] = None
            best_rank: int = -1
            for coding in codings:
                system: str = coding.get('system', '') or ''
                try:
                    rank: int = SYSTEM_PREFERENCE.index(system)
                except ValueError:
                    rank = -1
                # Update selection if this coding has a higher-priority system,
                # or if we have no selection yet (first-encountered fallback).
                if rank > best_rank or selected_coding is None:
                    best_rank       = rank
                    selected_coding = coding

            danger_code: str    = selected_coding.get('code', '') or ''
            coding_system: str  = selected_coding.get('system', '') or ''
            display: str        = selected_coding.get('display', '') or danger_code
            if not danger_code:
                continue  # no usable code — skip

            # ── Step 3: parse sub-extensions ──────────────────────────────────
            sensor_concepts: List[str] = []
            alert_concepts:  List[str] = []

            for sub in focus_ext.get('extension', []):
                sub_url: str  = sub.get('url', '') or ''
                val: str      = sub.get('valueString', '') or ''
                codes: List[str] = [c.strip() for c in val.split(',') if c.strip()]

                if sub_url == 'criticalSensorConcepts':
                    sensor_concepts = codes
                elif sub_url == 'priorityAlertConcepts':
                    alert_concepts  = codes

            if not sensor_concepts and not alert_concepts:
                continue  # extension present but completely empty — skip

            focus_rules.append({
                'danger_code':              danger_code,
                'disease_description':      f'{display} [FHIR Extension]',
                'critical_sensor_concepts': sensor_concepts,
                'priority_alert_concepts':  alert_concepts,
                'source':                   'fhir',
                '_system':                  coding_system,
            })

        return focus_rules

    def get_vital_measurements(self) -> Dict[str, Dict[str, str]]:
        """
        Возвращает антропометрические данные пациента.
        Поиск осуществляется строго по международным кодам LOINC.
        """
        # LOINC терминология для базовых параметров
        LOINC_WEIGHT = '29463-7'  # Body weight
        LOINC_HEIGHT = '8302-2'  # Body height

        measurements = {
            'weight': {'value': None, 'unit': 'kg'},
            'height': {'value': None, 'unit': 'cm'}
        }

        for obs in self._observations:
            codings = obs.get('code', {}).get('coding', [])

            is_weight = any(c.get('code') == LOINC_WEIGHT for c in codings)
            is_height = any(c.get('code') == LOINC_HEIGHT for c in codings)

            if is_weight or is_height:
                value_quantity = obs.get('valueQuantity', {})
                val = value_quantity.get('value')
                unit = value_quantity.get('unit', '')

                if val is not None:
                    if is_weight:
                        measurements['weight'] = {'value': str(val), 'unit': unit}
                    elif is_height:
                        measurements['height'] = {'value': str(val), 'unit': unit}

        return measurements