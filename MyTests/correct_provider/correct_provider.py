"""
correct_provider.py — IHE-PCD ACM AlarmCoordinator Test Harness
===============================================================
Emulates an ICU bedside monitor (Acme Medical / PatientMonitor Pro 3000).

DPWS metadata
  Manufacturer : "Acme Medical"
  ModelName    : "PatientMonitor Pro 3000"
  These values are used by the consumer's ClinicalRiskFilter to look up
  _DEVICE_PROFILES and compute calibrated LR+ ratios.

Simulation — 45-second cycle with 4 phases
───────────────────────────────────────────
┌──────────────────────────────────────────────────────────────────────────────┐
│ Phase 0 — BASELINE         (t=  0.. 4,  5s)                                 │
│   HR=80 bpm, SpO2=98%.  All alarms OFF.                                     │
│   Purpose: fill the _physiological_graph deque with stable history.         │
├──────────────────────────────────────────────────────────────────────────────┤
│ Phase 1 — HARDWARE ARTIFACT TEST  (t=  5.. 9,  5s)                          │
│   t=5: HR jumps 80→150 bpm in ONE step.                                     │
│        RoC = |150-80| / 1s = 70 bpm/s  >>  Stage 1 limit = 10 bpm/s        │
│        al_hr_hi (Priority=Hi) fired.                                        │
│   Expected consumer: Stage 1 SUPPRESSES alarm.                              │
│   t=10: HR restored to 80, alarm OFF.                                       │
├──────────────────────────────────────────────────────────────────────────────┤
│ Phase 2 — WEAK ALARM / LOW RISK TEST  (t= 15..24, 10s)                      │
│   t=15..19: SpO2 falls 2%/s: 98→96→94→92→90.                               │
│   t=20: SpO2=88%, RoC=2 %/s  <  Stage 1 limit=3 %/s.  Stage 1 passes.     │
│          al_spo2_lo (Priority=Me) fired.                                    │
│          Stage 2: Posterior_P≈0.804, P_total=6.0  →  risk≈4.83 < 5.0       │
│   Expected consumer: Stage 2 SUPPRESSES alarm.                              │
│   t=25: SpO2→98%, alarm OFF.                                                │
├──────────────────────────────────────────────────────────────────────────────┤
│ Phase 3 — SENSOR FUSION CRISIS  (t= 30..39, 10s)                            │
│   t=30..34: HR rises +10/s (80→90→100→110→120), SpO2 falls (98→…→88).     │
│   t=35: HR=130 bpm, SpO2=85%.  Both alarms fired simultaneously.            │
│          al_hr_hi  (Hi):  RoC=10 NOT>10  →  Stage 1 passes.                │
│                            risk = 0.917 × 10.0 = 9.17  ≥ 5.0  → ESCALATE  │
│          al_spo2_lo(Me):  RoC=3  NOT>3   →  Stage 1 passes.                │
│                            risk = 0.804 ×  6.0 = 4.83  < 5.0  → SUPPRESS  │
│   t=40: all vitals restored, all alarms OFF.                                │
└──────────────────────────────────────────────────────────────────────────────┘

Risk maths assume ClinicalRiskFilter._DEVICE_PROFILES contains calibrated
entries for "Acme Medical" / "PatientMonitor Pro 3000":
  8867-4  sensitivity=0.88, FAR=0.08  →  LR+ = 11.0
  59408-5 sensitivity=0.74, FAR=0.18  →  LR+ ≈ 4.11
"""

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


# ── Timing constants (seconds within each CYCLE_SEC-second loop) ──────────────

CYCLE_SEC         = 45   # total cycle length

ARTIFACT_START    = 5    # Phase 1: HR jumps to 150 + al_hr_hi ON
ARTIFACT_END      = 10   # Phase 1: HR returns to 80 + al_hr_hi OFF

SPO2_RISE_START   = 15   # Phase 2: SpO2 begins to fall (2%/s)
SPO2_ALARM_ON     = 20   # Phase 2: SpO2=88%, fire al_spo2_lo
SPO2_RESTORE      = 25   # Phase 2: SpO2→98%, al_spo2_lo OFF

FUSION_RISE_START = 30   # Phase 3: gradual change starts (HR↑, SpO2↓)
FUSION_ALARM_ON   = 35   # Phase 3: crisis values + both alarms ON
FUSION_RESTORE    = 40   # Phase 3: restore all, both alarms OFF


# ── Vitals computation ────────────────────────────────────────────────────────

def _compute_vitals(t: int) -> tuple[int, int]:
    """
    Return (hr_bpm, spo2_pct) for cycle position t.

    Phase 1 (5..9)  — HR spike artifact:
      HR=150, SpO2=98

    Phase 2 (15..24) — SpO2 slow drop:
      t=15..19: SpO2 = 98 − (t−15)×2   →  98, 96, 94, 92, 90
      t=20..24: SpO2 = 88 (alarm window)

    Phase 3 (30..39) — Fusion crisis:
      t=30..34: HR = 80 + (t−30)×10    →  80, 90, 100, 110, 120
                SpO2 = 98 − int((t−30)×2.6)  →  98, 96, 93, 91, 88
      t=35..39: HR=130, SpO2=85 (alarm window)
    """
    hr, spo2 = 80, 98

    if ARTIFACT_START <= t < ARTIFACT_END:
        hr = 150

    elif SPO2_RISE_START <= t < SPO2_RESTORE:
        if t < SPO2_ALARM_ON:
            spo2 = 98 - (t - SPO2_RISE_START) * 2
        else:
            spo2 = 88

    elif FUSION_RISE_START <= t < FUSION_RESTORE:
        steps = t - FUSION_RISE_START
        if t < FUSION_ALARM_ON:
            hr   = 80 + steps * 10
            spo2 = 98 - int(steps * 2.6)
        else:
            hr   = 130
            spo2 = 85

    return hr, spo2


