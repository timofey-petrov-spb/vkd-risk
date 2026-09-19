# -*- coding: utf-8 -*-
"""Вид графиков (app/viz.py): тёмная подложка, одна гарнитура, палитра проекта.

Проверяется не «красиво ли», а то, что поддаётся проверке и что ломается первым:

  * ни одного белого цвета в рисунке — белый прямоугольник на тёмной странице читается
    как дыра в вёрстке, и именно за это замечание пришло от экспертов;
  * подложка и сетка — ровно те цвета, что у страницы (.streamlit/config.toml, :root ui.py);
  * гарнитура одна на весь рисунок: разнобой шрифтов был вторым замечанием;
  * таблица тёмных пар DARK_PAIR не разошлась с DARK_TRACE_COLORS в app/ui.py;
  * заливки полос на ленте берут ТЁМНЫЕ пары: app/ui.dark_figure до заливок фигур разметки
    не достаёт, и тёмно-синяя полоса окна на тёмном фоне пропадала совсем;
  * рамки поля и вертикальной сетки времени нет: они ничего не отделяют и ничего не помогают
    прочесть;
  * заголовки и имена рядов короткие — владелец просил «никаких огромных текстов».

Смысл рисунков здесь не проверяется: за него отвечают tests/test_ui_app.py (оси, единицы,
число строк легенды, ряд событий) и tests/test_globe.py.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import plotly.graph_objects as go
import pytest

from app import viz
from app.viz import ground_track, style, timeline

T0 = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
THR = 24000.0

# Палитра проекта: фон, текст, приглушённый, наш расчёт, наблюдение, прогноз, условие, разделитель.
PALETTE = {'#0e1117', '#e8ebf2', '#a7b0c0', '#7ab8f5', '#5ed39a', '#e7b45c', '#f58b7f',
           '#9aa5b5', '#242b38', '#161b26', '#4f8fc0', '#3f6f97'}


def _traj(n: int = 90):
    """Трасса-образец: половина точек в аномалии, поле известно — рисуются все слои ленты."""
    return [SimpleNamespace(t_utc=T0 + timedelta(minutes=i), lat_deg=-20.0 + 0.1 * i, lon_deg=-50.0 + 0.3 * i,
                            alt_km=420.0, in_saa=(20 <= i < 40), B_nT=22000.0 if 20 <= i < 40 else 31000.0)
            for i in range(n)]


def _win(offset_min: int = 10, dur: int = 60):
    return SimpleNamespace(start_utc=T0 + timedelta(minutes=offset_min), duration_min=dur)


def _timeline():
    kp = [(T0 - timedelta(hours=h + 3), T0 - timedelta(hours=h), 3.0) for h in range(9, 0, -3)]
    obs = [(T0 - timedelta(hours=h), 10.0 ** (h % 3)) for h in range(6, 0, -1)]
    ev = [SimpleNamespace(kind_of_event='SEP', event_id='x1', note='протонное событие', is_simulated=False,
                          start_utc=T0 + timedelta(hours=1), valid_from_utc=None)]
    return timeline(_traj(), [_win(), _win(120, 60)], THR, T0, 360, None, None, ev, [], 'live',
                    kp_obs=kp, goes_obs=obs, search_min=360)


def _map():
    return ground_track(_traj(), [_win()], THR, T0)


# ------------------------------------------------------------------ обход цветов рисунка

_COLOR_KEY = re.compile(r'(color|colour)s?$', re.I)
_WHITE = re.compile(r'^(white|#fff|#ffffff|rgba?\(\s*255\s*,\s*255\s*,\s*255)', re.I)


def _colors(fig) -> list:
    """Все цвета рисунка: значения ключей, оканчивающихся на color.

    Шаблон Plotly (layout.template) в обход не идёт: это не наш рисунок, а таблица умолчаний
    библиотеки, и своих величин мы в неё не кладём.
    """
    out = []

    def walk(node, key=''):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == 'template':
                    continue
                walk(v, k)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and _COLOR_KEY.search(key):
            out.append(node)

    walk(fig.to_plotly_json())
    return out


def _fonts(fig) -> set:
    """Гарнитуры, названные в рисунке (font.family на любом уровне)."""
    out = set()

    def walk(node, key=''):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == 'template':
                    continue
                walk(v, k)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and key == 'family':
            out.add(node)

    walk(fig.to_plotly_json())
    return out


# ------------------------------------------------------------------ подложка

@pytest.mark.parametrize('name', ['лента', 'карта'])
def test_v_risunke_net_belogo(name):
    """Ни одного белого цвета: белая плашка, белый океан и белая штриховка на тёмной странице
    читаются как дыра в вёрстке. Это то самое замечание экспертов, с которого начался круг."""
    fig = _timeline() if name == 'лента' else _map()
    white = [c for c in _colors(fig) if _WHITE.match(c.strip())]
    assert not white, 'в рисунке «%s» остался белый цвет: %r' % (name, white)


def test_podlozhka_v_ton_stranitse():
    """Подложка рисунка — тот же фон, что у страницы, а не белый лист и не свой оттенок."""
    for fig in (_timeline(), _map()):
        assert fig.layout.paper_bgcolor == viz.BG, fig.layout.paper_bgcolor
        assert fig.layout.plot_bgcolor == viz.BG, fig.layout.plot_bgcolor
    geo = _map().layout.geo
    assert geo.oceancolor == viz.BG and geo.bgcolor == viz.BG, (geo.oceancolor, geo.bgcolor)
    assert geo.landcolor == viz.PANEL, geo.landcolor
    assert geo.showframe is False, 'рамки у карты нет: она ничего не отделяет'


def test_setka_bez_ramki_i_bez_vertikalnykh_liniy():
    """Осталась одна горизонтальная сетка цветом разделителя: рамка поля и вертикальная сетка
    времени ничего не отделяют и читать не помогают."""
    fig = style(go.Figure(), 200)
    assert fig.layout.xaxis.showgrid is False and fig.layout.yaxis.showgrid is True
    assert fig.layout.yaxis.gridcolor == viz.RULE
    assert fig.layout.xaxis.showline is False and fig.layout.yaxis.showline is False
    assert fig.layout.xaxis.zeroline is False and fig.layout.yaxis.zeroline is False


# ------------------------------------------------------------------ шрифт и палитра

@pytest.mark.parametrize('name', ['лента', 'карта'])
def test_odna_garnitura(name):
    """Одна гарнитура на весь рисунок: разнобой шрифтов внутри одного экрана виден сразу."""
    fig = _timeline() if name == 'лента' else _map()
    assert _fonts(fig) == {viz.FONT}, _fonts(fig)


def test_podpisi_osey_krupnee_deleniy():
    """Подпись оси крупнее её делений: ось без единицы нечитаема, и единицу надо видеть сразу."""
    fig = style(go.Figure(), 200)
    assert fig.layout.yaxis.title.font.size > fig.layout.yaxis.tickfont.size
    assert fig.layout.yaxis.tickfont.color == viz.MUTED
    assert fig.layout.font.color == viz.INK and fig.layout.font.size >= 13


def test_temnye_pary_te_zhe_chto_v_ui():
    """DARK_PAIR — та же таблица, что DARK_TRACE_COLORS в app/ui.py, а не похожие оттенки на глаз.

    Две таблицы держат один и тот же смысл: тёмная пара цвета величины. Разойдутся — один и тот же
    синий станет на ленте одним оттенком, а на карте другим, и система цветов, ради которой всё
    заведено, на экране перестанет читаться.
    """
    from app.ui import DARK_TRACE_COLORS
    assert viz.DARK_PAIR == DARK_TRACE_COLORS
    assert set(viz.DARK_PAIR.values()) <= PALETTE, set(viz.DARK_PAIR.values()) - PALETTE
    # незнакомый цвет остаётся собой: портить нечего
    assert viz.dark_pair('#123456') == '#123456'


def test_tsveta_iz_palitry_proekta():
    """Все цвета рисунка — из палитры проекта: непрозрачные берутся точно, полупрозрачные
    (rgba) должны совпадать с палитрой по составляющим. Новых цветов не изобретаем."""
    seen = set()
    for fig in (_timeline(), _map()):
        for c in _colors(fig):
            c = c.strip().lower()
            if c.startswith('#'):
                seen.add(c)
    # цвета величин остаются светлыми ключами таблицы: их подставит app/ui.dark_figure
    allowed = PALETTE | set(viz.DARK_PAIR) | {viz.PAST_FILL}
    assert seen <= allowed, seen - allowed


# ------------------------------------------------------------------ лента: полосы и тексты

def test_polosy_okon_i_anomalii_voobshche_est():
    """Полосы пролётов аномалии и полосы окон на ленте ЕСТЬ.

    Их не было. Plotly по умолчанию молча выбрасывает полосу (`add_vrect`), если в ряду, куда её
    кладут, ещё нет ни одного ряда данных, — а полосы рисуются до линий, потому что они подложка.
    На экране от этого ступени накопления висели без всякой привязки к тому, когда станция идёт
    через аномалию, и верхний ряд читался как «странный»: полосы были в коде и не были на экране.
    Считаем по числу: два окна на трёх рядах и один пролёт аномалии в образце.
    """
    fig = _timeline()
    fills = [sh.fillcolor for sh in (fig.layout.shapes or ()) if getattr(sh, 'fillcolor', None)]
    assert fills.count(viz.dark_pair(viz.RED)) == 1, 'полоса пролёта аномалии: %r' % fills
    assert fills.count(viz.dark_pair(viz.win_color(0))) == 3, 'окно 1 на всех трёх рядах: %r' % fills
    assert fills.count(viz.dark_pair(viz.win_color(1))) == 3, 'окно 2 на всех трёх рядах: %r' % fills


def test_zalivki_polos_berut_temnye_pary():
    """Заливки полос берут ТЁМНЫЕ пары. app/ui.dark_figure правит цвет линии фигуры разметки,
    а до заливки не достаёт: тёмно-синяя полоса окна и тёмно-карминовая полоса пролёта аномалии
    на тёмном фоне пропадали совсем, и пролёты было не различить."""
    fig = _timeline()
    fills = {sh.fillcolor for sh in (fig.layout.shapes or ()) if getattr(sh, 'fillcolor', None)}
    assert viz.dark_pair(viz.RED) in fills, fills
    assert viz.dark_pair(viz.win_color(0)) in fills, fills
    assert viz.BLUE not in fills and viz.RED not in fills, 'заливка взяла цвет светлой темы: %r' % fills


def test_podpis_itoga_okna_vidna():
    """Цвет ПОДПИСИ ряда (textfont) dark_figure не правит — подпись «окно 1: N мин» берёт
    тёмную пару сама, иначе она печаталась тёмно-синим по тёмному."""
    fig = _timeline()
    labels = [tr for tr in fig.data if tr.text and any('окно 1:' in str(t) for t in tr.text)]
    assert labels, [tr.name for tr in fig.data]
    assert labels[0].textfont.color == viz.dark_pair(viz.win_color(0)), labels[0].textfont.color


@pytest.mark.parametrize('mode', ['live', 'history_review', 'history_forecast'])
def test_zagolovok_lenty_korotkiy_i_nazyvaet_proiskhozhdenie(mode):
    """Заголовок — одна короткая строка «что и откуда». Длинный заголовок читается дольше,
    чем сам рисунок; подробности стоят в подписи под ним (app/ui.timeline_caption)."""
    title = viz.TIMELINE_TITLE[mode]
    assert len(title) <= 90, (len(title), title)
    assert 'наш расчёт' in title
    assert title.count(';') <= 1, 'заголовок из трёх частей — это уже абзац'


def test_imena_ryadov_v_dva_tri_slova():
    """Имя ряда внутри ряда — два-три слова: ряд объясняют подписанные оси, а не фраза над ним.
    Плашка под именем тёмная: белая наклейка на тёмной подложке была первым, что бросалось в глаза."""
    fig = _timeline()
    rows = [a for a in (fig.layout.annotations or ()) if a.text in
            ('Экспозиция по окнам', 'Внешняя обстановка', 'События и прогнозы')]
    assert len(rows) == 3, [a.text for a in (fig.layout.annotations or ())]
    for a in rows:
        assert len(a.text.split()) <= 3, a.text
        assert a.bgcolor and '255' not in a.bgcolor, a.bgcolor
        assert a.font.color == viz.MUTED and a.font.family == viz.FONT
