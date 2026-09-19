# -*- coding: utf-8 -*-
"""Сохранённые примеры расчётов для сдачи (постановка: «примеры сохранённых
расчётов»; критерий Т8). Запуск: python scripts/make_examples.py

Пишет в examples/: <имя>.json (снимок) и <имя>.zip (отчёт, запрос, факторы,
рекомендация, карточки, источники, манифест, сырые записи). Сведения в архиве
совпадают с интерфейсом, потому что строятся тем же app.compute.run.

INDEX.md содержит SHA коммита и версию алгоритма на момент генерации, короткие
условия по окнам и — для исторических примеров — факт после отсечки по архиву
(максимум окончательного Kp GFZ и протонные события на горизонте): пара
«событие против контроля» для Т5 названа явно, а не угадывается по именам файлов.
Повтор: python scripts/replay_example.py examples/<имя>.zip
"""
from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from app.compute import ALGO_VERSION, run                 # noqa: E402
from app.export import _git_sha, build_zip                # noqa: E402
from app.compute import GOES_CHANNEL                      # noqa: E402
from vkd.config import settings_path                      # noqa: E402
from vkd.history import history_bundle                    # noqa: E402
from vkd.integration.replay_live import fetch_none        # noqa: E402
from vkd.types import Request                             # noqa: E402
from vkd.windows.compare import Thresholds                # noqa: E402
from vkd.windows.scenario import Scenario                 # noqa: E402

OUT = os.path.join(_ROOT, 'examples')
UTC = timezone.utc


def utc(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=UTC)


CASES = [
    # имя, режим, t0, длительность, период поиска, сдвиги окон, сценарий, исключённые источники
    ('live_now', 'live', None, 360, 720, [0, 240], None, None),
    ('gannon_2024-05-10_cutoff12Z', 'history_forecast', utc(2024, 5, 10, 12), 360, 720, [0, 240], None, None),
    ('gannon_2024-05-10_cutoff19Z', 'history_forecast', utc(2024, 5, 10, 19), 360, 720, [0, 240], None, None),
    ('quiet_2024-05-03_12Z', 'history_forecast', utc(2024, 5, 3, 12), 360, 1440, [0, 480], None, None),
    ('gap_2024-05-20_12Z', 'history_forecast', utc(2024, 5, 20, 12), 360, 1440, [0, 480], None, None),
    ('end_2024-06-25_12Z', 'history_forecast', utc(2024, 6, 25, 12), 360, 1440, [0, 480], None, None),
    # Исход «равнозначны» на настоящих данных (О3 требует показать все исходы, а не только отказ).
    # Дата не подобрана под ответ: перебор всего архива 01.05–30.06 с шагом 6 ч даёт 80 случаев
    # «равнозначны» из 496; взят первый спокойный, без условий проверки у обоих окон.
    ('equal_2024-05-04_12Z', 'history_forecast', utc(2024, 5, 4, 12), 360, 1440, [0, 480], None, None),
    ('review_2024-05-10', 'history_review', utc(2024, 5, 10, 12), 360, 720, [0, 240], None, None),
    ('whatif_sep_now', 'live', None, 360, 720, [0, 240], Scenario('example', sep_onset_offset_min=120, sep_level_pfu=100.0), None),
    ('whatif_delay_90min', 'live', None, 360, 720, [0, 240], Scenario('example', work_delay_min=90), None),
    # Исход «отказ» на настоящих данных. До круга 11 его давали три примера текущего режима, но
    # давали ПО ПОСТРОЕНИЮ — окно позже часа от последнего измерения GOES в горизонт наблюдения не
    # попадало; правило R14 этот структурный отказ убрало, и показывать отказ на сдаче стало нечем.
    # Здесь отказ настоящий и не зависит от дня: пользователь ИСКЛЮЧИЛ источник протонных событий,
    # наблюдения нет вовсе, значит нет и общего канала (R14 требует полученного наблюдения) —
    # обязательная линия пуста, вердикт insufficient. Дата не подбиралась: случай не про дату.
    ('refusal_goes_off', 'live', None, 360, 720, [0, 240], None, {'goes': 'off'}),
]
SOURCE_OFF_RU = {'goes': 'наблюдение протонов GOES', 'kp': 'наблюдение Kp', 'noaa': 'прогноз NOAA'}
T5_EVENT = 'gannon_2024-05-10_cutoff12Z'
T5_CONTROL = ('quiet_2024-05-03_12Z', 'end_2024-06-25_12Z')


