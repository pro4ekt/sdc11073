# SPEC_C — ночной прогон на двух ПК без канала управления (вариант C)

Дата: 22.10.2026. Файлы: `schedule_c.json` (общее расписание), `pc2_scheduler.py` (ПК 2),
`pc1_listener.py` + `netmode.py` + `campaign.py --listen/--merge` (ПК 1), папка `pc2_c/` (всё для ПК 2),
`HANDOFF_PC2.md` (инструкция для сессии на ПК 2).

## 1. Принцип

- ПК 2 (192.168.0.105) **сам** выполняет сценарий из `schedule_c.json`: запускает и останавливает
  `testbed_provider.py`, подаёт `alarm on/off`, делает обрывы, пишет свой журнал. Команд с ПК 1 нет.
- ПК 1 (192.168.0.106) только **наблюдает**: headless-Dashboard с журналом (реальный `SdcMyConsumer`),
  авто-квитирование по появлению тревоги, tshark на сетевой карте, CPU/память, опрос часов ПК 2 по UDP.
- После прогона `campaign.py --merge` сопоставляет журналы по EPR и времени и генерирует события в формате
  существующих раннеров, после чего работают **неизменённые** анализаторы `ziel1_analyze.py` и `ziel2_analyze.py`.
- `testbed_provider.py`, `harness.py`, раннеры и Dashboard не изменены.

## 2. Адреса и порты

| Что | Значение |
|---|---|
| ПК 1 Dashboard | 192.168.0.106 (Wi-Fi), WS-Discovery UDP 3702, HTTP-сервер событий на эфемерном TCP-порту |
| ПК 2 провайдеры | 192.168.0.105, TCP 12100–12409 (Ziel 1 filtered), 12600 (ack), 12700–12804 (обрывы), 13700–13804 (reconnect = port + 1000), 12500–12543 (baseline), 12999 (selftest) |
| Обмен часами | UDP 5558 на ПК 2 (ПК 1 → ПК 2 запрос, ПК 2 → ПК 1 ответ), раз в 10 с |
| Firewall ПК 1 | входящие для `python.exe` (UDP 3702, TCP любые — уведомления приходят на эфемерный порт) |
| Firewall ПК 2 | входящие для `python.exe` (UDP 3702, TCP 12000–13999, UDP 5558) |
| netdown | на ПК 2 правила `SDC_C_NETDOWN_OUT/IN`: `action=block remoteip=192.168.0.106` |

Диапазон 12xxx выбран так, чтобы не пересекаться с локальной кампанией (10xxx/11xxx) при проверке конвейера на одном ПК.

## 3. Расписание (полный прогон / `--pilot`)

Порядок фаз и окна ПК 1:

| Фаза | Окно ПК 1 | Вид | n полный | n пилот | Порт-база | Оценка |
|---|---|---|---|---|---|---|
| z1_filtered | filtered (`--room ICU-1`) | Ziel 1 | foreign 150, mismatch 150, own 5, nolocation 5 (interleave) | 3/3/1/1 | 12100 | ≈ 145 мин |
| z2_ack | filtered | квитирование | accept 300, refuse 20 | 3 / 2 | 12600 | ≈ 32 мин |
| z2_fault | filtered | обрывы | kill 30, shutdown 30, suspend 10, reconnect 5, netdown 30 (interleave) | по 1 | 12700 | ≈ 112 мин |
| z1_baseline | baseline (без комнаты) | Ziel 1 | foreign 20, mismatch 20, own 2, nolocation 2 | 2/2/1/1 | 12500 | ≈ 21 мин |

