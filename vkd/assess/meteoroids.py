# -*- coding: utf-8 -*-
"""Природные метеороиды по ECSS-E-ST-10-04C Rev.1 (15.06.2020): ожидаемое число
попаданий на одностороннюю случайно кувыркающуюся пластину (B2 по спецификации A5).

Источник формул (номера страниц — печатные, совпадают с PDF в docs/istochniki/):
  * поток Grün F_met,0(m) — п. 10.2.2.2a, формула (10-1), с. 76–77; RD.22 Grün et al. 1985;
  * поправки низкой орбиты — Annex C.1.5, формула (C-25) F = F0·G·s_f·K, с. 112;
    множители для круговых орбит Земли — Table J-6, с. 195;
  * число попаданий — п. 10.2.5a, формула (10-2) N = F·A·T, с. 80; Пуассон (10-3);
  * неопределённость потока — фактор 0,33…3, J.2.3.2, с. 185.

Контроль: 400 км, 1 м², 6 ч, m ≥ 1e-3 г → N = 5,609728·10⁻⁷ — совпадает с
контрольным числом Codex (journal/friend.md) при множителях Table J-6 и годе
365,25 сут (константа 3,15576·10⁷ в формуле (10-1)).

Чего НЕ даёт (CONTRACT v3.1, R7 и раздел 9): вероятность повреждения скафандра,
попадание в космонавта, техногенный мусор, метеорные потоки конкретной даты
(10.2.2.2c требует их для миссий короче 3 недель — не реализовано, объявляется),
зависимость от наклонения (модель изотропна).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

SEC_PER_YEAR = 3.15576e7          # 365,25 сут — константа формулы (10-1)
HOURS_PER_YEAR = 8766.0
M_TABLE_MIN_G, M_TABLE_MAX_G = 1e-12, 5e2     # диапазон Table J-5, с. 194

# Table J-6, с. 195: высота, км → (Ḡ, s_f, K) для круговых орбит Земли
TABLE_J6 = [
    (100.0, 2.04, 0.50, 1.09), (200.0, 2.03, 0.58, 1.09), (400.0, 2.00, 0.63, 1.09),
    (800.0, 1.94, 0.70, 1.08), (1000.0, 1.92, 0.72, 1.08), (2000.0, 1.81, 0.79, 1.08),
    (4000.0, 1.65, 0.87, 1.07), (10000.0, 1.41, 0.95, 1.05), (20000.0, 1.26, 0.98, 1.04),
    (35790.0, 1.16, 0.99, 1.03), (100000.0, 1.06, 1.00, 1.01),
]


@dataclass(frozen=True)
class MeteoroidResult:
    N: float                       # ожидаемое число попаданий, безразмерно
    F0_per_m2_yr: float            # поток Grün на 1 а.е., 1/(м²·год)
    F_orbit_per_m2_yr: float       # с поправками (C-25)
    G: float
    s_f: float
    K: float
    area_m2: float
    duration_h: float
    m_min_g: float
    p_at_least_one: float          # 1 − exp(−N)
    factor_uncertainty: str        # «×0,33…3, J.2.3.2»
    streams_included: bool         # False — объявляется
    rule: str


def grun_flux_1au(m_g: float) -> float:
    """Формула (10-1): F_met,0(m) = 3,15576e7·(F1+F2+F3), 1/(м²·год), пластина 1 а.е."""
    if not (M_TABLE_MIN_G <= m_g <= M_TABLE_MAX_G):
        raise ValueError('масса %g г вне диапазона Table J-5 ECSS (%g…%g г)' % (m_g, M_TABLE_MIN_G, M_TABLE_MAX_G))
    m = m_g
    F1 = (2.2e3 * m ** 0.306 + 15.0) ** (-4.38)
    F2 = 1.3e-9 * (m + 1e11 * m ** 2 + 1e27 * m ** 4) ** (-0.36)
    F3 = 1.3e-16 * (m + 1e6 * m ** 2) ** (-0.85)
    return SEC_PER_YEAR * (F1 + F2 + F3)


def factors_table_j6(alt_km: float) -> tuple[float, float, float]:
    """Линейная интерполяция Table J-6 по высоте; вне 100…100000 км — отказ."""
    if alt_km < TABLE_J6[0][0] or alt_km > TABLE_J6[-1][0]:
        raise ValueError('высота %g км вне Table J-6 ECSS (100…100000 км)' % alt_km)
    for (h0, g0, s0, k0), (h1, g1, s1, k1) in zip(TABLE_J6, TABLE_J6[1:]):
        if h0 <= alt_km <= h1:
            w = 0.0 if h1 == h0 else (alt_km - h0) / (h1 - h0)
            return g0 + w * (g1 - g0), s0 + w * (s1 - s0), k0 + w * (k1 - k0)
    return TABLE_J6[-1][1:]


def meteoroid_hits(alt_km: float, area_m2: float, duration_h: float, m_min_g: float = 1e-3) -> MeteoroidResult:
    F0 = grun_flux_1au(m_min_g)
    G, s_f, K = factors_table_j6(alt_km)
    F = F0 * G * s_f * K
    N = F * area_m2 * duration_h / HOURS_PER_YEAR
    return MeteoroidResult(
        N=N, F0_per_m2_yr=F0, F_orbit_per_m2_yr=F, G=G, s_f=s_f, K=K, area_m2=area_m2, duration_h=duration_h,
        m_min_g=m_min_g, p_at_least_one=1.0 - math.exp(-N), factor_uncertainty='×0,33…3 (ECSS J.2.3.2)',
        streams_included=False,
        rule='ECSS-E-ST-10-04C Rev.1: Grün (10-1) × Table J-6 (G=%.2f, s_f=%.2f, K=%.2f) × A × T; '
             'односторонняя случайно кувыркающаяся пластина; метеорные потоки даты не включены (10.2.2.2c); '
             'техногенный мусор не включён' % (G, s_f, K),
    )
