# -*- coding: utf-8 -*-
"""Правило рекомендации и надёжность (CONTRACT.md v3, разделы 4 и 8; критерии О3, Т6)."""
from datetime import datetime, timedelta, timezone

import pytest

from vkd.assess.trapped import BeltTable
from vkd.types import EnvironmentSample, Kind, MagMethod, TrajectoryPoint, Window
from vkd.windows.compare import Thresholds, assess_window, recommend, rigidity_GV, s_level_ru

T0 = datetime(2024, 5, 3, 12, 0, tzinfo=timezone.utc)


def traj(minutes: int, saa_pattern, L_of=lambda i: 1.3, cutoff_of=lambda i: None):
    """Синтетическая траектория: L по L_of (по умолчанию 1.3), B/B0=1.2; аномалия по шаблону минут."""
    return [TrajectoryPoint(T0 + timedelta(minutes=i), 0.0, 0.0, 420.0, 20000.0 if saa_pattern(i) else 40000.0,
                            L_of(i), 1.2, cutoff_of(i), MagMethod.DIPOLE, 'approximation', bool(saa_pattern(i)))
            for i in range(minutes)]


def goes(pfu, age_min=5):
    return EnvironmentSample(T0 - timedelta(minutes=age_min), 'goes_p_ge10MeV', pfu, 'pfu', 'noaa', Kind.OBSERVATION,
                             None, None, None, T0, 'preliminary', 'rec#goes')


def kp_sample(value, age_min):
    t_end = T0 - timedelta(minutes=age_min)
    return EnvironmentSample(t_end, 'kp', value, '', 'gfz_kp', Kind.OBSERVATION, None, t_end - timedelta(hours=3), t_end, T0, 'final', 'rec#kp')


@pytest.fixture(scope='module')
def belts():
    return BeltTable('min')


def two_windows(belts, g, mmod=1e-6, pattern=lambda i: i < 60, kp=None, **kw):
    tr = traj(24 * 60, pattern, **kw)
    th = Thresholds()
    w = [Window(T0, 360), Window(T0 + timedelta(minutes=480), 360)]
    A = [assess_window(x, tr, belts, g, kp, [], th, T0, mmod_hits=mmod) for x in w]
    return A, th


def reasons_of(A):
    return [x for a in A for m in a.mechanisms for x in m.needs_check_reasons]


def test_missing_mandatory_line_gives_insufficient(belts):
    A, th = two_windows(belts, goes(0.2), mmod=None)
    r = recommend(A, th)
    assert r.verdict == 'insufficient' and any('метеороид' in m for m in r.missing)


def test_disabled_source_never_yields_favorable(belts):
    """Т6: отключённый GOES без кеша → «недостаточно», не «предпочтительно»; причина названа по каналу."""
    A, th = two_windows(belts, None)
    r = recommend(A, th)
    assert r.verdict == 'insufficient' and any('космопогода' in m and 'GOES' in m for m in r.missing)


def test_clean_windows_prefer_less_saa(belts):
    A, th = two_windows(belts, goes(0.2))
    r = recommend(A, th)
    assert r.verdict == 'preferred' and r.preferred.start_utc == T0 + timedelta(minutes=480)
    assert 'spaceweather' in r.per_mechanism_comparison
    # обе величины и номера окон видны в сравнении (О3/О5)
    txt = r.per_mechanism_comparison['spaceweather']
    assert 'окно 1' in txt and 'окно 2' in txt and 'флюенс' in txt and 'мин в аномалии' in txt


def test_priority_condition_S3_flags_all_windows(belts):
    """S ≥ 3 (≥1000 pfu): приоритетное предупреждение; все окна требуют проверки, не «недостаточно»."""
    A, th = two_windows(belts, goes(1500.0))
    r = recommend(A, th)
    assert r.verdict == 'all_need_check' and any('приоритетное' in x for x in r.reasons)
    assert all(m.priority for a in A for m in a.mechanisms if m.mechanism_id == 'spaceweather')
    # причины привязаны к окнам и не дублируются (одинаковое условие схлопнуто в «окна 1, 2»)
    cond = [x for x in r.reasons if 'приоритетное' in x]
    assert len(cond) == 1 and cond[0].startswith('окна 1, 2:')


