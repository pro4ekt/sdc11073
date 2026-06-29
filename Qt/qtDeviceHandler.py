"""
qtDeviceHandler.py — Qt/QML-обёртка над DeviceHandler для одного SDC-устройства.

АРХИТЕКТУРА (мост между рабочим потоком и UI):
  DeviceHandler живёт в рабочем потоке и владеет MDIB устройства.
  QML не может напрямую обращаться к объектам из других потоков.

  Решение — объект QtDeviceHandler (QObject):
    1. Создаётся в рабочем потоке (вместе с DeviceHandler).
    2. Перемещается в главный UI-поток через moveToThread().
    3. Рабочий поток вызывает scheduleUpdate() → Qt-сигнал → handleUpdateTick()
       выполняется в главном потоке → update_data() читает MDIB.

  Результат: QML видит только чистые свойства (str, list) без знания о потоках.

ПРИНЦИП РАБОТЫ СИГНАЛОВ Qt (для справки):
  Signal() — декларация сигнала на уровне класса.
  emit()   — испускание сигнала (потокобезопасно).
  При пересечении границы потоков Qt автоматически ставит вызов в очередь
  того потока, в котором живёт объект-получатель (Qt::QueuedConnection).
"""

import time
from decimal import Decimal, ROUND_HALF_UP

from PySide6.QtCore import QObject, Signal, Slot, Property

# pm — QName-имена типов BICEPS/SDC для фильтрации объектов MDIB
from sdc11073.xml_types import pm_qnames as pm

# pm_types — Python-классы с enum'ами и структурами BICEPS
# Нужны для сравнения Presence (AlertSignalPresence.ON/ACK/LATCH/OFF)
from sdc11073.xml_types import pm_types

from typing import TYPE_CHECKING

# TYPE_CHECKING = True только при статическом анализе (mypy/PyCharm).
# Позволяет использовать DeviceHandler в аннотациях, не создавая циклического импорта.
if TYPE_CHECKING:
    from deviceHandler import DeviceHandler


