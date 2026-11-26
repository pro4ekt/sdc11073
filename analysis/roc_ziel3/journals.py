"""Journale von PC 2 (Ziel 3): Wahrheit A (``truth.jsonl``) und Alarme B (``alarms.jsonl``).

Zeitbasis: ``t_abs`` (Sekunden seit Epoche).  Bei den Offline-Läufen (``logs/ziel3_roc/h_*``) ist
``t_abs = t_start_abs`` des Quelllaufs + Modellzeit; im Netzlauf (``netz_ziel3_12h``) ist ``t_prov_abs``
der Zeitpunkt, zu dem der Provider den Alarmzustand gesetzt hat (Uhr PC 2).  Für Alarme wird
``t_prov_abs`` genommen, falls vorhanden, sonst ``t_abs``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

SOURCES: Tuple[str, ...] = ("ecg", "vent", "spo2", "abp")
SOURCE_LABEL: Dict[str, str] = {"ecg": "ECG", "vent": "Vent", "spo2": "SpO2", "abp": "ABP"}


@dataclass(frozen=True)
class AlarmEvent:
    t: float
    patient: str
    source: str          # ecg | vent | spo2 | abp
    epr: str
    on: bool
    value: Optional[str] = None


@dataclass(frozen=True)
class Crisis:
    crisis_id: str
    patient: str
    t_start: float
    t_end: Optional[float]                       # None = am Laufende noch offen
    crossed: Dict[str, bool] = field(default_factory=dict)   # Quelle -> hat die Schwelle überschritten

    @property
    def closed(self) -> bool:
        return self.t_end is not None


@dataclass(frozen=True)
class SourceInfo:
    key: str
    j: int
    model: str
    channel: str
    loinc: str
    priority: str
    on_h: Optional[float] = None
    off_h: Optional[float] = None


@dataclass
class Journals:
    """Ein Lauf: Konfiguration, Fenster, Alarme (nach Zeit sortiert), Krisen."""

    name: str
    folder: Path
    t_start: float
    t_end: float
    patients: List[str]
    sources: Dict[str, SourceInfo]
    eprs: Dict[Tuple[str, str], str]             # (patient, source) -> EPR
    alarms: List[AlarmEvent]
    crises: List[Crisis]
    threshold_scale: Optional[float] = None
    config: dict = field(default_factory=dict)

    @property
    def hours(self) -> float:
        return (self.t_end - self.t_start) / 3600.0

    @property
    def bed_hours(self) -> float:
        return self.hours * len(self.patients)

    def crises_of(self, patient: str, closed_only: bool = True) -> List[Crisis]:
        return [c for c in self.crises if c.patient == patient and (c.closed or not closed_only)]

    def on_intervals(self, patient: str, source: str) -> List[Tuple[float, float]]:
        """ON-Abschnitte einer Quelle [t_on, t_off] innerhalb des Fensters (offene am Laufende geschlossen)."""
        out: List[Tuple[float, float]] = []
        t_on: Optional[float] = None
        for e in self.alarms:
            if e.patient != patient or e.source != source:
                continue
            if e.on:
                if t_on is None:
                    t_on = e.t
            elif t_on is not None:
                if e.t > t_on:
                    out.append((t_on, e.t))
                t_on = None
        if t_on is not None and self.t_end > t_on:
            out.append((t_on, self.t_end))
        return out

    def alarm_counts(self) -> Dict[str, int]:
        """Zahl der ON-Meldungen je Quelle."""
        out = {s: 0 for s in SOURCES}
        for e in self.alarms:
            if e.on:
                out[e.source] = out.get(e.source, 0) + 1
        return out


def _iter_jsonl(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def load_journals(folder: Path | str, name: Optional[str] = None) -> Journals:
    """``alarms.jsonl`` (B) und ``truth.jsonl`` (A) eines Laufordners lesen."""
    folder = Path(folder)
    cfg: dict = {}
    cfg_path = folder / "run_config.json"
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    t_start = t_end = None
    alarms: List[AlarmEvent] = []
    eprs: Dict[Tuple[str, str], str] = {}
    patients: List[str] = []
    for d in _iter_jsonl(folder / "alarms.jsonl"):
        ev = d.get("event")
        if ev == "run_config" and not cfg:
            cfg = d
        elif ev == "run_start":
            t_start = float(d.get("t_start_abs", d["t_abs"]))
        elif ev == "run_end":
            t_end = float(d.get("t_end_abs", d["t_abs"]))
        elif ev in ("provider_ready", "provider_start"):
            key = (d["patient"], d["source"])
            eprs.setdefault(key, d["epr"])
            if d["patient"] not in patients:
                patients.append(d["patient"])
        elif ev == "alarm":
            t = float(d.get("t_prov_abs") or d["t_abs"])
            alarms.append(AlarmEvent(t=t, patient=d["patient"], source=d["source"], epr=d["epr"],
                                     on=bool(d["on"]), value=d.get("value")))
            eprs.setdefault((d["patient"], d["source"]), d["epr"])
    alarms.sort(key=lambda e: (e.t, e.patient, e.source, not e.on))

    starts: Dict[str, dict] = {}
    crises: List[Crisis] = []
    for d in _iter_jsonl(folder / "truth.jsonl"):
        ev = d.get("event")
        if ev == "run_start" and t_start is None:
            t_start = float(d.get("t_start_abs", d["t_abs"]))
        elif ev == "run_end" and t_end is None:
            t_end = float(d.get("t_end_abs", d["t_abs"]))
        elif ev == "crisis_start":
            starts[d["crisis_id"]] = d
        elif ev == "crisis_end":
            s = starts.pop(d["crisis_id"], None)
            src = d.get("sources") or {}
            crossed = {k: bool(v.get("crossed", False)) for k, v in src.items()} if isinstance(src, dict) else {}
            crises.append(Crisis(crisis_id=d["crisis_id"], patient=d["patient"],
                                 t_start=float(d.get("t_start_abs", s["t_start_abs"] if s else d["t_abs"])),
                                 t_end=float(d.get("t_end_abs", d["t_abs"])), crossed=crossed))
    for cid, s in starts.items():                      # am Laufende noch offen
        crises.append(Crisis(crisis_id=cid, patient=s["patient"], t_start=float(s["t_start_abs"]), t_end=None))
    crises.sort(key=lambda c: (c.t_start, c.patient))
    if t_start is None or t_end is None:
        raise ValueError(f"{folder}: run_start/run_end fehlen")

    sources: Dict[str, SourceInfo] = {}
    for s in cfg.get("sources", []):
        sources[s["key"]] = SourceInfo(key=s["key"], j=int(s["j"]), model=s["model"], channel=s["channel"],
                                       loinc=s["loinc"], priority=s["priority"],
                                       on_h=s.get("on_h"), off_h=s.get("off_h"))
    if not patients:
        patients = sorted({p for p, _ in eprs} | {c.patient for c in crises})
    patients.sort()
    return Journals(name=name or cfg.get("name") or folder.name, folder=folder, t_start=t_start, t_end=t_end,
                    patients=patients, sources=sources, eprs=eprs, alarms=alarms, crises=crises,
                    threshold_scale=cfg.get("threshold_scale"), config=cfg)


def h_folders(roc_dir: Path | str) -> List[Tuple[float, Path]]:
    """Alle ``h_*``-Ordner eines ROC-Verzeichnisses, nach h sortiert."""
    roc_dir = Path(roc_dir)
    out: List[Tuple[float, Path]] = []
    for p in roc_dir.iterdir():
        if p.is_dir() and p.name.startswith("h_"):
            try:
                out.append((float(p.name[2:]), p))
            except ValueError:
                continue
    out.sort()
    return out
