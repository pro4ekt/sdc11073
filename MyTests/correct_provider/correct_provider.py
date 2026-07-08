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


def activate_alarm(provider):
    """Set temperature alarm ON (value far above threshold)."""
    with provider.mdib.metric_state_transaction() as tr:
        tr.get_state('temperature').MetricValue.Value = Decimal('50')  # threshold: 45

    with provider.mdib.alert_state_transaction() as tr:
        tr.get_state('al_condition_temperature').Presence = True
        tr.get_state('al_signal_temperature').Presence = AlertSignalPresence.ON

    print('[Provider] 🔴 Alarm ON  — temperature=50 (threshold: 45)', flush=True)


def deactivate_alarm(provider):
    """Set temperature alarm OFF (value within normal range)."""
    with provider.mdib.metric_state_transaction() as tr:
        tr.get_state('temperature').MetricValue.Value = Decimal('36')  # normal

    with provider.mdib.alert_state_transaction() as tr:
        tr.get_state('al_condition_temperature').Presence = False
        tr.get_state('al_signal_temperature').Presence = AlertSignalPresence.OFF

    print('[Provider] 🟢 Alarm OFF — temperature=36 (normal)',       flush=True)


async def main(provider):
    alarm_on = False
    TOGGLE_INTERVAL = 10  # seconds between each ON/OFF flip
    elapsed = 0

    while True:
        # Toggle alarm every TOGGLE_INTERVAL seconds
        if elapsed % TOGGLE_INTERVAL == 0:
            alarm_on = not alarm_on
            if alarm_on:
                activate_alarm(provider)
            else:
                deactivate_alarm(provider)

        n = sum(
            len(getattr(mgr, '_subscriptions', None).objects)
            for mgr in getattr(provider, '_subscriptions_managers', {}).values()
            if getattr(mgr, '_subscriptions', None) is not None
        )

        from sdc11073.xml_types import pm_qnames as pm
        patients = provider.mdib.context_states.NODETYPE.get(pm.PatientContextState, [])
        given_names = [p.CoreData.Givenname for p in patients if p.CoreData and p.CoreData.Givenname]

        locations = provider.mdib.context_states.NODETYPE.get(pm.LocationContextState, [])
        rooms = [l.LocationDetail.Room for l in locations if getattr(l, "LocationDetail", None)]

        ensembles = provider.mdib.context_states.NODETYPE.get(pm.EnsembleContextState, [])
        ens_info_list = []
        for e in ensembles:
            if getattr(e, "Identification", None) and len(e.Identification) > 0:
                ident = e.Identification[0]
                ident_name_str = "None"
                if getattr(ident, "IdentifierName", None):
                    ident_name = ident.IdentifierName[0] if isinstance(ident.IdentifierName,
                                                                       list) else ident.IdentifierName
                    ident_name_str = str(getattr(ident_name, "text", ident_name))
                ens_info_list.append(f"Root:{ident.Root} | Ext:{ident.Extension} | Name:{ident_name_str}")
            else:
                ens_info_list.append(None)
        workflows = provider.mdib.context_states.NODETYPE.get(pm.WorkflowContextState, [])
        print(
            f"[Context] Patients={given_names} | Rooms={rooms}  | "
            f"Ensembles={ens_info_list}%")
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
    print('Provider started. Alarm is permanently ON.')

    try:
        asyncio.run(main(provider))
    except KeyboardInterrupt:
        provider.stop_all()
        discovery.stop()
        print('Stopped.')
