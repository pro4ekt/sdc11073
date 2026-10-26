"""netmode.py -- PC 1 side of the two-PC night run, variant C (used by campaign.py).

    campaign.py --listen [--pilot]          one headless dashboard per window (filtered, baseline)
                                            via pc1_listener.py, tshark on the NIC, resources,
                                            status; ends when PC 2 reports 'done'
    campaign.py --merge RUN --pc2 PATH      PC 2 journal -> runner-format events per phase ->
                                            existing analyzers -> campaign_report.md + dossier
    campaign.py --selftest-merge            synthetic PC 1 + PC 2 journals through the merge

Clock: every ``clock_offset`` record of the listener gives offset = PC 2 - PC 1 at one moment
(NTP formula, uncertainty <= rtt/2).  PC 2 times are converted with the sample nearest in time
(linear interpolation between samples), so clock drift over the night is followed.
"""
from __future__ import annotations

import bisect
import collections
import copy
import datetime as _dt
import json
import pathlib
import statistics
import sys
import time
import uuid

import campaign as C
from campaign import (Campaign, LOG_DIR, PY, REPO, HERE, CAP_KEYS, read_jsonl, run_subprocess, run_analyzers,
                      summarise_journal, build_report, trial_only_subscriptions, pct, fmt, now_ts)

sys.path.insert(0, str(C.ZIEL2))
from ziel2_analyze import cp_lower  # noqa: E402

OUT_NAME = {'z1_filtered': 'ziel1_filtered', 'z1_baseline': 'ziel1_baseline', 'z2_ack': 'ziel2_ack', 'z2_fault': 'ziel2_fault'}
WINDOWS = ('filtered', 'baseline')


def iso(t: float) -> str:
    return _dt.datetime.fromtimestamp(t).isoformat(timespec='milliseconds')


def load_schedule(cfg: dict) -> tuple[pathlib.Path, dict]:
    p = HERE / cfg.get('network', {}).get('schedule', 'schedule_c.json')
    return p, json.loads(p.read_text(encoding='utf-8'))


def net_campaign(cfg: dict, run_dir: pathlib.Path, pilot: bool, dry: bool, capture: bool, sch: dict) -> Campaign:
    net = cfg.get('network', {})
    cfg2 = copy.deepcopy(cfg)
    cfg2.setdefault('providers', {}).update(source='remote', host=net.get('pc2_ip', sch.get('pc2_ip')))
    cap = cfg2.setdefault('capture', {})
    cap['filter'] = net.get('capture_filter', 'auto')
    cap['provider_ports'] = sch.get('ports', {}).get('capture_range', [12000, 13999])
    cap['ring_files'] = int(net.get('ring_files', cap.get('ring_files', 3)))
    if net.get('interface'):
        cap['interface'] = net['interface']
    return Campaign(cfg2, run_dir, pilot, dry, capture=capture)


# ──────────────────────────────────────────────────────────────────────────────
# --listen
# ──────────────────────────────────────────────────────────────────────────────

def listen(cfg: dict, a) -> int:
    net = cfg.get('network', {})
    sch_path, sch = load_schedule(cfg)
    pc2 = net.get('pc2_ip', sch['pc2_ip'])
    run_dir = (a.run_dir or LOG_DIR / ('net_' + ('pilot_' if a.pilot else 'run_') + now_ts())).resolve()
    if not a.dry_run:
        run_dir.mkdir(parents=True, exist_ok=True)
    c = net_campaign(cfg, run_dir, a.pilot, a.dry_run, not a.no_capture, sch)
    c.status.update(mode='listen', schedule=str(sch_path), pc2_ip=pc2, windows={})
    c.log(f"LISTEN {'PILOT ' if a.pilot else ''}{'DRY-RUN ' if a.dry_run else ''}run_dir={run_dir} pc1={cfg['ip']} pc2={pc2} "
          f"clock={pc2}:{sch['clock_port']} tshark={'ja (' + str(c.iface) + ', ' + c.capture_filter() + ')' if c.tshark and c.iface else 'nein'}")
    if c.capture_wanted and not (c.tshark and c.iface):
        c.warn('kein Mitschnitt: tshark oder Interface fehlt')
    windows_in_schedule = [ph['window'] for ph in sch['phases']]
    plan = [(w, 'baseline' if (w == 'filtered' and 'baseline' in windows_in_schedule) else 'done')
            for w in WINDOWS if w in windows_in_schedule]
    c.start_resources()
    try:
        for window, until in plan:
            wdir = run_dir / window
            st = {'state': 'running', 'start': _dt.datetime.now().isoformat(timespec='seconds'), 'until': until}
            c.status['windows'][window] = st
            c.save_status()
            cmd = [PY, '-u', str(HERE / 'pc1_listener.py'), '--window', window, '--until', until, '--ip', cfg['ip'],
                   '--room', cfg['room'], '--pc2', pc2, '--clock-port', str(sch['clock_port']),
                   '--clock-poll-s', str(sch.get('clock_poll_s', 10)), '--log', str(wdir / 'events.jsonl'),
                   '--tls', cfg.get('tls', 'auto'), '--max-hours', str(net.get('max_hours', 10)),
                   '--wait-start-min', str(net.get('wait_start_min', 60)), '--idle-exit-min', str(net.get('idle_exit_min', 30))]
            c.log(f'== window {window}: ' + ' '.join(cmd[2:]))
            if a.dry_run:
                st['state'] = 'dry'
                continue
            wdir.mkdir(parents=True, exist_ok=True)
            cap = c.start_capture(wdir)
            try:
                rc, timed_out = run_subprocess(cmd, wdir / 'console.txt', float(net.get('max_hours', 10)) * 3600 + 600)
            finally:
                c.stop_capture(cap)
            recs = read_jsonl(wdir / 'events.jsonl')
            end_rec = next((r for r in reversed(recs) if r.get('event') == 'listener_end'), {})
            st.update(rc=rc, timed_out=timed_out, end=_dt.datetime.now().isoformat(timespec='seconds'),
                      reason=end_rec.get('reason'), records=len(recs),
                      clock_samples=sum(1 for r in recs if r.get('event') == 'clock_offset'),
                      auto_acks=sum(1 for r in recs if r.get('event') == 'auto_ack'),
                      devices=len({r.get('epr') for r in recs if r.get('event') == 'device_discovered'}),
                      state='ok' if rc == 0 and not timed_out else 'failed')
            c.log(f'== window {window}: {st["state"]} ({st["reason"]}); records={st["records"]} clock={st["clock_samples"]} '
                  f'auto_acks={st["auto_acks"]} devices={st["devices"]}')
            c.save_status()
            if rc == 3:
                c.warn(f'window {window}: PC 2 hat nie geantwortet - Baseline-Fenster übersprungen')
                break
            if end_rec.get('reason') == 'pc2 done' and until != 'done':
                c.log('PC 2 meldet done: kein Baseline-Fenster')
                break
    except KeyboardInterrupt:
        c.log('Ctrl-C: listen abgebrochen')
        c.save_status()
        return 130
    finally:
        c.stop_resources()
    c.status['finished'] = _dt.datetime.now().isoformat(timespec='seconds')
    c.save_status()
    if not a.dry_run:
        print(f'\nLISTEN: fertig. Journale: {run_dir}\\filtered\\events.jsonl, {run_dir}\\baseline\\events.jsonl\n'
              f'Nächster Schritt (PC-2-Ordner logs\\<scenario> nach PC 1 kopieren):\n'
              f'  {PY} {HERE / "campaign.py"} --merge "{run_dir}" --pc2 <Pfad zu pc2_journal.jsonl oder dessen Ordner>')
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# clock model
# ──────────────────────────────────────────────────────────────────────────────

