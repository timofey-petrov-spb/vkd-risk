# -*- coding: utf-8 -*-
"""Техногенное вещество по ГОСТ Р 25645.167-2005: сверка с напечатанными числами
стандарта, согласование опорной геометрии с метеороидной линией и свойства модели,
которые объявлены в отчёте (в том числе то, что окна она НЕ различает)."""
import math
from datetime import datetime, timedelta, timezone

import pytest

import vkd.assess.debris as D
from vkd.assess.debris import (
    DebrisResult, debris_hits, debris_hits_track, flux_by_j, growth_by_j,
    index, reference_mass_g, size_ranges,
    PLATE_TO_CROSS_SECTION, TABLE_ALT_KM, TABLE_INC_DEG,
)
from vkd.assess.meteoroids import meteoroid_hits

T0 = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
ISS_ALT, ISS_INC = 420.0, 51.6


def _track(minutes, alt, t0=T0):
    ts = [t0 + timedelta(minutes=i) for i in range(minutes + 1)]
    alts = [alt(i) if callable(alt) else alt for i in range(minutes + 1)]
    return ts, alts


# ---------------------------------------------------------------- данные

def test_index_and_all_csv_checksums_hold():
    idx = index()
    assert idx['source']['sha256'].startswith('5b3c7e6c')
    assert {e['file'] for e in idx['files']} == {
        'table_5_1_size_ranges.csv', 'table_7_1_collision_velocity.csv',
        'table_7_2_flux.csv', 'table_8_forecast.csv'}
    assert idx['scope'] == {'size_cm_min': 0.1, 'alt_km': [200, 2000],
                            'years': [2000, 2025], 'note': idx['scope']['note']}


def test_flux_grid_is_complete_336_cells():
    grid, incs, alts = D._flux_grid()
    assert incs == (55.0, 65.0, 75.0, 85.0, 95.0, 105.0)
    assert alts == (200.0, 400.0, 600.0, 800.0, 1000.0, 1200.0, 1400.0)
    assert len(grid) == 6 * 8 * 7 == 336
    assert all(v > 0 for v in grid.values())


def test_forecast_grid_is_complete_2912_cells():
    grid, alts = D._forecast_grid()
    assert len(grid) == 8 * 2 * 26 * 7 == 2912
    assert all(grid[(j, K, 2000, h)] == 1.0
               for j in range(1, 9) for K in (1.0, 0.5) for h in alts)


@pytest.mark.parametrize('inc,j,alt,want', [
    (55, 1, 200, 5.18e-4),     # первая ячейка таблицы 7.2
    (55, 1, 400, 2.49e-3),
    (55, 8, 1400, 1.26e-6),    # последняя ячейка блока 55°
    (75, 4, 800, 7.33e-5),     # OCR подменял 3 на кириллическую З — сверено с растром
    (85, 1, 600, 1.30e-2),     # то же, 0 на О
    (85, 2, 400, 2.86e-4),     # OCR разрывал число пробелом «2 ,86E-4»
    (95, 5, 400, 2.86e-6),
    (105, 8, 1400, 1.98e-6),   # последняя ячейка таблицы
])
def test_flux_cells_match_printed_standard(inc, j, alt, want):
    grid, _, _ = D._flux_grid()
    assert grid[(float(inc), j, float(alt))] == pytest.approx(want, rel=1e-12)


@pytest.mark.parametrize('tab,year,alt,want', [
    ('8.1', 2000, 400, 1.000),
    ('8.1', 2025, 400, 35.253),
    ('8.8', 2025, 200, 42.433),
    ('8.16', 2025, 1400, 30.245),
])
def test_forecast_cells_match_printed_standard(tab, year, alt, want):
    rows = D._rows('table_8_forecast.csv')
    got = [r for r in rows if r['table'] == tab and int(r['year']) == year
           and float(r['alt_km']) == alt]
    assert len(got) == 1
    assert float(got[0]['F_years']) == pytest.approx(want, rel=1e-12)


