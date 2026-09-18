# -*- coding: utf-8 -*-
"""Правило рекомендации и надёжность (CONTRACT.md v3, разделы 4 и 8; критерии О3, Т6)."""
from datetime import datetime, timedelta, timezone

import pytest

from vkd.assess.trapped import BeltTable
from vkd.types import EnvironmentSample, Kind, MagMethod, TrajectoryPoint, Window
from vkd.windows.compare import Thresholds, assess_window, recommend

T0 = datetime(2024, 5, 3, 12, 0, tzinfo=timezone.utc)


def traj(minutes: int, saa_pattern):
    """Синтетическая траектория: L=1.3, B/B0=1.2; аномалия по шаблону минут."""
    return [TrajectoryPoint(T0 + timedelta(minutes=i), 0.0, 0.0, 420.0, 20000.0 if saa_pattern(i) else 40000.0,
                            1.3, 1.2, None, MagMethod.DIPOLE, 'approximation', bool(saa_pattern(i)))
            for i in range(minutes)]


def goes(pfu, age_min=5):
    return EnvironmentSample(T0 - timedelta(minutes=age_min), 'goes_p_ge10MeV', pfu, 'pfu', 'noaa', Kind.OBSERVATION,
                             None, None, None, T0, 'preliminary', 'rec#goes')


@pytest.fixture(scope='module')
def belts():
    return BeltTable('min')


def two_windows(belts, g, mmod=1e-6, pattern=lambda i: i < 60):
    tr = traj(24 * 60, pattern)
    th = Thresholds()
    w = [Window(T0, 360), Window(T0 + timedelta(minutes=480), 360)]
    A = [assess_window(x, tr, belts, g, None, [], th, T0, mmod_hits=mmod) for x in w]
    return A, th


def test_missing_mandatory_line_gives_insufficient(belts):
    A, th = two_windows(belts, goes(0.2), mmod=None)
    r = recommend(A, th)
    assert r.verdict == 'insufficient' and any('mmod_stat' in m for m in r.missing)


def test_disabled_source_never_yields_favorable(belts):
    """Т6: отключённый GOES без кеша → «недостаточно», не «предпочтительно»."""
    A, th = two_windows(belts, None)
    r = recommend(A, th)
    assert r.verdict == 'insufficient' and any('spaceweather' in m for m in r.missing)


def test_clean_windows_prefer_less_saa(belts):
    A, th = two_windows(belts, goes(0.2))
    r = recommend(A, th)
    assert r.verdict == 'preferred' and r.preferred.start_utc == T0 + timedelta(minutes=480)
    assert 'spaceweather' in r.per_mechanism_comparison


def test_priority_condition_S3_flags_all_windows(belts):
    """S ≥ 3 (≥1000 pfu): приоритетное предупреждение; все окна требуют проверки, не «недостаточно»."""
    A, th = two_windows(belts, goes(1500.0))
    r = recommend(A, th)
    assert r.verdict == 'all_need_check' and any('приоритетное' in x for x in r.reasons)


def test_warning_S1_S2_marks_windows_as_prototype_policy(belts):
    A, th = two_windows(belts, goes(20.0))
    reasons = [x for a in A for m in a.mechanisms for x in m.needs_check_reasons]
    assert reasons and all('политика прототипа' in x for x in reasons)
    assert all(m.needs_check for a in A for m in a.mechanisms if m.mechanism_id == 'spaceweather')


def test_no_abort_or_continue_commands_in_reasons(belts):
    """Из индексов NOAA не следует ни «прервать», ни «продолжать» ВКД (разбор Codex п. 6)."""
    A, th = two_windows(belts, goes(1500.0))
    text = ' '.join(x for a in A for m in a.mechanisms for x in m.needs_check_reasons)
    assert 'прерыва' not in text and 'продолжа' not in text


def test_equivalent_within_tolerance(belts):
    A, th = two_windows(belts, goes(0.2), pattern=lambda i: (i % 90) < 10)   # одинаковая доля аномалии
    r = recommend(A, th)
    assert r.verdict == 'equivalent' and 'инженерная' in r.tolerance_basis


def test_stale_goes_gives_partial_coverage_but_declared_not_blocking(belts):
    """Разбор Codex п. 14: частичное покрытие объявляется, но не превращается в отказ."""
    A, th = two_windows(belts, goes(0.2, age_min=600))
    m1 = A[0].mechanisms[0]
    assert m1.coverage.value == 'partial'
    r = recommend(A, th)
    assert r.verdict != 'insufficient' and any('частичное' in x for x in r.reasons)


def test_trade_off_when_mechanisms_disagree(belts):
    """Космопогода предпочитает второе окно, метеороиды — первое → компромисс без победителя."""
    tr = traj(24 * 60, lambda i: i < 60)
    th = Thresholds()
    w = [Window(T0, 360), Window(T0 + timedelta(minutes=480), 360)]
    A = [assess_window(w[0], tr, belts, goes(0.2), None, [], th, T0, mmod_hits=1e-7),
         assess_window(w[1], tr, belts, goes(0.2), None, [], th, T0, mmod_hits=3e-7)]
    r = recommend(A, th)
    assert r.verdict == 'trade_off' and 'mmod_stat' in r.per_mechanism_comparison
