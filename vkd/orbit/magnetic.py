"""IGRF |B| and explicitly approximate, internally consistent dipole coordinates."""
from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache
import hashlib
from pathlib import Path

import numpy as np
import ppigrf

from .oem import OrbitDataError

# IGRF reference radius, IAGA model convention; c is exact in SI.
IGRF_RADIUS_KM = 6371.2
LIGHT_SPEED_M_S = 299792458.0


@lru_cache(maxsize=4)
def _coefficients(path: str, content_sha256: str):
    return ppigrf.ppigrf.read_shc(path)


def magnetic_coordinates(ecef_km: np.ndarray, lon_deg: np.ndarray, lat_deg: np.ndarray,
                         alt_km: np.ndarray, times: list[datetime], coeff_path: Path) -> dict:
    """IGRF vectors vary at each point/time; no mixed IGRF/dipole B/B0.

    L, B/B0 and vertical Størmer cutoff all use degree-one IGRF only.
    They are approximations, not traced McIlwain coordinates or storm-time cutoff.
    Full degree-13 IGRF magnitude is returned separately for the field/SAA proxy.
    """
    g, h = _coefficients(str(coeff_path), hashlib.sha256(coeff_path.read_bytes()).hexdigest())
    # SHC epochs are naive UTC, so do not interpret timestamp() in local timezone.
    epochs_s = np.array([(d - datetime(1970, 1, 1)).total_seconds() for d in g.index.to_pydatetime()])
    times_s = np.array([t.timestamp() for t in times])
    if times_s.min() < epochs_s[0] or times_s.max() > epochs_s[-1]:
        raise OrbitDataError('Date outside magnetic coefficient coverage; no extrapolation')
    if ecef_km.shape != (len(times), 3) or not np.isfinite(ecef_km).all():
        raise OrbitDataError('Invalid geocentric position array')
    # Within each coefficient epoch interval, harmonic components are linear in
    # time at fixed coordinates. Two vector evaluations avoid an N-by-N array.
    bins = np.searchsorted(epochs_s, times_s, side='right') - 1
    bins[bins == len(epochs_s) - 1] = len(epochs_s) - 2
    components = np.empty((len(times), 3))
    for b in np.unique(bins):
        mask = bins == b
        lo, hi = times_s[mask].min(), times_s[mask].max()
        dates = [datetime(1970, 1, 1) + timedelta(seconds=float(s)) for s in (lo, hi)]
        be, bn, bu = ppigrf.igrf(lon_deg[mask], lat_deg[mask], alt_km[mask], dates,
                               coeff_fn=str(coeff_path))
        weight = np.zeros(mask.sum()) if hi == lo else (times_s[mask] - lo) / (hi - lo)
        for j, values in enumerate((be, bn, bu)):
            components[mask, j] = values[0] + weight * (values[1] - values[0])
    g10 = np.interp(times_s, epochs_s, g[(1, 0)].to_numpy())
    g11 = np.interp(times_s, epochs_s, g[(1, 1)].to_numpy())
    h11 = np.interp(times_s, epochs_s, h[(1, 1)].to_numpy())
    moment = np.column_stack((g11, h11, g10))
    equatorial_nT = np.linalg.norm(moment, axis=1)
    axis = -moment / equatorial_nT[:, None]
    radius_km = np.linalg.norm(ecef_km, axis=1)
    unit = ecef_km / radius_km[:, None]
    sine = np.einsum('ij,ij->i', unit, axis)
    cos_squared = 1 - sine ** 2
    valid = (cos_squared > 1e-12) & np.isfinite(cos_squared)
    shell_L = np.full(len(times), np.nan)
    ratio = np.full(len(times), np.nan)
    cutoff_GV = np.full(len(times), np.nan)
    shell_L[valid] = radius_km[valid] / IGRF_RADIUS_KM / cos_squared[valid]
    # Consistent dipole B/B_equator(L), analytically >= 1, no clamp.
    ratio[valid] = np.sqrt(1 + 3 * sine[valid] ** 2) / cos_squared[valid] ** 3
    # Vertical Størmer Rc = c * B_equator * R / (4 L²), converted T,m -> GV.
    cutoff_GV[valid] = (LIGHT_SPEED_M_S * equatorial_nT[valid] * 1e-9
                       * IGRF_RADIUS_KM * 1000 / 4 / 1e9 / shell_L[valid] ** 2)
    return {'B_nT': np.linalg.norm(components, axis=1), 'L': shell_L,
            'B_over_B0': ratio, 'cutoff_GV': cutoff_GV, 'valid': valid}
