#!/usr/bin/env python3
"""Offline encounter run or explicitly requested live acquisition; no UI edits."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vkd.conjunctions import assess_window, screen_catalogue
from vkd.conjunctions.snapshot import load_config, load_snapshot
from vkd.sources.catalogue import catalogue_latest
from vkd.sources.registry import utc
from vkd.types import Window


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot", type=Path, default=ROOT / "data/conjunctions/demo_2026-09-19"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="explicit network request with shared A4 cache",
    )
    parser.add_argument(
        "--start", help="UTC ISO; default pinned demo start, or now for --live"
    )
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--coarse-step", type=float)
    parser.add_argument("--no-radial-filter", action="store_true")
    args = parser.parse_args()
    cfg = load_config(args.config)
    if args.coarse_step is not None:
        cfg = replace(cfg, coarse_step_s=args.coarse_step)
    if args.no_radial_filter:
        cfg = replace(cfg, use_radial_filter=False)
    acquisitions = []
    if args.live:
        catalogues = []
        for selection in ("active", "debris", "rocket_bodies"):
            catalogue, receipt = catalogue_latest(selection)
            acquisitions.append(
                {
                    "selection": selection,
                    "status": receipt.status,
                    "error": receipt.error,
                    "metadata": receipt.metadata,
                }
            )
            if catalogue:
                catalogues.append(catalogue)
        start = utc(args.start) if args.start else datetime.now(timezone.utc)
    else:
        catalogues, manifest = load_snapshot(args.snapshot)
        start = utc(args.start or manifest["demo_start_utc"])
    result = screen_catalogue(
        tuple(catalogues), start, start + timedelta(hours=args.hours), config=cfg
    )
    payload = result.to_dict()
    payload["acquisitions"] = acquisitions
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": result.status,
                "stats": {
                    k: v
                    for k, v in result.stats.items()
                    if k not in ("excluded", "shared_primary_elements")
                },
                "excluded_records": len(result.stats["excluded"]),
                "shared_primary_elements": len(result.stats["shared_primary_elements"]),
                "conditions_first_6h": len(
                    assess_window(
                        result, Window(start, min(360, int(args.hours * 60)))
                    ).conditions
                ),
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 2 if result.status == "unavailable" else 0


if __name__ == "__main__":
    raise SystemExit(main())
