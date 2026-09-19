"""Verify frozen evidence with stdlib; optionally repeat the NetCDF comparison."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parent
EXPECTED_SHA256 = {
    "sci_sgps-l2-avg5m_g18_d20240510_v3-0-2.nc":
        "c4845f6da65b090525cac560037bc038dde9850a7334dbf76d4b4c944c59cb02",
    "iswa_primary_20240510_0000_0030.json":
        "f19686c797ef94a77e59002e33313d4cada4d96a764ee793b4af67cd8c900f75",
    "timestamp-proof.json":
        "6cf71a87bdb587e0baae174f3c0ed594af0b70a8dbe4d0a19587bdf46bac5286",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def verify(with_h5py: bool = False) -> dict:
    for name, expected in EXPECTED_SHA256.items():
        digest = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        require(digest == expected, f"SHA-256 mismatch: {name}")
    proof = json.loads((ROOT / "timestamp-proof.json").read_text(encoding='utf-8'))
    raw = json.loads((ROOT / proof["hapi_raw_path"]).read_text(encoding='utf-8'))
    require(proof["proof_version"] == "1.0", "Unknown proof version")
    require(proof["hapi_dataset"] == "goesp_part_flux_P5M", "Unexpected dataset")
    require(proof["crosscheck_sha256"] == EXPECTED_SHA256[proof["crosscheck_path"]],
            "NetCDF hash differs from evidence")
    require(proof["hapi_raw_sha256"] == EXPECTED_SHA256[proof["hapi_raw_path"]],
            "HAPI hash differs from evidence")
    require(raw["status"]["code"] == 1200, "HAPI response was not successful")
    parameters = raw["parameters"]
    names = [parameter["name"] for parameter in parameters]
    require(len(names) == len(set(names)), "Duplicate HAPI column names")
    columns = {name: names.index(name) for name in ("Time", "P500", "satelliteProton")}
    require(parameters[columns["Time"]]["units"] == "UTC", "Time must be UTC")
    require(parameters[columns["P500"]]["units"] == "Protons/cm^2 - s^-1 - sr",
            "Unexpected archived flux units")
    rows = raw["data"]
    require(len(rows) == 6, "Expected exactly six independent comparison points")
    require(all(len(row) == len(parameters) for row in rows), "Malformed HAPI row")
    timestamps = [row[columns["Time"]] for row in rows]
    expected_times = [
        (datetime(2024, 5, 10, tzinfo=timezone.utc) + timedelta(minutes=5 * i))
        .isoformat().replace("+00:00", "Z") for i in range(6)
    ]
    require(timestamps == proof["timestamps"] == expected_times, "Timestamp mismatch")
    values = [row[columns["P500"]] for row in rows]
    require(all(isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value >= 0 for value in values),
            "Invalid P500 measurement")
    require(values == proof["values_pfu"], "HAPI P500 values differ from evidence")
    require([row[columns["satelliteProton"]] for row in rows]
            == proof["hapi_satelliteProton"] == ["GOES-18"] * 6,
            "Proton spacecraft mismatch")
    require(proof["crosscheck_threshold_mev"] == 500
            and proof["crosscheck_sensor_index"] == 0, "Unexpected comparison channel")
    require(proof["time_names_equal"] is True and proof["values_exactly_equal"] is True
            and proof["max_abs_difference"] == 0.0, "Frozen comparison did not pass")

    if with_h5py:
        # This optional developer check is not a runtime dependency of vkd.
        import h5py

        with h5py.File(ROOT / proof["crosscheck_path"], "r") as nc:
            units = nc["time"].attrs["units"].decode()
            require(units == "seconds since 2000-01-01 12:00:00 UTC",
                    "Unexpected NetCDF time epoch")
            nc_times = [
                (datetime(2000, 1, 1, 12, tzinfo=timezone.utc)
                 + timedelta(seconds=float(value))).isoformat().replace("+00:00", "Z")
                for value in nc["time"][:6]
            ]
            require(nc_times == timestamps, "NetCDF timestamp mismatch")
            require(nc["time"].attrs["long_name"].decode()
                    == proof["netcdf_time_long_name"], "NetCDF time semantics mismatch")
            require("start of the averaging period" in proof["netcdf_time_long_name"],
                    "NetCDF does not identify beginning of averaging period")
            require(float(nc["IntegralProtonEffectiveEnergy"][0]) == 500000.0
                    and nc["IntegralProtonEffectiveEnergy"].attrs["units"].decode() == "keV",
                    "This check must use the 500 MeV channel")
            require(nc["yaw_flip_flag"][:6].tolist()
                    == proof["crosscheck_yaw_flip_flag"] == [0] * 6,
                    "Unexpected spacecraft orientation")
            require(nc["AvgIntProtonFlux"].attrs["units"].decode()
                    == "protons/(cm^2 sr s)", "Unexpected NetCDF flux units")
            require([float(value) for value in nc["AvgIntProtonFlux"][:6, 0]] == values,
                    "NetCDF P500 flux does not exactly match HAPI")
    return {
        "files_hash_verified": len(EXPECTED_SHA256),
        "hapi_points_verified": len(rows),
        "threshold_mev": 500,
        "netcdf_numeric_comparison": "repeated" if with_h5py else "not_repeated",
        "published_utc_proven": False,
        "p10_values_validated": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-h5py", action="store_true",
                        help="Also repeat the numeric comparison using optional h5py")
    args = parser.parse_args()
    try:
        result = verify(args.with_h5py)
    except (OSError, ValueError, KeyError, TypeError, ImportError) as error:
        parser.exit(1, f"Validation failed: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
