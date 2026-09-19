"""Independent ERFA frame conversion; no Skyfield or application imports.

Optional developer dependency pyerfa==2.0.1.5, not needed to run tests/app.
Pinned DUT1 inputs are from Skyfield's bundled timescale; this cross-checks
frame mathematics given the same Earth orientation, not Earth orientation data.
SGP4 itself is checked separately against published Vallado vectors.
"""
from datetime import datetime
import hashlib
from importlib.metadata import version
import json
from pathlib import Path

import erfa
import numpy as np
from sgp4.api import Satrec, WGS72

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
# UTC and UT1-UTC seconds. No live IERS tables or downloads.
CASES = [
    ('2026-09-18T03:25:38.782288Z', 0.09569869645346785),
    ('2026-09-18T03:26:38.782288Z', 0.09569895902289716),
    ('2026-09-18T03:55:38.782288Z', 0.09570657353685874),
    ('2026-09-18T04:25:38.782288Z', 0.0957144506200791),
    ('2026-09-19T03:25:38.782288Z', 0.09607679645347389),
    ('2026-09-19T11:25:38.782288Z', 0.09620282978686134),
]


def build():
    raw = (ROOT/'data/orbit/iss.tle').read_bytes()
    lines = raw.decode('ascii').splitlines()
    sat = Satrec.twoline2rv(lines[-2], lines[-1], WGS72)
    cases = []
    for iso, dut1 in CASES:
        dt = datetime.fromisoformat(iso)
        u1,u2 = erfa.dtf2d('UTC',dt.year,dt.month,dt.day,dt.hour,dt.minute,dt.second+dt.microsecond/1e6)
        a1,a2 = erfa.utctai(u1,u2)
        tt1,tt2 = erfa.taitt(a1,a2)
        ut1,ut2 = erfa.utcut1(u1,u2,dut1)
        error,r,v = sat.sgp4(u1,u2)
        if error:
            raise ValueError(f'SGP4 error {error}')
        angle = erfa.gmst82(ut1,ut2) - erfa.gst06a(ut1,ut2,tt1,tt2)
        c,s = np.cos(-angle),np.sin(-angle)
        z = np.array([[c,-s,0],[s,c,0],[0,0,1]])
        rotation = erfa.bp00(2451545.,0.)[0] @ erfa.pnm06a(tt1,tt2).T @ z
        cases.append({'t_utc':iso, 'dut1_seconds':dut1, 'native_teme_position_km':list(r),
            'native_teme_velocity_km_s':list(v), 'position_km':(rotation@r).tolist(),
            'velocity_km_s':(rotation@v).tolist()})
    return {'method':'ERFA IAU2006/2000A, GMST82, IAU2000 frame bias; instantaneous velocity rotation',
        'sources':['https://github.com/liberfa/erfa/blob/master/src/gst06a.c',
                   'https://www.iausofa.org/current-software'],
        'software':{name:version(name) for name in ('pyerfa','sgp4','numpy')},
        'tle_sha256':hashlib.sha256(raw).hexdigest(), 'frame':'EME2000',
        'position_tolerance_km':1e-5, 'velocity_tolerance_km_s':1e-8,
        'tolerance_reason':'1 cm and 0.01 mm/s: catches frame/unit/sign mistakes, allows small IAU series differences; not orbit accuracy.',
        'cases':cases}


if __name__ == '__main__':
    (HERE/'inertial_reference.json').write_text(json.dumps(build(),indent=2,allow_nan=False)+'\n')