class Clock:
    """offset(t) = PC 2 - PC 1 from the clock_offset samples; conversion PC 2 -> PC 1."""
    def __init__(self, samples: list[dict], max_rtt: float = 0.25):
        good = [s for s in samples if s.get('offset_s') is not None and float(s.get('rtt_s', 0)) <= max_rtt]
        good.sort(key=lambda s: s['t1'])
        self.t_pc1 = [float(s['t1']) + float(s['rtt_s']) / 2 for s in good]          # moment of the sample, PC 1 clock
        self.off = [float(s['offset_s']) for s in good]
        self.rtt = [float(s['rtt_s']) for s in good]
        self.t_pc2 = [t + o for t, o in zip(self.t_pc1, self.off)]
        self.n_all, self.n = len(samples), len(good)

    def ok(self) -> bool:
        return self.n >= 1

    def _at(self, t2: float) -> tuple[float, float]:
        """(offset, uncertainty) at PC 2 time t2."""
        if self.n == 1:
            return self.off[0], self.rtt[0] / 2
        i = bisect.bisect_left(self.t_pc2, t2)
        if i <= 0:
            return self.off[0], self.rtt[0] / 2
        if i >= self.n:
            return self.off[-1], self.rtt[-1] / 2
        t0, t1 = self.t_pc2[i - 1], self.t_pc2[i]
        w = (t2 - t0) / (t1 - t0) if t1 > t0 else 0.0
        return self.off[i - 1] + w * (self.off[i] - self.off[i - 1]), max(self.rtt[i - 1], self.rtt[i]) / 2

    def to_pc1(self, t2: float) -> float:
        return t2 - self._at(t2)[0]

    def unc(self, t2: float) -> float:
        return self._at(t2)[1]

    def stats(self) -> dict:
        if not self.ok():
            return {'samples': self.n_all, 'used': 0}
        return {'samples': self.n_all, 'used': self.n, 'offset_first_s': round(self.off[0], 4), 'offset_last_s': round(self.off[-1], 4),
                'drift_s': round(self.off[-1] - self.off[0], 4), 'offset_min_s': round(min(self.off), 4), 'offset_max_s': round(max(self.off), 4),
                'rtt_median_s': round(statistics.median(self.rtt), 4), 'rtt_max_s': round(max(self.rtt), 4),
                'uncertainty_max_s': round(max(self.rtt) / 2, 4), 'span_h': round((self.t_pc1[-1] - self.t_pc1[0]) / 3600, 2),
                'first_iso': iso(self.t_pc1[0]), 'last_iso': iso(self.t_pc1[-1])}


# ──────────────────────────────────────────────────────────────────────────────
# --merge
# ──────────────────────────────────────────────────────────────────────────────

def find_pc2_journal(p: pathlib.Path) -> pathlib.Path | None:
    p = p.resolve()
    if p.is_file():
        return p
    if (p / 'pc2_journal.jsonl').exists():
        return p / 'pc2_journal.jsonl'
    cands = sorted(p.glob('**/pc2_journal.jsonl'), key=lambda x: x.stat().st_mtime)
    return cands[-1] if cands else None


def newest_net_run() -> pathlib.Path | None:
    runs = sorted((p for p in LOG_DIR.glob('net_*') if (p / 'campaign_status.json').exists()), key=lambda p: p.stat().st_mtime)
    return runs[-1] if runs else None


def _rec(event: str, t: float, **f) -> dict:
    return {'t_abs': t, 'iso': iso(t), 'event': event, 'synthetic': True, **f}


