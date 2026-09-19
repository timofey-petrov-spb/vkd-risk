# -*- coding: utf-8 -*-
"""Экран сервиса «ВКД-Риск» — один путь пользователя (О5): запрос в боковой панели (пресеты, режим,
когда, окно работ, источники) → приборная полоса состояния → ответ первым (вердикт) → окна-кандидаты
карточками → лента времени → вкладки: Объяснения, Окна и факторы, Методика, Карта, Наблюдения
и прогнозы, Данные (и Устойчивость и нормы на профессиональном уровне).

Экран ничего не считает: всё берётся из одного снимка расчёта app.compute.run (Т7, Т8).
Новые ключи снимка читаются через .get: их отсутствие не должно ронять экран (стык третьего круга).
Два уровня: «Оперативный» показывает решение и условия, «Профессиональный» — все величины,
пороги, устойчивость, нормы и происхождение записей. На экране нет идентификаторов кода:
методы, типы событий, источники и ограничения модулей переводятся словарями app.ui.

Оформление: монотонная академическая система (app/ui.py) — цвет означает происхождение величины,
градиентов и эмодзи нет; на главном экране не больше двух графиков, панель инструментов Plotly скрыта.
"""
from __future__ import annotations

import json
import logging
import traceback
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import streamlit as st

from app import globe
from app.compute import ALGO_VERSION, HIST_SRC, ORBIT_SRC, SRC_LAYER, run, validate_request
from app.export import _git_sha, build_zip
from app.fetch_guard import LIMIT_MARK, fetch_live_sources, total_deadline_s
from app.norms import norms_rows, s_level
from app.obs import forecast_panel, observations_figure, observations_panel
from app.ui import (BOOL_RU, COLOR_LEGEND, COV_RU, CSS, DISABLED_KEY, MECH_RU, METHOD_BLOCKS, METHOD_RU, PRESETS,
                    RELEASE_BY_MODE_RU, RULE_POLICY, RULE_THRESHOLDS, SEV_RU, STRICT_RU, VERDICT_TITLE,
                    age_ru, close_cut_parens, coverage_reasons, coverage_rows_ru, coverage_scope_ru, dedup_clauses,
                    dt_ru, event_kind_ru, excl_group_ru, excl_reason_ru, factor_value_ru, fmt, formula_ref, frac_ru,
                    grid_cell_ru, head, kind_pill, limit_ru, nbsp_thousands, panel, pill, plural_ru, ratio_ru,
                    raw_record, record_no_url_ru, record_release_ru, record_url, registry_row, robustness_gain_ru,
                    screen_text, short_reason, source_issues, source_name_ru, source_short, spread_offsets,
                    status_ru, tle_origin, verdict_panel, verification_ru, window_card)
from app.viz import PLOTLY_CONFIG, ground_track, timeline
from vkd.config import section as _settings_section
from vkd.explain.cards import KIND_RU
from vkd.explain.format import record_ru, source_ru
from vkd.windows.compare import Thresholds
from vkd.windows.scenario import Scenario

UI = _settings_section('ui')          # умолчания элементов управления — config/settings.toml (Т7)
SRC_CFG = _settings_section('sources')
TOTAL_DEADLINE_S = total_deadline_s()   # общий предел получения живых источников, с (Т7; проверяется при запуске)
MODE_IDS = {'Текущая обстановка': 'live', 'Исторический разбор': 'history_review', 'Прогноз из прошлого': 'history_forecast'}
# В «Историческом разборе» отсечки нет вовсе, и слово «отсечка» на его экране быть не должно
# даже в отрицании: жюри читает подписи по отдельности (R4-4).
MODE_SUB = {'live': 'живые источники', 'history_review': 'весь архив, без ограничения по времени публикации',
            'history_forecast': 'только публикации до отсечки'}
ARCHIVE_FROM, ARCHIVE_TO = datetime(2024, 5, 1, tzinfo=timezone.utc), datetime(2024, 7, 1, tzinfo=timezone.utc)
LOG = logging.getLogger('vkd.app')

st.set_page_config(page_title='ВКД-Риск', layout='wide', initial_sidebar_state='expanded')
st.markdown(CSS, unsafe_allow_html=True)
st.session_state.setdefault('fetch_nonce', 0)     # Т6: обновление данных — сессионное, не st.cache_data.clear()


def _apply_preset(p: dict) -> None:
    """Пресет запроса (И1): выставляет режим, дату, час и окна одним нажатием и ничего не считает.
    Состояние ползунков меняется ДО их создания в этом же прогоне, поэтому значения применяются сразу."""
    st.session_state['mode'] = p['mode']
    if p['date'] is not None:
        st.session_state['hist_date'] = datetime(*p['date']).date()
        st.session_state['hist_hour'] = int(p['hour'])
    st.session_state['duration'] = int(p['duration_min'])
    st.session_state['search'] = int(p['search_min'])
    st.session_state['_search_prev'] = int(p['search_min'])       # период задан пресетом, пересчёта сдвигов не нужно
    st.session_state['n_windows'] = len(p['offsets_min'])
    for i, off in enumerate(p['offsets_min']):
        st.session_state['w%d' % i] = int(off)
        st.session_state['_off%d' % i] = int(off)                 # R4-1: сдвиги хранятся вне виджета
    st.session_state['_preset'] = p['key']


