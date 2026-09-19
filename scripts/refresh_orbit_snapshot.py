# -*- coding: utf-8 -*-
"""Обновление снимка элементов орбиты в репозитории (A3, критерий Т2/Т6).

Запуск:

    python scripts/refresh_orbit_snapshot.py            # только отчёт, ничего не пишет
    python scripts/refresh_orbit_snapshot.py --write    # записать, если полученный набор СВЕЖЕЕ

Зачем. Текущий режим требует элементы орбиты не старше `[thresholds].tle_max_age_days`
(3 сут) относительно ОБЕИХ границ горизонта (`vkd/orbit/trajectory.py`). Без сети и без
кеша орбита строится по снимку `data/orbit/iss.tle`, который проверяется по записи
`data/orbit/manifest.json` (SHA-256, размер, адрес, время получения) — `_bundled_tle`
в `vkd/sources/live_cache.py`. Когда эпоха снимка уходит за предел, орбита честно
объявляется недоступной, и вердикт становится «оснований недостаточно». Поэтому снимок
обновляют перед показом.

Как. Набор берётся ТЕМ ЖЕ слоем источников, что и приложение: те же `Product` и `acquire`
(`vkd/sources/live_cache.py`), тот же разбор и та же проверка `parse_tle`, те же адреса из
`config/settings.toml` (`[sources.urls].tle`). Никаких собственных адресов скрипт не знает:
иначе в манифест попал бы адрес, которого нет в цепочке приложения, и снимок перестал бы
подходить под `_bundled_tle` — проверка адреса там строгая.

Одно отличие от живого пути, и оно намеренное: приложение останавливается на ПЕРВОМ чистом
ответе (`tle_latest`), а снимок должен быть самым свежим из доступных, поэтому скрипт
опрашивает все адреса цепочки и берёт набор с наибольшей эпохой. Если самый свежий набор
пришёл не с первого адреса, скрипт говорит об этом отдельной строкой: значит, живой путь
приложения работает на более старых элементах, чем снимок, и порядок адресов в настройках
стоит пересмотреть.

Что записывается. Проверенный набор элементов (ASCII, перевод строки `\\n`, без хвостовых
пробелов) — ровно в том виде, в каком его кладёт `stage_live_root` (`vkd/integration/orbit_bridge.py`),
и запись манифеста: `sha256`, `bytes`, `url` фактического адреса, `fetched_utc`,
`available_utc`, `raw_record_id`, `release_id`, `evidence`.

Коды возврата: 0 — снимок актуален или обновлён; 2 — набор получить не удалось;
3 — полученный набор НЕ свежее снимка (обновлять нечего, файл не тронут).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from vkd.config import section                               # noqa: E402
from vkd.orbit.trajectory import satellite_from_tle          # noqa: E402
from vkd.sources.live import TLE_URL, _age, _configured      # noqa: E402
from vkd.sources.live_cache import Product, acquire          # noqa: E402
from vkd.sources.live_parsers import parse_tle               # noqa: E402

SNAPSHOT = os.path.join(_ROOT, 'data', 'orbit', 'iss.tle')
MANIFEST = os.path.join(_ROOT, 'data', 'orbit', 'manifest.json')
EVIDENCE = ('Direct HTTP retrieval of this element set through vkd.sources.live.tle_latest; '
            'epoch is separate from publication.')


def _h(hours: float) -> str:
    return ('%.1f' % hours).replace('.', ',')


def _ru(t: datetime | None) -> str:
    return t.strftime('%d.%m.%Y %H:%M') + ' UTC' if t else 'неизвестно'


def _epoch_of(text: str) -> datetime:
    return satellite_from_tle(text.strip().encode('ascii') + b'\n').epoch.utc_datetime()


def _snapshot_epoch() -> tuple[datetime | None, str]:
    try:
        text = io.open(SNAPSHOT, encoding='ascii').read()
    except OSError as exc:                       # noqa: BLE001 — снимка может не быть вовсе
        return None, 'снимок не читается (%s)' % exc
    try:
        return _epoch_of(text), ''
    except Exception as exc:                     # noqa: BLE001
        return None, 'снимок не разбирается (%s)' % exc


def _poll_all(now: datetime) -> list:
    """Все адреса цепочки приложения, тем же слоем; список Fetch с пригодным набором."""
    urls = section('sources').get('urls', {}).get('tle', TLE_URL)
    urls = [urls] if isinstance(urls, str) else list(urls)
    base = Product('celestrak_gp', TLE_URL, parse_tle, _age(3 * 24 * 60), 7200, strict_poll=True)
    out = []
    for url in urls:
        product = _configured(base, 'tle', endpoint=url)
        fetched = acquire(product, now=now)
        state = fetched.status if not fetched.error else '%s (%s)' % (fetched.status, fetched.error)
        epoch = fetched.parsed.get('data_utc') if fetched.parsed else None
        print('   адрес %-62s %-22s эпоха %s' % (url, state, _ru(epoch)))
        if fetched.payload is not None:
            out.append(fetched)
    return out


def main(write: bool) -> int:
    sys.stdout.reconfigure(encoding='utf-8')
    now = datetime.now(timezone.utc)
    old_epoch, why = _snapshot_epoch()
    print('снимок репозитория : %s  %s' % (_ru(old_epoch), why))
    print('опрос цепочки адресов (порядок — config/settings.toml, [sources.urls].tle):')

    usable = _poll_all(now)
    if not usable:
        print('получение          : НЕ УДАЛОСЬ ни по одному адресу')
        print('вывод              : снимок не тронут; без сети орбита строится по нему же, '
              'пока его эпоха не ушла за предел возраста')
        return 2

    fetch = max(usable, key=lambda f: (f.parsed['data_utc'], f.fetched_utc))
    text = fetch.payload
    new_epoch = _epoch_of(text)
    url = fetch.metadata.get('url') or ''
    print('самый свежий набор : эпоха %s, адрес %s' % (_ru(new_epoch), url))
    # то же правило выбора, что у tle_latest: первый ответ без ошибки, иначе самый свежий
    chain = next((f for f in usable if not f.error), fetch)
    if chain.parsed['data_utc'] < fetch.parsed['data_utc']:
        print('ВНИМАНИЕ           : живой путь приложения выберет %s с эпохой %s — на %s ч старше '
              'самого свежего набора и на %s ч старше снимка репозитория. Правило выбора — первый '
              'ответ без ошибки (vkd/sources/live.py, tle_latest), поэтому порядок адресов в '
              '[sources.urls].tle решает, по каким элементам считает текущий режим.'
              % (chain.metadata.get('url'), _ru(chain.parsed['data_utc']),
                 _h((fetch.parsed['data_utc'] - chain.parsed['data_utc']).total_seconds() / 3600),
                 _h(((old_epoch - chain.parsed['data_utc']).total_seconds() / 3600) if old_epoch else 0.0)))

    if old_epoch is not None and new_epoch <= old_epoch:
        print('вывод              : полученный набор НЕ свежее снимка '
              '(%s против %s) — файл не тронут' % (_ru(new_epoch), _ru(old_epoch)))
        return 3

    raw = text.strip().encode('ascii') + b'\n'
    sha = hashlib.sha256(raw).hexdigest()
    fetched = fetch.fetched_utc or now
    fetched_iso = fetched.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    record = {'file': 'iss.tle', 'sha256': sha, 'bytes': len(raw), 'url': url,
              'fetched_utc': fetched_iso, 'available_utc': fetched_iso, 'evidence': EVIDENCE,
              'raw_path': 'data/orbit/iss.tle', 'source_id': 'celestrak_gp',
              'raw_record_id': 'celestrak_gp:25544:' + sha[:12], 'release_id': sha}
    if not write:
        print('вывод              : набор СВЕЖЕЕ снимка на %s ч; запустите с --write, чтобы записать'
              % _h((new_epoch - old_epoch).total_seconds() / 3600 if old_epoch else 0.0))
        print(json.dumps(record, ensure_ascii=False, indent=1))
        return 0

    with io.open(MANIFEST, encoding='utf-8') as fh:
        manifest = json.load(fh)
    manifest['records'] = [r for r in manifest['records'] if r.get('file') != 'iss.tle'] + [record]
    with io.open(SNAPSHOT, 'wb') as fh:
        fh.write(raw)
    with io.open(MANIFEST, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write('\n')
    print('записано           : data/orbit/iss.tle (%d Б, SHA-256 %s…) и запись манифеста' % (len(raw), sha[:12]))
    print('проверьте          : python -m pytest -q tests/orbit tests/test_integration.py')
    return 0


if __name__ == '__main__':
    sys.exit(main('--write' in sys.argv[1:]))
