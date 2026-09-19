import base64
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests.sources.helpers import write_archive
from vkd.history import history_snapshot, HistoryDataError
from vkd.sources.goes_archive import (SOURCE_ID, SCHEMA_ID, CHANNEL_ID, REGISTRY_PATH,
    archive_snapshot, interval_coverage, parse_day)
from vkd.sources.registry import RegistryError, SourceRegistry, utc
from vkd.types import Kind, Request

ROOT = Path(__file__).resolve().parents[2]


class GoesArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = SourceRegistry(ROOT, REGISTRY_PATH)
        cls.records = {r['release_id']: r for r in cls.registry.records(SOURCE_ID)}
        cls.record = cls.records['2024-05-10']
        cls.schema_record = cls.registry.records(SCHEMA_ID)[0]
        cls.schema_raw = cls.registry.raw_bytes(cls.schema_record['raw_record_id'])
        cls.schema = json.loads(cls.schema_raw)
        cls.raw = cls.registry.raw_bytes(cls.record['raw_record_id'])

    def snapshot(self, start='2024-05-10T00:00Z', end='2024-05-11T00:00Z', **kwargs):
        return archive_snapshot(self.registry, start_utc=start, end_utc=end,
                                mode=kwargs.pop('mode', 'history_review'), **kwargs)

    def test_named_p10_not_p500_or_electron_and_original_satellite(self):
        parsed = parse_day(self.raw, self.schema, self.record)
        first = parsed['samples'][0]
        self.assertEqual(first.value, 1.0343433618545532)
        self.assertEqual(first.unit, 'pfu')
        self.assertEqual(first.channel_id, CHANNEL_ID)
        self.assertEqual(first.t_utc, utc('2024-05-10T00:00Z'))
        self.assertEqual(first.valid_to_utc, utc('2024-05-10T00:05Z'))
        self.assertIsNone(first.published_utc)
        self.assertEqual(first.quality, 'unknown')
        self.assertEqual(first.kind, Kind.OBSERVATION)
        self.assertEqual(parsed['satellites'], {'GOES-18': 288})
        peak = max(parsed['samples'], key=lambda s: s.value)
        self.assertEqual(peak.t_utc, utc('2024-05-10T17:45Z'))
        self.assertEqual(peak.value, 206.91934204101562)

    def test_real_gaps_remain_gaps_and_unknown_quality_never_becomes_final(self):
        report = self.snapshot('2024-05-01T00:00Z', '2024-05-02T00:00Z')
        self.assertEqual(len(report['samples']), 282)
        self.assertEqual(report['coverage']['covered_seconds'], 282*300)
        self.assertEqual(report['coverage']['status'], 'partial')
        self.assertEqual(len(report['coverage']['gaps']), 3)
        gap = self.snapshot('2024-06-24T21:25Z', '2024-06-25T12:00Z')
        self.assertEqual(gap['samples'], [])
        self.assertEqual(gap['coverage']['coverage_fraction'], 0)
        full = self.snapshot()
        self.assertEqual(full['coverage']['status'], 'full')
        self.assertEqual(full['coverage']['quality'], 'unknown')
        self.assertFalse(full['coverage']['strict_replay_eligible'])

    def test_all_65_days_and_padding_are_hash_verified_and_counted(self):
        self.assertEqual(len(self.records), 65)
        report = self.snapshot('2024-04-29T00:00Z', '2024-07-03T00:00Z')
        self.assertEqual(len(report['samples']), 18400)
        self.assertEqual(sum(len(a['rejected']) for a in report['record_audit']), 12)
        self.assertEqual(len(report['coverage']['gaps']), 14)
        self.assertEqual(len(report['raw_records']), 66)
        for rid, raw in report['raw_records'].items():
            body = base64.b64decode(raw['content_base64'], validate=True)
            self.assertEqual(hashlib.sha256(body).hexdigest(), raw['metadata']['sha256'])
            self.assertEqual(body, self.registry.raw_bytes(rid))
            self.assertIsNone(raw['metadata']['availability_proof'])

    def test_historical_measurement_does_not_imply_historical_publication(self):
        report = self.snapshot(mode='history_forecast', cutoff_utc='2024-05-11T00:00Z')
        self.assertEqual(report['samples'], [])
        self.assertEqual(report['raw_records'], {})
        self.assertEqual(report['source_versions'], {})
        self.assertEqual(report['coverage']['status'], 'missing')
        self.assertEqual(len(report['excluded']), 1)
        with self.assertRaises(ValueError):
            self.snapshot(mode='history_forecast')
        with self.assertRaises(ValueError):
            self.snapshot(cutoff_utc='2024-05-11T00:00Z')

    def test_fill_negative_nonfinite_and_unknown_satellite_are_excluded_not_zero(self):
        row = self.raw.decode().splitlines()[0].split(',')
        names = [p['name'] for p in self.schema['parameters']]
        for value in ['', 'null', '-100000', '-1', 'nan', 'inf']:
            with self.subTest(value=value):
                changed = row.copy(); changed[names.index('P10')] = value
                result = parse_day((','.join(changed)+'\n').encode(), self.schema, self.record)
                self.assertEqual(result['samples'], [])
                self.assertEqual(len(result['rejected']), 1)
        changed = row.copy(); changed[names.index('P10')] = '0'
        # A measured zero is valid; it is never manufactured for a missing row.
        result = parse_day((','.join(changed)+'\n').encode(), self.schema, self.record)
        self.assertEqual(result['samples'][0].value, 0)
        changed[names.index('satelliteProton')] = '-1'
        self.assertEqual(parse_day((','.join(changed)+'\n').encode(), self.schema, self.record)['samples'], [])

    def test_schema_changes_and_malformed_rows_fail_closed(self):
        for field, attribute, value in [('P10','units','MeV'), ('P10','type','string'),
            ('Time','units','TAI'), ('Time','type','string'), ('satelliteProton','type','double')]:
            with self.subTest(field=field, attribute=attribute):
                schema = deepcopy(self.schema)
                next(p for p in schema['parameters'] if p['name']==field)[attribute] = value
                with self.assertRaises(RegistryError):
                    parse_day(self.raw, schema, self.record)
        schema = deepcopy(self.schema); schema['parameters'].append({'name': 'DQF'})
        with self.assertRaises(RegistryError):
            parse_day(self.raw, schema, self.record)
        first = self.raw.splitlines()[0]+b'\n'
        for raw in [b'<html>failed</html>', first+first,
                    first.replace(b'00:00:00Z', b'00:01:00Z'),
                    first.replace(b'2024-05-10', b'2024-05-11')]:
            with self.subTest(raw=raw[:40]), self.assertRaises(RegistryError):
                parse_day(raw, self.schema, self.record)
        with self.assertRaises(RegistryError):
            parse_day(first, self.schema, dict(self.record, valid_to_utc='2024-05-10T00:01Z'))

    def test_fractional_request_edges_clip_support_without_resampling_values(self):
        result = self.snapshot('2024-05-10T00:02Z', '2024-05-10T00:07Z')
        self.assertEqual(len(result['samples']), 2)
        self.assertEqual(result['coverage']['covered_seconds'], 300)
        self.assertEqual(result['samples'][0].valid_from_utc, utc('2024-05-10T00:00Z'))
        exact = self.snapshot('2024-05-10T00:00Z', '2024-05-10T00:05Z')
        self.assertEqual(len(exact['samples']), 1)
        self.assertEqual(exact['coverage']['covered_seconds'], 300)

    def small_archive(self):
        directory = TemporaryDirectory(); self.addCleanup(directory.cleanup)
        write_archive(directory.name, [(self.schema_record,self.schema_raw),(self.record,self.raw)])
        return SourceRegistry(directory.name, 'registry.json'), Path(directory.name)

    def test_raw_or_schema_tampering_after_open_fails_before_returning_a_snapshot(self):
        for record_id in [self.record['raw_record_id'], self.schema_record['raw_record_id']]:
            registry, root = self.small_archive()
            record = registry.record(record_id)
            (root/record['raw_path']).write_bytes(b'corrupt')
            with self.assertRaises(RegistryError):
                archive_snapshot(registry, start_utc='2024-05-10T00:00Z',
                    end_utc='2024-05-11T00:00Z', mode='history_review')

    def test_snapshots_are_independent_and_each_sample_has_exact_provenance(self):
        left, right = self.snapshot(), self.snapshot()
        for sample in right['samples']:
            metadata = right['source_versions'][sample.source_id][sample.raw_record_id]
            self.assertEqual(metadata['version'], sample.version)
            self.assertIn('format=csv',metadata['url'])
        left['source_versions'][SOURCE_ID][self.record['raw_record_id']]['sha256']='changed'
        self.assertEqual(right, self.snapshot())

    def test_history_adapter_includes_review_only_and_32h_plus_delay_stays_in_padding(self):
        for start in ['2024-05-01T00:00Z', '2024-05-20T12:00Z', '2024-06-30T23:59Z']:
            t = utc(start)
            review = Request('history_review', t, 480, 1440, None, work_delay_min=180)
            result = history_snapshot(review, goes_registry=self.registry)
            self.assertTrue(any(s.channel_id==CHANNEL_ID for s in result['samples']))
            self.assertEqual(utc(result['valid_to_utc']), t+timedelta(hours=35))
            for s in result['samples']:
                self.assertIn(s.raw_record_id, result['raw_records'])
            strict = history_snapshot(replace(review,mode='history_forecast',cutoff_utc=t),
                                      goes_registry=self.registry)
            self.assertFalse(any(s.channel_id==CHANNEL_ID for s in strict['samples']))
            self.assertEqual(strict['coverage_map'][CHANNEL_ID+':observations']['status'], 'missing')
        # Padding contains the final window, but three genuine July 1 gaps must
        # still prevent FULL. Padding is not a promise of continuous monitoring.
        coverage = result['coverage_map'][CHANNEL_ID+':observations']
        self.assertEqual(coverage['status'], 'partial')
        self.assertEqual(len(coverage['gaps']), 3)
        self.assertEqual(coverage['covered_seconds'], 32*3600-15*60)


if __name__ == '__main__':
    unittest.main()
