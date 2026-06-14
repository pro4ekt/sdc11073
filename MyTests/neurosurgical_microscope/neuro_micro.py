from __future__ import annotations

import uuid
import asyncio
import os

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
from sdc11073.xml_types.pm_types import RelatedMeasurement
from sdc11073.xml_types.pm_types import ComponentActivation

# === НАШ МАНКИ-ПАТЧ ИСПРАВЛЕНИЯ БАГА БИБЛИОТЕКИ ===
from sdc11073.xml_types.pm_types import RelatedMeasurement, Measurement

@classmethod
def _related_measurement_from_node(cls, node):
    # Передаем заглушку Measurement, чтобы обойти ошибку __init__ "missing 1 required argument 'value'"
    obj = cls(Measurement(None, None))
    obj.update_from_node(node)
    return obj

RelatedMeasurement.from_node = _related_measurement_from_node
# === КОНЕЦ ПАТЧА ===

def update_statemachine_and_vmd(provider, new_state: str):
    """
    Обновляет метрику statemachine и синхронно переключает VMD ActivationState.
    Значения statemachine динамически проверяются по AllowedValue из MDIB.
    """
    # Динамически достаем разрешенные значения состояния
    desc = provider.mdib.entities.by_handle("metric_statemachine").descriptor
    allowed_values = [av.Value for av in desc.AllowedValue]
    
    if new_state not in allowed_values:
        print(f"Warning: State '{new_state}' is not in AllowedValues {allowed_values}. Ignoring.")
        return

    # Логика маппинга логического состояния SM на стандартизированный VMD ActivationState:
    if new_state == "Running":
        target_activation = ComponentActivation.ON
    elif new_state == "Aborted":
        target_activation = ComponentActivation.FAILURE
    else:
        # Для Idle, Paused, Complete уводим оборудование в стендбай
        target_activation = ComponentActivation.STANDBY

    # Транзакция 1: обновляем метрики (StateMachine + motor_movement)
    with provider.mdib.metric_state_transaction() as tr:
        metric_state = tr.get_state("metric_statemachine")
        if metric_state.MetricValue is None:
            metric_state.mk_metric_value()
        metric_state.MetricValue.Value = new_state

        motor_state = tr.get_state("motor_movement")
        if motor_state.MetricValue is None:
            motor_state.mk_metric_value()
        motor_state.MetricValue.Value = "Moving" if new_state == "Running" else "Stopped"

    # Транзакция 2: обновляем компонентные состояния (VMD ActivationState)
    with provider.mdib.component_state_transaction() as tr:
        vmd_state = tr.get_state("vmd_optics")
        vmd_state.ActivationState = target_activation

        vmd_motors_state = tr.get_state("vmd_motors")
        vmd_motors_state.ActivationState = ComponentActivation.ON if new_state == "Running" else ComponentActivation.STANDBY


async def main(provider):
    states_to_simulate = ["Idle", "Running", "Paused", "Running", "Complete", "Aborted"]
    i = 0
    while True:
        current_state = states_to_simulate[i % len(states_to_simulate)]
        print(f"\n--- Simulating command: Transition to {current_state} ---")
        update_statemachine_and_vmd(provider, current_state)
        
        # Выведем текущие состояния, чтобы удостовериться в синхронизации
        vmd_act = provider.mdib.entities.by_handle("vmd_optics").state.ActivationState
        vmd_motors_act = provider.mdib.entities.by_handle("vmd_motors").state.ActivationState
        sm_val = provider.mdib.entities.by_handle("metric_statemachine").state.MetricValue.Value
        mot_val = provider.mdib.entities.by_handle("motor_movement").state.MetricValue.Value
        
        print(f"VMD Optics State   : {vmd_act}")
        print(f"VMD Motors State   : {vmd_motors_act}")
        print(f"Workflow StateMachine: {sm_val}")
        print(f"Motor Movement State : {mot_val}")
        
        i += 1
        await asyncio.sleep(5)

# Add configuration constants
NETWORK_ADAPTER = "Wi-Fi"
MDIB_FILE = "neuro_micro_mdib.xml"

if __name__ == '__main__':
    # basic_logging_setup()

    # UUID objects (universally unique identifiers) according to RFC 4122
    base_uuid = uuid.UUID('{cc013678-79f6-403c-998f-3cc0cc050239}')
    my_uuid = uuid.uuid5(base_uuid, "neuro_microscope_provider")

    mdib = ProviderMdib.from_mdib_file(MDIB_FILE)

    # All necessary components for the provider
    model = ThisModelType(model_name='NeuroModel',
                          manufacturer='NeuroManufacturer',
                          manufacturer_url='http://n.com')

    # Dependency injection: This class defines which component implementations the sdc provider will use
    components = SdcProviderComponents(role_provider_class=ExtendedProduct)

    # ThisDeviceType object with friendly name and serial number
    device = ThisDeviceType(friendly_name='neurosurgical microscope', serial_number='123456')

    # UDP based discovery on single network adapter
    print(f"Starting discovery on {NETWORK_ADAPTER}")
    discovery = WSDiscoverySingleAdapter(NETWORK_ADAPTER)

    # Assambling everything which was created above to implement SDC Provider object
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

    try:
        a = provider
        asyncio.run(main(provider))
    except KeyboardInterrupt:
        print("Stopping provider...")
        provider.stop_all()
        discovery.stop()
        print("Provider stopped.")