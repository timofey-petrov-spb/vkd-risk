"""Cross-team regressions: actual archives, portable evidence and strict coverage."""
import base64
import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest

from app.compute import run
from app.export import build_zip
from tests.sources.test_live import NOW, ROOT, Response, get_goes
from tests.test_compare import T0, traj
from vkd.sources import kp_latest, tle_latest
from vkd.types import Coverage, EnvironmentSample, EventInterval, Kind, Window
from vkd.windows.compare import Thresholds, assess_window
from vkd.windows.scenario import Scenario
from vkd.assess.trapped import BeltTable


def test_live_archive_restores_ids_bytes_and_result_without_network(tmp_path, monkeypatch):
    from scripts.replay_example import load_snapshot, saved_sources
    kp = json.dumps(dict(datetime=['2026-09-18T18:00:00Z'], Kp=[1.333], status=['pre'])).encode()
    fetched = (get_goes(tmp_path),
               kp_latest(cache_dir=tmp_path, now=NOW, transport=Mock(return_value=Response(kp))),
               tle_latest(cache_dir=tmp_path, now=NOW, transport=Mock(return_value=Response(
                   (ROOT/'data/orbit/iss.tle').read_bytes())), use_bundled=False))
    a = run('live', NOW, 60, 60, [0, 60], fetched=fetched, now=NOW)
    dest = tmp_path/'snapshot.zip'
    dest.write_bytes(build_zip(a.S, a.raw_records))
    saved = load_snapshot(str(dest))
    assert set(saved['raw']) == set(a.raw_records)
    never = Mock(side_effect=AssertionError('offline replay attempted network'))
    monkeypatch.setattr('requests.sessions.Session.request', never)
    restored = saved_sources(saved, NOW)
    b = run('live', NOW, 60, 60, [0, 60], fetched=restored, now=NOW)
    assert a.S['windows'] == b.S['windows']
    assert a.S['recommendation'] == b.S['recommendation']
    assert a.S['source_versions']
    for sid, records in a.S['source_versions'].items():
        for rid, meta in records.items():
            raw = base64.b64decode(a.raw_records[rid]['content_base64'])
            assert hashlib.sha256(raw).hexdigest() == meta['sha256']
    for w in a.S['windows']:
        for m in w['mechanisms']:
            for f in m['factors']:
                assert set(f['records']) <= set(a.raw_records)
    corrupted = deepcopy(saved)
    rid = fetched[0][0].raw_record_id
    corrupted['raw'][rid]['content_base64'] = base64.b64encode(b'corrupt').decode()
    with pytest.raises(ValueError, match='SHA-256'):
        saved_sources(corrupted, NOW)
    never.assert_not_called()


def test_maximum_delay_covers_both_windows_and_keeps_original_cutoff():
    r = run('history_review', T0, 480, 1440, [0, 1440], now=T0,
            scenario=Scenario('delay', work_delay_min=180))
    assert len(r.traj) == 1921
    assert r.traj[0].t_utc == T0+timedelta(minutes=180)
    assert r.traj[-1].t_utc == T0+timedelta(minutes=2100)
    proof = r.raw_records['orbit_provenance']
    assert proof['selection_cutoff_utc'].replace('Z', '+00:00') == T0.isoformat()
    assert len(proof['inertial_states']['samples']) == 1921
    assert all(a.window.start_utc+timedelta(minutes=480) <= r.traj[-1].t_utc for a in r.assessments)


def test_historical_numeric_goes_and_explicit_off():
    a = run('history_review', T0, 60, 60, [0, 60], now=T0)
    b = run('history_review', T0, 60, 60, [0, 60], now=T0, disabled={'goes': 'off'})
    assert a.S['history']['goes_observations']
    assert not b.S['history']['goes_observations']
    assert 'nasa_iswa_goes_primary_p5m' in a.S['source_versions']
    assert 'nasa_iswa_goes_primary_p5m' not in b.S['source_versions']
    assert b.rec.verdict == 'insufficient'
    for key, value in b.S['coverage_map'].items():
        if key.startswith('goes_'):
            assert value['coverage_fraction'] == 0


