from fhirpy import SyncFHIRClient

# HAPI FHIR публичный тестовый сервер (R4)
client = SyncFHIRClient('http://hapi.fhir.org/baseR4')

PATIENT_ID = '131896579'  # Реальный пациент на HAPI FHIR публичном сервере

try:
    # Делаем один запрос, который вытягивает Пациента и связанные ресурсы
    bundle = client.resources('Patient') \
        .search(_id=PATIENT_ID) \
        .revinclude('Condition', 'patient') \
        .revinclude('Observation', 'patient') \
        .fetch_raw()

    # Разбираем полученный пакет данных (Bundle)
    patient = None
    conditions = []
    observations = []

    for entry in bundle.get('entry', []):
        resource = entry.get('resource', {})
        res_type = resource.get('resourceType')

        if res_type == 'Patient':
            patient = resource
        elif res_type == 'Condition':
            conditions.append(resource)
        elif res_type == 'Observation':
            observations.append(resource)

    # Выводим данные пациента
    if patient is None:
        print(f"Пациент с ID {PATIENT_ID} не найден.")
    else:
        print("=" * 50)
        print("ДАННЫЕ ПАЦИЕНТА")
        print("=" * 50)

        # Имя
        names = patient.get('name', [])
        if names:
            name = names[0]
            full_name = name.get('text') or \
                        ' '.join(name.get('given', []) + [name.get('family', '')]).strip()
            print(f"Имя:         {full_name or 'Не указано'}")

        # Пол
        print(f"Пол:         {patient.get('gender', 'Не указан')}")

        # Дата рождения
        print(f"Дата рожд.:  {patient.get('birthDate', 'Не указана')}")

        # Адрес
        addresses = patient.get('address', [])
        if addresses:
            addr = addresses[0]
            addr_parts = addr.get('line', []) + \
                         [addr.get('city', ''), addr.get('state', ''), addr.get('country', '')]
            addr_str = ', '.join(p for p in addr_parts if p)
            print(f"Адрес:       {addr_str or 'Не указан'}")

        # Телефон
        telecoms = patient.get('telecom', [])
        for t in telecoms:
            if t.get('system') == 'phone':
                print(f"Телефон:     {t.get('value', 'Не указан')}")
                break

        # Статус (active)
        print(f"Активен:     {patient.get('active', 'Не указано')}")

        print("=" * 50)
        print(f"Диагнозов (Condition):                 {len(conditions)}")
        print(f"Физиологических записей (Observation): {len(observations)}")

        # Первые 3 диагноза
        if conditions:
            print("\nПервые диагнозы:")
            for c in conditions[:3]:
                code_info = c.get('code', {})
                display = code_info.get('text') or \
                          (code_info.get('coding', [{}])[0].get('display', 'Без описания'))
                print(f"  - {display}")

        # Первые 3 наблюдения
        if observations:
            print("\nПервые наблюдения (Observation):")
            for o in observations[:3]:
                code_info = o.get('code', {})
                display = code_info.get('text') or \
                          (code_info.get('coding', [{}])[0].get('display', 'Без описания'))
                value = o.get('valueQuantity', {})
                val_str = f"{value.get('value', '')} {value.get('unit', '')}".strip()
                print(f"  - {display}: {val_str or 'Значение не указано'}")

except Exception as e:
    print(f"Ошибка при выполнении запроса: {e}")