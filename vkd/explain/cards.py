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

Карточки строятся для КАЖДОГО окна (window_index): условие окна 2 объясняется
так же, как условие окна 1. Условия берутся из структурных Condition, поэтому
период — это интервал события, а источник — записи DONKI/NOAA с временем публикации,
а не окно и таблица порогов (разбор 19.09, О4). Величины без данных подписываются
по происхождению: наблюдение без записи — «наблюдения нет», а не «наш расчёт».
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Sequence

from vkd.explain.format import (EVENT_KIND_RU, SOURCE_ID_RU, fmt_ru, record_ru, source_ru,
                                storm_signal_kinds)
from vkd.types import (Condition, Coverage, EnvironmentSample, EventInterval, FactorValue, Kind, Presence,
                       WindowAssessment)

KIND_RU = {Kind.OBSERVATION: 'наблюдение', Kind.EXTERNAL_FORECAST: 'внешний прогноз',
           Kind.OWN_CALCULATION: 'наш расчёт'}
COVERAGE_RU = {Coverage.FULL: 'полное', Coverage.PARTIAL: 'частичное', Coverage.NONE: 'отсутствует'}
PRESENCE_RU = {Presence.DETECTED: 'воздействие есть', Presence.NOT_DETECTED: 'не выявлено',
               Presence.UNKNOWN: 'неизвестно (данных нет)'}
QUALITY_RU = {'final': 'окончательное', 'preliminary': 'предварительное', 'model': 'модель', 'unknown': 'качество не указано'}
SOURCE_RU = {
    'ecss_grun:grun-ecss-2020-v1': 'ECSS-E-ST-10-04C Rev.1 (15.06.2020), раздел 10 и прил. J (Table J-5, J-6); '
                                   'спецификация A5 grun-ecss-2020-v1 (docs/methods/METEOROIDS_GRUN_SPEC.md)',
    'imo_calendar': 'календарь главных метеорных потоков IMO (Rendtel, ежегодные выпуски); справочные даты и ZHR',
}
NOTE_LIMIT = 160          # заметку источника не режем по символам: либо целиком, либо по границе слова
HOURS_PER_YEAR = 8766.0


def short_note(note: str, limit: int = NOTE_LIMIT) -> str:
    """Заметка записи целиком, а при длине больше limit — до границы слова с многоточием.
    Обрезка по символам давала на экране обрывки «тип сообщения: Space Wea» и «(по»."""
    n = ' '.join((note or '').split())
    if len(n) <= limit:
        return n
    cut = n[:limit].rsplit(' ', 1)[0] if ' ' in n[:limit] else n[:limit]
    return cut.rstrip(' ,;.:—-') + '…'


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
    window_index: Optional[int] = None     # номер окна (с 1), к которому относится карточка
    window_ru: str = ''                    # «окно 2024-05-10 12:00 — 18:00 UTC, 360 мин»


