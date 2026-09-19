# -*- coding: utf-8 -*-
"""Проверка ПРОВЕРЯЮЩЕГО: трассировщик силовой линии должен быть верен раньше, чем им что-то мерят.

Логика набора. Инструмент, которым измеряют цену допущения, сам обязан быть проверен на задаче
с ИЗВЕСТНЫМ ответом, иначе его числа ничего не стоят. Такая задача есть: в поле центрального
диполя координаты Мак-Илвейна известны аналитически (L = r/R_E/cos²λ, B/B0 = √(1+3sin²λ)/cos⁶λ),
и трассировщик обязан их воспроизвести, не зная формул. Там же проверяется сходимость по шагу.

На полном IGRF точного ответа нет, поэтому проверяется то, что проверить можно: совпадение
модуля поля в исходной точке с рабочим модулем орбиты (это доказывает, что сравниваются две
МОДЕЛИ КООРДИНАТ, а не две разные точки), структурное свойство B/B0 ≥ 1 и сходимость по шагу.

Отдельным тестом закреплено, что модуль не подключён к рабочему пути: это условие задачи —
измерить, а не заменить.
"""
from __future__ import annotations

import math
import os
import re
from datetime import datetime, timezone

import numpy as np
import pytest

from vkd.assess import magcheck as mc
from vkd.assess.magcoords import geodetic_to_ecef_km
from vkd.orbit.magnetic import magnetic_coordinates
from vkd.types import MagMethod, TrajectoryPoint

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHC = os.path.join(ROOT, 'data', 'orbit', 'IGRF13.shc')
WHEN = datetime(2024, 5, 3, 12, tzinfo=timezone.utc)

# Точки трассы МКС: заполярье, средние широты, ядро Южно-Атлантической аномалии и её край.
SAMPLE = [(51.6, 30.0, 415.0), (23.0, -120.0, 420.0), (-25.0, -45.0, 418.0),
          (-33.0, -30.0, 412.0), (-8.0, -70.0, 425.0), (45.0, 150.0, 410.0)]


def _points(sample=SAMPLE):
    return [TrajectoryPoint(WHEN, lat, lon, alt, None, None, None, None,
                            MagMethod.NONE, 'outside_model', None) for lat, lon, alt in sample]


def _points_with_field(sample=SAMPLE):
    """Те же точки, но с |B| полного IGRF от рабочего модуля орбиты: рабочему модулю координат
    оно нужно на входе, и подставлять сюда своё было бы подменой сравниваемой величины."""
    from pathlib import Path
    ecef = np.array([geodetic_to_ecef_km(lat, lon, alt) for lat, lon, alt in sample])
    field = magnetic_coordinates(ecef, np.array([s[1] for s in sample]), np.array([s[0] for s in sample]),
                                 np.array([s[2] for s in sample]), [WHEN] * len(sample), Path(SHC))
    return [TrajectoryPoint(WHEN, lat, lon, alt, float(field['B_nT'][i]), None, None, None,
                            MagMethod.NONE, 'outside_model', None) for i, (lat, lon, alt) in enumerate(sample)]


def _dipole_case(latitudes, alt_km=400.0):
    lat = np.asarray(latitudes, dtype=float)
    r = np.full(lat.size, mc.R_E_KM + alt_km)
    theta = np.radians(90.0 - lat)
    phi = np.radians(np.linspace(0.0, 300.0, lat.size))
    L_exact = (r / mc.R_E_KM) / np.cos(np.radians(lat)) ** 2
    BB_exact = np.sqrt(1 + 3 * np.sin(np.radians(lat)) ** 2) / np.cos(np.radians(lat)) ** 6
    return r, theta, phi, L_exact, BB_exact


# ---------------------------------------------------------------------------
# Диполь: ответ известен точно
# ---------------------------------------------------------------------------

def test_dipole_equator_L_equals_geocentric_distance_and_BB0_is_one():
    """На магнитном экваторе L обязан равняться r/R_E, а B/B0 — единице.

    Это самая жёсткая из точек с известным ответом: обе величины проверяются одновременно и
    не через ту же формулу, которой считались (L идёт через Д.29 от найденного минимума поля,
    а сравнивается с геометрией точки).
    """
    r, theta, phi, L_exact, BB_exact = _dipole_case([0.0])
    res = mc.trace_field_lines(mc.centred_dipole_field(), r, theta, phi, ds_km=20.0)
    assert res.status[0] == 'ok'
    assert res.L_ost[0] == pytest.approx(float(r[0] / mc.R_E_KM), rel=1e-9)
    assert res.B_over_B0[0] == pytest.approx(1.0, abs=1e-9)
    assert L_exact[0] == pytest.approx(float(r[0] / mc.R_E_KM), rel=1e-12) and BB_exact[0] == 1.0


