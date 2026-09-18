# -*- coding: utf-8 -*-
"""Типы стыка между двумя половинами проекта. CONTRACT.md, раздел 6.

ЗАМОРОЖЕНО. Изменение — только по согласованию вдвоём с записью в журналах.

Единицы входят в имена полей (CONTRACT.md, раздел 1). Все моменты — UTC,
timezone-aware datetime. Пропуск данных выражается None, а не нулём:
«пропуск данных ≠ нулевой риск» (CONTRACT.md, правило 7).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Kind(str, Enum):
    """Происхождение величины. Показывается пользователю всегда (О4)."""
    OBSERVATION = "observation"            # наблюдение источника
    EXTERNAL_FORECAST = "external_forecast"  # чужой прогноз, применённый нами
    OWN_CALCULATION = "own_calculation"    # наш расчёт


class DataStatus(str, Enum):
    """Три состояния, не два (CONTRACT.md, правило 7)."""
    PRESENT = "present"          # воздействие выявлено
    ABSENT = "absent"            # воздействие не выявлено, данные полные
    INSUFFICIENT = "insufficient"  # данных недостаточно для оценки


# ---------------------------------------------------------------------------
# Место первое: Codex -> Claude
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TrajectoryPoint:
    """Положение станции и магнитные координаты в момент t_utc.

    Источник орбиты и эпоха элементов идут отдельно, в TrajectoryMeta:
    постановка требует показывать источник, эпоху и давность (Т2).
    """
    t_utc: datetime
    lat_deg: float
    lon_deg: float
    alt_km: float
    L: float                 # параметр Мак-Илвейна, безразмерный
    B_over_B0: float         # отношение поля к экваториальному на той же L
    cutoff_GV: float         # вертикальная жёсткость обрезания, ГВ
    in_saa: bool             # внутри Южно-Атлантической аномалии по порогу поля


@dataclass(frozen=True)
class TrajectoryMeta:
    source_id: str           # например "celestrak_gp"
    tle_epoch_utc: datetime  # эпоха элементов — не время публикации
    fetched_utc: datetime    # когда получили
    is_reconstruction: bool  # True, если орбита периода не подтверждена на момент прогноза
    propagator: str = "sgp4"


@dataclass(frozen=True)
class EnvironmentSample:
    """Одно значение одного источника с тремя временами, ведущимися раздельно.

    Постановка: «время прихода события, срок действия предупреждения и момент
    его публикации учитываются раздельно». Отсюда три поля времени.
    """
    t_utc: datetime                    # к какому моменту относится значение
    value: Optional[float]             # None = пропуск, не ноль
    unit: str
    source_id: str
    published_utc: Optional[datetime]  # None = время публикации неизвестно ⇒ непригоден для строгого replay
    valid_from_utc: Optional[datetime]
    valid_to_utc: Optional[datetime]
    kind: Kind
    version: Optional[str] = None      # версия записи источника, если есть (DONKI versionId)


@dataclass(frozen=True)
class Conjunction:
    """Сближение станции с отслеживаемым объектом (SOCRATES).

    Это предупреждение о станции. Вероятность попадания фрагмента в космонавта
    отсюда НЕ следует (CONTRACT.md, раздел 9).
    """
    tca_utc: datetime
    other_object: str
    miss_distance_km: float
    relative_speed_km_s: float
    max_probability: Optional[float]   # как выдаёт источник, без пересчёта
    source_id: str
    published_utc: Optional[datetime]


# ---------------------------------------------------------------------------
# Место второе: Claude -> интерфейс
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Window:
    start_utc: datetime
    duration_min: int        # 60…480 по постановке


@dataclass(frozen=True)
class FactorValue:
    """Одна величина из CONTRACT.md, раздел 3, с происхождением и статусом."""
    name: str
    value: Optional[float]
    unit: str
    kind: Kind
    status: DataStatus
    source_ids: tuple[str, ...] = ()
    note: str = ""           # ограничение или обоснование уверенности


@dataclass(frozen=True)
class WindowAssessment:
    window: Window
    mechanism_1: tuple[FactorValue, ...]   # космическая погода
    mechanism_2: tuple[FactorValue, ...]   # микрометеороиды и мусор
    minutes_in_saa: Optional[float]
    minutes_high_latitude: Optional[float]
    status_1: DataStatus
    status_2: DataStatus


@dataclass(frozen=True)
class Recommendation:
    """Рекомендация ИЛИ явный отказ. Правило из CONTRACT.md, раздел 4."""
    preferred: Optional[Window]        # None = оснований недостаточно или равнозначны
    verdict: str                       # "preferred" | "equivalent" | "insufficient" | "all_need_check"
    rule_applied: str                  # какой пункт правила сработал, текстом
    reasons: tuple[str, ...]           # какие факторы и интервалы повлияли
    missing: tuple[str, ...] = ()      # чего не хватает, если insufficient


@dataclass(frozen=True)
class CutoffPolicy:
    """Исторический режим (CONTRACT.md, раздел 10)."""
    cutoff_utc: Optional[datetime]     # None = разбор по всему архиву; иначе строгий прогноз из прошлого
    source_versions: dict = field(default_factory=dict)
    algorithm_version: str = "0.1.0"
