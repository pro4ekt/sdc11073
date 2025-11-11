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
import threading

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

sense = SenseHat()
show_temp = True
DEVICE_ID = 0
OFFSET_LEFT = 1
OFFSET_TOP = 3

NUMS =[1,1,1,1,0,1,1,0,1,1,0,1,1,1,1,  # 0
       0,1,0,0,1,0,0,1,0,0,1,0,0,1,0,  # 1
       1,1,1,0,0,1,0,1,0,1,0,0,1,1,1,  # 2
       1,1,1,0,0,1,1,1,1,0,0,1,1,1,1,  # 3
       1,0,0,1,0,1,1,1,1,0,0,1,0,0,1,  # 4
       1,1,1,1,0,0,1,1,1,0,0,1,1,1,1,  # 5
       1,1,1,1,0,0,1,1,1,1,0,1,1,1,1,  # 6
       1,1,1,0,0,1,0,1,0,1,0,0,1,0,0,  # 7
       1,1,1,1,0,1,1,1,1,1,0,1,1,1,1,  # 8
       1,1,1,1,0,1,1,1,1,0,0,1,0,0,1]  # 9

# Displays a single digit (0-9)
def show_digit(val, xd, yd, r, g, b):
  offset = val * 15
  for p in range(offset, offset + 15):
    xt = p % 3
    yt = (p-offset) // 3
    sense.set_pixel(xt+xd, yt+yd, r*NUMS[p], g*NUMS[p], b*NUMS[p])

# Displays a two-digits positive number (0-99)
def show_number(val, r, g, b):
  abs_val = abs(val)
  tens = abs_val // 10
  units = abs_val % 10
  if (abs_val > 9): show_digit(tens, OFFSET_LEFT, OFFSET_TOP, r, g, b)
  show_digit(units, OFFSET_LEFT+4, OFFSET_TOP, r, g, b)

def background(r, g, b):
  pixels = sense.get_pixels()
  new_pixels = []
  for pixel in pixels:
    if pixel == [0,0,0]:
      new_pixels.append((r,g,b))
    else:
      new_pixels.append(pixel)
  sense.set_pixels(new_pixels)

def t_show(r, g, b):
    sense.set_pixel(0,0,r,g,b)
    sense.set_pixel(1,0,r,g,b)
    sense.set_pixel(2,0,r,g,b)
    sense.set_pixel(1,1,r,g,b)
    sense.set_pixel(1,2,r,g,b)

def h_show(r, g, b):
    sense.set_pixel(0,0,r,g,b)
    sense.set_pixel(0,1,r,g,b)
    sense.set_pixel(0,2,r,g,b)
    sense.set_pixel(1,1,r,g,b)
    sense.set_pixel(2,2,r,g,b)
    sense.set_pixel(2,1,r,g,b)
    sense.set_pixel(2,0,r,g,b)

def update_humidity(provider, value: Decimal):
    with provider.mdib.metric_state_transaction() as tr:
        temp_state = tr.get_state("humidity")
        mv = temp_state.MetricValue
        mv.Value = value
    #observation_register(HUMIDITY_ID, value)

def update_temperature(provider, value: Decimal):
    with provider.mdib.metric_state_transaction() as tr:
        temp_state = tr.get_state("temperature")
        mv = temp_state.MetricValue
        mv.Value = value
    #observation_register(HUMIDITY_ID, value)

def update_pressure(provider, value: Decimal):
    with provider.mdib.metric_state_transaction() as tr:
        temp_state = tr.get_state("pressure")
        mv = temp_state.MetricValue
        mv.Value = value
    #observation_register(HUMIDITY_ID, value)