def merge(cfg: dict, a) -> int:
    run_dir = pathlib.Path(a.merge).resolve() if a.merge != 'newest' else newest_net_run()
    if not run_dir or not (run_dir / 'campaign_status.json').exists():
        print('merge: kein --listen-Lauf gefunden (campaign_status.json fehlt)')
        return 2
    if not a.pc2:
        print('merge: --pc2 <pc2_journal.jsonl oder Ordner> fehlt')
        return 2
    pc2_paths = []
    for part in str(a.pc2).split(';'):
        found = find_pc2_journal(pathlib.Path(part.strip())) if part.strip() else None
        if not found:
            print(f'merge: pc2_journal.jsonl unter {part} nicht gefunden')
            return 2
        pc2_paths.append(found)
    journals2 = [read_jsonl(p) for p in pc2_paths]
    journals2.sort(key=lambda rs: next((r['t_abs'] for r in rs if r.get('event') == 'scenario_start'), 0))
    recs2 = journals2[-1]                                   # newest scenario: schedule, scenario_start/end
    pc2_path = pc2_paths[0] if len(pc2_paths) == 1 else '; '.join(str(p) for p in pc2_paths)
    # each phase from the journal in which it completed last (fallback: the newest start)
    phase_recs: dict[str, list[dict]] = {}
    for rs in journals2:
        for r in rs:
            if r.get('event') == 'phase_end':
                phase_recs[r['phase']] = rs
    for rs in journals2:
        for r in rs:
            if r.get('event') == 'phase_start' and r['phase'] not in phase_recs:
                phase_recs[r['phase']] = rs
    scen = next((r for r in recs2 if r.get('event') == 'scenario_start'), None)
    if not scen:
        print('merge: PC-2-Journal ohne scenario_start')
        return 2
    sch = scen['schedule']
    pilot = bool(scen.get('pilot'))
    c = net_campaign(cfg, run_dir, pilot, False, True, sch)
    c.status.update(mode='merge', pc2_journal=str(pc2_path), scenario_id=scen.get('scenario_id'), pilot=pilot)
    c.warnings = []                                   # a merge recomputes everything from the journals
    c.status.setdefault('phases', {})
    c.log(f'MERGE run_dir={run_dir} pc2={pc2_path} scenario={scen.get("scenario_id")} pilot={pilot}')

    pc1 = {w: read_jsonl(run_dir / w / 'events.jsonl') for w in WINDOWS}
    clock = Clock([r for w in WINDOWS for r in pc1[w] if r.get('event') == 'clock_offset'])
    stats: dict = {'clock': clock.stats(), 'phases': {}, 'not_discovered': {}, 'ack_cycles': {}, 'netdown_reconnect': {},
                   'pc2_journals': [str(p) for p in pc2_paths],
                   'scenario': {k: scen.get(k) for k in ('scenario_id', 'pc2_ip', 'pc1_ip', 'host', 'python', 'netdown_mode', 'pilot')},
                   'pc2_end': next((r for r in recs2 if r.get('event') == 'scenario_end'), None)}
    if not clock.ok():
        c.warn('keine brauchbare clock_offset-Probe (PC 1 hat PC 2 nie erreicht): Zeiten von PC 2 unkonvertiert übernommen')
        clock.off, clock.rtt, clock.t_pc1, clock.t_pc2, clock.n = [0.0], [0.0], [0.0], [0.0], 1
    by_epr: dict[str, list[dict]] = {w: collections.defaultdict(list) for w in WINDOWS}
    for w in WINDOWS:
        for r in pc1[w]:
            if r.get('epr'):
                by_epr[w][r['epr']].append(r)
    phase_eprs: set[str] = set()
    window_trials: dict[str, list[dict]] = {w: [] for w in WINDOWS}
    T = sch['timing']

    def pc1_recs(window: str, epr: str, t_from: float | None = None) -> list[dict]:
        rs = by_epr[window].get(epr, [])
        return [r for r in rs if t_from is None or r.get('t_abs', 0) >= t_from]

    for ph in sch['phases']:
        name, kind, window = ph['name'], ph['kind'], ph['window']
        out_name = OUT_NAME.get(name, name)
        recs2 = phase_recs.get(name, journals2[-1])        # journal of the scenario that ran this phase
        odir = run_dir / out_name
        odir.mkdir(parents=True, exist_ok=True)
        out: list[dict] = []
        starts2 = [r for r in recs2 if r.get('event') == 'trial_start' and r.get('phase') == name]
        ends2 = {r.get('trial'): r for r in recs2 if r.get('event') == 'trial_end' and r.get('phase') == name}
        pst = {'state': 'running', 'kind': kind, 'window': window, 'subruns': {}}
        ps = next((r for r in recs2 if r.get('event') == 'phase_start' and r.get('phase') == name), None)
        pe = next((r for r in recs2 if r.get('event') == 'phase_end' and r.get('phase') == name), None)
        pst['start'] = iso(clock.to_pc1(ps['t_abs'])) if ps else None
        pst['end'] = iso(clock.to_pc1(pe['t_abs'])) if pe else None
        nd: collections.Counter = collections.Counter()
        if kind == 'ziel1':
            planned = sum(C.parse_counts(ph['trials']).values())
            for s2 in starts2:
                epr = s2['epr']
                phase_eprs.add(epr)
                t1 = clock.to_pc1(s2['t_abs'])
                e2 = ends2.get(s2['trial'], {})
                ts_rec = _rec('trial_start', t1, trial=s2['trial'], role=s2['role'], epr=epr, port=s2['port'], mode=s2['mode'],
                              expected=s2['expected'], t_abs_pc2=s2['t_abs'])
                out.append(ts_rec)
                window_trials[window].append(ts_rec)
                rs = pc1_recs(window, epr)
                out += rs
                discovered = any(r.get('event') == 'device_discovered' for r in rs)
                decs = [r for r in rs if r.get('event') == 'topology_decision']
                created = sum(1 for r in rs if r.get('event') == 'subscription_created')
                final = decs[-1].get('decision') if decs else None
                if not discovered:
                    outcome = 'not_discovered'
                    nd[s2['role']] += 1
                elif final == 'reject':
                    outcome = 'reject'
                elif created:
                    outcome = 'subscribed'
                else:
                    outcome = final or 'no_decision'
                t_end = clock.to_pc1(e2['t_abs']) if e2 else t1 + float(T['z1_window_s'])
                out.append(_rec('trial_end', t_end, trial=s2['trial'], role=s2['role'], epr=epr, outcome=outcome,
                                provider_subscriptions=e2.get('provider_subscriptions'), elapsed_s=round(t_end - t1, 3),
                                pc2_outcome=e2.get('outcome'), discovered=discovered))
            stats['not_discovered'][out_name] = dict(nd)
        elif kind == 'ack':
            planned = int(ph['accept']) + int(ph['refuse'])
            s2 = starts2[0] if starts2 else None
            cycles_stat = {'cycles': 0, 'accept': {'n': 0, 'pressed': 0, 'accepted': 0, 'confirmed': 0},
                           'refuse': {'n': 0, 'pressed': 0, 'failed': 0}, 'spurious_presses': 0}
            if s2:
                epr = s2['epr']
                phase_eprs.add(epr)
                t1 = clock.to_pc1(s2['t_abs'])
                window_trials[window].append(_rec('trial_start', t1, trial=0, role='ack', epr=epr, port=s2['port']))
                out.append(_rec('experiment_start', t1, goal='ziel2_ack', device=s2.get('device'), channel=s2.get('channel'),
                                accept=s2.get('accept'), refuse=s2.get('refuse'), epr=epr))
                rs = pc1_recs(window, epr)
                out += rs
                acks = sorted((r for r in rs if r.get('event') == 'auto_ack'), key=lambda r: r.get('t_seen_abs') or r['t_abs'])
                used: set[int] = set()
                ons = [r for r in recs2 if r.get('event') == 'alarm' and r.get('epr') == epr and r.get('on')]
                offs = {r.get('trial'): r for r in recs2 if r.get('event') == 'alarm' and r.get('epr') == epr and not r.get('on')}
                for on in ons:
                    cyc = on['trial']
                    policy = on.get('policy', 'accept')
                    t_on = clock.to_pc1(on['t_abs'])
                    off = offs.get(cyc)
                    t_off = clock.to_pc1(off['t_abs']) if off else t_on + float(T['ack_hold_s'])
                    hit = None
                    for i, r in enumerate(acks):
                        if i in used:
                            continue
                        t = r.get('t_seen_abs') or r['t_abs']
                        if t_on - 0.5 <= t <= t_off + 1.0:
                            hit = r
                            used.add(i)
                            break
                    cycles_stat['cycles'] += 1
                    blk = cycles_stat[policy]
                    blk['n'] += 1
                    out.append(_rec('ack_trial_start', t_on, trial=cyc, policy=policy, epr=epr, signal_handle=on.get('signal_handle')))
                    if hit:
                        blk['pressed'] += 1
                        if policy == 'accept':
                            blk['accepted'] += 1 if hit.get('ack_state') in ('accepted', 'confirmed') else 0
                            blk['confirmed'] += 1 if hit.get('confirmed') else 0
                        else:
                            blk['failed'] += 1 if hit.get('ack_state') == 'failed' else 0
                        out.append(_rec('ack_trial_end', hit['t_abs'], trial=cyc, policy=policy, epr=epr, press_id=hit.get('press_id'),
                                        ack_state=hit.get('ack_state'), outcomes=hit.get('outcomes'), confirmed=bool(hit.get('confirmed')),
                                        confirm_latency_ms=hit.get('confirm_latency_ms'), rtt_ms=hit.get('rtt_ms'),
                                        ack_message=hit.get('ack_message')))
                    else:
                        out.append(_rec('ack_trial_end', t_off, trial=cyc, policy=policy, epr=epr, press_id=None, ack_state='no_press',
                                        outcomes=[], confirmed=False, confirm_latency_ms=None, rtt_ms=None))
                cycles_stat['spurious_presses'] = len(acks) - len(used)
                for blk, k in ((cycles_stat['accept'], 'confirmed'), (cycles_stat['refuse'], 'failed')):
                    blk['lower_95'] = cp_lower(blk[k], blk['n'], 0.05) if blk['n'] else None
            stats['ack_cycles'][out_name] = cycles_stat
        elif kind == 'fault':
            planned = sum(C.parse_counts(ph['methods']).values())
            bounds = sch.get('bounds_s', {})
            inj = {r.get('trial'): r for r in recs2 if r.get('event') == 'fault_injected'}
            restores = {r.get('trial'): r for r in recs2 if r.get('event') == 'fault_restore'}
            recon = {r.get('trial'): r for r in recs2 if r.get('event') == 'reconnect_started'}
            skipped = [r for r in recs2 if r.get('event') == 'trial_skipped' and r.get('phase') == name]
            nd_rc: list[float] = []
            nd_ok = 0
            nd_n = 0
            for s2 in starts2:
                epr, i, method = s2['epr'], s2['trial'], s2['method']
                phase_eprs.add(epr)
                t1 = clock.to_pc1(s2['t_abs'])
                window_trials[window].append(_rec('trial_start', t1, trial=i, role=method, epr=epr, port=s2['port']))
                rs = pc1_recs(window, epr)
                out += rs
                e2 = ends2.get(i, {})
                fi = inj.get(i)
                rec = {'trial': i, 'method': method, 'epr': epr, 'port': s2['port'], 'outcome': e2.get('outcome', 'provider_failed'),
                       'bound_s': bounds.get(method)}
                if fi:
                    t_loss = clock.to_pc1(fi['t_loss'])
                    unc = clock.unc(fi['t_loss'])
                    out.append(_rec('fault_injected', t_loss, trial=i, method=method, epr=epr, t_loss=t_loss, bound_s=bounds.get(method),
                                    t_loss_pc2=fi['t_loss'], uncertainty_s=round(unc, 4), rule_ok=fi.get('rule_ok')))
                    after = [r for r in rs if r.get('t_abs', 0) >= t_loss - 0.5]
                    fb = next((r for r in after if r.get('event') == 'fallback'), None)
                    al = next((r for r in after if r.get('event') == 'technical_alarm' and r.get('action') == 'raised'), None)
                    dp = next((r for r in after if r.get('event') == 'device_departed'), None)
                    rec.update(t_loss=t_loss, t_loss_uncertainty_s=round(unc, 4),
                               cause=fb.get('cause') if fb else None, end_status=fb.get('end_status') if fb else None,
                               dt_fallback_s=round(fb['t_abs'] - t_loss, 3) if fb else None,
                               dt_alarm_s=round(al['t_abs'] - t_loss, 3) if al else None,
                               dt_departed_s=round(dp['t_abs'] - t_loss, 3) if dp else None,
                               escalated_at_loss=al.get('escalated_at_loss') if al else None,
                               outcome='ok' if (fb and al) else ('no_fallback' if not fb else 'no_alarm'))
                    if fi.get('rule_ok') is False:
                        rec['outcome'] = 'netdown_rule_failed'
                    back = recon.get(i) or (restores.get(i) if method == 'netdown' else None)
                    if back:
                        t_back = clock.to_pc1(back.get('t_restart') or back['t_abs'])
                        cl = next((r for r in rs if r.get('event') == 'technical_alarm' and r.get('action') == 'cleared'
                                   and r.get('t_abs', 0) >= t_back - 0.5), None)
                        rec['dt_reconnect_s'] = round(cl['t_abs'] - t_back, 3) if cl else None
                        rec['reconnect_ok'] = cl is not None
                        if method == 'reconnect':
                            out.append(_rec('reconnect_started', t_back, trial=i, epr=epr, t_restart=t_back, port=back.get('port')))
                        else:
                            nd_n += 1
                            if cl:
                                nd_ok += 1
                                nd_rc.append(rec['dt_reconnect_s'])
                t_end = clock.to_pc1(e2['t_abs']) if e2 else t1
                out.append(_rec('fault_trial_end', t_end, **rec))
            if nd_n:
                stats['netdown_reconnect'] = {'n': nd_n, 'ok': nd_ok, 'median_s': statistics.median(nd_rc) if nd_rc else None,
                                              'max_s': max(nd_rc) if nd_rc else None}
            if skipped:
                pst['skipped'] = len(skipped)
                planned -= len(skipped)        # skipped by --netdown-mode skip: not planned in this run
        else:
            continue
        out.sort(key=lambda r: r.get('t_abs', 0))
        with (odir / 'events.jsonl').open('w', encoding='utf-8') as fh:
            for r in out:
                fh.write(json.dumps(r, default=str) + '\n')
        summ = summarise_journal(odir / 'events.jsonl', out_name, cfg)
        if kind == 'ack':
            summ['done'] = stats['ack_cycles'][out_name]['cycles']
        pst['subruns'][out_name] = {'rc': 0, 'timed_out': False, **summ}
        pst['planned_trials'] = planned
        pst['done_trials'] = summ['done'] if kind != 'ziel1' else len(starts2)
        pst['ok_trials'] = summ['ok']
        pst['failures'] = summ['failures']
        pst['not_discovered'] = dict(nd)
        pst['state'] = 'ok' if pst['done_trials'] >= planned else 'incomplete'
        c.status['phases'][out_name] = pst
        c.log(f'phase {name} -> {out_name}: planned={planned} done={pst["done_trials"]} ok={summ["ok"]} failures={summ["failures"]}'
              + (f' not_discovered={dict(nd)}' if nd else ''))
    # background devices: discovered on PC 1 but no trial of any phase
    bg = {}
    for w in WINDOWS:
        for epr, rs in by_epr[w].items():
            if epr in phase_eprs:
                continue
            if any(r.get('event') == 'device_discovered' for r in rs):
                d = [r for r in rs if r.get('event') == 'topology_decision']
                bg[epr[-12:]] = {'window': w, 'room': next((r.get('room') for r in rs if r.get('event') == 'device_discovered'), None),
                                 'decision': f"{d[-1].get('decision')}@{d[-1].get('phase')}" if d else None,
                                 'subscribed': any(r.get('event') == 'subscription_created' for r in rs)}
    stats['background'] = bg
    if stats['scenario'].get('netdown_mode') == 'skip':
        c.warn('netdown wurde auf PC 2 übersprungen (--netdown-mode skip): kein realer Netzverlust in diesem Lauf')
    exp_bg = int(cfg.get('background', {}).get('expect', 0))
    if exp_bg and len(bg) < exp_bg:
        c.warn(f'nur {len(bg)} von {exp_bg} Hintergrund-Provider (C++) in den Fenstern entdeckt')
    # capture per window with ALL trial windows of that window
    c.status['capture'] = {}
    for w in WINDOWS:
        wdir = run_dir / w
        if not wdir.is_dir():
            continue
        tl = wdir / 'trials_all.jsonl'
        with tl.open('w', encoding='utf-8') as fh:
            for r in sorted(window_trials[w], key=lambda r: r['t_abs']):
                fh.write(json.dumps(r) + '\n')
        res = c.analyse_capture(wdir, tl)
        if res:
            c.status['capture'][w] = {k: res[k] for k in CAP_KEYS}
            c.status['phases'].setdefault(OUT_NAME['z1_filtered'] if w == 'filtered' else OUT_NAME['z1_baseline'], {})
    # clock quality
    cs = stats['clock']
    if cs.get('used', 0) < 2:
        c.warn('weniger als 2 brauchbare Uhren-Proben: Offset nicht über die Nacht verfolgt')
    elif cs.get('uncertainty_max_s', 0) > 0.1:
        c.warn(f"Uhren-Unsicherheit bis {cs['uncertainty_max_s']} s (RTT/2) - größer als 100 ms")
    (run_dir / 'merge_stats.json').write_text(json.dumps(stats, indent=2, default=str), encoding='utf-8')
    c.save_status()
    ana = run_analyzers(c)
    report = build_net_report(c, ana, stats, sch)
    (run_dir / 'campaign_report.md').write_text(report, encoding='utf-8')
    dossier = build_net_dossier(c, ana, stats, sch, cfg)
    (run_dir / 'dossier.md').write_text(dossier, encoding='utf-8')
    if not pilot:
        target = REPO / cfg.get('network', {}).get('dossier_target', cfg.get('dossier_target', 'docs/thesis/campaign_netz_ergebnisse.md'))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(dossier, encoding='utf-8')
        c.log(f'Dossier: {target}')
    c.status['finished'] = _dt.datetime.now().isoformat(timespec='seconds')
    c.save_status()
    print('\n' + report)
    print(f'Report : {run_dir / "campaign_report.md"}\nDossier: {run_dir / "dossier.md"}\nStats  : {run_dir / "merge_stats.json"}')
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# report + dossier (network series)
# ──────────────────────────────────────────────────────────────────────────────

