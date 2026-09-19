# -*- coding: utf-8 -*-
"""Устойчивость вердикта к неизвестным параметрам (О7; CONTRACT v3.1 п. 4.5).

Вместо фиксированного допуска 5 мин — пересчёт оценки на сетке порога аномалии
и канала захваченных протонов. Допуск равнозначности по минутам — разброс
РАЗНОСТИ минут между двумя лучшими окнами по оси порога (порог сдвигает оба
окна синфазно, поэтому разброс абсолютных минут одного окна допуском быть не
может — найдено разбором 19.09: «равнозначны» при разнице 48 мин и «выбор
устойчив» на одном экране). Допуск по флюенсу — разброс отношения флюенсов
тех же окон по оси канала E_min, не меньше настройки fluence_equiv_ratio.

Две проверки на сетке:
  * ranking_stable — при нулевом допуске лучшее окно одно на всей сетке;
  * stable — с итоговым допуском предпочтительное окно (или одинаковый отказ)
    одно на всей сетке; это и печатается как «выбор устойчив».

Разбор Codex п. 10: смена канала GOES меняет показатель, а не его погрешность;
поэтому канал варьируется как отдельная ось, а допуск по минутам берётся по оси порога.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Optional, Sequence

from vkd.types import TrajectoryPoint, Window


@dataclass(frozen=True)
class Robustness:
    preferred_starts: dict          # (thr, e_min) -> ISO начала предпочтительного окна или None (итоговый допуск)
    ranking_by_grid: dict           # (thr, e_min) -> ISO лучшего окна при нулевом допуске или None
    diff_spread_min: float          # разброс разности минут (второе − лучшее) по оси порога
    fluence_ratio_spread: float     # разброс отношения флюенсов (второе / лучшее) по оси канала, ≥ 1
    tol_min: float                  # итоговый допуск по минутам
    tol_ratio: float                # итоговый допуск по флюенсу (отношение)
    ranking_stable: bool            # лучшее окно при нулевом допуске одно на всей сетке
    stable: bool                    # предпочтительное окно с итоговым допуском одно на всей сетке (или везде отказ)
    grid: tuple
    diff_by_thr: dict               # thr -> разность минут (второе − лучшее)
    ratio_by_e: dict                # e_min -> отношение флюенсов (второе / лучшее)
    pair: tuple                     # (ISO лучшего по минутам, ISO второго) по базовой точке или ()
    pair_fluence: tuple             # (ISO лучшего по флюенсу, ISO второго) или ()
    saa_spread_min: float           # прежний показатель: разброс абсолютных минут лучшего окна (только для сведения)


def resaa(traj: Sequence[TrajectoryPoint], thr_nT: float) -> list[TrajectoryPoint]:
    """Пересчёт признака аномалии из сохранённого |B| без повторного расчёта орбиты."""
    return [replace(p, in_saa=(p.B_nT < thr_nT) if p.B_nT is not None else None) for p in traj]


def _sw(a):
    f = {x.name: x.value for x in a.mechanisms[0].factors}
    return f.get('минут в аномалии'), next((v for n, v in f.items() if n.startswith('флюенс')), None)


def robustness(traj: Sequence[TrajectoryPoint], windows: Sequence[Window],
               assess: Callable, decide: Callable,
               thr_grid: Sequence[float], e_grid: Sequence[float],
               base_thr: float, base_e: float,
               min_tol_min: float = 1.0, min_tol_ratio: float = 1.5) -> Robustness:
    """assess(traj_with_saa, kwargs) -> список WindowAssessment;
    decide(assessments, kwargs) -> Recommendation. Оценка на сетке считается один раз,
    правило применяется дважды: с нулевым допуском (ранжирование) и с итоговым."""
    thr_grid = list(thr_grid)
    e_grid = list(e_grid)
    if base_thr not in thr_grid:
        thr_grid = sorted(thr_grid + [base_thr])
    if base_e not in e_grid:
        e_grid = sorted(e_grid + [base_e])
    A = {}
    for thr in thr_grid:
        tr = resaa(traj, thr)
        for e in e_grid:
            A[(thr, e)] = assess(tr, {'saa_B_threshold_nT': thr, 'e_min_MeV': e})
    zero = {'equiv_tol_min': 0.0, 'fluence_equiv_ratio': 1.0}
    ranking = {}
    for k, A_k in A.items():
        r = decide(A_k, {'saa_B_threshold_nT': k[0], 'e_min_MeV': k[1], **zero})
        ranking[k] = r.preferred.start_utc.isoformat() if r.preferred else None

    # базовая пара: лучшее и второе окно среди кандидатов без условий по (флюенс, минуты)
    base = A[(base_thr, base_e)]
    cands = [a for a in base if not any(m.needs_check for m in a.mechanisms)]

    def key(a):
        m, fl = _sw(a)
        return (fl if fl is not None else float('inf'), m if m is not None else float('inf'))

    diff_by_thr, ratio_by_e, pair, pair_fl, saa_best = {}, {}, (), (), []
    if len(cands) >= 2:
        # пара по минутам (лучшее — с меньшими минутами) и пара по флюенсу — могут различаться при трёх окнах
        by_min = sorted(cands, key=lambda a: (_sw(a)[0] if _sw(a)[0] is not None else float('inf')))[:2]
        by_fl = sorted(cands, key=key)[:2]
        pair = (by_min[0].window.start_utc.isoformat(), by_min[1].window.start_utc.isoformat())
        pair_fl = (by_fl[0].window.start_utc.isoformat(), by_fl[1].window.start_utc.isoformat())
        for thr in thr_grid:
            A_t = A[(thr, base_e)]
            b = next(a for a in A_t if a.window.start_utc == by_min[0].window.start_utc)
            s = next(a for a in A_t if a.window.start_utc == by_min[1].window.start_utc)
            mb, ms = _sw(b)[0], _sw(s)[0]
            if mb is not None and ms is not None:
                diff_by_thr[thr] = ms - mb
                saa_best.append(mb)
        for e in e_grid:
            A_e = A[(base_thr, e)]
            b = next(a for a in A_e if a.window.start_utc == by_fl[0].window.start_utc)
            s = next(a for a in A_e if a.window.start_utc == by_fl[1].window.start_utc)
            fb, fs = _sw(b)[1], _sw(s)[1]
            if fb and fs and fb > 0 and fs > 0:
                ratio_by_e[e] = fs / fb
    diff_spread = (max(diff_by_thr.values()) - min(diff_by_thr.values())) if diff_by_thr else 0.0
    ratio_spread = (max(ratio_by_e.values()) / min(ratio_by_e.values())) if ratio_by_e else 1.0
    tol_min = max(min_tol_min, diff_spread)
    tol_ratio = max(min_tol_ratio, ratio_spread)
    prefs = {}
    for k, A_k in A.items():
        r = decide(A_k, {'saa_B_threshold_nT': k[0], 'e_min_MeV': k[1], 'equiv_tol_min': tol_min, 'fluence_equiv_ratio': tol_ratio})
        prefs[k] = r.preferred.start_utc.isoformat() if r.preferred else None
    return Robustness(prefs, ranking, diff_spread, ratio_spread, tol_min, tol_ratio,
                      ranking_stable=len(set(ranking.values())) <= 1, stable=len(set(prefs.values())) <= 1,
                      grid=(tuple(thr_grid), tuple(e_grid)), diff_by_thr=diff_by_thr, ratio_by_e=ratio_by_e, pair=pair, pair_fluence=pair_fl,
                      saa_spread_min=(max(saa_best) - min(saa_best)) if saa_best else 0.0)
