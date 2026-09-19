# -*- coding: utf-8 -*-
"""Глобус вкладки «Карта»: трасса, окна и область аномалии на сфере.

Зачем он нужен и чего не делает
-------------------------------
Плоская равнопромежуточная карта отвечает на вопрос «где проходит трасса», но рвёт её
на долготе 180° и прячет поворот Земли под орбитой. Глобус отвечает на другой вопрос —
**как окно ложится на геометрию пролётов аномалии**: сколько входов в область подряд
захватывает окно и почему у соседнего окна их меньше. Это и есть геометрическое
объяснение числа «минут в аномалии» из карточки окна.

Глобус ничего не считает. Трасса приходит из `vkd/orbit` (SGP4 или OEM), признак
аномалии `in_saa` — из расчёта снимка, поле |B| области — из IGRF (`saa_field` ниже,
та же модель, та же сетка и тот же порог, что у плоской карты `app.viz.saa_grid`;
совпадение проверяется тестом). Здесь только перерисовка уже посчитанного.

Устройство
----------
* `saa_field` — полная сетка |B| по IGRF (не только точки ниже порога): по ней
  компонент строит сглаженный контур уровня, а не «квадратики» узлов сетки;
* `globe_payload` — данные для компонента: параллельные массивы трассы, границы окон,
  сетка |B|. Объём ограничен: если JSON больше `MAX_JSON_BYTES`, трасса прореживается
  до шага 2, затем 3 минут, и шаг объявляется подписью;
* `globe_html` — готовый документ: шаблон + одна строка `window.VKD = {...}`;
* `render_globe` — вывод в Streamlit (`st.iframe`, при его отсутствии `st.components.v1.html`).

Отказы объявляются, а не скрываются (Т6): нет WebGL, не загрузилась three.js или
текстура — компонент пишет об этом по-русски и отсылает к переключателю вида
«Глобус / Плоская карта», который в Streamlit доступен всегда.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import numpy as np

from app.viz import BLUE, GREY, RED, WIN, _nbsp_int

# Текстура грузится браузером зрителя, сервер её не трогает. Обязательное условие — заголовок
# Access-Control-Allow-Origin: без него WebGL текстуру не примет. Адреса и размеры измерены,
# см. docs/design/PROPOSAL_B_GLOBE.md, раздел 4.
TEXTURE_MAIN = {
    'url': 'https://cdn.jsdelivr.net/npm/three-globe@2.42.4/example/img/earth-blue-marble.jpg',
    'px': '4096×2048', 'bytes': 1461877, 'need': 4096,
    'name': 'NASA Blue Marble Next Generation, декабрь 2004 (копия в пакете three-globe, jsDelivr)',
}
TEXTURE_FALLBACK = {
    'url': 'https://threejs.org/examples/textures/planets/earth_atmos_2048.jpg',
    'px': '2048×1024', 'bytes': 512606, 'need': 2048,
    'name': 'запасная текстура 2K (three.js)',
}
THREE_JS = 'https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js'

MAX_TRACK_POINTS = 1921       # горизонт сервиса: 1440 мин поиска + 480 мин окна, шаг 1 мин
MAX_JSON_BYTES = 300 * 1024   # предел объёма данных, передаваемых в компонент
THIN_STEPS = (1, 2, 3)        # шаг прореживания трассы, мин: пробуется по порядку
SAA_STEP_DEG = 4.0            # шаг сетки |B| — тот же, что у плоской карты (app.viz.saa_grid)
SAA_LAT_MIN, SAA_LAT_MAX = -70.0, 70.0
GLOBE_HEIGHT = 560            # высота рамки, пикселей

VIEW_GLOBE, VIEW_FLAT = 'Глобус', 'Плоская карта'

# Подпись под глобусом: что нарисовано, чем посчитано, чего не даёт. Одной строкой (бриф §9.2).
CAPTION = ('Глобус: трасса МКС за весь горизонт серым — без разрыва на долготе 180°, который рвёт плоскую карту; '
           'область аномалии и отрезки трассы в ней красным — наш расчёт |B| по IGRF ниже порога %s нТл; '
           'окна-кандидаты своими цветами с подписью времени начала. Это показ уже посчитанного: '
           'не доза, не прогноз и не оценка риска.')


def window_color(i: int) -> str:
    """Цвет окна — тот же, что на плоской карте и в карточках: первое окно синим, остальные светлым."""
    return BLUE if i == 0 else WIN


def saa_field(alt_km: float, thr_nT: float, when: datetime, step_deg: float = SAA_STEP_DEG) -> dict:
    """Полная сетка |B| по IGRF на высоте alt_km — та же модель, шаг и диапазон, что у
    `app.viz.saa_grid`, но без отбора по порогу: контур уровня компонент строит сам.

    Возвращает узлы сетки и |B| в нТл, округлённые до целых (шаг сетки 4° грубее этой точности).
    """
    import ppigrf
    lats = np.arange(SAA_LAT_MIN, SAA_LAT_MAX + 0.001, step_deg)
    lons = np.arange(-180.0, 180.001, step_deg)
    LO, LA = np.meshgrid(lons, lats)
    Be, Bn, Bu = ppigrf.igrf(LO.ravel(), LA.ravel(), np.full(LO.size, float(alt_km)), when.replace(tzinfo=None))
    B = np.sqrt(Be ** 2 + Bn ** 2 + Bu ** 2).ravel()
    return {'lat0': float(lats[0]), 'lon0': float(lons[0]), 'step': float(step_deg),
            'nlat': int(lats.size), 'nlon': int(lons.size), 'thr': float(thr_nT),
            'B': [int(round(v)) for v in B.tolist()]}


def _thin(traj, windows, step_min: int) -> list[int]:
    """Номера точек трассы, которые уйдут в компонент при шаге step_min минут.

    Кроме узлов шага сохраняются точки, где меняется признак аномалии, и границы окон:
    иначе прореживание съело бы короткие пролёты, из которых и складываются минуты в аномалии.
    Возвращает возрастающий список без повторов; при step_min = 1 это все точки.
    """
    n = len(traj)
    keep = set(range(0, n, max(1, int(step_min))))
    keep.add(n - 1)
    prev = None
    for i, p in enumerate(traj):
        flag = bool(p.in_saa)
        if prev is not None and flag != prev:
            keep.add(i - 1)
            keep.add(i)
        prev = flag
    t0 = traj[0].t_utc
    for w in windows:
        for edge in (w.start_utc, w.start_utc + timedelta(minutes=w.duration_min)):
            k = int(round((edge - t0).total_seconds() / 60.0))
            if 0 <= k < n:
                keep.add(k)
    return sorted(keep)


def _dumps(payload: dict) -> str:
    """JSON для подстановки в документ. `</` экранируется: иначе строка данных закрыла бы тег script."""
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')


def globe_payload(traj, windows, thr_nT: float, when: datetime, field: dict | None = None,
                  max_bytes: int = MAX_JSON_BYTES) -> dict:
    """Данные для компонента: трасса, окна, сетка |B|, служебные величины.

    traj — точки шага 1 мин из снимка расчёта; windows — окна-кандидаты в порядке экрана.
    field — заранее посчитанная сетка |B| (`saa_field`); None — считается здесь.
    Объём ограничен дважды: не больше max_bytes в JSON и не больше MAX_TRACK_POINTS точек трассы.
    Не уложились — трасса прореживается по THIN_STEPS, и шаг объявляется подписью.
    """
    if not traj:
        raise ValueError('трассы нет: нечего рисовать на глобусе')
    alt_km = float(np.mean([p.alt_km for p in traj]))
    fld = field if field is not None else saa_field(alt_km, thr_nT, when)
    t0 = traj[0].t_utc
    wins = []
    for i, w in enumerate(windows):
        a = int(round((w.start_utc - t0).total_seconds() / 60.0))
        wins.append({'n': i + 1, 'a': a, 'b': a + int(w.duration_min), 'color': window_color(i),
                     'label': w.start_utc.strftime('%d.%m %H:%M')})
    payload = {}
    for step in THIN_STEPS:
        idx = _thin(traj, windows, step)
        payload = {
            't0': t0.strftime('%d.%m %H:%M'),
            'alt_km': round(alt_km, 1),
            'step_min': step,
            'n_full': len(traj),
            'n_shown': len(idx),
            'track': {
                'min': [int(round((traj[i].t_utc - t0).total_seconds() / 60.0)) for i in idx],
                'lat': [round(traj[i].lat_deg, 2) for i in idx],
                'lon': [round(traj[i].lon_deg, 2) for i in idx],
                'saa': [1 if traj[i].in_saa else 0 for i in idx],
            },
            'windows': wins,
            'saa': fld,
            'thr_nT': float(thr_nT),
            'texture': {'main': TEXTURE_MAIN, 'fallback': TEXTURE_FALLBACK},
            'colors': {'track': GREY, 'saa': RED},
        }
        # Вес объявляется точно: сам ключ «bytes» тоже попадает в строку, поэтому размер
        # берётся неподвижной точкой (двух-трёх проходов всегда хватает).
        payload['bytes'] = 0
        for _ in range(4):
            size = len(_dumps(payload).encode('utf-8'))
            if size == payload['bytes']:
                break
            payload['bytes'] = size
        if payload['bytes'] <= max_bytes and payload['n_shown'] <= MAX_TRACK_POINTS:
            break
    return payload


def caption(payload: dict) -> str:
    """Подпись под глобусом для обоих уровней: что нарисовано, чем посчитано, чего не даёт."""
    text = CAPTION % _nbsp_int(payload['thr_nT'])
    if payload['step_min'] > 1:
        text += (' Трасса прорежена до шага %d мин, чтобы данные компонента остались в пределах %d КБ; '
                 'моменты входа в аномалию и границы окон сохранены.'
                 % (payload['step_min'], MAX_JSON_BYTES // 1024))
    return text


def tech_line(payload: dict) -> str:
    """Строка профессионального уровня: сколько точек, какой шаг, откуда текстура и сколько весят данные."""
    return ('Точек трассы: %s из %s (шаг %d мин). Данные компонента: %s КБ. Сетка |B| по IGRF: шаг %g°, '
            'высота %s км (средняя по трассе). Текстура Земли — %s; грузится браузером зрителя, сервер её не передаёт.'
            % (_nbsp_int(payload['n_shown']), _nbsp_int(payload['n_full']), payload['step_min'],
               ('%.1f' % (payload['bytes'] / 1024.0)).replace('.', ','), payload['saa']['step'],
               ('%.1f' % payload['alt_km']).replace('.', ','), TEXTURE_MAIN['name']))


def globe_html(payload: dict) -> str:
    """Готовый документ компонента: шаблон и одна подстановка данных перед закрытием head."""
    return _TEMPLATE.replace('/*__VKD_DATA__*/', 'window.VKD = %s;' % _dumps(payload)) \
                    .replace('__VKD_THREE__', THREE_JS)


def render_globe(payload: dict, height: int = GLOBE_HEIGHT) -> None:
    """Вывод глобуса в Streamlit. `st.iframe` — основной путь; `st.components.v1.html` — запасной
    для сборок, где `st.iframe` ещё нет. JS в AppTest не исполняется, элемент просто не рендерится."""
    import streamlit as st
    doc = globe_html(payload)
    if hasattr(st, 'iframe'):
        st.iframe(doc, height=height)
    else:                                   # pragma: no cover — путь для старых сборок Streamlit
        import streamlit.components.v1 as components
        components.html(doc, height=height)


# --------------------------------------------------------------------------- шаблон компонента
# Ни одной пользовательской строки внутрь не подставляется — только числа и заранее известные
# подписи из этого файла. Данные приходят одной строкой window.VKD (см. globe_html).
_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Глобус ВКД-Риск</title>
<style>
  html,body{margin:0;height:100%;background:#ffffff;color:#1a1f2b;overflow:hidden;
    font-family:"Segoe UI",Inter,Roboto,Arial,sans-serif;font-size:12px}
  #scene{position:absolute;inset:0}
  .panel{position:absolute;background:#ffffffee;border:1px solid #d7dbdf;border-radius:6px;padding:8px 10px}
  #legend{left:10px;top:10px;max-width:270px}
  #ctl{right:10px;top:10px}
  #stat{left:10px;bottom:10px;right:10px;font-size:11px;color:#5b6675;line-height:1.45;
    background:none;border:none;padding:0}
  .row{display:flex;align-items:center;gap:7px;margin:4px 0}
  .sw{width:15px;height:3px;border-radius:2px;flex:none}
  .bx{width:13px;height:9px;border-radius:2px;flex:none;opacity:.45}
  button{background:#fff;color:#1a1f2b;border:1px solid #d7dbdf;border-radius:5px;padding:5px 10px;
    cursor:pointer;font-size:12px;font-family:inherit}
  button:hover{border-color:#1f4e79;color:#1f4e79}
  #err{position:absolute;inset:0;display:none;flex-direction:column;align-items:center;justify-content:center;
    padding:28px;text-align:center;line-height:1.65;background:#fff}
  #err b{display:block;margin-bottom:8px;font-size:13px}
  #err .hint{margin-top:12px;color:#1f4e79;border:1px solid #1f4e79;border-radius:5px;padding:7px 12px}
</style>
<script>/*__VKD_DATA__*/</script>
</head>
<body>
<div id="scene"></div>
<div class="panel" id="legend"></div>
<div class="panel" id="ctl"><button id="spin">Пауза вращения</button></div>
<div class="panel" id="stat">загрузка…</div>
<div id="err"></div>
<script src="__VKD_THREE__" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<script>
"use strict";
var D = window.VKD || null;
var TRACK_C = 0x7f8c8d, SAA_C = 0xc0392b, HALO_C = 0xffffff;
var R_E = 6371.0;
var scene, camera, renderer, globe, texInfo = "текстура загружается…", maxTex = 0;
var camR = 2.9, camLat = 0.30, camLon = 0.6, dragging = false, px = 0, py = 0, idle = 0;
var spinning = true, lastFrame = 0;

function fail(title, body) {
  var e = document.getElementById("err");
  e.style.display = "flex";
  e.innerHTML = "<b>" + title + "</b>" + body +
    "<div class='hint'>показать плоскую карту: переключатель «Вид» над глобусом</div>";
  var i, ids = ["legend", "ctl", "stat"];
  for (i = 0; i < ids.length; i++) document.getElementById(ids[i]).style.display = "none";
}

/* Положение на сфере THREE.SphereGeometry: uv совпадает с равнопромежуточной текстурой,
   поэтому шов текстуры и трасса не расходятся. */
function toVec(lat, lon, r) {
  var th = (90 - lat) * Math.PI / 180, ph = (lon + 180) * Math.PI / 180;
  return new THREE.Vector3(-r * Math.cos(ph) * Math.sin(th), r * Math.cos(th), r * Math.sin(ph) * Math.sin(th));
}
function hex(s) { return parseInt(String(s).replace("#", ""), 16); }

function legend() {
  var h = "", i, w = D.windows;
  h += "<div class='row'><span class='sw' style='background:" + D.colors.track + "'></span>" +
       "<span>трасса за горизонт, шаг " + D.step_min + " мин</span></div>";
  h += "<div class='row'><span class='sw' style='background:" + D.colors.saa + "'></span>" +
       "<span>трасса в аномалии</span></div>";
  h += "<div class='row'><span class='bx' style='background:" + D.colors.saa + "'></span>" +
       "<span>область аномалии, |B| ниже порога</span></div>";
  for (i = 0; i < w.length; i++) {
    h += "<div class='row'><span class='sw' style='background:" + w[i].color + "'></span>" +
         "<span>окно " + w[i].n + ": " + w[i].label + " UTC</span></div>";
  }
  document.getElementById("legend").innerHTML = h;
}

/* ---- область аномалии: контур уровня |B| = порог, а не квадраты узлов сетки.
   Каждая ячейка сетки отсекается по полю (линейная интерполяция вдоль рёбер) — получается
   выпуклый многоугольник, он разбивается веером на треугольники. Граница выходит гладкой. */
function buildSaa() {
  var g = D.saa, thr = g.thr, pos = [], idx = [], edge = [], i, j;
  var at = function (r, c) { return g.B[r * g.nlon + c]; };
  var la = function (r) { return g.lat0 + r * g.step; }, lo = function (c) { return g.lon0 + c * g.step; };
  for (i = 0; i < g.nlat - 1; i++) {
    for (j = 0; j < g.nlon - 1; j++) {
      var cor = [[la(i), lo(j), at(i, j)], [la(i), lo(j + 1), at(i, j + 1)],
                 [la(i + 1), lo(j + 1), at(i + 1, j + 1)], [la(i + 1), lo(j), at(i + 1, j)]];
      var k, a, b, out = [], cross = [];
      for (k = 0; k < 4; k++) {
        a = cor[k]; b = cor[(k + 1) % 4];
        if (a[2] < thr) out.push([a[0], a[1]]);
        if ((a[2] < thr) !== (b[2] < thr)) {
          var f = (thr - a[2]) / (b[2] - a[2]);
          var p = [a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])];
          out.push(p); cross.push(p);
        }
      }
      if (out.length < 3) continue;
      var base = pos.length / 3, v;
      for (k = 0; k < out.length; k++) { v = toVec(out[k][0], out[k][1], 1.0035); pos.push(v.x, v.y, v.z); }
      for (k = 1; k < out.length - 1; k++) idx.push(base, base + k, base + k + 1);
      if (cross.length === 2) {
        for (k = 0; k < 2; k++) { v = toVec(cross[k][0], cross[k][1], 1.0045); edge.push(v.x, v.y, v.z); }
      }
    }
  }
  if (!pos.length) return 0;
  var geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  geo.setIndex(idx);
  scene.add(new THREE.Mesh(geo, new THREE.MeshBasicMaterial({color: SAA_C, transparent: true, opacity: 0.30,
            side: THREE.DoubleSide, depthWrite: false})));
  var eg = new THREE.BufferGeometry();
  eg.setAttribute("position", new THREE.Float32BufferAttribute(edge, 3));
  scene.add(new THREE.LineSegments(eg, new THREE.LineBasicMaterial({color: SAA_C, transparent: true, opacity: 0.9})));
  return idx.length / 3;
}

/* Цвет и «слой» точки трассы: аномалия важнее окна, окно важнее фона. */
function kindOf(k) {
  var t = D.track.min[k], i;
  if (D.track.saa[k]) return {c: SAA_C, r: 1.0 + 0.0075, w: 3, hl: true};
  for (i = 0; i < D.windows.length; i++) {
    if (t >= D.windows[i].a && t <= D.windows[i].b) return {c: hex(D.windows[i].color), r: 1.0 + 0.0065, w: 2, hl: true};
  }
  return {c: TRACK_C, r: 1.0 + 0.0055, w: 1, hl: false};
}

/* Трасса: непрерывные отрезки одного вида. Разрыва на долготе 180° на сфере нет — в этом
   и состоит преимущество перед плоской картой. Толщину линий WebGL не поддерживает,
   поэтому яркие отрезки набираются точками с белой подложкой. */
function buildTrack() {
  var n = D.track.lat.length, k, cur = null, seg = [], made = 0;
  var flush = function () {
    if (!cur || seg.length < 2) { return; }
    scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(seg),
      new THREE.LineBasicMaterial({color: cur.c, transparent: true, opacity: cur.w === 1 ? 0.55 : 1.0})));
    if (cur.hl) {
      var p = [], i;
      for (i = 0; i < seg.length; i++) p.push(seg[i].x, seg[i].y, seg[i].z);
      var gh = new THREE.BufferGeometry();
      gh.setAttribute("position", new THREE.Float32BufferAttribute(p, 3));
      scene.add(new THREE.Points(gh, new THREE.PointsMaterial({color: HALO_C, size: 0.019,
                transparent: true, opacity: 0.7})));
      var gc = new THREE.BufferGeometry();
      gc.setAttribute("position", new THREE.Float32BufferAttribute(p.slice(), 3));
      scene.add(new THREE.Points(gc, new THREE.PointsMaterial({color: cur.c, size: 0.012})));
    }
    made++;
  };
  for (k = 0; k < n; k++) {
    var kd = kindOf(k), v = toVec(D.track.lat[k], D.track.lon[k], kd.r);
    if (!cur || kd.c !== cur.c) {
      if (cur) { seg.push(toVec(D.track.lat[k], D.track.lon[k], cur.r)); flush(); }
      cur = kd; seg = [];
    }
    seg.push(v);
  }
  flush();
  return made;
}

/* Подпись времени начала окна: спрайт с холста — в r128 иного способа дать текст в сцене нет. */
function label(text, color, v) {
  var cv = document.createElement("canvas"), s = 2;
  cv.width = 190 * s; cv.height = 40 * s;
  var c = cv.getContext("2d");
  c.scale(s, s);
  c.fillStyle = "rgba(255,255,255,0.92)";
  c.strokeStyle = color; c.lineWidth = 1.5;
  c.beginPath(); c.rect(1, 1, 188, 38); c.fill(); c.stroke();
  c.fillStyle = "#1a1f2b";
  c.font = "600 15px 'Segoe UI', Arial, sans-serif";
  c.textAlign = "center"; c.textBaseline = "middle";
  c.fillText(text, 95, 20);
  var tex = new THREE.CanvasTexture(cv);
  var sp = new THREE.Sprite(new THREE.SpriteMaterial({map: tex, transparent: true, depthTest: false}));
  sp.scale.set(0.42, 0.09, 1);
  sp.position.copy(v);
  scene.add(sp);
}

function buildWindowStarts() {
  var i, k, n = D.track.min.length;
  for (i = 0; i < D.windows.length; i++) {
    var w = D.windows[i], best = -1;
    for (k = 0; k < n; k++) { if (D.track.min[k] >= w.a) { best = k; break; } }
    if (best < 0) continue;
    var v = toVec(D.track.lat[best], D.track.lon[best], 1.012);
    var m = new THREE.Mesh(new THREE.SphereGeometry(0.016, 14, 10),
            new THREE.MeshBasicMaterial({color: hex(w.color)}));
    m.position.copy(v);
    scene.add(m);
    label("окно " + w.n + " · " + w.label, w.color, toVec(D.track.lat[best], D.track.lon[best], 1.13));
  }
}

function loadTexture() {
  var cfg = D.texture.main;
  if (cfg.need > maxTex) {
    texInfo = "текстура " + cfg.px + " не поддержана: предел устройства " + maxTex + " пикселей, взята запасная";
    cfg = D.texture.fallback;
  }
  var t0 = performance.now(), loader = new THREE.TextureLoader();
  loader.setCrossOrigin("anonymous");
  loader.load(cfg.url, function (tex) {
    tex.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
    globe.material = new THREE.MeshBasicMaterial({map: tex});
    globe.material.needsUpdate = true;
    texInfo = "текстура " + cfg.px + ", " + (cfg.bytes / 1048576).toFixed(2).replace(".", ",") + " МБ, за " +
              ((performance.now() - t0) / 1000).toFixed(2).replace(".", ",") + " с";
    status();
  }, undefined, function () {
    texInfo = "текстура Земли не загрузилась (нет сети или заголовка CORS): шар остаётся серым, " +
              "трасса, окна и область аномалии нарисованы верно";
    status();
  });
}

function status() {
  document.getElementById("stat").innerHTML =
    "Трасса и окна — показ уже посчитанного: минуты в аномалии считает расчёт по точкам трассы, а не картинка. " +
    "Точек " + D.n_shown + " из " + D.n_full + ", шаг " + D.step_min + " мин; высота " +
    String(D.alt_km).replace(".", ",") + " км в истинном масштабе. " + texInfo + ".";
}

function bind() {
  var el = renderer.domElement;
  el.addEventListener("mousedown", function (e) { dragging = true; px = e.clientX; py = e.clientY; idle = 0; });
  window.addEventListener("mouseup", function () { dragging = false; });
  window.addEventListener("mousemove", function (e) {
    if (!dragging) return;
    camLon -= (e.clientX - px) * 0.005;
    camLat = Math.max(-1.4, Math.min(1.4, camLat + (e.clientY - py) * 0.005));
    px = e.clientX; py = e.clientY; idle = 0;
  });
  el.addEventListener("wheel", function (e) {
    e.preventDefault();
    camR = Math.max(1.35, Math.min(8, camR * (1 + (e.deltaY > 0 ? 0.08 : -0.08))));
  }, {passive: false});
  document.getElementById("spin").onclick = function () {
    spinning = !spinning;
    this.textContent = spinning ? "Пауза вращения" : "Пуск вращения";
  };
  window.addEventListener("resize", function () {
    var host = document.getElementById("scene");
    if (!renderer) return;
    camera.aspect = host.clientWidth / Math.max(host.clientHeight, 1);
    camera.updateProjectionMatrix();
    renderer.setSize(host.clientWidth, host.clientHeight);
  });
}

function animate() {
  requestAnimationFrame(animate);
  var now = performance.now(), dt = Math.min(0.1, (now - lastFrame) / 1000);
  lastFrame = now; idle += dt;
  if (spinning && !dragging && idle > 1.5) camLon += dt * 0.035;   /* около 2 градусов в секунду */
  camera.position.set(camR * Math.cos(camLat) * Math.sin(camLon), camR * Math.sin(camLat),
                      camR * Math.cos(camLat) * Math.cos(camLon));
  camera.lookAt(0, 0, 0);
  renderer.render(scene, camera);
}

function init() {
  if (!D || !D.track || !D.track.lat.length) {
    fail("Данных для глобуса нет.", "Трасса пуста: орбита недоступна.");
    return;
  }
  if (typeof THREE === "undefined") {
    fail("Библиотека three.js не загрузилась.",
         "Компонент берёт её из сети (cdnjs). Расчёт это не затрагивает: числа на экране получены без неё.");
    return;
  }
  var host = document.getElementById("scene");
  try { renderer = new THREE.WebGLRenderer({antialias: true, alpha: false}); } catch (e) { renderer = null; }
  if (!renderer || !renderer.getContext()) {
    fail("WebGL в этом браузере недоступен.", "Трёхмерная сцена не строится.");
    return;
  }
  maxTex = renderer.getContext().getParameter(renderer.getContext().MAX_TEXTURE_SIZE);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0xffffff, 1);
  renderer.setSize(host.clientWidth, host.clientHeight);
  host.appendChild(renderer.domElement);
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(42, host.clientWidth / Math.max(host.clientHeight, 1), 0.05, 100);
  globe = new THREE.Mesh(new THREE.SphereGeometry(1, 96, 64), new THREE.MeshBasicMaterial({color: 0xb8c2cc}));
  scene.add(globe);
  legend();
  buildSaa();
  buildTrack();
  buildWindowStarts();
  loadTexture();
  bind();
  status();
  lastFrame = performance.now();
  animate();
}

init();
</script>
</body>
</html>
"""
