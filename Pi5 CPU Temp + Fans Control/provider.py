from __future__ import annotations

import decimal
from collections import Counter
from copy import deepcopy

import sqlite3
import keyboard
import os
import platform
import logging
import time
import uuid
from decimal import Decimal
import mysql.connector

from sdc11073.location import SdcLocation
from sdc11073.loghelper import basic_logging_setup
from sdc11073.mdib import ProviderMdib
from sdc11073.provider import SdcProvider
from sdc11073.provider.components import SdcProviderComponents
from sdc11073.roles.product import ExtendedProduct
from sdc11073.wsdiscovery import WSDiscoverySingleAdapter
from sdc11073.wsdiscovery.service import Service
from sdc11073.xml_types import pm_qnames as pm
from sdc11073.xml_types import pm_types
from sdc11073.xml_types.dpws_types import ThisDeviceType
from sdc11073.xml_types.dpws_types import ThisModelType
from sdc11073.xml_types.pm_types import AlertSignalPresence
from sdc11073.xml_types.pm_types import AlertActivation
from sdc11073.mdib.statecontainers import AlertSignalStateContainer
from sdc11073.xml_types.pm_types import NumericMetricValue
from sdc11073.xml_types.pm_types import MeasurementValidity
from sdc11073.provider.components import SdcProviderComponents
from sdc11073.roles.product import ExtendedProduct
from sdc11073.provider.operations import SetValueOperation

CPU_TEMP_HANDLE = 'cpu_temp'
AL_COND_HANDLE = 'al_condition_1'
AL_SIG_HANDLE = 'al_signal_1'
DEVICE_ID = 0
TEMP_ID = 0
TEMP_ALARM_ID = 0

def get_cpu_temperature():
    """
    Универсальная функция получения температуры CPU.
    Работает на Raspberry Pi, большинстве Linux-систем.
    На Windows и Mac возвращает заглушку.
    """
    system = platform.system()

    if system == 'Linux':
        # Стандартный путь для Raspberry Pi и других Linux
        path = '/sys/class/thermal/thermal_zone0/temp'
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    raw_temp = f.read().strip()
                    return round(int(raw_temp) / 1000.0, 1)
            except Exception as e:
                print(f"[Ошибка чтения температуры]: {e}")
                return 42.0

    # Если не Linux или файл не найден — вернуть заглушку
    #print("[INFO] Температура недоступна на этой системе.")
    return 47.0

def update_cpu_temp(provider, value: Decimal):
    # 1. Write new temperature

    with provider.mdib.metric_state_transaction() as tr:
        temp_state = tr.get_state(CPU_TEMP_HANDLE)
        mv = temp_state.MetricValue
        mv.Value = value
    # 2. Evaluate alert
    evaluate_temp_alert(provider, value)

def evaluate_temp_alert(provider, current: Decimal):
    fan_state = provider.mdib.entities.by_handle("fan_rotation").state.MetricValue.Value
    threshold = provider.mdib.entities.by_handle("temp_threshold").state.MetricValue.Value
    with provider.mdib.alert_state_transaction() as tr:
        cond_state = tr.get_state(AL_COND_HANDLE)
        sig_state = tr.get_state(AL_SIG_HANDLE)

        cond_should_fire = current >= threshold
        is_cond_active = cond_state.ActivationState == 'On'
        is_fan_active = fan_state == "On"

        if cond_should_fire and (not is_cond_active):
            cond_state.ActivationState = AlertActivation.ON
            cond_state.Presence = True
            sig_state.ActivationState = AlertActivation.ON
            sig_state.Presence = AlertSignalPresence.ON
            alarm_register()
        elif (not cond_should_fire) and is_cond_active:
            cond_state.ActivationState = AlertActivation.OFF
            cond_state.Presence = False
            sig_state.ActivationState = AlertActivation.OFF
            sig_state.Presence = AlertSignalPresence.OFF
            alarm_resolve(id)

def print_metrics(provider):
    print("Curent CPU Temp : ", provider.mdib.entities.by_handle("cpu_temp").state.MetricValue.Value)
    print("Alarm Condition : ", provider.mdib.entities.by_handle("al_condition_1").state.ActivationState)
    print("Alarm Signal : ", provider.mdib.entities.by_handle("al_signal_1").state.Presence)
    print("Fan Status : ", provider.mdib.entities.by_handle("fan_rotation").state.MetricValue.Value)


def sqlite_logging(provider, value : bool):
    conn = sqlite3.connect("cpu_fan.db")
    cur = conn.cursor()

    temp = provider.mdib.entities.by_handle("cpu_temp").state.MetricValue.Value
    fan = provider.mdib.entities.by_handle("fan_rotation").state.MetricValue.Value
    cond = provider.mdib.entities.by_handle("al_condition_1").state.ActivationState
    sig = provider.mdib.entities.by_handle("al_signal_1").state.ActivationState

    cur.execute("CREATE TABLE IF NOT EXISTS cpu_fan_data "
                "(cpu_temp REAL, fan_speed TEXT, cond TEXT, sig TEXT)")
    if(value):
        cur.execute("INSERT INTO cpu_fan_data (cpu_temp, fan_speed, cond, sig) VALUES (?, ?, ?, ?)",(float(temp), str(fan), str(cond), str(sig)))
    else:
        cur.execute("DELETE FROM cpu_fan_data")

    conn.commit()
    cur.close()
    conn.close()


def _connect_db():

    db = mysql.connector.connect(
        host="localhost",
        user="root",
        password="1234",
        database="test")
    return db

