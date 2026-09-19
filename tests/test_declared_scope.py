# -*- coding: utf-8 -*-
"""Объявленная область вывода (разбор Codex п. 1, решение владельца 19.09).

Codex предложил два выхода: либо показать реальный случай с достаточным охватом, либо чётко
ограничить результат. Владелец взял второй. Сервис сравнивает окна и называет предпочтительное
при ЧАСТИЧНОМ покрытии обязательной линии, но рядом печатает область: по каким факторам сделан
вывод, на какой доле окна, какие пропуски и чего вывод НЕ означает. Полный отказ остаётся только
там, где покрытие отсутствует вовсе.

Здесь проверяется, что это не смягчение проверок:
  * ни одно число, порог или модель не зависят от вердикта;
  * отказ по-прежнему наступает при ОТСУТСТВИИ покрытия;
  * область вывода собрана из тех же чисел, что стоят у величин, а не написана словами;
  * ни одна строка отказа не утверждает отсутствия там, где рядом напечатана доля покрытия.
"""
from dataclasses import replace
from datetime import timedelta

from tests.test_compare import T0, belts, goes, kp_sample, traj, two_windows  # noqa: F401
from vkd.types import Coverage, Window
from vkd.windows.compare import SCOPE_NOT_RU, Thresholds, assess_window, recommend


def partial_windows(belts_):
    """Два окна, у которых обязательные линии покрыты ЧАСТИЧНО, но ни одна не пуста.

    Оба окна лежат внутри горизонта наблюдения GOES (срок действия поднят настройкой, а не
    отменён), Kp свежий, трасса полная. Частичным остаётся то, что частично и есть на самом
    деле: модель ОСТ покрывает не всё окно, а сезонный вклад метеороидов не рассчитан.
    """
    th = Thresholds(goes_max_age_min=20 * 60)
    tr = traj(24 * 60, lambda i: i < 60)
    w = [Window(T0, 360), Window(T0 + timedelta(minutes=480), 360)]
    A = [assess_window(x, tr, belts_, goes(0.2), kp_sample(3.0, age_min=30), [], th, T0, mmod_hits=1e-6)
         for x in w]
    return A, th


def test_chastichnoe_pokrytie_daet_verdikt_s_obyavlennoy_oblastyu(belts):
    """Главное следствие решения: частичное покрытие больше не отменяет сравнение."""
    A, th = partial_windows(belts)
    assert any(m.coverage == Coverage.PARTIAL for a in A for m in a.mechanisms if m.mandatory), \
        'в этом наборе нет частичного покрытия — проверять нечего'
    r = recommend(A, th)
    assert r.verdict != 'insufficient', r.rule_applied
    assert r.scope_ru and SCOPE_NOT_RU in r.scope_ru, r.scope_ru
    assert 'при покрытии модели' in r.scope_ru, r.scope_ru


def test_otsutstvie_pokrytiya_po_prezhnemu_daet_otkaz(belts):
    """Ослабления нет: пустой обязательный канал — по-прежнему отказ, с названной причиной."""
    A, th = two_windows(belts, None)                 # GOES отключён и кеша нет
    r = recommend(A, th)
    assert r.verdict == 'insufficient'
    assert any('GOES' in m for m in r.missing), r.missing
    assert 'отказ от вывода, а не оценка риска' in r.scope_ru, r.scope_ru


def test_otkaz_ne_ssylaetsya_na_kanaly_s_chastichnym_pokrytiem(belts):
    """Находка при слиянии: при одном пустом канале в «не покрыто совсем» уезжали ВСЕ заметки
    механизма, включая «модель ОСТ покрывает 83,3 % времени окна». Вердикт стоял рядом с числом,
    которое его опровергает. Теперь причина отказа — только каналы без данных."""
    A, th = two_windows(belts, None)
    r = recommend(A, th)
    assert r.verdict == 'insufficient'
    for m in r.missing:
        assert 'покрывает' not in m or 'покрывает 0' in m, m
        assert 'сезонный вклад' not in m, m


def test_oblast_vyvoda_schitaetsya_po_tem_zhe_chislam_chto_i_velichiny(belts):
    """Доля в области вывода — то же `coverage_fraction`, что у механизма, а не отдельный текст."""
    A, th = partial_windows(belts)
    r = recommend(A, th)
    fracs = [m.coverage_fraction for a in A for m in a.mechanisms
             if m.mechanism_id == 'spaceweather' and m.coverage_fraction is not None]
    assert fracs
    lo, hi = round(100 * min(fracs)), round(100 * max(fracs))
    assert ('%d' % lo) in r.scope_ru or ('%d' % hi) in r.scope_ru, (lo, hi, r.scope_ru)


def test_korotkaya_oblast_est_nachalo_podrobnoy(belts):
    """Две формы одной области, а не два разных утверждения."""
    A, th = partial_windows(belts)
    r = recommend(A, th)
    head = r.scope_ru.split(';')[0]
    assert r.scope_detail_ru.startswith(head), (head, r.scope_detail_ru)
    assert SCOPE_NOT_RU in r.scope_detail_ru


def test_oblast_nazyvaet_propuski_v_minutah_i_prichiny(belts):
    """Пункт 3 Codex в самой области: не доля точек, а минуты и причина."""
    A, th = partial_windows(belts)
    r = recommend(A, th)
    facts = dict(r.scope_facts)
    assert 'пропуски модели' in facts, facts
    assert 'мин — ' in facts['пропуски модели'], facts['пропуски модели']
    assert 'вывод не означает' in facts and 'разгерметизации' in facts['вывод не означает']


def test_oblast_ne_obeshchaet_bezopasnosti(belts):
    """CONTRACT §9: слова «безопасно» нет ни в одной форме области."""
    A, th = partial_windows(belts)
    r = recommend(A, th)
    for text in (r.scope_ru, r.scope_detail_ru) + tuple(v for _, v in r.scope_facts):
        assert 'безопас' not in text.lower(), text


def test_verdikt_ne_menyaet_ni_odnogo_chisla(belts):
    """Смена правила вердикта не трогает величины: сравнение по механизму одно и то же
    при любом исходе. Проверяется прямо — теми же окнами с полным и с частичным покрытием."""
    A, th = partial_windows(belts)
    full = [replace(a, mechanisms=tuple(replace(m, coverage=Coverage.FULL, coverage_notes=(),
                                                blocking_notes=()) if m.mandatory else m
                                        for m in a.mechanisms)) for a in A]
    r_partial, r_full = recommend(A, th), recommend(full, th)
    assert r_partial.per_mechanism_comparison == r_full.per_mechanism_comparison
    assert r_partial.verdict == r_full.verdict
    # Доля окна в области вывода — свойство расчёта, а не вердикта: она одна и та же, как бы
    # ни было объявлено покрытие механизма. Иначе «объявленная область» подстраивалась бы под
    # желаемый вывод, а это и было бы ослаблением проверок.
    assert r_partial.scope_ru == r_full.scope_ru, (r_partial.scope_ru, r_full.scope_ru)
    # И пропуски тоже: они посчитаны по трассе и таблице, а не выведены из объявленного
    # покрытия. Объявить линию полной — не значит, что минуты без модели куда-то делись.
    assert dict(r_partial.scope_facts).get('пропуски модели')
    assert r_partial.scope_facts == r_full.scope_facts


pytest_plugins = ('tests.test_compare',)
