"""Der Filterkern des Dashboards als Bibliothek, offline mit Modellzeit getrieben (Regel „adaptiv“).

Was geladen wird
----------------
Das Paket ``Dashboard/app/alarms`` (SmartAlertAggregator → AlarmCoordinator → AdaptiveAlarmAggregator mit
EvidenceAccumulator, UrgencyEngine, HysteresisFilter, ClinicalContext; DeviceProfileRepository) wird
**unverändert** aus seinen Dateien geladen, aber unter dem Paketnamen ``dashcore_alarms`` – so wird
``Dashboard/app/__init__.py`` nicht ausgeführt (kein Qt, kein sdc11073, kein Datei-Logger
``Dashboard/logs/sdc_consumer.log``).  Die relativen Importe innerhalb des Pakets bleiben gültig.

Wie die Zeit ersetzt wird
-------------------------
``smartAlertAggregator`` und ``adaptive_alarm_aggregator`` rufen ``time.monotonic()`` über ihren Modulnamen
``time`` auf (TTL-Cache, Δt der Ticks, ZOH-Zeitstempel des FIR-Fensters, Gnadenfrist).  Während eines
Replays zeigt dieser Name auf einen Stellvertreter, dessen ``monotonic()`` die Modellzeit liefert
(``VirtualClock``).  Die beiden Hintergrund-Threads des Aggregators (Watchdog-GC, periodische Auswertung)
werden nicht benutzt: ``periodic_eval=False`` und ``_stop_gc`` sofort gesetzt; stattdessen ruft das Replay
``evaluate_periodic(now)`` auf dem Gitter Δ_eval und ``_cleanup_stale_alarms()`` alle ALARM_TTL_SEC/2 selbst
auf – dieselben Methoden, die im Dashboard die Threads aufrufen.

Was das Replay nachbildet (wie ``device/handler.py:on_alert_update``)
--------------------------------------------------------------------
* Alarm ON  → Presence der Quelle True, ``check_alert_validity(ensemble, '<epr>|<handle>', LOINC, …)``
* Alarm OFF → Presence False, ``clear_alarm(ensemble, '<epr>|<handle>')``
* Topologie: je Patient ein Ensemble mit genau den vier Kanälen, die ein Profil haben (Registry wie
  ``EnsembleTopologyManager.get_member_specs``); FHIR aus (keine Gefahrencodes, Context_Log_Odds = 0)
* Rot = Übergang ``is_escalated`` False → True im ``updateEnsemble``-Aufruf des Aggregators an das
  Overview-Modell (``RotRecorder`` spiegelt ``experiments/ziel3_simulation/pc1_observer.py:RotObserver``)
"""
from __future__ import annotations

import contextlib
import importlib
import json
import logging
import sys
import time
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .journals import AlarmEvent, Journals, SOURCES

REPO = Path(__file__).resolve().parents[2]
DASH_ALARMS = REPO / "Dashboard" / "app" / "alarms"
CONTRACT = REPO / "experiments" / "ziel3_simulation" / "ziel3_contract.json"
PKG = "dashcore_alarms"


# ─────────────────────────────────────────────────────────────────────────────
# Laden des Kerns
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Core:
    saa: types.ModuleType          # smartAlertAggregator
    aaa: types.ModuleType          # adaptive_alarm_aggregator
    repo: types.ModuleType         # device_profile_repo
    mt: types.ModuleType           # math_types
    ac: types.ModuleType           # alarmCoordinator

    @property
    def files(self) -> List[Path]:
        return [Path(m.__file__) for m in (self.saa, self.aaa, self.repo, self.mt, self.ac)]


_CORE: Optional[Core] = None


def load_core() -> Core:
    """``Dashboard/app/alarms`` als Paket ``dashcore_alarms`` laden (einmal je Prozess)."""
    global _CORE
    if _CORE is not None:
        return _CORE
    if not DASH_ALARMS.is_dir():
        raise FileNotFoundError(DASH_ALARMS)
    if PKG not in sys.modules:
        pkg = types.ModuleType(PKG)
        pkg.__path__ = [str(DASH_ALARMS)]          # type: ignore[attr-defined]
        pkg.__package__ = PKG
        sys.modules[PKG] = pkg
    saa = importlib.import_module(f"{PKG}.smartAlertAggregator")
    aaa = importlib.import_module(f"{PKG}.adaptive_alarm_aggregator")
    repo = importlib.import_module(f"{PKG}.device_profile_repo")
    mt = importlib.import_module(f"{PKG}.math_types")
    ac = importlib.import_module(f"{PKG}.alarmCoordinator")
    for name in ("sdc.consumer", "sdc.consumer.aggregator", "sdc.consumer.alarm_coordinator",
                 "sdc.consumer.clinical_context", "sdc.consumer.device_profile_repo"):
        logging.getLogger(name).setLevel(logging.WARNING)
    _CORE = Core(saa=saa, aaa=aaa, repo=repo, mt=mt, ac=ac)
    return _CORE


