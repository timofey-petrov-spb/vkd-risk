# -*- coding: utf-8 -*-
"""Эксперимент Т5: прогнозные линии строгого режима на всём периоде 1 мая — 30 июня 2024.

Запуск: python experiments/forecast_lines.py — пишет docs/EKSPERIMENTY_PROGNOZ.md и
examples/experiments/forecast_lines.json (сохранённые расчёты по каждому окну).
Сетевых запросов нет. Данные — ТОТ ЖЕ адаптер, что у приложения (`vkd.history`, A2):
исходные тела уведомлений DONKI, окончательный ряд Kp GFZ, численный архив наблюдений
GOES 2024 (NASA iSWA) и реестр выпусков NOAA (`vkd.integration.noaa_forecast`).

Вопрос: насколько условия, поставленные ТОЛЬКО по данным, опубликованным до отсечки,
совпадают с тем, что произошло в окне. Отсечки каждые 6 ч; для каждой — четыре окна
по 6 ч со сдвигом 0/6/12/18 ч от отсечки (горизонт 24 ч). Орбита не нужна: условия
по событиям и прогнозам вычисляет тот же assess_window, что и приложение, с пустой трассой.
Пороги — те же настройки, что у приложения (Thresholds.from_settings, config/settings.toml).

Каждая линия считается ПРОТИВ СВОЕЙ ВЕЛИЧИНЫ (договор: бурю и протонное событие не
складывать): линии по буре — против факта «Kp ≥ порога в окне», линия по протонному
событию — против факта «поток GOES ≥10 МэВ ≥ порога в окне».
  По буре:
  * baseline — «последнее наблюдение»: последний окончательный Kp GFZ с концом интервала
    ≤ отсечки (без проверки публикации — базовой линии дано больше, чем строгому режиму) ≥ порога;
  * notif_gst — уведомления DONKI о буре с наблюдённым Kp ≥ порога (facts.kp);
  * cme_arrival — ОПУБЛИКОВАННОЕ уведомление о прогнозе прихода выброса, у которого
    граница диапазона максимума Kp (facts.kp_range_min/max, настройка
    [history].cme_kp_range_bound) ≥ порога;
  * noaa — прогноз NOAA 3-day: Kp ≥ порога в окне (выпуск до отсечки; в разрыве каталога
    15.05–16.06 линии нет — это «нет данных», не «нет условия»);
  * system_storm — объединение notif_gst + cme_arrival + noaa (что видит пользователь по буре).
  По протонному событию:
  * notif_sep — уведомления DONKI о протонном событии;
  * system_sep — то же (других источников по SEP в строгом режиме нет).

Факт (после отсечки, только для проверки, Т4):
  * «буря в окне» — Kp ≥ порога хотя бы в одном 3-часовом интервале окончательного ряда
    GFZ, пересекающем окно;
  * «протонное событие в окне» — численное наблюдение GOES ≥10 МэВ ≥ goes_p10_warning_pfu
    в пятиминутной ячейке, пересекающей окно (архив NASA iSWA, `data/goes_2024`).
    До стыка A2 такого ряда не было и факт брался по карточкам SEP DONKI с конвенцией 24 ч;
    теперь это измеренный поток, и показатели по SEP с прежней редакцией не сравнимы.
Показатели: попадания, пропуски, ложные тревоги по окнам; заблаговременность по буре —
первая отсечка, на которой линия по буре поставила условие на окно с фактической бурей.

Чувствительность: какая граница ОПУБЛИКОВАННОГО диапазона Kp уведомления о приходе
выброса сравнивается с порогом — верхняя (max) или нижняя (min). Прежняя ось
(kp_90/kp_135/kp_180 прогона WSA-ENLIL) снята вместе с самой линией: время размещения
конкретной версии карточки CME не доказано (R10, разбор Codex 19.09).
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

from dataclasses import replace                                                       # noqa: E402
from pathlib import Path                                                              # noqa: E402

from vkd.assess.cutoff import apply_cutoff                                            # noqa: E402
from vkd.assess.trapped import BeltTable                                              # noqa: E402
from vkd.config import section as cfg_section, settings_path                          # noqa: E402
from vkd.history import history_bundle                                                # noqa: E402
from vkd.integration.noaa_forecast import noaa_forecasts                              # noqa: E402
from vkd.sources.goes_archive import (CHANNEL_ID as GOES_CHANNEL,                     # noqa: E402
                                      REGISTRY_PATH as GOES_REGISTRY_PATH,
                                      archive_snapshot as goes_archive_snapshot)
from vkd.sources.registry import SourceRegistry                                       # noqa: E402
from vkd.types import Window                                                          # noqa: E402
from vkd.windows.compare import Thresholds, assess_window                             # noqa: E402

UTC = timezone.utc
PERIOD = (datetime(2024, 5, 1, tzinfo=UTC), datetime(2024, 6, 30, tzinfo=UTC))
STEP_H, DUR_MIN, OFFSETS_H = 6, 360, (0, 6, 12, 18)
TH = Thresholds.from_settings()                              # те же пороги, что в приложении (Т7)
KP_STORM = TH.kp_check
PFU_SEP = TH.goes_p10_warning_pfu
EVENT_DAYS = ('2024-05-10', '2024-05-11', '2024-05-12')     # выраженное событие: буря Гэннон
STORM_LINES = ('baseline', 'notif_gst', 'cme_arrival', 'noaa', 'system_storm')
SEP_LINES = ('notif_sep', 'system_sep')
TRUTH_OF = {**{k: 'truth_storm' for k in STORM_LINES}, **{k: 'truth_sep' for k in SEP_LINES}}
CONFIGURED_BOUND = TH.cme_kp_bound
BOUNDS = (CONFIGURED_BOUND,) + tuple(b for b in ('max', 'min') if b != CONFIGURED_BOUND)


def archive():
    """Уведомления DONKI и окончательный ряд Kp GFZ через адаптер приложения (A2).

    Возвращает (samples, events, facts): facts — структурированные факты записи
    (raw_record_id -> facts), те же, что приложение передаёт в assess_window.
    """
    samples, events, raw = history_bundle()
    meta = raw.get('_history') or {}
    facts = {rid: dict((m.get('content_audit') or {}).get('facts') or {})
             for recs in (meta.get('source_versions') or {}).values() for rid, m in recs.items()}
    return list(samples), list(events), facts


def kp_truth(samples):
    """(t_from, t_to, kp) — окончательный ряд GFZ (факт после события)."""
    return sorted({(s.valid_from_utc, s.valid_to_utc, s.value) for s in samples
                   if s.channel_id == 'kp' and s.source_id == 'gfz_kp_archive'
                   and s.valid_from_utc and s.valid_to_utc and s.value is not None})


def goes_truth():
    """(t_from, t_to, pfu) — численные наблюдения GOES ≥10 МэВ за период (архив NASA iSWA).

    Это измеренный поток, а не пороговое сообщение: факт «протонное событие в окне»
    больше не опирается на конвенцию 24 ч по карточкам DONKI.
    """
    path = Path(ROOT) / GOES_REGISTRY_PATH
    if not path.is_file():
        return []
    snap = goes_archive_snapshot(SourceRegistry(ROOT, GOES_REGISTRY_PATH),
                                 start_utc=PERIOD[0] - timedelta(days=2),
                                 end_utc=PERIOD[1] + timedelta(days=2), mode='history_review')
    return sorted((s.valid_from_utc, s.valid_to_utc, s.value) for s in snap['samples']
                  if s.channel_id == GOES_CHANNEL and s.value is not None)


def evaluate(bound: str, samples, events, facts, kp_obs, goes_obs) -> list[dict]:
    th = replace(TH, cme_kp_bound=bound)
    belts = BeltTable('min')
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
            a = assess_window(w, [], belts, None, None, [], th, c, events=ev, forecasts=forecasts, event_facts=facts)
            reasons = list(a.mechanisms[0].needs_check_reasons)
            line = {'notif_sep': any('протонное событие' in r for r in reasons),
                    'notif_gst': any('уведомление DONKI о буре' in r for r in reasons),
                    'cme_arrival': any('прихода выброса' in r for r in reasons),
                    'noaa': any('прогноз NOAA' in r for r in reasons),
                    'baseline': base_kp is not None and base_kp >= KP_STORM}
            line['system_storm'] = line['notif_gst'] or line['cme_arrival'] or line['noaa']
            line['system_sep'] = line['notif_sep']
            storm = any(kp >= KP_STORM and t0_ < w_end and t1 > w.start_utc for t0_, t1, kp in kp_obs)
            sep = any(v >= PFU_SEP and a0 < w_end and a1 > w.start_utc for a0, a1, v in goes_obs)
            goes_cov = any(a0 < w_end and a1 > w.start_utc for a0, a1, _ in goes_obs)
            storm_start = min((t0_ for t0_, t1, kp in kp_obs if kp >= KP_STORM and t0_ < w_end and t1 > w.start_utc), default=None)
            rows.append({'cutoff_utc': c.isoformat(), 'window_start_utc': w.start_utc.isoformat(), 'offset_h': off,
                         'lines': line, 'noaa_available': noaa_ok, 'truth_storm': storm, 'truth_sep': sep,
                         'goes_truth_available': goes_cov,
                         'truth_storm_start_utc': storm_start.isoformat() if storm_start else None,
                         'event_day': c.strftime('%Y-%m-%d') in EVENT_DAYS, 'reasons': reasons})
        c += timedelta(hours=STEP_H)
    return rows


def score(subset, key):
    """Попадания, пропуски, ложные тревоги, верные отказы линии key против ЕЁ величины (TRUTH_OF)."""
    h = m = f = n = 0
    truth_key = TRUTH_OF[key]
    for r in subset:
        truth = r[truth_key]
        if key == 'noaa' and not r['noaa_available']:
            continue
        if truth_key == 'truth_sep' and not r['goes_truth_available']:
            continue                     # без наблюдений GOES на окно факт неизвестен — окно не считается
        flag = r['lines'][key]
        h += flag and truth
        m += (not flag) and truth
        f += flag and (not truth)
        n += (not flag) and (not truth)
    return h, m, f, n


def _table(L, rows_subset, keys, title):
    L += ['', title, '', '| линия | величина | попадания | пропуски | ложные тревоги | верные отказы |', '|---|---|---:|---:|---:|---:|']
    for key in keys:
        L.append('| %s | %s | %d | %d | %d | %d |' % ((key, 'буря' if TRUTH_OF[key] == 'truth_storm' else 'протонное событие') + score(rows_subset, key)))


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    samples, events, facts = archive()
    kp_obs, goes_obs = kp_truth(samples), goes_truth()
    default = evaluate(BOUNDS[0], samples, events, facts, kp_obs, goes_obs)
    ev_rows = [r for r in default if r['event_day']]
    ctl_rows = [r for r in default if not r['event_day']]
    n_sep_scored = len([r for r in ctl_rows + ev_rows if r['goes_truth_available']])
    L = ['# Эксперимент Т5: прогнозные линии строгого режима, 1 мая — 30 июня 2024', '',
         'Сгенерировано `experiments/forecast_lines.py` (%s); сохранённые расчёты по каждому окну — '
         '`examples/experiments/forecast_lines.json`. Отсечки каждые %d ч, окна по %d мин со сдвигом %s ч; '
         'всего окон %d (событие %d, контроль %d). Условия — тот же `assess_window` и тот же поставщик истории '
         '`vkd.history` (A2), что в приложении, по записям, опубликованным до отсечки; факт — окончательный ряд '
         'Kp GFZ и численные наблюдения GOES ≥10 МэВ после события, только для проверки.'
         % (datetime.now(UTC).strftime('%Y-%m-%d %H:%MZ'), STEP_H, DUR_MIN, '/'.join(map(str, OFFSETS_H)), len(default), len(ev_rows), len(ctl_rows)), '',
         'Настройки: `%s` — пороги приложения `kp_check = %g` (условие и факт «буря»), `goes_p10_warning_pfu = %g` '
         '(условие и факт «протонное событие»), `[history].cme_kp_range_bound = "%s"`, '
         '`[history].event_valid_hours = %g`, `[history].sep_valid_hours = %g`.'
         % (os.path.relpath(settings_path(), ROOT).replace('\\', '/'), KP_STORM, PFU_SEP, CONFIGURED_BOUND,
            TH.event_valid_hours, TH.sep_valid_hours), '',
         '## Что изменилось по сравнению с прежней редакцией', '',
         'Прежняя редакция считалась на временном слое `experiments/stub_history.py` (теперь `experiments/legacy/`). '
         'После стыка A2 изменились и вход, и факт, поэтому числа с ней напрямую не сравнимы:', '',
         '1. События берутся из **исходных тел уведомлений** DONKI, а не из полей поздних карточек;',
         '2. Линия `enlil` снята: время размещения конкретной версии карточки CME не доказано (R10). Её место заняла '
         '`cme_arrival` — ОПУБЛИКОВАННОЕ уведомление с диапазоном максимума Kp; ось чувствительности теперь — какая '
         'граница диапазона сравнивается с порогом, а не какое поле прогона WSA-ENLIL берётся;',
         '3. Факт «протонное событие в окне» — **измеренный поток** GOES ≥10 МэВ из архива NASA iSWA '
         '(`data/goes_2024`), а не карточка SEP с конвенцией «24 ч». Окна, на которые наблюдений GOES нет '
         '(разрывы архива), из счёта по SEP исключены: таких окон %d из %d.'
         % (len(default) - n_sep_scored, len(default)), '',
         'ЭТО ПРОМЕЖУТОЧНАЯ ВЕЛИЧИНА. Показатели по ПРОГНОЗИРУЕМОЙ величине сервиса — вердикту и '
         'выбранному окну — считает второй эксперимент Т5: `docs/EKSPERIMENTY_VERDIKT.md` '
         '(`python experiments/verdicts.py`). Он запускает на каждой отсечке полный расчёт '
         '`app.compute.run` и сравнивает сервис с простым подходом «последнее наблюдение». Факт '
         '(«буря в окне», «протонное событие в окне») у обоих экспериментов один и тот же: тот '
         'эксперимент берёт его теми же функциями этого файла.', '',
         '## Что считается', '',
         '- каждая линия считается против своей величины: линии по буре — против факта «буря в окне», линии по протонному '
         'событию — против факта «протонное событие в окне» (договор: бурю и протонное событие не складывать);',
         '- **факт «буря в окне»**: Kp ≥ %g в 3-часовом интервале окончательного ряда GFZ (`data/gfz_2024`), пересекающем окно;' % KP_STORM,
         '- **факт «протонное событие»**: наблюдённый поток GOES ≥10 МэВ ≥ %g pfu в пятиминутной ячейке, пересекающей окно;' % PFU_SEP,
         '- **попадание** — условие есть и факт есть; **пропуск** — условия нет, факт есть; **ложная тревога** — условие есть, факта нет;',
         '- линия `noaa` считается только там, где выпуск 3-day forecast есть в архиве (разрыв 15.05–16.06 исключён из её счёта);',
         '- базовая линия получает окончательный Kp по концу интервала без проверки публикации — ей дано больше, чем строгому режиму;',
         '- линия `cme_arrival` в основных таблицах — с границей диапазона из настроек приложения '
         '(`[history].cme_kp_range_bound = "%s"`); вторая граница — в таблице чувствительности ниже;' % CONFIGURED_BOUND,
         '- карточки событий DONKI в строгом режиме не используются (CONTRACT §10): условия — только из датированных уведомлений.']
    _table(L, ev_rows, STORM_LINES + SEP_LINES, '## Буря Гэннон (отсечки 10–12 мая, %d окон)' % len(ev_rows))
    _table(L, ctl_rows, STORM_LINES + SEP_LINES, '## Контрольный период (все остальные отсечки, %d окон)' % len(ctl_rows))
    first = {}
    for r in default:
        if r['truth_storm'] and r['truth_storm_start_utc']:
            for key in ('notif_gst', 'cme_arrival', 'noaa'):
                if r['lines'][key]:
                    first.setdefault(key, (r['cutoff_utc'], r['window_start_utc']))
    L += ['', '## Заблаговременность по буре Гэннон', '',
          'Первая отсечка, на которой линия ПО БУРЕ поставила условие на окно с фактической бурей '
          '(начало Kp ≥ %g — 10.05 15:00 UTC по интервалам GFZ). Условие по протонному событию здесь не считается: '
          'это другой механизм.' % KP_STORM, '']
    for key, (cut, ws) in first.items():
        L.append('- `%s`: отсечка %s, окно с %s' % (key, cut[:16].replace('T', ' '), ws[:16].replace('T', ' ')))
    if not first:
        L.append('- ни одна линия по буре не поставила условие на окно с бурей')
    fa = [r for r in ctl_rows if r['lines']['system_storm'] and not r['truth_storm']]
    days = defaultdict(int)
    for r in fa:
        days[r['cutoff_utc'][:10]] += 1
    L += ['', '## Ложные тревоги системы по буре на контроле по дням', '',
          ', '.join('%s: %d' % kv for kv in sorted(days.items())) or 'нет', '']
    fa_sep = [r for r in ctl_rows if r['lines']['system_sep'] and not r['truth_sep'] and r['goes_truth_available']]
    days_sep = defaultdict(int)
    for r in fa_sep:
        days_sep[r['cutoff_utc'][:10]] += 1
    L += ['## Ложные тревоги по протонному событию на контроле по дням', '',
          ', '.join('%s: %d' % kv for kv in sorted(days_sep.items())) or 'нет', '']
    # --- чувствительность к выбору границы опубликованного диапазона Kp ---
    L += ['## Чувствительность линии прихода выброса к границе опубликованного диапазона Kp', '',
          'Уведомление публикует диапазон («expected range of the maximum Kp index is 6-8»). Условие ставится, если '
          'выбранная граница ≥ %g. Обе границы опубликованы источником: это не подбор параметра модели, а решение, '
          'считать ли по верхней (консервативно) или по нижней. Все столбцы — против факта «буря».' % KP_STORM, '',
          '| граница | событие: попад./проп./ложн. (cme_arrival) | событие: система по буре | контроль: попад./проп./ложн. (cme_arrival) | контроль: система по буре |',
          '|---|---|---|---|---|']
    sens = {}
    for bound in BOUNDS:
        rows = default if bound == BOUNDS[0] else evaluate(bound, samples, events, facts, kp_obs, goes_obs)
        e_, c_ = [r for r in rows if r['event_day']], [r for r in rows if not r['event_day']]
        he, me, fe, _ = score(e_, 'cme_arrival')
        hs, ms, fs, _ = score(e_, 'system_storm')
        hc, mc, fc, _ = score(c_, 'cme_arrival')
        hcs, mcs, fcs, _ = score(c_, 'system_storm')
        sens[bound] = {'event_cme_arrival': [he, me, fe], 'event_system_storm': [hs, ms, fs],
                       'control_cme_arrival': [hc, mc, fc], 'control_system_storm': [hcs, mcs, fcs]}
        L.append('| %s | %d / %d / %d | %d / %d / %d | %d / %d / %d | %d / %d / %d |'
                 % (bound, he, me, fe, hs, ms, fs, hc, mc, fc, hcs, mcs, fcs))
    L += ['', 'Выбор для приложения: `%s` — верхняя граница опубликованного диапазона: пропустить бурю дороже, чем '
          'проверить окно лишний раз. Обоснование числами — столбцы «контроль».' % CONFIGURED_BOUND,
          '', '## Границы', '',
          '- буря в окне определяется по Kp ≥ %g; бури ниже порога не считаются событием ни для условий, ни для факта;' % KP_STORM,
          '- архив наблюдений GOES 2024 не имеет доказанной исторической публикации: он годится как ФАКТ после события '
          '(здесь) и как наблюдение в разборе, но в строгом прогнозе исключён — поэтому линия по протонному событию '
          'строится только по уведомлениям;',
          '- прогнозы NOAA и уведомления о приходе выброса — внешние; линии `system_*` показывают, что видит пользователь, '
          'и не приписывают их заблаговременность команде;',
          '- линия `notif_gst` даёт нули: уведомление о буре сообщает НАБЛЮДЁННЫЙ Kp за прошедший 3-часовой '
          'интервал, и его конец ОБЪЯВЛЕН, поэтому на будущее окно оно не распространяется (конвенция 24 ч '
          'к нему не применяется). Это не дефект линии, а её семантика: заблаговременность по буре дают '
          'прогнозные линии, а свежее наблюдение Kp входит в расчёт отдельным фактором, не событием;',
          '- одно выраженное событие за период: статистика по нему — разбор случая, не оценка вероятностей;',
          '- ложные тревоги после начала события (условие держится %g ч после протонного события и %g ч после прогноза '
          'прихода выброса) — следствие конвенций `[history]`, они названы.' % (TH.sep_valid_hours, TH.event_valid_hours), '']
    os.makedirs(os.path.join(ROOT, 'examples', 'experiments'), exist_ok=True)
    io.open(os.path.join(ROOT, 'examples', 'experiments', 'forecast_lines.json'), 'w', encoding='utf-8').write(
        json.dumps({'generated_utc': datetime.now(UTC).isoformat(), 'settings_path': os.path.relpath(settings_path(), ROOT).replace('\\', '/'),
                    'provider': 'vkd.history (A2)', 'algorithm_note': 'линия enlil снята по R10; факт SEP — численный архив GOES 2024',
                    'params': {'step_h': STEP_H, 'duration_min': DUR_MIN, 'offsets_h': OFFSETS_H, 'kp_storm': KP_STORM,
                               'pfu_sep': PFU_SEP, 'event_days': EVENT_DAYS, 'cme_kp_range_bound': CONFIGURED_BOUND,
                               'history_settings': cfg_section('history'),
                               'thresholds': TH.__dict__, 'truth_of': TRUTH_OF},
                    'scores': {'event': {k: score(ev_rows, k) for k in STORM_LINES + SEP_LINES},
                               'control': {k: score(ctl_rows, k) for k in STORM_LINES + SEP_LINES}},
                    'sensitivity': sens, 'rows': default}, ensure_ascii=False, indent=1))
    io.open(os.path.join(ROOT, 'docs', 'EKSPERIMENTY_PROGNOZ.md'), 'w', encoding='utf-8', newline='\n').write('\n'.join(L))
    print('\n'.join(L))


if __name__ == '__main__':
    main()
