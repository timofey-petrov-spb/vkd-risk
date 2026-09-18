# -*- coding: utf-8 -*-
"""Численные проверки интегрирования и границ модели поясов (CONTRACT.md v3, раздел 8)."""
import math

import numpy as np
import pytest

from vkd.assess.trapped import BeltTable, integrate_power_law

E_OST = np.array([0.2, 0.6, 1.25, 3, 5, 12.5, 30, 50, 125, 300])   # узлы протонов прил. А


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


def test_no_model_outside_L_grid():
    t = BeltTable('min')
    r = t.integral_flux(0.9, 1.2, 30.0)
    assert r.value_per_cm2_s_sr is None and r.status == 'no_model_L'
    r = t.integral_flux(12.0, 1.2, 30.0)
    assert r.value_per_cm2_s_sr is None and r.status == 'no_model_L'
    assert t.integral_flux(None, 1.0, 30.0).status == 'no_model_L'


def test_beyond_mirror_is_physical_zero_not_missing():
    t = BeltTable('min')
    r = t.integral_flux(1.3, 1e6, 30.0)
    assert r.value_per_cm2_s_sr == 0.0 and r.status == 'beyond_mirror'


def test_inconsistent_BB0_flagged_not_clamped_silently():
    t = BeltTable('min')
    r_low, r_eq = t.integral_flux(1.3, 0.9, 30.0), t.integral_flux(1.3, 1.0, 30.0)
    assert r_low.status == 'inconsistent_BB0' and r_eq.status == 'ok'
    assert r_low.value_per_cm2_s_sr == pytest.approx(r_eq.value_per_cm2_s_sr)


def test_flux_decreases_with_BB0_and_L_interpolation_is_between_rows():
    t = BeltTable('min')
    a, b = t.integral_flux(1.3, 1.0, 30.0), t.integral_flux(1.3, 1.5, 30.0)
    assert a.value_per_cm2_s_sr >= b.value_per_cm2_s_sr
    lo, mid, hi = (t.integral_flux(L, 1.0, 30.0).value_per_cm2_s_sr for L in (1.3, 1.35, 1.4))
    assert min(lo, hi) <= mid <= max(lo, hi)