def load_contract(path: Path | str = CONTRACT) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_raw_clinical_db(contract: dict, *, horizon_T: Optional[float] = None, alpha: Optional[float] = None,
                          delta_eval: Optional[float] = None, sensitivity: Optional[float] = None,
                          false_alarm_rate: Optional[float] = None,
                          profiles: Optional[Dict[str, Tuple[float, float]]] = None,
                          base_prob_P0: Optional[float] = None) -> dict:
    """Die Datenbank, die das Dashboard auf PC 1 statt ``clinical_db.json`` bekam
    (wie ``pc1_dashboard.build_raw_clinical_db``): genau ein Profil je Quelle; Abweichungen optional.
    ``profiles``: {Quellenschlüssel ecg|vent|spo2|abp → (TPR, FPR)} je Kanal (Prüfung der Kalibrierung);
    ``base_prob_P0``: Grundwahrscheinlichkeit P0 des klinischen Kontexts → Context_Log_Odds = ln(P0/(1 − P0)),
    Θ_target = max(ε, Θ_base·δ − Context_Log_Odds) (klassische ROC des Filters)."""
    profile = dict(contract["profile"])
    if sensitivity is not None:
        profile["sensitivity"] = sensitivity
    if false_alarm_rate is not None:
        profile["false_alarm_rate"] = false_alarm_rate
    manufacturer = contract["manufacturer"]
    models: dict = {}
    for i, src in enumerate(contract["sources"]):
        key = src.get("key") or SOURCES[int(src.get("index", i + 1)) - 1]
        tpr, fpr = (profiles or {}).get(key, (float(profile["sensitivity"]), float(profile["false_alarm_rate"])))
        model = models.setdefault(src["model"], {})
        model[src["loinc"]] = {"sensitivity": float(tpr), "false_alarm_rate": float(fpr)}
    fp = dict(contract["filter_params"])
    if horizon_T is not None:
        fp["horizon_T"] = horizon_T
    if alpha is not None:
        fp["alpha"] = alpha
    if delta_eval is not None:
        fp["delta_eval"] = delta_eval
    return {
        "_comment": "Ziel 3 ROC: aus ziel3_contract.json gebaut (analysis/roc_ziel3/dashcore.py)",
        "base_prob_P0": float(contract["context"]["base_prob_P0"] if base_prob_P0 is None else base_prob_P0),
        "filter_params": fp,
        "priority_map": dict(contract["priority_map"]),
        "odds_ratios": dict(contract["context"]["odds_ratios"]),
        "device_profiles": {manufacturer: models},
    }


def inject_db(core: Core, raw: dict) -> None:
    """Datenbank in das Repository-Singleton legen und dessen Zwischenspeicher leeren."""
    repo = core.repo.get_repository()
    repo._raw_clinical_db = raw
    repo._sensor_profile_cache = {}
    repo._odds_cache = None
    repo._priority_map = None
    repo._filter_params = None


# ─────────────────────────────────────────────────────────────────────────────
# Modellzeit
# ─────────────────────────────────────────────────────────────────────────────

class VirtualClock:
    def __init__(self, t0: float = 0.0) -> None:
        self.t = float(t0)

    def monotonic(self) -> float:
        return self.t

    def time(self) -> float:
        return self.t


@contextlib.contextmanager
def patched_time(core: Core, clock: VirtualClock):
    """``time`` der beiden Kernmodule für die Dauer des Blocks auf die Modellzeit umleiten."""
    proxy = types.SimpleNamespace(monotonic=clock.monotonic, time=clock.time, sleep=time.sleep,
                                  strftime=time.strftime, localtime=time.localtime, perf_counter=clock.monotonic)
    saved = (core.saa.time, core.aaa.time)
    core.saa.time = proxy
    core.aaa.time = proxy
    try:
        yield
    finally:
        core.saa.time, core.aaa.time = saved


