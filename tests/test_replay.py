# -*- coding: utf-8 -*-
"""Воспроизводимость (Т8): исторический расчёт при одинаковых входах даёт одинаковый снимок."""
import os
from datetime import datetime, timezone

from app.compute import run

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TLE = os.path.join(ROOT, 'data', 'spaceweather', 'iss.tle')


def _strip(S):
    """Убираем всё, что законно зависит от момента запуска."""
    S = dict(S)
    S.pop('computed_utc', None)
    S['sources'] = {k: {kk: vv for kk, vv in v.items() if kk not in ('fetched_utc', 'age_min', 'age_h', 'status')} for k, v in S['sources'].items()}
    S['trajectory_meta'] = {k: v for k, v in S['trajectory_meta'].items() if k not in ('fetched_utc', 'tle_fetch_status')}
    return S


def test_history_forecast_is_deterministic_with_pinned_tle():
    t0 = datetime(2024, 5, 20, 12, 0, tzinfo=timezone.utc)
    a = run('history_forecast', t0, 360, 1440, [0, 480], tle_override_path=TLE)
    b = run('history_forecast', t0, 360, 1440, [0, 480], tle_override_path=TLE)
    assert _strip(a.S) == _strip(b.S)
    assert a.S['recommendation']['verdict'] == b.S['recommendation']['verdict']


def test_robustness_spread_independent_of_saved_tolerance():
    """Дефект, найденный повтором примера: допуск в порогах не должен менять разброс."""
    from vkd.windows.compare import Thresholds
    t0 = datetime(2024, 5, 20, 12, 0, tzinfo=timezone.utc)
    a = run('history_forecast', t0, 360, 1440, [0, 480], tle_override_path=TLE, thresholds=Thresholds())
    b = run('history_forecast', t0, 360, 1440, [0, 480], tle_override_path=TLE, thresholds=Thresholds(equiv_tol_min=39.0))
    assert a.rob.saa_spread_min == b.rob.saa_spread_min
    assert a.S['recommendation']['verdict'] == b.S['recommendation']['verdict']