Итого ≈ 5,2 ч + паузы (`phase_gap_s` 30, `window_switch_s` 90); пилот ≈ 15 мин с паузами.
Тайминги (`timing`): `ready_timeout_s` 40, `z1_window_s` 18, `z1_gap_s` 4, `bind_timeout_s` 90, `bind_settle_s` 3,
`ack_settle_s` 5, `ack_hold_s` 4, `ack_pause_s` 2, `suspend_hold_s` 45, `netdown_hold_s` 45, `reconnect_wait_s` 20,
`reconnect_hold_s` 40, паузы после обрыва kill 25 / shutdown 15 / suspend 10 / netdown 15, `linger_s` 600.
Роли → устройства как в `ziel1_experiment.py`: own = ecg, foreign = spo2, nolocation = abp, mismatch = vent;
аргументы провайдера: own `--room ICU-1 --bed 1`; foreign `--room ICU-2 --bed 1`; mismatch `--scope-room ICU-1 --room ICU-2 --bed 1`; nolocation без `--room`.

## 4. Протокол попытки

**Ziel 1 (обе фазы).** Свежая EPR, свой порт → ждать `ready:` (≤ 40 с, иначе `trial_end outcome=provider_failed`) →
держать 18 с (окно для Hello/Probe, решения и precheck ПК 1) → `status` → `subscriptions=N` в журнал →
`trial_end` → graceful `shutdown` (Bye + SubscriptionEnd) → пауза 4 с.

**Квитирование.** Один ECG-провайдер (`--room ICU-1 --ack accept`) → `ready:` → опрос `status` каждые 2 с, пока
`subscriptions ≥ 1` (ПК 1 привязался; ≤ 90 с, иначе `phase_abort`) → 5 с → блок accept: 300 × [`alarm tachycardia on`,
4 с, `alarm tachycardia off`, 2 с] → `ack refuse` → блок refuse: 20 × то же. ПК 1 видит условие тревоги (опрос
`snapshot_alert_presence` каждые 0,25 с) и жмёт `silenceAlarm()` один раз на появление; RTT, InvocationState,
Presence=Ack целиком на ПК 1 (`ack_rtt`, `ack_confirmed`, `auto_ack`).

**Обрывы.** Свежий ECG-провайдер на попытку → `ready:` → `status` до `subscriptions ≥ 1` (≤ 90 с, иначе
`trial_end outcome=not_bound`) → 3 с → `fault_injected{t_loss}` → метод:
- `shutdown`: stdin `shutdown`; `kill`: TerminateProcess дерева процессов; `suspend`: SuspendThread всех потоков,
  45 с, `fault_restore`, resume, kill; `reconnect`: kill, 20 с, тот же EPR на port + 1000 (`reconnect_started`), 40 с, shutdown;
- `netdown`: запуск **сторожевого процесса** (отдельный detached-процесс, снимает правила через 45 + 15 с
  в любом случае) → `netsh advfirewall ... add rule ... action=block remoteip=192.168.0.106` (out и in) →
  `t_loss` = момент возврата `netsh` → 45 с → удаление правил (`netdown_rule delete`, `fault_restore`) → провайдер
  живёт ещё 40 с (ПК 1 переподключается, `technical_alarm cleared`) → shutdown. При старте и при любом завершении
  планировщика правила удаляются; `--netdown-clear` снимает их вручную; `--netdown-mode skip` пропускает метод.

**Условие начала обрыва:** `subscriptions ≥ 1` в ответе самого провайдера (эквивалент `wait_bound` локального
раннера) + 3 с.

## 5. Журнал ПК 2 (`logs/<scenario_id>/pc2_journal.jsonl`, время ПК 2)

Каждая запись: `seq, t_abs (time.time()), t_mono, iso, event` + поля:

