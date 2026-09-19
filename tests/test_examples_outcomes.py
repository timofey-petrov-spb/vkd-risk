# -*- coding: utf-8 -*-
"""Исходы правила на сохранённых данных (О3).

Счёт SCAN_COUNTS ниже — исторический прогон 496 запросов ДО сезонной модели.
Он не описывает новый алгоритм 0.10.1. После физического аудита неопределённость
фона и нормировки снимает прежнее предпочтение на 20.05.2024: настоящий trade_off
теперь обязан сохраняться. Параметры не подгонялись для появления этого исхода.
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
    """Главное, чего требовал Codex по п. 1: сравнение окон работает на настоящих данных.

    ИЗМЕНЕНО в круге 11 (R14). Прежде здесь требовался и исход `insufficient`: его давали три
    примера текущего режима — и давали его ВСЕГДА, при любых данных, потому что окно, начинающееся
    позже чем через час после последнего измерения GOES, по построению не попадало в горизонт
    наблюдения. Ровно этот отказ владелец увидел 19.09 первым экраном развёрнутого сервиса.
    После объявления канала протонных событий общим такого отказа на сохранённых примерах больше
    нет — и это цель круга, а не потеря проверки.

    ИЗМЕНЕНО ещё раз при слиянии круга 11. Прежняя редакция этой проверки требовала, чтобы отказа
    на примерах НЕ БЫЛО вовсе, — так было записано положение дел на момент сдачи движка: пример с
    исключённым источником тогда собрать было нечем (`scripts/make_examples.py` не прокидывал
    `disabled` в `run`). Теперь прокидывает, и пример `refusal_goes_off` собран. Проверка не
    ослаблена, а усилена: требуется и сам отказ, и то, что он ровно один и ровно там, где источник
    исключён пользователем. Структурный отказ, который правило R14 убрало, такой проверкой по-прежнему
    ловится — он появился бы на примерах, где никто ничего не исключал.

    Отказ проверяется ещё и там, где он настоящий: `tests/test_common_channel.py` держит все три
    случая полного отказа (наблюдения нет вовсе, наблюдение устарело сверх предела, пуста другая
    обязательная линия), `tests/test_compare.py` — исключённый пользователем источник.
    """
    v = verdicts()
    assert v, 'примеров нет — сначала python scripts/make_examples.py'
    assert set(v.values()) >= {'preferred', 'equivalent', 'all_need_check', 'insufficient'}, v
    refused = {n for n, x in v.items() if x == 'insufficient'}
    assert refused == {'refusal_goes_off'}, (
        'отказ стоит не только там, где пользователь исключил источник — проверьте, не вернулось ли '
        'структурное обнуление обязательной линии пустым каналом: %s' % v)
    off = json.load(io.open(os.path.join(EXAMPLES, 'refusal_goes_off.json'), encoding='utf-8'))
    assert (off['request']['disabled'] or {}).get('goes') == 'off', off['request']['disabled']


def test_trade_off_is_required_when_physical_hypotheses_change_comparison():
    """Изменено по физическому аудиту v2: старый запрет trade_off скрывал бы неопределённость."""
    from vkd.assess.seasonal import comparison_sensitivity
    s = json.load(io.open(os.path.join(EXAMPLES, 'gap_2024-05-20_12Z.json'), encoding='utf-8'))
    rows = [s['meteoroids'][t] for t in s['request']['windows']]
    assert not comparison_sensitivity(rows, s['request']['thresholds']['meteoroid_equal_pct'])['stable']
    assert s['recommendation']['verdict'] == 'trade_off'
    assert s['recommendation']['preferred'] is None
    # Keep the earlier study explicitly separate; do not pretend it was rerun.
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
