"""Offline audit: python -m vkd.sources.verify_goes_archive > coverage.json.

Run from the checkout root. Verifies every source byte before reporting coverage.
No network, flux interpolation, quality promotion or historical publication claim.
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta
import json
from pathlib import Path

from .goes_archive import SOURCE_ID, REGISTRY_PATH, archive_snapshot, interval_coverage
from .registry import SourceRegistry, iso_utc, utc


def coverage_report(root: str | Path) -> dict:
    registry = SourceRegistry(root, REGISTRY_PATH)
    snapshot = archive_snapshot(registry, start_utc='2024-04-29T00:00Z',
        end_utc='2024-07-03T00:00Z', mode='history_review')
    samples = snapshot['samples']
    records = registry.records(SOURCE_ID)
    by_day = {}
    for sample in samples:
        by_day.setdefault(sample.t_utc.date().isoformat(), []).append(sample)
    daily = []
    for record in sorted(records, key=lambda r: r['valid_from_utc']):
        selected = by_day.get(record['release_id'], [])
        peak = max(selected, key=lambda s: s.value) if selected else None
        daily.append({'date': record['release_id'], 'raw_record_id': record['raw_record_id'],
            'sha256': record['sha256'], 'accepted_samples': len(selected),
            'coverage': interval_coverage(selected, record['valid_from_utc'], record['valid_to_utc']),
            'spacecraft_counts': record['spacecraft_counts'],
            'maximum_p10_pfu': peak.value if peak else None,
            'maximum_time_utc': iso_utc(peak.t_utc) if peak else None})
    controls = []
    for date in ['2024-05-01T00:00Z', '2024-05-03T12:00Z', '2024-05-10T19:00Z',
                 '2024-05-20T12:00Z', '2024-06-01T12:00Z', '2024-06-25T00:00Z',
                 '2024-06-30T23:59Z']:
        start = utc(date)
        controls.append(interval_coverage(samples, start, start+timedelta(hours=32)))
    rejected = Counter(item['reason'] for audit in snapshot['record_audit'] for item in audit['rejected'])
    return {'schema_version': 1, 'parser_version': snapshot['parser_version'],
        'source_id': SOURCE_ID, 'daily_releases': len(records), 'accepted_samples': len(samples),
        'rejected_rows_by_reason': dict(rejected), 'padded_interval': snapshot['coverage'],
        'may_june': interval_coverage(samples, '2024-05-01T00:00Z', '2024-07-01T00:00Z'),
        'controls_32_hours': controls, 'daily': daily,
        'interpretation': 'Temporal availability of archived numerical P10 only; quality unknown; historical forecast ineligible.'}


if __name__ == '__main__':
    print(json.dumps(coverage_report(Path(__file__).resolve().parents[2]),
                     ensure_ascii=False, indent=2, allow_nan=False))
