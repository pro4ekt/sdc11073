# Ergänzungen zum Anhang der Masterarbeit

Diese Datei enthält Abschnitte, die in der gedruckten Fassung des Anhangs gekürzt wurden. Die Nummern der Tabellen, Abbildungen und Abschnitte entsprechen einer früheren, ungekürzten Fassung und nicht immer der gedruckten Arbeit. Pfade sind relativ zu diesem Ordner.

## Zu Anhang D.1 — Runner der Einzelrechner-Betriebsart

Die drei Programme, die in Abschnitt 4.1.4 die Versuche der Einzelrechner-Betriebsart steuern, sind [`ziel1/ziel1_experiment.py`](../experiments/ziel1/ziel1_experiment.py) (Topologiefilterung), [`ziel2/ziel2_ack.py`](../experiments/ziel2/ziel2_ack.py) (Fernquittierung) und [`ziel2/ziel2_fault.py`](../experiments/ziel2/ziel2_fault.py) (Fehlereinspeisung). Der Rahmen, der das Dashboard ohne Fenster startet, ist die Klasse `Dashboard` in [`harness.py`](../experiments/harness.py).

[`harness.py`](../experiments/harness.py): `provider_cmd(device, epr, ip, port, room, bed, scope_room, ack, tls, seed, friendly, patient)` baut die Kommandozeile des Providers aus der Tabelle `DEVICES` (Kurzname → PKI-Ordner, MDIB, Patientenkennung, Modell, erster Alarmkanal). `Prov(tag, args, echo)`: Provider als Unterprozess mit `send(cmd)`, `wait_line(text, timeout)`, `wait_new_line`, `alive()`, `shutdown(timeout)` (Kommando `shutdown`, dann Beenden), `kill()` (Beenden des gesamten Prozessbaums, weil `python.exe` der virtuellen Umgebung nur ein Startprogramm ist), `suspend()`/`resume()` (alle Threads des Prozessbaums anhalten beziehungsweise fortsetzen). `status_subscriptions(line)` liest `subscriptions=N`. Tabelle D.3 nennt die Optionen der Runner.

| Runner | Optionen (Standard) |
|---|---|
| [`ziel1/ziel1_experiment.py`](../experiments/ziel1/ziel1_experiment.py) | `--ip`, `--mode filtered\|baseline` (`filtered`), `--room` (`ICU-1`), `--other-room` (`ICU-2`), `--trials` (`foreign=10,mismatch=10,own=1,nolocation=1`), `--order interleave\|grouped`, `--log`, `--port-base` (10100), `--trial-timeout` (60 s), `--settle` (3 s), `--tls auto\|force_tls\|no_tls`, `--seed` (1), `--echo-provider`, `--dry-run` |
| [`ziel1/ziel1_analyze.py`](../experiments/ziel1/ziel1_analyze.py) | `journal`, `--baseline JOURNAL`, `--json DATEI`, `--per-trial`, `--confidence` (0,95) |
| [`ziel1/ziel1_run_all.py`](../experiments/ziel1/ziel1_run_all.py) | `--ip`, `--room`, `--trials-filtered` (150/150/5/5), `--trials-baseline` (20/20/2/2), `--skip-baseline`, `--capture auto\|off`, `--tshark`, `--name`, `--dry-run` |
| [`ziel2/ziel2_ack.py`](../experiments/ziel2/ziel2_ack.py) | `--ip`, `--room`, `--device` (`ecg`), `--channel` (erster Kanal des Geräts), `--accept` (300), `--refuse` (20), `--port` (10600), `--log`, `--confirm-timeout` (5 s), `--settle` (0,5 s), `--tls`, `--echo-provider`, `--dry-run` |
| [`ziel2/ziel2_fault.py`](../experiments/ziel2/ziel2_fault.py) | `--ip`, `--room`, `--device`, `--methods` (`shutdown=30,kill=30,suspend=30,reconnect=5`), `--order`, `--port-base` (10700), `--log`, `--fallback-timeout` (90 s), `--reconnect-timeout` (120 s), `--settle` (3 s), `--tls`, `--echo-provider`, `--dry-run` |
| [`ziel2/ziel2_analyze.py`](../experiments/ziel2/ziel2_analyze.py) | `journals ...`, `--json`, `--per-trial`, `--confidence` (0,95) |
| [`ziel2/ziel2_run_all.py`](../experiments/ziel2/ziel2_run_all.py) | `--ip`, `--room`, `--accept`, `--refuse`, `--methods`, `--skip-ack`, `--skip-fault`, `--name`, `--dry-run` |

*Tabelle D.3 — Optionen der Runner der Einzelrechner-Betriebsart, Standardwerte in Klammern.*

## Zu Anhang D.1 — weitere Angaben zur Kampagne

