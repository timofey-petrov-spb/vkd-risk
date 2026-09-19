# -*- coding: utf-8 -*-
"""Жёсткость геомагнитного обрезания по таблице Ж.1 ОСТ 134-1044-2007 (вместо симметричного диполя).

Что здесь проверяется и почему это важно.

Центральный наклонённый диполь симметричен по широте: на +50° и на −50° он даёт одно и то же
обрезание. Настоящее поле несимметрично, и в южном полушарии обрезание существенно ниже.
Из-за этого фактор «минут доступности протонов ≥100 МэВ» был тождественно нулевым: жёсткость
протона 100 МэВ 0,445 ГВ, а дипольный минимум на трассе МКС около 0,71 ГВ. По таблице Ж.1
минимум на южной кромке трассы около 0,40 ГВ, то есть протон проходит.

Проверяются: точное совпадение с таблицей в её узлах, замыкание по долготе, запасной
дипольный путь за границами таблицы и при недоступном файле, признак происхождения значения
в каждой точке, отношение старого и нового значения на сетке точек и воспроизводимость
объявленной оценки ошибки от разницы эпох.
"""
from __future__ import annotations

import csv
import io
import importlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from vkd.orbit.cutoff_table import (CutoffTableError, EARTH_RADIUS_KM, EPOCH_DIPOLE_SHIFT_MAX_PCT,
                                    EPOCH_DIPOLE_SHIFT_MEDIAN_PCT, EPOCH_DIPOLE_SHIFT_MIN_PCT,
                                    LAT_LIMIT_DEG, TABLE_ALTITUDE_KM, TABLE_RELATIVE_PATH,
                                    TABLE_REFERENCE_RADIUS_KM, load_table, vertical_cutoff_GV)
from vkd.orbit.magnetic import CUTOFF_DIPOLE, CUTOFF_NONE, CUTOFF_TABLE, magnetic_coordinates
from vkd.windows.compare import rigidity_GV

ROOT = Path(__file__).resolve().parents[1]
TABLE_PATH = ROOT / TABLE_RELATIVE_PATH
IGRF13 = ROOT / 'data' / 'orbit' / 'IGRF13.shc'
R_TABLE = TABLE_REFERENCE_RADIUS_KM
# Жёсткости каналов GOES, по которым считается фактор доступности (vkd/windows/compare.py).
R100 = rigidity_GV(100.0)
R10 = rigidity_GV(10.0)


@pytest.fixture(scope='module')
def table():
    return load_table(TABLE_PATH)


@pytest.fixture(scope='module')
def raw_csv():
    """Таблица прямо из файла, без нашего загрузчика: сверяться надо с источником, а не с собой."""
    with io.open(TABLE_PATH, encoding='utf-8', newline='') as fh:
        rows = list(csv.reader(fh))
    lons = [int(name.rsplit('_', 1)[-1]) for name in rows[0][1:]]
    return lons, [(int(r[0]), [float(v) for v in r[1:]]) for r in rows[1:]]


def dipole_cutoff(lat_deg, lon_deg, when, radius_km=R_TABLE, alt_km=TABLE_ALTITUDE_KM, coeff=IGRF13):
    """Прежний путь: вертикальная формула Штёрмера на центральном наклонённом диполе."""
    lat = np.atleast_1d(np.asarray(lat_deg, dtype=float))
    lon = np.atleast_1d(np.asarray(lon_deg, dtype=float))
    la, lo = np.radians(lat), np.radians(lon)
    ecef = np.column_stack((radius_km * np.cos(la) * np.cos(lo),
                            radius_km * np.cos(la) * np.sin(lo),
                            radius_km * np.sin(la)))
    field = magnetic_coordinates(ecef, lon, lat, np.full(lat.shape, float(alt_km)), [when] * len(lat), coeff)
    return field['cutoff_GV']


# --------------------------------------------------------------------------- узлы таблицы

