"""
main.py — Точка входа приложения SDC-консьюмера (ICU-режим).

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
  python main.py
  python main.py --room="Room_1"
  python main.py --no_tls
  python main.py --tls --room="ICU-3"
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

if __name__ == "__main__":

    # Импортируем логгер из deviceHandler (он уже настроен с файловым хэндлером)
    from deviceHandler import _module_log as _log

    # ------------------------------------------------------------------
    # ШАГ 0: Парсинг аргументов командной строки
    # ------------------------------------------------------------------
    parser = argparse.ArgumentParser(
        description="SDC Consumer — оркестратор медицинских устройств (ICU)"
    )
    parser.add_argument(
        "--room",
        default=None,
        metavar="ROOM_ID",
        help=(
            "Фильтр по комнате (LocationContext.Room). "
            "Если указан, Consumer подключается ТОЛЬКО к устройствам из этой комнаты. "
            "Пример: --room=\"ICU-3\". "
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
    target_room: str | None = args.room
    override_ip: str | None = args.ip
    tls_mode: str = 'force_tls' if args.tls else ('no_tls' if args.no_tls else 'auto')

    # ------------------------------------------------------------------
    # ШАГ 0b: Настройка логирования sdc11073
    # ------------------------------------------------------------------
    basic_logging_setup(level=logging.WARNING)

    # ── Разделитель сессии в лог-файле ───────────────────────────────────────
    _ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    _sep = '=' * 72
    _log.info(_sep)
    _log.info(f'SESSION START  {_ts}')
    _log.info(
        (f'Room: {target_room}' if target_room else 'No room filter')
        + f'  |  TLS: {tls_mode}'
    )
    _log.info(_sep)
    # ─────────────────────────────────────────────────────────────────────────

    # ------------------------------------------------------------------
    # ШАГ 1: Инициализация Qt-приложения (ДО Manager'а)
    # ------------------------------------------------------------------
    effective_argv = [sys.argv[0]] + qt_argv

    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication(effective_argv)

    # ------------------------------------------------------------------
    # ШАГ 2: Создание Manager'а и запуск сканирования сети
    # ------------------------------------------------------------------
    manager = SdcMyConsumer(
        target_room=target_room,
        override_ip=override_ip,
        tls_mode=tls_mode,
    )
    manager.start()

    # ------------------------------------------------------------------
    # ШАГ 3: QML-движок
    # ------------------------------------------------------------------
    from PySide6.QtQml import QQmlApplicationEngine

    engine = QQmlApplicationEngine()

    engine.rootContext().setContextProperty("sdcManager", manager)
    engine.rootContext().setContextProperty("fhirData", None)

    qml_file = os.path.join(os.path.dirname(__file__), "Main.qml")
    engine.load(qml_file)

    if not engine.rootObjects():
        sys.exit(-1)

    # ------------------------------------------------------------------
    # ШАГ 4: Запуск Qt event loop
    # ------------------------------------------------------------------
    sys.exit(app.exec())
