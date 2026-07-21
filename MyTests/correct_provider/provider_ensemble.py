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
    Posterior_P    ≈ 0.0321   risk = 0.0321 × 10.0 = 0.32  < 5.0 → SUPPRESS (Stage 1 artifact)

  Phase 2 — Monitor + Vent  (LR=6.60 × 9.80 = 64.68):
    Posterior_Odds = 0.005025 × 64.68  ≈ 0.3250
    Posterior_P    ≈ 0.2452   risk = 0.2452 × 10.0 = 2.45  < 5.0 → WARN (Stage 2, Yellow)

  Phase 3 — All 3  (LR=6.60 × 9.80 × 11.875 = 768.1):
    Posterior_Odds = 0.005025 × 768.1  ≈ 3.860
    Posterior_P    ≈ 0.7943   risk = 0.7943 × 10.0 = 7.94  ≥ 5.0 → ESCALATE ✓

Simulation — 225-second cycle (45 s per phase)
-----------------------------------------------
  Phase 0  t=  0- 44  BASELINE      All vitals normal, no alarms
  Phase 1  t= 45- 89  MONO ALARM    Monitor only   → risk=0.32  Stage1 SUPPRESS
  Phase 2  t= 90-134  DUAL ALARM    Monitor + Vent → risk=2.45  Stage2 WARN (Yellow)
  Phase 3  t=135-179  TRIPLE CRISIS All 3 devices  → risk=7.94  ESCALATE (Red)
            (metrics ramp gradually; monitor+vent alarms recycled OFF→ON at Phase3 start
             to refresh consumer TTL cache before pump alarm fires)
  Phase 4  t=180-224  RECOVERY      All cleared
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

# ── Timing (120-second cycle — 20 s per phase) ────────────────────────────────

CYCLE_SEC    = 120

PHASE1_START = 20;  PHASE1_END = 40   # MONO-ARTIFACT  : Monitor HR spike (Stage 1 SUPPRESS)
PHASE2_START = 40;  PHASE2_END = 60   # MONO-WARN      : Vent Paw slow ramp (Stage 2 WARN)
PHASE3_START = 60;  PHASE3_END = 80   # DUAL-WARN      : Vent + Monitor (Stage 2 WARN)
PHASE4_START = 80;  PHASE4_END = 100  # TRIPLE-CRISIS  : All 3 devices (Stage 2 ESCALATE)
PHASE5_START = 100; PHASE5_END = 120  # RECOVERY       : all alarms cleared

# ── Phase boundary values (pre-computed for RoC continuity) ──────────────────
# Each phase ramp starts from the previous phase's final value so the derivative
# at the boundary = 0 → Stage 1 HardwareArtifactFilter never falsely suppresses.

_P2_STEPS      = PHASE2_END - PHASE2_START - 1   # 19
_PAW_FINAL_P2  = 18.0 + _P2_STEPS * 1.0          # 37.0 cmH2O

_P3_STEPS      = PHASE3_END - PHASE3_START - 1   # 19
_HR_FINAL_P3   = 75 + (_P3_STEPS + 1) * 2        # 75 + 40 = 115 bpm
_PAW_FINAL_P3  = _PAW_FINAL_P2 + _P3_STEPS * 1.0 # 37.0 + 19 = 56.0 cmH2O

_PLINE_P4_BASE = 120                              # pline only ramps in Phase 4


# ── Vitals computation ────────────────────────────────────────────────────────

