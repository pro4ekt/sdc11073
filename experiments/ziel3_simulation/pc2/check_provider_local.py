"""check_provider_local.py -- exercise ONE provider through the pool wrapper, locally (default 127.0.0.1).

Not a run: starts SIM-P01 ECG on the given address/port, waits for ``ready:``, sends an alarm ON
with a value, a status request, an alarm OFF, another status, then ``shutdown``; prints what the
wrapper parsed and whether it all went through.  Journal B of the check goes to --log-dir.

    python check_provider_local.py --log-dir <folder> [--ip 127.0.0.1] [--port 13000]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for p in (HERE, ROOT / 'experiments', ROOT / 'experiments' / 'campaign_2pc'):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from provider_pool import ProviderProc, make_specs  # noqa: E402
from pc2_scheduler import Journal  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--log-dir', type=pathlib.Path, required=True)
    ap.add_argument('--ip', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=13000)
    ap.add_argument('--python', default=sys.executable)
    ap.add_argument('--ready-timeout', type=float, default=40.0)
    a = ap.parse_args(argv)

    a.log_dir.mkdir(parents=True, exist_ok=True)
    journal = Journal(a.log_dir / 'alarms_check.jsonl')
    spec = make_specs('local_check', 1, 1)[0]                   # SIM-P01 ECG
    spec = type(spec)(**{**spec.__dict__, 'port': a.port})
    print(f'provider: {spec.friendly}  {spec.source.model}  port {spec.port}  ip {a.ip}')
    print('argv: ' + ' '.join(spec.argv(a.python, a.ip)))
    proc = ProviderProc(spec, a.python, a.ip, journal)
    t0 = time.time()
    proc.start()
    ok_ready = proc.ready.wait(a.ready_timeout)
    print(f'ready: {ok_ready} after {time.time() - t0:.1f} s' if ok_ready else
          f'NOT READY after {a.ready_timeout:.0f} s; last lines:\n  ' + '\n  '.join(list(proc.lines)[-10:]))
    if not ok_ready:
        proc.kill()
        journal.close()
        return 1
    proc.set_alarm(True, '131')
    time.sleep(2.0)
    proc.request_status()
    time.sleep(1.5)
    proc.set_alarm(False, '96')
    time.sleep(2.0)
    proc.request_status()
    time.sleep(1.5)
    proc.shutdown(10.0)
    time.sleep(0.5)
    print(f'exited: {proc.exited.is_set()}  returncode: {proc.returncode}  alive: {proc.alive()}')
    journal.close()

    recs = [json.loads(line) for line in (a.log_dir / 'alarms_check.jsonl').read_text(encoding='utf-8').splitlines()]
    counts: dict[str, int] = {}
    for r in recs:
        counts[r['event']] = counts.get(r['event'], 0) + 1
    print('journal B events: ' + ', '.join(f'{k}={v}' for k, v in sorted(counts.items())))
    for r in recs:
        if r['event'] == 'alarm':
            print(f"  alarm {'ON ' if r['on'] else 'OFF'} value={r['value']} t_prov_ms={r['t_prov_ms']} "
                  f"latency={r['latency_ms']} ms  (iso {r['iso']})")
        elif r['event'] == 'status':
            print(f"  status subscriptions={r['subscriptions']} selfcheck={r['selfcheck']} ack={r['ack']}")
        elif r['event'] in ('provider_msg', 'alarm_failed', 'alarm_dropped'):
            print(f"  {r['event']}: {r.get('text') or r}")
    alarms = [r for r in recs if r['event'] == 'alarm']
    statuses = [r for r in recs if r['event'] == 'status']
    verdict = (ok_ready and len(alarms) == 2 and alarms[0]['on'] and not alarms[1]['on']
               and all(r['t_prov_ms'] for r in alarms) and len(statuses) == 2
               and proc.returncode == 0 and not proc.alive())
    print('LOCAL PROVIDER CHECK: ' + ('OK' if verdict else 'PROBLEM'))
    print('last provider lines:')
    for line in list(proc.lines)[-6:]:
        print('  ' + line[:160])
    return 0 if verdict else 1


if __name__ == '__main__':
    sys.exit(main())
