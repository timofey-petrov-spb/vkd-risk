# -*- coding: utf-8 -*-
"""Воспроизводимость (Т8) и временная честность на уровне сервиса (Т4): исторический расчёт
при одинаковых входах даёт одинаковый снимок; записи, дописанные в архив после отсечки,
строгий снимок не меняют."""
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
    """Без сети: живые источники в истории не нужны (иначе тест зависел бы от отказов CelesTrak)."""
    from tests.test_integration import _fetched
    t0 = datetime(2024, 5, 20, 12, 0, tzinfo=timezone.utc)
    a = run('history_forecast', t0, 360, 1440, [0, 480], tle_override_path=TLE, fetched=_fetched(), now=t0)
    b = run('history_forecast', t0, 360, 1440, [0, 480], tle_override_path=TLE, fetched=_fetched(), now=t0)
    assert _strip(a.S) == _strip(b.S)
    assert a.S['recommendation']['verdict'] == b.S['recommendation']['verdict']


def test_robustness_spread_independent_of_saved_tolerance():
    """Дефект, найденный повтором примера: допуск в порогах не должен менять разброс."""
    from tests.test_integration import _fetched
    from vkd.windows.compare import Thresholds
    t0 = datetime(2024, 5, 20, 12, 0, tzinfo=timezone.utc)
    a = run('history_forecast', t0, 360, 1440, [0, 480], tle_override_path=TLE, thresholds=Thresholds(), fetched=_fetched(), now=t0)
    b = run('history_forecast', t0, 360, 1440, [0, 480], tle_override_path=TLE, thresholds=Thresholds(equiv_tol_min=39.0), fetched=_fetched(), now=t0)
    assert a.rob.diff_spread_min == b.rob.diff_spread_min and a.rob.tol_min == b.rob.tol_min
    assert a.S['recommendation']['verdict'] == b.S['recommendation']['verdict']


def test_tolerance_is_spread_of_difference_and_consistent_with_grid():
    """DEMO-1/О3-2: допуск — разброс РАЗНОСТИ минут между окнами, а не абсолютных минут одного окна;
    «равнозначны» и «выбор устойчив» не могут стоять рядом при устойчивом порядке окон вне допуска."""
    from tests.test_integration import _fetched
    t0 = datetime(2024, 6, 25, 12, 0, tzinfo=timezone.utc)
    r = run('history_forecast', t0, 360, 1440, [0, 480], fetched=_fetched(), now=t0)
    rob = r.rob
    assert rob.diff_by_thr and rob.tol_min == max(1.0, max(rob.diff_by_thr.values()) - min(rob.diff_by_thr.values()))
    assert 'разброс разности минут' in r.rec.tolerance_basis
    if r.rec.verdict == 'equivalent':
        # равнозначность допустима только если на сетке с тем же допуском порядок не даёт одного победителя
        assert not rob.stable or all(v is None for v in rob.preferred_starts.values())
    if r.rec.verdict == 'preferred' and rob.stable:
        assert all(v == r.rec.preferred.start_utc.isoformat() for v in rob.preferred_starts.values())


def test_future_archive_records_do_not_change_strict_snapshot(monkeypatch):
    """Inject an actual late provider release at the real A2 registry boundary."""
    import vkd.history.bundle as hb
    from vkd.sources.registry import SourceRegistry
    from tests.test_integration import _fetched
    reg = SourceRegistry(ROOT)
    sid = 'nasa_donki_notification'
    late = next(r for r in reg.records(sid) if r['release_id'] == '20240510-AL-004')
    class FilteredRegistry:
        source_ids = reg.source_ids
        def records(self, source):
            return [r for r in reg.records(source) if r['raw_record_id'] != late['raw_record_id']]
        def raw_bytes(self, rid):
            return reg.raw_bytes(rid)
    t0 = datetime(2024, 5, 10, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(hb, '_registry', lambda root, supplied: FilteredRegistry())
    before = run('history_forecast', t0, 360, 720, [0, 240], fetched=_fetched(), now=t0)
    monkeypatch.setattr(hb, '_registry', lambda root, supplied: reg)
    after = run('history_forecast', t0, 360, 720, [0, 240], fetched=_fetched(), now=t0)
    assert before.S['windows'] == after.S['windows']
    assert before.S['recommendation'] == after.S['recommendation']
    assert any(late['raw_record_id'] in s and 'not_available_at_cutoff' in s for s in after.excluded)
    assert not any(e.raw_record_id == late['raw_record_id'] for e in after.events)
    assert late['raw_record_id'] not in after.raw_records
    review = run('history_review', t0, 360, 720, [0, 240], fetched=_fetched(), now=t0)
    assert any(e.raw_record_id == late['raw_record_id'] for e in review.events)
