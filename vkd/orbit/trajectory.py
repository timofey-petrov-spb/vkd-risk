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
from . import cutoff_table as zh1
from .magnetic import magnetic_coordinates
from .oem import OrbitDataError, parse_oem, validate_continuous_coverage
from .states import make_state_payload

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


def _cutoff_limitations_ru(info: dict) -> list[str]:
    """Оговорки к жёсткости обрезания — по-русски, теми же словами, что остальные ограничения.

    Печатаются в блоке ограничений модуля орбиты (app/main.py -> app.ui.limit_ru) и попадают
    в выгрузку вместе с provenance. Границы применимости объявляются ВСЕГДА, а не только
    когда таблица применилась: если её нет, сказано, что считал запасной диполь и чем он плох.
    """
    pct = lambda x: ('%+.1f' % x).replace('.', ',')      # запятая как разделитель, как везде на экране
    out = []
    if info.get('n_table'):
        out.append(
            'Вертикальная жёсткость обрезания взята из таблицы Ж.1 ОСТ 134-1044-2007 '
            '(вертикальное обрезание, высота %g км, эпоха %d, сетка 5° по широте и 30° по долготе; '
            'интерполяция билинейная, по долготе замкнута в круг) с пересчётом на высоту точки по '
            'формуле Ж.3 — %d из %d точек. Дипольная формула осталась запасным путём (%d точек: '
            '|широта| > 85° или файл таблицы недоступен); чем посчитана точка, записано в ней самой '
            '(cutoff_kind). Оговорка про центральный наклонный диполь относится к L и B/B0 всегда, '
            'а к обрезанию — только в запасных точках.'
            % (info.get('table_altitude_km', zh1.TABLE_ALTITUDE_KM), info.get('table_epoch_year', zh1.TABLE_EPOCH_YEAR),
               info['n_table'], info['n'], info['n_dipole']))
        out.append(
            'Таблица Ж.1 построена для эпохи %d и высоты %g км, а расчёт идёт на другую эпоху и на '
            'высоту трассы: высота пересчитана по Ж.3 (закон 1/r²), пересчёта эпохи в стандарте нет — '
            'значение в точке остаётся картиной %d года, а поле за прошедшие годы сместилось. '
            'Порядок ошибки от разницы эпох, измеренный нашей же дипольной формулой на широтах МКС '
            'при замене коэффициентов IGRF 2010 на 2024: медиана %s %%, в отдельных точках от '
            '%s до %s %%. Это оценка только дипольной части; дрейф недипольной картины — того '
            'самого, ради чего нужна таблица, — так не оценивается, и числа для него здесь нет. '
            'Для сравнения, расхождение таблицы с симметричным диполем в южном полушарии составляет '
            '70…120 %%, то есть на порядок больше.'
            % (info.get('table_epoch_year', zh1.TABLE_EPOCH_YEAR), info.get('table_altitude_km', zh1.TABLE_ALTITUDE_KM),
               info.get('table_epoch_year', zh1.TABLE_EPOCH_YEAR), pct(zh1.EPOCH_DIPOLE_SHIFT_MEDIAN_PCT),
               pct(zh1.EPOCH_DIPOLE_SHIFT_MIN_PCT), pct(zh1.EPOCH_DIPOLE_SHIFT_MAX_PCT)))
        out.append(
            'Коррекция обрезания по геомагнитной возмущённости Kp и местному времени (Ж.4–Ж.6 ОСТ) '
            'не выполнена: значения соответствуют СПОКОЙНЫМ условиям. Во время бури обрезание '
            'снижается, поэтому в бурю доступность частиц по этой величине занижена, а не завышена.')
        out.append(
            'Сам ОСТ (прил. Ж, примечание) объявляет, что расчёт по вертикальной жёсткости R0c '
            'вместо точного даёт ошибку функции проникновения до ≈10 %; направленная жёсткость '
            'обрезания не считается.')
    else:
        out.append(
            'Таблица Ж.1 ОСТ 134-1044-2007 не применена (%s). Вертикальная жёсткость обрезания '
            'посчитана центральным наклонённым диполем: он симметричен по широте и в южном '
            'полушарии завышает минимум обрезания в 1,7–2,2 раза по сравнению с таблицей.'
            % info.get('table_status', 'причина не записана'))
    return out


