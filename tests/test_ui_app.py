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
  * «исключён», «отказ», стресс-сценарий и три окна считаются без исключений.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from app.ui import dedup_clauses, fmt, frac_ru, spread_offsets, status_ru, sup
from app.viz import GOES_TICKVALS, timeline, window_bars

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app', 'main.py')
TIMEOUT = 300
MODES = ['Текущая обстановка', 'Исторический разбор', 'Прогноз из прошлого']


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
    """Строки таблицы источников вкладки «Данные и выгрузка»."""
    for d in at.dataframe:
        rows = d.value
        rows = rows.to_dict('records') if hasattr(rows, 'to_dict') else list(rows)
        if rows and 'источник' in rows[0]:
            return rows
    return []


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
    ('выпуск 202405100030three_day_forecast от 2024-05-10 00:30Z', 'выпуск от 2024-05-10', 'forecast'),
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
def test_timeline_metki_pravoy_osi_goes():
    t0 = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    obs = [(t0 - timedelta(hours=h), 10.0 ** (h % 3)) for h in range(6, 0, -1)]
    fig = timeline([], [], 24000.0, t0, 360, None, None, [], [], 'live', kp_obs=[], goes_obs=obs, search_min=360)
    axes = [v for k, v in fig.layout.to_plotly_json().items() if k.startswith('yaxis')]
    goes_axis = [a for a in axes if a.get('type') == 'log']
    assert goes_axis, 'правая ось GOES должна быть логарифмической'
    assert list(goes_axis[0]['tickvals']) == GOES_TICKVALS
    assert list(goes_axis[0]['ticktext']) == ['0,1', '1', '10', '100', '1000']


def _fake_assessment(start, saa, flu, mm):
    f = [SimpleNamespace(name='минут в аномалии', value=saa, unit='мин'),
         SimpleNamespace(name='флюенс захваченных протонов ≥30 МэВ', value=flu, unit='част./см²'),
         SimpleNamespace(name='ожидаемое число попаданий, пластина 1 м²', value=mm, unit='шт')]
    mech = SimpleNamespace(mechanism_id='spaceweather', factors=f, needs_check=False)
    return SimpleNamespace(window=SimpleNamespace(start_utc=start, duration_min=360), mechanisms=[mech])


def test_window_bars_podpis_meteoroidov_bez_latinicy():
    t0 = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    fig = window_bars([_fake_assessment(t0, 77.0, 1.27e6, 5.64e-7),
                       _fake_assessment(t0 + timedelta(hours=4), 91.0, 2.2e6, 6.1e-7)])
    ann = [a.text for a in fig.layout.annotations]
    assert any('метеороиды: 5,64·10⁻⁷ попаданий на 1 м²' == t for t in ann), ann
    assert not any(re.search(r'\bN\s*=', t) for t in ann)


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
                'CONTRACT.md', 'config/settings.toml', 'nasa_jsc_oem', 'donki_msg#', 'sep_valid_hours'):
        assert bad not in body, bad
    assert 'из архив ' not in body, 'падеж: «из архива»'


def test_professionalnyy_uroven_pokazyvaet_proishozhdenie():
    at = run_app(MODES[1], pro=True)
    assert not at.exception, at.exception
    rows = '\n'.join(str(r.get('статус', '')) for r in source_rows(at))
    assert 'experiments.stub_history' in rows or 'vkd.history' in rows
    assert 'sha256' in rows


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


def test_tri_okna():
    at = run_app(MODES[1])
    at.sidebar.radio('n_windows').set_value(3).run()
    assert not at.exception, at.exception
    assert at.sidebar.slider('w2') is not None
    cards = [m.value for m in at.markdown if 'class="wcard' in m.value]
    assert len(cards) == 3


# ----------------------------------------------------------------- U1: сжатие периода поиска
def test_szhatie_perioda_ne_ostanavlivaet_ekran():
    at = run_app(MODES[1])
    at.sidebar.slider('search').set_value(360).run()
    at.sidebar.slider('w0').set_value(120).run()
    at.sidebar.slider('w1').set_value(360).run()
    assert not at.error, [e.value for e in at.error]
    at.sidebar.slider('search').set_value(120).run()          # период короче прежнего сдвига
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    assert any('class="verdict' in m.value for m in at.markdown), 'вердикт исчез — экран остановился'
    assert at.sidebar.slider('w0').value != at.sidebar.slider('w1').value
    assert at.sidebar.slider('w1').value == 120
    assert any('сдвиги пересчитаны под период' in c.value for c in at.sidebar.caption)
    assert at.tabs, 'вкладки должны остаться на экране'


def test_odinakovye_sdvigi_preduprezhdenie_bez_ostanovki():
    at = run_app(MODES[1])
    at.sidebar.slider('w0').set_value(at.sidebar.slider('w1').value).run()      # оба окна с одним началом
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    warn = [w.value for w in at.sidebar.warning]
    assert any('одинаков' in w for w in warn), warn
    assert any('считаю по сдвигам' in w for w in warn), warn
    assert any('class="verdict' in m.value for m in at.markdown)
