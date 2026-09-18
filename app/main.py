# -*- coding: utf-8 -*-
"""ВКД-Риск — веб-сервис для аналитика ГОГУ. Один экран сверху вниз:
запрос → траектория → картина по времени → сравнение окон → рекомендация →
предупреждения по семи пунктам → нормы (контекст) → данные и выгрузка.

Два уровня интерфейса (сессия экспертов): «Оперативный» — вердикт, лента,
причины; «Профессиональный» — всё, включая происхождение каждой величины,
сырые записи, нормы, покрытие, пороги.

Временно до A3/A4/A5: орбита из experiments/stub_orbit (дипольная L,
помечена), GOES из локального снимка, линия метеороидов не подключена —
поэтому вердикт честно «недостаточно оснований».
"""
from __future__ import annotations

import io
import json
import os
from datetime import datetime, timedelta, timezone

import plotly.graph_objects as go
import streamlit as st

from dataclasses import replace as _replace

from app.export import build_zip
from app.norms import norms_rows, s_level
from vkd.assess.cutoff import apply_cutoff
from vkd.assess.trapped import BeltTable
from vkd.explain.cards import KIND_RU, cards_for_window
from vkd.types import EnvironmentSample, Kind, Window
from vkd.windows.compare import Thresholds, assess_window, recommend
from vkd.windows.scenario import Scenario, apply_to_windows, simulated_events, simulated_kp
from vkd.windows.sensitivity import resaa, robustness

try:                                   # A2: исторический режим, когда появится
    from vkd.history import history_bundle   # type: ignore
    HIST_SRC = 'vkd.history'
except ImportError:
    from experiments.stub_history import history_bundle
    HIST_SRC = 'experiments.stub_history — временно: архив DONKI, уведомления с messageIssueTime'

try:                                   # A3: производственная орбита, когда появится
    from vkd.orbit import trajectory   # type: ignore
    ORBIT_SRC = 'vkd.orbit'
except ImportError:
    from experiments.stub_orbit import trajectory
    ORBIT_SRC = 'experiments.stub_orbit — временно, дипольная L, помечена в статусе точек'
try:                                   # A4: слой источников, когда появится
    from vkd.sources import goes_latest, kp_latest, tle_latest   # type: ignore
    SRC_LAYER = 'vkd.sources'
except ImportError:
    from experiments.stub_sources import goes_latest, kp_latest, tle_latest
    SRC_LAYER = 'experiments.stub_sources — временно: живой запрос, кеш, снимок'

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALGO_VERSION = '0.2.0-b2'
KIND_ICON = {Kind.OBSERVATION: '🟢 наблюдение', Kind.EXTERNAL_FORECAST: '🟠 внешний прогноз', Kind.OWN_CALCULATION: '🔵 наш расчёт'}
VERDICT_RU = {'preferred': 'ПРЕДПОЧТИТЕЛЬНО', 'trade_off': 'КОМПРОМИСС', 'equivalent': 'РАВНОЗНАЧНЫ',
              'insufficient': 'ОСНОВАНИЙ НЕДОСТАТОЧНО', 'all_need_check': 'ВСЕ ОКНА ТРЕБУЮТ ПРОВЕРКИ'}

st.set_page_config(page_title='ВКД-Риск', page_icon='🛰', layout='wide')
st.title('ВКД-Риск: внешняя обстановка на траектории МКС и выбор окна ВКД')
st.caption('Исследовательский прототип поддержки решений для аналитика ГОГУ. '
           'Допуск к реальной ВКД остаётся за уполномоченными специалистами. Все времена UTC.')

