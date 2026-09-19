# -*- coding: utf-8 -*-
"""Средний по орбите уровень потока захваченных протонов — нормировка дозы.

Что здесь считается и зачем. Дозовые таблицы приложения К ОСТ 134-1044-2007
дают ПОГЛОЩЁННУЮ ДОЗУ за 10 лет САС на круговой орбите. Чтобы получить дозу
за шестичасовое окно ВКД, нужен коэффициент перехода, а для него — средний по
орбите уровень того же самого канала, посчитанный ТЕМ ЖЕ кодом, что и флюенс
окна. Здесь он и считается: та же траектория (`vkd.orbit.trajectory`), те же
магнитные координаты (`vkd.assess.magcoords`), та же таблица приложения А
(`vkd.assess.trapped`) и тот же интегратор (`vkd.orbit.integration`).

Образец подхода — `vkd/assess/seasonal.py`, где годовое среднее вычитается из
мгновенного значения в едином пространственном эталоне. Разница в том, что
там среднее считается на лету по аналитическому профилю, а здесь один прогон
по 60 суткам эфемериды занимает секунды и сохраняется в файл: интерфейс не
должен пересчитывать его на каждый запрос.

Период усреднения — 1 мая … 30 июня 2024, 60 суток, реконструированная
эфемерида МКС (OEM, режим `history_review`), шаг 60 с. Это не произвольный
отрезок: это ВЕСЬ архив орбиты проекта, и на нём среднее выходит на полку
(сходимость сохраняется в файл: от 30 к 60 суткам изменение около 1 %).

Соглашение об усреднении ТО ЖЕ, что у окна: `integrate_time` даёт известный
вклад по покрытым интервалам, а делится он на ПОЛНУЮ длительность периода.
Время без модели (у нас это L < 1,14 при сильном поле) входит в среднее как
нулевой вклад — ровно как в флюенсе окна. Оба конца отношения считаются
одинаково, иначе коэффициент был бы смещён.

Запуск: python scripts/dose_normalisation.py
Результат: data/ost1044_dose/orbit_mean_flux.json
"""
from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from vkd.assess.magcoords import belt_coordinates          # noqa: E402
from vkd.assess.trapped import BeltTable                   # noqa: E402
from vkd.orbit.integration import integrate_time           # noqa: E402
from vkd.orbit.trajectory import trajectory                # noqa: E402

OUT_DIR = os.path.join(_ROOT, 'data', 'ost1044_dose')
OUT = os.path.join(OUT_DIR, 'orbit_mean_flux.json')
COEFF = os.path.join(_ROOT, 'data', 'orbit', 'IGRF13.shc')

MODEL_ID = 'ost1044_belt_dose_norm_v1'
START = datetime(2024, 5, 1, tzinfo=timezone.utc)
CHUNK_MIN = 1920                 # верхний предел одного вызова trajectory()
N_CHUNKS = 45                    # 45 × 32 ч = 60 суток
STEP_SECONDS = 60
SAA_B_THRESHOLD_NT = 22000.0     # только чтобы вызвать trajectory(); в среднее не входит
E_MIN_GRID = (12.5, 30.0, 50.0)  # канал расчёта и обе точки сетки устойчивости compute.py
SOLAR = ('min', 'max')


