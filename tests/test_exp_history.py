# -*- coding: utf-8 -*-
"""Поставщик истории (experiments/stub_history): происхождение уведомлений по телу (О4/Т3),
карточки событий — только разбор после факта (CONTRACT §10, Т4), публикация ENLIL по политике
R10 (Т5-1), окончательный ряд Kp GFZ (Т1), происхождение архива в сырых записях (Т1)."""
from datetime import datetime, timedelta, timezone

import experiments.stub_history as SH
from vkd.assess.cutoff import apply_cutoff

UTC = timezone.utc


def _bundle():
    return SH.history_bundle()


def test_notification_kind_from_message_body():
    ev, raw = SH.notifications_events()
    by = {e.event_id: e for e in ev}
    assert by['donki_msg#20240510-AL-013'].kind.value == 'observation'          # «Kp index has reached level 7.67»
    assert by['donki_msg#20240510-AL-004'].kind.value == 'observation'          # «SEP event detected by GOES»
    assert by['donki_msg#20240509-AL-004'].kind.value == 'external_forecast'    # «SEP Prediction» (модель REleASE)
    assert raw['donki_msg#20240510-AL-013']['kind'] == 'observation' and raw['donki_msg#20240510-AL-013']['kp_in_body'] == 7.67
    assert raw['donki_msg#20240510-AL-013']['strict_replay_eligibility'] == 'dated_notification_pending_content_and_version_audit'
    assert 'аудит содержания и версий не завершён' in by['donki_msg#20240510-AL-013'].note
    assert raw['donki_msg#20240510-AL-013']['archive_sha256'] and raw['donki_msg#20240510-AL-013']['archive_api_url'].startswith('https://api.nasa.gov/DONKI/GST')


def test_sep_cards_have_no_publication_and_are_excluded_in_strict_mode():
    ev, raw = SH.sep_events()
    assert ev and all(e.published_utc is None and e.source_id == 'nasa_donki_sep_card' for e in ev)
    assert all(raw[e.event_id]['strict_replay_eligibility'].startswith('event_card') for e in ev)
    samples, events, _ = _bundle()
    cut = apply_cutoff(samples, events, [], datetime(2024, 5, 10, 19, tzinfo=UTC))
    assert not any(e.event_id.startswith('donki_sep#') for e in cut.events)
    assert any(x.startswith('donki_sep#') and 'непригодно' in x for x in cut.excluded)
    # уведомления о протонном событии остаются — условие SEP строгий режим не теряет
    assert any(e.kind_of_event == 'SEP' and e.published_utc is not None for e in cut.events)
    # разбор после факта — карточки используются
    assert any(e.event_id.startswith('donki_sep#') for e in apply_cutoff(samples, events, [], None).events)


def test_enlil_publication_not_earlier_than_analysis_submission():
    ev, raw = SH.enlil_arrivals()
    lag = timedelta(minutes=SH.ENLIL_PUBLICATION_LAG_MIN)
    late = [e for e in ev if raw[e.event_id]['analysis_submissionTime'] and raw[e.event_id]['analysis_submissionTime'] > '2024-07']
    assert late and all(e.published_utc == SH._t(raw[e.event_id]['analysis_submissionTime']) for e in late)
    assert any(e.published_utc.year >= 2025 for e in late)
    for e in ev:
        r = raw[e.event_id]
        mc = SH._t(r['modelCompletionTime'])
        sub = SH._t(r['analysis_submissionTime'])
        assert e.published_utc >= mc + lag and (sub is None or e.published_utc >= sub)
        assert 'доступность не доказана' in e.note and r['enlil_publication_lag_min'] == SH.ENLIL_PUBLICATION_LAG_MIN
    # прогон 9.05 20:28 с анализом, поданным в 2025 году, отсечкой 10.05 12:00 исключается
    samples, events, _ = _bundle()
    cut = apply_cutoff(samples, events, [], datetime(2024, 5, 10, 12, tzinfo=UTC))
    kept = {e.event_id for e in cut.events}
    assert 'donki_enlil#2024-05-08T05:36:00-CME-001#2024-05-09T2028Z' not in kept
    assert 'donki_enlil#2024-05-08T12:24:00-CME-001#2024-05-09T2028Z' in kept


def test_gfz_final_kp_matches_donki_and_is_review_only():
    ks, raw = SH.gfz_archive_kp_samples()
    s = next(x for x in ks if x.valid_from_utc == datetime(2024, 5, 10, 15, tzinfo=UTC))
    assert s.value == 7.667 and s.quality == 'final' and s.valid_to_utc == datetime(2024, 5, 10, 18, tzinfo=UTC) and s.t_utc == s.valid_to_utc
    assert s.published_utc is None and s.source_id == 'gfz_kp_archive'
    r = raw[s.raw_record_id]
    assert r['D'] == 2 and r['file_sha256'] and r['line'].startswith('2024 05 10') and r['fetched_utc'] is None
    # 61 сутки × 8 интервалов за май–июнь
    assert len(ks) == 61 * 8
    samples, events, _ = _bundle()
    assert samples and all(x.source_id == 'gfz_kp_archive' for x in samples)
    cut = apply_cutoff(samples, events, [], datetime(2024, 5, 3, 12, tzinfo=UTC))
    assert not cut.samples


def test_history_bundle_has_no_duplicate_ids_and_all_raw_traceable():
    samples, events, raw = _bundle()
    ids = [e.event_id for e in events]
    assert len(ids) == len(set(ids))
    assert all(e.raw_record_id in raw for e in events) and all(s.raw_record_id in raw for s in samples)
    for e in events:
        r = raw[e.raw_record_id]
        assert r.get('strict_replay_eligibility') and r.get('archive_file') and r.get('archive_sha256')