def _compute_vitals(t: int) -> tuple[int, float, int]:
    """Return (hr_bpm, airway_cmH2O, line_mmHg) for cycle position t.

    RoC guarantees — all ramps pass Stage 1 (except Phase 1 intentional artifact):
      HR    limit = 10 bpm/s  → Phase 1: +100 (SUPPRESS), Phase 3/4: +2/+3.2
      Paw   limit = 50 cmH2O/s → Phase 2/3/4: +1.0/+1.0/+1.3
      Pline limit = 100 mmHg/s → Phase 4: +26

    Continuity at phase boundaries (RoC=0):
      Phase 2 end → Phase 3 start: paw = _PAW_FINAL_P2
      Phase 3 end → Phase 4 start: hr  = _HR_FINAL_P3, paw = _PAW_FINAL_P3
    """
    hr    = 75
    paw   = 18.0
    pline = 120

    if PHASE1_START <= t < PHASE1_END:
        # MONO-ARTIFACT: intentional HR spike → RoC=100 >> limit=10 → Stage 1 SUPPRESS
        hr  = 175
        paw = 18.0

    elif PHASE2_START <= t < PHASE2_END:
        # MONO-WARN: slow Paw ramp (+1.0/s ≤ 50 ✓), HR at baseline
        steps = t - PHASE2_START
        hr    = 75
        paw   = 18.0 + steps * 1.0

    elif PHASE3_START <= t < PHASE3_END:
        # DUAL-WARN: Vent continues Paw ramp; Monitor starts slow HR ramp (+2/s ≤ 10 ✓)
        # step 0 → hr=77 (RoC=2 bpm/s, well below limit=10)
        steps = t - PHASE3_START
        hr    = 75 + (steps + 1) * 2
        paw   = _PAW_FINAL_P2 + steps * 1.0

    elif PHASE4_START <= t < PHASE4_END:
        # TRIPLE-CRISIS: all three ramp from Phase 3 finals (RoC at boundary = 0)
        steps = t - PHASE4_START
        hr    = _HR_FINAL_P3   + int(steps * 3.2)
        paw   = round(_PAW_FINAL_P3 + steps * 1.3, 1)
        pline = _PLINE_P4_BASE + steps * 26

    return hr, paw, pline


def _phase_name(t: int) -> str:
    if t < PHASE1_START: return 'BASELINE     '
    if t < PHASE1_END:   return 'MONO-ARTIFACT'
    if t < PHASE2_END:   return 'MONO-WARN    '
    if t < PHASE3_END:   return 'DUAL-WARN    '
    if t < PHASE4_END:   return 'TRIPLE-CRISIS'
    return                      'RECOVERY     '


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


async def _keepalive_refresh(
    p_monitor: MySdcProvider,
    p_vent:    MySdcProvider,
    p_pump:    MySdcProvider,
    mon_alarm: bool,
    vent_alarm: bool,
    pump_alarm: bool,
    elapsed:   int,
) -> None:
    """
    Silent TTL keep-alive: re-assert Presence=True for every active alarm.

    sdc11073 marks any state accessed inside alert_state_transaction() as
    modified and includes it in the next EpisodicAlertReport, even when the
    value has not changed.  The consumer receives the report and updates the
    timestamp (ts) in _active_alarms — preventing Watchdog GC expiry at 10 s.

    No OFF step here.  OFF would trigger clear_alarm() on the consumer, reset
    the UI to Blue, and force a full Bayesian re-evaluation — breaking the
    state machine.  Pure re-assertion of ON is invisible to the UI.
    """
    if mon_alarm:
        _alarm_on(p_monitor, 'al_monitor_hi', 'al_signal_monitor_hi')
    if vent_alarm:
        _alarm_on(p_vent, 'al_vent_hi', 'al_signal_vent_hi')
    if pump_alarm:
        _alarm_on(p_pump, 'al_pump_occ', 'al_signal_pump_occ')

    print(f'[t={elapsed:>3}s | {_ts()}] ♻  Keep-Alive re-assert '
          f'(mon={mon_alarm} vent={vent_alarm} pump={pump_alarm})', flush=True)


