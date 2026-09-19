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
аномалии `in_saa` — из расчёта снимка, поле |B| области — из IGRF (`saa_field` ниже:
та же модель и тот же порог, что у плоской карты, — обе области считает одна функция
`app.viz._field_nT`; совпадение проверяется тестом). Шаг сетки у видов разный: глобус
берёт узлы 4° и строит контур уровня уже в браузере, плоская карта — 2° по долготе
и 1° по широте на сервере. Здесь только перерисовка уже посчитанного.

Рекомендованное окно (круг 11, раздел 3.5)
-----------------------------------------
`globe_payload` принимает **необязательный** параметр `recommended` — границы
рекомендованного окна в UTC. Когда он задан, глобус обыгрывает рекомендацию:
трасса за окно рисуется ярко, остальной горизонт приглушён, положения станции на
начало и конец окна отмечены подписанными метками, а бегунок ведёт метку по трассе
внутри окна и показывает время UTC и модуль поля в этой точке. Бегунок целиком на
стороне браузера: он двигает уже переданную метку, к серверу не обращается.

Параметр именно необязательный, и без него данные компонента прежние до байта: экран
рекомендацию ещё не передаёт, и сломать его нельзя (проверяется тестом по контрольной
сумме строки данных).

Устройство
----------
* `saa_field` — полная сетка |B| по IGRF (не только точки ниже порога): по ней
  компонент строит сглаженный контур уровня, а не «квадратики» узлов сетки;
* `globe_payload` — данные для компонента: параллельные массивы трассы, границы окон,
  сетка |B|, при наличии — блок рекомендованного окна. Объём ограничен: если JSON больше
  `MAX_JSON_BYTES`, трасса прореживается до шага 2, затем 3 минут, и шаг объявляется подписью;
* `globe_html` — готовый документ: шаблон + одна строка `window.VKD = {...}`;
* `render_globe` — вывод в Streamlit (`st.iframe`, при его отсутствии `st.components.v1.html`).

Отказы объявляются, а не скрываются (Т6): нет WebGL, не загрузилась three.js или
текстура — компонент пишет об этом по-русски и отсылает к переключателю вида
«Глобус / Плоская карта», который в Streamlit доступен всегда. Рекомендованное окно,
не укладывающееся в трассу, — тоже отказ с сообщением, а не молчаливая подрезка.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

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
SAA_STEP_DEG = 4.0            # шаг сетки |B|, градусы: контур уровня строит компонент (app.viz.saa_grid)
SAA_LAT_MIN, SAA_LAT_MAX = -70.0, 70.0
GLOBE_HEIGHT = 560            # высота рамки, пикселей

# Во сколько раз рекомендованное окно вправе увеличить объём данных компонента. Предел назвал
# владелец: «не больше чем вдвое» от нынешних ~57 КБ на отрисовку. Рекомендация добавляет один
# массив — |B| по точкам внутри окна (не больше MAX_TRACK_POINTS чисел, ~6 знаков каждое), поэтому
# предел с запасом соблюдается по построению; тест это измеряет, а не принимает на веру.
REC_MAX_GROWTH = 2.0

VIEW_GLOBE, VIEW_FLAT = 'Глобус', 'Плоская карта'

# Подпись под глобусом: что нарисовано, чем посчитано, чего не даёт. Одной строкой (бриф §9.2).
CAPTION = ('Глобус: трасса МКС за весь горизонт серым — без разрыва на долготе 180°, который рвёт плоскую карту; '
           'область аномалии и отрезки трассы в ней красным — наш расчёт |B| по IGRF ниже порога %s нТл, '
           'контур области построен по сетке %s° и потому огрублён до неё; '
           'окна-кандидаты своими цветами с подписью времени начала. Это показ уже посчитанного: '
           'не доза, не прогноз и не оценка риска.')
# Добавка к подписи, когда экран передал рекомендованное окно.
REC_CAPTION = (' Рекомендованное окно %s — %s UTC выделено ярко, остальная трасса горизонта приглушена; '
               'метками с подписью времени отмечены положения станции на начало и на конец окна. '
               'Бегунок «Время в окне» ведёт метку по трассе и показывает время UTC и модуль поля '
               'в этой точке; бегунок работает в браузере и расчёта не касается.')


def window_color(i: int) -> str:
    """Цвет окна — тот же, что на плоской карте и в карточках: первое окно синим, остальные светлым."""
    return BLUE if i == 0 else WIN


def saa_field(alt_km: float, thr_nT: float, when: datetime, step_deg: float = SAA_STEP_DEG) -> dict:
    """Полная сетка |B| по IGRF на высоте alt_km — та же модель, шаг и диапазон, что у
    `app.viz.saa_grid` с тем же шагом, но без отбора по порогу: контур уровня компонент строит сам.

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


def _thin(traj, windows, step_min: int, extra_minutes=()) -> list[int]:
    """Номера точек трассы, которые уйдут в компонент при шаге step_min минут.

    Кроме узлов шага сохраняются точки, где меняется признак аномалии, и границы окон:
    иначе прореживание съело бы короткие пролёты, из которых и складываются минуты в аномалии.
    `extra_minutes` — ещё минуты от начала трассы, которые обязаны остаться точками (границы
    рекомендованного окна: на них стоят метки, и они не должны уехать на соседний узел).
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
    for k in extra_minutes:
        if 0 <= k < n:
            keep.add(int(k))
    return sorted(keep)


