# -*- coding: utf-8 -*-
"""Третий круг: конвейер на слоях А (vkd.sources, vkd.history) без заглушек.

Здесь закреплены пункты стыка C2–C7:
  * C2 — расчёт берёт источники и историю только из vkd.*, строки «experiments.stub»
    нет ни на экране, ни в выгрузке; условия строятся по структурированным фактам;
  * C3 — численный архив наблюдений GOES 2024 в разборе и его исключение в строгом режиме;
  * C4 — R10: приход выброса по опубликованному уведомлению, линии enlilList нет;
  * C6 — живой прогноз NOAA в текущем режиме, без сети (закреплённый ответ);
  * C7 — в путях конвейера нет слова stub.
Сети ни один тест не использует.
"""
import io
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from app.compute import GOES_CHANNEL, HIST_SRC, SRC_LAYER, run
from app.export import build_zip, report_md
from tests.test_integration import _fetched, pinned_noaa

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UTC = timezone.utc
T_CUT = datetime(2024, 5, 10, 19, 0, tzinfo=UTC)          # отсечка контрольного случая C3
TLE = os.path.join(ROOT, 'data', 'orbit', 'iss.tle')


# --------------------------------------------------------------------- C2: слои
def test_pipeline_layers_are_vkd_and_no_stub_string_reaches_the_user():
    assert SRC_LAYER == 'vkd.sources' and HIST_SRC == 'vkd.history'
    r = run('history_review', T_CUT, 360, 720, [0, 240], fetched=_fetched(), now=T_CUT)
    blob = json.dumps(r.S, ensure_ascii=False, default=str) + report_md(r.S)
    assert 'stub' not in blob.lower()
    assert 'experiments' not in blob
    z = build_zip(r.S, r.raw_records)
    assert b'experiments.stub' not in z


def test_structured_facts_are_recorded_in_the_snapshot_not_parsed_from_notes():
    """Разбор Codex, п. 3: правило читает facts, а не русский текст заметки."""
    r = run('history_forecast', T_CUT, 360, 720, [0, 240], fetched=_fetched(), now=T_CUT)
    facts = r.S['history']['event_facts']
    assert facts, 'факты уведомлений должны попадать в снимок'
    cme = [f for f in facts.values() if f.get('kp_basis') == 'published_notification_range']
    assert cme and all(f['kp_range_min'] <= f['kp_range_max'] for f in cme)
    sep = [f for f in facts.values() if f.get('detector') == 'GOES']
    assert sep and all(f['measured_flux_pfu'] is None and f['flux_lower_bound_pfu'] > 0 for f in sep)
    # аудит адаптера сохранён целиком, а не только записи по event_ids
    assert r.S['history']['adapter_version'] and r.S['history']['coverage_map']
    assert r.S['history']['source_versions'], 'метаданные использованных записей нужны манифесту'
    man = json.loads(_zip_read(build_zip(r.S, r.raw_records), 'manifest.json'))
    assert man['history_audit']['adapter_version'] == r.S['history']['adapter_version']
    assert man['history_audit']['coverage_map']


def _zip_read(blob, name):
    import zipfile
    return zipfile.ZipFile(io.BytesIO(blob)).read(name).decode('utf-8')


# --------------------------------------------------------------------- C3: архив GOES 2024
def test_goes_archive_is_an_observation_in_review_and_is_excluded_in_strict_mode():
    """На отсечке 10.05.2024 19:00: наблюдение GOES ≥10 МэВ до отсечки в разборе есть
    и это численное наблюдение с единицей pfu; после отсечки значения в расчёт не входят.

    В строгом режиме архив исключён целиком и причина названа: у выпусков iSWA нет
    ни времени публикации, ни доказательства доступности версии в 2024 году
    (data/goes_2024/README.md, раздел «Режимы и повторение»). Придумывать им
    время доступности по метке измерения нельзя — это было бы молчаливым допущением.
    """
    review = run('history_review', T_CUT, 360, 720, [0, 240], fetched=_fetched(), now=T_CUT)
    g = review.goes
    assert g is not None and g.unit == 'pfu' and g.channel_id == GOES_CHANNEL
    assert g.kind.value == 'observation' and g.source_id == 'nasa_iswa_goes_primary_p5m'
    assert g.t_utc <= T_CUT and g.t_utc > T_CUT - timedelta(minutes=10)      # ближайшее наблюдение до отсечки
    assert g.published_utc is None                                          # публикация не выдумывается
    gf = next(f for f in review.assessments[0].mechanisms[0].factors if f.name.startswith('поток протонов GOES'))
    # Review uses actual cells within the window, not forward filling the last observation.
    cells = review.S['history']['goes_observations']
    end = (T_CUT + timedelta(hours=6)).isoformat()
    assert gf.value == pytest.approx(max(c['value'] for c in cells if c['valid_from_utc'] < end and c['valid_to_utc'] > T_CUT.isoformat()))
    assert gf.unit == 'pfu'
    assert review.S['sources']['noaa_swpc_goes']['coverage_fraction'] == pytest.approx(1.0)

    strict = run('history_forecast', T_CUT, 360, 720, [0, 240], fetched=_fetched(), now=T_CUT)
    assert strict.goes is None
    assert 'не доказаны' in strict.S['sources']['noaa_swpc_goes']['status']
    assert any('не доказаны' in x for x in strict.excluded)
    # и ни одно значение архива не попало в строгий снимок
    assert not any(GOES_CHANNEL in str(k) for k in strict.raw_records)


