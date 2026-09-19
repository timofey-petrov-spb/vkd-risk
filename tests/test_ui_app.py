# -*- coding: utf-8 -*-
"""Проверки экрана без браузера (AppTest) и вспомогательных функций оформления.

Что проверяется:
  * три режима × два уровня проходят без исключений (О5);
  * сжатие периода поиска ниже текущих сдвигов не останавливает экран (U1);
  * одинаковые сдвиги разводятся, вердикт остаётся на месте (U1);
  * статусы источников на оперативном уровне без имён отказов, путей, хешей и записей (U2);
  * над сырыми записями стоит подпись «на языке источника» (U3);
  * правая ось GOES размечена своими метками, степени печатаются надстрочными (U4);
  * на оперативном уровне нет идентификаторов записей в таблице событий (U5);
  * «исключён», «отказ», стресс-сценарий и три окна считаются без исключений;
  * S1: «условие поставлено» печатается только там, где условия были; дроби с запятой;
  * S2: каждый пресет выставляет режим, дату, час и окна и считается без исключений;
  * S3: вкладка «Методика» есть, формулы поднимаются, кириллицы внутри формул нет;
  * S4: на экране нет эмодзи и градиентных заливок;
  * S5: приборная полоса в две строки, давность печатается только в ней;
  * S6: на главном экране не больше двух графиков, панель инструментов Plotly скрыта, легенда ленты ≤ 4 строк;
  * S8: новые ключи снимка читаются через .get и их отсутствие экран не роняет;
  * седьмой круг: верхний ряд ленты — накопленные минуты в аномалии по окнам, ряд событий
    появляется только при событиях, подпись отметки времени печатается один раз, ось потока
    строится по данным, область аномалии на карте — сглаженный контур с указанной высотой.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import plotly.graph_objects as go
import pytest
from streamlit.testing.v1 import AppTest

from app.ui import (METHOD_BLOCKS, age_ru, dedup_clauses, dt_ru, fmt, formula_ref, frac_ru, nbsp_thousands, panel,
                    spread_offsets, status_ru, sup, verification_ru)
from app.viz import GOES_TICKVALS, PLOTLY_CONFIG, timeline

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app', 'main.py')
TIMEOUT = 300
MODES = ['Текущая обстановка', 'Исторический разбор', 'Прогноз из прошлого']
# Бюджет видимой (без клика) части блока вердикта. Был 650. Девятый круг добавил на поверхность
# две строки, каждая из которых обязательна и ни одна не повторяет другую:
#   1) строку устойчивости выбора (находка «экран» №7) — иначе заголовок «Есть предпочтительное
#      окно» стоит увереннее расчёта, а фраза о неустойчивости лежит под свёрткой;
#   2) строку «Что дальше» (сквозное замечание аналитика) — что проверить, до какого момента
#      действует условие, когда пересчитать; без неё сервис заканчивается вердиктом и молчит.
# Третья строка пришла со слиянием восьмого круга — «Область вывода» (на какой доле окна сделано
# сравнение и чего оно не означает), она тоже не в свёртке ни на одном уровне.
# Измерено на слитой ветке: пресеты «Сейчас» 767, «Гэннон» 1157, «Тихая дата» 992; режимы без
# пресета 700…1000. Из 1157 у «Гэннона» 415 занимает перечень записей единственного условия
# (два уведомления DONKI, каждое со своим временем публикации и своим Kp — сводить нельзя)
# и около 200 — объявленная область вывода.
# Порог стоит как защита от нового разрастания, а не как достигнутая цель.
VERDICT_BUDGET = 1250


def run_app(mode: str | None = None, pro: bool = False) -> AppTest:
    """Один прогон экрана: режим и уровень переключаются как пользователь, по одному действию."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    if mode and mode != MODES[0]:
        at.sidebar.radio('mode').set_value(mode).run()
    if pro:
        at.sidebar.radio('level').set_value('Профессиональный').run()
    return at


def texts(at: AppTest, drop_urls: bool = False) -> str:
    """Весь видимый текст экрана одной строкой — для поиска обрывков кода.
    drop_urls убирает адреса первоисточников: имя файла у NOAA своё, мы его не придумываем."""
    parts = [e.value for e in at.markdown] + [e.value for e in at.caption] + [e.value for e in at.warning] \
        + [e.value for e in at.error] + [e.value for e in at.info] + [str(e.value) for e in at.subheader]
    out = '\n'.join(str(p) for p in parts)
    return re.sub(r'\(https?://[^)]*\)', '()', out) if drop_urls else out


def source_rows(at: AppTest) -> list[dict]:
    """Строки таблицы состояния источников вкладки «Данные» (не реестра и не таблицы порогов)."""
    for d in at.dataframe:
        rows = d.value
        rows = rows.to_dict('records') if hasattr(rows, 'to_dict') else list(rows)
        if rows and 'источник' in rows[0] and 'статус' in rows[0]:
            return rows
    return []


def main_charts(at: AppTest) -> int:
    """Сколько графиков на главном экране: всего минус те, что лежат во вкладках."""
    return len(at.get('plotly_chart')) - sum(len(t.get('plotly_chart')) for t in at.tabs)


# ----------------------------------------------------------------- оформление чисел (U4)
def test_fmt_stepen_nadstrochnymi():
    assert fmt(3.36e6) == '3,36·10⁶'
    assert fmt(5.64e-7) == '5,64·10⁻⁷'
    assert '^' not in fmt(1.27e6)
    assert fmt(77.0, 'мин') == '77 мин'
    assert fmt(None) == '—'


def test_sup_i_frac_ru():
    assert sup('3,36·10^6') == '3,36·10⁶'
    assert frac_ru('давность 1.5 ч') == 'давность 1,5 ч'
    assert frac_ru('контроль 5,609728e-7 на 400 км') == 'контроль 5,609728·10⁻⁷ на 400 км'
    # даты и номера версий не трогаем
    assert frac_ru('архив DONKI 30.04.2024 — 30.06.2024') == 'архив DONKI 30.04.2024 — 30.06.2024'
    assert frac_ru('окно 10.05 22:00 — 11.05 00:00 UTC') == 'окно 10.05 22:00 — 11.05 00:00 UTC'
    assert frac_ru('CONTRACT.md v3.1 раздел 4') == 'CONTRACT.md v3.1 раздел 4'


# ----------------------------------------------------------------- разведение сдвигов (U1)
def test_spread_offsets_razvodit_na_shag():
    assert spread_offsets([120, 120], 120) == [90, 120]
    assert spread_offsets([120, 120, 120], 120) == [60, 90, 120]
    assert spread_offsets([0, 240], 360) == [0, 240]          # различные сдвиги не трогаются
    assert spread_offsets([60, 60], 60) == [30, 60]
    assert len(set(spread_offsets([0, 0, 0], 60))) == 3


def test_dedup_clauses_povtor_odin_raz():
    t = 'траектория: OEM NASA/JSC, создан 08.05; поле IGRF-13; траектория: OEM NASA/JSC, создан 08.05; поле IGRF-13'
    assert dedup_clauses(t) == 'траектория: OEM NASA/JSC, создан 08.05; поле IGRF-13'
    assert dedup_clauses('а; б; в') == 'а; б; в'


# ----------------------------------------------------------------- перевод статусов (U2)
@pytest.mark.parametrize('raw, must_have, must_not', [
    ('получено живьём, HTTP 200', 'получено живьём', 'HTTP'),
    ('не ответили: celestrak.org: ReadTimeout', 'сервер не ответил за 6 с', 'ReadTimeout'),
    ('отбор по времени публикации (experiments.stub_history)', 'отбор по времени публикации', 'experiments'),
    ('прил. А; файл data/ost1044_belts/A_2_1.csv, sha256 59e573afafa8…', 'прил. А', 'sha256'),
    ('разбор: Kp из архив GFZ, запись gfz_kp_archive#2024-05-10T09:00Z', 'Kp из архива GFZ', 'gfz_kp_archive'),
    # S7: дата источника печатается в виде экрана — «дд.мм.гггг чч:мм», а не как в ответе службы
    ('выпуск 202405100030three_day_forecast от 2024-05-10 00:30Z', 'выпуск от 10.05.2024 00:30', 'forecast'),
    ('ответ не разбирается (JSONDecodeError: нет значения)', 'ответ источника не разобран', 'JSONDecodeError'),
])
def test_status_ru_operativnyy(raw, must_have, must_not):
    out = status_ru(raw)
    assert must_have in out
    assert must_not not in out


def test_status_ru_professionalnyy_ostavlyaet_proishozhdenie():
    raw = 'отбор по времени публикации (experiments.stub_history); sha256 59e573afafa8…'
    assert 'experiments.stub_history' in status_ru(raw, pro=True)
    assert 'sha256' in status_ru(raw, pro=True)
    # но число приводится к единому виду и на профессиональном уровне
    assert status_ru('давность 1.5 ч', pro=True) == 'давность 1,5 ч'


# ----------------------------------------------------------------- графики (U4, U5)
def _traj(t0, n=360, saa=range(60, 120)):
    """Трасса шагом 1 мин: точки с номерами из saa — в аномалии."""
    return [SimpleNamespace(t_utc=t0 + timedelta(minutes=i), lat_deg=0.0, lon_deg=float(i % 180) - 90.0,
                            alt_km=420.0, B_nT=30000.0 - (5000.0 if i in saa else 0.0), in_saa=(i in saa))
            for i in range(n + 1)]


def _win(t0, offset_min, duration_min=180):
    return SimpleNamespace(start_utc=t0 + timedelta(minutes=offset_min), duration_min=duration_min)


def _axes(fig) -> dict:
    """Оси рисунка: только те, что видны. Скрытая ось подписи не требует и места не занимает."""
    lay = fig.layout.to_plotly_json()
    return {k: v for k, v in lay.items() if k.startswith(('xaxis', 'yaxis')) and v.get('visible') is not False}


def _ann(fig) -> list:
    return [a.get('text') for a in (fig.layout.to_plotly_json().get('annotations') or [])]


def test_timeline_metki_pravoy_osi_goes():
    """U4: правая ось потока размечена своими метками, а её диапазон берётся по данным,
    а не фиксируется до 1000 pfu: при потоке в доли pfu линия ложилась на дно."""
    t0 = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    obs = [(t0 - timedelta(hours=h), 10.0 ** (h % 3)) for h in range(6, 0, -1)]      # максимум 100 pfu
    fig = timeline([], [], 24000.0, t0, 360, None, None, [], [], 'live', kp_obs=[], goes_obs=obs, search_min=360)
    goes_axis = [a for a in _axes(fig).values() if a.get('type') == 'log']
    assert goes_axis, 'правая ось GOES должна быть логарифмической'
    assert set(goes_axis[0]['tickvals']) <= set(GOES_TICKVALS)
    assert '10 (S1)' in list(goes_axis[0]['ticktext']), goes_axis[0]['ticktext']
    lo, hi = goes_axis[0]['range']
    assert hi < 3.0, ('диапазон оси задаётся данными, а не потолком 1000 pfu', hi)
    assert 10.0 ** lo <= 1.0 and 10.0 ** hi >= 100.0, (lo, hi)


def test_timeline_potok_nizhe_S1_bez_pustoy_osi():
    """Поток ниже первого порога шкалы S не получает целой оси: он печатается строкой
    под графиком с числом и единицей (иначе линия лежит на дне оси до 1000 pfu)."""
    t0 = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    obs = [(t0 - timedelta(hours=h), 0.26) for h in range(6, 0, -1)]
    fig = timeline([], [], 24000.0, t0, 360, None, None, [], [], 'live', kp_obs=[], goes_obs=obs, search_min=360)
    assert not [a for a in _axes(fig).values() if a.get('type') == 'log'], 'пустой логарифмической оси быть не должно'
    line = [t for t in _ann(fig) if 'поток GOES' in (t or '')]
    assert len(line) == 1, _ann(fig)
    assert '0,26 pfu' in line[0] and 'S1' in line[0], line[0]


def test_timeline_ryad_sobytiy_tolko_kogda_sobytiya_est():
    """Пустой ряд событий занимал пятую часть высоты. Событий нет — ряда нет, есть строка подписи."""
    t0 = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    ev = [SimpleNamespace(kind_of_event='SEP', event_id='x1', note='протонное событие', is_simulated=False,
                          start_utc=t0 + timedelta(hours=1), valid_from_utc=None)]
    with_ev = timeline([], [], 24000.0, t0, 360, None, None, ev, [], 'live', search_min=360)
    без = timeline([], [], 24000.0, t0, 360, None, None, [], [], 'live', search_min=360)
    assert 'событие, тип' in [(v.get('title') or {}).get('text') for v in _axes(with_ev).values()]
    assert 'событие, тип' not in [(v.get('title') or {}).get('text') for v in _axes(без).values()]
    assert len(без.layout.to_plotly_json()['yaxis']['domain']) == 2
    assert без.layout.height < with_ev.layout.height
    assert [t for t in _ann(без) if 'событий и прогнозов на горизонте нет' in (t or '')], _ann(без)


def test_timeline_podpis_kontsa_poiska_odin_raz():
    """Подпись «конец периода поиска начала» печаталась дважды — по разу на ряд со своей осью —
    и налезала на метки правой оси. Теперь линия и подпись ставятся вручную, ровно один раз."""
    t0 = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    obs = [(t0 - timedelta(hours=h), 10.0 ** (h % 3)) for h in range(6, 0, -1)]
    kp = [(t0 - timedelta(hours=h + 3), t0 - timedelta(hours=h), 3.0) for h in range(6, 0, -3)]
    fig = timeline([], [], 24000.0, t0, 360, None, None, [], [], 'live', kp_obs=kp, goes_obs=obs, search_min=360)
    assert len([t for t in _ann(fig) if 'конец периода поиска начала' in (t or '')]) == 1, _ann(fig)
    assert len([t for t in _ann(fig) if t == 'сейчас']) == 1, _ann(fig)


def test_timeline_odna_os_vremeni_i_edinitsy_u_kazhdoy_osi():
    """S6: у ленты заголовок «что и откуда», легенда не длиннее четырёх строк, ось времени
    одна и та же у всех рядов, и у каждой видимой оси есть подпись с единицей."""
    t0 = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    obs = [(t0 - timedelta(hours=h), 10.0 ** (h % 3)) for h in range(6, 0, -1)]
    kp = [(t0 - timedelta(hours=h + 3), t0 - timedelta(hours=h), 3.0) for h in range(6, 0, -3)]
    ev = [SimpleNamespace(kind_of_event='SEP', event_id='x1', note='протонное событие', is_simulated=False,
                          start_utc=t0 + timedelta(hours=1), valid_from_utc=None)]
    fig = timeline(_traj(t0), [_win(t0, 60)], 24000.0, t0, 360, None, None, ev, [], 'live',
                   kp_obs=kp, goes_obs=obs, search_min=360)
    assert fig.layout.title.text and 'наш расчёт' in fig.layout.title.text
    legend = [tr.name for tr in fig.data if tr.showlegend is not False]
    assert len(legend) <= 4, legend
    axes = _axes(fig)
    titles = [(v.get('title') or {}).get('text') for v in axes.values()]
    assert 'время, UTC' in titles, titles
    assert 'накоплено в аномалии, мин' in titles, titles
    assert 'Kp (безразмерный)' in titles, titles
    assert 'поток ≥10 МэВ, pfu' in titles, titles
    ranges = [tuple(v['range']) for k, v in axes.items() if k.startswith('xaxis')]
    assert len(set(ranges)) == 1, ranges
    # единица есть у каждой видимой оси: либо в подписи, либо это подписанная категория
    for k, v in axes.items():
        if not k.startswith('yaxis'):
            continue
        t = (v.get('title') or {}).get('text') or ''
        assert t, ('видимая ось без подписи', k)
        assert any(u in t for u in ('мин', 'pfu', 'безразмерный', 'тип', 'нТл')), (k, t)


def test_timeline_ekspozitsiya_schitaet_minuty_v_anomalii():
    """Верхний ряд отвечает на вопрос «почему это окно лучше»: ступень накопленных минут
    в аномалии от начала окна. Окно без пролётов даёт ровный ноль, а не пустое место."""
    from app.viz import window_exposure
    t0 = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    traj = _traj(t0, n=360, saa=range(60, 120))            # 60 минут в аномалии, с 60-й по 119-ю
    w_hit, w_miss = _win(t0, 0, 180), _win(t0, 180, 180)
    xs, ys, total = window_exposure(traj, w_hit)
    assert total == 60.0, total
    assert ys[0] == 0.0 and ys == sorted(ys), ys[:5]
    assert xs[-1] == w_hit.start_utc + timedelta(minutes=180)
    assert window_exposure(traj, w_miss)[2] == 0.0
    fig = timeline(traj, [w_hit, w_miss], 24000.0, t0, 360, None, None, [], [], 'live', search_min=360)
    assert [t for t in _ann(fig) if 'экспозиция считается' in (t or '')] == [], 'прошлого на ленте нет — нет и подписи'
    texts = [tuple(tr.text) for tr in fig.data if tr.text and tr.mode and 'text' in tr.mode]
    assert ('окно 1: 60 мин',) in texts, texts
    assert ('окно 2: 0 мин',) in texts, texts


def test_timeline_proshloe_podpisano_a_ne_prosto_pusto():
    """Левый край ленты — начало показа наблюдений, и верхний ряд там пуст по определению:
    экспозиция считается только внутри окон. Это подписано, иначе читается как потеря данных."""
    t0 = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    kp = [(t0 - timedelta(hours=h + 3), t0 - timedelta(hours=h), 3.0) for h in range(12, 0, -3)]
    fig = timeline(_traj(t0), [_win(t0, 60)], 24000.0, t0, 360, None, None, [], [], 'live',
                   kp_obs=kp, search_min=360)
    assert [t for t in _ann(fig) if 'экспозиция считается' in (t or '')], _ann(fig)
    x_from = _axes(fig)['xaxis']['range'][0]
    assert x_from <= t0 - timedelta(hours=11), x_from


def test_karta_anomalii_sglazhennym_konturom_s_vysotoy():
    """Плоская карта: область аномалии — замкнутый контур, а не квадраты сетки 4°,
    и высота, на которой он построен, стоит и в подписи слоя, и в заголовке."""
    from app.viz import ground_track, saa_contour
    t0 = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    polys = saa_contour(420.0, 24000.0, t0)
    assert polys, 'область аномалии на 420 км при пороге 24 000 нТл существует'
    lon, lat = polys[0]
    assert len(lon) > 40 and lon[0] == lon[-1] and lat[0] == lat[-1], (len(lon), lon[0], lon[-1])
    assert -130.0 < min(lon) and max(lon) < 60.0 and min(lat) > -70.0, (min(lon), max(lon), min(lat))
    fig = ground_track(_traj(t0, n=60, saa=()), [_win(t0, 0, 60)], 24000.0, t0)
    saa = [tr for tr in fig.data if tr.name and tr.name.startswith('аномалия')]
    assert len(saa) == 1 and saa[0].fill == 'toself' and saa[0].mode == 'lines', saa[0].mode
    assert '420 км' in saa[0].name, saa[0].name
    assert '420 км' in fig.layout.title.text, fig.layout.title.text


def test_podpisi_pod_grafikami_opisyvayut_tekushchie_ryady():
    """Подпись под лентой не обещает ряда, которого может не быть, и не называет верхний
    ряд полем |B|: текст живёт в app/ui.py рядом с описанием рисунка."""
    from app.ui import map_caption, timeline_caption
    for mode in ('live', 'history_review', 'history_forecast'):
        for pro in (False, True):
            t = timeline_caption(mode, pro)
            assert 'Ряд 3' not in t and 'Ряд 1' not in t, t
            assert '|B| на трассе, наш расчёт по IGRF:' not in t
            assert 'в аномалии' in t and 'окн' in t, t
    assert 'когда они есть на горизонте' in timeline_caption('live')
    assert '2° по долготе' in map_caption(True)
    assert 'сетке 4°' not in map_caption(True)


def test_plotly_panel_instrumentov_skryta():
    """S6: панель инструментов Plotly скрыта — на защите она только мешает."""
    assert PLOTLY_CONFIG['displayModeBar'] is False


