# -*- coding: utf-8 -*-
"""Таблица Ж.1 ОСТ 134-1044-2007: вертикальная жёсткость геомагнитного обрезания R0c.

Зачем. Раньше обрезание считалось только вертикальной формулой Штёрмера на ЦЕНТРАЛЬНОМ
наклонённом диполе (vkd/orbit/magnetic.py). Центральный диполь симметричен по широте:
на +50° и на −50° он даёт одно и то же значение. Настоящее поле не симметрично — над
Южной Атлантикой оно ослаблено, и обрезание там существенно ниже. Замер 19.09 на 450 км,
эпоха 2010, по двенадцати долготам таблицы:

    широта   Ж.1, мин..макс ГВ   диполь, мин..макс ГВ   отношение по минимуму
      +55      0,39 .. 2,71         0,42 .. 3,08                1,07
      +50      0,74 .. 4,10         0,81 .. 4,25                1,09
      −50      0,49 .. 5,44         0,81 .. 4,25                1,66
      −55      0,19 .. 3,92         0,42 .. 3,08                2,22

В северном полушарии формула сходится с таблицей в пределах 10 %, в южном завышает
минимум в 1,7–2,2 раза. Следствие на экране было видно прямо: жёсткость протона
100 МэВ равна 0,445 ГВ, дипольный минимум на южной кромке трассы МКС — около 0,71 ГВ,
поэтому фактор «минут доступности протонов ≥100 МэВ» был тождественно нулевым и в
тихий день, и в бурю. Это артефакт симметричного диполя, а не физика.

Что здесь есть. Загрузка таблицы, билинейная интерполяция по широте и долготе
(по долготе — с замыканием круга: 330° соседствует с 0°) и пересчёт на высоту точки
по формуле Ж.3 ОСТ. Коррекция по Kp и местному времени (Ж.4–Ж.6) НЕ реализована:
это отдельная работа, и её отсутствие объявлено в ограничениях — обрезание считается
для спокойных условий, во время бури оно СНИЖАЕТСЯ, то есть оценка консервативна
в сторону «частица не проходит».

Границы применимости — в константах ниже и в ограничениях, которые печатает
vkd/orbit/trajectory.py:
  * высота таблицы H0 = 450 км, эпоха 2010, сетка 5° по широте (+85…−85) и 30° по долготе;
  * вертикальное направление падения; направленная жёсткость не считается. Сам ОСТ
    (прил. Ж, примечание) объявляет, что замена точного расчёта вертикальной R0c даёт
    ошибку функции проникновения до ≈10 %;
  * |широта| > 85° — вне сетки, значения там нет; запасной путь — дипольная формула.

Долгота. Таблица размечена 0…330° на восток; skyfield отдаёт −180…180°, поэтому
долгота приводится к [0, 360) взятием остатка. Широта и долгота точки — геодезические
(WGS84, как их отдаёт A3); широта таблицы в стандарте не уточнена, а разница
геодезической и геоцентрической широты не превышает 0,19° — это меньше двадцатой доли
шага сетки 5°, и на интерполяцию не влияет.

Высота (Ж.3). Rc(H) = R0c · ((R_З + H0) / r)², где r — расстояние от центра Земли.
Закон 1/r² — это сам штёрмеровский множитель, и он совпадает с зависимостью, которую
даёт наш же дипольный расчёт (проверено тестом). В качестве r берётся ГЕОЦЕНТРИЧЕСКОЕ
расстояние точки (модуль вектора ECEF), а не R_З + h: высота A3 геодезическая, и на южной
кромке трассы МКС (широта 51,79°, высота 418,6 км — замер 19.09) сферическая подстановка
даёт 6789,6 км против настоящих 6783,6, то есть ошибается на 6 км и на 0,18 % в значении
жёсткости. Каким именно радиусом пользовался составитель таблицы для своей
высоты 450 км, стандарт не уточняет; неоднозначность того же порядка 0,2 % и она
объявлена, а не спрятана.
"""
from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

