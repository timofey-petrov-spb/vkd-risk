# -*- coding: utf-8 -*-
"""Визуализации: карта трассы с аномалией, сравнение окон. Только Plotly.

Правила оформления (docs/ZAMYSEL.md 5.2): три цвета — синий (наш расчёт),
красный (аномалия/условия), серый; окна — голубые. Всё, что нарисовано,
имеет происхождение: аномалия — наш расчёт по IGRF, окна — запрос.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import plotly.graph_objects as go

BLUE, RED, GREY, WIN = '#1f4e79', '#c0392b', '#7f8c8d', '#5dade2'


def saa_grid(alt_km: float, thr_nT: float, when: datetime, step_deg: float = 4.0):
    """Сетка точек, где |B| по IGRF ниже порога на высоте alt_km — область аномалии.
    Наш расчёт; сетка грубая (шаг step_deg) — для показа, не для расчёта."""
    import ppigrf
    lats = np.arange(-70, 70.001, step_deg)
    lons = np.arange(-180, 180.001, step_deg)
    LO, LA = np.meshgrid(lons, lats)
    Be, Bn, Bu = ppigrf.igrf(LO.ravel(), LA.ravel(), np.full(LO.size, alt_km), when.replace(tzinfo=None))
    B = np.sqrt(Be ** 2 + Bn ** 2 + Bu ** 2).ravel()
    m = B < thr_nT
    return LO.ravel()[m], LA.ravel()[m], B[m]


def ground_track(traj, windows, thr_nT: float, when: datetime) -> go.Figure:
    """Карта: область аномалии (наш расчёт), трасса за горизонт, окна ярко, начала окон подписаны."""
    fig = go.Figure()
    glo, gla, gb = saa_grid(float(np.mean([p.alt_km for p in traj])), thr_nT, when)
    if len(glo):
        fig.add_trace(go.Scattergeo(lon=glo, lat=gla, mode='markers', name='аномалия, |B| < %.0f нТл (наш расчёт по IGRF)' % thr_nT,
                                    marker=dict(size=9, color=RED, opacity=0.18, symbol='square'), hoverinfo='skip'))
    # трасса: разрываем линию на переходе через 180° долготы
    lon = np.array([p.lon_deg for p in traj]); lat = np.array([p.lat_deg for p in traj])
    jump = np.where(np.abs(np.diff(lon)) > 180)[0]
    lon_l, lat_l = lon.astype(float).tolist(), lat.astype(float).tolist()
    for j in reversed(jump):
        lon_l.insert(j + 1, None); lat_l.insert(j + 1, None)
    fig.add_trace(go.Scattergeo(lon=lon_l, lat=lat_l, mode='lines', name='трасса, шаг 1 мин', line=dict(color=GREY, width=1), hoverinfo='skip'))
    in_saa_pts = [p for p in traj if p.in_saa]
    if in_saa_pts:
        fig.add_trace(go.Scattergeo(lon=[p.lon_deg for p in in_saa_pts], lat=[p.lat_deg for p in in_saa_pts], mode='markers',
                                    name='точки трассы в аномалии', marker=dict(size=3, color=RED), hoverinfo='skip'))
    for i, w in enumerate(windows):
        end = w.start_utc + timedelta(minutes=w.duration_min)
        seg = [p for p in traj if w.start_utc <= p.t_utc < end]
        if not seg:
            continue
        slon = np.array([p.lon_deg for p in seg]); slat = np.array([p.lat_deg for p in seg])
        jj = np.where(np.abs(np.diff(slon)) > 180)[0]
        sl, sa = slon.astype(float).tolist(), slat.astype(float).tolist()
        for j in reversed(jj):
            sl.insert(j + 1, None); sa.insert(j + 1, None)
        fig.add_trace(go.Scattergeo(lon=sl, lat=sa, mode='lines', name='окно %d: %s' % (i + 1, w.start_utc.strftime('%m-%d %H:%MZ')),
                                    line=dict(color=WIN if i else BLUE, width=3),
                                    hovertemplate='окно %d<br>%%{lat:.1f}°, %%{lon:.1f}°<extra></extra>' % (i + 1)))
        fig.add_trace(go.Scattergeo(lon=[seg[0].lon_deg], lat=[seg[0].lat_deg], mode='markers+text', text=['старт %d' % (i + 1)],
                                    textposition='top center', marker=dict(size=9, color=WIN if i else BLUE, symbol='circle'),
                                    showlegend=False, hoverinfo='skip'))
    fig.update_geos(projection_type='equirectangular', showcountries=False, showcoastlines=True, coastlinecolor='#bbb',
                    showland=True, landcolor='#f7f7f7', showocean=True, oceancolor='#ffffff', lataxis_range=[-75, 75])
    fig.update_layout(height=420, margin=dict(l=0, r=0, t=10, b=0), legend=dict(orientation='h', y=-0.02))
    return fig


def window_bars(assessments) -> go.Figure:
    """Сравнение окон: минуты в аномалии (столбцы), флюенс (лог, точки), попадания метеороидов (подпись)."""
    names, saa, flu, mm, flagged = [], [], [], [], []
    for i, a in enumerate(assessments):
        f = {x.name: x.value for m in a.mechanisms for x in m.factors}
        names.append('окно %d\n%s' % (i + 1, a.window.start_utc.strftime('%m-%d %H:%MZ')))
        saa.append(f.get('минут в аномалии') or 0.0)
        flu.append(next((v for n, v in f.items() if n.startswith('флюенс')), None))
        mm.append(f.get('ожидаемое число попаданий, пластина 1 м²'))
        flagged.append(any(m.needs_check for m in a.mechanisms))
    fig = go.Figure()
    fig.add_trace(go.Bar(x=names, y=saa, name='минут в аномалии (наш расчёт)',
                         marker_color=[RED if fl else BLUE for fl in flagged],
                         text=['%.0f мин%s' % (s, ' · условие' if fl else '') for s, fl in zip(saa, flagged)], textposition='outside'))
    if any(v is not None for v in flu):
        fig.add_trace(go.Scatter(x=names, y=[v if v else None for v in flu], name='флюенс протонов ≥E_min, част./см² (лог)',
                                 mode='markers', marker=dict(size=14, color=GREY, symbol='diamond'), yaxis='y2'))
    for n, v in zip(names, mm):
        if v is not None:
            fig.add_annotation(x=n, y=0, yshift=-28, text='метеороиды: N = %.2g' % v, showarrow=False, font=dict(size=11, color=GREY))
    fig.update_layout(height=340, margin=dict(l=10, r=10, t=20, b=60), barmode='group', legend=dict(orientation='h'),
                      yaxis=dict(title='мин'), yaxis2=dict(title='част./см²', overlaying='y', side='right', type='log', showgrid=False))
    return fig
