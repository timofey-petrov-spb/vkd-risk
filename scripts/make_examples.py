# -*- coding: utf-8 -*-
"""Сохранённые примеры расчётов для сдачи (постановка: «примеры сохранённых
расчётов»; критерий Т8). Запуск: python scripts/make_examples.py

Пишет в examples/: <имя>.json (снимок) и <имя>.zip (отчёт, запрос, факторы,
рекомендация, карточки, источники, манифест, сырые записи). Сведения в архиве
совпадают с интерфейсом, потому что строятся тем же app.compute.run.
"""
from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from app.compute import run                      # noqa: E402
from app.export import build_zip                 # noqa: E402
from vkd.windows.scenario import Scenario        # noqa: E402

OUT = os.path.join(_ROOT, 'examples')


def utc(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


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


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    os.makedirs(OUT, exist_ok=True)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    index = []
    for name, mode, t0, dur, search, offs, sc in CASES:
        t0 = t0 or now
        r = run(mode, t0, dur, search, offs, scenario=sc, now=now)
        io.open(os.path.join(OUT, name + '.json'), 'w', encoding='utf-8').write(json.dumps(r.S, ensure_ascii=False, indent=1, default=str))
        open(os.path.join(OUT, name + '.zip'), 'wb').write(build_zip(r.S, r.raw_records))
        rec = r.S['recommendation']
        index.append({'name': name, 'mode': r.S['mode'], 't0_utc': t0.isoformat(), 'verdict': rec['verdict'],
                      'rule': rec['rule'], 'excluded_by_cutoff': len(r.excluded), 'events_used': len(r.events),
                      'is_simulated': r.S['is_simulated']})
        print('%-32s %-22s вердикт %-15s исключено %3d событий %2d' % (name, r.S['mode'], rec['verdict'], len(r.excluded), len(r.events)))
    io.open(os.path.join(OUT, 'INDEX.md'), 'w', encoding='utf-8').write(
        '# Сохранённые примеры расчётов\n\nСозданы `scripts/make_examples.py` тем же конвейером, что и интерфейс. '
        'Каждый пример: `<имя>.json` — снимок; `<имя>.zip` — отчёт, запрос, факторы, рекомендация, карточки, '
        'источники, манифест, сырые записи.\n\n| пример | режим | t0 | вердикт | правило | исключено отсечкой | событий | сценарий |\n|---|---|---|---|---|---|---|---|\n' +
        '\n'.join('| %s | %s | %s | %s | %s | %d | %d | %s |' % (x['name'], x['mode'], x['t0_utc'], x['verdict'], x['rule'],
                                                             x['excluded_by_cutoff'], x['events_used'], 'да' if x['is_simulated'] else '') for x in index) + '\n')
    print('индекс: examples/INDEX.md')


if __name__ == '__main__':
    main()
