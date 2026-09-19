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
(app, vkd, experiments, config, data) менялся после коммита манифеста или есть
незакоммиченные изменения, совпадение объявляется «ВОСПРОИЗВЕДЕНО ДРУГОЙ ВЕРСИЕЙ»
(код возврата 3). Коды: 0 — воспроизведено, 1 — расхождение, 2 — повтор невозможен,
3 — воспроизведено другой версией.
"""
from __future__ import annotations

import io
import base64
import hashlib
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

CODE_PATHS = ('app', 'vkd', 'experiments', 'config', 'data', 'scripts')
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
                    raw[next((rid for rid, path in (man.get('raw_record_files') or {}).items() if path == n), n[4:-5])] = j(n)
            for sid, records in (man.get('source_versions') or {}).items():
                # Legacy manifests stored UI status dictionaries, not release maps.
                for rid, metadata in records.items():
                    if not isinstance(metadata, dict) or not metadata.get('sha256'):
                        continue
                    item = raw.get(rid, {})
                    content = base64.b64decode(item.get('content_base64', ''), validate=True)
                    if hashlib.sha256(content).hexdigest() != metadata['sha256']:
                        raise ValueError(f'Replay SHA-256 mismatch: {rid}')
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
    if raw and 'источники: vkd.sources' in (S.get('sources', {}).get('_layers', {}).get('status', '')):
        from vkd.sources.replay import observation, forecast
        from vkd.sources import Fetch
        from vkd.sources.registry import utc
        goes = observation(raw, 'noaa_swpc_goes', now)
        kp = observation(raw, 'gfz_kp', now)
        tle = raw.get('iss.tle') or {}
        meta = S.get('trajectory_meta') or {}
        text = tle.get('text') or meta.get('tle_text')
        # Missing inputs are also reproducible when the snapshot explicitly records them.
        for sid, bundle in [('noaa_swpc_goes', goes), ('gfz_kp', kp)]:
            if S.get('sources', {}).get(sid, {}).get('data_utc') is None:
                if sid == 'noaa_swpc_goes':
                    goes = (None, bundle[1], bundle[2])
                else:
                    kp = (None, bundle[1], bundle[2])
        fetch = Fetch('celestrak_gp', False, bool(text), utc(meta['fetched_utc']) if meta.get('fetched_utc') else None, None,
                      tle.get('fetch') or 'повтор TLE', text, None,
                      metadata={'url': tle.get('url')})
        return goes, kp, (text, fetch), forecast(raw, now)
    goes = next((v for k, v in raw.items() if k.startswith('goes_p10_')), None)
    kp = next((v for k, v in raw.items() if k.startswith('gfz_kp_')), None)
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
        commit[:12], head[:12], ', '.join(CODE_PATHS), 'изменён' if changed else 'не менялся',
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
            thresholds=th, scenario=sc, T_months=req.get('T_months', 6), fetched=fetched, now=now, refinement_policy=req.get('refinement_policy'))
    old, new = S['recommendation'], r.S['recommendation']
    checks = [('вердикт', old['verdict'], new['verdict']), ('предпочтительное окно', old.get('preferred'), new.get('preferred')),
              ('сравнение по механизмам', old.get('per_mechanism'), new.get('per_mechanism')),
              ('причины', list(old.get('reasons') or []), list(new.get('reasons'))),
              ('условия по окнам', _conditions(S['windows']), _conditions(r.S['windows'])),
              ('значения и покрытие факторов', json.loads(json.dumps(S['windows'], default=str)),
               json.loads(json.dumps(r.S['windows'], default=str)))]
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
