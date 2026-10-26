# docs/model — normative model text and deviation protocol

- `math_part1.md` — chapter 3.1–3.4 of the thesis (German): the two-axis adaptive stochastic
  alarm model that `Dashboard/app/alarms/` implements. Section references such as "§5"
  or "§6" in the docstrings of `urgency_engine.py`, `math_types.py`,
  `adaptive_alarm_aggregator.py` and `smartAlertAggregator.py` point here.
- `SPEC_DELTA.md` — the deviation protocol: every place where the implementation and the
  normative text differ (entries SD-1 …), with direction, conditions and what a fix would
  take. Referenced by the aggregator, the consumer and the integration tests as
  `docs/model/SPEC_DELTA.md SD-n`; the thesis appendix on deviations is derived from it.

Both files are documents, not configuration: changing them changes nothing at run time.
