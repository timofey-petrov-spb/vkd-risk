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
    """П. 4: в пункте 5 карточки объяснения перед «Формальная запись» стоит конец предложения.

    Проверяется именно пункт 5 карточки: это одна строка одного абзаца, и склейка там
    видна глазами. Блок вердикта сюда не входит намеренно — там ссылка на формулу стоит
    ОТДЕЛЬНОЙ ячейкой разметки (`<div class="vm">`) после списка, то есть отдельным блоком
    на экране, и знак препинания перед ней ничего не решает.
    """
    пункты = [str(e.value) for e in app_now.markdown
              if str(e.value).startswith('**5. Применённое правило или модель.**')]
    со_ссылкой = [p for p in пункты if 'Формальная запись — вкладка' in p]
    assert со_ссылкой, 'карточек объяснения со ссылкой на формулу на экране нет — проверять нечего'
    склеенные = [p for p in со_ссылкой
                 if not p.split('Формальная запись — вкладка')[0].rstrip().endswith(('.', '!', '?', ':', ';'))]
    assert not склеенные, 'дописка приклеена к правилу без знака препинания: %r' % [p[-90:] for p in склеенные[:3]]


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


@pytest.mark.parametrize('уровень', [False, True], ids=['оперативный', 'профессиональный'])
def test_no_nested_parentheses_on_screen(уровень: bool) -> None:
    """Скобка в скобке на экране — признак склеенного шаблона, а не оформления.

    Проверка поставлена после того, как пятый круг закрыл два последних таких места
    (правило карточки флюенса и заметка GOES): область «экран» просила её поставить
    именно после закрытия R5-8, иначе она была бы красной с рождения. Разметка и CSS
    из проверки убраны: `grid-template-columns: repeat(auto-fit, minmax(190px, 1fr))` —
    не текст экрана.
    """
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.button(key='preset_gannon').click().run()
    if уровень:
        at.sidebar.radio('level').set_value('Профессиональный').run()
    assert not at.exception, at.exception
    тело = re.sub(r'<style[^>]*>.*?</style>', ' ', _texts(at), flags=re.S)
    тело = re.sub(r'<[^>]+>', ' ', тело)
    вложенные = re.findall(r'\([^()]*\([^()]*\)[^()]*\)', тело)
    assert not вложенные, 'вложенные скобки на экране: %r' % вложенные[:3]


def test_events_on_horizon_cell_has_unit_and_origin(app_now: AppTest) -> None:
    """П. 6: число в ячейке «События на горизонте» стоит с единицей, подпись называет происхождение."""
    body = _texts(app_now)
    m = re.search(r'События на горизонте</div><div class="cv">([^<]*)</div><div class="cs">([^<]*)</div>', body)
    assert m, 'ячейки «События на горизонте» на экране нет'
    значение, подпись = m.group(1), m.group(2)
    assert re.fullmatch(r'[\d ]+ (запись|записи|записей)', значение), \
        'число без единицы: %r' % значение
    assert 'наш подсчёт' in подпись, 'происхождение не названо: %r' % подпись