def test_three_misprints_are_kept_as_printed_and_corrected_explicitly():
    """Опечатки самого стандарта: в CSV лежит напечатанное, в расчёт идёт исправленное."""
    mps = index()['known_misprints']
    assert len(mps) == 3
    assert {m['table'] for m in mps} == {'7.2', '8.12', '8.13'}
    # 7.2: 105°, j=3, 1400 км — напечатано 3,53E-3, в расчёт идёт 3,53E-4
    row = [r for r in D._rows('table_7_2_flux.csv')
           if r['inclination_deg'] == '105' and r['j'] == '3' and r['alt_km'] == '1400'][0]
    assert float(row['flux_m2_yr']) == pytest.approx(3.53e-3)
    assert float(row['corrected']) == pytest.approx(3.53e-4)
    grid, _, _ = D._flux_grid()
    assert grid[(105.0, 3, 1400.0)] == pytest.approx(3.53e-4)
    # и после исправления поток снова убывает с размером
    assert grid[(105.0, 3, 1400.0)] < grid[(105.0, 2, 1400.0)]


def test_misprints_do_not_touch_the_iss_answer():
    """Расчёт для МКС (420 км, 51,6°->55°, 2026->прирост 2025 г., K=1) читает строго
    перечисленные ниже ячейки. Ни один ключ опечатки в этот набор не входит."""
    flux_keys = {(55.0, j, h) for j in range(1, 9) for h in (400.0, 600.0)}
    fc_keys = {(j, 1.0, y, h) for j in range(1, 9) for y in (2024, 2025) for h in (400.0, 600.0)}
    for m in index()['known_misprints']:
        w = m['where']
        if m['kind'] == 'flux':
            assert (float(w['inclination_deg']), w['j'], float(w['alt_km'])) not in flux_keys
        else:
            j = {'8.12': 4, '8.13': 5}[w['table']]
            assert (j, 0.5, w['year'], float(w['alt_km'])) not in fc_keys


def test_flux_decreases_with_particle_size_everywhere():
    grid, incs, alts = D._flux_grid()
    for inc in incs:
        for alt in alts:
            for j in range(1, 7):     # j=8 — каталог «свыше 20 см», стандарт даёт подъём
                assert grid[(inc, j + 1, alt)] < grid[(inc, j, alt)]


# ------------------------------------------- опорная геометрия и порог массы

def test_reference_mass_is_sphere_at_lower_size_bound_with_gost_density():
    """m = ρ·(π/6)·d³ по собственным числам таблицы 5.1: 2,5 г/см³ и 0,1 см."""
    assert reference_mass_g(1) == pytest.approx(2.5 * math.pi / 6.0 * 0.1 ** 3)
    assert reference_mass_g(1) == pytest.approx(1.309e-3, rel=1e-3)
    assert reference_mass_g(1) / 1e-3 == pytest.approx(1.309, rel=1e-3)


def _sphere_diameter_cm(r):
    return (r['mean_mass_kg'] * 1000.0 / (r['density_g_cm3'] * math.pi / 6.0)) ** (1.0 / 3.0)


def test_sphere_assumption_holds_on_the_small_ranges_that_carry_the_answer():
    """Средняя масса диапазона, обращённая в диаметр шара той же плотности, попадает
    ВНУТРЬ своего же диапазона размеров для j = 1…4 — то есть сведение размера к массе
    не выдумано именно там, где считается ответ."""
    for r in size_ranges()[:4]:
        d = _sphere_diameter_cm(r)
        assert r['size_min_cm'] <= d <= r['size_max_cm']


def test_sphere_assumption_fails_on_large_debris_and_that_is_declared():
    """Обратная сторона: для j = 5…7 средняя масса ГОСТ соответствует телу ЗАМЕТНО
    МЕНЬШЕ сплошного шара той же плотности — крупный мусор не компактен (обломки,
    фольга, ЭВТИ). Порог по массе строится только по j = 1, а j = 1…4 дают 99,9 % N,
    поэтому на ответ это не влияет; но молча выдавать шар за общее правило нельзя."""
    for r in size_ranges()[4:7]:
        assert _sphere_diameter_cm(r) < r['size_min_cm']
    r = debris_hits(ISS_ALT, ISS_INC, 1.0, 6.0, year=2026)
    assert sum(v for j, v in r.N_by_j.items() if j <= 4) / r.N > 0.998


