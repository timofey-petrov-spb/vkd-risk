"""Seasonal physics invariants and separately produced arithmetic/ephemeris controls.

These checks do not validate the absolute stream flux against measurements at ISS.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pytest

from vkd.assess.meteoroids import grun_flux_1au, SEC_PER_YEAR
from vkd.assess.seasonal import (
    ABSORBING_KM,
    CATALOGUE,
    annual_profiles,
    catalogue,
    comparison_sensitivity,
    local_speed,
    profiles,
    seasonal_hits_track,
    solar_longitude_deg,
    visible_rays,
)

ROOT = Path(__file__).resolve().parents[1]
CONTROLS = json.loads((ROOT / "data/meteoroids/seasonal_controls.json").read_text())
UTC = timezone.utc


def circle(start=None, minutes=90, phase=0):
    """Independent analytic circular orbit, inclination 51.6°, not SGP4 output."""
    start = start or datetime(2024, 6, 7, 12, tzinfo=UTC)
    omega = math.sqrt(398600 / 6778**3)
    inc = math.radians(51.6)
    times = [start + timedelta(minutes=i) for i in range(minutes + 1)]
    rows = []
    for i, t in enumerate(times):
        angle = phase + omega * i * 60
        c, s = math.cos(angle), math.sin(angle)
        rows.append(
            {
                "t_utc": t.isoformat(),
                "position_km": [6778 * c, 6778 * s * math.cos(inc), 6778 * s * math.sin(inc)],
                "velocity_km_s": [
                    -6778 * omega * s,
                    6778 * omega * c * math.cos(inc),
                    6778 * omega * c * math.sin(inc),
                ],
            }
        )
    return (
        times,
        [400] * len(times),
        dict(
            frame="EME2000",
            center="EARTH",
            time_system="UTC",
            position_unit="km",
            velocity_unit="km/s",
            samples=rows,
        ),
    )


def test_catalogue_matches_frozen_literal_source():
    assert hashlib.sha256(CATALOGUE.read_bytes()).hexdigest() == CONTROLS["source_c2_sha256"]
    assert len(catalogue()) == 49
    for row, control in zip(catalogue(), CONTROLS["stream_controls"]):
        assert row["name"] == control["name"]
        peak = row["k_per_m2_s_kg_alpha"] * (0.001 / 1000) ** (-row["alpha"])
        assert peak == pytest.approx(control["raw_peak_per_m2_s"], rel=1e-12, abs=0)
        for shift, key in [(-1, "q_at_minus_1_deg"), (0, "q_at_peak"), (1, "q_at_plus_1_deg")]:
            q = profiles(row["lambda_max_deg"] + shift, [row])[0]
            assert q == pytest.approx(control[key], rel=1e-12)
        assert profiles(row["lambda_max_deg"] + 360, [row])[0] == pytest.approx(1)


def test_solar_coordinates_against_archived_de421():
    controls = json.loads((ROOT / "data/meteoroids/seasonal_solar_controls.json").read_text())
    for row in controls["controls"]:
        t = datetime.fromisoformat(row["time_utc"].replace("Z", "+00:00")).timestamp()
        assert abs(float(solar_longitude_deg(t)) - row["solar_apparent_ecliptic_J2000_deg"]) < 0.004
        assert (
            abs(float(solar_longitude_deg(t, of_date=True)) - row["solar_apparent_ecliptic_of_date_deg"])
            < 0.006
        )


def test_one_sided_projection_is_quarter_not_sphere_area():
    # Uniform normals: cos(theta) is uniform on [-1, 1]. One face only.
    cos_theta = np.linspace(-1, 1, 20001)
    assert np.trapezoid(np.maximum(0, cos_theta), x=cos_theta) / 2 == pytest.approx(0.25)


def test_speed_and_shadow_against_separate_controls():
    for control in CONTROLS["speed_controls"]:
        speed = float(local_speed(control["entry_speed_km_s"], 6378 + control["h_km"]))
        assert speed == pytest.approx(control["local_speed_km_s"], rel=1e-12)
        station = control["satellite_speed_km_s"]
        assert (speed - station) / speed == pytest.approx(control["motion_factor_same_velocity_direction"])
        assert (speed + station) / speed == pytest.approx(
            control["motion_factor_opposite_velocity_direction"]
        )
    radius = 6778.0
    half_angle = math.asin(ABSORBING_KM / radius)
    assert math.degrees(half_angle) == pytest.approx(72.88950838326849)
    angles = np.array([0, half_angle - 1e-8, half_angle + 1e-8, math.pi])
    rays = np.column_stack((-np.cos(angles), np.sin(angles), np.zeros(4)))
    assert visible_rays(np.array([radius, 0, 0]), rays).tolist() == [False, False, True, True]
    with pytest.raises(ValueError):
        local_speed(8, radius)


def test_annual_time_mean_conserves_reference_without_double_counting():
    mean, audit = annual_profiles(2024)
    # Independent time midpoint quadrature, non-identical grid and reduction.
    a = datetime.fromisoformat(audit["from_utc"]).timestamp()
    dt = audit["duration_s"] / 50000
    points = a + (np.arange(50000) + 0.5) * dt
    independent = profiles(solar_longitude_deg(points)).mean(axis=1)
    assert np.max(np.abs(mean - independent)) < 3e-7
    rows = catalogue()
    peak = np.array([r["k_per_m2_s_kg_alpha"] * 1e-6 ** (-r["alpha"]) for r in rows])
    speed = np.array([r["entry_speed_km_s"] for r in rows])
    removed = sum(mean * peak * (1 - 2 * 398600 / 6478 / speed**2) / 4)
    grun = grun_flux_1au(0.001) / SEC_PER_YEAR
    assert 0.04 < removed / grun < 0.07
    sporadic = grun - removed
    assert sporadic + removed == pytest.approx(grun, rel=1e-14, abs=0)
    # Time weights matter: using equal solar-longitude bins is a different reference.
    uniform = CONTROLS["conditional_reference_mean"]["stream_plate_infty_uniform_longitude_per_m2_s"]
    assert not math.isclose(removed, uniform, rel_tol=1e-4, abs_tol=0)


@pytest.fixture(scope="module")
def summer_result():
    return seasonal_hits_track(*circle())


def test_sum_units_area_and_geometry(summer_result):
    result = summer_result
    assert result.N == pytest.approx(result.N_sporadic_adjusted + result.N_streams, rel=1e-14, abs=0)
    assert result.N_streams == pytest.approx(
        sum(x["expected_hits"] for x in result.contributions), rel=1e-14, abs=0
    )
    assert result.N_sporadic_adjusted < result.N_mean_background
    assert result.N_mean_background == pytest.approx(5.609728448e-7 / 4, rel=1e-9, abs=0)
    assert result.method_status == "engineering_approximation" and result.streams_included
    assert len(result.contributions) == 49
    twice = seasonal_hits_track(*circle(), area_m2=2)
    assert twice.N == pytest.approx(2 * result.N, rel=1e-12, abs=0)
    assert twice.N_streams == pytest.approx(2 * result.N_streams, rel=1e-12, abs=0)
    assert result.sensitivity["half_step_relative_change"] < 0.001
    assert result.sensitivity["hypotheses_N"]["no_earth_shadow"] >= result.N
    assert not result.sensitivity["invalid_hypotheses"]


@pytest.mark.parametrize(
    "start", [datetime(2024, 6, 7, 12, tzinfo=UTC), datetime(2024, 12, 31, 23, 30, tzinfo=UTC)]
)
def test_integral_additive_even_across_new_year(start):
    times, alts, states = circle(start)
    full = seasonal_hits_track(times, alts, states)
    a = seasonal_hits_track(times[:46], alts[:46], states)
    b = seasonal_hits_track(times[45:], alts[45:], states)
    for key in ("N", "N_streams", "N_sporadic_adjusted"):
        assert getattr(full, key) == pytest.approx(getattr(a, key) + getattr(b, key), rel=1e-12, abs=0)


def test_actual_date_and_orbit_direction_change_stream_contribution(summer_result):
    winter = seasonal_hits_track(*circle(datetime(2024, 2, 7, 12, tzinfo=UTC)))
    other_phase = seasonal_hits_track(*circle(minutes=20, phase=math.pi))
    phase_zero = seasonal_hits_track(*circle(minutes=20))
    assert summer_result.N_streams > 2 * winter.N_streams
    assert abs(other_phase.N_streams / phase_zero.N_streams - 1) > 0.1


@pytest.mark.parametrize(
    "bad", ["frame", "units", "missing", "duplicate", "gap", "altitude", "nan", "naive", "mass", "step"]
)
def test_missing_or_invalid_physics_never_becomes_zero(bad):
    ts, hs, state = circle(minutes=5)
    kw = {}
    if bad == "frame":
        state["frame"] = "TEME"
    if bad == "units":
        state["position_unit"] = "m"
    if bad == "missing":
        state["samples"].pop()
    if bad == "duplicate":
        state["samples"].append(deepcopy(state["samples"][0]))
    if bad == "gap":
        ts.pop(2)
        hs.pop(2)
    if bad == "altitude":
        hs[1] = 40
    if bad == "nan":
        state["samples"][1]["velocity_km_s"][0] = float("nan")
    if bad == "naive":
        ts = [t.replace(tzinfo=None) for t in ts]
    if bad == "mass":
        kw["mass_g"] = 1e-20
    if bad == "step":
        kw["integration_step_s"] = 300
    with pytest.raises(ValueError):
        seasonal_hits_track(ts, hs, state, **kw)


def test_paired_hypotheses_can_revoke_an_apparent_preference():
    def row(base, other):
        return {"sensitivity": {"hypotheses_N": {"base": base, "other": other}, "invalid_hypotheses": {}}}

    assert comparison_sensitivity([row(1, 2), row(2, 4)], 5)["stable"]
    assert not comparison_sensitivity([row(1, 3), row(2, 2)], 5)["stable"]
    assert not comparison_sensitivity([row(1, 1), row(1.01, 2)], 5)["stable"]
    assert not comparison_sensitivity([{}, row(1, 1)], 5)["stable"]
