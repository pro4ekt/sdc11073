from fhirpy import SyncFHIRClient


class FHIRPatientData:
    def __init__(self, server_url: str = 'http://hapi.fhir.org/baseR4'):
        self._client = SyncFHIRClient(server_url)
        self._patient = None
        self._conditions = []
        self._observations = []

    def fetch(self, patient_id: str) -> None:
        """Loads the patient and related resources from the FHIR server."""
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

    # ── Patient getters ───────────────────────────────────────────────────

    def get_patient(self) -> dict | None:
        """Returns the raw Patient resource dictionary."""
        return self._patient

    def get_name(self) -> str:
        if not self._patient:
            return 'Not specified'
        names = self._patient.get('name', [])
        if not names:
            return 'Not specified'
        n = names[0]
        return n.get('text') or \
               ' '.join(n.get('given', []) + [n.get('family', '')]).strip() or \
               'Not specified'

    def get_gender(self) -> str:
        return self._patient.get('gender', 'Not specified') if self._patient else 'Not specified'

    def get_birth_date(self) -> str:
        return self._patient.get('birthDate', 'Not specified') if self._patient else 'Not specified'

    def get_address(self) -> str:
        if not self._patient:
            return 'Not specified'
        addresses = self._patient.get('address', [])
        if not addresses:
            return 'Not specified'
        addr = addresses[0]
        parts = addr.get('line', []) + \
                [addr.get('city', ''), addr.get('state', ''), addr.get('country', '')]
        return ', '.join(p for p in parts if p) or 'Not specified'

    def get_phone(self) -> str:
        if not self._patient:
            return 'Not specified'
        for t in self._patient.get('telecom', []):
            if t.get('system') == 'phone':
                return t.get('value', 'Not specified')
        return 'Not specified'

    def is_active(self) -> bool | None:
        return self._patient.get('active') if self._patient else None

    # ── Condition getters ─────────────────────────────────────────────────

    def get_conditions(self) -> list[dict]:
        """Returns a list of raw Condition resources."""
        return self._conditions

    def get_condition_names(self) -> list[str]:
        """Returns a list of human-readable condition/diagnosis names."""
        result = []
        for c in self._conditions:
            code_info = c.get('code', {})
            display = code_info.get('text') or \
                      (code_info.get('coding', [{}])[0].get('display', 'No description'))
            result.append(display)
        return result

    # ── Observation getters ───────────────────────────────────────────────

    def get_observations(self) -> list[dict]:
        """Returns a list of raw Observation resources."""
        return self._observations

    def get_observation_summaries(self) -> list[dict]:
        """Returns observations as a list of {'name': ..., 'value': ...} dicts."""
        result = []
        for o in self._observations:
            code_info = o.get('code', {})
            name = code_info.get('text') or \
                   (code_info.get('coding', [{}])[0].get('display', 'No description'))
            value = o.get('valueQuantity', {})
            val_str = f"{value.get('value', '')} {value.get('unit', '')}".strip()
            result.append({'name': name, 'value': val_str or 'Not specified'})
        return result

    # ── Summary print ──────────────────────────────────────────────────────

    def print_summary(self) -> None:
        if not self._patient:
            print("Patient not loaded.")
            return

        print("=" * 50)
        print("PATIENT DATA")
        print("=" * 50)
        print(f"Name:         {self.get_name()}")
        print(f"Gender:       {self.get_gender()}")
        print(f"Date of birth:{self.get_birth_date()}")
        print(f"Address:      {self.get_address()}")
        print(f"Phone:        {self.get_phone()}")
        print(f"Active:       {self.is_active()}")
        print("=" * 50)
        print(f"Conditions:                {len(self._conditions)}")
        print(f"Observations:              {len(self._observations)}")

        names = self.get_condition_names()
        if names:
            print("\nFirst conditions:")
            for name in names[:3]:
                print(f"  - {name}")

        summaries = self.get_observation_summaries()
        if summaries:
            print("\nFirst observations:")
            for s in summaries[:3]:
                print(f"  - {s['name']}: {s['value']}")
