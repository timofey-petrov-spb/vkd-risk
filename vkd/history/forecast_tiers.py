"""Explicit NGDC → archived-original fallback. No local-k/Ap substitution for Kp."""

from __future__ import annotations

from vkd.sources.noaa import SOURCE_3DAY, SOURCE_3DAY_WAYBACK, SOURCE_DAYPRE
from vkd.sources.registry import utc
from .replay import replay_forecast


def replay_forecast_tiers(
    registry, *, source_id, channel_id, cutoff_utc, valid_from_utc, valid_to_utc
):
    """Same arguments/result as replay_forecast, plus tier/attempt diagnostics.

    Prefer an available NGDC bulletin fully covering the request. Otherwise
    choose the eligible source with the greatest coverage (tie: latest Issued).
    A single issue is used: forecasts from several releases are not stitched.
    No daily probability conversion, no data fetched, no future captures admitted.
    Consumers MUST take the actual source_id from the returned record, rather
    than retaining the requested logical source_id in their exported metadata.
    """
    sources = [source_id]
    if source_id == SOURCE_3DAY and SOURCE_3DAY_WAYBACK in registry.source_ids:
        sources.append(SOURCE_3DAY_WAYBACK)
    attempts = [
        replay_forecast(
            registry,
            source_id=source,
            channel_id=channel_id,
            cutoff_utc=cutoff_utc,
            valid_from_utc=valid_from_utc,
            valid_to_utc=valid_to_utc,
        )
        for source in sources
    ]
    primary = attempts[0]
    if primary["status"] == "full":
        chosen = primary
    else:
        usable = [r for r in attempts if r["status"] in ("full", "partial")]
        chosen = (
            max(
                usable,
                key=lambda r: (
                    r["coverage_fraction"],
                    utc(r["record"]["published_utc"]),
                ),
            )
            if usable
            else primary
        )
    selected = dict(chosen)
    tiers = {
        SOURCE_3DAY: "ngdc_3day",
        SOURCE_3DAY_WAYBACK: "wayback_3day",
        SOURCE_DAYPRE: "ngdc_daypre",
    }
    selected["tier"] = tiers.get(selected["source_id"], selected["source_id"])
    selected["tier_attempts"] = [
        {k: r[k] for k in ("source_id", "status", "coverage_fraction", "reason")}
        for r in attempts
    ]
    return selected
