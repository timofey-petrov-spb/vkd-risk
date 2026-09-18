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
import re
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


_A1_PUB: Optional[dict] = None
_A1_PATH: dict = {}


def _a1_published() -> dict:
    """messageID → published_utc из реестра A1 (data/source_registry_2024/donki/records.json);
    попутно messageID → путь к каноническому телу сообщения."""
    global _A1_PUB
    if _A1_PUB is None:
        _A1_PUB = {}
        path = os.path.join(_ROOT, 'data', 'source_registry_2024', 'donki', 'records.json')
        if os.path.exists(path):
            for r in json.load(io.open(path, encoding='utf-8')).get('records', []):
                t = _t(r.get('published_utc'))
                if t is not None:
                    _A1_PUB[r['release_id']] = max(t, _A1_PUB.get(r['release_id'], t))
                    _A1_PATH[r['release_id']] = os.path.join(_ROOT, r['raw_path'])
    return _A1_PUB


_KP_RE = re.compile(r'Kp(?: index)?(?: has reached level| of| is expected to reach| =|:)?\s*(\d+(?:\.\d+)?)', re.I)


def message_kp(mid: str) -> Optional[float]:
    """Максимальный Kp, названный в теле уведомления DONKI (реестр A1); None — не найден.
    Уровень нужен, чтобы уведомление о буре давало условие только при Kp ≥ порога,
    как и наблюдение Kp (иначе буря G2 помечала бы окна наравне с G3+)."""
    _a1_published()
    p = _A1_PATH.get(mid)
    if not p or not os.path.exists(p):
        return None
    try:
        body = json.load(io.open(p, encoding='utf-8')).get('messageBody', '') or ''
    except (OSError, ValueError):
        return None
    vals = [float(m.group(1)) for m in _KP_RE.finditer(body)]
    vals = [v for v in vals if 0 <= v <= 9]
    return max(vals) if vals else None


def notifications_events() -> tuple[list[EventInterval], dict]:
    """Датированные уведомления DONKI (ALERT/WARNING/…) как события с публикацией."""
    out, raw = [], {}
    for kind, key_time in (('gst', 'startTime'), ('sep', 'eventTime'), ('flr', 'beginTime'), ('cme', 'startTime')):
        for card in _load(kind):
            for n in card.get('sentNotifications', []) or []:
                mid, issued = n.get('messageID'), _t(n.get('messageIssueTime'))
                if not mid or issued is None:
                    continue
                # реестр A1 хранит более позднее из времени API и времени в теле сообщения
                # (API округляет вниз до минуты; у 20240516-7D-001 разница 13 ч 56 мин) — берём его
                issued = max(issued, _a1_published().get(mid, issued))
                rid = 'donki_msg#' + mid
                kp = message_kp(mid) if kind == 'gst' else None
                out.append(EventInterval(
                    event_id=rid, kind_of_event=kind.upper(), kind=Kind.EXTERNAL_FORECAST,
                    start_utc=_t(card.get(key_time)), end_utc=None, start_uncertain=True, end_uncertain=True,
                    valid_from_utc=issued, valid_to_utc=None, source_id='nasa_donki_notification',
                    published_utc=issued, raw_record_id=rid,
                    note='%s, сообщение %s%s' % (card.get('gstID') or card.get('sepID') or card.get('flrID') or card.get('activityID', ''), mid,
                                                 (', Kp до %g' % kp) if kp is not None else (', Kp не назван' if kind == 'gst' else ''))))
                raw[rid] = {'messageID': mid, 'messageIssueTime': n.get('messageIssueTime'), 'messageURL': n.get('messageURL'),
                            'card': card.get('gstID') or card.get('sepID') or card.get('flrID') or card.get('activityID'),
                            'kp_in_body': kp, 'published_utc_a1': (_a1_published().get(mid) or issued).isoformat()}
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


