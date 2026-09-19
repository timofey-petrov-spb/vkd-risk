"""Hash-verified GFZ Kp cells for retrospective review, never strict replay.

The original GFZ header defines D=0 (preliminary Kp), D=1/2 (definitive Kp)
and -1.000 as missing. We preserve the three-hour cell; no interpolation.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math

from .registry import RegistryError, SourceRegistry, utc
from .goes_archive import interval_coverage
from vkd.types import EnvironmentSample, Kind

SOURCE_ID = 'gfz_kp_archive'
REGISTRY_PATH = 'data/gfz_2024/registry.json'
PARSER_VERSION = 'gfz-kp-archive-v1'


def parse_archive(raw: bytes, record: dict) -> dict:
    """Read GFZ's 28-column format; a malformed cell invalidates the release."""
    try:
        start, end = utc(record['valid_from_utc']), utc(record['valid_to_utc'])
        if record['source_id'] != SOURCE_ID or end <= start:
            raise ValueError('Invalid GFZ release identity or bounds')
        lines = raw.decode('ascii').splitlines()
        columns = [line.split() for line in lines if line.startswith('#YYY MM DD')]
        if len(columns) != 1 or columns[0][7:15] != [f'Kp{i}' for i in range(1, 9)] or columns[0][-1] != 'D':
            raise ValueError('Missing or incompatible GFZ column header')
        samples, missing = [], []
        previous = None
        for number, line in enumerate(lines, 1):
            if not line.strip() or line.startswith('#'):
                continue
            parts = line.split()
            if len(parts) != 28:
                raise ValueError(f'Line {number}: expected 28 columns')
            day = datetime(*(int(x) for x in parts[:3]), tzinfo=timezone.utc)
            if not start <= day or day + timedelta(days=1) > end or (previous and day <= previous):
                raise ValueError(f'Line {number}: outside bounds, duplicate or reversed date')
            previous = day
            flag = int(parts[27])
            if flag not in (0, 1, 2):
                raise ValueError(f'Line {number}: invalid D quality flag')
            for i, text in enumerate(parts[7:15]):
                a = day + timedelta(hours=3*i)
                b = a + timedelta(hours=3)
                value = float(text)
                if value == -1:
                    missing.append({'line': number, 'interval_index': i, 'reason': 'GFZ_missing_sentinel'})
                    continue
                if not math.isfinite(value) or not 0 <= value <= 9:
                    raise ValueError(f'Line {number}, Kp{i+1}: outside [0, 9]')
                # Published decimals are rounded thirds, with at most 0.0005 error.
                if abs(value*3-round(value*3)) > 0.00151:
                    raise ValueError(f'Line {number}, Kp{i+1}: not a rounded Kp third')
                samples.append(EnvironmentSample(b, 'kp', value, '1', SOURCE_ID,
                    Kind.OBSERVATION, None, a, b, utc(record['fetched_utc']),
                    'final' if flag in (1, 2) else 'preliminary',
                    record['raw_record_id'], record['version']))
        if previous is None:
            raise ValueError('Empty GFZ release')
        return {'samples': samples, 'missing': missing, 'parser_version': PARSER_VERSION}
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise RegistryError(f'Invalid GFZ Kp archive: {exc}') from exc


def archive_snapshot(registry: SourceRegistry, *, start_utc, end_utc, mode: str) -> dict:
    """Review accepts final data; strict forecasts explicitly exclude this archive.

    Local import time is NOT historical publication. Even an externally supplied
    publication field cannot promote this review-only product into a forecast.
    """
    start, end = utc(start_utc), utc(end_utc)
    if end <= start or mode not in ('history_review', 'history_forecast'):
        raise ValueError('Valid interval and historical mode required')
    result = {'samples': [], 'raw_records': {}, 'source_versions': {}, 'excluded': [], 'audit': []}
    seen = set()
    for record in registry.records(SOURCE_ID):
        if utc(record['valid_to_utc']) <= start or utc(record['valid_from_utc']) >= end:
            continue
        rid = record['raw_record_id']
        if mode == 'history_forecast':
            result['excluded'].append({'raw_record_id': rid,
                'reason': 'final_GFZ_index_without_historic_publication_review_only'})
            continue
        raw = registry.raw_bytes(rid)
        parsed = parse_archive(raw, record)
        selected = [s for s in parsed['samples'] if s.valid_from_utc < end and s.valid_to_utc > start]
        for s in selected:
            if s.valid_from_utc in seen:
                raise RegistryError('Overlapping GFZ releases: select a content version explicitly')
            seen.add(s.valid_from_utc)
        result['samples'].extend(selected)
        metadata = deepcopy(record)
        metadata.update(availability_proof=None, quality='per_cell_D_flag', parser_version=PARSER_VERSION)
        result['source_versions'].setdefault(SOURCE_ID, {})[rid] = metadata
        result['raw_records'][rid] = {'metadata': deepcopy(record), 'encoding': 'base64',
            'content_base64': base64.b64encode(raw).decode('ascii')}
        result['audit'].append({'raw_record_id': rid, 'missing': parsed['missing'],
            'selected_cells': len(selected), 'parser_version': PARSER_VERSION})
    result['samples'].sort(key=lambda s: s.t_utc)
    result['coverage'] = interval_coverage(result['samples'], start, end)
    result['coverage']['reason'] = ('historical_publication_not_proven' if mode == 'history_forecast'
        else 'union_of_GFZ_three_hour_cells; final_data_for_review_only')
    return result
