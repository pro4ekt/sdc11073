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
import time
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

        # Флаг: True означает штатное завершение (DEV-49).
        # Используется в _graceful_shutdown() чтобы отличать плановый stop от аварийного.
        self._intentional_shutdown = False

        # Таймстемп последнего вызова scheduleUpdate() (monotonic, seconds).
        # Используется в on_metric_update / on_alert_update для rate-limiting:
        # не более 1 обновления UI в секунду при высокочастотных waveform-данных.
        self._last_ui_update_ts: float = 0.0


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
          5. Основной цикл мониторинга с механизмом T_fallback (IHE SDPi)
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

            # ------------------------------------------------------------------
            # ШАГ 2b: Подписка на SDPi-A R1030/R1031 (детекция смены сессии)
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
            # ─────────────────────────────────────────────────────────────────
            # ПОЧЕМУ НЕТ ПЕРИОДИЧЕСКОЙ ПРОВЕРКИ mdib_version (R1030/R1031):
            # ─────────────────────────────────────────────────────────────────
            # SDC работает поверх TCP. TCP гарантирует доставку и порядок байт —
            # потеря отдельного SOAP-репорта на транспортном уровне невозможна.
            # sdc11073 дополнительно валидирует MdibVersion внутри диспетчера
            # входящих сообщений (ConsumerMdib._on_episodic_report).
            #
            # Периодическое сравнение mdib_version раз в 5 секунд (polling) является
            # архитектурно неверным подходом для R1030/R1031: за один интервал
            # легитимно приходит N репортов → gap = N → ложная тревога.
            #
            # Правильный детектор смены сессии — event-driven через
            # sequence_or_instance_id_changed_event, что и подключается ниже.
            # Полноценная поимплементация R1030 per-report требует перехвата
            # заголовков SOAP (MdibVersion из SOAP Header) — за рамками текущей
            # архитектуры, sdc11073 не предоставляет эти данные в публичном API.
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
            # Оба callback'а активны в ОБОИХ режимах:
            #   'icu' — вызывают scheduleUpdate() для обновления Qt/QML UI (rate-limited)
            #   'op'  — пишут события в OperationLogger (тревоги сразу, метрики с троттлингом)
            observableproperties.bind(self.mdib, metrics_by_handle=self.on_metric_update)
            observableproperties.bind(self.mdib, alert_by_handle=self.on_alert_update)

            print(f"[Worker {self.epr}] Connection established. Monitoring...")

            # ------------------------------------------------------------------
            # ШАГ 3b: Запись PatientContext в устройство [только 'op']
            # ------------------------------------------------------------------
            # В 'op'-режиме FHIR-данные уже загружены в self.patient_context при
            # создании воркера (DeviceHandler.__init__ → manager.get_patient_context_data()).
            # Отправляем их немедленно после init_mdib(), не дожидаясь формирования ансамбля.
            #
            # Зачем это нужно, если apply_ensemble_context() тоже отправит Patient+Workflow?
            # Потому что ансамбль формируется через ~10 секунд (asyncio.sleep(10) в
            # _ensemble_formation_task) и требует подтверждения пользователя (y/n).
            # apply_patient_to_mdib() гарантирует, что данные пациента попадут в устройство
            # как можно раньше — независимо от того, будет ли ансамбль создан вообще.
            #
            # При последующем вызове apply_ensemble_context() данные будут перезаписаны —
            # это корректно, т.к. SetContextState идемпотентен (повторная запись тех же
            # данных не создаёт дублирования в MDIB провайдера).
            if self.mode == "op" and self.patient_context:
                self.apply_patient_to_mdib()

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
    # Вспомогательный метод: построение PatientContextState + WorkflowContextState
    # =========================================================================
    def _build_patient_workflow_states(self) -> list:
        """
        Builds a list of proposed BICEPS context states from self.patient_context.

        Returns: [PatientContextState, WorkflowContextState?]
          — PatientContextState is always included if PatientContextDescriptor exists.
          — WorkflowContextState is included only if WorkflowContextDescriptor exists.
          — Returns [] if patient_context is empty or no descriptors found.

        IMPORTANT: Must be called with self.data_lock HELD and self.mdib ready.
        Does NOT perform any network calls — only constructs in-memory objects.
        """
        ctx = self.patient_context
        if not ctx:
            return []

        states = []

        # ── PatientContextState ──────────────────────────────────────────────
        pat_descriptors = self.mdib.descriptions.NODETYPE.get(pm.PatientContextDescriptor, [])
        if pat_descriptors:
            pat_descriptor = pat_descriptors[0]
            existing_pat = self.mdib.context_states.NODETYPE.get(pm.PatientContextState, [])
            if existing_pat:
                # "Update" strategy: deep-copy the existing state so the Provider
                # keeps its own handle/version fields intact and only receives our
                # changes. mk_copy() is a BICEPS deep-copy — safe to modify freely.
                proposed_pat = existing_pat[0].mk_copy()
            else:
                # "Create" strategy: no state exists yet on the Provider.
                # mk_proposed_context_object() creates a blank proposed state
                # with the correct DescriptorHandle pre-filled by the library.
                proposed_pat = self.consumer.context_service_client.mk_proposed_context_object(
                    pat_descriptor.Handle
                )

            # ASSOCIATED = context is active and attached to this patient.
            # Other values: DISASSOCIATED (released), PRE_ASSOCIATED (pending), NO_ASSOCIATION.
            proposed_pat.ContextAssociation = pm_types.ContextAssociation.ASSOCIATED
            proposed_pat.CoreData.Givenname  = ctx.get('given_name')  or None
            proposed_pat.CoreData.Familyname = ctx.get('family_name') or None

            # Weight / Height: BICEPS requires Decimal for precise numeric values.
            # split()[0] strips the unit suffix if the value came as "70.5 kg" (string).
            weight_num = ctx.get('weight_value')
            if weight_num is not None:
                w_str = str(weight_num).split()[0] if isinstance(weight_num, str) else str(weight_num)
                proposed_pat.CoreData.Weight = Measurement(
                    Decimal(w_str), CodedValue(ctx.get('weight_unit') or 'kg')
                )
            else:
                proposed_pat.CoreData.Weight = None

            height_num = ctx.get('height_value')
            if height_num is not None:
                h_str = str(height_num).split()[0] if isinstance(height_num, str) else str(height_num)
                proposed_pat.CoreData.Height = Measurement(
                    Decimal(h_str), CodedValue(ctx.get('height_unit') or 'cm')
                )
            else:
                proposed_pat.CoreData.Height = None

            states.append(proposed_pat)

        # ── WorkflowContextState ─────────────────────────────────────────────
        # WorkflowContextDescriptor is optional — not all SDC devices expose it.
        # If absent, we skip silently (no error) since Patient+Ensemble are enough.
        wf_descriptors = self.mdib.descriptions.NODETYPE.get(pm.WorkflowContextDescriptor, [])
        if wf_descriptors:
            wf_descriptor = wf_descriptors[0]
            existing_wf = self.mdib.context_states.NODETYPE.get(pm.WorkflowContextState, [])
            if existing_wf:
                proposed_wf = existing_wf[0].mk_copy()
            else:
                proposed_wf = self.consumer.context_service_client.mk_proposed_context_object(
                    wf_descriptor.Handle
                )

            proposed_wf.ContextAssociation = pm_types.ContextAssociation.ASSOCIATED

            # WorkflowDetail can be None on a freshly created proposed state —
            # initialise it before accessing its sub-fields.
            if not hasattr(proposed_wf, 'WorkflowDetail') or proposed_wf.WorkflowDetail is None:
                proposed_wf.WorkflowDetail = pm_types.WorkflowDetail()

            # FHIR Patient ID → BICEPS InstanceIdentifier.
            # Root = our system namespace ("Hospital_FHIR"),
            # Extension = the patient's ID within that namespace.
            patient_id = ctx.get('patient_id')
            if patient_id:
                patient_data = pm_types.PatientDemographicsCoreData()
                identifier = pm_types.InstanceIdentifier(
                    root='Hospital_FHIR',
                    extension_string=str(patient_id)
                )
                patient_data.Identification.append(identifier)
                proposed_wf.WorkflowDetail.Patient = patient_data

            # DangerCode carries active diagnoses as CodedValue entries.
            # IMPORTANT: clear() before re-filling to prevent duplicates when this
            # method is called multiple times for the same device (e.g. apply_patient
            # followed by apply_ensemble both call _build_patient_workflow_states).
            conditions = ctx.get('conditions', [])
            if conditions:
                if not proposed_wf.WorkflowDetail.DangerCode:
                    proposed_wf.WorkflowDetail.DangerCode = []
                proposed_wf.WorkflowDetail.DangerCode.clear()
                for condition in conditions:
                    proposed_wf.WorkflowDetail.DangerCode.append(pm_types.CodedValue(str(condition)))

            states.append(proposed_wf)

        return states

    # =========================================================================
    # Запись данных пациента (из FHIR) в PatientContext устройства
    # =========================================================================
    def apply_patient_to_mdib(self):
        """
        Sends FHIR patient data into the provider's PatientContextState + WorkflowContextState
        via a SetContextState SOAP call.

        Uses _build_patient_workflow_states() to avoid duplicating the state-building logic
        that is also used in apply_ensemble_context().

        TWO-PHASE PATTERN (thread-safe network call):
          Phase 1 (under data_lock):  read MDIB, build proposed states.
          Phase 2 (lock released):    send SetContextState over the network.

        The operation handle is selected by matching OperationTarget to the
        PatientContextDescriptor handle — same approach as apply_ensemble_context().
        """
        if not self.patient_context:
            print(f"[Worker {self.epr}] No patient context data, skipping apply_patient_to_mdib.")
            return
        if self.mode == "icu":
            return

        operation_handle = None
        states_to_send   = []

        # ------------------------------------------------------------------
        # Phase 1: Build states under data_lock
        # ------------------------------------------------------------------
        try:
            with self.data_lock:
                if not self.mdib:
                    print(f"[Worker {self.epr}] MDIB not ready, skipping apply_patient_to_mdib.")
                    return

                # Find PatientContextDescriptor to locate the correct operation
                pat_descriptors = self.mdib.descriptions.NODETYPE.get(pm.PatientContextDescriptor, [])
                if not pat_descriptors:
                    print(f"[Worker {self.epr}] No PatientContextDescriptor found.")
                    return
                pat_descriptor = pat_descriptors[0]

                # Find SetContextState operation whose OperationTarget = PatientContextDescriptor.
                #
                # WHY filter by OperationTarget instead of taking set_ctx_ops[0]?
                # A device may expose multiple SetContextState operations, each targeting
                # a different context type (Patient, Ensemble, Location…).  Using the wrong
                # handle would cause the Provider to reject the request with OperationNotAllowed.
                # Filtering by OperationTarget == PatientContextDescriptor.Handle guarantees
                # we call the correct entry point.
                set_ctx_ops = self.mdib.descriptions.NODETYPE.get(pm.SetContextStateOperationDescriptor, [])
                for op in set_ctx_ops:
                    if op.OperationTarget == pat_descriptor.Handle:
                        operation_handle = op.Handle
                        break
                # Fallback: some minimal SDC implementations expose only ONE generic
                # SetContextState operation without OperationTarget specificity.
                # Accept it rather than silently failing.
                if not operation_handle and set_ctx_ops:
                    operation_handle = set_ctx_ops[0].Handle

                if not operation_handle:
                    print(f"[Worker {self.epr}] No SetContextStateOperation found.")
                    return

                states_to_send = self._build_patient_workflow_states()

        except Exception as e:
            print(f"[Worker {self.epr}] Failed to build patient context states: {e}")
            return

        if not states_to_send:
            print(f"[Worker {self.epr}] No states to send (no descriptors on device).")
            return

        # ------------------------------------------------------------------
        # Phase 2: Send SetContextState (lock released)
        # ------------------------------------------------------------------
        try:
            if self.consumer.context_service_client:
                print(f"[Worker {self.epr}] Sending PatientContext+Workflow (op='{operation_handle}')...")
                self.consumer.context_service_client.set_context_state(
                    operation_handle=operation_handle,
                    proposed_context_states=states_to_send
                )
                ctx = self.patient_context
                print(f"[Worker {self.epr}] PatientContext applied: "
                      f"{ctx.get('given_name')} {ctx.get('family_name')}")
                # Log to operation report if logger is active
                logger = getattr(self.manager, 'operation_logger', None)
                if logger:
                    logger.log_context_applied(self.epr, 'PatientContext + WorkflowContext')
            else:
                print(f"[Worker {self.epr}] No context_service_client available.")
        except Exception as e:
            print(f"[Worker {self.epr}] Failed to apply patient context: {e}")

    # =========================================================================
    # Отправка UUID ансамбля + полного контекста пациента одним батчем
    # =========================================================================
    def apply_ensemble_context(self, ensemble_uuid: str):
        """
        Sends EnsembleContextState (with the ensemble UUID) plus PatientContextState
        and WorkflowContextState — all in a single atomic SetContextState request.

        Patient/Workflow states are built via _build_patient_workflow_states() to
        avoid duplicating the ~60-line construction logic from apply_patient_to_mdib().

        MODE 'icu': returns immediately — no FHIR data, no ensemble in ICU mode.

        TWO-PHASE PATTERN: same as apply_patient_to_mdib() — Phase 1 under lock,
        Phase 2 (network call) with lock released.
        """
        if self.mode == "icu":
            return
        try:
            # pm and pm_types are already imported at module level, but we re-import
            # locally here so the names shadow the module-level ones cleanly within
            # this method's scope.  This also makes the dependency explicit if this
            # method is ever extracted or tested in isolation.
            from sdc11073.xml_types import pm_qnames as pm
            from sdc11073.xml_types import pm_types

            operation_handle = None
            states_to_send   = []

            # ------------------------------------------------------------------
            # Phase 1: Build all context states under data_lock
            # ------------------------------------------------------------------
            with self.data_lock:
                if not self.mdib:
                    return

                # Step 1: EnsembleContextDescriptor
                # Presence of EnsembleContextDescriptor indicates the device supports
                # BICEPS ensemble membership.  If absent, skip silently.
                ens_descriptors = self.mdib.descriptions.NODETYPE.get(pm.EnsembleContextDescriptor, [])
                if not ens_descriptors:
                    print(f"[Worker {self.epr}] No EnsembleContextDescriptor found.")
                    return
                descriptor = ens_descriptors[0]

                # Step 2: Operation targeting EnsembleContextDescriptor.
                # Same OperationTarget-based filter as apply_patient_to_mdib() —
                # see that method for the detailed rationale.
                set_ctx_ops = self.mdib.descriptions.NODETYPE.get(pm.SetContextStateOperationDescriptor, [])
                for op in set_ctx_ops:
                    if op.OperationTarget == descriptor.Handle:
                        operation_handle = op.Handle
                        break
                if not operation_handle:
                    print(f"[Worker {self.epr}] No SetContextState operation for EnsembleContext.")
                    return

                # Step 3: EnsembleContextState — update or create
                existing_ens = self.mdib.context_states.NODETYPE.get(pm.EnsembleContextState, [])
                if existing_ens:
                    proposed_ens = existing_ens[0].mk_copy()
                    # Clear stale identifiers to avoid accumulating duplicate UUIDs
                    # if apply_ensemble_context() is called more than once per session.
                    if hasattr(proposed_ens, 'Identification') and proposed_ens.Identification is not None:
                        proposed_ens.Identification.clear()
                else:
                    proposed_ens = self.consumer.context_service_client.mk_proposed_context_object(
                        descriptor.Handle
                    )

                proposed_ens.ContextAssociation = pm_types.ContextAssociation.ASSOCIATED

                # Ensemble InstanceIdentifier structure:
                #   root      = fixed UUID of our Orchestrator system (stable across sessions).
                #               Any SDC participant can use this to identify our system as the
                #               ensemble coordinator.
                #   extension = session-specific ensemble UUID (generated once per OR session).
                #               Allows matching devices to a particular surgical session.
                identifier = pm_types.InstanceIdentifier(
                    root='bce837e3-0c46-4e52-af32-15bb36cfd746',
                    extension_string=ensemble_uuid
                )
                # IdentifierName: human-readable label shown on the device's own display
                # (e.g. on a Dräger screen, if the device renders EnsembleContext names).
                identifier.IdentifierName = [pm_types.LocalizedText(ensemble_uuid)]
                if not hasattr(proposed_ens, 'Identification') or proposed_ens.Identification is None:
                    proposed_ens.Identification = []
                proposed_ens.Identification.append(identifier)

                states_to_send = [proposed_ens]

                # Step 4: Append Patient + Workflow states — same batch, atomic delivery.
                # Sending all three context types in ONE SetContextState request means the
                # Provider applies them atomically: either all succeed or all fail.
                # This prevents a race condition where Ensemble is written but PatientContext
                # is not yet present (which would happen with two separate requests).
                # _build_patient_workflow_states() is the shared builder — it keeps the
                # state construction logic in exactly one place.
                if self.patient_context:
                    states_to_send.extend(self._build_patient_workflow_states())

            # ------------------------------------------------------------------
            # Phase 2: Single atomic network request (lock released)
            # ------------------------------------------------------------------
            if self.consumer.context_service_client:
                count = len(states_to_send)
                print(f"[Worker {self.epr}] Sending {count} context states "
                      f"(Ensemble+Patient+Workflow) in one batch...")
                self.consumer.context_service_client.set_context_state(
                    operation_handle=operation_handle,
                    proposed_context_states=states_to_send
                )
                print(f"[Worker {self.epr}] All contexts applied successfully.")
                # Log to operation report
                logger = getattr(self.manager, 'operation_logger', None)
                if logger:
                    logger.log_context_applied(self.epr, 'EnsembleContext + Patient + Workflow')
            else:
                print(f"[Worker {self.epr}] No context_service_client available.")

        except Exception as e:
            print(f"[Worker {self.epr}] Failed to apply ensemble contexts: {e}")

    # =========================================================================
    # Callback для обновлений метрик
    # =========================================================================
    def on_metric_update(self, metrics_by_handle):
        """
        Called by sdc11073 from its notification thread on EpisodicMetricReport.

        'icu' mode: triggers scheduleUpdate() for Qt/QML refresh (rate-limited to 1 Hz
                    to avoid flooding the UI thread with waveform data).

        'op' mode:  writes metric snapshots to OperationLogger (throttled to once per
                    5 seconds per device to keep the log readable).
        """
        if self.mode == "icu":
            if not self.qtDeviceHandler:
                return
            now = time.monotonic()
            if now - self._last_ui_update_ts >= 1.0:
                self._last_ui_update_ts = now
                self.qtDeviceHandler.scheduleUpdate()

        elif self.mode == "op":
            logger = getattr(self.manager, 'operation_logger', None)
            if not logger:
                return
            now = time.monotonic()
            # Throttle: write one snapshot per 5 seconds per device
            if now - self._last_ui_update_ts < 5.0:
                return
            self._last_ui_update_ts = now
            for handle, state in metrics_by_handle.items():
                val = getattr(state.MetricValue, 'Value', None) if state.MetricValue else None
                if val is not None:
                    logger.log_metric(self.epr, handle, str(val))

    # =========================================================================
    # Callback для обновлений тревог
    # =========================================================================
    def on_alert_update(self, alert_by_handle):
        """
        Called by sdc11073 from its notification thread on EpisodicAlertReport.

        'icu' mode: triggers scheduleUpdate() immediately (0.2 s anti-spam cooldown)
                    because alarm state changes must reach UI without noticeable delay.

        'op' mode:  logs every alarm state transition to OperationLogger immediately
                    (no throttle — alarms are clinically significant events).
        """
        if self.mode == "icu":
            if not self.qtDeviceHandler:
                return
            now = time.monotonic()
            if now - self._last_ui_update_ts >= 0.2:
                self._last_ui_update_ts = now
                self.qtDeviceHandler.scheduleUpdate()

        elif self.mode == "op":
            logger = getattr(self.manager, 'operation_logger', None)
            if not logger:
                return
            for handle, state in alert_by_handle.items():
                presence = str(getattr(state, 'Presence', 'Unknown'))
                logger.log_alarm(self.epr, handle, presence)

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

        THREAD SAFETY: вызывается из потока уведомлений sdc11073 (не asyncio loop).
        Запись bool в self.running/self.error_occurred атомарна благодаря GIL Python.
        """
        if not sequence_or_instance_id_changed_event:
            return  # False-значение — игнорируем (ObservableProperty может сбрасываться)

        print(f"[Worker {self.epr}] WARNING SDPi-A: SequenceId/InstanceId changed! "
              f"Device may have restarted — forcing reconnect to resync MDIB.")
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
