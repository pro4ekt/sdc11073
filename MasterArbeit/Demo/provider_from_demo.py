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
from PIL import Image
from sense_hat import SenseHat

from mydbworker import DBWorker
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
settings = False
lower_threshold = True
VALUE = 0
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
def show_celsius_display(c_color, value=None):
    O = (0, 0, 0)
    C = c_color

    pixels = [
        C, C, C, O, O, C, C, C,
        O, C, O, O, O, O, C, O,
        O, C, O, O, O, O, C, O,
        O, O, O, O, O, O, O, O,
        O, O, O, O, O, O, O, O,
        O, O, O, O, O, O, O, O,
        O, O, O, O, O, O, O, O,
        O, O, O, O, O, O, O, O,
    ]
    sense.set_pixels(pixels)

    show_number(int(value) if value is not None else 0, *(c_color))
def show_humidity_display(h_color, value=None):
    O = (0, 0, 0)
    C = h_color

    pixels = [
        C, O, C, O, O, C, C, C,
        C, C, C, O, O, O, C, O,
        C, O, C, O, O, O, C, O,
        O, O, O, O, O, O, O, O,
        O, O, O, O, O, O, O, O,
        O, O, O, O, O, O, O, O,
        O, O, O, O, O, O, O, O,
        O, O, O, O, O, O, O, O,
    ]
    sense.set_pixels(pixels)

    show_number(int(value) if value is not None else 0, *(h_color))
def update_humidity(provider, value: Decimal, db):
    with provider.mdib.metric_state_transaction() as tr:
        temp_state = tr.get_state("humidity")
        mv = temp_state.MetricValue
        mv.Value = value
    db.observation_register("humidity", value)
def update_temperature(provider, value: Decimal, db):
    with provider.mdib.metric_state_transaction() as tr:
        temp_state = tr.get_state("temperature")
        mv = temp_state.MetricValue
        mv.Value = value
    db.observation_register("temperature", value)
def evaluate_alarm(provider, metric_name: str, value: float, timeout: bool, db):
    """
    Evaluates the current metric value against the configured thresholds.
    Manages the SDC AlertCondition and AlertSignal states, updates the physical display (SenseHat),
    handles audio feedback, and logs events to the database.
    """
    # Load specific configuration for the given metric (temperature or humidity)
    config = ALARM_CONFIG[metric_name]
    metric_handle = config['metric_handle']
    condition_handle = config['condition_handle']
    signal_handle = config['signal_handle']
    colors = config['colors']
    sound_freq = config['sound_freq']

    # Retrieve current physiological ranges (Lower and Upper limits) from the MDIB
    low_threshold = provider.mdib.entities.by_handle(metric_handle).state.PhysiologicalRange[0].Lower
    high_threshold = provider.mdib.entities.by_handle(metric_handle).state.PhysiologicalRange[0].Upper

    # Check if the current value is outside the safe range
    is_out_of_range = (value > high_threshold) or (value < low_threshold)

    if is_out_of_range:
        # --- Alarm Active State ---
        # Open a transaction to update alert states atomically
        with provider.mdib.alert_state_transaction() as tr:
            # Set the Alert Condition (logical state of the alarm) to Present
            cond_state = tr.get_state(condition_handle)
            cond_state.Presence = True

            # Set the Alert Signal (audible/visible manifestation).
            # If 'timeout' is True (e.g. user acknowledged/paused), the signal is suppressed (OFF).
            sig_state = tr.get_state(signal_handle)
            sig_state.Presence = AlertSignalPresence.OFF if timeout else AlertSignalPresence.ON

        # Register the alarm occurrence in the external database if not already tracked
        if not any(metric_handle in alarm for alarm in db.alarms):
            db.alarm_register(metric_handle)

        # Update physical display background color (Blue/Tan for Low, Red/Blue for High)
        if value < low_threshold:
            background(*colors['low'])
        else:
            background(*colors['high'])

        # Play acoustic alarm only if the signal is not suppressed (timeout is False)
        if not timeout:
            with AMPLITUDE_LOCK:
                amp = AMPLITUDE
            sound(1, sound_freq, amp)
    else:
        # --- Normal State ---
        # Set display background to green indicating normal operation
        background(*colors['normal'])

        # Open transaction to reset alert states
        with provider.mdib.alert_state_transaction() as tr:
            cond_state = tr.get_state(condition_handle)
            # Clear the Alert Condition if it was previously set
            if cond_state.Presence:
                cond_state.Presence = False

            # Turn off the Alert Signal
            sig_state = tr.get_state(signal_handle)
            if sig_state.Presence != AlertSignalPresence.OFF:
                sig_state.Presence = AlertSignalPresence.OFF

        # If the alarm was active in the database, resolve it
        if any(metric_handle in alarm for alarm in db.alarms):
            db.alarm_resolve(metric_handle)
