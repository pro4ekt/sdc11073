"""
clinical_context.py — HL7 FHIR clinical-context shift (Context_Log_Odds).
=========================================================================
Computes the *baseline sensitisation* of the alarm threshold from the patient's
clinical picture (diagnoses / danger codes carried over HL7 FHIR).  A sicker
patient (higher prior odds of a real crisis) should require *less* device
evidence to escalate — this class turns FHIR diagnoses into that log-odds shift.

Canonical mathematics
---------------------
    Base odds from prior probability P_0:
        O_0 = P_0 / (1 - P_0)

    Prior odds after applying diagnosis odds-ratios OR_d:
        O_prior = O_0 · Π_{d ∈ D} (OR_d)^{R_d(M)}

    Working in log-odds (the additive domain of the filter):
        Context_Log_Odds = ln(O_0) + Σ_{d ∈ D} R_d(M) · ln(OR_d)

where R_d(M) ∈ [0, 1] is the relevance indicator of diagnosis d to the connected
ensemble M (1.0 = fully relevant by default).  This scalar is subtracted from
the melted quorum barrier in ``AdaptiveAlarmAggregator.tick`` (math_part1.md §6):
        Θ_target = max(ε, k_min · w̄ · δ − Context_Log_Odds)

FHIR side of the SDC/FHIR coupling
----------------------------------
This layer is the HL7 FHIR side of the coupling between the two protocols.
SDC delivers an event-driven stream of device alarms at a cadence of seconds;
FHIR delivers the patient's static clinical context, updated over hours or
days, from a system that takes no part in monitoring.  Log-odds space makes
the two commensurable: E(t) accumulates device evidence, Context_Log_Odds
shifts the threshold, both in the same units.

The coupling is one-directional: the context moves the threshold, never the
evidence.  Unavailability of FHIR therefore does not disable the filter — it
returns the system to the neutral prior P_0 = 0.5 (math_part1.md §1, note),
for which O_0 = 1, ln(O_0) = 0 and Context_Log_Odds = 0, so the threshold is
determined exclusively by the device ensemble.  In the present configuration
(``clinical_db.json`` ``base_prob_P0 = 0.5``, no calibrated OR_d, no FHIR
danger codes) the system operates in exactly this neutral mode.  CLO ≡ 0 is
the DEFINED behaviour in the absence of clinical context, not a disabled
feature and not a computational shortcut.  The mode is made observable in the
logs: construction logs P_0, its provenance and ln(O_0) at INFO; a non-zero
log_odds result logs its composition at DEBUG.

Fail-open policy
----------------
Unknown diagnosis codes contribute ln(OR = 1.0) = 0 (neutral): a missing entry
in the DataSheet never suppresses an alarm, it simply adds no sensitisation.
"""

from __future__ import annotations

import logging
from typing import Dict, Iterable, Mapping, Optional

from .math_types import _EPS, _safe_ln

_logger = logging.getLogger('sdc.consumer.clinical_context')


class ClinicalContext:
    """Stateless calculator of the additive clinical log-odds shift.

    State (immutable after construction):
        _ln_O0:  ln(O_0) — the baseline log-odds of a crisis for an average patient.
        _ln_OR:  diagnosis code → ln(OR_d), pre-computed once for O(1) lookups.
    """

    def __init__(
        self,
        base_prob_P0: float,
        odds_ratios: Mapping[str, float],
        source: str = 'explicit',
    ) -> None:
        """Build the context from P_0 and the OR_d table.

        Args:
            base_prob_P0: baseline crisis probability P_0; 0.5 is the neutral
                          prior (ln(O_0) = 0).
            odds_ratios:  diagnosis code → OR_d.
            source:       free-text provenance of P_0 for the INFO log line
                          (e.g. 'clinical_db.json (config)', 'fallback ...').
        """
        # Convert the baseline crisis PROBABILITY P_0 into baseline ODDS O_0 =
        # P_0 / (1 - P_0), the additive-log domain the filter works in.  P_0 is
        # clamped to [_EPS, 1 - _EPS] so neither P_0 = 0 (→ O_0 = 0 → ln(0) = -inf)
        # nor P_0 = 1 (→ divide-by-zero) can ever break the logarithm.
        p0_safe = max(_EPS, min(base_prob_P0, 1.0 - _EPS))
        o_0 = p0_safe / (1.0 - p0_safe)
        self._ln_O0: float = _safe_ln(o_0)
        # Pre-log every odds-ratio: additive log-odds is the filter's native domain.
        self._ln_OR: Dict[str, float] = {
            code: _safe_ln(max(float(or_d), _EPS))
            for code, or_d in odds_ratios.items()
        }
        # Make the operating mode observable: the neutral mode (CLO = 0) and a
        # configured prior must be distinguishable in the telemetry.
        mode = 'NEUTRAL (Context_Log_Odds = 0)' if abs(self._ln_O0) < 1e-12 else 'SHIFTED'
        _logger.info(
            f'[ClinicalContext] P_0 = {base_prob_P0!r} from {source} → '
            f'ln(O_0) = {self._ln_O0:+.4f}; {len(self._ln_OR)} odds ratio(s) loaded; '
            f'mode = {mode}'
        )

    def log_odds(
        self,
        danger_codes: Iterable[str],
        r_indicator: Optional[Mapping[str, float]] = None,
    ) -> float:
        """Return Context_Log_Odds = ln(O_0) + Σ_d R_d · ln(OR_d).

        Args:
            danger_codes: iterable of FHIR diagnosis / danger codes for this patient.
            r_indicator:  optional map code → R_d(M) ∈ [0, 1] relevance weight.
                          Defaults to 1.0 for any code present (fully relevant).

        Numerical safety: unknown codes fall back to ln(OR) = 0.0 (neutral), so
        an incomplete DataSheet never shifts the threshold in the suppressing
        direction unexpectedly.
        """
        total = self._ln_O0
        terms: list = []
        for code in danger_codes:
            ln_or = self._ln_OR.get(code, 0.0)          # unknown → neutral (ln 1 = 0)
            r_d = 1.0 if r_indicator is None else float(r_indicator.get(code, 1.0))
            contribution = r_d * ln_or
            total += contribution
            if contribution != 0.0:
                terms.append(f'{code}: R_d={r_d:g}·ln(OR)={ln_or:+.4f} → {contribution:+.4f}')
        if total != 0.0 and _logger.isEnabledFor(logging.DEBUG):
            # Composition of a non-zero shift: baseline term + per-code contributions.
            _logger.debug(
                f'[ClinicalContext] Context_Log_Odds = {total:+.4f} = '
                f'ln(O_0) {self._ln_O0:+.4f}'
                + (' + ' + ' + '.join(terms) if terms else ' (no code contributions)')
            )
        return total

    @property
    def baseline_log_odds(self) -> float:
        """Return ln(O_0) — the shift when the patient has no known diagnoses."""
        return self._ln_O0

