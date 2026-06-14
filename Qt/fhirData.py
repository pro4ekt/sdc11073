from fhirpy import SyncFHIRClient


class FHIRPatientData:
    def __init__(self, server_url: str = 'http://hapi.fhir.org/baseR4'):
        self._client = SyncFHIRClient(server_url)
        self._patient = None
        self._conditions = []
        self._observations = []

    def fetch(self, patient_id: str) -> None:
        """Загружает пациента и связанные ресурсы с FHIR-сервера."""
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

    # ── Геттеры пациента ──────────────────────────────────────────────────

    def get_patient(self) -> dict | None:
        """Возвращает сырой словарь ресурса Patient."""
        return self._patient

    def get_name(self) -> str:
        if not self._patient:
            return 'Не указано'
        names = self._patient.get('name', [])
        if not names:
            return 'Не указано'
        n = names[0]
        return n.get('text') or \
               ' '.join(n.get('given', []) + [n.get('family', '')]).strip() or \
               'Не указано'

    def get_gender(self) -> str:
        return self._patient.get('gender', 'Не указан') if self._patient else 'Не указан'

    def get_birth_date(self) -> str:
        return self._patient.get('birthDate', 'Не указана') if self._patient else 'Не указана'

    def get_address(self) -> str:
        if not self._patient:
            return 'Не указан'
        addresses = self._patient.get('address', [])
        if not addresses:
            return 'Не указан'
        addr = addresses[0]
        parts = addr.get('line', []) + \
                [addr.get('city', ''), addr.get('state', ''), addr.get('country', '')]
        return ', '.join(p for p in parts if p) or 'Не указан'

    def get_phone(self) -> str:
        if not self._patient:
            return 'Не указан'
        for t in self._patient.get('telecom', []):
            if t.get('system') == 'phone':
                return t.get('value', 'Не указан')
        return 'Не указан'

    def is_active(self) -> bool | None:
        return self._patient.get('active') if self._patient else None

    # ── Геттеры диагнозов ─────────────────────────────────────────────────

    def get_conditions(self) -> list[dict]:
        """Возвращает список сырых ресурсов Condition."""
        return self._conditions

    def get_condition_names(self) -> list[str]:
        """Возвращает список читаемых названий диагнозов."""
        result = []
        for c in self._conditions:
            code_info = c.get('code', {})
            display = code_info.get('text') or \
                      (code_info.get('coding', [{}])[0].get('display', 'Без описания'))
            result.append(display)
        return result

    # ── Геттеры наблюдений ────────────────────────────────────────────────

    def get_observations(self) -> list[dict]:
        """Возвращает список сырых ресурсов Observation."""
        return self._observations

    def get_observation_summaries(self) -> list[dict]:
        """Возвращает список наблюдений в виде {'name': ..., 'value': ...}."""
        result = []
        for o in self._observations:
            code_info = o.get('code', {})
            name = code_info.get('text') or \
                   (code_info.get('coding', [{}])[0].get('display', 'Без описания'))
            value = o.get('valueQuantity', {})
            val_str = f"{value.get('value', '')} {value.get('unit', '')}".strip()
            result.append({'name': name, 'value': val_str or 'Не указано'})
        return result

    # ── Принт ─────────────────────────────────────────────────────────────

    def print_summary(self) -> None:
        if not self._patient:
            print("Пациент не загружен.")
            return

        print("=" * 50)
        print("ДАННЫЕ ПАЦИЕНТА")
        print("=" * 50)
        print(f"Имя:         {self.get_name()}")
        print(f"Пол:         {self.get_gender()}")
        print(f"Дата рожд.:  {self.get_birth_date()}")
        print(f"Адрес:       {self.get_address()}")
        print(f"Телефон:     {self.get_phone()}")
        print(f"Активен:     {self.is_active()}")
        print("=" * 50)
        print(f"Диагнозов (Condition):                 {len(self._conditions)}")
        print(f"Физиологических записей (Observation): {len(self._observations)}")

        names = self.get_condition_names()
        if names:
            print("\nПервые диагнозы:")
            for name in names[:3]:
                print(f"  - {name}")

        summaries = self.get_observation_summaries()
        if summaries:
            print("\nПервые наблюдения:")
            for s in summaries[:3]:
                print(f"  - {s['name']}: {s['value']}")
