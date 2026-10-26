#!/usr/bin/env python
"""demo_scenario.py -- screenshot demo for Kapitel 3.2.5 (Blau -> Gelb -> Rot, Fernquittierung,
Verbindung verloren).

This is a demo for screenshots, not an experiment and not a test.  It replays the worked
example of Kapitel 3 with four testbed providers on one host, all without TLS:

    bed 1 (ICU-1 / 1, patient DEMO-01):  Monitor Demo + Vent Demo   -> Gelb, then Rot
    bed 2 (ICU-1 / 2, patient DEMO-02):  Monitor Demo + Vent Demo   -> Gelb only

Channel set per bed (admitted channels and weights from Dashboard/config/clinical_db.json,
manufacturer "SDC Demo"):  desaturation Hi w=5, tachycardia Lo w=3, vt_low Lo w=2.5,
rr_high Lo w=3  ->  |M| = 4, mean weight 3.375.  With the monitor alone the floor of the
threshold is 0.5 * 3 * 3.375 = 5.06 > 5, so bed 2 never escalates; with the ventilator alarm
added, k_min drops to 2 and bed 1 escalates.

Usage
-----
1. Start the dashboard (from Dashboard/):   python main.py --no_tls --ip <IP>
2. Then:   python experiments/screenshots/demo_scenario.py --ip <IP> [--auto SEC]
   Each phase is advanced with Enter (or after SEC seconds with --auto).

   --headless   no interactive phases: starts the real SdcMyConsumer without a window
                (experiments/harness.py), runs phases 0-2 automatically and prints the
                patient cards (isEscalated, hasActiveAlarms, sdcScore) and the time from
                'alarm vt_low on' to Rot.  Do NOT run the dashboard window at the same time.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'experiments'))
from harness import Prov  # noqa: E402

PROVIDER = REPO / 'testbed' / 'providers' / 'testbed_provider.py'
MDIB_DIR = REPO / 'testbed' / 'mdib'
MANUFACTURER = 'SDC Demo'
ROOM = 'ICU-1'

# tag, mdib file, DPWS model, friendly name, patient id, bed, port, EPR
BEDS = [
    ('MON-1',  'monitor_demo_mdib.xml', 'Monitor Demo', 'Demo monitor, bed 1',    'DEMO-01', '1', 10800,
     'urn:uuid:0de30001-0000-4000-8000-000000000001'),
    ('VENT-1', 'vent_demo_mdib.xml',    'Vent Demo',    'Demo ventilator, bed 1', 'DEMO-01', '1', 10801,
     'urn:uuid:0de30001-0000-4000-8000-000000000002'),
    ('MON-2',  'monitor_demo_mdib.xml', 'Monitor Demo', 'Demo monitor, bed 2',    'DEMO-02', '2', 10802,
     'urn:uuid:0de30001-0000-4000-8000-000000000003'),
    ('VENT-2', 'vent_demo_mdib.xml',    'Vent Demo',    'Demo ventilator, bed 2', 'DEMO-02', '2', 10803,
     'urn:uuid:0de30001-0000-4000-8000-000000000004'),
]


def ts() -> str:
    return time.strftime('%H:%M:%S')


def provider_args(ip: str, mdib: str, model: str, friendly: str, patient: str, bed: str,
                  port: int, epr: str) -> list[str]:
    return [sys.executable, str(PROVIDER),
            '--mdib', str(MDIB_DIR / mdib), '--epr', epr,
            '--manufacturer', MANUFACTURER, '--model', model, '--friendly-name', friendly,
            '--patient-id', patient, '--room', ROOM, '--bed', bed,
            '--no-tls', '--ip', ip, '--port', str(port), '--seed', '1']


def start_providers(ip: str, echo: bool) -> dict[str, Prov]:
    provs: dict[str, Prov] = {}
    for tag, mdib, model, friendly, patient, bed, port, epr in BEDS:
        provs[tag] = Prov(tag, provider_args(ip, mdib, model, friendly, patient, bed, port, epr), echo=echo)
        print(f'[{ts()}] started {tag:6} {model:13} patient={patient} bed={bed} port={port}', flush=True)
    for tag, p in provs.items():
        if not p.wait_line('ready:', 60):
            raise SystemExit(f'ERROR: provider {tag} did not report ready (see its output above)')
    return provs


def send(p: Prov, cmd: str) -> None:
    print(f'[{ts()}] >> {p.tag}: {cmd}', flush=True)
    p.send(cmd)


def phase(n: int, action: str, expect: str) -> None:
    print('\n' + '=' * 78, flush=True)
    print(f'Phase {n}: {action}', flush=True)
    print(f'  expected on the dashboard: {expect}', flush=True)
    print('=' * 78, flush=True)


def advance(auto: float | None, prompt: str = 'next phase') -> None:
    if auto is None:
        try:
            input(f'  [Enter] -> {prompt} ')
        except EOFError:
            time.sleep(5.0)
        return
    for left in range(int(auto), 0, -1):
        print(f'  auto: {prompt} in {left:3d} s', end='\r', flush=True)
        time.sleep(1.0)
    print(' ' * 60, end='\r', flush=True)


def cleanup(provs: dict[str, Prov]) -> None:
    for tag in ('MON-1', 'MON-2'):
        p = provs.get(tag)
        if p and p.alive():
            send(p, 'alarm desaturation off')
    vent2 = provs.get('VENT-2')
    if vent2 and vent2.alive():
        send(vent2, 'alarm vt_low off')
    time.sleep(1.0)
    for p in provs.values():
        if p.alive():
            print(f'[{ts()}] shutdown {p.tag}', flush=True)
            p.shutdown(timeout=10.0)


# ──────────────────────────────────────────────────────────────────────────────
# Interactive run (dashboard window already running)
# ──────────────────────────────────────────────────────────────────────────────

def run_interactive(ip: str, auto: float | None, echo: bool) -> int:
    provs: dict[str, Prov] = {}
    try:
        phase(0, 'four providers connected, no alarms',
              'two patient cards DEMO-01 / DEMO-02, both Blau')
        provs = start_providers(ip, echo)
        print(f'[{ts()}] waiting for the dashboard to bind the devices (EnsembleContext write-back) ...',
              flush=True)
        for p in provs.values():
            if not p.wait_line('EnsembleContext set by consumer', 180):
                print(f'  WARNING: {p.tag} was not bound within 180 s -- is the dashboard running '
                      f'with --no_tls --ip {ip}?', flush=True)
        advance(auto)

        phase(1, "'alarm desaturation on' on both monitors",
              'both cards Gelb, Severity about 75 %')
        send(provs['MON-1'], 'alarm desaturation on')
        send(provs['MON-2'], 'alarm desaturation on')
        advance(auto)

        phase(2, "'alarm vt_low on' on the ventilator of bed 1",
              'bed 1 Rot (Severity about 83 %), bed 2 stays Gelb  <- main screenshot')
        send(provs['VENT-1'], 'alarm vt_low on')
        advance(auto)

        phase(3, 'nothing is sent: acknowledge the alarm by hand on the device page of bed 1',
              'acknowledgement status on the device page; the provider echoes <<< SetAlertState')
        advance(auto, prompt='continue after the screenshot')

        phase(4, 'kill() of the ventilator process of bed 1 (silent loss, no Bye)',
              '"Verbindung verloren" with "während Eskalation" on the device and patient card')
        provs['VENT-1'].kill()
        print(f'[{ts()}] VENT-1 killed (pid tree terminated)', flush=True)
        advance(auto, prompt='finish and shut down')

        phase(5, 'remaining alarms off, graceful shutdown of all providers', '-')
        return 0
    except KeyboardInterrupt:
        print('\ninterrupted', flush=True)
        return 130
    finally:
        cleanup(provs)


# ──────────────────────────────────────────────────────────────────────────────
# Headless check (no window): phases 0-2 against the real SdcMyConsumer
# ──────────────────────────────────────────────────────────────────────────────

def _cards(dash) -> dict[str, dict]:
    dash.app.processEvents()
    return {c.get('patientName', '?'): c for c in dash.overview.patients}


def _fmt(cards: dict[str, dict]) -> str:
    out = []
    for name in sorted(cards):
        c = cards[name]
        out.append(f"    {name:8} devices={c.get('deviceCount')} escalated={c.get('isEscalated')} "
                   f"active={c.get('hasActiveAlarms')} sdc={float(c.get('sdcScore') or 0.0):.4f}"
                   f"{'  LOST' if c.get('lostDevices') else ''}")
    return '\n'.join(out) if out else '    (no cards)'


def run_headless(ip: str, hold1: float, echo: bool) -> int:
    from harness import Dashboard  # noqa: PLC0415
    log_path = REPO / 'experiments' / 'screenshots' / 'logs' / 'headless.jsonl'
    provs: dict[str, Prov] = {}
    rc = 1
    try:
        phase(0, 'four providers + headless dashboard', 'two cards, both deviceCount=2, Blau')
        provs = start_providers(ip, echo)
        dash = Dashboard(ip=ip, room=None, log_path=log_path, tls='no_tls', quiet=True)
        ok = dash.wait_for(lambda: (len(_cards(dash)) == 2 and
                                    all(c.get('deviceCount') == 2 for c in _cards(dash).values())), 180)
        print(f'[{ts()}] bound: {bool(ok)}\n' + _fmt(_cards(dash)), flush=True)
        if not ok:
            return 1
        dash.pump(2.0)

        phase(1, "'alarm desaturation on' on both monitors", 'both cards Gelb, sdc = 0.75')
        send(provs['MON-1'], 'alarm desaturation on')
        send(provs['MON-2'], 'alarm desaturation on')
        dash.wait_for(lambda: all(c.get('hasActiveAlarms') for c in _cards(dash).values()), 15)
        dash.pump(1.0)
        print(f'[{ts()}] after alarm on:\n' + _fmt(_cards(dash)), flush=True)
        print(f'[{ts()}] holding phase 1 for {hold1:.0f} s', flush=True)
        dash.pump(hold1)
        print(f'[{ts()}] end of phase 1:\n' + _fmt(_cards(dash)), flush=True)

        phase(2, "'alarm vt_low on' on the ventilator of bed 1", 'DEMO-01 Rot (sdc = 0.8333), DEMO-02 Gelb')
        t0 = time.monotonic()
        send(provs['VENT-1'], 'alarm vt_low on')
        esc = dash.wait_for(lambda: _cards(dash).get('DEMO-01', {}).get('isEscalated'), 90, step=0.1)
        t_esc = time.monotonic() - t0
        print(f'[{ts()}] DEMO-01 escalated: {bool(esc)} after {t_esc:.1f} s\n' + _fmt(_cards(dash)), flush=True)
        print(f'[{ts()}] holding 25 s to confirm that DEMO-02 stays Gelb ...', flush=True)
        dash.pump(25.0)
        cards = _cards(dash)
        print(f'[{ts()}] after 25 s:\n' + _fmt(cards), flush=True)
        bed2_ok = bool(cards.get('DEMO-02', {}).get('hasActiveAlarms')) and not cards.get('DEMO-02', {}).get('isEscalated')
        rc = 0 if (esc and bed2_ok) else 2
        print(f'[{ts()}] RESULT: bed 1 Rot={bool(esc)} (t={t_esc:.1f} s), bed 2 Gelb only={bed2_ok}', flush=True)
        return rc
    finally:
        cleanup(provs)
        # the consumer threads are daemon-like but Qt may keep the process alive
        import os  # noqa: PLC0415
        os._exit(rc)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description='screenshot demo for Kapitel 3.2.5')
    ap.add_argument('--ip', required=True, help='local IPv4 address used by the providers and the dashboard')
    ap.add_argument('--auto', type=float, default=None, metavar='SEC',
                    help='advance the phases automatically after SEC seconds instead of Enter')
    ap.add_argument('--headless', action='store_true',
                    help='run phases 0-2 against a headless SdcMyConsumer and print the cards')
    ap.add_argument('--hold1', type=float, default=7.0, metavar='SEC',
                    help='headless: seconds between the monitor alarm and the ventilator alarm (default 7)')
    ap.add_argument('--echo', action='store_true', help='print every provider output line')
    a = ap.parse_args(argv)
    if a.headless:
        return run_headless(a.ip, a.hold1, a.echo)
    return run_interactive(a.ip, a.auto, a.echo)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
