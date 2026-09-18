# -*- coding: utf-8 -*-
"""Сравнение окон и правило рекомендации. CONTRACT.md v3, разделы 3 и 4.

Правило (по порядку, каждый пункт виден пользователю):
  1. Охват: у обязательной линии неполное покрытие → рекомендации нет.
  2. Условия: критическое (окно отменяется) и ограничивающее (окно не
     планируется) — по разделу 5 KRITERII_PLAN.md; пороги — настройки
     с источником. Помеченные окна из сравнения исключаются.
  3. Сравнение по каждому механизму отдельно.
  4. Сведение: предпочтительно только если не хуже по всем и лучше хотя бы
     по одному; противоречие → компромисс без победителя.
  5. Допуск равнозначности — пока инженерная настройка; заменяется
     разбросом чувствительности (О7).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional, Sequence

from vkd.assess.trapped import BeltTable
from vkd.types import (Conjunction, Coverage, EnvironmentSample, FactorValue, Kind,
                       MechanismAssessment, Presence, Recommendation, TrajectoryPoint,
                       Window, WindowAssessment)


@dataclass(frozen=True)
class Thresholds:
    """Пороги условий. Семантика по разбору Codex 19.09 (journal/friend.md, пп. 6–8):

    * S ≥ 3 (≥1000 pfu по ≥10 МэВ) — NOAA рекомендует избегать радиационной
      опасности при ВКД: ПРИОРИТЕТНОЕ предупреждение, окно требует срочной
      проверки специалистом. Ни «прервать», ни «продолжать» из индекса не следует.
    * S1–S2 (≥10 pfu) — предупреждение о протонном событии; исключение таких
      окон из автоматического выбора — консервативная политика ПРОТОТИПА,
      не эксплуатационная норма.
    * Kp ≥ 7 (G3) — триггер дополнительной проверки условий модели, орбиты
      и связи; сам по себе не запрет ВКД. Наша консервативная политика.
    * сближение с TCA в окне — качественное сообщение, требует ручной оценки;
      порог промаха как вероятность попадания не вводится.
    Источник шкал: NOAA SWPC, Space Weather Scales."""
    goes_p10_warning_pfu: float = 10.0      # S1: предупреждение о протонном событии
    goes_p10_priority_pfu: float = 1000.0   # S3: приоритетное предупреждение (NOAA: EVA hazard avoidance)
    kp_check: float = 7.0                   # G3: триггер дополнительной проверки, наш выбор
    goes_max_age_min: float = 60.0          # свежесть наблюдения для будущих участков
    equiv_tol_min: float = 5.0              # инженерная настройка, см. п. 5 правила
    e_min_MeV: float = 30.0                 # канал захваченных протонов для интеграла
    saa_B_threshold_nT: float = 24000.0     # инженерная оценка для ~420 км, в чувствительность


def assess_window(win: Window, traj: Sequence[TrajectoryPoint], belts: BeltTable,
                  goes: Optional[EnvironmentSample], kp: Optional[EnvironmentSample],
                  conj: Sequence[Conjunction], th: Thresholds,
                  now_utc, mmod_hits: Optional[float] = None,
                  mmod_rule: str = 'ECSS-E-ST-10-04C, Grün — спецификация A5 ожидается') -> WindowAssessment:
    """mmod_hits: ожидаемое число попаданий на пластину 1 м² за окно (B2 по
    спецификации A5). None — линия не подключена, покрытие NONE."""
    end = win.start_utc + timedelta(minutes=win.duration_min)
    pts = [p for p in traj if win.start_utc <= p.t_utc < end]
    step_min = 1.0
    expected = win.duration_min / step_min
    cov_traj = Coverage.FULL if len(pts) >= expected - 1 else (Coverage.PARTIAL if pts else Coverage.NONE)

    # --- механизм 1: космопогода на траектории ------------------------------
    saa_pts = [p for p in pts if p.in_saa is not None]
    minutes_saa = float(sum(1 for p in saa_pts if p.in_saa)) * step_min if saa_pts else None
    cov_saa = _cov(len(saa_pts), len(pts))

    fl_vals, fl_status = [], set()
    for p in pts:
        r = belts.integral_flux(p.L, p.B_over_B0, th.e_min_MeV)
        fl_status.add(r.status)
        if r.value_per_cm2_s_sr is not None:
            fl_vals.append(r.value_per_cm2_s_sr)
    fluence = float(sum(fl_vals) * 60.0 * step_min) if fl_vals else None   # част./(см²·ср)
    cov_fl = _cov(len(fl_vals), len(pts))

    goes_note, goes_val, goes_cov = 'нет данных GOES', None, Coverage.NONE
    if goes is not None and goes.value is not None:
        age_min = (now_utc - goes.t_utc).total_seconds() / 60.0
        goes_val = goes.value
        fresh = age_min <= th.goes_max_age_min
        goes_cov = Coverage.FULL if fresh else Coverage.PARTIAL
        goes_note = 'наблюдение %s, давность %.0f мин%s' % (goes.t_utc.strftime('%Y-%m-%d %H:%MZ'), age_min,
                                                            '' if fresh else ' — устарело для будущих участков')

    m1 = MechanismAssessment(
        mechanism_id='spaceweather', mandatory=True,
        factors=(
            FactorValue('минут в аномалии', minutes_saa, 'мин', Kind.OWN_CALCULATION,
                        _presence(minutes_saa), cov_saa, ('trajectory',), 'порог |B| %.0f нТл' % th.saa_B_threshold_nT,
                        'дипольная L — исследовательское приближение' if any(p.mag_status != 'ok' for p in pts) else ''),
            FactorValue('флюенс захваченных протонов ≥%g МэВ' % th.e_min_MeV, fluence, 'част./(см²·ср)',
                        Kind.OWN_CALCULATION, _presence(fluence), cov_fl, ('trajectory', 'ost1044_A'),
                        belts.source + '; трапеции по энергии, хвост выше %g МэВ отброшен' % belts.energies_MeV[-1],
                        'статусы точек: ' + ', '.join(sorted(fl_status))),
            FactorValue('поток протонов GOES ≥10 МэВ', goes_val, 'pfu', Kind.OBSERVATION,
                        _presence(goes_val), goes_cov, (goes.raw_record_id,) if goes else (), 'NOAA SWPC, последнее значение', goes_note),
        ),
        coverage=_min_cov(cov_traj, cov_saa, cov_fl, goes_cov),
        needs_check=False,
    )

    # --- механизм 2: статистика метеороидов ECSS — ещё не подключена --------
    m2 = MechanismAssessment(
        mechanism_id='mmod_stat', mandatory=True,
        factors=(FactorValue('ожидаемое число попаданий, пластина 1 м²', mmod_hits, 'шт', Kind.OWN_CALCULATION,
                             Presence.UNKNOWN if mmod_hits is None else Presence.DETECTED,
                             Coverage.NONE if mmod_hits is None else Coverage.FULL, () if mmod_hits is None else ('ecss_grun',),
                             mmod_rule,
                             'линия не подключена: расчёт невозможен' if mmod_hits is None
                             else 'природные метеороиды, случайно ориентированная пластина; техногенные частицы не включены'),),
        coverage=Coverage.NONE if mmod_hits is None else Coverage.FULL, needs_check=False,
    )

    # --- линия 3: сближения, необязательная ----------------------------------
    in_win = [c for c in conj if win.start_utc <= c.tca_utc < end]
    m3 = MechanismAssessment(
        mechanism_id='conjunctions', mandatory=False,
        factors=(FactorValue('сближений с TCA в окне', float(len(in_win)) if conj else None, 'шт',
                             Kind.EXTERNAL_FORECAST, Presence.DETECTED if in_win else (Presence.NOT_DETECTED if conj else Presence.UNKNOWN),
                             Coverage.FULL if conj else Coverage.NONE, tuple(c.raw_record_id for c in in_win),
                             'CelesTrak SOCRATES, TCA внутри окна', 'усечённая выдача не означает отсутствия других'),),
        coverage=Coverage.FULL if conj else Coverage.NONE, needs_check=bool(in_win),
        needs_check_reasons=tuple('сближение %s, промах %.1f км' % (c.other_object, c.miss_distance_km) for c in in_win),
    )

    # --- условия: критическое / ограничивающее -------------------------------
    crit, limit = [], []
    if goes_val is not None:
        if goes_val >= th.goes_p10_priority_pfu:
            crit.append('GOES ≥10 МэВ = %.3g pfu, S3 и выше: приоритетное — срочная проверка специалистом' % goes_val)
        elif goes_val >= th.goes_p10_warning_pfu:
            limit.append('GOES ≥10 МэВ = %.3g pfu, S1–S2: предупреждение о протонном событии — исключено из автовыбора (политика прототипа)' % goes_val)
    if kp is not None and kp.value is not None and kp.value >= th.kp_check:
        limit.append('Kp = %.1f ≥ %.0f (G3): триггер дополнительной проверки — политика прототипа' % (kp.value, th.kp_check))
    if in_win:
        limit.append('сообщение о сближении с TCA в окне: требует ручной оценки')
    m1 = MechanismAssessment(m1.mechanism_id, m1.mandatory, m1.factors, m1.coverage,
                             needs_check=bool(crit or limit), needs_check_reasons=tuple(crit + limit))

    return WindowAssessment(
        window=win, mechanisms=(m1, m2, m3),
        coverage_declared=('космическая погода на траектории',) + (('сближения SOCRATES',) if conj else ()),
        coverage_missing=('статистика метеороидов ECSS — не подключена',) + (() if conj else ('сближения SOCRATES — нет данных',)),
    )


def recommend(assessments: Sequence[WindowAssessment], th: Thresholds) -> Recommendation:
    # 1. охват
    missing = []
    for a in assessments:
        for m in a.mechanisms:
            if m.mandatory and m.coverage != Coverage.FULL:
                missing.append('%s: покрытие %s' % (m.mechanism_id, m.coverage.value))
    missing = sorted(set(missing))
    # 2. условия
    flagged = {id(a): [r for m in a.mechanisms for r in m.needs_check_reasons] for a in assessments}
    candidates = [a for a in assessments if not flagged[id(a)]]
    per = {}
    if len(candidates) >= 2:
        # 3. сравнение по механизму 1 (метрики: минуты в аномалии, затем флюенс)
        def key(a):
            f = {x.name: x.value for x in a.mechanisms[0].factors}
            return (f.get('минут в аномалии') if f.get('минут в аномалии') is not None else 1e9,
                    next((v for n, v in f.items() if n.startswith('флюенс')), None) or 1e30)
        ranked = sorted(candidates, key=key)
        best, worst = ranked[0], ranked[-1]
        d_saa = (key(worst)[0] - key(best)[0])
        per['spaceweather'] = ('%s: %.0f мин в аномалии против %.0f' % (
            best.window.start_utc.strftime('%H:%MZ'), key(best)[0], key(worst)[0]))
    else:
        best = None
    tol = 'допуск %.0f мин — инженерная настройка, до анализа чувствительности' % th.equiv_tol_min
    if missing:
        return Recommendation(preferred=None, verdict='insufficient',
                              rule_applied='п.1: неполное покрытие обязательной линии',
                              per_mechanism_comparison=per, reasons=tuple(per.values()),
                              missing=tuple(missing), tolerance_basis=tol)
    if not candidates:
        return Recommendation(preferred=None, verdict='all_need_check', rule_applied='п.2: все окна под условием',
                              per_mechanism_comparison=per,
                              reasons=tuple(r for a in assessments for r in flagged[id(a)]), tolerance_basis=tol)
    if len(candidates) == 1:
        return Recommendation(preferred=candidates[0].window, verdict='preferred',
                              rule_applied='п.2: единственное окно без условий',
                              per_mechanism_comparison=per, reasons=tuple(per.values()), tolerance_basis=tol)
    if d_saa < th.equiv_tol_min:
        return Recommendation(preferred=None, verdict='equivalent',
                              rule_applied='п.5: разница %.0f мин меньше допуска %.0f мин' % (d_saa, th.equiv_tol_min),
                              per_mechanism_comparison=per, reasons=tuple(per.values()), tolerance_basis=tol)
    return Recommendation(preferred=best.window, verdict='preferred',
                          rule_applied='п.3–4: лучше по механизму 1, другие линии не противоречат',
                          per_mechanism_comparison=per, reasons=tuple(per.values()), tolerance_basis=tol)


def _cov(n_ok: int, n_all: int) -> Coverage:
    if n_all == 0 or n_ok == 0:
        return Coverage.NONE
    return Coverage.FULL if n_ok == n_all else Coverage.PARTIAL


def _min_cov(*cs: Coverage) -> Coverage:
    order = {Coverage.FULL: 2, Coverage.PARTIAL: 1, Coverage.NONE: 0}
    return min(cs, key=lambda c: order[c])


def _presence(v) -> Presence:
    return Presence.UNKNOWN if v is None else (Presence.DETECTED if v > 0 else Presence.NOT_DETECTED)
