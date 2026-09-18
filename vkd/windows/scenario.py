# -*- coding: utf-8 -*-
"""Режим «Что если» — стресс-сценарий (сессия экспертов; KRITERII_PLAN раздел 5).

Разбор Codex п. 5: сценарий не подменяет физический тип события;
синтетический вход помечается `is_simulated=True`, задержка работ — свойство
запроса, отказ источника — статус источника, скачок Kp или потока —
значение с единицами. Метка сценария распространяется на весь результат
и выгрузку; синтетические значения никогда не попадают в живой кеш и replay.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Optional, Sequence

from vkd.types import EnvironmentSample, EventInterval, Kind, Window


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    work_delay_min: int = 0                      # задержка начала работ → сдвиг окон
    sep_onset_offset_min: Optional[int] = None   # протонное событие начинается через N мин после t0
    sep_level_pfu: Optional[float] = None        # его уровень, pfu ≥10 МэВ
    kp_override: Optional[float] = None          # скачок Kp
    note: str = ''


def apply_to_windows(windows: Sequence[Window], sc: Scenario) -> list[Window]:
    """Задержка работ: сдвиг начала при неизменной длительности — изменение плана."""
    if not sc.work_delay_min:
        return list(windows)
    return [Window(w.start_utc + timedelta(minutes=sc.work_delay_min), w.duration_min) for w in windows]


def simulated_events(t0: datetime, sc: Scenario) -> list[EventInterval]:
    if sc.sep_onset_offset_min is None or sc.sep_level_pfu is None:
        return []
    start = t0 + timedelta(minutes=sc.sep_onset_offset_min)
    return [EventInterval(
        event_id='sim_sep#' + sc.scenario_id, kind_of_event='SEP', kind=Kind.OWN_CALCULATION,
        start_utc=start, end_utc=None, start_uncertain=False, end_uncertain=True,
        valid_from_utc=start, valid_to_utc=None, source_id='scenario', published_utc=None,
        raw_record_id='sim_sep#' + sc.scenario_id, is_simulated=True,
        note='моделируемое протонное событие %.3g pfu с %s' % (sc.sep_level_pfu, start.strftime('%H:%MZ')))]


def simulated_kp(kp: Optional[EnvironmentSample], t0: datetime, sc: Scenario) -> Optional[EnvironmentSample]:
    if sc.kp_override is None:
        return kp
    base = kp or EnvironmentSample(t0, 'kp', None, '', 'scenario', Kind.OWN_CALCULATION, None, None, None, t0, 'model', 'sim_kp')
    return replace(base, value=float(sc.kp_override), source_id='scenario', kind=Kind.OWN_CALCULATION,
                   quality='model', raw_record_id='sim_kp#' + sc.scenario_id)
