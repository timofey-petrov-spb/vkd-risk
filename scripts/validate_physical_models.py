"""Offline physical audit: published references, independent numerical oracle, limitations.

Run: python scripts/validate_physical_models.py
NASA files are validation inputs ONLY; no forecast values are inserted into app results.
A successful exit means numerical checks passed, NOT absolute physical validation.
"""

from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

import numpy as np
from scipy.optimize import brentq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vkd.assess.seasonal import seasonal_hits_track, solar_longitude_deg, catalogue
from vkd.assess.meteoroids import grun_flux_1au

UTC = timezone.utc
REFERENCE = ROOT / "tests/fixtures/physics/nasa_2024"


def read_reference():
    manifest = json.loads((REFERENCE / "manifest.json").read_text())
    for name, metadata in manifest["files"].items():
        raw = (REFERENCE / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != metadata["sha256"]:
            raise ValueError("External validation reference hash mismatch: " + name)
    rows = []
    for line in (REFERENCE / "flux_data.txt").read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        f = line.split()
        rows.append(
            (datetime.fromisoformat(f[0] + "T" + f[1] + "+00:00"), *map(float, f[2:]))
        )
    return manifest, rows


def independent_solar(t):
    # Separate scalar implementation of published USNO approximation, no app helper.
    days = t / 86400 + 2440587.5 - 2451545
    anomaly = math.radians((357.529 + 0.98560028 * days) % 360)
    century = days / 36525
    return (
        280.459
        + 0.98564736 * days
        + 1.915 * math.sin(anomaly)
        + 0.020 * math.sin(2 * anomaly)
        - (5028.796195 * century + 1.1054348 * century**2) / 3600
    ) % 360


def zhr_mass_flux_2019(zhr, population_index, entry_speed_km_s, mass_g):
    """Moorhead 2019 Eqs 2,3,6: perpendicular TOA flux, m^-2 s^-1.

    Empirical alternative transfer law, not a new observation or NASA forecast.
    """
    r = population_index
    if not all(math.isfinite(v) for v in (zhr, r, entry_speed_km_s, mass_g)):
        raise ValueError("Finite physical parameters required")
    if zhr < 0 or r <= 1.3 or entry_speed_km_s <= 0 or mass_g <= 0:
        raise ValueError("Outside ZHR conversion domain")
    f65 = zhr * (13.1 * r - 16.5) * (r - 1.3) ** 0.748 / 37200
    fmg = f65 * r ** (9.775 * math.log10(29 / entry_speed_km_s))
    alpha = 2.3 * math.log10(r)
    return fmg * (mass_g / 0.001) ** (-alpha) / (1e6 * 3600)


def exact_circle(start, duration_min=360, step_s=60, phase=0.37):
    # Analytic Kepler orbit; does not use SGP4/OEM or the application interpolation.
    elapsed = np.arange(0, duration_min * 60 + step_s / 2, step_s)
    radius, mu, inclination = 6778.0, 398600.0, math.radians(51.6)
    omega = math.sqrt(mu / radius**3)
    theta = phase + omega * elapsed
    c, s = np.cos(theta), np.sin(theta)
    pos = radius * np.column_stack(
        (c, s * math.cos(inclination), s * math.sin(inclination))
    )
    vel = (
        radius
        * omega
        * np.column_stack((-s, c * math.cos(inclination), c * math.sin(inclination)))
    )
    times = [start + timedelta(seconds=float(t)) for t in elapsed]
    states = {
        "frame": "EME2000",
        "center": "EARTH",
        "time_system": "UTC",
        "position_unit": "km",
        "velocity_unit": "km/s",
        "samples": [
            {
                "t_utc": t.isoformat(),
                "position_km": p.tolist(),
                "velocity_km_s": v.tolist(),
            }
            for t, p, v in zip(times, pos, vel)
        ],
    }
    return times, pos, vel, states


def independent_stream_integrals(start, rows, step_s=0.5, duration_min=360):
    """Independent midpoint quadrature and analytic orbit, not production helpers.

    Same physical hypothesis; verifies implementation, not empirical calibration.
    Geometry is ray-sphere intersection solved as quadratic roots (app uses closest ray).
    """
    radius, mu, inc, phase = 6778.0, 398600.0, math.radians(51.6), 0.37
    elapsed = np.arange(step_s / 2, duration_min * 60, step_s)
    angle = phase + math.sqrt(mu / radius**3) * elapsed
    speed_station = math.sqrt(mu / radius)
    pos = radius * np.column_stack(
        (np.cos(angle), np.sin(angle) * math.cos(inc), np.sin(angle) * math.sin(inc))
    )
    vel = speed_station * np.column_stack(
        (-np.sin(angle), np.cos(angle) * math.cos(inc), np.cos(angle) * math.sin(inc))
    )
    lam = np.array([independent_solar(start.timestamp() + float(t)) for t in elapsed])
    out = {}
    for row in rows:
        delta = (lam - row["lambda_max_deg"] + 180) % 360 - 180
        shape = np.zeros(len(delta))
        for kind, z in [
            ("peak", row["zhr_peak"]),
            ("background", row["zhr_background"]),
        ]:
            slope = np.where(
                delta < 0,
                row["b_" + kind + "_before_per_deg"],
                row["b_" + kind + "_after_per_deg"],
            )
            shape += z * np.exp(-math.log(10) * slope * np.abs(delta))
        shape /= row["zhr_peak"] + row["zhr_background"]
        ra = np.radians(row["ra_max_deg"] + row["delta_ra_deg_per_deg"] * delta)
        dec = np.radians(row["dec_max_deg"] + row["delta_dec_deg_per_deg"] * delta)
        ray = np.column_stack(
            (np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec))
        )
        dot = np.einsum("ij,ij->i", pos, ray)
        disc = dot**2 - (radius**2 - 6478.0**2)
        # Quadratic intersection: two real roots and farthest root >=0 => blocked.
        blocked = (disc >= 0) & ((-dot + np.sqrt(np.maximum(disc, 0))) >= 0)
        entry = row["entry_speed_km_s"]
        local = math.sqrt(entry**2 + 2 * mu * (1 / radius - 1 / 6478.0))
        relative = np.linalg.norm(local * ray + vel, axis=1)
        flux = (
            row["k_per_m2_s_kg_alpha"]
            * 1e-6 ** (-row["alpha"])
            * shape
            * (local / entry) ** 2
            * relative
            / local
            / 4
        )
        out[row["name"]] = float(np.sum(flux[~blocked]) * step_s)
    return out


