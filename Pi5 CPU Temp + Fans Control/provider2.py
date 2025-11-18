from __future__ import annotations

from collections import Counter
from copy import deepcopy

import os
import mysql
import platform
import logging
import time
import uuid
from decimal import Decimal
import threading
import sounddevice as sd
import numpy as np
from sense_hat import SenseHat

from myproviderimpl import MySdcProvider
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
from sdc11073.xml_types.pm_types import MetricQuality
from sdc11073.mdib.statecontainers import AlertSignalStateContainer
from sdc11073.xml_types.pm_types import NumericMetricValue
from sdc11073.xml_types.pm_types import MeasurementValidity
from sdc11073.provider.components import SdcProviderComponents
from sdc11073.roles.product import ExtendedProduct
from sdc11073.provider.operations import SetValueOperation

sense = SenseHat()
show_temp = True
settings = False
lower_threshold = True
VALUE = 0
DEVICE_ID = 0
OFFSET_LEFT = 1
OFFSET_TOP = 3
REQUEST = {"temperature":False, "humidity":False}
TIME_T = 0
TIME_H = 0
AMPLITUDE = 1

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

def sound(duration,frequncy, amplitude=1):
    sample_rate = 44100
    t = np.linspace(0,duration,int(sample_rate*duration), endpoint=False)

    wave = amplitude * np.sin(2*np.pi*frequncy*t)

    sd.play(wave,sample_rate)
    sd.wait()

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

def show_celsius_display(c_color, symbol_color=(255, 165, 40)):
    """
    Displays a smaller 'C' and a larger degree symbol with a hole at the same time.
    """
    O = (0, 0, 0)
    C = c_color
    S = symbol_color

    pixels = [
        O, O, O, O, S, S, S, O,
        O, O, O, O, S, O, S, O,
        C, C, C, O, S, S, S, O,
        C, O, O, O, O, O, O, O,
        C, O, O, O, O, O, O, O,
        C, O, O, O, O, O, O, O,
        C, C, C, O, O, O, O, O,
        O, O, O, O, O, O, O, O,
    ]
    sense.set_pixels(pixels)

def show_humidity_display(h_color, symbol_color=(255, 100, 40)):
    O = (0, 0, 0) 
    H = h_color
    S = symbol_color

    pixels = [
        O, O, O, O, O, O, O, O,
        O, O, O, S, S, O, O, S,
        H, O, H, S, S, O, S, O,
        H, O, H, O, O, S, O, O,
        H, H, H, O, S, O, S, S,
        H, O, H, S, O, O, S, S,
        H, O, H, O, O, O, O, O,
        O, O, O, O, O, O, O, O,
    ]
    sense.set_pixels(pixels)

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

def temp_alarm_eveluation(provider, value, timeout):

    lowTempThreshold=provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Lower
    highTempThreshold=provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Upper
    
    if((value > highTempThreshold) or (value < lowTempThreshold)):
            with provider.mdib.alert_state_transaction() as tr:
                cond_state = tr.get_state("al_condition_temperature")
                cond_state.Presence = True
                sig_state = tr.get_state("al_signal_temperature")
                if(timeout):
                    sig_state.Presence = AlertSignalPresence.OFF
                else:
                    sig_state.Presence = AlertSignalPresence.ON
            if(value < lowTempThreshold):
                background(51, 153, 255)
                sound(1,420,AMPLITUDE)
            else:
                background(130,0,0)      
                if(provider.mdib.entities.by_handle("al_signal_temperature").state.Presence == AlertSignalPresence.ON):
                    sound(1,420,AMPLITUDE)        
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

def hum_alarm_eveluation(provider, value, timeout):

    lowHumThreshold= provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Lower
    highHumThreshold= provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Upper
    
    if((value > highHumThreshold) or (value < lowHumThreshold)):
            with provider.mdib.alert_state_transaction() as tr:
                cond_state = tr.get_state("al_condition_humidity")
                cond_state.Presence = True
                sig_state = tr.get_state("al_signal_humidity")
                if(timeout):
                    sig_state.Presence = AlertSignalPresence.OFF
                else:
                    sig_state.Presence = AlertSignalPresence.ON
            if(value < lowHumThreshold):
                background(204,153,102)
                sound(1,640,AMPLITUDE)  
            else:
                background(51,102,204)
                if(provider.mdib.entities.by_handle("al_signal_humidity").state.Presence == AlertSignalPresence.ON):
                    sound(1,640,AMPLITUDE)               
    else:
       background(51,204,51)