# Относительный путь таблицы в репозитории; разбор — scripts/ost_parse_cutoff.py
TABLE_RELATIVE_PATH = 'data/ost1044_cutoff/Zh_1_R0c_GV_h450km_epoch2010.csv'
TABLE_ALTITUDE_KM = 450.0          # H0 таблицы Ж.1
TABLE_EPOCH_YEAR = 2010            # эпоха таблицы Ж.1
EARTH_RADIUS_KM = 6371.0           # R_З формулы Ж.3 (средний радиус Земли)
LAT_STEP_DEG = 5.0
LON_STEP_DEG = 30.0
LAT_LIMIT_DEG = 85.0               # крайние строки таблицы: +85 и −85
TABLE_REFERENCE_RADIUS_KM = EARTH_RADIUS_KM + TABLE_ALTITUDE_KM

# Порядок ошибки от разницы эпох. Таблица — 2010, расчёт — 2024 (архив) и 2026 (текущий режим),
# пересчёта эпохи в стандарте нет. Прямо измерить сдвиг НЕДИПОЛЬНОЙ картины нечем: таблица
# существует только на одну эпоху. Измеримо другое — насколько за те же годы уезжает наша
# собственная дипольная формула при замене коэффициентов IGRF 2010 → 2024 на широтах трассы МКС
# (±50…±51,6°, 36 долгот, высота 450 км). Взята медиана и крайние значения по точкам: среднее
# здесь бессмысленно, отдельные точки уходят в обе стороны. Это оценка ПОРЯДКА, а не граница
# погрешности, и она относится только к дипольной части. Числа воспроизводятся тестом
# tests/test_cutoff_zh1.py::test_poryadok_oshibki_ot_raznicy_epoh_vosproizvodim.
EPOCH_DIPOLE_SHIFT_MEDIAN_PCT = -1.9
EPOCH_DIPOLE_SHIFT_MIN_PCT = -5.5
EPOCH_DIPOLE_SHIFT_MAX_PCT = +9.9


class CutoffTableError(Exception):
    """Таблица есть, но она не та, что объявлена: сетка, размер или значения не сходятся."""


@dataclass(frozen=True)
class CutoffTable:
    lat_deg: np.ndarray            # по возрастанию: −85 … +85
    lon_deg: np.ndarray            # 0 … 330
    values_GV: np.ndarray          # [широта, долгота], ГВ на высоте H0
    path: str
    sha256: str

    @property
    def description_ru(self) -> str:
        return ('таблица Ж.1 ОСТ 134-1044-2007: вертикальная жёсткость обрезания R0c, ГВ, '
                'высота %g км, эпоха %d, сетка %g° по широте (%+g…%+g) и %g° по долготе'
                % (TABLE_ALTITUDE_KM, TABLE_EPOCH_YEAR, LAT_STEP_DEG,
                   self.lat_deg[-1], self.lat_deg[0], LON_STEP_DEG))


def _read_rows(path: Path) -> tuple[list[int], list[tuple[int, list[float]]]]:
    with io.open(path, encoding='utf-8', newline='') as fh:
        rows = list(csv.reader(fh))
    if not rows:
        raise CutoffTableError('таблица Ж.1 пуста: %s' % path)
    header = rows[0]
    if not header or header[0] != 'lat_deg':
        raise CutoffTableError('в таблице Ж.1 нет столбца широты: %s' % path)
    try:
        lons = [int(name.rsplit('_', 1)[-1]) for name in header[1:]]
        body = [(int(r[0]), [float(v) for v in r[1:]]) for r in rows[1:] if r]
    except ValueError as exc:
        raise CutoffTableError('нечисловое значение в таблице Ж.1 (%s): %s' % (path, exc)) from exc
    return lons, body


