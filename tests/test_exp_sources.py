# -*- coding: utf-8 -*-
"""АРХИВ (experiments/legacy/stub_sources, в конвейере не используется с 0.6.0): режимы отказа без падения (Т6), давность не из
ничего (Т1), прослеживаемость кеша и снимка (Т2), незавершённый интервал Kp (Т1). Без сети:
живой запрос подменяется функцией."""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

import experiments.legacy.stub_sources as SS

UTC = timezone.utc
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(SS, 'CACHE', str(tmp_path / 'cache'))
    return tmp_path / 'cache'


def _live(monkeypatch, body):
    monkeypatch.setattr(SS, '_live', lambda url: body)


@pytest.mark.parametrize('body, fn', [
    ('{"a": 1}', SS.goes_latest),                                                          # GOES: словарь вместо списка
    ('[{"energy": ">=10 MeV", "flux": "abc", "time_tag": "2026-09-19T00:00:00Z"}]', SS.goes_latest),   # поток не число
    ('[1, 2]', SS.kp_latest),                                                              # Kp: список вместо объекта
    ('{"datetime": ["2026-09-19T00:00:00Z"], "Kp": [null], "status": ["pre"]}', SS.kp_latest),       # Kp = null
    ('not json at all', SS.goes_latest),
])
def test_malformed_response_gives_status_not_exception(cache, monkeypatch, body, fn):
    _live(monkeypatch, body)
    sample, raw, f = fn()
    assert sample is None and raw == {} and f.ok is False and f.payload is None
    assert 'ответ не разбирается' in f.status_ru


def test_cache_without_meta_has_unknown_fetch_time_not_zero(cache):
    os.makedirs(cache, exist_ok=True)
    (cache / 'x.json').write_text('[]', encoding='utf-8')
    f = SS._fallback('test', 'x.json', 'отказ источника (Test)')
    assert f.from_cache and f.fetched_utc is None and f.age_min is None
    assert 'время получения неизвестно' in f.status_ru and 'давность 0' not in f.status_ru


def test_broken_or_mismatching_meta_is_unknown_time(cache):
    os.makedirs(cache, exist_ok=True)
    (cache / 'x.json').write_text('[]', encoding='utf-8')
    (cache / 'x.json.meta.json').write_text('{broken', encoding='utf-8')
    f = SS._fallback('test', 'x.json', 'отказ')
    assert f.fetched_utc is None and 'неизвестно' in f.status_ru
    (cache / 'x.json.meta.json').write_text(json.dumps({'fetched_utc': '2026-09-18T00:00:00+00:00', 'sha256': 'deadbeef'}), encoding='utf-8')
    f = SS._fallback('test', 'x.json', 'отказ')
    assert f.fetched_utc is None and 'не соответствует' in f.status_ru


def test_cache_write_is_atomic_pair_with_sha_and_url(cache, monkeypatch):
    _live(monkeypatch, '[{"energy": ">=10 MeV", "flux": 0.5, "time_tag": "2026-09-19T00:00:00Z"}]')
    s, raw, f = SS.goes_latest()
    assert f.ok and f.fetched_basis == 'живой запрос' and s.value == 0.5
    meta = json.loads((cache / 'goes_protons_3day.json.meta.json').read_text(encoding='utf-8'))
    assert meta['url'] == SS.URLS['goes'] and meta['sha256'] == SS._sha256((cache / 'goes_protons_3day.json').read_bytes())
    assert not [p for p in os.listdir(cache) if p.startswith('.tmp_')]
    # отказ → кеш: адрес и время получения — из meta, давность считается от него
    monkeypatch.setattr(SS, '_live', lambda url: (_ for _ in ()).throw(ConnectionError('нет сети')))
    s2, raw2, f2 = SS.goes_latest()
    assert f2.from_cache and f2.url == SS.URLS['goes'] and f2.fetched_utc is not None and f2.age_min is not None
    assert 'кеш, получено' in f2.status_ru and list(raw2.values())[0]['fetched_utc'] is not None


