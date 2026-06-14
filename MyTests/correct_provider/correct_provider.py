from __future__ import annotations

import logging
import time
import uuid
import random
import math
import asyncio
import os
from decimal import Decimal
from copy import deepcopy

from sdc11073.loghelper import basic_logging_setup
from sdc11073.mdib import ProviderMdib
from sdc11073.provider import SdcProvider
from sdc11073.provider.components import SdcProviderComponents
from sdc11073.roles.product import ExtendedProduct
from sdc11073.wsdiscovery import WSDiscoverySingleAdapter
from sdc11073.xml_types.dpws_types import ThisDeviceType
from sdc11073.xml_types.dpws_types import ThisModelType
from sdc11073.xml_types.pm_types import AlertSignalPresence
from sdc11073.xml_types.pm_types import MeasurementValidity

# Mocking MySdcProvider to replace missing myproviderimpl
class MySdcProvider(SdcProvider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # List to store requests if we were intercepting them.
        # Since we don't have the interception logic, this will stay empty.
        self.requests = []

    def find_string_in_request(self, request, search_string):
        # Dummy implementation
        return False

# --- Constants for Alarms ---
ALARM_CONFIG = {
    'temperature': {
        'metric_handle': 'temperature',
        'condition_handle': 'al_condition_temperature',
        'signal_handle': 'al_signal_temperature',
    },
    'humidity': {
        'metric_handle': 'humidity',
        'condition_handle': 'al_condition_humidity',
        'signal_handle': 'al_signal_humidity',
    }
}

# Global state for request handling simulation
REQUEST = {"temperature": False, "humidity": False}
TIME_T = 0
TIME_H = 0


def update_humidity(provider, value: Decimal):
    with provider.mdib.metric_state_transaction() as tr:
        temp_state = tr.get_state("humidity")
        mv = temp_state.MetricValue
        mv.Value = value

def update_temperature(provider, value: Decimal):
    with provider.mdib.metric_state_transaction() as tr:
        temp_state = tr.get_state("temperature")
        mv = temp_state.MetricValue
        mv.Value = value

def evaluate_alarm(provider, metric_name: str, value: float, timeout: bool):
    """
    Evaluates the current metric value against the configured thresholds.
    Manages the SDC AlertCondition and AlertSignal states.
    """
    config = ALARM_CONFIG[metric_name]
    metric_handle = config['metric_handle']
    condition_handle = config['condition_handle']
    signal_handle = config['signal_handle']

    # Retrieve current physiological ranges from the MDIB
    low_threshold = provider.mdib.entities.by_handle(metric_handle).state.PhysiologicalRange[0].Lower
    high_threshold = provider.mdib.entities.by_handle(metric_handle).state.PhysiologicalRange[0].Upper

    # Check if the current value is outside the safe range
    is_out_of_range = (value > high_threshold) or (value < low_threshold)

    if is_out_of_range:
        # --- Alarm Active State ---
        with provider.mdib.alert_state_transaction() as tr:
            # Set the Alert Condition (logical state of the alarm) to Present
            cond_state = tr.get_state(condition_handle)
            cond_state.Presence = True

            # Set the Alert Signal (audible/visible manifestation).
            sig_state = tr.get_state(signal_handle)
            sig_state.Presence = AlertSignalPresence.OFF if timeout else AlertSignalPresence.ON
    else:
        # --- Normal State ---
        with provider.mdib.alert_state_transaction() as tr:
            cond_state = tr.get_state(condition_handle)
            # Clear the Alert Condition if it was previously set
            if cond_state.Presence:
                cond_state.Presence = False

            # Turn off the Alert Signal
            sig_state = tr.get_state(signal_handle)
            if sig_state.Presence != AlertSignalPresence.OFF:
                sig_state.Presence = AlertSignalPresence.OFF

def metrics_info(provider):
    # use print instead of logger
    t_val = provider.mdib.entities.by_handle('temperature').state.MetricValue.Value
    h_val = provider.mdib.entities.by_handle('humidity').state.MetricValue.Value
    print(f"Temperature = {t_val}, Humidity = {h_val}")

async def handle_requests(provider, share_state_temp, share_state_hum):
    """
    Handles incoming provider requests from the SDC network.
    """
    global TIME_T, TIME_H
    # If there are no pending requests, exit immediately.
    if not provider.requests:
        return
    # Peek at the first request in the queue without removing it (to process it safely).
    request = provider.requests[0]
    try:
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
        if hum_alert_control:
            REQUEST["humidity"] = True
            TIME_H = t
        elif temp_alert_control:
            REQUEST["temperature"] = True
            TIME_T = t

        elif temp_threshold_control:
            # Update local state if MDIB values changed
            if low_temp_changed:
                share_state_temp.Lower = provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Lower
            if high_temp_changed:
                share_state_temp.Upper = provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Upper

        elif hum_threshold_control:
            # Update local state if MDIB values changed
            if low_hum_changed:
                share_state_hum.Lower = provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Lower
            if high_hum_changed:
                share_state_hum.Upper = provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Upper

        # Short pause simulation
        await asyncio.sleep(0.2)
    finally:
        # Always remove the processed request from the queue, regardless of success/failure
        provider.requests.pop(0)

async def process_metric(provider, metric_name, value, timeout_duration):
    """Handles alarm logic for a given metric."""
    global REQUEST, TIME_T, TIME_H
    t = time.time()
    time_key = 'TIME_T' if metric_name == 'temperature' else 'TIME_H'
    current_time = globals()[time_key]

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
    # Create deep copies of the physiological ranges to detect changes later
    share_state_temp = deepcopy(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0])
    share_state_hum = deepcopy(provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0])

    # Initial mock values
    temperature = Decimal(37.0)
    humidity = Decimal(45.0)
    counter = 0.0

    while True:
        # Log current physiological ranges to console for debugging
        print("Temp Low = " + str(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Lower))
        print("Temp High = " + str(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Upper))
        print("Hum Low = " + str(provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Lower))
        print("Hum High = " + str(provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Upper))

        # Process any pending incoming requests (e.g. alert controls)
        await handle_requests(provider, share_state_temp, share_state_hum)

        # 1. Update Mock Values (Sine Wave Animation for Periodic Alarms)
        # Temp Safe ~[30, 45]. Center: 37.5. Amplitude: 10 => Range [27.5, 47.5] for definitive alarming
        # Hum Safe ~[18, 35]. Center: 26.5. Amplitude: 15 => Range [11.5, 41.5] for definitive alarming
        # Different frequencies and phases ensure they don't alarm exactly at same times always
        val_temp = 37.5 + 10.0 * math.sin(counter * 0.1)
        val_hum = 26.5 + 15.0 * math.sin(counter * 0.08 + 2.0)

        temperature = Decimal(val_temp)
        humidity = Decimal(val_hum)
        counter += 1.0

        # Oscillation ensures values go OUT of range periodically. No clamping.

        # 2. Update MDIB
        update_humidity(provider, humidity)
        update_temperature(provider, temperature)
        metrics_info(provider)

        # 6. Evaluate alarms
        should_continue_temp = await process_metric(provider, 'temperature', float(temperature), 10)
        if should_continue_temp:
            continue

        should_continue_hum = await process_metric(provider, 'humidity', float(humidity), 10)
        if should_continue_hum:
            continue

        await asyncio.sleep(1)

