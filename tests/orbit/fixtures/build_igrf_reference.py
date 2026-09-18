"""Optional reference regeneration: requires pyIGRF==0.3.3, not a runtime dependency.

Use identical SHC coefficients with an independent harmonic evaluator. Native
pyIGRF coefficients differ slightly from ppigrf's SHC snapshot; comparing those
bundles conflates evaluator error with coefficient rounding/version differences.
"""
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

import numpy as np
import ppigrf
import pyIGRF

ROOT = Path(__file__).resolve().parents[3]
path = ROOT / 'data/orbit/IGRF13.shc'
G, H = ppigrf.ppigrf.read_shc(path)
epochs = np.array([(t.to_pydatetime() - datetime(1970, 1, 1)).total_seconds() for t in G.index])
original_coefficients = pyIGRF.calculate.get_coeffs
cases = []
for lat, lon, alt, year in [(0,0,400,2024.0),(-30,-50,420,2024.0),(51,20,410,2024.0),
                            (-51,120,450,2024.0),(0,180,400,2024.5),(80,0,0,2024.0)]:
    date = datetime(2024,1,1) + timedelta(days=366*(year-2024))
    time_s = (date - datetime(1970,1,1)).total_seconds()
    native = pyIGRF.igrf_value(lat,lon,alt,year)
    def shared_coefficients(_year):
        g, h = [[None]*(n+1) for n in range(14)], [[None]*(n+1) for n in range(14)]
        for target, table in [(g,G),(h,H)]:
            for (n,m) in table.columns:
                target[n][m] = float(np.interp(time_s,epochs,table[(n,m)].to_numpy()))
        return g,h
    pyIGRF.calculate.get_coeffs = shared_coefficients
    try:
        v=pyIGRF.igrf_value(lat,lon,alt,year)
    finally:
        pyIGRF.calculate.get_coeffs = original_coefficients
    cases.append({'lat_deg':lat,'lon_deg':lon,'alt_km':alt,'decimal_year':year,
        'time_utc':date.isoformat()+'Z','north_nT':float(v[3]),'east_nT':float(v[4]),
        'down_nT':float(v[5]),'B_nT':float(v[6]),'native_coefficient_bundle_B_nT':float(native[6])})
result={'reference':'pyIGRF 0.3.3 independent harmonic synthesis with identical IGRF13.shc inputs',
    'source':'https://github.com/zzyztyy/pyIGRF/tree/v0.3.3',
    'coefficient_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
    'tolerance_nT':1.0,'tolerance_reason':'Independent evaluators, identical coefficients/calendar; geodetic conversion constants differ slightly. Not a bound on physical IGRF error.',
    'original_comparison_note':'Native coefficient bundles differed by up to 2.375 nT in total field. Inputs were aligned, not the acceptance tolerance widened.',
    'cases':cases}
Path(__file__).with_name('igrf13_reference.json').write_text(json.dumps(result,indent=2)+'\n')