async def main(  # noqa: C901
    p_monitor: MySdcProvider,
    p_vent:    MySdcProvider,
    p_pump:    MySdcProvider,
) -> None:
    elapsed    = 0
    mon_alarm  = False
    vent_alarm = False
    pump_alarm = False
    SEP = '─' * 78

    print(f'\n{SEP}')
    print('  IHE-PCD ACM  3-Node ICU Ensemble — 120-second cycle (20 s per phase)')
    print(f'  Device 1: Draeger / Infinity Monitor  (HR,    UUID …001)')
    print(f'  Device 2: Draeger / Evita Ventilator  (Paw,   UUID …002)')
    print(f'  Device 3: BBraun  / Space Perfusor    (Pline, UUID …003)')
    print(f'  Ensemble: patient="test-patient-1"  room="ICU-1"  prior=0.005')
    print(f'{SEP}')
    print(f'  Phase 0  t=  0-19  BASELINE        All vitals normal, no alarms')
    print(f'  Phase 1  t= 20-39  MONO-ARTIFACT   Monitor HR spike (RoC=100>>10)')
    print(f'                                     ✗ Stage 1 SUPPRESS → UI: 🔵 Blue')
    print(f'  Phase 2  t= 40-59  MONO-WARN       Vent Paw slow ramp (+1/s)')
    print(f'                                     ⚠ Stage 2 WARN  risk≈0.47<5.0 → UI: 🟡 Yellow')
    print(f'  Phase 3  t= 60-79  DUAL-WARN       Vent+Monitor slow ramps')
    print(f'                                     ⚠ Stage 2 WARN  risk=2.45<5.0 → UI: 🟡 Yellow')
    print(f'  Phase 4  t= 80-99  TRIPLE-CRISIS   All 3 devices alarm')
    print(f'                                     🚨 Stage 2 ESCALATE risk=7.94≥5.0 → UI: 🔴 Red')
    print(f'  Phase 5  t=100-119 RECOVERY        All alarms cleared → UI: 🔵 Blue')
    print(f'  Keep-Alive: re-assert Presence=True every 8 s (silent, no OFF step)')
    print(f'              prevents consumer Watchdog GC expiry at ALARM_TTL_SEC=10 s')
    print(f'{SEP}\n')

    while True:
        t = elapsed % CYCLE_SEC
        hr_val, paw_val, pline_val = _compute_vitals(t)

        # ── Metric updates BEFORE alarm transitions ───────────────────────────
        _set_monitor(p_monitor, Decimal(str(hr_val)))
        _set_vent(p_vent,       Decimal(str(round(paw_val, 1))))
        _set_pump(p_pump,       Decimal(str(pline_val)))

        # ── Alarm state machine ───────────────────────────────────────────────

        # Phase 1 start — Monitor artifact spike (Stage 1 SUPPRESS expected)
        if t == PHASE1_START and not mon_alarm:
            mon_alarm = True
            _alarm_on(p_monitor, 'al_monitor_hi', 'al_signal_monitor_hi')
            print(f'\n{SEP}')
            print(f'[t={elapsed:>3}s | {_ts()}] 🧪 PHASE 1 — MONO-ARTIFACT  [Draeger Infinity Monitor]')
            print(f'         HR: 75 → 175 bpm  (RoC=100 bpm/s  >>  limit=10 bpm/s)')
            print(f'         al_monitor_hi (Priority=Hi) fired.')
            print(f'         Expected: Stage 1 HardwareArtifactFilter → SUPPRESS')
            print(f'         UI: Monitor stays 🔵 Blue (artifact hidden)')
            print(f'{SEP}\n')

        # Phase 1 end — clear Monitor artifact, start Phase 2
        elif t == PHASE1_END and mon_alarm and not vent_alarm:
            _alarm_off(p_monitor, 'al_monitor_hi', 'al_signal_monitor_hi')
            mon_alarm = False
            print(f'[t={elapsed:>3}s | {_ts()}]    Phase 1 end — Monitor artifact cleared.\n')

        # Phase 2 start — Vent slow Paw ramp (Stage 2 WARN expected)
        if t == PHASE2_START and not vent_alarm:
            vent_alarm = True
            _alarm_on(p_vent, 'al_vent_hi', 'al_signal_vent_hi')
            print(f'\n{SEP}')
            print(f'[t={elapsed:>3}s | {_ts()}] ⚠  PHASE 2 — MONO-WARN  [Draeger Evita Ventilator]')
            print(f'         Paw: {paw_val:.1f} cmH2O  (+1.0/s, RoC=1.0 < 50 ✓)')
            print(f'         al_vent_hi (Priority=Hi) fired.')
            print(f'         Ensemble(1 vent): LR+=9.80  risk≈0.47 < 5.0')
            print(f'         Expected: Stage 2 ClinicalRisk → WARN')
            print(f'         UI: Vent 🟡 Yellow, Patient 🟡 Yellow')
            print(f'{SEP}\n')

        # Phase 3 start — add Monitor slow HR ramp (Stage 2 WARN, 2-device ensemble)
        elif t == PHASE3_START and vent_alarm and not mon_alarm:
            mon_alarm = True
            _alarm_on(p_monitor, 'al_monitor_hi', 'al_signal_monitor_hi')
            print(f'\n{SEP}')
            print(f'[t={elapsed:>3}s | {_ts()}] ⚠  PHASE 3 — DUAL-WARN  [Monitor + Ventilator]')
            print(f'         HR: 75→77 bpm ramp +2/s  (RoC=2 < 10 ✓)')
            print(f'         Paw: {paw_val:.1f} cmH2O  (continuing ramp ✓)')
            print(f'         al_monitor_hi + al_vent_hi both active.')
            print(f'         Ensemble(2 devices): LR+=6.60×9.80=64.68  risk=2.45 < 5.0')
            print(f'         Expected: Stage 2 ClinicalRisk → WARN')
            print(f'         UI: Monitor+Vent 🟡 Yellow, Patient 🟡 Yellow')
            print(f'{SEP}\n')

        # Phase 4 start — add Pump (ESCALATE expected).
        # Keep-Alive (every 8 s) keeps monitor+vent TTL fresh, so the consumer
        # already has both in _active_alarms when pump fires.  No OFF→ON recycle needed.
        elif t == PHASE4_START and mon_alarm and vent_alarm and not pump_alarm:
            _alarm_on(p_pump, 'al_pump_occ', 'al_signal_pump_occ')
            pump_alarm = True
            print(f'\n{SEP}')
            print(f'[t={elapsed:>3}s | {_ts()}] 🚨 PHASE 4 — TRIPLE-CRISIS  [All 3 devices]')
            print(f'         HR≈{_HR_FINAL_P3} bpm  Paw≈{_PAW_FINAL_P3:.1f} cmH2O  Pline≈{_PLINE_P4_BASE} mmHg')
            print(f'         RoC: HR +3.2/s < 10 ✓  Paw +1.3/s < 50 ✓  Pline +26/s < 100 ✓')
            print(f'         al_pump_occ fires; monitor+vent already in consumer TTL cache.')
            print(f'         al_monitor_hi + al_vent_hi + al_pump_occ ALL active.')
            print(f'         Ensemble(3 devices): LR+=6.60×9.80×11.875=768.1')
            print(f'         Posterior_Odds=0.005025×768.1≈3.860  Posterior_P≈0.794')
            print(f'         ✓ risk = 0.794 × 10.0 = 7.94  ≥  5.0  →  ESCALATE !!!')
            print(f'         UI: All devices 🔴 Red, Patient 🔴 Red')
            print(f'{SEP}\n')

        # Phase 5 start — RECOVERY
        elif t == PHASE5_START and (mon_alarm or vent_alarm or pump_alarm):
            if mon_alarm:
                _alarm_off(p_monitor, 'al_monitor_hi', 'al_signal_monitor_hi')
            if vent_alarm:
                _alarm_off(p_vent,    'al_vent_hi',    'al_signal_vent_hi')
            if pump_alarm:
                _alarm_off(p_pump,    'al_pump_occ',   'al_signal_pump_occ')
            mon_alarm = vent_alarm = pump_alarm = False
            print(f'[t={elapsed:>3}s | {_ts()}] ✅ PHASE 5 — RECOVERY: all alarms cleared.')
            print(f'         UI: All devices 🔵 Blue, Patient 🔵 Blue\n')

        # ── TTL Keep-Alive: micro-toggle every 8 s ────────────────────────────
        # sdc11073 only fires EpisodicAlertReport on *change*.  Watchdog GC
        # expires silent alarms after ALARM_TTL_SEC=10 s → UI resets to Blue.
        # This refresh forces new reports, resetting the TTL counter silently.
        if elapsed % 8 == 0 and elapsed > 0 and (mon_alarm or vent_alarm or pump_alarm):
            await _keepalive_refresh(
                p_monitor, p_vent, p_pump,
                mon_alarm, vent_alarm, pump_alarm,
                elapsed,
            )

        # ── Per-second status line ────────────────────────────────────────────
        alarms: list[str] = []
        if mon_alarm:  alarms.append('MON🔴')
        if vent_alarm: alarms.append('VENT🔴')
        if pump_alarm: alarms.append('PUMP🔴')
        alarm_str = ','.join(alarms) if alarms else '🟢 none'
        print(
            f'[t={elapsed:>3}s | {_ts()} | {_phase_name(t)} | {alarm_str:<18}]'
            f'  HR={hr_val:>3}  Paw={paw_val:>5.1f}  Pline={pline_val:>4}',
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