# Add configuration constants
NETWORK_ADAPTER = "Wi-Fi"
MDIB_FILE = "correct_mdib.xml"

if __name__ == '__main__':
    # basic_logging_setup()

    # UUID objects (universally unique identifiers) according to RFC 4122
    base_uuid = uuid.UUID('{cc013678-79f6-403c-998f-3cc0cc050234}')
    my_uuid = uuid.uuid5(base_uuid, "test_provider_mock")

    # getting mdib from xml file and converting it to mdib.py object
    # Construct absolute path to MDIB file relative to this script
    current_dir = os.path.dirname(os.path.abspath(__file__))
    mdib_path = os.path.join(current_dir, MDIB_FILE)

    if not os.path.exists(mdib_path):
        # Fallback to look in current working directory if script executed directly
        mdib_path = MDIB_FILE

    mdib = ProviderMdib.from_mdib_file(mdib_path)

    # All necessary components for the provider
    model = ThisModelType(model_name='MockModel',
                          manufacturer='MockManufacturer',
                          manufacturer_url='http://mockurl.com')

    # Dependency injection: This class defines which component implementations the sdc provider will use
    components = SdcProviderComponents(role_provider_class=ExtendedProduct)

    # ThisDeviceType object with friendly name and serial number
    device = ThisDeviceType(friendly_name='MockProvider', serial_number='123456')

    # UDP based discovery on single network adapter
    print(f"Starting discovery on {NETWORK_ADAPTER}")
    discovery = WSDiscoverySingleAdapter(NETWORK_ADAPTER)

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

    try:
        asyncio.run(main(provider))
    except KeyboardInterrupt:
        print("Stopping provider...")
        provider.stop_all()
        discovery.stop()
        print("Provider stopped.")