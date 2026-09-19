# -*- coding: utf-8 -*-
"""Закрепление находок разбора 19.09 по области «модели»: честность источников в истории,
объяснения для каждого окна, проверка после отсечки, валидация запроса, инвариант строгости
орбиты, единицы Kp, происхождение пустых величин, отчёт выгрузки."""
import os
from datetime import datetime, timedelta, timezone

import pytest

from app.compute import run, validate_request
from app.export import build_zip, report_md
from experiments.stub_sources import Fetch
from tests.test_integration import _fetched
from vkd.orbit.trajectory import satellite_from_tle

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TLE = os.path.join(ROOT, 'data', 'orbit', 'iss.tle')
UTC = timezone.utc
T_GANNON = datetime(2024, 5, 10, 12, 0, tzinfo=UTC)


@pytest.fixture(scope='module')
def gannon():
    return run('history_forecast', T_GANNON, 360, 720, [0, 240], fetched=_fetched(), now=T_GANNON)


# ----------------------------------------------------------------------------- Т7: границы запроса
@pytest.mark.parametrize('kw, msg', [
    (dict(duration_min=30), 'длительность'), (dict(duration_min=600), 'длительность'),
    (dict(search_min=2000), 'период поиска'), (dict(offsets=[0, 0]), 'различаться'),
    (dict(offsets=[0]), 'окон для сравнения'), (dict(offsets=[0, 240, 480, 600]), 'окон для сравнения'),
    (dict(offsets=[0, 900]), 'вне периода поиска'), (dict(t0=datetime(2024, 5, 10, 12)), 'часовым поясом'),
    (dict(mode='future'), 'режим'),
])
def test_request_validation_rejects_out_of_range(kw, msg):
    args = dict(mode='history_forecast', t0=T_GANNON, duration_min=360, search_min=720, offsets=[0, 240])
    args.update(kw)
    with pytest.raises(ValueError, match=msg):
        validate_request(args['mode'], args['t0'], args['duration_min'], args['search_min'], args['offsets'])
    with pytest.raises(ValueError):
        run(args['mode'], args['t0'], args['duration_min'], args['search_min'], args['offsets'], fetched=_fetched(), now=T_GANNON)


def test_request_validation_accepts_bounds():
    validate_request('history_forecast', T_GANNON, 480, 1440, [0, 720, 1440])
    validate_request('live', T_GANNON, 60, 60, [0, 60])


# ----------------------------------------------------------------------------- Т1/Т4: источники в истории
def test_history_sources_never_claim_live_requests(gannon, monkeypatch):
    src = gannon.S['sources']
    assert not any('живьём' in (v.get('status') or '') for v in src.values())
    assert src['noaa_swpc_goes']['live_ok'] is None and 'архива' in src['noaa_swpc_goes']['status']
    assert src['gfz_kp']['live_ok'] is None and 'строгий режим' in src['gfz_kp']['status']
    assert 'donki_archive' in src and 'архив DONKI' in src['donki_archive']['status'] and src['donki_archive']['events_used'] == len(gannon.events)
    assert src['noaa_forecast_kp_forecast']['from_cache'] is None and 'архив A1' in src['noaa_forecast_kp_forecast']['origin']
    # исторический режим без переданного fetched не обращается к живым источникам вовсе (Т1-15)
    import app.compute as ac

    def boom(**kw):
        raise AssertionError('живой запрос в историческом режиме')
    monkeypatch.setattr(ac, 'goes_latest', boom)
    monkeypatch.setattr(ac, 'kp_latest', boom)
    monkeypatch.setattr(ac, 'tle_latest', boom)
    r = run('history_review', T_GANNON, 360, 720, [0, 240], now=T_GANNON)
    assert r.meta is not None and r.S['sources']['gfz_kp']['origin'].startswith('архив DONKI')
    assert r.S['sources']['gfz_kp']['data_utc'] and r.S['sources']['gfz_kp']['age_min'] is not None


