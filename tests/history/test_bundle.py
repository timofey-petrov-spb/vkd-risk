import base64
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from vkd.history import HistoryDataError, history_bundle, history_snapshot
from vkd.sources.registry import SourceRegistry, utc
from vkd.types import EnvironmentSample, EventInterval, Kind, Request
from tests.sources.helpers import write_archive

ROOT = Path(__file__).resolve().parents[2]
DONKI = 'nasa_donki_notification'
NOAA = 'noaa_ngdc_3day_forecast'


class BundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = SourceRegistry(ROOT)
        cls.messages = {r['release_id']: r for r in cls.registry.records(DONKI)}

    def request(self, date='2024-05-10T19:00Z', **kwargs):
        t = utc(date)
        return replace(Request('history_forecast', t, 480, 1440, t), **kwargs)

    def snapshot(self, date='2024-05-10T19:00Z', **kwargs):
        return history_snapshot(self.request(date, **kwargs), registry=self.registry)

    def small_archive(self, entries):
        directory = TemporaryDirectory(); self.addCleanup(directory.cleanup)
        # Synthetic release metadata refers only to bytes supplied in this fixture.
        entries = [(dict(r, response_refs=[]), raw) for r, raw in entries]
        write_archive(directory.name, entries)
        return SourceRegistry(directory.name, 'registry.json'), Path(directory.name)

    def test_three_control_dates_and_arbitrary_date_keep_actual_channel_gaps(self):
        for date, status in [('2024-05-01T00:00Z', 'missing'), ('2024-05-20T12:00Z', 'missing'),
                             ('2024-06-30T12:00Z', 'full'), ('2024-05-08T07:17Z', 'full')]:
            with self.subTest(date=date):
                result = self.snapshot(date)
                self.assertEqual(result['coverage_map'][NOAA+':noaa_kp']['status'], status)
                self.assertEqual(result['coverage_map']['noaa_ngdc_daypre:whole_disk_proton_probability']['status'], 'full')
                self.assertEqual(utc(result['valid_to_utc'])-utc(result['valid_from_utc']), timedelta(hours=32))
                self.assertTrue(all(isinstance(s, EnvironmentSample) for s in result['samples']))
                self.assertTrue(all(isinstance(e, EventInterval) for e in result['events']))

    def test_issue_seconds_and_late_message_cannot_enter_early_forecast(self):
        before = self.snapshot('2024-05-10T13:46:57Z')
        after = self.snapshot('2024-05-10T13:46:58Z')
        rid = self.messages['20240510-AL-004']['raw_record_id']
        self.assertFalse(any(e.raw_record_id == rid for e in before['events']))
        self.assertTrue(any(e.raw_record_id == rid for e in after['events']))
        at_19 = self.snapshot()
        kp = [s for s in at_19['samples'] if s.channel_id == 'kp']
        self.assertEqual([s.value for s in kp], [7.67])
        self.assertTrue(all(s.published_utc <= utc('2024-05-10T19:00Z') for s in at_19['samples']))
        self.assertNotIn(self.messages['20240510-AL-014']['raw_record_id'], at_19['raw_records'])

    def test_forecast_future_cells_remain_valid_daily_probabilities_stay_daily(self):
        result = self.snapshot('2024-05-20T12:00Z', duration_min=360, search_period_min=0)
        p = [s for s in result['samples'] if s.channel_id == 'whole_disk_proton_probability']
        self.assertEqual(len(p), 1)
        self.assertEqual(p[0].value, 10)
        self.assertEqual(p[0].unit, '%')
        self.assertEqual(p[0].valid_to_utc-p[0].valid_from_utc, timedelta(days=1))
        result = self.snapshot('2024-05-03T12:00Z')
        self.assertTrue(any(s.t_utc > utc('2024-05-03T12:00Z') and s.kind == Kind.EXTERNAL_FORECAST for s in result['samples']))
        self.assertFalse(any(s.channel_id == 'kp' and s.source_id != DONKI for s in result['samples']))

    def test_cme_uncertainty_overlap_kept_without_inventing_event_duration(self):
        result = self.snapshot('2024-06-01T12:00Z', duration_min=360, search_period_min=0)
        rid = self.messages['20240529-AL-005']['raw_record_id']
        event = next(e for e in result['events'] if e.raw_record_id == rid)
        self.assertEqual(event.start_utc, utc('2024-06-01T20:00Z'))
        self.assertGreater(event.start_utc, utc(result['valid_to_utc']))
        self.assertIsNone(event.end_utc)
        audit = next(a for a in result['notification_audit'] if a['raw_record_id'] == rid)
        self.assertEqual(audit['facts']['arrival_earliest_utc'], '2024-06-01T13:00:00Z')

    def test_sparse_notifications_never_claim_full_monitoring_or_goes_flux(self):
        result = self.snapshot('2024-05-20T12:00Z')
        self.assertFalse(result['coverage_map']['donki:notifications']['complete_event_catalog'])
        self.assertIsNone(result['coverage_map']['donki:notifications']['physical_coverage_fraction'])
        self.assertEqual(result['coverage_map']['goes_p_ge10MeV:observations']['status'], 'missing')
        self.assertEqual(result['coverage_map']['kp:observations']['coverage_fraction'], 0)
        self.assertFalse(any(s.channel_id == 'goes_p_ge10MeV' for s in result['samples']))

    def test_consumer_can_read_sep_channel_without_parsing_russian_note(self):
        result = self.snapshot('2024-05-11T03:00Z')
        rid = self.messages['20240511-AL-007']['raw_record_id']
        facts = result['source_versions'][DONKI][rid]['content_audit']['facts']
        self.assertEqual(facts['energy_lower_bound_MeV'], 100)
        self.assertIsNone(facts['noaa_s_scale'])

    def test_exact_raw_bytes_and_nested_versions_resolve_every_typed_fact(self):
        result = self.snapshot()
        for obj in result['samples'] + result['events']:
            with self.subTest(rid=obj.raw_record_id):
                record = result['source_versions'][obj.source_id][obj.raw_record_id]
                raw = base64.b64decode(result['raw_records'][obj.raw_record_id]['content_base64'], validate=True)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), record['sha256'])
                self.assertEqual(raw, self.registry.raw_bytes(obj.raw_record_id))
                self.assertIn('availability_proof', record)

    def test_added_late_message_does_not_change_admitted_facts(self):
        early = self.messages['20240510-AL-013']
        late = self.messages['20240510-AL-015']
        entries = [(r, self.registry.raw_bytes(r['raw_record_id'])) for r in [early, late]]
        a,_ = self.small_archive(entries[:1]); b,_ = self.small_archive(entries)
        left = history_snapshot(self.request(), registry=a)
        right = history_snapshot(self.request(), registry=b)
        for key in ['samples', 'events', 'source_versions', 'raw_records']:
            self.assertEqual(left[key], right[key])

    def test_conflicting_versions_are_excluded_and_late_availability_does_not_backdate(self):
        record = self.messages['20240510-AL-013']
        raw = self.registry.raw_bytes(record['raw_record_id'])
        changed = deepcopy(record); changed['raw_record_id'] += ':changed'
        payload = json.loads(raw); payload['messageBody'] += '\nLater note.\n'
        revised = json.dumps(payload).encode()
        ambiguous,_ = self.small_archive([(record,raw),(changed,revised)])
        result = history_snapshot(self.request(), registry=ambiguous)
        self.assertEqual(result['events'], [])
        self.assertTrue(any(x['reason']=='ambiguous_notification_versions' for x in result['excluded']))
        changed['available_utc'] = '2024-05-12T00:00:00Z'
        late,_ = self.small_archive([(record,raw),(changed,revised)])
        result = history_snapshot(self.request(), registry=late)
        self.assertEqual(len(result['events']), 1)
        self.assertEqual(result['events'][0].raw_record_id, record['raw_record_id'])

    def test_corrupt_archive_cannot_be_successful_empty_data(self):
        with TemporaryDirectory() as empty:
            with self.assertRaises(HistoryDataError):
                history_snapshot(self.request(), repo_root=empty)
        record = self.messages['20240510-AL-013']
        registry,root = self.small_archive([(record,self.registry.raw_bytes(record['raw_record_id']))])
        (root/'raw/0.txt').write_text('damaged')
        result = history_snapshot(self.request(),registry=registry)
        self.assertEqual(result['events'], [])
        self.assertTrue(any(x['reason'].startswith('invalid:') for x in result['excluded']))
        self.assertFalse(result['coverage_map']['donki:notifications']['complete_event_catalog'])

    def test_request_validation_delay_and_disabled_cache_semantics(self):
        for changes in [dict(cutoff_utc=None), dict(cutoff_utc=utc('2024-05-11T00:00Z')),
                        dict(duration_min=59), dict(search_period_min=1441), dict(work_delay_min=-1),
                        dict(eva_start_utc=datetime(2024,5,10)),dict(mode='live')]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                history_snapshot(self.request(**changes),registry=self.registry)
        normal = self.snapshot(); delayed = self.snapshot(work_delay_min=180)
        self.assertEqual(utc(delayed['valid_from_utc'])-utc(normal['valid_from_utc']), timedelta(hours=3))
        self.assertEqual(delayed['cutoff_utc'],normal['cutoff_utc'])
        disabled = self.snapshot(disabled_sources=(DONKI,NOAA))
        self.assertEqual(normal['samples'], disabled['samples'])
        self.assertIn('verified_offline_cache',disabled['archive_access'])

    def test_legacy_interface_and_independent_snapshots(self):
        samples,events,raw = history_bundle(registry=self.registry)
        self.assertEqual(len(samples),21)
        self.assertEqual(len(events),166)
        self.assertEqual(raw['_history']['mode'],'legacy_notification_catalog')
        self.assertTrue(all(s.channel_id=='kp' for s in samples))
        left = self.snapshot(); right = self.snapshot()
        first_id = next(iter(left['raw_records']))
        left['raw_records'][first_id]['metadata']['sha256'] = 'mutated'
        self.assertNotEqual(left['raw_records'],right['raw_records'])
        self.assertEqual(right, self.snapshot())
