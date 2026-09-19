"""A4 entry points compatible with the existing B application imports."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import replace
import math
from urllib.parse import urlsplit

from vkd.types import EnvironmentSample, Kind
from .live_cache import Fetch, Product, acquire, raw_record, source_mode
from .live_parsers import parse_goes, parse_kp, parse_noaa_live, parse_tle
from .registry import utc

GOES_URL = 'https://services.swpc.noaa.gov/json/goes/primary/integral-protons-3-day.json'
TLE_URL = 'https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE'
NOAA_URL = 'https://services.swpc.noaa.gov/text/3-day-forecast.txt'


def _age(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError('max_age_min must be finite and positive')
    return float(value)


def _configured(product, key, *, endpoint=None):
    # Import at call time; module import must not freeze deployment settings.
    from vkd.config import section
    cfg = section('sources')
    urls = cfg.get('urls', {})
    if not isinstance(urls, dict):
        raise ValueError('sources.urls must be a table')
    url = urls.get(key, product.url) if endpoint is None else endpoint
    if not isinstance(url, str) or urlsplit(url).scheme != 'https' or not urlsplit(url).hostname:
        raise ValueError(f'sources.urls.{key} must be an absolute HTTPS URL')
    timeout = _age(cfg.get('timeout_s', product.read_timeout_s))
    if timeout > 30:
        raise ValueError('sources.timeout_s must not exceed 30 seconds')
    ttl = _age(cfg.get('cache_ttl_s', product.poll_seconds))
    return replace(product, url=url, read_timeout_s=timeout,
                   poll_seconds=max(product.poll_seconds, math.ceil(ttl)))


def _observation(product, disabled, kwargs):
    fetched = acquire(product, disabled=disabled, **kwargs)
    if fetched.payload is None:
        return None, raw_record(fetched), fetched
    p, m = fetched.parsed, fetched.metadata
    sample = EnvironmentSample(p['data_utc'], p['channel_id'], p['value'], p['unit'], product.source_id,
                               Kind.OBSERVATION, p['published_utc'], p.get('valid_from_utc'), p.get('valid_to_utc'),
                               fetched.fetched_utc, p['quality'], m['raw_record_id'], m['version'])
    return sample, raw_record(fetched), fetched


def goes_latest(disabled: bool | str = False, *, max_age_min=60, **kwargs):
    """GOES >=10 MeV, pfu; stale/invalid means None, never zero."""
    product = Product('noaa_swpc_goes', GOES_URL, parse_goes, _age(max_age_min), 300)
    return _observation(_configured(product, 'goes'), disabled, kwargs)


def kp_latest(disabled: bool | str = False, *, max_age_min=360, **kwargs):
    """Latest completed GFZ 3-hour interval; timestamp is its end."""
    now = utc(kwargs.pop('now', None) or datetime.now(timezone.utc))
    fmt = '%Y-%m-%dT%H:%M:%SZ'
    url = f'https://kp.gfz.de/app/json/?start={(now-timedelta(days=2)).strftime(fmt)}&end={now.strftime(fmt)}&index=Kp'
    product = Product('gfz_kp', url, parse_kp, _age(max_age_min), 900)
    return _observation(_configured(product, 'kp'), disabled, {**kwargs, 'now': now})


def tle_latest(disabled: bool | str = False, *, max_age_min=3*24*60, **kwargs):
    """Ordered independent endpoints; exact raw receipt plus validated TLE text."""
    from vkd.config import section
    mode = source_mode(disabled)
    configured_urls = section('sources').get('urls', {})
    if not isinstance(configured_urls, dict):
        raise ValueError('sources.urls must be a table')
    urls = configured_urls.get('tle', TLE_URL)
    urls = [urls] if isinstance(urls, str) else urls
    if not isinstance(urls, list) or not urls or len(urls) > 8:
        raise ValueError('sources.urls.tle must be an HTTPS URL or a nonempty list of at most 8 URLs')
    product = Product('celestrak_gp', TLE_URL, parse_tle, _age(max_age_min), 7200, strict_poll=True)
    products = [_configured(product, 'tle', endpoint=url) for url in urls]
    attempts, results = [], []
    # Freeze one reference time across fallbacks and cache selection.
    now = utc(kwargs.pop('now', None) or datetime.now(timezone.utc))
    for configured in products:
        fetched = acquire(configured, disabled=disabled, now=now, **kwargs)
        results.append(fetched)
        attempts.append(dict(url=configured.url, status=fetched.status,
                             error=fetched.error, usable=fetched.payload is not None))
        if mode == 'off' or (mode == 'on' and fetched.payload is not None and not fetched.error):
            break
    usable = [f for f in results if f.payload is not None]
    if mode == 'on' and results[-1].payload is not None and not results[-1].error:
        chosen = results[-1]
    elif usable:
        chosen = max(usable, key=lambda f: (f.parsed['data_utc'], f.fetched_utc))
    else:
        # Preserve stale payload provenance even when another endpoint had no bytes.
        chosen = next((f for f in results if f.raw is not None), results[0])
    failures = [a for a in attempts if a['error'] or not a['usable']]
    if mode == 'off':
        return None, chosen
    metadata = {**chosen.metadata, 'acquisition_attempts': attempts}
    note = '; '.join(f"{a['url']}: {a['error'] or a['status']}" for a in failures)
    chosen = replace(chosen, metadata=metadata,
                     status_ru=chosen.status_ru + ('; проверка адресов: ' + note if note else ''),
                     error=chosen.error or (note if failures else None))
    return chosen.payload, chosen


def noaa_latest(disabled: bool | str = False, *, max_age_min=36*60, **kwargs):
    """Current 3-day bulletin, original intervals, plus raw proof and Fetch.

    Channel names match the B forecast consumer: kp_forecast, s1_prob_daily.
    All returned cells retain their original bounds; intersect with the requested
    window and calculate coverage in B. No daily-to-window probability scaling.
    """
    product = Product('noaa_swpc_3day_forecast', NOAA_URL, parse_noaa_live, _age(max_age_min), 3600)
    fetched = acquire(_configured(product, 'noaa'), disabled=disabled, **kwargs)
    raw = raw_record(fetched)
    if fetched.payload is None:
        return (), raw, fetched
    names = {'noaa_kp': 'kp_forecast', 's1_or_greater_probability': 's1_prob_daily'}
    samples = tuple(EnvironmentSample(
        utc(c['valid_from_utc']), names[c['channel_id']], c['value'], c['unit'], product.source_id,
        Kind.EXTERNAL_FORECAST, utc(c['published_utc']), utc(c['valid_from_utc']), utc(c['valid_to_utc']),
        fetched.fetched_utc, 'model', fetched.metadata['raw_record_id'], fetched.metadata['version'])
        for c in fetched.parsed['cells'] if c['channel_id'] in names)
    return samples, raw, fetched


__all__ = ['Fetch', 'goes_latest', 'kp_latest', 'tle_latest', 'noaa_latest']