def test_grafiki_pishut_drobi_s_zapyatoy_i_razryady_tysyach():
    """Пятый круг, §9.8: своими подписями были закрыты только логарифмические оси. Деления
    остальных осей и всплывающие подписи рисует Plotly, и по умолчанию это «7.67» и «24000»
    рядом с «7,67» и «24 000» в тексте того же экрана. Разделители задаются один раз, в стиле."""
    from app.obs import observations_figure
    from app.viz import SEPARATORS, ground_track, style
    assert SEPARATORS == ',\u202f', repr(SEPARATORS)          # запятая и узкий неразрывный пробел
    t0 = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    obs = [(t0 - timedelta(hours=h), 10.0 ** (h % 3)) for h in range(6, 0, -1)]
    fig = timeline([], [], 24000.0, t0, 360, None, None, [], [], 'live', kp_obs=[], goes_obs=obs, search_min=360)
    assert fig.layout.separators == SEPARATORS
    assert style(go.Figure(), 200).layout.separators == SEPARATORS
    of = observations_figure([t for t, _ in obs], [v for _, v in obs], [], [], t0, 'GOES', 'Kp')
    assert of.layout.separators == SEPARATORS
    # карта трассы стиль задаёт сама — у неё те же разделители и время без машинной «Z»
    pt = SimpleNamespace(lon_deg=10.0, lat_deg=5.0, alt_km=420.0, in_saa=False, B_nT=30000.0, t_utc=t0)
    win = SimpleNamespace(start_utc=t0, duration_min=360)
    gt = ground_track([pt], [win], 24000.0, t0)
    assert gt.layout.separators == SEPARATORS
    names = [tr.name for tr in gt.data if tr.name]
    assert any(n.startswith('окно 1: 19.09 12:00') for n in names), names
    assert not any(re.search(r'\d\d:\d\dZ', n) for n in names), names
    assert 'UTC' in gt.layout.title.text, gt.layout.title.text


# ----------------------------------------------------------------- экран: три режима × два уровня
@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('pro', [False, True])
def test_ekran_rezhim_i_uroven_bez_isklyucheniy(mode, pro):
    at = run_app(mode, pro)
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    assert any('class="verdict' in m.value for m in at.markdown), 'вердикт должен быть на экране'


def test_operativnyy_uroven_bez_obryvkov_koda():
    """О5: на оперативном уровне нет путей модулей, хешей и имён отказов."""
    at = run_app(MODES[1], pro=False)
    assert not at.exception, at.exception
    body = texts(at, drop_urls=True) + '\n' + '\n'.join(str(r.get('статус', '')) for r in source_rows(at))
    for bad in ('experiments.', 'vkd.history', 'sha256', 'SHA-256', 'HTTP 200', 'ReadTimeout',
                'scripts/replay_example.py', 'three_day_forecast', 'README',
                'CONTRACT.md', 'config/settings.toml', 'nasa_jsc_oem', 'donki_msg#',
                'sep_valid_hours', 'event_valid_hours'):
        assert bad not in body, bad
    assert 'из архив ' not in body, 'падеж: «из архива»'


def test_professionalnyy_uroven_pokazyvaet_proishozhdenie():
    """Происхождение на профессиональном уровне: хеш таблицы стандарта — в статусе источника,
    имена программных слоёв — отдельной строкой «Слои: …», а не внутри текста статуса
    (в тексте, который читает аналитик, идентификаторов кода быть не должно, О5)."""
    at = run_app(MODES[1], pro=True)
    assert not at.exception, at.exception
    rows = '\n'.join(str(r.get('статус', '')) for r in source_rows(at))
    assert 'sha256' in rows
    assert 'vkd.history' not in rows and 'vkd.sources' not in rows, rows
    caps = '\n'.join(c.value for c in at.caption)
    assert 'Слои:' in caps and 'vkd.history' in caps and 'vkd.sources' in caps, caps


def test_syraya_zapis_podpisana_yazykom_istochnika():
    """U3: над сырыми записями NOAA/DONKI стоит подпись «на языке источника»."""
    at = run_app(MODES[1], pro=True)
    caps = ' '.join(c.value for c in at.caption)
    assert 'на языке источника' in caps


def test_tablica_sobytiy_bez_identifikatorov_na_operativnom():
    at_op, at_pro = run_app(MODES[1], pro=False), run_app(MODES[1], pro=True)
    def cols(at):
        out = set()
        for d in at.dataframe:
            rows = d.value
            rows = rows.to_dict('records') if hasattr(rows, 'to_dict') else list(rows)
            if rows and 'тип' in rows[0] and 'происхождение' in rows[0]:
                out |= set(rows[0])
        return out
    assert 'запись' not in cols(at_op)
    assert 'запись' in cols(at_pro)


# ----------------------------------------------------------------- отказ и исключение источника
def test_goes_isklyuchen_i_otkaz():
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.selectbox('dis_goes').set_value('исключён: нет данных').run()
    assert not at.exception, at.exception
    body = texts(at)
    assert 'исключён пользователем' in body
    assert 'безопас' not in body.lower()
    at.sidebar.selectbox('dis_goes').set_value('отказ: только кеш').run()
    assert not at.exception, at.exception
    assert any('class="verdict' in m.value for m in at.markdown)


def test_kp_isklyuchen_v_istorii():
    at = run_app(MODES[1])
    at.sidebar.checkbox('kp_off_hist').set_value(True).run()
    assert not at.exception, at.exception
    assert 'исключён пользователем' in texts(at)


# ----------------------------------------------------------------- сценарий и три окна
def test_stress_scenariy():
    at = run_app(MODES[1])
    at.sidebar.checkbox('sc_sep_on').set_value(True).run()
    at.sidebar.slider('sc_delay').set_value(60).run()
    assert not at.exception, at.exception
    body = texts(at)
    assert 'Изменение плана' in body
    assert 'сценарий' in body.lower()
    # плашка сценария стоит НА ВИДУ, а не в свёртке блока: она говорит, что числа блока получены
    # на подставленных условиях, а не на данных источников (пятый круг, сокращение блока вердикта)
    assert 'сценарий «что если»' in verdict_visible(at).lower(), verdict_visible(at)


def test_tri_okna():
    # Одиннадцатый круг, раздел 3.1: выбор числа окон и ползунки их сдвига — это РУЧНОЙ разбор,
    # и они переехали из боковой панели в свёрнутый раздел «Разобрать конкретные окна» главной
    # области. Проверка та же (три окна считаются и дают три карточки), сменилось только место
    # элементов: `at.sidebar.radio` → `at.radio`, `at.sidebar.slider` → `at.slider`.
    at = run_app(MODES[1])
    at.radio('n_windows').set_value(3).run()
    assert not at.exception, at.exception
    assert at.slider('w2') is not None
    cards = [m.value for m in at.markdown if 'class="wcard' in m.value]
    assert len(cards) == 3


# ----------------------------------------------------------------- U1: сжатие периода поиска
def test_szhatie_perioda_ne_ostanavlivaet_ekran():
    # Одиннадцатый круг, раздел 3.1: срок поиска задаётся строкой задачи в ЧАСАХ («начать
    # в ближайшие 6 ч»), а ползунки сдвига живут в разделе ручного разбора. Проверка прежняя —
    # срок короче прежнего сдвига не останавливает экран, — сменились место и единица элемента.
    at = run_app(MODES[1])
    at.number_input('search_h').set_value(6).run()
    at.slider('w0').set_value(120).run()
    at.slider('w1').set_value(360).run()
    assert not at.error, [e.value for e in at.error]
    at.number_input('search_h').set_value(2).run()            # срок короче прежнего сдвига
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    assert any('class="verdict' in m.value for m in at.markdown), 'вердикт исчез — экран остановился'
    assert at.slider('w0').value != at.slider('w1').value
    assert at.slider('w1').value == 120
    assert any('сдвиги пересчитаны под период' in c.value for c in at.caption)
    assert at.tabs, 'вкладки должны остаться на экране'


def test_odinakovye_sdvigi_preduprezhdenie_bez_ostanovki():
    # Место ползунков и предупреждения — раздел «Разобрать конкретные окна» главной области
    # (одиннадцатый круг, раздел 3.1); смысл проверки не изменился.
    at = run_app(MODES[1])
    at.slider('w0').set_value(at.slider('w1').value).run()      # оба окна с одним началом
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    warn = [w.value for w in at.warning]
    assert any('одинаков' in w for w in warn), warn
    assert any('считаю по сдвигам' in w for w in warn), warn
    assert any('class="verdict' in m.value for m in at.markdown)


def test_publication_evidence_survives_record_list_cleanup():
    text = 'NASA DONKI, записи: публикация 05-09 13:54Z — 05-10 14:19Z'
    assert 'публикация 09.05 13:54 — 10.05 14:19' in status_ru(text)
    assert 'nasa_donki_notification' not in status_ru('NASA; записи: nasa_donki_notification:release:hash')
# ================================================================= S1: мелкие правки экрана
def test_verification_ru_bez_usloviy_ne_vryot():
    """S1: «условие поставлено в 12:00» печатается только там, где условия действительно были.

    Буква Z из голого времени снята пятым кругом (`dates_ru`): на оперативном уровне время
    печатается одним видом «чч:мм», а UTC названо один раз в шапке экрана."""
    raw = 'условие поставлено в 12:00Z; факт: максимум Kp 2.67, бури Kp ≥ 7 не было'
    assert verification_ru(raw, False).startswith('условий проверки на отсечку не ставилось; факт:')
    assert 'условие поставлено' not in verification_ru(raw, False)
    assert verification_ru(raw, True).startswith('условие поставлено в 12:00;'), verification_ru(raw, True)
    assert '2,67' in verification_ru(raw, True)          # дробь приводится к запятой в обоих случаях
    assert verification_ru('наблюдений Kp нет', False) == 'наблюдений Kp нет'      # чужую сводку не трогаем


def test_tihaya_data_ne_govorit_chto_uslovie_stavilos():
    """S1 на живом экране: тихая дата 25.06.2024 — ни одного условия и ни одной фразы «условие поставлено»."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.button('preset_quiet').click().run()
    assert not at.exception, at.exception
    body = texts(at)
    assert 'Условий проверки нет' in body
    assert 'условие поставлено' not in body
    assert 'условий проверки на отсечку не ставилось' in body


def test_chisla_na_ekrane_s_zapyatoy():
    """S1: на оперативном уровне нет десятичной точки в числе, ни «e+», ни «10^»."""
    at = run_app(MODES[1])
    body = re.sub(r'<style>.*?</style>', '', texts(at, drop_urls=True), flags=re.S)     # CSS не текст экрана
    bad = [m.group(0) for m in re.finditer(r'(?<![\d.A-Za-z])\d+\.\d+(?=\s*(?:[А-Яа-я%·)\],;]|$))', body)]
    assert not bad, bad
    assert 'e+' not in body
    assert '10^' not in body


def test_nbsp_thousands_dt_ru_age_ru():
    """S7: разряды тысяч узким неразрывным пробелом, дата «дд.мм чч:мм», давность в одних единицах."""
    assert nbsp_thousands(24000.0) == '24 000'
    assert nbsp_thousands(None) == '—'
    assert dt_ru(datetime(2024, 5, 10, 12, 0, tzinfo=timezone.utc)) == '10.05 12:00'
    assert dt_ru(datetime(2024, 5, 10, 12, 0, tzinfo=timezone.utc), with_utc=True) == '10.05 12:00 UTC'
    assert dt_ru(None) == '—'
    assert age_ru(92, 60) == 'давность 92 мин (предел 60 мин)'
    assert age_ru(600) == 'давность 10 ч'
    assert age_ru(None) == 'давность не определена'
    # единица стоит один раз в конце перечисления — разряды получают все числа, а не последнее:
    # в подписи таблицы 1 на профессиональном уровне стояло «22000/24000/26 000 нТл»
    from app.ui import screen_text
    assert screen_text('при порогах 22000/24000/26000 нТл').replace(' ', ' ') \
        == 'при порогах 22 000/24 000/26 000 нТл'
    assert screen_text('канал 12,5/30/50 МэВ') == 'канал 12,5/30/50 МэВ'      # не тысячи и не та единица


# ================================================================= S2: пресеты запроса
@pytest.mark.parametrize('key, mode_ru, date, hour, search, offsets', [
    ('gannon', 'Прогноз из прошлого', (2024, 5, 10), 12, 720, [0, 240]),
    ('quiet', 'Прогноз из прошлого', (2024, 6, 25), 12, 1440, [0, 480]),
])
def test_preset_vystavlyaet_zapros(key, mode_ru, date, hour, search, offsets):
    """S2: одно нажатие выставляет режим, дату, час и окна; экран считается без исключений."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.button('preset_' + key).click().run()
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    assert at.sidebar.radio('mode').value == mode_ru
    assert at.sidebar.date_input('hist_date').value == datetime(*date).date()
    assert at.sidebar.slider('hist_hour').value == hour
    # Срок пресета проверяется там, где он теперь стоит, — в строке задачи и в часах
    # (одиннадцатый круг, раздел 3.1); значение то же самое, 720 мин это 12 ч.
    assert at.number_input('search_h').value == search // 60
    for i, off in enumerate(offsets):
        assert at.slider('w%d' % i).value == off
    assert any('class="verdict' in m.value for m in at.markdown), 'вердикт должен быть на экране'


def test_preset_seychas_vozvrashchaet_v_tekushchiy_rezhim():
    """S2: пресет «Сейчас» возвращает экран в текущий режим после исторического."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.button('preset_gannon').click().run()
    assert at.sidebar.radio('mode').value == 'Прогноз из прошлого'
    at.sidebar.button('preset_now').click().run()
    assert not at.exception, at.exception
    assert at.sidebar.radio('mode').value == 'Текущая обстановка'
    assert at.number_input('search_h').value == 12          # 720 мин — это 12 ч строки задачи
    assert any('class="verdict' in m.value for m in at.markdown)


def test_preset_podpisan_tem_chto_pokazyvaet():
    """S2: под кнопками — одна строка о том, что этот пресет показывает."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.button('preset_gannon').click().run()
    caps = ' '.join(c.value for c in at.sidebar.caption)
    assert 'Буря Гэннон' in caps
    assert 'до 10.05 12:00 UTC' in caps


# ================================================================= S3: вкладка «Методика»
def test_vkladka_metodika_tretya_i_s_formulami():
    """S3: «Методика» стоит третьей, формулы поднимаются AppTest, кириллицы внутри формул нет."""
    at = run_app(MODES[1])
    assert not at.exception, at.exception
    labels = [t.label for t in at.tabs]
    # Девятый круг, находка «экран» №29: порядок вкладок — рабочий путь аналитика, а не порядок
    # разработчика. «Методика» (формулы) ушла с третьего места за «Наблюдения и прогнозы»:
    # пользователь дважды проходил мимо формул, прежде чем добирался до данных.
    # Одиннадцатый круг, раздел 3.0: вкладка «Карта» упразднена — глобус и запасная плоская карта
    # стоят блоком 4 ГЛАВНОГО экрана и обыгрывают рекомендацию. Ничего не потеряно: переключатель
    # вида и обе подписи те же, что были во вкладке, и их проверяет tests/test_globe.py.
    assert labels[:4] == ['Объяснения', 'Окна и факторы', 'Наблюдения и прогнозы', 'Методика'], labels
    assert 'Карта' not in labels, labels
    assert at.radio('map_view') is not None, 'переключатель вида карты должен остаться на экране'
    lat = [x.value for x in at.latex]
    assert len(lat) >= 9, lat
    for f in lat:
        assert not re.search(r'[А-Яа-яёЁ]', f), f


def test_metodika_kazhdaya_formula_s_istochnikom_i_ogranicheniem():
    """S3: у каждой формулы — стандарт с пунктом или таблицей и то, что НЕ учтено."""
    nos = [b['no'] for b in METHOD_BLOCKS]
    assert nos == sorted(nos) and len(set(nos)) == len(nos)
    for b in METHOD_BLOCKS:
        assert b['source'].strip() and b['limits'].strip() and b['symbols'].strip(), b['no']
        assert not re.search(r'[А-Яа-яёЁ]', b['latex']), b['no']
    src = ' '.join(b['source'] for b in METHOD_BLOCKS)
    for std in ('ОСТ 134-1044-2007', 'ECSS-E-ST-10-04C', 'Fraser-Smith', 'договор команды', 'ГОСТ 25645.215-85'):
        assert std in src, std


def test_kartochki_ssylayutsya_na_formuly_po_nomeram():
    """S3: карточка объяснения указывает номер формулы; сопоставление — по смыслу текста."""
    assert formula_ref('флюенс захваченных протонов ≥30 МэВ') == '(1) и (2)'
    assert formula_ref('минут в аномалии') == '(3)'
    assert formula_ref('ожидаемое число попаданий метеороидов') == '(5)–(7)'
    assert formula_ref('окна равнозначны, допуск') == '(8) и (9)'
    assert formula_ref('что-то постороннее') is None
    at = run_app(MODES[1])
    assert 'вкладка «Методика», формул' in texts(at)


def test_kartochka_obrezaniya_ssylaetsya_na_svoyu_formulu():
    """Пятый круг: карточки «минут доступности протонов ≥10 МэВ по обрезанию» и «≥100 МэВ»
    отсылали к формуле (3) — «L-оболочка и B/B₀», — а на формулу (4) «жёсткость геомагнитного
    обрезания» не ссылалась ни одна карточка. Имя фактора содержит слово «минут» (ключ формулы (3)),
    и оно стояло в списке ключей раньше, чем «обрезание».

    Тексты ниже — дословно с экрана (дамп AppTest режима «Сейчас»)."""
    name10 = 'минут доступности протонов ≥10 МэВ по обрезанию'
    rule10 = ('точки трассы с вертикальной жёсткостью обрезания (A3, центральный диполь) ниже 0,14 ГВ — '
              'жёсткости протона 10 МэВ; предположение о спектре: порог по жёсткости канала, без формы спектра')
    assert formula_ref(name10, rule10, '0 мин') == '(4)'
    assert formula_ref('минут доступности протонов ≥100 МэВ по обрезанию', rule10) == '(4)'
    assert formula_ref(rule10) == '(4)'                       # и по одному правилу — та же формула
    # соседние карточки от перестановки ключей не пострадали
    assert formula_ref('минут в аномалии', 'точки трассы с |B| ниже порога 24000 нТл') == '(3)'
    assert formula_ref('флюенс захваченных протонов ≥30 МэВ',
                       'ОСТ 134-1044-2007, прил. А, табл. А.2.1; интерполяция по L и B/B0') == '(1) и (2)'