# ================================================================= запрос
with st.sidebar:
    level = st.radio('Уровень интерфейса', ['Оперативный', 'Профессиональный'], index=1, horizontal=True)
    pro = level == 'Профессиональный'
    st.header('Запрос')
    mode = st.radio('Режим', ['Текущая обстановка', 'Исторический разбор', 'Прогноз из прошлого'], index=0)
    cutoff_utc = None
    if mode == 'Текущая обстановка':
        t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        st.write('Начало периода поиска: **%s**' % t0.strftime('%Y-%m-%d %H:%M UTC'))
    else:
        d = st.date_input('Дата, 1 мая — 30 июня 2024', value=datetime(2024, 5, 10).date(),
                          min_value=datetime(2024, 5, 1).date(), max_value=datetime(2024, 6, 30).date())
        hh = st.slider('Час начала периода, UTC', 0, 23, 12)
        t0 = datetime(d.year, d.month, d.day, hh, tzinfo=timezone.utc)
        if mode == 'Прогноз из прошлого':
            cutoff_utc = t0
            st.write('Отсечка публикации: **%s**' % cutoff_utc.strftime('%Y-%m-%d %H:%M UTC'))
        st.warning('Исторические источники (A2, A3) ещё не подключены: показ по текущему TLE, помечено как реконструкция.')
    duration_min = st.slider('Длительность ВКД, мин', 60, 480, 360, step=30)
    search_min = st.slider('Период поиска начала, мин', 0, 1440, 720, step=60)
    n_windows = st.slider('Окон для сравнения', 2, 3, 2)
    starts = [t0 + timedelta(minutes=st.slider('Сдвиг начала окна %d, мин' % (i + 1), 0, search_min,
                                               min(i * 240, search_min), step=30, key='w%d' % i)) for i in range(n_windows)]
    st.header('Источники')
    disabled = {s: st.checkbox('Отключить %s' % s, key='dis_' + s) for s in ('goes', 'kp')}
    with st.expander('Что если — стресс-сценарий', expanded=False):
        sc_delay = st.slider('Задержка начала работ, мин', 0, 180, 0, step=15)
        sc_sep_on = st.checkbox('Смоделировать протонное событие')
        sc_sep_off = st.slider('… начало через, мин после t0', 0, 1440, 120, step=30, disabled=not sc_sep_on)
        sc_sep_pfu = st.select_slider('… уровень, pfu ≥10 МэВ', [10.0, 100.0, 1000.0, 10000.0], value=100.0, disabled=not sc_sep_on)
        sc_kp_on = st.checkbox('Смоделировать скачок Kp')
        sc_kp = st.slider('… Kp', 0.0, 9.0, 7.0, step=0.33, disabled=not sc_kp_on)
    scenario = Scenario('ui', work_delay_min=sc_delay, sep_onset_offset_min=(sc_sep_off if sc_sep_on else None),
                        sep_level_pfu=(sc_sep_pfu if sc_sep_on else None), kp_override=(sc_kp if sc_kp_on else None))
    is_sim = bool(sc_delay or sc_sep_on or sc_kp_on)
    if pro:
        st.header('Пороги и настройки')
        th = Thresholds(
            saa_B_threshold_nT=st.number_input('Порог аномалии |B|, нТл', 18000.0, 30000.0, 24000.0, 500.0),
            e_min_MeV=st.selectbox('Канал захваченных протонов, МэВ от', [12.5, 30.0, 50.0], index=1),
            goes_max_age_min=st.number_input('Свежесть GOES для будущих участков, мин', 10.0, 360.0, 60.0, 10.0),
        )
        T_months = st.slider('Длительность экспедиции для норм, мес', 1, 12, 6)
    else:
        th, T_months = Thresholds(), 6

now = datetime.now(timezone.utc)
horizon_min = search_min + duration_min

# ================================================================= данные: живые с кешем и статусами
# Кеш Streamlit на 5 минут защищает от повторных запросов при каждом движении
# ползунка. Это НЕ автоматическое обновление (разбор Codex п. 14): обновление —
# явной кнопкой ниже либо перезапуском; давность показывается всегда.
@st.cache_data(ttl=300, show_spinner=False)
def _fetch_all(dis_goes: bool, dis_kp: bool):
    return goes_latest(disabled=dis_goes), kp_latest(disabled=dis_kp), tle_latest(disabled=False)

with st.sidebar:
    if st.button('Обновить данные сейчас'):
        _fetch_all.clear()
with st.spinner('Источники: GOES, Kp, TLE — до 12 с на каждый при живом запросе…'):
    (goes, goes_raw, f_goes), (kp, kp_raw, f_kp), (tle_text, f_tle) = _fetch_all(disabled['goes'], disabled['kp'])