# ================================================================= боковая панель: запрос
with st.sidebar:
    st.markdown('### ВКД-Риск')
    st.markdown('<div class="sect">Пресеты запроса</div>', unsafe_allow_html=True)
    for _p, _col in zip(PRESETS, st.columns(len(PRESETS))):
        if _col.button(_p['label'].split(',')[0], key='preset_' + _p['key'], width='stretch', help=_p['label']):
            _apply_preset(_p)
    _cur = next((p for p in PRESETS if p['key'] == st.session_state.get('_preset')), None)
    st.caption(('%s — %s' % (_cur['label'], _cur['shows'])) if _cur else
               'Одна кнопка выставляет режим, дату, час и окна; расчёт запускается обычным путём.')
    level = st.radio('Уровень интерфейса', ['Оперативный', 'Профессиональный'], index=0, horizontal=True, key='level',
                     help='Оперативный — решение и условия; профессиональный — все величины, пороги, устойчивость, происхождение.')
    pro = level == 'Профессиональный'
    # умолчание задаётся только при первом показе: у элемента с сохранённым значением Streamlit
    # предупреждает и всё равно берёт сохранённое (пресет пишет состояние до создания элементов)
    _def = lambda key, kw: ({} if key in st.session_state else kw)
    mode_ru = st.radio('Режим', list(MODE_IDS), key='mode', **_def('mode', {'index': 0}),
                       help='Текущая обстановка — живые источники. Исторический разбор — весь архив мая–июня 2024. '
                            'Прогноз из прошлого — только записи, опубликованные до отсечки.')
    mode = MODE_IDS[mode_ru]
    if mode == 'live':
        t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        st.caption('Начало периода поиска: **%s UTC** (сейчас)' % t0.strftime('%d.%m.%Y %H:%M'))
    else:
        d = st.date_input('Дата (1 мая — 30 июня 2024)', key='hist_date',
                          **_def('hist_date', {'value': datetime(2024, 5, 10).date()}),
                          min_value=ARCHIVE_FROM.date(), max_value=datetime(2024, 6, 30).date())
        hh = st.slider('Час начала периода, UTC', 0, 23, key='hist_hour', **_def('hist_hour', {'value': 12}))
        t0 = datetime(d.year, d.month, d.day, hh, tzinfo=timezone.utc)
        if mode == 'history_forecast':
            st.caption('Отсечка публикации: **%s UTC** — позже ничего не используется.' % t0.strftime('%d.%m.%Y %H:%M'))
    st.markdown('**Окно работ**')
    duration_min = st.slider('Длительность ВКД, мин', 60, 480, step=30, key='duration',
                             **_def('duration', {'value': int(UI.get('duration_min', 360))}),
                             help='Постановка: 60…480 мин. Сокращение длительности — изменение плана, не улучшение обстановки.')
    search_min = st.slider('Период поиска начала ВКД, мин', 60, 1440, step=60, key='search',
                           **_def('search', {'value': max(60, int(UI.get('search_min', 720)))}),
                           help='В этом периоде размещаются начала окон-кандидатов; постановка — до 1440 мин (сутки).')
    n_windows = st.radio('Окон для сравнения', [2, 3], horizontal=True, key='n_windows')
    OFF_STEP = 30
    _def_off = list(UI.get('window_offsets_min', [0, 240]))
    # R4-1: сдвиги живут в невиджетных ключах `_off<i>`, а не только в состоянии ползунка.
    # У ползунка меняется max_value вместе с периодом поиска — Streamlit считает его НОВЫМ виджетом
    # и теряет сохранённое значение, поэтому при любом движении «Периода поиска» оба сдвига уходили
    # в 0 и настройка окон пропадала. Порядок каждого прогона: взять сдвиги из `_off<i>` (или из
    # ползунка, если период не менялся и пользователь только что его двинул), ограничить периодом,
    # развести совпавшие, записать обратно и отдать ползункам ДО их создания.
    _search_prev = st.session_state.get('_search_prev')
    _period_changed = _search_prev is not None and int(_search_prev) != int(search_min)
    want_off = []
    for i in range(n_windows):
        v = st.session_state.get('_off%d' % i)
        if not _period_changed and st.session_state.get('w%d' % i) is not None:
            v = st.session_state['w%d' % i]            # пользователь двинул ползунок в этом прогоне
        if v is None:
            v = int(_def_off[i]) if i < len(_def_off) else i * 240
        want_off.append(int(v))
    offsets_state = [max(0, min(v, search_min)) for v in want_off]
    if _period_changed and len(set(offsets_state)) != len(offsets_state):
        offsets_state = spread_offsets(offsets_state, search_min, OFF_STEP)
    recalc = list(offsets_state) if offsets_state != want_off else []
    for i, v in enumerate(offsets_state):
        st.session_state['_off%d' % i] = int(v)
        st.session_state['w%d' % i] = int(v)
    offsets_in = []
    for i in range(n_windows):
        offsets_in.append(st.slider('Сдвиг начала окна %d, мин после начала периода' % (i + 1), 0, search_min,
                                    step=30, key='w%d' % i))
    for i, v in enumerate(offsets_in):
        st.session_state['_off%d' % i] = int(v)
    if recalc:
        st.caption('сдвиги пересчитаны под период: %s' % ', '.join('окно %d — %d мин' % (i + 1, v) for i, v in enumerate(recalc)))
    st.session_state['_search_prev'] = search_min
    # окна нумеруются по времени начала; одинаковые сдвиги разводим на шаг, экран не останавливаем (U1)
    offsets = sorted(offsets_in)
    if offsets != offsets_in:
        st.caption('Окна пронумерованы по времени начала: окно 1 — самое раннее.')
    dup = sorted({o for o in offsets if offsets.count(o) > 1})
    if dup:
        offsets = spread_offsets(offsets, search_min, OFF_STEP)
        st.warning('Окна с одинаковым началом (сдвиг %s мин) сравнивать нечем: считаю по сдвигам %s мин. '
                   'Поставьте ползунки на нужные начала.'
                   % (', '.join(str(o) for o in dup), ', '.join(str(o) for o in offsets)))
    if mode == 'live':
        with st.expander('Источники и обновление', expanded=False):
            _SRC_STATE = {'включён': False, 'отказ: только кеш': 'cache', 'исключён: нет данных': 'off'}
            _SRC_LABEL = {'goes': 'GOES, протоны ≥10 МэВ', 'kp': 'Kp (GFZ)',
                          'noaa': 'Прогноз NOAA на трое суток'}     # живой бюллетень: те же три состояния (C6)
            disabled = {s: _SRC_STATE[st.selectbox(_SRC_LABEL[s], list(_SRC_STATE), key='dis_' + s,
                                                   help='«отказ» — живого запроса нет, берётся кеш с давностью (покрытие частичное); '
                                                        '«исключён» — данных нет, обязательная линия без покрытия → рекомендации нет')]
                        for s in ('goes', 'kp', 'noaa')}
            if st.button('Обновить данные сейчас', key='refresh', help='Повторный живой запрос GOES, Kp и TLE для этой сессии.'):
                st.session_state['fetch_nonce'] += 1
            _auto_def = int(UI.get('auto_refresh_min', 5))
            auto_min = st.select_slider('Автообновление, мин', [0, 5, 10, 15], value=_auto_def if _auto_def in (0, 5, 10, 15) else 5,
                                        key='auto_min', help='0 — выключено. Частота публикации: GOES 5 мин, Kp 3 ч, TLE по мере выпуска.')
        kp_off_hist = False
    else:
        if pro:
            # «выпуски NOAA до отсечки» верно только в строгом режиме: в разборе отсечки нет вовсе (R4-4)
            st.caption('Источники режима: архив уведомлений DONKI за 01.05–30.06.2024, орбита OEM NASA/JSC, выпуски NOAA %s; '
                       'Kp — окончательный ряд GFZ по 3-часовым интервалам%s. Живые наблюдения GOES и Kp в этом режиме не запрашиваются.'
                       % ('до отсечки' if mode == 'history_forecast' else 'из архива',
                          ' (в разборе; в строгом режиме исключён: времени публикации по интервалам нет)' if mode == 'history_forecast'
                          else ' (разбор после факта)'))
        else:
            st.caption('Источники режима — архив: уведомления DONKI, орбита OEM NASA/JSC, выпуски NOAA%s.'
                       % (' до отсечки' if mode == 'history_forecast' else ' и ряд Kp GFZ'))
        kp_off_hist = st.checkbox('Исключить Kp из архива (проверка отказа)', key='kp_off_hist',
                                  help='Проверка поведения при отказе источника: наблюдение Kp не используется, покрытие объявляется.')
        disabled = {'goes': False, 'kp': 'off' if kp_off_hist else False}
        auto_min = 0
    with st.expander('Что если — стресс-сценарий', expanded=False):
        sc_delay = st.slider('Задержка начала работ, мин', 0, 180, 0, step=15, key='sc_delay',
                             help='Все окна сдвигаются на задержку; показывается как изменение плана.')
        sc_sep_on = st.checkbox('Смоделировать протонное событие', key='sc_sep_on')
        sc_sep_off = st.slider('Начало протонного события, мин после начала периода', 0, 1440, 120, step=30, disabled=not sc_sep_on, key='sc_sep_off')
        sc_sep_pfu = st.select_slider('Уровень события, pfu (≥10 МэВ)', [10.0, 100.0, 1000.0, 10000.0], value=100.0, disabled=not sc_sep_on, key='sc_sep_pfu')
        sc_kp_on = st.checkbox('Смоделировать скачок Kp', key='sc_kp_on')
        sc_kp = st.slider('Kp при скачке', 0.0, 9.0, 7.0, step=0.33, disabled=not sc_kp_on, key='sc_kp')
    scenario = Scenario('ui', work_delay_min=sc_delay, sep_onset_offset_min=(sc_sep_off if sc_sep_on else None),
                        sep_level_pfu=(sc_sep_pfu if sc_sep_on else None), kp_override=(sc_kp if sc_kp_on else None))
    TH0 = Thresholds.from_settings()          # config/settings.toml — настройки вне кода (Т7)
    if pro:
        with st.expander('Пороги и настройки', expanded=False):
            st.caption('Умолчания — из config/settings.toml; всё применённое попадает в снимок и выгрузку.')
            th = replace(TH0,
                         saa_B_threshold_nT=st.number_input('Порог аномалии |B|, нТл', 18000.0, 30000.0, TH0.saa_B_threshold_nT, 500.0, key='th_B'),
                         e_min_MeV=st.selectbox('Канал захваченных протонов, МэВ от', [12.5, 30.0, 50.0], key='th_E',
                                                index=[12.5, 30.0, 50.0].index(TH0.e_min_MeV) if TH0.e_min_MeV in (12.5, 30.0, 50.0) else 1),
                         goes_max_age_min=st.number_input('Допустимая давность наблюдения GOES, мин', 10.0, 360.0, TH0.goes_max_age_min, 10.0, key='th_age',
                                                          help='Старше — для будущих участков окна покрытие частичное.'),
                         kp_check=st.number_input('Порог Kp для условия проверки', 5.0, 9.0, TH0.kp_check, 0.5, key='th_kp',
                                                  help='Применяется к наблюдению, уведомлениям о буре и прогнозам NOAA/ENLIL.'),
                         tle_max_age_days=st.number_input('Допустимый возраст TLE, сут', 1.0, 14.0, TH0.tle_max_age_days, 1.0, key='th_tle'))
            T_months = st.slider('Длительность экспедиции для норм, мес', 1, 12, 6, key='T_months')
    else:
        th, T_months = TH0, 6

# автообновление: фрагмент перезапускает расчёт по таймеру (Т1), обновляя только эту сессию (Т6).
if mode == 'live' and auto_min:
    @st.fragment(run_every=timedelta(minutes=auto_min))
    def _auto_refresh():
        last = st.session_state.get('_auto_last')
        nowr = datetime.now(timezone.utc)
        if last is not None and (nowr - last) >= timedelta(minutes=auto_min) - timedelta(seconds=5):
            st.session_state['fetch_nonce'] += 1
            st.session_state['_auto_last'] = nowr
            st.rerun()
        if last is None:
            st.session_state['_auto_last'] = nowr
        st.sidebar.caption('Автообновление каждые %d мин; последнее: %s UTC' % (auto_min, st.session_state['_auto_last'].strftime('%H:%M:%S')))
    _auto_refresh()


# ================================================================= расчёт: один снимок на рендер
@st.cache_data(ttl=300, show_spinner=False)
def _fetch_all(dis_goes, dis_kp, dis_noaa, nonce: int):
    """Кеш 5 мин против повторных запросов при каждом движении ползунка; nonce — счётчик обновления сессии.
    Четыре источника текущего режима: GOES, Kp, TLE и живой трёхсуточный бюллетень NOAA (C6).

    Получение идёт через app.fetch_guard: у всех четырёх ОДИН общий предел ([sources].total_deadline_s).
    Без него первый рендер на площадке с ограниченным исходящим доступом может не наступить вовсе —
    зависание на разрешении имени тайм-аутами requests не покрывается. По истечении предела
    возвращается тот же кортеж, собранный без сети (кеш → снимок репозитория → «данных нет»),
    и названная причина; кеш этой функции держит её TTL, то есть повтор не чаще раза в 5 мин.
    Возвращает (кортеж источников, причина отказа по общему пределу или None)."""
    return fetch_live_sources({'goes': dis_goes, 'kp': dis_kp, 'noaa': dis_noaa})


horizon_min = search_min + duration_min
# R4-2: предупреждение о границе архива включается по ФАКТИЧЕСКИМ окнам, а не по всему периоду
# поиска. Покрытие считается по окнам, и при сдвигах меньше периода сверху стояло «рекомендации
# не будет», а вердикт тут же называл предпочтительное окно — два ответа об одном на одном экране.
windows_end = t0 + timedelta(minutes=(max(offsets) if offsets else 0) + duration_min)
windows_beyond_archive = mode != 'live' and windows_end > ARCHIVE_TO
if windows_beyond_archive:
    st.warning('Последнее окно кончается %s UTC — за границей архива 30.06.2024. Покрытие обязательной линии за пределами '
               'архива отсутствует, рекомендации не будет. Сократите сдвиг окна или длительность либо выберите более '
               'раннюю дату.' % windows_end.strftime('%d.%m.%Y %H:%M'))
