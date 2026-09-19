# -*- coding: utf-8 -*-
"""Эксперимент Т5 по ВЕРДИКТУ (experiments/verdicts.py).

Проверяется то, на чём стоят выводы отчёта: постановка запроса и пороги берутся из настроек
приложения (Т7), а не из кода эксперимента; правило простого подхода «последнее наблюдение»
работает как описано; пропуск считается по НАЗВАННОМУ окну, ложная тревога — по поставленному
условию при пустом факте; сохранённые расчёты совпадают с таблицами документа.

Полный перебор здесь не запускается (он идёт минуты) — считается логика показателей на
искусственных строках того же вида, что пишет sweep().
"""
import io
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

import experiments.verdicts as V
from vkd.config import section as cfg_section
from vkd.windows.compare import Thresholds

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UTC = timezone.utc
W1, W2 = '2024-05-10T14:00:00+00:00', '2024-05-10T18:00:00+00:00'


def _truth(storm1=False, proton1=False, storm2=False, proton2=False, goes=True):
    def t(s, p):
        return {'storm': s, 'proton': p, 'kp_truth_available': True, 'goes_truth_available': goes}
    return {W1: t(storm1, proton1), W2: t(storm2, proton2)}


def _row(*, verdict, named=(), conditions=None, truth=None, base=None):
    group = ('named' if verdict in V.NAMED_VERDICTS else
             'refused' if verdict in V.REFUSAL_VERDICTS else 'analyst')
    svc = {'verdict': verdict, 'group': group, 'named': list(named), 'rule': 'правило',
           'missing': [], 'conditions': conditions or {W1: [], W2: []}, 'windows': [W1, W2],
           'noaa_status': 'full', 'noaa_reason': None}
    return {'cutoff_utc': '2024-05-10T12:00:00+00:00', 'day': '2024-05-10', 'seconds': 1.0,
            'truth': truth or _truth(), 'service': svc,
            'baseline': base or {'verdict': 'preferred', 'group': 'named', 'named': [W1],
                                 'condition_windows': [], 'storm_condition': False,
                                 'proton_condition': False, 'last_kp': 2.0, 'last_goes_pfu': 0.1,
                                 'rule': 'ниже порогов'}}


# --------------------------------------------------------------- постановка берётся у приложения
def test_request_and_thresholds_come_from_application_settings():
    th, ui = Thresholds.from_settings(), cfg_section('ui')
    assert (V.KP_STORM, V.PFU_SEP) == (th.kp_check, th.goes_p10_warning_pfu)
    assert V.DUR_MIN == int(ui['duration_min']) and V.SEARCH_MIN == int(ui['search_min'])
    assert V.OFFSETS_MIN == [int(x) for x in ui['window_offsets_min']]
    # два окна-кандидата: правило равнозначности и счёт «названных окон» опираются на это
    assert len(V.OFFSETS_MIN) == 2


def test_all_five_verdicts_are_mapped_to_an_outcome():
    """Шестого исхода нет: каждый вердикт recommend() попадает ровно в одну группу."""
    from vkd.windows.compare import recommend                     # noqa: F401 — источник перечня
    all_v = set(V.NAMED_VERDICTS) | set(V.ANALYST_VERDICTS) | set(V.REFUSAL_VERDICTS)
    assert all_v == {'preferred', 'equivalent', 'all_need_check', 'trade_off', 'insufficient'}
    assert all_v == set(V.VERDICT_RU)
    assert not (set(V.NAMED_VERDICTS) & set(V.ANALYST_VERDICTS))


def test_step_is_never_coarser_than_twelve_hours():
    with pytest.raises(SystemExit):
        V.main(['--step-h', '24'])


# --------------------------------------------------------------- показатели по вердикту
def test_miss_counted_only_for_the_window_that_was_named():
    """Событие во ВТОРОМ окне не делает промахом названное ПЕРВОЕ: считается названное окно."""
    rows = [_row(verdict='preferred', named=[W1], truth=_truth(storm2=True))]
    assert V.verdict_scores(rows, 'service')['пропуски'] == 0
    rows = [_row(verdict='preferred', named=[W1], truth=_truth(storm1=True))]
    s = V.verdict_scores(rows, 'service')
    assert s['пропуски'] == 1 and s['пропуски_по_буре'] == 1 and s['пропуски_по_протонам'] == 0
    rows = [_row(verdict='preferred', named=[W1], truth=_truth(proton1=True))]
    s = V.verdict_scores(rows, 'service')
    assert s['пропуски'] == 1 and s['пропуски_по_протонам'] == 1


def test_equivalent_names_windows_and_is_scored_as_named():
    rows = [_row(verdict='equivalent', named=[W1, W2], truth=_truth(storm2=True))]
    s = V.verdict_scores(rows, 'service')
    assert s['named'] == 1 and s['пропуски'] == 1, 'равнозначные окна — тоже названные'


