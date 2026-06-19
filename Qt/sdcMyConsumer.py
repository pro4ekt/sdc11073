"""
sdcMyConsumer.py — Менеджер (Manager) сети SDC-устройств.

РОЛЬ В АРХИТЕКТУРЕ:
  SdcMyConsumer — центральный координатор всей системы.
  Он не общается с устройствами напрямую — этим занимаются воркеры (DeviceHandler).
  Manager отвечает за:
    1. Сканирование сети (WSDiscovery) на предмет новых SDC-устройств
    2. Создание и запуск DeviceHandler для каждого нового устройства
    3. Реестр живых воркеры (словарь epr → DeviceHandler)
    4. Формирование "ансамблей" устройств (логических групп операционной)
    5. Уведомление QML при подключении/отключении устройств через Qt Signals
    6. Очистку кэша WSDiscovery при аварийном отключении (антизомби-защита)

ПАТТЕРН "ЗОМБИ-ЦИКЛ" И ЗАЩИТА ОТ НЕГО:
  Проблема: WSDiscovery кэширует IP-адреса найденных устройств.
  Если устройство аварийно отключилось, WSDiscovery продолжает возвращать его в search_services().
  DeviceHandler пытается подключиться, падает, Manager создаёт новый DeviceHandler — цикл.
  Решение: в remove_device() при error_occurred=True принудительно удаляем EPR из кэша WSDiscovery.

ПОТОКИ:
  - Главный поток Qt: создаёт SdcMyConsumer, запускает Qt event loop
  - discovery_thread: запускает asyncio event loop с _discovery_loop()
  - Воркеры DeviceHandler: отдельный поток + event loop для каждого устройства
"""

from __future__ import annotations
import asyncio
import socket
import threading
import uuid

from typing import TYPE_CHECKING

# Импорты Qt-объектов для работы как QObject (нужны сигналы deviceConnected/deviceDisconnected)
from qtDeviceHandler import QtDeviceHandler
from deviceHandler import DeviceHandler
from PySide6.QtCore import QObject, Signal, Slot, Property

# WSDiscovery — реализация WS-Discovery (стандарт обнаружения сервисов в локальной сети)
# Рассылает Probe-сообщения по multicast и собирает Hello/ProbeMatch ответы от устройств
from sdc11073.wsdiscovery import WSDiscovery

# Класс для работы с данными пациента из FHIR (используется для type hints)
from fhirData import FHIRPatientData


