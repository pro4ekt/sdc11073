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

from sdc11073.xml_types import pm_qnames as pm
from sdc11073.xml_types import pm_types
from sdc11073.mdib import ProviderMdib
from sdc11073.provider import SdcProvider
from sdc11073.provider.components import SdcProviderComponents
from sdc11073.roles.product import ExtendedProduct
from sdc11073.wsdiscovery import WSDiscoverySingleAdapter
from sdc11073.xml_types.dpws_types import ThisDeviceType
from sdc11073.xml_types.dpws_types import ThisModelType
from sdc11073.xml_types.pm_types import AlertSignalPresence
from sdc11073.location import SdcLocation
from sdc11073.xml_types.pm_types import Measurement, RelatedMeasurement

# ── Increase subscription error tolerance ────────────────────────────────────
# By default MAX_NOTIFY_ERRORS=1: after a single failed notification push
# the subscription is marked invalid and closed within ~1 second.
# sdcX Consumer's BeastTLSDetector rejects the first push attempts, causing
# premature subscription closure and a consumer segfault.
# Raising the limit keeps subscriptions alive for their full expiry period.
from sdc11073.provider.subscriptionmgr_base import SubscriptionBase
SubscriptionBase.MAX_NOTIFY_ERRORS = 999

@classmethod
def _related_measurement_from_node(cls, node):
    # Передаем заглушку Measurement, чтобы обойти ошибку __init__ "missing 1 required argument 'value'"
    obj = cls(Measurement(None, None))
    obj.update_from_node(node)
    return obj

RelatedMeasurement.from_node = _related_measurement_from_node

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

    def publish(self):
        """Override to add MDC type scopes required by sdcX CompleteConsumer."""
        scopes = self._components.scopes_factory(self._mdib)
        # MDC codes expected by sdcX CompleteConsumer (PulsoximeterProvider types)
        for code in ('sdc.cdc.type:///130535', 'sdc.cdc.type:///130536', 'sdc.cdc.type:///130736'):
            if code not in scopes.text:
                scopes.text.append(code)
        x_addrs = self.get_xaddrs()
        self._wsdiscovery.publish_service(
            self.epr_urn,
            list(self._mdib.sdc_definitions.MedicalDeviceTypesFilter),
            scopes,
            x_addrs,
        )

    def simulate_self_checkout(self, is_successful: bool = True) -> bool:
        vmd_states = self.mdib.states.NODETYPE.get(pm.VmdState, [])
        if not vmd_states:
            print("[Provider] Error: No VMD states found in MDIB.")
            with self.mdib.metric_state_transaction() as tr:
                device_health_state = tr.get_state("device_health")
                mv = device_health_state.MetricValue
                mv.Value = Decimal(0)
            return False
            
        vmd_handle = vmd_states[0].DescriptorHandle
        
        with self.mdib.component_state_transaction() as mgr:
            state = mgr.get_state(vmd_handle)
            if is_successful:
                state.ActivationState = pm_types.ComponentActivation.STANDBY
            else:
                state.ActivationState = pm_types.ComponentActivation.FAIL
                
        print(f"[Provider] VMD {vmd_handle} ActivationState changed to {state.ActivationState}")
        
        # Печать проверки!
        check_state = self.mdib.states.descriptor_handle.get_one(vmd_handle)
        print(f"[Provider Test] Current ActivationState of {vmd_handle} in MDIB is now: {check_state.ActivationState}")

        with self.mdib.metric_state_transaction() as tr:
            device_health_state = tr.get_state("device_health")
            mv = device_health_state.MetricValue
            mv.Value = Decimal(100)

        return True

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

def get_consumer_count(provider) -> int:
    """Count active consumer subscriptions across all subscription managers."""
    total = 0
    for mgr in getattr(provider, '_subscriptions_managers', {}).values():
        subs = getattr(mgr, '_subscriptions', None)
        if subs is not None:
            total += len(subs.objects)
    return total


