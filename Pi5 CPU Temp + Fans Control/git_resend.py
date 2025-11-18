from __future__ import annotations

from collections import Counter
from copy import deepcopy

import logging
import time
import uuid
from decimal import Decimal
import threading
import asyncio

import numpy as np
import sounddevice as sd
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
from sdc11073.mdib.statecontainers import AlertSignalStateContainer
from sdc11073.xml_types.pm_types import NumericMetricValue
from sdc11073.xml_types.pm_types import MeasurementValidity
from sdc11073.provider.operations import SetValueOperation

sense = SenseHat()
show_temp = True
DEVICE_ID = 0
OFFSET_LEFT = 1
OFFSET_TOP = 3
REQUEST = {"temperature": False, "humidity": False}
TIME_T = 0
TIME_H = 0
AMPLITUDE = 1

NUMS = [1, 1, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 1, 1,  # 0
        0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0,  # 1
        1, 1, 1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1, 1, 1,  # 2
        1, 1, 1, 0, 0, 1, 1, 1, 1, 0, 0, 1, 1, 1, 1,  # 3
        1, 0, 0, 1, 0, 1, 1, 1, 1, 0, 0, 1, 0, 0, 1,  # 4
        1, 1, 1, 1, 0, 0, 1, 1, 1, 0, 0, 1, 1, 1, 1,  # 5
        1, 1, 1, 1, 0, 0, 1, 1, 1, 1, 0, 1, 1, 1, 1,  # 6
        1, 1, 1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1, 0, 0,  # 7
        1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1,  # 8
        1, 1, 1, 1, 0, 1, 1, 1, 1, 0, 0, 1, 0, 0, 1]  # 9


# --- Constants for Alarms ---
ALARM_CONFIG = {
    'temperature': {
        'metric_handle': 'temperature',
        'condition_handle': 'al_condition_temperature',
        'signal_handle': 'al_signal_temperature',
        'colors': {
            'low': (51, 153, 255),
            'high': (130, 0, 0),
            'normal': (51, 204, 51),
        },
        'sound_freq': 420,
    },
    'humidity': {
        'metric_handle': 'humidity',
        'condition_handle': 'al_condition_humidity',
        'signal_handle': 'al_signal_humidity',
        'colors': {
            'low': (204, 153, 102),
            'high': (51, 102, 204),
            'normal': (51, 204, 51),
        },
        'sound_freq': 640,
    }
}


def sound(duration, frequncy, amplitude=1):
    sample_rate = 44100
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)

    wave = amplitude * np.sin(2 * np.pi * frequncy * t)

    sd.play(wave, sample_rate)
    sd.wait()


# Displays a single digit (0-9)
def show_digit(val, xd, yd, r, g, b):
    offset = val * 15
    for p in range(offset, offset + 15):
        xt = p % 3
        yt = (p - offset) // 3
        sense.set_pixel(xt + xd, yt + yd, r * NUMS[p], g * NUMS[p], b * NUMS[p])


# Displays a two-digits positive number (0-99)
def show_number(val, r, g, b):
    abs_val = abs(val)
    tens = abs_val // 10
    units = abs_val % 10
    if (abs_val > 9): show_digit(tens, OFFSET_LEFT, OFFSET_TOP, r, g, b)
    show_digit(units, OFFSET_LEFT + 4, OFFSET_TOP, r, g, b)


def background(r, g, b):
    pixels = sense.get_pixels()
    new_pixels = []
    for pixel in pixels:
        if pixel == [0, 0, 0]:
            new_pixels.append((r, g, b))
        else:
            new_pixels.append(pixel)
    sense.set_pixels(new_pixels)


def t_show(r, g, b):
    sense.set_pixel(0, 0, r, g, b)
    sense.set_pixel(1, 0, r, g, b)
    sense.set_pixel(2, 0, r, g, b)
    sense.set_pixel(1, 1, r, g, b)
    sense.set_pixel(1, 2, r, g, b)


def h_show(r, g, b):
    sense.set_pixel(0, 0, r, g, b)
    sense.set_pixel(0, 1, r, g, b)
    sense.set_pixel(0, 2, r, g, b)
    sense.set_pixel(1, 1, r, g, b)
    sense.set_pixel(2, 2, r, g, b)
    sense.set_pixel(2, 1, r, g, b)
    sense.set_pixel(2, 0, r, g, b)


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
    # observation_register(HUMIDITY_ID, value)


def update_temperature(provider, value: Decimal):
    with provider.mdib.metric_state_transaction() as tr:
        temp_state = tr.get_state("temperature")
        mv = temp_state.MetricValue
        mv.Value = value
    # observation_register(HUMIDITY_ID, value)


def update_pressure(provider, value: Decimal):
    with provider.mdib.metric_state_transaction() as tr:
        temp_state = tr.get_state("pressure")
        mv = temp_state.MetricValue
        mv.Value = value
    # observation_register(HUMIDITY_ID, value)


