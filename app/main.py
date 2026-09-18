# -*- coding: utf-8 -*-
"""ВКД-Риск — веб-сервис для аналитика ГОГУ. Каркас B1, один экран сверху вниз:
запрос → траектория → картина по времени → сравнение окон → предупреждения
и доказательства → данные и выгрузка. Streamlit.

Что здесь временно: орбита из experiments/stub_orbit (до vkd.orbit, A3);
GOES и Kp из локальных снимков (до vkd.sources, A4); линия метеороидов
не подключена (до A5/B2) — поэтому вердикт честно «недостаточно оснований».
"""
from __future__ import annotations

import io
import json
import os
from datetime import datetime, timedelta, timezone

import plotly.graph_objects as go
import streamlit as st

from vkd.assess.trapped import BeltTable
from vkd.types import EnvironmentSample, Kind, Window
from vkd.windows.compare import Thresholds, assess_window, recommend

try:                                   # A3: производственная орбита, когда появится
    from vkd.orbit import trajectory   # type: ignore
    ORBIT_SRC = 'vkd.orbit'
except ImportError:
    from experiments.stub_orbit import trajectory
    ORBIT_SRC = 'experiments.stub_orbit (временно, дипольная L)'

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALGO_VERSION = '0.1.0-b1'

st.set_page_config(page_title='ВКД-Риск', page_icon='🛰', layout='wide')
st.title('ВКД-Риск: внешняя обстановка на траектории МКС и выбор окна ВКД')
st.caption('Исследовательский прототип поддержки решений для аналитика ГОГУ. '
           'Допуск к реальной ВКД остаётся за уполномоченными специалистами.')

# ---------------------------------------------------------------- запрос
with st.sidebar:
    st.header('Запрос')
    mode = st.radio('Режим', ['Текущая обстановка', 'Исторический разбор', 'Прогноз из прошлого'], index=0)
    if mode == 'Текущая обстановка':
        t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        st.write('Начало периода поиска: **%s UTC**' % t0.strftime('%Y-%m-%d %H:%M'))
    else:
        d = st.date_input('Дата (1 мая — 30 июня 2024)', value=datetime(2024, 5, 10).date(),
                          min_value=datetime(2024, 5, 1).date(), max_value=datetime(2024, 6, 30).date())
        hh = st.slider('Час начала периода, UTC', 0, 23, 12)
        t0 = datetime(d.year, d.month, d.day, hh, tzinfo=timezone.utc)
        st.warning('Исторические источники (A2, A3) ещё не подключены: показ по текущему TLE как реконструкция.')
    duration_min = st.slider('Длительность ВКД, мин', 60, 480, 360, step=30)
    search_min = st.slider('Период поиска начала, мин', 0, 1440, 720, step=60)
    n_windows = st.slider('Окон для сравнения', 2, 3, 2)
    starts = [t0 + timedelta(minutes=st.slider('Сдвиг начала окна %d, мин' % (i + 1), 0, search_min,
                                               min(i * 240, search_min), step=30, key='w%d' % i)) for i in range(n_windows)]
    st.header('Источники')
    disabled = {s: st.checkbox('Отключить %s' % s, key='dis_' + s) for s in ('goes', 'kp')}
    st.header('Пороги')
    th = Thresholds(
        saa_B_threshold_nT=st.number_input('Порог аномалии |B|, нТл', 18000.0, 30000.0, 24000.0, 500.0),
        e_min_MeV=st.selectbox('Канал захваченных протонов, МэВ от', [12.5, 30.0, 50.0], index=1),
    )

now = datetime.now(timezone.utc)

