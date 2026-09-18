# -*- coding: utf-8 -*-
"""Сквозная цепочка на реальных данных: от TLE до рекомендации по окну.

Демонстрация метода, не производственный код. Показывает ВСЕ звенья:

  TLE → положение по минутам → |B| по IGRF → L и B/B0 → аномалия
      → поток поясов по приложению А ОСТ 1044 → окна → правило → рекомендация

Что здесь упрощено и должно быть заменено в vkd/orbit/ (Codex):
  * L — дипольное приближение, а не трассировка силовой линии;
  * жёсткость обрезания не считается (для демо аномалия важнее);
  * поток берётся по ближайшей L и линейно по B/B0.

Источники:
  TLE            data/spaceweather/iss.tle          CelesTrak, NORAD 25544
  IGRF-14        data/spaceweather/igrf14coeffs.txt IAGA (для оси диполя)
  |B|            ppigrf                              IGRF-13/14
  потоки поясов  data/ost1044_belts/A_2_1.csv       ОСТ 134-1044-2007, прил. А, протоны, мин. СА
  GOES >=10 МэВ  data/spaceweather/goes_protons_3day.json  NOAA SWPC
Шкала S: S1 начинается с 10 pfu (част./(см2·с·ср)) по >=10 МэВ — NOAA.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import ppigrf
from skyfield.api import EarthSatellite, load, wgs84

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = lambda *p: os.path.join(ROOT, 'data', *p)

# --- параметры демонстрации (в продукте — из запроса пользователя) --------
SEARCH_HOURS = 24        # период поиска начала, ч (постановка: до 24)
DURATION_MIN = 360       # длительность ВКД, мин (постановка: 1…8 ч)
STEP_MIN = 1             # шаг траектории, мин
START_STEP_MIN = 30      # шаг перебора начала окна, мин
SAA_B_THRESHOLD_nT = 24000.0   # порог |B| для аномалии на ~420 км; инженерный, в чувствительность
R_E_km = 6371.2          # радиус в IGRF
S1_THRESHOLD_pfu = 10.0  # NOAA: S1 при >=10 pfu по >=10 МэВ
EQUIV_TOL_MIN = 5.0      # окна с разницей меньше этого — равнозначны


def dipole_axis_from_igrf(path: str, epoch: float = 2025.0):
    """g10, g11, h11 на эпоху → единичный вектор оси диполя и B_eq."""
    g10 = g11 = h11 = None
    hdr = None
    for line in io.open(path, encoding='utf-8'):
        if line.startswith('g/h'):
            hdr = line.split()
            continue
        if hdr and (line.startswith('g ') or line.startswith('h ')):
            p = line.split()
            col = hdr.index('%.1f' % epoch)
            key = (p[0], int(p[1]), int(p[2]))
            v = float(p[col])
            if key == ('g', 1, 0): g10 = v
            if key == ('g', 1, 1): g11 = v
            if key == ('h', 1, 1): h11 = v
    B_eq_nT = math.sqrt(g10*g10 + g11*g11 + h11*h11)
    # ось диполя (направление на южный геомагнитный полюс = -m)
    m = np.array([g11, h11, g10]) / B_eq_nT
    return -m, B_eq_nT


def geo_to_ecef_unit(lat_deg, lon_deg):
    la, lo = math.radians(lat_deg), math.radians(lon_deg)
    return np.array([math.cos(la)*math.cos(lo), math.cos(la)*math.sin(lo), math.sin(la)])


def dipole_L_and_B0(lat_deg, lon_deg, alt_km, axis, B_eq_nT):
    """Дипольное приближение: L = r/cos²(λ_m), B0(L) = B_eq/L³."""
    r = (R_E_km + alt_km) / R_E_km
    u = geo_to_ecef_unit(lat_deg, lon_deg)
    sin_lm = float(np.dot(u, axis))
    cos2 = max(1.0 - sin_lm*sin_lm, 1e-6)
    L = r / cos2
    B0 = B_eq_nT / L**3
    return L, B0


def load_belt_table(path):
    rows = list(csv.reader(io.open(path, encoding='utf-8')))
    energies = [float(h.replace('E_', '').replace('_MeV', '')) for h in rows[0][2:]]
    data = {}
    for r in rows[1:]:
        L, bb = float(r[0]), float(r[1])
        data.setdefault(L, []).append((bb, [float(x) for x in r[2:]]))
    for L in data:
        data[L].sort()
    return energies, data


def belt_flux(L, B_over_B0, energies, data, e_min_MeV=30.0):
    """Суммарный дифференциальный поток протонов с E >= e_min по ближайшей L,
    линейно по B/B0. Вне табличного B/B0 — 0 (частицы там не удерживаются)."""
    Ls = np.array(sorted(data))
    Ln = float(Ls[np.argmin(np.abs(Ls - L))])
    if abs(Ln - L) > 0.15:                 # далеко от сетки — не экстраполируем
        return 0.0
    cols = [i for i, e in enumerate(energies) if e >= e_min_MeV]
    pts = data[Ln]
    bbs = [p[0] for p in pts]
    vals = [sum(p[1][i] for i in cols) for p in pts]
    if B_over_B0 <= bbs[0]:
        return vals[0]
    if B_over_B0 >= bbs[-1]:
        return 0.0
    return float(np.interp(B_over_B0, bbs, vals))


def latest_goes_p10(path):
    d = json.load(io.open(path, encoding='utf-8'))
    p10 = [x for x in d if x.get('energy') == '>=10 MeV' and x.get('flux') is not None]
    if not p10:
        return None, None
    last = max(p10, key=lambda x: x['time_tag'])
    return float(last['flux']), datetime.fromisoformat(last['time_tag'].replace('Z', '+00:00'))


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    ts = load.timescale()

    # 1. Орбита ------------------------------------------------------------
    lines = io.open(D('spaceweather', 'iss.tle'), encoding='utf-8').read().strip().split('\n')
    sat = EarthSatellite(lines[1], lines[2], lines[0].strip(), ts)
    epoch = sat.epoch.utc_datetime()
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    n_pts = SEARCH_HOURS*60 + DURATION_MIN
    times = [t0 + timedelta(minutes=i*STEP_MIN) for i in range(n_pts)]
    t_sf = ts.from_datetimes(times)
    sub = wgs84.geographic_position_of(sat.at(t_sf))
    lat = np.atleast_1d(sub.latitude.degrees)
    lon = np.atleast_1d(sub.longitude.degrees)
    alt = np.atleast_1d(sub.elevation.km)

    # 2. Магнитное поле и координаты -----------------------------------------
    axis, B_eq = dipole_axis_from_igrf(D('spaceweather', 'igrf14coeffs.txt'))
    Be, Bn, Bu = ppigrf.igrf(lon, lat, alt, t0.replace(tzinfo=None))  # нТл; ppigrf ждёт naive datetime
    Bmag = np.sqrt(Be**2 + Bn**2 + Bu**2).ravel()
    L = np.zeros(n_pts); BB0 = np.zeros(n_pts)
    for i in range(n_pts):
        Li, B0 = dipole_L_and_B0(lat[i], lon[i], alt[i], axis, B_eq)
        L[i] = Li; BB0[i] = max(Bmag[i] / B0, 1.0)
    in_saa = Bmag < SAA_B_THRESHOLD_nT

    # 3. Поток поясов по прил. А ОСТ ------------------------------------------
    energies, table = load_belt_table(D('ost1044_belts', 'A_2_1.csv'))
    flux = np.array([belt_flux(L[i], BB0[i], energies, table) for i in range(n_pts)])

    # 4. Наблюдение GOES -----------------------------------------------------
    p10, p10_t = latest_goes_p10(D('spaceweather', 'goes_protons_3day.json'))

    # 5. Окна ----------------------------------------------------------------
    windows = []
    for k in range(0, SEARCH_HOURS*60, START_STEP_MIN):
        sl = slice(k, k + DURATION_MIN)
        windows.append({
            'start': times[k],
            'saa_min': float(in_saa[sl].sum()) * STEP_MIN,
            'flux_int': float(flux[sl].sum()) * 60.0 * STEP_MIN,   # част./(см2·ср·МэВ)
            'n_crossings': int(np.sum(np.diff(in_saa[sl].astype(int)) == 1)),
        })

    # 6. Правило (CONTRACT.md, раздел 4) --------------------------------------
    if p10 is None:
        verdict, rule = 'insufficient', 'п.1: нет данных GOES >=10 МэВ'
        best = worst = None
    elif p10 >= S1_THRESHOLD_pfu:
        verdict, rule = 'all_need_check', 'п.2: протонное событие идёт (GOES >= S1), все окна требуют проверки'
        best = worst = None
    else:
        ranked = sorted(windows, key=lambda w: (w['saa_min'], w['flux_int']))
        best, worst = ranked[0], ranked[-1]
        if worst['saa_min'] - best['saa_min'] < EQUIV_TOL_MIN:
            verdict, rule = 'equivalent', 'п.4: разброс минут в аномалии меньше допуска %.0f мин' % EQUIV_TOL_MIN
        else:
            verdict, rule = 'preferred', 'п.3: минимум минут в аномалии, затем интеграл потока'

    # 7. Вывод ---------------------------------------------------------------
    age_h = (t0 - epoch).total_seconds() / 3600
    print('ТРАЕКТОРИЯ  источник CelesTrak GP 25544 | эпоха TLE %s | давность %.1f ч | SGP4 | шаг %d мин'
          % (epoch.strftime('%Y-%m-%d %H:%MZ'), age_h, STEP_MIN))
    print('            точек %d | высота %.0f…%.0f км | широта ±%.1f°' % (n_pts, alt.min(), alt.max(), abs(lat).max()))
    print('ПОЛЕ        IGRF через ppigrf | B_eq диполя %.0f нТл | порог аномалии %.0f нТл (инж. оценка)'
          % (B_eq, SAA_B_THRESHOLD_nT))
    print('            |B| на трассе %.0f…%.0f нТл | минут в аномалии за %d ч: %.0f | пролётов: %d'
          % (Bmag.min(), Bmag.max(), SEARCH_HOURS, float(in_saa[:SEARCH_HOURS*60].sum()), int(np.sum(np.diff(in_saa[:SEARCH_HOURS*60].astype(int)) == 1))))
    print('ПОЯСА       ОСТ 1044 прил. А табл. А.2.1, протоны >= 30 МэВ, дипольная L | L на трассе %.2f…%.2f'
          % (L.min(), L.max()))
    if p10 is not None:
        print('GOES        наблюдение >=10 МэВ: %.3g pfu на %s | S1 с %.0f pfu | статус: %s'
              % (p10, p10_t.strftime('%Y-%m-%d %H:%MZ'), S1_THRESHOLD_pfu, 'ниже S1' if p10 < S1_THRESHOLD_pfu else 'СОБЫТИЕ'))
    else:
        print('GOES        данных нет → статус INSUFFICIENT')
    print()
    print('ОКНА  длительность %d мин, начало каждые %d мин в течение %d ч' % (DURATION_MIN, START_STEP_MIN, SEARCH_HOURS))
    print('  %-17s %10s %9s %14s' % ('начало UTC', 'в аномалии', 'пролётов', 'интеграл >30МэВ'))
    show = sorted(windows, key=lambda w: w['saa_min'])
    for w in show[:3] + [None] + show[-3:]:
        if w is None:
            print('  ...'); continue
        print('  %-17s %7.0f мин %9d %14.3g' % (w['start'].strftime('%m-%d %H:%M'), w['saa_min'], w['n_crossings'], w['flux_int']))
    print()
    print('РЕКОМЕНДАЦИЯ  вердикт: %s' % verdict)
    print('              правило: %s' % rule)
    if verdict == 'preferred':
        print('              предпочтительно начало %s: %.0f мин в аномалии против %.0f у худшего (%s)'
              % (best['start'].strftime('%Y-%m-%d %H:%MZ'), best['saa_min'], worst['saa_min'], worst['start'].strftime('%H:%MZ')))
    print('              механизм 2 (сближения, статистика MMOD) в демо не считался → его статус INSUFFICIENT,')
    print('              и по п.1 правила ПОЛНАЯ рекомендация невозможна, пока он не подключён.')


if __name__ == '__main__':
    main()