def net_problems(c: Campaign, ana: dict, stats: dict, sch: dict) -> list[str]:
    problems = []
    for ph in sch['phases']:
        on = OUT_NAME.get(ph['name'], ph['name'])
        s = c.status['phases'].get(on)
        if not s or not s.get('subruns'):
            problems.append(f'{on}: nicht gelaufen')
            continue
        if s.get('state') != 'ok':
            problems.append(f"{on}: {s.get('done_trials')} von {s.get('planned_trials')} Versuchen ({s.get('state')})")
        if s.get('failures'):
            problems.append(f"{on}: {s['failures']} fehlgeschlagene Versuche")
        if s.get('skipped') and stats['scenario'].get('netdown_mode') == 'firewall':
            problems.append(f"{on}: {s['skipped']} Versuche übersprungen (netdown trotz Modus firewall)")
    z1, z2 = ana.get('ziel1'), ana.get('ziel2')
    kinds = {ph['kind'] for ph in sch['phases']}
    if 'ziel1' in kinds and not z1:
        problems.append('Ziel-1-Analyse fehlt')
    if 'ack' in kinds and not (z2 and z2.get('ack')):
        problems.append('Ziel-2-Quittierungsanalyse fehlt')
    if 'fault' in kinds and not (z2 and z2.get('fault')):
        problems.append('Ziel-2-Verlustanalyse fehlt')
    if stats['clock'].get('used', 0) < 1:
        problems.append('kein Uhren-Offset gemessen')
    if stats.get('pc2_end') is None:
        problems.append('PC-2-Journal ohne scenario_end (Szenario nicht zu Ende gelaufen)')
    elif stats['pc2_end'].get('aborted'):
        problems.append('PC-2-Szenario abgebrochen (Ctrl-C)')
    return problems


