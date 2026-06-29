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
import ssl
import time
import logging
import logging.handlers
import pathlib
from decimal import Decimal

# =============================================================================
# LOGGING SETUP
# =============================================================================
# Log directory: Qt/logs/  (создаётся автоматически если нет)
_LOG_DIR = pathlib.Path(__file__).parent / 'logs'
_LOG_DIR.mkdir(exist_ok=True)

class _SuppressGetContextStates400(logging.Filter):
    """
    Suppress the repetitive 'GetContextStates HTTP 400' ERROR spam from sdc11073's
    soap client / mdib logger. sdcX returns HTTP 400 for GetContextStates when the
    consumer is not authorized (no mTLS). We already handle this in the ping loop
    and log a one-time warning — the sdc11073 internal ERROR is redundant and noisy.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno != logging.ERROR:
            return True
        msg = record.getMessage()
        # Drop "GetContextStates: POST … HTTP response=400" from sdc.client.soap
        if 'GetContextStates' in msg and '400' in msg:
            return False
        # Drop the traceback from sdc.client.mdib that follows the soap error
        # (it contains HTTPReturnCodeError and _get_context_states in the traceback)
        if 'HTTPReturnCodeError' in msg or '_get_context_states' in msg:
            return False
        return True


def _setup_module_logger() -> logging.Logger:
    """
    Настраивает корневой логгер модуля deviceHandler.

    Уровни:
      - Консоль (StreamHandler): INFO
      - Файл (RotatingFileHandler): DEBUG
        Файл: Qt/logs/sdc_consumer.log
        Ротация: 5 МБ × 5 резервных копий → sdc_consumer.log.1 … .5
    """
    logger = logging.getLogger('sdc.consumer')

    # Не добавляем handlers повторно (если модуль импортируется несколько раз)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    _fmt = logging.Formatter(
        fmt='%(asctime)s [%(levelname)-8s] %(name)s — %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )


    # ── Файл (ротация) ────────────────────────────────────────────────────────
    _file_handler = logging.handlers.RotatingFileHandler(
        filename=str(_LOG_DIR / 'sdc_consumer.log'),
        maxBytes=5 * 1024 * 1024,   # 5 МБ на файл
        backupCount=5,
        encoding='utf-8',
    )
    _file_handler.setLevel(logging.DEBUG)
    _file_handler.setFormatter(_fmt)
    logger.addHandler(_file_handler)

    # Не пробрасываем в корневой логгер (предотвращаем дублирование)
    logger.propagate = False

    return logger

# Модульный логгер — используется для сообщений вне класса DeviceHandler
_module_log = _setup_module_logger()

# Suppress repetitive GetContextStates HTTP 400 ERROR spam from sdc11073 internals.
# sdcX blocks context queries from unauthorized participants — this is expected when
# running without mTLS. We handle it in the ping loop already; the sdc11073 internal
# ERROR is noise. Apply filter once at import time.
logging.getLogger('sdc.client.soap').addFilter(_SuppressGetContextStates400())
logging.getLogger('sdc.client.mdib').addFilter(_SuppressGetContextStates400())

# Импортируем Qt-обёртку — она создаётся для каждого устройства и живёт в UI-потоке
from qtDeviceHandler import QtDeviceHandler

# Основные классы sdc11073 для потребителя (Consumer = клиент SDC-устройства)
from sdc11073.consumer import SdcConsumer
from sdc11073.mdib import ConsumerMdib
from sdc11073.pysoap.soapclient import HTTPReturnCodeError

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

    def __init__(self, wsd_service, manager, mode: str = "icu",
                 target_room: str | None = None):
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
          target_room — фильтр по LocationContext.Room (строка или None).
                        Если задан, воркер проверяет комнату устройства ПОСЛЕ init_mdib()
                        и немедленно завершается без ошибки, если комната не совпадает.
                        None = фильтрация отключена.
        """
        # Инициализируем поток как демон: он автоматически завершится,
        # когда завершится главный поток приложения
        threading.Thread.__init__(self, daemon=True)

        # Режим запуска — управляет поведением Qt-UI и FHIR-контекстов
        self.mode = mode

        # Фильтр по LocationContext.Room.
        # Проверяется в _worker_logic() сразу после init_mdib().
        # Если не None и комната устройства не совпадает → воркер завершается без ошибки
        # (не очищается кэш WSDiscovery, не испускается deviceDisconnected).
        self.target_room: str | None = target_room

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

        # Таймстемп последнего вызова scheduleUpdate() (monotonic, seconds).
        # Используется в on_metric_update / on_alert_update для rate-limiting:
        # не более 1 обновления UI в секунду при высокочастотных waveform-данных.
        self._last_ui_update_ts: float = 0.0

        # Qt-обёртка, через которую QML читает данные этого устройства
        self.qtDeviceHandler = None

        # Флаг: True означает что deviceConnected.emit() уже был отправлен.
        # Используется в Manager.remove_device() чтобы решать, нужно ли emit deviceDisconnected.
        # Устройства, отфильтрованные по target_room до emit(), остаются False.
        self._ui_connected: bool = False

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

        # ── Логгер этого воркера ──────────────────────────────────────────────
        # Имя логгера включает укороченный EPR (последние 12 символов UUID),
        # чтобы в логах сразу было видно, от какого устройства пришло сообщение.
        _short_epr = self.epr[-12:] if len(self.epr) > 12 else self.epr
        self.logger = logging.getLogger(f'sdc.consumer.worker.{_short_epr}')

        # Флаг: True означает что воркер завершился из-за фильтрации по LocationContext.
        # Устанавливается в _worker_logic() при несовпадении target_room.
        # Используется в run() для передачи в manager.remove_device(location_filtered=True),
        # что добавляет EPR в сессионный ban-list → _discovery_loop больше не создаёт
        # DeviceHandler для этого устройства до рестарта оркестратора.
        self._location_filtered: bool = False

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
            # Передаём error_occurred: если True → Manager сбросит WSDiscovery-кэш.
            # Передаём location_filtered: если True → Manager добавит EPR в ban-list,
            # предотвращая повторное создание DeviceHandler для этого устройства.
            self.manager.remove_device(
                self.epr,
                self.error_occurred,
                location_filtered=self._location_filtered,
            )
            self.logger.info("Thread Exiting (Dead).")

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
        self.logger.info("Connecting...")
        try:
            # ------------------------------------------------------------------
            # ШАГ 1: Подключение к SDC-устройству
            # ------------------------------------------------------------------
            # from_wsd_service() создаёт SdcConsumer по данным из WSDiscovery —
            # это не требует ручного указания IP/порта.
            # ssl_context_container=None означает работу без TLS (нешифрованное соединение).
            x_addrs = getattr(self.wsd_service, 'x_addrs', 'unknown')
            self.logger.debug(f"Transport addresses (x_addrs): {x_addrs}")

            # ------------------------------------------------------------------
            # _build_ssl_container() — вспомогательная функция: строит SSLContextContainer
            # с клиентским сертификатом из pat/certs/ (IHE test PKI).
            # Используется и при явном https://, и при TLS-fallback для http://.
            # ------------------------------------------------------------------
            def _build_ssl_container():
                _qt_dir = pathlib.Path(__file__).parent        # Master/Qt/
                _base   = _qt_dir.parent                       # Master/
                _cert_candidates = [
                    # ① certs_out/ внутри Qt/ — наши сгенерированные сертификаты
                    (_qt_dir / 'certs_out' / 'consumer.pem',
                     _qt_dir / 'certs_out' / 'consumer.key',
                     _qt_dir / 'certs_out' / 'ca1.pem',
                     [None]),
                    (_qt_dir / 'certs_out' / 'consumer.pem',
                     _qt_dir / 'certs_out' / 'consumer.key',
                     _qt_dir / 'certs_out' / 'ca.pem',
                     [None]),
                    # ② pat/certs/ — наши сертификаты скопированные туда
                    (_base / 'pat' / 'certs' / 'consumer.pem',
                     _base / 'pat' / 'certs' / 'consumer.key',
                     _base / 'pat' / 'certs' / 'ca1.pem',
                     [None]),
                    # ③ IHE PAT test PKI (pat/certs/) — только если наших нет
                    (_base / 'pat' / 'certs' / 'user_certificate_root_signed.pem',
                     _base / 'pat' / 'certs' / 'user_private_key_encrypted.pem',
                     _base / 'pat' / 'certs' / 'root_certificate.pem',
                     ['dummypassword', 'password', 'sdcX', '12345']),
                    # ④ sdc11073 unit-test PKI (tests/certificates/)
                    (_base / 'tests' / 'certificates' / 'test_certificate.pem',
                     _base / 'tests' / 'certificates' / 'test_private_key.pem',
                     None,
                     ['password', 'dummypassword']),
                ]

                # ── CLIENT context: consumer → provider (стандартный SSL-клиент) ──
                _client_ctx = ssl.create_default_context()
                _client_ctx.check_hostname = False
                _client_ctx.verify_mode = ssl.CERT_NONE

                # ── SERVER context: provider → consumer (приём уведомлений WS-Eventing) ──
                # Важно: нужен PROTOCOL_TLS_SERVER, иначе Python не умеет принимать
                # входящие TLS-соединения от провайдера (create_default_context даёт CLIENT ctx).
                _server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                _server_ctx.check_hostname = False
                _server_ctx.verify_mode = ssl.CERT_NONE

                _loaded = False
                for _cert_file, _key_file, _ca_file, _passwords in _cert_candidates:
                    if not (_cert_file.exists() and _key_file.exists()):
                        continue
                    for _pwd in _passwords:
                        try:
                            _kw = dict(certfile=str(_cert_file),
                                       keyfile=str(_key_file),
                                       password=_pwd or None)
                            _client_ctx.load_cert_chain(**_kw)
                            _server_ctx.load_cert_chain(**_kw)
                            if _ca_file and _ca_file.exists():
                                _client_ctx.load_verify_locations(cafile=str(_ca_file))
                                _server_ctx.load_verify_locations(cafile=str(_ca_file))
                            self.logger.info(
                                f"mTLS: cert loaded from {_cert_file.parent} "
                                f"(password={'<empty>' if not _pwd else repr(_pwd)})"
                            )
                            _loaded = True
                            break
                        except Exception:
                            continue
                    if _loaded:
                        break

                if not _loaded:
                    self.logger.warning(
                        "Could not load any client cert. "
                        "Attempting TLS without client certificate (mTLS will fail if required)."
                    )

                try:
                    from sdc11073 import certloader
                    return certloader.SSLContextContainer(
                        client_context=_client_ctx,
                        server_context=_server_ctx,
                    )
                except Exception as ssl_err:
                    self.logger.warning(
                        f"Could not build SSLContextContainer ({ssl_err}). "
                        f"Using raw ssl_context as fallback."
                    )
                    return _ssl_ctx  # type: ignore[return-value]

            # ------------------------------------------------------------------
            # Стратегия подключения: автодетект https:// → TLS сразу.
            # Если sdcX анонсирует http:// но фактически требует TLS
            # (ConnectionResetError 10054) — повторяем с SSL (TLS-fallback).
            # Fallback теперь безопасен: провайдер собран с TLSConfig.
            # ------------------------------------------------------------------
            ssl_container = None
            if x_addrs and any(str(addr).startswith('https://') for addr in x_addrs):
                self.logger.info("HTTPS detected — building mTLS SSL context...")
                ssl_container = _build_ssl_container()
            else:
                self.logger.info('HTTP announced — connecting without SSL (will retry if reset).')

            self.consumer = SdcConsumer.from_wsd_service(
                wsd_service=self.wsd_service,
                ssl_context_container=ssl_container,
            )

            try:
                self.consumer.start_all(not_subscribed_actions=periodic_actions)
            except Exception as _connect_err:
                _cause = _connect_err.__cause__ or _connect_err
                _is_reset = (
                    isinstance(_cause, ConnectionResetError)
                    or (isinstance(_cause, OSError) and getattr(_cause, 'winerror', None) == 10054)
                    or 'NotConnected' in type(_connect_err).__name__
                )
                if _is_reset and ssl_container is None:
                    # Провайдер анонсировал http:// но требует TLS — повторяем с SSL
                    self.logger.warning(
                        'HTTP connection reset — provider likely requires TLS. '
                        'Retrying with mTLS SSL context...'
                    )
                    ssl_container = _build_ssl_container()
                    self.consumer = SdcConsumer.from_wsd_service(
                        wsd_service=self.wsd_service,
                        ssl_context_container=ssl_container,
                    )
                    self.consumer.start_all(not_subscribed_actions=periodic_actions)
                else:
                    raise

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
                # ДИАГНОСТИКА: что пришло в GetMdib и есть ли ContextService
                # ------------------------------------------------------------------
                _ctx_states = list(self.mdib.context_states.objects)
                _n_ctx = len(_ctx_states)
                if _n_ctx > 0:
                    self.logger.info(
                        f'[DIAG] GetMdib returned {_n_ctx} context state(s) — '
                        f'GetContextStates call NOT needed.'
                    )
                    for _s in _ctx_states:
                        self.logger.debug(
                            f'[DIAG]   context_state: type={_s.NODETYPE.localname}, '
                            f'handle={_s.Handle}, descriptor={_s.DescriptorHandle}'
                        )
                else:
                    self.logger.warning(
                        f'[DIAG] GetMdib returned 0 context states — '
                        f'device may require GetContextStates separately.'
                    )

                _has_ctx_svc = self.consumer.context_service_client is not None
                self.logger.info(
                    f'[DIAG] context_service_client available: {_has_ctx_svc}'
                )

                # ------------------------------------------------------------------
                # ШАГ 2b: Фильтрация по LocationContext (если задан target_room)
                # ------------------------------------------------------------------
                # WSDiscovery обнаруживает все SDC Provider'ы в сети вне зависимости
                # от их местоположения. LocationContext становится доступен только
                # ПОСЛЕ init_mdib() — он хранится в context_states MDIB.
                #
                # Если target_room задан и комната устройства не совпадает →
                # возвращаемся из _worker_logic() чистым путём (return, не исключение).
                # Это означает:
                #   - error_occurred = False → кэш WSDiscovery НЕ очищается
                #     (устройство здорово, просто в другой комнате — оно может снова
                #     прийти из WSDiscovery, и это нормально)
                #   - _ui_connected = False → Manager не шлёт deviceDisconnected
                #     (в QML ничего не было добавлено → нечего удалять)
                #   - consumer.stop_all() НЕ вызывается: WS-Eventing подписки ещё
                #     не оформлены (start_all() выполнен, но Subscribe ещё не нужен
                #     т.к. мы не намерены слушать события). На практике sdc11073
                #     уже отправил Subscribe в start_all — поэтому вызываем stop_all()
                #     в блоке finally через _graceful_shutdown() для чистоты.
                #
                # NOTE: если устройство НЕ публикует LocationContext (поле отсутствует
                # или пустое), фильтр пропускает его с предупреждением. Это позволяет
                # подключаться к устройствам, которые ещё не установили локацию
                # (например, только что включились).
                if self.target_room:
                    device_room = self._get_device_room()
                    if device_room == "":
                        # Нет LocationContext → предупреждение, но пропускаем (не фильтруем)
                        self.logger.warning(
                            f"No LocationContext found in MDIB. "
                            f"Room filter (target='{self.target_room}') skipped — accepting device."
                        )
                    elif device_room != self.target_room:
                        self.logger.info(
                            f"Location filter: device room '{device_room}' "
                            f"!= target '{self.target_room}'. Disconnecting (not an error)."
                        )
                        self._location_filtered = True   # → ban-list в Manager'е
                        return  # Выходим чисто — finally вызовет stop_all() через _graceful_shutdown
                    else:
                        self.logger.info(f"Location filter: room '{device_room}' matches. Accepting.")

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

            self.logger.info("Connection established. Monitoring...")

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
                    self.logger.warning("Could not find Main Thread!")

                # Уведомляем UI о появлении нового устройства.
                # Qt Signal автоматически маршалирует вызов в поток получателя (главный).
                # Помечаем _ui_connected ПЕРЕД emit() — это атомарный флаг для Manager'а:
                # он знает, что deviceDisconnected нужно послать при отключении.
                self._ui_connected = True
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
                    self.logger.warning("Connection lost reported by SDC stack.")
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
                            # Пинг успешен — контексты доступны
                            if not getattr(self, '_ctx_ping_ok_logged', False):
                                self.logger.info(
                                    '[DIAG] GetContextStates ping: SUCCESS — '
                                    'device allows context queries without authorization.'
                                )
                                self._ctx_ping_ok_logged = True
                        else:
                            # ContextService вообще не объявлен устройством
                            if not getattr(self, '_no_ctx_svc_logged', False):
                                self.logger.warning(
                                    '[DIAG] No context_service_client — '
                                    'device did not advertise ContextService in metadata.'
                                )
                                self._no_ctx_svc_logged = True
                    missed_heartbeats = 0  # Пинг успешен — сбрасываем счётчик

                except HTTPReturnCodeError as e:
                    # HTTP 4xx означает, что TCP-соединение живо — провайдер ответил,
                    # но отклонил запрос (например, HTTP 400 "unauthorized participant"
                    # от sdcX при --no_tls).  Это НЕ разрыв соединения — сбрасываем счётчик.
                    missed_heartbeats = 0
                    # Логируем один раз, чтобы не засорять вывод.
                    if not getattr(self, '_auth_warn_logged', False):
                        self.logger.warning(
                            f'[DIAG] Ping: provider returned HTTP {e.status} ({e.reason}) — '
                            f'connection alive but GetContextStates is blocked '
                            f'(unauthorized). Context data came via GetMdib only. '
                            f'Suppressing further warnings.'
                        )
                        self._auth_warn_logged = True

                except Exception as e:
                    missed_heartbeats += 1
                    self.logger.warning(f"Ping failed ({missed_heartbeats}/{MAX_MISSED}): {e}")
                    # Проверяем, превышен ли порог T_fallback
                    if missed_heartbeats * SLEEP_INTERVAL >= T_FALLBACK:
                        self.logger.error("T_fallback exceeded. Disconnecting.")
                        self.error_occurred = True
                        break  # Выходим из цикла → поток завершится → Manager удалит устройство

                # Ждём до следующей итерации.
                # await здесь критически важен: он отдаёт управление event loop'у,
                # позволяя обрабатывать другие async-задачи (например, входящие уведомления).
                await asyncio.sleep(SLEEP_INTERVAL)

        except Exception as e:
            # Критическая ошибка на этапе подключения или инициализации.
            self.logger.error(f"Critical Error ({type(e).__name__}): {e}", exc_info=True)
            self.error_occurred = True
        finally:
            # DEV-49: Штатное завершение сессии мониторинга.
            # _graceful_shutdown() отправляет Unsubscribe на все активные подписки
            # с таймаутом 5 секунд, что позволяет прикроватному монитору понять:
            # Оркестратор завершает работу штатно, а не аварийно.
            # Без этого шага устройство может активировать fallback-тревогу (60 dBA).
            if self.consumer:
                self.logger.info("Stopping consumer resources (DEV-49 graceful)...")
                try:
                    await self._graceful_shutdown()
                except Exception as e:
                    # Крайний случай: форсируем закрытие синхронно
                    self.logger.error(f"Graceful shutdown failed ({e}), forcing stop.")
                    try:
                        self.consumer.stop_all()
                    except Exception:
                        pass

    # =========================================================================
    # Вспомогательный метод: чтение комнаты из LocationContext MDIB
    # =========================================================================
    def _get_device_room(self) -> str:
        """
        Reads the device's room identifier from its MDIB LocationContextState.

        Returns:
            The Room string from LocationDetail (e.g. "ICU-3", "OR-1").
            Empty string "" if no LocationContextState is present, or if
            LocationDetail / Room is None.

        Thread safety: MUST be called WITHOUT data_lock held — this method
        acquires data_lock internally for the read and releases it immediately.
        The lock is needed because _worker_logic() may run concurrently with
        sdc11073 notification threads that update context_states.
        """
        try:
            with self.data_lock:
                if not self.mdib:
                    return ""
                loc_states = [
                    s for s in self.mdib.context_states.objects
                    if s.NODETYPE == pm.LocationContextState
                ]
                if loc_states and loc_states[0].LocationDetail:
                    return loc_states[0].LocationDetail.Room or ""
        except Exception as e:
            self.logger.error(f"_get_device_room error: {e}")
        return ""

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
            self.logger.debug("No patient context data, skipping apply_patient_to_mdib.")
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
                    self.logger.warning("MDIB not ready, skipping apply_patient_to_mdib.")
                    return

                # Find PatientContextDescriptor to locate the correct operation
                pat_descriptors = self.mdib.descriptions.NODETYPE.get(pm.PatientContextDescriptor, [])
                if not pat_descriptors:
                    self.logger.warning("No PatientContextDescriptor found.")
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
                    self.logger.warning("No SetContextStateOperation found.")
                    return

                states_to_send = self._build_patient_workflow_states()

        except Exception as e:
            self.logger.error(f"Failed to build patient context states: {e}")
            return

        if not states_to_send:
            self.logger.warning("No states to send (no descriptors on device).")
            return

        # ------------------------------------------------------------------
        # Phase 2: Send SetContextState (lock released)
        # ------------------------------------------------------------------
        try:
            if self.consumer.context_service_client:
                self.logger.info(f"Sending PatientContext+Workflow (op='{operation_handle}')...")
                self.consumer.context_service_client.set_context_state(
                    operation_handle=operation_handle,
                    proposed_context_states=states_to_send
                )
                ctx = self.patient_context
                self.logger.info(
                    f"PatientContext applied: {ctx.get('given_name')} {ctx.get('family_name')}"
                )
                # Log to operation report if logger is active
                logger = getattr(self.manager, 'operation_logger', None)
                if logger:
                    logger.log_context_applied(self.epr, 'PatientContext + WorkflowContext')
            else:
                self.logger.warning("No context_service_client available.")
        except Exception as e:
            self.logger.error(f"Failed to apply patient context: {e}")

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
                    self.logger.warning("No EnsembleContextDescriptor found.")
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
                    self.logger.warning("No SetContextState operation for EnsembleContext.")
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
                self.logger.info(
                    f"Sending {count} context states (Ensemble+Patient+Workflow) in one batch..."
                )
                self.consumer.context_service_client.set_context_state(
                    operation_handle=operation_handle,
                    proposed_context_states=states_to_send
                )
                self.logger.info("All contexts applied successfully.")
                # Log to operation report
                logger = getattr(self.manager, 'operation_logger', None)
                if logger:
                    logger.log_context_applied(self.epr, 'EnsembleContext + Patient + Workflow')
            else:
                self.logger.warning("No context_service_client available.")

        except Exception as e:
            self.logger.error(f"Failed to apply ensemble contexts: {e}")

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
                    self.logger.warning("acknowledge_alarm: consumer or mdib not available.")
                    return
                if not self.consumer.set_service_client:
                    self.logger.warning("acknowledge_alarm: set_service_client not available.")
                    return

                # mk_proposed_state() живёт на mdib.xtra (ConsumerMdibMethods), не на set_service_client.
                # Создаёт копию текущего стейта с handle alert_signal_handle для изменения.
                proposed_state = self.mdib.xtra.mk_proposed_state(alert_signal_handle)

                # Устанавливаем новое значение Presence = ACK
                proposed_state.Presence = pm_types.AlertSignalPresence.ACK

        except Exception as e:
            self.logger.error(f"Failed to prepare alarm acknowledgement: {e}")
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
            self.logger.info(f"Alarm '{alert_signal_handle}' acknowledged successfully.")
        except Exception as e:
            self.logger.error(f"Failed to acknowledge alarm: {e}")

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
            self.logger.info("DEV-49: Unsubscribe completed — device notified.")
        except asyncio.TimeoutError:
            self.logger.warning("DEV-49: Unsubscribe timed out (5s). Forcing close.")
        except Exception as e:
            self.logger.error(f"DEV-49: Error during graceful shutdown: {e}")

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

        self.logger.warning(
            "SDPi-A: SequenceId/InstanceId changed! "
            "Device may have restarted — forcing reconnect to resync MDIB."
        )
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
