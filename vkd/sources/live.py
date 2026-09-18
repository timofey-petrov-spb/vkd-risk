"""A4 entry points compatible with the existing B application imports."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

from vkd.types import EnvironmentSample, Kind
from .live_cache import Fetch, Product, acquire, raw_record
from .live_parsers import parse_goes, parse_kp, parse_noaa_live, parse_tle
from .registry import utc

GOES_URL = 'https://services.swpc.noaa.gov/json/goes/primary/integral-protons-3-day.json'
TLE_URL = 'https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE'
NOAA_URL = 'https://services.swpc.noaa.gov/text/3-day-forecast.txt'


def _age(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError('max_age_min must be finite and positive')
    return float(value)


def _observation(product, disabled, kwargs):
    fetched = acquire(product, disabled=disabled, **kwargs)
    if fetched.payload is None:
        return None, raw_record(fetched), fetched
    p, m = fetched.parsed, fetched.metadata
    sample = EnvironmentSample(p['data_utc'], p['channel_id'], p['value'], p['unit'], product.source_id,
                               Kind.OBSERVATION, p['published_utc'], p.get('valid_from_utc'), p.get('valid_to_utc'),
                               fetched.fetched_utc, p['quality'], m['raw_record_id'], m['version'])
    return sample, raw_record(fetched), fetched


def goes_latest(disabled: bool = False, *, max_age_min=60, **kwargs):
    """GOES >=10 MeV, pfu; stale/invalid means None, never zero."""
    product = Product('noaa_swpc_goes', GOES_URL, parse_goes, _age(max_age_min), 300)
    return _observation(product, disabled, kwargs)


def kp_latest(disabled: bool = False, *, max_age_min=360, **kwargs):
    """Latest completed GFZ 3-hour interval; timestamp is its end."""
    now = utc(kwargs.pop('now', None) or datetime.now(timezone.utc))
    fmt = '%Y-%m-%dT%H:%M:%SZ'
    url = f'https://kp.gfz.de/app/json/?start={(now-timedelta(days=2)).strftime(fmt)}&end={now.strftime(fmt)}&index=Kp'
    product = Product('gfz_kp', url, parse_kp, _age(max_age_min), 900)
    return _observation(product, disabled, {**kwargs, 'now': now})


def tle_latest(disabled: bool = False, *, max_age_min=3*24*60, **kwargs):
    """Two-value B interface; NORAD 25544, checksum, SGP4 and epoch checks."""
    product = Product('celestrak_gp', TLE_URL, parse_tle, _age(max_age_min), 7200, strict_poll=True)
    fetched = acquire(product, disabled=disabled, **kwargs)
    return fetched.payload, fetched


def noaa_latest(disabled: bool = False, *, max_age_min=36*60, **kwargs):
    """Current 3-day bulletin, original intervals, plus raw proof and Fetch.

    Channel names match the B forecast consumer: kp_forecast, s1_prob_daily.
    All returned cells retain their original bounds; intersect with the requested
    window and calculate coverage in B. No daily-to-window probability scaling.
    """
    product = Product('noaa_swpc_3day_forecast', NOAA_URL, parse_noaa_live, _age(max_age_min), 3600)
    fetched = acquire(product, disabled=disabled, **kwargs)
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
