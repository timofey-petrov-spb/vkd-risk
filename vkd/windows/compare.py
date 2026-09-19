# -*- coding: utf-8 -*-
"""Сравнение окон и правило рекомендации. CONTRACT.md v3, разделы 3 и 4.

Правило (по порядку, каждый пункт виден пользователю):
  1. Охват: у обязательной линии отсутствие покрытия → рекомендации нет,
     названо, чего не хватает (по каналам); частичное — объявляется.
     R14 (решение владельца 19.09): канал, у которого прогнозного продукта с
     разрешением по сравниваемому окну НЕ СУЩЕСТВУЕТ в природе, а последнее
     наблюдение получено и не устарело сверх своего предела, объявляется ОБЩИМ
     для всех окон: он их не различает и обязательную линию не обнуляет, а
     уходит в объявленную область вывода (`declared_common_ru`). Полный отказ по
     такому каналу остаётся там, где наблюдения нет вовсе или оно устарело;
     уровень выше фона ставит условие проверки ВСЕМ окнам (исход all_need_check).
     Суточные вероятности источников в вероятность за окно не пересчитываются
     ни при каких обстоятельствах и их отсутствие правилу не мешает.
  2. Условия: приоритетное (S ≥ 3) и предупреждение — по разделу 4 п. 2
     договора; пороги — настройки с источником. Помеченные окна из
     автоматического выбора исключаются (правило команды, не норма).
  3. Сравнение по каждому механизму отдельно. Космопогода — по ДВУМ
     величинам: минуты в аномалии и флюенс захваченных протонов; окно лучше,
     только если не хуже по обеим за пределами допуска и лучше хотя бы по одной;
     минуты и флюенс указывают на разные окна → противоречие внутри механизма,
     компромисс без победителя (найдено разбором 19.09: лексикографический
     выбор по минутам называл предпочтительным окно с флюенсом в 2,5 раза выше).
  4. Сведение: предпочтительно только если не хуже по всем и лучше хотя бы
     по одному; противоречие → компромисс без победителя.
  5. Допуск равнозначности — из чувствительности (vkd/windows/sensitivity.py):
     разброс разности минут между окнами по оси порога и разброс отношения
     флюенсов по оси канала; при трёх окнах равнозначность проверяется между
     лучшим и КАЖДЫМ следующим, а не только с худшим.
"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Optional, Sequence

from vkd.assess import dose as suit_dose
from vkd.assess import debris as debris_model
from vkd.assess.coverage import gaps_ru, uncovered_minutes_by_reason
from vkd.assess.meteoroids import SHOWERS_SOURCE_RU, active_showers
from vkd.assess.trapped import BeltTable
from vkd.orbit.integration import integrate_time
from vkd.explain.format import STORM_SIGNAL_RU, fmt_ru, record_ru
from vkd.types import (Condition, Conjunction, Coverage, CoverageGap, EnvironmentSample, EventInterval, FactorValue, Kind,
                       MechanismAssessment, Presence, Recommendation, TrajectoryPoint,
                       Window, WindowAssessment)


MECH_RU = {'spaceweather': 'космопогода на траектории', 'mmod_stat': 'статистика метеороидов', 'conjunctions': 'сближения'}
KP_UNIT = ''                                     # Kp безразмерен (CONTRACT раздел 1)
NOAA_S_PFU = ((1, 10.0), (2, 100.0), (3, 1000.0), (4, 1e4), (5, 1e5))   # NOAA SWPC Space Weather Scales, ≥10 МэВ
M_P_MEV = 938.272                                # масса протона, МэВ (CODATA 2018)
SEASONAL_ROLE_RU = ('сезонная оценка зависит от даты, направления потоков и движения МКС; '
                'различимость окон проверяется при альтернативных гипотезах модели')
MMOD_ROLE_RU = ('средняя линия метеороидов без сезонного вклада различает окна по высоте и длительности; '
                'роль — абсолютная оценка и охват; календарь не даёт количественного прогноза')
MAG_STATUS_RU = {'ok': 'в сетке таблицы', 'no_model_L': 'L вне сетки 1,14…9 (сильное поле вне аномалии)',
                 'beyond_mirror': 'выше точки отражения — поток 0', 'inconsistent_BB0': 'B/B0 < 1, помечено',
                 'approximation': 'эксцентричный диполь (объявленное приближение)', 'outside_model': 'вне модели координат'}
TEAM_RULE_RU = 'правило команды, не норма'
# Пометки к сравнению величин там, где правило выбирать не имеет права. Текст должен быть честным
# сам по себе, в любом месте, куда попадёт (экран, отчёт выгрузки, JSON-снимок), а не потому,
# что экран отрежет у него хвост.
ADVISORY_NO_DATA_RU = ('это сравнение факторов, а не рекомендация: обязательная линия осталась без данных, '
                       'и окно по этому сравнению не выбирается')
ADVISORY_ALL_FLAGGED_RU = ('это справка для решения руководителя работ, а не рекомендация сервиса: правило команды '
                           'окна с условием не выбирает')
ADVISORY_SINGLE_RU = ('сравнение факторов приведено для проверки: окно выбрано правилом условий, '
                      'а не этими величинами')


def _min_ru(x: float) -> str:
    """Минуты для русского текста: целые без дробной части, доли минуты — с запятой."""
    return ('%.0f' % x) if x >= 1 or x == 0 else ('%.1f' % x).replace('.', ',')


# Умолчание для причины отсутствия численного наблюдения GOES: значит «вызывающий причину не
# назвал», и тогда отказ ссылается на блок состояния источников, а не выдумывает архив.
_GOES_ABSENT_DEFAULT_RU = 'наблюдений GOES в архиве 2024 нет'


def _cutoff_fallback_note_ru(traj: Sequence[TrajectoryPoint]) -> str:
    """Сколько точек окна ушло на запасной путь обрезания.

    Круг 11: таблица Ж.1 стала основным источником жёсткости, но за пределами её широт и при
    недоступном файле считает прежняя дипольная формула. Молчать об этом нельзя — иначе
    примечание утверждало бы таблицу там, где стоит диполь. Пустая строка означает, что весь
    участок трассы окна посчитан таблицей."""
    fallback = sum(1 for p in traj if p.cutoff_kind == 'dipole_centred')
    unknown = sum(1 for p in traj if p.cutoff_kind is None)
    parts = []
    if fallback:
        parts.append('в %d из %d точек окна значение дало не таблица, а запасная дипольная формула '
                     '(вне широт таблицы либо файл недоступен)' % (fallback, len(traj)))
    if unknown:
        parts.append('у %d из %d точек происхождение обрезания не проставлено' % (unknown, len(traj)))
    return ('; ' + '; '.join(parts)) if parts else ''


def s_level_ru(pfu: Optional[float]) -> str:
    if pfu is None:
        return 'нет данных'
    # «фон (ниже S1)», а не «ниже S1 (фон)»: уровень подставляется в тексты, где вокруг него уже
    # стоят скобки, и прежний вид давал скобку в скобке (пятый круг)
    lvl = 'фон (ниже S1)'
    for n, thr in NOAA_S_PFU:
        if pfu >= thr:
            lvl = 'S%d' % n
    return lvl


def rigidity_GV(T_MeV: float) -> float:
    """Жёсткость протона с кинетической энергией T: R = sqrt(T² + 2·T·m_p)/(Z·e), ГВ."""
    return math.sqrt(T_MeV * T_MeV + 2.0 * T_MeV * M_P_MEV) / 1000.0


# Уровень фона канала протонных событий — НИЖНЯЯ ГРАНИЦА S1 по шкале NOAA SWPC (10 pfu по
# каналу ≥10 МэВ). Отдельного порога «фон» не заводится: это то же число, что в NOAA_S_PFU и в
# настройке goes_p10_warning_pfu, и брать его надо из одного места, иначе «ниже S1» на экране и
# «фон» в правиле разойдутся при изменении настройки.
S1_PFU = NOAA_S_PFU[0][1]

# Состояния канала протонных событий по последнему наблюдению GOES (R14, ТЗ круга 11 раздел 2).
PROTON_STATE_RU = {
    'no_data': 'наблюдения нет вовсе',
    'ahead': 'время наблюдения впереди момента расчёта',
    'stale': 'наблюдение устарело сверх предела',
    'background': 'фон (ниже S1)',
    'above_background': 'выше фона (S1 и выше)',
}


def proton_channel_state(goes: Optional[EnvironmentSample], now_utc, th: Thresholds):
    """Состояние канала протонных событий по ПОСЛЕДНЕМУ наблюдению GOES: (состояние, давность).

    Величина не зависит от окна — в этом и суть R14: прогноза потока протонов с разрешением по
    окну не публикует ни один источник, поэтому последнее наблюдение относится ко ВСЕМ
    сравниваемым окнам одинаково и различать их не может.

    Предел давности — тот же `goes_max_age_min` (60 мин), который уже печатается на экране как
    «срок действия данных»; нового порога не вводится. Уровень фона — `S1_PFU`.
    Отрицательная давность (наблюдение впереди момента расчёта) — отдельное состояние, а не
    молчаливый ноль: это признак рассогласования времён, и он должен быть виден.
    """
    if goes is None or goes.value is None:
        return 'no_data', None
    age_min = (now_utc - goes.t_utc).total_seconds() / 60.0
    if age_min < 0:
        return 'ahead', age_min
    if age_min > th.goes_max_age_min:
        return 'stale', age_min
    return ('background' if goes.value < S1_PFU else 'above_background'), age_min


def proton_channel_common_ru(goes: EnvironmentSample, state: str, daily_ru: str = '') -> str:
    """Фраза объявленной области для ОБЩЕГО канала протонных событий (R14).

    Отвечает ровно на то, о чём вывод молчать не имеет права: что именно не сравнивается,
    почему это не лечится данными, каков уровень последнего наблюдения и когда оно сделано, и
    что наличие события ВНУТРИ окна остаётся неизвестным. Ни одна вероятность здесь не
    пересчитывается: суточная величина источника печатается суточной (см. `proton_daily_ru`).

    Фраза КОРОТКАЯ намеренно. Она стоит на оперативном уровне рядом с вердиктом, где на весь
    блок отведено 1250 знаков (`tests/test_ui_app.py`), и длинная версия этот бюджет съедала.
    Числа, которых здесь нет, никуда не делись и не «упрощены»: уровень в pfu, давность и
    предел стоят у самой величины — в карточке фактора «поток протонов GOES ≥10 МэВ», в её
    ограничениях, в отчёте и в снимке. Повторять их в области вывода незачем.
    """
    head = ('протонные события в сравнение не входят — прогноза потока с разрешением по окну '
            'не существует ни у одного источника; ')
    tail = '; наличие события внутри окна неизвестно'
    if state == 'background':
        body = 'на момент расчёта фон по наблюдению %s' % goes.t_utc.strftime('%H:%MZ')
    else:
        body = ('последнее наблюдение %s выше фона, условие проверки поставлено всем окнам'
                % goes.t_utc.strftime('%H:%MZ'))
    return head + body + tail + daily_ru


def proton_daily_ru(forecasts: Sequence[EnvironmentSample], win: Window,
                    cutoff_utc: Optional[datetime] = None) -> str:
    """Суточная вероятность NOAA рядом с общим каналом — КАК ЕСТЬ, с прямым указанием, что она
    суточная. В вероятность за окно она не пересчитывается ни линейно, ни как угодно иначе
    (решение команды, записанное в восьми документах; CONTRACT §9, R14).

    Отсутствие выпуска НЕ является условием применения правила: на живом экране 19.09 выпуска
    с суточной вероятностью не было вовсе, и требование его наличия означало бы, что правило не
    срабатывает никогда. Поэтому отсутствие просто называется словами и на вывод не влияет.
    """
    from vkd.integration.noaa_forecast import in_window
    hits = in_window([s for s in (forecasts or []) if s.channel_id == 's1_prob_daily'],
                     win.start_utc, win.duration_min)
    val = max((s.value for s in hits if s.value is not None), default=None)
    if val is None:
        return ('; выпуска NOAA с суточной вероятностью %s — на вывод это не влияет'
                % ('до отсечки нет' if cutoff_utc is not None else 'нет'))
    return ('; суточная вероятность S1 по NOAA %s %% — она СУТОЧНАЯ и в вероятность за окно '
            'не пересчитывается' % fmt_ru(float(val)))


def _goes_cells_in_window(goes_observations: Sequence[EnvironmentSample], start: datetime, end: datetime) -> list:
    """Ячейки архива наблюдений GOES, ФАКТИЧЕСКИ пересекающие окно (исторический разбор).
    Одно место на весь модуль: его читают и оценка окна, и перебор начал."""
    return [s for s in goes_observations if s.value is not None
            and s.valid_from_utc is not None and s.valid_to_utc is not None
            and s.valid_from_utc < end and s.valid_to_utc > start]


def kp_observation_age(kp: Optional[EnvironmentSample], now_utc, th: Thresholds):
    """(давность наблюдения Kp в минутах, свежее ли оно). Давность считается от КОНЦА интервала
    измерения: у Kp GFZ t_utc — начало трёхчасового интервала."""
    if kp is None or kp.value is None:
        return None, False
    ref = kp.valid_to_utc or kp.t_utc
    age_min = max(0.0, (now_utc - ref).total_seconds() / 60.0)
    return age_min, age_min <= th.kp_max_age_min


@dataclass(frozen=True)
class Thresholds:
    """Пороги условий. Семантика по разбору Codex 19.09 (journal/friend.md, пп. 6–8):

    * S ≥ 3 (≥1000 pfu по ≥10 МэВ) — NOAA рекомендует избегать радиационной
      опасности при ВКД: ПРИОРИТЕТНОЕ предупреждение, окно требует срочной
      проверки специалистом. Ни «прервать», ни «продолжать» из индекса не следует.
    * S1–S2 (≥10 pfu) — предупреждение о протонном событии; исключение таких
      окон из автоматического выбора — правило команды, не эксплуатационная норма.
    * Kp ≥ 7 (G3) — триггер дополнительной проверки условий модели, орбиты
      и связи; сам по себе не запрет ВКД. Правило команды.
    * сближение с TCA в окне — качественное сообщение, требует ручной оценки;
      порог промаха как вероятность попадания не вводится.
    Источник шкал: NOAA SWPC, Space Weather Scales."""
    goes_p10_warning_pfu: float = 10.0      # S1: предупреждение о протонном событии
    goes_p10_priority_pfu: float = 1000.0   # S3: приоритетное предупреждение (NOAA: EVA hazard avoidance)
    kp_check: float = 7.0                   # G3: триггер дополнительной проверки, наш выбор
    goes_max_age_min: float = 60.0          # горизонт наблюдения GOES: дальше наблюдение на окно не распространяется
    kp_max_age_min: float = 180.0           # давность наблюдения Kp (один интервал): старше — условие не ставится
    equiv_tol_min: float = 5.0              # стартовый допуск по минутам; заменяется разбросом чувствительности
    fluence_equiv_ratio: float = 1.5        # допуск по флюенсу (отношение); нижняя граница, заменяется разбросом по каналу
    e_min_MeV: float = 30.0                 # канал захваченных протонов для интеграла
    saa_B_threshold_nT: float = 24000.0     # инженерная оценка для ~420 км, в чувствительность
    tle_max_age_days: float = 3.0           # A3: обе границы горизонта не дальше этого от эпохи TLE; инженерный предел
    sep_valid_hours: float = 24.0           # действие протонного события без объявленного конца (конвенция прототипа, [history])
    event_valid_hours: float = 24.0         # действие бури и прихода выброса без объявленного конца ([history])
    cme_kp_bound: str = 'max'               # какая граница ОПУБЛИКОВАННОГО диапазона Kp прихода выброса
                                            # сравнивается с kp_check: 'max' (верхняя, консервативно) или 'min' ([history])
    meteoroid_equal_pct: float = 5.0        # порог различимости окон по линии метеороидов, % относительной разницы
                                            # числа попаданий; правило команды, не норма и не стандарт (был литералом в коде)

    @classmethod
    def from_settings(cls) -> 'Thresholds':
        """Пороги из config/settings.toml [thresholds] (Т7: настройки вне кода); неизвестные
        ключи — ошибка, чтобы опечатка в файле не превращалась в молчаливое умолчание.
        sep_valid_hours и event_valid_hours читаются из [history] (там они описаны рядом
        с другими настройками истории): первый — для протонных событий, второй — для бурь
        и прогнозов прихода выброса, у которых своя типичная длительность действия."""
        from vkd.config import section
        raw = dict(section('thresholds'))
        unknown = set(raw) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError('config/settings.toml [thresholds]: неизвестные ключи %s' % sorted(unknown))
        hist = section('history')
        for key in ('sep_valid_hours', 'event_valid_hours'):
            if key in hist and key not in raw:
                raw[key] = hist[key]
        bound = hist.get('cme_kp_range_bound', cls.cme_kp_bound)
        if bound not in ('max', 'min'):
            raise ValueError('config/settings.toml [history].cme_kp_range_bound: допустимо "max" или "min", задано %r' % bound)
        return cls(cme_kp_bound=bound, **{k: float(v) for k, v in raw.items()})


MIN_OVERLAP = timedelta(minutes=1)          # M2: публикация реестра A1 имеет секунды — пересечение в секунды не считается


def assumed_valid_hours(kind_of_event: str, th: Thresholds) -> float:
    """Принятая длительность действия записи без объявленного конца: у протонного события
    своя настройка (sep_valid_hours), у бури и прихода выброса — event_valid_hours."""
    return th.sep_valid_hours if kind_of_event == 'SEP' else th.event_valid_hours


def action_span(e: EventInterval, th: Thresholds):
    """(начало, конец) действия записи. Конец вычисляется ДО любой проверки пересечения:
    неизвестный конец — это принятая настройкой длительность, а не «действует всегда»."""
    a0 = e.valid_from_utc or e.start_utc
    if a0 is None:
        return None, None
    a1 = e.valid_to_utc or e.end_utc or (a0 + timedelta(hours=assumed_valid_hours(e.kind_of_event, th)))
    return a0, a1


def overlaps(a0, a1, start, end, min_overlap: timedelta = MIN_OVERLAP) -> bool:
    """Строгое пересечение интервала действия с интервалом окна: не меньше min_overlap.
    Одинаково применяется при отборе событий и при предварительном отборе по горизонту."""
    if a0 is None or a1 is None:
        return False
    return (min(a1, end) - max(a0, start)) >= min_overlap


def window_conditions(win: Window, th: Thresholds, now_utc, *,
                      goes: Optional[EnvironmentSample] = None,
                      goes_observations: Optional[Sequence[EnvironmentSample]] = None,
                      kp: Optional[EnvironmentSample] = None,
                      events: Sequence[EventInterval] = (),
                      forecasts: Sequence[EnvironmentSample] = (),
                      event_facts: Optional[dict] = None) -> tuple[Condition, ...]:
    """Условия проверки окна — ОДИН расчёт на весь сервис.

    Его зовут и полная оценка окна (`assess_window`), и перебор начал (`vkd/windows/scan.py`):
    правило «кандидат с условием не может стоять выше кандидата без условий» обязано опираться
    на те же условия, что потом стоят в карточке окна. Второй реализации условий в проекте нет
    и быть не должно — иначе перебор и карточка разойдутся на глазах у читателя.

    Источники условий: наблюдение GOES ≥10 МэВ, прогноз Kp NOAA, наблюдение Kp и уведомления
    DONKI (протонное событие, буря, прогноз прихода выброса). Сближения (SOCRATES) — отдельная
    необязательная линия, её условия добавляет `assess_window`: они относятся к своему механизму.

    Порядок в кортеже — приоритетные (S ≥ 3) впереди, как и прежде; внутри класса сохраняется
    порядок появления.
    """
    end = win.start_utc + timedelta(minutes=win.duration_min)
    facts_of = (event_facts or {})
    conds: list[Condition] = []
    storm_signals: list[str] = []
    storm_ids: list[str] = []
    storm_span: list[tuple] = []

    # --- прогноз Kp NOAA: ячейки выпуска, попавшие в окно (это ПРОГНОЗ, не наблюдение) ---
    if forecasts:
        from vkd.integration.noaa_forecast import in_window
        ss = [s for s in forecasts if s.channel_id == 'kp_forecast']
        hit = in_window(ss, win.start_utc, win.duration_min)
        val = max((s.value for s in hit), default=None)
        pub = max((s.published_utc for s in hit if s.published_utc), default=None)
        if val is not None and val >= th.kp_check:
            cells = [s for s in hit if s.value is not None and s.value >= th.kp_check]
            storm_signals.append('%s %s в окне (выпуск %s)' % (STORM_SIGNAL_RU['noaa_kp_forecast'], fmt_ru(val),
                                                               pub.strftime('%m-%d %H:%MZ') if pub else '?'))
            storm_ids += sorted({s.raw_record_id for s in cells})
            storm_span += [(max(s.valid_from_utc, win.start_utc), min(s.valid_to_utc, end)) for s in cells]

    # --- канал протонных событий: наблюдение GOES ---------------------------------
    if goes_observations is not None:
        # Исторический разбор: ячейки архива, фактически пересекающие окно. Прогнозом они не
        # становятся, вперёд не продлеваются; условие ставится по максимуму внутри окна.
        cells = _goes_cells_in_window(goes_observations, win.start_utc, end)
        g = max(cells, key=lambda s: s.value, default=None)
        g_val = g.value if g else None
        g_hz = g.valid_to_utc if g else None
        applies = g is not None and g_val is not None and g.t_utc < end and g_hz > win.start_utc
        from_archive, common_note = True, ''
    else:
        g, from_archive = goes, False
        g_val = goes.value if goes is not None else None
        g_hz = (goes.t_utc + timedelta(minutes=th.goes_max_age_min)) if goes is not None else None
        state, _age = proton_channel_state(goes, now_utc, th)
        # R14: канал протонных событий ОБЩИЙ для всех сравниваемых окон, пока последнее
        # наблюдение получено и не устарело. Поэтому уровень выше фона ставит условие КАЖДОМУ
        # окну, а не только тому, на которое пришёлся горизонт наблюдения: прогноза с
        # разрешением по окну не существует, и молчать об уже нештатной обстановке нельзя.
        applies = g_val is not None and state in ('background', 'above_background')
        common_note = ('' if (g is not None and g.t_utc < end and g_hz > win.start_utc) else
                       '; канал общий для всех сравниваемых окон: прогноза потока с разрешением по окну не '
                       'существует, названный уровень относится к моменту наблюдения, наличие события внутри '
                       'окна неизвестно')
    if applies:
        obs_txt = 'наблюдение %s' % g.t_utc.strftime('%m-%d %H:%MZ')
        src_goes = ('GOES ≥10 МэВ %s pfu, %s, %s; %s' % (fmt_ru(g_val), s_level_ru(g_val), obs_txt,
                    'архив iSWA, время исторической публикации неизвестно' if from_archive
                    else 'наблюдение NOAA SWPC'),)
        if g_val >= th.goes_p10_priority_pfu:
            conds.append(Condition('GOES', 'critical',
                                   'GOES ≥10 МэВ = %.3g pfu (%s, %s): S3 и выше — приоритетное, срочная проверка специалистом'
                                   % (g_val, s_level_ru(g_val), obs_txt) + common_note, (g.raw_record_id,), (g.t_utc, g_hz),
                                   s_level_ru(g_val), src_goes))
        elif g_val >= th.goes_p10_warning_pfu:
            conds.append(Condition('GOES', 'limiting',
                                   'GOES ≥10 МэВ = %.3g pfu (%s, %s): предупреждение о протонном событии — окно не выбирается '
                                   'автоматически (правило команды: S1–S2)' % (g_val, s_level_ru(g_val), obs_txt) + common_note,
                                   (g.raw_record_id,), (g.t_utc, g_hz), s_level_ru(g_val), src_goes))
    # Kp: свежее наблюдение ≥ порога — сигнал о буре; распространение на окно ОБЪЯВЛЯЕТСЯ в тексте
    kp_age_min, kp_fresh = kp_observation_age(kp, now_utc, th)
    kp_sim = False
    if kp is not None and kp.value is not None and kp_fresh and kp.value >= th.kp_check:
        kp_sim = kp.source_id == 'scenario'
        ref = kp.valid_to_utc or kp.t_utc
        storm_signals.append('%s%s %s (интервал до %s, давность %.0f мин, на окно распространено как условие проверки — '
                             'буря может продолжаться, прогноза на окно нет)'
                             % ('МОДЕЛИРУЕМОЕ ' if kp_sim else '', STORM_SIGNAL_RU['kp_obs'], fmt_ru(kp.value),
                                ref.strftime('%d.%m %H:%MZ'), kp_age_min or 0))
        storm_ids.append(kp.raw_record_id)
        storm_span.append((kp.valid_from_utc or kp.t_utc, ref))
    # Условиями становятся ТОЛЬКО протонные события, бури и прогноз прихода выброса
    # (CONTRACT v3.1 п. 4.2, предложение R10); вспышки и сами выбросы — информация в картине
    # (эксперимент на контрольной неделе: «любое событие → условие» давало 113 ложных пометок).
    # Связанные записи одного события — уведомление + карточки по приборам, несколько прогонов
    # модели — сводятся в ОДНО условие: Т3, связанные сигналы не увеличивают риск несколько раз.
    # Три сигнала об одной буре (наблюдение Kp, прогноз NOAA, уведомление DONKI, прогноз ENLIL)
    # тоже сводятся в одно условие «буря в окне» с перечнем источников (О2: не умножать риск).
    hits = {'SEP': [], 'GST': [], 'CME_ARRIVAL': []}
    for e in events:
        if e.kind_of_event not in hits:
            continue
        # конец действия вычисляется ДО проверки: событие без объявленного конца действует
        # принятую настройкой длительность, а не «до любого окна». Пересечение — не меньше минуты
        # (публикация реестра A1 имеет секунды: 14:00:13 и окно ровно через 24 ч не пересекаются).
        a0, a1 = action_span(e, th)
        if not overlaps(a0, a1, win.start_utc, end):
            continue
        hits[e.kind_of_event].append((a0, e))
    storm_sim = kp_sim
    for kind_ev, lst in hits.items():
        for cluster in _clusters(lst, gap_h=6.0 if kind_ev == 'CME_ARRIVAL' else 12.0):
            a0, evs = cluster[0][0], [e for _, e in cluster]
            # конец действия — по СОБСТВЕННОМУ началу каждой записи, а не по началу кластера;
            # объявленным конец считается только у той записи, которая и задала конец кластера
            a1, end_known = max(((action_span(e, th)[1], bool(e.valid_to_utc or e.end_utc)) for e in evs),
                                key=lambda x: x[0])
            assumed_h = assumed_valid_hours(kind_ev, th)
            assumed_key = 'sep_valid_hours' if kind_ev == 'SEP' else 'event_valid_hours'
            assumed_txt = '' if end_known else ' (конец не объявлен — принято %g ч, настройка %s)' % (assumed_h, assumed_key)
            sim = any(e.is_simulated for e in evs)
            ids = sorted({e.event_id for e in evs})
            n = len(evs)
            rec_txt = '%d %s DONKI — %s' % (n, _plural(n, 'запись', 'записи', 'записей'),
                                            ', '.join(record_ru(i) for i in ids))
            pubs = [e.published_utc for e in evs if e.published_utc]
            # год печатается, если он отличается от года окна: переанализы ENLIL поданы в 2025,
            # и «05-07 — 03-12» без года выглядит идущим назад (разбор после факта их допускает)
            pub_txt = ('публикация %s — %s' % (_pub_time(min(pubs), win.start_utc.year), _pub_time(max(pubs), win.start_utc.year))
                       if pubs else 'без времени публикации (синтетика)')
            if kind_ev == 'SEP':
                tag = 'МОДЕЛИРУЕМОЕ ' if sim else ''
                # уровень протонного события — из СТРУКТУРИРОВАННЫХ фактов уведомления (A2):
                # measured_flux_pfu, если поток измерен и опубликован, иначе flux_lower_bound_pfu —
                # НИЖНЯЯ граница порогового сообщения («поток > 10 pfu»), не измеренное значение.
                # Шкала S определена по каналу ≥10 МэВ: у канала ≥100 МэВ уровень S не называется.
                level, measured, s_energy = _sep_level(evs, facts_of)
                span_txt = '%s — %s%s' % (a0.strftime('%m-%d %H:%MZ'), a1.strftime('%m-%d %H:%MZ'), assumed_txt)
                lvl_ru = (s_level_ru(level) if (level is not None and s_energy) else
                          ('уровень не указан' if level is None else 'порог по каналу ≥%g МэВ (шкала S определена по ≥10 МэВ)' % _sep_energy(evs, facts_of)))
                bound_ru = 'измеренный поток' if measured else 'нижняя граница по тексту уведомления'
                if s_energy and level is not None and level >= th.goes_p10_priority_pfu:
                    sev, cls = 'critical', '%s (%s pfu, %s) — S3 и выше, приоритетное: срочная проверка специалистом' % (lvl_ru, fmt_ru(level), bound_ru)
                elif not s_energy:
                    sev, cls = 'limiting', lvl_ru + ' — отдельный канал, уровень S по нему не определяется'
                elif level is not None:
                    sev, cls = 'limiting', '%s (%s pfu, %s) — окно не выбирается автоматически (правило команды: S1–S2)' % (lvl_ru, fmt_ru(level), bound_ru)
                else:
                    sev, cls = 'limiting', ('уровень потока в записи не указан (DONKI публикует порог, не измерение) — предупреждение, '
                                            'окно не выбирается автоматически (правило команды)')
                conds.append(Condition('SEP', sev,
                                       '%sпротонное событие с %s пересекает окно: %s; действие %s; %s'
                                       % (tag, a0.strftime('%m-%d %H:%MZ'), cls, span_txt, rec_txt),
                                       tuple(ids), (a0, a1), lvl_ru,
                                       ('NASA DONKI, %s: %s' % (_plural(n, 'запись', 'записи', 'записей'), pub_txt),) if not sim
                                       else ('сценарий «что если»: моделируемое событие %s pfu' % fmt_ru(level),), is_simulated=sim))
            else:
                # уровень бури — из СТРУКТУРИРОВАННЫХ фактов: у уведомления о буре это наблюдённый
                # facts.kp, у прогноза прихода выброса — граница ОПУБЛИКОВАННОГО диапазона
                # facts.kp_range_min/max (R10: поля прогона enlilList поздних карточек не используются).
                # Порог тот же, что для наблюдения Kp (kp_check): буря G2 не помечает окно наравне с G3+.
                # Уровень не назван → условие ставится (консервативно), с пометкой.
                kp_max, kp_basis_ru = _storm_kp(evs, facts_of, th)
                if kp_max is not None and kp_max < th.kp_check:
                    continue          # информация в картине, не условие
                storm_sim = storm_sim or sim
                # ЛОЖНАЯ АТРИБУЦИЯ (находка четвёртого круга). Прежде связка «время прихода — граница Kp»
                # печаталась одним предложением, где время бралось как минимум по кластеру (a0), а граница
                # Kp — как максимум по кластеру (kp_max). На буре Гэннон это давало «приход 10.05 12:14Z …
                # диапазон 8–9» со ссылкой на уведомление 20240508-AL-012, в теле которого объявлен
                # диапазон 6–8: жюри открывало первоисточник и видело другое число.
                # Теперь каждая пара «приход — Kp» печатается ТОЛЬКО внутри одной записи, рядом с её
                # номером выпуска и временем публикации, а кластер называется кластером.
                per_rec = [_storm_record_ru(a_i, e, facts_of, th, win.start_utc.year) for a_i, e in cluster]
                span_txt = 'действие %s — %s%s' % (a0.strftime('%m-%d %H:%MZ'), a1.strftime('%m-%d %H:%MZ'), assumed_txt)
                mark = STORM_SIGNAL_RU['donki_storm'] if kind_ev == 'GST' else STORM_SIGNAL_RU['cme_arrival']
                head = '%s%s%s: ' % ('МОДЕЛИРУЕМОЕ ' if sim else '', mark,
                                     ', %d %s одного события' % (n, _plural(n, 'запись', 'записи', 'записей')) if n > 1 else '')
                # разделитель записей — « · », а не «;»: короткая форма причины (_short, bullet_short_ru)
                # режет строку по первой «;», и кластер не должен обрываться на середине первой записи
                chosen = ('; условие поставлено по наибольшей объявленной верхней границе — Kp до %s'
                          % fmt_ru(kp_max)) if (n > 1 and kp_max is not None) else ''
                storm_signals.append(head + ' · '.join(per_rec) + chosen + '; ' + span_txt)
                storm_ids += ids
                storm_span.append((a0, a1))
    if storm_signals:
        n_src = len(storm_signals)
        ids = tuple(dict.fromkeys(storm_ids))
        n_rec = len([i for i in ids if not i.startswith('sim_')])
        # «(1 источник)» при двух уведомлениях создавало впечатление, что весь сигнал стоит в одной
        # записи (находка четвёртого круга). Считаются и сигналы, и записи, из которых они собраны.
        n_txt = '%d %s' % (n_src, _plural(n_src, 'сигнал', 'сигнала', 'сигналов'))
        if n_rec and n_rec != n_src:
            n_txt += ' по %d %s' % (n_rec, _plural(n_rec, 'записи', 'записям', 'записям'))
        conds.append(Condition(
            'GST', 'limiting',
            '%sгеомагнитная буря Kp ≥ %.0f в окне (%s): %s — условие проверки по правилу команды (порог Kp ≥ %.0f, не норма)%s'
            % ('МОДЕЛИРУЕМОЕ ' if storm_sim else '', th.kp_check, n_txt,
               ', '.join(storm_signals), th.kp_check,
               ('; %d %s DONKI/NOAA — %s' % (n_rec, _plural(n_rec, 'запись', 'записи', 'записей'),
                                             ', '.join(record_ru(i) for i in ids if not i.startswith('sim_'))) if n_rec else '')),
            ids, (min(a for a, _ in storm_span), max(b for _, b in storm_span)) if storm_span else (),
            'Kp ≥ %.0f' % th.kp_check, tuple(storm_signals), is_simulated=storm_sim))
    order = {'critical': 0, 'limiting': 1}
    conds.sort(key=lambda c: order[c.severity])
    return tuple(conds)


def assess_window(win: Window, traj: Sequence[TrajectoryPoint], belts: BeltTable,
                  goes: Optional[EnvironmentSample], kp: Optional[EnvironmentSample],
                  conj: Sequence[Conjunction], th: Thresholds,
                  now_utc, mmod_hits: Optional[float] = None,
                  mmod_rule: str = 'ECSS-E-ST-10-04C, Grün — спецификация A5 ожидается',
                  events: Sequence[EventInterval] = (),
                  catalog_coverage: Optional[tuple] = None,
                  forecasts: Sequence[EnvironmentSample] = (),
                  mmod_cov_fraction: float = 1.0,
                  trajectory_record_ids: Sequence[str] = ('trajectory',),
                  cutoff_utc: Optional[datetime] = None,
                  goes_observations: Optional[Sequence[EnvironmentSample]] = None,
                  event_facts: Optional[dict] = None,
                  goes_absent_ru: Optional[str] = None,
                  numerical_report: Optional[dict] = None,
                  mmod_seasonal: Optional[dict] = None) -> WindowAssessment:
    """mmod_hits: ожидаемое число попаданий на пластину 1 м² за окно (B2 по
    спецификации A5). None — линия не подключена, покрытие NONE.
    events: события с интервалами (в т. ч. моделируемые); пересечение окна
    с интервалом действия или физическим интервалом даёт условие проверки.
    catalog_coverage: (начало, конец) архива уведомлений DONKI; cutoff_utc —
    отсечка строгого режима (меняет формулировки: «до отсечки», а не «за период»).
    trajectory_record_ids: идентификаторы записей орбиты (TLE/OEM, IGRF) для
    прослеживаемости собственных расчётов до исходной записи.
    event_facts: структурированные факты записи (raw_record_id -> facts адаптера A2):
    Kp наблюдения бури, канал и порог протонного события, опубликованный диапазон Kp
    прихода выброса. Правило читает их, а не русский текст заметки; разбор note —
    только запасной вариант для записей без фактов.
    goes_absent_ru: чем именно объясняется отсутствие численного наблюдения GOES
    (нет архива / исключён строгим режимом / исключён пользователем) — чтобы на экране
    не стояла одна и та же фраза для разных причин."""
    end = win.start_utc + timedelta(minutes=win.duration_min)
    facts_of = (event_facts or {})
    # Исходное наблюдение GOES запоминается ДО того, как ветка исторического разбора подменит
    # `goes` максимальной ячейкой архива: условия считает одна функция модуля, и ей нужны те же
    # входы, что пришли в оценку окна, а не промежуточное состояние этой функции.
    goes_in = goes
    # Объявленные ОБЩИЕ каналы механизма (R14): не «чего не хватает», а часть объявленной
    # области вывода — см. MechanismAssessment.declared_common_ru.
    declared_common: list[str] = []
    goes_absent_ru = goes_absent_ru or _GOES_ABSENT_DEFAULT_RU
    # Include both window endpoints and bracketing samples for clipped boundaries.
    # Values outside this temporal support are never extrapolated.
    times = [p.t_utc for p in traj]
    duration_s = (end-win.start_utc).total_seconds()
    def integrate(values, threshold=None):
        return integrate_time(times, values, win.start_utc, end,
                              max_gap_seconds=60.0, threshold=threshold)
    def coverage(result):
        return _cov(result.covered_seconds, duration_s)
    pts = [p for p in traj if win.start_utc <= p.t_utc <= end]
    track_int = integrate([1.0]*len(traj))
    cov_traj = coverage(track_int)
    lo = max(0, bisect_right(times, win.start_utc)-1)
    hi = min(len(times), bisect_left(times, end)+1)
    traj, times = traj[lo:hi], times[lo:hi]
    traj_ids = tuple(trajectory_record_ids) or ('trajectory',)
    notes: list[str] = []
    # Какие из заметок о покрытии говорят об ОТСУТСТВИИ данных по своему каналу, а какие — о
    # частичном покрытии. Разделять обязательно: покрытие механизма берётся минимумом по каналам,
    # и без этого разделения при одном пустом канале ВСЕ заметки механизма попадали в «не покрыто
    # совсем» — в том числе «флюенс: модель ОСТ покрывает 83,3 % времени окна»: отказ печатался
    # рядом с числом, которое его опровергает.
    blocking: list[str] = []

    def note(text: str, absent: bool) -> None:
        notes.append(text)
        if absent:
            blocking.append(text)

    # Linear threshold crossings resolve fractional minutes instead of counting nodes.
    saa_int = integrate([p.B_nT for p in traj], th.saa_B_threshold_nT)
    minutes_saa = (saa_int.below_threshold_seconds / 60
                   if saa_int.below_threshold_seconds is not None else None)
    cov_saa = coverage(saa_int)
    if cov_traj != Coverage.FULL:
        note('трасса: известные интервалы орбиты покрывают %.1f %% времени окна' % (100*track_int.coverage_fraction), cov_traj == Coverage.NONE)

    flux = [belts.integral_flux(p.L, p.B_over_B0, th.e_min_MeV) for p in traj]
    fl_int = integrate([r.value_per_cm2_s for r in flux])
    fluence, cov_fl = fl_int.known_integral, coverage(fl_int)
    fl_status = {}
    for p, r in zip(traj, flux):
        if win.start_utc <= p.t_utc <= end:
            fl_status[r.status] = fl_status.get(r.status, 0) + 1
    # Пропуски — В МИНУТАХ И С ПРИЧИНАМИ (разбор Codex п. 3). Доля покрытия и счётчики узлов
    # ниже остаются, но на вопрос «сколько времени окна без модели и почему» отвечает эта строка.
    fl_gaps = uncovered_minutes_by_reason(times, [r.value_per_cm2_s for r in flux],
                                          [r.status for r in flux], win.start_utc, end,
                                          max_gap_seconds=60.0)
    fl_gap_min = sum(g.minutes for g in fl_gaps)
    fl_note = ['известный вклад: интеграл трапециями по фактическим интервалам времени; '
               'оба конца интервала должны иметь модель; разрывы свыше 60 с не заполняются',
               'покрытие по времени %.1f %% (%.1f из %.1f с)' %
               (100*fl_int.coverage_fraction, fl_int.covered_seconds, duration_s)]
    if fl_gaps:
        fl_note.append('без модели %s мин из %d мин окна: %s' %
                       (_min_ru(fl_gap_min), win.duration_min, gaps_ru(fl_gaps)))
    fl_note.append('статусы узлов: ' + ', '.join('%s — %d' % (MAG_STATUS_RU.get(k, k), v) for k,v in sorted(fl_status.items())))
    # Node counts are an explanatory diagnostic, NOT the time coverage above.
    saa_flux = [r for p,r in zip(traj, flux) if win.start_utc <= p.t_utc <= end and p.in_saa]
    if saa_flux:
        known = sum(r.value_per_cm2_s is not None for r in saa_flux)
        unknown_L = sum(r.status == 'no_model_L' for r in saa_flux)
        inconsistent = sum(r.status == 'inconsistent_BB0' for r in saa_flux)
        mirror = sum(r.status == 'beyond_mirror' for r in saa_flux)
        fl_note.append('точки аномалии со значением потока по таблице ОСТ: %d из %d (диагностика узлов, не покрытие времени)' % (known, len(saa_flux)))
        if unknown_L:
            fl_note.append('у %d точек L вне сетки — вклад неизвестен' % unknown_L)
        if inconsistent:
            fl_note.append('у %d точек B/B0 < 1 — магнитные координаты несовместимы, вклад неизвестен' % inconsistent)
        if mirror:
            fl_note.append(('ещё у %d точек' if unknown_L or inconsistent else 'у %d точек') % mirror +
                           ' значение известно и равно нулю — выше точки отражения')
    if cov_fl != Coverage.FULL:
        note('флюенс: модель ОСТ покрывает %.1f %% времени окна (%s мин из %d без модели: %s); '
             'полный флюенс неизвестен, вне модели не ноль'
             % (100*fl_int.coverage_fraction, _min_ru(fl_gap_min), win.duration_min, gaps_ru(fl_gaps)),
             cov_fl == Coverage.NONE)
        fl_note.append('полный флюенс окна неизвестен; показан только известный вклад')
    if fl_int.grid_relative_delta is not None:
        fl_note.append('чувствительность к объединению соседних интервалов %.2f %% на %.1f %% времени окна; '
                       'не граница физической погрешности' %
                       (100*fl_int.grid_relative_delta, 100*fl_int.grid_compared_seconds/duration_s))
    else:
        fl_note.append('для проверки сетки нет сопоставимых соседних интервалов')
    if numerical_report is not None:
        numerical_report.update({'method': 'piecewise_linear_actual_dt_v1', 'max_gap_seconds': 60.0,
                                 'fluence': asdict(fl_int), 'saa': asdict(saa_int),
                                 'fluence_unit': 'particles/cm2',
                                 'limitations': 'partial integral is not total; grid delta is not a physical error bound'})

    # доступность канала GOES на трассе через вертикальное обрезание (CONTRACT §3, «минут доступности частиц канала»)
    cut_factors = []
    for T_MeV in (10.0, 100.0):
        R = rigidity_GV(T_MeV)
        cut_int = integrate([p.cutoff_GV for p in traj], R)
        val = cut_int.below_threshold_seconds/60 if cut_int.below_threshold_seconds is not None else None
        cut_factors.append(FactorValue(
            'минут доступности протонов ≥%.0f МэВ по обрезанию' % T_MeV, val, 'мин', Kind.OWN_CALCULATION,
            _presence(val), coverage(cut_int), traj_ids,
            'время по линейным пересечениям порога вертикальной жёсткости обрезания (таблица Ж.1 ОСТ 134-1044-2007, '
            'запасной путь — центральный диполь A3) ниже %.2f ГВ — жёсткости протона %.0f МэВ; '
            'предположение о спектре: порог по жёсткости канала, без формы спектра' % (R, T_MeV),
            ('жёсткость обрезания взята из таблицы Ж.1 ОСТ 134-1044-2007 (вертикальное обрезание, эпоха 2010, '
             'высота 450 км, пересчёт высоты по Ж.3), происхождение значения стоит в каждой точке трассы '
             '(cutoff_kind); L и B/B0 для таблиц ОСТ считает vkd.assess.magcoords по эксцентричному диполю — '
             'это разные модели, складывать их точность нельзя; коррекция по Kp и местному времени (Ж.4–Ж.6) '
             'не сделана, в бурю обрезание ниже табличного'
             + _cutoff_fallback_note_ru(traj)
             + ('; по обрезанию протоны %.0f МэВ на трассе окна недоступны — условие GOES относится к штормовому '
                'ослаблению обрезания' % T_MeV if val == 0 else ''))))

    # Coverage belongs to each source, not to a shared calendar boundary.
    # A DONKI gap cannot erase actual GOES cells or valid NOAA forecast intervals.
    event_cov = Coverage.FULL
    if catalog_coverage and (win.start_utc < catalog_coverage[0] or end > catalog_coverage[1]):
        event_cov = Coverage.NONE
        note('DONKI: окно за границей архива уведомлений; полнота событий на непокрытом участке неизвестна', True)

    goes_note, goes_val, goes_cov, goes_hz, goes_frac = 'нет данных GOES', None, Coverage.NONE, None, None
    # Покрытие канала в ЛИНИИ отличается от покрытия величины только у объявленного общего
    # канала (R14); во всех остальных ветках это одно и то же число.
    goes_line_cov = None
    goes_records = (goes.raw_record_id,) if goes else ()
    goes_rule = 'NOAA SWPC, последнее наблюдение; уровень по шкале S NOAA (S1 = 10 pfu)'
    if goes_observations is not None:
        # Retrospective observations only: intersect actual averaging cells.
        # No forward fill, no extension by goes_max_age_min and no conversion to forecast.
        from vkd.sources.goes_archive import interval_coverage
        cells = [s for s in goes_observations if s.value is not None and
                 s.valid_from_utc is not None and s.valid_to_utc is not None and
                 s.valid_from_utc < end and s.valid_to_utc > win.start_utc]
        report = interval_coverage(cells, win.start_utc, end)
        fraction = goes_frac = report['coverage_fraction']
        goes_cov = Coverage.FULL if fraction == 1 else (Coverage.PARTIAL if fraction > 0 else Coverage.NONE)
        goes = max(cells, key=lambda s: s.value, default=None)
        goes_val = goes.value if goes else None
        goes_hz = goes.valid_to_utc if goes else None
        goes_records = tuple(sorted({s.raw_record_id for s in cells}))
        goes_rule = 'Архив GOES iSWA: максимум наблюдённых 5-минутных средних внутри окна; не прогноз'
        goes_note = ('разбор: фактические ячейки GOES покрывают %.1f %% окна; '
                     'пропуски не заполняются, качество инструмента в архиве неизвестно' % (100*fraction))
        if fraction < 1:
            note('GOES: архивные наблюдения покрывают %.1f %% окна' % (100*fraction), fraction <= 0)
    elif goes is not None and goes.value is not None:
        proton_state, age_min = proton_channel_state(goes, now_utc, th)
        goes_val = goes.value
        goes_hz = goes.t_utc + timedelta(minutes=th.goes_max_age_min)
        # покрытие — доля окна внутри горизонта наблюдения [t_obs, t_obs + goes_max_age_min]:
        # наблюдение сейчас не распространяется молча на будущие участки окна (CONTRACT §4 п. 2)
        lo, hi = max(win.start_utc, goes.t_utc), min(end, goes_hz)
        frac = max(0.0, (hi - lo).total_seconds()) / (end - win.start_utc).total_seconds()
        goes_frac = frac
        # R14 (решение владельца 19.09, ТЗ круга 11 раздел 2). Канал протонных событий перестаёт
        # ОБНУЛЯТЬ обязательную линию, пока последнее наблюдение получено и не устарело сверх
        # предела: прогноза потока с разрешением по окну не существует ни у одного источника,
        # поэтому канал одинаков для всех сравниваемых окон и различить их не может. Покрытие
        # при этом остаётся ЧАСТИЧНЫМ, а не становится полным: наличие события внутри окна
        # по-прежнему неизвестно, и это написано в объявленной области вывода.
        # Устаревшее наблюдение — по-прежнему полный отказ: текущее состояние среды неизвестно.
        common_channel = proton_state in ('background', 'above_background')
        # Покрытие САМОЙ ВЕЛИЧИНЫ остаётся честным: наблюдение, горизонт которого кончился до
        # начала окна, не покрывает его ни на минуту, и в карточке фактора стоит именно это.
        goes_cov = (Coverage.NONE if proton_state in ('stale', 'ahead') else
                    (Coverage.FULL if frac == 1 else (Coverage.PARTIAL if frac > 0 else Coverage.NONE)))
        # А вот покрытие ЛИНИИ (то, что берётся минимумом по каналам и решает, есть ли вывод)
        # объявленным общим каналом не обнуляется: см. R14 выше.
        goes_line_cov = Coverage.PARTIAL if (common_channel and goes_cov == Coverage.NONE) else goes_cov
        # ЯВНАЯ ЦЕПОЧКА (разбор Codex п. 7): срок действия данных → покрываемая часть окна →
        # допустимый вывод. Наблюдение сейчас и прогноз на всё окно — разные сущности, и решает
        # это не читатель: три звена печатаются подряд, каждое со своим числом, и последнее
        # звено НАЗЫВАЕТ допустимый вывод, а не оставляет его домыслить.
        # Уровень подставляется БЕЗ внешних скобок: s_level_ru сам печатает «фон (ниже S1)»,
        # и в шаблоне «наблюдение %s (%s)» получалась скобка в скобке (пятый круг).
        unc_min = (1 - frac) * win.duration_min
        # Звено 2 обязано содержать оборот «горизонт наблюдения … покрывает N % окна» ДОСЛОВНО:
        # по нему `app.ui.obs_share_pct` узнаёт долю и ставит прочерк вместо числа у окна, которое
        # наблюдение не покрывает. Связь проверяется `tests/test_goes_chain.py`, иначе переписанная
        # фраза молча вернула бы прошлое измерение в качестве величины будущего окна.
        goes_note = ('наблюдение %s, %s, давность %.0f мин; '
                     'срок действия данных — %.0f мин после измерения (настройка команды, не норма): '
                     'горизонт наблюдения до %s покрывает %.0f %% окна, это %s мин из %d' % (
                         goes.t_utc.strftime('%Y-%m-%d %H:%MZ'), s_level_ru(goes_val), age_min,
                         th.goes_max_age_min, goes_hz.strftime('%H:%MZ'),
                         100 * frac, _min_ru(frac * win.duration_min), win.duration_min))
        if frac >= 1:
            goes_note += '; допустимый вывод: уровень потока известен по наблюдению на всё окно'
        else:
            goes_note += ('; допустимый вывод: на остальные %s мин окна наблюдение не распространяется — '
                          'оно не говорит ни «событие есть», ни «события нет»; прогноза потока протонов '
                          'на окно нет, суточная вероятность NOAA в вероятность за окно не пересчитывается'
                          % _min_ru(unc_min))
            note('GOES: наблюдение %s покрывает %.0f %% окна (%s мин из %d без данных), '
                 'прогноза потока протонов на окно нет'
                 % (goes.t_utc.strftime('%H:%MZ'), 100 * frac, _min_ru(unc_min), win.duration_min),
                 frac <= 0 and not common_channel)
        if frac <= 0.0:
            # горизонт наблюдения кончился до начала окна: наличие протонного события В ОКНЕ неизвестно.
            # Покрытие остаётся частичным (уровень и время наблюдения объявлены), но «не выявлено» писать нельзя.
            gap_h = (win.start_utc - goes.t_utc).total_seconds() / 3600.0
            # уровень назван в начале заметки и второй раз не повторяется (бриф §9.7):
            # здесь важно не какой он, а к чему относится
            goes_note += ('; на само окно наблюдения нет — наличие протонного события в окне неизвестно, '
                          'названный уровень относится к моменту наблюдения, а не к окну')
            # Блокирует вывод только тогда, когда канал НЕ объявлен общим (R14): при свежем
            # наблюдении отсутствие покрытия окна — свойство природы канала, а не пробел данных,
            # и оно уходит в объявленную область вывода, а не в причину отказа.
            note('наблюдение GOES не покрывает окно (до его начала %.0f ч)' % gap_h, not common_channel)
        if proton_state in ('stale', 'ahead'):
            # Случай 2 полного отказа (ТЗ круга 11, раздел 2): наблюдение есть, но устарело сверх
            # предела — текущее состояние среды неизвестно, значит и сказать о нём нечего.
            # Причина называется ЧИСЛОМ, а не словом «устарело».
            stale_ru = ('наблюдение GOES устарело, давность %.0f мин при пределе %.0f мин' % (age_min, th.goes_max_age_min)
                        if proton_state == 'stale' else
                        'время наблюдения GOES впереди момента расчёта на %.0f мин — времена рассогласованы' % abs(age_min))
            goes_note += '; ' + stale_ru
            note('GOES: ' + stale_ru, True)
        elif common_channel:
            # Канал объявлен ОБЩИМ: фраза уходит в объявленную область вывода отдельным полем
            # механизма, а не в «чего не хватает». Суточная вероятность NOAA дописывается как
            # есть и в вероятность за окно не пересчитывается; её отсутствие правилу не мешает.
            declared_common.append(proton_channel_common_ru(
                goes, proton_state, proton_daily_ru(forecasts, win, cutoff_utc)))
    elif any(e.kind_of_event == 'SEP' and e.published_utc is not None for e in events):
        # архива GOES нет, но есть датированные уведомления о протонных событиях: частичное покрытие канала
        goes_cov = Coverage.PARTIAL
        goes_note = goes_absent_ru + '; канал частично покрыт датированными уведомлениями DONKI о протонных событиях'
        # Два разных канала — две разные строки. Пока причина отсутствия наблюдений GOES
        # («исключён пользователем», «исключён строгим режимом») стояла в одной строке с
        # уведомлениями DONKI, исключение читалось как относящееся и к ним (девятый круг, М3).
        notes.append('GOES (численные наблюдения): ' + goes_absent_ru)
        notes.append('уведомления DONKI: канал протонных событий покрыт только ими — это другой источник')
    elif catalog_coverage:
        c0, c1 = catalog_coverage[0], catalog_coverage[1]
        if cutoff_utc is not None:
            # строгий режим: важно, что архив содержит все публикации до отсечки и период действия событий до окна
            ok = c0 <= win.start_utc - timedelta(hours=th.sep_valid_hours) and c1 >= cutoff_utc
            if ok:
                goes_cov = Coverage.PARTIAL
                goes_note = ('%s; в уведомлениях DONKI, опубликованных до отсечки %s, протонных '
                             'событий с действием в окне не объявлено; после отсечки сведения не использованы; каталог в '
                             'репозитории охватывает %s — %s' % (goes_absent_ru, cutoff_utc.strftime('%Y-%m-%d %H:%MZ'), c0.strftime('%d.%m.%Y'),
                                                                  (c1 - timedelta(minutes=1)).strftime('%d.%m.%Y')))
                notes.append('GOES (численные наблюдения): ' + goes_absent_ru)
                notes.append('уведомления DONKI: протонных событий с действием в окне до отсечки %s не объявлено'
                             % cutoff_utc.strftime('%Y-%m-%d %H:%MZ'))
            else:
                goes_note = ('архив уведомлений DONKI %s — %s не покрывает публикации до отсечки %s'
                             % (c0.strftime('%d.%m.%Y'), c1.strftime('%d.%m.%Y'), cutoff_utc.strftime('%Y-%m-%d %H:%MZ')))
                note('GOES/DONKI: ' + goes_note, True)
        elif c0 <= win.start_utc and end <= c1:
            # пустой каталог за период — это результат «событий не объявлено», а не отсутствие данных
            # (CONTRACT v3.1, правило 9); покрытие частичное, потому что самого наблюдения GOES нет
            goes_cov = Coverage.PARTIAL
            goes_note = ('%s; в уведомлениях DONKI за %s — %s протонных событий с действием '
                         'в окне не объявлено' % (goes_absent_ru, c0.strftime('%d.%m.%Y'), (c1 - timedelta(minutes=1)).strftime('%d.%m.%Y')))
            notes.append('GOES (численные наблюдения): ' + goes_absent_ru)
            notes.append('уведомления DONKI: за %s — %s протонных событий с действием в окне не объявлено'
                         % (c0.strftime('%d.%m.%Y'), (c1 - timedelta(minutes=1)).strftime('%d.%m.%Y')))
        else:
            goes_note = ('уведомления DONKI: архив до %s, окно %s — %s за его пределами — сократите период поиска или сдвиг'
                         % (c1.strftime('%Y-%m-%d %H:%MZ'), win.start_utc.strftime('%d.%m %H:%MZ'), end.strftime('%d.%m %H:%MZ')))
            note('GOES/DONKI: ' + goes_note, True)
    else:
        # тот же разбор, что у Kp ниже: слой сравнения знает только, что значения нет; исключил ли
        # источник пользователь и что именно случилось с запросом, называет блок состояния источников.
        # Круг 11: если вызывающий ВСЁ ЖЕ назвал причину отсутствия (goes_absent_ru), она ставится
        # в причину отказа — читателю отказа не приходится искать её в другом блоке экрана.
        note('GOES: наблюдения нет — %s' % (goes_absent_ru if goes_absent_ru != _GOES_ABSENT_DEFAULT_RU else
                                            'источник значения не дал, кеша нет; причина — в состоянии источников'), True)

    # --- наблюдение Kp: отдельный фактор с давностью; условие — только при свежем наблюдении ---
    kp_factor, kp_cov = None, Coverage.PARTIAL
    kp_age_min, kp_fresh = kp_observation_age(kp, now_utc, th)    # тем же правилом, что и условие
    if kp is not None and kp.value is not None:
        ref = kp.valid_to_utc or kp.t_utc
        sim = kp.source_id == 'scenario'
        kp_note = 'интервал %s — %s, давность %.0f мин (предел %.0f мин)' % (
            (kp.valid_from_utc or kp.t_utc).strftime('%d.%m %H:%MZ'), ref.strftime('%H:%MZ'), kp_age_min, th.kp_max_age_min)
        if sim:
            kp_note = 'МОДЕЛИРУЕМОЕ значение сценария «что если»; ' + kp_note
        if not kp_fresh:
            kp_note += ' — устарело: условие проверки по нему не ставится; на окно наблюдение не распространяется'
            note('Kp: последнее наблюдение %s устарело (давность %.0f мин)' % (ref.strftime('%d.%m %H:%MZ'), kp_age_min), False)
        kp_cov = Coverage.FULL if kp_fresh else Coverage.PARTIAL
        kp_factor = FactorValue('Kp, последнее наблюдение' + (' (сценарий)' if sim else ''), kp.value, KP_UNIT, kp.kind,
                                Presence.DETECTED if kp.value >= th.kp_check else Presence.NOT_DETECTED,
                                kp_cov, (kp.raw_record_id,),
                                'порог проверки Kp ≥ %.0f (G3 по шкале NOAA); %s' % (th.kp_check, TEAM_RULE_RU), kp_note,
                                horizon_utc=ref + timedelta(minutes=th.kp_max_age_min))
    else:
        # канал Kp без наблюдения: у величины покрытия нет (NONE), но у МЕХАНИЗМА это объявленное
        # частичное покрытие — как у GOES без архива. Отсутствие Kp не должно молча исчезать
        # из покрытия и делать вердикт благоприятнее, чем с наблюдением Kp.
        kp_cov = Coverage.PARTIAL
        # Слой сравнения знает только то, что значения нет; исключил ли источник пользователь —
        # знает блок состояния источников (признак state == 'off'), и он это и печатает. Пока
        # здесь стояло «источник исключён ИЛИ …», отчёт утверждал исключение источника там, где
        # тремя строками выше стояло «источники, отключённые пользователем: нет» (пятый круг).
        note('Kp: наблюдения нет — %s'
             % ('записей с доказанной публикацией до отсечки нет' if cutoff_utc else 'источник значения не дал'), False)
        kp_factor = FactorValue('Kp, последнее наблюдение', None, KP_UNIT, Kind.OBSERVATION, Presence.UNKNOWN, Coverage.NONE, (),
                                'порог проверки Kp ≥ %.0f (G3 по шкале NOAA); %s' % (th.kp_check, TEAM_RULE_RU),
                                'наблюдения Kp нет' + (' (в строгом режиме — только с доказанной публикацией до отсечки)' if cutoff_utc else ''))

    # --- прогнозы NOAA, выпущенные до отсечки (история): факторы-прогнозы, не наблюдения ---
    # Исходное разрешение источника сохраняется: суточная вероятность остаётся суточной
    # (vkd/history/README.md), прогноз Kp — по 3-часовым интервалам. Покрытие окна
    # ячейками объявляется; отсутствие прогноза не ухудшает покрытие механизма.
    # Сигналы о буре из этих же выпусков собирает `window_conditions` — здесь только ВЕЛИЧИНЫ.
    # Пока условие и фактор считались в одном цикле, вынести условия в общий код было нельзя.
    fc_factors = []
    if forecasts:
        from vkd.integration.noaa_forecast import covered_fraction, in_window
        for cid, name, unit, rule in (
                ('kp_forecast', 'прогноз Kp NOAA, максимум в окне', KP_UNIT, 'NOAA SWPC 3-day forecast, 3-часовые интервалы; выпуск с указанием времени публикации'),
                ('s1_prob_daily', 'вероятность S1 и выше за сутки, прогноз NOAA', '%', 'NOAA SWPC 3-day forecast, суточная вероятность; выпуск с указанием времени публикации'),
                ('proton_prob_daily', 'вероятность протонного события за сутки, прогноз NOAA', '%', 'NOAA SWPC daypre, суточная вероятность; выпуск с указанием времени публикации')):
            ss = [s for s in forecasts if s.channel_id == cid]
            hit = in_window(ss, win.start_utc, win.duration_min)
            frac = covered_fraction(ss, win.start_utc, win.duration_min)
            cov = Coverage.FULL if frac >= 0.95 else (Coverage.PARTIAL if frac >= 0.5 else Coverage.NONE)
            val = max((s.value for s in hit), default=None)
            pub = max((s.published_utc for s in hit if s.published_utc), default=None)
            hz = max((s.valid_to_utc for s in ss if s.valid_to_utc), default=None)
            fc_factors.append(FactorValue(
                name, val, unit, Kind.EXTERNAL_FORECAST, Presence.UNKNOWN if val is None else Presence.DETECTED, cov,
                tuple(sorted({s.raw_record_id for s in hit})), rule + (' %s' % pub.strftime('%m-%d %H:%MZ') if pub else ''),
                ('суточная вероятность источника, не вероятность за окно; ' if unit == '%' else 'прогноз, не наблюдение; ')
                + 'покрытие окна ячейками %.0f %%' % (100 * frac)
                + ('' if hit else ('; выпуска до отсечки с ячейками на окно нет' if cutoff_utc
                                   else '; выпуска с ячейками на это окно нет')),
                horizon_utc=hz))

    # --- поглощённая доза за защитой скафандра (прил. К ОСТ 134-1044-2007) ------------
    # Считается ИЗ ТОГО ЖЕ флюенса, линейно по нему, поэтому покрытие у неё то же, что у
    # флюенса, и порядок окон она менять не может. В правило сравнения окон доза НЕ входит
    # именно поэтому: она добавила бы четвёртый признак, повторяющий второй.
    # Фаза СА берётся у самой таблицы потоков — средний уровень нормировки обязан быть её же.
    dose_res = suit_dose.window_dose(fluence, e_min_MeV=th.e_min_MeV,
                                     solar_activity=getattr(belts, 'solar_activity', 'min'))
    dose_note = '; '.join(x for x in (suit_dose.explain_ru(dose_res),
                                      suit_dose.band_ru(dose_res) if dose_res.band_mGy else '',
                                      suit_dose.LIMITS_RU) if x)

    factors_m1 = (
        FactorValue('минут в аномалии', minutes_saa, 'мин', Kind.OWN_CALCULATION,
                    _presence(minutes_saa), cov_saa, traj_ids, 'время по линейным пересечениям |B| ниже порога %.0f нТл (настройка, варьируется в чувствительности)' % th.saa_B_threshold_nT,
                    'дипольная L — исследовательское приближение' if any(p.mag_status != 'ok' for p in pts) else ''),
        FactorValue('флюенс захваченных протонов ≥%g МэВ' % th.e_min_MeV, fluence, 'част./см²',
                    Kind.OWN_CALCULATION, _presence(fluence), cov_fl, traj_ids + (belts.raw_record_id,),
                    # «всенаправленный поток (%s)» с полной единицей давало «всенаправленный
                    # (…, всенаправленный (ОСТ …))»: слово дважды подряд, скобка в скобке и
                    # стандарт дважды в одном предложении (пятый круг). Стандарт назван один раз
                    # в belts.source, поэтому единица подставляется короткой формой.
                    belts.source + '; единицы потока: %s; интерполяция: %s; L и B/B0 — эксцентричный диполь (R11)'
                    % (belts.flux_unit_short_ru, belts.interpolation_ru),
                    '; '.join(fl_note)),
        FactorValue('поглощённая доза за защитой скафандра %g г/см²' % dose_res.thickness_g_cm2,
                    dose_res.value_mGy, suit_dose.DOSE_UNIT_RU, Kind.OWN_CALCULATION,
                    _presence(dose_res.value_mGy), cov_fl,
                    traj_ids + (belts.raw_record_id,) + tuple(dose_res.record_ids),
                    suit_dose.RULE_RU, dose_note),
    ) + tuple(cut_factors) + (
        FactorValue('поток протонов GOES ≥10 МэВ', goes_val, 'pfu', Kind.OBSERVATION,
                    (Presence.UNKNOWN if goes_val is None or (goes_frac is not None and goes_frac <= 0.0) else
                     (Presence.DETECTED if goes_val >= th.goes_p10_warning_pfu else Presence.NOT_DETECTED)),
                    goes_cov, goes_records, goes_rule,
                    goes_note, horizon_utc=goes_hz),
        kp_factor,
    ) + tuple(fc_factors)

    # --- механизм 2: статистика метеороидов ECSS по высоте трассы (B2 по A5) --------
    # Техногенный мусор по ГОСТ Р 25645.167-2005. Считается ЗДЕСЬ, а не передаётся извне:
    # модели нужны только времена и высоты точек, они уже есть в срезе трассы окна, и лишний
    # параметр в сигнатуре сломал бы позиционные вызовы из перебора начал.
    # Наклонение берётся как максимум модуля широты по трассе окна: за шесть часов станция
    # проходит около четырёх витков и доходит до крайних широт, то есть до наклонения. Жёсткой
    # константы 51,6 градуса здесь нет намеренно — сервис не должен предполагать конкретный аппарат.
    debris_res = None
    try:
        _lat_max = max(abs(p.lat_deg) for p in traj)
        debris_res = debris_model.debris_hits_track(
            [p.t_utc for p in traj], [p.alt_km for p in traj],
            inclination_deg=_lat_max, area_m2=1.0)
    except (ValueError, KeyError, TypeError, OSError, ZeroDivisionError) as exc:
        # Отказ модели объявляется причиной, а не подменяется нулём: ноль попаданий и
        # невозможность посчитать попадания — разные утверждения (CONTRACT, правило 9).
        debris_res = None
        debris_error_ru = 'расчёт техногенного мусора не выполнен: %s' % type(exc).__name__
    else:
        debris_error_ru = None
    mm_cov = Coverage.NONE if mmod_hits is None else (
        Coverage.PARTIAL if mmod_cov_fraction > 0 else Coverage.NONE)
    showers = active_showers(win.start_utc)
    m2 = MechanismAssessment(
        mechanism_id='mmod_stat', mandatory=True,
        factors=(FactorValue('ожидаемое число попаданий, пластина 1 м²', mmod_hits, 'шт', Kind.OWN_CALCULATION,
                             Presence.UNKNOWN if mmod_hits is None else Presence.DETECTED,
                             mm_cov, () if mmod_hits is None else ('ecss_grun:grun-ecss-2020-v1',) + traj_ids,
                             mmod_rule,
                             'расчёт невозможен: нет трассы окна' if mmod_hits is None
                             else 'природные метеороиды, случайно ориентированная пластина; неопределённость потока ×0,33…3 '
                                  '(ECSS J.2.3.2); техногенные частицы и потоки даты не включены; ' + MMOD_ROLE_RU
                                  + ('; ЧАСТИЧНО: трасса покрывает %.0f %% окна' % (100 * mmod_cov_fraction) if mmod_cov_fraction < 0.95 else '')),
                 FactorValue('техногенный мусор, попаданий в пластину 1 м² за окно',
                             None if debris_res is None else debris_res.N, 'шт', Kind.OWN_CALCULATION,
                             Presence.UNKNOWN if debris_res is None else Presence.DETECTED,
                             Coverage.NONE if debris_res is None else Coverage.PARTIAL,
                             () if debris_res is None else ('gost167:tehnogennoe-veschestvo-2005',) + traj_ids,
                             debris_error_ru or debris_res.rule,
                             debris_error_ru or (debris_res.limits_ru
                                 + '; поток усреднён по витку, поэтому окна этой величиной не различаются')),
                 FactorValue('активных метеорных потоков на дату (календарь IMO)', float(len(showers)), 'шт', Kind.OWN_CALCULATION,
                             Presence.DETECTED if showers else Presence.NOT_DETECTED, Coverage.FULL, ('imo_calendar',),
                             SHOWERS_SOURCE_RU + '; признак активности потока на дату — в число попаданий не входит '
                             '(формула Grün, ECSS 10-1): ECSS 10.2.2.2c требует учёта потоков для миссий короче 3 недель, '
                             'вклад в N не рассчитан',
                             ('активны: ' + '; '.join('%s (пик %s, ZHR до %d, активность %s)' % (s['name'], s['peak'], s['zhr_peak'], s['active'])
                                                     for s in showers) + ' — поток активен, вклад в число попаданий не рассчитан'
                              if showers else 'главных потоков по календарю нет; спорадический фон учтён моделью Grün')
                             + '; ZHR — визуальная величина, не поток на пластину; даты календаря смещаются на ±1 сут по годам')),
        coverage=mm_cov, needs_check=False,
        # Трасса покрывает окно целиком или частично; сезонный вклад не рассчитан НИГДЕ,
        # поэтому он объявляется отдельным пропуском на ВСЮ длительность окна, а не долей
        # времени (разбор Codex п. 5: совпадение с контрольным числом подтверждает формулу,
        # а не полноту модели). Ноль вместо него не подставляется.
        coverage_fraction=(None if mmod_hits is None else mmod_cov_fraction),
        coverage_gaps=((CoverageGap('сезонный вклад метеорных потоков не рассчитан (средний фон ECSS/Grün)',
                                    float(win.duration_min)),)
                       + ((CoverageGap('трасса окна известна не полностью',
                                       float(win.duration_min) * (1 - mmod_cov_fraction)),)
                          if mmod_hits is not None and mmod_cov_fraction < 1 else ())),
        coverage_notes=('метеороиды: сезонный вклад не рассчитан; средний Grün не полный охват короткой ВКД',) + (('метеороиды: трасса покрывает %.0f %% окна' % (100 * mmod_cov_fraction),) if mmod_hits is not None and mmod_cov_fraction < 1
                        else (('метеороиды: расчёт невозможен — нет трассы окна',) if mmod_hits is None else ())),
        # Блокирует только ОТСУТСТВИЕ расчёта. Незакрытый сезонный вклад — объявленное
        # ограничение модели (разбор Codex п. 5), а не пустой канал: число попаданий посчитано.
        blocking_notes=(('метеороиды: расчёт невозможен — нет трассы окна',) if mmod_hits is None else ()),
    )

    if mmod_seasonal is not None:
        from vkd.assess.seasonal import CATALOGUE_ID, METHOD_ID, LIMITS_RU
        available = bool(mmod_seasonal.get('streams_included')) and mmod_hits is not None
        model_records = ('ecss_grun:grun-ecss-2020-v1', CATALOGUE_ID, METHOD_ID) + traj_ids
        issue = mmod_seasonal.get('error') or LIMITS_RU
        def mm_factor(name, value, explanation):
            return FactorValue(name, value, 'шт', Kind.OWN_CALCULATION,
                               Presence.UNKNOWN if value is None else Presence.DETECTED,
                               Coverage.PARTIAL if available else Coverage.NONE,
                               model_records, mmod_rule, explanation)
        mm_factors = [mm_factor('ожидаемое число попаданий, пластина 1 м²', mmod_hits, issue)]
        if available:
            mm_factors += [
                mm_factor('спорадическая составляющая после исключения среднего вклада потоков',
                          mmod_seasonal['N_sporadic_adjusted'], 'Фон и потоки приведены к одной геометрии и массе ≥0,001 г.'),
                mm_factor('сезонная составляющая метеорных потоков', mmod_seasonal['N_streams'],
                          'Сумма 49 профилей с тенью Земли и относительной скоростью; входит в итоговое число.'),
                mm_factor('средняя модель Grün без сезонного перераспределения (для сравнения)',
                          mmod_seasonal['N_mean_background'], 'Контрольная величина; к итогу повторно не прибавляется.')]
        # Техногенный мусор стоит в ТОМ ЖЕ механизме, потому что это второе слагаемое одного и
        # того же воздействия — удар частицы в пластину. Складывать два числа нельзя: порог
        # частицы у стандартов разный (мусор ≥0,1 см, метеороиды ≥0,001 г), поэтому они
        # печатаются раздельно, каждое со своим порогом в правиле.
        mm_factors.append(FactorValue(
            'техногенный мусор, попаданий в пластину 1 м² за окно',
            None if debris_res is None else debris_res.N, 'шт', Kind.OWN_CALCULATION,
            Presence.UNKNOWN if debris_res is None else Presence.DETECTED,
            Coverage.NONE if debris_res is None else Coverage.PARTIAL,
            () if debris_res is None else ('gost167:tehnogennoe-veschestvo-2005',) + traj_ids,
            debris_error_ru or debris_res.rule,
            debris_error_ru or (debris_res.limits_ru
                + '; поток усреднён по витку, поэтому окна этой величиной не различаются')))
        m2 = MechanismAssessment(
            mechanism_id='mmod_stat', mandatory=True, factors=tuple(mm_factors),
            coverage=Coverage.PARTIAL if available else Coverage.NONE, needs_check=False,
            coverage_fraction=mmod_cov_fraction if available else 0.0,
            coverage_gaps=(CoverageGap(
                'неопределённость нормировки и эпохи каталога; модель инженерная' if available else issue,
                float(win.duration_min)),),
            coverage_notes=(('метеороиды: сезонный вклад рассчитан; нормировка каталога и эпоха радиантов '
                             'остаются гипотезами, годовые всплески не предсказываются') if available else issue,),
            blocking_notes=() if available else (issue,))

    # --- линия 3: сближения, необязательная ----------------------------------
    in_win = [c for c in conj if win.start_utc <= c.tca_utc < end]
    conj_conds = tuple(Condition('CONJ', 'limiting',
                                 'сообщение о сближении %s, промах %.1f км, TCA %s в окне: требует ручной оценки' % (
                                     c.other_object, c.miss_distance_km, c.tca_utc.strftime('%m-%d %H:%MZ')),
                                 (c.raw_record_id,), (c.tca_utc, c.tca_utc), 'промах %.1f км' % c.miss_distance_km,
                                 ('CelesTrak SOCRATES, прогон %s, публикация %s' % (
                                     c.run_utc.strftime('%m-%d %H:%MZ') if c.run_utc else '?',
                                     c.published_utc.strftime('%m-%d %H:%MZ') if c.published_utc else 'неизвестна'),))
                       for c in in_win)
    m3 = MechanismAssessment(
        mechanism_id='conjunctions', mandatory=False,
        factors=(FactorValue('сближений с TCA в окне', float(len(in_win)) if conj else None, 'шт',
                             Kind.EXTERNAL_FORECAST, Presence.DETECTED if in_win else (Presence.NOT_DETECTED if conj else Presence.UNKNOWN),
                             Coverage.FULL if conj else Coverage.NONE, tuple(c.raw_record_id for c in in_win),
                             'CelesTrak SOCRATES, TCA внутри окна', 'усечённая выдача не означает отсутствия других'
                             + ('' if conj else '; источник не подключён (SOCRATES): данных нет')),),
        coverage=Coverage.FULL if conj else Coverage.NONE, needs_check=bool(in_win),
        needs_check_reasons=tuple(c.text for c in conj_conds), conditions=conj_conds,
    )

    # --- условия: приоритетное / предупреждение -------------------------------
    # Считает их ОДНА функция модуля (`window_conditions`), которую зовёт и перебор начал:
    # правило «кандидат с условием не может стоять выше кандидата без условий» обязано
    # опираться ровно на те условия, что стоят в карточке окна. Здесь к ним добавляются
    # условия необязательной линии сближений — они относятся к своему механизму.
    conds = list(window_conditions(win, th, now_utc, goes=goes_in, goes_observations=goes_observations,
                                   kp=kp, events=events, forecasts=forecasts, event_facts=event_facts))
    conds += list(conj_conds)
    order = {'critical': 0, 'limiting': 1}
    conds.sort(key=lambda c: order[c.severity])
    m1 = MechanismAssessment(
        mechanism_id='spaceweather', mandatory=True, factors=factors_m1,
        coverage=_min_cov(cov_traj, cov_saa, cov_fl,
                          goes_cov if goes_line_cov is None else goes_line_cov, kp_cov, event_cov),
        needs_check=bool(conds), needs_check_reasons=tuple(c.text for c in conds),
        priority=any(c.severity == 'critical' for c in conds), conditions=tuple(conds),
        coverage_notes=tuple(notes), blocking_notes=tuple(blocking),
        declared_common_ru=tuple(declared_common),
        # Целевая величина механизма — флюенс: её покрытие по ВРЕМЕНИ и есть та доля окна,
        # о которой говорит объявленная область вывода. Пропуски — в минутах, с причинами.
        coverage_fraction=fl_int.coverage_fraction, coverage_gaps=fl_gaps)

    return WindowAssessment(
        window=win, mechanisms=(m1, m2, m3),
        coverage_declared=('космическая погода на траектории',) + (('природные метеороиды ECSS/Grün',) if mmod_hits is not None else ())
                          + (('сближения SOCRATES',) if conj else ()),
        coverage_missing=(('статистика метеороидов — не подключена',) if mmod_hits is None else ())
                         + (() if conj else ('сближения SOCRATES — нет данных',))
                         + (('всплески метеорных потоков конкретного года; абсолютная нормировка каталога не подтверждена',)
                            if mmod_seasonal and mmod_seasonal.get('streams_included') else
                            ('вклад метеорных потоков даты в число попаданий — не рассчитан',))
                         + ('техногенный мусор статистически — не включён',),
    )


def pair_not_worse(m_a: Optional[float], f_a: Optional[float], m_b: Optional[float], f_b: Optional[float],
                   tol_m: float, tol_r: float) -> bool:
    """«A не хуже B» правила (8) методики: по ОБЕИМ величинам космопогоды, каждая со своим допуском.

    m — минуты в аномалии, Φ — флюенс за окно. Невычисленная величина не делает окно ни хуже,
    ни лучше: сравнение идёт по тем величинам, которые есть, а отсутствие не заменяется нулём.

    Функция вынесена в модуль, чтобы её применяли ОБА места, где выбирается окно: сравнение
    двух-трёх окон (`recommend`) и перебор начал (`vkd/windows/scan.py`). Два правила выбора
    на одном экране — первое, что заметит читатель.
    """
    m_ok = m_a is None or m_b is None or m_a <= m_b + tol_m
    f_ok = f_a is None or f_b is None or f_a <= f_b * tol_r
    return m_ok and f_ok


def pair_better(m_a: Optional[float], f_a: Optional[float], m_b: Optional[float], f_b: Optional[float],
                tol_m: float, tol_r: float) -> bool:
    """«A лучше B» правила (8): не хуже по обеим и строго лучше хотя бы по одной, сверх допуска."""
    m_win = m_a is not None and m_b is not None and m_a < m_b - tol_m
    f_win = f_a is not None and f_b is not None and f_a * tol_r < f_b
    return pair_not_worse(m_a, f_a, m_b, f_b, tol_m, tol_r) and (m_win or f_win)


def recommend(assessments: Sequence[WindowAssessment], th: Thresholds,
              mmod_sensitivity: Optional[dict] = None) -> Recommendation:
    idx = {id(a): i + 1 for i, a in enumerate(assessments)}

    def lab(a):
        return 'окно %d (%s)' % (idx[id(a)], a.window.start_utc.strftime('%H:%MZ'))

    # 1. ОХВАТ. Полный отказ даёт только ОТСУТСТВИЕ покрытия обязательной линии (Coverage.NONE:
    #    нет орбиты, источник исключён пользователем или строгим режимом, окно за границей архива).
    #    Частичное покрытие сравнение НЕ отменяет: числа обоих окон посчитаны на одних и тех же
    #    правилах, и отказ от вывода при 82 % покрытия — это не осторожность, а отказ отвечать на
    #    вопрос постановки. Вместо отказа вывод получает ОБЪЯВЛЕННУЮ ОБЛАСТЬ (`scope_ru` ниже):
    #    по каким факторам он сделан, на какой доле окна, чего в ней нет и чего он не означает.
    #    Решение владельца 19.09 по разбору Codex п. 1 (второй его же вариант — «чёткое ограничение
    #    результата»); CONTRACT §4.1 и §12 «Кеш». Ни одно число, ни один порог и ни одна модель
    #    здесь не меняются: меняется формулировка вывода и его объявленная область.
    #    Причина называется по каналам (coverage_notes), а не общим словом «частичное».
    missing, partial = {}, {}
    for a in assessments:
        for m in a.mechanisms:
            if not m.mandatory or m.coverage == Coverage.FULL:
                continue
            name = MECH_RU.get(m.mechanism_id, m.mechanism_id)
            # Каждая заметка попадает туда, где о ней сказана правда: отказ собирается ТОЛЬКО из
            # каналов, у которых данных нет вовсе (`blocking_notes`), остальные объявляются
            # частичным покрытием. Прежде при одном пустом канале в «не покрыто совсем» уезжали
            # и заметки вида «модель ОСТ покрывает 83,3 % времени окна»: отказ стоял рядом с
            # числом, которое его опровергает.
            blocking = set(m.blocking_notes)
            texts = [('%s: %s' % (name, n), n in blocking) for n in m.coverage_notes] or [
                ('%s: покрытие %s' % (name, 'отсутствует' if m.coverage == Coverage.NONE else 'частичное'),
                 m.coverage == Coverage.NONE)]
            for t, is_blocking in texts:
                (missing if is_blocking else partial).setdefault(t, []).append(idx[id(a)])
            # Страховка от молчаливого ослабления: покрытия у механизма НЕТ, но ни одна заметка
            # не помечена блокирующей — значит канал забыли пометить. Отказ всё равно ставится,
            # и причина называется механизмом, а не пропадает вместе с вердиктом.
            if m.coverage == Coverage.NONE and not any(b for _, b in texts):
                missing.setdefault('%s: покрытие отсутствует' % name, []).append(idx[id(a)])
    n_all = len(assessments)
    # ОБЩИЕ КАНАЛЫ (R14). Одинаковая для всех окон фраза собирается один раз и идёт в
    # объявленную область вывода — не в «чего не хватает» и не в «что повлияло»: канал,
    # который окна не различает, не может быть причиной выбора и не может быть пробелом,
    # который кто-то закроет следующим выпуском источника (пробел здесь структурный).
    common_ru = tuple(dict.fromkeys(n for a in assessments for m in a.mechanisms
                                    if m.mandatory for n in m.declared_common_ru))

    def _fold(d):
        out = []
        for t, wins in d.items():
            wins = sorted(set(wins))
            out.append(t if len(wins) == n_all else '%s %s: %s' % ('окно' if len(wins) == 1 else 'окна', ', '.join(map(str, wins)), t))
        return out
    missing_l, partial_l = _fold(missing), _fold(partial)
    # 2. условия
    flagged = {id(a): [c for m in a.mechanisms for c in m.conditions] for a in assessments}
    candidates = [a for a in assessments if not flagged[id(a)]]
    # причины по помеченным окнам: короткий текст условия с номером окна, одинаковые — схлопываются
    cond_groups: dict[str, list[int]] = {}
    for a in assessments:
        for c in flagged[id(a)]:
            cond_groups.setdefault(_short(c.text), []).append(idx[id(a)])
    cond_reasons = tuple('%s %s: %s' % ('окно' if len(set(w)) == 1 else 'окна', ', '.join(map(str, sorted(set(w)))), t)
                         for t, w in cond_groups.items())

    per, best, conflict, sw_conflict, equiv_set = {}, None, False, None, []
    # Величины окон считаются по ВСЕМ окнам, а не только по кандидатам без условий.
    # Пока расчёт стоял под `if len(candidates) >= 2`, при единственном кандидате и при
    # всех окнах под условием блок вердикта оставался без единого числа, а правило могло
    # назвать предпочтительным окно, ХУДШЕЕ по целевой величине, и промолчать об этом
    # (девятый круг: 10.05.2024 06:00, период 1440, сдвиги 0/240 — выбрано окно с флюенсом
    # 2,22·10^6 част./см² при 2,06·10^6 у окна под условием).
    mins = {id(a): _sw_vals(a)[0] for a in assessments}
    fls = {id(a): _sw_vals(a)[1] for a in assessments}
    tol_m, tol_r = th.equiv_tol_min, max(1.0, th.fluence_equiv_ratio)

    def lab_f(a):
        """Метка окна вместе с пометкой условия: в справочном списке по всем окнам без неё
        помеченное окно выглядит равноправным кандидатом."""
        return lab(a) + (' под условием' if flagged[id(a)] else '')

    def vtxt_all(a):
        return '%s: %s мин в аномалии, флюенс %s' % (lab_f(a), fmt_ru(mins[id(a)]), fmt_ru(fls[id(a)], 'част./см²'))

    def _rank(seq):
        return sorted(seq, key=lambda a: (fls[id(a)] if fls[id(a)] is not None else float('inf'),
                                          mins[id(a)] if mins[id(a)] is not None else float('inf')))

    def _spread(seq):
        """Куда расходятся окна по двум величинам космопогоды. Справка, а не вывод: её печатают
        там, где правило выбирать не имеет права (все окна под условием, единственный кандидат)."""
        ms = [a for a in seq if mins[id(a)] is not None]
        fs = [a for a in seq if fls[id(a)] is not None]
        if len(seq) < 2 or not ms or not fs:
            return ''
        by_min, worst_m = min(ms, key=lambda a: mins[id(a)]), max(ms, key=lambda a: mins[id(a)])
        by_fl, worst_f = min(fs, key=lambda a: fls[id(a)]), max(fs, key=lambda a: fls[id(a)])
        if by_min.window.start_utc == by_fl.window.start_utc:
            return 'обе величины ниже у %s' % lab(by_min)
        return ('по минутам в аномалии ниже %s (%s против %s мин), по флюенсу — %s (%s против %s част./см², отношение ×%s)'
                % (lab(by_min), fmt_ru(mins[id(by_min)]), fmt_ru(mins[id(worst_m)]),
                   lab(by_fl), fmt_ru(fls[id(by_fl)]), fmt_ru(fls[id(worst_f)]),
                   ('%.2f' % (fls[id(worst_f)] / max(fls[id(by_fl)], 1e-30))).replace('.', ',')))

    # Справочный список — по НОМЕРАМ окон, а не по возрастанию флюенса: это не ранжирование,
    # и порядок «окно 2; окно 1» читается как скрытый выбор.
    all_listing = '; '.join(vtxt_all(a) for a in assessments)
    # Хвост сравнения в двух видах: рабочий (его печатает вердикт с рекомендацией) и справочный
    # (его печатает отказ). При отказе утвердительное «лучше окно 2» стояло ПЕРВОЙ строкой под
    # заголовком «Оснований для рекомендации недостаточно» — самоопровержение (девятый круг).
    sw_listing, sw_tail, sw_tail_info = None, None, None
    if len(candidates) >= 2:
        # 3. сравнение по каждому механизму отдельно; космопогода — по двум величинам

        # Правило (8) методики целиком живёт в pair_not_worse/pair_better — там же, откуда его
        # берёт перебор начал. Здесь остаётся только подстановка величин окон.
        def not_worse(a, b):
            return pair_not_worse(mins[id(a)], fls[id(a)], mins[id(b)], fls[id(b)], tol_m, tol_r)

        def better(a, b):
            return pair_better(mins[id(a)], fls[id(a)], mins[id(b)], fls[id(b)], tol_m, tol_r)

        def vtxt(a):
            return '%s: %s мин в аномалии, флюенс %s' % (lab(a), fmt_ru(mins[id(a)]), fmt_ru(fls[id(a)], 'част./см²'))
        ranked = _rank(candidates)
        dominators = [a for a in candidates if all(not_worse(a, b) for b in candidates if b is not a)]
        listing = '; '.join(vtxt(a) for a in ranked)
        sw_listing = listing
        if not dominators:
            by_min = min(candidates, key=lambda a: mins[id(a)] if mins[id(a)] is not None else float('inf'))
            by_fl = ranked[0]
            sw_conflict = 'по минутам лучше %s, по флюенсу — %s' % (lab(by_min), lab(by_fl))
            sw_tail = sw_conflict
            sw_tail_info = 'по минутам в аномалии ниже у %s, по флюенсу — у %s' % (lab(by_min), lab(by_fl))
        else:
            strict = [a for a in dominators if all(better(a, b) for b in candidates if b is not a)]
            if strict:
                best = strict[0]
                sw_tail = 'лучше %s' % lab(best)
                sw_tail_info = 'по нашему расчёту величины ниже у %s' % lab(best)
            else:
                a0 = dominators[0]
                equiv_set = [a0] + [b for b in candidates if b is not a0 and not_worse(a0, b) and not_worse(b, a0)]
                worse = [b for b in candidates if b not in equiv_set]
                sw_tail = 'равнозначны в допуске: %s' % ', '.join(lab(a) for a in equiv_set) + (
                    '; хуже: %s' % ', '.join(lab(a) for a in worse) if worse else '')
                sw_tail_info = 'в допуске величины окон не различаются: %s' % ', '.join(lab(a) for a in equiv_set) + (
                    '; выше: %s' % ', '.join(lab(a) for a in worse) if worse else '')
        per['spaceweather'] = listing + ' — ' + sw_tail
        mm_vals = {id(a): _mm_val(a) for a in candidates}
        if any(v is not None for v in mm_vals.values()):
            vals = [v for v in mm_vals.values() if v is not None]
            best_mm = min((a for a in candidates if mm_vals[id(a)] is not None), key=lambda a: mm_vals[id(a)])
            rel = (max(vals) - min(vals)) / max(min(vals), 1e-30)
            # Порог различимости окон по линии метеороидов — НЕ стандарт: это правило команды,
            # вынесенное в config/settings.toml [thresholds].meteoroid_equal_pct. Происхождение
            # печатается рядом с числом, иначе «меньше 5 %» появляется на экране ниоткуда.
            equal_pct = th.meteoroid_equal_pct
            per['mmod_stat'] = ('%s: %s попаданий против %s' % (lab(best_mm), fmt_ru(min(vals)), fmt_ru(max(vals)))
                                if 100 * rel > equal_pct else
                                'окна не различаются: разница %s %% ниже порога различимости %s %% '
                                '(%s; порог задан в config/settings.toml); %s'
                                % (('%.3f' % (100 * rel)).replace('.', ','), fmt_ru(equal_pct), TEAM_RULE_RU, SEASONAL_ROLE_RU if mmod_sensitivity else MMOD_ROLE_RU))
            # 4. сведение: противоречие механизмов вне допуска → компромисс
            conflict = 100 * rel > equal_pct and best is not None and best_mm.window.start_utc != best.window.start_utc
    mm_unstable = False
    mm_selected = False
    if mmod_sensitivity and len(candidates) >= 2:
        from vkd.assess.seasonal import comparison_sensitivity
        stability = comparison_sensitivity(
            [mmod_sensitivity.get(a.window.start_utc.isoformat(), {}) for a in candidates],
            th.meteoroid_equal_pct)
        mm_unstable = not stability['stable']
        per['mmod_stat'] = per.get('mmod_stat', '') + '; ' + stability['note_ru']
        if not mm_unstable and not sw_conflict:
            # Fusion must also work when radiation alone sees equivalent windows.
            # Retain only windows acceptable to both independent mechanisms.
            radiation_options = equiv_set or ([best] if best is not None else [])
            values = {id(a): _mm_val(a) for a in candidates}
            if radiation_options and all(v is not None for v in values.values()):
                minimum = min(values.values())
                meteor_options = {id(a) for a in candidates
                                  if values[id(a)] <= minimum*(1+th.meteoroid_equal_pct/100)}
                common = [a for a in radiation_options if id(a) in meteor_options]
                conflict = not common
                if len(common) == 1:
                    mm_selected = bool(equiv_set)
                    best, equiv_set = common[0], []
                elif common:
                    equiv_set = common
    tol = 'допуск %.0f мин по минутам и ×%.2f по флюенсу — инженерная настройка, до анализа чувствительности' % (th.equiv_tol_min, th.fluence_equiv_ratio)
    note_partial = tuple(partial_l)

    def out(**kw):
        """Один выход на все пять исходов: объявленная область считается по фактическому вердикту
        и кладётся рядом с ним — в вердикт, а не в комментарий к нему. Пропустить её, добавив
        шестой исход, теперь нельзя: другого конструктора Recommendation в recommend() нет."""
        scope, detail, scope_facts = declared_scope(assessments, kw['verdict'], candidates,
                                                   kw.get('missing') or (), common_ru)
        # Исход может принести свой состав сравнения по механизмам (отказ, «все под условием»,
        # единственный кандидат добавляют в него справочную строку) — тогда он идёт в kw.
        kw.setdefault('per_mechanism_comparison', per)
        return Recommendation(tolerance_basis=tol,
                              scope_ru=scope, scope_detail_ru=detail, scope_facts=scope_facts, **kw)
    if missing_l:
        # Хвост сравнения переписывается В САМОЙ ВЕТКЕ: источник текста обязан быть честным сам
        # по себе, а не потому, что экран отрежет лишнее. «— лучше окно 2» под заголовком
        # «Оснований для рекомендации недостаточно» — это ровно та рекомендация, в которой
        # заголовок только что отказал (девятый круг, М1).
        per_ins = dict(per)
        if sw_listing is not None and sw_tail_info:
            per_ins['spaceweather'] = '%s — %s; %s' % (sw_listing, sw_tail_info, ADVISORY_NO_DATA_RU)
        return out(preferred=None, verdict='insufficient',
                   rule_applied='п.1: отсутствует покрытие обязательной линии — ' + '; '.join(missing_l),
                   per_mechanism_comparison=per_ins, reasons=tuple(per_ins.values()) + note_partial + cond_reasons,
                   missing=tuple(missing_l))
    if mm_unstable:
        return out(preferred=None, verdict='trade_off',
                   rule_applied='сезонная линия: сравнительный вывод меняется при гипотезах модели; '
                                'однозначное предпочтение окна не установлено',
                   reasons=tuple(per.values()) + note_partial + cond_reasons)
    if sw_conflict:
        return out(preferred=None, verdict='trade_off',
                   rule_applied='п.3: внутри механизма космопогоды минуты в аномалии и флюенс указывают на разные окна — '
                                'компромисс без победителя (%s)' % sw_conflict,
                   reasons=tuple(per.values()) + note_partial + cond_reasons)
    if conflict:
        return out(preferred=None, verdict='trade_off',
                   rule_applied='п.4: механизмы указывают на разные окна — компромисс без победителя',
                   reasons=tuple(per.values()) + note_partial + cond_reasons)
    if not candidates:
        # Все окна под условием: выбирать правило не имеет права, но числа у окон разные и
        # расходятся в разные стороны, а расчёт их уже содержит. Справка печатается ОТДЕЛЬНЫМ
        # ключом и прямо помечена как не-рекомендация (девятый круг; на Гэннон 10.05 12:00
        # по флюенсу лучше окно 1, по минутам — окно 2).
        per_all = dict(per)
        spread = _spread(list(assessments))
        ref = None
        if len(assessments) >= 2:
            # ключ — тот же 'spaceweather': имя механизма переводится MECH_RU, а собственный
            # ключ вида 'spaceweather_reference' уходил в отчёт идентификатором кода (О5)
            ref = '%s%s — %s' % (all_listing, ('. ' + spread) if spread else '', ADVISORY_ALL_FLAGGED_RU)
            per_all['spaceweather'] = ref
        return out(preferred=None, verdict='all_need_check', rule_applied='п.2: все окна под условием',
                   per_mechanism_comparison=per_all,
                   reasons=cond_reasons + note_partial + ((ref,) if ref else ()))
    if len(candidates) == 1:
        c = candidates[0]
        # Единственный кандидат: выбор сделан правилом условий, а не сравнением величин, и это
        # говорится вслух. Раньше per оставался пустым — в блоке вердикта не было ни одного
        # числа, и сервис мог рекомендовать окно, худшее по флюенсу, ничего об этом не сказав.
        per_one = dict(per)
        per_one['spaceweather'] = all_listing + ' — ' + ADVISORY_SINGLE_RU
        rule = 'п.2: %s — единственное без условий; выбор сделан правилом условий, а не сравнением величин' % lab(c)
        f_c = fls[id(c)]
        worse_than = [b for b in assessments if b is not c and fls[id(b)] is not None and f_c is not None and fls[id(b)] < f_c]
        if worse_than:
            b0 = min(worse_than, key=lambda b: fls[id(b)])
            rule += ('; выбранное окно хуже по флюенсу, чем %s (%s против %s част./см²), — это следствие правила условий, '
                     'а не преимущество обстановки' % (lab_f(b0), fmt_ru(f_c), fmt_ru(fls[id(b0)])))
        return out(preferred=c.window, verdict='preferred', rule_applied=rule,
                   per_mechanism_comparison=per_one,
                   reasons=tuple(per_one.values()) + cond_reasons + note_partial)
    if mm_selected:
        return out(preferred=best.window, verdict='preferred',
                   rule_applied='п.4–5: космопогода не различает кандидатов в допуске; '
                                '%s имеет меньшую сезонную оценку метеороидов, '
                                'сравнение устойчиво при проверенных гипотезах' % lab(best),
                   reasons=tuple(per.values()) + note_partial + cond_reasons)
    if equiv_set:
        return out(preferred=None, verdict='equivalent',
                   rule_applied='п.5: %s равнозначны — разница минут и флюенса внутри допуска (%.0f мин, ×%.2f)' % (
                       ', '.join(lab(a) for a in equiv_set), th.equiv_tol_min, th.fluence_equiv_ratio),
                   reasons=tuple(per.values()) + note_partial + cond_reasons)
    # Хвост «не хуже по флюенсу и минутам» печатался БЕЗУСЛОВНО и без допуска, тогда как у
    # выбранного окна флюенс может быть ВЫШЕ (на тихой дате 1,74·10⁶ против 1,65·10⁶): экран
    # утверждал то, что опровергалось числами двумя строками ниже (находка четвёртого круга).
    # Теперь обе части собираются из вычисленного, с числами, единицами и допуском.
    m_best, f_best = mins[id(best)], fls[id(best)]
    others = [b for b in candidates if b is not best]
    m_ref = min((mins[id(b)] for b in others if mins[id(b)] is not None), default=None)
    f_ref = min((fls[id(b)] for b in others if fls[id(b)] is not None), default=None)
    why = []
    if m_best is None or m_ref is None:
        why.append('минуты в аномалии не вычислены — по ним окна не сравнивались')
    elif m_best < m_ref:
        why.append('меньше по минутам в аномалии (%s против %s мин)' % (fmt_ru(m_best), fmt_ru(m_ref)))
    elif m_best == m_ref:
        why.append('по минутам в аномалии одинаково (%s мин у обоих)' % fmt_ru(m_best))
    else:
        # «не хуже по минутам» при числах, показывающих обратное, читается как подгонка вывода
        # под ответ (девятый круг). Проигрыш называется проигрышем, и рядом сказано, почему он
        # не считается различием. Раз выбранное окно всё же строго лучшее, выигрыш — по флюенсу.
        why.append('по минутам в аномалии выбранное окно хуже на %s мин (%s против %s мин) — в пределах допуска '
                   '%s мин, различием не считается; выбор сделан по флюенсу'
                   % (fmt_ru(m_best - m_ref), fmt_ru(m_best), fmt_ru(m_ref), fmt_ru(th.equiv_tol_min)))
    if f_best is None or f_ref is None or not f_ref:
        why.append('флюенс не вычислен — по нему окна не сравнивались')
    elif f_best <= f_ref:
        why.append('флюенс ниже (%s против %s част./см², отношение ×%s)'
                   % (fmt_ru(f_best), fmt_ru(f_ref), ('%.2f' % (f_best / f_ref)).replace('.', ',')))
    else:
        why.append('по флюенсу выбранное окно выше на %s %% (%s против %s част./см², отношение ×%s) — внутри допуска '
                   '×%s, различием не считается; выбор сделан по минутам в аномалии'
                   % (('%.0f' % (100.0 * (f_best / f_ref - 1.0))).replace('.', ','), fmt_ru(f_best), fmt_ru(f_ref),
                      ('%.2f' % (f_best / f_ref)).replace('.', ','),
                      ('%.2f' % max(1.0, th.fluence_equiv_ratio)).replace('.', ',')))
    return out(preferred=best.window, verdict='preferred',
               rule_applied='п.3–4: %s лучше по космопогоде — %s; линия метеороидов не противоречит'
                            % (lab(best), '; '.join(why)) + ('; покрытие частичное — объявлено' if partial_l else ''),
               reasons=tuple(per.values()) + cond_reasons + note_partial)


# Чего вывод НЕ означает — один и тот же текст на экране, в снимке и в выгрузке, чтобы его
# нельзя было потерять при пересказе. Слова «безопасно» здесь нет и быть не может (CONTRACT §9).
SCOPE_NOT_RU = 'это сравнение рассчитанных факторов, а не заключение о полном риске ВКД'
# Перечень того, чего в «полном риске» нет. В короткой форме его нет не потому, что он
# необязателен, а потому, что он всегда один и тот же и стоит рядом — в подробной форме,
# в отчёте и во вкладке «Методика». Короткая форма уже говорит главное: это НЕ полный риск.
SCOPE_NOT_LIST_RU = 'вероятность разгерметизации, доза на экипаж и техногенный мусор не считаются'
# Область ОТКАЗА. «Не покрыта совсем» обязано относиться к названным окнам, а не ко всем сразу:
# в живом режиме бывает, что у окна 1 наблюдение GOES покрывает 13 % окна, а у окна 2 — 0 %.
# Общая фраза «обязательная линия не покрыта совсем» стояла бы рядом с числом 13 %, которое её
# опровергает (находка при приёмке восьмого круга). Поэтому окна называются, а подробная форма
# перечисляет те же причины, что лежат в `missing` снимка, — одним и тем же текстом.
SCOPE_REFUSAL_TAIL_RU = 'это отказ от вывода, а не оценка риска'


def _refusal_scope(assessments: Sequence[WindowAssessment], blocking: Sequence[str],
                   common_tail: str = '') -> tuple[str, str]:
    blocked = [i for i, a in enumerate(assessments, 1)
               if any(m.mandatory and (m.coverage == Coverage.NONE
                                       or (set(m.blocking_notes) & set(m.coverage_notes)))
                      for m in a.mechanisms)]
    if blocked and len(blocked) == len(assessments):
        where = ' у каждого сравниваемого окна'
    elif len(blocked) == 1:
        where = ' у окна %d' % blocked[0]
    elif blocked:
        where = ' у окон %s' % ', '.join(str(i) for i in blocked)
    else:
        where = ''
    short = ('обязательная линия не покрыта совсем%s, поэтому факторы окон не сопоставляются; %s'
             % (where, SCOPE_REFUSAL_TAIL_RU)) + common_tail
    # Причины отделяются словом, а не двоеточием: между заголовком отказа и их перечнем теперь
    # может стоять фраза об общем канале (R14), и «…неизвестно: GOES: наблюдения нет» читалось бы
    # как продолжение этой фразы. Короткая форма при этом остаётся НАЧАЛОМ подробной.
    detail = short + (' — причины: ' + '; '.join(dict.fromkeys(blocking)) if blocking else '')
    return short, detail


def declared_scope(assessments: Sequence[WindowAssessment], verdict: str,
                   compared, blocking: Sequence[str] = (),
                   common_ru: Sequence[str] = ()) -> tuple[str, str, tuple[tuple[str, str], ...]]:
    """ОБЪЯВЛЕННАЯ ОБЛАСТЬ ВЫВОДА (разбор Codex п. 1, решение владельца 19.09).

    Собирается ИЗ ВЫЧИСЛЕННОГО, а не из заготовленной фразы: доля окна берётся из того же
    `coverage_fraction`, что и интеграл, минуты пропусков — из тех же `coverage_gaps`, что
    печатаются у величины. Поэтому строку нельзя рассогласовать с числами рядом.

    Отвечает на четыре вопроса ровно в этом порядке: по каким факторам сделан вывод,
    какая доля окна покрыта моделью, какие пропуски и почему, чего вывод НЕ означает.

    Возвращает ТРИ формы одного и того же, а не три разных утверждения:
    короткую (доля окна и чего вывод не означает) — она стоит на экране всегда;
    подробную (имена факторов и пропуски по причинам с минутами) — на профессиональном
    уровне, в свёртке оперативного и в отчёте; и по пунктам — в снимок и выгрузку.
    Короткая обязана быть началом подробной, иначе на одном экране окажутся две области.

    `compared` — окна, которые действительно сравнивались (кандидаты без условий); пусто,
    если до сравнения не дошло.

    `common_ru` — ОБЩИЕ каналы (R14): то, что по природе канала не различает окна. Эта часть
    области печатается и при отказе тоже: отказ по другому каналу не отменяет того, что про
    протонные события мы всё равно не можем сказать ничего с разрешением по окну.
    """
    facts: list[tuple[str, str]] = []
    names: list[str] = []
    for a in assessments:                       # имена факторов берутся у окна, а не пишутся руками
        for m in a.mechanisms:
            if not m.mandatory:
                continue
            for x in m.factors:
                if x.value is not None and (x.name == 'минут в аномалии' or x.name.startswith('флюенс')
                                            or x.name.startswith('ожидаемое число')) and x.name not in names:
                    names.append(x.name)
    fracs = [m.coverage_fraction for a in assessments for m in a.mechanisms
             if m.mechanism_id == 'spaceweather' and m.coverage_fraction is not None]
    gaps: dict[str, list[float]] = {}
    for a in assessments:
        for m in a.mechanisms:
            if m.mandatory:
                for g in m.coverage_gaps:
                    gaps.setdefault(g.reason_ru, []).append(g.minutes)

    common_tail = ('; ' + '; '.join(common_ru)) if common_ru else ''
    if verdict == 'insufficient':
        short, detail = _refusal_scope(assessments, blocking, common_tail)
        facts.append(('вывод не сделан', detail))
        if common_ru:
            facts.append(('общие каналы, окна не различающие', '; '.join(common_ru)))
        return short, detail, tuple(facts)

    if names:
        facts.append(('учтённые факторы', ', '.join(names)))
    cov_txt = ''
    if fracs:
        lo, hi = 100 * min(fracs), 100 * max(fracs)
        cov_txt = ('%.0f %%' % lo) if abs(hi - lo) < 0.5 else ('%.0f…%.0f %%' % (lo, hi))
        facts.append(('доля окна с моделью флюенса', cov_txt))
    gap_txt = '; '.join('%s мин — %s' % (_min_ru(min(v)) if abs(max(v) - min(v)) < 0.5
                                         else '%s…%s' % (_min_ru(min(v)), _min_ru(max(v))), r)
                        for r, v in sorted(gaps.items(), key=lambda kv: -max(kv[1])))
    if gap_txt:
        facts.append(('пропуски модели', gap_txt))
    if common_ru:
        facts.append(('общие каналы, окна не различающие', '; '.join(common_ru)))
    facts.append(('вывод не означает', SCOPE_NOT_RU + ': ' + SCOPE_NOT_LIST_RU))

    short = 'по учтённым факторам'
    if cov_txt:
        short += ' при покрытии модели %s окна' % cov_txt
    detail = short + (' (%s)' % ', '.join(names) if names else '')
    if gap_txt:
        detail += '; без модели ' + gap_txt
    return (short + '; ' + SCOPE_NOT_RU + common_tail,
            detail + '; ' + SCOPE_NOT_RU + ': ' + SCOPE_NOT_LIST_RU + common_tail,
            tuple(facts))


def _sw_vals(a: WindowAssessment):
    f = {x.name: x.value for x in a.mechanisms[0].factors}
    return f.get('минут в аномалии'), next((v for n, v in f.items() if n.startswith('флюенс')), None)


def _mm_val(a: WindowAssessment):
    return next((x.value for m in a.mechanisms if m.mechanism_id == 'mmod_stat'
                 for x in m.factors if x.name.startswith('ожидаемое число')), None)


def _short(text: str) -> str:
    """Короткая причина: до списка записей (первая «;»)."""
    return text.split(';')[0].strip()


def _clusters(items, gap_h: float):
    """Группы (a0, event) по близости начала: новый кластер, когда разрыв больше gap_h часов."""
    out, cur = [], []
    for a0, e in sorted(items, key=lambda x: x[0]):
        if cur and (a0 - cur[-1][0]) > timedelta(hours=gap_h):
            out.append(cur)
            cur = []
        cur.append((a0, e))
    if cur:
        out.append(cur)
    return out


def _plural(n: int, one: str, few: str, many: str) -> str:
    if 11 <= n % 100 <= 19:
        return many
    return one if n % 10 == 1 else (few if 2 <= n % 10 <= 4 else many)


def _pub_time(t: datetime, ref_year: int) -> str:
    """Время публикации: год печатается, если он отличается от года окна."""
    return t.strftime('%Y-%m-%d %H:%MZ') if t.year != ref_year else t.strftime('%m-%d %H:%MZ')


def _kp_from_note(note: Optional[str]) -> Optional[float]:
    """ЗАПАСНОЙ разбор текста: только для записей без структурированных фактов (старые заметки)."""
    m = re.search(r'Kp до (\d+(?:[.,]\d+)?)', note or '')
    return float(m.group(1).replace(',', '.')) if m else None


def _pfu_from_note(note: Optional[str]) -> Optional[float]:
    """ЗАПАСНОЙ разбор текста записи: «… 1e+04 pfu», «pfu=100», «exceeds 10 pfu»."""
    m = re.search(r'(\d+(?:[.,]\d+)?(?:e[+-]?\d+)?)\s*pfu', note or '', re.I) or re.search(r'pfu\s*=\s*(\d+(?:[.,]\d+)?(?:e[+-]?\d+)?)', note or '', re.I)
    return float(m.group(1).replace(',', '.')) if m else None


def _facts(e: EventInterval, facts_of: dict) -> dict:
    f = facts_of.get(e.raw_record_id) or facts_of.get(e.event_id) or {}
    return f if isinstance(f, dict) else {}


def _sep_energy(evs: Sequence[EventInterval], facts_of: dict) -> float:
    """Нижняя граница энергетического канала протонного события по фактам записей (МэВ)."""
    vals = [_facts(e, facts_of).get('energy_lower_bound_MeV') for e in evs]
    vals = [float(v) for v in vals if v is not None]
    return min(vals) if vals else 10.0


def _sep_level(evs: Sequence[EventInterval], facts_of: dict):
    """(уровень pfu, измерен ли он, относится ли канал к шкале S ≥10 МэВ).

    Порядок: измеренный поток из фактов → нижняя граница порога из фактов → запасной
    разбор текста (записи без фактов). Порог сообщения НЕ выдаётся за измерение:
    об этом говорит второй элемент кортежа, он печатается в тексте условия.
    """
    s_events = [e for e in evs if _facts(e, facts_of).get('energy_lower_bound_MeV', 10) == 10]
    if s_events:
        evs = s_events  # never combine a >100 MeV bound with the >10 MeV S scale
    measured = [float(v) for v in (_facts(e, facts_of).get('measured_flux_pfu') for e in evs) if v is not None]
    if measured:
        return max(measured), True, _sep_energy(evs, facts_of) <= 10.0
    bounds = [float(v) for v in (_facts(e, facts_of).get('flux_lower_bound_pfu') for e in evs) if v is not None]
    if bounds:
        return max(bounds), False, _sep_energy(evs, facts_of) <= 10.0
    from_note = [v for v in (_pfu_from_note(e.note) for e in evs) if v is not None]
    return (max(from_note) if from_note else None), False, True


def _storm_kp_one(e: EventInterval, facts_of: dict, th: Thresholds):
    """(Kp ОДНОЙ записи, чем он обоснован) — строго по структурированным фактам этой записи.

    Уведомление о буре: facts.kp — наблюдённый индекс за указанный 3-часовой интервал.
    Уведомление о приходе выброса: граница ОПУБЛИКОВАННОГО диапазона максимума Kp
    (facts.kp_range_min/max, kp_basis = published_notification_range); какая именно
    граница — настройка [history].cme_kp_range_bound, по умолчанию верхняя.

    Величины разных записей здесь не смешиваются: это и есть место, где обеспечивается
    правило «каждое число прослеживается до записи, из тела которой оно взято» (О4).
    """
    f = _facts(e, facts_of)
    if e.kind_of_event == 'CME_ARRIVAL':
        key = 'kp_range_max' if th.cme_kp_bound == 'max' else 'kp_range_min'
        v = f.get(key)
        if v is not None:
            label = 'верхняя' if th.cme_kp_bound == 'max' else 'нижняя'
            bounds = (f.get('kp_range_min'), f.get('kp_range_max'))
            span = (' %g–%g' % bounds) if all(x is not None for x in bounds) else ' (вторая граница не указана)'
            return float(v), label + ' граница опубликованного диапазона' + span
    elif f.get('kp') is not None:
        return float(f['kp']), 'наблюдённый Kp уведомления'
    v = _kp_from_note(e.note)
    if v is not None:
        return v, 'по тексту записи (запасной разбор)'
    return None, ''


def _storm_kp(evs: Sequence[EventInterval], facts_of: dict, th: Thresholds):
    """(Kp для сравнения с порогом, чем он обоснован) — наибольший по кластеру.

    Для ПЕЧАТИ эта пара не годится: значение и обоснование берутся из одной записи, а время
    прихода в кластере — из другой. Печать идёт через _storm_record_ru по каждой записи.
    """
    got = [(v, b) for v, b in (_storm_kp_one(e, facts_of, th) for e in evs) if v is not None]
    if not got:
        return None, ''
    return max(got, key=lambda x: x[0])


def _storm_record_ru(a_i: datetime, e: EventInterval, facts_of: dict, th: Thresholds, ref_year: int) -> str:
    """Одна запись кластера бури: её собственное время, её собственный Kp, её номер выпуска
    и её время публикации — в одной скобке, чтобы число и ссылка не расходились."""
    v, basis = _storm_kp_one(e, facts_of, th)
    kp_txt = ('Kp до %s' % fmt_ru(v)) if v is not None else 'уровень Kp в записи не назван'
    pub = ('публикация %s' % _pub_time(e.published_utc, ref_year)) if e.published_utc else 'без времени публикации'
    src = record_ru(e.event_id, with_kind=False)
    when = 'приход %s' % a_i.strftime('%m-%d %H:%MZ') if e.kind_of_event == 'CME_ARRIVAL' else 'начало %s' % a_i.strftime('%m-%d %H:%MZ')
    return '%s, %s (%s)' % (when, kp_txt, ', '.join(x for x in (basis, src, pub) if x))


def _cov(n_ok: int, n_all: int) -> Coverage:
    """Полное покрытие требует всех точек; любой ненулевой неполный охват — частичный."""
    if n_all == 0 or n_ok == 0:
        return Coverage.NONE
    frac = n_ok / n_all
    return Coverage.FULL if n_ok == n_all else Coverage.PARTIAL


def _min_cov(*cs: Coverage) -> Coverage:
    order = {Coverage.FULL: 2, Coverage.PARTIAL: 1, Coverage.NONE: 0}
    return min(cs, key=lambda c: order[c])


def _presence(v) -> Presence:
    return Presence.UNKNOWN if v is None else (Presence.DETECTED if v > 0 else Presence.NOT_DETECTED)
