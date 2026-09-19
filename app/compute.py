# -*- coding: utf-8 -*-
"""Расчётный конвейер без интерфейса (Т7: получение, расчёт и интерфейс — три слоя).

run(params) собирает один снимок расчёта. Его используют:
  * app/main.py — рендер экрана и кнопки выгрузки из одного и того же снимка;
  * scripts/make_examples.py — сохранённые примеры расчётов для сдачи (Т8);
  * эксперименты — прогоны без Streamlit.

Слои А: орбита — vkd.orbit (A3) через vkd.integration.orbit_bridge; живые источники —
vkd.sources (A4); история (уведомления DONKI по исходным телам сообщений, архив наблюдений
GOES 2024 NASA iSWA, окончательный ряд Kp GFZ) — vkd.history (A2). Заглушек в конвейере нет:
прежние временные модули перенесены в experiments/legacy/ и в расчёте не участвуют.

Прогнозы NOAA до отсечки — vkd.history.replay (A2) через vkd.integration.noaa_forecast;
живой трёхсуточный прогноз NOAA — vkd.sources.noaa_latest через тот же адаптер линий.

Исторические режимы НЕ обращаются к живым источникам: GOES и Kp берутся из архива
(или честно отсутствуют), TLE не нужен — орбита из OEM. Записи sources в снимке
описывают фактическое происхождение, а не результат живого запроса 2026 года.
"""
from __future__ import annotations

import io
import base64
import hashlib
from copy import deepcopy
import json
import os
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.fetch_guard import fetch_live_sources
# Форматирование чисел и времён — тем же средством, что экран (app/ui.py: ни одного вызова
# Streamlit, только перевод величин в русский текст). Иначе одна и та же величина печатается
# в снимке и на экране по-разному: «9,00» в сводке проверки против «9» в таблице под ней.
from app.ui import dt_ru, sup
from vkd.assess.cutoff import apply_cutoff
from vkd.assess.magcoords import belt_coordinates
from vkd.assess.seasonal import seasonal_hits_track, MODEL_ID, CATALOGUE_ID, METHOD_ID
from vkd.assess.trapped import BeltTable
from vkd.config import section as _cfg_section, settings_path
from vkd.explain.cards import cards_for_window
from vkd.explain.format import fmt_ru
from vkd.history import history_bundle
from vkd.integration.noaa_forecast import STATUS_RU as FC_STATUS_RU, live_forecasts, noaa_forecasts
from vkd.integration.orbit_bridge import ORBIT_SRC, TLE_URL_UNKNOWN, build_orbit, provenance_summary
from vkd.sources import Fetch, goes_latest, kp_latest, noaa_latest, tle_latest
from vkd.types import Request, SCHEMA_VERSION, Window
from vkd.integration.manifest import collect_records
from vkd.windows.compare import Thresholds, action_span, assess_window, overlaps, recommend
from vkd.windows.scenario import Scenario, apply_to_windows, simulated_events, simulated_kp
from vkd.windows.sensitivity import robustness

def fmt(v, unit: str = '') -> str:
    """Число — ЕДИНЫМ правилом ядра (vkd.explain.format.fmt_ru, порог степенной записи SCI_MIN),
    степень надстрочными цифрами (app.ui.sup). Расчёт, экран и выгрузка печатают одну и ту же
    величину одинаково: два порога в двух функциях давали «88701» в одной строке и «1,65·10⁶»
    в соседней (девятый круг, М5)."""
    return sup(fmt_ru(v, unit))


SRC_LAYER = 'vkd.sources'       # A4: живой запрос → кеш → снимок репозитория, три состояния источника
HIST_SRC = 'vkd.history'        # A2: уведомления DONKI, архивы наблюдений GOES и Kp за 2024, выпуски NOAA

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALGO_VERSION = '0.10.0'  # 0.7.0 + объявленная область вывода вердикта при частичном покрытии (CONTRACT §4 п. 1, решение владельца 19.09)
MODES = ('live', 'history_review', 'history_forecast')
MODE_RU = {'live': 'Текущая обстановка', 'history_review': 'Исторический разбор', 'history_forecast': 'Прогноз из прошлого'}
DONKI_ARCHIVE_DEFAULT = (datetime(2024, 5, 1, tzinfo=timezone.utc), datetime(2024, 7, 1, tzinfo=timezone.utc))

POLICY_NOTE = ('Правило команды, не норма: окно с протонным событием, бурей Kp ≥ 7 (наблюдение или прогноз) или сообщением '
               'о сближении автоматически не выбирается — его проверяет аналитик. Шкалы NOAA сами по себе не запрещают '
               'и не разрешают ВКД.')

# границы постановки (docs/case: длительность 1…8 ч, период начала до 24 ч, 2–3 окна)
DURATION_MIN_RANGE = (60, 480)
SEARCH_MIN_MAX = 1440
WINDOWS_RANGE = (2, 3)


@dataclass
class Result:
    S: dict                    # снимок для экрана и выгрузки
    raw_records: dict
    traj: list
    meta: Any
    assessments: list
    rec: Any
    cards: list                # карточки всех окон подряд (окно 1, затем окно 2, …), у каждой window_index
    events: list
    rob: Any
    goes: Any
    kp: Any
    excluded: list
    fetch_status: dict
    forecasts: list = None     # линии прогнозов NOAA (история)
    orbit: Any = None          # OrbitResult
    kp_obs: list = None        # исторический разбор: наблюдения Kp (от, до, значение) для ленты
    cards_by_window: dict = None   # start_utc -> карточки окна
    verification: dict = None      # прогноз из прошлого: что наблюдалось после отсечки (в расчёт не входит)


def validate_request(mode: str, t0: datetime, duration_min: int, search_min: int, window_offsets_min) -> None:
    """Границы постановки для любого вызова конвейера (Т7), не только для ползунков экрана."""
    if mode not in MODES:
        raise ValueError('неизвестный режим %r; допустимы %s' % (mode, ', '.join(MODES)))
    if not isinstance(t0, datetime) or t0.tzinfo is None or t0.utcoffset() is None:
        raise ValueError('начало периода t0 должно быть datetime с часовым поясом (UTC)')
    if type(duration_min) is not int or not (DURATION_MIN_RANGE[0] <= int(duration_min) <= DURATION_MIN_RANGE[1]):
        raise ValueError('длительность ВКД %s мин вне границ постановки %d…%d мин' % (duration_min, *DURATION_MIN_RANGE))
    if type(search_min) is not int or not (0 <= int(search_min) <= SEARCH_MIN_MAX):
        raise ValueError('период поиска начала %s мин вне границ постановки 0…%d мин' % (search_min, SEARCH_MIN_MAX))
    offs = list(window_offsets_min)
    if not (WINDOWS_RANGE[0] <= len(offs) <= WINDOWS_RANGE[1]):
        raise ValueError('окон для сравнения должно быть %d…%d, задано %d' % (*WINDOWS_RANGE, len(offs)))
    if len(set(offs)) != len(offs):
        raise ValueError('сдвиги окон должны различаться: одинаковые окна сравнивать бессмысленно (%s)' % offs)
    for o in offs:
        if type(o) is not int or not (0 <= int(o) <= int(search_min)):
            raise ValueError('сдвиг окна %s мин вне периода поиска 0…%s мин' % (o, search_min))
    if mode != 'live':
        # архив 2024 года ограничен: начало вне его границ — отказ с русским сообщением,
        # а не английское исключение адаптера истории где-то в середине расчёта
        a0, a1 = DONKI_ARCHIVE_DEFAULT
        if not (a0 <= t0 < a1):
            raise ValueError('начало периода %s вне архива исторических режимов %s — %s; выберите дату внутри архива'
                             % (t0.strftime('%Y-%m-%d %H:%MZ'), a0.strftime('%d.%m.%Y'),
                                (a1 - timedelta(minutes=1)).strftime('%d.%m.%Y')))


def _min0(t: Optional[datetime]) -> Optional[datetime]:
    """Момент без секунд: заблаговременность считается по тем же минутам, которые напечатаны
    рядом, иначе «10.05 13:46» и «через 1 ч 47 мин» расходятся на глазах у читателя."""
    return t.replace(second=0, microsecond=0) if t else None


