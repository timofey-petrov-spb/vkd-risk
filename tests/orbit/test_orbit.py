from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
import tempfile

import numpy as np
from skyfield.api import load, wgs84
from skyfield.framelib import itrs
from skyfield.positionlib import Geocentric
from skyfield.constants import AU_KM

from vkd.orbit import trajectory, trajectory_with_provenance, OrbitDataError
from vkd.orbit.trajectory import satellite_from_tle
from vkd.orbit.oem import OemSegment, parse_oem
from vkd.orbit.magnetic import magnetic_coordinates
from vkd.sources.registry import utc

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / 'fixtures'


class Sgp4Tests(unittest.TestCase):
    def test_published_vallado_near_earth_and_deep_space_vectors(self):
        lines = (FIXTURES / 'SGP4-VER.TLE').read_text().splitlines()
        satellites = {}
        for i, line in enumerate(lines):
            if line.startswith('1 ') and int(line[2:7]) in (5, 4632):
                satellites[int(line[2:7])] = satellite_from_tle(
                    (line[:69] + '\n' + lines[i + 1][:69]).encode(), expected_norad=None)
        satellite = None
        tested = 0
        for line in (FIXTURES / 'tcppver.out').read_text().splitlines():
            if line.endswith('xx'):
                satellite = satellites.get(int(line.split()[0])); continue
            if satellite is None or not line.strip():
                continue
            fields = list(map(float, line.split()[:7]))
            minutes, expected_position, expected_velocity = fields[0], fields[1:4], fields[4:7]
            error, position, velocity = satellite.model.sgp4_tsince(minutes)
            self.assertEqual(error, 0)
            np.testing.assert_allclose(position, expected_position, rtol=0, atol=1e-7)
            np.testing.assert_allclose(velocity, expected_velocity, rtol=0, atol=1e-8)
            tested += 1
        self.assertGreaterEqual(tested, 15)

    def test_checksum_and_identity(self):
        raw = (ROOT / 'data/orbit/iss.tle').read_bytes()
        with self.assertRaises(OrbitDataError):
            satellite_from_tle(raw.replace(b'25544', b'25545', 1))
        with self.assertRaises(OrbitDataError):
            satellite_from_tle(raw, expected_norad=12345)


