"""Acquisition failures and scientific input semantics, without external network."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest
import requests

from vkd.sources import goes_latest, kp_latest, noaa_latest, tle_latest
from vkd.sources.live_cache import MAX_BYTES
from vkd.sources.live_parsers import LiveDataError, parse_goes, parse_kp, parse_tle

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 18, 22, 30, tzinfo=timezone.utc)


class Response:
    def __init__(self, raw=b'', status=200, headers=None):
        self.raw = raw
        self.status_code = status
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, size):
        for i in range(0, len(self.raw), size):
            yield self.raw[i:i+size]

    def close(self):
        self.closed = True


def goes_bytes(value=.25, t=NOW, **extra):
    return json.dumps([dict(time_tag=t.isoformat(), energy='>=10 MeV', flux=value, satellite=18, **extra)]).encode()


def get_goes(tmp_path, raw=None, **kwargs):
    transport = kwargs.pop('transport', Mock(return_value=Response(raw or goes_bytes())))
    return goes_latest(cache_dir=tmp_path, now=kwargs.pop('now', NOW), transport=transport, **kwargs)


def test_exact_bytes_and_publication_unknown(tmp_path):
    raw = b' \n' + goes_bytes() + b'\n'
    sample, records, f = get_goes(tmp_path, raw)
    assert sample.value == .25 and sample.unit == 'pfu'
    assert sample.published_utc is None and sample.valid_to_utc is None
    assert f.ok and not f.from_cache and f.age_min == 0
    rec = records[sample.raw_record_id]
    assert base64.b64decode(rec['content_base64']) == raw
    assert Path(f.raw_path).read_bytes() == raw
    assert rec['metadata']['sha256'] == hashlib.sha256(raw).hexdigest()
    assert rec['metadata']['available_utc'] == '2026-09-18T22:30:00Z'


@pytest.mark.parametrize('bad', [None, -1, float('nan'), float('inf'), True, '0.1'])
def test_goes_bad_latest_is_not_fake_zero(bad):
    rows = json.loads(goes_bytes(.5, NOW-timedelta(minutes=5))) + json.loads(goes_bytes(bad))
    p = parse_goes(json.dumps(rows).encode(), NOW)
    assert p['value'] == .5 and p['rejected_rows'] == 1


def test_goes_other_channel_future_quality_and_conflict():
    rows = json.loads(goes_bytes())
    rows += json.loads(goes_bytes(9999, NOW+timedelta(minutes=5)))
    rows += json.loads(goes_bytes(123, quality_flag=0))
    rows += [dict(time_tag=NOW.isoformat(), energy='>=100 MeV', flux=1e6, satellite=18)]
    p = parse_goes(json.dumps(rows).encode(), NOW)
    assert p['value'] == .25 and p['rejected_rows'] == 2
    with pytest.raises(LiveDataError, match='Conflicting'):
        parse_goes(goes_bytes()[:-1] + b',' + goes_bytes(4)[1:], NOW)


def test_kp_completed_interval_and_status(tmp_path):
    raw = json.dumps(dict(datetime=['2026-09-18T21:00:00Z', '2026-09-18T18:00:00Z'],
                          Kp=[8.333, 7.667], status=['pre', 'def'], meta={'license': 'CC BY 4.0'})).encode()
    sample, records, f = kp_latest(cache_dir=tmp_path, now=NOW, transport=Mock(return_value=Response(raw)))
    assert sample.value == 7.667 and sample.quality == 'final'
    assert sample.t_utc == sample.valid_to_utc == NOW.replace(hour=21, minute=0)
    assert sample.valid_from_utc == NOW.replace(hour=18, minute=0)
    assert f.parsed['ongoing_rows'] == 1 and f.age_min == 90
    assert sample.raw_record_id in records
    assert sample.published_utc is None


@pytest.mark.parametrize('change', [{'Kp': [1]}, {'status': ['pre']}, {'datetime': ['bad', 'bad']},
                                     {'Kp': [1.7, 100]}, {'Kp': [None, -1]}])
def test_kp_bad_arrays_or_values(change):
    raw = dict(datetime=['2026-09-18T15:00:00Z', '2026-09-18T18:00:00Z'], Kp=[1, 2], status=['pre', 'def'])
    raw.update(change)
    with pytest.raises(ValueError):
        parse_kp(json.dumps(raw).encode(), NOW)


def test_offline_cache_and_session_isolation(tmp_path):
    original, _, live = get_goes(tmp_path)
    never = Mock(side_effect=AssertionError('disabled must not access network'))
    a, raw_a, cached = get_goes(tmp_path, disabled=True, now=NOW+timedelta(minutes=2), transport=never)
    assert a == original and cached.from_cache and not cached.ok and cached.age_min == 2
    raw_a[a.raw_record_id]['metadata']['quality'] = 'tampered'
    b, raw_b, _ = get_goes(tmp_path, disabled=True, transport=never)
    assert raw_b[b.raw_record_id]['metadata']['quality'] == 'preliminary'
    fresh, _, f = get_goes(tmp_path, goes_bytes(11, NOW+timedelta(minutes=6)), now=NOW+timedelta(minutes=6))
    assert fresh.value == 11 and f.ok
    never.assert_not_called()


def test_disabled_without_cache_is_none_and_does_not_write(tmp_path):
    sample, raw, f = get_goes(tmp_path, disabled=True)
    assert sample is None and raw == {} and f.status == 'disabled'
    assert not list(tmp_path.rglob('*'))


def test_stale_data_age_uses_observation_not_receipt(tmp_path):
    sample, raw, f = get_goes(tmp_path, goes_bytes(.1, NOW-timedelta(hours=2)))
    assert sample is None and f.status == 'stale' and f.age_min == 120
    assert raw and not f.metadata['admissibility']['usable']
    sample, _, f = get_goes(tmp_path, disabled=True, now=NOW+timedelta(minutes=1))
    assert sample is None and f.age_min == 121


@pytest.mark.parametrize('response,error,requests_count', [
    (Response(b''), 'empty_response', 1), (Response(b'<html>broken</html>'), 'invalid_response', 1),
    (Response(status=403), 'http_403', 1), (Response(status=500), 'http_500', 2),
    (Response(status=429, headers={'Retry-After': '1200'}), 'http_429', 1),
])
def test_failure_keeps_last_valid_bytes(tmp_path, response, error, requests_count):
    old, _, _ = get_goes(tmp_path)
    get = Mock(return_value=response)
    sample, _, f = get_goes(tmp_path, now=NOW+timedelta(minutes=6), transport=get)
    assert sample == old and f.from_cache and not f.ok and f.error == error
    assert get.call_count == requests_count and response.closed
    assert len(list(tmp_path.rglob('receipts/*.json'))) == 1


def test_retry_after_and_forced_refresh_cannot_bypass_backoff(tmp_path):
    get = Mock(return_value=Response(status=429, headers={'Retry-After': '1200'}))
    get_goes(tmp_path, transport=get)
    _, _, f = get_goes(tmp_path, transport=get, now=NOW+timedelta(minutes=10), force_refresh=True)
    assert f.status == 'cooldown' and f.error == 'http_429' and get.call_count == 1
    sample, _, f = get_goes(tmp_path, goes_bytes(4, NOW+timedelta(minutes=21)), now=NOW+timedelta(minutes=21))
    assert sample.value == 4 and f.ok


def test_timeout_retry_limit_and_recovery(tmp_path):
    get = Mock(side_effect=[requests.Timeout(), Response(goes_bytes())])
    sample, _, f = get_goes(tmp_path, transport=get)
    assert sample.value == .25 and f.ok and get.call_count == 2
    get = Mock(side_effect=requests.Timeout())
    _, _, f = get_goes(tmp_path, now=NOW+timedelta(minutes=6), transport=get)
    assert f.error == 'timeout' and get.call_count == 2


def test_corrupt_cache_rejected_and_no_mtime_publication(tmp_path):
    _, _, f = get_goes(tmp_path)
    Path(f.raw_path).write_bytes(b'corruption')
    sample, _, f = get_goes(tmp_path, disabled=True)
    assert sample is None and f.status == 'invalid_cache'
    # A naked cache file without a receipt cannot invent availability from mtime.
    (tmp_path/'untracked.json').write_bytes(goes_bytes())
    assert get_goes(tmp_path, disabled=True)[0] is None


def test_oversized_payload_does_not_poison_cache(tmp_path):
    _, _, f = get_goes(tmp_path, b'x'*(MAX_BYTES+1))
    assert f.error == 'invalid_response' and f.payload is None
    assert not list(tmp_path.rglob('receipts/*.json'))


def test_parallel_sessions_make_one_request(tmp_path):
    entered, release = Event(), Event()
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return Response(goes_bytes())
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(get_goes, tmp_path, transport=slow)
        assert entered.wait(3)
        second = get_goes(tmp_path, transport=Mock(side_effect=AssertionError('second download')))
        assert second[0] is None and second[2].status == 'busy'
        release.set()
        assert first.result()[0].value == .25
    assert len(list(tmp_path.rglob('receipts/*.json'))) == 1


def test_tle_identity_checksum_epoch_and_throttle(tmp_path):
    raw = (ROOT/'data/orbit/iss.tle').read_bytes()
    get = Mock(return_value=Response(raw))
    text, f = tle_latest(cache_dir=tmp_path, now=NOW, transport=get, use_bundled=False)
    assert text.encode() == raw and f.parsed['norad_id'] == 25544
    _, f = tle_latest(cache_dir=tmp_path, now=NOW+timedelta(minutes=90), transport=get,
                      force_refresh=True, use_bundled=False)
    assert get.call_count == 1 and f.from_cache
    with pytest.raises(ValueError):
        parse_tle(raw.replace(b'25544', b'25545'), NOW)
    with pytest.raises(ValueError):
        parse_tle(raw, NOW-timedelta(days=4))
    text, f = tle_latest(cache_dir=tmp_path, now=NOW+timedelta(days=4), disabled=True, use_bundled=False)
    assert text is None and f.status == 'stale'


@pytest.mark.parametrize('status', [301, 403, 429, 500])
def test_celestrak_never_retries_non_200(tmp_path, status):
    get = Mock(return_value=Response(status=status))
    text, f = tle_latest(cache_dir=tmp_path, now=NOW, transport=get, use_bundled=False)
    assert text is None and f.error == f'http_{status}' and get.call_count == 1
    tle_latest(cache_dir=tmp_path, now=NOW+timedelta(minutes=119), transport=get, use_bundled=False, force_refresh=True)
    assert get.call_count == 1


def test_noaa_bulletin_retains_original_intervals(tmp_path):
    from vkd.sources.registry import SourceRegistry
    record = SourceRegistry(ROOT).records('noaa_ngdc_3day_forecast')[0]
    now = datetime.fromisoformat(record['published_utc'].replace('Z', '+00:00')) + timedelta(hours=1)
    raw = (ROOT/record['raw_path']).read_bytes()
    samples, records, f = noaa_latest(cache_dir=tmp_path, now=now, transport=Mock(return_value=Response(raw)))
    assert f.ok and len(samples) == 27
    assert all(c['raw_record_id'] == f.metadata['raw_record_id']
               and c['source_id'] == 'noaa_swpc_3day_forecast' for c in f.parsed['cells'])
    assert {s.channel_id for s in samples} == {'kp_forecast', 's1_prob_daily'}
    assert all(s.kind.value == 'external_forecast' for s in samples)
    for sample in samples:
        assert sample.raw_record_id in records
        assert sample.published_utc == now-timedelta(hours=1)
        assert (sample.valid_to_utc-sample.valid_from_utc).total_seconds() == (86400 if sample.unit == '%' else 10800)


def test_invalid_inputs_and_poll_metadata_fail_closed(tmp_path):
    with pytest.raises(ValueError):
        get_goes(tmp_path, max_age_min=float('nan'))
    with pytest.raises(ValueError):
        get_goes(tmp_path, disabled='false')
    get_goes(tmp_path)
    next(tmp_path.rglob('attempt.json')).write_text('broken')
    get = Mock(side_effect=AssertionError('corrupt gate must not trigger traffic'))
    _, _, f = get_goes(tmp_path, transport=get, force_refresh=True)
    assert f.error == 'invalid_poll_metadata'
    get.assert_not_called()


def test_server_regression_does_not_replace_newer_cache(tmp_path):
    original, _, _ = get_goes(tmp_path, goes_bytes(11))
    sample, _, f = get_goes(tmp_path, goes_bytes(.01, NOW-timedelta(hours=1)),
                             now=NOW+timedelta(minutes=6))
    assert sample == original and f.error == 'regressed_response'
    assert len(list(tmp_path.rglob('receipts/*.json'))) == 1


def test_cache_receipt_publication_tampering_is_detected(tmp_path):
    get_goes(tmp_path)
    receipt = next(tmp_path.rglob('receipts/*.json'))
    rec = json.loads(receipt.read_text())
    rec['published_utc'] = '2024-05-01T00:00:00Z'
    receipt.write_text(json.dumps(rec))
    sample, _, f = get_goes(tmp_path, disabled=True)
    assert sample is None and f.status == 'invalid_cache'


def test_live_app_uses_real_sources_and_exports_exact_raw(tmp_path):
    from app.compute import run, SRC_LAYER, HIST_SRC
    from app.export import build_zip
    from io import BytesIO
    from zipfile import ZipFile
    assert (SRC_LAYER, HIST_SRC) == ('vkd.sources', 'vkd.history')
    goes = get_goes(tmp_path)
    kp_raw = json.dumps(dict(datetime=['2026-09-18T18:00:00Z'], Kp=[1.333], status=['pre'])).encode()
    kp = kp_latest(cache_dir=tmp_path, now=NOW, transport=Mock(return_value=Response(kp_raw)))
    tle_raw = (ROOT/'data/orbit/iss.tle').read_bytes()
    tle = tle_latest(cache_dir=tmp_path, now=NOW, transport=Mock(return_value=Response(tle_raw)), use_bundled=False)
    r = run('live', NOW, 60, 60, [0, 60], fetched=(goes, kp, tle), now=NOW)
    assert r.meta and len(r.traj) == 121
    assert r.goes.value == .25 and r.kp.value == 1.333
    with ZipFile(BytesIO(build_zip(r.S, r.raw_records))) as z:
        record = next(json.loads(z.read(name)) for name in z.namelist()
                      if name.startswith('raw/noaa_swpc_goes-'))
    assert base64.b64decode(record['content_base64']) == goes_bytes()


def test_external_settings_are_applied_and_recorded(tmp_path, monkeypatch):
    import vkd.config as cfg
    config = tmp_path/'settings.toml'
    config.write_text('[sources]\ntimeout_s=4\ncache_ttl_s=1200\n[sources.urls]\ngoes="https://example.invalid/goes"\n')
    monkeypatch.setenv('VKD_SETTINGS', str(config))
    cfg.settings.cache_clear()
    try:
        get = Mock(return_value=Response(goes_bytes()))
        _, _, f = get_goes(tmp_path/'cache', transport=get)
        assert get.call_args.args[0] == 'https://example.invalid/goes'
        assert get.call_args.kwargs['timeout'] == (3.05, 4)
        assert f.metadata['effective_config']['poll_seconds'] == 1200
        assert f.metadata['url'] == 'https://example.invalid/goes'
        config.write_text('[sources]\ntimeout_s=-1\n')
        cfg.settings.cache_clear()
        with pytest.raises(ValueError):
            get_goes(tmp_path/'cache', transport=get)
    finally:
        cfg.settings.cache_clear()


def test_changed_endpoint_does_not_relabel_existing_cache(tmp_path, monkeypatch):
    import vkd.config as cfg
    get_goes(tmp_path/'cache')
    config = tmp_path/'settings.toml'
    config.write_text('[sources.urls]\ngoes="https://example.invalid/different-product"\n')
    monkeypatch.setenv('VKD_SETTINGS', str(config))
    cfg.settings.cache_clear()
    try:
        sample, _, f = get_goes(tmp_path/'cache', disabled=True)
        assert sample is None and f.payload is None
    finally:
        cfg.settings.cache_clear()


def test_windows_lock_uses_same_byte_and_maps_contention(tmp_path, monkeypatch):
    import errno
    import sys
    from types import SimpleNamespace
    import vkd.sources.live_cache as cache
    lock = Mock()
    monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(locking=lock, LK_UNLCK=0, LK_NBLCK=2))
    monkeypatch.setattr(cache, '_WINDOWS', True)
    with (tmp_path/'lock').open('a+b') as handle:
        handle.write(b'\0'); handle.flush()
        cache._file_lock(handle)
        assert handle.tell() == 0
        lock.assert_called_with(handle.fileno(), 2, 1)
        cache._file_lock(handle, release=True)
        lock.assert_called_with(handle.fileno(), 0, 1)
        lock.side_effect = OSError(errno.EACCES, 'locked')
        with pytest.raises(BlockingIOError):
            cache._file_lock(handle)
