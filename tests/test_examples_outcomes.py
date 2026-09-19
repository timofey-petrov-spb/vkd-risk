# -*- coding: utf-8 -*-
"""Какие исходы правила рекомендации показаны на НАСТОЯЩИХ данных (критерий О3, разбор Codex п. 1).

До 19.09 все девять примеров давали `insufficient`: отказ был корректен, но сравнение окон — то,
ради чего сервис существует, — на реальных данных не показывалось ни разу. Здесь зафиксировано,
какие исходы показаны сейчас и какой не показан, чтобы «показали все исходы» нельзя было
утверждать на словах.

Про `trade_off` — честно и с числом. Перебраны все даты архива 01.05–30.06.2024 с шагом 6 ч в
обоих исторических режимах: 496 расчётов, из них preferred 351, equivalent 80, all_need_check 50,
insufficient 7, ошибок границы архива 8. `trade_off` не встретился НИ РАЗУ. Причина не случайная,
и её видно в самом правиле:
  * между механизмами компромисс требует различия линии метеороидов больше порога различимости
    (5 % — настройка команды). На орбите МКС при равной длительности окон различие порядка
    0,01 %, то есть на два-три порядка ниже порога: механизм по построению не спорит с
    космопогодой (`MMOD_ROLE_RU` в `vkd/windows/compare.py`);
  * внутри космопогоды компромисс требует, чтобы минуты в аномалии и флюенс указывали на разные
    окна вне допуска. Обе величины считаются по одной и той же трассе через одну и ту же
    аномалию и почти всегда меняются согласованно.
Подгонять дату или опускать порог ради появления шестого исхода нельзя: это ровно то ослабление
проверок, которое запрещено. Исход остаётся реализованным и проверенным на синтетике
(`tests/test_compare.py`), но на реальных данных периода он не встречается — так и записано.
"""
import glob
import io
import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, 'examples')

# Измеренный перебор 19.09: 496 расчётов архива 01.05–30.06.2024, шаг 6 ч, оба исторических режима.
SCAN_TOTAL = 496
SCAN_COUNTS = {'preferred': 351, 'equivalent': 80, 'all_need_check': 50, 'insufficient': 7,
               'trade_off': 0, 'вне архива (отказ запроса)': 8}


def verdicts():
    out = {}
    for f in sorted(glob.glob(os.path.join(EXAMPLES, '*.json'))):
        d = json.load(io.open(f, encoding='utf-8'))
        rec = d.get('recommendation')
        if rec:
            out[os.path.splitext(os.path.basename(f))[0]] = rec['verdict']
    return out


def test_primery_pokazyvayut_ne_tolko_otkaz():
    """Главное, чего требовал Codex по п. 1: сравнение окон работает на настоящих данных."""
    v = verdicts()
    assert v, 'примеров нет — сначала python scripts/make_examples.py'
    assert set(v.values()) >= {'preferred', 'equivalent', 'all_need_check', 'insufficient'}, v
    assert sum(1 for x in v.values() if x == 'insufficient') < len(v), v


def test_trade_off_ne_vstretilsya_i_eto_zapisano():
    """Шестой исход на реальных данных периода не встречается. Не подгоняем — объявляем."""
    assert 'trade_off' not in set(verdicts().values())
    assert SCAN_COUNTS['trade_off'] == 0
    assert sum(SCAN_COUNTS.values()) == SCAN_TOTAL


def test_u_kazhdogo_primera_est_obyavlennaya_oblast_vyvoda():
    """Вердикт без своей области — утверждение шире, чем посчитано. В снимке её нельзя потерять."""
    for f in sorted(glob.glob(os.path.join(EXAMPLES, '*.json'))):
        d = json.load(io.open(f, encoding='utf-8'))
        rec = d.get('recommendation')
        if not rec:
            continue
        name = os.path.basename(f)
        assert rec.get('scope'), name
        assert rec.get('scope_detail'), name
        assert rec.get('scope_facts'), name
        if rec['verdict'] == 'insufficient':
            assert 'отказ от вывода' in rec['scope'], (name, rec['scope'])
        else:
            assert 'не заключение о полном риске ВКД' in rec['scope'], (name, rec['scope'])


def test_otkaz_ne_ssylaetsya_na_chastichno_pokrytye_kanaly():
    """Ни одна строка отказа не утверждает отсутствия там, где рядом напечатана доля покрытия."""
    for f in sorted(glob.glob(os.path.join(EXAMPLES, '*.json'))):
        d = json.load(io.open(f, encoding='utf-8'))
        rec = d.get('recommendation') or {}
        if rec.get('verdict') != 'insufficient':
            continue
        for m in rec['missing']:
            assert 'сезонный вклад' not in m, (os.path.basename(f), m)
            # «не покрывает окно» — это и есть отсутствие; ищем только НЕНУЛЕВУЮ долю рядом с отказом
            share = re.search(r'(?<!не )покрывает (\d+(?:[.,]\d+)?) %', m)
            assert share is None or float(share.group(1).replace(',', '.')) == 0, (os.path.basename(f), m)


def test_propuski_modeli_v_snimke_dany_v_minutah():
    """Пункт 3 Codex доходит до выгрузки, а не остаётся в коде: минуты и причина у каждого окна."""
    checked = 0
    for f in sorted(glob.glob(os.path.join(EXAMPLES, '*.json'))):
        d = json.load(io.open(f, encoding='utf-8'))
        for w in d.get('windows') or []:
            for m in w['mechanisms']:
                if not m['mandatory']:
                    continue
                assert 'coverage_gaps' in m, os.path.basename(f)
                for g in m['coverage_gaps']:
                    assert g['minutes'] > 0 and g['reason'], (os.path.basename(f), g)
                    checked += 1
    assert checked, 'ни одного пропуска в примерах — проверять нечего'
