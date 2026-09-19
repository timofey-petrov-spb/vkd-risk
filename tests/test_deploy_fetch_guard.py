# -*- coding: utf-8 -*-
"""Развёртывание: экран обязан отрисоваться при любом состоянии сети (app/fetch_guard.py).

Причина проверок. На Streamlit Community Cloud служебный адрес отвечал 200, а экран не доходил
до первого рендера. Единственная зависимость первого рендера от внешнего мира — четыре живых
источника подряд (GOES, Kp, цепочка TLE из трёх адресов, бюллетень NOAA). У каждого свой тайм-аут
чтения, общего предела не было, а зависание на разрешении имени (DNS) тайм-аутами requests не
покрывается вовсе.

Что проверяется:
  (а) транспорт спит дольше предела — run() и AppTest заканчиваются по пределу, а не по сну,
      экран отрисован, причина названа в состоянии источников;
  (б) сети нет вовсе (socket.getaddrinfo зависает) — тот же результат;
  (в) исторические режимы не делают НИ ОДНОГО обращения к сети;
  плюс: причина уходит в снимок и в выгрузку как отказ источника; источник, исключённый
  пользователем, чужой причиной не переписывается; ключ настроек читается и проверяется.

Приём подмены. Транспорт и разрешение имени спят на threading.Event, а не на time.sleep:
брошенный поток по концу проверки останавливается сразу и не мешает следующим проверкам
(ровно тот же поток на площадке висит до конца своего запроса, рендеру не мешая).
"""
from __future__ import annotations

import io
import os
import re
import socket
import threading
import time
import zipfile
from datetime import datetime, timezone

import pytest
import requests

import app.fetch_guard as fg
import vkd.config as cfg
import vkd.sources.live_cache as lc
from app.compute import run
from app.export import build_zip

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, 'app', 'main.py')
REASON = 'превышен общий предел получения источников'
SLEEP_S = 60.0           # столько «висит» сеть в проверках: без предела рендер ждал бы её шесть раз
TEST_DEADLINE_S = 2.0    # предел проверок; в config/settings.toml стоит рабочее значение
T_HIST = datetime(2024, 5, 10, 12, tzinfo=timezone.utc)


@pytest.fixture
def freeze(monkeypatch, tmp_path):
    """Пустой кеш источников + остановка брошенных попыток по концу проверки.

    Пустой кеш обязателен: на готовых квитанциях и паузе запросов (attempt.json) слой источников
    до сети не доходит вовсе, и проверять было бы нечего. По той же причине сбрасывается кеш
    экрана (`st.cache_data`): он живёт в процессе, и удачный прогон соседней проверки отдал бы
    этой готовый кортеж источников вместо живого запроса."""
    import streamlit as st
    monkeypatch.setattr(lc, 'DEFAULT_CACHE', tmp_path / 'cache')
    st.cache_data.clear()
    fg.reset_pending()
    stop = threading.Event()
    yield stop
    stop.set()
    th = fg.pending_thread()
    if th is not None:
        th.join(10)
    fg.reset_pending()
    st.cache_data.clear()


def _hanging_transport(stop):
    """Транспорт, который спит дольше предела и только потом объявляет отказ сети."""
    def get(url, **kw):
        stop.wait(SLEEP_S)
        raise requests.ConnectionError('проверка: ответа нет')
    return get


def _hanging_dns(stop, counter):
    """Разрешение имени, которое зависает: тайм-аут requests этот участок не покрывает."""
    def getaddrinfo(*a, **kw):
        counter.append(a[:2])
        stop.wait(SLEEP_S)
        raise socket.gaierror('проверка: имя не разрешается')
    return getaddrinfo


def _settings_with_deadline(tmp_path, monkeypatch, seconds) -> str:
    """Копия рабочих настроек с другим общим пределом: экран читает предел только из файла (Т7)."""
    text = io.open(os.path.join(ROOT, 'config', 'settings.toml'), encoding='utf-8').read()
    text, n = re.subn(r'(?m)^total_deadline_s\s*=.*$', 'total_deadline_s = %s' % seconds, text, count=1)
    assert n == 1, 'ключ total_deadline_s должен стоять в config/settings.toml'
    p = tmp_path / 'settings.toml'
    io.open(p, 'w', encoding='utf-8').write(text)
    monkeypatch.setenv('VKD_SETTINGS', str(p))
    cfg.settings.cache_clear()
    return str(p)


