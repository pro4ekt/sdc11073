"""ziel3_export_pc1.py -- Auszug der PC-1-Journale eines Laufs als CSV (nur lesend).

Aufruf (Repository-Wurzel):
    .venv\\Scripts\\python experiments\\ziel3_simulation\\ziel3_export_pc1.py --name ziel3_1h

Liest  logs/<name>/pc1/{events.jsonl, decisions.jsonl, summary.json, sdc_consumer.log*}
und den Vertrag; schreibt logs/<name>/pc1/export/:

    rot.csv          patient; t_start; t_end; sdc_score; cause_end
    clock.csv        t_pc1; offset_s; rtt_ms            (erste Zeile: Kommentar mit Vorzeichen)
    devices.csv      patient; model; epr; t_accepted; t_departed; status
    ttl_expired.csv  t; patient; alarms; sources_on_derived
    alarm_in.csv     t; patient; source; on_off; t_source
    summary.json     Zeilenzahlen, Dateigroessen, Zuordnungen, Hinweise

CSV: Trennzeichen ';', Zeiten = Epoch-Sekunden der Uhr von PC 1 mit 3 Nachkommastellen.
Logzeilen (sdc_consumer.log) haben Sekundenaufloesung; Journalzeilen (events/decisions) Millisekunden.
Es wird nichts im Dashboard, in den Journalen oder in den Logkopien veraendert.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import csv
import json
import pathlib
import re
import sys
import time
from typing import Optional

HERE = pathlib.Path(__file__).resolve().parent
LOG_TS = '%Y-%m-%d %H:%M:%S'

RE_LOG = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(\w+)\s*\] (\S+) -- (.*)$')
RE_XADDR = re.compile(r"Transport addresses \(x_addrs\): \['https?://[^:]+:(\d+)/")
RE_DPWS = re.compile(r"DPWS metadata: manufacturer='([^']*)', model='([^']*)'")
RE_NEW_ENS = re.compile(r'New ensemble ([0-9a-f]{8})\.\.\. created for patient=(\S+), room=')
RE_JOIN_ENS = re.compile(r'joining existing ensemble ([0-9a-f]{8})\.\.\. \(patient=([^,]+),')
RE_EXTRACTED = re.compile(r"Extracted for ([0-9a-f]{12}): patient_id='([^']+)'")
RE_ALARM = re.compile(r"\[ALARM\] \S+ (ON|OFF)\s*\| Condition handle='([^']+)' code='[^']*' ensemble=([0-9a-f]+) \| device=([0-9a-f]{12})")
RE_GC = re.compile(r'\[AggregatorGC\] Warning resolved: ensemble=([0-9a-f]+) — all alarms TTL-expired')
RE_PRECHECK_FAIL = re.compile(r'\[Precheck\] GetContextStates failed')


def fmt(t: Optional[float]) -> str:
    return '' if t is None else f'{t:.3f}'


def log_epoch(ts: str) -> float:
    """'YYYY-MM-DD HH:MM:SS' (Ortszeit des Logs) -> Epoch-Sekunden."""
    return time.mktime(time.strptime(ts, LOG_TS))


def read_jsonl(path: pathlib.Path, lo: float, hi: float) -> list[dict]:
    out = []
    if not path.exists():
        return out
    with path.open(encoding='utf-8') as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            t = rec.get('t_abs')
            if isinstance(t, (int, float)) and lo <= t <= hi:
                out.append(rec)
    return out


def log_files(run_dir: pathlib.Path) -> list[pathlib.Path]:
    """Logkopien des Laufs: sdc_consumer.log* im Laufordner plus log_archive/*.log (Rotations-Backups, die
    waehrend eines langen Laufs gesichert wurden, weil das Dashboard nur 5 Backups a 5 MB behaelt).
    Inhaltlich gleiche Dateien (Groesse + erste Zeile) werden nur einmal gelesen."""
    seen: set[tuple[int, str]] = set()
    out = []
    for path in sorted(run_dir.glob('sdc_consumer.log*')) + sorted((run_dir / 'log_archive').glob('*.log')):
        with path.open(encoding='utf-8', errors='replace') as fh:
            key = (path.stat().st_size, fh.readline())
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def read_log(run_dir: pathlib.Path, lo: float, hi: float) -> list[tuple[float, str, str, str]]:
    """Alle Logzeilen der Kopien (s. log_files) im Zeitfenster: (epoch, level, logger, msg)."""
    rows = []
    for path in log_files(run_dir):
        with path.open(encoding='utf-8', errors='replace') as fh:
            for line in fh:
                m = RE_LOG.match(line.rstrip('\n'))
                if not m:
                    continue
                t = log_epoch(m.group(1))
                if lo <= t <= hi:
                    rows.append((t, m.group(2), m.group(3), m.group(4)))
    rows.sort(key=lambda r: r[0])
    return rows


def write_csv(path: pathlib.Path, header: list[str], rows: list[list], comment: Optional[str] = None) -> int:
    with path.open('w', encoding='utf-8', newline='') as fh:
        if comment:
            fh.write('# ' + comment + '\n')
        w = csv.writer(fh, delimiter=';', lineterminator='\n')
        w.writerow(header)
        w.writerows(rows)
    return len(rows)


def main(argv: Optional[list[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    ap = argparse.ArgumentParser(description='Ziel 3: CSV-Auszug der PC-1-Journale eines Laufs')
    ap.add_argument('--name', required=True)
    ap.add_argument('--contract', type=pathlib.Path, default=HERE / 'ziel3_contract.json')
    args = ap.parse_args(argv)

    run_dir = HERE / 'logs' / args.name / 'pc1'
    out_dir = run_dir / 'export'
    contract = json.loads(args.contract.read_text(encoding='utf-8'))
    run_summary = json.loads((run_dir / 'summary.json').read_text(encoding='utf-8'))
    t_start = float(run_summary['t_start_abs'])
    t_end = float(run_summary['t_end_abs'])
    lo, hi = t_start - 5.0, t_end + 5.0

    patients = contract['patients']
    sources = contract['sources']
    n_sources = len(sources)
    port_first = int(contract['network']['provider_ports']['first'])
    handle_to_model = {s['channel']: s['model'] for s in sources}

    events = read_jsonl(run_dir / 'events.jsonl', lo, hi)
    decisions = read_jsonl(run_dir / 'decisions.jsonl', lo, hi)
    log = read_log(run_dir, lo, hi)
    notes: list[str] = []

    # ── Zuordnungen ──────────────────────────────────────────────────────────
    short_port: dict[str, int] = {}
    short_model: dict[str, str] = {}
    short_patient_log: dict[str, str] = {}
    ens_patient: dict[str, str] = {}             # 8-stellige Kurz-Id -> Patient
    for _t, _lvl, logger, msg in log:
        if logger.startswith('sdc.consumer.worker.'):
            short = logger.rsplit('.', 1)[1]
            m = RE_XADDR.search(msg)
            if m:
                short_port[short] = int(m.group(1))
                continue
            m = RE_DPWS.search(msg)
            if m:
                short_model[short] = m.group(2)
                continue
        elif logger == 'sdc.consumer.topology':
            m = RE_NEW_ENS.search(msg) or RE_JOIN_ENS.search(msg)
            if m:
                ens_patient[m.group(1)] = m.group(2)
                continue
            m = RE_EXTRACTED.search(msg)
            if m:
                short_patient_log[m.group(1)] = m.group(2)
    for d in decisions:
        if d.get('ensemble') and d.get('patient_id'):
            ens_patient[d['ensemble'][:8]] = d['patient_id']

    def by_port(port: int) -> tuple[Optional[str], Optional[str]]:
        k = port - port_first
        if k < 0 or k >= len(patients) * n_sources:
            return None, None
        return patients[k // n_sources], sources[k % n_sources]['model']

    def ens_to_patient(ens: str) -> str:
        return ens_patient.get(ens[:8], '')

    # ── devices.csv ──────────────────────────────────────────────────────────
    disc = [e for e in events if e['event'] == 'device_discovered']
    acc = {e['epr']: e['t_abs'] for e in events if e['event'] == 'device_accepted'}
    dep = {e['epr']: e for e in events if e['event'] == 'device_departed'}
    rejects = {e['epr']: e for e in events if e['event'] == 'topology_decision' and e.get('decision') == 'reject'}
    precheck_fail_shorts = {logger.rsplit('.', 1)[1] for _t, _l, logger, msg in log
                            if logger.startswith('sdc.consumer.worker.') and RE_PRECHECK_FAIL.search(msg)}
    # Rueckfall fuer das Modell, wenn die Startzeilen (x_addrs, DPWS) nicht mehr im Log sind (Rotation):
    # Alarm-Handle der [ALARM]-Zeilen des Geraets -> Quelle laut Vertrag.
    short_model_alarm: dict[str, str] = {}
    for _t, _lvl, _logger, msg in log:
        m = RE_ALARM.search(msg)
        if m and m.group(4) not in short_model_alarm:
            mdl = next((mdl for ch, mdl in handle_to_model.items() if ch in m.group(2)), None)
            if mdl:
                short_model_alarm[m.group(4)] = mdl
    dev_rows = []
    short_patient: dict[str, str] = {}
    short_model_final: dict[str, str] = {}
    mapped_by_port = mapped_by_scope = model_by_alarm = 0
    for e in disc:
        epr = e['epr']
        short = epr[-12:]
        scope_patient = next((m.group(1) for s in e.get('scopes', []) for m in [re.search(r'sdc\.ctxt\.wfl:/[^/]+/(\S+)$', s)] if m), None)
        p_port, m_port = by_port(short_port[short]) if short in short_port else (None, None)
        patient = p_port or scope_patient or short_patient_log.get(short, '')
        model = short_model.get(short) or m_port or short_model_alarm.get(short, '')
        mapped_by_port += p_port is not None
        mapped_by_scope += p_port is None and scope_patient is not None
        model_by_alarm += not short_model.get(short) and not m_port and short in short_model_alarm
        if p_port and scope_patient and p_port != scope_patient:
            notes.append(f'{short}: Port sagt {p_port}, Scope sagt {scope_patient}')
        if m_port and short_model.get(short) and m_port != short_model[short]:
            notes.append(f'{short}: Port sagt {m_port}, DPWS sagt {short_model[short]}')
        short_patient[short] = patient
        short_model_final[short] = model
        if epr in acc:
            status = 'akzeptiert'
            if epr in dep:
                status += f"; Abmeldung {dep[epr].get('cause')}"
        else:
            r = rejects.get(epr, {})
            status = f"abgewiesen: {r.get('reason', 'unbekannt')} ({r.get('phase', '')})"
            if short in precheck_fail_shorts:
                status += '; GetContextStates failed (Timeout nach Hello)'
        dev_rows.append([patient, model, epr, fmt(acc.get(epr)), fmt(dep[epr]['t_abs'] if epr in dep else None), status])
    dev_rows.sort(key=lambda r: (r[0], r[1]))

    # ── rot.csv ──────────────────────────────────────────────────────────────
    rot_rows = []
    pending: dict[str, dict] = {}
    for d in decisions:
        if d['event'] == 'rot_start':
            pending[d['ensemble']] = d
        elif d['event'] == 'rot_end':
            s = pending.pop(d['ensemble'], None)
            if s is None:
                notes.append(f"rot_end ohne rot_start: {d['patient_id']} {fmt(d['t_abs'])}")
                continue
            rot_rows.append([s['patient_id'], fmt(s['t_abs']), fmt(d['t_abs']), s['sdc_score'], d['cause']])
    for s in pending.values():
        rot_rows.append([s['patient_id'], fmt(s['t_abs']), '', s['sdc_score'], 'offen_am_ende'])
    rot_rows.sort(key=lambda r: r[1])

    # ── clock.csv ────────────────────────────────────────────────────────────
    clk_rows = []
    for e in events:
        if e['event'] != 'clock_offset':
            continue
        t_mid = (float(e['t1']) + float(e['t4'])) / 2.0 if e.get('t1') and e.get('t4') else e['t_abs']
        clk_rows.append([fmt(t_mid), f"{float(e['offset_s']):.6f}", f"{float(e['rtt_s']) * 1000:.3f}"])

    # ── alarm_in.csv (Logzeilen [ALARM] Condition, Zeit per Verdict verfeinert) ─
    verdict_times: dict[str, list[float]] = collections.defaultdict(list)
    for e in events:
        if e['event'] == 'verdict' and e.get('alert_key') and e['alert_key'] != '<periodic>':
            urn, _, handle = e['alert_key'].partition('|')
            verdict_times[(urn[-12:], handle)].append(e['t_abs'])
    for v in verdict_times.values():
        v.sort()
    used: dict[tuple, set] = collections.defaultdict(set)
    alarm_rows = []
    state_on: dict[tuple[str, str], bool] = {}
    alarm_state_log: list[tuple[float, str, str, bool]] = []       # (t, short, handle, on) fuer die TTL-Ableitung
    n_refined = 0
    for t, _lvl, logger, msg in log:
        m = RE_ALARM.search(msg)
        if not m:
            continue
        on = m.group(1) == 'ON'
        handle, ens, short = m.group(2), m.group(3), m.group(4)
        t_out, t_source = t, 'log'
        cand = verdict_times.get((short, handle))
        if cand:
            i = bisect.bisect_left(cand, t - 0.5)
            while i < len(cand) and cand[i] < t + 2.0:
                if i not in used[(short, handle)]:
                    used[(short, handle)].add(i)
                    t_out, t_source = cand[i], 'verdict'
                    n_refined += 1
                    break
                i += 1
        patient = short_patient.get(short) or ens_to_patient(ens)
        source = short_model_final.get(short) or next((mdl for ch, mdl in handle_to_model.items() if ch in handle), handle)
        alarm_rows.append([fmt(t_out), patient, source, 'on' if on else 'off', t_source])
        state_on[(short, handle)] = on
        alarm_state_log.append((t, short, handle, on))
    alarm_rows.sort(key=lambda r: r[0])

    # ── ttl_expired.csv ──────────────────────────────────────────────────────
    ttl_rows = []
    j = 0
    cur_on: dict[tuple[str, str], bool] = {}
    for t, _lvl, _logger, msg in log:
        m = RE_GC.search(msg)
        if not m:
            continue
        while j < len(alarm_state_log) and alarm_state_log[j][0] <= t:
            _tt, short, handle, on = alarm_state_log[j]
            cur_on[(short, handle)] = on
            j += 1
        patient = ens_to_patient(m.group(1))
        on_sources = sorted({short_model_final.get(s, s) for (s, h), on in cur_on.items() if on and short_patient.get(s) == patient})
        ttl_rows.append([fmt(t), patient, 'alle (Log: all alarms TTL-expired)', ','.join(on_sources)])

    # ── schreiben ────────────────────────────────────────────────────────────
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {
        'rot.csv': write_csv(out_dir / 'rot.csv', ['patient', 't_start', 't_end', 'sdc_score', 'cause_end'], rot_rows),
        'clock.csv': write_csv(out_dir / 'clock.csv', ['t_pc1', 'offset_s', 'rtt_ms'], clk_rows,
                               comment='offset_s = Uhr PC 2 - Uhr PC 1 (positiv: PC 2 geht vor); Uhr PC 2 = t_pc1 + offset_s; '
                                       't_pc1 = (t1 + t4) / 2 nach Uhr PC 1; Messung: UDP-Austausch, offset = ((t2 - t1) + (t3 - t4)) / 2'),
        'devices.csv': write_csv(out_dir / 'devices.csv', ['patient', 'model', 'epr', 't_accepted', 't_departed', 'status'], dev_rows),
        'ttl_expired.csv': write_csv(out_dir / 'ttl_expired.csv', ['t', 'patient', 'alarms', 'sources_on_derived'], ttl_rows),
        'alarm_in.csv': write_csv(out_dir / 'alarm_in.csv', ['t', 'patient', 'source', 'on_off', 't_source'], alarm_rows),
    }
    rot_by_patient = collections.Counter(r[0] for r in rot_rows)
    summary = {
        'name': args.name,
        'source_dir': str(run_dir),
        'run': {'t_start_abs': t_start, 't_end_abs': t_end, 'reason': run_summary.get('reason'),
                'window_used': [lo, hi]},
        'rows': counts,
        'bytes': {k: (out_dir / k).stat().st_size for k in counts},
        'inputs': {'events': len(events), 'decisions': len(decisions), 'log_lines': len(log),
                   'log_first': fmt(log[0][0]) if log else None, 'log_last': fmt(log[-1][0]) if log else None,
                   'log_files': [str(p.relative_to(run_dir)).replace('\\', '/') for p in log_files(run_dir)]},
        'devices': {'discovered': len(disc), 'accepted': len(acc), 'departed': len(dep),
                    'rejected': len(rejects), 'mapped_by_port': mapped_by_port, 'mapped_by_scope_only': mapped_by_scope,
                    'model_by_alarm_handle': model_by_alarm,
                    'per_patient_accepted': dict(sorted(collections.Counter(r[0] for r in dev_rows if r[3]).items()))},
        'rot': {'total': len(rot_rows), 'open_at_end': sum(r[4] == 'offen_am_ende' for r in rot_rows),
                'by_patient': dict(sorted(rot_by_patient.items()))},
        'clock': {'samples': len(clk_rows),
                  'offset_first_s': clk_rows[0][1] if clk_rows else None, 'offset_last_s': clk_rows[-1][1] if clk_rows else None},
        'alarms': {'rows': len(alarm_rows), 'on': sum(r[3] == 'on' for r in alarm_rows),
                   'off': sum(r[3] == 'off' for r in alarm_rows), 'time_from_verdict': n_refined,
                   'time_from_log_second': len(alarm_rows) - n_refined,
                   'event_verdicts_total': sum(len(v) for v in verdict_times.values())},
        'ttl_expired': {'rows': len(ttl_rows), 'by_patient': dict(sorted(collections.Counter(r[1] for r in ttl_rows).items()))},
        'notes': [
            'Zeiten: Epoch-Sekunden der Uhr von PC 1, 3 Nachkommastellen.',
            'Logzeilen haben Sekundenaufloesung (…000); alarm_in.csv nutzt die Millisekunden des zugehoerigen '
            'ereignisgetriebenen verdict (t_source=verdict), sonst die Logsekunde (t_source=log).',
            'ttl_expired.csv: das Log nennt keine einzelnen Alarme ("all alarms TTL-expired"); sources_on_derived = Quellen '
            'des Patienten, deren letzte [ALARM]-Zeile vor dem Zeitpunkt ON war (abgeleitet, Sekundenaufloesung).',
            'devices.csv: patient/model aus dem Provider-Port (Vertragsformel), gegengeprueft mit Discovery-Scope und DPWS-Modell.',
            'clock.csv: offset_s = Uhr PC 2 - Uhr PC 1.',
        ] + notes,
    }
    if log and log[0][0] > t_start + 60:
        summary['notes'].append(
            f'Log deckt erst ab {fmt(log[0][0])} ab ({(log[0][0] - t_start) / 60:.0f} min nach Start): aeltere Rotations-Backups '
            'hat das Dashboard geloescht. Startzeilen (x_addrs, DPWS, Topology) fehlen -> patient aus dem Discovery-Scope, '
            'model aus den Alarm-Handles; alarm_in.csv/ttl_expired.csv beginnen erst mit dem Log.')
    if not log:
        summary['notes'].append('Keine Logzeilen im Zeitfenster gefunden.')
    (out_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    for k, n in counts.items():
        print(f'{k:16s} {n:6d} Zeilen {summary["bytes"][k]:9d} B')
    print(f'summary.json     {(out_dir / "summary.json").stat().st_size:9d} B  -> {out_dir}')
    if notes:
        print('Hinweise:', *notes, sep='\n  ')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
