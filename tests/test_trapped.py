# -*- coding: utf-8 -*-
"""Численные проверки интегрирования и границ модели поясов (CONTRACT.md v3, раздел 8)."""
import math
import os
from datetime import datetime, timezone

import numpy as np
import pytest

from vkd.assess.trapped import BeltTable, FLUX_UNIT_RU, integrate_power_law

E_OST = np.array([0.2, 0.6, 1.25, 3, 5, 12.5, 30, 50, 125, 300])   # узлы протонов прил. А
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize('b', [-1.0, -1.5, -2.0, -3.0])
def test_power_law_exact_on_ost_grid(b):
    """Аналитический эталон: ∫ a·E^b dE от 30 до 300 совпадает с точностью 1e-9."""
    a = 7.0
    f = a * E_OST ** b
    e_min, e_max = 30.0, 300.0
    exact = a * math.log(e_max / e_min) if b == -1 else a / (b + 1) * (e_max ** (b + 1) - e_min ** (b + 1))
    assert integrate_power_law(E_OST, f, e_min) == pytest.approx(exact, rel=1e-9)


def test_power_law_e_min_between_nodes():
    f = 3.0 * E_OST ** -2.0
    exact = 3.0 * (1 / 40.0 - 1 / 300.0)
    assert integrate_power_law(E_OST, f, 40.0) == pytest.approx(exact, rel=1e-9)


def test_tail_dropped_and_declared():
    f = E_OST ** -2.0
    assert integrate_power_law(E_OST, f, 300.0) == 0.0
    assert integrate_power_law(E_OST, f, 500.0) == 0.0


def test_trapezoid_would_overestimate():
    """Документируем, почему не трапеции: на сетке ОСТ они завышают ∝E⁻² на десятки процентов."""
    f = E_OST ** -2.0
    mask = E_OST >= 30
    trap = float(np.trapezoid(f[mask], E_OST[mask])) if hasattr(np, 'trapezoid') else float(np.trapz(f[mask], E_OST[mask]))
    exact = 1 / 30.0 - 1 / 300.0
    assert trap / exact > 1.2


def test_table_loads_and_units():
    t = BeltTable('min')
    assert list(t.energies_MeV) == list(E_OST)
    assert t.Ls[0] == pytest.approx(1.14) and t.Ls[-1] == pytest.approx(9.0)
    # единицы: всенаправленный поток см⁻²·с⁻¹, без «на стерадиан» (CONTRACT R3, вводный текст прил. А)
    assert 'ср' not in FLUX_UNIT_RU and 'всенаправленный' in t.flux_unit_ru
    assert t.raw_record_id.startswith('ost1044_A_') and len(t.raw_record_id.split(':')[1]) == 12
    assert t.file == 'data/ost1044_belts/A_2_1.csv'


def test_no_model_outside_L_grid():
    t = BeltTable('min')
    r = t.integral_flux(0.9, 1.2, 30.0)
    assert r.value_per_cm2_s is None and r.status == 'no_model_L'
    r = t.integral_flux(12.0, 1.2, 30.0)
    assert r.value_per_cm2_s is None and r.status == 'no_model_L'
    assert t.integral_flux(None, 1.0, 30.0).status == 'no_model_L'


def test_beyond_tabulated_B_is_unknown_not_a_physical_zero():
    t = BeltTable('min')
    r = t.integral_flux(1.3, 1e6, 30.0)
    assert r.value_per_cm2_s is None and r.status == 'no_model_BB0'


def test_inconsistent_BB0_flagged_not_clamped_silently():
    t = BeltTable('min')
    r_low, r_eq = t.integral_flux(1.3, 0.9, 30.0), t.integral_flux(1.3, 1.0, 30.0)
    assert r_low.status == 'inconsistent_BB0' and r_eq.status == 'ok'
    assert r_low.value_per_cm2_s is None and r_eq.value_per_cm2_s > 0


def test_flux_decreases_with_BB0_and_L_interpolation_is_between_rows():
    t = BeltTable('min')
    a, b = t.integral_flux(1.3, 1.0, 30.0), t.integral_flux(1.3, 1.5, 30.0)
    assert a.value_per_cm2_s >= b.value_per_cm2_s
    lo, mid, hi = (t.integral_flux(L, 1.0, 30.0).value_per_cm2_s for L in (1.3, 1.35, 1.4))
    assert min(lo, hi) <= mid <= max(lo, hi)


def _synthetic_table(rows):
    """Таблица из заданных строк (L, B/B0, спектр) без файла — для аналитических проверок интерполяции."""
    t = BeltTable.__new__(BeltTable)
    t.energies_MeV = E_OST.copy()
    t.table = {}
    for L, bb, spec in rows:
        t.table.setdefault(L, []).append((bb, np.asarray(spec, dtype=float)))
    for L in t.table:
        t.table[L].sort(key=lambda p: p[0])
    t.Ls = np.array(sorted(t.table))
    return t


def test_L_interpolation_is_logarithmic_between_rows():
    """Т3: между оболочками с потоком, отличающимся в 100 раз, середина — геометрическое среднее
    (ошибка < 1e-9), а не арифметическое (которое завышало бы её в ~5 раз)."""
    spec_hi, spec_lo = 1000.0 * E_OST ** -2.0, 10.0 * E_OST ** -2.0
    t = _synthetic_table([(1.0, 1.0, spec_hi), (1.0, 2.0, spec_hi), (2.0, 1.0, spec_lo), (2.0, 2.0, spec_lo)])
    f_lo, f_hi, f_mid = (t.integral_flux(L, 1.0, 30.0).value_per_cm2_s for L in (2.0, 1.0, 1.5))
    assert f_mid == pytest.approx(math.sqrt(f_lo * f_hi), rel=1e-9)
    assert f_mid < 0.5 * (f_lo + f_hi) / 2       # арифметика была бы много выше
    # по B/B0 — тоже логарифм
    t2 = _synthetic_table([(1.0, 1.0, spec_hi), (1.0, 2.0, spec_lo)])
    b_lo, b_hi, b_mid = (t2.integral_flux(1.0, bb, 30.0).value_per_cm2_s for bb in (2.0, 1.0, 1.5))
    assert b_mid == pytest.approx(math.sqrt(b_lo * b_hi), rel=1e-9)


