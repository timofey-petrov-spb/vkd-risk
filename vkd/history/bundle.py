"""Request-aware archive adapter to the agreed vkd.types 2.1 interface.

The snapshot is a data report, not a recommendation. Forecast support, sparse
observations and notification inventory have separate coverage descriptions.
"""
from __future__ import annotations

import base64
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vkd.sources.donki import NotificationParseError, SOURCE_ID as DONKI, parse_notification
from vkd.sources.noaa import SOURCE_3DAY, SOURCE_DAYPRE
from vkd.sources.registry import RegistryError, SourceRegistry, iso_utc, utc
from vkd.types import EnvironmentSample, Kind, Request
from .replay import replay_forecast

ROOT = Path(__file__).resolve().parents[2]
ADAPTER_VERSION = 'history-a2-v2'


class HistoryDataError(ValueError):
    """Archive integrity failed; no successful empty coverage can be inferred."""


def _registry(root, supplied):
    try:
        return supplied if supplied is not None else SourceRegistry(root)
    except (OSError, RegistryError) as exc:
        raise HistoryDataError(f'Historical archive unavailable or invalid: {exc}') from exc


def _source_records(registry, source_id):
    return registry.records(source_id) if source_id in registry.source_ids else []


def _attach(snapshot, registry, record):
    rid = record['raw_record_id']
    if rid in snapshot['raw_records']:
        return
    raw = registry.raw_bytes(rid)
    metadata = deepcopy(record)
    metadata['availability_proof'] = record.get('availability_evidence')
    metadata['quality'] = 'preliminary' if record['source_id'] == DONKI else 'model'
    snapshot['source_versions'].setdefault(record['source_id'], {})[rid] = metadata
    snapshot['raw_records'][rid] = {'metadata': deepcopy(record), 'encoding': 'base64',
                                    'content_base64': base64.b64encode(raw).decode('ascii')}


def _empty_snapshot():
    return {'adapter_version': ADAPTER_VERSION, 'schema_version': '2.1',
            'samples': [], 'events': [], 'raw_records': {}, 'source_versions': {},
            'coverage_map': {}, 'excluded': [], 'notification_audit': [],
            'limitations': [
                'Publication eligibility relies on dated provider releases, not independent historical captures.',
                'Notification inventory is not continuous monitoring coverage or evidence that no event occurred.',
                'GOES threshold alerts do not provide a numerical GOES flux series.',
                'Unknown event end/validity must not be replaced with 24 hours.',
                'NOAA daily probabilities retain their full daily support; no rescaling to an EVA window.']}


def _notifications(snapshot, registry, cutoff, *, context_start=None, end=None):
    groups = defaultdict(list)
    for record in _source_records(registry, DONKI):
        reason = None
        if record['published_utc'] is None or record['available_utc'] is None:
            reason = 'unknown_publication_or_availability'
        elif cutoff is not None and (utc(record['published_utc']) > cutoff or utc(record['available_utc']) > cutoff):
            reason = 'not_available_at_cutoff'
        if reason:
            snapshot['excluded'].append({'raw_record_id': record['raw_record_id'], 'reason': reason})
        else:
            groups[record['release_id']].append(record)
    for mid, records in sorted(groups.items()):
        if len({r['sha256'] for r in records}) > 1:
            snapshot['excluded'].extend({'raw_record_id': r['raw_record_id'],
                                        'reason': 'ambiguous_notification_versions'} for r in records)
            continue
        record = min(records, key=lambda r: r['raw_record_id'])
        try:
            parsed = parse_notification(registry.raw_bytes(record['raw_record_id']), record)
        except (NotificationParseError, RegistryError, OSError) as exc:
            snapshot['excluded'].append({'raw_record_id': record['raw_record_id'], 'reason': 'invalid: ' + str(exc)})
            continue
        audit = {key: deepcopy(value) for key, value in parsed.items() if key not in ('samples', 'events')}
        snapshot['notification_audit'].append(audit)
        if parsed['status'] != 'parsed':
            snapshot['excluded'].append({'raw_record_id': record['raw_record_id'],
                                        'reason': parsed['status'] + ': ' + parsed['reason']})
            continue
        events = parsed['events']
        if context_start is not None:
            # This is a display/input selection, not an assumed event duration.
            # Old unclosed events are not silently declared ended or absent.
            def relevant(event):
                earliest = parsed['facts'].get('arrival_earliest_utc')
                latest = parsed['facts'].get('arrival_latest_utc')
                if earliest and latest:
                    return utc(earliest) < end and utc(latest) >= context_start
                return (event.start_utc is not None and event.start_utc < end
                        and ((event.end_utc is not None and event.end_utc > context_start)
                             or (event.end_utc is None and event.start_utc >= context_start)))
            events = [e for e in events if relevant(e)]
        if not events:
            snapshot['excluded'].append({'raw_record_id': record['raw_record_id'],
                                        'reason': 'outside_requested_display_context; unknown ends remain unknown'})
            continue
        try:
            _attach(snapshot, registry, record)
        except (RegistryError, OSError) as exc:
            snapshot['excluded'].append({'raw_record_id': record['raw_record_id'], 'reason': 'invalid: ' + str(exc)})
            continue
        snapshot['events'].extend(events)
        snapshot['samples'].extend(parsed['samples'])
        snapshot['source_versions'][DONKI][record['raw_record_id']]['content_audit'] = {
            'parser_version': parsed['parser_version'], 'target_scope': parsed['target_scope'],
            'eligibility': 'audited_dated_notification', 'policy': parsed['availability_policy'],
            'facts': deepcopy(parsed['facts'])}


