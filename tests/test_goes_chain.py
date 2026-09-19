# -*- coding: utf-8 -*-
"""Цепочка GOES «срок действия данных → покрытая часть окна → допустимый вывод» (разбор Codex п. 7)
и её сцепка с экраном.

Почему эти проверки существуют. Слой сравнения печатает долю покрытия ПРОЗОЙ, а экран
(`app.ui.obs_share_pct`) вынимает её из этой прозы регулярным выражением и по нулевой доле ставит
прочерк вместо числа. Связь двух модулей держится на одном обороте речи. Переписав фразу при
работе над п. 7, я эту связь порвал: доля переставала читаться, и прошлое измерение снова
печаталось как величина будущего окна — ровно то, что третий круг уже исправлял. Поймал случайный
тест отчёта. Здесь связь проверяется прямо, на выходе самого `assess_window`, а не через отчёт.
"""
from datetime import timedelta

import pytest

from app.ui import obs_share_pct
from tests.test_compare import T0, belts, goes, traj          # noqa: F401  (belts — фикстура)
from vkd.types import Window
from vkd.windows.compare import Thresholds, assess_window


def _goes_factor(win, tr, belts_, th):
    a = assess_window(win, tr, belts_, goes(0.2), None, [], th, T0, mmod_hits=1e-6)
    return next(x for x in a.mechanisms[0].factors if x.name.startswith('поток протонов GOES'))


def test_dolya_okna_chitaetsya_ekranom_iz_teksta_sloya(belts):
    """Оборот, по которому экран узнаёт долю, обязан присутствовать при ЛЮБОЙ доле."""
    tr = traj(30 * 60, lambda i: i < 60)
    th = Thresholds()
    for hours, expected in ((20, 0), (0, None)):
        f = _goes_factor(Window(T0 + timedelta(hours=hours), 360), tr, belts, th)
        share = obs_share_pct(f)
        assert share is not None, f.limits_note        # экран не смог прочитать долю из текста слоя
        if expected is not None:
            assert share == expected, f.limits_note


def test_tri_zvena_cepochki_nazvany_i_neset_minuty(belts):
    """Срок действия, покрытая часть окна В МИНУТАХ и допустимый вывод — три звена подряд."""
    tr = traj(30 * 60, lambda i: i < 60)
    th = Thresholds()
    f = _goes_factor(Window(T0 + timedelta(hours=20), 360), tr, belts, th)
    note = f.limits_note
    assert 'срок действия данных' in note, note
    assert 'горизонт наблюдения' in note and '% окна' in note, note
    assert 'мин из 360' in note, note                  # доля окна названа и минутами, а не только в процентах
    assert 'допустимый вывод' in note, note
    assert note.index('срок действия данных') < note.index('горизонт наблюдения') < note.index('допустимый вывод')


def test_nabludenie_ne_rasprostranyaetsya_na_nepokrytuyu_chast(belts):
    """Наблюдение сейчас ≠ прогноз на всё окно: последнее звено говорит это словами, а не умолчанием."""
    tr = traj(30 * 60, lambda i: i < 60)
    note = _goes_factor(Window(T0 + timedelta(hours=20), 360), tr, belts, Thresholds()).limits_note
    assert 'не распространяется' in note, note
    assert 'прогноза потока протонов' in note and 'нет' in note, note
    assert 'суточная вероятность' in note and 'не пересчитывается' in note, note
    assert 'безопасн' not in note.lower()


def test_polnoe_pokrytie_daet_drugoy_dopustimy_vyvod(belts):
    """При покрытии на всё окно третье звено другое — «известен на всё окно», без запрета."""
    tr = traj(30 * 60, lambda i: i < 60)
    th = Thresholds(goes_max_age_min=10 * 60)
    note = _goes_factor(Window(T0, 360), tr, belts, th).limits_note
    assert 'покрывает 100 % окна' in note, note
    assert 'на всё окно' in note and 'не распространяется' not in note, note
