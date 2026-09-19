"""Seasonal mechanism must reach the shared pipeline, verdict, export and replay."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import io
import json
import zipfile

import pytest

from app.compute import run
from app.export import build_zip, report_md
from app.ui import mmod_scale_ru
from scripts.replay_example import load_snapshot
from vkd.assess.seasonal import CATALOGUE_ID, METHOD_ID
from vkd.assess.trapped import BeltTable
from vkd.integration.replay_live import fetch_none
from vkd.windows.compare import recommend
from vkd.windows.scenario import Scenario

UTC = timezone.utc


@pytest.fixture(scope="module")
def actual():
    return run(
        "history_forecast", datetime(2024, 6, 7, 12, tzinfo=UTC), 360, 720, [0, 240], fetched=fetch_none()
    )


def test_window_model_reaches_factors_and_manifest(actual):
    for window in actual.assessments:
        result = actual.S["meteoroids"][window.window.start_utc.isoformat()]
        mechanism = next(m for m in window.mechanisms if m.mechanism_id == "mmod_stat")
        assert mechanism.coverage.value == "partial"
        assert mechanism.coverage_fraction == 1
        assert mechanism.factors[0].value == result["N"]
        assert CATALOGUE_ID in mechanism.factors[0].record_ids
        assert METHOD_ID in mechanism.factors[0].record_ids
        assert result["N_streams"] > 0
        assert "не рассчитан" not in " ".join(mechanism.coverage_notes)
        assert "лет" not in mmod_scale_ru(mechanism.factors[0], 360)
    for rid in (CATALOGUE_ID, METHOD_ID):
        import base64

        raw = actual.raw_records[rid]
        assert (
            hashlib.sha256(base64.b64decode(raw["content_base64"])).hexdigest() == raw["metadata"]["sha256"]
        )
    assert actual.S["effective_config"]["mmod"]["model_id"].startswith("ecss-seasonal")


def test_zip_and_replay_preserve_all_seasonal_evidence(actual, tmp_path):
    path = tmp_path / "seasonal.zip"
    path.write_bytes(build_zip(actual.S, actual.raw_records))
    snapshot = load_snapshot(str(path))
    assert snapshot["meteoroids"] == json.loads(json.dumps(actual.S["meteoroids"]))
    with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as z:
        assert "meteoroids.json" in z.namelist()
        text = z.read("report.md").decode()
        assert "Сезонная оценка метеороидов" in text and "не доверительный интервал" in text
    replay = run(
        "history_forecast",
        datetime(2024, 6, 7, 12, tzinfo=UTC),
        360,
        720,
        [0, 240],
        fetched=fetch_none(),
        now=datetime.fromisoformat(actual.S["computed_utc"]),
    )
    assert replay.S["meteoroids"] == actual.S["meteoroids"]
    assert replay.S["recommendation"] == actual.S["recommendation"]


@pytest.mark.parametrize("mode", ["history_forecast", "history_review"])
def test_history_and_simulated_delay_keep_orbit_and_meteor_times_together(mode):
    result = run(
        mode,
        datetime(2024, 5, 6, 12, tzinfo=UTC),
        360,
        720,
        [0, 240],
        scenario=Scenario("test", work_delay_min=90) if mode == "history_review" else None,
        fetched=fetch_none(),
    )
    assert len(result.S["meteoroids"]) == 2
    for window in result.S["windows"]:
        m = result.S["meteoroids"][window["start_utc"]]
        assert m["streams_included"] and m["N"] > 0
        assert m["provenance"]["inertial_states_sha256"]


def test_model_failure_is_missing_not_zero_or_baseline_fallback(monkeypatch):
    def reject(*args, **kwargs):
        raise ValueError("test invalid orbital states")

    monkeypatch.setattr("app.compute.seasonal_hits_track", reject)
    result = run(
        "history_forecast", datetime(2024, 6, 7, 12, tzinfo=UTC), 360, 720, [0, 240], fetched=fetch_none()
    )
    assert result.rec.verdict == "insufficient"
    assert any("test invalid orbital states" in x for x in result.rec.missing)
    for m in result.S["meteoroids"].values():
        assert m["N"] is None and not m["streams_included"]


def synthetic_comparison(numbers, alternative):
    from tests.test_compare import two_windows, complete_for_comparator, goes

    assessments, thresholds = two_windows(BeltTable("min"), goes(0.2), pattern=lambda i: False)
    assessments = complete_for_comparator(assessments)
    updated, models = [], {}
    for a, number, other in zip(assessments, numbers, alternative):
        updated.append(
            replace(
                a,
                mechanisms=tuple(
                    (
                        replace(m, factors=(replace(m.factors[0], value=number),) + m.factors[1:])
                        if m.mechanism_id == "mmod_stat"
                        else m
                    )
                    for m in a.mechanisms
                ),
            )
        )
        models[a.window.start_utc.isoformat()] = {
            "sensitivity": {"hypotheses_N": {"base": number, "alternative": other}, "invalid_hypotheses": {}}
        }
    return recommend(updated, thresholds, mmod_sensitivity=models)


def test_seasonal_line_can_choose_between_radiation_equivalent_windows():
    r = synthetic_comparison([1e-6, 2e-6], [2e-6, 4e-6])
    assert r.verdict == "preferred"
    assert r.preferred.start_utc.hour == 12
    assert "метеороидов" in r.rule_applied


def test_hypothesis_rank_reversal_prevents_false_winner():
    r = synthetic_comparison([1e-6, 2e-6], [4e-6, 2e-6])
    assert r.verdict == "trade_off" and r.preferred is None
    assert "гипотез" in r.rule_applied


def test_current_mode_uses_same_seasonal_model_with_pinned_sgp4():
    from tests.test_integration import _fetched, TLE
    from vkd.orbit.trajectory import satellite_from_tle
    from datetime import timedelta
    from pathlib import Path

    tle = Path(TLE).read_text()
    t0 = (satellite_from_tle(tle.encode()).epoch.utc_datetime() + timedelta(days=1)).replace(
        second=0, microsecond=0
    )
    result = run("live", t0, 360, 720, [0, 240], fetched=_fetched(tle, goes_at=t0), now=t0)
    assert result.meta.method == "sgp4"
    assert all(m["streams_included"] for m in result.S["meteoroids"].values())
    assert all(not m["provenance"]["is_reconstruction"] for m in result.S["meteoroids"].values())
