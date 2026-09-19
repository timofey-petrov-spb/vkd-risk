from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest

from vkd.orbit.integration import integrate_time
from vkd.assess.trapped import FluxResult
from vkd.types import Coverage, Window
from vkd.windows.compare import assess_window, Thresholds
from tests.test_compare import traj

T = datetime(2024, 5, 3, 12, tzinfo=timezone.utc)


def times(seconds):
    return [T+timedelta(seconds=x) for x in seconds]


@pytest.mark.parametrize('grid', [[0, 10, 20, 35, 65], [0, 30, 60, 65]])
def test_linear_integral_and_crossing_with_clipped_window(grid):
    # f(t)=2t+3, integral from 7 to 62 = [t²+3t] = 3960.
    r = integrate_time(times(grid), [2*t+3 for t in grid], *times([7, 62]), threshold=53)
    assert r.total_integral == pytest.approx(3960)
    assert r.covered_seconds == 55
    assert r.below_threshold_seconds == pytest.approx(18)  # crossing at t=25
    assert r.grid_absolute_delta == pytest.approx(0, abs=1e-10)


def test_unknown_endpoints_gaps_and_missing_tail_are_not_zero():
    r = integrate_time(times([0,10,20,120,130]), [2,2,None,2,2], *times([0,140]))
    assert r.covered_seconds == 20
    assert r.known_integral == 40
    assert r.total_integral is None
    assert r.grid_absolute_delta is None
    assert integrate_time(times([0]), [2], *times([0,60])).known_integral is None
    zero = integrate_time(times([0,60]), [0,0], *times([0,60]))
    assert zero.total_integral == 0  # known zero differs from no model


def test_quadratic_convergence_is_second_order_on_common_support():
    errors = []
    for step in [20,10,5]:
        grid = list(range(0,121,step))
        r = integrate_time(times(grid), [t*t for t in grid], *times([0,120]))
        errors.append(r.total_integral-120**3/3)
        assert r.grid_compared_seconds == 120
        assert r.grid_absolute_delta == pytest.approx(3*errors[-1])
    assert errors[0]/errors[1] == pytest.approx(4)
    assert errors[1]/errors[2] == pytest.approx(4)


def test_grid_diagnostic_does_not_compare_different_coverage():
    r = integrate_time(times([0,10,20,30,40]), [1,None,1,2,1], *times([0,40]))
    assert r.grid_compared_seconds == 20
    assert r.covered_seconds == 20
    assert r.grid_absolute_delta == 10


@pytest.mark.parametrize('grid,values', [([0,0],[1,1]), ([10,0],[1,1]), ([0,10],[1,float('nan')])])
def test_invalid_nodes_rejected(grid, values):
    with pytest.raises(ValueError):
        integrate_time(times(grid), values, *times([0,60]))


class LinearBelts:
    raw_record_id = 'test:linear'
    source = 'analytic test only'
    flux_unit_short_ru = 'см⁻²·с⁻¹'
    interpolation_ru = 'analytic test only'

    def integral_flux(self, L, B_over_B0, e_min_MeV):
        return FluxResult(L, 'ok' if L is not None else 'no_model_L', e_min_MeV, 300)


@pytest.mark.parametrize('step', [5,10,30,60])
def test_production_assessment_has_no_minute_multiplier(step):
    base = traj(1, lambda i: False)[0]
    grid = list(range(0,121,step))
    track = [replace(base, t_utc=T+timedelta(seconds=t), L=2*t+3,
                     B_nT=23000+20*t, cutoff_GV=0.05+0.001*t) for t in grid]
    report = {}
    a = assess_window(Window(T,2), track, LinearBelts(), None, None, [], Thresholds(), T,
                      numerical_report=report)
    factors = a.mechanisms[0].factors
    assert factors[0].value == pytest.approx(50/60)
    assert factors[1].value == pytest.approx(14760)
    assert factors[1].coverage == Coverage.FULL
    assert report['fluence']['covered_seconds'] == 120


def test_missing_sample_lowers_temporal_coverage_in_production():
    track = traj(4, lambda i: True)
    track[1] = replace(track[1], L=None)
    report = {}
    a = assess_window(Window(T,3), track, LinearBelts(), None, None, [], Thresholds(), T,
                      numerical_report=report)
    assert a.mechanisms[0].factors[1].coverage == Coverage.PARTIAL
    assert report['fluence']['covered_seconds'] == 60
    # A dense cluster of nodes followed by a gap is not full coverage either.
    a = assess_window(Window(T,3), [track[0], track[-1]], LinearBelts(), None, None, [], Thresholds(), T)
    assert a.mechanisms[0].factors[1].coverage == Coverage.NONE


def test_elapsed_time_is_utc_not_wall_clock_across_dst():
    from zoneinfo import ZoneInfo
    zone = ZoneInfo('America/New_York')
    start = datetime(2024, 11, 3, 1, 15, tzinfo=zone, fold=0)
    end = datetime(2024, 11, 3, 1, 45, tzinfo=zone, fold=1)
    r = integrate_time([start,end], [2,2], start,end, max_gap_seconds=5400)
    assert r.duration_seconds == 5400
    assert r.total_integral == 10800
