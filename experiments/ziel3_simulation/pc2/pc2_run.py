r"""pc2_run.py -- Ziel 3 runner of PC 2 ("the ward"): one script, one console.

    python experiments\ziel3_simulation\pc2\pc2_run.py --name ziel3_1h --patients 20 --hours 1 --seed 1 --ip 192.168.0.100

Sequence (contract with PC 1):
  1. clock responder on UDP 5558 (state=waiting), wait for the FIRST clock request of PC 1;
  2. start N x 4 providers with a gap of >= 3 s (80 providers ~ 4 min), wait for every ``ready:``;
  3. state=running: patients and Szenario tick once per second, alarm transitions go to the providers;
  4. after --hours: state=done, all alarms off, >= 20 s (default 60 s) later ``shutdown`` to all providers,
     keep answering the clock for 10 more minutes, exit.
Journals (JSON Lines, t_abs = clock of PC 2) in experiments/ziel3_simulation/logs/<name>/pc2/:
  truth.jsonl (A: crises, movements), alarms.jsonl (B: provider events), clock.jsonl, console.txt,
  run_config.json.  Ctrl-C: alarms off, shutdown, short linger, exit code 130.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import platform
import socket
import subprocess
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]                                   # pc2_c
for p in (HERE, ROOT / 'experiments', ROOT / 'experiments' / 'campaign_2pc'):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from patient import (AlarmEvent, MovementEvent, Patient, PatientParams, ROOM, MANUFACTURER, SOURCES,  # noqa: E402
                     make_seed_sequences)
from szenario import CrisisParams, Szenario  # noqa: E402
from alarmebene import AlarmLevelParams, make_patients, make_szenario  # noqa: E402
from provider_pool import BASE_PORT, ProviderPool  # noqa: E402
from pc2_scheduler import ClockResponder, Journal  # noqa: E402   (Ziel 1/2 clock protocol and journal format)

CLOCK_PORT = 5558
LOGS = ROOT / 'experiments' / 'ziel3_simulation' / 'logs'
MIN_START_GAP_S = 3.0            # >= 3 s between provider starts: the Dashboard rejected a device whose three
                                 # siblings appeared within one second (context check timeout, run ziel3_1h)
MIN_OFF_TO_SHUTDOWN_S = 20.0     # >= 20 s between state=done (alarms off) and the shutdown of the providers


def effective_timings(start_gap: float, off_to_shutdown: float, aborted: bool = False) -> tuple[float, float]:
    """Clamp the CLI timings to the minimums above. After Ctrl-C the pause before shutdown is the minimum."""
    gap = max(float(start_gap), MIN_START_GAP_S)
    pause = MIN_OFF_TO_SHUTDOWN_S if aborted else max(float(off_to_shutdown), MIN_OFF_TO_SHUTDOWN_S)
    return gap, pause


def ts() -> str:
    return _dt.datetime.now().strftime('%H:%M:%S')


def log(msg: str) -> None:
    print(f'[{ts()}] {msg}', flush=True)


class Tee:
    def __init__(self, path: pathlib.Path, orig):
        self._fh = path.open('a', encoding='utf-8')
        self._orig = orig

    def write(self, s: str) -> None:
        self._fh.write(s)
        self._orig.write(s)

    def flush(self) -> None:
        self._fh.flush()
        self._orig.flush()


def detect_ip() -> str:
    """Local IPv4 of the adapter that would route to the LAN (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('192.168.0.1', 9))
        return s.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        s.close()


