"""Parse forecast tables only; observation prose is deliberately not a forecast."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import re

from .registry import iso_utc, utc

MONTHS = {name: i for i, name in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
SOURCE_3DAY = "noaa_ngdc_3day_forecast"
SOURCE_3DAY_WAYBACK = "noaa_wayback_3day_forecast"
SOURCES_3DAY = (SOURCE_3DAY, SOURCE_3DAY_WAYBACK)
SOURCE_DAYPRE = "noaa_ngdc_daypre"
DAILY_3DAY = {"S1 or greater": "s1_or_greater_probability",
              "R1-R2": "r1_r2_probability", "R3 or greater": "r3_or_greater_probability"}
DAILY_DAYPRE = {
    "Mid/Active": "mid_active_probability", "Mid/Minor_Storm": "mid_minor_storm_probability",
    "Mid/Major-Severe_Storm": "mid_major_severe_storm_probability",
    "High/Active": "high_active_probability", "High/Minor_Storm": "high_minor_storm_probability",
    "High/Major-Severe_Storm": "high_major_severe_storm_probability",
    "Class_M": "whole_disk_m_flare_probability", "Class_X": "whole_disk_x_flare_probability",
    "Proton": "whole_disk_proton_probability",
}


class ForecastParseError(ValueError):
    """A forecast table is malformed, incomplete or inconsistent with its manifest."""


def _date(year: str | int, month: str, day: str | int) -> datetime:
    return datetime(int(year), MONTHS[month], int(day), tzinfo=timezone.utc)


def parse_forecast(raw: bytes, record: dict) -> list[dict]:
    """Return interval-valued cells with original units, resolution and provenance.

    Results are private parser records; the adapter to vkd.types belongs at the
    agreed B1 boundary. Daily probabilities remain daily probabilities.
    """
    try:
        if record["source_id"] == SOURCE_3DAY_WAYBACK:
            from .wayback_forecast import verify_capture
            verify_capture(raw, record)
        return _parse(raw.decode("utf-8"), record)
    except (UnicodeDecodeError, KeyError, ValueError, OverflowError, IndexError) as exc:
        if isinstance(exc, ForecastParseError):
            raise
        raise ForecastParseError(str(exc)) from exc


def _parse(text: str, record: dict) -> list[dict]:
    issued = re.findall(r"^:Issued:\s+(\d{4}) (\w{3}) (\d{2}) (\d{2})(\d{2}) UTC\s*$", text, re.M)
    if len(issued) != 1:
        raise ForecastParseError("Expected exactly one Issued header")
    year, month, day, hour, minute = issued[0]
    if int(hour) >= 24 or int(minute) >= 60:
        raise ForecastParseError("Invalid Issued clock time")
    published = _date(year, month, day) + timedelta(hours=int(hour), minutes=int(minute))
    if published != utc(record["published_utc"]):
        raise ForecastParseError("Issued header disagrees with registry")
    source = record["source_id"]
    if source == SOURCE_DAYPRE:
        header = re.findall(r"^:Prediction_dates:(.*)$", text, re.M)
        if len(header) != 1:
            raise ForecastParseError("Missing or repeated Prediction_dates header")
        dates = [_date(*parts) for parts in re.findall(r"(\d{4}) (\w{3}) (\d{2})", header[0])]
    elif source in SOURCES_3DAY:
        headers = [re.findall(r"(\w{3}) (\d{2})", line) for line in text.splitlines()
                   if re.fullmatch(r"\s*(?:[A-Z][a-z]{2} \d{2}\s*){3}", line)]
        if len(headers) != 3 or not all(h == headers[0] for h in headers):
            raise ForecastParseError("Forecast sections have inconsistent date columns")
        dates = []
        for mon, d in headers[0]:
            candidates = [_date(int(year) + offset, mon, d) for offset in (-1, 0, 1)]
            dates.append(min(candidates, key=lambda date: abs(date - published)))
    else:
        raise ForecastParseError(f"Unsupported NOAA forecast source: {source}")
    if len(dates) != 3 or any(b - a != timedelta(days=1) for a, b in zip(dates, dates[1:])):
        raise ForecastParseError("Expected three consecutive prediction dates")
    channels = {c["channel_id"]: c for c in record["channels"]}
    cells: list[dict] = []
    seen: set[tuple[str, datetime]] = set()

    def add(channel_id: str, values: str, start_hour: int = 0, hours: int = 24) -> None:
        channel = channels[channel_id]
        unit = "1" if hours == 3 else ("sfu" if channel_id == "f107" else "%")
        if channel["unit"] != unit or channel["temporal_resolution"] != f"{hours}h":
            raise ForecastParseError("Channel units or resolution disagree with product")
        if channel.get("kind") != "external_forecast":
            raise ForecastParseError("Channel is not an external forecast")
        tokens = re.sub(r"\(G[0-5]\)", "", values).split()
        if len(tokens) != 3:
            raise ForecastParseError(f"Expected three values for {channel_id}")
        for date, token in zip(dates, tokens):
            value = float(token.removesuffix("%"))
            if not math.isfinite(value) or value < 0:
                raise ForecastParseError(f"Invalid {channel_id} value: {token}")
            if ((channel["unit"] == "%" and value > 100)
                    or (channel_id in ("noaa_kp", "mid_latitude_k", "high_latitude_k") and value > 9)):
                raise ForecastParseError(f"Out-of-range {channel_id} value: {token}")
            start = date + timedelta(hours=start_hour)
            end = start + timedelta(hours=hours)
            if (start < utc(channel["valid_from_utc"]) or end > utc(channel["valid_to_utc"])
                    or (channel_id, start) in seen):
                raise ForecastParseError("Duplicate cell or validity mismatch")
            seen.add((channel_id, start))
            cells.append({"source_id": source, "raw_record_id": record["raw_record_id"],
                          "channel_id": channel_id, "value": value, "unit": channel["unit"],
                          "valid_from_utc": iso_utc(start), "valid_to_utc": iso_utc(end),
                          "published_utc": record["published_utc"], "kind": "external_forecast",
                          "temporal_resolution": channel["temporal_resolution"]})

    lines = text.splitlines()
    in_forecast_table = False
    for i, line in enumerate(lines):
        line = line.strip()
        if source in SOURCES_3DAY:
            if re.fullmatch(r"(?:[A-Z][a-z]{2} \d{2}\s*){3}", line):
                in_forecast_table = True
            elif line.startswith('Rationale:'):
                in_forecast_table = False
            if not in_forecast_table:
                continue  # prose can also start with "R1-R2 conditions ..."
        prefix = r"(\d{2})-(\d{2})UT" if source in SOURCES_3DAY else r"(Mid|High)/(\d{2})-(\d{2})UT"
        match = re.fullmatch(prefix + r"\s+(.+)", line)
        if match:
            if source in SOURCES_3DAY:
                begin, end, values = match.groups(); channel_id = "noaa_kp"
            else:
                region, begin, end, values = match.groups()
                channel_id = "mid_latitude_k" if region == "Mid" else "high_latitude_k"
            start_hour = int(begin)
            if start_hour not in range(0, 24, 3) or int(end) != (start_hour + 3) % 24:
                raise ForecastParseError("Invalid three-hour bin")
            add(channel_id, values, start_hour, 3)
        for label, channel_id in (DAILY_3DAY if source in SOURCES_3DAY else DAILY_DAYPRE).items():
            match = re.fullmatch(re.escape(label) + r"\s+(.+)", line)
            if match:
                add(channel_id, match[1])
        if source == SOURCE_DAYPRE and line == ":10cm_flux:":
            add("f107", lines[i + 1].strip())
    for channel_id, channel in channels.items():
        expected = 24 if channel["temporal_resolution"] == "3h" else 3
        if sum(cell["channel_id"] == channel_id for cell in cells) != expected:
            raise ForecastParseError(f"Incomplete forecast table: {channel_id}")
    return sorted(cells, key=lambda c: (c["channel_id"], c["valid_from_utc"]))
