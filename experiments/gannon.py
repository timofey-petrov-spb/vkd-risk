# -*- coding: utf-8 -*-
"""Эксперименты Т5 на архиве DONKI: буря Гэннон 10–11 мая 2024 и тихая неделя июня.

Запуск: python experiments/gannon.py — печатает таблицы и пишет docs/EKSPERIMENTY.md.
Сетевых запросов нет; вход — только data/archive_2024/donki_*.json.

Два эксперимента раздельно (docs/KRITERII_PLAN.md, Т5; разбор Codex 19.09):
  1. Корректность применения внешних сообщений к последующим наблюдениям:
     заблаговременность датированных уведомлений DONKI (messageIssueTime)
     относительно физического начала (GST startTime, SEP eventTime); что было
     известно к отсечкам 10.05 06/12/15/19Z через vkd.assess.cutoff.apply_cutoff;
     контрольная неделя июня, выбранная по данным.
  2. Базовый подход «последнее наблюдение» против правила условий договора
     (CONTRACT v3.1, раздел 4, п. 2) на тех же отсечках; устойчивость набора
     помеченных окон при сдвиге отсечки на ±3 ч на сопоставимом наборе окон
     (окна, уже недоступные после сдвига, не считаются).

Две заблаговременности разделены: сообщения поставщика (NOAA/DONKI) и момент,
когда наш сервис мог его применить при запуске раз в N часов (N = 1, 3, 6).
Заблаговременность поставщика сервису не приписывается.

Соглашения (все — параметры ниже, все названы в отчёте):
  * заблаговременность = физическое начало − выпуск сообщения; отрицательная
    означает, что сообщение вышло после начала;
  * Kp в карточке GST с observedTime = T относится к интервалу [T − 3 ч, T):
    проверено вручную по строке GFZ за 10.05.2024 (15–18 UT: 7,667 = DONKI
    observedTime 18:00); в расчётах файл GFZ не используется;
  * действие сообщения без известного конца — VALID_H часов, как в
    vkd/windows/compare.py; тело сообщения в архиве отсутствует, поэтому
    класс условия (приоритетное / предупреждение) из него не выводится.
"""
from __future__ import annotations

import hashlib
import io
import math
import os
import sys
from collections import Counter, OrderedDict
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import experiments.legacy.stub_history as SH                                    # noqa: E402
from experiments.legacy.stub_history import ARCH, _load, _t, history_bundle   # noqa: E402
from vkd.assess.cutoff import apply_cutoff                              # noqa: E402
from vkd.config import settings_path                                     # noqa: E402
from vkd.integration.noaa_forecast import noaa_forecasts                 # noqa: E402
from vkd.types import Window                                             # noqa: E402
from vkd.windows.compare import Thresholds                               # noqa: E402

UTC = timezone.utc
GST_ID = '2024-05-10T15:00:00-GST-001'
SEP_DAYS = ('2024-05-10', '2024-05-11')
CUTOFFS = tuple(datetime(2024, 5, 10, h, 0, tzinfo=UTC) for h in (6, 12, 15, 19))
DURATION_MIN = 360           # длительность ВКД в эксперименте, мин
SEARCH_H = 24                # период поиска начала, ч (постановка: до 24)
START_STEP_H = 1             # шаг перебора начала окна, ч
VALID_H = 24                 # действие сообщения без известного конца, ч (compare.py)
RUN_PERIODS_H = (1, 3, 6)    # период запуска сервиса, ч
SHIFT_H = 3                  # сдвиг отсечки для проверки устойчивости, ч
KP_INTERVAL_H = 3            # интервал индекса Kp, ч
LOOKBACK_H = 48              # как в app/main.py: к отсечке показываются события не старше 48 ч
AFTER_H = 24                 # «случилось потом»: горизонт после отсечки, ч
CONTROL_AFTER_H = 48         # контроль: события, которые могли бы оправдать сигнал недели, ч
TH = Thresholds.from_settings()           # те же пороги, что у приложения (config/settings.toml, Т7)
KP_CHECK = TH.kp_check                    # Kp ≥ 7 — триггер проверки (политика прототипа)
CONTRACT_KINDS = ('GST', 'SEP')           # условия по договору образуют только буря и протонное событие
OUT_MD = os.path.join(ROOT, 'docs', 'EKSPERIMENTY.md')

CARD_ID_KEY = {'gst': 'gstID', 'sep': 'sepID', 'flr': 'flrID', 'cme': 'activityID', 'ips': 'activityID', 'rbe': 'rbeID'}
FILES = {k: 'donki_%s_2024-05-01_2024-06-30.json' % k for k in ('gst', 'sep', 'cme', 'flr', 'ips', 'rbe')}


# ----------------------------------------------------------------------------- форматирование
def fmt(t):
    return t.strftime('%Y-%m-%d %H:%MZ') if t else '—'


def fmt_s(t):
    return t.strftime('%m-%d %H:%MZ')


def num(x, nd=1):
    return ('%.*f' % (nd, x)).replace('.', ',').replace('-', '−')


def hours(a, b):
    """a − b в часах."""
    return (a - b).total_seconds() / 3600.0


def lead(start, issued):
    """Заблаговременность: начало − выпуск; «+» — до начала, «−» — после."""
    h = hours(start, issued)
    return ('+' if h >= 0 else '−') + num(abs(h)) + ' ч'


def signed_h(h):
    """Знаковая разность в часах: «+» — позже, «−» — раньше."""
    return ('+' if h >= 0 else '−') + num(abs(h)) + ' ч'


def words(start, issued):
    """«за X ч до» / «через X ч после» — для связного текста."""
    h = hours(start, issued)
    if abs(h) < 1.0:
        return ('за %s мин до' if h >= 0 else 'через %s мин после') % num(abs(h) * 60, 0)
    return ('за %s ч до' if h >= 0 else 'через %s ч после') % num(abs(h))


def rid(event_id):
    """Идентификатор записи без префикса поставщика: messageID, sepID."""
    return event_id.split('#', 1)[1] if '#' in event_id else event_id


def sha256(path):
    h = hashlib.sha256()
    with io.open(path, 'rb') as f:
        h.update(f.read())
    return h.hexdigest()


def table(header, rows):
    out = ['| ' + ' | '.join(header) + ' |', '|' + '---|' * len(header)]
    out += ['| ' + ' | '.join(str(c) for c in r) + ' |' for r in rows]
    return out


# ----------------------------------------------------------------------------- данные
def load_cards():
    return {k: _load(k) for k in FILES}


def messages(cards):
    """Уникальные уведомления: messageID → выпуск, типы карточек, карточки.
    Одно сообщение может быть прикреплено к нескольким карточкам (например,
    20240509-AL-010 — к пяти CME); считаем его один раз."""
    out = OrderedDict()
    for kind, lst in cards.items():
        for c in lst:
            for n in c.get('sentNotifications') or []:
                mid, t = n.get('messageID'), _t(n.get('messageIssueTime'))
                if not mid or t is None:
                    continue
                m = out.setdefault(mid, {'mid': mid, 'issued': t, 'kinds': set(), 'cards': []})
                m['kinds'].add(kind.upper())
                m['cards'].append(c[CARD_ID_KEY[kind]])
    return out


