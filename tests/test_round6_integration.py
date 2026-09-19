# -*- coding: utf-8 -*-
"""Шестой круг, слияние: три находки области «экран», лежавшие в `app/main.py`.

Область «экран» нашла их дампами по брифу §9, но править `app/main.py` ей нельзя было;
закрыты интегратором на коммите слияния. Проверяется то, что видно на экране:

  * п. 4 — дописка о формуле в пункте 5 карточки объяснения не приклеивается к тексту
    правила без знака препинания;
  * п. 5 — колонка «по» таблицы «Проверка после отсечки» не печатает конец интервала
    временем без даты, когда интервал переходит через полночь (иначе конец читается
    как утро того же дня, то есть РАНЬШЕ начала);
  * п. 6 — ячейка «События на горизонте» приборной полосы несёт единицу и происхождение,
    как все соседние ячейки.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

import pytest
from streamlit.testing.v1 import AppTest

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app', 'main.py')
TIMEOUT = 300


def _texts(at: AppTest) -> str:
    parts = [e.value for e in at.markdown] + [e.value for e in at.caption] \
        + [e.value for e in at.warning] + [e.value for e in at.error] + [e.value for e in at.info]
    return '\n'.join(str(p) for p in parts)


@pytest.fixture(scope='module')
def app_now() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    assert not at.exception
    return at


@pytest.fixture(scope='module')
def app_strict() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.button(key='preset_gannon').click().run()
    assert not at.exception
    return at


def test_formula_reference_is_a_separate_sentence(app_now: AppTest) -> None:
    """П. 4: перед «Формальная запись — вкладка «Методика»» всегда стоит конец предложения."""
    # разметку убираем: в блоке вердикта дописка стоит отдельной ячейкой <div class="vm">,
    # и её открывающий тег сам по себе не является знаком препинания
    body = re.sub(r'<[^>]+>', ' ', _texts(app_now))
    hits = list(re.finditer(r'(.{1,80}?)\s*Формальная запись — вкладка', body))
    assert hits, 'карточек объяснения со ссылкой на формулу на экране нет — проверять нечего'
    склеенные = [h.group(1) for h in hits
                 if not h.group(1).rstrip().endswith(('.', '!', '?', ':', ';'))]
    assert not склеенные, 'дописка приклеена к правилу без знака препинания: %r' % склеенные[:3]


def test_verification_table_end_of_interval_is_not_earlier_than_start(app_strict: AppTest) -> None:
    """П. 5: в таблице «Проверка после отсечки» конец интервала не выглядит раньше начала."""
    таблицы = [df.value for df in app_strict.dataframe]
    kp = [t for t in таблицы if 'интервал с' in list(t.columns) and 'Kp' in list(t.columns)]
    if not kp:
        pytest.skip('таблицы наблюдений Kp после отсечки в этом прогоне нет')
    for t in kp:
        for нач, кон in zip(t['интервал с'], t['по']):
            if кон == '—':
                continue
            дата_нач = str(нач).split()[0]
            если_с_датой = len(str(кон).split()) == 2
            # либо у конца своя дата, либо конец того же дня и позже начала по времени
            if не_тот_же_день(нач, кон, если_с_датой):
                assert если_с_датой, 'конец интервала «%s» напечатан без даты при начале «%s»' % (кон, нач)
            else:
                assert str(кон) > str(нач).split()[1], (
                    'конец «%s» не позже начала «%s» в пределах суток %s' % (кон, нач, дата_нач))


def не_тот_же_день(нач: str, кон: str, кон_с_датой: bool) -> bool:
    """Начало и конец приходятся на разные сутки: у конца стоит своя дата и она другая."""
    if not кон_с_датой:
        return False
    return str(кон).split()[0] != str(нач).split()[0]


def test_events_on_horizon_cell_has_unit_and_origin(app_now: AppTest) -> None:
    """П. 6: число в ячейке «События на горизонте» стоит с единицей, подпись называет происхождение."""
    body = _texts(app_now)
    m = re.search(r'События на горизонте</div><div class="cv">([^<]*)</div><div class="cs">([^<]*)</div>', body)
    assert m, 'ячейки «События на горизонте» на экране нет'
    значение, подпись = m.group(1), m.group(2)
    assert re.fullmatch(r'[\d ]+ (запись|записи|записей)', значение), \
        'число без единицы: %r' % значение
    assert 'наш подсчёт' in подпись, 'происхождение не названо: %r' % подпись
