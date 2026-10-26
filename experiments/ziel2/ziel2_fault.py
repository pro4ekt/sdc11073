"""ziel2_fault.py -- fault injection for Ziel 2 (fallback entry within the upper bound).

Headless dashboard (real SdcMyConsumer) + one fresh testbed ECG provider per trial.  When the
device is bound and shown, the connection is broken with one of:

    shutdown   provider 'shutdown' on stdin        -> SubscriptionEnd + Bye   (bound  ~10 s)
    kill       TerminateProcess                    -> TCP RST on the next ping (bound  ~15 s)
    suspend    process frozen (NtSuspendProcess)   -> silent loss on ONE host: TCP stays
               open, nothing answers, every ping waits the socket timeout   (bound  30 s)
    reconnect  kill, then the SAME EPR comes back  -> technical alarm cleared (informational)

t_loss is stamped in the journal right before the injection (``fault_injected``), the
dashboard writes ``fallback`` and ``technical_alarm{raised}`` itself; ``fault_trial_end``
collects the deltas.  A two-host firewall drop is not scripted here (loopback cannot be
blocked); ``suspend`` is the one-host equivalent of a silent packet loss.

Usage (repo root, dashboard window closed):
    .venv\\Scripts\\python experiments\\ziel2\\ziel2_fault.py --ip <IP> --methods shutdown=30,kill=30,suspend=30,reconnect=5
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
BOUNDS = {'shutdown': 10.0, 'kill': 15.0, 'suspend': 30.0, 'reconnect': None}
METHODS = tuple(BOUNDS)


def parse_methods(spec: str) -> list[tuple[str, int]]:
    out = []
    for part in spec.split(','):
        part = part.strip()
        if not part:
            continue
        m, _, n = part.partition('=')
        if m not in METHODS:
            raise SystemExit(f'unknown method {m!r}; methods: {", ".join(METHODS)}')
        out.append((m, int(n or 1)))
    return out


def make_plan(counts: list[tuple[str, int]], order: str) -> list[str]:
    if order == 'grouped':
        return [m for m, n in counts for _ in range(n)]
    rem = dict(counts)
    plan = []
    while any(rem.values()):
        for m in list(rem):
            if rem[m] > 0:
                plan.append(m)
                rem[m] -= 1
    return plan


def main(argv: list[str] | None = None) -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description='Ziel 2: fault injection, fallback entry time')
    ap.add_argument('--ip', help='adapter IPv4 (required unless --dry-run)')
    ap.add_argument('--room', default='ICU-1')
    ap.add_argument('--device', default='ecg', choices=sorted(DEVICES))
    ap.add_argument('--methods', default='shutdown=30,kill=30,suspend=30,reconnect=5')
    ap.add_argument('--order', choices=('interleave', 'grouped'), default='interleave')
    ap.add_argument('--port-base', type=int, default=10700)
    ap.add_argument('--log', help='journal path (default experiments/ziel2/logs/ziel2_fault_<ts>.jsonl)')
    ap.add_argument('--fallback-timeout', type=float, default=90.0, help='max wait for the fallback event')
    ap.add_argument('--reconnect-timeout', type=float, default=120.0)
    ap.add_argument('--settle', type=float, default=3.0, help='seconds the device is shown before the injection')
    ap.add_argument('--tls', choices=('auto', 'force_tls', 'no_tls'), default='auto')
    ap.add_argument('--echo-provider', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)

    counts = parse_methods(a.methods)
    plan = make_plan(counts, a.order)
    log_path = pathlib.Path(a.log) if a.log else LOG_DIR / (
        f"ziel2_fault_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl")
    print(f'Ziel 2 fault runner: methods={dict(counts)} order={a.order} bounds={BOUNDS} journal={log_path}')
    if a.dry_run:
        for i, m in enumerate(plan, 1):
            print(f'  trial {i:3d} {m:9s} port {a.port_base + i - 1}'
                  + ('  (+ restart with the same EPR on port +1000)' if m == 'reconnect' else ''))
        return 0
    if not a.ip:
        ap.error('--ip is required (adapter IPv4), or use --dry-run')

    dash = Dashboard(a.ip, a.room, log_path, tls=a.tls)
    dash.journal.emit('experiment_start', goal='ziel2_fault', methods=dict(counts), order=a.order,
                      bounds=BOUNDS, ip=a.ip, tls=a.tls)
    dash.pump(2)

    results: list[dict] = []
    try:
        for i, method in enumerate(plan, 1):
            epr = f'urn:uuid:{uuid.uuid4()}'
            port = a.port_base + i - 1
            prov = Prov(f'{method}#{i}', provider_cmd(a.device, epr, a.ip, port, room=a.room, tls=a.tls,
                                                     friendly=f'ziel2 {method}'), a.echo_provider)
            print(f'[{ts()}] trial {i}/{len(plan)} {method:9s} epr=..{epr[-12:]} port={port}')
            rec = {'trial': i, 'method': method, 'epr': epr, 'outcome': 'provider_failed'}
            try:
                if not prov.wait_line('ready:', 40):
                    print('    provider did not become ready')
                    continue
                if dash.wait_bound(epr, 90) is None:
                    rec['outcome'] = 'not_bound'
                    print('    device not bound within 90 s')
                    continue
                dash.pump(a.settle)
                mark = dash.mark()
                t_loss = time.time()
                dash.journal.emit('fault_injected', trial=i, method=method, epr=epr, t_loss=t_loss,
                                  pid=prov.pid, bound_s=BOUNDS[method])
                if method == 'shutdown':
                    prov.send('shutdown')
                elif method in ('kill', 'reconnect'):
                    prov.kill()
                elif method == 'suspend':
                    if not prov.suspend():
                        rec['outcome'] = 'suspend_failed'
                        print('    NtSuspendProcess failed')
                        prov.kill()
                        continue
                fb = dash.wait_for(lambda: next(iter(dash.events(epr=epr, event='fallback', since=mark)), None),
                                   a.fallback_timeout)
                al = dash.wait_for(lambda: next((r for r in dash.events(epr=epr, event='technical_alarm', since=mark)
                                                 if r.get('action') == 'raised'), None), 15)
                dp = dash.wait_for(lambda: next(iter(dash.events(epr=epr, event='device_departed', since=mark)), None), 20)
                rec.update({
                    't_loss': t_loss,
                    'cause': fb.get('cause') if fb else None,
                    'end_status': fb.get('end_status') if fb else None,
                    'dt_fallback_s': round(fb['t_abs'] - t_loss, 3) if fb else None,
                    'dt_alarm_s': round(al['t_abs'] - t_loss, 3) if al else None,
                    'dt_departed_s': round(dp['t_abs'] - t_loss, 3) if dp else None,
                    'escalated_at_loss': al.get('escalated_at_loss') if al else None,
                    'bound_s': BOUNDS[method],
                    'outcome': 'ok' if (fb and al) else ('no_fallback' if not fb else 'no_alarm'),
                })
                if method == 'suspend':
                    prov.resume()
                    prov.kill()
                print(f"    -> {rec['outcome']}: cause={rec['cause']} fallback={rec['dt_fallback_s']} s "
                      f"alarm={rec['dt_alarm_s']} s departed={rec['dt_departed_s']} s (bound {BOUNDS[method]})")

                if method == 'reconnect' and fb:
                    port2 = port + 1000
                    t_restart = time.time()
                    dash.journal.emit('reconnect_started', trial=i, epr=epr, t_restart=t_restart, port=port2)
                    prov2 = Prov(f'reconnect#{i}b', provider_cmd(a.device, epr, a.ip, port2, room=a.room, tls=a.tls,
                                                                  friendly='ziel2 reconnect'), a.echo_provider)
                    try:
                        prov2.wait_line('ready:', 40)
                        cl = dash.wait_for(lambda: next((r for r in dash.events(epr=epr, event='technical_alarm', since=mark)
                                                         if r.get('action') == 'cleared'), None), a.reconnect_timeout)
                        bound = dash.wait_bound(epr, 30) if cl else None
                        rec['dt_reconnect_s'] = round(cl['t_abs'] - t_restart, 3) if cl else None
                        rec['reconnect_ok'] = bool(cl and bound)
                        print(f"    -> reconnect: cleared after {rec['dt_reconnect_s']} s, bound={bool(bound)}")
                    finally:
                        prov2.shutdown()
                        dash.wait_for(lambda: dash.events(epr=epr, event='device_departed', since=mark + 1), 30)
            finally:
                dash.journal.emit('fault_trial_end', **rec)
                results.append(rec)
                if prov.alive():
                    prov.kill()
                dash.pump(1.0)
    except KeyboardInterrupt:
        print('interrupted')
    finally:
        dash.journal.emit('experiment_end', goal='ziel2_fault', trials_done=len(results))
        dash.stop()

    print('\nSummary:')
    for m, _n in counts:
        rs = [r for r in results if r['method'] == m and r.get('dt_fallback_s') is not None]
        if not rs:
            print(f'  {m:9s} no data')
            continue
        fbs = [r['dt_fallback_s'] for r in rs]
        als = [r['dt_alarm_s'] for r in rs if r.get('dt_alarm_s') is not None]
        b = BOUNDS[m]
        over = sum(1 for r in rs if b is not None and r.get('dt_alarm_s') is not None and r['dt_alarm_s'] > b)
        print(f'  {m:9s} n={len(rs)} fallback max {max(fbs):.1f} s, alarm max {max(als) if als else float("nan"):.1f} s'
              + (f', over bound {b}: {over}' if b is not None else ''))
    print(f'journal: {log_path}\nanalyze: {sys.executable} experiments/ziel2/ziel2_analyze.py "{log_path}"')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
