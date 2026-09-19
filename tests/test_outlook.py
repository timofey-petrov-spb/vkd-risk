# -*- coding: utf-8 -*-
"""Перспектива на будущее: мост к 27-суточному обзору NOAA и ярусы точности.

Сеть здесь не трогается ни разу: продукт подаётся байтами через тот же транспорт-заглушку,
что и в tests/sources/test_live.py. Проверяется не «работает ли», а ровно то, чем перспектива
отличается от вердикта: время публикации берётся из выпуска, горизонт считается от выпуска,
протонной линии за тремя сутками нет, отсутствие данных не становится нулём, а суточная
вероятность не превращается в вероятность за окно.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from vkd.integration.noaa_27day import (HORIZON_DAYS, Outlook27ParseError, SOURCE_ID,
                                        fetch_27day, parse_27day)
from vkd.types import EnvironmentSample, Kind
from vkd.windows.outlook import (MAX_DAYS, TIER_27DAY, TIER_3DAY, TIER_SEASONAL, TIER_VERDICT,
                                 build_outlook, declared_tiers, outlook_table)

ROOT = Path(__file__).resolve().parents[1]
TLE = str(ROOT / 'data/orbit/iss.tle')
# Эпоха набора элементов в снимке репозитория — 2026-09-18 03:25 UTC.
START = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 9, 19, 13, 0, tzinfo=timezone.utc)
MONTHS = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()


class Response:
    def __init__(self, raw=b'', status=200, headers=None):
        self.raw, self.status_code, self.headers = raw, status, headers or {}

    def iter_content(self, size):
        for i in range(0, len(self.raw), size):
            yield self.raw[i:i + size]

    def close(self):
        pass


def bulletin(issued=datetime(2026, 9, 14, 1, 17, tzinfo=timezone.utc), first=None, days=HORIZON_DAYS,
             kp=None, step_days=1):
    """Настоящий формат продукта 27DO.txt, собранный по строкам."""
    first = first or issued.replace(hour=0, minute=0, second=0, microsecond=0)
    head = (':Product: 27-day Space Weather Outlook Table 27DO.txt\n'
            ':Issued: %04d %s %02d %02d%02d UTC\n'
            '# Prepared by the US Dept. of Commerce, NOAA, Space Weather Prediction Center\n'
            '#\n#   UTC      Radio Flux   Planetary   Largest\n#  Date       10.7 cm      A Index    Kp Index\n'
            % (issued.year, MONTHS[issued.month - 1], issued.day, issued.hour, issued.minute))
    rows = []
    for i in range(days):
        d = first + timedelta(days=i * step_days)
        value = kp[i] if kp else (5 if 10 <= i <= 12 else 2)
        rows.append('%04d %s %02d     %3d          %2d          %d'
                    % (d.year, MONTHS[d.month - 1], d.day, 100 + i, 5 + i, value))
    return (head + '\n'.join(rows) + '\n').encode()


def get_27day(tmp_path, raw=None, now=NOW, **kwargs):
    transport = kwargs.pop('transport', Mock(return_value=Response(raw if raw is not None else bulletin())))
    return fetch_27day(cache_dir=tmp_path, now=now, transport=transport, **kwargs)


def three_day_samples(first_day=datetime(2026, 9, 19, tzinfo=timezone.utc), proton_pct=1.0, kp=4.0):
    """Ячейки живого трёхсуточного прогноза NOAA в их исходном разрешении."""
    published = first_day + timedelta(hours=1)
    out = []
    for day in range(3):
        start = first_day + timedelta(days=day)
        for bin_index in range(8):
            lo = start + timedelta(hours=3 * bin_index)
            out.append(EnvironmentSample(lo, 'kp_forecast', kp if bin_index == 2 else 2.0, '1',
                                         'noaa_swpc_3day_forecast', Kind.EXTERNAL_FORECAST, published,
                                         lo, lo + timedelta(hours=3), published, 'model', 'rid', 'v'))
        out.append(EnvironmentSample(start, 's1_prob_daily', proton_pct, '%', 'noaa_swpc_3day_forecast',
                                     Kind.EXTERNAL_FORECAST, published, start, start + timedelta(days=1),
                                     published, 'model', 'rid', 'v'))
    return tuple(out)


# ---------------------------------------------------------------------------
# Мост к источнику: что именно отдаёт продукт
# ---------------------------------------------------------------------------

def test_publication_time_comes_from_issued_header_not_from_download():
    """Выпуск недельный: если публикацией считать время скачивания, горизонт уедет на неделю."""
    raw = bulletin(issued=datetime(2026, 9, 14, 1, 17, tzinfo=timezone.utc))
    parsed = parse_27day(raw, NOW)
    assert parsed['published_utc'] == datetime(2026, 9, 14, 1, 17, tzinfo=timezone.utc)
    assert parsed['data_utc'] == parsed['published_utc'] != NOW
    # Горизонт отсчитывается от первой строки выпуска, а не от «сегодня + 27».
    assert parsed['valid_from_utc'] == datetime(2026, 9, 14, tzinfo=timezone.utc)
    assert parsed['valid_to_utc'] == datetime(2026, 9, 14, tzinfo=timezone.utc) + timedelta(days=HORIZON_DAYS)


def test_product_carries_three_daily_channels_and_no_proton_forecast(tmp_path):
    outlook, raw = get_27day(tmp_path)
    assert outlook.status == 'full' and len(outlook.days) == HORIZON_DAYS
    assert outlook.proton_forecast_available is False
    channels = {s.channel_id for s in outlook.samples}
    assert channels == {'kp_max_daily', 'a_index_daily', 'f107_daily'}
    assert not any('proton' in c or 'prob' in c for c in channels)
    assert all(s.kind == Kind.EXTERNAL_FORECAST and s.source_id == SOURCE_ID for s in outlook.samples)
    # Ячейки хранят ИСХОДНЫЕ суточные границы; никакого пересчёта в часы или в окно.
    assert all(s.valid_to_utc - s.valid_from_utc == timedelta(days=1) for s in outlook.samples)
    assert raw and outlook.raw_record_id in raw


def test_horizon_ends_with_the_release_not_with_today(tmp_path):
    """Выпуск от 14.09, запрос 19.09: покрытие кончается 11.10, а не «через 27 суток»."""
    outlook, _ = get_27day(tmp_path)
    assert outlook.coverage_to_utc == datetime(2026, 10, 11, tzinfo=timezone.utc)
    assert outlook.day(datetime(2026, 10, 10, 23, tzinfo=timezone.utc)) is not None
    assert outlook.day(datetime(2026, 10, 11, tzinfo=timezone.utc)) is None
    assert outlook.horizon_days == HORIZON_DAYS


@pytest.mark.parametrize('broken, message', [
    (bulletin().replace(b':Issued:', b':issued:', 1), 'Issued'),
    (bulletin() + b':Issued: 2026 Sep 14 0117 UTC\n', 'Issued'),
    (bulletin(days=HORIZON_DAYS - 1), 'строк'),
    (bulletin(step_days=2), 'подряд'),
    (bulletin(kp=[10] * HORIZON_DAYS), 'Kp'),
    (bulletin(first=datetime(2026, 10, 1, tzinfo=timezone.utc)), 'далеко'),
])
def test_malformed_product_is_rejected_loudly(broken, message):
    with pytest.raises(Outlook27ParseError) as exc:
        parse_27day(broken, NOW)
    assert message in str(exc.value)


def test_future_release_is_rejected():
    raw = bulletin(issued=datetime(2026, 9, 20, 1, 17, tzinfo=timezone.utc))
    with pytest.raises(Outlook27ParseError):
        parse_27day(raw, NOW)


def test_unavailable_source_gives_no_days_and_no_zeros(tmp_path):
    outlook, raw = get_27day(tmp_path, transport=Mock(return_value=Response(b'', status=503)))
    assert outlook.status in ('unavailable', 'missing')
    assert outlook.days == () and outlook.samples == () and raw == {}
    assert outlook.published_utc is None and outlook.day(NOW) is None
    assert outlook.proton_forecast_available is False


def test_release_older_than_declared_age_is_not_applied(tmp_path):
    """Выпуск старше предела давности объявляется устаревшим, а не применяется молча."""
    outlook, _ = get_27day(tmp_path, now=NOW + timedelta(days=30))
    assert outlook.status == 'stale' and outlook.days == ()
    assert outlook.published_utc == datetime(2026, 9, 14, 1, 17, tzinfo=timezone.utc)


def test_cached_receipt_keeps_the_release_publication_time(tmp_path):
    first, _ = get_27day(tmp_path)
    again, _ = fetch_27day(disabled=True, cache_dir=tmp_path, now=NOW + timedelta(hours=2))
    assert again.status == 'full'
    assert again.published_utc == first.published_utc
    assert again.sha256 == first.sha256 and again.days == first.days


# ---------------------------------------------------------------------------
# Ярусы: границы видны в данных
# ---------------------------------------------------------------------------

def test_four_tiers_are_declared_with_their_boundaries_and_reasons():
    tiers = declared_tiers(3, 22)
    assert [t.tier_id for t in tiers] == [TIER_VERDICT, TIER_3DAY, TIER_27DAY, TIER_SEASONAL]
    assert [(t.from_day, t.to_day) for t in tiers] == [(0, 1), (1, 3), (3, 22), (22, None)]
    assert [t.geometry_resolution for t in tiers] == ['minute', 'minute', 'daily', 'daily']
    assert [t.weather_resolution for t in tiers] == ['3h', '3h', 'daily_max', 'none']
    assert [t.proton_forecast for t in tiers] == [True, True, False, False]
    assert [t.is_verdict for t in tiers] == [True, False, False, False]
    assert all(t.boundary_reason_ru for t in tiers)
    # Дальше обзора космопогода объявлена неизвестной, а не спокойной.
    assert any('НЕИЗВЕСТНА' in s or 'неизвестна' in s for s in tiers[-1].unknown_ru)


def test_tier_of_seasonal_only_appears_when_the_outlook_has_no_release():
    tiers = declared_tiers(3, None)
    assert [t.tier_id for t in tiers] == [TIER_VERDICT, TIER_3DAY, TIER_SEASONAL]
    assert tiers[-1].from_day == 3


# ---------------------------------------------------------------------------
# Перспектива целиком
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def result(tmp_path_factory):
    outlook27, _ = get_27day(tmp_path_factory.mktemp('cache'))
    return build_outlook(START, 27, three_day_samples=three_day_samples(), outlook27=outlook27,
                         tle_path=TLE, include_meteoroids=False, now_utc=NOW)


def test_rows_cover_the_requested_days_and_carry_their_tier(result):
    assert len(result.rows) == 27
    assert result.first_day_utc == datetime(2026, 9, 19, tzinfo=timezone.utc)
    assert [r.day_index for r in result.rows] == list(range(27))
    assert result.rows[0].tier_id == TIER_VERDICT
    assert result.rows[1].tier_id == TIER_3DAY and result.rows[2].tier_id == TIER_3DAY
    assert result.rows[3].tier_id == TIER_27DAY and result.rows[21].tier_id == TIER_27DAY
    # Обзор кончается 11.10 — это 22-е сутки от 19.09; дальше только метеороиды.
    assert result.boundaries['outlook27_last_day'] == 22
    assert all(r.tier_id == TIER_SEASONAL for r in result.rows[22:])


def test_beyond_the_release_weather_is_unknown_not_quiet(result):
    far = result.rows[-1]
    assert far.kp_max is None and far.kp_resolution is None
    assert far.a_index is None and far.f107_sfu is None
    assert far.proton_known is False and far.proton_prob_daily_pct is None
    assert 'НЕИЗВЕСТНА' in far.weather_status_ru
    assert any('неизвестна вовсе' in s for s in far.unknown_ru)


def test_proton_line_exists_only_where_noaa_publishes_it(result):
    """Трое суток — суточная вероятность; дальше её нет, и это сказано словами."""
    assert [r.proton_known for r in result.rows[:3]] == [True, True, True]
    assert not any(r.proton_known for r in result.rows[3:])
    for row in result.rows[3:]:
        assert any('НЕИЗВЕСТНА' in s for s in row.unknown_ru)
    assert result.weather['proton_forecast_last_day'] == 2


def test_daily_probability_is_carried_over_untouched(result):
    """Суточная вероятность не пересчитывается в вероятность за окно ни делением, ни степенью."""
    source = [s for s in three_day_samples() if s.channel_id == 's1_prob_daily']
    assert result.rows[0].proton_prob_daily_pct == source[0].value
    assert all(r.proton_prob_daily_pct == 1.0 for r in result.rows[:3])


def test_kp_resolution_is_declared_and_changes_at_the_boundary(result):
    assert [r.kp_resolution for r in result.rows[:3]] == ['3h', '3h', '3h']
    assert result.rows[0].kp_max == 4.0 and len(result.rows[0].kp_cells_3h) == 8
    assert all(r.kp_resolution == 'daily_max' for r in result.rows[3:22])
    # Суточный наибольший Kp не раскладывается по часам: 3-часовых ячеек на этом ярусе нет.
    assert all(r.kp_cells_3h == () for r in result.rows[3:])
    # Возмущённые сутки выпуска (строки 10–12 бюллетеня от 14.09) — это 5–7-е сутки от 19.09.
    assert [r.kp_max for r in result.rows[5:8]] == [5.0, 5.0, 5.0]
    assert result.rows[6].a_index == 16.0 and result.rows[6].f107_sfu == 111.0


def test_geometry_switches_from_minutes_to_daily_at_the_declared_age_limit(result):
    """Поминутное разрешение живёт ровно столько, сколько объявлен применимым набор элементов."""
    minute = [r for r in result.rows if r.geometry_resolution == 'minute']
    assert minute and all(r.day_index < 3 for r in minute)
    assert all(not r.beyond_declared_tle_age for r in minute)
    assert result.boundaries['minute_geometry_last_day_effective'] == len(minute)
    beyond = [r for r in result.rows if r.geometry_resolution == 'daily']
    assert all(r.beyond_declared_tle_age for r in beyond)
    assert all(any('не обещаются' in s for s in r.unknown_ru) for r in beyond)
    # Возраст набора элементов растёт ровно на сутки за сутки — это и есть причина перехода.
    ages = [r.element_set_age_days for r in result.rows]
    assert all(abs(b - a - 1.0) < 1e-6 for a, b in zip(ages, ages[1:]))


def test_daily_geometry_is_a_day_total_and_an_hour_profile(result):
    for row in result.rows:
        assert row.saa_min is not None and 100 < row.saa_min < 400
        assert len(row.saa_by_hour) == 24 and sum(row.saa_by_hour) == row.saa_min
        assert row.quiet_hours_utc and row.busy_hours_utc
        assert row.quiet_hours_utc[2] <= row.busy_hours_utc[2]
        assert abs(row.saa_share_pct - 100 * row.saa_min / 1440) < 1e-9
    # Картина медленно смещается прецессией: часы тишины первых и последних суток различаются.
    assert result.rows[0].quiet_hours_utc[0] != result.rows[-1].quiet_hours_utc[0]


def test_outlook_never_claims_to_be_the_verdict(result):
    assert 'не вердикт' in result.limitations[0]
    assert 'вероятность за окно' in result.limitations[1]
    assert not any(t.is_verdict for t in result.tiers[1:])
    row = outlook_table(result)[0]
    assert set(row) & {'recommended', 'rank', 'verdict', 'best'} == set()


def test_meteoroid_line_is_deterministic_by_date_and_shows_seasonality(tmp_path):
    """Метеороиды считаются точно на любую дату: от даты РАСЧЁТА число не зависит."""
    early = build_outlook(START, 2, tle_path=TLE, now_utc=NOW)
    later = build_outlook(START, 2, tle_path=TLE, now_utc=NOW + timedelta(days=4))
    assert [r.mmod_hits for r in early.rows] == [r.mmod_hits for r in later.rows]
    assert all(r.mmod_hits and r.mmod_hits > 0 for r in early.rows)
    assert all(r.mmod_streams is not None and r.mmod_excess_pct is not None for r in early.rows)
    # Вклад потоков — часть полного числа, а не отдельная величина рядом.
    for row in early.rows:
        assert row.mmod_streams < row.mmod_hits
        assert row.mmod_top_streams and row.mmod_top_streams[0][1] > 0


def test_meteoroid_line_distinguishes_an_active_date_from_a_quiet_one(tmp_path):
    """Геминиды (середина декабря) против спокойной даты: сезонность видна числом."""
    quiet = build_outlook(datetime(2026, 10, 12, tzinfo=timezone.utc), 1, tle_path=TLE, now_utc=NOW)
    active = build_outlook(datetime(2026, 12, 14, tzinfo=timezone.utc), 1, tle_path=TLE, now_utc=NOW)
    assert active.rows[0].mmod_streams > 5 * quiet.rows[0].mmod_streams
    assert active.rows[0].mmod_excess_pct > quiet.rows[0].mmod_excess_pct


def test_geometry_beyond_the_measured_range_says_so(result):
    """Замер накопления ошибки кончается на 30 сутках от эпохи — дальше это объявлено, а не скрыто."""
    assert all(r.geometry_measured for r in result.rows)
    far = build_outlook(datetime(2026, 12, 14, tzinfo=timezone.utc), 1, tle_path=TLE,
                        include_meteoroids=False, now_utc=NOW)
    row = far.rows[0]
    assert row.element_set_age_days > 30 and row.geometry_measured is False
    assert row.saa_min is not None                       # величина считается, но помечена
    assert any('за пределом замера' in s for s in row.unknown_ru)
    assert any('geometry_measured' in s for s in far.limitations)


def test_missing_sources_do_not_become_zeros(tmp_path):
    result = build_outlook(START, 4, tle_path=TLE, include_meteoroids=False, now_utc=NOW)
    assert all(r.kp_max is None and r.proton_prob_daily_pct is None for r in result.rows)
    assert all(r.mmod_hits is None for r in result.rows)
    assert all(r.tier_id in (TIER_VERDICT, TIER_SEASONAL) for r in result.rows)
    assert any('27-суточный обзор NOAA не передан' in s for s in result.limitations)


def test_requested_days_are_validated():
    with pytest.raises(ValueError):
        build_outlook(START, 0, tle_path=TLE, include_meteoroids=False, now_utc=NOW)
    with pytest.raises(ValueError):
        build_outlook(START, MAX_DAYS + 1, tle_path=TLE, include_meteoroids=False, now_utc=NOW)
    with pytest.raises(ValueError):
        build_outlook(datetime(2026, 9, 19, 12), 2, tle_path=TLE, include_meteoroids=False, now_utc=NOW)


def test_seasonal_domain_is_refused_outside_2000_2050():
    with pytest.raises(ValueError) as exc:
        build_outlook(datetime(2051, 1, 1, tzinfo=timezone.utc), 2, tle_path=TLE,
                      include_meteoroids=False, now_utc=NOW)
    assert '2000' in str(exc.value)
