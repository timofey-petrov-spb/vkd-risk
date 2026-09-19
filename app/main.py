# -*- coding: utf-8 -*-
"""Экран сервиса «ВКД-Риск» — один путь пользователя (О5): запрос в боковой панели →
ответ первым (вердикт) → окна-кандидаты карточками → лента времени → вкладки с объяснениями,
факторами, картой, наблюдениями и прогнозами, данными и выгрузкой.

Экран ничего не считает: всё берётся из одного снимка расчёта app.compute.run (Т7, Т8).
Два уровня: «Оперативный» показывает решение и условия, «Профессиональный» — все величины,
пороги, устойчивость, нормы и происхождение записей. На экране нет идентификаторов кода:
методы, типы событий, источники и ограничения модулей переводятся словарями app.ui.
"""
from __future__ import annotations

import json
import logging
import traceback
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import streamlit as st

from app.compute import ALGO_VERSION, HIST_SRC, ORBIT_SRC, SRC_LAYER, goes_latest, kp_latest, noaa_latest, run, tle_latest, validate_request
from app.export import _git_sha, build_zip
from app.norms import norms_rows, s_level
from app.obs import forecast_panel, observations_panel
from app.ui import (COV_RU, CSS, MECH_RU, METHOD_RU, STRICT_RU, coverage_reasons, event_kind_ru, fmt, head, kind_pill,
                    dedup_clauses, frac_ru, grid_cell_ru, limit_ru, pill, short_reason, source_issues, source_name_ru, source_short,
                    spread_offsets, status_ru, strip, tle_origin, verdict_panel, window_card, BOOL_RU)
from app.viz import ground_track, timeline, window_bars
from vkd.config import section as _settings_section
from vkd.explain.cards import KIND_RU
from vkd.windows.compare import Thresholds
from vkd.windows.scenario import Scenario

UI = _settings_section('ui')          # умолчания элементов управления — config/settings.toml (Т7)
MODE_IDS = {'Текущая обстановка': 'live', 'Исторический разбор': 'history_review', 'Прогноз из прошлого': 'history_forecast'}
SEV_ICON = {'critical': '🔴', 'limiting': '🟠', 'info': '○'}
ARCHIVE_FROM, ARCHIVE_TO = datetime(2024, 5, 1, tzinfo=timezone.utc), datetime(2024, 7, 1, tzinfo=timezone.utc)
LOG = logging.getLogger('vkd.app')

st.set_page_config(page_title='ВКД-Риск', page_icon='🛰', layout='wide', initial_sidebar_state='expanded')
st.markdown(CSS, unsafe_allow_html=True)
st.session_state.setdefault('fetch_nonce', 0)     # Т6: обновление данных — сессионное, не st.cache_data.clear()

