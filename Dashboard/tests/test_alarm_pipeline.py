"""
tests/test_alarm_pipeline.py
============================
Academic Verification Suite — Circuit I: Mathematical Verification of Alarm Filters
=====================================================================================

Implements test matrices RoC-01..10 (HardwareArtifactFilter) and Bay-01..09
(ClinicalRiskFilter) defined in the approved Verification Plan.

Additional suites:
  Perm-01   — Permutation Invariance (commutativity of the Bayesian product)
  Thresh-*  — Escalation threshold boundary algebraic proofs
  Num-*     — IEEE 754 numerical stability under extreme parameter configurations

Reference: Master's Thesis, Chapter "Methodology" — Sections 2.1–2.3
Run with:  pytest Dashboard/tests/test_alarm_pipeline.py -v
"""

from __future__ import annotations

import importlib.util
import itertools
import math
import random
import sys
import types
import unittest
from collections import deque
from pathlib import Path
from typing import Optional

# ══════════════════════════════════════════════════════════════════════════════
#  Isolated module bootstrap
#  ─────────────────────────────────────────────────────────────────────────────
#  ПРОБЛЕМА:
#    Dashboard/app/__init__.py при импорте немедленно тянет весь стек
#    приложения (Qt, SOAP, WSDiscovery ...). В окружении unit-тестов этих
#    библиотек нет → ImportError.
#
#  РЕШЕНИЕ — три шага:
#    1. Зарегистрировать в sys.modules пустые «stub»-пакеты для Dashboard,
#       Dashboard.app и Dashboard.app.alarms.  Python считает их «уже
#       загруженными» и НЕ запускает их __init__.py.
#    2. Загрузить нужные .py-файлы напрямую через
#       importlib.util.spec_from_file_location, выставив __package__ вручную.
#       Это позволяет относительным импортам (from .device_profile_repo …)
#       резолвиться через уже зарегистрированные stub-пакеты.
#    3. Обычный `from Dashboard.app.alarms.X import Y` отдаёт объекты прямо
#       из кеша sys.modules — никакого I/O.
#
#  _PROJECT_ROOT вычисляется как parents[2] потому что файл находится по
#  пути Master/Dashboard/tests/test_alarm_pipeline.py:
#    parents[0] = tests/
#    parents[1] = Dashboard/
#    parents[2] = Master/   ← корень проекта
# ══════════════════════════════════════════════════════════════════════════════

_PROJECT_ROOT = Path(__file__).resolve().parents[2]   # Master/
_ALARMS_DIR   = _PROJECT_ROOT / 'Dashboard' / 'app' / 'alarms'


def _stub_package(dotted_name: str, fs_path: Path) -> types.ModuleType:
    """
    Регистрирует пустой пакет в sys.modules без выполнения __init__.py.
    Идемпотентен: если имя уже есть — возвращает существующий объект.
    """
    if dotted_name in sys.modules:
        return sys.modules[dotted_name]
    pkg = types.ModuleType(dotted_name)
    pkg.__path__    = [str(fs_path)]   # нужен, чтобы Python считал модуль пакетом
    pkg.__package__ = dotted_name
    pkg.__file__    = str(fs_path / '__init__.py')
    pkg.__spec__    = None
    sys.modules[dotted_name] = pkg
    return pkg


def _load_module(dotted_name: str, file_path: Path, package: str) -> types.ModuleType:
    """
    Загружает один .py-файл как именованный модуль.
    __package__ выставляется явно, чтобы относительные импорты работали
    через уже зарегистрированные stub-пакеты.
    Идемпотентен: если имя уже есть — возвращает существующий объект.
    """
    if dotted_name in sys.modules:
        return sys.modules[dotted_name]
    spec   = importlib.util.spec_from_file_location(dotted_name, file_path)
    module = importlib.util.module_from_spec(spec)   # type: ignore[arg-type]
    module.__package__ = package
    sys.modules[dotted_name] = module
    spec.loader.exec_module(module)                  # type: ignore[union-attr]
    return module


# Шаг 1 — stub-пакеты (их __init__.py НЕ запускаются)
_stub_package('Dashboard',            _PROJECT_ROOT / 'Dashboard')
_stub_package('Dashboard.app',        _PROJECT_ROOT / 'Dashboard' / 'app')
_stub_package('Dashboard.app.alarms', _ALARMS_DIR)

# Шаг 2а — device_profile_repo: только stdlib, нет относительных импортов → грузится первым
_load_module(
    'Dashboard.app.alarms.device_profile_repo',
    _ALARMS_DIR / 'device_profile_repo.py',
    'Dashboard.app.alarms',
)

# Шаг 2б — alarmCoordinator: содержит `from .device_profile_repo import …`
#           → резолвится в уже зарегистрированный Dashboard.app.alarms.device_profile_repo
_load_module(
    'Dashboard.app.alarms.alarmCoordinator',
    _ALARMS_DIR / 'alarmCoordinator.py',
    'Dashboard.app.alarms',
)

# Шаг 3 — обычный импорт: теперь работает через кеш sys.modules, без I/O
from Dashboard.app.alarms.alarmCoordinator import (  # noqa: E402
    AlarmCoordinator,
    ClinicalRiskFilter,
    DeviceAlertEvidence,
    HardwareArtifactFilter,
)
from Dashboard.app.alarms.device_profile_repo import DeviceReliabilityProfile  # noqa: E402


# ══════════════════════════════════════════════════════════════════════════════
#  Общие фабрики и вспомогательные функции
#  ─────────────────────────────────────────────────────────────────────────────
#  Все тесты строят тестовые данные через три фабрики:
#    _make_evidence      — DeviceAlertEvidence DTO (входной «тикет» для пайплайна)
#    _make_buffer        — deque[(value, timestamp)] (скользящее окно метрики)
#    _bayesian_expected  — независимая реализация байесовской формулы
#                          (используется для кросс-валидации production-кода)
# ══════════════════════════════════════════════════════════════════════════════

# Fail-safe профиль: Se=0.5, FAR=0.5 → LR+ = 1.0 (нейтральное обновление Байеса).
# Зеркалирует _FAIL_SAFE_PROFILE из ClinicalRiskFilter.
FAIL_SAFE_PROFILE = DeviceReliabilityProfile(sensitivity=0.5, false_alarm_rate=0.5)

_DEFAULT_ENSEMBLE_UUID = 'ens-0000-0000-0000-0001'


def _make_evidence(
    *,
    alert_key: str = 'alert-001',
    metric_concept: str = 'MDC_PULS_OXIM_SAT_O2',
    biceps_priority: str = 'Hi',
    reliability_profile: Optional[DeviceReliabilityProfile] = None,
    roc_limit: Optional[float] = None,
    ensemble_uuid: str = _DEFAULT_ENSEMBLE_UUID,
) -> DeviceAlertEvidence:
    """
    Фабрика DeviceAlertEvidence.
    Тест указывает только значимые для сценария поля; остальные берутся из дефолтов.
    Никакого сетевого I/O и обращений к БД не происходит.
    """
    return DeviceAlertEvidence(
        alert_key=alert_key,
        metric_concept=metric_concept,
        manufacturer='TestMfr',
        model='TestModel',
        ensemble_uuid=ensemble_uuid,
        biceps_priority=biceps_priority,
        reliability_profile=reliability_profile,
        roc_limit=roc_limit,
    )


def _make_buffer(*readings: tuple[float, float], maxlen: int = 15) -> deque:
    """
    Фабрика скользящего буфера метрики.
    Принимает произвольное число пар (value, timestamp) и пакует в deque(maxlen=15).
    maxlen=15 соответствует размеру окна в SmartAlertAggregator.update_metric_state().
    """
    buf: deque = deque(maxlen=maxlen)
    for item in readings:
        buf.append(item)
    return buf


