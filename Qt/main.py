"""
main.py — Точка входа приложения SDC-консьюмера.

РЕЖИМЫ ЗАПУСКА (--mode):
  --mode=icu  Silent ICU:  полный Qt/QML стек, обработка тревог (DEV-31), БЕЗ FHIR.
  --mode=op   Operating Room: Headless QCoreApplication, загрузка FHIR,
              формирование BICEPS EnsembleContext/WorkflowContext.

ФИЛЬТРАЦИЯ ПО КОМНАТЕ (--room):
  Если указан, Consumer подписывается ТОЛЬКО на те SDC Provider'ы, у которых
  LocationContext.Room совпадает с указанным значением.

TLS-РЕЖИМ (--tls / --no_tls):
  --tls      Принудительный TLS для всех подключений (загружает сертификаты
             из certs_out/ или pat/certs/). Используй когда Provider анонсирует
             http://, но фактически требует TLS.
  --no_tls   Отключить TLS полностью — plain HTTP, без fallback. Удобно для
             тестирования Provider'ов без сертификатов.
  (без флага) auto-режим: https:// → TLS сразу; http:// → plain с TLS-fallback
             если получен ConnectionResetError.

Примеры:
  python main.py --mode=icu
  python main.py --mode=icu --room="Room_1"
  python main.py --mode=icu --no_tls
  python main.py --mode=icu --tls --room="OR-1"
"""

from __future__ import annotations

import sys
import os
import argparse
import logging
import datetime

from sdc11073.loghelper import basic_logging_setup

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
    parser.add_argument(
        "--ip",
        default=None,
        metavar="IP_ADDRESS",
        help=(
            "IP-адрес сетевого адаптера для WSDiscovery. "
            "Используй если устройства находятся в другом VMware/сетевом сегменте. "
            "Пример: --ip=192.168.242.100. "
            "По умолчанию: автоопределение через маршрут к 8.8.8.8."
        ),
    )

    # ── TLS-стратегия (взаимоисключающая группа) ─────────────────────────────
    _tls_group = parser.add_mutually_exclusive_group()
    _tls_group.add_argument(
        "--tls",
        action="store_true",
        default=False,
        help=(
            "Принудительный TLS для ВСЕХ подключений. "
            "Загружает сертификаты из Qt/certs_out/ или pat/certs/. "
            "Используй когда Provider анонсирует http:// но требует TLS."
        ),
    )
    _tls_group.add_argument(
        "--no_tls",
        action="store_true",
        default=False,
        help=(
            "Отключить TLS — plain HTTP, без TLS-fallback. "
            "Удобно для тестирования Provider'ов без сертификатов. "
            "Соответствует запуску sdcProvider/sdcX с --no_tls."
        ),
    )

    # parse_known_args позволяет Qt-аргументам (-platform, -style) не вызывать ошибку
    args, qt_argv = parser.parse_known_args()
    mode = args.mode
    target_room: str | None = args.room
    override_ip: str | None = args.ip
    tls_mode: str = 'force_tls' if args.tls else ('no_tls' if args.no_tls else 'auto')

    # ------------------------------------------------------------------
    # ШАГ 0b: Настройка логирования sdc11073 (как в tutorial/consumer/consumer.py)
    # ------------------------------------------------------------------
    # basic_logging_setup настраивает ВСЁ дерево логгеров sdc11073.*
    # Без этого все внутренние предупреждения/ошибки библиотеки тихо исчезают,
    # т.к. корневой логгер не имеет обработчиков.
    # DEBUG — максимальная детализация: SOAP-сообщения, WS-Discovery, подписки.
    # Для продакшена можно поставить logging.INFO или logging.WARNING.
    basic_logging_setup(level=logging.WARNING)


    # ── Разделитель сессии в лог-файле ───────────────────────────────────────
    _ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    _sep = '=' * 72
    _log.info(_sep)
    _log.info(f'SESSION START  {_ts}')
    _log.info(
        f'Mode: {mode}'
        + (f'  |  Room: {target_room}' if target_room else '  |  No room filter')
        + f'  |  TLS: {tls_mode}'
    )
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
    manager = SdcMyConsumer(
        fhir_data=fhir, mode=mode, target_room=target_room,
        override_ip=override_ip, tls_mode=tls_mode,
    )
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
