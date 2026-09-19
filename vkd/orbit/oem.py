"""CCSDS OEM state vectors: UTC/EME2000, piecewise cubic Hermite interpolation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re

import numpy as np
from scipy.interpolate import CubicHermiteSpline

from vkd.sources.registry import utc


class OrbitDataError(ValueError):
    """Invalid orbital input, unavailable coverage, or unsupported semantics."""


def validate_continuous_coverage(segments, start_utc, end_utc):
    """Check every usable interpolation interval, including between output nodes.

    A coarse requested grid must not hide missing OEM knots or segment gaps.
    Overlapping segments can supply a valid alternative interval.
    """
    start, end = utc(start_utc).timestamp(), utc(end_utc).timestamp()
    intervals = []
    for segment in segments:
        lo = utc(segment.metadata.get('USEABLE_START_TIME', segment.metadata['START_TIME'])).timestamp()
        hi = utc(segment.metadata.get('USEABLE_STOP_TIME', segment.metadata['STOP_TIME'])).timestamp()
        for a, b in zip(segment.times_s, segment.times_s[1:]):
            if b-a > 300:
                continue
            a, b = max(a, lo, start), min(b, hi, end)
            if b > a:
                intervals.append((a, b))
    cursor = start
    for a, b in sorted(intervals):
        if a > cursor:
            raise OrbitDataError('Gap in continuous OEM coverage between output samples')
        cursor = max(cursor, b)
    if cursor < end:
        raise OrbitDataError('Incomplete continuous OEM coverage')


@dataclass(frozen=True)
class OemSegment:
    metadata: dict
    times_s: np.ndarray
    position_km: np.ndarray
    velocity_km_s: np.ndarray

    def interpolate(self, times_utc: list[datetime]) -> tuple[np.ndarray, np.ndarray]:
        t = np.array([utc(t).timestamp() for t in times_utc])
        start = utc(self.metadata.get('USEABLE_START_TIME', self.metadata['START_TIME'])).timestamp()
        stop = utc(self.metadata.get('USEABLE_STOP_TIME', self.metadata['STOP_TIME'])).timestamp()
        if np.any(t < max(start, self.times_s[0])) or np.any(t > min(stop, self.times_s[-1])):
            raise OrbitDataError('OEM extrapolation or access outside usable interval is forbidden')
        intervals = np.searchsorted(self.times_s, t, side='right') - 1
        intervals[intervals == len(self.times_s) - 1] -= 1
        if np.any(np.diff(self.times_s)[intervals] > 300):
            raise OrbitDataError('OEM sample gap exceeds the engineering 300-second limit')
        # Work in seconds relative to the first knot to avoid unnecessary rounding.
        spline = CubicHermiteSpline(self.times_s - self.times_s[0], self.position_km,
                                   self.velocity_km_s, axis=0, extrapolate=False)
        return spline(t - self.times_s[0]), spline(t - self.times_s[0], 1)


def parse_oem(raw: bytes) -> tuple[dict, list[OemSegment]]:
    """Read every segment and validate the original UTC/frame/units assumptions."""
    try:
        lines = raw.decode('ascii').splitlines()
    except UnicodeDecodeError as exc:
        raise OrbitDataError('OEM must be ASCII') from exc
    header, meta, rows, segments = {}, None, [], []
    in_meta = False

    def finish():
        if meta is None:
            return
        if not rows:
            raise OrbitDataError('OEM segment has no state vectors')
        if (meta.get('REF_FRAME') != 'EME2000' or meta.get('TIME_SYSTEM') != 'UTC'
                or meta.get('CENTER_NAME', '').upper() != 'EARTH'
                or meta.get('OBJECT_ID') != '1998-067-A'):
            raise OrbitDataError('Expected ISS OEM in EME2000/UTC centred on Earth')
        array = np.array(rows, dtype=float)
        if len(array) < 2 or not np.isfinite(array).all() or np.any(np.diff(array[:, 0]) <= 0):
            raise OrbitDataError('OEM times must increase and state vectors must be finite')
        # NASA ISS files use km and km/s (CCSDS OEM), regardless of COMMENT units
        # concerning mass/drag area. A units mismatch must not silently pass.
        radii = np.linalg.norm(array[:, 1:4], axis=1)
        speeds = np.linalg.norm(array[:, 4:7], axis=1)
        if np.any((radii < 6370) | (radii > 8000)) or np.any((speeds < 5) | (speeds > 10)):
            raise OrbitDataError('ISS vector magnitude inconsistent with km and km/s')
        for key in ('START_TIME', 'STOP_TIME', 'USEABLE_START_TIME', 'USEABLE_STOP_TIME'):
            if key in meta:
                utc(meta[key])
        if utc(meta['START_TIME']) >= utc(meta['STOP_TIME']):
            raise OrbitDataError('Invalid OEM segment interval')
        for arr in (array,):
            arr.setflags(write=False)
        segments.append(OemSegment(dict(meta), array[:, 0], array[:, 1:4], array[:, 4:7]))

    for line in lines:
        line = line.strip()
        if not line or line.startswith('COMMENT'):
            continue
        if line == 'META_START':
            finish(); meta, rows, in_meta = {}, [], True
        elif line == 'META_STOP':
            if not in_meta:
                raise OrbitDataError('Unexpected META_STOP')
            in_meta = False
        elif '=' in line:
            key, value = (s.strip() for s in line.split('=', 1))
            if key.endswith('_TIME') or key == 'CREATION_DATE':
                # UTC is declared explicitly by the OEM header; this is not a
                # generic assumption for arbitrary timestamp strings.
                value = value if value.endswith('Z') else value + 'Z'
            target = meta if in_meta else header
            if key in target:
                raise OrbitDataError(f'Duplicate OEM metadata: {key}')
            target[key] = value
        elif re.match(r'^\d{4}-\d{2}-\d{2}T', line):
            fields = line.split()
            if meta is None or in_meta or len(fields) != 7:
                raise OrbitDataError('Expected timestamp and six OEM state components')
            try:
                rows.append([utc(fields[0] + ('' if fields[0].endswith('Z') else 'Z')).timestamp(),
                             *map(float, fields[1:])])
            except ValueError as exc:
                raise OrbitDataError('Invalid OEM state vector') from exc
        else:
            raise OrbitDataError(f'Unsupported OEM section: {line[:60]}')
    finish()
    if in_meta or header.get('CCSDS_OEM_VERS') != '2.0' or not segments:
        raise OrbitDataError('Incomplete or unsupported OEM')
    return header, segments
