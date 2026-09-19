# -*- coding: utf-8 -*-
"""ВРЕМЕННЫЙ поставщик исторических данных из архива DONKI (до vkd/history, A2).

Область Б: experiments/. Разбирает data/archive_2024/donki_*.json и реестр A1
(data/source_registry_2024/donki/records.json — тела и время выпуска уведомлений):
  * sentNotifications → EventInterval с published_utc = messageIssueTime (более позднее
    из времени API и времени в теле, как в реестре A1) — ЭТО датированные выпуски,
    пригодные для строгого режима (CONTRACT §10). Происхождение (наблюдение / внешний
    прогноз) определяется по телу сообщения, а не по типу файла: «Kp index has reached»,
    «detected by GOES» — наблюдение; «SEP Prediction», «forecasted», «expected» — прогноз;
  * карточки событий SEP → EventInterval с published_utc = None: карточка версии N —
    не датированный выпуск (submissionTime меняется при редактировании: у 6 из 27
    карточек versionId > 1), поэтому в строгом режиме apply_cutoff исключает их с
    причиной, а в разборе после факта они используются (CONTRACT §10, R4);
  * прогнозы прихода выброса WSA-ENLIL из карточек CME → внешний прогноз; публикация
    принята = завершение прогона + запас [history].enlil_publication_lag_min, но не
    раньше подачи анализа (submissionTime): у 42 из 98 прогонов анализ переподан позже
    прогона, у 13 — в 2025 году. Доступность к отсечке этим не доказана — политика
    прототипа R10, и она названа в записи;
  * наблюдения Kp — окончательный ряд GFZ из data/spaceweather/kp_ap_sn_f107.txt
    (признак D, интервалы по 3 ч), published_utc = None: только разбор после факта;
    GST.allKpIndex карточек DONKI (те же значения NOAA, округлённые) — резерв, если
    файла GFZ нет.
Каждая сырая запись несёт происхождение архива: файл, SHA-256, адрес API и когда
архив оказался в репозитории (точное время выгрузки не записано — верхняя граница).
Заменяется импортом vkd.history, когда появится.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

from vkd.config import section as _cfg_section   # Т7: настройки вне кода
from vkd.types import EnvironmentSample, EventInterval, Kind

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCH = os.path.join(_ROOT, 'data', 'archive_2024')
GFZ_FILE = os.path.join(_ROOT, 'data', 'spaceweather', 'kp_ap_sn_f107.txt')
ARCHIVE_PERIOD = (datetime(2024, 5, 1, tzinfo=timezone.utc), datetime(2024, 7, 1, tzinfo=timezone.utc))
# Архив DONKI добавлен в репозиторий коммитом 1f732f4 (2026-09-18 15:43Z); момент самой выгрузки
# с api.nasa.gov не записан, поэтому это ВЕРХНЯЯ граница времени получения, и так она подписана.
ARCHIVE_IN_REPO_UTC = datetime(2026, 9, 18, 15, 43, tzinfo=timezone.utc)
DONKI_API = 'https://api.nasa.gov/DONKI/%s?startDate=2024-05-01&endDate=2024-06-30'
# Снимок GFZ скачан 17.09.2026 (data/spaceweather/README.md), время суток не записано
GFZ_SNAPSHOT_UTC = datetime(2026, 9, 17, tzinfo=timezone.utc)
GFZ_SOURCE = 'GFZ Potsdam, kp.gfz.de, файл Kp_ap_Ap_SN_F107_since_1932.txt (CC BY 4.0)'

_HIST = _cfg_section('history')
# Оценки Kp прогона ENLIL по углу IMF: kp_180 — южное поле, верхняя оценка; kp_90 — типичная.
# Какие поля дают «Kp до …» для условия (максимум из них) — настройка [history].enlil_kp_fields;
# выбор kp_90 обоснован в docs/EKSPERIMENTY_PROGNOZ.md: kp_180 давал 54 ложные тревоги на
# контроле против 3 при той же полноте на буре Гэннон. Верхняя оценка остаётся в тексте.
ENLIL_KP_FIELDS = tuple(_HIST.get('enlil_kp_fields', ('kp_90',)))
# Запас между завершением прогона ENLIL и принятой публикацией, мин (политика прототипа R10;
# ключ [history].enlil_publication_lag_min, умолчание 60 — на случай отсутствия ключа в файле).
ENLIL_PUBLICATION_LAG_MIN = int(_HIST.get('enlil_publication_lag_min', 60))


def _t(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    return datetime.fromisoformat(s.replace('Z', '+00:00'))


def _archive_file(kind: str) -> str:
    return os.path.join(ARCH, 'donki_%s_2024-05-01_2024-06-30.json' % kind)


def _load(kind: str) -> list[dict]:
    p = _archive_file(kind)
    return json.load(io.open(p, encoding='utf-8')) if os.path.exists(p) else []


@lru_cache(maxsize=None)
def _file_sha256(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with io.open(path, 'rb') as fh:
        h.update(fh.read())
    return h.hexdigest()


def archive_provenance(kind: str) -> dict:
    """Происхождение файла архива для сырых записей (Т1): файл, SHA-256, адрес API, верхняя граница получения."""
    p = _archive_file(kind)
    return {'archive_file': os.path.relpath(p, _ROOT).replace('\\', '/'), 'archive_sha256': _file_sha256(p),
            'archive_api_url': DONKI_API % kind.upper(),
            'archive_fetched_not_later_than_utc': ARCHIVE_IN_REPO_UTC.isoformat(),
            'archive_fetched_basis': 'момент выгрузки с api.nasa.gov не записан; файл в репозитории с коммита 1f732f4'}


# ----------------------------------------------------------------- Kp: окончательный ряд GFZ
def gfz_archive_kp_samples(period: tuple = ARCHIVE_PERIOD) -> tuple[list[EnvironmentSample], dict]:
    """Kp по 3-часовым интервалам из файла GFZ (YYYY MM DD … Kp1..Kp8 … D). quality = 'final' при
    D ≥ 1, иначе 'preliminary'. published_utc = None — окончательный ряд не имеет времени
    публикации по интервалам, в строгом режиме исключается автоматически (только разбор, CONTRACT §10).
    t_utc — конец интервала (момент, когда значение стало наблюдённым), valid_from — начало."""
    out, raw = [], {}
    if not os.path.exists(GFZ_FILE):
        return out, raw
    sha = _file_sha256(GFZ_FILE)
    rel = os.path.relpath(GFZ_FILE, _ROOT).replace('\\', '/')
    with io.open(GFZ_FILE, encoding='utf-8') as fh:
        for line in fh:
            if line.startswith('#') or len(line) < 40:
                continue
            parts = line.split()
            try:
                day = datetime(int(parts[0]), int(parts[1]), int(parts[2]), tzinfo=timezone.utc)
            except (ValueError, IndexError):
                continue
            if not (period[0] <= day < period[1]):
                continue
            try:
                kps = [float(x) for x in parts[7:15]]
                d_flag = int(parts[-1])
            except (ValueError, IndexError):
                continue
            q = 'final' if d_flag >= 1 else 'preliminary'
            for i, kp in enumerate(kps):
                if kp < 0:                       # -1.000 — пропуск у GFZ
                    continue
                vf = day + timedelta(hours=3 * i)
                vt = vf + timedelta(hours=3)
                rid = 'gfz_kp_archive#%s' % vf.strftime('%Y-%m-%dT%H:%MZ')
                out.append(EnvironmentSample(vt, 'kp', kp, '', 'gfz_kp_archive', Kind.OBSERVATION, None, vf, vt,
                                             GFZ_SNAPSHOT_UTC, q, rid, version='D=%d' % d_flag))
                raw[rid] = {'file': rel, 'file_sha256': sha, 'line': line.rstrip('\n'), 'interval_from_utc': vf.isoformat(),
                            'interval_to_utc': vt.isoformat(), 'Kp': kp, 'D': d_flag, 'quality': q, 'source': GFZ_SOURCE,
                            'fetched_utc': None, 'fetched_basis': 'снимок скачан %s (README), время суток не записано' % GFZ_SNAPSHOT_UTC.strftime('%d.%m.%Y'),
                            'strict_replay_eligibility': 'final_index_without_publication_time_review_only'}
    return out, raw


def gst_kp_samples() -> tuple[list[EnvironmentSample], dict]:
    """Kp из карточек бурь DONKI (резерв, если файла GFZ нет). published_utc = None: вложенные
    наблюдения без собственной публикации → в строгом режиме исключаются."""
    out, raw = [], {}
    prov = archive_provenance('gst')
    for g in _load('gst'):
        for k in g.get('allKpIndex', []):
            t = _t(k['observedTime'])
            rid = 'donki_gst#%s#%s' % (g['gstID'], k['observedTime'])
            # observedTime в DONKI — КОНЕЦ трёхчасового интервала Kp (сверено с GFZ за 10.05.2024)
            out.append(EnvironmentSample(t, 'kp', float(k['kpIndex']), '', 'nasa_donki_gst', Kind.OBSERVATION,
                                         None, t - timedelta(hours=3), t, ARCHIVE_IN_REPO_UTC, 'preliminary', rid,
                                         version=str(g.get('versionId'))))
            raw[rid] = {'gstID': g['gstID'], 'kp': k, 'card_submissionTime': g.get('submissionTime'), 'versionId': g.get('versionId'),
                        'strict_replay_eligibility': 'nested_observation_without_publication_time', **prov}
    return out, raw


# ----------------------------------------------------------------- реестр A1: уведомления с телами
_A1: Optional[dict] = None


def _a1() -> dict:
    """messageID → запись реестра A1 (published_utc, message_type, strict_replay_eligibility, raw_path, url)."""
    global _A1
    if _A1 is None:
        _A1 = {}
        path = os.path.join(_ROOT, 'data', 'source_registry_2024', 'donki', 'records.json')
        if os.path.exists(path):
            for r in json.load(io.open(path, encoding='utf-8')).get('records', []):
                mid = r.get('release_id')
                t = _t(r.get('published_utc'))
                if not mid or t is None:
                    continue
                prev = _A1.get(mid)
                if prev is None or t > prev['published_utc']:
                    _A1[mid] = {'published_utc': t, 'message_type': r.get('message_type'),
                                'strict_replay_eligibility': r.get('strict_replay_eligibility'),
                                'availability_evidence': r.get('availability_evidence'),
                                'raw_path': os.path.join(_ROOT, r['raw_path']) if r.get('raw_path') else None,
                                'sha256': r.get('sha256'), 'url': r.get('url'), 'limitations': r.get('limitations') or []}
    return _A1


def _a1_published() -> dict:
    """messageID → published_utc (совместимость с прежним интерфейсом)."""
    return {mid: r['published_utc'] for mid, r in _a1().items()}


@lru_cache(maxsize=None)
def message_body(mid: str) -> Optional[str]:
    """Тело уведомления DONKI из реестра A1; None — нет в реестре или не читается."""
    r = _a1().get(mid)
    if not r or not r.get('raw_path') or not os.path.exists(r['raw_path']):
        return None
    try:
        return json.load(io.open(r['raw_path'], encoding='utf-8')).get('messageBody', '') or ''
    except (OSError, ValueError, AttributeError):
        return None


_KP_RE = re.compile(r'Kp(?: index)?(?: has reached level| of| is expected to reach| =|:)?\s*(\d+(?:\.\d+)?)', re.I)


def message_kp(mid: str) -> Optional[float]:
    """Максимальный Kp, названный в теле уведомления DONKI (реестр A1); None — не найден.
    Уровень нужен, чтобы уведомление о буре давало условие только при Kp ≥ порога,
    как и наблюдение Kp (иначе буря G2 помечала бы окна наравне с G3+)."""
    body = message_body(mid)
    if not body:
        return None
    vals = [float(m.group(1)) for m in _KP_RE.finditer(body)]
    vals = [v for v in vals if 0 <= v <= 9]
    return max(vals) if vals else None


_FORECAST_MARKERS = ('sep prediction', 'forecasted', 'is expected to reach', 'expected to reach', 'watch', 'warning')
_OBS_MARKERS = ('has reached', 'detected', 'observed', 'has crossed', 'exceeds 10 pfu starting')


def message_kind(mid: str, card_kind: str, instrument: str = '') -> tuple[Kind, str]:
    """Происхождение уведомления по его телу (реестр A1): (kind, основание).
    Наблюдение — сообщение о наблюдённом начале/уровне («Kp index has reached», «detected by GOES»);
    внешний прогноз — «SEP Prediction» (модель REleASE), «forecasted», «expected», WATCH/WARNING.
    Без тела — по типу карточки: прибор карточки SEP с «MODEL» → прогноз, иначе наблюдение."""
    body = message_body(mid)
    if body:
        head = '\n'.join(l for l in body.split('\n') if l.startswith('## Message Type'))
        low_head, low = head.lower(), body.lower()
        if any(m in low_head for m in _FORECAST_MARKERS):
            return Kind.EXTERNAL_FORECAST, 'тип сообщения: %s' % head.split(':', 1)[-1].strip()[:60]
        first = next((l for l in body.split('\n') if l.strip() and not l.startswith('##')), '').lower()
        if any(m in first for m in _FORECAST_MARKERS):
            return Kind.EXTERNAL_FORECAST, 'в тексте: прогноз («forecasted» / «expected»)'
        if any(m in first or m in low for m in _OBS_MARKERS):
            return Kind.OBSERVATION, 'в тексте: наблюдённое событие («detected» / «has reached»)'
        return Kind.OBSERVATION, 'по типу карточки %s (маркеров прогноза в теле нет)' % card_kind.upper()
    if card_kind == 'sep' and 'MODEL' in (instrument or ''):
        return Kind.EXTERNAL_FORECAST, 'тела нет; прибор карточки — модель'
    return Kind.OBSERVATION, 'тела нет; по типу карточки %s' % card_kind.upper()


A1_LIMIT_RU = 'датированное уведомление DONKI; аудит содержания и версий не завершён (реестр A1)'


def notifications_events() -> tuple[list[EventInterval], dict]:
    """Датированные уведомления DONKI как события с публикацией (основа строгого режима, CONTRACT §10)."""
    out, raw = [], {}
    for kind, key_time in (('gst', 'startTime'), ('sep', 'eventTime'), ('flr', 'beginTime'), ('cme', 'startTime')):
        prov = archive_provenance(kind)
        for card in _load(kind):
            card_id = card.get('gstID') or card.get('sepID') or card.get('flrID') or card.get('activityID', '')
            instr = (card.get('instruments') or [{}])[0].get('displayName', '') if kind == 'sep' else ''
            for n in card.get('sentNotifications', []) or []:
                mid, issued = n.get('messageID'), _t(n.get('messageIssueTime'))
                if not mid or issued is None:
                    continue
                a1 = _a1().get(mid)
                # реестр A1 хранит более позднее из времени API и времени в теле сообщения
                # (API округляет вниз до минуты; у 20240516-7D-001 разница 13 ч 56 мин) — берём его
                issued = max(issued, a1['published_utc']) if a1 else issued
                rid = 'donki_msg#' + mid
                if rid in raw:            # одно сообщение бывает привязано к нескольким карточкам — дубли не создаём (Т1)
                    continue
                kp = message_kp(mid) if kind == 'gst' else None
                k, basis = message_kind(mid, kind, instr)
                elig = (a1 or {}).get('strict_replay_eligibility') or 'not_in_registry_A1'
                out.append(EventInterval(
                    event_id=rid, kind_of_event=kind.upper(), kind=k,
                    start_utc=_t(card.get(key_time)), end_utc=None, start_uncertain=True, end_uncertain=True,
                    valid_from_utc=issued, valid_to_utc=None, source_id='nasa_donki_notification',
                    published_utc=issued, raw_record_id=rid,
                    note='%s, сообщение %s%s; %s; %s' % (
                        card_id, mid, (', Kp до %g' % kp) if kp is not None else (', Kp не назван' if kind == 'gst' else ''),
                        basis, A1_LIMIT_RU)))
                raw[rid] = {'messageID': mid, 'messageIssueTime': n.get('messageIssueTime'), 'messageURL': n.get('messageURL'),
                            'url': (a1 or {}).get('url') or n.get('messageURL'), 'card': card_id, 'kind_of_event': kind.upper(),
                            'kind': k.value, 'kind_basis': basis, 'kp_in_body': kp,
                            'published_utc_a1': issued.isoformat(), 'message_type_a1': (a1 or {}).get('message_type'),
                            'body_sha256_a1': (a1 or {}).get('sha256'),
                            'strict_replay_eligibility': elig, 'limitation_ru': A1_LIMIT_RU,
                            'availability_evidence_a1': (a1 or {}).get('availability_evidence'), **prov}
    return out, raw


def sep_events() -> tuple[list[EventInterval], dict]:
    """Карточки протонных событий DONKI — наблюдения по приборам (MODEL: — прогноз модели REleASE).
    published_utc = None: карточка версии N — не датированный выпуск (CONTRACT §10: карточки —
    только разбор после факта; R4: submissionTime меняется при редактировании). В строгом
    режиме apply_cutoff исключает их с причиной; в разборе — используются."""
    out, raw = [], {}
    prov = archive_provenance('sep')
    for s in _load('sep'):
        rid = 'donki_sep#' + s['sepID']
        instr = (s.get('instruments') or [{}])[0].get('displayName', '')
        ns = sorted((n for n in (s.get('sentNotifications') or []) if n.get('messageIssueTime')), key=lambda n: n['messageIssueTime'])
        first = ns[0] if ns else None
        ver = s.get('versionId')
        out.append(EventInterval(rid, 'SEP', Kind.OBSERVATION if 'MODEL' not in instr else Kind.EXTERNAL_FORECAST,
                                 _t(s.get('eventTime')), None, False, True, None, None, 'nasa_donki_sep_card',
                                 None, rid,
                                 note='%s; карточка события DONKI v%s (подана %s) — только разбор после факта (CONTRACT §10), '
                                      'публикация содержания не датирована%s' % (
                                          instr, ver, s.get('submissionTime'),
                                          ('; первое уведомление %s %s' % (first['messageID'], first['messageIssueTime'])) if first else '')))
        raw[rid] = {'sepID': s['sepID'], 'eventTime': s.get('eventTime'), 'submissionTime': s.get('submissionTime'),
                    'instrument': instr, 'versionId': ver, 'link': s.get('link'), 'url': s.get('link'),
                    'first_notification': first, 'published_utc': None,
                    'strict_replay_eligibility': 'event_card_not_a_dated_release_review_only',
                    'limitation_ru': 'карточка события: submissionTime — подача версии %s, не публикация содержания; %s'
                                     % (ver, 'редактировалась (versionId > 1)' if (ver or 1) > 1 else 'версия 1'), **prov}
    return out, raw


def enlil_arrivals() -> tuple[list[EventInterval], dict]:
    """Прогнозы прихода выбросов к Земле по WSA-ENLIL из карточек CME DONKI: датированный
    ВНЕШНИЙ прогноз. Принятая публикация = max(modelCompletionTime + запас, submissionTime анализа):
    время размещения прогона на сайте в данных нет, а анализ, поданный позже прогона, не мог быть
    виден раньше подачи (T5-1: 13 анализов поданы после июня 2024). Доступность не доказана —
    политика прототипа R10, названа в записи. Ожидаемый Kp — максимум из выбранных полей прогона.
    Каждый прогон — отдельная запись; связанные прогоны сводит в одно условие compare."""
    out, raw = [], {}
    prov = archive_provenance('cme')
    lag = timedelta(minutes=ENLIL_PUBLICATION_LAG_MIN)
    for c in _load('cme'):
        for a in c.get('cmeAnalyses') or []:
            sub = _t(a.get('submissionTime'))
            for e in a.get('enlilList') or []:
                arr = _t(e.get('estimatedShockArrivalTime'))
                if arr is None:
                    continue
                mc = _t(e.get('modelCompletionTime'))
                cands = [t for t in ((mc + lag) if mc else None, sub) if t is not None]
                pub = max(cands) if cands else None
                pub_basis = ('завершение прогона %s + %d мин' % (e.get('modelCompletionTime'), ENLIL_PUBLICATION_LAG_MIN)
                             if pub is not None and mc is not None and pub == mc + lag
                             else ('подача анализа %s (позже прогона)' % a.get('submissionTime') if pub is not None else 'нет ни времени прогона, ни подачи'))
                dur_h = e.get('estimatedDuration')
                kps = [e.get(k) for k in ENLIL_KP_FIELDS if e.get(k) is not None]
                upper = e.get('kp_180')
                rid = 'donki_enlil#%s#%s' % (c.get('activityID'), (e.get('modelCompletionTime') or '?').replace(':', ''))
                note = 'WSA-ENLIL, прогон %s: приход %s%s%s; glancing blow: %s; публикация принята = %s — доступность не доказана (политика прототипа R10)' % (
                    e.get('modelCompletionTime'), arr.strftime('%m-%d %H:%MZ'),
                    ', длительность %s ч' % dur_h if dur_h is not None else '',
                    (', Kp до %g (типичная оценка %s%s)' % (max(kps), '/'.join(ENLIL_KP_FIELDS),
                                                            '; верхняя при южном поле %g' % upper if upper is not None else ''))
                    if kps else ', Kp не оценён', 'да' if e.get('isEarthGB') else 'нет', pub_basis)
                out.append(EventInterval(
                    event_id=rid, kind_of_event='CME_ARRIVAL', kind=Kind.EXTERNAL_FORECAST,
                    start_utc=arr, end_utc=(arr + timedelta(hours=float(dur_h))) if dur_h is not None else None,
                    start_uncertain=True, end_uncertain=True, valid_from_utc=arr,
                    valid_to_utc=(arr + timedelta(hours=float(dur_h))) if dur_h is not None else None,
                    source_id='nasa_donki_wsa_enlil', published_utc=pub, raw_record_id=rid, note=note))
                raw[rid] = {'activityID': c.get('activityID'), 'cme_startTime': c.get('startTime'),
                            'analysis_submissionTime': a.get('submissionTime'), 'speed_km_s': a.get('speed'),
                            'modelCompletionTime': e.get('modelCompletionTime'),
                            'published_utc_policy': pub.isoformat() if pub else None, 'published_utc_basis': pub_basis,
                            'enlil_publication_lag_min': ENLIL_PUBLICATION_LAG_MIN,
                            'estimatedShockArrivalTime': e.get('estimatedShockArrivalTime'),
                            'estimatedDuration_h': dur_h, 'kp_18': e.get('kp_18'), 'kp_90': e.get('kp_90'),
                            'kp_135': e.get('kp_135'), 'kp_180': e.get('kp_180'), 'isEarthGB': e.get('isEarthGB'),
                            'isEarthMinorImpact': e.get('isEarthMinorImpact'), 'link': e.get('link'), 'url': e.get('link'),
                            'strict_replay_eligibility': 'model_run_completion_plus_lag_policy_R10_availability_not_proven',
                            'limitation_ru': 'публикация = завершение прогона + запас, не раньше подачи анализа; доступность к отсечке не доказана (R10)',
                            **prov}
    return out, raw


def history_bundle():
    """Всё вместе: образцы Kp (GFZ окончательный ряд; резерв — карточки GST), события уведомлений,
    карточки SEP и прогнозы прихода выбросов, сырые записи."""
    ks, kr = gfz_archive_kp_samples()
    if not ks:
        ks, kr = gst_kp_samples()
    ne, nr = notifications_events()
    se, sr = sep_events()
    ce, cr = enlil_arrivals()
    return ks, ne + se + ce, {**kr, **nr, **sr, **cr}
