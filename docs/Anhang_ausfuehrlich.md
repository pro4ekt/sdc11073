# Ergänzungen zum Anhang der Masterarbeit

Diese Datei enthält Abschnitte, die in der gedruckten Fassung des Anhangs gekürzt wurden. Die Nummern der Tabellen und Abschnitte entsprechen der ungekürzten Fassung und nicht der gedruckten Arbeit. Pfade sind relativ zu diesem Ordner.

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
