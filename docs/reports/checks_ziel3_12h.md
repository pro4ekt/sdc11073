# Prüfungen ziel3_12h

## 4.1 Alarme aus Journal B ohne Zeile in alarm_in_pc1.csv

Log-Abdeckung PC 1: 1793592211.742 .. 1793633541.000 (PC-1-Uhr). Ereignisse von B in dieser Spanne: 23076 (ON und OFF, Zeiten nach PC 1 umgerechnet). Zuordnung: gleicher Patient, Quelle, on/off, |dt| <= 2.5 s, jede Logzeile höchstens einmal.

Ohne Zuordnung: 0 (0.00 %), davon ON 0, OFF 0.

Zeitdifferenz Logzeile PC 1 − Echo Provider (nach Uhrabgleich): verdict: n=11353, Median -52 ms, p90 -46 ms, min -272 ms, max 1820 ms; log: n=11723, Median -943 ms, p90 -138 ms, min -1553 ms, max -37 ms

- während eines laufenden Rot desselben Patienten: 0
- innerhalb 2,5 s eines TTL-Expired-Eintrags des Patienten (Log nennt dann keine Einzelalarme): 0
- sonst (Verlust nicht auszuschließen): 0

## 4.2 Auffällige Rot-Episoden

## Rot mit sdc_score <= 0,5

n = 25; davon in/nahe einer Krise: 1; aktive B-Alarme beim Rot-Start 0/1/2/3/4 Quellen: 0/3/22/0/0;
vorheriges Rot desselben Patienten endete <= 10 s davor: 1, <= 30 s davor: 1; letzter Alarm-OFF des Patienten <= 10 s davor: 6; Dauer min/median/max = 0.24/4.12/17.03 s

| t_start (PC 1) | Patient | Dauer s | sdc_score | aktiv beim Start | Alarmstarts in den 15 s davor (Quelle, dt) | Abstand zum vorigen Rot s | Krise |
|---|---|---|---|---|---|---|---|
| 1793590456.267 | SIM-P17 | 15.92 | 0.500 | SpO2,ABP | ABP:-14.9 | - | nein |
| 1793592123.815 | SIM-P06 | 0.41 | 0.444 | SpO2 | ABP:-7.5 Vent:-7.5 SpO2:-7.5 | 48.6 | nein |
| 1793593016.532 | SIM-P17 | 0.70 | 0.500 | ABP,SpO2 | — | 811.1 | nein |
| 1793594171.202 | SIM-P13 | 6.96 | 0.500 | ABP,SpO2 | SpO2:-10.0 | 153.9 | nein |
| 1793598782.919 | SIM-P01 | 2.14 | 0.500 | SpO2,ABP | — | 98.9 | nein |
| 1793599337.471 | SIM-P08 | 4.45 | 0.500 | SpO2,ABP | — | 667.5 | nein |
| 1793599419.409 | SIM-P16 | 3.52 | 0.500 | ABP,SpO2 | — | 440.4 | nein |
| 1793599517.639 | SIM-P14 | 5.31 | 0.500 | SpO2,ABP | — | 702.7 | nein |
| 1793600874.011 | SIM-P07 | 7.91 | 0.500 | ABP,SpO2 | SpO2:-9.0 | 142.1 | nein |
| 1793601580.333 | SIM-P20 | 1.60 | 0.500 | ABP,SpO2 | — | 2012.0 | nein |
| 1793603197.679 | SIM-P11 | 7.26 | 0.500 | SpO2,ABP | ABP:-12.5 | 316.7 | nein |
| 1793603368.819 | SIM-P09 | 3.12 | 0.500 | ABP,SpO2 | Vent:-13.8 ABP:-7.8 ECG:-7.8 SpO2:-7.8 | 9.9 | nein |
| 1793609870.671 | SIM-P11 | 1.28 | 0.500 | ABP,SpO2 | — | 2157.7 | nein |
| 1793612364.655 | SIM-P10 | 0.24 | 0.500 | ABP,SpO2 | SpO2:-10.7 | 253.8 | nein |
| 1793613675.031 | SIM-P05 | 2.84 | 0.500 | SpO2,ABP | — | 1084.1 | nein |
| 1793613933.131 | SIM-P10 | 5.75 | 0.500 | SpO2,ABP | ABP:-10.2 | 481.3 | nein |
| 1793614109.266 | SIM-P19 | 0.85 | 0.222 | ABP | ABP:-7.3 Vent:-7.3 ECG:-7.3 | 379.4 | nein |
| 1793622492.559 | SIM-P09 | 0.36 | 0.444 | SpO2 | SpO2:-7.5 ECG:-7.5 ABP:-7.3 | 2092.7 | nein |
| 1793624428.996 | SIM-P14 | 17.03 | 0.500 | ABP,SpO2 | SpO2:-5.0 | 183.1 | ja |
| 1793624672.779 | SIM-P12 | 4.17 | 0.500 | ABP,SpO2 | — | 52.7 | nein |
| 1793625478.327 | SIM-P13 | 4.62 | 0.500 | ABP,SpO2 | SpO2:-13.2 | 268.4 | nein |
| 1793630078.699 | SIM-P05 | 2.16 | 0.500 | ABP,SpO2 | — | 213.8 | nein |
| 1793630078.700 | SIM-P03 | 4.16 | 0.500 | ABP,SpO2 | ABP:-9.8 SpO2:-7.8 Vent:-7.8 | 739.5 | nein |
| 1793631443.186 | SIM-P20 | 11.67 | 0.500 | SpO2,ABP | SpO2:-10.3 ABP:-10.1 Vent:-2.3 | 465.3 | nein |
| 1793631672.925 | SIM-P04 | 4.12 | 0.500 | SpO2,ABP | — | 2009.1 | nein |

