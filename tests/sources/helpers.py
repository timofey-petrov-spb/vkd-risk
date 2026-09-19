from copy import deepcopy
import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def archived_release(release_id):
    records = json.loads((REPO / 'data/source_registry_2024/noaa/records.json').read_text(encoding='utf-8'))['records']
    record = next(r for r in records if r['release_id'] == release_id)
    return deepcopy(record), (REPO / record['raw_path']).read_bytes()


def write_archive(root, releases):
    """Small on-disk archive for faults/version changes, using actual NOAA bytes."""
    root = Path(root)
    records = []
    sources = {}
    for i, (original, raw) in enumerate(releases):
        record = deepcopy(original)
        record['sha256'] = hashlib.sha256(raw).hexdigest()
        record['version'] = 'sha256:' + record['sha256']
        record['bytes'] = len(raw)
        record['raw_path'] = f'raw/{i}.txt'
        path = root / record['raw_path']; path.parent.mkdir(exist_ok=True)
        path.write_bytes(raw)
        records.append(record)
    payload = json.dumps({'schema_version': 1, 'records': records}).encode()
    (root / 'records.json').write_bytes(payload)
    for i, record in enumerate(records):
        source = sources.setdefault(record['source_id'], {'records_path': 'records.json',
            'records_sha256': hashlib.sha256(payload).hexdigest(), 'record_count': 0, 'records': {}})
        source['record_count'] += 1
        source['records'][record['raw_record_id']] = {'record_index': i, 'release_id': record['release_id'],
                                                    'sha256': record['sha256']}
    index = {'schema_version': 1, 'record_count': len(records), 'sources': sources}
    (root / 'registry.json').write_text(json.dumps(index))
