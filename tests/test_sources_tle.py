# -*- coding: utf-8 -*-
"""Резервная цепочка TLE (Т6): ответы разных адресов приводятся к одному виду без сети;
CelesTrak 19.09.2026 не отвечал — без резервов текущий режим терял орбиту."""
import json
import os

from experiments.legacy.stub_sources import tle_from_text
from vkd.orbit.trajectory import satellite_from_tle

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = open(os.path.join(ROOT, 'data', 'orbit', 'iss.tle'), encoding='utf-8').read()
L1, L2 = [l for l in RAW.splitlines() if l.startswith(('1 ', '2 '))]


def _ok(txt):
    assert txt is not None
    sat = satellite_from_tle(txt.encode('ascii'))
    assert sat.model.satnum == 25544


def test_celestrak_text_two_or_three_lines():
    _ok(tle_from_text(RAW))
    _ok(tle_from_text(L1 + '\n' + L2 + '\n'))
    assert tle_from_text(RAW).splitlines()[0].strip() == 'ISS (ZARYA)'


def test_json_variants_wheretheiss_and_ivanstanojevic():
    w = json.dumps({'requested_timestamp': 1, 'tle_timestamp': 2, 'id': '25544', 'name': 'iss',
                    'header': 'ISS (ZARYA)', 'line1': L1, 'line2': L2})
    i = json.dumps({'@context': 'x', '@id': 'y', 'satelliteId': 25544, 'name': 'ISS (ZARYA)', 'date': 'z',
                    'line1': L1, 'line2': L2})
    for body in (w, i, '[' + i + ']'):
        _ok(tle_from_text(body))


def test_station_list_picks_iss_block_and_garbage_is_rejected():
    other1 = L1.replace('25544', '99999')
    other2 = L2.replace('25544', '99999')
    lst = 'OTHER\n' + other1 + '\n' + other2 + '\nISS (ZARYA)\n' + L1 + '\n' + L2 + '\n'
    assert tle_from_text(lst).splitlines()[1] == L1
    assert tle_from_text('<html>503</html>') is None
    assert tle_from_text('{"error": "rate limit"}') is None
    assert tle_from_text('') is None and tle_from_text(None) is None
