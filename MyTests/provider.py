from __future__ import annotations

import psutil
import socket
import logging
import time
import uuid
from decimal import Decimal

from attr.setters import convert

import sdc11073.entity_mdib.entity_providermdib
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
from sdc11073.xml_types.pm_types import NumericMetricValue
from sdc11073.xml_types.pm_types import MeasurementValidity

base_uuid = uuid.UUID('{cc013678-79f6-403c-998f-3cc0cc050230}')
my_uuid = uuid.uuid5(base_uuid, "12345")

def get_if_name_by_ip(ip_to_find: str) -> str:
    for iface_name, iface_addrs in psutil.net_if_addrs().items():
        for addr in iface_addrs:
            if addr.family == socket.AF_INET:
                print(f"[debug] {iface_name} = {addr.address}")
                if addr.address == ip_to_find:
                    return iface_name
    raise RuntimeError(f"Не найден интерфейс с IP: {ip_to_find}")


logging.basicConfig(level=logging.INFO)

#Подгрузка mdib с файла
mdib = ProviderMdib.from_mdib_file("mdib.xml")
a = mdib.descriptions.NODETYPE
#Затычки для инициалзации Provider
model = ThisModelType(model_name='TestModel')
device = ThisDeviceType(friendly_name='TestDevice', serial_number='12345')


discovery = WSDiscoverySingleAdapter("WLAN")#WLAN
discovery.start()


#Создание экземпляра Provider
provider = SdcProvider(ws_discovery=discovery,
                       epr=my_uuid,
                       this_model=model,
                       this_device=device,
                       device_mdib_container=mdib)

provider.start_all()
provider.publish()
while True:
    print(1)
    time.sleep(1)
"""#Время с включения прибора
t = 0
while t<5:
    with provider.mdib.metric_state_transaction() as tr:
        t = t + 1
        state = tr.get_state("met1")
        obj = NumericMetricValue()
        obj.Value = Decimal(t)
        state.MetricValue = obj
        print(f"Времени с запуска прибора = {state.MetricValue.Value} с")
        time.sleep(1)

print(f"Прибор выключили через = {provider.mdib.entities.by_handle("met1").state.MetricValue.Value} с")"""

"""Тут просто всякие разные тесты для проверок этапов"""
"""
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
"""
