# -*- coding: utf-8 -*-
"""Средний уровень потока ЕРПЗ: наш расчёт против собственной таблицы К.2.2 ОСТ 134-1044-2007.

Вопрос, на который отвечает скрипт: объясняет ли переход с эксцентричного диполя на трассировку
силовой линии расхождение среднего уровня потока с таблицей К.2.2 стандарта — и если нет, то что.

Считаются средние интегральные потоки ≥30 МэВ по приложению А ОСТ на ОДНОЙ И ТОЙ ЖЕ эфемериде:
  1) с рабочими координатами (эксцентричный диполь, vkd/assess/magcoords.py);
  2) с координатами от трассировки силовой линии по полному IGRF (vkd/assess/magcheck.py);
  3) то и другое, но с полем ЭПОХИ МОДЕЛИ (AP-8 построена на поле конца 1960-х), а не 2024 года —
     это проверка того, не даёт ли основной вклад несовпадение эпох поля и таблицы.
Плюс само сравнение с таблицей К.2.2 (i = 60°, H = 400 км), извлечённой здесь же из текста ОСТ.

Скрипт ничего не меняет в рабочем расчёте. Запуск:
    python scripts/magcheck_average.py [--days 60] [--ds-km 40]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import numpy as np
import ppigrf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vkd.assess import magcheck as mc
from vkd.assess.magcoords import belt_coordinates
from vkd.assess.trapped import BeltTable, integrate_power_law
from vkd.orbit import trajectory_with_provenance
from vkd.orbit.oem import OrbitDataError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHC = os.path.join(ROOT, 'data', 'orbit', 'IGRF13.shc')
OST_TXT = os.path.join(ROOT, 'docs', 'istochniki', 'OST_134-1044-2007_tekst.txt')
E_MIN_MEV = 30.0
SAA_B_NT = 24000.0
CHUNK_MIN = 1920                       # предел одного вызова модуля орбиты
START = datetime(2024, 5, 1, 12, tzinfo=timezone.utc)
# Эпоха поля, на котором построена AP-8 (она же основа приложения А ОСТ): модель минимума
# солнечной активности AP8MIN привязана к полю GSFC 12/66, продлённому на 1970 год.
MODEL_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
# Строки таблицы К.2.2 в текстовом дампе ОСТ (i = 60°, дифференциальные спектры за САС 10 лет)
K22_BLOCKS = ((21723, 22165), (22166, 22407))
K22_ALTITUDES = (400, 600, 800, 1000, 1700, 2000, 2500, 3000, 4000, 5000, 6000,
                 8000, 10000, 15000, 20000, 30000)
SAS_YEARS_SECONDS = 10 * 365.25 * 86400.0


# ---------------------------------------------------------------------------
# Таблица К.2.2 из текста стандарта
# ---------------------------------------------------------------------------

def k22_table() -> tuple[np.ndarray, np.ndarray]:
    """Энергии и значения таблицы К.2.2 (16 высот) из docs/istochniki/OST_134-1044-2007_tekst.txt.

    В дампе каждое число стоит на своей строке, а таблица разорвана страницей («Окончание
    таблицы К.2.2»), поэтому блоки склеиваются и режутся по 17 чисел (энергия + 16 высот).
    Единица столбцов — пр./(МэВ·см²) ЗА САС 10 лет, то есть дифференциальный ФЛЮЕНС, а не поток.
    """
    lines = io.open(OST_TXT, encoding='utf-8').read().splitlines()
    pattern = re.compile(r'^\s*(\d+(?:,\d+)?[eE][+-]\d+|\d+)\s*$')
    grab = lambda a, b: [float(m.group(1).replace(',', '.')) for m in
                         (pattern.match(ln.strip()) for ln in lines[a - 1:b]) if m]
    first, second = grab(*K22_BLOCKS[0]), grab(*K22_BLOCKS[1])
    n = len(K22_ALTITUDES)
    values = first[n:] + second[:1] + second[n + 1:]
    if len(values) % (n + 1):
        raise ValueError('К.2.2 разобрана неверно: %d чисел не делится на %d' % (len(values), n + 1))
    rows = np.array(values).reshape(-1, n + 1)
    if not np.all(np.diff(rows[:, 0]) > 0):
        raise ValueError('К.2.2: энергии не возрастают — разбор сбился')
    return rows[:, 0], rows[:, 1:]


def k22_mean_flux(e_min: float = E_MIN_MEV) -> dict:
    E, F = k22_table()
    out = {}
    for i, alt in enumerate(K22_ALTITUDES[:3]):
        fluence = integrate_power_law(E, F[:, i], e_min)
        out['H_%d_km' % alt] = {'fluence_per_cm2_10y': fluence,
                                'flux_per_cm2_s': fluence / SAS_YEARS_SECONDS,
                                'flux_per_cm2_s_trapezoid': float(np.trapezoid(F[E >= e_min, i], E[E >= e_min]))
                                / SAS_YEARS_SECONDS}
    out['note'] = ('единица столбцов — пр./(МэВ·см²) за САС 10 лет (дифференциальный флюенс); '
                   'поток получен делением на %g с' % SAS_YEARS_SECONDS)
    return out


# ---------------------------------------------------------------------------
# Эфемерида и средние потоки
# ---------------------------------------------------------------------------

def ephemeris(days: int, step_seconds: int = 60):
    points, chunks = [], []
    start = START
    while (start - START).days < days:
        minutes = min(CHUNK_MIN, days * 1440 - int((start - START).total_seconds() // 60))
        try:
            _, chunk, _ = trajectory_with_provenance(start, minutes, SAA_B_NT, mode='history_review',
                                                     cutoff_utc=start, step_seconds=step_seconds)
        except OrbitDataError as exc:
            chunks.append({'start_utc': start.isoformat(), 'minutes': minutes, 'error': str(exc)})
            start += timedelta(minutes=minutes)
            continue
        points.extend(chunk[:-1])
        chunks.append({'start_utc': start.isoformat(), 'minutes': minutes, 'n': len(chunk) - 1})
        start += timedelta(minutes=minutes)
    return points, chunks


def _field_at(points, when: datetime) -> np.ndarray:
    """|B| полного IGRF в точках трассы на ЗАДАННУЮ эпоху — так же, как его считает модуль орбиты
    (ppigrf.igrf по геодезическим широте, долготе и высоте), но с другой датой коэффициентов."""
    be, bn, bu = ppigrf.igrf(np.array([p.lon_deg for p in points]), np.array([p.lat_deg for p in points]),
                             np.array([p.alt_km for p in points]), when.replace(tzinfo=None), coeff_fn=SHC)
    return np.sqrt(be[0] ** 2 + bn[0] ** 2 + bu[0] ** 2)


def _mean_flux(flux: np.ndarray) -> dict:
    """Средний поток по равномерной сетке: по покрытым точкам и по ВСЕМ точкам (нет модели = 0).

    Сетка равномерна, поэтому среднее по времени равно среднему арифметическому. Оба числа
    нужны: таблица К.2 — среднее по ВСЕЙ орбите, а покрытие нашей модели меньше единицы.
    """
    known = np.isfinite(flux)
    return {'mean_covered': float(np.mean(flux[known])) if known.any() else None,
            'mean_all_zero_outside': float(np.mean(np.where(known, flux, 0.0))),
            'coverage_fraction': float(known.mean()), 'n': int(flux.size)}


def flux_series(points, L, BB, belts: BeltTable) -> np.ndarray:
    out = np.full(len(points), np.nan)
    for i in range(len(points)):
        if not (np.isfinite(L[i]) and np.isfinite(BB[i])):
            continue
        value = belts.integral_flux(float(L[i]), float(BB[i]), E_MIN_MEV).value_per_cm2_s
        out[i] = np.nan if value is None else value
    return out


def dipole_coords(points, epoch: datetime | None = None):
    """Рабочая пара координат. Для проверки эпохи точки переносятся во времени вместе с |B|:
    сам модуль magcoords не меняется — меняется то, что ему подаётся."""
    if epoch is not None:
        B = _field_at(points, epoch)
        points = [replace(p, t_utc=epoch, B_nT=float(B[i])) for i, p in enumerate(points)]
    coords, _ = belt_coordinates(points, SHC)
    return (np.array([c.L if c.L is not None else np.nan for c in coords]),
            np.array([c.B_over_B0 if c.B_over_B0 is not None else np.nan for c in coords]))


def circular_orbit_points(alt_km: float = 400.0, inclination_deg: float = 60.0, days: int = 30,
                          step_seconds: int = 60, epoch: datetime = datetime(2024, 5, 1, tzinfo=timezone.utc)):
    """Синтетическая КРУГОВАЯ орбита — те самые условия, для которых дана таблица К.2.2.

    Нужна, чтобы отделить «наш расчёт завышает» от «условия К.2.2 не совпадают с трассой МКС»:
    здесь высота и наклонение ровно такие, как в шапке таблицы стандарта. Движение —
    кеплеровское с вековым уходом узла от J2 (приложение Д, формула Д.8: именно этот уход
    стандарт и учитывает), Земля вращается под орбитой. Высота — ГЕОЦЕНТРИЧЕСКАЯ: круговая
    орбита это r = const, и «высота 400 км» в шапке К.2.2 читается именно так.
    """
    from vkd.types import MagMethod, TrajectoryPoint
    MU = 398600.4418
    J2, R_EQ, OMEGA_E = 1.08262668e-3, 6378.137, 7.292115e-5
    r = mc.R_E_KM + alt_km
    n = np.sqrt(MU / r ** 3)
    inc = np.radians(inclination_deg)
    node_rate = -1.5 * J2 * (R_EQ / r) ** 2 * n * np.cos(inc)
    t = np.arange(0, days * 86400, step_seconds, dtype=float)
    u, node, theta = n * t, node_rate * t, OMEGA_E * t
    x = r * (np.cos(u) * np.cos(node) - np.sin(u) * np.cos(inc) * np.sin(node))
    y = r * (np.cos(u) * np.sin(node) + np.sin(u) * np.cos(inc) * np.cos(node))
    z = r * np.sin(u) * np.sin(inc)
    xe = x * np.cos(theta) + y * np.sin(theta)
    ye = -x * np.sin(theta) + y * np.cos(theta)
    lat, lon, alt = _ecef_to_geodetic(xe, ye, z)
    B = np.sqrt(sum(c[0] ** 2 for c in ppigrf.igrf(lon, lat, alt, epoch.replace(tzinfo=None), coeff_fn=SHC)))
    return [TrajectoryPoint(epoch + timedelta(seconds=float(s)), float(lat[i]), float(lon[i]), float(alt[i]),
                            float(B[i]), None, None, None, MagMethod.NONE, 'outside_model', None)
            for i, s in enumerate(t)]


def _ecef_to_geodetic(x, y, z):
    """ECEF (км) → геодезические широта, долгота (град) и высота над эллипсоидом WGS84 (км),
    замкнутая формула Боуринга; обратное преобразование к geodetic_to_ecef_km рабочего модуля."""
    a, f = 6378.137, 1 / 298.257223563
    e2, b = 2 * f - f * f, 6378.137 * (1 - f)
    ep2 = (a * a - b * b) / (b * b)
    p = np.sqrt(x * x + y * y)
    th = np.arctan2(z * a, p * b)
    lat = np.arctan2(z + ep2 * b * np.sin(th) ** 3, p - e2 * a * np.cos(th) ** 3)
    N = a / np.sqrt(1 - e2 * np.sin(lat) ** 2)
    return np.degrees(lat), np.degrees(np.arctan2(y, x)), p / np.cos(lat) - N


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=60)
    ap.add_argument('--ds-km', type=float, default=40.0)
    ap.add_argument('--trace-days', type=int, default=3)
    ap.add_argument('--out', default=os.path.join(ROOT, 'docs', 'methods', 'proverka_sredniy_uroven.json'))
    args = ap.parse_args()
    belts = BeltTable('min')
    report = {'generated_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
              'e_min_MeV': E_MIN_MEV, 'belt_table': belts.source, 'belt_file': belts.file,
              'ost_K22': k22_mean_flux()}
    print('K.2.2:', report['ost_K22']['H_400_km'], flush=True)

    points, chunks = ephemeris(args.days)
    report['ephemeris'] = {'start_utc': START.isoformat(), 'days': args.days, 'step_seconds': 60,
                           'n_points': len(points), 'chunks': chunks,
                           'alt_km_mean': float(np.mean([p.alt_km for p in points])),
                           'alt_km_min_max': [float(min(p.alt_km for p in points)),
                                              float(max(p.alt_km for p in points))]}
    print('ephemeris points', len(points), flush=True)

    # полная эфемерида, рабочие координаты — воспроизведение числа смежного исполнителя
    L, BB = dipole_coords(points)
    full = _mean_flux(flux_series(points, L, BB, belts))
    report['full_ephemeris_dipole_2024'] = full
    print('full dipole 2024', full, flush=True)
    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)

    # подвыборка суток для трассировки: равномерно по всему интервалу, целыми сутками
    step = max(1, len(points) // args.trace_days)
    subset = []
    for k in range(args.trace_days):
        lo = k * step
        subset.extend(points[lo:lo + 1440])
    report['subset'] = {'n_points': len(subset), 'days': args.trace_days,
                        'from_utc': subset[0].t_utc.isoformat(), 'to_utc': subset[-1].t_utc.isoformat()}
    variants = {}
    for name, epoch in (('2024', None), ('1970', MODEL_EPOCH)):
        Ls, Bs = dipole_coords(subset, epoch)
        variants['dipole_' + name] = _mean_flux(flux_series(subset, Ls, Bs, belts))
        when = epoch if epoch is not None else subset[len(subset) // 2].t_utc
        traced = mc.traced_coordinates(subset, SHC, ds_km=args.ds_km, when=when)
        Lt = np.where(traced.ok, traced.L_ost, np.nan)
        Bt = np.where(traced.ok, traced.B_over_B0, np.nan)
        variants['traced_' + name] = _mean_flux(flux_series(subset, Lt, Bt, belts))
        variants['traced_' + name]['trace_status'] = {k: int(v) for k, v in
                                                      zip(*np.unique(traced.status.astype(str), return_counts=True))}
        print(name, variants['dipole_' + name]['mean_all_zero_outside'],
              variants['traced_' + name]['mean_all_zero_outside'], flush=True)
        report['subset_variants'] = variants
        with open(args.out, 'w', encoding='utf-8') as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)

    ref = report['ost_K22']['H_400_km']['flux_per_cm2_s']
    report['ratios_to_K22_400km'] = {
        'reference_flux_per_cm2_s': ref,
        'full_dipole_2024_over_K22': full['mean_all_zero_outside'] / ref,
        'full_dipole_2024_covered_over_K22': full['mean_covered'] / ref,
        **{'%s_over_K22' % k: v['mean_all_zero_outside'] / ref for k, v in variants.items()}}
    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    print('written', args.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