events, hist_raw, excluded = [], {}, []
if mode != 'Текущая обстановка':
    goes, goes_raw = None, {}            # архива GOES за 2024 в репозитории нет (A2) — линия честно без данных
    h_samples, h_events, hist_raw = history_bundle()
    cut = apply_cutoff(h_samples, h_events, [], cutoff_utc)
    excluded = list(cut.excluded)
    kp_hist = [s for s in cut.samples if s.channel_id == 'kp' and s.t_utc <= t0]
    kp = max(kp_hist, key=lambda s: s.t_utc) if (kp_hist and not disabled['kp']) else None
    kp_raw = {kp.raw_record_id: hist_raw.get(kp.raw_record_id)} if kp else {}
    events = [e for e in cut.events if e.start_utc is None or (e.start_utc <= t0 + timedelta(minutes=horizon_min) and
                                                                (e.published_utc or e.start_utc) >= t0 - timedelta(hours=48))]
# ----------------------------------------------------------------- сценарий «Что если»
kp = simulated_kp(kp, t0, scenario)
events = events + simulated_events(t0, scenario)

# ================================================================= расчёт: один снимок на рендер
with st.spinner('Траектория и поле, %d мин по 1 мин…' % horizon_min):
    meta, traj = trajectory(t0, horizon_min, th.saa_B_threshold_nT, tle_path=f_tle.raw_path)
    if mode != 'Текущая обстановка' or not f_tle.ok:
        meta = meta.__class__(**{**meta.__dict__, 'is_reconstruction': mode != 'Текущая обстановка'})

belts = BeltTable('min')
windows = apply_to_windows([Window(s, duration_min) for s in starts], scenario)


def _run(tr, kw):
    th_i = _replace(th, **kw)
    A_i = [assess_window(w, tr, belts, goes, kp, [], th_i, now, events=events) for w in windows]
    return A_i, recommend(A_i, th_i)

# устойчивость: сетка порога аномалии и канала → допуск равнозначности из разброса (О7, CONTRACT 4.5)
with st.spinner('Устойчивость вердикта на сетке порогов…'):
    rob = robustness(traj, windows, _run,
                     thr_grid=[th.saa_B_threshold_nT - 2000, th.saa_B_threshold_nT, th.saa_B_threshold_nT + 2000],
                     e_grid=[12.5, 30.0, 50.0])
th = _replace(th, equiv_tol_min=max(1.0, rob.saa_spread_min))
assessments = [assess_window(w, traj, belts, goes, kp, [], th, now, events=events) for w in windows]
rec = recommend(assessments, th)
rec = _replace(rec, is_simulated=is_sim,
               tolerance_basis='допуск %.0f мин — разброс минут в аномалии у лучшего окна при порогах %s нТл' % (
                   th.equiv_tol_min, '/'.join('%.0f' % x for x in rob.grid[0])) + ('; выбор устойчив' if rob.stable else '; ВЫБОР МЕНЯЕТСЯ на сетке'))
samples = {**({goes.raw_record_id: goes} if goes else {}), **({kp.raw_record_id: kp} if kp else {})}
cards = cards_for_window(assessments[0], samples)
ev_raw = {e.raw_record_id: hist_raw[e.raw_record_id] for e in events if e.raw_record_id in hist_raw}
raw_records = {**(goes_raw or {}), **(kp_raw or {}), **ev_raw,
               'iss.tle': {'text': tle_text, 'epoch_utc': meta.epoch_utc.isoformat() if meta.epoch_utc else None, 'fetch': f_tle.status_ru}}


def _src(f, role, sample=None):
    return {'role': role, 'status': f.status_ru, 'live_ok': f.ok, 'from_cache': f.from_cache,
            'fetched_utc': f.fetched_utc.isoformat() if f.fetched_utc else None,
            'data_utc': sample.t_utc.isoformat() if sample else None,
            'age_min': round((now - sample.t_utc).total_seconds() / 60) if sample else f.age_min}

