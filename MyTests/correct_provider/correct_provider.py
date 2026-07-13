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
# DSP SignalProcessor Test Scenarios
# =============================================================================
#
# The consumer's SignalProcessor validates alarms by computing dx/dt on the
# metric buffer (LOINC 8310-5, temperature).
# Limit for 8310-5: MAX_ROC = 0.5 °C/s
#
# Three scenarios repeat in a 70-second cycle:
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │ Phase 1 — WARMUP          (t=  0..19, 20s)                             │
# │   temp = 36.0 °C (stable).  No alarm.                                  │
# │   Purpose: fill the _physiological_graph deque with stable history.    │
# │   Consumer: PhysGraph updates visible, no alarm logs.                  │
# ├─────────────────────────────────────────────────────────────────────────┤
# │ Phase 2 — ARTIFACT TEST   (t= 20..34, 15s)                             │
# │   t=20: temperature 36→200 °C in ONE step + alarm ON.                  │
# │   RoC = |200-36| / 1s = 164 °C/s  >> limit=0.5 °C/s                   │
# │   Expected consumer: [DSP FILTER] Artifact detected  →  alarm LOST.    │
# │   t=28: temp back to 36 °C + alarm OFF.                                │
# ├─────────────────────────────────────────────────────────────────────────┤
# │ Phase 3 — REAL ALARM TEST (t= 35..69, 35s)                             │
# │   t=35..54: temp rises 36 + (t-35)*0.1 °C/s (0.1 °C per second).      │
# │   t=50: alarm ON.  Buffer at this moment:                               │
# │     [..., (37.4, t49), (37.5, t50)]                                    │
# │     RoC = 0.1 / 1s = 0.1 °C/s  << limit=0.5 °C/s                      │
# │   Expected consumer: 🔴 ON  (alarm escalated).                          │
# │   t=65: temp back to 36 °C + alarm OFF.                                │
# │   Expected consumer: 🟢 OFF.                                            │
# └─────────────────────────────────────────────────────────────────────────┘
#
# Bonus edge cases embedded in the cycle:
#   • Phase 2, t=20: metric update and alarm fire in same second.
#     Tests that SDC Pub/Sub ordering (metric before alert) is preserved.
#   • Phase 3, t=35: alarm fires while buffer has only recovery readings
#     (36 °C from phase 2 end). RoC = 0 → passes.
#   • No humidity alarm is fired, so humidity stays silent the whole time.
# =============================================================================

CYCLE_SEC       = 70   # total cycle duration (seconds)
WARMUP_END      = 20   # Phase 1: 0..19
ARTIFACT_ON     = 20   # Phase 2 start: jump temp + fire alarm
ARTIFACT_OFF    = 28   # Phase 2: clear alarm + restore temp
RISE_START      = 35   # Phase 3: begin gradual temperature rise
REAL_ALARM_ON   = 50   # Phase 3: fire alarm (temp ~37.5 °C, rising steadily)
REAL_ALARM_OFF  = 65   # Phase 3: clear alarm + restore temp


# ---------------------------------------------------------------------------
# Primitive metric / alert helpers
# ---------------------------------------------------------------------------

def _set_temperature(provider, value: Decimal) -> None:
    """Write a single temperature metric sample (generates EpisodicMetricReport)."""
    with provider.mdib.metric_state_transaction() as tr:
        tr.get_state('temperature').MetricValue.Value = value


def _alarm_on(provider) -> None:
    """Fire the temperature alarm (generates EpisodicAlertReport)."""
    with provider.mdib.alert_state_transaction() as tr:
        tr.get_state('al_condition_temperature').Presence = True
        tr.get_state('al_signal_temperature').Presence = AlertSignalPresence.ON


def _alarm_off(provider) -> None:
    """Clear the temperature alarm (generates EpisodicAlertReport)."""
    with provider.mdib.alert_state_transaction() as tr:
        tr.get_state('al_condition_temperature').Presence = False
        tr.get_state('al_signal_temperature').Presence = AlertSignalPresence.OFF


# ---------------------------------------------------------------------------
# Main test loop
# ---------------------------------------------------------------------------