try:                              # границы постановки проверяются до любого запроса (Т7): сообщение зрителю, расчёта нет
    validate_request(mode, t0, duration_min, search_min, offsets)
except ValueError as e:
    st.error('Запрос вне границ постановки: %s. Измените запрос в боковой панели.' % e)
    st.stop()
try:
    if mode == 'live':
        with st.spinner('Источники: GOES, Kp, TLE, бюллетень NOAA — до %s с на адрес и не дольше %s с на все вместе; '
                        'дальше кеш и снимок репозитория…' % (fmt(SRC_CFG.get('timeout_s', 6)), fmt(TOTAL_DEADLINE_S))):
            fetched, fetch_note = _fetch_all(disabled['goes'], disabled['kp'], disabled['noaa'],
                                             int(st.session_state['fetch_nonce']))
    else:
        fetched, fetch_note = None, None   # архивные режимы: живые источники не запрашиваются вовсе — входы только из архива (Т1, Т6)
    with st.spinner('Траектория, поле, оценка окон, устойчивость…'):
        R = run(mode, t0, duration_min, search_min, offsets, disabled=disabled, thresholds=th,
                scenario=scenario, T_months=T_months, fetched=fetched, fetch_note=fetch_note)
except Exception as e:            # noqa: BLE001 — экран не падает; подробности в лог, не зрителю (Т6, Т7)
    LOG.error('расчёт не выполнен: %s\n%s', e, traceback.format_exc())
    st.error('Расчёт не выполнен: %s. Измените запрос или повторите позже; подробности записаны в журнал сервера.' % type(e).__name__)
    st.stop()
S, rec, meta, traj, rob = R.S, R.rec, R.meta, R.traj, R.rob
now = datetime.fromisoformat(S['computed_utc'])
windows = [a.window for a in R.assessments]
windows_ru = {a.window.start_utc: str(i + 1) for i, a in enumerate(R.assessments)}

# изменение плана: сокращение длительности — изменение плана, а не улучшение обстановки (постановка; O3-4)
_plan_prev = st.session_state.get('_plan_duration')
if _plan_prev is not None and _plan_prev != duration_min:
    st.session_state['_plan_change'] = (_plan_prev, duration_min)
st.session_state['_plan_duration'] = duration_min
_pc = st.session_state.get('_plan_change')
plan_change = None
if _pc and _pc[1] == duration_min:
    plan_change = ('Изменение плана: длительность ВКД %d → %d мин. Условия обстановки те же; сравнение окон выполнено '
                   'при новой длительности — это не улучшение обстановки.' % _pc)
if sc_delay:
    plan_change = ((plan_change + ' ') if plan_change else '') + 'Изменение плана: сценарий «что если» сдвигает начало работ на %d мин.' % sc_delay
if plan_change:
    S['request']['plan_change'] = plan_change

# ================================================================= шапка и приборная полоса
st.markdown(head('ВКД-Риск', 'внешняя обстановка на траектории МКС и выбор окна ВКД · %s · времена UTC' % mode_ru), unsafe_allow_html=True)
src = S['sources']
tm = S['trajectory_meta']
QUALITY_RU = {'final': 'окончательное', 'preliminary': 'предварительное', 'model': 'модель', 'unknown': 'качество не указано'}
horizon_to = t0 + timedelta(minutes=horizon_min)
# --- строка 1: обстановка. Всё в ней — наш расчёт и запрос, поэтому тон синий.
# трасса — наш расчёт при любой строгости входа (синий); строгость названа словом в подписи ячейки,
# а янтарный оставлен внешнему прогнозу и только ему (R4-28)
orbit_kind = 'cond' if meta is None else 'calc'
orbit_val = 'недоступна' if meta is None else METHOD_RU.get(meta.method, meta.method)
orbit_sub = status_ru(tm['status'], pro) if meta is None else '%s · шаг трассы 1 мин, точек %s' % (
    STRICT_RU.get(tm['strictness'], tm['strictness']), nbsp_thousands(tm['n_points']))
row1 = [('Время расчёта', dt_ru(now), 'все времена на экране — UTC', 'calc'),
        ('Режим', mode_ru, MODE_SUB[mode], 'calc'),
        ('Орбита', orbit_val, orbit_sub, orbit_kind),
        ('Горизонт', '%s ч от начала периода' % fmt(horizon_min / 60.0),
         'данные берутся до %s, последнее окно кончается %s' % (dt_ru(horizon_to), dt_ru(windows_end)), 'calc')]
# --- строка 2: источники. Цвет означает ТОЛЬКО происхождение: наблюдение зелёное, даже если взято
# из кеша (давность стоит подписью); янтарный остаётся за внешним прогнозом; красный — условие
# проверки по порогу или отказ источника. Кеш цветом больше не помечается (R4-28).
row2 = []
if mode == 'live':
    g, k = R.goes, R.kp
    g_src, k_src = src['noaa_swpc_goes'], src['gfz_kp']
    if g:
        row2.append(('GOES ≥10 МэВ, pfu', fmt(float(g.value)),
                     '%s · наблюдение %s · %s%s' % (s_level(g.value), dt_ru(g.t_utc, with_date=False),
                                                    age_ru(g_src.get('age_min'), th.goes_max_age_min),
                                                    ' · из кеша' if g_src.get('from_cache') else ''),
                     'cond' if g.value >= th.goes_p10_warning_pfu else 'obs'))
    else:
        _v, _k = source_short(g_src, disabled.get('goes'))
        row2.append(('GOES ≥10 МэВ, pfu', '—', _v, {'ok': 'obs', 'warn': 'fc', 'crit': 'cond'}.get(_k, 'none')))
    if k:
        row2.append(('Kp (GFZ)', fmt(float(k.value)),
                     '%s · интервал до %s · %s%s' % (QUALITY_RU.get(k.quality, k.quality), dt_ru(k.t_utc),
                                                     age_ru(k_src.get('age_min'), th.kp_max_age_min),
                                                     ' · из кеша' if k_src.get('from_cache') else ''),
                     'cond' if k.value >= th.kp_check else 'obs'))
    else:
        _v, _k = source_short(k_src, disabled.get('kp'))
        row2.append(('Kp (GFZ)', '—', _v, {'ok': 'obs', 'warn': 'fc', 'crit': 'cond'}.get(_k, 'none')))
    if meta and meta.epoch_utc:
        age_h = src['orbit'].get('age_h')
        row2.append(('Элементы орбиты', 'двухстрочные (TLE)',
                     'эпоха %s · %s · %s' % (dt_ru(meta.epoch_utc),
                                             age_ru(age_h * 60 if age_h is not None else None,
                                                    limit_ru='%s сут' % fmt(th.tle_max_age_days)),
                                             tle_origin(tm.get('tle_fetch_status'))),
                     'obs'))          # элементы — данные источника; возраст объявлен подписью и в блоке состояния
    else:
        row2.append(('Элементы орбиты', '—', 'элементов нет — орбита не построена', 'cond'))
    # Единица и происхождение — как у всех соседних ячеек полосы: голое число «0» их не имело
    # (находка шестого круга, область «экран», п. 6).
    row2.append(('События на горизонте',
                 '%s %s' % (fmt(len(R.events)), plural_ru(len(R.events), ('запись', 'записи', 'записей'))),
                 'наш подсчёт по реестру: уведомления и датированные прогнозы, учтённые в окнах',
                 'obs' if R.events else 'none'))
else:
    # Сколько записей не пошло в расчёт ИМЕННО из-за отсечки: остальные не подошли по содержанию,
    # и называть их исключёнными отсечкой нельзя (в разборе отсечки нет вовсе).
    _n_by_cutoff = sum(1 for x in R.excluded
                       if excl_group_ru(x.partition(': ')[2]) == 'время публикации или доступность')
    row2.append(('Отсечка публикации' if mode == 'history_forecast' else 'Начало периода', dt_ru(t0),
                 ('позже отсечки не используется ничего: по времени публикации отложено %s %s'
                  % (nbsp_thousands(_n_by_cutoff), plural_ru(_n_by_cutoff, ('запись', 'записи', 'записей'))))
                 if mode == 'history_forecast' else 'архив взят весь — разбор после факта', 'calc'))
    # Наблюдение Kp: в разборе — окончательный ряд GFZ, в строгом режиме — уведомление DONKI
    # с наблюдённым Kp и собственным временем публикации. Подпись идёт от записи, а не от режима.
    _kp_label = 'Kp (архив GFZ)' if mode == 'history_review' else 'Kp, наблюдение'
    if kp_off_hist:
        row2.append((_kp_label, 'исключён', 'исключён пользователем — проверка отказа источника', 'cond'))
    elif R.kp is not None and R.kp.source_id != 'scenario':
        row2.append((_kp_label, fmt(float(R.kp.value)),
                     '%s · интервал до %s · %s' % (source_ru(R.kp.source_id), dt_ru(R.kp.t_utc),
                                                   age_ru(src['gfz_kp'].get('age_min'), th.kp_max_age_min)),
                     'cond' if R.kp.value >= th.kp_check else 'obs'))
    elif mode == 'history_forecast':
        row2.append((_kp_label, '—', 'наблюдения с доказанной публикацией до отсечки нет', 'fc'))
    else:
        row2.append((_kp_label, '—', 'в архиве на этот момент нет', 'none'))
    # C3: в разборе есть численное наблюдение GOES — показываем его там же, где Kp.
    # Его отсутствие в строгом режиме объявляется один раз, в общем блоке о состоянии
    # источников, а не второй плашкой здесь (одно сообщение об одном и том же).
    if R.goes is not None and R.goes.source_id != 'scenario':
        row2.append(('GOES ≥10 МэВ, pfu', fmt(float(R.goes.value)),
                     '%s · наблюдение %s' % (s_level(R.goes.value), dt_ru(R.goes.t_utc)),
                     'cond' if R.goes.value >= th.goes_p10_warning_pfu else 'obs'))
    if meta and meta.created_utc:
        row2.append(('Элементы орбиты', 'эфемериды OEM NASA/JSC',
                     'создан %s · за %s ч до начала периода · %s'
                     % (dt_ru(meta.created_utc),
                        fmt(round((t0 - meta.created_utc).total_seconds() / 3600)),
                        STRICT_RU.get(tm['strictness'], tm['strictness'])), 'obs'))
    else:
        row2.append(('Элементы орбиты', '—', 'эфемерид на этот момент нет — орбита не построена', 'cond'))
    if pro:                    # границы архива не меняются от запроса — на оперативном уровне это шум
        _cat = (S.get('history') or {}).get('catalog_coverage') or {}
        _cat_ru = lambda k: (datetime.fromisoformat(_cat[k]).strftime('%d.%m.%Y') if _cat.get(k) else '—')
        row2.append(('Архив уведомлений', '%s — %s' % (_cat_ru('from_utc'), _cat_ru('to_utc')),
                     'уведомления и карточки событий NASA DONKI', 'obs' if _cat else 'none'))
