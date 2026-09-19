# -*- coding: utf-8 -*-
"""Живой опрос уведомлений NASA DONKI (двенадцатый круг, п. 6).

Что проверяется и почему именно это.

Владелец сказал дословно: «как это живого ответа нет, мы же подключаем базы данных» и «почему
событий на горизонте ноль». За обеими фразами стояли две разные вещи, и обе проверяются здесь:

  (а) источник уведомлений в текущем режиме НЕ ОПРАШИВАЛСЯ вовсе — теперь опрашивается, и это
      видно в снимке расчёта числом сообщений и моментом опроса, а не словами;
  (б) отказ источника обязан называться причиной, а не подменяться пустым перечнем: «событий
      нет» и «мы не спросили» — разные вещи.

Сети здесь нет ни в одной проверке: используются сохранённые байты ответов службы (реестр A1,
май 2024) и подставной транспорт. Иначе проверка зависела бы от того, что происходит на Солнце
в день прогона, и от связи площадки.

Побайтовое совпадение с архивом A1 проверяется отдельно: живой опрос обязан давать сообщению ТОТ
ЖЕ идентификатор и тот же хеш, что архив, иначе одна и та же запись называлась бы в выгрузке
двумя именами.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
import requests

import app.fetch_guard as fg
import vkd.config as cfg
from vkd.integration.donki_live import (FEED_WINDOW_DAYS, MAX_AGE_MIN, _issue_times, canonical_message,
                                        coverage_ru, donki_latest, parse_notifications, summary_ru)
from vkd.sources.live_parsers import LiveDataError
from vkd.sources.registry import SourceRegistry

UTC = timezone.utc
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESPONSE_MAY = os.path.join(ROOT, 'data', 'source_registry_2024', 'donki', 'raw',
                            'notifications_2024-05_response.json')
# Момент опроса для проверок. Сохранённый майский ответ службы содержит сообщения, выпущенные
# 01.06.2024 до 20:21Z (запрос делался по 01.06 включительно), поэтому момент взят позже их всех:
# уведомление, выпущенное позже момента запроса, разборщик законно отбрасывает как «из будущего»,
# и при более раннем моменте проверка мерила бы этот отбор, а не то, что собиралась мерить.
NOW = datetime(2024, 6, 2, 12, tzinfo=UTC)


def response_bytes(path: str = RESPONSE_MAY) -> bytes:
    with open(path, 'rb') as fh:
        return fh.read()


class Response:
    """Подставной ответ транспорта — тот же приём, что в проверках слоя источников."""

    def __init__(self, raw=b'[]', status=200, headers=None):
        self.raw, self.status_code, self.headers = raw, status, headers or {}

    def iter_content(self, size):
        for i in range(0, len(self.raw), size):
            yield self.raw[i:i + size]

    def close(self):
        pass


# ===================================================== каноническая форма сообщения и хеш записи
def test_kanonicheskaya_forma_sovpadaet_s_arhivom_pobaytno():
    """Сообщение, вырезанное из живого ответа, побайтно равно записи архива A1.

    Это и есть условие, при котором живой опрос и архив называют одну запись одним именем:
    идентификатор записи содержит первые 12 знаков sha256 канонических байтов.
    """
    registry = SourceRegistry(ROOT)
    records = {r['release_id']: r for r in registry.records('nasa_donki_notification')}
    messages = {m['messageID']: m for m in json.loads(response_bytes())}
    checked = 0
    for release_id, record in records.items():
        if release_id not in messages:
            continue                       # запись из апрельского или июньского ответа
        canonical = canonical_message(messages[release_id])
        assert canonical == registry.raw_bytes(record['raw_record_id']), release_id
        assert hashlib.sha256(canonical).hexdigest() == record['sha256'], release_id
        assert record['raw_record_id'].endswith(record['sha256'][:12]), release_id
        checked += 1
    # 175 — столько сообщений майского ответа службы одновременно проиндексировано реестром A1
    # (всего в реестре 247 записей за три запроса: 30.04, май и июнь). Число измерено, а не
    # взято с потолка: если архив изменится, проверка обязана это заметить, а не промолчать.
    assert checked == 175, 'сверено записей: %d' % checked


# ===================================================== разбор ответа службы до записи в кеш
def test_vremya_dannyh_lenty_eto_vypusk_samogo_pozdnego_uvedomleniya():
    """У перечня сообщений нет своего момента измерения; временем данных объявлен выпуск
    самого позднего уведомления — и он вычисляется ТОЛЬКО из байтов ответа."""
    raw = response_bytes()
    parsed = parse_notifications(raw, NOW)
    # ожидаемое значение считается ТЕМ ЖЕ правилом, что объявлено в мосте: позднее из времени
    # выпуска в ленте и времени выпуска в теле сообщения (у тела бывают секунды, у ленты — нет)
    issued = [max([t for t in _issue_times(m) if t]) for m in json.loads(raw)]
    assert parsed['data_utc'] == max(issued)
    assert parsed['published_utc'] == parsed['data_utc']
    # то же самое при другом моменте запроса: квитанция иначе расходилась бы сама с собой
    assert parse_notifications(raw, NOW + timedelta(days=3))['data_utc'] == parsed['data_utc']
    assert parsed['selected']['message_count'] == len(issued)
    assert parsed['selected']['feed_window_days'] == FEED_WINDOW_DAYS


@pytest.mark.parametrize('raw, why', [
    (b'{"messageID": "1"}', 'ответ не перечень'),
    (b'[]', 'перечень пуст'),
    (b'[{"messageID": "x", "messageType": "GST"}]', 'сообщение без тела'),
])
def test_neprigodnyy_otvet_ne_vydayotsya_za_uspeshnyy_opros(raw, why):
    """Пустой или непригодный ответ — отказ разбора, а не «уведомлений нет»."""
    with pytest.raises(LiveDataError):
        parse_notifications(raw, NOW)


def test_uvedomlenie_iz_budushchego_otbrasyvaetsya():
    """Сообщение, выпущенное позже момента запроса, в ленту не попадает."""
    messages = json.loads(response_bytes())[:3]
    for m in messages:
        m['messageIssueTime'] = '2030-01-01T00:00Z'
        m['messageBody'] = m['messageBody'].replace('## Message Issue Date: ',
                                                    '## Message Issue Date: ')
    with pytest.raises(LiveDataError):
        parse_notifications(json.dumps(messages).encode(), NOW)


def test_soobshchenie_bez_vremeni_vypuska_nazyvaetsya_a_ne_ronyaet_lentu(tmp_path):
    """Одно сообщение без времени выпуска не отменяет ленту: оно названо неразобранным."""
    messages = json.loads(response_bytes())[:4]
    del messages[1]['messageIssueTime']
    notes, _, f = _fetch_from_transport(tmp_path, json.dumps(messages, ensure_ascii=False).encode())
    assert notes.connected is True
    assert len(notes.not_parsed) == 1, notes.not_parsed
    assert notes.not_parsed[0][0] == messages[1]['messageID']
    assert notes.message_count == 4 and sum(notes.by_status.values()) == 4


def test_neprigodnye_soobshcheniya_schitayutsya_a_ne_ischezayut():
    """Одно битое сообщение не отменяет ленту, но и не пропадает: оно сосчитано."""
    messages = json.loads(response_bytes())[:5] + [{'messageID': 'битое'}]
    parsed = parse_notifications(json.dumps(messages).encode(), NOW)
    assert parsed['rejected_rows'] == 1
    assert parsed['selected']['message_count'] == 5


# ===================================================== разбор ленты в события и факты
def _fetch_from_transport(tmp_path, raw, now=NOW, **kwargs):
    return donki_latest(cache_dir=tmp_path, now=now, transport=Mock(return_value=Response(raw)), **kwargs)


def test_lenta_dayot_sobytiya_fakty_i_zapisi(tmp_path):
    """Разбор тел сообщений остаётся один на весь проект: события и факты приходят от него."""
    notes, raw_records, f = _fetch_from_transport(tmp_path, response_bytes())
    assert f.status == 'live' and notes.connected is True
    assert notes.message_count == len(json.loads(response_bytes()))
    # буря Гэннон 10.05.2024 — уведомление о буре и уведомления о приходе выброса в этом же ответе
    kinds = {e.kind_of_event for e in notes.events}
    assert {'GST', 'CME_ARRIVAL', 'SEP'} <= kinds, kinds
    # у каждого события есть запись с метаданными и каноническими байтами
    for e in notes.events:
        assert e.raw_record_id in notes.records, e.raw_record_id
        assert e.raw_record_id in notes.bodies, e.raw_record_id
        meta = notes.records[e.raw_record_id]
        assert meta['sha256'] == hashlib.sha256(notes.bodies[e.raw_record_id]).hexdigest()
        assert meta['source_id'] == 'nasa_donki_notification'
    # структурированные факты бури — наблюдённый Kp, а не пересказ русской заметки
    gst = [e for e in notes.events if e.kind_of_event == 'GST']
    assert any(notes.facts.get(e.raw_record_id, {}).get('kp') for e in gst), notes.facts
    assert not notes.not_parsed, notes.not_parsed
    # состояния разбора названы поимённо, и их сумма равна числу сообщений
    assert sum(notes.by_status.values()) == notes.message_count
    assert sum(notes.by_type.values()) == notes.message_count


def test_summary_i_ohvat_na_russkom_bez_identifikatorov(tmp_path):
    """На оперативном уровне ни идентификаторов кода, ни английских слов, ни голых адресов."""
    notes, _, _ = _fetch_from_transport(tmp_path, response_bytes())
    for text in (summary_ru(notes), coverage_ru(notes)):
        assert 'DONKI' not in text or 'NASA' in text
        for forbidden in ('messageType', 'http', 'None', 'parsed', 'out_of_scope'):
            assert forbidden not in text, (forbidden, text)


# ===================================================== кеш, квитанция и отказ источника
def test_kvitanciya_perechityvaetsya_i_ne_schitaetsya_povrezhdyonnoy(tmp_path):
    """Второй вызов берёт кеш: время данных вычислено из байтов, поэтому квитанция сходится.

    Если бы время данных зависело от момента запроса, перечитанная квитанция каждый раз
    расходилась бы сама с собой и слой источников объявлял бы кеш повреждённым.
    """
    raw = response_bytes()
    first, _, f1 = _fetch_from_transport(tmp_path, raw)
    assert f1.status == 'live'
    transport = Mock(side_effect=AssertionError('второго обращения к сети быть не должно'))
    notes, _, f2 = donki_latest(cache_dir=tmp_path, now=NOW + timedelta(minutes=1), transport=transport)
    assert f2.from_cache and f2.status == 'cached', f2.status_ru
    assert 'повреждённая запись кеша' not in f2.status_ru, f2.status_ru
    assert notes.connected and notes.message_count == first.message_count


def test_otkaz_seti_dayot_prichinu_a_ne_pustoy_perechen(tmp_path):
    """Сеть не ответила и кеша нет: `connected` ложь, причина названа, событий НОЛЬ не объявлено."""
    transport = Mock(side_effect=requests.Timeout('нет ответа'))
    notes, raw_records, f = donki_latest(cache_dir=tmp_path, now=NOW, transport=transport)
    assert notes.connected is False and notes.events == ()
    assert notes.reason_ru and 'нет' in notes.reason_ru.lower(), notes.reason_ru
    assert raw_records == {}
    assert f.payload is None and f.ok is False


def test_istochnik_isklyuchyonnyy_polzovatelem_ne_hodit_v_set(tmp_path):
    """'off' — не отказ сети: обращения нет вовсе, и данных нет."""
    transport = Mock(side_effect=AssertionError('обращения к сети быть не должно'))
    notes, _, f = donki_latest(disabled='off', cache_dir=tmp_path, now=NOW, transport=transport)
    assert notes.connected is False and f.status == 'off'


def test_ustarevshaya_lenta_ne_ispolzuetsya_molcha(tmp_path):
    """Ответ старше допустимой давности не превращается в текущую картину."""
    notes, _, f = _fetch_from_transport(tmp_path, response_bytes(),
                                        now=NOW + timedelta(minutes=MAX_AGE_MIN))
    assert f.status == 'stale' and notes.connected is False
    assert 'устарело' in f.status_ru, f.status_ru


# ===================================================== общий предел получения (app/fetch_guard.py)
def test_lenta_vhodit_v_obshchiy_predel_polucheniya():
    """Уведомления идут тем же слоем и тем же пределом, что остальные живые источники."""
    assert fg.KEYS[-1] == 'donki', fg.KEYS
    assert fg.SOURCE_ID['donki'] == 'nasa_donki_notification'
    assert fg.modes()['donki'] is False
    # причина отказа по общему пределу проставляется и этому источнику
    empty = fg.Fetch('nasa_donki_notification', False, False, None, None, 'источник отключён', None, None)
    marked = fg.mark(tuple([(None, {}, empty)] * len(fg.KEYS)), fg.modes(), 'превышен предел')
    assert len(marked) == len(fg.KEYS)
    assert 'превышен предел' in marked[-1][-1].status_ru


def test_klyuch_obnovleniya_chitaetsya_iz_nastroek_i_proveryaetsya(tmp_path, monkeypatch):
    """`refresh_each_render` — настройка вне кода; неверное значение даёт ошибку, а не умолчание."""
    cfg.settings.cache_clear()
    assert fg.refresh_each_render() is bool(cfg.section('sources').get('refresh_each_render', True))
    p = tmp_path / 'bad.toml'
    p.write_text('[sources]\nrefresh_each_render = 1\n', encoding='utf-8')
    monkeypatch.setenv('VKD_SETTINGS', str(p))
    cfg.settings.cache_clear()
    with pytest.raises(ValueError, match='refresh_each_render'):
        fg.refresh_each_render()
    monkeypatch.delenv('VKD_SETTINGS', raising=False)
    cfg.settings.cache_clear()


def test_adres_sluzhby_chitaetsya_iz_nastroek_i_proveryaetsya(tmp_path, monkeypatch):
    """Адрес — из `config/settings.toml`; не-https отвергается до обращения к сети."""
    p = tmp_path / 'bad.toml'
    p.write_text('[sources.urls]\ndonki = "http://example.org/x"\n', encoding='utf-8')
    monkeypatch.setenv('VKD_SETTINGS', str(p))
    cfg.settings.cache_clear()
    with pytest.raises(ValueError, match='donki'):
        donki_latest(cache_dir=tmp_path, now=NOW, transport=Mock())
    monkeypatch.delenv('VKD_SETTINGS', raising=False)
    cfg.settings.cache_clear()


# ===================================================== лента доходит до расчёта и до условий
def _shift(messages, delta: timedelta):
    """Сдвинуть выпуск и все времена сообщений на заданный промежуток — чтобы архивные
    уведомления пришлись на горизонт проверяемого расчёта."""
    import re
    stamp = re.compile(r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(:\d{2}(?:\.\d+)?)?Z')

    def move(text):
        def one(m):
            base = datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), tzinfo=UTC) + delta
            return base.strftime('%Y-%m-%dT%H:%M') + (m[6] or '') + 'Z'
        return stamp.sub(one, text)

    out = []
    for m in messages:
        item = dict(m)
        item['messageIssueTime'] = move(m['messageIssueTime'])
        item['messageBody'] = move(m['messageBody'])
        out.append(item)
    return out


def test_zhivoe_uvedomlenie_o_protonnom_sobytii_stavit_uslovie_v_raschyote(tmp_path):
    """Сквозная проверка: уведомление на горизонте доходит до условия проверки окна.

    Без неё «подключено» означало бы только то, что запрос состоялся, а не то, что данные
    участвуют в решении.
    """
    from app.compute import run
    from vkd.integration.replay_live import fetch_none

    t0 = datetime(2026, 9, 19, 12, tzinfo=UTC)
    original = json.loads(response_bytes())
    sep = [m for m in original if m['messageType'] == 'SEP']
    assert sep, 'в сохранённом ответе нет уведомлений о протонном событии'
    # сдвигаем май 2024 к дате расчёта: время события внутри горизонта, выпуск — раньше начала
    delta = t0.replace(minute=0) - datetime(2024, 5, 10, 12, tzinfo=UTC)
    shifted = _shift(sep, delta)
    notes, _, f = _fetch_from_transport(tmp_path, json.dumps(shifted, ensure_ascii=False).encode(),
                                        now=t0 + timedelta(hours=1))
    assert notes.connected and notes.events, summary_ru(notes)

    fetched = list(fetch_none('источник не запрашивался в этой проверке'))
    fetched[4] = (notes, {}, f)
    r = run('live', t0, 360, 720, [0, 240], now=t0, fetched=tuple(fetched))
    line = r.S['events_line']
    assert line['connected'] is True and line['records'] >= 1, line
    # условие проверки поставлено именно по уведомлению, и запись названа
    kinds = {c.kind for a in r.assessments for m in a.mechanisms for c in m.conditions}
    assert 'SEP' in kinds, kinds
    used = r.S['sources']['donki_live']
    assert used['events_used'] >= 1 and used['record_ids'], used
    # каждая учтённая запись лежит в выгрузке своими байтами
    for rid in used['record_ids']:
        assert rid in r.raw_records, rid
