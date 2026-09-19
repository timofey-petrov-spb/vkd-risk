import base64
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path

import pytest

from vkd.sources.gfz_archive import SOURCE_ID, REGISTRY_PATH, archive_snapshot, parse_archive
from vkd.sources.registry import RegistryError, SourceRegistry, utc
from vkd.history import history_bundle, history_snapshot
from vkd.assess.cutoff import apply_cutoff
from vkd.types import Request, SCHEMA_VERSION
from tests.sources.helpers import write_archive

ROOT = Path(__file__).resolve().parents[2]


def archive():
    registry = SourceRegistry(ROOT, REGISTRY_PATH)
    record = registry.records(SOURCE_ID)[0]
    return registry, record, registry.raw_bytes(record['raw_record_id'])


def test_actual_cells_quality_end_times_and_independent_controls():
    registry, record, raw = archive()
    rows = parse_archive(raw, record)['samples']
    assert len(rows) == 488
    controls = {'2024-05-10T15:00Z': 3.667, '2024-05-10T18:00Z': 7.667, '2024-05-11T03:00Z': 9.0,
                '2024-05-12T21:00Z': 4.0}
    for date, value in controls.items():
        row = next(s for s in rows if s.t_utc == utc(date))
        assert row.value == value
    assert all(s.quality == 'final' and s.published_utc is None and s.unit == '1' for s in rows)
    assert all(s.t_utc == s.valid_to_utc and s.valid_to_utc-s.valid_from_utc == timedelta(hours=3) for s in rows)
    assert rows[0].valid_from_utc == utc('2024-05-01T00:00Z')
    assert rows[-1].valid_to_utc == utc('2024-07-01T00:00Z')
    assert hashlib.sha256(raw).hexdigest() == '2cb26056e449762ec828425f666bf1017a02ad47d747ffbe58ed4f21af771dd7'


def test_cross_check_24_cells_against_separate_official_json_format():
    _, record, raw = archive()
    rows = {s.valid_from_utc: s for s in parse_archive(raw, record)['samples']}
    folder = ROOT/'data/gfz_2024/validation'
    api_raw = (folder/'gfz_api_20240510_12.json').read_bytes()
    receipt = json.loads((folder/'receipt.json').read_text())
    assert hashlib.sha256(api_raw).hexdigest() == receipt['sha256']
    api = json.loads(api_raw)
    assert len(api['datetime']) == len(api['Kp']) == len(api['status']) == 24
    for date, value, status in zip(api['datetime'], api['Kp'], api['status']):
        assert rows[utc(date)].value == value and status == 'def'


def test_empty_and_wrong_headers_rejected():
    _, record, raw = archive()
    for content in (b'', raw.replace(b'Kp1 ', b'Bad ')):
        with pytest.raises(RegistryError):
            parse_archive(content, record)


def test_publication_unknown_never_promoted_and_exact_raw_hashes():
    reg, record, raw = archive()
    kw = dict(start_utc=utc('2024-05-10T01:00Z'), end_utc=utc('2024-05-11T09:00Z'))
    review = archive_snapshot(reg, mode='history_review', **kw)
    strict = archive_snapshot(reg, mode='history_forecast', **kw)
    assert review['coverage']['coverage_fraction'] == 1
    assert not strict['samples'] and not strict['raw_records'] and strict['excluded']
    saved = base64.b64decode(review['raw_records'][record['raw_record_id']]['content_base64'])
    assert saved == raw and hashlib.sha256(saved).hexdigest() == record['sha256']
    changed = dict(record, published_utc='2024-04-01T00:00Z')
    assert all(s.published_utc is None for s in parse_archive(raw, changed)['samples'])


def changed_row(raw, column, value):
    lines = raw.decode('ascii').splitlines()
    idx = next(i for i, line in enumerate(lines) if not line.startswith('#'))
    parts = lines[idx].split(); parts[column] = value
    lines[idx] = ' '.join(parts)
    return ('\n'.join(lines)+'\n').encode()


@pytest.mark.parametrize('column,value', [(7,'nan'), (7,'inf'), (7,'9.333'), (7,'-0.1'),
                                         (7,'1.5'), (27,'3'), (1,'13')])
def test_invalid_values_fail_closed(column, value):
    _, record, raw = archive()
    with pytest.raises(RegistryError):
        parse_archive(changed_row(raw, column, value), record)


def test_missing_cell_is_gap_and_quality_flag_is_respected(tmp_path):
    _, record, raw = archive()
    changed = changed_row(changed_row(raw, 7, '-1.000'), 27, '0')
    write_archive(tmp_path, [(record,changed)])
    reg = SourceRegistry(tmp_path, 'registry.json')
    result = archive_snapshot(reg, start_utc=utc('2024-05-01T00:00Z'),
        end_utc=utc('2024-05-02T00:00Z'), mode='history_review')
    assert len(result['samples']) == 7
    assert result['coverage']['coverage_fraction'] == 7/8
    assert all(s.quality == 'preliminary' for s in result['samples'])
    assert result['audit'][0]['missing']
    (tmp_path/'raw/0.txt').write_bytes(b'corrupted')
    with pytest.raises(RegistryError, match='SHA-256'):
        archive_snapshot(reg, start_utc=utc('2024-05-01T00:00Z'),
            end_utc=utc('2024-05-02T00:00Z'), mode='history_review')


def test_overlap_and_duplicate_dates_rejected(tmp_path):
    _, record, raw = archive()
    with pytest.raises(RegistryError):
        parse_archive(raw+raw, record)
    second = dict(record, raw_record_id=record['raw_record_id']+':another')
    write_archive(tmp_path, [(record,raw),(second,raw)])
    with pytest.raises(RegistryError, match='Overlapping'):
        archive_snapshot(SourceRegistry(tmp_path,'registry.json'), start_utc=utc('2024-05-01T00:00Z'),
            end_utc=utc('2024-05-02T00:00Z'), mode='history_review')


def test_request_coverage_delay_legacy_cutoff_and_snapshot_isolation():
    t = utc('2024-06-30T12:00Z')
    request = Request('history_review', t, 480, 1440, None, work_delay_min=180)
    result = history_snapshot(request)
    assert result['schema_version'] == SCHEMA_VERSION
    assert result['coverage_map'][SOURCE_ID+':observations']['coverage_fraction'] == 9/32
    assert result['coverage_map']['kp:observations']['coverage_fraction'] == 9/32
    strict = history_snapshot(replace(request, mode='history_forecast', cutoff_utc=t))
    assert not any(s.source_id == SOURCE_ID for s in strict['samples'])
    assert SOURCE_ID not in strict['source_versions']
    samples, events, raw = history_bundle()
    assert len([s for s in samples if s.source_id == SOURCE_ID]) == 488
    cut = apply_cutoff(samples, events, [], utc('2024-05-10T19:00Z'))
    assert not any(s.source_id == SOURCE_ID for s in cut.samples)
    assert any(s.source_id == 'nasa_donki_notification' for s in cut.samples)
    latest = max((s for s in samples if s.t_utc <= utc('2024-05-12T23:00Z')), key=lambda s:s.t_utc)
    assert latest.source_id == SOURCE_ID and latest.value == 4.0
    rid = next(iter(result['source_versions'][SOURCE_ID]))
    result['source_versions'][SOURCE_ID][rid]['sha256'] = 'mutated'
    assert history_snapshot(request)['source_versions'][SOURCE_ID][rid]['sha256'] != 'mutated'
