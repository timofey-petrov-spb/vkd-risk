# -*- coding: utf-8 -*-
"""Стык Б ↔ А после слияния codex/a1-sources: орбита A3 через мост, прогнозы NOAA A2,
метеороиды по трассе. Без сети: источники передаются как отключённые."""
import os
from datetime import datetime, timedelta, timezone

import pytest

from app.compute import run
from vkd.orbit.trajectory import satellite_from_tle
from vkd.sources import Fetch, noaa_latest
from vkd.types import EnvironmentSample, Kind

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TLE = os.path.join(ROOT, 'data', 'orbit', 'iss.tle')
UTC = timezone.utc


def pinned_noaa(tmp_dir, now=None):
    """Живой прогноз NOAA из ЗАКРЕПЛЁННОГО ответа: сети нет, байты — выпуск из реестра A1.

    Возвращает то же, что noaa_latest(): (samples, raw, Fetch). Используется тестами
    текущего режима — живой запрос в тестах недопустим (CONTRACT §8).
    """
    from unittest.mock import Mock
    from vkd.sources.registry import SourceRegistry

    class _Response:
        def __init__(self, raw):
            self.raw, self.status_code, self.headers = raw, 200, {}

        def iter_content(self, size):
            for i in range(0, len(self.raw), size):
                yield self.raw[i:i + size]

        def close(self):
            pass

    record = SourceRegistry(ROOT).records('noaa_ngdc_3day_forecast')[0]
    raw = open(os.path.join(ROOT, record['raw_path']), 'rb').read()
    # момент запроса — час после выпуска бюллетеня: иначе закреплённый ответ 2024 года
    # честно объявляется устаревшим и значения не даёт
    now = now or datetime.fromisoformat(record['published_utc'].replace('Z', '+00:00')) + timedelta(hours=1)
    return noaa_latest(cache_dir=tmp_dir, now=now, transport=Mock(return_value=_Response(raw)))


def _fetched(tle_text=None, goes_at=None):
    off = lambda sid: Fetch(sid, False, False, None, None, 'отключено в тесте', None, None)
    f_tle = Fetch('celestrak_gp', False, True, datetime(2026, 9, 18, 21, 8, tzinfo=UTC), None, 'снимок A3 (тест)', tle_text, TLE)
    goes, goes_raw, f_goes = None, {}, off('noaa_swpc_goes')
    if goes_at is not None:      # текущий режим: без наблюдения GOES линия космопогоды не покрыта — это по договору
        goes = EnvironmentSample(goes_at - timedelta(minutes=5), 'goes_p10', 0.4, 'pfu', 'noaa_swpc_goes', Kind.OBSERVATION,
                                 goes_at - timedelta(minutes=5), None, None, goes_at, 'preliminary', 'goes_test')
        goes_raw = {'goes_test': {'flux': 0.4}}
        f_goes = Fetch('noaa_swpc_goes', True, False, goes_at, 5.0, 'тестовое наблюдение', None, None)
    # живой прогноз NOAA в тестах исключён как источник: сети быть не должно ('off' не читает
    # ни сеть, ни кеш). Отдельный тест C6 подаёт закреплённый ответ через pinned_noaa().
    return ((goes, goes_raw, f_goes), (None, {}, off('gfz_kp')), (tle_text, f_tle),
            noaa_latest(disabled='off'))


def test_live_orbit_through_bridge_with_pinned_tle():
    epoch = satellite_from_tle(open(TLE, 'rb').read()).epoch.utc_datetime()
    t0 = (epoch + timedelta(days=1)).replace(second=0, microsecond=0)
    r = run('live', t0, 360, 1440, [0, 480], fetched=_fetched(open(TLE, encoding='utf-8').read(), goes_at=t0), now=t0)
    assert r.meta is not None and r.meta.method == 'sgp4' and r.meta.field_model == 'IGRF-14'
    assert not r.meta.is_reconstruction                 # TLE получен до момента расчёта — не реконструкция
    assert len(r.traj) == 1800 + 1                      # конец горизонта включён (A3)
    assert r.S['trajectory_meta']['strictness'] == 'strict'
    assert r.S['sources']['orbit']['source_id'] == 'celestrak_gp'
    assert 'celestrak_gp:25544:' in ''.join(r.S['trajectory_meta']['provenance']['records'])
    assert r.rec.verdict != 'insufficient', r.rec.missing
    # метеороиды по трассе: N порядка контрольного 5,6e-7 на 6 ч, покрытие полное
    m2 = [m for m in r.assessments[0].mechanisms if m.mechanism_id == 'mmod_stat'][0]
    assert m2.coverage.value == 'full' and 4e-7 < m2.factors[0].value < 8e-7