# Значение для ВКД по каждой величине — одна фраза, откуда следует угроза.
_IMPACT = {
    'минут в аномалии': 'Пролёт Южно-Атлантической аномалии — участок с наибольшим потоком захваченных '
                        'протонов на орбите МКС. Чем больше минут окна приходится на аномалию, тем выше '
                        'экспозиция при прочих равных. Это условие на траектории, не доза человека.',
    'флюенс захваченных протонов': 'Интеграл всенаправленного потока захваченных протонов вдоль пути за окно — '
                                   'целевая величина сравнения окон по космопогоде (минуты в аномалии — её объяснение). '
                                   'В дозу не переводится без модели защиты и ткани.',
    'минут доступности протонов': 'Сколько минут окна трасса проходит там, где вертикальное геомагнитное обрезание ниже '
                                  'жёсткости протонов канала: только на этих участках наблюдаемый на GOES поток может '
                                  'достигать станции. Связывает измерение на геостационарной орбите с траекторией.',
    'поток протонов GOES ≥10 МэВ': 'Наблюдаемый поток на геостационарной орбите — индикатор солнечного '
                                   'протонного события. У станции на 51,6° большая часть таких протонов '
                                   'отсекается геомагнитным обрезанием; переносится не напрямую — см. фактор '
                                   '«минут доступности протонов ≥10 МэВ по обрезанию».',
    'Kp, последнее наблюдение': 'Последнее наблюдение планетарного индекса Kp. Kp ≥ 7 (G3) — триггер проверки условий '
                                'модели, орбиты и связи; сам по себе не рост дозы и не запрет ВКД.',
    'ожидаемое число попаданий, пластина 1 м²': 'Статистика природных метеороидов на опорную площадь за окно. '
                                                'Не вероятность повреждения скафандра и не попадание в космонавта. '
                                                'Эта линия даёт АБСОЛЮТНУЮ оценку и охват механизма; выбор между окнами '
                                                'она не определяет — при равной длительности окна различаются по ней '
                                                'меньше порога различимости, и окно выбирается по космопогоде.',
    'активных метеорных потоков': 'Признак активности главного метеорного потока на дату окна. Поток добавляет частицы '
                                  'к спорадическому фону, но его вклад в число попаданий не рассчитан (ECSS 10.2.2.2c).',
    'сближений с TCA в окне': 'Прогноз сближений станции с отслеживаемыми объектами. Относится к станции; '
                              'вероятность попадания фрагмента в космонавта отсюда не следует.',
    'прогноз Kp NOAA': 'Прогноз планетарного индекса Kp службой NOAA SWPC по 3-часовым интервалам; у выпуска указано '
                       'время публикации. При Kp ≥ 7 (G3) геомагнитное обрезание снижается, и если одновременно идёт протонное '
                       'событие, солнечные протоны достигают более низких широт орбиты станции; сама по себе буря — '
                       'триггер проверки условий модели, орбиты и связи, не рост дозы. Это внешний прогноз, не наблюдение.',
    'вероятность S1 и выше': 'Суточная вероятность протонного события S1 и выше по прогнозу NOAA. Относится к суткам '
                             'целиком, а не к окну ВКД; в вероятность за окно не пересчитывается.',
    'вероятность протонного события': 'Суточная вероятность протонного события по прогнозу NOAA daypre. Относится '
                                       'к суткам целиком; в вероятность за окно не пересчитывается.',
}

_COND_IMPACT = {
    'critical': ('Приоритетное предупреждение (S ≥ 3): NOAA рекомендует избегать радиационной опасности при ВКД; '
                 'окно требует срочной проверки специалистом. Команды прервать или продолжать ВКД из индекса не следуют.'),
    'limiting': ('Предупреждение: окно не выбирается автоматически и требует проверки аналитиком. '
                 'Это правило команды, не эксплуатационная норма; шкалы NOAA сами по себе не запрещают и не разрешают ВКД.'),
}
_COND_LIMITS = {
    'SEP': ('Уверенность: конец события не объявляется — принятая длительность действия задана настройкой '
            'sep_valid_hours, поэтому пересечение с окном условно. Уведомление DONKI сообщает ПОРОГ («поток > 10 pfu»), '
            'а не измеренное значение: уровень S здесь — нижняя граница, если в записи нет измеренного потока. '
            'Каналы > 10 МэВ и > 100 МэВ различны; шкала S определена по каналу ≥10 МэВ.'),
    # 'GST' собирается по сигналам условия (_gst_limits): условие «буря в окне» сводит
    # до четырёх разных сигналов, и оговорка про наблюдение не должна стоять в карточке,
    # где никакого наблюдения нет (находка третьего круга).
    'GST': '',
    'GOES': ('Уверенность: последнее наблюдение GOES с давностью; на будущие участки окна не распространяется; '
             'перенос на станцию — через обрезание, см. фактор доступности.'),
    'CONJ': 'Уверенность: качественное сообщение SOCRATES; усечённая выдача не означает отсутствия других сближений.',
}
_COND_TAIL = 'Пороги S1/S3 и G3 — шкалы NOAA SWPC; отнесение к условиям проверки — правило команды, меняется в config/settings.toml.'

# Оговорки к условию «буря в окне» — по одной на КАЖДЫЙ сигнал, который в это условие вошёл.
# Раньше здесь стоял один текст на все случаи, и в карточке прогноза прихода выброса
# (происхождение — внешний прогноз) печаталась фраза про наблюдение, которого в записи нет.
_GST_SIGNAL_LIMITS = {
    'kp_obs': ('Kp измеряется по 3-часовым интервалам; распространение последнего наблюдения на окно объявлено, '
               'не молчаливо: прогноза Kp на само окно здесь нет.'),
    'noaa_kp_forecast': 'Прогноз Kp NOAA — по 3-часовым ячейкам выпуска, как опубликован; в вероятность за окно не пересчитывается.',
    'donki_storm': 'Уровень уведомления о буре — наблюдённый Kp из тела сообщения, а не прогноз.',
    'cme_arrival': ('«Kp до N» — граница ОПУБЛИКОВАННОГО в самом уведомлении диапазона максимума Kp, а не поле прогона '
                    'модели из поздней карточки; объявленная неопределённость времени прихода не является '
                    'длительностью бури. Это внешний прогноз, не наблюдение.'),
    'unknown': 'Часть сигналов условия не отнесена к известному виду — их ограничения здесь не объявляются.',
}
_GST_LIMITS_HEAD = ('Уверенность: конец действия записи без объявленного конца — принятая длительность, настройка '
                    'event_valid_hours.')


