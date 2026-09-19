"""Numerical, provenance and integration acceptance for the own GP screen."""

import csv
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import json

import numpy as np
import pytest
from scipy.optimize import minimize_scalar
from sgp4.api import Satrec, jday
from sgp4.exporter import export_omm

from vkd.conjunctions.screening import (
    ScreeningConfig,
    _refine,
    assess_window,
    chord_candidates,
    screen_catalogue,
)
from vkd.sources.catalogue import Catalogue, GPObject, parse_catalogue
from vkd.types import Coverage, Kind, Presence, Window

START = datetime(2026, 9, 19, 6, tzinfo=timezone.utc)
TLE1 = "1 25544U 98067A   26261.14280998  .00005718  00000+0  11125-3 0  9991"
TLE2 = "2 25544  51.6307 200.0361 0004822 152.4527 207.6718 15.49160218586162"


def rows():
    row = export_omm(Satrec.twoline2rv(TLE1, TLE2), "ISS")
    second = {
        **row,
        "NORAD_CAT_ID": 100057,
        "OBJECT_NAME": "test γ object",
        "RA_OF_ASC_NODE": float(row["RA_OF_ASC_NODE"]) + 0.02,
    }
    # A generated orbit, explicitly not an independent measured encounter.
    return [row, second]


def raw_rows(entries):
    handle = io.StringIO()
    writer = csv.DictWriter(handle, fieldnames=entries[0].keys())
    writer.writeheader()
    writer.writerows(entries)
    return handle.getvalue().encode("utf-8")


def catalogue(entries=None, available=START):
    return parse_catalogue(
        raw_rows(rows() if entries is None else entries),
        available_utc=available,
        url="fixture:generated",
        scope="generated test objects",
    )


def test_csv_large_id_unicode_and_exact_hash():
    import hashlib

    c = catalogue()
    assert c.objects[1].norad_id == 100057
    assert c.objects[1].satrec.satnum == 100057
    assert "γ" in c.objects[1].name
    assert c.metadata["sha256"] == hashlib.sha256(raw_rows(rows())).hexdigest()
    assert c.metadata["published_utc"] is None
    assert c.metadata["complete_public_catalogue"] is False


@pytest.mark.parametrize(
    "key,value",
    [
        ("REF_FRAME", "GCRF"),
        ("TIME_SYSTEM", "TAI"),
        ("MEAN_ELEMENT_THEORY", "SGP4-XP"),
        ("BSTAR", "nan"),
        ("MEAN_MOTION", "0"),
        ("ECCENTRICITY", "1.1"),
        ("INCLINATION", "-1"),
        ("NORAD_CAT_ID", "0"),
    ],
)
def test_bad_rows_reported(key, value):
    data = rows()
    data[1][key] = value
    c = catalogue(data)
    assert len(c.objects) == 1 and len(c.rejected_rows) == 1 and c.input_rows == 2


def test_duplicate_id_chooses_latest_epoch():
    data = rows()
    c = catalogue(data + [dict(data[1], EPOCH="2026-09-19T00:00:00.000000")])
    assert len(c.objects) == 2 and c.duplicate_rows == 1
    assert c.objects[1].epoch_utc.day == 19


@pytest.mark.parametrize(
    "payload", [b"", b"<html>403</html>", b"OBJECT_NAME,EPOCH\nISS,no\n"]
)
def test_empty_or_error_document_fails(payload):
    with pytest.raises(ValueError):
        parse_catalogue(payload, available_utc=START, url="x", scope="x")


def test_swept_filter_keeps_fast_crossing_between_nodes():
    # At both minute nodes the distance is 450km; at t=30s it is only 0.2km.
    r = np.array([[-450.0, 0.2, 0], [450.0, 0.2, 0]])
    assert min(np.linalg.norm(r, axis=1)) > 400
    assert chord_candidates(r, np.array([0.0, 60.0]), 5.0, 0.04)[0]


def test_curved_path_uses_acceleration_padding():
    # Endpoints at y=15km, parabola dips to y=1.5km between them (a=.03).
    r = np.array([[-100.0, 15.0, 0], [100.0, 15.0, 0]])
    assert not chord_candidates(r, np.array([0.0, 60.0]), 5.0, 0)[0]
    assert chord_candidates(r, np.array([0.0, 60.0]), 5.0, 0.04)[0]