class OemTests(unittest.TestCase):
    def test_all_26_archived_oem_and_exact_knots(self):
        paths = list((ROOT / 'data/source_registry_2024/nasa_oem/raw').glob('*.txt'))
        self.assertEqual(len(paths), 26)
        for path in paths:
            _, segments = parse_oem(path.read_bytes())
            for segment in segments:
                with self.subTest(file=path.name):
                    indices = [0, len(segment.times_s)//2, len(segment.times_s)-1]
                    ts = [datetime.fromtimestamp(segment.times_s[i], timezone.utc) for i in indices]
                    p,v = segment.interpolate(ts)
                    np.testing.assert_allclose(p, segment.position_km[indices], atol=1e-8, rtol=0)
                    np.testing.assert_allclose(v, segment.velocity_km_s[indices], atol=1e-10, rtol=0)

    def test_circular_orbit_analytic_interpolation_error(self):
        omega = np.sqrt(398600.4418 / 6800**3)
        ts = np.arange(0, 961, 240, dtype=float)
        p = np.column_stack((6800*np.cos(omega*ts),6800*np.sin(omega*ts),np.zeros(len(ts))))
        v = np.column_stack((-6800*omega*np.sin(omega*ts),6800*omega*np.cos(omega*ts),np.zeros(len(ts))))
        start = utc('2024-05-01T00:00Z')
        segment = OemSegment({'START_TIME':start.isoformat(),'STOP_TIME':(start+timedelta(seconds=960)).isoformat()},
                             ts+start.timestamp(),p,v)
        tmid = np.array([120,360,600,840])
        result,_ = segment.interpolate([start+timedelta(seconds=int(s)) for s in tmid])
        exact = np.column_stack((6800*np.cos(omega*tmid),6800*np.sin(omega*tmid),np.zeros(4)))
        self.assertLess(np.max(np.linalg.norm(result-exact,axis=1)),0.1)  # 100 m, a priori cubic bound
        with self.assertRaises(OrbitDataError):segment.interpolate([start-timedelta(seconds=1)])
        with self.assertRaises(OrbitDataError):segment.interpolate([start+timedelta(seconds=961)])

    def test_wrong_frame_and_time_system_rejected(self):
        raw = next((ROOT / 'data/source_registry_2024/nasa_oem/raw').glob('*.txt')).read_bytes()
        for changed in (raw.replace(b'EME2000', b'TEME'),raw.replace(b'= UTC',b'= TAI')):
            with self.assertRaises(OrbitDataError):parse_oem(changed)


class MagneticTests(unittest.TestCase):
    def test_independent_igrf13_reference(self):
        reference = json.loads((FIXTURES/'igrf13_reference.json').read_text())
        ts = load.timescale(builtin=True)
        for case in reference['cases']:
            when=utc(case['time_utc'])
            location=wgs84.latlon(case['lat_deg'],case['lon_deg'],case['alt_km']*1000)
            ecef=location.at(ts.from_datetime(when)).frame_xyz(itrs).km.reshape(1,3)
            f=magnetic_coordinates(ecef,np.array([case['lon_deg']]),np.array([case['lat_deg']]),
                np.array([case['alt_km']]),[when],ROOT/'data/orbit/IGRF13.shc')
            with self.subTest(case=case):
                self.assertLess(abs(f['B_nT'][0]-case['B_nT']),reference['tolerance_nT'])
                self.assertGreaterEqual(f['B_over_B0'][0],1)

    def test_field_at_each_time_and_no_model_extrapolation(self):
        import ppigrf
        times=[utc('2024-05-01T00:00Z'),utc('2024-05-02T08:00Z')]
        lon=np.array([0.,-50.]);lat=np.array([0.,-30.]);alt=np.array([400.,420.])
        ts=load.timescale(builtin=True)
        ecef=np.array([wgs84.latlon(a,b,h*1000).at(ts.from_datetime(t)).frame_xyz(itrs).km
                       for a,b,h,t in zip(lat,lon,alt,times)])
        f=magnetic_coordinates(ecef,lon,lat,alt,times,ROOT/'data/orbit/IGRF13.shc')
        for i,t in enumerate(times):
            e,n,u=ppigrf.igrf(lon[i],lat[i],alt[i],t.replace(tzinfo=None),coeff_fn=str(ROOT/'data/orbit/IGRF13.shc'))
            self.assertAlmostEqual(f['B_nT'][i],float(np.sqrt(e*e+n*n+u*u).item()),places=7)
        with self.assertRaises(OrbitDataError):
            magnetic_coordinates(ecef,lon,lat,alt,[utc('2026-05-01T00:00Z')]*2,ROOT/'data/orbit/IGRF13.shc')


class TrajectoryTests(unittest.TestCase):
    def test_pinned_oem_replay_and_changed_input_refusal(self):
        t = utc('2024-05-03T12:00Z')
        meta, points, proof = trajectory_with_provenance(t, 60, 24000)
        oem_id = next(rid for rid, r in proof['records'].items() if r['source_id'] == 'nasa_jsc_oem')
        hashes = {rid: r['sha256'] for rid, r in proof['records'].items()}
        replay_meta, replay = trajectory(t, 60, 24000, oem_raw_record_id=oem_id,
                                        expected_record_hashes=hashes)
        self.assertEqual(meta, replay_meta)
        self.assertEqual(points, replay)
        with self.assertRaisesRegex(OrbitDataError, 'hashes differ'):
            trajectory(t, 60, 24000, oem_raw_record_id=oem_id,
                       expected_record_hashes={**hashes, oem_id: '0'*64})
        with self.assertRaises(OrbitDataError):
            trajectory(t, 60, 24000, oem_raw_record_id='missing release')

    def test_colleague_tle_path_interface_and_validation(self):
        path = ROOT / 'data/orbit/iss.tle'
        epoch = satellite_from_tle(path.read_bytes()).epoch.utc_datetime()
        meta, points, proof = trajectory_with_provenance(
            epoch + timedelta(days=1), 60, 24000, tle_path=path)
        self.assertEqual(meta.method, 'sgp4')
        self.assertEqual(meta.frame, 'TEME')
        self.assertEqual(len(points), 61)
        self.assertTrue(any(r.get('raw_path') == str(path) for r in proof['records'].values()))
        with tempfile.TemporaryDirectory() as directory:
            bad = Path(directory) / 'invalid.tle'
            bad.write_bytes(path.read_bytes().replace(b'25544', b'25545', 1))
            with self.assertRaises(OrbitDataError):
                trajectory(epoch, 60, 24000, tle_path=bad)
        # A legacy B1 TLE override must never drag a 2024 orbit into today's epoch.
        historical, _ = trajectory(utc('2024-05-03T12:00Z'), 60, 24000, tle_path=path)
        self.assertEqual(historical.method, 'oem_interp')
        self.assertEqual(historical.frame, 'EME2000')

    def test_six_dates_full_32h_horizon_and_provenance(self):
        for date in ['2024-05-01T00:00Z','2024-05-03T12:00Z','2024-05-20T12:00Z',
                     '2024-06-01T12:00Z','2024-06-25T12:00Z','2024-06-30T12:00Z']:
            start=utc(date)
            meta,points,proof=trajectory_with_provenance(start,1920,24000)
            with self.subTest(date=date):
                self.assertEqual(len(points),1921)
                self.assertEqual(points[-1].t_utc,start+timedelta(hours=32))
                self.assertEqual(meta.coverage_to_utc,points[-1].t_utc)
                self.assertEqual(meta.field_model,'IGRF-13')
                self.assertTrue(meta.is_reconstruction)
                self.assertIsNone(meta.available_utc)
                self.assertTrue(proof['records'])
                self.assertTrue(all(350<p.alt_km<500 and abs(p.lat_deg)<53 for p in points))
                self.assertTrue(all(p.B_over_B0>=1 and p.cutoff_GV>0 and p.mag_status=='approximation' for p in points))
                self.assertTrue(all(p.in_saa==(p.B_nT<24000) for p in points))

    def test_current_sgp4_and_three_argument_b1_interface(self):
        raw=(ROOT/'data/orbit/iss.tle').read_bytes()
        epoch=satellite_from_tle(raw).epoch.utc_datetime()
        meta,points=trajectory(epoch+timedelta(days=1),1920,24000)
        self.assertEqual(meta.method,'sgp4')
        self.assertEqual(len(points),1921)
        self.assertEqual(meta.field_model,'IGRF-14')
        with self.assertRaises(OrbitDataError):trajectory(epoch+timedelta(days=10),60,24000)

    def test_strict_unknown_publication_never_falls_back_to_tle(self):
        t=utc('2024-05-03T12:00Z')
        with self.assertRaisesRegex(OrbitDataError,'historical availability'):
            trajectory(t,60,24000,mode='history_forecast',cutoff_utc=t)
        with self.assertRaises(ValueError):trajectory(t,60,24000,mode='history_forecast')

    def test_inputs_and_grid_endpoint(self):
        t=utc('2024-05-03T12:00Z')
        for minutes in [0,-1,1921,1.5,True]:
            with self.assertRaises(ValueError):trajectory(t,minutes,24000)
        with self.assertRaises(ValueError):trajectory(datetime(2024,5,3),60,24000)
        with self.assertRaises(ValueError):trajectory(t,60,float('nan'))
        _,p=trajectory(t,1,24000,step_seconds=40)
        self.assertEqual([int((x.t_utc-t).total_seconds()) for x in p],[0,40,60])

    def test_finer_grid_agrees_at_common_times(self):
        t=utc('2024-05-03T12:00Z')
        _,coarse=trajectory(t,180,24000)
        _,fine=trajectory(t,180,24000,step_seconds=30)
        for a,b in zip(coarse,fine[::2]):
            self.assertEqual(a.t_utc,b.t_utc)
            self.assertAlmostEqual(a.lat_deg,b.lat_deg,places=9)
            self.assertAlmostEqual(a.B_nT,b.B_nT,places=7)
