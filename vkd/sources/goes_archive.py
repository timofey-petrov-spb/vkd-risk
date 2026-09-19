"""Auditable offline GOES primary-stream observations from NASA iSWA HAPI.

Five-minute averages are observations, not five-minute hazard predictions.
This archived response has no historic publication receipt: review only.
No flux interpolation, filling of gaps or joining of two primary satellites.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import csv
from datetime import timedelta
import io
import json
import math
import re

from vkd.sources.registry import RegistryError, SourceRegistry, iso_utc, utc
from vkd.types import EnvironmentSample, Kind

SOURCE_ID = 'nasa_iswa_goes_primary_p5m'
SCHEMA_ID = SOURCE_ID + '_schema'
CHANNEL_ID = 'goes_p_ge10MeV'
REGISTRY_PATH = 'data/goes_2024/registry.json'
PARSER_VERSION = 'iswa-p5m-v1'
PERIOD = timedelta(minutes=5)
KNOWN_PARAMETERS = {'Time', 'P1', 'P5', 'P10', 'P30', 'P50', 'P60', 'P100', 'P500',
                    'E_8', 'E2_0', 'E4_0', 'satelliteElectron', 'satelliteProton'}


def parse_day(raw: bytes, schema: dict, record: dict) -> dict:
    """Read the named P10 column and retain each row's proton spacecraft.