def _bayesian_expected(
    prior: float,
    profiles: list[DeviceReliabilityProfile],
    p_total: float,
) -> float:
    """
    НЕЗАВИСИМАЯ реализация байесовской формулы — написана заново, без вызова
    production-кода. Используется во всех Bay-* тестах для кросс-валидации:
    если оба числа совпадают, формула в production-коде реализована верно.

    Формула:
        Prior_Odds     = prior / (1 − prior)
        Posterior_Odds = Prior_Odds × ∏ (Se_i / FAR_i)
        Posterior_P    = Posterior_Odds / (1 + Posterior_Odds)
        risk_score     = Posterior_P × p_total
    """
    if not (0.0 < prior < 1.0):
        raise ValueError(f'prior must be in (0, 1), got {prior}')
    if p_total == 0.0:
        return 0.0
    prior_odds: float = prior / (1.0 - prior)
    posterior_odds: float = prior_odds
    for p in profiles:
        # LR+ = Se / FAR — отношение правдоподобия для каждого устройства
        posterior_odds *= p.sensitivity / p.false_alarm_rate
    posterior_p: float = posterior_odds / (1.0 + posterior_odds)
    return posterior_p * p_total


# ══════════════════════════════════════════════════════════════════════════════
#  Suite 1: HardwareArtifactFilter — RoC-01..10
#  ─────────────────────────────────────────────────────────────────────────────
#  Тестируемый метод: HardwareArtifactFilter.validate(evidence, buffer) -> bool
#    True  → тревога физиологически правдоподобна, передать в Stage 2
#    False → артефакт оборудования (RoC > roc_limit), подавить
#
#  Клинический инвариант (Se = 1.0):
#    Любой путь с недостаточными данными (нет истории, нарушение монотонности,
#    неизвестная концепция) ОБЯЗАН вернуть True (fail-open), чтобы реальная
#    тревога никогда не была подавлена молча.
# ══════════════════════════════════════════════════════════════════════════════