def _rec_bounds(recommended) -> tuple[datetime, datetime]:
    """Границы рекомендованного окна в UTC из того, что дал экран.

    Принимается либо пара (начало, конец), либо само окно с полями `start_utc` и
    `duration_min` — в таком виде рекомендация приходит из `vkd/windows`, и разбирать её на
    стороне экрана незачем. Всё остальное — отказ с сообщением: молча угадывать формат нельзя.
    """
    if hasattr(recommended, 'start_utc') and hasattr(recommended, 'duration_min'):
        start = recommended.start_utc
        return start, start + timedelta(minutes=float(recommended.duration_min))
    if isinstance(recommended, (tuple, list)) and len(recommended) == 2:
        return recommended[0], recommended[1]
    raise ValueError('рекомендованное окно: ожидалась пара (начало, конец) в UTC либо окно с полями '
                     'start_utc и duration_min, получено %r' % (recommended,))


def _rec_minutes(recommended, traj) -> tuple[int, int, datetime, datetime]:
    """Границы рекомендованного окна в минутах от начала трассы, с проверками вместо подрезки."""
    start, end = _rec_bounds(recommended)
    for name, value in (('начало', start), ('конец', end)):
        if not isinstance(value, datetime):
            raise ValueError('рекомендованное окно: %s должно быть моментом времени, получено %r' % (name, value))
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('рекомендованное окно: %s без часового пояса — момент UTC не определён' % name)
    t0, t_last = traj[0].t_utc, traj[-1].t_utc
    a = int(round((start - t0).total_seconds() / 60.0))
    b = int(round((end - t0).total_seconds() / 60.0))
    if b <= a:
        raise ValueError('рекомендованное окно: конец %s не позже начала %s'
                         % (end.isoformat(), start.isoformat()))
    if a < 0 or b > len(traj) - 1:
        raise ValueError('рекомендованное окно %s — %s выходит за трассу %s — %s: подрезать его молча нельзя'
                         % (start.isoformat(), end.isoformat(), t0.isoformat(), t_last.isoformat()))
    return a, b, start, end


def _rec_block(traj, idx: list[int], minutes: list[int], a: int, b: int,
               t0: datetime, start: datetime, end: datetime, thr_nT: float) -> dict:
    """Блок рекомендованного окна для компонента.

    `a`, `b` — границы окна в минутах от начала трассы; `i0`, `i1` — номера первой и последней
    ПОКАЗАННЫХ точек трассы внутри окна (по ним компонент ставит метки и водит бегунок).
    `B` — модуль поля в этих точках, нТл: его показывает бегунок. Величина ровно та же, по
    которой считается признак аномалии и минуты в аномалии, — `TrajectoryPoint.B_nT` против
    порога `saa_B_threshold_nT` (см. vkd/windows/compare.py, строка с integrate(... th.saa_B_threshold_nT)).
    Округление до целого нТл: это заметно мельче погрешности самой модели IGRF и совпадает с
    округлением сетки |B| в `saa_field`. Нет значения — None, а не подставленный ноль.
    `t0_iso` нужен браузеру, чтобы посчитать время метки: минута от начала трассы плюс этот момент.
    """
    i0 = next((k for k, m in enumerate(minutes) if m >= a), None)
    i1 = next((k for k in range(len(minutes) - 1, -1, -1) if minutes[k] <= b), None)
    if i0 is None or i1 is None or i1 < i0:
        raise ValueError('рекомендованное окно: внутри него не осталось точек трассы (минуты %d…%d)' % (a, b))
    if minutes[i0] != a or minutes[i1] != b:
        raise ValueError('рекомендованное окно: его границы не попали в точки трассы (%d…%d вместо %d…%d)'
                         % (minutes[i0], minutes[i1], a, b))
    field = []
    for k in range(i0, i1 + 1):
        point = traj[idx[k]]
        value = point.B_nT
        field.append(None if value is None else int(round(float(value))))
        # Признак аномалии и порог контура обязаны быть согласованы: красные отрезки трассы
        # рисуются по in_saa, а контур области на поверхности — по этому же порогу на сетке |B|.
        # Разошлись — значит трассу строили с одним порогом, а рисуют с другим, и картинка соврёт
        # ровно там, где показывает, где набирается доза. Это отказ, а не повод подогнать одно к другому.
        if value is not None and point.in_saa is not None \
                and bool(point.in_saa) != bool(float(value) < float(thr_nT)):
            raise ValueError('точка трассы на минуте %d: признак аномалии %r не отвечает порогу %g нТл '
                             'при |B| = %g нТл — трасса и контур области посчитаны по разным порогам'
                             % (minutes[k], point.in_saa, float(thr_nT), float(value)))
    return {'a': a, 'b': b, 'i0': i0, 'i1': i1,
            'start': start.astimezone(timezone.utc).strftime('%d.%m %H:%M'),
            'end': end.astimezone(timezone.utc).strftime('%d.%m %H:%M'),
            't0_iso': t0.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'color': BLUE,          # рекомендация — наш расчёт: тот же синий, что у расчётных величин
            'B': field}


