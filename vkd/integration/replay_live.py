# -*- coding: utf-8 -*-
"""Воспроизведение текущего режима из сохранённых сырых записей (Т8) на слое A4.

До стыка третьего круга это делал временный слой Б (`experiments/stub_sources`):
он хранил разобранные поля и собирал из них значение заново. A4 сохраняет ТОЧНЫЕ
БАЙТЫ ответа (`content_base64`) и метаданные квитанции, поэтому повтор теперь
разбирает те же байты тем же разборщиком, что и живой запрос, — совпадение
проверяется на уровне исходного ответа, а не нашего пересказа.

Сети здесь нет ни при каких условиях: функции принимают уже сохранённые записи.
"""
from __future__ import annotations

import base64
from datetime import datetime
from typing import Any, Optional

from vkd.sources.live_cache import Fetch
from vkd.sources.live_parsers import LiveDataError, parse_goes, parse_kp, parse_noaa_live, parse_tle
from vkd.sources.registry import utc
from vkd.types import EnvironmentSample, Kind

GOES = 'noaa_swpc_goes'
KP = 'gfz_kp'
TLE = 'celestrak_gp'
NOAA = 'noaa_swpc_3day_forecast'
PARSERS = {GOES: parse_goes, KP: parse_kp, TLE: parse_tle, NOAA: parse_noaa_live}
WHY_REPLAY = 'из сохранённого расчёта (воспроизведение)'
WHY_NONE = 'источники не запрашивались: исторический режим, входы — архив'


def _off(source_id: str, why: str) -> Fetch:
    return Fetch(source_id, False, False, None, None, why, None, None)


def fetch_none(why: str = WHY_NONE) -> tuple:
    """Кортеж «живые источники не запрашивались» для исторических режимов (Т6: без сети ждать нечего).

    Форма совпадает с тем, что передаёт экран: GOES, Kp, TLE и живой прогноз NOAA.
    """
    return ((None, {}, _off(GOES, why)), (None, {}, _off(KP, why)),
            (None, _off(TLE, why)), ((), {}, _off(NOAA, why)))


def _find(raw_records: dict, source_id: str) -> Optional[tuple[str, dict]]:
    for rid, rec in (raw_records or {}).items():
        if not isinstance(rec, dict):
            continue
        meta = rec.get('metadata') or {}
        if meta.get('source_id') == source_id and rec.get('content_base64'):
            return rid, rec
    return None


def _parse(rec: dict, source_id: str, now: datetime):
    """(метаданные, разбор, байты) из сохранённой записи; LiveDataError — если байты не разбираются.

    Разбор идёт на момент ПОЛУЧЕНИЯ записи, а не на момент расчёта. Иначе повтор
    не воспроизводит живой запрос: разборщик Kp отбрасывает незавершённый 3-часовой
    интервал по правилу `конец интервала > now` (`vkd/sources/live_parsers.parse_kp`),
    и при разборе на момент расчёта интервал, который в живом запросе был ещё
    незавершённым, становится завершённым — повтор берёт другое значение Kp.
    Давность данных при этом по-прежнему считается от момента расчёта (см. `_fetch`).
    """
    meta = dict(rec.get('metadata') or {})
    raw = base64.b64decode(rec['content_base64'])
    parsed = PARSERS[source_id](raw, utc(meta['fetched_utc']) if meta.get('fetched_utc') else now)
    return meta, parsed, raw


def _fetch(source_id: str, meta: dict, parsed: dict, raw: bytes, now: datetime, note: str = '') -> Fetch:
    fetched = utc(meta['fetched_utc']) if meta.get('fetched_utc') else None
    age = (now - parsed['data_utc']).total_seconds() / 60.0 if parsed.get('data_utc') else None
    payload = parsed.get('payload_text', raw.decode('utf-8', 'replace'))
    return Fetch(source_id, False, True, fetched, age,
                 WHY_REPLAY + (('; ' + note) if note else '') + ('; давность данных %.1f мин' % age if age is not None else ''),
                 payload, meta.get('raw_path'), 'replay', None, meta, parsed, raw)