def _gst_limits(c: Condition) -> str:
    """Ограничения условия «буря в окне» — только по тем сигналам, которые в нём есть."""
    kinds = storm_signal_kinds(c.sources_ru)
    parts = [_GST_SIGNAL_LIMITS[k] for k in ('kp_obs', 'donki_storm', 'cme_arrival', 'noaa_kp_forecast', 'unknown')
             if k in kinds]
    return ' '.join([_GST_LIMITS_HEAD] + parts)


def _period(a: WindowAssessment) -> str:
    end = a.window.start_utc + timedelta(minutes=a.window.duration_min)
    return 'окно %s — %s UTC, %d мин' % (a.window.start_utc.strftime('%Y-%m-%d %H:%M'),
                                         end.strftime('%H:%M'), a.window.duration_min)


def _traj_line(meta) -> str:
    if meta is None:
        return 'траектория: орбита недоступна'
    if meta.method == 'oem_interp':
        return 'траектория: OEM NASA/JSC (%s), создан %s, интерполяция; поле %s%s' % (
            meta.source_id, meta.created_utc.strftime('%Y-%m-%d %H:%MZ') if meta.created_utc else '?', meta.field_model,
            '; объявленная реконструкция' if meta.is_reconstruction else '')
    return 'траектория: SGP4 по TLE (%s), эпоха %s, получен %s; поле %s%s' % (
        meta.source_id, meta.epoch_utc.strftime('%Y-%m-%d %H:%MZ') if meta.epoch_utc else '?',
        meta.fetched_utc.strftime('%Y-%m-%d %H:%MZ') if meta.fetched_utc else '?', meta.field_model,
        '; объявленная реконструкция' if meta.is_reconstruction else '')


def _source_line(f: FactorValue, samples: dict[str, EnvironmentSample], meta, traj_ids: set) -> str:
    parts = []
    for rid in f.record_ids:
        s = samples.get(rid)
        if s is not None and s.source_id == 'scenario':
            parts.append('значение задано пользователем в сценарии «что если», не наблюдение (запись %s)' % s.raw_record_id)
        elif s is not None:
            pub = s.published_utc.strftime('%Y-%m-%d %H:%MZ') if s.published_utc else 'время публикации неизвестно'
            parts.append('%s, %s, момент %s, публикация %s, получено %s, %s' % (
                source_ru(s.source_id), record_ru(s.raw_record_id), s.t_utc.strftime('%Y-%m-%d %H:%MZ'), pub,
                s.fetched_utc.strftime('%Y-%m-%d %H:%MZ'), QUALITY_RU.get(s.quality, s.quality)))
        elif rid in traj_ids or rid == 'trajectory':
            parts.append('%s (запись %s)' % (_traj_line(meta), rid))
        elif rid.startswith('ost1044_A'):
            parts.append('ОСТ 134-1044-2007, прил. А (таблица AP-8 в дифференциальной форме), файл data/ost1044_belts, запись %s' % rid)
        elif rid in SOURCE_RU:
            parts.append(SOURCE_RU[rid])
        else:
            parts.append(record_ru(rid))
    if parts:
        return '; '.join(dict.fromkeys(parts))
    # записи нет: подпись по происхождению, а не «собственный расчёт» для наблюдения
    first = (f.limits_note or '').split(';')[0].strip()
    if f.kind == Kind.OBSERVATION:
        return 'наблюдения нет: %s' % (first or 'записи источника за период нет')
    if f.kind == Kind.EXTERNAL_FORECAST:
        # режим здесь не известен, поэтому запасная подпись не называет отсечку:
        # в текущем режиме отсечки нет, и слово «отсечка» было бы неправдой (О2).
        return 'выпуска прогноза нет: %s' % (first or 'источник не подключён или выпуска за период нет')
    return 'наш расчёт по траектории: %s' % (first or 'расчёт невозможен')