def _dumps(payload: dict) -> str:
    """JSON для подстановки в документ. `</` экранируется: иначе строка данных закрыла бы тег script."""
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')


def globe_payload(traj, windows, thr_nT: float, when: datetime, field: dict | None = None,
                  max_bytes: int = MAX_JSON_BYTES, recommended=None) -> dict:
    """Данные для компонента: трасса, окна, сетка |B|, служебные величины.

    traj — точки шага 1 мин из снимка расчёта; windows — окна-кандидаты в порядке экрана.
    field — заранее посчитанная сетка |B| (`saa_field`); None — считается здесь.
    recommended — НЕОБЯЗАТЕЛЬНЫЕ границы рекомендованного окна в UTC: пара (начало, конец) либо
    само окно с полями start_utc и duration_min. Не задано — данные ровно те же, что были до
    круга 11, до байта: ключ `rec` не появляется и ничего не сдвигается.
    Объём ограничен дважды: не больше max_bytes в JSON и не больше MAX_TRACK_POINTS точек трассы.
    Не уложились — трасса прореживается по THIN_STEPS, и шаг объявляется подписью.
    """
    if not traj:
        raise ValueError('трассы нет: нечего рисовать на глобусе')
    alt_km = float(np.mean([p.alt_km for p in traj]))
    fld = field if field is not None else saa_field(alt_km, thr_nT, when)
    t0 = traj[0].t_utc
    rec_a = rec_b = rec_start = rec_end = None
    if recommended is not None:
        rec_a, rec_b, rec_start, rec_end = _rec_minutes(recommended, traj)
    wins = []
    for i, w in enumerate(windows):
        a = int(round((w.start_utc - t0).total_seconds() / 60.0))
        wins.append({'n': i + 1, 'a': a, 'b': a + int(w.duration_min), 'color': window_color(i),
                     'label': w.start_utc.strftime('%d.%m %H:%M')})
    payload = {}
    for step in THIN_STEPS:
        idx = _thin(traj, windows, step, () if rec_a is None else (rec_a, rec_b))
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
        if rec_a is not None:
            payload['rec'] = _rec_block(traj, idx, payload['track']['min'],
                                        rec_a, rec_b, t0, rec_start, rec_end, thr_nT)
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
    text = CAPTION % (_nbsp_int(payload['thr_nT']), ('%g' % payload['saa']['step']).replace('.', ','))
    if 'rec' in payload:
        text += REC_CAPTION % (payload['rec']['start'], payload['rec']['end'])
    if payload['step_min'] > 1:
        text += (' Трасса прорежена до шага %d мин, чтобы данные компонента остались в пределах %d КБ; '
                 'моменты входа в аномалию и границы окон сохранены.'
                 % (payload['step_min'], MAX_JSON_BYTES // 1024))
    return text


def tech_line(payload: dict) -> str:
    """Строка профессионального уровня: сколько точек, какой шаг, откуда текстура и сколько весят данные."""
    text = ('Точек трассы: %s из %s (шаг %d мин). Данные компонента: %s КБ. Сетка |B| по IGRF: шаг %g°, '
            'высота %s км (средняя по трассе). Текстура Земли — %s; грузится браузером зрителя, сервер её не передаёт.'
            % (_nbsp_int(payload['n_shown']), _nbsp_int(payload['n_full']), payload['step_min'],
               ('%.1f' % (payload['bytes'] / 1024.0)).replace('.', ','), payload['saa']['step'],
               ('%.1f' % payload['alt_km']).replace('.', ','), TEXTURE_MAIN['name']))
    if 'rec' in payload:
        rec = payload['rec']
        known = sum(v is not None for v in rec['B'])
        text += (' Рекомендованное окно: минуты %s…%s от начала трассы, точек в окне %s, '
                 'из них с известным |B| %s (бегунок ведёт метку по этим точкам).'
                 % (_nbsp_int(rec['a']), _nbsp_int(rec['b']), _nbsp_int(len(rec['B'])), _nbsp_int(known)))
    return text


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
  /* Палитра сцены — та же, что у страницы: фон #0e1117, текст #e8ebf2, приглушённый #a7b0c0,
     разделитель #242b38. Космос тёмный, и единственное яркое пятно в кадре — сама Земля.
     Гарнитура одна на всё: панели, подписи меток на шаре и строка бегунка. */
  html,body{margin:0;height:100%;background:#0e1117;color:#e8ebf2;overflow:hidden;
    font-family:Inter,"Segoe UI",Roboto,Arial,sans-serif;font-size:13px;line-height:1.35;
    -webkit-font-smoothing:antialiased}
  #scene{position:absolute;inset:0}
  .panel{position:absolute;background:rgba(22,27,38,0.88);border:1px solid #242b38;border-radius:8px;
    padding:11px 13px}
  #legend{left:14px;top:14px}
  #ctl{right:14px;top:14px;padding:0;background:none;border:none}
  /* Низ сцены — одна полоса: слева бегунок, справа техническая строка. Прежде панель бегунка
     стояла под кнопкой вращения и закрывала метку начала окна; здесь она шар не перекрывает.
     Полоса не перехватывает мышь (pointer-events), иначе шар переставал вращаться над ней. */
  #foot{position:absolute;left:14px;right:14px;bottom:14px;display:flex;align-items:flex-end;
    gap:16px;justify-content:space-between;pointer-events:none}
  #foot>*{pointer-events:auto}
  #stat{flex:1;min-width:0;text-align:right;font-size:12px;color:#a7b0c0}
  /* Ширина 320 px подобрана по самой длинной подписи «дд.мм чч:мм UTC · |B| 24 000 нТл · в аномалии». */
  #tl{position:static;width:320px;flex:none;display:none}
  #tl .ttl{color:#a7b0c0;font-size:12px;margin-bottom:7px}
  /* Бегунок нарисован сам, а не оставлен браузеру: дорожка цветом разделителя, ползунок цветом
     нашего расчёта. Своя рамка фокуса нужна затем, чтобы вместо неё браузер не рисовал
     собственную оранжевую — оранжевого в палитре экрана нет. */
  #tl input[type=range]{-webkit-appearance:none;appearance:none;width:100%;height:16px;margin:2px 0 7px;
    background:transparent;cursor:pointer}
  #tl input[type=range]::-webkit-slider-runnable-track{height:4px;border-radius:2px;background:#242b38}
  #tl input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:14px;height:14px;
    margin-top:-5px;border-radius:50%;background:#7ab8f5;border:2px solid #0e1117}
  #tl input[type=range]::-moz-range-track{height:4px;border-radius:2px;background:#242b38}
  #tl input[type=range]::-moz-range-thumb{width:12px;height:12px;border:2px solid #0e1117;
    border-radius:50%;background:#7ab8f5}
  #tl input[type=range]:focus{outline:2px solid #7ab8f5;outline-offset:3px}
  #tl .val{white-space:nowrap;font-weight:600;font-size:13px;color:#e8ebf2}
  /* Легенда — сетка с одинаковым шагом строк и одинаковыми образцами, а не список разной длины. */
  .row{display:flex;align-items:center;gap:10px;height:23px}
  .sw{width:22px;height:4px;border-radius:2px;flex:none}
  .bx{width:22px;height:12px;border-radius:3px;flex:none;opacity:.55}
  /* Приглушённое в легенде показано так же, как на шаре: тот же цвет, меньшая насыщенность. */
  .dim{opacity:.5}
  button{background:rgba(22,27,38,0.88);color:#e8ebf2;border:1px solid #242b38;border-radius:8px;
    padding:8px 13px;cursor:pointer;font-size:12px;font-family:inherit}
  button:hover{border-color:#7ab8f5;color:#7ab8f5}
  #err{position:absolute;inset:0;display:none;flex-direction:column;align-items:center;justify-content:center;
    padding:28px;text-align:center;line-height:1.65;background:#0e1117;color:#e8ebf2}
  #err b{display:block;margin-bottom:8px;font-size:14px}
  #err .hint{margin-top:12px;color:#7ab8f5;border:1px solid #7ab8f5;border-radius:8px;padding:8px 13px}
</style>
<script>/*__VKD_DATA__*/</script>
</head>
<body>
<div id="scene"></div>
<div class="panel" id="legend"></div>
<div class="panel" id="ctl"><button id="spin">Пауза вращения</button></div>
<div id="foot">
  <div class="panel" id="tl">
    <div class="ttl">Время в окне</div>
    <input type="range" id="tlr" min="0" max="1" step="1" value="0" aria-label="Время внутри рекомендованного окна">
    <div class="val" id="tlv"></div>
  </div>
  <div id="stat">загрузка…</div>
</div>
<div id="err"></div>
<script src="__VKD_THREE__" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<script>
"use strict";
var D = window.VKD || null;
/* Тёмные пары цветов величин — та же таблица, что подставляет графикам app/ui.dark_figure.
   Строка данных несёт цвет ПРОИСХОЖДЕНИЯ величины (синий — наш расчёт), а оттенок под тёмную
   сцену выбирает компонент: от этого строка данных не меняется ни на байт, и прежний экран,
   который сверяет её контрольную сумму, ничего не замечает. */
var DARK = {"#1f4e79": "#7ab8f5", "#5dade2": "#4f8fc0", "#85c1e9": "#3f6f97",
            "#c0392b": "#f58b7f", "#7f8c8d": "#9aa5b5"};
function dark(c) { var k = String(c).toLowerCase(); return DARK[k] || c; }
/* Трасса горизонта — цвет вспомогательных линий, аномалия — красный тёмной темы: насыщенный,
   но не кислотный. Кант меток белый: на тёмной текстуре океана он их и держит. */
var TRACK_C = 0x9aa5b5, SAA_C = 0xf58b7f, HALO_C = 0xffffff;
/* Свечение атмосферы: цвет нашего расчёта, три тонкие оболочки по краю диска. */
var AIR_C = 0x7ab8f5;
var scene, camera, renderer, globe, texInfo = "текстура загружается…", maxTex = 0;
var camR = 2.9, camLat = 0.30, camLon = 0.6, dragging = false, px = 0, py = 0, idle = 0;
var spinning = true, lastFrame = 0;
/* Инерция вращения: скорость, набранная перетаскиванием, затухает по экспоненте.
   DAMP = 2.2 1/с — скорость падает вдвое примерно за 0,3 с, шар не «улетает».
   V_MAX ограничивает рывок, чтобы резкое движение мышью не раскручивало сцену. */
var vLon = 0, vLat = 0, lastMove = 0;
var DAMP = 2.2, V_MAX = 3.0, V_STOP = 1e-4;
/* Непрозрачность линий трассы. Когда экран передал рекомендованное окно, ярко идёт только оно:
   OP_REC — рекомендованное окно, OP_DIM — аномалия и окна-кандидаты вне рекомендации (их видно,
   но они не спорят с ответом), OP_BG_DIM — остальной горизонт. Когда рекомендации нет, фон идёт
   OP_BG, а всё остальное ярко. На тёмной сцене приглушённое читается лучше, чем на светлой,
   поэтому все три приглушённые яркости ниже прежних: ярче Земли и рекомендации не должно
   быть ничего. */
var OP_REC = 1.0, OP_DIM = 0.28, OP_BG_DIM = 0.12, OP_BG = 0.45;
/* Радиусы меток, доли радиуса Земли: окно-кандидат 0,016 — как было; границы рекомендованного
   окна 0,020 и бегунок 0,022 крупнее, потому что читаются первыми. Белый кант 1,5 радиуса. */
var R_WIN_MARK = 0.016, R_REC_MARK = 0.020, R_SLIDE_MARK = 0.022, RIM_K = 1.5;
var recMark = null;

function fail(title, body) {
  var e = document.getElementById("err");
  e.style.display = "flex";
  e.innerHTML = "<b>" + title + "</b>" + body +
    "<div class='hint'>показать плоскую карту: переключатель «Вид» над глобусом</div>";
  var i, ids = ["legend", "ctl", "stat", "tl", "foot"];
  for (i = 0; i < ids.length; i++) document.getElementById(ids[i]).style.display = "none";
}

/* Положение на сфере THREE.SphereGeometry: uv совпадает с равнопромежуточной текстурой,
   поэтому шов текстуры и трасса не расходятся. */
function toVec(lat, lon, r) {
  var th = (90 - lat) * Math.PI / 180, ph = (lon + 180) * Math.PI / 180;
  return new THREE.Vector3(-r * Math.cos(ph) * Math.sin(th), r * Math.cos(th), r * Math.sin(ph) * Math.sin(th));
}
function hex(s) { return parseInt(dark(s).replace("#", ""), 16); }

/* Строка легенды: образец одного размера, подпись и признак приглушения — тот же приём, что
   на шаре. Образцы у всех строк одинаковые по ширине, поэтому подписи выстраиваются по сетке. */
function lrow(cls, color, text, dim) {
  var d = dim ? " dim" : "";
  return "<div class='row'><span class='" + cls + d + "' style='background:" + dark(color) + "'></span>" +
         "<span class='" + (dim ? "dim" : "") + "'>" + text + "</span></div>";
}

/* Легенда читается за секунду: две-три слова на строку, ровный шаг строк, одинаковые образцы.
   Всё, что подписью не является — шаг трассы, порог поля, шаг сетки контура, времена окон, —
   стоит в строке под сценой и в подписи под глобусом, а не здесь: легенда называет, ЧТО каким
   цветом нарисовано, и ничего больше. Времена начала и конца рекомендованного окна стоят
   подписанными метками на самой трассе, повторять их в легенде незачем. */
function legend() {
  var h = "", i, w = D.windows;
  if (D.rec) {
    h += lrow("sw", D.rec.color, "рекомендованное окно", false);
    h += lrow("sw", D.colors.saa, "аномалия в окне", false);
    h += lrow("sw", D.colors.saa, "аномалия вне окна", true);
    h += lrow("sw", D.colors.track, "трасса горизонта", true);
  } else {
    h += lrow("sw", D.colors.track, "трасса горизонта", false);
    h += lrow("sw", D.colors.saa, "трасса в аномалии", false);
  }
  h += lrow("bx", D.colors.saa, "область аномалии", false);
  for (i = 0; i < w.length; i++) {
    h += lrow("sw", w[i].color, "окно " + w[i].n + " · " + w[i].label, !!D.rec);
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
  scene.add(new THREE.Mesh(geo, new THREE.MeshBasicMaterial({color: SAA_C, transparent: true, opacity: 0.20,
            side: THREE.DoubleSide, depthWrite: false})));
  var eg = new THREE.BufferGeometry();
  eg.setAttribute("position", new THREE.Float32BufferAttribute(edge, 3));
  scene.add(new THREE.LineSegments(eg, new THREE.LineBasicMaterial({color: SAA_C, transparent: true, opacity: 0.7})));
  return idx.length / 3;
}

/* Тонкое свечение по краю диска. Оболочка чуть больше шара, у которой рисуются только ЗАДНИЕ
   грани: там, где она за Землёй, её закрывает сам шар, и на экране остаётся только ободок по
   краю диска. Три оболочки убывающей плотности дают мягкий спад вместо кольца с резким краем.
   Сложением цвета (AdditiveBlending) и без записи глубины — чтобы свечение не гасило трассу.
   Ни анимации, ни бликов: это оформление края, а не эффект. */
var AIR_SHELLS = [[1.012, 0.10], [1.030, 0.06], [1.055, 0.03]];

function buildAtmosphere() {
  var i, r, o;
  for (i = 0; i < AIR_SHELLS.length; i++) {
    r = AIR_SHELLS[i][0]; o = AIR_SHELLS[i][1];
    scene.add(new THREE.Mesh(new THREE.SphereGeometry(r, 48, 32),
      new THREE.MeshBasicMaterial({color: AIR_C, transparent: true, opacity: o, side: THREE.BackSide,
                                   blending: THREE.AdditiveBlending, depthWrite: false})));
  }
}

function inRec(t) { return !!D.rec && t >= D.rec.a && t <= D.rec.b; }

/* Цвет, «слой» и яркость точки трассы.

   Цвет означает состояние величины, а не украшение: красный — точка внутри аномалии (|B| ниже
   порога), синий рекомендации — точка внутри рекомендованного окна, цвет окна — точка внутри
   окна-кандидата, серый — остальной горизонт. Яркость означает другое: ярко идёт то, о чём
   спрашивал человек, то есть рекомендованное окно; всё прочее приглушено, но остаётся видимым.

   Радиусы (1,0075 / 1,0065 / 1,0055 доли радиуса Земли) разводят линии по высоте, чтобы совпавшие
   отрезки не мерцали; порядок тот же, что и важность: аномалия над окном, окно над фоном. */
function kindOf(k) {
  var t = D.track.min[k], i, saa = !!D.track.saa[k];
  if (D.rec) {
    if (inRec(t)) {
      return saa ? {c: SAA_C, r: 1.0 + 0.0075, w: 3, hl: true, op: OP_REC}
                 : {c: hex(D.rec.color), r: 1.0 + 0.0065, w: 2, hl: true, op: OP_REC};
    }
    if (saa) return {c: SAA_C, r: 1.0 + 0.0075, w: 3, hl: false, op: OP_DIM};
    for (i = 0; i < D.windows.length; i++) {
      if (t >= D.windows[i].a && t <= D.windows[i].b) {
        return {c: hex(D.windows[i].color), r: 1.0 + 0.0065, w: 2, hl: false, op: OP_DIM};
      }
    }
    return {c: TRACK_C, r: 1.0 + 0.0055, w: 1, hl: false, op: OP_BG_DIM};
  }
  if (saa) return {c: SAA_C, r: 1.0 + 0.0075, w: 3, hl: true, op: OP_REC};
  for (i = 0; i < D.windows.length; i++) {
    if (t >= D.windows[i].a && t <= D.windows[i].b) {
      return {c: hex(D.windows[i].color), r: 1.0 + 0.0065, w: 2, hl: true, op: OP_REC};
    }
  }
  return {c: TRACK_C, r: 1.0 + 0.0055, w: 1, hl: false, op: OP_BG};
}

/* Трасса: непрерывные отрезки одного вида. Разрыва на долготе 180° на сфере нет — в этом
   и состоит преимущество перед плоской картой. Толщину линий WebGL не поддерживает,
   поэтому яркие отрезки набираются точками с белой подложкой. */
function buildTrack() {
  var n = D.track.lat.length, k, cur = null, seg = [], made = 0;
  var flush = function () {
    if (!cur || seg.length < 2) { return; }
    scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(seg),
      new THREE.LineBasicMaterial({color: cur.c, transparent: true, opacity: cur.op})));
    if (cur.hl) {
      var p = [], i;
      for (i = 0; i < seg.length; i++) p.push(seg[i].x, seg[i].y, seg[i].z);
      var gh = new THREE.BufferGeometry();
      gh.setAttribute("position", new THREE.Float32BufferAttribute(p, 3));
      /* Подложка яркой линии — её собственный цвет вполсилы, а не белый: на тёмной сцене белая
         обводка спорила бы с Землёй, а своим цветом линия просто читается толще. */
      scene.add(new THREE.Points(gh, new THREE.PointsMaterial({color: cur.c, size: 0.021,
                transparent: true, opacity: 0.30})));
      var gc = new THREE.BufferGeometry();
      gc.setAttribute("position", new THREE.Float32BufferAttribute(p.slice(), 3));
      scene.add(new THREE.Points(gc, new THREE.PointsMaterial({color: cur.c, size: 0.012})));
    }
    made++;
  };
  for (k = 0; k < n; k++) {
    /* Отрезок рвётся и по цвету, и по яркости: два соседних куска одного цвета могут различаться
       только тем, попал кусок в рекомендованное окно или нет. */
    var kd = kindOf(k), v = toVec(D.track.lat[k], D.track.lon[k], kd.r);
    if (!cur || kd.c !== cur.c || kd.op !== cur.op) {
      if (cur) { seg.push(toVec(D.track.lat[k], D.track.lon[k], cur.r)); flush(); }
      cur = kd; seg = [];
    }
    seg.push(v);
  }
  flush();
  return made;
}

