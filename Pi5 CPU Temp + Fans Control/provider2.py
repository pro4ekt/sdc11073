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

from sense_hat import SenseHat

DEVICE_ID = 0
HUMIDITY_HANDLE = "sense-hat_metric"
HUMIDITY_ID = 0

def update_humidity(provider, value: Decimal):
    with provider.mdib.metric_state_transaction() as tr:
        temp_state = tr.get_state(HUMIDITY_HANDLE)
        mv = temp_state.MetricValue
        mv.Value = value
    observation_register(HUMIDITY_ID, value)

def _connect_db():

    db = mysql.connector.connect(
        host="192.168.0.102",
        user="testuser1",
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
            ("Provider", "provider", "Würzburg, DE")
        )
        device_id = cur.lastrowid  # Получаем сгенерированный ID
        global DEVICE_ID
        DEVICE_ID = device_id # сохраняем в глобальную переменную device id в базе данных

        # 🔹 Вставляем метрики, связанные с этим устройством
        metrics = [
            ("humidity", "%",999)
        ]

        for name, unit, threshold in metrics:
            cur.execute(
                "INSERT INTO metrics (device_id, name, unit, threshold) VALUES (%s, %s, %s, %s)",
                (device_id, name, unit, threshold)
            )
            metric_id = cur.lastrowid  # если нужно использовать дальше
            if name == "cpu_temp":
                global TEMP_ID
                TEMP_ID = metric_id
            if name == "humidity":
                global HUMIDITY_ID
                HUMIDITY_ID = metric_id

        db.commit()

    finally:
        try:
            cur.close()
            db.close()
        except:
            pass

def observation_register(metric_id : int, value : Decimal):
    db = _connect_db()

    try:
        cur = db.cursor()
        cur.execute(  "INSERT INTO observations (metric_id, time, value) VALUES (%s, %s, %s)",(metric_id, time.strftime("%Y-%m-%d %H:%M:%S"), value, ))
        db.commit()
    finally:
        try:
            cur.close()
            db.close()
        except:
            pass

def operation_register():
    db = _connect_db()

    try:
        cur = db.cursor()
        cur.execute(
            "INSERT INTO operations (consumer_id, provider_id, time, type, performed_by) VALUES (%s, %s, %s, %s, %s)",
            (DEVICE_ID, DEVICE_ID, time.strftime("%Y-%m-%d %H:%M:%S"), "alert_control", "provider"))
        db.commit()
    finally:
        try:
            cur.close()
            db.close()
        except:
            pass

def alarm_register():
    db = _connect_db()
    try:
        cur = db.cursor()

        now = time.strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "INSERT INTO alarms (metric_id, device_id, state, triggered_at, threshold) VALUES (%s, %s, %s, %s, %s)",
            (TEMP_ID, DEVICE_ID, "firing", now, 54))
        alarm_id = cur.lastrowid
        global TEMP_ALARM_ID
        TEMP_ALARM_ID = alarm_id

        db.commit()
    finally:
        try:
            cur.close()
            db.close()
        except:
            pass

def alarm_resolve(alarm_id: int):
    db = _connect_db()
    try:
        cur = db.cursor()
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "UPDATE alarms SET state=%s, resolved_at=%s WHERE id=%s",
            ("resolved", now, alarm_id)
        )
        db.commit()
    finally:
        try:
            cur.close()
            db.close()
        except:
            pass

def delete_db():
    db = _connect_db()
    try:
        cur = db.cursor()
        cur.execute("SET FOREIGN_KEY_CHECKS=0")
        for tbl in ['observations', 'alarms', 'operations', 'metrics', 'devices']:
            cur.execute(f"TRUNCATE TABLE {tbl}")
        cur.execute("SET FOREIGN_KEY_CHECKS=1")
        db.commit()
    finally:
        try:
            cur.close()
            db.close()
        except:
            pass


if __name__ == '__main__':
    #logging.basicConfig(level=logging.INFO)

    #UUID objects (universally unique identifiers) according to RFC 4122
    base_uuid = uuid.UUID('{cc013678-79f6-403c-998f-3cc0cc050231}')
    my_uuid = uuid.uuid5(base_uuid, "test_provider_2")

    # getting mdib from xml file and converting it to mdib.py object
    mdib = ProviderMdib.from_mdib_file("Pi5 CPU Temp + Fans Control/mdib2.xml")

    # All necessary components for the provider

    model = ThisModelType(model_name='TestModel',
                          manufacturer='TestManufacturer',
                          manufacturer_url='http://testurl.com')
    #Dependency injection: This class defines which component implementations the sdc provider will use
    components = SdcProviderComponents(role_provider_class=ExtendedProduct)
    #ThisDeviceType object with friendly name and serial number
    device = ThisDeviceType(friendly_name='TestDevice2', serial_number='123456')
    #UDP based discovery on single network adapter
    discovery = WSDiscoverySingleAdapter("wlan0")  # Wi-Fi or WLAN if on windows or wlan0 if Linux

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

    delete_db()
    register()

    while True:
        sense = SenseHat()
        
        humidity = sense.humidity
        update_humidity(provider, Decimal(humidity))
        print(provider.mdib.entities.by_handle("sense-hat_metric").state.MetricValue.Value)
        time.sleep(1)
        