LIVE_SRC = ('noaa_swpc_goes', 'gfz_kp', 'noaa_swpc_3day_forecast')     # элементы орбиты стоят в 'orbit'


def _statuses(S) -> dict:
    return {k: S['sources'][k].get('status') or '' for k in LIVE_SRC}


def _screen(at) -> str:
    return '\n'.join([m.value for m in at.markdown] + [w.value for w in at.warning]
                     + [c.value for c in at.caption] + [e.value for e in at.error])


# ================================================================= (а) транспорт спит дольше предела
def test_a_zavisshiy_transport_ukladyvaetsya_v_obshchiy_predel(freeze, monkeypatch):
    """Получение ВСЕХ живых источников заканчивается по пределу, а не по сну транспорта:
    без предела тот же рендер ждал бы семь адресов по SLEEP_S секунд каждый.

    Число источников не вписывается числом: с двенадцатого круга к ним добавлены уведомления
    NASA DONKI, и проверка сверяется с самим перечнем `fg.KEYS` — иначе она проверяла бы не то,
    что есть, а то, что было."""
    monkeypatch.setattr(lc.requests, 'get', _hanging_transport(freeze))
    t = time.monotonic()
    fetched, note = fg.fetch_live_sources({'goes': False, 'kp': False, 'noaa': False}, deadline_s=TEST_DEADLINE_S)
    dt = time.monotonic() - t
    assert dt < TEST_DEADLINE_S + 1.0, 'получение источников заняло %.1f с при пределе %s с' % (dt, TEST_DEADLINE_S)
    assert note and note.startswith(REASON) and '2 с' in note, note
    assert len(fetched) == len(fg.KEYS)
    for key, item in zip(fg.KEYS, fetched):
        assert REASON in item[-1].status_ru, (key, item[-1].status_ru)
        assert not item[-1].ok, key                       # живого ответа нет и он таким не назван


def test_a_raschyot_zakanchivaetsya_po_predelu(freeze, monkeypatch, tmp_path):
    """run() без переданных источников берёт предел из настроек и заканчивается по нему."""
    _settings_with_deadline(tmp_path, monkeypatch, TEST_DEADLINE_S)
    monkeypatch.setattr(lc.requests, 'get', _hanging_transport(freeze))
    t = time.monotonic()
    r = run('live', datetime.now(timezone.utc).replace(second=0, microsecond=0), 60, 60, [0, 60])
    dt = time.monotonic() - t
    assert dt < SLEEP_S, 'расчёт ждал сеть (%.1f с), а не предел' % dt
    assert dt < TEST_DEADLINE_S + 20, 'расчёт занял %.1f с' % dt     # предел плюс сам расчёт
    assert r.S['windows'] and r.S['sources']['_live_fetch']['limit_note'].startswith(REASON)
    assert all(REASON in s for s in _statuses(r.S).values()), _statuses(r.S)
    cfg.settings.cache_clear()


def test_a_ekran_otrisovan_i_prichina_vidna_polzovatelyu(freeze, monkeypatch, tmp_path):
    """AppTest: экран отрисован, вердикт на месте, причина стоит в состоянии источников."""
    _settings_with_deadline(tmp_path, monkeypatch, TEST_DEADLINE_S)
    monkeypatch.setattr(lc.requests, 'get', _hanging_transport(freeze))
    from streamlit.testing.v1 import AppTest
    t = time.monotonic()
    at = AppTest.from_file(APP, default_timeout=300).run()
    dt = time.monotonic() - t
    assert not at.exception, at.exception
    assert dt < SLEEP_S, 'первый рендер ждал сеть (%.1f с), а не общий предел' % dt
    body = _screen(at)
    from app.ui import VERDICT_TITLE
    assert 'ВКД-Риск' in body, body[:400]                      # шапка
    assert any(v in body for v in VERDICT_TITLE.values()), body[:800]     # ответ первым — вердикт на месте
    assert any(REASON in w.value for w in at.warning), [w.value for w in at.warning]
    # бриф §9.7: причина названа один раз. Здесь она стоит в строках источников, оставшихся
    # без данных, — общей строки «Живые источники: …» при этом быть не должно.
    named = [x for w in at.warning for x in w.value.split('\n') if fg.LIMIT_MARK in x]
    assert named and not any(x.lstrip('- ').startswith('Живые источники') for x in named), named
    cfg.settings.cache_clear()


