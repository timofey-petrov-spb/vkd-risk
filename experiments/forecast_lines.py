# -*- coding: utf-8 -*-
"""Эксперимент Т5: прогнозные линии строгого режима на всём периоде 1 мая — 30 июня 2024.

Запуск: python experiments/forecast_lines.py — пишет docs/EKSPERIMENTY_PROGNOZ.md и
examples/experiments/forecast_lines.json (сохранённые расчёты по каждому окну).
Сетевых запросов нет: архив DONKI (data/archive_2024), реестр NOAA A1 (A2 replay).

Вопрос: насколько условия, поставленные ТОЛЬКО по данным, опубликованным до отсечки,
совпадают с тем, что произошло в окне. Отсечки каждые 6 ч; для каждой — четыре окна
по 6 ч со сдвигом 0/6/12/18 ч от отсечки (горизонт 24 ч). Орбита не нужна: условия
по событиям и прогнозам вычисляет тот же assess_window, что и приложение, с пустой трассой.

Линии (каждая отдельно, чтобы видеть вклад и ложные тревоги):
  * baseline — «последнее наблюдение»: последний Kp из карточек GST с observedTime ≤ отсечки
    (без проверки публикации — базовой линии дано больше, чем строгому режиму) ≥ 7;
  * notif — уведомления DONKI (SEP; GST с Kp ≥ 7 в теле);
  * enlil — прогноз прихода выброса WSA-ENLIL с ожидаемым Kp ≥ 7;
  * noaa — прогноз NOAA 3-day: Kp ≥ 7 в окне (выпуск до отсечки; в разрыве каталога
    15.05–16.06 линии нет — это учитывается как «нет данных», не как «нет условия»);
  * system — объединение notif + enlil + noaa (то, что видит пользователь).

Факт (после отсечки, только для проверки, Т4): «буря в окне» — Kp ≥ 7 хотя бы в одном
3-часовом интервале, пересекающем окно (карточки GST, allKpIndex); «протонное событие
в окне» — SEP DONKI по наблюдательному прибору с eventTime в [начало − 24 ч, конец)
(24 ч — та же конвенция, что в compare.py; архива GOES за 2024 нет — ограничение).
Показатели: попадания, пропуски, ложные тревоги по окнам; заблаговременность — от
публикации записи, давшей условие, до фактического начала бури (только для попаданий).

Чувствительность: линия ENLIL зависит от того, какая оценка Kp прогона берётся
(kp_180 — верхняя, южное поле; kp_135; kp_90 — типичная). Считаются три варианта.
"""
from __future__ import annotations

import io
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import experiments.stub_history as SH                                                 # noqa: E402
from experiments.stub_history import _load, _t, history_bundle, sep_events          # noqa: E402
from vkd.assess.cutoff import apply_cutoff                                            # noqa: E402
from vkd.assess.trapped import BeltTable                                              # noqa: E402
from vkd.integration.noaa_forecast import noaa_forecasts                              # noqa: E402
from vkd.types import Window                                                           # noqa: E402
from vkd.windows.compare import Thresholds, assess_window                             # noqa: E402

UTC = timezone.utc
PERIOD = (datetime(2024, 5, 1, tzinfo=UTC), datetime(2024, 6, 30, tzinfo=UTC))
STEP_H, DUR_MIN, OFFSETS_H, KP_STORM = 6, 360, (0, 6, 12, 18), 7.0
EVENT_DAYS = ('2024-05-10', '2024-05-11', '2024-05-12')     # выраженное событие: буря Гэннон
LINES = ('baseline', 'notif', 'enlil', 'noaa', 'system')
CONFIGURED = tuple(SH.ENLIL_KP_FIELDS)                       # как в приложении (config/settings.toml [history])
VARIANTS = (CONFIGURED,) + tuple(v for v in (('kp_18', 'kp_90', 'kp_135', 'kp_180'), ('kp_180',), ('kp_135',), ('kp_90',)) if v != CONFIGURED)


def kp_truth():
    """(t_from, t_to, kp) по карточкам GST — факт после события."""
    out = []
    for g in _load('gst'):
        for k in g.get('allKpIndex') or []:
            t = _t(k.get('observedTime'))
            if t and k.get('kpIndex') is not None:
                out.append((t - timedelta(hours=3), t, float(k['kpIndex'])))
    return sorted(set(out))


def sep_truth():
    return [(e.start_utc, e.event_id) for e in sep_events()[0] if e.kind.value == 'observation' and e.start_utc]