# ─────────────────────────────────────────────────────────────────────────────
# Stellvertreter für Manager, Topologie und Overview-Modell
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Channel:
    patient: str
    source: str
    epr: str
    handle: str
    loinc: str
    priority: str       # BICEPS-String Hi | Me | Lo | None
    model: str
    manufacturer: str

    @property
    def key(self) -> str:
        return f"{self.epr}|{self.handle}"


class _Handler:
    """Ersatz des DeviceHandlers: nur ``snapshot_alert_presence`` (Gerätewahrheit a_j) wird gebraucht."""

    def __init__(self, epr: str) -> None:
        self.epr = epr
        self.presence: Dict[str, bool] = {}

    def snapshot_alert_presence(self) -> Dict[str, bool]:
        return dict(self.presence)


class _Manager:
    def __init__(self) -> None:
        self.devices: Dict[str, _Handler] = {}


class _Topology:
    """Ersatz des EnsembleTopologyManagers: feste Registry je Ensemble (= Patient)."""

    def __init__(self) -> None:
        self.specs: Dict[str, dict] = {}
        self.members: Dict[str, set] = {}
        self.patient: Dict[str, Tuple[str, str]] = {}

    def get_member_specs(self, ensemble_uuid: str) -> dict:
        return dict(self.specs.get(ensemble_uuid, {}))

    def get_members(self, ensemble_uuid: str) -> set:
        return set(self.members.get(ensemble_uuid, set()))

    def get_member_count(self, ensemble_uuid: str) -> int:
        return len(self.members.get(ensemble_uuid, set()))

    def reverse_lookup_patient_room(self, ensemble_uuid: str) -> Tuple[str, str]:
        return self.patient.get(ensemble_uuid, ("", ""))

    def collect_patient_danger_codes(self, ensemble_uuid: str) -> set:
        return set()


class RotRecorder:
    """Spiegel von ``pc1_observer.RotObserver``: Rot beginnt beim ersten ``is_escalated=True`` eines
    Ensembles und endet beim nächsten ``is_escalated=False`` (oder ``removeEnsemble``)."""

    def __init__(self, clock: VirtualClock) -> None:
        self._clock = clock
        self.rot_since: Dict[str, float] = {}
        self.sdc_at_start: Dict[str, float] = {}
        self.patients: Dict[str, str] = {}
        self.intervals: Dict[str, List[Tuple[float, float, float]]] = {}   # patient -> [(t0, t1, sdc)]
        self.n_updates = 0

    def updateEnsemble(self, ensemble_uuid: str, patient_id: str, room: str, device_count: int,
                       is_escalated: bool, sdc_score: float, has_active_alarms: bool = False) -> None:
        self.n_updates += 1
        self.patients[ensemble_uuid] = patient_id
        t = self._clock.monotonic()
        if is_escalated and ensemble_uuid not in self.rot_since:
            self.rot_since[ensemble_uuid] = t
            self.sdc_at_start[ensemble_uuid] = float(sdc_score)
        elif not is_escalated and ensemble_uuid in self.rot_since:
            self._end(ensemble_uuid, t)

    def removeEnsemble(self, ensemble_uuid: str) -> None:
        if ensemble_uuid in self.rot_since:
            self._end(ensemble_uuid, self._clock.monotonic())

    def markLost(self, *args, **kwargs) -> None:
        return None

    def clearLost(self, *args, **kwargs) -> None:
        return None

    def _end(self, ensemble_uuid: str, t: float) -> None:
        t0 = self.rot_since.pop(ensemble_uuid)
        sdc = self.sdc_at_start.pop(ensemble_uuid, float("nan"))
        self.intervals.setdefault(self.patients.get(ensemble_uuid, ensemble_uuid), []).append((t0, t, sdc))

    def close_open(self, t_end: float) -> None:
        for eid in list(self.rot_since):
            self._end(eid, t_end)


# ─────────────────────────────────────────────────────────────────────────────
# Replay
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ReplayResult:
    rot: Dict[str, List[Tuple[float, float, float]]]     # patient -> [(t0, t1, sdc_score bei Beginn)]
    t_start: float
    t_end: float
    n_events: int
    n_grid: int
    n_gc: int
    n_updates: int
    wall_s: float
    params: dict = field(default_factory=dict)

    def intervals(self, patient: str) -> List[Tuple[float, float]]:
        return [(a, b) for a, b, _ in self.rot.get(patient, [])]

    @property
    def n_rot(self) -> int:
        return sum(len(v) for v in self.rot.values())


