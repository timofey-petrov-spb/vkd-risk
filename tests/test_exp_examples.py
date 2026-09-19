# -*- coding: utf-8 -*-
"""Сохранённые примеры (examples/*.json, *.zip): соответствие коду и постановке (Т8, О4).
Проверяются снимки, а не пересчёт: пересчёт — scripts/replay_example.py."""
import glob
import io
import json
import os
import re
import zipfile
from datetime import datetime

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, 'examples')
JSONS = sorted(glob.glob(os.path.join(EX, '*.json')))


def _load(p):
    return json.load(io.open(p, encoding='utf-8'))


@pytest.mark.parametrize('path', JSONS, ids=[os.path.basename(p) for p in JSONS])
def test_condition_cards_trace_to_records_and_titles_keep_time(path):
    S = _load(path)
    for c in S['cards']:
        if not c['title'].startswith(('Условие', 'Сценарий')):
            continue
        assert not re.search(r' \d\d$', c['title']), 'заголовок обрезан по двоеточию времени: %s' % c['title']
        if 'DONKI — ' in c['data_ru']:
            assert c['record_ids'], 'условие без первоисточника: %s' % c['title']
            if c['title'].startswith('Условие'):
                assert all(rid.startswith('donki_') for rid in c['record_ids'])
        if c['title'].startswith('Условие') and 'прогноз' in c['title']:
            assert c['kind'] == 'external_forecast'


@pytest.mark.parametrize('path', [p for p in JSONS if _load(p)['mode_id'] == 'history_forecast'],
                         ids=[os.path.basename(p) for p in JSONS if _load(p)['mode_id'] == 'history_forecast'])
def test_strict_examples_use_only_dated_records_before_cutoff(path):
    S = _load(path)
    cutoff = datetime.fromisoformat(S['request']['cutoff_utc'])
    for e in S['history']['events_used']:
        assert not e['id'].startswith('donki_sep#'), 'карточка события DONKI в строгом режиме: %s' % e['id']
        if not e['simulated']:
            assert e['published_utc'] and datetime.fromisoformat(e['published_utc']) <= cutoff, e
    assert S['history']['excluded_by_cutoff']
    assert S['sources']['noaa_swpc_goes']['live_ok'] in (False, None)


@pytest.mark.parametrize('path', JSONS, ids=[os.path.basename(p) for p in JSONS])
def test_snapshot_and_zip_agree_and_manifest_is_versioned(path):
    S = _load(path)
    zp = path[:-5] + '.zip'
    assert os.path.exists(zp)
    with zipfile.ZipFile(zp) as z:
        man = json.loads(z.read('manifest.json').decode('utf-8'))
        rec = json.loads(z.read('recommendation.json').decode('utf-8'))
        names = z.namelist()
        raw = [json.loads(z.read(n)) for n in names if n.startswith('raw/') and n.endswith('.json')]
        goes = next((r for r in raw if r.get('metadata', {}).get('source_id') == 'noaa_swpc_goes'), None)
        if goes is None:
            goes = (json.loads(z.read(next(n for n in names if n.startswith('raw/goes_p10_'))).decode('utf-8'))
                    if any(n.startswith('raw/goes_p10_') for n in names) else None)
    assert man['algorithm_version'] == S['algorithm_version'] and man['git_commit']
    assert rec == S['recommendation']
    if S['mode_id'] == 'live':
        # текущий режим воспроизводим только с сырыми записями источников
        assert 'raw/iss.tle.json' in names
        if S['sources']['noaa_swpc_goes'].get('data_utc') is not None:
            assert goes is not None
        if goes is not None:
            if 'metadata' in goes:
                assert goes['metadata']['url'] and goes['content_base64']
            else:
                assert goes.get('url') and 'fetched_basis' in goes


def test_index_names_t5_pair_and_lists_every_example():
    txt = io.open(os.path.join(EX, 'INDEX.md'), encoding='utf-8').read()
    assert 'Пара для Т5' in txt and 'gannon_2024-05-10_cutoff12Z' in txt
    assert 'Факт после отсечки' in txt and 'коммит кода' in txt
    for p in JSONS:
        assert os.path.basename(p)[:-5] in txt
