"""ECSS C.1.4 seasonal engineering model on the same one-sided 1 m² plate.

Not an operational EVA risk/damage model. Catalogue geometry and radiant epoch
remain explicit hypotheses (see docs/methods/METEOROIDS_SEASONAL_SPEC.md).
Earth shielding uses geocentric rays, spacecraft motion uses relative velocity.
All fluxes here are cumulative number fluxes above mass_g, in m^-2 s^-1.
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from scipy.optimize import brentq
from .meteoroids import grun_flux_1au, factors_table_j6, SEC_PER_YEAR

MODEL_ID = "ecss-seasonal-engineering-v1"
ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = ROOT / "data/meteoroids/ecss_c2_streams.json"
CATALOGUE_SHA256 = "3b0a7a7cbee0c704c083d71603059eff58c5569846d00446fa31bd54f1d8bc6b"
CATALOGUE_ID = "ecss_streams:c2-2020"
METHOD_ID = "ecss_seasonal:" + MODEL_ID
MU = 398600.0  # km^3/s^2, ECSS C-16
EARTH_KM = 6378.0  # ECSS C-27
ABSORBING_KM = EARTH_KM + 100.0
LIMITS_RU = (
    "Сезонная инженерная оценка ECSS C-2: 49 климатологических потоков; "
    "всплески конкретного года не предсказываются. Нормировка k на перпендикулярную площадь "
    "и эпоха радиантов J2000 — допущения, проверены альтернативные варианты. "
    "Средняя гравитационная поправка и прямолинейная тень не описывают локальную "
    "гравитационную фокусировку. Пластина не моделирует скафандр; техногенный мусор не учтён. "
    "Средний фон имеет неопределённость ×0,33…3 (ECSS J.2.3.2); "
    "разброс гипотез — не доверительный интервал и не вероятность повреждения."
)
RULE_RU = (
    "ECSS 10-1, C-2, C-5–C-7, C-25: средний Grün минус годовой средний вклад потоков "
    "в едином пространственном эталоне, затем фон × J-6 и сумма направленных потоков даты. "
    "Потоки: kg, м⁻²·с⁻¹; односторонняя случайная пластина, /4; "
    "поправка скорости по инерциальным состояниям МКС, тень Земли; интеграл по фактическому времени."
)


def catalogue():
    raw = CATALOGUE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != CATALOGUE_SHA256:
        raise ValueError("ECSS C-2 catalogue changed: review source, model version and controls")
    data = json.loads(raw)
    rows = data["streams"]
    if len(rows) != 49:
        raise ValueError("ECSS C-2: expected 49 literal source rows")
    return tuple(rows)


def solar_longitude_deg(unix_s, *, of_date=False):
    """USNO approximate apparent solar longitude, with general precession to J2000.

    2000–2050 application domain. Cross-checked with six archived DE421 controls
    to <0.01 degree; this is a deterministic approximation, not DE421 itself.
    A ±0.02° solar-coordinate sensitivity is reported, not a rigorous bound.
    """
    d = np.asarray(unix_s, dtype=float) / 86400.0 + 2440587.5 - 2451545.0
    g = np.deg2rad((357.529 + 0.98560028 * d) % 360)
    lam = 280.459 + 0.98564736 * d + 1.915 * np.sin(g) + 0.020 * np.sin(2 * g)
    T = d / 36525.0
    if not of_date:
        lam -= (5028.796195 * T + 1.1054348 * T * T) / 3600.0
    return lam % 360.0


def profiles(lam, rows=None, *, truncate=False, peak_only=False, bootids110=False):
    rows = rows or catalogue()
    lam = np.asarray(lam, dtype=float)
    values = []
    for row in rows:
        d = (lam - row["lambda_max_deg"] + 180.0) % 360.0 - 180.0
        zp = 110.0 if bootids110 and row["name"] == "Bootids" else row["zhr_peak"]
        zb = row["zhr_background"]
        terms = []
        for prefix, z in [("peak", zp), ("background", zb)]:
            slope = np.where(
                d < 0, row["b_" + prefix + "_before_per_deg"], row["b_" + prefix + "_after_per_deg"]
            )
            q = 10.0 ** (-slope * np.abs(d))
            if truncate:
                q = np.where(q >= 0.01, q, 0.0)
            terms.append(z * q)
        values.append((terms[0] + terms[1]) / (zp if peak_only else zp + zb))
    return np.asarray(values)


@lru_cache(maxsize=32)
def annual_profiles(year, truncate=False, peak_only=False, bootids110=False, of_date=False, shift_deg=0.0):
    """Time-weighted full solar cycle, independent of the requested EVA windows."""
    t0 = datetime(year, 1, 1, tzinfo=timezone.utc).timestamp()
    l0 = float(solar_longitude_deg(t0, of_date=of_date))

    def crossing(days):
        # Root lies at the *next* passage through the start longitude.
        return (float(solar_longitude_deg(t0 + days * 86400, of_date=of_date)) - l0 + 180) % 360 - 180

    days = brentq(crossing, 364.0, 367.0, xtol=1e-10)
    means = []
    for n in (16384, 32768):
        ts = np.linspace(t0, t0 + days * 86400, n + 1)
        q = profiles(
            solar_longitude_deg(ts, of_date=of_date) + shift_deg,
            truncate=truncate,
            peak_only=peak_only,
            bootids110=bootids110,
        )
        means.append(np.trapezoid(q, x=ts, axis=1) / (days * 86400))
    err = float(np.max(np.abs(means[1] - means[0])))
    return means[1], {
        "from_utc": datetime.fromtimestamp(t0, timezone.utc).isoformat(),
        "to_utc": datetime.fromtimestamp(t0 + days * 86400, timezone.utc).isoformat(),
        "duration_s": days * 86400,
        "quadrature_intervals": 32768,
        "max_profile_mean_change": err,
        "method": "time-weighted full solar longitude cycle",
    }


def visible_rays(position_km, radiant):
    """Straight geocentric ray to the radiant; tangent is blocked."""
    dot = np.sum(position_km * radiant, axis=-1)
    closest2 = np.sum(position_km * position_km, axis=-1) - dot * dot
    return (dot >= 0) | (closest2 > ABSORBING_KM**2)


def local_speed(entry_km_s, radius_km):
    vinf2 = np.asarray(entry_km_s) ** 2 - 2 * MU / ABSORBING_KM
    if np.any(vinf2 <= 0):
        raise ValueError("Stream speed is not hyperbolic at Earth")
    return np.sqrt(vinf2 + 2 * MU / np.asarray(radius_km))


@dataclass(frozen=True)
class SeasonalResult:
    N: float
    N_mean_background: float
    N_sporadic_adjusted: float
    N_streams: float
    mass_g: float
    area_m2: float
    coverage_fraction: float
    method_status: str
    streams_included: bool
    contributions: tuple
    sensitivity: dict
    provenance: dict
    rule: str = RULE_RU
    limits: str = LIMITS_RU

    def as_dict(self):
        return asdict(self)


def seasonal_hits_track(
    times_utc,
    alts_km,
    states,
    area_m2=1.0,
    mass_g=1e-3,
    max_step_s=60.0,
    integration_step_s=10.0,
    _prepare=False,
):
    """One full window. Missing/gapped/unsynchronised states fail; never fill with zero.

    Hermite interpolation uses the supplied position AND inertial velocity and
    samples every <=10 s to resolve the geometric limb; a 5 s check is reported.
    This refines geometry, not accuracy of the original ephemeris or flux model.
    """
    ts = np.array([t.timestamp() for t in times_utc], dtype=float)
    if (
        len(ts) < 2
        or len(alts_km) != len(ts)
        or not all(t.tzinfo is not None and t.utcoffset() is not None for t in times_utc)
    ):
        raise ValueError("Seasonal model requires at least two UTC-aware trajectory points")
    if any(not 2000 <= t.year <= 2050 for t in times_utc):
        raise ValueError("Solar approximation supported only for 2000–2050")
    if not all(math.isfinite(x) and x > 0 for x in (area_m2, mass_g, max_step_s, integration_step_s)):
        raise ValueError("Area, mass and integration steps must be finite and positive")
    if integration_step_s > 10 or max_step_s > 60:
        raise ValueError("Seasonal geometry requires <=60 s source states and <=10 s integration")
    if np.any(np.diff(ts) <= 0) or np.any(np.diff(ts) > max_step_s + 1e-6):
        raise ValueError("Seasonal states are unordered or have gaps larger than 60 s")
    alt = np.asarray(alts_km, dtype=float)
    if not np.isfinite(alt).all() or np.any((alt < 200) | (alt > 2000)):
        raise ValueError("Seasonal ISS/LEO model altitude domain: 200–2000 km")
    if (
        states.get("frame") != "EME2000"
        or states.get("center") != "EARTH"
        or states.get("time_system") != "UTC"
        or states.get("position_unit") != "km"
        or states.get("velocity_unit") != "km/s"
    ):
        raise ValueError("Seasonal model requires geocentric EME2000 positions km and velocities km/s")
    if states.get("content_sha256"):
        content = {k: v for k, v in states.items() if k != "content_sha256"}
        encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if hashlib.sha256(encoded).hexdigest() != states["content_sha256"]:
            raise ValueError("Inertial state payload hash mismatch")
    raw = states.get("samples", [])
    try:
        by_time = {}
        for sample in raw:
            when = datetime.fromisoformat(sample["t_utc"].replace("Z", "+00:00"))
            if when.tzinfo is None or when.utcoffset() is None:
                raise ValueError("Naive state timestamp")
            by_time[when.timestamp()] = sample
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Malformed inertial state record") from exc
    if len(by_time) != len(raw) or any(t not in by_time for t in ts):
        raise ValueError("Missing or duplicate inertial states for seasonal track")
    pos = np.asarray([by_time[t]["position_km"] for t in ts], dtype=float)
    vel = np.asarray([by_time[t]["velocity_km_s"] for t in ts], dtype=float)
    if (
        pos.shape != (len(ts), 3)
        or vel.shape != pos.shape
        or not np.isfinite(pos).all()
        or not np.isfinite(vel).all()
    ):
        raise ValueError("Invalid seasonal inertial vectors")
    radius = np.linalg.norm(pos, axis=1)
    if np.any(np.abs(radius - (EARTH_KM + alt)) > 35) or np.any(np.linalg.norm(vel, axis=1) > 12):
        raise ValueError("Inertial states inconsistent with LEO geographic trajectory")
    from scipy.interpolate import CubicHermiteSpline

    spline = CubicHermiteSpline(ts - ts[0], pos, vel)
    rows = catalogue()
    peaks = np.array([r["k_per_m2_s_kg_alpha"] * (mass_g / 1000.0) ** (-r["alpha"]) for r in rows])
    entries = np.array([r["entry_speed_km_s"] for r in rows])
    toa_to_infty = (entries**2 - 2 * MU / ABSORBING_KM) / entries**2
    grun = grun_flux_1au(mass_g) / SEC_PER_YEAR

    # One prepared horizon serves manual windows and the automatic scan.
    # Cumulative integrals avoid recomputing 49 streams for every candidate.
    from scipy.integrate import cumulative_trapezoid

    grid_cache = {}
    left_s, right_s = 0.0, ts[-1] - ts[0]

    def summarize_grid(grid):
        x, c_stream, c_visible, c_bg, c_mean, audit, removed = grid
        lo, hi = np.searchsorted(x, [left_s, right_s])
        if hi >= len(x) or abs(x[lo] - left_s) > 1e-6 or abs(x[hi] - right_s) > 1e-6:
            raise ValueError("Seasonal window boundaries must coincide with supplied trajectory times")
        parts = (c_stream[:, hi] - c_stream[:, lo]).tolist()
        visible = (c_visible[:, hi] - c_visible[:, lo]).tolist()
        ns = float(sum(parts))
        nb = float(c_bg[hi] - c_bg[lo])
        return nb + ns, nb, ns, parts, visible, audit, removed, float(c_mean[hi] - c_mean[lo])

    def calculate(
        step,
        *,
        projection=0.25,
        truncate=False,
        peak_only=False,
        bootids110=False,
        of_date=False,
        shift_deg=0.0,
        shadow=True,
    ):
        key = (step, projection, truncate, peak_only, bootids110, of_date, shift_deg, shadow)
        if key in grid_cache:
            return summarize_grid(grid_cache[key])
        x = np.unique(
            np.concatenate(
                [
                    np.linspace(
                        ts[i] - ts[0], ts[i + 1] - ts[0], int(math.ceil((ts[i + 1] - ts[i]) / step)) + 1
                    )
                    for i in range(len(ts) - 1)
                ]
            )
        )
        p = spline(x)
        v = spline(x, 1)
        h = np.interp(x, ts - ts[0], alt)
        rnorm = np.linalg.norm(p, axis=1)
        if np.any(np.linalg.norm(v, axis=1) > 12) or np.any(np.abs(rnorm - (EARTH_KM + h)) > 35):
            raise ValueError(
                "Interpolated states inconsistent with LEO: possible orbit segment discontinuity"
            )
        unix = ts[0] + x
        lam = solar_longitude_deg(unix, of_date=of_date) + shift_deg
        q = profiles(lam, truncate=truncate, peak_only=peak_only, bootids110=bootids110)
        # Fixed climatological reference cycle: splitting an EVA at New Year must
        # not change the sporadic baseline. Solar coordinates are deterministic,
        # not observations from the future, including in historical forecast mode.
        mean, audit = annual_profiles(2024, truncate, peak_only, bootids110, of_date, shift_deg)
        removed = float(np.sum(peaks * mean * toa_to_infty * projection))
        if removed >= grun:
            raise ValueError("Annual stream subtraction exceeds Grun: inconsistent mass/normalization")
        j6 = np.array([math.prod(factors_table_j6(float(hh))) for hh in h])
        bg = (grun - removed) * j6
        parts = []
        visible_times = []
        rotation = None
        if of_date:
            from skyfield.api import load
            from skyfield.framelib import ICRS_to_J2000

            tt = load.timescale(builtin=True).from_datetimes(
                [datetime.fromtimestamp(t, timezone.utc) for t in unix]
            )
            rotation = np.einsum("ij,jkn->ikn", ICRS_to_J2000, tt.M.transpose(1, 0, 2))
        for i, row in enumerate(rows):
            d = (lam - row["lambda_max_deg"] + 180) % 360 - 180
            # ECSS linear drift extrapolated with the profile tails; the 1%
            # tail-cut hypothesis measures sensitivity to far-tail extrapolation.
            drift = d
            ra = np.deg2rad(row["ra_max_deg"] + row["delta_ra_deg_per_deg"] * drift)
            dec = np.deg2rad(row["dec_max_deg"] + row["delta_dec_deg_per_deg"] * drift)
            u = np.column_stack((np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)))
            if rotation is not None:
                u = np.einsum("ijn,nj->ni", rotation, u)
            speed = local_speed(entries[i], rnorm)
            moving = np.linalg.norm(-speed[:, None] * u - v, axis=1) / speed
            vis = visible_rays(p, u) if shadow else np.ones(len(x), dtype=bool)
            flux = peaks[i] * q[i] * (speed / entries[i]) ** 2 * moving * projection * vis
            parts.append(area_m2 * cumulative_trapezoid(flux, x=x, initial=0))
            visible_times.append(cumulative_trapezoid(vis.astype(float), x=x, initial=0))
        grid_cache[key] = (
            x,
            np.asarray(parts),
            np.asarray(visible_times),
            area_m2 * cumulative_trapezoid(bg, x=x, initial=0),
            area_m2 * cumulative_trapezoid(grun * j6, x=x, initial=0),
            audit,
            removed,
        )
        return summarize_grid(grid_cache[key])

    def evaluate(start_utc, end_utc):
        nonlocal left_s, right_s
        if start_utc.tzinfo is None or end_utc.tzinfo is None:
            raise ValueError("Window needs timezone-aware UTC bounds")
        left_s, right_s = start_utc.timestamp() - ts[0], end_utc.timestamp() - ts[0]
        if not 0 <= left_s < right_s <= ts[-1] - ts[0]:
            raise ValueError("Seasonal window outside the prepared orbital coverage")
        if start_utc.timestamp() not in by_time or end_utc.timestamp() not in by_time:
            raise ValueError("Window boundary lacks an inertial state")
        base = calculate(integration_step_s)
        fine = calculate(integration_step_s / 2)
        scenarios = {"base": base[0], "half_step": fine[0]}
        variants = {
            "catalogue_already_plate": {"projection": 1.0},
            "bootids_110": {"bootids110": True},
            "peak_only_normalization": {"peak_only": True},
            "tails_cut_1pct": {"truncate": True},
            "solar_minus_0_02deg": {"shift_deg": -0.02},
            "solar_plus_0_02deg": {"shift_deg": 0.02},
            "catalogue_of_date": {"of_date": True},
            "no_earth_shadow": {"shadow": False},
        }
        invalid = {}
        for name, kw in variants.items():
            try:
                scenarios[name] = calculate(integration_step_s, **kw)[0]
            except ValueError as e:
                invalid[name] = str(e)
        contributions = tuple(
            {"name": row["name"], "expected_hits": n, "unblocked_duration_s": vis}
            for row, n, vis in sorted(zip(rows, base[3], base[4]), key=lambda x: x[1], reverse=True)
        )
        return SeasonalResult(
            base[0],
            base[7],
            base[1],
            base[2],
            mass_g,
            area_m2,
            1.0,
            "engineering_approximation",
            True,
            contributions,
            {
                "hypotheses_N": scenarios,
                "invalid_hypotheses": invalid,
                "min_N": min(scenarios.values()),
                "max_N": max(scenarios.values()),
                "half_step_relative_change": abs(fine[0] - base[0]) / base[0],
                "interpretation": "alternative hypotheses, not a confidence interval",
            },
            {
                "model_id": MODEL_ID,
                "catalogue_sha256": CATALOGUE_SHA256,
                "frame": "EME2000; C-2 radiant epoch assumed J2000",
                "inertial_states_sha256": states.get("content_sha256"),
                "orbit_method": states.get("method"),
                "orbit_source_hashes": states.get("source_hashes", {}),
                "mass_threshold_kg": mass_g / 1000.0,
                "flux_unit": "m^-2 s^-1",
                "result_unit": "expected count",
                "source_records": [CATALOGUE_ID, METHOD_ID],
                "solar_method": "USNO approximate apparent longitude minus general precession to J2000; 2000–2050",
                "solar_control_max_error_deg": 0.004,
                "annual_cycle": base[5],
                "annual_stream_plate_infty_per_m2_s": base[6],
                "integration_step_s": integration_step_s,
                "check_step_s": integration_step_s / 2,
                "geometry": "one-sided randomly oriented plate; k assumed perpendicular TOA flux; /4 once",
                "profile": "(Zp*qp+Zb*qb)/(Zp+Zb), all 49 tails retained",
                "radiant_drift_policy": "linear drift extrapolated with all tails; 1% tail-cut sensitivity reported",
                "is_reconstruction": states.get("is_reconstruction", True),
            },
        )

    return evaluate if _prepare else evaluate(times_utc[0], times_utc[-1])


def prepare_seasonal_track(times_utc, alts_km, states, **kwargs):
    """Return an evaluator for exact-node subwindows; reuse immutable orbit physics.

    The callable is owned by one computation, not a global mutable UI cache.
    Grid gaps or inconsistent states reject preparation; callers may evaluate
    separate contiguous windows instead, preserving local coverage failures.
    """
    return seasonal_hits_track(times_utc, alts_km, states, _prepare=True, **kwargs)


def comparison_sensitivity(results, equal_pct):
    """Compare windows under paired assumptions, not independent error extremes.

    Identical catalogue assumptions apply to every window. A change in pairwise
    ordering OR distinguishability prevents an unqualified preferred window.
    This checks model hypotheses, not a statistical confidence level.
    """
    if not math.isfinite(equal_pct) or equal_pct < 0:
        raise ValueError("Invalid meteor comparison tolerance")
    series = [r.get("sensitivity", {}).get("hypotheses_N", {}) for r in results]
    invalid = any(r.get("sensitivity", {}).get("invalid_hypotheses") for r in results)
    complete = (
        len(series) >= 2
        and all(s and "base" in s for s in series)
        and all(set(s) == set(series[0]) for s in series)
    )
    signatures = {}
    if complete and not invalid:
        for key in sorted(series[0]):
            values = [s[key] for s in series]
            if any(not math.isfinite(v) or v < 0 for v in values):
                complete = False
                break
            signs = []
            for i, v in enumerate(values):
                for other in values[i + 1 :]:
                    equal = abs(v - other) <= equal_pct / 100 * max(min(v, other), 1e-30)
                    signs.append(0 if equal else (-1 if v < other else 1))
            signatures[key] = tuple(signs)
    stable = complete and not invalid and len(set(signatures.values())) == 1
    return {
        "stable": stable,
        "pairwise_orderings": signatures,
        "note_ru": (
            "порядок и различимость окон сохраняются при всех проверенных гипотезах; "
            "это не физическая валидация потока"
            if stable
            else "порядок или различимость окон не подтверждены при альтернативных гипотезах; "
            "однозначный выбор по сезонной модели не обоснован"
        ),
    }
