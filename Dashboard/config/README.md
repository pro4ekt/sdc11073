# Dashboard/config — run-time configuration

- `clinical_db.json` — calibration database of the alarm model: `base_prob_P0`,
  `filter_params`, `priority_map`, `odds_ratios` and `device_profiles` (per manufacturer /
  model / LOINC channel: TPR, FPR, priority). Read lazily by
  `Dashboard/app/alarms/device_profile_repo.py`; a channel without a profile is not admitted to the
  ensemble set. Schema notes: `Dashboard/ARCHITECTURE.md`, section 7.
- `conceptmap_mdc_to_loinc.json` — FHIR R4 ConceptMap from IEEE 11073-10101 (MDC) codes to
  LOINC, read by `Dashboard/app/concept_map.py` and used in `Dashboard/device/handler.py` to normalise metric codes.

Both files are plain JSON and are loaded relative to this folder, so the Dashboard can be
started from any working directory.