def test_goes_cells_do_not_fill_gaps_or_apply_100mev_to_s_scale():
    cell = EnvironmentSample(T0, 'goes_p_ge10MeV', 11, 'pfu', 'archive', Kind.OBSERVATION,
        None, T0, T0+timedelta(minutes=5), T0, 'unknown', 'cell')
    event = EventInterval(event_id='sep100', kind_of_event='SEP', kind=Kind.OBSERVATION,
        start_utc=T0, end_utc=T0+timedelta(hours=1), start_uncertain=False, end_uncertain=False,
        valid_from_utc=T0, valid_to_utc=T0+timedelta(hours=1), source_id='archive',
        published_utc=T0, raw_record_id='sep100', note='pfu=10000')
    a = assess_window(Window(T0, 60), traj(61, lambda i: False), BeltTable('min'), None,
        None, [], Thresholds(), T0, goes_observations=[cell], events=[event],
        event_facts={'sep100': {'energy_lower_bound_MeV': 100, 'flux_lower_bound_pfu': 10000}})
    factors = [f for m in a.mechanisms for f in m.factors if 'GOES' in f.name]
    assert any(f.value == 11 and f.coverage == Coverage.PARTIAL for f in factors)
    assert not any(c.severity == 'critical' for m in a.mechanisms for c in m.conditions)


def test_wgs84_geodetic_position_not_spherical(monkeypatch):
    import vkd.assess.magcoords as mag
    monkeypatch.setattr(mag, 'eccentric_dipole', lambda *args: (np.array([0.,0.,1.]), 30000., np.zeros(3)))
    p = replace(traj(1, lambda i: False)[0], lat_deg=45., lon_deg=30., alt_km=420.)
    out, meta = mag.belt_coordinates([p], 'unused')
    f = 1/298.257223563
    e2 = f*(2-f)
    lat = np.deg2rad(45.)
    N = 6378.137/np.sqrt(1-e2*np.sin(lat)**2)
    rho = (N+420)*np.cos(lat)
    z = (N*(1-e2)+420)*np.sin(lat)
    radius = np.hypot(rho, z)
    expected = radius/mag.R_E_KM/(1-(z/radius)**2)
    assert out[0].L == pytest.approx(expected, abs=1e-12)
    assert 'WGS84' in meta['position_conversion']


def test_same_tle_different_receipts_are_isolated(tmp_path, monkeypatch):
    import vkd.integration.orbit_bridge as bridge
    monkeypatch.setattr(bridge, 'CACHE_ROOT', str(tmp_path))
    text = (ROOT/'data/orbit/iss.tle').read_text()
    a = bridge.stage_live_root(text, NOW, NOW, 'https://example.test', 'receipt1')
    before = (Path(a)/'data/orbit/manifest.json').read_bytes()
    b = bridge.stage_live_root(text, NOW+timedelta(minutes=5), NOW+timedelta(minutes=5), 'https://example.test', 'receipt2')
    assert a != b
    assert (Path(a)/'data/orbit/manifest.json').read_bytes() == before

@pytest.mark.parametrize('event_kind,key,value,expected', [('GST','kp',2,False),('GST','kp',8,True),
    ('CME_ARRIVAL','kp_range_max',2,False),('CME_ARRIVAL','kp_range_max',8,True)])
def test_structured_donki_fact_overrides_display_note(event_kind,key,value,expected):
    event=EventInterval(event_id='message:event',kind_of_event=event_kind,kind=Kind.EXTERNAL_FORECAST,
        start_utc=T0,end_utc=T0+timedelta(hours=1),start_uncertain=False,end_uncertain=False,
        valid_from_utc=T0,valid_to_utc=T0+timedelta(hours=1),source_id='nasa_donki_notification',
        published_utc=T0,raw_record_id='message',note='Kp до 9 (display text must not override parsed facts)')
    a=assess_window(Window(T0,60),traj(61,lambda i:False),BeltTable('min'),None,None,[],Thresholds(),T0,
        events=[event],event_facts={'message':{key:value}})
    assert any(c.kind=='GST' for m in a.mechanisms for c in m.conditions)==expected


