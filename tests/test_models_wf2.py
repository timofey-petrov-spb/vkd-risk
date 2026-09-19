# -*- coding: utf-8 -*-
"""Разбор 19.09 (волна 2), область «модели»: отбор событий по действию и пересечению,
покрытие каналов GOES и Kp, устойчивость по паре «вердикт + окно», происхождение условий
и русские имена в объяснениях.

Каждый тест закрепляет один найденный дефект, а не «работает вообще».
"""
import re
from datetime import datetime, timedelta, timezone

import pytest

from app.compute import run
from tests.test_integration import _fetched
from tests.test_compare import belts, goes, kp_sample, traj, two_windows      # noqa: F401 — фикстура belts
from vkd.explain.cards import SOURCE_ID_RU, cards_for_window, short_note, source_ru
from vkd.types import (Coverage, EnvironmentSample, EventInterval, FactorValue, Kind, MechanismAssessment,
                       Presence, Recommendation, Window, WindowAssessment)
from vkd.windows.compare import Thresholds, action_span, assess_window, overlaps, recommend

UTC = timezone.utc
T0 = datetime(2024, 5, 3, 12, 0, tzinfo=UTC)          # совпадает с tests/test_compare.py
T_GANNON = datetime(2024, 5, 10, 12, 0, tzinfo=UTC)


def sep_event(start, end=None, kind=Kind.OBSERVATION, eid='donki_msg#test', note='', published=None):
    return EventInterval(event_id=eid, kind_of_event='SEP', kind=kind, start_utc=start, end_utc=end,
                         start_uncertain=True, end_uncertain=end is None, valid_from_utc=start, valid_to_utc=end,
                         source_id='nasa_donki_notification', published_utc=published or start,
                         raw_record_id=eid, note=note)


def assess(win, events=(), th=None, belts_=None, g=None, kp=None, now=None):
    th = th or Thresholds()
    tr = traj(72 * 60, lambda i: i < 60)
    return assess_window(win, tr, belts_, g, kp, [], th, now or T0, mmod_hits=1e-6, events=list(events))


# ------------------------------------------------------- M1: конец действия до проверки пересечения
def test_sep_without_declared_end_does_not_reach_window_beyond_assumed_action(belts):
    """Протонное событие 09.05 14:00 без объявленного конца действует 24 ч (sep_valid_hours):
    окно, начинающееся 10.05 16:00, оно не пересекает. Конец действия обязан вычисляться ДО
    проверки пересечения, иначе «конец неизвестен» означает «действует в любом окне»."""
    th = Thresholds(sep_valid_hours=24.0)
    e = sep_event(datetime(2024, 5, 9, 14, 0, tzinfo=UTC))
    a0, a1 = action_span(e, th)
    assert a1 == datetime(2024, 5, 10, 14, 0, tzinfo=UTC)

    tr = traj(72 * 60, lambda i: i < 60)
    far = Window(datetime(2024, 5, 10, 16, 0, tzinfo=UTC), 360)
    a = assess_window(far, tr, belts, None, None, [], th, T0, mmod_hits=1e-6, events=[e])
    assert not [c for m in a.mechanisms for c in m.conditions]

    near = Window(datetime(2024, 5, 10, 10, 0, tzinfo=UTC), 360)
    b = assess_window(near, tr, belts, None, None, [], th, T0, mmod_hits=1e-6, events=[e])
    assert [c for m in b.mechanisms for c in m.conditions if c.kind == 'SEP']


def test_action_end_uses_each_record_own_start_not_cluster_start(belts):
    """Конец действия кластера считается по собственному началу каждой записи: иначе поздняя
    запись получала конец, отсчитанный от начала кластера, и действие оказывалось короче."""
    th = Thresholds(sep_valid_hours=24.0)
    e1 = sep_event(datetime(2024, 5, 9, 14, 0, tzinfo=UTC), eid='donki_msg#a')
    e2 = sep_event(datetime(2024, 5, 9, 22, 0, tzinfo=UTC), eid='donki_msg#b')
    tr = traj(72 * 60, lambda i: i < 60)
    w = Window(datetime(2024, 5, 10, 16, 0, tzinfo=UTC), 360)
    a = assess_window(w, tr, belts, None, None, [], th, T0, mmod_hits=1e-6, events=[e1, e2])
    cond = [c for m in a.mechanisms for c in m.conditions if c.kind == 'SEP']
    assert cond and cond[0].interval_utc[1] == datetime(2024, 5, 10, 22, 0, tzinfo=UTC)


