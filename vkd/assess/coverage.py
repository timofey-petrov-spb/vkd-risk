# -*- coding: utf-8 -*-
"""Непокрытое время окна по причинам — в минутах, а не в долях узлов.

Разбор Codex, п. 3: «Нужно отдельно показывать длительность пропусков, известный вклад
и причины отсутствия модели. Заполнять пропуски нулём недопустимо.» До этого модуля
сервис печатал долю покрытия (`81,1 % времени окна`) и ОТДЕЛЬНО счётчики статусов узлов
(«у 12 точек L вне сетки»). Ни одно из двух не отвечает на вопрос «сколько минут окна
осталось без модели и почему именно там».

Правило разбиения здесь ТО ЖЕ, что у рабочего интегратора `vkd.orbit.integration.integrate_time`
(область Codex, не изменяется): интервал между соседними узлами даёт время только тогда,
когда известны ОБА конца и разрыв не больше `max_gap_seconds`. Всё остальное время окна —
пропуск. Совпадение с интегратором проверяется тождеством

    покрытое время (integrate_time) + сумма пропусков (этот модуль) = длительность окна

в `tests/test_coverage_gaps.py`: два независимых прохода по одним и тем же данным.

Причина пропуска берётся у концов интервала. Если причины концов разные, минуты делятся
пополам между ними: приписать весь интервал одному концу значило бы утверждать, где
внутри интервала модель кончилась, а этого мы не знаем.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence

from vkd.types import CoverageGap

# Русские названия статусов узла таблицы ОСТ. Ключи — статусы `vkd.assess.trapped.FluxResult`.
GAP_REASON_RU = {
    'no_model_L': 'L вне сетки таблицы ОСТ 1,14…9 (сильное поле вне аномалии)',
    'inconsistent_BB0': 'B/B0 < 1 — магнитные координаты несовместимы',
    'outside_model': 'вне модели магнитных координат',
    'no_model': 'модели для точки нет',
    'no_point': 'точек трассы нет',
}
GAP_BEYOND_HORIZON_RU = 'вне известной трассы'
GAP_STEP_RU = 'разрыв трассы больше допустимого'
GAP_MIXED_RU = 'причина участка не одна'


def uncovered_minutes_by_reason(times: Sequence[datetime], values: Sequence[Optional[float]],
                                statuses: Sequence[Optional[str]],
                                start_utc: datetime, end_utc: datetime, *,
                                max_gap_seconds: float = 60.0) -> tuple[CoverageGap, ...]:
    """Пропуски окна [start, end) по причинам, в минутах, по убыванию длительности.

    `statuses[i]` — почему у узла i нет значения; у узла со значением статус не читается.
    Возвращается пустой кортеж, если пропусков нет (покрытие полное).

    Ноль в `values` — это ИЗВЕСТНЫЙ ноль (например, точка выше точки отражения): такой узел
    покрывает время. Отсутствие модели — только None. Подмена одного другим и есть то самое
    «заполнение пропусков нулём», которое запрещено.
    """
    if not (len(times) == len(values) == len(statuses)):
        raise ValueError('Длины времён, значений и статусов различаются')
    start_utc, end_utc = start_utc.astimezone(timezone.utc), end_utc.astimezone(timezone.utc)
    if end_utc <= start_utc:
        raise ValueError('Длительность окна должна быть положительной')
    ts = [t.astimezone(timezone.utc) for t in times]
    for a, b in zip(ts, ts[1:]):
        if b <= a:
            raise ValueError('Времена должны строго возрастать')
    total_s = (end_utc - start_utc).total_seconds()

    acc: dict[str, float] = {}

    def add(reason: str, seconds: float) -> None:
        if seconds > 0:
            acc[reason] = acc.get(reason, 0.0) + seconds

    if not ts:
        return (CoverageGap(GAP_REASON_RU['no_point'], total_s / 60.0),)

    # Время окна за пределами известной трассы — экстраполяции нет, это пропуск «вне трассы».
    add(GAP_BEYOND_HORIZON_RU, (min(ts[0], end_utc) - start_utc).total_seconds())
    add(GAP_BEYOND_HORIZON_RU, (end_utc - max(ts[-1], start_utc)).total_seconds())

    for a, b, va, vb, sa, sb in zip(ts, ts[1:], values, values[1:], statuses, statuses[1:]):
        lo, hi = max(a, start_utc), min(b, end_utc)
        dt = (hi - lo).total_seconds()
        if dt <= 0:
            continue
        span = (b - a).total_seconds()
        if va is not None and vb is not None and span <= max_gap_seconds:
            continue                                   # интервал покрыт: у интегратора он тоже покрыт
        if va is not None and vb is not None:
            add(GAP_STEP_RU, dt)                       # значения есть, но шаг трассы больше допустимого
            continue
        ends = [GAP_REASON_RU.get(s or 'no_model', s or GAP_REASON_RU['no_model'])
                for v, s in ((va, sa), (vb, sb)) if v is None]
        if len(ends) == 2 and ends[0] != ends[1]:
            add(ends[0], dt / 2.0)                     # где внутри интервала сменилась причина — неизвестно
            add(ends[1], dt / 2.0)
        else:
            add(ends[0], dt)
    return tuple(CoverageGap(r, s / 60.0) for r, s in sorted(acc.items(), key=lambda kv: -kv[1]))


def gaps_ru(gaps: Sequence[CoverageGap], fmt=None) -> str:
    """«14 мин — L вне сетки таблицы ОСТ; 4 мин — вне известной трассы» одной строкой."""
    f = fmt or (lambda x: ('%.0f' % x) if x >= 1 else ('%.1f' % x).replace('.', ','))
    return '; '.join('%s мин — %s' % (f(g.minutes), g.reason_ru) for g in gaps)