def _data_line(f: FactorValue) -> str:
    if f.value is None:
        return 'нет значения'
    if f.name.startswith('Kp') or f.name.startswith('прогноз Kp'):
        return 'Kp %s' % fmt_ru(f.value)
    txt = fmt_ru(f.value, f.unit)
    if f.name.startswith('ожидаемое число попаданий') and f.value > 0:
        return txt
    return txt


def _mmod_interpretation(f: FactorValue, duration_min: int) -> str:
    if f.value is None or f.value <= 0:
        return ''
    per_year = f.value * HOURS_PER_YEAR / (duration_min / 60.0)
    return ('; %s за окно ≈ одно попадание частицы ≥1 мг на 1 м² за ~%s лет непрерывной экспозиции'
            % (fmt_ru(f.value), fmt_ru(1.0 / per_year) if per_year > 0 else '—'))


def cards_for_window(a: WindowAssessment, samples: dict[str, EnvironmentSample],
                     events: Sequence[EventInterval] = (), window_index: Optional[int] = None,
                     meta=None, trajectory_ids: Sequence[str] = (), cutoff_utc: Optional[datetime] = None) -> list[Card]:
    cards: list[Card] = []
    period = _period(a)
    prefix = ('Окно %d · ' % window_index) if window_index is not None else ''
    traj_ids = set(trajectory_ids)
    ev_by_id = {e.event_id: e for e in events}
    for m in a.mechanisms:
        for f in m.factors:
            key = next((k for k in _IMPACT if f.name.startswith(k)), None)
            limits = 'покрытие %s; наличие воздействия: %s' % (COVERAGE_RU[f.coverage], PRESENCE_RU[f.presence])
            if f.horizon_utc is not None:
                limits += '; горизонт данных до %s' % f.horizon_utc.strftime('%Y-%m-%d %H:%MZ')
            if f.limits_note:
                limits += '; ' + f.limits_note
            if f.name.startswith('ожидаемое число попаданий'):
                limits += _mmod_interpretation(f, a.window.duration_min)
            if f.kind == Kind.OWN_CALCULATION:
                limits += '; уверенность зависит от точности траектории и границ модели, не от статистики'
            cards.append(Card(
                title=prefix + f.name, kind=f.kind,
                impact_ru=_IMPACT.get(key, 'значение для ВКД описано в правиле'),
                period_ru=period,
                data_ru=_data_line(f),
                source_ru=_source_line(f, samples, meta, traj_ids),
                rule_ru=f.rule_applied,
                limits_ru=limits,
                record_ids=f.record_ids,
                severity='info', window_index=window_index, window_ru=period,
            ))
        conds = m.conditions or tuple(_condition_from_text(r) for r in m.needs_check_reasons)
        for c in conds:
            cards.append(_condition_card(c, a, period, prefix, ev_by_id, samples, window_index, cutoff_utc))
    return cards


def _condition_from_text(reason: str) -> Condition:
    """Совместимость: условие только в виде текста (старые оценки без структуры)."""
    return Condition('GOES' if reason.startswith('GOES') else ('SEP' if 'протонное' in reason else 'GST'),
                     'critical' if 'приоритетное' in reason else 'limiting', reason,
                     tuple(x.strip() for x in reason.split('DONKI — ')[-1].split(', ')) if 'DONKI — ' in reason else ())


