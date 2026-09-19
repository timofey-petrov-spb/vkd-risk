from copy import deepcopy
from datetime import timedelta, datetime, timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
from skyfield.api import load, wgs84
from skyfield.constants import AU_KM, DAY_S
from skyfield.framelib import ICRS_to_J2000
from skyfield.positionlib import Geocentric

from vkd.orbit import trajectory_with_provenance, OrbitDataError
from vkd.orbit.oem import OemSegment, parse_oem, validate_continuous_coverage
from vkd.sources.registry import SourceRegistry, utc

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent/'fixtures'


class InertialTests(unittest.TestCase):
    def test_independent_erfa_teme_to_j2000_position_and_velocity(self):
        ref = json.loads((FIXTURES/'inertial_reference.json').read_text())
        self.assertEqual(hashlib.sha256((ROOT/'data/orbit/iss.tle').read_bytes()).hexdigest(), ref['tle_sha256'])
        start = utc(ref['cases'][0]['t_utc'])
        meta,_,proof = trajectory_with_provenance(start,1920,24000,mode='live',include_inertial_states=True)
        states = proof['inertial_states']
        self.assertEqual(meta.frame,'TEME')  # Legacy meta describes INPUT frame.
        self.assertEqual(states['frame'],'EME2000')
        self.assertEqual(states['segments'][0]['input_frame'],'TEME')
        self.assertEqual(states['segments'][0]['output_frame'],'EME2000')
        indexed = {s['t_utc']:s for s in states['samples']}
        for case in ref['cases']:
            with self.subTest(t=case['t_utc']):
                row = indexed[case['t_utc']]
                np.testing.assert_allclose(row['position_km'],case['position_km'],rtol=0,atol=ref['position_tolerance_km'])
                np.testing.assert_allclose(row['velocity_km_s'],case['velocity_km_s'],rtol=0,atol=ref['velocity_tolerance_km_s'])
                self.assertGreater(np.linalg.norm(np.array(row['position_km'])-case['native_teme_position_km']),1)

    def test_oem_vectors_at_original_knot_and_analytic_derivative_between_knots(self):
        registry = SourceRegistry(ROOT)
        record = registry.records('nasa_jsc_oem')[0]
        _,segments = parse_oem(registry.raw_bytes(record['raw_record_id']))
        segment = segments[0]
        i = len(segment.times_s)//2
        start = datetime.fromtimestamp(segment.times_s[i],timezone.utc)
        _,_,proof = trajectory_with_provenance(start,4,24000,mode='history_review',
            oem_raw_record_id=record['raw_record_id'],include_inertial_states=True)
        states = proof['inertial_states']['samples']
        np.testing.assert_allclose(states[0]['position_km'],segment.position_km[i],rtol=0,atol=1e-9)
        np.testing.assert_allclose(states[0]['velocity_km_s'],segment.velocity_km_s[i],rtol=0,atol=1e-11)
        # Independent closed-form derivative of a cubic Hermite polynomial.
        dt = segment.times_s[i+1]-segment.times_s[i]
        u = 60/dt
        p0,p1 = segment.position_km[i:i+2];v0,v1 = segment.velocity_km_s[i:i+2]
        expected = (6*u*u-6*u)*p0/dt + (3*u*u-4*u+1)*v0 + (-6*u*u+6*u)*p1/dt + (3*u*u-2*u)*v1
        np.testing.assert_allclose(states[1]['velocity_km_s'],expected,rtol=0,atol=1e-10)

    def test_same_states_and_geographic_points_for_three_dates_full_horizon(self):
        ts = load.timescale(builtin=True)
        for date in ['2024-05-01T00:00Z','2024-05-20T12:00Z','2024-06-30T12:00Z']:
            start = utc(date)
            meta,points,proof = trajectory_with_provenance(start,1920,24000,include_inertial_states=True)
            payload = proof['inertial_states']
            rows = payload['samples']
            self.assertEqual(len(rows),1921)
            self.assertTrue(payload['is_reconstruction'])
            self.assertIsNone(meta.available_utc)
            for i in [0,731,1920]:
                row,point = rows[i],points[i]
                self.assertEqual(utc(row['t_utc']),point.t_utc)
                self.assertEqual(payload['source_hashes'][row['raw_record_id']],proof['records'][row['raw_record_id']]['sha256'])
                p = ICRS_to_J2000.T@row['position_km']/AU_KM
                v = ICRS_to_J2000.T@row['velocity_km_s']*DAY_S/AU_KM
                geo = wgs84.geographic_position_of(Geocentric(p,v,t=ts.from_datetime(point.t_utc),center=399))
                self.assertAlmostEqual(geo.latitude.degrees,point.lat_deg,places=9)
                self.assertAlmostEqual(geo.longitude.degrees,point.lon_deg,places=9)
                self.assertAlmostEqual(geo.elevation.km,point.alt_km,places=8)
                self.assertTrue(5 < np.linalg.norm(row['velocity_km_s']) < 10)

    def test_exact_endpoints_canonical_hash_replay_and_independence(self):
        start = utc('2024-05-03T12:00Z')
        meta,points,proof = trajectory_with_provenance(start,1,24000,step_seconds=40,include_inertial_states=True)
        payload = proof['inertial_states']
        self.assertEqual([(utc(s['t_utc'])-start).total_seconds() for s in payload['samples']],[0,40,60])
        canonical = deepcopy(payload); expected = canonical.pop('content_sha256')
        actual = hashlib.sha256(json.dumps(canonical,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        self.assertEqual(actual,expected)
        record_id = payload['samples'][0]['raw_record_id']
        hashes = {rid:r['sha256'] for rid,r in proof['records'].items()}
        meta2,points2,proof2 = trajectory_with_provenance(start,1,24000,step_seconds=40,
            include_inertial_states=True,oem_raw_record_id=record_id,expected_record_hashes=hashes)
        self.assertEqual(meta,meta2);self.assertEqual(points,points2);self.assertEqual(proof,proof2)
        payload['samples'][0]['position_km'][0]=0
        self.assertNotEqual(payload,proof2['inertial_states'])

    def test_opt_in_does_not_change_legacy_points_or_historical_eligibility(self):
        t=utc('2024-05-03T12:00Z')
        m,p,proof=trajectory_with_provenance(t,60,24000)
        n,q,with_states=trajectory_with_provenance(t,60,24000,include_inertial_states=True)
        self.assertEqual((m,p),(n,q))
        self.assertNotIn('inertial_states',proof)
        for flag in ['yes',1,None]:
            with self.assertRaises(ValueError):
                trajectory_with_provenance(t,60,24000,include_inertial_states=flag)
        with self.assertRaisesRegex(OrbitDataError,'historical availability'):
            trajectory_with_provenance(t,60,24000,mode='history_forecast',cutoff_utc=t,include_inertial_states=True)

    def test_coarse_grid_cannot_hide_gaps_in_oem_support(self):
        t=utc('2024-05-03T12:00Z')
        def segment(offsets):
            return OemSegment({'START_TIME':t.isoformat(),'STOP_TIME':(t+timedelta(seconds=600)).isoformat()},
                np.array(offsets)+t.timestamp(),np.zeros((len(offsets),3)),np.zeros((len(offsets),3)))
        # Nodes at 0 and 600 would both exist, but their missing interpolation
        # support cannot become 100% coverage merely by requesting a 600s step.
        for segments in [[segment([0,600])],[segment([0,240]),segment([360,600])]]:
            with self.assertRaises(OrbitDataError):
                validate_continuous_coverage(segments,t,t+timedelta(seconds=600))
        validate_continuous_coverage([segment([0,240,480,600])],t,t+timedelta(seconds=600))

    def test_consumed_tle_bytes_must_match_the_verified_receipt(self):
        path = ROOT/'data/orbit/iss.tle'
        old_record = {'sha256':'0'*64,'bytes':len(path.read_bytes())}
        with patch('vkd.orbit.trajectory._checked_model',return_value=(path,old_record)):
            with self.assertRaisesRegex(OrbitDataError,'bytes changed'):
                trajectory_with_provenance(utc('2026-09-18T12:00Z'),60,24000,
                    mode='live',include_inertial_states=True)


if __name__ == '__main__':
    unittest.main()
