# HANDOFF_PC2 — инструкция для сессии Claude Code на ПК 2 (192.168.0.105)

Дата: 22.10.2026. Автор: сессия на ПК 1. Твоя роль на ПК 2: **только подготовка окружения и запуск**.
Код не менять, файлы не удалять, содержимое `pki/` никогда не выводить и не копировать в чат, git не использовать.

## 1. Что это

Магистерская работа: ICU-Dashboard на SDC/BICEPS (sdc11073 3.0.0). Ночной измерительный прогон на двух ПК:
ПК 1 (192.168.0.106) запускает headless-Dashboard и наблюдает; ПК 2 (этот ПК, 192.168.0.105) **сам** по расписанию
запускает тестовые провайдеры (`testbed_provider.py`), поднимает тревоги, делает обрывы (в том числе `netdown`
правилом брандмауэра) и пишет журнал. Канала управления между ПК нет, только обмен временем по UDP.
Подробности: `experiments/campaign_2pc/SPEC_C.md` в этой папке.

## 2. Что лежит в папке (корень = папка, где лежат `experiments/`, `testbed/`, `pki/`)

```
experiments/harness.py                      Prov (провайдер как подпроцесс), provider_cmd, DEVICES
experiments/campaign_2pc/pc2_scheduler.py   планировщик (этот ПК)
experiments/campaign_2pc/schedule_c.json    расписание (общее для обоих ПК, не менять)
experiments/campaign_2pc/SPEC_C.md          спецификация
experiments/campaign_2pc/HANDOFF_PC2.md     этот файл
testbed/providers/testbed_provider.py       тестовый SDC-провайдер
testbed/mdib/{ecg,spo2,abp,vent}_mdib_v2.xml
pki/{ecg,spo2,abp,vent}/userkey.pem sdccert.pem cacert.pem   (mTLS; не открывать)
logs/                                       появится при запуске: logs/<scenario_id>/pc2_journal.jsonl, console.txt
```

## 3. Проверка окружения

Нужны: Python ≥ 3.10 (проверено на 3.13), пакет `sdc11073` 3.0.x с зависимостями (lxml и т. д.).

```
python --version
python -c "import sdc11073, importlib.metadata as m; print(m.version('sdc11073'))"
python -c "import sys; print(sys.executable); print(sys.base_prefix)"
ipconfig
```

Адрес 192.168.0.105 должен быть на адаптере, который смотрит в ту же сеть, что ПК 1 (Ethernet или Wi-Fi).
Если адрес другой, запускать с `--ip <адрес>`; ПК 1 тогда нужно сообщить новый адрес (он прописан у него в конфиге).

## 4. Брандмауэр ПК 2 (нужна консоль администратора, спросить пользователя перед выполнением)

Входящие соединения от ПК 1 должны доходить до интерпретатора Python, который реально слушает сокеты:
UDP 3702 (WS-Discovery), TCP 12000–13999 (провайдеры), UDP 5558 (обмен временем). Правило по программе
для **обоих** путей из п. 3 (`sys.executable` и, если это venv, базовый `python.exe` в `sys.base_prefix`):

```
netsh advfirewall firewall add rule name="SDC_C_python_in" dir=in action=allow program="<путь к python.exe>" enable=yes profile=any
```

Проверка, что брандмауэр включён (нужно для `netdown`): `netsh advfirewall show currentprofile` → `State ON`.
Если он выключен, `netdown` не сработает (правило блокировки ничего не блокирует), selftest это сообщит.

## 5. Запуск (из корня папки, консоль администратора из-за `netdown`)

```
python experiments\campaign_2pc\pc2_scheduler.py --selftest
```
Ожидается `SELFTEST: OK`. Проверяется: Python, sdc11073, MDIB, PKI-файлы (по именам), адрес, UDP 5558,
права администратора, состояние брандмауэра, запуск одного провайдера с mTLS на порту 12999 (≈ 10 с).

Необязательно, 10 с: `python experiments\campaign_2pc\pc2_scheduler.py --netdown-test 5` — правило блокировки
к ПК 1 на 5 с, затем проверка, что оно снято (и сторожевой процесс снял бы его сам). Во время теста `ping 192.168.0.106` не отвечает.

Пилот (≈ 15 мин) — только после того, как пользователь сказал, что на ПК 1 запущен `campaign.py --listen --pilot`:
```
python experiments\campaign_2pc\pc2_scheduler.py --pilot
```
Полный прогон (≈ 5,5 ч) — после `PILOT: OK` на ПК 1 и команды пользователя; на ПК 1 должен идти `campaign.py --listen`:
```
python experiments\campaign_2pc\pc2_scheduler.py
```

Что видно в консоли: планировщик ждёт первый запрос времени от ПК 1 (до 30 мин), потом печатает фазы и попытки;
в конце «scenario finished» и ещё 10 минут отвечает ПК 1 (это нормально, не прерывать). `--dry-run` печатает план без запуска.

Остановка: Ctrl-C — провайдер убивается, правила брандмауэра снимаются, журнал закрывается. После Ctrl-C планировщик ещё 60 с сообщает ПК 1 состояние `aborted`; ПК 1 продолжает ждать до 30 мин, поэтому перезапуск (при необходимости с `--phases <оставшиеся фазы>`) можно делать без перезапуска ПК 1. Если после аварии
подозрение на оставшееся правило: `python experiments\campaign_2pc\pc2_scheduler.py --netdown-clear`.

## 6. После прогона

Скопировать на ПК 1 **всю папку** `logs\<scenario_id>\` (в ней `pc2_journal.jsonl`, `console.txt`, `scenario.json`,
`netdown_watchdog.log`). На ПК 1 выполняется `campaign.py --merge … --pc2 <эта папка>`; вердикт выдаёт ПК 1.

## 7. Если что-то не так

- `SELFTEST: PROBLEME` с текстом — передать пользователю дословно; типичные причины: не админ-консоль,
  брандмауэр выключен, нет `sdc11073`, другой IP.
- Провайдер не печатает `ready:` — посмотреть `logs\<id>\console.txt` (ошибки загрузки MDIB/сертификатов).
- `PC 1 did not ask for the clock` — ПК 1 не запустил `--listen`, или UDP 5558 закрыт брандмауэром ПК 2; сценарий всё равно
  начнётся, но слияние без проб времени невозможно → лучше прервать (Ctrl-C) и исправить.
- Порт занят (`Address already in use`) — остался провайдер от прошлого запуска: `tasklist | findstr python`, завершить.

Ничего из этого не требует правок кода. Если кажется, что без правки не обойтись, остановиться и спросить пользователя.