def test_live_noaa_forecast_flows_to_windows_zip_and_offline_replay(tmp_path, monkeypatch):
    from vkd.sources import noaa_latest, Fetch
    from vkd.sources.registry import SourceRegistry, utc
    from scripts.replay_example import load_snapshot, saved_sources
    from vkd.integration.noaa_forecast import live_forecasts
    reg=SourceRegistry(ROOT)
    record=reg.records('noaa_ngdc_3day_forecast')[0]
    now=utc(record['published_utc'])+timedelta(hours=1)
    forecast=noaa_latest(cache_dir=tmp_path,now=now,transport=Mock(return_value=Response(reg.raw_bytes(record['raw_record_id']))))
    off=Fetch('off',False,False,None,None,'нет данных',None,None)
    fetched=((None,{},off),(None,{},off),(None,off),forecast)
    a=run('live',now,60,60,[0,60],now=now,fetched=fetched)
    assert {line['channel'] for line in a.S['forecasts'] if line['cells']}=={'kp_forecast','s1_prob_daily'}
    assert next(line for line in a.S['forecasts'] if line['channel']=='proton_prob_daily')['status'] in ('missing','unavailable')
    assert 'noaa_swpc_3day_forecast' in a.S['source_versions']
    assert any(f.name.startswith('прогноз Kp') and f.value is not None for m in a.assessments[0].mechanisms for f in m.factors)
    rid=forecast[0][0].raw_record_id
    assert base64.b64decode(a.raw_records[rid]['content_base64'])==reg.raw_bytes(record['raw_record_id'])
    dest=tmp_path/'live_forecast.zip';dest.write_bytes(build_zip(a.S,a.raw_records))
    never=Mock(side_effect=AssertionError('replay requested network'))
    monkeypatch.setattr('requests.sessions.Session.request',never)
    b=run('live',now,60,60,[0,60],now=now,fetched=saved_sources(load_snapshot(str(dest)),now))
    assert a.S['windows']==b.S['windows'] and a.S['recommendation']==b.S['recommendation']
    excluded=run('live',now,60,60,[0,60],now=now,fetched=fetched,disabled={'noaa':'off'})
    assert 'noaa_swpc_3day_forecast' not in excluded.S['source_versions']
    assert all(not line['cells'] for line in excluded.S['forecasts'])
    assert excluded.rec.verdict=='insufficient'
    never.assert_not_called()


def test_noaa_coverage_is_union_not_sum_of_duplicate_cells():
    from vkd.integration.noaa_forecast import covered_fraction
    from tests.test_compare import goes
    cell = replace(goes(1), valid_from_utc=T0, valid_to_utc=T0+timedelta(minutes=30))
    assert covered_fraction([cell, cell], T0, 60) == .5


def test_off_noaa_history_is_not_reintroduced_by_consumer():
    r = run('history_forecast', T0, 60, 60, [0,60], now=T0,
            disabled={'goes':False,'kp':False,'noaa':'off'})
    assert not any(line['cells'] for line in r.S['forecasts'])
    assert not any(sid.startswith('noaa_ngdc') for sid in r.S['source_versions'])
    assert all(v['status'] == 'disabled' for k,v in r.S['coverage_map'].items() if k.startswith('noaa_'))


def test_mixed_sep_energy_channels_cannot_escalate_s_level():
    from tests.test_models_wf2 import sep_event
    from vkd.windows.compare import _sep_level
    a = sep_event(T0, note='pfu=10')
    a = replace(a, raw_record_id='p10')
    b = replace(a, raw_record_id='p100', event_id='p100')
    assert _sep_level([a,b], {'p10':{'energy_lower_bound_MeV':10,'flux_lower_bound_pfu':10},
                             'p100':{'energy_lower_bound_MeV':100,'flux_lower_bound_pfu':10000}}) == (10.,False,True)
