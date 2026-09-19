# -*- coding: utf-8 -*-
"""Эксперимент Т5 ПО ВЕРДИКТУ: сквозной перебор отсечек 1 мая — 30 июня 2024.

Запуск (одна команда): python experiments/verdicts.py
Пишет docs/EKSPERIMENTY_VERDIKT.md и examples/experiments/verdicts.json — сохранённые
расчёты по каждой отсечке, из которых собраны все числа отчёта.
Ключи: --step-h N (шаг отсечки, ч; по умолчанию 6), --from/--to ГГГГ-ММ-ДД (для проверок).

ЧЕМ ЭТОТ ЭКСПЕРИМЕНТ ОТЛИЧАЕТСЯ ОТ `experiments/forecast_lines.py`.
Тот считает срабатывания ОТДЕЛЬНЫХ ЛИНИЙ УСЛОВИЙ — промежуточную величину. Критерий Т5
требует показателей по ПРОГНОЗИРУЕМОЙ ВЕЛИЧИНЕ, а прогнозируемая величина сервиса — это
ВЕРДИКТ и НАЗВАННОЕ ОКНО. Поэтому здесь на каждой отсечке запускается полный расчёт тем же
кодом, что и приложение (`app.compute.run`, режим «прогноз из прошлого»), и считается то,
что пользователь видит на экране: назвал ли сервис окно, отказал ли, потребовал ли решения
аналитика — и оказалось ли названное окно неудачным по окончательным архивным данным.

ПОСТАНОВКА ЗАПРОСА — умолчания приложения из `config/settings.toml` [ui]: длительность ВКД
360 мин, период поиска 720 мин, два окна-кандидата со сдвигами 120 и 360 мин. Ни один
параметр под результат не подбирался: взято то, что видит пользователь при открытии экрана.

ПРОСТОЙ ПОДХОД ДЛЯ СРАВНЕНИЯ — «последнее наблюдение»: решение по последнему завершённому
наблюдению на отсечку, без прогноза, без траектории и без сравнения окон. Ему СОЗНАТЕЛЬНО
дано больше данных, чем строгому режиму сервиса: окончательный ряд Kp GFZ и численный архив
GOES, которых строгий режим не имеет права использовать (историческая публикация именно этих
версий не доказана, CONTRACT §10). Это делает его честным соперником, а не соломенным чучелом:
он проигрывает не от нехватки данных, а от отсутствия прогноза и траектории.

ФАКТ (после отсечки, только для проверки, Т4) — те же определения, что в forecast_lines.py:
  * «буря в окне» — Kp ≥ kp_check хотя бы в одном 3-часовом интервале ОКОНЧАТЕЛЬНОГО ряда GFZ,
    пересекающем окно;
  * «протонное событие в окне» — наблюдённый поток GOES ≥10 МэВ ≥ goes_p10_warning_pfu в
    пятиминутной ячейке, пересекающей окно (архив NASA iSWA, `data/goes_2024`).
Пороги — из настроек приложения (Thresholds.from_settings), не из кода эксперимента.

СЧЁТ ПО ВЕРДИКТУ (главные числа отчёта, по отсечкам):
  * «назвал окно» — вердикт `preferred` (одно окно) или `equivalent` (окна равнозначны);
  * «отказ» — вердикт `insufficient` (нет покрытия обязательной линии);
  * «решение аналитика» — вердикты `all_need_check` и `trade_off`;
  * ПРОПУСК — сервис назвал окно, а в названном окне по факту БЫЛА буря или протонное событие;
  * ЛОЖНАЯ ТРЕВОГА — условие поставлено хотя бы на одно окно, а по факту ни в одном окне
    отсечки не было ни бури, ни протонного события.
Разбор ошибок по видам (буря отдельно от протонного события, договор: не складывать) —
вторая таблица, по ОКНАМ, а не по отсечкам.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import traceback
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.compute import ALGO_VERSION, run                                             # noqa: E402
from vkd.config import section as cfg_section, settings_path                          # noqa: E402
from vkd.windows.compare import Thresholds                                            # noqa: E402

UTC = timezone.utc
PERIOD = (datetime(2024, 5, 1, tzinfo=UTC), datetime(2024, 6, 30, 23, 59, tzinfo=UTC))
STEP_H_DEFAULT = 6                       # плотнее обязательного «не реже раза в 12 ч»
TH = Thresholds.from_settings()
KP_STORM, PFU_SEP = TH.kp_check, TH.goes_p10_warning_pfu
_UI = cfg_section('ui')
DUR_MIN = int(_UI.get('duration_min', 360))
SEARCH_MIN = int(_UI.get('search_min', 720))
OFFSETS_MIN = [int(x) for x in _UI.get('window_offsets_min', [120, 360])]

# Выраженное событие: буря Гэннон. Те же сутки, что в experiments/forecast_lines.py, —
# два эксперимента Т5 должны говорить об одном и том же событии.
EVENT_DAYS = ('2024-05-10', '2024-05-11', '2024-05-12')
# Контрольный период выбран ПРАВИЛОМ ПО ДАННЫМ, а не под результат: самый длинный отрезок
# целых суток внутри 01.05—30.06, где по окончательным архивным данным нет ни бури (Kp ≥ порога),
# ни протонного события (поток ≥ порога). Правило проверяется в control_period() ниже —
# если архив изменится, границы пересчитаются, а не останутся зашитыми.
CONTROL_RULE_RU = ('самый длинный непрерывный отрезок целых суток периода, в котором по '
                   'окончательным архивным данным нет ни бури Kp ≥ %g, ни протонного события '
                   '≥ %g pfu' % (KP_STORM, PFU_SEP))

STORM_KINDS = {'GST', 'KP'}              # виды условий Condition.kind по буре
PROTON_KINDS = {'SEP', 'GOES'}           # ... и по протонному событию
NAMED_VERDICTS = ('preferred', 'equivalent')
ANALYST_VERDICTS = ('all_need_check', 'trade_off')
REFUSAL_VERDICTS = ('insufficient',)
VERDICT_RU = {'preferred': 'предпочтительное окно', 'equivalent': 'окна равнозначны',
              'all_need_check': 'все окна под условием', 'trade_off': 'компромисс без победителя',
              'insufficient': 'оснований недостаточно'}
GROUP_RU = {'named': 'назвал окно', 'refused': 'отказался', 'analyst': 'решение аналитика',
            'failed': 'расчёт не состоялся'}


# --------------------------------------------------------------------------- факт после отсечки
def truth_series():
    """Окончательный ряд Kp GFZ и численные наблюдения GOES ≥10 МэВ за весь период.

    Берутся ТЕМИ ЖЕ функциями, что в experiments/forecast_lines.py, чтобы у двух экспериментов
    Т5 факт был буквально один и тот же, а не два похожих определения.
    """
    import experiments.forecast_lines as FL
    samples, _events, _facts = FL.archive()
    return FL.kp_truth(samples), FL.goes_truth()


def _overlap(rows, a, b, *, above=None):
    """Есть ли в rows [(от, до, значение)] запись, пересекающая [a, b) (и ≥ above, если задано)."""
    for t0, t1, v in rows:
        if t0 < b and t1 > a and (above is None or v >= above):
            return True
    return False


def window_truth(kp_obs, goes_obs, start, minutes):
    """Факт по окну: была ли буря, было ли протонное событие, есть ли чем это проверить."""
    end = start + timedelta(minutes=minutes)
    return {'storm': _overlap(kp_obs, start, end, above=KP_STORM),
            'proton': _overlap(goes_obs, start, end, above=PFU_SEP),
            'kp_truth_available': _overlap(kp_obs, start, end),
            'goes_truth_available': _overlap(goes_obs, start, end)}


def control_period(kp_obs, goes_obs):
    """Границы контрольного отрезка по правилу CONTROL_RULE_RU (сутки UTC, обе включительно)."""
    day = PERIOD[0]
    quiet = []
    while day <= PERIOD[1]:
        nxt = day + timedelta(days=1)
        calm = not _overlap(kp_obs, day, nxt, above=KP_STORM) and not _overlap(goes_obs, day, nxt, above=PFU_SEP)
        quiet.append((day.strftime('%Y-%m-%d'), calm))
        day = nxt
    best = cur = None
    for d, calm in quiet:
        if not calm:
            cur = None
            continue
        cur = (d, d) if cur is None else (cur[0], d)
        if best is None or (datetime.fromisoformat(cur[1]) - datetime.fromisoformat(cur[0])) > \
                (datetime.fromisoformat(best[1]) - datetime.fromisoformat(best[0])):
            best = cur
    return best


# --------------------------------------------------------------------------- простой подход
def baseline_decision(cutoff, kp_obs, goes_obs, kp_ends, goes_ts, windows):
    """«Последнее наблюдение»: вердикт по последнему завершённому наблюдению на отсечку.

    Правило целиком:
      * нет ни одного завершённого наблюдения (ни Kp, ни GOES) до отсечки → «оснований
        недостаточно» (отказ) — простому подходу нечем решать;
      * последний Kp ≥ kp_check ИЛИ последний поток GOES ≥10 МэВ ≥ goes_p10_warning_pfu →
        условие на ОБА окна, «решение аналитика»: обстановка сейчас плохая;
      * иначе → называет ПЕРВОЕ (раннее) окно-кандидат. Траектории у подхода нет, различать
        окна ему нечем, поэтому выбирается ближайшее — это и есть «без траектории».
    Подходу даны ОКОНЧАТЕЛЬНЫЙ ряд GFZ и численный архив GOES, то есть больше, чем строгому
    режиму сервиса; проверка публикации к нему не применяется сознательно.
    """
    i = bisect_right(kp_ends, cutoff) - 1
    last_kp = kp_obs[i][2] if i >= 0 else None
    j = bisect_right(goes_ts, cutoff) - 1
    last_goes = goes_obs[j][2] if j >= 0 else None
    if last_kp is None and last_goes is None:
        return {'verdict': 'insufficient', 'group': 'refused', 'named': [],
                'condition_windows': [], 'storm_condition': False, 'proton_condition': False,
                'last_kp': None, 'last_goes_pfu': None,
                'rule': 'наблюдений до отсечки нет — решать не по чему'}
    storm_c = last_kp is not None and last_kp >= KP_STORM
    proton_c = last_goes is not None and last_goes >= PFU_SEP
    if storm_c or proton_c:
        return {'verdict': 'all_need_check', 'group': 'analyst', 'named': [],
                'condition_windows': [w.isoformat() for w in windows],
                'storm_condition': storm_c, 'proton_condition': proton_c,
                'last_kp': last_kp, 'last_goes_pfu': last_goes,
                'rule': 'последнее наблюдение выше порога — условие на все окна'}
    return {'verdict': 'preferred', 'group': 'named', 'named': [windows[0].isoformat()],
            'condition_windows': [], 'storm_condition': False, 'proton_condition': False,
            'last_kp': last_kp, 'last_goes_pfu': last_goes,
            'rule': 'последнее наблюдение ниже порогов — названо ближайшее окно'}


# --------------------------------------------------------------------------- перебор
def service_row(cutoff):
    """Полный расчёт приложения на отсечку. Возвращает разбор вердикта или причину несостоявшегося."""
    r = run('history_forecast', cutoff, DUR_MIN, SEARCH_MIN, list(OFFSETS_MIN))
    rec = r.rec
    windows, conds = [], {}
    for a in r.assessments:
        w = a.window.start_utc
        windows.append(w)
        cl = [c for m in a.mechanisms for c in m.conditions]
        conds[w.isoformat()] = [{'kind': c.kind, 'severity': c.severity, 'text': c.text} for c in cl]
    verdict = rec.verdict
    if verdict == 'preferred':
        named = [rec.preferred.start_utc.isoformat()]
    elif verdict == 'equivalent':
        # равнозначными объявляются окна БЕЗ условий: при двух окнах-кандидатах это ровно
        # тот набор, который правило назвало равнозначным (vkd/windows/compare.py, п.5)
        named = [k for k in conds if not conds[k]]
    else:
        named = []
    group = ('named' if verdict in NAMED_VERDICTS else
             'refused' if verdict in REFUSAL_VERDICTS else 'analyst')
    fc = [f for f in (r.S.get('forecasts') or []) if f.get('channel') == 'kp_forecast']
    return {'verdict': verdict, 'group': group, 'named': named, 'rule': rec.rule_applied,
            'missing': list(rec.missing), 'conditions': conds,
            'windows': [w.isoformat() for w in windows],
            'noaa_status': (fc[0].get('status') if fc else 'absent'),
            'noaa_reason': (fc[0].get('reason') if fc else None)}


def sweep(step_h, t_from, t_to, kp_obs, goes_obs, progress=True):
    kp_ends = [x[1] for x in kp_obs]
    goes_ts = [x[1] for x in goes_obs]
    rows, cut = [], t_from
    n_total = int((t_to - t_from).total_seconds() // 3600 // step_h) + 1
    while cut <= t_to:
        row = {'cutoff_utc': cut.isoformat(), 'day': cut.strftime('%Y-%m-%d')}
        t0 = time.time()
        try:
            svc = service_row(cut)
        except Exception as exc:                                        # noqa: BLE001
            svc = {'verdict': None, 'group': 'failed', 'named': [], 'rule': None,
                   'missing': [], 'conditions': {}, 'windows': [],
                   'error': '%s: %s' % (type(exc).__name__, exc),
                   'traceback_tail': traceback.format_exc().strip().splitlines()[-1]}
        row['seconds'] = round(time.time() - t0, 3)
        wins = [datetime.fromisoformat(w) for w in svc.get('windows') or []]
        if not wins:                                    # расчёт не состоялся — окна считаем по постановке
            wins = [cut + timedelta(minutes=o) for o in OFFSETS_MIN]
        row['truth'] = {w.isoformat(): window_truth(kp_obs, goes_obs, w, DUR_MIN) for w in wins}
        row['service'] = svc
        row['baseline'] = baseline_decision(cut, kp_obs, goes_obs, kp_ends, goes_ts, wins)
        rows.append(row)
        if progress and len(rows) % 20 == 0:
            print('  ... %d/%d отсечек, последняя %s' % (len(rows), n_total, cut.strftime('%d.%m %HZ')),
                  file=sys.stderr, flush=True)
        cut += timedelta(hours=step_h)
    return rows


# --------------------------------------------------------------------------- показатели
def _any_fact(row):
    """Был ли по факту хоть один из двух видов события хоть в одном окне отсечки."""
    return any(t['storm'] or t['proton'] for t in row['truth'].values())


def verdict_scores(rows, side):
    """Числа по вердиктам для одной стороны ('service' или 'baseline'). Всё — по отсечкам."""
    out = Counter()
    for row in rows:
        d = row[side]
        out[d['group']] += 1
        out['всего'] += 1
        if d['group'] == 'named':
            bad = [w for w in d['named'] if row['truth'].get(w, {}).get('storm') or
                   row['truth'].get(w, {}).get('proton')]
            if bad:
                out['пропуски'] += 1
                out['пропуски_по_буре'] += any(row['truth'][w]['storm'] for w in bad)
                out['пропуски_по_протонам'] += any(row['truth'][w]['proton'] for w in bad)
        if side == 'service':
            flagged = any(c['kind'] in (STORM_KINDS | PROTON_KINDS)
                          for cl in (d.get('conditions') or {}).values() for c in cl)
        else:
            flagged = bool(d.get('condition_windows'))
        if flagged:
            out['условие_поставлено'] += 1
            if not _any_fact(row):
                out['ложные_тревоги'] += 1
    return out


def window_error_table(rows, side):
    """Ошибки по ВИДАМ события, по окнам: буря отдельно, протонное событие отдельно.

    Окна без наблюдений соответствующего ряда в счёт не идут: факт там неизвестен, и
    записывать его в «ничего не было» нельзя.
    """
    res = {'буря': Counter(), 'протонное событие': Counter()}
    for row in rows:
        d = row[side]
        if d['group'] == 'failed':
            continue
        for w, t in row['truth'].items():
            if side == 'service':
                kinds = {c['kind'] for c in (d.get('conditions') or {}).get(w, [])}
                storm_flag, proton_flag = bool(kinds & STORM_KINDS), bool(kinds & PROTON_KINDS)
            else:
                on = w in (d.get('condition_windows') or [])
                storm_flag, proton_flag = on and d.get('storm_condition', False), on and d.get('proton_condition', False)
            for name, flag, fact, have in (('буря', storm_flag, t['storm'], t['kp_truth_available']),
                                           ('протонное событие', proton_flag, t['proton'], t['goes_truth_available'])):
                if not have:
                    res[name]['факт_неизвестен'] += 1
                    continue
                c = res[name]
                c['попадания'] += flag and fact
                c['пропуски'] += (not flag) and fact
                c['ложные_тревоги'] += flag and (not fact)
                c['верные_отказы'] += (not flag) and (not fact)
    return res


# --------------------------------------------------------------------------- отчёт
def _pct(a, b):
    return '—' if not b else ('%.1f %%' % (100.0 * a / b)).replace('.', ',')


def misses(rows, side):
    """Промахи поимённо: отсечка, названное окно, что в нём оказалось по факту, каким правилом назвали."""
    out = []
    for row in rows:
        d = row[side]
        if d['group'] != 'named':
            continue
        for w in d['named']:
            t = row['truth'].get(w) or {}
            if not (t.get('storm') or t.get('proton')):
                continue
            out.append({'cutoff_utc': row['cutoff_utc'], 'window_start_utc': w,
                        'storm': bool(t.get('storm')), 'proton': bool(t.get('proton')),
                        'verdict': d['verdict'], 'rule': d.get('rule')})
    return out


def false_alarms(rows, side):
    """Ложные тревоги поимённо: отсечка и виды поставленных условий при пустом факте."""
    out = []
    for row in rows:
        d = row[side]
        if d['group'] == 'failed' or _any_fact(row):
            continue
        if side == 'service':
            kinds = sorted({c['kind'] for cl in (d.get('conditions') or {}).values() for c in cl
                            if c['kind'] in (STORM_KINDS | PROTON_KINDS)})
        else:
            kinds = ([k for k, on in (('GST', d.get('storm_condition')), ('GOES', d.get('proton_condition'))) if on]
                     if d.get('condition_windows') else [])
        if kinds:
            out.append({'cutoff_utc': row['cutoff_utc'], 'kinds': kinds, 'verdict': d['verdict']})
    return out


def _verdict_block(L, rows, title):
    svc, base = verdict_scores(rows, 'service'), verdict_scores(rows, 'baseline')
    if not rows:
        L += ['', title, '', 'Отсечек в этом отрезке нет.']
        return svc, base
    L += ['', title, '',
          '| показатель по вердикту | сервис | простой подход «последнее наблюдение» |',
          '|---|---:|---:|']
    for key, ru in (('named', GROUP_RU['named']), ('refused', GROUP_RU['refused']),
                    ('analyst', GROUP_RU['analyst']), ('failed', GROUP_RU['failed'])):
        L.append('| %s | %d | %d |' % (ru, svc[key], base[key]))
    L.append('| **пропуски** (названо окно, а событие в нём было) | **%d** | **%d** |'
             % (svc['пропуски'], base['пропуски']))
    L.append('| **ложные тревоги** (условие поставлено, события не было ни в одном окне) | **%d** | **%d** |'
             % (svc['ложные_тревоги'], base['ложные_тревоги']))
    L.append('| отсечек, где условие вообще ставилось | %d | %d |'
             % (svc['условие_поставлено'], base['условие_поставлено']))
    L.append('| всего отсечек | %d | %d |' % (svc['всего'], base['всего']))
    return svc, base


def _error_block(L, rows, side, title):
    t = window_error_table(rows, side)
    if not rows:
        L += ['', title, '', 'Отсечек в этом отрезке нет.']
        return t
    L += ['', title, '', '| вид события | попадания | пропуски | ложные тревоги | верные отказы | факт неизвестен |',
          '|---|---:|---:|---:|---:|---:|']
    for name in ('буря', 'протонное событие'):
        c = t[name]
        L.append('| %s | %d | %d | %d | %d | %d |'
                 % (name, c['попадания'], c['пропуски'], c['ложные_тревоги'], c['верные_отказы'], c['факт_неизвестен']))
    return t


def build_report(rows, step_h, elapsed_s, control, t_from, t_to, generated=None):
    ev_rows = [r for r in rows if r['day'] in EVENT_DAYS]
    ctl_rows = [r for r in rows if control and control[0] <= r['day'] <= control[1]]
    failed = [r for r in rows if r['service']['group'] == 'failed']
    refused = [r for r in rows if r['service']['group'] == 'refused']
    noaa_missing = [r for r in rows if r['service'].get('noaa_status') == 'missing']
    L = ['# Эксперимент Т5 по ВЕРДИКТУ: сквозной перебор отсечек, %s — %s'
         % (t_from.strftime('%d.%m.%Y'), t_to.strftime('%d.%m.%Y')), '',
         'Сгенерировано `experiments/verdicts.py` (%s), одна команда: `python experiments/verdicts.py`. '
         'Сохранённые расчёты по каждой отсечке — `examples/experiments/verdicts.json`; все числа ниже '
         'собраны из этого файла, а не набраны руками.'
         % (datetime.fromisoformat(generated) if generated else datetime.now(UTC)).strftime('%Y-%m-%d %H:%MZ'), '',
         '## Что именно меряется', '',
         'Существующий эксперимент `docs/EKSPERIMENTY_PROGNOZ.md` считает срабатывания ОТДЕЛЬНЫХ ЛИНИЙ '
         'условий — промежуточную величину. Критерий Т5 требует показателей по ПРОГНОЗИРУЕМОЙ ВЕЛИЧИНЕ, '
         'а прогнозируемая величина сервиса — это ВЕРДИКТ и НАЗВАННОЕ ОКНО. Здесь на каждой отсечке '
         'запускается полный расчёт тем же кодом, что и приложение (`app.compute.run`, режим «прогноз из '
         'прошлого»), и считается то, что видит пользователь.', '',
         '- период: %s — %s, шаг отсечки **%d ч** (обязательное «не реже раза в 12 ч» перекрыто вдвое), '
         'всего отсечек **%d**;' % (t_from.strftime('%d.%m.%Y %HZ'), t_to.strftime('%d.%m.%Y %HZ'), step_h, len(rows)),
         '- постановка запроса — умолчания приложения из `%s` [ui]: длительность ВКД %d мин, период поиска '
         '%d мин, два окна-кандидата со сдвигами %s мин. Под результат ничего не подбиралось;'
         % (os.path.relpath(settings_path(), ROOT).replace('\\', '/'), DUR_MIN, SEARCH_MIN,
            ' и '.join(map(str, OFFSETS_MIN))),
         '- пороги — из настроек приложения: `kp_check = %g`, `goes_p10_warning_pfu = %g` (версия алгоритма `%s`);'
         % (KP_STORM, PFU_SEP, ALGO_VERSION),
         '- прогон занял **%d мин %02d с** (%.2f с на отсечку в среднем), сеть не использовалась.'
         % (int(elapsed_s) // 60, int(elapsed_s) % 60, elapsed_s / max(1, len(rows))), '',
         '### Как вердикты сведены в три исхода', '',
         '| вердикт сервиса | исход | смысл |', '|---|---|---|',
         '| `preferred` | назвал окно | одно окно предпочтительнее |',
         '| `equivalent` | назвал окно | окна равнозначны, годится любое из названных |',
         '| `all_need_check` | решение аналитика | все окна под условием, правило выбирать не вправе |',
         '| `trade_off` | решение аналитика | механизмы указывают на разные окна |',
         '| `insufficient` | отказался | нет покрытия обязательной линии |', '',
         '### Определения показателей', '',
         '- **ПРОПУСК** — сервис назвал окно, а по ОКОНЧАТЕЛЬНЫМ архивным данным в этом окне событие всё-таки '
         'было: буря Kp ≥ %g в 3-часовом интервале ряда GFZ, пересекающем окно, или наблюдённый поток GOES '
         '≥10 МэВ ≥ %g pfu в пятиминутной ячейке, пересекающей окно;' % (KP_STORM, PFU_SEP),
         '- **ЛОЖНАЯ ТРЕВОГА** — условие поставлено хотя бы на одно окно отсечки, а по факту ни в одном окне '
         'этой отсечки не было ни бури, ни протонного события;',
         '- факт берётся теми же функциями, что в `experiments/forecast_lines.py`, — у двух экспериментов Т5 '
         'определение факта буквально одно.', '',
         '## Простой подход для сравнения', '',
         '**«Последнее наблюдение»**: решение принимается по последнему завершённому наблюдению на отсечку — '
         'если последний окончательный 3-часовой Kp GFZ ≥ %g или последний пятиминутный поток GOES ≥10 МэВ '
         '≥ %g pfu, подход объявляет условие на оба окна («решение аналитика»); если наблюдений до отсечки нет '
         'вовсе — отказывает; иначе называет ближайшее окно-кандидат. Ни прогноза, ни траектории, ни сравнения '
         'окон у него нет.' % (KP_STORM, PFU_SEP), '',
         'Почему это честный соперник, а не соломенное чучело:', '',
         '1. он решает ТУ ЖЕ задачу на ТЕХ ЖЕ отсечках и тех же двух окнах-кандидатах, теми же порогами;',
         '2. ему сознательно дано БОЛЬШЕ данных, чем сервису: окончательный ряд Kp GFZ и численный архив GOES, '
         'которые строгий режим использовать не вправе (историческая публикация именно этих версий не доказана, '
         'CONTRACT §10). Проверка публикации к нему не применяется;',
         '3. это ровно тот порядок, которым обстановку оценивают «на глаз» по последнему индексу, — его и надо '
         'побеждать, чтобы сервис имел смысл;',
         '4. он проигрывает (или выигрывает) не из-за нехватки данных, а только из-за отсутствия прогноза и '
         'траектории — то есть сравнение адресует именно то, что сервис добавляет.']
    svc_all, base_all = _verdict_block(L, rows, '## Сервис против простого подхода: весь период')
    L += ['', 'Как читать: строки «назвал окно / отказался / решение аналитика» — это распределение исходов, '
          'а не оценка качества; качество — в двух выделенных строках.', '',
          '### Из чего сложилось распределение вердиктов сервиса', '',
          '| вердикт | отсечек | доля |', '|---|---:|---:|']
    dist = Counter(r['service']['verdict'] for r in rows)
    for v, n in dist.most_common():
        L.append('| `%s` (%s) | %d | %s |' % (v, VERDICT_RU.get(v, 'расчёт не состоялся'), n, _pct(n, len(rows))))
    es = _error_block(L, rows, 'service', '### Ошибки сервиса по видам события (по окнам, весь период)')
    eb = _error_block(L, rows, 'baseline', '### Ошибки простого подхода по видам события (по окнам, весь период)')
    L += ['', 'Бурю и протонное событие не складываем (договор): каждая строка считается против своей величины. '
          'Столбец «факт неизвестен» — окна, на которые соответствующего ряда наблюдений в архиве нет; '
          'записывать их в «ничего не было» нельзя.']
    # --- чтение результата: собирается ИЗ ЧИСЕЛ выше, а не написано заранее
    L += ['', '## Чтение результата: где сервис выигрывает и где проигрывает', '']
    if svc_all['пропуски'] > base_all['пропуски']:
        L.append('**По пропускам сервис проигрывает простому подходу: %d против %d.** Это неудобный для нас '
                 'результат, и он не смягчается.' % (svc_all['пропуски'], base_all['пропуски']))
    elif svc_all['пропуски'] < base_all['пропуски']:
        L.append('**По пропускам сервис выигрывает: %d против %d у простого подхода.**'
                 % (svc_all['пропуски'], base_all['пропуски']))
    else:
        L.append('**По пропускам сервис и простой подход равны: по %d.**' % svc_all['пропуски'])
    L += ['', '| линия | пропуски сервиса | пропуски простого подхода | ложные тревоги сервиса | ложные тревоги простого подхода |',
          '|---|---:|---:|---:|---:|']
    for name in ('буря', 'протонное событие'):
        L.append('| %s | %d | %d | %d | %d |' % (name, es[name]['пропуски'], eb[name]['пропуски'],
                                                 es[name]['ложные_тревоги'], eb[name]['ложные_тревоги']))
    sep_worse = es['протонное событие']['пропуски'] > eb['протонное событие']['пропуски']
    gst_ahead = es['буря']['попадания'] > eb['буря']['попадания']
    L += ['', 'Разложение по линиям показывает, откуда берётся разница:', '',
          '1. **Протонное событие.** Простой подход видит ЧИСЛЕННЫЙ архив GOES и ловит событие в ту же минуту, '
          'когда поток перешёл порог. Строгий режим сервиса этот архив использовать НЕ ВПРАВЕ (историческая '
          'публикация именно этой версии не доказана, CONTRACT §10) и знает о протонном событии только из '
          'уведомлений DONKI, которые выходят позже. Счёт пропусков по окнам: %d у сервиса против %d у '
          'простого подхода — %s.'
          % (es['протонное событие']['пропуски'], eb['протонное событие']['пропуски'],
             'это ЦЕНА строгости, и её надо знать, а не прятать за средним числом' if sep_worse
             else 'разрыв в эту сторону не подтвердился'),
          '2. **Буря.** Сервис опирается на опубликованный прогноз прихода выброса и прогноз NOAA, поэтому '
          'может поставить условие ЗАРАНЕЕ, а простой подход узнаёт о буре, только когда она уже в последнем '
          'наблюдении. Счёт по окнам: попаданий %d у сервиса против %d, ложных тревог %d против %d — %s.'
          % (es['буря']['попадания'], eb['буря']['попадания'],
             es['буря']['ложные_тревоги'], eb['буря']['ложные_тревоги'],
             'заблаговременность подтверждается и оплачивается ложными тревогами' if gst_ahead
             else 'преимущество в заблаговременности этими числами НЕ подтверждается'),
          '3. **Осторожность вердикта.** Решения аналитика сервис потребовал на отсечках числом %d, простой '
          'подход — %d. Каждое такое требование — это отказ назвать окно, и неназванное окно в счёт пропусков не '
          'попадает ни у кого: часть разницы по пропускам объясняется именно этим, а не точностью.'
          % (svc_all['analyst'], base_all['analyst']), '']
    if sep_worse:
        L.append('Вывод, который отсюда следует и который нельзя подменить: **по протонному событию сервис '
                 'в строгом режиме уступает простому подходу, потому что у простого подхода есть численный ряд '
                 'наблюдений, а у строгого режима его нет.** Закрыть этот разрыв можно доказанной исторической '
                 'публикацией архива GOES, а не изменением правила; пока она не доказана, разрыв остаётся и '
                 'объявляется.')
    else:
        L.append('Вывод: по протонному событию разрыв не в пользу простого подхода — числа выше.')
    # --- собственные промахи сервиса поимённо: критерий требует показать их явно
    sv_miss, bs_miss = misses(rows, 'service'), misses(rows, 'baseline')
    L += ['', '## Промахи сервиса поимённо', '']
    if sv_miss:
        L += ['Каждая строка — отсечка, на которой сервис НАЗВАЛ окно, а по окончательным архивным данным '
              'в этом окне событие было.', '',
              '| отсечка (UTC) | названное окно | что было по факту | вердикт |', '|---|---|---|---|']
        for m in sv_miss:
            what = ', '.join(x for x in (('буря Kp ≥ %g' % KP_STORM) if m['storm'] else '',
                                         ('поток GOES ≥ %g pfu' % PFU_SEP) if m['proton'] else '') if x)
            L.append('| %s | %s | %s | `%s` |' % (m['cutoff_utc'][:16].replace('T', ' '),
                                                  m['window_start_utc'][:16].replace('T', ' '), what, m['verdict']))
        L += ['', 'Строк в таблице %d, а пропусков по вердиктам %d: счёт по вердиктам идёт по ОТСЕЧКАМ, '
              'и отсечка с двумя названными равнозначными окнами, оба из которых оказались неудачными, '
              'считается одним пропуском. Правило, которым окно было названо, по каждой строке лежит в '
              '`examples/experiments/verdicts.json` (ключ `rows[].service.rule`).'
              % (len(sv_miss), svc_all['пропуски'])]
    else:
        L.append('Промахов нет: ни на одной отсечке сервис не назвал окно, в котором по окончательным архивным '
                 'данным была буря или протонное событие. Это результат ОСТОРОЖНОСТИ правила, а не точности '
                 'прогноза: цена осторожности — ложные тревоги, они в таблицах выше.')
    L += ['', 'Для сравнения, промахи простого подхода: **%d** (список — в `verdicts.json`, '
          '`rows[].baseline`).' % len(bs_miss)]
    fa_days = Counter(x['cutoff_utc'][:10] for x in false_alarms(rows, 'service'))
    L += ['', '## Ложные тревоги сервиса по дням', '',
          ', '.join('%s: %d' % kv for kv in sorted(fa_days.items())) or 'нет']
    _verdict_block(L, ev_rows, '## Выраженное событие: буря Гэннон, отсечки %s (всего %d)'
                   % ('–'.join(d[8:] + '.05' for d in (EVENT_DAYS[0], EVENT_DAYS[-1])), len(ev_rows)))
    _error_block(L, ev_rows, 'service', '### Ошибки сервиса на буре (по окнам)')
    if control:
        _verdict_block(L, ctl_rows, '## Контрольный период: %s — %s (всего отсечек %d)'
                       % (control[0], control[1], len(ctl_rows)))
        _error_block(L, ctl_rows, 'service', '### Ошибки сервиса на контроле (по окнам)')
        L += ['', 'Контрольный отрезок выбран ПРАВИЛОМ ПО ДАННЫМ, а не под результат: %s. '
              'Границы пересчитываются при каждом прогоне.' % CONTROL_RULE_RU]
        ctl_miss = misses(ctl_rows, 'service')
        if ctl_miss:
            L += ['', 'На контроле у сервиса пропусков числом %d — при том, что отрезок выбран как спокойный. '
                  'Противоречия нет: отрезок спокоен ПО СУТКАМ, а горизонт поиска (%d мин) выносит окна за '
                  'его правую границу, где событие уже начиналось. Первое такое окно — %s. Это граница '
                  'самого способа резать период сутками, и она названа, а не спрятана.'
                  % (len(ctl_miss), SEARCH_MIN + DUR_MIN,
                     ctl_miss[0]['window_start_utc'][:16].replace('T', ' '))]
    # --- границы применимости
    L += ['', '## Границы применимости: где расчёт не состоялся и почему', '']
    if failed:
        L.append('Расчёт не состоялся (исключение в конвейере) на **%d** отсечках из %d:' % (len(failed), len(rows)))
        errs = Counter(r['service'].get('error', '') for r in failed)
        for e, n in errs.most_common():
            L.append('- %d отсечек: `%s`' % (n, e))
    else:
        L.append('Расчёт **состоялся на всех %d отсечках**: ни одного исключения в конвейере. '
                 'Это отдельный результат — строгий режим не падает ни в разрыве архива выпусков, '
                 'ни на границах периода.' % len(rows))
    L += ['', 'Отказ вердиктом («оснований недостаточно») — это НЕ несостоявшийся расчёт, а состоявшийся '
          'вывод: таких отсечек **%d** из %d (%s).' % (len(refused), len(rows), _pct(len(refused), len(rows)))]
    if refused:
        miss = Counter(m for r in refused for m in r['service']['missing'])
        for m, n in miss.most_common(8):
            L.append('- %d раз: %s' % (n, m))
    noaa_dist = Counter(r['service'].get('noaa_status') for r in rows)
    n_gap = sum(1 for r in noaa_missing if 'разрыв' in (r['service'].get('noaa_reason') or ''))
    n_other = len(noaa_missing) - n_gap
    L += ['', 'Прогнозная линия Kp NOAA на горизонт есть не всегда — это второй предел применимости, и он '
          'считан отдельно по причинам, а не одним словом «разрыв»:', '',
          '| состояние линии прогноза NOAA | отсечек | доля |', '|---|---:|---:|']
    for st, ru in (('full', 'полное покрытие горизонта'), ('partial', 'частичное покрытие'),
                   ('missing', 'выпуска на горизонт нет'), ('absent', 'линии в снимке нет')):
        if noaa_dist.get(st):
            L.append('| %s (`%s`) | %d | %s |' % (ru, st, noaa_dist[st], _pct(noaa_dist[st], len(rows))))
    L += ['', 'Из **%d** отсечек без прогноза **%d** приходятся на РАЗРЫВ АРХИВА ВЫПУСКОВ NOAA 15.05—16.06.2024 '
          '(последний допустимый выпуск до отсечки — от 14.05 12:30Z, его горизонт до запрошенного периода не '
          'достаёт), и **%d** — на другие причины (начало периода: допустимого выпуска до отсечки ещё нет). '
          'Это «нет данных», а не «нет условия»: в этих отсечках сервис работает без внешнего прогноза, и его '
          'показатели там относятся к обстановке по уведомлениям DONKI и к траектории. Доля периода без '
          'внешнего прогноза — %s: это надо читать вместе с таблицами выше, а не после них.'
          % (len(noaa_missing), n_gap, n_other, _pct(len(noaa_missing), len(rows)))]
    unknown = sum(1 for r in rows for t in r['truth'].values() if not t['goes_truth_available'])
    tot_w = sum(len(r['truth']) for r in rows)
    L += ['', 'Окон, на которые численных наблюдений GOES в архиве нет (факт по протонному событию неизвестен): '
          '**%d** из %d (%s) — они исключены из счёта по протонному событию, а не записаны в «ничего не было».'
          % (unknown, tot_w, _pct(unknown, tot_w)), '',
          '## Что этот эксперимент НЕ показывает', '',
          '- выраженное событие за период ОДНО (буря Гэннон): числа по нему — разбор случая, а не оценка '
          'вероятностей; доверительных интервалов здесь нет и быть не может;',
          '- «пропуск» здесь означает, что в названном окне по архиву было событие, а не что ВКД в нём '
          'закончился бы происшествием: связь события с исходом работ сервис не заявляет (CONTRACT §9);',
          '- простой подход получает окончательные данные, то есть сравнение поставлено НЕ в нашу пользу; '
          'обратное сравнение (оба на строгом режиме) дало бы сервису преимущество по построению и потому '
          'не считалось;',
          '- отсечки внутри одного события не независимы: соседние отсечки видят одни и те же уведомления, '
          'поэтому числа по отсечкам — это доля времени, а не доля независимых случаев.']
    return L, svc_all, base_all


def main(argv=None):
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description='Эксперимент Т5 по вердикту')
    ap.add_argument('--step-h', type=int, default=STEP_H_DEFAULT, help='шаг отсечки в часах (умолчание 6)')
    ap.add_argument('--from', dest='t_from', default=PERIOD[0].strftime('%Y-%m-%d'))
    ap.add_argument('--to', dest='t_to', default=PERIOD[1].strftime('%Y-%m-%d'))
    ap.add_argument('--out-json', default=os.path.join('examples', 'experiments', 'verdicts.json'))
    ap.add_argument('--out-doc', default=os.path.join('docs', 'EKSPERIMENTY_VERDIKT.md'))
    ap.add_argument('--rebuild-doc', action='store_true',
                    help='пересобрать отчёт из СОХРАНЁННЫХ расчётов, не повторяя перебор '
                         '(правка формулировок не должна требовать восьмиминутного прогона)')
    a = ap.parse_args(argv)
    if a.step_h <= 0 or a.step_h > 12:
        raise SystemExit('шаг отсечки должен быть 1…12 ч: критерий требует не реже раза в 12 часов')
    t_from = datetime.fromisoformat(a.t_from).replace(tzinfo=UTC)
    t_to = datetime.fromisoformat(a.t_to).replace(hour=23, minute=59, tzinfo=UTC)
    if a.rebuild_doc:
        saved = json.load(io.open(os.path.join(ROOT, a.out_json), encoding='utf-8'))
        rows, elapsed = saved['rows'], float(saved['measured']['elapsed_s'])
        control = tuple(saved['params']['control_period']) if saved['params'].get('control_period') else None
        a.step_h = int(saved['params']['step_h'])
        t_from = datetime.fromisoformat(saved['params']['period'][0])
        t_to = datetime.fromisoformat(saved['params']['period'][1])
        generated = saved['generated_utc']
        print('отчёт пересобран из сохранённых расчётов, перебор не повторялся', file=sys.stderr, flush=True)
    else:
        generated = datetime.now(UTC).isoformat()
        print('факт: окончательный ряд Kp GFZ и численные наблюдения GOES…', file=sys.stderr, flush=True)
        kp_obs, goes_obs = truth_series()
        control = control_period(kp_obs, goes_obs)
        print('перебор отсечек с шагом %d ч, %s — %s' % (a.step_h, a.t_from, a.t_to), file=sys.stderr, flush=True)
        t0 = time.time()
        rows = sweep(a.step_h, t_from, t_to, kp_obs, goes_obs)
        elapsed = time.time() - t0
    L, svc, base = build_report(rows, a.step_h, elapsed, control, t_from, t_to, generated)
    out_json = os.path.join(ROOT, a.out_json)
    os.makedirs(os.path.dirname(out_json), exist_ok=True)
    io.open(out_json, 'w', encoding='utf-8').write(json.dumps(
        {'generated_utc': generated, 'algorithm_version': ALGO_VERSION,
         'settings_path': os.path.relpath(settings_path(), ROOT).replace('\\', '/'),
         'command': 'python experiments/verdicts.py --step-h %d' % a.step_h,
         'measured': {'elapsed_s': round(elapsed, 1), 'cutoffs': len(rows),
                      'seconds_per_cutoff': round(elapsed / max(1, len(rows)), 2)},
         'params': {'step_h': a.step_h, 'period': [t_from.isoformat(), t_to.isoformat()],
                    'duration_min': DUR_MIN, 'search_min': SEARCH_MIN, 'window_offsets_min': OFFSETS_MIN,
                    'kp_storm': KP_STORM, 'pfu_sep': PFU_SEP, 'thresholds': TH.__dict__,
                    'event_days': EVENT_DAYS, 'control_period': control, 'control_rule_ru': CONTROL_RULE_RU,
                    'storm_kinds': sorted(STORM_KINDS), 'proton_kinds': sorted(PROTON_KINDS),
                    'baseline_ru': 'последнее наблюдение: окончательный Kp GFZ и численный поток GOES до отсечки'},
         'scores': {'all': {'service': dict(svc), 'baseline': dict(base)},
                    'event': {'service': dict(verdict_scores([r for r in rows if r['day'] in EVENT_DAYS], 'service')),
                              'baseline': dict(verdict_scores([r for r in rows if r['day'] in EVENT_DAYS], 'baseline'))},
                    'control': {'service': dict(verdict_scores([r for r in rows if control and control[0] <= r['day'] <= control[1]], 'service')),
                                'baseline': dict(verdict_scores([r for r in rows if control and control[0] <= r['day'] <= control[1]], 'baseline'))}},
         'window_errors': {side: {k: dict(v) for k, v in window_error_table(rows, side).items()}
                           for side in ('service', 'baseline')},
         'verdict_distribution': dict(Counter(r['service']['verdict'] for r in rows)),
         'misses': {side: misses(rows, side) for side in ('service', 'baseline')},
         'false_alarms': {side: false_alarms(rows, side) for side in ('service', 'baseline')},
         'rows': rows}, ensure_ascii=False, indent=1))
    io.open(os.path.join(ROOT, a.out_doc), 'w', encoding='utf-8', newline='\n').write('\n'.join(L) + '\n')
    print('\n'.join(L))
    print('\nсохранено: %s, %s' % (a.out_doc, a.out_json), file=sys.stderr)


if __name__ == '__main__':
    main()