if S['is_simulated']:
    row2.append(('Сценарий «что если»', 'моделируемые значения', 'часть величин задана пользователем, не источником', 'fc'))
st.markdown(panel([row1, row2]), unsafe_allow_html=True)
if meta is None:
    st.error('**Орбита недоступна.** %s Оценка без траектории невозможна: покрытие обязательной линии отсутствует, '
             'рекомендации нет. Заглушка не подставляется.' % status_ru(tm['status'], pro))
# Признак исключения источника берётся из ЗАПРОСА (что выставил пользователь), а не из подстроки
# «исключён» в тексте статуса: слой источников дописывает это слово в свои штатные пояснения (R4-11).
issues = source_issues(src, th, mode, kp_excluded_hist=kp_off_hist, tle_fetch=tm.get('tle_fetch_status'), pro=pro,
                       disabled=S['request'].get('disabled') or {}, cutoff_utc=S['request'].get('cutoff_utc'),
                       orbit_created_utc=tm.get('created_utc'))
if fetch_note and not any(LIMIT_MARK in x for x in issues):
    # Общий предел получения источников назван РОВНО ОДИН раз (бриф §9.7): если он уже стоит
    # в строке конкретного источника, который остался без данных, общей строки не нужно.
    # Она нужна в другом случае — когда все источники подхватились из кеша и по отдельным
    # строкам непонятно, почему живого запроса не было ни у одного.
    issues.insert(0, 'Живые источники: %s%s. Взяты кеш и снимок репозитория — экран построен без сети.'
                  % (fetch_note, ' (настройка total_deadline_s в config/settings.toml)' if pro else ''))
if issues:
    st.warning('**Состояние источников:**\n' + '\n'.join('- ' + x for x in issues))

# ================================================================= ответ первым
missing_ru = []
for m_ in rec.missing:
    extra = ''
    if 'космопогода' in m_ and mode == 'live' and (S['request'].get('disabled') or {}).get('goes') == 'off':
        extra = ' — GOES исключён пользователем'
    elif 'космопогода' in m_ and mode != 'live':
        extra = ' — наблюдений GOES в архиве нет, а окна выходят за каталог DONKI (01.05–30.06.2024)' \
            if windows_beyond_archive else ' — линия без данных на горизонте'
    missing_ru.append(m_ + extra)
any_cond = any(m.needs_check for a in R.assessments for m in a.mechanisms)
# политика прототипа целиком — один раз, во вкладке «Объяснения»; здесь только указатель (U5)
policy_short = 'Окна с условиями не выбираются автоматически — правило команды, не норма (вкладка «Объяснения»).' if any_cond else None
c_main, c_btn = st.columns([6, 1.5])
c_main.markdown(verdict_panel(rec, S, windows_ru, assessments=R.assessments, pro=pro, plan_change=plan_change,
                              missing_ru=missing_ru, policy_short=policy_short,
                              thr_nT=th.saa_B_threshold_nT, e_min_MeV=th.e_min_MeV), unsafe_allow_html=True)
_fname = 'vkd_risk_%s_%s_calc%s' % (mode, t0.strftime('%Y%m%dT%H%M'), now.strftime('%Y%m%dT%H%M'))
_zip = build_zip(S, R.raw_records)
c_btn.download_button('Скачать отчёт (ZIP)', _zip, file_name=_fname + '.zip', mime='application/zip', width='stretch', key='dl_top')
c_btn.caption('отчёт, запрос, факторы, сырые записи')
cols = st.columns(len(R.assessments))
for i, (col, a) in enumerate(zip(cols, R.assessments)):
    col.markdown(window_card(i, a, best=(rec.preferred is not None and rec.preferred.start_utc == a.window.start_utc),
                             mode=mode, saa_thr_nT=th.saa_B_threshold_nT), unsafe_allow_html=True)
st.markdown('<div class="legend">Происхождение величин: %s — измерено источником; %s — выпуск с указанием времени публикации; '
            '%s — посчитано сервисом по траектории и стандартам. %s</div>'
            % (kind_pill('observation'), kind_pill('external_forecast'), kind_pill('own_calculation'), COLOR_LEGEND),
            unsafe_allow_html=True)
if mode == 'history_forecast' and R.verification:
    st.info('**Проверка после отсечки** (в расчёт не входит — только сопоставление прогноза с фактом): %s. '
            'Подробности — вкладка «Наблюдения и прогнозы».' % verification_ru(R.verification['summary'], any_cond))

# ================================================================= лента времени (график 1 из 2 на главном экране)
kp_obs, goes_obs = [], []
if mode == 'live':
    from app.obs import goes_series, kp_series
    tk, vk = kp_series(R.fetch_status['kp'].raw_path)
    kp_obs = [(t, t + timedelta(hours=3), v) for t, v in zip(tk, vk) if t >= t0 - timedelta(hours=12)]
    tg, vg = goes_series(R.fetch_status['goes'].raw_path)
    goes_obs = [(t, v) for t, v in zip(tg, vg) if t >= t0 - timedelta(hours=12)]
elif mode == 'history_review':
    kp_obs = R.kp_obs or []
# S8: снимок третьего круга может принести наблюдения GOES из архива 2024 — рисуем их, если ключ есть.
for _row in (S.get('observations') or []):
    if _row.get('channel') in ('goes_p_ge10MeV', 'goes') and not goes_obs:
        goes_obs = [(datetime.fromisoformat(c['t']), float(c['value'])) for c in (_row.get('points') or [])
                    if c.get('t') and c.get('value') is not None]
_mid_ru = ('прогноз Kp NOAA по 3-часовым интервалам из выпуска до отсечки' if mode == 'history_forecast' else
           'наблюдения Kp (GFZ) и GOES ≥10 МэВ за последние 12 ч' if mode == 'live' else
           'наблюдения Kp из архива (разбор после факта)')
_tl_caption = ('Ряд 1 — |B| на трассе, наш расчёт по IGRF: красные полосы — пролёты аномалии, синие — окна-кандидаты. '
               'Ряд 2 — %s. Ряд 3 — события и прогнозы по типам: положение по вертикали означает тип, не значение.' % _mid_ru) if pro else \
              ('Ряд 1 — |B| на трассе, наш расчёт. Ряд 2 — %s. Ряд 3 — события по типам.'
               % _mid_ru.split(' из выпуска')[0].split(' за последние')[0].split(' (разбор')[0])


def _timeline_chart():
    st.plotly_chart(timeline(traj, windows, th.saa_B_threshold_nT, t0, horizon_min, R.goes, R.kp, R.events,
                             S.get('forecasts', []), mode, kp_obs=kp_obs, goes_obs=goes_obs, search_min=search_min),
                    width='stretch', config=PLOTLY_CONFIG)
    st.caption(_tl_caption)


if pro:
    st.subheader('Картина по времени')
    _timeline_chart()
else:
    # И6: на оперативном уровне лента убрана в свёртку — экран короче, первые четыре блока видны без прокрутки
    with st.expander('Картина по времени: обстановка на трассе и события', expanded=False):
        _timeline_chart()

# ================================================================= вкладки
tab_names = ['Объяснения', 'Окна и факторы', 'Методика', 'Карта', 'Наблюдения и прогнозы', 'Данные'] \
    + (['Устойчивость и нормы'] if pro else [])
tabs = st.tabs(tab_names)

