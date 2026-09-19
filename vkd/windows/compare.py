# -*- coding: utf-8 -*-
"""Сравнение окон и правило рекомендации. CONTRACT.md v3, разделы 3 и 4.

Правило (по порядку, каждый пункт виден пользователю):
  1. Охват: у обязательной линии отсутствие покрытия → рекомендации нет,
     названо, чего не хватает (по каналам); частичное — объявляется.
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
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Sequence

from vkd.assess.meteoroids import SHOWERS_SOURCE_RU, active_showers
from vkd.assess.trapped import BeltTable
from vkd.explain.format import STORM_SIGNAL_RU, fmt_ru, record_ru
from vkd.types import (Condition, Conjunction, Coverage, EnvironmentSample, EventInterval, FactorValue, Kind,
                       MechanismAssessment, Presence, Recommendation, TrajectoryPoint,
                       Window, WindowAssessment)


MECH_RU = {'spaceweather': 'космопогода на траектории', 'mmod_stat': 'статистика метеороидов', 'conjunctions': 'сближения'}
KP_UNIT = ''                                     # Kp безразмерен (CONTRACT раздел 1)
NOAA_S_PFU = ((1, 10.0), (2, 100.0), (3, 1000.0), (4, 1e4), (5, 1e5))   # NOAA SWPC Space Weather Scales, ≥10 МэВ
M_P_MEV = 938.272                                # масса протона, МэВ (CODATA 2018)
MMOD_ROLE_RU = ('линия метеороидов различает окна только по высоте и длительности; при равной длительности '
                'на орбите МКС различие меньше 0,01 % — её роль здесь абсолютная оценка и охват, не выбор окна')
MAG_STATUS_RU = {'ok': 'в сетке таблицы', 'no_model_L': 'L вне сетки 1,14…9 (сильное поле вне аномалии)',
                 'beyond_mirror': 'выше точки отражения — поток 0', 'inconsistent_BB0': 'B/B0 < 1, помечено',
                 'approximation': 'эксцентричный диполь (объявленное приближение)', 'outside_model': 'вне модели координат'}
TEAM_RULE_RU = 'правило команды, не норма'


def s_level_ru(pfu: Optional[float]) -> str:
    if pfu is None:
        return 'нет данных'
    lvl = 'ниже S1 (фон)'
    for n, thr in NOAA_S_PFU:
        if pfu >= thr:
            lvl = 'S%d' % n
    return lvl


def rigidity_GV(T_MeV: float) -> float:
    """Жёсткость протона с кинетической энергией T: R = sqrt(T² + 2·T·m_p)/(Z·e), ГВ."""
    return math.sqrt(T_MeV * T_MeV + 2.0 * T_MeV * M_P_MEV) / 1000.0


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
                  event_facts: Optional[dict] = None,
                  goes_absent_ru: Optional[str] = None) -> WindowAssessment:
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
    goes_absent_ru = goes_absent_ru or 'наблюдений GOES в архиве 2024 нет'
    pts = [p for p in traj if win.start_utc <= p.t_utc < end]
    step_min = 1.0
    expected = win.duration_min / step_min
    cov_traj = Coverage.FULL if len(pts) >= expected - 1 else (Coverage.PARTIAL if pts else Coverage.NONE)
    traj_ids = tuple(trajectory_record_ids) or ('trajectory',)
    notes: list[str] = []            # чего именно не хватает по каналам (для правила и панели)

    # --- механизм 1: космопогода на траектории ------------------------------
    saa_pts = [p for p in pts if p.in_saa is not None]
    minutes_saa = float(sum(1 for p in saa_pts if p.in_saa)) * step_min if saa_pts else None
    cov_saa = _cov(len(saa_pts), len(pts))
    if cov_traj != Coverage.FULL:
        notes.append('трасса: точки орбиты покрывают %.0f %% окна' % (100.0 * len(pts) / max(expected, 1)))

    # флюенс: вклад считается только там, где есть модель ОСТ; покрытие — по точкам АНОМАЛИИ
    # (вне аномалии сильное поле даёт L < 1,14 — граница сетки таблицы, не пропуск данных)
    fl_vals, fl_status, n_saa_model, n_saa = [], {}, 0, 0
    for p in pts:
        r = belts.integral_flux(p.L, p.B_over_B0, th.e_min_MeV)
        fl_status[r.status] = fl_status.get(r.status, 0) + 1
        if p.in_saa:
            n_saa += 1
            n_saa_model += r.value_per_cm2_s is not None
        if r.value_per_cm2_s is not None:
            fl_vals.append(r.value_per_cm2_s)
    fluence = float(sum(fl_vals) * 60.0 * step_min) if fl_vals else None   # част./см² (всенаправленный, R3)
    n_nomodel = fl_status.get('no_model_L', 0)
    # покрытие флюенса: непокрытыми считаются только точки АНОМАЛИИ без модели; точки вне аномалии с L < 1,14
    # (сильное поле, ниже пояса) — граница сетки ОСТ, где захваченных протонов таблица не содержит,
    # это объявляется как ограничение модели, а не как пропуск данных
    cov_fl = _cov(len(pts) - (n_saa - n_saa_model), len(pts)) if pts else Coverage.NONE
    fl_note = []
    if n_saa:
        fl_note.append('точки аномалии с моделью ОСТ: %d из %d (%.0f %%)' % (n_saa_model, n_saa, 100.0 * n_saa_model / n_saa))
    else:
        fl_note.append('окно не пересекает аномалию по порогу |B|')
    if n_nomodel:
        fl_note.append('%.0f %% точек трассы вне сетки ОСТ (L < 1,14, сильное поле, вне аномалии) — вклад не рассчитан, '
                       'сравнение окон не затрагивает' % (100.0 * n_nomodel / max(len(pts), 1)))
    fl_note.append('статусы точек: ' + ', '.join('%s — %d' % (MAG_STATUS_RU.get(k, k), v) for k, v in sorted(fl_status.items())))
    if cov_fl == Coverage.PARTIAL:
        notes.append('флюенс: модель ОСТ есть только на %.0f %% точек аномалии (%d из %d)' % (100.0 * n_saa_model / max(n_saa, 1), n_saa_model, n_saa))
    elif cov_fl == Coverage.NONE and pts:
        notes.append('флюенс: точки аномалии без модели ОСТ (%d из %d)' % (n_saa - n_saa_model, n_saa))

    # доступность канала GOES на трассе через вертикальное обрезание (CONTRACT §3, «минут доступности частиц канала»)
    cut_pts = [p for p in pts if p.cutoff_GV is not None]
    cut_factors = []
    for T_MeV in (10.0, 100.0):
        R = rigidity_GV(T_MeV)
        val = float(sum(1 for p in cut_pts if p.cutoff_GV < R)) * step_min if cut_pts else None
        cut_factors.append(FactorValue(
            'минут доступности протонов ≥%.0f МэВ по обрезанию' % T_MeV, val, 'мин', Kind.OWN_CALCULATION,
            _presence(val), _cov(len(cut_pts), len(pts)), traj_ids,
            'точки трассы с вертикальной жёсткостью обрезания (A3, центральный диполь) ниже %.2f ГВ — жёсткости протона %.0f МэВ; '
            'предположение о спектре: порог по жёсткости канала, без формы спектра' % (R, T_MeV),
            ('жёсткость обрезания считает A3 по ЦЕНТРАЛЬНОМУ наклонённому диполю; L и B/B0 для таблиц ОСТ считает '
             'vkd.assess.magcoords по ЭКСЦЕНТРИЧНОМУ диполю (R11) — это две разные модели, не одна система координат, '
             'и складывать их точность нельзя; '
             'дипольное вертикальное обрезание A3 в спокойных условиях; при буре Kp ≥ 7 обрезание снижается — не моделируется'
             + ('; по дипольному обрезанию протоны %.0f МэВ на трассе окна недоступны — условие GOES относится к штормовому '
                'ослаблению обрезания' % T_MeV if val == 0 else ''))))

    goes_note, goes_val, goes_cov, goes_hz, goes_frac = 'нет данных GOES', None, Coverage.NONE, None, None
    # окно целиком или частью выходит за границы архива событий и прогнозов: линия без покрытия.
    # Иначе окно на 30.06 в историческом режиме получало бы «предпочтительно» при пустом архиве
    # за его концом, тогда как на экране обещано «рекомендации не будет».
    beyond_archive = bool(catalog_coverage) and (win.start_utc < catalog_coverage[0] or end > catalog_coverage[1])
    if beyond_archive:
        c0, c1 = catalog_coverage[0], catalog_coverage[1]
        goes_cov = Coverage.NONE
        goes_note = ('окно %s — %s за границей архива уведомлений и прогнозов (архив %s — %s): событий и прогнозов '
                     'на этот интервал нет не потому, что их не было, а потому, что данных нет'
                     % (win.start_utc.strftime('%d.%m %H:%MZ'), end.strftime('%d.%m %H:%MZ'),
                        c0.strftime('%d.%m.%Y'), (c1 - timedelta(minutes=1)).strftime('%d.%m.%Y')))
        notes.append('события и прогнозы: окно за границей архива (архив до %s) — покрытие линии отсутствует; '
                     'сократите период поиска или длительность либо выберите более раннюю дату'
                     % (c1 - timedelta(minutes=1)).strftime('%d.%m.%Y'))
    elif goes is not None and goes.value is not None:
        age_min = (now_utc - goes.t_utc).total_seconds() / 60.0
        goes_val = goes.value
        goes_hz = goes.t_utc + timedelta(minutes=th.goes_max_age_min)
        # покрытие — доля окна внутри горизонта наблюдения [t_obs, t_obs + goes_max_age_min]:
        # наблюдение сейчас не распространяется молча на будущие участки окна (CONTRACT §4 п. 2)
        lo, hi = max(win.start_utc, goes.t_utc), min(end, goes_hz)
        frac = max(0.0, (hi - lo).total_seconds()) / (end - win.start_utc).total_seconds()
        goes_frac = frac
        goes_cov = Coverage.FULL if frac >= 0.95 else Coverage.PARTIAL
        goes_note = 'наблюдение %s (%s), давность %.0f мин; горизонт наблюдения до %s покрывает %.0f %% окна' % (
            goes.t_utc.strftime('%Y-%m-%d %H:%MZ'), s_level_ru(goes_val), age_min, goes_hz.strftime('%H:%MZ'), 100 * frac)
        if frac < 0.95:
            goes_note += '; на остальные участки окна наблюдение не распространяется, прогноза потока на окно нет'
            notes.append('GOES: наблюдение %s покрывает %.0f %% окна, прогноза потока протонов на окно нет'
                         % (goes.t_utc.strftime('%H:%MZ'), 100 * frac))
        if frac <= 0.0:
            # горизонт наблюдения кончился до начала окна: наличие протонного события В ОКНЕ неизвестно.
            # Покрытие остаётся частичным (уровень и время наблюдения объявлены), но «не выявлено» писать нельзя.
            gap_h = (win.start_utc - goes.t_utc).total_seconds() / 3600.0
            goes_note += ('; на само окно наблюдения нет — наличие протонного события в окне неизвестно, '
                          'уровень %s относится к моменту наблюдения' % s_level_ru(goes_val))
            notes.append('наблюдение GOES не покрывает окно (до его начала %.0f ч)' % gap_h)
    elif any(e.kind_of_event == 'SEP' and e.published_utc is not None for e in events):
        # архива GOES нет, но есть датированные уведомления о протонных событиях: частичное покрытие канала
        goes_cov = Coverage.PARTIAL
        goes_note = goes_absent_ru + '; канал частично покрыт датированными уведомлениями DONKI о протонных событиях'
        notes.append('GOES: ' + goes_absent_ru + ', канал покрыт только уведомлениями DONKI')
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
                notes.append('GOES: ' + goes_absent_ru + '; уведомлений DONKI о протонных событиях до отсечки нет')
            else:
                goes_note = ('архив уведомлений DONKI %s — %s не покрывает публикации до отсечки %s'
                             % (c0.strftime('%d.%m.%Y'), c1.strftime('%d.%m.%Y'), cutoff_utc.strftime('%Y-%m-%d %H:%MZ')))
                notes.append('GOES/DONKI: ' + goes_note)
        elif c0 <= win.start_utc and end <= c1:
            # пустой каталог за период — это результат «событий не объявлено», а не отсутствие данных
            # (CONTRACT v3.1, правило 9); покрытие частичное, потому что самого наблюдения GOES нет
            goes_cov = Coverage.PARTIAL
            goes_note = ('%s; в уведомлениях DONKI за %s — %s протонных событий с действием '
                         'в окне не объявлено' % (goes_absent_ru, c0.strftime('%d.%m.%Y'), (c1 - timedelta(minutes=1)).strftime('%d.%m.%Y')))
            notes.append('GOES: ' + goes_absent_ru + '; уведомлений DONKI о протонных событиях в окне нет')
        else:
            goes_note = ('уведомления DONKI: архив до %s, окно %s — %s за его пределами — сократите период поиска или сдвиг'
                         % (c1.strftime('%Y-%m-%d %H:%MZ'), win.start_utc.strftime('%d.%m %H:%MZ'), end.strftime('%d.%m %H:%MZ')))
            notes.append('GOES/DONKI: ' + goes_note)
    else:
        notes.append('GOES: данных нет (источник исключён или недоступен, кеша нет)')

    # --- наблюдение Kp: отдельный фактор с давностью; условие — только при свежем наблюдении ---
    kp_factor, kp_fresh, kp_age_min, kp_cov = None, False, None, Coverage.PARTIAL
    if kp is not None and kp.value is not None:
        ref = kp.valid_to_utc or kp.t_utc
        kp_age_min = max(0.0, (now_utc - ref).total_seconds() / 60.0)
        kp_fresh = kp_age_min <= th.kp_max_age_min
        sim = kp.source_id == 'scenario'
        kp_note = 'интервал %s — %s, давность %.0f мин (предел %.0f мин)' % (
            (kp.valid_from_utc or kp.t_utc).strftime('%d.%m %H:%MZ'), ref.strftime('%H:%MZ'), kp_age_min, th.kp_max_age_min)
        if sim:
            kp_note = 'МОДЕЛИРУЕМОЕ значение сценария «что если»; ' + kp_note
        if not kp_fresh:
            kp_note += ' — устарело: условие проверки по нему не ставится; на окно наблюдение не распространяется'
            notes.append('Kp: последнее наблюдение %s устарело (давность %.0f мин)' % (ref.strftime('%d.%m %H:%MZ'), kp_age_min))
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
        notes.append('Kp: наблюдения нет (%s)'
                     % ('источник исключён или публикации до отсечки нет' if cutoff_utc else 'источник исключён или данных нет'))
        kp_factor = FactorValue('Kp, последнее наблюдение', None, KP_UNIT, Kind.OBSERVATION, Presence.UNKNOWN, Coverage.NONE, (),
                                'порог проверки Kp ≥ %.0f (G3 по шкале NOAA); %s' % (th.kp_check, TEAM_RULE_RU),
                                'наблюдения Kp нет' + (' (в строгом режиме — только с доказанной публикацией до отсечки)' if cutoff_utc else ''))

    # --- прогнозы NOAA, выпущенные до отсечки (история): факторы-прогнозы, не наблюдения ---
    # Исходное разрешение источника сохраняется: суточная вероятность остаётся суточной
    # (vkd/history/README.md), прогноз Kp — по 3-часовым интервалам. Покрытие окна
    # ячейками объявляется; отсутствие прогноза не ухудшает покрытие механизма.
    fc_factors, storm_signals, storm_ids, storm_span = [], [], [], []
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
                + 'покрытие окна ячейками %.0f %%' % (100 * frac) + ('' if hit else '; выпуска до отсечки с ячейками на окно нет'),
                horizon_utc=hz))
            if cid == 'kp_forecast' and val is not None and val >= th.kp_check:
                cells = [s for s in hit if s.value is not None and s.value >= th.kp_check]
                storm_signals.append('%s %s в окне (выпуск %s)' % (STORM_SIGNAL_RU['noaa_kp_forecast'], fmt_ru(val),
                                                                   pub.strftime('%m-%d %H:%MZ') if pub else '?'))
                storm_ids += sorted({s.raw_record_id for s in cells})
                storm_span += [(max(s.valid_from_utc, win.start_utc), min(s.valid_to_utc, end)) for s in cells]

    factors_m1 = (
        FactorValue('минут в аномалии', minutes_saa, 'мин', Kind.OWN_CALCULATION,
                    _presence(minutes_saa), cov_saa, traj_ids, 'точки трассы с |B| ниже порога %.0f нТл (настройка, варьируется в чувствительности)' % th.saa_B_threshold_nT,
                    'дипольная L — исследовательское приближение' if any(p.mag_status != 'ok' for p in pts) else ''),
        FactorValue('флюенс захваченных протонов ≥%g МэВ' % th.e_min_MeV, fluence, 'част./см²',
                    Kind.OWN_CALCULATION, _presence(fluence), cov_fl, traj_ids + (belts.raw_record_id,),
                    belts.source + '; всенаправленный поток (%s); интерполяция: %s; L и B/B0 — эксцентричный диполь (R11)'
                    % (belts.flux_unit_ru, belts.interpolation_ru),
                    '; '.join(fl_note)),
    ) + tuple(cut_factors) + (
        FactorValue('поток протонов GOES ≥10 МэВ', goes_val, 'pfu', Kind.OBSERVATION,
                    (Presence.UNKNOWN if goes_val is None or (goes_frac is not None and goes_frac <= 0.0) else
                     (Presence.DETECTED if goes_val >= th.goes_p10_warning_pfu else Presence.NOT_DETECTED)),
                    goes_cov, (goes.raw_record_id,) if goes else (), 'NOAA SWPC, последнее наблюдение; уровень по шкале S NOAA (S1 = 10 pfu)',
                    goes_note + ('; уровень %s' % s_level_ru(goes_val) if goes_val is not None else ''), horizon_utc=goes_hz),
        kp_factor,
    ) + tuple(fc_factors)

    # --- механизм 2: статистика метеороидов ECSS по высоте трассы (B2 по A5) --------
    mm_cov = Coverage.NONE if mmod_hits is None else (
        Coverage.FULL if mmod_cov_fraction >= 0.95 else (Coverage.PARTIAL if mmod_cov_fraction >= 0.5 else Coverage.NONE))
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
        coverage_notes=(('метеороиды: трасса покрывает %.0f %% окна' % (100 * mmod_cov_fraction),) if mmod_hits is not None and mm_cov != Coverage.FULL
                        else (('метеороиды: расчёт невозможен — нет трассы окна',) if mmod_hits is None else ())),
    )

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
    conds: list[Condition] = []
    if goes_val is not None:
        obs_txt = 'наблюдение %s' % goes.t_utc.strftime('%m-%d %H:%MZ')
        src_goes = ('GOES ≥10 МэВ %s pfu, %s, %s, публикация NOAA SWPC в реальном времени' % (fmt_ru(goes_val), s_level_ru(goes_val), obs_txt),)
        if goes_val >= th.goes_p10_priority_pfu:
            conds.append(Condition('GOES', 'critical',
                                   'GOES ≥10 МэВ = %.3g pfu (%s, %s): S3 и выше — приоритетное, срочная проверка специалистом'
                                   % (goes_val, s_level_ru(goes_val), obs_txt), (goes.raw_record_id,), (goes.t_utc, goes_hz),
                                   s_level_ru(goes_val), src_goes))
        elif goes_val >= th.goes_p10_warning_pfu:
            conds.append(Condition('GOES', 'limiting',
                                   'GOES ≥10 МэВ = %.3g pfu (%s, %s): предупреждение о протонном событии — окно не выбирается '
                                   'автоматически (правило команды: S1–S2)' % (goes_val, s_level_ru(goes_val), obs_txt),
                                   (goes.raw_record_id,), (goes.t_utc, goes_hz), s_level_ru(goes_val), src_goes))
    # Kp: свежее наблюдение ≥ порога — сигнал о буре; распространение на окно ОБЪЯВЛЯЕТСЯ в тексте
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
                if level is not None and level >= th.goes_p10_priority_pfu:
                    sev, cls = 'critical', '%s (%s pfu, %s) — S3 и выше, приоритетное: срочная проверка специалистом' % (lvl_ru, fmt_ru(level), bound_ru)
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
                kp_txt = ('Kp до %g (%s)' % (kp_max, kp_basis_ru)) if kp_max is not None else 'уровень Kp не назван'
                storm_sim = storm_sim or sim
                if kind_ev == 'GST':
                    storm_signals.append('%s%s с %s, %s; действие %s — %s%s (%d %s, %s)'
                                         % ('МОДЕЛИРУЕМОЕ ' if sim else '', STORM_SIGNAL_RU['donki_storm'],
                                            a0.strftime('%m-%d %H:%MZ'), kp_txt,
                                            a0.strftime('%m-%d %H:%MZ'), a1.strftime('%m-%d %H:%MZ'), assumed_txt, n,
                                            _plural(n, 'запись', 'записи', 'записей'), pub_txt))
                else:
                    storm_signals.append('%s%s %s (уведомление NASA DONKI), ожидаемый %s; действие %s — %s%s (%d %s, %s)'
                                         % ('МОДЕЛИРУЕМОЕ ' if sim else '', STORM_SIGNAL_RU['cme_arrival'],
                                            a0.strftime('%m-%d %H:%MZ'), kp_txt,
                                            a0.strftime('%m-%d %H:%MZ'), a1.strftime('%m-%d %H:%MZ'), assumed_txt, n,
                                            _plural(n, 'запись', 'записи', 'записей'), pub_txt))
                storm_ids += ids
                storm_span.append((a0, a1))
    if storm_signals:
        n_src = len(storm_signals)
        ids = tuple(dict.fromkeys(storm_ids))
        n_rec = len([i for i in ids if not i.startswith('sim_')])
        conds.append(Condition(
            'GST', 'limiting',
            '%sгеомагнитная буря Kp ≥ %.0f в окне (%d %s): %s — условие проверки по правилу команды (порог Kp ≥ %.0f, не норма)%s'
            % ('МОДЕЛИРУЕМОЕ ' if storm_sim else '', th.kp_check, n_src, _plural(n_src, 'источник', 'источника', 'источников'),
               ', '.join(storm_signals), th.kp_check,
               ('; %d %s DONKI/NOAA — %s' % (n_rec, _plural(n_rec, 'запись', 'записи', 'записей'),
                                             ', '.join(record_ru(i) for i in ids if not i.startswith('sim_'))) if n_rec else '')),
            ids, (min(a for a, _ in storm_span), max(b for _, b in storm_span)) if storm_span else (),
            'Kp ≥ %.0f' % th.kp_check, tuple(storm_signals), is_simulated=storm_sim))
    conds += list(conj_conds)
    order = {'critical': 0, 'limiting': 1}
    conds.sort(key=lambda c: order[c.severity])
    m1 = MechanismAssessment(
        mechanism_id='spaceweather', mandatory=True, factors=factors_m1,
        coverage=_min_cov(cov_traj, cov_saa, cov_fl, goes_cov, kp_cov),
        needs_check=bool(conds), needs_check_reasons=tuple(c.text for c in conds),
        priority=any(c.severity == 'critical' for c in conds), conditions=tuple(conds),
        coverage_notes=tuple(notes))

    return WindowAssessment(
        window=win, mechanisms=(m1, m2, m3),
        coverage_declared=('космическая погода на траектории',) + (('природные метеороиды ECSS/Grün',) if mmod_hits is not None else ())
                          + (('сближения SOCRATES',) if conj else ()),
        coverage_missing=(('статистика метеороидов — не подключена',) if mmod_hits is None else ())
                         + (() if conj else ('сближения SOCRATES — нет данных',))
                         + ('вклад метеорных потоков даты в число попаданий — не рассчитан (календарь даёт только признак активности)',
                            'техногенный мусор статистически — не включён'),
    )


def recommend(assessments: Sequence[WindowAssessment], th: Thresholds) -> Recommendation:
    idx = {id(a): i + 1 for i, a in enumerate(assessments)}

    def lab(a):
        return 'окно %d (%s)' % (idx[id(a)], a.window.start_utc.strftime('%H:%MZ'))

    # 1. охват: блокирует только ОТСУТСТВИЕ покрытия обязательной линии; частичное — объявляется
    #    (разбор Codex п. 14: отказ с пригодным кешем не обязан давать отказ от рекомендации).
    #    Причина называется по каналам (coverage_notes), а не общим словом «частичное».
    missing, partial = {}, {}
    for a in assessments:
        for m in a.mechanisms:
            if not m.mandatory or m.coverage == Coverage.FULL:
                continue
            name = MECH_RU.get(m.mechanism_id, m.mechanism_id)
            target = missing if m.coverage == Coverage.NONE else partial
            texts = ['%s: %s' % (name, n) for n in m.coverage_notes] or ['%s: покрытие %s' % (name, 'отсутствует' if m.coverage == Coverage.NONE else 'частичное')]
            for t in texts:
                target.setdefault(t, []).append(idx[id(a)])
    n_all = len(assessments)

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

    per, best, conflict, sw_conflict, equiv_set, d_saa, ratio = {}, None, False, None, [], 0.0, None
    if len(candidates) >= 2:
        # 3. сравнение по каждому механизму отдельно; космопогода — по двум величинам
        mins = {id(a): _sw_vals(a)[0] for a in candidates}
        fls = {id(a): _sw_vals(a)[1] for a in candidates}
        tol_m, tol_r = th.equiv_tol_min, max(1.0, th.fluence_equiv_ratio)

        def not_worse(a, b):
            m_ok = mins[id(a)] is None or mins[id(b)] is None or mins[id(a)] <= mins[id(b)] + tol_m
            f_ok = fls[id(a)] is None or fls[id(b)] is None or fls[id(a)] <= fls[id(b)] * tol_r
            return m_ok and f_ok

        def better(a, b):
            m_b = mins[id(a)] is not None and mins[id(b)] is not None and mins[id(a)] < mins[id(b)] - tol_m
            f_b = fls[id(a)] is not None and fls[id(b)] is not None and fls[id(a)] * tol_r < fls[id(b)]
            return not_worse(a, b) and (m_b or f_b)

        def vtxt(a):
            return '%s: %s мин в аномалии, флюенс %s' % (lab(a), fmt_ru(mins[id(a)]), fmt_ru(fls[id(a)], 'част./см²'))
        ranked = sorted(candidates, key=lambda a: (fls[id(a)] if fls[id(a)] is not None else float('inf'),
                                                   mins[id(a)] if mins[id(a)] is not None else float('inf')))
        dominators = [a for a in candidates if all(not_worse(a, b) for b in candidates if b is not a)]
        listing = '; '.join(vtxt(a) for a in ranked)
        if not dominators:
            by_min = min(candidates, key=lambda a: mins[id(a)] if mins[id(a)] is not None else float('inf'))
            by_fl = ranked[0]
            sw_conflict = 'по минутам лучше %s, по флюенсу — %s' % (lab(by_min), lab(by_fl))
            per['spaceweather'] = listing + ' — ' + sw_conflict
        else:
            strict = [a for a in dominators if all(better(a, b) for b in candidates if b is not a)]
            if strict:
                best = strict[0]
                others = [b for b in candidates if b is not best]
                dd = [mins[id(b)] - mins[id(best)] for b in others if mins[id(b)] is not None and mins[id(best)] is not None]
                d_saa = min(dd) if dd else 0.0
                rr = [fls[id(b)] / fls[id(best)] for b in others if fls[id(b)] is not None and fls[id(best)]]
                ratio = min(rr) if rr else None
                per['spaceweather'] = listing + ' — лучше %s' % lab(best)
            else:
                a0 = dominators[0]
                equiv_set = [a0] + [b for b in candidates if b is not a0 and not_worse(a0, b) and not_worse(b, a0)]
                worse = [b for b in candidates if b not in equiv_set]
                per['spaceweather'] = listing + ' — равнозначны в допуске: %s' % ', '.join(lab(a) for a in equiv_set) + (
                    '; хуже: %s' % ', '.join(lab(a) for a in worse) if worse else '')
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
                                '(%s; настройка config/settings.toml); %s'
                                % (('%.3f' % (100 * rel)).replace('.', ','), fmt_ru(equal_pct), TEAM_RULE_RU, MMOD_ROLE_RU))
            # 4. сведение: противоречие механизмов вне допуска → компромисс
            conflict = 100 * rel > equal_pct and best is not None and best_mm.window.start_utc != best.window.start_utc
    tol = 'допуск %.0f мин по минутам и ×%.2f по флюенсу — инженерная настройка, до анализа чувствительности' % (th.equiv_tol_min, th.fluence_equiv_ratio)
    note_partial = tuple(partial_l)
    if missing_l:
        return Recommendation(preferred=None, verdict='insufficient',
                              rule_applied='п.1: отсутствует покрытие обязательной линии — ' + '; '.join(missing_l),
                              per_mechanism_comparison=per, reasons=tuple(per.values()) + note_partial + cond_reasons,
                              missing=tuple(missing_l), tolerance_basis=tol)
    if sw_conflict:
        return Recommendation(preferred=None, verdict='trade_off',
                              rule_applied='п.3: внутри механизма космопогоды минуты в аномалии и флюенс указывают на разные окна — '
                                           'компромисс без победителя (%s)' % sw_conflict,
                              per_mechanism_comparison=per, reasons=tuple(per.values()) + note_partial + cond_reasons, tolerance_basis=tol)
    if conflict:
        return Recommendation(preferred=None, verdict='trade_off',
                              rule_applied='п.4: механизмы указывают на разные окна — компромисс без победителя',
                              per_mechanism_comparison=per, reasons=tuple(per.values()) + note_partial + cond_reasons, tolerance_basis=tol)
    if not candidates:
        return Recommendation(preferred=None, verdict='all_need_check', rule_applied='п.2: все окна под условием',
                              per_mechanism_comparison=per, reasons=cond_reasons + note_partial, tolerance_basis=tol)
    if len(candidates) == 1:
        c = candidates[0]
        return Recommendation(preferred=c.window, verdict='preferred',
                              rule_applied='п.2: %s — единственное без условий' % lab(c),
                              per_mechanism_comparison=per, reasons=tuple(per.values()) + cond_reasons + note_partial, tolerance_basis=tol)
    if equiv_set:
        return Recommendation(preferred=None, verdict='equivalent',
                              rule_applied='п.5: %s равнозначны — разница минут и флюенса внутри допуска (%.0f мин, ×%.2f)' % (
                                  ', '.join(lab(a) for a in equiv_set), th.equiv_tol_min, th.fluence_equiv_ratio),
                              per_mechanism_comparison=per, reasons=tuple(per.values()) + note_partial + cond_reasons, tolerance_basis=tol)
    why = []
    if d_saa > th.equiv_tol_min:
        why.append('на %.0f мин меньше в аномалии' % d_saa)
    if ratio is not None and ratio > th.fluence_equiv_ratio:
        why.append('флюенс ниже в %.2f раза' % ratio)
    return Recommendation(preferred=best.window, verdict='preferred',
                          rule_applied='п.3–4: %s лучше по космопогоде (%s), не хуже по флюенсу и минутам, линия метеороидов не противоречит'
                                       % (lab(best), ', '.join(why) or 'за пределами допуска') + ('; покрытие частичное — объявлено' if partial_l else ''),
                          per_mechanism_comparison=per, reasons=tuple(per.values()) + cond_reasons + note_partial, tolerance_basis=tol)


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
    measured = [float(v) for v in (_facts(e, facts_of).get('measured_flux_pfu') for e in evs) if v is not None]
    if measured:
        return max(measured), True, _sep_energy(evs, facts_of) <= 10.0
    bounds = [float(v) for v in (_facts(e, facts_of).get('flux_lower_bound_pfu') for e in evs) if v is not None]
    if bounds:
        return max(bounds), False, _sep_energy(evs, facts_of) <= 10.0
    from_note = [v for v in (_pfu_from_note(e.note) for e in evs) if v is not None]
    return (max(from_note) if from_note else None), False, True


def _storm_kp(evs: Sequence[EventInterval], facts_of: dict, th: Thresholds):
    """(Kp для сравнения с порогом, чем он обоснован) — по структурированным фактам.

    Уведомление о буре: facts.kp — наблюдённый индекс за указанный 3-часовой интервал.
    Уведомление о приходе выброса: граница ОПУБЛИКОВАННОГО диапазона максимума Kp
    (facts.kp_range_min/max, kp_basis = published_notification_range); какая именно
    граница — настройка [history].cme_kp_range_bound, по умолчанию верхняя.
    """
    vals, bases = [], []
    for e in evs:
        f = _facts(e, facts_of)
        if e.kind_of_event == 'CME_ARRIVAL':
            key = 'kp_range_max' if th.cme_kp_bound == 'max' else 'kp_range_min'
            v = f.get(key)
            if v is not None:
                vals.append(float(v))
                bases.append('%s граница опубликованного диапазона %g–%g'
                             % ('верхняя' if th.cme_kp_bound == 'max' else 'нижняя',
                                float(f.get('kp_range_min')), float(f.get('kp_range_max'))))
                continue
        elif f.get('kp') is not None:
            vals.append(float(f['kp']))
            bases.append('наблюдённый Kp уведомления')
            continue
        v = _kp_from_note(e.note)
        if v is not None:
            vals.append(v)
            bases.append('по тексту записи (запасной разбор)')
    if not vals:
        return None, ''
    best = max(range(len(vals)), key=lambda i: vals[i])
    return vals[best], bases[best]


def _cov(n_ok: int, n_all: int) -> Coverage:
    """Покрытие точками окна: ≥95 % — полное, ≥50 % — частичное (доля объявляется), иначе нет."""
    if n_all == 0 or n_ok == 0:
        return Coverage.NONE
    frac = n_ok / n_all
    return Coverage.FULL if frac >= 0.95 else (Coverage.PARTIAL if frac >= 0.5 else Coverage.NONE)


def _min_cov(*cs: Coverage) -> Coverage:
    order = {Coverage.FULL: 2, Coverage.PARTIAL: 1, Coverage.NONE: 0}
    return min(cs, key=lambda c: order[c])


def _presence(v) -> Presence:
    return Presence.UNKNOWN if v is None else (Presence.DETECTED if v > 0 else Presence.NOT_DETECTED)
