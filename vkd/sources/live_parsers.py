"""Validate provider bytes before caching; never manufacture observation intervals."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re

from .noaa import DAILY_3DAY, MONTHS, SOURCE_3DAY, parse_forecast
from .registry import iso_utc, utc


class LiveDataError(ValueError):
    """Successful HTTP response without usable, unambiguous provider data."""


def number(value, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LiveDataError('Expected a numeric measurement')
    value = float(value)
    if not math.isfinite(value) or value < 0 or (maximum is not None and value > maximum):
        raise LiveDataError('Measurement outside its physical/index range')
    return value


def _latest(rows):
    if not rows:
        raise LiveDataError('No eligible observations')
    latest = max(r['data_utc'] for r in rows)
    chosen = [r for r in rows if r['data_utc'] == latest]
    if len({(r['value'], r.get('satellite'), r['quality']) for r in chosen}) != 1:
        raise LiveDataError('Conflicting observations at the latest timestamp')
    return chosen[0]


def parse_goes(raw: bytes, now: datetime) -> dict:
    data = json.loads(raw)
    if not isinstance(data, list):
        raise LiveDataError('GOES response must be a list')
    rows, rejected = [], 0
    for row in data:
        if not isinstance(row, dict) or row.get('energy') != '>=10 MeV':
            continue
        try:
            t = utc(row['time_tag'])
            value = number(row['flux'])
            satellite = row['satellite']
            if isinstance(satellite, bool) or not isinstance(satellite, int) or satellite <= 0:
                raise LiveDataError('Invalid satellite identity')
            # This product has no documented quality flags. A changed schema
            # with flags needs a documented interpretation, not guessed 0=good.
            if any(k in row for k in ('quality', 'quality_flag', 'data_quality')):
                raise LiveDataError('Uninterpreted quality flag in GOES product')
            if t > now:
                raise LiveDataError('Future observation')
            rows.append(dict(data_utc=t, value=value, satellite=satellite,
                             quality='preliminary', selected=row))
        except (KeyError, ValueError, TypeError, OverflowError):
            rejected += 1
    result = _latest(rows)
    result.update(channel_id='goes_p_ge10MeV', unit='pfu', published_utc=None,
                  valid_from_utc=None, valid_to_utc=None, rejected_rows=rejected,
                  sampling_note='5-minute averaged flux; timestamp convention is not used to invent interval bounds')
    return result


def parse_kp(raw: bytes, now: datetime) -> dict:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise LiveDataError('GFZ response must be an object')
    times, values = data.get('datetime'), data.get('Kp')
    if not isinstance(times, list) or not isinstance(values, list) or len(times) != len(values):
        raise LiveDataError('GFZ time/value arrays are inconsistent')
    statuses = data.get('status', ['unknown'] * len(times))
    if not isinstance(statuses, list) or len(statuses) != len(times):
        raise LiveDataError('GFZ status array is inconsistent')
    rows, rejected, ongoing = [], 0, 0
    for t_raw, value_raw, status in zip(times, values, statuses):
        try:
            start = utc(t_raw)
            end = start + timedelta(hours=3)
            value = number(value_raw, 9)
            if abs(value * 3 - round(value * 3)) > .002:
                raise LiveDataError('Kp is not a third-unit index value')
            if start.hour % 3 or start.minute or start.second or start.microsecond:
                raise LiveDataError('Kp interval is not aligned to UTC 3h bins')
            if end > now:
                ongoing += 1
                continue  # ongoing nowcast is not a completed 3-hour observation
            if status not in ('pre', 'def', 'unknown'):
                raise LiveDataError('Unknown GFZ status')
            rows.append(dict(data_utc=end, value=value,
                             quality={'pre': 'preliminary', 'def': 'final', 'unknown': 'unknown'}[status],
                             valid_from_utc=start, valid_to_utc=end,
                             selected={'datetime': t_raw, 'Kp': value_raw, 'status': status}))
        except (ValueError, TypeError, OverflowError):
            rejected += 1
    result = _latest(rows)
    result.update(channel_id='kp', unit='1', published_utc=None,
                  rejected_rows=rejected, ongoing_rows=ongoing,
                  attribution=data.get('meta', data.get('metadata', {})))
    return result


def parse_tle(raw: bytes, now: datetime) -> dict:
    # Lazy import avoids sources -> orbit -> sources.registry import cycles.
    from vkd.orbit.trajectory import satellite_from_tle
    text = raw.decode('ascii')
    payload_format = 'tle_text'
    if text.lstrip().startswith(('{', '[')):
        data = json.loads(text)
        if not isinstance(data, dict):
            raise LiveDataError('TLE endpoint must return one JSON object')
        if data.get('error') or data.get('status') == 'error':
            raise LiveDataError('TLE endpoint returned an error object')
        for key in ('id', 'satelliteId'):
            if key in data and (isinstance(data[key], bool) or str(data[key]) != '25544'):
                raise LiveDataError('JSON satellite identity mismatch')
        line1, line2 = data.get('line1'), data.get('line2')
        name = data.get('header') or data.get('name') or 'ISS'
        if (not isinstance(name, str) or not name.strip()
                or any(c in name for c in '\r\n')
                or not all(isinstance(line, str) and not any(c in line for c in '\r\n')
                           for line in (line1, line2))):
            raise LiveDataError('Invalid JSON TLE fields')
        text = name.strip() + '\n' + line1 + '\n' + line2 + '\n'
        payload_format = 'tle_json'
    else:
        # The stations product can contain other satellites; select exactly one
        # ISS pair, while preserving the full original body as raw evidence.
        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        if len(lines) > 3:
            matches = [i for i, line in enumerate(lines[:-1])
                       if line.startswith('1 25544') and lines[i+1].startswith('2 25544')]
            if len(matches) != 1:
                raise LiveDataError('Missing or ambiguous ISS element set')
            i = matches[0]
            name = lines[i-1] if i and not lines[i-1].startswith(('1 ', '2 ')) else 'ISS'
            text = '\n'.join([name, lines[i], lines[i+1]]) + '\n'
            payload_format = 'tle_catalog'
    satellite = satellite_from_tle(text.encode('ascii'))
    sat = satellite.model
    if not (math.isfinite(sat.ecco) and 0 <= sat.ecco < 1
            and math.isfinite(sat.no_kozai) and sat.no_kozai > 0
            and math.isfinite(sat.inclo) and 0 <= sat.inclo <= math.pi):
        raise LiveDataError('Invalid orbital elements')
    error, position, velocity = sat.sgp4(sat.jdsatepoch, sat.jdsatepochF)
    if error or not all(math.isfinite(x) for x in (*position, *velocity)):
        raise LiveDataError('SGP4 rejects TLE at its epoch')
    epoch = satellite.epoch.utc_datetime()
    if epoch > now:
        raise LiveDataError('TLE epoch is in the future')
    tle_sha = hashlib.sha256(text.encode('ascii')).hexdigest()
    return dict(data_utc=epoch, published_utc=None, quality='model', norad_id=25544,
                payload_text=text,
                selected={'epoch_utc': iso_utc(epoch), 'norad_id': 25544,
                          'input_format': payload_format, 'parsed_tle_sha256': tle_sha})


def parse_noaa_live(raw: bytes, now: datetime) -> dict:
    text = raw.decode('utf-8')
    match = re.findall(r'^:Issued:\s+(\d{4}) (\w{3}) (\d{2}) (\d{2})(\d{2}) UTC\s*$', text, re.M)
    if len(match) != 1:
        raise LiveDataError('Missing/duplicate Issued header')
    y, month, day, hour, minute = match[0]
    issued = datetime(int(y), MONTHS[month], int(day), int(hour), int(minute), tzinfo=timezone.utc)
    if issued > now:
        raise LiveDataError('Future NOAA publication')
    channels = [('noaa_kp', '1', '3h')] + [(c, '%', '24h') for c in DAILY_3DAY.values()]
    record = dict(source_id=SOURCE_3DAY, published_utc=iso_utc(issued), raw_record_id='pending',
                  channels=[dict(channel_id=c, unit=u, temporal_resolution=r, kind='external_forecast',
                                 valid_from_utc=iso_utc(issued - timedelta(days=1)),
                                 valid_to_utc=iso_utc(issued + timedelta(days=4))) for c, u, r in channels])
    cells = parse_forecast(raw, record)
    return dict(data_utc=issued, published_utc=issued, quality='model', cells=cells,
                selected={'issued_utc': iso_utc(issued)},
                valid_from_utc=min(utc(c['valid_from_utc']) for c in cells),
                valid_to_utc=max(utc(c['valid_to_utc']) for c in cells))