def metrics_info(provider):
    print("Humidity = ", provider.mdib.entities.by_handle("humidity").state.MetricValue.Value)
    print("Temperature = ", provider.mdib.entities.by_handle("temperature").state.MetricValue.Value)

def joystick():
    global show_temp, AMPLITUDE, settings, VALUE, lower_threshold
    while True:
        events = sense.stick.get_events()
        for e in events:
         if(settings):
             if e.action == "pressed" and e.direction == "up":
                VALUE = VALUE + 1
             if e.action == "pressed" and e.direction == "down":
                VALUE = VALUE - 1
             if e.action == "pressed" and e.direction == "middle":
                settings = not settings
             continue 
         if e.action == "pressed" and e.direction == "middle":
             show_temp = not show_temp
         if e.action == "pressed" and e.direction == "left":
             AMPLITUDE = AMPLITUDE - 0.3
             if(AMPLITUDE < 0):
                 AMPLITUDE = 0
                 print("Min volume reached")
         if e.action == "pressed" and e.direction == "right":
             AMPLITUDE = AMPLITUDE + 0.3
         if e.action == "pressed" and e.direction == "up":
             settings = not settings
             lower_threshold = not lower_threshold
         if e.action == "pressed" and e.direction == "down":
             settings = not settings
         """
         if e.action == "held":
             duration = time.time() - start
             if(duration > 3):
              os.execvp("python3", ["python3", "Pi5 CPU Temp + Fans Control/sensestart.py"])
         """
        time.sleep(0.05)

def first_start(provider):

    
    #sense.show_message("Started SDC-mode")

    lowTempThreshold=int(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Lower)
    highTempThreshold=int(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Upper)
    midTemp=int((highTempThreshold+lowTempThreshold)/2)

    lowHumThreshold=int(provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Lower)
    highHumThreshold=int(provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Upper)
    midHum=int((highHumThreshold+lowHumThreshold)/2)

    sense.clear()
    t_show(255,255,255)
    show_number(lowTempThreshold, 255, 165, 40)
    background(51, 153, 255)
    time.sleep(2)

    sense.clear()
    t_show(255,255,255)
    show_number(midTemp, 255, 165, 40)
    background(51, 204, 51)
    time.sleep(2)

    sense.clear()
    t_show(255,255,255)
    show_number(highTempThreshold, 255, 165, 40)
    background(255,51,51)
    time.sleep(2)

    sense.clear()
    h_show(255,255,255)
    show_number(lowHumThreshold, 255, 100, 40)
    background(204,153,102)
    time.sleep(2)

    sense.clear()
    h_show(255,255,255)
    show_number(midHum, 255, 100, 40)
    background(51,204,51)
    time.sleep(2)

    sense.clear()
    h_show(255,255,255)
    show_number(highHumThreshold, 255, 100, 40)
    background(51,102,204) 
    
    time.sleep(2)