def test_v_uzlah_setki_sovpadenie_s_tablicey_tochnoe(table, raw_csv):
    """Все 420 узлов: интерполяция в узле обязана вернуть само табличное число."""
    lons, rows = raw_csv
    n = 0
    for lat, values in rows:
        got, inside = vertical_cutoff_GV(table, np.full(len(lons), float(lat)), np.array(lons, dtype=float))
        assert inside.all(), lat
        for lon, expected, actual in zip(lons, values, got):
            assert actual == pytest.approx(expected, abs=1e-12), (lat, lon, expected, actual)
            n += 1
    assert n == 35 * 12, n


def test_setka_i_granicy_obyavleny(table, raw_csv):
    """Объявленные границы применимости — это то, что лежит в файле, а не то, что помнит код."""
    lons, rows = raw_csv
    assert lons == list(range(0, 360, 30))
    assert [lat for lat, _ in rows] == list(range(85, -90, -5))
    assert table.lat_deg[0] == -LAT_LIMIT_DEG and table.lat_deg[-1] == LAT_LIMIT_DEG
    assert table.values_GV.shape == (35, 12)
    assert '450' in table.description_ru and '2010' in table.description_ru


# --------------------------------------------------------------------------- интерполяция

def test_zamykanie_po_dolgote(table):
    """330° соседствует с 0°: середина между ними — среднее крайних столбцов, без разрыва."""
    lats = np.array([-51.6, 0.0, 51.6, -85.0, 85.0])
    edge_330, _ = vertical_cutoff_GV(table, lats, np.full(lats.shape, 330.0))
    edge_0, _ = vertical_cutoff_GV(table, lats, np.zeros(lats.shape))
    middle, _ = vertical_cutoff_GV(table, lats, np.full(lats.shape, 345.0))
    assert middle == pytest.approx(0.5 * (edge_330 + edge_0), abs=1e-12)
    # долгота приводится к диапазону таблицы: 360 == 0, −30 == 330, 690 == 330
    for lon, same in ((360.0, 0.0), (-30.0, 330.0), (690.0, 330.0), (-360.0, 0.0)):
        a, _ = vertical_cutoff_GV(table, lats, np.full(lats.shape, lon))
        b, _ = vertical_cutoff_GV(table, lats, np.full(lats.shape, same))
        assert a == pytest.approx(b, abs=1e-12), lon
    # в самой узкой точке — на стыке — значение не «прыгает»
    left, _ = vertical_cutoff_GV(table, lats, np.full(lats.shape, 359.99))
    assert np.abs(left - edge_0).max() < 0.01


def test_bilineynaya_interpolyaciya_po_shirote_i_dolgote(table, raw_csv):
    """Середина ячейки — среднее четырёх узлов; четверть ячейки — с весами 0,75/0,25."""
    lons, rows = raw_csv
    by_lat = dict(rows)
    for lat_hi, lat_lo, j in ((-50, -55, 4), (55, 50, 0), (5, 0, 11)):
        corners = (by_lat[lat_hi][j], by_lat[lat_hi][(j + 1) % 12],
                   by_lat[lat_lo][j], by_lat[lat_lo][(j + 1) % 12])
        mid, _ = vertical_cutoff_GV(table, np.array([(lat_hi + lat_lo) / 2.0]),
                                    np.array([lons[j] + 15.0]))
        assert mid[0] == pytest.approx(sum(corners) / 4.0, abs=1e-12)
        quarter, _ = vertical_cutoff_GV(table, np.array([lat_lo + 0.25 * (lat_hi - lat_lo)]),
                                        np.array([lons[j] + 0.25 * 30.0]))
        expected = (0.75 * (0.75 * corners[2] + 0.25 * corners[3])
                    + 0.25 * (0.75 * corners[0] + 0.25 * corners[1]))
        assert quarter[0] == pytest.approx(expected, abs=1e-12)