def fact_after(t0: datetime, duration_min: int, search_min: int, kp_check: float, pfu_warn: float) -> str:
    """Факт после отсечки по архиву — только для проверки (Т4).

    Берётся ТОТ ЖЕ поставщик, что у приложения (vkd.history в режиме разбора):
    окончательный ряд Kp GFZ и численные наблюдения GOES ≥10 МэВ (NASA iSWA).
    Это измеренные величины, а не пороговые сообщения; в расчёт они не входят.
    """
    end = t0 + timedelta(minutes=duration_min + search_min)
    samples, events, _ = history_bundle(Request('history_review', t0, int(duration_min), int(search_min), None))
    in_h = [s for s in samples if s.channel_id == 'kp' and s.source_id == 'gfz_kp_archive'
            and s.valid_from_utc and s.valid_to_utc and s.valid_from_utc < end and s.valid_to_utc > t0]
    goes = [s for s in samples if s.channel_id == GOES_CHANNEL and s.value is not None and t0 <= s.t_utc < end]
    seps = [e for e in events if e.kind_of_event == 'SEP' and e.start_utc and t0 <= e.start_utc < end]
    if not in_h:
        return 'ряда Kp GFZ за период нет'
    mx = max(in_h, key=lambda s: s.value)
    above = [s for s in in_h if s.value >= kp_check]
    kp_txt = 'Kp макс. %.2f (%s–%s UTC, GFZ %s)' % (mx.value, mx.valid_from_utc.strftime('%d.%m %H:%M'), mx.valid_to_utc.strftime('%H:%M'),
                                                     'окончательный' if mx.quality == 'final' else 'предварительный')
    if above:
        kp_txt += ', Kp ≥ %g с %s UTC' % (kp_check, min(above, key=lambda s: s.valid_from_utc).valid_from_utc.strftime('%d.%m %H:%M'))
    else:
        kp_txt += ', бури Kp ≥ %g не было' % kp_check
    if goes:
        g = max(goes, key=lambda s: s.value)
        over = [s for s in goes if s.value >= pfu_warn]
        goes_txt = 'поток GOES ≥10 МэВ макс. %.6g pfu (%s UTC)' % (g.value, g.t_utc.strftime('%d.%m %H:%M'))
        goes_txt += (', ≥%g pfu с %s UTC' % (pfu_warn, min(over, key=lambda s: s.t_utc).t_utc.strftime('%d.%m %H:%M'))
                     if over else ', порога %g pfu не достигал' % pfu_warn)
    else:
        goes_txt = 'наблюдений GOES на горизонте в архиве нет'
    sep_txt = ('уведомлений о протонном событии: %d (первое %s UTC)' % (len(seps), min(e.start_utc for e in seps).strftime('%d.%m %H:%M'))
               if seps else 'уведомлений о протонном событии не было')
    return kp_txt + '; ' + goes_txt + '; ' + sep_txt


