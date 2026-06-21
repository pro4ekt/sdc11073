"""
main.py — Точка входа приложения SDC-консьюмера.

РЕЖИМЫ ЗАПУСКА (--mode):
  --mode=icu  Silent ICU:  полный Qt/QML стек, обработка тревог (DEV-31), БЕЗ FHIR.
  --mode=op   Operating Room: Headless QCoreApplication, загрузка FHIR,
              формирование BICEPS EnsembleContext/WorkflowContext.

ПОСЛЕДОВАТЕЛЬНОСТЬ ЗАПУСКА (ICU):
  1. QGuiApplication + QML-движок
  2. SdcMyConsumer(fhir_data=None, mode='icu') → WSDiscovery
  3. QML-контекст регистрируется → Qt event loop

ПОСЛЕДОВАТЕЛЬНОСТЬ ЗАПУСКА (OP):
  1. QCoreApplication (headless)
  2. FHIRPatientData.fetch(patient_id)
  3. SdcMyConsumer(fhir_data=fhir, mode='op') → WSDiscovery
  4. Qt event loop (без UI)
"""

from __future__ import annotations

import sys
import os
import argparse

# Manager — координатор сети SDC-устройств
from sdcMyConsumer import SdcMyConsumer

# FHIRPatientData — обёртка для работы с FHIR R4 REST API
from fhirData import FHIRPatientData

if __name__ == "__main__":

    # ------------------------------------------------------------------
    # ШАГ 0: Парсинг аргументов командной строки
    # ------------------------------------------------------------------
    parser = argparse.ArgumentParser(
        description="SDC Consumer — многорежимный оркестратор медицинских устройств"
    )
    parser.add_argument(
        "--mode",
        choices=["icu", "op"],
        default="op",
        help=(
            "Режим запуска: "
            "'icu' — Silent ICU (Qt/QML UI, без FHIR); "
            "'op'  — Operating Room (Headless, с FHIR-контекстами)."
        ),
    )
    # parse_known_args позволяет Qt-аргументам (-platform, -style) не вызывать ошибку
    args, qt_argv = parser.parse_known_args()
    mode = args.mode
    print(f"[Main] Starting in mode: '{mode}'")

    # ------------------------------------------------------------------
    # ШАГ 1: Инициализация Qt-приложения (ДО загрузки FHIR и Manager'а)
    # ------------------------------------------------------------------
    # QGuiApplication/QCoreApplication должны быть созданы ДО любого QObject.
    # qt_argv — аргументы без --mode (Qt их не знает).
    effective_argv = [sys.argv[0]] + qt_argv

    if mode == "icu":
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication(effective_argv)
    else:  # op
        from PySide6.QtCore import QCoreApplication
        app = QCoreApplication(effective_argv)

    # ------------------------------------------------------------------
    # ШАГ 2: Загрузка данных пациента из FHIR (только в режиме 'op')
    # ------------------------------------------------------------------
    fhir = None
    if mode == "op":
        patient_id = input("Введите ID пациента и нажмите Enter: ").strip()
        fhir = FHIRPatientData()
        try:
            fhir.fetch(patient_id)
            fhir.print_summary()
        except Exception as e:
            print(f"Ошибка загрузки данных пациента: {e}")
            # Продолжаем без FHIR — приложение справится с пустым контекстом

    # ------------------------------------------------------------------
    # ШАГ 3: Создание Manager'а и запуск сканирования сети
    # ------------------------------------------------------------------
    # В режиме 'icu': fhir_data=None (FHIR не нужен, UI работает без него).
    # В режиме 'op':  fhir_data=fhir (контексты будут записаны в каждое устройство).
    manager = SdcMyConsumer(fhir_data=fhir, mode=mode)
    manager.start()

    # ------------------------------------------------------------------
    # ШАГ 4: QML-движок (только в режиме 'icu')
    # ------------------------------------------------------------------
    if mode == "icu":
        from PySide6.QtQml import QQmlApplicationEngine

        engine = QQmlApplicationEngine()

        # Регистрируем Python-объекты в QML-контексте
        engine.rootContext().setContextProperty("sdcManager", manager)
        # fhirData в ICU-режиме не используется, но регистрируем None-заглушку
        # чтобы QML-код не падал при обращении к fhirData
        engine.rootContext().setContextProperty("fhirData", None)

        qml_file = os.path.join(os.path.dirname(__file__), "Main.qml")
        engine.load(qml_file)

        if not engine.rootObjects():
            sys.exit(-1)

    # ------------------------------------------------------------------
    # ШАГ 5: Запуск Qt event loop
    # ------------------------------------------------------------------
    # В 'icu': обрабатывает UI, сигналы, слоты.
    # В 'op':  обрабатывает только Qt-сигналы (headless).
    sys.exit(app.exec())