def test_pereschet_vysoty_po_zh3_sovpadaet_s_zakonom_dipolya(table):
    """Ж.3 — закон 1/r². Проверяется и арифметика, и то, что тот же закон даёт наш диполь."""
    lat = np.array([-51.6, 20.0, 60.0])
    lon = np.array([120.0, 300.0, 15.0])
    base, _ = vertical_cutoff_GV(table, lat, lon)
    r = np.full(lat.shape, EARTH_RADIUS_KM + 420.0)
    moved, inside = vertical_cutoff_GV(table, lat, lon, r)
    assert inside.all()
    assert moved == pytest.approx(base * (R_TABLE / r) ** 2, rel=1e-12)
    assert (moved > base).all()          # ниже — обрезание выше, а не ниже
    assert moved / base == pytest.approx(np.full(3, 1.0089), abs=5e-4)
    # независимая проверка показателя степени: у дипольной формулы та же зависимость от радиуса
    when = datetime(2010, 1, 1, tzinfo=timezone.utc)
    hi = dipole_cutoff(lat, lon, when, radius_km=R_TABLE, alt_km=450.0)
    lo = dipole_cutoff(lat, lon, when, radius_km=EARTH_RADIUS_KM + 420.0, alt_km=420.0)
    assert lo / hi == pytest.approx((R_TABLE / (EARTH_RADIUS_KM + 420.0)) ** 2, rel=1e-9)


def test_vne_shirot_tablicy_znacheniya_net(table):
    """|широта| > 85° — вне сетки: NaN и False, а не экстраполяция последней строкой."""
    lat = np.array([85.0, 85.0001, 88.0, -85.0, -85.0001, -90.0, float('nan')])
    lon = np.full(lat.shape, 45.0)
    values, inside = vertical_cutoff_GV(table, lat, lon)
    assert list(inside) == [True, False, False, True, False, False, False]
    assert np.isnan(values[~inside]).all() and np.isfinite(values[inside]).all()


# --------------------------------------------------------------------------- битая таблица

def test_bitaya_tablica_eto_oshibka_a_ne_tihoe_umolchanie(tmp_path):
    """Файл есть, но сетка не та — отказ с объяснением. Тихо подставить диполь тут нельзя:
    это была бы подмена объявленного источника незаметно для пользователя."""
    good = io.open(TABLE_PATH, encoding='utf-8').read().splitlines()
    cases = {'lost_row': '\n'.join(good[:5] + good[6:]),
             'lost_column': '\n'.join(line.rsplit(',', 1)[0] for line in good),
             'letters': '\n'.join([good[0], good[1].replace('0.004', 'нет')] + good[2:]),
             'negative': '\n'.join([good[0], good[1].replace('0.004', '-1.0')] + good[2:])}
    for name, text in cases.items():
        path = tmp_path / ('%s.csv' % name)
        io.open(path, 'w', encoding='utf-8').write(text)
        with pytest.raises(CutoffTableError):
            load_table(path)
    with pytest.raises(FileNotFoundError):       # файла нет — отдельный случай, запасной путь
        load_table(tmp_path / 'net_takogo.csv')


# --------------------------------------------------------------------------- запасной путь

def test_zapasnoy_dipol_za_granicami_tablicy_i_bez_fayla(table):
    """Вне широт таблицы и при недоступном файле считает диполь, и это видно в признаке."""
    when = datetime(2024, 5, 10, tzinfo=timezone.utc)
    lat = np.array([-51.6, 88.0, -88.0, 10.0])
    lon = np.array([120.0, 10.0, 200.0, 300.0])
    la, lo = np.radians(lat), np.radians(lon)
    ecef = np.column_stack((R_TABLE * np.cos(la) * np.cos(lo), R_TABLE * np.cos(la) * np.sin(lo),
                            R_TABLE * np.sin(la)))
    alt = np.full(lat.shape, 450.0)
    with_table = magnetic_coordinates(ecef, lon, lat, alt, [when] * 4, IGRF13, cutoff_table_path=TABLE_PATH)
    assert list(with_table['cutoff_source']) == [CUTOFF_TABLE, CUTOFF_DIPOLE, CUTOFF_DIPOLE, CUTOFF_TABLE]
    assert with_table['cutoff_valid'].all()
    assert with_table['cutoff_info']['n_table'] == 2 and with_table['cutoff_info']['n_dipole'] == 2
    # в запасных точках значение равно дипольному в точности, а не «почти»
    dip = with_table['cutoff_dipole_GV']
    assert with_table['cutoff_GV'][1] == dip[1] and with_table['cutoff_GV'][2] == dip[2]
    assert with_table['cutoff_GV'][0] != dip[0]

    missing = magnetic_coordinates(ecef, lon, lat, alt, [when] * 4, IGRF13,
                                   cutoff_table_path=TABLE_PATH.parent / 'net_takogo.csv')
    assert list(missing['cutoff_source']) == [CUTOFF_DIPOLE] * 4
    assert missing['cutoff_GV'] == pytest.approx(dip)
    assert 'недоступен' in missing['cutoff_info']['table_status']
    assert missing['cutoff_info']['n_table'] == 0

    # без аргумента поведение прежнее: только диполь, и это тоже подписано
    old = magnetic_coordinates(ecef, lon, lat, alt, [when] * 4, IGRF13)
    assert list(old['cutoff_source']) == [CUTOFF_DIPOLE] * 4 and old['cutoff_info']['n_table'] == 0
    assert CUTOFF_NONE not in set(old['cutoff_source'])