def test_review_kp_age_is_measured_from_t0_not_now(gannon):
    """Т1-3: давность наблюдения Kp в разборе считается от t0 (2024), а не от момента запуска (2026)."""
    t0 = datetime(2024, 5, 12, 23, 0, tzinfo=UTC)
    r = run('history_review', t0, 360, 720, [0, 240], fetched=_fetched(), now=datetime(2026, 9, 19, tzinfo=UTC))
    kpf = next(f for f in r.assessments[0].mechanisms[0].factors if f.name.startswith('Kp'))
    assert kpf.value == 7.0 and kpf.coverage.value == 'partial' and 'устарело' in kpf.limits_note
    assert not any('наблюдение Kp' in c.text for m in r.assessments[0].mechanisms for c in m.conditions)
    assert 0 < r.S['sources']['gfz_kp']['age_min'] < 3 * 24 * 60


# ----------------------------------------------------------------------------- О4/О5: объяснения для каждого окна
def test_cards_exist_for_every_window_with_conditions(gannon):
    cards = gannon.cards
    idx = {c.window_index for c in cards}
    assert idx == {1, 2}
    assert gannon.cards_by_window[gannon.assessments[1].window.start_utc]
    cond2 = [c for c in cards if c.window_index == 2 and c.severity != 'info']
    assert cond2 and all(c.title.startswith('Окно 2 · Условие:') for c in cond2)
    # период условия — интервал события, источник — записи с публикацией, не таблица порогов
    c = cond2[0]
    assert 'пересекает окно' in c.period_ru and 'событие/действие' in c.period_ru
    assert 'опубликовано' in c.source_ru and 'до отсечки' in c.source_ru
    assert 'NOAA Space Weather Scales' not in c.source_ru and 'правило команды' in c.rule_ru
    assert c.limits_ru and 'kp_90' in c.limits_ru or 'sep_valid_hours' in c.limits_ru
    S_cards = gannon.S['cards']
    assert all('window_index' in x for x in S_cards) and {x['window_index'] for x in S_cards} == {1, 2}


def test_card_texts_are_russian_and_units_are_honest(gannon):
    cards = {c.title: c for c in gannon.cards if c.window_index == 1}
    g = cards['Окно 1 · поток протонов GOES ≥10 МэВ']
    assert g.source_ru.startswith('наблюдения нет') and 'собственный расчёт' not in g.source_ru
    assert 'неизвестно (данных нет)' in g.limits_ru and 'detected' not in g.limits_ru
    kpf = cards['Окно 1 · прогноз Kp NOAA, максимум в окне']
    assert kpf.data_ru == 'Kp 3,67'
    fl = next(v for k, v in cards.items() if 'флюенс' in k)
    assert '·10^' in fl.data_ru and 'e+' not in fl.data_ru
    assert 'OEM NASA/JSC' in fl.source_ru and 'ОСТ 134-1044-2007' in fl.source_ru and 'реконструкция' in fl.source_ru
    assert 'в сетке таблицы' in fl.limits_ru and 'no_model_L' not in fl.limits_ru
    mm = cards['Окно 1 · ожидаемое число попаданий, пластина 1 м²']
    assert 'лет непрерывной экспозиции' in mm.limits_ru and 'ECSS-E-ST-10-04C' in mm.source_ru
    assert all(f['unit'] != '1' for w in gannon.S['windows'] for m in w['mechanisms'] for f in m['factors'])


# ----------------------------------------------------------------------------- Т4/О6: проверка после отсечки
def test_verification_after_cutoff_is_separate_and_not_used(gannon):
    v = gannon.verification
    assert v and 'в расчёт не входит' in v['note']
    assert any(k['kp'] >= 7 for k in v['kp_obs']) and 'факт' in v['summary'] and '7,67' in v['summary']
    assert any(e['kind'] == 'SEP' for e in v['events']) and all(e['published_utc'] > T_GANNON.isoformat() for e in v['events'])
    # ни одна запись проверки не участвует в оценке
    used = {e.event_id for e in gannon.events}
    assert not any(e['id'] in used for e in v['events'])
    assert gannon.S['verification'] is v
    quiet = run('history_review', T_GANNON, 360, 720, [0, 240], fetched=_fetched(), now=T_GANNON)
    assert quiet.verification is None


