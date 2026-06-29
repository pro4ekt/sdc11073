"""
main.py — Точка входа приложения SDC-консьюмера.

РЕЖИМЫ ЗАПУСКА (--mode):
  --mode=icu  Silent ICU:  полный Qt/QML стек, обработка тревог (DEV-31), БЕЗ FHIR.
  --mode=op   Operating Room: Headless QCoreApplication, загрузка FHIR,
              формирование BICEPS EnsembleContext/WorkflowContext.

ФИЛЬТРАЦИЯ ПО КОМНАТЕ (--room):
  Опциональный аргумент. Если указан, Consumer подписывается ТОЛЬКО на те
  SDC Provider'ы, у которых LocationContext.Room совпадает с указанным значением.
  Устройства из других комнат обнаруживаются WSDiscovery, но после init_mdib()
  немедленно отключаются без занесения в UI и без ошибки (DEV-49 не вызывается,
  т.к. подписок WS-Eventing ещё нет).

  Пример запуска:
    python main.py --mode=icu --room="ICU-3"
    python main.py --mode=icu            # без фильтра — подключает все устройства

ПОСЛЕДОВАТЕЛЬНОСТЬ ЗАПУСКА (ICU):
  1. QGuiApplication + QML-движок
  2. SdcMyConsumer(fhir_data=None, mode='icu', target_room=args.room) → WSDiscovery
  3. QML-контекст регистрируется → Qt event loop

ПОСЛЕДОВАТЕЛЬНОСТЬ ЗАПУСКА (OP):
  1. QCoreApplication (headless)
  2. FHIRPatientData.fetch(patient_id)
  3. SdcMyConsumer(fhir_data=fhir, mode='op', target_room=args.room) → WSDiscovery
  4. Qt event loop (без UI)
"""

from __future__ import annotations

import sys
import os
import argparse
import logging
import datetime

# Manager — координатор сети SDC-устройств
from sdcMyConsumer import SdcMyConsumer

# FHIRPatientData — обёртка для работы с FHIR R4 REST API
from fhirData import FHIRPatientData

if __name__ == "__main__":

    # Импортируем логгер из deviceHandler (он уже настроен с файловым хэндлером)
    # Если deviceHandler ещё не импортирован — импортируем здесь.
    from deviceHandler import _module_log as _log

    # ------------------------------------------------------------------
    # ШАГ 0: Парсинг аргументов командной строки
    # ------------------------------------------------------------------
    parser = argparse.ArgumentParser(
        description="SDC Consumer — многорежимный оркестратор медицинских устройств"
    )
    parser.add_argument(
        "--mode",
        choices=["icu", "op"],
        default="icu",
        help=(
            "Режим запуска: "
            "'icu' — Silent ICU (Qt/QML UI, без FHIR); "
            "'op'  — Operating Room (Headless, с FHIR-контекстами)."
        ),
    )
    parser.add_argument(
        "--room",
        default=None,
        metavar="ROOM_ID",
        help=(
            "Фильтр по комнате (LocationContext.Room). "
            "Если указан, Consumer подключается ТОЛЬКО к устройствам из этой комнаты. "
            "Пример: --room=\"ICU-3\" или --room=\"OR-1\". "
            "По умолчанию: None (фильтрация отключена, все устройства принимаются)."
        ),
    )
    # parse_known_args позволяет Qt-аргументам (-platform, -style) не вызывать ошибку
    args, qt_argv = parser.parse_known_args()
    mode = args.mode
    target_room: str | None = args.room

    # ── Разделитель сессии в лог-файле ───────────────────────────────────────
    _ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    _sep = '=' * 72
    _log.info(_sep)
    _log.info(f'SESSION START  {_ts}')
    _log.info(f'Mode: {mode}' + (f'  |  Room filter: {target_room}' if target_room else '  |  No room filter'))
    _log.info(_sep)
    # ─────────────────────────────────────────────────────────────────────────

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
            _log.error(f"Ошибка загрузки данных пациента: {e}")

    # ------------------------------------------------------------------
    # ШАГ 3: Создание Manager'а и запуск сканирования сети
    # ------------------------------------------------------------------
    # В режиме 'icu': fhir_data=None (FHIR не нужен, UI работает без него).
    # В режиме 'op':  fhir_data=fhir (контексты будут записаны в каждое устройство).
    manager = SdcMyConsumer(fhir_data=fhir, mode=mode, target_room=target_room)
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
