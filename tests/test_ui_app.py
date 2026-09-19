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
  * S8: новые ключи снимка читаются через .get и их отсутствие экран не роняет.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from app.ui import (METHOD_BLOCKS, age_ru, dedup_clauses, dt_ru, fmt, formula_ref, frac_ru, nbsp_thousands, panel,
                    spread_offsets, status_ru, sup, verification_ru)
from app.viz import GOES_TICKVALS, PLOTLY_CONFIG, timeline

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
def test_timeline_metki_pravoy_osi_goes():
    t0 = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    obs = [(t0 - timedelta(hours=h), 10.0 ** (h % 3)) for h in range(6, 0, -1)]
    fig = timeline([], [], 24000.0, t0, 360, None, None, [], [], 'live', kp_obs=[], goes_obs=obs, search_min=360)
    axes = [v for k, v in fig.layout.to_plotly_json().items() if k.startswith('yaxis')]
    goes_axis = [a for a in axes if a.get('type') == 'log']
    assert goes_axis, 'правая ось GOES должна быть логарифмической'
    assert list(goes_axis[0]['tickvals']) == GOES_TICKVALS
    assert list(goes_axis[0]['ticktext']) == ['0,1', '1', '10', '100', '1000']


def test_timeline_zagolovok_i_legenda_ne_dlinnee_chetyryoh():
    """S6: у ленты есть заголовок «что и откуда», подписи осей с единицами, легенда не длиннее четырёх строк."""
    t0 = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    obs = [(t0 - timedelta(hours=h), 10.0 ** (h % 3)) for h in range(6, 0, -1)]
    kp = [(t0 - timedelta(hours=h + 3), t0 - timedelta(hours=h), 3.0) for h in range(6, 0, -3)]
    ev = [SimpleNamespace(kind_of_event='SEP', event_id='x1', note='протонное событие', is_simulated=False,
                          start_utc=t0 + timedelta(hours=1), valid_from_utc=None)]
    fig = timeline([], [], 24000.0, t0, 360, None, None, ev, [], 'live', kp_obs=kp, goes_obs=obs, search_min=360)
    assert fig.layout.title.text and 'наш расчёт' in fig.layout.title.text
    legend = [tr.name for tr in fig.data if tr.showlegend is not False]
    assert len(legend) <= 4, legend
    axes = fig.layout.to_plotly_json()
    titles = [(v.get('title') or {}).get('text') for k, v in axes.items() if k.startswith(('xaxis', 'yaxis'))]
    assert 'время, UTC' in titles, titles
    assert '|B|, нТл' in titles, titles


def test_plotly_panel_instrumentov_skryta():
    """S6: панель инструментов Plotly скрыта — на защите она только мешает."""
    assert PLOTLY_CONFIG['displayModeBar'] is False


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


# ================================================================= S1: мелкие правки экрана
def test_verification_ru_bez_usloviy_ne_vryot():
    """S1: «условие поставлено в 12:00Z» печатается только там, где условия действительно были."""
    raw = 'условие поставлено в 12:00Z; факт: максимум Kp 2.67, бури Kp ≥ 7 не было'
    assert verification_ru(raw, False).startswith('условий проверки на отсечку не ставилось; факт:')
    assert 'условие поставлено' not in verification_ru(raw, False)
    assert verification_ru(raw, True).startswith('условие поставлено в 12:00Z')
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
    assert at.sidebar.slider('search').value == search
    for i, off in enumerate(offsets):
        assert at.sidebar.slider('w%d' % i).value == off
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
    assert at.sidebar.slider('search').value == 720
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
    assert labels[:3] == ['Объяснения', 'Окна и факторы', 'Методика'], labels
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
    """S6 (И6): на оперативном уровне лента убрана в свёртку, на профессиональном — развёрнута."""
    at_op = run_app(MODES[1], pro=False)
    assert any('Картина по времени' in str(e.label) for e in at_op.expander), [str(e.label) for e in at_op.expander]
    at_pro = run_app(MODES[1], pro=True)
    assert any('Картина по времени' in str(s.value) for s in at_pro.subheader)


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
    assert 'Kp, наблюдение' in bar and 'Kp (архив GFZ)' not in bar, bar
    assert 'уведомление NASA DONKI' in bar, bar
    warns = '\n'.join(w.value for w in at.warning) + '\n'.join(i.value for i in at.info)
    assert warns.count('GOES ≥10 МэВ: численного наблюдения') == 1, warns


def test_pribornaya_polosa_razbora_pokazyvaet_nablyudenie_goes():
    """C3: в разборе численное наблюдение GOES стоит в полосе состояния рядом с Kp."""
    at = run_app(MODES[1])
    assert not at.exception, at.exception
    bar = next(m.value for m in at.markdown if 'class="panel"' in m.value)
    assert 'GOES ≥10 МэВ, pfu' in bar and 'Kp (архив GFZ)' in bar, bar


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
    assert raw_record(store, 'nasa_donki_notification:20240509-AL-001:beef') is None
    assert raw_record({}, 'x') is None and raw_record(None, 'x') is None


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


def test_zhivoy_rezhim_imeet_ssylku_na_pervoistochnik():
    """О4 в режиме «Сейчас»: хотя бы одна ссылка на первоисточник NOAA на экране есть."""
    at = run_app(MODES[0])
    assert not at.exception, at.exception
    body = texts(at)
    assert 'https://' in body, 'в текущем режиме на экране не было ни одной ссылки'
    assert 'services.swpc.noaa.gov' in body, body[:400]


# ================================================================= R4: очередь четвёртого круга
def test_sdvigi_okon_perezhivayut_smenu_perioda_poiska():
    """R4-1: смена периода поиска меняет max_value ползунков, и Streamlit теряет их значения.
    Сдвиги держатся в невиджетном ключе состояния и переживают и сжатие, и расширение периода."""
    at = run_app(MODES[1])
    at.sidebar.slider('search').set_value(1440).run()
    at.sidebar.slider('w0').set_value(120).run()
    at.sidebar.slider('w1').set_value(360).run()
    for period in (720, 360, 1440):
        at.sidebar.slider('search').set_value(period).run()
        assert not at.exception, at.exception
        assert at.sidebar.slider('w0').value == 120, (period, at.sidebar.slider('w0').value)
        assert at.sidebar.slider('w1').value == 360, (period, at.sidebar.slider('w1').value)
    assert any('class="verdict' in m.value for m in at.markdown)


def test_granica_arhiva_i_verdikt_ne_protivorechat():
    """R4-2: предупреждение о границе архива включается по фактическим окнам, а не по всему
    периоду поиска — иначе сверху «рекомендации не будет», а ниже предпочтительное окно."""
    at = run_app(MODES[2])
    at.sidebar.date_input('hist_date').set_value(datetime(2024, 6, 30).date()).run()
    at.sidebar.slider('hist_hour').set_value(12).run()
    at.sidebar.slider('search').set_value(1440).run()
    at.sidebar.slider('w0').set_value(0).run()
    at.sidebar.slider('w1').set_value(120).run()
    assert not at.exception, at.exception
    warn = '\n'.join(w.value for w in at.warning)
    assert 'за границей архива' not in warn, warn
    assert any('class="verdict' in m.value for m in at.markdown)
    at.sidebar.slider('w1').set_value(780).run()          # последнее окно уходит за 01.07.2024
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
    out = rule_ru('п.3–4: окно 2 (20:00Z) лучше по космопогоде (на 21 мин меньше в аномалии), '
                  'не хуже по флюенсу и минутам, линия метеороидов не противоречит')
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