/* Подпись в сцене: спрайт с холста — в r128 иного способа дать текст в сцене нет.
   Ширина плашки не задана числом, а измерена по самому тексту (measureText) плюс поля по 12 px:
   подписи рекомендованного окна длиннее подписей окон-кандидатов, и фиксированные 190 px их
   обрезали бы. Холст рисуется вдвое крупнее CSS-размера (s = 2) — иначе текст мылится на экранах
   с удвоенной плотностью. Высота плашки 40 px и высота в сцене 0,09 — как было; ширина в сцене
   берётся из тех же пропорций, поэтому буквы не растягиваются. */
var LBL_H = 40, LBL_PAD = 14, LBL_WORLD_H = 0.095;
var LBL_FONT = "600 17px Inter, 'Segoe UI', Arial, sans-serif";

function label(text, color, v) {
  var cv = document.createElement("canvas"), s = 2, c = cv.getContext("2d");
  c.font = LBL_FONT;
  var w = Math.ceil(c.measureText(text).width) + 2 * LBL_PAD;
  cv.width = w * s; cv.height = LBL_H * s;
  c = cv.getContext("2d");
  c.scale(s, s);
  /* Плашка тёмная, в тон панелям сцены, с кантом цвета самой метки: белая наклейка на тёмном
     шаре была самым светлым пятном кадра и спорила с Землёй. */
  c.fillStyle = "rgba(14,17,23,0.82)";
  c.strokeStyle = dark(color); c.lineWidth = 1.5;
  c.beginPath(); c.rect(1, 1, w - 2, LBL_H - 2); c.fill(); c.stroke();
  c.fillStyle = "#e8ebf2";
  c.font = LBL_FONT;
  c.textAlign = "center"; c.textBaseline = "middle";
  c.fillText(text, w / 2, LBL_H / 2);
  var tex = new THREE.CanvasTexture(cv);
  var sp = new THREE.Sprite(new THREE.SpriteMaterial({map: tex, transparent: true, depthTest: false}));
  sp.scale.set(LBL_WORLD_H * w / LBL_H, LBL_WORLD_H, 1);
  sp.position.copy(v);
  scene.add(sp);
}

