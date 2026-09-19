"""Independent A5 arithmetic controls; NOT the application's meteoroid estimator.

No vkd imports, network access, orbit propagation, or recommendation output.
All catalogue-dependent numbers are conditional on the interpretation documented
in METEOROIDS_SEASONAL_SPEC.md; they must not be labelled validated station flux.
Run with --check to compare the committed controls, or --write to regenerate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
INPUT = HERE / 'ecss_c2_streams.json'
OUTPUT = HERE / 'seasonal_controls.json'
MU_KM3_S2 = 398600.0  # ECSS C-16
EARTH_RADIUS_KM = 6378.0  # ECSS C-27
ATMOSPHERE_KM = 100.0  # ECSS C-27
MASS_KG = 1e-6


def delta_deg(longitude_deg: float, peak_deg: float) -> float:
    return (longitude_deg - peak_deg + 180.0) % 360.0 - 180.0


def component(offset_deg: float, before: float, after: float) -> float:
    slope = before if offset_deg < 0.0 else after
    return 10.0 ** (-slope * abs(offset_deg))


def profile(row: dict, offset_deg: float) -> float:
    """Proposed total-peak normalization, NOT a resolved source ambiguity."""
    zp, zb = row['zhr_peak'], row['zhr_background']
    peak = component(offset_deg, row['b_peak_before_per_deg'], row['b_peak_after_per_deg'])
    base = component(offset_deg, row['b_background_before_per_deg'], row['b_background_after_per_deg']) if zb else 0.0
    return (zp * peak + zb * base) / (zp + zb)


def mean_component(before: float, after: float, truncate_1pct: bool) -> float:
    """Exact longitude mean, NOT a time average over calendar year 2024."""
    sides = []
    for slope in (before, after):
        limit = min(180.0, 2.0 / slope) if truncate_1pct else 180.0
        sides.append(-math.expm1(-math.log(10.0) * slope * limit) / (math.log(10.0) * slope))
    return sum(sides) / 360.0


def mean_profile(row: dict, truncate_1pct: bool) -> float:
    zp, zb = row['zhr_peak'], row['zhr_background']
    peak = mean_component(row['b_peak_before_per_deg'], row['b_peak_after_per_deg'], truncate_1pct)
    base = mean_component(row['b_background_before_per_deg'], row['b_background_after_per_deg'], truncate_1pct) if zb else 0.0
    return (zp * peak + zb * base) / (zp + zb)


def make_controls() -> dict:
    source = json.loads(INPUT.read_text(encoding='utf-8'))
    rows = source['streams']
    assert len(rows) == 49
    mass_g = MASS_KG * 1000.0
    grun = ((2200.0 * mass_g ** .306 + 15.0) ** -4.38
            + 1.3e-9 * (mass_g + 1e11 * mass_g**2 + 1e27 * mass_g**4) ** -.36
            + 1.3e-16 * (mass_g + 1e6 * mass_g**2) ** -.85)
    stream_controls = []
    mean_infty = 0.0
    mean_toa = 0.0
    for index, row in enumerate(rows):
        peak_raw = row['k_per_m2_s_kg_alpha'] * MASS_KG ** -row['alpha']
        mean_q = mean_profile(row, False)
        entry = row['entry_speed_km_s']
        v_infty_squared = entry**2 - 2.0 * MU_KM3_S2 / (EARTH_RADIUS_KM + ATMOSPHERE_KM)
        assert v_infty_squared > 0.0
        conditional_mean = peak_raw * mean_q / 4.0
        mean_toa += conditional_mean
        mean_infty += conditional_mean * v_infty_squared / entry**2
        stream_controls.append({
            'c2_row_1based': index + 1, 'name': row['name'],
            'raw_peak_per_m2_s': peak_raw,
            'q_at_minus_1_deg': profile(row, -1.0), 'q_at_peak': profile(row, 0.0),
            'q_at_plus_1_deg': profile(row, 1.0),
            'mean_q_uniform_longitude_untruncated': mean_q,
            'mean_q_uniform_longitude_components_cut_at_1pct': mean_profile(row, True),
            'conditional_plate_mean_toa_per_m2_s': conditional_mean,
            'conditional_plate_mean_infty_per_m2_s': conditional_mean * v_infty_squared / entry**2,
        })
    geometry = []
    for bins in (100, 1000, 10000):
        # Uniform u = cos(theta), midpoint quadrature. One-sided projected area.
        value = sum(max(0.0, -1.0 + (i + .5) * 2.0 / bins) for i in range(bins)) / bins
        geometry.append({'uniform_cos_theta_bins': bins, 'mean_projected_area_ratio': value,
                         'absolute_error_from_one_quarter': abs(value - .25)})
    speed = []
    for entry in (18.0, 38.0, 66.0):
        h_km, satellite_km_s = 400.0, 7.67
        local_squared = entry**2 - 2.0 * MU_KM3_S2 / (EARTH_RADIUS_KM + ATMOSPHERE_KM) + 2.0 * MU_KM3_S2 / (EARTH_RADIUS_KM + h_km)
        local = math.sqrt(local_squared)
        speed.append({'entry_speed_km_s': entry, 'h_km': h_km, 'satellite_speed_km_s': satellite_km_s,
                      'local_speed_km_s': local, 'average_focusing_toa_to_altitude': local_squared / entry**2,
                      'motion_factor_same_velocity_direction': (local - satellite_km_s) / local,
                      'motion_factor_perpendicular': math.hypot(local, satellite_km_s) / local,
                      'motion_factor_opposite_velocity_direction': (local + satellite_km_s) / local})
    radius = EARTH_RADIUS_KM + 400.0
    shield_angle = math.degrees(math.asin((EARTH_RADIUS_KM + ATMOSPHERE_KM) / radius))
    result = {
        'schema_version': 1, 'method_id': 'a5-seasonal-arithmetic-controls-v1',
        'status': 'conditional_controls_not_station_prediction',
        'source_c2_sha256': hashlib.sha256(INPUT.read_bytes()).hexdigest(),
        'mass_kg': MASS_KG, 'mass_g': mass_g,
        'geometry': 'one-sided plate with uniformly random surface normal',
        'catalogue_assumptions': {
            'raw_flux_convention': 'hypothetical perpendicular TOA flux; not independently established for C-2 k',
            'profile_normalization': '(Zp*q_peak + Zb*q_base)/(Zp+Zb); proposed interpretation',
            'annual_average': 'uniform solar longitude; arithmetic cross-check only, not time-weighted 2024',
            'source_policy': 'all 49 literal C-2 rows, Bootids Zp=10 unchanged',
        },
        'geometric_projection_quadrature': geometry,
        'angular_wrap_controls': [{'longitude_deg': a, 'peak_deg': b, 'signed_delta_deg': delta_deg(a,b)}
                                  for a,b in [(1.0,359.0),(359.0,1.0),(721.0,359.0),(46.5,46.5)]],
        'speed_controls': speed,
        'shielding_control': {
            'h_km': 400.0, 'earth_radius_km': EARTH_RADIUS_KM, 'absorbing_atmosphere_km': ATMOSPHERE_KM,
            'straight_line_earth_cone_half_angle_deg': shield_angle,
            'isotropic_geometrical_visible_fraction': (1.0 + math.cos(math.radians(shield_angle))) / 2.0,
            'radiant_angle_from_nadir_deg_and_visible': [[0.0,False],[60.0,False],[90.0,True],[180.0,True]],
            'warning': 'Straight-line geometric test only; not gravitationally bent stream shadow.',
        },
        'stream_controls': stream_controls,
        'conditional_reference_mean': {
            'grun_infty_plate_per_m2_s': grun,
            'stream_plate_toa_uniform_longitude_per_m2_s': mean_toa,
            'stream_plate_infty_uniform_longitude_per_m2_s': mean_infty,
            'ecss_subtracted_sporadic_infty_per_m2_s': grun - mean_infty,
            'reference_stream_fraction': mean_infty / grun,
            'warning': 'Not a runtime correction or validation of C-2 normalization; see assumptions.',
        },
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    payload = make_controls()
    if args.write:
        OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    elif args.check:
        expected = json.loads(OUTPUT.read_text(encoding='utf-8'))
        assert expected == payload, 'Committed controls differ from independent calculation'
        assert all(abs(row['q_at_peak'] - 1.0) < 1e-14 for row in payload['stream_controls'])
        assert all(row['mean_q_uniform_longitude_components_cut_at_1pct'] <= row['mean_q_uniform_longitude_untruncated'] for row in payload['stream_controls'])
        assert all(g['absolute_error_from_one_quarter'] < 1e-12 for g in payload['geometric_projection_quadrature'])
        print('A5: 49 stream arithmetic controls, 3 projection integrals, 3 speed cases, wrapping/shielding: PASS')
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
