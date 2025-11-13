from __future__ import annotations

import mysql.connector
import socket
import logging
import time
import uuid
from decimal import Decimal
from copy import deepcopy
import asyncio
import threading

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
SERVICES = []
SERVICES_Flag = False
flag = True

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
    """This Part is for Provider self Fan controll
    if(consumer.mdib.entities.by_handle("al_condition_1").state.Presence):
        print("Temp is too high! Fan should be ON")
        print(print(f"Curent CPU Temperature : {consumer.mdib.entities.by_handle("cpu_temp").state.MetricValue.Value}"))
    """
    print("Temperature : ",consumer.mdib.entities.by_handle("temperature").state.MetricValue.Value)
    """ This Part is for Consumer Controlled Fan
    print(f"Curent CPU Temperature : {consumer.mdib.entities.by_handle("cpu_temp").state.MetricValue.Value}")
    print("Fan Status ", consumer.mdib.entities.by_handle("fan_rotation").state.MetricValue.Value)
    print(f"Current Alarm Signal State: {consumer.mdib.entities.by_handle("al_signal_1").state.Presence}")
    print(f"Current Alarm Condition State: {consumer.mdib.entities.by_handle("al_condition_1").state.Presence}")
    """

'''
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
'''

def handle_services(services):
    if(SERVICES_Flag):
        return
    if(services == []):
        print("No services found yet, waiting...")
    if(services != []):
        print("Found services")


async def discovery_loop(local_ip: str, timeout: float = 0.25, interval: float = 0.5):
    discovery = WSDiscovery(local_ip)
    discovery.start()
    try:
        while True:
            global SERVICES
            if(SERVICES == []):
                services = await asyncio.to_thread(discovery.search_services, timeout=timeout)
                handle_services(services)
                SERVICES = services
                if services != []:
                    global SERVICES_Flag
                    SERVICES_Flag = True
                await asyncio.sleep(interval)
            else:
                await asyncio.sleep(interval)
    finally:
        try:
            discovery.stop()
        except Exception:
            pass

def start_discovery_in_background(local_ip: str, timeout: float = 0.1, interval: float = 0.1):
    def runner():
        asyncio.run(discovery_loop(local_ip, timeout, interval))
    t = threading.Thread(target=runner, daemon=True)
    t.start()
    return t

if __name__ == '__main__':
    #logging.basicConfig(level=logging.INFO)
    discovery_thread = start_discovery_in_background(get_local_ip())
    while True:
        """
        if(SERVICES == []):
            print("No services found yet, waiting...")
            time.sleep(1)
        """
        if (SERVICES_Flag and SERVICES[0] != None):
            while True:
                if flag:
                    consumer = SdcConsumer.from_wsd_service(wsd_service=SERVICES[0], ssl_context_container=None)

                    consumer.start_all()

                    mdib = ConsumerMdib(consumer)
                    mdib.init_mdib()

                    observableproperties.bind(mdib, metrics_by_handle=on_metric_update)

                    #sub = consumer.subscription_manager.is_subscribed
                    flag = False

                #time.sleep(1)

                try:
                    mrg = consumer.subscription_mgr.subscriptions
                    for key, value in mrg.items():
                        if value.is_subscribed is False:
                            SERVICES_Flag = False
                            SERVICES = []
                            flag = True
                            consumer.stop_all()
                            break
                except Exception:
                    break
                finally:
                    pass

    #sig_state = consumer.mdib.entities.by_handle("al_signal").state
    #sig_state.Presence = AlertSignalPresence.OFF

    #consumer.set_service_client.set_alert_state(operation_handle="alert_control",proposed_alert_state=sig_state)