def evaluate_alarm(provider, metric_name: str, value: float, timeout: bool):
    """Generic alarm evaluation function."""
    config = ALARM_CONFIG[metric_name]
    metric_handle = config['metric_handle']
    condition_handle = config['condition_handle']
    signal_handle = config['signal_handle']
    colors = config['colors']
    sound_freq = config['sound_freq']

    low_threshold = provider.mdib.entities.by_handle(metric_handle).state.PhysiologicalRange[0].Lower
    high_threshold = provider.mdib.entities.by_handle(metric_handle).state.PhysiologicalRange[0].Upper

    is_out_of_range = (value > high_threshold) or (value < low_threshold)

    if is_out_of_range:
        with provider.mdib.alert_state_transaction() as tr:
            cond_state = tr.get_state(condition_handle)
            cond_state.Presence = True
            sig_state = tr.get_state(signal_handle)
            # The signal is ON if not in timeout, otherwise OFF.
            sig_state.Presence = AlertSignalPresence.OFF if timeout else AlertSignalPresence.ON

        # Set background color regardless of timeout
        if value < low_threshold:
            background(*colors['low'])
        else:
            background(*colors['high'])

        # Play sound only if the signal is ON (i.e., not in timeout)
        if not timeout:
            with AMPLITUDE_LOCK:
                amp = AMPLITUDE
            sound(1, sound_freq, amp)
    else:
        background(*colors['normal'])
        # Optionally reset alert condition when back in normal range
        with provider.mdib.alert_state_transaction() as tr:
            cond_state = tr.get_state(condition_handle)
            if cond_state.Presence:
                cond_state.Presence = False
            sig_state = tr.get_state(signal_handle)
            if sig_state.Presence != AlertSignalPresence.OFF:
                sig_state.Presence = AlertSignalPresence.OFF


def metrics_info(provider):
    # use print instead of logger
    print(f"Humidity = {provider.mdib.entities.by_handle('humidity').state.MetricValue.Value}")
    print(f"Temperature = {provider.mdib.entities.by_handle('temperature').state.MetricValue.Value}")


def joystick():
    global show_temp, AMPLITUDE
    while True:
        events = sense.stick.get_events()
        for e in events:
            if e.action == "pressed" and e.direction == "middle":
                show_temp = not show_temp
            if e.action == "pressed" and e.direction == "left":
                # modify AMPLITUDE safely
                with AMPLITUDE_LOCK:
                    AMPLITUDE = AMPLITUDE - 0.3
                    if AMPLITUDE < 0:
                        AMPLITUDE = 0
                        print("Min volume reached")
            if e.action == "pressed" and e.direction == "right":
                with AMPLITUDE_LOCK:
                    AMPLITUDE = AMPLITUDE + 0.3
        time.sleep(0.05)


def show_startup_screen(symbol_func, number, number_color, bg_color):
    """Helper to display a screen during startup."""
    sense.clear()
    symbol_func(255, 255, 255)
    show_number(number, *number_color)
    background(*bg_color)
    time.sleep(2)


def first_start(provider):
    # sense.show_message("Started SDC-mode")

    lowTempThreshold = int(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Lower)
    highTempThreshold = int(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Upper)
    midTemp = int((highTempThreshold + lowTempThreshold) / 2)

    lowHumThreshold = int(provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Lower)
    highHumThreshold = int(provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Upper)
    midHum = int((highHumThreshold + lowHumThreshold) / 2)

    temp_color = (255, 165, 40)
    hum_color = (255, 100, 40)

    show_startup_screen(t_show, lowTempThreshold, temp_color, (51, 153, 255))
    show_startup_screen(t_show, midTemp, temp_color, (51, 204, 51))
    show_startup_screen(t_show, highTempThreshold, temp_color, (255, 51, 51))

    show_startup_screen(h_show, lowHumThreshold, hum_color, (204, 153, 102))
    show_startup_screen(h_show, midHum, hum_color, (51, 204, 51))
    show_startup_screen(h_show, highHumThreshold, hum_color, (51, 102, 204))


