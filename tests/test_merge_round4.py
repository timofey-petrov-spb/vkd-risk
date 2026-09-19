# -*- coding: utf-8 -*-
"""Проверки стыка четвёртого круга: то, что видно только после слияния трёх областей.

Каждая область по отдельности была зелёной; эти проверки закрепляют места, где
строка одной области попадает на экран другой.

  * M1 — в текущем режиме слово «отсечк» не появляется нигде: ни в правиле фактора,
    ни в ограничениях, ни в снимке, ни в отчёте. Отсечки в этом режиме нет вовсе,
    и любое упоминание о ней — неправда о происхождении величины (О2).
  * M2 — подпись таблицы сравнения окон (app/compute.py, область «модели») и панель
    вердикта (app/ui.py, область «экран») называют допуск равнозначности одинаково:
    разбросом РАЗНОСТИ минут между окнами, а не разбросом абсолютных минут одного окна
    (CONTRACT §4, формула (9), docs/HANDOFF_2026-09-19.md §2).
"""
import io
import json
import os
import re
from dataclasses import replace as dc_replace
from datetime import datetime, timedelta, timezone

from app.compute import run
from app.export import report_md
from tests.test_integration import _fetched, pinned_noaa

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UTC = timezone.utc
TLE = os.path.join(ROOT, 'data', 'orbit', 'iss.tle')


def _live_with_forecast(tmp_path):
    """Текущий режим с закреплённым бюллетенем NOAA: сети нет, ячейки сдвинуты на окно расчёта."""
    from vkd.orbit.trajectory import satellite_from_tle
    epoch = satellite_from_tle(open(TLE, 'rb').read()).epoch.utc_datetime()
    t0 = (epoch + timedelta(days=1)).replace(second=0, microsecond=0)
    samples, raw, fetch = pinned_noaa(tmp_path)
    shift = t0 - min(s.valid_from_utc for s in samples)
    samples = tuple(dc_replace(s, t_utc=s.t_utc + shift, valid_from_utc=s.valid_from_utc + shift,
                               valid_to_utc=s.valid_to_utc + shift) for s in samples)
    (g, g_raw, f_goes), kp3, (txt, f_tle), _ = _fetched(open(TLE, encoding='utf-8').read(), goes_at=t0)
    return run('live', t0, 360, 720, [0, 240],
               fetched=((g, g_raw, f_goes), kp3, (txt, f_tle), (samples, raw, fetch)), now=t0)


# --------------------------------------------------- M1: в текущем режиме отсечки нет
def test_live_run_never_mentions_a_cutoff_it_does_not_have(tmp_path):
    r = _live_with_forecast(tmp_path)
    assert r.S['request'].get('cutoff_utc') in (None, ''), 'у текущего режима отсечки быть не должно'
    blob = json.dumps(r.S, ensure_ascii=False, default=str)
    assert 'отсечк' not in blob, [s for s in re.findall(r'.{0,60}отсечк.{0,60}', blob)][:3]
    md = report_md(r.S)
    assert 'отсечк' not in md, [s for s in re.findall(r'.{0,60}отсечк.{0,60}', md)][:3]
    # ветка «ячеек на это окно нет» в текущем режиме действительно проходится:
    # иначе проверка выше зелёная по случайности, а не по исправлению
    limits = [f['limits'] for w in r.S['windows'] for m in w['mechanisms'] for f in m['factors']]
    assert any('выпуска с ячейками на это окно нет' in x for x in limits), limits


def test_live_run_has_no_empty_window_cell_text_about_a_cutoff(tmp_path):
    """Ограничение фактора-прогноза при непокрытом окне называло отсечку во всех режимах."""
    src = io.open(os.path.join(ROOT, 'vkd', 'windows', 'compare.py'), encoding='utf-8').read()
    i = src.find('выпуска до отсечки с ячейками на окно нет')
    assert i > 0, 'строка исчезла — проверку переписать вместе с ней'
    assert 'if cutoff_utc' in src[i:i + 200], 'строка про отсечку должна зависеть от наличия отсечки'
    src_cards = io.open(os.path.join(ROOT, 'vkd', 'explain', 'cards.py'), encoding='utf-8').read()
    assert 'источник не подключён или выпуска до отсечки нет' not in src_cards


def test_strict_mode_still_says_cutoff_where_the_cutoff_is_real():
    """Обратная сторона M1: в строгом режиме отсечка настоящая и молчать о ней нельзя."""
    t0 = datetime(2024, 5, 10, 12, 0, tzinfo=UTC)
    r = run('history_forecast', t0, 360, 720, [0, 240], fetched=_fetched(), now=t0)
    assert r.S['request'].get('cutoff_utc')
    assert 'отсечк' in json.dumps(r.S, ensure_ascii=False, default=str)


# --------------------------------------------------- M2: допуск назван одинаково в двух областях
def test_tolerance_is_named_the_same_way_on_table_caption_and_verdict_panel():
    """Подпись таблицы 1 приходит из app/compute.py, панель вердикта — из app/ui.py.
    Обе стоят на одном экране, и обе обязаны называть допуск разбросом РАЗНОСТИ минут."""
    forbidden = 'разброс минут в аномалии на сетке'
    for rel in (('app', 'compute.py'), ('app', 'ui.py')):
        src = io.open(os.path.join(ROOT, *rel), encoding='utf-8').read()
        assert forbidden not in src, os.path.join(*rel)
    ui = io.open(os.path.join(ROOT, 'app', 'ui.py'), encoding='utf-8').read()
    assert 'разброс разности минут' in ui, 'панель вердикта должна называть основание допуска'
    comp = io.open(os.path.join(ROOT, 'app', 'compute.py'), encoding='utf-8').read()
    assert 'разброс разности минут' in comp, 'подпись таблицы 1 должна называть основание допуска'