@pytest.mark.parametrize('preset', ['now', 'gannon', 'quiet'])
def test_na_kazhduyu_formulu_metodiki_est_ssylka(preset):
    """Пятый круг: обязательный шаг жюри «открыть Методику по номеру формулы» обязан работать
    в обе стороны — карточка ведёт к своей формуле, и у каждой формулы вкладки есть место на
    экране, которое на неё ссылается.

    Ссылку даёт не только карточка «Объяснений»: на формулу (3) ссылается ещё подпись под
    величинами карточки окна, на (9) — строка о допуске, на (10) — блок норм. Поэтому ищем по
    всему профессиональному экрану: на нём видны все десять формул."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.button('preset_' + preset).click().run()
    at.sidebar.radio('level').set_value('Профессиональный').run()
    assert not at.exception, at.exception
    body = _body_bez_metodiki(at)
    refs = set()
    for grp in re.findall(r'формул[аы]?\s+((?:\(\d+\)(?:\s*(?:и|–|,)\s*)?)+)', body):
        refs.update(int(x) for x in re.findall(r'\((\d+)\)', grp))
    refs.update(range(5, 8) if '(5)–(7)' in body else ())     # диапазон метеороидов — тремя формулами
    missing = [b['no'] for b in METHOD_BLOCKS if b['no'] not in refs]
    assert not missing, ('на эти формулы «Методики» на экране никто не ссылается', missing, sorted(refs))


# ================================================================= S4: монотонный цвет, без эмодзи
EMOJI = re.compile('[\U0001F300-\U0001FAFF☀-➿️]')


@pytest.mark.parametrize('mode', MODES)
def test_bez_emodzi_i_gradientov(mode):
    """S4: ни эмодзи, ни градиентных заливок — на проекторе они дают грязь."""
    at = run_app(mode)
    assert not at.exception, at.exception
    body = '\n'.join([m.value for m in at.markdown] + [c.value for c in at.caption]
                     + [str(e.label) for e in at.expander])
    assert not EMOJI.findall(body), EMOJI.findall(body)
    assert 'linear-gradient' not in body


def test_uroven_preduprezhdeniya_slovami():
    """S4: заголовок карточки начинается словом КРИТИЧНО / ВНИМАНИЕ, а не значком."""
    at = run_app(MODES[1])
    labels = [str(e.label) for e in at.expander]
    assert any(l.startswith(('КРИТИЧНО', 'ВНИМАНИЕ', '—')) for l in labels), labels


def test_cvet_oznachaet_proishozhdenie():
    """S4: на экране объявлено, что цвет означает происхождение, а не «безопасно»."""
    body = texts(run_app(MODES[1]))
    assert 'синий — наш расчёт' in body and 'зелёный — наблюдение' in body
    assert 'янтарный — внешний прогноз' in body
    assert 'безопас' not in body.lower()


# ================================================================= S5: приборная полоса
def test_panel_dve_stroki():
    """S5: полоса состояния — строки фиксированной сетки, у ячейки метка, значение и подпись."""
    html = panel([[('Режим', 'Текущая обстановка', 'живые источники', 'calc')],
                  [('Kp (GFZ)', '2,33', 'давность 14 мин', 'obs')]])
    assert html.count('class="prow"') == 2
    assert html.count('class="cell k-') == 2
    assert 'class="cl"' in html and 'class="cv"' in html and 'class="cs"' in html


@pytest.mark.parametrize('mode', MODES)
def test_davnost_tolko_v_pribornoy_polose(mode):
    """S5: давность печатается в полосе состояния и в сводке источников, но не в карточках окон."""
    at = run_app(mode)
    assert not at.exception, at.exception
    strip_html = [m.value for m in at.markdown if 'class="panel"' in m.value]
    assert strip_html, 'приборная полоса должна быть на экране'
    assert strip_html[0].count('class="prow"') == 2
    for card in [m.value for m in at.markdown if 'class="wcard' in m.value]:
        assert 'давность' not in card, card


@pytest.mark.parametrize('mode', MODES)
def test_polosa_bez_identifikatorov_koda(mode):
    """S5: на оперативном уровне в полосе нет идентификаторов кода."""
    at = run_app(mode)
    strip_html = ' '.join(m.value for m in at.markdown if 'class="panel"' in m.value)
    for bad in ('experiments.', 'vkd.', 'sha256', 'noaa_swpc_goes', 'gfz_kp', 'None', 'strict'):
        assert bad not in strip_html, bad


# ================================================================= S6: графики
@pytest.mark.parametrize('mode', MODES)
def test_ne_bolshe_dvuh_grafikov_na_glavnom_ekrane(mode):
    """S6: на главном экране графиков не больше двух."""
    at = run_app(mode)
    assert not at.exception, at.exception
    assert main_charts(at) <= 2, main_charts(at)


def test_lenta_v_svyortke_na_operativnom_i_razvyornuta_na_professionalnom():
    """S6 (И6): «Картина по времени» доступна на обоих уровнях и НЕ свёрнута ни на одном.

    Было: на оперативном уровне лента лежала в свёртке, на профессиональном стояла заголовком.
    Стало (одиннадцатый круг, раздел 3.0 и замечание владельца о самом графике): главный экран
    занят ответом — рекомендацией, глобусом и лентой окон, — а «Картина по времени» переехала
    во вкладку «Наблюдения и прогнозы», где стоит первой и РАЗВЁРНУТОЙ на обоих уровнях.
    Разница уровней была в том, свёрнут график или нет; теперь он не свёрнут нигде, и проверка
    стала строже, а не слабее: ни на одном уровне он не спрятан.
    """
    for pro in (False, True):
        at = run_app(MODES[1], pro=pro)
        assert not at.exception, at.exception
        assert any('Картина по времени' in str(s.value) for s in at.subheader), [str(s.value) for s in at.subheader]
        assert not any('Картина по времени' in str(e.label) for e in at.expander), \
            'график не должен быть спрятан в свёртку ни на одном уровне'
        tab = next(t for t in at.tabs if t.label == 'Наблюдения и прогнозы')
        assert any('Картина по времени' in str(s.value) for s in tab.get('subheader')), 'график ушёл не в ту вкладку'
        assert tab.get('plotly_chart'), 'во вкладке нет ни одного графика'


def test_okna_i_faktory_tablitsa_bez_grafika():
    """S6: «Окна и факторы» — таблица «как в статье» с группами строк и подписью, без столбцов с двумя осями."""
    at = run_app(MODES[1])
    rows = []
    for d in at.dataframe:
        vals = d.value
        vals = vals.to_dict('records') if hasattr(vals, 'to_dict') else list(vals)
        if vals and 'показатель' in vals[0]:
            rows = vals
            break
    assert rows, 'таблица сравнения окон должна быть на экране'
    labels = [r['показатель'] for r in rows]
    assert 'ВЕЛИЧИНЫ' in labels and 'ПОКРЫТИЕ' in labels and 'УСЛОВИЯ' in labels, labels
    assert 'минут в аномалии, мин' in labels, labels          # единица — в подписи строки
    assert any('Таблица 1.' in m.value for m in at.markdown)


# ================================================================= S8: новые ключи снимка
def test_novye_klyuchi_snimka_otsutstvuyut_i_ekran_zhivoy():
    """S8: новых ключей снимка ещё нет — экран читает их через .get и не падает."""
    at = run_app(MODES[1], pro=True)
    assert not at.exception, at.exception
    assert any('class="verdict' in m.value for m in at.markdown)


def test_reestr_istochnikov_s_edinitsami_i_licenziey():
    """S3: во вкладке «Данные» есть реестр: величина, единица, частота, публикация, лицензия."""
    at = run_app(MODES[1])
    reg = []
    for d in at.dataframe:
        vals = d.value
        vals = vals.to_dict('records') if hasattr(vals, 'to_dict') else list(vals)
        if vals and 'лицензия' in vals[0]:
            reg = vals
            break
    assert reg, 'реестр источников должен быть на экране'
    for col in ('источник', 'величина', 'единица', 'частота', 'публикация', 'лицензия', 'ограничение'):
        assert col in reg[0], col
    assert any('CC BY 4.0' in str(r['лицензия']) for r in reg), 'записанная лицензия должна печататься'
    assert any('не указана' in str(r['лицензия']) for r in reg), 'незаписанная лицензия объявляется, а не додумывается'


def table_text(at: AppTest, skip_ids: set | None = None) -> str:
    """Содержимое всех таблиц экрана одной строкой.

    Без этого проверки были слепы к st.dataframe — а именно там, в таблице 3 «Состояние
    источников», на оперативном уровне лежал 64-значный хеш выпуска и подпись «архив выпусков»
    для живого запроса. Две находки третьего круга дожили до жюри ровно поэтому."""
    skip_ids = skip_ids or set()
    out = []
    for d in at.dataframe:
        if id(d) in skip_ids:
            continue
        v = d.value
        try:
            out.append(v.to_csv(index=False))
        except AttributeError:
            out.append(str(v))
    return '\n'.join(out)


def _body_bez_metodiki(at: AppTest) -> str:
    """Весь текст экрана, кроме вкладки «Методика»: в ней символы формул и библиографические
    ссылки стоят законно, а во всём остальном голых идентификаторов быть не должно.
    Содержимое таблиц входит в тело наравне с подписями — зритель читает его так же."""
    skip = set()
    for tab in at.tabs:
        if tab.label == 'Методика':
            skip = {id(e) for e in list(tab.get('markdown')) + list(tab.get('caption')) + list(tab.get('dataframe'))}
    parts = [e.value for e in at.markdown if id(e) not in skip] \
        + [e.value for e in at.caption if id(e) not in skip] \
        + [e.value for e in at.warning] + [e.value for e in at.info] + [str(e.label) for e in at.expander] \
        + [table_text(at, skip)]
    body = '\n'.join(str(p) for p in parts)
    body = re.sub(r'<style>.*?</style>', '', body, flags=re.S)         # CSS — не текст экрана
    body = re.sub(r'\sclass="[^"]*"', '', body)                        # имена классов зритель не видит
    return re.sub(r'\(https?://[^)]*\)', '()', body)


@pytest.mark.parametrize('mode', MODES)
def test_operativnyy_uroven_bez_stub_i_golyh_identifikatorov(mode):
    """S8: на оперативном уровне нет строки «stub», путей модулей и имён вида имя_поля."""
    at = run_app(mode)
    assert not at.exception, at.exception
    body = _body_bez_metodiki(at)
    assert 'stub' not in body.lower(), 'слой заглушек не должен называться на экране'
    assert '.py' not in body
    mods = re.findall(r'\b(?:vkd|app|experiments|scripts|tests)\.[a-z_]+', body)
    assert not mods, mods
    snake = sorted(set(re.findall(r'(?<![\w/])[a-z][a-z0-9]*_[a-z0-9_]+(?![\w/])', body)))
    assert not snake, snake


def test_screen_text_daty_i_razryady():
    """S7: готовая строка модуля печатается в виде экрана — дата, дробь и разряды тысяч."""
    from app.ui import screen_text
    assert screen_text('событие с 05-09 14:00Z пересекает окно') == 'событие с 09.05 14:00 пересекает окно'
    assert screen_text('наблюдение 2026-09-19 01:35Z') == 'наблюдение 19.09.2026 01:35'
    assert screen_text('порог 24000 нТл') == 'порог 24 000 нТл'
    assert screen_text('разница 1.5 мин') == 'разница 1,5 мин'
    assert screen_text('архив DONKI 30.04.2024 — 30.06.2024') == 'архив DONKI 30.04.2024 — 30.06.2024'


@pytest.mark.parametrize('mode', MODES)
def test_punkt_4_kartochki_bez_povtora_istochnika(mode):
    """S1: в пункте 4 карточки одна и та же часть подписи печатается один раз."""
    at = run_app(mode)
    assert not at.exception, at.exception
    fours = [m.value for m in at.markdown if m.value.startswith('**4. Источник')]
    assert fours, 'карточки объяснений должны быть на экране'
    for txt in fours:
        body = txt.partition('**4. Источник и время публикации.** ')[2]
        parts = [p.strip().rstrip('.').lower() for p in body.split('; ') if p.strip()]
        assert len(parts) == len(set(parts)), txt


# ================================================================= C: стык с адаптером истории A2 и живыми источниками A4
def test_excl_reason_i_gruppa_po_russki():
    """Причина исключения группируется без конкретных дат, а отсечка не называется там, где её нет."""
    from app.ui import excl_group_ru, excl_reason_ru, record_release_ru
    assert excl_reason_ru('опубликовано 2024-05-10 13:04Z, после отсечки') == 'опубликовано после отсечки'
    assert excl_reason_ru('время публикации неизвестно — непригодно для строгого воспроизведения') == 'времени публикации нет'
    assert excl_reason_ru('вне области: запись не является наблюдением GOES у Земли') \
        == 'вне области: запись не является наблюдением GOES у Земли'
    assert excl_group_ru('опубликовано после отсечки') == 'время публикации или доступность'
    assert excl_group_ru('вне области: запись не является наблюдением GOES у Земли') == 'содержание записи'
    assert excl_group_ru('окончательный индекс GFZ без собственного времени публикации — только для разбора после факта') \
        == 'время публикации или доступность'
    assert record_release_ru('nasa_donki_notification:20240510-AL-004:c3955880cb5f:SEP') == '20240510-AL-004'
    assert record_release_ru('iss.tle') == 'iss.tle'


def test_prichiny_isklyucheniya_perevedeny_polnostyu():
    """Ни одна причина исключения адаптера A2 не должна остаться по-английски."""
    from app.compute import excluded_ru
    for reason in ('out_of_scope: no explicit Earth arrival in primary summary',
                   'out_of_scope: not a GOES observation at Earth',
                   'context_only: weekly retrospective report is not a current event',
                   'publication_conflict: API/body issue times differ by at least one minute',
                   'final_GFZ_index_without_historic_publication_review_only'):
        assert not re.search(r'[A-Za-z]{4,}', excluded_ru(reason).replace('GOES', '').replace('GFZ', '')), reason
    assert excluded_ru('invalid: bad body').startswith('запись не разобрана')
    assert excluded_ru('нечто своё') == 'нечто своё'          # неизвестную причину не подменяем обобщением


def test_karta_pokrytiya_chitaetsya_iz_coverage_map():
    """Карта покрытия A2 лежит в ключе coverage_map; её строки печатаются по-русски."""
    from app.ui import coverage_rows_ru
    rows = coverage_rows_ru({'goes_p_ge10MeV:observations': {'status': 'missing', 'coverage_fraction': 0.0,
                                                             'reason': 'historic_publication_and_version_availability_not_proven'},
                             'noaa_ngdc_3day_forecast:noaa_kp': {'source_id': 'noaa_ngdc_3day_forecast',
                                                                 'channel_id': 'noaa_kp', 'status': 'full',
                                                                 'coverage_fraction': 1.0, 'reason': None}})
    assert rows[0]['покрытие'] == 'записи нет'
    assert rows[0]['причина'] == 'историческая публикация и доступность именно этой версии не доказаны'
    assert rows[0]['источник'] == 'GOES ≥10 МэВ (архив NASA iSWA)'
    assert rows[-1]['канал'] == 'прогноз Kp'


def test_reestr_razlichaet_zhivuyu_lentu_goes_i_arhiv_iswa():
    """Частота и правило публикации у архива iSWA свои — реестр не выдаёт их за живую ленту."""
    from app.ui import registry_row
    live = registry_row('noaa_swpc_goes')
    arch = registry_row('noaa_swpc_goes', origin='архив наблюдений NASA iSWA (data/goes_2024), 5-минутные средние')
    assert live['частота'] != arch['частота']
    assert '5-минутные' in arch['частота'] and 'не доказано' in arch['публикация']


def test_razbor_ne_nazyvaet_arhiv_goes_otsutstvuyushchim():
    """C3: в «Историческом разборе» архив наблюдений GOES 2024 подключён — состояние из снимка."""
    at = run_app(MODES[1], pro=True)
    assert not at.exception, at.exception
    goes = [r for r in source_rows(at) if 'GOES' in str(r['источник'])]
    assert goes, source_rows(at)
    assert goes[0]['состояние'] == 'архив наблюдений', goes[0]
    assert goes[0]['данные на'] != '—', 'момент данных архива есть в снимке — его надо показать'


def test_strogiy_rezhim_obyavlyaet_isklyuchenie_arhiva_goes():
    """C3: в строгом режиме тот же архив исключён по недоказанной публикации — так и написано."""
    at = run_app(MODES[2], pro=True)
    assert not at.exception, at.exception
    goes = [r for r in source_rows(at) if 'GOES' in str(r['источник'])]
    assert goes and goes[0]['состояние'] == 'исключён строгим режимом', goes


def test_snimok_nesyot_ryad_nablyudeniy_goes_v_razbore():
    """C3: ряд наблюдений GOES ≥10 МэВ есть в снимке разбора и отсутствует в строгом режиме."""
    from datetime import datetime as _dt
    from app.compute import GOES_CHANNEL, run
    t0 = _dt(2024, 5, 10, 12, 0, tzinfo=timezone.utc)
    rev = run('history_review', t0, 240, 360, [0, 120]).S['observations']
    line = [o for o in rev if o['channel'] == GOES_CHANNEL]
    assert line and len(line[0]['points']) > 10, rev
    assert line[0]['unit'] and line[0]['record'], line[0]
    assert all(p['value'] is not None for p in line[0]['points'])
    strict = run('history_forecast', t0, 240, 360, [0, 120]).S['observations']
    assert not [o for o in strict if o['channel'] == GOES_CHANNEL], \
        'в строгом режиме архив GOES исключён — ряда наблюдений быть не должно'


def test_panel_isklyuchennogo_bez_syryh_identifikatorov():
    """Панель «Не вошло в расчёт» переводит причины и не группирует по устаревшему «#»."""
    at = run_app(MODES[2], pro=True)
    assert not at.exception, at.exception
    labels = [str(e.label) for e in at.expander]
    panel_ = [l for l in labels if l.startswith('Не вошло в расчёт')]
    assert panel_, labels
    rows = []
    for d in at.dataframe:
        vals = d.value
        vals = vals.to_dict('records') if hasattr(vals, 'to_dict') else list(vals)
        if vals and 'почему не в расчёте' in vals[0]:
            rows = vals
            break
    assert rows, 'таблица исключённого должна быть на экране'
    for r in rows:
        assert not re.search(r'\b(?:out_of_scope|context_only|publication_conflict|invalid)\b', str(r)), r
        assert '#' not in str(r['первый выпуск']), r


def test_istochnik_zhivogo_prognoza_pereklyuchaetsya():
    """C6: у живого бюллетеня NOAA есть те же три состояния, что у GOES и Kp."""
    at = run_app(MODES[0])
    assert not at.exception, at.exception
    labels = [str(s.label) for s in at.sidebar.selectbox]
    assert any('NOAA' in l for l in labels), labels


def test_lenta_nablyudeniy_v_razbore_i_chestnoe_otsutstvie_v_strogom():
    """C3: в разборе вкладка наблюдений показывает ряд архива, в строгом — объявляет его отсутствие."""
    rev = run_app(MODES[1])
    assert not rev.exception, rev.exception
    obs_tab = [t for t in rev.tabs if t.label == 'Наблюдения и прогнозы'][0]
    assert len(obs_tab.get('plotly_chart')) >= 2, 'ряд наблюдений и прогноз — два рисунка'
    strict = run_app(MODES[2])
    assert not strict.exception, strict.exception
    s_tab = [t for t in strict.tabs if t.label == 'Наблюдения и прогнозы'][0]
    s_body = '\n'.join(str(m.value) for m in s_tab.get('markdown'))
    assert 'Численных наблюдений на этом горизонте нет' in s_body, s_body
    assert 'не доказан' in s_body, 'причина отсутствия берётся из снимка, а не придумывается'