def test_goes_observations_after_the_cutoff_are_dropped_by_apply_cutoff():
    """Отбор по отсечке — тот же vkd.assess.cutoff.apply_cutoff, что и для остальных записей:
    наблюдение с моментом позже отсечки исключается, а без времени публикации — тем более."""
    from dataclasses import replace

    from vkd.assess.cutoff import apply_cutoff
    from vkd.history import history_bundle
    from vkd.types import Request

    samples, events, _ = history_bundle(Request('history_review', T_CUT, 360, 720, None))
    goes = [s for s in samples if s.channel_id == GOES_CHANNEL]
    assert goes and any(s.t_utc <= T_CUT for s in goes) and any(s.t_utc > T_CUT for s in goes)
    # как есть (публикации нет) — исключены все
    cut = apply_cutoff(goes, [], [], T_CUT)
    assert cut.samples == () and len(cut.excluded) == len(goes)
    assert all('время публикации неизвестно' in x for x in cut.excluded)
    # если бы публикация была доказана моментом измерения — до отсечки остались бы, после исключены
    dated = [replace(s, published_utc=s.t_utc) for s in goes]
    cut2 = apply_cutoff(dated, [], [], T_CUT)
    assert cut2.samples and all(s.t_utc <= T_CUT for s in cut2.samples)
    assert len(cut2.excluded) == sum(1 for s in goes if s.t_utc > T_CUT)
    assert all('после отсечки' in x for x in cut2.excluded)


# --------------------------------------------------------------------- C4: R10
def test_r10_cme_arrival_comes_from_published_notifications_only():
    r = run('history_forecast', T_CUT, 360, 720, [0, 240], fetched=_fetched(), now=T_CUT)
    storm = [c for m in r.assessments[0].mechanisms for c in m.conditions if c.kind == 'GST']
    assert len(storm) == 1
    text = storm[0].text
    assert 'опубликованный прогноз прихода выброса' in text
    assert 'верхняя граница опубликованного диапазона' in text
    for banned in ('ENLIL', 'kp_90', 'kp_180', 'modelCompletionTime', 'политика прототипа'):
        assert banned not in text, banned
    # все записи условия — датированные уведомления, опубликованные до отсечки
    used = {e.event_id: e for e in r.events}
    for rid in storm[0].event_ids:
        if rid in used:
            assert used[rid].source_id == 'nasa_donki_notification'
            assert used[rid].published_utc <= T_CUT
    blob = json.dumps(r.S, ensure_ascii=False, default=str)
    assert 'enlil' not in blob.lower() and 'enlil_kp_fields' not in blob


def test_settings_no_longer_carry_the_enlil_policy():
    from vkd.config import section
    hist = section('history')
    assert 'enlil_kp_fields' not in hist and 'enlil_publication_lag_min' not in hist
    assert hist['cme_kp_range_bound'] in ('max', 'min')


# --------------------------------------------------------------------- C6: живой прогноз NOAA
def test_live_noaa_forecast_gives_the_same_factors_as_history(tmp_path):
    """Закреплённый ответ вместо сети: тот же канал kp_forecast и та же суточная вероятность."""
    from vkd.orbit.trajectory import satellite_from_tle
    epoch = satellite_from_tle(open(TLE, 'rb').read()).epoch.utc_datetime()
    t0 = (epoch + timedelta(days=1)).replace(second=0, microsecond=0)
    samples, raw, fetch = pinned_noaa(tmp_path)
    assert fetch.ok and samples
    # бюллетень 2024 года, окно 2026: сдвигаем ячейки на окно расчёта, чтобы проверить сам конвейер
    from dataclasses import replace as dc_replace
    shift = t0 - min(s.valid_from_utc for s in samples)
    samples = tuple(dc_replace(s, t_utc=s.t_utc + shift, valid_from_utc=s.valid_from_utc + shift,
                               valid_to_utc=s.valid_to_utc + shift) for s in samples)
    (g, g_raw, f_goes), kp3, (txt, f_tle), _ = _fetched(open(TLE, encoding='utf-8').read(), goes_at=t0)
    r = run('live', t0, 360, 720, [0, 240], fetched=((g, g_raw, f_goes), kp3, (txt, f_tle), (samples, raw, fetch)), now=t0)
    names = {f.name for f in r.assessments[0].mechanisms[0].factors}
    assert 'прогноз Kp NOAA, максимум в окне' in names
    assert 'вероятность S1 и выше за сутки, прогноз NOAA' in names
    kpf = next(f for f in r.assessments[0].mechanisms[0].factors if f.name.startswith('прогноз Kp NOAA'))
    assert kpf.kind.value == 'external_forecast' and kpf.value is not None
    assert kpf.record_ids and kpf.coverage.value in ('full', 'partial')
    line = next(l for l in r.S['forecasts'] if l['channel'] == 'kp_forecast')
    assert line['source_id'] == 'noaa_swpc_3day_forecast' and line['cells']
    assert r.S['sources']['noaa_swpc_3day_forecast']['live_ok'] is True
    # канал daypre живого бюллетеня не имеет — это объявлено, а не подменено 3-суточным
    daypre = next(l for l in r.S['forecasts'] if l['channel'] == 'proton_prob_daily')
    assert daypre['status'] in ('missing', 'unavailable') and 'daypre' in daypre['reason']