def orbit_and_radiation_checks():
    from vkd.orbit.trajectory import satellite_from_tle
    from vkd.orbit.magnetic import magnetic_coordinates
    from vkd.assess.magcoords import geodetic_to_ecef_km
    from vkd.assess.trapped import integrate_power_law

    fixtures = ROOT / "tests/orbit/fixtures"
    lines = (fixtures / "SGP4-VER.TLE").read_text().splitlines()
    satellites = {}
    for i, line in enumerate(lines):
        if line.startswith("1 ") and int(line[2:7]) in (5, 4632):
            satellites[int(line[2:7])] = satellite_from_tle(
                (line[:69] + "\n" + lines[i + 1][:69]).encode(), expected_norad=None
            )
    sat = None
    position_errors = []
    velocity_errors = []
    for line in (fixtures / "tcppver.out").read_text().splitlines():
        if line.endswith("xx"):
            sat = satellites.get(int(line.split()[0]))
            continue
        if sat is None or not line.strip():
            continue
        values = list(map(float, line.split()[:7]))
        code, pos, vel = sat.model.sgp4_tsince(values[0])
        if code:
            raise ValueError("SGP4 failed published reference: " + str(code))
        position_errors.append(float(np.max(np.abs(np.array(pos) - values[1:4]))))
        velocity_errors.append(float(np.max(np.abs(np.array(vel) - values[4:7]))))
    field_reference = json.loads((fixtures / "igrf13_reference.json").read_text())
    field_errors = []
    for row in field_reference["cases"]:
        pos = geodetic_to_ecef_km(
            row["lat_deg"], row["lon_deg"], row["alt_km"]
        ).reshape(1, 3)
        f = magnetic_coordinates(
            pos,
            np.array([row["lon_deg"]]),
            np.array([row["lat_deg"]]),
            np.array([row["alt_km"]]),
            [datetime.fromisoformat(row["time_utc"].replace("Z", "+00:00"))],
            ROOT / "data/orbit/IGRF13.shc",
        )
        field_errors.append(abs(float(f["B_nT"][0]) - row["B_nT"]))
    integration = []
    energy = np.array([1.0, 2.0, 5.0, 10.0, 30.0, 100.0, 300.0])
    for power in [-3.0, -2.0, -1.0, -0.5, 0.0, 1.0]:
        expected = (
            math.log(300 / 7)
            if power == -1
            else (300 ** (power + 1) - 7 ** (power + 1)) / (power + 1)
        )
        actual = integrate_power_law(energy, energy**power, 7.0)
        integration.append(abs(actual / expected - 1))
    return {
        "sgp4": {
            "vectors": len(position_errors),
            "max_position_component_error_km": max(position_errors),
            "max_velocity_component_error_km_s": max(velocity_errors),
            "passed": max(position_errors) < 1e-7 and max(velocity_errors) < 1e-8,
            "meaning": "Vallado published implementation reference, NOT actual ISS orbit prediction error",
        },
        "igrf": {
            "points": len(field_errors),
            "max_field_difference_nT": max(field_errors),
            "passed": max(field_errors) < 1,
            "meaning": "Independent evaluator, same IGRF13 coefficients; not storm field observation accuracy",
        },
        "energy_integration": {
            "analytic_spectra": len(integration),
            "max_relative_error": max(integration),
            "passed": max(integration) < 1e-12,
            "meaning": "Analytic power law integration, not absolute belt flux validation",
        },
    }