# --------------------------------------------------------------------------- старое против нового

def test_staroe_protiv_novogo_na_setke_tochek(table):
    """Сетка точек с записью отношения: на севере формула сходится с таблицей в пределах 10 %,
    на юге завышает минимум в 1,6–2,4 раза. Это и есть причина всей работы — она зафиксирована
    числом, чтобы «диполь сойдёт» нельзя было утверждать на словах."""
    # Замер 19.09 на высоте таблицы, эпоха 2010, 12 долгот сетки:
    # широта -> (отношение по минимуму «диполь / таблица», минимум таблицы, минимум диполя), ГВ.
    # Числа записаны, а не только границы: молчаливый сдвиг любой из двух моделей будет виден.
    PINNED = {55.0: (1.073, 0.389, 0.417), 50.0: (1.089, 0.743, 0.809), 45.0: (1.082, 1.285, 1.390),
              -45.0: (1.390, 1.000, 1.390), -50.0: (1.657, 0.488, 0.809), -55.0: (2.220, 0.188, 0.417)}
    when = datetime(2010, 1, 1, tzinfo=timezone.utc)
    lons = np.arange(0, 360, 30.0)
    measured = {}
    for lat in PINNED:
        new, inside = vertical_cutoff_GV(table, np.full(lons.shape, lat), lons)
        old = dipole_cutoff(np.full(lons.shape, lat), lons, when)
        assert inside.all()
        measured[lat] = (old.min() / new.min(), float(new.min()), float(old.min()))
        assert measured[lat] == pytest.approx(PINNED[lat], abs=0.005), (lat, measured[lat], PINNED[lat])
    for lat in (55.0, 50.0, 45.0):
        assert 0.9 <= measured[lat][0] <= 1.15, (lat, measured[lat])       # север: сходится
    for lat, lo, hi in ((-45.0, 1.25, 1.55), (-50.0, 1.55, 1.85), (-55.0, 2.0, 2.45)):
        assert lo <= measured[lat][0] <= hi, (lat, measured[lat])          # юг: завышает, и насколько
    # симметрия диполя: +50 и −50 у него совпадают, у таблицы — нет
    assert measured[50.0][2] == pytest.approx(measured[-50.0][2], rel=1e-9)
    assert measured[-50.0][1] < 0.8 * measured[50.0][1]


def test_na_yuzhnoy_kromke_trassy_mks_proton_100_MeV_prohodit(table):
    """Следствие, ради которого всё делалось: по таблице протон 100 МэВ на южной кромке
    трассы МКС проходит, по диполю — никогда. Числа взяты на широте −51,79° (максимум
    трассы МКС по замеру 19.09) и на высоте 420 км."""
    lons = np.arange(0, 360, 5.0)
    lat = np.full(lons.shape, -51.79)
    r = np.full(lons.shape, EARTH_RADIUS_KM + 420.0)
    new, inside = vertical_cutoff_GV(table, lat, lons, r)
    old = dipole_cutoff(lat, lons, datetime(2024, 5, 10, tzinfo=timezone.utc),
                        radius_km=EARTH_RADIUS_KM + 420.0, alt_km=420.0)
    assert inside.all()
    assert new.min() < R100 < old.min(), (new.min(), R100, old.min())
    assert new.min() > R10 and old.min() > R10        # канал 10 МэВ не проходит ни там, ни там
    # проходит узкой полосой, а не «везде»: не выдаём мелкое следствие за большое
    assert 0 < (new < R100).sum() <= 0.1 * len(lons)
    # запас по порогу невелик: 0,40 против 0,445 ГВ — вывод чувствителен к ошибке эпохи
    assert 0.85 < new.min() / R100 < 0.95