def _observation(rid: str, rec: dict, source_id: str, now: datetime):
    meta, parsed, raw = _parse(rec, source_id, now)
    f = _fetch(source_id, meta, parsed, raw, now)
    sample = EnvironmentSample(parsed['data_utc'], parsed['channel_id'], parsed['value'], parsed['unit'], source_id,
                               Kind.OBSERVATION, parsed['published_utc'], parsed.get('valid_from_utc'),
                               parsed.get('valid_to_utc'), f.fetched_utc, parsed['quality'],
                               meta['raw_record_id'], meta['version'])
    return sample, {rid: rec}, f


def from_saved_records(raw_records: dict, now: datetime, tle_text: Optional[str] = None,
                       tle_status: Optional[str] = None) -> Optional[tuple]:
    """Кортеж источников текущего режима из сырых записей архива; None — повтор невозможен.

    Повтор невозможен, если в архиве нет ни байтов GOES, ни текста TLE: без них
    расчёт был бы не воспроизведением, а новым расчётом с другими входами.
    """
    goes_rec = _find(raw_records, GOES)
    kp_rec = _find(raw_records, KP)
    tle_rec = _find(raw_records, TLE)
    noaa_rec = _find(raw_records, NOAA)

    goes, goes_raw, f_goes = None, {}, _off(GOES, WHY_REPLAY + ': запись не сохранена')
    if goes_rec:
        try:
            goes, goes_raw, f_goes = _observation(goes_rec[0], goes_rec[1], GOES, now)
        except (LiveDataError, KeyError, ValueError) as exc:
            f_goes = _off(GOES, WHY_REPLAY + ': сохранённые байты не разобраны (%s)' % exc)
    kp, kp_raw, f_kp = None, {}, _off(KP, WHY_REPLAY + ': запись не сохранена')
    if kp_rec:
        try:
            kp, kp_raw, f_kp = _observation(kp_rec[0], kp_rec[1], KP, now)
        except (LiveDataError, KeyError, ValueError) as exc:
            f_kp = _off(KP, WHY_REPLAY + ': сохранённые байты не разобраны (%s)' % exc)

    f_tle = _off(TLE, WHY_REPLAY + ': запись не сохранена')
    if tle_rec:
        try:
            meta, parsed, raw = _parse(tle_rec[1], TLE, now)
            f_tle = _fetch(TLE, meta, parsed, raw, now, note=('исходно: %s' % tle_status) if tle_status else '')
            tle_text = parsed.get('payload_text') or tle_text
        except (LiveDataError, KeyError, ValueError) as exc:
            f_tle = _off(TLE, WHY_REPLAY + ': сохранённые байты TLE не разобраны (%s)' % exc)
    if tle_text and f_tle.payload is None:
        # запись байтов не сохранена (старый пример) — текст TLE из снимка, происхождение объявлено
        f_tle = Fetch(TLE, False, True, None, None,
                      WHY_REPLAY + ': текст TLE из снимка, исходные байты ответа не сохранены'
                      + (('; исходно: %s' % tle_status) if tle_status else ''),
                      tle_text, None, 'replay')
    if not tle_text or goes_rec is None:
        return None

    noaa_samples: tuple = ()
    noaa_raw: dict = {}
    f_noaa = _off(NOAA, WHY_REPLAY + ': запись не сохранена')
    if noaa_rec:
        try:
            meta, parsed, raw = _parse(noaa_rec[1], NOAA, now)
            f_noaa = _fetch(NOAA, meta, parsed, raw, now)
            names = {'noaa_kp': 'kp_forecast', 's1_or_greater_probability': 's1_prob_daily'}
            noaa_samples = tuple(EnvironmentSample(
                utc(c['valid_from_utc']), names[c['channel_id']], c['value'], c['unit'], NOAA,
                Kind.EXTERNAL_FORECAST, utc(c['published_utc']), utc(c['valid_from_utc']), utc(c['valid_to_utc']),
                f_noaa.fetched_utc, 'model', meta['raw_record_id'], meta['version'])
                for c in parsed['cells'] if c['channel_id'] in names)
            noaa_raw = {noaa_rec[0]: noaa_rec[1]}
        except (LiveDataError, KeyError, ValueError) as exc:
            f_noaa = _off(NOAA, WHY_REPLAY + ': сохранённые байты прогноза не разобраны (%s)' % exc)
    return (goes, goes_raw, f_goes), (kp, kp_raw, f_kp), (tle_text, f_tle), (noaa_samples, noaa_raw, f_noaa)


__all__: list[Any] = ['fetch_none', 'from_saved_records', 'WHY_NONE', 'WHY_REPLAY']
