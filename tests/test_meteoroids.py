# -*- coding: utf-8 -*-
"""Метеороиды по ECSS: контрольные числа стандарта и коллеги (Т3, численные эталоны)."""
import pytest

from vkd.assess.meteoroids import factors_table_j6, grun_flux_1au, meteoroid_hits


@pytest.mark.parametrize('m_g, table', [(1e-12, 1.09e3), (1e-9, 9.51e1), (1e-6, 1.49), (1e-4, 1.04e-2),
                                        (1e-3, 5.97e-4), (1e-2, 3.07e-5), (1e-1, 1.49e-6), (1.0, 7.02e-8), (1e2, 1.50e-10)])
def test_grun_matches_ecss_table_j5(m_g, table):
    """Table J-5 ECSS-E-ST-10-04C Rev.1, с. 194 — Nmet на 1 а.е., м⁻²·год⁻¹."""
    assert grun_flux_1au(m_g) == pytest.approx(table, rel=6e-3)


def test_control_number_matches_codex():
    """400 км, 1 м², 6 ч, m ≥ 1e-3 г → 5,609728448e-7 (journal/friend.md, спецификация A5)."""
    r = meteoroid_hits(400.0, 1.0, 6.0, 1e-3)
    assert r.N == pytest.approx(5.609728448e-7, rel=1e-6)
    assert (r.G, r.s_f, r.K) == (2.00, 0.63, 1.09)
    assert r.streams_included is False


def test_out_of_range_refuses_not_clamps():
    with pytest.raises(ValueError):
        grun_flux_1au(1e-15)
    with pytest.raises(ValueError):
        factors_table_j6(50.0)


def test_linear_in_area_and_time():
    a = meteoroid_hits(420.0, 1.0, 6.0).N
    assert meteoroid_hits(420.0, 2.0, 6.0).N == pytest.approx(2 * a)
    assert meteoroid_hits(420.0, 1.0, 3.0).N == pytest.approx(a / 2)