def test_warning_S1_S2_marks_windows_as_team_rule(belts):
    A, th = two_windows(belts, goes(20.0))
    reasons = reasons_of(A)
    assert reasons and all('правило команды' in x and 'S1' in x for x in reasons)
    assert not any('политика прототипа' in x for x in reasons)
    assert all(m.needs_check for a in A for m in a.mechanisms if m.mechanism_id == 'spaceweather')


def test_goes_presence_distinguishes_background_from_event(belts):
    """О2: фон 0,3 pfu — «не выявлено», а не «обнаружено»; уровень S в ограничениях."""
    A, _ = two_windows(belts, goes(0.35))
    f = next(x for x in A[0].mechanisms[0].factors if x.name.startswith('поток протонов GOES'))
    assert f.presence.value == 'not_detected' and 'ниже S1' in f.limits_note
    A, _ = two_windows(belts, goes(150.0))
    f = next(x for x in A[0].mechanisms[0].factors if x.name.startswith('поток протонов GOES'))
    assert f.presence.value == 'detected' and 'S2' in f.limits_note
    assert s_level_ru(None) == 'нет данных' and s_level_ru(12000.0) == 'S4'


def test_no_abort_or_continue_commands_in_reasons(belts):
    """Из индексов NOAA не следует ни «прервать», ни «продолжать» ВКД (разбор Codex п. 6)."""
    A, th = two_windows(belts, goes(1500.0))
    text = ' '.join(reasons_of(A))
    assert 'прерыва' not in text and 'продолжа' not in text


def test_equivalent_within_tolerance(belts):
    A, th = two_windows(belts, goes(0.2), pattern=lambda i: (i % 90) < 10)   # одинаковая доля аномалии
    r = recommend(A, th)
    assert r.verdict == 'equivalent' and 'инженерная' in r.tolerance_basis
    assert 'окно 1' in r.rule_applied and 'окно 2' in r.rule_applied


def test_stale_goes_gives_partial_coverage_but_declared_not_blocking(belts):
    """Разбор Codex п. 14: частичное покрытие объявляется, но не превращается в отказ."""
    A, th = two_windows(belts, goes(0.2, age_min=600))
    m1 = A[0].mechanisms[0]
    assert m1.coverage.value == 'partial'
    r = recommend(A, th)
    assert r.verdict != 'insufficient' and any('GOES' in x for x in r.reasons)


def test_goes_observation_does_not_cover_future_window(belts):
    """Т3/Т4: свежее наблюдение GOES покрывает только горизонт goes_max_age_min, не окно через 20 ч."""
    tr = traj(30 * 60, lambda i: i < 60)
    th = Thresholds()
    late = Window(T0 + timedelta(hours=20), 360)
    a = assess_window(late, tr, belts, goes(0.2), None, [], th, T0, mmod_hits=1e-6)
    f = next(x for x in a.mechanisms[0].factors if x.name.startswith('поток протонов GOES'))
    assert f.coverage.value == 'partial' and f.horizon_utc == T0 - timedelta(minutes=5) + timedelta(minutes=th.goes_max_age_min)
    assert 'не распространяется' in f.limits_note and 'прогноза' in f.limits_note
    assert any('GOES' in n and 'прогноза' in n for n in a.mechanisms[0].coverage_notes)
    # окно внутри горизонта наблюдения — покрытие полное
    now = Window(T0, 50)
    a2 = assess_window(now, tr, belts, goes(0.2), None, [], th, T0, mmod_hits=1e-6)
    f2 = next(x for x in a2.mechanisms[0].factors if x.name.startswith('поток протонов GOES'))
    assert f2.coverage.value == 'full'


def test_trade_off_when_mechanisms_disagree(belts):
    """Космопогода предпочитает второе окно, метеороиды — первое → компромисс без победителя."""
    tr = traj(24 * 60, lambda i: i < 60)
    th = Thresholds()
    w = [Window(T0, 360), Window(T0 + timedelta(minutes=480), 360)]
    A = [assess_window(w[0], tr, belts, goes(0.2), None, [], th, T0, mmod_hits=1e-7),
         assess_window(w[1], tr, belts, goes(0.2), None, [], th, T0, mmod_hits=3e-7)]
    r = recommend(A, th)
    assert r.verdict == 'trade_off' and 'mmod_stat' in r.per_mechanism_comparison


