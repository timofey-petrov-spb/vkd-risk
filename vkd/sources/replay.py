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


def forecast(records, now):
    """Replay one current NOAA bulletin using original bytes and receipt admissibility."""
    from .live_parsers import parse_noaa_live
    sid = 'noaa_swpc_3day_forecast'
    selected = [(rid,item) for rid,item in records.items() if isinstance(item,dict)
                and item.get('metadata',{}).get('source_id') == sid]
    if not selected:
        return (), {}, Fetch(sid,False,False,None,None,'нет сохранённого прогноза NOAA',None,None)
    if len(selected) != 1:
        raise ValueError('Ambiguous replay releases: '+sid)
    rid,item = selected[0];meta = item['metadata']
    raw = base64.b64decode(item['content_base64'],validate=True)
    if hashlib.sha256(raw).hexdigest() != meta['sha256']:
        raise ValueError('Replay SHA-256 mismatch: '+rid)
    parsed = parse_noaa_live(raw,now)
    fetched = utc(meta['fetched_utc'])
    names = {'noaa_kp':'kp_forecast','s1_or_greater_probability':'s1_prob_daily'}
    samples = tuple(EnvironmentSample(utc(c['valid_from_utc']),names[c['channel_id']],c['value'],c['unit'],sid,
        Kind.EXTERNAL_FORECAST,utc(c['published_utc']),utc(c['valid_from_utc']),utc(c['valid_to_utc']),
        fetched,'model',rid,meta['version']) for c in parsed['cells'] if c['channel_id'] in names)
    if not meta.get('admissibility',{}).get('usable',True):
        samples = ()
    fetch = Fetch(sid,False,True,fetched,(now-parsed['data_utc']).total_seconds()/60,
                  'повтор бюллетеня NOAA из сохранённых байтов',parsed,None,metadata=meta,parsed=parsed,raw=raw)
    return samples,{rid:item},fetch