/* Метка положения станции: цветной шар и белый кант вокруг него. Кант — сфера чуть большего
   радиуса, у которой рисуются только ЗАДНИЕ грани (THREE.BackSide): передняя полусфера не
   закрывает цветной шар, а по краю остаётся белый ободок, и метка не теряется на тёмной текстуре.
   Обе части в одной группе — двигать метку бегунком достаточно за группу.

   Центр метки поднят над поверхностью ровно на радиус канта: иначе нижняя половина шара тонет
   в глобусе и от метки остаётся серп. Высоту орбиты это не изображает — метка лежит на шаре. */
function markRadius(rad) { return 1.0 + rad * RIM_K; }

function dot(lat, lon, rad, color) {
  var g = new THREE.Group();
  var rim = new THREE.Mesh(new THREE.SphereGeometry(rad * RIM_K, 16, 12),
            new THREE.MeshBasicMaterial({color: HALO_C, side: THREE.BackSide}));
  var m = new THREE.Mesh(new THREE.SphereGeometry(rad, 16, 12), new THREE.MeshBasicMaterial({color: color}));
  g.add(rim); g.add(m);
  g.position.copy(toVec(lat, lon, markRadius(rad)));
  scene.add(g);
  return g;
}

function buildWindowStarts() {
  var i, k, n = D.track.min.length;
  for (i = 0; i < D.windows.length; i++) {
    var w = D.windows[i], best = -1;
    for (k = 0; k < n; k++) { if (D.track.min[k] >= w.a) { best = k; break; } }
    if (best < 0) continue;
    /* Окно-кандидат, начинающееся там же, где рекомендация, второй раз не подписываем:
       две плашки в одной точке нечитаемы, а метка рекомендации крупнее и стоит первой. */
    if (D.rec && w.a === D.rec.a) continue;
    dot(D.track.lat[best], D.track.lon[best], R_WIN_MARK, hex(w.color));
    label("окно " + w.n + " · " + w.label, w.color, toVec(D.track.lat[best], D.track.lon[best], 1.13));
  }
}