Negative/fill/nonfinite fluxes are missing, never zero. Malformed records,
wrong product schemas and conflicting/nonmonotonic timestamps fail closed.
"""
    try:
        parameters = schema['parameters']
        names = [p['name'] for p in parameters]
        if len(set(names)) != len(names) or not {'Time', 'P10', 'satelliteProton'} <= set(names):
            raise ValueError('Missing or duplicate HAPI parameters')
        if set(names)-KNOWN_PARAMETERS:
            raise ValueError('Unrecognized HAPI fields; inspect schema and potential quality flags')
        proton = parameters[names.index('P10')]
        timestamp = parameters[names.index('Time')]
        spacecraft = parameters[names.index('satelliteProton')]
        if (proton['type'] != 'double' or proton['units'] != 'Protons/cm^2 - s^-1 - sr'
                or timestamp['type'] != 'isotime' or timestamp['units'] != 'UTC'
                or spacecraft['type'] != 'string' or schema['status']['code'] != 1200):
            raise ValueError('Unexpected HAPI P10 units, type or status')
        fill = str(proton['fill'])
        start, end = utc(record['valid_from_utc']), utc(record['valid_to_utc'])
        if end <= start or any(t.second or t.microsecond or t.minute % 5 for t in (start, end)):
            raise ValueError('Release bounds must follow the five-minute grid')
        samples, rejected, satellites = [], [], {}
        previous = None
        for number, row in enumerate(csv.reader(io.StringIO(raw.decode('utf-8'))), 1):
            if len(row) != len(names):
                raise ValueError(f'Row {number}: wrong number of HAPI columns')
            values = dict(zip(names, row))
            t = utc(values['Time'])
            if t.second or t.microsecond or t.minute % 5 or not start <= t or t+PERIOD > end:
                raise ValueError(f'Row {number}: outside requested five-minute grid')
            if previous is not None and t <= previous:
                raise ValueError(f'Row {number}: duplicate or nonmonotonic timestamp')
            previous = t
            satellite = values['satelliteProton']
            value_text = values['P10'].strip()
            if value_text in ('', 'null', fill):
                rejected.append({'t_utc': iso_utc(t), 'reason': 'fill_or_missing'})
                continue
            try:
                flux_pfu = float(value_text)
            except ValueError as exc:
                raise ValueError(f'Row {number}: nonnumeric P10') from exc
            if not math.isfinite(flux_pfu) or flux_pfu < 0:
                rejected.append({'t_utc': iso_utc(t), 'reason': 'nonfinite_or_negative'})
                continue
            if re.fullmatch(r'GOES-\d{1,2}', satellite) is None:
                rejected.append({'t_utc': iso_utc(t), 'reason': 'unknown_proton_spacecraft'})
                continue
            satellites[satellite] = satellites.get(satellite, 0) + 1
            samples.append(EnvironmentSample(
                t_utc=t, channel_id=CHANNEL_ID, value=flux_pfu, unit='pfu',
                source_id=SOURCE_ID, kind=Kind.OBSERVATION, published_utc=None,
                valid_from_utc=t, valid_to_utc=t+PERIOD,
                fetched_utc=utc(record['fetched_utc']), quality='unknown',
                raw_record_id=record['raw_record_id'], version=record['version']))
        return {'samples': samples, 'rejected': rejected, 'satellites': satellites,
                'parser_version': PARSER_VERSION, 'quality_flags_present': False}
    except (KeyError, TypeError, UnicodeDecodeError, csv.Error, ValueError) as exc:
        raise RegistryError(f'Invalid iSWA GOES release: {exc}') from exc


def interval_coverage(samples, start, end) -> dict:
    """Union of actual averaging cells, clipped to the requested interval."""
    start, end = utc(start), utc(end)
    if end <= start:
        raise ValueError('Coverage end must be after start')
    cursor, gaps = start, []
    for sample in sorted(samples, key=lambda s: s.valid_from_utc):
        a, b = max(start, sample.valid_from_utc), min(end, sample.valid_to_utc)
        if b <= a:
            continue
        if a > cursor:
            gaps.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < end:
        gaps.append((cursor, end))
    total = (end-start).total_seconds()
    covered = total - sum((b-a).total_seconds() for a, b in gaps)
    return {'status': 'full' if not gaps else ('partial' if covered else 'missing'),
            'coverage_fraction': covered/total, 'covered_seconds': covered,
            'valid_from_utc': iso_utc(start), 'valid_to_utc': iso_utc(end),
            'gaps': [{'valid_from_utc': iso_utc(a), 'valid_to_utc': iso_utc(b)} for a, b in gaps],
            'kind': 'observation', 'cadence_seconds': 300,
            'coverage_basis': 'valid five-minute averaging cells; no interpolation',
            'quality': 'unknown', 'quality_flags_present': False,
            'strict_replay_eligible': False,
            'limitations': ['iSWA archive omits per-value quality flags and historic publication receipts.',
                            'Full temporal coverage does not establish instrument quality or an EVA recommendation.']}


def _attach(result, registry, record):
    rid = record['raw_record_id']
    if rid in result['raw_records']:
        return
    raw = registry.raw_bytes(rid)
    metadata = deepcopy(record)
    metadata.update(quality='unknown', availability_proof=None)
    result['source_versions'].setdefault(record['source_id'], {})[rid] = metadata
    result['raw_records'][rid] = {'metadata': deepcopy(metadata), 'encoding': 'base64',
                                 'content_base64': base64.b64encode(raw).decode('ascii')}


def archive_snapshot(registry: SourceRegistry, *, start_utc, end_utc,
                     mode: str, cutoff_utc=None) -> dict:
    """Data plus exact source responses. No network or shared mutable caches.

    Even observations before cutoff are excluded from historical forecasts:
    a 2026 archive download does not prove availability of its version in 2024.
    """
    if mode not in ('history_review', 'history_forecast'):
        raise ValueError('Historical mode required')
    start, end = utc(start_utc), utc(end_utc)
    if end <= start:
        raise ValueError('Archive end must be after start')
    if mode == 'history_forecast':
        if cutoff_utc is None:
            raise ValueError('Forecast cutoff required')
        utc(cutoff_utc)
    elif cutoff_utc is not None:
        raise ValueError('Review has no publication cutoff')
    result = {'samples': [], 'raw_records': {}, 'source_versions': {}, 'excluded': [],
              'record_audit': [], 'parser_version': PARSER_VERSION}
    records = sorted(registry.records(SOURCE_ID), key=lambda r: r['valid_from_utc'])
    seen_releases = set()
    for record in records:
        if utc(record['valid_from_utc']) >= end or utc(record['valid_to_utc']) <= start:
            continue
        if mode == 'history_forecast':
            result['excluded'].append({'raw_record_id': record['raw_record_id'],
                'reason': 'historic_publication_and_version_availability_not_proven'})
            continue
        # Multiple responses for the same date need an explicit version policy;
        # do not double-count samples or silently choose the latest one.
        if record['release_id'] in seen_releases:
            raise RegistryError('Conflicting versions of an iSWA daily release')
        seen_releases.add(record['release_id'])
        schema_record = registry.record(record['schema_record_id'])
        if schema_record['source_id'] != SCHEMA_ID:
            raise RegistryError('Unexpected GOES schema source')
        schema = json.loads(registry.raw_bytes(schema_record['raw_record_id']))
        parsed = parse_day(registry.raw_bytes(record['raw_record_id']), schema, record)
        selected = [s for s in parsed['samples'] if s.valid_from_utc < end and s.valid_to_utc > start]
        result['samples'].extend(selected)
        result['record_audit'].append({'raw_record_id': record['raw_record_id'],
            'satellites': parsed['satellites'], 'rejected': parsed['rejected'],
            'accepted_samples': len(parsed['samples'])})
        _attach(result, registry, schema_record)
        _attach(result, registry, record)
    result['samples'].sort(key=lambda s: s.t_utc)
    if len({s.t_utc for s in result['samples']}) != len(result['samples']):
        raise RegistryError('Overlapping GOES releases produced duplicate observations')
    result['coverage'] = interval_coverage(result['samples'], start, end)
    if mode == 'history_forecast':
        result['coverage']['reason'] = 'historic_publication_and_version_availability_not_proven'
    return result