def channels_from_journals(j: Journals, contract: dict) -> List[Channel]:
    """Die 4 Kanäle je Patient: EPR aus dem Journal, Handle ``<quelle>_alert_cond_<kanal>`` wie in den
    MDIBs des Testbeds, LOINC und Priorität aus der Laufkonfiguration (Ersatz: Vertrag)."""
    manufacturer = contract["manufacturer"]
    by_key = {s["key"] if "key" in s else SOURCES[s["index"] - 1]: s for s in contract["sources"]}
    out: List[Channel] = []
    for patient in j.patients:
        for src in SOURCES:
            epr = j.eprs.get((patient, src))
            if epr is None:
                continue
            info = j.sources.get(src)
            if info is not None:
                channel, loinc, prio, model = info.channel, info.loinc, info.priority, info.model
            else:
                c = by_key[src]
                channel, loinc, prio, model = c["channel"], c["loinc"], c["priority"], c["model"]
            out.append(Channel(patient=patient, source=src, epr=epr, handle=f"{src}_alert_cond_{channel}",
                               loinc=loinc, priority=prio, model=model, manufacturer=manufacturer))
    return out


class AdaptivReplay:
    """Treibt den unveränderten ``SmartAlertAggregator`` mit Journal-B-Ereignissen in Modellzeit.

    ``grid_phase``: Lage des Gitters Δ_eval relativ zu ganzen Sekunden ab Laufbeginn (im Dashboard ist die
    Phase zufällig; 0,5 s vermeidet bei den Offline-Journalen mit ganzzahligen Modellsekunden das
    Zusammenfallen von Alarmzeitpunkt und Gitterpunkt).
    """

    def __init__(self, channels: Sequence[Channel], raw_db: dict, room: str = "ICU-3",
                 grid_phase: float = 0.5, delta_min: Optional[float] = None) -> None:
        self.core = load_core()
        self.raw_db = raw_db
        self.room = room
        self.grid_phase = float(grid_phase)
        self.delta_min = None if delta_min is None else float(delta_min)
        self.channels = list(channels)
        self.by_ps: Dict[Tuple[str, str], Channel] = {(c.patient, c.source): c for c in self.channels}

    def _build(self, clock: VirtualClock):
        core = self.core
        inject_db(core, self.raw_db)
        repo = core.repo.get_repository()
        manager = _Manager()
        topology = _Topology()
        for c in self.channels:
            ens = c.patient
            h = manager.devices.setdefault(c.epr, _Handler(c.epr))
            h.presence[c.key] = False
            prof = repo.get_profile(c.manufacturer, c.model, c.loinc)
            if prof is None or not (0.0 < prof.false_positive_rate < prof.true_positive_rate):
                continue                                   # Zulassungsregel: nur Kanäle mit Profil in M
            topology.specs.setdefault(ens, {})[c.key] = core.mt.SensorSpec(
                sensor_id=c.key, tpr=prof.true_positive_rate, fpr=prof.false_positive_rate,
                priority=repo.get_priority(c.priority))
            topology.members.setdefault(ens, set()).add(c.epr)
            topology.patient[ens] = (c.patient, self.room)
        recorder = RotRecorder(clock)
        agg = core.saa.SmartAlertAggregator(manager, topology, recorder, periodic_eval=False)
        agg._stop_gc.set()                                  # Watchdog-Thread beenden; GC ruft das Replay selbst
        agg._record_arr_metric = lambda *a, **k: None       # keine CSV-Metriken des Dashboards schreiben
        if self.delta_min is not None:
            # δ_min ist in clinical_db.json nicht vorgesehen (Vertrag: Standardwert des Codes).  Hier wird das
            # Parameterobjekt EngineConfig, das SmartAlertAggregator._get_math_core_params sonst selbst aus
            # horizon_T und alpha baut, vorab mit demselben Inhalt plus δ_min gesetzt; ClinicalContext wie dort.
            fp = self.raw_db.get("filter_params", {})
            agg._engine_config = core.mt.EngineConfig(horizon_T=float(fp.get("horizon_T", 10.0)),
                                                      alpha=float(fp.get("alpha", 0.5)), delta_min=self.delta_min)
            agg._clinical_context = core.saa.ClinicalContext(
                base_prob_P0=repo.get_base_prob(), odds_ratios=repo.get_odds_ratios(),
                source=f"clinical_db.json ({repo.base_prob_source})")
        return agg, manager, topology, recorder, repo

    def run(self, events: Iterable[AlarmEvent], t_start: float, t_end: float) -> ReplayResult:
        core = self.core
        clock = VirtualClock(t_start)
        wall0 = time.perf_counter()
        with patched_time(core, clock):
            agg, manager, topology, recorder, repo = self._build(clock)
            delta_eval = float(agg.DELTA_EVAL_SEC)
            gc_every = float(agg.ALARM_TTL_SEC) / 2.0
            next_grid = t_start + self.grid_phase
            next_gc = t_start + gc_every
            n_grid = n_gc = n_events = 0

            def advance(until: float) -> None:
                nonlocal next_grid, next_gc, n_grid, n_gc
                while True:
                    tg = next_grid if next_grid < until else None
                    tc = next_gc if next_gc < until else None
                    if tg is None and tc is None:
                        return
                    if tc is None or (tg is not None and tg <= tc):
                        clock.t = tg
                        agg.evaluate_periodic(now=tg)
                        n_grid += 1
                        next_grid += delta_eval
                    else:
                        clock.t = tc
                        agg._cleanup_stale_alarms()
                        n_gc += 1
                        next_gc += gc_every

            for e in events:
                if e.t < t_start or e.t > t_end:
                    continue
                c = self.by_ps.get((e.patient, e.source))
                if c is None:
                    continue
                advance(e.t)
                clock.t = e.t
                n_events += 1
                handler = manager.devices[c.epr]
                if e.on:
                    handler.presence[c.key] = True
                    prof = repo.get_profile(c.manufacturer, c.model, c.loinc)
                    agg.check_alert_validity(c.patient, c.key, c.loinc, biceps_priority=c.priority,
                                             manufacturer=c.manufacturer, model=c.model, device_epr=c.epr,
                                             reliability_profile=prof)
                else:
                    handler.presence[c.key] = False
                    agg.clear_alarm(c.patient, c.key)
            advance(t_end)
            clock.t = t_end
            recorder.close_open(t_end)
            agg.stop()
        return ReplayResult(rot={p: sorted(v) for p, v in recorder.intervals.items()}, t_start=t_start,
                            t_end=t_end, n_events=n_events, n_grid=n_grid, n_gc=n_gc,
                            n_updates=recorder.n_updates, wall_s=time.perf_counter() - wall0,
                            params=dict(delta_eval=delta_eval, gc_every=gc_every, grid_phase=self.grid_phase,
                                        filter_params=dict(self.raw_db.get("filter_params", {})),
                                        profile=_first_profile(self.raw_db),
                                        delta_min=self.delta_min if self.delta_min is not None else 0.5))


