"""External reference integrity and independent empirical conversion controls."""

import math
import numpy as np
import pytest

from scripts.validate_physical_models import read_reference, zhr_mass_flux_2019
from vkd.assess.seasonal import solar_longitude_deg


def test_tradeoff_title_does_not_assert_a_conflict_between_mechanisms():
    from app.ui import VERDICT_TITLE

    # trade_off also represents an unstable SAME-mechanism physical hypothesis.
    assert VERDICT_TITLE["trade_off"] == "Однозначного выбора нет"


def test_published_hourly_solar_coordinates_not_just_six_selected_dates():
    manifest, rows = read_reference()
    assert manifest["usage"].startswith("validation only")
    assert len(rows) == 8791
    expected = np.array([r[2] for r in rows])
    actual = solar_longitude_deg(np.array([r[0].timestamp() for r in rows]))
    assert np.max(np.abs((actual - expected + 180) % 360 - 180)) < 0.02


def test_moorhead_equations_have_no_extra_area_or_time_factor():
    # v=29 km/s makes Eq 3 exactly unity; choose r=2, mass=1 mg.
    expected = 100 * 9.7 * math.pow(0.7, 0.748) / 37200 / 1e6 / 3600
    actual = zhr_mass_flux_2019(100, 2, 29, 0.001)
    assert actual == pytest.approx(expected, rel=1e-14)
    assert zhr_mass_flux_2019(100, 2, 29, 0.002) / actual == pytest.approx(
        2 ** (-2.3 * math.log10(2))
    )
    assert zhr_mass_flux_2019(0, 2, 29, 0.001) == 0


@pytest.mark.parametrize(
    "args",
    [
        (-1, 2, 29, 0.001),
        (1, 1.2, 29, 0.001),
        (1, 2, 0, 0.001),
        (1, 2, 29, 0),
        (1, 2, float("nan"), 0.001),
    ],
)
def test_empirical_reference_rejects_outside_domain(args):
    with pytest.raises(ValueError):
        zhr_mass_flux_2019(*args)


def test_production_transfer_hypothesis_matches_independent_scalar_conversion():
    from tests.test_seasonal_meteoroids import circle
    from vkd.assess.seasonal import seasonal_hits_track, catalogue, annual_profiles
    from vkd.assess.meteoroids import factors_table_j6

    rows = catalogue()
    actual = seasonal_hits_track(*circle())
    means, _ = annual_profiles(2024)
    contribution = {r["name"]: r["expected_hits"] for r in actual.contributions}
    alternate_streams = 0.0
    alternate_removed = 0.0
    for row, mean in zip(rows, means):
        baseline = row["k_per_m2_s_kg_alpha"] * 1e-6 ** (-row["alpha"])
        alt = zhr_mass_flux_2019(
            row["zhr_peak"] + row["zhr_background"],
            10 ** (row["alpha"] / 2.3),
            row["entry_speed_km_s"],
            0.001,
        )
        alternate_streams += contribution[row["name"]] * alt / baseline
        alternate_removed += (
            alt * mean * (1 - 2 * 398600 / 6478 / row["entry_speed_km_s"] ** 2) / 4
        )
    expected = (
        actual.N_mean_background
        - alternate_removed * math.prod(factors_table_j6(400)) * 90 * 60
        + alternate_streams
    )
    assert actual.sensitivity["hypotheses_N"]["zhr_transfer_2019"] == pytest.approx(
        expected, rel=1e-11
    )
