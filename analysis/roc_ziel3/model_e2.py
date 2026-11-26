"""Modellerweiterung E2 „teilweise gemeinsame Artefakte“.

Statt des einen Parameters c_art (Anteil „alle 4 Kanäle“) werden aus den Journalen die Fehlalarm-Episoden
eines Patienten gemessen (Episode = Vereinigung überlappender Fehlalarme; ``metrics.false_episodes``):
Anteil der Fehlalarmzeit und Rate je Episodengröße m = 1 … 4, mittlere Dauer je m und die Zusammensetzung
(welche Quellen, größte gleichzeitige Zahl m_max).

Fehleskalationen je Bett·h = unabhängiger Teil + gemeinsamer Teil:
* unabhängiger Teil: die Zufallskoinzidenzen der Einzelalarme (m = 1) nach der bisherigen Episodenzählung
  (G.7 / ``model_ziel3.episode_rate``) mit |M|·FPR_ind = Σ FPR_j · q_1 (q_1 = Anteil der Fehlalarmzeit in
  Episoden mit m = 1) und d_false = mittlere Dauer der m-1-Episoden;
* gemeinsamer Teil: Episoden mit m ≥ 2, die die Regel eskaliert — k-aus-4: Episoden mit m ≥ k (Variante
  m_max ≥ k als Kontrolle); adaptiv: eine Episode mit Zusammensetzung S und Dauer L eskaliert, wenn die
  Kapitel-3-Regel (``model_ziel3.outcome`` mit T) ihren Eskalationszeitpunkt t_esc(S) ≤ L hat (alle Quellen der
  Episode gleichzeitig aktiv angenommen).  Rot-Zeit der Episode unter Verriegelung: L − t_esc.

Reduktion: gibt es nur m = 1 und m = 4, ist das Ergebnis das der bisherigen Formel mit c_art = q_4, sofern die
Rate der 4er-Episoden gleich c_art·FPR/μ ist (``e2_from_shares``).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

from analysis.belastungsmodell_app import model_ziel3 as mz

from .journals import SOURCES
from .metrics import FalseEpisode

P_SIM: Tuple[int, ...] = (3, 3, 2, 1)
PRIO_OF: Dict[str, int] = dict(zip(SOURCES, P_SIM))


@dataclass(frozen=True)
class E2Inputs:
    h: float
    episodes: Tuple[FalseEpisode, ...]
    bed_hours: float
    t_stable: float                      # stabile Zeit aller Patienten [s]
    pc: float                            # P(C)
    fpr_mean: float                      # FPR̄ der Geräte (Zeitanteil)
    P: Tuple[int, ...] = P_SIM

    # ── Messgrößen ──
    @property
    def t_false(self) -> float:
        return sum(e.t_sources for e in self.episodes)

    def share(self, m: int) -> float:
        """q_m: Anteil der Fehlalarmzeit (Summe über Quellen) in Episoden der Größe m."""
        t = self.t_false
        return sum(e.t_sources for e in self.episodes if e.m == m) / t if t else float("nan")

    def rate(self, m: int, by_mmax: bool = False) -> float:
        """Episoden je Bett·h der Größe m (bzw. mit m_max = m)."""
        n = sum(1 for e in self.episodes if (e.m_max if by_mmax else e.m) == m)
        return n / self.bed_hours if self.bed_hours else float("nan")

    def mean_L(self, m: int) -> float:
        L = [e.L for e in self.episodes if e.m == m]
        return sum(L) / len(L) if L else float("nan")

    @property
    def q1(self) -> float:
        return self.share(1)

    @property
    def d_false_1(self) -> float:
        return self.mean_L(1)

    def summary(self) -> dict:
        return dict(h=self.h, n_episoden=len(self.episodes), t_false_s=self.t_false,
                    anteil={m: self.share(m) for m in range(1, 5)},
                    rate_je_bett_h={m: self.rate(m) for m in range(1, 5)},
                    rate_mmax_je_bett_h={m: self.rate(m, True) for m in range(1, 5)},
                    dauer_s={m: self.mean_L(m) for m in range(1, 5)}, fpr_mean=self.fpr_mean, pc=self.pc)


@dataclass(frozen=True)
class E2Result:
    rule: str
    T: float
    false_per_bed_h: float       # gesamt
    part_indep: float            # Zufallskoinzidenzen der Einzelalarme (Formel) je Bett·h
    part_common: float           # eskalierende Episoden mit m ≥ 2 je Bett·h
    fpr_ii: float                # vorhergesagter Zeitanteil (Variante ii): Rot-Zeit der eskalierenden Episoden / stabile Zeit
    n_common: int


def _params(inp: E2Inputs, T: float, delta_min: float = 0.5, alpha: float = 0.5) -> mz.Params:
    q1 = inp.q1 if math.isfinite(inp.q1) else 1.0
    mfpr = max(len(inp.P) * inp.fpr_mean * q1, 1e-12)
    d1 = inp.d_false_1 if math.isfinite(inp.d_false_1) and inp.d_false_1 > 0 else 8.0
    return mz.Params(d_false=d1, P=tuple(inp.P), c_art=0.0, T=T, delta_min=delta_min, alpha=alpha,
                     mfpr_direct=mfpr, pc_direct=min(max(inp.pc, 1e-9), 1 - 1e-9))


def _indep(inp: E2Inputs, rule: str, T: float) -> Tuple[float, float]:
    """(Episoden je Bett·h, Zeitanteil) der Zufallskoinzidenzen unter den Einzelalarmen."""
    p = _params(inp, T)
    cal = mz.calibrate(p)
    rate, _common = mz.episode_rate(p, rule, cal)
    res = mz.evaluate(p, rule, cal)
    per_bed_h = rate * 3600.0 * (1 - cal.PC)
    share = res.noise_f / (1 - cal.PC) if cal.PC < 1 else float("nan")
    return per_bed_h, share


def e2_k_of_n(inp: E2Inputs, k: int, T: float = 10.0, by_mmax: bool = False) -> E2Result:
    rule = "r2" if k == 2 else "r3" if k == 3 else None
    indep, indep_share = _indep(inp, rule, T) if rule else (0.0, 0.0)
    common = [e for e in inp.episodes if (e.m_max if by_mmax else e.m) >= k and e.m >= 2]
    n_c = len(common)
    t_rot = sum(e.L for e in common)
    return E2Result(rule=f"{k}-aus-4", T=float("nan"), false_per_bed_h=indep + n_c / inp.bed_hours,
                    part_indep=indep, part_common=n_c / inp.bed_hours,
                    fpr_ii=indep_share + t_rot / inp.t_stable if inp.t_stable else float("nan"), n_common=n_c)


def episode_escalates(e: FalseEpisode, p: mz.Params, cache: Optional[Dict[Tuple[int, ...], mz.Outcome]] = None,
                      co_active: bool = True) -> Tuple[bool, float]:
    """Eskaliert die Kapitel-3-Regel „adaptiv“ in dieser Episode?  Rückgabe (ja/nein, Rot-Zeit unter Verriegelung).

    ``co_active=True``: für r' = m_max … 2 werden die r' Quellen höchster Priorität der Episode als gleichzeitig
    aktiv angenommen, und zwar für die gemessene Dauer mit ≥ r' gleichzeitig aktiven Quellen; die Episode
    eskaliert, wenn für ein r' diese Dauer ≥ t_esc(Zusammensetzung) ist.  ``co_active=False``: alle m Quellen
    für die ganze Episodendauer L aktiv (gröbere Annahme)."""
    cache = cache if cache is not None else {}
    prios = sorted((PRIO_OF[s] for s in e.sources), reverse=True)
    if len(prios) < 2:
        return False, 0.0
    candidates = range(min(e.m_max, len(prios)), 1, -1) if co_active else [len(prios)]
    for r in candidates:
        key = tuple(sorted(prios[:r]))
        o = cache.get(key)
        if o is None:
            o = cache[key] = mz.outcome(key, p, "adaptiv")
        dur = e.co_active(r) if co_active else e.L
        if o.branch != "nie" and dur >= o.t:
            # Rot unter Verriegelung bis zum Episodenende; Lage der gleichzeitig aktiven Phase in der Episode
            # unbekannt → im Mittel liegt die Hälfte der übrigen Episodendauer davor
            rot = e.L - o.t - 0.5 * (e.L - dur) if co_active else e.L - o.t
            return True, max(0.0, rot)
    return False, 0.0


def e2_adaptiv(inp: E2Inputs, T: float = 10.0, delta_min: float = 0.5, alpha: float = 0.5,
               co_active: bool = True) -> E2Result:
    p = _params(inp, T, delta_min, alpha)
    indep, indep_share = _indep(inp, "adaptiv", T)
    n_c = 0
    t_rot = 0.0
    cache: Dict[Tuple[int, ...], mz.Outcome] = {}
    for e in inp.episodes:
        esc, rot = episode_escalates(e, p, cache, co_active)
        if esc:
            n_c += 1
            t_rot += rot
    return E2Result(rule="adaptiv", T=T, false_per_bed_h=indep + n_c / inp.bed_hours, part_indep=indep,
                    part_common=n_c / inp.bed_hours,
                    fpr_ii=indep_share + t_rot / inp.t_stable if inp.t_stable else float("nan"), n_common=n_c)


def e2_from_shares(fpr: float, d_false: float, pc: float, q1: float, q4: float, rate4_per_s: float,
                   rule: str, T: float = 10.0, P: Sequence[int] = P_SIM) -> float:
    """Reduktion auf die bisherige Formel: nur Einzelalarme (Anteil q1) und 4er-Episoden (Anteil q4, Rate
    ``rate4_per_s`` je Sekunde); Rückgabe Fehleskalationen je Bett und Tag."""
    p = mz.Params(d_false=d_false, P=tuple(P), c_art=0.0, T=T, mfpr_direct=max(len(P) * fpr * q1, 1e-12),
                  pc_direct=pc)
    cal = mz.calibrate(p)
    rate, _ = mz.episode_rate(p, rule, cal)
    return (rate + rate4_per_s) * 86400.0 * (1 - cal.PC)
