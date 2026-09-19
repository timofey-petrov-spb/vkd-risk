# -*- coding: utf-8 -*-
"""Магнитные координаты для входа в таблицы ОСТ 134-1044-2007 (прил. А): эксцентричный диполь.

Зачем отдельно от A3. Модуль орбиты отдаёт L и B/B0 центрального наклонённого
диполя. В ядре Южно-Атлантической аномалии он даёт L ≈ 1,07 — ниже первой строки
сетки ОСТ (1,14), а вне ядра B/B0 быстро уходит за точку отражения: проверка 19.09
на 6 часах трассы — ни одной точки с ненулевым потоком, флюенс тождественно нуль.
Причина физическая: слабое поле над Южной Атлантикой — это смещение центра
земного диполя примерно на 0,08 R_E в сторону западной части Тихого океана.
Эксцентричный диполь (Fraser-Smith, Rev. Geophys. 1987, по квадрупольным членам
IGRF) это смещение воспроизводит; L и экваториальное поле B0 считаются от
смещённого центра, а B берётся полное IGRF из точки A3. Отношение B/B0 < 1
помечается статусом inconsistent_BB0, не обрезается. Это ОБЪЯВЛЕННОЕ приближение
до трассировки силовых линий (будущая работа A3); статус точек — approximation.
Коэффициенты — из тех же файлов IGRF, что использует A3 (data/orbit/*.shc).
"""
from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime
from functools import lru_cache

import numpy as np
import ppigrf
from skyfield.api import wgs84

from vkd.types import MagMethod, TrajectoryPoint

R_E_KM = 6371.2                      # опорный радиус IGRF
_EPOCH0 = datetime(1970, 1, 1)


@lru_cache(maxsize=4)
def _shc(path: str):
    return ppigrf.ppigrf.read_shc(path)


def eccentric_dipole(coeff_path: str, when: datetime) -> tuple[np.ndarray, float, np.ndarray]:
    """Ось диполя (единичный вектор к северному геомагнитному полюсу... в ECEF), экваториальное
    поле B_eq (нТл) и смещение центра диполя (в R_E) на момент when; коэффициенты
    интерполируются линейно между эпохами файла, вне таблицы — отказ."""
    g, h = _shc(str(coeff_path))
    epochs = np.array([(d - _EPOCH0).total_seconds() for d in g.index.to_pydatetime()])
    t = (when.replace(tzinfo=None) - _EPOCH0).total_seconds()
    if t < epochs[0] or t > epochs[-1]:
        raise ValueError('дата %s вне таблицы коэффициентов %s' % (when.date(), coeff_path))
    c = lambda df, n, m: float(np.interp(t, epochs, df[(n, m)].to_numpy()))
    g10, g11, h11 = c(g, 1, 0), c(g, 1, 1), c(h, 1, 1)
    g20, g21, h21, g22, h22 = c(g, 2, 0), c(g, 2, 1), c(h, 2, 1), c(g, 2, 2), c(h, 2, 2)
    B0sq = g10 * g10 + g11 * g11 + h11 * h11
    s3 = math.sqrt(3.0)
    L0 = 2 * g10 * g20 + s3 * (g11 * g21 + h11 * h21)
    L1 = -g11 * g20 + s3 * (g10 * g21 + g11 * g22 + h11 * h22)
    L2 = -h11 * g20 + s3 * (g10 * h21 - h11 * g22 + g11 * h22)
    E = (L0 * g10 + L1 * g11 + L2 * h11) / (4 * B0sq)
    offset_re = np.array([(L1 - g11 * E) / (3 * B0sq), (L2 - h11 * E) / (3 * B0sq), (L0 - g10 * E) / (3 * B0sq)])
    B_eq = math.sqrt(B0sq)
    axis = -np.array([g11, h11, g10]) / B_eq
    return axis, B_eq, offset_re


def belt_coordinates(points: list[TrajectoryPoint], coeff_path: str) -> tuple[list[TrajectoryPoint], dict]:
    """Копии точек с L и B/B0 эксцентричного диполя для таблиц ОСТ; |B|, широта, высота,
    признак аномалии — без изменений (из A3). Возвращает также сводку для происхождения."""
    if not points:
        return [], {'method': 'eccentric_dipole', 'n': 0}
    when = points[len(points) // 2].t_utc
    axis, B_eq, off = eccentric_dipole(coeff_path, when)
    out, n_incons, n_nomodel = [], 0, 0
    for p in points:
        # A3 supplies geodetic WGS84 latitude and ellipsoidal height.
        pos = wgs84.latlon(p.lat_deg, p.lon_deg, elevation_m=p.alt_km*1000).itrs_xyz.km / R_E_KM - off
        rr = float(np.linalg.norm(pos))
        s = float(np.dot(pos / rr, axis))
        cos2 = 1.0 - s * s
        if cos2 <= 1e-9 or p.B_nT is None:
            n_nomodel += 1
            out.append(replace(p, L=None, B_over_B0=None, mag_method=MagMethod.NONE, mag_status='outside_model'))
            continue
        L = rr / cos2
        B0 = B_eq / L ** 3
        ratio = p.B_nT / B0
        status = 'approximation' if ratio >= 1.0 else 'inconsistent_BB0'
        n_incons += status == 'inconsistent_BB0'
        out.append(replace(p, L=L, B_over_B0=ratio, mag_method=MagMethod.DIPOLE, mag_status=status))
    return out, {'method': 'eccentric_dipole', 'coefficients': str(coeff_path), 'epoch_utc': when.isoformat(), 'position_conversion': 'WGS84 geodetic to ECEF',
                 'offset_km': [float(x) * R_E_KM for x in off], 'B_eq_nT': B_eq, 'n': len(points),
                 'n_inconsistent_BB0': n_incons, 'n_outside_model': n_nomodel,
                 'note': 'L и B0 от смещённого центра диполя (Fraser-Smith 1987), B — полный IGRF точки (A3); '
                         'приближение до трассировки силовых линий; B/B0 < 1 помечается, не обрезается'}