/* Положения станции на начало и на конец рекомендованного окна — подписанными метками.
   Обе точки уже сохранены в трассе при прореживании (см. _thin), подбирать ближайшую не надо. */
function buildRecMarks() {
  if (!D.rec) return;
  var ends = [[D.rec.i0, "начало окна", D.rec.start], [D.rec.i1, "конец окна", D.rec.end]], i, k;
  for (i = 0; i < ends.length; i++) {
    k = ends[i][0];
    dot(D.track.lat[k], D.track.lon[k], R_REC_MARK, hex(D.rec.color));
    label(ends[i][1] + " · " + ends[i][2] + " UTC", D.rec.color, toVec(D.track.lat[k], D.track.lon[k], 1.13));
  }
}

function two(v) { return (v < 10 ? "0" : "") + v; }

/* Время метки «дд.мм чч:мм»: момент начала трассы плюс минута точки. Поля берутся из UTC явно —
   toLocaleString отдал бы часовой пояс зрителя, а на экране всюду UTC. */
function utcLabel(minFromT0) {
  var d = new Date(Date.parse(D.rec.t0_iso) + minFromT0 * 60000);
  return two(d.getUTCDate()) + "." + two(d.getUTCMonth() + 1) + " " +
         two(d.getUTCHours()) + ":" + two(d.getUTCMinutes());
}

