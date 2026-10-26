# docs — documentation that is not code

- `model/` — the normative text of the alarm model (thesis chapter 3, sections 3.1–3.4) and
  the deviation protocol between that text and the implementation. Docstrings in
  `Dashboard/app/alarms/` cite these two files by name.
- `reports/` — analysis reports written while the testbed was designed (MDIB schema analysis).
- `thesis/` — the thesis text, figures and result dossiers. This folder is excluded from git
  (`.gitignore`) and may be absent in a clone; nothing in the code depends on it.

Architecture of the consumer: `Dashboard/ARCHITECTURE.md`. Goal maps, metrics and run
procedures of the experiments: `experiments/README.md` and the READMEs below it.