# ------------------------------------------------------- M2: пересечение не меньше минуты
def test_overlap_shorter_than_a_minute_is_not_an_intersection(belts):
    """Публикация реестра A1 имеет секунды (2024-05-09T14:00:13Z). Окно, начинающееся ровно
    через 24 ч, пересекается с действием на 13 с — это не пересечение."""
    th = Thresholds(sep_valid_hours=24.0)
    e = sep_event(datetime(2024, 5, 9, 14, 0, 13, tzinfo=UTC))
    tr = traj(72 * 60, lambda i: i < 60)
    w = Window(datetime(2024, 5, 10, 14, 0, tzinfo=UTC), 360)
    a = assess_window(w, tr, belts, None, None, [], th, T0, mmod_hits=1e-6, events=[e])
    assert not [c for m in a.mechanisms for c in m.conditions]
    # минута и больше — пересечение есть
    w2 = Window(datetime(2024, 5, 10, 13, 59, tzinfo=UTC), 360)
    b = assess_window(w2, tr, belts, None, None, [], th, T0, mmod_hits=1e-6, events=[e])
    assert [c for m in b.mechanisms for c in m.conditions if c.kind == 'SEP']


def test_same_overlap_rule_in_compare_and_compute():
    """Одно правило пересечения в обоих местах: отбор по горизонту в app/compute.py и отбор
    условий в compare.py — одна и та же функция, а не две похожие проверки."""
    import app.compute as ac
    import vkd.windows.compare as cmp
    assert ac.overlaps is cmp.overlaps and ac.action_span is cmp.action_span
    a0 = datetime(2024, 5, 9, 14, 0, 13, tzinfo=UTC)
    a1 = a0 + timedelta(hours=24)
    assert not overlaps(a0, a1, datetime(2024, 5, 10, 14, 0, tzinfo=UTC), datetime(2024, 5, 10, 20, 0, tzinfo=UTC))
    assert overlaps(a0, a1, datetime(2024, 5, 10, 13, 59, tzinfo=UTC), datetime(2024, 5, 10, 20, 0, tzinfo=UTC))
    assert not overlaps(None, a1, a0, a1)


# ------------------------------------------------------- M3: наблюдение GOES не покрывает окно
def test_goes_observation_outside_window_is_unknown_not_not_detected(belts):
    """Горизонт наблюдения GOES кончился до начала окна: «не выявлено» писать нельзя —
    наличие события в окне неизвестно. Покрытие канала остаётся частичным (не NONE),
    иначе любое окно через 20 ч давало бы «оснований недостаточно»."""
    th = Thresholds()
    tr = traj(30 * 60, lambda i: i < 60)
    late = Window(T0 + timedelta(hours=20), 360)
    a = assess_window(late, tr, belts, goes(0.2), kp_sample(3.0, age_min=30), [], th, T0, mmod_hits=1e-6)
    f = next(x for x in a.mechanisms[0].factors if x.name.startswith('поток протонов GOES'))
    assert f.presence == Presence.UNKNOWN
    assert f.coverage == Coverage.PARTIAL
    assert 'ниже S1' in f.limits_note and '2024-05-03 11:55Z' in f.limits_note   # уровень и время наблюдения остались
    assert any('наблюдение GOES не покрывает окно' in n and 'до его начала' in n
               for n in a.mechanisms[0].coverage_notes)
    assert a.mechanisms[0].coverage == Coverage.PARTIAL
    # окно внутри горизонта: наличие определяется как прежде
    near = Window(T0, 50)
    b = assess_window(near, tr, belts, goes(0.2), kp_sample(3.0, age_min=30), [], th, T0, mmod_hits=1e-6)
    fb = next(x for x in b.mechanisms[0].factors if x.name.startswith('поток протонов GOES'))
    assert fb.presence == Presence.NOT_DETECTED and fb.coverage == Coverage.FULL


