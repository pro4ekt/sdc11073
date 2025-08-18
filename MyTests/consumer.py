from __future__ import annotations

import socket
import logging
import time
import uuid
from decimal import Decimal

import sdc11073.entity_mdib.entity_providermdib
from aiohttp.helpers import set_result
from sdc11073 import observableproperties
from sdc11073.definitions_sdc import SdcV1Definitions
from sdc11073.location import SdcLocation
from sdc11073.loghelper import basic_logging_setup
from sdc11073.mdib import ProviderMdib, ConsumerMdib
from sdc11073.consumer import SdcConsumer
from sdc11073.roles.product import ExtendedProduct
from sdc11073.wsdiscovery import WSDiscovery
from sdc11073.xml_types import pm_qnames as pm
from sdc11073.xml_types import pm_types
from sdc11073.xml_types.dpws_types import ThisDeviceType
from sdc11073.xml_types.dpws_types import ThisModelType
from sdc11073.xml_types.pm_types import NumericMetricValue
from sdc11073.pysoap.msgfactory import CreatedMessage
from sdc11073.xml_types.actions import periodic_actions
from sdc11073.consumer.serviceclients.setservice import SetServiceClient

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.146.164.72", 80))
        return s.getsockname()[0]
    except Exception:
        return "Exception"
    finally:
        s.close()

#Функция которая потом будет вызываться в observableproperties.bind которая нужна для вывода обновлённых метрик
def on_metric_update(metrics_by_handle: dict):
    print(f"Got update on Metric with handle: {list(metrics_by_handle.keys())}")
    print(f"Curent CPU Temperature : {consumer.mdib.entities.by_handle("met1").state.MetricValue.Value}")
    print(f"Current Alarm State: {consumer.mdib.entities.by_handle("als1").state.Presence}")

def get_number():
    value = Decimal(input("Input your Value: "))
    return value

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)

    # Создаём и запускаем discovery для поиска
    discovery = WSDiscovery(get_local_ip())
    discovery.start()

    # Достаём все сервиы которые были найдены в discovery
    services = discovery.search_services(timeout=1)

    # затычка конкретно для меня потомушо у меня ток 1 сервис
    service = services[0]

    # Инициализация consumer
    consumer = SdcConsumer.from_wsd_service(wsd_service=service, ssl_context_container=None)

    time.sleep(3)

    # Старт консьюмера
    consumer.start_all()

    # Инициализация mdib от provider
    mdib = ConsumerMdib(consumer)
    mdib.init_mdib()

    # Фиксация изменений
    observableproperties.bind(mdib, metrics_by_handle=on_metric_update)

    set_service = consumer.set_service_client
    r = consumer.mdib.entities.by_handle("liquid").descriptor.TechnicalRange[0]
    marker = True
    while marker:
        value = get_number()
        if value < r.Lower or value > r.Upper:
            print("Value is out of range Lower: {}, Upper: {}".format(r.Lower, r.Upper))
        else:
            marker = False

    set_result1 = set_service.set_numeric_value(operation_handle="inject",
                                               requested_numeric_value=value)

    while True:
        time.sleep(1)

"""Просто всякие разные тесты"""
"""
# Просто вывод инфы про сервисы чтобы была
    if not services:
        print("No services found")
    else:
        print("There are some services")
        for s in services:
            print(f"Adresses: {s.x_addrs}")
            print(f"Types: {s.types}")
"""