def short_conditions(S: dict) -> str:
    """Короткие причины условий по окнам: заголовок до «: », без идентификаторов записей."""
    parts = []
    for i, w in enumerate(S['windows']):
        reasons = [r for m in w['mechanisms'] for r in m['needs_check']]
        heads = []
        for r in reasons:
            h = r.split(': ')[0]
            if h not in heads:
                heads.append(h)
        parts.append('окно %d: %s' % (i + 1, '; '.join(heads) if heads else 'условий нет'))
    return ' / '.join(parts)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    os.makedirs(OUT, exist_ok=True)
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    th = Thresholds.from_settings()
    sha = _git_sha()
    index = []
    for name, mode, t0, dur, search, offs, sc, off_sources in CASES:
        t0 = t0 or now
        # исторические режимы: живые источники не запрашиваются (Т6) — входы только из архива, статус так и записан
        # disabled — исключённые пользователем источники (Т6, состояние 'off'); без проброса
        # сохранить пример с настоящим отказом было нечем
        r = run(mode, t0, dur, search, offs, disabled=off_sources, scenario=sc, now=now,
                fetched=(None if mode == 'live' else fetch_none()))
        # JSON-снимок несёт коммит кода, как и манифест ZIP: повтор по JSON тоже может доказать версию (Т8)
        io.open(os.path.join(OUT, name + '.json'), 'w', encoding='utf-8').write(json.dumps({**r.S, 'git_commit': sha}, ensure_ascii=False, indent=1, default=str))
        open(os.path.join(OUT, name + '.zip'), 'wb').write(build_zip(r.S, r.raw_records))
        rec = r.S['recommendation']
        n_kp_excl = sum(1 for x in r.excluded if x.startswith('gfz_kp_archive'))
        index.append({'name': name, 'mode': r.S['mode'], 'mode_id': mode, 't0_utc': t0.isoformat(), 'verdict': rec['verdict'],
                      'rule': rec['rule'], 'conditions': short_conditions(r.S),
                      'excluded_by_cutoff': len(r.excluded), 'excluded_kp': n_kp_excl, 'events_used': len(r.events),
                      'fact': fact_after(t0, dur, search, th.kp_check, th.goes_p10_warning_pfu) if mode != 'live' else '—',
                      'is_simulated': r.S['is_simulated'],
                      # исключённые пользователем источники называются в индексе: иначе отказ выглядел бы
                      # свойством дня, а он свойство запроса
                      'disabled_ru': ', '.join(sorted(SOURCE_OFF_RU.get(k, k) for k, v in (off_sources or {}).items() if v == 'off'))})
        print('%-30s %-22s вердикт %-15s исключено %3d событий %2d | %s' % (name, r.S['mode'], rec['verdict'], len(r.excluded), len(r.events),
                                                                              index[-1]['conditions'][:90]))
    by = {x['name']: x for x in index}
    fl_path = os.path.join(OUT, 'experiments', 'forecast_lines.json')
    t5 = ''
    if os.path.exists(fl_path):
        try:
            sc_ = json.load(io.open(fl_path, encoding='utf-8')).get('scores') or {}
            ev, ct = sc_.get('event', {}), sc_.get('control', {})
            if 'system_storm' in ev:
                t5 = (' По всем отсечкам мая–июня (`docs/EKSPERIMENTY_PROGNOZ.md`): по буре система даёт %d попаданий / %d пропусков / %d ложных '
                      'тревог на событии и %d / %d / %d на контроле; по протонному событию — %d / %d / %d и %d / %d / %d.'
                      % (tuple(ev['system_storm'][:3]) + tuple(ct['system_storm'][:3]) + tuple(ev['system_sep'][:3]) + tuple(ct['system_sep'][:3])))
        except (ValueError, KeyError, TypeError):
            t5 = ''
    e = by[T5_EVENT]
    pair = ['## Пара для Т5: событие против контроля', '',
            '- **Событие** — `%s`: отсечка %s, вердикт `%s` (%s). Условия: %s. Факт после отсечки (только проверка, Т4): %s.'
            % (T5_EVENT, e['t0_utc'][:16].replace('T', ' '), e['verdict'], e['rule'], e['conditions'], e['fact'])]
    for c in T5_CONTROL:
        x = by[c]
        pair.append('- **Контроль** — `%s`: отсечка %s, вердикт `%s` (%s). Условия: %s. Факт после отсечки: %s.'
                    % (c, x['t0_utc'][:16].replace('T', ' '), x['verdict'], x['rule'], x['conditions'], x['fact']))
    pair.append('')
    pair.append('Строгий режим использует только записи, опубликованные до отсечки; факт взят из окончательного ряда Kp GFZ '
                'и численного архива наблюдений GOES ≥10 МэВ (NASA iSWA) после события и ни в один расчёт не входит.' + t5)
    head = ['# Сохранённые примеры расчётов', '',
            'Созданы `scripts/make_examples.py` тем же конвейером, что и интерфейс (`app.compute.run`); %s UTC; версия алгоритма `%s`; '
            'коммит кода `%s`; настройки `%s` (kp_check = %g, goes_p10_warning_pfu = %g). Каждый пример: `<имя>.json` — снимок; '
            '`<имя>.zip` — отчёт, запрос, факторы, рекомендация, карточки, источники, манифест (`git_commit`, `algorithm_version`), '
            'сырые записи `raw/*.json`. Повтор: `python scripts/replay_example.py examples/<имя>.zip` (код возврата 0 — воспроизведено, '
            '3 — воспроизведено другой версией кода, 1 — расхождение, 2 — повтор невозможен).'
            % (now.strftime('%Y-%m-%d %H:%M'), ALGO_VERSION, sha or 'вне репозитория', os.path.relpath(settings_path(), _ROOT).replace('\\', '/'),
               th.kp_check, th.goes_p10_warning_pfu), '']
    cols = ['пример', 'режим', 't0 (отсечка)', 'вердикт', 'правило', 'условия по окнам', 'исключено отсечкой (из них интервалов Kp)',
            'событий', 'факт после отсечки (проверка)', 'сценарий', 'источники, исключённые пользователем']
    tbl = ['| ' + ' | '.join(cols) + ' |', '|' + '---|' * len(cols)]
    for x in index:
        tbl.append('| %s | %s | %s | %s | %s | %s | %s | %d | %s | %s | %s |' % (
            x['name'], x['mode'], x['t0_utc'][:16].replace('T', ' '), x['verdict'], x['rule'], x['conditions'],
            ('%d (%d)' % (x['excluded_by_cutoff'], x['excluded_kp'])) if x['mode_id'] != 'live' else '—',
            x['events_used'], x['fact'], 'да' if x['is_simulated'] else '', x['disabled_ru'] or '—'))
    notes = ['', '## Что означают столбцы', '',
             '- «условия по окнам» — заголовки условий проверки из панели вердикта (полный текст с идентификаторами записей — в `cards.json` и `factors.json` архива);',
             '- «исключено отсечкой» — записи, отброшенные строгим отбором по времени публикации (`manifest.json` → `excluded_by_cutoff`, каждая с причиной); '
             'в скобках — записи окончательного ряда Kp GFZ, у которых нет собственного времени публикации по интервалам (только разбор после факта);',
             '- «факт после отсечки» — из архива после события, для проверки прогноза (Т4/Т5); в расчёт не входит;',
             '- «Текущая обстановка» — живые источники на момент генерации; их сырые записи (GOES, Kp, TLE с адресом и временем получения) лежат в `raw/`, '
             'и повтор по ZIP их использует вместо живых запросов;',
             '- «источники, исключённые пользователем» — состояние `off` запроса (Т6): источник не опрашивается и данных от него нет. '
             'Именно так получен единственный пример с вердиктом `insufficient`: без наблюдения протонов обязательная линия пуста, '
             'канал общим не объявляется (правило требует полученного наблюдения), и рекомендации нет — отказ по запросу, а не по дате.', '']
    io.open(os.path.join(OUT, 'INDEX.md'), 'w', encoding='utf-8', newline='\n').write('\n'.join(head + pair + [''] + ['## Все примеры', ''] + tbl + notes))
    print('индекс: examples/INDEX.md')


if __name__ == '__main__':
    main()
