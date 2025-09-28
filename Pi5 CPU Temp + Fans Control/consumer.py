from __future__ import annotations

import mysql.connector
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

DEVICE_ID = 0

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
    """This Part is for Provider self Fan controll"""
    if(consumer.mdib.entities.by_handle("al_condition_1").state.Presence):
        print("Temp is too high! Fan should be ON")
        print(print(f"Curent CPU Temperature : {consumer.mdib.entities.by_handle("cpu_temp").state.MetricValue.Value}"))
    """"""

    """ This Part is for Consumer Controlled Fan
    print(f"Curent CPU Temperature : {consumer.mdib.entities.by_handle("cpu_temp").state.MetricValue.Value}")
    print("Fan Status ", consumer.mdib.entities.by_handle("fan_rotation").state.MetricValue.Value)
    print(f"Current Alarm Signal State: {consumer.mdib.entities.by_handle("al_signal_1").state.Presence}")
    print(f"Current Alarm Condition State: {consumer.mdib.entities.by_handle("al_condition_1").state.Presence}")
    """

def get_number():
    print("INPUT YOUR VALUE")
    value = Decimal(input())
    return value

def turn_fan(consumer, state: str):
    consumer.set_service_client.set_string(operation_handle="fan_control",
                                           requested_string=state)
    operation_register(consumer, "fan_control")

def threshold_control(consumer, value: Decimal):
    consumer.set_service_client.set_numeric_value(operation_handle="threshold_control", requested_numeric_value=value)
    operation_register(consumer, "threshold_control")

def _connect_db():

    db = mysql.connector.connect(
        host="192.168.0.102",
        user="testuser2",
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
            ("Consumer", "consumer", "Würzburg, DE")
        )
        device_id = cur.lastrowid  # Получаем сгенерированный ID
        global DEVICE_ID
        DEVICE_ID = device_id # сохраняем в глобальную переменную device id в базе данных

        db.commit()

    finally:
        try:
            cur.close()
            db.close()
        except:
            pass

def operation_register(consumer ,op_type: str):
    db = _connect_db()
    provider_id = consumer.mdib.entities.by_handle("device_id").state.MetricValue.Value

    try:
        cur = db.cursor()
        if(op_type == "fan_control"):
            cur.execute(
                "INSERT INTO operations (consumer_id, provider_id, time, type, performed_by) VALUES (%s, %s, %s, %s, %s)",
                (DEVICE_ID, provider_id, time.strftime("%Y-%m-%d %H:%M:%S"), "fan_control", "consumer"))
        elif(op_type == "alert_control"):
            cur.execute(
                "INSERT INTO operations (consumer_id, provider_id, time, type, performed_by) VALUES (%s, %s, %s, %s, %s)",
                (DEVICE_ID, provider_id, time.strftime("%Y-%m-%d %H:%M:%S"), "alert_control", "consumer"))
        elif (op_type == "threshold_control"):
            cur.execute(
                "INSERT INTO operations (consumer_id, provider_id, time, type, performed_by) VALUES (%s, %s, %s, %s, %s)",
                (DEVICE_ID, provider_id, time.strftime("%Y-%m-%d %H:%M:%S"), "threshold_control", "consumer"))
        db.commit()
    finally:
        try:
            cur.close()
            db.close()
        except:
            pass

if __name__ == '__main__':
    #logging.basicConfig(level=logging.INFO)

    #Create and start WS-Discovery therefore we can find services(provider(s)) in the network
    discovery = WSDiscovery(get_local_ip())
    discovery.start()

    #Writing down all the services we found in the network
    services = discovery.search_services(timeout=1)

    # Just for the tests
    service = services[0]

    # Initialization consumer from the service we found
    consumer = SdcConsumer.from_wsd_service(wsd_service=service, ssl_context_container=None)

    time.sleep(1)

    #Start background threads, read metadata from device, instantiate detected port type clients and subscribe
    consumer.start_all()

    #Copy mdib from provider to consumer
    mdib = ConsumerMdib(consumer)
    #And initialize it
    mdib.init_mdib()

    register()

    value = Decimal(input("Enter a number: "))
    threshold_control(consumer, value)

    # Metric update binding, allows consumer to observe all updates from provider and "customize" it
    observableproperties.bind(mdib, metrics_by_handle=on_metric_update)

    # A loop in which all processes take place, for example continuous temperature checking and logging.
    while True:
        cond_state = consumer.mdib.entities.by_handle("al_condition_1").state.ActivationState == "On"
        fan_state = consumer.mdib.entities.by_handle("fan_rotation").state.MetricValue.Value == "On"
        time.sleep(0.5)
        if(cond_state and (not fan_state)):
            time.sleep(3)
            turn_fan(consumer, "On")
        if((not cond_state) and (fan_state)):
            turn_fan(consumer, "Off")