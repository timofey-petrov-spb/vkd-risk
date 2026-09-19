# -*- coding: utf-8 -*-
"""Общий предел на получение живых источников: экран обязан отрисоваться даже без сети (Т6).

ЗАЧЕМ. У каждого живого запроса в `vkd.sources` свой тайм-аут соединения и чтения, но общего
предела на ВСЕ источники одного рендера не было: в текущем режиме подряд идут GOES, Kp, цепочка
TLE из трёх адресов и трёхсуточный бюллетень NOAA — шесть адресов. Хуже того, зависание на
разрешении имени (DNS) тайм-аутами `requests` не покрывается вовсе: `socket.getaddrinfo` ждёт
столько, сколько ждёт системный резолвер. На площадке с ограниченным исходящим доступом это даёт
первый рендер, который не наступает никогда, — ровно то, что наблюдалось на развёрнутом сервисе.
Замер на этой машине (`socket.getaddrinfo` спит 5 с и только потом объявляет отказ, кеш пуст):
девять разрешений имени, первый рендер 47,4 с; при реальном зависании резолвера предела нет.

ЧТО ДЕЛАЕТ. Живое получение выполняется в отдельном потоке; ждём его не дольше
`[sources].total_deadline_s` секунд. Не уложились — поток бросаем (он демонский и рендеру больше
не мешает), а тот же кортеж собираем БЕЗ СЕТИ тем же слоем `vkd.sources` в режиме «только кеш»:
кеш, затем снимок репозитория, затем честное «данных нет». Причина называется словами и попадает
в `status_ru` каждого источника, который запрашивался живьём, то есть в снимок расчёта, на экран
и в выгрузку. Источник, который пользователь сам отключил или перевёл на кеш, не переписывается:
предел — не его причина.

ПОВТОРНЫЕ ПОПЫТКИ. Пока брошенный поток жив, нового живого запроса не делается вовсе (сеть уже
занята зависшей попыткой) — следующий рендер сразу идёт по кешу. Частота повторов сверх этого
ограничена двумя уже существующими рубежами: кеш экрана `@st.cache_data(ttl=[sources].cache_ttl_s)`
и пауза запросов слоя источников (`attempt.json`, `poll_seconds`), которую слой резервирует ДО
обращения к сети — то есть зависшая попытка её уже израсходовала.

ПОЧЕМУ НЕ ThreadPoolExecutor. `concurrent.futures` на выходе из интерпретатора join-ит свои
рабочие потоки (`threading._register_atexit`), и поток, зависший на разрешении имени, задержал бы
остановку контейнера ровно так же, как он задерживал рендер. У обычного потока с `daemon=True`
этого свойства нет.
"""
from __future__ import annotations

import logging
import math
import threading
from dataclasses import replace

from vkd.config import section as _settings_section
from vkd.sources import Fetch, goes_latest, kp_latest, noaa_latest, tle_latest

LOG = logging.getLogger('vkd.app.fetch')

DEFAULT_TOTAL_DEADLINE_S = 12.0     # умолчание кода на случай отсутствия ключа или файла настроек
MAX_TOTAL_DEADLINE_S = 120.0        # больше двух минут — это уже не «предел», а прежнее поведение
KEYS = ('goes', 'kp', 'tle', 'noaa')                  # порядок кортежа = порядок распаковки в app.compute.run
SOURCE_ID = {'goes': 'noaa_swpc_goes', 'kp': 'gfz_kp', 'tle': 'celestrak_gp', 'noaa': 'noaa_swpc_3day_forecast'}
# С чего слой источников начинает статус, когда живого запроса не было (vkd/sources/live_cache.py,
# labels): эту подпись мы заменяем своей причиной, остальную часть статуса сохраняем как есть.
_NO_REQUEST_LABELS = ('источник отключён', 'источник исключён пользователем')

_lock = threading.Lock()
_pending: threading.Thread | None = None      # брошенная попытка, если она ещё жива


def _sec_ru(v: float) -> str:
    """Секунды по-русски: 12 → «12», 7.5 → «7,5». Единица подставляется вызывающим."""
    return ('%g' % float(v)).replace('.', ',')


def total_deadline_s() -> float:
    """Общий предел из config/settings.toml. Неправильное значение — ошибка, а не тихое умолчание."""
    v = _settings_section('sources').get('total_deadline_s', DEFAULT_TOTAL_DEADLINE_S)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
        raise ValueError('sources.total_deadline_s должен быть положительным числом секунд, получено %r' % (v,))
    if float(v) > MAX_TOTAL_DEADLINE_S:
        raise ValueError('sources.total_deadline_s не должен превышать %s с, получено %s'
                         % (_sec_ru(MAX_TOTAL_DEADLINE_S), _sec_ru(v)))
    return float(v)


LIMIT_MARK = 'общий предел получения источников'    # по этому признаку экран видит, что причина уже названа