def test_minutes_and_fluence_inversion_is_trade_off_not_preferred(belts):
    """О3-1/Т3: меньше минут в аномалии, но флюенс выше вне допуска — противоречие внутри механизма,
    компромисс с текстом «по минутам лучше A, по флюенсу — B», а не «предпочтительно»."""
    # окно 1 (0–360): 60 мин аномалии на L=1,2 (слабый поток); окно 2 (480–840): 30 мин на L=1,3 (поток в сотни раз выше)
    def L_of(i):
        return 1.2 if i < 360 else 1.3

    def pattern(i):
        return i < 60 or 480 <= i < 510
    A, th = two_windows(belts, goes(0.2), pattern=pattern, L_of=L_of)
    m1, m2 = (next(x.value for x in a.mechanisms[0].factors if x.name == 'минут в аномалии') for a in A)
    f1, f2 = (next(x.value for x in a.mechanisms[0].factors if x.name.startswith('флюенс')) for a in A)
    assert m2 < m1 and f2 > f1 * th.fluence_equiv_ratio
    r = recommend(A, th)
    assert r.verdict == 'trade_off' and 'по минутам лучше окно 2' in r.rule_applied and 'по флюенсу — окно 1' in r.rule_applied
    assert 'флюенс' in r.per_mechanism_comparison['spaceweather']


def test_three_windows_equivalence_with_runner_up(belts):
    """Т3: 10/12/60 мин при допуске 5 — лучшее и второе равнозначны, третье хуже; не «preferred»."""
    def pattern(i):
        return i < 10 or 480 <= i < 492 or 960 <= i < 1020
    tr = traj(24 * 60, pattern)
    th = Thresholds(equiv_tol_min=5.0)
    w = [Window(T0, 360), Window(T0 + timedelta(minutes=480), 360), Window(T0 + timedelta(minutes=960), 360)]
    A = [assess_window(x, tr, belts, goes(0.2), None, [], th, T0, mmod_hits=1e-6) for x in w]
    r = recommend(A, th)
    assert r.verdict == 'equivalent' and 'окно 1' in r.rule_applied and 'окно 2' in r.rule_applied and 'окно 3' not in r.rule_applied
    assert 'хуже: окно 3' in r.per_mechanism_comparison['spaceweather']
    # с допуском 1 мин — лучшее окно 1
    th1 = Thresholds(equiv_tol_min=1.0)
    r1 = recommend(A, th1)
    assert r1.verdict == 'preferred' and r1.preferred.start_utc == T0


def test_kp_condition_only_when_fresh_and_declared(belts):
    """Т1/Т3: наблюдение Kp давностью 17 ч условие не ставит (фактор частичный, «устарело»);
    свежее Kp ≥ 7 — условие с явным распространением на окно и без слова «политика прототипа»."""
    A, th = two_windows(belts, goes(0.2), kp=kp_sample(7.0, age_min=17 * 60))
    assert not reasons_of(A)
    f = next(x for x in A[0].mechanisms[0].factors if x.name.startswith('Kp'))
    assert f.coverage.value == 'partial' and 'устарело' in f.limits_note and f.unit == ''
    A, th = two_windows(belts, goes(0.2), kp=kp_sample(7.33, age_min=30))
    rs = reasons_of(A)
    assert rs and all('наблюдение Kp 7,33' in x and 'распространено' in x and 'правилу команды' in x for x in rs)
    r = recommend(A, th)
    assert r.verdict == 'all_need_check'


def test_sep_level_defines_class_and_unknown_level_is_declared(belts):
    """Т3: уровень протонного события из записи: ≥1000 pfu — приоритетное; уровень не указан — сказано прямо."""
    from vkd.windows.scenario import Scenario, simulated_events
    tr = traj(24 * 60, lambda i: i < 60)
    th = Thresholds()
    w = Window(T0, 360)
    ev = simulated_events(T0, Scenario('x', sep_onset_offset_min=0, sep_level_pfu=5000.0))
    a = assess_window(w, tr, belts, goes(0.2), None, [], th, T0, mmod_hits=1e-6, events=ev)
    c = a.mechanisms[0].conditions[0]
    assert c.severity == 'critical' and 'S3' in c.text and 'приоритетное' in c.text and c.is_simulated and c.text.startswith('МОДЕЛИРУЕМОЕ')
    assert 'sep_valid_hours' in c.text and c.interval_utc[1] == T0 + timedelta(hours=th.sep_valid_hours)
    from vkd.types import EventInterval
    unknown = EventInterval('donki_msg#X', 'SEP', Kind.OBSERVATION, T0, None, True, True, T0, None, 'donki', T0, 'donki_msg#X', note='уведомление')
    a = assess_window(w, tr, belts, goes(0.2), None, [], th, T0, mmod_hits=1e-6, events=[unknown])
    c = a.mechanisms[0].conditions[0]
    assert c.severity == 'limiting' and 'уровень потока в записи не указан' in c.text and 'не выбирается автоматически' in c.text


