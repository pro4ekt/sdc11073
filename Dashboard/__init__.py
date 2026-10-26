"""
Dashboard — SDC Consumer application (PySide6/QML desktop).

Package layout:
  app/      Core application modules (Manager, Qt bridge, aggregator, FHIR, …)
  device/   SDC device worker thread and sub-components
  qml/      QML UI files
  config/   clinical_db.json, conceptmap_mdc_to_loinc.json
  logs/     Rotating log output (auto-created)
  img/      UI image assets

Entry point:
  python main.py          (from the Dashboard/ directory)
"""

