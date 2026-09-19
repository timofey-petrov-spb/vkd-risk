# -*- coding: utf-8 -*-
"""Панель наблюдений: ряды GOES и Kp за последние дни с порогами шкал NOAA.

Это то, что видит аналитик как «текущую обстановку» до всякого расчёта:
наблюдения (зелёный), внешний прогноз (янтарный), пороги шкал (серые линии), момент запроса.
Цвет означает происхождение величины, как и на всём экране, а не «хорошо/плохо».
Ряды читаются из кеша слоя источников.
"""
from __future__ import annotations

import io
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from app.viz import style

GREEN, GREY, RED, AMBER, BLUE = '#1e8449', '#7f8c8d', '#c0392b', '#b9770e', '#1f4e79'
S_LEVELS = [(10.0, 'S1'), (100.0, 'S2'), (1000.0, 'S3')]
G_LEVELS = [(5, 'G1'), (7, 'G3'), (9, 'G5')]
PFU_TICKVALS = [0.01, 0.1, 1, 10, 100, 1000, 10000]
PFU_TICKTEXT = ['0,01', '0,1', '1', '10', '100', '1000', '10 000']


def _load(path: Optional[str]):
    if not path:
        return None
    try:
        return json.load(io.open(path, encoding='utf-8'))
    except Exception:            # noqa: BLE001 — нечитаемый кеш = нет ряда
        return None


def goes_series(path: Optional[str], channel: str = '>=10 MeV'):
    d = _load(path)
    if not isinstance(d, list):
        return [], []
    pts = sorted((x for x in d if x.get('energy') == channel and x.get('flux') is not None), key=lambda x: x['time_tag'])
    return [datetime.fromisoformat(x['time_tag'].replace('Z', '+00:00')) for x in pts], [float(x['flux']) for x in pts]


def kp_series(path: Optional[str]):
    d = _load(path)
    if not isinstance(d, dict) or not d.get('datetime'):
        return [], []
    return [datetime.fromisoformat(t.replace('Z', '+00:00')) for t in d['datetime']], [float(v) for v in d['Kp']]


def observations_panel(goes_path: Optional[str], kp_path: Optional[str], t0: datetime) -> Optional[go.Figure]:
    tg, vg = goes_series(goes_path)
    tk, vk = kp_series(kp_path)
    if not tg and not tk:
        return None
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1,
                        subplot_titles=('GOES, протоны ≥10 МэВ, pfu — наблюдение NOAA SWPC', 'Kp — наблюдение GFZ'))
    if tg:
        fig.add_trace(go.Scatter(x=tg, y=vg, name='GOES ≥10 МэВ, pfu', line=dict(color=GREEN, width=1.5),
                                 hovertemplate='%{y:.3g} pfu<extra></extra>'), row=1, col=1)
        for thr, name in S_LEVELS:
            # на логарифмической оси координата линии — log10 значения, иначе 1000 читается как 10^1000
            fig.add_hline(y=math.log10(thr), line_dash='dot', line_color=GREY, annotation_text=name, row=1, col=1)
    if tk:
        fig.add_trace(go.Bar(x=tk, y=vk, name='Kp', marker_color=[RED if v >= 7 else GREEN for v in vk],
                             hovertemplate='Kp %{y:.2f}<extra></extra>'), row=2, col=1)
        for thr, name in G_LEVELS:
            fig.add_hline(y=thr, line_dash='dot', line_color=GREY, annotation_text=name, row=2, col=1)
    fig.add_vline(x=int(t0.timestamp() * 1000), line_dash='dash', line_color='#1f4e79')
    fig.add_annotation(x=t0, y=1.0, xref='x', yref='paper', text='запрос', showarrow=False, xanchor='left', yanchor='bottom',
                       font=dict(size=10, color='#1f4e79'))
    lo = min([v for v in vg if v > 0] or [0.1])
    # метки логарифмической оси задаём сами: иначе печатаются числа вида 192,3432 (U4)
    fig.update_yaxes(type='log', title_text='поток, pfu', range=[math.log10(lo) - 0.5, 4.2], row=1, col=1,
                     tickmode='array', tickvals=PFU_TICKVALS, ticktext=PFU_TICKTEXT)
    fig.update_yaxes(range=[0, 9], title_text='Kp', row=2, col=1)
    fig.update_xaxes(title_text='время, UTC', row=2, col=1)
    fig = style(fig, 440)
    fig.update_layout(margin=dict(l=10, r=10, t=48, b=10), showlegend=False)
    return fig


