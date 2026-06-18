"""
main.py — Точка входа приложения SDC-консьюмера с Qt/QML интерфейсом.

ПОСЛЕДОВАТЕЛЬНОСТЬ ЗАПУСКА:
  1. Пользователь вводит Patient ID (FHIR ID)
  2. Загружаются данные пациента из FHIR-сервера (имя, диагнозы, рост, вес)
  3. Создаётся SdcMyConsumer (Manager) с данными пациента
  4. Manager запускает фоновый поток сканирования сети (WSDiscovery)
  5. Создаётся Qt-приложение и QML-движок
  6. Manager и FHIRData регистрируются в QML-контексте (доступны в .qml файлах)
  7. Загружается Main.qml — точка входа UI
  8. Запускается Qt event loop (app.exec()) — блокирует поток до закрытия окна

ПОТОКИ ПОСЛЕ ЗАПУСКА:
  - Главный поток: Qt event loop (обрабатывает UI, сигналы, слоты)
  - discovery_thread: asyncio loop для WSDiscovery и управления воркерами
  - DeviceHandler потоки: по одному на каждое найденное SDC-устройство
"""

from __future__ import annotations

import sys
import os

from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

# Manager — координатор сети SDC-устройств
from sdcMyConsumer import SdcMyConsumer

# FHIRPatientData — обёртка для работы с FHIR R4 REST API
from fhirData import FHIRPatientData

# --- Закомментированные импорты (старый монолитный код, заменён модульной архитектурой) ---
"""
import sdc11073
import asyncio
import socket
import threading
import time
from PySide6.QtCore import QObject, Signal, Slot, Property
from GateWay import sdc_opc_gateway
from sdc11073.consumer import SdcConsumer
from sdc11073.mdib import ConsumerMdib
from sdc11073.xml_types.actions import periodic_actions
from sdc11073.mdib.statecontainers import LocationContextStateContainer
from sdc11073.wsdiscovery import WSDiscovery
from sdc11073.xml_types import pm_qnames as pm
from sdc11073.xml_types.pm_qnames import LocationContextState
from sdc11073 import observableproperties
from sdc11073.xml_types.pm_types import AlertSignalPresence, AlertActivation
"""

if __name__ == "__main__":

    # ------------------------------------------------------------------
    # ШАГ 1: Загрузка данных пациента из FHIR
    # ------------------------------------------------------------------
    # Пользователь вводит FHIR Patient ID (например, "12345" или UUID).
    # В реальном приложении этот ID может приходить из ЭМК/HIS системы.
    patient_id = input("Введите ID пациента и нажмите Enter: ").strip()

    fhir = FHIRPatientData()
    try:
        # fetch() делает HTTP-запросы к FHIR-серверу:
        #   GET /Patient/{id}
        #   GET /Condition?patient={id}
        #   GET /Observation?patient={id}
        fhir.fetch(patient_id)
        fhir.print_summary()  # Выводит краткую сводку в консоль для проверки
    except Exception as e:
        print(f"Ошибка загрузки данных пациента: {e}")
        # Не выходим из программы — приложение может работать и без FHIR-данных

    # ------------------------------------------------------------------
    # ШАГ 2: Создание Manager'а и запуск сканирования сети
    # ------------------------------------------------------------------
    # Manager создаётся ДО QGuiApplication — он не требует Qt event loop для старта.
    # Передаём fhir-данные: Manager передаст их каждому DeviceHandler при создании.
    manager = SdcMyConsumer(fhir_data=fhir)
    manager.start()  # Запускает discovery_thread (WSDiscovery + asyncio loop)

    # ------------------------------------------------------------------
    # ШАГ 3: Инициализация Qt-приложения
    # ------------------------------------------------------------------
    # QGuiApplication — базовый класс для GUI-приложений без виджетов (только QML).
    # sys.argv передаёт аргументы командной строки (Qt обрабатывает -platform, -style и т.д.)
    app = QGuiApplication(sys.argv)

    # QQmlApplicationEngine — загружает и исполняет QML-файлы
    engine = QQmlApplicationEngine()

    # ------------------------------------------------------------------
    # ШАГ 4: Регистрация Python-объектов в QML-контексте
    # ------------------------------------------------------------------
    # setContextProperty() делает Python-объект доступным в QML по имени.
    # QML может обращаться к методам, свойствам и сигналам этих объектов.

    # sdcManager — доступ к Manager'у: сигналы deviceConnected/deviceDisconnected,
    #              список устройств, состояние сети
    engine.rootContext().setContextProperty("sdcManager", manager)

    # fhirData — доступ к данным пациента: имя, диагнозы, наблюдения
    # QML может отображать эти данные в панели пациента
    engine.rootContext().setContextProperty("fhirData", fhir)

    # ------------------------------------------------------------------
    # ШАГ 5: Загрузка главного QML-файла
    # ------------------------------------------------------------------
    # os.path.dirname(__file__) + "Main.qml" — путь относительно этого файла,
    # что позволяет запускать из любой директории.
    qml_file = os.path.join(os.path.dirname(__file__), "Main.qml")
    engine.load(qml_file)

    # Проверяем успешность загрузки QML
    if not engine.rootObjects():
        # rootObjects() пуст если QML-файл не найден или содержит синтаксические ошибки
        sys.exit(-1)

    # ------------------------------------------------------------------
    # ШАГ 6: Запуск Qt event loop
    # ------------------------------------------------------------------
    # app.exec() блокирует этот поток до закрытия последнего окна.
    # Все Qt-сигналы, слоты и таймеры обрабатываются здесь.
    # При выходе sys.exit() завершает процесс с кодом возврата из exec().
    sys.exit(app.exec())