with tabs[0]:
    _win_labels = ['Окно %d (%s)%s' % (i + 1, dt_ru(a.window.start_utc),
                                        ' — предпочтительное' if rec.preferred is not None and rec.preferred.start_utc == a.window.start_utc else '')
                   for i, a in enumerate(R.assessments)]
    if len(_win_labels) > 1:
        _pick = st.radio('Окно', _win_labels, horizontal=True, key='cards_win')
        _wi = _win_labels.index(_pick)
    else:
        _wi = 0
    cards = ((R.cards_by_window or {}).get(R.assessments[_wi].window.start_utc) if R.assessments else None) \
        or [c for c in R.cards if c.window_index == _wi + 1]
    shown = cards if pro else [c for c in cards if c.severity != 'info'] + [c for c in cards if c.severity == 'info'][:4]
    st.markdown('<div class="small">%s</div>'
                % ('Каждое предупреждение раскрывается по семи пунктам постановки: воздействие, период, данные, '
                   'источник и время публикации, правило, ограничения, происхождение. Карточки каждого окна — '
                   'по переключателю выше.' if pro else
                   'Каждое предупреждение раскрывается по семи пунктам: что, когда, по каким данным и из какого источника.'),
                unsafe_allow_html=True)
    for c in shown:
        _sev = SEV_RU.get(c.severity, '—')
        # заголовок свёртки — тем же форматом, что весь экран: даты «дд.мм чч:мм», дроби с запятой,
        # разряды тысяч. Через frac_ru даты не проходили и оставались машинными «05-10 13:35Z» (R4-3).
        label = '%s · %s · %s' % (_sev, screen_text(c.title), KIND_RU[c.kind])
        with st.expander(label, expanded=(c.severity != 'info')):
            # повтор одной и той же части подписи убираем, обрывки кода — на профессиональный уровень (U2, U5)
            # обрез тела уведомления многоточием оставлял незакрытую скобку — закрываем её на месте обреза
            _txt = lambda t: close_cut_parens(dedup_clauses(status_ru(t, pro)))
            st.markdown('**1. Воздействие и значение для ВКД.** ' + frac_ru(c.impact_ru))
            st.markdown('**2. Период.** ' + _txt(c.period_ru))
            st.markdown('**3. Данные и единицы.** ' + _txt(c.data_ru))
            st.markdown('**4. Источник и время публикации.** ' + _txt(c.source_ru))
            _fref = formula_ref(c.title, c.rule_ru, c.data_ru)
            # Текст правила приходит из слоёв без конечной точки, и дописка о формуле приклеивалась
            # к нему без знака препинания («…варьируется в чувствительности) Формальная запись —…»).
            # Ставим точку сами, если её там нет (находка шестого круга, область «экран», п. 4).
            _rule = _txt(c.rule_ru)
            if _fref and _rule and not _rule.rstrip().endswith(('.', '!', '?', ':', ';')):
                _rule = _rule.rstrip() + '.'
            st.markdown('**5. Применённое правило или модель.** ' + _rule
                        + (' Формальная запись — вкладка «Методика», формула %s.' % _fref if _fref else ''))
            st.markdown('**6. Ограничения и уверенность.** ' + _txt(c.limits_ru))
            st.markdown('**7. Происхождение.** ' + KIND_RU[c.kind])
            links, no_link = [], []
            for rid in c.record_ids:
                u = record_url(raw_record(R.raw_records, rid))
                if pro:                    # идентификатор записи виден только на профессиональном уровне (U5)
                    links.append('[%s](%s)' % (rid, u) if u else '`%s`' % rid)
                elif u:
                    # Подпись ссылки — имя самой записи (выпуск источника), а не «первоисточник 1»:
                    # в одной карточке стоят два уведомления с разными числами, и по безымянной
                    # подписи нельзя понять, какая ссылка к какому числу (находка пятого круга).
                    links.append('[%s](%s)' % (record_ru(rid, with_kind=False), u))
                else:
                    no_link.append(rid)
            if links or no_link:
                _n = 12 if pro else 6          # оперативному уровню хватает нескольких ссылок (U5)
                # причина отсутствия адреса у записей разная: таблицы стандартов и эфемериды сервис
                # везёт с собой, а у живой записи адрес просто не сохранён слоем источников — он есть
                # в манифесте выгрузки. Одной фразой на оба случая это была неправда (R4-10).
                st.markdown('**Первоисточник:** ' + ', '.join(links[:_n])
                            + (' … ещё %d' % (len(links) - _n) if len(links) > _n else '')
                            + ((('; ' if links else '') + record_no_url_ru(no_link)) if no_link else ''))
            if pro:
                _raw = [rid for rid in c.record_ids[:12] if raw_record(R.raw_records, rid) is not None]
                if _raw:
                    st.caption('Ниже — сырая запись источника, на языке источника: как её опубликовал NOAA, NASA или GFZ, '
                               'без нашего перевода и без изменений.')
                for rid in _raw:
                    st.json(raw_record(R.raw_records, rid), expanded=False)
    with st.expander('Политика прототипа и чего не заявляем', expanded=False):
        st.markdown(S['policy_note'])
        st.markdown('Чего сервис не заявляет:\n'
                    '- допустимость реального выхода — за уполномоченными специалистами;\n'
                    '- вероятность разгерметизации и попадания в космонавта не вычисляется;\n'
                    '- доза человека не вычисляется; поток GOES не переносится на станцию без обрезания;\n'
                    '- неизвестное не превращается ни в нуль, ни в «благоприятно»: отсутствие данных объявляется.')

with tabs[1]:
    # Таблица «как в статье» (PROPOSAL_A п. 6): единица — в подписи строки, в ячейке только число;
    # строки сгруппированы заголовками ВЕЛИЧИНЫ / ПОКРЫТИЕ / УСЛОВИЯ; под таблицей — подпись «Таблица 1.».
    # Столбчатого графика с двумя осями здесь нет: он сравнивал минуты и флюенс в разных масштабах и
    # читался как «во столько раз хуже», чего из него не следует.
    col_names = ['Окно %d (%s)' % (i + 1, dt_ru(a.window.start_utc)) for i, a in enumerate(R.assessments)]
    row_keys: list[tuple[str, str]] = []
    cells: dict[tuple[str, str], list] = {}
    units: dict[tuple[str, str], str] = {}
    for j, a in enumerate(R.assessments):
        for m in a.mechanisms:
            if not (m.mandatory or m.coverage.value != 'none'):
                continue
            for f in m.factors:
                k_ = ('величина', f.name)
                if k_ not in cells:
                    row_keys.append(k_); cells[k_] = ['—'] * len(R.assessments); units[k_] = f.unit or ''
                # единица — в подписи строки, не в ячейке; наблюдение, не покрывающее окно ни на
                # одну минуту, даёт прочерк, а не число прошлого измерения (О2)
                cells[k_][j] = factor_value_ru(f)
            k_ = ('покрытие', MECH_RU.get(m.mechanism_id, m.mechanism_id))
            if k_ not in cells:
                row_keys.append(k_); cells[k_] = ['—'] * len(R.assessments)
            why = '; '.join(coverage_reasons(a)) if m.coverage.value != 'full' and m.mechanism_id == 'spaceweather' else ''
            cells[k_][j] = COV_RU[m.coverage.value] + ((' — ' + why) if why else '')
            k_ = ('условия', MECH_RU.get(m.mechanism_id, m.mechanism_id))
            if k_ not in cells:
                row_keys.append(k_); cells[k_] = ['нет'] * len(R.assessments)
            if m.needs_check:
                cells[k_][j] = '; '.join(short_reason(r) for r in m.needs_check_reasons)
    order = {'величина': 0, 'покрытие': 1, 'условия': 2}
    group_ru = {'величина': 'ВЕЛИЧИНЫ', 'покрытие': 'ПОКРЫТИЕ', 'условия': 'УСЛОВИЯ'}
    rows, last_group = [], None
    for k_ in sorted(row_keys, key=lambda k: (order[k[0]], row_keys.index(k))):
        if k_[0] != last_group:                            # разделительная строка-заголовок группы
            rows.append({'показатель': group_ru[k_[0]], **{cn: '' for cn in col_names}})
            last_group = k_[0]
        label = k_[1] + ((', ' + units[k_]) if k_[0] == 'величина' and units.get(k_) not in (None, '', '1') else '')
        # screen_text, а не frac_ru: в ячейках стоят готовые строки условий и причин покрытия, и
        # без перевода дат в них оставались машинные «05-10 13:35Z» рядом с «10.05 12:14» в вердикте (R4-3)
        rows.append({'показатель': label, **{cn: screen_text(cells[k_][j]) for j, cn in enumerate(col_names)}})
    st.dataframe(rows, width='stretch', hide_index=True,
                 column_config={'показатель': st.column_config.TextColumn(width='medium'),
                                **{cn: st.column_config.TextColumn(width='large') for cn in col_names}})
    # Подпись таблицы 1 на оперативном уровне называет допуск числами и отсылает к строке под
    # вердиктом; слова «ранжирование» и «порядок окон при нулевом допуске» остаются на
    # профессиональном уровне и во вкладке «Устойчивость и нормы» (R4-18).
    _rob = S.get('robustness') or {}
    st.markdown('<div class="tcap">Таблица 1. Сравнение окон по учтённым механизмам; допуск равнозначности — %s.</div>'
                % (screen_text(rec.tolerance_basis) if pro else
                   '%s мин по минутам в аномалии и ×%s по флюенсу; откуда он взялся — строка под вердиктом'
                   % (fmt(round(float(_rob.get('tol_min') or 0))), ratio_ru(_rob.get('tol_ratio')))),
                unsafe_allow_html=True)
    st.markdown('<div class="small">%s</div>' % coverage_scope_ru(S), unsafe_allow_html=True)
    if pro:
        st.caption('Порядок сравнения — пять шагов: охват → условия → сравнение по каждому механизму → сведение → допуск '
                   '(формальная запись — вкладка «Методика», формулы (8) и (9)).')
    else:
        st.caption('Порядок сравнения: охват → условия → сравнение по механизмам → сведение → допуск равнозначности '
                   '(формулы — вкладка «Методика»).')

