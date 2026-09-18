# -*- coding: utf-8 -*-
"""Воспроизведение сохранённого расчёта (Т8): из request.json архива или JSON-снимка
повторно запускается конвейер и сравнивается вердикт.

    python scripts/replay_example.py examples/gannon_2024-05-10_cutoff19Z.json
    python scripts/replay_example.py examples/gannon_2024-05-10_cutoff19Z.zip

Для исторических режимов результат обязан совпасть: входы — архив в репозитории
и отсечка. Для текущего режима совпадение не гарантируется — живые данные
изменились; скрипт печатает обе версии и разницу.
"""
from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from app.compute import run                      # noqa: E402
from vkd.windows.compare import Thresholds       # noqa: E402
from vkd.windows.scenario import Scenario        # noqa: E402


def load_snapshot(path: str) -> dict:
    if path.endswith('.zip'):
        with zipfile.ZipFile(path) as z:
            req = json.loads(z.read('request.json').decode('utf-8'))
            rec = json.loads(z.read('recommendation.json').decode('utf-8'))
            man = json.loads(z.read('manifest.json').decode('utf-8'))
            meta = json.loads(z.read('trajectory_meta.json').decode('utf-8'))
            tle = None
            if 'raw/iss.tle.json' in z.namelist():
                tle = json.loads(z.read('raw/iss.tle.json').decode('utf-8')).get('text')
        return {'request': req, 'recommendation': rec, 'mode_id': man.get('mode'), 'algorithm_version': man.get('algorithm_version'),
                'trajectory_meta': {**meta, 'tle_text': meta.get('tle_text') or tle}}
    return json.load(io.open(path, encoding='utf-8'))


def pin_tle(S: dict) -> str | None:
    """Сохранённый TLE → временный файл; без него исторический повтор зависит от текущего TLE."""
    txt = (S.get('trajectory_meta') or {}).get('tle_text')
    if not txt:
        return None
    p = os.path.join(_ROOT, 'data', 'cache', 'replay_iss.tle')
    os.makedirs(os.path.dirname(p), exist_ok=True)
    io.open(p, 'w', encoding='utf-8').write(txt)
    return p


def main(path: str):
    sys.stdout.reconfigure(encoding='utf-8')
    S = load_snapshot(path)
    req = S['request']
    mode = S.get('mode_id') or {'Текущая обстановка': 'live', 'Исторический разбор': 'history_review', 'Прогноз из прошлого': 'history_forecast'}[S['mode']]
    t0 = datetime.fromisoformat(req['t0_utc'])
    th_d = dict(req['thresholds'])
    th = Thresholds(**{k: v for k, v in th_d.items() if k in Thresholds.__dataclass_fields__})
    sc = Scenario(**req['scenario']) if req.get('scenario') else None
    tle_path = pin_tle(S)
    r = run(mode, t0, req['duration_min'], req['search_min'], req['window_offsets_min'], disabled=req['disabled'],
            thresholds=th, scenario=sc, T_months=req.get('T_months', 6), tle_override_path=tle_path)
    old, new = S['recommendation'], r.S['recommendation']
    print('файл           :', path)
    print('TLE            :', 'из сохранённого расчёта' if tle_path else 'НЕ СОХРАНЁН — орбита по текущему TLE, совпадение не гарантируется')
    print('режим          :', mode, '| t0', t0.isoformat(), '| версия алгоритма сохранённая/текущая:', S.get('algorithm_version'), '/', r.S['algorithm_version'])
    print('вердикт был    :', old['verdict'], '|', old['rule'])
    print('вердикт теперь :', new['verdict'], '|', new['rule'])
    same = old['verdict'] == new['verdict'] and old.get('preferred') == new.get('preferred')
    if mode == 'live':
        print('РЕЗУЛЬТАТ      : текущий режим — совпадение не гарантируется (живые данные); совпало: %s' % same)
    else:
        print('РЕЗУЛЬТАТ      : %s' % ('ВОСПРОИЗВЕДЕНО' if same else 'РАСХОЖДЕНИЕ — проверить версии архива и алгоритма'))
    return 0 if (same or mode == 'live') else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
