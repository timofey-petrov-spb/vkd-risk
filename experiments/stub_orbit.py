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


def _igrf_coeffs(path, epoch=2025.0):
    c, hdr = {}, None
    for line in io.open(path, encoding='utf-8'):
        if line.startswith('g/h'):
            hdr = line.split(); continue
        if hdr and line[:2] in ('g ', 'h '):
            p = line.split(); col = hdr.index('%.1f' % epoch)
            c[(p[0], int(p[1]), int(p[2]))] = float(p[col])
    return c


def _eccentric_dipole(path, epoch=2025.0):
    """Ось диполя, экваториальное поле и СМЕЩЕНИЕ центра диполя (эксцентричный
    диполь по квадрупольным членам IGRF; Fraser-Smith 1987). Смещение ≈ 0,08 R_E
    в сторону западной части Тихого океана — именно оно делает поле над
    Южной Атлантикой слабым; центрированный диполь занижал L в ядре аномалии
    ниже сетки таблицы ОСТ (1,14) на ~21 % точек трассы."""
    c = _igrf_coeffs(path, epoch)
    g10, g11, h11 = c[('g', 1, 0)], c[('g', 1, 1)], c[('h', 1, 1)]
    g20, g21, h21, g22, h22 = c[('g', 2, 0)], c[('g', 2, 1)], c[('h', 2, 1)], c[('g', 2, 2)], c[('h', 2, 2)]
    B0sq = g10 * g10 + g11 * g11 + h11 * h11
    s3 = math.sqrt(3.0)
    L0 = 2 * g10 * g20 + s3 * (g11 * g21 + h11 * h21)
    L1 = -g11 * g20 + s3 * (g10 * g21 + g11 * g22 + h11 * h22)
    L2 = -h11 * g20 + s3 * (g10 * h21 - h11 * g22 + g11 * h22)
    E = (L0 * g10 + L1 * g11 + L2 * h11) / (4 * B0sq)
    offset_re = np.array([(L1 - g11 * E) / (3 * B0sq), (L2 - h11 * E) / (3 * B0sq), (L0 - g10 * E) / (3 * B0sq)])
    B_eq = math.sqrt(B0sq)
    return -np.array([g11, h11, g10]) / B_eq, B_eq, offset_re


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
    axis, B_eq, off = _eccentric_dipole(os.path.join(_ROOT, 'data', 'spaceweather', 'igrf14coeffs.txt'))
    pts = []
    for i, t in enumerate(times):
        la, lo = math.radians(lat[i]), math.radians(lon[i])
        r_re = (R_E_km + alt[i]) / R_E_km
        pos = r_re * np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)]) - off
        rr = float(np.linalg.norm(pos))
        s = float(np.dot(pos / rr, axis)); cos2 = max(1.0 - s * s, 1e-6)
        L = rr / cos2                              # эксцентричный диполь: r' и λ' от смещённого центра
        B0 = B_eq / L ** 3
        bb = float(B[i] / B0)                      # НЕ клампится: <1 помечается в assess
        pts.append(TrajectoryPoint(t, float(lat[i]), float(lon[i]), float(alt[i]), float(B[i]), float(L), bb,
                                   None, MagMethod.DIPOLE, 'approximation' if bb >= 1.0 else 'inconsistent_BB0',
                                   bool(B[i] < saa_B_threshold_nT)))
    meta = TrajectoryMeta('celestrak_gp', 'sgp4', 'TEME→WGS84', sat.epoch.utc_datetime(),
                          times[0], times[-1], None, None, datetime.now(timezone.utc), False, 'IGRF-14 (ppigrf)')
    return meta, pts
