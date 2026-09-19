# -*- coding: utf-8 -*-
"""Закрепление находок разбора 19.09 по области «модели»: честность источников в истории,
объяснения для каждого окна, проверка после отсечки, валидация запроса, инвариант строгости
орбиты, единицы Kp, происхождение пустых величин, отчёт выгрузки."""
import os
from datetime import datetime, timedelta, timezone

import pytest

from app.compute import run, validate_request
from app.export import build_zip, report_md
from vkd.sources import Fetch
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
    assert src['noaa_swpc_goes']['live_ok'] is None and 'архив наблюдений GOES' in src['noaa_swpc_goes']['status']
    assert src['gfz_kp']['live_ok'] is None and 'строгий режим' in src['gfz_kp']['status']
    # C3: численный архив GOES в строгом режиме исключён — причина названа, а не «архива нет»
    assert 'не доказаны' in src['noaa_swpc_goes']['status'] and src['noaa_swpc_goes']['data_utc'] is None
    assert 'donki_archive' in src and 'архив уведомлений DONKI' in src['donki_archive']['status'] and src['donki_archive']['events_used'] == len(gannon.events)
    assert src['noaa_forecast_kp_forecast']['from_cache'] is None and 'архив A1' in src['noaa_forecast_kp_forecast']['origin']
    # исторический режим без переданного fetched не обращается к живым источникам вовсе (Т1-15)
    import app.compute as ac

    def boom(**kw):
        raise AssertionError('живой запрос в историческом режиме')
    monkeypatch.setattr(ac, 'goes_latest', boom)
    monkeypatch.setattr(ac, 'kp_latest', boom)
    monkeypatch.setattr(ac, 'tle_latest', boom)
    r = run('history_review', T_GANNON, 360, 720, [0, 240], now=T_GANNON)
    monkeypatch.setattr(ac, 'noaa_latest', boom)
    r = run('history_review', T_GANNON, 360, 720, [0, 240], now=T_GANNON)
    assert r.meta is not None and r.S['sources']['gfz_kp']['origin'].startswith('архив GFZ')
    assert r.S['sources']['gfz_kp']['data_utc'] and r.S['sources']['gfz_kp']['age_min'] is not None
    # C3: в разборе канал GOES — численное наблюдение архива, а не «данных нет»
    assert r.S['sources']['noaa_swpc_goes']['data_utc'] and r.goes is not None and r.goes.unit == 'pfu'


