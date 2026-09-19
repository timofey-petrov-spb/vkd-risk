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
import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from vkd.assess.cutoff import apply_cutoff
from vkd.assess.magcoords import belt_coordinates
from vkd.assess.meteoroids import meteoroid_hits_track
from vkd.assess.trapped import BeltTable
from vkd.config import section as _cfg_section, settings_path
from vkd.explain.cards import cards_for_window
from vkd.history import history_bundle
from vkd.integration.noaa_forecast import STATUS_RU as FC_STATUS_RU, live_forecast_lines, noaa_forecasts
from vkd.integration.orbit_bridge import ORBIT_SRC, TLE_URL_UNKNOWN, build_orbit, provenance_summary
from vkd.sources import Fetch, goes_latest, kp_latest, noaa_latest, tle_latest
from vkd.types import Request, SCHEMA_VERSION, Window
from vkd.windows.compare import Thresholds, action_span, assess_window, overlaps, recommend
from vkd.windows.scenario import Scenario, apply_to_windows, simulated_events, simulated_kp
from vkd.windows.sensitivity import robustness

SRC_LAYER = 'vkd.sources'       # A4: живой запрос → кеш → снимок репозитория, три состояния источника
HIST_SRC = 'vkd.history'        # A2: уведомления DONKI, архивы наблюдений GOES и Kp за 2024, выпуски NOAA

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALGO_VERSION = '0.6.0'      # 19.09, третий круг: конвейер на слоях А (vkd.sources, vkd.history) без заглушек; архив наблюдений GOES 2024
                            # в разборе; условия по структурированным фактам уведомлений (facts), R10 по опубликованному диапазону Kp
                            # уведомления о приходе выброса (линия enlilList снята); живой прогноз NOAA в текущем режиме; R11 — WGS84 → ECEF
                            # (0.5.1: конец действия записи по её собственному началу, окно вне архива — «оснований недостаточно»)
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
    if not (DURATION_MIN_RANGE[0] <= int(duration_min) <= DURATION_MIN_RANGE[1]):
        raise ValueError('длительность ВКД %s мин вне границ постановки %d…%d мин' % (duration_min, *DURATION_MIN_RANGE))
    if not (0 <= int(search_min) <= SEARCH_MIN_MAX):
        raise ValueError('период поиска начала %s мин вне границ постановки 0…%d мин' % (search_min, SEARCH_MIN_MAX))
    offs = list(window_offsets_min)
    if not (WINDOWS_RANGE[0] <= len(offs) <= WINDOWS_RANGE[1]):
        raise ValueError('окон для сравнения должно быть %d…%d, задано %d' % (*WINDOWS_RANGE, len(offs)))
    if len(set(offs)) != len(offs):
        raise ValueError('сдвиги окон должны различаться: одинаковые окна сравнивать бессмысленно (%s)' % offs)
    for o in offs:
        if not (0 <= int(o) <= int(search_min)):
            raise ValueError('сдвиг окна %s мин вне периода поиска 0…%s мин' % (o, search_min))
    if mode != 'live':
        # архив 2024 года ограничен: начало вне его границ — отказ с русским сообщением,
        # а не английское исключение адаптера истории где-то в середине расчёта
        a0, a1 = DONKI_ARCHIVE_DEFAULT
        if not (a0 <= t0 < a1):
            raise ValueError('начало периода %s вне архива исторических режимов %s — %s; выберите дату внутри архива'
                             % (t0.strftime('%Y-%m-%d %H:%MZ'), a0.strftime('%d.%m.%Y'),
                                (a1 - timedelta(minutes=1)).strftime('%d.%m.%Y')))


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


def _empty_fetch(source_id: str, why: str) -> Fetch:
    return Fetch(source_id, False, False, None, None, why, None, None)


