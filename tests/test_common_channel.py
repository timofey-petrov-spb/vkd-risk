# -*- coding: utf-8 -*-
"""Общий канал протонных событий — правило R14 (ТЗ круга 11, раздел 2).

Решение владельца 19.09 (docs/design/RESHENIE_TEKUSCHIY_REZHIM.md, вариант 2). Проверено на
развёрнутом сервисе: в текущем режиме вердикт был отказом ВСЕГДА и при любых данных, потому что
окно, начинающееся позже чем через 60 мин после последнего измерения GOES, по построению не
попадает в горизонт наблюдения, а пустой канал обнулял всю обязательную линию. При этом рядом
с отказом сервис печатал факторы окон, различающиеся в 6,8 раза по флюенсу.

Прогноза потока протонов с разрешением по окну не существует НИ У ОДНОГО источника, поэтому
пробел здесь структурный: он не закрывается ни ожиданием следующего выпуска, ни новыми данными.
Канал, который по природе одинаков для всех сравниваемых окон, объявляется общим: он их не
различает, вывод не блокирует и уходит в объявленную область.

Что здесь проверяется:
  * три случая полного отказа сохранены (нет наблюдения, наблюдение устарело, пуста другая линия);
  * при фоновом наблюдении вердикт выносится, и область вывода содержит обе обязательные фразы;
  * уровень выше фона — не отказ, а условие проверки ВСЕМ окнам (исход all_need_check);
  * общий канал не может изменить порядок окон: он для них один и тот же;
  * суточная вероятность NOAA не является условием применения правила и не пересчитывается.
"""
from dataclasses import replace
from datetime import timedelta

import pytest

from tests.test_compare import T0, belts, goes, kp_sample, traj, two_windows  # noqa: F401
from vkd.types import Coverage, EnvironmentSample, Kind, Window
from vkd.windows.compare import (S1_PFU, Thresholds, assess_window, proton_channel_state,
                                 recommend)

# Обе фразы обязаны стоять в объявленной области — ТЗ раздела 2 называет их дословно.
PHRASE_NO_FORECAST = 'прогноза потока с разрешением по окну не существует ни у одного источника'
PHRASE_UNKNOWN_IN_WINDOW = 'наличие события внутри окна неизвестно'


def _windows(belts_, g, th=None, **kw):
    """Два окна: первое в горизонте наблюдения GOES, второе — далеко за ним.

    Это ровно тот случай, из-за которого сервис отказывал всегда: второе окно начинается через
    8 часов, и никакое наблюдение «сейчас» его не покроет.
    """
    th = th or Thresholds()
    tr = traj(24 * 60, lambda i: i < 60)
    w = [Window(T0, 360), Window(T0 + timedelta(minutes=480), 360)]
    A = [assess_window(x, tr, belts_, g, kp_sample(3.0, age_min=30), [], th, T0, mmod_hits=1e-6, **kw)
         for x in w]
    return A, th


# ------------------------------------------------------------------ три случая полного отказа
def test_otkaz_1_nablyudeniya_net_vovse(belts):
    """Случай 1: источник исключён или не ответил, кеша нет — данных нет вовсе."""
    A, th = _windows(belts, None)
    r = recommend(A, th)
    assert r.verdict == 'insufficient'
    assert any('GOES' in m for m in r.missing), r.missing
    assert proton_channel_state(None, T0, th)[0] == 'no_data'


def test_otkaz_2_nablyudenie_ustarelo_prichina_nazvana_chislom(belts):
    """Случай 2: наблюдение есть, но старше предела. Причина называется ЧИСЛОМ, а не словом."""
    th = Thresholds()
    stale = goes(0.2, age_min=int(th.goes_max_age_min) + 45)      # 105 мин при пределе 60
    A, th = _windows(belts, stale, th=th)
    r = recommend(A, th)
    assert r.verdict == 'insufficient'
    assert proton_channel_state(stale, T0, th)[0] == 'stale'
    причина = [m for m in r.missing if 'устарело' in m]
    assert причина, r.missing
    assert 'давность 105 мин при пределе 60 мин' in причина[0], причина
    # и это именно отсутствие покрытия, а не частичное
    assert all(m.coverage == Coverage.NONE for a in A for m in a.mechanisms if m.mechanism_id == 'spaceweather')


