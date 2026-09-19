"""Integrate sampled scalar fields on UTC intervals without extrapolation.

Coverage is measured in seconds, never in point counts. Unknown endpoints and
intervals above the declared maximum gap contribute neither time nor integral.
The coarse/fine diagnostic compares only identical, fully known intervals; it
is a numerical sensitivity indicator, not a bound on physical model error.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Sequence


@dataclass(frozen=True)
class TimeIntegral:
    duration_seconds: float
    covered_seconds: float
    known_integral: float | None
    below_threshold_seconds: float | None
    grid_compared_seconds: float
    grid_absolute_delta: float | None
    grid_relative_delta: float | None

    @property
    def coverage_fraction(self) -> float:
        return self.covered_seconds / self.duration_seconds

    @property
    def total_integral(self) -> float | None:
        return self.known_integral if self.covered_seconds == self.duration_seconds else None


def integrate_time(times: Sequence[datetime], values: Sequence[float | None],
                   start_utc: datetime, end_utc: datetime, *,
                   max_gap_seconds: float = 60.0,
                   threshold: float | None = None) -> TimeIntegral:
    """Piecewise linear quadrature, clipped to [start, end].

No final sample is silently assigned another minute of exposure. A window
boundary within a known pair is linearly interpolated; outside the sampled
span it remains uncovered. All input times must be aware and strictly ordered.
Nonfinite values must be represented as None by the producer.
"""
    if len(times) != len(values):
        raise ValueError('Time and value lengths differ')
    if any(t.tzinfo is None or t.utcoffset() is None for t in (*times, start_utc, end_utc)):
        raise ValueError('Times must have UTC offsets')
    # Same-zone datetime subtraction otherwise measures wall time across DST.
    times = [t.astimezone(timezone.utc) for t in times]
    start_utc, end_utc = start_utc.astimezone(timezone.utc), end_utc.astimezone(timezone.utc)
    if end_utc <= start_utc:
        raise ValueError('Window duration must be positive')
    if not math.isfinite(max_gap_seconds) or max_gap_seconds <= 0:
        raise ValueError('Maximum gap must be finite and positive')
    if threshold is not None and not math.isfinite(threshold):
        raise ValueError('Threshold must be finite')
    if any(v is not None and not math.isfinite(v) for v in values):
        raise ValueError('Nonfinite value: represent missing data as None')
    for a, b in zip(times, times[1:]):
        if b <= a:
            raise ValueError('Times must strictly increase')

    def segment(a, b, va, vb):
        lo, hi = max(a, start_utc), min(b, end_utc)
        if hi <= lo:
            return None
        span = (b-a).total_seconds()
        x = va + (vb-va) * (lo-a).total_seconds()/span
        y = va + (vb-va) * (hi-a).total_seconds()/span
        dt = (hi-lo).total_seconds()
        integral = (x+y)*0.5*dt
        below = 0.0
        if threshold is not None:
            if x < threshold and y < threshold:
                below = dt
            elif (x < threshold) != (y < threshold):
                fraction = (threshold-x)/(y-x)
                below = dt * (fraction if x < threshold else 1-fraction)
        return dt, integral, below

    covered, integral, below = [], [], []
    known_segments = {}
    for i, (a, b, va, vb) in enumerate(zip(times, times[1:], values, values[1:])):
        if va is None or vb is None or (b-a).total_seconds() > max_gap_seconds:
            continue
        result = segment(a, b, va, vb)
        if result is not None:
            dt, value, under = result
            covered.append(dt); integral.append(value); below.append(under)
            known_segments[i] = result

    # Non-overlapping pairs of fine intervals: the coarse segment uses their
    # outer endpoints. Compare only when BOTH fine intervals are fully known.
    # Summing absolute local differences prevents cancellation hiding sensitivity.
    compared, deltas, fine_abs = [], [], []
    for i in range(0, len(times)-2, 2):
        if i not in known_segments or i+1 not in known_segments:
            continue
        coarse = segment(times[i], times[i+2], values[i], values[i+2])
        left, right = known_segments[i], known_segments[i+1]
        fine = left[1] + right[1]
        compared.append(left[0]+right[0])
        deltas.append(abs(coarse[1]-fine))
        fine_abs.append(abs(fine))
    delta = math.fsum(deltas) if compared else None
    scale = math.fsum(fine_abs)
    relative = delta/scale if scale else (0.0 if delta == 0 else None)
    return TimeIntegral((end_utc-start_utc).total_seconds(), math.fsum(covered),
                        math.fsum(integral) if covered else None,
                        math.fsum(below) if covered and threshold is not None else None,
                        math.fsum(compared), delta, relative)
