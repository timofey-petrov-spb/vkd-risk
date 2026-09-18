# -*- coding: utf-8 -*-
"""Отсечка по времени публикации — потребительская сторона строгого режима (Т4).

CONTRACT.md v3.1, раздел 10 и R4: в строгом прогнозе из прошлого допустима
только доказанно доступная к отсечке версия каждого использованного значения.
Запись без времени публикации непригодна для строгого воспроизведения —
она исключается и попадает в список исключённых с причиной, а не
используется «как есть».

Разбор Codex 19.09, п. 1: фильтровать надо каждое использованное наблюдение,
а не родительскую запись — иначе будущие значения, вложенные в старую
карточку, просачиваются в прошлое.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from vkd.types import Conjunction, EnvironmentSample, EventInterval


@dataclass(frozen=True)
class CutoffResult:
    samples: tuple[EnvironmentSample, ...]
    events: tuple[EventInterval, ...]
    conjunctions: tuple[Conjunction, ...]
    excluded: tuple[str, ...]          # «id: причина» — показывается пользователю
    cutoff_utc: Optional[datetime]


def _keep(published_utc: Optional[datetime], t_utc: Optional[datetime], cutoff: datetime) -> Optional[str]:
    """None = оставить; строка = причина исключения."""
    if published_utc is None:
        return 'время публикации неизвестно — непригодно для строгого воспроизведения'
    if published_utc > cutoff:
        return 'опубликовано %s, после отсечки' % published_utc.strftime('%Y-%m-%d %H:%MZ')
    if t_utc is not None and t_utc > cutoff:
        # наблюдение из будущего внутри старой записи (вложенные allKpIndex и т. п.)
        return 'момент наблюдения %s позже отсечки, хотя запись опубликована раньше' % t_utc.strftime('%Y-%m-%d %H:%MZ')
    return None


def apply_cutoff(samples: Sequence[EnvironmentSample], events: Sequence[EventInterval],
                 conjunctions: Sequence[Conjunction], cutoff_utc: Optional[datetime]) -> CutoffResult:
    if cutoff_utc is None:                       # разбор: весь архив, без отсечки
        return CutoffResult(tuple(samples), tuple(events), tuple(conjunctions), (), None)
    ks, ke, kc, ex = [], [], [], []
    for s in samples:
        # для наблюдений момент значения не может быть позже отсечки; для прогнозов
        # (kind = external_forecast) момент действия может быть в будущем — это разрешено (R6)
        t_check = s.t_utc if s.kind.value == 'observation' else None
        why = _keep(s.published_utc, t_check, cutoff_utc)
        (ks.append(s) if why is None else ex.append('%s: %s' % (s.raw_record_id, why)))
    for e in events:
        t_check = e.start_utc if (e.kind.value == 'observation' and e.start_utc) else None
        why = _keep(e.published_utc, t_check, cutoff_utc)
        (ke.append(e) if why is None else ex.append('%s: %s' % (e.event_id, why)))
    for c in conjunctions:
        why = _keep(c.published_utc, None, cutoff_utc)
        (kc.append(c) if why is None else ex.append('%s: %s' % (c.raw_record_id, why)))
    return CutoffResult(tuple(ks), tuple(ke), tuple(kc), tuple(ex), cutoff_utc)