def test_review_kp_age_is_measured_from_t0_not_now():
    """Т1-3: давность наблюдения Kp в разборе считается от t0 (2024), а не от момента запуска (2026).
    Ряд Kp разбора — окончательный ряд GFZ по 3-часовым интервалам (слой данных 19.09): внутри архива
    последний интервал перед t0 моложе предела 180 мин — покрытие полное; сразу за концом архива
    (01.07.2024) наблюдение устаревает по давности от t0, а не на два года «от сегодня»."""
    now = datetime(2026, 9, 19, tzinfo=UTC)
    t0 = datetime(2024, 5, 12, 23, 0, tzinfo=UTC)
    r = run('history_review', t0, 360, 720, [0, 240], fetched=_fetched(), now=now)
    kpf = next(f for f in r.assessments[0].mechanisms[0].factors if f.name.startswith('Kp'))
    assert kpf.value == 4.0 and kpf.coverage.value == 'full' and 'устарело' not in kpf.limits_note
    assert kpf.record_ids and kpf.record_ids[0].startswith('gfz_kp_archive:')
    assert not any('наблюдение Kp' in c.text for m in r.assessments[0].mechanisms for c in m.conditions)
    assert r.S['sources']['gfz_kp']['age_min'] == 120
    # в самом начале архива наблюдения Kp до t0 ещё нет: причина называется, «нет данных» не выдаётся
    # за спокойную обстановку, а давность считается от t0
    t1 = datetime(2024, 5, 1, 0, 0, tzinfo=UTC)
    r1 = run('history_review', t1, 360, 720, [0, 240], fetched=_fetched(), now=now)
    kpf1 = next(f for f in r1.assessments[0].mechanisms[0].factors if f.name.startswith('Kp'))
    assert kpf1.value is None and kpf1.coverage.value == 'none'
    assert 'наблюдений Kp в архиве' in r1.S['sources']['gfz_kp']['status']
    # дата вне архива 2024 года отклоняется русским сообщением, а не исключением адаптера
    with pytest.raises(ValueError, match='вне архива исторических режимов'):
        run('history_review', datetime(2024, 7, 1, 12, 0, tzinfo=UTC), 360, 720, [0, 240], fetched=_fetched(), now=now)


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
    assert c.limits_ru and ('event_valid_hours' in c.limits_ru or 'sep_valid_hours' in c.limits_ru)
    assert 'kp_90' not in c.limits_ru          # R10: поля прогона поздних карточек больше не используются
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
    # адрес фактически полученных байтов у A4 — в metadata (Fetch.url читает именно его)
    f_tle = Fetch('celestrak_gp', True, False, late, 0.0, 'получено живьём с api.wheretheiss.at', txt, TLE,
                  metadata={'url': 'https://api.wheretheiss.at/v1/satellites/25544/tles'})
    fetched = ((None, {}, Fetch('noaa_swpc_goes', False, False, None, None, 'откл', None, None)),
               (None, {}, Fetch('gfz_kp', False, False, None, None, 'откл', None, None)), (txt, f_tle),
               _fetched()[3])
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
    # в текущем режиме окно через 4 ч не покрыто наблюдением GOES: покрытие отсутствует
    g2 = next(f for f in r.assessments[1].mechanisms[0].factors if f.name.startswith('поток протонов GOES'))
    assert g2.coverage.value == 'none' and g2.horizon_utc is not None
    assert r.rec.verdict == 'insufficient' and any('GOES' in x for x in r.rec.missing)
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
    assert any('прогноз Kp' in x or 'GOES' in x for x in r.rec.reasons + r.rec.missing)
    assert r.S['history']['catalog_coverage']['from_utc'].startswith('2024-04-30')


def test_review_window_beyond_archive_end_names_the_reason():
    """M11: окно за концом архива — покрытие линии событий и прогнозов отсутствует В ЛЮБОМ
    историческом режиме. Прежде строгий режим оценивал такое окно (смотрел только на публикации
    до отсечки) и выдавал «предпочтительное окно», тогда как экран сверху обещал «рекомендации
    не будет»; теперь обещание и вердикт согласованы."""
    t0 = datetime(2024, 6, 30, 12, 0, tzinfo=UTC)
    r = run('history_review', t0, 360, 1440, [0, 480], fetched=_fetched(), now=t0)
    assert r.rec.verdict == 'insufficient'
    assert any('метеороиды' in m for m in r.rec.missing)
    # GOES extends through July 2; DONKI's catalog end is not its data boundary.
    assert r.S['history']['goes_observations']
    # в строгом режиме то же окно оценивается: важны публикации до отсечки, а не конец архива
    s = run('history_forecast', t0, 360, 1440, [0, 480], fetched=_fetched(), now=t0)
    assert s.rec.verdict == 'insufficient'
    g = next(f for f in s.assessments[1].mechanisms[0].factors if f.name.startswith('поток протонов GOES'))
    assert 'до отсечки' in g.limits_note and 'после отсечки сведения не использованы' in g.limits_note


def test_window_past_archive_end_is_insufficient_not_preferred():
    """M11, случай постановки: 30.06.2024 20:00 — оба окна выходят за 01.07.2024."""
    t0 = datetime(2024, 6, 30, 20, 0, tzinfo=UTC)
    for mode in ('history_review', 'history_forecast'):
        r = run(mode, t0, 360, 720, [0, 240], fetched=_fetched(), now=t0)
        assert r.rec.verdict == 'insufficient' and r.rec.preferred is None, mode
        assert any('за границей архива' in m for m in r.rec.missing), mode
        for a in r.assessments:
            m1 = next(m for m in a.mechanisms if m.mechanism_id == 'spaceweather')
            assert m1.coverage.value == 'none'
            assert any('за границей архива' in n for n in m1.coverage_notes)