def forecast_panel(lines: list, t0: datetime, horizon_min: int) -> Optional[go.Figure]:
    """Прогнозы NOAA из выпусков до отсечки: Kp по 3-часовым интервалам (столбцы), суточные
    вероятности S1+ и протонного события (ступени). Исходное разрешение сохраняется."""
    by = {l['channel']: l for l in lines}
    kp = by.get('kp_forecast', {}).get('cells', [])
    probs = [(by.get(c, {}), name) for c, name in (('s1_prob_daily', 'S1+ за сутки, %'), ('proton_prob_daily', 'протонное событие за сутки, %'))]
    if not kp and not any(p[0].get('cells') for p in probs):
        return None
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1,
                        subplot_titles=('Прогноз Kp NOAA по 3-часовым интервалам (выпуск до отсечки)',
                                        'Суточные вероятности NOAA, %'))
    if kp:
        x = [datetime.fromisoformat(c['from']) + (datetime.fromisoformat(c['to']) - datetime.fromisoformat(c['from'])) / 2 for c in kp]
        w = [(datetime.fromisoformat(c['to']) - datetime.fromisoformat(c['from'])).total_seconds() * 1000 * 0.9 for c in kp]
        fig.add_trace(go.Bar(x=x, y=[c['value'] for c in kp], width=w, name='прогноз Kp, 3-часовые интервалы',
                             marker_color=[RED if c['value'] >= 7 else AMBER for c in kp],
                             hovertemplate='прогноз Kp %{y:.2f}<extra></extra>'), row=1, col=1)
        for thr, name in G_LEVELS:
            fig.add_hline(y=thr, line_dash='dot', line_color=GREY, annotation_text=name, row=1, col=1)
    for line, name in probs:
        cells = line.get('cells', [])
        if cells:
            xs, ys = [], []
            for c in cells:
                xs += [datetime.fromisoformat(c['from']), datetime.fromisoformat(c['to'])]
                ys += [c['value'], c['value']]
            fig.add_trace(go.Scatter(x=xs, y=ys, mode='lines', name=name,
                                     line=dict(width=2, color=AMBER, dash=None if 'S1' in name else 'dot')), row=2, col=1)
    fig.add_vline(x=int(t0.timestamp() * 1000), line_dash='dash', line_color='#1f4e79')
    fig.add_annotation(x=t0, y=1.0, xref='x', yref='paper', text='отсечка', showarrow=False, xanchor='right', yanchor='bottom',
                       font=dict(size=10, color='#1f4e79'))
    fig.add_vrect(x0=t0, x1=t0 + timedelta(minutes=horizon_min), fillcolor='steelblue', opacity=0.06, line_width=0)
    fig.add_annotation(x=t0 + timedelta(minutes=horizon_min / 2), y=1.0, xref='x', yref='paper', text='горизонт окон', showarrow=False,
                       xanchor='center', yanchor='bottom', font=dict(size=10, color='#7f8c8d'))
    fig.update_yaxes(range=[0, 9], title_text='Kp', row=1, col=1)
    fig.update_yaxes(range=[0, 100], title_text='вероятность, %', row=2, col=1)
    fig.update_xaxes(title_text='время, UTC', row=2, col=1)
    fig = style(fig, 420)
    fig.update_layout(margin=dict(l=10, r=10, t=56, b=10))
    return fig