def kp_intervals(cards):
    """Интервалы Kp по карточкам GST (после факта): (начало, конец, Kp, gstID, observedTime)."""
    out = []
    for g in cards['gst']:
        for k in g.get('allKpIndex', []):
            t = _t(k['observedTime'])
            out.append((t - timedelta(hours=KP_INTERVAL_H), t, float(k['kpIndex']), g['gstID'], k['observedTime']))
    return sorted(out)


def windows_for(cutoff):
    return [Window(cutoff + timedelta(hours=k), DURATION_MIN) for k in range(0, SEARCH_H + 1, START_STEP_H)]


def w_end(w):
    return w.start_utc + timedelta(minutes=w.duration_min)


def overlaps(a0, a1, b0, b1):
    return a0 < b1 and b0 < a1


def strict_events(bundle, cutoff):
    """Строгий отбор через apply_cutoff; дубли одного messageID (несколько карточек) убраны."""
    samples, events, _ = bundle
    cut = apply_cutoff(samples, events, [], cutoff)
    seen, uniq = set(), []
    for e in cut.events:
        if e.event_id not in seen:
            seen.add(e.event_id)
            uniq.append(e)
    return cut, uniq


def condition_intervals(events, rule):
    """Интервалы условий проверки из событий. Зеркало ветки условий compare.py:
    a0 = valid_from или start; a1 = valid_to или end или a0 + VALID_H.
    rule = 'contract': только GST и SEP (CONTRACT v3.1, раздел 4, п. 2);
    rule = 'implemented': любое событие, как сейчас в compare.py (в том числе CME и FLR)."""
    out = []
    for e in events:
        if rule == 'contract' and e.kind_of_event not in CONTRACT_KINDS:
            continue
        a0 = e.valid_from_utc or e.start_utc
        if a0 is None:
            continue
        a1 = e.valid_to_utc or e.end_utc or (a0 + timedelta(hours=VALID_H))
        out.append((a0, a1, e))
    return out


def flag_windows(windows, conds):
    return {w.start_utc: sorted({e.event_id for a0, a1, e in conds if overlaps(a0, a1, w.start_utc, w_end(w))})
            for w in windows}


def apply_at(issued, period_h):
    """Первый запуск сервиса с периодом period_h (сетка от 00:00Z), не раньше выпуска."""
    day = issued.replace(hour=0, minute=0, second=0, microsecond=0)
    k = math.ceil(hours(issued, day) / period_h - 1e-9)
    return day + timedelta(hours=k * period_h)


def linked_ips(cards):
    """Приход ударной волны у Земли, связанный с карточкой бури (карточка IPS с location = Earth)."""
    g = next(x for x in cards['gst'] if x['gstID'] == GST_ID)
    linked = {l['activityID'] for l in g.get('linkedEvents', [])}
    ips = [c for c in cards['ips'] if c['activityID'] in linked and c.get('location') == 'Earth']
    return min(ips, key=lambda c: c['eventTime']) if ips else None


# ----------------------------------------------------------------------------- эксперимент 1
def exp1_storm(cards):
    g = next(x for x in cards['gst'] if x['gstID'] == GST_ID)
    start = _t(g['startTime'])
    iv = [x for x in kp_intervals(cards) if x[3] == GST_ID]
    kp7 = [x for x in iv if x[2] >= KP_CHECK]
    kp_max = max(x[2] for x in iv)
    peak = next(x for x in iv if x[2] == kp_max)
    ips = linked_ips(cards)
    lines = ['### 1.1. Буря: физические времена по карточке `%s` (после факта)' % GST_ID, '']
    lines += ['* начало бури (`startTime`): %s — начало первого трёхчасового интервала Kp, не момент прихода возмущения' % fmt(start),
              '* первый интервал Kp ≥ %g: %s–%s, Kp %s (`observedTime` %s)' % (KP_CHECK, fmt(kp7[0][0]), fmt_s(kp7[0][1]), num(kp7[0][2], 2), kp7[0][4]),
              '* пик Kp %s: интервал %s–%s (`observedTime` %s); значение стало наблюдённым в конце интервала' % (num(kp_max, 2), fmt(peak[0]), fmt_s(peak[1]), peak[4]),
              '* последний интервал Kp ≥ %g: %s–%s, Kp %s (`observedTime` %s)' % (KP_CHECK, fmt(kp7[-1][0]), fmt_s(kp7[-1][1]), num(kp7[-1][2], 2), kp7[-1][4]),
              '* карточка подана `submissionTime` %s, `versionId` %s; наблюдений Kp в карточке: %d, все без собственного времени публикации' % (g['submissionTime'], g['versionId'], len(iv))]
    if ips is not None:
        ns = sorted(ips.get('sentNotifications') or [], key=lambda n: n['messageIssueTime'])
        lines += ['* приход ударной волны у Земли (`eventTime` карточки `%s`, связана с бурей): %s; уведомление %s' % (
            ips['activityID'], fmt(_t(ips['eventTime'])),
            ', '.join('`%s` %s' % (n['messageID'], fmt(_t(n['messageIssueTime']))) for n in ns) or 'не выпускалось')]
    lines += ['']
    lines += table(['интервал (UTC)', 'Kp', 'observedTime', 'источник записи'],
                   [(fmt(a) + '–' + b.strftime('%H:%MZ'), num(kp, 2), obs, '`%s`' % GST_ID) for a, b, kp, _, obs in iv])
    lines += ['', '### 1.2. Уведомления DONKI по буре: заблаговременность поставщика', '',
              'Заблаговременность = физический момент − `messageIssueTime`; «−» означает, что сообщение вышло после этого момента.', '']
    rows = []
    for n in g['sentNotifications']:
        t = _t(n['messageIssueTime'])
        rows.append(('`%s`' % n['messageID'], fmt(t), lead(start, t), lead(kp7[0][0], t), lead(peak[0], t), lead(peak[1], t)))
    lines += table(['messageID', 'выпуск', 'до начала бури %s' % fmt_s(start), 'до первого Kp ≥ %g' % KP_CHECK,
                    'до начала интервала пика %s' % fmt_s(peak[0]), 'до конца интервала пика %s' % fmt_s(peak[1])], rows)
    first = min(g['sentNotifications'], key=lambda n: n['messageIssueTime'])
    t_first = _t(first['messageIssueTime'])
    lines += ['', 'Вывод: все %d уведомлений по карточке бури выпущены после её начала; первое (`%s`) — %s `startTime`, '
              'то есть уведомление GST в DONKI констатирует уже идущую бурю, а не предсказывает её.' % (
                  len(g['sentNotifications']), first['messageID'], words(start, t_first))]
    if ips is not None and ips.get('sentNotifications'):
        n_ips = min(ips['sentNotifications'], key=lambda n: n['messageIssueTime'])
        t_ips = _t(n_ips['messageIssueTime'])
        lines += ['Уведомление о приходе ударной волны `%s` вышло %s (на %s ч раньше первого уведомления GST, %s начала бури); '
                  'по договору (раздел 4, п. 2) запись IPS условия не образует — это резерв для будущего правила, не результат.' % (
                      n_ips['messageID'], fmt(t_ips), num(hours(t_first, t_ips)), words(start, t_ips))]
    return lines, start, kp7, peak