sources = {
    'celestrak_gp': {**_src(f_tle, 'орбита'), 'epoch_utc': meta.epoch_utc.isoformat() if meta.epoch_utc else None,
                     'age_h': round((now - meta.epoch_utc).total_seconds() / 3600, 1) if meta.epoch_utc else None},
    'noaa_swpc_goes': _src(f_goes, 'протоны ≥10 МэВ', goes),
    'gfz_kp': _src(f_kp, 'Kp', kp),
    'ost1044_belts': {'role': 'захваченные протоны', 'status': belts.source, 'live_ok': None, 'from_cache': None},
    'ecss_grun': {'role': 'метеороиды', 'status': 'спецификация A5 ожидается — линия не подключена', 'live_ok': None, 'from_cache': None},
    '_layer': {'role': 'слой источников', 'status': SRC_LAYER},
}
S = {
    'algorithm_version': ALGO_VERSION, 'computed_utc': now.isoformat(), 'mode': mode, 'level': level,
    'request': {'t0_utc': t0.isoformat(), 'duration_min': duration_min, 'search_min': search_min,
                'windows': [w.start_utc.isoformat() for w in windows], 'thresholds': th.__dict__,
                'disabled': disabled, 'cutoff_utc': cutoff_utc.isoformat() if cutoff_utc else None, 'T_months': T_months},
    'trajectory_meta': {**{k: (v.isoformat() if hasattr(v, 'isoformat') else v) for k, v in meta.__dict__.items()}, 'orbit_module': ORBIT_SRC},
    'windows': [{'start_utc': a.window.start_utc.isoformat(), 'duration_min': a.window.duration_min, 'mechanisms': [
        {'id': m.mechanism_id, 'mandatory': m.mandatory, 'coverage': m.coverage.value, 'needs_check': list(m.needs_check_reasons),
         'factors': [{'name': f.name, 'value': f.value, 'unit': f.unit, 'kind': f.kind.value, 'presence': f.presence.value,
                      'coverage': f.coverage.value, 'records': list(f.record_ids), 'rule': f.rule_applied, 'limits': f.limits_note}
                     for f in m.factors]} for m in a.mechanisms]} for a in assessments],
    'recommendation': {'verdict': rec.verdict, 'rule': rec.rule_applied, 'reasons': list(rec.reasons), 'missing': list(rec.missing),
                       'preferred': rec.preferred.start_utc.isoformat() if rec.preferred else None,
                       'per_mechanism': rec.per_mechanism_comparison, 'tolerance_basis': rec.tolerance_basis},
    'cards': [{**{k: (v.value if hasattr(v, 'value') else v) for k, v in c.__dict__.items()}} for c in cards],
    'sources': sources,
    'coverage_declared': list(assessments[0].coverage_declared), 'coverage_missing': list(assessments[0].coverage_missing),
    'policy_note': 'Исключение окон с S1–S2, Kp ≥ 7 или сообщением о сближении из автоматического выбора — '
                   'консервативная политика прототипа, не эксплуатационная норма; из индексов NOAA не следует '
                   'ни прерывание, ни продолжение ВКД.',
    'is_simulated': is_sim, 'scenario': scenario.__dict__ if is_sim else None,
    'history': {'provider': HIST_SRC if mode != 'Текущая обстановка' else None, 'excluded_by_cutoff': excluded,
                'events_used': [{'id': e.event_id, 'kind': e.kind_of_event, 'published_utc': e.published_utc.isoformat() if e.published_utc else None,
                                 'start_utc': e.start_utc.isoformat() if e.start_utc else None, 'simulated': e.is_simulated} for e in events]},
    'robustness': {'stable': rob.stable, 'saa_spread_min': rob.saa_spread_min, 'grid': rob.grid,
                   'preferred_by_grid': {'%.0f nT / %g MeV' % k: v for k, v in rob.preferred_starts.items()}},
}