def evaluate(kp_fields) -> list[dict]:
    SH.ENLIL_KP_FIELDS = tuple(kp_fields)
    samples, events, _ = history_bundle()
    kp_obs, seps = kp_truth(), sep_truth()
    belts, th = BeltTable('min'), Thresholds()
    rows = []
    c = PERIOD[0]
    while c <= PERIOD[1]:
        cut = apply_cutoff(samples, events, [], c)
        ev = list(cut.events)
        fc_lines, _ = noaa_forecasts(c, c, c + timedelta(hours=24))
        forecasts = [s for line in fc_lines for s in line.samples]
        noaa_ok = any(line.channel_id == 'kp_forecast' and line.status in ('full', 'partial') for line in fc_lines)
        last = [(t1, kp) for t0_, t1, kp in kp_obs if t1 <= c]
        base_kp = max(last, key=lambda x: x[0])[1] if last else None
        for off in OFFSETS_H:
            w = Window(c + timedelta(hours=off), DUR_MIN)
            w_end = w.start_utc + timedelta(minutes=DUR_MIN)
            a = assess_window(w, [], belts, None, None, [], th, c, events=ev, forecasts=forecasts)
            reasons = list(a.mechanisms[0].needs_check_reasons)
            line = {'notif': any(('протонное событие' in r or 'геомагнитная буря' in r) for r in reasons),
                    'enlil': any('прихода выброса' in r for r in reasons),
                    'noaa': any('прогноз NOAA' in r for r in reasons),
                    'baseline': base_kp is not None and base_kp >= KP_STORM}
            line['system'] = line['notif'] or line['enlil'] or line['noaa']
            storm = any(kp >= KP_STORM and t0_ < w_end and t1 > w.start_utc for t0_, t1, kp in kp_obs)
            sep = any(w.start_utc - timedelta(hours=24) <= t < w_end for t, _ in seps)
            storm_start = min((t0_ for t0_, t1, kp in kp_obs if kp >= KP_STORM and t0_ < w_end and t1 > w.start_utc), default=None)
            rows.append({'cutoff_utc': c.isoformat(), 'window_start_utc': w.start_utc.isoformat(), 'offset_h': off,
                         'lines': line, 'noaa_available': noaa_ok, 'truth_storm': storm, 'truth_sep': sep,
                         'truth_storm_start_utc': storm_start.isoformat() if storm_start else None,
                         'event_day': c.strftime('%Y-%m-%d') in EVENT_DAYS, 'reasons': reasons})
        c += timedelta(hours=STEP_H)
    return rows