def deadline_reason_ru(limit_s: float) -> str:
    """Причина отказа, одна на все места: экран, снимок, выгрузка."""
    return 'превышен %s %s с' % (LIMIT_MARK, _sec_ru(limit_s))


def modes(disabled: dict | None = None) -> dict:
    """Состояние каждого живого источника по запросу экрана. TLE в текущем режиме запрашивается
    всегда: орбита без элементов не строится, и отдельного переключателя у него на экране нет."""
    d = disabled or {}
    return {'goes': d.get('goes', False), 'kp': d.get('kp', False), 'tle': False, 'noaa': d.get('noaa', False)}


def _live_attempt(mode) -> bool:
    """Пойдёт ли этот источник в сеть: False/'on' — да; True/'cache' — только кеш; 'off' — исключён."""
    return mode is False or mode == 'on'


def fetch(mode_map: dict, *, offline: bool = False, transport=None) -> tuple:
    """Кортеж четырёх источников в порядке KEYS. offline=True — ни одного обращения к сети:
    слой источников переводится в режим «только кеш» ('off' остаётся 'off' — это выбор пользователя)."""
    m = ({k: ('off' if v == 'off' else 'cache') for k, v in mode_map.items()} if offline else dict(mode_map))
    kw = {} if transport is None else {'transport': transport}
    return (goes_latest(disabled=m['goes'], **kw), kp_latest(disabled=m['kp'], **kw),
            tle_latest(disabled=m['tle'], **kw), noaa_latest(disabled=m['noaa'], **kw))


def _restate(f: Fetch, reason: str) -> Fetch:
    """Статус источника, который не успел: причина названа, остальная часть статуса слоя сохранена."""
    s = (f.status_ru or '').strip()
    tail = s
    for label in _NO_REQUEST_LABELS:
        if s.startswith(label):
            tail = s[len(label):].lstrip(' ,:;')
            break
    head = reason + ': живого запроса не было' + (', взят проверенный кеш' if f.from_cache else '')
    return replace(f, status_ru=head + (', ' + tail if tail else ''))


def mark(fetched: tuple, mode_map: dict, reason: str) -> tuple:
    """Причина проставляется только тем источникам, которые ДОЛЖНЫ были идти в сеть в этом рендере."""
    out = []
    for key, item in zip(KEYS, fetched):
        if _live_attempt(mode_map[key]) and isinstance(item, tuple) and item and isinstance(item[-1], Fetch):
            item = item[:-1] + (_restate(item[-1], reason),)
        out.append(item)
    return tuple(out)


def reset_pending() -> None:
    """Забыть брошенную попытку (нужно тестам и повторному запуску в одном процессе)."""
    global _pending
    with _lock:
        _pending = None


def pending_thread():
    """Брошенная попытка, если она ещё жива, иначе None — для проверок и диагностики."""
    with _lock:
        return _pending if (_pending is not None and _pending.is_alive()) else None


def fetch_live_sources(disabled: dict | None = None, *, deadline_s: float | None = None, transport=None) -> tuple:
    """(кортеж четырёх источников, причина отказа по общему пределу или None).

    Исключений не бросает: экран должен отрисоваться при любом состоянии сети. Ошибка живого
    получения не проглатывается молча — она называется в причине и уходит в журнал сервера.
    """
    global _pending
    mode_map = modes(disabled)
    limit = total_deadline_s() if deadline_s is None else float(deadline_s)
    if not math.isfinite(limit) or limit <= 0:
        raise ValueError('предел получения источников должен быть положительным числом секунд, получено %r' % (deadline_s,))
    reason = deadline_reason_ru(limit)

    stuck = pending_thread()
    if stuck is not None:
        why = reason + '; предыдущая попытка получения ещё не завершилась'
        LOG.warning('живые источники: предыдущая попытка ещё не завершилась — рендер по кешу')
        return mark(fetch(mode_map, offline=True), mode_map, why), why
    reset_pending()

    box: dict = {}

    def _target():
        try:
            box['value'] = fetch(mode_map, transport=transport)
        except BaseException as exc:            # noqa: BLE001 — поток не имеет права уронить рендер
            box['error'] = exc

    th = threading.Thread(target=_target, name='vkd-live-sources', daemon=True)
    th.start()
    th.join(limit)
    if th.is_alive():
        with _lock:
            _pending = th
        LOG.warning('живые источники не уложились в %s с — рендер по кешу и снимку репозитория', _sec_ru(limit))
        return mark(fetch(mode_map, offline=True), mode_map, reason), reason
    err = box.get('error')
    if err is not None:
        why = ('получение живых источников прервано ошибкой %s — взяты кеш и снимок репозитория'
               % type(err).__name__)
        LOG.error('живые источники: %s', err, exc_info=err)
        return mark(fetch(mode_map, offline=True), mode_map, why), why
    return box['value'], None