def _condition_card(c: Condition, a: WindowAssessment, period: str, prefix: str, ev_by_id: dict,
                    samples: dict, window_index: Optional[int], cutoff_utc: Optional[datetime]) -> Card:
    end = a.window.start_utc + timedelta(minutes=a.window.duration_min)
    # 2. период — интервал события/действия и его пересечение с окном
    if c.interval_utc and c.interval_utc[0] is not None:
        a0, a1 = c.interval_utc[0], c.interval_utc[1] or end
        lo, hi = max(a0, a.window.start_utc), min(a1, end)
        overlap = max(0.0, (hi - lo).total_seconds() / 60.0)
        period_ru = 'событие/действие %s — %s; пересекает окно %s — %s (%.0f мин); %s' % (
            a0.strftime('%d.%m %H:%MZ'), a1.strftime('%d.%m %H:%MZ'), lo.strftime('%H:%MZ'), hi.strftime('%H:%MZ'), overlap, period)
    else:
        period_ru = period
    # 4. источник и время публикации — по записям события
    src = list(c.sources_ru)
    pubs = []
    for rid in c.event_ids:
        e = ev_by_id.get(rid)
        s = samples.get(rid)
        if e is not None and (e.is_simulated or e.source_id == 'scenario'):
            pubs.append('%s: значение задано пользователем в сценарии «что если», не наблюдение%s'
                        % (e.event_id, ('; ' + short_note(e.note)) if e.note else ''))
        elif e is not None:
            pubs.append('%s, %s%s' % (
                record_ru(e.event_id), 'опубликовано ' + e.published_utc.strftime('%d.%m %H:%MZ') if e.published_utc else 'без времени публикации',
                ('; ' + short_note(e.note)) if e.note else ''))
        elif s is not None and s.source_id == 'scenario':
            pubs.append('%s: значение задано пользователем в сценарии «что если», не наблюдение' % s.raw_record_id)
        elif s is not None:
            pubs.append('%s, момент %s, публикация %s, получено %s' % (
                record_ru(s.raw_record_id), s.t_utc.strftime('%d.%m %H:%MZ'),
                s.published_utc.strftime('%d.%m %H:%MZ') if s.published_utc else 'в реальном времени (NOAA/GFZ)',
                s.fetched_utc.strftime('%d.%m %H:%MZ')))
    if pubs:
        shown = pubs[:6]
        src.append('записи: ' + '; '.join(shown) + ('; … всего %d (полный список — cards.json, raw/)' % len(pubs) if len(pubs) > 6 else ''))
    if c.is_simulated:
        # сценарий: «опубликовано до отсечки» писать нельзя — значение не публиковал никто
        src.append('значение задано пользователем в сценарии «что если», не наблюдение и не публикация источника')
    elif cutoff_utc is not None:
        src.append('все записи опубликованы до отсечки %s' % cutoff_utc.strftime('%Y-%m-%d %H:%MZ'))
    source_line = '; '.join(src) if src else 'источник указан в тексте условия'
    # происхождение условия — по записям, а не по типу события: уведомления REleASE/«SEP Prediction»
    # в DONKI имеют kind external_forecast и наблюдением не являются
    ev_kinds = [ev_by_id[rid].kind for rid in c.event_ids if rid in ev_by_id]
    kind = (Kind.OWN_CALCULATION if c.is_simulated else
            (Kind.OBSERVATION if c.kind in ('GOES', 'SEP') else Kind.EXTERNAL_FORECAST))
    origin_note = ''
    if not c.is_simulated and c.kind in ('GOES', 'SEP') and ev_kinds:
        if all(k == Kind.EXTERNAL_FORECAST for k in ev_kinds):
            kind = Kind.EXTERNAL_FORECAST
            origin_note = ' Происхождение: прогноз модели, не наблюдение (все записи условия — внешний прогноз).'
        elif any(k == Kind.EXTERNAL_FORECAST for k in ev_kinds):
            kind = Kind.OBSERVATION
            origin_note = ' Происхождение: часть записей — прогноз модели, не наблюдение.'
        else:
            origin_note = ' Происхождение: событие наблюдено (уведомление DONKI или карточка по прибору).'
    if c.kind == 'GST' and not c.is_simulated and any('наблюдение Kp' in s for s in c.sources_ru) and len(c.sources_ru) == 1:
        kind = Kind.OBSERVATION
    base_limits = _gst_limits(c) if c.kind == 'GST' else _COND_LIMITS.get(c.kind, '')
    limits = base_limits + origin_note
    if c.is_simulated:
        limits = ((base_limits if c.kind != 'SEP' else
                   'Уверенность: конец действия не объявлен — принятая длительность действия задана настройкой sep_valid_hours.')
                  + ' Сценарий «что если»: значение задано пользователем в сценарии, не наблюдение; '
                    'в живой кеш и воспроизведение расчёта такие значения не попадают.')
    return Card(
        title=prefix + ('Сценарий: ' if c.is_simulated else 'Условие: ') + c.text.split(': ')[0],
        kind=kind,
        impact_ru=_COND_IMPACT[c.severity],
        period_ru=period_ru,
        data_ru=c.text.split(';')[0] + ('; уровень: %s' % c.level_note if c.level_note else ''),
        source_ru=source_line,
        rule_ru='CONTRACT.md v3.1 раздел 4, пункт 2: условия дополнительной проверки; %s' % _COND_TAIL,
        limits_ru=limits,
        record_ids=c.event_ids,
        severity=c.severity, window_index=window_index, window_ru=period,
    )
