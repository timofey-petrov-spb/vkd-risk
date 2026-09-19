#!/usr/bin/env python3
"""Repeatable numerical validation on actual received catalogue bytes (offline).

This validates the screening algorithm, NOT actual GP orbit errors or a Pc model.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import timedelta
import json
import hashlib
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
from scipy.optimize import minimize_scalar
from sgp4.api import jday
import sgp4

from vkd.conjunctions import screen_catalogue
from vkd.conjunctions.snapshot import load_config, load_snapshot
from vkd.sources.registry import utc


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--snapshot", type=Path, default=ROOT / "data/conjunctions/demo_2026-09-19"
    )
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    catalogues, manifest = load_snapshot(args.snapshot)
    start = utc(manifest["demo_start_utc"])
    config = load_config()
    nominal = screen_catalogue(
        catalogues, start, start + timedelta(hours=6), config=config
    )
    print("6h nominal finished", flush=True)
    # All eligible objects, no radial rejection, six times finer temporal grid.
    fine = screen_catalogue(
        catalogues,
        start,
        start + timedelta(hours=6),
        config=replace(config, coarse_step_s=10, use_radial_filter=False),
    )
    print("6h fine/no-radial finished", flush=True)
    daily = screen_catalogue(
        catalogues, start, start + timedelta(hours=24), config=config
    )
    objects = {}
    for catalogue in catalogues:
        for obj in catalogue.objects:
            if (
                obj.norad_id not in objects
                or obj.epoch_utc > objects[obj.norad_id].epoch_utc
            ):
                objects[obj.norad_id] = obj
    primary = objects[25544]
    jd, frac = jday(
        start.year, start.month, start.day, start.hour, start.minute, start.second
    )
    errors = []
    for event in nominal.encounters:
        if event.at_horizon_boundary:
            continue
        other = objects[event.other_norad_id]
        t = (event.conjunction.tca_utc - start).total_seconds()

        def distance(offset_s):
            _, p1, _ = primary.satrec.sgp4(jd, frac + (t + offset_s) / 86400)
            _, p2, _ = other.satrec.sgp4(jd, frac + (t + offset_s) / 86400)
            return float(np.linalg.norm(np.asarray(p1) - p2))

        # Independent local reference uses only positions, 0.1s dense search,
        # then scalar minimization; production solves approach/recession r·v=0.
        mesh = np.linspace(-3, 3, 61)
        ds = np.array([distance(x) for x in mesh])
        i = int(np.argmin(ds))
        if i == 0 or i == len(mesh) - 1:
            raise AssertionError("Reference minimum outside local bracket")
        ref = minimize_scalar(
            distance,
            bounds=(mesh[i - 1], mesh[i + 1]),
            method="bounded",
            options={"xatol": 1e-7},
        )
        errors.append(
            {
                "norad_id": event.other_norad_id,
                "tca_error_s": float(abs(ref.x)),
                "distance_error_km": float(
                    abs(ref.fun - event.conjunction.miss_distance_km)
                ),
            }
        )
    coarse_keys = [
        (e.other_norad_id, e.conjunction.tca_utc) for e in nominal.encounters
    ]
    fine_keys = [(e.other_norad_id, e.conjunction.tca_utc) for e in fine.encounters]
    missing = [
        str((oid, t))
        for oid, t in fine_keys
        if not any(
            oid == a and abs((t - b).total_seconds()) < 0.01 for a, b in coarse_keys
        )
    ]
    extra = [
        str((oid, t))
        for oid, t in coarse_keys
        if not any(
            oid == a and abs((t - b).total_seconds()) < 0.01 for a, b in fine_keys
        )
    ]
    max_time = max((e["tca_error_s"] for e in errors), default=None)
    max_distance = max((e["distance_error_km"] for e in errors), default=None)
    passed = bool(
        not missing
        and not extra
        and max_time is not None
        and max_time < 0.005
        and max_distance < 1e-6
    )

    def summary(result):
        return {
            "status": result.status,
            "stats": result.stats,
            "events_under_5km": sum(
                e.conjunction.miss_distance_km < 5 for e in result.encounters
            ),
            "minimum_distance_km": min(
                (e.conjunction.miss_distance_km for e in result.encounters),
                default=None,
            ),
        }

    audit = {
        "numerical_checks_passed": passed,
        "absolute_orbit_accuracy_validated": False,
        "collision_probability_computed": False,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sgp4": sgp4.__version__,
        "base_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, encoding="utf-8"
        ).strip(),
        "code_sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [
                *sorted((ROOT / "vkd/conjunctions").glob("*.py")),
                ROOT / "vkd/sources/catalogue.py",
                Path(__file__),
            ]
        },
        "provenance": nominal.to_dict()["provenance"],
        "nominal_6h": summary(nominal),
        "fine_6h_no_radial": summary(fine),
        "nominal_24h": summary(daily),
        "missing_events": missing,
        "extra_events": extra,
        "independent_local_reference_count": len(errors),
        "max_reference_tca_error_s": max_time,
        "max_reference_distance_error_km": max_distance,
        "reference_errors": errors,
        "limitations": [
            "Same SGP4 orbit model for both methods: NOT observational validation",
            "Sampling/refinement comparison limited to this pinned 6h case",
            "Catalogue subset, stale and invalid elements excluded explicitly",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    daily_path = args.output.with_name("screening_24h.json")
    daily_path.write_text(
        json.dumps(daily.to_dict(), ensure_ascii=False, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in audit.items()
                if k
                in (
                    "numerical_checks_passed",
                    "independent_local_reference_count",
                    "max_reference_tca_error_s",
                    "max_reference_distance_error_km",
                    "missing_events",
                    "extra_events",
                )
            },
            indent=2,
        )
    )
    print(
        json.dumps(
            {
                "daily_stats": {
                    k: v
                    for k, v in daily.stats.items()
                    if k not in ("excluded", "shared_primary_elements")
                }
            },
            indent=2,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