# ------------------------------------------------------- M4: канал Kp в покрытии и заметках
def test_missing_kp_is_declared_in_coverage_and_never_improves_verdict(belts):
    """Исключённый или отсутствующий Kp прежде не отражался в покрытии механизма и в заметках:
    вердикт с Kp и без Kp выглядел одинаково уверенным."""
    th = Thresholds()
    tr = traj(24 * 60, lambda i: i < 60)
    w = Window(T0, 50)                       # окно внутри горизонта GOES: остаётся только канал Kp
    a_on = assess_window(w, tr, belts, goes(0.2), kp_sample(3.0, age_min=30), [], th, T0, mmod_hits=1e-6)
    a_off = assess_window(w, tr, belts, goes(0.2), None, [], th, T0, mmod_hits=1e-6)
    assert a_on.mechanisms[0].coverage == Coverage.FULL
    assert a_off.mechanisms[0].coverage == Coverage.PARTIAL
    assert not any(n.startswith('Kp: наблюдения нет') for n in a_on.mechanisms[0].coverage_notes)
    assert any(n.startswith('Kp: наблюдения нет') for n in a_off.mechanisms[0].coverage_notes)

    A_off, th = two_windows(belts, goes(0.2), kp=None)
    r_off = recommend(A_off, th)
    assert any('Kp' in x for x in r_off.reasons)

    A_on, th2 = two_windows(belts, goes(0.2), kp=kp_sample(3.0, age_min=30))
    r_on = recommend(A_on, th2)
    # без Kp вердикт не может быть благоприятнее, чем с Kp
    rank = {'insufficient': 0, 'all_need_check': 1, 'trade_off': 2, 'equivalent': 3, 'preferred': 4}
    assert rank[r_off.verdict] <= rank[r_on.verdict]
    assert 'покрытие частичное — объявлено' in r_off.rule_applied


def test_strict_mode_without_kp_declares_the_cutoff_reason():
    r = run('history_forecast', T_GANNON, 360, 720, [0, 240], fetched=_fetched(), now=T_GANNON)
    m1 = r.assessments[0].mechanisms[0]
    assert any(n.startswith('Kp: наблюдения нет') and 'отсечки' in n for n in m1.coverage_notes)


# ------------------------------------------------------- M5: устойчивость по паре «вердикт + окно»
def _fake_assessment(start, minutes, fluence):
    f = (FactorValue('минут в аномалии', minutes, 'мин', Kind.OWN_CALCULATION, Presence.DETECTED, Coverage.FULL, (), '', ''),
         FactorValue('флюенс захваченных протонов ≥30 МэВ', fluence, 'част./см²', Kind.OWN_CALCULATION,
                     Presence.DETECTED, Coverage.FULL, (), '', ''))
    m = MechanismAssessment(mechanism_id='spaceweather', mandatory=True, factors=f, coverage=Coverage.FULL, needs_check=False)
    return WindowAssessment(window=Window(start, 360), mechanisms=(m,), coverage_declared=(), coverage_missing=())


def test_stability_accounts_for_verdict_not_only_preferred_window():
    """M5: на сетке менялся вердикт (компромисс ↔ равнозначны) при preferred = None в обоих
    случаях, и экран печатал «выбор устойчив». Устойчивость — по паре (вердикт, окно)."""
    from vkd.windows.sensitivity import robustness
    A = [_fake_assessment(T0, 60.0, 1e5), _fake_assessment(T0 + timedelta(hours=8), 62.0, 1.1e5)]

    def _assess_all(tr, kw):
        return A

    def _decide(A_i, kw):
        verdict = 'trade_off' if kw['saa_B_threshold_nT'] < 24000.0 else 'equivalent'
        return Recommendation(preferred=None, verdict=verdict, rule_applied='', per_mechanism_comparison={}, reasons=())

    rob = robustness([], [a.window for a in A], _assess_all, _decide,
                     thr_grid=[22000.0, 24000.0, 26000.0], e_grid=[30.0],
                     base_thr=24000.0, base_e=30.0)
    assert set(rob.preferred_starts.values()) == {None}          # предпочтительного окна нет нигде
    assert set(rob.verdict_by_grid.values()) == {'trade_off', 'equivalent'}
    assert rob.stable is False


def test_stability_true_when_verdict_and_window_agree_on_grid():
    from vkd.windows.sensitivity import robustness
    A = [_fake_assessment(T0, 10.0, 1e5), _fake_assessment(T0 + timedelta(hours=8), 200.0, 9e5)]

    def _assess_all(tr, kw):
        return A

    def _decide(A_i, kw):
        return Recommendation(preferred=A[0].window, verdict='preferred', rule_applied='', per_mechanism_comparison={}, reasons=())

    rob = robustness([], [a.window for a in A], _assess_all, _decide,
                     thr_grid=[22000.0, 24000.0], e_grid=[30.0], base_thr=24000.0, base_e=30.0)
    assert rob.stable is True