def test_a_obshchaya_stroka_poyavlyaetsya_kogda_istochniki_ne_zhaluyutsya(freeze, monkeypatch, tmp_path):
    """Обратный случай: три источника исключены пользователем, живьём идёт только цепочка TLE.
    Ни одна строка источника про предел не говорит — и тогда его называет общая строка."""
    _settings_with_deadline(tmp_path, monkeypatch, TEST_DEADLINE_S)
    monkeypatch.setattr(lc.requests, 'get', _hanging_transport(freeze))
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=300)
    for key in ('dis_goes', 'dis_kp', 'dis_noaa'):
        at.session_state[key] = 'исключён: нет данных'
    at.run()
    assert not at.exception, at.exception
    named = [x for w in at.warning for x in w.value.split('\n') if fg.LIMIT_MARK in x]
    assert len(named) == 1 and named[0].lstrip('- ').startswith('Живые источники'), named
    cfg.settings.cache_clear()


# ================================================================= (б) сети нет вовсе
def test_b_zavisanie_razresheniya_imeni(freeze, monkeypatch, tmp_path):
    """Сети нет вовсе: getaddrinfo зависает. Тайм-аут requests сюда не достаёт — достаёт только
    общий предел. Экран отрисован, причина названа, расчёт заканчивается по пределу."""
    calls = []
    monkeypatch.setattr(socket, 'getaddrinfo', _hanging_dns(freeze, calls))
    t = time.monotonic()
    fetched, note = fg.fetch_live_sources({'goes': False, 'kp': False, 'noaa': False}, deadline_s=TEST_DEADLINE_S)
    dt = time.monotonic() - t
    assert dt < TEST_DEADLINE_S + 1.0, 'получение источников заняло %.1f с' % dt
    assert note and note.startswith(REASON), note
    assert calls, 'проверка должна была дойти до разрешения имени'
    assert all(REASON in item[-1].status_ru for item in fetched)

    _settings_with_deadline(tmp_path, monkeypatch, TEST_DEADLINE_S)
    from streamlit.testing.v1 import AppTest
    t = time.monotonic()
    at = AppTest.from_file(APP, default_timeout=300).run()
    dt = time.monotonic() - t
    assert not at.exception, at.exception
    assert dt < SLEEP_S, 'первый рендер без сети занял %.1f с' % dt
    assert any(REASON in w.value for w in at.warning), [w.value for w in at.warning]
    assert at.markdown, 'экран пуст'
    cfg.settings.cache_clear()


# ================================================================= (в) история не трогает сеть
def test_v_istoricheskie_rezhimy_ne_trogayut_set(monkeypatch):
    """Ни одного сетевого вызова в обоих исторических режимах: подмена обрывает попытку сразу."""
    calls = []

    def _no_net(*a, **kw):
        calls.append(a[:2])
        raise AssertionError('исторический режим обратился к сети')

    monkeypatch.setattr(socket, 'getaddrinfo', _no_net)
    monkeypatch.setattr(lc.requests, 'get', _no_net)
    for mode in ('history_review', 'history_forecast'):
        r = run(mode, T_HIST, 360, 720, [0, 240], now=T_HIST)
        assert r.S['windows'] and r.S['sources'].get('_live_fetch') is None, mode
    assert calls == [], calls


def test_v_ekran_istorii_bez_seti(monkeypatch):
    """Тот же запрет для экрана: исторический режим выставляется до первого прогона."""
    calls = []

    def _no_net(*a, **kw):
        calls.append(a[:2])
        raise AssertionError('исторический режим обратился к сети')

    monkeypatch.setattr(socket, 'getaddrinfo', _no_net)
    monkeypatch.setattr(lc.requests, 'get', _no_net)
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=300)
    at.session_state['mode'] = 'Исторический разбор'
    at.run()
    assert not at.exception, at.exception
    assert calls == [], calls


