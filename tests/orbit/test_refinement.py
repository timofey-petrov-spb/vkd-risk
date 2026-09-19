from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import hashlib
import pytest
from vkd.integration.orbit_bridge import OrbitResult
from vkd.integration.orbit_refinement import RefinementPolicy, refine_orbit
import vkd.integration.orbit_refinement as module

T=datetime(2024,5,3,tzinfo=timezone.utc)
@pytest.fixture
def problem(tmp_path,monkeypatch):
    coeff=tmp_path/'coeff';coeff.write_bytes(b'coeff')
    digest=hashlib.sha256(b'coeff').hexdigest()
    monkeypatch.setattr(module,'belt_coordinates',lambda points,path,**kwargs:(points,{}))
    class Table:
        sha256='table-test-hash'
        def __init__(self,*args): pass
        def integral_flux(self,L,B,E): return SimpleNamespace(value_per_cm2_s=L)
    monkeypatch.setattr(module,'BeltTable',Table)
    def build(step,func=lambda s:1):
        pts=[SimpleNamespace(t_utc=T+timedelta(seconds=s),L=func(s),B_over_B0=1,B_nT=25000)
             for s in range(0,121,step)]
        return OrbitResult(None,pts,{'step_seconds':step,'records':{'coeff':{'sha256':digest}}},'', 'declared_reconstruction',None)
    def run(builder=build,policy=None):
        return refine_orbit(builder(60),builder,[(T,T+timedelta(seconds=120))],coeff,saa_threshold_nT=24000,policy=policy)
    return build,run

def test_two_successive_comparisons_required(problem):
    build,run=problem
    orbit,r=run()
    assert r['selected_step_seconds']==15
    assert r['status']=='converged_known_support'
    assert len(r['comparisons'])==2
    assert r['evaluated_points']==3+5+9

def test_budget_retains_last_verified_grid(problem):
    build,run=problem
    orbit,r=run(policy=RefinementPolicy(max_total_points=8))
    assert r['status']=='point_budget_exhausted'
    assert r['selected_step_seconds']==30
    assert len(orbit.points)==5

def test_source_switch_is_not_numerical_convergence(problem):
    build,run=problem
    def switched(step):
        r=build(step)
        if step<60:r.provenance['records']['coeff']['sha256']='different'
        return r
    orbit,r=run(switched)
    assert r['status']=='source_changed'
    assert r['selected_step_seconds']==60

def test_missing_signal_cannot_converge(problem):
    build,run=problem
    orbit,r=run(lambda step:build(step,lambda s:None))
    assert r['status']=='resolution_limit_reached'
    assert not any(c['agreed'] for c in r['comparisons'])

def test_partial_constant_signal_still_declares_partial_scope(problem):
    build,run=problem
    orbit,r=run(lambda step:build(step,lambda s:1 if s>=60 else None))
    assert r['status']=='converged_known_support'
    channel=r['comparisons'][-1]['windows'][0]['fluence_channels']['30.0']
    assert channel['common_seconds']==60
    assert channel['duration_seconds']==120
    assert channel['fine_covered_seconds']==60

def test_strictness_change_rejected(problem):
    build,run=problem
    from dataclasses import replace
    orbit,r=run(lambda step:replace(build(step),strictness='strict' if step<60 else 'declared_reconstruction'))
    assert r['status']=='source_changed'

def test_wrong_grid_rejected(problem):
    build,run=problem
    def wrong(step):
        o=build(step)
        if step<60:o.points[1].t_utc+=timedelta(seconds=1)
        return o
    orbit,r=run(wrong)
    assert r['status']=='refinement_unavailable'
    assert r['selected_step_seconds']==60

@pytest.mark.parametrize('kwargs',[{'steps_seconds':[60,15,10]}, {'relative_tolerance':float('nan')},
                                  {'max_total_points':True},{'required_consecutive_passes':4}])
def test_invalid_policy(kwargs):
    with pytest.raises(ValueError):RefinementPolicy(**kwargs)