def compute() -> dict:
    tables = {sa: BeltTable(sa) for sa in SOLAR}
    acc = {(sa, e): {'integral': 0.0, 'covered': 0.0} for sa in SOLAR for e in E_MIN_GRID}
    duration = 0.0
    alt_sum, alt_n, alt_lo, alt_hi = 0.0, 0, None, None
    progress = []
    method = field_model = None
    for i in range(N_CHUNKS):
        start = START + timedelta(minutes=CHUNK_MIN * i)
        end = start + timedelta(minutes=CHUNK_MIN)
        meta, points = trajectory(start, CHUNK_MIN, SAA_B_THRESHOLD_NT, step_seconds=STEP_SECONDS)
        method, field_model = meta.method, meta.field_model
        track, _ = belt_coordinates(points, COEFF)
        times = [p.t_utc for p in track]
        for sa in SOLAR:
            for e_min in E_MIN_GRID:
                values = [tables[sa].integral_flux(p.L, p.B_over_B0, e_min).value_per_cm2_s for p in track]
                ti = integrate_time(times, values, start, end, max_gap_seconds=60.0)
                acc[(sa, e_min)]['integral'] += ti.known_integral or 0.0
                acc[(sa, e_min)]['covered'] += ti.covered_seconds
        duration += (end - start).total_seconds()
        for p in points:
            alt_sum += p.alt_km
            alt_n += 1
            alt_lo = p.alt_km if alt_lo is None else min(alt_lo, p.alt_km)
            alt_hi = p.alt_km if alt_hi is None else max(alt_hi, p.alt_km)
        progress.append({'chunks': i + 1, 'days': duration / 86400.0,
                         'mean_flux_per_cm2_s': acc[('min', 30.0)]['integral'] / duration})
        print('  %2d/%d  %5.1f сут  <φ>(min, ≥30 МэВ) = %.4f см⁻²·с⁻¹'
              % (i + 1, N_CHUNKS, duration / 86400.0, progress[-1]['mean_flux_per_cm2_s']))

    channels = {}
    for sa in SOLAR:
        for e_min in E_MIN_GRID:
            a = acc[(sa, e_min)]
            channels['%s|%g' % (sa, e_min)] = {
                'solar_activity': sa, 'e_min_MeV': e_min,
                'table': tables[sa].table_name, 'raw_record_id': tables[sa].raw_record_id,
                # Основное значение: известный вклад / ПОЛНАЯ длительность — соглашение окна.
                'mean_flux_per_cm2_s': a['integral'] / duration,
                # Альтернатива: известный вклад / покрытое время. Разница — объявленное число,
                # а не выбор «как удобнее»: она целиком равна доле времени без модели.
                'mean_flux_over_covered_time_per_cm2_s': a['integral'] / a['covered'],
                'coverage_fraction': a['covered'] / duration,
                'fluence_10y_per_cm2': a['integral'] / duration * 10 * 365.25 * 86400.0,
            }
    half = progress[N_CHUNKS // 2 - 1]
    return {
        'model_id': MODEL_ID,
        'generated_by': 'scripts/dose_normalisation.py',
        'method_ru': ('средний по орбите уровень потока захваченных протонов, посчитанный тем же '
                      'кодом, что и флюенс окна: trajectory → magcoords → trapped → integrate_time; '
                      'известный вклад делится на ПОЛНУЮ длительность периода, как у окна'),
        'period': {'from_utc': START.isoformat(),
                   'to_utc': (START + timedelta(minutes=CHUNK_MIN * N_CHUNKS)).isoformat(),
                   'days': duration / 86400.0, 'step_seconds': STEP_SECONDS,
                   'chunk_minutes': CHUNK_MIN, 'chunks': N_CHUNKS,
                   'trajectory_method': method, 'field_model': field_model,
                   'saa_B_threshold_nT': SAA_B_THRESHOLD_NT},
        'orbit': {'mean_alt_km': alt_sum / alt_n, 'min_alt_km': alt_lo, 'max_alt_km': alt_hi,
                  'inclination_deg': 51.6,
                  'inclination_note_ru': 'наклонение МКС; в расчёт нормировки не вводится — '
                                         'оно уже заложено в самой эфемериде'},
        'channels': channels,
        'convergence': {
            'mean_at_half_period_per_cm2_s': half['mean_flux_per_cm2_s'],
            'half_period_days': half['days'],
            'relative_change_half_to_full': abs(channels['min|30']['mean_flux_per_cm2_s']
                                                - half['mean_flux_per_cm2_s'])
                                            / channels['min|30']['mean_flux_per_cm2_s'],
            'note_ru': 'изменение среднего при удвоении периода усреднения; это показатель '
                       'сходимости выборки, а не граница физической погрешности модели',
            'progress': progress,
        },
    }


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    data = compute()
    io.open(OUT, 'w', encoding='utf-8').write(json.dumps(data, ensure_ascii=False, indent=1))
    c = data['channels']['min|30']
    print('\nсредний поток ≥30 МэВ (минимум СА): %.4f см⁻²·с⁻¹ (по покрытому времени %.4f, '
          'покрытие %.4f)' % (c['mean_flux_per_cm2_s'], c['mean_flux_over_covered_time_per_cm2_s'],
                              c['coverage_fraction']))
    print('средняя высота %.1f км; сходимость на половине периода %.3f %%'
          % (data['orbit']['mean_alt_km'], 100 * data['convergence']['relative_change_half_to_full']))
    print('записано:', OUT)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
