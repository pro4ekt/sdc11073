from __future__ import annotations

import uuid
import asyncio
import os
from decimal import Decimal

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
from sdc11073.xml_types import pm_qnames as pm

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

def update_device_health(provider):
    """
    Вычисляет и обновляет device_health на основе ActivationState обоих VMD.
    Логика:
      - vmd_optics=ON  + vmd_motors=ON  → 100%
      - vmd_optics=STANDBY              → 50%
      - vmd_optics=FAILURE              → 0%
    """
    optics_state = provider.mdib.entities.by_handle("vmd_optics").state.ActivationState
    motors_state = provider.mdib.entities.by_handle("vmd_motors").state.ActivationState

    if optics_state == ComponentActivation.FAILURE:
        health = Decimal(0)
    elif optics_state == ComponentActivation.ON and motors_state == ComponentActivation.ON:
        health = Decimal(100)
    else:
        # STANDBY или любое другое состояние
        health = Decimal(50)

    with provider.mdib.metric_state_transaction() as tr:
        h_state = tr.get_state("device_health")
        if h_state.MetricValue is None:
            h_state.mk_metric_value()
        h_state.MetricValue.Value = health

    return health


def print_contexts(provider):
    """Печатает все контексты: пациент, локация, ансамбль, workflow."""
    patients = provider.mdib.context_states.NODETYPE.get(pm.PatientContextState, [])
    given_names = [p.CoreData.Givenname for p in patients if p.CoreData and p.CoreData.Givenname]

    locations = provider.mdib.context_states.NODETYPE.get(pm.LocationContextState, [])
    rooms = [l.LocationDetail.Room for l in locations if getattr(l, "LocationDetail", None)]

    workflows = provider.mdib.context_states.NODETYPE.get(pm.WorkflowContextState, [])
    danger_codes = []
    for w in workflows:
        if getattr(w, "WorkflowDetail", None):
            if hasattr(w.WorkflowDetail, 'DangerCode') and w.WorkflowDetail.DangerCode:
                danger_codes.append(w.WorkflowDetail.DangerCode[0].Code)
            else:
                danger_codes.append(None)

    ensembles = provider.mdib.context_states.NODETYPE.get(pm.EnsembleContextState, [])
    ens_info_list = []
    for e in ensembles:
        if getattr(e, "Identification", None) and len(e.Identification) > 0:
            ident = e.Identification[0]
            ident_name_str = "None"
            if getattr(ident, "IdentifierName", None):
                ident_name = ident.IdentifierName[0] if isinstance(ident.IdentifierName, list) else ident.IdentifierName
                ident_name_str = str(getattr(ident_name, "text", ident_name))
            ens_info_list.append(f"Root:{ident.Root} | Ext:{ident.Extension} | Name:{ident_name_str}")
        else:
            ens_info_list.append(None)

    health_state = provider.mdib.entities.by_handle("device_health").state.MetricValue
    health_val = health_state.Value if health_state else "N/A"

    print(f"[Context] Patients={given_names} | Rooms={rooms} | DangerCodes={danger_codes} | "
          f"Ensembles={ens_info_list} | DeviceHealth={health_val}%")


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

        # Обновляем device_health на основе текущих VMD ActivationState
        health = update_device_health(provider)

        # Выведем текущие состояния, чтобы удостовериться в синхронизации
        vmd_act = provider.mdib.entities.by_handle("vmd_optics").state.ActivationState
        vmd_motors_act = provider.mdib.entities.by_handle("vmd_motors").state.ActivationState
        sm_val = provider.mdib.entities.by_handle("metric_statemachine").state.MetricValue.Value
        mot_val = provider.mdib.entities.by_handle("motor_movement").state.MetricValue.Value

        print(f"VMD Optics State     : {vmd_act}")
        print(f"VMD Motors State     : {vmd_motors_act}")
        print(f"Workflow StateMachine: {sm_val}")
        print(f"Motor Movement State : {mot_val}")
        print(f"Device Health        : {health}%")

        # Печатаем все контексты
        print_contexts(provider)

        i += 1
        await asyncio.sleep(5)

# Add configuration constants
NETWORK_ADAPTER = "Ethernet 2"
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