def test_tca_matches_independent_dense_distance_minimization():
    c = catalogue()
    end = START + timedelta(hours=2)
    result = screen_catalogue(c, START, end)
    assert len(result.encounters) >= 2
    jd, frac = jday(2026, 9, 19, 6, 0, 0)
    a, b = c.objects

    def distance(t):
        ra = a.satrec.sgp4(jd, frac + t / 86400)[1]
        rb = b.satrec.sgp4(jd, frac + t / 86400)[1]
        return float(np.linalg.norm(np.array(rb) - ra))

    # Independent reference does not call production chord filter or r·v root.
    mesh = np.arange(0.0, 7200.01, 0.5)
    ds = np.array([distance(t) for t in mesh])
    indices = np.flatnonzero((ds[1:-1] < ds[:-2]) & (ds[1:-1] < ds[2:])) + 1
    expected = [
        minimize_scalar(
            distance,
            bounds=(mesh[i - 1], mesh[i + 1]),
            method="bounded",
            options={"xatol": 1e-6},
        )
        for i in indices
    ]
    actual = [e for e in result.encounters if not e.at_horizon_boundary]
    assert len(expected) == len(actual)
    for ref, event in zip(expected, actual):
        assert abs((event.conjunction.tca_utc - START).total_seconds() - ref.x) < 0.005
        assert abs(event.conjunction.miss_distance_km - ref.fun) < 1e-6
        assert event.conjunction.max_probability is None
        assert event.conjunction.published_utc is None


def test_identical_station_elements_are_not_independent_encounters():
    data = rows()
    data[1] = dict(data[0], NORAD_CAT_ID=100057, OBJECT_NAME="catalogue alias")
    result = screen_catalogue(catalogue(data), START, START + timedelta(hours=1))
    assert not result.encounters
    assert result.stats["shared_primary_elements"][0]["norad_id"] == 100057
    assert (
        "docking_status_not_inferred"
        in result.stats["shared_primary_elements"][0]["reason"]
    )


def test_future_receipt_rejected_for_history_even_if_epoch_is_old():
    c = catalogue(available=START + timedelta(minutes=1))
    result = screen_catalogue(c, START, START + timedelta(hours=1), decision_utc=START)
    assert result.status == "unavailable" and not result.encounters
    assert (
        assess_window(result, Window(START, 60)).factors[0].presence == Presence.UNKNOWN
    )


def test_modern_catalogue_never_substitutes_2024():
    t = datetime(2024, 5, 10, tzinfo=timezone.utc)
    result = screen_catalogue(catalogue(), t, t + timedelta(hours=6), decision_utc=t)
    assert result.status == "unavailable"


def test_expired_elements_at_horizon_end_rejected():
    result = screen_catalogue(catalogue(), START, START + timedelta(hours=72))
    assert result.status == "unavailable"  # primary >3 days old at final time


@pytest.mark.parametrize(
    "config",
    [
        {"coarse_step_s": 61},
        {"refine_step_s": 10},
        {"check_distance_km": 51},
        {"radial_margin_km": float("nan")},
        {"max_horizon_hours": 100},
        {"batch_size": 1.5},
        {"coarse_step_s": True},
    ],
)
def test_config_domain(config):
    with pytest.raises(ValueError):
        ScreeningConfig(**config)


def test_empty_catalogue_is_missing_not_zero():
    result = screen_catalogue((), START, START + timedelta(hours=1))
    assessment = assess_window(result, Window(START, 60))
    assert assessment.coverage == Coverage.NONE
    assert assessment.factors[0].value is None


def test_screened_empty_selection_is_not_missing():
    data = rows()
    data[1]["MEAN_MOTION"] = 1.0027
    result = screen_catalogue(catalogue(data), START, START + timedelta(hours=1))
    assessment = assess_window(result, Window(START, 60))
    assert assessment.coverage == Coverage.PARTIAL
    assert assessment.factors[0].value == 0
    assert assessment.factors[0].presence == Presence.NOT_DETECTED


def test_threshold_and_half_open_window_and_serialization():
    result = screen_catalogue(catalogue(), START, START + timedelta(hours=2))
    event = next(e for e in result.encounters if not e.at_horizon_boundary)
    result = replace(result, encounters=(event,))
    t = event.conjunction.tca_utc
    within = assess_window(result, Window(t, 1))
    before = assess_window(result, Window(t - timedelta(minutes=1), 1))
    assert within.needs_check and not before.needs_check
    assert within.factors[0].kind == Kind.OWN_CALCULATION
    assert within.coverage != Coverage.FULL
    high = replace(event, conjunction=replace(event.conjunction, miss_distance_km=5.0))
    assert not assess_window(
        replace(result, encounters=(high,)), Window(t, 1)
    ).needs_check
    json.dumps(result.to_dict(), allow_nan=False)


def test_step_and_radial_filter_do_not_change_controlled_minima():
    c = catalogue()
    first = screen_catalogue(c, START, START + timedelta(hours=2))
    second = screen_catalogue(
        c,
        START,
        START + timedelta(hours=2),
        config=ScreeningConfig(coarse_step_s=10, use_radial_filter=False),
    )
    assert len(first.encounters) == len(second.encounters)
    for a, b in zip(first.encounters, second.encounters):
        assert (
            abs((a.conjunction.tca_utc - b.conjunction.tca_utc).total_seconds()) < 0.005
        )
        assert (
            abs(a.conjunction.miss_distance_km - b.conjunction.miss_distance_km) < 1e-6
        )


