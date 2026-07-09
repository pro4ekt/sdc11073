from __future__ import annotations

import uuid
import asyncio
import os
from decimal import Decimal

from sdc11073.mdib import ProviderMdib
from sdc11073.provider import SdcProvider
from sdc11073.provider.components import SdcProviderComponents
from sdc11073.roles.product import ExtendedProduct
from sdc11073.wsdiscovery import WSDiscoverySingleAdapter
from sdc11073.xml_types.dpws_types import ThisDeviceType, ThisModelType
from sdc11073.xml_types.pm_types import AlertSignalPresence, Measurement, RelatedMeasurement

from sdc11073.provider.subscriptionmgr_base import SubscriptionBase
SubscriptionBase.MAX_NOTIFY_ERRORS = 999

@classmethod
def _related_measurement_from_node(cls, node):
    obj = cls(Measurement(None, None))
    obj.update_from_node(node)
    return obj
RelatedMeasurement.from_node = _related_measurement_from_node


class MySdcProvider(SdcProvider):
    def publish(self):
        scopes = self._components.scopes_factory(self._mdib)
        for code in ('sdc.cdc.type:///130535', 'sdc.cdc.type:///130536', 'sdc.cdc.type:///130736'):
            if code not in scopes.text:
                scopes.text.append(code)
        self._wsdiscovery.publish_service(
            self.epr_urn,
            list(self._mdib.sdc_definitions.MedicalDeviceTypesFilter),
            scopes,
            self.get_xaddrs(),
        )


# =============================================================================
# RuleEngine test scenarios
# =============================================================================
# Patient in MDIB has DangerCode = SNOMED:40275004 (Contact dermatitis).
# rules.json clinical_focus maps 40275004 →
#     critical_concepts: ["MDC_ALERT_TEMP_HIGH", "8310-5"]
#
# Expected consumer behavior:
#   Alarm ON  → [PRIORITY CLINICAL FOCUS] [ALARM] 🔴 ON   (MDC_ALERT_TEMP_HIGH is critical)
#   Alarm OFF → [ALARM] 🟢 OFF                            (still logged, no priority tag)
#   Topology  → [TOPOLOGY AUDIT] All required sensors present  (8310-5 is in PhysGraph)
# =============================================================================

def alarm_on(provider):
    """Fire the temperature alarm with elevated value (genuine reading > threshold)."""
    with provider.mdib.metric_state_transaction() as tr:
        tr.get_state('temperature').MetricValue.Value = Decimal('50')  # > upper bound 45

    with provider.mdib.alert_state_transaction() as tr:
        tr.get_state('al_condition_temperature').Presence = True
        tr.get_state('al_signal_temperature').Presence = AlertSignalPresence.ON

    print(
        '[Provider] 🔴 ALARM ON  | temp=50°C\n'
        '           Consumer should log: [PRIORITY CLINICAL FOCUS] [ALARM] 🔴 ON',
        flush=True,
    )


def alarm_off(provider):
    """Clear the temperature alarm, return metric to normal range."""
    with provider.mdib.metric_state_transaction() as tr:
        tr.get_state('temperature').MetricValue.Value = Decimal('36')  # normal

    with provider.mdib.alert_state_transaction() as tr:
        tr.get_state('al_condition_temperature').Presence = False
        tr.get_state('al_signal_temperature').Presence = AlertSignalPresence.OFF

    print(
        '[Provider] 🟢 ALARM OFF | temp=36°C\n'
        '           Consumer should log: [ALARM] 🟢 OFF (no priority tag)',
        flush=True,
    )


async def main(provider):
    TOGGLE_INTERVAL = 10   # seconds between ON ↔ OFF
    elapsed  = 0
    alarm_active = False

    while True:
        if elapsed % TOGGLE_INTERVAL == 0:
            alarm_active = not alarm_active
            if alarm_active:
                alarm_on(provider)
            else:
                alarm_off(provider)

        # Status line
        from sdc11073.xml_types import pm_qnames as pm
        patients  = provider.mdib.context_states.NODETYPE.get(pm.PatientContextState, [])
        locations = provider.mdib.context_states.NODETYPE.get(pm.LocationContextState, [])
        ensembles = provider.mdib.context_states.NODETYPE.get(pm.EnsembleContextState, [])

        given_names = [p.CoreData.Givenname for p in patients if p.CoreData and p.CoreData.Givenname]
        rooms       = [l.LocationDetail.Room for l in locations if getattr(l, 'LocationDetail', None)]
        ens_list    = []
        for e in ensembles:
            if getattr(e, 'Identification', None) and e.Identification:
                ens_list.append(f'Ext:{e.Identification[0].Extension}')
            else:
                ens_list.append('(none)')

        # -- DangerCodes from WorkflowContextState --------------------------------
        workflows = provider.mdib.context_states.NODETYPE.get(pm.WorkflowContextState, [])
        danger_codes = []
        for wf in workflows:
            wd = getattr(wf, 'WorkflowDetail', None)
            if not wd:
                continue
            raw = getattr(wd, 'DangerCode', None)
            if raw is None:
                continue
            # DangerCode can be a single object or a list
            items = raw if isinstance(raw, list) else [raw]
            for dc in items:
                code   = getattr(dc, 'Code', None) or ''
                system = getattr(dc, 'CodingSystem', None) or ''
                code_str = f'{system}:{code}' if system else code
                if code.strip():
                    danger_codes.append(code_str)

        state_str = '🔴 ON' if alarm_active else '🟢 OFF'
        print(
            f'[t={elapsed:>4}s | alarm={state_str}] '
            f'Patients={given_names} | Rooms={rooms} | Ensembles={ens_list}\n'
            f'           DangerCodes in MDIB ({len(danger_codes)}): {danger_codes}',
            flush=True,
        )

        elapsed += 1
        await asyncio.sleep(1)


NETWORK_ADAPTER = 'Wi-Fi'
MDIB_FILE = 'correct_mdib.xml'

if __name__ == '__main__':
    my_uuid = uuid.UUID('ba8ad49f-e25b-43ad-870b-c1bdba91d431')
    mdib_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), MDIB_FILE)

    mdib       = ProviderMdib.from_mdib_file(mdib_path)
    model      = ThisModelType(model_name='MockModel', manufacturer='MockManufacturer',
                               manufacturer_url='http://mockurl.com')
    device     = ThisDeviceType(friendly_name='MockProvider', serial_number='123456')
    components = SdcProviderComponents(role_provider_class=ExtendedProduct)
    discovery  = WSDiscoverySingleAdapter(NETWORK_ADAPTER)

    provider = MySdcProvider(ws_discovery=discovery, epr=my_uuid,
                             this_model=model, this_device=device,
                             device_mdib_container=mdib,
                             specific_components=components,
                             ssl_context_container=None)
    provider.set_used_compression()

    discovery.start()
    provider.start_all()
    provider.publish()
    print('Provider started — RuleEngine Priority + Topology Audit test.')
    print('Patient DangerCode: SNOMED:40275004 (Contact dermatitis)')
    print('Expected: [PRIORITY CLINICAL FOCUS] prefix on temperature alarms.')
    print()

    try:
        asyncio.run(main(provider))
    except KeyboardInterrupt:
        provider.stop_all()
        discovery.stop()
        print('Stopped.')
