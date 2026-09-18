"""Historical NOAA forecasts selected from exact archived releases, without I/O to the web."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from vkd.sources.noaa import ForecastParseError, parse_forecast
from vkd.sources.registry import RegistryError, SourceRegistry, iso_utc, utc


def replay_forecast(registry: SourceRegistry, *, source_id: str, channel_id: str,
                    cutoff_utc: datetime, valid_from_utc: datetime,
                    valid_to_utc: datetime) -> dict[str, Any]:
    """Select the latest eligible issued bulletin, then report actual cell coverage.

    Eligibility uses both publication and declared availability. Current policy
    admits dated official bulletins only: unreviewed DONKI and reconstruction OEM
    stay excluded. This policy does not establish independent historical archive
    capture. Original daily/3h cells are returned unchanged, never rescaled to the
    requested window. The result is an internal data report, not a risk verdict.
    """
    cutoff, start, end = map(utc, (cutoff_utc, valid_from_utc, valid_to_utc))
    if not cutoff <= start < end:
        raise ValueError("Forecast requires cutoff <= valid_from_utc < valid_to_utc")
    result: dict[str, Any] = {
        "source_id": source_id, "channel_id": channel_id, "cutoff_utc": iso_utc(cutoff),
        "valid_from_utc": iso_utc(start), "valid_to_utc": iso_utc(end),
        "status": "missing", "coverage_fraction": 0.0, "covered_seconds": 0.0,
        "gaps": [{"valid_from_utc": iso_utc(start), "valid_to_utc": iso_utc(end)}],
        "record": None, "cells": [], "reason": None,
        "eligibility_policy": "dated_official_bulletin; provider issue-time evidence",
        "limitations": ["No risk verdict. Missing coverage never means zero risk.",
                        "A current content hash does not prove historical payload immutability."],
    }
    candidates = []
    for record in registry.records(source_id):
        reason = None
        if not record["published_utc"] or not record["available_utc"]:
            reason = "unknown_publication_or_availability"
        elif utc(record["published_utc"]) > cutoff or utc(record["available_utc"]) > cutoff:
            reason = "not_available_at_cutoff"
        elif record["strict_replay_eligibility"] != "dated_official_bulletin":
            reason = "historical_content_not_admitted"
        elif not any(c["channel_id"] == channel_id and c.get("kind") == "external_forecast"
                     for c in record["channels"]):
            reason = "channel_absent"
        if reason is None:
            candidates.append(record)
    if not candidates:
        result["reason"] = "no_eligible_release"
        return result
    latest_publication = max(utc(r["published_utc"]) for r in candidates)
    latest = [r for r in candidates if utc(r["published_utc"]) == latest_publication]
    if len({r["sha256"] for r in latest}) > 1:
        result.update(status="ambiguous", reason="conflicting_versions_at_same_publication")
        return result
    record = min(latest, key=lambda r: r["raw_record_id"])
    result["record"] = record
    result["limitations"].extend(record.get("limitations", []))
    try:
        cells = parse_forecast(registry.raw_bytes(record["raw_record_id"]), record)
    except (RegistryError, ForecastParseError, OSError) as exc:
        result.update(status="invalid", reason=str(exc))
        return result
    cells = [c for c in cells if c["channel_id"] == channel_id
             and utc(c["valid_from_utc"]) < end and utc(c["valid_to_utc"]) > start]
    result["cells"] = cells
    # Cells preserve original support; clipping here measures coverage only.
    cursor = start
    gaps = []
    for cell in sorted(cells, key=lambda c: utc(c["valid_from_utc"])):
        left, right = max(start, utc(cell["valid_from_utc"])), min(end, utc(cell["valid_to_utc"]))
        if left > cursor:
            gaps.append((cursor, left))
        cursor = max(cursor, right)
    if cursor < end:
        gaps.append((cursor, end))
    missing_seconds = sum((right - left).total_seconds() for left, right in gaps)
    total_seconds = (end - start).total_seconds()
    result["covered_seconds"] = total_seconds - missing_seconds
    result["coverage_fraction"] = result["covered_seconds"] / total_seconds
    result["gaps"] = [{"valid_from_utc": iso_utc(a), "valid_to_utc": iso_utc(b)} for a, b in gaps]
    result["status"] = "full" if not gaps else ("partial" if cells else "missing")
    result["reason"] = None if not gaps else "forecast_validity_gap"
    return result