def test_analyst_and_refusal_never_produce_a_miss():
    for v in ('all_need_check', 'trade_off', 'insufficient'):
        rows = [_row(verdict=v, truth=_truth(storm1=True, storm2=True))]
        assert V.verdict_scores(rows, 'service')['пропуски'] == 0


def test_false_alarm_needs_a_condition_and_an_empty_fact():
    cond = {W1: [{'kind': 'GST', 'severity': 'limiting', 'text': 'буря'}], W2: []}
    rows = [_row(verdict='all_need_check', conditions=cond, truth=_truth())]
    assert V.verdict_scores(rows, 'service')['ложные_тревоги'] == 1
    # событие было хоть в одном окне отсечки — это не ложная тревога
    rows = [_row(verdict='all_need_check', conditions=cond, truth=_truth(storm2=True))]
    assert V.verdict_scores(rows, 'service')['ложные_тревоги'] == 0
    # условия не ставилось вовсе — тревоги нет
    rows = [_row(verdict='preferred', named=[W1], truth=_truth())]
    s = V.verdict_scores(rows, 'service')
    assert s['ложные_тревоги'] == 0 and s['условие_поставлено'] == 0


def test_storm_and_proton_are_not_added_together():
    """Условие по буре не оправдывается протонным событием и наоборот (договор)."""
    cond = {W1: [{'kind': 'GST', 'severity': 'limiting', 'text': 'буря'}], W2: []}
    rows = [_row(verdict='all_need_check', conditions=cond, truth=_truth(proton1=True))]
    t = V.window_error_table(rows, 'service')
    assert t['буря']['ложные_тревоги'] == 1 and t['буря']['попадания'] == 0
    assert t['протонное событие']['пропуски'] == 1


def test_window_without_goes_observations_is_not_counted_as_calm():
    cond = {W1: [], W2: []}
    rows = [_row(verdict='preferred', named=[W1], conditions=cond, truth=_truth(goes=False))]
    t = V.window_error_table(rows, 'service')
    assert t['протонное событие']['факт_неизвестен'] == 2
    assert sum(t['протонное событие'][k] for k in ('попадания', 'пропуски', 'ложные_тревоги', 'верные_отказы')) == 0
    assert t['буря']['верные_отказы'] == 2, 'ряд Kp есть — окно по буре считается'


def test_misses_are_listed_by_name():
    rows = [_row(verdict='preferred', named=[W1], truth=_truth(storm1=True))]
    m = V.misses(rows, 'service')
    assert len(m) == 1 and m[0]['window_start_utc'] == W1 and m[0]['storm'] is True


# --------------------------------------------------------------- простой подход
def _obs():
    base = datetime(2024, 5, 10, tzinfo=UTC)
    kp = [(base + timedelta(hours=3 * i), base + timedelta(hours=3 * i + 3), 2.0) for i in range(8)]
    goes = [(base + timedelta(minutes=5 * i), base + timedelta(minutes=5 * i + 5), 0.5) for i in range(200)]
    return kp, goes


def test_baseline_names_the_earliest_window_when_last_observation_is_calm():
    kp, goes = _obs()
    wins = [datetime(2024, 5, 10, 14, tzinfo=UTC), datetime(2024, 5, 10, 18, tzinfo=UTC)]
    d = V.baseline_decision(datetime(2024, 5, 10, 12, tzinfo=UTC), kp, goes,
                            [x[1] for x in kp], [x[1] for x in goes], wins)
    assert d['group'] == 'named' and d['named'] == [wins[0].isoformat()] and not d['condition_windows']


def test_baseline_flags_every_window_when_last_observation_is_above_threshold():
    kp, goes = _obs()
    kp[3] = (kp[3][0], kp[3][1], V.KP_STORM)                      # последний завершённый Kp на 12:00
    wins = [datetime(2024, 5, 10, 14, tzinfo=UTC), datetime(2024, 5, 10, 18, tzinfo=UTC)]
    d = V.baseline_decision(datetime(2024, 5, 10, 12, tzinfo=UTC), kp, goes,
                            [x[1] for x in kp], [x[1] for x in goes], wins)
    assert d['group'] == 'analyst' and d['storm_condition'] and len(d['condition_windows']) == 2
    kp[3] = (kp[3][0], kp[3][1], 2.0)
    goes[143] = (goes[143][0], goes[143][1], V.PFU_SEP)           # последняя ячейка до 12:00
    d = V.baseline_decision(datetime(2024, 5, 10, 12, tzinfo=UTC), kp, goes,
                            [x[1] for x in kp], [x[1] for x in goes], wins)
    assert d['group'] == 'analyst' and d['proton_condition'] and not d['storm_condition']


def test_baseline_refuses_when_there_is_nothing_observed_yet():
    wins = [datetime(2024, 5, 1, 2, tzinfo=UTC), datetime(2024, 5, 1, 6, tzinfo=UTC)]
    d = V.baseline_decision(datetime(2024, 5, 1, tzinfo=UTC), [], [], [], [], wins)
    assert d['group'] == 'refused' and d['verdict'] == 'insufficient'