def collect():
    manifest, reference = read_reference()
    rows = catalogue()
    solar_errors = []
    for item in reference:
        solar_errors.append(
            abs(
                (float(solar_longitude_deg(item[0].timestamp())) - item[2] + 180) % 360
                - 180
            )
        )
    solar = {
        "count": len(reference),
        "max_abs_difference_deg": max(solar_errors),
        "p95_abs_difference_deg": float(np.percentile(solar_errors, 95)),
        "reference_rounding_deg": 0.001,
        "acceptance_deg": 0.02,
        "meaning": "External solar-coordinate agreement for 2024 only; not flux accuracy",
    }
    solar["passed"] = solar["max_abs_difference_deg"] < solar["acceptance_deg"]
    j5 = json.loads((ROOT / "data/meteoroids/grun_reference.json").read_text())[
        "j5_reference"
    ]
    errors = [
        abs(grun_flux_1au(r["mass_g"]) / r["F0_table_per_m2_year"] - 1) for r in j5
    ]
    grun = {
        "published_table_nodes": len(j5),
        "max_relative_difference": max(errors),
        "acceptance_relative": 0.005,
        "passed": max(errors) < 0.005,
        "meaning": "Agreement with rounded ECSS J-5, not observation validation",
    }
    cases = []
    for date in [
        "2024-05-05T12:00:00+00:00",
        "2024-05-10T12:00:00+00:00",
        "2024-06-07T12:00:00+00:00",
    ]:
        start = datetime.fromisoformat(date)
        times, _, _, states = exact_circle(start)
        actual = seasonal_hits_track(times, [400] * len(times), states)
        oracle = independent_stream_integrals(start, rows)
        oracle_coarse = independent_stream_integrals(start, rows, step_s=1)
        ns = sum(oracle.values())
        stream_error = abs(actual.N_streams / ns - 1)
        per_stream = [
            {
                "name": x["name"],
                "N_app": x["expected_hits"],
                "N_oracle": oracle[x["name"]],
                "absolute_difference": abs(x["expected_hits"] - oracle[x["name"]]),
            }
            for x in actual.contributions
        ]
        cases.append(
            {
                "start_utc": date,
                "duration_min": 360,
                "inclination_deg": 51.6,
                "N_streams_app": actual.N_streams,
                "N_streams_oracle": ns,
                "relative_difference": stream_error,
                "acceptance_relative": 0.005,
                "passed": stream_error < 0.005,
                "oracle_half_step_relative_change": abs(
                    sum(oracle_coarse.values()) / ns - 1
                ),
                "per_stream": per_stream,
                "meaning": "Independent implementation, same climate/geometry assumptions",
            }
        )
        print("Independent seasonal orbit check:", date, stream_error, flush=True)
    transfer = []
    for row in rows:
        zhr = row["zhr_peak"] + row["zhr_background"]
        r = 10 ** (row["alpha"] / 2.3)
        alternate = zhr_mass_flux_2019(zhr, r, row["entry_speed_km_s"], 0.001)
        original = row["k_per_m2_s_kg_alpha"] * 1e-6 ** (-row["alpha"])
        transfer.append(
            {
                "name": row["name"],
                "ecss_assumed_perpendicular_TOA_per_m2_s": original,
                "moorhead2019_same_catalogue_TOA_per_m2_s": alternate,
                "ratio_ecss_over_alternate": original / alternate,
                "status": "model_disagreement_not_calibration",
            }
        )
    # Published 2024 Table 2, visually checked. Peak parameters NOT fitted into the app.
    peaks = [
        ("Bootids", "2024-01-04T06:32:00+00:00", 230, 49, 41),
        ("ηAquarids", "2024-05-05T13:53:00+00:00", 338, -1, 66),
        ("Da.Arietids", "2024-06-09T22:39:00+00:00", 42, 24, 39),
        ("Perseids", "2024-08-12T14:59:00+00:00", 47, 58, 61),
        ("Geminids", "2024-12-14T02:10:00+00:00", 113, 32, 35),
    ]
    peak_checks = []
    for name, date, ra, dec, speed in peaks:
        row = next(r for r in rows if r["name"] == name)
        reference_time = datetime.fromisoformat(date)
        middle = reference_time.timestamp()

        def residual(t):
            return (
                float(solar_longitude_deg(t)) - row["lambda_max_deg"] + 180
            ) % 360 - 180

        predicted = brentq(residual, middle - 15 * 86400, middle + 15 * 86400)
        dot = math.sin(math.radians(row["dec_max_deg"])) * math.sin(
            math.radians(dec)
        ) + math.cos(math.radians(row["dec_max_deg"])) * math.cos(
            math.radians(dec)
        ) * math.cos(
            math.radians(row["ra_max_deg"] - ra)
        )
        peak_checks.append(
            {
                "name": name,
                "ecss_peak_utc": datetime.fromtimestamp(predicted, UTC).isoformat(),
                "nasa2024_peak_utc": date,
                "difference_hours": (predicted - middle) / 3600,
                "nominal_radiant_separation_deg": math.degrees(
                    math.acos(max(-1, min(1, dot)))
                ),
                "speed_difference_km_s": row["entry_speed_km_s"] - speed,
                "status": "external_model_comparison_not_observations",
            }
        )
    core = orbit_and_radiation_checks()
    return {
        "schema_version": 1,
        "git_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "core_physics": core,
        "source_files": manifest,
        "solar": solar,
        "grun_table": grun,
        "independent_stream_orbits": cases,
        "normalization_comparison": transfer,
        "peak_comparison_2024": peak_checks,
        "numerical_checks_passed": solar["passed"]
        and grun["passed"]
        and all(c["passed"] for c in cases)
        and all(c["passed"] for c in core.values()),
        "absolute_flux_observation_validation": "NOT_ESTABLISHED",
        "nasa_flux_direct_comparison": "NOT_COMPARABLE: energy thresholds and unshielded facing plate differ from mass-limited tumbling plate on actual ISS orbit",
        "open_findings": [
            "ECSS coefficient geometry remains assumed; independent empirical transfer differs",
            "Climatology misses 2024 annual changes; NASA ETA 161 ZHR vs ECSS peak+base 36.7",
            "Quiet-field cutoff is not a conservative upper bound on proton exposure in a storm",
            "Trapped proton absolute flux lacks a collocated independent particle measurement",
            "Numerical convergence, agreement between models and observation validation are distinct",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "examples/validation/physical_audit.json"
    )
    args = parser.parse_args()
    result = collect()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    print("Numerical checks:", result["numerical_checks_passed"])
    print(
        "Absolute physical flux validation:",
        result["absolute_flux_observation_validation"],
    )
    print(args.output)
    return 0 if result["numerical_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