def _forecast_channels(snapshot, registry, cutoff, start, end):
    for source in (SOURCE_3DAY, SOURCE_DAYPRE):
        channel_ids = sorted({c['channel_id'] for r in _source_records(registry, source) for c in r['channels']})
        # Even a missing product must have its principal gap explicitly reported.
        if not channel_ids:
            channel_ids = ['noaa_kp', 's1_or_greater_probability'] if source == SOURCE_3DAY else ['whole_disk_proton_probability']
        for channel in channel_ids:
            key = source + ':' + channel
            if source not in registry.source_ids:
                report = {'status': 'missing', 'reason': 'source_absent', 'coverage_fraction': 0.0,
                          'covered_seconds': 0.0, 'gaps': [{'valid_from_utc': iso_utc(start), 'valid_to_utc': iso_utc(end)}],
                          'record': None, 'cells': []}
            else:
                report = replay_forecast(registry, source_id=source, channel_id=channel,
                    cutoff_utc=cutoff, valid_from_utc=start, valid_to_utc=end)
            snapshot['coverage_map'][key] = {k: deepcopy(v) for k, v in report.items() if k not in ('record', 'cells')}
            snapshot['coverage_map'][key].update(kind='external_forecast', channel_id=channel,
                raw_record_id=report['record']['raw_record_id'] if report['record'] else None)
            if report['status'] not in ('full', 'partial') or not report['record']:
                continue
            record = report['record']
            try:
                _attach(snapshot, registry, record)
            except (RegistryError, OSError) as exc:
                snapshot['coverage_map'][key].update(status='invalid', reason=str(exc), coverage_fraction=0,
                    covered_seconds=0, gaps=[{'valid_from_utc': iso_utc(start), 'valid_to_utc': iso_utc(end)}])
                continue
            for cell in report['cells']:
                snapshot['samples'].append(EnvironmentSample(
                    t_utc=utc(cell['valid_from_utc']), channel_id=cell['channel_id'], value=cell['value'],
                    unit=cell['unit'], source_id=source, kind=Kind.EXTERNAL_FORECAST,
                    published_utc=utc(record['published_utc']), valid_from_utc=utc(cell['valid_from_utc']),
                    valid_to_utc=utc(cell['valid_to_utc']), fetched_utc=utc(record['fetched_utc']),
                    quality='model', raw_record_id=record['raw_record_id'], version=record['version']))


def _coverage(snapshot, start=None, end=None):
    snapshot['coverage_map']['goes_p_ge10MeV:observations'] = {
        'status': 'missing', 'coverage_fraction': 0.0,
        'reason': 'numerical historical GOES observations not present; threshold notifications do not replace them'}
    intervals = sorted({(s.valid_from_utc, s.valid_to_utc) for s in snapshot['samples']
                        if s.channel_id == 'kp' and s.kind == Kind.OBSERVATION})
    kp_coverage = {'status': 'sparse' if intervals else 'missing', 'kind': 'observation',
                   'intervals': [{'valid_from_utc': iso_utc(a), 'valid_to_utc': iso_utc(b)} for a, b in intervals],
                   'reason': 'only synoptic intervals explicitly reported in admitted notifications'}
    if start is not None:
        cursor, gaps = start, []
        for a, b in intervals:
            a, b = max(start, a), min(end, b)
            if b <= a:
                continue
            if a > cursor:
                gaps.append((cursor, a))
            cursor = max(cursor, b)
        if cursor < end:
            gaps.append((cursor, end))
        missing = sum((b-a).total_seconds() for a, b in gaps)
        kp_coverage.update(coverage_fraction=1-missing/(end-start).total_seconds(),
            gaps=[{'valid_from_utc': iso_utc(a), 'valid_to_utc': iso_utc(b)} for a, b in gaps])
    snapshot['coverage_map']['kp:observations'] = kp_coverage
    snapshot['coverage_map']['donki:notifications'] = {
        'status': 'inventory_only', 'physical_coverage_fraction': None,
        'included_messages': len(snapshot['source_versions'].get(DONKI, {})),
        'parsed_events': len(snapshot['events']), 'complete_event_catalog': False,
        'reason': 'no notification is not a declaration of no SEP/GST; ends and monitoring gaps may be unknown'}