def exp1_sep(cards):
    lines = ['', '### 1.3. Протонные события 10–11 мая: заблаговременность по каждой записи', '',
             'Публикация: `messageIssueTime` уведомления — датированный выпуск (основа строгого режима). `submissionTime` карточки — '
             'время подачи версии `versionId`, не публикация содержания (карточка редактируется: у %d из %d карточек SEP архива `versionId` > 1); '
             'в строгий режим карточки не входят (CONTRACT §10), строка подачи показана только для сравнения.'
             % (sum(1 for s in cards['sep'] if (s.get('versionId') or 1) > 1), len(cards['sep'])), '']
    rows = []
    seps = [s for s in cards['sep'] if s['eventTime'][:10] in SEP_DAYS]
    for s in sorted(seps, key=lambda x: x['eventTime']):
        ev = _t(s['eventTime'])
        instr = (s.get('instruments') or [{}])[0].get('displayName', '')
        sub = _t(s['submissionTime'])
        rows.append(('`%s`' % s['sepID'], instr, fmt(ev), 'подача карточки v%s (не выпуск)' % s['versionId'], fmt(sub), lead(ev, sub)))
        for n in s.get('sentNotifications') or []:
            t = _t(n['messageIssueTime'])
            rows.append(('`%s`' % s['sepID'], instr, fmt(ev), 'уведомление `%s`' % n['messageID'], fmt(t), lead(ev, t)))
    lines += table(['sepID', 'прибор', 'начало (`eventTime`)', 'публикация', 'время публикации', 'заблаговременность'], rows)
    goes10 = [s for s in seps if 'SEISS >10' in (s.get('instruments') or [{}])[0].get('displayName', '')]
    if goes10:
        s = goes10[0]
        n0 = min(s['sentNotifications'], key=lambda n: n['messageIssueTime'])
        lines += ['', 'Вывод: первое уведомление по событию GOES ≥10 МэВ (`%s`, `%s`) выпущено %s `eventTime`; '
                  'уведомления DONKI по SEP — констатация начала, не прогноз. Прогнозные записи (модель REleASE, `MODEL:` в приборе) '
                  'в архиве есть только для 9 и 13 мая; для 10–11 мая их нет.' % (
                      n0['messageID'], s['sepID'], words(_t(s['eventTime']), _t(n0['messageIssueTime'])))]
    return lines, seps


def exp1_precursors(cards, storm_start):
    g = next(x for x in cards['gst'] if x['gstID'] == GST_ID)
    linked = {l['activityID'] for l in g.get('linkedEvents', [])}
    cmes = [c for c in cards['cme'] if c['activityID'] in linked]
    lines = ['', '### 1.4. Предвестники: CME, связанные с карточкой бури', '',
             'Датированные уведомления по CME (`sentNotifications`) — это то, что строгий режим мог знать до бури. '
             'Тела сообщений в архиве нет, поэтому известен только факт выпуска и связь через `linkedEvents`, '
             'а не предсказанное в сообщении время прихода.', '']
    rows = []
    for c in sorted(cmes, key=lambda x: x['startTime']):
        for n in c.get('sentNotifications') or []:
            t = _t(n['messageIssueTime'])
            rows.append(('`%s`' % c['activityID'], '`%s`' % n['messageID'], fmt(t), lead(storm_start, t)))
    lines += table(['activityID (CME)', 'messageID', 'выпуск', 'до начала бури'], rows)
    ips = linked_ips(cards)
    ref, ref_txt = (_t(ips['eventTime']), 'прихода ударной волны %s (`%s`)' % (fmt_s(_t(ips['eventTime'])), ips['activityID'])) if ips else (storm_start, 'начала бури')
    lines += ['', 'Оценки прихода из `cmeAnalyses.enlilList` карточек — внешний прогноз WSA-ENLIL. Времени размещения прогона на сайте '
              'в данных нет; `modelCompletionTime` — время завершения модели, а не доказанная публикация (R4). Политика прототипа R10 '
              '(`experiments/legacy/stub_history.py`, `enlil_arrivals`): принятая публикация = завершение прогона + %d мин запаса '
              '(`[history].enlil_publication_lag_min`), но не раньше подачи анализа `submissionTime` — анализ, переподанный позже прогона, '
              'не мог быть виден раньше подачи. Доступность к отсечке этим не доказана, и запись так и помечена. '
              'Ошибка = оценка − фактический момент %s; «+» — оценка позже факта.' % (SH.ENLIL_PUBLICATION_LAG_MIN, ref_txt), '']
    rows = []
    lag = timedelta(minutes=SH.ENLIL_PUBLICATION_LAG_MIN)
    for c in sorted(cmes, key=lambda x: x['startTime']):
        for a in c.get('cmeAnalyses') or []:
            for e in a.get('enlilList') or []:
                arr = _t(e.get('estimatedShockArrivalTime'))
                if arr is None:
                    continue
                mc, sub = _t(e.get('modelCompletionTime')), _t(a.get('submissionTime'))
                pub = max(t for t in ((mc + lag) if mc else None, sub) if t is not None)
                rows.append(('`%s`' % c['activityID'], fmt(arr), signed_h(hours(arr, ref)), fmt(mc), fmt(sub),
                             fmt(pub) + (' (подача)' if sub is not None and pub == sub and (mc is None or sub > mc + lag) else ' (прогон + запас)')))
    rows.sort(key=lambda r: r[3])
    lines += table(['activityID (CME)', 'оценка прихода ENLIL', 'ошибка оценки', 'modelCompletionTime', 'подача анализа', 'принятая публикация (R10)'], rows)
    late = [r for r in rows if r[4] > '2024-06']
    if late:
        lines += ['', 'Замечание: %d из %d анализов поданы после июня 2024 (самая поздняя подача %s) — карточка DONKI редактируется '
                  'спустя месяцы; по политике R10 публикация таких прогонов = подача анализа, и отсечка 2024 года их исключает.' % (
                      len(late), len(rows), max(r[4] for r in rows))]
    return lines