| event | поля |
|---|---|
| scenario_start | scenario_id, pc2_ip, pc1_ip, room, other_room, tls, pilot, netdown_mode, phases, estimate_s, schedule (полная копия), host, python |
| pc1_seen | requests, peer (первый запрос часов от ПК 1) |
| phase_start / phase_end | phase, window, kind, spec |
| window_switch | to_window, next_phase, pause_s |
| trial_start | phase, kind (ziel1/ack/fault), trial, role, device, epr, port; ziel1: mode, expected; ack: channel, cond_handle, signal_handle, accept, refuse; fault: method, bound_s |
| provider_ready | trial, epr, after_s |
| bound | trial, epr, subscriptions, after_s (timeout=true при неудаче) |
| status | trial, epr, subscriptions |
| ack_policy | epr, policy, n |
| alarm | trial (= номер цикла), epr, channel, on (true/false), policy, cond_handle, signal_handle |
| fault_injected | trial, method, epr, **t_loss**, pid, bound_s, rule_ok (netdown) |
| fault_restore | trial, method, epr, ok |
| reconnect_started | trial, epr, t_restart, port |
| reconnect_status | trial, epr, subscriptions |
| netdown_rule | trial, action add/delete, ok, watchdog_pid |
| trial_end | phase, kind, trial, role/method, epr, port, outcome, provider_subscriptions, elapsed_s / cycles |
| trial_skipped, phase_abort, phase_error | причина |
| clock_reply | peer, t1, t2, t3, seq |
| scenario_end | aborted, trials_done, netdown_rules_left |

Рядом: `console.txt` (вывод планировщика и всех провайдеров), `scenario.json` (разрешённое расписание),
`netdown_watchdog.log`.

## 6. Обмен смещением часов

UDP, JSON. ПК 1 каждые 10 с: `{"t1": <time.time() ПК 1>, "seq": n}` → ПК 2 отвечает
`{"t1","seq","t2": <приём>, "t3": <отправка>, "scenario_id","pilot","phase","window","state","trial","trials_done","trials_total"}`.
ПК 1 при приёме `t4`: `offset = ((t2 − t1) + (t3 − t4)) / 2` (ПК 2 − ПК 1), `rtt = (t4 − t1) − (t3 − t2)`,
запись `clock_offset{offset_s, rtt_s, t1, t4, phase, window, state, …}` в журнал ПК 1, при изменении фазы `pc2_phase`.
Обмен идёт всю ночь, а не только в начале и в конце: при слиянии время ПК 2 переводится по ближайшим пробам
с линейной интерполяцией (дрейф учитывается), неопределённость каждой конвертации ≤ rtt/2, пробы с rtt > 250 мс
отбрасываются. В досье: offset в начале и в конце, дрейф, максимальная неопределённость. Во время `netdown`
(45 с) запросы не доходят — это ожидаемо и игнорируется.

## 7. Порядок запуска

1. ПК 1: `campaign.py --listen --pilot` (или без `--pilot`). Слушатель окна `filtered` стартует, опрашивает часы ПК 2.
2. ПК 2 (админ-консоль): `pc2_scheduler.py --selftest` → `SELFTEST: OK`; затем `--pilot` (или полный).
   Планировщик ждёт первый запрос часов от ПК 1 до 30 мин (`--wait-pc1-min`), потом начинает фазы.
3. Переход в окно baseline: ПК 2 выставляет `phase=z1_baseline, state=waiting` и ждёт 90 с; слушатель `filtered`
   видит `window=baseline` в ответе и завершается; `campaign.py` запускает слушателя `baseline` (без комнаты).
4. Конец: ПК 2 выставляет `state=done` и ещё 10 мин (`linger_s`) отвечает на запросы; слушатель `baseline`
   (или `filtered`, если baseline нет) видит `done` и завершается; `--listen` печатает команду для `--merge`.
5. ПК 2: скопировать папку `logs/<scenario_id>/` на ПК 1. ПК 1: `campaign.py --merge <run_dir> --pc2 <папка>`.

Порядок «сначала ПК 1, потом ПК 2» не обязателен: планировщик ждёт ПК 1 до 30 мин, слушатель ждёт ПК 2 до
60 мин (`wait_start_min`). Страховки ПК 1: `max_hours` 10; нет ответов 30 мин после хотя бы одного — выход.

