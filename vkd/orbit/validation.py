"""Numerical grid diagnostics, not physical validation or an operational estimator.

No extrapolation, no gap filling. Intervals with an unknown endpoint remain
uncovered; a partial integral is explicitly a known contribution, not a total.
"""
from datetime import datetime
import math
from typing import Sequence


def interval_diagnostics(times: Sequence[datetime], values: Sequence[float | None], *,
                         max_gap_seconds: float, threshold: float | None = None) -> dict:
    if len(times) != len(values) or len(times) < 2:
        raise ValueError('Need at least two matching time/value samples')
    if not math.isfinite(max_gap_seconds) or max_gap_seconds <= 0:
        raise ValueError('Maximum gap must be finite and positive')
    if threshold is not None and not math.isfinite(threshold):
        raise ValueError('Threshold must be finite')
    if any(t.tzinfo is None or t.utcoffset() is None for t in times):
        raise ValueError('Times must have UTC offsets')
    if any(v is not None and not math.isfinite(v) for v in values):
        raise ValueError('Nonfinite value: represent missing data as None')
    duration = (times[-1]-times[0]).total_seconds()
    covered = left_covered = trapezoid = left = below = below_left = 0.0
    for t0, t1, v0, v1 in zip(times, times[1:], values, values[1:]):
        dt = (t1-t0).total_seconds()
        if dt <= 0:
            raise ValueError('Times must strictly increase')
        if dt > max_gap_seconds:
            continue
        # Left rectangles reproduce the production one-minute sampling convention.
        if v0 is not None:
            left += v0*dt
            left_covered += dt
            if threshold is not None and v0 < threshold:
                below_left += dt
        if v0 is None or v1 is None:
            continue
        covered += dt
        trapezoid += .5*(v0+v1)*dt
        if threshold is not None:
            if v0 < threshold and v1 < threshold:
                below += dt
            elif (v0 < threshold) != (v1 < threshold):
                f = (threshold-v0)/(v1-v0)
                below += dt*(f if v0 < threshold else 1-f)
    return {'duration_seconds': duration, 'covered_seconds': covered,
            'coverage_fraction': covered/duration,
            'trapezoid_known_integral_value_seconds': trapezoid if covered else None,
            'left_known_integral_value_seconds': left if left_covered else None,
            'left_coverage_fraction': left_covered/duration,
            'below_threshold_linear_seconds': below if threshold is not None and covered else None,
            'below_threshold_left_seconds': below_left if threshold is not None and left_covered else None,
            'total_integral_value_seconds': trapezoid if covered == duration else None,
            'status': 'full' if covered == duration else ('partial' if covered else 'none')}
