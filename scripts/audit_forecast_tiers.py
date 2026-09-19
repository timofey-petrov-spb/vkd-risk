#!/usr/bin/env python3
"""Hourly availability/coverage audit on the 33-day NOAA archive gap, offline."""

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vkd.history.forecast_tiers import replay_forecast_tiers
from vkd.history.replay import replay_forecast
from vkd.sources.registry import SourceRegistry


def main():
    registry = SourceRegistry(ROOT)
    start = datetime(2024, 5, 15, tzinfo=timezone.utc)
    transitions, rows = Counter(), []
    for hour in range(33 * 24):
        t = start + timedelta(hours=hour)
        args = dict(
            source_id="noaa_ngdc_3day_forecast",
            channel_id="noaa_kp",
            cutoff_utc=t,
            valid_from_utc=t,
            valid_to_utc=t + timedelta(hours=6),
        )
        old = replay_forecast(registry, **args)
        new = replay_forecast_tiers(registry, **args)
        transitions[old["status"] + " -> " + new["status"]] += 1
        rows.append(
            {
                "cutoff_utc": t.isoformat(),
                "baseline_status": old["status"],
                "status": new["status"],
                "tier": new["tier"],
                "coverage_fraction": new["coverage_fraction"],
                "raw_record_id": (
                    new["record"]["raw_record_id"] if new["record"] else None
                ),
                "available_utc": (
                    new["record"]["available_utc"] if new["record"] else None
                ),
            }
        )
    summary = {
        "cutoff_from_utc": start.isoformat(),
        "cutoff_to_exclusive_utc": (start + timedelta(days=33)).isoformat(),
        "horizon_hours": 6,
        "step_hours": 1,
        "cutoffs": len(rows),
        "transitions": dict(transitions),
        "baseline_full": sum(r["baseline_status"] == "full" for r in rows),
        "extended_full": sum(r["status"] == "full" for r in rows),
        "extended_partial": sum(r["status"] == "partial" for r in rows),
        "extended_missing": sum(r["status"] == "missing" for r in rows),
        "scope": "Data availability for overlapping 6h windows, NOT forecast accuracy or independent trials",
    }
    path = ROOT / "data/source_registry_2024/noaa_wayback/coverage_audit.json"
    path.write_bytes(
        (
            json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2)
            + "\n"
        ).encode("utf-8")
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
