"""Ziel 3 – ROC-Kurven bei Variation der Gerätegrenzwerte.

Module

* ``journals``   – Journale von PC 2 lesen (A: Wahrheit ``truth.jsonl``, B: Alarme ``alarms.jsonl``)
* ``dashcore``   – das Filterkern-Paket des Dashboards (``Dashboard/app/alarms``) als Bibliothek laden und
                   ``SmartAlertAggregator`` offline mit Modellzeit treiben (Regel „adaptiv“); kein Code des
                   Dashboards wird verändert
* ``metrics``    – TPR/FPR nach Zeitanteil, erkannte Krisen, Fehleskalationen je Bett·h, k-aus-N,
                   AUC / partieller AUC, Interpolation
* ``model_roc``  – Formel des Betreuers (binomial) und das Belastungsmodell (``model_ziel3``) je Schwellenfaktor h
* ``figures_roc`` – Abbildungen R1–R3
* ``run_roc``    – Skript: Prüfung durch Wiederholung, Sweep über h, ``roc_alle.csv``, Tabellen, Abbildungen
"""