def run(mode: str, t0: datetime, duration_min: int, search_min: int, window_offsets_min: list[int],
        disabled: Optional[dict] = None, thresholds: Optional[Thresholds] = None,
        scenario: Optional[Scenario] = None, T_months: int = 6,
        fetched: Optional[tuple] = None, now: Optional[datetime] = None,
        tle_override_path: Optional[str] = None) -> Result:
    """tle_override_path — воспроизведение сохранённого расчёта текущего режима: орбита
    строится по сохранённому TLE, а не по текущему (Т8). В исторических режимах орбита
    берётся из архива OEM (A1/A3) и от TLE не зависит; живые источники не вызываются."""
    validate_request(mode, t0, duration_min, search_min, window_offsets_min)
    disabled = disabled or {'goes': False, 'kp': False}
    th = thresholds or Thresholds.from_settings()
    scenario = scenario or Scenario('none')
    now = now or datetime.now(timezone.utc)
    ref_now = now if mode == 'live' else t0          # момент, от которого считаются давности: в истории — t0
    cutoff_utc = t0 if mode == 'history_forecast' else None
    horizon_min = search_min + duration_min
    is_sim = bool(scenario.work_delay_min or scenario.sep_onset_offset_min is not None or scenario.kp_override is not None)

    # ------------------------------------------------------------ источники
    f_noaa, noaa_raw, noaa_samples = None, {}, ()
    if mode == 'live':
        if fetched is None:
            fetched = (goes_latest(disabled=disabled['goes']), kp_latest(disabled=disabled['kp']), tle_latest(disabled=False))
        fetched = tuple(fetched)
        if len(fetched) == 3:
            # живой прогноз NOAA не передан экраном — берём его здесь (C6): те же каналы,
            # что и в истории (kp_forecast, s1_prob_daily), исходные 3-часовые и суточные ячейки
            fetched = fetched + (noaa_latest(disabled=disabled.get('noaa', False)),)
        (goes, goes_raw, f_goes), (kp, kp_raw, f_kp), (tle_text, f_tle), (noaa_samples, noaa_raw, f_noaa) = fetched
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
    events, hist_raw, excluded, fc_lines, fc_raw, forecasts, kp_obs, verification = [], {}, [], [], {}, [], [], None
    observations: list = []          # ряды наблюдений для ленты времени и выгрузки (C3)
    excluded_archive: list = []
    catalog = None
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
        kp_hist = [s for s in cut.samples if s.channel_id == 'kp' and s.t_utc <= t0]
        kp = max(kp_hist, key=lambda s: s.t_utc) if (kp_hist and disabled.get('kp') != 'off') else None
        kp_raw = {kp.raw_record_id: hist_raw.get(kp.raw_record_id)} if kp else {}
        # C3: численные наблюдения GOES ≥10 МэВ из архива NASA iSWA (A2). В строгом режиме адаптер
        # исключает их сам, а apply_cutoff исключает повторно: published_utc у выпуска нет.
        goes_hist = [s for s in cut.samples if s.channel_id == GOES_CHANNEL and s.value is not None and s.t_utc <= t0]
        goes = max(goes_hist, key=lambda s: s.t_utc) if (goes_hist and disabled.get('goes') != 'off') else None
        goes_raw = {goes.raw_record_id: hist_raw.get(goes.raw_record_id)} if goes else {}
        goes_cov_a2 = (hist_meta.get('coverage_map') or {}).get('goes_p_ge10MeV:observations') or {}
        if disabled.get('goes') == 'off':
            goes_src_note = 'источник исключён пользователем — данных нет'
            goes_absent_ru = 'архив наблюдений GOES исключён пользователем'
        elif goes is not None:
            goes_src_note = ('разбор: численный архив наблюдений GOES ≥10 МэВ (NASA iSWA, 5-минутные средние), запись %s; '
                             'временной охват горизонта %.2f %%' % (goes.raw_record_id, 100.0 * float(goes_cov_a2.get('coverage_fraction') or 0.0)))
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
                observations.append({
                    'channel': GOES_CHANNEL, 'label': 'GOES, протоны ≥10 МэВ — наблюдение',
                    'unit': _g_line[0].unit, 'source_id': _g_line[0].source_id,
                    'published_utc': (_g_line[0].published_utc.isoformat() if _g_line[0].published_utc else None),
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
        if mode == 'history_review':      # разбор: наблюдения Kp вокруг периода — контекст ленты, не вход строгого режима
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
            return overlaps(a0, a1, t0 - timedelta(hours=6), t0 + timedelta(minutes=horizon_min))
        events = [e for e in cut.events if _touches(e)]
        # прогнозы NOAA, выпущенные до отсечки (в разборе — до начала периода): A1/A2 через адаптер Б
        fc_lines, fc_raw = noaa_forecasts(t0, t0, t0 + timedelta(minutes=horizon_min))
        forecasts = [s for line in fc_lines for s in line.samples]
        if mode == 'history_forecast':
            # Проверка после отсечки: что наблюдалось потом — В РАСЧЁТ НЕ ВХОДИТ, только сопоставление (Т4, О6).
            # Строгий снимок по построению не содержит записей после отсечки, поэтому факт берётся
            # ОТДЕЛЬНЫМ разбором того же архива (history_review) — он нигде не смешивается с расчётом.
            h_end = t0 + timedelta(minutes=horizon_min)
            v_samples, v_events, _ = history_bundle(replace(hreq, mode='history_review', cutoff_utc=None))
            kp_after = sorted({(s.valid_from_utc, s.valid_to_utc, s.value, s.raw_record_id) for s in v_samples
                               if s.channel_id == 'kp' and s.valid_from_utc and s.valid_to_utc
                               and s.valid_to_utc > t0 and s.valid_from_utc < h_end})
            goes_after = sorted((s.t_utc, s.value, s.raw_record_id) for s in v_samples
                                if s.channel_id == GOES_CHANNEL and s.value is not None and t0 < s.t_utc <= h_end)
            ev_after = [e for e in v_events if e.published_utc and e.published_utc > cutoff_utc and e.published_utc <= h_end
                        and e.kind_of_event in ('SEP', 'GST')]
            first_storm = next(((a, v) for a, b, v, _ in kp_after if v >= th.kp_check), None)
            kp_max = max((v for _, _, v, _ in kp_after), default=None)
            summary = 'условие поставлено в %s; факт: ' % t0.strftime('%H:%MZ')
            summary += ('Kp %s с %s, максимум %s' % (('%.2f' % first_storm[1]).replace('.', ','), first_storm[0].strftime('%d.%m %H:%MZ'),
                                                     ('%.2f' % kp_max).replace('.', ',')) if first_storm
                        else ('максимум Kp %s, бури Kp ≥ %.0f не было' % (('%.2f' % kp_max).replace('.', ','), th.kp_check) if kp_max is not None
                              else 'наблюдений Kp в архиве (окончательный ряд GFZ, резерв — уведомления DONKI о буре) на горизонте нет'))
            if goes_after:
                g_max = max(goes_after, key=lambda x: x[1])
                summary += '; максимум наблюдённого потока GOES ≥10 МэВ %s pfu в %s' % (
                    ('%.6g' % g_max[1]).replace('.', ','), g_max[0].strftime('%d.%m %H:%MZ'))
            seps = [e for e in ev_after if e.kind_of_event == 'SEP']
            if seps:
                summary += '; протонное событие: первая публикация %s' % min(e.published_utc for e in seps).strftime('%d.%m %H:%MZ')
            verification = {
                # U5: имя программного слоя — отдельным ключом, чтобы оперативный уровень экрана
                # печатал только русский текст, а происхождение факта оставалось прослеживаемым
                'note': 'в расчёт не входит: наблюдения и публикации после отсечки, только для сопоставления прогноза '
                        'с фактом; источник факта — тот же архив, прочитанный в режиме исторического разбора',
                'source_layer': '%s, history_review' % HIST_SRC,
                'cutoff_utc': cutoff_utc.isoformat(), 'horizon_to_utc': h_end.isoformat(),
                'kp_obs': [{'from_utc': a.isoformat(), 'to_utc': b.isoformat(), 'kp': v, 'record': rid} for a, b, v, rid in kp_after],
                'goes_obs_max': ({'t_utc': max(goes_after, key=lambda x: x[1])[0].isoformat(),
                                  'value_pfu': max(goes_after, key=lambda x: x[1])[1],
                                  'record': max(goes_after, key=lambda x: x[1])[2],
                                  'n_samples': len(goes_after)} if goes_after else None),
                'events': [{'id': e.event_id, 'kind': e.kind_of_event, 'published_utc': e.published_utc.isoformat(),
                            'start_utc': e.start_utc.isoformat() if e.start_utc else None, 'note': (e.note or '')[:120]} for e in ev_after],
                'summary': summary,
            }
    if mode == 'live':
        # C6: живой трёхсуточный прогноз NOAA теми же линиями и каналами, что в истории —
        # условие «прогноз Kp ≥ kp_check» работает и в текущем режиме
        fc_lines, fc_raw = live_forecast_lines(noaa_samples, noaa_raw, f_noaa, t0, t0 + timedelta(minutes=horizon_min))
        forecasts = [s for line in fc_lines for s in line.samples]
    kp = simulated_kp(kp, t0, scenario)
    sim_events = simulated_events(t0, scenario)
    events = events + sim_events
    for e in sim_events:      # сценарий даёт те же структурированные факты, что и уведомление: правило одно
        if e.kind_of_event == 'SEP' and scenario.sep_level_pfu is not None:
            event_facts[e.raw_record_id] = {'detector': 'сценарий «что если»', 'energy_lower_bound_MeV': 10.0,
                                            'energy_operator': '>', 'flux_lower_bound_pfu': float(scenario.sep_level_pfu),
                                            'flux_operator': '=', 'measured_flux_pfu': float(scenario.sep_level_pfu)}

    # ------------------------------------------------------------ траектория (A3 через мост Б)
    if tle_override_path:
        tle_text = open(tle_override_path, encoding='utf-8').read()
        f_tle = replace(f_tle, status_ru='TLE из сохранённого расчёта (воспроизведение)', ok=False, from_cache=True)
    tle_fetched = getattr(f_tle, 'fetched_utc', None)
    # момент расчёта в текущем режиме — не раньше момента получения TLE: иначе A3 честно ставит
    # «реконструкция» рядом со статусом «строго» (Т2)
    live_cutoff = max(now, t0, tle_fetched) if tle_fetched else max(now, t0)
    orb = build_orbit(mode, t0, horizon_min, th.saa_B_threshold_nT, tle_text=tle_text,
                      tle_fetched_utc=tle_fetched, tle_available_utc=tle_fetched,
                      tle_url=(getattr(f_tle, 'url', None) or TLE_URL_UNKNOWN),
                      tle_evidence=getattr(f_tle, 'status_ru', ''), max_tle_age_days=th.tle_max_age_days,
                      cutoff_utc=(cutoff_utc if mode != 'live' else live_cutoff))
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

    # метеороиды по ECSS (B2 по спецификации A5): по фактической высоте трассы, концы окна включены
    def _mmod(w: Window):
        end = w.start_utc + timedelta(minutes=w.duration_min)
        pts = [p for p in traj if w.start_utc <= p.t_utc <= end]
        if len(pts) < 2:
            return None, 'расчёт невозможен: нет трассы окна', 0.0
        frac = (pts[-1].t_utc - pts[0].t_utc).total_seconds() / (60.0 * w.duration_min)
        try:
            r = meteoroid_hits_track([p.t_utc for p in pts], [p.alt_km for p in pts], 1.0, 1e-3)
            return r.N, r.rule, frac
        except ValueError as e:
            return None, 'вне области применимости ECSS: %s' % e, frac

    mmod = {w.start_utc: _mmod(w) for w in windows}      # один раз на окно

    def _assess(w, tr, th_i):
        return assess_window(w, tr, belts, goes, kp, [], th_i, ref_now, events=events, catalog_coverage=catalog,
                             mmod_hits=mmod[w.start_utc][0], mmod_rule=mmod[w.start_utc][1],
                             mmod_cov_fraction=mmod[w.start_utc][2], forecasts=forecasts,
                             trajectory_record_ids=orbit_ids, cutoff_utc=cutoff_utc,
                             event_facts=event_facts, goes_absent_ru=goes_absent_ru)

    def _assess_all(tr, kw):
        th_i = replace(th, **kw)
        return [_assess(w, tr, th_i) for w in windows]

    def _decide(A_i, kw):
        return recommend(A_i, replace(th, **kw))

    rob = robustness(traj, windows, _assess_all, _decide,
                     thr_grid=[th.saa_B_threshold_nT - 2000, th.saa_B_threshold_nT, th.saa_B_threshold_nT + 2000],
                     e_grid=[12.5, 30.0, 50.0], base_thr=th.saa_B_threshold_nT, base_e=th.e_min_MeV,
                     min_tol_min=1.0, min_tol_ratio=th.fluence_equiv_ratio)
    th = replace(th, equiv_tol_min=rob.tol_min, fluence_equiv_ratio=rob.tol_ratio)
    assessments = [_assess(w, traj, th) for w in windows]
    rec = recommend(assessments, th)
    thr_txt = '/'.join('%.0f' % x for x in rob.grid[0])
    def _pair_txt(pair):
        if not pair:
            return ''
        i_b = next(i + 1 for i, a in enumerate(assessments) if a.window.start_utc.isoformat() == pair[0])
        i_s = next(i + 1 for i, a in enumerate(assessments) if a.window.start_utc.isoformat() == pair[1])
        return ' между окнами %d и %d' % (i_b, i_s)
    d_txt = ('разность минут%s на сетке от %.0f до %.0f' % (_pair_txt(rob.pair), min(rob.diff_by_thr.values()), max(rob.diff_by_thr.values()))
             if rob.diff_by_thr else 'разность минут на сетке не определена (меньше двух окон без условий)')
    r_txt = ('отношение флюенсов%s по каналу от ×%.2f до ×%.2f' % (_pair_txt(rob.pair_fluence), min(rob.ratio_by_e.values()), max(rob.ratio_by_e.values()))
             if rob.ratio_by_e else 'отношение флюенсов не определено')
    rec = replace(rec, is_simulated=is_sim,
                  missing=rec.missing + (('орбита недоступна: %s' % orb.error,) if orb.error else ()),
                  tolerance_basis='допуск %.0f мин — разброс разности минут в аномалии между окнами при порогах %s нТл (%s); '
                                  'допуск ×%.2f по флюенсу — не меньше настройки ×%.2f и разброса по каналу %s МэВ (%s); '
                                  'порядок окон при нулевом допуске на сетке %s; %s' % (
                                      th.equiv_tol_min, thr_txt, d_txt, th.fluence_equiv_ratio,
                                      max(1.0, (thresholds or Thresholds.from_settings()).fluence_equiv_ratio),
                                      '/'.join('%g' % x for x in rob.grid[1]), r_txt,
                                      'сохраняется' if rob.ranking_stable else 'МЕНЯЕТСЯ',
                                      'выбор устойчив: на всей сетке одинаковы и вердикт, и предпочтительное окно'
                                      if rob.stable else
                                      'ВЫБОР МЕНЯЕТСЯ на сетке: вердикт или предпочтительное окно на ней не одни и те же'))
    samples = {**({goes.raw_record_id: goes} if goes else {}), **({kp.raw_record_id: kp} if kp else {}),
               **{s.raw_record_id: s for s in forecasts}}
    cards_by_window = {a.window.start_utc: cards_for_window(a, samples, events=events, window_index=i + 1, meta=meta,
                                                            trajectory_ids=orbit_ids, cutoff_utc=cutoff_utc)
                       for i, a in enumerate(assessments)}
    cards = [c for a in assessments for c in cards_by_window[a.window.start_utc]]

    # ------------------------------------------------------------ снимок
    iso = lambda v: v.isoformat() if hasattr(v, 'isoformat') else v
    ev_raw = {e.raw_record_id: hist_raw[e.raw_record_id] for e in events if e.raw_record_id in hist_raw}
    raw_records = {**(goes_raw or {}), **(kp_raw or {}), **ev_raw, **fc_raw,
                   'orbit_provenance': orb.provenance}
    if mode == 'live':
        raw_records['iss.tle'] = {'text': tle_text, 'epoch_utc': iso(meta.epoch_utc) if meta and meta.epoch_utc else None,
                                  'fetch': getattr(f_tle, 'status_ru', None), 'url': getattr(f_tle, 'url', None) or TLE_URL_UNKNOWN,
                                  'metadata': dict(getattr(f_tle, 'metadata', None) or {})}
        if noaa_raw:
            raw_records.update(noaa_raw)
        # точные байты ответа TLE и квитанция A4 — рядом с нормализованным текстом:
        # повтор текущего режима разбирает исходный ответ, а не наш пересказ (стык, п. 2)
        from vkd.sources.live_cache import raw_record as _raw_record
        raw_records.update(_raw_record(f_tle))

    def _src(f, role, sample=None):
        # давность — от КОНЦА интервала измерения (valid_to_utc), как в compare.py: у Kp GFZ
        # t_utc — начало трёхчасового интервала, и таблица источников расходилась с фактором на 3 ч
        ref_t = (sample.valid_to_utc or sample.t_utc) if sample else None
        return {'role': role, 'status': f.status_ru, 'live_ok': f.ok, 'from_cache': f.from_cache,
                'origin': ('живой запрос' if f.ok else ('кеш или снимок репозитория' if f.from_cache else 'данных нет')),
                'fetched_utc': iso(f.fetched_utc) if f.fetched_utc else None,
                'data_utc': iso(sample.t_utc) if sample else None,
                'age_min': round((ref_now - ref_t).total_seconds() / 60) if sample else f.age_min}

    orbit_src = {'role': 'орбита', 'status': orb.status_ru, 'strictness': orb.strictness,
                 'origin': ('живой запрос TLE' if mode == 'live' and getattr(f_tle, 'ok', None) else
                            ('кеш/снимок TLE' if mode == 'live' else 'архив OEM NASA/JSC (реестр A1)')),
                 'live_ok': f_tle.ok if mode == 'live' else None, 'from_cache': f_tle.from_cache if mode == 'live' else None,
                 'fetched_utc': iso(meta.fetched_utc) if meta else None,
                 'epoch_utc': iso(meta.epoch_utc) if meta and meta.epoch_utc else None,
                 'age_h': round((now - meta.epoch_utc).total_seconds() / 3600, 1) if meta and meta.epoch_utc else None,
                 'source_id': meta.source_id if meta else None}
    sources = {'orbit': orbit_src}
    if mode == 'live':
        sources['noaa_swpc_goes'] = _src(f_goes, 'протоны ≥10 МэВ', goes)
        sources['gfz_kp'] = _src(f_kp, 'Kp', kp if (kp and kp.source_id != 'scenario') else None)
        sources['noaa_swpc_3day_forecast'] = {
            **_src(f_noaa, 'прогноз Kp и вероятности NOAA (3 суток)'),
            'data_utc': iso(min((s.published_utc for s in noaa_samples if s.published_utc), default=None)),
            'cells': len(noaa_samples)}
    else:
        c0, c1, n_msg, cat_src = _donki_catalog_coverage()
        goes_origin = ('архив наблюдений NASA iSWA (data/goes_2024), 5-минутные средние' if goes is not None
                       else ('исключён строгим режимом' if mode == 'history_forecast' else 'нет данных'))
        sources['noaa_swpc_goes'] = {'role': 'протоны ≥10 МэВ',
                                     'status': goes_src_note or 'канал без данных',
                                     'live_ok': None, 'from_cache': None, 'origin': goes_origin, 'fetched_utc': None,
                                     'data_utc': iso(goes.t_utc) if goes is not None else None,
                                     # давность GOES — от МОМЕНТА наблюдения (начала 5-минутного усреднения),
                                     # как её считает фактор в compare.py; иначе таблица и фактор разойдутся
                                     'age_min': round((t0 - goes.t_utc).total_seconds() / 60) if goes is not None else None,
                                     'coverage_fraction': (hist_meta.get('coverage_map') or {}).get(
                                         'goes_p_ge10MeV:observations', {}).get('coverage_fraction')}
        kp_origin = {'gfz_kp_archive': 'архив GFZ (окончательный ряд Kp по 3-часовым интервалам, без времени публикации)',
                     'nasa_donki_notification': 'уведомление DONKI о буре (наблюдённый Kp с временем публикации)',
                     'nasa_donki_gst': 'архив DONKI (карточки GST)'}.get(kp.source_id if kp else '', 'архив (%s)' % kp.source_id if kp else 'нет данных')
        sources['gfz_kp'] = {'role': 'Kp (в разборе — окончательный ряд GFZ; в строгом режиме — уведомления DONKI о буре)',
                             'status': (kp_src_note or 'разбор: Kp из %s, запись %s' % (kp_origin.split(' (')[0], kp.raw_record_id)) if not (kp and kp.source_id == 'scenario')
                             else 'сценарий «что если»: моделируемое значение',
                             'live_ok': None, 'from_cache': None,
                             'origin': kp_origin if kp and kp.source_id != 'scenario' else ('сценарий' if kp else 'нет данных'),
                             'fetched_utc': None,
                             'data_utc': iso(kp.t_utc) if kp and kp.source_id != 'scenario' else None,
                             'age_min': round((t0 - (kp.valid_to_utc or kp.t_utc)).total_seconds() / 60)
                             if kp and kp.source_id != 'scenario' else None}
        sources['donki_archive'] = {'role': 'уведомления DONKI (протонное событие, буря, прогноз прихода выброса)',
                                    # имя программного слоя стоит в '_layers'; в тексте статуса,
                                    # который читает аналитик, идентификаторов кода быть не должно (О5)
                                    'status': 'архив уведомлений DONKI %s — %s%s; разбор исходных тел сообщений, отбор по времени публикации' % (
                                        c0.strftime('%d.%m.%Y'), (c1 - timedelta(minutes=1)).strftime('%d.%m.%Y'),
                                        ', %d сообщений' % n_msg if n_msg else ''),
                                    'live_ok': None, 'from_cache': None, 'origin': 'архив A1 (%s)' % cat_src,
                                    'fetched_utc': None, 'data_utc': iso(t0), 'age_min': None,
                                    'events_used': len(events), 'excluded_by_cutoff': len(excluded)}
    sources.update({
        'ost1044_belts': {'role': 'захваченные протоны', 'status': '%s; файл %s, sha256 %s…; запись %s' % (
            belts.source, belts.file, belts.sha256[:12], belts.raw_record_id), 'live_ok': None, 'from_cache': None,
            'origin': 'таблица стандарта в репозитории'},
        'ecss_grun': {'role': 'метеороиды', 'status': 'ECSS-E-ST-10-04C Rev.1: Grün (10-1), Table J-6 по высоте трассы, '
                                                      'интеграл по dt (спецификация A5, grun-ecss-2020-v1); потоки даты — только признак '
                                                      'по календарю IMO; контроль 5,609728e-7 на 400 км/1 м²/6 ч воспроизведён',
                      'live_ok': None, 'from_cache': None, 'origin': 'модель стандарта в репозитории'},
        '_layers': {'role': 'слои', 'status': 'орбита: %s; источники: %s; история: %s' % (ORBIT_SRC, SRC_LAYER, HIST_SRC)},
    })
    for line in fc_lines:
        lr = line.last_release_before_cutoff
        sources['noaa_forecast_' + line.channel_id] = {
            'role': line.label_ru, 'status': FC_STATUS_RU.get(line.status, line.status) + (
                '; выпуск %s от %s' % (line.release_id, line.published_utc.strftime('%Y-%m-%d %H:%MZ')) if line.record_id_ok() else
                ('; ' + line.reason if line.reason else '')),
            'live_ok': f_noaa.ok if mode == 'live' else None, 'from_cache': f_noaa.from_cache if mode == 'live' else None,
            'origin': ('живой бюллетень NOAA SWPC' if mode == 'live' else 'архив A1 (data/source_registry_2024/noaa)' + (
                ', sha256 %s…' % (fc_raw.get(line.raw_record_id, {}).get('sha256') or '')[:12] if line.raw_record_id else '')),
            'fetched_utc': iso(f_noaa.fetched_utc) if mode == 'live' and f_noaa.fetched_utc else None,
            'data_utc': iso(line.published_utc) if mode == 'live' and line.published_utc else None,
            'coverage_fraction': line.coverage_fraction,
            'last_release_before_cutoff': lr}

    meta_dict = ({**{k: iso(v) for k, v in meta.__dict__.items()}} if meta else
                 {'source_id': None, 'method': None, 'frame': None, 'epoch_utc': None, 'coverage_from_utc': None,
                  'coverage_to_utc': None, 'created_utc': None, 'available_utc': None, 'fetched_utc': None,
                  'is_reconstruction': None, 'field_model': None})
    effective_config = {'thresholds': th.__dict__, 'thresholds_requested': (thresholds or Thresholds.from_settings()).__dict__,
                        'history': _cfg_section('history'), 'sources': _cfg_section('sources'), 'ui': _cfg_section('ui'),
                        'settings_path': settings_path(), 'mmod': {'area_m2': 1.0, 'm_min_g': 1e-3, 'solar_activity': 'min'},
                        'robustness_grid': {'thr_nT': list(rob.grid[0]), 'e_min_MeV': list(rob.grid[1])}}
    S = {
        'schema_version': SCHEMA_VERSION, 'algorithm_version': ALGO_VERSION, 'computed_utc': now.isoformat(),
        'mode': MODE_RU[mode], 'mode_id': mode,
        'request': {'t0_utc': t0.isoformat(), 'duration_min': duration_min, 'search_min': search_min,
                    'windows': [w.start_utc.isoformat() for w in windows], 'window_offsets_min': list(window_offsets_min),
                    'thresholds': th.__dict__, 'disabled': disabled, 'cutoff_utc': cutoff_utc.isoformat() if cutoff_utc else None,
                    'T_months': T_months, 'scenario': scenario.__dict__ if is_sim else None},
        'effective_config': effective_config,
        'trajectory_meta': {**meta_dict, 'orbit_module': ORBIT_SRC, 'status': orb.status_ru, 'strictness': orb.strictness,
                            'error': orb.error, 'n_points': len(traj), 'provenance': provenance_summary(orb.provenance),
                            'belt_coordinates': belt_coords, 'record_ids': list(orbit_ids),
                            'tle_text': tle_text if mode == 'live' else None,
                            'tle_fetch_status': getattr(f_tle, 'status_ru', None) if mode == 'live' else None},
        'windows': [{'index': i + 1, 'start_utc': a.window.start_utc.isoformat(), 'duration_min': a.window.duration_min, 'mechanisms': [
            {'id': m.mechanism_id, 'mandatory': m.mandatory, 'coverage': m.coverage.value, 'needs_check': list(m.needs_check_reasons),
             'coverage_notes': list(m.coverage_notes),
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
                           'per_mechanism': rec.per_mechanism_comparison, 'tolerance_basis': rec.tolerance_basis},
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
        'coverage_declared': list(assessments[0].coverage_declared), 'coverage_missing': list(assessments[0].coverage_missing),
        'policy_note': POLICY_NOTE,
        'is_simulated': is_sim,
        'history': {'provider': HIST_SRC if mode != 'live' else None, 'excluded_by_cutoff': excluded,
                    'excluded_by_archive': excluded_archive,
                    'catalog_coverage': ({'from_utc': iso(catalog[0]), 'to_utc': iso(catalog[1])} if catalog else None),
                    'events_used': [{'id': e.event_id, 'kind': e.kind_of_event, 'published_utc': iso(e.published_utc),
                                     'start_utc': iso(e.start_utc), 'simulated': e.is_simulated} for e in events],
                    # аудит адаптера A2: покрытие по каналам, версия адаптера, ограничения и метаданные
                    # ИМЕННО использованных записей — иначе фильтрация raw по событиям теряет доказательство (стык, п. 2)
                    'adapter_version': hist_meta.get('adapter_version'),
                    'coverage_map': hist_meta.get('coverage_map'),
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
