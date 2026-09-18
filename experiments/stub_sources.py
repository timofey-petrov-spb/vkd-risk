# -*- coding: utf-8 -*-
"""ВРЕМЕННЫЙ слой живых источников (до vkd/sources, A4). Область Б: experiments/.

Что делает: живой запрос → кеш на диске с временем получения → при отказе
источника отдаёт кеш с давностью и статусом «из кеша», при отсутствии кеша —
статус «нет данных». Никогда не возвращает ложное «благоприятно»: отсутствие
данных выражается None/статусом, не нулём (CONTRACT.md правила 7–9).

Источники (реестр — data/spaceweather/README.md):
  GOES  https://services.swpc.noaa.gov/json/goes/primary/integral-protons-3-day.json
  Kp    https://kp.gfz.de/app/json/?start=…&end=…&index=Kp   (GFZ, CC BY 4.0)
  TLE   https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE
Замещается импортом vkd.sources, когда он появится.
"""
from __future__ import annotations

import io
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

from vkd.types import EnvironmentSample, Kind

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(_ROOT, 'data', 'cache')
SNAP = os.path.join(_ROOT, 'data', 'spaceweather')
from vkd.config import section as _cfg_section   # noqa: E402  (Т7: настройки вне кода)
TIMEOUT_S = float(_cfg_section('sources').get('timeout_s', 8))   # при отказе — кеш или снимок со статусом

URLS = {
    'goes': 'https://services.swpc.noaa.gov/json/goes/primary/integral-protons-3-day.json',
    'tle': 'https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE',
}
URLS.update({k: str(v) for k, v in (_cfg_section('sources').get('urls') or {}).items()})   # адреса — из настроек


@dataclass(frozen=True)
class Fetch:
    source_id: str
    ok: bool                       # запрос удался
    from_cache: bool
    fetched_utc: Optional[datetime]
    age_min: Optional[float]       # давность данных к моменту запроса
    status_ru: str
    payload: Any                   # разобранный ответ или None
    raw_path: Optional[str]


def _now():
    return datetime.now(timezone.utc)


def _cache_path(key: str) -> str:
    os.makedirs(CACHE, exist_ok=True)
    return os.path.join(CACHE, key)


def _read_cache(key: str):
    p = _cache_path(key)
    if not os.path.exists(p):
        return None, None
    meta_p = p + '.meta.json'
    fetched = None
    if os.path.exists(meta_p):
        fetched = datetime.fromisoformat(json.load(io.open(meta_p, encoding='utf-8'))['fetched_utc'])
    return io.open(p, encoding='utf-8').read(), fetched


def _write_cache(key: str, text: str):
    p = _cache_path(key)
    io.open(p, 'w', encoding='utf-8').write(text)
    io.open(p + '.meta.json', 'w', encoding='utf-8').write(json.dumps({'fetched_utc': _now().isoformat()}))
    return p


def fetch_text(source_id: str, url: str, key: str, disabled: bool = False, force_live: bool = True) -> Fetch:
    """Живой запрос с кешем. disabled → только кеш, помечено. force_live=False → сначала кеш."""
    if disabled:
        txt, fetched = _read_cache(key)
        if txt is None:
            return Fetch(source_id, False, False, None, None, 'источник отключён, кеша нет — данных нет', None, None)
        age = (_now() - fetched).total_seconds() / 60 if fetched else None
        return Fetch(source_id, False, True, fetched, age, 'источник отключён: данные из кеша, давность %.0f мин' % (age or 0), txt, _cache_path(key))
    try:
        r = requests.get(url, timeout=TIMEOUT_S, headers={'User-Agent': 'vkd-risk/0.2 (hackathon prototype)'})
        r.raise_for_status()
        if not r.text.strip():
            raise ValueError('пустой ответ')
        p = _write_cache(key, r.text)
        return Fetch(source_id, True, False, _now(), 0.0, 'получено живьём, HTTP %d' % r.status_code, r.text, p)
    except Exception as e:            # noqa: BLE001 — любой отказ источника обрабатывается одинаково
        txt, fetched = _read_cache(key)
        if txt is None:
            snap = os.path.join(SNAP, key)
            if os.path.exists(snap):
                fetched = datetime.fromtimestamp(os.path.getmtime(snap), tz=timezone.utc)
                age = (_now() - fetched).total_seconds() / 60
                return Fetch(source_id, False, True, fetched, age, 'отказ источника (%s): снимок репозитория, давность %.0f мин' % (type(e).__name__, age),
                             io.open(snap, encoding='utf-8').read(), snap)
            return Fetch(source_id, False, False, None, None, 'отказ источника (%s), кеша нет — данных нет' % type(e).__name__, None, None)
        age = (_now() - fetched).total_seconds() / 60 if fetched else None
        return Fetch(source_id, False, True, fetched, age, 'отказ источника (%s): кеш, давность %.0f мин' % (type(e).__name__, age or 0), txt, _cache_path(key))


