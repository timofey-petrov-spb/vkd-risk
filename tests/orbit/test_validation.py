from datetime import datetime, timedelta, timezone
import pytest
from vkd.orbit.validation import interval_diagnostics

T0 = datetime(2024, 5, 10, tzinfo=timezone.utc)

def times(*seconds):
    return [T0+timedelta(seconds=s) for s in seconds]


def test_linear_integral_crossing_irregular_last_step():
    # f(t)=2t, interval 0..65: integral 65**2; f<30 for exactly 15 seconds.
    d = interval_diagnostics(times(0,30,60,65), [0,60,120,130], max_gap_seconds=30, threshold=30)
    assert d['total_integral_value_seconds'] == 65**2
    assert d['below_threshold_linear_seconds'] == 15
    assert d['duration_seconds'] == d['covered_seconds'] == 65


def test_missing_endpoint_and_gap_never_become_total_or_zero():
    d = interval_diagnostics(times(0,10,20,30,100), [2,2,None,2,2], max_gap_seconds=10)
    assert d['coverage_fraction'] == .1 and d['status'] == 'partial'
    assert d['trapezoid_known_integral_value_seconds'] == 20
    assert d['total_integral_value_seconds'] is None
    d = interval_diagnostics(times(0,10), [None,None], max_gap_seconds=10, threshold=1)
    assert d['status'] == 'none' and d['trapezoid_known_integral_value_seconds'] is None
    assert d['below_threshold_linear_seconds'] is None


@pytest.mark.parametrize('seconds,values', [((0,0),(1,1)),((10,0),(1,1)),((0,10),(1,float('nan')))])
def test_invalid_inputs_rejected(seconds,values):
    with pytest.raises(ValueError):
        interval_diagnostics(times(*seconds),values,max_gap_seconds=10)