def register():
    db = _connect_db()

    try:
        cur = db.cursor()

        # 🔹 Вставляем устройство без проверки
        cur.execute(
            "INSERT INTO devices (name, device_type, location) VALUES (%s, %s, %s)",
            ("Provider", "provider", "Würzburg, DE")
        )
        device_id = cur.lastrowid  # Получаем сгенерированный ID
        global DEVICE_ID
        DEVICE_ID = device_id # сохраняем в глобальную переменную device id в базе данных

        # 🔹 Вставляем метрики, связанные с этим устройством
        metrics = [
            ("cpu_temp", "C", 54),
            ("fan_rotation", "bool", 999)
        ]

        for name, unit, threshold in metrics:
            cur.execute(
                "INSERT INTO metrics (device_id, name, unit, threshold) VALUES (%s, %s, %s, %s)",
                (device_id, name, unit, threshold)
            )
            metric_id = cur.lastrowid  # если нужно использовать дальше
            if name == "cpu_temp":
                global TEMP_ID
                TEMP_ID = metric_id

        db.commit()

    finally:
        try:
            cur.close()
            db.close()
        except:
            pass

def observation_register(metric_id : int, value : Decimal):
    db = _connect_db()

    try:
        cur = db.cursor()
        cur.execute(  "INSERT INTO observations (metric_id, time, value) VALUES (%s, %s, %s)",(metric_id, time.strftime("%Y-%m-%d %H:%M:%S"), value, ))
        db.commit()
    finally:
        try:
            cur.close()
            db.close()
        except:
            pass

def operation_register():
    db = _connect_db()

    try:
        cur = db.cursor()
        cur.execute(
            "INSERT INTO operations (consumer_id, provider_id, time, type, performed_by) VALUES (%s, %s, %s, %s, %s)",
            (DEVICE_ID, DEVICE_ID, time.strftime("%Y-%m-%d %H:%M:%S"), "alert_control", "provider"))
        db.commit()
    finally:
        try:
            cur.close()
            db.close()
        except:
            pass

def alarm_register():
    db = _connect_db()
    try:
        cur = db.cursor()

        now = time.strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "INSERT INTO alarms (metric_id, device_id, state, triggered_at, threshold) VALUES (%s, %s, %s, %s, %s)",
            (TEMP_ID, DEVICE_ID, "firing", now, 54))
        alarm_id = cur.lastrowid
        global TEMP_ALARM_ID
        TEMP_ALARM_ID = alarm_id

        db.commit()
    finally:
        try:
            cur.close()
            db.close()
        except:
            pass

def alarm_resolve(alarm_id: int):
    db = _connect_db()
    try:
        cur = db.cursor()
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "UPDATE alarms SET state=%s, resolved_at=%s WHERE id=%s",
            ("resolved", now, alarm_id)
        )
        db.commit()
    finally:
        try:
            cur.close()
            db.close()
        except:
            pass

def delete_db():
    db = _connect_db()
    try:
        cur = db.cursor()
        cur.execute("SET FOREIGN_KEY_CHECKS=0")
        for tbl in ['observations', 'alarms', 'operations', 'metrics', 'devices']:
            cur.execute(f"TRUNCATE TABLE {tbl}")
        cur.execute("SET FOREIGN_KEY_CHECKS=1")
        db.commit()
    finally:
        try:
            cur.close()
            db.close()
        except:
            pass


if __name__ == '__main__':
    #logging.basicConfig(level=logging.INFO)

    base_uuid = uuid.UUID('{cc013678-79f6-403c-998f-3cc0cc050230}')
    my_uuid = uuid.uuid5(base_uuid, "12345")

    # mdib from xml file
    mdib = ProviderMdib.from_mdib_file("mdib.xml")

    # All necessary components for the provider
    model = ThisModelType(model_name='TestModel',
                          manufacturer='TestManufacturer',
                          manufacturer_url='http://testurl.com')
    components = SdcProviderComponents(role_provider_class=ExtendedProduct)
    device = ThisDeviceType(friendly_name='TestDevice', serial_number='12345')
    discovery = WSDiscoverySingleAdapter("WLAN")  # Wi-Fi если на windows или wlan0 если линукс или же WLAN

    # Создание экземпляра Provider
    provider = SdcProvider(ws_discovery=discovery,
                           epr=my_uuid,
                           this_model=model,
                           this_device=device,
                           device_mdib_container=mdib,
                           specific_components=components)

    # Запуск Дискавери
    discovery.start()

    # Запуск всех сервисов провайера
    provider.start_all()

    # Публикация провайлера в сеть чтобы его можно было обнаружить
    provider.publish()

    t = 0

    with provider.mdib.alert_state_transaction() as tr:
        cond_state = tr.get_state(AL_COND_HANDLE)
        cond_state.ActivationState = AlertActivation.OFF

    sqlite_logging(provider, False)
    delete_db()
    register()

    while True:
        update_cpu_temp(provider, Decimal(t))
        print_metrics(provider)
        sqlite_logging(provider, True)
        cpu_temp = provider.mdib.entities.by_handle("cpu_temp").state.MetricValue.Value
        observation_register(TEMP_ID, Decimal(cpu_temp))
        if(provider.mdib.entities.by_handle("fan_rotation").state.MetricValue.Value == "On"):
            t = t - 1
        if(provider.mdib.entities.by_handle("fan_rotation").state.MetricValue.Value == "Off"):
            t = t + 1
        time.sleep(1)