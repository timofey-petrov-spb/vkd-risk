"""Build the release registry from calculation evidence, not UI status labels."""
from copy import deepcopy
import base64
import hashlib
from pathlib import Path


def collect_records(raw_records, history_versions, orbit_provenance, belts, root):
    root = Path(root)
    versions = deepcopy(history_versions)

    def add_file(rid, metadata, path):
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if metadata.get('sha256') and metadata['sha256'] != sha:
            raise ValueError(f'Source changed before export: {rid}')
        metadata = dict(metadata, raw_record_id=rid, sha256=sha, bytes=len(raw), version='sha256:'+sha)
        metadata.setdefault('published_utc', None)
        metadata.setdefault('availability_proof', None)
        raw_records[rid] = {'metadata': metadata, 'encoding': 'base64',
                            'content_base64': base64.b64encode(raw).decode('ascii')}

    for rid, meta in orbit_provenance.get('records', {}).items():
        base = root
        if meta.get('source_id') == 'celestrak_gp' and orbit_provenance.get('staged_root'):
            base = root / orbit_provenance['staged_root']
        add_file(rid, meta, base / meta['raw_path'])
    add_file(belts.raw_record_id, {'source_id': 'ost1044_belts', 'raw_path': belts.file,
             'sha256': belts.sha256, 'citation': belts.source, 'quality': 'model'}, root/belts.file)
    for rid, source in [('ecss_grun:grun-ecss-2020-v1','ecss_grun'), ('imo_calendar','imo_calendar')]:
        path = 'vkd/assess/meteoroids.py'
        add_file(rid, {'source_id': source, 'raw_path': path, 'quality': 'model',
                      'evidence_role': 'implementation_and_constants; not a provider observation'}, root/path)
    for rid, item in raw_records.items():
        meta = item.get('metadata') if isinstance(item, dict) else None
        if not meta:
            continue
        meta = deepcopy(meta)
        sid = meta['source_id']
        if 'content_base64' in item:
            raw = base64.b64decode(item['content_base64'], validate=True)
            if hashlib.sha256(raw).hexdigest() != meta['sha256']:
                raise ValueError(f'Export SHA-256 mismatch: {rid}')
        previous = versions.setdefault(sid, {}).get(rid)
        if previous and previous.get('sha256') != meta.get('sha256'):
            raise ValueError(f'Conflicting source versions: {rid}')
        versions[sid][rid] = {**(previous or {}), **meta}
    return versions
