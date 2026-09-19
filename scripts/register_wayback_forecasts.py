#!/usr/bin/env python3
"""Verify downloaded NOAA capture evidence and register it in A1, offline.

Input directory: original CDX JSON, exact *.txt bytes, acquisition_receipts.json.
No timestamp is inferred from filenames except the CDX capture identifier;
publication always comes from the original NOAA :Issued: header.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vkd.sources.noaa import MONTHS, SOURCE_3DAY_WAYBACK, parse_forecast
from vkd.sources.registry import SourceRegistry


def main():
    folder = ROOT / "data/source_registry_2024/noaa_wayback"
    receipts = json.loads(
        (folder / "acquisition_receipts.json").read_text(encoding="utf-8")
    )
    cdx_path = folder / "listings/cdx.json"
    cdx_bytes = cdx_path.read_bytes()
    cdx = json.loads(cdx_bytes)
    indexed = {row[1]: row for row in cdx[1:]}
    template = json.loads(
        (ROOT / "data/source_registry_2024/noaa/records.json").read_text(
            encoding="utf-8"
        )
    )["records"][0]
    records, rejected = [], []
    for receipt in receipts:
        stamp = receipt["capture"]
        row = indexed[stamp]
        raw_path = folder / "raw" / (stamp + ".txt")
        raw = raw_path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if receipt["sha256"] != sha or row[4] != "200" or receipt["status"] != 200:
            raise ValueError("Failed acquisition or changed bytes")
        text = raw.decode("utf-8")
        issue = re.findall(
            r"^:Issued:\s+(\d{4}) (\w{3}) (\d{2}) (\d{2})(\d{2}) UTC\s*$", text, re.M
        )
        if len(issue) != 1:
            raise ValueError("Expected one NOAA Issued header")
        year, month, day, hour, minute = issue[0]
        published = datetime(
            int(year),
            MONTHS[month],
            int(day),
            int(hour),
            int(minute),
            tzinfo=timezone.utc,
        )
        date_headers = [
            re.findall(r"(\w{3}) (\d{2})", line)
            for line in text.splitlines()
            if re.fullmatch(r"\s*(?:[A-Z][a-z]{2} \d{2}\s*){3}", line)
        ]
        if len(date_headers) != 3 or any(h != date_headers[0] for h in date_headers):
            raise ValueError("Inconsistent forecast columns")
        start = datetime(
            int(year),
            MONTHS[date_headers[0][0][0]],
            int(date_headers[0][0][1]),
            tzinfo=timezone.utc,
        )
        end = start + timedelta(days=3)
        capture = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        record = deepcopy(template)
        record.update(
            url=receipt["url"],
            raw_path=str(raw_path.relative_to(ROOT)),
            sha256=sha,
            bytes=len(raw),
            fetched_utc=receipt["fetched_utc"],
            http_last_modified=None,
            source_id=SOURCE_3DAY_WAYBACK,
            release_id="wayback_" + stamp,
            raw_record_id=f"{SOURCE_3DAY_WAYBACK}:{stamp}:{sha[:12]}",
            version="sha256:" + sha,
            published_utc=published.isoformat(),
            available_utc=capture.isoformat(),
            archive_timestamp=stamp,
            cdx_payload_digest=row[5],
            original_url=row[2],
            valid_from_utc=start.isoformat(),
            valid_to_utc=end.isoformat(),
            publication_evidence={
                "kind": "internal_issued_header",
                "text": f":Issued: {year} {month} {day} {hour}{minute} UTC",
            },
            availability_evidence="Internet Archive timestamped capture; original payload SHA-1 matches CDX digest; availability conservatively starts at capture",
            limitations=[
                "Available only from archive capture, never backdated to earlier Issued",
                "Capture is evidence of this archived edition, not exhaustive NOAA release history",
                "Forecast probabilities keep daily support; no conversion to EVA-window probability",
            ],
            response_refs=[
                {
                    "raw_path": str(cdx_path.relative_to(ROOT)),
                    "sha256": hashlib.sha256(cdx_bytes).hexdigest(),
                }
            ],
        )
        for channel in record["channels"]:
            channel.update(
                valid_from_utc=start.isoformat(), valid_to_utc=end.isoformat()
            )
        try:
            parse_forecast(raw, record)  # includes CDX digest and capture/Issued checks
        except ValueError as exc:
            rejected.append(
                {
                    "capture": stamp,
                    "url": receipt["url"],
                    "sha256": sha,
                    "reason": str(exc),
                    "strict_replay_admitted": False,
                }
            )
            continue
        records.append(record)
    (folder / "rejected_captures.json").write_bytes(
        (json.dumps(rejected, indent=2) + "\n").encode("utf-8")
    )
    document = {"schema_version": 1, "source": SOURCE_3DAY_WAYBACK, "records": records}
    records_path = folder / "records.json"
    # Explicit bytes make hash independent of host newline conventions.
    payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    records_path.write_bytes(payload)
    index_path = ROOT / "data/source_registry_2024/registry.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["sources"][SOURCE_3DAY_WAYBACK] = {
        "status": "captured_originals_verified_against_cdx",
        "records_path": str(records_path.relative_to(ROOT)),
        "records_sha256": hashlib.sha256(payload).hexdigest(),
        "record_count": len(records),
        "records": {
            r["raw_record_id"]: {
                "record_index": i,
                "release_id": r["release_id"],
                "sha256": r["sha256"],
            }
            for i, r in enumerate(records)
        },
    }
    index["record_count"] = sum(s["record_count"] for s in index["sources"].values())
    index_path.write_bytes(
        (json.dumps(index, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    registry = SourceRegistry(ROOT)
    print(
        f"Verified {len(registry.records(SOURCE_3DAY_WAYBACK))} captures; total {index['record_count']} records"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
