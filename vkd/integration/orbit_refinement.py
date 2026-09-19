"""Bounded, source-fixed orbit grid refinement for the production estimator.

Recomputes orbit and IGRF at each grid, without network access. It reports
numerical agreement ONLY on common known support; it cannot fill model gaps.
"""
from dataclasses import asdict, dataclass
from datetime import timedelta
import hashlib
import math
from pathlib import Path
from typing import Callable

from vkd.assess.magcoords import belt_coordinates
from vkd.assess.trapped import BeltTable
from vkd.orbit import OrbitDataError
from vkd.orbit.convergence import compare_grids
from vkd.integration.orbit_bridge import OrbitResult


@dataclass(frozen=True)
class RefinementPolicy:
    steps_seconds: tuple[int, ...] = (60, 30, 15, 5)
    relative_tolerance: float = 0.02
    absolute_fluence_tolerance_per_cm2: float = 1e-6
    support_change_tolerance_seconds: float = 10.0
    threshold_time_tolerance_seconds: float = 2.0
    max_total_points: int = 40000
    required_consecutive_passes: int = 2

    def __post_init__(self):
        steps = tuple(self.steps_seconds)
        object.__setattr__(self, 'steps_seconds', steps)
        if len(steps) < 2 or steps[0] != 60 or any(type(s) is not int or not 1 <= s <= 60 for s in steps):
            raise ValueError('Refinement grids must start at 60 s and contain at least two integer steps in [1,60]')
        if any(a <= b or a % b for a,b in zip(steps,steps[1:])):
            raise ValueError('Refinement grids must be strictly decreasing and nested')
        for name in ('relative_tolerance', 'absolute_fluence_tolerance_per_cm2',
                     'support_change_tolerance_seconds', 'threshold_time_tolerance_seconds'):
            value = getattr(self, name)
            if isinstance(value,bool) or not math.isfinite(value) or value < 0:
                raise ValueError(f'{name} must be finite and nonnegative')
        if type(self.max_total_points) is not int or self.max_total_points < 2:
            raise ValueError('max_total_points must be an integer >=2')
        if type(self.required_consecutive_passes) is not int or not 1 <= self.required_consecutive_passes < len(steps):
            raise ValueError('required_consecutive_passes must fit the number of grid comparisons')


def refine_orbit(initial: OrbitResult, build_at_step: Callable[[int], OrbitResult],
                 windows: list[tuple], coefficients_path: str | Path, *,
                 saa_threshold_nT: float, e_min_MeV: float = 30,
                 policy: RefinementPolicy | None = None) -> tuple[OrbitResult, dict]:
    policy = policy or RefinementPolicy()
    if not windows:
        raise ValueError('At least one window is required')
    report = {'schema_version': 'orbit-refinement-v1', 'policy': asdict(policy),
              'status': 'unavailable', 'selected_step_seconds': initial.provenance.get('step_seconds'),
              'evaluated_points': len(initial.points), 'comparisons': [],
              'scope': 'numerical agreement on common known support; NOT physical accuracy or full coverage'}
    if len(initial.points) < 2:
        return initial, report
    if initial.provenance.get('step_seconds') != policy.steps_seconds[0]:
        raise ValueError('Initial orbit grid does not match the refinement policy')
    hashes = {rid:r['sha256'] for rid,r in initial.provenance['records'].items()}
    if not hashes:
        raise ValueError('Refinement requires identified source hashes')
    report['source_hashes'] = hashes
    reference_utc = initial.points[len(initial.points)//2].t_utc
    report['magnetic_epoch_utc'] = reference_utc.isoformat()
    belts = BeltTable('min')
    report['belt_table_sha256'] = belts.sha256
    energies = sorted({12.5, 30.0, 50.0, float(e_min_MeV)})
    report['energy_channels_MeV'] = energies

    def evaluate(orbit):
        # Coefficient file used by B must match the very same IGRF bytes as A3.
        coefficient_hash = hashlib.sha256(Path(coefficients_path).read_bytes()).hexdigest()
        if coefficient_hash not in hashes.values():
            raise OrbitDataError('Belt coordinates use a different IGRF coefficient file')
        coords,_ = belt_coordinates(orbit.points, str(coefficients_path), reference_utc=reference_utc)
        return ([p.t_utc for p in coords],
                {energy:[belts.integral_flux(p.L,p.B_over_B0,energy).value_per_cm2_s for p in coords]
                 for energy in energies}, [p.B_nT for p in coords])

    current, coarse = initial, evaluate(initial)
    streak = 0
    for step in policy.steps_seconds[1:]:
        duration = (initial.points[-1].t_utc-initial.points[0].t_utc).total_seconds()
        expected_points = math.ceil(duration/step)+1
        if report['evaluated_points'] + expected_points > policy.max_total_points:
            report['status'] = 'point_budget_exhausted'
            return current, report
        try:
            candidate = build_at_step(step)
            report['evaluated_points'] += len(candidate.points)
            if len(candidate.points) != expected_points or candidate.error:
                report['status'] = 'refinement_unavailable'
                report['reason'] = candidate.error or 'Incomplete candidate grid'
                return current, report
            actual = {rid:r['sha256'] for rid,r in candidate.provenance['records'].items()}
            if actual != hashes or candidate.strictness != initial.strictness:
                report['status'] = 'source_changed'
                report['reason'] = 'Sources or historical interpretation changed between grids'
                return current, report
            if candidate.provenance.get('step_seconds') != step or any(
                p.t_utc != initial.points[0].t_utc + timedelta(seconds=min(i*step,duration))
                for i,p in enumerate(candidate.points)):
                raise OrbitDataError('Candidate times do not match requested refinement grid')
            fine = evaluate(candidate)
        except OrbitDataError as exc:
            report['status'] = 'refinement_unavailable'; report['reason'] = str(exc)
            return current, report
        entries=[]
        for start,end in windows:
            channels = {}
            for energy in energies:
                result = compare_grids(coarse[0],coarse[1][energy],fine[0],fine[1][energy],start,end)
                delta = result.absolute_difference_integral
                agreed = (delta is not None and result.common_seconds > 0 and
                    delta <= policy.absolute_fluence_tolerance_per_cm2 +
                             policy.relative_tolerance*abs(result.fine_common_integral or 0) and
                    result.support_change_seconds <= policy.support_change_tolerance_seconds)
                channels[str(energy)] = {**result.to_dict(), 'agreed': agreed}
            field = compare_grids(coarse[0],coarse[2],fine[0],fine[2],start,end,threshold=saa_threshold_nT)
            field_agreed = (field.common_seconds == field.duration_seconds and
                field.threshold_duration_change_seconds is not None and
                field.threshold_duration_change_seconds <= policy.threshold_time_tolerance_seconds)
            entries.append({'start_utc':start.isoformat(),'end_utc':end.isoformat(),
                            'fluence_channels':channels,'saa':field.to_dict(),
                            'agreed':field_agreed and all(v['agreed'] for v in channels.values())})
        passed = all(w['agreed'] for w in entries)
        streak = streak+1 if passed else 0
        report['comparisons'].append({'coarse_step_seconds':current.provenance['step_seconds'],
                                     'fine_step_seconds':step,'windows':entries,'agreed':passed})
        current,coarse = candidate,fine
        report['selected_step_seconds'] = step
        if streak >= policy.required_consecutive_passes:
            report['status'] = 'converged_known_support'
            return current,report
    report['status'] = 'resolution_limit_reached'
    return current,report
