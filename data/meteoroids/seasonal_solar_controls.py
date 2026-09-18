"""Recheck A5 solar controls using a separately obtained DE421 file.

Usage: python data/meteoroids/seasonal_solar_controls.py --ephemeris /path/de421.bsp
No network access; the ephemeris hash and Skyfield version are checked first.
This validates solar coordinates, not the equinox convention of the C-2 catalogue.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path

import skyfield
from skyfield.api import load, load_file
from skyfield.framelib import ecliptic_frame, ecliptic_J2000_frame


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ephemeris', type=Path, required=True)
    args = parser.parse_args()
    expected = json.loads(Path(__file__).with_suffix('.json').read_text())
    if hashlib.sha256(args.ephemeris.read_bytes()).hexdigest() != expected['ephemeris_sha256']:
        raise ValueError('DE421 SHA-256 differs from the verified reference')
    if skyfield.__version__ != expected['skyfield']:
        raise ValueError('Skyfield version differs from the reference; report a separate comparison')
    timescale = load.timescale(builtin=True)
    with closing(load_file(str(args.ephemeris))) as ephemeris:
        for row in expected['controls']:
            time = timescale.from_datetime(datetime.fromisoformat(row['time_utc']))
            sun = ephemeris['earth'].at(time).observe(ephemeris['sun']).apparent()
            for frame, key in [(ecliptic_frame, 'solar_apparent_ecliptic_of_date_deg'),
                               (ecliptic_J2000_frame, 'solar_apparent_ecliptic_J2000_deg')]:
                value = float(sun.frame_latlon(frame)[1].degrees)
                error = abs((value - row[key] + 180.0) % 360.0 - 180.0)
                assert error < 1e-9, (row['time_utc'], key, error)
            day = datetime.fromisoformat(row['time_utc']).timestamp() / 86400.0 + 2440587.5 - 2451545.0
            anomaly = math.radians((357.529 + .98560028 * day) % 360.0)
            approximate = (280.459 + .98564736 * day + 1.915 * math.sin(anomaly) + .020 * math.sin(2.0 * anomaly)) % 360.0
            assert abs(approximate - row['usno_approx_apparent_of_date_deg']) < 1e-10
            assert abs((approximate - row['solar_apparent_ecliptic_of_date_deg'] + 180.0) % 360.0 - 180.0) * 60.0 < 1.0
    print('A5: 6 dates, DE421/J2000/of-date plus independent USNO comparison: PASS')


if __name__ == '__main__':
    main()