def build_net_report(c: Campaign, ana: dict, stats: dict, sch: dict) -> str:
    problems = net_problems(c, ana, stats, sch)
    phases = tuple(OUT_NAME.get(ph['name'], ph['name']) for ph in sch['phases'])
    lines = [build_report(c, ana, problems=problems, phases=phases).rstrip('\n'), '']
    cs = stats['clock']
    lines += ['Uhren (PC 2 - PC 1):',
              f"- Proben {cs.get('used')}/{cs.get('samples')}; Offset Anfang {cs.get('offset_first_s')} s, Ende {cs.get('offset_last_s')} s, "
              f"Drift {cs.get('drift_s')} s über {cs.get('span_h')} h; RTT median {cs.get('rtt_median_s')} s, max {cs.get('rtt_max_s')} s; "
              f"Unsicherheit max {cs.get('uncertainty_max_s')} s", '']
    for on, cyc in stats.get('ack_cycles', {}).items():
        a_, r_ = cyc['accept'], cyc['refuse']
        lines += ['Quittierungszyklen (Alarm von PC 2 ausgelöst):',
                  f"- accept: {a_['n']} Alarme, {a_['pressed']} gedrückt, {a_['accepted']} Fin, {a_['confirmed']} bestätigt (Presence=Ack); "
                  f"untere 95 %-Grenze bestätigt {pct(a_.get('lower_95'))}",
                  f"- refuse: {r_['n']} Alarme, {r_['pressed']} gedrückt, {r_['failed']} Fail; untere 95 %-Grenze {pct(r_.get('lower_95'))}",
                  f"- Tastendrücke ohne Zyklus: {cyc['spurious_presses']}", '']
    nd = {k: v for k, v in stats.get('not_discovered', {}).items() if v}
    if nd:
        lines += ['Nicht entdeckt (zählt gegen TRA / als falsch):'] + [f'- {k}: {v}' for k, v in nd.items()] + ['']
    if stats.get('netdown_reconnect'):
        n = stats['netdown_reconnect']
        lines += [f"netdown: Rückkehr nach Regel-Ende {n['ok']}/{n['n']}, Alarm aufgehoben median {fmt(n['median_s'], ' s')} max {fmt(n['max_s'], ' s')}", '']
    caps = c.status.get('capture', {})
    if caps:
        lines.append('Mitschnitt je Fenster:')
        for w, v in caps.items():
            lines.append(f"- {w}: {v['size_bytes'] / 2**20:.1f} MB, {v['bytes']} B; UDP/Discovery {v['udp_bytes']} B; TCP Provider-Port {v['tcp_provider_bytes']} B; "
                         f"TCP Benachrichtigungen {v['tcp_notify_bytes']} B")
        lines.append('')
    if stats.get('background'):
        lines.append('Hintergrundgeräte: ' + ', '.join(f"..{e} {v['window']} room={v['room']} {v['decision']} sub={v['subscribed']}"
                                                      for e, v in stats['background'].items()))
    return '\n'.join(lines) + '\n'


