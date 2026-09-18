from copy import deepcopy
import unittest

from vkd.sources.noaa import ForecastParseError, parse_forecast
from vkd.sources.registry import SourceRegistry
from tests.sources.helpers import REPO, archived_release


class NoaaTests(unittest.TestCase):
    def test_all_117_real_bulletins(self):
        registry = SourceRegistry(REPO)
        count = 0
        for source in ('noaa_ngdc_3day_forecast', 'noaa_ngdc_daypre'):
            for record in registry.records(source):
                with self.subTest(release=record['release_id']):
                    count += len(parse_forecast(registry.raw_bytes(record['raw_record_id']), record))
        self.assertEqual(count, 6561)

    def test_values_against_original_tables(self):
        record, raw = archived_release('202405030030three_day_forecast')
        cells = parse_forecast(raw, record)
        kp = [c for c in cells if c['channel_id'] == 'noaa_kp']
        self.assertEqual((kp[0]['value'], kp[0]['valid_from_utc'], kp[0]['valid_to_utc']),
                         (6.67, '2024-05-03T00:00:00Z', '2024-05-03T03:00:00Z'))
        record, raw = archived_release('20240519daypre')
        cells = parse_forecast(raw, record)
        proton = [c for c in cells if c['channel_id'] == 'whole_disk_proton_probability']
        self.assertEqual([c['value'] for c in proton], [10.0, 10.0, 10.0])
        self.assertEqual(proton[0]['unit'], '%')
        self.assertEqual([c['value'] for c in cells if c['channel_id'] == 'f107'], [200.0] * 3)
        self.assertNotIn('noaa_kp', {c['channel_id'] for c in cells})

    def test_future_observation_prose_does_not_become_forecast(self):
        record, raw = archived_release('202405030030three_day_forecast')
        changed = raw + b'\nLater observation: 2024 May 10 1200 UTC Kp=9, proton=1000000.\n'
        self.assertEqual(parse_forecast(raw, record), parse_forecast(changed, record))

    def test_wrong_publication_is_rejected(self):
        record, raw = archived_release('202406161230three_day_forecast')
        record['published_utc'] = '2024-06-16T12:30:00Z'
        with self.assertRaisesRegex(ForecastParseError, 'Issued'):
            parse_forecast(raw, record)

    def test_missing_or_invalid_numeric_cells_are_not_zero(self):
        record, raw = archived_release('202405030030three_day_forecast')
        for replacement in (b'--', b'nan', b'10.0', b'-1.0'):
            changed = raw.replace(b'00-03UT       6.67', b'00-03UT       ' + replacement)
            with self.subTest(replacement=replacement), self.assertRaises(ForecastParseError):
                parse_forecast(changed, record)

    def test_metadata_cannot_relabel_probability_as_dose(self):
        record, raw = archived_release('202405030030three_day_forecast')
        record = deepcopy(record)
        record['channels'][1]['unit'] = 'mSv'
        with self.assertRaisesRegex(ForecastParseError, 'units'):
            parse_forecast(raw, record)