with tabs[2]:
    st.markdown('**Как считается то, что показано на экране.** У каждой формулы — номер, расшифровка символов, '
                'стандарт с пунктом или таблицей и то, что в ней НЕ учтено. Карточки во вкладке «Объяснения» '
                'ссылаются на эти номера.')
    st.caption('Внутри формул только латиница и греческие буквы: шрифты набора формул не содержат кириллицы, '
               'и русские слова в них на части браузеров не рисуются. Все пояснения — обычным текстом под формулой.')
    _group = None
    for b in METHOD_BLOCKS:
        if b.get('pro') and not pro:
            continue
        if b['group'] != _group:
            _group = b['group']
            st.markdown('<div class="sect">%s</div>' % _group, unsafe_allow_html=True)
        st.markdown('**(%d) %s**' % (b['no'], b['title']))
        st.latex(b['latex'])
        st.markdown('<div class="small">%s</div>' % b['symbols'], unsafe_allow_html=True)
        st.markdown('<div class="small">Источник: %s</div>' % b['source'], unsafe_allow_html=True)
        st.markdown('<div class="small">Не учтено и ограничения: %s</div>' % b['limits'], unsafe_allow_html=True)
    st.markdown('<div class="sect">Пороги условий проверки</div>', unsafe_allow_html=True)
    st.dataframe(RULE_THRESHOLDS, width='stretch', hide_index=True,
                 column_config={'условие': st.column_config.TextColumn(width='medium'),
                                'источник': st.column_config.TextColumn(width='medium')})
    st.markdown('<div class="tcap">Таблица 2. Пороги, по которым окно получает условие проверки; источник — на каждую строку.</div>',
                unsafe_allow_html=True)
    st.markdown('<div class="small">%s</div>' % RULE_POLICY, unsafe_allow_html=True)
    if pro:
        st.caption('Тексты правил и порогов — CONTRACT.md, раздел 4; настройки — config/settings.toml.')
        st.markdown('<div class="sect">Подстановка текущего расчёта</div>', unsafe_allow_html=True)
        _flu = next((f for a in R.assessments for m in a.mechanisms for f in m.factors if f.name.startswith('флюенс')), None)
        st.markdown('<div class="small">Канал захваченных протонов: от %s МэВ. Порог аномалии |B|: %s нТл. '
                    'Шаг трассы: 1 мин, точек трассы %s. Флюенс окна 1: %s. Допуск равнозначности: %s.</div>'
                    % (fmt(th.e_min_MeV), nbsp_thousands(th.saa_B_threshold_nT), fmt(tm['n_points']),
                       fmt(_flu.value if _flu else None, _flu.unit if _flu else ''), frac_ru(rec.tolerance_basis)),
                    unsafe_allow_html=True)

with tabs[3]:
    if traj:
        # Основной вид — глобус: на сфере трасса не рвётся на долготе 180°, и видно, как окно
        # ложится на геометрию пролётов аномалии. Плоская карта остаётся запасным видом и нужна
        # там, где трёхмерная сцена не строится (нет WebGL, нет сети за three.js или текстурой).
        view = st.radio('Вид', [globe.VIEW_GLOBE, globe.VIEW_FLAT], index=0, horizontal=True, key='map_view',
                        help='Глобус показывает ту же трассу и ту же область аномалии на сфере, без разрыва '
                             'по долготе. Плоская карта — запасной вид: она работает без WebGL и без сети.')
        if view == globe.VIEW_GLOBE:
            try:
                with st.spinner('Область аномалии по IGRF на сетке 4°…'):
                    _gp = globe.globe_payload(traj, windows, th.saa_B_threshold_nT, t0)
                globe.render_globe(_gp)
                st.caption(globe.caption(_gp))
                if pro:
                    st.caption(globe.tech_line(_gp))
            except Exception as e:                    # noqa: BLE001 — Т6: вид отказал, экран остаётся
                LOG.error('глобус не построен: %s\n%s', e, traceback.format_exc())
                st.warning('Глобус не построен (%s). Переключите вид на «Плоская карта» — данные те же.'
                           % type(e).__name__)
        else:
            with st.spinner('Область аномалии по IGRF на сетке 4°…'):
                st.plotly_chart(ground_track(traj, windows, th.saa_B_threshold_nT, t0), width='stretch', config=PLOTLY_CONFIG)
            if pro:
                st.caption('Область аномалии — наш расчёт |B| по IGRF на средней высоте трассы; трасса за весь горизонт серым, '
                           'окна-кандидаты цветом, точки трассы в аномалии красным. Карта показывает, откуда берутся минуты в аномалии.')
            else:
                st.caption('Красным — область аномалии и точки трассы в ней, цветом — окна: откуда берутся минуты в аномалии.')
    else:
        st.write('Трассы нет: орбита недоступна.')

with tabs[4]:
    if mode == 'live':
        obs_fig = observations_panel(R.fetch_status['goes'].raw_path, R.fetch_status['kp'].raw_path, t0)
        if obs_fig is not None:
            st.plotly_chart(obs_fig, width='stretch', config=PLOTLY_CONFIG)
            st.caption('Наблюдения источников за последние дни, не расчёт. Пороги — шкалы NOAA S и G.' + (
                ' GOES меряет на геостационарной орбите и переносится на станцию только через геомагнитное обрезание.'
                if pro else ''))
        else:
            st.write('Рядов наблюдений нет: источники отключены или недоступны.')
        # S8: в текущем режиме снимок третьего круга может принести датированный прогноз NOAA —
        # показываем его теми же строками, что и в архивных режимах; ключа нет — блока нет.
        _live_fc = S.get('forecasts') or []
        if _live_fc:
            st.markdown('**Внешний прогноз NOAA на горизонте окон** (выпуск с указанием времени публикации)')
            for line in _live_fc:
                _pub = screen_text((line.get('published_utc') or '')[:16].replace('T', ' '))
                _u = record_url(raw_record(R.raw_records, line.get('record'))) if line.get('record') else None
                if _pub:
                    _rel = ('[выпуск от %s UTC](%s)' % (_pub, _u)) if _u else 'выпуск от %s UTC' % _pub
                else:
                    _rel = 'времени выпуска в записи нет'
                st.markdown('%s **%s** — %s' % (pill(line.get('status_ru') or '—',
                                                     'ok' if line.get('status') == 'full' else
                                                     'warn' if line.get('status') == 'partial' else 'none'),
                                                line.get('label') or '—', _rel),
                            unsafe_allow_html=True)
            st.caption('Внешний прогноз, не наблюдение. Суточные вероятности относятся к суткам, а не к окну ВКД, '
                       'и в вероятность за окно не пересчитываются.')
    else:
        # C3: в исторических режимах архив даёт настоящий ряд наблюдений — он рисуется тем же
        # рисунком, что и в текущем режиме. В строгом режиме ряда нет (архив исключён по
        # недоказанной публикации), и вместо картинки печатается причина из снимка.
        _obs_line = next((o for o in (S.get('observations') or []) if o.get('channel') in ('goes_p_ge10MeV', 'goes')), None)
        _tg = [datetime.fromisoformat(p['t']) for p in (_obs_line or {}).get('points', [])]
        _vg = [float(p['value']) for p in (_obs_line or {}).get('points', [])]
        _tk = [a for a, _b, _v in (R.kp_obs or [])]
        _vk = [_v for _a, _b, _v in (R.kp_obs or [])]
        hist_obs_fig = observations_figure(_tg, _vg, _tk, _vk, t0,
                                           'GOES, протоны ≥10 МэВ, pfu — наблюдение из архива NASA iSWA за 2024',
                                           'Kp — наблюдение из окончательного ряда GFZ',
                                           'отсечка' if mode == 'history_forecast' else 'начало периода')
        if hist_obs_fig is not None:
            st.plotly_chart(hist_obs_fig, width='stretch', config=PLOTLY_CONFIG)
            st.caption('Наблюдения архива, не расчёт и не прогноз. Пороги — шкалы NOAA S и G.%s'
                       % (' Ряд GOES — 5-минутные средние; Kp — 3-часовые интервалы.' if pro else ''))
        else:
            st.markdown('%s Численных наблюдений на этом горизонте нет: %s'
                        % (pill('нет наблюдений', 'none'),
                           status_ru(src.get('noaa_swpc_goes', {}).get('status') or '—', pro)), unsafe_allow_html=True)
        fc_fig = forecast_panel(S.get('forecasts', []), t0, horizon_min,
                                kp_title='Прогноз Kp NOAA по 3-часовым интервалам (%s)' % RELEASE_BY_MODE_RU[mode],
                                mark_ru='отсечка' if mode == 'history_forecast' else 'начало периода')
        if fc_fig is not None:
            st.plotly_chart(fc_fig, width='stretch', config=PLOTLY_CONFIG)
        for line in S.get('forecasts', []):
            if line['release_id']:
                u = record_url(raw_record(R.raw_records, line['record'])) if line.get('record') else None
                pub = screen_text((line['published_utc'] or '')[:16].replace('T', ' '))
                rel = ('[выпуск от %s UTC](%s)' % (pub, u)) if u else 'выпуск от %s UTC' % pub
            else:
                rel = 'выпуска до отсечки в архиве нет'
            st.markdown('%s **%s** — %s' % (pill(line['status_ru'], 'ok' if line['status'] == 'full' else 'warn' if line['status'] == 'partial' else 'none'),
                                            line['label'], rel), unsafe_allow_html=True)
        st.caption('Внешний прогноз, не наблюдение. Суточные вероятности относятся к суткам, а не к окну ВКД; прогноз Kp — по '
                   '3-часовым интервалам. В архиве трёхсуточных выпусков NOAA есть разрыв 15.05–16.06.2024: внутри него '
                   'канал объявляется отсутствующим, а не заполняется устаревшим бюллетенем.'
                   + (' Отбор выпуска по времени публикации.' if pro else ''))
    if mode == 'history_forecast' and R.verification:
        ver = R.verification
        with st.expander('Проверка после отсечки — что наблюдалось потом (в расчёт не входит)', expanded=True):
            st.markdown('**%s.**' % verification_ru(ver['summary'], any_cond))
            _iso_ru = lambda s: datetime.fromisoformat(s).strftime('%d.%m.%Y %H:%M') if s else '—'
            # подпись называет ПРАВИЛО отбора, а не один источник: один и тот же 3-часовой интервал
            # сообщают и окончательный ряд GFZ, и датированные уведомления DONKI о буре, и в колонке
            # происхождения половина строк «Гэннон» — уведомления. Прежняя подпись «наблюдения Kp —
            # окончательный ряд GFZ» опровергалась колонкой рядом (правило пятого круга).
            st.caption('%s. Отсечка %s, горизонт до %s UTC. На один 3-часовой интервал берётся запись '
                       'с самым поздним известным временем публикации: у окончательного ряда GFZ '
                       'собственного времени публикации по интервалам нет, и он уступает датированному '
                       'уведомлению DONKI. Происхождение каждой строки — в таблице.%s'
                       % (ver['note'], _iso_ru(ver['cutoff_utc']), _iso_ru(ver['horizon_to_utc']),
                          ' События — уведомления DONKI, опубликованные после отсечки. Слой: %s.'
                          % ver.get('source_layer', '—') if pro else ''))
            vc1, vc2 = st.columns(2)
            # даты таблиц проверки — в том же виде, что везде на экране: «дд.мм чч:мм», не «05-10 15:00»
            _dt_ru = lambda s: dt_ru(datetime.fromisoformat(s)) if s else '—'
            if ver.get('kp_obs'):
                # третья колонка — происхождение строки: из какой записи взято число и почему именно
                # из неё. Без неё экран печатал Kp 9 там, где окончательный ряд GFZ даёт 8,67, и
                # объяснение стояло только в выгрузке (стык с областью «модели», ключ origin).
                # конец интервала: время без даты читалось как утро того же дня и выходило РАНЬШЕ
                # начала («с 10.05 21:00 | по 00:00»). Дата конца печатается при переходе через
                # полночь — тем же правилом, что app.ui.win_span (находка шестого круга, п. 5).
                _dt_to_ru = lambda a, b: (_dt_ru(b) if not a or not b
                                          or datetime.fromisoformat(a).date() != datetime.fromisoformat(b).date()
                                          else datetime.fromisoformat(b).strftime('%H:%M'))
                vc1.dataframe([{'интервал с': _dt_ru(x['from_utc']),
                                'по': _dt_to_ru(x.get('from_utc'), x.get('to_utc')), 'Kp': fmt(x['kp']),
                                'условие проверки': 'да' if x['kp'] >= th.kp_check else 'нет',
                                'происхождение': screen_text(x.get('origin') or '—')} for x in ver['kp_obs']],
                              width='stretch', hide_index=True,
                              column_config={'происхождение': st.column_config.TextColumn(width='large')})
            else:
                vc1.write('Наблюдений Kp на горизонте в архиве нет.')
            if ver.get('events'):
                vc2.dataframe([{'тип': event_kind_ru(x['kind']), 'публикация': _dt_ru(x['published_utc']),
                                'начало': _dt_ru(x['start_utc']), 'примечание': screen_text(x['note'])} for x in ver['events']],
                              width='stretch', hide_index=True, column_config={'примечание': st.column_config.TextColumn(width='large')})
            else:
                vc2.write('Уведомлений о протонных событиях и бурях после отсечки на горизонте нет.')
    if R.events:
        st.markdown('**События и прогнозы, учтённые на горизонте** (время публикации — из записи источника)')
        ev_rows = []
        for e in sorted(R.events, key=lambda e: (e.published_utc or e.start_utc or datetime.max.replace(tzinfo=timezone.utc))):
            u = record_url(raw_record(R.raw_records, e.raw_record_id))
            row = {'тип': event_kind_ru(e.kind_of_event) + (' (сценарий)' if e.is_simulated else ''),
                   'происхождение': KIND_RU[e.kind],
                   'начало / приход': dt_ru(e.start_utc),
                   'публикация': dt_ru(e.published_utc) if e.published_utc else 'нет — моделируемое' if e.is_simulated else 'нет',
                   'примечание': status_ru(e.note or '', pro), 'ссылка': u}
            if pro:                       # идентификатор записи — только на профессиональном уровне (U5)
                row['запись'] = e.event_id
            ev_rows.append(row)
        kinds_all = sorted({r['тип'] for r in ev_rows})
        if len(ev_rows) > 20:
            sel = st.multiselect('Показать типы', kinds_all, default=kinds_all, key='ev_filter')
            ev_rows = [r for r in ev_rows if r['тип'] in sel]
        st.dataframe(ev_rows, width='stretch', hide_index=True,
                     column_config={'ссылка': st.column_config.LinkColumn('первоисточник', display_text='открыть'),
                                    'примечание': st.column_config.TextColumn(width='large'),
                                    **({'запись': st.column_config.TextColumn('запись', width='medium')} if pro else {})})

