# -*- coding: utf-8 -*-
"""Воспроизведение сохранённого расчёта (Т8): из архива ZIP или JSON-снимка повторно
запускается конвейер и сравниваются вердикт, предпочтительное окно, сравнение по
механизмам, причины и условия по окнам.

    python scripts/replay_example.py examples/gannon_2024-05-10_cutoff19Z.zip
    python scripts/replay_example.py examples/live_now.zip

Исторические режимы: входы — архив в репозитории и отсечка, живые источники не
запрашиваются (fetch_none), результат обязан совпасть. Текущий режим: сохранённые
сырые записи GOES, Kp и TLE (raw/*.json архива) подставляются вместо живых запросов,
момент расчёта берётся из манифеста — повтор детерминирован и тоже обязан совпасть;
JSON-снимок текущего режима без сырых записей не воспроизводим (код возврата 2).

Версия: сравниваются версия алгоритма и коммит манифеста с текущими; если код расчёта
(CODE_PATHS — только модули, которые конвейер действительно вызывает) менялся после
коммита манифеста или есть незакоммиченные изменения, совпадение объявляется «ВОСПРОИЗВЕДЕНО ДРУГОЙ ВЕРСИЕЙ»
(код возврата 3). Коды: 0 — воспроизведено, 1 — расхождение, 2 — повтор невозможен,
3 — воспроизведено другой версией.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from app.compute import ALGO_VERSION, run                        # noqa: E402
from vkd.integration.replay_live import fetch_none, from_saved_records    # noqa: E402
from vkd.windows.compare import Thresholds                       # noqa: E402
from vkd.windows.scenario import Scenario                        # noqa: E402

# Пути, изменение которых означает ДРУГУЮ версию расчёта. Список сужен до модулей, которые
# расчёт действительно вызывает (девятый круг, М4): раньше здесь стоял весь каталог `app`, и
# правка модуля отрисовки глобуса (`app/globe.py`, который конвейер не импортирует) объявляла
# все девять примеров «воспроизведёнными другой версией» — проверено `git diff 79b25571 fcd1e0e
# -- app vkd experiments config data`: единственный изменившийся файл был `app/globe.py`.
# Выбран именно этот путь, а не пересборка примеров: пересборка живёт до первой правки экрана,
# сужение списка честно отвечает на вопрос «менялся ли код, который считал».
# `app/ui.py` оставлен: `app.compute` импортирует из него запись чисел и времени, и она попадает
# в снимок. `app/main.py`, `app/viz.py`, `app/globe.py`, `app/export.py` конвейер не вызывает.
# Снимок TLE и его манифест — ВХОД текущего режима, а не код: повтор подставляет сохранённые
# записи архива (`raw/*.json`), поэтому обновление снимка версией расчёта не является.
CODE_PATHS = ('app/compute.py', 'app/fetch_guard.py', 'app/norms.py', 'app/obs.py', 'app/ui.py',
              'vkd', 'experiments', 'config', 'data',
              ':(exclude)data/orbit/iss.tle', ':(exclude)data/orbit/manifest.json')
CODE_PATHS_RU = 'модули расчёта: app/compute.py и его зависимости, vkd, experiments, config, data (кроме снимка TLE)'
MODE_IDS = {'Текущая обстановка': 'live', 'Исторический разбор': 'history_review', 'Прогноз из прошлого': 'history_forecast'}


def load_snapshot(path: str) -> dict:
    """Единая форма для ZIP и JSON: request, recommendation, windows, manifest-поля, сырые записи (только ZIP)."""
    if path.endswith('.zip'):
        with zipfile.ZipFile(path) as z:
            j = lambda name: json.loads(z.read(name).decode('utf-8'))   # noqa: E731
            man, meta = j('manifest.json'), j('trajectory_meta.json')
            raw = {}
            for n in z.namelist():
                if n.startswith('raw/') and n.endswith('.json'):
                    raw[n[4:-5]] = j(n)
            return {'request': j('request.json'), 'recommendation': j('recommendation.json'), 'windows': j('factors.json'),
                    'mode_id': man.get('mode'), 'algorithm_version': man.get('algorithm_version'), 'git_commit': man.get('git_commit'),
                    'computed_utc': man.get('computed_utc'), 'trajectory_meta': meta, 'raw': raw, 'sources': j('sources.json')}
    S = json.load(io.open(path, encoding='utf-8'))
    return {'request': S['request'], 'recommendation': S['recommendation'], 'windows': S['windows'],
            'mode_id': S.get('mode_id') or MODE_IDS[S['mode']], 'algorithm_version': S.get('algorithm_version'), 'git_commit': S.get('git_commit'),
            'computed_utc': S.get('computed_utc'), 'trajectory_meta': S.get('trajectory_meta') or {}, 'raw': {}, 'sources': S.get('sources') or {}}


def saved_sources(S: dict, now: datetime) -> tuple | None:
    """Кортеж источников текущего режима из сырых записей архива; None, если записей нет.

    Записи A4 хранят точные байты ответа: повтор разбирает их тем же разборщиком,
    что и живой запрос (vkd.integration.replay_live)."""
    raw = S.get('raw') or {}
    tle = raw.get('iss.tle') or {}
    tle_text = tle.get('text') or (S.get('trajectory_meta') or {}).get('tle_text')
    return from_saved_records(raw, now, tle_text=tle_text, tle_status=tle.get('fetch'))


def code_state(commit: str | None) -> tuple[str, bool]:
    """(описание, изменился ли код расчёта относительно коммита манифеста)."""
    try:
        head = subprocess.check_output(['git', '-C', _ROOT, 'rev-parse', 'HEAD'], stderr=subprocess.DEVNULL, timeout=5).decode().strip()
    except Exception:            # noqa: BLE001 — вне репозитория версия кода не проверяема
        return 'git недоступен — версия кода не проверена', False
    dirty = ''
    try:
        st = subprocess.check_output(['git', '-C', _ROOT, 'status', '--porcelain', '--'] + list(CODE_PATHS), stderr=subprocess.DEVNULL, timeout=10).decode()
        dirty = st.strip()
    except Exception:            # noqa: BLE001
        pass
    if not commit:
        return 'в манифесте нет коммита; HEAD %s%s' % (head[:12], '; есть незакоммиченные изменения кода' if dirty else ''), True
    if commit == head and not dirty:
        return 'коммит манифеста %s = HEAD, рабочая копия чистая' % head[:12], False
    changed = True
    if commit != head:
        try:
            rc = subprocess.call(['git', '-C', _ROOT, 'diff', '--quiet', commit, head, '--'] + list(CODE_PATHS),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
            changed = rc != 0
        except Exception:        # noqa: BLE001
            changed = True
    else:
        changed = False
    if dirty:
        changed = True
    return ('коммит манифеста %s, HEAD %s: код расчёта (%s) %s%s' % (
        commit[:12], head[:12], CODE_PATHS_RU, 'изменён' if changed else 'не менялся',
        '; есть незакоммиченные изменения кода' if dirty else ''), changed)


def _conditions(windows: list) -> list:
    return [[r for m in w['mechanisms'] for r in m['needs_check']] for w in windows]


def main(path: str) -> int:
    sys.stdout.reconfigure(encoding='utf-8')
    S = load_snapshot(path)
    req = S['request']
    mode = S['mode_id']
    t0 = datetime.fromisoformat(req['t0_utc'])
    now = datetime.fromisoformat(S['computed_utc']) if S.get('computed_utc') else datetime.now(timezone.utc)
    th = Thresholds(**{k: v for k, v in dict(req['thresholds']).items() if k in Thresholds.__dataclass_fields__})
    sc = Scenario(**req['scenario']) if req.get('scenario') else None
    print('файл           :', path)
    print('режим          :', mode, '| t0', t0.isoformat(), '| момент расчёта', now.isoformat())
    if mode == 'live':
        fetched = saved_sources(S, now)
        if fetched is None:
            print('РЕЗУЛЬТАТ      : повтор невозможен — в снимке нет сырых записей GOES/Kp/TLE (нужен ZIP с raw/, а не JSON)')
            return 2
        print('источники      : GOES, Kp, TLE — из сырых записей архива (живых запросов нет)')
    else:
        fetched = fetch_none()
        print('источники      : не запрашивались — орбита из архива OEM 2024 (A1/A3), события из архива DONKI/NOAA, повтор детерминирован')
    r = run(mode, t0, req['duration_min'], req['search_min'], req['window_offsets_min'], disabled=req['disabled'],
            thresholds=th, scenario=sc, T_months=req.get('T_months', 6), fetched=fetched, now=now)
    old, new = S['recommendation'], r.S['recommendation']
    checks = [('вердикт', old['verdict'], new['verdict']), ('предпочтительное окно', old.get('preferred'), new.get('preferred')),
              ('сравнение по механизмам', old.get('per_mechanism'), new.get('per_mechanism')),
              ('причины', list(old.get('reasons') or []), list(new.get('reasons'))),
              ('условия по окнам', _conditions(S['windows']), _conditions(r.S['windows']))]
    same = True
    for name, a, b in checks:
        ok = a == b
        same &= ok
        print('%-22s: %s' % (name, 'совпало' if ok else 'РАСХОЖДЕНИЕ'))
        if not ok:
            print('   было  :', json.dumps(a, ensure_ascii=False)[:400])
            print('   стало :', json.dumps(b, ensure_ascii=False)[:400])
    algo_same = S.get('algorithm_version') == ALGO_VERSION
    code_txt, code_changed = code_state(S.get('git_commit'))
    print('версия алгоритма: сохранённая %s / текущая %s — %s' % (S.get('algorithm_version'), ALGO_VERSION, 'совпадает' if algo_same else 'ОТЛИЧАЕТСЯ'))
    print('версия кода    :', code_txt)
    if not same:
        print('РЕЗУЛЬТАТ      : РАСХОЖДЕНИЕ — проверить версии архива, настроек и алгоритма')
        return 1
    if not algo_same or code_changed:
        print('РЕЗУЛЬТАТ      : ВОСПРОИЗВЕДЕНО ДРУГОЙ ВЕРСИЕЙ (результат совпал, но версия алгоритма или код расчёта изменились)')
        return 3
    print('РЕЗУЛЬТАТ      : ВОСПРОИЗВЕДЕНО')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
