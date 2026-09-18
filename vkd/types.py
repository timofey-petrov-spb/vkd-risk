# -*- coding: utf-8 -*-
"""Типы стыка между двумя половинами проекта. CONTRACT.md v3, раздел 6.

ВЕРСИЯ 2 — по разбору Codex (journal/friend.md, 19.09). Не заморожена:
замораживается после записи Codex «согласовано» в журнале.

Принципы: единицы в именах; все времена — timezone-aware UTC; отсутствие
величины — None, не ноль; наличие воздействия и полнота данных — два разных
поля; каждая величина знает, из каких записей она получена.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Kind(str, Enum):
    OBSERVATION = "observation"
    EXTERNAL_FORECAST = "external_forecast"
    OWN_CALCULATION = "own_calculation"


class Presence(str, Enum):
    """Наличие воздействия — отдельно от полноты данных."""
    DETECTED = "detected"
    NOT_DETECTED = "not_detected"
    UNKNOWN = "unknown"


class Coverage(str, Enum):
    """Полнота данных по окну — отдельно от наличия воздействия."""
    FULL = "full"
    PARTIAL = "partial"
    NONE = "none"


class MagMethod(str, Enum):
    DIPOLE = "dipole"            # центрированный диполь: исследовательское приближение
    IGRF_TRACE = "igrf_trace"    # трассировка силовой линии по IGRF
    NONE = "none"


# ---------------------------------------------------------------------------
# Запрос и манифест
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Request:
    mode: str                          # "live" | "history_review" | "history_forecast"
    eva_start_utc: datetime
    duration_min: int                  # 60…480
    search_period_min: int             # 0…1440
    cutoff_utc: Optional[datetime]     # обязателен для history_forecast
    disabled_sources: tuple[str, ...] = ()
    settings: dict = field(default_factory=dict)   # пороги, каналы, модель MMOD


@dataclass(frozen=True)
class Manifest:
    """Единый снимок расчёта: экран, объяснения и выгрузка ссылаются на него."""
    request: Request
    algorithm_version: str
    source_versions: dict              # source_id -> версия/эпоха/etag
    fetched_utc: dict                  # source_id -> когда получено
    raw_record_ids: tuple[str, ...]    # все сырые записи, вошедшие в расчёт
    coverage_map: dict                 # source_id -> периоды покрытия и пропуски


# ---------------------------------------------------------------------------
# Codex -> Claude
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TrajectoryMeta:
    source_id: str                     # "celestrak_gp" | "nasa_oem" | ...
    method: str                        # "sgp4" | "oem_interp"
    frame: str                         # "TEME" | "J2000" | "ITRF"
    epoch_utc: Optional[datetime]      # эпоха элементов (SGP4); None для OEM
    coverage_from_utc: datetime
    coverage_to_utc: datetime
    created_utc: Optional[datetime]    # CREATION_DATE — создание, не публикация
    available_utc: Optional[datetime]  # подтверждённая доступность; None = не доказана
    fetched_utc: datetime
    is_reconstruction: bool            # True, если available_utc не доказана к отсечке
    field_model: str                   # "IGRF-13" | "IGRF-14" — для строгого режима 2024 помечать


@dataclass(frozen=True)
class TrajectoryPoint:
    t_utc: datetime
    lat_deg: float
    lon_deg: float
    alt_km: float
    B_nT: Optional[float]
    L: Optional[float]
    B_over_B0: Optional[float]
    cutoff_GV: Optional[float]
    mag_method: MagMethod
    mag_status: str                    # "ok" | "outside_model" | "approximation"
    in_saa: Optional[bool]             # None, если B_nT отсутствует


@dataclass(frozen=True)
class EnvironmentSample:
    """Одно значение одного канала с раздельными временами и ссылкой на запись."""
    t_utc: datetime                    # к какому моменту относится
    channel_id: str                    # например "goes_p_ge10MeV", "kp", "s_scale"
    value: Optional[float]
    unit: str
    source_id: str
    kind: Kind
    published_utc: Optional[datetime]  # None ⇒ непригоден для строгого replay
    valid_from_utc: Optional[datetime]
    valid_to_utc: Optional[datetime]
    fetched_utc: datetime
    quality: str                       # "final" | "preliminary" | "model" | "unknown"
    raw_record_id: str                 # ссылка на сырую запись в кеше
    version: Optional[str] = None


@dataclass(frozen=True)
class EventInterval:
    """Событие с возможно неизвестными границами."""
    event_id: str
    kind_of_event: str                 # "SEP" | "GST" | "CME_arrival" | ...
    start_utc: Optional[datetime]
    end_utc: Optional[datetime]
    start_uncertain: bool
    end_uncertain: bool
    source_id: str
    published_utc: Optional[datetime]
    raw_record_id: str
    note: str = ""


@dataclass(frozen=True)
class Conjunction:
    """Внешний ПРОГНОЗ сближения станции (SOCRATES), не наблюдение."""
    tca_utc: datetime
    other_object: str
    miss_distance_km: float
    relative_speed_km_s: float
    max_probability: Optional[float]
    dse_days: Optional[float]          # эпоха элементов → TCA; не возраст данных
    run_utc: Optional[datetime]        # момент прогона SOCRATES
    source_id: str
    published_utc: Optional[datetime]
    raw_record_id: str
    table_truncated: bool              # MAX=25 ⇒ «других нет» утверждать нельзя


# ---------------------------------------------------------------------------
# Claude -> интерфейс
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Window:
    start_utc: datetime
    duration_min: int


@dataclass(frozen=True)
class FactorValue:
    name: str
    value: Optional[float]
    unit: str
    kind: Kind
    presence: Presence
    coverage: Coverage
    record_ids: tuple[str, ...]        # какие записи и версии вошли
    rule_applied: str                  # какое правило/модель применено
    limits_note: str = ""              # границы применимости, обоснование уверенности


@dataclass(frozen=True)
class MechanismAssessment:
    mechanism_id: str                  # "spaceweather" | "mmod_stat" | "conjunctions"
    mandatory: bool
    factors: tuple[FactorValue, ...]
    coverage: Coverage
    needs_check: bool                  # условие дополнительной проверки сработало
    needs_check_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class WindowAssessment:
    window: Window
    mechanisms: tuple[MechanismAssessment, ...]
    coverage_declared: tuple[str, ...]     # какие линии учтены — заявление об охвате
    coverage_missing: tuple[str, ...]      # какие не учтены и почему


@dataclass(frozen=True)
class Recommendation:
    preferred: Optional[Window]
    verdict: str                       # "preferred" | "trade_off" | "equivalent" | "insufficient" | "all_need_check"
    rule_applied: str
    per_mechanism_comparison: dict     # mechanism_id -> какое окно лучше и почему
    reasons: tuple[str, ...]
    missing: tuple[str, ...] = ()
    tolerance_basis: str = ""          # из чего взят допуск равнозначности
