# docs — documentation that is not code

- `Anhang_ausfuehrlich.md` — the parts of the thesis appendix that were shortened for print
  (German): options of the experiment runners, further journal formats, data directory of the
  simulation, the complete table E.5, McNemar test, control values and Monte Carlo check of the
  load model. Its links point to files of this repository.
- `model/` — the normative text of the alarm model (thesis chapter 3, sections 3.1–3.4) and
  the deviation protocol between that text and the implementation. Docstrings in
  `Dashboard/app/alarms/` cite these two files by name.
- `reports/` — the MDIB analysis written while the testbed was designed, and the result files
  of the goal 3 simulation runs.

The thesis text and its figures are not part of the repository.

Architecture of the consumer: `Dashboard/ARCHITECTURE.md`. Goal maps, metrics and run
procedures of the experiments: `experiments/README.md` and the READMEs below it.

## Dependencies

`requirements.txt` pins `sdc11073==3.0.0`, `PySide6`, `lxml`, `cryptography` (PKI tool) and
`fhirpy` (optional FHIR binding). From sdc11073 the project uses `sdc11073.consumer`
(`SdcConsumer`), `sdc11073.provider` (`SdcProvider`, `BaseProduct`, operations, role provider
components, scopes factory), `sdc11073.mdib` (`ConsumerMdib`, `ProviderMdib`),
`sdc11073.wsdiscovery`, `sdc11073.certloader`, `sdc11073.location`, `sdc11073.loghelper`,
`sdc11073.network`, `sdc11073.observableproperties`, `sdc11073.httpserver`,
`sdc11073.pysoap` and `sdc11073.xml_types` (`pm_types`, `pm_qnames`, `msg_types`,
`dpws_types`, `eventing_types`, `actions`).

`requirements-app.txt` pins numpy, pandas, scipy, matplotlib and streamlit. It is needed for
`analysis/`, for `experiments/ziel3_simulation/auswertung/` and for the tests of the evaluation.

## Further documents

- `Dashboard/ARCHITECTURE.md` — architecture reference of the consumer (packages, threads, data flow, alarm model).
- `testbed/mdib/README.md` — design notes of the testbed MDIBs.
- `tests/README.md` — the test files and what each covers.
- Every component folder has a short `README.md`.
