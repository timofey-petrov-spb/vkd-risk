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


def _style(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(template='plotly_white', height=height, margin=dict(l=10, r=10, t=36, b=10),
                      font=dict(family='Segoe UI, Inter, Roboto, Arial, sans-serif', size=12, color='#1a1f2b'),
                      legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='left', x=0, font=dict(size=11)),
                      hovermode='x unified', paper_bgcolor='white', plot_bgcolor='white')
    fig.update_xaxes(showgrid=True, gridcolor='#eef0f3', zeroline=False, showline=True, linecolor='#d7dbdf')
    fig.update_yaxes(showgrid=True, gridcolor='#eef0f3', zeroline=False, showline=True, linecolor='#d7dbdf')
    return fig


def timeline(traj, windows, thr_nT: float, t0: datetime, horizon_min: int, goes, kp, events, forecasts: list,
             mode: str, kp_obs=(), goes_obs=(), past_h: float = 12.0) -> go.Figure:
    """Одна лента: слева прошедшие наблюдения (past_h часов), справа горизонт окон.
    Верх — |B| по трассе (наш расчёт), пролёты аномалии красным, окна-кандидаты синим.
    Низ — Kp: наблюдения столбцами (kp_obs: (от, до, значение)), в строгой истории —
    прогноз NOAA из выпуска до отсечки; GOES ≥10 МэВ — линия по правой оси (goes_obs: (t, pfu));
    события и прогнозы прихода — маркеры с подписью."""
    from plotly.subplots import make_subplots
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.06, row_heights=[0.6, 0.4],
                        specs=[[{}], [{'secondary_y': True}]])
    ms = lambda t: int(t.timestamp() * 1000)
    end_h = t0 + timedelta(minutes=horizon_min)
    x_from = t0 - timedelta(hours=past_h if (kp_obs or goes_obs) else 1)
    fig.add_vline(x=ms(t0), line_color='#1a1f2b', line_width=1.2,
                  annotation_text='сейчас' if mode == 'live' else ('отсечка' if mode == 'history_forecast' else 'начало периода'),
                  annotation_position='top right', annotation_font_size=10)
    if kp_obs:
        xs = [a + (b - a) / 2 for a, b, _ in kp_obs]
        wd = [(b - a).total_seconds() * 1000 * 0.92 for a, b, _ in kp_obs]
        fig.add_trace(go.Bar(x=xs, y=[v for _, _, v in kp_obs], width=wd, name='Kp наблюдение',
                             marker_color=[RED if v >= 7 else '#9aa5b1' for _, _, v in kp_obs], opacity=0.9,
                             hovertemplate='Kp %{y:.2f}<extra></extra>'), row=2, col=1, secondary_y=False)
    if goes_obs:
        fig.add_trace(go.Scatter(x=[t for t, _ in goes_obs], y=[v for _, v in goes_obs], name='GOES ≥10 МэВ, pfu (наблюдение)',
                                 line=dict(width=1.2, color='#1e8449'), hovertemplate='%{y:.3g} pfu<extra></extra>'),
                      row=2, col=1, secondary_y=True)
        fig.update_yaxes(type='log', title_text='pfu', range=[-1, 4.2], showgrid=False, row=2, col=1, secondary_y=True)
    if traj:
        ts = [p.t_utc for p in traj]
        fig.add_trace(go.Scatter(x=ts, y=[p.B_nT for p in traj], name='|B| на трассе, нТл (наш расчёт по IGRF)',
                                 line=dict(width=1.4, color=BLUE), hovertemplate='%{y:.0f} нТл<extra></extra>'), row=1, col=1)
        fig.add_hline(y=thr_nT, line_dash='dot', line_color=RED, line_width=1, row=1, col=1,
                      annotation_text='порог аномалии %.0f нТл' % thr_nT, annotation_position='top left', annotation_font_size=10)
        in_saa, seg = False, None
        for p in traj + [None]:
            flag = bool(p.in_saa) if p else False
            if flag and not in_saa:
                seg = p.t_utc
            if in_saa and not flag:
                fig.add_vrect(x0=seg, x1=(p.t_utc if p else ts[-1]), fillcolor=RED, opacity=0.14, line_width=0, row=1, col=1)
            in_saa = flag
    for i, w in enumerate(windows):
        x1 = w.start_utc + timedelta(minutes=w.duration_min)
        for r in (1, 2):
            fig.add_vrect(x0=w.start_utc, x1=x1, fillcolor=WIN if i else BLUE, opacity=0.10, line_width=0, row=r, col=1)
        fig.add_annotation(x=w.start_utc, y=1.0, xref='x', yref='paper', text='окно %d' % (i + 1), showarrow=False,
                           xanchor='left', yanchor='bottom', font=dict(size=11, color=BLUE))
    fig.add_vline(x=ms(t0 + timedelta(hours=24)), line_dash='dash', line_color=GREY, line_width=1,
                  annotation_text='граница периода начала (24 ч)', annotation_position='bottom right', annotation_font_size=10)
    # --- низ: Kp
    fc = {l['channel']: l for l in (forecasts or [])}
    cells = fc.get('kp_forecast', {}).get('cells', [])
    if cells:
        x = [datetime.fromisoformat(c['from']) + (datetime.fromisoformat(c['to']) - datetime.fromisoformat(c['from'])) / 2 for c in cells]
        wd = [(datetime.fromisoformat(c['to']) - datetime.fromisoformat(c['from'])).total_seconds() * 1000 * 0.92 for c in cells]
        fig.add_trace(go.Bar(x=x, y=[c['value'] for c in cells], width=wd, name='прогноз Kp NOAA (выпуск до отсечки)',
                             marker_color=[RED if c['value'] >= 7 else '#d9a441' for c in cells], opacity=0.85,
                             hovertemplate='прогноз Kp %{y:.2f}<extra></extra>'), row=2, col=1)
    if kp is not None and kp.value is not None and not kp_obs:
        fig.add_trace(go.Scatter(x=[kp.t_utc], y=[kp.value], mode='markers+text', name='Kp — последнее наблюдение',
                                 marker=dict(size=11, color=RED if kp.value >= 7 else '#1e8449', symbol='diamond'),
                                 text=['Kp %.1f' % kp.value], textposition='top center', hoverinfo='skip'), row=2, col=1)
    if goes is not None and goes.value is not None and not goes_obs:
        fig.add_annotation(x=goes.t_utc, y=8.6, xref='x', yref='y2', text='GOES %.2g pfu' % goes.value, showarrow=False,
                           font=dict(size=10, color='#1e8449'), bgcolor='#eafaf1')
    fig.add_hline(y=7, line_dash='dot', line_color=GREY, line_width=1, row=2, col=1,
                  annotation_text='Kp 7 (G3)', annotation_position='top left', annotation_font_size=10)
    sym = {'SEP': ('triangle-up', RED, 'протонное событие'), 'GST': ('diamond', '#b9770e', 'буря'),
           'CME_ARRIVAL': ('star', '#8e44ad', 'прогноз прихода выброса'), 'FLR': ('circle', GREY, 'вспышка'), 'CME': ('circle-open', GREY, 'выброс')}
    shown = {}
    for e in events or []:
        a0 = e.valid_from_utc or e.start_utc
        if not a0 or not (t0 - timedelta(hours=6) <= a0 <= end_h):
            continue
        k = e.kind_of_event
        s, c, nm = sym.get(k, ('x', GREY, k))
        shown.setdefault(k, []).append((a0, e))
    levels = {'SEP': 6.2, 'GST': 5.2, 'CME_ARRIVAL': 4.2, 'FLR': 3.2, 'CME': 2.2}
    for k, lst in shown.items():
        s, c, nm = sym[k] if k in sym else ('x', GREY, k)
        fig.add_trace(go.Scatter(x=[a for a, _ in lst], y=[levels.get(k, 1.5)] * len(lst), mode='markers', name=nm + (' (сцен.)' if any(e.is_simulated for _, e in lst) else ''),
                                 marker=dict(size=10, color=c, symbol=s, line=dict(width=1, color='white')),
                                 text=[(e.note or e.event_id)[:110] for _, e in lst], hovertemplate='%{text}<extra>' + nm + '</extra>'), row=2, col=1)
    fig.update_yaxes(title_text='нТл', row=1, col=1)
    fig.update_yaxes(title_text='Kp', range=[0, 9.5], row=2, col=1, secondary_y=False)
    fig.update_xaxes(range=[x_from, end_h + timedelta(minutes=30)], row=2, col=1)
    fig = _style(fig, 540)
    fig.update_layout(barmode='overlay')
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
    fig.update_layout(barmode='group', yaxis=dict(title='мин'),
                      yaxis2=dict(title='част./см²', overlaying='y', side='right', type='log', showgrid=False))
    fig = _style(fig, 360)
    fig.update_layout(margin=dict(l=10, r=10, t=36, b=64), hovermode='closest')
    return fig
