"""pc1_dashboard.py -- PC 1 (Zentralstation) der Ziel-3-Simulation: das unveraenderte Dashboard ohne Fenster.

Ablauf
  1. Vertrag ``ziel3_contract.json`` lesen und daraus die Geraetedatenbank bauen: genau ein
     Profil je Quelle (ein LOINC je Modell -> |M| = Zahl der Quellen, im Vertrag 4), odds_ratios
     leer, P0 = 0,5, filter_params wie ``Dashboard/config/clinical_db.json``.  Zahl der Patienten
     N, erwartete Geraete N x Quellen und der Portbereich von PC 2 kommen ebenfalls aus dem Vertrag
     (``expected_counts``); nichts davon ist hier fest verdrahtet.
  2. Logger wie ``harness.Dashboard`` einrichten, Dashboard-Paket laden und die Datenbank im
     ``DeviceProfileRepository`` ersetzen -- VOR dem Bau des ``SdcMyConsumer`` (der liest beim
     Bau bereits ``filter_params``).
  3. ``harness.Dashboard(ip, room, events.jsonl, tls='no_tls')`` mit dem ``RotObserver`` an Stelle
     des PatientOverviewModel; FHIR-Abfrage abschalten; den Datei-Logger wiederherstellen, den
     der zweite ``basic_logging_setup``-Aufruf in ``harness.Dashboard`` entfernt hat.
  4. ``run_config`` ins Journal; ``ClockPoller`` aus ``pc1_listener.py`` gegen PC 2 (UDP, 10 s).
  5. ``pump``-Schleife; jede Minute eine ``alive``-Zeile; Ende bei ``state = done`` von PC 2,
     nach ``--hours`` oder bei Strg-C.
  6. ``manager.stop()``, ``aggregator.stop()``, ``sdc_consumer.log*`` kopieren, ``summary.json``.

Aufruf (Repository-Wurzel)
    .venv\\Scripts\\python experiments\\ziel3_simulation\\pc1_dashboard.py --name ziel3_1h --hours 2 \\
        --ip 192.168.0.106 --pc2-ip 192.168.0.100
    .venv\\Scripts\\python experiments\\ziel3_simulation\\pc1_dashboard.py --name trocken --dry-run
    (im Trockenlauf wird PC 2 nicht abgefragt, ausser --pc2-ip steht explizit auf der Kommandozeile)

Nichts in ``Dashboard/`` wird veraendert.  Zur Laufzeit werden drei Dinge ersetzt, alle ausserhalb
des Dashboard-Codes: ``get_repository()._raw_clinical_db`` (wie tests/test_periodic_eval.py:315),
``app.patientOverviewModel.PatientOverviewModel`` fuer die Dauer des Baus (damit harness.Dashboard
den Beobachter uebergibt) und ``topology._get_or_fetch_fhir_data`` (wie tests/test_departure_overview.py:385).
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import json
import logging
import pathlib
import platform
import re
import shutil
import statistics
import subprocess
import sys
import time
from typing import Optional

HERE = pathlib.Path(__file__).resolve().parent
EXPERIMENTS = HERE.parent
REPO = EXPERIMENTS.parent
DASH = REPO / 'Dashboard'
NEUTRAL_LINE = 'mode = NEUTRAL (Context_Log_Odds = 0)'
AGGREGATOR_LINE = 'Built aggregator for ensemble='
PRECHECK_FAIL_LINE = '[Precheck] GetContextStates failed'     # Geraet wird fuer die Sitzung abgewiesen (handler.py:435)
_AGG_RE = re.compile(r'Built aggregator for ensemble=(\S+)\s+\S+\s+(\d+) sensor')

for _p in (HERE, EXPERIMENTS, EXPERIMENTS / 'campaign_2pc'):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from pc1_observer import RotObserver  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Vertrag -> Geraetedatenbank
# ─────────────────────────────────────────────────────────────────────────────

def load_contract(path: pathlib.Path) -> dict:
    with pathlib.Path(path).open(encoding='utf-8') as fh:
        return json.load(fh)


def build_raw_clinical_db(contract: dict) -> dict:
    """Die Datenbank, die das Dashboard statt clinical_db.json bekommt: genau ein Profil je Quelle."""
    profile = contract['profile']
    manufacturer = contract['manufacturer']
    models: dict = {}
    for src in contract['sources']:
        model = models.setdefault(src['model'], {})
        model[src['loinc']] = {'sensitivity': float(profile['sensitivity']),
                              'false_alarm_rate': float(profile['false_alarm_rate'])}
    return {
        '_comment': 'Ziel 3 Simulation: aus ziel3_contract.json gebaut (pc1_dashboard.py)',
        'base_prob_P0': float(contract['context']['base_prob_P0']),
        'filter_params': dict(contract['filter_params']),
        'priority_map': dict(contract['priority_map']),
        'odds_ratios': dict(contract['context']['odds_ratios']),
        'device_profiles': {manufacturer: models},
    }


def profile_entries(raw: dict) -> list[tuple[str, str, str]]:
    """Alle (Hersteller, Modell, LOINC) mit Profil -- das ist M, wenn jede Quelle ihren Kanal schaltet."""
    out = []
    for manufacturer, models in raw['device_profiles'].items():
        for model, codes in models.items():
            for code in codes:
                out.append((manufacturer, model, code))
    return out


def expected_counts(contract: dict) -> dict:
    """Alles, was von der Zahl der Patienten abhaengt, aus dem Vertrag: N, Quellen je Patient,
    erwartete Geraete N x Quellen und der Portbereich auf PC 2."""
    n_patients = len(contract['patients'])
    n_sources = len(contract['sources'])
    ports = contract['network'].get('provider_ports', {})
    first = int(ports.get('first', 0))
    last = int(ports.get('last', -1))
    return {
        'n_patients': n_patients,
        'n_sources': n_sources,
        'expected_devices': n_patients * n_sources,
        'port_first': first,
        'port_last': last,
        'ports_consistent': (last - first + 1) == n_patients * n_sources,
    }


def provider_port(contract: dict, patient_index: int, source_index: int) -> int:
    """Port eines Providers auf PC 2: first + Quellen * (i - 1) + (j - 1), i und j ab 1."""
    n_sources = len(contract['sources'])
    first = int(contract['network']['provider_ports']['first'])
    return first + n_sources * (patient_index - 1) + (source_index - 1)


def git_head() -> Optional[str]:
    try:
        r = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=REPO, capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard-Bausteine (nur Laufzeit-Ersetzungen, kein Code im Dashboard)
# ─────────────────────────────────────────────────────────────────────────────

def inject_database(raw: dict):
    """Datenbank in das Repository-Singleton legen, bevor irgendetwas sie liest."""
    from app.alarms.device_profile_repo import get_repository
    repo = get_repository()
    repo._raw_clinical_db = raw
    return repo


@contextlib.contextmanager
def overview_replaced_by(observer):
    """harness.Dashboard baut ``PatientOverviewModel()`` selbst; fuer die Dauer des Baus liefert der
    Modul-Name den Beobachter."""
    import app.patientOverviewModel as pom
    original = pom.PatientOverviewModel
    pom.PatientOverviewModel = lambda: observer
    try:
        yield
    finally:
        pom.PatientOverviewModel = original


class _ConsoleQuiet(logging.Filter):
    """Wie in harness.Dashboard: Konsole ohne das Gezwitscher der Geraete-Threads."""
    _noisy = ('sdc.consumer.worker', 'sdc.consumer.aggregator', 'sdc.consumer.topology')

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.ERROR or not record.name.startswith(self._noisy)


def restore_file_logger() -> None:
    """``basic_logging_setup`` in harness.Dashboard entfernt alle Handler unter 'sdc.*', also auch den
    Datei-Logger Dashboard/logs/sdc_consumer.log, der beim Laden des Pakets entstand.  Der Aufruf von
    ``setup_module_logger()`` legt ihn wieder an (die Funktion ist leer-sicher: nur ohne Handler)."""
    from device.logging_setup import setup_module_logger
    logger = setup_module_logger()
    for h in logger.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            h.addFilter(_ConsoleQuiet())


def warm_up_context(aggregator) -> bool:
    """EngineConfig und ClinicalContext des Dashboards jetzt bauen statt erst beim ersten Ensemble, damit
    '[ClinicalContext] ... mode = NEUTRAL (Context_Log_Odds = 0)' am Anfang von sdc_consumer.log steht.
    Einziger Zugriff auf eine private Methode (lesend; Ergebnis wird vom Dashboard selbst zwischengespeichert)."""
    try:
        lock = getattr(aggregator, '_adaptive_lock', None)
        if lock is not None:
            with lock:
                aggregator._get_math_core_params()
        else:
            aggregator._get_math_core_params()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f'WARNUNG: ClinicalContext konnte nicht vorab gebaut werden: {exc!r}')
        return False


def device_counts(dash) -> dict:
    with dash.manager.lock:
        handlers = list(dash.manager.devices.values())
    bound = [h for h in handlers if getattr(h, '_ui_connected', False)]
    ensembles = {h.ensemble_uuid for h in handlers if getattr(h, 'ensemble_uuid', None)}
    return {'devices': len(handlers), 'bound': len(bound), 'ensembles': len(ensembles)}


def copy_consumer_logs(out_dir: pathlib.Path) -> list[str]:
    copied = []
    for src in sorted((DASH / 'logs').glob('sdc_consumer.log*')):
        dst = out_dir / src.name
        try:
            shutil.copy2(src, dst)
            copied.append(dst.name)
        except Exception as exc:  # noqa: BLE001
            print(f'WARNUNG: {src.name} nicht kopiert: {exc!r}')
    return copied


def count_events(path: pathlib.Path, since_abs: float = 0.0) -> tuple[collections.Counter, Optional[dict], dict]:
    """Ereignisse im Journal zaehlen (nur Zeilen mit t_abs >= since_abs: das Journal wird angehaengt, bei
    gleichem --name stehen aeltere Laeufe davor); letzte clock_offset-Zeile und Uhren-Statistik zurueckgeben."""
    counts: collections.Counter = collections.Counter()
    last_clock = None
    rtts: list[float] = []
    offsets: list[float] = []
    if not path.exists():
        return counts, None, {}
    with path.open(encoding='utf-8') as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            try:
                if float(rec.get('t_abs', 0.0)) < since_abs:
                    continue
            except (TypeError, ValueError):
                pass
            counts[rec.get('event')] += 1
            if rec.get('event') == 'clock_offset':
                last_clock = rec
                try:
                    rtts.append(float(rec['rtt_s']))
                    offsets.append(float(rec['offset_s']))
                except (KeyError, TypeError, ValueError):
                    pass
    clock = {}
    if rtts:
        clock = {
            'replies': len(rtts),
            'rtt_min_s': min(rtts), 'rtt_median_s': statistics.median(rtts), 'rtt_max_s': max(rtts),
            'rtt_under_50ms': sum(1 for r in rtts if r < 0.05),
            'offset_first_s': offsets[0], 'offset_last_s': offsets[-1],
            'offset_min_s': min(offsets), 'offset_max_s': max(offsets),
        }
    return counts, last_clock, clock


def log_has_line(needle: str) -> bool:
    for path in sorted((DASH / 'logs').glob('sdc_consumer.log*')):
        try:
            with path.open(encoding='utf-8', errors='replace') as fh:
                if any(needle in line for line in fh):
                    return True
        except OSError:
            continue
    return False


def aggregator_ensembles(since: str) -> dict:
    """Ensemble-Id -> Sensoren laut der LETZTEN Zeile 'Built aggregator for ensemble=<id> — N sensor(s)' seit
    ``since``.  Die Zeile erscheint bei jedem Neubau des Aggregators (jedes beitretende Geraet, spaetere
    Aenderungen), also mehrfach je Ensemble; die Abnahme zaehlt Ensembles, nicht Zeilen."""
    out: dict = {}
    for path in sorted((DASH / 'logs').glob('sdc_consumer.log*')):
        try:
            with path.open(encoding='utf-8', errors='replace') as fh:
                for line in fh:
                    if line[:19] >= since and AGGREGATOR_LINE in line:
                        m = _AGG_RE.search(line)
                        if m:
                            out[m.group(1)] = int(m.group(2))
        except OSError:
            continue
    return out


def count_log_lines(needles: tuple[str, ...], since: str) -> int:
    """Zeilen in sdc_consumer.log*, die alle ``needles`` enthalten und deren Zeitstempel am Zeilenanfang
    ('YYYY-MM-DD HH:MM:SS', Ortszeit) nicht vor ``since`` liegt -- die Logdatei enthaelt auch aeltere Laeufe."""
    n = 0
    for path in sorted((DASH / 'logs').glob('sdc_consumer.log*')):
        try:
            with path.open(encoding='utf-8', errors='replace') as fh:
                for line in fh:
                    if line[:19] >= since and all(x in line for x in needles):
                        n += 1
        except OSError:
            continue
    return n


# ─────────────────────────────────────────────────────────────────────────────
# Hauptprogramm
# ─────────────────────────────────────────────────────────────────────────────

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description='Ziel 3: Dashboard ohne Fenster auf PC 1')
    ap.add_argument('--name', required=True, help='Name des Laufs (gleich auf beiden PCs): logs/<name>/pc1')
    ap.add_argument('--hours', type=float, default=12.0, help='Zeitgrenze (Sicherung), Standard 12 h')
    ap.add_argument('--ip', default=None, help='IPv4 des Adapters von PC 1 (sonst aus dem Vertrag)')
    ap.add_argument('--pc2-ip', default=None, help='IPv4 von PC 2 fuer die Uhrenabfrage (sonst aus dem Vertrag)')
    ap.add_argument('--contract', type=pathlib.Path, default=HERE / 'ziel3_contract.json')
    ap.add_argument('--dry-run', action='store_true',
                    help='ohne PC 2: Dashboard ~30 s ohne Geraete laufen lassen und run_config, '
                         'NEUTRAL-Zeile und sauberen Stopp pruefen')
    ap.add_argument('--dry-run-seconds', type=float, default=30.0)
    return ap.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    t_prog_abs = time.time()
    since = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t_prog_abs - 2))   # Logzeilen dieses Laufs
    contract = load_contract(args.contract)
    net = contract['network']
    ip = args.ip or net.get('pc1_ip') or None
    pc2 = args.pc2_ip or net.get('pc2_ip') or None
    if args.dry_run and not args.pc2_ip:
        pc2 = None                     # Trockenlauf darf PC 2 nicht wecken (der wartet auf die erste Uhrenanfrage)
    room = contract['room']
    tls = net.get('tls', 'no_tls')
    exp = expected_counts(contract)
    if not exp['ports_consistent']:
        print(f'WARNUNG: Portbereich {exp["port_first"]}-{exp["port_last"]} passt nicht zu '
              f'{exp["n_patients"]} x {exp["n_sources"]} = {exp["expected_devices"]} Providern.')
    if not args.dry_run and not pc2:
        print('FEHLER: --pc2-ip fehlt und network.pc2_ip im Vertrag ist leer.')
        return 2
    if not args.dry_run and not ip:
        print('FEHLER: --ip fehlt und network.pc1_ip im Vertrag ist leer.')
        return 2

    out_dir = (HERE / 'logs' / args.name / 'pc1').resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / 'events.jsonl'
    decisions_path = out_dir / 'decisions.jsonl'
    raw = build_raw_clinical_db(contract)

    # 2. Logger wie harness, Paket laden, Datenbank ersetzen -- vor dem SdcMyConsumer.
    from sdc11073.loghelper import basic_logging_setup
    basic_logging_setup(level=logging.WARNING)
    if str(DASH) not in sys.path:
        sys.path.insert(0, str(DASH))
    inject_database(raw)
    from harness import Dashboard, setup_stdout, ts
    from pc1_listener import ClockPoller
    setup_stdout()

    observer = RotObserver(decisions_path)
    print(f'[{ts()}] PC 1 Dashboard: Lauf {args.name!r}, Raum {room}, ip={ip or "auto"}, pc2={pc2 or "-"}, tls={tls}, '
          f'Profile={len(profile_entries(raw))}, Patienten={exp["n_patients"]}, erwartete Geraete={exp["expected_devices"]}, '
          f'Journal={events_path}')

    # 3. Dashboard ohne Fenster mit dem Beobachter; FHIR aus; Datei-Logger zurueck.
    with overview_replaced_by(observer):
        dash = Dashboard(ip, room, events_path, tls=tls, quiet=True)
    dash.manager.topology._get_or_fetch_fhir_data = lambda patient_id: None
    restore_file_logger()
    dash.seen = collections.deque(maxlen=1000)           # harness haengt jede Journalzeile hier an
    warmed = warm_up_context(dash.manager.aggregator)

    # 4. run_config und Uhrenabfrage.
    t_start_abs = time.time()
    dash.journal.emit('run_config', name=args.name, role='pc1', hours=args.hours, dry_run=args.dry_run,
                      pc1_ip=ip, pc2_ip=pc2, room=room, tls=tls, contract_file=str(args.contract),
                      contract=contract, raw_clinical_db=raw, profiles=profile_entries(raw),
                      git_head=git_head(), python=platform.python_version(), host=platform.node(),
                      argv=sys.argv[1:], context_warmed=warmed, expected=exp)
    pc2_state: dict = {}

    def on_state(rec: dict) -> None:
        pc2_state.update(rec)

    poller = None
    if pc2:
        poller = ClockPoller(pc2, int(net.get('clock_port', 5558)), float(net.get('clock_poll_s', 10)),
                             dash.journal, on_state)
        poller.start()

    # 5. Schleife.
    limit_s = args.dry_run_seconds if args.dry_run else args.hours * 3600.0
    t0 = time.monotonic()
    last_alive = t0
    reason = ''
    rc = 0
    try:
        while True:
            dash.pump(0.25)
            now = time.monotonic()
            if pc2_state.get('state') == 'done':
                reason = 'PC 2 meldet state=done'
                break
            if now - t0 >= limit_s:
                reason = 'Trockenlauf beendet' if args.dry_run else f'Zeitgrenze --hours {args.hours:g} erreicht'
                break
            if now - last_alive >= 60.0:
                last_alive = now
                c = device_counts(dash)
                s = observer.snapshot()
                print(f'[{ts()}] alive: Geraete {c["devices"]}/{exp["expected_devices"]} (gebunden {c["bound"]}), '
                      f'Ensembles {c["ensembles"]}/{exp["n_patients"]}, '
                      f'Rot gesamt {s["rot_count"]} (aktiv {s["rot_active"]} {s["rot_active_patients"]}), '
                      f'PC 2 state={pc2_state.get("state")} offset={pc2_state.get("offset_s")} s '
                      f'rtt={pc2_state.get("rtt_s")} s, Journal {dash.journal.count} Zeilen')
    except KeyboardInterrupt:
        reason = 'Strg-C'
        rc = 130
    finally:
        # 6. Geordnet anhalten, Logs sichern, Zusammenfassung.
        if poller is not None:
            poller.stop.set()
        counts_dev = device_counts(dash)
        snap = observer.snapshot()
        dash.journal.emit('run_end', name=args.name, reason=reason, elapsed_s=round(time.monotonic() - t0, 1),
                          **counts_dev, **snap, clock_replies=poller.replies if poller else 0,
                          pc2_state=pc2_state.get('state'))
        dash.manager.stop()
        dash.manager.aggregator.stop()
        dash.pump(1.0)
        dash.journal.close()
        observer.close()
        copied = copy_consumer_logs(out_dir)
        ev_counts, last_clock, clock_stats = count_events(events_path, t_prog_abs - 1.0)
        accepted = ev_counts.get('device_accepted', 0)
        agg_ens = aggregator_ensembles(since)
        agg_full = sum(1 for n in agg_ens.values() if n == exp['n_sources'])
        precheck_failures = count_log_lines((PRECHECK_FAIL_LINE,), since)
        summary = {
            'name': args.name, 'role': 'pc1', 'reason': reason, 'dry_run': args.dry_run,
            't_start_abs': t_start_abs, 't_end_abs': time.time(),
            'elapsed_s': round(time.monotonic() - t0, 1),
            'expected': exp,
            **counts_dev, **snap,
            'events': dict(ev_counts),
            'device_accepted': accepted,
            'accepted_ok': accepted == exp['expected_devices'],
            'aggregator_ensembles': len(agg_ens),                   # Ensembles mit mindestens einer 'Built aggregator'-Zeile
            'aggregator_ensembles_full': agg_full,                  # davon zuletzt mit N Quellen (N = n_sources)
            'aggregators_ok': agg_full == exp['n_patients'],
            'precheck_failures': precheck_failures,                 # '[Precheck] GetContextStates failed' -> Geraet abgewiesen
            'verdicts': ev_counts.get('verdict', 0),
            'clock_replies': poller.replies if poller else 0,
            'clock_last': {k: last_clock.get(k) for k in ('offset_s', 'rtt_s', 'state')} if last_clock else None,
            'clock_stats': clock_stats,
            'neutral_line_in_log': count_log_lines((NEUTRAL_LINE,), since) >= 1,
            'copied_logs': copied,
            'files': {'events': str(events_path), 'decisions': str(decisions_path)},
        }
        (out_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'[{ts()}] Ende: {reason}; Geraete {counts_dev["devices"]}, akzeptiert {accepted}/{exp["expected_devices"]}, '
              f'Ensembles {counts_dev["ensembles"]}/{exp["n_patients"]}, davon mit {exp["n_sources"]} Quellen {agg_full}, '
              f'Precheck-Fehler {precheck_failures}, Rot {snap["rot_count"]}, Uhr-Antworten {summary["clock_replies"]}, '
              f'NEUTRAL-Zeile {"ja" if summary["neutral_line_in_log"] else "NEIN"}; summary.json in {out_dir}')

    if args.dry_run:
        ok_config = ev_counts.get('run_config', 0) == 1
        ok_end = ev_counts.get('run_end', 0) == 1
        ok_neutral = summary['neutral_line_in_log']
        print(f'[{ts()}] Trockenlauf: run_config {"ok" if ok_config else "FEHLT"}, run_end {"ok" if ok_end else "FEHLT"}, '
              f'NEUTRAL-Zeile {"ok" if ok_neutral else "FEHLT"}')
        if not (ok_config and ok_end and ok_neutral):
            rc = 1
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
