# -*- coding: utf-8 -*-
"""Визуализации: карта трассы с аномалией и лента времени. Только Plotly.

Цвет означает происхождение величины, как и на всём экране: синий — наш расчёт,
зелёный — наблюдение, янтарный — внешний прогноз, красный — условие или аномалия,
серый — вспомогательные линии. Единый стиль всех графиков — style(): тёмная подложка
в тон странице, легенда сверху не длиннее четырёх строк, подписи осей с единицами,
заголовок говорит, ЧТО показано и ОТКУДА.

Тёмная подложка и цвета величин заданы в разных местах, и это не небрежность.
Оттенки самих величин (BLUE, RED, GREEN, AMBER, GREY) остаются светлотемными: их
пересчитывает в тёмные пары `app.ui.dark_figure` по точной таблице соответствий, и
перекрасить их здесь значит разойтись с той таблицей. Здесь задаётся только то, до чего
dark_figure не достаёт и что иначе осталось бы белым прямоугольником на тёмной странице:
подложка поля, сетка, шрифт, заливки фигур разметки и картографическая подложка карты.

Лента отвечает на вопрос аналитика «почему это окно лучше», а не показывает сырое поле:
верхний ряд — накопленные минуты в аномалии по каждому окну (ступенями, от начала окна),
нижний — внешняя обстановка (Kp и поток GOES), ряд событий появляется только тогда,
когда события на горизонте есть. Сырое |B| осталось на ленте одной свёрнутой линией
легенды: кому надо — включит, на первый взгляд оно не мешает.

Панель инструментов Plotly скрыта (PLOTLY_CONFIG): на защите она только мешает,
а всё, что из неё нужно, есть в выгрузке.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache

import numpy as np
import plotly.graph_objects as go

from app.ui import event_kind_ru      # имена типов событий по-русски; app/ui.py не знает о Plotly

BLUE, RED, GREY, WIN = '#1f4e79', '#c0392b', '#7f8c8d', '#5dade2'
GREEN, AMBER = '#1e8449', '#b9770e'
# Тёмная подложка экрана: ровно те же значения, что в теме Streamlit (.streamlit/config.toml)
# и в :root app/ui.py. Это НЕ цвета величин — это бумага, сетка и шрифт, на которых величины
# нарисованы. Тёмные пары самих величин берёт app/ui.dark_figure.
BG, PANEL, RULE = '#0e1117', '#161b26', '#242b38'
INK, MUTED = '#e8ebf2', '#a7b0c0'
# Одна гарнитура на весь экран: та же, что у страницы и у подписей внутри глобуса.
# Public Sans — шрифт государственного стандарта США, которым набран сайт NASA; он свободный,
# но адресом сюда не подключается: посторонних адресов на площадке не заводим. Нет его в системе —
# встанет следующий без засечек, а размеры и начертания заданы числами и не поплывут.
FONT = 'Public Sans, Inter, Segoe UI, Roboto, Arial, sans-serif'
# Тёмные пары для заливок: dark_figure подставляет тёмный оттенок только по точному совпадению
# строки цвета, а заливка задаётся с прозрачностью (rgba) и мимо той таблицы проходит.
RED_FILL = 'rgba(245,139,127,0.16)'          # аномалия: красный тёмной темы (#f58b7f) вполсилы
PAST_FILL = '#161b26'                        # прошлое на ленте: та же подложка, только плотнее
# Тёмные пары цветов величин. Нужны там, где цвет попадает в ЗАЛИВКУ фигуры разметки (vrect):
# app/ui.dark_figure правит цвет линии фигуры, а до заливки не достаёт, и тёмно-синяя полоса окна
# на тёмном фоне пропадала совсем. Таблица — та же, что DARK_TRACE_COLORS в app/ui.py; совпадение
# проверяется тестом (tests/test_viz_visual.py), чтобы две таблицы не разошлись молча.
DARK_PAIR = {'#1f4e79': '#7ab8f5', '#5dade2': '#4f8fc0', '#85c1e9': '#3f6f97',
             '#1e8449': '#5ed39a', '#b9770e': '#e7b45c', '#c0392b': '#f58b7f',
             '#7f8c8d': '#9aa5b5', '#9aa0a6': '#9aa5b5'}

# цвета окон — оттенки синего (окно, как и трасса, наш расчёт); первое окно самое тёмное
WIN_COLORS = (BLUE, WIN, '#85c1e9')
# панель инструментов скрыта, график не масштабируется мышью: экран читают, а не крутят
PLOTLY_CONFIG = {'displayModeBar': False, 'scrollZoom': False, 'staticPlot': False, 'displaylogo': False}
EVENT_SYM = {'SEP': ('triangle-up', RED, 'протонное событие'), 'GST': ('diamond', RED, 'геомагнитная буря'),
             'CME_ARRIVAL': ('star', AMBER, 'прогноз прихода выброса'), 'FLR': ('circle', GREY, 'вспышка'),
             'CME': ('circle-open', GREY, 'выброс')}
EVENT_ORDER = ['SEP', 'GST', 'CME_ARRIVAL', 'FLR', 'CME']
# правая ось GOES — логарифмическая: метки задаём сами, по-русски, без «192,3432» (U4)
GOES_TICKVALS = [0.1, 1, 10, 100, 1000]
GOES_TICKTEXT = ['0,1', '1', '10', '100', '1000']
# деления, совпадающие с порогами шкалы NOAA S, подписаны порогом: «10 (S1)», а не просто «10»
GOES_S_TICKTEXT = {10: '10 (S1)', 100: '100 (S2)', 1000: '1000 (S3)'}
# первый порог шкалы NOAA S (S1) по потоку протонов ≥10 МэВ, pfu: ниже него ось не рисуем,
# потому что вся линия ложится на дно, и вместо оси печатаем одну строку с числом
S1_PFU = 10.0
KP_G3 = 7.0
# Разделители чисел Plotly: запятая в дробной части и узкий неразрывный пробел в разрядах тысяч.
# Своими подписями закрыты только логарифмические оси; всё остальное — деления осей и всплывающие
# подписи (`hovertemplate`) — рисует Plotly, и по умолчанию это «7.67» и «24000» рядом с «7,67»
# и «24 000» в тексте того же экрана (бриф §9.8: дроби с запятой). Первый знак — десятичный,
# второй — разряды тысяч.
SEPARATORS = ', '
# Заголовок ленты говорит, ЧТО показано и ОТКУДА — по режиму, одной короткой строкой.
# Подробности (какой именно ряд GFZ, какой выпуск прогноза) стоят в подписи под рисунком:
# заголовок в две строки читается дольше, чем сам рисунок.
TIMELINE_TITLE = {
    'live': 'Экспозиция окон — наш расчёт по IGRF; Kp и поток GOES — наблюдения GFZ и NOAA',
    'history_review': 'Экспозиция окон — наш расчёт по IGRF; Kp — наблюдения GFZ (разбор после факта)',
    'history_forecast': 'Экспозиция окон — наш расчёт по IGRF; Kp — внешний прогноз NOAA до отсечки',
}


def win_color(i: int) -> str:
    """Цвет окна — один и тот же на карте, на ленте и в подписях."""
    return WIN_COLORS[i % len(WIN_COLORS)]


def dark_pair(color: str) -> str:
    """Тёмная пара цвета величины; незнакомый цвет остаётся собой — портить нечего."""
    return DARK_PAIR.get(str(color).lower(), color)


def style(fig: go.Figure, height: int, legend_top: bool = True, title: str | None = None) -> go.Figure:
    """Единый стиль: тёмная подложка в тон странице, заголовок «что и откуда», легенда сверху.

    Что убрано намеренно. Рамка поля и вертикальная сетка времени ничего не отделяют и ничего
    не помогают прочесть: значение читают по горизонтали, а моменты времени на ленте отмечены
    своими линиями. Осталась одна горизонтальная сетка цветом разделителя — она тише подписей
    и не спорит с линиями данных. Подписи осей крупнее делений: ось без единицы нечитаема, и
    единицу нужно видеть, не приглядываясь.
    """
    fig.update_layout(template='plotly_dark', height=height, margin=dict(l=10, r=10, t=36, b=10),
                      font=dict(family=FONT, size=13, color=INK),
                      hovermode='x unified', paper_bgcolor=BG, plot_bgcolor=BG,
                      separators=SEPARATORS,
                      hoverlabel=dict(bgcolor=PANEL, bordercolor=RULE, font=dict(family=FONT, size=12, color=INK)))
    if title:
        # Заголовок внутри рисунка набран как ПОДПИСЬ, а не как второй заголовок: имя блока уже
        # стоит на экране над рисунком, и повторять его крупным кеглем — удвоение. Строка остаётся
        # потому, что называет ОТКУДА взяты величины, а имя блока этого не говорит.
        fig.update_layout(title=dict(text=title, x=0, xanchor='left',
                                     font=dict(family=FONT, size=12, weight=400, color=MUTED)),
                          margin=dict(l=10, r=10, t=70, b=10))
    if legend_top:
        fig.update_layout(legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='left', x=0,
                                      font=dict(family=FONT, size=12, weight=500, color=MUTED),
                                      bgcolor='rgba(0,0,0,0)'))
    # Размеры сняты с сайта NASA и приведены к экрану: подпись оси 13 px полужирная, деления
    # 12 px обычные приглушённым тоном. Деления тише подписи — иначе сетка чисел спорит с именем
    # величины, а читают сначала имя.
    fig.update_xaxes(showgrid=False, zeroline=False, showline=False, ticks='outside', ticklen=4, tickcolor=RULE,
                     tickfont=dict(family=FONT, size=12, weight=400, color=MUTED),
                     title_font=dict(family=FONT, size=13, weight=600, color=MUTED))
    fig.update_yaxes(showgrid=True, gridcolor=RULE, gridwidth=1, zeroline=False, showline=False,
                     tickfont=dict(family=FONT, size=12, weight=400, color=MUTED),
                     title_font=dict(family=FONT, size=13, weight=600, color=MUTED))
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


def _num_ru(v, digits: int = 3) -> str:
    """Число для подписи: дробь с запятой, без хвостовых нулей («0,26», «1,4·10³» не нужно — тут pfu)."""
    return ('%.*g' % (digits, float(v))).replace('.', ',')


def _field_nT(lons, lats, alt_km: float, when: datetime) -> np.ndarray:
    """|B| по IGRF в узлах сетки, нТл. Наш расчёт; та же модель, что и в ядре."""
    import ppigrf
    LO, LA = np.meshgrid(lons, lats)
    Be, Bn, Bu = ppigrf.igrf(LO.ravel(), LA.ravel(), np.full(LO.size, alt_km), when.replace(tzinfo=None))
    return np.sqrt(Be ** 2 + Bn ** 2 + Bu ** 2).ravel().reshape(LA.shape)


def saa_grid(alt_km: float, thr_nT: float, when: datetime, step_deg: float = 4.0):
    """Сетка точек, где |B| по IGRF ниже порога на высоте alt_km — область аномалии.
    Наш расчёт; сетка грубая (шаг step_deg) — для показа, не для расчёта.
    Остаётся для тех, кому нужны именно точки (например, глобус)."""
    lats = np.arange(-70, 70.001, step_deg)
    lons = np.arange(-180, 180.001, step_deg)
    LO, LA = np.meshgrid(lons, lats)
    B = _field_nT(lons, lats, alt_km, when).ravel()
    m = B < thr_nT
    return LO.ravel()[m], LA.ravel()[m], B[m]


def saa_contour(alt_km: float, thr_nT: float, when: datetime, step_lon: float = 2.0, step_lat: float = 1.0):
    """Замкнутый контур области |B| < thr_nT на высоте alt_km: список (долготы, широты) полигонов.

    Квадраты сетки 4° читались как артефакт, поэтому граница берётся по мелкой сетке
    (2° по долготе, 1° по широте) и рисуется одной сглаженной линией: для каждой долготы
    внутри области берутся крайняя южная и крайняя северная широта, контур обходит область
    сверху и возвращается снизу. Область аномалии на одной высоте односвязна по долготе,
    поэтому такой обход её и описывает; несмежные участки долгот дают отдельные полигоны.

    Мелкая сетка стоит около половины секунды, а экран перерисовывается на каждое действие,
    поэтому результат запоминается по высоте (до километра), порогу и суткам: за сутки IGRF
    не меняется настолько, чтобы это было видно на карте."""
    return _contour_cached(round(float(alt_km), 0), float(thr_nT), when.date().toordinal(),
                           float(step_lon), float(step_lat))


@lru_cache(maxsize=16)
def _contour_cached(alt_km: float, thr_nT: float, day: int, step_lon: float, step_lat: float):
    when = datetime.fromordinal(day)
    lats = np.arange(-80.0, 80.001, step_lat)
    lons = np.arange(-180.0, 180.001, step_lon)
    mask = _field_nT(lons, lats, alt_km, when) < thr_nT
    cols = np.where(mask.any(axis=0))[0]
    if not len(cols):
        return []
    polys = []
    for run in np.split(cols, np.where(np.diff(cols) != 1)[0] + 1):
        if len(run) < 2:
            continue
        lo_lat, hi_lat = [], []
        for c in run:
            idx = np.where(mask[:, c])[0]
            lo_lat.append(float(lats[idx[0]])); hi_lat.append(float(lats[idx[-1]]))
        lon_run = [float(x) for x in lons[run]]
        polys.append((lon_run + lon_run[::-1] + lon_run[:1], hi_lat + lo_lat[::-1] + hi_lat[:1]))
    return polys


def _split_dateline(lon, lat):
    """Разрыв линии на переходе через 180° долготы."""
    lon = np.asarray(lon, dtype=float); lat = np.asarray(lat, dtype=float)
    jump = np.where(np.abs(np.diff(lon)) > 180)[0]
    lon_l, lat_l = lon.tolist(), lat.tolist()
    for j in reversed(jump):
        lon_l.insert(j + 1, None); lat_l.insert(j + 1, None)
    return lon_l, lat_l


def ground_track(traj, windows, thr_nT: float, when: datetime, step_lon: float = 2.0, step_lat: float = 1.0) -> go.Figure:
    """Карта: область аномалии сглаженным контуром (наш расчёт), трасса за горизонт,
    окна ярко, начала окон подписаны. Высота, на которой построен контур, стоит
    и в подписи области, и в заголовке: |B| зависит от высоты, и число без неё ничего не значит."""
    fig = go.Figure()
    alt_km = float(np.mean([p.alt_km for p in traj])) if traj else 420.0
    alt_ru = '%s км' % _nbsp_int(alt_km)
    polys = saa_contour(alt_km, thr_nT, when, step_lon=step_lon, step_lat=step_lat)
    for k, (plon, plat) in enumerate(polys):
        fig.add_trace(go.Scattergeo(lon=plon, lat=plat, mode='lines', fill='toself',
                                    fillcolor=RED_FILL, line=dict(color=RED, width=1.4),
                                    name='аномалия: |B| ниже %s нТл, %s' % (_nbsp_int(thr_nT), alt_ru),
                                    showlegend=(k == 0), hoverinfo='skip'))
    lon_l, lat_l = _split_dateline([p.lon_deg for p in traj], [p.lat_deg for p in traj])
    fig.add_trace(go.Scattergeo(lon=lon_l, lat=lat_l, mode='lines', name='трасса, шаг 1 мин',
                                line=dict(color=GREY, width=1.2), opacity=0.55, hoverinfo='skip'))
    in_saa_pts = [p for p in traj if p.in_saa]
    if in_saa_pts:
        fig.add_trace(go.Scattergeo(lon=[p.lon_deg for p in in_saa_pts], lat=[p.lat_deg for p in in_saa_pts], mode='markers',
                                    name='трасса в аномалии', marker=dict(size=3.5, color=RED), hoverinfo='skip'))
    for i, w in enumerate(windows):
        end = w.start_utc + timedelta(minutes=w.duration_min)
        seg = [p for p in traj if w.start_utc <= p.t_utc < end]
        if not seg:
            continue
        sl, sa = _split_dateline([p.lon_deg for p in seg], [p.lat_deg for p in seg])
        fig.add_trace(go.Scattergeo(lon=sl, lat=sa, mode='lines', name='окно %d: %s' % (i + 1, w.start_utc.strftime('%d.%m %H:%M')),
                                    line=dict(color=win_color(i), width=3),
                                    hovertemplate='окно %d<br>%%{lat:.1f}°, %%{lon:.1f}°<extra></extra>' % (i + 1)))
        fig.add_trace(go.Scattergeo(lon=[seg[0].lon_deg], lat=[seg[0].lat_deg], mode='markers+text', text=['старт %d' % (i + 1)],
                                    textposition='top center', marker=dict(size=9, color=win_color(i), symbol='circle'),
                                    textfont=dict(family=FONT, size=12, color=MUTED),
                                    showlegend=False, hoverinfo='skip'))
    # Подложка карты тёмная, в тон странице: белый океан на тёмном экране читался как дыра в вёрстке.
    # Суша чуть светлее океана и без границ государств — она здесь только затем, чтобы по трассе
    # было видно, над чем идёт станция; береговая линия приглушена до цвета вспомогательных линий.
    fig.update_geos(projection_type='equirectangular', showcountries=False, showcoastlines=True, coastlinecolor=RULE,
                    coastlinewidth=1, showland=True, landcolor=PANEL, showocean=True, oceancolor=BG,
                    showframe=False, bgcolor=BG, lataxis_range=[-75, 75])
    fig.update_layout(template='plotly_dark', height=440, margin=dict(l=0, r=0, t=76, b=0),
                      title=dict(text='Трасса МКС и область аномалии — наш расчёт |B| по IGRF, '
                                      'высота %s, время UTC' % alt_ru,
                                 x=0, xanchor='left',
                                 font=dict(family=FONT, size=12, weight=400, color=MUTED)),
                      font=dict(family=FONT, size=13, color=INK),
                      paper_bgcolor=BG, plot_bgcolor=BG, separators=SEPARATORS,
                      hoverlabel=dict(bgcolor=PANEL, bordercolor=RULE, font=dict(family=FONT, size=12, color=INK)),
                      legend=dict(orientation='h', yanchor='bottom', y=1.0, xanchor='left', x=0,
                                  font=dict(family=FONT, size=12, weight=500, color=MUTED),
                                  bgcolor='rgba(0,0,0,0)'))
    return fig


def _traj_step_min(traj) -> float:
    """Шаг трассы в минутах — из самой трассы, а не из предположения «одна минута»."""
    if len(traj) < 2:
        return 1.0
    d = (traj[1].t_utc - traj[0].t_utc).total_seconds() / 60.0
    return d if d > 0 else 1.0


def window_exposure(traj, w, step_min: float | None = None):
    """Ступенчатое накопление минут в аномалии внутри окна: (времена, минуты, всего).

    Ответ на вопрос «почему это окно лучше»: видно и сколько окно набирает всего,
    и в какие именно минуты оно это набирает."""
    if not traj:
        return [], [], 0.0
    step = _traj_step_min(traj) if step_min is None else step_min
    end = w.start_utc + timedelta(minutes=w.duration_min)
    xs, ys, acc = [], [], 0.0
    for p in traj:
        if p.t_utc < w.start_utc or p.t_utc > end:
            continue
        xs.append(p.t_utc); ys.append(acc)
        if p.in_saa and p.t_utc < end:
            acc += step
    if not xs:
        return [], [], 0.0
    xs.append(end); ys.append(acc)
    return xs, ys, acc


def _saa_spans(traj):
    """Отрезки трассы в аномалии: [(начало, конец), …] — полосы пролётов."""
    spans, inside, t_from = [], False, None
    for p in list(traj) + [None]:
        flag = bool(p.in_saa) if p is not None else False
        if flag and not inside:
            t_from = p.t_utc
        if inside and not flag:
            spans.append((t_from, p.t_utc if p is not None else traj[-1].t_utc))
        inside = flag
    return spans


def _row_title(fig: go.Figure, row: int, text: str) -> None:
    """Имя ряда внутри самого ряда, слева сверху: над верхним рядом стоит легенда,
    и подписи make_subplots столкнулись бы с ней.

    Имя короткое — два-три слова: ряд объясняют подписанные оси, а не фраза над ним.
    Плашка под именем тёмная, в тон подложке: белая читалась на тёмной странице как наклейка."""
    fig.add_annotation(x=0.005, y=0.99, xref='x domain', yref='y domain', row=row, col=1, secondary_y=False,
                       text=text, showarrow=False, xanchor='left', yanchor='top',
                       font=dict(family=FONT, size=12, color=MUTED), bgcolor='rgba(14,17,23,0.72)',
                       borderpad=3)


def timeline(traj, windows, thr_nT: float, t0: datetime, horizon_min: int, goes, kp, events, forecasts: list,
             mode: str, kp_obs=(), goes_obs=(), past_h: float = 12.0, search_min: int | None = None) -> go.Figure:
    """Лента времени: одна ось времени UTC на все ряды.

    Ряд 1 «Экспозиция по окнам» — накопленные минуты в аномалии от начала каждого окна,
    ступенями, цветом окна; красные полосы — пролёты аномалии, светлые — сами окна.
    Сырое |B| по трассе осталось отдельной линией, свёрнутой в легенде.
    Ряд 2 «Внешняя обстановка» — Kp наблюдения (зелёные столбцы) и прогноз Kp (янтарные,
    штриховкой), порог Kp 7; поток GOES ≥10 МэВ — по правой оси, диапазон по данным; если
    поток ниже первого порога шкалы S, оси нет, а число печатается строкой под графиком.
    Ряд 3 «События и прогнозы» существует только тогда, когда события на горизонте есть;
    иначе под графиком стоит одна строка подписи.

    Слева от t0 показываются прошедшие наблюдения (past_h часов). Экспозиция там не
    рисуется намеренно: она считается от начала каждого окна, а окон в прошлом нет —
    прошлое затенено и подписано, чтобы пустое место не читалось как потерянные данные.
    search_min — период поиска начала: его конец отмечен штриховой линией один раз."""
    from plotly.subplots import make_subplots
    end_h = t0 + timedelta(minutes=horizon_min)
    x_from = t0 - timedelta(hours=past_h if (kp_obs or goes_obs) else 1)
    x_to = end_h + timedelta(minutes=60)

    # --- какие события попадают на горизонт: от этого зависит, есть ли третий ряд
    shown = {}
    for e in events or []:
        a0 = e.valid_from_utc or e.start_utc
        if not a0 or not (t0 - timedelta(hours=6) <= a0 <= end_h):
            continue
        shown.setdefault(e.kind_of_event, []).append((a0, e))
    kinds = [k for k in EVENT_ORDER if k in shown] + [k for k in shown if k not in EVENT_ORDER]
    ev_row = 3 if kinds else None
    rows = 3 if kinds else 2
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.07,
                        row_heights=([0.46, 0.34, 0.20] if kinds else [0.58, 0.42]),
                        specs=([[{'secondary_y': True}], [{'secondary_y': True}], [{}]] if kinds
                               else [[{'secondary_y': True}], [{'secondary_y': True}]]))
    kp_row = 2
    # имя ряда стоит внутри самого ряда, а не над ним: над верхним рядом стоит легенда
    _row_title(fig, 1, 'Экспозиция по окнам')
    _row_title(fig, kp_row, 'Внешняя обстановка')
    if ev_row:
        _row_title(fig, ev_row, 'События и прогнозы')
    foot = []          # строки-подписи под графиком: то, для чего не нужен целый ряд

    # --- ряд 1: прошлое затенено и подписано — пусто там не по ошибке, а по определению
    if (t0 - x_from) >= timedelta(hours=2):
        fig.add_vrect(x0=x_from, x1=t0, fillcolor=PAST_FILL, opacity=0.55, line_width=0, layer='below',
                      row=1, col=1, exclude_empty_subplots=False)
        fig.add_annotation(x=x_from + (t0 - x_from) / 2, y=0.45, xref='x', yref='y domain', row=1, col=1,
                           secondary_y=False, text='работ в прошлом нет: экспозиция считается<br>от начала каждого окна',
                           showarrow=False, align='center', font=dict(family=FONT, size=11, color=GREY))

    # --- ряд 1: полосы пролётов аномалии и полосы окон
    # Заливки полос берут тёмные пары цветов: на тёмной подложке тёмно-синяя и тёмно-карминовая
    # полосы исчезали совсем, и пролёты аномалии было не различить.
    #
    # exclude_empty_subplots=False обязателен. По умолчанию Plotly МОЛЧА выбрасывает полосу,
    # если в ряду, куда её кладут, ещё нет ни одного ряда данных, — а полосы здесь рисуются до
    # линий, потому что они подложка. В итоге ни одна полоса на ленту не попадала: ни пролёты
    # аномалии, ни сами окна. Молчаливого выбрасывания в сервисе быть не может (CONTRACT.md,
    # раздел 7), и «странность» верхнего ряда была ровно этим: ступени накопления висели без
    # всякой привязки к тому, когда станция шла через аномалию.
    for a, b in _saa_spans(traj):
        fig.add_vrect(x0=a, x1=b, fillcolor=dark_pair(RED), opacity=0.16, line_width=0, layer='below',
                      row=1, col=1, exclude_empty_subplots=False)
    for i, w in enumerate(windows):
        x1 = w.start_utc + timedelta(minutes=w.duration_min)
        for r in range(1, rows + 1):
            fig.add_vrect(x0=w.start_utc, x1=x1, fillcolor=dark_pair(win_color(i)), opacity=0.09,
                          line_width=0, layer='below', row=r, col=1, exclude_empty_subplots=False)
        fig.add_annotation(x=w.start_utc, y=0.86, xref='x', yref='y domain', row=1, col=1, secondary_y=False,
                           text='окно %d' % (i + 1), showarrow=False, xanchor='left', yanchor='top',
                           font=dict(family=FONT, size=12, color=win_color(i)))

    # --- ряд 1: накопленные минуты по каждому окну; подпись итога стоит на конце ступени,
    # поэтому в легенду окна не идут (легенда не длиннее четырёх строк)
    step_min = _traj_step_min(traj)
    for i, w in enumerate(windows):
        xs, ys, total = window_exposure(traj, w, step_min)
        if not xs:
            continue
        fig.add_trace(go.Scatter(x=xs, y=ys, mode='lines', showlegend=False,
                                 name='окно %d: накоплено, мин' % (i + 1),
                                 line=dict(color=win_color(i), width=2.4, shape='hv'),
                                 hovertemplate='окно %d: %%{y:.0f} мин в аномалии<extra></extra>' % (i + 1)),
                      row=1, col=1, secondary_y=False)
        # итог окна подписан прямо на конце ступени: цифра рядом с линией читается без легенды,
        # а у правого края подпись уходит влево, чтобы не обрезалась
        near_edge = xs[-1] > x_to - timedelta(hours=3)
        fig.add_trace(go.Scatter(x=[xs[-1]], y=[ys[-1]], mode='markers+text', showlegend=False, hoverinfo='skip',
                                 marker=dict(size=7, color=win_color(i)),
                                 text=['окно %d: %s мин' % (i + 1, _nbsp_int(total))],
                                 textposition='top left' if near_edge else 'middle right',
                                 # цвет ПОДПИСИ ряда (textfont) dark_figure не правит — берём тёмную пару сами
                                 textfont=dict(family=FONT, size=12, color=dark_pair(win_color(i)))),
                      row=1, col=1, secondary_y=False)
    if traj:
        # сырое поле: линия есть, но свёрнута в легенде — кому надо, тот развернёт одним нажатием
        fig.add_trace(go.Scatter(x=[p.t_utc for p in traj], y=[p.B_nT for p in traj], visible='legendonly',
                                 name='|B| на трассе, нТл — наш расчёт',
                                 line=dict(width=1.2, color=BLUE), hovertemplate='%{y:.0f} нТл<extra></extra>'),
                      row=1, col=1, secondary_y=True)
        # ось скрыта: пока линия свёрнута, пустая ось справа была бы тем же, за что ругали ось потока;
        # единица стоит в имени линии и во всплывающей подписи
        fig.update_yaxes(visible=False, showgrid=False, row=1, col=1, secondary_y=True)

    # --- ряд 2: Kp наблюдения одним цветом (цвет = происхождение, не «хорошо/плохо»)
    if kp_obs:
        xs = [a + (b - a) / 2 for a, b, _ in kp_obs]
        wd = [(b - a).total_seconds() * 1000 * 0.92 for a, b, _ in kp_obs]
        fig.add_trace(go.Bar(x=xs, y=[v for _, _, v in kp_obs], width=wd, name='Kp — наблюдение',
                             marker_color=GREEN, opacity=0.85,
                             hovertemplate='Kp %{y:.2f}<extra></extra>'), row=kp_row, col=1, secondary_y=False)
    elif kp is not None and kp.value is not None:
        fig.add_trace(go.Scatter(x=[kp.t_utc], y=[kp.value], mode='markers+text', name='Kp — последнее наблюдение',
                                 marker=dict(size=11, color=GREEN, symbol='diamond'),
                                 text=['Kp %s' % _num_ru(kp.value, 2)], textposition='top center',
                                 hoverinfo='skip'), row=kp_row, col=1, secondary_y=False)
    # --- ряд 2: прогноз Kp — другой цвет и штриховка
    fc = {l['channel']: l for l in (forecasts or [])}
    cells = fc.get('kp_forecast', {}).get('cells', [])
    if cells:
        x = [datetime.fromisoformat(c['from']) + (datetime.fromisoformat(c['to']) - datetime.fromisoformat(c['from'])) / 2 for c in cells]
        wd = [(datetime.fromisoformat(c['to']) - datetime.fromisoformat(c['from'])).total_seconds() * 1000 * 0.92 for c in cells]
        fig.add_trace(go.Bar(x=x, y=[c['value'] for c in cells], width=wd, name='Kp — внешний прогноз',
                             marker=dict(color=AMBER, opacity=0.85, pattern=dict(shape='/', size=5, solidity=0.25,
                                                                                 fgcolor=BG)),
                             hovertemplate='прогноз Kp %{y:.2f}<extra></extra>'), row=kp_row, col=1, secondary_y=False)
    fig.add_hline(y=KP_G3, line_dash='dot', line_color=RED, line_width=1, row=kp_row, col=1, secondary_y=False,
                  annotation_text='порог Kp 7 (буря G3)', annotation_position='top left', annotation_font_size=11,
                  annotation_font_color=RED)

    # --- ряд 2: поток GOES. Ось рисуется только тогда, когда по ней есть что читать
    g_pts = [(t, float(v)) for t, v in (goes_obs or []) if v is not None and float(v) > 0]
    g_max = max((v for _, v in g_pts), default=None)
    if g_pts and g_max >= S1_PFU:
        y_lo = np.log10(min(v for _, v in g_pts)) - 0.2
        y_hi = max(np.log10(g_max) + 0.2, np.log10(S1_PFU) + 0.15)
        fig.add_trace(go.Scatter(x=[t for t, _ in g_pts], y=[v for _, v in g_pts], name='поток GOES — наблюдение',
                                 line=dict(width=1.4, color=GREEN, dash='dot'),
                                 hovertemplate='%{y:.3g} pfu<extra></extra>'), row=kp_row, col=1, secondary_y=True)
        # границы шкалы NOAA S подписаны прямо на делениях оси: отдельная линия на вторичной оси
        # у plotly уезжает на ось Kp, а деление «10» без пометки — просто число
        keep = [(tv, GOES_S_TICKTEXT.get(tv, tt)) for tv, tt in zip(GOES_TICKVALS, GOES_TICKTEXT)
                if y_lo - 0.3 <= np.log10(tv) <= y_hi + 0.3]
        fig.update_yaxes(type='log', title_text='поток ≥10 МэВ, pfu', range=[y_lo, y_hi], showgrid=False,
                         row=kp_row, col=1, secondary_y=True, tickmode='array',
                         tickvals=[tv for tv, _ in keep] or GOES_TICKVALS,
                         ticktext=[tt for _, tt in keep] or GOES_TICKTEXT)
    else:
        # пустую ось до 1000 pfu не рисуем: линия легла бы на дно и читать было бы нечего
        fig.update_yaxes(visible=False, showgrid=False, row=kp_row, col=1, secondary_y=True)
        v = g_max if g_max is not None else (goes.value if (goes is not None and goes.value is not None) else None)
        if v is not None:
            foot.append('поток GOES ≥10 МэВ, наблюдение: максимум %s pfu — ниже порога S1 (%s pfu), '
                        'отдельной оси нет' % (_num_ru(v, 2), _nbsp_int(S1_PFU)))

    # --- ряд 3: события и прогнозы, по типам (категориальная ось). Нет событий — нет ряда
    if kinds:
        for k in kinds:
            lst = shown[k]
            s, c, nm = EVENT_SYM.get(k, ('x', GREY, event_kind_ru(k)))
            # тип события подписан на оси ряда 3 — в легенду он не идёт: легенда не длиннее четырёх строк
            fig.add_trace(go.Scatter(x=[a for a, _ in lst], y=[nm] * len(lst), mode='markers', showlegend=False,
                                     name=nm + (' (сценарий)' if any(e.is_simulated for _, e in lst) else ''),
                                     marker=dict(size=11, color=c, symbol=s, line=dict(width=1, color=BG)),
                                     text=[(e.note or e.event_id)[:110] for _, e in lst], hovertemplate='%{text}<extra>' + nm + '</extra>'),
                          row=ev_row, col=1)
        fig.update_yaxes(title_text='событие, тип', type='category', categoryorder='array',
                         categoryarray=[EVENT_SYM.get(k, ('', '', event_kind_ru(k)))[2] for k in reversed(kinds)],
                         showgrid=True, row=ev_row, col=1)
    else:
        foot.append('событий и прогнозов на горизонте нет — ряд событий не показан')

    # --- отметки времени: каждая ровно один раз и внизу, чтобы не лезть на метки осей
    bottom = rows
    now_ru = 'сейчас' if mode == 'live' else ('отсечка' if mode == 'history_forecast' else 'начало периода')
    _vmark(fig, t0, now_ru, INK, 'solid', 'left')
    if search_min:
        _vmark(fig, t0 + timedelta(minutes=search_min),
               'конец периода поиска начала (%s ч)' % _num_ru(search_min / 60.0), GREY, 'dash', 'right')

    fig.update_yaxes(title_text='накоплено в аномалии, мин', rangemode='tozero', row=1, col=1, secondary_y=False)
    fig.update_yaxes(title_text='Kp (безразмерный)', range=[0, 9.8], row=kp_row, col=1, secondary_y=False)
    # одна и та же ось времени у всех рядов: диапазон задаётся всем, а подпись — только нижнему
    fig.update_xaxes(range=[x_from, x_to])
    fig.update_xaxes(title_text='время, UTC', row=bottom, col=1)
    height = 620 if kinds else 470
    fig = style(fig, height, title=TIMELINE_TITLE.get(mode, TIMELINE_TITLE['live']))
    # строки-подписи стоят ниже подписи оси времени, поэтому нижнее поле считается по их числу
    b = 36 + (14 + 18 * len(foot) if foot else 0)
    fig.update_layout(barmode='overlay', margin=dict(l=10, r=10, t=86, b=b))
    plot_px = max(height - 86 - b, 1)
    for i, line in enumerate(foot):
        fig.add_annotation(x=0, y=-(44.0 + 18.0 * i) / plot_px, xref='paper', yref='paper', text=line,
                           showarrow=False, xanchor='left', yanchor='top',
                           font=dict(family=FONT, size=11, color=GREY))
    return fig


def _vmark(fig: go.Figure, when: datetime, text: str, color: str, dash: str, side: str) -> None:
    """Вертикальная отметка времени: одна линия на всю ленту и одна подпись внизу.

    Подпись ставится у основания линии, а не под верхним рядом: прежде plotly повторял её
    для каждого ряда с собственной осью, и «конец периода поиска начала» печатался дважды,
    налезая на метки правой оси."""
    fig.add_shape(type='line', x0=when, x1=when, xref='x', y0=0, y1=1, yref='paper',
                  line=dict(color=color, width=1.2, dash=dash), layer='above')
    fig.add_annotation(x=when, y=0, xref='x', yref='paper', text=text, showarrow=False,
                       xanchor=side, yanchor='bottom', font=dict(family=FONT, size=11, color=color),
                       bgcolor='rgba(14,17,23,0.78)', borderpad=3)
