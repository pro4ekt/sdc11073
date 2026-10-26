"""ziel2_ack.py -- remote acknowledgement experiment (success rate, RTT p95) for Ziel 2.

Headless dashboard (real SdcMyConsumer, room filter on) + ONE testbed ECG provider.  Per trial:
    provider: alarm <channel> on           -> EpisodicAlertReport, condition present
    dashboard: QtDeviceHandler.silenceAlarm()  (the real bell path: target selection, one
               SetAlertState per active signal, InvocationState evaluation, press_id;
               run synchronously here instead of in the bell's worker thread)
    wait for the provider's Presence=Ack report  (ack_confirmed)
    provider: alarm <channel> off
Two blocks: N trials with the provider policy ``ack accept`` (expected: accepted + confirmed)
and M trials with ``ack refuse`` (expected: failed, InvocationState Fail, nothing confirmed).

Journal (same file as the dashboard's ack_rtt / ack_confirmed records):
    ack_trial_start {trial, policy, epr, signal_handle}
    ack_trial_end   {trial, policy, epr, press_id, ack_state, outcomes, confirmed,
                     confirm_latency_ms, rtt_ms}

Usage (repo root, dashboard window closed):
    .venv\\Scripts\\python experiments\\ziel2\\ziel2_ack.py --ip <IP> --accept 300 --refuse 20
"""
from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import sys
import time
import uuid

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from harness import Dashboard, Prov, provider_cmd, setup_stdout, ts, DEVICES  # noqa: E402

LOG_DIR = HERE / 'logs'


