from datetime import datetime, timedelta, timezone
import pytest
from vkd.orbit.convergence import compare_grids

T = datetime(2024, 5, 3, tzinfo=timezone.utc)
def ts(seconds):
    return [T+timedelta(seconds=s) for s in seconds]

def test_same_linear_function_irregular_clipped_grids():
    r = compare_grids(ts([0, 20, 60]), [0,20,60], ts([0, 5, 17, 60]), [0,5,17,60],
                      T+timedelta(seconds=3), T+timedelta(seconds=55), threshold=30)
    assert r.common_seconds == 52
    assert r.support_change_seconds == 0
    assert r.absolute_difference_integral == pytest.approx(0, abs=1e-10)
    assert r.fine_common_integral == pytest.approx((55**2-3**2)/2)
    assert r.threshold_duration_change_seconds == 0

def test_opposite_signed_changes_do_not_cancel():
    r = compare_grids(ts([0,60]), [0,0], ts([0,30,60]), [-1,0,1], T,T+timedelta(seconds=60))
    assert r.coarse_common_integral == r.fine_common_integral == 0
    assert r.absolute_difference_integral == 30
    assert r.relative_difference == 1

def test_difference_zero_crossing_inside_segment():
    r = compare_grids(ts([0,60]), [1,-1], ts([0,60]), [0,0], T,T+timedelta(seconds=60))
    assert r.absolute_difference_integral == 30
    assert r.relative_difference is None  # nonzero difference from zero fine signal

def test_equal_coverage_different_support_cannot_pass():
    r = compare_grids(ts([0,20,40,60]), [1,1,None,None], ts([0,20,40,60]),
                      [None,None,1,1], T,T+timedelta(seconds=60))
    assert r.coarse_covered_seconds == r.fine_covered_seconds == 20
    assert r.support_change_seconds == 40
    assert r.common_seconds == 0
    assert r.relative_difference is None

def test_unknown_regions_never_compared_as_zero():
    r = compare_grids(ts([0,20,40,60]), [1,1,None,1], ts([0,10,20,30,40,50,60]),
                      [1]*7, T,T+timedelta(seconds=60))
    assert r.common_seconds == 20
    assert r.support_change_seconds == 40
    assert r.absolute_difference_integral == 0

def test_known_zero_and_long_gap():
    r = compare_grids(ts([0,60]), [0,0], ts([0,30,60]), [0,0,0], T,T+timedelta(seconds=60))
    assert r.relative_difference == 0
    r = compare_grids(ts([0,120]), [1,1], ts([0,60,120]), [1,1,1], T,T+timedelta(seconds=120))
    assert r.common_seconds == 0
    assert r.support_change_seconds == 120

def test_quadratic_second_order_change():
    results=[]
    for h in (30,15,5):
        c=list(range(0,61,h));f=list(range(0,61,h//3))
        r=compare_grids(ts(c),[s*s for s in c],ts(f),[s*s for s in f],T,T+timedelta(seconds=60))
        results.append(r.absolute_difference_integral)
    assert results[0]/results[1] == pytest.approx(4)

def test_invalid_grid_rejected():
    with pytest.raises(ValueError,match='strictly'):
        compare_grids(ts([0,0]),[0,0],ts([0,60]),[0,0],T,T+timedelta(seconds=60))


def test_local_diagnostics_sum_to_global_and_locate_signed_changes():
    import math
    intervals=[]
    r=compare_grids(ts([0,60]),[0,0],ts([0,30,60]),[-1,0,1],T,T+timedelta(seconds=60),interval_report=intervals)
    assert len(intervals)==2
    assert [x['fine_interval_index'] for x in intervals]==[0,1]
    assert [x['coarse_interval_index'] for x in intervals]==[0,0]
    assert math.fsum(x['absolute_difference_integral'] for x in intervals)==r.absolute_difference_integral
    assert math.fsum(x['fine_integral'] for x in intervals)==r.fine_common_integral