def test_tle_snapshot_uses_orbit_manifest_not_mtime(cache):
    f = SS._fallback('celestrak_gp', 'iss.tle', 'отказ')
    man = json.load(open(os.path.join(ROOT, 'data', 'orbit', 'manifest.json'), encoding='utf-8'))
    rec = next(r for r in man['records'] if r['file'] == 'iss.tle')
    assert f.raw_path.endswith(os.path.join('data', 'orbit', 'iss.tle'))
    assert f.fetched_utc == datetime.fromisoformat(rec['fetched_utc'].replace('Z', '+00:00'))
    assert f.url == rec['url'] and f.raw_response_sha256 == rec['sha256'] and f.fetched_basis == 'манифест A3'
    assert 'снимок репозитория' in f.status_ru and SS.tle_from_text(f.payload) is not None


def test_tle_live_keeps_raw_response_and_prefers_config_order(cache, monkeypatch):
    tle = open(os.path.join(ROOT, 'data', 'orbit', 'iss.tle'), encoding='utf-8').read()
    l1, l2 = [l for l in tle.splitlines() if l.startswith(('1 ', '2 '))]
    urls = ['https://primary.example/tle', 'https://backup.example/v1/tles']
    monkeypatch.setitem(SS.URLS, 'tle', urls)
    body_json = json.dumps({'tle_timestamp': 1789000000, 'line1': l1, 'line2': l2, 'header': 'ISS (ZARYA)'})

    def fake(url):
        if 'primary' in url:
            raise TimeoutError('timeout')
        return body_json
    monkeypatch.setattr(SS, '_live', fake)
    txt, f = SS.tle_latest()
    assert txt is not None and f.ok and f.url == urls[1] and 'primary.example' in f.status_ru
    assert f.raw_response_path and open(f.raw_response_path, encoding='utf-8').read() == body_json
    assert f.raw_response_sha256 == SS._sha256(body_json.encode('utf-8')) and f.provider_time is not None
    # оба адреса отвечают → в расчёт идёт первый по порядку настроек
    monkeypatch.setattr(SS, '_live', lambda url: tle if 'primary' in url else body_json)
    txt2, f2 = SS.tle_latest()
    assert f2.url == urls[0]


def test_kp_running_interval_is_flagged_as_preliminary(cache, monkeypatch):
    start = (datetime.now(UTC) - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    _live(monkeypatch, json.dumps({'datetime': [start.strftime('%Y-%m-%dT%H:%M:%SZ')], 'Kp': [2.333], 'status': ['pre']}))
    s, raw, f = SS.kp_latest()
    rec = list(raw.values())[0]
    assert s.valid_from_utc == start and s.valid_to_utc == start + timedelta(hours=3) and s.quality == 'preliminary'
    assert rec['interval_running_at_request'] is True and 'не завершён' in f.status_ru


def test_from_records_rebuilds_samples_without_network(cache, monkeypatch):
    monkeypatch.setattr(SS, '_live', lambda url: (_ for _ in ()).throw(AssertionError('сеть не должна использоваться')))
    now = datetime(2026, 9, 18, 22, 49, tzinfo=UTC)
    (g, graw, fg), (k, kraw, fk), (t, ft) = SS.from_records(
        {'time_tag': '2026-09-18T22:35:00Z', 'satellite': 18, 'flux': 0.3457, 'energy': '>=10 MeV', 'url': 'u', 'fetched_utc': '2026-09-18T22:40:00+00:00'},
        {'datetime': '2026-09-18T21:00:00Z', 'Kp': 1.333, 'status': 'pre', 'url': 'k', 'fetched_utc': '2026-09-18T22:40:00+00:00'},
        'ISS (ZARYA)\n1 …\n2 …\n', now, {'fetched_utc': '2026-09-18T22:40:00+00:00', 'fetch': 'x'})
    assert g.value == 0.3457 and k.value == 1.333 and t.startswith('ISS') and fg.from_cache and fk.from_cache
    assert 'сохранённого расчёта' in fg.status_ru and fg.fetched_utc == datetime(2026, 9, 18, 22, 40, tzinfo=UTC)


def test_fetch_none_declares_sources_not_requested():
    (g, graw, fg), (k, kraw, fk), (t, ft) = SS.fetch_none()
    assert g is None and k is None and t is None and not fg.ok and not fg.from_cache and 'не запрашивались' in fg.status_ru
