"""
Dashboard — SDC Consumer application (PySide6/QML desktop).

Package layout:
  app/      Core application modules (Manager, Qt bridge, aggregator, FHIR, …)
  device/   SDC device worker thread and sub-components
  qml/      QML UI files
  config/   Runtime configuration (rules.json, MDIB fixtures)
  tools/    Utility scripts (certificate generation, …)
  data/     Session logs and test data
  logs/     Rotating log output (auto-created)
  img/      UI image assets
  uml/      Architecture diagrams

Entry point:
  python main.py          (from the Dashboard/ directory)
"""