## 8. Как ПК 1 понимает конец сценария

Только по ответам на запрос часов: `state == "done"` (ПК 2 держит его `linger_s` = 600 с после `scenario_end`).
Если ПК 2 упал, ответов нет → через `idle_exit_min` = 30 мин слушатель завершается с причиной
`no reply from PC 2`; `--merge` тогда помечает `PC-2-Journal ohne scenario_end` как проблему (`GESAMT: FEHLER`).

## 9. Слияние (`campaign.py --merge RUN --pc2 PATH`)

- Фазы ПК 2 → каталоги `ziel1_filtered/`, `ziel2_ack/`, `ziel2_fault/`, `ziel1_baseline/` с `events.jsonl`:
  синтетические записи в формате раннеров + все записи ПК 1 с тем же EPR.
- Ziel 1: `trial_start{trial, role, epr, port, mode, expected}` (время ПК 2 → ПК 1), `trial_end{outcome,
  provider_subscriptions}`; outcome: `not_discovered`, если на ПК 1 нет `device_discovered` для EPR (считается
  **против TRA** — анализатор видит «нет решения»), иначе `reject` / `subscribed` / решение.
- Квитирование: цикл = запись `alarm on` ПК 2; к нему подбирается `auto_ack` ПК 1 с `t_seen` в
  [t_on − 0,5 с; t_off + 1 с]; `ack_trial_start/ack_trial_end{policy, press_id, ack_state, confirmed, …}`;
  без нажатия → `ack_state = no_press` (неуспех). Транзакции (`ack_rtt`) берёт анализатор как есть.
  В досье две таблицы: по циклам (n = число выведенных тревог) и по транзакциям.
- Обрывы: `fault_injected{t_loss}` (конвертировано, + `uncertainty_s`), `fault_trial_end{dt_fallback_s, dt_alarm_s,
  dt_departed_s, cause, bound_s, outcome, dt_reconnect_s, reconnect_ok}` из `fallback` / `technical_alarm raised|cleared`
  / `device_departed` ПК 1 после t_loss; `netdown` получает `bound_s` 30 и свою статистику возврата.
- Митшнит: `filtered/trials_all.jsonl` (все `trial_start` окна) → байты по ролям через временные окна попыток.
- Фоновые устройства: EPR с `device_discovered` на ПК 1, не принадлежащие ни одной попытке.
- Вердикт `GESAMT: OK` (пилот: `PILOT: OK`), если каждая фаза сделала все запланированные попытки, нет
  неуспешных попыток (по тем же правилам, что локально), есть все три анализа, есть хотя бы одна проба часов и
  `scenario_end` без `aborted`. Иначе `FEHLER`/`PROBLEME` с перечнем. Отсутствие C++-фона и грубые часы — предупреждения.
- Досье `dossier.md` (полный прогон также `docs/thesis/campaign_netz_ergebnisse.md`): смещение часов и его изменение;
  «nicht entdeckt» отдельно и против TRA; расписание вместо событийного управления в «Grenzen»; n и односторонняя
  95 %-граница Клоппера–Пирсона для каждой метрики; сетевая серия заменяет TRA и квитирование и дополняет обрывы
  (netdown); локальная серия — Voruntersuchung; C++ — только фон.

## 10. Что и где проверяется до ночи

- ПК 2: `--selftest` (Python ≥ 3.10, sdc11073 3.0.x, MDIB, PKI-файлы по именам, IP, UDP 5558, админ, firewall ON,
  запуск одного провайдера с mTLS на порту 12999), `--netdown-test 5`.
- ПК 1: `campaign.py --selftest-merge` (синтетические журналы через слияние, анализаторы, отчёт, досье),
  `--listen --dry-run`, захват на Wi-Fi без прав администратора.
- Двухмашинный пилот `--pilot` + `--merge` → `PILOT: OK` — обязательное условие ночного прогона.