def score(subset, key):
    h = m = f = n = 0
    for r in subset:
        truth = r['truth_storm'] or r['truth_sep']
        if key == 'noaa' and not r['noaa_available']:
            continue
        flag = r['lines'][key]
        h += flag and truth; m += (not flag) and truth; f += flag and (not truth); n += (not flag) and (not truth)
    return h, m, f, n


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    default = evaluate(VARIANTS[0])
    ev_rows = [r for r in default if r['event_day']]
    ctl_rows = [r for r in default if not r['event_day']]
    L = ['# Эксперимент Т5: прогнозные линии строгого режима, 1 мая — 30 июня 2024', '',
         'Сгенерировано `experiments/forecast_lines.py`; сохранённые расчёты по каждому окну — '
         '`examples/experiments/forecast_lines.json`. Отсечки каждые %d ч, окна по %d мин со сдвигом %s ч; '
         'всего окон %d (событие %d, контроль %d). Условия — тот же `assess_window`, что в приложении, '
         'по записям, опубликованным до отсечки; факт — карточки GST (Kp) и SEP после события, только для проверки.'
         % (STEP_H, DUR_MIN, '/'.join(map(str, OFFSETS_H)), len(default), len(ev_rows), len(ctl_rows)), '',
         '## Что считается', '',
         '- **факт «буря в окне»**: Kp ≥ %g в 3-часовом интервале, пересекающем окно (allKpIndex карточек GST);' % KP_STORM,
         '- **факт «протонное событие»**: SEP DONKI по наблюдательному прибору, начало в [начало окна − 24 ч, конец окна);',
         '- **попадание** — условие есть и факт есть; **пропуск** — условия нет, факт есть; **ложная тревога** — условие есть, факта нет;',
         '- линия `noaa` считается только там, где выпуск 3-day forecast есть в архиве (разрыв 15.05–16.06 исключён из её счёта);',
         '- базовая линия получает Kp по observedTime без проверки публикации — ей дано больше, чем строгому режиму;',
         '- линия `enlil` в основных таблицах — с оценкой Kp из настроек приложения (`[history].enlil_kp_fields = %s`); '
         'другие варианты — в таблице чувствительности ниже.' % '+'.join(CONFIGURED), '',
         '## Буря Гэннон (отсечки 10–12 мая, %d окон)' % len(ev_rows), '',
         '| линия | попадания | пропуски | ложные тревоги | верные отказы |', '|---|---:|---:|---:|---:|']
    for key in LINES:
        L.append('| %s | %d | %d | %d | %d |' % ((key,) + score(ev_rows, key)))
    L += ['', '## Контрольный период (все остальные отсечки, %d окон)' % len(ctl_rows), '',
          '| линия | попадания | пропуски | ложные тревоги | верные отказы |', '|---|---:|---:|---:|---:|']
    for key in LINES:
        L.append('| %s | %d | %d | %d | %d |' % ((key,) + score(ctl_rows, key)))
    first = {}
    for r in default:
        if r['truth_storm'] and r['lines']['system'] and r['truth_storm_start_utc']:
            for key in ('notif', 'enlil', 'noaa'):
                if r['lines'][key]:
                    first.setdefault(key, r['cutoff_utc'])
    L += ['', '## Заблаговременность по буре Гэннон', '',
          'Первая отсечка, на которой линия поставила условие на окно с фактической бурей (начало Kp ≥ 7 — 10.05 15:00 UTC по интервалам GST):', '']
    for key, cut in first.items():
        L.append('- `%s`: отсечка %s' % (key, cut[:16].replace('T', ' ')))
    fa = [r for r in ctl_rows if r['lines']['system'] and not (r['truth_storm'] or r['truth_sep'])]
    days = defaultdict(int)
    for r in fa:
        days[r['cutoff_utc'][:10]] += 1
    L += ['', '## Ложные тревоги системы на контроле по дням', '',
          ', '.join('%s: %d' % kv for kv in sorted(days.items())) or 'нет', '']
    # --- чувствительность ENLIL к выбору оценки Kp ---
    L += ['## Чувствительность линии ENLIL к выбору оценки Kp прогона', '',
          'Условие ставится при «Kp до …» ≥ 7; «Kp до» — максимум из выбранных полей прогона WSA-ENLIL.', '',
          '| поля Kp | событие: попад./проп./ложн. (enlil) | событие: система | контроль: попад./проп./ложн. (enlil) | контроль: система |',
          '|---|---|---|---|---|']
    sens = {}
    for fields in VARIANTS:
        rows = default if fields == VARIANTS[0] else evaluate(fields)
        e_, c_ = [r for r in rows if r['event_day']], [r for r in rows if not r['event_day']]
        he, me, fe, _ = score(e_, 'enlil'); hs, ms, fs, _ = score(e_, 'system')
        hc, mc, fc, _ = score(c_, 'enlil'); hcs, mcs, fcs, _ = score(c_, 'system')
        sens['+'.join(fields)] = {'event_enlil': [he, me, fe], 'event_system': [hs, ms, fs],
                                  'control_enlil': [hc, mc, fc], 'control_system': [hcs, mcs, fcs]}
        L.append('| %s | %d / %d / %d | %d / %d / %d | %d / %d / %d | %d / %d / %d |'
                 % ('+'.join(fields), he, me, fe, hs, ms, fs, hc, mc, fc, hcs, mcs, fcs))
    SH.ENLIL_KP_FIELDS = CONFIGURED
    L += ['', 'Выбор для приложения: `%s` — типичная оценка прогона; верхняя оценка при южном поле (kp_180) '
          'остаётся в тексте условия. Основание — столбцы «контроль»: ложные тревоги линии ENLIL и системы.' % '+'.join(CONFIGURED),
          '', '## Границы', '',
          '- Архива наблюдений GOES за 2024 нет: факт протонного события — по карточкам SEP DONKI с конвенцией 24 ч, не по потоку;',
          '- буря в окне определяется по Kp ≥ 7; бури G1–G2 не считаются событием ни для условий, ни для факта;',
          '- прогнозы ENLIL и NOAA — внешние; линия `system` показывает, что видит пользователь, и не приписывает их заблаговременность команде;',
          '- одно выраженное событие за период: статистика по нему — разбор случая, не оценка вероятностей;',
          '- ложные тревоги после начала бури (условие держится 24 ч после протонного события и на длительность прогона ENLIL) — следствие конвенций, они названы.', '']
    os.makedirs(os.path.join(ROOT, 'examples', 'experiments'), exist_ok=True)
    io.open(os.path.join(ROOT, 'examples', 'experiments', 'forecast_lines.json'), 'w', encoding='utf-8').write(
        json.dumps({'generated_utc': datetime.now(UTC).isoformat(), 'params': {'step_h': STEP_H, 'duration_min': DUR_MIN,
                    'offsets_h': OFFSETS_H, 'kp_storm': KP_STORM, 'event_days': EVENT_DAYS, 'enlil_kp_fields': VARIANTS[0]},
                    'sensitivity': sens, 'rows': default}, ensure_ascii=False, indent=1))
    io.open(os.path.join(ROOT, 'docs', 'EKSPERIMENTY_PROGNOZ.md'), 'w', encoding='utf-8', newline='\n').write('\n'.join(L))
    print('\n'.join(L))


if __name__ == '__main__':
    main()
