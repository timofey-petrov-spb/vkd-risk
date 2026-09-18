# -*- coding: utf-8 -*-
"""B2 против контрольных данных A5 (data/meteoroids/grun_reference.json) и свойств из §3 спецификации."""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from vkd.assess.meteoroids import grun_flux_1au, meteoroid_hits, meteoroid_hits_track

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = json.load(open(os.path.join(ROOT, 'data', 'meteoroids', 'grun_reference.json'), encoding='utf-8'))
T0 = datetime(2024, 5, 1, tzinfo=timezone.utc)


def _track(minutes, alt):
    ts = [T0 + timedelta(minutes=i) for i in range(minutes + 1)]
    alts = [alt(i) if callable(alt) else alt for i in range(minutes + 1)]
    return ts, alts


def test_formula_against_30_j5_pairs_within_0_6_percent():
    for row in REF['j5_reference']:
        assert grun_flux_1au(row['mass_g']) == pytest.approx(row['F0_formula_per_m2_year'], rel=1e-10)
        assert abs(grun_flux_1au(row['mass_g']) / row['F0_table_per_m2_year'] - 1) < 6e-3


def test_baseline_control_6h_and_8h_rtol_1e10():
    c = REF['baseline_control']
    assert meteoroid_hits(400.0, 1.0, 6.0, c['mass_g']).N == pytest.approx(c['N_1m2_6h'], rel=1e-10)
    assert meteoroid_hits(400.0, 1.0, 8.0, c['mass_g']).N == pytest.approx(c['N_1m2_8h'], rel=1e-10)
    ts, alts = _track(360, 400.0)
    assert meteoroid_hits_track(ts, alts, 1.0, c['mass_g']).N == pytest.approx(c['N_1m2_6h'], rel=1e-10)


def test_track_integral_linear_in_area_additive_on_split_and_decreasing_in_mass():
    ts, alts = _track(360, lambda i: 410.0 + 8.0 * ((i % 92) / 92.0))
    n1 = meteoroid_hits_track(ts, alts, 1.0).N
    assert meteoroid_hits_track(ts, alts, 2.5).N == pytest.approx(2.5 * n1, rel=1e-12)
    left = meteoroid_hits_track(ts[:181], alts[:181], 1.0).N
    right = meteoroid_hits_track(ts[180:], alts[180:], 1.0).N
    assert left + right == pytest.approx(n1, rel=1e-12)
    assert meteoroid_hits_track(ts, alts, 1.0, 1e-2).N < n1 < meteoroid_hits_track(ts, alts, 1.0, 1e-4).N


def test_track_uses_actual_dt_not_point_count():
    ts, alts = _track(360, 400.0)
    coarse = meteoroid_hits_track(ts[::5], alts[::5], 1.0).N        # шаг 5 мин, те же концы
    assert coarse == pytest.approx(meteoroid_hits_track(ts, alts, 1.0).N, rel=1e-12)
    with pytest.raises(ValueError):
        meteoroid_hits_track(ts[:1], alts[:1])
    with pytest.raises(ValueError):
        meteoroid_hits_track([T0, T0], [400.0, 400.0])


def test_altitude_dependence_j6_interpolation_is_explicit():
    ts, _ = _track(360, 400.0)
    n400 = meteoroid_hits_track(ts, [400.0] * 361, 1.0).N
    n600 = meteoroid_hits_track(ts, [600.0] * 361, 1.0).N
    assert n600 != n400 and abs(n600 / n400 - 1) < 0.05        # между узлами 400 и 800 км линейно, различие малое
    with pytest.raises(ValueError):
        meteoroid_hits_track(ts, [50.0] * 361, 1.0)              # ниже Table J-6 — отказ, не clamping
