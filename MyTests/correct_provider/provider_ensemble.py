"""
provider_ensemble.py — IHE-PCD ACM 3-Node ICU Ensemble Test Harness
====================================================================
Emulates THREE independent SDC devices in the same ICU patient room.
SmartAlertAggregator detects identical (patient_id, room) keys and binds
all three into ONE ensemble, enabling true multi-device Bayesian fusion.

Devices
-------
  Provider 1 — Draeger / Infinity Monitor       (HR monitor)
    MDIB: mdib_monitor.xml   UUID: …001
    Alert: al_monitor_hi  Priority=Hi  Source: hr_metric (8867-4)
    LR+ = 0.99/0.15 = 6.60

  Provider 2 — Draeger / Evita Ventilator       (ventilator)
    MDIB: mdib_vent.xml      UUID: …002
    Alert: al_vent_hi     Priority=Hi  Source: airway_pressure (20053-5)
    LR+ = 0.98/0.10 = 9.80

  Provider 3 — BBraun / Space Perfusor          (infusion pump)
    MDIB: mdib_pump.xml      UUID: …003
    Alert: al_pump_occ    Priority=Hi  Source: line_pressure (8775-2)
    LR+ = 0.95/0.08 = 11.875

Ensemble key: patient_id="test-patient-1"  room="ICU-1"

Bayesian maths (prior = 0.005)
--------------------------------
  Prior_Odds = 0.005 / 0.995 ≈ 0.005025

  Phase 1 — Monitor alone  (LR=6.60):
    Posterior_Odds = 0.005025 × 6.60   ≈ 0.03317
    Posterior_P    ≈ 0.0321   risk = 0.0321 × 10.0 = 0.32  < 5.0 → SUPPRESS

  Phase 2 — Monitor + Vent  (LR=6.60 × 9.80 = 64.68):
    Posterior_Odds = 0.005025 × 64.68  ≈ 0.3250
    Posterior_P    ≈ 0.2452   risk = 0.2452 × 10.0 = 2.45  < 5.0 → SUPPRESS

  Phase 3 — All 3  (LR=6.60 × 9.80 × 11.875 = 768.1):
    Posterior_Odds = 0.005025 × 768.1  ≈ 3.860
    Posterior_P    ≈ 0.7943   risk = 0.7943 × 10.0 = 7.94  ≥ 5.0 → ESCALATE ✓

Simulation — 45-second cycle
------------------------------
  Phase 0  t= 0- 4   BASELINE      All vitals normal, no alarms
  Phase 1  t= 5-14   MONO ALARM    Monitor only   → risk=0.32  SUPPRESS
  Phase 2  t=15-24   DUAL ALARM    Monitor + Vent → risk=2.45  SUPPRESS
  Phase 3  t=25-34   TRIPLE CRISIS All 3 devices  → risk=7.94  ESCALATE ✓
            (metrics ramp gradually from Phase 2 finals;
             monitor+vent alarms recycled OFF→ON to refresh consumer TTL cache)
  Phase 4  t=35-44   RECOVERY      All cleared
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime
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
    """SdcProvider subclass that injects required SDC CDC scope codes."""

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


# ── Device UUIDs ──────────────────────────────────────────────────────────────

UUID_MONITOR = uuid.UUID('11111111-0000-0000-0000-000000000001')
UUID_VENT    = uuid.UUID('22222222-0000-0000-0000-000000000002')
UUID_PUMP    = uuid.UUID('33333333-0000-0000-0000-000000000003')

# ── Timing (45-second cycle) ──────────────────────────────────────────────────

CYCLE_SEC      = 45

PHASE1_START   = 5    # Monitor al_monitor_hi ON
PHASE1_END     = 15   # Monitor alarm OFF

PHASE2_START   = 15   # Monitor + Vent alarms ON
PHASE2_END     = 25   # Both OFF

PHASE3_START   = 25   # All 3 alarms ON  — CRISIS
PHASE3_END     = 35   # All OFF

# Phase 4 / RECOVERY: t=35-44, all alarms cleared above


# ── Vitals computation ────────────────────────────────────────────────────────

def _compute_vitals(t: int) -> tuple[int, float, int]:
    """Return (hr_bpm, airway_cmH2O, line_mmHg) for cycle position t."""
    hr    = 75
    paw   = 18.0
    pline = 120

    if PHASE1_START <= t < PHASE1_END:
        # Phase 1: HR artifact spike (RoC=100 >> limit=10 → Stage1 suppresses)
        hr = 175

    elif PHASE2_START <= t < PHASE2_END:
        # Phase 2: physiologically plausible slow HR rise + Paw rise
        steps = t - PHASE2_START
        hr    = 80 + steps * 2       # +2 bpm/s, RoC=2 < 10 → Stage1 passes
        paw   = 20.0 + steps * 1.0   # +1 cmH2O/s, RoC=1 < 50 → Stage1 passes

    elif PHASE3_START <= t < PHASE3_END:
        # Phase 3: GRADUAL crisis ramp — continuity from Phase 2 final values:
        #   Phase 2 ends at t=24 (steps=9): hr=98, paw=29.0, pline=120
        #
        #   HR:    98  → 130 bpm   (+3.2 bpm/s   < limit=10  ✓)
        #   Paw:   29  → 42  cmH2O (+1.3 cmH2O/s < limit=50  ✓)
        #   Pline: 120 → 380 mmHg  (+26  mmHg/s  < limit=100 ✓)
        #
        # At t=PHASE3_START (steps=0) values equal Phase 2 final → RoC=0 → Stage1 always passes.
        steps = t - PHASE3_START
        hr    = int(98   + steps * 3.2)       # 98  … 126 bpm
        paw   = round(29.0 + steps * 1.3, 1)  # 29.0 … 40.7 cmH2O
        pline = int(120  + steps * 26)        # 120  … 354 mmHg

    return hr, paw, pline


def _phase_name(t: int) -> str:
    if t < PHASE1_START:  return 'BASELINE      '
    if t < PHASE1_END:    return 'MONO-ALARM    '
    if t < PHASE2_END:    return 'DUAL-ALARM    '
    if t < PHASE3_END:    return 'TRIPLE-CRISIS '
    return                       'RECOVERY      '


# ── MDIB helpers ──────────────────────────────────────────────────────────────

def _set_monitor(provider, hr: Decimal) -> None:
    with provider.mdib.metric_state_transaction() as tr:
        tr.get_state('hr_metric').MetricValue.Value = hr

def _set_vent(provider, paw: Decimal) -> None:
    with provider.mdib.metric_state_transaction() as tr:
        tr.get_state('airway_pressure').MetricValue.Value = paw

def _set_pump(provider, pline: Decimal) -> None:
    with provider.mdib.metric_state_transaction() as tr:
        tr.get_state('line_pressure').MetricValue.Value = pline

def _alarm_on(provider, condition_handle: str, signal_handle: str) -> None:
    with provider.mdib.alert_state_transaction() as tr:
        tr.get_state(condition_handle).Presence = True
        tr.get_state(signal_handle).Presence    = AlertSignalPresence.ON

def _alarm_off(provider, condition_handle: str, signal_handle: str) -> None:
    with provider.mdib.alert_state_transaction() as tr:
        tr.get_state(condition_handle).Presence = False
        tr.get_state(signal_handle).Presence    = AlertSignalPresence.OFF


# ── Simulation loop ───────────────────────────────────────────────────────────

def _ts() -> str:
    """Return current wall-clock time as HH:MM:SS.mmm string."""
    return datetime.now().strftime('%H:%M:%S.%f')[:-3]


async def main(  # noqa: C901
    p_monitor: MySdcProvider,
    p_vent:    MySdcProvider,
    p_pump:    MySdcProvider,
) -> None:
    elapsed       = 0
    mon_alarm     = False
    vent_alarm    = False
    pump_alarm    = False
    SEP = '─' * 78

    print(f'\n{SEP}')
    print('  IHE-PCD ACM  3-Node ICU Ensemble Test Harness  —  45-second cycle')
    print(f'  Device 1: Draeger / Infinity Monitor  (HR,   UUID …001)')
    print(f'  Device 2: Draeger / Evita Ventilator  (Paw,  UUID …002)')
    print(f'  Device 3: BBraun  / Space Perfusor    (Pline,UUID …003)')
    print(f'  Ensemble: patient="test-patient-1"  room="ICU-1"  prior=0.005')
    print(f'{SEP}')
    print('  Phase 0  t= 0- 4  BASELINE        All vitals normal')
    print('  Phase 1  t= 5-14  MONO ALARM       Monitor HR spike (artifact)')
    print('                                     ✗ Stage 1 MUST suppress  (risk=0.32<5.0)')
    print('  Phase 2  t=15-24  DUAL ALARM       Monitor+Vent alarms (local event)')
    print('                                     ✗ Stage 2 MUST suppress  (risk=2.45<5.0)')
    print('  Phase 3  t=25-34  TRIPLE CRISIS    All 3 devices alarm (systemic crisis)')
    print('                                     ✓ Stage 2 MUST escalate  (risk=7.94≥5.0)')
    print('  Phase 4  t=35-44  RECOVERY         All alarms cleared')
    print(f'{SEP}\n')

    while True:
        t = elapsed % CYCLE_SEC
        hr_val, paw_val, pline_val = _compute_vitals(t)

        # ── Metric updates BEFORE alarm transitions ───────────────────────────
        _set_monitor(p_monitor, Decimal(str(hr_val)))
        _set_vent(p_vent,       Decimal(str(round(paw_val, 1))))
        _set_pump(p_pump,       Decimal(str(pline_val)))

        # ── Alarm state machine ───────────────────────────────────────────────

        # Phase 1 start — Monitor artifact spike
        if t == PHASE1_START and not mon_alarm:
            mon_alarm = True
            _alarm_on(p_monitor, 'al_monitor_hi', 'al_signal_monitor_hi')
            print(f'\n{SEP}')
            print(f'[t={elapsed:>3}s | {_ts()}] 🧪 PHASE 1 — MONO ALARM  [Draeger Infinity Monitor]')
            print(f'         HR: 75 → 175 bpm  (RoC=100 bpm/s  >>  limit=10 bpm/s)')
            print(f'         al_monitor_hi (Priority=Hi) fired.')
            print(f'         Ensemble(1 device): LR+=6.60  Prior_Odds×LR+=0.03317')
            print(f'         ✗ risk = 0.0321 × 10.0 = 0.32  <  5.0  →  Stage 1 SUPPRESS')
            print(f'{SEP}\n')

        elif t == PHASE1_END and mon_alarm and not vent_alarm:
            # Phase 1 end / Phase 2 start — add Vent, keep Monitor
            _alarm_on(p_vent, 'al_vent_hi', 'al_signal_vent_hi')
            vent_alarm = True
            print(f'\n{SEP}')
            print(f'[t={elapsed:>3}s | {_ts()}] 🧪 PHASE 2 — DUAL ALARM  [Monitor + Evita Ventilator]')
            print(f'         HR={hr_val} bpm  Paw={paw_val:.1f} cmH2O  (RoC both below limits)')
            print(f'         al_monitor_hi (Hi) + al_vent_hi (Hi) both active.')
            print(f'         Ensemble(2 devices): LR+=6.60×9.80=64.68  Odds=0.3250')
            print(f'         ✗ risk = 0.2452 × 10.0 = 2.45  <  5.0  →  Stage 2 SUPPRESS')
            print(f'{SEP}\n')

        elif t == PHASE2_END and mon_alarm and vent_alarm and not pump_alarm:
            # Phase 2 end / Phase 3 start — add Pump (crisis).
            #
            # BUG-FIX: monitor and vent alarms have been continuously ON since
            # Phase 1/2 with no state change → no new EpisodicAlertReport fired
            # → their TTL cache entries in the consumer expired (10 s).
            # Fix: brief OFF→ON cycle for monitor + vent forces new reports and
            # refreshes their entries.  Pump fires LAST so the consumer evaluates
            # it only after monitor and vent are already in _active_alarms.
            _alarm_off(p_monitor, 'al_monitor_hi', 'al_signal_monitor_hi')
            _alarm_off(p_vent,    'al_vent_hi',    'al_signal_vent_hi')
            await asyncio.sleep(0.15)   # let OFF reports reach consumer first
            _alarm_on(p_monitor, 'al_monitor_hi', 'al_signal_monitor_hi')
            await asyncio.sleep(0.05)   # monitor ON → consumer caches it
            _alarm_on(p_vent,    'al_vent_hi',    'al_signal_vent_hi')
            await asyncio.sleep(0.05)   # vent ON → consumer caches it
            _alarm_on(p_pump,    'al_pump_occ',   'al_signal_pump_occ')
            # Pump evaluated last: TTL cache contains monitor + vent → 3-device fusion
            pump_alarm = True
            print(f'\n{SEP}')
            print(f'[t={elapsed:>3}s | {_ts()}] 🚨 PHASE 3 — TRIPLE CRISIS  [All 3 devices]')
            print(f'         HR≈{int(98)}bpm  Paw≈29.0cmH2O  Pline≈120mmHg  (ramps over 10 s)')
            print(f'         RoC: HR +3.2/s < 10 ✓  Paw +1.3/s < 50 ✓  Pline +26/s < 100 ✓')
            print(f'         monitor+vent alarms recycled (OFF→ON) to refresh consumer TTL cache.')
            print(f'         al_monitor_hi + al_vent_hi + al_pump_occ ALL active.')
            print(f'         Ensemble(3 devices): LR+=6.60×9.80×11.875 = 768.1')
            print(f'         Posterior_Odds = 0.005025 × 768.1 ≈ 3.860')
            print(f'         ✓ risk = 0.7943 × 10.0 = 7.94  ≥  5.0  →  ESCALATE !!!')
            print(f'{SEP}\n')

        elif t == PHASE3_END and (mon_alarm or vent_alarm or pump_alarm):
            # Phase 4 — clear all alarms
            if mon_alarm:
                _alarm_off(p_monitor, 'al_monitor_hi', 'al_signal_monitor_hi')
            if vent_alarm:
                _alarm_off(p_vent,    'al_vent_hi',    'al_signal_vent_hi')
            if pump_alarm:
                _alarm_off(p_pump,    'al_pump_occ',   'al_signal_pump_occ')
            mon_alarm = vent_alarm = pump_alarm = False
            print(f'[t={elapsed:>3}s | {_ts()}] ✅ PHASE 4 — RECOVERY: all alarms cleared, vitals normalising.\n')

        # ── Per-second status line ────────────────────────────────────────────
        alarms: list[str] = []
        if mon_alarm:  alarms.append('MON🔴')
        if vent_alarm: alarms.append('VENT🔴')
        if pump_alarm: alarms.append('PUMP🔴')
        alarm_str = ','.join(alarms) if alarms else '🟢 none'
        print(
            f'[t={elapsed:>3}s | {_ts()} | {_phase_name(t)} | {alarm_str:<18}]'
            f'  HR={hr_val:>3}  Paw={paw_val:>4.1f}  Pline={pline_val:>3}',
            flush=True,
        )

        elapsed += 1
        await asyncio.sleep(1)


# ── Provider factory ──────────────────────────────────────────────────────────

NETWORK_ADAPTER = 'Wi-Fi'
_DIR = os.path.dirname(os.path.abspath(__file__))


def _make_provider(
    mdib_file:     str,
    my_uuid:       uuid.UUID,
    manufacturer:  str,
    model_name:    str,
    friendly_name: str,
    serial:        str,
    discovery:     WSDiscoverySingleAdapter,
) -> MySdcProvider:
    mdib = ProviderMdib.from_mdib_file(os.path.join(_DIR, mdib_file))
    this_model = ThisModelType(
        manufacturer=manufacturer,
        manufacturer_url='https://icu-device.example.com',
        model_name=model_name,
    )
    this_device = ThisDeviceType(
        friendly_name=friendly_name,
        serial_number=serial,
    )
    provider = MySdcProvider(
        ws_discovery=discovery,
        epr=my_uuid,
        this_model=this_model,
        this_device=this_device,
        device_mdib_container=mdib,
        specific_components=SdcProviderComponents(role_provider_class=ExtendedProduct),
        ssl_context_container=None,
    )
    provider.set_used_compression()
    return provider


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    discovery = WSDiscoverySingleAdapter(NETWORK_ADAPTER)

    p_monitor = _make_provider(
        mdib_file='mdib_monitor.xml', my_uuid=UUID_MONITOR,
        manufacturer='Draeger', model_name='Infinity Monitor',
        friendly_name='Draeger Infinity Monitor (ICU)', serial='DRG-MON-001',
        discovery=discovery,
    )
    p_vent = _make_provider(
        mdib_file='mdib_vent.xml', my_uuid=UUID_VENT,
        manufacturer='Draeger', model_name='Evita Ventilator',
        friendly_name='Draeger Evita Ventilator (ICU)', serial='DRG-VNT-002',
        discovery=discovery,
    )
    p_pump = _make_provider(
        mdib_file='mdib_pump.xml', my_uuid=UUID_PUMP,
        manufacturer='BBraun', model_name='Space Perfusor',
        friendly_name='BBraun Space Perfusor (ICU)', serial='BBR-PMP-003',
        discovery=discovery,
    )

    discovery.start()

    for label, prov, uid in [
        ('Infinity Monitor ', p_monitor, UUID_MONITOR),
        ('Evita Ventilator ', p_vent,    UUID_VENT),
        ('Space Perfusor   ', p_pump,    UUID_PUMP),
    ]:
        prov.start_all()
        prov.publish()
        print(f'[INIT] {label}  EPR={uid}')

    print(f'\n[INIT] All 3 devices published.')
    print(f'[INIT] Ensemble key: patient_id="test-patient-1"  room="ICU-1"')
    print(f'[INIT] Consumer should discover all 3 and bind into ONE ensemble.\n')

    try:
        asyncio.run(main(p_monitor, p_vent, p_pump))
    except KeyboardInterrupt:
        p_monitor.stop_all()
        p_vent.stop_all()
        p_pump.stop_all()
        discovery.stop()
        print('\nStopped.')

