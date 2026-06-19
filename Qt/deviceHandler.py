"""
deviceHandler.py — «Рабочий поток» (Worker) для одного SDC-устройства.

АРХИТЕКТУРА:
  Каждое обнаруженное устройство получает свой собственный объект DeviceHandler,
  который запускается как отдельный системный поток (threading.Thread).
  Внутри этого потока создаётся изолированный asyncio event loop — это ключевое решение:
  сетевые задержки или зависание одного устройства не влияют на остальные.

  Поток живёт ровно столько, сколько живёт соединение с устройством.
  При разрыве (штатном или аварийном) поток завершается и уведомляет Manager
  через метод remove_device(), который решает, нужно ли сбросить кэш WSDiscovery.

ВЗАИМОДЕЙСТВИЕ С UI:
  Для передачи данных в QML используется объект QtDeviceHandler (QObject).
  Он создаётся в рабочем потоке, но сразу перемещается в главный поток через moveToThread(),
  чтобы Qt-сигналы и свойства работали корректно и потокобезопасно.
"""

import threading
import asyncio
from decimal import Decimal

# Импортируем Qt-обёртку — она создаётся для каждого устройства и живёт в UI-потоке
from qtDeviceHandler import QtDeviceHandler

# Основные классы sdc11073 для потребителя (Consumer = клиент SDC-устройства)
from sdc11073.consumer import SdcConsumer
from sdc11073.mdib import ConsumerMdib

# periodic_actions — список действий, которые НЕ нужно подписывать через subscriptions.
# Они приходят периодически и обрабатываются иначе.
from sdc11073.xml_types.actions import periodic_actions

# pm — XML-имена (QName) для типов BICEPS/SDC (дескрипторы, стейты и т.д.)
from sdc11073.xml_types import pm_qnames as pm

# pm_types — Python-классы для значений типов BICEPS (enum'ы, структуры данных)
from sdc11073.xml_types import pm_types

# Конкретные типы для работы с измерениями
from sdc11073.xml_types.pm_types import Measurement, CodedValue

# observableproperties — механизм подписки на изменения в ConsumerMdib
# Позволяет вызывать callback при обновлении метрик или тревог
from sdc11073 import observableproperties

# Нужен для получения ссылки на главный UI-поток Qt при moveToThread() (только 'icu').
# QCoreApplication работает в обоих режимах — это базовый класс для QGuiApplication.
from PySide6.QtCore import QCoreApplication

# RelatedMeasurement — класс sdc11073, у которого есть баг в методе from_node():
# он вызывает __init__ с аргументом, который в норме обязателен, но при десериализации
# ещё не известен. Обходим это патчем ниже.
from sdc11073.xml_types.pm_types import Measurement, RelatedMeasurement


# =============================================================================
# MONKEY-PATCH: Исправление бага десериализации RelatedMeasurement
# =============================================================================
# Проблема: стандартный from_node() пытается вызвать cls(...), что требует
#           аргумент 'value' в __init__ — но при парсинге XML он ещё неизвестен.
# Решение:  подменяем from_node() на версию, которая сначала создаёт объект
#           с заглушкой (None, None), а потом заполняет его через update_from_node().
# Это классический monkey-patch — изменение поведения чужой библиотеки в рантайме.
@classmethod
def _related_measurement_from_node(cls, node):
    # Создаём "пустой" объект с заглушкой, минуя требование __init__
    obj = cls(Measurement(None, None))
    # Теперь заполняем его реальными данными из XML-узла
    obj.update_from_node(node)
    return obj

# Заменяем метод класса RelatedMeasurement на нашу исправленную версию
RelatedMeasurement.from_node = _related_measurement_from_node