/* Разряды тысяч: «24 000». Пробел обычный, а панель не переносит строку (white-space:nowrap):
   неразрывный пробел — знак вне печатного диапазона, в разметке его быть не должно.
   |B| неотрицателен по построению (модуль вектора), поэтому знак не разбирается. */
function thousands(v) {
  var s = String(v), out = "", i, c = 0;
  for (i = s.length - 1; i >= 0; i--) {
    out = s.charAt(i) + out; c++;
    if (c % 3 === 0 && i > 0) out = " " + out;
  }
  return out;
}

/* Бегунок ведёт метку по ПОКАЗАННЫМ точкам трассы внутри окна: значение бегунка — номер точки,
   считая от начала окна. Промежуточных положений не выдумываем — метка стоит там, где есть
   посчитанная точка. Всё считается в браузере, к серверу обращений нет. */
function setSlide(j) {
  var k = D.rec.i0 + j, b = D.rec.B[j];
  recMark.position.copy(toVec(D.track.lat[k], D.track.lon[k], markRadius(R_SLIDE_MARK)));
  document.getElementById("tlv").innerHTML =
    utcLabel(D.track.min[k]) + " UTC · |B| " +
    (b === null ? "нет значения в этой точке" : thousands(b) + " нТл") +
    (D.track.saa[k] ? " · в аномалии" : "");
}

