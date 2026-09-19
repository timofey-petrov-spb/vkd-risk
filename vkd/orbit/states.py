"""JSON-native inertial-state extension of the existing A3 provenance report.

No second propagator or assessment model. EME2000 is mean equator/equinox
J2000 with the geocentre as origin; it is neither TEME nor rotating ITRS.
"""
from __future__ import annotations

import hashlib
from importlib.metadata import version
import json

import numpy as np

from vkd.sources.registry import iso_utc
from .oem import OrbitDataError

SCHEMA_VERSION = 'orbit-inertial-states-1'


def make_state_payload(times, position_km, velocity_km_s, assignments, segments,
                       records, timescale, method, is_reconstruction):
    """Package the very states used for the geographic trajectory."""
    p, v = np.asarray(position_km), np.asarray(velocity_km_s)
    if p.shape != (len(times), 3) or v.shape != p.shape or not np.isfinite(p).all() or not np.isfinite(v).all():
        raise OrbitDataError('Invalid inertial position/velocity arrays')
    if len(assignments) != len(times) or any(b <= a for a, b in zip(times, times[1:])):
        raise OrbitDataError('Invalid inertial time grid or source assignments')
    # Canonical little-endian doubles make the fingerprint platform independent.
    fingerprint = hashlib.sha256()
    for values in (*timescale.delta_t_table, timescale.leap_dates, timescale.leap_offsets):
        array = np.asarray(values, dtype='<f8')
        fingerprint.update(str(array.shape).encode('ascii'))
        fingerprint.update(array.tobytes())
    source_ids = sorted({s['raw_record_id'] for s in segments})
    rows = []
    for i, when in enumerate(times):
        index = int(assignments[i])
        if not 0 <= index < len(segments):
            raise OrbitDataError('Inertial sample has no source segment')
        rows.append({'t_utc': iso_utc(when), 'position_km': p[i].tolist(),
                     'velocity_km_s': v[i].tolist(), 'segment_index': index,
                     'raw_record_id': segments[index]['raw_record_id']})
    result = {'schema_version': SCHEMA_VERSION, 'frame': 'EME2000',
        'axes': 'mean equator and equinox J2000', 'center': 'EARTH', 'time_system': 'UTC',
        'position_unit': 'km', 'velocity_unit': 'km/s', 'method': method,
        'is_reconstruction': is_reconstruction,
        'source_hashes': {rid: records[rid]['sha256'] for rid in source_ids},
        'coverage_from_utc': iso_utc(times[0]), 'coverage_to_utc': iso_utc(times[-1]),
        'endpoint_policy': 'both endpoints included; last step may be shorter',
        'software': {name: version(name) for name in ('skyfield','sgp4','numpy','scipy')},
        'timescale_tables_sha256': fingerprint.hexdigest(),
        'segments': [{**{k:v for k,v in s.items() if k != 'frame'},
                      'input_frame':s['frame'], 'output_frame':'EME2000',
                      'support_kind':'extent of assigned output samples'} for s in segments], 'samples': rows,
        'limitations': [
            'States preserve the orbit source eligibility; reconstruction is not a strict historical forecast.',
            'Radiants and states must share axes and geocentric velocity convention before subtraction.',
            'These state vectors do not resolve uncertainty of the seasonal flux catalogue.']}
    if method == 'sgp4':
        result['transform'] = 'SGP4 TEME -> Skyfield GCRS -> fixed IERS-2003 frame bias -> EME2000'
        result['limitations'].append('Skyfield rotates the SGP4 velocity without the small TEME precession/nutation frame-rate term; no sub-mm/s claim.')
    else:
        result['transform'] = 'OEM EME2000 position and analytic derivative of piecewise cubic Hermite interpolation'
    encoded = json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    result['content_sha256'] = hashlib.sha256(encoded).hexdigest()
    return result
