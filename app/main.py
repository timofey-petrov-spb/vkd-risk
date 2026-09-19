# -*- coding: utf-8 -*-
"""Экран сервиса «ВКД-Риск» — один путь пользователя (О5), сверху вниз, как ответ на вопрос
человека «нам надо выйти» (техзадание одиннадцатого круга, раздел 3.0):

  1 заголовок и назначение → 2 строка задачи (длительность, срок, «Найти окна») →
  3 рекомендация (когда выходить, два предложения «почему» обычными словами, область вывода,
    условия, отчёт) →
  4 глобус (где и когда) → 5 профиль воздействия на сроке (один график и таблица лучших начал) →
  6 что учтено → 7 состояние источников (в нём же приборная полоса) →
  8 разобрать конкретные окна, свёрнуто (прежние карточки, ползунки сдвига и вердикт по ним) →
  9 вкладки: Объяснения, Окна и факторы, Наблюдения и прогнозы, Методика, Данные
  (и Устойчивость и нормы на профессиональном уровне).

Рекомендация читается из ключа `scan` снимка (договор раздела 1a). Ключа нет — перебор не
выполнялся, и экран показывает прежний разбор окон, честно называя, чего не было.

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

from app import backdrop, globe
from app.compute import ALGO_VERSION, HIST_SRC, ORBIT_SRC, SRC_LAYER, run, validate_request
from app.export import _git_sha, build_zip
from app.fetch_guard import LIMIT_MARK, fetch_live_sources, total_deadline_s
from app.norms import norms_rows, s_level
from app.obs import forecast_panel, observations_figure, observations_panel
from app.ui import (BOOL_RU, COLOR_LEGEND, COV_RU, CSS, DECISION_LEGEND, DISABLED_KEY,
                    LIMITS_SECTION_RU, LIVE_DONKI_REGISTRY_ROW,
                    LIVE_NO_EVENTS_RU,
                    MECH_RU, METHOD_BLOCKS, METHOD_RU, PRESET_CHANGED_RU, PRESETS,
                    RELEASE_BY_MODE_RU, RULE_POLICY, RULE_THRESHOLDS, RULE_THRESHOLDS_NOTE, SCOPE_TAB_HINT_RU,
                    SEV_RU, STRICT_RU, TASK_HINT_RU, VERDICT_TITLE,
                    SCAN_SOURCES_RU, accounted_lines, accounted_lines_from_scan, accounted_sources,
                    age_ru, close_cut_parens, coverage_consequence_ru, coverage_reasons, coverage_rows_ru,
                    coverage_scope_ru, dark_figure, dedup_clauses, dose_factor, forecast_label_ru,
                    dt_ru, event_kind_ru, excl_group_ru, excl_reason_ru, factor_value_ru, fmt, formula_ref, frac_ru,
                    grid_cell_ru, head, kind_pill, limit_ru, map_caption, method_source_ru, nbsp_thousands,
                    not_accounted_ru, panel, split_panel_rows,
                    pill, plan_change_ru, plan_state, plural_ru, preset_matches,
                    ratio_ru, raw_record, recommendation_panel, record_label_ru, record_no_url_ru, record_release_ru,
                    record_url, registry_row, ribbon_caption_ru, robustness_gain_ru,
                    saa_note_ru, scan_absent_ru, scan_answer, scan_best_rows, scan_conditions_note_ru,
                    scan_of, screen_text, short_reason,
                    source_issues, source_issues_short_ru, source_name_ru,
                    source_short, spread_offsets,
                    status_ru, timeline_caption, tle_origin, verdict_panel, verification_ru, window_card,
                    windows_ribbon)
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
# Тёмный фон со звёздами и короткие осмысленные анимации (app/backdrop.py). Отдельный
# модуль, а не часть CSS экрана: у него своя проверка контраста на 21 пару и свой
# предел веса разметки. Возвращает вес в байтах и падает сам, если предел превышен.
backdrop.apply(st)
st.session_state.setdefault('fetch_nonce', 0)     # Т6: обновление данных — сессионное, не st.cache_data.clear()


def hm_ru(minutes: int) -> str:
    """«6 ч 00 мин» из минут: аналитик планирует в часах, а не в минутах от начала периода."""
    m = int(minutes)
    return '%d ч %02d мин' % (m // 60, m % 60) if m % 60 else '%d ч' % (m // 60)


def _apply_preset(p: dict) -> None:
    """Пресет запроса (И1): выставляет режим, дату, час и окна одним нажатием и ничего не считает.
    Состояние ползунков меняется ДО их создания в этом же прогоне, поэтому значения применяются сразу."""
    st.session_state['mode'] = p['mode']
    if p['date'] is not None:
        st.session_state['hist_date'] = datetime(*p['date']).date()
        st.session_state['hist_hour'] = int(p['hour'])
    st.session_state['duration'] = int(p['duration_min'])
    # Срок человек называет в ЧАСАХ: «надо выйти в ближайшие 12 ч», а не «в ближайшие 720 мин».
    # Внутри всё считается в минутах, и `_search_prev` остаётся в минутах.
    st.session_state['search_h'] = max(1, int(p['search_min']) // 60)
    st.session_state['_search_prev'] = int(p['search_min'])       # период задан пресетом, пересчёта сдвигов не нужно
    st.session_state['n_windows'] = len(p['offsets_min'])
    for i, off in enumerate(p['offsets_min']):
        st.session_state['w%d' % i] = int(off)
        st.session_state['_off%d' % i] = int(off)                 # R4-1: сдвиги хранятся вне виджета
    st.session_state['_preset'] = p['key']
    # Пресет — это ДРУГОЙ запрос, а не изменение плана внутри прежнего: плашку пересчёта
    # («длительность 360 → 120, ответ изменился») он ставить не должен.
    for _k in ('_plan_state', '_plan_change_ru', '_plan_change_for'):
        st.session_state.pop(_k, None)


# ================================================================= боковая панель: ввод по классам
# Владелец о прежней панели: «однородная каша слева». Ввод разложен по СМЫСЛОВЫМ разделам, у
# каждого — материальная иконка (не эмодзи: эмодзи делают экран детским, а нужен вид рабочего
# решателя). Иконка означает смысл раздела, а не украшает.
#
# Разделов пять, и они отвечают на разные вопросы: КОГДА считаем, ЧЕМ считаем, ЧТО проверяем,
# ПО КАКИМ порогам, КАК показываем. Шестого раздела «задача выхода» в панели нет намеренно:
# длительность и срок — это вопрос человека, и он задаётся строкой наверху главной области
# (раздел 3.1 техзадания). Второй такой же пары полей в панели быть не должно: один параметр —
# один элемент управления.
#
# Пресеты стоят НАД разделами: это быстрый путь целиком, а не класс параметров.
with st.sidebar:
    st.markdown('### ВКД-Риск')
    st.markdown('<div class="sect">Пресеты запроса</div>', unsafe_allow_html=True)
    for _p, _col in zip(PRESETS, st.columns(len(PRESETS))):
        if _col.button(_p['label'].split(',')[0], key='preset_' + _p['key'], width='stretch', help=_p['label']):
            _apply_preset(_p)
    _cur = next((p for p in PRESETS if p['key'] == st.session_state.get('_preset')), None)
    # Подпись печатается ПОСЛЕ сборки запроса: пока она стояла здесь, она утверждала параметры
    # пресета до конца сессии, даже когда пользователь всё перекрутил руками (К4). Само место
    # под подпись занимается сейчас, а текст ставится ниже, когда известны все поля запроса.
    _preset_caption = st.empty()
    # умолчание задаётся только при первом показе: у элемента с сохранённым значением Streamlit
    # предупреждает и всё равно берёт сохранённое (пресет пишет состояние до создания элементов)
    _def = lambda key, kw: ({} if key in st.session_state else kw)
    # Уровень интерфейса стоит в разделе «Вид» — последнем, — но нужен раньше: от него зависит,
    # какие разделы показываются и насколько подробны их подписи. Значение читается из состояния
    # до создания элемента, как и сдвиги окон; сам элемент создаётся ниже.
    pro = st.session_state.get('level') == 'Профессиональный'

    with st.expander('Когда считаем', expanded=True, icon=':material/schedule:'):
        mode_ru = st.radio('Режим', list(MODE_IDS), key='mode', **_def('mode', {'index': 0}),
                           help='Текущая обстановка — живые источники. Исторический разбор — весь архив мая–июня 2024. '
                                'Прогноз из прошлого — только записи, опубликованные до отсечки.')
        mode = MODE_IDS[mode_ru]
        if mode == 'live':
            t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
            st.caption('Начало срока поиска: **%s UTC** (сейчас)' % t0.strftime('%d.%m.%Y %H:%M'))
        else:
            d = st.date_input('Дата (1 мая — 30 июня 2024)', key='hist_date',
                              **_def('hist_date', {'value': datetime(2024, 5, 10).date()}),
                              min_value=ARCHIVE_FROM.date(), max_value=datetime(2024, 6, 30).date())
            hh = st.slider('Час начала срока, UTC', 0, 23, key='hist_hour', **_def('hist_hour', {'value': 12}))
            t0 = datetime(d.year, d.month, d.day, hh, tzinfo=timezone.utc)
            if mode == 'history_forecast':
                st.caption('Отсечка публикации: **%s UTC** — позже ничего не используется.' % t0.strftime('%d.%m.%Y %H:%M'))

    with st.expander('Источники данных', expanded=False, icon=':material/database:'):
        if mode == 'live':
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
            auto_min = 0

    with st.expander('Проверки и сценарии', expanded=False, icon=':material/science:'):
        if mode == 'live':
            kp_off_hist = False
        else:
            kp_off_hist = st.checkbox('Исключить Kp из архива (проверка отказа)', key='kp_off_hist',
                                      help='Проверка поведения при отказе источника: наблюдение Kp не используется, покрытие объявляется.')
            disabled = {'goes': False, 'kp': 'off' if kp_off_hist else False}
        # О7 оценивает не саму функцию, а объяснённую потребность, связь с основным сценарием и
        # пользу, подтверждённую примером. Без этой подписи свёртка содержала только ползунки.
        # Числа примера проверены прогоном: history_forecast 25.06.2024 12:00, период 1440,
        # сдвиги 0/480, Kp 7 — вердикт меняется с preferred на all_need_check.
        # Тринадцатый круг: объяснение потребности (О7) уехало под раскрытие в самой панели.
        # Оно не потеряно — заголовок раскрытия виден и называет, что внутри.
        with st.expander('Зачем нужен сценарий «что если»', expanded=False):
            st.caption('Зачем: проверить, устоит ли выбор окна, если обстановка ухудшится уже ПОСЛЕ согласования '
                       'плана. Подставленные условия помечаются на экране плашкой «сценарий «что если»» и в '
                       'выгрузке, в реестр источников не попадают. Пример: на тихой дате 25.06.2024 при Kp 7 '
                       'оба окна получают условие проверки, и вердикт меняется с «есть предпочтительное окно» '
                       'на «все окна требуют проверки» — то же правило, что на буре Гэннон, но на данных, '
                       'происхождение которых видно.')
        sc_delay = st.slider('Задержка начала работ, мин', 0, 180, 0, step=15, key='sc_delay',
                             help='Все окна сдвигаются на задержку; показывается как изменение плана.')
        sc_sep_on = st.checkbox('Смоделировать протонное событие', key='sc_sep_on')
        sc_sep_off = st.slider('Начало протонного события, мин после начала срока', 0, 1440, 120, step=30, disabled=not sc_sep_on, key='sc_sep_off')
        sc_sep_pfu = st.select_slider('Уровень события, pfu (≥10 МэВ)', [10.0, 100.0, 1000.0, 10000.0], value=100.0, disabled=not sc_sep_on, key='sc_sep_pfu')
        sc_kp_on = st.checkbox('Смоделировать скачок Kp', key='sc_kp_on')
        sc_kp = st.slider('Kp при скачке, безразмерный', 0.0, 9.0, 7.0, step=0.33, disabled=not sc_kp_on, key='sc_kp')
        st.caption('Ручное сравнение окон — раздел «Разобрать конкретные окна» под ответом.')
    scenario = Scenario('ui', work_delay_min=sc_delay, sep_onset_offset_min=(sc_sep_off if sc_sep_on else None),
                        sep_level_pfu=(sc_sep_pfu if sc_sep_on else None), kp_override=(sc_kp if sc_kp_on else None))
    if mode == 'history_forecast':
        if sc_delay or sc_sep_on or sc_kp_on:
            st.info('Сценарий «Что если» отключён для строгого прогноза. Для моделирования выберите исторический разбор или текущую обстановку.')
        scenario = Scenario('none')
    TH0 = Thresholds.from_settings()          # config/settings.toml — настройки вне кода (Т7)
    if pro:
        with st.expander('Пороги и правила', expanded=False, icon=':material/tune:'):
            st.caption('Умолчания — из config/settings.toml; всё применённое попадает в снимок и выгрузку.')
            th = replace(TH0,
                         saa_B_threshold_nT=st.number_input('Порог аномалии |B|, нТл', 18000.0, 30000.0, TH0.saa_B_threshold_nT, 500.0, key='th_B'),
                         e_min_MeV=st.selectbox('Канал захваченных протонов, МэВ от', [12.5, 30.0, 50.0], key='th_E',
                                                index=[12.5, 30.0, 50.0].index(TH0.e_min_MeV) if TH0.e_min_MeV in (12.5, 30.0, 50.0) else 1),
                         goes_max_age_min=st.number_input('Допустимая давность наблюдения GOES, мин', 10.0, 360.0, TH0.goes_max_age_min, 10.0, key='th_age',
                                                          help='Старше — для будущих участков окна покрытие частичное.'),
                         kp_check=st.number_input('Порог Kp для условия проверки, безразмерный', 5.0, 9.0, TH0.kp_check, 0.5, key='th_kp',
                                                  help='Применяется к наблюдению, уведомлениям о буре и прогнозам NOAA/ENLIL.'),
                         tle_max_age_days=st.number_input('Допустимый возраст TLE, сут', 1.0, 14.0, TH0.tle_max_age_days, 1.0, key='th_tle'))
            T_months = st.slider('Длительность экспедиции для норм, мес', 1, 12, 6, key='T_months')
    else:
        th, T_months = TH0, 6

    with st.expander('Вид', expanded=False, icon=':material/visibility:'):
        level = st.radio('Уровень интерфейса', ['Оперативный', 'Профессиональный'], index=0, horizontal=True, key='level',
                         help='Оперативный — решение и условия; профессиональный — все величины, пороги, устойчивость, происхождение.')
        # Почему светлой темы нет — в подсказке переключателя, а не строкой в панели.
        st.caption('Тема тёмная, светлой нет.', help='Экран смотрят в затемнённом зале, и светлая заливка '
                   'на проекторе слепит. Переключателя не ставим, чтобы половина элементов не осталась '
                   'светлой на тёмном фоне.')

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


# ================================================================= блок 1: заголовок и назначение
# Тринадцатый круг: под названием стоит ЯРЛЫК в четыре слова, а не предложение о назначении.
# Полное описание сервиса — во вкладке «Объяснения», первым абзацем.
st.markdown(head('ВКД-Риск', 'выбор окна выхода · UTC'), unsafe_allow_html=True)

# ================================================================= блок 2: строка задачи
# Вопрос человека стоит первым элементом главной области, а не в боковой панели: он приходит
# спросить «нам надо выйти на столько-то минут в ближайшие столько-то часов — когда?», и до
# одиннадцатого круга сервис требовал от него ответа, за которым он и пришёл (ползунки сдвига).
_task = st.container(border=True)       # строка задачи обведена рамкой: это отдельный блок, а не поля россыпью
_t1, _t2, _t3, _t4 = _task.columns([1.5, 1.6, 1.3, 3.2])
duration_min = int(_t1.number_input('Выход на, мин', min_value=60, max_value=480, step=30, key='duration',
                                    **_def('duration', {'value': int(UI.get('duration_min', 360))}),
                                    help='Постановка: 60…480 мин. Сокращение длительности — изменение плана, '
                                         'не улучшение обстановки.'))
search_h = int(_t2.number_input('Начать в ближайшие, ч', min_value=1, max_value=24, step=1, key='search_h',
                                **_def('search_h', {'value': max(1, int(UI.get('search_min', 720)) // 60)}),
                                help='Срок, внутри которого сервис ищет начало выхода; постановка — до суток.'))
search_min = int(search_h) * 60
_t3.markdown('<div class="btnpad"></div>', unsafe_allow_html=True)   # кнопка встаёт вровень с полями
_t3.button('Найти окна', key='find_windows', width='stretch', help=TASK_HINT_RU)
# Одна приглушённая строка на блок: длительность и срок уже стоят в самих полях, повторять их
# словами незачем. Как работает кнопка — в её подсказке, а не третьей строкой на экране.
# Ярлык, а не фраза: те же три факта через разделитель, без связок и без «времена UTC»
# (единица времени названа один раз, в ярлыке под названием сервиса).
_t4.caption('Выход на %s · срок %s · режим: %s'
            % (hm_ru(duration_min), hm_ru(search_min), mode_ru))

# --- сдвиги окон ручного разбора: значения берутся из состояния ДО расчёта, сами ползунки стоят
# в свёрнутом разделе «Разобрать конкретные окна» ниже (раздел 3.1 техзадания). Порядок обязателен:
# Streamlit читает значение элемента из состояния, а записывать состояние можно только ДО создания
# элемента, поэтому здесь значения только читаются и ограничиваются сроком.
n_windows = int(st.session_state.get('n_windows') or 2)
OFF_STEP = 30
_def_off = list(UI.get('window_offsets_min', [0, 240]))
# R4-1: сдвиги живут в невиджетных ключах `_off<i>`, а не только в состоянии ползунка.
# У ползунка меняется max_value вместе со сроком поиска — Streamlit считает его НОВЫМ виджетом
# и теряет сохранённое значение, поэтому при любом изменении срока оба сдвига уходили в 0 и
# настройка окон пропадала. Порядок каждого прогона: взять сдвиги из `_off<i>` (или из ползунка,
# если срок не менялся и пользователь только что его двинул), ограничить сроком, развести
# совпавшие, записать обратно и отдать ползункам ДО их создания.
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
st.session_state['_search_prev'] = search_min
# окна нумеруются по времени начала; одинаковые сдвиги разводим на шаг, экран не останавливаем (U1)
offsets, offsets_in = sorted(offsets_state), list(offsets_state)
dup = sorted({o for o in offsets if offsets.count(o) > 1})
if dup:
    offsets = spread_offsets(offsets, search_min, OFF_STEP)
# К4: подпись пресета сверяется с ФАКТИЧЕСКИМ запросом по каждому полю. Расходится хоть одно —
# печатается это, а не параметры пресета: иначе член жюри, нажавший пресет и двинувший ползунок,
# читает в первом же элементе интерфейса три утверждения, два из которых ложные.
if _cur is None:
    _preset_caption.caption('пресет не выбран')
elif preset_matches(_cur, mode_ru, st.session_state.get('hist_date'), st.session_state.get('hist_hour'),
                    duration_min, search_min, offsets):
    _preset_caption.caption('%s — %s' % (_cur['label'], _cur['shows']))
else:
    _preset_caption.caption(PRESET_CHANGED_RU)

horizon_min = search_min + duration_min
windows_end = t0 + timedelta(minutes=max(offsets) + duration_min + scenario.work_delay_min)
# Предупреждение о границе архива печатается ВНИЗУ, в разделе ручного разбора: оно относится
# к окнам, которые человек расставил сам, и между его вопросом и ответом ему не место (3.0).
windows_beyond_archive = mode != 'live' and windows_end > ARCHIVE_TO
try:                              # границы постановки проверяются до любого запроса (Т7): сообщение зрителю, расчёта нет
    validate_request(mode, t0, duration_min, search_min, offsets)
except ValueError as e:
    st.error('Запрос вне границ постановки: %s. Измените запрос строкой задачи выше или в боковой панели.' % e)
    st.stop()
try:
    if mode == 'live':
        with st.spinner('Источники: GOES, Kp, TLE, бюллетень NOAA — до %s с на адрес и не дольше %s с на все вместе; '
                        'дальше кеш и снимок репозитория…' % (fmt(SRC_CFG.get('timeout_s', 6)), fmt(TOTAL_DEADLINE_S))):
            fetched, fetch_note = _fetch_all(disabled['goes'], disabled['kp'], disabled['noaa'],
                                             int(st.session_state['fetch_nonce']))
    else:
        fetched, fetch_note = None, None   # архивные режимы: живые источники не запрашиваются вовсе — входы только из архива (Т1, Т6)
    # Этапы перечисляются заранее и закрываются по факту: человек видит, ЧТО считается,
    # а не одну фразу «идёт расчёт». Порядок соответствует действительному ходу расчёта
    # внутри app.compute.run; если он там изменится, этот перечень станет враньём.
    with st.status('Считаю окна выхода…', expanded=True) as _st_progress:
        st.write('Траектория станции: распространение элементов орбиты и магнитное поле по трассе')
        st.write('Оценка окон: минуты в аномалии, флюенс захваченных протонов, доза за защитой')
        st.write('Метеороиды и техногенный мусор: ожидаемое число попаданий за окно')
        st.write('Перебор начал выхода на всём сроке и проверка устойчивости выбора')
        R = run(mode, t0, duration_min, search_min, offsets, disabled=disabled, thresholds=th,
                scenario=scenario, T_months=T_months, fetched=fetched, fetch_note=fetch_note)
        _st_progress.update(label='Расчёт закончен', state='complete', expanded=False)
except Exception as e:            # noqa: BLE001 — экран не падает; подробности в лог, не зрителю (Т6, Т7)
    LOG.error('расчёт не выполнен: %s\n%s', e, traceback.format_exc())
    st.error('Расчёт не выполнен: %s. Измените запрос или повторите позже; подробности записаны в журнал сервера.' % type(e).__name__)
    st.stop()
S, rec, meta, traj, rob = R.S, R.rec, R.meta, R.traj, R.rob
now = datetime.fromisoformat(S['computed_utc'])
windows = [a.window for a in R.assessments]
windows_ru = {a.window.start_utc: str(i + 1) for i, a in enumerate(R.assessments)}

# Изменение плана: постановка требует не только пересчитать, но и показать ПОСЛЕДСТВИЯ.
# Прежде плашка появлялась только при изменении длительности и говорила лишь о плане, ни слова
# об исходе; при сдвиге окна её не было вовсе. Теперь хранится снимок плана и исхода, и плашка
# печатается при любом изменении запроса, с тем, что изменилось в пересчёте (О3, О5).
_plan_now = plan_state(rec, R.assessments, S, duration_min, offsets, mode=mode, t0=t0)
_plan_prev = st.session_state.get('_plan_state')
_same_request = _plan_prev is not None and _plan_prev.get('mode') == _plan_now['mode'] \
    and _plan_prev.get('t0') == _plan_now['t0']
if _same_request and (_plan_prev.get('duration_min') != _plan_now['duration_min']
                      or _plan_prev.get('offsets') != _plan_now['offsets']):
    st.session_state['_plan_change_ru'] = plan_change_ru(_plan_prev, _plan_now)
    st.session_state['_plan_change_for'] = (_plan_now['duration_min'], tuple(_plan_now['offsets']))
elif not _same_request:
    st.session_state.pop('_plan_change_ru', None)      # другой запрос — прежняя плашка к нему не относится
    st.session_state.pop('_plan_change_for', None)
st.session_state['_plan_state'] = _plan_now
plan_change = None
if st.session_state.get('_plan_change_for') == (duration_min, tuple(_plan_now['offsets'])):
    plan_change = st.session_state.get('_plan_change_ru') or None
if sc_delay:
    plan_change = ((plan_change + ' ') if plan_change else '') + 'Изменение плана: сценарий «что если» сдвигает начало работ на %d мин.' % sc_delay
if plan_change:
    S['request']['plan_change'] = plan_change

# ================================================================= приборная полоса (рисуется в блоке 7)
# Шапка стоит блоком 1, ДО расчёта: человек должен понять, куда попал, раньше, чем увидит числа.
# Приборная полоса из четырёх ячеек собирается здесь, а печатается в блоке «Состояние источников»:
# это происхождение данных, а не ответ на вопрос человека (раздел 3.0 техзадания).
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
         'данные до %s · окно до %s' % (dt_ru(horizon_to), dt_ru(windows_end)), 'calc')]
# --- строка 2: источники. Цвет означает ТОЛЬКО происхождение: наблюдение зелёное, даже если взято
# из кеша (давность стоит подписью); янтарный остаётся за внешним прогнозом; красный — условие
# проверки по порогу или отказ источника. Кеш цветом больше не помечается (R4-28).
row2 = []
if mode == 'live':
    g, k = R.goes, R.kp
    g_src, k_src = src['noaa_swpc_goes'], src['gfz_kp']
    # Метка полосы говорит «сейчас», метка карточки окна — «на окно»: под одной меткой
    # «GOES ≥10 МэВ, pfu» на экране стояли три разных ответа (полоса и две карточки), и причина
    # расхождения была спрятана мелким «почему неполное».
    if g:
        row2.append(('GOES ≥10 МэВ сейчас, pfu', fmt(float(g.value)),
                     '%s · наблюдение %s · %s%s' % (s_level(g.value), dt_ru(g.t_utc, with_date=False),
                                                    age_ru(g_src.get('age_min'), th.goes_max_age_min),
                                                    ' · из кеша' if g_src.get('from_cache') else ''),
                     'cond' if g.value >= th.goes_p10_warning_pfu else 'obs'))
    else:
        _v, _k = source_short(g_src, disabled.get('goes'))
        row2.append(('GOES ≥10 МэВ сейчас, pfu', '—', _v, {'ok': 'obs', 'warn': 'fc', 'crit': 'cond'}.get(_k, 'none')))
    if k:
        row2.append(('Kp (GFZ), безразмерный', fmt(float(k.value)),
                     '%s · интервал до %s · %s%s' % (QUALITY_RU.get(k.quality, k.quality), dt_ru(k.t_utc),
                                                     age_ru(k_src.get('age_min'), th.kp_max_age_min),
                                                     ' · из кеша' if k_src.get('from_cache') else ''),
                     'cond' if k.value >= th.kp_check else 'obs'))
    else:
        _v, _k = source_short(k_src, disabled.get('kp'))
        row2.append(('Kp (GFZ), безразмерный', '—', _v, {'ok': 'obs', 'warn': 'fc', 'crit': 'cond'}.get(_k, 'none')))
    if meta and meta.epoch_utc:
        age_h = src['orbit'].get('age_h')
        # Давность печатается ОДИН раз и в одних единицах (часы). Прежде в ту же ячейку уходило
        # происхождение элементов вместе со второй давностью в минутах и с адресом резервной
        # цепочки: «давность 35 ч … давность данных 2117.4 мин; проверка адресов: https://…: timeout».
        # Английское слово, точка вместо запятой и голый адрес с параметрами — на оперативном
        # уровне это техническая строка (бриф §9.8); полный адрес остаётся на профессиональном.
        _tle_org = tle_origin(tm.get('tle_fetch_status')).partition(', давность')[0]
        row2.append(('Элементы орбиты', 'двухстрочные (TLE)',
                     'эпоха %s · %s · %s' % (dt_ru(meta.epoch_utc),
                                             age_ru(age_h * 60 if age_h is not None else None,
                                                    limit_ru='%s сут' % fmt(th.tle_max_age_days)),
                                             _tle_org),
                     'obs'))          # элементы — данные источника; возраст объявлен подписью и в блоке состояния
    else:
        row2.append(('Элементы орбиты', '—', 'элементов нет — орбита не построена', 'cond'))
    # О1: в текущем режиме источник уведомлений (NASA DONKI) не опрашивается вовсе — живого
    # загрузчика нет. Счётчик «0 записей» читался как «опросили, событий нет», а «Пропуск данных
    # не равен нулевому риску». Число печатается только тогда, когда события действительно есть
    # (их приносит сценарий «что если»).
    row2.append(('События на горизонте',
                 '%s %s' % (fmt(len(R.events)), plural_ru(len(R.events), ('запись', 'записи', 'записей'))),
                 # Двенадцатый круг: лента уведомлений NASA DONKI ТЕПЕРЬ ОПРАШИВАЕТСЯ живьём
                 # (vkd/integration/donki_live.py). Прежняя подпись «источник не опрашивается»
                 # стала неправдой, и её нельзя оставлять: ноль записей теперь означает
                 # «опросили, событий на горизонте нет», а это другое утверждение. Признак и
                 # числа берутся из снимка расчёта (`events_line`), а не из головы экрана.
                 ('наш подсчёт по реестру: сценарий «что если» и датированные прогнозы, учтённые в окнах'
                  if R.events else
                  # Подпись строится теми же смысловыми частями через « · », что у остальных
                  # ячеек: первая часть остаётся в полосе, полная уходит в раскрытие под ней
                  # (app.ui.split_panel_rows). Без разделителей ячейка не даёт записи в
                  # раскрытие, и оно пропадает целиком — это ловило падение проверки.
                  # Происхождение величины стоит ПЕРВЫМ и не выбрасывается: число в ячейке —
                  # наш подсчёт по реестру окон, а не счётчик сообщений службы. Это разные
                  # величины, и проверка требует, чтобы ячейка называла своё происхождение.
                  ('наш подсчёт по реестру · лента опрошена живьём: %s %s за %s суток · '
                   'событий с действием в окнах нет — ноль здесь означает «опросили, событий '
                   'нет», а не «источник не спрашивали»'
                   % (fmt((S.get('events_line') or {}).get('feed_messages', 0)),
                      plural_ru(int((S.get('events_line') or {}).get('feed_messages', 0)),
                                ('сообщение', 'сообщения', 'сообщений')),
                      fmt((S.get('events_line') or {}).get('feed_window_days', 7)))
                   if (S.get('events_line') or {}).get('connected')
                   else 'источник уведомлений не ответил: %s — это не отсутствие событий'
                        % ((S.get('events_line') or {}).get('reason_ru') or 'причина не названа'))),
                 'fc' if R.events else ('obs' if (S.get('events_line') or {}).get('connected') else 'none')))
else:
    # Сколько записей не пошло в расчёт ИМЕННО из-за отсечки: остальные не подошли по содержанию,
    # и называть их исключёнными отсечкой нельзя (в разборе отсечки нет вовсе).
    _n_by_cutoff = sum(1 for x in R.excluded
                       if excl_group_ru(x.partition(': ')[2]) == 'время публикации или доступность')
    row2.append(('Отсечка публикации' if mode == 'history_forecast' else 'Начало периода', dt_ru(t0),
                 ('отложено %s %s позже отсечки'
                  % (nbsp_thousands(_n_by_cutoff), plural_ru(_n_by_cutoff, ('запись', 'записи', 'записей'))))
                 if mode == 'history_forecast' else 'архив взят весь — разбор после факта', 'calc'))
    # Наблюдение Kp: в разборе — окончательный ряд GFZ, в строгом режиме — уведомление DONKI
    # с наблюдённым Kp и собственным временем публикации. Подпись идёт от записи, а не от режима.
    # Момент — в самой МЕТКЕ. Значение относится к началу периода, а окна живут до 22:00; время
    # стояло в мелкой подписи, а крупное зелёное число её забивало, и верхняя полоса за десять
    # секунд читалась как «обстановка спокойная» — прямо противоположное вердикту на том же экране.
    # В архивных режимах тон ячеек нейтральный: зелёный здесь означал бы «благоприятно» (Т6, О2).
    # Единица называется прямо в метке: Kp безразмерен, и голое число рядом с «pfu» и «нТл»
    # соседних ячеек читалось бы как величина в тех же единицах (замечание владельца о размерностях).
    _kp_label = ('Kp на начало периода, безразмерный (архив GFZ)' if mode == 'history_review'
                 else 'Kp на начало периода, безразмерный, наблюдение')
    _win_hint = ' Характеристика окон — в карточках окон, а не здесь.'
    if kp_off_hist:
        row2.append((_kp_label, 'исключён', 'исключён пользователем — проверка отказа источника', 'cond'))
    elif R.kp is not None and R.kp.source_id != 'scenario':
        row2.append((_kp_label, fmt(float(R.kp.value)),
                     '%s · интервал до %s · %s · значение на %s — начало периода поиска.%s'
                     % (source_ru(R.kp.source_id), dt_ru(R.kp.t_utc),
                        age_ru(src['gfz_kp'].get('age_min'), th.kp_max_age_min), dt_ru(t0), _win_hint),
                     'cond' if R.kp.value >= th.kp_check else 'calc'))
    elif mode == 'history_forecast':
        row2.append((_kp_label, '—', 'наблюдения с доказанной публикацией до отсечки нет', 'fc'))
    else:
        row2.append((_kp_label, '—', 'в архиве на этот момент нет', 'none'))
    # C3: в разборе есть численное наблюдение GOES — показываем его там же, где Kp.
    # Его отсутствие в строгом режиме объявляется один раз, в общем блоке о состоянии
    # источников, а не второй плашкой здесь (одно сообщение об одном и том же).
    if R.goes is not None and R.goes.source_id != 'scenario':
        row2.append(('GOES ≥10 МэВ на начало периода, pfu', fmt(float(R.goes.value)),
                     '%s · наблюдение %s — начало периода поиска.%s'
                     % (s_level(R.goes.value), dt_ru(R.goes.t_utc), _win_hint),
                     'cond' if R.goes.value >= th.goes_p10_warning_pfu else 'calc'))
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
# Полоса печатается в блоке 7 «Состояние источников»: короткие подписи в ячейках, полные —
# раскрытием под полосой (у шести ячеек подряд подпись в три строки читать было нечем).
_panel_short_rows, _panel_details = split_panel_rows([row1, row2])
_panel_html = panel(_panel_short_rows)
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

# ================================================================= чего не хватает (вход блока 3)
missing_ru = []
_dis = S['request'].get('disabled') or {}
for m_ in rec.missing:
    extra = ''
    # Суффикс привязан к КАНАЛУ, а не к механизму. Прежде ' — GOES исключён пользователем'
    # приклеивалось к каждой строке со словом «космопогода», и строка про отсутствие Kp получала
    # объяснение про GOES: пользователь шёл чинить GOES, а дело было в Kp (ложная атрибуция причины
    # в блоке вердикта).
    if mode == 'live' and 'GOES' in m_ and _dis.get('goes') == 'off':
        extra = ' — GOES исключён пользователем'
    elif mode == 'live' and 'Kp' in m_ and _dis.get('kp') == 'off':
        extra = ' — Kp (GFZ) исключён пользователем'
    elif mode == 'live' and 'прогноз' in m_ and _dis.get('noaa') == 'off':
        extra = ' — трёхсуточный бюллетень NOAA исключён пользователем'
    elif 'космопогода' in m_ and mode != 'live':
        extra = ' — окна выходят за каталог уведомлений DONKI (01.05–30.06.2024)' \
            if windows_beyond_archive else ' — линия без данных на горизонте'
    missing_ru.append(m_ + extra)
# Сказать, чего нет, мало: аналитик спрашивает «и что?». Следствие дописывается к последней строке
# нехватки одной фразой, а не отдельным пунктом — иначе это второе сообщение об одном и том же.
_conseq = coverage_consequence_ru(missing_ru)
if _conseq and missing_ru:
    missing_ru[-1] = missing_ru[-1].rstrip(' .;,') + '. ' + _conseq
any_cond = any(m.needs_check for a in R.assessments for m in a.mechanisms)
# политика прототипа целиком — один раз, во вкладке «Объяснения»; здесь только указатель (U5)
policy_short = 'Окна с условиями не выбираются автоматически — правило команды, не норма (вкладка «Объяснения»).' if any_cond else None
_fname = 'vkd_risk_%s_%s_calc%s' % (mode, t0.strftime('%Y%m%dT%H%M'), now.strftime('%Y%m%dT%H%M'))
_zip = build_zip(S, R.raw_records)
# Перебор начал пишется параллельно с экраном. Ключа нет — экран показывает прежний разбор окон
# и честно говорит, что перебора не было; выдуманных чисел он не подставляет (раздел 1a техзадания).
scan = scan_of(S)
# Ответ перебора почти всегда ПРОМЕЖУТОК, а не точка: соседние начала неразличимы внутри
# собственной чувствительности модели. Поэтому окно для блока «Что учтено» берётся из ответа
# (первое начало названной группы), а не из `recommended_index`, который на живых данных чаще
# всего пуст, и пустота эта отказом не является.
scan_ans = scan_answer(scan) if scan is not None else None
scan_cand = (scan_ans or {}).get('first')

# ================================================================= блок 3: рекомендация
# Оценка окна, отвечающая ответу перебора, ищется ЗДЕСЬ, а не только в блоке «Что учтено»:
# из неё берётся доза за защитой скафандра — третье число ряда под решением. Перебор дозу не
# считает (договор раздела 1a — три различающие величины), поэтому, когда полной оценки на
# это начало нет, числа дозы нет тоже, и подставлять его неоткуда.
_acc_target = None
if scan_cand is not None:
    _acc_target = next((a for a in R.assessments
                        if a.window.start_utc.isoformat() == str(scan_cand.get('start_utc'))), None)
if _acc_target is None and scan_cand is None:
    _acc_target = next((a for a in R.assessments
                        if rec.preferred is not None and a.window.start_utc == rec.preferred.start_utc),
                       (R.assessments[0] if R.assessments else None))
_dose = dose_factor(_acc_target) if _acc_target is not None else None
c_main, c_btn = st.columns([6, 1.5])
if scan is not None:
    c_main.markdown(recommendation_panel(scan, S, pro=pro, mode=mode, missing_ru=missing_ru,
                                         duration_min=duration_min, dose=_dose), unsafe_allow_html=True)
    if plan_change:
        c_main.info(plan_change)
else:
    # Перебора нет: на месте рекомендации стоит вердикт по вручную заданным окнам — ровно то,
    # что сервис действительно посчитал, — и одна строка о том, что перебор не выполнялся.
    c_main.markdown(verdict_panel(rec, S, windows_ru, assessments=R.assessments, pro=pro, plan_change=plan_change,
                                  missing_ru=missing_ru, policy_short=policy_short,
                                  thr_nT=th.saa_B_threshold_nT, e_min_MeV=th.e_min_MeV, mode=mode),
                    unsafe_allow_html=True)
    c_main.caption(scan_absent_ru())
c_btn.download_button('Скачать отчёт (ZIP)', _zip, file_name=_fname + '.zip', mime='application/zip', width='stretch', key='dl_top')
c_btn.caption('отчёт, запрос, факторы, сырые записи')
# Пункт 1 двенадцатого круга. Здесь стояла плашка «Проверка после отсечки»: владелец о ней —
# «проверка после отсечки мне тоже не ясна, либо убираем, либо обыгрываем нормально». Между
# вопросом человека и ответом ей не место: она не о том, когда выходить, а о том, сошёлся ли
# прогноз с фактом задним числом. Сама проверка никуда не делась — она стоит целиком во вкладке
# «Наблюдения и прогнозы», где рядом лежат и наблюдения, и прогнозы, из которых она собрана,
# и названа там своими словами: что это сопоставление, а не часть расчёта.

# ================================================================= блок 4: глобус — где и когда
# Глобус переехал из вкладки на главный экран: он обыгрывает рекомендацию, а не иллюстрирует
# методику (раздел 3.5 техзадания). Плоская карта остаётся запасным видом — она работает без
# WebGL и без сети.
_globe_recommended = None
# Ограничения глобуса собираются здесь, а печатаются во вкладке «Методика» (пункт 1). Умолчание
# пустое: вид не построен или выбрана плоская карта — говорить о нём во вкладке нечего, и
# выдумывать текст вместо отсутствующего нельзя.
_globe_limits, _globe_tech = '', ''
if scan_cand is not None and (scan_ans or {}).get('kind') in ('point', 'interval'):
    try:
        _globe_recommended = (datetime.fromisoformat(scan_cand['start_utc']),
                              datetime.fromisoformat(scan_cand['end_utc']))
    except (KeyError, TypeError, ValueError):
        _globe_recommended = None
if _globe_recommended is None and rec.preferred is not None:
    _globe_recommended = rec.preferred      # перебора нет — подсвечивается предпочтительное окно разбора
# Тринадцатый круг. Эксперт хакатона о прежнем заголовке «Где и когда: трасса, аномалия и окно»:
# «глобус и плоская карта — для чего, нужен сигнал, пометка», «для чего это надо». Значит
# заголовок не сработал: он называл, ЧТО нарисовано, а не ЧТО это доказывает. Новый заголовок —
# ярлык в пять слов, и он связан с числом из ряда под решением: «Минут в аномалии 4,9 / худшее 104».
# Рисунок показывает, ГДЕ именно эти минуты набираются, — красные отрезки трассы внутри области.
# Строки пояснения под ним больше нет: у самого глобуса есть своя легенда, а полная подпись
# (чем посчитано и чего вид не даёт) стоит во вкладке «Методика», в «Границах применимости».
st.markdown('<div class="sect">Где набираются минуты в аномалии</div>', unsafe_allow_html=True)
if traj:
    # Подпись у переключателя называет РАЗНИЦУ видов, а не их назначение: на шаре трасса
    # непрерывна, плоскую карту рвёт 180-й меридиан. Это ровно то, из-за чего вид и выбирают.
    view = st.radio('Вид', [globe.VIEW_GLOBE, globe.VIEW_FLAT], index=0, horizontal=True, key='map_view',
                    captions=['трасса непрерывна', 'разрыв на 180° долготы'],
                    help='Глобус показывает ту же трассу и ту же область аномалии на сфере, без разрыва '
                         'по долготе. Плоская карта — запасной вид: она работает без WebGL и без сети.')
    if view == globe.VIEW_GLOBE:
        try:
            with st.spinner('Область аномалии по IGRF на сетке 4°…'):
                # Глобус подсвечивает РЕКОМЕНДОВАННОЕ окно — то самое, которое названо в блоке
                # ответа выше, а не предпочтительное окно ручного разбора: иначе на одном экране
                # подсвечено одно окно, а рекомендовано другое. При ответе промежутком берётся
                # самое раннее начало из него — то же окно, что разобрано в блоке «Что учтено».
                # Ответа нет (отказ или условие у всех начал) — подсветки нет, и это честно.
                _gp = globe.globe_payload(traj, windows, th.saa_B_threshold_nT, t0,
                                          recommended=_globe_recommended)
            globe.render_globe(_gp)
            # Подпись глобуса длинная и своя: на первом экране остаётся первая фраза — что
            # нарисовано. Остальное (чем посчитано и чего глобус НЕ даёт) с двенадцатого круга
            # уезжает не в раскрытие рядом, а во вкладку «Методика», в раздел «Границы
            # применимости»: владелец просил не перечислять ограничения на главном экране
            # россыпью — «что не даёт глобус, тоже не надо писать». Текст не потерян: он стоит
            # в одном месте со всеми остальными ограничениями и попадает в отчёт.
            # Подпись глобуса не печатается на главном экране вовсе (тринадцатый круг): и первая
            # её фраза — предложение на 250 знаков. Целиком она стоит во вкладке «Методика»,
            # в разделе «Границы применимости», вместе с остальными ограничениями.
            _globe_limits = globe.caption(_gp)
            _globe_tech = globe.tech_line(_gp) if pro else ''
        except Exception as e:                    # noqa: BLE001 — Т6: вид отказал, экран остаётся
            LOG.error('глобус не построен: %s\n%s', e, traceback.format_exc())
            st.warning('Глобус не построен (%s). Переключите вид на «Плоская карта» — данные те же.'
                       % type(e).__name__)
    else:
        with st.spinner('Область аномалии по IGRF: контур на сетке 2° × 1°…'):
            st.plotly_chart(dark_figure(ground_track(traj, windows, th.saa_B_threshold_nT, t0)), width='stretch', config=PLOTLY_CONFIG)
        # Подпись карты — раскрытием, а не строкой под рисунком: цвета названы легендой самой карты.
        with st.expander('Как читать карту: что означают цвета', expanded=False):
            st.caption(map_caption(pro))
else:
    st.write('Трассы нет: орбита недоступна.')

# ================================================================= блок 5: профиль воздействия
st.markdown('<div class="sect">Профиль воздействия на сроке поиска</div>', unsafe_allow_html=True)
if scan is not None:
    # Пункт 3: один график вместо двух панелей с ломаными. Ось Y — круглые степени десяти,
    # рекомендованный промежуток залит, пик подписан временем (app.ui.windows_ribbon).
    st.plotly_chart(dark_figure(windows_ribbon(scan, th.e_min_MeV, duration_min=duration_min)),
                    width='stretch', config=PLOTLY_CONFIG, key=backdrop.LENTA_KEY)
    # Строки пояснения под графиком больше нет: направление сказано на самой оси («ниже — лучше»),
    # полоса и пик подписаны на рисунке. Подпись целиком — раскрытием, одним нажатием.
    with st.expander('Как читать профиль: кривая, полоса и пик', expanded=False):
        st.caption(ribbon_caption_ru(scan, duration_min=duration_min))
    _best_rows = scan_best_rows(scan)
    if _best_rows:
        st.dataframe(_best_rows, width='stretch', hide_index=True,
                     column_config={'условия проверки': st.column_config.TextColumn(width='large')})
        # Пункт 4: колонка условий печатается только тогда, когда условие есть хоть у одного
        # показанного начала. Когда его нет ни у кого, вместо колонки из одних «нет» стоит
        # одна строка — она сообщает ровно тот же факт и не занимает треть ширины таблицы.
        _cond_note = scan_conditions_note_ru(_best_rows)
        # Номера 1…6 заняты таблицами вкладок; эта стоит на главном экране и называется по имени,
        # чтобы не оказалось двух «Таблиц 1» на одном экране.
        # Ярлык вместо абзаца: «по тому же правилу» и «баллов и нормировки нет» — это утверждения
        # о методе, и они стоят во вкладке «Методика», рядом с самим правилом.
        st.markdown('<div class="tcap">Таблица лучших начал%s</div>'
                    % ((' · ' + _cond_note) if _cond_note else ''), unsafe_allow_html=True)
else:
    st.caption('Профиля нет: перебор начал не выполнялся, рисовать по нему нечего.')

# ================================================================= блок 6: что учтено
# Заголовок был «Что учтено и что нет». Вторая половина переехала во вкладку «Методика» (пункт 1),
# и держать её в названии блока значило бы обещать на экране то, чего на нём больше нет.
st.markdown('<div class="sect">Что учтено</div>', unsafe_allow_html=True)
# `_acc_target` найден выше, в блоке 3: там из него берётся доза для ряда чисел под решением.
# Ярлыки в три-четыре слова. Какое именно начало взято, сказано словом «раннего»: полная
# формулировка стоит раскрытием «Откуда взята каждая величина» и в отчёте.
_ACC_HEAD_RU = {'point': 'Величины рекомендованного окна',
                'interval': 'Величины самого раннего начала',
                'tradeoff': 'Величины первого из названных начал'}
_acc_head = _ACC_HEAD_RU.get((scan_ans or {}).get('kind')) if scan_cand is not None else None
if not _acc_head:
    _acc_head = 'Величины предпочтительного окна' if rec.preferred is not None else 'Величины окна 1'
# Ярлык, а не фраза: «со своей единицей и происхождением» видно по самим строкам ниже.
st.markdown('**%s**' % _acc_head)
_acc_sources = []
if _acc_target is not None:
    for _line in accounted_lines(_acc_target):
        st.markdown(_line, unsafe_allow_html=True)
    _acc_sources = accounted_sources(_acc_target, R.raw_records)
elif scan_cand is not None:
    for _line in accounted_lines_from_scan(scan_cand, th.e_min_MeV):
        st.markdown(_line, unsafe_allow_html=True)
    _acc_sources = list(SCAN_SOURCES_RU)
else:
    st.write('Величин нет: окна не посчитаны.')
# Источник каждой величины — раскрытием: прослеживаемость сохранена целиком, но вторую половину
# строки у каждой из десяти величин первый экран больше не несёт.
if _acc_sources:
    with st.expander('Откуда взята каждая величина: первоисточник', expanded=False):
        for _line in _acc_sources:
            st.markdown(_line)
# Легенда стоит ровно здесь, рядом с плашками, и один раз на весь экран. Тринадцатый круг:
# на поверхности от неё остаётся РЯД ПЛАШЕК (каждая называет себя сама) и два коротких ярлыка —
# про цвет величины и про цвет решения. Второй ярлык обязателен: на экране теперь два языка
# цвета, и без одной строки зелёная полоса решения читалась бы как «зелёное = наблюдение».
# Полная формулировка (COLOR_LEGEND) стоит раскрытием здесь же и во вкладке «Методика».
st.markdown('<div class="legend">%s %s %s &nbsp; %s</div>'
            % (kind_pill('own_calculation'), kind_pill('observation'), kind_pill('external_forecast'),
               DECISION_LEGEND), unsafe_allow_html=True)
with st.expander('Что означает каждый цвет и каждая плашка', expanded=False):
    st.markdown('%s — измерено источником; %s — выпуск с указанием времени публикации; '
                '%s — посчитано сервисом по траектории и стандартам.'
                % (kind_pill('observation'), kind_pill('external_forecast'), kind_pill('own_calculation')),
                unsafe_allow_html=True)
    st.markdown(COLOR_LEGEND)
    st.markdown('%s Полоса решения — широкая, во всю ширину блока и прямыми углами; плашка '
                'происхождения — маленькая и скруглённая. Формы разные намеренно: цвет на экране '
                'значит разное, и спутать их нельзя.' % DECISION_LEGEND)
# Пункт 1 двенадцатого круга. Здесь стояло раскрытие «Чего сервис не учёл: N пунктов — поимённо
# и с причиной». Владелец: «вообще недочёты не надо указывать в программе, подчисти это». Список
# не удалён — он целиком переехал во вкладку «Методика», в раздел «Границы применимости», где
# стоит вместе с остальными ограничениями, а не россыпью по главному экрану. Здесь остаётся одна
# нейтральная строка со ссылкой: ни перечисления, ни числа пунктов, ни покаянного тона.
_not_acc = not_accounted_ru(S, mode)
st.caption(SCOPE_TAB_HINT_RU)

# ================================================================= блок 7: состояние источников
st.markdown('<div class="sect">Состояние источников: чем считали</div>', unsafe_allow_html=True)
st.markdown(_panel_html, unsafe_allow_html=True)
if _panel_details:
    with st.expander('Полоса источников: происхождение и давность', expanded=False):
        for _lbl, _full in _panel_details:
            st.markdown('- **%s** — %s' % (_lbl, _full))
if meta is None:
    st.error('**Орбита недоступна.** %s Оценка без траектории невозможна: покрытие обязательной линии отсутствует, '
             'рекомендации нет. Заглушка не подставляется.' % status_ru(tm['status'], pro))
if issues:
    # Бриф §9.1: ответ на вопрос «Когда выходить?» стоит первым и без прокрутки. Жёлтая плашка
    # на четыре пункта уводила его вниз, и первым впечатлением становилась тревога, не относящаяся
    # к решению. С одиннадцатого круга весь блок стоит ПОСЛЕ ответа; свёртка перечня остаётся.
    # Только в текущем режиме: там строк четыре и все они об одном — живых ответов нет, взят кеш.
    # В архивных режимах строк мало и каждая о своём (нет численного GOES, объявленная
    # реконструкция орбиты), сводить их в одну нельзя — потеряется смысл каждой.
    # Общий предел получения источников (LIMIT_MARK) не сворачивается никогда: когда живых
    # ответов нет по пределу, причина — самое важное на экране, и она обязана стоять в самой
    # плашке, а не на клик глубже (бриф §9.7 и проверки развёртывания).
    # Тринадцатый круг: перечень по источникам уехал под раскрытие во ВСЕХ режимах и на обоих
    # уровнях, а не только в текущем режиме на оперативном. Прежде в архивных режимах эти строки
    # печатались целиком — два абзаца про объявленную реконструкцию эфемериды прямо на главном
    # экране. Ничего не потеряно: заголовок раскрытия виден, перечень открывается одним нажатием
    # и целиком лежит в выгрузке.
    if any(LIMIT_MARK in x for x in issues):
        st.warning('**Состояние источников:**\n' + '\n'.join('- ' + x for x in issues))
    else:
        _short = source_issues_short_ru(issues) if mode == 'live' else ''
        _n = len(issues)
        st.markdown('<div class="legend">%s</div>'
                    % screen_text(_short or ('источники: %d %s' % (_n, plural_ru(_n, ('замечание', 'замечания', 'замечаний'))))),
                    unsafe_allow_html=True)
        with st.expander('Состояние источников: по каждому источнику', expanded=False):
            st.markdown('\n'.join('- ' + x for x in issues))

# ================================================================= блок 8: разобрать конкретные окна
# Ручное сравнение двух-трёх окон осталось целиком, но стало РАЗБОРОМ, а не входом: человек
# приходит спросить «когда выходить», и требовать от него расставить окна ползунками — значит
# требовать ответа, за которым он пришёл (раздел 3.1 техзадания).
with st.expander('Разобрать конкретные окна: сравнить два-три начала вручную', expanded=False):
    st.caption('Раздел для ручного сравнения и показа. На рекомендацию выше он не влияет: она '
               'считается перебором всех начал на сроке.')
    if windows_beyond_archive:
        st.warning('Последнее окно за границей архива уведомлений DONKI (май–июнь 2024). Покрытие проверяется по фактическим '
                   'интервалам каждого источника; наличие нескольких соседних суток не гарантирует полноту всех линий.')
    st.radio('Окон для сравнения', [2, 3], horizontal=True, key='n_windows')
    _wc = st.columns(len(offsets_in))
    for i in range(len(offsets_in)):
        _wc[i].slider('Сдвиг начала окна %d, мин после начала срока' % (i + 1), 0, search_min, step=30, key='w%d' % i)
        # Аналитик планирует в часах UTC, а не в сдвигах от начала срока: пока тянется ползунок,
        # время начала окна не видно — оно появлялось только в карточке после пересчёта.
        _ws = t0 + timedelta(minutes=int(offsets_in[i]))
        _wc[i].caption('окно %d: %s — %s UTC' % (i + 1, _ws.strftime('%d.%m %H:%M'),
                                                 (_ws + timedelta(minutes=int(duration_min))).strftime('%H:%M')))
    # Невиджетные ключи `_off<i>` записаны выше, ДО создания ползунков: писать их здесь второй раз
    # нечем — значение ползунка этого прогона уже учтено там же, откуда взялся `offsets_in`.
    if recalc:
        st.caption('сдвиги пересчитаны под период: %s' % ', '.join('окно %d — %d мин' % (i + 1, v) for i, v in enumerate(recalc)))
    if sorted(offsets_in) != offsets_in:
        st.caption('Окна пронумерованы по времени начала: окно 1 — самое раннее.')
    if dup:
        st.warning('Окна с одинаковым началом (сдвиг %s мин) сравнивать нечем: считаю по сдвигам %s мин. '
                   'Поставьте ползунки на нужные начала.'
                   % (', '.join(str(o) for o in dup), ', '.join(str(o) for o in offsets)))
    if scan is not None:
        # Вердикт по вручную заданным окнам стоит здесь, рядом со своими карточками: он отвечает
        # на другой вопрос, чем рекомендация, и два ответа на одном месте спорили бы друг с другом.
        st.markdown(verdict_panel(rec, S, windows_ru, assessments=R.assessments, pro=pro, plan_change=plan_change,
                                  missing_ru=missing_ru, policy_short=policy_short,
                                  thr_nT=th.saa_B_threshold_nT, e_min_MeV=th.e_min_MeV, mode=mode),
                    unsafe_allow_html=True)
    cols = st.columns(len(R.assessments))
    for i, (col, a) in enumerate(zip(cols, R.assessments)):
        col.markdown(window_card(i, a, best=(rec.preferred is not None and rec.preferred.start_utc == a.window.start_utc),
                                 mode=mode, saa_thr_nT=th.saa_B_threshold_nT), unsafe_allow_html=True)
    # Служебная подпись о том, как считаны минуты в аномалии, одинакова у всех окон и печатается
    # один раз под парой карточек, а не в каждой: место под карточками нужно ответу (О5).
    _saa_note = saa_note_ru(th.saa_B_threshold_nT)
    if _saa_note:
        st.markdown('<div class="legend">%s</div>' % _saa_note, unsafe_allow_html=True)

# ================================================================= блок 9: вкладки
# Порядок вкладок — рабочий путь аналитика, а не порядок разработчика: вердикт → почему →
# чем подтверждается в наблюдениях и прогнозах → и только при споре формулы.
# Прежде «Методика» стояла третьей, и пользователь дважды проходил мимо формул, прежде чем
# добирался до данных. Вкладка «Карта» упразднена: её содержимое — глобус и запасная плоская
# карта — стоит блоком 4 главного экрана, и держать второе такое же место незачем.
tab_names = ['Объяснения', 'Окна и факторы', 'Наблюдения и прогнозы', 'Методика', 'Данные',
             'Перспектива'] + (['Устойчивость и нормы'] if pro else [])
tabs = st.tabs(tab_names)
TAB_CARDS, TAB_FACTORS, TAB_OBS, TAB_METHOD, TAB_DATA, TAB_OUTLOOK, TAB_ROBUST = 0, 1, 2, 3, 4, 5, 6

with tabs[TAB_CARDS]:
    _win_labels = ['Окно %d (%s)%s' % (i + 1, dt_ru(a.window.start_utc),
                                        ' — предпочтительное' if rec.preferred is not None and rec.preferred.start_utc == a.window.start_utc else '')
                   for i, a in enumerate(R.assessments)]
    if len(_win_labels) > 1:
        # О4: переключатель открывается на ПРЕДПОЧТИТЕЛЬНОМ окне. Жюри читает «Окно 2 —
        # предпочтительное», нажимает «Объяснения», чтобы проверить рекомендацию, и получало
        # карточки окна 1. Умолчание задаётся только при первом показе — тем же приёмом `_def`,
        # что у остальных элементов, иначе выбор пользователя сбрасывался бы каждым пересчётом.
        _pref_i = next((i for i, a in enumerate(R.assessments)
                        if rec.preferred is not None and rec.preferred.start_utc == a.window.start_utc), 0)
        if st.session_state.get('cards_win') not in _win_labels:
            st.session_state.pop('cards_win', None)      # подписи окон меняются с запросом: старый выбор снимаем
        _pick = st.radio('Окно', _win_labels, horizontal=True, key='cards_win',
                         **_def('cards_win', {'index': _pref_i}))
        _wi = _win_labels.index(_pick) if _pick in _win_labels else _pref_i
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
        # заголовок свёртки — тем же форматом, что весь экран: даты «дд.мм чч:мм», дроби с запятой,
        # разряды тысяч. Через frac_ru даты не проходили и оставались машинными «05-10 13:35Z» (R4-3).
        # Пустые части пропускаются: у информационных карточек уровня нет, и вкладка начиналась
        # четырьмя (на профессиональном уровне — двенадцатью) строками «— · Окно 1 · …».
        label = ' · '.join(x for x in (SEV_RU.get(c.severity, ''), screen_text(c.title), KIND_RU[c.kind]) if x)
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
                    links.append('[%s](%s)' % (record_label_ru(rid), u))
                else:
                    no_link.append(rid)
            if links or no_link:
                _n = 12 if pro else 6          # оперативному уровню хватает нескольких ссылок (U5)
                # причина отсутствия адреса у записей разная: таблицы стандартов и эфемериды сервис
                # везёт с собой, а у живой записи адрес просто не сохранён слоем источников — он есть
                # в манифесте выгрузки. Одной фразой на оба случая это была неправда (R4-10).
                st.markdown('**Первоисточник:** ' + ', '.join(links[:_n])
                            + (' … ещё %d' % (len(links) - _n) if len(links) > _n else '')
                            + ((('; ' if links else '') + record_no_url_ru(no_link, R.raw_records)) if no_link else ''))
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

with tabs[TAB_FACTORS]:
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
    st.markdown('<div class="small">%s</div>' % coverage_scope_ru(S, mode), unsafe_allow_html=True)
    # Т1: «Два сайта, перепечатывающих одно измерение, не считаются независимым подтверждением».
    # Две строки таблицы 1 показывают ОДИН показатель NOAA из двух выпусков одной службы; без
    # этой подписи они читаются как два независимых подтверждения.
    _noaa_prob = [k for k in row_keys if k[0] == 'величина' and 'вероятность' in k[1] and 'NOAA' in k[1]]
    if len(_noaa_prob) > 1:
        _rel = sorted({(x.get('published_utc') or '')[:16].replace('T', ' ')
                       for x in (S.get('forecasts') or []) if 'вероятность' in (x.get('label') or '')})
        st.markdown('<div class="small">Обе строки вероятности — <b>один показатель NOAA</b>, а не два независимых '
                    'источника: это одна служба в двух выпусках%s. Совпадение значений подтверждением '
                    'не является.</div>'
                    % ((' (' + ', '.join(screen_text(x) + ' UTC' for x in _rel if x) + ')') if any(_rel) else ''),
                    unsafe_allow_html=True)
    if pro:
        st.caption('Порядок сравнения — пять шагов: охват → условия → сравнение по каждому механизму → сведение → допуск '
                   '(формальная запись — вкладка «Методика», формулы (8) и (9)).')
    else:
        st.caption('Порядок сравнения: охват → условия → сравнение по механизмам → сведение → допуск равнозначности '
                   '(формулы — вкладка «Методика»).')

    if S.get('meteoroids'):
        with st.expander('Метеороиды: вклад потоков и чувствительность расчёта'):
            st.caption('Сезонная инженерная модель: 49 потоков ECSS, масса ≥0,001 г, '
                       'случайно ориентированная односторонняя пластина 1 м². '
                       'Всплески конкретного года не предсказываются.')
            manual_starts = {w['start_utc'] for w in S['windows']}
            models = ((start, m) for start, m in S['meteoroids'].items() if start in manual_starts)
            for number, (start, model) in enumerate(models, 1):
                st.markdown('**Окно %d**' % number)
                if not model.get('streams_included'):
                    st.warning(model.get('error', 'Сезонный расчёт недоступен'))
                    continue
                st.write('Итого %s = фон после вычитания среднего вклада потоков %s + потоки даты %s.' % (
                    fmt(model['N']), fmt(model['N_sporadic_adjusted']), fmt(model['N_streams'])))
                st.dataframe([{'поток': part['name'], 'попаданий за окно': fmt(part['expected_hits']),
                               'время вне тени Земли, мин': round(part['unblocked_duration_s']/60, 2)}
                              for part in model['contributions'][:5]], hide_index=True, width='stretch')
                sensitivity = model['sensitivity']
                st.write('Разброс при альтернативных гипотезах: %s…%s попаданий. '
                         'Это не доверительный интервал. При уменьшении шага вдвое итог меняется на %s %%.' % (
                         fmt(sensitivity['min_N']), fmt(sensitivity['max_N']),
                         fmt(100*sensitivity['half_step_relative_change'])))
                st.caption(model['limits'])
            st.caption('Все 49 вкладов, варианты расчёта и происхождение данных сохранены в ZIP-отчёте.')

with tabs[TAB_METHOD]:
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
        # О4: вкладка «Устойчивость и нормы» есть только на профессиональном уровне, и ссылка
        # на неё из формулы (9) на оперативном уровне вела в никуда.
        st.markdown('<div class="small">Источник: %s</div>' % method_source_ru(b, pro), unsafe_allow_html=True)
        st.markdown('<div class="small">Не учтено и ограничения: %s</div>' % b['limits'], unsafe_allow_html=True)
    # ------------------------------------------------ границы применимости (пункт 1 двенадцатого круга)
    # Всё, что раньше стояло россыпью на главном экране и читалось как перечень наших оправданий,
    # собрано ЗДЕСЬ, в одном разделе и в одном порядке: чего сервис не учёл, что не даёт глобус,
    # что означает проверка после отсечки. Ничего не удалено — критерии оценки прямо требуют, чтобы
    # ограничения охвата были видны в сервисе (О1 «учтены ограничения охвата», О2 «без
    # необоснованных заявлений о безопасности», О4 «ограничения и обоснование уверенности»,
    # Т1 «сказано ли, чего в данных нет»). На главном экране на это ведёт одна строка.
    st.markdown('<div class="sect">%s</div>' % LIMITS_SECTION_RU, unsafe_allow_html=True)
    # Сказано ровно то, что есть на самом деле: пропуски охвата ЭТОГО расчёта лежат в снимке
    # (ключи `coverage_declared` и `coverage_missing`) и печатаются отчётом выгрузки, а строки
    # о том, чего сервис не считает в принципе, — это правило сервиса, и живут они здесь.
    # Обещать «всё то же самое есть в отчёте» было бы заявлением, которого мы не проверяли.
    st.markdown('Раздел собран в одном месте намеренно: границы расчёта читаются подряд, а не '
                'выискиваются по экрану. Пропуски охвата этого расчёта входят и в снимок, и в '
                'выгрузку; строки о том, чего сервис не считает в принципе, — правило сервиса.')
    if _not_acc:
        st.markdown('**Чего сервис не учёл** — %s %s, поимённо и с причиной.'
                    % (fmt(len(_not_acc)), plural_ru(len(_not_acc), ('пункт', 'пункта', 'пунктов'))))
        for _line in _not_acc:
            st.markdown('- ' + _line)
    if _globe_limits or _globe_tech:
        # Приглушённой подписью, а не пунктом списка: это служебное пояснение к рисунку, и
        # набрано оно тем же кеглем, каким подписаны все рисунки сервиса. Заодно строка
        # остаётся там, где её ищут проверки глобуса, — среди подписей экрана (tests/test_globe.py).
        st.markdown('**Чем посчитан глобус и чего он не даёт.**')
        if _globe_limits:
            st.caption(_globe_limits)
        if _globe_tech:
            st.caption(_globe_tech)
    if mode == 'history_forecast' and R.verification:
        # «Обыграть нормально» из замечания владельца — это назвать своими словами, что это такое
        # и почему оно не часть расчёта, а не убрать плашку и промолчать.
        st.markdown('**Проверка после отсечки.** Сопоставление того, что знал прогноз до отсечки, '
                    'с тем, что наблюдалось потом. В расчёт она не входит и на рекомендацию не влияет: '
                    'это способ проверить сам сервис, а не обстановку. Итог: %s. Таблицы наблюдений '
                    'и событий — вкладка «Наблюдения и прогнозы».'
                    % verification_ru(R.verification['summary'], any_cond))
    st.markdown('<div class="sect">Пороги условий проверки</div>', unsafe_allow_html=True)
    st.dataframe(RULE_THRESHOLDS, width='stretch', hide_index=True,
                 column_config={'условие': st.column_config.TextColumn(width='medium'),
                                'источник': st.column_config.TextColumn(width='medium'),
                                'состояние': st.column_config.TextColumn(width='medium')})
    st.markdown('<div class="tcap">Таблица 2. Пороги, по которым окно получает условие проверки; источник и состояние — '
                'на каждую строку. %s</div>' % RULE_THRESHOLDS_NOTE, unsafe_allow_html=True)
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

with tabs[TAB_OBS]:
    # «Картина по времени» переехала сюда с главного экрана. Владелец о ней сказал: «график
    # кажется достаточно странным и нерезультативным». Лента окон его не заменяет — она про выбор
    # начала, а он про обстановку на трассе, — но место главного экрана занимает ответ, а не
    # обстановка. Здесь он стоит рядом с наблюдениями и прогнозами, из которых и построен,
    # и открыт сразу на обоих уровнях: свёрнутым его прятать нельзя (находка №33).
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
    # S8: снимок третьего круга может принести наблюдения GOES из архива 2024 — рисуем их, если ключ есть.
    for _row in (S.get('observations') or []):
        if _row.get('channel') in ('goes_p_ge10MeV', 'goes') and not goes_obs:
            goes_obs = [(datetime.fromisoformat(c['t']), float(c['value'])) for c in (_row.get('points') or [])
                        if c.get('t') and c.get('value') is not None]
    st.plotly_chart(dark_figure(timeline(traj, windows, th.saa_B_threshold_nT, t0, horizon_min, R.goes, R.kp,
                                         R.events, S.get('forecasts', []), mode, kp_obs=kp_obs, goes_obs=goes_obs,
                                         search_min=search_min)),
                    width='stretch', config=PLOTLY_CONFIG)
    # подпись описывает ряды ленты и живёт рядом с самим рисунком (app/ui.py): ряды менялись,
    # и подпись, написанная здесь, каждый раз оставалась описывать прежний вид
    st.caption(timeline_caption(mode, pro))
    st.markdown('<div class="sect">Наблюдения и прогнозы источников</div>', unsafe_allow_html=True)
    if mode == 'live':
        obs_fig = observations_panel(R.fetch_status['goes'].raw_path, R.fetch_status['kp'].raw_path, t0)
        if obs_fig is not None:
            st.plotly_chart(dark_figure(obs_fig), width='stretch', config=PLOTLY_CONFIG)
            st.caption('Наблюдения источников за последние дни, не расчёт. Пороги — шкалы NOAA S и G.' + (
                ' GOES меряет на геостационарной орбите. Обрезание — отдельный показатель; локальный поток МКС не рассчитан.'
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
                    # Причина отсутствия выпуска читается ИЗ ЗАПИСИ, а не пишется из головы:
                    # в снимке стоит «живой бюллетень NOAA 3-day не содержит этого канала…»,
                    # а экран печатал «времени выпуска в записи нет». Выгрузка при этом печатала
                    # настоящую причину, и экран с выгрузкой называли разные причины одного и того же.
                    _rel = screen_text(status_ru(line.get('reason') or 'времени выпуска в записи нет', pro))
                st.markdown('%s **%s** — %s' % (pill(line.get('status_ru') or '—',
                                                     'ok' if line.get('status') == 'full' else
                                                     'warn' if line.get('status') == 'partial' else 'none'),
                                                forecast_label_ru(line), _rel),
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
            st.plotly_chart(dark_figure(hist_obs_fig), width='stretch', config=PLOTLY_CONFIG)
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
            st.plotly_chart(dark_figure(fc_fig), width='stretch', config=PLOTLY_CONFIG)
        for line in S.get('forecasts', []):
            if line['release_id']:
                u = record_url(raw_record(R.raw_records, line['record'])) if line.get('record') else None
                pub = screen_text((line['published_utc'] or '')[:16].replace('T', ' '))
                rel = ('[выпуск от %s UTC](%s)' % (pub, u)) if u else 'выпуск от %s UTC' % pub
            else:
                rel = 'выпуска до отсечки в архиве нет'
            st.markdown('%s **%s** — %s' % (pill(line['status_ru'], 'ok' if line['status'] == 'full' else 'warn' if line['status'] == 'partial' else 'none'),
                                            forecast_label_ru(line), rel), unsafe_allow_html=True)
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

with tabs[TAB_OUTLOOK]:
    # Слово «ранжирование» на оперативном уровне запрещено (оно ничего не объясняет человеку)
    # и проверяется тестом; здесь говорим тем же языком, что и остальной экран.
    st.markdown('**Когда планировать выход дальше ближайших суток.** Это НЕ вердикт: '
                'рекомендованного окна, отбора начал по правилу и условий проверки здесь нет. '
                'Разные механизмы видят вперёд на разное расстояние, и каждый срок объявлен.')
    _ndays = st.slider('На сколько суток вперёд', min_value=3, max_value=27, value=14, step=1,
                       key='outlook_days', help='Обзор NOAA выпускается раз в неделю, поэтому '
                       'космическая погода кончается раньше выбранного срока — это видно в таблице.')
    if st.button('Посчитать перспективу', key='outlook_go'):
        try:
            from vkd.integration.noaa_27day import fetch_27day
            from vkd.windows.outlook import build_outlook, outlook_table
            with st.status('Считаю перспективу…', expanded=True) as _op:
                st.write('Обзор NOAA на 27 суток: выпуск и его время публикации')
                _o27, _ = fetch_27day()
                st.write('Геометрия трассы и метеороидные потоки по суткам')
                _res = build_outlook(now, days=int(_ndays), three_day_samples=S.get('forecasts'),
                                     outlook27=_o27, include_meteoroids=True)
                _op.update(label='Перспектива посчитана', state='complete', expanded=False)
            st.session_state['outlook_rows'] = outlook_table(_res)
            st.session_state['outlook_scope'] = getattr(_res, 'scope_ru', '')
        except Exception as _e:            # noqa: BLE001 — вкладка не роняет экран (Т6)
            LOG.error('перспектива не посчитана: %s\n%s', _e, traceback.format_exc())
            st.warning('Перспектива не посчитана: %s. Основной расчёт это не затрагивает.'
                       % type(_e).__name__)
    if st.session_state.get('outlook_rows'):
        st.dataframe(st.session_state['outlook_rows'], hide_index=True, width='stretch')
        if st.session_state.get('outlook_scope'):
            st.caption(st.session_state['outlook_scope'])

with tabs[TAB_DATA]:
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
    if mode == 'live' and not any('DONKI' in str(r.get('источник')) for r in reg_rows):
        # О1: канал уведомлений в текущем режиме не опрашивается, и это ограничение охвата
        # объявляется строкой реестра, а не выводится зрителем из нулевого счётчика событий.
        reg_rows.append(dict(LIVE_DONKI_REGISTRY_ROW))
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
    with tabs[TAB_ROBUST]:
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