# ----------------------------------------------------------------- GOES
def goes_latest(disabled: bool = False) -> tuple[Optional[EnvironmentSample], dict, Fetch]:
    f = fetch_text('noaa_swpc_goes', URLS['goes'], 'goes_protons_3day.json', disabled)
    if f.payload is None:
        return None, {}, f
    try:
        d = json.loads(f.payload)
    except ValueError:
        return None, {}, Fetch(f.source_id, False, f.from_cache, f.fetched_utc, f.age_min, 'ответ не разбирается', None, f.raw_path)
    p10 = [x for x in d if x.get('energy') == '>=10 MeV' and x.get('flux') is not None]
    if not p10:
        return None, {}, Fetch(f.source_id, f.ok, f.from_cache, f.fetched_utc, f.age_min, 'в ответе нет канала >=10 МэВ', None, f.raw_path)
    last = max(p10, key=lambda x: x['time_tag'])
    t = datetime.fromisoformat(last['time_tag'].replace('Z', '+00:00'))
    rid = 'goes_p10#' + last['time_tag']
    s = EnvironmentSample(t, 'goes_p_ge10MeV', float(last['flux']), 'pfu', 'noaa_swpc_goes', Kind.OBSERVATION,
                          None, None, None, f.fetched_utc or _now(), 'preliminary', rid)
    return s, {rid: {**last, 'url': URLS['goes'], 'fetched_utc': (f.fetched_utc or _now()).isoformat()}}, f


# ----------------------------------------------------------------- Kp (GFZ)
def kp_latest(disabled: bool = False) -> tuple[Optional[EnvironmentSample], dict, Fetch]:
    end = _now(); start = end - timedelta(days=2)
    url = 'https://kp.gfz.de/app/json/?start=%s&end=%s&index=Kp' % (start.strftime('%Y-%m-%dT%H:%M:%SZ'), end.strftime('%Y-%m-%dT%H:%M:%SZ'))
    f = fetch_text('gfz_kp', url, 'kp_recent.json', disabled)
    if f.payload is None:
        return None, {}, f
    try:
        d = json.loads(f.payload)
        times, vals = d.get('datetime', []), d.get('Kp', [])
        status = d.get('status', [])
    except ValueError:
        return None, {}, Fetch(f.source_id, False, f.from_cache, f.fetched_utc, f.age_min, 'ответ не разбирается', None, f.raw_path)
    if not times:
        return None, {}, Fetch(f.source_id, f.ok, f.from_cache, f.fetched_utc, f.age_min, 'ответ пуст: нет значений за двое суток', None, f.raw_path)
    t = datetime.fromisoformat(times[-1].replace('Z', '+00:00'))
    rid = 'gfz_kp#' + times[-1]
    q = 'final' if (status and str(status[-1]).lower().startswith('def')) else 'preliminary'
    s = EnvironmentSample(t, 'kp', float(vals[-1]), '', 'gfz_kp', Kind.OBSERVATION, None, t, t + timedelta(hours=3),
                          f.fetched_utc or _now(), q, rid)
    return s, {rid: {'datetime': times[-1], 'Kp': vals[-1], 'status': status[-1] if status else None, 'url': url,
                     'fetched_utc': (f.fetched_utc or _now()).isoformat()}}, f


# ----------------------------------------------------------------- TLE
def tle_latest(disabled: bool = False) -> tuple[Optional[str], Fetch]:
    f = fetch_text('celestrak_gp', URLS['tle'], 'iss.tle', disabled)
    if f.payload is None or 'ISS' not in f.payload:
        return None, f
    return f.payload, f
