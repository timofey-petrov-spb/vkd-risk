# -*- coding: utf-8 -*-
"""Т7: настройки вне кода. Пороги читаются из config/settings.toml; опечатка в ключе — ошибка, не умолчание."""
import os
import tempfile

import pytest

import vkd.config as cfg
from vkd.windows.compare import Thresholds

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _use(path, monkeypatch):
    monkeypatch.setenv('VKD_SETTINGS', path)
    cfg.settings.cache_clear()


def test_repo_settings_file_matches_thresholds_fields(monkeypatch):
    _use(os.path.join(ROOT, 'config', 'settings.toml'), monkeypatch)
    th = Thresholds.from_settings()
    raw = cfg.section('thresholds')
    assert raw and set(raw) <= set(Thresholds.__dataclass_fields__)
    for k, v in raw.items():
        assert getattr(th, k) == pytest.approx(float(v))
    # R10 после стыка A2: оценки Kp прогона WSA-ENLIL больше нет — условие по приходу выброса
    # строится по опубликованному в уведомлении диапазону, настройка выбирает его границу
    assert cfg.section('history').get('enlil_kp_fields') is None
    assert cfg.section('history').get('enlil_publication_lag_min') is None
    assert cfg.section('history').get('cme_kp_range_bound') == 'max' and th.cme_kp_bound == 'max'
    # каждая настройка либо читается кодом, либо тест падает (Т7: мёртвых ключей нет)
    assert th.sep_valid_hours == float(cfg.section('history')['sep_valid_hours'])
    assert th.kp_max_age_min == float(raw['kp_max_age_min']) and th.fluence_equiv_ratio == float(raw['fluence_equiv_ratio'])
    ui = cfg.section('ui')
    assert len(ui['window_offsets_min']) >= 2 and all(0 < o <= ui['search_min'] for o in ui['window_offsets_min'])
    tle = cfg.section('sources')['urls']['tle']
    assert isinstance(tle, list) and len(tle) >= 2 and tle[0].startswith('https://celestrak.org/')   # резервная цепочка
    cfg.settings.cache_clear()


def test_missing_file_gives_code_defaults_and_unknown_key_is_an_error(monkeypatch):
    _use(os.path.join(tempfile.gettempdir(), 'vkd_no_such_settings.toml'), monkeypatch)
    assert Thresholds.from_settings() == Thresholds()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'bad.toml')
        open(p, 'w', encoding='utf-8').write('[thresholds]\nkp_chek = 7.0\n')
        _use(p, monkeypatch)
        with pytest.raises(ValueError, match='kp_chek'):
            Thresholds.from_settings()
        p2 = os.path.join(d, 'ok.toml')
        open(p2, 'w', encoding='utf-8').write('[thresholds]\nkp_check = 6.0\nsaa_B_threshold_nT = 25000\n')
        _use(p2, monkeypatch)
        th = Thresholds.from_settings()
        assert th.kp_check == 6.0 and th.saa_B_threshold_nT == 25000.0 and th.e_min_MeV == 30.0
    cfg.settings.cache_clear()
