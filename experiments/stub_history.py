# -*- coding: utf-8 -*-
"""ВРЕМЕННЫЙ поставщик исторических данных из архива DONKI (до vkd/history, A2).

Область Б: experiments/. Разбирает data/archive_2024/donki_*.json:
  * sentNotifications → EventInterval с published_utc = messageIssueTime —
    ЭТО датированные выпуски, пригодные для строгого режима;
  * GST.allKpIndex → EnvironmentSample с published_utc = None — вложенные
    наблюдения без собственного времени публикации; в строгом режиме
    исключаются фильтром apply_cutoff (см. разбор Codex п. 1), в разборе
    после факта — используются;
  * SEP.eventTime → EventInterval(kind=observation) с published_utc =
    submissionTime карточки; версия карточки — versionId.
Заменяется импортом vkd.history, когда появится.
"""
from __future__ import annotations

import io
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from vkd.types import EnvironmentSample, EventInterval, Kind

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCH = os.path.join(_ROOT, 'data', 'archive_2024')


def _t(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    return datetime.fromisoformat(s.replace('Z', '+00:00'))


def _load(kind: str) -> list[dict]:
    p = os.path.join(ARCH, 'donki_%s_2024-05-01_2024-06-30.json' % kind)
    return json.load(io.open(p, encoding='utf-8')) if os.path.exists(p) else []


def gst_kp_samples() -> tuple[list[EnvironmentSample], dict]:
    """Kp из карточек бурь. published_utc = None: вложенные наблюдения без
    собственной публикации → в строгом режиме исключаются."""
    out, raw = [], {}
    for g in _load('gst'):
        for k in g.get('allKpIndex', []):
            t = _t(k['observedTime'])
            rid = 'donki_gst#%s#%s' % (g['gstID'], k['observedTime'])
            # observedTime в DONKI — КОНЕЦ трёхчасового интервала Kp (сверено с GFZ за 10.05.2024)
            out.append(EnvironmentSample(t, 'kp', float(k['kpIndex']), '', 'nasa_donki_gst', Kind.OBSERVATION,
                                         None, t - timedelta(hours=3), t, datetime.now(timezone.utc), 'preliminary', rid,
                                         version=str(g.get('versionId'))))
            raw[rid] = {'gstID': g['gstID'], 'kp': k, 'card_submissionTime': g.get('submissionTime'), 'versionId': g.get('versionId')}
    return out, raw


def notifications_events() -> tuple[list[EventInterval], dict]:
    """Датированные уведомления DONKI (ALERT/WARNING/…) как события с публикацией."""
    out, raw = [], {}
    for kind, key_time in (('gst', 'startTime'), ('sep', 'eventTime'), ('flr', 'beginTime'), ('cme', 'startTime')):
        for card in _load(kind):
            for n in card.get('sentNotifications', []) or []:
                mid, issued = n.get('messageID'), _t(n.get('messageIssueTime'))
                if not mid or issued is None:
                    continue
                rid = 'donki_msg#' + mid
                out.append(EventInterval(
                    event_id=rid, kind_of_event=kind.upper(), kind=Kind.EXTERNAL_FORECAST,
                    start_utc=_t(card.get(key_time)), end_utc=None, start_uncertain=True, end_uncertain=True,
                    valid_from_utc=issued, valid_to_utc=None, source_id='nasa_donki_notification',
                    published_utc=issued, raw_record_id=rid,
                    note='%s, сообщение %s' % (card.get('gstID') or card.get('sepID') or card.get('flrID') or card.get('activityID', ''), mid)))
                raw[rid] = {'messageID': mid, 'messageIssueTime': n.get('messageIssueTime'), 'messageURL': n.get('messageURL'),
                            'card': card.get('gstID') or card.get('sepID') or card.get('flrID') or card.get('activityID')}
    return out, raw


def sep_events() -> tuple[list[EventInterval], dict]:
    """Протонные события как наблюдения; публикация = submissionTime карточки."""
    out, raw = [], {}
    for s in _load('sep'):
        rid = 'donki_sep#' + s['sepID']
        instr = (s.get('instruments') or [{}])[0].get('displayName', '')
        out.append(EventInterval(rid, 'SEP', Kind.OBSERVATION if 'MODEL' not in instr else Kind.EXTERNAL_FORECAST,
                                 _t(s.get('eventTime')), None, False, True, None, None, 'nasa_donki_sep',
                                 _t(s.get('submissionTime')), rid, note=instr))
        raw[rid] = {'sepID': s['sepID'], 'eventTime': s.get('eventTime'), 'submissionTime': s.get('submissionTime'),
                    'instrument': instr, 'versionId': s.get('versionId'), 'link': s.get('link')}
    return out, raw


def history_bundle():
    """Всё вместе: образцы Kp, события уведомлений и SEP, сырые записи."""
    ks, kr = gst_kp_samples()
    ne, nr = notifications_events()
    se, sr = sep_events()
    return ks, ne + se, {**kr, **nr, **sr}
