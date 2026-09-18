# -*- coding: utf-8 -*-
"""Временная честность (Т4) и сценарии (О3/О7). CONTRACT v3.1, раздел 8."""
from datetime import datetime, timedelta, timezone

from vkd.assess.cutoff import apply_cutoff
from vkd.types import EnvironmentSample, EventInterval, Kind
from vkd.windows.scenario import Scenario, apply_to_windows, simulated_events, simulated_kp
from vkd.types import Window

T = datetime(2024, 5, 10, 19, 0, tzinfo=timezone.utc)      # отсечка: буря Гэннон, вечер 10 мая


def sample(t_obs, published, kind=Kind.OBSERVATION, rid='r'):
    return EnvironmentSample(t_obs, 'kp', 5.0, '', 'src', kind, published, None, None, T, 'final', rid)


def test_nested_future_inside_old_record_is_excluded():
    """Карточка опубликована 18:44, но внутри наблюдение 12 мая 06:00 — исключить (разбор Codex п. 1)."""
    pub = datetime(2024, 5, 10, 18, 44, tzinfo=timezone.utc)
    old = sample(datetime(2024, 5, 10, 18, 0, tzinfo=timezone.utc), pub, rid='kp_1800')
    fut = sample(datetime(2024, 5, 12, 6, 0, tzinfo=timezone.utc), pub, rid='kp_0512')
    r = apply_cutoff([old, fut], [], [], T)
    assert [s.raw_record_id for s in r.samples] == ['kp_1800']
    assert any('kp_0512' in x and 'позже отсечки' in x for x in r.excluded)


def test_unknown_publication_time_is_unusable_in_strict_mode():
    s = sample(datetime(2024, 5, 10, 18, 0, tzinfo=timezone.utc), None, rid='nopub')
    r = apply_cutoff([s], [], [], T)
    assert not r.samples and any('непригодно' in x for x in r.excluded)
    assert apply_cutoff([s], [], [], None).samples == (s,)      # разбор после факта — используется


def test_timely_forecast_of_future_is_allowed():
    """R6: своевременный прогноз на будущее разрешён; будущее наблюдение — нет."""
    pub = datetime(2024, 5, 10, 18, 44, tzinfo=timezone.utc)
    fc = sample(datetime(2024, 5, 11, 3, 0, tzinfo=timezone.utc), pub, kind=Kind.EXTERNAL_FORECAST, rid='fc')
    ob = sample(datetime(2024, 5, 11, 3, 0, tzinfo=timezone.utc), pub, kind=Kind.OBSERVATION, rid='ob')
    r = apply_cutoff([fc, ob], [], [], T)
    assert [s.raw_record_id for s in r.samples] == ['fc']


def test_adding_future_data_does_not_change_past_result():
    """Обязательный тест Т4: добавление сведений после отсечки не меняет отобранный набор."""
    pub = datetime(2024, 5, 10, 18, 44, tzinfo=timezone.utc)
    base = [sample(datetime(2024, 5, 10, 15, 0, tzinfo=timezone.utc), pub, rid='a')]
    later = base + [sample(datetime(2024, 5, 10, 21, 0, tzinfo=timezone.utc), datetime(2024, 5, 10, 21, 35, tzinfo=timezone.utc), rid='b'),
                    sample(datetime(2024, 5, 10, 16, 0, tzinfo=timezone.utc), pub, rid='c_nested_future_free')]
    r1, r2 = apply_cutoff(base, [], [], T), apply_cutoff(later, [], [], T)
    assert {s.raw_record_id for s in r1.samples} <= {s.raw_record_id for s in r2.samples}
    assert 'b' not in {s.raw_record_id for s in r2.samples}


def test_event_with_notification_time_kept_and_marked():
    ev = EventInterval('donki_msg#20240510-AL-013', 'GST', Kind.EXTERNAL_FORECAST,
                       datetime(2024, 5, 10, 15, 0, tzinfo=timezone.utc), None, True, True,
                       datetime(2024, 5, 10, 18, 44, tzinfo=timezone.utc), None, 'donki',
                       datetime(2024, 5, 10, 18, 44, tzinfo=timezone.utc), 'donki_msg#20240510-AL-013')
    late = EventInterval('donki_msg#20240510-AL-014', 'GST', Kind.EXTERNAL_FORECAST, None, None, True, True,
                         datetime(2024, 5, 10, 19, 19, tzinfo=timezone.utc), None, 'donki',
                         datetime(2024, 5, 10, 19, 19, tzinfo=timezone.utc), 'donki_msg#20240510-AL-014')
    r = apply_cutoff([], [ev, late], [], T)
    assert [e.event_id for e in r.events] == ['donki_msg#20240510-AL-013']


def test_scenario_marks_simulated_and_shifts_plan():
    t0 = T
    sc = Scenario('s1', work_delay_min=60, sep_onset_offset_min=120, sep_level_pfu=50.0, kp_override=7.5)
    w = apply_to_windows([Window(t0, 360)], sc)
    assert w[0].start_utc == t0 + timedelta(minutes=60) and w[0].duration_min == 360
    ev = simulated_events(t0, sc)
    assert ev and ev[0].is_simulated and ev[0].kind_of_event == 'SEP' and ev[0].published_utc is None
    k = simulated_kp(None, t0, sc)
    assert k.value == 7.5 and k.source_id == 'scenario' and k.quality == 'model'
    # синтетика непригодна для строгого replay: без времени публикации она отсекается
    assert not apply_cutoff([k], ev, [], T).samples and not apply_cutoff([k], ev, [], T).events