def history_snapshot(request: Request, *, repo_root: str | Path = ROOT,
                     registry: SourceRegistry | None = None, lookback_hours: int = 48) -> dict:
    """Typed samples/events, exact raw bytes, source versions and per-channel gaps.

    For review, observations may use later publications, while the NOAA forecast
    panel deliberately shows releases known at request start. For a forecast,
    every used record must be published AND available by its explicit cutoff.
    """
    if not isinstance(request, Request) or request.mode not in ('history_review', 'history_forecast'):
        raise ValueError('A historical Request is required')
    requested_start = utc(request.eva_start_utc)
    if not datetime(2024, 5, 1, tzinfo=timezone.utc) <= requested_start < datetime(2024, 7, 1, tzinfo=timezone.utc):
        raise ValueError('Historical request start must be in May–June 2024')
    for name, low, high in [('duration_min', 60, 480), ('search_period_min', 0, 1440)]:
        value = getattr(request, name)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'{name} must be an integer in [{low}, {high}]')
    if type(lookback_hours) is not int or not 0 <= lookback_hours <= 168:
        raise ValueError('lookback_hours must be an integer in [0, 168]')
    if type(request.work_delay_min) is not int or not 0 <= request.work_delay_min <= 180:
        raise ValueError('work_delay_min must be an integer in [0, 180]')
    start = requested_start + timedelta(minutes=request.work_delay_min)
    cutoff = None
    if request.mode == 'history_forecast':
        if request.cutoff_utc is None:
            raise ValueError('history_forecast requires cutoff_utc')
        cutoff = utc(request.cutoff_utc)
        if cutoff > requested_start:
            raise ValueError('cutoff_utc cannot be after forecast start')
    elif request.cutoff_utc is not None:
        raise ValueError('Review has no publication cutoff; use history_forecast')
    registry = _registry(repo_root, registry)
    end = start + timedelta(minutes=request.duration_min + request.search_period_min)
    snapshot = _empty_snapshot()
    snapshot.update(mode=request.mode, cutoff_utc=iso_utc(cutoff) if cutoff else None,
        requested_start_utc=iso_utc(requested_start), work_delay_min=request.work_delay_min,
        valid_from_utc=iso_utc(start), valid_to_utc=iso_utc(end),
        forecast_as_of_utc=iso_utc(cutoff or requested_start), notification_lookback_hours=lookback_hours,
        disabled_sources=list(request.disabled_sources))
    _forecast_channels(snapshot, registry, cutoff or requested_start, start, end)
    _notifications(snapshot, registry, cutoff, context_start=start-timedelta(hours=lookback_hours), end=end)
    _coverage(snapshot, start, end)
    # Source disabling controls acquisition, not erasure of an already verified
    # offline archive. No network is used and no shared cache is mutated here.
    snapshot['archive_access'] = 'verified_offline_cache; disabling network acquisition preserves admissible cached data'
    snapshot['samples'].sort(key=lambda s: (s.t_utc, s.source_id, s.channel_id, s.raw_record_id))
    snapshot['events'].sort(key=lambda e: (e.start_utc, e.raw_record_id))
    snapshot['raw_record_ids'] = sorted(snapshot['raw_records'])
    return snapshot


def history_bundle(request: Request | None = None, *, repo_root: str | Path = ROOT,
                   registry: SourceRegistry | None = None):
    """B1 tuple interface; pass Request to include the correct NOAA releases.

    Legacy no-argument mode supplies the audited notification catalog only.
    It never guesses a forecast cutoff. B1 may then apply its own cutoff to
    these independently dated entries. Metadata is under raw['_history'].
    """
    if request is None:
        registry = _registry(repo_root, registry)
        snapshot = _empty_snapshot()
        snapshot.update(mode='legacy_notification_catalog', cutoff_utc=None,
            limitations=_empty_snapshot()['limitations'] + ['NOAA forecast selection requires an explicit Request.'])
        _notifications(snapshot, registry, None)
        _coverage(snapshot)
        snapshot['raw_record_ids'] = sorted(snapshot['raw_records'])
    else:
        snapshot = history_snapshot(request, repo_root=repo_root, registry=registry)
    raw = deepcopy(snapshot['raw_records'])
    raw['_history'] = {key: deepcopy(value) for key, value in snapshot.items()
                       if key not in ('samples', 'events', 'raw_records')}
    return list(snapshot['samples']), list(snapshot['events']), raw
