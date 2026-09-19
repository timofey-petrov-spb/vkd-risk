"""Restore A4 observations from exported bytes without touching network/cache."""
import base64
import hashlib
from .live_cache import Fetch
from .live_parsers import parse_goes, parse_kp
from .registry import utc
from vkd.types import EnvironmentSample, Kind


def observation(records, source_id, now):
    candidates = [(rid, item) for rid, item in records.items()
                  if isinstance(item, dict) and item.get('metadata', {}).get('source_id') == source_id]
    if not candidates:
        return None, {}, Fetch(source_id, False, False, None, None, 'нет сохранённого наблюдения', None, None)
    if len(candidates) != 1:
        raise ValueError(f'Ambiguous replay releases: {source_id}')
    rid, item = candidates[0]
    meta = item['metadata']
    raw = base64.b64decode(item['content_base64'], validate=True)
    if hashlib.sha256(raw).hexdigest() != meta['sha256']:
        raise ValueError(f'Replay SHA-256 mismatch: {rid}')
    p = (parse_goes if source_id == 'noaa_swpc_goes' else parse_kp)(raw, now)
    fetched = utc(meta['fetched_utc'])
    sample = EnvironmentSample(p['data_utc'], p['channel_id'], p['value'], p['unit'], source_id,
        Kind.OBSERVATION, p['published_utc'], p.get('valid_from_utc'), p.get('valid_to_utc'),
        fetched, p['quality'], rid, meta['version'])
    return sample, {rid: item}, Fetch(source_id, False, True, fetched,
        (now-p['data_utc']).total_seconds()/60, 'повтор из сохранённых байтов',
        p, None, metadata=meta, parsed=p, raw=raw)