def test_stale_tle_gives_no_orbit_and_insufficient_not_a_stub():
    epoch = satellite_from_tle(open(TLE, 'rb').read()).epoch.utc_datetime()
    t0 = (epoch + timedelta(days=10)).replace(second=0, microsecond=0)
    r = run('live', t0, 360, 1440, [0, 480], fetched=_fetched(open(TLE, encoding='utf-8').read()), now=t0)
    assert r.meta is None and r.traj == []
    assert r.rec.verdict == 'insufficient'
    assert any('орбита недоступна' in x for x in r.rec.missing)
    assert r.S['trajectory_meta']['strictness'] == 'unavailable'


def test_history_forecast_gannon_uses_oem_and_noaa_forecast_before_cutoff():
    t0 = datetime(2024, 5, 10, 12, 0, tzinfo=UTC)
    r = run('history_forecast', t0, 360, 1440, [0, 900], fetched=_fetched(), now=t0)
    assert r.meta.method == 'oem_interp' and r.meta.source_id == 'nasa_jsc_oem' and r.meta.field_model == 'IGRF-13'
    assert r.meta.is_reconstruction and r.S['trajectory_meta']['strictness'] == 'declared_reconstruction'
    assert r.meta.created_utc <= t0                     # OEM создан до отсечки
    fc = {l['channel']: l for l in r.S['forecasts']}
    assert fc['kp_forecast']['release_id'] == '202405100030three_day_forecast' and fc['kp_forecast']['status'] == 'full'
    assert fc['proton_prob_daily']['release_id'] == '20240509daypre'
    # окно 2 (11 мая 03:00–09:00): прогноз Kp 8,33 ≥ 7 → условие проверки со словом «прогноз»
    a2 = r.assessments[1]
    kpf = next(f for f in a2.mechanisms[0].factors if f.name.startswith('прогноз Kp NOAA'))
    assert kpf.value == pytest.approx(8.33) and kpf.kind.value == 'external_forecast'
    assert any('прогноз NOAA' in x for x in a2.mechanisms[0].needs_check_reasons)
    # окно 1 (10 мая 12:00–18:00): прогноз Kp до 4,33 — условия нет
    a1 = r.assessments[0]
    assert not any('прогноз NOAA' in x for x in a1.mechanisms[0].needs_check_reasons)
    assert r.rec.verdict != 'insufficient'
    # сырые записи выгрузки содержат текст выбранного бюллетеня и происхождение орбиты
    assert any(v.get('text', '').startswith(':Product') for k, v in r.raw_records.items() if k.startswith('noaa_ngdc_3day'))
    assert 'orbit_provenance' in r.raw_records and r.raw_records['orbit_provenance']['records']


def test_history_forecast_gap_declares_missing_kp_forecast():
    t0 = datetime(2024, 5, 20, 12, 0, tzinfo=UTC)
    r = run('history_forecast', t0, 360, 1440, [0, 480], fetched=_fetched(), now=t0)
    fc = {l['channel']: l for l in r.S['forecasts']}
    assert fc['kp_forecast']['status'] == 'missing' and fc['kp_forecast']['cells'] == []
    assert fc['proton_prob_daily']['status'] == 'full'
    kpf = next(f for f in r.assessments[0].mechanisms[0].factors if f.name.startswith('прогноз Kp NOAA'))
    assert kpf.value is None and kpf.coverage.value == 'none'
    assert r.rec.verdict != 'insufficient'              # отсутствие прогноза не блокирует оценку


def test_history_forecast_is_deterministic_without_tle():
    t0 = datetime(2024, 6, 25, 12, 0, tzinfo=UTC)
    a = run('history_forecast', t0, 360, 1440, [0, 480], fetched=_fetched(), now=t0)
    b = run('history_forecast', t0, 360, 1440, [0, 480], fetched=_fetched(), now=t0)
    assert a.S['windows'] == b.S['windows'] and a.S['recommendation'] == b.S['recommendation']
