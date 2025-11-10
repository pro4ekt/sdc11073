from __future__ import annotations

from collections import Counter
from copy import deepcopy

import mysql.connector
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

from myDbClass import dbworker

CPU_TEMP_HANDLE = 'cpu_temp'
AL_COND_HANDLE = 'al_condition_1'
AL_SIG_HANDLE = 'al_signal_1'
FAN_HANDLE = 'fan_rotation'
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
    with provider.mdib.metric_state_transaction() as tr:
        temp_state = tr.get_state(CPU_TEMP_HANDLE)
        mv = temp_state.MetricValue
        mv.Value = value
    evaluate_temp_alert(provider, value)

def evaluate_temp_alert(provider, current: Decimal):
    fan_state = provider.mdib.entities.by_handle("fan_rotation").state.MetricValue.Value
    # get threshold of CPU temperature
    threshold = provider.mdib.entities.by_handle("temp_threshold").state.MetricValue.Value
    # transaction for alert state changes
    with provider.mdib.alert_state_transaction() as tr:
        cond_state = tr.get_state(AL_COND_HANDLE)
        sig_state = tr.get_state(AL_SIG_HANDLE)
        # evaluate if condition should fire
        cond_should_fire = current >= threshold
        # get current states
        is_cond_active = cond_state.Presence
        is_fan_active = fan_state == "On"
        # if condition should fire and is not active yet -> activate condition and signal
        if cond_should_fire and (not is_cond_active):
            cond_state.ActivationState = AlertActivation.ON
            cond_state.Presence = True
            sig_state.ActivationState = AlertActivation.ON
            sig_state.Presence = AlertSignalPresence.ON
        # if condition should not fire and is active -> deactivate condition and signal
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
    print("Temp Threshold : ", provider.mdib.entities.by_handle("temp_threshold").state.MetricValue.Value)
    print("-----------------------------------------------------")

def fan_control(provider):
    #pinctrl FAN_PWM a0 это на пай вернуть чтобы система автоматически контролировала вентилятор
    state = provider.mdib.entities.by_handle(FAN_HANDLE).state.MetricValue.Value
    if platform.system() != 'Linux':
        return
    if state == "On":
        os.system("pinctrl FAN_PWM op dl")
    elif state == "Off":
        os.system("pinctrl FAN_PWM op dh")

if __name__ == '__main__':
    #logging.basicConfig(level=logging.INFO)

    #UUID objects (universally unique identifiers) according to RFC 4122
    base_uuid = uuid.UUID('{cc013678-79f6-403c-998f-3cc0cc050230}')
    my_uuid = uuid.uuid5(base_uuid, "test_provider_1")

    # getting mdib from xml file and converting it to mdib.py object
    mdib = ProviderMdib.from_mdib_file("mdib1.xml")

    metrics = []
    metrics1 = []
    obj = mdib.descriptions.objects

    for containers in obj:
        type_name = type(containers).__name__
        if "MetricDescriptor" in type_name:
            metrics.append(containers)

    for metric in metrics:
        metrics1.append(metric.Handle)
    # All necessary components for the provider

    model = ThisModelType(model_name='TestModel',
                          manufacturer='TestManufacturer',
                          manufacturer_url='http://testurl.com')
    #Dependency injection: This class defines which component implementations the sdc provider will use
    components = SdcProviderComponents(role_provider_class=ExtendedProduct)
    #ThisDeviceType object with friendly name and serial number
    device = ThisDeviceType(friendly_name='TestDevice', serial_number='12345')
    #UDP based discovery on single network adapter
    discovery = WSDiscoverySingleAdapter("Wi-Fi")  # Wi-Fi or WLAN if on windows or wlan0 if Linux

    #Assambling everything which was created above to implement SDC Provider object
    provider = SdcProvider(ws_discovery=discovery,
                           epr=my_uuid,
                           this_model=model,
                           this_device=device,
                           device_mdib_container=mdib,
                           specific_components=components)

    # Discovery start
    discovery.start()

    # Starting all Services of provider
    provider.start_all()

    # Publishing the provider into Network to make it visible for consumers
    provider.publish()

    with provider.mdib.alert_state_transaction() as tr:
        cond_state = tr.get_state(AL_COND_HANDLE)
        cond_state.ActivationState = AlertActivation.OFF

    with provider.mdib.metric_state_transaction() as tr:
        id = tr.get_state("device_id")
        id.MetricValue.Value = Decimal(DEVICE_ID)

    # A loop in which all processes take place, for example continuous temperature checking and logging.
    while True:
        temperature = get_cpu_temperature()
        update_cpu_temp(provider, Decimal(temperature))
        fan_control(provider)
        print_metrics(provider)
        """This Part is for Provider self Fan controll
        if(provider.mdib.entities.by_handle("al_signal_1").state.Presence == "On"):
            turn_fan(provider, "On")
        elif(not provider.mdib.entities.by_handle("al_condition_1").state.Presence):
            turn_fan(provider, "Off")
        """
        """
        if(provider.mdib.entities.by_handle("fan_rotation").state.MetricValue.Value == "On"):
            t = t - 1
        if(provider.mdib.entities.by_handle("fan_rotation").state.MetricValue.Value == "Off"):
            t = t + 1
        """
        time.sleep(1)