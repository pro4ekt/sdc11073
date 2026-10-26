"""pc1_listener.py -- PC 1 side of the two-PC night run (variant C): one headless dashboard window.

Started by ``campaign.py --listen`` once per window (``filtered``: room filter on; ``baseline``:
no room filter).  It does three things and nothing else:
  1. runs the real SdcMyConsumer headless with the JSON Lines journal (harness.Dashboard) --
     every discovery decision, subscription, ack_rtt, fallback, technical_alarm is written by
     the dashboard itself;
  2. acknowledges every alarm that appears on a bound device the way the bell does
     (QtDeviceHandler.silenceAlarm, synchronous, as in ziel2_ack.py), waits for the provider's
     Presence=Ack report and writes ``auto_ack`` to the journal;
  3. asks PC 2 for its clock every ``clock_poll_s`` seconds over UDP ({"t1"} -> {t2, t3, phase,
     window, state}) and writes ``clock_offset`` (offset = PC 2 - PC 1, NTP formula) plus
     ``pc2_phase`` on every change.  The reply also tells it when to stop: ``--until baseline``
     ends when PC 2 switches to the baseline window, ``--until done`` when the scenario is over.
Fallbacks: no reply from PC 2 for ``--idle-exit-min`` after at least one reply -> exit;
never a reply within ``--wait-start-min`` -> exit code 3; ``--max-hours`` hard stop.
Usage (repo root):
    .venv\\Scripts\\python experiments\\campaign_2pc\\pc1_listener.py --window filtered --until baseline
        --ip 192.168.0.106 --room ICU-1 --pc2 192.168.0.105 --clock-port 5558 --log <journal>
"""
from __future__ import annotations

import argparse
import json
import pathlib
import socket
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from harness import Dashboard, setup_stdout, ts  # noqa: E402


def udp_no_reset(sock: socket.socket) -> None:
    """Windows: a UDP socket raises ConnectionResetError on recv after an ICMP 'port unreachable'
    answered one of its earlier sends (e.g. the peer closed its socket); switch that off."""
    try:
        sock.ioctl(socket.SIO_UDP_CONNRESET, False)
    except (AttributeError, OSError):
        pass


class ClockPoller(threading.Thread):
    def __init__(self, pc2: str, port: int, poll_s: float, journal, on_state):
        super().__init__(daemon=True, name='clockpoll')
        self.addr = (pc2, port)
        self.poll_s = poll_s
        self.journal = journal
        self.on_state = on_state
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_no_reset(self.sock)
        self.sock.settimeout(2.0)
        self.stop = threading.Event()
        self.seq = 0
        self.replies = 0
        self.last_reply_mono: float | None = None
        self.last_state: dict = {}

    def exchange(self) -> dict | None:
        self.seq += 1
        t1 = time.time()
        try:
            self.sock.sendto(json.dumps({'t1': t1, 'seq': self.seq}).encode('utf-8'), self.addr)
            data, _ = self.sock.recvfrom(4096)
        except (socket.timeout, OSError):
            return None
        t4 = time.time()
        try:
            r = json.loads(data.decode('utf-8'))
        except Exception:
            return None
        if r.get('seq') != self.seq or r.get('t2') is None:
            return None
        t2, t3 = float(r['t2']), float(r['t3'])
        offset = ((t2 - t1) + (t3 - t4)) / 2.0           # PC 2 clock - PC 1 clock
        rtt = (t4 - t1) - (t3 - t2)
        rec = {'offset_s': round(offset, 6), 'rtt_s': round(rtt, 6), 't1': t1, 't4': t4,
               'phase': r.get('phase'), 'window': r.get('window'), 'state': r.get('state'),
               'trial': r.get('trial'), 'trials_done': r.get('trials_done'), 'scenario_id': r.get('scenario_id'),
               'pilot': r.get('pilot')}
        return rec

    def run(self) -> None:
        while not self.stop.is_set():
            rec = self.exchange()
            if rec is not None:
                self.replies += 1
                self.last_reply_mono = time.monotonic()
                self.journal.emit('clock_offset', **rec)
                st = {k: rec[k] for k in ('phase', 'window', 'state', 'scenario_id')}
                if st != self.last_state:
                    self.journal.emit('pc2_phase', **st, trial=rec['trial'])
                    self.last_state = st
                self.on_state(rec)
            self.stop.wait(self.poll_s)