# ---------------------------------------------------------------- траектория
horizon_min = search_min + duration_min
meta, traj = trajectory(t0, horizon_min, th.saa_B_threshold_nT)
age_h = (now - meta.epoch_utc).total_seconds() / 3600 if meta.epoch_utc else None
c1, c2, c3, c4 = st.columns(4)
c1.metric('Источник орбиты', meta.source_id)
c2.metric('Эпоха элементов', meta.epoch_utc.strftime('%m-%d %H:%MZ') if meta.epoch_utc else '—')
c3.metric('Давность, ч', '%.1f' % age_h if age_h is not None else '—')
c4.metric('Метод', meta.method + (' · реконструкция' if meta.is_reconstruction else ''))
st.caption('Магнитные координаты: %s. Поле: %s. Шаг 1 мин, горизонт %d ч.' % (ORBIT_SRC, meta.field_model, horizon_min // 60))

# ---------------------------------------------------------------- среда: снимки (до A4)
def _latest_goes():
    p = os.path.join(ROOT, 'data', 'spaceweather', 'goes_protons_3day.json')
    d = json.load(io.open(p, encoding='utf-8'))
    p10 = [x for x in d if x.get('energy') == '>=10 MeV' and x.get('flux') is not None]
    if not p10:
        return None
    last = max(p10, key=lambda x: x['time_tag'])
    t = datetime.fromisoformat(last['time_tag'].replace('Z', '+00:00'))
    return EnvironmentSample(t, 'goes_p_ge10MeV', float(last['flux']), 'pfu', 'noaa_swpc_goes', Kind.OBSERVATION,
                             None, None, None, now, 'preliminary', 'goes_protons_3day.json#' + last['time_tag'])

goes = None if disabled['goes'] else _latest_goes()
kp = None   # до A4: GFZ JSON-API
belts = BeltTable('min')
windows = [Window(s, duration_min) for s in starts]
assessments = [assess_window(w, traj, belts, goes, kp, [], th, now) for w in windows]
rec = recommend(assessments, th)

# ---------------------------------------------------------------- картина по времени
st.subheader('Картина по времени')
fig = go.Figure()
ts_ = [p.t_utc for p in traj]
fig.add_trace(go.Scatter(x=ts_, y=[p.B_nT for p in traj], name='|B|, нТл (наш расчёт по IGRF)', line=dict(width=1)))
fig.add_hline(y=th.saa_B_threshold_nT, line_dash='dot', annotation_text='порог аномалии')
in_saa = False; seg_start = None
for p in traj + [None]:
    flag = bool(p.in_saa) if p else False
    if flag and not in_saa:
        seg_start = p.t_utc
    if in_saa and (not flag):
        fig.add_vrect(x0=seg_start, x1=(p.t_utc if p else ts_[-1]), fillcolor='crimson', opacity=0.15, line_width=0)
    in_saa = flag
for i, w in enumerate(windows):
    fig.add_vrect(x0=w.start_utc, x1=w.start_utc + timedelta(minutes=w.duration_min), fillcolor='steelblue',
                  opacity=0.12, line_width=1, annotation_text='окно %d' % (i + 1), annotation_position='top left')
fig.update_layout(height=340, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation='h'))
st.plotly_chart(fig, use_container_width=True)
st.caption('Красное — пролёты аномалии (наш расчёт). Синее — окна-кандидаты. Все времена UTC.')

# ---------------------------------------------------------------- сравнение
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
verdict_ru = {'preferred': 'ПРЕДПОЧТИТЕЛЬНО', 'trade_off': 'КОМПРОМИСС', 'equivalent': 'РАВНОЗНАЧНЫ',
              'insufficient': 'ОСНОВАНИЙ НЕДОСТАТОЧНО', 'all_need_check': 'ВСЕ ОКНА ТРЕБУЮТ ПРОВЕРКИ'}[rec.verdict]
(st.success if rec.verdict == 'preferred' else st.warning)(
    '**%s** — правило: %s' % (verdict_ru, rec.rule_applied) +
    (' — окно с началом %s' % rec.preferred.start_utc.strftime('%Y-%m-%d %H:%MZ') if rec.preferred else ''))
if rec.reasons:
    st.write('Что повлияло: ' + '; '.join(rec.reasons))
if rec.missing:
    st.error('Чего не хватает: ' + '; '.join(rec.missing))
st.caption('Охват: учтено — %s. Не учтено — %s.' % (', '.join(assessments[0].coverage_declared),
                                                  ', '.join(assessments[0].coverage_missing)))

# ---------------------------------------------------------------- доказательства
st.subheader('Предупреждения и доказательства')
for a in assessments[:1]:
    for m in a.mechanisms:
        for f in m.factors:
            with st.expander('%s — %s' % (f.name, {'observation': 'наблюдение', 'external_forecast': 'внешний прогноз',
                                                    'own_calculation': 'наш расчёт'}[f.kind.value])):
                st.write('Значение: **%s %s**' % ('%.4g' % f.value if f.value is not None else '—', f.unit))
                st.write('Правило или модель: ' + f.rule_applied)
                st.write('Записи: ' + (', '.join(f.record_ids) or '—'))
                st.write('Покрытие: %s. Наличие: %s.' % (f.coverage.value, f.presence.value))
                if f.limits_note:
                    st.write('Ограничения: ' + f.limits_note)

# ---------------------------------------------------------------- данные и выгрузка
st.subheader('Данные и выгрузка')
st.write({'goes_protons_3day.json': 'локальный снимок NOAA SWPC' + (' — ОТКЛЮЧЁН' if disabled['goes'] else ''),
          'kp': 'не подключён (A4)', 'iss.tle': 'CelesTrak, эпоха %s' % (meta.epoch_utc.strftime('%Y-%m-%d %H:%MZ') if meta.epoch_utc else '—'),
          'ost1044_belts': belts.source})
snapshot = {
    'algorithm_version': ALGO_VERSION, 'computed_utc': now.isoformat(), 'mode': mode,
    'request': {'t0_utc': t0.isoformat(), 'duration_min': duration_min, 'search_min': search_min,
                'windows': [w.start_utc.isoformat() for w in windows], 'thresholds': th.__dict__, 'disabled': disabled},
    'trajectory_meta': {k: (v.isoformat() if hasattr(v, 'isoformat') else v) for k, v in meta.__dict__.items()},
    'windows': [{'start_utc': a.window.start_utc.isoformat(), 'mechanisms': [
        {'id': m.mechanism_id, 'coverage': m.coverage.value, 'needs_check': m.needs_check_reasons,
         'factors': [{'name': f.name, 'value': f.value, 'unit': f.unit, 'kind': f.kind.value,
                      'coverage': f.coverage.value, 'records': f.record_ids, 'rule': f.rule_applied} for f in m.factors]}
        for m in a.mechanisms]} for a in assessments],
    'recommendation': {'verdict': rec.verdict, 'rule': rec.rule_applied, 'reasons': rec.reasons, 'missing': rec.missing,
                       'preferred': rec.preferred.start_utc.isoformat() if rec.preferred else None},
}
st.download_button('Скачать расчёт (JSON)', json.dumps(snapshot, ensure_ascii=False, indent=1, default=str),
                   file_name='vkd_risk_%s.json' % now.strftime('%Y%m%dT%H%M'), mime='application/json')
