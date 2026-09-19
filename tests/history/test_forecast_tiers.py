from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vkd.history.forecast_tiers import replay_forecast_tiers
from vkd.history.replay import replay_forecast
from vkd.sources.noaa import (
    SOURCE_3DAY,
    SOURCE_3DAY_WAYBACK,
    SOURCE_DAYPRE,
    ForecastParseError,
    parse_forecast,
)
from vkd.sources.registry import SourceRegistry, utc

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def registry():
    return SourceRegistry(ROOT)


def call(registry, t, source=SOURCE_3DAY, channel="noaa_kp", tiered=True):
    fn = replay_forecast_tiers if tiered else replay_forecast
    return fn(
        registry,
        source_id=source,
        channel_id=channel,
        cutoff_utc=t,
        valid_from_utc=t,
        valid_to_utc=t + timedelta(hours=6),
    )


def test_all_captures_verify_and_parse_real_rationale_text(registry):
    records = registry.records(SOURCE_3DAY_WAYBACK)
    assert len(records) == 20
    for record in records:
        raw = registry.raw_bytes(record["raw_record_id"])
        cells = parse_forecast(raw, record)
        assert len(cells) == 33
        assert {c["source_id"] for c in cells} == {SOURCE_3DAY_WAYBACK}
        assert utc(record["available_utc"]) >= utc(record["published_utc"])
        assert all(c["temporal_resolution"] in ("3h", "24h") for c in cells)


def test_capture_not_earlier_issued_controls_cutoff(registry):
    record = registry.records(SOURCE_3DAY_WAYBACK)[0]
    capture = utc(record["available_utc"])
    before = call(
        registry, capture - timedelta(seconds=1), SOURCE_3DAY_WAYBACK, tiered=False
    )
    after = call(registry, capture, SOURCE_3DAY_WAYBACK, tiered=False)
    assert before["status"] == "missing" and after["status"] == "full"
    assert utc(record["published_utc"]) < capture


@pytest.mark.parametrize("mutation", ["body", "availability", "url"])
def test_cannot_backdate_or_rewrite_capture(registry, mutation):
    record = deepcopy(registry.records(SOURCE_3DAY_WAYBACK)[0])
    raw = registry.raw_bytes(record["raw_record_id"])
    if mutation == "body":
        raw += b" \n"
    elif mutation == "availability":
        record["available_utc"] = record["published_utc"]
    else:
        record["url"] = record["url"].replace(
            record["archive_timestamp"], "20200101000000"
        )
    with pytest.raises(ForecastParseError):
        parse_forecast(raw, record)


def test_fallback_fills_actual_may_gap(registry):
    t = datetime(2024, 5, 20, 12, tzinfo=timezone.utc)
    baseline = call(registry, t, tiered=False)
    extended = call(registry, t)
    assert baseline["status"] == "missing"
    assert extended["status"] == "full" and extended["tier"] == "wayback_3day"
    assert extended["record"]["source_id"] == SOURCE_3DAY_WAYBACK
    assert utc(extended["record"]["available_utc"]) <= t
    assert extended["record"]["archive_timestamp"] == "20240520004448"


def test_ngdc_keeps_priority_and_does_not_change_old_results(registry):
    t = datetime(2024, 6, 20, 12, tzinfo=timezone.utc)
    baseline, extended = call(registry, t, tiered=False), call(registry, t)
    assert extended["tier"] == "ngdc_3day"
    assert baseline["record"] == extended["record"]
    assert baseline["cells"] == extended["cells"]


def test_uncovered_june_date_stays_missing_not_zero(registry):
    t = datetime(2024, 6, 6, 12, tzinfo=timezone.utc)
    result = call(registry, t)
    assert result["status"] == "missing" and result["cells"] == []


def test_daypre_does_not_substitute_planetary_kp(registry):
    t = datetime(2024, 6, 6, 12, tzinfo=timezone.utc)
    proton = call(registry, t, SOURCE_DAYPRE, "whole_disk_proton_probability")
    kp = call(registry, t, SOURCE_DAYPRE, "noaa_kp")
    assert proton["status"] == "full" and proton["tier"] == "ngdc_daypre"
    assert kp["status"] == "missing"
    assert all(c["temporal_resolution"] == "24h" for c in proton["cells"])