def test_pribornaya_polosa_istorii_nazyvaet_zapis_a_ne_rezhim():
    """C3: в строгом режиме наблюдение Kp приходит из уведомления DONKI — полоса состояния
    не подписывает его архивом GFZ; отсутствие численного GOES объявляется один раз."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.radio('mode').set_value(MODES[2]).run()
    at.sidebar.slider('hist_hour').set_value(19).run()
    assert not at.exception, at.exception
    bar = next(m.value for m in at.markdown if 'class="panel"' in m.value)
    # Девятый круг, находка «экран» №14: момент стоит в самой МЕТКЕ ячейки, а не только в мелкой
    # подписи — крупное число забивало подпись, и полоса читалась как «обстановка спокойная».
    # В метке прибавилась единица: Kp безразмерен, и голое число рядом с «pfu» и «нТл» соседних
    # ячеек читалось бы как величина в тех же единицах (замечание владельца о размерностях).
    # Проверяемое утверждение то же: метка называет ЗАПИСЬ наблюдения, а не режим.
    assert 'Kp на начало периода, безразмерный, наблюдение' in bar and 'архив GFZ' not in bar, bar
    assert 'уведомление NASA DONKI' in bar, bar
    # Полная подпись ячейки («значение на … — начало периода поиска») сокращена до одной строки
    # при разгрузке главного экрана и целиком стоит в раскрытии «Подробнее о каждой величине
    # полосы» — там же, в блоке состояния источников. Ничего не потеряно: момент назван и в
    # самой МЕТКЕ ячейки, что и есть закрытая находка №14, а полный текст — одним кликом.
    _details = [str(m.value) for e in at.expander if 'Подробнее о каждой величине полосы' in str(e.label)
                for m in e.get('markdown')]
    assert any('начало периода поиска' in x for x in _details), _details
    warns = '\n'.join(w.value for w in at.warning) + '\n'.join(i.value for i in at.info)
    assert warns.count('GOES ≥10 МэВ: численного наблюдения') == 1, warns


def test_pribornaya_polosa_razbora_pokazyvaet_nablyudenie_goes():
    """C3: в разборе численное наблюдение GOES стоит в полосе состояния рядом с Kp."""
    at = run_app(MODES[1])
    assert not at.exception, at.exception
    bar = next(m.value for m in at.markdown if 'class="panel"' in m.value)
    # Та же прибавка единицы к метке Kp; смысл проверки не изменился.
    assert 'GOES ≥10 МэВ на начало периода, pfu' in bar, bar
    assert 'Kp на начало периода, безразмерный (архив GFZ)' in bar, bar
    # в архивных режимах тон ячеек нейтральный: зелёный здесь читался бы как «благоприятно» (Т6)
    assert 'k-obs"><div class="cl">GOES' not in bar, bar


# ================================================================= О4: путь к первоисточнику
def test_record_url_ishchet_adres_vo_vseh_polyah():
    """О4: адрес записи ищется в одном месте и во всех полях, где его кладут слои источников.
    Слой A2/A4 кладёт адрес в metadata.url — раньше экран туда не смотрел и печатал «ссылки нет»."""
    from app.ui import record_url
    assert record_url({'url': 'https://a'}) == 'https://a'
    assert record_url({'link': 'https://b'}) == 'https://b'
    assert record_url({'messageURL': 'https://c'}) == 'https://c'
    assert record_url({'metadata': {'url': 'https://d'}}) == 'https://d'
    assert record_url({'url': 'https://a', 'metadata': {'url': 'https://d'}}) == 'https://a'   # порядок полей
    assert record_url({'metadata': {}}) is None
    assert record_url({'url': '   '}) is None
    assert record_url(None) is None and record_url('строка') is None


def test_raw_record_nahodit_zapis_s_tipom_sobytiya():
    """Идентификатор карточки несёт тип события («…:CME_ARRIVAL»), а сырая запись лежит без него."""
    from app.ui import raw_record
    store = {'nasa_donki_notification:20240508-AL-012:00f5': {'metadata': {'url': 'https://kauai/1'}}}
    assert raw_record(store, 'nasa_donki_notification:20240508-AL-012:00f5:CME_ARRIVAL') is not None
    assert raw_record(store, 'nasa_donki_notification:20240508-AL-012:00f5')['metadata']['url'] == 'https://kauai/1'
    assert raw_record({}, 'x') is None and raw_record(None, 'x') is None
    # Девятый круг, находка «экран» №2 (К5): третья ступень — по ИМЕНИ ИСТОЧНИКА, и только когда
    # запись этого источника ровно одна. В текущем режиме карточка ссылается на
    # «celestrak_gp:25544:<хеш>», а слой источников кладёт ту же запись под «celestrak_gp:<64 hex>»,
    # и путь «от предупреждения к первоисточнику» был разорван.
    assert raw_record(store, 'nasa_donki_notification:20240509-AL-001:beef')['metadata']['url'] == 'https://kauai/1'
    two = dict(store, **{'nasa_donki_notification:20240509-AL-001:dead': {'metadata': {'url': 'https://kauai/2'}}})
    assert raw_record(two, 'nasa_donki_notification:20240510-AL-009:beef') is None   # неоднозначно — молчим
    assert raw_record(store, 'gfz_kp:что-нибудь') is None                            # другой источник


def test_gannon_kartochka_usloviya_vedyot_na_pervoistochnik():
    """О4, шаг жюри «от предупреждения к первоисточнику»: в карточке условия окна 2 на пресете
    «Буря Гэннон» стоит ссылка вида https:// на сообщение DONKI, а не «записей без ссылки»."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.button('preset_gannon').click().run()
    assert not at.exception, at.exception
    at.radio('cards_win').set_value(at.radio('cards_win').options[1]).run()      # окно 2
    assert not at.exception, at.exception
    links = [m.value for m in at.markdown if m.value.startswith('**Первоисточник:**')]
    assert links, 'карточки объяснений должны называть первоисточник'
    storm = [x for x in links if 'https://' in x]
    assert storm, links
    assert any('kauai.ccmc.gsfc.nasa.gov' in x for x in storm), storm
    # формулировки «записей без ссылки» на экране больше нет: у записи без адреса сказано, где она
    assert not any('записей без ссылки' in x for x in links), links


def test_zhivoy_rezhim_imeet_ssylku_na_pervoistochnik(monkeypatch, request):
    """О4 в режиме «Сейчас»: хотя бы одна ссылка на первоисточник NOAA на экране есть."""
    import streamlit as st
    from tests.test_integration import _fetched, TLE
    from datetime import timezone
    st.cache_data.clear()
    request.addfinalizer(st.cache_data.clear)
    pinned = _fetched(open(TLE, encoding='utf-8').read(), goes_at=datetime.now(timezone.utc))
    pinned[0][1]['goes_test']['url'] = 'https://services.swpc.noaa.gov/json/goes/primary/integral-protons-1-day.json'
    monkeypatch.setattr('app.fetch_guard.fetch_live_sources', lambda *a, **kw: (pinned, None))
    at = run_app(MODES[0], pro=True)
    assert not at.exception, at.exception
    body = texts(at)
    assert 'https://' in body, 'в текущем режиме на экране не было ни одной ссылки'
    assert 'services.swpc.noaa.gov' in body, body[:400]


# ================================================================= R4: очередь четвёртого круга
def test_sdvigi_okon_perezhivayut_smenu_perioda_poiska():
    """R4-1: смена периода поиска меняет max_value ползунков, и Streamlit теряет их значения.
    Сдвиги держатся в невиджетном ключе состояния и переживают и сжатие, и расширение периода."""
    # Срок и сдвиги переехали из боковой панели (одиннадцатый круг, раздел 3.1); смысл проверки
    # тот же: сдвиги держатся в невиджетном ключе и переживают и сжатие, и расширение срока.
    at = run_app(MODES[1])
    at.number_input('search_h').set_value(24).run()
    at.slider('w0').set_value(120).run()
    at.slider('w1').set_value(360).run()
    for period_h in (12, 6, 24):
        at.number_input('search_h').set_value(period_h).run()
        assert not at.exception, at.exception
        assert at.slider('w0').value == 120, (period_h, at.slider('w0').value)
        assert at.slider('w1').value == 360, (period_h, at.slider('w1').value)
    assert any('class="verdict' in m.value for m in at.markdown)


def test_granica_arhiva_i_verdikt_ne_protivorechat():
    """R4-2: предупреждение о границе архива включается по фактическим окнам, а не по всему
    периоду поиска — иначе сверху «рекомендации не будет», а ниже предпочтительное окно."""
    at = run_app(MODES[2])
    at.sidebar.date_input('hist_date').set_value(datetime(2024, 6, 30).date()).run()
    at.sidebar.slider('hist_hour').set_value(12).run()
    at.number_input('search_h').set_value(24).run()       # срок задаётся строкой задачи, в часах
    at.slider('w0').set_value(0).run()
    at.slider('w1').set_value(120).run()
    assert not at.exception, at.exception
    warn = '\n'.join(w.value for w in at.warning)
    assert 'за границей архива' not in warn, warn
    assert any('class="verdict' in m.value for m in at.markdown)
    at.slider('w1').set_value(780).run()          # последнее окно уходит за 01.07.2024
    assert not at.exception, at.exception
    warn = '\n'.join(w.value for w in at.warning)
    assert 'за границей архива' in warn, warn
    verdict = next(m.value for m in at.markdown if 'class="verdict' in m.value)
    assert 'Оснований для рекомендации недостаточно' in verdict, verdict


def test_vremya_publikacii_ne_vyrezaetsya_iz_istochnika():
    """R4-4: чистка источника режет перечень идентификаторов записей, но не время публикации."""
    keep = status_ru('NASA DONKI, записи: публикация 05-09 13:54Z — 05-10 14:19Z')
    assert 'публикация 09.05 13:54 — 10.05 14:19' in keep, keep
    cut = status_ru('NASA DONKI, записи: nasa_donki_notification:20240509-AL-002:9f24c0f733af; покрытие полное')
    assert 'nasa_donki_notification' not in cut, cut
    assert 'покрытие полное' in cut, cut


@pytest.mark.parametrize('mode', MODES)
def test_panel_verdikta_bez_desyatichnoy_tochki(mode):
    """R4-6: текст правила и причин в панели вердикта проходит через frac_ru — в самой заметной
    строке экрана не должно быть десятичной точки."""
    at = run_app(mode)
    assert not at.exception, at.exception
    panel_html = [m.value for m in at.markdown if 'class="verdict' in m.value]
    assert panel_html, 'панель вердикта должна быть на экране'
    body = re.sub(r'<[^>]+>', ' ', panel_html[0])
    # дата «10.05 13:35» — не дробь: за ней идёт время, а не единица или скобка
    bad = [m.group(0) for m in re.finditer(r'(?<![\d.A-Za-z])\d+\.\d+(?=\s*(?:[А-Яа-я%·)(\],;]|$))', body)]
    assert not bad, bad
    assert 'разброс минут в аномалии на сетке' not in body, body


def test_vkladka_ustoychivosti_pokazyvaet_polzu_sravneniem():
    """R4-9 (О7): на вкладке устойчивости одна строка сравнивает ответ без сетки и допуска с
    итоговым — польза дополнительной функции показана, а не заявлена."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.button('preset_quiet').click().run()
    at.sidebar.radio('level').set_value('Профессиональный').run()
    assert not at.exception, at.exception
    body = texts(at)
    line = [l for l in re.findall(r'<div class="small">([^<]*)</div>', body) if l.startswith('Что даёт проверка на сетке')]
    assert line, 'строки сравнения с простым подходом нет'
    assert 'нулевой допуск' in line[0] and 'вердикт —' in line[0], line[0]


def test_dopusk_ne_nazyvaetsya_razbrosom_absolyutnyh_minut():
    """О3/§2 передачи дел: допуском не может быть разброс абсолютных минут одного окна."""
    from app.ui import rule_ru
    out = rule_ru('п.5: окно 1 (03:38Z), окно 2 (07:38Z) равнозначны — разница минут и флюенса внутри допуска (36 мин, ×1.50)')
    assert 'разброс разности минут между двумя лучшими окнами' in out, out
    assert 'разброс минут в аномалии' not in out, out


def test_pravilo_shagov_3_4_nazyvaet_obe_velichiny():
    """Правило шагов 3–4 печатает вычисленный текст: и минуты в аномалии, и флюенс.
    Заготовка называла только минуты, и выбор окна с бо́льшим флюенсом оставался без объяснения."""
    from app.ui import rule_ru
    out = rule_ru('п.3–4: окно 2 (20:00Z) лучше по космопогоде — меньше по минутам в аномалии '
                  '(51 против 72 мин); не хуже по флюенсу в пределах допуска ×1,50 '
                  '(1,74·10⁶ против 1,65·10⁶ част./см², отношение ×1,05); '
                  'линия метеороидов не противоречит')
    assert 'минут' in out and 'флюенс' in out, out
    assert out.startswith('шаги 3–4 из 5')


def test_plashka_ustoychivosti_razlichaet_prichinu_otsutstviya_vybora():
    """Без предпочтительного окна причина называется вердиктом: условия у всех окон — одно,
    равнозначность — другое. «Без автовыбора» читалось как «окна заблокированы условиями»."""
    from app.ui import robustness_pill
    rob = {'preferred_by_grid': {(1, 1): None, (2, 2): None}, 'stable': True}
    need = robustness_pill(SimpleNamespace(preferred=None, verdict='all_need_check'), rob)
    equiv = robustness_pill(SimpleNamespace(preferred=None, verdict='equivalent'), rob)
    assert 'у каждого окна условие' in need, need
    assert 'остаются равнозначными' in equiv, equiv
    assert 'без автовыбора' not in need and 'без автовыбора' not in equiv


def test_nablyudenie_ne_pokryvayushchee_okno_daet_procherk():
    """О2: измерение, горизонт которого не покрывает окно ни на одну минуту, характеристикой
    этого окна не является — в карточке и в таблице стоит прочерк, а не число прошлого замера."""
    from app.ui import factor_value_ru, obs_share_pct
    covered = SimpleNamespace(value=0.22, limits_note='наблюдение 03:15Z; горизонт наблюдения до 04:15Z покрывает 13 % окна')
    empty = SimpleNamespace(value=0.22, limits_note='наблюдение 03:15Z; горизонт наблюдения до 04:15Z покрывает 0 % окна')
    plain = SimpleNamespace(value=51.0, limits_note='шаг трассы 1 мин')
    assert obs_share_pct(covered) == 13 and obs_share_pct(empty) == 0 and obs_share_pct(plain) is None
    assert factor_value_ru(covered) == '0,22'
    assert factor_value_ru(empty) == '—'
    assert factor_value_ru(plain, 'мин') == '51 мин'
    assert factor_value_ru(None) == '—'


@pytest.mark.parametrize('mode', MODES)
def test_tekushchiy_rezhim_ne_nazyvaet_zhivoy_vypusk_arhivom(mode):
    """О2: происхождение выпуска берётся из режима. В «Сейчас» отсечки нет вовсе, и подписи
    «выпуск до отсечки» / «архив выпусков» у живого бюллетеня были неправдой."""
    at = run_app(mode)
    assert not at.exception, at.exception
    body = _body_bez_metodiki(at)
    if mode == MODES[0]:
        assert 'до отсечк' not in body, body[:600]
        assert 'архив выпусков' not in body
        assert 'живой выпуск' in body or 'живой бюллетень' in body
    elif mode == MODES[1]:
        assert 'выпуск из архива' in body, 'в разборе отсечки нет — выпуск из архива'


# ================================================================= выгрузка говорит словами экрана
@pytest.mark.parametrize('mode_id, args', [
    ('live', ()),
    ('history_review', (datetime(2024, 5, 10, 12, tzinfo=timezone.utc),)),
    ('history_forecast', (datetime(2024, 5, 10, 12, tzinfo=timezone.utc),)),
])
def test_otchyot_govorit_slovami_ekrana(mode_id, args):
    """Отчёт и экран строятся из одного снимка и должны говорить одним языком: русские имена
    источников, русский вердикт, надстрочные степени, числа как на экране."""
    from app.compute import run
    from app.export import report_md
    t0 = args[0] if args else datetime.now(timezone.utc).replace(second=0, microsecond=0)
    r = run(mode_id, t0, 360, 720, [0, 240])
    md = report_md(r.S, r.raw_records)
    assert '10^' not in md and 'e+' not in md
    table = [l for l in md.split('\n') if l.startswith('| ')]
    assert table, 'таблица источников должна быть в отчёте'
    for line in table:
        bad = re.findall(r'(?<![\w/.:-])[a-z][a-z0-9]*_[a-z0-9_]+', line)
        assert not bad, (line, bad)
    for code in ('all_need_check', 'preferred', 'equivalent', 'declared_reconstruction', 'own_calculation'):
        assert '(%s)' % code not in md, code
    assert 'строгость: %s' % ('строгая' if r.S['trajectory_meta'].get('strictness') == 'strict'
                              else 'объявленная реконструкция') in md or 'строгость: недоступна' in md
    if mode_id == 'live':
        assert 'до отсечк' not in md, [l for l in md.split('\n') if 'отсечк' in l]


# ================================================================= пятый круг: экран не спорит сам с собой
def _strip_tags(s: str) -> str:
    """Текст без разметки: закрывается только настоящий тег, иначе «|B| < 24 000 нТл» съедало
    полстроки и любая проверка по скобкам давала ложное срабатывание."""
    return re.sub(r'</?[a-zA-Z][^>]*>', ' ', str(s))


def verdict_html(at: AppTest) -> str:
    """Разметка панели вердикта — самый читаемый блок экрана."""
    return next(m.value for m in at.markdown if 'class="verdict' in m.value)


@pytest.mark.parametrize('mode', MODES)
def test_istochnik_ne_nazyvaetsya_isklyuchyonnym_bez_isklyucheniya(mode):
    """R4-11 (критическая находка): «исключён пользователем» печаталось в режиме «Сейчас» всегда.

    Слой источников штатно дописывает в текст статуса Kp слова «незавершённый Kp-nowcast исключён»
    (текущий 3-часовой интервал почти всегда не закончен), а экран искал подстроку «исключён» по
    всей строке. Признак исключения берётся из запроса, а не из текста."""
    at = run_app(mode)
    assert not at.exception, at.exception
    body = '\n'.join([w.value for w in at.warning] + [table_text(at)])
    assert 'исключён пользователем' not in body, [l for l in body.split('\n') if 'исключён пользователем' in l]


def test_source_short_chitaet_priznak_a_ne_tekst():
    """Тот же дефект прямым вызовом: живой ответ с «…исключён» в тексте — не исключение."""
    from app.ui import source_short, source_state
    live = {'status': 'получено по сети, давность данных 102,7 мин; незавершённый Kp-nowcast исключён',
            'live_ok': True, 'from_cache': False}
    assert source_state(live, False) is None
    assert source_short(live, False) == ('живой запрос', 'ok')
    off = {'status': 'источник исключён пользователем — данных нет', 'live_ok': False, 'from_cache': False}
    assert source_state(off, 'off') == 'off'
    assert source_short(off, 'off') == ('исключён пользователем', 'crit')
    # признак из снимка сильнее запроса: когда слой расчёта начнёт класть state, экран возьмёт его
    assert source_state({'state': 'off'}, False) == 'off'


@pytest.mark.parametrize('mode', MODES)
def test_v_tele_ekrana_net_neparnyh_skobok(mode):
    """R4-1 и R4-13: bullet_short_ru резал строку по первому «;», а он стоял ВНУТРИ скобок, и на
    экране оставалась незакрытая скобка без точки; вместе с ней пропадало происхождение порога 5 %."""
    at = run_app(mode)
    assert not at.exception, at.exception
    bad = [l for l in _strip_tags(_body_bez_metodiki(at)).split('\n') if l.count('(') != l.count(')')]
    assert not bad, [l.strip()[:200] for l in bad[:3]]


def test_bullet_short_ru_ne_teryaet_proishozhdenie_poroga():
    """Тот же дефект прямым вызовом: короткая форма сохраняет и скобку, и происхождение числа."""
    from app.ui import bullet_short_ru
    src = ('окна не различаются: разница 0,003 % ниже порога различимости 5 % '
           '(правило команды, не норма; настройка config/settings.toml); '
           'линия метеороидов различает окна только по высоте и длительности; при равной длительности '
           'на орбите МКС различие меньше 0,01 % — её роль здесь абсолютная оценка и охват, не выбор окна')
    out = bullet_short_ru(src)
    assert out.count('(') == out.count(')') == 1, out
    assert 'настрой' in out, out
    assert 'config/settings.toml' not in out, out
    assert out.endswith('не выбор окна.'), out


def test_close_cut_parens_zakryvaet_obrezannyy_fragment():
    """Слой объяснений обрезает тело уведомления многоточием — иногда посреди скобки."""
    from app.ui import close_cut_parens
    out = close_cut_parens('опубликованный прогноз: Kp до 8 (диапазон 6–8, верхняя граница, не…; следующая запись')
    assert out.count('(') == out.count(')') == 1, out
    assert 'не…)' in out, out
    assert close_cut_parens('всё (на месте) тут') == 'всё (на месте) тут'


@pytest.mark.parametrize('pro', [False, True])
def test_istoricheskiy_razbor_ne_govorit_ob_otsechke(pro):
    """R4-4: в «Историческом разборе» отсечки нет (cutoff_utc = None), и слово «отсечка» на его
    экране не должно стоять ни в предупреждении, ни в подписи — даже в отрицании: жюри читает
    подписи по отдельности, а два блока того же экрана говорят «архив взят весь»."""
    at = run_app(MODES[1], pro=pro)
    assert not at.exception, at.exception
    body = _body_bez_metodiki(at)
    assert 'отсечк' not in body, [s for s in re.findall(r'.{0,70}отсечк.{0,70}', body)][:3]


@pytest.mark.parametrize('mode', MODES)
def test_operativnyy_uroven_bez_mashinnyh_dat(mode):
    """R4-3: даты вида «05-10 13:35Z» оставались в таблице «Окна и факторы» и в заголовках свёрток
    «Объяснений», хотя строкой выше вердикт печатал то же событие как «10.05 12:14»."""
    at = run_app(mode)
    assert not at.exception, at.exception
    body = _body_bez_metodiki(at)
    assert not re.search(r'\d\d-\d\d \d\d:\d\dZ', body), re.findall(r'.{0,50}\d\d-\d\d \d\d:\d\dZ', body)[:5]


@pytest.mark.parametrize('mode', MODES)
def test_operativnyy_uroven_bez_gologo_vremeni_s_Z(mode):
    """Пятый круг: на одном оперативном экране стояли три вида одного времени — «наблюдение 06:15»
    в приборной полосе, «наблюдение 06:15Z покрывает 13 % окна» в блоке вердикта и «наблюдение
    19.09.2026 06:15» в карточке окна. Под прежние правила `dates_ru` голое «06:15Z» не подпадало:
    им обеим нужна дата. Бриф §9.8 требует одного вида и называет UTC один раз, в шапке."""
    at = run_app(mode)
    assert not at.exception, at.exception
    body = _body_bez_metodiki(at)
    assert not re.search(r'\d\d:\d\d\s*Z', body), re.findall(r'.{0,60}\d\d:\d\d\s*Z', body)[:5]


def test_dates_ru_snimaet_Z_no_ne_trogaet_chisla():
    """Та же правка прямым вызовом: снимается только признак зоны у времени."""
    from app.ui import dates_ru
    assert dates_ru('окно 1 (06:23Z), окно 2 (10:23Z)') == 'окно 1 (06:23), окно 2 (10:23)'
    assert dates_ru('наблюдение 06:15Z покрывает 13 % окна') == 'наблюдение 06:15 покрывает 13 % окна'
    assert dates_ru('2026-09-19 05:55Z') == '19.09.2026 05:55'       # дата с временем — как прежде
    assert dates_ru('05-10 13:35Z') == '10.05 13:35'
    assert dates_ru('206,919 pfu в 10.05 17:45Z') == '206,919 pfu в 10.05 17:45'
    assert dates_ru('запись 1:23:45Z') == 'запись 1:23:45Z'          # внутри длинного времени не режем
    assert dates_ru('высота 420 км') == 'высота 420 км'


@pytest.mark.parametrize('mode', MODES)
def test_odna_fraza_ob_ustoychivosti_vybora(mode):
    """R4-18: на одном экране стояли «Есть предпочтительное окно» и «выбор меняется на сетке порогов»,
    а подпись таблицы 1 добавляла «порядок окон при нулевом допуске». Формально верно всё, читается
    как взаимное опровержение. На оперативном уровне остаётся ровно одна фраза — с причиной."""
    at = run_app(mode)
    assert not at.exception, at.exception
    body = _body_bez_metodiki(at)
    assert len(re.findall(r'устойчив', body)) == 1, re.findall(r'.{0,90}устойчив.{0,60}', body)
    assert 'ранжирован' not in body, body[:400]
    assert 'порядок окон при нулевом допуске' not in body


def test_pod_verdiktom_skazano_otkuda_dopusk():
    """R4-19: «окна равнозначны — разница внутри допуска (48 мин)» без единого слова о том, откуда
    взялись 48 мин. Числа берутся из Robustness: разность минут по каждому порогу сетки и её размах."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.button('preset_quiet').click().run()
    assert not at.exception, at.exception
    line = _strip_tags(verdict_html(at))
    # Покрытие частичное, но сравнение состоялось (решение владельца 19.09 по разбору Codex п. 1),
    # поэтому допуск участвует в выводе — и обязан объяснить, откуда взят. Рядом стоит область
    # вывода: на какой доле окна сравнение сделано и чего оно не означает.
    # Девятый круг, находка «экран» №7: при неустойчивом исходе заголовок смягчается, поэтому
    # проверяется вердикт по существу, а не дословная формулировка заголовка.
    assert 'редпочтительное окно' in line, line[:400]
    assert 'Откуда допуск' in line, line
    assert '24 000 нТл' in line.replace(' ', ' '), line            # рабочий порог сетки назван
    assert 'размах по сетке' in line, line
    assert 'Область вывода' in line and 'при покрытии модели' in line, line[:600]
    assert 'не заключение о полном риске ВКД' in line, line[:600]


