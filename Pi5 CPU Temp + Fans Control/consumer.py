from __future__ import annotations

import socket
import logging
import time
import uuid
from decimal import Decimal
from copy import deepcopy

import sdc11073.entity_mdib.entity_providermdib
from aiohttp.helpers import set_result
from sdc11073 import observableproperties
from sdc11073.definitions_sdc import SdcV1Definitions
from sdc11073.location import SdcLocation
from sdc11073.loghelper import basic_logging_setup
from sdc11073.mdib import ProviderMdib, ConsumerMdib
from sdc11073.consumer import SdcConsumer
from sdc11073.mdib.statecontainers import AlertSignalStateContainer
from sdc11073.roles.product import ExtendedProduct
from sdc11073.wsdiscovery import WSDiscovery
from sdc11073.xml_types import pm_qnames as pm
from sdc11073.xml_types import pm_types
from sdc11073.xml_types.pm_types import AlertSignalPresence
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
    #print(f"Got update on Metric with handle: {list(metrics_by_handle.keys())}")
    print(f"Curent CPU Temperature : {consumer.mdib.entities.by_handle("cpu_temp").state.MetricValue.Value}")
    print("Fan Status ", consumer.mdib.entities.by_handle("fan_rotation").state.MetricValue.Value)
    print(f"Current Alarm State: {consumer.mdib.entities.by_handle("al_signal_1").state.Presence}")

def get_number():
    print("INPUT YOUR VALUE")
    value = Decimal(input())
    return value

def turn_fan(consumer, state: str):
    consumer.set_service_client.set_string(operation_handle="fan_control",
                                           requested_string=state)

if __name__ == '__main__':
    #logging.basicConfig(level=logging.INFO)

    # Создаём и запускаем discovery для поиска
    discovery = WSDiscovery(get_local_ip())
    discovery.start()

    # Достаём все сервиы которые были найдены в discovery
    services = discovery.search_services(timeout=1)

    # затычка конкретно для меня потомушо у меня ток 1 сервис
    service = services[0]

    # Инициализация consumer
    consumer = SdcConsumer.from_wsd_service(wsd_service=service, ssl_context_container=None)

    time.sleep(1)

    # Старт консьюмера
    consumer.start_all()

    # Инициализация mdib от provider
    mdib = ConsumerMdib(consumer)
    mdib.init_mdib()

    observableproperties.bind(mdib, metrics_by_handle=on_metric_update)

    t = 0
    while True:
        cond_state = consumer.mdib.entities.by_handle("al_condition_1").state.ActivationState == "On"
        fan_state = consumer.mdib.entities.by_handle("fan_rotation").state.MetricValue.Value == "On"
        if(cond_state and (not fan_state)):
            if(t == 0):
                time.sleep(5)
                t = t + 1
            turn_fan(consumer, "On")
        if((not cond_state) and (fan_state)):
            if (t == 0):
                time.sleep(5)
                t = t + 1
            turn_fan(consumer, "Off")
        time.sleep(1)