# ================================================================= траектория
c1, c2, c3, c4 = st.columns(4)
c1.metric('Источник орбиты', meta.source_id)
c2.metric('Эпоха элементов', meta.epoch_utc.strftime('%m-%d %H:%MZ') if meta.epoch_utc else '—')
c3.metric('Давность, ч', '%.1f' % sources['celestrak_gp']['age_h'] if sources['celestrak_gp']['age_h'] is not None else '—')
c4.metric('Метод', meta.method + (' · реконструкция' if meta.is_reconstruction else ''))
if pro:
    st.caption('Магнитные координаты: %s. Поле: %s. Шаг 1 мин, траектория %d ч (период поиска + длительность).'
               % (ORBIT_SRC, meta.field_model, horizon_min // 60))

# ================================================================= рекомендация (оперативный — сверху)
def show_verdict():
    box = st.success if rec.verdict == 'preferred' else (st.info if rec.verdict == 'equivalent' else st.warning)
    box('**%s** — правило: %s%s' % (VERDICT_RU[rec.verdict], rec.rule_applied,
                                     ' — окно с началом **%s**' % rec.preferred.start_utc.strftime('%Y-%m-%d %H:%MZ') if rec.preferred else ''))
    if rec.reasons:
        st.write('Что повлияло: ' + '; '.join(rec.reasons))
    if rec.missing:
        st.error('Чего не хватает: ' + '; '.join(rec.missing))
    st.caption('Охват: учтено — %s. Не учтено — %s.' % (', '.join(assessments[0].coverage_declared), ', '.join(assessments[0].coverage_missing)))

if is_sim:
    st.warning('**Моделируемый сценарий «Что если».** %s Результат и выгрузка помечены как сценарий; синтетические '
               'значения не попадают в кеш и в строгий исторический режим.' % ' '.join(
                   ([f'Задержка работ {sc_delay} мин — изменение плана, не улучшение условий.'] if sc_delay else []) +
                   ([f'Протонное событие {sc_sep_pfu:g} pfu через {sc_sep_off} мин.'] if sc_sep_on else []) +
                   ([f'Kp = {sc_kp:.1f}.'] if sc_kp_on else [])))
if mode == 'Прогноз из прошлого':
    st.info('**Строгий прогноз из прошлого.** Отсечка %s: использованы только записи, опубликованные до неё; '
            'исключено %d записей (список в блоке «Данные»). Орбита — реконструкция по текущему TLE до подключения OEM (A3).'
            % (cutoff_utc.strftime('%Y-%m-%d %H:%MZ'), len(excluded)))
elif mode == 'Исторический разбор':
    st.info('**Исторический разбор** по всему доступному сегодня архиву, без отсечки. Не является проверяемым прогнозом.')
if not pro:
    st.subheader('Рекомендация')
    show_verdict()

# ================================================================= картина по времени
st.subheader('Картина по времени')
fig = go.Figure()
ts_ = [p.t_utc for p in traj]
fig.add_trace(go.Scatter(x=ts_, y=[p.B_nT for p in traj], name='|B|, нТл — наш расчёт по IGRF', line=dict(width=1, color='#1f77b4')))
fig.add_hline(y=th.saa_B_threshold_nT, line_dash='dot', line_color='crimson', annotation_text='порог аномалии %.0f нТл' % th.saa_B_threshold_nT)
in_saa, seg_start = False, None
for p in traj + [None]:
    flag = bool(p.in_saa) if p else False
    if flag and not in_saa:
        seg_start = p.t_utc
    if in_saa and not flag:
        fig.add_vrect(x0=seg_start, x1=(p.t_utc if p else ts_[-1]), fillcolor='crimson', opacity=0.15, line_width=0)
    in_saa = flag
for i, w in enumerate(windows):
    fig.add_vrect(x0=w.start_utc, x1=w.start_utc + timedelta(minutes=w.duration_min), fillcolor='steelblue', opacity=0.12,
                  line_width=1, annotation_text='окно %d' % (i + 1), annotation_position='top left')
_ms = lambda t: int(t.timestamp() * 1000)     # add_vline на оси дат принимает миллисекунды эпохи, не datetime
fig.add_vline(x=_ms(t0 + timedelta(hours=24)), line_dash='dash', line_color='gray', annotation_text='горизонт 24 ч')
if goes:
    fig.add_vline(x=_ms(goes.t_utc), line_dash='dot', line_color='green', annotation_text='GOES %.2g pfu, %s' % (goes.value, s_level(goes.value)))
if kp:
    fig.add_vline(x=_ms(kp.t_utc), line_dash='dot', line_color='orange', annotation_text='Kp %.1f (%s)' % (kp.value, kp.quality), annotation_position='bottom right')
fig.update_layout(height=360, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation='h'))
st.plotly_chart(fig, use_container_width=True)
st.caption('Красное — пролёты аномалии (наш расчёт). Синее — окна-кандидаты. Серая линия — горизонт 24 ч; '
           'дальше — только собственный расчёт траектории, внешних прогнозов на этот участок нет.')

# ================================================================= сравнение
st.subheader('Сравнение окон')
rows = []
for i, a in enumerate(assessments):
    r = {'окно': '%d: %s' % (i + 1, a.window.start_utc.strftime('%m-%d %H:%MZ'))}
    for m in a.mechanisms:
        for f in m.factors:
            r[f.name] = ('%.3g' % f.value if f.value is not None else '—') + ' ' + f.unit
        r['покрытие ' + m.mechanism_id] = m.coverage.value
        if m.needs_check:
            r['условие ' + m.mechanism_id] = '; '.join(m.needs_check_reasons)
    rows.append(r)
st.dataframe(rows, use_container_width=True)
if pro:
    st.subheader('Рекомендация')
    show_verdict()
    st.caption('Допуск равнозначности: ' + rec.tolerance_basis)
    with st.expander('Устойчивость вердикта на сетке порогов (О7)', expanded=not rob.stable):
        st.write('Порог аномалии × канал захваченных протонов → предпочтительное окно. '
                 + ('**Выбор устойчив**: одно и то же окно (или одинаковый отказ) на всей сетке.' if rob.stable
                    else '**Выбор меняется** на сетке — рекомендация чувствительна к настройке, показано честно.'))
        st.dataframe([{'порог |B|, нТл': '%.0f' % k[0], 'канал, МэВ от': '%g' % k[1], 'предпочтительное окно': v or 'отказ / равнозначны'}
                      for k, v in rob.preferred_starts.items()], use_container_width=True)
    if events:
        with st.expander('События, учтённые в окне (уведомления с временем публикации)', expanded=False):
            st.dataframe([{'событие': e.event_id, 'тип': e.kind_of_event, 'происхождение': KIND_RU[e.kind],
                           'начало': e.start_utc.strftime('%m-%d %H:%MZ') if e.start_utc else '—',
                           'действие с': e.valid_from_utc.strftime('%m-%d %H:%MZ') if e.valid_from_utc else '—',
                           'публикация': e.published_utc.strftime('%m-%d %H:%MZ') if e.published_utc else 'нет — синтетика',
                           'сценарий': 'да' if e.is_simulated else ''} for e in events], use_container_width=True)

# ================================================================= предупреждения по семи пунктам
st.subheader('Предупреждения и доказательства' + ('' if pro else ' — условия и основные величины'))
shown = cards if pro else [c for c in cards if c.severity != 'info'][:3] + [c for c in cards if c.severity == 'info'][:3]
for c in shown:
    label = ('🔴 ' if c.severity == 'critical' else '🟠 ' if c.severity == 'limiting' else '') + c.title + '  ·  ' + KIND_ICON[c.kind]
    with st.expander(label, expanded=(c.severity != 'info')):
        st.markdown('**1. Воздействие и значение для ВКД.** ' + c.impact_ru)
        st.markdown('**2. Период.** ' + c.period_ru)
        st.markdown('**3. Данные и единицы.** ' + c.data_ru)
        st.markdown('**4. Источник и время публикации.** ' + c.source_ru)
        st.markdown('**5. Применённое правило или модель.** ' + c.rule_ru)
        st.markdown('**6. Ограничения и уверенность.** ' + c.limits_ru)
        st.markdown('**7. Происхождение.** ' + KIND_RU[c.kind])
        if pro and c.record_ids:
            for rid in c.record_ids:
                if rid in raw_records:
                    st.json(raw_records[rid], expanded=False)

# ================================================================= нормы — контекст
if pro:
    st.subheader('Нормы — справочный контекст, не вердикт по дозе')
    st.dataframe(norms_rows(T_months), use_container_width=True)
    st.caption('Сервис не вычисляет дозу человека: для этого нужны модели защиты и ткани, которых в обязательной '
               'части нет. Нормы показаны, чтобы аналитик соотносил показатели среды с действующими пределами.')

# ================================================================= данные и выгрузка
st.subheader('Данные и выгрузка')
if pro:
    st.dataframe([{'источник': k, **v} for k, v in sources.items()], use_container_width=True)
    if mode != 'Текущая обстановка':
        st.caption('Поставщик истории: %s' % HIST_SRC)
        with st.expander('Исключено отсечкой: %d записей' % len(excluded), expanded=False):
            st.write('\n'.join('- ' + x for x in excluded[:200]) + ('\n- …' if len(excluded) > 200 else ''))
c1, c2 = st.columns(2)
c1.download_button('Скачать расчёт (JSON)', json.dumps(S, ensure_ascii=False, indent=1, default=str),
                   file_name='vkd_risk_%s.json' % now.strftime('%Y%m%dT%H%M'), mime='application/json')
c2.download_button('Скачать архив: отчёт, запрос, факторы, сырые записи (ZIP)', build_zip(S, raw_records),
                   file_name='vkd_risk_%s.zip' % now.strftime('%Y%m%dT%H%M'), mime='application/zip')
st.caption('Экран и выгрузка построены из одного снимка расчёта; версия алгоритма %s.' % ALGO_VERSION)
