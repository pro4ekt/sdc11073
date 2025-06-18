from __future__ import annotations

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
from sdc11073.xml_types import pm_qnames as pm
from sdc11073.xml_types import pm_types
from sdc11073.xml_types.dpws_types import ThisDeviceType
from sdc11073.xml_types.dpws_types import ThisModelType
from sdc11073.xml_types.pm_types import NumericMetricValue
from sdc11073.xml_types.pm_types import MeasurementValidity

#Подгрузка mdib с файла
mdib = ProviderMdib.from_mdib_file("mdib.xml")

#Затычки для инициалзации Provider
model = "My Model"
adapter = WSDiscoverySingleAdapter("Loopback Pseudo-Interface 1")
device = "My Device"

#Создание экземпляра Provider
provider = SdcProvider(ws_discovery=adapter,
                       this_model=model,
                       this_device=device,
                       device_mdib_container=mdib)
#запуск WSDiscrovery  тд
provider.start_all()

#Время с включения прибора
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

print(f"Прибор выключили через = {provider.mdib.entities.by_handle("met1").state.MetricValue.Value} с")




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
