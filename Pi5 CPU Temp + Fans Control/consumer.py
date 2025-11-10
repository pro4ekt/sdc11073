from __future__ import annotations

import socket
import logging
import time
import uuid
from decimal import Decimal
from copy import deepcopy
import mysql.connector
from myDbClass import dbworker

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
import asyncio
import threading

from myDbClass.dbworker import DBWorker

DEVICE_ID = 0
SERVICES = []

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
#def on_metric_update(metrics_by_handle: dict):
    #print(f"Got update on Metric with handle: {list(metrics_by_handle.keys())}")
    try:
        print(f"Curent CPU Temperature : {consumer.mdib.entities.by_handle("cpu_temp").state.MetricValue.Value}")
        #print("Fan Status ", consumer.mdib.entities.by_handle("fan_rotation").state.MetricValue.Value)
        #print(f"Current Alarm State: {consumer.mdib.entities.by_handle("al_signal_1").state.Presence}")
    except:
        pass
    finally:
        pass

def get_number():
    print("INPUT YOUR VALUE")
    value = Decimal(input())
    return value

def turn_fan(consumer, state: str):
    consumer.set_service_client.set_string(operation_handle="fan_control",
                                           requested_string=state)

def threshold_controll(consumer, value: Decimal):
    consumer.set_service_client.set_numeric_value(operation_handle="threshold_control", requested_numeric_value=value)

def handle_services(services):
    # process or store results (thread-safe access if you mutate shared state)
    print(f"Found {len(services)} services")

async def discovery_loop(local_ip: str, timeout: float = 1.0, interval: float = 5.0):
    discovery = WSDiscovery(local_ip)
    discovery.start()
    try:
        while True:
            services = await asyncio.to_thread(discovery.search_services, timeout=timeout)
            handle_services(services)
            if services != []:
                SERVICES.append(services)
            await asyncio.sleep(interval)
    finally:
        try:
            discovery.stop()
        except Exception:
            pass

def start_discovery_in_background(local_ip: str):
    def runner():
        asyncio.run(discovery_loop(local_ip, timeout=1.0, interval=5.0))
    t = threading.Thread(target=runner, daemon=True)
    t.start()
    return t

if __name__ == '__main__':
    #logging.basicConfig(level=logging.INFO)

    db = DBWorker(host="192.168.0.102", user="testuser2", password="1234", database="test")

    db.register(device_name="Test_DBWorker", device_type="consumer", device_location="DE")

    while (SERVICES == []):
        print("No services found yet, waiting...")
        time.sleep(1)

    # Создаём и запускаем discovery для поиска
    discovery = WSDiscovery(get_local_ip())
    discovery.start()

    # Достаём все сервиы которые были найдены в discovery
    services = discovery.search_services(timeout=1)

    # затычка конкретно для меня потомушо у меня ток 1 сервис
    service1 = services[0]
    service2 = services[1]

    # Инициализация consumer
    consumer1 = SdcConsumer.from_wsd_service(wsd_service=service1, ssl_context_container=None)
    consumer2 = SdcConsumer.from_wsd_service(wsd_service=service2, ssl_context_container=None)

    time.sleep(1)

    # Старт консьюмера
    consumer1.start_all()
    consumer2.start_all()

    # Инициализация mdib от provider
    mdib1 = ConsumerMdib(consumer1)
    mdib1.init_mdib()
    mdib2 = ConsumerMdib(consumer2)
    mdib2.init_mdib()

    #observableproperties.bind(mdib1, metrics_by_handle=on_metric_update)
    t = 0
    while True:
        print(consumer1.mdib.entities.by_handle("sense-hat_metric").state.MetricValue.Value)
        time.sleep(1)
