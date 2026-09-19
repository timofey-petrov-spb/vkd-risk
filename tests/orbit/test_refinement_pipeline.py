from datetime import datetime, timedelta, timezone
import io
import json
import zipfile
import pytest
from app.compute import run
from app.export import build_zip

T=datetime(2024,5,3,12,tzinfo=timezone.utc)

def test_refined_production_pipeline_and_export_are_reproducible_without_network(monkeypatch):
    import requests
    def never(*args,**kwargs):raise AssertionError('network forbidden')
    monkeypatch.setattr(requests.sessions.Session,'request',never)
    kwargs=dict(mode='history_review',t0=T,duration_min=60,search_min=60,
                window_offsets_min=[0,60],now=T,refinement_policy={})
    result=run(**kwargs)
    r=result.S['grid_refinement']
    step=r['selected_step_seconds']
    assert step < 60
    assert len(result.traj)==120*60//step+1
    assert result.S['trajectory_meta']['provenance']['step_seconds']==step
    assert r['status'] in ('resolution_limit_reached','converged_known_support')
    assert result.rec.verdict=='insufficient'  # refinement does not heal physical coverage
    assert r['source_hashes']=={rid:meta['sha256'] for rid,meta in result.S['trajectory_meta']['provenance']['records'].items()}
    with zipfile.ZipFile(io.BytesIO(build_zip(result.S,result.raw_records))) as z:
        exported=json.loads(z.read('grid_refinement.json'))
        assert exported==json.loads(json.dumps(r))
        assert json.loads(z.read('manifest.json'))['grid_refinement']==exported
    repeated=run(**{**kwargs,'refinement_policy':json.loads(json.dumps(result.S['request']['refinement_policy']))})
    assert repeated.S['grid_refinement']==result.S['grid_refinement']
    assert repeated.S['windows']==result.S['windows']

def test_refinement_budget_does_not_change_scenario_or_publication_cutoff():
    from vkd.windows.scenario import Scenario
    result=run('history_review',T,60,60,[0,60],now=T,scenario=Scenario('delay',work_delay_min=90),
               refinement_policy={'max_total_points':2})
    assert result.S['grid_refinement']['status']=='point_budget_exhausted'
    assert result.traj[0].t_utc==T+timedelta(minutes=90)
    assert result.S['trajectory_meta']['provenance']['selection_cutoff_utc']==T.isoformat().replace('+00:00','Z')

def test_invalid_policy_rejected_before_orbit(monkeypatch):
    monkeypatch.setattr('app.compute.build_orbit',lambda *a,**k:pytest.fail('orbit must not run'))
    with pytest.raises(ValueError):run('history_review',T,60,60,[0,60],refinement_policy={'relative_tolerance':-1})


def test_refinement_freezes_magnetic_epoch_on_odd_minute_horizon():
    result=run('history_review',T,60,61,[0,61],now=T,refinement_policy={'max_total_points':500})
    expected=(T+timedelta(minutes=61)).isoformat()
    assert result.S['grid_refinement']['magnetic_epoch_utc']==expected
    assert result.S['trajectory_meta']['belt_coordinates']['epoch_utc']==expected
    assert result.S['grid_refinement']['selected_step_seconds']==30


def test_unconfirmed_numerics_cannot_keep_preferred_verdict(monkeypatch):
    from dataclasses import replace
    import app.compute as pipeline
    original=pipeline.recommend
    def force_preferred(assessments,th):
        return replace(original(assessments,th),preferred=assessments[0].window,verdict='preferred',missing=())
    monkeypatch.setattr(pipeline,'recommend',force_preferred)
    result=run('history_review',T,60,60,[0,60],now=T,refinement_policy={'max_total_points':2})
    assert result.rec.verdict=='insufficient'
    assert result.rec.preferred is None
    assert any('численное согласие' in r for r in result.rec.missing)
