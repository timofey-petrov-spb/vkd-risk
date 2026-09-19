from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import pytest
from vkd.orbit.exposure import threshold_exposure
from vkd.orbit.integration import integrate_time

T=datetime(2024,5,3,tzinfo=timezone.utc)
def ts(seconds):return [T+timedelta(seconds=s) for s in seconds]

def test_analytic_crossings_not_left_rectangles():
    r=threshold_exposure(ts([0,60,120]),[30,20,30],T,T+timedelta(seconds=120),threshold=24)
    assert r.total_seconds==48
    assert r.below_intervals==((T+timedelta(seconds=36),T+timedelta(seconds=84)),)
    assert r.cumulative_seconds[-1]==48
    assert all(a<=b for a,b in zip(r.cumulative_seconds,r.cumulative_seconds[1:]))

def test_clips_both_window_edges():
    r=threshold_exposure(ts([0,60,120]),[30,20,30],T+timedelta(seconds=40),T+timedelta(seconds=80),threshold=24)
    assert r.total_seconds==40
    assert r.times[0]==T+timedelta(seconds=40)
    assert r.times[-1]==T+timedelta(seconds=80)

def test_gaps_and_unknown_endpoints_do_not_make_flat_known_curves():
    r=threshold_exposure(ts([0,30,120,150]),[1,1,1,1],T,T+timedelta(seconds=150),threshold=2)
    assert r.known_seconds==60
    assert r.covered_seconds==60
    assert r.total_seconds is None
    assert None in r.cumulative_seconds
    assert len(r.below_intervals)==2

def test_unknown_not_zero_and_no_extrapolation():
    r=threshold_exposure(ts([0,60]),[None,1],T,T+timedelta(seconds=60),threshold=2)
    assert r.known_seconds is None and r.total_seconds is None
    r=threshold_exposure(ts([10,60]),[3,3],T,T+timedelta(seconds=70),threshold=2)
    assert r.known_seconds==0 and r.total_seconds is None
    assert r.cumulative_seconds[0] is None and r.cumulative_seconds[-1] is None

@pytest.mark.parametrize('step',[5,10,30,60])
def test_curve_agrees_with_assessment_quadrature_on_all_steps(step):
    seconds=list(range(0,121,step));values=[20+abs(s-60)/6 for s in seconds]
    r=threshold_exposure(ts(seconds),values,T,T+timedelta(seconds=120),threshold=24)
    expected=integrate_time(ts(seconds),values,T,T+timedelta(seconds=120),threshold=24)
    assert r.total_seconds==expected.below_threshold_seconds
    assert r.total_seconds==pytest.approx(48, abs=1e-10)
    assert r.cumulative_seconds[-1]==pytest.approx(48, abs=1e-10)

def test_plot_uses_field_and_declared_threshold_not_cached_boolean():
    from app.viz import window_exposure
    pts=[SimpleNamespace(t_utc=t,B_nT=b,in_saa=False) for t,b in zip(ts([0,60,120]),[30,20,30])]
    w=SimpleNamespace(start_utc=T,duration_min=2)
    xs,ys,total=window_exposure(pts,w,step_min=123,threshold_nT=24)
    assert total==.8
    assert ys[-1]==.8

def test_invalid_grid_rejected():
    with pytest.raises(ValueError):threshold_exposure(ts([0,0]),[1,1],T,T+timedelta(seconds=60),threshold=2)


@pytest.mark.parametrize('step', [5, 10, 30, 60])
def test_plot_total_matches_production_window_factor(step):
    from dataclasses import replace
    from app.viz import window_exposure
    from tests.test_compare import traj
    from tests.orbit.test_time_integration import LinearBelts
    from vkd.types import Window
    from vkd.windows.compare import assess_window, Thresholds

    base = traj(1, lambda i: False)[0]
    track = [replace(base, t_utc=T+timedelta(seconds=t), L=2,
                     B_nT=23000+20*t) for t in range(0, 121, step)]
    window = Window(T, 2)
    assessment = assess_window(window, track, LinearBelts(), None, None, [], Thresholds(), T)
    _, values, total = window_exposure(track, window)
    assert total == pytest.approx(50/60)
    assert total == assessment.mechanisms[0].factors[0].value
    assert values[-1] == total