def trajectory(start_utc: datetime, minutes: int, saa_B_threshold_nT: float, tle_path=None, **kwargs):
    """B1 entry point: return (TrajectoryMeta, list[TrajectoryPoint]).

    The grid includes both endpoints: minutes=1920 yields 1921 one-minute
    points, covering the complete 24h search + 8h final window. History defaults
    to explicitly labelled OEM reconstruction, never propagation of today's TLE.
    """
    meta, points, _ = trajectory_with_provenance(start_utc, minutes, saa_B_threshold_nT,
                                               tle_path=tle_path, **kwargs)
    return meta, points


def trajectory_with_provenance(start_utc: datetime, minutes: int, saa_B_threshold_nT: float, *,
                               mode: str = 'auto', cutoff_utc: datetime | None = None,
                               repo_root: str | Path = ROOT, step_seconds: int = 60,
                               max_tle_age_days: float = MAX_TLE_AGE_DAYS,
                               tle_path: str | Path | None = None,
                               oem_raw_record_id: str | None = None,
                               expected_record_hashes: dict[str, str] | None = None,
                               include_inertial_states: bool = False):
    """Same solution plus per-source records for the shared export Manifest.

    Optional finer grids serve convergence checks. No implicit network requests,
    coefficient extrapolation, cached global output or silent source fallback.
    """
    start = utc(start_utc)
    if type(include_inertial_states) is not bool:
        raise ValueError('include_inertial_states must be a boolean')
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
    provenance = {'algorithm_version': 'orbit-a3-v2', 'mode': mode, 'step_seconds': step_seconds,
                  'cutoff_utc': iso_utc(cutoff) if mode == 'history_forecast' else None,
                  'records': {}, 'segments': [], 'limitations': [],
                  'output_frame': 'ITRS / WGS84 geodetic latitude, longitude, altitude',
                  'earth_orientation': 'Skyfield bundled UT1/leap seconds, no external polar-motion table; not centimetre-level geodesy'}
    if mode in ('history_review', 'history_forecast'):
        registry = SourceRegistry(root)
        candidates = []
        for record in registry.records('nasa_jsc_oem'):
            if oem_raw_record_id is not None and record['raw_record_id'] != oem_raw_record_id:
                continue
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
        validate_continuous_coverage(segments, start, end)
        position_km, velocity_km_s = np.empty((len(times), 3)), np.empty((len(times), 3))
        assigned = np.zeros(len(times), dtype=bool)
        state_assignments = np.full(len(times), -1, dtype=int)
        for segment in segments:
            lo = utc(segment.metadata.get('USEABLE_START_TIME', segment.metadata['START_TIME']))
            hi = utc(segment.metadata.get('USEABLE_STOP_TIME', segment.metadata['STOP_TIME']))
            indices = [i for i, when in enumerate(times) if lo <= when <= hi and not assigned[i]]
            if not indices:
                continue
            p, v = segment.interpolate([times[i] for i in indices])
            position_km[indices], velocity_km_s[indices], assigned[indices] = p, v, True
            state_assignments[indices] = len(provenance['segments'])
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
        source, method, frame = 'nasa_jsc_oem', 'oem_interp', 'EME2000'
        provenance['records'][record['raw_record_id']] = record
        provenance['limitations'].append('OEM timestamps of creation/modification do not prove historical public availability.')
        if tle_path is not None:
            provenance['limitations'].append('B1 tle_path applies to live mode only; historical orbit uses the identified OEM, not current TLE.')
    else:
        if tle_path is None:
            path, record = _checked_model(root, 'iss.tle')
        else:
            path = Path(tle_path)
            raw = path.read_bytes()
            fetched = datetime.now(timezone.utc)
            sidecar = Path(str(path) + '.meta.json')
            if sidecar.exists():
                fetched = utc(json.loads(sidecar.read_text())['fetched_utc'])
            record = {'source_id': 'celestrak_gp', 'raw_path': str(path),
                      'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                      'fetched_utc': iso_utc(fetched), 'available_utc': None,
                      'evidence': 'Caller-supplied TLE bytes; checksum/identity/epoch verified. Historical publication is not established.'}
            record['release_id'] = record['sha256']
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != record['sha256'] or len(raw) != record['bytes']:
            raise OrbitDataError('TLE bytes changed before propagation; input hash no longer matches')
        satellite = satellite_from_tle(raw)
        epoch = satellite.epoch.utc_datetime()
        if max(abs((start - epoch).total_seconds()), abs((end - epoch).total_seconds())) > max_tle_age_days * 86400:
            raise OrbitDataError('TLE outside declared age limit over the full requested interval; refresh the source')
        position = satellite.at(t)
        if any(message is not None for message in np.atleast_1d(position.message)):
            raise OrbitDataError(f'SGP4 propagation failed: {position.message}')
        available = utc(record['available_utc']) if record.get('available_utc') else None
        fetched = utc(record['fetched_utc'])
        created, reconstruction = None, available is None or available > cutoff
        source, method, frame = 'celestrak_gp', 'sgp4', 'TEME'
        raw_id = 'celestrak_gp:25544:' + record['sha256'][:12]
        provenance['records'][raw_id] = dict(record, raw_record_id=raw_id, epoch_utc=iso_utc(epoch))
        if include_inertial_states:
            position_km = (ICRS_to_J2000 @ position.position.km).T
            velocity_km_s = (ICRS_to_J2000 @ position.velocity.km_per_s).T
            state_assignments = np.zeros(len(times), dtype=int)
        provenance['max_tle_age_days'] = max_tle_age_days
        provenance['limitations'].append('TLE age limit is an engineering guard, not a position-error guarantee; manoeuvres are not predicted.')
    geo = wgs84.geographic_position_of(position)
    lat, lon, alt = map(np.atleast_1d, (geo.latitude.degrees, geo.longitude.degrees, geo.elevation.km))
    ecef_km = position.frame_xyz(itrs).km.T
    if not all(np.isfinite(a).all() for a in (lat, lon, alt, ecef_km)):
        raise OrbitDataError('Nonfinite orbital coordinates')
    model = 'IGRF13.shc' if start.year < 2025 else 'IGRF14.shc'
    coeff_path, coeff_record = _checked_model(root, model)
    field = magnetic_coordinates(ecef_km, lon, lat, alt, times, coeff_path,
                                 cutoff_table_path=root / zh1.TABLE_RELATIVE_PATH)
    provenance['field_model'] = coeff_record
    provenance['cutoff_model'] = field['cutoff_info']
    provenance['records'][coeff_record['raw_record_id']] = coeff_record
    if expected_record_hashes is not None:
        actual = {rid: record['sha256'] for rid, record in provenance['records'].items()}
        if actual != expected_record_hashes:
            raise OrbitDataError('Replay orbital input records/hashes differ from the saved calculation')
    provenance['limitations'].extend([
        'IGRF is the internal main field; no storm-time external field is modelled.',
        # Круг 11: прежняя строка называла диполем и обрезание тоже. Когда таблица Ж.1 применена,
        # это уже неправда, и на экране она вставала рядом с оговоркой про таблицу — два
        # противоположных утверждения об одной величине. Русский текст экран печатает как есть.
        ('L и B/B_0 — центральный наклонный диполь, не трассированная L Мак-Илвейна; '
         'вертикальное обрезание считается отдельно (строки ниже), направленная жёсткость '
         'обрезания во время бури не считается.') if field['cutoff_info'].get('n_table') else
        'L, B/B0 and vertical cutoff use a centred tilted dipole; not traced McIlwain L or directional storm-time rigidity.',
        'Full IGRF |B| is separate from dipole B/B0; they must not be mixed to infer an IGRF equatorial field.',
        'SAA flag is the configured |B| threshold proxy, not an official region boundary.'])
    provenance['limitations'].extend(_cutoff_limitations_ru(field['cutoff_info']))
    points = [TrajectoryPoint(when, float(lat[i]), float(lon[i]), float(alt[i]), float(field['B_nT'][i]),
              float(field['L'][i]) if field['valid'][i] else None,
              float(field['B_over_B0'][i]) if field['valid'][i] else None,
              float(field['cutoff_GV'][i]) if field['cutoff_valid'][i] else None,
              MagMethod.DIPOLE if field['valid'][i] else MagMethod.NONE,
              'approximation' if field['valid'][i] else 'outside_model',
              bool(field['B_nT'][i] < saa_B_threshold_nT),
              str(field['cutoff_source'][i])) for i, when in enumerate(times)]
    meta = TrajectoryMeta(source, method, frame, epoch, start, end, created, available, fetched,
                          reconstruction, 'IGRF-13' if model == 'IGRF13.shc' else 'IGRF-14')
    if include_inertial_states:
        state_segments = provenance['segments'] if method == 'oem_interp' else [
            {'raw_record_id': raw_id, 'from_utc': iso_utc(start), 'to_utc': iso_utc(end),
             'frame': 'TEME', 'interpolation': 'none; SGP4 at each requested time'}]
        provenance['inertial_states'] = make_state_payload(times, position_km, velocity_km_s,
            state_assignments, state_segments, provenance['records'], ts, method, reconstruction)
    return meta, points, provenance
