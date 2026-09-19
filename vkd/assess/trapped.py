# -*- coding: utf-8 -*-
"""Захваченные протоны по приложению А ОСТ 134-1044-2007: поток вдоль траектории.

Источник данных: data/ost1044_belts/A_2_1.csv (минимум СА) и A_2_2.csv
(максимум СА) — дифференциальная плотность ВСЕНАПРАВЛЕННОГО потока,
см⁻²·с⁻¹·МэВ⁻¹ (вводный текст прил. А: «плотность потока ЗЧ приводится
в единицах [см-2·с-1·МэВ-1] … спектры всенаправленного потока»;
CONTRACT v3.1, R3 — без произвольного 4π), как функция (L, B/B0) и энергии.
Извлечено из текста стандарта, сверено построчно (data/ost1044_belts/README.md).

Единицы результата: интегральный всенаправленный поток выше e_min —
см⁻²·с⁻¹; флюенс за окно (в compare) — част./см². Никаких «на стерадиан».

Метод:
  * по L — интерполяция между соседними табличными оболочками В ЛОГАРИФМЕ
    ПОТОКА: между узлами 1,20 и 1,30 интегральный поток ≥30 МэВ меняется
    в ~445 раз, и линейная интерполяция завышала середину в ~24 раза
    (найдено разбором 19.09); при нулевом узле — линейно;
    вне диапазона таблицы — статус «нет модели», не ноль;
  * по B/B0 — та же логарифмическая интерполяция внутри оболочки; выше
    максимального табличного B/B0 — физический ноль (точка отражения выше),
    статус «за зеркальной точкой»; B/B0 < 1 (несовместимость дипольной L
    и поля IGRF) не заменяется экваториальным значением: поток отсутствует, статус inconsistent_BB0;
  * по энергии — интегрирование ПО СТЕПЕННОМУ ЗАКОНУ между узлами (log-log),
    точное для спектров вида a·E^b на логарифмической сетке; обычные трапеции
    на сетке ОСТ завышают интеграл ∝E⁻² на ~30 %. Хвост выше последнего узла
    ОТБРАСЫВАЕТСЯ, и это объявляется в результате.
"""
from __future__ import annotations

import csv
import hashlib
import io
import math
import os
from dataclasses import dataclass
from typing import Optional

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DATA = os.path.join(_ROOT, 'data', 'ost1044_belts')
FLUX_UNIT_RU = 'см⁻²·с⁻¹, всенаправленный (ОСТ 134-1044-2007, прил. А, вводный текст)'
# та же единица там, где стандарт уже назван в той же фразе: иначе «ОСТ 134-1044-2007» стоит
# в одном предложении дважды (карточка флюенса, пятый круг). Без стандарта рядом печатать нельзя —
# происхождение единицы должно оставаться прослеживаемым.
FLUX_UNIT_SHORT_RU = 'см⁻²·с⁻¹, всенаправленный (прил. А, вводный текст)'


@dataclass(frozen=True)
class FluxResult:
    value_per_cm2_s: Optional[float]   # интегральный всенаправленный поток выше e_min, см⁻²·с⁻¹; None = нет модели
    status: str                        # "ok" | "no_model_L" | "beyond_mirror" | "inconsistent_BB0"
    e_min_MeV: float
    e_max_MeV: float                   # последний узел таблицы: хвост выше отброшен


def _log_mix(a: np.ndarray, b: np.ndarray, w: float) -> np.ndarray:
    """(1−w)·a ⊕ w·b в логарифме там, где оба узла положительны; иначе линейно."""
    both = (a > 0) & (b > 0)
    out = a * (1 - w) + b * w
    with np.errstate(divide='ignore'):
        out = np.where(both, np.exp((1 - w) * np.log(np.where(both, a, 1.0)) + w * np.log(np.where(both, b, 1.0))), out)
    return out


