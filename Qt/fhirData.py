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