class QtDeviceHandler(QObject):
    """
    Qt-обёртка над DeviceHandler для одного SDC-устройства.

    Предоставляет QML:
      - Свойства (Property): patientName, patientRoom, deviceName, deviceValue,
                             alarmStatus, priority, metrics, operations, epr
      - Сигналы изменения каждого свойства (notify-сигналы для QML-биндингов)
      - Слот acknowledgeAlarm() для квитирования тревог из UI

    Потокобезопасность обеспечивается:
      - Чтением MDIB только через data_lock (неблокирующий acquire)
      - Передачей обновлений через updateTick Signal (авто-маршалинг Qt)
    """

    # =========================================================================
    # Сигналы изменения свойств (notify-сигналы для QML Property bindings)
    # =========================================================================
    # Каждый сигнал соответствует одному @Property и испускается в update_data()
    # при изменении значения свойства. QML-элементы подписаны автоматически через binding.

    patientNameChanged  = Signal()   # Имя пациента изменилось
    patientRoomChanged  = Signal()   # Палата/комната пациента изменилась
    deviceNameChanged   = Signal()   # Название устройства изменилось
    deviceValueChanged  = Signal()   # Отображаемое значение метрики изменилось
    alarmStatusChanged  = Signal()   # Статус тревоги изменился (Off/On/Ack/Latch/COMM_FAILURE)
    priorityChanged     = Signal()   # Приоритет устройства изменился
    metricsChanged      = Signal()   # Список метрик обновился
    operationsChanged   = Signal()   # Список доступных операций обновился
    eprChanged          = Signal()   # EPR (ID устройства) изменился (теоретически не меняется)
    connectedChanged    = Signal()   # Статус подключения изменился (резерв)

    # Внутренний сигнал для "переброса" обновления между потоками.
    # Рабочий поток испускает updateTick → Qt ставит вызов в очередь главного потока
    # → handleUpdateTick() выполняется в главном потоке → update_data() читает MDIB.
    updateTick = Signal()

    def __init__(self, device: 'DeviceHandler'):
        """
        Параметры:
          device — ссылка на DeviceHandler (рабочий поток устройства).
                   Используется для доступа к MDIB и consumer в update_data().
        """
        super().__init__()

        # Храним ссылку на DeviceHandler.
        # В production-коде лучше использовать weakref, чтобы избежать
        # удержания DeviceHandler в памяти после его остановки.
        self._device = device

        # Начальные значения свойств (показываются до первого успешного update_data())
        self._patientRoom  = "Unknown"
        self._patientName  = "Unknown"
        self._deviceName   = "SDC Device"
        self._deviceValue  = "---"
        self._alarmStatus  = ""
        self._priority     = "3"
        self._metrics      = []   # Список dict'ов: {descriptor, state, metricname, value, samples, alarm}
        self._operations   = []   # Список dict'ов: {name, handle, mode, type}

        # Подключаем внутренний сигнал: любой вызов emit() из любого потока
        # автоматически вызовет handleUpdateTick() в потоке, которому принадлежит этот объект.
        self.updateTick.connect(self.handleUpdateTick)

        # Первичное чтение данных (синхронно, прямо при создании объекта).
        # В этот момент объект ещё в рабочем потоке, но MDIB уже готов (init_mdib завершён).
        self.update_data()

    # =========================================================================
    # Потокобезопасный запрос обновления из рабочего потока
    # =========================================================================
    def scheduleUpdate(self):
        """
        Вызывается из рабочего потока DeviceHandler в каждой итерации цикла мониторинга.
        Испускает updateTick — Qt автоматически маршалирует его в главный поток.
        НЕ выполняет никакой работы напрямую — только сигнализирует.
        """
        self.updateTick.emit()

    @Slot()
    def handleUpdateTick(self):
        """
        Слот, вызываемый в ГЛАВНОМ ПОТОКЕ при получении updateTick.
        Декоратор @Slot() — явная регистрация как Qt-слот (нужно для корректной маршалинги).
        """
        self.update_data()

    # =========================================================================
    # Основной метод чтения MDIB и обновления свойств
    # =========================================================================
    def update_data(self):
        """
        Читает актуальные данные из MDIB устройства и обновляет все Qt-свойства.

        ВЫЗЫВАЕТСЯ: в главном потоке (через updateTick Signal).
        БЛОКИРОВКА: использует неблокирующий acquire(blocking=False) на data_lock.
                    Если рабочий поток занят (держит лок) — пропускаем этот кадр,
                    чтобы не зависать в UI.

        ПОРЯДОК ОБНОВЛЕНИЙ:
          1. LocationContext → patientRoom
          2. PatientContext  → patientName
          3. Имя устройства  → deviceName
          4. Матрица тревог  → alarmStatus (On > Ack > Latch > Off)
          5. SelfCheckPeriod → COMM_FAILURE если AlertSystem молчит слишком долго
          6. EpochSupport    → clock_offset_sec из ClockState для коррекции таймстемпов
          7. Метрики         → metrics (с округлением по StepWidth, с timestamp_ms)
          8. Операции        → operations
          9. Главное значение → deviceValue (первая тревожная метрика или последняя)
        """
        if not self._device:
            return

        # Пытаемся захватить лок без блокировки.
        # Если рабочий поток сейчас пишет в MDIB — возвращаемся; следующий updateTick всё обновит.
        if hasattr(self._device, 'data_lock'):
            if not self._device.data_lock.acquire(blocking=False):
                return  # Пропускаем кадр — MDIB занят
        else:
            # data_lock ещё не создан (гонка при инициализации) — пропускаем
            return

        try:
            if not self._device.mdib:
                return  # MDIB ещё не инициализирован (init_mdib не завершён)

            # ------------------------------------------------------------------
            # 1. КОНТЕКСТ МЕСТОПОЛОЖЕНИЯ (LocationContext)
            # ------------------------------------------------------------------
            # LocationContextState содержит данные о палате, корпусе, кровати пациента.
            # Используем list comprehension вместо NODETYPE.get() для совместимости
            # с разными версиями sdc11073 (некоторые не индексируют context_states по NODETYPE).
            locations = [l for l in self._device.mdib.context_states.objects
                         if l.NODETYPE == pm.LocationContextState]
            patients  = [p for p in self._device.mdib.context_states.objects
                         if p.NODETYPE == pm.PatientContextState]

            if locations and locations[0].LocationDetail:
                # Room может быть None или пустой строкой — заменяем на "Unknown"
                self._patientRoom = locations[0].LocationDetail.Room or "Unknown"

            # ------------------------------------------------------------------
            # 2. КОНТЕКСТ ПАЦИЕНТА (PatientContext)
            # ------------------------------------------------------------------
            # CoreData.Givenname = имя, CoreData.Familyname = фамилия.
            # ВАЖНО: Birthname (девичья фамилия) — это не то же самое, что Familyname!
            if patients and patients[0].CoreData:
                given  = patients[0].CoreData.Givenname  or ""
                family = patients[0].CoreData.Familyname or ""
                # strip() убирает лишние пробелы если одно из полей пустое
                self._patientName = f"{given} {family}".strip() or "Unknown"

            # ------------------------------------------------------------------
            # 3. ИМЯ УСТРОЙСТВА (приоритетная цепочка)
            # ------------------------------------------------------------------
            # Попытка 1: DPWS FriendlyName — самое человекочитаемое имя
            # Попытка 2: MDIB MdsDescriptor.ModelName — модель устройства
            # Попытка 3: MDIB MdsDescriptor.Type.localname — технический тип

            name_candidate = "SDC Device"  # Fallback по умолчанию

            try:
                # host_description — DPWS-метаданные провайдера
                # this_device.FriendlyName — список локализованных строк, берём первую
                name_candidate = self._device.consumer.host_description.this_device.FriendlyName[0].text
            except Exception:
                pass  # FriendlyName может отсутствовать — не страшно

            if name_candidate == "SDC Device":
                # MdsDescriptor — корневой дескриптор устройства в иерархии BICEPS
                mds_descriptors = [d for d in self._device.mdib.descriptions.objects
                                   if d.NODETYPE == pm.MdsDescriptor]
                if mds_descriptors:
                    mds = mds_descriptors[0]
                    if mds.ModelName:
                        name_candidate = mds.ModelName[0].text
                    elif mds.Type:
                        # localname — локальная часть XML QName (без namespace)
                        name_candidate = mds.Type.localname

            # Обновляем свойство и испускаем сигнал только при изменении (оптимизация)
            if self._deviceName != name_candidate:
                self._deviceName = name_candidate
                self.deviceNameChanged.emit()

            # ------------------------------------------------------------------
            # 4. ЛОГИКА ТРЕВОГ (Alert Logic)
            # ------------------------------------------------------------------
            # active_alert_handles — set handle'ов метрик, на которые есть активная тревога.
            # Используется далее при построении списка метрик для пометки тревожных.
            active_alert_handles = set()
            new_alarm_status = "Off"  # Начальный статус — тревог нет

            # Собираем все AlertSignalState из MDIB
            alert_signals = [
                s for s in self._device.mdib.states.objects
                if s.NODETYPE == pm.AlertSignalState
            ]

            # ------------------------------------------------------------------
            # 4a. Матрица приоритетов тревог (BICEPS AlertSignalPresence)
            # ------------------------------------------------------------------
            # Стандарт BICEPS определяет 4 значения Presence у AlertSignalState:
            #   On    (3) — тревога активна, звук + индикация включены
            #   Ack   (2) — пользователь квитировал: звук отключён, индикация остаётся
            #   Latch (1) — причина устранена, но требуется ручной сброс
            #   Off   (0) — тревога неактивна
            #
            # Итерируем ВСЕ сигналы и выбираем НАИВЫСШИЙ приоритет.
            # Нельзя останавливаться на первом "On" — нужно проверить все сигналы.
            _ALARM_PRIORITY = {
                str(pm_types.AlertSignalPresence.ON):    3,
                str(pm_types.AlertSignalPresence.ACK):   2,
                str(pm_types.AlertSignalPresence.LATCH): 1,
                str(pm_types.AlertSignalPresence.OFF):   0,
            }
            highest_priority = 0

            for s in alert_signals:
                presence_str = str(s.Presence)
                priority = _ALARM_PRIORITY.get(presence_str, 0)
                if priority > highest_priority:
                    highest_priority = priority
                    # Сохраняем строковое значение напрямую ('On', 'Ack', 'Latch', 'Off')
                    new_alarm_status = presence_str

            # ------------------------------------------------------------------
            # 4b. Alert Conditions → источники тревог (для подсветки метрик)
            # ------------------------------------------------------------------
            # AlertConditionState.Presence = True означает, что условие тревоги выполнено
            # (например, значение вышло за допустимый диапазон).
            # У каждого AlertConditionDescriptor есть поле Source — список handle'ов метрик,
            # которые "провоцируют" эту тревогу. Собираем их для подсветки в UI.
            alert_condition_types = [pm.AlertConditionState, pm.LimitAlertConditionState]
            active_conditions = [
                s for s in self._device.mdib.states.objects
                if s.NODETYPE in alert_condition_types and getattr(s, 'Presence', False)
            ]

            for alert in active_conditions:
                alert_desc = self._device.mdib.descriptions.handle.get_one(
                    alert.DescriptorHandle, allow_none=True
                )
                # Source — список handle'ов метрик, которые мониторит эта тревога
                if alert_desc and hasattr(alert_desc, 'Source') and alert_desc.Source:
                    for source_handle in alert_desc.Source:
                        active_alert_handles.add(source_handle)

            # ------------------------------------------------------------------
            # EpochSupport: поправка на расхождение часов устройства
            # ------------------------------------------------------------------
            # Вычисляем ДО SelfCheck — чтобы определить, надёжны ли таймстемпы.
            clock_offset_sec = 0.0
            try:
                clock_states_list = [s for s in self._device.mdib.states.objects
                                     if s.NODETYPE == pm.ClockState]
                if clock_states_list:
                    clock_state = clock_states_list[0]

                    remote_sync = getattr(clock_state, 'RemoteSync', True)
                    if not remote_sync and not getattr(self, '_clock_nosync_warned', False):
                        self._clock_nosync_warned = True
                        self._device.logger.warning(
                            "Device clock NOT NTP-synced (RemoteSync=False). "
                            "Timestamp correction may be inaccurate."
                        )

                    device_time = getattr(clock_state, 'DateAndTime', None)
                    if device_time is not None:
                        clock_offset_sec = float(device_time) - time.time()
                        # Расхождение > 60с — предупреждаем ОДИН РАЗ (подавляем спам)
                        if abs(clock_offset_sec) > 60.0:
                            if not getattr(self, '_clock_offset_warned', False):
                                self._clock_offset_warned = True
                                self._device.logger.warning(
                                    f"Large clock offset detected ({clock_offset_sec:+.1f}s). "
                                    f"Device NTP may be misconfigured. "
                                    f"SelfCheck validation disabled. Suppressing further warnings."
                                )
                            # Если расхождение огромное — не корректируем таймстемпы
                            if abs(clock_offset_sec) >= 3600.0:
                                clock_offset_sec = 0.0
            except Exception:
                pass  # ClockState недоступен — offset=0, работаем без поправки

            # ------------------------------------------------------------------
            # 4c. SelfCheckPeriod validation (IHE SDPi / BICEPS)
            # ------------------------------------------------------------------
            # AlertSystemDescriptor.SelfCheckPeriod — ожидаемый интервал самодиагностики (секунды).
            # AlertSystemState.LastSelfCheck — Unix-timestamp последней самодиагностики (секунды).
            #
            # Если текущее время превышает LastSelfCheck + SelfCheckPeriod * 1.5 →
            # AlertSystem молчит слишком долго → переводим устройство в COMM_FAILURE.
            # Коэффициент 1.5 даёт допуск на задержки сети (50% сверх нормы).
            #
            # ВАЖНО: все сравнения ведём в СЕКУНДАХ (не в миллисекундах).
            # LastSelfCheck — pm:Timestamp = float (Unix секунды) в sdc11073.
            _SELF_CHECK_MULTIPLIER = 1.5
            # Если разрыв часов с устройством превышает порог — SelfCheck не валиден
            # (нельзя доверять таймштемпам устройства; не объявляем COMM_FAILURE зазря).
            _CLOCK_SKEW_SKIP_THRESHOLD = 3600.0  # 1 час — считаем часы устройства ненадёжными
            alert_system_state_list = [
                s for s in self._device.mdib.states.objects
                if s.NODETYPE == pm.AlertSystemState
            ]
            for als in alert_system_state_list:
                als_desc = self._device.mdib.descriptions.handle.get_one(
                    als.DescriptorHandle, allow_none=True
                )
                if als_desc and als_desc.SelfCheckPeriod and als.LastSelfCheck:
                    # Пропускаем проверку если часы устройства сильно расходятся
                    if abs(clock_offset_sec) >= _CLOCK_SKEW_SKIP_THRESHOLD:
                        break
                    # Сравниваем в секундах (LastSelfCheck уже в секундах Unix)
                    deadline_s = als_desc.SelfCheckPeriod * _SELF_CHECK_MULTIPLIER
                    current_time_s = time.time()
                    if current_time_s - float(als.LastSelfCheck) > deadline_s:
                        new_alarm_status = "COMM_FAILURE"
                        break  # Достаточно одного нарушения

            # Обновляем свойство alarmStatus только при реальном изменении
            if self._alarmStatus != new_alarm_status:
                self._alarmStatus = new_alarm_status
                self.alarmStatusChanged.emit()
            # --- ALARM LOGIC END ---

            # ------------------------------------------------------------------
            # 5. СПИСОК МЕТРИК (Metrics)
            # ------------------------------------------------------------------
            # Собираем все NumericMetricState, StringMetricState, RealTimeSampleArrayMetricState.
            # Для каждого формируем dict с данными для QML.
            metric_types = [
                pm.NumericMetricState,
                pm.StringMetricState,
                pm.RealTimeSampleArrayMetricState
            ]
            metric_states = [m for m in self._device.mdib.states.objects
                             if m.NODETYPE in metric_types]

            new_metrics_list = []

            for state in metric_states:
                # Дескриптор содержит метаданные: handle, единицы измерения, диапазон и т.д.
                descriptor = self._device.mdib.descriptions.handle.get_one(state.DescriptorHandle)

                # handle дескриптора используется как имя метрики в UI
                metric_name    = descriptor.Handle
                metric_value   = "---"   # Значение по умолчанию (нет данных)
                metric_samples = []      # Для waveform-метрик: список float-значений
                # Таймстемп последнего батча данных, скорректированный на clock_offset_sec.
                # None — если DeterminationTime отсутствует в MetricValue.
                metric_timestamp_ms = None

                # Проверяем, является ли эта метрика источником активной тревоги
                if descriptor.Handle in active_alert_handles:
                    # Передаем актуальный статус сигнала (On, Ack или Latch) вместо жесткого "On".
                    # Если глобальный статус COMM_FAILURE или Off, всё равно ставим "On",
                    # так как физиологическое условие (Condition) нарушено.
                    metric_alarm = new_alarm_status if new_alarm_status in ["On", "Ack", "Latch"] else "On"
                else:
                    metric_alarm = "Off"

                try:
                    if state.NODETYPE == pm.RealTimeSampleArrayMetricState:
                        # RealTime waveform — нет скалярного Value, только массив Samples
                        metric_value = "Waveform"
                        if state.MetricValue and state.MetricValue.Samples:
                            # Samples — список Decimal → конвертируем в float для QML
                            metric_samples = [float(x) for x in state.MetricValue.Samples]
                        # EpochSupport: корректируем DeterminationTime батча на смещение часов.
                        # DeterminationTime — таймстемп последнего sample в секундах (Unix).
                        if state.MetricValue:
                            det_time = getattr(state.MetricValue, 'DeterminationTime', None)
                            if det_time is not None:
                                metric_timestamp_ms = (float(det_time) + clock_offset_sec) * 1000

                    elif state.MetricValue:
                        # NumericMetric и StringMetric имеют поле Value
                        # getattr с default None защищает от AttributeError
                        val = getattr(state.MetricValue, 'Value', None)
                        if val is not None:
                            # --------------------------------------------------
                            # ОКРУГЛЕНИЕ ПО StepWidth (только для NumericMetric)
                            # --------------------------------------------------
                            # NumericMetricDescriptor.TechnicalRange[0].StepWidth —
                            # шаг допустимых значений (например, Decimal('0.1') для одного знака).
                            # quantize() округляет val до нужного числа знаков после запятой.
                            # ROUND_HALF_UP: 120.005 при step=0.1 → 120.0 (стандартное медицинское округление)
                            try:
                                if (state.NODETYPE == pm.NumericMetricState and
                                        hasattr(descriptor, 'TechnicalRange') and
                                        descriptor.TechnicalRange and
                                        descriptor.TechnicalRange[0].StepWidth is not None):
                                    step = descriptor.TechnicalRange[0].StepWidth
                                    val = Decimal(str(val)).quantize(step, rounding=ROUND_HALF_UP)
                            except Exception:
                                pass  # Если округление не удалось — используем исходное значение
                            metric_value = str(val)
                        # EpochSupport: применяем поправку и к скалярным метрикам
                        det_time = getattr(state.MetricValue, 'DeterminationTime', None)
                        if det_time is not None:
                            metric_timestamp_ms = (float(det_time) + clock_offset_sec) * 1000

                except Exception:
                    pass  # Ошибка конверсии — оставляем "---"

                new_metrics_list.append({
                    "descriptor":    descriptor,         # Сырой объект дескриптора (для доп. обработки в QML)
                    "state":         state,              # Сырой объект стейта
                    "metricname":    metric_name,        # Строка: handle метрики (для отображения)
                    "value":         metric_value,       # Строка: текущее значение (или "---"/"Waveform")
                    "samples":       metric_samples,     # list[float]: для графика waveform
                    "alarm":         metric_alarm,       # "On" / "Off": есть ли тревога по этой метрике
                    "timestamp_ms":  metric_timestamp_ms # float | None: скорректированный таймстемп (мс)
                })

            self._metrics = new_metrics_list
            self.metricsChanged.emit()

            # ------------------------------------------------------------------
            # 6. СПИСОК ОПЕРАЦИЙ (Operations)
            # ------------------------------------------------------------------
            # Операции — это действия, которые Consumer может выполнить на Provider'е:
            # SetValue, SetString, Activate, SetContextState и т.д.
            # Ищем по стейтам операций (не по дескрипторам) — это надёжнее,
            # так как стейт существует только для реально активных операций.
            op_state_types = [
                pm.SetValueOperationState,         # Установка числового значения
                pm.SetStringOperationState,        # Установка строкового значения
                pm.ActivateOperationState,         # Активация команды (без параметров)
                pm.SetContextStateOperationState,  # Изменение контекста
                pm.SetMetricStateOperationState,   # Изменение состояния метрики
                pm.SetAlertStateOperationState,    # Изменение состояния тревоги
                pm.SetComponentStateOperationState # Изменение состояния компонента
            ]

            op_states = [s for s in self._device.mdib.states.objects
                         if s.NODETYPE in op_state_types]

            new_ops = []
            for state in op_states:
                d = self._device.mdib.descriptions.handle.get_one(
                    state.DescriptorHandle, allow_none=True
                )
                if not d:
                    continue  # Дескриптор исчез (например, при динамическом обновлении MDIB)

                op_name = d.Handle  # Fallback: используем handle как имя

                # Ищем человекочитаемое имя в Type.ConceptDescription или Type.Code
                if d.Type:
                    txt = None
                    if hasattr(d.Type, 'ConceptDescription') and d.Type.ConceptDescription:
                        txt = d.Type.ConceptDescription[0].text  # Предпочтительный вариант
                    if not txt and hasattr(d.Type, 'Code'):
                        txt = d.Type.Code  # Технический код как запасной вариант
                    if txt:
                        op_name = txt

                # OperatingMode: Enabled / Disabled / NA — доступна ли операция сейчас
                mode = "Enabled"
                if state.OperatingMode:
                    mode = str(state.OperatingMode)

                new_ops.append({
                    "name":   op_name,                    # Человекочитаемое имя операции
                    "handle": d.Handle,                   # Handle для вызова операции
                    "mode":   mode,                       # Доступность: Enabled/Disabled
                    "type":   str(d.NODETYPE.localname)   # Тип: SetValueOperation и т.д.
                })

            self._operations = new_ops
            self.operationsChanged.emit()

            # ------------------------------------------------------------------
            # 7. ГЛАВНОЕ ОТОБРАЖАЕМОЕ ЗНАЧЕНИЕ (deviceValue)
            # ------------------------------------------------------------------
            # Логика выбора:
            #   Если есть тревожная метрика → показываем первую (наиболее критичную)
            #   Иначе → показываем последнюю метрику в списке (самую "свежую")
            display_val = "---"

            alarming_metric = next(
                (m for m in new_metrics_list if m["alarm"] == "On"), None
            )

            if alarming_metric:
                display_val = f"{alarming_metric['metricname']}: {alarming_metric['value']}"
            elif new_metrics_list:
                last_mt = new_metrics_list[-1]
                display_val = f"{last_mt['metricname']}: {last_mt['value']}"

            if self._deviceValue != display_val:
                self._deviceValue = display_val
                self.deviceValueChanged.emit()

        except Exception as e:
            self._device.logger.error(f"update_data error: {e}", exc_info=True)
        finally:
            # ОБЯЗАТЕЛЬНО освобождаем лок, даже если возникло исключение.
            # Без этого рабочий поток навсегда заблокируется при следующей попытке
            # взять data_lock (дедлок).
            if hasattr(self._device, 'data_lock'):
                self._device.data_lock.release()

    # =========================================================================
    # Qt Properties — свойства, доступные из QML
    # =========================================================================
    # Синтаксис: @Property(тип, notify=сигнал)
    # notify= указывает QML-движку, какой сигнал означает "значение изменилось".
    # QML автоматически перерисует все binding'и при получении этого сигнала.

    @Property(str, notify=patientNameChanged)
    def patientName(self):
        """Имя пациента: '{Givenname} {Familyname}' или 'Unknown'."""
        return self._patientName

    @Property(str, notify=patientRoomChanged)
    def patientRoom(self):
        """Комната/палата пациента из LocationContextState."""
        return self._patientRoom

    @Property(str, notify=eprChanged)
    def epr(self):
        """
        EPR (Endpoint Reference) — уникальный UUID устройства в сети.
        Используется QML как уникальный ключ для идентификации устройства в списке.
        """
        return self._device.epr if self._device else ""

    @Property(str, notify=deviceNameChanged)
    def deviceName(self):
        """Название устройства (DPWS FriendlyName → ModelName → Type → 'SDC Device')."""
        return self._deviceName

    @Property(str, notify=deviceValueChanged)
    def deviceValue(self):
        """
        Главное значение для отображения на карточке устройства.
        Формат: 'handle: value'. Приоритет — первая тревожная метрика.
        """
        return self._deviceValue

    @Property(list, notify=metricsChanged)
    def metrics(self):
        """
        Список метрик устройства.
        Каждый элемент: dict {descriptor, state, metricname, value, samples, alarm, timestamp_ms}.
          timestamp_ms — таймстемп последнего батча в миллисекундах, скорректированный
                         на смещение часов устройства (EpochSupport).
                         None если MetricValue.DeterminationTime отсутствует.
        QML может итерировать этот список для отображения таблицы метрик и графиков.
        """
        return self._metrics

    @Property(str, notify=alarmStatusChanged)
    def alarmStatus(self):
        """
        Глобальный статус тревоги устройства.
        Возможные значения: 'Off', 'On', 'Ack', 'Latch', 'COMM_FAILURE'.
        QML использует это для цветовой индикации карточки устройства.
        """
        return self._alarmStatus

    @Property(str, notify=priorityChanged)
    def priority(self):
        """
        Приоритет устройства в списке (число в виде строки, '1' = высший).
        Сейчас всегда '3' — резерв для будущей сортировки.
        """
        return self._priority

    @Property(list, notify=operationsChanged)
    def operations(self):
        """
        Список доступных операций на устройстве.
        Каждый элемент: dict {name, handle, mode, type}.
        QML отображает их как кнопки управления.
        """
        return self._operations