def test_dipole_L_and_BB0_match_analytic_values():
    r, theta, phi, L_exact, BB_exact = _dipole_case([0.0, 5.0, 15.0, 30.0, 45.0, 51.6, -40.0])
    res = mc.trace_field_lines(mc.centred_dipole_field(), r, theta, phi, ds_km=20.0)
    assert (res.status.astype(str) == 'ok').all()
    assert np.max(np.abs(res.L_ost / L_exact - 1.0)) < 1e-9
    assert np.max(np.abs(res.B_over_B0 / BB_exact - 1.0)) < 1e-9


def test_dipole_step_halving_converges():
    """Уменьшение шага вдвое обязано уменьшать ошибку, а не менять ответ как попало."""
    r, theta, phi, L_exact, _ = _dipole_case([15.0, 30.0, 51.6])
    errs = []
    for ds in (160.0, 80.0, 40.0, 20.0):
        res = mc.trace_field_lines(mc.centred_dipole_field(), r, theta, phi, ds_km=ds)
        errs.append(float(np.max(np.abs(res.L_ost / L_exact - 1.0))))
    for coarse, fine in zip(errs, errs[1:]):
        assert fine < coarse / 4.0, errs        # порядок схемы не ниже второго
    assert errs[-1] < 1e-9, errs


def test_invariant_branch_reproduces_dipole_shell():
    """Вторая ветка (интегральный инвариант I и определение Мак-Илвейна) — тоже на известном ответе.

    Она независима от Д.29: L получается из пары (I, B_m), а не из поля на экваторе. Согласие
    двух веток в дипольном поле означает, что и квадратура инварианта, и связь L(I, B_m)
    построены верно.
    """
    r, theta, phi, L_exact, _ = _dipole_case([5.0, 15.0, 30.0, 45.0, 51.6])
    res = mc.trace_field_lines(mc.centred_dipole_field(), r, theta, phi, ds_km=20.0)
    assert np.isfinite(res.L_invariant).all()
    assert np.max(np.abs(res.L_invariant / L_exact - 1.0)) < 1e-4
    # инвариант растёт с широтой зеркальной точки и обращается в нуль на экваторе
    assert np.all(np.diff(res.invariant_RE[:4]) > 0)


def test_invariant_zero_gives_equatorial_L():
    """I → 0 — частица зеркалит на экваторе: L обязан стать ∛(M/B_m), как в Д.29."""
    Bm = np.array([0.311653 / 1.5 ** 3])
    assert float(mc.l_from_invariant(np.array([0.0]), Bm)[0]) == pytest.approx(1.5, rel=1e-6)


def test_D29_constant_is_the_one_from_the_standard():
    """Число в Д.29 — из ОСТ 134-1044-2007 (момент Мак-Илвейна), а не подобрано."""
    assert mc.M_GAUSS_RE3 == 0.311653
    assert mc.R_E_KM == 6371.2
    assert '0,311653' in mc.__doc__ and 'Д.29' in mc.__doc__


# ---------------------------------------------------------------------------
# Полное IGRF: точного ответа нет, проверяется то, что проверяемо
# ---------------------------------------------------------------------------

def test_traced_start_field_matches_orbit_module():
    """|B| в исходной точке у трассировщика и у рабочего модуля орбиты — одно и то же поле.

    Без этого сравнение координат было бы сравнением двух разных точек: расхождение позиции
    или эпохи выдало бы себя именно здесь.
    """
    points = _points()
    res = mc.traced_coordinates(points, SHC, ds_km=100.0, when=WHEN)
    ecef = np.array([geodetic_to_ecef_km(p.lat_deg, p.lon_deg, p.alt_km) for p in points])
    field = magnetic_coordinates(ecef, np.array([p.lon_deg for p in points]),
                                 np.array([p.lat_deg for p in points]),
                                 np.array([p.alt_km for p in points]), [p.t_utc for p in points],
                                 __import__('pathlib').Path(SHC))
    assert np.max(np.abs(res.B_start_nT / field['B_nT'] - 1.0)) < 1e-5


def test_traced_ratio_never_below_one():
    """B/B0 ≥ 1 по построению: B0 — минимум поля на той же линии, где взята точка.

    У рабочей пары координат этого свойства нет (B — полное IGRF, B0 — от диполя), и статус
    inconsistent_BB0 в magcoords.py существует именно поэтому.
    """
    res = mc.traced_coordinates(_points(), SHC, ds_km=100.0, when=WHEN)
    ok = res.ok
    assert ok.any()
    assert np.all(res.B_over_B0[ok] >= 1.0 - 1e-12)
    assert np.all(res.L_ost[ok] > 1.0)