# Оценки Kp прогона ENLIL по углу IMF: kp_180 — южное поле, верхняя оценка; kp_90 — типичная.
# Какие поля дают «Kp до …» для условия (максимум из них) — настройка [history].enlil_kp_fields;
# выбор kp_90 обоснован в docs/EKSPERIMENTY_PROGNOZ.md: kp_180 давал 54 ложные тревоги на
# контроле против 3 при той же полноте на буре Гэннон. Верхняя оценка остаётся в тексте.
from vkd.config import section as _cfg_section   # noqa: E402
ENLIL_KP_FIELDS = tuple(_cfg_section('history').get('enlil_kp_fields', ('kp_90',)))


def enlil_arrivals() -> tuple[list[EventInterval], dict]:
    """Прогнозы прихода выбросов к Земле по WSA-ENLIL из карточек CME DONKI: датированный
    ВНЕШНИЙ прогноз. Публикация = modelCompletionTime прогона (запасной вариант —
    submissionTime анализа). Ожидаемый Kp — максимум из kp_18/90/135/180 прогона, если задан.
    Каждый прогон — отдельная запись; связанные прогоны сводит в одно условие compare."""
    out, raw = [], {}
    for c in _load('cme'):
        for a in c.get('cmeAnalyses') or []:
            for e in a.get('enlilList') or []:
                arr = _t(e.get('estimatedShockArrivalTime'))
                if arr is None:
                    continue
                pub = _t(e.get('modelCompletionTime')) or _t(a.get('submissionTime'))
                dur_h = e.get('estimatedDuration')
                kps = [e.get(k) for k in ENLIL_KP_FIELDS if e.get(k) is not None]
                upper = e.get('kp_180')
                rid = 'donki_enlil#%s#%s' % (c.get('activityID'), (e.get('modelCompletionTime') or '?').replace(':', ''))
                note = 'WSA-ENLIL, прогон %s: приход %s%s%s; glancing blow: %s' % (
                    e.get('modelCompletionTime'), arr.strftime('%m-%d %H:%MZ'),
                    ', длительность %s ч' % dur_h if dur_h is not None else '',
                    (', Kp до %g (типичная оценка %s%s)' % (max(kps), '/'.join(ENLIL_KP_FIELDS),
                                                            '; верхняя при южном поле %g' % upper if upper is not None else ''))
                    if kps else ', Kp не оценён', 'да' if e.get('isEarthGB') else 'нет')
                out.append(EventInterval(
                    event_id=rid, kind_of_event='CME_ARRIVAL', kind=Kind.EXTERNAL_FORECAST,
                    start_utc=arr, end_utc=(arr + timedelta(hours=float(dur_h))) if dur_h is not None else None,
                    start_uncertain=True, end_uncertain=True, valid_from_utc=arr,
                    valid_to_utc=(arr + timedelta(hours=float(dur_h))) if dur_h is not None else None,
                    source_id='nasa_donki_wsa_enlil', published_utc=pub, raw_record_id=rid, note=note))
                raw[rid] = {'activityID': c.get('activityID'), 'cme_startTime': c.get('startTime'),
                            'analysis_submissionTime': a.get('submissionTime'), 'speed_km_s': a.get('speed'),
                            'modelCompletionTime': e.get('modelCompletionTime'),
                            'estimatedShockArrivalTime': e.get('estimatedShockArrivalTime'),
                            'estimatedDuration_h': dur_h, 'kp_18': e.get('kp_18'), 'kp_90': e.get('kp_90'),
                            'kp_135': e.get('kp_135'), 'kp_180': e.get('kp_180'), 'isEarthGB': e.get('isEarthGB'),
                            'isEarthMinorImpact': e.get('isEarthMinorImpact'), 'link': e.get('link')}
    return out, raw


def history_bundle():
    """Всё вместе: образцы Kp, события уведомлений, SEP и прогнозы прихода выбросов, сырые записи."""
    ks, kr = gst_kp_samples()
    ne, nr = notifications_events()
    se, sr = sep_events()
    ce, cr = enlil_arrivals()
    return ks, ne + se + ce, {**kr, **nr, **sr, **cr}
