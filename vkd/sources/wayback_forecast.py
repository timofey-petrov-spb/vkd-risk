"""Evidence checks for original NOAA bulletins captured by Internet Archive.

The SHA-1 CDX digest identifies the archived payload; SHA-256 is our local
integrity hash. The capture is a conservative historical availability bound.
"""

import base64
from datetime import datetime, timezone
import hashlib

from .registry import utc


def verify_capture(raw, record):
    capture = datetime.strptime(record["archive_timestamp"], "%Y%m%d%H%M%S").replace(
        tzinfo=timezone.utc
    )
    digest = base64.b32encode(hashlib.sha1(raw).digest()).decode("ascii").rstrip("=")
    if digest != record["cdx_payload_digest"]:
        raise ValueError("Wayback payload disagrees with archived CDX digest")
    if (
        utc(record["available_utc"]) != capture
        or utc(record["published_utc"]) > capture
    ):
        raise ValueError("Wayback availability must equal capture, after Issued")
    expected = f"https://web.archive.org/web/{record['archive_timestamp']}id_/https://services.swpc.noaa.gov/text/3-day-forecast.txt"
    if record["url"] != expected:
        raise ValueError("Wayback URL does not identify the exact original capture")