def test_otkaz_3_obyazatelnaya_liniya_pusta_po_drugim_kanalam(belts):
    """Случай 3: наблюдение GOES свежее и фоновое, но пуста другая обязательная линия."""
    th = Thresholds()
    tr = traj(24 * 60, lambda i: i < 60)
    A = [assess_window(Window(T0, 360), tr, belts, goes(0.2), kp_sample(3.0, age_min=30), [], th, T0,
                       mmod_hits=None),                       # линия метеороидов не подключена
         assess_window(Window(T0 + timedelta(minutes=480), 360), tr, belts, goes(0.2),
                       kp_sample(3.0, age_min=30), [], th, T0, mmod_hits=None)]
    r = recommend(A, th)
    assert r.verdict == 'insufficient'
    assert any('метеороид' in m for m in r.missing), r.missing
    # общий канал назван даже в отказе: про протонные события мы всё равно ничего не знаем
    assert PHRASE_NO_FORECAST in r.scope_ru


# ------------------------------------------------------------------ фон: вердикт выносится
def test_fon_daet_verdikt_i_obyavlennuyu_oblast(belts):
    """Главное следствие решения: при фоновом наблюдении сервис перестаёт молчать."""
    A, th = _windows(belts, goes(0.2))
    r = recommend(A, th)
    assert r.verdict != 'insufficient', r.rule_applied
    for phrase in (PHRASE_NO_FORECAST, PHRASE_UNKNOWN_IN_WINDOW):
        assert phrase in r.scope_ru, (phrase, r.scope_ru)
        assert phrase in r.scope_detail_ru
    assert 'фон по наблюдению' in r.scope_ru and 'ниже S1' in r.scope_ru
    facts = dict(r.scope_facts)
    assert 'общие каналы, окна не различающие' in facts
    # канал объявлен общим у КАЖДОГО окна: он одинаков для них по построению
    assert all(m.declared_common_ru for a in A for m in a.mechanisms if m.mechanism_id == 'spaceweather')


def test_fon_ne_delaet_pokrytie_polnym(belts):
    """Объявление канала общим не означает, что мы знаем обстановку в окне: покрытие остаётся
    частичным, а не становится полным. Иначе «объявленная область» превратилась бы в обещание."""
    A, _ = _windows(belts, goes(0.2))
    m = A[1].mechanisms[0]
    assert m.coverage == Coverage.PARTIAL
    assert any('не покрывает окно' in n for n in m.coverage_notes)
    assert not m.blocking_notes or all('GOES' not in n for n in m.blocking_notes)


def test_uroven_vyshe_fona_eto_uslovie_vsem_oknam_a_ne_otkaz(belts):
    """Отдельный случай: обстановка уже нештатная. Молчать нельзя, рекомендовать нельзя."""
    A, th = _windows(belts, goes(20.0))              # S1: выше фона
    r = recommend(A, th)
    assert r.verdict == 'all_need_check', r.rule_applied
    for a in A:                                       # условие стоит у КАЖДОГО окна, а не у первого
        conds = [c for m in a.mechanisms for c in m.conditions if c.kind == 'GOES']
        assert conds, a.window.start_utc
    assert 'ВЫШЕ фона' in r.scope_ru
    assert proton_channel_state(goes(20.0), T0, th)[0] == 'above_background'


def test_granica_fona_eto_S1_a_ne_otdelnyy_porog(belts):
    """Фон — это «ниже S1 (10 pfu)» по шкале NOAA, а не заведённый по случаю новый порог."""
    th = Thresholds()
    assert S1_PFU == 10.0 == th.goes_p10_warning_pfu
    assert proton_channel_state(goes(S1_PFU - 0.001), T0, th)[0] == 'background'
    assert proton_channel_state(goes(S1_PFU), T0, th)[0] == 'above_background'