# ----------------------------------------------------------------------------- Т2: инвариант строгости орбиты
def test_live_tle_fetched_after_now_is_not_strict_reconstruction():
    epoch = satellite_from_tle(open(TLE, 'rb').read()).epoch.utc_datetime()
    t0 = (epoch + timedelta(days=1)).replace(second=0, microsecond=0)
    txt = open(TLE, encoding='utf-8').read()
    late = t0 + timedelta(seconds=7)          # TLE получен через 7 с после зафиксированного «сейчас»
    f_tle = Fetch('celestrak_gp', True, False, late, 0.0, 'получено живьём с api.wheretheiss.at', txt, TLE, url='https://api.wheretheiss.at/v1/satellites/25544/tles')
    fetched = ((None, {}, Fetch('noaa_swpc_goes', False, False, None, None, 'откл', None, None)),
               (None, {}, Fetch('gfz_kp', False, False, None, None, 'откл', None, None)), (txt, f_tle))
    r = run('live', t0, 360, 720, [0, 240], fetched=fetched, now=t0)
    tm = r.S['trajectory_meta']
    assert not (tm['strictness'] == 'strict' and tm['is_reconstruction'])
    assert tm['strictness'] == 'strict' and not r.meta.is_reconstruction
    assert tm['provenance']['staged_root'].startswith('data/cache/orbit_root/') and ':\\' not in tm['provenance']['staged_root']
    assert tm['belt_coordinates']['coefficients'] == 'data/orbit/IGRF14.shc' and tm['belt_coordinates']['coefficients_sha256']
    assert r.raw_records['iss.tle']['url'].startswith('https://api.wheretheiss.at')


def test_live_without_tle_url_does_not_invent_address():
    epoch = satellite_from_tle(open(TLE, 'rb').read()).epoch.utc_datetime()
    t0 = (epoch + timedelta(days=1)).replace(second=0, microsecond=0)
    r = run('live', t0, 360, 720, [0, 240], fetched=_fetched(open(TLE, encoding='utf-8').read(), goes_at=t0), now=t0)
    rec = next(v for k, v in r.S['trajectory_meta']['provenance']['records'].items() if k.startswith('celestrak_gp'))
    assert 'celestrak.org' not in (rec.get('url') or '') and 'неизвестен' in rec['url']
    assert r.S['trajectory_meta']['provenance']['requested_mode'] == 'live'
    # в текущем режиме окно через 4 ч не покрыто наблюдением GOES: частично и объявлено, а не «полное»
    g2 = next(f for f in r.assessments[1].mechanisms[0].factors if f.name.startswith('поток протонов GOES'))
    assert g2.coverage.value == 'partial' and g2.horizon_utc is not None
    assert r.rec.verdict != 'insufficient' and any('GOES' in x for x in r.rec.reasons)
    assert r.S['effective_config']['sources']['cache_ttl_s'] == 300 and r.S['effective_config']['thresholds']['sep_valid_hours'] == 24.0


def test_history_provenance_records_selection_cutoff(gannon):
    prov = gannon.S['trajectory_meta']['provenance']
    assert prov['requested_mode'] == 'history_forecast' and prov['selection_cutoff_utc'].startswith('2024-05-10T12:00')
    assert 'CREATION_DATE' in prov['selection_rule']
    fl = next(f for f in gannon.assessments[0].mechanisms[0].factors if f.name.startswith('флюенс'))
    assert any(rid.startswith('nasa_jsc_oem:') for rid in fl.record_ids) and any(rid.startswith('ost1044_A') for rid in fl.record_ids)