def main(argv: list[str] | None = None) -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description='Ziel 2: acknowledgement success rate and RTT')
    ap.add_argument('--ip', help='adapter IPv4 (required unless --dry-run)')
    ap.add_argument('--room', default='ICU-1')
    ap.add_argument('--device', default='ecg', choices=sorted(DEVICES))
    ap.add_argument('--channel', default=None, help='alarm channel (default: first channel of the device)')
    ap.add_argument('--accept', type=int, default=300, help='trials with provider policy "ack accept"')
    ap.add_argument('--refuse', type=int, default=20, help='trials with provider policy "ack refuse"')
    ap.add_argument('--port', type=int, default=10600)
    ap.add_argument('--log', help='journal path (default experiments/ziel2/logs/ziel2_ack_<ts>.jsonl)')
    ap.add_argument('--confirm-timeout', type=float, default=5.0, help='seconds to wait for Presence=Ack')
    ap.add_argument('--settle', type=float, default=0.5, help='pause after alarm off')
    ap.add_argument('--tls', choices=('auto', 'force_tls', 'no_tls'), default='auto')
    ap.add_argument('--echo-provider', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)

    channel = a.channel or DEVICES[a.device][4]
    if not channel:
        ap.error(f'device {a.device} has no default alarm channel; pass --channel')
    log_path = pathlib.Path(a.log) if a.log else LOG_DIR / (
        f"ziel2_ack_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl")
    epr = f'urn:uuid:{uuid.uuid4()}'
    cmd = provider_cmd(a.device, epr, a.ip or '<IP>', a.port, room=a.room, ack='accept',
                       tls=a.tls, friendly='ziel2 ack')
    print(f'Ziel 2 ack runner: device={a.device} channel={channel} accept={a.accept} refuse={a.refuse} '
          f'journal={log_path}')
    if a.dry_run:
        print('  provider: ' + ' '.join(cmd[2:]))
        print(f'  trials: {a.accept} x [alarm on -> silenceAlarm -> ack_confirmed -> alarm off] with ack accept, '
              f'then {a.refuse} x with ack refuse')
        return 0
    if not a.ip:
        ap.error('--ip is required (adapter IPv4), or use --dry-run')

    dash = Dashboard(a.ip, a.room, log_path, tls=a.tls)
    from app.alarms.alarmCoordinator import alert_key_for  # after Dashboard() put Dashboard/ on sys.path
    dash.journal.emit('experiment_start', goal='ziel2_ack', device=a.device, channel=channel,
                      accept=a.accept, refuse=a.refuse, ip=a.ip, tls=a.tls, epr=epr)
    dash.pump(2)

    prov = Prov('ecg', cmd, a.echo_provider)
    results: list[dict] = []
    try:
        if not prov.wait_line('ready:', 40):
            print('provider did not become ready')
            return 2
        line = next((l for l in prov.lines if 'alarm' in l and 'ON' in l), None)  # noqa: F841
        handler = dash.wait_bound(epr, 90)
        if handler is None:
            print('device not bound within 90 s')
            return 2
        qt = handler.qtDeviceHandler
        qt._ack_synchronous = True
        # handles of the channel: from the provider's "ready" context we know the channel
        # name; the condition / signal handles follow the MDIB naming <dev>_alert_cond_<ch>
        cond_handle = f'{a.device}_alert_cond_{channel}'
        sig_handle = f'{a.device}_alert_signal_{channel}'
        cond_key = alert_key_for(epr, cond_handle)

        def presence() -> bool | None:
            snap = handler.snapshot_alert_presence()
            return None if snap is None else snap.get(cond_key)

        print(f'[{ts()}] bound: epr=..{epr[-12:]} handles {cond_handle} / {sig_handle}')
        dash.pump(2)

        trial = 0
        for policy, n in (('accept', a.accept), ('refuse', a.refuse)):
            if n <= 0:
                continue
            prov.send(f'ack {policy}')
            prov.wait_new_line('ack policy', 5)
            print(f'[{ts()}] block: policy={policy} n={n}')
            for _ in range(n):
                trial += 1
                prov.send(f'alarm {channel} on')
                if dash.wait_for(lambda: presence() is True, 10) is None:
                    print(f'  trial {trial}: condition did not become present')
                    dash.journal.emit('ack_trial_end', trial=trial, policy=policy, epr=epr,
                                      ack_state='no_alarm_report', press_id=None, outcomes=[],
                                      confirmed=False, confirm_latency_ms=None, rtt_ms=None)
                    prov.send(f'alarm {channel} off')
                    dash.pump(a.settle)
                    continue
                dash.pump(0.2)
                mark = dash.mark()
                dash.journal.emit('ack_trial_start', trial=trial, policy=policy, epr=epr,
                                  signal_handle=sig_handle)
                qt.silenceAlarm()                       # synchronous: transactions + ackState
                state, press, msg = qt.ackState, qt.ackPressId, qt.ackMessage
                rtts = [r for r in dash.events(epr=epr, event='ack_rtt', since=mark) if r.get('press_id') == press]
                outcomes = [(r.get('handle'), r.get('outcome'), r.get('invocation_state'), r.get('rtt_ms')) for r in rtts]
                rtt_ms = rtts[0]['rtt_ms'] if rtts else None
                # request time: the provider's Presence=Ack report can arrive BEFORE the
                # SetAlertState transaction is closed, so measure from the request, not
                # from the ack_rtt record (which is written at the end of the transaction)
                t_req = (rtts[0].get('t_request_abs') or rtts[0]['t_abs']) if rtts else time.time()
                confirmed = False
                conf_ms = None
                if state == 'accepted':
                    rec = dash.wait_for(
                        lambda: next((r for r in dash.events(epr=epr, event='ack_confirmed', since=mark)
                                      if r.get('handle') == sig_handle), None), a.confirm_timeout)
                    if rec:
                        confirmed = True
                        conf_ms = round((rec['t_abs'] - t_req) * 1000, 1)
                        dash.pump(0.1)
                        state = qt.ackState            # 'confirmed' after _applyAckConfirmed
                dash.journal.emit('ack_trial_end', trial=trial, policy=policy, epr=epr, press_id=press,
                                  ack_state=state, outcomes=outcomes, confirmed=confirmed,
                                  confirm_latency_ms=conf_ms, rtt_ms=rtt_ms, ack_message=msg)
                results.append({'trial': trial, 'policy': policy, 'state': state, 'rtt_ms': rtt_ms,
                                'confirmed': confirmed, 'confirm_ms': conf_ms})
                print(f'  trial {trial:4d} {policy:6s} -> {state:11s} rtt={rtt_ms} ms confirm={conf_ms} ms'
                      + (f'  [{msg}]' if state not in ('accepted', 'confirmed') else ''))
                prov.send(f'alarm {channel} off')
                dash.wait_for(lambda: presence() is False, 10)
                dash.pump(a.settle)
    except KeyboardInterrupt:
        print('interrupted')
    finally:
        dash.journal.emit('experiment_end', goal='ziel2_ack', trials_done=len(results))
        prov.shutdown()
        dash.pump(1)
        dash.stop()

    ok = sum(1 for r in results if r['policy'] == 'accept' and r['state'] in ('accepted', 'confirmed'))
    n_acc = sum(1 for r in results if r['policy'] == 'accept')
    fails = sum(1 for r in results if r['policy'] == 'refuse' and r['state'] == 'failed')
    n_ref = sum(1 for r in results if r['policy'] == 'refuse')
    rtts = sorted(r['rtt_ms'] for r in results if r['policy'] == 'accept' and r['rtt_ms'] is not None
                  and r['state'] in ('accepted', 'confirmed'))
    p95 = rtts[min(len(rtts) - 1, int(round(0.95 * len(rtts) + 0.5)) - 1)] if rtts else None
    print(f'\naccept block: {ok}/{n_acc} accepted, confirmed {sum(1 for r in results if r["confirmed"])}, '
          f'p95 RTT {p95} ms;  refuse block: {fails}/{n_ref} correctly failed')
    print(f'journal: {log_path}\nanalyze: {sys.executable} experiments/ziel2/ziel2_analyze.py "{log_path}"')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
