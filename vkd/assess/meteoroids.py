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
попадание в космонавта, техногенный мусор, вклад метеорных потоков конкретной даты
в N (10.2.2.2c требует его для миссий короче 3 недель — не реализовано, объявляется;
календарь главных потоков ниже даёт только ПРИЗНАК активности), зависимость от
наклонения (модель изотропна).

Следствие для сравнения окон: модель различает окна только по высоте и длительности;
при равной длительности на орбите МКС различие N между окнами < 0,01 %, поэтому роль
линии — абсолютная оценка и заявление об охвате, а не выбор окна.
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


# Главные метеорные потоки — справочный календарь (IMO Meteor Shower Calendar, Rendtel, ежегодные
# выпуски; даты активности и пиковый ZHR округлены, из года в год смещаются на ±1 сут).
# (название, (мес, день) начала, (мес, день) конца, (мес, день) пика, ZHR в пике)
# ZHR — визуальная зенитная часовая частота, НЕ поток на пластину: в N не пересчитывается.
MAJOR_SHOWERS = (
    ('Квадрантиды', (12, 28), (1, 12), (1, 3), 80),
    ('Лириды', (4, 14), (4, 30), (4, 22), 18),
    ('эта-Аквариды', (4, 19), (5, 28), (5, 6), 50),
    ('Южные дельта-Аквариды', (7, 12), (8, 23), (7, 30), 25),
    ('Персеиды', (7, 17), (8, 24), (8, 12), 100),
    ('Дракониды', (10, 6), (10, 10), (10, 8), 10),
    ('Ориониды', (10, 2), (11, 7), (10, 21), 20),
    ('Южные Тауриды', (9, 10), (11, 20), (10, 10), 5),
    ('Северные Тауриды', (10, 20), (12, 10), (11, 12), 5),
    ('Леониды', (11, 6), (11, 30), (11, 17), 10),
    ('Геминиды', (12, 4), (12, 20), (12, 14), 150),
    ('Урсиды', (12, 17), (12, 26), (12, 22), 10),
)


def _doy(month: int, day: int) -> int:
    return (month - 1) * 31 + day          # монотонный порядок внутри года; точные сутки не нужны


def active_showers(when) -> list[dict]:
    """Главные потоки, активные на дату (по календарю IMO). Признак, не поток: вклад в N не считается."""
    d = _doy(when.month, when.day)
    out = []
    for name, (m0, d0), (m1, d1), (mp, dp), zhr in MAJOR_SHOWERS:
        a, b = _doy(m0, d0), _doy(m1, d1)
        active = (a <= d <= b) if a <= b else (d >= a or d <= b)      # потоки через Новый год
        if active:
            out.append({'name': name, 'peak': '%02d.%02d' % (dp, mp), 'zhr_peak': zhr,
                        'active': '%02d.%02d—%02d.%02d' % (d0, m0, d1, m1)})
    return out


SHOWERS_SOURCE_RU = 'календарь главных метеорных потоков IMO (даты и пиковый ZHR округлены); признак активности, не поток'


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


def meteoroid_hits_track(times_utc, alts_km, area_m2: float = 1.0, m_min_g: float = 1e-3) -> MeteoroidResult:
    """Спецификация A5 (docs/methods/METEOROIDS_GRUN_SPEC.md, §2): поток F_LEO по
    ФАКТИЧЕСКОЙ высоте каждой точки трассы (Table J-6 линейно между узлами), интеграл
    трапециями по фактическим интервалам dt, начало и конец окна включены;
    N = A·∫F dt / 31 557 600. Число точек само по себе не даёт лишней минуты."""
    if len(times_utc) < 2 or len(times_utc) != len(alts_km):
        raise ValueError('нужны минимум две точки трассы с высотами')
    t = [(x - times_utc[0]).total_seconds() for x in times_utc]
    if any(b <= a for a, b in zip(t, t[1:])):
        raise ValueError('времена трассы должны строго возрастать')
    F0 = grun_flux_1au(m_min_g)
    fac = [factors_table_j6(h) for h in alts_km]
    F = [F0 * g * s * k for g, s, k in fac]
    integral = sum(0.5 * (F[i] + F[i + 1]) * (t[i + 1] - t[i]) for i in range(len(t) - 1))   # (1/(м²·год))·с
    N = area_m2 * integral / SEC_PER_YEAR
    duration_h = t[-1] / 3600.0
    G, s_f, K = (sum(f[i] for f in fac) / len(fac) for i in range(3))
    h_lo, h_hi = min(alts_km), max(alts_km)
    return MeteoroidResult(
        N=N, F0_per_m2_yr=F0, F_orbit_per_m2_yr=N * SEC_PER_YEAR / (area_m2 * t[-1]) if t[-1] > 0 else 0.0,
        G=G, s_f=s_f, K=K, area_m2=area_m2, duration_h=duration_h, m_min_g=m_min_g,
        p_at_least_one=1.0 - math.exp(-N), factor_uncertainty='×0,33…3 (ECSS J.2.3.2)', streams_included=False,
        rule='ECSS-E-ST-10-04C Rev.1: Grün (10-1) × Table J-6 по высоте трассы %.0f…%.0f км (средние G=%.2f, s_f=%.2f, '
             'K=%.2f), интеграл по фактическим dt, концы окна включены; односторонняя случайно кувыркающаяся пластина '
             '%g м²; метеорные потоки даты не включены (10.2.2.2c); техногенный мусор не включён'
             % (h_lo, h_hi, G, s_f, K, area_m2),
    )


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
