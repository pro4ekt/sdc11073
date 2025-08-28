from __future__ import annotations

from collections import Counter
from copy import deepcopy

import sqlite3
import os
import platform
import logging
import time
import uuid
from decimal import Decimal

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

COND_THRESHOLD = 5
SIG_THRESHOLD = 7
CPU_TEMP_HANDLE = 'cpu_temp'
AL_COND_HANDLE = 'al_condition_1'
AL_SIG_HANDLE = 'al_signal_1'
FAN_HANDLE = 'fan_rotation'

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
    with provider.mdib.metric_state_transaction() as tr:
        temp_state = tr.get_state(CPU_TEMP_HANDLE)
        mv = temp_state.MetricValue
        mv.Value = value
    evaluate_temp_alert(provider, value)

def evaluate_temp_alert(provider, current: Decimal):
    fan_state = provider.mdib.entities.by_handle("fan_rotation").state.MetricValue.Value
    with provider.mdib.alert_state_transaction() as tr:
        cond_state = tr.get_state(AL_COND_HANDLE)
        sig_state = tr.get_state(AL_SIG_HANDLE)

        cond_should_fire = current >= COND_THRESHOLD
        sig_should_fire = current >= SIG_THRESHOLD
        is_cond_active = cond_state.ActivationState == 'On'
        is_sig_active = sig_state.ActivationState == 'On'
        is_fan_active = fan_state == "On"

        if cond_should_fire and (not is_cond_active):
            cond_state.ActivationState = AlertActivation.ON
            cond_state.Presence = True
        elif sig_should_fire and (not is_sig_active) and (not is_fan_active):
            sig_state.ActivationState = AlertActivation.ON
            sig_state.Presence = AlertSignalPresence.ON
        elif is_sig_active and (is_fan_active):
            sig_state.ActivationState = AlertActivation.OFF
            sig_state.Presence = AlertSignalPresence.OFF
        elif (not cond_should_fire) and is_cond_active:
            cond_state.ActivationState = AlertActivation.OFF
            cond_state.Presence = False
            sig_state.ActivationState = AlertActivation.OFF
            sig_state.Presence = AlertSignalPresence.OFF

def print_metrics(provider):
    print("Curent CPU Temp : ", provider.mdib.entities.by_handle("cpu_temp").state.MetricValue.Value)
    print("Alarm Condition : ", provider.mdib.entities.by_handle("al_condition_1").state.ActivationState)
    print("Alarm Signal : ", provider.mdib.entities.by_handle("al_signal_1").state.Presence)
    print("Fan Status : ", provider.mdib.entities.by_handle("fan_rotation").state.MetricValue.Value)

def sqlite_logging(provider, value : bool):
    conn = sqlite3.connect("Pi5 CPU Temp + Fans Control/cpu_fan.db")
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

def turn_fan(provider, state: str):
    with provider.mdib.metric_state_transaction() as tr:
        fan_state = tr.get_state(FAN_HANDLE)
        mv = fan_state.MetricValue
        mv.Value = state

if __name__ == '__main__':
    #logging.basicConfig(level=logging.INFO)

    base_uuid = uuid.UUID('{cc013678-79f6-403c-998f-3cc0cc050230}')
    my_uuid = uuid.uuid5(base_uuid, "12345")

    # mdib from xml file
    mdib = ProviderMdib.from_mdib_file("Pi5 CPU Temp + Fans Control/mdib.xml")

    # All necessary components for the provider
    model = ThisModelType(model_name='TestModel',
                          manufacturer='TestManufacturer',
                          manufacturer_url='http://testurl.com')
    components = SdcProviderComponents(role_provider_class=ExtendedProduct)
    device = ThisDeviceType(friendly_name='TestDevice', serial_number='12345')
    discovery = WSDiscoverySingleAdapter("wlan0")  # Wi-Fi если на windows или wlan0 если линукс или же WLAN

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
    sqlite_logging(provider, False)

    while True:
        update_cpu_temp(provider, Decimal(t))
        print_metrics(provider)
        sqlite_logging(provider, True)
        """This Part is for Provider self Fan controll
        if(provider.mdib.entities.by_handle("al_signal_1").state.Presence == "On"):
            turn_fan(provider, "On")
        elif(not provider.mdib.entities.by_handle("al_condition_1").state.Presence):
            turn_fan(provider, "Off")
        """
        if(provider.mdib.entities.by_handle("fan_rotation").state.MetricValue.Value == "On"):
            t = t - 1
        if(provider.mdib.entities.by_handle("fan_rotation").state.MetricValue.Value == "Off"):
            t = t + 1
        time.sleep(1)