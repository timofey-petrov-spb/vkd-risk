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

from app.export import build_zip
from app.norms import norms_rows, s_level
from vkd.assess.trapped import BeltTable
from vkd.explain.cards import KIND_RU, cards_for_window
from vkd.types import EnvironmentSample, Kind, Window
from vkd.windows.compare import Thresholds, assess_window, recommend

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
    if mode != 'Текущая обстановка':
        goes, goes_raw = None, {}        # живое наблюдение не относится к исторической дате (A2 подключит архив)
        kp, kp_raw = None, {}

# ================================================================= расчёт: один снимок на рендер
with st.spinner('Траектория и поле, %d мин по 1 мин…' % horizon_min):
    meta, traj = trajectory(t0, horizon_min, th.saa_B_threshold_nT, tle_path=f_tle.raw_path)
    if mode != 'Текущая обстановка' or not f_tle.ok:
        meta = meta.__class__(**{**meta.__dict__, 'is_reconstruction': mode != 'Текущая обстановка'})

belts = BeltTable('min')
windows = [Window(s, duration_min) for s in starts]
assessments = [assess_window(w, traj, belts, goes, kp, [], th, now) for w in windows]
rec = recommend(assessments, th)
samples = {**({goes.raw_record_id: goes} if goes else {}), **({kp.raw_record_id: kp} if kp else {})}
cards = cards_for_window(assessments[0], samples)
raw_records = {**(goes_raw or {}), **(kp_raw or {}),
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
c1, c2 = st.columns(2)
c1.download_button('Скачать расчёт (JSON)', json.dumps(S, ensure_ascii=False, indent=1, default=str),
                   file_name='vkd_risk_%s.json' % now.strftime('%Y%m%dT%H%M'), mime='application/json')
c2.download_button('Скачать архив: отчёт, запрос, факторы, сырые записи (ZIP)', build_zip(S, raw_records),
                   file_name='vkd_risk_%s.zip' % now.strftime('%Y%m%dT%H%M'), mime='application/zip')
st.caption('Экран и выгрузка построены из одного снимка расчёта; версия алгоритма %s.' % ALGO_VERSION)
