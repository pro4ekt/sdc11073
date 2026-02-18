from __future__ import annotations

import sys
import asyncio
import socket
import threading
import os

# 1. Сначала imports Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtCore import QObject, Signal, Slot, Property, QThread

# 2. Потом imports логики
from sdc11073.consumer import SdcConsumer
from sdc11073.mdib import ConsumerMdib
from sdc11073.wsdiscovery import WSDiscovery
from sdc11073.xml_types import pm_qnames as pm


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


# ==========================================
# ЧАСТЬ 1: ЧИСТЫЕ ДАННЫЕ ДЛЯ QML
# ==========================================
class QtDeviceHandler(QObject):
    """
    Этот класс живет в памяти. Его единственная задача - хранить цифры
    и сообщать QML, если цифры изменились.
    Никакой сетевой логики тут нет.
    """

    # Сигналы уведомления (Notify)
    patientNameChanged = Signal()
    pulseChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._patientName = "Searching..."
        self._pulse = "--"
        self._epr = ""  # Уникальный ID

    # --- Свойства (Properties), которые читает QML ---
    @Property(str, notify=patientNameChanged)
    def patientName(self): return self._patientName

    @Property(str, notify=pulseChanged)
    def pulse(self): return self._pulse

    @Property(str, constant=True)
    def epr(self): return self._epr

    # --- Методы для обновления (вызываются извне) ---
    def set_identity(self, epr, name):
        self._epr = epr
        self._patientName = name
        self.patientNameChanged.emit()

    def update_pulse(self, value):
        if self._pulse != str(value):
            self._pulse = str(value)
            self.pulseChanged.emit()


# ==========================================
# ЧАСТЬ 2: РАБОТЯГА (WORKER)
# ==========================================
class DeviceWorker(threading.Thread):
    """
    Этот класс живет в фоне. Его задача - грязная работа с сетью.
    Он ничего не знает про UI. Он просто дергает методы QtDeviceHandler.
    """

    def __init__(self, service, manager, qt_handler):
        super().__init__(daemon=True)
        self.service = service
        self.manager = manager
        self.qt_handler = qt_handler  # Ссылка на наш Qt объект
        self.epr = str(service.epr).strip()
        self.running = True

    def run(self):
        # Изоляция потока asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._logic())
        finally:
            loop.close()
            # Если поток умер - убираем устройство
            self.manager.remove_device_safe(self.epr)

    async def _logic(self):
        print(f"[Worker {self.epr}] Connecting...")
        # 1. Подключение
        consumer = SdcConsumer.from_wsd_service(self.service, ssl_context_container=None)
        consumer.start_all()
        mdib = ConsumerMdib(consumer)
        mdib.init_mdib()

        # 2. Первичное чтение данных
        # (Тут упрощенно, в реальности парсим MDIB)
        patient_name = "John Doe"
        try:
            pat_state = [p for p in mdib.context_states.objects if p.NODETYPE == pm.PatientContextState]
            if pat_state and pat_state[0].CoreData:
                patient_name = pat_state[0].CoreData.Birthname or "Unknown"
        except:
            pass

        # 3. Обновляем Qt объект (это потокобезопасно через QObject)
        self.qt_handler.set_identity(self.epr, patient_name)

        # 4. СООБЩАЕМ МЕНЕДЖЕРУ, ЧТО МЫ ГОТОВЫ ПОКАЗАТЬСЯ В UI
        # (Менеджер сам перешлет сигнал в QML)
        self.manager.on_worker_ready(self.qt_handler)

        print(f"[Worker {self.epr}] Ready. Loop start.")

        # 5. Цикл жизни
        import random
        while self.running:
            if not consumer.is_connected:
                break

            # Эмуляция данных
            new_val = random.randint(60, 100)
            self.qt_handler.update_pulse(new_val)

            await asyncio.sleep(1)

        consumer.stop_all()

    def stop(self):
        self.running = False


# ==========================================
# ЧАСТЬ 3: МЕНЕДЖЕР (ГЛАВНЫЙ)
# ==========================================
class SdcManager(QObject):
    """
    Связующее звено.
    1. Ищет устройства.
    2. Создает пару (Worker + QtHandler).
    3. Отдает QtHandler в QML.
    """

    # Сигналы для QML
    deviceAdded = Signal(QtDeviceHandler, arguments=['device'])
    deviceRemoved = Signal(str, arguments=['epr'])

    def __init__(self):
        super().__init__()
        self.running = True
        self.workers = {}  # {epr: WorkerThread}
        self.discovery_thread = threading.Thread(target=self._discovery_run, daemon=True)

    def start(self):
        self.discovery_thread.start()

    def cleanup(self):
        self.running = False
        for w in self.workers.values():
            w.stop()

    # --- Логика Discovery (Фон) ---
    def _discovery_run(self):
        asyncio.run(self._async_discovery())

    async def _async_discovery(self):
        my_ip = get_local_ip()
        wsd = WSDiscovery(my_ip)
        wsd.start()

        while self.running:
            try:
                services = await asyncio.to_thread(wsd.search_services, timeout=2)
                current_eprs = []

                for srv in services:
                    epr = str(srv.epr).strip()
                    current_eprs.append(epr)

                    if epr not in self.workers:
                        # === РОЖДЕНИЕ УСТРОЙСТВА ===
                        print(f"[Manager] New device found: {epr}")

                        # 1. Создаем пустую оболочку для QML
                        q_handler = QtDeviceHandler()

                        # 2. Создаем рабочего, даем ему эту оболочку
                        worker = DeviceWorker(srv, self, q_handler)

                        # 3. Сохраняем и запускаем
                        self.workers[epr] = worker
                        worker.start()

                await asyncio.sleep(1)
            except Exception as e:
                print(f"Discovery error: {e}")
                await asyncio.sleep(2)

        wsd.stop()

    # --- Обратные вызовы (Callbacks) ---

    def on_worker_ready(self, qt_handler):
        # Worker вызывает это, когда подключился и заполнил имя пациента
        # Сигнал deviceAdded уйдет в QML
        self.deviceAdded.emit(qt_handler)

    def remove_device_safe(self, epr):
        # Worker вызывает это перед смертью
        if epr in self.workers:
            del self.workers[epr]
            # Говорим QML удалить квадратик
            self.deviceRemoved.emit(epr)


# ==========================================
# MAIN
# ==========================================
if __name__ == "__main__":
    # 1. СНАЧАЛА ЯДРО QT
    app = QGuiApplication(sys.argv)

    # 2. ПОТОМ МЕНЕДЖЕР
    manager = SdcManager()

    # 3. ПОТОМ ДВИЖОК QML
    engine = QQmlApplicationEngine()

    # Связываем Python -> QML
    engine.rootContext().setContextProperty("sdcManager", manager)

    # Загружаем интерфейс
    qml_file = os.path.join(os.path.dirname(__file__), "Main.qml")
    engine.load(qml_file)

    if not engine.rootObjects():
        sys.exit(-1)

    # 4. ЗАПУСКАЕМ ЛОГИКУ ПОИСКА
    manager.start()

    # 5. ЗАПУСКАЕМ APP LOOP
    print("System running...")
    exit_code = app.exec()

    # Чистим за собой
    manager.cleanup()
    sys.exit(exit_code)