def test_poryadok_oshibki_ot_raznicy_epoh_vosproizvodim():
    """Объявленная в ограничениях оценка ошибки от разницы эпох — измеримая, а не придуманная.
    Меряется сдвиг НАШЕЙ дипольной формулы при замене коэффициентов IGRF 2010 → 2024 на
    широтах МКС. Недипольную часть (сам дрейф картины) так оценить нельзя, и в ограничениях
    прямо сказано, что числа для неё нет."""
    lons = np.arange(0, 360, 10.0)
    lats = np.array([50.0, 51.6, -50.0, -51.6])
    grid_lat, grid_lon = (x.ravel() for x in np.meshgrid(lats, lons, indexing='ij'))
    a = dipole_cutoff(grid_lat, grid_lon, datetime(2010, 1, 1, tzinfo=timezone.utc))
    b = dipole_cutoff(grid_lat, grid_lon, datetime(2024, 5, 10, tzinfo=timezone.utc))
    rel = 100.0 * (b / a - 1.0)
    assert float(np.median(rel)) == pytest.approx(EPOCH_DIPOLE_SHIFT_MEDIAN_PCT, abs=0.15)
    assert float(rel.min()) == pytest.approx(EPOCH_DIPOLE_SHIFT_MIN_PCT, abs=0.15)
    assert float(rel.max()) == pytest.approx(EPOCH_DIPOLE_SHIFT_MAX_PCT, abs=0.15)
    # порядок этой ошибки — единицы процентов, а расхождение с симметричным диполем на юге —
    # десятки процентов и разы; именно поэтому эпоха 2010 не повод не брать таблицу
    assert abs(np.median(rel)) < 10.0


# --------------------------------------------------------------------------- трасса целиком

def test_na_realnoy_trasse_tablica_primenena_i_proishozhdenie_prostavleno():
    """Сквозная проверка на архивной трассе МКС: таблица применена во всех точках, признак
    происхождения стоит в каждой, ограничения напечатаны словами, а минимум обрезания упал
    ниже жёсткости протона 100 МэВ — того самого порога, который раньше не достигался."""
    T = importlib.import_module('vkd.orbit.trajectory')
    start = datetime(2024, 5, 4, 1, 16, tzinfo=timezone.utc)
    meta, points, prov = T.trajectory_with_provenance(start, 360, 24000.0, mode='history_review')
    kinds = {p.cutoff_kind for p in points}
    assert kinds == {CUTOFF_TABLE}, kinds
    assert all(p.cutoff_GV is not None for p in points)
    info = prov['cutoff_model']
    assert info['primary'] == CUTOFF_TABLE and info['n_table'] == len(points) and info['n_dipole'] == 0
    assert info['table_sha256'] and info['kp_mlt_correction'].startswith('не применена')
    limits = ' | '.join(prov['limitations'])
    assert 'Ж.1' in limits and '450' in limits and '2010' in limits and 'Ж.3' in limits
    assert 'Ж.4–Ж.6' in limits and 'СПОКОЙНЫМ' in limits
    below = [p for p in points if p.cutoff_GV < R100]
    assert below, 'на этом окне протон 100 МэВ обязан проходить: замер 19.09 дал 2,1 минуты'
    assert all(p.lat_deg < -50 and 100 < p.lon_deg < 135 for p in below), [(p.lat_deg, p.lon_deg) for p in below]
    assert not [p for p in points if p.cutoff_GV < R10]     # канал 10 МэВ по-прежнему закрыт
