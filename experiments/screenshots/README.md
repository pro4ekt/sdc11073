# experiments/screenshots — UI screenshots for Kapitel 3.2.5

Demo only (no experiment, no test): the worked example of Kapitel 3 on one host,
two beds, four testbed providers, no TLS. Patient data are fictional.

## Files

| File | Purpose |
|---|---|
| `demo_scenario.py` | starts the providers and drives the phases over stdin |
| `../../testbed/mdib/monitor_demo_mdib.xml` | copy of `spo2_mdib_v2.xml`: `desaturation` Hi, `tachycardia` Lo |
| `../../testbed/mdib/vent_demo_mdib.xml` | copy of `vent_mdib_v2.xml`: `rr_high` Lo, `vt_low` Lo (unchanged) |
| `../../Dashboard/config/clinical_db.json` | block `"SDC Demo"`: weights 5 / 3 / 2.5 / 3 (TPR 0.9, FPR 0.00606 / 0.0448 / 0.0739 / 0.0448) |

Channels without a profile (`ppeak_high`, `mv_low`) stay outside the channel set.

## Beds

| Bed | Device | Model | patient-id | room / bed | port |
|---|---|---|---|---|---|
| 1 | monitor | Monitor Demo | DEMO-01 | ICU-1 / 1 | 10800 |
| 1 | ventilator | Vent Demo | DEMO-01 | ICU-1 / 1 | 10801 |
| 2 | monitor | Monitor Demo | DEMO-02 | ICU-1 / 2 | 10802 |
| 2 | ventilator | Vent Demo | DEMO-02 | ICU-1 / 2 | 10803 |

The dashboard forms the two ensembles itself from (patient, room).

## Run

1. Dashboard, from `Dashboard/` (no room filter, no TLS):

       ..\.venv\Scripts\python main.py --no_tls --ip <IP>

2. Demo, from the repository root (same `<IP>`):

       .venv\Scripts\python experiments\screenshots\demo_scenario.py --ip <IP>

   `--auto 30` advances every phase after 30 s instead of Enter; `--echo` prints all
   provider output.

## Phases

| Phase | Script sends | Screenshot |
|---|---|---|
| 0 | nothing (providers bound) | both cards Blau |
| 1 | `alarm desaturation on` on both monitors | both cards Gelb, Severity ≈ 75 % |
| 2 | `alarm vt_low on` on the ventilator of bed 1 | bed 1 Rot (≈ 83 %), bed 2 Gelb — main screenshot |
| 3 | nothing; acknowledge by hand on the device page of bed 1 | acknowledgement status |
| 4 | `kill()` of the bed-1 ventilator (silent loss, no Bye) | "Verbindung verloren … während Eskalation" |
| 5 | alarms off, `shutdown` of all providers | — |

Why bed 2 stays Gelb: with the monitor alone, SDC_score = 0.75, k_min = 3 and the fully
lowered threshold is 0.5 · 3 · 3.375 ≈ 5.06 > 5 = w of the monitor channel. Bed 1 reaches Rot
once the ventilator alarm lowers k_min to 2 (SDC_score ≈ 0.83). How fast depends on how long
phase 1 lasted: after about 19 s of monitor alarm the threshold is already at its floor and Rot
follows at the next evaluation; after a short phase 1 it takes about 4 to 6 s.

## Headless check (no window, dashboard must NOT be running)

    .venv\Scripts\python experiments\screenshots\demo_scenario.py --ip <IP> --headless [--hold1 7]

Runs phases 0–2 against the real `SdcMyConsumer` and prints the cards (`isEscalated`,
`hasActiveAlarms`, `sdcScore`) and the time from `alarm vt_low on` to Rot. Journal:
`logs/headless.jsonl`.