# ================================================================= боковая панель: запрос
with st.sidebar:
    st.markdown('### ВКД-Риск')
    level = st.radio('Уровень интерфейса', ['Оперативный', 'Профессиональный'], index=0, horizontal=True, key='level',
                     help='Оперативный — решение и условия; профессиональный — все величины, пороги, устойчивость, происхождение.')
    pro = level == 'Профессиональный'
    mode_ru = st.radio('Режим', list(MODE_IDS), index=0, key='mode',
                       help='Текущая обстановка — живые источники. Исторический разбор — весь архив мая–июня 2024. '
                            'Прогноз из прошлого — только записи, опубликованные до отсечки.')
    mode = MODE_IDS[mode_ru]
    if mode == 'live':
        t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        st.caption('Начало периода поиска: **%s UTC** (сейчас)' % t0.strftime('%Y-%m-%d %H:%M'))
    else:
        d = st.date_input('Дата (1 мая — 30 июня 2024)', value=datetime(2024, 5, 10).date(), key='hist_date',
                          min_value=ARCHIVE_FROM.date(), max_value=datetime(2024, 6, 30).date())
        hh = st.slider('Час начала периода, UTC', 0, 23, 12, key='hist_hour')
        t0 = datetime(d.year, d.month, d.day, hh, tzinfo=timezone.utc)
        if mode == 'history_forecast':
            st.caption('Отсечка публикации: **%s UTC** — позже ничего не используется.' % t0.strftime('%Y-%m-%d %H:%M'))
    st.markdown('**Окно работ**')
    duration_min = st.slider('Длительность ВКД, мин', 60, 480, int(UI.get('duration_min', 360)), step=30, key='duration',
                             help='Постановка: 60…480 мин. Сокращение длительности — изменение плана, не улучшение обстановки.')
    search_min = st.slider('Период поиска начала ВКД, мин', 60, 1440, max(60, int(UI.get('search_min', 720))), step=60, key='search',
                           help='В этом периоде размещаются начала окон-кандидатов; постановка — до 1440 мин (сутки).')
    n_windows = st.radio('Окон для сравнения', [2, 3], horizontal=True, key='n_windows')
    OFF_STEP = 30
    _def_off = list(UI.get('window_offsets_min', [0, 240]))
    # период сжали: прежние сдвиги в него не помещаются. Пересчитываем их ДО создания ползунков,
    # иначе два сдвига сходятся в один, запрос становится недопустимым и экран останавливается (U1).
    _search_prev = st.session_state.get('_search_prev')
    recalc = []
    if _search_prev is not None and search_min < _search_prev:
        cur = []
        for i in range(n_windows):
            v = st.session_state.get('w%d' % i)
            want = int(_def_off[i]) if i < len(_def_off) else i * 240
            cur.append(int(v) if v is not None else min(want, search_min))
        new = [min(v, search_min) for v in cur]
        if len(set(new)) != len(new):
            new = spread_offsets(new, search_min, OFF_STEP)
            recalc = list(new)
        if new != cur:
            for i, v in enumerate(new):
                st.session_state['w%d' % i] = int(v)
    offsets_in = []
    for i in range(n_windows):
        want = int(_def_off[i]) if i < len(_def_off) else i * 240
        # значение по умолчанию — только для первого показа: у ползунка с уже сохранённым положением
        # его задавать нельзя (Streamlit пишет предупреждение и берёт сохранённое)
        _kw = {} if ('w%d' % i) in st.session_state else {'value': min(want, search_min)}
        offsets_in.append(st.slider('Сдвиг начала окна %d, мин после начала периода' % (i + 1), 0, search_min,
                                    step=30, key='w%d' % i, **_kw))
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
            disabled = {s: _SRC_STATE[st.selectbox({'goes': 'GOES, протоны ≥10 МэВ', 'kp': 'Kp (GFZ)', 'noaa': 'Прогноз NOAA'}[s], list(_SRC_STATE), key='dis_' + s,
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
            st.caption('Источники режима: архив уведомлений DONKI за 01.05–30.06.2024, орбита OEM NASA/JSC, выпуски NOAA до отсечки; '
                       'Kp — окончательный ряд GFZ по 3-часовым интервалам%s. Живые наблюдения GOES и Kp в этом режиме не запрашиваются.'
                       % (' (в разборе; в строгом режиме исключён: времени публикации по интервалам нет)' if mode == 'history_forecast'
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
    if mode == 'history_forecast':
        if sc_delay or sc_sep_on or sc_kp_on:
            st.info('Сценарий «Что если» отключён для строгого прогноза. Для моделирования выберите исторический разбор или текущую обстановку.')
        scenario = Scenario('none')
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
def _fetch_all(dis_goes: bool | str, dis_kp: bool | str, nonce: int, dis_noaa: bool | str = False):
    """Кеш 5 мин против повторных запросов при каждом движении ползунка; nonce — счётчик обновления сессии."""
    return goes_latest(disabled=dis_goes), kp_latest(disabled=dis_kp), tle_latest(disabled=False), noaa_latest(disabled=dis_noaa)


horizon_min = search_min + duration_min
if mode != 'live' and t0 + timedelta(minutes=horizon_min + scenario.work_delay_min) > ARCHIVE_TO:
    st.warning('Горизонт выходит за основной период кейса (май–июнь 2024). Покрытие проверяется по фактическим '
               'интервалам каждого источника; наличие нескольких соседних суток не гарантирует полноту всех линий.')
try:                              # границы постановки проверяются до любого запроса (Т7): сообщение зрителю, расчёта нет
    validate_request(mode, t0, duration_min, search_min, offsets)
except ValueError as e:
    st.error('Запрос вне границ постановки: %s. Измените запрос в боковой панели.' % e)
    st.stop()
try:
    if mode == 'live':
        with st.spinner('Источники: GOES, Kp, TLE — до 6 с на адрес при живом запросе, затем резервы и кеш…'):
            fetched = _fetch_all(disabled['goes'], disabled['kp'], int(st.session_state['fetch_nonce']), disabled.get('noaa', False))
    else:
        fetched = None            # архивные режимы: живые источники не запрашиваются вовсе — входы только из архива (Т1, Т6)
    with st.spinner('Траектория, поле, оценка окон, устойчивость…'):
        R = run(mode, t0, duration_min, search_min, offsets, disabled=disabled, thresholds=th,
                scenario=scenario, T_months=T_months, fetched=fetched)
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

# ================================================================= шапка и полоса состояния
st.markdown(head('ВКД-Риск', 'внешняя обстановка на траектории МКС и выбор окна ВКД · %s · все времена UTC' % mode_ru), unsafe_allow_html=True)
src = S['sources']
tm = S['trajectory_meta']
orbit_kind = 'crit' if meta is None else ('ok' if tm['strictness'] == 'strict' else 'warn')
orbit_txt = ('недоступна' if meta is None else '%s%s' % (METHOD_RU.get(meta.method, meta.method),
                                                       ' · ' + STRICT_RU.get(tm['strictness'], tm['strictness']) if tm['strictness'] != 'strict' else ''))
items = [('Режим', mode_ru, 'calc' if mode == 'live' else 'fc'), ('Орбита', orbit_txt, orbit_kind)]
if meta and mode == 'live' and meta.epoch_utc:
    age_h = src['orbit'].get('age_h')
    items.append(('TLE', 'эпоха %s · давность %s · %s' % (meta.epoch_utc.strftime('%d.%m %H:%MZ'),
                                                         ('%.0f ч' % age_h) if age_h is not None else '—', tle_origin(tm.get('tle_fetch_status'))),
                  'warn' if (age_h is not None and age_h > 24) or src['orbit'].get('from_cache') else 'ok'))
elif meta and meta.created_utc:
    items.append(('OEM', 'создан %s · за %.0f ч до начала периода' % (meta.created_utc.strftime('%d.%m %H:%MZ'),
                                                                     (t0 - meta.created_utc).total_seconds() / 3600), None))
if mode == 'live':
    g, k = R.goes, R.kp
    g_src, k_src = src['noaa_swpc_goes'], src['gfz_kp']
    if g:
        items.append(('GOES ≥10 МэВ', '%s pfu · %s · %s%s' % (fmt(float(g.value)), s_level(g.value), g.t_utc.strftime('%H:%MZ'), ' · кеш' if g_src.get('from_cache') else ''),
                      'crit' if g.value >= th.goes_p10_priority_pfu else 'warn' if (g.value >= th.goes_p10_warning_pfu or g_src.get('from_cache')) else 'ok'))
    else:
        items.append(('GOES ≥10 МэВ',) + source_short(g_src))
    QUALITY_RU = {'final': 'окончательное', 'preliminary': 'предварительное', 'model': 'модель', 'unknown': 'качество не указано'}
    if k:
        items.append(('Kp', '%s · %s · %s%s' % (fmt(float(k.value)), QUALITY_RU.get(k.quality, k.quality), k.t_utc.strftime('%d.%m %H:%MZ'), ' · кеш' if k_src.get('from_cache') else ''),
                      'crit' if k.value >= th.kp_check else 'warn' if k_src.get('from_cache') else 'ok'))
    else:
        items.append(('Kp',) + source_short(k_src))
else:
    items.append(('Отсечка' if mode == 'history_forecast' else 'Начало периода', t0.strftime('%Y-%m-%d %H:%MZ'), None))
    if mode == 'history_forecast':
        items.append(('После отсечки не использовано', '%d записей' % len(R.excluded), None))
    else:
        items.append(('Архив', 'весь, без отсечки — разбор после факта', None))
    if kp_off_hist:
        items.append(('Kp', 'исключён пользователем', 'crit'))
    elif R.kp is not None and R.kp.source_id != 'scenario':
        items.append(('Kp (архив GFZ)', '%s · интервал до %s · давность %s мин' % (
            fmt(float(R.kp.value)), R.kp.t_utc.strftime('%d.%m %H:%MZ'), src['gfz_kp'].get('age_min', '—')),
            'crit' if R.kp.value >= th.kp_check else 'warn' if (src['gfz_kp'].get('age_min') or 0) > th.kp_max_age_min else 'ok'))
    elif mode == 'history_forecast':
        items.append(('Kp', 'наблюдения до отсечки нет', 'warn'))
if S['is_simulated']:
    items.append(('Сценарий', 'моделируемые значения', 'warn'))
st.markdown(strip(items), unsafe_allow_html=True)
if meta is None:
    st.error('**Орбита недоступна.** %s Оценка без траектории невозможна: покрытие обязательной линии отсутствует, '
             'рекомендации нет. Заглушка не подставляется.' % status_ru(tm['status'], pro))
issues = source_issues(src, th, mode, kp_excluded_hist=kp_off_hist, tle_fetch=tm.get('tle_fetch_status'), pro=pro)
if issues:
    st.warning('**Состояние источников:**\n' + '\n'.join('- ' + x for x in issues))

# ================================================================= ответ первым
missing_ru = []
for m_ in rec.missing:
    extra = ''
    if 'космопогода' in m_ and mode == 'live' and 'исключён' in (src['noaa_swpc_goes'].get('status') or ''):
        extra = ' — GOES исключён пользователем'
    elif 'космопогода' in m_ and mode != 'live':
        extra = ' — наблюдений GOES в архиве нет, а горизонт выходит за каталог DONKI (01.05–30.06.2024)' \
            if t0 + timedelta(minutes=horizon_min) > ARCHIVE_TO else ' — линия без данных на горизонте'
    missing_ru.append(m_ + extra)
any_cond = any(m.needs_check for a in R.assessments for m in a.mechanisms)
# политика прототипа целиком — один раз, во вкладке «Объяснения»; здесь только указатель (U5)
policy_short = 'Окна с условиями не выбираются автоматически — правило команды, не норма (вкладка «Объяснения»).' if any_cond else None
c_main, c_btn = st.columns([6, 1.5])
c_main.markdown(verdict_panel(rec, S, windows_ru, assessments=R.assessments, pro=pro, plan_change=plan_change,
                              missing_ru=missing_ru, policy_short=policy_short), unsafe_allow_html=True)
_fname = 'vkd_risk_%s_%s_calc%s' % (mode, t0.strftime('%Y%m%dT%H%M'), now.strftime('%Y%m%dT%H%M'))
_zip = build_zip(S, R.raw_records)
c_btn.download_button('Скачать отчёт (ZIP)', _zip, file_name=_fname + '.zip', mime='application/zip', width='stretch', key='dl_top')
c_btn.caption('отчёт, запрос, факторы, сырые записи')
cols = st.columns(len(R.assessments))
for i, (col, a) in enumerate(zip(cols, R.assessments)):
    col.markdown(window_card(i, a, best=(rec.preferred is not None and rec.preferred.start_utc == a.window.start_utc), mode=mode),
                 unsafe_allow_html=True)
st.markdown('<div class="legend">Происхождение: %s %s %s — минуты в аномалии и флюенс по трассе, условия по наблюдениям и датированным прогнозам.</div>'
            % (kind_pill('observation'), kind_pill('external_forecast'), kind_pill('own_calculation')), unsafe_allow_html=True)
if mode == 'history_forecast' and R.verification:
    st.info('**Проверка после отсечки** (в расчёт не входит — только сопоставление прогноза с фактом): %s. '
            'Подробности — вкладка «Наблюдения и прогнозы».' % R.verification['summary'])

# ================================================================= лента времени
st.subheader('Картина по времени')
kp_obs, goes_obs = [], []
if mode == 'live':
    from app.obs import goes_series, kp_series
    tk, vk = kp_series(R.fetch_status['kp'].raw_path)
    kp_obs = [(t, t + timedelta(hours=3), v) for t, v in zip(tk, vk) if t >= t0 - timedelta(hours=12)]
    tg, vg = goes_series(R.fetch_status['goes'].raw_path)
    goes_obs = [(t, v) for t, v in zip(tg, vg) if t >= t0 - timedelta(hours=12)]
elif mode == 'history_review':
    kp_obs = R.kp_obs or []
st.plotly_chart(timeline(traj, windows, th.saa_B_threshold_nT, t0, horizon_min, R.goes, R.kp, R.events, S.get('forecasts', []), mode,
                         kp_obs=kp_obs, goes_obs=goes_obs, search_min=search_min), width='stretch')
_mid_ru = ('прогноз Kp NOAA по 3-часовым интервалам из выпуска до отсечки' if mode == 'history_forecast' else
           'наблюдения Kp (GFZ) и GOES ≥10 МэВ за последние 12 ч' if mode == 'live' else
           'наблюдения Kp из архива (разбор после факта)')
if pro:
    st.caption('Верх: |B| по трассе (наш расчёт по IGRF), красные полосы — пролёты аномалии, синие — окна-кандидаты. '
               'Середина: %s. Низ: события и прогнозы по типам — положение по вертикали означает тип, не значение.' % _mid_ru)
else:
    st.caption('Верх — |B| на трассе: красным аномалия, синим окна. Середина — %s. Низ — события по типам.'
               % _mid_ru.split(' из выпуска')[0].split(' за последние')[0].split(' (разбор')[0])

# ================================================================= вкладки
tab_names = ['Объяснения', 'Окна и факторы', 'Карта', 'Наблюдения и прогнозы', 'Данные и выгрузка'] + (['Устойчивость и нормы'] if pro else [])
tabs = st.tabs(tab_names)

with tabs[0]:
    _win_labels = ['Окно %d (%s)%s' % (i + 1, a.window.start_utc.strftime('%d.%m %H:%MZ'),
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
        label = '%s %s · %s' % (SEV_ICON.get(c.severity, '•'), c.title, KIND_RU[c.kind])
        with st.expander(label, expanded=(c.severity != 'info')):
            # повтор одной и той же части подписи убираем, обрывки кода — на профессиональный уровень (U2, U5)
            _txt = lambda t: dedup_clauses(status_ru(t, pro))
            st.markdown('**1. Воздействие и значение для ВКД.** ' + frac_ru(c.impact_ru))
            st.markdown('**2. Период.** ' + _txt(c.period_ru))
            st.markdown('**3. Данные и единицы.** ' + _txt(c.data_ru))
            st.markdown('**4. Источник и время публикации.** ' + _txt(c.source_ru))
            st.markdown('**5. Применённое правило или модель.** ' + _txt(c.rule_ru))
            st.markdown('**6. Ограничения и уверенность.** ' + _txt(c.limits_ru))
            st.markdown('**7. Происхождение.** ' + KIND_RU[c.kind])
            links, no_link = [], 0
            for rid in c.record_ids:
                rec_ = R.raw_records.get(rid) or {}
                u = (rec_.get('url') or rec_.get('link') or rec_.get('messageURL')) if isinstance(rec_, dict) else None
                if pro:                    # идентификатор записи виден только на профессиональном уровне (U5)
                    links.append('[%s](%s)' % (rid, u) if u else '`%s`' % rid)
                elif u:
                    links.append('[первоисточник %d](%s)' % (len(links) + 1, u))
                else:
                    no_link += 1
            if links or no_link:
                _n = 12 if pro else 6          # оперативному уровню хватает нескольких ссылок (U5)
                st.markdown('**Первоисточник:** ' + ', '.join(links[:_n])
                            + (' … ещё %d' % (len(links) - _n) if len(links) > _n else '')
                            + (('%sзаписей без ссылки: %d' % ('; ' if links else '', no_link)) if no_link else ''))
            if pro:
                _raw = [rid for rid in c.record_ids[:12] if rid in R.raw_records]
                if _raw:
                    st.caption('Ниже — сырая запись источника, на языке источника: как её опубликовал NOAA, NASA или GFZ, '
                               'без нашего перевода и без изменений.')
                for rid in _raw:
                    st.json(R.raw_records[rid], expanded=False)
    with st.expander('Политика прототипа и чего не заявляем', expanded=False):
        st.markdown(S['policy_note'])
        st.markdown('Чего сервис не заявляет:\n'
                    '- допустимость реального выхода — за уполномоченными специалистами;\n'
                    '- вероятность разгерметизации и попадания в космонавта не вычисляется;\n'
                    '- доза человека не вычисляется; поток GOES не переносится на станцию без обрезания;\n'
                    '- неизвестное не превращается ни в нуль, ни в «благоприятно»: отсутствие данных объявляется.')

with tabs[1]:
    st.plotly_chart(window_bars(R.assessments), width='stretch')
    # таблица сравнения: строки — величины, покрытие, условия; столбцы — окна (O5-7)
    col_names = ['Окно %d (%s)' % (i + 1, a.window.start_utc.strftime('%d.%m %H:%MZ')) for i, a in enumerate(R.assessments)]
    row_keys: list[tuple[str, str]] = []
    cells: dict[tuple[str, str], list] = {}
    for j, a in enumerate(R.assessments):
        for m in a.mechanisms:
            if not (m.mandatory or m.coverage.value != 'none'):
                continue
            for f in m.factors:
                k_ = ('величина', f.name)
                if k_ not in cells:
                    row_keys.append(k_); cells[k_] = ['—'] * len(R.assessments)
                cells[k_][j] = fmt(f.value, f.unit)
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
    rows = [{'показатель': '%s%s' % (k_[1], '' if k_[0] == 'величина' else ' — ' + k_[0]),
             **{cn: cells[k_][j] for j, cn in enumerate(col_names)}}
            for k_ in sorted(row_keys, key=lambda k: (order[k[0]], row_keys.index(k)))]
    st.dataframe(rows, width='stretch', hide_index=True,
                 column_config={'показатель': st.column_config.TextColumn(width='medium'),
                                **{cn: st.column_config.TextColumn(width='large') for cn in col_names}})
    if pro:
        st.caption('Порядок сравнения — пять шагов: охват → условия → сравнение по каждому механизму → сведение → допуск '
                   '(подробно — README, раздел 5). Допуск равнозначности: %s.' % frac_ru(rec.tolerance_basis))
    else:
        st.caption('Порядок сравнения: охват → условия → сравнение по механизмам → сведение → допуск равнозначности.')

with tabs[2]:
    if traj:
        with st.spinner('Область аномалии по IGRF на сетке 4°…'):
            st.plotly_chart(ground_track(traj, windows, th.saa_B_threshold_nT, t0), width='stretch')
        if pro:
            st.caption('Область аномалии — наш расчёт |B| по IGRF на средней высоте трассы; трасса за весь горизонт серым, '
                       'окна-кандидаты цветом, точки трассы в аномалии красным. Карта показывает, откуда берутся минуты в аномалии.')
        else:
            st.caption('Красным — область аномалии и точки трассы в ней, цветом — окна: откуда берутся минуты в аномалии.')
    else:
        st.write('Трассы нет: орбита недоступна.')

with tabs[3]:
    if mode == 'live':
        obs_fig = observations_panel(R.fetch_status['goes'].raw_path, R.fetch_status['kp'].raw_path, t0)
        if obs_fig is not None:
            st.plotly_chart(obs_fig, width='stretch')
            st.caption('Наблюдения источников за последние дни, не расчёт. Пороги — шкалы NOAA S и G.' + (
                ' GOES меряет на геостационарной орбите. Обрезание — отдельный показатель; локальный поток МКС не рассчитан.'
                if pro else ''))
        else:
            st.write('Рядов наблюдений нет: источники отключены или недоступны.')
        live_fc = forecast_panel(S.get('forecasts', []), t0, horizon_min)
        if live_fc is not None:
            st.plotly_chart(live_fc, width='stretch')
            st.caption('Внешний прогноз NOAA: Kp по 3-часовым интервалам, вероятность S1+ за сутки. Суточная вероятность не является вероятностью за окно ВКД.')
    else:
        archive_goes = S.get('history', {}).get('goes_observations', [])
        if archive_goes:
            st.scatter_chart([{'UTC': datetime.fromisoformat(x['t_utc']), 'GOES ≥10 МэВ, pfu': x['value']}
                              for x in archive_goes], x='UTC', y='GOES ≥10 МэВ, pfu')
            st.caption('Исторический разбор: наблюдённые 5-минутные средние GOES iSWA. Пропуски не заполняются; '
                       'время исторической публикации неизвестно, в строгом прогнозе этот ряд не используется.')
        fc_fig = forecast_panel(S.get('forecasts', []), t0, horizon_min)
        if fc_fig is not None:
            st.plotly_chart(fc_fig, width='stretch')
        for line in S.get('forecasts', []):
            if line['release_id']:
                u = (R.raw_records.get(line['record']) or {}).get('metadata', R.raw_records.get(line['record']) or {}).get('url') if line.get('record') else None
                pub = (line['published_utc'] or '')[:16].replace('T', ' ')
                rel = ('[выпуск от %s UTC](%s)' % (pub, u)) if u else 'выпуск от %s UTC' % pub
            else:
                rel = 'выпуска до отсечки в архиве нет'
            st.markdown('%s **%s** — %s' % (pill(line['status_ru'], 'ok' if line['status'] == 'full' else 'warn' if line['status'] == 'partial' else 'none'),
                                            line['label'], rel), unsafe_allow_html=True)
        st.caption('Внешний прогноз, не наблюдение. Суточные вероятности относятся к суткам, а не к окну ВКД; прогноз Kp — по '
                   '3-часовым интервалам.' + (' Отбор выпуска по времени публикации.' if pro else ''))
    if mode == 'history_forecast' and R.verification:
        ver = R.verification
        with st.expander('Проверка после отсечки — что наблюдалось потом (в расчёт не входит)', expanded=True):
            st.markdown('**%s.**' % frac_ru(ver['summary']))
            st.caption('%s. Отсечка %s, горизонт до %s UTC.%s'
                       % (ver['note'], ver['cutoff_utc'][:16].replace('T', ' '), ver['horizon_to_utc'][:16].replace('T', ' '),
                          ' Наблюдения Kp — окончательный ряд GFZ по 3-часовым интервалам; '
                          'события — уведомления DONKI, опубликованные после отсечки.' if pro else ''))
            vc1, vc2 = st.columns(2)
            if ver.get('kp_obs'):
                vc1.dataframe([{'интервал с': x['from_utc'][5:16].replace('T', ' '), 'по': x['to_utc'][11:16], 'Kp': fmt(x['kp']),
                                'условие проверки': 'да' if x['kp'] >= th.kp_check else 'нет'} for x in ver['kp_obs']],
                              width='stretch', hide_index=True)
            else:
                vc1.write('Наблюдений Kp на горизонте в архиве нет.')
            if ver.get('events'):
                vc2.dataframe([{'тип': event_kind_ru(x['kind']), 'публикация': x['published_utc'][5:16].replace('T', ' '),
                                'начало': (x['start_utc'] or '')[5:16].replace('T', ' ') or '—', 'примечание': frac_ru(x['note'])} for x in ver['events']],
                              width='stretch', hide_index=True, column_config={'примечание': st.column_config.TextColumn(width='large')})
            else:
                vc2.write('Уведомлений о протонных событиях и бурях после отсечки на горизонте нет.')
    if R.events:
        st.markdown('**События и прогнозы, учтённые на горизонте** (время публикации — из записи источника)')
        ev_rows = []
        for e in sorted(R.events, key=lambda e: (e.published_utc or e.start_utc or datetime.max.replace(tzinfo=timezone.utc))):
            rec_ = R.raw_records.get(e.raw_record_id) or {}
            u = (rec_.get('url') or rec_.get('link') or rec_.get('messageURL')) if isinstance(rec_, dict) else None
            row = {'тип': event_kind_ru(e.kind_of_event) + (' (сценарий)' if e.is_simulated else ''),
                   'происхождение': KIND_RU[e.kind],
                   'начало / приход': e.start_utc.strftime('%d.%m %H:%MZ') if e.start_utc else '—',
                   'публикация': e.published_utc.strftime('%d.%m %H:%MZ') if e.published_utc else 'нет — моделируемое' if e.is_simulated else 'нет',
                   'примечание': frac_ru(e.note or ''), 'ссылка': u}
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

with tabs[4]:
    st.markdown('**Источники этого расчёта** — состояние, получение, давность; каждый фактор прослеживается до записи в выгрузке.')
    src_rows = []
    for k, v in src.items():
        if k.startswith('_'):
            continue
        static = k in ('ost1044_belts', 'ecss_grun')
        hist_obs = mode != 'live' and k in ('noaa_swpc_goes', 'gfz_kp')     # живые наблюдения в архивных режимах не используются
        status = v.get('status') or ''
        state, kind = ('таблица встроена', 'ok') if static else source_short(v)
        if k == 'orbit':
            if mode == 'live' and tm.get('tle_fetch_status'):
                status = '%s; TLE: %s' % (status, tm['tle_fetch_status'])
            state, kind = STRICT_RU.get(v.get('strictness'), v.get('strictness') or '—'), ('ok' if v.get('strictness') == 'strict' else 'warn')
        elif mode != 'live' and k == 'noaa_swpc_goes':
            status, state = 'архива наблюдений GOES за 2024 нет — линия без наблюдения, объявлено', 'нет архива'
        elif mode != 'live' and k == 'gfz_kp':
            state = ('исключён' if kp_off_hist else 'сценарий' if (R.kp is not None and R.kp.source_id == 'scenario')
                     else 'архив' if R.kp is not None else 'нет до отсечки' if mode == 'history_forecast' else 'нет в архиве')
            hist_obs = R.kp is None or R.kp.source_id == 'scenario'      # есть запись архива — показываем её время и давность от t0
        elif k.startswith('noaa_forecast_'):
            state = 'архив выпусков'
        elif k == 'donki_archive':
            state = 'архив, отбор по публикации' if mode == 'history_forecast' else 'архив, весь'
        age = v.get('age_min')
        if k == 'orbit' and v.get('age_h') is not None:
            age = v['age_h'] * 60
        src_rows.append({'источник': source_name_ru(k), 'роль': v.get('role'), 'состояние': state,
                         'живой запрос': BOOL_RU.get(v.get('live_ok'), '—') if not static and mode == 'live' else '—',
                         'из кеша': BOOL_RU.get(v.get('from_cache'), '—') if not static and mode == 'live' else '—',
                         'получено': 'встроено' if static else '—' if hist_obs else (v.get('fetched_utc') or '—')[:16].replace('T', ' '),
                         'данные на': '—' if hist_obs else (v.get('data_utc') or v.get('epoch_utc') or '—')[:16].replace('T', ' '),
                         'давность, мин': ('%d' % round(age)) if (age is not None and not hist_obs) else '—',
                         'строгость': STRICT_RU.get(v.get('strictness'), '—') if k == 'orbit' else '—',
                         'статус': status_ru(status, pro)})
    st.dataframe(src_rows, width='stretch', hide_index=True,
                 column_config={'статус': st.column_config.TextColumn(width='large'), 'источник': st.column_config.TextColumn(width='medium')})
    if mode == 'history_forecast':
        with st.expander('После отсечки не использовано: %d записей' % len(R.excluded), expanded=False):
            st.caption('Записи архива, опубликованные позже отсечки или без времени публикации; ни одна не участвует в расчёте.'
                       + (' Сначала итог по источникам и причинам, ниже — записи поимённо (интервалы Kp свёрнуты в одну строку).'
                          if pro else ''))
            _EXCL_SRC = {'gfz_kp_archive': 'Kp, окончательный ряд GFZ (3-часовые интервалы)', 'donki_gst': 'DONKI: Kp карточек бурь',
                         'donki_msg': 'DONKI: уведомления', 'donki_enlil': 'DONKI: прогоны WSA-ENLIL', 'donki_sep': 'DONKI: карточки протонных событий',
                         'donki_flr': 'DONKI: вспышки', 'donki_cme': 'DONKI: выбросы'}
            _groups: dict[tuple[str, str], list[str]] = {}
            for x in R.excluded:
                rid_, _, reason_ = x.partition(': ')
                cat_ = ('без времени публикации' if 'неизвестно' in reason_ else 'опубликовано после отсечки' if 'после отсечки' in reason_ else reason_)
                _groups.setdefault((rid_.split('#', 1)[0], cat_), []).append(rid_)
            st.dataframe([{'источник': _EXCL_SRC.get(k_[0], k_[0]), 'причина': k_[1], 'записей': len(v_),
                           'первая': v_[0].split('#', 1)[-1], 'последняя': v_[-1].split('#', 1)[-1]}
                          for k_, v_ in sorted(_groups.items(), key=lambda kv: -len(kv[1]))], width='stretch', hide_index=True)
            _rest = [x for x in R.excluded if not x.startswith('gfz_kp_archive#')]
            _kp_n = len(R.excluded) - len(_rest)
            if pro:               # записи поимённо — только на профессиональном уровне (U5)
                st.write(('- интервалы Kp GFZ: %d записей, одна причина — ряд окончательный, времени публикации по интервалам нет\n' % _kp_n if _kp_n else '')
                         + '\n'.join('- ' + x for x in _rest[:200]) + ('\n- …' if len(_rest) > 200 else ''))
            elif _kp_n:
                st.write('- интервалы Kp GFZ: %d записей, одна причина — ряд окончательный, времени публикации по интервалам нет' % _kp_n)
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
    with tabs[5]:
        from collections import Counter
        grid_vals = list(rob.preferred_starts.values())
        rank_vals = list(rob.ranking_by_grid.values())
        main_pref = rec.preferred.start_utc.isoformat() if rec.preferred else None
        counts = Counter(grid_cell_ru(v, windows_ru) for v in grid_vals)
        st.markdown('**Устойчивость вердикта на сетке порогов (О7).** Сетка: порог аномалии |B| %s нТл × канал захваченных протонов '
                    'от %s МэВ. В каждой ячейке два исхода: лучшее окно по ранжированию без допуска и предпочтительное окно с итоговым '
                    'допуском равнозначности (%.0f мин, ×%.2f по флюенсу). ' % (
                        '/'.join('%.0f' % x for x in rob.grid[0]), '/'.join('%g' % x for x in rob.grid[1]), rob.tol_min, rob.tol_ratio) + (
                        '**Сетка исход не меняет**: ни в одной ячейке автоматический выбор не делается.'
                        if main_pref is None and all(v is None for v in grid_vals) else
                        '**Вердикт устойчив на сетке**: во всех ячейках один и тот же исход.' if rob.stable else
                        '**Вердикт меняется на сетке** — рекомендация чувствительна к настройке порогов.') + (
                        ' Лучшее окно без допуска %s.' % ('одно на всей сетке' if rob.ranking_stable else 'меняется по ячейкам')
                        if any(v is not None for v in rank_vals) else ''))
        st.dataframe([{'порог |B|, нТл': '%.0f' % k[0], 'канал, МэВ от': '%g' % k[1],
                       'лучшее без допуска': grid_cell_ru(rob.ranking_by_grid.get(k), windows_ru) if rob.ranking_by_grid.get(k)
                       else 'нет (все окна под условием или отказ)',
                       'предпочтительное с допуском': grid_cell_ru(v, windows_ru)}
                      for k, v in rob.preferred_starts.items()], width='stretch', hide_index=True)
        st.caption('Исход с допуском на сетке: %s. Основной расчёт: %s. Допуск: %s.' % (
            '; '.join('%s — %d из %d ячеек' % (lbl, n, len(grid_vals)) for lbl, n in counts.most_common()),
            ('предпочтительное окно %s' % windows_ru.get(rec.preferred.start_utc, '')) if rec.preferred else
            'без предпочтительного окна (%s)' % rec.verdict, rec.tolerance_basis))
        if rob.diff_by_thr or rob.ratio_by_e:
            rc1, rc2 = st.columns(2)
            if rob.diff_by_thr:
                rc1.dataframe([{'порог |B|, нТл': '%.0f' % k, 'разность минут в аномалии (второе − лучшее)': fmt(v)} for k, v in rob.diff_by_thr.items()],
                              width='stretch', hide_index=True)
            if rob.ratio_by_e:
                rc2.dataframe([{'канал, МэВ от': '%g' % k, 'отношение флюенсов (второе / лучшее)': fmt(v)} for k, v in rob.ratio_by_e.items()],
                              width='stretch', hide_index=True)
            rc1.caption('Разброс разности по порогу: %.1f мин → допуск по минутам не меньше него. Разброс отношения по каналу: ×%.2f → допуск по флюенсу не меньше него.'
                        % (rob.diff_spread_min, rob.fluence_ratio_spread))
        st.markdown('**Нормы — справочный контекст, не вердикт по дозе.** Сервис не вычисляет дозу человека: для этого нужны '
                    'модели защиты и ткани, которых в обязательной части нет.')
        st.dataframe(norms_rows(T_months), width='stretch', hide_index=True)