def test_tolerance_basis_text_names_verdict_and_window():
    """Подпись говорит и про вердикт, и про сетку. С третьего круга «предпочтительное окно»
    называется только там, где оно есть: на «Гэнноне» его нет ни здесь, ни в ячейках сетки,
    и подпись обязана сказать именно это (ветки — tests/test_models_round3.py)."""
    r = run('history_forecast', T_GANNON, 360, 720, [0, 240], fetched=_fetched(), now=T_GANNON)
    t = r.rec.tolerance_basis
    assert r.rec.preferred is None and all(v is None for v in r.rob.preferred_starts.values())
    assert 'предпочтительного окна нет ни в одной ячейке сетки' in t
    assert 'вердикт' in t and 'выбор устойчив' not in t
    assert 'verdict_by_grid' in r.S['robustness'] and r.S['robustness']['verdict_by_grid']


# ------------------------------------------------------- M6: давность Kp одинакова в таблице и в факторе
def test_kp_age_in_sources_table_equals_age_in_factor():
    """M6: таблица источников считала давность от начала 3-часового интервала GFZ, а фактор —
    от его конца; расхождение ровно 3 ч на одном экране."""
    import os
    from vkd.sources import Fetch
    from vkd.orbit.trajectory import satellite_from_tle
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tle_path = os.path.join(ROOT, 'data', 'orbit', 'iss.tle')
    epoch = satellite_from_tle(open(tle_path, 'rb').read()).epoch.utc_datetime()
    t0 = (epoch + timedelta(days=1)).replace(second=0, microsecond=0)
    tle_text = open(tle_path, encoding='utf-8').read()

    vf = t0 - timedelta(hours=4)
    kp = EnvironmentSample(vf, 'kp', 3.0, '', 'gfz_kp', Kind.OBSERVATION, None, vf, vf + timedelta(hours=3),
                           t0, 'final', 'gfz_kp#test')
    (g, g_raw, f_goes), _, (txt, f_tle), noaa = _fetched(tle_text, goes_at=t0)
    f_kp = Fetch('gfz_kp', True, False, t0, 60.0, 'тестовое наблюдение', None, None)
    r = run('live', t0, 360, 720, [0, 240],
            fetched=((g, g_raw, f_goes), (kp, {'gfz_kp#test': {}}, f_kp), (txt, f_tle), noaa), now=t0)
    age_table = r.S['sources']['gfz_kp']['age_min']
    kpf = next(f for f in r.assessments[0].mechanisms[0].factors if f.name.startswith('Kp'))
    age_factor = float(kpf.limits_note.split('давность ')[1].split(' мин')[0])
    assert age_table == pytest.approx(age_factor, abs=1.0)
    assert age_table == 60          # конец интервала t0 − 1 ч, не начало t0 − 4 ч


# ------------------------------------------------------- M7: отдельная настройка действия бури и прихода выброса
def test_event_valid_hours_is_a_separate_setting_and_is_printed(belts):
    from vkd.config import section
    th_file = Thresholds.from_settings()
    assert th_file.event_valid_hours == float(section('history')['event_valid_hours'])
    assert th_file.sep_valid_hours == float(section('history')['sep_valid_hours'])

    th = Thresholds(event_valid_hours=6.0)
    gst = EventInterval(event_id='donki_msg#gst', kind_of_event='GST', kind=Kind.OBSERVATION,
                        start_utc=T0, end_utc=None, start_uncertain=True, end_uncertain=True,
                        valid_from_utc=T0, valid_to_utc=None, source_id='nasa_donki_notification',
                        published_utc=T0, raw_record_id='donki_msg#gst', note='GST-001, сообщение x, Kp до 8')
    a0, a1 = action_span(gst, th)
    assert a1 == T0 + timedelta(hours=6)
    tr = traj(24 * 60, lambda i: i < 60)
    a = assess_window(Window(T0, 360), tr, belts, None, None, [], th, T0, mmod_hits=1e-6, events=[gst])
    c = next(c for m in a.mechanisms for c in m.conditions if c.kind == 'GST')
    assert 'настройка event_valid_hours' in c.text and 'принято 6 ч' in c.text
    assert 'sep_valid_hours' not in c.text


