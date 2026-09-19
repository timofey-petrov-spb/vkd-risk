# -*- coding: utf-8 -*-
"""Пропуски модели в МИНУТАХ, с причинами (разбор Codex п. 3).

Главная проверка здесь — тождество

    покрытое время (`vkd.orbit.integration.integrate_time`, область Codex)
  + сумма пропусков (`vkd.assess.coverage`, наша область)
  = длительность окна.

Два независимых прохода по одним и тем же данным обязаны сойтись ровно. Если они разойдутся,
значит одна из сторон где-то приписала или потеряла время, и доля покрытия на экране перестанет
соответствовать минутам рядом с ней. Именно на это ссылается требование «показывать длительность
пропусков, известный вклад и причины отсутствия модели».
"""
from datetime import datetime, timedelta, timezone

import pytest

from vkd.assess.coverage import (GAP_BEYOND_HORIZON_RU, GAP_REASON_RU, GAP_STEP_RU, gaps_ru,
                                 uncovered_minutes_by_reason)
from vkd.orbit.integration import integrate_time

T = datetime(2024, 5, 3, 12, tzinfo=timezone.utc)


def ts(seconds):
    return [T + timedelta(seconds=x) for x in seconds]


def _identity(times, values, statuses, a, b, max_gap=60.0):
    """Тождество «покрытое + пропуски = длительность» на произвольном наборе."""
    cov = integrate_time(times, values, a, b, max_gap_seconds=max_gap)
    gaps = uncovered_minutes_by_reason(times, values, statuses, a, b, max_gap_seconds=max_gap)
    total_gap_s = sum(g.minutes for g in gaps) * 60.0
    assert cov.covered_seconds + total_gap_s == pytest.approx((b - a).total_seconds(), abs=1e-9)
    return cov, gaps


def test_summa_propuskov_i_pokrytiya_ravna_dline_okna():
    sec = list(range(0, 361, 60))
    values = [1.0, 1.0, None, None, 1.0, 1.0, 1.0]
    statuses = ['ok', 'ok', 'no_model_L', 'no_model_L', 'ok', 'ok', 'ok']
    cov, gaps = _identity(ts(sec), values, statuses, T, T + timedelta(seconds=360))
    # три интервала касаются узлов без модели (60–120, 120–180, 180–240) — 180 с
    assert dict((g.reason_ru, g.minutes) for g in gaps) == {GAP_REASON_RU['no_model_L']: 3.0}
    assert cov.covered_seconds == 180.0


def test_nol_vyshe_tochki_otrazheniya_pokryvaet_vremya_a_ne_propusk():
    """Известный ноль — это значение, а не отсутствие модели. Подмена одного другим и есть
    запрещённое «заполнение пропусков нулём», только наоборот: она бы СКРЫЛА пропуск."""
    sec = [0, 60, 120]
    cov, gaps = _identity(ts(sec), [0.0, 0.0, 0.0], ['beyond_mirror'] * 3, T, T + timedelta(seconds=120))
    assert gaps == () and cov.covered_seconds == 120.0 and cov.known_integral == 0.0


def test_prichiny_raznyh_koncov_delyatsya_popolam():
    """Где внутри интервала сменилась причина — неизвестно, поэтому минуты делятся, а не
    приписываются одному концу."""
    cov, gaps = _identity(ts([0, 60, 120]), [None, None, 1.0],
                          ['no_model_L', 'inconsistent_BB0', 'ok'], T, T + timedelta(seconds=120))
    d = dict((g.reason_ru, g.minutes) for g in gaps)
    # интервал 0–60 с: концы разные (L вне сетки и B/B0 < 1) — по 30 с каждому;
    # интервал 60–120 с: неизвестен только левый конец — все 60 с ему одному
    assert d[GAP_REASON_RU['no_model_L']] == pytest.approx(0.5)
    assert d[GAP_REASON_RU['inconsistent_BB0']] == pytest.approx(1.5)
    assert cov.covered_seconds == 0.0


def test_vne_izvestnoy_trassy_i_slishkom_bolshoy_shag_nazvany_otdelno():
    """Два разных пропуска: окна нет в трассе и шаг трассы больше допустимого."""
    # трасса начинается через минуту после начала окна и кончается за минуту до конца
    cov, gaps = _identity(ts([60, 120, 300, 360]), [1.0, 1.0, 1.0, 1.0], ['ok'] * 4,
                          T, T + timedelta(seconds=420))
    d = dict((g.reason_ru, g.minutes) for g in gaps)
    assert d[GAP_BEYOND_HORIZON_RU] == pytest.approx(2.0)            # 60 с в начале и 60 с в конце
    assert d[GAP_STEP_RU] == pytest.approx(3.0)                      # разрыв 120–300 с
    assert cov.covered_seconds == 120.0


def test_pustaya_trassa_daet_ves_okno_propuskom():
    gaps = uncovered_minutes_by_reason([], [], [], T, T + timedelta(minutes=360))
    assert len(gaps) == 1 and gaps[0].minutes == pytest.approx(360.0)


def test_stroka_dlya_ekrana_nazyvaet_minuty_i_prichinu():
    _, gaps = _identity(ts([0, 60, 120]), [1.0, None, 1.0], ['ok', 'no_model_L', 'ok'],
                        T, T + timedelta(seconds=120))
    line = gaps_ru(gaps)
    assert 'мин' in line and GAP_REASON_RU['no_model_L'] in line, line


def test_propuski_v_okne_sovpadayut_s_doley_pokrytiya(belts):
    """То же тождество на НАСТОЯЩЕМ окне сервиса: минуты пропусков у механизма согласованы
    с долей покрытия, которую печатает тот же механизм."""
    from tests.test_compare import T0, goes, traj
    from vkd.types import Window
    from vkd.windows.compare import Thresholds, assess_window
    a = assess_window(Window(T0, 360), traj(30 * 60, lambda i: i % 7 < 2), belts,
                      goes(0.2), None, [], Thresholds(), T0, mmod_hits=1e-6)
    m = a.mechanisms[0]
    gap_min = sum(g.minutes for g in m.coverage_gaps)
    assert m.coverage_fraction is not None
    assert gap_min + m.coverage_fraction * 360.0 == pytest.approx(360.0, abs=1e-6)


pytest_plugins = ('tests.test_compare',)