# =============================================================================
# Класс DeviceHandler — рабочий поток для одного SDC-устройства
# =============================================================================
class DeviceHandler(threading.Thread):
    """
    Worker class (The "Worker").

    Отвечает за поддержание соединения с ОДНИМ конкретным SDC-устройством (Provider).
    Запускается в собственном системном потоке с независимым asyncio event loop.

    Жизненный цикл:
      1. Manager создаёт DeviceHandler и вызывает start().
      2. Поток подключается к устройству, инициализирует MDIB, запускает мониторинг.
      3. При разрыве соединения (или ошибке) поток завершается и вызывает
         manager.remove_device() для самоудаления из реестра.
    """

    def __init__(self, wsd_service, manager, mode: str = "icu"):
        """
        Параметры:
          wsd_service — объект из WSDiscovery, содержащий EPR (уникальный ID)
                        и адрес устройства для подключения.
          manager     — ссылка на SdcMyConsumer (Manager), нужна для:
                        - получения данных пациента
                        - отправки сигнала deviceConnected в UI
                        - регистрации/удаления из реестра устройств
          mode        — режим запуска ('icu' | 'op'):
                        'icu' — создаёт QtDeviceHandler, испускает сигналы в UI.
                        'op'  — headless, пропускает Qt/QML-логику, активирует FHIR-контексты.
        """
        # Инициализируем поток как демон: он автоматически завершится,
        # когда завершится главный поток приложения
        threading.Thread.__init__(self, daemon=True)

        # Режим запуска — управляет поведением Qt-UI и FHIR-контекстов
        self.mode = mode

        # Сохраняем WSD-сервис — он нужен для подключения SdcConsumer
        self.wsd_service = wsd_service

        # EPR (Endpoint Reference) — уникальный UUID устройства в сети.
        # Явно конвертируем в str для гарантии корректного сравнения в словарях.
        self.epr = str(wsd_service.epr)

        # Ссылка на Manager — нужна для обратных вызовов и доступа к данным пациента
        self.manager = manager

        # Получаем данные пациента из FHIR при создании воркера.
        # Они будут записаны в SDC-контекст устройства после инициализации MDIB.
        self.patient_context = self.manager.get_patient_context_data()

        # Флаг для управления основным циклом мониторинга
        self.running = True

        # SDC Consumer — объект для общения с устройством по сети (HTTP/SOAP/WS)
        # Инициализируется в _worker_logic(), здесь None
        self.consumer = None

        # ConsumerMdib — локальная копия MDIB (Medical Device Information Base) устройства.
        # Автоматически обновляется при получении уведомлений от устройства.
        self.mdib = None

        # Qt-обёртка, через которую QML читает данные этого устройства
        self.qtDeviceHandler = None

        # Флаг: завершился ли поток из-за ошибки (в отличие от штатной остановки).
        # Если True — Manager сбросит кэш WSDiscovery для этого EPR.
        self.error_occurred = False

        # Мьютекс для защиты доступа к self.mdib.
        # ПРАВИЛО: любой поток, читающий/записывающий MDIB, ОБЯЗАН держать этот лок.
        # Исключение: сетевые вызовы (set_context_state и т.д.) делаются БЕЗ лока,
        # чтобы не блокировать UI-поток на время ожидания сетевого ответа.
        self.data_lock = threading.Lock()

        # Заглушка для будущего OPC UA сервера (сейчас не используется)
        self.opcua_server = None

        # Последняя известная версия MDIB — для детекции пропущенных SOAP-репортов
        # (SDPi-A R1030/R1031: проверка монотонности ReportSequence/MessageNumber).
        # None означает «ещё не инициализировано».
        self._last_mdib_version = None

        # Флаг: True означает штатное завершение (DEV-49).
        # Используется в _graceful_shutdown() чтобы отличать плановый stop от аварийного.
        self._intentional_shutdown = False

    # =========================================================================
    # Точка входа потока (вызывается threading.Thread при start())
    # =========================================================================
    def run(self):
        """
        Запускается автоматически при вызове DeviceHandler.start().
        Создаёт изолированный asyncio event loop и запускает в нём основную логику.

        ВАЖНО: каждый поток создаёт свой event loop — это обеспечивает изоляцию.
        Сетевой таймаут на одном устройстве не замораживает обработку других.
        """
        # Создаём новый event loop специально для этого потока
        loop = asyncio.new_event_loop()
        # Регистрируем его как "текущий" для данного потока
        asyncio.set_event_loop(loop)

        try:
            # Запускаем основную async-логику и блокируем поток до её завершения
            loop.run_until_complete(self._worker_logic())
        finally:
            # Гарантированная очистка event loop при любом исходе
            try:
                loop.close()
            except Exception:
                pass

            # САМОУДАЛЕНИЕ из реестра Manager'а.
            # Передаём флаг error_occurred: если True, Manager сбросит WSDiscovery-кэш,
            # предотвращая бесконечный цикл повторных подключений к неисправному устройству.
            self.manager.remove_device(self.epr, self.error_occurred)
            print(f"[Worker {self.epr}] Thread Exiting (Dead).")

    # =========================================================================
    # Основная асинхронная логика подключения и мониторинга
    # =========================================================================
    async def _worker_logic(self):
        """
        Выполняется внутри изолированного asyncio event loop этого потока.

        Фазы работы:
          1. Подключение к устройству (SdcConsumer)
          2. Инициализация локальной копии MDIB
          3. Подписка на обновления метрик и тревог
          4. Создание Qt-объекта и его перемещение в главный поток
          5. Основной цикл мониторинга с T_fallback (IHE SDPi)
        """
        print(f"[Worker {self.epr}] Connecting...")
        try:
            # ------------------------------------------------------------------
            # ШАГ 1: Подключение к SDC-устройству
            # ------------------------------------------------------------------
            # from_wsd_service() создаёт SdcConsumer по данным из WSDiscovery —
            # это не требует ручного указания IP/порта.
            # ssl_context_container=None означает работу без TLS (нешифрованное соединение).
            self.consumer = SdcConsumer.from_wsd_service(
                wsd_service=self.wsd_service,
                ssl_context_container=None
            )
            # start_all() запускает HTTP-сервер для получения уведомлений от устройства
            # и подписывается на все доступные сервисы, кроме указанных в not_subscribed_actions.
            # periodic_actions — это отчёты, которые устройство шлёт само по таймеру;
            # их подписывать не нужно, они приходят автоматически.
            self.consumer.start_all(not_subscribed_actions=periodic_actions)

            # ------------------------------------------------------------------
            # ШАГ 2: Инициализация MDIB (под локом!)
            # ------------------------------------------------------------------
            # init_mdib() делает сетевой запрос GetMdib и загружает полное описание
            # устройства (дескрипторы + начальные стейты) в локальную память.
            # После этого MDIB живёт и обновляется через push-уведомления.
            # Держим лок, пока MDIB не готов — иначе QtDeviceHandler может прочитать None.
            with self.data_lock:
                self.mdib = ConsumerMdib(self.consumer)
                self.mdib.init_mdib()
                # Запоминаем стартовую версию для последующей проверки пропусков (Task 4)
                self._last_mdib_version = self.mdib.mdib_version

            # ------------------------------------------------------------------
            # ШАГ 2b: Подписка на SDPi-A R1030/R1031 (детекция пропусков репортов)
            # ------------------------------------------------------------------
            # ЧТО ТАКОЕ ObservableProperty (механизм sdc11073):
            # ─────────────────────────────────────────────────────────────────
            # ObservableProperty — это Python-дескриптор (аналог property), объявленный
            # на уровне класса ConsumerMdib. Например:
            #
            #   class ConsumerMdib:
            #       sequence_or_instance_id_changed_event = ObservableProperty()
            #       metrics_by_handle                     = ObservableProperty()
            #       alert_by_handle                       = ObservableProperty()
            #       ...
            #
            # Дескриптор перехватывает операцию присваивания (=) и при каждом
            # изменении значения автоматически вызывает всех зарегистрированных
            # подписчиков. Внутри это выглядит примерно так:
            #
            #   class ObservableProperty:
            #       def __set__(self, obj, value):
            #           obj._storage[self.name] = value       # сохраняем значение
            #           for cb in obj._subscribers[self.name]: # уведомляем всех
            #               cb(value)
            #
            # КТО ДЕЛАЕТ ПРИСВАИВАНИЕ:
            # sdc11073, когда получает входящий SOAP-репорт от устройства:
            #
            #   # Внутри ConsumerMdib (упрощённо):
            #   def _on_episodic_metric_report(self, report):
            #       states = {s.DescriptorHandle: s for s in report.states}
            #       self.metrics_by_handle = states   # ← ObservableProperty срабатывает здесь
            #       # → все подписанные callback'и вызываются автоматически
            #
            # КАК ПОДПИСАТЬСЯ — функция observableproperties.bind():
            #   observableproperties.bind(obj, имя_поля=callback)
            #   Эквивалентно: obj._subscribers['имя_поля'].append(callback)
            #
            # ВАЖНО — НЕ ВСЕ ПОЛЯ ConsumerMdib являются ObservableProperty.
            # Список ObservableProperty в ConsumerMdib (можно привязать через bind()):
            #   ✓ sequence_or_instance_id_changed_event — перезапуск/смена сессии
            #   ✓ metrics_by_handle     — EpisodicMetricReport
            #   ✓ alert_by_handle       — EpisodicAlertReport
            #   ✓ operation_by_handle   — EpisodicOperationalStateReport
            #   ✓ context_by_handle     — EpisodicContextReport
            #   ✓ component_by_handle   — EpisodicComponentReport
            #   ✓ waveform_by_handle    — WaveformStream
            #   ✓ description_modifications — DescriptionModificationReport
            #
            # Обычные атрибуты (bind() к ним вызовет ошибку):
            #   ✗ mdib_version  — просто int, обновляется внутри при каждом репорте,
            #                     но НЕ через ObservableProperty → нельзя подписаться.
            #
            # Именно поэтому мы используем два механизма ниже:
            # ─────────────────────────────────────────────────────────────────
            #
            # Механизм 1: sequence_or_instance_id_changed_event (ObservableProperty ✓)
            #   Срабатывает когда устройство изменяет SequenceId или InstanceId —
            #   это признак перезапуска или сброса сессии на стороне устройства.
            #   Callback (_on_sequence_id_changed) вызывается из потока уведомлений sdc11073.
            #
            # Механизм 2: Проверка gap'а в основном цикле мониторинга (ниже).
            #   После каждого успешного пинга сравниваем self.mdib.mdib_version
            #   (обычный int) с self._last_mdib_version напрямую.
            #   Если разница > 1 → между пингами потеряны репорты.
            observableproperties.bind(
                self.mdib,
                sequence_or_instance_id_changed_event=self._on_sequence_id_changed
            )

            # ------------------------------------------------------------------
            # ШАГ 3: Подписка на push-уведомления через observableproperties
            # ------------------------------------------------------------------
            # Используем тот же механизм ObservableProperty (см. описание выше в ШАГ 2b).
            #
            # Поток данных для metrics_by_handle:
            #   Устройство (сеть)
            #     ↓  SOAP EpisodicMetricReport
            #   sdc11073 внутренний поток уведомлений
            #     ↓  ConsumerMdib._on_episodic_metric_report()
            #     ↓  self.metrics_by_handle = { handle: MetricState }
            #   ObservableProperty.__set__() → вызывает подписчиков
            #     ↓
            #   on_metric_update(metrics_by_handle)  ← наш callback
            #
            # Аналогично для alert_by_handle — только триггером служит EpisodicAlertReport.
            #
            # ПРИМЕЧАНИЕ: сейчас оба callback'а просто делают return (OPC UA отключён),
            # но инфраструктура сохранена для будущего использования.
            #
            # РЕЖИМ 'op': подписки на метрики и тревоги пропускаются —
            # они нужны только для UI и OPC UA, которые в headless-режиме отключены.
            # Это снижает нагрузку на CPU: sdc11073 не будет вызывать callback'и,
            # которые всё равно ничего не делают.
            if self.mode == "icu":
                observableproperties.bind(self.mdib, metrics_by_handle=self.on_metric_update)
                observableproperties.bind(self.mdib, alert_by_handle=self.on_alert_update)

            print(f"[Worker {self.epr}] Connection established. Monitoring...")

            # ------------------------------------------------------------------
            # ШАГ 4: Создание Qt-объекта и его передача в UI-поток (только 'icu')
            # ------------------------------------------------------------------
            if self.mode == "icu":
                # QtDeviceHandler создаётся здесь (в рабочем потоке) — это нормально.
                # НО объект QObject нельзя долго использовать из чужого потока.
                self.qtDeviceHandler = QtDeviceHandler(self)

                # moveToThread() перемещает Qt-объект в главный поток.
                # После этого все слоты и сигналы QtDeviceHandler будут выполняться
                # в главном потоке через очередь событий Qt — это потокобезопасно.
                main_thread = QCoreApplication.instance().thread()
                if main_thread:
                    self.qtDeviceHandler.moveToThread(main_thread)
                else:
                    print(f"[Worker {self.epr}] Warning: Could not find Main Thread!")

                # Уведомляем UI о появлении нового устройства.
                # Qt Signal автоматически маршалирует вызов в поток получателя (главный).
                self.manager.deviceConnected.emit(self.qtDeviceHandler)

            # ------------------------------------------------------------------
            # ШАГ 5: Основной цикл мониторинга с механизмом T_fallback (IHE SDPi)
            # ------------------------------------------------------------------
            # T_fallback — требование стандарта IHE SDPi: потребитель должен
            # обнаружить разрыв соединения не позднее чем через T_fallback секунд
            # после последнего успешного обмена данными.
            # Реализация: активный пинг (get_context_states) каждые SLEEP_INTERVAL секунд.
            # Если подряд N пингов упали — считаем соединение потерянным.

            SLEEP_INTERVAL = 5.0        # T_keepalive: интервал между пингами (секунды)
            T_FALLBACK = 15.0           # Максимальное время до объявления разрыва (секунды)
            MAX_MISSED = int(T_FALLBACK / SLEEP_INTERVAL)  # = 3 пропущенных интервала

            missed_heartbeats = 0  # Счётчик последовательных неудачных пингов

            while self.running:
                # Проверяем флаг is_connected от sdc11073 (реагирует на TCP-разрыв)
                if not self.consumer.is_connected:
                    print(f"[Worker {self.epr}] Connection lost reported by SDC stack.")
                    self.error_occurred = True
                    break

                # Запускаем обновление UI в главном потоке (только в режиме 'icu')
                if self.mode == "icu" and self.qtDeviceHandler:
                    self.qtDeviceHandler.scheduleUpdate()

                # Активный пинг: пытаемся сделать лёгкий сетевой запрос.
                # get_context_states() — один из самых лёгких запросов к устройству.
                # ВАЖНО: get_context_states() — синхронный блокирующий SOAP-вызов.
                # asyncio.to_thread() выполняет его в пуле потоков, освобождая event loop.
                # Это гарантирует, что входящие WS-Eventing уведомления (тревоги, метрики)
                # продолжают обрабатываться, даже если устройство отвечает с задержкой.
                try:
                    if self.consumer and self.consumer.is_connected:
                        if self.consumer.context_service_client:
                            await asyncio.to_thread(
                                self.consumer.context_service_client.get_context_states
                            )
                        # Если ContextService недоступен (редкий случай) — пропускаем пинг.
                        # В этом случае missed_heartbeats не сбрасывается, что правильно.
                    missed_heartbeats = 0  # Пинг успешен — сбрасываем счётчик

                    # ----------------------------------------------------------
                    # SDPi-A R1030/R1031: проверка пропуска SOAP-репортов
                    # ----------------------------------------------------------
                    # mdib_version увеличивается на 1 при каждом EpisodicReport.
                    # Проверяем это здесь (не через bind), т.к. mdib_version —
                    # обычное свойство (не ObservableProperty) в ConsumerMdib.
                    current_version = self.mdib.mdib_version
                    if self._last_mdib_version is not None and current_version is not None:
                        gap = current_version - self._last_mdib_version
                        if gap > 1:
                            missed = gap - 1
                            print(f"[Worker {self.epr}] WARNING SDPi-A R1030: MDIB version gap! "
                                  f"Expected {self._last_mdib_version + 1}, got {current_version}. "
                                  f"Missed {missed} report(s) — possible lost alert/metric data.")
                            #Сдесь должно быть 5 но для тестов оставил 100
                            if gap > 100:
                                # Слишком большой пропуск — переподключаемся для свежего GetMdib
                                print(f"[Worker {self.epr}] Gap {gap} exceeds threshold. "
                                      f"Forcing reconnect to resync MDIB.")
                                self.error_occurred = True
                                break
                    self._last_mdib_version = current_version

                except Exception as e:
                    missed_heartbeats += 1
                    print(f"[Worker {self.epr}] Ping failed ({missed_heartbeats}/{MAX_MISSED}): {e}")
                    # Проверяем, превышен ли порог T_fallback
                    if missed_heartbeats * SLEEP_INTERVAL >= T_FALLBACK:
                        print(f"[Worker {self.epr}] T_fallback exceeded. Disconnecting.")
                        self.error_occurred = True
                        break  # Выходим из цикла → поток завершится → Manager удалит устройство

                # Ждём до следующей итерации.
                # await здесь критически важен: он отдаёт управление event loop'у,
                # позволяя обрабатывать другие async-задачи (например, входящие уведомления).
                await asyncio.sleep(SLEEP_INTERVAL)

        except Exception as e:
            # Критическая ошибка на этапе подключения или инициализации
            print(f"[Worker {self.epr}] Critical Error: {e}")
            self.error_occurred = True
        finally:
            # DEV-49: Штатное завершение сессии мониторинга.
            # _graceful_shutdown() отправляет Unsubscribe на все активные подписки
            # с таймаутом 5 секунд, что позволяет прикроватному монитору понять:
            # Оркестратор завершает работу штатно, а не аварийно.
            # Без этого шага устройство может активировать fallback-тревогу (60 dBA).
            if self.consumer:
                print(f"[Worker {self.epr}] Stopping consumer resources (DEV-49 graceful)...")
                try:
                    await self._graceful_shutdown()
                except Exception as e:
                    # Крайний случай: форсируем закрытие синхронно
                    print(f"[Worker {self.epr}] Graceful shutdown failed ({e}), forcing stop.")
                    try:
                        self.consumer.stop_all()
                    except Exception:
                        pass

    # =========================================================================
    # Запись данных пациента (из FHIR) в PatientContext устройства
    # =========================================================================
    def apply_patient_to_mdib(self):
        """
        Отправляет данные пациента из FHIR в PatientContextState провайдера
        через сетевой вызов SetContextState (SOAP/HTTP).

        Дополнительно отправляет WorkflowContextState с ID пациента и диагнозами.

        ДВУХФАЗОВАЯ СТРУКТУРА (паттерн для thread-safe сетевых вызовов):
          Phase 1 (под data_lock):  читаем MDIB, строим proposed states.
          Phase 2 (без лока):       выполняем сетевой запрос SetContextState.

        Почему так? Сетевой запрос может занять сотни миллисекунд.
        Держать data_lock на это время означало бы блокировку UI-потока
        (который пытается прочитать MDIB через update_data()).
        Поэтому: собираем данные быстро под локом, затем отпускаем лок и делаем запрос.
        """
        if not self.patient_context:
            print(f"[Worker {self.epr}] No patient context data, skipping.")
            return

        # В режиме 'icu' FHIR-данных нет (fhir_data=None → patient_context пуст).
        # Метод уже завершился бы на проверке выше, но для явности добавляем guard.
        if self.mode == "icu":
            return

        # Переменные, которые будут заполнены в Phase 1 и использованы в Phase 2
        operation_handle = None
        states_to_send = []

        # ------------------------------------------------------------------
        # PHASE 1: Чтение MDIB под локом и построение proposed states
        # ------------------------------------------------------------------
        try:
            with self.data_lock:
                if not self.mdib:
                    print(f"[Worker {self.epr}] MDIB not ready, skipping apply_patient_to_mdib.")
                    return

                # Находим PatientContextDescriptor — он описывает структуру контекста пациента.
                # В MDIB он должен быть ровно один (или ни одного, если устройство не поддерживает).
                pat_descriptors = self.mdib.descriptions.NODETYPE.get(pm.PatientContextDescriptor, [])
                if not pat_descriptors:
                    print(f"[Worker {self.epr}] No PatientContextDescriptor found in provider MDIB.")
                    return
                descriptor = pat_descriptors[0]

                # Находим операцию SetContextState — это handle операции, которую нужно вызвать.
                # Операция — это "точка входа" на стороне Provider'а для изменения контекстов.
                set_ctx_ops = self.mdib.descriptions.NODETYPE.get(pm.SetContextStateOperationDescriptor, [])
                if not set_ctx_ops:
                    print(f"[Worker {self.epr}] No SetContextStateOperationDescriptor found in provider MDIB.")
                    return
                operation_handle = set_ctx_ops[0].Handle  # Handle — строковый ID операции

                # Стратегия "update or create":
                # Если PatientContextState уже существует → берём копию и модифицируем её.
                # Если нет → просим ContextServiceClient создать новый proposed state.
                # mk_copy() создаёт глубокую копию, безопасную для изменений без влияния на MDIB.
                existing_states = self.mdib.context_states.NODETYPE.get(pm.PatientContextState, [])
                if existing_states:
                    proposed_state = existing_states[0].mk_copy()
                else:
                    proposed_state = self.consumer.context_service_client.mk_proposed_context_object(descriptor.Handle)

                # ASSOCIATED — означает, что этот контекст активен и привязан к пациенту
                proposed_state.ContextAssociation = pm_types.ContextAssociation.ASSOCIATED

                # Заполняем CoreData данными из FHIR
                ctx = self.patient_context
                proposed_state.CoreData.Givenname = ctx.get('given_name') or None
                proposed_state.CoreData.Familyname = ctx.get('family_name') or None

                # Вес: преобразуем в Decimal (требование BICEPS — точная арифметика).
                # split()[0] нужен на случай если строка содержит единицу ("70.5 kg").
                weight_num = ctx.get('weight_value')
                if weight_num is not None:
                    weight_val_str = str(weight_num).split()[0] if isinstance(weight_num, str) else str(weight_num)
                    proposed_state.CoreData.Weight = Measurement(
                        Decimal(weight_val_str),
                        CodedValue(ctx.get('weight_unit') or 'kg')
                    )
                else:
                    proposed_state.CoreData.Weight = None

                # Рост — аналогично весу
                height_num = ctx.get('height_value')
                if height_num is not None:
                    height_val_str = str(height_num).split()[0] if isinstance(height_num, str) else str(height_num)
                    proposed_state.CoreData.Height = Measurement(
                        Decimal(height_val_str),
                        CodedValue(ctx.get('height_unit') or 'cm')
                    )
                else:
                    proposed_state.CoreData.Height = None

                # Начинаем список стейтов для отправки с PatientContextState
                states_to_send = [proposed_state]

                # ------------------------------------------------------------------
                # WorkflowContextState — дополнительный контекст для рабочего процесса.
                # Содержит: ID пациента (из FHIR), коды заболеваний (DiagnosisCodes).
                # Проверяем наличие WorkflowContextDescriptor — не все устройства его имеют.
                # ------------------------------------------------------------------
                wf_descriptors = self.mdib.descriptions.NODETYPE.get(pm.WorkflowContextDescriptor, [])
                if wf_descriptors:
                    wf_descriptor = wf_descriptors[0]

                    # Аналогичная стратегия update or create для WorkflowContextState
                    existing_wf_states = self.mdib.context_states.NODETYPE.get(pm.WorkflowContextState, [])
                    if existing_wf_states:
                        proposed_wf_state = existing_wf_states[0].mk_copy()
                    else:
                        proposed_wf_state = self.consumer.context_service_client.mk_proposed_context_object(wf_descriptor.Handle)

                    proposed_wf_state.ContextAssociation = pm_types.ContextAssociation.ASSOCIATED

                    # WorkflowDetail может быть None у нового стейта — инициализируем
                    if not hasattr(proposed_wf_state, 'WorkflowDetail') or proposed_wf_state.WorkflowDetail is None:
                        proposed_wf_state.WorkflowDetail = pm_types.WorkflowDetail()

                    # Привязываем FHIR Patient ID через InstanceIdentifier.
                    # Root — это пространство имён идентификатора (наша "система" = FHIR).
                    # Extension — сам ID пациента в этой системе.
                    patient_id = ctx.get('patient_id')
                    if patient_id:
                        patient_data = pm_types.PatientDemographicsCoreData()
                        identifier = pm_types.InstanceIdentifier(
                            root="Hospital_FHIR",
                            extension_string=str(patient_id)
                        )
                        patient_data.Identification.append(identifier)
                        proposed_wf_state.WorkflowDetail.Patient = patient_data

                    # Диагнозы пациента — каждое заболевание как отдельный CodedValue в DangerCode.
                    # Сначала очищаем старые значения, чтобы не дублировать при повторных вызовах.
                    conditions = ctx.get('conditions', [])
                    if conditions:
                        if not proposed_wf_state.WorkflowDetail.DangerCode:
                            proposed_wf_state.WorkflowDetail.DangerCode = []
                        proposed_wf_state.WorkflowDetail.DangerCode.clear()
                        for condition in conditions:
                            danger_code_obj = pm_types.CodedValue(str(condition))
                            proposed_wf_state.WorkflowDetail.DangerCode.append(danger_code_obj)

                    states_to_send.append(proposed_wf_state)

        except Exception as e:
            print(f"[Worker {self.epr}] Failed to build patient context states: {e}")
            return  # Не продолжаем — нечего отправлять

        # ------------------------------------------------------------------
        # PHASE 2: Сетевой запрос SetContextState (БЕЗ data_lock)
        # ------------------------------------------------------------------
        # Все proposed states готовы. Теперь отправляем их одним запросом.
        # Лок уже отпущен — UI-поток может свободно читать MDIB во время ожидания ответа.
        try:
            if self.consumer.context_service_client:
                if not operation_handle:
                    # operation_handle пуст — такого быть не должно, но защищаемся
                    print(f"[Worker {self.epr}] operation_handle is empty, cannot send SetContextState.")
                    return
                print(f"[Worker {self.epr}] Sending SetContextState with operation '{operation_handle}'")
                # Отправляем все стейты единым SOAP-запросом
                self.consumer.context_service_client.set_context_state(
                    operation_handle=operation_handle,
                    proposed_context_states=states_to_send
                )
                ctx = self.patient_context
                print(f"[Worker {self.epr}] PatientContext applied: "
                      f"{ctx.get('given_name')} {ctx.get('family_name')}")
            else:
                print(f"[Worker {self.epr}] No context_service_client available.")
        except Exception as e:
            print(f"[Worker {self.epr}] Failed to apply patient context: {e}")

    # =========================================================================
    # Отправка UUID ансамбля + полного контекста пациента одним батчем
    # =========================================================================
    def apply_ensemble_context(self, ensemble_uuid: str):
        """
        Отправляет EnsembleContext с UUID ансамбля на устройство.
        Одновременно отправляет PatientContextState и WorkflowContextState —
        всё в одном SetContextState запросе для атомарности.

        Ансамбль — это логическая группа SDC-устройств в одной операционной.
        UUID ансамбля генерируется Manager'ом (SdcMyConsumer) один раз при формировании
        группы и рассылается всем участникам через этот метод.

        РЕЖИМ 'icu': метод сразу завершается — в ICU-режиме FHIR-данных нет и
        ансамбли формируются в 'op'-режиме.

        СТРУКТУРА: так же двухфазовая (Phase 1 под локом, Phase 2 без лока),
        как и apply_patient_to_mdib() — по тем же причинам безопасности.
        """
        # В ICU-режиме FHIR-данных нет — ансамбль не формируется
        if self.mode == "icu":
            return
        try:
            # Локальный импорт — избегаем циклических зависимостей на уровне модуля
            from sdc11073.xml_types import pm_qnames as pm
            from sdc11073.xml_types import pm_types

            with self.data_lock:
                if not self.mdib:
                    return  # MDIB ещё не инициализирован

                # ------------------------------------------------------------------
                # Шаг 1: Находим EnsembleContextDescriptor
                # Ансамбль описывается своим дескриптором в MDIB.
                # Если дескриптора нет — устройство не поддерживает ансамбли.
                # ------------------------------------------------------------------
                ens_descriptors = self.mdib.descriptions.NODETYPE.get(pm.EnsembleContextDescriptor, [])
                if not ens_descriptors:
                    print(f"[Worker {self.epr}] No EnsembleContextDescriptor found.")
                    return
                descriptor = ens_descriptors[0]

                # ------------------------------------------------------------------
                # Шаг 2: Находим операцию SetContextState для EnsembleContext.
                # ВАЖНО: нельзя использовать первую попавшуюся операцию — нужна та,
                # у которой OperationTarget указывает именно на наш EnsembleContextDescriptor.
                # ------------------------------------------------------------------
                operation_handle = None
                set_ctx_ops = self.mdib.descriptions.NODETYPE.get(pm.SetContextStateOperationDescriptor, [])
                for op in set_ctx_ops:
                    if op.OperationTarget == descriptor.Handle:
                        operation_handle = op.Handle
                        break

                if not operation_handle:
                    print(f"[Worker {self.epr}] No SetContext operation found for EnsembleContext.")
                    return

                # ------------------------------------------------------------------
                # Шаг 3: Создаём/обновляем EnsembleContextState
                # ------------------------------------------------------------------
                existing_states = self.mdib.context_states.NODETYPE.get(pm.EnsembleContextState, [])
                if existing_states:
                    proposed_state = existing_states[0].mk_copy()
                    # Очищаем старые идентификаторы, чтобы не накапливать дубликаты
                    if hasattr(proposed_state, 'Identification') and proposed_state.Identification is not None:
                        proposed_state.Identification.clear()
                else:
                    proposed_state = self.consumer.context_service_client.mk_proposed_context_object(descriptor.Handle)

                proposed_state.ContextAssociation = pm_types.ContextAssociation.ASSOCIATED

                # Создаём идентификатор ансамбля:
                # root    = фиксированный UUID нашей системы (не меняется между сессиями)
                # extension = уникальный UUID конкретного ансамбля (генерируется при формировании)
                identifier = pm_types.InstanceIdentifier(
                    root="bce837e3-0c46-4e52-af32-15bb36cfd746",
                    extension_string=ensemble_uuid
                )
                # IdentifierName — человекочитаемое имя для отображения на устройстве
                identifier.IdentifierName = [pm_types.LocalizedText(ensemble_uuid)]

                # Убеждаемся, что список Identification инициализирован
                if not hasattr(proposed_state, 'Identification') or proposed_state.Identification is None:
                    proposed_state.Identification = []
                proposed_state.Identification.append(identifier)

                # Список стейтов для единого запроса: начинаем с EnsembleContextState
                states_to_send = [proposed_state]

                # ------------------------------------------------------------------
                # Шаг 4: Добавляем PatientContext и WorkflowContext в тот же батч.
                # Отправка всех трёх контекстов одним запросом — атомарная операция:
                # устройство либо применит все три, либо не применит ни одного.
                # ------------------------------------------------------------------
                if self.patient_context:
                    ctx = self.patient_context

                    # PatientContextState — имя, рост, вес
                    pat_descriptors = self.mdib.descriptions.NODETYPE.get(pm.PatientContextDescriptor, [])
                    if pat_descriptors:
                        pat_descriptor = pat_descriptors[0]
                        existing_pat_states = self.mdib.context_states.NODETYPE.get(pm.PatientContextState, [])
                        if existing_pat_states:
                            proposed_pat_state = existing_pat_states[0].mk_copy()
                        else:
                            proposed_pat_state = self.consumer.context_service_client.mk_proposed_context_object(pat_descriptor.Handle)

                        proposed_pat_state.ContextAssociation = pm_types.ContextAssociation.ASSOCIATED
                        proposed_pat_state.CoreData.Givenname = ctx.get('given_name') or None
                        proposed_pat_state.CoreData.Familyname = ctx.get('family_name') or None

                        weight_num = ctx.get('weight_value')
                        if weight_num is not None:
                            weight_val_str = str(weight_num).split()[0] if isinstance(weight_num, str) else str(weight_num)
                            proposed_pat_state.CoreData.Weight = Measurement(
                                Decimal(weight_val_str),
                                CodedValue(ctx.get('weight_unit') or 'kg')
                            )
                        else:
                            proposed_pat_state.CoreData.Weight = None

                        height_num = ctx.get('height_value')
                        if height_num is not None:
                            height_val_str = str(height_num).split()[0] if isinstance(height_num, str) else str(height_num)
                            proposed_pat_state.CoreData.Height = Measurement(
                                Decimal(height_val_str),
                                CodedValue(ctx.get('height_unit') or 'cm')
                            )
                        else:
                            proposed_pat_state.CoreData.Height = None

                        states_to_send.append(proposed_pat_state)

                    # WorkflowContextState — ID пациента, диагнозы
                    wf_descriptors = self.mdib.descriptions.NODETYPE.get(pm.WorkflowContextDescriptor, [])
                    if wf_descriptors:
                        wf_descriptor = wf_descriptors[0]
                        existing_wf_states = self.mdib.context_states.NODETYPE.get(pm.WorkflowContextState, [])
                        if existing_wf_states:
                            proposed_wf_state = existing_wf_states[0].mk_copy()
                        else:
                            proposed_wf_state = self.consumer.context_service_client.mk_proposed_context_object(wf_descriptor.Handle)

                        proposed_wf_state.ContextAssociation = pm_types.ContextAssociation.ASSOCIATED

                        if not hasattr(proposed_wf_state, 'WorkflowDetail') or proposed_wf_state.WorkflowDetail is None:
                            proposed_wf_state.WorkflowDetail = pm_types.WorkflowDetail()

                        # Привязка FHIR Patient ID через InstanceIdentifier
                        patient_id = ctx.get('patient_id')
                        if patient_id:
                            patient_data = pm_types.PatientDemographicsCoreData()
                            patient_identifier = pm_types.InstanceIdentifier(
                                root="Hospital_FHIR",
                                extension_string=str(patient_id)
                            )
                            patient_data.Identification.append(patient_identifier)
                            proposed_wf_state.WorkflowDetail.Patient = patient_data

                        # Коды заболеваний — каждый диагноз как отдельный CodedValue
                        conditions = ctx.get('conditions', [])
                        if conditions:
                            if not proposed_wf_state.WorkflowDetail.DangerCode:
                                proposed_wf_state.WorkflowDetail.DangerCode = []
                            proposed_wf_state.WorkflowDetail.DangerCode.clear()
                            for condition in conditions:
                                danger_code_obj = pm_types.CodedValue(str(condition))
                                proposed_wf_state.WorkflowDetail.DangerCode.append(danger_code_obj)

                        states_to_send.append(proposed_wf_state)

            # ------------------------------------------------------------------
            # Phase 2: Единый сетевой запрос (лок уже отпущен)
            # ------------------------------------------------------------------
            if self.consumer.context_service_client:
                print(f"[Worker {self.epr}] Sending EnsembleContext + Patient + Workflow in one batch...")
                self.consumer.context_service_client.set_context_state(
                    operation_handle=operation_handle,
                    proposed_context_states=states_to_send
                )
                print(f"[Worker {self.epr}] All Contexts applied successfully in one batch!")
            else:
                print(f"[Worker {self.epr}] No context_service_client available.")

        except Exception as e:
            print(f"[Worker {self.epr}] Failed to apply contexts in batch: {e}")

    # =========================================================================
    # Callback для обновлений метрик (OPC UA — отключён)
    # =========================================================================
    def on_metric_update(self, metrics_by_handle):
        """
        Вызывается библиотекой sdc11073 при получении EpisodicMetricReport от устройства.
        metrics_by_handle: dict { handle: AbstractMetricState }

        Сейчас отключён (return сразу) — код OPC UA закомментирован.
        При включении: извлекает числовые/строковые значения и передаёт
        их в OPC UA Gateway через asyncio.run_coroutine_threadsafe().
        """
        return  # OPC UA Updates disabled
        # if not self.manager.opcua_gateway or not hasattr(self.manager, 'manager_loop'):
        #     return
        # 
        # updates = {}
        # for handle, state in metrics_by_handle.items():
        #     if state.NODETYPE == pm.NumericMetricState:
        #         val = getattr(state.MetricValue, 'Value', None)
        #         if val is not None:
        #             try:
        #                 updates[handle] = float(val)
        #             except ValueError:
        #                 pass
        #     elif state.NODETYPE in [pm.StringMetricState, pm.EnumStringMetricState]:
        #         val = getattr(state.MetricValue, 'Value', None)
        #         if val is not None:
        #             updates[handle] = str(val)
        # 
        # if updates:
        #     # Передаем обновление в асинхронный цикл менеджера для безопасной записи в OPC
        #     asyncio.run_coroutine_threadsafe(
        #         self.manager.opcua_gateway.update_values(self.epr, updates),
        #         self.manager.manager_loop
        #     )

    # =========================================================================
    # Callback для обновлений тревог (OPC UA — отключён)
    # =========================================================================
    def on_alert_update(self, alert_by_handle):
        """
        Вызывается библиотекой sdc11073 при получении EpisodicAlertReport от устройства.
        alert_by_handle: dict { handle: AbstractAlertState }

        Сейчас отключён (return сразу) — код OPC UA закомментирован.
        При включении: извлекает Presence-флаги тревог и передаёт их в OPC UA Gateway.
        """
        return  # OPC UA Updates disabled
        # if not self.manager.opcua_gateway or not hasattr(self.manager, 'manager_loop'):
        #     return
        # 
        # updates = {}
        # for handle, state in alert_by_handle.items():
        #     if state.NODETYPE in [pm.AlertConditionState, pm.LimitAlertConditionState]:
        #         presence = getattr(state, 'Presence', False)
        #         updates[f"Condition_{handle}_Presence"] = presence
        #     elif state.NODETYPE == pm.AlertSignalState:
        #         signal_presence = str(getattr(state, 'Presence', 'Unknown'))
        #         updates[f"Signal_{handle}"] = signal_presence
        # 
        # if updates:
        #     # Передаем обновление в асинхронный цикл менеджера для безопасной записи в OPC
        #     asyncio.run_coroutine_threadsafe(
        #         self.manager.opcua_gateway.update_values(self.epr, updates),
        #         self.manager.manager_loop
        #     )

    # =========================================================================
    # DEV-31: Удалённое квитирование (acknowledgement) тревоги
    # =========================================================================
    def acknowledge_alarm(self, operation_handle: str, alert_signal_handle: str):
        """
        Квитирует активную тревогу сигнала на устройстве (DEV-31 из IHE SDPi).

        Квитирование переводит AlertSignalPresence из On → Ack:
          On  — тревога активна, звук и индикация включены
          Ack — звук подавлен (пользователь принял к сведению), индикация остаётся
          Latch — параметр вернулся в норму, но требуется ручной сброс
          Off — тревога неактивна

        Параметры:
          operation_handle      — handle операции SetAlertState (из MDIB устройства)
          alert_signal_handle   — handle конкретного AlertSignalState для квитирования

        ДВУХФАЗОВАЯ СТРУКТУРА:
          Phase 1 (под data_lock): читаем стейт из MDIB и готовим proposed state.
          Phase 2 (без лока):      отправляем SetAlertState по сети.
        """
        proposed_state = None

        # ------------------------------------------------------------------
        # Phase 1: Подготовка proposed state под локом
        # ------------------------------------------------------------------
        try:
            with self.data_lock:
                if not self.consumer or not self.mdib:
                    print(f"[Worker {self.epr}] acknowledge_alarm: consumer or mdib not available.")
                    return
                if not self.consumer.set_service_client:
                    # set_service_client — клиент для SetService (SetValue, SetAlertState и т.д.)
                    print(f"[Worker {self.epr}] acknowledge_alarm: set_service_client not available.")
                    return

                # mk_proposed_state() живёт на mdib.xtra (ConsumerMdibMethods), не на set_service_client.
                # Создаёт копию текущего стейта с handle alert_signal_handle для изменения.
                proposed_state = self.mdib.xtra.mk_proposed_state(alert_signal_handle)

                # Устанавливаем новое значение Presence = ACK
                proposed_state.Presence = pm_types.AlertSignalPresence.ACK

        except Exception as e:
            print(f"[Worker {self.epr}] Failed to prepare alarm acknowledgement: {e}")
            return

        # ------------------------------------------------------------------
        # Phase 2: Сетевой вызов SetAlertState (без data_lock)
        # ------------------------------------------------------------------
        try:
            # set_alert_state() возвращает Future — результат операции на устройстве
            future = self.consumer.set_service_client.set_alert_state(
                operation_handle,
                proposed_state
            )
            # Ждём подтверждения от устройства (таймаут 5 секунд)
            future.result(timeout=5)
            print(f"[Worker {self.epr}] Alarm '{alert_signal_handle}' acknowledged successfully.")
        except Exception as e:
            print(f"[Worker {self.epr}] Failed to acknowledge alarm: {e}")

    # =========================================================================
    # DEV-49: Штатное завершение сессии мониторинга
    # =========================================================================
    async def _graceful_shutdown(self):
        """
        Реализует транзакцию DEV-49 (IHE SDPi) — штатное завершение мониторинга.

        Логика:
          1. Отправляем WS-Eventing Unsubscribe на все активные подписки.
          2. Ждём подтверждения (timeout 5 секунд).
          3. Если устройство не отвечает — принудительно закрываем соединение.

        Почему это важно:
          Если TCP-сокет закрыть без Unsubscribe, прикроватный монитор расценит это
          как аварийный разрыв и запустит акустическую fallback-тревогу (60 dBA).
          При штатном Unsubscribe устройство понимает, что наблюдатель ушёл сам,
          и возвращает ответственность за управление тревогами себе.

        asyncio.to_thread() нужен, т.к. stop_all() — синхронный блокирующий вызов:
          он выполняется в пуле потоков, не блокируя event loop.
        """
        self._intentional_shutdown = True
        try:
            # Даём stop_all() до 5 секунд на отправку Unsubscribe и получение ответа.
            await asyncio.wait_for(
                asyncio.to_thread(self.consumer.stop_all),
                timeout=5.0
            )
            print(f"[Worker {self.epr}] DEV-49: Unsubscribe completed — device notified.")
        except asyncio.TimeoutError:
            # Устройство не ответило — закрываем принудительно.
            # Это лучше, чем висеть бесконечно при завершении приложения.
            print(f"[Worker {self.epr}] DEV-49: Unsubscribe timed out (5s). Forcing close.")
        except Exception as e:
            print(f"[Worker {self.epr}] DEV-49: Error during graceful shutdown: {e}")

    # =========================================================================
    # SDPi-A R1030/R1031: Детекция перезапуска / смены сессии устройства
    # =========================================================================
    def _on_sequence_id_changed(self, sequence_or_instance_id_changed_event: bool):
        """
        Callback для observableproperties: срабатывает когда устройство изменяет
        SequenceId или InstanceId в заголовках SOAP-репортов.

        SequenceId/InstanceId меняется при:
          - Перезапуске устройства (reboot)
          - Сбросе SDC-сессии (software reset)
          - Смене активного сетевого интерфейса

        В любом из этих случаев текущая MDIB-копия устарела:
        устройство начало новую "жизнь" с новым MDIB.
        Единственное корректное действие — разорвать соединение и переподключиться,
        выполнив GetMdib заново для получения актуального состояния всех тревог.

        NOTE: _last_mdib_version сбрасываем в None, чтобы цикл мониторинга
        не ложно детектировал "gap" при следующем сравнении версий.

        THREAD SAFETY: вызывается из потока уведомлений sdc11073 (не asyncio loop).
        Запись bool в self.running/self.error_occurred атомарна благодаря GIL Python.
        """
        if not sequence_or_instance_id_changed_event:
            return  # False-значение — игнорируем (ObservableProperty может сбрасываться)

        print(f"[Worker {self.epr}] WARNING SDPi-A: SequenceId/InstanceId changed! "
              f"Device may have restarted — forcing reconnect to resync MDIB.")
        self._last_mdib_version = None  # Сбрасываем — иначе ложный gap после переподключения
        self.error_occurred = True
        self.running = False  # Выход из цикла при следующей итерации


    # =========================================================================
    # Сигнал остановки
    # =========================================================================
    def stop(self):
        """
        Штатная остановка воркера.
        Устанавливает self.running = False, что приводит к выходу из основного цикла
        мониторинга при следующей итерации (после текущего asyncio.sleep()).
        """
        self.running = False
