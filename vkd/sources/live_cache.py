"""Bounded acquisition, validated immutable receipts and a per-provider poll gate."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any
from uuid import uuid4

import requests

from .registry import iso_utc, utc

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE = ROOT / 'data/cache/sources'
MAX_BYTES = 4 * 1024 * 1024
PARSER_VERSION = 'live-a4-v1'


@dataclass(frozen=True)
class Fetch:
    # Existing B consumer interface; extra fields are optional audit information.
    source_id: str
    ok: bool
    from_cache: bool
    fetched_utc: datetime | None
    age_min: float | None
    status_ru: str
    payload: Any
    raw_path: str | None
    status: str = 'missing'
    error: str | None = None
    metadata: dict = field(default_factory=dict)
    parsed: dict = field(default_factory=dict)
    raw: bytes | None = None


@dataclass(frozen=True)
class Product:
    source_id: str
    url: str
    parser: Any
    max_age_min: float
    poll_seconds: int
    strict_poll: bool = False


def _atomic(path: Path, raw: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _json(path, data):
    _atomic(path, json.dumps(data, ensure_ascii=False, allow_nan=False).encode())


@contextmanager
def _gate(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / '.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _record(product, raw, parsed, fetched, headers, raw_path):
    sha = hashlib.sha256(raw).hexdigest()
    return dict(source_id=product.source_id, raw_record_id=f'{product.source_id}:{sha}',
                url=product.url, version=sha, sha256=sha, bytes=len(raw),
                published_utc=iso_utc(parsed['published_utc']) if parsed.get('published_utc') else None,
                available_utc=iso_utc(fetched), fetched_utc=iso_utc(fetched),
                data_utc=iso_utc(parsed['data_utc']), quality=parsed['quality'],
                availability_proof='Direct HTTP receipt of the exact stored entity bytes; not historical publication evidence',
                raw_path=str(raw_path) if raw_path else None, parser_version=PARSER_VERSION,
                http_headers={k: headers[k] for k in ('ETag', 'Last-Modified', 'Date') if k in headers},
                limitations=['Observation/epoch time is not publication time.',
                             'Retrieval date is not inferred from filesystem mtime.'])


def _write_receipt(folder, product, raw, parsed, fetched, headers):
    sha = hashlib.sha256(raw).hexdigest()
    path = folder / 'raw' / (sha + '.bin')
    # A corrupted object can be repaired only by independently received bytes.
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        _atomic(path, raw)
    rec = _record(product, raw, parsed, fetched, headers, path)
    name = fetched.strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid4().hex + '.json'
    _json(folder / 'receipts' / name, rec)
    return rec


def _read_receipt(folder, product, now):
    corrupt = False
    for path in sorted((folder / 'receipts').glob('*.json'), reverse=True)[:32]:
        try:
            rec = json.loads(path.read_bytes())
            sha = rec['sha256']
            if len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
                raise ValueError('Invalid digest')
            if (rec['source_id'] != product.source_id or rec['version'] != sha
                    or rec['raw_record_id'] != f'{product.source_id}:{sha}'):
                raise ValueError('Receipt identity mismatch')
            fetched = utc(rec['fetched_utc'])
            if fetched > now or utc(rec['available_utc']) != fetched:
                raise ValueError('Invalid receipt time')
            raw_path = folder / 'raw' / (sha + '.bin')
            if raw_path.stat().st_size > MAX_BYTES:
                raise ValueError('Oversized cache object')
            raw = raw_path.read_bytes()
            if len(raw) != rec['bytes'] or hashlib.sha256(raw).hexdigest() != sha:
                raise ValueError('Cache digest mismatch')
            parsed = product.parser(raw, fetched)
            published = iso_utc(parsed['published_utc']) if parsed.get('published_utc') else None
            if (iso_utc(parsed['data_utc']) != rec['data_utc']
                    or published != rec['published_utc'] or parsed['quality'] != rec['quality']):
                raise ValueError('Receipt/data timestamp mismatch')
            rec['raw_path'] = str(raw_path)
            return (raw, parsed, rec), corrupt
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            corrupt = True
    return None, corrupt


def _bundled_tle(product, now):
    """Pinned A3 receipt is a valid fallback, with its original retrieval time."""
    if product.source_id != 'celestrak_gp':
        return None
    try:
        records = json.loads((ROOT / 'data/orbit/manifest.json').read_bytes())['records']
        original = next(r for r in records if r['file'] == 'iss.tle')
        path = ROOT / 'data/orbit/iss.tle'
        raw = path.read_bytes()
        if len(raw) != original['bytes'] or hashlib.sha256(raw).hexdigest() != original['sha256']:
            return None
        fetched = utc(original['fetched_utc'])
        if fetched > now:
            return None
        parsed = product.parser(raw, fetched)
        rec = _record(product, raw, parsed, fetched, {}, path)
        rec['availability_proof'] = original['evidence']
        rec['cache_origin'] = 'bundled_verified_A3_receipt'
        return raw, parsed, rec
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return None


def _result(product, entry, now, *, origin, error=None, corrupt=False):
    notes = []
    labels = dict(live='получено по сети', cached='проверенный кеш', disabled='источник отключён',
                  fallback='отказ сети: проверенный кеш', cooldown='пауза запросов', busy='получение уже выполняется',
                  missing='данных нет', cache_unavailable='кеш недоступен')
    if entry is None:
        return Fetch(product.source_id, False, False, None, None,
                     labels.get(origin, origin) + ': пригодных данных нет' + (f' ({error})' if error else ''),
                     None, None, 'invalid_cache' if corrupt else origin, error)
    raw, parsed, rec = entry
    if 'cells' in parsed:
        parsed = {**parsed, 'cells': [{**cell, 'source_id': product.source_id,
                                      'raw_record_id': rec['raw_record_id']} for cell in parsed['cells']]}
    age = (now - parsed['data_utc']).total_seconds() / 60
    usable = 0 <= age <= product.max_age_min
    if 'valid_to_utc' in parsed and product.source_id == 'noaa_swpc_3day_forecast':
        usable = usable and parsed['valid_to_utc'] > now
    if parsed.get('rejected_rows'):
        notes.append(f"отброшено непригодных строк: {parsed['rejected_rows']}")
    if parsed.get('ongoing_rows'):
        notes.append('незавершённый Kp-nowcast исключён')
    if corrupt:
        notes.append('повреждённая запись кеша исключена')
    if not usable:
        notes.append('устарело: значение не используется')
    if error:
        notes.append(error)
    rec = {**rec, 'admissibility': {'max_age_min': product.max_age_min, 'data_age_min': age,
                                 'usable': usable}, 'parser_audit': {
                                     k: v for k, v in parsed.items() if k in ('rejected_rows', 'ongoing_rows', 'sampling_note', 'attribution')}}
    return Fetch(product.source_id, origin == 'live', origin != 'live', utc(rec['fetched_utc']), age,
                 f"{labels[origin]}, давность данных {age:.1f} мин" + ('; ' + '; '.join(notes) if notes else ''),
                 raw.decode('utf-8') if usable else None, rec['raw_path'],
                 origin if usable else 'stale', error, rec, parsed, raw)


def _retry_time(value, now, fallback):
    try:
        seconds = int(value)
        return now + timedelta(seconds=max(1, seconds))
    except (ValueError, TypeError, OverflowError):
        try:
            return max(now + timedelta(seconds=1), parsedate_to_datetime(value).astimezone(timezone.utc))
        except (ValueError, TypeError, OverflowError, AttributeError):
            return now + timedelta(seconds=fallback)


def acquire(product: Product, *, disabled=False, cache_dir=None, now=None, transport=None,
            force_refresh=False, use_bundled=True) -> Fetch:
    now = utc(now or datetime.now(timezone.utc))
    if not isinstance(disabled, bool) or not isinstance(force_refresh, bool):
        raise ValueError('disabled and force_refresh must be booleans')
    folder = Path(cache_dir or DEFAULT_CACHE) / product.source_id
    try:
        entry, corrupt = _read_receipt(folder, product, now)
        if entry is None and use_bundled:
            entry = _bundled_tle(product, now)
        if disabled:
            return _result(product, entry, now, origin='disabled', corrupt=corrupt)
        with _gate(folder) as locked:
            if not locked:
                return _result(product, entry, now, origin='busy', corrupt=corrupt)
            # Another process may have completed while this call opened the gate.
            current, bad = _read_receipt(folder, product, now)
            entry, corrupt = current or entry, corrupt or bad
            attempt = {}
            try:
                attempt = json.loads((folder / 'attempt.json').read_bytes())
                if utc(attempt['next_attempt_utc']) > now and (
                        product.strict_poll or attempt.get('error') or not force_refresh):
                    return _result(product, entry, now, origin='cooldown' if attempt.get('error') else 'cached',
                                   error=attempt.get('error'), corrupt=corrupt)
            except FileNotFoundError:
                pass
            except (ValueError, KeyError, TypeError):
                # A corrupt throttle record must not trigger a request burst.
                _json(folder / 'attempt.json', {'next_attempt_utc': iso_utc(now + timedelta(seconds=product.poll_seconds)),
                                               'error': 'invalid_poll_metadata'})
                return _result(product, entry, now, origin='cooldown', error='invalid_poll_metadata', corrupt=True)
            if (entry and (product.strict_poll or not force_refresh)
                    and (now - utc(entry[2]['fetched_utc'])).total_seconds() < product.poll_seconds):
                return _result(product, entry, now, origin='cached', corrupt=corrupt)
            # Reserve before I/O so crashes also respect the provider interval.
            next_time = now + timedelta(seconds=product.poll_seconds)
            _json(folder / 'attempt.json', dict(attempted_utc=iso_utc(now), next_attempt_utc=iso_utc(next_time), error='request_incomplete'))
            get = transport or requests.get
            error = None
            for attempt_index in range(1 if product.strict_poll else 2):
                retry = False
                try:
                    started = time.monotonic()
                    response = get(product.url, timeout=(3.05, 6), allow_redirects=False, stream=True,
                                   headers={'User-Agent': 'vkd-risk/0.4 scientific prototype'})
                    try:
                        status = response.status_code
                        if status != 200:
                            error = f'http_{status}'
                            retry = status in (500, 502, 503, 504) and not product.strict_poll
                            if status == 429 or response.headers.get('Retry-After'):
                                next_time = _retry_time(response.headers.get('Retry-After'), now, product.poll_seconds)
                                next_time = max(next_time, now + timedelta(seconds=product.poll_seconds))
                                retry = False
                        else:
                            chunks, total = [], 0
                            for chunk in response.iter_content(65536):
                                if time.monotonic() - started > 12:
                                    raise requests.Timeout('Total response deadline exceeded')
                                total += len(chunk)
                                if total > MAX_BYTES:
                                    raise ValueError('oversized_response')
                                chunks.append(chunk)
                            raw = b''.join(chunks)
                            if not raw.strip():
                                error = 'empty_response'
                            else:
                                parsed = product.parser(raw, now)
                                if entry and parsed['data_utc'] < entry[1]['data_utc']:
                                    error = 'regressed_response'
                                    break
                                fetched = now if transport is not None else datetime.now(timezone.utc)
                                try:
                                    rec = _write_receipt(folder, product, raw, parsed, fetched, response.headers)
                                except OSError:
                                    rec = _record(product, raw, parsed, fetched, response.headers, None)
                                    error = 'cache_write_failed'
                                _json(folder / 'attempt.json', dict(attempted_utc=iso_utc(now), next_attempt_utc=iso_utc(next_time), error=None))
                                return _result(product, (raw, parsed, rec), fetched, origin='live', error=error, corrupt=corrupt)
                    finally:
                        response.close()
                except requests.Timeout:
                    error, retry = 'timeout', not product.strict_poll
                except requests.RequestException:
                    error, retry = 'network_error', not product.strict_poll
                except (ValueError, KeyError, TypeError, OverflowError):
                    error = 'invalid_response'
                if not retry or attempt_index == 1:
                    break
                time.sleep(.25)
            _json(folder / 'attempt.json', dict(attempted_utc=iso_utc(now), next_attempt_utc=iso_utc(next_time), error=error))
            return _result(product, entry, now, origin='fallback', error=error, corrupt=corrupt)
    except OSError:
        return _result(product, locals().get('entry'), now, origin='cache_unavailable', error='cache_io_error')


def raw_record(fetch: Fetch) -> dict:
    """Self-contained JSON-safe byte evidence; no mutable cache pointer is needed."""
    if fetch.raw is None:
        return {}
    return {fetch.metadata['raw_record_id']: dict(metadata=fetch.metadata,
            encoding='base64', content_base64=base64.b64encode(fetch.raw).decode('ascii'),
            selected=fetch.parsed.get('selected'))}