class BeltTable:
    def __init__(self, solar_activity: str = 'min'):
        fn = 'A_2_1.csv' if solar_activity == 'min' else 'A_2_2.csv'
        path = os.path.join(_DATA, fn)
        raw = open(path, 'rb').read()
        rows = list(csv.reader(io.StringIO(raw.decode('utf-8'))))
        self.energies_MeV = np.array([float(h.replace('E_', '').replace('_MeV', '')) for h in rows[0][2:]])
        self.table: dict[float, list[tuple[float, np.ndarray]]] = {}
        for r in rows[1:]:
            L, bb = float(r[0]), float(r[1])
            self.table.setdefault(L, []).append((bb, np.array([float(x) for x in r[2:]])))
        for L in self.table:
            self.table[L].sort(key=lambda p: p[0])
        self.Ls = np.array(sorted(self.table))
        self.table_name = 'А.2.1' if solar_activity == 'min' else 'А.2.2'
        self.file = 'data/ost1044_belts/' + fn
        self.sha256 = hashlib.sha256(raw).hexdigest()
        # идентификатор записи таблицы для прослеживаемости фактора до файла (Т1/Т2): имя + хеш содержимого
        self.raw_record_id = 'ost1044_A_%s:%s' % (self.table_name.replace('.', '_'), self.sha256[:12])
        self.source = 'ОСТ 134-1044-2007, прил. А, табл. %s' % self.table_name
        self.flux_unit_ru = FLUX_UNIT_RU
        self.flux_unit_short_ru = FLUX_UNIT_SHORT_RU
        self.interpolation_ru = ('по L и B/B0 — в логарифме потока между узлами таблицы (линейно только при нулевом узле); '
                                 'по энергии — степенной закон между узлами, хвост выше %g МэВ отброшен' % self.energies_MeV[-1])

    def _spectrum_at(self, L_row: float, B_over_B0: float) -> Optional[np.ndarray]:
        """Спектр на табличной оболочке при данном B/B0; None — за зеркальной точкой."""
        pts = self.table[L_row]
        bbs = np.array([p[0] for p in pts])
        if B_over_B0 > bbs[-1]:
            return None
        j = int(np.searchsorted(bbs, B_over_B0))
        if j == 0:
            return pts[0][1]
        b0, b1 = bbs[j - 1], bbs[j]
        w = 0.0 if b1 == b0 else (B_over_B0 - b0) / (b1 - b0)
        return _log_mix(pts[j - 1][1], pts[j][1], w)

    def integral_flux(self, L: Optional[float], B_over_B0: Optional[float], e_min_MeV: float) -> FluxResult:
        e_max = float(self.energies_MeV[-1])
        if L is None or B_over_B0 is None or L < self.Ls[0] or L > self.Ls[-1]:
            return FluxResult(None, 'no_model_L', e_min_MeV, e_max)
        status = 'ok'
        if B_over_B0 < 1.0:
            return FluxResult(None, 'inconsistent_BB0', e_min_MeV, e_max)
        j = int(np.searchsorted(self.Ls, L))
        if j == 0 or self.Ls[j - 1] == L:
            rows = [(self.Ls[max(j - 1, 0)] if self.Ls[max(j - 1, 0)] == L else self.Ls[j], 1.0)]
        else:
            L0, L1 = self.Ls[j - 1], self.Ls[j]
            w = (L - L0) / (L1 - L0)
            rows = [(L0, 1 - w), (L1, w)]
        # интеграл по энергии на каждой оболочке, затем смешивание в логарифме (степенной закон по L
        # между узлами); если на одной из оболочек точка за зеркальной — вклад этой оболочки нулевой
        vals = []
        for L_row, wt in rows:
            spec = self._spectrum_at(float(L_row), B_over_B0)
            vals.append(None if spec is None else (integrate_power_law(self.energies_MeV, spec, e_min_MeV), wt))
        if all(v is None for v in vals):
            return FluxResult(0.0, 'beyond_mirror', e_min_MeV, e_max)
        if len(vals) == 1 or any(v is None for v in vals):
            total = sum(v[0] * v[1] for v in vals if v is not None)
        else:
            (f0, w0), (f1, w1) = vals
            total = float(_log_mix(np.array([f0]), np.array([f1]), w1)[0])
        return FluxResult(float(total), status, e_min_MeV, e_max)


def integrate_power_law(E: np.ndarray, f: np.ndarray, e_min: float) -> float:
    """∫ f dE от e_min до E[-1], между узлами f = a·E^b (точно для степенных
    спектров); если один из узлов нулевой — линейно. Хвост выше E[-1] отброшен."""
    E = np.asarray(E, dtype=float); f = np.asarray(f, dtype=float)
    if e_min >= E[-1]:
        return 0.0
    total = 0.0
    for i in range(len(E) - 1):
        E0, E1, f0, f1 = E[i], E[i + 1], f[i], f[i + 1]
        if E1 <= e_min:
            continue
        lo = max(E0, e_min)
        if f0 > 0 and f1 > 0:
            b = math.log(f1 / f0) / math.log(E1 / E0)
            a = f0 / E0 ** b
            if abs(b + 1.0) < 1e-9:
                total += a * math.log(E1 / lo)
            else:
                total += a / (b + 1.0) * (E1 ** (b + 1.0) - lo ** (b + 1.0))
        else:                                  # нулевой узел: линейная интерполяция
            f_lo = f0 + (f1 - f0) * (lo - E0) / (E1 - E0)
            total += 0.5 * (f_lo + f1) * (E1 - lo)
    return float(total)