function buildSlider() {
  if (!D.rec) return;
  var sl = document.getElementById("tlr");
  document.getElementById("tl").style.display = "block";
  sl.min = "0";
  sl.max = String(D.rec.i1 - D.rec.i0);
  sl.value = "0";
  recMark = dot(D.track.lat[D.rec.i0], D.track.lon[D.rec.i0], R_SLIDE_MARK, hex(D.rec.color));
  sl.addEventListener("input", function () { setSlide(parseInt(sl.value, 10)); });
  setSlide(0);
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

/* Строка под сценой — одна, и в ней только то, чего нет ни в легенде, ни в подписи под глобусом:
   сколько точек показано, с каким шагом, на какой высоте посчитано поле и что с текстурой.
   Прежде здесь стоял абзац в пять строк, повторявший подпись под рисунком слово в слово; на
   экране он читался как стена текста поверх сцены. Оговорка «это показ уже посчитанного» никуда
   не делась — она стоит в подписи под глобусом (app/globe.caption), где ей и место. */
function status() {
  document.getElementById("stat").innerHTML =
    "Точек " + D.n_shown + " из " + D.n_full + " · шаг " + D.step_min + " мин · высота " +
    String(D.alt_km).replace(".", ",") + " км · " + texInfo;
}

function bind() {
  var el = renderer.domElement;
  function grab(x, y) {
    dragging = true; px = x; py = y; idle = 0;
    vLon = 0; vLat = 0;                       /* захват гасит инерцию: шар слушается руки сразу */
    lastMove = performance.now();
  }
  function move(x, y) {
    if (!dragging) return;
    var dLon = -(x - px) * 0.005, dLat = (y - py) * 0.005;
    camLon += dLon;
    camLat = Math.max(-1.4, Math.min(1.4, camLat + dLat));
    var now = performance.now(), dt = Math.max(0.008, (now - lastMove) / 1000);
    vLon = Math.max(-V_MAX, Math.min(V_MAX, dLon / dt));
    vLat = Math.max(-V_MAX, Math.min(V_MAX, dLat / dt));
    px = x; py = y; idle = 0; lastMove = now;
  }
  el.addEventListener("mousedown", function (e) { grab(e.clientX, e.clientY); });
  window.addEventListener("mouseup", function () { dragging = false; });
  window.addEventListener("mousemove", function (e) { move(e.clientX, e.clientY); });
  el.addEventListener("touchstart", function (e) {
    if (e.touches.length === 1) { grab(e.touches[0].clientX, e.touches[0].clientY); }
  }, {passive: true});
  window.addEventListener("touchend", function () { dragging = false; });
  el.addEventListener("touchmove", function (e) {
    if (dragging && e.touches.length === 1) { e.preventDefault(); move(e.touches[0].clientX, e.touches[0].clientY); }
  }, {passive: false});
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
  if (!dragging && (Math.abs(vLon) > V_STOP || Math.abs(vLat) > V_STOP)) {
    /* докат по инерции; пока он идёт, отсчёт покоя сброшен — автоповорот не вмешивается */
    camLon += vLon * dt;
    camLat = Math.max(-1.4, Math.min(1.4, camLat + vLat * dt));
    var k = Math.exp(-DAMP * dt);
    vLon *= k; vLat *= k;
    idle = 0;
  }
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
  renderer.setClearColor(0x0e1117, 1);
  renderer.setSize(host.clientWidth, host.clientHeight);
  host.appendChild(renderer.domElement);
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(42, host.clientWidth / Math.max(host.clientHeight, 1), 0.05, 100);
  /* Цвет шара до загрузки текстуры — тёмно-серый: на тёмном фоне светлый шар вспыхивал белым
     кругом на те полсекунды, пока идёт текстура, и это читалось как поломка. */
  globe = new THREE.Mesh(new THREE.SphereGeometry(1, 96, 64), new THREE.MeshBasicMaterial({color: 0x39424f}));
  scene.add(globe);
  buildAtmosphere();
  legend();
  buildSaa();
  buildTrack();
  buildWindowStarts();
  buildRecMarks();
  buildSlider();
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