def metrics_info(provider):
    # use print instead of logger
    print(f"Humidity = {provider.mdib.entities.by_handle('humidity').state.MetricValue.Value}")
    print(f"Temperature = {provider.mdib.entities.by_handle('temperature').state.MetricValue.Value}")
def joystick():
    global show_temp, AMPLITUDE, settings, VALUE, lower_threshold
    while True:
        events = sense.stick.get_events()
        for e in events:
            if (settings):
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
                # modify AMPLITUDE safely
                with AMPLITUDE_LOCK:
                    AMPLITUDE = AMPLITUDE - 0.3
                    if AMPLITUDE < 0:
                        AMPLITUDE = 0
                        print("Min volume reached")
            if e.action == "pressed" and e.direction == "right":
                with AMPLITUDE_LOCK:
                    AMPLITUDE = AMPLITUDE + 0.3
            if e.action == "pressed" and e.direction == "up":
                settings = not settings
                lower_threshold = not lower_threshold
            if e.action == "pressed" and e.direction == "down":
                settings = not settings
        time.sleep(0.05)

async def threshold_setting(provider):
    global VALUE
    if (show_temp):
        if (lower_threshold):
            value = Decimal(VALUE) + provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Lower
        else:
            value = Decimal(VALUE) + provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Upper
    else:
        if (lower_threshold):
            value = Decimal(VALUE) + provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Lower
        else:
            value = Decimal(VALUE) + provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Upper
    sense.clear()
    O = (0, 0, 0)
    C = (255, 100, 40)
    pixels = [
        O, O, O, O, O, C, C, C,
        O, O, O, O, O, O, C, O,
        O, O, O, O, O, O, C, O,
        O, O, O, O, O, O, O, O,
        O, O, O, O, O, O, O, O,
        O, O, O, O, O, O, O, O,
        O, O, O, O, O, O, O, O,
        O, O, O, O, O, O, O, O,
    ]
    sense.set_pixels(pixels)

    show_number(int(value), 255, 100, 40)

    await asyncio.sleep(1)
    return value

def show_startup_screen(symbol_func, number, number_color, bg_color, name):
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

    show_startup_screen(t_show, lowTempThreshold, temp_color, (51, 153, 255), "startup_temp_low.png")
    show_startup_screen(t_show, midTemp, temp_color, (51, 204, 51), "startup_temp_mid.png")
    show_startup_screen(t_show, highTempThreshold, temp_color, (255, 51, 51), "startup_temp_high.png")

    show_startup_screen(h_show, lowHumThreshold, hum_color, (204, 153, 102), "startup_hum_low.png")
    show_startup_screen(h_show, midHum, hum_color, (51, 204, 51), "startup_hum_mid.png")
    show_startup_screen(h_show, highHumThreshold, hum_color, (51, 102, 204), "startup_hum_high.png")

