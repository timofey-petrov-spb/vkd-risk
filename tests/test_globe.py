# -*- coding: utf-8 -*-
"""Проверки глобуса вкладки «Карта» (app/globe.py) — без браузера.

Что проверяется:
  * форма данных компонента: параллельные массивы трассы, окна, сетка |B|;
  * признак аномалии в данных совпадает с признаком точек трассы — в том числе после прореживания;
  * сетка |B| глобуса и поле плоской карты (app.viz.saa_grid на том же шаге) — одна модель и один порог;
  * прореживание включается только по объёму, сохраняет входы в аномалию и границы окон;
  * подстановка данных не может закрыть тег script; документ собирается и остаётся небольшим;
  * пустая трасса даёт отказ, а не пустой глобус;
  * подготовка данных укладывается в 0,2 с (требование «ничего не замедляет первый рендер»);
  * AppTest: вкладка рендерится в трёх режимах на обоих уровнях, переключатель вида работает.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest
from streamlit.testing.v1 import AppTest

from app import globe
from app.viz import BLUE, WIN, saa_grid

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app', 'main.py')
TIMEOUT = 300
MODES = ['Текущая обстановка', 'Исторический разбор', 'Прогноз из прошлого']
T0 = datetime(2024, 5, 10, 12, tzinfo=timezone.utc)
THR = 22000.0
ALT = 417.0


def make_traj(n: int = 1921):
    """Трасса-образец шага 1 мин: круговая орбита i = 51,6°, h = 417 км, признак аномалии по овалу.
    Сами числа расчёта здесь не проверяются — проверяется только перенос их в компонент."""
    pts = []
    for i in range(n):
        arg = 2 * np.pi * i / 92.9
        lat = np.degrees(np.arcsin(np.sin(np.radians(51.6)) * np.sin(arg)))
        lon = np.degrees(np.arctan2(np.cos(np.radians(51.6)) * np.sin(arg), np.cos(arg))) - i * 360.0 / 1436.07
        lon = (lon + 180.0) % 360.0 - 180.0
        dx = ((lon + 53.0 + 180.0) % 360.0 - 180.0) / 33.0
        dy = (lat + 26.0) / 17.0
        pts.append(SimpleNamespace(t_utc=T0 + timedelta(minutes=i), lat_deg=float(lat), lon_deg=float(lon),
                                   alt_km=ALT, in_saa=bool(dx * dx + dy * dy <= 1.0)))
    return pts


def make_windows():
    return [SimpleNamespace(start_utc=T0 + timedelta(minutes=120), duration_min=360),
            SimpleNamespace(start_utc=T0 + timedelta(minutes=600), duration_min=360)]


@pytest.fixture(scope='module')
def field():
    return globe.saa_field(ALT, THR, T0)


@pytest.fixture(scope='module')
def payload(field):
    return globe.globe_payload(make_traj(), make_windows(), THR, T0, field=field)


# ------------------------------------------------------------------ форма данных

def test_payload_shape(payload):
    """Массивы трассы параллельны и упорядочены по времени; окна и сетка на месте."""
    tr = payload['track']
    assert len(tr['lat']) == len(tr['lon']) == len(tr['saa']) == len(tr['min']) == payload['n_shown']
    assert tr['min'] == sorted(set(tr['min'])), 'минуты трассы должны идти по возрастанию и без повторов'
    assert payload['n_full'] == 1921 and payload['n_shown'] <= globe.MAX_TRACK_POINTS
    assert all(-90.0 <= v <= 90.0 for v in tr['lat'])
    assert all(-180.0 <= v <= 180.0 for v in tr['lon'])
    assert [w['n'] for w in payload['windows']] == [1, 2]
    assert [w['color'] for w in payload['windows']] == [BLUE, WIN], 'цвета окон — те же, что на плоской карте'
    assert payload['windows'][0]['a'] == 120 and payload['windows'][0]['b'] == 480
    g = payload['saa']
    assert len(g['B']) == g['nlat'] * g['nlon'] and g['thr'] == THR
    assert payload['thr_nT'] == THR and payload['step_min'] == 1
    assert payload['bytes'] <= globe.MAX_JSON_BYTES


def test_payload_is_json_and_declares_its_own_weight(payload):
    """Данные сериализуются и читаются обратно без потерь, а объявленный вес — вес этой же строки:
    число в подписи профессионального уровня не должно расходиться с тем, что ушло в компонент."""
    s = globe._dumps(payload)
    assert json.loads(s.replace('<\\/', '</')) == payload
    assert payload['bytes'] == len(s.encode('utf-8'))


@pytest.mark.parametrize('max_bytes', [globe.MAX_JSON_BYTES, 1])
def test_saa_flag_matches_track(field, max_bytes):
    """Признак аномалии в данных компонента совпадает с признаком той же точки трассы —
    и без прореживания, и с ним. Номер точки восстанавливается по минутам: шаг трассы 1 мин."""
    traj = make_traj()
    tr = globe.globe_payload(traj, make_windows(), THR, T0, field=field, max_bytes=max_bytes)['track']
    assert len(tr['min']) == len(tr['saa'])
    for m, s, la, lo in zip(tr['min'], tr['saa'], tr['lat'], tr['lon']):
        assert bool(traj[m].in_saa) == bool(s)
        assert abs(traj[m].lat_deg - la) < 0.01 and abs(traj[m].lon_deg - lo) < 0.01


def test_saa_field_is_the_same_field_as_flat_map(field):
    """Сетка |B| глобуса и поле плоской карты — одна модель IGRF и один порог: область на сфере
    и область на карте не могут разойтись. Сравнение идёт на общем шаге; на экране плоская карта
    берёт мельче (2° × 1°), но поле обеим считает одна функция app.viz._field_nT."""
    lo, la, _ = saa_grid(ALT, THR, T0, step_deg=globe.SAA_STEP_DEG)
    flat = {(round(float(a), 3), round(float(b), 3)) for a, b in zip(la, lo)}
    g = field
    ours = set()
    for r in range(g['nlat']):
        for c in range(g['nlon']):
            if g['B'][r * g['nlon'] + c] < g['thr']:
                ours.add((round(g['lat0'] + r * g['step'], 3), round(g['lon0'] + c * g['step'], 3)))
    assert ours == flat, 'порог |B| по IGRF даёт на глобусе те же узлы, что и у общей сетки карты'
    assert ours, 'область аномалии не должна быть пустой на высоте МКС'


# ------------------------------------------------------------------ прореживание

def test_thinning_triggers_only_by_size(field):
    """При обычном пределе прореживания нет; при жёстком — шаг растёт, объём падает."""
    traj, wins = make_traj(), make_windows()
    full = globe.globe_payload(traj, wins, THR, T0, field=field)
    thin = globe.globe_payload(traj, wins, THR, T0, field=field, max_bytes=1)
    assert full['step_min'] == 1 and full['n_shown'] == 1921
    assert thin['step_min'] == globe.THIN_STEPS[-1]
    assert thin['n_shown'] < full['n_shown'] and thin['bytes'] < full['bytes']


def test_thinning_keeps_anomaly_entries_and_window_edges(field):
    """Прореживание не съедает пролёты аномалии: число входов в область сохраняется,
    границы окон остаются точками трассы — иначе минуты в аномалии нечем объяснить."""
    traj, wins = make_traj(), make_windows()

    def entries(flags):
        return sum(1 for a, b in zip([0] + list(flags), list(flags)) if b and not a)

    full_flags = [1 if p.in_saa else 0 for p in traj]
    thin = globe.globe_payload(traj, wins, THR, T0, field=field, max_bytes=1)
    assert entries(thin['track']['saa']) == entries(full_flags)
    for w in thin['windows']:
        assert w['a'] in thin['track']['min'] and w['b'] in thin['track']['min']


# ------------------------------------------------------------------ документ компонента

def test_html_cannot_close_script_tag(field):
    """Строка данных экранирована: `</` внутри JSON не закроет тег script (иначе документ сломан)."""
    p = globe.globe_payload(make_traj(60), make_windows(), THR, T0, field=field)
    p['texture'] = dict(p['texture'])
    p['texture']['main'] = dict(p['texture']['main'], name='провокация </script><b>')
    doc = globe.globe_html(p)
    head = doc.split('</head>')[0]
    assert 'window.VKD = {' in head
    assert '</script><b>' not in head and '<\\/script>' in head


def test_html_is_complete_and_small(payload):
    """Документ собран целиком, тянет three.js с cdnjs и остаётся небольшим."""
    doc = globe.globe_html(payload)
    assert doc.startswith('<!DOCTYPE html>') and doc.rstrip().endswith('</html>')
    assert globe.THREE_JS in doc and 'cdnjs.cloudflare.com' in globe.THREE_JS
    assert globe.TEXTURE_MAIN['url'] in doc and globe.TEXTURE_FALLBACK['url'] in doc
    assert '/*__VKD_DATA__*/' not in doc and '__VKD_THREE__' not in doc
    assert len(doc.encode('utf-8')) < 200 * 1024
    # отказы объявлены по-русски и отсылают к запасному виду (Т6)
    for word in ('three.js не загрузилась', 'WebGL', 'текстура Земли не загрузилась', 'показать плоскую карту'):
        assert word in doc


def test_empty_trajectory_is_refused(field):
    """Пустая трасса — отказ с сообщением, а не пустой глобус (никаких молчаливых заглушек)."""
    with pytest.raises(ValueError):
        globe.globe_payload([], make_windows(), THR, T0, field=field)


def test_caption_says_what_and_what_not(payload):
    """Подпись одной строкой: что нарисовано, чем посчитано и чего это не даёт."""
    cap = globe.caption(payload)
    assert '180' in cap and 'IGRF' in cap and 'нТл' in cap
    assert 'не доза' in cap and 'не прогноз' in cap
    assert 'прорежена' not in cap                     # при шаге 1 мин про прореживание не пишем
    assert not re.search(r'\d\.\d', cap), 'дроби печатаются с запятой'


def test_caption_declares_thinning(field):
    traj, wins = make_traj(), make_windows()
    thin = globe.globe_payload(traj, wins, THR, T0, field=field, max_bytes=1)
    assert 'прорежена до шага 3 мин' in globe.caption(thin)


def test_tech_line_has_points_step_and_texture(payload):
    """Профессиональный уровень: число точек, шаг прореживания и источник текстуры — в одной строке."""
    line = globe.tech_line(payload)
    assert '1921' in line.replace(' ', '').replace(' ', '')
    assert 'шаг 1 мин' in line and 'NASA Blue Marble' in line and 'КБ' in line
    assert 'IGRF' in line and 'км' in line


def test_component_script_parses(payload, tmp_path):
    """Код компонента — разбираемый JavaScript. AppTest его не исполняет, поэтому синтаксис
    проверяется отдельно: `node --check`. Нет node — проверка пропускается, а не выдумывается."""
    node = shutil.which('node')
    if not node:
        pytest.skip('node не установлен: синтаксис компонента не проверен')
    blocks = re.findall(r'<script(?![^>]*src=)[^>]*>(.*?)</script>', globe.globe_html(payload), re.S)
    assert len(blocks) == 2, 'в документе ровно два своих скрипта: данные и код'
    f = tmp_path / 'component.js'
    f.write_text('var window={};\n' + blocks[0] + '\n' + blocks[1], encoding='utf-8')
    r = subprocess.run([node, '--check', str(f)], capture_output=True, text=True, encoding='utf-8')
    assert r.returncode == 0, r.stderr


# ------------------------------------------------------------------ цена по времени

def test_preparation_is_fast(field):
    """Подготовка данных для глобуса не дороже 0,2 с: первый рендер она замедлять не должна."""
    traj, wins = make_traj(), make_windows()
    globe.globe_payload(traj, wins, THR, T0, field=field)          # прогрев импорта json/numpy
    t = time.perf_counter()
    p = globe.globe_payload(traj, wins, THR, T0, field=field)
    dt_data = time.perf_counter() - t
    t = time.perf_counter()
    globe.globe_html(p)
    dt_html = time.perf_counter() - t
    assert dt_data + dt_html < 0.2, 'подготовка данных %.3f с + документ %.3f с' % (dt_data, dt_html)


def test_saa_field_is_not_slower_than_flat_map():
    """Сетка |B| для глобуса стоит не дороже той же сетки для плоской карты: второй раз IGRF не считается."""
    globe.saa_field(ALT, THR, T0)
    t = time.perf_counter()
    globe.saa_field(ALT, THR, T0)
    dt = time.perf_counter() - t
    assert dt < 0.2, 'сетка |B| считалась %.3f с' % dt


# ------------------------------------------------------------------ экран

def run_app(mode: str | None = None, pro: bool = False) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    if mode and mode != MODES[0]:
        at.sidebar.radio('mode').set_value(mode).run()
    if pro:
        at.sidebar.radio('level').set_value('Профессиональный').run()
    return at


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('pro', [False, True])
def test_map_tab_renders_in_all_modes(mode, pro):
    """Вкладка «Карта» с глобусом рендерится во всех трёх режимах на обоих уровнях без исключений."""
    at = run_app(mode, pro)
    assert not at.exception
    view = at.radio('map_view')
    assert list(view.options) == [globe.VIEW_GLOBE, globe.VIEW_FLAT]
    assert view.value == globe.VIEW_GLOBE, 'по умолчанию — глобус'


def test_view_switch_returns_flat_map():
    """Переключатель вида возвращает плоскую карту: запасной вид доступен всегда."""
    at = run_app()
    assert not at.exception
    caps = '\n'.join(str(c.value) for c in at.caption)
    assert 'не доза' in caps, 'подпись глобуса должна быть на экране'
    n_plotly = len(at.get('plotly_chart'))
    at.radio('map_view').set_value(globe.VIEW_FLAT).run()
    assert not at.exception
    assert len(at.get('plotly_chart')) == n_plotly + 1, 'в запасном виде появляется плоская карта'
    caps = '\n'.join(str(c.value) for c in at.caption)
    assert 'область аномалии и точки трассы в ней' in caps


def test_professional_level_shows_technical_line():
    """На профессиональном уровне под глобусом — точки, шаг и источник текстуры."""
    at = run_app(pro=True)
    assert not at.exception
    caps = '\n'.join(str(c.value) for c in at.caption)
    assert 'NASA Blue Marble' in caps and 'Точек трассы' in caps
