import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from vkd.sources.registry import RegistryError, SourceRegistry
from tests.sources.helpers import REPO, archived_release, write_archive


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.release = archived_release('202405030030three_day_forecast')
        write_archive(self.root, [self.release])

    def load(self):
        return SourceRegistry(self.root, 'registry.json')

    def test_corrupt_raw_is_rejected_at_load(self):
        (self.root / 'raw/0.txt').write_bytes(b'corrupt')
        with self.assertRaisesRegex(RegistryError, 'SHA-256'):
            self.load()

    def test_corrupt_metadata_is_rejected(self):
        with (self.root / 'records.json').open('ab') as stream:
            stream.write(b' ')
        with self.assertRaisesRegex(RegistryError, 'SHA-256'):
            self.load()

    def test_mutation_after_load_is_rejected_at_read(self):
        registry = self.load()
        (self.root / 'raw/0.txt').write_bytes(b'changed after verification')
        with self.assertRaisesRegex(RegistryError, 'SHA-256'):
            registry.raw_bytes(self.release[0]['raw_record_id'])

    def test_callers_cannot_modify_shared_snapshot(self):
        registry = self.load()
        raw_id = self.release[0]['raw_record_id']
        a = registry.record(raw_id)
        a['channels'][0]['unit'] = 'wrong'
        self.assertEqual(registry.record(raw_id)['channels'][0]['unit'], '1')
        registry.records(a['source_id'])[0]['published_utc'] = None
        self.assertIsNotNone(registry.record(raw_id)['published_utc'])

    def test_paths_cannot_escape_repository(self):
        index = json.loads((self.root / 'registry.json').read_text())
        next(iter(index['sources'].values()))['records_path'] = '../outside.json'
        (self.root / 'registry.json').write_text(json.dumps(index))
        with self.assertRaisesRegex(RegistryError, 'escapes'):
            self.load()

    def test_timezone_missing_from_metadata_is_rejected(self):
        record, raw = self.release
        record['published_utc'] = '2024-05-03T00:30:00'
        write_archive(self.root, [(record, raw)])
        with self.assertRaisesRegex(RegistryError, 'timezone-aware'):
            self.load()

    def test_real_archive_all_releases_verify(self):
        registry = SourceRegistry(REPO)
        # Original 390 records plus 20 CDX-verified historical NOAA captures.
        self.assertEqual(sum(len(registry.records(s)) for s in registry.source_ids), 410)
        for source in registry.source_ids:
            for record in registry.records(source):
                with self.subTest(raw_record_id=record['raw_record_id']):
                    self.assertEqual(hashlib.sha256(registry.raw_bytes(record['raw_record_id'])).hexdigest(),
                                     record['sha256'])
