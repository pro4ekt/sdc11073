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
from myProvider.myproviderimpl import MySdcProvider

#from sense_hat import SenseHat

DEVICE_ID = 0
HUMIDITY_HANDLE = "sense-hat_metric"
HUMIDITY_ID = 0


def update_humidity(provider, value: Decimal):
    with provider.mdib.metric_state_transaction() as tr:
        temp_state = tr.get_state(HUMIDITY_HANDLE)
        mv = temp_state.MetricValue
        mv.Value = value

if __name__ == '__main__':
    # logging.basicConfig(level=logging.INFO)

    # UUID objects (universally unique identifiers) according to RFC 4122
    base_uuid = uuid.UUID('{cc013678-79f6-403c-998f-3cc0cc050231}')
    my_uuid = uuid.uuid5(base_uuid, "test_provider_2")

    # getting mdib from xml file and converting it to mdib.py object
    mdib = ProviderMdib.from_mdib_file("mdib2.xml")

    # All necessary components for the provider

    model = ThisModelType(model_name='TestModel',
                          manufacturer='TestManufacturer',
                          manufacturer_url='http://testurl.com')
    # Dependency injection: This class defines which component implementations the sdc provider will use
    components = SdcProviderComponents(role_provider_class=ExtendedProduct)
    # ThisDeviceType object with friendly name and serial number
    device = ThisDeviceType(friendly_name='TestDevice2', serial_number='123456')
    # UDP based discovery on single network adapter
    discovery = WSDiscoverySingleAdapter("Wi-Fi")  # Wi-Fi or WLAN if on windows or wlan0 if Linux

    # Assambling everything which was created above to implement SDC Provider object
    provider = MySdcProvider(ws_discovery=discovery,
                           epr=my_uuid,
                           this_model=model,
                           this_device=device,
                           device_mdib_container=mdib,
                           specific_components=components)

    # Discovery start
    discovery.start()

    # Starting all Services of provider
    provider.start_all()
    a = provider.mdib.entities.by_handle("device_id").state.PhysiologicalRange[0].Lower
    # Publishing the provider into Network to make it visible for consumers
    #provider.publish()

    with provider.mdib.metric_state_transaction() as tr:
        id = tr.get_state("device_id")
        id.MetricValue.Value = Decimal(DEVICE_ID)

    while True:
        #sense = SenseHat()

        #humidity = sense.humidity
        #update_humidity(provider, Decimal(humidity))
        print(provider.mdib.entities.by_handle("sense-hat_metric").state.MetricValue.Value)
        time.sleep(1)