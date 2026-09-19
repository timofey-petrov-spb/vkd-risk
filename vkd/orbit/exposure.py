"""Cumulative threshold exposure consistent with the window integrator.

A curve of known accumulated time is not a total when coverage is incomplete.
Gaps are explicit None values so plots cannot connect through unknown intervals.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from .integration import integrate_time


@dataclass(frozen=True)
class ThresholdExposure:
    times: tuple[datetime, ...]
    cumulative_seconds: tuple[float | None, ...]
    below_intervals: tuple[tuple[datetime, datetime], ...]
    known_seconds: float | None
    covered_seconds: float
    duration_seconds: float

    @property
    def total_seconds(self):
        return self.known_seconds if self.covered_seconds == self.duration_seconds else None


def threshold_exposure(times, values, start_utc, end_utc, *, threshold, max_gap_seconds=60):
    result = integrate_time(times, values, start_utc, end_utc,
                            threshold=threshold, max_gap_seconds=max_gap_seconds)
    times = [t.astimezone(timezone.utc) for t in times]
    start, end = start_utc.astimezone(timezone.utc), end_utc.astimezone(timezone.utc)
    xs, ys, spans = [], [], []
    acc, previous_end = 0.0, start

    def add(t, value):
        if xs and xs[-1] == t and ys[-1] == value:
            return
        xs.append(t); ys.append(value)

    for a,b,va,vb in zip(times,times[1:],values,values[1:]):
        lo,hi = max(a,start),min(b,end)
        dt=(b-a).total_seconds()
        if hi <= lo or va is None or vb is None or dt > max_gap_seconds:
            continue
        x=va+(vb-va)*(lo-a).total_seconds()/dt
        y=va+(vb-va)*(hi-a).total_seconds()/dt
        span=(hi-lo).total_seconds()
        if lo > previous_end:
            add(previous_end,None);add(lo,None)
        add(lo,acc)
        if x < threshold and y < threshold:
            spans.append((lo,hi));acc += span
        elif (x < threshold) != (y < threshold):
            fraction=(threshold-x)/(y-x)
            cross=lo+timedelta(seconds=span*fraction)
            if x < threshold:
                acc += span*fraction
                spans.append((lo,cross));add(cross,acc)
            else:
                add(cross,acc);acc += span*(1-fraction)
                spans.append((cross,hi))
        add(hi,acc)
        previous_end=hi
    if not xs:
        return ThresholdExposure((start,end),(None,None),(),None,0.0,result.duration_seconds)
    # Anchor the final known value to the SAME fsum-based quadrature as assessment.
    ys[-1]=result.below_threshold_seconds
    if previous_end < end:
        add(previous_end,None);add(end,None)
    merged=[]
    for lo,hi in spans:
        if hi<=lo:
            continue
        if merged and merged[-1][1]==lo:
            merged[-1]=(merged[-1][0],hi)
        else:
            merged.append((lo,hi))
    return ThresholdExposure(tuple(xs),tuple(ys),tuple(merged),result.below_threshold_seconds,
                             result.covered_seconds,result.duration_seconds)