def test_cross_section_is_a_quarter_of_the_plate():
    """Односторонняя кувыркающаяся пластина 1 м² видна потоку как 0,25 м² — это и есть
    S формулы (2) при C_N = 1. Иначе два числа на экране несопоставимы."""
    assert PLATE_TO_CROSS_SECTION == 0.25
    r = debris_hits(ISS_ALT, ISS_INC, 1.0, 6.0, year=2026)
    assert r.cross_section_m2 == pytest.approx(0.25)
    assert r.area_m2 == 1.0


def test_N_scales_with_area_and_duration():
    base = debris_hits(ISS_ALT, ISS_INC, 1.0, 6.0, year=2026).N
    assert debris_hits(ISS_ALT, ISS_INC, 2.5, 6.0, year=2026).N == pytest.approx(2.5 * base)
    assert debris_hits(ISS_ALT, ISS_INC, 1.0, 3.0, year=2026).N == pytest.approx(base / 2)


# ------------------------------------------------- интерполяция и края сетки

@pytest.mark.parametrize('interp', ['log', 'linear'])
def test_interpolation_reproduces_grid_nodes_exactly(interp):
    grid, _, _ = D._flux_grid()
    for alt in (400.0, 600.0, 1400.0):
        got = flux_by_j(alt, 55.0, interp=interp)
        for j in range(1, 9):
            assert got[j] == pytest.approx(grid[(55.0, j, alt)], rel=1e-12)


def test_log_and_linear_interpolation_differ_by_declared_11_percent_at_420km():
    """Расхождение объявлено числом в отчёте; если сетка или способ поменяются — тест упадёт."""
    lg = sum(flux_by_j(420.0, 55.0, interp='log').values())
    lin = sum(flux_by_j(420.0, 55.0, interp='linear').values())
    assert lin > lg
    assert lin / lg - 1.0 == pytest.approx(0.110, abs=0.005)


def test_iss_inclination_is_below_the_table_and_is_clamped_loudly():
    r = debris_hits(ISS_ALT, ISS_INC, 1.0, 6.0, year=2026)
    assert ISS_INC < TABLE_INC_DEG[0]
    assert r.inclination_clamped is True
    assert r.inclination_used_deg == 55.0
    assert 'ВНЕ таблицы' in r.rule
    # внутри сетки клампа нет
    assert debris_hits(420.0, 65.0, 1.0, 6.0, year=2026).inclination_clamped is False


def test_year_2026_is_outside_the_forecast_and_uses_the_2025_rate():
    r = debris_hits(ISS_ALT, ISS_INC, 1.0, 6.0, year=2026)
    assert r.year_extrapolated is True
    assert 'ВНЕ прогноза' in r.rule
    g2026, ex = growth_by_j(2026, 400.0)
    g2025, _ = growth_by_j(2025, 400.0)
    assert ex is True
    assert g2026 == g2025
    assert growth_by_j(2025, 400.0)[1] is False


def test_growth_factor_is_the_annual_increment_of_F():
    grid, _ = D._forecast_grid()
    want = grid[(1, 1.0, 2025, 400.0)] - grid[(1, 1.0, 2024, 400.0)]
    assert growth_by_j(2025, 400.0, K=1.0)[0][1] == pytest.approx(want, rel=1e-12)
    assert want == pytest.approx(1.042, abs=1e-3)


def test_optimistic_hypothesis_gives_fewer_hits():
    n1 = debris_hits(ISS_ALT, ISS_INC, 1.0, 6.0, K=1.0, year=2026).N
    n05 = debris_hits(ISS_ALT, ISS_INC, 1.0, 6.0, K=0.5, year=2026).N
    assert 0 < n05 < n1


def test_flux_grows_with_altitude_in_leo():
    a = debris_hits(400.0, 55.0, 1.0, 6.0, year=2025).N
    b = debris_hits(600.0, 55.0, 1.0, 6.0, year=2025).N
    assert b > a


# ------------------------------------------------------- интеграл по трассе

def test_track_integral_matches_closed_form_on_a_flat_track():
    ts, alts = _track(360, ISS_ALT)
    t = debris_hits_track(ts, alts, ISS_INC, 1.0)
    c = debris_hits(ISS_ALT, ISS_INC, 1.0, 6.0, year=2026)
    assert t.duration_h == pytest.approx(6.0)
    assert t.N == pytest.approx(c.N, rel=1e-12)