async def handle_requests(provider, share_state_temp, share_state_hum):
    """
    Handles incoming provider requests from the SDC network.

    It checks for specific keywords in the request to determine if it's an alert
    activation or a threshold modification. It updates the internal state and
    visualizes the changes on the SenseHat LED matrix.
    """
    global TIME_T, TIME_H
    # If there are no pending requests, exit immediately.
    if not provider.requests:
        return
    # Peek at the first request in the queue without removing it (to process it safely).
    request = provider.requests[0]
    try:
        # print(request.raw_data)
        t = time.time()
        # Check if the request contains specific control strings indicating
        # which operation needs to be performed (alert control or threshold update).
        temp_alert_control = provider.find_string_in_request(request, "temperature_alert_control")
        hum_alert_control = provider.find_string_in_request(request, "humidity_alert_control")
        temp_threshold_control = provider.find_string_in_request(request, "temperature_threshold_control")
        hum_threshold_control = provider.find_string_in_request(request, "humidity_threshold_control")

        # Compare current MDIB thresholds with the locally stored state copies to detect changes.
        low_temp_changed = provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[
                               0].Lower != share_state_temp.Lower
        high_temp_changed = provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[
                                0].Upper != share_state_temp.Upper
        low_hum_changed = provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[
                              0].Lower != share_state_hum.Lower
        high_hum_changed = provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[
                               0].Upper != share_state_hum.Upper
        # --- Request Processing Logic ---
        # Case 1: Humidity Alert Triggered
        if hum_alert_control:
            REQUEST["humidity"] = True
            TIME_H = t # Reset timer for humidity alert display
        # Case 2: Temperature Alert Triggered
        elif temp_alert_control:
            REQUEST["temperature"] = True
            TIME_T = t # Reset timer for temperature alert display

        # Case 3: Temperature Threshold Adjustment
        elif temp_threshold_control:
            # Update local state if MDIB values changed
            if low_temp_changed:
                share_state_temp.Lower = provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[
                    0].Lower
            if high_temp_changed:
                share_state_temp.Upper = provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[
                    0].Upper

            # Visualize the threshold change on the LED matrix
            if low_temp_changed or high_temp_changed:
                if low_temp_changed:
                    # Show Lower threshold (Blue background)
                    show_celsius_display((255, 165, 40), share_state_temp.Lower)
                    background(51, 153, 255)
                else:  # high_temp_changed
                    # Show Upper threshold (Red background)
                    show_celsius_display((255, 165, 40), share_state_temp.Upper)
                    background(130, 0, 0)
            else:
                # If no value changed but request was received, default to showing upper threshold
                show_celsius_display((255, 165, 40), share_state_temp.Upper)
                background(130, 0, 0)

            # Hold the display for 2 seconds to let user see the change
            await asyncio.sleep(2)

        # Case 4: Humidity Threshold Adjustment
        elif hum_threshold_control:
            # Update local state if MDIB values changed
            if low_hum_changed:
                share_state_hum.Lower = provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Lower
            if high_hum_changed:
                share_state_hum.Upper = provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Upper

            # Visualize the threshold change on the LED matrix
            if low_hum_changed or high_hum_changed:
                if low_hum_changed:
                    # Show Lower threshold (Tan background)
                    show_humidity_display((255, 100, 40), share_state_hum.Lower)
                    background(204, 153, 102)
                else:  # high_hum_changed
                    # Show Upper threshold (Blue/Grey background)
                    show_humidity_display((255, 100, 40), share_state_hum.Upper)
                    background(51, 102, 204)
            else:
                # Default to showing upper threshold
                show_humidity_display((255, 100, 40), share_state_hum.Upper)
                background(51, 102, 204)

            # Hold the display for 2 seconds
            await asyncio.sleep(2)

        # Short pause directly after processing
        await asyncio.sleep(0.2)
        sense.clear()
        await asyncio.sleep(0.5)
    finally:
        # Always remove the processed request from the queue, regardless of success/failure
        provider.requests.pop(0)

async def process_metric(provider, metric_name, value, symbol_func, number_color, timeout_duration, db):
    """Handles display and alarm logic for a given metric."""
    global REQUEST, TIME_T, TIME_H
    t = time.time()
    time_key = 'TIME_T' if metric_name == 'temperature' else 'TIME_H'
    current_time = globals()[time_key]

    symbol_func(255, 255, 0)
    show_number(int(value + 0.5), *number_color)

    if REQUEST[metric_name]:
        if t - current_time < timeout_duration:
            evaluate_alarm(provider, metric_name, value, True, db)
            await asyncio.sleep(1)
            return True  # Indicate that we should 'continue' the loop
        else:
            evaluate_alarm(provider, metric_name, value, False, db)
            REQUEST[metric_name] = False
            globals()[time_key] = 0
            await asyncio.sleep(1)
            return True  # Indicate that we should 'continue' the loop

    evaluate_alarm(provider, metric_name, value, False, db)
    return False