# ------------------------------------------------------- M8: происхождение условия по записям
def test_sep_condition_kind_follows_record_kind_not_event_type(belts):
    """Уведомления «SEP Prediction» (модель REleASE) имеют kind external_forecast: карточка
    условия не имеет права называть их наблюдением."""
    th = Thresholds()
    tr = traj(24 * 60, lambda i: i < 60)
    w = Window(T0, 360)

    fc = sep_event(T0 - timedelta(minutes=30), kind=Kind.EXTERNAL_FORECAST, eid='donki_msg#fc', note='SEP Prediction')
    a = assess_window(w, tr, belts, None, None, [], th, T0, mmod_hits=1e-6, events=[fc])
    cards = cards_for_window(a, {}, events=[fc], window_index=1)
    card = next(c for c in cards if c.title.startswith('Окно 1 · Условие'))
    assert card.kind == Kind.EXTERNAL_FORECAST
    assert 'прогноз модели, не наблюдение' in card.limits_ru
    assert 'событие наблюдено' not in card.limits_ru

    obs = sep_event(T0 - timedelta(minutes=20), kind=Kind.OBSERVATION, eid='donki_msg#obs', note='has reached')
    b = assess_window(w, tr, belts, None, None, [], th, T0, mmod_hits=1e-6, events=[fc, obs])
    cards_b = cards_for_window(b, {}, events=[fc, obs], window_index=1)
    card_b = next(c for c in cards_b if c.title.startswith('Окно 1 · Условие'))
    assert card_b.kind == Kind.OBSERVATION and 'часть записей — прогноз модели' in card_b.limits_ru

    c2 = assess_window(w, tr, belts, None, None, [], th, T0, mmod_hits=1e-6, events=[obs])
    cards_c = cards_for_window(c2, {}, events=[obs], window_index=1)
    card_c = next(c for c in cards_c if c.title.startswith('Окно 1 · Условие'))
    assert card_c.kind == Kind.OBSERVATION and 'событие наблюдено' in card_c.limits_ru


# ------------------------------------------------------- M9: год публикации, если он не совпадает с годом окна
def test_publication_range_shows_year_when_it_differs_from_window_year():
    """M9: год печатается, если он отличается от года окна («05-07 — 03-12» читалось как ход назад).

    После стыка A2 все записи конвейера — уведомления 2024 года (переанализы поздних карточек
    ENLIL сняты по R10), поэтому правило проверяется на самой функции формата и на том,
    что реальные диапазоны публикации читаются вперёд."""
    from vkd.windows.compare import _pub_time
    assert _pub_time(datetime(2025, 3, 12, 16, 52, tzinfo=UTC), 2024) == '2025-03-12 16:52Z'
    assert _pub_time(datetime(2024, 5, 7, 14, 21, tzinfo=UTC), 2024) == '05-07 14:21Z'
    r = run('history_review', T_GANNON, 360, 720, [0, 240], fetched=_fetched(), now=T_GANNON)
    # Условие бури печатает публикацию ПОКАЗАПИСНО — одним временем рядом с номером выпуска,
    # из тела которого взято число (четвёртый круг: связка «приход — Kp» только внутри записи).
    # Диапазон публикации остаётся у кластера протонного события, в перечне источников условия.
    texts = [t for a in r.assessments for m in a.mechanisms for c in m.conditions
             for t in (c.text,) + tuple(c.sources_ru) if 'публикация ' in t]
    assert texts
    parts = [t.split('публикация ')[i + 1].split(')')[0] for t in texts
             for i in range(t.count('публикация '))]
    ranges = [x for x in parts if ' — ' in x]
    assert ranges, parts
    for x in ranges:                     # диапазон читается вперёд: конец не раньше начала
        lo, hi = [p.strip() for p in x.split(' — ')]
        norm = lambda s: s if s.startswith('20') else '2024-' + s
        assert norm(lo) <= norm(hi), x
    # одиночные времена публикации тоже читаются в едином виде: «05-08 18:43Z» или с годом
    for x in (p for p in parts if ' — ' not in p):
        assert re.fullmatch(r'(?:\d{4}-)?\d{2}-\d{2} \d{2}:\d{2}Z', x.strip()), x