def _first_profile(raw: dict) -> dict:
    for models in raw.get("device_profiles", {}).values():
        for codes in models.values():
            for v in codes.values():
                return dict(v)
    return {}


def all_profiles(raw: dict) -> Dict[str, dict]:
    """{Modellname → Profil} der Datenbank (zur Kennzeichnung von Replays mit Kanalprofilen)."""
    out: Dict[str, dict] = {}
    for models in raw.get("device_profiles", {}).values():
        for model, codes in models.items():
            for v in codes.values():
                out[model] = dict(v)
    return out


def horizon_T(raw: dict) -> float:
    return float(raw.get("filter_params", {}).get("horizon_T", 10.0))


def replay_journals(j: Journals, contract: Optional[dict] = None, raw_db: Optional[dict] = None,
                    grid_phase: float = 0.5, delta_min: Optional[float] = None) -> ReplayResult:
    """Bequemer Einstieg: Kanäle aus dem Journal, Datenbank aus dem Vertrag, Replay über das ganze Fenster;
    ``delta_min`` nur, wenn vom Standard des Kerns (0,5) abgewichen werden soll."""
    contract = contract or load_contract()
    raw_db = raw_db or build_raw_clinical_db(contract)
    rep = AdaptivReplay(channels_from_journals(j, contract), raw_db, room=contract.get("room", "ICU-3"),
                        grid_phase=grid_phase, delta_min=delta_min)
    return rep.run(j.alarms, j.t_start, j.t_end)