def test_live_storm_condition_from_noaa_forecast_works_now_too(tmp_path):
    """Условие «прогноз Kp ≥ 7» должно срабатывать и в текущем режиме, не только в истории."""
    from dataclasses import replace as dc_replace

    from vkd.orbit.trajectory import satellite_from_tle
    epoch = satellite_from_tle(open(TLE, 'rb').read()).epoch.utc_datetime()
    t0 = (epoch + timedelta(days=1)).replace(second=0, microsecond=0)
    samples, raw, fetch = pinned_noaa(tmp_path)
    shift = t0 - min(s.valid_from_utc for s in samples)
    samples = tuple(dc_replace(s, t_utc=s.t_utc + shift, valid_from_utc=s.valid_from_utc + shift,
                               valid_to_utc=s.valid_to_utc + shift,
                               value=(8.0 if s.channel_id == 'kp_forecast' else s.value)) for s in samples)
    (g, g_raw, f_goes), kp3, (txt, f_tle), _ = _fetched(open(TLE, encoding='utf-8').read(), goes_at=t0)
    r = run('live', t0, 360, 720, [0, 240], fetched=((g, g_raw, f_goes), kp3, (txt, f_tle), (samples, raw, fetch)), now=t0)
    storm = [c for m in r.assessments[0].mechanisms for c in m.conditions if c.kind == 'GST']
    assert len(storm) == 1 and 'прогноз NOAA: Kp 8' in storm[0].text
    assert r.rec.verdict == 'insufficient'
    assert all(any(m.needs_check for m in a.mechanisms) for a in r.assessments)


# --------------------------------------------------------------------- C7: заглушек в конвейере нет
def test_no_module_of_the_pipeline_imports_a_stub():
    import app.compute as ac
    import experiments.forecast_lines as fl
    for module in (ac, fl):
        src = io.open(module.__file__, encoding='utf-8').read()
        imports = [l for l in src.splitlines() if l.startswith(('import ', 'from ')) or ' import ' in l]
        assert not any('stub_' in l for l in imports), module.__name__
        assert 'experiments.stub_history' not in src.replace('experiments/stub_history.py', ''), module.__name__
    assert not os.path.exists(os.path.join(ROOT, 'experiments', 'stub_history.py'))
    assert not os.path.exists(os.path.join(ROOT, 'experiments', 'stub_sources.py'))
    assert os.path.isdir(os.path.join(ROOT, 'experiments', 'legacy'))
    readme = io.open(os.path.join(ROOT, 'experiments', 'legacy', 'README.md'), encoding='utf-8').read()
    assert 'архив' in readme.lower()


# --------------------------------------------------------------------- экран: бриф, п. 6
def test_screen_has_no_stub_layer_string_in_any_mode():
    """Бриф экрана, п. 6: строк вида «experiments.stub_…» на экране быть не должно ни в одном режиме."""
    from tests.test_ui_app import MODES, run_app, source_rows, texts
    for mode in (MODES[1], MODES[2]):
        at = run_app(mode, pro=True)
        assert not at.exception, (mode, at.exception)
        body = texts(at) + ' '.join(str(r) for r in source_rows(at))
        assert 'stub' not in body.lower(), mode
        assert 'vkd.history' in body and 'vkd.sources' in body, mode


def test_observations_series_reaches_snapshot_and_export():
    """C3: ряд наблюдений GOES попадает в снимок с единицей, источником и записью,
    в выгрузку — отдельным файлом, а в строгом режиме его нет по той же отсечке."""
    review = run('history_review', T_CUT, 360, 720, [0, 240], fetched=_fetched(), now=T_CUT)
    line = next(o for o in review.S['observations'] if o['channel'] == GOES_CHANNEL)
    assert line['unit'] == 'pfu' and line['source_id'] == 'nasa_iswa_goes_primary_p5m'
    assert line['record'] and line['n_points'] == len(line['points']) > 10
    assert all(p['value'] is not None and p['t'] for p in line['points'])
    names = json.loads(_zip_read(build_zip(review.S, review.raw_records), 'observations.json'))
    assert names == review.S['observations']
    strict = run('history_forecast', T_CUT, 360, 720, [0, 240], fetched=_fetched(), now=T_CUT)
    assert not [o for o in strict.S['observations'] if o['channel'] == GOES_CHANNEL]
