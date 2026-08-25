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
the raw consensus threshold in the UrgencyEngine:
        Θ_target = k_min · w̄ − Context_Log_Odds

Fail-open policy
----------------
Unknown diagnosis codes contribute ln(OR = 1.0) = 0 (neutral): a missing entry
in the DataSheet never suppresses an alarm, it simply adds no sensitisation.
"""

from __future__ import annotations

from typing import Dict, Iterable, Mapping, Optional

from .math_types import _EPS, _safe_ln


class ClinicalContext:
    """Stateless calculator of the additive clinical log-odds shift.

    State (immutable after construction):
        _ln_O0:  ln(O_0) — the baseline log-odds of a crisis for an average patient.
        _ln_OR:  diagnosis code → ln(OR_d), pre-computed once for O(1) lookups.
    """

    def __init__(self, base_prob_P0: float, odds_ratios: Mapping[str, float]) -> None:
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
        for code in danger_codes:
            ln_or = self._ln_OR.get(code, 0.0)          # unknown → neutral (ln 1 = 0)
            r_d = 1.0 if r_indicator is None else float(r_indicator.get(code, 1.0))
            total += r_d * ln_or
        return total

    @property
    def baseline_log_odds(self) -> float:
        """Return ln(O_0) — the shift when the patient has no known diagnoses."""
        return self._ln_O0