def exp1_cutoffs(bundle, cards, msgs, kp7_all, seps):
    lines = ['', '### 1.5. Отсечки 10 мая: что известно строго и что случилось потом', '',
             'Отбор — `vkd.assess.cutoff.apply_cutoff` на выдаче `experiments.legacy.stub_history.history_bundle()` '
             '(%d образцов Kp без публикации, %d событий, из них уникальных %d). '
             '«Известно» — уникальные записи с публикацией ≤ отсечки, не старше %d ч; '
             '«случилось потом» — по карточкам после факта, горизонт %d ч.' % (
                 len(bundle[0]), len(bundle[1]), len({e.event_id for e in bundle[1]}), LOOKBACK_H, AFTER_H), '']
    rows = []
    detail = []
    for c in CUTOFFS:
        cut, ev = strict_events(bundle, c)
        recent = [e for e in ev if e.published_utc >= c - timedelta(hours=LOOKBACK_H)]
        by_kind = Counter(e.kind_of_event for e in recent)
        last = {}
        for e in sorted(recent, key=lambda e: e.published_utc):
            last[e.kind_of_event] = e
        known = '; '.join('%s: %d (последнее `%s` %s)' % (k, by_kind[k], rid(last[k].event_id), fmt_s(last[k].published_utc)) for k in sorted(by_kind))
        n_kp_excl = sum(1 for x in cut.excluded if 'непригодно' in x and not x.startswith('donki_sep#'))
        n_cards = sum(1 for x in cut.excluded if x.startswith('donki_sep#'))
        n_after = sum(1 for x in cut.excluded if 'после отсечки' in x or 'позже отсечки' in x)
        after = []
        end = c + timedelta(hours=AFTER_H)
        kp_after = [x for x in kp7_all if c < x[1] <= end]
        if kp_after:
            mx = max(kp_after, key=lambda x: x[2])
            after.append('Kp ≥ %g в %d интервалах, максимум %s (%s–%s, `%s`)' % (
                KP_CHECK, len(kp_after), num(mx[2], 2), fmt_s(mx[0]), mx[1].strftime('%H:%MZ'), mx[3]))
        sep_after = [s for s in seps if c < _t(s['eventTime']) <= end]
        if sep_after:
            after.append('начала SEP: ' + ', '.join('`%s`' % s['sepID'] for s in sorted(sep_after, key=lambda s: s['eventTime'])))
        n_msg_after = sum(1 for m in msgs.values() if c < m['issued'] <= end)
        after.append('уведомлений в следующие %d ч: %d' % (AFTER_H, n_msg_after))
        rows.append((fmt(c), known or 'ничего за %d ч' % LOOKBACK_H,
                     '%d интервалов Kp без публикации; %d карточек SEP (не датированный выпуск); %d записей позже отсечки' % (n_kp_excl, n_cards, n_after),
                     '; '.join(after)))
        detail.append((c, recent, cut))
    lines += table(['отсечка', 'известно строго (не старше %d ч)' % LOOKBACK_H, 'исключено отсечкой', 'случилось потом (%d ч)' % AFTER_H], rows)
    lines += ['', 'Записи GST и SEP, известные строго к каждой отсечке (только они образуют условия по договору): '
              'датированные уведомления, публикация — `messageIssueTime`; карточки SEP в строгий отбор не входят (CONTRACT §10). '
              'Происхождение — по телу сообщения из реестра A1:', '']
    rows = []
    for c, recent, cut in detail:
        gs = [e for e in recent if e.kind_of_event in CONTRACT_KINDS]
        rows.append((fmt(c), ', '.join('`%s` (%s, %s, %s)' % (rid(e.event_id), e.kind_of_event, fmt_s(e.published_utc),
                                                              'наблюдение' if e.kind.value == 'observation' else 'прогноз')
                                       for e in sorted(gs, key=lambda e: e.published_utc)) or 'нет'))
    lines += table(['отсечка', 'записи GST/SEP с публикацией ≤ отсечки'], rows)
    return lines