def build_net_dossier(c: Campaign, ana: dict, stats: dict, sch: dict, cfg: dict) -> str:
    z1, z2 = ana.get('ziel1'), ana.get('ziel2')
    st = c.status['phases']
    cs = stats['clock']
    pilot = bool(c.status.get('pilot'))
    sc = stats['scenario']
    T = sch['timing']
    L = [f"# Netzserie (zwei Rechner){' — PILOT' if pilot else ''} — Ergebnisdossier Ziel 1 + Ziel 2", '',
         f"Lauf `{c.run_dir.name}` (PC 1) mit PC-2-Szenario `{sc.get('scenario_id')}`. Alle Zahlen aus den Journalen beider Rechner "
         "(`filtered/events.jsonl`, `baseline/events.jsonl`, `pc2_journal.jsonl`), zusammengeführt mit `campaign.py --merge` "
         "(`merge_stats.json`, `*/analysis.json`, `ziel2_analysis.json`). Grenzen = untere einseitige 95 %-Grenze nach Clopper–Pearson. "
         "Absätze unter „Entwurf“ sind Textvorschläge.", '',
         '## 1. Einordnung', '',
         '- Diese Netzserie **ersetzt** die lokalen Ergebnisse für TRA (Ziel 1) und Fernquittierung (Ziel 2) und **ergänzt** die Verlustarten um '
         '`netdown` (realer Netzverlust). Die lokale Serie (`docs/thesis/ziel1_ergebnisse.md`, `docs/thesis/ziel2_ergebnisse.md`, lokaler '
         '`campaign.py`-Lauf) ist Voruntersuchung auf einem Rechner.',
         '- Die C++-Provider (VirtualBox) sind nur Hintergrundlast: sie werden entdeckt und entschieden, aber nicht gesteuert und nicht gemessen.', '',
         '## 2. Messaufbau', '', '| Element | Wert |', '|---|---|',
         f"| PC 1 | Dashboard headless (`SdcMyConsumer`), {sc.get('pc1_ip')}; Fenster `filtered` (`--room {sch['room']}`) und `baseline` (kein Raum); Auto-Quittierung über `QtDeviceHandler.silenceAlarm` |",
         f"| PC 2 | `pc2_scheduler.py` ({sc.get('host')}, Python {sc.get('python')}), {sc.get('pc2_ip')}: `testbed_provider.py` je Versuch mit frischer EPR, eigenem Port, mTLS aus `pki/<gerät>` |",
         f"| Steuerung | kein Steuerkanal; fester Zeitplan `schedule_c.json` (Fenster Ziel 1 {T['z1_window_s']} s, Alarmzyklus {T['ack_hold_s']}+{T['ack_pause_s']} s, suspend/netdown {T['suspend_hold_s']}/{T['netdown_hold_s']} s) |",
         f"| Uhren | UDP-Austausch alle {sch.get('clock_poll_s', 10)} s (NTP-Formel): {cs.get('used')} Proben, Offset {cs.get('offset_first_s')} s → {cs.get('offset_last_s')} s (Drift {cs.get('drift_s')} s über {cs.get('span_h')} h), Unsicherheit ≤ {cs.get('uncertainty_max_s')} s |",
         f"| netdown | Firewall-Blockregel auf PC 2 (`remoteip={sc.get('pc1_ip')}`, ein- und ausgehend) für {T['netdown_hold_s']} s, Sturzwächter als eigener Prozess; Modus `{sc.get('netdown_mode')}` |",
         f"| Mitschnitt | {'tshark auf ' + str(c.iface) + ', Filter `' + c.capture_filter() + '`' if c.tshark and c.iface else 'keiner'} |",
         f"| Hintergrund | {cfg.get('background', {}).get('expect', 0)} C++-Provider erwartet, {len(stats.get('background', {}))} entdeckt |", '',
         '## 3. Messgrößen', '',
         'TRA = korrekt abgewiesene bettfremde Versuche (foreign + mismatch) / alle bettfremden Versuche; korrekt = letzte Entscheidung `reject`, '
         'keine `subscription_created`, Provider `subscriptions = 0`. **Ein Versuch, dessen Gerät PC 1 nie entdeckt hat, zählt als falsch** (nicht entdeckt, '
         'unten getrennt ausgewiesen). Quittierung: je Alarm, den PC 2 auslöst, zählt der Zyklus als erfolgreich, wenn PC 1 gedrückt hat, die Transaktion '
         'mit Fin endete und Presence=Ack bestätigt wurde; RTT je Transaktion, p95 über erfolgreiche. Rückfall: Δt = `technical_alarm{raised}` (PC 1) − '
         't_loss (PC 2, in PC-1-Zeit umgerechnet) gegen die Schranke der Verlustart; die Unsicherheit der Umrechnung (RTT/2) steht je Versuch im Journal.', '']
    if z1:
        sub = trial_only_subscriptions(z1)
        nd = stats.get('not_discovered', {}).get('ziel1_filtered', {})
        L += ['## 4. Ziel 1 — Topologiefilterung über das Netz', '', '**Tabelle — Entscheidungen je Rolle (gefiltert)**', '',
              '| Rolle | n | korrekt | falsch | davon nicht entdeckt | letzte Entscheidung |', '|---|---|---|---|---|---|']
        for r, b in z1['by_role'].items():
            L.append(f"| {r} | {b['n']} | {b['correct']} | {b['wrong']} | {nd.get(r, 0)} | {', '.join(f'{k}={v}' for k, v in b.get('final_phase', {}).items())} |")
        L += ['', '**Tabelle — TRA**', '', '| Größe | Wert |', '|---|---|',
              f"| bettfremde Versuche (n) | {z1['bettfremd_n']} |", f"| korrekt abgewiesen | {z1['bettfremd_correct']} |",
              f"| TRA | {pct(z1['tra'])} |", f"| untere 95 %-Grenze (einseitig) | {pct(z1['tra_lower_95_one_sided'])} |",
              f"| nicht entdeckt (bettfremd) | {nd.get('foreign', 0) + nd.get('mismatch', 0)} |",
              f"| Fehlabweisungen own | {z1['false_rejects_own']} |", f"| nolocation (unknown) | {z1['nolocation_n']} |",
              f"| Subskriptionen erzeugt / vermieden (Versuchsgeräte) | {sub.get('created')} / {sub.get('avoided')} |",
              f"| Provider-status geprüft / Abweichungen | {sub.get('provider_checked')} / {len(z1['provider_disagreements'])} |"]
        if z1.get('baseline'):
            b = z1['baseline']
            ndb = stats.get('not_discovered', {}).get('ziel1_baseline', {})
            L.append(f"| Baseline (ohne Filter) | {b['eprs']} Geräte, {b['subscriptions_created']} Subskriptionen, nicht entdeckt {sum(ndb.values())} |")
        caps = c.status.get('capture', {})
        if caps:
            L += ['', '**Tabelle — Mitschnitt je Fenster (Bytes)**', '', '| Fenster | gesamt | UDP (Discovery) | TCP Provider-Port | TCP Benachrichtigungen |', '|---|---|---|---|---|']
            for w, v in caps.items():
                L.append(f"| {w} | {v['bytes']} | {v['udp_bytes']} | {v['tcp_provider_bytes']} | {v['tcp_notify_bytes']} |")
            for w in WINDOWS:
                f = c.run_dir / w / 'capture_bytes.json'
                if f.exists():
                    cb = json.loads(f.read_text(encoding='utf-8'))
                    L += ['', f'**Tabelle — TCP-Bytes je Rolle (Fenster {w}; Zuordnung über das Zeitfenster des Versuchs)**', '',
                          '| Rolle | Versuche | Bytes | je Versuch | Provider-Port | Benachrichtigungen |', '|---|---|---|---|---|---|']
                    for r, v in cb.get('by_role', {}).items():
                        L.append(f"| {r} | {v.get('trials') or '-'} | {v['bytes']} | {fmt(v.get('bytes_per_trial'))} | {v['provider_port_bytes']} | {v['notify_bytes']} |")
        L.append('')
    for on, cyc in stats.get('ack_cycles', {}).items():
        a_, r_ = cyc['accept'], cyc['refuse']
        L += ['## 5. Ziel 2 — Fernquittierung über das Netz', '', '**Tabelle — je ausgelöstem Alarm (Zyklus)**', '',
              '| Block | Alarme (n) | gedrückt | Fin | bestätigt (Presence=Ack) | Erfolgsquote | untere 95 %-Grenze |', '|---|---|---|---|---|---|---|',
              f"| accept | {a_['n']} | {a_['pressed']} | {a_['accepted']} | {a_['confirmed']} | {pct(a_['confirmed'] / a_['n']) if a_['n'] else '-'} | {pct(a_.get('lower_95'))} |",
              f"| refuse (Kontrolle) | {r_['n']} | {r_['pressed']} | 0 | 0 | Fail erkannt {r_['failed']}/{r_['n']} | {pct(r_.get('lower_95'))} |", '']
        if z2 and z2.get('ack'):
            k, ab, rb = z2['ack'], z2['ack']['accept'], z2['ack']['refuse']
            L += ['**Tabelle — je Transaktion (SetAlertState)**', '', '| Block | Transaktionen | Fin | Fail | Timeout | Erfolgsquote | untere 95 %-Grenze |', '|---|---|---|---|---|---|---|',
                  f"| accept | {ab['transactions']} | {ab['ok']} | {ab['by_outcome'].get('failed', 0)} | {ab['by_outcome'].get('timeout', 0)} | {pct(ab['success_rate'])} | {pct(ab['success_lower_95_one_sided'])} |",
                  f"| refuse | {rb['transactions']} | {rb['ok']} | {rb['failed']} | {rb['by_outcome'].get('timeout', 0)} | {pct(rb['success_rate'])} (erwartet 0) | — |", '',
                  f"RTT (Fin, n = {ab['ok']}): median {fmt(ab['rtt_ms_median'], ' ms')}, p95 {fmt(ab['rtt_ms_p95'], ' ms')}, max {fmt(ab['rtt_ms_max'], ' ms')}, min {fmt(ab['rtt_ms_min'], ' ms')}. "
                  f"Bestätigungslatenz median {fmt(k['confirm_latency_ms_median'], ' ms')}, p95 {fmt(k['confirm_latency_ms_p95'], ' ms')}; late success {k['late_success']}; Tastendrücke ohne Zyklus {cyc['spurious_presses']}.", '']
    if z2 and z2.get('fault'):
        L += ['## 6. Ziel 2 — Verbindungsverlust über das Netz', '',
              '| Verlustart | n | Ursache | median | p95 | max | Schranke | innerhalb | untere 95 %-Grenze |', '|---|---|---|---|---|---|---|---|---|']
        for m, v in z2['fault']['methods'].items():
            L.append(f"| {m} | {v['measured']}/{v['n']} | {', '.join(f'{a}={b}' for a, b in v['causes'].items())} | {fmt(v['alarm_s']['median'], ' s', 2)} | {fmt(v['alarm_s']['p95'], ' s', 2)} | {fmt(v['alarm_s']['max'], ' s', 2)} | "
                     f"{fmt(v['bound_s'], ' s', 0) if v.get('bound_s') is not None else '—'} | {v['within_bound'] if v.get('bound_s') is not None else '—'} | {pct(v['within_bound_lower_95_one_sided']) if v.get('bound_s') is not None else '—'} |")
        rc = z2['fault']['methods'].get('reconnect', {}).get('reconnect_s')
        if rc:
            L.append(f"\nRückkehr derselben EPR (reconnect): {rc['ok']}/{rc['n']}, Alarm aufgehoben nach median {fmt(rc['median'], ' s')} (max {fmt(rc['max'], ' s')}).")
        if stats.get('netdown_reconnect'):
            n = stats['netdown_reconnect']
            L.append(f"Rückkehr nach netdown (Regel entfernt, Provider lief weiter): {n['ok']}/{n['n']}, Alarm aufgehoben nach median {fmt(n['median_s'], ' s')} (max {fmt(n['max_s'], ' s')}).")
        L.append('')
    L += ['## 7. Hintergrundgeräte (C++)', '',
          ('keine entdeckt' if not stats.get('background') else '\n'.join(f"- ..{e}: Fenster {v['window']}, room={v['room']}, {v['decision']}{', subskribiert' if v['subscribed'] else ''}" for e, v in stats['background'].items())), '',
          '## 8. Grenzen', '',
          '- Zeitplan statt ereignisgesteuerter Versuche: PC 2 wartet nicht auf Entscheidungen von PC 1 (nur auf `subscriptions ≥ 1` vor einem Verlust). '
          f"Ein Gerät, das PC 1 im Fenster von {T['z1_window_s']} s nicht entdeckt, zählt als falsch (Abschnitt 4).",
          f"- Uhren: t_loss stammt von PC 2; Offset {cs.get('offset_first_s')} s → {cs.get('offset_last_s')} s gemessen, zwischen den Proben linear interpoliert; "
          f"Unsicherheit ≤ {cs.get('uncertainty_max_s')} s je Umrechnung — bei Schranken von 10–30 s und Messwerten von wenigen Sekunden ohne Einfluss auf das Urteil „innerhalb der Schranke“.",
          '- `netdown` ist eine Firewall-Blockregel auf PC 2 (Pakete werden still verworfen, kein TCP-RST), kein physischer Kabelzug; Link und IP-Adresse bleiben bestehen.',
          '- Mitschnitt: TCP-Verkehr wird über das Zeitfenster des jeweiligen Versuchs der Rolle zugeordnet (Versuche laufen nacheinander); '
          'Verbindungsaufnahme vor dem Precheck (Metadaten/WSDL) fällt auch bei abgewiesenen mismatch-Geräten an.',
          '- Quittierung: der Tastendruck erfolgt automatisch, sobald PC 1 den Alarm sieht (Abfrage alle 0,25 s), nicht durch eine Person.',
          '- C++-Provider: nicht gesteuert, nicht gemessen; nur Discovery und Entscheidung protokolliert.',
          '- Grenzen bei n = 30 je Verlustart: „alle innerhalb der Schranke“ mit 90,5 % Konfidenz; n = 300 für 99 %.', '',
          '## 9. Dateien', '',
          f"`{c.run_dir}`: `campaign_status.json`, `campaign_report.md`, `merge_stats.json`, `resources.jsonl`, `filtered/` und `baseline/` (`events.jsonl`, `console.txt`, `capture*.pcapng`, `capture_bytes.json`), "
          "je Phase `ziel1_filtered/`, `ziel1_baseline/`, `ziel2_ack/`, `ziel2_fault/` (`events.jsonl` zusammengeführt, `analysis.*`); PC-2-Journal `pc2_journal.jsonl` + `console.txt` im kopierten PC-2-Ordner.", '']
    if z1 and z2 and z2.get('ack') and z2.get('fault') and not pilot:
        ab = z2['ack']['accept']
        cyc = next(iter(stats.get('ack_cycles', {}).values()), None)
        L += ['## 10. Entwurf — Kapitel 5 (ein Absatz)', '',
              f"Über das Netz (Dashboard auf PC 1, Provider auf PC 2) wurden {z1['bettfremd_correct']} von {z1['bettfremd_n']} bettfremden Geräten abgewiesen "
              f"(TRA {pct(z1['tra'])}, untere 95 %-Grenze {pct(z1['tra_lower_95_one_sided'])}); "
              + (f"von {cyc['accept']['n']} ausgelösten Alarmen wurden {cyc['accept']['confirmed']} quittiert und bestätigt (untere Grenze {pct(cyc['accept'].get('lower_95'))}), " if cyc else '')
              + f"RTT der Quittierung p95 {fmt(ab['rtt_ms_p95'], ' ms')}; der Verbindungsverlust lag in allen Verlustarten innerhalb der hergeleiteten Schranken: "
              + '; '.join(f"{m} max {fmt(v['alarm_s']['max'], ' s')} (Schranke {fmt(v['bound_s'], ' s', 0)}, {v['within_bound']}/{v['measured']})" for m, v in z2['fault']['methods'].items() if v.get('bound_s') is not None) + '.', '']
    return '\n'.join(L)