def test_tolerance_origin_ru_schitaet_po_snimku():
    """Та же строка прямым вызовом: ни одно число не зашито — все из снимка."""
    from app.ui import tolerance_origin_ru
    S = {'robustness': {'diff_by_thr': {'22000': 15.0, '24000': 48.0, '26000': 63.0}, 'tol_min': 48.0,
                        'tol_ratio': 1.5, 'ratio_by_e': {'12.5': 1.0, '30': 1.05, '50': 1.07}}}
    out = tolerance_origin_ru(S)
    assert 'Откуда допуск 48 мин' in out, out
    assert '15 / 48 / 63 мин' in out, out
    assert 'размах по сетке — 48 мин' in out, out
    assert tolerance_origin_ru({'robustness': {}}) == ''
    assert '×1,50' in tolerance_origin_ru(S, pro=True)


def verdict_visible(at: AppTest) -> str:
    """Текст блока вердикта, который виден БЕЗ клика: весь блок минус содержимое свёртки."""
    return re.sub(r'\s+', ' ', _strip_tags(re.sub(r'<details class="vmore">.*?</details>', ' ',
                                                  verdict_html(at), flags=re.S))).strip()


def verdict_folded(at: AppTest) -> str:
    """Текст, уехавший в свёртку блока вердикта («на один клик глубже», бриф §9.1)."""
    return re.sub(r'\s+', ' ', _strip_tags(''.join(re.findall(r'<details class="vmore">(.*?)</details>',
                                                              verdict_html(at), re.S)))).strip()


@pytest.mark.parametrize('mode', MODES)
def test_blok_verdikta_ne_splosnoy_abzac(mode):
    """R4-7, R4-23 и находка пятого круга: блок «Почему?» был сплошным абзацем — 934…1921 символа
    на оперативном уровне. Внутри подряд стояли правило, сравнение по космопогоде, абзац про
    метеороиды, строки о покрытии, плашка устойчивости и абзац «Откуда допуск». Всё верно, но
    у члена жюри три минуты на весь экран.

    Теперь без клика видно только то, что отвечает на два вопроса брифа §9.1: вердикт с окном,
    величины сравнения (или названия условий) и чего не хватает. Правило целиком, роль линии
    метеороидов, остальные строки о покрытии, происхождение допуска и результат сетки порогов
    стоят в свёртке ВНУТРИ того же блока — ничего не выброшено (проверяется ниже).

    Измерено на этой ветке 19.09 (оперативный уровень, видимая часть): режимы «Текущая обстановка»
    315, «Исторический разбор» 526, «Прогноз из прошлого» 409; пресеты «Сейчас» 315 (живые данные
    от прогона к прогону разные), «Гэннон» 625, «Тихая дата» 489 символов.
    Было 1242/1921/1343/1242/934/1517.

    400 символов из находки достигнуты не везде, и вот чем заняты остальные — это ограничение,
    а не недосмотр: «Гэннон» 625 — из них 415 занимает перечень записей единственного условия:
    два уведомления DONKI, каждое со своим временем публикации и своим Kp, и сводить их в одну
    фразу нельзя (закрытая критическая находка пятого круга), а ещё 141 — строка «Область вывода»,
    без которой заголовок утверждает больше, чем посчитано (решение владельца 19.09 по разбору
    Codex п. 1). «Тихая дата» 489 — почти всё занимает вычисленное правило шага 3–4, где рядом
    с «не хуже по флюенсу» обязаны стоять обе величины, допуск и отношение (R4-17: без них фраза
    опровергается числами той же строки); прежняя строка о причине неполного покрытия (209
    символов) из видимой части убрана — те же доля и пропуски теперь стоят в «Области вывода»,
    а подробности с минутами — в свёртке, в карточке окна и в отчёте.

    Порог 650 стоит как защита от нового разрастания и с появлением «Области вывода» НЕ поднят:
    место под неё освобождено снятием повтора, а не расширением бюджета."""
    at = run_app(mode)
    assert not at.exception, at.exception
    html_ = verdict_html(at)
    vis = verdict_visible(at)
    assert len(vis) <= VERDICT_BUDGET, (len(vis), vis)
    assert html_.count('<li') <= 5, html_
    assert 'Охват:' not in vis and 'Не учтено:' not in vis, vis      # они во вкладке «Окна и факторы»
    assert len(re.findall(r'не покрывает окно', vis)) <= 1, vis
    # на поверхности нет ни происхождения допуска, ни разбора сетки порогов — они на клик глубже
    assert 'Откуда допуск' not in vis, vis
    # Строка устойчивости с девятого круга стоит НА ПОВЕРХНОСТИ (находка «экран» №7): заголовок
    # «Есть предпочтительное окно» при фразе о неустойчивости, спрятанной под свёртку, — это
    # заголовок увереннее расчёта. Разбор самой сетки по ячейкам остаётся во вкладке.
    assert 'сетк' in vis, vis


@pytest.mark.parametrize('mode', MODES)
def test_iz_bloka_verdikta_nichego_ne_propalo(mode):
    """Обратная сторона сокращения: всё, что ушло с поверхности, обязано стоять в свёртке того же
    блока — иначе это не «на один клик глубже», а молчаливое выбрасывание пояснения."""
    at = run_app(mode)
    assert not at.exception, at.exception
    fold = verdict_folded(at)
    assert fold, 'свёртки под вердиктом нет'
    assert 'формулы (8) и (9)' in fold, fold                          # формальная запись правила
    # результат сетки порогов ушёл на поверхность (девятый круг, находка «экран» №7), и проверяется
    # он теперь в test_blok_verdikta_ne_splosnoy_abzac; здесь — что свёртка не опустела
    assert 'сетк' in verdict_visible(at), verdict_visible(at)
    # правило целиком уезжает в свёртку всюду, кроме шагов 3–4 и 4: там оно и есть ответ «почему»
    # и остаётся на поверхности — тогда в свёртке стоит происхождение допуска
    assert 'Правило целиком' in fold or 'Откуда допуск' in fold or 'условия' in fold, fold


@pytest.mark.parametrize('preset', ['now', 'gannon', 'quiet'])
def test_blok_verdikta_korotkiy_na_presetah(preset):
    """Тот же бюджет на трёх пресетах защиты — их жюри увидит первыми (бриф §9.9)."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.button('preset_' + preset).click().run()
    assert not at.exception, at.exception
    vis = verdict_visible(at)
    assert len(vis) <= VERDICT_BUDGET, (len(vis), vis)
    assert verdict_folded(at), 'свёртки под вердиктом нет'


def test_ohvat_ostalsya_na_ekrane_vo_vkladke():
    """Охват и «не учтено» с оперативного уровня не исчезли — они во вкладке «Окна и факторы» (О1)."""
    at = run_app(MODES[1])
    assert not at.exception, at.exception
    tab = next(t for t in at.tabs if t.label == 'Окна и факторы')
    body = '\n'.join(str(m.value) for m in tab.get('markdown'))
    assert 'Охват:' in body and 'Не учтено:' in body, body[:400]


def test_status_ru_srezaet_tehnicheskiy_hvost_i_perevodit_produkty():
    """R4-6 и R4-27: на оперативном уровне в таблице 3 стояли сырой адрес с параметрами запроса и
    слово timeout, имя продукта NOAA «NGDC daypre» и служебное «Kp-nowcast», а давность печаталась
    двумя округлениями — своей колонкой (103) и внутри статуса (102,7)."""
    tle = ('SGP4 по TLE (api.wheretheiss.at), эпоха 17.09.2026 21:14; TLE: получено по сети, '
           'давность данных 1888,7 мин; проверка адресов: '
           'https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE: timeout')
    out = status_ru(tle)
    assert 'timeout' not in out and 'CATNR' not in out and 'http' not in out, out
    assert 'часть адресов цепочки не ответила' in out, out
    assert 'давность' not in out, out
    assert 'проверка адресов' in status_ru(tle, pro=True)              # на профессиональном — как есть
    kp = status_ru('получено по сети, давность данных 102,7 мин; незавершённый Kp-nowcast исключён')
    assert 'Kp-nowcast' not in kp and 'незавершённый 3-часовой интервал Kp' in kp, kp
    noaa = status_ru('живой бюллетень NOAA 3-day не содержит этого канала: суточная вероятность '
                     'публикуется отдельным выпуском NGDC daypre, которого в текущем режиме нет')
    assert 'daypre' not in noaa and '3-day' not in noaa, noaa
    assert 'суточным выпуском NOAA' in noaa, noaa


def test_pervoistochnik_razlichaet_prichinu_otsutstviya_adresa():
    """R4-10: «таблицы стандартов и эфемериды в составе сервиса» говорилось и про живой набор
    орбитальных элементов, у которого адрес просто не сохранён (он есть в манифесте выгрузки)."""
    from app.ui import record_no_url_ru
    out = record_no_url_ru(['celestrak_gp:25544:aedc1743d259', 'igrf14:717f6dce821a'])
    assert 'из состава сервиса' in out and 'живого источника' in out, out
    assert 'манифесте выгрузки' in out, out
    only_builtin = record_no_url_ru(['ost1044_A_А_2_1:59e573afafa8'])
    assert 'живого источника' not in only_builtin, only_builtin


def test_prichina_nepokrytiya_kanala_ne_vydumyvaetsya():
    """R4-21: «ячейки покрывают 0 % окна» верно только когда выпуск есть. Когда допустимого выпуска
    нет вовсе, карточка обязана называть ту же причину, что таблица источников."""
    from app.ui import _cov_reason
    f = SimpleNamespace(name='вероятность протонного события за сутки, прогноз NOAA',
                        limits_note='суточная вероятность источника, не вероятность за окно; '
                                    'покрытие окна ячейками 0 %; выпуска с ячейками на это окно нет')
    assert _cov_reason(f) == ('вероятность протонного события за сутки: выпуска с этим каналом на горизонт окна '
                              'нет — канал не учитывается, объявлено')
    f2 = SimpleNamespace(name='прогноз Kp NOAA, максимум в окне',
                         limits_note='прогноз, не наблюдение; покрытие окна ячейками 40 %')
    assert _cov_reason(f2) == 'прогноз Kp: ячейки покрывают 40 % окна'


def test_prichina_nepokrytiya_GOES_nazyvaet_dolyu_a_ne_uroven():
    """Пятый круг: при частичном покрытии ветки не было, и карточка окна на вопрос «почему покрытие
    неполное» отвечала уровнем по шкале S — «поток протонов GOES ≥10 МэВ: наблюдение 19.09.2026 06:15
    (ниже S1 (фон))». Уровень к покрытию отношения не имеет, а блок вердикта на том же экране
    называл настоящую причину — долю окна. Два места об одном говорили разное (бриф §9.7)."""
    from app.ui import _cov_reason
    f = SimpleNamespace(name='поток протонов GOES ≥10 МэВ',
                        limits_note='покрытие частичное; горизонт данных до 2026-09-19 07:15Z; '
                                    'наблюдение 2026-09-19 06:15Z (ниже S1 (фон)), давность 9 мин; '
                                    'горизонт наблюдения до 07:15Z покрывает 12 % окна; '
                                    'на остальные участки окна наблюдение не распространяется, '
                                    'прогноза потока на окно нет; уровень ниже S1 (фон)')
    out = _cov_reason(f)
    assert out == ('GOES: наблюдение 19.09.2026 06:15 покрывает 12 % окна — '
                   'на остальные участки прогноза потока нет'), out
    assert 'S1' not in out, out                       # уровень по шкале S стоит в приборной полосе
    assert out.count('(') == out.count(')') == 0, out  # и вложенных скобок в карточке больше нет


def test_kesh_ne_menyaet_cvet_proishozhdeniya():
    """R4-28: янтарный означал разом внешний прогноз и наблюдение из кеша, и по цвету жюри не
    отличало наблюдение от прогноза — ровно то различие, ради которого система цветов заведена."""
    import io as _io
    from app.ui import COLOR_LEGEND
    assert 'прогноз или данные из кеша' not in COLOR_LEGEND, COLOR_LEGEND
    assert 'янтарный — внешний прогноз.' in COLOR_LEGEND, COLOR_LEGEND
    assert 'из кеша' in COLOR_LEGEND, COLOR_LEGEND                     # кеш назван, но подписью, не цветом
    src = _io.open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app', 'main.py'),
                   encoding='utf-8').read()
    assert "'fc' if (g.value >=" not in src, 'наблюдение GOES не должно краситься тоном прогноза'
    assert "'fc' if k_src.get('from_cache')" not in src, 'кеш Kp не должен краситься тоном прогноза'
    # тема Streamlit описывает ту же систему цветов; её комментарий пережил R4-28 и говорил
    # «янтарный — внешний прогноз ИЛИ кеш», то есть ровно то, что было исправлено в коде
    cfg = _io.open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                '.streamlit', 'config.toml'), encoding='utf-8').read()
    assert 'внешний прогноз или кеш' not in cfg, cfg[:400]
    assert 'Кеш цвет НЕ меняет' in cfg, cfg[:400]


def test_orbita_v_razbore_nazyvaet_datu_sozdaniya():
    """R4-4, обратная сторона: строгий режим по-прежнему называет отсечку, разбор — дату создания."""
    from app.ui import source_issues
    src = {'orbit': {'strictness': 'declared_reconstruction'}, 'noaa_swpc_goes': {'data_utc': '2024-05-10T12:00:00+00:00'}}
    strict = source_issues(src, None, 'history_forecast', cutoff_utc='2024-05-10T12:00:00+00:00')
    assert any('до отсечки' in x for x in strict), strict
    review = source_issues(src, None, 'history_review', cutoff_utc=None,
                           orbit_created_utc='2024-05-08T16:49:00+00:00')
    assert any('создана 08.05.2024 16:49 UTC' in x for x in review), review
    assert not any('отсечк' in x for x in review), review


def test_tihaya_data_pravilo_s_dopuskom_na_ekrane():
    """R4-17 на слитой ветке: «окно 2 … не хуже по флюенсу» стояло рядом с числами, которые это
    опровергают — флюенс выбранного окна 1,74·10⁶ против 1,65·10⁶. Утверждение верно только
    по модулю допуска, и допуск обязан стоять в той же фразе, а не в подписи таблицы
    на другой вкладке.

    Экран этой фразы не переписывает: после слияния пятого круга её собирает из вычисленного
    сам слой сравнения окон (`vkd/windows/compare.py`), вместе с обеими величинами, допуском
    и отношением. Здесь проверяется то, что в итоге видит жюри."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.button('preset_quiet').click().run()
    assert not at.exception, at.exception
    rule = re.sub(r'\s+', ' ', _strip_tags(verdict_html(at)))
    # заголовок при неустойчивом исходе — «Предпочтительное окно есть, но выбор неустойчив»
    assert 'редпочтительное окно' in rule, rule[:400]
    # Десятый круг (находка 15): формулировка «не хуже по флюенсу» рядом с числами, которые
    # показывают обратное, читалась как подгонка вывода под ответ. Слова «не хуже» на экране
    # больше нет ни в одной ветке; какая из двух печатается на этой дате, зависит от чисел —
    # после перехода на интегрирование по фактическому времени (ветка Codex 0.7.0) здесь
    # флюенс выбранного окна НИЖЕ. Требования к фразе прежние: оба числа с единицей и отношение,
    # а если выбранное окно проигрывает — ещё и допуск.
    assert 'не хуже' not in rule, rule[:600]
    m = re.search(r'(?:флюенс ниже|по флюенсу выбранное окно выше)[^;]*(?:;[^;]*различием не считается[^;]*)?', rule)
    assert m, rule[:600]
    claim = m.group(0)
    assert re.search(r'\d+(?:,\d+)?(?:·10⁶)? против \d+(?:,\d+)?(?:·10⁶)? част\./см²', claim), claim
    assert re.search(r'отношение ×\d+,\d+', claim), claim
    if 'выбранное окно выше' in claim:
        assert 'внутри допуска ×' in claim, claim
        assert 'различием не считается' in claim, claim
    assert 'мин' in rule and 'флюенс' in rule, rule[:600]
    # Вывод сделан при неполном покрытии — это сказано рядом, а не подразумевается.
    assert 'при покрытии модели' in verdict_visible(at), verdict_visible(at)


