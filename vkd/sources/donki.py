"""Audited DONKI notification bodies, without consulting mutable event cards.

Only explicit facts in the main summary are parsed. Notes about associated
events cannot change the primary event's time, detector or particle channel.
Unsupported formats are reported, never converted to an empty successful event.
"""
from __future__ import annotations

import json
import re
from datetime import timedelta

from vkd.types import EnvironmentSample, EventInterval, Kind
from .registry import iso_utc, utc

PARSER_VERSION = 'donki-body-v1'
SOURCE_ID = 'nasa_donki_notification'
STAMP = r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?Z'


class NotificationParseError(ValueError):
    """An indexed notification disagrees with its body or has invalid facts."""


def parse_notification(raw: bytes, record: dict) -> dict:
    """Return shared typed objects and a private audit of the parsed facts.

    A threshold crossing is an event, not a measurement equal to its threshold.
    Unknown ends and validity remain None. Earth/L1/GEO are not interchangeable.
    """
    try:
        message = json.loads(raw)
        body = message['messageBody']
        mid, kind = message['messageID'], message['messageType']
        body_ids = re.findall(r'^## Message ID:\s*(\S+)\s*$', body, re.M)
        body_dates = re.findall(r'^## Message Issue Date:\s*(' + STAMP + r')\s*$', body, re.M)
        if body_ids != [mid] or len(body_dates) != 1:
            raise NotificationParseError('Missing, duplicate or inconsistent notification headers')
        api_issue, body_issue = utc(message['messageIssueTime']), utc(body_dates[0])
        published = max(api_issue, body_issue)
        if (record['source_id'] != SOURCE_ID or mid != record['release_id']
                or kind != record['message_type'] or published != utc(record['published_utc'])):
            raise NotificationParseError('Message identity/publication differs from archive metadata')
        if '## Summary:' not in body:
            raise NotificationParseError('Missing primary summary')
        summary = body.split('## Summary:', 1)[1].split('## Notes:', 1)[0].strip()
        first = re.split(r'\n\s*\n', summary, maxsplit=1)[0].strip()
        result = {'parser_version': PARSER_VERSION, 'message_id': mid,
                  'raw_record_id': record['raw_record_id'], 'message_type': kind,
                  'published_utc': iso_utc(published), 'target_scope': 'unknown',
                  'status': 'unsupported', 'reason': 'unrecognized_primary_summary',
                  'samples': [], 'events': [], 'facts': {},
                  'availability_policy': 'dated_provider_message; content-audited, not independent archive capture',
                  'limitations': ['Experimental NASA research notification, not an operational NOAA WARNING.',
                                  'Unknown duration is not zero or a 24-hour interval.',
                                  'Content hash fixes this retrieved version, not its complete publication history.']}
        if abs((api_issue - body_issue).total_seconds()) >= 60:
            result.update(status='publication_conflict', reason='API/body issue times differ by at least one minute')
            return result
        if kind.lower() == 'report':
            result.update(status='context_only', reason='weekly retrospective report is not a current event')
            return result

        def event(event_kind, start, *, end=None, origin=Kind.OBSERVATION,
                  uncertain=False, scope='earth', note=''):
            if end is not None and end <= start:
                raise NotificationParseError('Reversed physical event interval')
            if origin == Kind.OBSERVATION and (end or start) > published:
                raise NotificationParseError('Observed event extends beyond notification publication')
            result.update(status='parsed', reason=None, target_scope=scope)
            result['events'].append(EventInterval(
                event_id=record['raw_record_id'] + ':' + event_kind,
                kind_of_event=event_kind, kind=origin, start_utc=start, end_utc=end,
                start_uncertain=uncertain, end_uncertain=end is None,
                valid_from_utc=None, valid_to_utc=None, source_id=SOURCE_ID,
                published_utc=published, raw_record_id=record['raw_record_id'],
                note=note + '; исходное сообщение ' + mid))

        if kind == 'GST':
            match = re.search(r"Geomagnetic Kp index has reached level ([\d.]+).*?"
                              r'synoptic period (' + STAMP + r') to (' + STAMP + r')', first)
            if match and "Earth's magnetosphere" in first:
                value, start, end = float(match[1]), utc(match[2]), utc(match[3])
                if not 0 <= value <= 9 or end - start != timedelta(hours=3):
                    raise NotificationParseError('Invalid Kp or synoptic interval')
                event('GST', start, end=end, note=f'Наблюдение Kp={value:g} за указанные 3 часа; не прогноз будущего окна')
                result['samples'].append(EnvironmentSample(
                    t_utc=end, channel_id='kp', value=value, unit='1', source_id=SOURCE_ID,
                    kind=Kind.OBSERVATION, published_utc=published,
                    valid_from_utc=start, valid_to_utc=end, fetched_utc=utc(record['fetched_utc']),
                    quality='preliminary', raw_record_id=record['raw_record_id'], version=record['version']))
                result['facts'] = {'kp': value, 'synoptic_from_utc': iso_utc(start),
                                   'synoptic_to_utc': iso_utc(end), 'time_semantics': 'observation at interval end'}
        elif kind == 'SEP':
            detector = re.match(r'Solar energetic particle event (detected by|forecasted at the orbit of) ([^.]+)\.', first)
            if detector is None:
                return result
            # Restrict channel/time parsing to the primary paragraph. Later notes
            # can mention GOES in an alert whose actual detector is STEREO A.
            name = detector[2]
            if detector[1].startswith('forecasted') or 'GOES' not in name:
                scope = 'stereo_a' if 'STEREO' in name else ('soho_l1' if 'SOHO' in name else 'other_or_unknown')
                result.update(status='out_of_scope', reason='not a GOES observation at Earth', target_scope=scope)
                result['facts'] = {'detector': name, 'is_prediction': detector[1].startswith('forecasted')}
                return result
            match = re.search(r'flux of\s*>\s*(\d+(?:\.\d+)?)\s*MeV protons exceeds\s*'
                              r'(\d+(?:\.\d+)?)\s*pfu starting at (' + STAMP + r')', first)
            if match:
                energy, threshold, start = float(match[1]), float(match[2]), utc(match[3])
                if energy <= 0 or threshold <= 0:
                    raise NotificationParseError('Invalid proton threshold')
                event('SEP', start, note=f'GOES: >{energy:g} МэВ, поток >{threshold:g} pfu; пороговое сообщение, значение потока и конец неизвестны')
                result['facts'] = {'detector': 'GOES', 'energy_lower_bound_MeV': energy,
                                   'energy_operator': '>', 'flux_lower_bound_pfu': threshold,
                                   'flux_operator': '>', 'measured_flux_pfu': None,
                                   'noaa_s_scale': None}
        elif kind == 'CME':
            # "Heliocentric Earth Equatorial coordinates" is not an Earth impact.
            arrivals = re.findall(r'(?:may|might|will)\s+reach\s+(?:NASA missions near Earth|Earth)\s+at\s+(?:about\s+)?('
                                  + STAMP + r')(?:\s*\(plus minus ([\d.]+) hours\))?', summary, re.I)
            if len(arrivals) == 1:
                start, uncertainty = utc(arrivals[0][0]), arrivals[0][1]
                hours = float(uncertainty) if uncertainty else None
                event('CME_arrival', start, origin=Kind.EXTERNAL_FORECAST, uncertain=True,
                      note='Модельный приход CME к Земле; неопределённость времени не является длительностью бури')
                result['facts'] = {'arrival_utc': iso_utc(start), 'arrival_uncertainty_h': hours,
                                   'arrival_earliest_utc': iso_utc(start-timedelta(hours=hours)) if hours is not None else None,
                                   'arrival_latest_utc': iso_utc(start+timedelta(hours=hours)) if hours is not None else None}
            elif not re.search(r'(?<![A-Za-z])(?:missions near Earth|impact Earth|reach Earth)(?![A-Za-z])', summary, re.I):
                result.update(status='out_of_scope', reason='no explicit Earth arrival in primary summary', target_scope='other_targets')
        elif kind == 'FLR' and 'detected by GOES' in summary:
            match = re.search(r'Flare start time:\s*(' + STAMP + r')', summary)
            crossing = re.search(r'Flare [MX][\d.]+ crossing time:\s*(' + STAMP + r')', summary)
            chosen = match or crossing
            if chosen:
                event('FLR', utc(chosen[1]), uncertain=match is None, scope='solar_observed_by_goes',
                      note='Информация о вспышке; не измерение протонов у МКС')
                result['facts'] = {'time_semantics': 'flare_start' if match else 'threshold_crossing_not_flare_start'}
        elif kind == 'IPS':
            match = re.search(r'Significant interplanetary shock detected by .+? at L1 at (' + STAMP + r')', first)
            if match:
                event('IPS', utc(match[1]), scope='earth_l1', note='Ударная волна у L1; не момент воздействия на МКС')
        elif kind == 'MPC':
            match = re.search(r'(?:starting at|since)\s+(' + STAMP + r')', first)
            if match and 'Simulations' in first and 'geosynchronous orbit' in first:
                event('MPC', utc(match[1]), origin=Kind.EXTERNAL_FORECAST, uncertain=True,
                      scope='earth_geo', note='Внешнее моделирование магнитопаузы около GEO; не наблюдение и не положение МКС')
        elif kind == 'RBE':
            match = re.search(r'GOES.*?electron flux.*?starting at (' + STAMP + r')', first)
            if match:
                event('RBE', utc(match[1]), scope='earth_outer_belt',
                      note='Электроны внешнего пояса у GOES; не поток протонов или доза у МКС')
        return result
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError) as exc:
        if isinstance(exc, NotificationParseError):
            raise
        raise NotificationParseError(str(exc)) from exc
