# -*- coding: utf-8 -*-
"""Экран сервиса «ВКД-Риск» — один путь пользователя (О5): запрос в боковой панели →
ответ первым (вердикт) → окна-кандидаты карточками → лента времени → вкладки с объяснениями,
факторами, картой, наблюдениями и прогнозами, данными и выгрузкой.

Экран ничего не считает: всё берётся из одного снимка расчёта app.compute.run (Т7, Т8).
Два уровня: «Оперативный» показывает решение и условия, «Профессиональный» — все величины,
пороги, устойчивость, нормы и происхождение записей.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import streamlit as st

from app.compute import ALGO_VERSION, HIST_SRC, ORBIT_SRC, SRC_LAYER, goes_latest, kp_latest, run, tle_latest
from app.export import build_zip
from app.norms import norms_rows, s_level
from app.obs import forecast_panel, observations_panel
from app.ui import COV_KIND, COV_RU, CSS, MECH_RU, fmt, head, kind_pill, pill, strip, verdict_panel, window_card
from app.viz import ground_track, timeline, window_bars
from vkd.config import section as _settings_section
from vkd.explain.cards import KIND_RU
from vkd.windows.compare import Thresholds
from vkd.windows.scenario import Scenario

UI = _settings_section('ui')          # умолчания элементов управления — config/settings.toml (Т7)
MODE_IDS = {'Текущая обстановка': 'live', 'Исторический разбор': 'history_review', 'Прогноз из прошлого': 'history_forecast'}
SEV_ICON = {'critical': '🔴', 'limiting': '🟠', 'info': '•'}

st.set_page_config(page_title='ВКД-Риск', page_icon='🛰', layout='wide', initial_sidebar_state='expanded')
st.markdown(CSS, unsafe_allow_html=True)

# ================================================================= боковая панель: запрос
with st.sidebar:
    st.markdown('### ВКД-Риск')
    level = st.radio('Уровень интерфейса', ['Оперативный', 'Профессиональный'], index=0, horizontal=True,
                     help='Оперативный — решение и условия; профессиональный — все величины, пороги, устойчивость, происхождение.')
    pro = level == 'Профессиональный'
    mode_ru = st.radio('Режим', list(MODE_IDS), index=0,
                       help='Текущая обстановка — живые источники. Исторический разбор — весь архив мая–июня 2024. '
                            'Прогноз из прошлого — только записи, опубликованные до отсечки.')
    mode = MODE_IDS[mode_ru]
    if mode == 'live':
        t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        st.caption('Начало периода поиска: **%s UTC** (сейчас)' % t0.strftime('%Y-%m-%d %H:%M'))
    else:
        d = st.date_input('Дата (1 мая — 30 июня 2024)', value=datetime(2024, 5, 10).date(),
                          min_value=datetime(2024, 5, 1).date(), max_value=datetime(2024, 6, 30).date())
        hh = st.slider('Час начала периода, UTC', 0, 23, 12)
        t0 = datetime(d.year, d.month, d.day, hh, tzinfo=timezone.utc)
        if mode == 'history_forecast':
            st.caption('Отсечка публикации: **%s UTC** — позже ничего не используется.' % t0.strftime('%Y-%m-%d %H:%M'))
    st.markdown('**Окно работ**')
    duration_min = st.slider('Длительность ВКД, мин', 60, 480, int(UI.get('duration_min', 360)), step=30)
    search_min = st.slider('Период поиска начала, мин', 0, 1440, int(UI.get('search_min', 720)), step=60)
    n_windows = st.slider('Окон для сравнения', 2, 3, 2)
    _def_off = list(UI.get('window_offsets_min', [0, 240]))
    offsets = [st.slider('Сдвиг начала окна %d, мин' % (i + 1), 0, search_min,
                         min(int(_def_off[i]) if i < len(_def_off) else i * 240, search_min), step=30, key='w%d' % i)
               for i in range(n_windows)]
    with st.expander('Источники', expanded=False):
        _SRC_STATE = {'включён': False, 'отказ: только кеш': 'cache', 'исключён: нет данных': 'off'}
        disabled = {s: _SRC_STATE[st.selectbox({'goes': 'GOES, протоны ≥10 МэВ', 'kp': 'Kp (GFZ)'}[s], list(_SRC_STATE), key='dis_' + s,
                                               help='«отказ» — живого запроса нет, берётся кеш с давностью (покрытие частичное); '
                                                    '«исключён» — данных нет, обязательная линия без покрытия → рекомендации нет')]
                    for s in ('goes', 'kp')}
        if st.button('Обновить данные сейчас'):
            st.cache_data.clear()
        _auto_def = int(UI.get('auto_refresh_min', 5))
        auto_min = st.select_slider('Автообновление в текущем режиме, мин', [0, 5, 10, 15], value=_auto_def if _auto_def in (0, 5, 10, 15) else 5,
                                    help='0 — выключено. Частота публикации: GOES 5 мин, Kp 3 ч, TLE по мере выпуска.')
    with st.expander('Что если — стресс-сценарий', expanded=False):
        sc_delay = st.slider('Задержка начала работ, мин', 0, 180, 0, step=15)
        sc_sep_on = st.checkbox('Смоделировать протонное событие')
        sc_sep_off = st.slider('… начало через, мин после t0', 0, 1440, 120, step=30, disabled=not sc_sep_on)
        sc_sep_pfu = st.select_slider('… уровень, pfu ≥10 МэВ', [10.0, 100.0, 1000.0, 10000.0], value=100.0, disabled=not sc_sep_on)
        sc_kp_on = st.checkbox('Смоделировать скачок Kp')
        sc_kp = st.slider('… Kp', 0.0, 9.0, 7.0, step=0.33, disabled=not sc_kp_on)
    scenario = Scenario('ui', work_delay_min=sc_delay, sep_onset_offset_min=(sc_sep_off if sc_sep_on else None),
                        sep_level_pfu=(sc_sep_pfu if sc_sep_on else None), kp_override=(sc_kp if sc_kp_on else None))
    TH0 = Thresholds.from_settings()          # config/settings.toml — настройки вне кода (Т7)
    if pro:
        with st.expander('Пороги и настройки', expanded=False):
            st.caption('Умолчания — из config/settings.toml; всё применённое попадает в снимок и выгрузку.')
            th = replace(TH0,
                         saa_B_threshold_nT=st.number_input('Порог аномалии |B|, нТл', 18000.0, 30000.0, TH0.saa_B_threshold_nT, 500.0),
                         e_min_MeV=st.selectbox('Канал захваченных протонов, МэВ от', [12.5, 30.0, 50.0],
                                                index=[12.5, 30.0, 50.0].index(TH0.e_min_MeV) if TH0.e_min_MeV in (12.5, 30.0, 50.0) else 1),
                         goes_max_age_min=st.number_input('Свежесть GOES для будущих участков, мин', 10.0, 360.0, TH0.goes_max_age_min, 10.0),
                         kp_check=st.number_input('Kp — триггер проверки (наблюдение, уведомление, прогнозы)', 5.0, 9.0, TH0.kp_check, 0.5),
                         tle_max_age_days=st.number_input('Допустимый возраст TLE, сут', 1.0, 14.0, TH0.tle_max_age_days, 1.0))
            T_months = st.slider('Длительность экспедиции для норм, мес', 1, 12, 6)
    else:
        th, T_months = TH0, 6

# автообновление: фрагмент перезапускает расчёт с очисткой кеша по таймеру (Т1). Стоит после боковой панели.
if mode == 'live' and auto_min:
    @st.fragment(run_every=timedelta(minutes=auto_min))
    def _auto_refresh():
        last = st.session_state.get('_auto_last')
        nowr = datetime.now(timezone.utc)
        if last is not None and (nowr - last) >= timedelta(minutes=auto_min) - timedelta(seconds=5):
            st.cache_data.clear()
            st.session_state['_auto_last'] = nowr
            st.rerun()
        if last is None:
            st.session_state['_auto_last'] = nowr
        st.sidebar.caption('Автообновление каждые %d мин; последнее: %s UTC' % (auto_min, st.session_state['_auto_last'].strftime('%H:%M:%S')))
    _auto_refresh()


# ================================================================= расчёт: один снимок на рендер
@st.cache_data(ttl=300, show_spinner=False)
def _fetch_all(dis_goes: bool, dis_kp: bool):
    """Кеш 5 мин против повторных запросов при каждом движении ползунка."""
    return goes_latest(disabled=dis_goes), kp_latest(disabled=dis_kp), tle_latest(disabled=False)

with st.spinner('Источники: GOES, Kp, TLE — до 6 с на адрес при живом запросе, затем резервы и кеш…'):
    fetched = _fetch_all(bool(disabled['goes']), bool(disabled['kp']))
with st.spinner('Траектория, поле, оценка окон, устойчивость…'):
    R = run(mode, t0, duration_min, search_min, offsets, disabled=disabled, thresholds=th,
            scenario=scenario, T_months=T_months, fetched=fetched)
S, rec, meta, traj, rob = R.S, R.rec, R.meta, R.traj, R.rob
now = datetime.fromisoformat(S['computed_utc'])
windows = [a.window for a in R.assessments]
horizon_min = search_min + duration_min
windows_ru = {a.window.start_utc: str(i + 1) for i, a in enumerate(R.assessments)}

# ================================================================= шапка и полоса состояния
st.markdown(head('ВКД-Риск', 'внешняя обстановка на траектории МКС и выбор окна ВКД · %s · все времена UTC' % mode_ru), unsafe_allow_html=True)
src = S['sources']
orbit_kind = 'crit' if meta is None else ('ok' if S['trajectory_meta']['strictness'] == 'strict' else 'warn')
orbit_txt = ('недоступна' if meta is None else '%s%s' % (meta.method, ' · реконструкция' if meta.is_reconstruction else ''))
items = [('Режим', mode_ru, 'calc' if mode == 'live' else 'fc'),
         ('Орбита', orbit_txt, orbit_kind),
         ('Эпоха' if mode == 'live' else 'OEM создан',
          ((meta.epoch_utc or meta.created_utc).strftime('%d.%m %H:%MZ') + (' · %.1f ч' % src['orbit']['age_h'] if src['orbit'].get('age_h') is not None else ''))
          if meta and (meta.epoch_utc or meta.created_utc) else '—', None)]
if mode == 'live':
    g, k = R.goes, R.kp
    items.append(('GOES ≥10 МэВ', ('%.2g pfu · %s · %s' % (g.value, s_level(g.value), g.t_utc.strftime('%H:%MZ'))) if g else src['noaa_swpc_goes']['status'][:40],
                  ('crit' if g and g.value >= th.goes_p10_priority_pfu else 'warn' if g and g.value >= th.goes_p10_warning_pfu else 'ok') if g else 'crit'))
    items.append(('Kp', ('%.1f · %s · %s' % (k.value, k.quality, k.t_utc.strftime('%d.%m %H:%MZ'))) if k else src['gfz_kp']['status'][:40],
                  ('crit' if k and k.value >= th.kp_check else 'ok') if k else 'crit'))
else:
    items.append(('Отсечка' if mode == 'history_forecast' else 'Начало периода', t0.strftime('%Y-%m-%d %H:%MZ'), None))
    items.append(('Исключено отсечкой', '%d записей' % len(R.excluded), None))
if S['is_simulated']:
    items.append(('Сценарий', 'моделируемые значения', 'warn'))
st.markdown(strip(items), unsafe_allow_html=True)
if meta is None:
    st.error('**Орбита недоступна.** %s Оценка без траектории невозможна: покрытие обязательной линии отсутствует, '
             'рекомендации нет. Заглушка не подставляется.' % S['trajectory_meta']['status'])

# ================================================================= ответ первым
st.markdown(verdict_panel(rec, S, windows_ru), unsafe_allow_html=True)
cols = st.columns(len(R.assessments))
for i, (col, a) in enumerate(zip(cols, R.assessments)):
    col.markdown(window_card(i, a, best=(rec.preferred is not None and rec.preferred.start_utc == a.window.start_utc), mode=mode),
                 unsafe_allow_html=True)
st.markdown('<div class="legend">Происхождение везде помечено: %s %s %s. Минуты в аномалии и флюенс — по трассе; '
            'условия — по наблюдениям и датированным прогнозам. %s</div>'
            % (kind_pill('observation'), kind_pill('external_forecast'), kind_pill('own_calculation'), S['policy_note']),
            unsafe_allow_html=True)

# ================================================================= лента времени
st.subheader('Картина по времени')
st.plotly_chart(timeline(traj, windows, th.saa_B_threshold_nT, t0, horizon_min, R.goes, R.kp, R.events, S.get('forecasts', []), mode),
                width='stretch')
st.caption('Верх: |B| по трассе (наш расчёт по IGRF), красные полосы — пролёты аномалии, синие — окна-кандидаты. '
           'Низ: Kp — %s; маркеры — события и прогнозы с временем публикации.' % (
               'прогноз NOAA по 3-часовым интервалам из выпуска до отсечки' if mode != 'live' else 'последнее наблюдение GFZ'))

# ================================================================= вкладки
tab_names = ['Объяснения', 'Окна и факторы', 'Карта', 'Наблюдения и прогнозы', 'Данные и выгрузка'] + (['Устойчивость и нормы'] if pro else [])
tabs = st.tabs(tab_names)

with tabs[0]:
    cards = R.cards
    shown = cards if pro else [c for c in cards if c.severity != 'info'] + [c for c in cards if c.severity == 'info'][:4]
    st.markdown('<div class="small">Каждое предупреждение раскрывается по семи пунктам постановки: воздействие, период, данные, '
                'источник и время публикации, правило, ограничения, происхождение. %s</div>'
                % ('' if pro else 'Показаны условия и основные величины окна 1; полный список — на профессиональном уровне.'),
                unsafe_allow_html=True)
    for c in shown:
        label = '%s %s · %s' % (SEV_ICON.get(c.severity, '•'), c.title, KIND_RU[c.kind])
        with st.expander(label, expanded=(c.severity != 'info')):
            st.markdown('**1. Воздействие и значение для ВКД.** ' + c.impact_ru)
            st.markdown('**2. Период.** ' + c.period_ru)
            st.markdown('**3. Данные и единицы.** ' + c.data_ru)
            st.markdown('**4. Источник и время публикации.** ' + c.source_ru)
            st.markdown('**5. Применённое правило или модель.** ' + c.rule_ru)
            st.markdown('**6. Ограничения и уверенность.** ' + c.limits_ru)
            st.markdown('**7. Происхождение.** ' + KIND_RU[c.kind])
            links = []
            for rid in c.record_ids:
                rec_ = R.raw_records.get(rid) or {}
                u = (rec_.get('url') or rec_.get('link') or rec_.get('messageURL')) if isinstance(rec_, dict) else None
                links.append('[%s](%s)' % (rid, u) if u else '`%s`' % rid)
            if links:
                st.markdown('**Первоисточник:** ' + ', '.join(links[:12]) + (' …' if len(links) > 12 else ''))
            if pro:
                for rid in c.record_ids[:12]:
                    if rid in R.raw_records:
                        st.json(R.raw_records[rid], expanded=False)

with tabs[1]:
    st.plotly_chart(window_bars(R.assessments), width='stretch')
    rows = []
    for i, a in enumerate(R.assessments):
        r = {'окно': '%d · %s' % (i + 1, a.window.start_utc.strftime('%d.%m %H:%MZ'))}
        for m in a.mechanisms:
            for f in m.factors:
                r[f.name] = fmt(f.value, f.unit)
            r['покрытие: ' + MECH_RU.get(m.mechanism_id, m.mechanism_id)] = COV_RU[m.coverage.value]
            if m.needs_check:
                r['условия: ' + MECH_RU.get(m.mechanism_id, m.mechanism_id)] = '; '.join(m.needs_check_reasons)
        rows.append(r)
    st.dataframe(rows, width='stretch')
    st.caption('Правило сравнения — CONTRACT.md раздел 4: покрытие → условия → сравнение по каждому механизму → сведение → допуск. '
               + rec.tolerance_basis)

with tabs[2]:
    if traj:
        with st.spinner('Область аномалии по IGRF на сетке 4°…'):
            st.plotly_chart(ground_track(traj, windows, th.saa_B_threshold_nT, t0), width='stretch')
        st.caption('Область аномалии — наш расчёт |B| по IGRF на средней высоте трассы; трасса за весь горизонт серым, '
                   'окна-кандидаты цветом, точки трассы в аномалии красным. Карта показывает, откуда берутся минуты в аномалии.')
    else:
        st.write('Трассы нет: орбита недоступна.')

with tabs[3]:
    if mode == 'live':
        obs_fig = observations_panel(R.fetch_status['goes'].raw_path, R.fetch_status['kp'].raw_path, t0)
        if obs_fig is not None:
            st.plotly_chart(obs_fig, width='stretch')
            st.caption('Наблюдения источников за последние дни, не расчёт. Пороги — шкалы NOAA S и G; GOES меряет на '
                       'геостационарной орбите и переносится на станцию только через геомагнитное обрезание.')
        else:
            st.write('Рядов наблюдений нет: источники отключены или недоступны.')
    else:
        fc_fig = forecast_panel(S.get('forecasts', []), t0, horizon_min)
        if fc_fig is not None:
            st.plotly_chart(fc_fig, width='stretch')
        for line in S.get('forecasts', []):
            st.markdown('%s **%s** — %s%s' % (
                pill(line['status_ru'], 'ok' if line['status'] == 'full' else 'warn' if line['status'] == 'partial' else 'none'),
                line['label'], line['status_ru'] if False else '',
                ('выпуск %s от %s' % (line['release_id'], line['published_utc'][:16].replace('T', ' '))) if line['release_id'] else 'выпуска до отсечки в архиве нет'),
                unsafe_allow_html=True)
        st.caption('Внешний прогноз, не наблюдение. Суточные вероятности относятся к суткам, а не к окну ВКД; прогноз Kp — по '
                   '3-часовым интервалам. Отбор выпуска по времени публикации (A2).')
    if R.events:
        st.markdown('**События и прогнозы, учтённые на горизонте** (время публикации — из записи источника)')
        st.dataframe([{'событие': e.event_id, 'тип': e.kind_of_event, 'происхождение': KIND_RU[e.kind],
                       'начало / приход': e.start_utc.strftime('%d.%m %H:%MZ') if e.start_utc else '—',
                       'публикация': e.published_utc.strftime('%d.%m %H:%MZ') if e.published_utc else 'нет — синтетика',
                       'примечание': (e.note or '')[:120], 'сценарий': 'да' if e.is_simulated else ''} for e in R.events], width='stretch')

with tabs[4]:
    st.markdown('**Источники этого расчёта** — статус, получение, давность; каждый фактор прослеживается до записи в выгрузке.')
    st.dataframe([{'источник': k, 'роль': v.get('role'), 'статус': v.get('status'), 'живой запрос': v.get('live_ok'),
                   'из кеша': v.get('from_cache'), 'получено': (v.get('fetched_utc') or '')[:16].replace('T', ' '),
                   'данные на': (v.get('data_utc') or '')[:16].replace('T', ' '), 'давность, мин': v.get('age_min')}
                  for k, v in src.items() if not k.startswith('_')], width='stretch')
    if mode != 'live':
        with st.expander('Исключено отсечкой: %d записей' % len(R.excluded), expanded=False):
            st.write('\n'.join('- ' + x for x in R.excluded[:200]) + ('\n- …' if len(R.excluded) > 200 else ''))
    if pro and S['trajectory_meta']['provenance'].get('limitations'):
        with st.expander('Орбита: происхождение и ограничения (A3)', expanded=False):
            st.write('\n'.join('- ' + x for x in S['trajectory_meta']['provenance']['limitations']))
            st.json({k: v for k, v in S['trajectory_meta']['provenance'].items() if k != 'limitations'}, expanded=False)
    c1, c2 = st.columns(2)
    c1.download_button('Скачать расчёт (JSON)', json.dumps(S, ensure_ascii=False, indent=1, default=str),
                       file_name='vkd_risk_%s.json' % now.strftime('%Y%m%dT%H%M'), mime='application/json', width='stretch')
    c2.download_button('Скачать архив: отчёт, запрос, факторы, сырые записи (ZIP)', build_zip(S, R.raw_records),
                       file_name='vkd_risk_%s.zip' % now.strftime('%Y%m%dT%H%M'), mime='application/zip', width='stretch')
    st.caption('Экран и выгрузка построены из одного снимка; версия алгоритма %s. Слои: %s; %s; %s.'
               % (ALGO_VERSION, ORBIT_SRC.split(':')[0], SRC_LAYER.split(' — ')[0], HIST_SRC.split(' — ')[0]))

if pro:
    with tabs[5]:
        st.markdown('**Устойчивость вердикта на сетке порогов (О7).** Порог аномалии × канал захваченных протонов → '
                    'предпочтительное окно. ' + ('**Выбор устойчив**: одно и то же окно (или одинаковый отказ) на всей сетке.'
                                                if rob.stable else '**Выбор меняется** на сетке — рекомендация чувствительна к настройке.'))
        st.dataframe([{'порог |B|, нТл': '%.0f' % k[0], 'канал, МэВ от': '%g' % k[1], 'предпочтительное окно': v or 'отказ / равнозначны'}
                      for k, v in rob.preferred_starts.items()], width='stretch')
        st.caption('Допуск равнозначности: ' + rec.tolerance_basis)
        st.markdown('**Нормы — справочный контекст, не вердикт по дозе.** Сервис не вычисляет дозу человека: для этого нужны '
                    'модели защиты и ткани, которых в обязательной части нет.')
        st.dataframe(norms_rows(T_months), width='stretch')