# ----------------------------------------------------------------------------- Т4: разрыв архива и конец каталога
def test_gap_forecast_line_does_not_call_stale_bulletin_a_release():
    t0 = datetime(2024, 5, 20, 12, 0, tzinfo=UTC)
    r = run('history_forecast', t0, 360, 1440, [0, 480], fetched=_fetched(), now=t0)
    line = next(l for l in r.S['forecasts'] if l['channel'] == 'kp_forecast')
    assert line['status'] == 'missing' and line['release_id'] is None and line['record'] is None
    assert line['last_release_before_cutoff']['release_id'].startswith('202405141230') and 'разрыв' in line['reason']
    assert not any(k.startswith('noaa_ngdc_3day') for k in r.raw_records)
    assert 'разрыв' in r.S['sources']['noaa_forecast_kp_forecast']['status']
    assert any('прогноз Kp' in x or 'GOES' in x for x in r.rec.reasons)
    assert r.S['history']['catalog_coverage']['from_utc'].startswith('2024-04-30')


def test_review_window_beyond_archive_end_names_the_reason():
    t0 = datetime(2024, 6, 30, 12, 0, tzinfo=UTC)
    r = run('history_review', t0, 360, 1440, [0, 480], fetched=_fetched(), now=t0)
    assert r.rec.verdict == 'insufficient'
    assert any('архив до' in m and 'сократите' in m for m in r.rec.missing)
    # в строгом режиме то же окно оценивается: важны публикации до отсечки, а не конец архива
    s = run('history_forecast', t0, 360, 1440, [0, 480], fetched=_fetched(), now=t0)
    assert s.rec.verdict != 'insufficient'
    g = next(f for f in s.assessments[1].mechanisms[0].factors if f.name.startswith('поток протонов GOES'))
    assert 'до отсечки' in g.limits_note and 'после отсечки сведения не использованы' in g.limits_note


# ----------------------------------------------------------------------------- О2/Т3: уровень SEP из тела уведомления, буря как одно условие
def test_sep_level_from_donki_body_and_storm_signals_grouped():
    t0 = datetime(2024, 5, 10, 19, 0, tzinfo=UTC)
    r = run('history_forecast', t0, 360, 720, [0, 240], fetched=_fetched(), now=t0)
    conds = [c for m in r.assessments[0].mechanisms for c in m.conditions]
    sep = [c for c in conds if c.kind == 'SEP']
    assert sep and all('S1 (10 pfu)' in c.text for c in sep) and all(c.severity == 'limiting' for c in sep)
    storm = [c for c in conds if c.kind == 'GST']
    assert len(storm) == 1 and len(storm[0].sources_ru) >= 2 and 'уведомление DONKI' in storm[0].text and 'ENLIL' in storm[0].text
    assert not any('политика прототипа' in c.text for c in conds)
    assert r.rec.verdict == 'all_need_check'
    assert all(x.startswith('окн') for x in r.rec.reasons if 'условие' in x or 'протонное' in x)
    assert len(r.rec.reasons) == len(set(r.rec.reasons))


# ----------------------------------------------------------------------------- Т8: отчёт и выгрузка
def test_report_is_a_readable_document(gannon):
    md = report_md(gannon.S)
    for section in ('## Вывод', '## Окна и величины', '## Почему такой вывод', '## Источники и публикация',
                    '## Условия по окнам', '## Проверка после отсечки', 'Окно 1 — 10.05 12:00–18:00 UTC (360 мин)'):
        assert section in md, section
    assert 'own_calculation' not in md and 'external_forecast' not in md
    assert 'Kp 3,67' in md and '3.67 1' not in md and '1654378.43' not in md
    assert 'полный список — cards.json' in md
    z = build_zip(gannon.S, gannon.raw_records)
    import io as _io
    import zipfile
    names = zipfile.ZipFile(_io.BytesIO(z)).namelist()
    assert 'verification.json' in names and 'cards.json' in names
    man = zipfile.ZipFile(_io.BytesIO(z)).read('manifest.json').decode('utf-8')
    assert 'settings_path' in man and 'sep_valid_hours' in man
