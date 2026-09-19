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
    assert any(late['raw_record_id'] in s and 'опубликовано или доступно позже отсечки' in s for s in after.excluded)
    assert not any(e.raw_record_id == late['raw_record_id'] for e in after.events)
    assert late['raw_record_id'] not in after.raw_records
    review = run('history_review', t0, 360, 720, [0, 240], fetched=_fetched(), now=t0)
    assert any(e.raw_record_id == late['raw_record_id'] for e in review.events)
def test_future_archive_records_do_not_enter_the_strict_snapshot():
    """CONTRACT §8, строки 1–2, на уровне app.compute.run и НА РЕАЛЬНОМ поставщике (vkd.history).

    Прежняя версия теста дописывала записи в заглушку experiments.stub_history, которой
    в конвейере больше нет (разбор Codex, docs/integration/A2_SYNC_2026-09-19.md).
    Выдумывать записи и не нужно: в архиве уведомлений DONKI за 10–11 мая 2024 лежат
    реальные выпуски ПОЗЖЕ отсечки 19:00 — уведомления о буре Kp 7,67 (20240510-AL-014,
    19:19Z), Kp 8,67 (20240510-AL-015, 21:35Z) и Kp 9 (20240511-AL-002, 00:34Z),
    а также численный архив наблюдений GOES без доказанной публикации. Проверяется,
    что ни одна из них не попала в строгий снимок, что причина исключения названа
    и что в разборе те же записи, напротив, видны — режимы различимы.
    """
    from tests.test_integration import _fetched
    t0 = datetime(2024, 5, 10, 19, 0, tzinfo=timezone.utc)
    strict = run('history_forecast', t0, 360, 720, [0, 240], fetched=_fetched(), now=t0)
    review = run('history_review', t0, 360, 720, [0, 240], fetched=_fetched(), now=t0)

    # 1) инвариант строгого режима: у каждой использованной записи есть публикация и она до отсечки
    assert strict.events
    assert all(e.published_utc is not None and e.published_utc <= t0 for e in strict.events)
    assert strict.kp is not None and strict.kp.published_utc is not None and strict.kp.published_utc <= t0
    assert strict.kp.value == 3.67 or strict.kp.value == 7.67        # наблюдение из уведомления, не из будущего

    # 2) реальные более поздние выпуски названы в списке исключённых с причиной
    ex = ' '.join(strict.excluded)
    for mid in ('20240510-AL-014', '20240510-AL-015', '20240511-AL-002'):
        assert mid in ex, mid
        assert not any(mid in e.event_id for e in strict.events), mid
    assert 'позже отсечки' in ex
    assert 'не доказаны' in ex                                       # архив наблюдений GOES 2024

    # 3) в разборе (без отсечки) те же записи видны
    assert any('20240510-AL-015' in e.event_id for e in review.events)
    assert review.goes is not None and strict.goes is None

    # 4) повтор строгого расчёта даёт тот же снимок
    again = run('history_forecast', t0, 360, 720, [0, 240], fetched=_fetched(), now=t0)
    assert _strip(strict.S)['windows'] == _strip(again.S)['windows']
    assert strict.S['recommendation'] == again.S['recommendation']


def test_replay_parses_saved_records_at_their_fetch_time_not_at_calculation_time():
    """Т8: повтор текущего режима разбирает сохранённые байты на момент ПОЛУЧЕНИЯ записи.

    Разборщик Kp отбрасывает незавершённый 3-часовой интервал по правилу «конец
    интервала позже now» (`vkd/sources/live_parsers.parse_kp`). Если повторять разбор
    на момент расчёта, интервал, который в живом запросе был ещё незавершённым, к
    моменту расчёта оказывается завершённым, и повтор берёт ДРУГОЕ значение Kp —
    расчёт перестаёт воспроизводиться. Найдено на `examples/live_now.zip` 19.09.2026:
    живой запрос в 02:55 дал Kp интервала, закончившегося в 00:00, а повтор на момент
    расчёта 03:07 — интервала, закончившегося в 03:00.
    """
    import base64
    import json as _json
    from vkd.integration.replay_live import from_saved_records

    fetched = datetime(2026, 9, 19, 2, 55, tzinfo=timezone.utc)
    computed = datetime(2026, 9, 19, 3, 7, tzinfo=timezone.utc)
    payload = _json.dumps({'Kp': [1.667, 2.0], 'status': ['def', 'pre'],
                           'datetime': ['2026-09-18T21:00:00Z', '2026-09-19T00:00:00Z']}).encode()
    records = {
        'gfz_kp:sha': {'metadata': {'source_id': 'gfz_kp', 'raw_record_id': 'gfz_kp:sha', 'version': 'v1',
                                    'fetched_utc': fetched.isoformat()},
                       'encoding': 'base64', 'content_base64': base64.b64encode(payload).decode()},
        'noaa_swpc_goes:sha': {'metadata': {'source_id': 'noaa_swpc_goes', 'raw_record_id': 'noaa_swpc_goes:sha',
                                            'version': 'v1', 'fetched_utc': fetched.isoformat()},
                               'encoding': 'base64',
                               'content_base64': base64.b64encode(_json.dumps(
                                   [{'time_tag': '2026-09-19T02:55:00Z', 'satellite': 18, 'flux': 0.21,
                                     'energy': '>=10 MeV'}]).encode()).decode()},
    }
    tle = open(TLE, encoding='utf-8').read()
    (_g, _gr, _fg), (kp, _kr, f_kp), _tle, _noaa = from_saved_records(records, computed, tle_text=tle)
    assert kp is not None, f_kp.status_ru
    # интервал 21:00–00:00 завершён к 02:55; интервал 00:00–03:00 на 02:55 ещё шёл
    assert kp.t_utc == datetime(2026, 9, 19, 0, 0, tzinfo=timezone.utc), kp.t_utc
    assert kp.value == 1.667
    # давность по-прежнему считается от момента РАСЧЁТА, а не от момента получения
    assert abs(f_kp.age_min - 187.0) < 0.001, f_kp.age_min