def temp_alarm_eveluation(provider, value):

    lowTempThreshold=int(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Lower)
    highTempThreshold=int(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Upper)
    
    if((int(value) > highTempThreshold) or (int(value) < lowTempThreshold)):
            with provider.mdib.alert_state_transaction() as tr:
                cond_state = tr.get_state("al_condition_temperature")
                cond_state.Presence = True
                sig_state = tr.get_state("al_signal_temperature")
                sig_state.Presence = AlertSignalPresence.ON
            if(int(value) < lowTempThreshold):
                background(51, 153, 255)
            else:
                background(255,51,51)              
    else:
       background(51, 204, 51)
    """
    esli potom budy delt timeout
    a = provider.mdib.entities.by_handle("al_signal").state.Presence == AlertSignalPresence.OFF
    b = provider.mdib.entities.by_handle("al_condition").state.Presence == True

    if a and (not b):
        background(0,100,0)
    if a and b:
        background(0,0,150)
    if (not a) and b:
        background(150,0,0)

    if(int(value) < lowTempThreshold):
            with provider.mdib.alert_state_transaction() as tr:
                cond_state = tr.get_state("al_condition")
                cond_state.Presence = False
                sig_state = tr.get_state("al_signal")
                sig_state.Presence = AlertSignalPresence.OF
    """

def hum_alarm_eveluaton(provider, value):

    lowHumThreshold=int(provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Lower)
    highHumThreshold=int(provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Upper)
    
    if((int(value) > highHumThreshold) or (int(value) < lowHumThreshold)):
            with provider.mdib.alert_state_transaction() as tr:
                cond_state = tr.get_state("al_condition_humidity")
                cond_state.Presence = True
                sig_state = tr.get_state("al_signal_humidity")
                sig_state.Presence = AlertSignalPresence.ON
            if(int(value) < lowHumThreshold):
                background(204,153,102)
            else:
                background(51,102,204)              
    else:
       background(51,204,51)

def metrics_info(provider):
    print("Humidity = ", provider.mdib.entities.by_handle("humidity").state.MetricValue.Value)
    print("Temperature = ", provider.mdib.entities.by_handle("temperature").state.MetricValue.Value)

def joystick():
    global show_temp
    while True:
        events = sense.stick.get_events()
        for e in events:
         if e.action == "pressed":
             show_temp = not show_temp
        time.sleep(0.05)

def first_start(provider):
    sense.show_message("Started SDC-mode")

    lowTempThreshold=int(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Lower)
    highTempThreshold=int(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Upper)
    midTemp=int((highTempThreshold-lowTempThreshold)/2)

    lowHumThreshold=int(provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Lower)
    highHumThreshold=int(provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Upper)
    midHum=int((highHumThreshold-lowHumThreshold)/2)

    sense.clear()
    t_show(255,255,255)
    show_number(lowTempThreshold, 20, 20, 20)
    background(51, 153, 255)
    time.sleep(2)

    sense.clear()
    t_show(255,255,255)
    show_number(midTemp, 20, 20, 20)
    background(51, 204, 51)
    time.sleep(2)

    sense.clear()
    t_show(255,255,255)
    show_number(highTempThreshold, 20, 20, 20)
    background(255,51,51)
    time.sleep(2)

    sense.clear()
    h_show(255,255,255)
    show_number(lowHumThreshold, 20, 20, 20)
    background(204,153,102)
    time.sleep(2)

    sense.clear()
    h_show(255,255,255)
    show_number(midHum, 20, 20, 20)
    background(51,204,51)
    time.sleep(2)

    sense.clear()
    h_show(255,255,255)
    show_number(highHumThreshold, 20, 20, 20)
    background(51,204,51)
    time.sleep(2)



if __name__ == '__main__':
    #logging.basicConfig(level=logging.INFO)
    t = threading.Thread(target=joystick, daemon=True)
    t.start()
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

    #register()

    with provider.mdib.metric_state_transaction() as tr:
        id = tr.get_state("device_id")
        id.MetricValue.Value = Decimal(DEVICE_ID) 

    first_start(provider)

    while True:
        sense.clear()
        
        humidity = sense.humidity
        temperature = sense.temperature

        update_humidity(provider, Decimal(humidity))
        update_temperature(provider, Decimal(temperature))
        metrics_info(provider)

        lowTempThreshold=int(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Lower)
        highTempThreshold=int(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Upper)

        if(show_temp):
           t_show(255,255,255)
           show_number(int(temperature), 20, 20, 20)
           temp_alarm_eveluation(provider, temperature)
        else:
           h_show(255,255,0)
           show_number(int(humidity), 20, 20, 20)
           hum_alarm_eveluaton(provider, humidity)

        time.sleep(1) 