@lru_cache(maxsize=4)
def _load(path_str: str, content_sha256: str) -> CutoffTable:
    path = Path(path_str)
    lons, body = _read_rows(path)
    expected_lons = list(range(0, 360, int(LON_STEP_DEG)))
    if lons != expected_lons:
        raise CutoffTableError('сетка долгот таблицы Ж.1 не %s, а %s' % (expected_lons, lons))
    lats = [lat for lat, _ in body]
    expected_lats = list(range(int(LAT_LIMIT_DEG), -int(LAT_LIMIT_DEG) - 1, -int(LAT_STEP_DEG)))
    if lats != expected_lats:
        raise CutoffTableError('сетка широт таблицы Ж.1 не %+d…%+d с шагом %d, а %s'
                               % (expected_lats[0], expected_lats[-1], int(LAT_STEP_DEG), lats))
    grid = np.array([v for _, v in body], dtype=float)
    if grid.shape != (len(expected_lats), len(expected_lons)):
        raise CutoffTableError('таблица Ж.1 имеет размер %s вместо %s'
                               % (grid.shape, (len(expected_lats), len(expected_lons))))
    if not np.isfinite(grid).all() or (grid < 0).any():
        raise CutoffTableError('в таблице Ж.1 есть отрицательные или нечисловые значения')
    # строки идут от +85 к −85; для интерполяции нужен возрастающий порядок широт
    return CutoffTable(np.array(expected_lats, dtype=float)[::-1], np.array(expected_lons, dtype=float),
                       grid[::-1, :], str(path), content_sha256)


def load_table(path: str | Path) -> CutoffTable:
    """Таблица из файла. Файла нет — FileNotFoundError (вызывающий уходит на запасной путь);
    файл есть, но не совпадает с объявленной сеткой — CutoffTableError, без тихого умолчания."""
    p = Path(path)
    raw = p.read_bytes()                      # FileNotFoundError наружу: это ожидаемый случай
    return _load(str(p), hashlib.sha256(raw).hexdigest())


def vertical_cutoff_GV(table: CutoffTable, lat_deg: np.ndarray, lon_deg: np.ndarray,
                       radius_km: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Вертикальная жёсткость обрезания по Ж.1, ГВ, и признак «точка внутри сетки».

    Интерполяция билинейная: по широте — между соседними строками сетки 5°, по долготе —
    между соседними столбцами 30° с замыканием круга (330° соседствует с 0°). Долгота
    приводится к [0, 360). Вне широт таблицы (|широта| > 85°) значение не выдаётся:
    там NaN и False, а не экстраполяция.

    radius_km — геоцентрическое расстояние точки; если задано, применяется пересчёт на
    высоту по Ж.3: Rc = R0c · ((R_З + H0) / r)². Без него возвращается табличное значение
    на высоте 450 км.
    """
    lat = np.atleast_1d(np.asarray(lat_deg, dtype=float))
    lon = np.atleast_1d(np.asarray(lon_deg, dtype=float))
    if lat.shape != lon.shape:
        raise ValueError('широта и долгота должны быть одной длины')
    inside = np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) <= LAT_LIMIT_DEG)
    out = np.full(lat.shape, np.nan)
    if inside.any():
        la, lo = lat[inside], np.mod(lon[inside], 360.0)
        # индексы левого нижнего узла и доли внутри ячейки
        fi = (la - table.lat_deg[0]) / LAT_STEP_DEG
        i0 = np.clip(np.floor(fi).astype(int), 0, len(table.lat_deg) - 2)
        wi = fi - i0
        fj = lo / LON_STEP_DEG
        j0 = np.clip(np.floor(fj).astype(int), 0, len(table.lon_deg) - 1)
        wj = fj - j0
        j1 = (j0 + 1) % len(table.lon_deg)     # замыкание круга: после 330° идёт 0°
        g = table.values_GV
        out[inside] = ((1 - wi) * ((1 - wj) * g[i0, j0] + wj * g[i0, j1])
                       + wi * ((1 - wj) * g[i0 + 1, j0] + wj * g[i0 + 1, j1]))
    if radius_km is not None:
        r = np.atleast_1d(np.asarray(radius_km, dtype=float))
        if r.shape != lat.shape:
            raise ValueError('радиус должен быть той же длины, что широта')
        good = inside & np.isfinite(r) & (r > 0)
        out[inside & ~good] = np.nan
        inside = good
        out[good] *= (TABLE_REFERENCE_RADIUS_KM / r[good]) ** 2
    return out, inside