def git_head() -> str | None:
    try:
        return subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True, timeout=10,
                              cwd=str(ROOT)).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--name', required=True, help='run name -> logs/<name>/pc2/')
    ap.add_argument('--patients', type=int, default=20)
    ap.add_argument('--hours', type=float, required=True, help='duration of the running phase')
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--p-cross', type=float, default=CrisisParams().p_cross,
                    help='probability that a source is pushed beyond its threshold in a crisis (default %(default)s)')
    ap.add_argument('--crisis-min', type=float, default=CrisisParams().dur_min_s,
                    help='minimum crisis duration [s] (default %(default)s)')
    ap.add_argument('--crisis-extra-mean', type=float, default=CrisisParams().dur_extra_mean_s,
                    help='mean of the exponential part of the crisis duration [s] (default %(default)s)')
    ap.add_argument('--generator', choices=('prozess', 'alarmebene'), default='prozess',
                    help='prozess = random value processes with thresholds (default); alarmebene = alarms drawn as the load model assumes')
    ap.add_argument('--fpr', type=float, default=AlarmLevelParams().fpr, help='alarmebene: false-alarm time share per channel (default %(default)s)')
    ap.add_argument('--c-art', type=float, default=AlarmLevelParams().c_art, help='alarmebene: share of common Stoesse (default %(default)s)')
    ap.add_argument('--d-false', type=float, default=AlarmLevelParams().d_false_s, help='alarmebene: mean false-alarm length [s] (default %(default)s)')
    ap.add_argument('--threshold-scale', type=float, default=PatientParams().threshold_scale,
                    help='prozess: scale h of the alarm thresholds of all four sources, alarm at x >= h (default %(default)s = contract)')
    ap.add_argument('--ip', help='IPv4 of PC 2 for the providers (default: auto-detected)')
    ap.add_argument('--start-gap', type=float, default=MIN_START_GAP_S,
                    help=f'seconds between provider starts (minimum {MIN_START_GAP_S:g})')
    ap.add_argument('--ready-timeout', type=float, default=40.0)
    ap.add_argument('--wait-pc1-min', type=float, default=30.0, help='wait for the first clock request this long (0 = do not wait)')
    ap.add_argument('--status-every', type=float, default=60.0, help='status poll period per provider [s]')
    ap.add_argument('--off-to-shutdown', type=float, default=60.0,
                    help=f'seconds between state=done (alarms off) and shutdown (minimum {MIN_OFF_TO_SHUTDOWN_S:g})')
    ap.add_argument('--linger', type=float, default=600.0, help='keep answering the clock after done [s]')
    ap.add_argument('--log-dir', type=pathlib.Path, help='override logs/<name>/pc2')
    ap.add_argument('--python', default=sys.executable)
    ap.add_argument('--dry-run', action='store_true', help='print the plan (ports, EPRs) and exit; nothing is started')
    a = ap.parse_args(argv)

    ip = a.ip or detect_ip()
    run_dir = (a.log_dir or LOGS / a.name / 'pc2').resolve()
    patients_n = a.patients
    seqs, sseq = make_seed_sequences(a.seed, patients_n)
    if not 0.0 <= a.p_cross <= 1.0:
        ap.error('--p-cross must be between 0 and 1')
    if a.crisis_min < 0 or a.crisis_extra_mean <= 0:
        ap.error('--crisis-min must be >= 0 and --crisis-extra-mean > 0')
    if not a.threshold_scale > 0:
        ap.error('--threshold-scale must be > 0')
    pparams = PatientParams(threshold_scale=a.threshold_scale)
    cparams = CrisisParams(p_cross=a.p_cross, dur_min_s=a.crisis_min, dur_extra_mean_s=a.crisis_extra_mean)
    aparams = AlarmLevelParams(fpr=a.fpr, c_art=a.c_art, d_false_s=a.d_false)
    start_gap, off_to_shutdown = effective_timings(a.start_gap, a.off_to_shutdown)
    if start_gap != a.start_gap or off_to_shutdown != a.off_to_shutdown:
        print(f'note: timings raised to the minimums: start gap {start_gap:g} s, done->shutdown {off_to_shutdown:g} s')

    plan_pool = ProviderPool(a.name, patients_n, ip, journal=None, run_seed=a.seed, python=a.python,
                             start_gap_s=start_gap, ready_timeout_s=a.ready_timeout, status_every_s=a.status_every, log=log)
    config = {
        'name': a.name, 'seed': a.seed, 'patients': patients_n, 'hours': a.hours, 'p_cross': a.p_cross,
        'generator': a.generator, 'crisis_dur_min_s': a.crisis_min, 'crisis_dur_extra_mean_s': a.crisis_extra_mean,
        'threshold_scale': a.threshold_scale,
        'alarmlevel_params': aparams.to_dict() if a.generator == 'alarmebene' else None, 'ip': ip, 'clock_port': CLOCK_PORT,
        'room': ROOM, 'manufacturer': MANUFACTURER, 'tls': 'no_tls', 'base_port': BASE_PORT,
        'ports': f'{BASE_PORT} + 4*(i-1) + (j-1)', 'start_gap_s': start_gap, 'ready_timeout_s': a.ready_timeout,
        'status_every_s': a.status_every, 'off_to_shutdown_s': off_to_shutdown, 'linger_s': a.linger,
        'sources': [{'j': s.j, 'key': s.key, 'model': s.model, 'mdib': s.mdib, 'channel': s.channel, 'loinc': s.loinc,
                     'priority': s.priority, 'unit': s.unit, 'base': s.base, 'on': s.on, 'off': s.off,
                     'direction': s.direction, 'decimals': s.decimals} for s in SOURCES],
        'providers': plan_pool.plan(), 'patient_params': pparams.to_dict(), 'crisis_params': cparams.to_dict(),
        'python': platform.python_version(), 'host': socket.gethostname(), 'git_head': git_head(),
        'run_dir': str(run_dir), 'created': _dt.datetime.now().isoformat(timespec='seconds'),
    }
    try:
        import importlib.metadata as _m
        config['sdc11073'] = _m.version('sdc11073')
    except Exception:  # noqa: BLE001
        pass

    if a.dry_run:
        print(json.dumps({k: v for k, v in config.items() if k != 'providers'}, indent=2, ensure_ascii=False))
        for pr in config['providers']:
            print(f"  {pr['friendly_name']:14s} port {pr['port']}  {pr['model']:9s} {pr['channel']:13s} {pr['epr']}")
        print(f'(dry run: nothing started; {len(config["providers"])} providers planned, log dir {run_dir})')
        return 0

    run_dir.mkdir(parents=True, exist_ok=True)
    sys.stdout = Tee(run_dir / 'console.txt', sys.stdout)
    (run_dir / 'run_config.json').write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding='utf-8')
    truth = Journal(run_dir / 'truth.jsonl')
    alarms = Journal(run_dir / 'alarms.jsonl')
    clockj = Journal(run_dir / 'clock.jsonl')
    for j in (truth, alarms, clockj):
        j.emit('run_config', **config)
    log(f'run {a.name}: {patients_n} patients x 4 providers, {a.hours:g} h, seed {a.seed}, generator {a.generator}, p_cross {a.p_cross:g}, '
        f'crisis {a.crisis_min:g} s + Exp({a.crisis_extra_mean:g} s), ip {ip}, dir {run_dir}')

    state = {'scenario_id': a.name, 'state': 'waiting', 'phase': 'init', 'patients': patients_n, 'hours': a.hours,
             't_start_abs': None, 't_model': 0.0, 'providers_ready': 0}
    responder = ClockResponder(ip, CLOCK_PORT, state, clockj)
    responder.start()
    stop = threading.Event()
    pool = ProviderPool(a.name, patients_n, ip, alarms, run_seed=a.seed, python=a.python, start_gap_s=start_gap,
                        ready_timeout_s=a.ready_timeout, status_every_s=a.status_every, log=log)
    patients = make_patients(a.generator, seqs, pparams, aparams)
    aborted = False
    exit_code = 0
    t_run_start = None
    try:
        # 1. wait for PC 1
        if a.wait_pc1_min > 0:
            log(f'waiting for the first clock request of PC 1 on UDP {CLOCK_PORT} (up to {a.wait_pc1_min:g} min) ...')
            end = time.monotonic() + a.wait_pc1_min * 60.0
            while responder.requests == 0 and time.monotonic() < end:
                time.sleep(0.5)
            if responder.requests == 0:
                log('PC 1 did not ask for the clock - starting anyway (PC 1 needs at least one exchange later)')
            else:
                log(f'PC 1 is listening (first request from {responder.last_peer})')
            alarms.emit('pc1_seen', requests=responder.requests, peer=responder.last_peer)
        # 2. providers
        state.update(phase='starting_providers', state='waiting')
        log(f'starting {len(pool.specs)} providers on {ip}, ports {BASE_PORT}..{BASE_PORT + 4 * patients_n - 1}, gap {start_gap:g} s ...')
        t0 = time.time()
        failed = pool.start_all(stop)
        state['providers_ready'] = pool.count_ready()
        log(f'{pool.count_ready()}/{len(pool.specs)} providers ready after {time.time() - t0:.0f} s')
        if failed:
            log(f'ABORT: providers never became ready: {", ".join(failed)}')
            alarms.emit('run_abort', reason='providers not ready', failed=failed)
            aborted = True
            exit_code = 1
            return exit_code
        # 3. run
        t_run_start = time.time()
        state.update(phase='running', state='running', t_start_abs=t_run_start)
        sz = make_szenario(a.generator, patients, sseq, truth, cparams, t0_abs=t_run_start)
        alarms.emit('run_start', t_start_abs=t_run_start, providers=len(pool.specs))
        truth.emit('run_start', t_start_abs=t_run_start)
        log(f'running for {a.hours:g} h ({patients_n} patients). Ctrl-C aborts gracefully.')
        total_steps = int(round(a.hours * 3600.0))
        counts = {s.key: 0 for s in SOURCES}
        late_max = 0.0
        next_report = t_run_start + 60.0
        for k in range(total_steps):
            due = t_run_start + k
            now = time.time()
            if due > now:
                time.sleep(due - now)
            else:
                late_max = max(late_max, now - due)
            t = float(k)
            now = time.time()
            for pt in patients:
                for ev in pt.step(t):
                    if isinstance(ev, AlarmEvent):
                        pool.set_alarm(pt.index, ev.j, ev.on, ev.value)
                        if ev.on:
                            counts[SOURCES[ev.j - 1].key] += 1
                    elif isinstance(ev, MovementEvent):
                        truth.emit('movement', patient=pt.patient_id, patient_index=pt.index, t_model=t,
                                   duration_s=round(ev.duration_s, 2), gains=[round(g, 3) for g in ev.gains])
            sz.step(t)
            pool.tick(now)
            state['t_model'] = t
            if now >= next_report:
                next_report += 60.0
                subs = pool.subscriptions_total()
                log(f'{k / 3600.0:5.2f} h  alarms on: ' + ' '.join(f'{key}={n}' for key, n in counts.items())
                    + f'  crises done={len(sz.crises_done)} active={sum(1 for c in sz.active.values() if not c.ended)}'
                    + f'  providers alive={pool.count_alive()}/{len(pool.specs)} subscriptions={subs if subs is not None else "?"}'
                    + f'  clock requests={responder.requests}  max tick delay={late_max:.2f} s')
        log('running phase finished')
    except KeyboardInterrupt:
        aborted = True
        exit_code = 130
        log('Ctrl-C: aborting')
        stop.set()
    finally:
        # 4. finish
        state.update(phase='finishing', state='done')
        try:
            n_off = pool.all_alarms_off()
            log(f'state=done; {n_off} alarm(s) switched off')
            alarms.emit('run_end', aborted=aborted, t_end_abs=time.time(), alarms_switched_off=n_off)
            truth.emit('run_end', aborted=aborted, t_end_abs=time.time())
            wait_s = effective_timings(a.start_gap, a.off_to_shutdown, aborted)[1]
            log(f'shutdown of the providers in {wait_s:g} s ...')
            time.sleep(wait_s)
            res = pool.shutdown_all(timeout=20.0)
            log(f'providers stopped ({res["killed"]} had to be killed)')
        except Exception as exc:  # noqa: BLE001
            log(f'error during shutdown: {exc!r}')
            pool.kill_all()
        linger = 60.0 if aborted else a.linger
        log(f'clock responder reports state=done for {linger:g} s more (Ctrl-C to quit) ...')
        try:
            time.sleep(linger)
        except KeyboardInterrupt:
            pass
        responder.close()
        for j in (truth, alarms, clockj):
            j.close()
        log(f'journals: {run_dir}')
        print(f'copy the whole folder {run_dir.parent} to PC 1 (truth.jsonl, alarms.jsonl, clock.jsonl, run_config.json, console.txt)')
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