# ================================================================= снимок, выгрузка, честность причины
def test_prichina_uhodit_v_snimok_i_v_vygruzku(freeze, monkeypatch):
    """Т1/О4: в выгрузке это отказ источника с названной причиной, а не отсутствие данных молча."""
    monkeypatch.setattr(lc.requests, 'get', _hanging_transport(freeze))
    fetched, note = fg.fetch_live_sources({'goes': False, 'kp': False, 'noaa': False}, deadline_s=TEST_DEADLINE_S)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    r = run('live', t0, 60, 60, [0, 60], fetched=fetched, fetch_note=note, now=t0)
    assert REASON in r.S['sources']['noaa_swpc_goes']['status']
    with zipfile.ZipFile(io.BytesIO(build_zip(r.S, r.raw_records))) as z:
        md = z.read('report.md').decode('utf-8')
        sources_json = z.read('sources.json').decode('utf-8')
    assert REASON in md, md[md.find('## Источники'):][:800]
    assert REASON in sources_json


def test_isklyuchyonnyy_polzovatelem_istochnik_ne_perepisyvaetsya(freeze, monkeypatch):
    """Причина называется только там, где она правда: исключённый пользователем источник
    и источник, переведённый на кеш вручную, остаются со своей причиной."""
    monkeypatch.setattr(lc.requests, 'get', _hanging_transport(freeze))
    fetched, note = fg.fetch_live_sources({'goes': 'off', 'kp': 'cache', 'noaa': False}, deadline_s=TEST_DEADLINE_S)
    by_key = dict(zip(fg.KEYS, fetched))
    assert REASON not in by_key['goes'][-1].status_ru and 'исключён пользователем' in by_key['goes'][-1].status_ru
    assert REASON not in by_key['kp'][-1].status_ru
    assert REASON in by_key['noaa'][-1].status_ru and REASON in by_key['tle'][-1].status_ru
    assert note.startswith(REASON)


def test_zavisshiy_potok_ne_povtoryaet_zapros(freeze, monkeypatch):
    """Пока брошенная попытка жива, нового живого запроса не делается: следующий рендер сразу
    идёт по кешу и говорит об этом."""
    monkeypatch.setattr(lc.requests, 'get', _hanging_transport(freeze))
    fg.fetch_live_sources({'goes': False, 'kp': False, 'noaa': False}, deadline_s=TEST_DEADLINE_S)
    assert fg.pending_thread() is not None
    t = time.monotonic()
    _, note = fg.fetch_live_sources({'goes': False, 'kp': False, 'noaa': False}, deadline_s=TEST_DEADLINE_S)
    dt = time.monotonic() - t
    assert dt < 1.0, 'повторная попытка ждала сеть %.1f с' % dt
    assert 'предыдущая попытка получения ещё не завершилась' in note, note


# ================================================================= настройка предела (Т7)
def test_predel_chitaetsya_iz_nastroek_i_proveryaetsya(monkeypatch, tmp_path):
    """Ключ живой: значение берётся из файла, а не из кода; неправильное значение — ошибка."""
    cfg.settings.cache_clear()
    # Значение не вписывается числом: рабочий предел меняется по замерам (двенадцатый круг поднял
    # его с 12 до 20 с, обоснование — в config/settings.toml). Проверяется именно то, что ключ
    # живой: функция возвращает то, что стоит в файле, а не умолчание кода.
    assert fg.total_deadline_s() == float(cfg.section('sources')['total_deadline_s'])
    _settings_with_deadline(tmp_path, monkeypatch, 3)
    assert fg.total_deadline_s() == 3.0
    for bad in ('0', '-1', '"12"', '1000'):
        p = tmp_path / 'bad.toml'
        io.open(p, 'w', encoding='utf-8').write('[sources]\ntotal_deadline_s = %s\n' % bad)
        monkeypatch.setenv('VKD_SETTINGS', str(p))
        cfg.settings.cache_clear()
        with pytest.raises(ValueError, match='total_deadline_s'):
            fg.total_deadline_s()
    monkeypatch.delenv('VKD_SETTINGS', raising=False)
    cfg.settings.cache_clear()
    assert fg.total_deadline_s() == float(cfg.section('sources')['total_deadline_s'])
