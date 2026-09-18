# -*- coding: utf-8 -*-
"""Устойчивость вердикта к неизвестным параметрам (О7; CONTRACT v3.1 п. 4.5).

Вместо фиксированного допуска 5 мин: пересчёт оценки на сетке порога аномалии
и канала захваченных протонов. Разброс минут в аномалии у лучшего окна —
и есть допуск равнозначности; смена предпочтительного окна на сетке —
признак неустойчивости, о котором сообщается пользователю.

Разбор Codex п. 10: смена канала GOES меняет показатель, а не его погрешность;
поэтому канал варьируется как отдельная ось, а допуск берётся по оси порога.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Optional, Sequence

from vkd.types import TrajectoryPoint, Window


@dataclass(frozen=True)
class Robustness:
    preferred_starts: dict          # (thr, e_min) -> ISO начала предпочтительного окна или None
    saa_spread_min: float           # разброс минут в аномалии у базового лучшего окна по оси порога
    stable: bool                    # предпочтительное окно одно на всей сетке (или везде отказ)
    grid: tuple


def resaa(traj: Sequence[TrajectoryPoint], thr_nT: float) -> list[TrajectoryPoint]:
    """Пересчёт признака аномалии из сохранённого |B| без повторного расчёта орбиты."""
    return [replace(p, in_saa=(p.B_nT < thr_nT) if p.B_nT is not None else None) for p in traj]


def robustness(traj: Sequence[TrajectoryPoint], windows: Sequence[Window], run: Callable,
               thr_grid: Sequence[float], e_grid: Sequence[float]) -> Robustness:
    """run(traj_with_saa, thresholds_kwargs) -> (assessments, recommendation)."""
    prefs, saa_best = {}, []
    for thr in thr_grid:
        tr = resaa(traj, thr)
        for e in e_grid:
            # допуск внутри сетки — ноль: предпочтение определяется ранжированием, иначе
            # разброс зависел бы от самого допуска (дефект воспроизводимости, найден повтором примера)
            A, rec = run(tr, {'saa_B_threshold_nT': thr, 'e_min_MeV': e, 'equiv_tol_min': 0.0})
            prefs[(thr, e)] = rec.preferred.start_utc.isoformat() if rec.preferred else None
            if rec.preferred is not None:
                a = next(x for x in A if x.window.start_utc == rec.preferred.start_utc)
                v = next((f.value for f in a.mechanisms[0].factors if f.name == 'минут в аномалии'), None)
                if v is not None and e == e_grid[0]:
                    saa_best.append(v)
    distinct = set(prefs.values())
    return Robustness(prefs, (max(saa_best) - min(saa_best)) if saa_best else 0.0,
                      stable=len(distinct) <= 1, grid=(tuple(thr_grid), tuple(e_grid)))