def test_tablica_posle_otsechki_nazyvaet_proishozhdenie_stroki():
    """Слияние пятого круга: на «Гэннон» половина строк таблицы «Проверка после отсечки» взята
    не из окончательного ряда GFZ, а из датированных уведомлений DONKI (Kp 9 против 8,67 у GFZ),
    и подпись «наблюдения Kp — окончательный ряд GFZ» опровергалась числами той же таблицы.

    Теперь правило отбора стоит в подписи, а происхождение каждой строки — своей колонкой,
    из ключа `origin` снимка. Экран и выгрузка говорят об этой таблице одно и то же."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.button('preset_gannon').click().run()
    assert not at.exception, at.exception
    rows = []
    for d in at.dataframe:
        v = d.value
        v = v.to_dict('records') if hasattr(v, 'to_dict') else list(v)
        if v and isinstance(v[0], dict) and 'интервал с' in v[0]:
            rows = v
            break
    assert rows, 'таблицы проверки после отсечки на экране нет'
    assert all(r.get('происхождение') for r in rows), rows
    assert any('уведомление NASA DONKI' in r['происхождение'] for r in rows), rows
    assert any('окончательный ряд GFZ' in r['происхождение'] for r in rows), rows
    assert any('уточнено с 8,67 до 9' in r['происхождение'] for r in rows), rows
    caps = ' '.join(str(c.value) for c in at.caption)
    assert 'с самым поздним известным временем публикации' in caps, caps
    assert 'Наблюдения Kp — окончательный ряд GFZ' not in caps, caps


# ================================================================= девятый круг, область «экран»
# Каждая проверка ниже закрывает одну находку глубокого разбора пятью взглядами (19.09) или одну
# находку владельца прямо с развёрнутого экрана. Номера — по списку находок области «экран».


def test_css_bez_znakov_vne_pechatnogo_diapazona():
    """К6 (критическая, находка владельца №1): строка стилей была объявлена обычной, и escape
    вида «\\25B8» читался Python как ВОСЬМЕРИЧНЫЙ — в CSS уходили U+0015 и U+0000. Браузер по
    спецификации заменяет нуль на U+FFFD, и перед подписью «Как это посчитано…» в блоке вердикта
    рисовался мусор «B8◆A0» — в первом же элементе экрана."""
    from app.ui import CSS
    bad = sorted({hex(ord(c)) for c in CSS if ord(c) < 32 and c not in '\n\r\t'})
    assert not bad, bad
    assert '\x00' not in CSS and '\x15' not in CSS
    assert 'summary::before' in CSS


def test_raw_record_nahodit_zapis_po_imeni_istochnika():
    """К5 (критическая, находка №2): в текущем режиме карточки ссылаются на запись трассы
    «celestrak_gp:25544:<хеш>», а слой источников кладёт ту же запись под «celestrak_gp:<64 hex>».
    Совпадения не было ни на одном шаге, и карточка печатала «адрес записи не сохранён слоем
    источников», хотя адрес есть в metadata.url и выгрузка его печатает."""
    from app.ui import raw_record, record_url
    at = run_app(MODES[0])
    assert not at.exception, at.exception
    links = [m.value for m in at.markdown if str(m.value).startswith('**Первоисточник:**')]
    assert links, 'карточки объяснений должны называть первоисточник'
    assert any('https://' in x for x in links), links
    assert not any('адрес записи не сохранён' in x for x in links), links
    # и подпись ссылки — по-русски, без английского идентификатора слоя источников (бриф §9.8)
    assert not any('celestrak_gp' in x for x in links), links
    store = {'celestrak_gp:0011223344556677': {'metadata': {'url': 'https://wheretheiss/1'}}}
    assert record_url(raw_record(store, 'celestrak_gp:25544:aedc1743d259')) == 'https://wheretheiss/1'


def test_record_no_url_razlichaet_tri_prichiny():
    """К5, вторая половина: при неудаче поиска экран обязан писать правду о том, чего именно нет.
    Причин три — запись из состава сервиса, запись без адреса и запись, которой в выгрузке нет."""
    from app.ui import record_no_url_ru
    store = {'gfz_kp:abcd': {'value': 1}}
    out = record_no_url_ru(['ost1044_A:1', 'gfz_kp:abcd', 'noaa_swpc_goes:zzz'], store)
    assert 'из состава сервиса' in out, out
    assert 'сетевого адреса в ней не сохранено' in out, out
    assert 'в сырые записи этого расчёта не попало' in out, out


def test_kartochka_bez_pokrytiya_ne_zelenaya():
    """К2 (критическая, находка №4): при исключённом GOES карточка окна показывала ЗЕЛЁНУЮ плашку
    «без условий» рядом с «покрытие: космопогода: нет» — отсутствие данных выдавалось за отсутствие
    воздействия. Постановка и Т6 требуют различать эти два случая."""
    at = run_app(MODES[0])
    at.sidebar.selectbox('dis_goes').set_value('исключён: нет данных').run()
    assert not at.exception, at.exception
    cards = [m.value for m in at.markdown if 'class="wcard' in str(m.value)]
    assert cards, 'карточек окон на экране нет'
    for c in cards:
        assert 'pill-ok">без условий' not in c, c
        assert 'условия не проверены: нет данных' in c, c
        assert 'Отсутствие условия здесь не означает отсутствия воздействия' in c, c


def test_otkaz_ot_rekomendacii_ne_nazyvaet_luchshee_okno():
    """К1 (критическая, находка №6): под заголовком «Оснований для рекомендации недостаточно»
    ПЕРВОЙ видимой строкой стоял утвердительный вывод сравнения — «— лучше окно 2», то есть
    ровно та рекомендация, в которой заголовок только что отказал. Числа остаются, вывод снят,
    причина отказа стоит первой."""
    from app.ui import comparison_without_pick_ru
    at = run_app(MODES[0])
    at.sidebar.selectbox('dis_goes').set_value('исключён: нет данных').run()
    assert not at.exception, at.exception
    html_ = verdict_html(at)
    assert 'Оснований для рекомендации недостаточно' in html_, html_
    items = re.findall(r'<li[^>]*>(.*?)</li>', html_, re.S)
    assert items, html_
    # Первой строкой — причина отказа, а не сравнение окон. После слияния восьмого круга при
    # `insufficient` на оперативном уровне видимый список собирается своей веткой («Неполный
    # охват: …»), поэтому проверяется смысл, а не дословная приставка.
    assert items[0].startswith('Чего не хватает') or items[0].startswith('Неполный охват'), items
    assert 'мин в аномалии' not in items[0], items
    body = _strip_tags(html_)
    assert '— лучше окно' not in body, body
    assert 'как расчёт факторов, а не как рекомендация' in body, body
    # числа сравнения при этом никуда не делись
    assert 'мин в аномалии' in body and 'флюенс' in body, body
    txt = comparison_without_pick_ru('окно 1: 5 мин, флюенс 1 — лучше окно 2 (20:00Z)', 'insufficient')
    assert 'лучше окно' not in txt and '5 мин' in txt, txt
    assert comparison_without_pick_ru('окно 1 — лучше окно 2', 'preferred') == 'окно 1 — лучше окно 2'


def test_neustoychivyy_vybor_viden_bez_klika_i_v_zagolovke():
    """К3 (критическая, находка №7): заголовок говорил «Есть предпочтительное окно», а фраза
    о неустойчивости выбора лежала под свёрткой. На пресете «Тихая дата» рекомендация держится
    на 21 мин при собственном допуске 20 мин, и на поверхности об этом не было ни слова."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.button('preset_quiet').click().run()
    assert not at.exception, at.exception
    vis = verdict_visible(at)
    assert 'выбор неустойчив' in vis, vis
    assert 'Предпочтение держится на' in vis and 'при допуске' in vis, vis
    assert 'сетк' in vis, vis


def test_podpis_preseta_ne_vryot_posle_ruchnyh_izmeneniy():
    """К4 (критическая, находка №5): подпись под кнопками пресетов утверждала параметры пресета
    до конца сессии, даже когда пользователь всё перекрутил руками, — три утверждения на одной
    панели, два ложные."""
    from app.ui import PRESET_CHANGED_RU, PRESETS, preset_matches
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.button('preset_gannon').click().run()
    caps = ' '.join(str(c.value) for c in at.sidebar.caption)
    assert 'Буря Гэннон' in caps, caps
    # Длительность выхода задаётся строкой задачи главной области, а не ползунком боковой панели
    # (одиннадцатый круг, раздел 3.1): проверяется то же самое изменение плана 360 -> 120 мин.
    at.number_input('duration').set_value(120).run()
    assert not at.exception, at.exception
    caps = ' '.join(str(c.value) for c in at.sidebar.caption)
    assert PRESET_CHANGED_RU in caps, caps
    assert 'Буря Гэннон, 10.05.2024 12:00 UTC —' not in caps, caps
    g = next(p for p in PRESETS if p['key'] == 'gannon')
    d = datetime(2024, 5, 10).date()
    assert preset_matches(g, 'Прогноз из прошлого', d, 12, 360, 720, [0, 240])
    assert not preset_matches(g, 'Прогноз из прошлого', d, 12, 120, 720, [0, 240])
    assert not preset_matches(g, 'Прогноз из прошлого', d, 12, 360, 720, [0, 480])
    assert not preset_matches(g, 'Текущая обстановка', d, 12, 360, 720, [0, 240])


def test_prichina_nehvatki_privyazana_k_kanalu():
    """Находка №3 (критическая): суффикс « — GOES исключён пользователем» приклеивался к КАЖДОЙ
    строке нехватки со словом «космопогода». Строка про отсутствие Kp получала объяснение про
    GOES — ложная атрибуция причины в блоке вердикта."""
    at = run_app(MODES[0])
    at.sidebar.selectbox('dis_kp').set_value('исключён: нет данных').run()
    assert not at.exception, at.exception
    for line in re.findall(r'Чего не хватает: ([^<]*)', verdict_html(at)):
        head_, sep, _ = line.partition(' — GOES исключён')
        if sep:
            assert 'GOES' in head_, line


def test_uvedomleniya_o_sobytiyah_v_zhivom_rezhime_obyavleny():
    """Находка №9 (критическая): в текущем режиме не опрашивается ни один источник событий, и это
    нигде не объявлялось — полоса печатала «События на горизонте — 0 записей», что читается как
    «опросили, событий нет». Постановка: «Пропуск данных не равен нулевому риску»."""
    at = run_app(MODES[0])
    assert not at.exception, at.exception
    bar = next(m.value for m in at.markdown if 'class="panel"' in m.value)
    assert 'События на горизонте' in bar, bar
    # Метка и счётчик оставлены прежними — их проверяет tests/test_round6_integration.py, файл вне
    # правки этой области. Ложное чтение снято подписью: ноль означает «источников событий не
    # опрашивали», а не «событий нет».
    assert 'не опрашивается' in bar, bar
    assert 'не отсутствие событий' in bar, bar
    assert 'NASA DONKI' in bar, bar
    tab = next(t for t in at.tabs if t.label == 'Окна и факторы')
    body = '\n'.join(str(m.value) for m in tab.get('markdown'))
    # Проверка была красной ДО правок одиннадцатого круга — это столкновение двух областей на
    # слиянии, а не перестановка блоков. Слой расчёта стал сам класть канал уведомлений в пропуски
    # охвата словами «в текущем режиме НЕ ОПРАШИВАЮТСЯ», и экранная заготовка со словом
    # «не запрашиваются» перестала подставляться: `coverage_scope_ru` не ставит её, когда строка
    # про DONKI уже пришла из расчёта, — иначе на экране стояли бы два сообщения об одном и том же.
    # Утверждение, ради которого проверка написана (находка №9: пропуск данных не равен нулевому
    # риску и объявлен), осталось на экране слово в слово; глагол приведён к одному на весь экран
    # в app/ui.py, а проверка принимает оба написания, чтобы не зависеть от области соседа.
    assert 'DONKI' in body, body[:600]
    assert ('не опрашива' in body or 'не запрашива' in body), body[:600]


def test_chto_delat_dalshe_est_pri_lyubom_verdikte():
    """Сквозное замечание аналитика (находка №10): сервис заканчивался вердиктом и объяснением
    и нигде не говорил, ЧТО ДЕЛАТЬ — что проверить, до какого момента действует условие, когда
    пересчитать. Строка печатается по исходу и берёт сроки из снимка, а не из головы."""
    for mode in MODES:
        at = run_app(mode)
        assert not at.exception, at.exception
        vis = verdict_visible(at)
        assert 'Что дальше:' in vis, (mode, vis)
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.button('preset_gannon').click().run()
    vis = verdict_visible(at)
    assert 'условие действует до' in vis, vis
    assert 'пересчитать после следующего выпуска' in vis, vis


def test_dva_kp_na_kartochke_primireny():
    """Находка №11: на карточке стояли «прогноз Kp NOAA, макс. в окне 3,67» и тут же условие
    «Kp до 9» по адресному уведомлению — два числа из разных выпусков, и никто их не мирил."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.button('preset_gannon').click().run()
    assert not at.exception, at.exception
    cards = [m.value for m in at.markdown if 'class="wcard' in str(m.value)]
    assert cards, 'карточек окон нет'
    assert any('разные величины, а не расхождение расчёта' in c for c in cards), cards[0]
    assert any('по уведомлению, а не по регулярному бюллетеню' in c for c in cards), cards[0]


def test_izmenenie_plana_pokazyvaet_ishod_pereschyota():
    """Находка №12: постановка требует «Система пересчитывает последствия». Пересчёт шёл, а что
    изменилось — не говорилось, и при сдвиге окна плашки не было вовсе."""
    from app.ui import plan_change_ru
    prev = {'duration_min': 360, 'offsets': [0, 480], 'mode': 'history_forecast', 't0': 'x',
            'verdict': 'preferred', 'pref': 'a', 'pref_ru': '25.06 20:00',
            'saa_min': 72, 'fluence': 1.65e6, 'tol_min': 20.0}
    cur = {'duration_min': 120, 'offsets': [0, 480], 'mode': 'history_forecast', 't0': 'x',
           'verdict': 'preferred', 'pref': 'b', 'pref_ru': '25.06 12:00',
           'saa_min': 17, 'fluence': 1.73e5, 'tol_min': 15.0}
    out = plan_change_ru(prev, cur)
    assert 'длительность ВКД 360 → 120 мин' in out, out
    assert '72 → 17 мин' in out, out
    assert '1,65·10⁶ → 1,73·10⁵' in out, out
    assert 'допуск равнозначности — 20 → 15 мин' in out, out
    assert 'было окно 25.06 20:00, стало окно 25.06 12:00' in out, out
    assert 'изменение плана, а не улучшение обстановки' in out, out
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.button('preset_quiet').click().run()
    assert 'Изменение плана' not in verdict_visible(at), verdict_visible(at)
    # Длительность выхода задаётся строкой задачи главной области, а не ползунком боковой панели
    # (одиннадцатый круг, раздел 3.1): проверяется то же самое изменение плана 360 -> 120 мин.
    at.number_input('duration').set_value(120).run()
    assert not at.exception, at.exception
    assert 'Изменение плана' in verdict_visible(at), verdict_visible(at)
    assert 'Пересчёт:' in verdict_visible(at), verdict_visible(at)


def test_otkaz_zadannyy_polzovatelem_otlichim_ot_setevogo():
    """Находка №13: смоделированный отказ источника и настоящий отказ сети печатались одной
    фразой, и на показе ничто не подтверждало, что отказ вызван проверкой, а не сетью (Т6)."""
    at = run_app(MODES[0])
    assert 'вы перевели источник в режим отказа' not in texts(at)
    at.sidebar.selectbox('dis_goes').set_value('отказ: только кеш').run()
    assert not at.exception, at.exception
    body = texts(at)
    assert 'вы перевели источник в режим отказа' in body, body[:600]
    assert 'живой запрос не выполнялся' in body, body[:600]


def test_pribornaya_polosa_arhiva_neytralna_i_nazyvaet_moment():
    """Находка №14: верхняя полоса в «Историческом разборе» показывала Kp 2 и GOES 4,01 ЗЕЛЁНЫМ,
    то есть «спокойно», тогда как весь остальной экран про бурю. Значения относятся к началу
    периода, а окна живут до 22:00; момент стоял в мелкой подписи, крупное число её забивало."""
    at = run_app(MODES[1])
    assert not at.exception, at.exception
    bar = next(m.value for m in at.markdown if 'class="panel"' in m.value)
    assert 'на начало периода' in bar, bar
    for kind, label in re.findall(r'<div class="cell k-(\w+)"><div class="cl">([^<]*)</div>', bar):
        if 'начало периода' in label:
            assert kind != 'obs', (kind, label)     # нейтральный тон, не «наблюдение зелёным»


def test_blok_istochnikov_svyornut_v_odnu_stroku():
    """Находка №15: над ответом стояла жёлтая плашка на четыре пункта, две из которых об ОДНОМ
    источнике элементов орбиты. Бриф §9.7: нет двух сообщений об одном и том же."""
    from app.ui import source_issues_short_ru
    at = run_app(MODES[0])
    assert not at.exception, at.exception
    from app.fetch_guard import LIMIT_MARK
    for w in at.warning:
        v = str(w.value)
        if 'Состояние источников' not in v or LIMIT_MARK in v:
            continue        # общий предел получения источников не сворачивается: причина важнее
        # Либо строка одна и без перечня, либо перечень свёрнут в одну строку «Источники: …».
        assert v.count('\n- ') <= 1, v
        if v.count('\n- ') == 0:
            assert 'Источники:' in v, v
    out = source_issues_short_ru(['GOES ≥10 МэВ: живого ответа нет, взят кеш, давность 14 мин — объявлено',
                                  'Kp (GFZ): живого ответа нет, взят кеш, давность 154 мин — объявлено'])
    assert 'GOES ≥10 МэВ 14 мин' in out and 'Kp (GFZ) 154 мин' in out, out
    assert ' ,' not in out, out


def test_metki_goes_razvedeny_i_kp_est_v_zhivom_rezhime():
    """Находка №17: под одной меткой «GOES ≥10 МэВ, pfu» на экране стояли три разных ответа
    (полоса и две карточки), а строки «прогноз Kp NOAA, макс. в окне» в живом режиме не было
    вовсе — в оперативном режиме по окну было видно МЕНЬШЕ, чем в разборе."""
    at = run_app(MODES[0])
    assert not at.exception, at.exception
    bar = next(m.value for m in at.markdown if 'class="panel"' in m.value)
    cards = [m.value for m in at.markdown if 'class="wcard' in str(m.value)]
    assert 'GOES ≥10 МэВ сейчас, pfu' in bar, bar
    assert cards, 'карточек окон нет'
    for c in cards:
        assert 'GOES ≥10 МэВ на окно, pfu' in c, c
        assert 'прогноз Kp NOAA, макс. в окне' in c, c


def test_usloviye_na_kartochke_nazyvaet_srok_deystviya():
    """Находка №19: короткая форма условия резалась по первой точке с запятой, и с экрана уходил
    срок действия — ровно то, что нужно аналитику, чтобы понять, выводит ли сдвиг окно из-под
    условия. Теперь она собирается из полей Condition, а не выкусыванием из строки."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.button('preset_gannon').click().run()
    assert not at.exception, at.exception
    cards = [m.value for m in at.markdown if 'class="wcard' in str(m.value)]
    assert cards, 'карточек окон нет'
    assert any('действует до 11.05 13:03 UTC' in c for c in cards), cards[0]


