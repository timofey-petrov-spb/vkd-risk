# -*- coding: utf-8 -*-
"""Типы стыка между двумя половинами проекта. CONTRACT.md v3.1, раздел 6.

ВЕРСИЯ 2.1 — правки по разбору Codex 19.09 (journal/friend.md, пп. 1, 2, 5, 14):
  * EventInterval: kind, valid_from_utc, valid_to_utc, is_simulated при
    сохранении физического kind_of_event;
  * Manifest: реестр по выпускам source_versions[source_id][raw_record_id]
    → metadata (URL, версия, SHA-256, публикация, доказательство доступности,
    путь raw), schema_version, effective_config;
  * пять исходов Recommendation сохранены, «все требуют проверки» отличается
    от «недостаточно данных»;
  * наличие воздействия и полнота данных — два разных поля.
ВЕРСИЯ 2.2 (19.09, область Б, обратно совместима): Condition и поля
MechanismAssessment.conditions / coverage_notes с умолчаниями — стык Codex не меняется.
Заморозка — после записи Codex «согласовано» в журнале.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

SCHEMA_VERSION = '2.2'


class Kind(str, Enum):
    OBSERVATION = "observation"
    EXTERNAL_FORECAST = "external_forecast"
    OWN_CALCULATION = "own_calculation"


class Presence(str, Enum):
    DETECTED = "detected"
    NOT_DETECTED = "not_detected"
    UNKNOWN = "unknown"


class Coverage(str, Enum):
    FULL = "full"
    PARTIAL = "partial"
    NONE = "none"


class MagMethod(str, Enum):
    DIPOLE = "dipole"
    IGRF_TRACE = "igrf_trace"
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
    settings: dict = field(default_factory=dict)      # пороги, каналы, модель MMOD
    scenario_id: Optional[str] = None                 # режим «Что если»: идентификатор сценария
    work_delay_min: int = 0                           # задержка работ — свойство запроса, не событие


@dataclass(frozen=True)
class RecordMeta:
    """Метаданные одного выпуска источника (реестр A1)."""
    url: Optional[str]
    version: Optional[str]
    sha256: Optional[str]
    published_utc: Optional[datetime]
    availability_proof: Optional[str]  # чем доказана доступность к отсечке; None = не доказана
    raw_path: Optional[str]
    fetched_utc: Optional[datetime]
    quality: str = "unknown"


@dataclass(frozen=True)
class Manifest:
    """Единый снимок расчёта: экран, объяснения и выгрузка ссылаются на него."""
    schema_version: str
    algorithm_version: str
    request: Request
    effective_config: dict                     # все настройки с умолчаниями, как применены
    source_versions: dict                      # source_id -> {raw_record_id -> RecordMeta-as-dict}
    coverage_map: dict                         # source_id -> периоды покрытия и пропуски
    raw_record_ids: tuple[str, ...]
    is_simulated: bool = False                 # сценарий «Что если» помечает весь результат


# ---------------------------------------------------------------------------
# Codex -> Claude
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TrajectoryMeta:
    source_id: str
    method: str                        # "sgp4" | "oem_interp"
    frame: str
    epoch_utc: Optional[datetime]
    coverage_from_utc: datetime
    coverage_to_utc: datetime
    created_utc: Optional[datetime]    # CREATION_DATE — создание, не публикация
    available_utc: Optional[datetime]  # подтверждённая доступность; None = не доказана
    fetched_utc: datetime
    is_reconstruction: bool
    field_model: str                   # "IGRF-13" в строгой линии 2024; "IGRF-14" — реконструкция


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
    mag_status: str                    # "ok" | "outside_model" | "approximation" | "inconsistent_BB0"
    in_saa: Optional[bool]


@dataclass(frozen=True)
class EnvironmentSample:
    t_utc: datetime
    channel_id: str
    value: Optional[float]
    unit: str
    source_id: str
    kind: Kind
    published_utc: Optional[datetime]  # None ⇒ непригоден для строгого replay
    valid_from_utc: Optional[datetime]
    valid_to_utc: Optional[datetime]
    fetched_utc: datetime
    quality: str                       # "final" | "preliminary" | "model" | "unknown"
    raw_record_id: str
    version: Optional[str] = None


@dataclass(frozen=True)
class EventInterval:
    """Событие с раздельными временами: физические границы, действие сообщения, публикация."""
    event_id: str
    kind_of_event: str                 # физический тип: "SEP" | "GST" | "CME_arrival" | "FLR" | ...
    kind: Kind                         # наблюдение / внешний прогноз / наш расчёт
    start_utc: Optional[datetime]      # физическое начало
    end_utc: Optional[datetime]        # физический конец
    start_uncertain: bool
    end_uncertain: bool
    valid_from_utc: Optional[datetime] # действие сообщения (WARNING/WATCH)
    valid_to_utc: Optional[datetime]
    source_id: str
    published_utc: Optional[datetime]
    raw_record_id: str
    is_simulated: bool = False         # синтетический вход сценария; никогда не попадает в live-кеш и replay
    note: str = ""


@dataclass(frozen=True)
class Conjunction:
    tca_utc: datetime
    other_object: str
    miss_distance_km: float
    relative_speed_km_s: float
    max_probability: Optional[float]
    dse_days: Optional[float]
    run_utc: Optional[datetime]
    source_id: str
    published_utc: Optional[datetime]
    raw_record_id: str
    table_truncated: bool


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
    record_ids: tuple[str, ...]
    rule_applied: str
    limits_note: str = ""
    horizon_utc: Optional[datetime] = None     # до какого момента у канала есть данные/прогноз


@dataclass(frozen=True)
class Condition:
    """Условие дополнительной проверки (CONTRACT v3.1, раздел 4, п. 2) в структурном виде:
    текст — для экрана, остальное — для карточки объяснения (О4: период события, источники,
    время публикации) без разбора строки. needs_check_reasons остаются текстами условий."""
    kind: str                          # "SEP" | "GST" | "GOES" | "KP" | "CONJ"
    severity: str                      # "critical" (приоритетное, S ≥ 3) | "limiting" (предупреждение)
    text: str                          # полный текст условия (совпадает с элементом needs_check_reasons)
    event_ids: tuple[str, ...] = ()    # записи источников, давшие условие
    interval_utc: tuple = ()           # (начало, конец) действия условия; конец может быть принят по конвенции
    level_note: str = ''               # уровень: «S3», «Kp до 8», «уровень не указан»
    sources_ru: tuple[str, ...] = ()   # по одной строке на источник сигнала: что, когда, публикация
    is_simulated: bool = False


@dataclass(frozen=True)
class CoverageGap:
    """Непокрытый участок окна в МИНУТАХ с причиной (разбор Codex п. 3).

    Доля точек и доля времени — разные величины: узлы трассы считаются штуками, окно
    измеряется временем. Здесь всегда время: сумма `minutes` всех пропусков плюс покрытое
    время даёт длительность окна ровно (проверяется `tests/test_coverage_gaps.py`).
    Нулём пропуск не заполняется ни здесь, ни в интеграле."""
    reason_ru: str                     # почему модели нет именно на этом участке
    minutes: float                     # длительность участка, мин


@dataclass(frozen=True)
class MechanismAssessment:
    mechanism_id: str
    mandatory: bool
    factors: tuple[FactorValue, ...]
    coverage: Coverage
    needs_check: bool
    needs_check_reasons: tuple[str, ...] = ()
    priority: bool = False             # S ≥ 3: приоритетное предупреждение, срочная проверка специалистом
    conditions: tuple[Condition, ...] = ()      # те же условия в структурном виде
    coverage_notes: tuple[str, ...] = ()        # чего именно не хватает (по каналам), если покрытие не полное
    # Подмножество `coverage_notes`: каналы, у которых данных нет ВОВСЕ. Покрытие механизма —
    # минимум по каналам, поэтому один пустой канал делает механизм NONE, а остальные заметки
    # при этом продолжают говорить о ЧАСТИЧНОМ покрытии. Без этого разделения отказ печатался
    # рядом с «модель ОСТ покрывает 83,3 % времени окна» — числом, которое его опровергает.
    blocking_notes: tuple[str, ...] = ()
    # Покрытие окна ВРЕМЕНЕМ по целевой величине механизма (флюенс для космопогоды):
    # None — величина не считалась. Вместе с `coverage_gaps` это и есть объявленная
    # область вывода: по какой доле окна получено число и чего в ней нет.
    coverage_fraction: Optional[float] = None
    coverage_gaps: tuple[CoverageGap, ...] = ()


@dataclass(frozen=True)
class WindowAssessment:
    window: Window
    mechanisms: tuple[MechanismAssessment, ...]
    coverage_declared: tuple[str, ...]
    coverage_missing: tuple[str, ...]


@dataclass(frozen=True)
class Recommendation:
    """Пять исходов: preferred | trade_off | equivalent | insufficient | all_need_check."""
    preferred: Optional[Window]
    verdict: str
    rule_applied: str
    per_mechanism_comparison: dict
    reasons: tuple[str, ...]
    missing: tuple[str, ...] = ()
    tolerance_basis: str = ""
    is_simulated: bool = False
    # ОБЪЯВЛЕННАЯ ОБЛАСТЬ ВЫВОДА (разбор Codex п. 1, второй вариант — решение владельца 19.09).
    # Вердикт при частичном покрытии обязательной линии остаётся, но НЕ выдаётся за заключение
    # о полном риске ВКД: рядом печатается, по каким факторам сделан вывод, какая доля окна
    # покрыта моделью, чего в ней нет и чего вывод не означает. Ни одно число, ни один порог
    # и ни одна модель ради вердикта не меняются — меняется только объявленная область.
    scope_ru: str = ""                            # короткая фраза: доля окна и чего вывод не означает
    scope_detail_ru: str = ""                     # та же область подробно: имена факторов и пропуски по причинам
    scope_facts: tuple[tuple[str, str], ...] = () # то же по пунктам: (что, значение) — в снимок и выгрузку
