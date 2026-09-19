# -*- coding: utf-8 -*-
"""НЕЗАВИСИМАЯ проверка магнитных координат: L и B/B0 трассировкой силовой линии по полному IGRF.

В РАБОЧИЙ РАСЧЁТ ЭТОТ МОДУЛЬ НЕ ВКЛЮЧАЁТСЯ. Его никто не импортирует, кроме тестов
`tests/test_magcheck.py` и скрипта `scripts/magcheck_run.py`. Он существует, чтобы измерить
цену объявленного допущения, а не чтобы его заменить: за четыре часа до заморозки менять
модель, на которой держатся все сохранённые примеры, нельзя.

ЧТО ИМЕННО ПРОВЕРЯЕТСЯ. В рабочем расчёте в одной величине работают две модели поля:
  * L и B0 для входа в таблицы приложения А ОСТ 134-1044-2007 — эксцентричный диполь
    (`vkd/assess/magcoords.py`, решение R11);
  * |B| в точке — полное IGRF степени 13 (`vkd/orbit/magnetic.py`).
Здесь та же пара (L, B/B0) считается ОДНОЙ моделью — полным IGRF — по методу самого ОСТ.

ПЕРВОИСТОЧНИК МЕТОДА — ОСТ 134-1044-2007, приложение Д (рекомендуемое), пункт Д.2 «Метод
расчёта L-B координат», тот же стандарт, из приложения А которого взяты таблицы потоков.
Текст стандарта лежит в репозитории: `docs/istochniki/OST_134-1044-2007_izm2_radiaciya.docx`
(формулы там — объекты редактора уравнений, в текстовом дампе
`docs/istochniki/OST_134-1044-2007_tekst.txt` они утрачены; ниже они выписаны из DOCX).

  Д.26   dr/ds     = σ·B_r / B
  Д.27   dθ/ds     = σ·B_θ / (r·B)
  Д.28   dλ/ds     = σ·B_λ / (r·sinθ·B)
         где σ = +1, если линия строится в сторону от Земли, σ = −1 — в сторону Земли;
         s — длина дуги силовой линии; B — полная величина поля (Д.25).
  Д.29   L = ∛(0,311653 / B0),  B0 — величина поля оболочки НА МАГНИТНОМ ЭКВАТОРЕ, Гс.

Порядок действий стандарт задаёт дословно (приложение Д, конец п. Д.2): «координаты L из
заданной точки пространства определяют путем интегрирования системы обыкновенных
дифференциальных уравнений (Д.26)–(Д.28) … Интегрирование проводят до достижения точки
магнитного экватора. Найденное в этой точке значение магнитного поля позволяет определить
координату L по зависимости (Д.29)». Основанием для такого упрощения ОСТ называет свойство
параметра Мак-Илвейна: «для областей пространства с L < 5 он имеет с точностью до 1 %
одинаковое значение вдоль силовой линии». Трасса МКС лежит при L ≈ 1,1…1,6, то есть внутри
этой области; вход в таблицы А.2.1/А.2.2 идёт по паре (L, B/B0), где B0 — поле оболочки на
экваторе (приложение Е, расчёт (L, B/B0)-координат ссылается на приложение Д).

Число 0,311653 Гс·R_E³ в Д.29 — это магнитный момент Земли в единицах Мак-Илвейна
(McIlwain C. E. Coordinates for mapping the distribution of magnetically trapped particles //
J. Geophys. Res. 1961. Vol. 66, № 11. P. 3681–3691), опорный радиус R_E = 6371,2 км — тот же,
что у IGRF и у рабочего модуля координат.

ВТОРАЯ, НЕЗАВИСИМАЯ ОТ Д.29 ВЕТКА. Упрощение Д.29 опирается на постоянство L вдоль линии
с точностью 1 %, и проверять его тем же Д.29 бессмысленно. Поэтому здесь считается ещё и
классический интегральный инвариант (McIlwain 1961, формулы 5 и 6)

    I = ∫ sqrt(1 − B(s)/B_m) ds   — по дуге между двумя зеркальными точками,

и L восстанавливается из пары (I, B_m) по ОПРЕДЕЛЕНИЮ Мак-Илвейна: L — такой параметр
дипольной оболочки, при котором дипольное поле даёт то же сочетание инвариантов. Связь
L(I, B_m) строится здесь ЧИСЛЕННО, из самого диполя (функция `_dipole_invariant_curve`),
а не по запомненным коэффициентам аппроксимации: в дипольном поле величины
X = I³·B_m/M и Y = L³·B_m/M не зависят от L (масштабная инвариантность: длины ∝ L,
поле ∝ L⁻³), поэтому одной кривой Y(X), посчитанной на оболочке L = 1, хватает для любой
точки. Аппроксимация Хилтона (Hilton H. H. L parameter: a new approximation //
J. Geophys. Res. 1971. Vol. 76, № 28. P. 6952–6954) — это приближение к той же кривой;
здесь она НЕ используется, чтобы не вносить чужие коэффициенты по памяти.

ЕДИНИЦЫ. Поле внутри модуля — нТл (как отдаёт ppigrf и как хранит TrajectoryPoint),
в формулу Д.29 подставляется в гауссах (1 Гс = 1e5 нТл). Длины — км, инвариант I — в
радиусах Земли R_E = 6371,2 км.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Callable, Optional, Sequence

import numpy as np
import ppigrf

# Перевод геодезических (широта, долгота, высота) в ECEF берётся из рабочего модуля: позиция
# точки обязана строиться ТЕМ ЖЕ способом, иначе сравнивались бы две разные точки, а не две
# модели поля. Зависимость односторонняя — рабочий модуль об этом файле не знает.
from vkd.assess.magcoords import geodetic_to_ecef_km

R_E_KM = 6371.2                    # опорный радиус IGRF и единица длины для L
M_GAUSS_RE3 = 0.311653             # Д.29 ОСТ 134-1044-2007; McIlwain 1961
NT_PER_GAUSS = 1.0e5
MIN_ALT_KM = 100.0                 # ниже трассировать бессмысленно: частица поглощена атмосферой

FieldFn = Callable[[np.ndarray, np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray, np.ndarray]]


# ---------------------------------------------------------------------------
# Модели поля: полное IGRF (рабочие коэффициенты) и аналитический диполь для проверки
# ---------------------------------------------------------------------------

def igrf_field(coeff_path: str, when: datetime) -> FieldFn:
    """Полное IGRF степени 13 из ТЕХ ЖЕ файлов коэффициентов, что в рабочем расчёте.

    Возвращает функцию (r [км], θ [рад], φ [рад]) → (B_r, B_θ, B_λ) в нТл — ровно те
    составляющие, что стоят в правых частях Д.26–Д.28. Эпоха фиксирована одним моментом на
    весь обход линии: за время прохода частицы вдоль линии поле не меняется, а по трассе
    эпоха берётся своя у каждой выборки.
    """
    day = when.replace(tzinfo=None)

    def field(r_km: np.ndarray, theta: np.ndarray, phi: np.ndarray):
        Br, Bt, Bp = ppigrf.igrf_gc(np.asarray(r_km, dtype=float), np.degrees(theta),
                                    np.degrees(phi), day, coeff_fn=coeff_path)
        return Br[0], Bt[0], Bp[0]
    return field


def centred_dipole_field(moment_gauss_re3: float = M_GAUSS_RE3) -> FieldFn:
    """Аналитический центральный диполь, ось совпадает с осью вращения (для проверки трассировщика).

    B_r = −2·M·cosθ/(r/R_E)³, B_θ = −M·sinθ/(r/R_E)³, B_λ = 0; |B| = M/(r/R_E)³·√(1+3cos²θ).
    Знак выбран так, что на северном полюсе поле направлено ВНИЗ, как у Земли.
    В этом поле точный ответ известен заранее: L = (r/R_E)/cos²(широта), B/B0 = |B|/(M/L³).
    """
    m_nT = moment_gauss_re3 * NT_PER_GAUSS

    def field(r_km: np.ndarray, theta: np.ndarray, phi: np.ndarray):
        x = (np.asarray(r_km, dtype=float) / R_E_KM) ** 3
        return -2.0 * m_nT * np.cos(theta) / x, -m_nT * np.sin(theta) / x, np.zeros_like(x)
    return field


# ---------------------------------------------------------------------------
# Трассировка силовой линии: Д.26–Д.28, схема Рунге—Кутты 4-го порядка по дуге s
# ---------------------------------------------------------------------------

def _derivatives(field: FieldFn, r, theta, phi, sigma):
    """Правые части Д.26–Д.28 и модуль поля в точке."""
    Br, Bt, Bp = field(r, theta, phi)
    B = np.sqrt(Br * Br + Bt * Bt + Bp * Bp)
    # линии этой задачи идут к экватору и полюсов не касаются; клип — защита от деления на нуль
    sin_t = np.where(np.abs(np.sin(theta)) < 1e-9, 1e-9, np.sin(theta))
    return sigma * Br / B, sigma * Bt / (r * B), sigma * Bp / (r * sin_t * B), B


def _rk4_step(field: FieldFn, r, theta, phi, sigma, ds):
    k1r, k1t, k1p, B = _derivatives(field, r, theta, phi, sigma)
    k2r, k2t, k2p, _ = _derivatives(field, r + 0.5 * ds * k1r, theta + 0.5 * ds * k1t, phi + 0.5 * ds * k1p, sigma)
    k3r, k3t, k3p, _ = _derivatives(field, r + 0.5 * ds * k2r, theta + 0.5 * ds * k2t, phi + 0.5 * ds * k2p, sigma)
    k4r, k4t, k4p, _ = _derivatives(field, r + ds * k3r, theta + ds * k3t, phi + ds * k3p, sigma)
    return (r + ds / 6.0 * (k1r + 2 * k2r + 2 * k3r + k4r),
            theta + ds / 6.0 * (k1t + 2 * k2t + 2 * k3t + k4t),
            phi + ds / 6.0 * (k1p + 2 * k2p + 2 * k3p + k4p), B)


@dataclass(frozen=True)
class TraceResult:
    """Результат обхода силовых линий для набора точек (все массивы длины N)."""
    B_start_nT: np.ndarray        # |B| в исходной точке (полное IGRF)
    B0_nT: np.ndarray             # минимум |B| на линии — поле оболочки на магнитном экваторе
    L_ost: np.ndarray             # Д.29: L = ∛(0,311653/B0)
    B_over_B0: np.ndarray         # B_start / B0, по построению ≥ 1
    invariant_RE: np.ndarray      # I между зеркальными точками, R_E
    L_invariant: np.ndarray       # L из пары (I, B_m) по определению Мак-Илвейна
    r_eq_km: np.ndarray           # геоцентрическое расстояние точки минимума поля
    lat_eq_deg: np.ndarray        # геоцентрическая широта точки минимума
    arc_to_eq_km: np.ndarray      # дуга от исходной точки до минимума
    arc_total_km: np.ndarray      # дуга между зеркальными точками
    status: np.ndarray            # см. ниже
    steps: np.ndarray             # сделано шагов
    ds_km: float

    # 'ok'                  — найден и минимум поля, и сопряжённая зеркальная точка: годны L, B/B0 и I;
    # 'mirror_in_atmosphere'— минимум найден, но линия ушла ниже 100 км раньше, чем поле вернулось
    #                         к исходному: L и B/B0 годны, инварианта I нет (частица с такой
    #                         зеркальной точкой поглощается атмосферой — конус потерь);
    # 'no_minimum'          — минимума поля на линии не встретилось: не годно ничего.
    HAVE_L = ('ok', 'mirror_in_atmosphere')

    @property
    def ok(self) -> np.ndarray:
        """Точки, где годится пара (L, B/B0) — то, ради чего модуль написан."""
        return np.isin(self.status.astype(str), self.HAVE_L)

    @property
    def ok_invariant(self) -> np.ndarray:
        """Точки, где годится ещё и интегральный инвариант I."""
        return self.status.astype(str) == 'ok'


def trace_field_lines(field: FieldFn, r_km, theta, phi, *, ds_km: float = 20.0,
                      max_arc_km: float = 60000.0) -> TraceResult:
    """Обход силовых линий из N точек сразу: от точки в сторону убывания |B| до сопряжённой
    зеркальной точки (там |B| снова равен исходному).

    Одного прохода хватает на обе величины. Минимум |B| на линии лежит МЕЖДУ зеркальными
    точками, поэтому он встречается по дороге; инвариант I набирается на том же наборе узлов.

    Направление обхода выбирается пробным шагом в обе стороны: идём туда, где |B| убывает
    (σ из Д.26–Д.28 выбирается по полю, а не задаётся руками).
    """
    r = np.array(r_km, dtype=float).copy()
    th = np.array(theta, dtype=float).copy()
    ph = np.array(phi, dtype=float).copy()
    n = r.size
    max_steps = int(math.ceil(max_arc_km / ds_km))

    Bm = _derivatives(field, r, th, ph, 1.0)[3]          # |B| в исходной точке = поле зеркальной точки
    # выбор направления: пробный шаг вперёд и назад, идём в сторону меньшего |B|
    rp, tp, pp, _ = _rk4_step(field, r, th, ph, 1.0, ds_km)
    rm, tm, pm, _ = _rk4_step(field, r, th, ph, -1.0, ds_km)
    B_plus = _derivatives(field, rp, tp, pp, 1.0)[3]
    B_minus = _derivatives(field, rm, tm, pm, 1.0)[3]
    sigma = np.where(B_plus <= B_minus, 1.0, -1.0)

    B_hist = np.full((max_steps + 1, n), np.nan)
    B_hist[0] = Bm
    r_hist = np.full((max_steps + 1, n), np.nan)
    lat_hist = np.full((max_steps + 1, n), np.nan)
    r_hist[0], lat_hist[0] = r, 90.0 - np.degrees(th)
    active = np.ones(n, dtype=bool)
    steps = np.zeros(n, dtype=int)
    status = np.full(n, 'no_conjugate', dtype=object)

    for k in range(1, max_steps + 1):
        if not active.any():
            break
        idx = np.nonzero(active)[0]
        r_new, t_new, p_new, _ = _rk4_step(field, r[idx], th[idx], ph[idx], sigma[idx], ds_km)
        B_new = _derivatives(field, r_new, t_new, p_new, 1.0)[3]
        r[idx], th[idx], ph[idx] = r_new, t_new, p_new
        B_hist[k, idx] = B_new
        r_hist[k, idx] = r_new
        lat_hist[k, idx] = 90.0 - np.degrees(t_new)
        steps[idx] = k
        low = r_new < R_E_KM + MIN_ALT_KM
        done = (B_new >= Bm[idx]) & (k > 1)
        status[idx[low & ~done]] = 'mirror_in_atmosphere'
        status[idx[done]] = 'ok'
        active[idx[low | done]] = False

    # минимум поля на линии: узел с наименьшим |B| плюс параболическое уточнение по трём узлам
    with np.errstate(invalid='ignore'):
        j = np.nanargmin(np.where(np.isnan(B_hist), np.inf, B_hist), axis=0)
    rows = np.arange(n)
    B0 = B_hist[j, rows].copy()
    interior = (j > 0) & (j < steps)
    if interior.any():
        ii = np.nonzero(interior)[0]
        b_prev, b_mid, b_next = B_hist[j[ii] - 1, ii], B_hist[j[ii], ii], B_hist[j[ii] + 1, ii]
        denom = b_prev - 2.0 * b_mid + b_next
        good = denom > 0
        shift = np.where(good, 0.5 * (b_prev - b_next) / np.where(good, denom, 1.0), 0.0)
        B0[ii] = np.where(good, b_mid - 0.25 * (b_prev - b_next) * shift, b_mid)
    # Минимум поля обязан лежать ВНУТРИ пройденного отрезка: если наименьшее значение пришлось на
    # последний узел, поле всё ещё убывало и экватор не достигнут — тогда не годится ничего.
    # Исключение — точка, которая сама оказалась минимумом (j = 0, поле растёт в обе стороны).
    fell_to_end = (j >= steps) & (j > 0)
    status[fell_to_end] = 'no_minimum'
    status[(j == 0) & (steps == 0)] = 'no_minimum'

    invariant = np.full(n, np.nan)
    arc_total = np.full(n, np.nan)
    for i in range(n):
        if status[i] != 'ok':
            continue
        b = B_hist[:steps[i] + 1, i]
        invariant[i], arc_total[i] = _integral_invariant(b, Bm[i], ds_km)

    L_ost = np.cbrt(M_GAUSS_RE3 / (B0 / NT_PER_GAUSS))
    L_inv = l_from_invariant(invariant / R_E_KM, Bm / NT_PER_GAUSS)
    return TraceResult(B_start_nT=Bm, B0_nT=B0, L_ost=L_ost, B_over_B0=Bm / B0,
                       invariant_RE=invariant / R_E_KM, L_invariant=L_inv,
                       r_eq_km=r_hist[j, rows], lat_eq_deg=lat_hist[j, rows],
                       arc_to_eq_km=j * ds_km, arc_total_km=arc_total,
                       status=status, steps=steps, ds_km=ds_km)


def _integral_invariant(B: np.ndarray, Bm: float, ds_km: float) -> tuple[float, float]:
    """I = ∫ sqrt(1 − B/B_m) ds по узлам обхода, км; и длина дуги между зеркальными точками.

    Подынтегральная функция ведёт себя у концов как sqrt(расстояния до зеркальной точки), и
    обычная трапеция там теряет порядок. Поэтому оба крайних участка считаются аналитически
    в предположении линейного хода B: ∫₀^h sqrt(c·s) ds = (2/3)·h·sqrt(c·h). Последний шаг
    обрезается по линейно найденной точке B = B_m, а не по узлу сетки.
    """
    f = np.sqrt(np.clip(1.0 - B / Bm, 0.0, None))
    # последний узел уже за зеркальной точкой: линейно ищем, где B пересекает B_m
    if B[-1] >= Bm and len(B) >= 2:
        span = B[-1] - B[-2]
        frac = float(np.clip((Bm - B[-2]) / span, 0.0, 1.0)) if span > 0 else 1.0
        tail = 2.0 / 3.0 * f[-2] * frac * ds_km
        f = f[:-1]
        arc = (len(f) - 1) * ds_km + frac * ds_km
    else:
        tail, arc = 0.0, (len(f) - 1) * ds_km
    if len(f) < 3:
        return float(tail), float(arc)
    head = 2.0 / 3.0 * f[1] * ds_km                     # первый участок: B = B_m в самой точке
    middle = float(np.trapezoid(f[1:], dx=ds_km))
    return float(head + middle + tail), float(arc)


# ---------------------------------------------------------------------------
# L из интегрального инварианта: определение Мак-Илвейна, связь L(I, B_m) из самого диполя
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _dipole_invariant_curve(n_points: int = 600, n_nodes: int = 200):
    """Кривая Y(X) дипольного поля: X = I³·B_m/M, Y = L³·B_m/M, параметр — зеркальная широта.

    В дипольном поле обе величины НЕ зависят от L (длины ∝ L, поле ∝ L⁻³), поэтому кривая
    считается один раз на оболочке L = 1 в единицах M = 1:
        r(λ) = cos²λ,  B(λ) = √(1+3sin²λ)/cos⁶λ,  ds = cosλ·√(1+3sin²λ)·dλ,
        I = 2·∫₀^{λm} sqrt(1 − B(λ)/B_m)·cosλ·√(1+3sin²λ) dλ.
    Корневая особенность у λ = λm снимается заменой λ = λm − v²; по v интеграл гладкий и
    берётся формулой Гаусса—Лежандра.
    """
    lam_m = np.radians(np.linspace(0.05, 87.0, n_points))
    nodes, weights = np.polynomial.legendre.leggauss(n_nodes)
    X = np.empty(n_points)
    Y = np.empty(n_points)
    for i, lm in enumerate(lam_m):
        Bm = math.sqrt(1.0 + 3.0 * math.sin(lm) ** 2) / math.cos(lm) ** 6
        v_max = math.sqrt(lm)
        v = 0.5 * v_max * (nodes + 1.0)
        w = 0.5 * v_max * weights
        lam = lm - v * v
        B = np.sqrt(1.0 + 3.0 * np.sin(lam) ** 2) / np.cos(lam) ** 6
        integrand = np.sqrt(np.clip(1.0 - B / Bm, 0.0, None)) * np.cos(lam) * np.sqrt(1.0 + 3.0 * np.sin(lam) ** 2)
        X[i] = (2.0 * float(np.sum(w * integrand * 2.0 * v))) ** 3 * Bm
        Y[i] = Bm
    order = np.argsort(X)
    # Точный предел при I → 0 (зеркальная точка на самом экваторе): Y → 1, то есть L = ∛(M/B_m).
    # Он приписан к таблице явно, иначе левый край держал бы Y первой строки (λm = 0,05°),
    # и «нулевой инвариант» давал бы L с ошибкой ≈1e-6 — маленькой, но выдуманной.
    X = np.concatenate(([X[order][0] * 1e-9], X[order]))
    Y = np.concatenate(([1.0], Y[order]))
    return np.log(X), np.log(Y)


def l_from_invariant(I_RE, Bm_gauss) -> np.ndarray:
    """L по определению Мак-Илвейна из пары (I, B_m): L = ∛(M·Y(X)/B_m), X = I³·B_m/M.

    Ниже таблицы (I → 0, зеркальная точка сама на экваторе) берётся предел Y → 1, то есть
    L = ∛(M/B_m) — то же, что даёт Д.29 в точке минимума поля. Выше таблицы (зеркальная
    широта больше 87°, у трассы МКС не встречается) — NaN, а не край таблицы: молчаливый
    кламп дал бы ложное согласие.
    """
    I_RE = np.atleast_1d(np.asarray(I_RE, dtype=float))
    Bm_gauss = np.atleast_1d(np.asarray(Bm_gauss, dtype=float))
    logX_tab, logY_tab = _dipole_invariant_curve()
    X = I_RE ** 3 * Bm_gauss / M_GAUSS_RE3
    out = np.full(I_RE.shape, np.nan)
    inside = np.isfinite(X) & (X >= 0) & (np.log(np.where(X > 0, X, 1e-300)) <= logX_tab[-1])
    logX = np.log(np.where(X > 0, X, 1e-300))
    Y = np.exp(np.interp(logX[inside], logX_tab, logY_tab))   # np.interp сам держит левый предел
    out[inside] = np.cbrt(M_GAUSS_RE3 * Y / Bm_gauss[inside])
    return out


# ---------------------------------------------------------------------------
# Вход по точкам трассы
# ---------------------------------------------------------------------------

def traced_coordinates(points: Sequence, coeff_path: str, *, ds_km: float = 20.0,
                       when: Optional[datetime] = None) -> TraceResult:
    """(L, B/B0) трассировкой по полному IGRF для точек трассы (TrajectoryPoint или похожих).

    Позиция строится тем же переводом WGS84 (геодезические широта, долгота, высота) → ECEF,
    что и в рабочем модуле координат: сравнивать надо модели поля, а не две разные точки.
    Эпоха коэффициентов — как в рабочем модуле, одна на выборку (середина трассы), если не
    задана явно.
    """
    if not len(points):
        raise ValueError('пустая выборка точек')
    epoch = when if when is not None else points[len(points) // 2].t_utc
    ecef = np.array([geodetic_to_ecef_km(p.lat_deg, p.lon_deg, p.alt_km) for p in points])
    r = np.linalg.norm(ecef, axis=1)
    theta = np.arccos(np.clip(ecef[:, 2] / r, -1.0, 1.0))
    phi = np.arctan2(ecef[:, 1], ecef[:, 0])
    return trace_field_lines(igrf_field(str(coeff_path), epoch), r, theta, phi, ds_km=ds_km)