async def main(provider):
    # Create deep copies of the physiological ranges to detect changes later
    share_state_temp = deepcopy(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0])
    share_state_hum = deepcopy(provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0])

    # Initial mock values
    temperature = Decimal(37.0)
    humidity = Decimal(45.0)
    counter = 0.0

    while True:
        # ─── Connection status ────────────────────────────────────────────
        n = get_consumer_count(provider)
        status = f'✓ {n} consumer(s) connected' if n > 0 else '✗ no consumers connected'
        print(f'[Provider] {status}', flush=True)

        # Log current physiological ranges to console for debugging
        """
         print("Temp Low = " + str(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Lower))
        print("Temp High = " + str(provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0].Upper))
        print("Hum Low = " + str(provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Lower))
        print("Hum High = " + str(provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0].Upper))
        """
        patients = provider.mdib.context_states.NODETYPE.get(pm.PatientContextState, [])
        given_names = [p.CoreData.Givenname for p in patients if p.CoreData and p.CoreData.Givenname]
        heights = [p.CoreData.Height.MeasuredValue if p.CoreData and p.CoreData.Height else None for p in patients]
        weights = [p.CoreData.Weight.MeasuredValue if p.CoreData and p.CoreData.Weight else None for p in patients]
        
        locations = provider.mdib.context_states.NODETYPE.get(pm.LocationContextState, [])
        rooms = [l.LocationDetail.Room for l in locations if getattr(l, "LocationDetail", None)]
        
        workflows = provider.mdib.context_states.NODETYPE.get(pm.WorkflowContextState, [])
        danger_codes = []
        for w in workflows:
            if getattr(w, "WorkflowDetail", None):
                # В зависимости от того, как sdc11073 парсит нестандартные/кастомные теги,
                # значение может лежать в .DangerCode, .RelevantClinicalInfo или расширениях.
                if hasattr(w.WorkflowDetail, 'DangerCode') and w.WorkflowDetail.DangerCode:
                    code = w.WorkflowDetail.DangerCode[0].Code
                    danger_codes.append(code)
                else:
                    danger_codes.append(None)
        
        ensembles = provider.mdib.context_states.NODETYPE.get(pm.EnsembleContextState, [])
        ens_info_list = []
        for e in ensembles:
            ctx_assoc = getattr(e, 'ContextAssociation', 'N/A')
            if getattr(e, "Identification", None) and len(e.Identification) > 0:
                ident = e.Identification[0]
                root = getattr(ident, 'Root', 'N/A')
                extension = getattr(ident, 'Extension', 'N/A')
                ens_info_list.append(
                    f"Root:{root} | Extension:{extension} | ContextAssociation:{ctx_assoc}"
                )
            else:
                ens_info_list.append(f"(no Identification) | ContextAssociation:{ctx_assoc}")
        
        print(f"Given names = {given_names}, Heights = {heights}, Weights = {weights}, Rooms = {rooms}, Danger codes = {danger_codes}, Ensembles = {ens_info_list}")

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
    import pathlib
    import logging
    from sdc11073.loghelper import basic_logging_setup
    from sdc11073 import commlog
    basic_logging_setup(level=logging.INFO)

    # ── SSL disabled (plain HTTP) ─────────────────────────────────────────
    # Consumer launched with --no_tls flag → both sides use plain HTTP.
    ssl_container = None

    # UUID must match what sdcX ReferenceConsumer expects in DEV-24
    my_uuid = uuid.UUID('ba8ad49f-e25b-43ad-870b-c1bdba91d431')

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
                             specific_components=components,
                             ssl_context_container=ssl_container)  # HTTPS for sdcX

    # Disable HTTP compression — sdcX C++ Consumer may crash on compressed responses
    provider.set_used_compression()  # empty = no compression

    provider.simulate_self_checkout()

    # Discovery start
    discovery.start()

    # Starting all Services of provider
    provider.start_all()

    # Set location to match CompleteConsumer's scope filter: DWHL/F05/TKl
    #loc = SdcLocation(fac='DWHL', poc='F05', bed='TKl')
    #provider.set_location(loc)

    # Publishing the provider into Network to make it visible for consumers
    provider.publish()

    try:
        asyncio.run(main(provider))
    except KeyboardInterrupt:
        print("Stopping provider...")
        provider.stop_all()
        discovery.stop()
        print("Provider stopped.")