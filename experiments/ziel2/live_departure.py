"""live_departure.py -- headless reproduction of a device departure with the REAL Dashboard
objects (SdcMyConsumer, PatientOverviewModel) and two real testbed providers.

Usage:  .venv\\Scripts\\python experiments\\ziel2\\live_departure.py <adapter-ipv4>

Ziel 2 (connection loss / fallback): phase 3 = graceful shutdown (SubscriptionEnd + Bye),
phase 4 = killed process (TCP RST), phases 5-6 = reconnect of the same EPR.  Since Fix 17
a loss through an error is a TECHNICAL ALARM: the overview keeps a "Verbindung verloren"
card (connectionLost / lostDevices in the snapshots) until the EPR reconnects — the
"card still on the overview?" questions in the phase titles therefore expect YES now.
Needs pki/ecg, pki/spo2, pki/dashboard (mTLS) and ports 10002/10003 free.
Written for the 2026-09-30 diagnosis (Fix 9); after Fix 9 the overview cards follow
the model: devices=1 right after the SpO2 departure, no card after the full release,
one card per live ensemble after a restart in another room.

No QML window: QCoreApplication + processEvents() drive the queued
signals of PatientOverviewModel / QtDeviceHandler.

Phases:
  1. start ECG (Acme Medical / PatientMonitor Pro 3000: 8867-4 has a profile) and SpO2
     (59408-5 has a profile) providers for patient P001, room ICU-1  -> one ensemble, |M| = 2
  2. alarm on ECG -> aggregator built; capture k_min
  3. SpO2 'shutdown' (graceful, Bye)   -> partial departure
  4. ECG killed (no Bye)               -> full departure
  5. ECG restarted, same EPR, room ICU-2 -> restart scenario
After every phase print: manager.devices, topology members, |M|, k_min, overview cards.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import threading
import time

# experiments/ziel2/live_departure.py → repo root is two levels up (moved from tools/ on 2026-10-11)
REPO = str(pathlib.Path(__file__).resolve().parents[2])
DASH = os.path.join(REPO, 'Dashboard')
sys.path.insert(0, DASH)
os.chdir(DASH)

IP = sys.argv[1] if len(sys.argv) > 1 else '192.168.247.204'
EPR_ECG = 'urn:uuid:aaaaaaaa-1111-4111-8111-111111111111'
EPR_SPO2 = 'urn:uuid:bbbbbbbb-2222-4222-8222-222222222222'

import logging
from sdc11073.loghelper import basic_logging_setup
basic_logging_setup(level=logging.WARNING)
from PySide6.QtCore import QCoreApplication
app = QCoreApplication([])
from app.patientOverviewModel import PatientOverviewModel
from app.sdcMyConsumer import SdcMyConsumer
logging.getLogger('sdc.consumer').setLevel(logging.WARNING)
for name in ('sdc.consumer.topology', 'sdc.consumer.manager', 'sdc.consumer.aggregator'):
    logging.getLogger(name).setLevel(logging.INFO)


def ts() -> str:
    return time.strftime('%H:%M:%S')


class Prov:
    def __init__(self, tag, mdib, certs, port, epr, room):
        args = [sys.executable, os.path.join(REPO, 'testbed', 'providers', 'testbed_provider.py'),
                '--mdib', os.path.join(REPO, 'testbed', 'mdib', mdib), '--epr', epr,
                '--manufacturer', 'Acme Medical', '--model', 'PatientMonitor Pro 3000',
                '--friendly-name', f'live {tag}', '--patient-id', 'P001', '--room', room, '--bed', '1',
                '--certs-dir', os.path.join(REPO, 'pki', certs), '--ip', IP, '--port', str(port), '--seed', '1']
        self.tag = tag
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding='utf-8', errors='replace', bufsize=1,
                                     creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        self.lines = []
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            line = line.rstrip('\n')
            self.lines.append(line)
            if 'publish:' not in line:
                print(f'  {self.tag}| {line}', flush=True)

    def send(self, cmd):
        print(f'>> {self.tag}: {cmd}', flush=True)
        self.proc.stdin.write(cmd + '\n'); self.proc.stdin.flush()

    def wait_ready(self, timeout=30):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if any('ready:' in l for l in self.lines):
                return True
            time.sleep(0.2)
        return False


overview = PatientOverviewModel()
manager = SdcMyConsumer(target_room=None, override_ip=IP, tls_mode='auto', overview_model=overview)
topo = manager.topology
sa = manager.aggregator
tick_results = []


def pump(seconds: float):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.05)


def snapshot(label: str):
    app.processEvents()
    with topo.lock:
        ens = {k: sorted(v) for k, v in topo._ensembles_devices.items()}
        key_map = dict(topo._patient_to_ensemble_map)
    with manager.lock:
        devs = sorted(manager.devices)
    print(f'\n=== [{ts()}] {label} ===')
    print(f'  manager.devices      : {[d[-12:] for d in devs]}')
    print(f'  topology ensembles   : {{ {", ".join(f"{k[:8]}: {[e[-12:] for e in v]}" for k, v in ens.items())} }}')
    print(f'  (patient,room)->ens  : {{ {", ".join(f"{k}: {v[:8]}" for k, v in key_map.items())} }}')
    for eid in ens:
        specs = topo.get_member_specs(eid)
        agg = sa._ensemble_adaptive_aggregators.get(eid)
        print(f'  ensemble {eid[:8]}: |M|={len(specs)} channels={sorted(specs)} '
              f'aggregator_sensor_ids={sorted(agg.sensor_ids) if agg else None}')
    last = tick_results[-1] if tick_results else None
    print(f'  last tick            : k_min={last.k_min if last else None} '
          f'|M|(tick)={len(last.sensor_states) if last and hasattr(last, "sensor_states") else "n/a"}')
    cards = overview.patients
    print(f'  PatientOverview cards: {[(c["ensembleUuid"][:8], c["patientName"], c["room"], "devices=" + str(c["deviceCount"]), "yellow" if c["hasActiveAlarms"] else "blue", "RED" if c["isEscalated"] else "") for c in cards]}')


def wait_until(pred, timeout, what):
    end = time.monotonic() + timeout
    t0 = time.monotonic()
    while time.monotonic() < end:
        app.processEvents()
        if pred():
            print(f'  [{ts()}] {what}: after {time.monotonic() - t0:.1f} s')
            return True
        time.sleep(0.2)
    print(f'  [{ts()}] {what}: TIMEOUT after {timeout} s')
    return False


def members_of_first_ensemble():
    with topo.lock:
        for v in topo._ensembles_devices.values():
            return set(v)
    return set()


def install_tick_spy():
    for eid, agg in list(sa._ensemble_adaptive_aggregators.items()):
        if getattr(agg, '_spied', False):
            continue
        real = agg.tick

        def spy(*a, _real=real, **k):
            r = _real(*a, **k)
            tick_results.append(r)
            return r
        agg.tick = spy
        agg._spied = True


try:
    # ── Phase 1: two providers, one ensemble ────────────────────────────────────
    ecg = Prov('ECG', 'ecg_mdib_v2.xml', 'ecg', 10002, EPR_ECG, 'ICU-1')
    spo2 = Prov('SPO2', 'spo2_mdib_v2.xml', 'spo2', 10003, EPR_SPO2, 'ICU-1')
    assert ecg.wait_ready() and spo2.wait_ready()
    manager.start()
    wait_until(lambda: len(members_of_first_ensemble()) == 2, 90, 'both devices bound to one ensemble')
    pump(2)
    snapshot('phase 1: both connected')

    # ── Phase 2: alarm on ECG → aggregator, k_min ───────────────────────────────
    ecg.send('alarm tachycardia on')
    wait_until(lambda: bool(sa._ensemble_adaptive_aggregators), 15, 'aggregator built')
    install_tick_spy()
    ecg.send('alarm tachycardia off'); pump(1.5)
    ecg.send('alarm tachycardia on'); pump(1.5)
    snapshot('phase 2: ECG tachycardia ON (k_min from the tick)')
    ecg.send('alarm tachycardia off'); pump(1.5)

    # ── Phase 3: SpO2 graceful shutdown (Bye) → partial departure ───────────────
    spo2.send('shutdown')
    t_bye = time.monotonic()
    wait_until(lambda: EPR_SPO2 not in manager.devices, 60, 'SpO2 worker removed after Bye')
    pump(2)
    snapshot('phase 3: after SpO2 shutdown (Bye) -- overview still says devices=2?')
    ecg.send('alarm tachycardia on'); pump(1.5)
    snapshot('phase 3b: next alarm event on ECG (k_min over survivors, card refreshed by the event)')
    ecg.send('alarm tachycardia off'); pump(1.5)

    # ── Phase 4: ECG killed (no Bye) → full departure ───────────────────────────
    ecg.proc.kill()
    wait_until(lambda: EPR_ECG not in manager.devices, 60, 'ECG worker removed after kill')
    pump(2)
    snapshot('phase 4: after ECG killed -- ensemble gone in the model, card still on the overview?')
    pump(12)   # one GC period + grace
    snapshot('phase 4b: 12 s later (GC ran)')

    # ── Phase 5: ECG restarted, same EPR, other room ─────────────────────────────
    ecg = Prov('ECG2', 'ecg_mdib_v2.xml', 'ecg', 10002, EPR_ECG, 'ICU-2')
    assert ecg.wait_ready()
    wait_until(lambda: EPR_ECG in manager.devices and any(
        k[1] == 'ICU-2' for k in topo._patient_to_ensemble_map), 90, 'ECG re-bound in ICU-2')
    pump(2)
    snapshot('phase 5: restarted with the same EPR in ICU-2 -- one ensemble, how many cards?')

    # ── Phase 6: quick kill + immediate restart in yet another room ──────────────
    ecg.proc.kill()
    ecg = Prov('ECG3', 'ecg_mdib_v2.xml', 'ecg', 10002, EPR_ECG, 'ICU-3')
    assert ecg.wait_ready()
    wait_until(lambda: any(k[1] == 'ICU-3' for k in topo._patient_to_ensemble_map), 120,
               'ECG re-bound in ICU-3 after kill+restart before the dashboard noticed')
    pump(2)
    snapshot('phase 6: kill + immediate restart in ICU-3')
    ecg.send('shutdown')
    pump(3)
finally:
    for p in (ecg, spo2):
        try:
            p.proc.kill()
        except Exception:
            pass
    manager.stop()
    sa.stop()
    print('done')