with tabs[5]:
    st.markdown('**Источники этого расчёта** — состояние, получение, давность; каждый фактор прослеживается до записи в выгрузке.')
    src_rows = []
    for k, v in src.items():
        if k.startswith('_'):
            continue
        static = k in ('ost1044_belts', 'ecss_grun')
        hist_obs = mode != 'live' and k in ('noaa_swpc_goes', 'gfz_kp')     # живые наблюдения в архивных режимах не используются
        status = v.get('status') or ''
        # состояние «исключён» — по признаку из запроса, а не по подстроке в тексте статуса (R4-11)
        state, kind = ('таблица встроена', 'ok') if static else source_short(
            v, (S['request'].get('disabled') or {}).get(DISABLED_KEY.get(k)))
        if k == 'orbit':
            if mode == 'live' and tm.get('tle_fetch_status'):
                status = '%s; TLE: %s' % (status, tm['tle_fetch_status'])
            state, kind = STRICT_RU.get(v.get('strictness'), v.get('strictness') or '—'), ('ok' if v.get('strictness') == 'strict' else 'warn')
        elif mode != 'live' and k == 'noaa_swpc_goes':
            # C3: численный архив наблюдений GOES 2024 подключён. Состояние и давность берём
            # из снимка; прежняя зашитая фраза «архива нет» в «Историческом разборе» была неправдой.
            if v.get('data_utc'):
                state, hist_obs = 'архив наблюдений', False
            elif bool(disabled.get('goes')):
                state = 'исключён'
            elif mode == 'history_forecast':
                state = 'исключён строгим режимом'
            else:
                state = 'нет в архиве'
        elif mode != 'live' and k == 'gfz_kp':
            state = ('исключён' if kp_off_hist else 'сценарий' if (R.kp is not None and R.kp.source_id == 'scenario')
                     else 'архив' if R.kp is not None else 'нет до отсечки' if mode == 'history_forecast' else 'нет в архиве')
            hist_obs = R.kp is None or R.kp.source_id == 'scenario'      # есть запись архива — показываем её время и давность от t0
        elif k.startswith('noaa_forecast_'):
            # происхождение выпуска берётся из снимка, а не от имени ключа: в текущем режиме это
            # живой бюллетень NOAA, и подписывать его «архивом выпусков» — неправда (О2)
            state = 'живой бюллетень' if mode == 'live' else 'архив выпусков'
        elif k == 'donki_archive':
            state = 'архив, отбор по публикации' if mode == 'history_forecast' else 'архив, весь'
        age = v.get('age_min')
        if k == 'orbit' and v.get('age_h') is not None:
            age = v['age_h'] * 60
        src_rows.append({'источник': source_name_ru(k, mode), 'роль': v.get('role'), 'состояние': state,
                         'живой запрос': BOOL_RU.get(v.get('live_ok'), '—') if not static and mode == 'live' else '—',
                         'из кеша': BOOL_RU.get(v.get('from_cache'), '—') if not static and mode == 'live' else '—',
                         'получено': 'встроено' if static else '—' if hist_obs
                         else screen_text((v.get('fetched_utc') or '—')[:16].replace('T', ' ')),
                         'данные на': '—' if hist_obs
                         else screen_text((v.get('data_utc') or v.get('epoch_utc') or '—')[:16].replace('T', ' ')),
                         'давность, мин': fmt(round(age)) if (age is not None and not hist_obs) else '—',
                         'строгость': STRICT_RU.get(v.get('strictness'), '—') if k == 'orbit' else '—',
                         'статус': status_ru(status, pro)})
    st.dataframe(src_rows, width='stretch', hide_index=True,
                 column_config={'статус': st.column_config.TextColumn(width='large'), 'источник': st.column_config.TextColumn(width='medium')})
    st.markdown('<div class="tcap">Таблица 3. Состояние источников этого расчёта: получение, момент данных и давность.</div>',
                unsafe_allow_html=True)
    # Реестр источников: величина, единица, частота, что считается публикацией, лицензия, ограничение.
    # Лицензия печатается только там, где она записана в самом ответе службы или в документе стандарта.
    st.markdown('<div class="sect">Реестр источников</div>', unsafe_allow_html=True)
    reg_rows = []
    for k in src:
        if k.startswith('_'):
            continue
        r = registry_row(k, origin=src[k].get('origin'))
        if r['величина'] == '—' and not k.startswith('noaa_forecast_'):
            continue
        reg_rows.append({'источник': source_name_ru(k, mode), **r})
    st.dataframe(reg_rows, width='stretch', hide_index=True,
                 column_config={'источник': st.column_config.TextColumn(width='medium'),
                                'величина': st.column_config.TextColumn(width='medium'),
                                'публикация': st.column_config.TextColumn(width='medium'),
                                'ограничение': st.column_config.TextColumn(width='large')})
    st.markdown('<div class="tcap">Таблица 4. Реестр источников: величина, единица, частота выпуска, что считается '
                'временем публикации, условия использования и ограничение. Где лицензия не записана в ответе службы, '
                'так и сказано — сервис её не додумывает.</div>', unsafe_allow_html=True)
    # Записи архива, не вошедшие в расчёт. После стыка с адаптером A2 идентификаторы имеют вид
    # «источник:выпуск:хеш[:тип]», а причин две семьи: отсечка по времени публикации и содержание
    # самой записи. Смешивать их нельзя — иначе разбор, где отсечки нет вовсе, показывал
    # «исключено 81 отсечкой». Поэтому обе колонки печатаются явно, и разбор тоже видит свой список.
    _excl_arch = list((S.get('history') or {}).get('excluded_by_archive') or [])
    _excl_all = [(x, True) for x in R.excluded] + [(x, False) for x in _excl_arch]
    if mode != 'live' and _excl_all:
        _n_cut = sum(1 for x, _ in _excl_all if excl_group_ru(x.partition(': ')[2]) == 'время публикации или доступность')
        with st.expander('Не вошло в расчёт: %s %s%s'
                         % (nbsp_thousands(len(_excl_all)), plural_ru(len(_excl_all), ('запись', 'записи', 'записей')),
                            (' — из них %s по времени публикации' % nbsp_thousands(_n_cut)) if _n_cut else ''),
                         expanded=False):
            st.caption(('Записи архива, опубликованные позже отсечки или без времени публикации, и записи, не подходящие '
                        'по содержанию; ни одна не участвует в расчёте.' if mode == 'history_forecast' else
                        'В этом режиме ограничения по времени публикации нет: записи не вошли в расчёт по содержанию — '
                        'не то событие, не тот прибор или неразобранное тело сообщения.')
                       + (' Сначала итог по источникам и причинам, ниже — записи поимённо '
                          '(однотипные свёрнуты в одну строку).' if pro else ''))
            _groups: dict[tuple[str, str, str], list[str]] = {}
            for x, _ in _excl_all:
                rid_, _, reason_ = x.partition(': ')
                _groups.setdefault((rid_.split(':')[0], excl_reason_ru(reason_), excl_group_ru(reason_)), []).append(rid_)
            st.dataframe([{'источник': source_ru(k_[0]), 'почему не в расчёте': k_[2], 'причина': k_[1], 'записей': len(v_),
                           'первый выпуск': record_release_ru(v_[0]), 'последний выпуск': record_release_ru(v_[-1])}
                          for k_, v_ in sorted(_groups.items(), key=lambda kv: -len(kv[1]))], width='stretch', hide_index=True,
                         column_config={'причина': st.column_config.TextColumn(width='large')})
            if pro:               # записи поимённо — только на профессиональном уровне (U5)
                _named = ['- %s — %s' % (record_ru(rid_), k_[1])
                          for k_, v_ in sorted(_groups.items(), key=lambda kv: -len(kv[1])) if len(v_) <= 5
                          for rid_ in v_]
                st.write('\n'.join(_named[:200]) + ('\n- …' if len(_named) > 200 else '') if _named else
                         'Каждая группа выше содержит больше пяти однотипных записей — поимённый список не печатается.')
    # Карта покрытия каналов от адаптера истории (A2) и версии использованных записей.
    # Ключ снимка — 'coverage_map'; 'coverage' оставлен как прежнее имя на случай старого снимка.
    _hist = S.get('history') or {}
    _hcov = _hist.get('coverage_map') or _hist.get('coverage') or {}
    _hver = _hist.get('source_versions') or {}
    if pro and (_hcov or _hver):
        with st.expander('История: покрытие каналов и версии записей', expanded=False):
            if _hcov:
                st.dataframe(coverage_rows_ru(_hcov), width='stretch', hide_index=True,
                             column_config={'причина': st.column_config.TextColumn(width='large')})
                st.caption('Доля горизонта окон, для которой у канала есть данные. Канал без покрытия объявляется '
                           'отсутствующим, а не заполняется нулём.')
            if _hver:
                st.caption('Версий записей источников: %s — по одной на каждую использованную запись. '
                           'Полный перечень с хешами — в манифесте выгрузки.'
                           % fmt(sum(len(v or {}) for v in _hver.values())))
    if pro and tm['provenance'].get('limitations'):
        with st.expander('Орбита: происхождение и ограничения', expanded=False):
            st.write('\n'.join('- ' + limit_ru(x) for x in tm['provenance']['limitations']))
            st.caption('Ниже — сырая запись источника орбиты, на языке источника, без нашего перевода.')
            st.json({k: v for k, v in tm['provenance'].items() if k != 'limitations'}, expanded=False)
    c1, c2 = st.columns(2)
    c1.download_button('Скачать расчёт (JSON)', json.dumps({**S, 'git_commit': _git_sha()}, ensure_ascii=False, indent=1, default=str),
                       file_name=_fname + '.json', mime='application/json', width='stretch', key='dl_json')
    c2.download_button('Скачать архив: отчёт, запрос, факторы, сырые записи (ZIP)', _zip,
                       file_name=_fname + '.zip', mime='application/zip', width='stretch', key='dl_zip')
    if pro:
        st.caption('Имя файла: режим, анализируемый момент, время расчёта. Файл содержит запрос, снимок и сырые записи; '
                   'воспроизведение — scripts/replay_example.py. Экран и выгрузка построены из одного снимка; версия алгоритма %s.' % ALGO_VERSION)
    else:
        st.caption('В файле — запрос, расчёт и сырые записи источников: то же, что на экране. Версия алгоритма %s.' % ALGO_VERSION)
    if pro:
        st.caption('Слои: %s; %s; %s.' % (ORBIT_SRC.split(':')[0], SRC_LAYER.split(' — ')[0], HIST_SRC.split(' — ')[0]))