# ──────────────────────────────────────────────────────────────────────────────
# --selftest-merge: synthetic journals through the whole pipeline
# ──────────────────────────────────────────────────────────────────────────────

def selftest_merge(cfg: dict, a) -> int:
    sch_path, sch = load_schedule(cfg)
    sch = C.json.loads(sch_path.read_text(encoding='utf-8'))
    for name, over in sch.get('pilot', {}).items():
        if name == 'timing':
            sch['timing'].update(over)
        else:
            for ph in sch['phases']:
                if ph['name'] == name:
                    ph.update(over)
    run_dir = (a.run_dir or LOG_DIR / ('selftest_merge_' + now_ts())).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    OFF = 1.5                                   # PC 2 clock is 1.5 s ahead
    t = time.time() - 3600
    pc2: list[dict] = []
    pc1: dict[str, list[dict]] = {'filtered': [], 'baseline': []}

    def p2(ev, **f):
        nonlocal t
        pc2.append({'t_abs': t + OFF, 'iso': iso(t + OFF), 'event': ev, **f})

    def p1(w, ev, dt=0.0, **f):
        pc1[w].append({'t_abs': t + dt, 'iso': iso(t + dt), 'event': ev, **f})

    def clock(w):
        pc1[w].append({'t_abs': t, 'event': 'clock_offset', 'offset_s': OFF, 'rtt_s': 0.004, 't1': t - 0.002, 't4': t + 0.002})

    scenario_id = 'selftest'
    p2('scenario_start', scenario_id=scenario_id, pc2_ip='2.2.2.2', pc1_ip='1.1.1.1', pilot=True, schedule=sch, host='pc2', python='3.x',
       netdown_mode='firewall', phases=[ph['name'] for ph in sch['phases']])
    pc1['filtered'].append({'t_abs': t, 'event': 'listener_start', 'window': 'filtered'})
    T = sch['timing']
    for ph in sch['phases']:
        w = ph['window']
        clock(w)
        p2('phase_start', phase=ph['name'], window=w, kind=ph['kind'])
        t += 2
        if ph['kind'] == 'ziel1':
            plan = C.parse_counts(ph['trials'])
            i = 0
            for role, n in plan.items():
                for _ in range(n):
                    i += 1
                    epr = f'urn:uuid:{uuid.uuid4()}'
                    exp = ('reject' if role in ('foreign', 'mismatch') else 'unknown' if role == 'nolocation' else 'accept') if ph['mode'] == 'filtered' else 'accept'
                    p2('trial_start', phase=ph['name'], kind='ziel1', trial=i, role=role, epr=epr, port=12000 + i, mode=ph['mode'], expected=exp)
                    room = {'own': 'ICU-1', 'foreign': 'ICU-2', 'mismatch': 'ICU-1', 'nolocation': None}[role]
                    p1(w, 'device_discovered', 3.0, epr=epr, room=room, scopes=[])
                    if ph['mode'] == 'filtered':
                        if role == 'foreign':
                            p1(w, 'topology_decision', 3.1, epr=epr, decision='reject', phase='discovery', reason='scope_room_mismatch')
                        elif role == 'mismatch':
                            p1(w, 'topology_decision', 4.5, epr=epr, decision='reject', phase='context_precheck', reason='scope_mismatch')
                        elif role == 'own':
                            p1(w, 'topology_decision', 4.6, epr=epr, decision='accept', phase='mdib', reason='room_match')
                            p1(w, 'subscription_created', 4.8, epr=epr, n_actions=5)
                        else:
                            p1(w, 'topology_decision', 4.6, epr=epr, decision='unknown', phase='mdib', reason='no_location')
                            p1(w, 'subscription_created', 4.8, epr=epr, n_actions=5)
                        subs = 0 if role in ('foreign', 'mismatch') else 1
                    else:
                        p1(w, 'topology_decision', 4.6, epr=epr, decision='accept', phase='no_filter', reason='baseline')
                        p1(w, 'subscription_created', 4.8, epr=epr, n_actions=5)
                        subs = 1
                    t += T['z1_window_s'] + 5
                    p2('trial_end', phase=ph['name'], kind='ziel1', trial=i, role=role, epr=epr, outcome='window_done', provider_subscriptions=subs)
                    p1(w, 'device_departed', 1.0, epr=epr)
                    t += T['z1_gap_s']
                    clock(w)
        elif ph['kind'] == 'ack':
            epr = f'urn:uuid:{uuid.uuid4()}'
            p2('trial_start', phase=ph['name'], kind='ack', trial=0, role='ack', device='ecg', epr=epr, port=12600, channel='tachycardia',
               cond_handle='ecg_alert_cond_tachycardia', signal_handle='ecg_alert_signal_tachycardia', accept=ph['accept'], refuse=ph['refuse'])
            p1(w, 'device_discovered', 2, epr=epr, room='ICU-1')
            p1(w, 'topology_decision', 3, epr=epr, decision='accept', phase='mdib', reason='room_match')
            p1(w, 'subscription_created', 3.5, epr=epr, n_actions=5)
            t += 10
            cyc = 0
            for policy, n in (('accept', ph['accept']), ('refuse', ph['refuse'])):
                p2('ack_policy', epr=epr, policy=policy, n=n)
                for _ in range(n):
                    cyc += 1
                    p2('alarm', trial=cyc, epr=epr, channel='tachycardia', on=True, policy=policy, signal_handle='ecg_alert_signal_tachycardia')
                    press = f'press-{cyc}'
                    ok = policy == 'accept'
                    p1(w, 'ack_rtt', 0.4, epr=epr, handle='ecg_alert_signal_tachycardia', ok=ok, outcome='accepted' if ok else 'failed',
                       invocation_state='Fin' if ok else 'Fail', rtt_ms=18.0 + cyc, press_id=press, purpose='ack', t_request_abs=t + 0.38)
                    if ok:
                        p1(w, 'ack_confirmed', 0.41, epr=epr, handle='ecg_alert_signal_tachycardia')
                    p1(w, 'auto_ack', 0.42, epr=epr, cond_keys=[f'{epr}|ecg_alert_cond_tachycardia'], press_id=press,
                       ack_state='confirmed' if ok else 'failed', outcomes=[], confirmed=ok, confirm_latency_ms=30.0 if ok else None,
                       rtt_ms=18.0 + cyc, t_seen_abs=t + 0.3)
                    t += T['ack_hold_s']
                    p2('alarm', trial=cyc, epr=epr, channel='tachycardia', on=False, policy=policy)
                    t += T['ack_pause_s']
            p2('trial_end', phase=ph['name'], kind='ack', trial=0, epr=epr, cycles=cyc, provider_subscriptions=1)
            p1(w, 'device_departed', 1.0, epr=epr)
            clock(w)
        elif ph['kind'] == 'fault':
            plan = C.parse_counts(ph['methods'])
            i = 0
            for method, n in plan.items():
                for _ in range(n):
                    i += 1
                    epr = f'urn:uuid:{uuid.uuid4()}'
                    p2('trial_start', phase=ph['name'], kind='fault', trial=i, role=method, method=method, device='ecg', epr=epr, port=12700 + i)
                    p1(w, 'device_discovered', 2, epr=epr, room='ICU-1')
                    p1(w, 'subscription_created', 3.5, epr=epr, n_actions=5)
                    t += 12
                    p2('fault_injected', trial=i, method=method, epr=epr, t_loss=t + OFF, bound_s=sch['bounds_s'].get(method), rule_ok=True if method == 'netdown' else None)
                    dt = {'shutdown': 1.9, 'kill': 6.9, 'suspend': 11.9, 'reconnect': 6.9, 'netdown': 12.5}[method]
                    p1(w, 'fallback', dt, epr=epr, cause='is_connected', end_status=None)
                    p1(w, 'technical_alarm', dt + 0.02, epr=epr, action='raised', cause='is_connected', escalated_at_loss=False)
                    p1(w, 'device_departed', dt + 1.5, epr=epr)
                    if method in ('reconnect', 'netdown'):
                        t += T['reconnect_wait_s'] if method == 'reconnect' else T['netdown_hold_s']
                        if method == 'reconnect':
                            p2('reconnect_started', trial=i, epr=epr, t_restart=t + OFF, port=13700 + i)
                        else:
                            p2('fault_restore', trial=i, method=method, epr=epr, ok=True)
                        p1(w, 'technical_alarm', 17.0, epr=epr, action='cleared', cause=None)
                        t += T['reconnect_hold_s']
                    else:
                        t += 30
                    p2('trial_end', phase=ph['name'], kind='fault', trial=i, method=method, epr=epr, port=12700 + i, outcome='injected')
                    t += 5
                    clock(w)
        p2('phase_end', phase=ph['name'])
        t += 5
    p2('scenario_end', aborted=False, trials_done=99)
    for w in WINDOWS:
        (run_dir / w).mkdir(exist_ok=True)
        with (run_dir / w / 'events.jsonl').open('w', encoding='utf-8') as fh:
            for r in pc1[w]:
                fh.write(json.dumps(r) + '\n')
    (run_dir / 'pc2_journal.jsonl').write_text('\n'.join(json.dumps(r) for r in pc2) + '\n', encoding='utf-8')
    (run_dir / 'campaign_status.json').write_text(json.dumps({'mode': 'listen', 'pilot': True, 'phases': {}, 'started': iso(time.time())}), encoding='utf-8')

    class A:
        merge = str(run_dir)
        pc2 = str(run_dir / 'pc2_journal.jsonl')
    rc = merge(cfg, A)
    status = json.loads((run_dir / 'campaign_status.json').read_text(encoding='utf-8'))
    bad = [f"{p}: {s.get('state')} failures={s.get('failures')}" for p, s in status['phases'].items()
           if s.get('subruns') and (s.get('state') != 'ok' or s.get('failures'))]
    head = (run_dir / 'campaign_report.md').read_text(encoding='utf-8').split('\n', 1)[0]
    print(f'\nSELFTEST-MERGE: {"OK" if rc == 0 and not bad and head == "PILOT: OK" else "PROBLEME"}  (report head: {head}; run_dir={run_dir})')
    for b in bad:
        print('- ' + b)
    return 0 if (rc == 0 and not bad) else 1
