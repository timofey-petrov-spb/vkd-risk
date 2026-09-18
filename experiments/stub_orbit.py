# -*- coding: utf-8 -*-
"""ВРЕМЕННАЯ заглушка орбиты для каркаса приложения, пока не подключён vkd/orbit (A3).

Область Б по договору — experiments/. Здесь НЕ производственный расчёт:
дипольная L, нет обрезания, IGRF-14 без пометки эпохи. Каждая точка помечена
mag_status="approximation". Заменяется импортом vkd.orbit, когда он появится.
Начало расчёта задаётся явно (воспроизводимость), не из текущего времени.
"""
from __future__ import annotations

import io
import math
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import ppigrf
from skyfield.api import EarthSatellite, load, wgs84

from vkd.types import MagMethod, TrajectoryMeta, TrajectoryPoint

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R_E_km = 6371.2


def _dipole_axis(path, epoch=2025.0):
    g10 = g11 = h11 = None
    hdr = None
    for line in io.open(path, encoding='utf-8'):
        if line.startswith('g/h'):
            hdr = line.split(); continue
        if hdr and line[:2] in ('g ', 'h '):
            p = line.split(); col = hdr.index('%.1f' % epoch)
            key = (p[0], int(p[1]), int(p[2])); v = float(p[col])
            if key == ('g', 1, 0): g10 = v
            if key == ('g', 1, 1): g11 = v
            if key == ('h', 1, 1): h11 = v
    B_eq = math.sqrt(g10 * g10 + g11 * g11 + h11 * h11)
    return -np.array([g11, h11, g10]) / B_eq, B_eq


def trajectory(start_utc: datetime, minutes: int, saa_B_threshold_nT: float,
               tle_path: str | None = None):
    tle_path = tle_path or os.path.join(_ROOT, 'data', 'spaceweather', 'iss.tle')
    lines = io.open(tle_path, encoding='utf-8').read().strip().split('\n')
    ts = load.timescale()
    sat = EarthSatellite(lines[1], lines[2], lines[0].strip(), ts)
    times = [start_utc + timedelta(minutes=i) for i in range(minutes)]
    sub = wgs84.geographic_position_of(sat.at(ts.from_datetimes(times)))
    lat = np.atleast_1d(sub.latitude.degrees); lon = np.atleast_1d(sub.longitude.degrees); alt = np.atleast_1d(sub.elevation.km)
    Be, Bn, Bu = ppigrf.igrf(lon, lat, alt, start_utc.replace(tzinfo=None))
    B = np.sqrt(Be ** 2 + Bn ** 2 + Bu ** 2).ravel()
    axis, B_eq = _dipole_axis(os.path.join(_ROOT, 'data', 'spaceweather', 'igrf14coeffs.txt'))
    pts = []
    for i, t in enumerate(times):
        la, lo = math.radians(lat[i]), math.radians(lon[i])
        u = np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])
        s = float(np.dot(u, axis)); cos2 = max(1.0 - s * s, 1e-6)
        L = (R_E_km + alt[i]) / R_E_km / cos2
        B0 = B_eq / L ** 3
        bb = float(B[i] / B0)                     # НЕ клампится: <1 помечается в assess
        pts.append(TrajectoryPoint(t, float(lat[i]), float(lon[i]), float(alt[i]), float(B[i]), float(L), bb,
                                   None, MagMethod.DIPOLE, 'approximation' if bb >= 1.0 else 'inconsistent_BB0',
                                   bool(B[i] < saa_B_threshold_nT)))
    meta = TrajectoryMeta('celestrak_gp', 'sgp4', 'TEME→WGS84', sat.epoch.utc_datetime(),
                          times[0], times[-1], None, None, datetime.now(timezone.utc), False, 'IGRF-14 (ppigrf)')
    return meta, pts
