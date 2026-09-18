# -*- coding: utf-8 -*-
"""Захваченные протоны по приложению А ОСТ 134-1044-2007: поток вдоль траектории.

Источник данных: data/ost1044_belts/A_2_1.csv (минимум СА) и A_2_2.csv
(максимум СА) — дифференциальная плотность потока, см⁻²·с⁻¹·ср⁻¹·МэВ⁻¹,
как функция (L, B/B0) и энергии. Извлечено из текста стандарта, сверено.

Исправления по разбору Codex 19.09 (journal/friend.md):
  * интегрирование по энергии трапециями, а не сумма узлов;
  * хвост спектра выше последнего узла ОТБРАСЫВАЕТСЯ и это объявлено;
  * вне сетки L — статус «нет модели», не ноль;
  * выше максимального табличного B/B0 — физический ноль (точка отражения
    выше), статус «за зеркальной точкой» — это НЕ отсутствие модели;
  * B/B0 < 1 (несовместимость дипольной L и поля IGRF) не клампится:
    берётся экваториальное значение и ставится статус «несогласованность».
"""
from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass
from typing import Optional

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DATA = os.path.join(_ROOT, 'data', 'ost1044_belts')

L_MATCH_TOL = 0.15        # ближайшая табличная L не дальше этого — иначе «нет модели»


@dataclass(frozen=True)
class FluxResult:
    value_per_cm2_s_sr: Optional[float]   # интегральный поток выше e_min; None = нет модели
    status: str                            # "ok" | "no_model_L" | "beyond_mirror" | "inconsistent_BB0"
    e_min_MeV: float
    e_max_MeV: float                       # последний узел таблицы: хвост выше отброшен


class BeltTable:
    def __init__(self, solar_activity: str = 'min'):
        fn = 'A_2_1.csv' if solar_activity == 'min' else 'A_2_2.csv'
        rows = list(csv.reader(io.open(os.path.join(_DATA, fn), encoding='utf-8')))
        self.energies_MeV = np.array([float(h.replace('E_', '').replace('_MeV', '')) for h in rows[0][2:]])
        self.table: dict[float, list[tuple[float, np.ndarray]]] = {}
        for r in rows[1:]:
            L, bb = float(r[0]), float(r[1])
            self.table.setdefault(L, []).append((bb, np.array([float(x) for x in r[2:]])))
        for L in self.table:
            self.table[L].sort(key=lambda p: p[0])
        self.Ls = np.array(sorted(self.table))
        self.source = 'ОСТ 134-1044-2007, прил. А, табл. %s' % ('А.2.1' if solar_activity == 'min' else 'А.2.2')

    def integral_flux(self, L: Optional[float], B_over_B0: Optional[float], e_min_MeV: float) -> FluxResult:
        e_max = float(self.energies_MeV[-1])
        if L is None or B_over_B0 is None:
            return FluxResult(None, 'no_model_L', e_min_MeV, e_max)
        Ln = float(self.Ls[np.argmin(np.abs(self.Ls - L))])
        if abs(Ln - L) > L_MATCH_TOL:
            return FluxResult(None, 'no_model_L', e_min_MeV, e_max)
        pts = self.table[Ln]
        bbs = np.array([p[0] for p in pts])
        status = 'ok'
        if B_over_B0 < 1.0:
            status = 'inconsistent_BB0'
            B_over_B0 = 1.0
        if B_over_B0 > bbs[-1]:
            return FluxResult(0.0, 'beyond_mirror', e_min_MeV, e_max)
        # интерполяция спектра по B/B0 (линейно между соседними строками)
        j = int(np.searchsorted(bbs, B_over_B0))
        if j == 0:
            spec = pts[0][1]
        else:
            b0, b1 = bbs[j - 1], bbs[j]
            w = 0.0 if b1 == b0 else (B_over_B0 - b0) / (b1 - b0)
            spec = pts[j - 1][1] * (1 - w) + pts[j][1] * w
        return FluxResult(_integrate(self.energies_MeV, spec, e_min_MeV), status, e_min_MeV, e_max)


def _integrate(E: np.ndarray, f: np.ndarray, e_min: float) -> float:
    """Трапеции по узлам от e_min до последнего узла. Хвост выше — отброшен."""
    if e_min >= E[-1]:
        return 0.0
    if e_min <= E[0]:
        Ei, fi = E, f
    else:
        k = int(np.searchsorted(E, e_min))
        f_at = float(np.interp(e_min, E, f))
        Ei = np.concatenate(([e_min], E[k:]))
        fi = np.concatenate(([f_at], f[k:]))
    return float(np.trapezoid(fi, Ei)) if hasattr(np, 'trapezoid') else float(np.trapz(fi, Ei))