def test_track_is_invariant_to_sampling_step_on_a_flat_track():
    ts, alts = _track(360, ISS_ALT)
    fine = debris_hits_track(ts, alts, ISS_INC, 1.0).N
    coarse = debris_hits_track(ts[::5], alts[::5], ISS_INC, 1.0).N
    assert coarse == pytest.approx(fine, rel=1e-12)


def test_track_splits_additively():
    ts, alts = _track(360, lambda i: 415.0 + 10.0 * math.sin(i / 40.0))
    whole = debris_hits_track(ts, alts, ISS_INC, 1.0).N
    left = debris_hits_track(ts[:181], alts[:181], ISS_INC, 1.0).N
    right = debris_hits_track(ts[180:], alts[180:], ISS_INC, 1.0).N
    assert left + right == pytest.approx(whole, rel=1e-12)


def test_model_cannot_discriminate_windows_of_equal_altitude_and_length():
    """Ключевое объявление отчёта: поток усреднён по витку, поэтому два окна в разные
    сутки при одинаковой высоте и длительности дают ОДНО И ТО ЖЕ число."""
    a = debris_hits_track(*_track(360, ISS_ALT, T0), inclination_deg=ISS_INC)
    b = debris_hits_track(*_track(360, ISS_ALT, T0 + timedelta(days=11, hours=7)),
                          inclination_deg=ISS_INC)
    assert a.N == pytest.approx(b.N, rel=1e-15)


# --------------------------------------------------------------- отказы

def test_refuses_altitude_outside_the_printed_flux_table():
    with pytest.raises(ValueError, match='вне таблицы'):
        debris_hits(150.0, 55.0, 1.0, 6.0)
    with pytest.raises(ValueError, match='вне таблицы'):
        debris_hits(1800.0, 55.0, 1.0, 6.0)      # область применения до 2000 км, Q_отн — до 1400
    assert TABLE_ALT_KM == (200.0, 1400.0)


def test_refuses_bad_inputs():
    with pytest.raises(ValueError):
        debris_hits(ISS_ALT, ISS_INC, 1.0, 6.0, interp='квадратичная')
    with pytest.raises(ValueError):
        debris_hits_track([T0], [ISS_ALT], ISS_INC)
    with pytest.raises(ValueError):
        debris_hits_track([T0, T0], [ISS_ALT, ISS_ALT], ISS_INC)
    with pytest.raises(ValueError):
        debris_hits_track([T0, T0 + timedelta(hours=1)], [ISS_ALT], ISS_INC)


# ------------------------------------------- сопоставимость с метеороидами

def test_debris_and_meteoroids_are_the_same_order_on_the_same_plate_and_threshold():
    """Обе линии — одна пластина 1 м², один порог массы. Разойдись они на порядки,
    это была бы ошибка в единицах или в геометрии, а не физика."""
    m = reference_mass_g(1)
    deb = debris_hits(ISS_ALT, ISS_INC, 1.0, 6.0, year=2026).N
    met = meteoroid_hits(ISS_ALT, 1.0, 6.0, m).N
    assert 0.1 < deb / met < 10.0
    assert deb / met == pytest.approx(1.39, abs=0.05)


def test_iss_reference_number_is_pinned():
    """Опорное число: 420 км, 51,6°, 6 ч, 1 м², K = 1, 2026 г., логарифмическая интерполяция."""
    r = debris_hits(ISS_ALT, ISS_INC, 1.0, 6.0, K=1.0, interp='log', year=2026)
    assert r.N == pytest.approx(5.561e-7, rel=2e-3)
    assert r.p_at_least_one == pytest.approx(r.N, rel=1e-6)   # N мало, 1−e⁻ᴺ ≈ N
    assert r.N_by_j[1] / r.N == pytest.approx(0.904, abs=0.01)
    assert r.v_collision_km_s == pytest.approx(10.8, abs=0.05)


def test_result_declares_its_limits():
    r = debris_hits(ISS_ALT, ISS_INC, 1.0, 6.0, year=2026)
    assert isinstance(r, DebrisResult)
    for word in ('2025', 'усреднён по витку', 'не вероятность пробоя', '51,6'):
        assert word in r.limits_ru