def test_baseline_uses_only_observations_before_the_cutoff():
    """Простому подходу дан окончательный ряд, но НЕ будущее: наблюдение после отсечки не видно."""
    kp, goes = _obs()
    kp[6] = (kp[6][0], kp[6][1], 9.0)                             # 18:00—21:00, после отсечки 12:00
    wins = [datetime(2024, 5, 10, 14, tzinfo=UTC), datetime(2024, 5, 10, 18, tzinfo=UTC)]
    d = V.baseline_decision(datetime(2024, 5, 10, 12, tzinfo=UTC), kp, goes,
                            [x[1] for x in kp], [x[1] for x in goes], wins)
    assert d['group'] == 'named' and d['last_kp'] == 2.0


# --------------------------------------------------------------- контрольный период по правилу
def test_control_period_is_chosen_by_data_not_hardcoded():
    kp = [(datetime(2024, 5, 10, tzinfo=UTC), datetime(2024, 5, 11, tzinfo=UTC), V.KP_STORM)]
    goes = [(datetime(2024, 6, 20, tzinfo=UTC), datetime(2024, 6, 21, tzinfo=UTC), V.PFU_SEP)]
    a, b = V.control_period(kp, goes)
    assert a == '2024-05-11' and b == '2024-06-19', 'самый длинный спокойный отрезок между событиями'


# --------------------------------------------------------------- сохранённые расчёты и документ
@pytest.mark.skipif(not os.path.isfile(os.path.join(ROOT, 'examples', 'experiments', 'verdicts.json')),
                    reason='сохранённого прогона нет: python experiments/verdicts.py')
def test_saved_run_matches_the_document():
    d = json.load(io.open(os.path.join(ROOT, 'examples', 'experiments', 'verdicts.json'), encoding='utf-8'))
    doc = io.open(os.path.join(ROOT, 'docs', 'EKSPERIMENTY_VERDIKT.md'), encoding='utf-8').read()
    p = d['params']
    assert p['step_h'] <= 12, 'критерий требует отсечку не реже раза в 12 часов'
    assert p['kp_storm'] == V.KP_STORM and p['pfu_sep'] == V.PFU_SEP
    assert p['duration_min'] == V.DUR_MIN and p['window_offsets_min'] == V.OFFSETS_MIN
    assert p['period'][0].startswith('2024-05-01') and p['period'][1].startswith('2024-06-30')
    s, b = d['scores']['all']['service'], d['scores']['all']['baseline']
    assert s['всего'] == b['всего'] == len(d['rows']), 'обе стороны считаны на одних отсечках'
    assert sum(s.get(k, 0) for k in ('named', 'refused', 'analyst', 'failed')) == s['всего']
    # выделенные числа документа совпадают с сохранёнными расчётами
    assert '| **пропуски** (названо окно, а событие в нём было) | **%d** | **%d** |' % (
        s.get('пропуски', 0), b.get('пропуски', 0)) in doc
    assert '| **ложные тревоги** (условие поставлено, события не было ни в одном окне) | **%d** | **%d** |' % (
        s.get('ложные_тревоги', 0), b.get('ложные_тревоги', 0)) in doc
    assert len(d['misses']['service']) >= s.get('пропуски', 0), 'промахи перечислены поимённо'
    # сумма по видам события сходится с числом окон: ни одно окно не потеряно и не посчитано дважды
    n_win = sum(len(r['truth']) for r in d['rows'])
    for side in ('service', 'baseline'):
        for kind, c in d['window_errors'][side].items():
            assert sum(c.get(k, 0) for k in ('попадания', 'пропуски', 'ложные_тревоги',
                                             'верные_отказы', 'факт_неизвестен')) == n_win, (side, kind)
    assert 'python experiments/verdicts.py' in d['command']
    assert d['measured']['elapsed_s'] > 0 and d['measured']['cutoffs'] == len(d['rows'])


@pytest.mark.skipif(not os.path.isfile(os.path.join(ROOT, 'examples', 'experiments', 'verdicts.json')),
                    reason='сохранённого прогона нет: python experiments/verdicts.py')
def test_saved_run_is_a_strict_forecast_sweep_over_the_required_period():
    d = json.load(io.open(os.path.join(ROOT, 'examples', 'experiments', 'verdicts.json'), encoding='utf-8'))
    cuts = [datetime.fromisoformat(r['cutoff_utc']) for r in d['rows']]
    assert cuts[0] == datetime(2024, 5, 1, tzinfo=UTC)
    assert max(cuts) >= datetime(2024, 6, 30, tzinfo=UTC), 'период доведён до 30 июня'
    step = timedelta(hours=d['params']['step_h'])
    assert all(b - a == step for a, b in zip(cuts, cuts[1:])), 'шаг ровный, пропусков отсечек нет'
    doc = io.open(os.path.join(ROOT, 'docs', 'EKSPERIMENTY_VERDIKT.md'), encoding='utf-8').read()
    assert 'Границы применимости' in doc and 'Промахи сервиса поимённо' in doc
    assert 'последнее наблюдение' in doc.lower()
