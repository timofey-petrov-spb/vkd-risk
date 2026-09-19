# -*- coding: utf-8 -*-
"""Снимок орбиты из репозитория против кеша: берётся СВЕЖИЙ, а не «какой есть».

Находка 19.09 на развёрнутом сервисе: приборная полоса показывала эпоху элементов 17.09 21:14,
хотя в репозитории лежал набор с эпохой 18.09 03:25. Причина была в одной строке
`vkd/sources/live_cache.py`: снимок репозитория подставлялся только когда кеш ПУСТ
(`if entry is None and use_bundled`), поэтому любая, даже более старая, запись кеша закрывала
дорогу более свежему набору. Орбита считалась по элементам на шесть часов старше доступных.

Правка сравнивает времена САМИХ ДАННЫХ (`data_utc` — эпоха элементов), а не наличие файла и не
время получения: получить старые элементы можно и позже новых.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.sources.test_live import Response
from vkd.sources import tle_latest

ROOT = Path(__file__).resolve().parents[2]
BUNDLED = (ROOT / 'data/orbit/iss.tle').read_bytes()
# Момент расчёта позже эпохи снимка репозитория (18.09 03:25) и позже обеих подставляемых эпох:
# разборщик отвергает элементы с эпохой в будущем, и это его правильное поведение.
NOW = datetime(2026, 9, 18, 22, 30, tzinfo=timezone.utc)
ONLY_CELESTRAK = 'https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE'


def _checksummed(line: str) -> str:
    """Контрольная сумма строки TLE (позиция 69): сумма цифр, каждый минус считается единицей."""
    body = line[:68]
    total = sum(int(c) for c in body if c.isdigit()) + body.count('-')
    return body + str(total % 10)


def tle_with_epoch(epoch_field: str) -> bytes:
    """Тот же набор элементов с другой эпохой. Позиции 19–32 первой строки — эпоха YYDDD.DDDDDDDD."""
    name, line1, line2 = BUNDLED.decode('ascii').splitlines()[:3]
    assert len(epoch_field) == 14, epoch_field
    return ('\n'.join([name, _checksummed(line1[:18] + epoch_field + line1[32:]), line2]) + '\n').encode('ascii')


OLDER = tle_with_epoch('26260.88472222')     # 17.09.2026 21:14 — то, что показывал сервис
NEWER = tle_with_epoch('26261.50000000')     # 18.09.2026 12:00 — свежее снимка репозитория


def _seed_cache(tmp_path, raw: bytes):
    """Кладёт в кеш квитанцию с этими элементами: один живой ответ на адрес CelesTrak."""
    text, f = tle_latest(cache_dir=tmp_path, now=NOW, transport=Mock(return_value=Response(raw)),
                         use_bundled=False)
    assert text is not None, f.status_ru
    return text


@pytest.fixture(autouse=True)
def only_celestrak(monkeypatch):
    """Резервные адреса в этой проверке не участвуют: снимок репозитория относится к CelesTrak,
    и выбор между ним и кешем должен быть виден без примеси выбора между адресами."""
    import vkd.config as cfg
    original = cfg.section

    def section(name):
        result = original(name)
        return {**result, 'urls': {**result.get('urls', {}), 'tle': [ONLY_CELESTRAK]}} if name == 'sources' else result
    monkeypatch.setattr(cfg, 'section', section)


def epoch_of(text: str) -> str:
    return text.splitlines()[1][18:32]


def test_kesh_staree_snimka_beryotsya_snimok(tmp_path):
    """Главный случай находки: в кеше элементы 17.09 21:14, в репозитории — 18.09 03:25."""
    _seed_cache(tmp_path, OLDER)
    text, f = tle_latest(disabled='cache', cache_dir=tmp_path, now=NOW, use_bundled=True)
    assert epoch_of(text) == '26261.14280998', (epoch_of(text), f.status_ru)
    # та самая эпоха 18.09 03:25, о которой шла речь в находке (секунды и доли не сверяем:
    # они получаются из дробной части суток и к сути не относятся)
    assert f.parsed['data_utc'].replace(second=0, microsecond=0) == datetime(2026, 9, 18, 3, 25, tzinfo=timezone.utc)
    # выигрыш правки числом: элементы на шесть с лишним часов свежее тех, что брались раньше
    assert f.parsed['data_utc'] - datetime(2026, 9, 17, 21, 14, tzinfo=timezone.utc) > timedelta(hours=6)


def test_kesh_svezhee_snimka_beryotsya_kesh(tmp_path):
    """Обратный случай: кеш свежее — снимок репозитория его не вытесняет."""
    _seed_cache(tmp_path, NEWER)
    text, f = tle_latest(disabled='cache', cache_dir=tmp_path, now=NOW, use_bundled=True)
    assert epoch_of(text) == '26261.50000000', (epoch_of(text), f.status_ru)


def test_kesha_net_povedenie_prezhnee(tmp_path):
    """Снимка не касается прежний случай: кеша нет — берётся снимок репозитория."""
    text, f = tle_latest(disabled='cache', cache_dir=tmp_path, now=NOW, use_bundled=True)
    assert epoch_of(text) == '26261.14280998'
    assert f.metadata['cache_origin'] == 'bundled_verified_A3_receipt'


def test_bez_snimka_beryotsya_tolko_kesh(tmp_path):
    """Флаг use_bundled продолжает значить то, что значил: без снимка остаётся только кеш."""
    _seed_cache(tmp_path, OLDER)
    text, _ = tle_latest(disabled='cache', cache_dir=tmp_path, now=NOW, use_bundled=False)
    assert epoch_of(text) == '26260.88472222'