def get_local_ip() -> str:
    """
    Определяет локальный IP-адрес этой машины в сети.

    Трюк: создаём UDP-сокет и "подключаемся" к внешнему адресу (без реальной отправки).
    После этого getsockname() возвращает IP, который ОС выбрала бы для этого маршрута.
    Надёжнее, чем socket.gethostbyname(), который может вернуть 127.0.0.1 на некоторых системах.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # Адрес не достижим, реальной отправки нет
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"  # Fallback: нет сети
    finally:
        s.close()
    return ip


class SdcMyConsumer(QObject):
    """
    Manager class (The "Manager").

    Сканирует сеть и создаёт воркеры (DeviceHandler) для каждого нового SDC-устройства.
    Является QObject — это позволяет испускать Qt-сигналы из фоновых потоков в UI.

    ВАЖНОЕ АРХИТЕКТУРНОЕ ЗАМЕЧАНИЕ:
    Для предотвращения "зомби-циклов" (бесконечное повторное подключение к сломанному устройству)
    мы принудительно очищаем кэш WSDiscovery в remove_device() при error_occurred=True.
    Это заставляет WSDiscovery делать новый Probe вместо возврата старого кэшированного адреса.
    """

    # Сигнал: новое устройство подключилось.
    # Передаёт QtDeviceHandler — Qt-объект устройства, который QML добавит в список.
    # arguments=['device'] — имя аргумента для QML
    deviceConnected = Signal(QtDeviceHandler, arguments=['device'])

    # Сигнал: устройство отключилось.
    # Передаёт EPR (UUID-строку) — QML удалит соответствующий элемент из списка.
    deviceDisconnected = Signal(str, arguments=['epr'])

    def __init__(self, fhir_data: FHIRPatientData = None, mode: str = "icu"):
        """
        Параметры:
          fhir_data — данные пациента из FHIR (имя, рост, вес, диагнозы).
                      Передаются каждому DeviceHandler при создании для записи
                      в PatientContext устройства.
                      В режиме 'icu' передаётся None (FHIR не загружается).
          mode      — режим запуска: 'icu' (Qt/QML) или 'op' (headless + FHIR).
                      Влияет на испускание сигналов в UI и поведение воркеров.
        """
        super().__init__()

        # Режим запуска ('icu' | 'op') — прокидывается в каждый DeviceHandler
        self.mode = mode

        # Данные пациента из FHIR — используются при создании каждого нового воркера
        self.fhir_data = fhir_data

        # Флаг работы — False останавливает _discovery_loop
        self.running = True

        # Реестр активных воркеры: { EPR (str): DeviceHandler }
        # Доступ к словарю ВСЕГДА должен быть под self.lock
        self.devices = {}

        # Таблица ансамблей: { EPR (str): ensemble_uuid (str) }
        # Заполняется при формировании ансамбля в _ensemble_formation_task
        self.ensemble_devices = {}

        # Идентификатор операционной этого Оркестратора.
        # Только устройства с LocationContext.Room == orchestrator_room
        # включаются в ансамбль.
        self.orchestrator_room = "OR-1"

        # Мьютекс для потокобезопасного доступа к self.devices
        self.lock = threading.Lock()

        # Ссылка на объект WSDiscovery (создаётся в _discovery_loop)
        self.discovery = None

        # Заглушка для будущего OPC UA Gateway (сейчас не используется)
        self.opcua_gateway = None

        # Поток обнаружения устройств — запускается в start()
        # daemon=True: завершится вместе с главным потоком
        self.discovery_thread = threading.Thread(target=self._run_discovery, daemon=True)

    def start(self):
        """
        Запускает фоновый поток сканирования сети.
        Вызывается из main.py после создания Qt-приложения.
        """
        self.discovery_thread.start()
        print("[Manager] System started. Discovery loop active.")

    def get_patient_context_data(self) -> dict:
        """
        Возвращает данные пациента из FHIR в формате, пригодном для SDC PatientContext.

        Парсит FHIRPatientData и извлекает:
          - given_name / family_name — имя и фамилия
          - birth_date              — дата рождения
          - conditions              — список диагнозов
          - weight_value/unit       — вес с единицей
          - height_value/unit       — рост с единицей

        Вызывается при создании каждого DeviceHandler, чтобы воркер мог
        записать данные пациента в PatientContext устройства.
        """
        if not self.fhir_data:
            return {}  # Нет данных FHIR — возвращаем пустой dict

        # Разбиваем полное имя на имя и фамилию (FHIR может возвращать "Ivan Petrov")
        full_name = self.fhir_data.get_name()
        name_parts = full_name.split(' ', 1)
        given_name  = name_parts[0] if name_parts else ''
        family_name = name_parts[1] if len(name_parts) > 1 else ''

        conditions = self.fhir_data.get_condition_names()
        birth_date = self.fhir_data.get_birth_date()

        weight_value, weight_unit = None, 'kg'
        height_value, height_unit = None, 'cm'

        # Парсим наблюдения (Observations) из FHIR — ищем рост и вес
        for obs in self.fhir_data.get_observation_summaries():
            name_lower = obs['name'].lower()
            try:
                # Значение может быть "70.5 kg" — берём число и единицу отдельно
                parts = obs['value'].split()
                val  = float(parts[0])
                unit = parts[1] if len(parts) > 1 else ''
                if 'weight' in name_lower or 'вес' in name_lower:
                    weight_value, weight_unit = val, unit or 'kg'
                elif 'height' in name_lower or 'length' in name_lower or 'рост' in name_lower:
                    height_value, height_unit = val, unit or 'cm'
            except (ValueError, IndexError):
                pass  # Непарсируемое значение — пропускаем

        return {
            'given_name':   given_name,
            'family_name':  family_name,
            'birth_date':   birth_date,
            'conditions':   conditions,
            'weight_value': weight_value,
            'weight_unit':  weight_unit,
            'height_value': height_value,
            'height_unit':  height_unit,
        }

    def stop(self):
        """
        Штатная остановка Manager'а.
        Устанавливает флаг running=False (остановит _discovery_loop)
        и вызывает stop() у всех активных воркеры.
        """
        self.running = False
        print("[Manager] Stopping...")
        with self.lock:
            for epr, handler in self.devices.items():
                print(f"[Manager] Stopping worker for: {epr}")
                handler.stop()  # Устанавливает handler.running = False

    def _run_discovery(self):
        """
        Точка входа потока discovery_thread.
        asyncio.run() создаёт event loop, запускает _discovery_loop() и ждёт его завершения.
        """
        asyncio.run(self._discovery_loop())

    # =========================================================================
    # Асинхронная задача формирования ансамбля (только режим 'op')
    # =========================================================================
    async def _ensemble_formation_task(self):
        """
        Запускается параллельно с _discovery_loop() через create_task().
        Работает ТОЛЬКО в режиме 'op' (Operating Room).

        В режиме 'icu' эта задача не запускается — там нет FHIR-данных,
        а ансамблевые контексты (EnsembleContext/WorkflowContext) не используются.

        Ждёт 10 секунд после старта (чтобы все устройства успели подключиться),
        затем проводит интерактивный процесс формирования ансамбля.

        Алгоритм:
          1. Собираем список подключённых устройств
          2. Фильтруем по комнате (orchestrator_room) и состоянию (нет FAILURE)
          3. Выводим список и спрашиваем пользователя: создать ансамбль? (y/n)
          4. Если да — генерируем UUID, отправляем на все устройства через apply_ensemble_context()
        """
        # Ждём, пока устройства успеют подключиться
        await asyncio.sleep(10)
        print("\n[Ensemble Manager] Checking connected devices...")

        # Снимок реестра под локом — итерируем копию, не оригинал
        with self.lock:
            devices_snapshot = list(self.devices.values())

        if not devices_snapshot:
            print("[Ensemble Manager] No devices found to form an ensemble.")
            return

        from sdc11073.xml_types import pm_qnames as pm

        # ------------------------------------------------------------------
        # Фильтрация устройств
        # ------------------------------------------------------------------
        valid_devices = []
        for dev in devices_snapshot:
            with dev.data_lock:
                if not dev.mdib:
                    continue  # MDIB ещё не готов — пропускаем

                # Читаем LocationContext — нужна комната
                room_str = "Unknown"
                loc_states = dev.mdib.context_states.NODETYPE.get(pm.LocationContextState, [])
                if loc_states and loc_states[0].LocationDetail:
                    room_str = loc_states[0].LocationDetail.Room or "Unknown"

                # Проверяем ActivationState == FAILURE (неисправное устройство)
                # Проверяем и VmdState, и MdsState — оба могут сигнализировать о неисправности
                has_failure = False
                for state_type in [pm.VmdState, pm.MdsState]:
                    for s in dev.mdib.states.NODETYPE.get(state_type, []):
                        act_state = getattr(s, 'ActivationState', None)
                        if act_state and str(act_state).lower() in ("fail", "failure"):
                            has_failure = True
                            break
                    if has_failure:
                        break

                # Включаем только устройства из нашей операционной без ошибок
                if room_str == self.orchestrator_room and not has_failure:
                    valid_devices.append(dev)
                else:
                    reason = []
                    if room_str != self.orchestrator_room:
                        reason.append(f"Room mismatch ('{room_str}' != '{self.orchestrator_room}')")
                    if has_failure:
                        reason.append("ActivationState is FAILURE")
                    print(f"[Ensemble Manager] Filtered out device {dev.epr}. Reason: {', '.join(reason)}")

        devices_snapshot = valid_devices

        if not devices_snapshot:
            print("[Ensemble Manager] No valid devices remaining to form an ensemble.")
            return

        # ------------------------------------------------------------------
        # Сбор информации об устройствах для отображения пользователю
        # ------------------------------------------------------------------
        print("-" * 40)
        for dev in devices_snapshot:
            with dev.data_lock:
                loc_str = "Unknown"
                loc_states = dev.mdib.context_states.NODETYPE.get(pm.LocationContextState, [])
                if loc_states and loc_states[0].LocationDetail:
                    detail = loc_states[0].LocationDetail
                    loc_str = (f"Facility:{getattr(detail, 'Facility', '')} "
                               f"Room:{getattr(detail, 'Room', '')} "
                               f"Bed:{getattr(detail, 'Bed', '')}")

                # Читаем метрику "device_health" для отображения состояния устройства
                health = "Unknown"
                for state in dev.mdib.states.NODETYPE.get(pm.NumericMetricState, []):
                    if getattr(state, 'DescriptorHandle', None) == "device_health":
                        if state.MetricValue is not None:
                            health = state.MetricValue.Value
                        break

                print(f"[Device] EPR: {dev.epr} | Location: {loc_str} | Health: {health}")
        print("-" * 40)

        # Запускаем blocking input() в отдельном потоке через asyncio.to_thread(),
        # чтобы не заблокировать event loop на время ожидания ввода пользователя
        ans = await asyncio.to_thread(input, "Create Ensemble for these devices? (y/n): ")

        if ans.strip().lower() == 'y':
            # Генерируем уникальный UUID для этого ансамбля
            ensemble_uuid = str(uuid.uuid4())
            print(f"\n[Ensemble Manager] Creating Ensemble with UUID: {ensemble_uuid}")

            # Сохраняем в память: epr → uuid (для будущего использования)
            for dev in devices_snapshot:
                self.ensemble_devices[dev.epr] = ensemble_uuid

            # Отправляем UUID на все устройства ансамбля
            # apply_ensemble_context() вызывает SetContextState на каждом устройстве
            for dev in devices_snapshot:
                dev.apply_ensemble_context(ensemble_uuid)
        else:
            print("[Ensemble Manager] Ensemble creation aborted.")

    # =========================================================================
    # Основной цикл обнаружения устройств
    # =========================================================================
    async def _discovery_loop(self):
        """
        Основной async-цикл Manager'а. Работает в отдельном потоке (discovery_thread).

        Алгоритм:
          1. Запускаем WSDiscovery на локальном IP
          2. Каждые 2 секунды: search_services() → проверяем новые EPR
          3. Для каждого нового EPR: создаём DeviceHandler и запускаем его
          4. Параллельно: запускаем _ensemble_formation_task()
        """
        # Сохраняем ссылку на running event loop — нужна воркерам для run_coroutine_threadsafe()
        self.manager_loop = asyncio.get_running_loop()

        local_ip = get_local_ip()
        print(f"[Manager] Network Scan on IP: {local_ip}")

        # Инициализируем WS-Discovery на нашем IP.
        # WSDiscovery рассылает UDP multicast Probe и слушает Hello/ProbeMatch ответы.
        self.discovery = WSDiscovery(local_ip)
        self.discovery.start()

        # Запускаем задачу формирования ансамбля параллельно с основным циклом.
        # create_task() запускает корутину "в фоне" в том же event loop.
        # ТОЛЬКО в режиме 'op': в 'icu' нет FHIR-данных и ансамбли не нужны.
        if self.mode == "op":
            self.manager_loop.create_task(self._ensemble_formation_task())

        while self.running:
            try:
                # search_services() — синхронная блокирующая функция (ждёт ответов timeout сек).
                # Запускаем её в отдельном потоке через to_thread(), чтобы не блокировать event loop.
                services = await asyncio.to_thread(self.discovery.search_services, timeout=2)

                # Обрабатываем каждый найденный сервис
                for service in services:
                    try:
                        # Нормализуем EPR: strip() убирает случайные пробелы
                        epr = str(service.epr).strip()

                        with self.lock:
                            # Проверяем "мёртвые" воркеры: поток завершился, но запись осталась.
                            # Это может случиться при гонке между remove_device() и следующим Probe.
                            if epr in self.devices and not self.devices[epr].is_alive():
                                print(f"[Manager] Found dead worker thread for {epr}. Cleaning up.")
                                del self.devices[epr]

                            # Создаём воркер только для НЕЗНАКОМЫХ устройств
                            if epr not in self.devices:
                                print(f"[Manager] Found NEW device: {epr}. Spawning Worker.")
                                device = DeviceHandler(service, self, mode=self.mode)
                                self.devices[epr] = device
                                device.start()  # Запускает threading.Thread.start()

                    except Exception as loop_err:
                        print(f"[Manager] Error processing a discovered service: {loop_err}")

                # Пауза 2 секунды перед следующим сканированием
                await asyncio.sleep(2)

            except Exception as e:
                print(f"[Manager] Discovery Loop Error: {e}")
                await asyncio.sleep(5)  # Длиннее пауза после ошибки

        # Цикл завершён (self.running = False) — останавливаем WSDiscovery
        self.discovery.stop()

    # =========================================================================
    # Удаление воркера из реестра (вызывается воркером при завершении)
    # =========================================================================
    def remove_device(self, epr: str, error_occurred: bool = False):
        """
        Callback для самоудаления DeviceHandler'а из реестра Manager'а.
        Вызывается из потока DeviceHandler при его завершении (штатном или аварийном).

        Параметры:
          epr            — UUID устройства (строка)
          error_occurred — True если воркер завершился из-за ошибки/разрыва соединения

        АНТИЗОМБИ-ЗАЩИТА:
        Если error_occurred=True, принудительно удаляем EPR из кэша WSDiscovery.
        Это предотвращает бесконечный цикл: WSDiscovery возвращает старый IP →
        новый воркер пытается подключиться → падает → повторяется.
        """
        epr = str(epr).strip()  # Нормализуем на случай разных представлений
        with self.lock:
            if epr in self.devices:
                print(f"[Manager] Removing handler for {epr} from registry.")
                del self.devices[epr]
                # Уведомляем QML только в режиме 'icu' (в 'op' UI-потока нет)
                if self.mode == "icu":
                    self.deviceDisconnected.emit(epr)

            # ------------------------------------------------------------------
            # Очистка кэша WSDiscovery (антизомби-защита)
            # ------------------------------------------------------------------
            if error_occurred and self.discovery:
                print(f"[Manager] Device {epr} had error. Clearing WSDiscovery cache...")
                try:
                    cleared = False
                    # _services / _remote_services — внутренние кэши sdc11073 WSDiscovery
                    # Названия могут отличаться в разных версиях библиотеки — проверяем оба
                    if hasattr(self.discovery, '_services') and epr in self.discovery._services:
                        del self.discovery._services[epr]
                        cleared = True
                    if hasattr(self.discovery, '_remote_services') and epr in self.discovery._remote_services:
                        del self.discovery._remote_services[epr]
                        cleared = True

                    if cleared:
                        print(f"[Manager] Cache for {epr} cleared successfully.")
                except Exception as e:
                    print(f"[Manager] Error clearing cache for {epr}: {e}")