# ------------------------------------------------------------------ общий канал и порядок окон
def test_obshchiy_kanal_ne_menyaet_poryadok_okon(belts):
    """Канал одинаков для всех окон по построению, значит различить их не может. Подмена уровня
    фона в пределах фона не имеет права поменять ни вердикт, ни выбранное окно, ни сравнение."""
    base = recommend(*_windows(belts, goes(0.2))[::-1][::-1])
    A1, th1 = _windows(belts, goes(0.2))
    r1 = recommend(A1, th1)
    for level in (0.01, 1.0, 5.0, 9.99):
        A2, th2 = _windows(belts, goes(level))
        r2 = recommend(A2, th2)
        assert r2.verdict == r1.verdict, level
        assert (r2.preferred and r2.preferred.start_utc) == (r1.preferred and r1.preferred.start_utc), level
        assert r2.per_mechanism_comparison == r1.per_mechanism_comparison, level
    assert base.verdict == r1.verdict


def test_znacheniya_okon_ot_urovnya_fona_ne_zavisyat(belts):
    """И сами величины окон тоже: уровень протонного фона в них не входит вовсе."""
    from vkd.windows.compare import _sw_vals
    A1, _ = _windows(belts, goes(0.2))
    A2, _ = _windows(belts, goes(9.0))
    assert [_sw_vals(a) for a in A1] == [_sw_vals(a) for a in A2]


# ------------------------------------------------------------------ суточная вероятность NOAA
def _daily(value, published=None, hours=24):
    """Ячейка суточной вероятности NOAA — та же форма, что даёт адаптер прогнозов."""
    return EnvironmentSample(T0 - timedelta(hours=2), 's1_prob_daily', value, '%', 'noaa_swpc_3day_forecast',
                             Kind.EXTERNAL_FORECAST, published or (T0 - timedelta(hours=2)),
                             T0 - timedelta(hours=2), T0 + timedelta(hours=hours), T0, 'model', 'rec#noaa')


def test_bez_vypuska_NOAA_pravilo_vsyo_ravno_srabatyvaet(belts):
    """САМОЕ ВАЖНОЕ в этом правиле. На живом экране 19.09 выпуска с суточной вероятностью не было
    вовсе. Если сделать её обязательным условием, правило не сработает никогда, и весь круг
    окажется впустую. Поэтому отсутствие выпуска только называется словами."""
    A, th = _windows(belts, goes(0.2), forecasts=())
    r = recommend(A, th)
    assert r.verdict != 'insufficient'
    assert PHRASE_NO_FORECAST in r.scope_ru
    assert 'выпуска NOAA с суточной вероятностью протонного события на это окно нет' in r.scope_ru
    assert 'на вывод это не влияет' in r.scope_ru


def test_sutochnaya_veroyatnost_pechataetsya_kak_est_i_ne_pereschityvaetsya(belts):
    """Значение дописывается как есть, с прямым указанием, что оно СУТОЧНОЕ. Пересчёта в
    вероятность за окно нет ни линейного, ни любого другого — решение команды, записанное
    в восьми документах."""
    A, th = _windows(belts, goes(0.2), forecasts=(_daily(1.0),))
    r = recommend(A, th)
    assert r.verdict != 'insufficient'
    assert 'ЗА СУТКИ' in r.scope_ru, r.scope_ru
    assert 'в вероятность за окно она не пересчитывается' in r.scope_ru
    # число напечатано тем же, каким пришло: 1 % суток не превращается в 0,25 % за шестичасовое окно
    assert '1 %' in r.scope_ru
    assert '0,25' not in r.scope_ru and '0.25' not in r.scope_ru


def test_sutochnaya_veroyatnost_ne_menyaet_verdikt(belts):
    """Наличие или отсутствие выпуска не меняет ни вердикт, ни выбранное окно."""
    a = recommend(*_windows(belts, goes(0.2), forecasts=()))
    b = recommend(*_windows(belts, goes(0.2), forecasts=(_daily(45.0),)))
    assert a.verdict == b.verdict
    assert (a.preferred and a.preferred.start_utc) == (b.preferred and b.preferred.start_utc)


pytest_plugins = ('tests.test_compare',)