async def main(provider):  # noqa: C901
    elapsed      = 0
    alarm_active = False

    _SEP = '─' * 70

    print(f'\n{_SEP}')
    print('  DSP SignalProcessor integration test  —  70-second cycle')
    print(f'{_SEP}')
    print('  Phase 1 WARMUP     t= 0-19  :  36°C stable, buffer fills')
    print('  Phase 2 ARTIFACT   t=20-34  :  36→200°C jump + alarm ON')
    print('                                 Expected: [DSP FILTER] SUPPRESSED')
    print('  Phase 3 REAL ALARM t=35-69  :  slow rise 0.1°C/s, alarm at t=50')
    print('                                 Expected: 🔴 ON escalated')
    print(f'{_SEP}\n')

    while True:
        t = elapsed % CYCLE_SEC

        # ==================================================================
        # STEP 1: Compute the temperature value for this tick
        # ==================================================================
        if t < WARMUP_END:
            # Phase 1: stable baseline — fills the sliding-window buffer
            temp = Decimal('36.0')

        elif t < ARTIFACT_OFF:
            # Phase 2: artifact window
            if t == ARTIFACT_ON:
                # Instantaneous jump: impossible RoC for any sensor
                temp = Decimal('200.0')
            else:
                temp = Decimal('36.0')

        elif t < RISE_START:
            # Recovery between phases
            temp = Decimal('36.0')

        elif t < REAL_ALARM_OFF:
            # Phase 3: physiologically plausible slow rise (0.1 °C/s)
            rise = Decimal(str((t - RISE_START) * 0.1))
            temp = Decimal('36.0') + rise

        else:
            # Post-alarm cooldown: restore baseline
            temp = Decimal('36.0')

        # ==================================================================
        # STEP 2: Send metric update BEFORE any alarm state change.
        # SDC guarantees metric report arrives before alert report because
        # transactions are sequential on the same provider connection.
        # The consumer's _physiological_graph is updated FIRST so the
        # SignalProcessor has the latest reading in the buffer.
        # ==================================================================
        _set_temperature(provider, temp)

        # ==================================================================
        # STEP 3: Alarm state transitions (only on boundary ticks)
        # ==================================================================
        if t == ARTIFACT_ON and not alarm_active:
            alarm_active = True
            _alarm_on(provider)
            print(f'\n{_SEP}')
            print(f'[t={elapsed:>3}s] 🧪 ARTIFACT TEST')
            print(f'         Temp jumped: 36.0 → 200.0 °C  (RoC ≈ 164 °C/s >> limit 0.5)')
            print(f'         Alarm ON fired.')
            print(f'         ✗ Consumer MUST log: [DSP FILTER] Artifact detected')
            print(f'         ✗ Consumer must NOT log: 🔴 ON')
            print(f'{_SEP}\n')

        elif t == ARTIFACT_OFF and alarm_active:
            alarm_active = False
            _alarm_off(provider)
            print(f'[t={elapsed:>3}s] Phase 2 end — temp restored to 36°C, alarm OFF.')

        elif t == REAL_ALARM_ON and not alarm_active:
            alarm_active = True
            _alarm_on(provider)
            roc_actual = 0.1  # 0.1 °C/s
            print(f'\n{_SEP}')
            print(f'[t={elapsed:>3}s] 🧪 REAL ALARM TEST')
            print(f'         Temp now: {float(temp):.2f} °C  (risen 0.1°C/s for {t - RISE_START}s)')
            print(f'         Actual RoC ≈ {roc_actual:.2f} °C/s  <<  limit 0.5 °C/s')
            print(f'         Alarm ON fired.')
            print(f'         ✓ Consumer MUST log: 🔴 ON  (real clinical event)')
            print(f'         ✓ Consumer must NOT suppress this alarm')
            print(f'{_SEP}\n')

        elif t == REAL_ALARM_OFF and alarm_active:
            alarm_active = False
            _alarm_off(provider)
            print(f'[t={elapsed:>3}s] Phase 3 end — temp restored, alarm OFF.')
            print(f'         ✓ Consumer MUST log: 🟢 OFF\n')

        # ==================================================================
        # STEP 4: Per-second status line
        # ==================================================================
        phase = (
            'WARMUP   ' if t < WARMUP_END else
            'ARTIFACT ' if t < ARTIFACT_OFF else
            'RECOVERY ' if t < RISE_START else
            'REAL ALRM' if t < REAL_ALARM_OFF else
            'COOLDOWN '
        )
        alarm_str = '🔴 ON ' if alarm_active else '🟢 OFF'
        print(
            f'[t={elapsed:>3}s | {phase} | alarm={alarm_str}]  temp={float(temp):>6.2f}°C',
            flush=True,
        )

        elapsed += 1
        await asyncio.sleep(1)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

NETWORK_ADAPTER = 'Wi-Fi'
MDIB_FILE       = 'correct_mdib.xml'

if __name__ == '__main__':
    my_uuid   = uuid.UUID('ba8ad49f-e25b-43ad-870b-c1bdba91d431')
    mdib_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), MDIB_FILE)

    mdib       = ProviderMdib.from_mdib_file(mdib_path)
    model      = ThisModelType(model_name='MockModel', manufacturer='MockManufacturer',
                               manufacturer_url='http://mockurl.com')
    device     = ThisDeviceType(friendly_name='MockProvider', serial_number='123456')
    components = SdcProviderComponents(role_provider_class=ExtendedProduct)
    discovery  = WSDiscoverySingleAdapter(NETWORK_ADAPTER)

    provider = MySdcProvider(
        ws_discovery=discovery,
        epr=my_uuid,
        this_model=model,
        this_device=device,
        device_mdib_container=mdib,
        specific_components=components,
        ssl_context_container=None,
    )
    provider.set_used_compression()

    discovery.start()
    provider.start_all()
    provider.publish()

    try:
        asyncio.run(main(provider))
    except KeyboardInterrupt:
        provider.stop_all()
        discovery.stop()
        print('\nStopped.')
