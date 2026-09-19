"""CelesTrak GP CSV catalogue, exact receipts and explicit selection scope.

CSV preserves six/nine-digit NORAD identifiers that legacy TLE cannot carry.
An element epoch is NOT its publication time. No acquisition happens on import.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import io
import math

from sgp4 import omm
from sgp4.api import Satrec, WGS72

from .live_cache import Product, acquire
from .registry import utc

CATALOGUE_QUERIES = {
    "active": ("GROUP=active", "Действующие аппараты; не весь каталог и не весь мусор"),
    "debris": ("NAME=DEB", "Объекты с DEB в имени; не весь каталог мусора"),
    "rocket_bodies": (
        "NAME=R%2FB",
        "Объекты с R/B в имени; не все неработающие аппараты",
    ),
}


@dataclass(frozen=True)
class GPObject:
    norad_id: int
    name: str
    epoch_utc: datetime
    raw_record_id: str
    satrec: Satrec = field(repr=False, compare=False)


@dataclass(frozen=True)
class Catalogue:
    objects: tuple[GPObject, ...]
    metadata: dict
    input_rows: int
    rejected_rows: tuple[str, ...] = ()
    duplicate_rows: int = 0


def parse_catalogue(
    raw: bytes,
    *,
    available_utc: datetime,
    url: str,
    scope: str,
    source_id: str = "celestrak_catalogue",
) -> Catalogue:
    """Parse a received CSV. available_utc is a receipt/capture, never TLE epoch.

    Malformed individual rows are exposed; malformed headers/empty data fail.
    Omitted TEME/UTC/Earth/SGP4 fields follow CelesTrak's GP CSV specification.
    Explicit conflicting fields are rejected, never silently reinterpreted.
    """
    available_utc = utc(available_utc)
    if not scope.strip() or not raw:
        raise ValueError("Catalogue bytes and explicit scope are required")
    sha = hashlib.sha256(raw).hexdigest()
    record_id = f"{source_id}:{sha}"
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    numeric = (
        "MEAN_MOTION",
        "ECCENTRICITY",
        "INCLINATION",
        "RA_OF_ASC_NODE",
        "ARG_OF_PERICENTER",
        "MEAN_ANOMALY",
        "BSTAR",
        "MEAN_MOTION_DOT",
        "MEAN_MOTION_DDOT",
    )
    required = set(numeric) | {
        "EPOCH",
        "NORAD_CAT_ID",
        "OBJECT_NAME",
        "OBJECT_ID",
        "CLASSIFICATION_TYPE",
        "EPHEMERIS_TYPE",
        "ELEMENT_SET_NO",
        "REV_AT_EPOCH",
    }
    if not required.issubset(reader.fieldnames or ()):
        raise ValueError("Not a CelesTrak GP CSV catalogue: required columns missing")
    objects, rejected, count, duplicates = {}, [], 0, 0
    for count, row in enumerate(reader, 1):
        try:
            if None in row or any(row[k] is None for k in required):
                raise ValueError("truncated or extra CSV fields")
            for key, expected in (
                ("REF_FRAME", "TEME"),
                ("TIME_SYSTEM", "UTC"),
                ("CENTER_NAME", "EARTH"),
                ("MEAN_ELEMENT_THEORY", "SGP4"),
            ):
                if row.get(key, expected) != expected:
                    raise ValueError(f"unsupported {key}")
            values = {k: float(row[k]) for k in numeric}
            if not all(math.isfinite(v) for v in values.values()):
                raise ValueError("nonfinite orbital elements")
            if not (
                0 < values["MEAN_MOTION"] < 25
                and 0 <= values["ECCENTRICITY"] < 1
                and 0 <= values["INCLINATION"] <= 180
            ):
                raise ValueError("orbital elements outside supported domain")
            if any(
                not 0 <= values[k] < 360
                for k in ("RA_OF_ASC_NODE", "ARG_OF_PERICENTER", "MEAN_ANOMALY")
            ):
                raise ValueError("orbital angle outside [0, 360)")
            norad_id = int(row["NORAD_CAT_ID"])
            if not 1 <= norad_id <= 999999999 or int(row["EPHEMERIS_TYPE"]) != 0:
                raise ValueError("unsupported NORAD ID or ephemeris type")
            # CelesTrak CSV explicitly specifies UTC even when ISO lacks suffix.
            epoch = datetime.fromisoformat(row["EPOCH"].replace("Z", "+00:00"))
            epoch = (
                epoch.replace(tzinfo=timezone.utc)
                if epoch.tzinfo is None
                else utc(epoch)
            )
            row["EPOCH"] = epoch.strftime("%Y-%m-%dT%H:%M:%S.%f")
            satellite = Satrec()
            omm.initialize(satellite, row, WGS72)
            if satellite.error or not math.isfinite(satellite.a):
                raise ValueError(f"SGP4 initialization error {satellite.error}")
            obj = GPObject(
                norad_id,
                row["OBJECT_NAME"].strip() or str(norad_id),
                epoch,
                record_id,
                satellite,
            )
            if norad_id in objects:
                duplicates += 1
                if objects[norad_id].epoch_utc >= epoch:
                    continue
            objects[norad_id] = obj
        except (ValueError, TypeError, OverflowError) as exc:
            rejected.append(f"row {count}, NORAD {row.get('NORAD_CAT_ID')}: {exc}")
    if not objects:
        raise ValueError("Catalogue contains no valid orbital elements")
    return Catalogue(
        tuple(objects.values()),
        {
            "source_id": source_id,
            "raw_record_id": record_id,
            "sha256": sha,
            "version": sha,
            "bytes": len(raw),
            "url": url,
            "scope": scope,
            "published_utc": None,
            "available_utc": available_utc.isoformat(),
            "fetched_utc": available_utc.isoformat(),
            "availability_proof": "Exact bytes available at receipt/capture; epoch is not publication",
            "format": "CelesTrak GP CSV",
            "frame": "TEME",
            "gravity_model": "WGS72",
            "complete_public_catalogue": False,
        },
        count,
        tuple(rejected),
        duplicates,
    )


def _parse_live(raw, fetched_utc):
    # Cache age is the receipt age. Per-object epoch age is checked by screening.
    catalogue = parse_catalogue(
        raw, available_utc=fetched_utc, url="", scope="GP query"
    )
    return {
        "data_utc": fetched_utc,
        "published_utc": None,
        "quality": "model",
        "rejected_rows": len(catalogue.rejected_rows),
    }


def catalogue_latest(selection="active", *, disabled=False, **kwargs):
    """Return (Catalogue | None, Fetch); on/cache/off and the A4 receipt protocol.

    One query per two hours at most, no retry after HTTP refusal. Pass cache_dir
    shared by sessions. Subsets stay explicit, including when no encounter exists.
    """
    if selection not in CATALOGUE_QUERIES:
        raise ValueError(f"selection must be one of {tuple(CATALOGUE_QUERIES)}")
    query, scope = CATALOGUE_QUERIES[selection]
    product = Product(
        f"celestrak_catalogue_{selection}",
        f"https://celestrak.org/NORAD/elements/gp.php?{query}&FORMAT=csv",
        _parse_live,
        24 * 60,
        7200,
        strict_poll=True,
    )
    receipt = acquire(product, disabled=disabled, **kwargs)
    if receipt.payload is None:
        return None, receipt
    catalogue = parse_catalogue(
        receipt.raw,
        available_utc=receipt.fetched_utc,
        url=receipt.url,
        scope=scope,
        source_id=receipt.source_id,
    )
    # Preserve exact A4 raw path / headers / hash / receipt, plus catalogue scope.
    return (
        Catalogue(
            catalogue.objects,
            {**receipt.metadata, **catalogue.metadata, "raw_path": receipt.raw_path},
            catalogue.input_rows,
            catalogue.rejected_rows,
            catalogue.duplicate_rows,
        ),
        receipt,
    )


def primary_catalogue_from_fetch(receipt):
    """Reuse the EXACT A4 ISS receipt used by orbit_bridge; no second fetch.

    Include the returned one-object catalogue alongside secondary catalogues,
    and pass its object explicitly as screen_catalogue(primary=...).
    """
    from vkd.orbit.trajectory import satellite_from_tle
    from .live_parsers import parse_tle

    if receipt.payload is None or receipt.raw is None or receipt.fetched_utc is None:
        raise ValueError("No usable primary orbit receipt")
    if hashlib.sha256(receipt.raw).hexdigest() != receipt.metadata["sha256"]:
        raise ValueError("Primary receipt digest mismatch")
    parsed = parse_tle(receipt.raw, receipt.fetched_utc)
    satellite = satellite_from_tle(parsed["payload_text"].encode("ascii"))
    obj = GPObject(
        25544,
        "ISS",
        parsed["data_utc"],
        receipt.metadata["raw_record_id"],
        satellite.model,
    )
    return Catalogue(
        (obj,),
        {
            **receipt.metadata,
            "scope": "Точные элементы МКС из основного расчёта",
            "complete_public_catalogue": False,
            "format": parsed["selected"]["input_format"],
            "frame": "TEME",
            "gravity_model": "WGS72",
        },
        1,
    )