async def handle_requests(provider, share_state_temp, share_state_hum):
    """Handles incoming provider requests."""
    global TIME_T, TIME_H
    if not provider.requests:
        return

    request = provider.requests[0]  # Get request without removing it yet
    try:
        #print(request.raw_data)
        t = time.time()

        temp_alert_control = provider.find_string_in_request(request, "temperature_alert_control")
        hum_alert_control = provider.find_string_in_request(request, "humidity_alert_control")
        temp_threshold_control = provider.find_string_in_request(request, "temperature_threshold_control")
        hum_threshold_control = provider.find_string_in_request(request, "humidity_threshold_control")

        # Check for threshold changes
        low_temp_changed = provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Lower != share_state_temp.Lower
        high_temp_changed = provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Upper != share_state_temp.Upper
        low_hum_changed = provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Lower != share_state_hum.Lower
        high_hum_changed = provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Upper != share_state_hum.Upper

        if hum_alert_control:
            REQUEST["humidity"] = True
            TIME_H = t
        elif temp_alert_control:
            REQUEST["temperature"] = True
            TIME_T = t
        elif temp_threshold_control:
            show_celsius_display((255, 165, 40))
            if low_temp_changed:
                share_state_temp.Lower = provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Lower
                background(51, 153, 255)
            elif high_temp_changed:
                share_state_temp.Upper = provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Upper
                background(130, 0, 0)
            await asyncio.sleep(2)
        elif hum_threshold_control:
            show_humidity_display((255, 100, 40))
            if low_hum_changed:
                share_state_hum.Lower = provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Lower
                background(204, 153, 102)
            elif high_hum_changed:
                share_state_hum.Upper = provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Upper
                background(51, 102, 204)
            await asyncio.sleep(2)
        time.sleep(0.2)
        sense.clear()
        await asyncio.sleep(0.5)
    finally:
        provider.requests.pop(0)  # Now remove the request


async def process_metric(provider, metric_name, value, symbol_func, number_color, timeout_duration):
    """Handles display and alarm logic for a given metric."""
    global REQUEST, TIME_T, TIME_H
    t = time.time()
    time_key = 'TIME_T' if metric_name == 'temperature' else 'TIME_H'
    current_time = globals()[time_key]

    symbol_func(255, 255, 0)
    show_number(int(value + 0.5), *number_color)

    if REQUEST[metric_name]:
        if t - current_time < timeout_duration:
            evaluate_alarm(provider, metric_name, value, True)
            await asyncio.sleep(1)
            return True  # Indicate that we should 'continue' the loop
        else:
            evaluate_alarm(provider, metric_name, value, False)
            REQUEST[metric_name] = False
            globals()[time_key] = 0
            await asyncio.sleep(1)
            return True  # Indicate that we should 'continue' the loop

    evaluate_alarm(provider, metric_name, value, False)
    return False


async def main(provider):
    global TIME_T, TIME_H
    share_state_temp = deepcopy(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0])
    share_state_hum = deepcopy(provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0])

    while True:
        t = time.time()
        sense.clear()
        print("Temp Low = " + str(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Lower))
        print("Temp High = " + str(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Upper))
        print("Hum Low = " + str(provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Lower))
        print("Hum High = " + str(provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Upper))

        await handle_requests(provider, share_state_temp, share_state_hum)

        humidity = sense.humidity
        temperature = sense.temperature

        update_humidity(provider, Decimal(humidity))

        update_temperature(provider, Decimal(temperature))
        metrics_info(provider)

        if show_temp:
            should_continue = await process_metric(provider, 'temperature', temperature, t_show, (255, 165, 40), 3)
            if should_continue:
                continue
        else:
            should_continue = await process_metric(provider, 'humidity', humidity, h_show, (255, 100, 40), 6)
            if should_continue:
                continue

        await asyncio.sleep(1)


# Add configuration constants and lock for thread-safe amplitude access
NETWORK_ADAPTER = "wlan0"
MDIB_FILE = "Pi5 CPU Temp + Fans Control/mdib2.xml"
AMPLITUDE_LOCK = threading.Lock()

if __name__ == '__main__':
    # logging.basicConfig(level=logging.INFO)
    t = threading.Thread(target=joystick, daemon=True)
    t.start()
    # UUID objects (universally unique identifiers) according to RFC 4122
    base_uuid = uuid.UUID('{cc013678-79f6-403c-998f-3cc0cc050231}')
    my_uuid = uuid.uuid5(base_uuid, "test_provider_2")

    # getting mdib from xml file and converting it to mdib.py object
    mdib = ProviderMdib.from_mdib_file(MDIB_FILE)

    # All necessary components for the provider

    model = ThisModelType(model_name='TestModel',
                          manufacturer='TestManufacturer',
                          manufacturer_url='http://testurl.com')
    # Dependency injection: This class defines which component implementations the sdc provider will use
    components = SdcProviderComponents(role_provider_class=ExtendedProduct)
    # ThisDeviceType object with friendly name and serial number
    device = ThisDeviceType(friendly_name='TestDevice2', serial_number='123456')
    # UDP based discovery on single network adapter
    discovery = WSDiscoverySingleAdapter(NETWORK_ADAPTER)  # configurable adapter

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

    # Publishing the provider into Network to make it visible for consumers
    provider.publish()

    #first_start(provider)
    # register()

    with provider.mdib.metric_state_transaction() as tr:
        id = tr.get_state("device_id")
        id.MetricValue.Value = Decimal(DEVICE_ID)

    try:
        asyncio.run(main(provider))
    except KeyboardInterrupt:
        print("Stopping provider...")
        provider.stop_all()
        discovery.stop()
        print("Provider stopped.")