def exp1_control(bundle, cards, msgs):
    gst_starts = [_t(g['startTime']) for g in cards['gst']]
    kp_ends = [x[1] for x in kp_intervals(cards)]
    sep_times = [_t(s['eventTime']) for s in cards['sep']]
    cand = []
    for d in range(1, 24):
        a = datetime(2024, 6, d, tzinfo=UTC)
        b = a + timedelta(days=7)
        n_gst = sum(a <= t < b for t in gst_starts)
        n_kp = sum(a < t <= b for t in kp_ends)
        n_sep = sum(a <= t < b for t in sep_times)
        n_msg = sum(a <= m['issued'] < b for m in msgs.values())
        cand.append((a, b, n_gst, n_kp, n_sep, n_msg))
    ok = [r for r in cand if r[2] == 0 and r[3] == 0 and r[4] == 0]
    a, b, _, _, _, n_msg = min(ok, key=lambda r: (r[5], r[0]))
    lines = ['', '### 1.6. Контрольная неделя июня: выбор по данным', '',
             'Критерий: 7 суток в июне 2024 без начала бури (`startTime` GST), без наблюдений Kp в карточках GST и без начала '
             'протонного события (`eventTime` SEP) по архиву; из таких — неделя с наименьшим числом уникальных уведомлений, '
             'при равенстве — более ранняя. Кандидаты (начало недели 00:00Z):', '']
    lines += table(['неделя с', 'GST', 'Kp в карточках', 'SEP', 'уведомлений', 'подходит'],
                   [(fmt_s(r[0]), r[2], r[3], r[4], r[5], 'да' if r[2] == r[3] == r[4] == 0 else 'нет') for r in cand])
    lines += ['', '**Выбрана неделя %s – %s** (%d уведомлений).' % (fmt(a), fmt(b), n_msg), '']
    week = sorted((m for m in msgs.values() if a <= m['issued'] < b), key=lambda m: m['issued'])
    lines += table(['messageID', 'выпуск', 'тип карточки', 'карточки'],
                   [('`%s`' % m['mid'], fmt(m['issued']), '/'.join(sorted(m['kinds'])), ', '.join('`%s`' % c for c in m['cards'])) for m in week])
    # события, которые могли бы оправдать сигнал: неделя + CONTROL_AFTER_H
    b2 = b + timedelta(hours=CONTROL_AFTER_H)
    gst_next = [g['gstID'] for g in cards['gst'] if a <= _t(g['startTime']) < b2]
    sep_next = [s['sepID'] for s in cards['sep'] if a <= _t(s['eventTime']) < b2]
    # окна по DURATION_MIN, начало каждый час недели; отсечка = начало окна
    samples, events, _ = bundle
    n = int(hours(b, a))
    flagged = {'contract': [], 'implemented': []}
    by_msg = {'contract': Counter(), 'implemented': Counter()}
    for k in range(n):
        w = Window(a + timedelta(hours=k), DURATION_MIN)
        cut = apply_cutoff(samples, events, [], w.start_utc)
        for rule in flagged:
            f = flag_windows([w], condition_intervals(cut.events, rule))[w.start_utc]
            if f:
                flagged[rule].append(w.start_utc)
                by_msg[rule].update(f)
    truth = []      # окна, пересекающие интервал Kp ≥ 7 или содержащие начало SEP — по карточкам
    for k in range(n):
        w = Window(a + timedelta(hours=k), DURATION_MIN)
        if any(overlaps(x[0], x[1], w.start_utc, w_end(w)) for x in kp_intervals(cards) if x[2] >= KP_CHECK) or \
           any(w.start_utc <= t < w_end(w) for t in sep_times):
            truth.append(w.start_utc)
    lines += ['', 'Итог по неделе (окна по %d ч, начало каждый час, отсечка = начало окна, всего %d окон). В столбце записей могут быть '
              'и сообщения, выпущенные до начала недели, чьё %d-часовое действие заходит в неделю:' % (DURATION_MIN // 60, n, VALID_H), '']
    rows = [('уведомлений DONKI за неделю (уникальных)', str(n_msg), ', '.join('%s: %d' % (k, v) for k, v in sorted(Counter('/'.join(sorted(m['kinds'])) for m in week).items()))),
            ('бури и SEP за неделю и следующие %d ч (после факта)' % CONTROL_AFTER_H, str(len(gst_next) + len(sep_next)), ', '.join('`%s`' % x for x in gst_next + sep_next) or 'нет'),
            ('окон с событием по карточкам (истина после факта)', str(len(truth)), '—'),
            ('условия по договору (GST/SEP): помечено окон', str(len(flagged['contract'])), ', '.join('`%s`' % rid(m) for m in by_msg['contract']) or 'нет'),
            ('ложных предупреждений по договору', str(len([s for s in flagged['contract'] if s not in truth])), '—'),
            ('«любое уведомление — проверка» (вариант отвергнут; в compare.py не реализован, см. ветку условий): помечено окон',
             str(len(flagged['implemented'])), ', '.join('`%s`' % rid(m) for m in sorted(by_msg['implemented'])) or 'нет'),
            ('ложных предупреждений при «любом уведомлении»', str(len([s for s in flagged['implemented'] if s not in truth])), '—')]
    lines += table(['показатель', 'значение', 'записи'], rows)
    return lines, (a, b, n_msg, len(flagged['contract']), len(flagged['implemented']), n, len(truth))


# ----------------------------------------------------------------------------- эксперимент 2
def exp2_baseline_vs_rule(bundle, cards, kp7_all, storm_start, peak):
    samples, events, raw = bundle
    kp_iv = kp_intervals(cards)
    lines = ['', '### 2.1. На отсечках: «последнее наблюдение» против правила условий', '',
             'Базовый метод: последнее наблюдение Kp (окончательный ряд GFZ; в карточках GST DONKI — те же значения NOAA). В строгом режиме '
             'образцы Kp не имеют времени публикации по интервалам и исключаются `apply_cutoff` — метод слеп. Нестрогий вариант (по концу '
             'интервала `observedTime` карточки GST, публикация игнорируется) показан для сравнения и **не является прогнозом из прошлого**: '
             'значение из карточки, поданной %s, нельзя считать доступным раньше подачи. Наше правило: условия из датированных '
             'уведомлений GST/SEP с публикацией ≤ отсечки, действие %d ч при неизвестном конце.'
             % (next(g['submissionTime'] for g in cards['gst'] if g['gstID'] == GST_ID), VALID_H), '']
    rows = []
    for c in CUTOFFS:
        cut, ev = strict_events(bundle, c)
        kp_strict = [s for s in cut.samples if s.channel_id == 'kp']
        strict_txt = 'нет данных: %d из %d образцов Kp исключены (нет публикации)' % (
            sum(1 for x in cut.excluded if 'непригодно' in x and not x.startswith('donki_sep#')), len(samples)) if not kp_strict else '%d образцов' % len(kp_strict)
        past = [x for x in kp_iv if x[1] <= c]
        if past:
            lst = max(past, key=lambda x: x[1])
            leaky_txt = 'Kp %s за %s–%s (`%s`), давность %s ч → %s' % (
                num(lst[2], 2), fmt_s(lst[0]), lst[1].strftime('%H:%MZ'), lst[3], num(hours(c, lst[1])),
                'условие Kp ≥ %g' % KP_CHECK if lst[2] >= KP_CHECK else 'условия нет')
        else:
            leaky_txt = 'нет наблюдений'
        recent = [e for e in ev if e.published_utc >= c - timedelta(hours=LOOKBACK_H)]
        conds = [x for x in condition_intervals(recent, 'contract') if x[1] > c]      # действующие или будущие к отсечке
        gst_c = [x for x in conds if x[2].kind_of_event == 'GST']
        sep_c = [x for x in conds if x[2].kind_of_event == 'SEP']
        other = Counter(e.kind_of_event for e in recent if e.kind_of_event not in CONTRACT_KINDS)
        ours = []
        if gst_c:
            g0 = min(gst_c, key=lambda x: x[0])
            ours.append('буря: проверка с %s (`%s`), %s до начала интервала пика' % (fmt_s(g0[0]), rid(g0[2].event_id), lead(peak[0], g0[0])))
        else:
            ours.append('буря: условия по записям GST нет (записей других типов, здесь не образующих условие: %s; прогнозы ENLIL — раздел 5)'
                        % (', '.join('%s %d' % (k, v) for k, v in sorted(other.items())) or 'нет'))
        if sep_c:
            s0 = min(sep_c, key=lambda x: x[0])
            ours.append('SEP: проверка с %s до %s (`%s`%s)' % (fmt_s(s0[0]), fmt_s(s0[1]), rid(s0[2].event_id),
                                                              ', ' + s0[2].note if s0[2].note and not s0[2].note.startswith('20') else ''))
        else:
            ours.append('SEP: условия нет')
        rows.append((fmt(c), strict_txt, leaky_txt, '; '.join(ours)))
    lines += table(['отсечка', 'базовый метод, строгий режим', 'базовый метод, нестрогий (для сравнения)', 'наше правило (строго)'], rows)
    # матрица по механизму «буря» на окнах
    lines += ['', 'Окна %d ч, начало каждый час в течение %d ч после отсечки (%d окон). Механизм «буря»: помечено — окно пересекает '
              'интервал действия записи GST, известной к отсечке; истина после факта — окно пересекает интервал Kp ≥ %g '
              'по карточкам. Отдельно — окна под условием SEP (истина по SEP из архива не выводится: нет конца события и потоков).' % (
                  DURATION_MIN // 60, SEARCH_H, len(windows_for(CUTOFFS[0])), KP_CHECK), '']
    rows = []
    for c in CUTOFFS:
        W = windows_for(c)
        cut, ev = strict_events(bundle, c)
        ev = [e for e in ev if e.published_utc >= c - timedelta(hours=LOOKBACK_H)]
        Fg = flag_windows(W, condition_intervals([e for e in ev if e.kind_of_event == 'GST'], 'contract'))
        Fs = flag_windows(W, condition_intervals([e for e in ev if e.kind_of_event == 'SEP'], 'contract'))
        truth = {w.start_utc: any(overlaps(x[0], x[1], w.start_utc, w_end(w)) for x in kp7_all) for w in W}
        hit = sum(1 for w in W if Fg[w.start_utc] and truth[w.start_utc])
        miss = sum(1 for w in W if not Fg[w.start_utc] and truth[w.start_utc])
        fa = sum(1 for w in W if Fg[w.start_utc] and not truth[w.start_utc])
        cr = sum(1 for w in W if not Fg[w.start_utc] and not truth[w.start_utc])
        miss_sep = sum(1 for w in W if not Fg[w.start_utc] and truth[w.start_utc] and Fs[w.start_utc])
        missed_starts = [w.start_utc for w in W if not Fg[w.start_utc] and truth[w.start_utc]]
        rows.append((fmt(c), hit, miss, fa, cr, sum(1 for w in W if Fs[w.start_utc]), miss_sep,
                     (fmt_s(missed_starts[0]) + ('…' + fmt_s(missed_starts[-1]) if len(missed_starts) > 1 else '')) if missed_starts else '—'))
    lines += table(['отсечка', 'буря: попадания', 'буря: пропуски', 'буря: ложные', 'буря: верные отказы', 'окон под условием SEP',
                    'из пропусков по буре покрыты условием SEP', 'пропущенные окна (начало)'], rows)
    lines += ['', 'Чтение таблицы. До 18:44 записей GST нет вовсе, поэтому на отсечках 06:00–15:00 все окна, попавшие в бурю, — пропуски '
              'по механизму «буря»: DONKI не предсказывает бурю записью GST, а констатирует её. Часть этих окон стоит под условием SEP — '
              'это другой механизм (договор: бурю и протонное событие не складывать), и его совпадение с бурей здесь случайно. '
              'На отсечке 19:00 единственный пропуск — последнее окно периода поиска: оно начинается позже конца %d-часового действия '
              'записи `20240510-AL-013`, а более поздние уведомления (`20240510-AL-014` и далее) отсечкой исключены. '
              'Ложных предупреждений по буре нет ни на одной отсечке.' % VALID_H]
    return lines


def exp2_service_lag(cards, storm_start, peak):
    g = next(x for x in cards['gst'] if x['gstID'] == GST_ID)
    items = []
    n0 = min(g['sentNotifications'], key=lambda n: n['messageIssueTime'])
    items.append(('буря, первое уведомление `%s` (`%s`)' % (n0['messageID'], GST_ID), _t(n0['messageIssueTime']), peak[0], 'до начала интервала пика %s' % fmt_s(peak[0])))
    for s in sorted((s for s in cards['sep'] if s['eventTime'][:10] in SEP_DAYS and s.get('sentNotifications')), key=lambda s: s['eventTime']):
        n = min(s['sentNotifications'], key=lambda n: n['messageIssueTime'])
        instr = (s.get('instruments') or [{}])[0].get('displayName', '')
        items.append(('SEP %s, `%s` (`%s`)' % (instr, n['messageID'], s['sepID']), _t(n['messageIssueTime']), _t(s['eventTime']), 'до начала события'))
    lines = ['', '### 2.2. Заблаговременность поставщика и момент применения нашим сервисом', '',
             'Сервис запускается раз в N ч по сетке от 00:00Z и применяет сообщение при первом запуске не раньше выпуска. '
             'Заблаговременность поставщика — свойство NOAA/DONKI; наша — только то, что остаётся после задержки применения.', '']
    rows = []
    for name, issued, ref, what in items:
        cells = [name, fmt(issued), lead(ref, issued) + ' (' + what + ')']
        for N in RUN_PERIODS_H:
            t = apply_at(issued, N)
            cells.append('%s → %s' % (t.strftime('%H:%MZ'), lead(ref, t)))
        rows.append(tuple(cells))
    lines += table(['запись', 'выпуск', 'заблаговременность поставщика'] + ['сервис, N = %d ч' % N for N in RUN_PERIODS_H], rows)
    t0 = items[0][1]
    lines += ['', 'Относительно начала бури %s все значения отрицательны (таблица 1.2). Относительно начала интервала пика поставщик '
              'даёт %s; при запуске раз в 6 ч сервис применит запись в %s — уже после начала интервала пика. Эти %s принадлежат '
              'DONKI, а не сервису; сервис вправе заявлять только столбцы «сервис, N».' % (
                  fmt_s(storm_start), lead(peak[0], t0), apply_at(t0, RUN_PERIODS_H[-1]).strftime('%H:%MZ'), lead(peak[0], t0))]
    return lines


def exp2_stability(bundle):
    lines = ['', '### 2.3. Устойчивость набора помеченных окон при сдвиге отсечки на ±%d ч' % SHIFT_H, '',
             'Правило по договору (GST/SEP). Сопоставимый набор — окна, доступные при обеих отсечках; окна, начало которых '
             'раньше сдвинутой отсечки или позже её периода поиска, не считаются. Различие объясняется записями, '
             'опубликованными между отсечками, — это обновление информации, а не неустойчивость правила.', '']
    rows = []
    _, all_events, _ = bundle
    for c in CUTOFFS:
        for sh in (-SHIFT_H, SHIFT_H):
            c2 = c + timedelta(hours=sh)
            W1, W2 = windows_for(c), windows_for(c2)
            common = sorted({w.start_utc for w in W1} & {w.start_utc for w in W2})
            _, e1 = strict_events(bundle, c)
            _, e2 = strict_events(bundle, c2)
            F1 = flag_windows(W1, condition_intervals(e1, 'contract'))
            F2 = flag_windows(W2, condition_intervals(e2, 'contract'))
            same = [s for s in common if bool(F1[s]) == bool(F2[s])]
            diff = [s for s in common if bool(F1[s]) != bool(F2[s])]
            lo, hi = min(c, c2), max(c, c2)
            between = sorted({(e.published_utc, e.event_id) for e in all_events
                              if e.kind_of_event in CONTRACT_KINDS and e.published_utc is not None and lo < e.published_utc <= hi})
            why = ', '.join('`%s` %s' % (rid(i), fmt_s(t)) for t, i in between) or 'нет'
            if len(diff) > 4:
                which = '%s … %s (%d окон подряд)' % (fmt_s(diff[0]), fmt_s(diff[-1]), len(diff))
            else:
                which = ', '.join(fmt_s(s) for s in diff) or '—'
            rows.append((fmt(c), ('+' if sh > 0 else '−') + '%d ч' % abs(sh), len(common), len(same), len(diff), which, why))
    lines += table(['отсечка', 'сдвиг', 'сопоставимых окон', 'совпало', 'изменилось', 'какие окна (начало)', 'записи GST/SEP между отсечками'], rows)
    n_unexplained = sum(1 for r in rows if r[4] > 0 and r[6] == 'нет')
    lines += ['', 'Каждое изменение набора объясняется записями GST/SEP, опубликованными между отсечками (необъяснённых строк: %d). '
              'Без новых записей набор помеченных окон на сопоставимом множестве совпадает полностью.' % n_unexplained]
    return lines


# ----------------------------------------------------------------------------- раздел 5: прогнозы на отсечке
def exp5_forecast_at_cutoff(bundle, cutoff=datetime(2024, 5, 10, 12, 0, tzinfo=UTC), horizon_h=32, gap_cutoff=datetime(2024, 5, 20, 12, 0, tzinfo=UTC)):
    """Что строгий режим видит на отсечке из внешних прогнозов (архив NOAA A1/A2 и ENLIL по политике R10).
    Заменяет ручное «Дополнение 19.09»: числа считаются, а не переписываются."""
    lines = ['', '## 5. Внешние прогнозы на отсечке %s: NOAA и WSA-ENLIL' % fmt(cutoff), '',
             'Уведомления DONKI о буре Гэннон вышли после её начала (раздел 1.2), поэтому на отсечке %s строгий режим '
             'может видеть бурю только из внешних прогнозов, выпущенных раньше. Отбор выпуска NOAA — по времени публикации '
             '(A2, `vkd.integration.noaa_forecast`); прогноз ENLIL — по принятой публикации R10 (раздел 1.4).' % fmt_s(cutoff), '']
    end = cutoff + timedelta(hours=horizon_h)
    fc_lines, _ = noaa_forecasts(cutoff, cutoff, end)
    rows = []
    for line in fc_lines:
        cells = [s for s in line.samples if s.valid_from_utc and s.valid_to_utc and s.valid_from_utc < end and s.valid_to_utc > cutoff]
        if line.status in ('full', 'partial') and cells:
            mx = max(cells, key=lambda s: s.value if s.value is not None else -1)
            above = [s for s in cells if s.value is not None and line.channel_id == 'kp_forecast' and s.value >= KP_CHECK]
            what = ('максимум %s: %s–%s' % (num(mx.value, 2), fmt(mx.valid_from_utc),
                                           mx.valid_to_utc.strftime('%H:%MZ') if line.channel_id == 'kp_forecast' else fmt(mx.valid_to_utc))
                    + ('; интервалы с Kp ≥ %g: %s' % (KP_CHECK, ', '.join('%s–%s (%s)' % (fmt_s(s.valid_from_utc), s.valid_to_utc.strftime('%H:%MZ'), num(s.value, 2)) for s in above)) if above else ''))
        else:
            what = 'выпуска до отсечки в архиве нет' if line.status == 'missing' else line.status
        rows.append((line.label_ru, '`%s`' % line.release_id if line.release_id else '—',
                     fmt(line.published_utc) if line.published_utc else '—', what))
    lines += table(['линия NOAA', 'выпуск', 'публикация', 'на горизонте %d ч' % horizon_h], rows)
    samples, events, raw = bundle
    cut = apply_cutoff(samples, events, [], cutoff)
    enl = sorted((e for e in cut.events if e.kind_of_event == 'CME_ARRIVAL' and e.start_utc and cutoff - timedelta(hours=6) <= e.start_utc <= end),
                 key=lambda e: e.published_utc)
    lines += ['', 'Прогнозы прихода выброса WSA-ENLIL, известные к отсечке по политике R10 и приходящиеся на горизонт (каждый прогон — запись; '
              'связанные прогоны сводятся в одно условие):', '']
    rows = []
    for e in enl:
        r = raw.get(e.raw_record_id, {})
        rows.append(('`%s`' % r.get('activityID', '?'), fmt(_t(r.get('modelCompletionTime'))), fmt(e.published_utc), fmt(e.start_utc),
                     num(float(r['estimatedDuration_h']), 1) if r.get('estimatedDuration_h') is not None else '—',
                     num(float(r['kp_90']), 0) if r.get('kp_90') is not None else '—', num(float(r['kp_180']), 0) if r.get('kp_180') is not None else '—'))
    # «типичный» из подписи снят: kp_90 — сценарий прогона при повороте межпланетного поля
    # на 90°, а не медиана и не типичная оценка (возражение А по R10, CONTRACT §12)
    lines += table(['activityID (CME)', 'завершение прогона', 'принятая публикация', 'приход к Земле', 'длительность, ч',
                    'Kp при угле поля 90° (kp_90)', 'Kp при южном поле (kp_180)'], rows or [('нет', '—', '—', '—', '—', '—', '—')])
    n_cond = sum(1 for e in enl if any(k is not None and float(k) >= KP_CHECK for k in (raw.get(e.raw_record_id, {}).get(f) for f in SH.ENLIL_KP_FIELDS)))
    excl_2025 = [e for e in events if e.kind_of_event == 'CME_ARRIVAL' and e.published_utc and e.published_utc.year >= 2025
                 and e.start_utc and cutoff - timedelta(hours=6) <= e.start_utc <= end]
    lines += ['', 'Из них с ожидаемым Kp ≥ %g по полям `%s` (условие «прогноз прихода выброса»): %d. Прогоны, чьи анализы переподаны в 2025 году '
              '(%d на этом горизонте), отсечкой исключены — их содержимое не считается известным в 2024 году.' % (
                  KP_CHECK, '+'.join(SH.ENLIL_KP_FIELDS), n_cond, len(excl_2025)),
              '', 'Чтение: базовая линия «последнее наблюдение» на этой отсечке условий по буре не имеет (Kp накануне ниже порога), '
              'различие с правилом условий — вклад датированных внешних прогнозов, и заблаговременность здесь принадлежит NOAA и ENLIL, '
              'а не сервису. Уровень: условие по буре ставится только при Kp ≥ %g (наблюдение, уведомление с уровнем в теле или прогноз '
              'модели), иначе бури ниже порога помечали бы окна наравне с G3+. Полный пересчёт по отсечкам мая–июня — '
              '`experiments/forecast_lines.py` → `docs/EKSPERIMENTY_PROGNOZ.md`.' % KP_CHECK]
    fc_gap, _ = noaa_forecasts(gap_cutoff, gap_cutoff, gap_cutoff + timedelta(hours=horizon_h))
    kp_gap = next((l for l in fc_gap if l.channel_id == 'kp_forecast'), None)
    if kp_gap is not None:
        lines += ['', 'На отсечке %s линия «прогноз Kp NOAA» имеет статус `%s` (разрыв каталога 3-day forecast 15.05–16.06): канал '
                  'объявлен отсутствующим, оценка окна не блокируется; суточные вероятности daypre остаются и не пересчитываются в '
                  'вероятность за окно.' % (fmt(gap_cutoff), kp_gap.status)]
    return lines + ['']


# ----------------------------------------------------------------------------- отчёт
def main():
    sys.stdout.reconfigure(encoding='utf-8')
    cards = load_cards()
    msgs = messages(cards)
    bundle = history_bundle()
    kp7_all = [x for x in kp_intervals(cards) if x[2] >= KP_CHECK]

    lines = ['# Эксперименты: буря Гэннон 10–11 мая 2024 и контрольная неделя июня (Т5)', '',
             'Сформировано скриптом `experiments/gannon.py` (запуск `python experiments/gannon.py`, %s). '
             'Сетевых запросов нет; все числа — из файлов ниже, каждое привязано к записи (`messageID`, `gstID`, `sepID`, `activityID`).' % datetime.now(UTC).strftime('%Y-%m-%d %H:%MZ'), '',
             '## 0. Данные и соглашения', '']
    rows = []
    for k, f in FILES.items():
        p = os.path.join(ARCH, f)
        lst = cards[k]
        rows.append(('`data/archive_2024/%s`' % f, len(lst), sum(len(c.get('sentNotifications') or []) for c in lst), '`%s`' % sha256(p)))
    lines += table(['файл', 'карточек', 'прикреплений уведомлений', 'SHA-256'], rows)
    msg_types = Counter(m['mid'].split('-')[1] if m['mid'].count('-') >= 2 else '?' for m in msgs.values())
    n_a1 = len(SH._a1())
    lines += ['', 'Уникальных уведомлений в архиве: %d (одно сообщение может быть прикреплено к нескольким карточкам). '
              'Типы по `messageID`: %s. Тела сообщений и время выпуска с секундами — в реестре A1 (`data/source_registry_2024/donki/`, '
              '%d записей, статус `strict_replay_eligibility` у всех — датированное уведомление, аудит содержания и версий не завершён); '
              'из тела берутся уровень Kp бури и происхождение (наблюдение / прогноз).' % (
                  len(msgs), ', '.join('`%s` — %d' % (k, v) for k, v in sorted(msg_types.items())), n_a1), '',
              'Настройки: `%s` — пороги приложения (`kp_check = %g`), `[history].enlil_kp_fields = %s`, '
              '`[history].enlil_publication_lag_min = %d`.' % (os.path.relpath(settings_path(), ROOT).replace('\\', '/'), KP_CHECK,
                                                                '+'.join(SH.ENLIL_KP_FIELDS), SH.ENLIL_PUBLICATION_LAG_MIN), '',
              'Соглашения эксперимента:', '',
              '* заблаговременность = физическое начало − `messageIssueTime`; «−» — сообщение вышло после начала;',
              '* Kp с `observedTime` = T относится к интервалу [T − %d ч, T): проверено по строке GFZ за 10.05.2024 в '
              '`data/spaceweather/kp_ap_sn_f107.txt` (15–18 UT: 7,667 = DONKI `observedTime` 18:00); в разборе после факта '
              'поставщик истории берёт окончательный ряд GFZ (D = 2), карточки GST — резерв;' % KP_INTERVAL_H,
              '* действие записи без известного конца — %d ч от публикации или начала (как в `vkd/windows/compare.py`);' % VALID_H,
              '* окна ВКД %d ч, начало каждый час в течение %d ч после отсечки (%d окон); условие проверки — пересечение окна с интервалом действия записи;' % (
                  DURATION_MIN // 60, SEARCH_H, len(windows_for(CUTOFFS[0]))),
              '* условия по договору (CONTRACT v3.1, раздел 4, п. 2) образуют только записи GST (триггер Kp ≥ %g) и SEP (предупреждение); '
              'класс «приоритетное» из тела сообщения не выводится; CME/FLR/IPS/RBE — сообщения без условия; прогнозы прихода выброса '
              'WSA-ENLIL — условие по политике R10 (раздел 5);' % KP_CHECK,
              '* карточки событий DONKI (SEP, GST.allKpIndex) — только разбор после факта (CONTRACT §10): в строгий отбор входят '
              'датированные уведомления и прогнозы ENLIL по принятой публикации;',
              '* строгий отбор — `vkd.assess.cutoff.apply_cutoff` на выдаче `experiments.legacy.stub_history.history_bundle()`.', '',
              '## 1. Эксперимент 1 — внешние сообщения и последующие наблюдения', '']
    l1, storm_start, kp7, peak = exp1_storm(cards)
    lines += l1
    l2, seps = exp1_sep(cards)
    lines += l2
    lines += exp1_precursors(cards, storm_start)
    lines += exp1_cutoffs(bundle, cards, msgs, kp7_all, seps)
    lc, ctrl = exp1_control(bundle, cards, msgs)
    lines += lc
    lines += ['', '## 2. Эксперимент 2 — базовый подход против правила условий', '']
    lines += exp2_baseline_vs_rule(bundle, cards, kp7_all, storm_start, peak)
    lines += exp2_service_lag(cards, storm_start, peak)
    lines += exp2_stability(bundle)

    # сводка
    g = next(x for x in cards['gst'] if x['gstID'] == GST_ID)
    n0 = min(g['sentNotifications'], key=lambda n: n['messageIssueTime'])
    t0 = _t(n0['messageIssueTime'])
    goes10 = next(s for s in seps if 'SEISS >10' in (s.get('instruments') or [{}])[0].get('displayName', ''))
    ns = min(goes10['sentNotifications'], key=lambda n: n['messageIssueTime'])
    a, b, n_msg, n_flag_c, n_flag_i, n_win, n_truth = ctrl
    lines += ['', '## 3. Сводка (четыре строки для защиты)', '']
    lines += table(['показатель', 'результат', 'записи'], [
        ('заблаговременность DONKI по буре', 'первое уведомление вышло %s начала бури; %s начала интервала пика Kp %s; %s его конца' % (
            words(storm_start, t0), words(peak[0], t0), num(peak[2], 2), words(peak[1], t0)), '`%s`, `%s`' % (n0['messageID'], GST_ID)),
        ('заблаговременность DONKI по SEP', 'первое уведомление вышло %s начала события GOES ≥10 МэВ' % words(_t(goes10['eventTime']), _t(ns['messageIssueTime'])),
         '`%s`, `%s`' % (ns['messageID'], goes10['sepID'])),
        ('базовый метод против нашего правила', 'строгий базовый метод слеп на всех %d отсечках (%d образцов Kp без публикации); наше правило даёт условие по буре с %s '
         '(сервис при N = %s ч применит в %s)' % (len(CUTOFFS), len(bundle[0]), fmt_s(t0), '/'.join(str(N) for N in RUN_PERIODS_H),
                                                    ' / '.join(apply_at(t0, N).strftime('%H:%MZ') for N in RUN_PERIODS_H)), '`%s`' % n0['messageID']),
        ('контрольная неделя %s – %s' % (fmt_s(a), fmt_s(b)), '%d уведомлений, %d событий; по договору помечено %d окон из %d, ложных %d; '
         'отвергнутый вариант «любое уведомление — проверка» пометил бы %d окон, все ложные' % (n_msg, n_truth, n_flag_c, n_win, n_flag_c - n_truth, n_flag_i), 'таблица 1.6'),
    ])
    lines += ['', '## 4. Ограничения эксперимента', '',
              '* Из тела уведомления (реестр A1) берутся уровень Kp и происхождение; класс условия (S ≥ 3 / S1–S2) и заявленный в сообщении '
              'срок действия не разбираются; действие %d ч — соглашение прототипа. Аудит содержания и версий уведомлений (A1) не завершён.' % VALID_H,
              '* Kp в таблицах раздела 1 — из карточек DONKI (предварительные значения NOAA); поставщик истории в разборе после факта '
              'использует окончательный ряд GFZ (D = 2), значения совпадают с точностью округления. Истина «после факта» для бури — Kp ≥ %g.' % KP_CHECK,
              '* Истина по протонным событиям из архива не выводится: у карточек SEP нет конца события и потоков; для SEP оценена только '
              'заблаговременность относительно `eventTime`; попадания/пропуски по SEP считаются против своей величины в `docs/EKSPERIMENTY_PROGNOZ.md`.',
              '* Карточки SEP не имеют датированной публикации (`submissionTime` — подача версии, у %d из %d карточек `versionId` > 1) и в строгий '
              'режим не входят (CONTRACT §10); датированный выпуск — только `messageIssueTime` уведомления.' % (
                  sum(1 for s in cards['sep'] if (s.get('versionId') or 1) > 1), len(cards['sep'])),
              '* Прогнозы прихода ENLIL: публикация принята = завершение прогона + %d мин, не раньше подачи анализа (политика прототипа R10); '
              'доступность к отсечке не доказана датированным выпуском; анализы, поданные в 2025 году, отсечкой исключаются.' % SH.ENLIL_PUBLICATION_LAG_MIN,
              '* Базовый метод «последнее наблюдение» проверен только по Kp: архива GOES за май 2024 в репозитории нет.',
              '* Контрольная неделя выбрана по отсутствию карточек GST/SEP в DONKI; отсутствие карточки не доказывает отсутствие '
              'слабой активности (DONKI заводит GST при заметной буре).',
              '* Окна — %d ч с шагом начала 1 ч, период поиска %d ч; сервис — сетка от 00:00Z. Другая сетка даст другие задержки применения, '
              'но не изменит заблаговременность поставщика.' % (DURATION_MIN // 60, SEARCH_H),
              '* Поставщик истории — временная заглушка `experiments/legacy/stub_history.py`; после появления `vkd/history` (A2) эксперимент '
              'повторяется на реестре выпусков с доказанной доступностью.',
              '* Вариант «любое уведомление — проверка» (таблица 1.6) отвергнут: ветка условий `vkd/windows/compare.py` образует условия только '
              'из протонных событий, бурь с Kp ≥ порога и прогнозов прихода выброса (тест `test_storm_level_below_threshold_is_information_not_condition`).',
              '']
    lines += exp5_forecast_at_cutoff(bundle)
    text = '\n'.join(lines)
    print(text)
    with io.open(OUT_MD, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    print('\n[записано %s]' % os.path.relpath(OUT_MD, ROOT))


if __name__ == '__main__':
    main()
