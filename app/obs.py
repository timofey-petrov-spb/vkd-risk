# -*- coding: utf-8 -*-
"""Панель наблюдений: ряды GOES и Kp за последние дни с порогами шкал NOAA.

Это то, что видит аналитик как «текущую обстановку» до всякого расчёта:
наблюдения (зелёный), пороги шкал (серые линии), момент запроса.
Ряды читаются из кеша слоя источников (experiments/stub_sources или vkd.sources).
"""
from __future__ import annotations

import io
import json
import math
from datetime import datetime, timezone
from typing import Optional

import plotly.graph_objects as go
from plotly.subplots import make_subplots

GREEN, GREY, RED = '#1e8449', '#7f8c8d', '#c0392b'
S_LEVELS = [(10.0, 'S1'), (100.0, 'S2'), (1000.0, 'S3')]
G_LEVELS = [(5, 'G1'), (7, 'G3'), (9, 'G5')]


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
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
                        subplot_titles=('GOES, протоны ≥10 МэВ, pfu — наблюдение NOAA SWPC', 'Kp — наблюдение GFZ'))
    if tg:
        fig.add_trace(go.Scatter(x=tg, y=vg, name='GOES ≥10 МэВ', line=dict(color=GREEN, width=1.5)), row=1, col=1)
        for thr, name in S_LEVELS:
            # на логарифмической оси координата линии — log10 значения, иначе 1000 читается как 10^1000
            fig.add_hline(y=math.log10(thr), line_dash='dot', line_color=GREY, annotation_text=name, row=1, col=1)
    if tk:
        fig.add_trace(go.Bar(x=tk, y=vk, name='Kp', marker_color=[RED if v >= 7 else GREEN for v in vk]), row=2, col=1)
        for thr, name in G_LEVELS:
            fig.add_hline(y=thr, line_dash='dot', line_color=GREY, annotation_text=name, row=2, col=1)
    fig.add_vline(x=int(t0.timestamp() * 1000), line_dash='dash', line_color='#1f4e79', annotation_text='запрос')
    lo = min([v for v in vg if v > 0] or [0.1])
    fig.update_yaxes(type='log', title_text='pfu', range=[math.log10(lo) - 0.5, 4.2], row=1, col=1)
    fig.update_yaxes(range=[0, 9], title_text='Kp', row=2, col=1)
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=40, b=10), showlegend=False)
    return fig