def test_real_table_midpoint_follows_log_law():
    """Строки 1,20 и 1,30 табл. А.2.1 различаются в сотни раз: середина по логарифму, а не линейно."""
    t = BeltTable('min')
    f120, f130, f125 = (t.integral_flux(L, 1.21, 30.0).value_per_cm2_s for L in (1.20, 1.30, 1.25))
    assert f130 / f120 > 100
    assert f125 == pytest.approx(math.sqrt(f120 * f130), rel=0.15)     # B/B0 узлы строк различаются, точное равенство не ожидается
    assert f125 < 0.1 * (f120 + f130) / 2


def test_zero_node_falls_back_to_linear():
    spec_hi, spec_zero = 100.0 * E_OST ** -2.0, np.zeros_like(E_OST)
    t = _synthetic_table([(1.0, 1.0, spec_hi), (2.0, 1.0, spec_zero)])
    f_mid = t.integral_flux(1.5, 1.0, 30.0).value_per_cm2_s
    assert f_mid == pytest.approx(0.5 * t.integral_flux(1.0, 1.0, 30.0).value_per_cm2_s, rel=1e-9)


def test_real_orbit_keeps_unknown_samples_out_of_known_flux():
    """The real trajectory has both tabulated and unmodelled points; no missing-as-zero mean."""
    from vkd.assess.magcoords import belt_coordinates
    from vkd.orbit import trajectory
    t0 = datetime(2024, 5, 10, 12, tzinfo=timezone.utc)
    _, pts = trajectory(t0, 1440, 24000, mode='history_review', cutoff_utc=t0)
    ecc, _ = belt_coordinates(pts, os.path.join(ROOT, 'data', 'orbit', 'IGRF13.shc'))
    table = BeltTable('min')
    results = [table.integral_flux(p.L, p.B_over_B0, 30.0) for p in ecc]
    known = [r.value_per_cm2_s for r in results if r.value_per_cm2_s is not None]
    assert 0 < len(known) < len(results)
    assert all(math.isfinite(v) and v >= 0 for v in known)
    assert any(r.status == 'no_model_BB0' for r in results)
    assert all(r.value_per_cm2_s is None for r in results if r.status != 'ok')


def test_interpolate_differential_spectrum_before_integrating_energy():
    # At the midpoint between E^-1 and E^-3, the log-mixed spectrum is E^-2.
    # Geometric mixing of the two energy integrals would NOT equal this answer.
    table = _synthetic_table([(1., 1., E_OST**-1.), (2., 1., E_OST**-3.)])
    actual = table.integral_flux(1.5, 1., 30.).value_per_cm2_s
    expected = 1/30 - 1/300
    old_order = math.sqrt(math.log(10) * (30**-2 - 300**-2) / 2)
    assert actual == pytest.approx(expected, rel=1e-12)
    assert abs(old_order/expected - 1) > .1


def test_missing_neighbour_is_not_a_zero_spectrum():
    table = BeltTable('min')
    assert table.integral_flux(1.3, 1.5, 30).status == 'ok'
    assert table.integral_flux(1.2, 1.5, 30).status == 'no_model_BB0'
    middle = table.integral_flux(1.25, 1.5, 30)
    assert middle.value_per_cm2_s is None and middle.status == 'no_model_BB0'


@pytest.mark.parametrize('activity', ['min', 'max'])
def test_every_tabulated_shell_endpoint_is_known_but_next_float_outside_is_not(activity):
    table = BeltTable(activity)
    for L, rows in table.table.items():
        bb, spectrum = rows[-1]
        result = table.integral_flux(L, bb, 30)
        assert result.status == 'ok', (L, bb)
        assert result.value_per_cm2_s == pytest.approx(
            integrate_power_law(table.energies_MeV, spectrum, 30)), (L, bb)
        outside = table.integral_flux(L, math.nextafter(bb, math.inf), 30)
        assert outside.value_per_cm2_s is None and outside.status == 'no_model_BB0', (L, bb)


def test_no_lower_boundary_clamp_and_explicit_zero_remains_known():
    table = _synthetic_table([(1., 1.2, np.zeros_like(E_OST)), (1., 2., np.zeros_like(E_OST))])
    assert table.integral_flux(1., 1.1, 30).status == 'no_model_BB0'
    known_zero = table.integral_flux(1., 1.5, 30)
    assert known_zero.value_per_cm2_s == 0 and known_zero.status == 'ok'


@pytest.mark.parametrize('L,bb', [(float('nan'), 1), (2, float('inf')), (-float('inf'), 1)])
def test_nonfinite_coordinates_are_missing(L, bb):
    r = BeltTable().integral_flux(L, bb, 30)
    assert r.status == 'invalid_coordinates' and r.value_per_cm2_s is None


@pytest.mark.parametrize('energy', [.1, 300, 500, float('nan'), float('inf')])
def test_energy_outside_table_is_not_a_zero_flux(energy):
    r = BeltTable().integral_flux(2, 1, energy)
    assert r.status == 'no_model_energy' and r.value_per_cm2_s is None


def test_unknown_solar_phase_is_not_silently_maximum():
    with pytest.raises(ValueError, match='solar_activity'):
        BeltTable('typo')
