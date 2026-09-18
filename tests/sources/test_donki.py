from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import unittest

from vkd.sources.donki import NotificationParseError, parse_notification
from vkd.sources.registry import SourceRegistry, utc
from vkd.types import Kind

ROOT = Path(__file__).resolve().parents[2]


class DonkiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = SourceRegistry(ROOT)
        cls.records = {r['release_id']: r for r in cls.registry.records('nasa_donki_notification')}

    def parse(self, mid):
        record = self.records[mid]
        return parse_notification(self.registry.raw_bytes(record['raw_record_id']), record)

    def changed(self, mid, transform):
        record = deepcopy(self.records[mid])
        message = json.loads(self.registry.raw_bytes(record['raw_record_id']))
        transform(message)
        return parse_notification(json.dumps(message).encode(), record)

    def test_all_archived_notifications_have_an_explicit_classification(self):
        counts = Counter(self.parse(mid)['status'] for mid in self.records)
        self.assertEqual(sum(counts.values()), 247)
        self.assertEqual(counts, {'parsed': 166, 'out_of_scope': 71,
                                  'context_only': 9, 'publication_conflict': 1})

    def test_gannon_first_observation_not_later_card_peak(self):
        result = self.parse('20240510-AL-013')
        sample, = result['samples']
        self.assertEqual(sample.value, 7.67)
        self.assertEqual(sample.t_utc, utc('2024-05-10T18:00Z'))
        self.assertEqual(sample.valid_from_utc, utc('2024-05-10T15:00Z'))
        self.assertEqual(sample.published_utc, utc('2024-05-10T18:44:21Z'))
        event, = result['events']
        self.assertEqual(event.end_utc, utc('2024-05-10T18:00Z'))
        self.assertEqual(event.kind, Kind.OBSERVATION)
        self.assertIsNone(event.valid_to_utc)

    def test_goes_threshold_is_not_a_measured_flux_or_s_level(self):
        result = self.parse('20240510-AL-004')
        self.assertEqual(result['facts']['energy_lower_bound_MeV'], 10)
        self.assertEqual(result['facts']['flux_lower_bound_pfu'], 10)
        self.assertIsNone(result['facts']['measured_flux_pfu'])
        self.assertIsNone(result['facts']['noaa_s_scale'])
        self.assertEqual(result['samples'], [])
        event, = result['events']
        self.assertEqual(event.start_utc, utc('2024-05-10T13:35Z'))
        self.assertIsNone(event.end_utc)
        self.assertTrue(event.end_uncertain)
        self.assertIsNone(event.valid_from_utc)
        self.assertIsNone(event.valid_to_utc)

    def test_100_mev_message_cannot_become_10_mev_from_notes(self):
        result = self.parse('20240511-AL-007')
        self.assertEqual(result['facts']['energy_lower_bound_MeV'], 100)
        self.assertEqual(result['facts']['flux_lower_bound_pfu'], 1)
        self.assertEqual(result['events'][0].start_utc, utc('2024-05-11T02:10Z'))
        changed = self.changed('20240511-AL-007', lambda m: m.update(
            messageBody=m['messageBody'] + '\nGOES > 10 MeV protons exceeds 9999 pfu starting at 2024-05-12T23:00Z.'))
        self.assertEqual(result, changed)

    def test_stereo_and_soho_are_not_earth_goes_alerts(self):
        for mid in ['20240511-AL-013', '20240513-AL-010', '20240612-AL-004']:
            with self.subTest(mid=mid):
                result = self.parse(mid)
                self.assertEqual(result['status'], 'out_of_scope')
                self.assertEqual(result['events'], [])
                self.assertEqual(result['samples'], [])

    def test_cme_arrival_not_launch_time_and_uncertainty_not_duration(self):
        result = self.parse('20240509-AL-010')
        event, = result['events']
        self.assertEqual(event.kind_of_event, 'CME_arrival')
        self.assertEqual(event.kind, Kind.EXTERNAL_FORECAST)
        self.assertEqual(event.start_utc, utc('2024-05-10T13:03Z'))
        self.assertGreater(event.start_utc, event.published_utc)
        self.assertTrue(event.start_uncertain)
        self.assertIsNone(event.end_utc)
        self.assertIsNone(event.valid_to_utc)
        self.assertEqual(result['facts']['arrival_earliest_utc'], '2024-05-10T06:03:00Z')
        self.assertEqual(result['facts']['arrival_latest_utc'], '2024-05-10T20:03:00Z')
        self.assertEqual(self.parse('20240430-AL-001')['status'], 'out_of_scope')

    def test_negated_arrival_is_not_a_positive_forecast(self):
        result = self.changed('20240509-AL-010', lambda m: m.update(
            messageBody=m['messageBody'].replace('will reach NASA missions near Earth', 'will not reach NASA missions near Earth')))
        self.assertEqual(result['events'], [])
        self.assertEqual(result['status'], 'unsupported')

    def test_header_mismatch_invalid_value_and_future_observation_fail(self):
        mutations = [lambda m: m.update(messageID='wrong'),
            lambda m: m.update(messageBody=m['messageBody'].replace('level 7.67', 'level 17.67')),
            lambda m: m.update(messageBody=m['messageBody'].replace('2024-05-10T15:00Z to 2024-05-10T18:00Z',
                                                                   '2024-05-11T15:00Z to 2024-05-11T18:00Z'))]
        for mutate in mutations:
            with self.assertRaises(NotificationParseError):
                self.changed('20240510-AL-013', mutate)

    def test_weekly_report_publication_conflict_not_assumed_rounding(self):
        result = self.parse('20240516-7D-001')
        self.assertEqual(result['status'], 'publication_conflict')
        self.assertEqual(result['events'], [])

