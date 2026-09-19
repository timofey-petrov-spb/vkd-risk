"""Preliminary, covariance-free GP/SGP4 encounter screening in TEME.

This is an engineering screen, not an operational collision-risk assessment.
The acceleration padding and radial margin are declared assumptions; validate
their numerical effects independently of the unknown real GP orbit error.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
import time

import numpy as np
from scipy.optimize import brentq
from sgp4.api import SatrecArray, jday

from vkd.sources.catalogue import Catalogue, GPObject
from vkd.sources.registry import utc
from vkd.types import (
    Condition,
    Conjunction,
    Coverage,
    FactorValue,
    Kind,
    MechanismAssessment,
    Presence,
    Window,
)

MODEL_VERSION = "gp-screen-v1"


@dataclass(frozen=True)
class ScreeningConfig:
    report_distance_km: float = 50.0
    check_distance_km: float = 5.0
    coarse_step_s: float = 60.0
    refine_step_s: float = 5.0
    tca_tolerance_s: float = 0.001
    radial_margin_km: float = 100.0
    relative_acceleration_bound_km_s2: float = 0.04
    max_element_age_days: float = 3.0
    max_catalogue_age_hours: float = 24.0
    max_horizon_hours: float = 72.0
    batch_size: int = 128
    use_radial_filter: bool = True

    def __post_init__(self):
        for name, value in asdict(self).items():
            if name == "use_radial_filter":
                if not isinstance(value, bool):
                    raise ValueError("use_radial_filter must be boolean")
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be finite and positive")
        if (
            self.check_distance_km > self.report_distance_km
            or self.coarse_step_s > 60
            or self.refine_step_s > min(5, self.coarse_step_s)
            or self.tca_tolerance_s >= self.refine_step_s
            or self.relative_acceleration_bound_km_s2 < 0.04
            or self.max_horizon_hours > 72
            or not isinstance(self.batch_size, int)
        ):
            raise ValueError("Unsupported screening configuration")


@dataclass(frozen=True)
class Encounter:
    conjunction: Conjunction
    other_norad_id: int
    other_name: str
    other_epoch_utc: datetime
    primary_norad_id: int
    primary_epoch_utc: datetime
    primary_raw_record_id: str
    other_age_days_at_tca: float
    primary_age_days_at_tca: float
    at_horizon_boundary: bool


@dataclass(frozen=True)
class ScreeningResult:
    start_utc: datetime
    end_utc: datetime
    status: str
    encounters: tuple[Encounter, ...]
    stats: dict
    provenance: dict
    limitations_ru: tuple[str, ...]

    @property
    def conjunctions(self):
        return tuple(e.conjunction for e in self.encounters)

    def to_dict(self):
        """JSON-ready snapshot; do not fetch sources again when exporting."""

        def convert(value):
            if isinstance(value, datetime):
                return utc(value).isoformat()
            if isinstance(value, dict):
                return {k: convert(v) for k, v in value.items()}
            if isinstance(value, (tuple, list)):
                return [convert(v) for v in value]
            return value

        return convert(asdict(self))


LIMITATIONS = (
    "Предварительный геометрический расчёт SGP4/TEME по общедоступным GP; не допуск к ВКД.",
    "Ковариаций нет: вероятность столкновения и доверительный интервал промаха не вычисляются.",
    "Ошибки элементов, сопротивление атмосферы и неизвестные манёвры не ограничены числом; малый промах не является измерением.",
    "Охват ограничен перечисленными каталогами; мелкие частицы и отсутствующие объекты не проверены.",
    "Порог проверки — правило команды, не норматив и не команда отменить или прервать ВКД.",
    "Сито высот: средние перигей/апогей с запасом; сито времени: хорда с запасом a·dt²/8. Это инженерные допущения, не доказательство полноты реальных сближений.",
    "Стыковка/отстыковка и принадлежность объектов к станции не определяются по GP; близкие сопутствующие объекты требуют ручной проверки.",
)


def chord_candidates(
    relative_positions_km, times_s, radius_km, acceleration_bound_km_s2
):
    """Distance to the swept chord, padded by the interpolation-error bound.

    For a twice differentiable vector trajectory with ||r''|| <= a, deviation
    from its endpoint chord is <= a*dt**2/8. Thus fast crossings between minute
    nodes are retained. The bound is a model assumption, not GP uncertainty.
    """
    r0 = relative_positions_km[..., :-1, :]
    dr = relative_positions_km[..., 1:, :] - r0
    dr2 = np.sum(dr * dr, axis=-1)
    fraction = np.zeros_like(dr2)
    np.divide(-np.sum(r0 * dr, axis=-1), dr2, out=fraction, where=dr2 > 0)
    # Projection on a finite segment is mathematically defined by [0,1].
    fraction = np.clip(fraction, 0, 1)
    distance = np.linalg.norm(r0 + fraction[..., None] * dr, axis=-1)
    padding = acceleration_bound_km_s2 * np.diff(times_s) ** 2 / 8
    return distance <= radius_km + padding


def _state(obj, jd, fraction, seconds):
    error, r, v = obj.satrec.sgp4(jd, fraction + seconds / 86400.0)
    if error or not np.all(np.isfinite(r)) or not np.all(np.isfinite(v)):
        raise ValueError(f"SGP4 {obj.norad_id}: error {error}")
    return np.asarray(r), np.asarray(v)


def _same_elements(a, b):
    # Catalogue IDs differ for docked vehicles/modules carrying precisely the
    # station GP. This proves identical model inputs, NOT docking status.
    fields = (
        "jdsatepoch",
        "jdsatepochF",
        "no_kozai",
        "ecco",
        "inclo",
        "nodeo",
        "argpo",
        "mo",
        "bstar",
        "ndot",
        "nddot",
    )
    return all(getattr(a.satrec, k) == getattr(b.satrec, k) for k in fields)


def _refine(primary, other, jd, fraction, left_s, right_s, cfg, *, total_s):
    """Bracket approach/recession zeroes on <=5s mesh, solve r·v=0.

    Flat/tangent dwell cases are retained by endpoint checks at horizon edges.
    Interior monotone segment edges are NOT falsely reported as new TCAs.
    """

    def state(t):
        r1, v1 = _state(primary, jd, fraction, t)
        r2, v2 = _state(other, jd, fraction, t)
        return r2 - r1, v2 - v1

    def derivative(t):
        r, v = state(t)
        return float(np.dot(r, v))

    ts = np.linspace(
        left_s, right_s, max(2, math.ceil((right_s - left_s) / cfg.refine_step_s) + 1)
    )
    derivatives = [derivative(t) for t in ts]
    roots = []
    for a, b, fa, fb in zip(ts[:-1], ts[1:], derivatives[:-1], derivatives[1:]):
        if fa < 0 < fb:
            roots.append(brentq(derivative, a, b, xtol=cfg.tca_tolerance_s))
        elif fa == 0 and fb > 0:
            roots.append(float(a))
        elif fa < 0 and fb == 0:
            roots.append(float(b))
    if left_s == 0 and derivatives[0] >= 0:
        roots.append(0.0)
    if right_s == total_s and derivatives[-1] <= 0:
        roots.append(total_s)
    out = []
    for root in sorted(set(roots)):
        r, v = state(root)
        distance = float(np.linalg.norm(r))
        if distance <= cfg.report_distance_km:
            out.append((root, distance, float(np.linalg.norm(v))))
    return out


def screen_catalogue(
    catalogues: Catalogue | tuple[Catalogue, ...],
    start_utc: datetime,
    end_utc: datetime,
    *,
    primary_norad_id=25544,
    primary: GPObject | None = None,
    decision_utc: datetime | None = None,
    config: ScreeningConfig | None = None,
) -> ScreeningResult:
    """Pure offline calculation. Pass the same primary GP as the main trajectory.

    In history decision_utc MUST be the historical cutoff. A modern receipt is
    rejected even if its elements have a historical epoch. Results preserve
    partial coverage independently from whether any encounters were detected.
    """
    began = time.perf_counter()
    cfg = config or ScreeningConfig()
    start, end = utc(start_utc), utc(end_utc)
    decision = utc(decision_utc) if decision_utc else start
    if (
        start < decision
        or not 0 < (end - start).total_seconds() <= cfg.max_horizon_hours * 3600
    ):
        raise ValueError(
            "Future horizon must be positive and <= configured maximum (72h)"
        )
    catalogues = (
        (catalogues,) if isinstance(catalogues, Catalogue) else tuple(catalogues)
    )
    stats = {
        "catalogue_rows": sum(c.input_rows for c in catalogues),
        "unique_objects": 0,
        "eligible_objects": 0,
        "after_radial_filter": 0,
        "after_temporal_filter": 0,
        "refined_intervals": 0,
        "objects_with_encounters": 0,
        "encounters": 0,
        "excluded": [],
        "shared_primary_elements": [],
        "invalid_rows": sum(len(c.rejected_rows) for c in catalogues),
    }
    provenance = {
        "model_version": MODEL_VERSION,
        "kind": Kind.OWN_CALCULATION.value,
        "frame": "TEME",
        "gravity_model": "WGS72",
        "decision_utc": decision,
        "computed_utc": datetime.now(timezone.utc),
        "config": asdict(cfg),
        "catalogues": [deepcopy(c.metadata) for c in catalogues],
        "input_audit": [
            {
                "raw_record_id": c.metadata["raw_record_id"],
                "input_rows": c.input_rows,
                "duplicate_rows": c.duplicate_rows,
                "rejected_rows": c.rejected_rows,
            }
            for c in catalogues
        ],
        "complete_public_catalogue": False,
    }

    def result(status, encounters=(), extra=()):
        stats["elapsed_s"] = time.perf_counter() - began
        return ScreeningResult(
            start,
            end,
            status,
            tuple(encounters),
            stats,
            provenance,
            LIMITATIONS + extra,
        )

    usable = []
    for catalogue in catalogues:
        available = utc(catalogue.metadata["available_utc"])
        if (
            available > decision
            or (decision - available).total_seconds()
            > cfg.max_catalogue_age_hours * 3600
        ):
            stats["excluded"].append(
                {
                    "record": catalogue.metadata["raw_record_id"],
                    "reason": "catalogue_unavailable_at_cutoff_or_stale",
                }
            )
        else:
            usable.append(catalogue)
    objects = {}
    for catalogue in usable:
        for obj in catalogue.objects:
            if (
                obj.norad_id not in objects
                or obj.epoch_utc > objects[obj.norad_id].epoch_utc
            ):
                objects[obj.norad_id] = obj
    stats["unique_objects"] = len(objects)
    primary = primary or objects.get(primary_norad_id)
    if not usable or primary is None:
        return result(
            "unavailable",
            extra=(
                "Нет пригодного каталога/элементов станции к отсечке; отсутствие сближений не установлено.",
            ),
        )
    if primary.norad_id != primary_norad_id or primary.raw_record_id not in {
        c.metadata["raw_record_id"] for c in catalogues
    }:
        raise ValueError("Primary must belong to a supplied catalogue receipt")
    if primary.raw_record_id not in {c.metadata["raw_record_id"] for c in usable}:
        return result(
            "unavailable",
            extra=(
                "Снимок выбранной основной орбиты устарел или недоступен к отсечке.",
            ),
        )
    provenance["primary"] = {
        "norad_id": primary.norad_id,
        "epoch_utc": primary.epoch_utc,
        "raw_record_id": primary.raw_record_id,
    }

    def age_ok(obj):
        return (
            max(
                abs((start - obj.epoch_utc).total_seconds()),
                abs((end - obj.epoch_utc).total_seconds()),
            )
            <= cfg.max_element_age_days * 86400
        )

    if not age_ok(primary):
        return result(
            "unavailable",
            extra=("Элементы станции за пределами объявленной давности на горизонте.",),
        )
    total = (end - start).total_seconds()
    ts = np.unique(np.append(np.arange(0, total, cfg.coarse_step_s), total))
    jd, frac = jday(
        start.year,
        start.month,
        start.day,
        start.hour,
        start.minute,
        start.second + start.microsecond / 1e6,
    )
    jds, fractions = np.full(len(ts), jd), frac + ts / 86400.0
    errors, rp, vp = primary.satrec.sgp4_array(jds, fractions)
    if (
        np.any(errors)
        or not np.all(np.isfinite(rp))
        or not np.all(np.isfinite(vp))
        or np.any(np.linalg.norm(rp, axis=1) < 6378.135)
    ):
        return result(
            "unavailable",
            extra=("SGP4 станции не покрывает горизонт над поверхностью Земли.",),
        )
    primary_min = float(np.min(np.linalg.norm(rp, axis=1)))
    primary_max = float(np.max(np.linalg.norm(rp, axis=1)))
    candidates = []
    primary_versions = [primary] + [
        o for c in usable for o in c.objects if o.norad_id == primary_norad_id
    ]
    for obj in objects.values():
        if obj.norad_id == primary_norad_id:
            continue
        shared_with = next(
            (p for p in primary_versions if _same_elements(p, obj)), None
        )
        if shared_with is not None:
            stats["shared_primary_elements"].append(
                {
                    "norad_id": obj.norad_id,
                    "name": obj.name,
                    "epoch_utc": obj.epoch_utc.isoformat(),
                    "raw_record_id": obj.raw_record_id,
                    "matched_primary_raw_record_id": shared_with.raw_record_id,
                    "matched_primary_epoch_utc": shared_with.epoch_utc.isoformat(),
                    "reason": "identical_primary_GP_not_an_independent_orbit; docking_status_not_inferred",
                }
            )
            continue
        if not age_ok(obj):
            stats["excluded"].append(
                {"norad_id": obj.norad_id, "reason": "element_age_outside_policy"}
            )
            continue
        stats["eligible_objects"] += 1
        sat = obj.satrec
        perigee = sat.a * (1 - sat.ecco) * sat.radiusearthkm
        apogee = sat.a * (1 + sat.ecco) * sat.radiusearthkm
        margin = cfg.radial_margin_km + cfg.report_distance_km
        # No radial rejection of low perigee/high drag/old elements: rapid drag
        # evolution makes an epoch-only mean perigee/apogee sieve unreliable.
        steady = (
            perigee > sat.radiusearthkm + 300
            and abs(sat.bstar) < 0.001
            and abs((start - obj.epoch_utc).total_seconds()) < 86400
        )
        if (
            cfg.use_radial_filter
            and steady
            and (perigee > primary_max + margin or apogee < primary_min - margin)
        ):
            continue
        candidates.append(obj)
    stats["after_radial_filter"] = len(candidates)
    encounters = []
    for offset in range(0, len(candidates), cfg.batch_size):
        batch = candidates[offset : offset + cfg.batch_size]
        errors, rs, vs = SatrecArray([o.satrec for o in batch]).sgp4(jds, fractions)
        valid = (
            (errors == 0)
            & np.all(np.isfinite(rs), axis=2)
            & np.all(np.isfinite(vs), axis=2)
            & (np.linalg.norm(rs, axis=2) >= 6378.135)
        )
        masks = chord_candidates(
            rs - rp, ts, cfg.report_distance_km, cfg.relative_acceleration_bound_km_s2
        )
        masks &= valid[:, :-1] & valid[:, 1:]
        for index, obj in enumerate(batch):
            if not np.all(valid[index]):
                stats["excluded"].append(
                    {
                        "norad_id": obj.norad_id,
                        "reason": "sgp4_error_or_below_earth",
                        "invalid_nodes": int(np.count_nonzero(~valid[index])),
                    }
                )
            intervals = np.flatnonzero(masks[index])
            if len(intervals):
                stats["after_temporal_filter"] += 1
            minima = []
            for i in intervals:
                stats["refined_intervals"] += 1
                try:
                    minima.extend(
                        _refine(
                            primary,
                            obj,
                            jd,
                            frac,
                            float(ts[i]),
                            float(ts[i + 1]),
                            cfg,
                            total_s=total,
                        )
                    )
                except ValueError as exc:
                    stats["excluded"].append(
                        {
                            "norad_id": obj.norad_id,
                            "reason": str(exc),
                            "interval_start_s": float(ts[i]),
                        }
                    )
            last_t = -math.inf
            for t, distance, speed in sorted(minima):
                if t - last_t <= 2 * cfg.tca_tolerance_s:
                    continue
                last_t = t
                tca = start + timedelta(seconds=t)
                other_age, primary_age = (
                    tca - obj.epoch_utc
                ).total_seconds() / 86400, (
                    tca - primary.epoch_utc
                ).total_seconds() / 86400
                conjunction = Conjunction(
                    tca,
                    f"{obj.norad_id} — {obj.name}",
                    distance,
                    speed,
                    None,
                    other_age,
                    provenance["computed_utc"],
                    "celestrak_gp_screening",
                    None,
                    obj.raw_record_id,
                    True,
                )
                encounters.append(
                    Encounter(
                        conjunction,
                        obj.norad_id,
                        obj.name,
                        obj.epoch_utc,
                        primary.norad_id,
                        primary.epoch_utc,
                        primary.raw_record_id,
                        other_age,
                        primary_age,
                        t == 0 or t == total,
                    )
                )
    encounters.sort(key=lambda e: (e.conjunction.tca_utc, e.other_norad_id))
    stats["encounters"] = len(encounters)
    stats["objects_with_encounters"] = len({e.other_norad_id for e in encounters})
    # Scope is always partial with public GP selection. Numerical success is a
    # separate field so an empty, fully screened selection is not 'no data'.
    provenance["selection_calculation_complete"] = (
        not stats["excluded"] and not stats["invalid_rows"]
    )
    status = "partial" if stats["eligible_objects"] else "unavailable"
    return result(status, encounters)


def assess_window(result: ScreeningResult, window: Window) -> MechanismAssessment:
    """Typed B integration: own calculation, threshold, no false FULL coverage.

    Window is [start, end). Boundary minimum at the horizon is a constrained
    minimum; its flag is retained in the detailed encounter list.
    """
    start = utc(window.start_utc)
    if window.duration_min <= 0:
        raise ValueError("Window duration must be positive")
    end = start + timedelta(minutes=window.duration_min)
    covered = (
        result.status != "unavailable"
        and result.start_utc <= start
        and end <= result.end_utc
    )
    encounters = [e for e in result.encounters if start <= e.conjunction.tca_utc < end]
    threshold = result.provenance["config"]["check_distance_km"]
    conditions = []
    for event in encounters:
        c = event.conjunction
        if c.miss_distance_km >= threshold:
            continue
        conditions.append(
            Condition(
                "CONJ",
                "limiting",
                f"Расчёт SGP4: {c.other_object}, промах {c.miss_distance_km:.3f} км < порога команды {threshold:g} км; TCA {c.tca_utc.isoformat()} внутри окна — ручная проверка.",
                (event.primary_raw_record_id, c.raw_record_id),
                (c.tca_utc, c.tca_utc),
                f"порог команды {threshold:g} км; не норматив",
                (
                    f"Наш расчёт GP/TEME; эпоха объекта {event.other_epoch_utc.isoformat()}, давность при TCA {event.other_age_days_at_tca:.3f} сут; эпоха станции {event.primary_epoch_utc.isoformat()}, давность {event.primary_age_days_at_tca:.3f} сут; Pc не рассчитана",
                ),
            )
        )
    coverage = Coverage.PARTIAL if covered or encounters else Coverage.NONE
    refs = tuple(
        dict.fromkeys(c["raw_record_id"] for c in result.provenance["catalogues"])
    )
    factor = FactorValue(
        "расчётных сближений с TCA в окне",
        float(len(encounters)) if covered else None,
        "шт",
        Kind.OWN_CALCULATION,
        (
            Presence.DETECTED
            if encounters
            else Presence.NOT_DETECTED if covered else Presence.UNKNOWN
        ),
        coverage,
        refs,
        f"SGP4; промах ≤ {result.provenance['config']['report_distance_km']:g} км; [начало, конец)",
        "Публичная выборка неполная; ноль не означает отсутствия риска столкновения",
    )
    notes = result.limitations_ru + (
        () if covered else ("Окно не покрыто полным горизонтом вычисления.",)
    )
    return MechanismAssessment(
        mechanism_id="conjunctions",
        mandatory=False,
        factors=(factor,),
        coverage=coverage,
        needs_check=bool(conditions),
        needs_check_reasons=tuple(c.text for c in conditions),
        conditions=tuple(conditions),
        coverage_notes=notes,
    )
