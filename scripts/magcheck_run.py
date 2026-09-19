# -*- coding: utf-8 -*-
"""Измерение цены допущения R11: дипольные (L, B/B0) против трассировки силовой линии по IGRF.

Скрипт НИЧЕГО не меняет в рабочем расчёте. Он берёт трассу тем же кодом орбиты, что и сервис,
считает для её точек две пары координат — рабочую (эксцентричный диполь, vkd/assess/magcoords.py)
и проверочную (трассировка по полному IGRF, vkd/assess/magcheck.py) — и сравнивает:
L, B/B0 и, главное, ФЛЮЕНС за окно по таблицам приложения А ОСТ 134-1044-2007.

Запуск:  python scripts/magcheck_run.py [--ds-km 20] [--out <файл.json>]
Результат — JSON в docs/methods/proverka_magkoordinat.json, из него собран
docs/methods/PROVERKA_MAGKOORDINAT.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vkd.assess import magcheck as mc
from vkd.assess.magcoords import belt_coordinates
from vkd.assess.trapped import BeltTable
from vkd.orbit import trajectory_with_provenance
from vkd.orbit.integration import integrate_time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHC = os.path.join(ROOT, 'data', 'orbit', 'IGRF13.shc')
E_MIN_MEV = 30.0                      # канал сравнения окон, effective_config сохранённых примеров
SAA_B_NT = 24000.0                    # порог признака аномалии, тот же
STEP_SECONDS = 60                     # рабочая сетка трассы

# Случаи — из сохранённых примеров: начало трассы, её длительность и окна сравнения (начало, минуты)
CASES = [
    {'name': 'quiet_2024-05-03_12Z', 'start': datetime(2024, 5, 3, 12, tzinfo=timezone.utc),
     'span_min': 840, 'windows': [(0, 360), (480, 360)]},
    {'name': 'gannon_2024-05-10_cutoff12Z', 'start': datetime(2024, 5, 10, 12, tzinfo=timezone.utc),
     'span_min': 600, 'windows': [(0, 360), (240, 360)]},
]


def _stats(values) -> dict:
    v = np.asarray([x for x in values if np.isfinite(x)], dtype=float)
    if not v.size:
        return {'n': 0}
    return {'n': int(v.size), 'median': float(np.median(v)), 'p95': float(np.percentile(v, 95)),
            'max': float(np.max(v)), 'mean': float(np.mean(v))}


def _fluence(times, values, start, end) -> dict:
    r = integrate_time(times, values, start, end, max_gap_seconds=60.0)
    return {'value_per_cm2': r.known_integral, 'coverage_fraction': r.coverage_fraction,
            'covered_seconds': r.covered_seconds}


def run_case(case: dict, ds_km: float, belts: BeltTable) -> dict:
    start, span = case['start'], case['span_min']
    t0 = time.perf_counter()
    _, points, _ = trajectory_with_provenance(start, span, SAA_B_NT, mode='history_review',
                                              cutoff_utc=start, step_seconds=STEP_SECONDS)
    ecc, ecc_info = belt_coordinates(points, SHC)
    traced = mc.traced_coordinates(points, SHC, ds_km=ds_km)
    seconds = time.perf_counter() - t0

    L_dip = np.array([p.L if p.L is not None else np.nan for p in ecc], dtype=float)
    BB_dip = np.array([p.B_over_B0 if p.B_over_B0 is not None else np.nan for p in ecc], dtype=float)
    B_point = np.array([p.B_nT for p in points], dtype=float)
    in_saa = np.array([bool(p.in_saa) for p in points])
    have = traced.ok & np.isfinite(L_dip) & np.isfinite(BB_dip)

    # Контроль того, что сравниваются ДВЕ МОДЕЛИ, а не две разные точки: |B| в исходной точке
    # у трассировщика и у модуля орбиты обязан совпасть (общий источник — то же IGRF).
    b_mismatch_pct = float(np.max(np.abs(traced.B_start_nT / B_point - 1.0)) * 100.0)

    dL = np.abs(L_dip / traced.L_ost - 1.0) * 100.0
    dBB = np.abs(BB_dip / traced.B_over_B0 - 1.0) * 100.0
    inv = traced.ok_invariant & np.isfinite(traced.L_invariant)
    dL_inv = np.abs(traced.L_invariant / traced.L_ost - 1.0) * 100.0

    # поток в точке по таблицам ОСТ: рабочая пара координат и проверочная
    flux_dip, flux_tr, status_dip, status_tr = [], [], {}, {}
    for i, p in enumerate(points):
        a = belts.integral_flux(L_dip[i] if np.isfinite(L_dip[i]) else None,
                                BB_dip[i] if np.isfinite(BB_dip[i]) else None, E_MIN_MEV)
        ok_t = traced.ok[i]
        b = belts.integral_flux(float(traced.L_ost[i]) if ok_t else None,
                                float(traced.B_over_B0[i]) if ok_t else None, E_MIN_MEV)
        flux_dip.append(a.value_per_cm2_s)
        flux_tr.append(b.value_per_cm2_s)
        status_dip[a.status] = status_dip.get(a.status, 0) + 1
        status_tr[b.status] = status_tr.get(b.status, 0) + 1

    # Общая опора: там, где модель ОСТ молчит хотя бы у одной пары координат, обнуляются ОБЕ.
    # Иначе отношение флюенсов смешивало бы две разные вещи — различие моделей и различие
    # покрытых участков окна (та же ловушка, что разобрана в tests/test_fluence_convergence.py).
    both = [None if (a is None or b is None) else a for a, b in zip(flux_dip, flux_tr)]
    both_tr = [None if (a is None or b is None) else b for a, b in zip(flux_dip, flux_tr)]

    times = [p.t_utc for p in points]
    windows = []
    for offset, minutes in case['windows']:
        w0 = start + timedelta(minutes=offset)
        w1 = w0 + timedelta(minutes=minutes)
        a, b = _fluence(times, flux_dip, w0, w1), _fluence(times, flux_tr, w0, w1)
        ca, cb = _fluence(times, both, w0, w1), _fluence(times, both_tr, w0, w1)
        div = lambda x, y: (y['value_per_cm2'] / x['value_per_cm2']
                            if x['value_per_cm2'] not in (None, 0.0) and y['value_per_cm2'] is not None else None)
        windows.append({'start_utc': w0.isoformat(), 'duration_min': minutes,
                        'fluence_dipole': a, 'fluence_traced': b, 'ratio_as_is': div(a, b),
                        'fluence_dipole_common': ca, 'fluence_traced_common': cb,
                        'ratio_common_support': div(ca, cb)})

    return {
        'name': case['name'], 'start_utc': start.isoformat(), 'span_min': span,
        'step_seconds': STEP_SECONDS, 'ds_km': ds_km, 'seconds_spent': round(seconds, 1),
        'n_points': len(points), 'n_in_saa': int(in_saa.sum()),
        'trace_status': {k: int(v) for k, v in zip(*np.unique(traced.status.astype(str), return_counts=True))},
        'B_start_vs_orbit_module_max_pct': b_mismatch_pct,
        'L_dipole_range': [float(np.nanmin(L_dip)), float(np.nanmax(L_dip))],
        'L_traced_range': [float(np.nanmin(traced.L_ost[traced.ok])), float(np.nanmax(traced.L_ost[traced.ok]))],
        'delta_L_pct': _stats(dL[have]),
        'delta_L_pct_saa': _stats(dL[have & in_saa]),
        'delta_BB0_pct': _stats(dBB[have]),
        'delta_BB0_pct_saa': _stats(dBB[have & in_saa]),
        'delta_L_invariant_vs_D29_pct': _stats(dL_inv[inv]),
        'flux_status_dipole': status_dip, 'flux_status_traced': status_tr,
        'n_points_flux_dipole_positive': int(sum(1 for f in flux_dip if f)),
        'n_points_flux_traced_positive': int(sum(1 for f in flux_tr if f)),
        'windows': windows,
        'eccentric_dipole_offset_km': ecc_info['offset_km'],
    }


def convergence(case: dict, ds_list, stride: int) -> dict:
    """Сходимость трассировщика по шагу на РЕАЛЬНОЙ трассе (подвыборка точек)."""
    start = case['start']
    _, points, _ = trajectory_with_provenance(start, case['span_min'], SAA_B_NT, mode='history_review',
                                              cutoff_utc=start, step_seconds=STEP_SECONDS)
    subset = points[::stride]
    out = {'case': case['name'], 'n_points': len(subset), 'steps': []}
    ref = None
    for ds in ds_list:
        t0 = time.perf_counter()
        r = mc.traced_coordinates(subset, SHC, ds_km=ds)
        row = {'ds_km': ds, 'seconds': round(time.perf_counter() - t0, 1),
               'n_ok': int(r.ok.sum()),
               'L_median': float(np.nanmedian(r.L_ost[r.ok])),
               'BB0_median': float(np.nanmedian(r.B_over_B0[r.ok]))}
        if ref is not None:
            m = ref.ok & r.ok
            row['max_dL_vs_prev_pct'] = float(np.nanmax(np.abs(r.L_ost[m] / ref.L_ost[m] - 1.0)) * 100)
            row['max_dBB0_vs_prev_pct'] = float(np.nanmax(np.abs(r.B_over_B0[m] / ref.B_over_B0[m] - 1.0)) * 100)
            # точки, где зеркальная точка сама на экваторе (I ≈ 0), в относительное сравнение
            # не берутся: у них знаменатель нулевой, а сравнивать нечего
            mi = ref.ok_invariant & r.ok_invariant & (ref.invariant_RE > 1e-3)
            row['n_invariant_compared'] = int(mi.sum())
            row['max_dI_vs_prev_pct'] = (float(np.nanmax(np.abs(r.invariant_RE[mi] / ref.invariant_RE[mi] - 1.0)) * 100)
                                         if mi.any() else None)
        out['steps'].append(row)
        ref = r
    return out


def dipole_selftest(ds_list) -> dict:
    """Проверка трассировщика там, где ответ известен точно: аналитический центральный диполь."""
    lat = np.array([0.0, 5.0, 15.0, 30.0, 45.0, 51.6, -40.0])
    r = np.full(lat.size, mc.R_E_KM + 400.0)
    theta, phi = np.radians(90.0 - lat), np.radians(np.linspace(0.0, 300.0, lat.size))
    L_exact = (r / mc.R_E_KM) / np.cos(np.radians(lat)) ** 2
    BB_exact = np.sqrt(1 + 3 * np.sin(np.radians(lat)) ** 2) / np.cos(np.radians(lat)) ** 6
    field = mc.centred_dipole_field()
    rows = []
    for ds in ds_list:
        res = mc.trace_field_lines(field, r, theta, phi, ds_km=ds)
        rows.append({'ds_km': ds,
                     'max_dL_pct': float(np.max(np.abs(res.L_ost / L_exact - 1)) * 100),
                     'max_dBB0_pct': float(np.max(np.abs(res.B_over_B0 / BB_exact - 1)) * 100),
                     'max_dL_invariant_pct': float(np.max(np.abs(res.L_invariant / L_exact - 1)) * 100)})
    return {'latitudes_deg': lat.tolist(), 'altitude_km': 400.0,
            'L_exact': L_exact.tolist(), 'B_over_B0_exact': BB_exact.tolist(),
            'equator_point': {'lat_deg': 0.0, 'L_exact': float(L_exact[0]),
                              'r_over_RE': float(r[0] / mc.R_E_KM), 'B_over_B0_exact': float(BB_exact[0])},
            'steps': rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--ds-km', type=float, default=20.0)
    ap.add_argument('--out', default=os.path.join(ROOT, 'docs', 'methods', 'proverka_magkoordinat.json'))
    ap.add_argument('--stride', type=int, default=8)
    args = ap.parse_args()

    belts = BeltTable('min')
    report = {'generated_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
              'method': 'ОСТ 134-1044-2007, приложение Д, п. Д.2: Д.26–Д.28 (трассировка), Д.29 (L по B0 на экваторе)',
              'field': 'полное IGRF-13 степени 13, data/orbit/IGRF13.shc, через ppigrf (те же коэффициенты, что в расчёте)',
              'belt_table': {'source': belts.source, 'file': belts.file, 'sha256': belts.sha256},
              'e_min_MeV': E_MIN_MEV, 'ds_km': args.ds_km,
              'dipole_selftest': dipole_selftest([80.0, 40.0, 20.0, 10.0, 5.0]),
              'convergence_igrf': convergence(CASES[0], [80.0, 40.0, 20.0, 10.0], args.stride),
              'cases': []}
    for case in CASES:
        print('case', case['name'], flush=True)
        report['cases'].append(run_case(case, args.ds_km, belts))
        with open(args.out, 'w', encoding='utf-8') as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
    print('written', args.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