class TestHardwareArtifactFilter(unittest.TestCase):

    def setUp(self) -> None:
        # Создаём один экземпляр фильтра на все тесты класса
        self.f = HardwareArtifactFilter()

    # ─────────────────────────────────────────────────────────────────────────
    # RoC-01: Номинальный путь — фильтр работает корректно в штатном режиме
    # ─────────────────────────────────────────────────────────────────────────

    # ► [PASS] RoC-01 Нормальный: Δv=1.0, Δt=2.0 → RoC=0.5 u/s < limit=2.0 → True
    def test_roc01_normal_roc_within_limit_passes(self) -> None:
        """RoC-01 (норма): RoC = 0.5 u/s, limit = 2.0 u/s → физиологично → Pass (True)."""
        # Строим evidence с калиброванным лимитом 2.0 u/s
        ev = _make_evidence(roc_limit=2.0)
        # Буфер: значение 10→11 за 2 секунды → RoC = |11-10| / 2.0 = 0.5 u/s
        buf = _make_buffer((10.0, 0.0), (11.0, 2.0))
        # Проверка: 0.5 > 2.0 = False → фильтр НЕ подавляет → возвращает True
        self.assertTrue(self.f.validate(ev, buf), 'RoC-01: Physiological RoC must pass.')

    # ► [SUPPRESS] RoC-01 Артефакт: Δv=20.0, Δt=1.0 → RoC=20.0 u/s > limit=2.0 → False
    def test_roc01_artifact_above_limit_suppressed(self) -> None:
        """RoC-01 (артефакт): RoC = 20.0 u/s >> limit = 2.0 u/s → аппаратный артефакт → Suppress (False)."""
        # Evidence с лимитом 2.0 u/s
        ev = _make_evidence(roc_limit=2.0)
        # Буфер: падение 90→70 за 1 секунду → RoC = |90-70| / 1.0 = 20.0 u/s
        buf = _make_buffer((90.0, 0.0), (70.0, 1.0))
        # Проверка: 20.0 > 2.0 = True → фильтр подавляет → возвращает False
        self.assertFalse(self.f.validate(ev, buf), 'RoC-01 artifact: Must suppress.')

    # ─────────────────────────────────────────────────────────────────────────
    # RoC-02: Недостаточная история — только одно показание в буфере
    # ─────────────────────────────────────────────────────────────────────────

    # ► [FAIL-OPEN] RoC-02: буфер из 1 элемента → нельзя вычислить Δv/Δt → True
    def test_roc02_single_element_buffer_fail_open(self) -> None:
        """RoC-02: len(buffer)=1 → вычисление RoC невозможно → fail-open (True)."""
        ev = _make_evidence(roc_limit=2.0)
        # Только одно измерение: вычислить разность v[-1]-v[-2] невозможно
        buf = _make_buffer((95.0, 1.0))
        # Условие `if len(metric_buffer) < 2: return True` в production-коде
        # Клинический смысл: при подключении устройства нельзя подавлять тревоги из-за отсутствия истории
        self.assertTrue(self.f.validate(ev, buf), 'RoC-02: buf len=1 must fail-open.')

    # ─────────────────────────────────────────────────────────────────────────
    # RoC-03: Пустой буфер
    # ─────────────────────────────────────────────────────────────────────────

    # ► [FAIL-OPEN] RoC-03: пустой буфер → нет данных вообще → True
    def test_roc03_empty_buffer_fail_open(self) -> None:
        """RoC-03: len(buffer)=0 → нет данных для RoC → fail-open (True)."""
        ev = _make_evidence(roc_limit=2.0)
        # Создаём полностью пустой буфер
        buf = _make_buffer()
        # Аналогично RoC-02: условие `len < 2` → True
        self.assertTrue(self.f.validate(ev, buf), 'RoC-03: Empty buffer must fail-open.')

    # ─────────────────────────────────────────────────────────────────────────
    # RoC-04: Одинаковые метки времени (dt = 0) — защита от деления на ноль
    # ─────────────────────────────────────────────────────────────────────────

    # ► [FAIL-OPEN] RoC-04: t_curr == t_prev → dt=0 → ZeroDivisionError guard → True
    def test_roc04_duplicate_timestamps_fail_open(self) -> None:
        """RoC-04: dt=0 (одинаковые timestamp) → защита от деления на ноль → fail-open (True)."""
        ev = _make_evidence(roc_limit=2.0)
        # Два разных значения, но с ОДИНАКОВОЙ меткой времени t=5.0
        # → dt = 5.0 - 5.0 = 0.0
        buf = _make_buffer((95.0, 5.0), (80.0, 5.0))
        # Условие `if dt <= 0.0: return True` в production-коде
        # Защищает от ZeroDivisionError при вычислении RoC = Δv / dt
        self.assertTrue(self.f.validate(ev, buf), 'RoC-04: dt=0 must fail-open.')

    # ─────────────────────────────────────────────────────────────────────────
    # RoC-05: Отрицательный dt — сдвиг часов / NTP-коррекция
    # ─────────────────────────────────────────────────────────────────────────

    # ► [FAIL-OPEN] RoC-05: t_curr=9.0 < t_prev=10.0 → dt=-1.0 (NTP clock skew) → True
    def test_roc05_negative_dt_clock_skew_fail_open(self) -> None:
        """RoC-05: dt<0 (нарушение монотонности часов / NTP step-correction) → fail-open (True)."""
        ev = _make_evidence(roc_limit=2.0)
        # Текущее время (9.0) МЕНЬШЕ предыдущего (10.0) — нарушение монотонности
        # Возникает при перезапуске monotonic() или NTP step-correction в сети SDC
        buf = _make_buffer((90.0, 10.0), (70.0, 9.0))
        # dt = 9.0 - 10.0 = -1.0 < 0 → условие `if dt <= 0.0: return True`
        # Защита: реальная тревога НЕ должна подавляться из-за ненадёжных timestamp'ов
        self.assertTrue(self.f.validate(ev, buf), 'RoC-05: Negative dt must fail-open.')

    # ─────────────────────────────────────────────────────────────────────────
    # RoC-06: roc_limit = None — неизвестная метрическая концепция
    # ─────────────────────────────────────────────────────────────────────────

    # ► [FAIL-OPEN] RoC-06: roc_limit=None → концепция не в clinical_db.json → True (даже RoC=65)
    def test_roc06_roc_limit_none_fail_open(self) -> None:
        """RoC-06: roc_limit=None (концепция не в clinical_db.json) → fail-open (True)."""
        # roc_limit=None: DeviceHandler не нашёл калибровку для данной концепции
        ev = _make_evidence(roc_limit=None)
        # Огромный скачок: 98→33 за 1с → RoC=65 %/s (физиологически невозможно)
        # НО: без эталонного лимита сравнивать не с чем → подавлять нельзя
        buf = _make_buffer((98.0, 0.0), (33.0, 1.0))
        # Условие `if evidence.roc_limit is None: return True` в production-коде
        self.assertTrue(self.f.validate(ev, buf), 'RoC-06: roc_limit=None must fail-open.')

    # ─────────────────────────────────────────────────────────────────────────
    # RoC-07: RoC ровно равен лимиту — проверка строгого неравенства '>'
    # ─────────────────────────────────────────────────────────────────────────

    # ► [PASS] RoC-07: RoC=2.0 == limit=2.0 → строгое '>' (не '≥') → Pass (True). Клинически: граничная тревога реальная.
    def test_roc07_roc_exactly_on_limit_passes(self) -> None:
        """RoC-07: RoC == roc_limit точно → строгое '>' (не '≥') → Pass (True). Клинически: граничная тревога реальная."""
        ev = _make_evidence(roc_limit=2.0)
        # Δv = |95 - 93| = 2.0, Δt = 1.0 → RoC = 2.0/1.0 = 2.0 u/s = limit ровно
        buf = _make_buffer((95.0, 0.0), (93.0, 1.0))
        # Production-код: `if rate_of_change > max_roc` (СТРОГОЕ >)
        # 2.0 > 2.0 = False → подавления НЕТ → возвращает True
        # КЛИНИЧЕСКАЯ ВАЖНОСТЬ: тревога на точной границе — реальная, не артефакт!
        self.assertTrue(self.f.validate(ev, buf), 'RoC-07: RoC == limit must PASS (strict >).')

    # ─────────────────────────────────────────────────────────────────────────
    # RoC-08: RoC = limit + ε — минимальное превышение над границей
    # ─────────────────────────────────────────────────────────────────────────

    # ► [SUPPRESS] RoC-08: RoC=2.0+1e-9 (ε выше limit) → 2.000000001 > 2.0 = True → False
    def test_roc08_roc_just_above_limit_suppressed(self) -> None:
        """RoC-08: RoC = limit + 1e-9 → минимальное превышение → артефакт → Suppress (False)."""
        roc_limit = 2.0
        epsilon   = 1e-9
        ev = _make_evidence(roc_limit=roc_limit)
        # Δv = roc_limit + epsilon = 2.000000001, Δt = 1.0 → RoC = 2.000000001 u/s
        buf = _make_buffer((95.0, 0.0), (95.0 - (roc_limit + epsilon), 1.0))
        # 2.000000001 > 2.0 = True → подавить
        # Парный тест с RoC-07: вместе доказывают корректность строгого '>'
        self.assertFalse(self.f.validate(ev, buf), 'RoC-08: RoC = limit+ε must SUPPRESS.')

    # ─────────────────────────────────────────────────────────────────────────
    # RoC-09: Клинический сценарий — артефакт пульсоксиметра (SpO₂)
    # ─────────────────────────────────────────────────────────────────────────

    # ► [SUPPRESS] RoC-09: SpO₂ 98%→70% за 1с → RoC=28 %/s >> limit=2.0 → motion artifact → False
    def test_roc09_spo2_motion_artifact_suppressed(self) -> None:
        """RoC-09: SpO₂ падение 98%→70% за 1с (motion artifact, RoC=28 %/s) → Suppress (False)."""
        ev = _make_evidence(
            metric_concept='MDC_PULS_OXIM_SAT_O2',
            roc_limit=2.0,   # физиологический лимит SpO₂: 2 %/с (из клинической литературы)
        )
        # Реальная десатурация не может упасть на 28% за секунду —
        # типичный артефакт движения пациента (motion artifact) пульсоксиметра
        buf = _make_buffer((98.0, 0.0), (70.0, 1.0))   # RoC = 28.0 %/s
        # 28.0 > 2.0 = True → hardware artifact → подавить
        self.assertFalse(
            self.f.validate(ev, buf),
            'RoC-09: SpO₂ motion artifact (RoC=28 %/s) must be suppressed.',
        )

    # ─────────────────────────────────────────────────────────────────────────
    # RoC-10: Клинический сценарий — нормальная флуктуация ЧСС
    # ─────────────────────────────────────────────────────────────────────────

    # ► [PASS] RoC-10: ЧСС 75→78 bpm за 2с → RoC=1.5 bpm/s < limit=5.0 → физиологично → True
    def test_roc10_heart_rate_normal_fluctuation_passes(self) -> None:
        """RoC-10: ЧСС 75→78 bpm за 2с (нормальная вариабельность, RoC=1.5 bpm/s) → Pass (True)."""
        ev = _make_evidence(
            metric_concept='MDC_ECG_HEART_RATE',
            roc_limit=5.0,   # физиологический лимит ЧСС: 5 bpm/с
        )
        # Нормальное изменение ЧСС: 75→78 bpm за 2 секунды
        # RoC = |78-75| / 2.0 = 1.5 bpm/s — в пределах физиологической нормы
        buf = _make_buffer((75.0, 0.0), (78.0, 2.0))
        # 1.5 > 5.0 = False → физиологично → передать в Stage 2
        self.assertTrue(
            self.f.validate(ev, buf),
            'RoC-10: Normal HR fluctuation (1.5 bpm/s) must pass.',
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Структурные инварианты: только последние два элемента буфера используются
    # ─────────────────────────────────────────────────────────────────────────

    # ► [SUPPRESS] Structural: спайк в последней паре buf[-2:-1] → подавить; старые 8 элементов игнорируются
    def test_structural_only_last_two_elements_matter(self) -> None:
        """Structural: validate() читает только buf[-1] и buf[-2]; старые данные не влияют на решение."""
        ev = _make_evidence(roc_limit=2.0)
        # Элементы 0–7: нормальные инкременты (RoC=1.0 u/s — в норме)
        readings: list[tuple[float, float]] = [(float(i), float(i)) for i in range(8)]
        # Элемент 8 → buf[-2]: нормальное значение 50.0 @ t=8
        readings.append((50.0, 8.0))
        # Элемент 9 → buf[-1]: артефактный скачок вниз 10.0 @ t=9
        # RoC = |50.0 - 10.0| / 1.0 = 40.0 u/s >> limit=2.0
        readings.append((10.0, 9.0))
        buf = _make_buffer(*readings)
        # Только пара buf[-2]/buf[-1] определяет результат → артефакт → False
        self.assertFalse(
            self.f.validate(ev, buf),
            'Structural: artifact in last pair must suppress regardless of older readings.',
        )

    # ► [PASS] Structural (inverse): спайк в позиции 3→4, но последняя пара нормальная → True
    def test_structural_large_buffer_with_safe_last_pair_passes(self) -> None:
        """Structural (обратное): старый спайк в позиции 3→4 игнорируется — последняя пара нормальная → Pass."""
        ev = _make_evidence(roc_limit=2.0)
        readings: list[tuple[float, float]] = [
            (100.0, 0.0), (100.0, 1.0), (100.0, 2.0),
            (100.0, 3.0), (50.0,  4.0),   # ← спайк: Δv=50 за 1с → RoC=50, но НЕ последняя пара
            (50.0,  5.0), (50.1,  6.0), (50.2, 7.0),
            (50.3,  8.0),                  # buf[-2]: базовое значение
            (50.4,  9.0),                  # buf[-1]: Δv=0.1, Δt=1 → RoC=0.1 << 2.0
        ]
        buf = _make_buffer(*readings)
        # buf[-2]=50.3, buf[-1]=50.4 → RoC=0.1 < 2.0 → физиологично → True
        self.assertTrue(
            self.f.validate(ev, buf),
            'Structural (converse): old spike must not affect decision if last pair is safe.',
        )


# ══════════════════════════════════════════════════════════════════════════════
#  Suite 2: ClinicalRiskFilter — Bay-01..09
#  ─────────────────────────────────────────────────────────────────────────────
#  Тестируемый метод: ClinicalRiskFilter.compute_risk(evidences, prior) -> float
#
#  Формула (полная):
#    Prior_Odds     = prior / (1 − prior)
#    LR+_i          = Se_i / FAR_i               ← на каждое устройство
#    Posterior_Odds = Prior_Odds × ∏ LR+_i       ← произведение по ансамблю
#    Posterior_P    = Post_Odds / (1 + Post_Odds) ∈ [0, 1]
#    P_total        = max(WEIGHTS[priority_i])    ← берём максимум, не сумму
#    risk_score     = Posterior_P × P_total       ∈ [0.0, 10.0]
#
#  Каждый Bay-* тест:
#    1. Строит список evidences
#    2. Вызывает production-код compute_risk()
#    3. Сравнивает с _bayesian_expected() через assertAlmostEqual(places=8)
# ══════════════════════════════════════════════════════════════════════════════

class TestClinicalRiskFilter(unittest.TestCase):

    def setUp(self) -> None:
        # Один экземпляр фильтра на все тесты класса (stateless)
        self.f = ClinicalRiskFilter()

    # ─────────────────────────────────────────────────────────────────────────
    # Bay-01: Пустой список evidences → short-circuit без вычислений
    # ─────────────────────────────────────────────────────────────────────────

    # ► [risk=0.0] Bay-01: evidences=[] → нет устройств → короткое замыкание → 0.0
    def test_bay01_empty_evidences_returns_zero(self) -> None:
        """Bay-01: evidences=[] → ансамбль пуст → risk_score = 0.0 (short-circuit)."""
        # Передаём пустой список — ансамбль устройств не сформирован
        result = self.f.compute_risk([], prior=0.01)
        # Первый guard в production-коде: `if not evidences: return 0.0`
        # Байесовская формула не вычисляется вообще
        self.assertEqual(0.0, result, 'Bay-01: Empty evidences must return 0.0.')

    # ─────────────────────────────────────────────────────────────────────────
    # Bay-02: Математическое доказательство калибровки ESCALATION_THRESHOLD=5.0
    # ─────────────────────────────────────────────────────────────────────────

    # ► [risk=5.0 ТОЧНО] Bay-02: fail-safe LR+=1.0, Hi (P=10), prior=0.5 → Post_P=0.5 → 0.5×10=5.0
    def test_bay02_failsafe_hi_prior05_is_exactly_5(self) -> None:
        """Bay-02: algebraic proof — fail-safe+Hi+prior=0.5 → risk=5.0 ≡ ESCALATION_THRESHOLD (places=10)."""
        # Evidence: fail-safe профиль (Se=FAR=0.5 → LR+=1.0) + приоритет 'Hi' (вес=10.0)
        ev = _make_evidence(biceps_priority='Hi', reliability_profile=FAIL_SAFE_PROFILE)
        # prior=0.5: равные шансы «есть кризис» / «нет кризиса» (Prior_Odds = 1.0)
        result = self.f.compute_risk([ev], prior=0.5)
        # Ручной расчёт (закодирован как математическое доказательство):
        #   Prior_Odds  = 0.5/0.5 = 1.0
        #   LR+         = 0.5/0.5 = 1.0  (нейтральное обновление — posterior = prior)
        #   Post_Odds   = 1.0 × 1.0 = 1.0
        #   Post_P      = 1.0 / (1.0+1.0) = 0.5
        #   risk_score  = 0.5 × 10.0 = 5.0  ← ровно на пороге ESCALATION_THRESHOLD
        # СМЫСЛ: константа 5.0 означает «некалиброванная Hi-тревога при prior=0.5 = кризис»
        self.assertAlmostEqual(
            5.0, result, places=10,
            msg='Bay-02: Fail-safe + Hi + prior=0.5 must yield exactly 5.0.',
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Bay-03: Fail-safe + приоритет 'Me' → risk = 3.0 (WARN, ниже порога)
    # ─────────────────────────────────────────────────────────────────────────

    # ► [risk=3.0, WARN] Bay-03: fail-safe + Me (P=6.0) + prior=0.5 → Post_P=0.5 × 6.0 = 3.0 < 5.0
    def test_bay03_failsafe_me_prior05_yields_3(self) -> None:
        """Bay-03: fail-safe + 'Me' (P_total=6.0) + prior=0.5 → risk=3.0 < 5.0 → WARN."""
        ev = _make_evidence(biceps_priority='Me', reliability_profile=FAIL_SAFE_PROFILE)
        result = self.f.compute_risk([ev], prior=0.5)
        # Независимая кросс-валидация: Post_P=0.5, WEIGHTS['Me']=6.0 → 0.5×6.0=3.0
        expected = _bayesian_expected(0.5, [FAIL_SAFE_PROFILE], 6.0)
        self.assertAlmostEqual(expected, result, places=10, msg='Bay-03: cross-validation failed.')
        self.assertAlmostEqual(3.0, result, places=10, msg='Bay-03: must equal 3.0 exactly.')
        # Финальная проверка: ниже порога эскалации → WARN, не ESCALATE
        self.assertLess(result, AlarmCoordinator.ESCALATION_THRESHOLD,
                        'Bay-03: Me priority risk must be below escalation threshold.')

    # ─────────────────────────────────────────────────────────────────────────
    # Bay-04: Fail-safe + приоритет 'Lo' → risk = 1.5 (WARN, далеко от порога)
    # ─────────────────────────────────────────────────────────────────────────

    # ► [risk=1.5, WARN] Bay-04: fail-safe + Lo (P=3.0) + prior=0.5 → Post_P=0.5 × 3.0 = 1.5 < 5.0
    def test_bay04_failsafe_lo_prior05_yields_1_5(self) -> None:
        """Bay-04: fail-safe + 'Lo' (P_total=3.0) + prior=0.5 → risk=1.5 < 5.0 → WARN."""
        ev = _make_evidence(biceps_priority='Lo', reliability_profile=FAIL_SAFE_PROFILE)
        result = self.f.compute_risk([ev], prior=0.5)
        # Post_P=0.5, WEIGHTS['Lo']=3.0 → 0.5×3.0=1.5
        expected = _bayesian_expected(0.5, [FAIL_SAFE_PROFILE], 3.0)
        self.assertAlmostEqual(expected, result, places=10, msg='Bay-04: cross-validation failed.')
        self.assertAlmostEqual(1.5, result, places=10, msg='Bay-04: must equal 1.5 exactly.')

    # ─────────────────────────────────────────────────────────────────────────
    # Bay-05: Приоритет 'None' → P_total=0.0 → short-circuit без вычислений
    # ─────────────────────────────────────────────────────────────────────────

    # ► [risk=0.0] Bay-05: priority='None' → WEIGHTS['None']=0.0 → P_total=0.0 → 0.0 (short-circuit)
    def test_bay05_priority_none_short_circuits_to_zero(self) -> None:
        """Bay-05: priority='None' → P_total=0.0 → Байес не вычисляется → risk=0.0."""
        ev = _make_evidence(biceps_priority='None', reliability_profile=FAIL_SAFE_PROFILE)
        result = self.f.compute_risk([ev], prior=0.5)
        # Production-код: после вычисления P_total выполняется `if p_total == 0.0: return 0.0`
        # WEIGHTS['None']=0.0 → p_total=0.0 → немедленный return, формула не считается
        # Смысл: информационные тревоги (уровень None) не участвуют в расчёте клинического риска
        self.assertEqual(0.0, result, 'Bay-05: Priority None must short-circuit to 0.0.')

    # ─────────────────────────────────────────────────────────────────────────
    # Bay-06: Умеренный профиль, малый prior → risk ≈ 4.79 (зона WARN)
    # ─────────────────────────────────────────────────────────────────────────

    # ► [risk≈4.79, WARN] Bay-06: N=3, Se=0.9, FAR=0.2 (LR+=4.5), Hi, prior=0.01 → ~4.79 < 5.0
    def test_bay06_moderate_profile_low_prior_warn_region(self) -> None:
        """Bay-06: N=3 устройства, LR+=4.5, prior=0.01 → risk≈4.79 < 5.0 → WARN (клинический prior мал)."""
        # LR+ = 0.9/0.2 = 4.5 — умеренно хороший профиль
        profile = DeviceReliabilityProfile(sensitivity=0.9, false_alarm_rate=0.2)
        evidences = [
            _make_evidence(biceps_priority='Hi', reliability_profile=profile)
            for _ in range(3)   # три устройства
        ]
        # prior=0.01: базовая вероятность кризиса ОИТ = 1%
        result = self.f.compute_risk(evidences, prior=0.01)
        # Независимый расчёт для кросс-валидации:
        #   LR+        = 4.5
        #   Prior_Odds = 0.01/0.99 ≈ 0.0101
        #   Post_Odds  = 0.0101 × 4.5³ = 0.0101 × 91.125 ≈ 0.9205
        #   Post_P     = 0.9205 / 1.9205 ≈ 0.4793
        #   risk       = 0.4793 × 10.0 ≈ 4.793
        expected = _bayesian_expected(0.01, [profile] * 3, 10.0)
        self.assertAlmostEqual(expected, result, places=8, msg='Bay-06: cross-validation failed.')
        # Ключевая проверка: < 5.0 (тревога реальна, но не кризис при малом prior)
        self.assertLess(result, AlarmCoordinator.ESCALATION_THRESHOLD,
                        'Bay-06: moderate profile + low prior must be in WARN region.')

    # ─────────────────────────────────────────────────────────────────────────
    # Bay-07: Сильный профиль, высокий prior → risk ≈ 9.99 (зона ESCALATE)
    # ─────────────────────────────────────────────────────────────────────────

    # ► [risk≈9.99, ESCALATE] Bay-07: N=3, Se=0.9, FAR=0.1 (LR+=9.0), Hi, prior=0.5 → ~9.99 ≥ 5.0
    def test_bay07_strong_profile_high_prior_escalate(self) -> None:
        """Bay-07: N=3 устройства, LR+=9.0, prior=0.5 → risk≈9.99 >> 5.0 → ESCALATE."""
        # LR+ = 0.9/0.1 = 9.0 — хорошо откалиброванный профиль
        profile = DeviceReliabilityProfile(sensitivity=0.9, false_alarm_rate=0.1)
        evidences = [
            _make_evidence(biceps_priority='Hi', reliability_profile=profile)
            for _ in range(3)
        ]
        # prior=0.5: высокая клиническая настороженность (равные шансы)
        result = self.f.compute_risk(evidences, prior=0.5)
        # Расчёт:
        #   Prior_Odds = 0.5/0.5 = 1.0
        #   Post_Odds  = 1.0 × 9³ = 729.0
        #   Post_P     = 729/730 ≈ 0.99863
        #   risk       = 0.99863 × 10.0 ≈ 9.986
        expected = _bayesian_expected(0.5, [profile] * 3, 10.0)
        self.assertAlmostEqual(expected, result, places=8, msg='Bay-07: cross-validation failed.')
        # risk ≥ 5.0 → должна произойти эскалация
        self.assertGreaterEqual(result, AlarmCoordinator.ESCALATION_THRESHOLD,
                                'Bay-07: strong profile + high prior must ESCALATE (≥5.0).')

    # ─────────────────────────────────────────────────────────────────────────
    # Bay-08: Насыщение постериорной вероятности → risk → P_total = 10.0
    # ─────────────────────────────────────────────────────────────────────────

    # ► [risk→10.0, насыщение] Bay-08: N=5, LR+=99 (Se=0.99/FAR=0.01), prior=0.01 → Post_P→1.0 → ~10.0
    def test_bay08_posterior_saturation_approaches_p_total(self) -> None:
        """Bay-08: N=5, LR+=99 → Post_P→1.0 (насыщение) → risk→10.0, без IEEE 754 overflow."""
        # LR+ = 0.99/0.01 = 99.0 — очень точный профиль
        profile = DeviceReliabilityProfile(sensitivity=0.99, false_alarm_rate=0.01)
        evidences = [
            _make_evidence(biceps_priority='Hi', reliability_profile=profile)
            for _ in range(5)   # пять устройств
        ]
        result = self.f.compute_risk(evidences, prior=0.01)
        # Расчёт (N=5):
        #   Prior_Odds = 0.01/0.99 ≈ 0.0101
        #   Post_Odds  = 0.0101 × 99⁵ ≈ 0.0101 × 9.034×10⁹ ≈ 9.13×10⁷
        #   Post_P     = 9.13e7/(1+9.13e7) ≈ 0.999999989
        #   risk       ≈ 9.9999989

        # 1. Нет переполнения IEEE 754 (+inf или NaN)
        self.assertTrue(math.isfinite(result), 'Bay-08: result must be finite (no overflow/NaN).')
        # 2. Не выходит за физический максимум P_total = 10.0
        self.assertLessEqual(result, 10.0, 'Bay-08: risk must not exceed P_total=10.0.')
        # 3. Насыщение подтверждено: > 9.999
        self.assertGreater(result, 9.999, 'Bay-08: saturation requires risk > 9.999.')

    # ─────────────────────────────────────────────────────────────────────────
    # Bay-09: LR+=1.0 (нейтральный) + слабый prior → минимальный риск ≈ 0.1
    # ─────────────────────────────────────────────────────────────────────────

    # ► [risk≈0.10, WARN] Bay-09: Se=FAR=0.5 (LR+=1.0), Hi, prior=0.01 → posterior=prior → risk≈0.10
    def test_bay09_neutral_lr_weak_prior_minimal_risk(self) -> None:
        """Bay-09: LR+=1.0 (нейтральный) + prior=0.01 → информации ноль → risk≈0.10 (почти нулевой)."""
        # Нейтральный профиль: Se=FAR=0.5 → LR+=1.0 → posterior не меняется от prior
        profile = DeviceReliabilityProfile(sensitivity=0.5, false_alarm_rate=0.5)
        ev = _make_evidence(biceps_priority='Hi', reliability_profile=profile)
        result = self.f.compute_risk([ev], prior=0.01)
        # Расчёт:
        #   Prior_Odds = 0.01/0.99 ≈ 0.0101
        #   LR+        = 1.0  → Post_Odds = Prior_Odds (нет информации)
        #   Post_P     ≈ 0.01
        #   risk       = 0.01 × 10.0 = 0.10
        expected = _bayesian_expected(0.01, [profile], 10.0)
        self.assertAlmostEqual(expected, result, places=8, msg='Bay-09: cross-validation failed.')
        # Намного ниже порога эскалации
        self.assertLess(result, AlarmCoordinator.ESCALATION_THRESHOLD,
                        'Bay-09: neutral LR+ with low prior must be far below threshold.')
        # Числовая проверка: ≈0.10 (±0.01)
        self.assertAlmostEqual(0.1, result, delta=0.01,
                               msg='Bay-09: risk_score must be ≈ 0.10 (within ±0.01).')

    # ─────────────────────────────────────────────────────────────────────────
    # Структурные инварианты ClinicalRiskFilter
    # ─────────────────────────────────────────────────────────────────────────

    # ► [идентичен fail-safe] None-профиль: reliability_profile=None → _FAIL_SAFE_PROFILE → результаты равны
    def test_none_profile_identical_to_explicit_failsafe(self) -> None:
        """None-профиль: фильтр подставляет _FAIL_SAFE_PROFILE → результат bit-exact совпадает с явным."""
        # evidence с reliability_profile=None (DeviceHandler не нашёл калибровку)
        ev_none = _make_evidence(biceps_priority='Hi', reliability_profile=None)
        # evidence с явно указанным fail-safe профилем (эталон)
        ev_safe = _make_evidence(biceps_priority='Hi', reliability_profile=FAIL_SAFE_PROFILE)
        result_none = self.f.compute_risk([ev_none], prior=0.5)
        result_safe = self.f.compute_risk([ev_safe], prior=0.5)
        # Метод _get_profile() подставляет _FAIL_SAFE_PROFILE при None
        # Оба результата должны быть bit-exact равны (places=12 ≈ 12 значащих цифр)
        self.assertAlmostEqual(result_none, result_safe, places=12,
                               msg='None profile must produce identical result to explicit fail-safe.')

    # ► [P_total=max=10.0, не sum=19.0] Смешанные приоритеты: Lo+Me+Hi → max(3,6,10)=10.0
    def test_mixed_priorities_p_total_is_max_not_sum(self) -> None:
        """Смешанные приоритеты: P_total = max(Lo=3, Me=6, Hi=10) = 10.0 (не сумма = 19.0)."""
        profile = DeviceReliabilityProfile(sensitivity=0.9, false_alarm_rate=0.1)
        # Три устройства с разными BICEPS-приоритетами
        evidences = [
            _make_evidence(biceps_priority='Lo', reliability_profile=profile),  # вес 3.0
            _make_evidence(biceps_priority='Me', reliability_profile=profile),  # вес 6.0
            _make_evidence(biceps_priority='Hi', reliability_profile=profile),  # вес 10.0 ← wins
        ]
        result = self.f.compute_risk(evidences, prior=0.5)
        # Правильно: P_total = max(3.0, 6.0, 10.0) = 10.0
        expected_max = _bayesian_expected(0.5, [profile] * 3, 10.0)
        # Неправильно (ошибка суммирования): P_total = 3+6+10 = 19.0 — завышение в 1.9×
        expected_sum = _bayesian_expected(0.5, [profile] * 3, 19.0)
        # Результат должен совпадать с max-версией
        self.assertAlmostEqual(expected_max, result, places=8,
                               msg='P_total must use max weight (Hi=10.0), not sum.')
        # И НЕ совпадать с sum-версией (грубая ошибка — разница ~2 единицы)
        self.assertNotAlmostEqual(expected_sum, result, places=2,
                                  msg='P_total must NOT equal sum of weights (would be 19.0).')

    # ► [risk=0.0] Неизвестный приоритет → dict.get() fallback 0.0 → P_total=0.0 → risk=0.0
    def test_unknown_priority_string_maps_to_zero(self) -> None:
        """Неизвестная строка приоритета → dict.get(key, 0.0) → P_total=0.0 → risk=0.0 (защита)."""
        ev = _make_evidence(
            biceps_priority='UNRECOGNISED_PRIORITY_99',   # нет в _PRIORITY_WEIGHTS
            reliability_profile=FAIL_SAFE_PROFILE,
        )
        result = self.f.compute_risk([ev], prior=0.5)
        # `self._PRIORITY_WEIGHTS.get('UNRECOGNISED_PRIORITY_99', 0.0)` → 0.0
        # P_total = max({0.0}) = 0.0 → short-circuit → return 0.0
        # Защита от мусорных BICEPS-данных из нестандартных провайдеров
        self.assertEqual(0.0, result,
                         'Unknown priority string must map to weight 0.0 and return risk=0.0.')


# ══════════════════════════════════════════════════════════════════════════════
#  Suite 3: Perm-01 — Инвариант перестановок (коммутативность произведения LR+)
#  ─────────────────────────────────────────────────────────────────────────────
#  Формальное свойство:
#    ∀ перестановка σ: ∏ LR+_i == ∏ LR+_{σ(i)}  ⟹  risk_score одинаков
#
#  Клинический смысл: порядок прихода пакетов EpisodicAlertReport по сети SDC
#  не должен влиять на решение об эскалации.
#  Нарушение = недетерминизм → нарушение контракта ISO/IEEE 11073 MDPWS.
# ══════════════════════════════════════════════════════════════════════════════

class TestPermutationInvariance(unittest.TestCase):

    def setUp(self) -> None:
        self.f = ClinicalRiskFilter()

    # ► [bit-exact 6 перестановок] Perm-01 N=3 exhaustive: все 3!=6 порядков → одинаковый risk (places=12)
    def test_perm01_three_device_all_permutations_bit_exact(self) -> None:
        """Perm-01 (N=3, exhaustive): все 3!=6 перестановок ансамбля → risk bit-exact идентичен."""
        # Три разнородных устройства с отличающимися LR+
        profiles = [
            DeviceReliabilityProfile(sensitivity=0.95, false_alarm_rate=0.05),   # LR+ = 19.0
            DeviceReliabilityProfile(sensitivity=0.80, false_alarm_rate=0.15),   # LR+ ≈ 5.33
            DeviceReliabilityProfile(sensitivity=0.70, false_alarm_rate=0.30),   # LR+ ≈ 2.33
        ]
        priorities = ['Hi', 'Hi', 'Me']
        evidences = [
            _make_evidence(
                alert_key=f'alert-{i:03d}',
                biceps_priority=priorities[i],
                reliability_profile=profiles[i],
            )
            for i in range(3)
        ]

        # Перебираем ВСЕ 6 перестановок индексов [0,1,2] через itertools.permutations
        scores: list[float] = []
        for perm_idx in itertools.permutations(range(3)):
            permuted = [evidences[j] for j in perm_idx]
            scores.append(self.f.compute_risk(permuted, prior=0.01))

        # Базовый результат — первая перестановка (0,1,2)
        baseline = scores[0]
        # Все остальные 5 результатов должны совпадать bit-exact (places=12)
        for i, score in enumerate(scores[1:], start=1):
            self.assertAlmostEqual(
                baseline, score, places=12,
                msg=(
                    f'Perm-01: permutation #{i} → {score:.15f} '
                    f'≠ baseline {baseline:.15f} — ordering must not affect risk.'
                ),
            )

    # ► [bit-exact 20 случайных перестановок] Perm-01 N=5 sampled: seed=42, 20 shuffle → одинаковый risk
    def test_perm01_five_device_random_permutations(self) -> None:
        """Perm-01 (N=5, sampled): 20 случайных перестановок из 5!=120, seed=42 → risk неизменен."""
        # Фиксированный seed для воспроизводимости в CI/диссертации
        random.seed(42)
        # Пять устройств с разными профилями (убывающая точность)
        profiles = [
            DeviceReliabilityProfile(sensitivity=0.95, false_alarm_rate=0.05),
            DeviceReliabilityProfile(sensitivity=0.90, false_alarm_rate=0.10),
            DeviceReliabilityProfile(sensitivity=0.85, false_alarm_rate=0.20),
            DeviceReliabilityProfile(sensitivity=0.75, false_alarm_rate=0.25),
            DeviceReliabilityProfile(sensitivity=0.60, false_alarm_rate=0.40),
        ]
        priorities = ['Hi', 'Hi', 'Me', 'Me', 'Lo']
        evidences = [
            _make_evidence(
                alert_key=f'alert-{i:03d}',
                biceps_priority=priorities[i],
                reliability_profile=profiles[i],
            )
            for i in range(5)
        ]

        # Базовый результат в исходном порядке [0,1,2,3,4]
        baseline = self.f.compute_risk(list(evidences), prior=0.01)
        indices = list(range(5))

        # 20 случайных shuffle-тестов
        for trial in range(20):
            shuffled = random.sample(indices, len(indices))
            permuted = [evidences[j] for j in shuffled]
            score = self.f.compute_risk(permuted, prior=0.01)
            self.assertAlmostEqual(
                baseline, score, places=12,
                msg=f'Perm-01 (N=5, trial {trial}): shuffled order must not change risk_score.',
            )


# ══════════════════════════════════════════════════════════════════════════════
#  Suite 4: Граничные условия порога эскалации — алгебраические доказательства
#  ─────────────────────────────────────────────────────────────────────────────
#  Кодируют инварианты дизайна ESCALATION_THRESHOLD = 5.0:
#    Hi  + fail-safe + prior=0.5 → 5.0 ≥ threshold → ESCALATE (граничное)
#    Me  + fail-safe + prior=0.5 → 3.0  < threshold → WARN
#    Lo  + fail-safe + prior=0.5 → 1.5  < threshold → WARN
#    None                        → 0.0              → нет риска
# ══════════════════════════════════════════════════════════════════════════════

class TestEscalationThresholdSemantics(unittest.TestCase):

    def setUp(self) -> None:
        self.f = ClinicalRiskFilter()
        # Читаем константу из production-кода, не хардкодим 5.0 в тестах
        self.threshold = AlarmCoordinator.ESCALATION_THRESHOLD

    # ► [risk≥threshold] Hi+fail-safe+prior=0.5 → 5.0 ≥ ESCALATION_THRESHOLD → ESCALATE
    def test_thresh_hi_failsafe_at_or_above_threshold(self) -> None:
        """Algebraic proof: Hi+fail-safe+prior=0.5 → risk=5.0 ≥ threshold=5.0 → ESCALATE."""
        ev = _make_evidence(biceps_priority='Hi', reliability_profile=FAIL_SAFE_PROFILE)
        risk = self.f.compute_risk([ev], prior=0.5)
        # WEIGHTS['Hi']=10.0, Post_P=0.5 → 0.5×10.0=5.0 ≥ 5.0
        self.assertGreaterEqual(risk, self.threshold,
                                'Hi + fail-safe + prior=0.5 must reach escalation threshold.')

    # ► [risk<threshold] Me+fail-safe+prior=0.5 → 3.0 < ESCALATION_THRESHOLD → WARN
    def test_thresh_me_failsafe_below_threshold(self) -> None:
        """Algebraic proof: Me+fail-safe+prior=0.5 → risk=3.0 < threshold=5.0 → WARN (не ESCALATE)."""
        ev = _make_evidence(biceps_priority='Me', reliability_profile=FAIL_SAFE_PROFILE)
        risk = self.f.compute_risk([ev], prior=0.5)
        # WEIGHTS['Me']=6.0, Post_P=0.5 → 0.5×6.0=3.0 < 5.0
        self.assertLess(risk, self.threshold,
                        'Me + fail-safe + prior=0.5 must NOT reach escalation threshold.')

    # ► [risk<threshold] Lo+fail-safe+prior=0.5 → 1.5 << ESCALATION_THRESHOLD → WARN
    def test_thresh_lo_failsafe_well_below_threshold(self) -> None:
        """Algebraic proof: Lo+fail-safe+prior=0.5 → risk=1.5 << threshold=5.0 → WARN."""
        ev = _make_evidence(biceps_priority='Lo', reliability_profile=FAIL_SAFE_PROFILE)
        risk = self.f.compute_risk([ev], prior=0.5)
        # WEIGHTS['Lo']=3.0, Post_P=0.5 → 0.5×3.0=1.5 << 5.0
        self.assertLess(risk, self.threshold,
                        'Lo + fail-safe must be well below escalation threshold.')

    # ► [risk=0.0] None: WEIGHTS['None']=0.0 → short-circuit → 0.0 при ЛЮБОМ профиле и prior
    def test_thresh_none_always_zero(self) -> None:
        """Algebraic proof: priority='None' → WEIGHTS=0.0 → P_total=0.0 → risk=0.0 (всегда)."""
        ev = _make_evidence(biceps_priority='None', reliability_profile=FAIL_SAFE_PROFILE)
        risk = self.f.compute_risk([ev], prior=0.5)
        # max({WEIGHTS['None']}) = max({0.0}) = 0.0 → short-circuit → return 0.0
        self.assertEqual(0.0, risk, 'None priority must always yield risk=0.0.')

    # ► [risk≥threshold] Me+Se=0.99+N=3+prior=0.5: хорошая калибровка поднимает Me выше порога
    def test_calibrated_profile_can_escalate_from_me_priority(self) -> None:
        """Sensitivity: Me-ансамбль с LR+=99 и prior=0.5 → risk≥5.0 (порог динамический, не per-priority)."""
        profile = DeviceReliabilityProfile(sensitivity=0.99, false_alarm_rate=0.01)
        # Три устройства с Me-приоритетом, но с отличной калибровкой
        evidences = [
            _make_evidence(biceps_priority='Me', reliability_profile=profile)
            for _ in range(3)
        ]
        risk = self.f.compute_risk(evidences, prior=0.5)
        # Post_Odds = 1.0 × 99³ = 970299 → Post_P ≈ 1.0 → risk ≈ 1.0×6.0 = 6.0 > 5.0
        # Доказывает: порог не захардкожен по типу приоритета — калибровка повышает риск динамически
        self.assertGreaterEqual(risk, self.threshold,
                                'Calibrated Me-ensemble with high prior must exceed threshold.')


# ══════════════════════════════════════════════════════════════════════════════
#  Suite 5: Численная стабильность IEEE 754
#  ─────────────────────────────────────────────────────────────────────────────
#  Произведение Prior_Odds × ∏ LR+_i может стать очень большим.
#  Переполнение до +inf приводит к Post_P = inf/(1+inf) = NaN,
#  что молча ломает вычисление риска. Эти тесты документируют числовые пределы.
# ══════════════════════════════════════════════════════════════════════════════

class TestNumericalStability(unittest.TestCase):

    def setUp(self) -> None:
        self.f = ClinicalRiskFilter()

    # ► [конечный ∈ (9.999, 10.0]] Num-01: N=50, LR+=99 → Post_Odds≈6×10^97 → в пределах double → risk→10.0
    def test_large_ensemble_no_overflow_saturates_correctly(self) -> None:
        """Num-01: N=50, LR+=99 → Post_Odds≈6×10^97 (в пределах double≈1.8×10^308) → насыщение risk→10.0."""
        profile = DeviceReliabilityProfile(sensitivity=0.99, false_alarm_rate=0.01)
        # 50 устройств с LR+=99 каждое
        evidences = [
            _make_evidence(biceps_priority='Hi', reliability_profile=profile)
            for _ in range(50)
        ]
        result = self.f.compute_risk(evidences, prior=0.01)
        # 99^50 ≈ 6×10^99, умноженное на Prior_Odds≈0.0101 → Post_Odds≈6×10^97
        # Максимум IEEE 754 double: ~1.8×10^308 → переполнения нет

        # 1. Нет +inf, NaN, или иных non-finite значений
        self.assertTrue(math.isfinite(result), 'Num-01: result must be finite (no IEEE 754 overflow).')
        # 2. Не выходит за P_total = 10.0
        self.assertLessEqual(result, 10.0, 'Num-01: risk must not exceed P_total=10.0.')
        # 3. Насыщение: близко к P_total
        self.assertGreater(result, 9.999, 'Num-01: saturated result must be > 9.999.')

    # ► [конечный > 0.0] Num-02: prior=0.0001 → Prior_Odds≈1e-4 → Post_Odds≈1.9e-3 → не underflow
    def test_very_low_prior_no_underflow(self) -> None:
        """Num-02: prior=0.0001 (очень малый) → Post_Odds≈1.9×10^-3 → не underflow → finite > 0."""
        profile = DeviceReliabilityProfile(sensitivity=0.95, false_alarm_rate=0.05)
        ev = _make_evidence(biceps_priority='Hi', reliability_profile=profile)
        result = self.f.compute_risk([ev], prior=0.0001)
        # Prior_Odds = 0.0001/0.9999 ≈ 1.0001×10^-4
        # Post_Odds  = 1.0001×10^-4 × 19 ≈ 1.9×10^-3 → Post_P ≈ 0.0019
        # IEEE 754 double min нормальное ≈ 2.2×10^-308 → подпольного нет

        # Конечный результат
        self.assertTrue(math.isfinite(result), 'Num-02: result must be finite.')
        # Строго больше нуля (нет потери значимости до 0.0)
        self.assertGreater(result, 0.0, 'Num-02: result must be strictly positive (no underflow).')

    # ► [конечный ∈ (9.0, 10.0]] Num-03: prior=0.99 → Prior_Odds=99.0 → Post_Odds=891 → risk≈9.99
    def test_high_prior_boundary_prior_0_99_no_overflow(self) -> None:
        """Num-03: prior=0.99 → Prior_Odds=99.0 (большой до LR+) → Post_Odds=891 → risk≈9.99, конечный."""
        profile = DeviceReliabilityProfile(sensitivity=0.9, false_alarm_rate=0.1)
        ev = _make_evidence(biceps_priority='Hi', reliability_profile=profile)
        result = self.f.compute_risk([ev], prior=0.99)
        # Prior_Odds = 0.99/0.01 = 99.0 (уже большой)
        # Post_Odds  = 99.0 × 9.0 = 891.0 → Post_P = 891/892 ≈ 0.99888
        # risk       = 0.99888 × 10.0 ≈ 9.99

        self.assertTrue(math.isfinite(result), 'Num-03: result must be finite.')
        self.assertLessEqual(result, 10.0, 'Num-03: risk must not exceed P_total=10.0.')
        self.assertGreater(result, 9.0, 'Num-03: high prior + strong profile must approach 10.0.')


# ══════════════════════════════════════════════════════════════════════════════
#  Suite 6: Интеграционные тесты полного пайплайна (AlarmCoordinator.evaluate)
#  ─────────────────────────────────────────────────────────────────────────────
#  Тестируется полный двухэтапный пайплайн БЕЗ mock-ов отдельных стадий:
#
#  evaluate(triggering_ev, metric_buf, ensemble_evidences) -> AlarmDecision
#    ├── Stage 1: HardwareArtifactFilter.validate()
#    │     False → AlarmDecision(escalate=False, risk=-1.0,
#    │                           suppression_stage='HardwareArtifactFilter')
#    └── Stage 2: ClinicalRiskFilter.compute_risk()
#          risk ≥ 5.0 → AlarmDecision(escalate=True,  suppression_stage=None)
#          risk < 5.0 → AlarmDecision(escalate=False, suppression_stage='ClinicalRiskFilter')
# ══════════════════════════════════════════════════════════════════════════════

class TestAlarmCoordinatorPipeline(unittest.TestCase):

    def setUp(self) -> None:
        # Создаём фасад AlarmCoordinator (содержит оба фильтра внутри)
        self.coordinator = AlarmCoordinator()

    def _make_decision(
        self,
        roc_limit: Optional[float],
        buffer: deque,
        profile: Optional[DeviceReliabilityProfile],
        priority: str = 'Hi',
        prior: float = 0.5,
        n_ensemble: int = 1,
    ):
        """
        Хелпер: строит triggering evidence + список из n_ensemble ансамблевых evidence
        и вызывает coordinator.evaluate().
        Примечание: evaluate() использует дефолт prior=0.01 внутри compute_risk(),
        параметр prior в хелпере — только для документирования намерения теста.
        """
        triggering = _make_evidence(
            biceps_priority=priority,
            reliability_profile=profile,
            roc_limit=roc_limit,
        )
        # Формируем ансамбль из n_ensemble устройств с теми же параметрами
        ensemble = [
            _make_evidence(
                alert_key=f'alert-ens-{i}',
                biceps_priority=priority,
                reliability_profile=profile,
                roc_limit=roc_limit,
            )
            for i in range(n_ensemble)
        ]
        return self.coordinator.evaluate(triggering, buffer, ensemble)

    # ─────────────────────────────────────────────────────────────────────────
    # Stage 1 suppress: артефакт с RoC >> limit подавляется на первой стадии
    # ─────────────────────────────────────────────────────────────────────────

    # ► [escalate=False, stage='HardwareArtifactFilter', risk=-1.0] Stage1: RoC=48>>limit=2 → подавить
    def test_pipeline_stage1_suppresses_artifact(self) -> None:
        """Pipeline Stage 1 suppress: RoC=48 >> limit=2.0 → escalate=False, risk=-1.0, stage=HardwareArtifactFilter."""
        # Буфер: падение 98→50 за 1с → RoC = |98-50|/1 = 48.0 >> 2.0 → артефакт
        buf = _make_buffer((98.0, 0.0), (50.0, 1.0))
        decision = self._make_decision(roc_limit=2.0, buffer=buf, profile=FAIL_SAFE_PROFILE)

        # Проверяем все три поля AlarmDecision для пути Stage 1:
        # 1. Тревога НЕ эскалируется
        self.assertFalse(decision.escalate, 'Stage 1 artifact path must not escalate.')
        # 2. Источник подавления: Stage 1
        self.assertEqual('HardwareArtifactFilter', decision.suppression_stage,
                         'Stage 1 path must identify HardwareArtifactFilter as suppressor.')
        # 3. Сигнальный маркер risk=-1.0: «Stage 2 не запускался»
        self.assertEqual(-1.0, decision.risk_score,
                         'Stage 1 suppress must set risk_score sentinel = -1.0.')

    # ─────────────────────────────────────────────────────────────────────────
    # Stage 2 ESCALATE: сильный профиль + ансамбль из 3 устройств → risk >> 5.0
    # ─────────────────────────────────────────────────────────────────────────

    # ► [escalate=True, stage=None, risk≥5.0] Stage2 ESCALATE: N=3, LR+=99, prior=0.01(default) → risk≈10
    def test_pipeline_stage2_escalates_hi_prior05(self) -> None:
        """Pipeline Stage 2 ESCALATE: N=3, Se=0.99, Hi → risk≥5.0 → escalate=True, stage=None."""
        # Буфер: RoC = |95-94|/1 = 1.0 < 2.0 → Stage 1 пропускает (не артефакт)
        buf = _make_buffer((95.0, 0.0), (94.0, 1.0))
        # Сильный профиль: LR+ = 0.99/0.01 = 99.0
        # С prior=0.01 (дефолт evaluate()) и N=3:
        #   Post_Odds ≈ 0.0101 × 99³ ≈ 0.0101 × 970299 ≈ 9800 → Post_P≈0.9999 → risk≈9.99
        strong_profile = DeviceReliabilityProfile(sensitivity=0.99, false_alarm_rate=0.01)
        decision = self._make_decision(
            roc_limit=2.0, buffer=buf, profile=strong_profile,
            priority='Hi', n_ensemble=3,
        )

        # 1. Тревога ЭСКАЛИРУЕТСЯ
        self.assertTrue(decision.escalate, 'Strong N=3 profile must escalate.')
        # 2. Нет источника подавления (тревога прошла оба фильтра)
        self.assertIsNone(decision.suppression_stage, 'Escalated alarm must have no suppression_stage.')
        # 3. risk_score ≥ порога эскалации
        self.assertGreaterEqual(decision.risk_score, AlarmCoordinator.ESCALATION_THRESHOLD,
                                'Escalated alarm risk_score must be ≥ ESCALATION_THRESHOLD.')

    # ─────────────────────────────────────────────────────────────────────────
    # Stage 2 WARN: fail-safe + Me приоритет → risk < 5.0, тревога реальная но не кризис
    # ─────────────────────────────────────────────────────────────────────────

    # ► [escalate=False, stage='ClinicalRiskFilter', 0<risk<5] Stage2 WARN: fail-safe+Me → risk≈0.06
    def test_pipeline_stage2_warns_moderate_risk(self) -> None:
        """Pipeline Stage 2 WARN: fail-safe+Me+prior=0.01(default) → 0<risk<5.0 → stage=ClinicalRiskFilter."""
        # Буфер: RoC = |75.5-75.0|/1 = 0.5 < 5.0 → Stage 1 пропускает
        buf = _make_buffer((75.0, 0.0), (75.5, 1.0))
        decision = self._make_decision(
            roc_limit=5.0, buffer=buf, profile=FAIL_SAFE_PROFILE, priority='Me',
        )

        # 1. Тревога НЕ эскалируется (риск ниже порога)
        self.assertFalse(decision.escalate, 'Moderate risk path must not escalate.')
        # 2. Подавление на Stage 2 (не на Stage 1)
        self.assertEqual('ClinicalRiskFilter', decision.suppression_stage,
                         'Below-threshold path must identify ClinicalRiskFilter as suppressor.')
        # 3. risk > 0.0: это WARN, не артефакт Stage 1 (у которого risk=-1.0)
        self.assertGreater(decision.risk_score, 0.0,
                           'WARN path risk must be positive (not Stage 1 sentinel -1.0).')
        # 4. risk < 5.0: действительно ниже порога
        self.assertLess(decision.risk_score, AlarmCoordinator.ESCALATION_THRESHOLD,
                        'WARN path risk must be below ESCALATION_THRESHOLD.')

    # ─────────────────────────────────────────────────────────────────────────
    # Fail-open: пустой буфер → Stage 1 пропускает → Stage 2 отрабатывает нормально
    # ─────────────────────────────────────────────────────────────────────────

    # ► [stage≠'HardwareArtifactFilter', risk≠-1.0] Fail-open: buf=[] → Stage1 True → Stage2 работает
    def test_pipeline_fail_open_empty_buffer(self) -> None:
        """Pipeline fail-open: пустой буфер → Stage 1 не подавляет → Stage 2 отрабатывает нормально."""
        # Пустой буфер: len=0 < 2 → Stage 1 условие `len < 2` → return True (fail-open)
        buf = _make_buffer()
        decision = self._make_decision(
            roc_limit=2.0, buffer=buf, profile=FAIL_SAFE_PROFILE, priority='Hi',
        )

        # 1. Результат — валидный объект AlarmDecision (pipeline не упал)
        self.assertIsNotNone(decision, 'Pipeline must return a valid AlarmDecision object.')
        # 2. Stage 1 НЕ является источником подавления (он пропустил тревогу через fail-open)
        self.assertNotEqual('HardwareArtifactFilter', decision.suppression_stage,
                            'Empty buffer fail-open must NOT set Stage 1 as suppressor.')
        # 3. risk ≠ -1.0: Stage 2 реально запускался и вернул числовой риск
        #    (fail-safe + Hi + prior=0.01 дефолт → risk≈0.1 → WARN)
        self.assertNotEqual(-1.0, decision.risk_score,
                            'Fail-open path must have real risk_score, not Stage 1 sentinel -1.0.')


if __name__ == '__main__':
    unittest.main(verbosity=2)

