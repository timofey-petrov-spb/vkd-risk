"""Compare piecewise-linear grids on identical known UTC support.

A numerical agreement indicator, never a physical error bound. Unknown model
regions and long gaps are removed from both sides of the comparison.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
from typing import Sequence
from .integration import integrate_time


@dataclass(frozen=True)
class GridComparison:
    duration_seconds: float
    coarse_covered_seconds: float
    fine_covered_seconds: float
    common_seconds: float
    support_change_seconds: float
    coarse_common_integral: float | None
    fine_common_integral: float | None
    absolute_difference_integral: float | None
    relative_difference: float | None
    threshold_duration_change_seconds: float | None

    def to_dict(self):
        return asdict(self)


def compare_grids(coarse_times: Sequence[datetime], coarse_values: Sequence[float | None],
                  fine_times: Sequence[datetime], fine_values: Sequence[float | None],
                  start_utc: datetime, end_utc: datetime, *, max_gap_seconds: float = 60,
                  threshold: float | None = None) -> GridComparison:
    """Integrate |linear_fine - linear_coarse| on their common valid support.

The absolute difference is integrated BEFORE summation, including zero
crossings. Opposite local changes cannot cancel. Coverage change is symmetric
difference of supports (not difference of lengths, which could also cancel).
Threshold durations are compared on that same common support only.
"""
    coarse = integrate_time(coarse_times, coarse_values, start_utc, end_utc,
                            max_gap_seconds=max_gap_seconds, threshold=threshold)
    fine = integrate_time(fine_times, fine_values, start_utc, end_utc,
                          max_gap_seconds=max_gap_seconds, threshold=threshold)
    start, end = start_utc.astimezone(timezone.utc), end_utc.astimezone(timezone.utc)

    def segments(times, values):
        times = [t.astimezone(timezone.utc) for t in times]
        for a, b, va, vb in zip(times, times[1:], values, values[1:]):
            if va is not None and vb is not None and (b-a).total_seconds() <= max_gap_seconds:
                lo, hi = max(a, start), min(b, end)
                if lo < hi:
                    yield lo, hi, a, b, va, vb

    def at(s, t):
        return s[4] + (s[5]-s[4]) * (t-s[2]).total_seconds()/(s[3]-s[2]).total_seconds()

    def abs_area(a, b, dt):
        if a*b >= 0:
            return dt*(abs(a)+abs(b))/2
        # Two triangles around a zero crossing of a linear difference.
        return dt*(a*a+b*b)/(2*(abs(a)+abs(b)))

    def under(a, b, dt):
        if threshold is None:
            return 0.0
        if a < threshold and b < threshold:
            return dt
        if (a < threshold) == (b < threshold):
            return 0.0
        f = (threshold-a)/(b-a)
        return dt*(f if a < threshold else 1-f)

    left, right = iter(segments(coarse_times, coarse_values)), iter(segments(fine_times, fine_values))
    a, b = next(left, None), next(right, None)
    durations, ci, fi, differences, scales, tc, tf = [], [], [], [], [], [], []
    while a is not None and b is not None:
        lo, hi = max(a[0], b[0]), min(a[1], b[1])
        if hi > lo:
            dt = (hi-lo).total_seconds()
            c0, c1, f0, f1 = at(a, lo), at(a, hi), at(b, lo), at(b, hi)
            durations.append(dt)
            ci.append(dt*(c0+c1)/2); fi.append(dt*(f0+f1)/2)
            differences.append(abs_area(f0-c0, f1-c1, dt))
            scales.append(abs_area(f0, f1, dt))
            tc.append(under(c0,c1,dt)); tf.append(under(f0,f1,dt))
        ae, be = a[1], b[1]
        if ae <= be:
            a = next(left, None)
        if be <= ae:
            b = next(right, None)
    common = math.fsum(durations)
    delta, scale = math.fsum(differences), math.fsum(scales)
    return GridComparison(coarse.duration_seconds, coarse.covered_seconds, fine.covered_seconds,
        common, coarse.covered_seconds + fine.covered_seconds - 2*common,
        math.fsum(ci) if common else None, math.fsum(fi) if common else None,
        delta if common else None, delta/scale if scale else (0.0 if common and delta == 0 else None),
        abs(math.fsum(tc)-math.fsum(tf)) if common and threshold is not None else None)