def threshold_setting(provider):
    global VALUE
    if(show_temp):
        if(lower_threshold):
            value = Decimal(VALUE) + provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Lower
        else:
            value = Decimal(VALUE) + provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Upper
    else:
        if(lower_threshold):
            value = Decimal(VALUE) + provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Lower
        else:
            value = Decimal(VALUE) + provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Upper
    sense.clear()
    show_number(int(value), 255, 165, 40)
    time.sleep(1)
    return value

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

    # Publishing the provider into Network to make it visible for consumers
    provider.publish()
    a = provider.mdib.entities.by_handle("temperature").state.MetricValue.MetricQuality.Validity
    #register()

    with provider.mdib.metric_state_transaction() as tr:
        id = tr.get_state("device_id")
        id.MetricValue.Value = Decimal(DEVICE_ID) 

    #first_start(provider)
    share_state_temp = deepcopy(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0])
    share_state_hum = deepcopy(provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0])

    while True:
        t = time.time()
        sense.clear()
        print("Temp Low = " + str(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Lower))
        print("Temp High = " + str(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Upper))
        print("Hum Low = " + str(provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Lower))
        print("Hum High = " + str(provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Upper))
        
        if(provider.requests != []):
            print(provider.requests[0].raw_data)
            temp = provider.find_string_in_request(provider.requests[0], "temperature_alert_control")
            hum = provider.find_string_in_request(provider.requests[0], "humidity_alert_control")
            temp_threshold = provider.find_string_in_request(provider.requests[0], "temperature_threshold_control")
            hum_threshold = provider.find_string_in_request(provider.requests[0], "humidity_threshold_control")
            low_temp = provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Lower == share_state_temp.Lower
            high_temp = provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Upper == share_state_temp.Upper
            low_hum = provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Lower == share_state_hum.Lower
            high_hum = provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Upper == share_state_hum.Upper
            if(hum):
                REQUEST["humidity"] = True
                TIME_H = t
            elif(temp):
                REQUEST["temperature"] = True
                TIME_T = t
            elif(temp_threshold):
                show_celsius_display((255, 165, 40))
                if(not low_temp):
                    share_state_temp.Lower = provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Lower
                    background(51, 153, 255)
                elif(not high_temp):
                    share_state_temp.Upper = provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Upper
                    background(130,0,0)
                time.sleep(2)
            elif(hum_threshold):
                show_humidity_display((255, 100, 40))
                if(not low_hum):
                    share_state_hum.Lower = provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Lower
                    background(204,153,102)
                elif(not high_hum):
                    share_state_hum.Upper = provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Upper
                    background(51,102,204)
                time.sleep(1)
                sense.clear()
                time.sleep(0.5)
            provider.requests.pop(0)

        humidity = sense.humidity
        temperature = sense.temperature

        update_humidity(provider, Decimal(humidity))
    

        update_temperature(provider, Decimal(temperature))
        #metrics_info(provider)

        lowTempThreshold=int(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Lower)
        highTempThreshold=int(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Upper)
        if(settings):
            new_threshold = threshold_setting(provider)
            continue
        try:
            if(new_threshold != None):
                if(show_temp):
                    with provider.mdib.metric_state_transaction() as tr:
                        temp_state = tr.get_state("temperature")
                        vl = temp_state.MetricValue.MetricQuality.Validity
                        vl = MeasurementValidity.CALIBRATION_ONGOING
                        pr = temp_state.PhysiologicalRange[0]
                        if(lower_threshold):
                            pr.Lower = Decimal(new_threshold)
                        else:
                            pr.Upper = Decimal(new_threshold)
                else:
                    with provider.mdib.metric_state_transaction() as tr:
                        hum_state = tr.get_state("humidity")
                        pr = hum_state.PhysiologicalRange[0]
                        if(lower_threshold):
                            pr.Lower = Decimal(new_threshold)
                        else:
                            pr.Upper = Decimal(new_threshold)
                VALUE = 0
        except:
            pass
        finally:
            pass
        new_threshold = None
        if(show_temp):
           t_show(255, 255, 0)
           show_number(int(temperature+0.5), 255, 165, 40)
           if(REQUEST["temperature"] == True):
               if(t - TIME_T < 3):
                temp_alarm_eveluation(provider, temperature, True)
                time.sleep(1)
                continue
               else:
                temp_alarm_eveluation(provider, temperature, False)
                REQUEST["temperature"] = False
                TIME_T = 0
                time.sleep(1)
                continue
                    
           temp_alarm_eveluation(provider, temperature, False)        
        else:
           h_show(255, 255, 0)
           show_number(int(humidity+0.5), 255, 100, 40)
           if(REQUEST["humidity"] == True):
               if(t - TIME_H < 6):
                hum_alarm_eveluation(provider, humidity, True)
                time.sleep(1)
                continue
               else:
                hum_alarm_eveluation(provider, humidity, False)
                REQUEST["humidity"] = False
                TIME_H = 0
                time.sleep(1)
                continue
           hum_alarm_eveluation(provider, humidity, False)  

        time.sleep(1) 