def test_dopusk_po_flyuensu_obyasnyon_na_operativnom_urovne():
    """Находка №20: допуск равнозначности по флюенсу ×1,50 решает вердикт, а на оперативном
    уровне печатался числом без происхождения; фактическое отношение пары не называлось вовсе."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.button('preset_quiet').click().run()
    assert not at.exception, at.exception
    fold = verdict_folded(at)
    assert 'Допуск по флюенсу ×1,50' in fold, fold
    assert 'Отношение флюенсов сравниваемых окон на рабочем канале' in fold, fold


def test_pereklyuchatel_okon_otkryvaetsya_na_predpochtitelnom():
    """Находка №22: вкладка «Объяснения» всегда открывалась на окне 1, даже когда вердикт
    рекомендует окно 2. Именно этот шаг проверяет О4."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.button('preset_quiet').click().run()
    assert not at.exception, at.exception
    r = at.radio('cards_win')
    pref = [i for i, o in enumerate(r.options) if 'предпочтительное' in o]
    assert pref, r.options
    assert r.options.index(r.value) == pref[0], (r.value, r.options)


def test_tablica_porogov_nazyvaet_nepodklyuchyonnyy_mehanizm():
    """Находка №23: в таблице действующих порогов стояла строка о сближениях со ссылкой на
    CelesTrak SOCRATES, хотя данных SOCRATES нет ни в одном режиме, а строка охвата двумя блоками
    ниже честно писала «Не учтено: сближения SOCRATES — нет данных»."""
    from app.ui import RULE_THRESHOLDS, RULE_THRESHOLDS_NOTE
    assert all('состояние' in r for r in RULE_THRESHOLDS), RULE_THRESHOLDS
    soc = next(r for r in RULE_THRESHOLDS if 'SOCRATES' in r['источник'])
    assert soc['состояние'].startswith('не применяется'), soc
    assert all(r['состояние'] == 'применяется' for r in RULE_THRESHOLDS if 'SOCRATES' not in r['источник'])
    at = run_app(MODES[1])
    assert RULE_THRESHOLDS_NOTE in texts(at), 'подписи о непринятых строках нет'


def test_stress_scenariy_obyasnyon_potrebnostyu_i_primerom():
    """Находка №24: главная дополнительная функция не была объяснена ничем — ни потребности,
    ни связи с основным сценарием, ни примера. О7 оценивает ровно это."""
    at = run_app(MODES[0])
    caps = ' '.join(str(c.value) for c in at.sidebar.caption)
    assert 'Зачем: проверить, устоит ли выбор окна' in caps, caps
    assert '25.06.2024 при Kp 7' in caps, caps


def test_istochnik_formuly_9_zavisit_ot_urovnya():
    """Находка №25: вкладка «Методика» видна на оперативном уровне, а «Устойчивость и нормы» —
    только на профессиональном. Формула (9) отсылала пользователя к вкладке, которой у него нет."""
    from app.ui import method_source_ru
    b9 = next(b for b in METHOD_BLOCKS if b['no'] == 9)
    assert method_source_ru(b9, pro=True) == b9['source']
    op = method_source_ru(b9, pro=False)
    assert 'строке «Как это посчитано»' in op, op
    assert 'на профессиональном уровне' in op, op
    at = run_app(MODES[1])
    assert op in texts(at), 'источник формулы (9) на оперативном уровне не переписан'


def test_zagolovok_kartochki_bez_pustogo_procherka():
    """Находка №27: заголовок свёртки собирался как «уровень · окно · название», и у информационных
    карточек уровень равен «—». Вкладка начиналась четырьмя строками «— · …»."""
    from app.ui import SEV_RU
    assert SEV_RU['info'] == ''
    at = run_app(MODES[1])
    assert not at.exception, at.exception
    labels = [str(e.label) for e in at.expander]
    assert labels, 'свёрток на экране нет'
    assert not any(x.startswith('— ·') for x in labels), labels


def test_flyuens_pechataetsya_odnim_vidom():
    """Находка №28: fmt(88701)='88701', а fmt(173159.77)='1,73·10⁵' — одна и та же величина
    на одном экране двумя видами: «флюенс ниже (88701 против 2,22·10⁶ част./см²)»."""
    from app.ui import fluence_sci_ru, fmt_fluence, screen_text
    assert fmt_fluence(88701.0) == '8,87·10⁴'
    assert fmt_fluence(42936.0) == '4,29·10⁴'
    assert fmt_fluence(173159.77) == '1,73·10⁵'
    assert fmt_fluence(61.59) == '61,59'
    assert fmt_fluence(None) == '—'
    assert fluence_sci_ru('флюенс 57605 част./см²') == 'флюенс 5,76·10⁴ част./см²'
    out = screen_text('флюенс ниже (61,59 против 57605 част./см²)')
    assert '5,76·10⁴' in out and '57 605' not in out, out
    assert '24' in screen_text('порог 24000 нТл') and '2,40·10⁴' not in screen_text('порог 24000 нТл')


def test_lenta_vremeni_vidna_na_operativnom_urovne():
    """Находка №33: постановка перечисляет, что должно быть ВИДНО, и «временная картина выбранных
    воздействий» стоит в этом перечне; на оперативном уровне она была свёрнута.

    Одиннадцатый круг: владелец назвал этот график «странным и нерезультативным», а таблица 3.0
    техзадания отдаёт главный экран ответу. График не выброшен и не свёрнут — он стоит первым во
    вкладке «Наблюдения и прогнозы», рядом с наблюдениями, из которых построен, и открыт сразу.
    Проверка требует того же, ради чего была написана: график на месте и не спрятан за клик
    свёртки; изменилось только то, что он больше не занимает главный экран.
    """
    at = run_app(MODES[1])
    assert not at.exception, at.exception
    tab = next(t for t in at.tabs if t.label == 'Наблюдения и прогнозы')
    assert any('Картина по времени' in str(s.value) for s in tab.get('subheader')), 'графика нет во вкладке'
    assert not [e for e in at.expander if 'Картина по времени' in str(e.label)], \
        'график не должен лежать в свёртке'
    caps = ' '.join(str(c.value) for c in tab.get('caption'))
    assert 'в аномалии' in caps, caps           # подпись про то, как читать, осталась при графике


def test_sluzhebnaya_podpis_o_minutah_odna_na_pare_kartochek():
    """Находка №34: «минуты в аномалии: |B| < 24 000 нТл…» печаталось под КАЖДЫМ окном, а сразу
    под карточками шла 330-значная легенда о цветах, которую читают один раз за сессию."""
    from app.ui import saa_note_ru
    assert 'всех окон' in saa_note_ru(24000.0)
    assert saa_note_ru(None) == ''
    at = run_app(MODES[1])
    assert not at.exception, at.exception
    cards = [m.value for m in at.markdown if 'class="wcard' in str(m.value)]
    assert cards and all('формула (3)' not in c for c in cards), cards[0]
    assert sum(1 for m in at.markdown if 'Минуты в аномалии у всех окон' in str(m.value)) == 1


def test_meteoroidy_v_chelovecheskom_masshtabe_i_posledney_strokoy():
    """Находка №31: «метеороиды, попаданий на 1 м² 5,64·10⁻⁷» — число, которое не с чем
    сопоставить и которое по построению одинаково у обоих окон, стояло сразу под флюенсом,
    как равноправная величина выбора."""
    from app.ui import mmod_scale_ru
    out = mmod_scale_ru(SimpleNamespace(name='ожидаемое число попаданий, пластина 1 м²', value=5.64e-7), 360)
    assert '5,64·10⁻⁷' in out and 'раз в ~' in out and 'лет' in out, out
    assert mmod_scale_ru(None, 360) == '—'
    at = run_app(MODES[1])
    cards = [m.value for m in at.markdown if 'class="wcard' in str(m.value)]
    assert cards, 'карточек окон нет'
    c = _strip_tags(cards[0])
    assert 'метеороиды, попаданий в пластину 1 м² за окно' in c, c
    assert c.index('флюенс') < c.index('метеороиды'), c


def test_tle_origin_ne_vypuskaet_adres_i_angliyskoe_slovo():
    """Находка владельца №2: в ячейке «Элементы орбиты» стояли две давности в разных единицах,
    десятичная точка вместо запятой и английское `timeout` с голым адресом и параметрами."""
    from app.ui import tle_origin
    s = ('получено по сети, давность данных 2117.4 мин; проверка адресов: '
         'https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE: timeout')
    out = tle_origin(s)
    assert 'http' not in out and 'timeout' not in out and 'CATNR' not in out, out
    assert '2117' not in out and '.' not in out, out
    assert 'резервный' in out, out
    at = run_app(MODES[0])
    bar = next(m.value for m in at.markdown if 'class="panel"' in m.value)
    cell = re.search(r'<div class="cl">Элементы орбиты</div>.*?<div class="cs">([^<]*)</div>', bar, re.S)
    if cell:
        assert 'http' not in cell.group(1) and 'timeout' not in cell.group(1), cell.group(1)
        # Подпись ячейки сокращена до одной строки (разгрузка главного экрана): давность в ней
        # либо одна, либо уехала в раскрытие «Подробнее о каждой величине полосы». Проверяемое
        # утверждение прежнее: ДВУХ давностей в разных единицах в одной ячейке быть не должно.
        assert cell.group(1).count('давность') <= 1, cell.group(1)


def test_nehvatka_pokrytiya_nazyvaet_sledstvie():
    """Находка владельца №3: «GOES: наблюдение 08:20 покрывает 0 % окна, прогноза потока протонов
    на окно нет» — сказано, чего нет, но не сказано, что из этого следует."""
    from app.ui import coverage_consequence_ru
    assert coverage_consequence_ru([]) == ''
    assert 'по протонному потоку окно не оценивается' in coverage_consequence_ru(['что-то'])
    at = run_app(MODES[0])
    at.sidebar.selectbox('dis_goes').set_value('исключён: нет данных').run()
    assert not at.exception, at.exception
    body = _strip_tags(verdict_html(at))
    assert 'Следствие:' in body, body
    assert 'рекомендации не будет' in body, body


def test_dve_veroyatnosti_noaa_nazvany_odnim_pokazatelem():
    """Находка №32: две строки таблицы 1 показывают одну и ту же величину NOAA из двух выпусков.
    Постановка предупреждает: «Два сайта, перепечатывающих одно измерение, не считаются
    независимым подтверждением»."""
    from app.ui import forecast_label_ru
    assert 'трёхсуточный бюллетень' in forecast_label_ru({'channel': 's1_prob_daily'})
    assert 'суточный выпуск' in forecast_label_ru({'channel': 'proton_prob_daily'})
    assert forecast_label_ru({'channel': 'kp_forecast', 'label': 'прогноз Kp'}) == 'прогноз Kp'
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.button('preset_gannon').click().run()
    assert not at.exception, at.exception
    assert 'один показатель NOAA' in texts(at), texts(at)[:800]


# ================================================================= одиннадцатый круг: задача → ответ
# Экран отвечает на вопрос человека «нам надо выйти», а не требует от него расставить окна.
# Порядок блоков главной области — таблица 3.0 техзадания, и он проверяется дампом, а не на глаз.
BLOCK_MARKS = [('ВКД-Риск', 'заголовок'),
               ('Выход на', 'строка задачи'),
               ('class="reco', 'рекомендация'),
               ('class="verdict', 'рекомендация'),
               ('Где и когда', 'глобус'),
               ('Лента окон', 'лента окон'),
               ('Что учтено и что нет', 'что учтено'),
               ('Состояние источников: чем считали', 'состояние источников')]


def main_blocks(at: AppTest) -> list[str]:
    """Блоки главной области сверху вниз — по ним сверяется таблица 3.0 техзадания.

    Обход идёт по дереву элементов в порядке показа. Внутрь свёртки ручного разбора и внутрь
    вкладок не заходим: их содержимое — часть своего блока, а не отдельные блоки.
    """
    out: list[str] = []

    def add(name: str) -> None:
        if not out or out[-1] != name:
            out.append(name)

    def walk(node) -> None:
        kids = getattr(node, 'children', None) or {}
        for e in (kids.values() if isinstance(kids, dict) else kids):
            label = str(getattr(e, 'label', '') or '')
            ty = str(getattr(e, 'type', '') or '')
            if 'Разобрать конкретные окна' in label:
                add('разбор окон')
                continue
            if 'tab' in ty.lower():
                add('вкладки')
                continue
            if hasattr(e, 'children'):
                walk(e)
                continue
            try:
                v = str(getattr(e, 'value', '') or '')
            except Exception:                              # noqa: BLE001 — значение таблицы не строка
                v = ''
            for mark, name in BLOCK_MARKS:
                if mark in v:
                    add(name)
                    break

    walk(at.main)
    return out


# Ровно та последовательность, что записана таблицей 3.0 техзадания одиннадцатого круга.
EXPECTED_BLOCKS = ['заголовок', 'строка задачи', 'рекомендация', 'глобус', 'лента окон', 'что учтено',
                   'состояние источников', 'разбор окон', 'вкладки']


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('pro', [False, True])
def test_poryadok_blokov_po_tablice_3_0(mode, pro):
    """Одиннадцатый круг, раздел 3.0: порядок блоков жёсткий и одинаков на обоих уровнях."""
    at = run_app(mode, pro)
    assert not at.exception, at.exception
    assert main_blocks(at) == EXPECTED_BLOCKS, main_blocks(at)


@pytest.mark.parametrize('mode', MODES)
def test_stroka_zadachi_stoit_v_glavnoy_oblasti(mode):
    """Раздел 3.1: человек задаёт свой вопрос первым делом — длительность, срок и кнопка стоят
    в главной области, а не в боковой панели, и ползунков сдвига рядом с ними нет."""
    at = run_app(mode)
    assert not at.exception, at.exception
    assert at.number_input('duration').value == 360
    assert at.number_input('search_h').value == 12
    assert any('Найти окна' in str(b.label) for b in at.button), [str(b.label) for b in at.button]
    assert not [s for s in at.sidebar.slider if str(s.label).startswith('Сдвиг начала окна')], \
        'ползунки сдвига окон должны уйти из боковой панели в раздел ручного разбора'
    caps = ' '.join(str(c.value) for c in at.caption)
    # Строка задачи сокращена до одной приглушённой строки: те же три факта, но без повторов.
    assert 'режим:' in caps and 'Выход на' in caps, caps[:300]


def test_bez_perebora_ekran_govorit_chto_perebora_ne_bylo():
    """Договор раздела 1a: ключа `scan` в снимке нет — экран показывает прежний разбор окон и
    честно говорит, что перебор не выполнялся. Выдуманных чисел на месте рекомендации нет."""
    from app.ui import scan_of
    assert scan_of({}) is None
    assert scan_of({'scan': None}) is None
    assert scan_of({'scan': {'candidates': []}}) is None, 'пустой перебор договором не допускается'
    at = run_app(MODES[1])
    assert not at.exception, at.exception
    body = texts(at)
    # Формулировки сокращены при разгрузке главного экрана; утверждения те же.
    assert 'Перебор начал не выполнялся' in body, body[:400]
    assert 'Ленты нет: перебор начал не выполнялся' in body, body[:400]
    assert any('class="verdict' in m.value for m in at.markdown), 'вердикт по окнам обязан остаться'


_NOTSET = object()


def _scan_fixture(t0, n=7, step=10, duration=240, verdict='recommended', conditions=(),
                  best=None, recommended_index=None, answer_kind=_NOTSET, answer_span=None):
    """Искусственный снимок перебора ровно той формы, что записана в разделе 1a техзадания.

    По умолчанию — ответ ТОЧКОЙ: один кандидат в лучшей группе и заполненный `recommended_index`.
    На живых данных так бывает редко (по замерам движка — 12 прогонов из 110), поэтому исходы
    «промежуток», «спор величин», «проверка аналитиком» и «отказ» собираются здесь же, своими
    значениями `best`, `recommended_index` и `verdict`.
    """
    cands = []
    for k in range(n):
        s = t0 + timedelta(minutes=step * k)
        cands.append({'start_utc': s.isoformat(), 'end_utc': (s + timedelta(minutes=duration)).isoformat(),
                      'saa_min': 53.0 - 4.0 * k, 'fluence': 2.4e6 - 2.5e5 * k, 'mmod_hits': 5.64e-7,
                      'coverage': 'partial', 'conditions': list(conditions) if k == n - 1 else [],
                      'rank': n - k, 'group': 1 if k == n - 1 else 2})
    return {'requested_duration_min': duration, 'search_from_utc': t0.isoformat(),
            'search_to_utc': (t0 + timedelta(hours=12)).isoformat(), 'step_min': step, 'n_candidates': n,
            'rule': 'не хуже по обеим величинам и строго лучше хотя бы по одной',
            'tolerance_note': 'допуск равнозначности 20 мин по минутам и отношение 1,5 по флюенсу',
            'candidates': cands, 'best': list(best) if best is not None else [n - 1],
            'recommended_index': (n - 1) if (recommended_index is None and best is None) else recommended_index,
            'verdict': verdict,
            'scope': 'сравнение сделано по минутам в аномалии и флюенсу захваченных протонов',
            'why': 'наименьшее воздействие из 7 проверенных начал: 29 мин в аномалии против 53 у худшего',
            # `answer_kind` кладётся только когда его задали: снимок БЕЗ этого ключа проверяет
            # прежний разбор (движок ещё не слит), снимок С ключом — договор движка.
            **({} if answer_kind is _NOTSET else {'answer_kind': answer_kind, 'answer_span': answer_span})}


def test_rekomendaciya_iz_perebora_krupno_i_s_chislami():
    """Раздел 3.2: когда перебор есть, на месте ответа стоит «Выходить …», одна фраза «почему»
    с числами, объявленная область вывода и число перебранных начал."""
    from app.ui import recommendation_panel
    t0 = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    html_ = recommendation_panel(_scan_fixture(t0), {'coverage_missing': []}, duration_min=240)
    txt = _strip_tags(html_)
    # «19.09 в 13:00 UTC» — так человек и говорит; час без даты читался бы как «сегодня»,
    # а зона названа прямо в самой заметной строке экрана (раздел 3.2 техзадания).
    assert 'Выходить 19.09 в 13:00 UTC' in txt, txt
    assert '240 мин' in txt, txt
    assert 'Перебрано 7 начал с шагом 10 мин' in txt, txt
    assert 'Область вывода:' in txt, txt
    assert 'Условий проверки у рекомендованного окна нет' in txt, txt
    assert txt.count('(') == txt.count(')'), txt
    # На профессиональном уровне названо, один кандидат в лучшей группе или несколько: множество
    # равнозначных — повод проверить допуски, а не признак хорошей обстановки.
    pro_txt = _strip_tags(recommendation_panel(_scan_fixture(t0), {}, pro=True, duration_min=240))
    assert 'В лучшей группе 1 из 7 перебранных начал' in pro_txt, pro_txt
    # Т6 и находка №4 десятого круга: там, где различающие величины не посчитаны, «условий нет» —
    # неправда, условию просто неоткуда взяться. Плашка серая в обоих случаях, но говорит разное.
    # Поле coverage кандидата считается только по каналам ранжирования, и читать его шире нельзя.
    sc = _scan_fixture(t0)
    sc['candidates'][-1]['coverage'] = 'none'
    no_cov = _strip_tags(recommendation_panel(sc, {}, duration_min=240))
    assert 'которые различают начала, на это окно не посчитаны' in no_cov, no_cov
    assert 'не означает отсутствия воздействия' in no_cov, no_cov


def test_otkaz_perebora_nazyvaet_prichinu_i_chto_nuzhno():
    """Раздел 3.2: при отказе на том же месте крупно стоит причина и что нужно, чтобы он снялся.
    Структурный пробел не закрывается ожиданием нового выпуска — так решено владельцем 19.09."""
    from app.ui import recommendation_panel, refusal_lift_ru
    t0 = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    sc = _scan_fixture(t0, verdict='insufficient')
    sc['recommended_index'] = None
    txt = _strip_tags(recommendation_panel(sc, {}, missing_ru=['наблюдение GOES устарело'], mode='live'))
    assert 'Оснований для рекомендации недостаточно' in txt, txt
    assert 'Чтобы отказ снялся' in txt, txt
    assert 'не существует ни у одного источника' in txt, txt
    assert 'Выходить' not in txt, txt
    need = refusal_lift_ru('all_need_check', [], 'live')
    assert 'решение аналитика' in need and 'нештатная' in need, need