async def main(provider, db):
    global TIME_T, TIME_H, VALUE, lower_threshold
    # Create deep copies of the physiological ranges to detect changes later
    share_state_temp = deepcopy(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0])
    share_state_hum = deepcopy(provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0])
    new_threshold = None  # Initialize new_threshold to prevent UnboundLocalError
    metric_to_validate = None

    while True:
        t = time.time()
        sense.clear()
        # Log current physiological ranges to console for debugging
        print("Temp Low = " + str(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Lower))
        print("Temp High = " + str(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Upper))
        print("Hum Low = " + str(provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Lower))
        print("Hum High = " + str(provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Upper))

        # Process any pending incoming requests (e.g. alert controls)
        await handle_requests(provider, share_state_temp, share_state_hum)

        # specific SDC logic:
        # 1. Get raw values from hardware sensors
        humidity = sense.humidity
        temperature = sense.temperature

        # 2. Update MDIB and database with new sensor values
        update_humidity(provider, Decimal(humidity), db)
        update_temperature(provider, Decimal(temperature), db)
        metrics_info(provider)

        # 3. Handle threshold adjustment mode if 'settings' is active (via joystick)
        if settings:
            new_threshold = await threshold_setting(provider)
            continue

        # 4. If a new threshold was set, start the validation process (SDC workflow)
        try:
            if new_threshold is not None:
                metric_handle = "temperature" if show_temp else "humidity"
                if metric_handle == "temperature":
                    # Mark data quality as 'CalibrationOngoing' before applying changes
                    with provider.mdib.metric_state_transaction() as tr:
                        state = tr.get_state(metric_handle)
                        state.MetricValue.MetricQuality.Validity = MeasurementValidity.CALIBRATION_ONGOING
                    metric_to_validate = metric_handle  # Mark this metric for validation
                VALUE = 0
        except NameError:
            pass
        finally:
            pass

        # 5. Wait for validation (simulating external check or manual confirmation)
        try:
            if metric_to_validate is not None:
                print(f"Waiting for validation of {metric_to_validate}...")
                while True:
                    if (metric_handle == "temperature"):
                        state = provider.mdib.entities.by_handle(metric_to_validate).state
                        # If validated, apply the new threshold
                        if state.MetricValue.MetricQuality.Validity == MeasurementValidity.VALIDATED_DATA:
                            print(f"{metric_to_validate} is validated.")
                            with provider.mdib.metric_state_transaction() as tr:
                                state = tr.get_state(metric_handle)
                                pr = state.PhysiologicalRange[0]
                                if lower_threshold:
                                    pr.Lower = Decimal(new_threshold)
                                else:
                                    pr.Upper = Decimal(new_threshold)
                            break
                        # If validation failed, exit loop without applying
                        if state.MetricValue.MetricQuality.Validity == MeasurementValidity.QUESTIONABLE:
                            print(f"{metric_to_validate} is not validated.")
                            break
                    await asyncio.sleep(0.5)  # Wait and check again
            elif metric_to_validate is None and new_threshold is not None:
                # Direct application if no validation logic is required for this metric
                with provider.mdib.metric_state_transaction() as tr:
                    state = tr.get_state(metric_handle)
                    pr = state.PhysiologicalRange[0]
                    if lower_threshold:
                        pr.Lower = Decimal(new_threshold)
                    else:
                        pr.Upper = Decimal(new_threshold)
        except NameError:
            pass
        finally:
            # Restore validity to VALID after operation
            if metric_to_validate is not None and metric_handle == "temperature":
                with provider.mdib.metric_state_transaction() as tr:
                    state = tr.get_state(metric_handle)
                    state.MetricValue.MetricQuality.Validity = MeasurementValidity.VALID
            metric_to_validate = None
            new_threshold = None
            lower_threshold = True
            pass

        # 6. Display current value on LED matrix and evaluate alarms
        if show_temp:
            should_continue = await process_metric(provider, 'temperature', temperature, t_show, (255, 165, 40), 10, db)
            if should_continue:
                continue
        else:
            should_continue = await process_metric(provider, 'humidity', humidity, h_show, (255, 100, 40), 10, db)
            if should_continue:
                continue

        await asyncio.sleep(1)

# Add configuration constants and lock for thread-safe amplitude access
NETWORK_ADAPTER = "wlan0"
MDIB_FILE = "Pi5 CPU Temp + Fans Control/demo_mdib.xml"
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

    while True:
        try:
            db = DBWorker(host="10.248.255.140", user="testuser1", password="1234", database="demo_db",
                          mdib=provider.mdib)
            db.delete_db()
            db.register(device_name="Sense Hat", device_type="provider", device_location="TTZ Bad Kissingen")
            DEVICE_ID = db.device_id
            break
        except:
            print("Database connection failed. Retrying in 5 seconds...")
            continue

    with provider.mdib.metric_state_transaction() as tr:
        id = tr.get_state("device_id")
        id.MetricValue.Value = Decimal(DEVICE_ID)

    first_start(provider)
    try:
        asyncio.run(main(provider, db))
    except KeyboardInterrupt:
        print("Stopping provider...")
        provider.stop_all()
        discovery.stop()
        print("Provider stopped.")

