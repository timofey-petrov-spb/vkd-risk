# -*- coding: utf-8 -*-
"""Техногенное вещество (космический мусор) по ГОСТ Р 25645.167-2005:
ожидаемое число попаданий в ту же опорную пластину 1 м², что и метеороидная
линия в vkd/assess/meteoroids.py.

Источник (номера — печатные пункты и таблицы стандарта, PDF в docs/istochniki/):
  * поток относительно КА Q_отн(h, i)_j, м⁻²·год⁻¹ — таблица 7.2, с. 15—16;
    эпоха t0 = 2000 г. (п. 5.1), сетка h = 200…1400 км, i = 55…105°, j = 1…8;
  * число попаданий P_j = C_N·S·Q_отн — формула (2), п. 5.7; для сферы C_N = 1,
    S = πD²/4, то есть S — площадь поперечного сечения;
  * среднее число столкновений N(t1,t2)_j = P_j·[F(t2) − F(t1)] — формула (6), п. 5.10;
  * функция прогноза F(t), годы — таблицы 8.1—8.16, с. 24—34; две гипотезы
    интенсивности образования КО: K = 1 (мер не принято) и K = 0,5 (п. 8.3);
  * разбиение размеров, средняя масса и плотность КО — таблица 5.1, с. 4;
  * средняя скорость возможных столкновений V_стл — таблица 7.1, с. 15.

Данные извлечены scripts/gost167_parse.py в data/gost167/ и проверены по
контрольной сумме. Три опечатки самого стандарта найдены структурными
проверками, сохранены «как напечатано» и исправлены явно — см. index.json,
ключ known_misprints. Ни одна из них не попадает в расчёт для орбиты МКС.

ОПОРНАЯ ГЕОМЕТРИЯ (согласована с метеороидной линией). ECSS даёт поток на
ОДНОСТОРОННЮЮ СЛУЧАЙНО КУВЫРКАЮЩУЮСЯ ПЛАСТИНУ площадью A, ГОСТ (п. 3.8) —
плотность потока через сферическую поверхность ЕДИНИЧНОГО СЕЧЕНИЯ. У выпуклого
тела средняя по ориентациям площадь проекции равна S_полн/4, у односторонней
пластины — A/4, у сферы — ровно её сечение. Значит пластина 1 м² метеороидной
линии эквивалентна сечению 0,25 м², и именно это сечение подставляется в
формулу (2) при C_N = 1. Коэффициент C_N для ОРИЕНТИРОВАННОЙ панели в стандарте
дан только графиками (рисунки 7.3—7.5) — растр, оцифровке не поддаётся, поэтому
направленный вариант не реализован и не объявляется.

НИЖНИЙ ПОРОГ ЧАСТИЦЫ. Стандарт отсекает КО по РАЗМЕРУ (0,1 см), метеороидная
линия — по МАССЕ (1e-3 г). Сведение — через собственную плотность стандарта из
таблицы 5.1 (2,5 г/см³ для j = 1) и шар: m = ρ·(π/6)·d³ = 1,309e-3 г, то есть
порог мусора в 1,31 раза тяжелее метеороидного. Для честного сравнения
метеороидную линию надо звать с m_min_g = reference_mass_g().

Допущение о шаре проверено по СОБСТВЕННЫМ средним массам стандарта: для
j = 1…4 обращение средней массы в диаметр даёт значение внутри своего же
диапазона размеров (для j = 1 — 0,187 см при границах 0,10…0,25 см), а вот для
j = 5…7 — заметно НИЖЕ нижней границы (1,80 см при границах 2,50…5,0 см и т. д.):
крупный мусор не компактен. Порог по массе строится только по j = 1, а диапазоны
j = 1…4 дают 99,9 % ответа, поэтому на число это не влияет, но общим правилом
допущение о шаре не является.

ЧЕГО МОДЕЛЬ НЕ ДАЁТ (объявляется, а не подменяется):
  * зависимости от НАПРАВЛЕНИЯ и от положения на витке: Q_отн(h, i) получена
    усреднением мгновенных значений по витку (п. 5.6), поэтому при равной высоте
    и длительности число попаданий ОДИНАКОВО у всех окон и окно им не выбирается;
  * вероятности пробоя скафандра: это ожидаемое число попаданий в опорную
    пластину, не повреждение и не попадание в космонавта;
  * года после 2025: область применения стандарта — 2000…2025 (п. 1);
  * наклонения ниже 55°: таблица 7.2 начинается с 55°, у МКС 51,6°.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .meteoroids import SEC_PER_YEAR, HOURS_PER_YEAR

MODEL_ID = 'gost-25645.167-2005-debris-v1'
ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'data' / 'gost167'
# Одна константа в коде -> index.json -> контрольные суммы всех CSV.
INDEX_SHA256 = 'ce229e98f4ee5255465084ef5e4dd714df0127966339935dcb3073996ddab868'

# Область применения, п. 1 стандарта.
SCOPE_ALT_KM = (200.0, 2000.0)
SCOPE_YEARS = (2000, 2025)
SCOPE_SIZE_CM_MIN = 0.1
# Сетка таблицы 7.2 — уже, чем область применения: Q_отн напечатана до 1400 км.
TABLE_ALT_KM = (200.0, 1400.0)
TABLE_INC_DEG = (55.0, 105.0)

# Одностороння случайно кувыркающаяся пластина: средняя площадь проекции = A/4.
# Сфера видна как своё сечение. Отсюда переход от площади пластины к S формулы (2).
PLATE_TO_CROSS_SECTION = 0.25

LIMITS_RU = (
    'ГОСТ Р 25645.167-2005: эпоха модели — 2000 г., прогноз доведён только до 2025 г., '
    'а распределение мусора меняется со временем (модель 2005 года не знает ни разрушения '
    'Фэнъюнь-1С 2007 г., ни столкновения Иридиум—Космос 2009 г., ни массовых группировок). '
    'Поток усреднён по витку и не зависит ни от направления, ни от положения на орбите, '
    'поэтому при равной высоте и длительности он ОДИНАКОВ у всех окон и окно им не выбирается. '
    'Это ожидаемое число попаданий в опорную пластину 1 м², а не вероятность пробоя скафандра '
    'и не попадание в космонавта. Наклонение МКС 51,6° лежит НИЖЕ таблицы (с 55°) — '
    'берётся край сетки. Коэффициент формы C_N для ориентированной панели в стандарте '
    'только графиками (рисунки 7.3—7.5), направленный расчёт не реализован.'
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=1)
def index() -> dict:
    """index.json с проверкой контрольной суммы — и его, и каждого CSV."""
    p = DATA / 'index.json'
    got = _sha256(p)
    if got != INDEX_SHA256:
        raise ValueError(
            'data/gost167/index.json изменился (%s != %s): пересоберите данные '
            'scripts/gost167_parse.py и обновите INDEX_SHA256' % (got, INDEX_SHA256))
    idx = json.loads(p.read_text(encoding='utf-8'))
    for entry in idx['files']:
        f = DATA / entry['file']
        if _sha256(f) != entry['sha256']:
            raise ValueError('data/gost167/%s не совпадает с контрольной суммой из index.json'
                             % entry['file'])
    return idx


def _rows(name: str) -> list[dict]:
    index()          # проверка контрольных сумм до первого чтения
    with io.open(DATA / name, encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


def _val(row: dict, field_name: str) -> float:
    """Исправленное значение, если опечатка объявлена, иначе напечатанное."""
    c = row.get('corrected', '')
    return float(c) if c not in ('', None) else float(row[field_name])


@lru_cache(maxsize=1)
def size_ranges() -> tuple[dict, ...]:
    """Таблица 5.1: границы размеров, средняя масса и плотность по диапазонам j."""
    out = []
    for r in _rows('table_5_1_size_ranges.csv'):
        out.append({
            'j': int(r['j']),
            'size_min_cm': float(r['size_min_cm']),
            'size_max_cm': None if r['size_max_cm'] == '' else float(r['size_max_cm']),
            'mean_mass_kg': float(r['mean_mass_kg']),
            'density_g_cm3': float(r['density_g_cm3']),
        })
    if len(out) != 8:
        raise ValueError('таблица 5.1: ожидалось 8 диапазонов размеров')
    return tuple(out)


@lru_cache(maxsize=1)
def _flux_grid() -> tuple[dict, tuple, tuple]:
    grid, incs, alts = {}, set(), set()
    for r in _rows('table_7_2_flux.csv'):
        inc, j, alt = float(r['inclination_deg']), int(r['j']), float(r['alt_km'])
        grid[(inc, j, alt)] = _val(r, 'flux_m2_yr')
        incs.add(inc)
        alts.add(alt)
    return grid, tuple(sorted(incs)), tuple(sorted(alts))


@lru_cache(maxsize=1)
def _forecast_grid() -> tuple[dict, tuple]:
    grid, alts = {}, set()
    for r in _rows('table_8_forecast.csv'):
        key = (int(r['j']), float(r['K']), int(r['year']), float(r['alt_km']))
        grid[key] = _val(r, 'F_years')
        alts.add(float(r['alt_km']))
    return grid, tuple(sorted(alts))


@lru_cache(maxsize=1)
def _velocity_grid() -> tuple[dict, tuple]:
    grid, alts = {}, set()
    for r in _rows('table_7_1_collision_velocity.csv'):
        grid[(float(r['inclination_deg']), float(r['alt_km']))] = float(r['v_collision_km_s'])
        alts.add(float(r['alt_km']))
    return grid, tuple(sorted(alts))


def reference_mass_g(j: int = 1) -> float:
    """Нижний порог диапазона j, переведённый в массу шара по СОБСТВЕННОЙ плотности
    стандарта (таблица 5.1): m = ρ·(π/6)·d³. Для j = 1 даёт 1,309·10⁻³ г.
    Этим значением надо звать метеороидную линию, чтобы сравнение было честным."""
    row = size_ranges()[j - 1]
    d_cm = row['size_min_cm']
    return row['density_g_cm3'] * (math.pi / 6.0) * d_cm ** 3


def _bracket(xs: tuple, x: float) -> tuple[float, float, float]:
    """Соседние узлы сетки и вес. Вне сетки — ошибка, клампа здесь нет."""
    if x < xs[0] or x > xs[-1]:
        raise ValueError('значение %g вне сетки %g…%g' % (x, xs[0], xs[-1]))
    for a, b in zip(xs, xs[1:]):
        if a <= x <= b:
            return a, b, (0.0 if b == a else (x - a) / (b - a))
    return xs[-1], xs[-1], 0.0


def _interp(v0: float, v1: float, w: float, mode: str) -> float:
    if mode == 'linear':
        return v0 + w * (v1 - v0)
    if mode == 'log':
        if v0 <= 0 or v1 <= 0:
            raise ValueError('логарифмическая интерполяция требует положительных значений')
        return math.exp(math.log(v0) + w * (math.log(v1) - math.log(v0)))
    raise ValueError('неизвестный режим интерполяции %r' % mode)


def flux_by_j(alt_km: float, inclination_deg: float, *, interp: str = 'log') -> dict[int, float]:
    """Q_отн(h, i)_j на эпоху 2000 г., м⁻²·год⁻¹, по каждому диапазону размеров.

    Интерполяция по высоте — между узлами 200 км: 'log' (по умолчанию, поток в
    этом диапазоне растёт почти экспоненциально) или 'linear'. По наклонению —
    линейная между узлами 10°. Наклонение ВНЕ 55…105° сюда не передают:
    край сетки выбирает вызывающий и обязан это объявить."""
    grid, incs, alts = _flux_grid()
    i0, i1, wi = _bracket(incs, inclination_deg)
    h0, h1, wh = _bracket(alts, alt_km)
    out = {}
    for j in range(1, 9):
        # сначала по высоте на каждом узле наклонения, затем линейно по наклонению
        a = _interp(grid[(i0, j, h0)], grid[(i0, j, h1)], wh, interp)
        b = _interp(grid[(i1, j, h0)], grid[(i1, j, h1)], wh, interp)
        out[j] = a + wi * (b - a)
    return out


def growth_by_j(year: int, alt_km: float, *, K: float = 1.0) -> tuple[dict[int, float], bool]:
    """Годовой прирост dF/dt = F(год) − F(год−1) по каждому j — во сколько раз поток
    в этот год отличается от эпохи 2000 г. Возвращает (значения, признак экстраполяции).

    За пределами 2000…2025 берётся последний известный прирост (2025 − 2024), и
    признак экстраполяции поднимается: стандарт дальше 2025 г. не считает."""
    grid, alts = _forecast_grid()
    y = int(year)
    extrapolated = not (SCOPE_YEARS[0] < y <= SCOPE_YEARS[1])
    y_use = min(max(y, SCOPE_YEARS[0] + 1), SCOPE_YEARS[1])
    h0, h1, wh = _bracket(alts, alt_km)
    out = {}
    for j in range(1, 9):
        d0 = grid[(j, K, y_use, h0)] - grid[(j, K, y_use - 1, h0)]
        d1 = grid[(j, K, y_use, h1)] - grid[(j, K, y_use - 1, h1)]
        out[j] = d0 + wh * (d1 - d0)     # прирост по высоте — линейно, он гладкий
    return out, extrapolated


def collision_velocity_km_s(alt_km: float, inclination_deg: float) -> float:
    """Таблица 7.1: средняя скорость возможных столкновений, км/с. Справочно —
    в число попаданий не входит, но задаёт масштаб удара."""
    grid, alts = _velocity_grid()
    _, incs, _ = _flux_grid()
    i0, i1, wi = _bracket(incs, inclination_deg)
    h0, h1, wh = _bracket(alts, alt_km)
    a = grid[(i0, h0)] + wh * (grid[(i0, h1)] - grid[(i0, h0)])
    b = grid[(i1, h0)] + wh * (grid[(i1, h1)] - grid[(i1, h0)])
    return a + wi * (b - a)


@dataclass(frozen=True)
class DebrisResult:
    N: float                        # ожидаемое число попаданий, безразмерно
    N_by_j: dict                    # вклад каждого диапазона размеров
    flux_2000_per_m2_yr: float      # Q_отн, сумма по j, эпоха 2000 г.
    flux_epoch_per_m2_yr: float     # то же, умноженное на прирост F(t) года окна
    growth_factor: float            # эффективный множитель года, взвешенный по потоку
    area_m2: float                  # площадь опорной пластины
    cross_section_m2: float         # она же в терминах формулы (2): A/4
    duration_h: float
    year: int
    K: float
    size_min_cm: float
    mass_min_g: float               # порог по массе, эквивалентный size_min_cm
    inclination_deg: float          # запрошенное
    inclination_used_deg: float     # фактически взятое из сетки
    inclination_clamped: bool
    alt_km_min: float
    alt_km_max: float
    year_extrapolated: bool
    interp: str
    v_collision_km_s: float
    p_at_least_one: float
    rule: str
    limits_ru: str = LIMITS_RU


def _prepare(alt_km_seq, inclination_deg, interp):
    lo, hi = min(alt_km_seq), max(alt_km_seq)
    if lo < TABLE_ALT_KM[0] or hi > TABLE_ALT_KM[1]:
        raise ValueError(
            'высота %g…%g км вне таблицы 7.2 ГОСТ Р 25645.167 (%g…%g км); область '
            'применения стандарта шире (до 2000 км), но Q_отн напечатана только до 1400 км'
            % (lo, hi, TABLE_ALT_KM[0], TABLE_ALT_KM[1]))
    if interp not in ('log', 'linear'):
        raise ValueError('interp должен быть "log" или "linear", получено %r' % interp)
    used = min(max(float(inclination_deg), TABLE_INC_DEG[0]), TABLE_INC_DEG[1])
    clamped = used != float(inclination_deg)
    return lo, hi, used, clamped


def _rule(inc_req, inc_used, clamped, year, extrapolated, K, interp, area_m2, lo, hi):
    parts = [
        'ГОСТ Р 25645.167-2005: Q_отн(h, i)_j таблица 7.2 (эпоха 2000 г.) × сечение %.3f м² '
        '(формула (2), C_N = 1) × прирост F(t) таблиц 8.1—8.16'
        % (area_m2 * PLATE_TO_CROSS_SECTION),
        'сумма по восьми диапазонам размеров j (КО крупнее %.1f см)' % SCOPE_SIZE_CM_MIN,
        'гипотеза K = %g' % K,
        'высота трассы %.0f…%.0f км, интерполяция по высоте — %s'
        % (lo, hi, 'логарифмическая' if interp == 'log' else 'линейная'),
        'опорная пластина %g м², односторонняя случайно кувыркающаяся' % area_m2,
    ]
    if clamped:
        parts.append('наклонение %.1f° ВНЕ таблицы (55…105°), взят край сетки %.0f°'
                     % (inc_req, inc_used))
    else:
        parts.append('наклонение %.1f°' % inc_used)
    if extrapolated:
        parts.append('год %d ВНЕ прогноза стандарта (2000…2025), взят прирост 2025 г.' % year)
    parts.append('поток усреднён по витку: от направления и от положения на орбите не зависит')
    return '; '.join(parts)


def debris_hits_track(times_utc, alts_km, inclination_deg: float, area_m2: float = 1.0, *,
                      K: float = 1.0, interp: str = 'log', year: int | None = None) -> DebrisResult:
    """Ожидаемое число попаданий КО за окно — интегралом по фактической трассе,
    тем же способом, что и метеороиды: поток считается по ФАКТИЧЕСКОЙ высоте
    каждой точки, интеграл трапециями по фактическим dt, оба конца окна включены.

    times_utc — возрастающая последовательность datetime; alts_km — высоты тех же
    точек; inclination_deg — наклонение орбиты; area_m2 — площадь опорной пластины.
    """
    if len(times_utc) < 2 or len(times_utc) != len(alts_km):
        raise ValueError('нужны минимум две точки трассы с высотами')
    t = [(x - times_utc[0]).total_seconds() for x in times_utc]
    if any(b <= a for a, b in zip(t, t[1:])):
        raise ValueError('времена трассы должны строго возрастать')
    lo, hi, inc_used, clamped = _prepare(alts_km, inclination_deg, interp)
    if year is None:
        # год середины окна: прирост F(t) в пределах окна постоянен
        year = times_utc[len(times_utc) // 2].year

    per_point = []          # поток на эпоху года окна, м⁻²·год⁻¹, по точкам
    per_point_2000 = []
    by_j_int = {j: 0.0 for j in range(1, 9)}
    extrapolated = False
    cache = {}
    for h in alts_km:
        if h not in cache:
            q = flux_by_j(h, inc_used, interp=interp)
            g, ex = growth_by_j(year, h, K=K)
            extrapolated = extrapolated or ex
            cache[h] = ({j: q[j] * g[j] for j in q}, sum(q.values()))
        per_point.append(cache[h][0])
        per_point_2000.append(cache[h][1])

    cross = area_m2 * PLATE_TO_CROSS_SECTION
    for j in range(1, 9):
        integral = sum(0.5 * (per_point[i][j] + per_point[i + 1][j]) * (t[i + 1] - t[i])
                       for i in range(len(t) - 1))          # (1/(м²·год))·с
        by_j_int[j] = cross * integral / SEC_PER_YEAR
    N = sum(by_j_int.values())

    flux_2000 = sum(0.5 * (per_point_2000[i] + per_point_2000[i + 1]) * (t[i + 1] - t[i])
                    for i in range(len(t) - 1)) / t[-1]
    flux_epoch = N * SEC_PER_YEAR / (cross * t[-1]) if t[-1] > 0 else 0.0
    return DebrisResult(
        N=N, N_by_j=by_j_int, flux_2000_per_m2_yr=flux_2000, flux_epoch_per_m2_yr=flux_epoch,
        growth_factor=(flux_epoch / flux_2000 if flux_2000 > 0 else 0.0),
        area_m2=area_m2, cross_section_m2=cross, duration_h=t[-1] / 3600.0, year=int(year), K=K,
        size_min_cm=SCOPE_SIZE_CM_MIN, mass_min_g=reference_mass_g(1),
        inclination_deg=float(inclination_deg), inclination_used_deg=inc_used,
        inclination_clamped=clamped, alt_km_min=lo, alt_km_max=hi,
        year_extrapolated=extrapolated, interp=interp,
        v_collision_km_s=collision_velocity_km_s(max(lo, 400.0), inc_used),
        p_at_least_one=1.0 - math.exp(-N),
        rule=_rule(inclination_deg, inc_used, clamped, year, extrapolated, K, interp,
                   area_m2, lo, hi),
    )


def debris_hits(alt_km: float, inclination_deg: float, area_m2: float, duration_h: float, *,
                K: float = 1.0, interp: str = 'log', year: int = 2025) -> DebrisResult:
    """Постоянная высота и длительность — без трассы. Формулы (2) и (6) напрямую."""
    lo, hi, inc_used, clamped = _prepare([alt_km], inclination_deg, interp)
    q = flux_by_j(alt_km, inc_used, interp=interp)
    g, extrapolated = growth_by_j(year, alt_km, K=K)
    cross = area_m2 * PLATE_TO_CROSS_SECTION
    by_j = {j: q[j] * g[j] * cross * duration_h / HOURS_PER_YEAR for j in q}
    N = sum(by_j.values())
    flux_2000 = sum(q.values())
    flux_epoch = sum(q[j] * g[j] for j in q)
    return DebrisResult(
        N=N, N_by_j=by_j, flux_2000_per_m2_yr=flux_2000, flux_epoch_per_m2_yr=flux_epoch,
        growth_factor=(flux_epoch / flux_2000 if flux_2000 > 0 else 0.0),
        area_m2=area_m2, cross_section_m2=cross, duration_h=duration_h, year=int(year), K=K,
        size_min_cm=SCOPE_SIZE_CM_MIN, mass_min_g=reference_mass_g(1),
        inclination_deg=float(inclination_deg), inclination_used_deg=inc_used,
        inclination_clamped=clamped, alt_km_min=lo, alt_km_max=hi,
        year_extrapolated=extrapolated, interp=interp,
        v_collision_km_s=collision_velocity_km_s(max(alt_km, 400.0), inc_used),
        p_at_least_one=1.0 - math.exp(-N),
        rule=_rule(inclination_deg, inc_used, clamped, year, extrapolated, K, interp,
                   area_m2, lo, hi),
    )
