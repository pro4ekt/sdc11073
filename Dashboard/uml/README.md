# SDC Orchestrator — UML-диаграммы для магистерской работы

## Структура файлов

```
Dashboard/uml/
├── 01_component_architecture.puml   — Компонентная архитектура (обзор системы)
├── 02_sequence_device_lifecycle.puml — Жизненный цикл устройства (DEV-31, DEV-49, R1030, R1031)
├── 03_state_alarm_matrix.puml       — Матрица состояний тревог (IEC 60601-1-8)
├── 04_class_diagram.puml            — Диаграмма классов
└── 05_thread_model.puml             — Модель потоков и event loop'ов
```

## Как отрендерить

### Вариант 1: Online (быстро)
1. Открыть https://www.plantuml.com/plantuml/uml/
2. Вставить содержимое `.puml` файла
3. Скачать PNG/SVG

### Вариант 2: PyCharm Plugin
- Установить плагин **PlantUML Integration**
- Открыть `.puml` файл → превью рендерится автоматически

### Вариант 3: CLI (для батчевой генерации)
```bash
# Установить plantuml.jar
# https://plantuml.com/download

java -jar plantuml.jar -tsvg uml/*.puml
# SVG файлы появятся рядом с .puml
```

### Вариант 4: VS Code
- Установить расширение **PlantUML** (jebbs)
- Ctrl+Shift+P → "PlantUML: Preview Current Diagram"

---

## Что показывает каждая диаграмма

### 01 — Компонентная архитектура
Обзорная схема всей системы: все компоненты и их связи.
Подходит для введения в архитектурный раздел главы.
Включает: WSDiscovery, SdcConsumer, MDIB, QtDeviceHandler, QML Engine, FHIR.

### 02 — Жизненный цикл устройства (ГЛАВНАЯ)
Детальная sequence diagram всех 9 фаз:
1. Запуск (`--mode=icu/op`)
2. Обнаружение устройства (WSDiscovery)
3. Подключение (SdcConsumer + init_mdib)
4. Подписки (ObservableProperty)
5. Qt/QML мост (moveToThread)
6. Мониторинговый цикл (T_fallback)
7. Push-уведомления (EpisodicReport)
8. **R1030/R1031** — смена SequenceId/InstanceId
9. **DEV-31** — квитирование тревоги (On → Ack)
10. **DEV-49** — штатное завершение (Unsubscribe)

### 03 — Матрица состояний тревог
State diagram: Off → On → Ack → Latch → Off.
Включает COMM_FAILURE (SelfCheckPeriod), цвета UI, звуковые состояния.
Соответствие IEC 60601-1-8 §6.8.5.

### 04 — Диаграмма классов
Все атрибуты и методы ключевых классов:
SdcMyConsumer, DeviceHandler, QtDeviceHandler, FHIRPatientData.
Внешние зависимости: WSDiscovery, SdcConsumer, ConsumerMdib.

### 05 — Модель потоков
Архитектура многопоточности:
- Main Thread (Qt UI)
- discovery_thread (asyncio)
- DeviceHandler Thread × N (asyncio per device)
- sdc11073 internal threads (HTTP listener, notification dispatcher)
Схема data_lock и Qt Signal bridge.

---

## Ссылки на стандарты в тексте работы

| Обозначение | Стандарт | Реализация |
|---|---|---|
| **DEV-31** | IHE SDPi — Remote Alarm Acknowledgement | `acknowledge_alarm()` → `SetAlertState(Presence=Ack)` |
| **DEV-49** | IHE SDPi — Graceful Session Termination | `_graceful_shutdown()` → `WS-Eventing Unsubscribe` |
| **R1030** | IHE SDPi-A — Missing Report Detection | `_on_sequence_id_changed()` → reconnect → GetMdib |
| **R1031** | IHE SDPi-A — MDIB Resynchronization | Переподключение → `ConsumerMdib.init_mdib()` |
| **IEC 60601-1-8** | Alarm State Machine | Priority matrix: On>Ack>Latch>Off |
| **BICEPS** | Medical Device Communication | `pm_qnames`, `pm_types`, MDIB structure |
| **HL7 FHIR R4** | Patient Data Exchange | `FHIRPatientData`, `/Patient`, `/Condition`, `/Observation` |

