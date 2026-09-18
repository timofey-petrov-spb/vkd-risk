# -*- coding: utf-8 -*-
"""Настройки вне кода (Т7): config/settings.toml, путь переопределяется переменной VKD_SETTINGS.

Значения в коде — только умолчания на случай отсутствия файла или ключа. Всё, что
применено в расчёте, попадает в снимок (request.thresholds, effective_config), поэтому
изменение файла никогда не бывает «молчаливым». Секретов в файле нет и быть не должно
(источники открытые); ключи, если появятся, — только через переменные окружения.
"""
from __future__ import annotations

import os
import tomllib
from functools import lru_cache

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(ROOT, 'config', 'settings.toml')


def settings_path() -> str:
    return os.environ.get('VKD_SETTINGS', DEFAULT_PATH)


@lru_cache(maxsize=1)
def settings() -> dict:
    """Весь файл настроек; пустой словарь, если файла нет. Ошибка разбора — исключение,
    а не тихие умолчания: сломанный файл настроек должен быть виден сразу."""
    p = settings_path()
    if not os.path.exists(p):
        return {}
    with open(p, 'rb') as fh:
        return tomllib.load(fh)


def section(name: str) -> dict:
    v = settings().get(name, {})
    return dict(v) if isinstance(v, dict) else {}
