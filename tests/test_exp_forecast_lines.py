# -*- coding: utf-8 -*-
"""Эксперимент Т5 (experiments/forecast_lines.py): каждая линия считается против своей величины
(бурю и протонное событие не складывать), пороги — из настроек приложения (Т7), поставщик —
тот же vkd.history (A2), что у приложения; линии enlil больше нет (R10)."""
import io
import json
import os

import experiments.forecast_lines as FL
from vkd.windows.compare import Thresholds

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _row(lines, storm, sep, noaa=True, goes=True):
    return {'lines': lines, 'truth_storm': storm, 'truth_sep': sep,
            'noaa_available': noaa, 'goes_truth_available': goes}


def test_lines_scored_against_own_truth():
    L = {'baseline': False, 'notif_gst': False, 'cme_arrival': False, 'noaa': False,
         'system_storm': False, 'notif_sep': True, 'system_sep': True}
    rows = [_row(L, storm=False, sep=True)]          # только протонное событие: по буре — верный отказ, по SEP — попадание
    assert FL.score(rows, 'cme_arrival') == (0, 0, 0, 1)
    assert FL.score(rows, 'system_storm') == (0, 0, 0, 1)
    assert FL.score(rows, 'notif_sep') == (1, 0, 0, 0)
    L2 = {**L, 'cme_arrival': True, 'system_storm': True, 'notif_sep': False, 'system_sep': False}
    rows = [_row(L2, storm=True, sep=False)]         # только буря: cme_arrival — попадание, SEP-линия — верный отказ
    assert FL.score(rows, 'cme_arrival') == (1, 0, 0, 0) and FL.score(rows, 'notif_sep') == (0, 0, 0, 1)
    rows = [_row(L2, storm=False, sep=True, noaa=False)]   # SEP не оправдывает условие по буре; noaa без выпуска не считается
    assert FL.score(rows, 'cme_arrival') == (0, 0, 1, 0) and FL.score(rows, 'noaa') == (0, 0, 0, 0)
    # окно без наблюдений GOES: факт по протонному событию неизвестен — окно не считается
    rows = [_row(L, storm=False, sep=False, goes=False)]
    assert FL.score(rows, 'notif_sep') == (0, 0, 0, 0)
    assert FL.score(rows, 'system_storm') == (0, 0, 0, 1)


def test_thresholds_come_from_settings():
    th = Thresholds.from_settings()
    assert FL.KP_STORM == th.kp_check and FL.PFU_SEP == th.goes_p10_warning_pfu
    assert FL.CONFIGURED_BOUND == th.cme_kp_bound and set(FL.BOUNDS) == {'max', 'min'}
    assert set(FL.TRUTH_OF) == set(FL.STORM_LINES) | set(FL.SEP_LINES)
    assert 'enlil' not in FL.TRUTH_OF and 'cme_arrival' in FL.STORM_LINES


def test_saved_experiment_matches_doc_conventions():
    p = os.path.join(ROOT, 'examples', 'experiments', 'forecast_lines.json')
    d = json.load(io.open(p, encoding='utf-8'))
    assert d['params']['kp_storm'] == FL.KP_STORM and d['params']['truth_of'] == FL.TRUTH_OF
    assert d['params']['cme_kp_range_bound'] == FL.CONFIGURED_BOUND
    assert d['provider'].startswith('vkd.history')
    assert set(d['scores']['event']) == set(FL.TRUTH_OF)
    assert set(d['sensitivity']) == {'max', 'min'}
    doc = io.open(os.path.join(ROOT, 'docs', 'EKSPERIMENTY_PROGNOZ.md'), encoding='utf-8').read()
    assert 'против своей величины' in doc and 'system_sep' in doc
    assert 'vkd.history' in doc
    # таблица в документе совпадает с сохранёнными числами
    h, m, f, n = d['scores']['event']['system_storm']
    assert '| system_storm | буря | %d | %d | %d | %d |' % (h, m, f, n) in doc
    h, m, f, n = d['scores']['control']['system_sep']
    assert '| system_sep | протонное событие | %d | %d | %d | %d |' % (h, m, f, n) in doc


def test_doc_declares_what_changed_since_the_previous_edition():
    """Показатели изменились — причина названа прямо, а не «пересчитано»."""
    doc = io.open(os.path.join(ROOT, 'docs', 'EKSPERIMENTY_PROGNOZ.md'), encoding='utf-8').read()
    assert '## Что изменилось по сравнению с прежней редакцией' in doc
    assert 'напрямую не сравнимы' in doc
    assert 'измеренный поток' in doc and 'NASA iSWA' in doc
    assert 'cme_arrival' in doc and 'граница' in doc