def test_traced_igrf_step_halving_is_stable():
    """Шаг вдвое мельче не должен менять измеряемые величины заметно по сравнению с тем
    расхождением, которое этим инструментом меряют (единицы и десятки процентов)."""
    points = _points()
    coarse = mc.traced_coordinates(points, SHC, ds_km=100.0, when=WHEN)
    fine = mc.traced_coordinates(points, SHC, ds_km=50.0, when=WHEN)
    m = coarse.ok & fine.ok
    assert m.sum() >= 4
    assert np.max(np.abs(fine.L_ost[m] / coarse.L_ost[m] - 1.0)) < 1e-4
    assert np.max(np.abs(fine.B_over_B0[m] / coarse.B_over_B0[m] - 1.0)) < 1e-4


def test_saa_point_differs_from_eccentric_dipole():
    """В ядре аномалии рабочая и проверочная пары координат ДОЛЖНЫ разойтись: это и есть предмет
    измерения. Тест фиксирует, что расхождение не нулевое и не бесконечное — иначе сравнивать
    было бы нечего или сравнение было бы сломано."""
    from vkd.assess.magcoords import belt_coordinates
    points = _points_with_field([(-25.0, -45.0, 418.0), (-33.0, -30.0, 412.0)])
    ecc, _ = belt_coordinates(points, SHC)
    res = mc.traced_coordinates(points, SHC, ds_km=100.0, when=WHEN)
    for i, p in enumerate(ecc):
        assert p.L is not None and res.ok[i]
        rel = abs(p.L / res.L_ost[i] - 1.0)
        assert 0.0 < rel < 0.5


# ---------------------------------------------------------------------------
# Абсолютный уровень: то, на чём держатся числа раздела 5 документа
# ---------------------------------------------------------------------------

def test_energy_integration_is_exact_on_power_law():
    """Интеграл по энергии обязан быть ТОЧНЫМ на степенном спектре: таблицы приложения А
    дают дифференциальный поток, и интегральный получается интегрированием, а не суммой узлов.

    Проверка нужна потому, что ошибка здесь дала бы ПОСТОЯННЫЙ множитель во всех флюенсах
    сразу и объяснила бы расхождение среднего уровня с таблицей К.2.2 одним махом.
    Она его не объясняет: расхождение здесь — машинный нуль.
    """
    from vkd.assess.trapped import integrate_power_law
    E = np.array([0.2, 0.6, 1.25, 3, 5, 12.5, 30, 50, 125, 300], dtype=float)
    for power in (-0.5, -1.0, -2.0, -3.0):
        f = 137.0 * E ** power
        exact = (137.0 * math.log(E[-1] / 30.0) if abs(power + 1) < 1e-12 else
                 137.0 / (power + 1) * (E[-1] ** (power + 1) - 30.0 ** (power + 1)))
        assert integrate_power_law(E, f, 30.0) == pytest.approx(exact, rel=1e-12)
    # излом спектра и нижний предел между узлами
    f = np.where(E < 12.5, 100 * E ** -1.5, 100 * 12.5 ** -1.5 * (E / 12.5) ** -3.0)
    a = 100 * 12.5 ** -1.5 * 12.5 ** 3.0
    exact = a / (-2.0) * (300.0 ** -2.0 - 37.0 ** -2.0)
    assert integrate_power_law(E, f, 37.0) == pytest.approx(exact, rel=1e-12)


def test_K22_reference_level_from_the_standard():
    """Опорный уровень К.2.2 (60°, 400 км, ≥30 МэВ) разбирается из текста ОСТ и равен 5,56.

    Число процитировано в docs/methods/PROVERKA_MAGKOORDINAT.md и в строке для экрана;
    если разбор таблицы собьётся, тест обязан упасть, а не тихо дать другое основание.
    """
    import importlib.util
    path = os.path.join(ROOT, 'scripts', 'magcheck_average.py')
    spec = importlib.util.spec_from_file_location('magcheck_average', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    energies, values = module.k22_table()
    assert energies[0] == 0.1 and energies[-1] == 501.0 and values.shape == (38, 16)
    assert module.k22_mean_flux()['H_400_km']['flux_per_cm2_s'] == pytest.approx(5.56, abs=0.01)


# ---------------------------------------------------------------------------
# Условие задачи: модуль в рабочий расчёт не подключён
# ---------------------------------------------------------------------------

def test_magcheck_is_not_wired_into_production():
    """Ни один рабочий модуль не имеет права импортировать проверку: она измеряет, а не заменяет."""
    offenders = []
    for folder in ('vkd', 'app'):
        for base, _, files in os.walk(os.path.join(ROOT, folder)):
            for name in files:
                if not name.endswith('.py') or name == 'magcheck.py':
                    continue
                path = os.path.join(base, name)
                text = open(path, encoding='utf-8').read()
                if re.search(r'^\s*(from|import)\s+.*magcheck', text, re.M):
                    offenders.append(path)
    assert not offenders, offenders