def _phase_name(t: int) -> str:
    if t < ARTIFACT_START:                        return 'BASELINE      '
    if t < ARTIFACT_END:                          return 'ARTIFACT      '
    if t < SPO2_RISE_START:                       return 'RECOVERY-1    '
    if t < SPO2_RESTORE:                          return 'WEAK-ALARM    '
    if t < FUSION_RISE_START:                     return 'RECOVERY-2    '
    if t < FUSION_RESTORE:                        return 'FUSION-CRISIS '
    return                                               'COOLDOWN      '


# ── MDIB helpers ──────────────────────────────────────────────────────────────

def _set_vitals(provider, hr_val: Decimal, spo2_val: Decimal) -> None:
    """Write HR + SpO2 metrics in a single transaction (one EpisodicMetricReport)."""
    with provider.mdib.metric_state_transaction() as tr:
        tr.get_state('hr_metric').MetricValue.Value   = hr_val
        tr.get_state('spo2_metric').MetricValue.Value = spo2_val


def _hr_alarm_on(provider) -> None:
    with provider.mdib.alert_state_transaction() as tr:
        tr.get_state('al_hr_hi').Presence        = True
        tr.get_state('al_signal_hr_hi').Presence = AlertSignalPresence.ON


def _hr_alarm_off(provider) -> None:
    with provider.mdib.alert_state_transaction() as tr:
        tr.get_state('al_hr_hi').Presence        = False
        tr.get_state('al_signal_hr_hi').Presence = AlertSignalPresence.OFF


def _spo2_alarm_on(provider) -> None:
    with provider.mdib.alert_state_transaction() as tr:
        tr.get_state('al_spo2_lo').Presence        = True
        tr.get_state('al_signal_spo2_lo').Presence = AlertSignalPresence.ON


def _spo2_alarm_off(provider) -> None:
    with provider.mdib.alert_state_transaction() as tr:
        tr.get_state('al_spo2_lo').Presence        = False
        tr.get_state('al_signal_spo2_lo').Presence = AlertSignalPresence.OFF


# ── Simulation loop ───────────────────────────────────────────────────────────

# ── Simulation loop (UI ACK TEST) ────────────────────────────────────────────

async def main(provider) -> None:
    elapsed = 0
    SEP = '─' * 74

    print(f'\n{SEP}')
    print('  UI ACK TEST HARNESS — Permanent Alarm Mode')
    print('  Device: Acme Medical / PatientMonitor Pro 3000')
    print(f'{SEP}')
    print('  1. Setting HR to 150 bpm.')
    print('  2. Firing al_hr_hi (Priority=Hi) PERMANENTLY.')
    print('  3. Waiting for Consumer to send ACK operation via UI...')
    print(f'{SEP}\n')

    # Включаем тревогу один раз при старте и больше не трогаем
    _set_vitals(provider, Decimal('150'), Decimal('98'))
    _hr_alarm_on(provider)

    while True:
        # Читаем текущее состояние Alert Signal прямо из MDIB Провайдера.
        # Если UI работает правильно, после нажатия кнопки "Ack",
        # Consumer пришлет SDC-операцию, и этот статус изменится.
        try:
            signal_state = provider.mdib.states.descriptor_handle.get_one('al_signal_hr_hi')
            presence_status = signal_state.Presence
        except Exception as e:
            presence_status = f"ERROR: {e}"

        # Визуальный маркер для удобства чтения консоли
        if presence_status == AlertSignalPresence.ACK:
            marker = "🟡 ACKNOWLEDGED"
        elif presence_status == AlertSignalPresence.ON:
            marker = "🔴 ALARM ON"
        else:
            marker = f"⚪ {presence_status}"

        print(
            f'[t={elapsed:>3}s] HR=150 bpm | State: {marker}',
            flush=True,
        )

        elapsed += 1
        await asyncio.sleep(1)


# ── Entrypoint ────────────────────────────────────────────────────────────────

NETWORK_ADAPTER = 'Wi-Fi'
MDIB_FILE       = 'correct_mdib.xml'

if __name__ == '__main__':
    my_uuid   = uuid.UUID('ba8ad49f-e25b-43ad-870b-c1bdba91d431')
    mdib_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), MDIB_FILE)

    mdib = ProviderMdib.from_mdib_file(mdib_path)

    # DPWS device identification — consumed by ClinicalRiskFilter._DEVICE_PROFILES
    # Key: manufacturer='Acme Medical', model='PatientMonitor Pro 3000'
    model = ThisModelType(
        manufacturer='Acme Medical',
        manufacturer_url='https://acme-medical.example.com',
        model_name='PatientMonitor Pro 3000',
        model_number='PPM-3000-REV-B',
    )
    device = ThisDeviceType(
        friendly_name='ICU Bedside Monitor (AlarmCoordinator Test)',
        serial_number='ACM-001-TEST',
    )

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