# ------------------------------------------------------- M10: карточка сценария
def test_scenario_card_never_claims_observation_or_publication():
    from vkd.windows.scenario import Scenario
    sc = Scenario('ui', sep_onset_offset_min=60, sep_level_pfu=5000.0, kp_override=8.0)
    r = run('history_forecast', T_GANNON, 360, 720, [0, 240], fetched=_fetched(), now=T_GANNON, scenario=sc)
    sim = [c for c in r.cards if c.title.startswith('Окно 1 · Сценарий')]
    assert sim
    for c in sim:
        assert 'событие наблюдено' not in c.limits_ru
        assert 'опубликованы до отсечки' not in c.source_ru
        assert 'задано пользователем в сценарии' in c.source_ru
        assert 'scenario' not in c.source_ru and 'scenario' not in c.limits_ru
    kp_card = next(c for c in r.cards if c.title.startswith('Окно 1 · Kp'))
    assert 'scenario' not in kp_card.source_ru and 'сценарии' in kp_card.source_ru


# ------------------------------------------------------- M12: без обрывков и идентификаторов кода
def test_condition_sources_have_no_chopped_notes_or_code_identifiers():
    r = run('history_review', T_GANNON, 360, 720, [0, 240], fetched=_fetched(), now=T_GANNON)
    conds = [c for c in r.cards if c.title.startswith(('Окно 1 · Условие', 'Окно 2 · Условие'))]
    assert conds
    joined = ' '.join(c.source_ru for c in conds)
    for sid in ('nasa_donki_notification', 'nasa_donki_wsa_enlil', 'nasa_donki_sep_card',
                'nasa_iswa_goes_primary_p5m', 'gfz_kp_archive'):
        assert sid not in joined, sid                    # идентификаторов кода на экране нет
    assert 'уведомление NASA DONKI' in joined
    # номер выпуска источника остаётся виден: запись должна оставаться находимой
    from vkd.explain.cards import record_ru
    assert 'уведомление NASA DONKI 20240510-AL-004, протонное событие' in joined
    # заметка записи не режется по символам: либо целиком, либо по границе слова с многоточием
    shown = {e.event_id for c in conds for e in r.events if record_ru(e.event_id) in c.source_ru}
    assert shown
    for e in r.events:
        if e.event_id in shown and e.note:
            assert short_note(e.note) in joined, e.event_id
    for piece in joined.split('; '):
        assert not piece.endswith(('(по', 'Space Wea')), piece


def test_short_note_cuts_on_word_boundary_and_keeps_short_notes_whole():
    assert short_note('коротко') == 'коротко'
    long = 'слово ' * 60
    out = short_note(long)
    assert out.endswith('…') and len(out) <= 161 and out[:-1].strip().endswith('слово')
    assert short_note('a' * 300).endswith('…')


def test_short_note_ne_rezhet_frazu_vnutri_skobki():
    """Заметка уведомления DONKI о приходе выброса длиннее предела на один символ, и обрез по
    границе слова приходился внутрь скобки: «…(диапазон 6–8, верхняя граница, не…» — открытая
    скобка и отрицание без продолжения в самой читаемой карточке «Гэннон». Режем по границе
    пункта: остаётся целая фраза. Сам диапазон Kp не теряется — он стоит в той же карточке
    рядом с номером уведомления, из которого взят."""
    note = ('Модельный приход CME к Земле; неопределённость времени не является длительностью бури; '
            'опубликованный прогноз: Kp до 8 (диапазон 6–8, верхняя граница, не kp_90) ')
    out = short_note(note.strip() + ' хвост')
    assert out == 'Модельный приход CME к Земле; неопределённость времени не является длительностью бури…', out
    assert out.count('(') == out.count(')')
    # границы пункта нет вовсе — режем по слову, но не внутрь незакрытой скобки
    no_clause = 'приход выброса (модель WSA-ENLIL, ' + 'очень длинное слово ' * 12
    out2 = short_note(no_clause)
    assert out2.count('(') == out2.count(')'), out2
    assert out2.endswith('…') and 'приход выброса' in out2, out2


def test_source_id_translation_covers_used_sources():
    for sid in ('nasa_donki_notification', 'nasa_donki_wsa_enlil', 'gfz_kp', 'gfz_kp_archive',
                'noaa_swpc_goes', 'scenario'):
        assert sid in SOURCE_ID_RU and source_ru(sid) != sid
    assert source_ru('неизвестный_источник') == 'неизвестный_источник'


def test_settings_keys_are_all_read_by_code():
    """Т7: каждый ключ настроек читается кодом — новый event_valid_hours не исключение."""
    from vkd.config import section
    hist = section('history')
    assert 'event_valid_hours' in hist
    th = Thresholds.from_settings()
    assert th.event_valid_hours == float(hist['event_valid_hours'])
