"""Explicit acquisition command; never called by the app or offline replay.

python -m vkd.sources.acquire_goes_archive --output data/goes_2024
Writes exact HAPI responses and the A1 index only after every day validates.
Existing archives are never overwritten. No additional runtime dependencies.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import requests

from .goes_archive import CHANNEL_ID, SCHEMA_ID, SOURCE_ID, parse_day
from .registry import iso_utc

BASE = 'https://iswa.gsfc.nasa.gov/IswaSystemWebApp/hapi/'
DATASET = 'goesp_part_flux_P5M'
INFO_URL = BASE + 'info?id=' + DATASET
ATBD_URL = ('https://www.ngdc.noaa.gov/stp/space-weather/online-publications/'
            'stp_sii/spades/algorithm-theoretic-basis-documents/atbds_seiss/'
            'atbd_seiss18_integral-flux_v1-0.pdf')


def _get(url):
    with requests.get(url, timeout=(3.05, 30), stream=True,
            headers={'User-Agent': 'vkd-risk/archive-research', 'Accept-Encoding': 'identity'}) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 2*1024*1024:
                raise ValueError('Unexpectedly large daily archive response')
            chunks.append(chunk)
        content = b''.join(chunks)
        return content, {key: response.headers.get(key) for key in
                         ('Date', 'Last-Modified', 'ETag', 'Content-Type')}, iso_utc(datetime.now(timezone.utc))


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode()


def acquire(root: Path, relative_output: str, first: date, stop: date):
    if stop <= first or (stop-first).days > 100:
        raise ValueError('Expected 1–100 days, end date exclusive')
    root = root.resolve()
    output = (root/relative_output).resolve()
    if not output.is_relative_to(root):
        raise ValueError('Output must be inside repository')
    if output.exists():
        raise FileExistsError('Archive exists; use a separate version directory')
    raw_info, info_headers, info_fetched = _get(INFO_URL)
    schema = json.loads(raw_info)
    prefix = output.relative_to(root).as_posix()
    records, payloads = [], {}

    def record(content, *, source, release, url, relative_path, fetched, headers, start=None, end=None):
        sha = hashlib.sha256(content).hexdigest()
        return {'source_id': source, 'raw_record_id': f'{source}:{release}:{sha}',
            'release_id': release, 'version': 'sha256:'+sha, 'sha256': sha,
            'raw_path': prefix+'/'+relative_path, 'bytes': len(content), 'url': url,
            'fetched_utc': fetched, 'created_utc': None, 'published_utc': None,
            'available_utc': None, 'valid_from_utc': start, 'valid_to_utc': end,
            'strict_replay_eligibility': 'excluded_unknown_historical_publication_and_version_availability',
            'availability_evidence': None, 'http_headers': headers,
            'channels': ([{'channel_id': CHANNEL_ID, 'unit': 'pfu', 'parameter': 'P10',
                          'energy_threshold_MeV': 10, 'kind': 'observation'}] if start else []),
            'quality': 'unknown', 'provider_dataset': DATASET,
            'limitations': ['Archive retrieval is not a historical delivery receipt.',
                            'No instrument quality flags are supplied by this HAPI dataset.']}

    info_record = record(raw_info, source=SCHEMA_ID, release='hapi-info', url=INFO_URL,
        relative_path='raw/hapi-info.json', fetched=info_fetched, headers=info_headers)
    records.append(info_record); payloads[info_record['raw_path']] = raw_info

    def fetch_day(day):
        next_day = day+timedelta(days=1)
        a, b = day.isoformat()+'T00:00:00Z', next_day.isoformat()+'T00:00:00Z'
        url = BASE+f'data?id={DATASET}&time.min={a}&time.max={b}&format=csv'
        raw, headers, fetched = _get(url)
        item = record(raw, source=SOURCE_ID, release=day.isoformat(), url=url,
            relative_path='raw/'+day.isoformat()+'.csv', fetched=fetched, headers=headers, start=a, end=b)
        item.update(schema_record_id=info_record['raw_record_id'], parser_version='iswa-p5m-v1',
            averaging_interval_seconds=300, timestamp_semantics='start_of_averaging_period',
            timestamp_reference=ATBD_URL+' (Table 7, page 37)')
        audit = parse_day(raw, schema, item)
        item.update(accepted_sample_count=len(audit['samples']), rejected_sample_count=len(audit['rejected']),
                    spacecraft_counts=audit['satellites'])
        return item, raw

    dates = [first+timedelta(days=i) for i in range((stop-first).days)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for item, raw in pool.map(fetch_day, dates):
            records.append(item); payloads[item['raw_path']] = raw
    # No incomplete index is written when retrieval/validation fails.
    output.mkdir(parents=True)
    (output/'raw').mkdir()
    (output/'.gitattributes').write_text(
        '*.json text eol=lf\n*.md text eol=lf\nraw/** -text\n', encoding='utf-8')
    for path, raw in payloads.items():
        (root/path).write_bytes(raw)
    records_path = prefix+'/records.json'
    records_raw = _json_bytes({'schema_version': 1, 'records': records})
    (root/records_path).write_bytes(records_raw)
    sources = {}
    for i, item in enumerate(records):
        source = sources.setdefault(item['source_id'], {'records_path': records_path,
            'records_sha256': hashlib.sha256(records_raw).hexdigest(), 'record_count': 0, 'records': {}})
        source['records'][item['raw_record_id']] = {'record_index': i,
            'release_id': item['release_id'], 'sha256': item['sha256']}
        source['record_count'] += 1
    (output/'registry.json').write_bytes(_json_bytes({'schema_version': 1,
        'record_count': len(records), 'sources': sources}))
    return {'records': len(records), 'bytes': sum(map(len, payloads.values())),
            'first': first.isoformat(), 'end_exclusive': stop.isoformat()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', default='data/goes_2024')
    parser.add_argument('--first', type=date.fromisoformat, default=date(2024, 4, 29))
    parser.add_argument('--stop', type=date.fromisoformat, default=date(2024, 7, 3))
    args = parser.parse_args()
    print(json.dumps(acquire(args.root, args.output, args.first, args.stop), ensure_ascii=False))
