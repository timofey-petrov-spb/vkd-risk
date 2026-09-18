# -*- coding: utf-8 -*-
"""Объяснение предупреждения по семи пунктам постановки (раздел 4, критерий О4).

Для каждого предупреждения доступны:
  1. воздействие и его значение для ВКД;
  2. ожидаемый или наблюдаемый период;
  3. данные и единицы измерения;
  4. источник и время публикации;
  5. применённое правило или модель;
  6. ограничения и обоснование уверенности;
  7. происхождение: наблюдение, внешний прогноз или наш расчёт — различимо.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional, Sequence

from vkd.types import (Coverage, EnvironmentSample, FactorValue, Kind,
                       MechanismAssessment, WindowAssessment)

KIND_RU = {Kind.OBSERVATION: 'наблюдение', Kind.EXTERNAL_FORECAST: 'внешний прогноз',
           Kind.OWN_CALCULATION: 'наш расчёт'}
COVERAGE_RU = {Coverage.FULL: 'полное', Coverage.PARTIAL: 'частичное', Coverage.NONE: 'отсутствует'}


@dataclass(frozen=True)
class Card:
    title: str
    kind: Kind
    impact_ru: str
    period_ru: str
    data_ru: str
    source_ru: str
    rule_ru: str
    limits_ru: str
    record_ids: tuple[str, ...]
    severity: str          # "info" | "limiting" | "critical"


# Значение для ВКД по каждой величине — одна фраза, откуда следует угроза.
_IMPACT = {
    'минут в аномалии': 'Пролёт Южно-Атлантической аномалии — участок с наибольшим потоком захваченных '
                        'протонов на орбите МКС. Чем больше минут окна приходится на аномалию, тем выше '
                        'экспозиция при прочих равных. Это условие на траектории, не доза человека.',
    'флюенс захваченных протонов': 'Интеграл потока захваченных протонов вдоль пути за окно. Показатель '
                                   'относительного сравнения окон между собой; в дозу не переводится без '
                                   'модели защиты и ткани.',
    'поток протонов GOES ≥10 МэВ': 'Наблюдаемый поток на геостационарной орбите — индикатор солнечного '
                                   'протонного события. У станции на 51,6° большая часть таких протонов '
                                   'отсекается геомагнитным обрезанием; переносится не напрямую.',
    'ожидаемое число попаданий, пластина 1 м²': 'Статистика природных метеороидов на опорную площадь за окно. '
                                                'Не вероятность повреждения скафандра и не попадание в космонавта.',
    'сближений с TCA в окне': 'Прогноз сближений станции с отслеживаемыми объектами. Относится к станции; '
                              'вероятность попадания фрагмента в космонавта отсюда не следует.',
    'прогноз Kp NOAA': 'Прогноз планетарного индекса Kp службой NOAA SWPC по 3-часовым интервалам, выпущенный до '
                       'отсечки. Kp ≥ 7 (G3) — сильная геомагнитная буря: геомагнитное обрезание ослабевает и поток '
                       'протонов на орбите станции растёт. Это внешний прогноз, не наблюдение.',
    'вероятность S1 и выше': 'Суточная вероятность протонного события S1 и выше по прогнозу NOAA. Относится к суткам '
                             'целиком, а не к окну ВКД; в вероятность за окно не пересчитывается.',
    'вероятность протонного события': 'Суточная вероятность протонного события по прогнозу NOAA daypre. Относится '
                                       'к суткам целиком; в вероятность за окно не пересчитывается.',
}


def _period(a: WindowAssessment) -> str:
    end = a.window.start_utc + timedelta(minutes=a.window.duration_min)
    return 'окно %s — %s UTC, %d мин' % (a.window.start_utc.strftime('%Y-%m-%d %H:%M'),
                                         end.strftime('%H:%M'), a.window.duration_min)


def _source_line(f: FactorValue, samples: dict[str, EnvironmentSample]) -> str:
    parts = []
    for rid in f.record_ids:
        s = samples.get(rid)
        if s is None:
            parts.append(rid)
            continue
        pub = s.published_utc.strftime('%Y-%m-%d %H:%MZ') if s.published_utc else 'время публикации неизвестно'
        parts.append('%s, запись %s, момент %s, публикация %s, получено %s, качество %s' % (
            s.source_id, s.raw_record_id, s.t_utc.strftime('%Y-%m-%d %H:%MZ'), pub,
            s.fetched_utc.strftime('%H:%MZ'), s.quality))
    return '; '.join(parts) if parts else 'источник: собственный расчёт по траектории'


def cards_for_window(a: WindowAssessment, samples: dict[str, EnvironmentSample]) -> list[Card]:
    cards: list[Card] = []
    period = _period(a)
    for m in a.mechanisms:
        for f in m.factors:
            key = next((k for k in _IMPACT if f.name.startswith(k)), None)
            value = ('%.4g %s' % (f.value, f.unit)) if f.value is not None else 'нет значения'
            limits = 'покрытие %s; наличие воздействия: %s' % (COVERAGE_RU[f.coverage], f.presence.value)
            if f.limits_note:
                limits += '; ' + f.limits_note
            if f.kind == Kind.OWN_CALCULATION:
                limits += '; уверенность зависит от точности траектории и границ модели, не от статистики'
            cards.append(Card(
                title=f.name, kind=f.kind,
                impact_ru=_IMPACT.get(key, 'значение для ВКД описано в правиле'),
                period_ru=period,
                data_ru=value,
                source_ru=_source_line(f, samples),
                rule_ru=f.rule_applied,
                limits_ru=limits,
                record_ids=f.record_ids,
                severity='info',
            ))
        for reason in m.needs_check_reasons:
            sev = 'critical' if 'приоритетное' in reason else 'limiting'
            sim = 'МОДЕЛИРУЕМОЕ' in reason
            cards.append(Card(
                title=('Сценарий: ' if sim else 'Условие: ') + reason.split(':')[0],
                kind=Kind.OWN_CALCULATION if sim else (Kind.EXTERNAL_FORECAST if 'прогноз' in reason else (
                    Kind.OBSERVATION if ('GOES' in reason or 'Kp' in reason) else Kind.EXTERNAL_FORECAST)),
                impact_ru=('Приоритетное предупреждение (S ≥ 3): NOAA рекомендует избегать радиационной опасности '
                           'при ВКД; окно требует срочной проверки специалистом. Команды прервать или продолжать ВКД '
                           'из индекса не следуют.' if sev == 'critical'
                           else 'Предупреждение: окно исключено из автоматического выбора и требует ручной проверки '
                                'аналитиком. Это консервативная политика прототипа, не эксплуатационная норма.'),
                period_ru=period,
                data_ru=reason,
                source_ru='пороги: NOAA Space Weather Scales (S1 10, S2 100, S3 1000 pfu по ≥10 МэВ; G3 = Kp 7); '
                          'граница класса — решение команды, обоснование в docs/KRITERII_PLAN.md разделы 5 и 7',
                rule_ru='CONTRACT.md v3.1 раздел 4, пункт 2: условия дополнительной проверки',
                limits_ru='порог — настройка с источником; наблюдение сейчас не распространяется молча на всё окно; '
                          'при иной шкале организации порог меняется в конфигурации',
                record_ids=(),
                severity=sev,
            ))
    return cards
