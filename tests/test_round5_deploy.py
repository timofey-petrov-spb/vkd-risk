# -*- coding: utf-8 -*-
"""Находки пятого круга, область «deploy»: сводка проверки после отсечки и подписи ссылок.

  * app/compute.py — блок «Проверка после отсечки» печатал одну и ту же величину в разных
    точностях: «максимум 9,00» в сводке против «9» в таблице прямо под ней, «206,919 pfu»
    против двух знаков у того же потока на экране, машинное «12:00Z» вместо «10.05 12:00»;
  * app/main.py — подписи ссылок «первоисточник 1», «первоисточник 2» в карточке условия:
    в карточке названы два уведомления с разными числами, и по подписи нельзя было понять,
    какая ссылка к какому из них.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

import pytest
from streamlit.testing.v1 import AppTest

from app.compute import run
from app.export import report_md
from app.ui import fmt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, 'app', 'main.py')
T_GANNON = datetime(2024, 5, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope='module')
def gannon():
    return run('history_forecast', T_GANNON, 360, 720, [0, 240], now=T_GANNON)


def test_svodka_proverki_pishet_chisla_kak_tablitsa_pod_ney(gannon):
    """Одна величина — одна запись: максимум Kp в сводке и в таблице `kp_obs`, поток GOES —
    как на экране, времена — «дд.мм чч:мм», без машинного «Z»."""
    v = gannon.verification
    s = v['summary']
    kp_max = max(k['kp'] for k in v['kp_obs'])
    assert ('максимум %s' % fmt(float(kp_max))) in s, s
    assert '9,00' not in s and '7.67' not in s, s
    g = v['goes_obs_max']
    assert ('%s pfu' % fmt(float(g['value_pfu']))) in s and '206,919' not in s, s
    assert not re.search(r'\d\d:\d\d\s*Z', s), s
    assert s.startswith('условие поставлено в 10.05 12:00; факт: Kp 7,67 с 10.05 15:00, максимум 9;'), s
    # то же число в выгрузке: сводка и строка таблицы отчёта не расходятся
    md = report_md(gannon.S, gannon.raw_records)
    assert ('максимум %s' % fmt(float(kp_max))) in md and '9,00' not in md
    assert re.search(r'\|\s*%s\s*\|' % re.escape(fmt(float(kp_max))), md), 'таблица Kp отчёта'


def test_tihaya_data_bez_porogovoy_frazy():
    """На дате без бури сводка не приобретает чужих чисел: «бури Kp ≥ 7 не было» — без «7,00»."""
    t = datetime(2024, 6, 25, 12, tzinfo=timezone.utc)
    r = run('history_forecast', t, 360, 720, [0, 240], now=t)
    s = r.verification['summary']
    assert 'бури Kp ≥ 7 не было' in s and '7,00' not in s and '≥ 7,0' not in s, s
    assert not re.search(r'\d\d:\d\d\s*Z', s), s


def test_podpis_ssylki_nazyvaet_zapis_a_ne_nomer():
    """О4: подпись ссылки — имя записи (выпуск источника), «первоисточник 1» на экране нет."""
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    at.sidebar.button('preset_gannon').click().run()
    assert not at.exception, at.exception
    at.radio('cards_win').set_value(at.radio('cards_win').options[1]).run()
    assert not at.exception, at.exception
    links = [m.value for m in at.markdown if m.value.startswith('**Первоисточник:**')]
    assert links, 'карточки объяснений должны называть первоисточник'
    body = '\n'.join(links)
    assert 'первоисточник 1' not in body and 'первоисточник 2' not in body, body
    donki = [x for x in links if 'kauai.ccmc.gsfc.nasa.gov' in x]
    assert donki, links
    # у уведомления DONKI в подписи стоит его номер выпуска (20240510-AL-014 и подобные)
    assert re.search(r'\[уведомление NASA DONKI \d{8}-[A-Z]{2}-\d{3}\]\(https://', '\n'.join(donki)), donki