def _lead_ru(a: datetime, b: datetime) -> str:
    """Заблаговременность от b до a словами: «3 ч 00 мин», «1 ч 46 мин».
    Отрицательной заблаговременности не бывает: если событие началось раньше, так и сказано."""
    m = int((_min0(a) - _min0(b)).total_seconds() // 60)
    if m < 0:
        return 'заблаговременности нет: началось на %d ч %02d мин раньше отсечки' % (abs(m) // 60, abs(m) % 60)
    return 'заблаговременность %d ч %02d мин' % (m // 60, m % 60)


def _verification_summary(ver: dict, t0: datetime, th, kinds: set) -> tuple:
    """Сводка проверки после отсечки — С ВЕРДИКТОМ по каждой линии, а не перечнем фактов.

    Т5 требует «проверены ошибки и ложные предупреждения». Прежняя сводка ровным перечислением
    сообщала, что буря была и что протонное событие опубликовано через 1 ч 46 мин после отсечки,
    и ни разу не говорила, что первое — попадание, а второе — ПРОПУСК (девятый круг, М4-17).
    Каждая линия получает одну из четырёх меток: сбылось / не предупредили / ложная тревога /
    ложных тревог нет. Метка выводится из двух вещей: ставил ли сервис условие по линии (kinds —
    виды условий, поставленных расчётом) и что наблюдалось после отсечки (факт из архива).

    Возвращает (текст, разметка по линиям) — разметка идёт в снимок отдельным ключом, чтобы
    экран и выгрузка не разбирали текст обратно.
    """
    rows = [(datetime.fromisoformat(x['from_utc']), x['kp']) for x in (ver.get('kp_obs') or [])]
    first_storm = next(((a, v) for a, v in rows if v >= th.kp_check), None)
    kp_max = max((v for _, v in rows), default=None)
    g = ver.get('goes_obs_max') or None
    g_val = float(g['value_pfu']) if g else None
    g_t = datetime.fromisoformat(g['t_utc']) if g else None
    seps = [e for e in (ver.get('events') or []) if e.get('kind') == 'SEP']
    sep_pub = min((datetime.fromisoformat(e['published_utc']) for e in seps), default=None)

    storm_warned, proton_warned = ('GST' in kinds), bool(kinds & {'SEP', 'GOES'})
    storm_fact = first_storm is not None
    proton_fact = bool(seps) or (g_val is not None and g_val >= th.goes_p10_warning_pfu)

    parts, marks = [], {}
    kp_ru = ('максимум Kp %s' % fmt(float(kp_max))) if kp_max is not None else \
        'наблюдений Kp в архиве (окончательный ряд GFZ, резерв — уведомления DONKI о буре) на горизонте нет'
    if storm_fact and storm_warned:
        marks['storm'] = 'hit'
        parts.append('Сбылось: буря Kp ≥ %s — да; условие поставлено в %s по уведомлениям, факт — Kp %s с %s, '
                     'максимум %s; %s.'
                     % (fmt(float(th.kp_check)), dt_ru(t0), fmt(float(first_storm[1])), dt_ru(first_storm[0]),
                        fmt(float(kp_max)), _lead_ru(first_storm[0], t0)))
    elif storm_fact and not storm_warned:
        marks['storm'] = 'miss'
        parts.append('Не предупредили: геомагнитная буря. Факт — Kp %s с %s, максимум %s. По данным, доступным '
                     'на %s, этой линии у нас не было — это пропуск, а не ложная тревога.'
                     % (fmt(float(first_storm[1])), dt_ru(first_storm[0]), fmt(float(kp_max)), dt_ru(t0)))
    elif storm_warned and not storm_fact:
        marks['storm'] = 'false_alarm'
        parts.append('Ложная тревога по буре: условие Kp ≥ %s поставлено, факт — %s, порог не достигнут.'
                     % (fmt(float(th.kp_check)), kp_ru))
    else:
        marks['storm'] = 'true_negative'
        parts.append('Ложных тревог по буре нет: условий проверки на отсечку не ставилось, факт — %s.' % kp_ru)

    g_ru = ('максимум наблюдённого потока GOES ≥10 МэВ %s pfu в %s' % (fmt(g_val), dt_ru(g_t))) if g else \
        'численных наблюдений GOES ≥10 МэВ на горизонте в архиве нет'
    if proton_fact and proton_warned:
        marks['proton'] = 'hit'
        parts.append('Сбылось: протонное событие — да; условие поставлено в %s, факт — %s%s.'
                     % (dt_ru(t0), g_ru, ('; первое уведомление DONKI %s' % dt_ru(sep_pub)) if sep_pub else ''))
    elif proton_fact and not proton_warned:
        marks['proton'] = 'miss'
        _late = int((_min0(sep_pub) - _min0(t0)).total_seconds() // 60) if sep_pub else 0
        lead = ('Первое уведомление DONKI вышло %s — через %d ч %02d мин ПОСЛЕ отсечки. '
                % (dt_ru(sep_pub), _late // 60, _late % 60)) if (sep_pub and _late > 0) else (
            ('Первое уведомление DONKI вышло %s. ' % dt_ru(sep_pub)) if sep_pub else '')
        parts.append('Не предупредили: протонное событие. %sФакт — %s. По данным, доступным на %s, этой линии '
                     'у нас не было — это пропуск, а не ложная тревога.' % (lead, g_ru, dt_ru(t0)))
    elif proton_warned and not proton_fact:
        marks['proton'] = 'false_alarm'
        parts.append('Ложная тревога по протонному событию: условие поставлено, факт — %s, порог предупреждения '
                     '%s pfu не превышен.' % (g_ru, fmt(float(th.goes_p10_warning_pfu))))
    else:
        marks['proton'] = 'true_negative'
        parts.append('Ложных тревог по протонному событию нет: условий по нему не ставилось, факт — %s (порог '
                     'предупреждения %s pfu).' % (g_ru, fmt(float(th.goes_p10_warning_pfu))))
    return ' '.join(parts), marks


VERIFICATION_MARK_RU = {'hit': 'сбылось', 'miss': 'не предупредили (пропуск)',
                        'false_alarm': 'ложная тревога', 'true_negative': 'ложных тревог нет'}


def _donki_catalog_coverage() -> tuple:
    """Границы архива уведомлений DONKI — из реестра A1 (coverage.json: периоды запросов), а не из константы."""
    p = os.path.join(ROOT, 'data', 'source_registry_2024', 'donki', 'coverage.json')
    try:
        d = json.load(io.open(p, encoding='utf-8'))
        periods = d.get('query_periods') or []
        starts = [datetime.fromisoformat(x['start']).replace(tzinfo=timezone.utc) for x in periods]
        ends = [datetime.fromisoformat(x['end']).replace(tzinfo=timezone.utc) for x in periods]
        if starts and ends:
            return min(starts), max(ends), int(d.get('record_count') or 0), 'data/source_registry_2024/donki/coverage.json'
    except Exception:            # noqa: BLE001 — реестр недоступен: честно объявленная константа
        pass
    return DONKI_ARCHIVE_DEFAULT + (None, 'константа (реестр A1 не прочитан)')


# причины исключения записей адаптером истории (A2) — по-русски для экрана и выгрузки
EXCLUDED_RU = {
    'not_available_at_cutoff': 'опубликовано или доступно позже отсечки',
    'unknown_publication_or_availability': 'время публикации или доступности неизвестно',
    'ambiguous_notification_versions': 'разные версии одного сообщения к отсечке — неоднозначно',
    'historic_publication_and_version_availability_not_proven':
        'историческая публикация и доступность именно этой версии не доказаны',
    # причины адаптера A2 и разборщика уведомлений A1 приходят по-английски (vkd/sources/donki.py,
    # vkd/sources/gfz_archive.py). На экране идентификаторов и английского быть не должно (О5),
    # поэтому каждая переводится здесь дословно, без обобщения и без потери причины.
    'out_of_scope: no explicit Earth arrival in primary summary':
        'вне области: в теле уведомления нет объявленного прихода к Земле',
    'out_of_scope: not a GOES observation at Earth':
        'вне области: запись не является наблюдением GOES у Земли',
    'context_only: weekly retrospective report is not a current event':
        'только контекст: недельный обзор за прошедшую неделю — не текущее событие',
    'publication_conflict: API/body issue times differ by at least one minute':
        'расхождение времени публикации: в ответе службы и в теле сообщения оно отличается не меньше чем на минуту',
    'unsupported: unrecognized_primary_summary':
        'тело сообщения не разобрано: сводка в неизвестном формате',
    'final_GFZ_index_without_historic_publication_review_only':
        'окончательный индекс GFZ без собственного времени публикации — только для разбора после факта',
}


def excluded_ru(reason: str) -> str:
    """Причина исключения записи по-русски. Непереведённая причина отдаётся как есть — молча
    подменять её обобщением нельзя: в списке исключённого должна стоять настоящая причина."""
    if reason in EXCLUDED_RU:
        return EXCLUDED_RU[reason]
    if reason.startswith('invalid: '):        # исключение разборщика: текст ошибки оставляем дословно
        return 'запись не разобрана: ' + reason[len('invalid: '):]
    return reason
# исключения адаптера, которые НЕ являются отсечкой (запись вне отображаемого контекста запроса):
# в список «после отсечки не использовано» они не попадают, иначе список перестаёт значить то, что назван
_NOT_CUTOFF_REASONS = ('outside_requested_display_context',)
GOES_CHANNEL = 'goes_p_ge10MeV'      # численные наблюдения GOES ≥10 МэВ (A2, архив NASA iSWA)


def _fold_kp_obs(samples) -> list:
    """Наблюдения Kp на горизонте — по одной строке на 3-часовой интервал.

    Возвращает [(от, до, Kp, запись, происхождение)] с возрастанием времени. Один интервал
    сообщают несколько записей: окончательный ряд GFZ (собственного времени публикации у него
    нет) и уведомления DONKI о буре (датированные). Берётся значение с САМЫМ ПОЗДНИМ известным
    временем публикации — уточнение позже отменяет предварительное; записи без времени
    публикации стоят в этом порядке первыми и уступают датированному уведомлению.

    Правило не молчаливое: колонка происхождения называет число записей, взятую запись, время
    её публикации и — при расхождении — с какого значения на какое уточнено.
    """
    from vkd.explain.format import record_ru
    groups: dict = {}
    for s in samples:
        groups.setdefault((s.valid_from_utc, s.valid_to_utc), []).append(s)
    out = []
    for (a, b), recs in sorted(groups.items()):
        # порядок: сначала без времени публикации, затем по возрастанию времени публикации
        recs = sorted(recs, key=lambda s: (s.published_utc is not None,
                                           s.published_utc or datetime(1, 1, 1, tzinfo=timezone.utc)))
        last = recs[-1]
        n = len(recs)
        vals = [float(s.value) for s in recs if s.value is not None]
        rounded = sorted({round(v, 2) for v in vals})
        if n == 1:
            org = record_ru(last.raw_record_id, with_kind=False)
            if last.published_utc:
                org += ', публикация %s' % last.published_utc.strftime('%d.%m %H:%MZ')
            else:
                org += ', собственного времени публикации у записи нет'
        else:
            org = '%d %s' % (n, _plural_ru(n, 'запись', 'записи', 'записей'))
            if len(rounded) > 1:
                org += '; уточнено с %s до %s' % (_num_ru(rounded[0]), _num_ru(rounded[-1]))
            else:
                org += ', значение одно'
            org += '; взято %s' % record_ru(last.raw_record_id, with_kind=False)
            org += (', публикация %s' % last.published_utc.strftime('%d.%m %H:%MZ')) if last.published_utc \
                else ' (самая поздняя известная публикация в группе отсутствует — взята последняя запись)'
        out.append((a, b, last.value, last.raw_record_id, org))
    return out


def _note_short(note, limit: int = 160) -> str:
    """Заметка записи для сопоставления с фактом. Обрезка идёт по границе слова и помечается
    многоточием: срез ровно на limit давал в отчёте оборванное «исходное сообщение 20240510-AL-00»
    (находка четвёртого круга — оборванных фраз в выгрузке быть не должно)."""
    s = (note or '').strip()
    if len(s) <= limit:
        return s
    cut = s[:limit].rstrip()
    sp = cut.rfind(' ')
    return (cut[:sp] if sp > limit // 2 else cut).rstrip(' ,;.') + ' …'


def _plural_ru(n: int, one: str, few: str, many: str) -> str:
    if 11 <= n % 100 <= 19:
        return many
    return one if n % 10 == 1 else (few if 2 <= n % 10 <= 4 else many)


def _num_ru(v: float) -> str:
    return ('%.2f' % v).rstrip('0').rstrip('.').replace('.', ',')


def _empty_fetch(source_id: str, why: str) -> Fetch:
    return Fetch(source_id, False, False, None, None, why, None, None)


def tolerance_caption(th, rob, preferred, requested_fluence_ratio: float, pair_txt=lambda pair: '') -> str:
    """Подпись под таблицей сравнения окон: из чего получен допуск равнозначности и что
    говорит о выборе сетка порогов и каналов.

    Подпись обязана совпадать И с вердиктом, И с сеткой. Пока хвост собирался безусловно,
    при вердикте «окна равнозначны» (предпочтительного окна нет) печаталось «выбор устойчив:
    на всей сетке одинаковы и вердикт, и ПРЕДПОЧТИТЕЛЬНОЕ ОКНО», а при вердикте «все окна
    требуют проверки» рядом стояли «порядок окон … МЕНЯЕТСЯ» и «выбор устойчив» плюс числовой
    «допуск 1 мин», хотя допуск в этом случае не вычисляется вовсе (находка третьего круга).

    Поэтому каждая часть печатается только тогда, когда за ней есть вычисленная величина:
      * допуск по минутам — только при непустом rob.diff_by_thr;
      * допуск по флюенсу — только при непустом rob.ratio_by_e;
      * «порядок окон … сохраняется/МЕНЯЕТСЯ» — только там, где на сетке вообще есть лучшее окно;
      * хвост о выборе — тремя ветками по наличию предпочтительного окна здесь и на сетке.
    """
    thr_txt = '/'.join('%.0f' % x for x in rob.grid[0])
    e_txt = '/'.join('%g' % x for x in rob.grid[1])
    if rob.diff_by_thr:
        tol_min_txt = ('допуск %.0f мин — разброс разности минут в аномалии между окнами при порогах %s нТл '
                       '(разность минут%s на сетке от %.0f до %.0f)'
                       % (th.equiv_tol_min, thr_txt, pair_txt(rob.pair),
                          min(rob.diff_by_thr.values()), max(rob.diff_by_thr.values())))
    else:
        tol_min_txt = ('допуск равнозначности по минутам не вычисляется: на сетке порогов %s нТл разность минут между '
                       'двумя лучшими окнами не определена (меньше двух окон без условий либо минуты не вычислены)' % thr_txt)
    if rob.ratio_by_e:
        tol_fl_txt = ('допуск ×%.2f по флюенсу — не меньше настройки ×%.2f и разброса по каналу %s МэВ '
                      '(отношение флюенсов%s по каналу от ×%.2f до ×%.2f)'
                      % (th.fluence_equiv_ratio, requested_fluence_ratio, e_txt, pair_txt(rob.pair_fluence),
                         min(rob.ratio_by_e.values()), max(rob.ratio_by_e.values())))
    else:
        tol_fl_txt = ('допуск равнозначности по флюенсу не вычисляется: отношение флюенсов двух лучших окон по каналу '
                      '%s МэВ не определено (меньше двух окон без условий либо флюенс не вычислен)' % e_txt)
    if all(v is None for v in (rob.ranking_by_grid or {}).values()):
        rank_txt = 'порядок окон при нулевом допуске не определён: ни в одной ячейке сетки правило не называет лучшее окно'
    else:
        rank_txt = 'порядок окон при нулевом допуске на сетке %s' % ('сохраняется' if rob.ranking_stable else 'МЕНЯЕТСЯ')
    grid_prefs = set((rob.preferred_starts or {}).values())
    if preferred is not None:
        choice_txt = ('выбор устойчив: на всей сетке одно и то же предпочтительное окно и тот же вердикт' if rob.stable else
                      'ВЫБОР МЕНЯЕТСЯ на сетке: предпочтительное окно или вердикт на ней не одни и те же')
    elif grid_prefs in ({None}, set()):
        choice_txt = ('предпочтительного окна нет ни в одной ячейке сетки — сравнивать нечего, и вердикт на сетке '
                      'один и тот же' if rob.stable else
                      'предпочтительного окна нет ни в одной ячейке сетки, но ВЕРДИКТ на сетке меняется')
    else:
        choice_txt = 'ВЫБОР МЕНЯЕТСЯ на сетке: здесь предпочтительного окна нет, а в части её ячеек оно есть'
    return '; '.join((tol_min_txt, tol_fl_txt, rank_txt, choice_txt))


def run(mode: str, t0: datetime, duration_min: int, search_min: int, window_offsets_min: list[int],
        disabled: Optional[dict] = None, thresholds: Optional[Thresholds] = None,
        scenario: Optional[Scenario] = None, T_months: int = 6,
        fetched: Optional[tuple] = None, now: Optional[datetime] = None,
        tle_override_path: Optional[str] = None, fetch_note: Optional[str] = None) -> Result:
    """tle_override_path — воспроизведение сохранённого расчёта текущего режима: орбита
    строится по сохранённому TLE, а не по текущему (Т8). В исторических режимах орбита
    берётся из архива OEM (A1/A3) и от TLE не зависит; живые источники не вызываются.

    fetch_note — причина отказа живых источников по ОБЩЕМУ пределу получения, если источники
    получал вызывающий (экран получает их своим кешированным вызовом). Когда их получает сам
    run, причина берётся у app.fetch_guard. Причина стоит и в статусе каждого источника, и
    отдельным ключом снимка `sources['_live_fetch']` — чтобы выгрузка и примеры не гадали."""
    validate_request(mode, t0, duration_min, search_min, window_offsets_min)
    t0 = t0.astimezone(timezone.utc)
    disabled = {'goes': False, 'kp': False, **(disabled or {})}
    th = thresholds or Thresholds.from_settings()
    scenario = scenario or Scenario('none')
    now = now or datetime.now(timezone.utc)
    ref_now = now if mode == 'live' else t0          # момент, от которого считаются давности: в истории — t0
    cutoff_utc = t0 if mode == 'history_forecast' else None
    horizon_min = search_min + duration_min
    if type(scenario.work_delay_min) is not int or not 0 <= scenario.work_delay_min <= 180:
        raise ValueError('задержка работ должна быть целым числом минут 0…180')
    orbit_start = t0 + timedelta(minutes=scenario.work_delay_min)
    is_sim = bool(scenario.work_delay_min or scenario.sep_onset_offset_min is not None or scenario.kp_override is not None)

    if mode == 'history_forecast' and is_sim:
        raise ValueError('сценарий Что если нельзя выдавать за строгий прогноз из прошлого')

    # ------------------------------------------------------------ источники
    f_noaa, noaa_raw, noaa_samples = None, {}, ()
    if fetch_note is not None and not isinstance(fetch_note, str):
        raise ValueError('fetch_note — текст причины или None, получено %r' % (fetch_note,))
    live_fetch_note = fetch_note if mode == 'live' else None
    if mode == 'live':
        if fetched is None:
            # Общий предел на все живые источники: без него первый рендер может не наступить
            # вовсе (зависание DNS тайм-аутом requests не покрывается) — app/fetch_guard.py.
            fetched, _limit_note = fetch_live_sources(disabled)
            live_fetch_note = live_fetch_note or _limit_note
        fetched = tuple(fetched)
        (goes, goes_raw, f_goes), (kp, kp_raw, f_kp), (tle_text, f_tle) = fetched[:3]
    else:
        # история: живые запросы не делаются и не учитываются, даже если экран их передал
        why = 'в историческом режиме живой источник не запрашивается'
        goes, goes_raw, f_goes = None, {}, _empty_fetch('noaa_swpc_goes', why)
        kp, kp_raw, f_kp = None, {}, _empty_fetch('gfz_kp', why)
        tle_text, f_tle = None, _empty_fetch('celestrak_gp', 'в историческом режиме орбита из архива OEM, TLE не нужен')
    # Т6: два разных состояния источника. 'cache' (или True) — имитация отказа: живого запроса нет,
    # берётся кеш с давностью, покрытие становится частичным и объявляется. 'off' — источник
    # исключён: данных нет, покрытие обязательной линии NONE → рекомендации нет. Ни одно из них
    # не превращается в «благоприятно».
    if disabled.get('goes') == 'off' and f_goes is not None:
        goes, goes_raw = None, {}
        f_goes = replace(f_goes, ok=False, from_cache=False, status_ru='источник исключён пользователем — данных нет', payload=None, raw_path=None)
    if disabled.get('kp') == 'off' and f_kp is not None:
        kp, kp_raw = None, {}
        f_kp = replace(f_kp, ok=False, from_cache=False, status_ru='источник исключён пользователем — данных нет', payload=None, raw_path=None)
    if mode == 'live' and kp is not None and kp.valid_to_utc and kp.valid_to_utc > now:
        kp, kp_raw = None, {}
        f_kp = replace(f_kp, ok=False, from_cache=False, age_min=None,
                      status_ru='незавершённый интервал Kp исключён: наблюдение ещё не завершилось')
    events, hist_raw, excluded, fc_lines, fc_raw, forecasts, kp_obs, verification = [], {}, [], [], {}, [], [], None
    f_noaa = _empty_fetch('noaa_swpc_3day_forecast', 'прогноз NOAA не получен')
    if mode == 'live':
        bundle = fetched[3] if len(fetched) > 3 else ((), {}, f_noaa)
        if disabled.get('noaa') == 'off':
            bundle = ((), {}, _empty_fetch('noaa_swpc_3day_forecast', 'источник исключён пользователем'))
        noaa_samples, noaa_raw, f_noaa = bundle
        fc_lines, fc_raw = live_forecasts(bundle, orbit_start, orbit_start + timedelta(minutes=horizon_min))
        forecasts = [sample for line in fc_lines for sample in line.samples]
    observations: list = []          # ряды наблюдений для ленты времени и выгрузки (C3)
    excluded_archive: list = []
    catalog = None
    history_proof = {}
    goes_observations = None
    kp_src_note = None
    goes_src_note = None
    goes_absent_ru = None
    event_facts: dict = {}
    hist_meta: dict = {}
    if mode != 'live':
        c0, c1, n_msg, cat_src = _donki_catalog_coverage()
        catalog = (c0, c1)
        # A2: запрос передаётся адаптеру целиком — иначе он не может выбрать правильные выпуски
        # NOAA и границы архива наблюдений (vkd/history/README.md). Сам адаптер применяет свою
        # отсечку; apply_cutoff ниже — независимая потребительская проверка каждой записи (Т4).
        hreq = Request(mode=mode, eva_start_utc=t0, duration_min=int(duration_min),
                       search_period_min=int(search_min), cutoff_utc=cutoff_utc,
                       disabled_sources=tuple(sorted(k for k, v in disabled.items() if v == 'off')),
                       work_delay_min=int(scenario.work_delay_min or 0))
        h_samples, h_events, hist_raw = history_bundle(hreq)
        hist_meta = hist_raw.get('_history') or {}
        # структурированные факты уведомлений (Kp бури, канал и порог SEP, диапазон Kp прихода выброса):
        # правило читает их, а не русский текст заметки (разбор Codex, п. 3)
        event_facts = {rid: dict(((meta.get('content_audit') or {}).get('facts') or {}))
                       for recs in (hist_meta.get('source_versions') or {}).values() for rid, meta in recs.items()}
        cut = apply_cutoff(h_samples, h_events, [], cutoff_utc)
        # «исключено отсечкой» — только там, где отсечка есть (строгий режим). В разборе
        # отсечки нет, и называть ею записи, непригодные по другим причинам, нельзя:
        # они идут отдельным списком с собственной причиной.
        adapter_excluded = ['%s: %s' % (x['raw_record_id'], excluded_ru(x['reason']))
                            for x in (hist_meta.get('excluded') or [])
                            if not any(x['reason'].startswith(p) for p in _NOT_CUTOFF_REASONS)]
        excluded = list(cut.excluded) + (adapter_excluded if cutoff_utc is not None else [])
        excluded_archive = [] if cutoff_utc is not None else adapter_excluded
        history_proof = hist_meta
        kp_hist = [s for s in cut.samples if s.channel_id == 'kp' and s.t_utc <= t0]
        kp = max(kp_hist, key=lambda s: s.t_utc) if (kp_hist and disabled.get('kp') != 'off') else None
        kp_raw = {kp.raw_record_id: hist_raw.get(kp.raw_record_id)} if kp else {}
        # C3: численные наблюдения GOES ≥10 МэВ из архива NASA iSWA (A2). В строгом режиме адаптер
        # исключает их сам, а apply_cutoff исключает повторно: published_utc у выпуска нет.
        goes_hist = [s for s in cut.samples if s.channel_id == GOES_CHANNEL and s.value is not None and s.t_utc <= t0]
        goes = max(goes_hist, key=lambda s: s.t_utc) if (goes_hist and disabled.get('goes') != 'off') else None
        goes_raw = {goes.raw_record_id: hist_raw.get(goes.raw_record_id)} if goes else {}
        if mode == 'history_review':
            goes_observations = [s for s in cut.samples if s.channel_id == GOES_CHANNEL]
            goes_raw = {s.raw_record_id: hist_raw[s.raw_record_id] for s in goes_observations}

        goes_cov_a2 = (hist_meta.get('coverage_map') or {}).get('goes_p_ge10MeV:observations') or {}
        if disabled.get('goes') == 'off':
            goes_src_note = 'источник исключён пользователем — данных нет'
            goes_absent_ru = 'архив наблюдений GOES исключён пользователем'
        elif goes is not None:
            # имя режима в префиксе — по фактическому режиму: «разбор» означает history_review
            goes_src_note = ('%sчисленный архив наблюдений GOES ≥10 МэВ (NASA iSWA, 5-минутные средние), запись %s; '
                             'временной охват горизонта %.2f %%'
                             % ('строгий режим: ' if mode == 'history_forecast' else 'разбор: ',
                                goes.raw_record_id, 100.0 * float(goes_cov_a2.get('coverage_fraction') or 0.0)))
        elif mode == 'history_forecast':
            goes_absent_ru = ('архив наблюдений GOES 2024 в строгом режиме исключён: %s'
                              % EXCLUDED_RU['historic_publication_and_version_availability_not_proven'])
            goes_src_note = 'строгий режим: ' + goes_absent_ru + ' (канал остаётся покрытым только датированными уведомлениями DONKI)'
        else:
            goes_absent_ru = 'численных наблюдений GOES в архиве на этот момент нет (%s)' % (goes_cov_a2.get('reason') or 'запись отсутствует')
            goes_src_note = 'разбор: ' + goes_absent_ru
        # C3: пятиминутный ряд наблюдений GOES ≥10 МэВ на ленту окна. Берётся из cut.samples,
        # то есть в строгом режиме ряда нет по той же отсечке, что и у остальных записей, —
        # экран не может показать наблюдение, которого на тот момент не было доказано.
        if disabled.get('goes') != 'off':
            _g_line = sorted((s for s in cut.samples
                              if s.channel_id == GOES_CHANNEL and s.value is not None
                              and t0 - timedelta(hours=12) <= s.t_utc <= t0 + timedelta(minutes=horizon_min)),
                             key=lambda s: s.t_utc)
            if _g_line:
                # Прослеживаемость ряда: у каждой записи выгрузки должно быть ЛИБО время публикации,
                # ЛИБО названная причина его отсутствия — иначе причина живёт только в таблице
                # источников на экране, а в самом файле ряда её нет (находка третьего круга).
                _g_pub = _g_line[0].published_utc
                observations.append({
                    'channel': GOES_CHANNEL, 'label': 'GOES, протоны ≥10 МэВ — наблюдение',
                    'unit': _g_line[0].unit, 'source_id': _g_line[0].source_id,
                    'published_utc': (_g_pub.isoformat() if _g_pub else None),
                    'publication_absence_reason': (
                        None if _g_pub else
                        'у записей архива наблюдений собственного времени публикации нет: %s'
                        % EXCLUDED_RU['historic_publication_and_version_availability_not_proven']),
                    'record': _g_line[0].raw_record_id,
                    'quality': _g_line[0].quality, 'n_points': len(_g_line),
                    'points': [{'t': s.t_utc.isoformat(), 'value': float(s.value)} for s in _g_line]})
        if disabled.get('kp') == 'off':
            kp_src_note = 'источник исключён пользователем — данных нет'
        elif kp is None:
            kp_src_note = ('строгий режим: до отсечки %s нет уведомления DONKI с наблюдённым Kp; окончательный ряд GFZ '
                           'в строгом режиме исключён — у его интервалов нет собственного времени публикации'
                           % t0.strftime('%Y-%m-%d %H:%MZ') if mode == 'history_forecast'
                           else 'разбор: наблюдений Kp в архиве (окончательный ряд GFZ %s — %s, резерв — уведомления DONKI о буре) до %s нет' % (
                               DONKI_ARCHIVE_DEFAULT[0].strftime('%d.%m.%Y'), (DONKI_ARCHIVE_DEFAULT[1] - timedelta(minutes=1)).strftime('%d.%m.%Y'),
                               t0.strftime('%Y-%m-%d %H:%MZ')))
        if mode == 'history_review' and disabled.get('kp') != 'off':      # разбор: наблюдения Kp вокруг периода — контекст ленты, не вход строгого режима
            kp_obs = sorted({(s.valid_from_utc, s.valid_to_utc, s.value) for s in h_samples
                             if s.channel_id == 'kp' and s.valid_from_utc and s.valid_to_utc
                             and t0 - timedelta(hours=12) <= s.t_utc <= t0 + timedelta(minutes=horizon_min + 180)})
        # события, чей интервал касается [t0 − 6 ч, конец горизонта]; давность публикации не ограничивается —
        # прогноз прихода выброса, выпущенный за трое суток, всё равно относится к окну
        def _touches(e):
            # то же правило пересечения, что при отборе условий в compare.action_span/overlaps:
            # конец действия по настройке своего типа события, пересечение не меньше минуты
            a0, a1 = action_span(e, th)
            if a0 is None:
                return True
            return overlaps(a0, a1, t0 - timedelta(hours=6), orbit_start + timedelta(minutes=horizon_min))
        events = [e for e in cut.events if _touches(e)]
        # прогнозы NOAA, выпущенные до отсечки (в разборе — до начала периода): A1/A2 через адаптер Б
        if disabled.get('noaa') == 'off':
            fc_lines, fc_raw = live_forecasts(((), {}, _empty_fetch('noaa_swpc_3day_forecast',
                'источник исключён пользователем')), orbit_start, orbit_start + timedelta(minutes=horizon_min))
        else:
            fc_lines, fc_raw = noaa_forecasts(t0, orbit_start, orbit_start + timedelta(minutes=horizon_min))
        forecasts = [s for line in fc_lines for s in line.samples]
        if mode == 'history_forecast':
            # Проверка после отсечки: что наблюдалось потом — В РАСЧЁТ НЕ ВХОДИТ, только сопоставление (Т4, О6).
            # Строгий снимок по построению не содержит записей после отсечки, поэтому факт берётся
            # ОТДЕЛЬНЫМ разбором того же архива (history_review) — он нигде не смешивается с расчётом.
            h_end = orbit_start + timedelta(minutes=horizon_min)
            v_samples, v_events, _ = history_bundle(replace(hreq, mode='history_review', cutoff_utc=None))
            # Один 3-часовой интервал сообщают несколько записей: окончательный ряд GFZ и одно-два
            # уведомления DONKI о буре, причём значения могут расходиться (8,67 против 9). Прежде
            # дедупликация включала идентификатор записи, и таблица печатала один интервал по три
            # раза, а один — дважды с разными Kp и без единого слова о том, почему (находка
            # четвёртого круга). Сворачиваем по интервалу и называем происхождение вслух.
            kp_after = _fold_kp_obs([s for s in v_samples
                                     if s.channel_id == 'kp' and s.valid_from_utc and s.valid_to_utc
                                     and s.valid_to_utc > t0 and s.valid_from_utc < h_end])
            goes_after = sorted((s.t_utc, s.value, s.raw_record_id) for s in v_samples
                                if s.channel_id == GOES_CHANNEL and s.value is not None and t0 < s.t_utc <= h_end)
            ev_after = [e for e in v_events if e.published_utc and e.published_utc > cutoff_utc and e.published_utc <= h_end
                        and e.kind_of_event in ('SEP', 'GST')]
            # Сводка собирается НИЖЕ, после расчёта условий (_verification_summary): чтобы назвать
            # попадание попаданием, а пропуск — пропуском, нужно знать, какие условия сервис
            # поставил; здесь условий ещё нет. Числа и времена — теми же средствами, что экран
            # (fmt, dt_ru): жёсткие форматы '%.2f' и '%.6g' печатали ту же величину иначе, чем
            # таблица прямо под этой строкой (находка пятого круга).
            verification = {
                # U5: имя программного слоя — отдельным ключом, чтобы оперативный уровень экрана
                # печатал только русский текст, а происхождение факта оставалось прослеживаемым
                'note': 'в расчёт не входит: наблюдения и публикации после отсечки, только для сопоставления прогноза '
                        'с фактом; источник факта — тот же архив, прочитанный в режиме исторического разбора',
                'source_layer': '%s, history_review' % HIST_SRC,
                'cutoff_utc': cutoff_utc.isoformat(), 'horizon_to_utc': h_end.isoformat(),
                'kp_obs': [{'from_utc': a.isoformat(), 'to_utc': b.isoformat(), 'kp': v, 'record': rid, 'origin': org}
                           for a, b, v, rid, org in kp_after],
                'goes_obs_max': ({'t_utc': max(goes_after, key=lambda x: x[1])[0].isoformat(),
                                  'value_pfu': max(goes_after, key=lambda x: x[1])[1],
                                  'record': max(goes_after, key=lambda x: x[1])[2],
                                  'n_samples': len(goes_after)} if goes_after else None),
                'events': [{'id': e.event_id, 'kind': e.kind_of_event, 'published_utc': e.published_utc.isoformat(),
                            'start_utc': e.start_utc.isoformat() if e.start_utc else None,
                            'note': _note_short(e.note, 160)} for e in ev_after],
                'summary': None,        # заполняется ниже, когда известны поставленные условия
                'marks': {},            # разметка по линиям: сбылось / пропуск / ложная тревога / ложных тревог нет
            }
    if disabled.get('goes') == 'off':
        goes_observations = []
    kp = simulated_kp(kp, t0, scenario)
    sim_events = simulated_events(t0, scenario)
    events = events + sim_events
    for e in sim_events:      # сценарий даёт те же структурированные факты, что и уведомление: правило одно
        if e.kind_of_event == 'SEP' and scenario.sep_level_pfu is not None:
            event_facts[e.raw_record_id] = {'detector': 'сценарий «что если»', 'energy_lower_bound_MeV': 10.0,
                                            'energy_operator': '>', 'flux_lower_bound_pfu': float(scenario.sep_level_pfu),
                                            'flux_operator': '=', 'measured_flux_pfu': float(scenario.sep_level_pfu)}

    # ------------------------------------------------- линия уведомлений о событиях: подключена или нет
    # В текущем режиме источник уведомлений НЕ ОПРАШИВАЕТСЯ: живого загрузчика DONKI в сервисе нет,
    # весь блок событий закрыт условием `mode != 'live'`. Приборная полоса при этом печатала
    # «0 записей · наш подсчёт по реестру», то есть непроверенное выдавалось за проверенное
    # (девятый круг, М2). Признак кладётся в снимок ЯВНО — его читают и экран, и выгрузка;
    # «пропуск данных не равен нулевому риску» (постановка).
    _ev_c0, _ev_c1, _, _ = _donki_catalog_coverage()
    _ev_archive_ru = 'архив уведомлений охватывает %s — %s' % (
        _ev_c0.strftime('%d.%m.%Y'), (_ev_c1 - timedelta(minutes=1)).strftime('%d.%m.%Y'))
    events_line = {
        'connected': mode != 'live',
        'source_ru': 'уведомления NASA DONKI (протонное событие, буря, приход выброса)',
        'records': len(events),
        'simulated_records': len(sim_events),
        'reason_ru': (None if mode != 'live' else
                      'в текущем режиме источник уведомлений (NASA DONKI) не опрашивается: живого загрузчика в сервисе '
                      'нет. Условия ставятся по наблюдению GOES ≥10 МэВ и прогнозу Kp NOAA; %s и доступен в '
                      'исторических режимах' % _ev_archive_ru),
        'archive_ru': _ev_archive_ru,
    }
    # Строка охвата: то же самое словами аналитика, рядом с остальным «не учтено».
    coverage_missing_extra = ((
        'события и уведомления (NASA DONKI) в текущем режиме не опрашиваются — %s, он доступен только в '
        'исторических режимах' % _ev_archive_ru,) if mode == 'live' else ())

    # ------------------------------------------------------------ траектория (A3 через мост Б)
    if tle_override_path and mode == 'live':
        tle_text = open(tle_override_path, encoding='utf-8').read()
        f_tle = replace(f_tle, status_ru='TLE из сохранённого расчёта (воспроизведение)', ok=False, from_cache=True)
    tle_fetched = getattr(f_tle, 'fetched_utc', None)
    # момент расчёта в текущем режиме — не раньше момента получения TLE: иначе A3 честно ставит
    # «реконструкция» рядом со статусом «строго» (Т2)
    live_cutoff = max(now, t0, tle_fetched) if tle_fetched else max(now, t0)
    orb = build_orbit(mode, orbit_start, horizon_min, th.saa_B_threshold_nT, tle_text=tle_text,
                      tle_fetched_utc=tle_fetched, tle_available_utc=tle_fetched,
                      tle_url=(getattr(f_tle, 'url', None) or TLE_URL_UNKNOWN),
                      tle_evidence=getattr(f_tle, 'status_ru', ''), max_tle_age_days=th.tle_max_age_days,
                      cutoff_utc=(t0 if mode != 'live' else live_cutoff))
    meta = orb.meta
    # координаты для таблиц ОСТ — эксцентричный диполь (Б): центральный диполь A3 в ядре аномалии
    # даёт L ниже сетки и нулевой поток на всей трассе (см. vkd/assess/magcoords.py); |B| — от A3
    coeff_name = 'IGRF13.shc' if t0.year < 2025 else 'IGRF14.shc'
    coeff_path = os.path.join(ROOT, 'data', 'orbit', coeff_name)
    traj, belt_coords = belt_coordinates(orb.points, coeff_path)
    if traj:
        # в выгрузку — репозиторный путь и хеш коэффициентов из data/orbit/manifest.json, не путь машины (Т8)
        try:
            man = json.load(io.open(os.path.join(ROOT, 'data', 'orbit', 'manifest.json'), encoding='utf-8'))
            rec_c = next((r for r in man['records'] if r['file'] == coeff_name), {})
        except Exception:        # noqa: BLE001
            rec_c = {}
        belt_coords = {**belt_coords, 'coefficients': 'data/orbit/' + coeff_name,
                       'coefficients_sha256': rec_c.get('sha256'), 'coefficients_record': rec_c.get('raw_record_id')}
        orb.provenance.setdefault('limitations', []).append(
            'Для входа в таблицы ОСТ прил. А использованы L и B/B0 эксцентричного диполя (Б, magcoords), '
            'не значения A3: центральный диполь даёт L < 1,14 в ядре аномалии; %d из %d точек с B/B0 < 1 помечены. '
            'Положение точки переводится WGS84 → ECEF (NIMA TR8350.2), сферическая формула не применяется. '
            'Вертикальная жёсткость обрезания cutoff_GV остаётся методом A3 (центральный наклонённый диполь) — '
            'это ДРУГАЯ модель, чем L и B/B0, и за одну систему координат они не выдаются.'
            % (belt_coords['n_inconsistent_BB0'], belt_coords['n']))
    orbit_ids = tuple((orb.provenance or {}).get('records', {}).keys()) or ('trajectory',)

    # ------------------------------------------------------------ окна, устойчивость, оценка
    belts = BeltTable('min')
    windows = apply_to_windows([Window(t0 + timedelta(minutes=o), duration_min) for o in window_offsets_min], scenario)

    # Full directional seasonal calculation, once per window (not per radiation threshold).
    # Parameters define the agreed reference target, not the area of a spacesuit.
    seasonal_results = {}
    def _mmod(w: Window):
        end = w.start_utc + timedelta(minutes=w.duration_min)
        pts = [p for p in traj if w.start_utc <= p.t_utc <= end]
        try:
            if len(pts) < 2 or pts[0].t_utc != w.start_utc or pts[-1].t_utc != end:
                raise ValueError('орбита не покрывает оба конца окна')
            result = seasonal_hits_track(
                [p.t_utc for p in pts], [p.alt_km for p in pts],
                (orb.provenance or {}).get('inertial_states') or {}, 1.0, 1e-3)
            seasonal_results[w.start_utc.isoformat()] = result.as_dict()
            return result.N, result.rule, result.coverage_fraction
        except ValueError as exc:
            reason = 'Сезонный расчёт метеороидов недоступен: %s' % exc
            seasonal_results[w.start_utc.isoformat()] = {
                'N': None, 'streams_included': False, 'error': reason, 'method_status': 'unavailable'}
            return None, reason, 0.0

    mmod = {w.start_utc: _mmod(w) for w in windows}

    event_facts = {rid: meta.get('content_audit', {}).get('facts', {})
                   for records in history_proof.get('source_versions', {}).values() for rid, meta in records.items()}

    numerical_reports = {}

    def _assess(w, tr, th_i):
        return assess_window(w, tr, belts, goes, kp, [], th_i, ref_now, events=events, catalog_coverage=catalog,
                             mmod_hits=mmod[w.start_utc][0], mmod_rule=mmod[w.start_utc][1],
                             mmod_cov_fraction=mmod[w.start_utc][2], forecasts=forecasts,
                             mmod_seasonal=seasonal_results[w.start_utc.isoformat()],
                             trajectory_record_ids=orbit_ids, cutoff_utc=cutoff_utc,
                             goes_observations=goes_observations, event_facts=event_facts, goes_absent_ru=goes_absent_ru,
                             numerical_report=numerical_reports.setdefault(w.start_utc.isoformat(), {}))

    def _assess_all(tr, kw):
        th_i = replace(th, **kw)
        return [_assess(w, tr, th_i) for w in windows]

    def _decide(A_i, kw):
        return recommend(A_i, replace(th, **kw), mmod_sensitivity=seasonal_results)

    rob = robustness(traj, windows, _assess_all, _decide,
                     thr_grid=[th.saa_B_threshold_nT - 2000, th.saa_B_threshold_nT, th.saa_B_threshold_nT + 2000],
                     e_grid=[12.5, 30.0, 50.0], base_thr=th.saa_B_threshold_nT, base_e=th.e_min_MeV,
                     min_tol_min=1.0, min_tol_ratio=th.fluence_equiv_ratio)
    th = replace(th, equiv_tol_min=rob.tol_min, fluence_equiv_ratio=rob.tol_ratio)
    assessments = [_assess(w, traj, th) for w in windows]
    rec = recommend(assessments, th, mmod_sensitivity=seasonal_results)
    def _pair_txt(pair):
        if not pair:
            return ''
        i_b = next(i + 1 for i, a in enumerate(assessments) if a.window.start_utc.isoformat() == pair[0])
        i_s = next(i + 1 for i, a in enumerate(assessments) if a.window.start_utc.isoformat() == pair[1])
        return ' между окнами %d и %d' % (i_b, i_s)
    rec = replace(rec, is_simulated=is_sim,
                  missing=rec.missing + (('орбита недоступна: %s' % orb.error,) if orb.error else ()),
                  tolerance_basis=tolerance_caption(
                      th, rob, rec.preferred,
                      max(1.0, (thresholds or Thresholds.from_settings()).fluence_equiv_ratio), _pair_txt))
    samples = {**({goes.raw_record_id: goes} if goes else {}), **({kp.raw_record_id: kp} if kp else {}),
               **{s.raw_record_id: s for s in forecasts}}
    def window_samples(a):
        current = dict(samples)
        end = a.window.start_utc + timedelta(minutes=a.window.duration_min)
        for sample in goes_observations or []:
            if sample.valid_from_utc < end and sample.valid_to_utc > a.window.start_utc:
                previous = current.get(sample.raw_record_id)
                if previous is None or previous.value < sample.value:
                    current[sample.raw_record_id] = sample
        return current

    cards_by_window = {a.window.start_utc: cards_for_window(a, window_samples(a), events=events, window_index=i + 1, meta=meta,
                                                            trajectory_ids=orbit_ids, cutoff_utc=cutoff_utc)
                       for i, a in enumerate(assessments)}
    cards = [c for a in assessments for c in cards_by_window[a.window.start_utc]]
    if verification is not None:
        # Т5: вердикт по собственному прогнозу. Виды поставленных условий берутся из расчёта,
        # а не угадываются по тексту: GST — буря, SEP/GOES — протонная линия.
        _kinds = {c.kind for a in assessments for m in a.mechanisms for c in m.conditions}
        _txt, _marks = _verification_summary(verification, t0, th, _kinds)
        verification = {**verification, 'summary': _txt, 'marks': _marks,
                        'marks_ru': {k: VERIFICATION_MARK_RU[v] for k, v in _marks.items()},
                        'conditions_set': sorted(_kinds)}

    # ------------------------------------------------------------ снимок
    iso = lambda v: v.isoformat() if hasattr(v, 'isoformat') else v
    ev_raw = {e.raw_record_id: hist_raw[e.raw_record_id] for e in events if e.raw_record_id in hist_raw}
    raw_records = {**fc_raw, **{k:v for k,v in hist_raw.items() if k != '_history'},
                   **(goes_raw or {}), **(kp_raw or {}), **ev_raw,
                   'orbit_provenance': orb.provenance}
    if mode == 'live':
        if getattr(f_tle, 'raw', None) is not None:
            from vkd.sources.live_cache import raw_record
            raw_records.update(raw_record(f_tle))
        raw_records['iss.tle'] = {'text': tle_text, 'epoch_utc': iso(meta.epoch_utc) if meta and meta.epoch_utc else None,
                                  'fetch': getattr(f_tle, 'status_ru', None), 'url': getattr(f_tle, 'url', None) or TLE_URL_UNKNOWN,
                                  'acquisition_metadata': dict(getattr(f_tle, 'metadata', None) or {})}
        if noaa_raw:
            raw_records.update(noaa_raw)
        # точные байты ответа TLE и квитанция A4 — рядом с нормализованным текстом:
        # повтор текущего режима разбирает исходный ответ, а не наш пересказ (стык, п. 2)
        from vkd.sources.live_cache import raw_record as _raw_record
        raw_records.update(_raw_record(f_tle))

    off_sources = ({'nasa_iswa_goes_primary_p5m', 'nasa_iswa_goes_primary_p5m_schema', 'noaa_swpc_goes'} if disabled.get('goes') == 'off' else set())
    if disabled.get('kp') == 'off':
        off_sources.add('gfz_kp_archive')
    if disabled.get('noaa') == 'off':
        off_sources.update({'noaa_swpc_3day_forecast', 'noaa_ngdc_3day_forecast', 'noaa_ngdc_daypre'})
    raw_records = {rid: item for rid, item in raw_records.items()
                   if item.get('metadata', {}).get('source_id') not in off_sources}
    history_proof['source_versions'] = {sid: records for sid, records in history_proof.get('source_versions', {}).items()
                                        if sid not in off_sources}
    for item in [e for e in events if e.is_simulated] + ([kp] if kp and kp.source_id == 'scenario' else []):
        content = json.dumps(asdict(item), default=lambda v: iso(v), sort_keys=True).encode()
        sha = hashlib.sha256(content).hexdigest()
        raw_records[item.raw_record_id] = {'metadata': {'source_id': 'scenario', 'raw_record_id': item.raw_record_id,
            'sha256': sha, 'version': 'sha256:'+sha, 'bytes': len(content), 'is_simulated': True,
            'quality': 'model', 'published_utc': None, 'availability_proof': None},
            'encoding': 'base64', 'content_base64': base64.b64encode(content).decode()}
    coverage_map = deepcopy(history_proof.get('coverage_map', {}))
    for key in list(coverage_map):
        if (disabled.get('goes') == 'off' and key.startswith('goes_')) or (disabled.get('kp') == 'off' and key in ('kp:observations', 'gfz_kp_archive:observations')) or (disabled.get('noaa') == 'off' and key.startswith('noaa_')):
            coverage_map[key] = {'status': 'disabled', 'coverage_fraction': 0.0, 'reason': 'explicitly_excluded_by_user'}
    source_versions = collect_records(raw_records, history_proof.get('source_versions', {}), orb.provenance, belts, ROOT)

    # Состояние источника — ЯВНЫМ полем снимка, а не подстрокой «исключён» в тексте статуса.
    # Слой источников штатно дописывает в статус живого Kp «незавершённый Kp-nowcast исключён»
    # всякий раз, когда текущий 3-часовой интервал ещё не закончился, и экран, искавший подстроку,
    # объявлял исключённым пользователем источник, который отвечал (находка четвёртого круга).
    # Значения: 'live' — живой ответ; 'cache' — живого ответа нет, взят проверенный кеш или снимок
    # репозитория; 'off' — источник исключён пользователем, данных нет; 'archive' — исторический
    # режим, данные из архива; 'none' — данных нет по иной названной причине; 'builtin' — таблица
    # стандарта в составе сервиса; 'simulated' — сценарий «что если».
    def _state(key: str, f=None, has_data: bool = True) -> str:
        if disabled.get(key) == 'off':
            return 'off'
        if mode != 'live':
            return 'archive' if has_data else 'none'
        if f is not None and f.ok:
            return 'live'
        if f is not None and f.from_cache:
            return 'cache'
        return 'none'

    def _src(f, role, sample=None, key=''):
        # давность — от КОНЦА интервала измерения (valid_to_utc), как в compare.py: у Kp GFZ
        # t_utc — начало трёхчасового интервала, и таблица источников расходилась с фактором на 3 ч
        ref_t = (sample.valid_to_utc or sample.t_utc) if sample else None
        # давность не бывает отрицательной: у живого Kp GFZ текущий 3-часовой интервал ещё не закончился,
        # и таблица источников печатала «−97 мин». Фактор в compare.py клампит через max(0, …) —
        # здесь тот же клампинг, иначе одна и та же величина на одном экране печатается по-разному (R4-3).
        return {'role': role, 'status': f.status_ru, 'live_ok': f.ok, 'from_cache': f.from_cache,
                'state': _state(key, f),
                'origin': ('живой запрос' if f.ok else ('кеш или снимок репозитория' if f.from_cache else 'данных нет')),
                'fetched_utc': iso(f.fetched_utc) if f.fetched_utc else None,
                'data_utc': iso(sample.t_utc) if sample else None,
                'age_min': round((ref_now - ref_t).total_seconds() / 60) if sample else f.age_min}

    # Фактический адрес получения TLE и признак резерва — из квитанции A4 (acquisition_attempts):
    # отчёт называл источником службу из имени парсера (celestrak_gp), хотя данные отдал резервный
    # адрес, и через строку сам себе противоречил (находка четвёртого круга).
    _tle_md = dict(getattr(f_tle, 'metadata', None) or {})
    _tle_attempts = [a for a in (_tle_md.get('acquisition_attempts') or []) if isinstance(a, dict)]
    _tle_url = _tle_md.get('url') or getattr(f_tle, 'url', None)
    _tle_primary = next((a.get('url') for a in _tle_attempts if a.get('url') and a.get('url') != _tle_url and a.get('error')), None)

    orbit_src = {'role': 'орбита', 'status': orb.status_ru, 'strictness': orb.strictness,
                 'state': ('none' if orb.error else
                           ('archive' if mode != 'live' else ('live' if getattr(f_tle, 'ok', None) else
                                                              ('cache' if getattr(f_tle, 'from_cache', None) else 'none')))),
                 'origin': ('живой запрос TLE' if mode == 'live' and getattr(f_tle, 'ok', None) else
                            ('кеш/снимок TLE' if mode == 'live' else 'архив OEM NASA/JSC (реестр A1)')),
                 'live_ok': f_tle.ok if mode == 'live' else None, 'from_cache': f_tle.from_cache if mode == 'live' else None,
                 'fetched_utc': iso(meta.fetched_utc) if meta else None,
                 'epoch_utc': iso(meta.epoch_utc) if meta and meta.epoch_utc else None,
                 'age_h': round((now - meta.epoch_utc).total_seconds() / 3600, 1) if meta and meta.epoch_utc else None,
                 'source_id': meta.source_id if meta else None,
                 'record_ids': list(orbit_ids),
                 'url': _tle_url if mode == 'live' else None,
                 'url_primary_failed': _tle_primary if mode == 'live' else None}
    sources = {'orbit': orbit_src}
    if mode == 'live' and live_fetch_note:
        # Ключ с подчёркиванием: это не источник, а обстоятельство их получения, и перечни
        # источников (экран, реестр, таблица выгрузки) такие ключи пропускают. Само число
        # предела стоит внутри причины (и в effective_config.sources) — второй раз, отдельным
        # полем, его писать нельзя: получение могло идти с другим пределом, чем стоит сейчас в файле.
        sources['_live_fetch'] = {'limit_note': live_fetch_note}
    if mode == 'live':
        sources['noaa_swpc_goes'] = _src(f_goes, 'протоны ≥10 МэВ', goes, key='goes')
        sources['noaa_swpc_goes']['record_ids'] = [goes.raw_record_id] if goes else []
        sources['gfz_kp'] = _src(f_kp, 'Kp', kp if (kp and kp.source_id != 'scenario') else None, key='kp')
        sources['gfz_kp']['record_ids'] = [kp.raw_record_id] if kp else []
        sources['noaa_swpc_3day_forecast'] = {
            **_src(f_noaa, 'прогноз Kp и вероятности NOAA (3 суток)', key='noaa'),
            'data_utc': iso(min((s.published_utc for s in noaa_samples if s.published_utc), default=None)),
            'cells': len(noaa_samples),
            'record_ids': sorted({line.raw_record_id for line in fc_lines if line.raw_record_id})}
        # Линия уведомлений в реестре присутствует и в текущем режиме — со статусом «не
        # запрашивается». Пока строки не было, отсутствие событий читалось как «опросили,
        # событий нет» (девятый круг, М2). Ноль записей без опроса — не ноль риска.
        sources['donki_archive'] = {'role': 'уведомления DONKI (протонное событие, буря, прогноз прихода выброса)',
                                    'status': 'в текущем режиме не запрашивается: живого загрузчика уведомлений в сервисе нет; %s'
                                              % _ev_archive_ru,
                                    'state': 'none', 'live_ok': None, 'from_cache': None,
                                    'origin': 'не запрашивается (доступен в исторических режимах)',
                                    'fetched_utc': None, 'data_utc': None, 'age_min': None,
                                    'events_used': len(sim_events), 'record_ids': []}
    else:
        c0, c1, n_msg, cat_src = _donki_catalog_coverage()
        goes_origin = ('архив наблюдений NASA iSWA (data/goes_2024), 5-минутные средние' if goes is not None
                       else ('исключён строгим режимом' if mode == 'history_forecast' else 'нет данных'))
        sources['noaa_swpc_goes'] = {'role': 'протоны ≥10 МэВ',
                                     'status': goes_src_note or 'канал без данных',
                                     'state': _state('goes', has_data=goes is not None),
                                     'live_ok': None, 'from_cache': None, 'origin': goes_origin, 'fetched_utc': None,
                                     'data_utc': iso(goes.t_utc) if goes is not None else None,
                                     # давность GOES — от МОМЕНТА наблюдения (начала 5-минутного усреднения),
                                     # как её считает фактор в compare.py; иначе таблица и фактор разойдутся
                                     'age_min': max(0, round((t0 - goes.t_utc).total_seconds() / 60)) if goes is not None else None,
                                     'coverage_fraction': (hist_meta.get('coverage_map') or {}).get(
                                         'goes_p_ge10MeV:observations', {}).get('coverage_fraction'),
                                     'record_ids': [goes.raw_record_id] if goes is not None else []}
        kp_origin = {'gfz_kp_archive': 'архив GFZ (окончательный ряд Kp по 3-часовым интервалам, без времени публикации)',
                     'nasa_donki_notification': 'уведомление DONKI о буре (наблюдённый Kp с временем публикации)',
                     'nasa_donki_gst': 'архив DONKI (карточки GST)'}.get(kp.source_id if kp else '', 'архив (%s)' % kp.source_id if kp else 'нет данных')
        # родительный падеж для строки «Kp из …»: «из уведомления DONKI о буре», а не «из уведомление».
        # Имя режима в префиксе — по фактическому режиму: «разбор» — это history_review, разбор после
        # факта. Строгий прогноз, подписанный словом «разбор», стирает ровно то различие, на котором
        # держится проверка отсечкой (находка третьего круга); строка уходит в отчёт и манифест.
        kp_origin_gen = {'gfz_kp_archive': 'архива GFZ (окончательный ряд Kp по 3-часовым интервалам)',
                         'nasa_donki_notification': 'уведомления DONKI о буре',
                         'nasa_donki_gst': 'архива DONKI (карточки GST)'}.get(
            kp.source_id if kp else '', ('архива (%s)' % kp.source_id) if kp else 'нет данных')
        kp_mode_prefix = 'строгий режим: ' if mode == 'history_forecast' else 'разбор: '
        sources['gfz_kp'] = {'role': 'Kp (в разборе — окончательный ряд GFZ; в строгом режиме — уведомления DONKI о буре)',
                             'status': (kp_src_note or '%sKp из %s, запись %s' % (kp_mode_prefix, kp_origin_gen, kp.raw_record_id)) if not (kp and kp.source_id == 'scenario')
                             else 'сценарий «что если»: моделируемое значение',
                             'state': ('simulated' if (kp and kp.source_id == 'scenario') else _state('kp', has_data=kp is not None)),
                             'live_ok': None, 'from_cache': None,
                             'origin': kp_origin if kp and kp.source_id != 'scenario' else ('сценарий' if kp else 'нет данных'),
                             'fetched_utc': None,
                             'data_utc': iso(kp.t_utc) if kp and kp.source_id != 'scenario' else None,
                             'age_min': max(0, round((t0 - (kp.valid_to_utc or kp.t_utc)).total_seconds() / 60))
                             if kp and kp.source_id != 'scenario' else None,
                             'record_ids': [kp.raw_record_id] if (kp and kp.source_id != 'scenario') else []}
        sources['donki_archive'] = {'role': 'уведомления DONKI (протонное событие, буря, прогноз прихода выброса)',
                                    # имя программного слоя стоит в '_layers'; в тексте статуса,
                                    # который читает аналитик, идентификаторов кода быть не должно (О5)
                                    'status': 'архив уведомлений DONKI %s — %s%s; разбор исходных тел сообщений, отбор по времени публикации' % (
                                        c0.strftime('%d.%m.%Y'), (c1 - timedelta(minutes=1)).strftime('%d.%m.%Y'),
                                        ', %d сообщений' % n_msg if n_msg else ''),
                                    'state': 'archive',
                                    'live_ok': None, 'from_cache': None, 'origin': 'архив A1 (%s)' % cat_src,
                                    'fetched_utc': None, 'data_utc': iso(t0), 'age_min': None,
                                    'events_used': len(events), 'excluded_by_cutoff': len(excluded),
                                    'record_ids': sorted({e.raw_record_id for e in events if e.raw_record_id})}
    sources.update({
        'ost1044_belts': {'role': 'захваченные протоны', 'status': '%s; файл %s, sha256 %s…; запись %s' % (
            belts.source, belts.file, belts.sha256[:12], belts.raw_record_id), 'live_ok': None, 'from_cache': None,
            'state': 'builtin', 'origin': 'таблица стандарта в репозитории', 'record_ids': [belts.raw_record_id]},
        'ecss_grun': {'role': 'природные метеороиды',
                      'status': 'ECSS Grün + сезонная модель 49 потоков C-2; направленная геометрия, '
                                'тень Земли, скорость МКС, исключение двойного учёта среднего вклада; '
                                'инженерная оценка с анализом чувствительности, не модель повреждения скафандра',
                      'live_ok': None, 'from_cache': None, 'state': 'builtin',
                      'origin': 'ECSS 2020 и явно объявленные гипотезы сезонной модели',
                      'record_ids': ['ecss_grun:grun-ecss-2020-v1', CATALOGUE_ID, METHOD_ID]},
        '_layers': {'role': 'слои', 'status': 'орбита: %s; источники: %s; история: %s' % (ORBIT_SRC, SRC_LAYER, HIST_SRC)},
    })
    for line in fc_lines:
        lr = line.last_release_before_cutoff
        sources['noaa_forecast_' + line.channel_id] = {
            'role': line.label_ru, 'status': FC_STATUS_RU.get(line.status, line.status) + (
                '; выпуск %s от %s' % (line.release_id, line.published_utc.strftime('%Y-%m-%d %H:%MZ')) if line.record_id_ok() else
                ('; ' + line.reason if line.reason else '')),
            'state': _state('noaa', f_noaa if mode == 'live' else None, has_data=line.record_id_ok()),
            'live_ok': f_noaa.ok if mode == 'live' else None, 'from_cache': f_noaa.from_cache if mode == 'live' else None,
            'origin': ('живой бюллетень NOAA SWPC' if mode == 'live' else 'архив A1 (data/source_registry_2024/noaa)' + (
                ', sha256 %s…' % (fc_raw.get(line.raw_record_id, {}).get('sha256') or '')[:12] if line.raw_record_id else '')),
            'fetched_utc': iso(f_noaa.fetched_utc) if mode == 'live' and f_noaa.fetched_utc else None,
            'data_utc': iso(line.published_utc) if mode == 'live' and line.published_utc else None,
            'coverage_fraction': line.coverage_fraction,
            'record_ids': [line.raw_record_id] if line.raw_record_id else [],
            'last_release_before_cutoff': lr}

    meta_dict = ({**{k: iso(v) for k, v in meta.__dict__.items()}} if meta else
                 {'source_id': None, 'method': None, 'frame': None, 'epoch_utc': None, 'coverage_from_utc': None,
                  'coverage_to_utc': None, 'created_utc': None, 'available_utc': None, 'fetched_utc': None,
                  'is_reconstruction': None, 'field_model': None})
    effective_config = {'thresholds': th.__dict__, 'thresholds_requested': (thresholds or Thresholds.from_settings()).__dict__,
                        'history': _cfg_section('history'), 'sources': _cfg_section('sources'), 'ui': _cfg_section('ui'),
                        'settings_path': settings_path(), 'mmod': {'area_m2': 1.0, 'm_min_g': 1e-3, 'solar_activity': 'min',
                        'model_id': MODEL_ID, 'integration_step_s': 10.0, 'annual_reference_year': 2024},
                        'robustness_grid': {'thr_nT': list(rob.grid[0]), 'e_min_MeV': list(rob.grid[1])}}
    S = {
        'schema_version': SCHEMA_VERSION, 'algorithm_version': ALGO_VERSION, 'computed_utc': now.isoformat(),
        'mode': MODE_RU[mode], 'mode_id': mode,
        'request': {'t0_utc': t0.isoformat(), 'duration_min': duration_min, 'search_min': search_min,
                    'windows': [w.start_utc.isoformat() for w in windows], 'window_offsets_min': list(window_offsets_min),
                    'thresholds': th.__dict__, 'disabled': disabled, 'cutoff_utc': cutoff_utc.isoformat() if cutoff_utc else None,
                    'T_months': T_months, 'scenario': scenario.__dict__ if is_sim else None},
        'effective_config': effective_config,
        'source_versions': source_versions,
        'numerical_integration': numerical_reports,
        'meteoroids': seasonal_results,
        'coverage_map': coverage_map,
        'trajectory_meta': {**meta_dict, 'orbit_module': ORBIT_SRC, 'status': orb.status_ru, 'strictness': orb.strictness,
                            'error': orb.error, 'n_points': len(traj), 'provenance': provenance_summary(orb.provenance),
                            'belt_coordinates': belt_coords, 'record_ids': list(orbit_ids),
                            'tle_text': tle_text if mode == 'live' else None,
                            'tle_url': _tle_url if mode == 'live' else None,
                            'tle_url_primary_failed': _tle_primary if mode == 'live' else None,
                            'tle_fetch_status': getattr(f_tle, 'status_ru', None) if mode == 'live' else None},
        'windows': [{'index': i + 1, 'start_utc': a.window.start_utc.isoformat(), 'duration_min': a.window.duration_min, 'mechanisms': [
            {'id': m.mechanism_id, 'mandatory': m.mandatory, 'coverage': m.coverage.value, 'needs_check': list(m.needs_check_reasons),
             'coverage_notes': list(m.coverage_notes),
             'coverage_fraction': m.coverage_fraction,
             'coverage_gaps': [{'reason': g.reason_ru, 'minutes': g.minutes} for g in m.coverage_gaps],
             'conditions': [{'kind': c.kind, 'severity': c.severity, 'text': c.text, 'event_ids': list(c.event_ids),
                             'interval_utc': [iso(x) for x in c.interval_utc], 'level': c.level_note,
                             'sources': list(c.sources_ru), 'simulated': c.is_simulated} for c in m.conditions],
             'factors': [{'name': f.name, 'value': f.value, 'unit': f.unit, 'kind': f.kind.value, 'presence': f.presence.value,
                          'coverage': f.coverage.value, 'records': list(f.record_ids), 'rule': f.rule_applied, 'limits': f.limits_note,
                          'horizon_utc': iso(f.horizon_utc)}
                         for f in m.factors]} for m in a.mechanisms]} for i, a in enumerate(assessments)],
        'recommendation': {'verdict': rec.verdict, 'rule': rec.rule_applied, 'reasons': list(rec.reasons), 'missing': list(rec.missing),
                           'preferred': rec.preferred.start_utc.isoformat() if rec.preferred else None,
                           'preferred_index': next((i + 1 for i, a in enumerate(assessments) if rec.preferred and a.window.start_utc == rec.preferred.start_utc), None),
                           'per_mechanism': rec.per_mechanism_comparison, 'tolerance_basis': rec.tolerance_basis,
                           # объявленная область вывода — рядом с вердиктом в снимке, а не только на экране:
                           # выгрузка и повтор обязаны нести её вместе с числами (решение владельца 19.09)
                           'scope': rec.scope_ru, 'scope_detail': rec.scope_detail_ru,
                           'scope_facts': [list(x) for x in rec.scope_facts]},
        'cards': [{k: (v.value if hasattr(v, 'value') else v) for k, v in c.__dict__.items()} for c in cards],
        'sources': sources,
        'forecasts': [{'channel': line.channel_id, 'label': line.label_ru, 'source_id': line.source_id, 'status': line.status,
                       'status_ru': FC_STATUS_RU.get(line.status, line.status), 'coverage_fraction': line.coverage_fraction,
                       'release_id': line.release_id, 'published_utc': iso(line.published_utc), 'record': line.raw_record_id,
                       'reason': line.reason, 'gaps': list(line.gaps), 'last_release_before_cutoff': line.last_release_before_cutoff,
                       'cells': [{'from': iso(s.valid_from_utc), 'to': iso(s.valid_to_utc), 'value': s.value} for s in line.samples]}
                      for line in fc_lines],
        # ряды наблюдений: происхождение «наблюдение», единица и запись — при каждом ряде (C3)
        'observations': observations,
        'coverage_declared': list(assessments[0].coverage_declared),
        'coverage_missing': list(coverage_missing_extra) + list(assessments[0].coverage_missing),
        # явный признак: опрашивалась ли линия уведомлений о событиях и почему нет (М2)
        'events_line': events_line,
        'policy_note': POLICY_NOTE,
        'is_simulated': is_sim,
        'history': {'goes_observations': [{k: iso(v) for k, v in asdict(s).items()} for s in goes_observations or []], 'provider': HIST_SRC if mode != 'live' else None, 'excluded_by_cutoff': excluded,
                    'excluded_by_archive': excluded_archive,
                    'catalog_coverage': ({'from_utc': iso(catalog[0]), 'to_utc': iso(catalog[1])} if catalog else None),
                    'events_used': [{'id': e.event_id, 'kind': e.kind_of_event, 'published_utc': iso(e.published_utc),
                                     'start_utc': iso(e.start_utc), 'simulated': e.is_simulated} for e in events],
                    # аудит адаптера A2: покрытие по каналам, версия адаптера, ограничения и метаданные
                    # ИМЕННО использованных записей — иначе фильтрация raw по событиям теряет доказательство (стык, п. 2)
                    'adapter_version': hist_meta.get('adapter_version'),
                    'coverage_map': coverage_map,
                    'limitations': hist_meta.get('limitations'),
                    'archive_access': hist_meta.get('archive_access'),
                    'source_versions': {sid: {rid: meta for rid, meta in recs.items() if rid in raw_records}
                                        for sid, recs in (hist_meta.get('source_versions') or {}).items()
                                        if any(rid in raw_records for rid in recs)},
                    'event_facts': {rid: f for rid, f in event_facts.items() if rid in raw_records and f}},
        'verification': verification,
        'robustness': {'stable': rob.stable, 'ranking_stable': rob.ranking_stable, 'diff_spread_min': rob.diff_spread_min,
                       'fluence_ratio_spread': rob.fluence_ratio_spread, 'tol_min': rob.tol_min, 'tol_ratio': rob.tol_ratio,
                       'saa_spread_min': rob.saa_spread_min, 'grid': rob.grid, 'pair': list(rob.pair),
                       'diff_by_thr': {'%.0f' % k: v for k, v in rob.diff_by_thr.items()},
                       'ratio_by_e': {'%g' % k: v for k, v in rob.ratio_by_e.items()},
                       'preferred_by_grid': {'%.0f nT / %g MeV' % k: v for k, v in rob.preferred_starts.items()},
                       'verdict_by_grid': {'%.0f nT / %g MeV' % k: v for k, v in (rob.verdict_by_grid or {}).items()},
                       'ranking_by_grid': {'%.0f nT / %g MeV' % k: v for k, v in rob.ranking_by_grid.items()}},
    }
    return Result(S, raw_records, traj, meta, assessments, rec, cards, events, rob, goes, kp, excluded,
                  {'goes': f_goes, 'kp': f_kp, 'tle': f_tle,
                   'noaa': f_noaa or _empty_fetch('noaa_swpc_3day_forecast', 'в историческом режиме живой прогноз не запрашивается')},
                  forecasts=fc_lines, orbit=orb, kp_obs=kp_obs,
                  cards_by_window=cards_by_window, verification=verification)
