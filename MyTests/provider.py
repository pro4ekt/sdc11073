from __future__ import annotations

from collections import Counter

import keyboard
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
from sdc11073.mdib.statecontainers import AlertSignalStateContainer
from sdc11073.xml_types.pm_types import NumericMetricValue
from sdc11073.xml_types.pm_types import MeasurementValidity
from sdc11073.provider.components import SdcProviderComponents
from sdc11073.roles.product import ExtendedProduct
from sdc11073.provider.operations import SetValueOperation


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
    print("[INFO] Температура недоступна на этой системе.")
    return 42.0

if __name__ == '__main__':
    #logging.basicConfig(level=logging.INFO)

    base_uuid = uuid.UUID('{cc013678-79f6-403c-998f-3cc0cc050230}')
    my_uuid = uuid.uuid5(base_uuid, "12345")

    # Подгрузка mdib с файла
    mdib = ProviderMdib.from_mdib_file("mdib.xml")

    # Объявление компонентов(полей) провайдера
    model = ThisModelType(model_name='TestModel',
                          manufacturer='TestManufacturer',
                          manufacturer_url='http://testurl.com')
    components = SdcProviderComponents(role_provider_class=ExtendedProduct)
    device = ThisDeviceType(friendly_name='TestDevice', serial_number='12345')
    discovery = WSDiscoverySingleAdapter("Wi-Fi")  # Wi-Fi если на windows или wlan0 если линукс

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

    """
    a = provider.get_operation_by_handle("inject")
    b = provider.get_operation_by_handle("op1")
    c = provider.mdib.entities.by_handle("op1")
    with provider.mdib.alert_state_transaction() as alert_tr:
        alert_tr.get_state("als1").Presence = AlertSignalPresence.ON
    print(provider.mdib.entities.by_handle("als1").state.Presence)
    """

    while True:
        liquid_volume = provider.mdib.entities.by_handle("liquid").state.MetricValue.Value
        if (t % 500 == 0 and liquid_volume != 0):
            print("Liquid Volume : ",liquid_volume)
        with provider.mdib.alert_state_transaction() as alert_tr:
            condition = alert_tr.get_state("alc1")
            signal = alert_tr.get_state("als1")
            if (liquid_volume == 0):
                condition.Presence = True
                signal.Presence = AlertSignalPresence.ON
                if(signal.Presence == AlertSignalPresence.ON):
                    print("ALARM IS ON! NO MORE LIQUID!")
                if(signal.Presence == AlertSignalPresence.OFF):
                    print("Alarm is OFF, but there is still no liquid!")
        t = t + 1
"""
#Цикл для показа температуры процессора
    while True:
        with provider.mdib.metric_state_transaction() as tr:
            t = get_cpu_temperature()
            print(f"CPU Temperature: {t}")
            state = tr.get_state("met1")
            obj = NumericMetricValue()
            obj.Value = Decimal(t)
            state.MetricValue = obj
            time.sleep(3)

"""

"""Тут просто всякие разные тесты для проверок этапов"""
"""
#Метод для проверки имёе IPшников
def get_if_name_by_ip(ip_to_find: str) -> str:
    for iface_name, iface_addrs in psutil.net_if_addrs().items():
        for addr in iface_addrs:
            if addr.family == socket.AF_INET:
                print(f"[debug] {iface_name} = {addr.address}")
                if addr.address == ip_to_find:
                    return iface_name
    raise RuntimeError(f"Не найден интерфейс с IP: {ip_to_find}")

#Проверка подтянулась ли mdib
a = provider.mdib.entities.items()
for handle, entity in a:
    print(handle)

#проверка того записалась ли в provider инфа
b = [provider.device,provider.model]
print()

#Достать метрику с mdib
metric = provider.mdib.entities.by_handle("met1").state.MetricValue.Value

#Цикл для отображения времени с начала запуска программы
t = 0
while True:
    with provider.mdib.metric_state_transaction() as tr:
        time.sleep(1)
        state = tr.get_state("met1").MetricValue.Value
        state = Decimal(t)
        t = t + 1
        tr.
        print(state)
        
#Создал транзакцию для изменения mdib в начале и конце проверка что изменения реально были
metric = provider.mdib.entities.by_handle("met1").state.MetricValue
print(metric)

with provider.mdib.metric_state_transaction() as tr:
    state = tr.get_state("met1")
    val = NumericMetricValue()
    val.Value = Decimal(47)
    val.MetricQuality.Validity = MeasurementValidity("Inv")
    state.MetricValue = val

new_metric = provider.mdib.entities.by_handle("met1").state.MetricValue
print(new_metric)

#Выдача всех сервивос по именам
service = provider.hosted_services
for name, obj in vars(service).items():
    if not name.startswith('_'):  # исключаем внутренние атрибуты
        print(f"{name} → {type(obj).__name__}")

#Посмотреть конкретных подписчиков конкретного сервиса   
subscribers = provider._subscriptions_managers['StateEvent']._subscriptions

# Время с включения прибора
    t = 0
    while t < 100:
        with provider.mdib.metric_state_transaction() as tr:
            t = t + 1
            state = tr.get_state("met1")
            obj = NumericMetricValue()
            obj.Value = Decimal(t)
            state.MetricValue = obj
            print(f"Time from start = {state.MetricValue.Value} с")
            time.sleep(2)
            
# Изменение метрики и Alarm
   while True:
        time.sleep(1)
        t = t + 0.1
        with provider.mdib.metric_state_transaction() as metric_tr:
            state = metric_tr.get_state("liquid")
            state.MetricValue.Value = Decimal(t)
        with provider.mdib.alert_state_transaction() as alert_tr:
            condition = alert_tr.get_state("alc1")
            if(t == 3):
                condition.Presence = True
            if (condition.Presence == True):
                signal = alert_tr.get_state("als1")
                signal.Presence = AlertSignalPresence.ON
            if(t == 5):
                print("Alarm is ON")
            if(keyboard.is_pressed("r")):
                condition.Presence = False
                signal.Presence = AlertSignalPresence.OFF
                print("YOU STOPPED THE ALARM")
                text = "Alarm is OFF"
                print("Prause")
                time.sleep(1)
"""