def main(argv: list[str] | None = None) -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description='PC 1 listener (variant C)')
    ap.add_argument('--window', choices=('filtered', 'baseline'), required=True)
    ap.add_argument('--until', choices=('baseline', 'done'), required=True)
    ap.add_argument('--ip', required=True, help='adapter IPv4 of PC 1')
    ap.add_argument('--room', default='ICU-1', help='room filter for window filtered (ignored for baseline)')
    ap.add_argument('--pc2', required=True)
    ap.add_argument('--clock-port', type=int, default=5558)
    ap.add_argument('--clock-poll-s', type=float, default=10.0)
    ap.add_argument('--log', required=True, help='journal path (absolute)')
    ap.add_argument('--tls', choices=('auto', 'force_tls', 'no_tls'), default='auto')
    ap.add_argument('--confirm-timeout', type=float, default=5.0)
    ap.add_argument('--wait-start-min', type=float, default=60.0)
    ap.add_argument('--idle-exit-min', type=float, default=30.0)
    ap.add_argument('--max-hours', type=float, default=10.0)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)
    room = a.room if a.window == 'filtered' else None
    print(f'[{ts()}] listener window={a.window} room={room!r} ip={a.ip} pc2={a.pc2}:{a.clock_port} until={a.until} '
          f'journal={a.log}')
    if a.dry_run:
        return 0

    log_path = pathlib.Path(a.log).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    dash = Dashboard(a.ip, room, log_path, tls=a.tls)
    dash.journal.emit('listener_start', window=a.window, room=room, pc1_ip=a.ip, pc2_ip=a.pc2, until=a.until,
                      clock_port=a.clock_port, tls=a.tls)
    state: dict = {}
    stop_reason: list[str] = []

    def on_state(rec: dict) -> None:
        state.update(rec)
        if rec.get('state') == 'done':
            stop_reason.append('pc2 done')
        elif a.until == 'baseline' and rec.get('window') == 'baseline':
            stop_reason.append('pc2 switched to baseline window')

    poller = ClockPoller(a.pc2, a.clock_port, a.clock_poll_s, dash.journal, on_state)
    poller.start()
    dash.pump(2)

    armed: dict[str, bool] = {}            # epr -> an alarm is present and was already pressed
    presses = 0
    t_start = time.monotonic()
    last_report = time.monotonic()
    rc = 0
    try:
        while True:
            dash.pump(0.25)
            now = time.monotonic()
            # -- stop conditions ------------------------------------------------
            if stop_reason:
                print(f'[{ts()}] stop: {stop_reason[0]}')
                break
            if now - t_start > a.max_hours * 3600:
                stop_reason.append('max hours')
                break
            if poller.replies == 0 and now - t_start > a.wait_start_min * 60:
                stop_reason.append('PC 2 never answered')
                rc = 3
                break
            if poller.replies and poller.last_reply_mono and now - poller.last_reply_mono > a.idle_exit_min * 60:
                stop_reason.append(f'no reply from PC 2 for {a.idle_exit_min:.0f} min')
                break
            # -- auto acknowledgement ------------------------------------------------
            with dash.manager.lock:
                handlers = list(dash.manager.devices.items())
            for epr, h in handlers:
                qt = getattr(h, 'qtDeviceHandler', None)
                if not getattr(h, '_ui_connected', False) or qt is None:
                    continue
                snap = h.snapshot_alert_presence()
                if snap is None:
                    continue
                present = sorted(k for k, v in snap.items() if v)
                if not present:
                    armed.pop(epr, None)
                    continue
                if armed.get(epr):
                    continue
                armed[epr] = True
                t_seen = time.time()
                qt._ack_synchronous = True
                mark = dash.mark()
                qt.silenceAlarm()
                ack_state, press, msg = qt.ackState, qt.ackPressId, qt.ackMessage
                rtts = [r for r in dash.events(epr=epr, event='ack_rtt', since=mark) if r.get('press_id') == press]
                outcomes = [(r.get('handle'), r.get('outcome'), r.get('invocation_state'), r.get('rtt_ms')) for r in rtts]
                rtt_ms = rtts[0]['rtt_ms'] if rtts else None
                t_req = (rtts[0].get('t_request_abs') or rtts[0]['t_abs']) if rtts else t_seen
                confirmed, conf_ms = False, None
                if ack_state == 'accepted':
                    handles = {r.get('handle') for r in rtts}
                    rec = dash.wait_for(lambda: next((r for r in dash.events(epr=epr, event='ack_confirmed', since=mark)
                                                      if not handles or r.get('handle') in handles), None),
                                        a.confirm_timeout)
                    if rec:
                        confirmed = True
                        conf_ms = round((rec['t_abs'] - t_req) * 1000, 1)
                        dash.pump(0.1)
                        ack_state = qt.ackState
                presses += 1
                dash.journal.emit('auto_ack', epr=epr, cond_keys=present, press_id=press, ack_state=ack_state,
                                  outcomes=outcomes, confirmed=confirmed, confirm_latency_ms=conf_ms, rtt_ms=rtt_ms,
                                  t_seen_abs=t_seen, ack_message=msg, press_no=presses)
                print(f'[{ts()}] auto-ack #{presses} ..{epr[-12:]} -> {ack_state} rtt={rtt_ms} ms confirm={conf_ms} ms')
            # -- periodic status line ----------------------------------------------------
            if now - last_report > 60:
                last_report = now
                print(f'[{ts()}] alive: pc2 phase={state.get("phase")} state={state.get("state")} trial={state.get("trial")} '
                      f'offset={state.get("offset_s")} s rtt={state.get("rtt_s")} s replies={poller.replies} '
                      f'devices={len(handlers)} presses={presses}')
    except KeyboardInterrupt:
        stop_reason.append('Ctrl-C')
        rc = 130
    finally:
        poller.stop.set()
        dash.journal.emit('listener_end', window=a.window, reason=stop_reason[0] if stop_reason else 'unknown',
                          presses=presses, clock_replies=poller.replies, last_pc2_state=state)
        dash.stop()
    print(f'[{ts()}] listener {a.window} ended: {stop_reason[0] if stop_reason else "?"}; presses={presses}')
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