def test_lenta_okon_dva_grafika_bez_bezrazmernyh_ballov():
    """Раздел 3.3: два графика делят ось времени, у обоих подпись «ниже — лучше»; ни третьей оси,
    ни нормировки, ни безразмерного балла."""
    from app.ui import ribbon_caption_ru, scan_best_rows, windows_ribbon
    t0 = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    sc = _scan_fixture(t0)
    fig = windows_ribbon(sc, 30.0)
    ys = [fig.layout[k].title.text for k in ('yaxis', 'yaxis2')]
    assert all(y and 'ниже — лучше' in y for y in ys), ys
    assert 'минут в аномалии' in ys[0] and 'флюенс' in ys[1], ys
    assert fig.layout.xaxis2.title.text and 'врем' in fig.layout.xaxis2.title.text
    body = ' '.join(str(tr.name or '') for tr in fig.data) + ' ' + ribbon_caption_ru(sc, 30.0)
    for bad in ('балл', 'нормиров', 'индекс риска'):
        assert bad not in body, bad
    rows = scan_best_rows(sc)
    assert 1 <= len(rows) <= 5, rows
    assert list(rows[0]) == ['начало выхода', 'минут в аномалии', 'флюенс, част./см²', 'условия проверки']
    assert rows[0]['начало выхода'] == '19.09 13:00', rows[0]


def test_chto_uchteno_nazyvaet_edinicu_proishozhdenie_i_istochnik():
    """Раздел 3.4: у каждой величины окна — единица, происхождение плашкой и источник; отдельно
    перечень неучтённого с причинами, обычными словами."""
    from app.ui import accounted_lines_from_scan, not_accounted_ru
    t0 = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    lines = accounted_lines_from_scan(_scan_fixture(t0)['candidates'][-1], 30.0)
    assert len(lines) == 3, lines
    assert all(x.startswith('- **') and 'pill-calc' in x for x in lines), lines
    assert any('мин' in x for x in lines) and any('част./см²' in x for x in lines), lines
    out = not_accounted_ru({'coverage_missing': ['сближения SOCRATES — нет данных'],
                            'events_line': {'connected': False, 'source_ru': 'уведомления NASA DONKI',
                                            'reason_ru': 'источник не опрашивается'}}, 'live')
    assert out[0] == 'сближения SOCRATES — нет данных', out
    assert any('прогноза потока с разрешением по окну не существует' in x for x in out), out
    assert any('доза на человека' in x for x in out), out
    # одно сообщение об одном и том же: линия уведомлений называется один раз (бриф §9.7)
    dup = not_accounted_ru({'coverage_missing': ['события и уведомления NASA DONKI не опрашиваются'],
                            'events_line': {'connected': False, 'source_ru': 'уведомления NASA DONKI',
                                            'reason_ru': 'источник не опрашивается'}}, 'live')
    assert sum(1 for x in dup if 'DONKI' in x) == 1, dup


def test_ekran_s_pereborom_risuet_rekomendaciyu_lentu_i_tablicu(monkeypatch):
    """Тот же экран на снимке С ключом `scan`: рекомендация из перебора, лента окон двумя
    графиками, таблица лучших и вердикт по вручную заданным окнам — в разделе разбора."""
    import app.compute as compute
    real_run = compute.run

    def fake_run(*a, **kw):
        r = real_run(*a, **kw)
        t0 = datetime.fromisoformat(r.S['request']['t0_utc'])
        r.S['scan'] = _scan_fixture(t0, duration=int(r.S['request']['duration_min']))
        return r

    monkeypatch.setattr(compute, 'run', fake_run)
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.radio('mode').set_value(MODES[1]).run()
    assert not at.exception, at.exception
    assert main_blocks(at) == EXPECTED_BLOCKS, main_blocks(at)
    body = texts(at)
    assert 'Выходить ' in body, body[:500]
    assert 'Перебрано 7 начал с шагом 10 мин' in body, body[:500]
    assert 'ниже — лучше' in body, body[:500]
    assert main_charts(at) == 1, 'лента окон — один рисунок из двух графиков, других на главном нет'
    assert any('class="verdict' in m.value for m in at.markdown), 'вердикт по окнам обязан остаться на экране'
    assert 'Таблица лучших начал' in body, body[:500]


def test_chto_dalshe_ne_obeshchaet_vypuska_pri_strukturnom_probele():
    """Смежная находка решения владельца 19.09: при структурном пробеле строка «Что дальше»
    обещала новый выпуск источника. Прогноза потока протонов с разрешением по окну не существует
    ни у одного источника, и сколько ни ждать выпуска NOAA, покрытие не появится: строка обязана
    называть причину и предлагать посильное."""
    from app.ui import structural_gap
    assert structural_gap({'request': {'disabled': {}}}, 'live', ['нет прогноза потока протонов на окно'])
    assert not structural_gap({'request': {'disabled': {'goes': 'off'}}}, 'live', ['GOES исключён'])
    assert not structural_gap({'request': {'disabled': {}}}, 'history_review', ['GOES'])
    at = run_app(MODES[0])
    assert not at.exception, at.exception
    vis = verdict_visible(at)
    if 'Оснований для рекомендации недостаточно' not in vis:
        pytest.skip('на живых данных этого прогона вердикт не отказ — проверять структурный пробел не на чем')
    assert 'Следующий выпуск источника этот пробел не закроет' in vis, vis
    assert 'или новый выпуск' not in vis, vis
    assert 'перейти в исторический разбор' in vis, vis


def test_otvet_pereborom_chashche_promezhutok_chem_tochka():
    """Ответ перебора почти всегда ПРОМЕЖУТОК, а не точка: по замерам движка на 110 датах архива
    недоминируемых кандидатов медиана шесть, единственный кандидат — лишь в 12 прогонах, а в 43
    случаях из 55 лучшая группа это ПОДРЯД идущие начала. Пустой `recommended_index` при исходе
    «равнозначны» — нормальный успешный случай, и рисовать на его месте отказ нельзя."""
    from app.ui import recommendation_panel, scan_answer
    t0 = datetime(2026, 9, 19, 23, 30, tzinfo=timezone.utc)
    # подряд идущие начала → один промежуток
    sc = _scan_fixture(t0, n=13, step=10, verdict='equivalent', best=[0, 1, 2, 3, 4, 5])
    assert scan_answer(sc)['kind'] == 'interval', scan_answer(sc)
    txt = _strip_tags(recommendation_panel(sc, {}, duration_min=240))
    assert 'Выходить в промежутке 19.09 23:30 — 20.09 00:20 UTC' in txt, txt
    assert 'неразличимы в пределах чувствительности модели' in txt, txt
    assert 'начало в этих границах' in txt, txt
    for bad in ('Оснований для рекомендации недостаточно', 'Чтобы отказ снялся', 'Рекомендации нет'):
        assert bad not in txt, (bad, txt)
    # один кандидат → ответ точкой
    one = _scan_fixture(t0, n=13, step=10, verdict='equivalent', best=[4])
    assert scan_answer(one)['kind'] == 'point', scan_answer(one)
    assert 'Выходить 20.09 в 00:10 UTC' in _strip_tags(recommendation_panel(one, {}, duration_min=240))
    # разрыв в лучшей группе → спор величин, а не промежуток
    gap = _scan_fixture(t0, n=13, step=10, verdict='equivalent', best=[0, 9])
    assert scan_answer(gap)['kind'] == 'tradeoff', scan_answer(gap)
    dis = _strip_tags(recommendation_panel(gap, {}, duration_min=240))
    assert 'Выбор между окнами сервис не делает' in dis, dis
    assert 'выбор за аналитиком' in dis, dis
    assert 'Чтобы отказ снялся' not in dis, dis


def test_otkaz_tolko_pri_nedostatke_osnovaniy():
    """Отказ рисуется ТОЛЬКО при исходе «оснований недостаточно». «Все начала требуют проверки» —
    не отказ: обстановка нештатная, условие стоит у каждого начала, решает аналитик."""
    from app.ui import recommendation_panel, scan_answer
    t0 = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    chk = _scan_fixture(t0, verdict='all_need_check', best=[])
    assert scan_answer(chk)['kind'] == 'check', scan_answer(chk)
    txt = _strip_tags(recommendation_panel(chk, {}, mode='live'))
    assert 'Все начала требуют проверки аналитиком' in txt, txt
    assert 'Чтобы отказ снялся' not in txt, txt
    assert 'решение аналитика' in txt, txt
    no = _scan_fixture(t0, verdict='insufficient', best=[])
    assert scan_answer(no)['kind'] == 'none', scan_answer(no)
    ref = _strip_tags(recommendation_panel(no, {}, missing_ru=['наблюдение GOES устарело'], mode='live'))
    assert 'Оснований для рекомендации недостаточно' in ref, ref
    assert 'Чтобы отказ снялся' in ref, ref
    assert 'Выходить' not in ref, ref


def test_ekran_s_promezhutkom_pokazyvaet_otvet_a_ne_pustotu(monkeypatch):
    """Тот же экран на снимке, где перебор ответил ПРОМЕЖУТКОМ и `recommended_index` пуст:
    на месте ответа стоит промежуток, а не отказ и не пустое место."""
    import app.compute as compute
    real_run = compute.run

    def fake_run(*a, **kw):
        r = real_run(*a, **kw)
        t0 = datetime.fromisoformat(r.S['request']['t0_utc'])
        r.S['scan'] = _scan_fixture(t0, n=13, step=30, duration=int(r.S['request']['duration_min']),
                                    verdict='equivalent', best=[2, 3, 4])
        return r

    monkeypatch.setattr(compute, 'run', fake_run)
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    at.sidebar.radio('mode').set_value(MODES[1]).run()
    assert not at.exception, at.exception
    assert main_blocks(at) == EXPECTED_BLOCKS, main_blocks(at)
    body = texts(at)
    assert 'Выходить в промежутке' in body, body[:600]
    assert 'Оснований для рекомендации недостаточно' not in body.split('Разобрать конкретные окна')[0], body[:600]
    assert 'Величины самого раннего начала из рекомендованного промежутка' in body, body[:600]


def test_dogovor_answer_kind_chetyre_znacheniya():
    """Договор движка: `answer_kind` принимает 'point', 'interval', 'tradeoff' и ПУСТО.

    Пустое значение означает «ответа нет вовсе» — 15 прогонов из 110 на архиве. Без него при
    вердикте «все начала требуют проверки» пришло бы 'interval', и над окнами, каждое из которых
    требует решения аналитика, встало бы крупное «Выходить в промежутке»: ровно то утверждение,
    которое опровергается числами рядом с ним.
    """
    from app.ui import recommendation_panel, scan_answer
    t0 = datetime(2026, 5, 4, 1, 0, tzinfo=timezone.utc)
    span = {'from_utc': (t0 + timedelta(minutes=30)).isoformat(),
            'to_utc': (t0 + timedelta(minutes=80)).isoformat()}
    iv = _scan_fixture(t0, n=60, step=10, verdict='equivalent', best=[0, 1, 38, 39, 40, 41, 48, 49, 50],
                       recommended_index=None, answer_kind='interval', answer_span=span)
    ans = scan_answer(iv)
    assert ans['kind'] == 'interval', ans
    # Промежуток берётся из `answer_span` КАК ЕСТЬ: по крайним элементам `best` он тянулся бы
    # через разрыв и утверждал бы равнозначность начал, которые правило отбросило.
    assert ans['from'] == datetime.fromisoformat(span['from_utc']), ans
    assert ans['to'] == datetime.fromisoformat(span['to_utc']), ans
    txt = _strip_tags(recommendation_panel(iv, {}, duration_min=240))
    assert 'Выходить в промежутке 04.05 01:30 — 02:20 UTC' in txt, txt
    assert 'любое начало в этих границах' in txt, txt        # теми же словами, что строка отчёта
    assert '08:20' not in txt, 'промежуток протянут через разрыв лучшей группы'
    # пустой answer_kind: отказ и «все требуют проверки» различаются по вердикту
    none_chk = _scan_fixture(t0, verdict='all_need_check', best=[], recommended_index=None,
                             answer_kind=None, answer_span=None)
    assert scan_answer(none_chk)['kind'] == 'check', scan_answer(none_chk)
    chk = _strip_tags(recommendation_panel(none_chk, {}, mode='live'))
    assert 'Выходить в промежутке' not in chk and 'Выходить' not in chk, chk
    assert 'Все начала требуют проверки аналитиком' in chk, chk
    none_ref = _scan_fixture(t0, verdict='insufficient', best=[], recommended_index=None,
                             answer_kind=None, answer_span=None)
    assert scan_answer(none_ref)['kind'] == 'none', scan_answer(none_ref)
    # 'point' берёт кандидата по recommended_index
    pt = _scan_fixture(t0, n=60, step=10, verdict='recommended', best=[5], recommended_index=5,
                       answer_kind='point')
    assert scan_answer(pt)['kind'] == 'point'
    assert 'Выходить 04.05 в 01:50 UTC' in _strip_tags(recommendation_panel(pt, {}, duration_min=240))


def test_drob_pered_pfu_tozhe_s_zapyatoy():
    """Найдено дампом чисел экрана: единица «pfu» пишется латиницей, и по прежнему правилу дробь
    перед ней запятой не получала — на экране стояло «GOES ≥10 МэВ = 77.5 pfu» рядом с «207 pfu»
    и «2,43·10⁶ част./см²» того же блока. Прежняя проверка дробей смотрела только на кириллицу
    и знаки после числа, поэтому латинская единица сквозь неё проходила."""
    from app.ui import frac_ru, screen_text
    assert screen_text('окно 2: GOES ≥10 МэВ = 77.5 pfu') == 'окно 2: GOES ≥10 МэВ = 77,5 pfu'
    assert frac_ru('поток 0.19 pfu') == 'поток 0,19 pfu'
    assert screen_text('версия v3.1') == 'версия v3.1'            # номер версии не трогаем
    assert screen_text('дата 01.05.2024') == 'дата 01.05.2024'    # и дату тоже


@pytest.mark.parametrize('mode', MODES)
def test_na_glavnom_ekrane_net_desyatichnoy_tochki_pered_edinicey(mode):
    """Та же находка на живом экране: перед любой единицей, латинской или кириллической,
    десятичный разделитель — запятая (техзадание, раздел 4)."""
    at = run_app(mode)
    assert not at.exception, at.exception
    body = _body_bez_metodiki(at)
    bad = re.findall(r'\d+\.\d+\s*(?:pfu|мин|нТл|МэВ|%|част|км|ч\b|сут)', body)
    assert not bad, bad


def test_velichiny_nazyvayut_edinicu_ili_bezrazmernost():
    """Замечание владельца: «добавь размерности, где они нужны». У величины либо стоит единица,
    либо прямо сказано, что она безразмерная. Kp — худший случай: голое «1,33» не говорит, в чём
    оно измерено, и рядом в той же полосе стоят «pfu» и «нТл»."""
    from app.ui import DIMENSIONLESS_RU, value_with_unit_ru
    kp = SimpleNamespace(name='Kp, последнее наблюдение', unit='', value=1.33, limits_note='')
    assert value_with_unit_ru(kp) == '1,33, ' + DIMENSIONLESS_RU, value_with_unit_ru(kp)
    saa = SimpleNamespace(name='минут в аномалии', unit='мин', value=48.55, limits_note='')
    assert value_with_unit_ru(saa) == '48,55 мин', value_with_unit_ru(saa)
    empty = SimpleNamespace(name='поток протонов GOES ≥10 МэВ', unit='pfu', value=0.2,
                            limits_note='горизонт наблюдения до 04:15Z покрывает 0 % окна')
    assert value_with_unit_ru(empty) == 'значения на окно нет', value_with_unit_ru(empty)
    at = run_app(MODES[0])
    assert not at.exception, at.exception
    bar = next(m.value for m in at.markdown if 'class="panel"' in m.value)
    assert 'безразмерный' in bar, bar          # метка Kp в приборной полосе называет безразмерность
    cards = [m.value for m in at.markdown if 'class="wcard' in str(m.value)]
    assert cards and all('макс. в окне, безразмерный' in c for c in cards), cards[0]


def test_glavnyy_ekran_razgruzhen_i_nichego_ne_poteryano():
    """Замечание владельца: «сделай главную страницу менее загруженной… адекватно с ходу
    воспринимать информацию просто невозможно». Подробности уехали под раскрытия, но остались
    достижимыми: источник каждой величины, перечень неучтённого и полные подписи полосы."""
    at = run_app(MODES[0])
    assert not at.exception, at.exception
    labels = [str(e.label) for e in at.expander]
    for need in ('Откуда взята каждая величина', 'Чего сервис не учёл', 'Подробнее о каждой величине полосы'):
        assert any(need in x for x in labels), (need, labels)
    body = texts(at)
    # прослеживаемость не потеряна: первоисточники и пропуски охвата на экране есть
    assert 'прогноза потока с разрешением по окну не существует' in body, body[:300]
    assert 'Цвет = происхождение' in body or 'Происхождение величин' in body, body[:300]
    # приглушённая подпись ленты окон — одна строка
    from app.ui import ribbon_caption_ru
    cap = ribbon_caption_ru({'candidates': []}, 30.0)
    assert cap.count('.') <= 1 and len(cap) < 160, cap


def test_temnaya_tema_i_grafiki_v_ney():
    """Тёмная тема: палитра одна и означает происхождение величины, контраст проверен расчётом,
    графики Plotly приводятся к тёмной подложке — светлый рисунок на тёмной странице читается
    как поломка."""
    import tomllib
    from app.ui import CSS, DARK_PAPER, dark_figure
    cfg = tomllib.loads(open(os.path.join(os.path.dirname(APP), '..', '.streamlit', 'config.toml'),
                             encoding='utf-8').read())
    assert cfg['theme']['base'] == 'dark', cfg['theme']
    assert cfg['theme']['backgroundColor'] == '#0e1117', cfg['theme']

    def contrast(a, b):
        def lum(h):
            h = h.lstrip('#')
            ch = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
            f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
            return 0.2126 * f(ch[0]) + 0.7152 * f(ch[1]) + 0.0722 * f(ch[2])
        la, lb = lum(a), lum(b)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)

    root = re.search(r':root \{(.*?)\}', CSS, re.S).group(1)
    var = lambda name: re.search(r'--%s:(#[0-9a-f]{6})' % name, root).group(1)
    bg = var('bg')
    for name in ('ink', 'muted', 'calc', 'obs', 'fc', 'cond', 'none'):
        assert contrast(var(name), bg) >= 4.5, (name, contrast(var(name), bg))
        # текст плашки на её собственной заливке — тоже не ниже 4,5:1
        if name in ('calc', 'obs', 'fc', 'cond', 'none'):
            assert contrast(var(name), var(name + '-bg')) >= 4.5, (name, contrast(var(name), var(name + '-bg')))
    fig = dark_figure(go.Figure(go.Scatter(x=[1, 2], y=[1, 2], line={'color': '#1f4e79'})))
    assert fig.layout.paper_bgcolor == DARK_PAPER, fig.layout.paper_bgcolor
    assert fig.data[0].line.color != '#1f4e79', 'тёмный синий светлой темы не читается на тёмном фоне'


def test_vvod_razlozhen_po_klassam_s_ikonkami():
    """Замечание владельца: ввод «не выглядеть как однородная каша слева, распределённая по
    логическим иконкам». Боковая панель разложена на смысловые разделы с материальными иконками;
    эмодзи не используются — нужен вид рабочего решателя, а не детского приложения."""
    src = open(APP, encoding='utf-8').read()
    sections = re.findall(r"st\.expander\('([^']+)', expanded=\w+, icon=':material/([a-z_]+):'\)", src)
    names = [n for n, _ in sections]
    for need in ('Когда считаем', 'Источники данных', 'Проверки и сценарии', 'Пороги и правила', 'Вид'):
        assert need in names, (need, names)
    assert all(icon and '_' not in icon or icon for _, icon in sections), sections
    assert not EMOJI.findall(' '.join(names)), names
    at = run_app(MODES[1])
    assert not at.exception, at.exception
    # элементы управления остались на месте и доступны
    assert at.sidebar.radio('mode') is not None and at.sidebar.radio('level') is not None
    assert at.sidebar.checkbox('sc_sep_on') is not None
    assert any('Длительность выхода и срок задаются строкой задачи' in str(c.value) for c in at.sidebar.caption), \
        [str(c.value) for c in at.sidebar.caption]