# ----------------------------------------------------------------------------- О2/Т3: уровень SEP из тела уведомления, буря как одно условие
def test_sep_level_from_donki_body_and_storm_signals_grouped():
    t0 = datetime(2024, 5, 10, 19, 0, tzinfo=UTC)
    r = run('history_forecast', t0, 360, 720, [0, 240], fetched=_fetched(), now=t0)
    conds = [c for m in r.assessments[0].mechanisms for c in m.conditions]
    sep = [c for c in conds if c.kind == 'SEP']
    # уведомление DONKI публикует ПОРОГ канала, а не измеренный поток: это сказано прямо
    assert sep and all('S1 (10 pfu, нижняя граница по тексту уведомления)' in c.text for c in sep)
    assert all(c.severity == 'limiting' for c in sep)
    storm = [c for c in conds if c.kind == 'GST']
    assert len(storm) == 1 and len(storm[0].sources_ru) >= 2
    # три сигнала об одной буре сведены в одно условие: наблюдение Kp из уведомления
    # и два ОПУБЛИКОВАННЫХ прогноза прихода выброса с диапазоном Kp
    assert 'наблюдение Kp 7,67' in storm[0].text
    assert 'опубликованный прогноз прихода выброса' in storm[0].text
    assert 'верхняя граница опубликованного диапазона' in storm[0].text
    assert 'ENLIL' not in storm[0].text and 'kp_90' not in storm[0].text
    # идентификаторов кода в тексте условия нет — записи названы номером выпуска источника
    assert 'nasa_donki_notification:' not in storm[0].text
    assert 'уведомление NASA DONKI 20240509-AL-010, приход выброса' in storm[0].text
    assert not any('политика прототипа' in c.text for c in conds)
    assert r.rec.verdict == 'insufficient' and storm and sep
    assert all(x.startswith('окн') for x in r.rec.reasons if 'условие' in x or 'протонное' in x)
    assert len(r.rec.reasons) == len(set(r.rec.reasons))


# ----------------------------------------------------------------------------- Т8: отчёт и выгрузка
def test_report_is_a_readable_document(gannon):
    md = report_md(gannon.S)
    for section in ('## Вывод', '## Окна и величины', '## Почему такой вывод', '## Источники и публикация',
                    '## Условия по окнам', '## Проверка после отсечки', 'Окно 1 — 10.05 12:00 — 18:00 UTC (360 мин)'):
        assert section in md, section
    assert 'own_calculation' not in md and 'external_forecast' not in md
    assert 'Kp 3,67' in md and '3.67 1' not in md and '1654378.43' not in md
    from app.export import _fold_records
    assert _fold_records(['a', 'b', 'c', 'd']).endswith('всего 4 (полный список — cards.json, raw/)')
    # записи называются номером выпуска источника, как на экране, а не машинным ключом
    assert _fold_records(['nasa_donki_notification:20240508-AL-012:00f5:CME_ARRIVAL']) == \
        'уведомление NASA DONKI 20240508-AL-012, приход выброса'
    z = build_zip(gannon.S, gannon.raw_records)
    import io as _io
    import zipfile
    names = zipfile.ZipFile(_io.BytesIO(z)).namelist()
    assert 'verification.json' in names and 'cards.json' in names
    import json
    assert json.loads(zipfile.ZipFile(_io.BytesIO(z)).read('cards.json')) == json.loads(json.dumps(gannon.S['cards'], default=str))
    man = zipfile.ZipFile(_io.BytesIO(z)).read('manifest.json').decode('utf-8')
    assert 'settings_path' in man and 'sep_valid_hours' in man
