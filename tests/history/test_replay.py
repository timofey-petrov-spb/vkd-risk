from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from vkd.history.replay import replay_forecast
from vkd.sources.registry import SourceRegistry, utc
from tests.sources.helpers import REPO, archived_release, write_archive

KP = 'noaa_ngdc_3day_forecast'
DAYPRE = 'noaa_ngdc_daypre'


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = SourceRegistry(REPO)

    def replay(self, cutoff='2024-05-03T12:00:00Z', source=KP, channel='noaa_kp', hours=32, registry=None):
        t = utc(cutoff)
        return replay_forecast(registry or self.registry, source_id=source, channel_id=channel,
                               cutoff_utc=t, valid_from_utc=t, valid_to_utc=t + timedelta(hours=hours))

    def temporary_archive(self, releases):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        write_archive(temp.name, releases)
        return SourceRegistry(temp.name, 'registry.json'), Path(temp.name)

    def test_six_cutoffs_and_gap_are_real_not_hardcoded_answers(self):
        cases = [('2024-05-01T00:00:00Z', '20240430daypre', 'missing'),
                 ('2024-05-03T12:00:00Z', '20240502daypre', 'full'),
                 ('2024-05-20T12:00:00Z', '20240519daypre', 'missing'),
                 ('2024-06-01T12:00:00Z', '20240531daypre', 'missing'),
                 ('2024-06-25T12:00:00Z', '20240624daypre', 'full'),
                 ('2024-06-30T12:00:00Z', '20240629daypre', 'full')]
        for cutoff, expected_release, kp_status in cases:
            with self.subTest(cutoff=cutoff):
                daypre = self.replay(cutoff, DAYPRE, 'whole_disk_proton_probability')
                self.assertEqual(daypre['record']['release_id'], expected_release)
                self.assertEqual(daypre['coverage_fraction'], 1.0)
                self.assertEqual(self.replay(cutoff)['status'], kp_status)
        self.assertEqual(self.replay('2024-05-08T07:17:00Z')['status'], 'full')

    def test_future_releases_do_not_change_result(self):
        early = archived_release('202405030030three_day_forecast')
        future = archived_release('202405031230three_day_forecast')
        before, _ = self.temporary_archive([early])
        after, _ = self.temporary_archive([early, future])
        self.assertEqual(self.replay(registry=before), self.replay(registry=after))

    def test_late_revision_cannot_enter_through_old_publication(self):
        early = archived_release('202405030030three_day_forecast')
        record, raw = deepcopy(early)
        record['raw_record_id'] += ':later-revision'
        record['available_utc'] = '2024-05-04T00:00:00Z'
        before, _ = self.temporary_archive([early])
        after, _ = self.temporary_archive([early, (record, raw + b'\nRevision\n')])
        self.assertEqual(self.replay(registry=before), self.replay(registry=after))

    def test_unordered_different_versions_are_ambiguous(self):
        early = archived_release('202405030030three_day_forecast')
        record, raw = deepcopy(early); record['raw_record_id'] += ':other'
        registry, _ = self.temporary_archive([early, (record, raw + b'\nDifferent bytes\n')])
        result = self.replay(registry=registry)
        self.assertEqual(result['status'], 'ambiguous')
        self.assertEqual(result['coverage_fraction'], 0)
        self.assertEqual(result['cells'], [])

    def test_same_bytes_from_two_urls_are_not_ambiguous(self):
        early = archived_release('202405030030three_day_forecast')
        record, raw = deepcopy(early); record['raw_record_id'] += ':alias'
        registry, _ = self.temporary_archive([early, (record, raw)])
        self.assertEqual(self.replay(registry=registry)['status'], 'full')

    def test_issue_boundary_uses_header_not_filename(self):
        self.assertEqual(self.replay('2024-06-16T12:49:59Z')['status'], 'missing')
        result = self.replay('2024-06-16T12:50:00Z')
        self.assertEqual(result['status'], 'full')
        self.assertEqual(result['record']['published_utc'], '2024-06-16T12:50:00Z')

    def test_partial_interval_has_numeric_coverage_and_explicit_gap(self):
        result = self.replay('2024-05-16T12:00:00Z')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['covered_seconds'], 12 * 3600)
        self.assertEqual(result['coverage_fraction'], 12 / 32)
        self.assertEqual(result['gaps'], [{'valid_from_utc': '2024-05-17T00:00:00Z',
                                          'valid_to_utc': '2024-05-17T20:00:00Z'}])

    def test_six_hour_query_preserves_daily_probability(self):
        result = self.replay('2024-05-20T12:00:00Z', DAYPRE, 'whole_disk_proton_probability', hours=6)
        self.assertEqual(result['coverage_fraction'], 1)
        self.assertEqual(len(result['cells']), 1)
        cell = result['cells'][0]
        self.assertEqual(cell['value'], 10)
        self.assertEqual(cell['temporal_resolution'], '24h')
        self.assertEqual(cell['valid_from_utc'], '2024-05-20T00:00:00Z')
        self.assertEqual(cell['valid_to_utc'], '2024-05-21T00:00:00Z')

    def test_daypre_does_not_substitute_local_k_for_planetary_kp(self):
        result = self.replay('2024-05-20T12:00:00Z', DAYPRE, 'noaa_kp')
        self.assertEqual(result['status'], 'missing')
        self.assertEqual(result['cells'], [])

    def test_oem_and_unaudited_donki_are_not_admitted(self):
        for source, channel in [('nasa_jsc_oem', 'position'), ('nasa_donki_notification', 'sep')]:
            with self.subTest(source=source):
                result = self.replay('2024-05-20T12:00:00Z', source, channel)
                self.assertEqual(result['status'], 'missing')
                self.assertIsNone(result['record'])

    def test_corruption_after_load_cannot_keep_full_coverage(self):
        registry, root = self.temporary_archive([archived_release('202405030030three_day_forecast')])
        (root / 'raw/0.txt').write_bytes(b'broken')
        result = self.replay(registry=registry)
        self.assertEqual(result['status'], 'invalid')
        self.assertEqual(result['coverage_fraction'], 0)
        self.assertEqual(result['cells'], [])

    def test_time_validation_and_timezone_equivalence(self):
        with self.assertRaisesRegex(ValueError, 'timezone-aware'):
            self.replay(datetime(2024, 5, 3, 12))
        self.assertEqual(self.replay('2024-05-03T15:00:00+03:00'), self.replay())
        for hours in (0, -1):
            with self.subTest(hours=hours), self.assertRaises(ValueError):
                self.replay(hours=hours)
        with self.assertRaises(ValueError):
            replay_forecast(self.registry, source_id=KP, channel_id='noaa_kp',
                            cutoff_utc=utc('2024-05-03T12:00Z'), valid_from_utc=utc('2024-05-03T11:00Z'),
                            valid_to_utc=utc('2024-05-03T13:00Z'))