def test_storm_signals_are_one_condition_with_sources(belts):
    """О2/Т3: уведомление о буре, прогноз ENLIL и свежее наблюдение Kp — одно условие «буря», три источника."""
    from vkd.types import EventInterval
    tr = traj(24 * 60, lambda i: i < 60)
    th = Thresholds()
    w = Window(T0, 360)
    evs = [EventInterval('donki_msg#GST', 'GST', Kind.EXTERNAL_FORECAST, T0 - timedelta(hours=1), None, True, True,
                         T0 - timedelta(hours=1), None, 'donki', T0 - timedelta(hours=1), 'donki_msg#GST', note='gstID, сообщение, Kp до 7.67'),
           EventInterval('donki_enlil#A#1', 'CME_ARRIVAL', Kind.EXTERNAL_FORECAST, T0 + timedelta(hours=2), None, True, True,
                         T0 + timedelta(hours=2), None, 'donki', T0 - timedelta(hours=10), 'donki_enlil#A#1', note='WSA-ENLIL: приход, Kp до 8')]
    a = assess_window(w, tr, belts, goes(0.2), kp_sample(7.0, 30), [], th, T0, mmod_hits=1e-6, events=evs)
    conds = [c for c in a.mechanisms[0].conditions if c.kind == 'GST']
    # счёт называет и сигналы, и записи: «(1 источник)» при двух уведомлениях создавало
    # впечатление, что весь сигнал стоит в одной записи (находка четвёртого круга)
    assert len(conds) == 1 and len(conds[0].sources_ru) == 3 and '3 сигнала' in conds[0].text
    assert set(conds[0].event_ids) == {'donki_msg#GST', 'donki_enlil#A#1', 'rec#kp'}
    assert len(a.mechanisms[0].conditions) == 1          # буря не размножается на три условия


def test_cutoff_availability_factor_from_trajectory(belts):
    """CONTRACT §3 «минут доступности частиц канала»: по cutoff_GV трассы, ноль объявляется словами."""
    tr = traj(24 * 60, lambda i: i < 60, cutoff_of=lambda i: 0.1 if i < 30 else 5.0)
    th = Thresholds()
    a = assess_window(Window(T0, 360), tr, belts, goes(0.2), None, [], th, T0, mmod_hits=1e-6)
    f10 = next(x for x in a.mechanisms[0].factors if x.name.startswith('минут доступности протонов ≥10 МэВ'))
    f100 = next(x for x in a.mechanisms[0].factors if x.name.startswith('минут доступности протонов ≥100 МэВ'))
    assert f10.value == 30.0 and f100.value == 30.0 and f10.kind.value == 'own_calculation'
    assert 0.13 < rigidity_GV(10.0) < 0.14 and 0.44 < rigidity_GV(100.0) < 0.45
    a0 = assess_window(Window(T0 + timedelta(hours=6), 360), tr, belts, goes(0.2), None, [], th, T0, mmod_hits=1e-6)
    f = next(x for x in a0.mechanisms[0].factors if x.name.startswith('минут доступности протонов ≥10'))
    assert f.value == 0.0 and 'недоступны' in f.limits_note


def test_meteor_shower_factor_is_flag_not_flux(belts):
    """О1: календарь IMO даёт признак активности (эта-Аквариды 3 мая), вклад в N не считается — сказано прямо."""
    A, th = two_windows(belts, goes(0.2))
    f = next(x for m in A[0].mechanisms if m.mechanism_id == 'mmod_stat' for x in m.factors if 'метеорных потоков' in x.name)
    assert f.value == 1.0 and f.presence.value == 'detected' and 'эта-Аквариды' in f.limits_note and 'не рассчитан' in f.limits_note
    r = recommend(A, th)
    assert 'роль' in r.per_mechanism_comparison['mmod_stat']