## Rot kürzer als 0,5 s

n = 12; davon in/nahe einer Krise: 0; aktive B-Alarme beim Rot-Start 0/1/2/3/4 Quellen: 0/3/8/1/0;
vorheriges Rot desselben Patienten endete <= 10 s davor: 0, <= 30 s davor: 0; letzter Alarm-OFF des Patienten <= 10 s davor: 3; Dauer min/median/max = 0.02/0.25/0.49 s

| t_start (PC 1) | Patient | Dauer s | sdc_score | aktiv beim Start | Alarmstarts in den 15 s davor (Quelle, dt) | Abstand zum vorigen Rot s | Krise |
|---|---|---|---|---|---|---|---|
| 1793592123.815 | SIM-P06 | 0.41 | 0.444 | SpO2 | ABP:-7.5 Vent:-7.5 SpO2:-7.5 | 48.6 | nein |
| 1793594307.733 | SIM-P17 | 0.40 | 0.833 | ECG,Vent | ECG:-10.5 Vent:-10.2 | 638.5 | nein |
| 1793610787.892 | SIM-P15 | 0.03 | 0.889 | ABP,Vent,ECG | ABP:-9.7 Vent:-5.9 ECG:-4.9 | 262.0 | nein |
| 1793612364.655 | SIM-P10 | 0.24 | 0.500 | ABP,SpO2 | SpO2:-10.7 | 253.8 | nein |
| 1793614415.853 | SIM-P04 | 0.02 | 0.833 | ECG,Vent | ECG:-10.8 Vent:-10.7 | 1374.0 | nein |
| 1793616529.558 | SIM-P05 | 0.44 | 0.722 | ECG,ABP | ABP:-10.6 | 260.2 | nein |
| 1793618996.732 | SIM-P11 | 0.25 | 0.778 | SpO2,Vent | SpO2:-10.8 Vent:-10.7 | 994.8 | nein |
| 1793622251.713 | SIM-P15 | 0.20 | 0.778 | SpO2,Vent | SpO2:-10.7 Vent:-10.7 | 1594.8 | nein |
| 1793622492.559 | SIM-P09 | 0.36 | 0.444 | SpO2 | SpO2:-7.5 ECG:-7.5 ABP:-7.3 | 2092.7 | nein |
| 1793624145.764 | SIM-P03 | 0.18 | 0.667 | ECG | ECG:-8.8 | 125.8 | nein |
| 1793624824.590 | SIM-P06 | 0.49 | 0.833 | ECG,Vent | ECG:-11.6 Vent:-9.6 | 2336.7 | nein |
| 1793630327.712 | SIM-P13 | 0.16 | 0.778 | SpO2,Vent | SpO2:-10.8 Vent:-10.6 | 370.8 | nein |

## 4.3 Von adaptiv verpasste Krisen

| Krise | Patient | Klasse | Dauer s | Quellen: Schwelle überschritten bei t − Start (t − Ende) | 2-aus-4 | 3-aus-4 | nächstes Rot des Patienten (Abstand zum Krisenstart) |
|---|---|---|---|---|---|---|---|
| SIM-P07-C004 | SIM-P07 | Me+Lo | 16.0 | SpO2 +16.0 (+0.0), ABP +12.0 (-4.0) | ja | nein | -295 s (Dauer 7.1 s) |
| SIM-P03-C005 | SIM-P03 | Hi+Lo | 16.0 | Vent +4.0 (-12.0), ABP +9.0 (-7.0) | ja | nein | +459 s (Dauer 14.9 s) |
| SIM-P15-C005 | SIM-P15 | 1 Quelle | 17.0 | ABP +13.0 (-4.0) | ja | nein | -680 s (Dauer 15.9 s) |
| SIM-P07-C011 | SIM-P07 | 3 | 18.0 | Vent +18.0 (+0.0), SpO2 +13.0 (-5.0), ABP +16.0 (-2.0) | ja | ja | +246 s (Dauer 5.8 s) |
| SIM-P02-C007 | SIM-P02 | 1 Quelle | 19.0 | ECG +4.0 (-15.0) | nein | nein | +33 s (Dauer 9.0 s) |
| SIM-P15-C014 | SIM-P15 | 1 Quelle | 16.0 | ABP +14.0 (-2.0) | nein | nein | -5 s (Dauer 4.5 s) |
| SIM-P09-C014 | SIM-P09 | Hi+Lo | 19.0 | ECG +17.0 (-2.0), ABP +15.0 (-4.0) | ja | nein | -893 s (Dauer 6.6 s) |