if pro:
    with tabs[6]:
        from collections import Counter
        grid_vals = list(rob.preferred_starts.values())
        rank_vals = list(rob.ranking_by_grid.values())
        main_pref = rec.preferred.start_utc.isoformat() if rec.preferred else None
        counts = Counter(grid_cell_ru(v, windows_ru) for v in grid_vals)
        st.markdown('**Устойчивость вердикта на сетке порогов (О7).** Сетка: порог аномалии |B| %s нТл × канал захваченных протонов '
                    'от %s МэВ. В каждой ячейке два исхода: лучшее окно по ранжированию без допуска и предпочтительное окно с итоговым '
                    'допуском равнозначности (%s мин, ×%s по флюенсу — формула (9)). ' % (
                        ' / '.join(nbsp_thousands(x) for x in rob.grid[0]), ' / '.join(fmt(float(x)) for x in rob.grid[1]),
                        fmt(round(rob.tol_min)), fmt(round(rob.tol_ratio, 2))) + (
                        '**Сетка исход не меняет**: ни в одной ячейке автоматический выбор не делается.'
                        if main_pref is None and all(v is None for v in grid_vals) else
                        '**Вердикт устойчив на сетке**: во всех ячейках один и тот же исход.' if rob.stable else
                        '**Вердикт меняется на сетке** — рекомендация чувствительна к настройке порогов.') + (
                        ' Лучшее окно без допуска %s.' % ('одно на всей сетке' if rob.ranking_stable else 'меняется по ячейкам')
                        if any(v is not None for v in rank_vals) else ''))
        st.dataframe([{'порог |B|, нТл': nbsp_thousands(k[0]), 'канал, МэВ от': fmt(float(k[1])),
                       'лучшее без допуска': grid_cell_ru(rob.ranking_by_grid.get(k), windows_ru) if rob.ranking_by_grid.get(k)
                       else 'нет (все окна под условием или отказ)',
                       'предпочтительное с допуском': grid_cell_ru(v, windows_ru)}
                      for k, v in rob.preferred_starts.items()], width='stretch', hide_index=True)
        st.markdown('<div class="tcap">Таблица 5. Устойчивость выбора на сетке порогов: два исхода в каждой ячейке.</div>',
                    unsafe_allow_html=True)
        # О7: польза дополнительной функции показана сравнением — что ответил бы сервис без сетки
        # и нулевого допуска и что он отвечает с ними. Оба ответа взяты из того же расчёта (R4-9).
        _gain = robustness_gain_ru(rec, rob, windows_ru, th.saa_B_threshold_nT, th.e_min_MeV)
        if _gain:
            st.markdown('<div class="small">%s</div>' % _gain, unsafe_allow_html=True)
        st.caption('Исход с допуском на сетке: %s. Основной расчёт: %s. Допуск: %s.' % (
            '; '.join('%s — %s из %s ячеек' % (lbl, fmt(n), fmt(len(grid_vals))) for lbl, n in counts.most_common()),
            ('предпочтительное окно %s' % windows_ru.get(rec.preferred.start_utc, '')) if rec.preferred else
            'без предпочтительного окна (%s)' % VERDICT_TITLE.get(rec.verdict, rec.verdict).lower(),
            frac_ru(rec.tolerance_basis)))
        if rob.diff_by_thr or rob.ratio_by_e:
            rc1, rc2 = st.columns(2)
            if rob.diff_by_thr:
                rc1.dataframe([{'порог |B|, нТл': nbsp_thousands(k), 'разность минут в аномалии (второе − лучшее), мин': fmt(v)}
                               for k, v in rob.diff_by_thr.items()],
                              width='stretch', hide_index=True)
            if rob.ratio_by_e:
                rc2.dataframe([{'канал, МэВ от': fmt(float(k)), 'отношение флюенсов (второе / лучшее)': fmt(v)}
                               for k, v in rob.ratio_by_e.items()],
                              width='stretch', hide_index=True)
            rc1.caption('Разброс разности по порогу: %s мин → допуск по минутам не меньше него. Разброс отношения по каналу: '
                        '×%s → допуск по флюенсу не меньше него. Формальная запись — вкладка «Методика», формула (9).'
                        % (fmt(round(rob.diff_spread_min, 1)), fmt(round(rob.fluence_ratio_spread, 2))))
        st.markdown('**Нормы — справочный контекст, не вердикт по дозе.** Сервис не вычисляет дозу человека: для этого нужны '
                    'модели защиты и ткани, которых в обязательной части нет. Формула предела — вкладка «Методика», формула (10).')
        st.dataframe(norms_rows(T_months), width='stretch', hide_index=True)
        st.markdown('<div class="tcap">Таблица 6. Нормы и шкалы как контекст: значение, источник, применимость.</div>',
                    unsafe_allow_html=True)
