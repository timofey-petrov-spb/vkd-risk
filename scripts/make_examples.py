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
from experiments.stub_history import gfz_archive_kp_samples, sep_events   # noqa: E402
from experiments.stub_sources import fetch_none                            # noqa: E402
from vkd.config import settings_path                      # noqa: E402
from vkd.windows.compare import Thresholds                # noqa: E402
from vkd.windows.scenario import Scenario                 # noqa: E402

OUT = os.path.join(_ROOT, 'examples')
UTC = timezone.utc


def utc(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=UTC)


CASES = [
    # имя, режим, t0, длительность, период поиска, сдвиги окон, сценарий
    ('live_now', 'live', None, 360, 720, [0, 240], None),
    ('gannon_2024-05-10_cutoff12Z', 'history_forecast', utc(2024, 5, 10, 12), 360, 720, [0, 240], None),
    ('gannon_2024-05-10_cutoff19Z', 'history_forecast', utc(2024, 5, 10, 19), 360, 720, [0, 240], None),
    ('quiet_2024-05-03_12Z', 'history_forecast', utc(2024, 5, 3, 12), 360, 1440, [0, 480], None),
    ('gap_2024-05-20_12Z', 'history_forecast', utc(2024, 5, 20, 12), 360, 1440, [0, 480], None),
    ('end_2024-06-25_12Z', 'history_forecast', utc(2024, 6, 25, 12), 360, 1440, [0, 480], None),
    ('review_2024-05-10', 'history_review', utc(2024, 5, 10, 12), 360, 720, [0, 240], None),
    ('whatif_sep_now', 'live', None, 360, 720, [0, 240], Scenario('example', sep_onset_offset_min=120, sep_level_pfu=100.0)),
    ('whatif_delay_90min', 'live', None, 360, 720, [0, 240], Scenario('example', work_delay_min=90)),
]
T5_EVENT = 'gannon_2024-05-10_cutoff12Z'
T5_CONTROL = ('quiet_2024-05-03_12Z', 'end_2024-06-25_12Z')


def fact_after(t0: datetime, horizon_min: int, kp_check: float) -> str:
    """Факт после отсечки по архиву — только для проверки (Т4): максимум окончательного Kp GFZ на
    горизонте и протонные события DONKI по наблюдательным приборам с началом на горизонте."""
    end = t0 + timedelta(minutes=horizon_min)
    ks, _ = gfz_archive_kp_samples()
    in_h = [s for s in ks if s.valid_from_utc < end and s.valid_to_utc > t0]
    seps = [e for e in sep_events()[0] if e.kind.value == 'observation' and e.start_utc and t0 <= e.start_utc < end]
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
    sep_txt = ('протонных событий: %d (первое %s UTC)' % (len(seps), min(e.start_utc for e in seps).strftime('%d.%m %H:%M'))
               if seps else 'протонных событий не было')
    return kp_txt + '; ' + sep_txt


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
    for name, mode, t0, dur, search, offs, sc in CASES:
        t0 = t0 or now
        # исторические режимы: живые источники не запрашиваются (Т6) — входы только из архива, статус так и записан
        r = run(mode, t0, dur, search, offs, scenario=sc, now=now, fetched=(None if mode == 'live' else fetch_none()))
        io.open(os.path.join(OUT, name + '.json'), 'w', encoding='utf-8').write(json.dumps(r.S, ensure_ascii=False, indent=1, default=str))
        open(os.path.join(OUT, name + '.zip'), 'wb').write(build_zip(r.S, r.raw_records))
        rec = r.S['recommendation']
        n_kp_excl = sum(1 for x in r.excluded if x.startswith('gfz_kp_archive#') or x.startswith('donki_gst#'))
        index.append({'name': name, 'mode': r.S['mode'], 'mode_id': mode, 't0_utc': t0.isoformat(), 'verdict': rec['verdict'],
                      'rule': rec['rule'], 'conditions': short_conditions(r.S),
                      'excluded_by_cutoff': len(r.excluded), 'excluded_kp': n_kp_excl, 'events_used': len(r.events),
                      'fact': fact_after(t0, search + dur, th.kp_check) if mode != 'live' else '—',
                      'is_simulated': r.S['is_simulated']})
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
    pair.append('Строгий режим использует только записи, опубликованные до отсечки; факт взят из окончательного ряда Kp GFZ и карточек SEP DONKI '
                'после события и ни в один расчёт не входит.' + t5)
    head = ['# Сохранённые примеры расчётов', '',
            'Созданы `scripts/make_examples.py` тем же конвейером, что и интерфейс (`app.compute.run`); %s UTC; версия алгоритма `%s`; '
            'коммит кода `%s`; настройки `%s` (kp_check = %g, goes_p10_warning_pfu = %g). Каждый пример: `<имя>.json` — снимок; '
            '`<имя>.zip` — отчёт, запрос, факторы, рекомендация, карточки, источники, манифест (`git_commit`, `algorithm_version`), '
            'сырые записи `raw/*.json`. Повтор: `python scripts/replay_example.py examples/<имя>.zip` (код возврата 0 — воспроизведено, '
            '3 — воспроизведено другой версией кода, 1 — расхождение, 2 — повтор невозможен).'
            % (now.strftime('%Y-%m-%d %H:%M'), ALGO_VERSION, sha or 'вне репозитория', os.path.relpath(settings_path(), _ROOT).replace('\\', '/'),
               th.kp_check, th.goes_p10_warning_pfu), '']
    cols = ['пример', 'режим', 't0 (отсечка)', 'вердикт', 'правило', 'условия по окнам', 'исключено отсечкой (из них интервалов Kp)',
            'событий', 'факт после отсечки (проверка)', 'сценарий']
    tbl = ['| ' + ' | '.join(cols) + ' |', '|' + '---|' * len(cols)]
    for x in index:
        tbl.append('| %s | %s | %s | %s | %s | %s | %s | %d | %s | %s |' % (
            x['name'], x['mode'], x['t0_utc'][:16].replace('T', ' '), x['verdict'], x['rule'], x['conditions'],
            ('%d (%d)' % (x['excluded_by_cutoff'], x['excluded_kp'])) if x['mode_id'] != 'live' else '—',
            x['events_used'], x['fact'], 'да' if x['is_simulated'] else ''))
    notes = ['', '## Что означают столбцы', '',
             '- «условия по окнам» — заголовки условий проверки из панели вердикта (полный текст с идентификаторами записей — в `cards.json` и `factors.json` архива);',
             '- «исключено отсечкой» — записи, отброшенные строгим отбором по времени публикации (`manifest.json` → `excluded_by_cutoff`, каждая с причиной); '
             'в скобках — интервалы Kp окончательного ряда GFZ и карточек GST, у которых нет времени публикации по интервалам (только разбор после факта);',
             '- «факт после отсечки» — из архива после события, для проверки прогноза (Т4/Т5); в расчёт не входит;',
             '- «Текущая обстановка» — живые источники на момент генерации; их сырые записи (GOES, Kp, TLE с адресом и временем получения) лежат в `raw/`, '
             'и повтор по ZIP их использует вместо живых запросов.', '']
    io.open(os.path.join(OUT, 'INDEX.md'), 'w', encoding='utf-8', newline='\n').write('\n'.join(head + pair + [''] + ['## Все примеры', ''] + tbl + notes))
    print('индекс: examples/INDEX.md')


if __name__ == '__main__':
    main()