def test_sgp4_failure_is_reported():
    data = rows()
    data[1]["BSTAR"] = 0.5  # valid at initialization; decays before test horizon
    result = screen_catalogue(catalogue(data), START, START + timedelta(hours=1))
    assert result.stats["excluded"]
    assert result.provenance["selection_calculation_complete"] is False


def test_live_cache_source_off_corruption_and_failure(tmp_path):
    from unittest.mock import Mock
    from tests.sources.test_live import Response
    from vkd.sources.catalogue import catalogue_latest

    payload = raw_rows(rows())
    transport = Mock(return_value=Response(payload))
    c, receipt = catalogue_latest(cache_dir=tmp_path, now=START, transport=transport)
    assert c is not None and receipt.ok and receipt.raw == payload
    assert receipt.metadata["sha256"] == c.metadata["sha256"]
    # Two-hour throttle survives force refresh and source disable does no I/O.
    cached, r = catalogue_latest(
        cache_dir=tmp_path,
        now=START + timedelta(minutes=1),
        transport=transport,
        force_refresh=True,
    )
    assert cached is not None and r.from_cache and transport.call_count == 1
    off, r = catalogue_latest(
        cache_dir=tmp_path, now=START, disabled="off", transport=transport
    )
    assert off is None and transport.call_count == 1
    transport.return_value = Response(status=403)
    fallback, r = catalogue_latest(
        cache_dir=tmp_path, now=START + timedelta(hours=3), transport=transport
    )
    assert fallback is not None and r.error == "http_403" and transport.call_count == 2
    from pathlib import Path

    Path(r.raw_path).write_bytes(b"corrupt")
    damaged, r = catalogue_latest(
        cache_dir=tmp_path, now=START + timedelta(hours=3), disabled="cache"
    )
    assert damaged is None


def test_primary_receipt_reuses_exact_orbit_bytes(tmp_path):
    from unittest.mock import Mock
    from tests.sources.test_live import Response
    from vkd.sources.catalogue import primary_catalogue_from_fetch
    from vkd.sources.live import tle_latest

    raw = ("ISS\n" + TLE1 + "\n" + TLE2 + "\n").encode("ascii")
    _, receipt = tle_latest(
        cache_dir=tmp_path,
        now=START,
        transport=Mock(return_value=Response(raw)),
        use_bundled=False,
    )
    primary = primary_catalogue_from_fetch(receipt)
    assert primary.objects[0].raw_record_id == receipt.metadata["raw_record_id"]
    result = screen_catalogue(
        (catalogue(), primary),
        START,
        START + timedelta(hours=1),
        primary=primary.objects[0],
    )
    assert (
        result.provenance["primary"]["raw_record_id"]
        == receipt.metadata["raw_record_id"]
    )


def test_snapshot_export_preserves_bytes_and_detects_corruption(tmp_path):
    import gzip
    import hashlib
    import base64
    from vkd.conjunctions.snapshot import load_snapshot, source_records

    raw = raw_rows(rows())
    record = {
        "file": "gp.csv.gz",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "available_utc": START.isoformat(),
        "url": "fixture:csv",
        "scope": "test",
        "source_id": "test",
        "availability_proof": "test receipt",
    }
    (tmp_path / record["file"]).write_bytes(gzip.compress(raw, mtime=0))
    (tmp_path / "manifest.json").write_text(
        json.dumps({"records": [record]}), encoding="utf-8"
    )
    cats, _ = load_snapshot(tmp_path)
    output = source_records(cats)
    assert base64.b64decode(next(iter(output.values()))["content_base64"]) == raw
    (tmp_path / record["file"]).write_bytes(gzip.compress(raw + b" ", mtime=0))
    with pytest.raises(ValueError, match="changed"):
        source_records(cats)
    with pytest.raises(ValueError, match="digest"):
        load_snapshot(tmp_path)


def test_result_metadata_does_not_alias_inputs():
    c = catalogue()
    result = screen_catalogue(c, START, START + timedelta(hours=1))
    c.metadata["scope"] = "changed afterwards"
    assert result.provenance["catalogues"][0]["scope"] != "changed afterwards"


def test_station_alias_detected_when_app_uses_older_primary():
    old_primary = catalogue([rows()[0]])
    new_station = dict(rows()[0], EPOCH="2026-09-19T04:00:00.000000")
    new_alias = dict(
        new_station, NORAD_CAT_ID=100057, OBJECT_NAME="shared new station GP"
    )
    newer = catalogue([new_station, new_alias])
    result = screen_catalogue(
        (old_primary, newer),
        START,
        START + timedelta(hours=1),
        primary=old_primary.objects[0],
    )
    assert not result.encounters
    shared = result.stats["shared_primary_elements"][0]
    assert shared["matched_primary_raw_record_id"] == newer.metadata["raw_record_id"]


def test_old_primary_receipt_gives_status_instead_of_crashing():
    old = catalogue([rows()[0]], available=START - timedelta(days=2))
    result = screen_catalogue(
        (old, catalogue()), START, START + timedelta(hours=1), primary=old.objects[0]
    )
    assert result.status == "unavailable"
