"""ISS trajectory for the B1 interface, with a separate auditable provenance report."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
from sgp4.api import Satrec, WGS72
from sgp4.io import verify_checksum
from skyfield.api import EarthSatellite, load, wgs84
from skyfield.constants import AU_KM, DAY_S
from skyfield.framelib import ICRS_to_J2000, itrs
from skyfield.positionlib import Geocentric

from vkd.sources.registry import SourceRegistry, iso_utc, utc
from vkd.types import MagMethod, TrajectoryMeta, TrajectoryPoint
from .magnetic import magnetic_coordinates
from .oem import OrbitDataError, parse_oem

ROOT = Path(__file__).resolve().parents[2]
# Engineering admissibility limit, not a physical guarantee of TLE accuracy.
MAX_TLE_AGE_DAYS = 3.0


def satellite_from_tle(raw: bytes, *, expected_norad: int | None = 25544):
    lines = [line.rstrip() for line in raw.decode('ascii').splitlines() if line.strip()]
    if len(lines) == 3:
        name, line1, line2 = lines
    elif len(lines) == 2:
        name, (line1, line2) = 'ISS', lines
    else:
        raise OrbitDataError('Expected exactly one two-line element set')
    if not line1.startswith('1 ') or not line2.startswith('2 ') or len(line1) != 69 or len(line2) != 69:
        raise OrbitDataError('Invalid TLE line layout')
    try:
        verify_checksum(line1, line2)
        satrec = Satrec.twoline2rv(line1, line2, WGS72)
    except ValueError as exc:
        raise OrbitDataError(f'Invalid TLE: {exc}') from exc
    if line1[2:7] != line2[2:7] or (expected_norad is not None and satrec.satnum != expected_norad):
        raise OrbitDataError('TLE satellite identity mismatch')
    satellite = EarthSatellite.from_satrec(satrec, load.timescale(builtin=True))
    satellite.name = name
    return satellite


def _checked_model(root: Path, filename: str) -> tuple[Path, dict]:
    manifest = json.loads((root / 'data/orbit/manifest.json').read_text())
    item = next((r for r in manifest['records'] if r['file'] == filename), None)
    if item is None:
        raise OrbitDataError(f'Missing orbital data manifest entry: {filename}')
    path = root / 'data/orbit' / filename
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != item['sha256'] or len(raw) != item['bytes']:
        raise OrbitDataError(f'Orbital data hash/length mismatch: {filename}')
    return path, item


def trajectory(start_utc: datetime, minutes: int, saa_B_threshold_nT: float, **kwargs):
    """B1 entry point: return (TrajectoryMeta, list[TrajectoryPoint]).

    The grid includes both endpoints: minutes=1920 yields 1921 one-minute
    points, covering the complete 24h search + 8h final window. History defaults
    to explicitly labelled OEM reconstruction, never propagation of today's TLE.
    """
    meta, points, _ = trajectory_with_provenance(start_utc, minutes, saa_B_threshold_nT, **kwargs)
    return meta, points


def trajectory_with_provenance(start_utc: datetime, minutes: int, saa_B_threshold_nT: float, *,
                               mode: str = 'auto', cutoff_utc: datetime | None = None,
                               repo_root: str | Path = ROOT, step_seconds: int = 60,
                               max_tle_age_days: float = MAX_TLE_AGE_DAYS):
    """Same solution plus per-source records for the shared export Manifest.

    Optional finer grids serve convergence checks. No implicit network requests,
    coefficient extrapolation, cached global output or silent source fallback.
    """
    start = utc(start_utc)
    if type(minutes) is not int or not 1 <= minutes <= 1920:
        raise ValueError('minutes must be an integer in [1, 1920]')
    if type(step_seconds) is not int or not 1 <= step_seconds <= 600:
        raise ValueError('step_seconds must be an integer in [1, 600]')
    if not np.isfinite(saa_B_threshold_nT) or saa_B_threshold_nT <= 0:
        raise ValueError('SAA field threshold must be positive and finite')
    if not np.isfinite(max_tle_age_days) or max_tle_age_days <= 0:
        raise ValueError('TLE age limit must be positive and finite')
    end = start + timedelta(minutes=minutes)
    seconds = list(range(0, minutes * 60, step_seconds)) + [minutes * 60]
    times = [start + timedelta(seconds=s) for s in seconds]
    root = Path(repo_root)
    if mode == 'auto':
        mode = 'history_review' if datetime(2024, 5, 1, tzinfo=timezone.utc) <= start < datetime(2024, 7, 1, tzinfo=timezone.utc) else 'live'
    if mode not in ('live', 'history_review', 'history_forecast'):
        raise ValueError('Unknown trajectory mode')
    cutoff = utc(cutoff_utc) if cutoff_utc is not None else start
    if mode == 'history_forecast' and cutoff_utc is None:
        raise ValueError('history_forecast requires an explicit cutoff_utc')
    if mode == 'history_forecast' and cutoff > start:
        raise ValueError('Historical cutoff cannot be after forecast start')
    ts = load.timescale(builtin=True)
    t = ts.from_datetimes(times)
    provenance = {'algorithm_version': 'orbit-a3-v1', 'mode': mode, 'step_seconds': step_seconds,
                  'cutoff_utc': iso_utc(cutoff) if mode == 'history_forecast' else None,
                  'records': {}, 'segments': [], 'limitations': [],
                  'earth_orientation': 'Skyfield bundled UT1/leap seconds, no external polar-motion table; not centimetre-level geodesy'}
    if mode in ('history_review', 'history_forecast'):
        registry = SourceRegistry(root)
        candidates = []
        for record in registry.records('nasa_jsc_oem'):
            if utc(record['valid_from_utc']) > start or utc(record['valid_to_utc']) < end:
                continue
            created = utc(record['created_utc'])
            modified = utc(record['s3_metadata']['LastModified'])
            if created > cutoff or modified > cutoff:
                continue
            if mode == 'history_forecast':
                if (not record['available_utc'] or not record['published_utc']
                        or utc(record['available_utc']) > cutoff or utc(record['published_utc']) > cutoff
                        or record['strict_replay_eligibility'] != 'verified_historical_publication'):
                    continue
            candidates.append(record)
        if not candidates:
            raise OrbitDataError('No OEM covering the full interval with required historical availability; reconstruction is a separate mode')
        record = max(candidates, key=lambda r: (utc(r['created_utc']), r['release_id']))
        header, segments = parse_oem(registry.raw_bytes(record['raw_record_id']))
        position_km, velocity_km_s = np.empty((len(times), 3)), np.empty((len(times), 3))
        assigned = np.zeros(len(times), dtype=bool)
        for segment in segments:
            lo = utc(segment.metadata.get('USEABLE_START_TIME', segment.metadata['START_TIME']))
            hi = utc(segment.metadata.get('USEABLE_STOP_TIME', segment.metadata['STOP_TIME']))
            indices = [i for i, when in enumerate(times) if lo <= when <= hi and not assigned[i]]
            if not indices:
                continue
            p, v = segment.interpolate([times[i] for i in indices])
            position_km[indices], velocity_km_s[indices], assigned[indices] = p, v, True
            provenance['segments'].append({'raw_record_id': record['raw_record_id'],
                                           'from_utc': iso_utc(times[indices[0]]), 'to_utc': iso_utc(times[indices[-1]]),
                                           'frame': 'EME2000', 'interpolation': 'piecewise cubic Hermite, position and velocity'})
        if not assigned.all():
            raise OrbitDataError('Gap between usable OEM segments')
        # EME2000 is mean equator/equinox J2000; apply IAU frame bias, then let
        # Skyfield perform precession, nutation and Earth rotation to ITRS.
        position = Geocentric(ICRS_to_J2000.T @ position_km.T / AU_KM,
                              ICRS_to_J2000.T @ velocity_km_s.T * DAY_S / AU_KM, t=t, center=399)
        epoch, created = None, utc(header['CREATION_DATE'])
        available = utc(record['available_utc']) if record['available_utc'] else None
        fetched = utc(record['fetched_utc'])
        reconstruction = mode == 'history_review'
        source, method, frame = 'nasa_jsc_oem', 'oem_interp', 'EME2000 -> ITRS / WGS84'
        provenance['records'][record['raw_record_id']] = record
        provenance['limitations'].append('OEM timestamps of creation/modification do not prove historical public availability.')
    else:
        path, record = _checked_model(root, 'iss.tle')
        satellite = satellite_from_tle(path.read_bytes())
        epoch = satellite.epoch.utc_datetime()
        if max(abs((start - epoch).total_seconds()), abs((end - epoch).total_seconds())) > max_tle_age_days * 86400:
            raise OrbitDataError('TLE outside declared age limit over the full requested interval; refresh the source')
        position = satellite.at(t)
        if any(message is not None for message in np.atleast_1d(position.message)):
            raise OrbitDataError(f'SGP4 propagation failed: {position.message}')
        available = utc(record['available_utc']) if record.get('available_utc') else None
        fetched = utc(record['fetched_utc'])
        created, reconstruction = None, available is None or available > cutoff
        source, method, frame = 'celestrak_gp', 'sgp4', 'TEME -> ITRS / WGS84'
        raw_id = 'celestrak_gp:25544:' + record['sha256'][:12]
        provenance['records'][raw_id] = dict(record, raw_record_id=raw_id, epoch_utc=iso_utc(epoch))
        provenance['max_tle_age_days'] = max_tle_age_days
        provenance['limitations'].append('TLE age limit is an engineering guard, not a position-error guarantee; manoeuvres are not predicted.')
    geo = wgs84.geographic_position_of(position)
    lat, lon, alt = map(np.atleast_1d, (geo.latitude.degrees, geo.longitude.degrees, geo.elevation.km))
    ecef_km = position.frame_xyz(itrs).km.T
    if not all(np.isfinite(a).all() for a in (lat, lon, alt, ecef_km)):
        raise OrbitDataError('Nonfinite orbital coordinates')
    model = 'IGRF13.shc' if start.year < 2025 else 'IGRF14.shc'
    coeff_path, coeff_record = _checked_model(root, model)
    field = magnetic_coordinates(ecef_km, lon, lat, alt, times, coeff_path)
    provenance['field_model'] = coeff_record
    provenance['records'][coeff_record['raw_record_id']] = coeff_record
    provenance['limitations'].extend([
        'IGRF is the internal main field; no storm-time external field is modelled.',
        'L, B/B0 and vertical cutoff use a centred tilted dipole; not traced McIlwain L or directional storm-time rigidity.',
        'Full IGRF |B| is separate from dipole B/B0; they must not be mixed to infer an IGRF equatorial field.',
        'SAA flag is the configured |B| threshold proxy, not an official region boundary.'])
    points = [TrajectoryPoint(when, float(lat[i]), float(lon[i]), float(alt[i]), float(field['B_nT'][i]),
              float(field['L'][i]) if field['valid'][i] else None,
              float(field['B_over_B0'][i]) if field['valid'][i] else None,
              float(field['cutoff_GV'][i]) if field['valid'][i] else None,
              MagMethod.DIPOLE if field['valid'][i] else MagMethod.NONE,
              'approximation' if field['valid'][i] else 'outside_model',
              bool(field['B_nT'][i] < saa_B_threshold_nT)) for i, when in enumerate(times)]
    meta = TrajectoryMeta(source, method, frame, epoch, start, end, created, available, fetched,
                          reconstruction, 'IGRF-13' if model == 'IGRF13.shc' else 'IGRF-14')
    return meta, points, provenance
