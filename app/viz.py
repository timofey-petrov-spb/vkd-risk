# -*- coding: utf-8 -*-
"""Визуализации: карта трассы с аномалией и лента времени. Только Plotly.

Цвет означает происхождение величины, как и на всём экране: синий — наш расчёт,
зелёный — наблюдение, янтарный — внешний прогноз, красный — условие или аномалия,
серый — вспомогательные линии. Единый стиль всех графиков — style(): шаблон
plotly_white, легенда сверху не длиннее четырёх строк, подписи осей с единицами,
заголовок говорит, ЧТО показано и ОТКУДА.

Панель инструментов Plotly скрыта (PLOTLY_CONFIG): на защите она только мешает,
а всё, что из неё нужно, есть в выгрузке.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import plotly.graph_objects as go

BLUE, RED, GREY, WIN = '#1f4e79', '#c0392b', '#7f8c8d', '#5dade2'
GREEN, AMBER = '#1e8449', '#b9770e'
# панель инструментов скрыта, график не масштабируется мышью: экран читают, а не крутят
PLOTLY_CONFIG = {'displayModeBar': False, 'scrollZoom': False, 'staticPlot': False, 'displaylogo': False}
EVENT_SYM = {'SEP': ('triangle-up', RED, 'протонное событие'), 'GST': ('diamond', RED, 'геомагнитная буря'),
             'CME_ARRIVAL': ('star', AMBER, 'прогноз прихода выброса'), 'FLR': ('circle', GREY, 'вспышка'),
             'CME': ('circle-open', GREY, 'выброс')}
EVENT_ORDER = ['SEP', 'GST', 'CME_ARRIVAL', 'FLR', 'CME']
# правая ось GOES — логарифмическая: метки задаём сами, по-русски, без «192,3432» (U4)
GOES_TICKVALS = [0.1, 1, 10, 100, 1000]
GOES_TICKTEXT = ['0,1', '1', '10', '100', '1000']
# Разделители чисел Plotly: запятая в дробной части и узкий неразрывный пробел в разрядах тысяч.
# Своими подписями закрыты только логарифмические оси; всё остальное — деления осей и всплывающие
# подписи (`hovertemplate`) — рисует Plotly, и по умолчанию это «7.67» и «24000» рядом с «7,67»
# и «24 000» в тексте того же экрана (бриф §9.8: дроби с запятой). Первый знак — десятичный,
# второй — разряды тысяч.
SEPARATORS = ', '
# заголовок ленты говорит, ЧТО показано и ОТКУДА — по режиму, одной строкой
TIMELINE_TITLE = {
    'live': 'Поле на трассе МКС по IGRF — наш расчёт; Kp и поток GOES ≥10 МэВ — наблюдения NOAA SWPC и GFZ',
    'history_review': 'Поле на трассе МКС по IGRF — наш расчёт; Kp — наблюдения окончательного ряда GFZ (разбор после факта)',
    'history_forecast': 'Поле на трассе МКС по IGRF — наш расчёт; Kp — внешний прогноз NOAA из выпуска до отсечки',
}


def style(fig: go.Figure, height: int, legend_top: bool = True, title: str | None = None) -> go.Figure:
    """Единый стиль: plotly_white, заголовок «что и откуда», легенда сверху, сетка светлая, шрифт экрана."""
    fig.update_layout(template='plotly_white', height=height, margin=dict(l=10, r=10, t=36, b=10),
                      font=dict(family='Segoe UI, Inter, Roboto, Arial, sans-serif', size=12, color='#1a1f2b'),
                      hovermode='x unified', paper_bgcolor='white', plot_bgcolor='white',
                      separators=SEPARATORS)
    if title:
        fig.update_layout(title=dict(text=title, x=0, xanchor='left', font=dict(size=13, color='#1a1f2b')),
                          margin=dict(l=10, r=10, t=76, b=10))
    if legend_top:
        fig.update_layout(legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='left', x=0, font=dict(size=11)))
    fig.update_xaxes(showgrid=True, gridcolor='#eef0f3', zeroline=False, showline=True, linecolor='#d7dbdf')
    fig.update_yaxes(showgrid=True, gridcolor='#eef0f3', zeroline=False, showline=True, linecolor='#d7dbdf')
    return fig


_style = style     # прежнее имя


def _nbsp_int(v) -> str:
    """Целое с узким неразрывным пробелом в разрядах: 24000 → «24 000» (подписи графиков)."""
    s = '%.0f' % float(v)
    sign, s = ('-', s[1:]) if s.startswith('-') else ('', s)
    groups = []
    while len(s) > 3:
        groups.insert(0, s[-3:]); s = s[:-3]
    groups.insert(0, s)
    return sign + ' '.join(groups)


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


def _split_dateline(lon, lat):
    """Разрыв линии на переходе через 180° долготы."""
    lon = np.asarray(lon, dtype=float); lat = np.asarray(lat, dtype=float)
    jump = np.where(np.abs(np.diff(lon)) > 180)[0]
    lon_l, lat_l = lon.tolist(), lat.tolist()
    for j in reversed(jump):
        lon_l.insert(j + 1, None); lat_l.insert(j + 1, None)
    return lon_l, lat_l


def ground_track(traj, windows, thr_nT: float, when: datetime) -> go.Figure:
    """Карта: область аномалии (наш расчёт), трасса за горизонт, окна ярко, начала окон подписаны."""
    fig = go.Figure()
    glo, gla, gb = saa_grid(float(np.mean([p.alt_km for p in traj])), thr_nT, when)
    if len(glo):
        fig.add_trace(go.Scattergeo(lon=glo, lat=gla, mode='markers',
                                    name='аномалия: |B| ниже порога %s нТл' % _nbsp_int(thr_nT),
                                    marker=dict(size=9, color=RED, opacity=0.18, symbol='square'), hoverinfo='skip'))
    lon_l, lat_l = _split_dateline([p.lon_deg for p in traj], [p.lat_deg for p in traj])
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
        sl, sa = _split_dateline([p.lon_deg for p in seg], [p.lat_deg for p in seg])
        fig.add_trace(go.Scattergeo(lon=sl, lat=sa, mode='lines', name='окно %d: %s' % (i + 1, w.start_utc.strftime('%d.%m %H:%M')),
                                    line=dict(color=WIN if i else BLUE, width=3),
                                    hovertemplate='окно %d<br>%%{lat:.1f}°, %%{lon:.1f}°<extra></extra>' % (i + 1)))
        fig.add_trace(go.Scattergeo(lon=[seg[0].lon_deg], lat=[seg[0].lat_deg], mode='markers+text', text=['старт %d' % (i + 1)],
                                    textposition='top center', marker=dict(size=9, color=WIN if i else BLUE, symbol='circle'),
                                    showlegend=False, hoverinfo='skip'))
    fig.update_geos(projection_type='equirectangular', showcountries=False, showcoastlines=True, coastlinecolor='#bbb',
                    showland=True, landcolor='#f7f7f7', showocean=True, oceancolor='#ffffff', lataxis_range=[-75, 75])
    fig.update_layout(template='plotly_white', height=440, margin=dict(l=0, r=0, t=76, b=0),
                      title=dict(text='Область аномалии и трасса МКС — наш расчёт |B| по IGRF на средней высоте трассы; '
                                      'времена UTC',
                                 x=0, xanchor='left', font=dict(size=13, color='#1a1f2b')),
                      font=dict(family='Segoe UI, Inter, Roboto, Arial, sans-serif', size=12, color='#1a1f2b'),
                      separators=SEPARATORS,
                      legend=dict(orientation='h', yanchor='bottom', y=1.0, xanchor='left', x=0, font=dict(size=11)))
    return fig


def timeline(traj, windows, thr_nT: float, t0: datetime, horizon_min: int, goes, kp, events, forecasts: list,
             mode: str, kp_obs=(), goes_obs=(), past_h: float = 12.0, search_min: int | None = None) -> go.Figure:
    """Одна лента: слева прошедшие наблюдения (past_h часов), справа горизонт окон.
    Ряд 1 — |B| по трассе (наш расчёт), пролёты аномалии красным, окна-кандидаты синим.
    Ряд 2 — Kp: наблюдения столбцами (kp_obs: (от, до, значение)), в строгой истории —
    прогноз NOAA из выпуска до отсечки; GOES ≥10 МэВ — линия по правой оси (goes_obs: (t, pfu)).
    Ряд 3 — события и прогнозы по типам (категориальная ось: положение по вертикали — тип, не значение).
    search_min — период поиска начала: его конец рисуется штриховой линией (O5-4)."""
    from plotly.subplots import make_subplots
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.5, 0.32, 0.18],
                        specs=[[{}], [{'secondary_y': True}], [{}]])
    ms = lambda t: int(t.timestamp() * 1000)
    end_h = t0 + timedelta(minutes=horizon_min)
    x_from = t0 - timedelta(hours=past_h if (kp_obs or goes_obs) else 1)
    fig.add_vline(x=ms(t0), line_color='#1a1f2b', line_width=1.2,
                  annotation_text='сейчас' if mode == 'live' else ('отсечка' if mode == 'history_forecast' else 'начало периода'),
                  annotation_position='top right', annotation_font_size=10)
    if kp_obs:
        xs = [a + (b - a) / 2 for a, b, _ in kp_obs]
        wd = [(b - a).total_seconds() * 1000 * 0.92 for a, b, _ in kp_obs]
        fig.add_trace(go.Bar(x=xs, y=[v for _, _, v in kp_obs], width=wd, name='Kp — наблюдение',
                             marker_color=[RED if v >= 7 else GREEN for _, _, v in kp_obs], opacity=0.9,
                             hovertemplate='Kp %{y:.2f}<extra></extra>'), row=2, col=1, secondary_y=False)
    if goes_obs:
        fig.add_trace(go.Scatter(x=[t for t, _ in goes_obs], y=[v for _, v in goes_obs], name='поток GOES ≥10 МэВ — наблюдение',
                                 line=dict(width=1.2, color=GREEN), hovertemplate='%{y:.3g} pfu<extra></extra>'),
                      row=2, col=1, secondary_y=True)
        # метки правой оси задаём сами: иначе на логарифмической оси печатаются числа вида 192,3432 (U4)
        fig.update_yaxes(type='log', title_text='поток ≥10 МэВ, pfu', range=[-1, 4.2], showgrid=False, row=2, col=1,
                         secondary_y=True, tickmode='array', tickvals=GOES_TICKVALS, ticktext=GOES_TICKTEXT)
    if traj:
        ts = [p.t_utc for p in traj]
        fig.add_trace(go.Scatter(x=ts, y=[p.B_nT for p in traj], name='|B| на трассе — наш расчёт по IGRF',
                                 line=dict(width=1.4, color=BLUE), hovertemplate='%{y:.0f} нТл<extra></extra>'), row=1, col=1)
        fig.add_hline(y=thr_nT, line_dash='dot', line_color=RED, line_width=1, row=1, col=1,
                      annotation_text='порог аномалии %s нТл' % _nbsp_int(thr_nT), annotation_position='top left',
                      annotation_font_size=10)
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
        for r in (1, 2, 3):
            fig.add_vrect(x0=w.start_utc, x1=x1, fillcolor=WIN if i else BLUE, opacity=0.10, line_width=0, row=r, col=1)
        fig.add_annotation(x=w.start_utc, y=1.0, xref='x', yref='paper', text='окно %d' % (i + 1), showarrow=False,
                           xanchor='left', yanchor='bottom', font=dict(size=11, color=BLUE))
    if search_min:
        fig.add_vline(x=ms(t0 + timedelta(minutes=search_min)), line_dash='dash', line_color=GREY, line_width=1,
                      annotation_text='конец периода поиска начала (%s ч)' % ('%g' % (search_min / 60.0)).replace('.', ','),
                      annotation_position='bottom right', annotation_font_size=10)
    # --- ряд 2: Kp
    fc = {l['channel']: l for l in (forecasts or [])}
    cells = fc.get('kp_forecast', {}).get('cells', [])
    if cells:
        x = [datetime.fromisoformat(c['from']) + (datetime.fromisoformat(c['to']) - datetime.fromisoformat(c['from'])) / 2 for c in cells]
        wd = [(datetime.fromisoformat(c['to']) - datetime.fromisoformat(c['from'])).total_seconds() * 1000 * 0.92 for c in cells]
        fig.add_trace(go.Bar(x=x, y=[c['value'] for c in cells], width=wd, name='прогноз Kp NOAA — внешний прогноз',
                             marker_color=[RED if c['value'] >= 7 else AMBER for c in cells], opacity=0.85,
                             hovertemplate='прогноз Kp %{y:.2f}<extra></extra>'), row=2, col=1)
    if kp is not None and kp.value is not None and not kp_obs:
        fig.add_trace(go.Scatter(x=[kp.t_utc], y=[kp.value], mode='markers+text', name='Kp — последнее наблюдение',
                                 marker=dict(size=11, color=RED if kp.value >= 7 else GREEN, symbol='diamond'),
                                 text=['Kp %s' % ('%.1f' % kp.value).replace('.', ',')], textposition='top center',
                                 hoverinfo='skip'), row=2, col=1)
    if goes is not None and goes.value is not None and not goes_obs:
        fig.add_annotation(x=goes.t_utc, y=8.6, xref='x', yref='y2',
                           text='GOES %s pfu' % ('%.2g' % goes.value).replace('.', ','), showarrow=False,
                           font=dict(size=10, color=GREEN), bgcolor='#eafaf1')
    fig.add_hline(y=7, line_dash='dot', line_color=GREY, line_width=1, row=2, col=1,
                  annotation_text='Kp 7 (G3)', annotation_position='top left', annotation_font_size=10)
    # --- ряд 3: события и прогнозы, по типам (категориальная ось)
    shown = {}
    for e in events or []:
        a0 = e.valid_from_utc or e.start_utc
        if not a0 or not (t0 - timedelta(hours=6) <= a0 <= end_h):
            continue
        shown.setdefault(e.kind_of_event, []).append((a0, e))
    kinds = [k for k in EVENT_ORDER if k in shown] + [k for k in shown if k not in EVENT_ORDER]
    for k in kinds:
        lst = shown[k]
        s, c, nm = EVENT_SYM.get(k, ('x', GREY, k))
        # тип события подписан на оси ряда 3 — в легенду он не идёт: легенда не длиннее четырёх строк
        fig.add_trace(go.Scatter(x=[a for a, _ in lst], y=[nm] * len(lst), mode='markers', showlegend=False,
                                 name=nm + (' (сценарий)' if any(e.is_simulated for _, e in lst) else ''),
                                 marker=dict(size=10, color=c, symbol=s, line=dict(width=1, color='white')),
                                 text=[(e.note or e.event_id)[:110] for _, e in lst], hovertemplate='%{text}<extra>' + nm + '</extra>'),
                      row=3, col=1)
    if not kinds:
        fig.add_annotation(x=0.01, y=0.5, xref='paper', yref='y3 domain', text='событий и прогнозов на горизонте нет',
                           showarrow=False, font=dict(size=10, color=GREY), xanchor='left')
    fig.update_yaxes(title_text='|B|, нТл', row=1, col=1)
    fig.update_yaxes(title_text='Kp (безразмерный)', range=[0, 9.5], row=2, col=1, secondary_y=False)
    fig.update_yaxes(title_text='событие, тип', type='category', categoryorder='array',
                     categoryarray=[EVENT_SYM.get(k, ('', '', k))[2] for k in reversed(kinds)] if kinds else ['—'],
                     showgrid=True, row=3, col=1)
    fig.update_xaxes(range=[x_from, end_h + timedelta(minutes=30)], row=3, col=1)
    fig.update_xaxes(title_text='время, UTC', row=3, col=1)
    fig = style(fig, 600, title=TIMELINE_TITLE.get(mode, TIMELINE_TITLE['live']))
    fig.update_layout(barmode='overlay')
    return fig