Zwei weitere Optionen von [`pc2_scheduler.py`](../experiments/campaign_2pc/pc2_scheduler.py), `--netdown-watchdog` und `--watchdog-log`, sind nicht für den Aufruf von Hand gedacht. Mit ihnen startet der Planer bei einem Eingriff `netdown` eine zweite Instanz von sich selbst als Wächter ([`pc2_scheduler.py:185–198`](../experiments/campaign_2pc/pc2_scheduler.py#L185-L198)). Der Wächter entfernt die Firewall-Regeln nach der Haltezeit und einer Reserve von 15 s ([`pc2_scheduler.py:503`](../experiments/campaign_2pc/pc2_scheduler.py#L503)) auch dann, wenn der Planer selbst vorher endet.

[`campaign.json`](../experiments/campaign_2pc/campaign.json) enthält Adresse und Raum von PC 1, die Phasen der Einzelrechner-Betriebsart mit Portbereichen und Stichproben (`phases`, `pilot`), die Mitschnitteinstellungen (`capture`) und den Abschnitt `network` für die Zweirechner-Betriebsart (Adresse von PC 2, Mitschnittfilter `host {provider_host} or udp port 3702`, Ringpuffer 6 Dateien, Zeitgrenzen). [`schedule_c.json`](../experiments/campaign_2pc/schedule_c.json) ist das gemeinsame Szenario beider Rechner: Phasen mit Rollen und Stichproben, Portbereiche je Phase (12100 ff., 12500 ff., 12600, 12700 ff., Rückkehr auf Port + 1000), Zeitkonstanten (D.3), Schranken je Verlustart, Zuordnung Rolle → Gerät. Der erzeugte Ordner `pc2_c/` enthält alles, was PC 2 braucht ([`harness.py`](../experiments/harness.py), Planer, Szenario, Provider, vier MDIBs, vier PKI-Ordner, [`SPEC_C.md`](../experiments/campaign_2pc/SPEC_C.md), [`HANDOFF_PC2.md`](../experiments/campaign_2pc/HANDOFF_PC2.md)).

## Zu Anhang D.4 — weitere Journale

### Runner-Ereignisse

Die Ereignisse der Runner stehen im selben Journal. In der Einzelrechner-Betriebsart schreibt sie der Runner selbst, in der Zweirechner-Betriebsart erzeugt sie `--merge` und markiert sie mit `synthetic: true`. Es sind `experiment_start`/`experiment_end`, `trial_start` (`trial`, `role`, `epr`, `port`, `mode`, `expected`) und `trial_end` (`outcome`, `provider_subscriptions`, `elapsed_s`) für Ziel 1, `ack_trial_start` (`trial`, `policy`, `epr`, `signal_handle`) und `ack_trial_end` (`press_id`, `ack_state`, `outcomes`, `confirmed`, `confirm_latency_ms`, `rtt_ms`) für die Quittierung, `fault_injected` (`trial`, `method`, `epr`, `t_loss`, `bound_s`), `reconnect_started` (`t_restart`, `port`) und `fault_trial_end` (`cause`, `end_status`, `dt_fallback_s`, `dt_alarm_s`, `dt_departed_s`, `escalated_at_loss`, `bound_s`, `outcome`, `dt_reconnect_s`, `reconnect_ok`) für die Fehlereinspeisung.

### Ereignisse des Messrahmens

Der Messrahmen ([`pc1_listener.py`](../experiments/campaign_2pc/pc1_listener.py)) schreibt in das Dashboard-Journal des Fensters: `listener_start`/`listener_end` (`window`, `reason`), `clock_offset` (`offset_s` = Uhr PC 2 − Uhr PC 1, `rtt_s`, `t1`, `t4`, Phase und Zustand von PC 2), `pc2_phase` bei jedem Wechsel, `auto_ack` (`epr`, `cond_keys`, `press_id`, `ack_state`, `outcomes`, `confirmed`, `confirm_latency_ms`, `rtt_ms`, `t_seen_abs`).

### Journal des Planers

Der Planer ([`pc2_scheduler.py`](../experiments/campaign_2pc/pc2_scheduler.py)) schreibt `pc2_journal.jsonl` auf der Uhr von PC 2 mit den Feldern `seq`, `t_abs`, `t_mono` und `iso` (lokal). Ereignisse: `scenario_start` (Adressen, Räume, Szenario als Kopie), `pc1_seen`, `phase_start`/`phase_end`, `window_switch`, `trial_start` (`phase`, `kind`, `trial`, `role`, `device`, `epr`, `port`, je nach Art `mode`/`expected`, `channel`, `method`, `bound_s`), `provider_ready`, `bound` (`subscriptions`, `after_s`), `status`, `ack_policy`, `alarm` (`trial`, `on`, `policy`, Handles), `fault_injected` (`t_loss`, `pid`, `bound_s`, bei `netdown` `rule_ok`), `fault_restore`, `reconnect_started`, `reconnect_status`, `netdown_rule`, `trial_end` (`outcome`, `provider_subscriptions`, `elapsed_s` oder `cycles`), `trial_skipped`, `phase_abort`, `phase_error`, `clock_reply`, `clock_error`, `scenario_end` (`aborted`, `trials_done`, `netdown_rules_left`). Vollständige Feldliste in [`experiments/campaign_2pc/SPEC_C.md`](../experiments/campaign_2pc/SPEC_C.md), Abschnitt 5.

### Zusammenführung

Die Zusammenführung ([`netmode.py`](../experiments/campaign_2pc/netmode.py), `merge`) erzeugt je Phase ein Journal `events.jsonl` im Runner-Format unter `ziel1_filtered/`, `ziel1_baseline/`, `ziel2_ack/`, `ziel2_fault/`, dazu `merge_stats.json` (Uhrenstatistik, Zyklen der Quittierung, Rückkehr nach `netdown`, nicht entdeckte Versuche, Hintergrundgeräte), je Fenster `capture_bytes.json` (Bytes nach Klassen und Rollen) und `trials_all.jsonl` (Zeitfenster aller Versuche für die Zuordnung des Mitschnitts). Zeitquellen: Dashboard und Planer `time.time()` der jeweiligen Uhr, umgerechnet nach (4.1). Mitschnitt: Zeitstempel des Npcap-Treibers (im Lauf vom 22.10.2026 rund 0,6 s vor der Uhr des Dashboards).

## Zu Anhang D — Struktur der Testinfrastruktur

Die Testinfrastruktur besteht aus Runnern und Orchestrator auf PC 1, dem Planer auf PC 2, gemeinsamen Hilfsklassen und der Auswertung. Die wichtigsten Klassen liegen so: `Prov`, `Dashboard` und `provider_cmd` in [`experiments/harness.py`](../experiments/harness.py), `Campaign` in [`campaign.py`](../experiments/campaign_2pc/campaign.py), `Clock` und die Funktionen in [`netmode.py`](../experiments/campaign_2pc/netmode.py), `ClockPoller` in [`pc1_listener.py`](../experiments/campaign_2pc/pc1_listener.py), `Scheduler`, `ClockResponder` und `Journal` in [`pc2_scheduler.py`](../experiments/campaign_2pc/pc2_scheduler.py).

## Zu Anhang E.1.2 — Verzeichnis der Daten auf PC 1

| Inhalt | Ort |
|---|---|
| Netzläufe, Journale von PC 1 | `experiments/ziel3_simulation/logs/ziel3_<lauf>/pc1/` |
| Netzläufe 12 h und A, Journale von PC 2 | `logs/ziel3_roc/netz_ziel3_12h/`, `logs/ziel3_roc_p07/netz_ziel3_4h_p07/` |
| Netzläufe B, C und D, Journale von PC 2 | `logs/ziel3_4h_p08_pc2/pc2/`, `logs/ziel3_4h_lang_pc2/pc2/`, `logs/ziel3_6h_ideal_pc2/pc2/` |
| ROC 0,9 und ROC 0,7, je $h$ ein Ordner | `logs/ziel3_roc/`, `logs/ziel3_roc_p07/`, verfeinertes Raster in `logs/ziel3_roc_dicht/`, `logs/ziel3_roc_p07_dicht/` |
| Prüfung 0,9 und Prüfung 0,7 | `logs/ziel3_roc_val09/`, `logs/ziel3_roc_val07/` |
| Ergebnisse je Netzlauf | [`docs/reports/results_<lauf>.json`](../docs/reports), `messwerte_<lauf>.json` |
| Auswertung über $h$ | `logs/ziel3_roc*/roc_alle.csv`, `roc_tabellen.json`, [`analysis/roc_ziel3/roc_dicht.json`](../analysis/roc_ziel3/roc_dicht.json) |
| Klassische ROC, Raster, Einzelvariation, Zerlegung | `logs/ziel3_roc*/kalibrierung/`, `werkstatt/`, `zeit_roc/` |

*Tabelle E.3 — Verzeichnis der Daten. `logs/` steht für `experiments/ziel3_simulation/logs/`.*

Die Journale von PC 2 zu den Läufen 1 h und 6 h liegen auf PC 1 nicht vor. Für diese beiden Läufe stehen auf PC 1 nur die fertigen Ergebnisdateien zur Verfügung. Auf PC 2 liegen die Journale aller Netzläufe unter `logs/ziel3_<lauf>/pc2` und die Ergebnisse als `results_ziel3_<lauf>.json` im Auswertungsordner (Auswertung von PC 2). Weil die Grenzen des Auswertungsfensters dort etwas anders liegen, zählt PC 2 in den Läufen A bis D zwei bis fünf Fehlalarme der Geräte weniger als Tabelle E.2, nämlich 3635, 3670, 3645 und 5468.

## Zu Anhang E.1.3 — Auswertung von PC 2

Die Auswertung von PC 2 ([`auswertung/ziel3_analyze.py`](../experiments/ziel3_simulation/auswertung/ziel3_analyze.py), [`auswertung/common.py`](../experiments/ziel3_simulation/auswertung/common.py)) rechnet alle Zeiten von PC 2 in die Zeit von PC 1 um und zählt in einem Fenster, das 5 s nach der letzten Anmeldung eines Geräts am Dashboard beginnt und 2 s vor dem Ende des Laufs endet. Gezählt werden die Krisen, die in diesem Fenster enden, sowie die Alarme der Geräte und die Eskalationen, die in diesem Fenster beginnen. Die Eskalationen des Filters stammen aus dem Journal des Dashboards, die der Zählregeln werden aus den Alarmen des Journals von PC 2 gebildet. Für den Uhrenabgleich verwirft PC 2 Messungen mit einem Umlauf von 20 ms oder mehr, bildet über die übrigen einen gleitenden Median über fünf Punkte und interpoliert dazwischen linear (Klasse `ClockMap`, Z. 174–200). Im Lauf 12 h sprang der Versatz 20-mal zwischen zwei Messungen um mehr als 50 ms, höchstens um 0,107 s. Diese Sprünge sind Korrekturen der Systemuhr, die der Median ausgleicht.

## Zu Anhang E.2.4 — vollständige Tabelle der Abstände

| $h$ | $\eta_j = 0{,}9$: je Bett und Stunde | erkannt | Abstand zur Mischung | Abstand zur Hülle | $\eta_j = 0{,}7$: je Bett und Stunde | erkannt | Abstand zur Mischung | Abstand zur Hülle |
|---:|---:|---:|---|---|---:|---:|---|---|
| 0,50 | 7,87 | 100,0 % | +0,7 [+0,0; +1,6] | +0,0 [+0,0; +0,0] | 7,73 | 99,3 % | +2,2 [+1,0; +3,6] | −0,1 [−1,0; +0,4] |
| 0,55 | 7,38 | 100,0 % | +0,7 [+0,0; +1,6] | +0,0 [+0,0; +0,0] | 7,28 | 99,3 % | +4,4 [+2,2; +6,5] | −0,1 [−1,0; +0,6] |
| 0,60 | 6,54 | 99,7 % | +0,9 [+0,0; +1,9] | −0,2 [−0,9; +0,1] | 6,43 | 97,7 % | +6,1 [+3,5; +8,9] | −0,7 [−2,3; +0,4] |
| 0,65 | 6,08 | 99,4 % | +0,6 [−0,6; +1,8] | −0,4 [−1,3; +0,1] | 6,06 | 96,7 % | +6,7 [+4,1; +9,6] | −1,1 [−2,8; +0,2] |
| 0,70 | 5,68 | 99,0 % | +0,5 [−0,9; +2,0] | −0,5 [−1,8; +0,1] | 5,65 | 95,1 % | +7,1 [+4,0; +10,2] | −2,0 [−4,1; −0,5] |
| 0,75 | 5,11 | 99,0 % | +1,6 [−0,0; +3,2] | −0,3 [−1,6; +0,2] | 5,05 | 91,5 % | +7,8 [+4,4; +11,0] | −4,6 [−7,3; −2,5] |
| 0,80 | 4,62 | 99,0 % | +2,1 [+0,4; +3,7] | −0,2 [−1,4; +0,4] | 4,60 | 89,5 % | +8,0 [+5,3; +10,4] | −5,8 [−9,0; −3,2] |
| 0,85 | 4,23 | 99,0 % | +2,6 [+0,9; +4,2] | −0,0 [−1,4; +0,6] | 4,25 | 88,2 % | +8,1 [+5,3; +10,5] | −6,5 [−10,2; −3,7] |
| 0,90 | 3,68 | 98,4 % | +2,3 [+0,6; +3,9] | −0,3 [−1,6; −0,0] | 3,74 | 87,9 % | +9,4 [+6,9; +12,0] | −5,2 [−8,7; −2,6] |
| 0,95 | 3,43 | 98,1 % | +2,1 [+0,4; +3,8] | −0,4 [−2,0; +0,0] | 3,51 | 86,9 % | +7,9 [+5,4; +10,5] | −4,8 [−8,8; −2,2] |
| 1,00 | 3,13 | 97,7 % | +1,8 [+0,0; +3,6] | −0,4 [−1,8; +0,4] | 3,19 | 86,2 % | +8,2 [+5,6; +11,1] | −3,3 [−7,5; −0,5] |
| 1,05 | 2,64 | 96,8 % | +2,0 [+0,5; +3,7] | −0,8 [−2,2; +0,5] | 2,71 | 85,6 % | +9,0 [+6,2; +12,0] | +1,1 [−2,1; +2,9] |
| 1,10 | 2,42 | 96,1 % | +1,7 [+0,4; +3,1] | −1,1 [−2,8; +0,5] | 2,48 | 84,9 % | +8,2 [+5,4; +11,2] | +2,9 [−0,9; +5,1] |
| 1,15 | 2,00 | 95,8 % | +1,8 [+0,5; +3,1] | +0,0 [−1,8; +1,5] | 2,05 | 83,3 % | +8,3 [+5,2; +11,3] | +6,1 [+2,3; +8,5] |
| 1,20 | 1,80 | 95,2 % | +1,6 [−0,5; +3,4] | +0,2 [−2,1; +2,0] | 1,87 | 80,3 % | +6,4 [+2,0; +10,4] | +5,3 [+0,4; +8,6] |
| 1,25 | 1,60 | 94,5 % | +1,6 [−0,9; +3,6] | +0,4 [−2,1; +2,4] | 1,67 | 77,4 % | +5,1 [+0,5; +9,5] | +4,7 [−0,1; +8,5] |
| 1,30 | 1,29 | 90,4 % | +2,0 [+0,3; +3,9] | −2,5 [−5,0; −0,5] | 1,34 | 72,1 % | +6,2 [+2,2; +9,8] | +3,3 [−1,4; +7,0] |
| 1,35 | 1,18 | 87,5 % | +1,2 [−0,6; +2,7] | −5,0 [−7,6; −2,8] | 1,23 | 69,5 % | +5,9 [+1,9; +10,1] | +2,0 [−2,7; +6,2] |
| 1,40 | 1,05 | 83,9 % | +1,6 [−0,9; +3,9] | −8,0 [−11,8; −4,7] | 1,09 | 67,9 % | +7,0 [+2,9; +10,7] | +2,0 [−2,5; +5,9] |
| 1,45 | 0,84 | 76,8 % | +5,4 [+3,0; +7,9] | −14,0 [−17,1; −11,1] | 0,89 | 60,0 % | +7,6 [+4,0; +10,9] | −3,5 [−7,7; +0,3] |
| 1,50 | 0,79 | 71,1 % | +5,8 [+2,1; +9,3] | −19,4 [−24,1; −15,1] | 0,83 | 56,1 % | +7,0 [+3,9; +10,0] | −6,7 [−10,4; −2,7] |
| 1,55 | 0,68 | 65,9 % | +9,2 [+5,2; +13,5] | −21,8 [−25,5; −18,3] | 0,69 | 53,1 % | +10,9 [+7,6; +14,4] | −6,0 [−10,1; −2,1] |
| 1,60 | 0,54 | 52,7 % | +6,7 [+2,5; +11,1] | −30,5 [−34,6; −26,5] | 0,56 | 43,3 % | +12,1 [+9,1; +15,1] | −11,4 [−16,6; −6,1] |
| 1,65 | 0,51 | 47,3 % | +7,0 [+3,0; +11,2] | −34,7 [−39,7; −30,3] | 0,52 | 36,1 % | +9,6 [+6,8; +12,3] | −16,7 [−22,0; −11,6] |
| 1,70 | 0,45 | 42,4 % | +7,0 [+3,6; +10,9] | −37,0 [−42,5; −32,5] | 0,50 | 30,5 % | +7,3 [+4,0; +10,2] | −21,5 [−27,1; −16,5] |
| 1,75 | 0,36 | 30,9 % | +6,8 [+3,5; +9,7] | −44,8 [−49,6; −40,2] | 0,43 | 21,0 % | +5,8 [+2,8; +8,4] | −26,2 [−32,7; −20,7] |
| 1,80 | 0,32 | 25,4 % | +6,2 [+3,3; +9,3] | −48,5 [−53,7; −43,3] | 0,36 | 18,4 % | +6,2 [+4,0; +8,4] | −23,3 [−30,1; −18,2] |
| 1,85 | 0,29 | 20,9 % | +4,8 [+1,6; +8,2] | −51,5 [−57,5; −45,6] | 0,34 | 15,4 % | +4,2 [+2,3; +6,0] | −24,3 [−31,7; −19,2] |
| 1,90 | 0,22 | 15,8 % | +4,8 [+1,7; +8,1] | −53,6 [−59,5; −47,3] | 0,29 | 11,1 % | +2,5 [+0,6; +4,5] | −24,6 [−31,8; −19,8] |
| 1,95 | 0,19 | 13,2 % | +3,3 [+0,5; +6,1] | −55,1 [−60,5; −49,1] | 0,25 | 9,5 % | +1,8 [−0,1; +3,8] | −23,0 [−29,7; −18,0] |
| 2,00 | 0,18 | 10,9 % | +2,0 [−0,2; +4,1] | −56,6 [−62,0; −49,4] | 0,23 | 7,9 % | +1,1 [−0,0; +2,6] | −23,0 [−29,7; −18,5] |
| 2,05 | 0,14 | 7,7 % | +0,7 [−0,9; +2,5] | −57,2 [−62,6; −49,4] | 0,19 | 5,9 % | +0,1 [−0,9; +1,0] | −22,0 [−29,3; −16,8] |
| 2,10 | 0,13 | 7,4 % | +0,5 [−1,0; +2,2] | −56,9 [−62,5; −48,6] | 0,19 | 4,9 % | −0,5 [−2,1; +0,7] | −23,0 [−29,9; −17,8] |
| 2,15 | 0,11 | 6,1 % | +0,2 [−0,9; +1,3] | −55,9 [−62,5; −45,1] | 0,16 | 3,9 % | −1,0 [−2,7; +0,4] | −22,0 [−28,2; −17,6] |
| 2,20 | 0,09 | 4,8 % | −0,2 [−1,6; +0,9] | −51,1 [−62,1; −41,1] | 0,14 | 3,9 % | −0,5 [−1,9; +0,7] | −20,3 [−26,1; −16,0] |

*Tabelle E.6 der ungekürzten Fassung — Fehleskalationen je Bett und Stunde und erkannte Krisen (mild) des Filters über alle 35 Werte von $h$ sowie sein Abstand zur Mischung aus 2-aus-4 und 3-aus-4 bei gleichem $h$ und zur oberen Hülle, in Prozentpunkten mit 95-%-Intervall (Bootstrap über die 20 Patienten, 1000 Wiederholungen).*

## Zu Anhang E.4.3 — McNemar-Test

Zur Kontrolle hat die Auswertung von PC 2 für die erkannten Krisen zusätzlich den exakten McNemar-Test gerechnet. Er verwendet nur die Krisen, die genau eine der beiden verglichenen Regeln erkennt, und behandelt die Krisen als unabhängig und nicht die Patienten. Tabelle E.14 zeigt das Ergebnis.

| Lauf | gegen 2-aus-4, mild | gegen 2-aus-4, streng | gegen 3-aus-4, mild | gegen 3-aus-4, streng |
|---|---:|---:|---:|---:|
| 12 h | 0 / 5 (0,063) | 0 / 24 (< 0,001) | 11 / 1 (0,006) | 7 / 16 (0,093) |
| B | 0 / 4 (0,125) | 0 / 12 (< 0,001) | 4 / 0 (0,125) | 2 / 6 (0,289) |
| A | 0 / 5 (0,063) | 0 / 11 (< 0,001) | 16 / 0 (< 0,001) | 15 / 4 (0,019) |
| 6 h | 0 / 10 (0,002) | 0 / 18 (< 0,001) | 30 / 0 (< 0,001) | 26 / 3 (< 0,001) |
| C | 0 / 0 (1,000) | 0 / 0 (1,000) | 2 / 0 (0,500) | 2 / 0 (0,500) |
| D | 0 / 0 (1,000) | 0 / 0 (1,000) | 4 / 0 (0,125) | 4 / 0 (0,125) |

*Tabelle E.14 — McNemar-Test in den Netzläufen (Auswertung von PC 2): Zahl der Krisen, die nur der Filter erkennt / die nur die Zählregel erkennt, in Klammern der p-Wert (exakt, zweiseitig).*

Der McNemar-Test führt zu denselben Aussagen wie der Bootstrap, mit vier Ausnahmen an der Grenze, alle mild gezählt: Den Rückstand auf 2-aus-4 in Lauf 12 h und Lauf B und den Vorsprung vor 3-aus-4 in Lauf B und Lauf D sichert der Bootstrap knapp, der McNemar-Test nicht (p = 0,063 und dreimal 0,125). In diesen Fällen unterscheiden sich die Regeln nur in vier oder fünf Krisen. In Lauf D fallen mild und streng zusammen, die Ausnahme gilt dort also für beide Zählungen.

## Zu Anhang G — Werkzeug und Kontrollwerte

Die Kurven werden mit der Anwendung [`analysis/belastungsmodell_app/app_ziel3.py`](../analysis/belastungsmodell_app/app_ziel3.py) berechnet (Streamlit, Modell in [`model_ziel3.py`](../analysis/belastungsmodell_app/model_ziel3.py), Abbildungen mit [`export_ziel3.py`](../analysis/belastungsmodell_app/export_ziel3.py)). Alle Parameter aus Tabelle G.1 sind dort einstellbar. Die Testsuite [`tests/test_model_ziel3.py`](../tests/test_model_ziel3.py) prüft, dass das Modell mit $P(D \ge t) \equiv 1$ die Werte aus Tabelle 5.7 wiedergibt:
- ARR: adaptiv 87,48 %, 2-aus-N 87,38 %, 3-aus-N 87,50 %.
- System-TPR: 99,63 %, 99,63 % und 94,77 %.
- 16,9 Fehlalarme je Bett und Tag beim adaptiven Modell.
- $t(F)$ = 15,41 s und 23,90 s.

Außerdem prüft sie die Werte aus Tabelle G.4.

## Zu Anhang G — Prüfung der Rechnung

Ob die Formeln unter ihren eigenen Annahmen richtig rechnen, wurde auf drei Wegen geprüft. Die Prüfung betrifft nur die Rechnung. Ob die Annahmen tragen, zeigt die Simulation am Prototyp.

Erstens prüft eine vollständige Aufzählung aller Teilmengen ARR und System-TPR. Die Testsuite [`tests/test_model_ziel3.py`](../tests/test_model_ziel3.py) hält die Werte der Median-Intensivstation fest: ARR 87,48 %, 87,38 % und 87,50 % für adaptiv, 2-aus-N und 3-aus-N, System-TPR 99,63 %, 99,63 % und 94,77 %, 16,9 Fehlalarme je Bett und Tag beim adaptiven Modell und die Zeiten 15,41 s und 23,90 s aus Tabelle G.2. Sie stimmen mit Tabelle 5.7 überein.

Zweitens prüft eine Monte-Carlo-Rechnung die Zählung der Episoden nach (G.7). Sie erzeugt Alarme genau nach den Annahmen des Modells: unabhängige Fehlalarme mit exponentialverteilter Dauer (Mittelwert 8 s), gemeinsame Stöße aller Kanäle ($c_{art} = 0{,}1$), Prioritäten (3, 3, 2, 1), Schrittweite 0,2 s und 2000 Bettstunden je Punkt. Tabelle G.10 zeigt den Vergleich für die Zählregeln.

| $FPR_j$ | Regel | Monte-Carlo je Bett und Stunde | Formel (G.7) | Abweichung der Formel |
|---:|---|---:|---:|---:|
| 0,008 | 2-aus-N | 0,650 ± 0,018 | 0,636 | −2,2 % |
| 0,008 | 3-aus-N | 0,370 ± 0,014 | 0,362 | −2,3 % |
| 0,015 | 2-aus-N | 1,604 ± 0,028 | 1,633 | +1,8 % |
| 0,015 | 3-aus-N | 0,693 ± 0,019 | 0,688 | −0,6 % |
| 0,026 | 2-aus-N | 3,877 ± 0,044 | 3,990 | +2,9 % |
| 0,026 | 3-aus-N | 1,296 ± 0,025 | 1,238 | −4,5 % |

*Tabelle G.10 — Monte-Carlo-Prüfung der Episodenzählung für die Zählregeln, Mittelwert und Standardfehler.*

Die Formel trifft die Monte-Carlo-Rechnung für die Zählregeln auf höchstens 4,5 %. Die Spalte der Formel ist hier ohne Krisen gerechnet, also mit $P(C) \to 0$. Mit dem Zeitanteil der Krisen liegt (G.7) rund 1 % niedriger. Die Auswertung von PC 2 erhält so für Lauf D 3,94 statt 3,99 für 2-aus-4 und 1,22 statt 1,24 für 3-aus-4. Für das adaptive Modell ist (G.7) eine obere Grenze, weil die Zählung nicht berücksichtigt, dass auch eine sofort eskalierende Teilmenge erst nach dem Füllen des Fensters $T$ eskaliert. Bei der Fehlalarmrate von Lauf D ($FPR_j = 0{,}026$) ergibt die Formel 2,61 Fehleskalationen je Bett und Stunde. Der Modellwert 1,39 in Tabelle 5.10 stammt aus einer Monte-Carlo-Rechnung mit dem vollständigen Entscheidungsmodell, die Anhang E.4.4 beschreibt.

Drittens prüft Lauf D dasselbe am laufenden Prototyp (Anhang E.4.4). Für die erkannten Krisen in Lauf D gilt (G.8) mit den Prioritäten (3, 3, 2, 1) der Simulation: 99,4 % für das adaptive Modell, 99,6 % für 2-aus-4 und 94,8 % für 3-aus-4.

# Weitere Kürzungen

## Zu Anhang A.8 — Zahlenbeispiel zur quadratischen Unterdrückung

Ein Zahlenbeispiel: Ein Bettplatz hat vier Kanäle, jeder ist 0,3 % der krisenfreien Zeit fälschlich aktiv, und die Hälfte davon ist unabhängig ($c_{art} = 0{,}5$, also $q_i = 0{,}0015$). Am Bett sind dann im Mittel $4 \cdot 0{,}0015 = 0{,}006$ unabhängige Fehlalarme gleichzeitig aktiv. Davon erreichen nur $\binom{4}{2} \cdot 0{,}0015^2 \approx 0{,}0000135$ die Zentralstation, rund 440-mal weniger.

## Zu Anhang A.8 — Tabelle A.2 (Verschiebung der abgesunkenen Schwelle durch den Kontext)

| Odds Ratio | $\ln(OR)$ | abgesunkene Schwelle | einzelner Kanal (Evidenz 5,66) |
|---:|---:|---:|---|
| 1 (neutral) | 0,00 | 8,49 | eskaliert nicht |
| 2 | 0,69 | 7,80 | eskaliert nicht |
| 2,5 | 0,92 | 7,58 | eskaliert nicht |
| 3,1 | 1,13 | 7,36 | eskaliert nicht |
| 4 | 1,39 | 7,11 | eskaliert nicht |
| 5 | 1,61 | 6,88 | eskaliert nicht |
| 17 | 2,83 | 5,66 | eskaliert |

*Tabelle A.2 — Verschiebung der vollständig abgesunkenen Schwelle eines einzelnen Kanals hoher Priorität durch den Kontext. Die Werte 2,5, 3,1, 4 und 5 sind die Odds Ratios der Konfiguration des Prototyps.*

## Zu Anhang A.9 — weitere Zeilen von Tabelle A.3

| Größe | Bezug | Rechnung | Wert |
|---|---|---|---|
| Dauer eines Tages | Zeit | | 1440 min (86 400 s) |
| Positiver Vorhersagewert $\text{PPV}_j = 1 - \text{FDR}_j$ | Anzahl | 13 / (13 + 38,5) | ≈ 0,25 (25 %) |

## Zu Anhang A.9 — weitere Zeilen von Tabelle A.3

| Größe | Bezug | Rechnung | Wert |
|---|---|---|---|
| **Fehlalarmrate je Quelle $FPR_j$** | **Zeitanteil der krisenfreien Zeit** | **38,5 min / 1425,6 min** | **≈ 0,027 (2,7 %)** |

## Zu Anhang B.1 — vollständige Tabelle B.1 mit dem Befund vor der Korrektur

| Nr. | Modellstelle | Befund vor der Korrektur | Änderung | Tests |
|---|---|---|---|---|
| 1 | (3.19) | Die Kontextverschiebung wurde vor dem Zerfallsmultiplikator abgezogen. War sie größer als die Basisschwelle, hob das Sinken von $\delta$ die Schwelle an, statt sie zu senken. | Reihenfolge nach (3.19): erst $\Theta_{base}\cdot\delta$, dann $-\,Context\_Log\_Odds$. | L1–L3, F1, F2 |
| 2 | (3.20) | Ohne Untergrenze strebte $\delta$ gegen null. Jede anhaltende schwache Evidenz eskalierte irgendwann. | Untergrenze $\delta_{min} = 0{,}5$. | L4, L5, J2, J5 |
| 3 | (3.19) | Ohne Boden konnte ein starker Kontext die Schwelle auf null oder darunter senken. Das Ensemble blieb dann dauerhaft eskaliert. | Boden $\epsilon(t) = \min_{j \in M} w_j$. | L6, L7 |
| 4 | (3.17) | $k_{min}$ wurde abgerundet. Das Quorum war systematisch weicher als beschrieben. | Kaufmännische Rundung (half-up). | L8–L10 |
| 5 | (3.14) | Kanäle ohne Zuverlässigkeitsprofil gingen mit $w_j = 0$ in $M$ ein, senkten $\bar w$ und damit die Schwelle. | Zulassungsregel $0 < FPR_j < TPR_j$. Abgewiesene Kanäle bleiben am Gerät sichtbar. | 5a–5e |
| 6 | (3.6) | Das Ende eines Alarms erreichte den Kern erst mit dem nächsten Ereignis. Ein kurzer Alarm zählte bis dahin als aktiv. | Auch das Ende eines Alarms löst eine Auswertung aus. | 6a–6f |
| 7 | (3.16) | Der Code verwendete $\alpha = 0{,}7$, die Arbeit $\alpha = 0{,}5$. | $\alpha = 0{,}5$ in Code und Konfiguration. | L11, L12 |
| 8 | 3.1.5.2 | Verließ ein Gerät ein Ensemble, blieben seine Kanäle in $M$. $|M|$, $\bar w$ und $k_{min}$ waren falsch. | Kanalmenge wird beim Abgang neu gebildet. Die Registrierung der Geräte ist maßgeblich. | 8a, 8b |
| 9 | 3.1.5.2 | Der Abgang eines Geräts erreichte die Übersicht nicht: Die Karte blieb stehen, nach einem Neustart in einem anderen Raum erschien derselbe Patient mehrfach. Alarme des Geräts blieben bis zu 10 s im Zwischenspeicher. | Der Abgang wird an die Übersicht gemeldet. Die Alarme des Geräts werden entfernt. | `test_departure_overview` |
| 10 | 3.1.5.5, A.4 | Ausgewertet wurde nur bei Alarmereignissen. Ein Ensemble mit fast gleichzeitig einschaltenden Quellen wurde nie mit gefülltem Fenster ausgewertet. | Zusätzliches Auswertungsraster im Abstand $\Delta_{eval}$. | P1–P5 |
| 11 | 3.1.4 | Ein anhaltender, nicht erneut gemeldeter Alarm fiel nach 10 s aus der Auswertung. | $a_j$ wird bei jeder Auswertung aus dem aktuellen Abbild des Geräts gelesen. Der Zwischenspeicher dient nur noch als Sicherheitsnetz. | Q1–Q4, Q9 |
| 12 | 3.1.5.6 | Ein gehaltenes Ensemble wurde nicht weiter ausgewertet. | Der Kern rechnet während des Haltens weiter. Nur die Entscheidung wird gehalten. | Q5, Q6, P3b |
| 13 | 3.1.5.2 | Kanäle wurden nur über die Alarmbedingung identifiziert. Zwei gleichartige Geräte fielen zu einem Kanal zusammen. | Kennung aus Gerät und Alarmbedingung. | Q7, Q8 |
| 14 | Ziel 1 (Kapitel 1) | Geräte wurden abonniert, bevor ihr Raum geprüft war. | Raumfilter in der Erkennungsphase. Sonst Prüfung des Raums über die gesicherte Verbindung vor dem ersten Abonnement. | R1–R3, R6, R7 |
| 15 | Ziele 1–3 (Kapitel 1) | Die Kennzahlen der Ziele wurden nicht erfasst. Das Paket für die Zähler fehlte, alle Aufzeichnungen liefen ins Leere. | Ereignisprotokoll für Topologiefilterung, Latenzen und Entscheidungen des Filters. | R4, R5 |
| 16 | Ziel 2 (Kapitel 1) | Eine vom Gerät abgelehnte Quittierung galt als Erfolg. Ohne passendes Signal wurde ersatzweise das erste verfügbare quittiert. | Ergebnis der Operation wird ausgewertet und angezeigt. Quittiert werden nur aktive Signale mit eigener Operation. | `test_ack_watchdog` (K1–K4) |
| 17 | Ziel 2 (Kapitel 1), 3.1.5.6 | Ein Verbindungsverlust führte nur zum stillen Entfernen des Geräts. Das Personal sah nichts. | Technische Alarmbedingung bei Verbindungsverlust, mit dem Eskalationszustand im Moment des Verlusts. | `test_connection_lost` (L1–L5) |
| 18 | Ziel 2 (Kapitel 1) | Die Konstanten der Verbindungsüberwachung waren lokal im Code verteilt, ihre Zeitschranke nicht festgehalten und nicht getestet. | Konstanten zusammengeführt. Schranke $T_{FALLBACK} + MAX_{MISSED} \cdot \tau_{sock} = 30$ s dokumentiert und geprüft. | `test_watchdog` (W1–W4) |

## Zu Anhang B.2 — geschlossene und zurückgezogene Einträge von Tabelle B.2

| Kennung | Abweichung | Modellstelle | Wirkung | Status |
|---|---|---|---|---|
| SD-1 | Bei $|M| = 2$ ist $k_{min} \equiv 2$. Nach vollständigem Sinken der Schwelle ($\delta = \delta_{min}$) liegt sie bei $\bar w$. Eine Quelle mit $w_j \ge \bar w$ kann dann allein eskalieren. | (3.17), (3.20), A.7 | erleichtert | keine Abweichung, Eigenschaft der Formeln |
| SD-2 | Alarme eines abgegangenen Geräts blieben im Zwischenspeicher. | 3.1.5.2 | nur Anzeige | geschlossen (Korrektur 9) |
| SD-8 | Klemmung $k_{min} \ge 2$ bei $|M| = 1$. | 3.1.5.2 | — | zurückgezogen (setzt die Mindestgröße um, siehe SD-18) |
| SD-9 | Ein anhaltender, nicht erneut gemeldeter Alarm fiel nach 10 s aus der Auswertung. | 3.1.4, 3.1.5.6 | erschwert | geschlossen (Korrektur 11) |
| SD-11 | Gleichartige Geräte fielen zu einem Kanal zusammen. | 3.1.5.2 | erschwert | geschlossen (Korrektur 13) |

## Zu Anhang C.1 — Absatz zum Abgleich

Der zweite Weg war der Abgleich. Dabei wurde jede Formel des Kerns mit dem Modell verglichen und geprüft, ob die in Kapitel 1 zugesagten Funktionen vorhanden sind. Daraus ergaben sich 18 Korrekturen, unter anderem an der Schwellenbildung, am Auswertungsraster, an der Bildung der Kanalmenge und an den Funktionen zu den Zielen 1 und 2. Jede ist mit Modellstelle, Änderung und Tests dokumentiert (Anhang B).

## Zu Anhang D.1.1 — Betriebsarten von make_testbed_pki.py

Betriebsarten: Ausstellen (`--ip IP ...`, `--out`, `--force`), Nachstellen eines Teilnehmers (`--add NAME --role provider|consumer`), Prüfen (`--verify`) und Selbsttest eines beidseitigen TLS-Handshakes über `localhost` (`--selftest --server NAME --client NAME`).

## Zu Anhang D.1.2 — weitere Optionen des Providers (Tabelle D.2)

| Option | Standard | Bedeutung |
|---|---|---|
| `--patient-root URI` | feste `urn:uuid` | `InstanceIdentifier/@Root` der Patientenkennung |
| `--serial` | `0001` | DPWS-Seriennummer |
| `--no-tls` | aus | reines HTTP, nur zur Fehlersuche. Schließt `--certs-dir` aus |
| `--ip IP` | erster Adapter | zu bindende Adresse |
| `--ctx-patient`, `--ctx-location`, `--ctx-ensemble`, `--ctx-workflow` | aus der MDIB | Handles der Kontextdeskriptoren überschreiben |

## Zu Anhang D.1.2 — Kommandos auf der Standardeingabe

Kommandos auf der Standardeingabe: `value <Kanal> <Zahl>` (festen Wert veröffentlichen), `rest <Kanal>` (zurück in den Ruhemodus, Anfangswert ±2 %), `alarm <Kanal> on|off` (Präsenz von Alarmbedingung und Alarmsignal mit `DeterminationTime`), `ack accept|refuse`, `selfcheck off|on` (Aktualisierung von `LastSelfCheck` aller Alarmsysteme), `status` (Werte, Modus, Alarme aller Kanäle, Politik, Zahl der Abonnements), `shutdown` (geordnet: `SubscriptionEnd`, `Bye`, auch bei Ctrl-C und Ende der Eingabe). Ausgabe: `ready: ...` nach dem Start, `publish: ...` einmal pro Sekunde mit allen Kanalwerten, `<<< SetAlertState ...` bei jeder empfangenen Operation mit der Entscheidung, `status: ack=... subscriptions=N`. Die Startreihenfolge legt die Kontextzustände vor der ersten Ankündigung an, damit schon das erste `Hello` den Standort-Scope trägt.

## Zu Anhang D.1.3 — Tabelle D.3, Optionen der Programme der Kampagne

| Programm | Optionen (Standard) |
|---|---|
| [`campaign.py`](../experiments/campaign_2pc/campaign.py) (PC 1) | `--config` ([`campaign.json`](../experiments/campaign_2pc/campaign.json)), `--pilot`, `--resume [LAUF]`, `--report-only [LAUF]`, `--run-dir`, `--phases a,b`, `--no-capture`, `--dry-run`, Zweirechner-Betriebsart: `--listen`, `--merge [LAUF] --pc2 ORDNER[;ORDNER]`, `--selftest-merge` |
| [`pc1_listener.py`](../experiments/campaign_2pc/pc1_listener.py) (PC 1, von `--listen` gestartet) | `--window filtered\|baseline`, `--until baseline\|done`, `--ip`, `--room` (`ICU-1`), `--pc2`, `--clock-port` (5558), `--clock-poll-s` (10), `--log`, `--tls`, `--confirm-timeout` (5 s), `--wait-start-min` (60), `--idle-exit-min` (30), `--max-hours` (10), `--dry-run` |
| [`pc2_scheduler.py`](../experiments/campaign_2pc/pc2_scheduler.py) (PC 2) | `--schedule` ([`schedule_c.json`](../experiments/campaign_2pc/schedule_c.json)), `--ip`, `--pc1`, `--log-dir`, `--pilot`, `--selftest`, `--dry-run`, `--phases`, `--netdown-mode firewall\|skip`, `--wait-pc1-min` (30), `--no-echo-provider`, `--netdown-clear`, `--netdown-test SEKUNDEN` |
| [`build_pc2_c.py`](../experiments/campaign_2pc/build_pc2_c.py) | keine. Das Programm erzeugt den Ordner `pc2_c/` für PC 2 (nicht im Repositorium) |

*Tabelle D.3 — Optionen der Programme der Kampagne, Standardwerte in Klammern.*

## Zu Anhang D.2 — Tabelle D.4, Alarmkanäle der Testgeräte

| Gerät (`Mds`) | Alarmbedingung | Priorität | Typ (SNOMED CT) | Quelle (Metrik) | LOINC | Einheit | Alarmsignal | Operation |
|---|---|---|---|---|---|---|---|---|
| `ecg` (`ecg_mds`) | `ecg_alert_cond_tachycardia` | Hi | 3424008 | `ecg_metric_hr` | 8867-4 | /min | `ecg_alert_signal_tachycardia` | `ecg_op_ack_tachycardia` |
| | `ecg_alert_cond_tachypnea` | Me | 271823003 | `ecg_metric_rr` | 76270-8 | /min | `ecg_alert_signal_tachypnea` | `ecg_op_ack_tachypnea` |
| `spo2` (`spo2_mds`) | `spo2_alert_cond_desaturation` | Me | 449171008 | `spo2_metric_spo2` | 59408-5 | % | `spo2_alert_signal_desaturation` | `spo2_op_ack_desaturation` |
| | `spo2_alert_cond_tachycardia` | Me | 3424008 | `spo2_metric_pulse` | 8889-8 | /min | `spo2_alert_signal_tachycardia` | `spo2_op_ack_tachycardia` |
| `abp` (`abp_mds`) | `abp_alert_cond_hypotension` | Hi | 45007003 | `abp_metric_map` | 8478-0 | mm[Hg] | `abp_alert_signal_hypotension` | `abp_op_ack_hypotension` |
| | `abp_alert_cond_sys_low` | Me | 81010002 | `abp_metric_sys` | 8480-6 | mm[Hg] | `abp_alert_signal_sys_low` | `abp_op_ack_sys_low` |
| | `abp_alert_cond_dia_low` | Lo | 42689008 | `abp_metric_dia` | 8462-4 | mm[Hg] | `abp_alert_signal_dia_low` | `abp_op_ack_dia_low` |
| | `abp_alert_cond_tachycardia` | Me | 3424008 | `abp_metric_pr` | 60978-4 | /min | `abp_alert_signal_tachycardia` | `abp_op_ack_tachycardia` |
| `vent` (`vent_mds`) | `vent_alert_cond_rr_high` | Me | 271823003 | `vent_metric_rr` | 9279-1 | /min | `vent_alert_signal_rr_high` | `vent_op_ack_rr_high` |
| | `vent_alert_cond_ppeak_high` | Me | 405495005 | `vent_metric_ppeak` | 19976-0 | cm[H2O] | `vent_alert_signal_ppeak_high` | `vent_op_ack_ppeak_high` |
| | `vent_alert_cond_vt_low` | Lo | 31515003 | `vent_metric_vt` | 76007-4 | mL | `vent_alert_signal_vt_low` | `vent_op_ack_vt_low` |
| | `vent_alert_cond_mv_low` | Hi | 31515003 | `vent_metric_mv` | 76008-2 | L/min | `vent_alert_signal_mv_low` | `vent_op_ack_mv_low` |

*Tabelle D.4 — Alarmkanäle der Testgeräte. Kontextoperationen: `<name>_op_set_ensemble_ctx` → `<name>_ctx_ensemble`, `<name>_op_set_workflow_ctx` → `<name>_ctx_workflow`. Die beiden Beatmungskanäle `vt_low` und `mv_low` tragen denselben SNOMED-CT-Code (31515003).*

## Zu Anhang D.3 — weitere Zeilen von Tabelle D.5 (Zeitkonstanten)

| Konstante | Wert | Fundstelle | Wirkung |
|---|---|---|---|
| Abmeldung | 5 s | [`handler.py:1466`](../Dashboard/device/handler.py#L1466) | Timeout des geordneten `Unsubscribe` nach einem Verlust |
| TLS-Wechsel | einmalig | [`handler.py:362–363`](../Dashboard/device/handler.py#L362-L363) | im Modus `auto` bei zurückgesetzter Verbindung |
| `RECONNECT_COOLDOWN_SEC` | 15 s | [`Dashboard/app/sdcMyConsumer.py:95`](../Dashboard/app/sdcMyConsumer.py#L95) | Sperre für das Neuverbinden einer verlorenen EPR |
| `ADAPTIVE_DISCARD_GRACE_SEC` | 10 s | [`smartAlertAggregator.py:128`](../Dashboard/app/alarms/smartAlertAggregator.py#L128) | Entfernen einer Kerninstanz nach dem Zurücksetzen (3.2.4.1) |
| Veröffentlichung des Providers | 1 s | [`testbed/providers/testbed_provider.py:906`](../testbed/providers/testbed_provider.py#L906) | alle Kanalwerte einmal pro Sekunde |
| `Bye`-Wartezeit | 0,5 s | [`testbed_provider.py:916`](../testbed/providers/testbed_provider.py#L916) | nach `shutdown` |
| `ready_timeout_s` | 40 s | [`schedule_c.json`](../experiments/campaign_2pc/schedule_c.json) `timing` | Warten auf `ready:` des Providers |
| `bind_timeout_s` / `bind_settle_s` | 90 s / 3 s | [`schedule_c.json`](../experiments/campaign_2pc/schedule_c.json) | Warten auf `subscriptions ≥ 1` und Beruhigung vor einem Eingriff |
| `ack_settle_s`, `ack_hold_s`, `ack_pause_s` | 5 s, 4 s, 2 s | [`schedule_c.json`](../experiments/campaign_2pc/schedule_c.json) | Quittierungszyklus |
| `suspend_hold_s`, `netdown_hold_s`, `netdown_watchdog_extra_s` | 45 s, 45 s, 15 s | [`schedule_c.json`](../experiments/campaign_2pc/schedule_c.json) | Dauer des stillen Verlusts, Nachlauf des Wächters |
| `reconnect_wait_s`, `reconnect_hold_s` | 20 s, 40 s | [`schedule_c.json`](../experiments/campaign_2pc/schedule_c.json) | Wartezeit bis zum Neustart, Beobachtung der Rückkehr |
| `clock_poll_s`, `clock_port` | 10 s, 5558 | [`schedule_c.json`](../experiments/campaign_2pc/schedule_c.json), [`pc1_listener.py`](../experiments/campaign_2pc/pc1_listener.py) | Uhrenabgleich |
| Abfrage der Alarmzustände | 0,25 s | [`pc1_listener.py`](../experiments/campaign_2pc/pc1_listener.py) (Hauptschleife) | automatische Quittierung |
| `linger_s` | 600 s | [`schedule_c.json`](../experiments/campaign_2pc/schedule_c.json) | Antwortzeit des Planers nach dem Ende |

## Zu Anhang D.4 — Tabelle D.7, Ereignisse des Dashboard-Journals

| Ereignis | Felder | Quelle |
|---|---|---|
| `device_discovered` | `epr`, `scopes`, `room` | [`app/metrics/topology_metrics.py`](../Dashboard/app/metrics/topology_metrics.py) (einmal je EPR und Raum) |
| `topology_decision` | `epr`, `decision` (`accept`/`reject`/`unknown`), `phase` (`discovery`/`context_precheck`/`mdib`/`no_filter`), `reason`, `room`, `target_room` | ebd. (nur bei Änderung) |
| `device_accepted` | `epr`, `phase`, `subscriptions` | ebd. |
| `subscription_created` | `epr`, `phase`, `n_actions` | ebd. |
| `subscription_avoided` | `epr`, `phase`, `reason` | ebd. |
| `scope_mismatch` | `epr`, `scope_room`, Kontextraum, `target_room` | ebd. |
| `device_departed` | `epr`, `cause` | ebd. |
| `fallback` | `epr`, `cause`, `since_rx_s`, `end_status`, `end_reason` | [`app/metrics/latency_metrics.py`](../Dashboard/app/metrics/latency_metrics.py) |
| `technical_alarm` | `epr`, `action` (`raised`/`cleared`/`dismissed`), `cause`, `patient_id`, `room`, `ensemble`, `escalated_at_loss`, `lost_since_abs` | ebd. |
| `ack_rtt` | `epr`, `handle`, `operation_handle`, `press_id`, `purpose` (`ack`/`reraise`), `rtt_ms`, `ok`, `outcome`, `invocation_state`, `transaction_id`, `t_request_abs` | ebd. |
| `ack_confirmed` | `epr`, `handle` | ebd. |
| `topology_summary`, `latency_summary`, `arr_summary`, `verdict` | Zusammenfassungen und Modellurteile | [`topology_metrics.py`](../Dashboard/app/metrics/topology_metrics.py), [`latency_metrics.py`](../Dashboard/app/metrics/latency_metrics.py), [`arr_metrics.py`](../Dashboard/app/metrics/arr_metrics.py) |

*Tabelle D.7 — Ereignisse des Dashboard-Journals. Zeiten in Sekunden, Umlaufzeiten in Millisekunden.*

## Zu Anhang D.5 — Tabelle D.8, TCP-Verkehr je Versuch und Rolle

| Rolle | Phase | Versuche | Bytes je Versuch | davon Port des Providers | davon Berichte |
|---|---|---:|---:|---:|---:|
| *foreign* | mit Filter | 150 | 0 | 0 | 0 |
| *mismatch* | mit Filter | 150 | 460 556 | 460 556 | 0 |
| *own* | mit Filter | 5 | 605 081 | 505 758 | 99 322 |
| *nolocation* | mit Filter | 5 | 662 341 | 505 330 | 157 011 |
| *foreign* | ohne Filter | 20 | 601 042 | 502 981 | 98 061 |
| *mismatch* | ohne Filter | 20 | 665 183 | 507 754 | 157 429 |
| *own* | ohne Filter | 2 | 601 467 | 501 963 | 99 505 |
| *nolocation* | ohne Filter | 2 | 664 465 | 502 029 | 162 436 |

*Tabelle D.8 — TCP-Verkehr je Versuch und Rolle im Lauf vom 22.10.2026, Mittelwerte in Bytes.*

## Zu Anhang E.1.1 — weitere Parameter des Generators (Tabelle E.1)

| Anteil | Parameter | Wert |
|---|---|---|
| Zeitschritt | Schrittweite | 1 s |
| Drift | Ornstein-Uhlenbeck-Prozess, Zeitkonstante und Standardabweichung | 480 s und 0,10 |
| Rauschen | autoregressiver Prozess erster Ordnung, Zeitkonstante und Standardabweichung | 20 s und 0,09 |
| Ausreißer | Anstieg und Zeitkonstante des Abklingens | 1 bis 2 s und 6 bis 13 s |
| Ausreißer | Median der Höhe, Anteil in Richtung der Alarmschwelle | 1,0 und 85 % |
| Bewegung | Anteil in Richtung der Alarmschwelle, Zeitkonstante des Abklingens | 85 % und 2 s |
| Krise | Höhe bei einem nicht anzeigenden Gerät | 0,3 bis 0,75 |
| Krise | Zeitkonstante der Rückkehr nach dem Ende | 8 s |

## Zu Anhang E.1.1 — Zufallszahlen im Einzelnen

Die Zufallszahlen (4.4.4.2) liefert der Standardgenerator von NumPy (`numpy.random.default_rng`, PCG64). Aus dem Startwert eines Laufs wird eine `SeedSequence` gebildet und in 21 Teilfolgen geteilt, eine je Patient und eine für die Krisen (Auswertung von PC 2: [`pc2/patient.py`](../experiments/ziel3_simulation/pc2/patient.py), Funktion `make_seed_sequences`, Z. 348–352, verwendet in `Patient.__init__`, Z. 241, und in [`pc2/szenario.py`](../experiments/ziel3_simulation/pc2/szenario.py), Z. 75). Die Folge eines Patienten hängt damit nicht von den anderen Patienten ab, und derselbe Startwert liefert bei jedem Wert von $h$ dieselben Auslenkungen.

## Zu Anhang E.2.1 — Umsetzung der Zählweisen

Die Zählweisen sind in 4.4.4.3 festgelegt. Dieser Abschnitt hält fest, wie sie in der Auswertung umgesetzt sind. Jede Krise wird um 10 s davor und danach erweitert. Ein Gerätealarm oder eine Eskalation ist falsch, wenn sie keine so erweiterte Krise berührt. Eine Krise ist erkannt (mild), wenn eine Eskalation die Zeit vom Beginn der Krise bis 10 s nach ihrem Ende berührt, und erkannt (streng), wenn die Eskalation vor dem Ende der Krise beginnt. Die Zählregeln eskalieren, sobald mindestens $k$ Geräte eines Patienten alarmieren, und halten die Eskalation, bis alle Alarme des Patienten beendet sind.

## Zu Anhang E.2.2 — Abbildung des Ablaufs

Die Abbildung ist in dieser Fassung nicht enthalten.

## Zu Anhang E.2.2 — Zählregeln ohne Netz

Für die Zählregeln gibt es keinen Takt, sie werden unmittelbar aus den Alarmen gebildet. Ohne Netz ergeben sie bei $h = 1$ 0,8 und 0,4 % weniger Fehleskalationen als im Lauf 12 h (2-aus-4: 1397 gegenüber 1408, 3-aus-4: 483 gegenüber 485) und gleich viele mild erkannte Krisen (309 und 294). Die Auswertung von PC 2 kommt auf dieselben Zahlen. Der Unterschied entsteht nach ihr, weil ohne Netz auf dem Zeitraster des Generators gezählt wird und im Netzlauf mit den umgerechneten Zeiten des Dashboards.

## Zu Anhang E.2.3 — Beispiel 4-aus-4 und Tabelle E.4

Ein Beispiel zeigt den Unterschied: 4-aus-4 eskaliert bei $h = 0{,}2$ nur 3,6 % der Episoden, der Filter bei $h = 1$ dagegen 8,6 %. Trotzdem ruft 4-aus-4 das Personal dort häufiger ohne Grund, nämlich 4,1-mal je Bett und Stunde gegenüber 3,1-mal. Für die Arbeit des Personals zählt die zweite Zahl. Tabelle E.4 nennt für den Filter beide Größen bei den drei Werten von $h$ aus Tabelle 5.9.

| $\eta_j$ | $h$ | Fehlalarm-Episoden | eskalierte Episoden des Filters | Fehleskalationen je Bett und Stunde |
|---:|---:|---:|---:|---:|
| 0,9 | 0,8 | 10 668 | 10,40 % | 4,62 |
| 0,9 | 1,0 | 8715 | 8,62 % | 3,13 |
| 0,9 | 1,2 | 6273 | 6,85 % | 1,80 |
| 0,7 | 0,8 | 10 779 | 10,24 % | 4,60 |
| 0,7 | 1,0 | 8740 | 8,74 % | 3,19 |
| 0,7 | 1,2 | 6227 | 7,18 % | 1,87 |

*Tabelle E.4 — Fehlalarm-Episoden und Fehleskalationen des Filters bei drei Werten von $h$.*

## Zu Anhang E.2.5 — Vergleich der Kurven Punkt für Punkt

Gegenüber der Kurve von 3-aus-4 sind bei beiden Anzeigewahrscheinlichkeiten 29 Punkte vergleichbar. Bei $\eta_j = 0{,}9$ liegt der Filter in 21 davon unter der Kurve, in einem gleichauf und in sieben darüber, nämlich für $h$ zwischen 0,8 und 1,2 um höchstens 0,6 Prozentpunkte. Bei $\eta_j = 0{,}7$ liegt er in 22 Punkten darunter und in sieben darüber, für $h$ = 1,0 bis 1,3 um 0,5 bis 7,8 Prozentpunkte. Ab $h$ = 1,3 beziehungsweise 1,35 liegt er zunehmend darunter. Gegenüber der Kurve von 4-aus-4 liegt er in allen 14 vergleichbaren Punkten darunter, gegenüber der Kurve von 2-aus-4 in allen vergleichbaren Punkten darüber.

## Zu Anhang E.3.1 — Abbildung zur Verschiebung der Zielschwelle

Die Abbildung ist in dieser Fassung nicht enthalten.

## Zu Anhang E.3.3 — weitere Zeilen von Tabelle E.9

| Vergleich | beste Konfiguration des Rasters | Prototyp |
|---|---:|---:|
| ROC 0,9: Fläche unter der Kurve über $h$ | 0,940 | 0,958 |
| ROC 0,7: Fläche unter der Kurve über $h$ | 0,922 | 0,942 |
| Alarme des Laufs 12 h: Fehleskalationen je Bett und Stunde | 4,13 | 3,12 |
| Alarme des Laufs A | 4,43 | 3,35 |
| Alarme des Laufs B | 4,14 | 3,11 |
| Alarme des Laufs C | 3,96 | 3,16 |
| Alarme des Laufs D | 1,44 | 1,28 |

## Zu Anhang E.3.4 — mittlere TPR im Bereich kleiner FPR

Ob eine Einstellung bei gleicher Zeit mit Fehleskalation mehr Krisenzeit abdeckt als 3-aus-4, zeigt die mittlere $TPR_{sys}$ im Bereich $FPR_{sys} \le 3\,\%$ der Kurve über $h$ (Tabelle E.11). Bei $\eta_j = 0{,}9$ liegt jede Einstellung des Fensters unter 3-aus-4. Bei $\eta_j = 0{,}7$ liegen $T$ = 5 s und $T$ = 8 s darüber, 2-aus-4 erreicht keine.

## Zu Anhang E.3.4 — Tabelle E.11

| $\eta_j$ | 2-aus-4 | 3-aus-4 | $T$ = 5 s | $T$ = 8 s | $T$ = 10 s | $T$ = 15 s | $T$ = 20 s | $T$ = 30 s |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0,9 | 0,581 | 0,569 | 0,543 | 0,513 | 0,495 | 0,443 | 0,393 | 0,302 |
| 0,7 | 0,499 | 0,465 | 0,483 | 0,467 | 0,455 | 0,416 | 0,378 | 0,304 |

*Tabelle E.11 — Mittlere $TPR_{sys}$ der Kurve über $h$ im Bereich $FPR_{sys} \le 3\,\%$ für die Zählregeln und den Filter mit verschiedenen Fenstern.*

## Zu Anhang E.4.2 — Quantelung der Latenzen

Die Latenzen sind auf das Raster des Generators von 1 s gequantelt. Ein Median nahe einer Stufe kann sich deshalb zwischen zwei Auswertungen um bis zu eine Sekunde unterscheiden. Für 3-aus-4 im Lauf 12 h liegen 152 der 291 Latenzen unter 6,5 s. Der Median von 6,1 s liegt also unmittelbar an der Stufe zu 7 s, und die Nachrechnung ohne Netz ergibt für dieselbe Regel 7,0 s.

## Zu Anhang E.4.3 — Zeilen von Lauf 1 h in Tabelle E.13

| Lauf | $\eta_j$ | Vergleich | Fehleskalationen je Bett und Stunde | erkannt mild | erkannt streng |
|---|---:|---|---|---|---|
| 1 h | 0,9 | 2-aus-4 | −2,35 [−3,10; −1,60] \* | −4,8 [−12,5; +0,0] | −4,8 [−12,5; +0,0] |
| 1 h | 0,9 | 3-aus-4 | +1,25 [+0,85; +1,65] \* | +0,0 [+0,0; +0,0] | +0,0 [+0,0; +0,0] |
| 1 h | 0,9 | Mischung | gleich (Festlegung) | −1,7 [−4,2; +0,0] | −1,7 [−4,2; +0,0] |

## Zu Anhang E.4.5 — weitere Zeilen von Tabelle E.15

| Regel | Fehleskalationen je Bett und Stunde | erkannt mild | erkannt streng | Latenz (Median) |
|---|---|---:|---:|---:|
| 2-aus-4, $\tau$ = 2 s | 5,43 [5,14; 5,74] | 99,0 % | 97,4 % | 5,1 s |
| 2-aus-4, $\tau$ = 4 s | 4,91 [4,63; 5,20] | 98,4 % | 95,5 % | 7,1 s |
| 2-aus-4, $\tau$ = 6 s | 4,50 [4,24; 4,78] | 98,4 % | 92,3 % | 9,1 s |
| 2-aus-4, $\tau$ = 15 s | 1,40 [1,25; 1,56] | 80,4 % | 54,7 % | 18,1 s |

## Zu Anhang F — Abbildungen F.1 und F.2 (Zustand ohne Alarm)

Die Abbildung ist in dieser Fassung nicht enthalten.

## Zu Anhang G.2 — Formel (G.1)

Aus den Stationsdaten folgen nach (5.1) die Krisenprävalenz und die Fehlalarmrate je Quelle:

$$
P(C) = \frac{n\,(1-\text{FDR})\,d_{true}}{86\,400 \cdot TPR}, \qquad FPR = \frac{n\,\text{FDR}\,d_{false}}{86\,400\,\big(1-P(C)\big)}. \tag{G.1}
$$

## Zu Anhang G.2 — zwei Zahlenwerte zur Einordnung

Zwei Zahlenwerte zur Einordnung:

- Median-Intensivstation: $\lvert M \rvert \cdot \overline{FPR} = 4 \cdot 0{,}00313 \approx 0{,}0125$. An einem Bettplatz alarmiert im Mittel rund 1,25 % der Zeit irgendein Kanal fälschlich. Die Fehlalarmzeit je Bettplatz ist damit rund neunmal so groß wie die Krisenzeit.
- Simulation, Lauf 12 h: 10 802 Fehlalarme auf 20 Betten × 4 Kanäle × 12 h ergeben 11,3 Fehlalarme je Kanal und Stunde. Bei 8,3 s mittlerer Dauer ist jeder Kanal rund 2,6 % der Zeit fälschlich aktiv, also $\overline{FPR} \approx 0{,}026$ und $\lvert M \rvert \cdot \overline{FPR} \approx 0{,}104$. Das ist rund das Achtfache der Median-Intensivstation (Szenario sehr hoher Last). Auf die ganze Station mit 20 Betten bezogen sind im Mittel $20 \cdot 0{,}104 \approx 2$ Kanäle gleichzeitig fälschlich aktiv.

## Zu Anhang G.7 — Tabelle G.3, Kurven des Belastungsmodells

| Kurve über | Größe | Regeln | Formeln | Verwendung |
|---|---|---|---|---|
| Zahl der Kanäle $\lvert M \rvert$ von 2 bis 12 | ARR | 2-aus-N, 3-aus-N, adaptiv | (G.5), (G.6), G.6 | Abbildung 5.2a |
| Anteil gemeinsamer Artefakte $c_{art}$ von 0 bis 1 | ARR | 2-aus-4, 3-aus-4, adaptiv | (G.5), (G.6) | Abbildung 5.2b |
| Anzeigewahrscheinlichkeit $\eta_j$ je Kanal | $TPR_{sys}$ | ohne Filter, 2-aus-N, 3-aus-N, adaptiv | (G.8), (G.9) | in Kapitel 5 nicht abgebildet |
| Zahl der Kanäle $\lvert M \rvert$ von 4 bis 12 | unnötige Eskalationen und verpasste Krisen je Station und Woche | 2-aus-N, 3-aus-N, adaptiv | (G.7), (G.10), G.6 | Zahlenbeispiel in G.8 |

*Tabelle G.3 — Kurven des Belastungsmodells.*

## Zu Anhang G.8 — Zahlenbeispiel

### G.8 Zahlenbeispiel

Für die Median-Intensivstation mit $\lvert M \rvert = 4$ ist $q = 0{,}5 \cdot 0{,}00313 = 0{,}00156$. Die Wahrscheinlichkeit, dass außerhalb einer Krise genau zwei bestimmte Quellen zufällig zugleich fälschlich alarmieren, ist $q^2 (1-q)^2 \approx 2{,}4 \cdot 10^{-6}$. Nach (G.7) beginnen solche Überlappungen für jedes der sechs Paare $2 \cdot 2{,}4 \cdot 10^{-6} / 8\ \text{s} \cdot 86\,400\ \text{s} \approx 0{,}053$-mal je Bett und Tag.

### Unnötige Eskalationen

- 2-aus-N eskaliert alle sechs Paare sofort: $6 \cdot 0{,}053 = 0{,}32$ je Bett und Tag. Eine neue Eskalation durch drei oder vier Kanäle beginnt kaum, weil schon ein Paar darin eskaliert hätte.
- Das adaptive Modell eskaliert das Paar hoch + hoch sofort ($0{,}053$). Die vier Paare hoch + niedrig eskalieren nur, wenn beide Alarme 15,4 s andauern: $e^{-2 \cdot 15{,}4/8} = 0{,}021$. Das Paar niedrig + niedrig braucht 23,9 s: $e^{-2 \cdot 23{,}9/8} = 0{,}0025$. Zusammen mit den seltenen Dreiergruppen sind das rund $0{,}058$ je Bett und Tag, knapp ein Fünftel des Werts von 2-aus-N. Das ist die obere Grenze aus (G.7).
- 3-aus-N eskaliert keine Paare. Es bleiben die seltenen Dreiergruppen mit $0{,}0005$ je Bett und Tag.

Hinzu kommen für jede Regel gleich $c_{art} \cdot FPR \cdot 86\,400/d_{false} = 16{,}9$ Eskalationen durch gemeinsame Stöße. Insgesamt sind das 17,19 (2-aus-N), 16,93 (adaptiv) und 16,88 (3-aus-N) Fehlalarme je Bett und Tag gegenüber 135 ohne Filter. Die ARR als Zeitanteil (G.6) beträgt 87,4 bis 87,5 % wie in Tabelle 5.7. Auf der Median-Intensivstation überwiegen die gemeinsamen Stöße so stark, dass die korrigierte Zählung daran fast nichts ändert.

### Verpasste Krisen

Mit $\eta_j = TPR = 0{,}9$ zeigen eine Krise alle vier Quellen mit 65,6 % Wahrscheinlichkeit, genau drei mit 29,2 %, genau zwei mit 4,9 %, eine oder keine mit 0,37 %.
- 2-aus-N verpasst nur den letzten Fall: 0,37 %.
- 3-aus-N verpasst auch alle Krisen mit genau zwei Quellen: 5,2 %.
- Das adaptive Modell erkennt dieselben Teilmengen wie 2-aus-N, braucht aber Zeit (Tabelle G.2). Nach (G.9) dauern 97,3 % der Krisen mindestens 15,4 s und 55,2 % mindestens 23,9 s. Die übrigen Teilmengen eskalieren nach höchstens 10 s, also vor $d_0 = 15$ s. Es verpasst damit 0,82 %. Nach (4.7), ohne Krisendauer, wären es 0,37 %.

### Je Station und Woche

Für 10 Betten und 7 Tage werden die Werte mit 70 multipliziert. Bei $K_d = 4{,}17$ sind das 292 Krisen. Tabelle G.4 zeigt das Ergebnis.

| Regel | unnötige Eskalationen | verpasste Krisen |
|---|---:|---:|
| 2-aus-N | 22,1 | 1,1 |
| adaptiv | 4,0 | 2,4 |
| 3-aus-N | 0,0 | 15,3 |

*Tabelle G.4 — Unnötige Eskalationen und verpasste Krisen je Station mit 10 Betten und Woche, Median-Intensivstation, $\lvert M \rvert = 4$. Die unnötigen Eskalationen des adaptiven Modells sind die obere Grenze nach (G.7).*

Mit wachsender Gerätezahl steigt die Zahl der Paare quadratisch, $\binom{\lvert M \rvert}{2}$: bei zwölf Quellen sind es 66. Bei 2-aus-N wachsen deshalb die unnötigen Eskalationen auf 240 je Station und Woche. Beim adaptiven Modell steigt mit $\lvert M \rvert$ auch das Quorum, und die Paare werden ausgefiltert.

## Zu Anhang G.10 — Prüfung von E1 durch die Auswertung von PC 2

Die Auswertung von PC 2 (Anhang E) prüft denselben Zusammenhang unabhängig davon und ohne das Entscheidungsmodell. Sie rechnet den Anteil der Krisenzeit mit Eskalation für die Zählregeln allein aus den gemessenen Einstiegszeiten: k-aus-4 eskaliert in einer Krise mit dem $k$-ten Einstieg und bleibt bis zum Krisenende eskaliert.

$$
TPR_{sys}^{(k)} = \frac{\sum_c \max(0,\; t_{Ende,c} - t_{c}^{(k)})}{\sum_c (t_{Ende,c} - t_{Beginn,c})} \tag{G.11}
$$

Darin ist $t_c^{(k)}$ der Zeitpunkt des $k$-ten Einstiegs in Krise $c$. Tabelle G.6 zeigt die gemessene Verteilung des Einstiegs, also der Zeit vom Krisenbeginn bis zur ersten Überschreitung je Gerät, und das Ergebnis der Rechnung.

| Lauf | Einstiege | 25 % | Median | 75 % | 90 % | 2-aus-4: gerechnet / gemessen | 3-aus-4: gerechnet / gemessen |
|---|---:|---:|---:|---:|---:|---:|---:|
| ROC 0,9 | 1118 | 7 s | 10 s | 14 s | 16 s | 67,4 / 69,0 % | 54,5 / 56,0 % |
| ROC 0,7 | 888 | 6 s | 10 s | 14 s | 17 s | 61,4 / 62,3 % | 41,5 / 41,8 % |
| Prüfung 0,9 | 1090 | 6 s | 10 s | 14 s | 17 s | 69,0 / 69,8 % | 57,0 / 58,1 % |
| Prüfung 0,7 | 911 | 6 s | 10 s | 14 s | 17 s | 62,8 / 63,5 % | 40,3 / 41,4 % |

*Tabelle G.6 — Einstieg der Geräte in eine Krise bei $h = 1$ und Anteil der Krisenzeit mit Eskalation für die Zählregeln, aus den Einstiegszeiten nach (G.11) gerechnet und gemessen (Auswertung von PC 2).*

Diese Rechnung kennt nur den Einstieg der Geräte und trifft die Messung für 2-aus-4 und 3-aus-4 in allen vier Datensätzen auf 0,3 bis 1,6 Prozentpunkte. Sie liegt stets etwas darunter, weil in der Messung ein Teil der Eskalationen schon durch einen Fehlalarm kurz vor der Krise besteht. Die gemessenen Werte von PC 2 liegen für ROC 0,9 um 0,2 Prozentpunkte unter denen in Tabelle G.5 (69,0 und 56,0 % statt 69,2 und 56,2 %), weil das Auswertungsfenster dort etwas anders gewählt ist.