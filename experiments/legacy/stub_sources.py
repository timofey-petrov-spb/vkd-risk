# -*- coding: utf-8 -*-
"""ВРЕМЕННЫЙ слой живых источников (до vkd/sources, A4). Область Б: experiments/.

Что делает: живой запрос → кеш на диске с временем получения → при отказе
источника отдаёт кеш с давностью и статусом «из кеша», при отсутствии кеша —
снимок репозитория с записанным происхождением, иначе — статус «нет данных».
Никогда не возвращает ложное «благоприятно»: отсутствие данных выражается
None/статусом, не нулём; неизвестное время получения — словами «неизвестно»,
не «давность 0 мин» (CONTRACT.md правила 7–9, критерий Т1).

Источники (реестр — data/spaceweather/README.md):
  GOES  https://services.swpc.noaa.gov/json/goes/primary/integral-protons-3-day.json
  Kp    https://kp.gfz.de/app/json/?start=…&end=…&index=Kp   (GFZ, CC BY 4.0)
  TLE   цепочка адресов из config/settings.toml [sources.urls].tle (CelesTrak — первичный)

Прослеживаемость (Т1, Т2):
  * каждая запись кеша сопровождается meta-файлом: время получения, адрес, SHA-256
    данных; meta без совпадающего SHA-256 считается несоответствующим, и время
    получения объявляется неизвестным;
  * тело ответа TLE сохраняется байт в байт (data/cache/tle_raw_<хост>.txt) вместе
    с SHA-256; нормализованные три строки — производная запись со ссылкой на исходную;
  * снимок TLE в репозитории берётся из data/orbit/iss.tle с записью манифеста A3
    (fetched_utc, url, sha256), а не по времени изменения файла, которое на свежем
    клоне равно времени клонирования.

Отказы (Т6): любой ответ не той формы даёт Fetch(ok=False, «ответ не разбирается»),
а не исключение; три источника опрашиваются параллельно (fetch_all), поэтому
ожидание при недоступной сети ограничено одним тайм-аутом.
Замещается импортом vkd.sources, когда он появится.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

from vkd.config import section as _cfg_section   # Т7: настройки вне кода
from vkd.types import EnvironmentSample, Kind

# модуль перенесён в experiments/legacy/ (архив, в конвейере не используется с 0.6.0):
# корень репозитория теперь на уровень выше
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE = os.path.join(_ROOT, 'data', 'cache')
SNAP = os.path.join(_ROOT, 'data', 'spaceweather')
ORBIT_SNAP_DIR = os.path.join(_ROOT, 'data', 'orbit')             # снимок TLE с манифестом A3
TIMEOUT_S = float(_cfg_section('sources').get('timeout_s', 8))   # при отказе — кеш или снимок со статусом
# Снимки data/spaceweather скачаны 17.09.2026 (README там же); время суток не записано,
# поэтому давность снимка считается от начала этих суток и так и подписывается.
SNAPSHOT_DOWNLOADED_UTC = datetime(2026, 9, 17, tzinfo=timezone.utc)

URLS = {
    'goes': 'https://services.swpc.noaa.gov/json/goes/primary/integral-protons-3-day.json',
    'tle': 'https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE',
}
URLS.update({k: (list(v) if isinstance(v, (list, tuple)) else str(v))
             for k, v in (_cfg_section('sources').get('urls') or {}).items()})   # адреса — из настроек; TLE — список резервов

_PARSE_ERRORS = (ValueError, TypeError, KeyError, AttributeError, IndexError)


@dataclass(frozen=True)
class Fetch:
    source_id: str
    ok: bool                       # запрос удался
    from_cache: bool
    fetched_utc: Optional[datetime]     # None — время получения неизвестно (не подменяется «сейчас»)
    age_min: Optional[float]       # давность данных к моменту запроса; None — неизвестна
    status_ru: str
    payload: Any                   # разобранный ответ или None
    raw_path: Optional[str]
    url: Optional[str] = None      # адрес, с которого фактически получены данные (живьём или по meta кеша/манифесту)
    raw_response_path: Optional[str] = None     # тело ответа как есть (TLE: JSON поставщика), Т2
    raw_response_sha256: Optional[str] = None
    provider_time: Optional[str] = None         # время по данным поставщика (tle_timestamp / date), если названо
    fetched_basis: str = ''                     # чем подтверждено время получения: 'живой запрос' | 'meta кеша' | 'манифест A3' | 'README снимка'


def _now():
    return datetime.now(timezone.utc)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cache_path(key: str) -> str:
    os.makedirs(CACHE, exist_ok=True)
    return os.path.join(CACHE, key)


def _atomic_write(path: str, data: bytes) -> None:
    """Запись через временное имя в том же каталоге и os.replace: читатель никогда не видит
    полуфайл, обрыв не оставляет усечённой записи."""
    fd, tmp = tempfile.mkstemp(prefix='.tmp_', dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_cache(key: str):
    """(текст, время получения, meta). Время получения None, если meta-файла нет, он не читается
    или его SHA-256 не совпадает с данными (meta от другой записи после обрыва)."""
    p = _cache_path(key)
    if not os.path.exists(p):
        return None, None, {}
    try:
        raw = io.open(p, 'rb').read()
    except OSError:
        return None, None, {}
    txt = raw.decode('utf-8', errors='replace')
    meta_p = p + '.meta.json'
    meta: dict = {}
    fetched = None
    if os.path.exists(meta_p):
        try:
            meta = json.load(io.open(meta_p, encoding='utf-8'))
            if not isinstance(meta, dict):
                meta = {}
            if meta.get('sha256') and meta['sha256'] != _sha256(raw):
                meta = {**meta, 'mismatch': True}
            elif meta.get('fetched_utc'):
                fetched = datetime.fromisoformat(str(meta['fetched_utc']))
                if fetched.tzinfo is None:
                    fetched = fetched.replace(tzinfo=timezone.utc)
        except (OSError, ValueError, TypeError, KeyError):
            meta, fetched = {}, None
    return txt, fetched, meta


def _write_cache(key: str, text: str, url: Optional[str] = None, extra: Optional[dict] = None) -> str:
    """Сначала meta (с SHA-256 данных), затем данные; каждый файл заменяется атомарно.
    Несовпадение SHA-256 при чтении означает, что пара неполная, и время получения неизвестно."""
    p = _cache_path(key)
    data = text.encode('utf-8')
    meta = {'fetched_utc': _now().isoformat(), 'url': url, 'sha256': _sha256(data), 'bytes': len(data), **(extra or {})}
    _atomic_write(p + '.meta.json', json.dumps(meta, ensure_ascii=False).encode('utf-8'))
    _atomic_write(p, data)
    return p


def _live(url: str) -> str:
    """Один живой запрос; любая ошибка — исключение наверх."""
    r = requests.get(url, timeout=TIMEOUT_S, headers={'User-Agent': 'vkd-risk/0.4 (hackathon prototype)'})
    r.raise_for_status()
    if not r.text.strip():
        raise ValueError('пустой ответ')
    return r.text


def _fmt_age(age_min: float) -> str:
    return '%.0f мин' % age_min if age_min < 180 else '%.1f ч' % (age_min / 60.0)


def _tle_snapshot(source_id: str, why: str) -> Optional[Fetch]:
    """Снимок TLE репозитория: data/orbit/iss.tle + запись манифеста A3 (время получения, адрес,
    SHA-256). Файл сверяется с манифестом; несовпадение — время получения неизвестно."""
    snap = os.path.join(ORBIT_SNAP_DIR, 'iss.tle')
    man_p = os.path.join(ORBIT_SNAP_DIR, 'manifest.json')
    if not os.path.exists(snap):
        return None
    try:
        raw = io.open(snap, 'rb').read()
        rec = next((r for r in json.load(io.open(man_p, encoding='utf-8')).get('records', []) if r.get('file') == 'iss.tle'), {}) \
            if os.path.exists(man_p) else {}
    except (OSError, ValueError, TypeError, AttributeError):
        raw, rec = io.open(snap, 'rb').read(), {}
    txt = raw.decode('ascii', errors='replace')
    sha = _sha256(raw)
    if rec.get('sha256') == sha and rec.get('fetched_utc'):
        fetched = datetime.fromisoformat(str(rec['fetched_utc']).replace('Z', '+00:00'))
        age = (_now() - fetched).total_seconds() / 60
        return Fetch(source_id, False, True, fetched, age,
                     '%s: снимок репозитория data/orbit/iss.tle, получен %s с %s (манифест A3, SHA-256 %s…), давность %s'
                     % (why, fetched.strftime('%Y-%m-%d %H:%MZ'), (rec.get('url') or '?').split('/')[2] if '://' in (rec.get('url') or '') else '?',
                        sha[:12], _fmt_age(age)),
                     txt, snap, url=rec.get('url'), raw_response_path=snap, raw_response_sha256=sha, fetched_basis='манифест A3')
    return Fetch(source_id, False, True, None, None,
                 '%s: снимок репозитория data/orbit/iss.tle без совпадающей записи манифеста — время получения неизвестно' % why,
                 txt, snap, url=None, raw_response_path=snap, raw_response_sha256=sha, fetched_basis='')


def _fallback(source_id: str, key: str, why: str, disabled: bool = False) -> Fetch:
    """Кеш с давностью, затем снимок репозитория, затем «данных нет». Никогда не «благоприятно»,
    никогда «давность 0» для неизвестного времени."""
    txt, fetched, meta = _read_cache(key)
    if txt is not None:
        p = _cache_path(key)
        if fetched is None:
            reason = 'meta-файл не соответствует данным' if meta.get('mismatch') else 'meta-файла нет или он не читается'
            return Fetch(source_id, False, True, None, None, '%s: кеш, время получения неизвестно (%s)' % (why, reason),
                         txt, p, url=None, fetched_basis='')
        age = (_now() - fetched).total_seconds() / 60
        return Fetch(source_id, False, True, fetched, age, '%s: кеш, получено %s, давность %s' % (why, fetched.strftime('%Y-%m-%d %H:%MZ'), _fmt_age(age)),
                     txt, p, url=meta.get('url'), raw_response_path=meta.get('raw_response_path'),
                     raw_response_sha256=meta.get('raw_response_sha256'), provider_time=meta.get('provider_time'), fetched_basis='meta кеша')
    if disabled:
        return Fetch(source_id, False, False, None, None, '%s, кеша нет — данных нет' % why, None, None)
    if key == 'iss.tle':
        f = _tle_snapshot(source_id, why)
        if f is not None:
            return f
    snap = os.path.join(SNAP, key)
    if os.path.exists(snap):
        fetched = SNAPSHOT_DOWNLOADED_UTC
        age = (_now() - fetched).total_seconds() / 60
        return Fetch(source_id, False, True, fetched, age,
                     '%s: снимок репозитория data/spaceweather/%s, скачан %s (README; время суток не записано), давность не меньше %s'
                     % (why, key, fetched.strftime('%d.%m.%Y'), _fmt_age(age)),
                     io.open(snap, encoding='utf-8').read(), snap, url=None, fetched_basis='README снимка')
    return Fetch(source_id, False, False, None, None, '%s, кеша нет — данных нет' % why, None, None)


def fetch_text(source_id: str, url: str, key: str, disabled: bool = False, force_live: bool = True) -> Fetch:
    """Живой запрос с кешем. disabled → только кеш, помечено (имитация отказа)."""
    if disabled:
        return _fallback(source_id, key, 'источник отключён', disabled=True)
    try:
        txt = _live(url)
    except Exception as e:            # noqa: BLE001 — любой отказ источника обрабатывается одинаково
        return _fallback(source_id, key, 'отказ источника (%s)' % type(e).__name__)
    p = _write_cache(key, txt, url=url)
    return Fetch(source_id, True, False, _now(), 0.0, 'получено живьём, HTTP 200', txt, p, url=url, fetched_basis='живой запрос')


def _unparsable(f: Fetch, e: BaseException) -> Fetch:
    return replace(f, ok=False, status_ru='ответ не разбирается (%s: %s)' % (type(e).__name__, str(e)[:80]), payload=None)


def _fetch_note(f: Fetch) -> dict:
    """Происхождение записи для сырой выгрузки: адрес, время и основание времени получения."""
    return {'url': f.url, 'fetched_utc': f.fetched_utc.isoformat() if f.fetched_utc else None,
            'fetched_basis': f.fetched_basis or ('неизвестно' if f.fetched_utc is None else ''), 'fetch_status': f.status_ru}


def _sample_fetched(f: Fetch) -> datetime:
    """EnvironmentSample требует время получения. Если оно неизвестно, берётся время записи файла
    кеша (единственное свидетельство), а в сырой записи fetched_utc остаётся None."""
    if f.fetched_utc is not None:
        return f.fetched_utc
    if f.raw_path and os.path.exists(f.raw_path):
        return datetime.fromtimestamp(os.path.getmtime(f.raw_path), tz=timezone.utc)
    return _now()


# ----------------------------------------------------------------- GOES
def _goes_sample(last: dict, f: Fetch) -> tuple[EnvironmentSample, dict]:
    t = datetime.fromisoformat(str(last['time_tag']).replace('Z', '+00:00'))
    rid = 'goes_p10#' + str(last['time_tag'])
    s = EnvironmentSample(t, 'goes_p_ge10MeV', float(last['flux']), 'pfu', 'noaa_swpc_goes', Kind.OBSERVATION,
                          None, None, None, _sample_fetched(f), 'preliminary', rid)
    return s, {rid: {**last, **_fetch_note(f)}}


def goes_latest(disabled: bool = False) -> tuple[Optional[EnvironmentSample], dict, Fetch]:
    f = fetch_text('noaa_swpc_goes', URLS['goes'], 'goes_protons_3day.json', disabled)
    if f.payload is None:
        return None, {}, f
    try:
        d = json.loads(f.payload)
        if not isinstance(d, list):
            raise TypeError('ожидался список записей, получен %s' % type(d).__name__)
        p10 = [x for x in d if isinstance(x, dict) and x.get('energy') == '>=10 MeV' and x.get('flux') is not None]
        if not p10:
            return None, {}, replace(f, ok=f.ok, status_ru='в ответе нет канала >=10 МэВ', payload=None)
        last = max(p10, key=lambda x: str(x['time_tag']))
        s, raw = _goes_sample(last, f)
    except _PARSE_ERRORS as e:
        return None, {}, _unparsable(f, e)
    return s, raw, f


# ----------------------------------------------------------------- Kp (GFZ)
def _kp_sample(dt_str: str, kp_val, status_val, f: Fetch, now: datetime) -> tuple[EnvironmentSample, dict, Fetch]:
    """datetime GFZ — НАЧАЛО трёхчасового интервала (сверено с файлом Kp за 10.05.2024: 15:00Z → 7,667 = Kp6).
    Интервал, не завершённый к моменту запроса, — предварительная оценка (nowcast), и это объявляется."""
    t = datetime.fromisoformat(str(dt_str).replace('Z', '+00:00'))
    vf, vt = t, t + timedelta(hours=3)
    rid = 'gfz_kp#' + str(dt_str)
    q = 'final' if (status_val is not None and str(status_val).lower().startswith('def')) else 'preliminary'
    running = vt > now
    s = EnvironmentSample(t, 'kp', float(kp_val), '', 'gfz_kp', Kind.OBSERVATION, None, vf, vt, _sample_fetched(f), q, rid)
    if running:
        f = replace(f, status_ru=f.status_ru + '; интервал %s–%s UTC не завершён — предварительная оценка GFZ, значение изменится'
                    % (vf.strftime('%H:%M'), vt.strftime('%H:%M')))
    raw = {rid: {'datetime': dt_str, 'Kp': kp_val, 'status': status_val, 'interval_from_utc': vf.isoformat(), 'interval_to_utc': vt.isoformat(),
                 'datetime_is': 'начало трёхчасового интервала', 'interval_running_at_request': running, **_fetch_note(f)}}
    return s, raw, f


def kp_latest(disabled: bool = False) -> tuple[Optional[EnvironmentSample], dict, Fetch]:
    end = _now(); start = end - timedelta(days=2)
    url = 'https://kp.gfz.de/app/json/?start=%s&end=%s&index=Kp' % (start.strftime('%Y-%m-%dT%H:%M:%SZ'), end.strftime('%Y-%m-%dT%H:%M:%SZ'))
    f = fetch_text('gfz_kp', url, 'kp_recent.json', disabled)
    if f.payload is None:
        return None, {}, f
    try:
        d = json.loads(f.payload)
        if not isinstance(d, dict):
            raise TypeError('ожидался объект с полями datetime/Kp, получен %s' % type(d).__name__)
        times, vals, status = d.get('datetime') or [], d.get('Kp') or [], d.get('status') or []
        if not times:
            return None, {}, replace(f, status_ru='ответ пуст: нет значений за двое суток', payload=None)
        if vals[-1] is None:
            raise ValueError('последнее значение Kp = null')
        s, raw, f2 = _kp_sample(times[-1], vals[-1], status[-1] if status else None, f, end)
    except _PARSE_ERRORS as e:
        return None, {}, _unparsable(f, e)
    return s, raw, f2


# ----------------------------------------------------------------- TLE
def tle_from_text(txt: Optional[str]) -> Optional[str]:
    """TLE МКС из ответа любого из резервных адресов → три строки; None, если не разобрать.
    CelesTrak отдаёт текст (имя + две строки); wheretheiss.at и tle.ivanstanojevic.me — JSON
    с полями line1/line2 (имя — header или name). Контрольные суммы и NORAD проверяет A3."""
    if not txt:
        return None
    t = txt.strip()
    if t.startswith('{') or t.startswith('['):
        try:
            d = json.loads(t)
        except ValueError:
            return None
        if isinstance(d, list):
            d = d[0] if d else {}
        if not isinstance(d, dict):
            return None
        l1, l2 = d.get('line1'), d.get('line2')
        if not (isinstance(l1, str) and isinstance(l2, str)):
            return None
        name = str(d.get('header') or d.get('name') or 'ISS (ZARYA)').strip()
        lines = [name[:24], l1.strip(), l2.strip()]
    else:
        lines = [l.rstrip() for l in t.splitlines() if l.strip()]
        if len(lines) == 2:
            lines = ['ISS (ZARYA)'] + lines
        if len(lines) > 3:                       # список станций: берём блок с 25544
            for i in range(len(lines) - 2):
                if lines[i + 1].startswith('1 25544') and lines[i + 2].startswith('2 25544'):
                    lines = lines[i:i + 3]
                    break
    if len(lines) != 3 or not lines[1].startswith('1 ') or not lines[2].startswith('2 '):
        return None
    if len(lines[1]) != 69 or len(lines[2]) != 69:
        return None
    return '%s\n%s\n%s\n' % (lines[0][:24], lines[1], lines[2])


def tle_provider_time(txt: Optional[str]) -> Optional[str]:
    """Время выпуска по данным поставщика, если оно есть в ответе (JSON: tle_timestamp — unix, date — ISO)."""
    if not txt or not txt.strip().startswith(('{', '[')):
        return None
    try:
        d = json.loads(txt)
        d = d[0] if isinstance(d, list) and d else d
        if not isinstance(d, dict):
            return None
        if d.get('tle_timestamp') is not None:
            return datetime.fromtimestamp(float(d['tle_timestamp']), tz=timezone.utc).isoformat()
        if d.get('date'):
            return str(d['date'])
    except (ValueError, TypeError, OSError, OverflowError):
        return None
    return None


def _tle_try(u: str):
    """Один адрес: (url, тело ответа | None, ошибка | None)."""
    try:
        return u, _live(u), None
    except Exception as e:        # noqa: BLE001
        return u, None, type(e).__name__


def tle_latest(disabled: bool = False) -> tuple[Optional[str], Fetch]:
    """Резервная цепочка адресов TLE (config [sources.urls].tle). Адреса опрашиваются параллельно,
    чтобы отказ первичного не стоил трёх тайм-аутов подряд; в расчёт идёт первый по порядку настроек
    разобранный ответ, адрес — в Fetch.url и далее в манифест орбиты. Тело ответа сохраняется как есть
    (Т2: прослеживаемость до исходной записи), нормализованный TLE — производная запись со ссылкой.
    Ни один адрес не ответил → кеш, затем снимок репозитория с манифестом A3."""
    urls = URLS['tle'] if isinstance(URLS['tle'], list) else [URLS['tle']]
    if disabled:
        f = _fallback('celestrak_gp', 'iss.tle', 'источник отключён', disabled=True)
        return tle_from_text(f.payload), f
    with ThreadPoolExecutor(max_workers=max(1, len(urls))) as ex:
        results = list(ex.map(_tle_try, urls))
    errors, chosen = [], None
    for u, body, err in results:
        if body is None:
            errors.append('%s: %s' % (u.split('/')[2], err))
            continue
        tle = tle_from_text(body)
        if tle is None:
            errors.append('%s: ответ не разобран' % u.split('/')[2])
            continue
        if chosen is None:
            chosen = (u, body, tle)
    if chosen is None:
        f = _fallback('celestrak_gp', 'iss.tle', 'отказ всех адресов TLE (%s)' % '; '.join(errors))
        return tle_from_text(f.payload), f
    u, body, tle = chosen
    host = u.split('/')[2]
    raw_key = 'tle_raw_%s.txt' % host.replace(':', '_')
    raw_p = _write_cache(raw_key, body, url=u)
    raw_sha = _sha256(body.encode('utf-8'))
    ptime = tle_provider_time(body)
    p = _write_cache('iss.tle', tle, url=u, extra={'raw_response_path': raw_p, 'raw_response_sha256': raw_sha, 'provider_time': ptime,
                                                   'derived': 'три строки TLE, нормализованные из ответа raw_response_path'})
    note = '' if not errors else '; не ответили: ' + ', '.join(errors)
    return tle, Fetch('celestrak_gp', True, False, _now(), 0.0,
                      'получено живьём с %s (ответ сохранён, SHA-256 %s…)%s' % (host, raw_sha[:12], note), tle, p, url=u,
                      raw_response_path=raw_p, raw_response_sha256=raw_sha, provider_time=ptime, fetched_basis='живой запрос')


# ----------------------------------------------------------------- наборы для конвейера
def fetch_all(disabled: Optional[dict] = None) -> tuple:
    """Три источника параллельно (Т6: ожидание при недоступной сети — один тайм-аут, а не сумма).
    Возвращает кортеж в форме app.compute.run(fetched=…)."""
    disabled = disabled or {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        fg = ex.submit(goes_latest, bool(disabled.get('goes')))
        fk = ex.submit(kp_latest, bool(disabled.get('kp')))
        ft = ex.submit(tle_latest, False)
        return fg.result(), fk.result(), ft.result()


def fetch_none(why: str = 'источники не запрашивались: исторический режим, входы — архив') -> tuple:
    """Кортеж «живые источники не запрашивались» для исторических режимов: GOES обнуляется,
    Kp берётся из архива, орбита — из OEM (Т6: без сети ждать нечего)."""
    off = lambda sid: Fetch(sid, False, False, None, None, why, None, None)   # noqa: E731
    return (None, {}, off('noaa_swpc_goes')), (None, {}, off('gfz_kp')), (None, off('celestrak_gp'))


def from_records(goes_rec: Optional[dict], kp_rec: Optional[dict], tle_text: Optional[str], now: datetime,
                 tle_rec: Optional[dict] = None) -> tuple:
    """Кортеж источников из сырых записей сохранённого расчёта (Т8: воспроизведение текущего режима
    без живых запросов). Статус каждого — «из сохранённого расчёта»; время получения — из записи."""
    def _t(rec):
        v = (rec or {}).get('fetched_utc')
        return datetime.fromisoformat(v) if v else None

    why = 'из сохранённого расчёта (воспроизведение)'
    goes = kp = None
    goes_raw = kp_raw = {}
    f_goes = Fetch('noaa_swpc_goes', False, True, _t(goes_rec), None, why + (': запись не сохранена' if not goes_rec else ''), None, None,
                   url=(goes_rec or {}).get('url'), fetched_basis='сохранённый расчёт')
    if goes_rec:
        goes, goes_raw = _goes_sample({k: goes_rec[k] for k in ('time_tag', 'satellite', 'flux', 'energy') if k in goes_rec}, f_goes)
        goes_raw = {k: {**v, 'fetch_status': why} for k, v in goes_raw.items()}
    f_kp = Fetch('gfz_kp', False, True, _t(kp_rec), None, why + (': запись не сохранена' if not kp_rec else ''), None, None,
                 url=(kp_rec or {}).get('url'), fetched_basis='сохранённый расчёт')
    if kp_rec:
        kp, kp_raw, f_kp = _kp_sample(kp_rec['datetime'], kp_rec['Kp'], kp_rec.get('status'), f_kp, now)
    f_tle = Fetch('celestrak_gp', False, True, _t(tle_rec) if tle_rec else None, None,
                  (tle_rec or {}).get('fetch') and ('%s; исходно: %s' % (why, tle_rec['fetch'])) or why,
                  tle_text, None, url=(tle_rec or {}).get('url'), fetched_basis='сохранённый расчёт')
    return (goes, goes_raw, f_goes), (kp, kp_raw, f_kp), (tle_text, f_tle)
