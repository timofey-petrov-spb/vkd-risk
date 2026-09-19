# -*- coding: utf-8 -*-
"""27-суточный обзор NOAA SWPC (27DO.txt) — ДАЛЬНИЙ источник космопогоды для перспективы.

Продукт: https://services.swpc.noaa.gov/text/27-day-outlook.txt, около 1,6 КБ,
шапка с `:Issued:` и 27 строк по одной на сутки: дата UTC, поток радиоизлучения
10,7 см (sfu), планетарный индекс A, НАИБОЛЬШИЙ Kp за сутки.

ЧТО ЗДЕСЬ ПОВТОРЕНО ИЗ УЖЕ НАПИСАННЫХ МОСТОВ (vkd/integration/noaa_forecast.py,
vkd/integration/orbit_bridge.py) и почему именно так:

  * получение идёт ТЕМ ЖЕ слоем A4 (`vkd.sources.live_cache.acquire`) с тем же
    ограничением размера, тем же замком на каталог, той же паузой между запросами
    и той же квитанцией на точные байты. Второго способа ходить в сеть в проекте нет;
  * ВРЕМЯ ПУБЛИКАЦИИ берётся из строки `:Issued:`, а не из времени скачивания.
    Это не мелочь: обзор выпускается раз в неделю, и 19.09.2026 живой ответ нёс
    выпуск от 14.09 01:17 UTC. Если бы публикацией считалось время запроса, сервис
    объявил бы горизонт «27 суток от сегодня», хотя обзор кончается 10.10 —
    то есть пообещал бы космопогоду на шесть суток, которых в бюллетене нет;
  * горизонт продукта считается от ПЕРВОЙ СТРОКИ выпуска, а не от «сегодня»:
    `coverage_to_utc` — конец последних суток таблицы. Сутки за этой границей
    возвращаются как неизвестные, а не достраиваются последним значением;
  * ошибка получения никогда не превращается в нули: при недоступности возвращается
    статус `unavailable`/`stale` с названной причиной и ПУСТЫМ рядом суток.

ЧЕГО В ЭТОМ ПРОДУКТЕ НЕТ И ЧЕМ ЕГО НЕЛЬЗЯ ПОДМЕНИТЬ:

  * ПРОГНОЗА ПОТОКА ПРОТОНОВ И ВЕРОЯТНОСТИ ПРОТОННОГО СОБЫТИЯ ЗДЕСЬ НЕТ ВОВСЕ.
    Ни одной колонки. За пределами трёхсуточного прогноза NOAA сервису про
    протонные события сказать нечего, и это объявляется словами
    (`PROTON_UNKNOWN_RU`), а не обходится молчанием;
  * разрешение СУТОЧНОЕ. Наибольший Kp за сутки — не то же самое, что 3-часовой
    ряд Kp трёхсуточного прогноза: по нему нельзя сказать, в какие часы буря,
    и подставлять его в почасовую линию запрещено;
  * индекс A и поток 10,7 см — не пороговые величины сервиса. Они отдаются как
    справка о фоне (уровень активности, плотность атмосферы), в вердикт не входят
    и ни с каким порогом не сравниваются;
  * в архиве A1 (`data/source_registry_2024`) этого продукта нет: исторический
    режим его не получает, и подмены живым выпуском не делается.

Пересчёта суточных величин в оконные здесь нет и быть не может: ячейка сохраняет
исходные границы суток, как и в остальных мостах (см. vkd/history/README.md).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Optional
from urllib.parse import urlsplit

from vkd.sources.live_cache import Fetch, Product, acquire, raw_record
from vkd.sources.registry import iso_utc, utc
from vkd.types import EnvironmentSample, Kind

SOURCE_ID = 'noaa_swpc_27day_outlook'
URL = 'https://services.swpc.noaa.gov/text/27-day-outlook.txt'
HORIZON_DAYS = 27                    # столько строк в продукте; иначе это уже другой продукт
POLL_SECONDS = 3600                  # выпуск недельный: чаще раза в час спрашивать незачем
# Обзор выпускается раз в неделю. Предел давности 10 суток — это «выпуск ещё тот самый,
# что действует сейчас», с запасом на пропущенный выпуск; старше — данные не применяются.
MAX_AGE_MIN_DEFAULT = 10 * 24 * 60

MONTHS = {name: i for i, name in enumerate('Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split(), 1)}
KP_UNIT = ''                         # Kp безразмерен — как в vkd/integration/noaa_forecast.py
A_UNIT = ''                          # планетарный индекс A безразмерен

# (наш channel_id, единица, подпись для экрана и выгрузки)
CHANNELS = (
    ('kp_max_daily', KP_UNIT, 'наибольший Kp за сутки, 27-суточный обзор NOAA'),
    ('a_index_daily', A_UNIT, 'планетарный индекс A за сутки, 27-суточный обзор NOAA'),
    ('f107_daily', 'sfu', 'поток радиоизлучения 10,7 см за сутки, 27-суточный обзор NOAA'),
)

PROTON_UNKNOWN_RU = (
    'Прогноза протонных событий за пределами трёх суток у NOAA нет: 27-суточный обзор содержит '
    'только поток 10,7 см, индекс A и наибольший Kp за сутки. Протонная линия на этом горизонте '
    'НЕИЗВЕСТНА — это не «событий не ожидается» и не ноль.')

RESOLUTION_RU = (
    'Разрешение обзора — сутки. Наибольший Kp за сутки не говорит, в какие часы буря; '
    'в 3-часовую линию Kp он не подставляется и в вероятность за окно не пересчитывается.')

LIMITS_RU = (
    PROTON_UNKNOWN_RU,
    RESOLUTION_RU,
    'Обзор — климатологический прогноз по вращению Солнца (27 суток): он переносит вперёд '
    'активные области прошлого оборота и не предсказывает вспышку конкретного дня.',
    'Индекс A и поток 10,7 см ни с каким порогом сервиса не сравниваются: это справка о фоне.',
    'Живой выпуск: доступность подтверждена самим запросом, историческая неизменность байтов '
    'не доказывается.',
)

STATUS_RU = {
    'full': 'обзор получен, горизонт продукта покрыт',
    'stale': 'выпуск старше допустимой давности — значения не применяются',
    'missing': 'пригодного выпуска нет',
    'unavailable': 'источник недоступен',
}


class Outlook27ParseError(ValueError):
    """Ответ получен, но это не 27-суточный обзор NOAA или он неполон."""


@dataclass(frozen=True)
class OutlookDay27:
    """Одни сутки обзора. Значение None означает «не сказано», а не ноль."""
    date_utc: datetime               # 00:00 UTC этих суток
    valid_from_utc: datetime
    valid_to_utc: datetime
    kp_max: float                    # наибольший Kp за сутки
    a_index: float
    f107_sfu: float


@dataclass(frozen=True)
class Outlook27:
    """Один выпуск обзора, приведённый к типам проекта.

    `days` пуст при любом статусе, кроме 'full': отсутствие данных не заполняется.
    """
    status: str                      # 'full' | 'stale' | 'missing' | 'unavailable'
    status_ru: str
    published_utc: Optional[datetime]        # из строки :Issued:, НЕ время скачивания
    fetched_utc: Optional[datetime]
    release_id: Optional[str]
    raw_record_id: Optional[str]
    sha256: Optional[str]
    url: Optional[str]
    coverage_from_utc: Optional[datetime]
    coverage_to_utc: Optional[datetime]
    days: tuple                      # OutlookDay27, по одному на сутки
    samples: tuple                   # EnvironmentSample, исходное суточное разрешение
    proton_forecast_available: bool  # всегда False: колонки протонов в продукте нет
    limitations: tuple
    reason: Optional[str]
    fetch: Optional[Fetch] = None    # исходная квитанция слоя A4 (для состояния источников)

    def day(self, date_utc: datetime) -> Optional[OutlookDay27]:
        """Сутки обзора, покрывающие эту дату; None — за горизонтом выпуска."""
        d = utc(date_utc)
        for row in self.days:
            if row.valid_from_utc <= d < row.valid_to_utc:
                return row
        return None

    @property
    def horizon_days(self) -> Optional[int]:
        if not self.coverage_from_utc or not self.coverage_to_utc:
            return None
        return round((self.coverage_to_utc - self.coverage_from_utc).total_seconds() / 86400)


def parse_27day(raw: bytes, now: datetime) -> dict:
    """Проверить байты продукта до кеширования. Публикация — из `:Issued:`.

    Возвращает запись в том же виде, какой ждёт слой A4: data_utc / published_utc /
    quality / cells с исходными границами суток.
    """
    text = raw.decode('utf-8')
    issued = re.findall(r'^:Issued:\s+(\d{4}) (\w{3}) (\d{2}) (\d{2})(\d{2}) UTC\s*$', text, re.M)
    if len(issued) != 1:
        raise Outlook27ParseError('Ожидалась ровно одна строка :Issued:')
    year, month, day, hour, minute = issued[0]
    if month not in MONTHS or int(hour) >= 24 or int(minute) >= 60:
        raise Outlook27ParseError('Испорченная отметка выпуска')
    published = datetime(int(year), MONTHS[month], int(day), int(hour), int(minute), tzinfo=timezone.utc)
    if published > utc(now):
        raise Outlook27ParseError('Публикация в будущем')
    rows = re.findall(r'^(\d{4}) (\w{3}) (\d{2})\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*$', text, re.M)
    if len(rows) != HORIZON_DAYS:
        raise Outlook27ParseError('Ожидалось %d строк суток, найдено %d' % (HORIZON_DAYS, len(rows)))
    cells, dates = [], []
    for y, mon, d, f107, a_index, kp_max in rows:
        if mon not in MONTHS:
            raise Outlook27ParseError('Неизвестный месяц: %s' % mon)
        date = datetime(int(y), MONTHS[mon], int(d), tzinfo=timezone.utc)
        values = {'f107_daily': float(f107), 'a_index_daily': float(a_index), 'kp_max_daily': float(kp_max)}
        # Диапазоны — физические границы самих индексов, а не «разумные» отсечки:
        # Kp определён на 0…9, планетарный A — на 0…400, поток 10,7 см ниже 50 sfu
        # не опускался ни в один минимум и выше 500 sfu держится только во вспышку.
        if not 0 <= values['kp_max_daily'] <= 9 or abs(values['kp_max_daily'] * 3 - round(values['kp_max_daily'] * 3)) > 2e-3:
            raise Outlook27ParseError('Kp вне шкалы 0…9 или не кратен трети: %s' % kp_max)
        if not 0 <= values['a_index_daily'] <= 400:
            raise Outlook27ParseError('Индекс A вне шкалы 0…400: %s' % a_index)
        if not 50 <= values['f107_daily'] <= 500:
            raise Outlook27ParseError('Поток 10,7 см вне 50…500 sfu: %s' % f107)
        dates.append(date)
        for channel_id, unit, _label in CHANNELS:
            cells.append({'source_id': SOURCE_ID, 'channel_id': channel_id, 'value': values[channel_id],
                          'unit': unit, 'temporal_resolution': '24h', 'kind': 'external_forecast',
                          'valid_from_utc': iso_utc(date), 'valid_to_utc': iso_utc(date + timedelta(days=1)),
                          'published_utc': iso_utc(published)})
    if any(b - a != timedelta(days=1) for a, b in zip(dates, dates[1:])):
        raise Outlook27ParseError('Сутки обзора не идут подряд')
    # Таблица обязана начинаться рядом с выпуском: иначе это чужой или подменённый файл,
    # а сервис по нему объявил бы горизонт не там, где он есть.
    if abs((dates[0] - published).total_seconds()) > 2 * 86400:
        raise Outlook27ParseError('Первые сутки таблицы далеко от даты выпуска')
    return {'data_utc': published, 'published_utc': published, 'quality': 'model', 'cells': cells,
            'valid_from_utc': dates[0], 'valid_to_utc': dates[-1] + timedelta(days=1),
            'selected': {'issued_utc': iso_utc(published), 'days': len(dates),
                         'coverage_from_utc': iso_utc(dates[0]),
                         'coverage_to_utc': iso_utc(dates[-1] + timedelta(days=1))}}


def _configured(product: Product) -> Product:
    """Адрес, тайм-аут и пауза — из config/settings.toml, как у остальных продуктов A4.

    Настройки читаются в момент вызова: импорт модуля не имеет права заморозить
    развёрнутую конфигурацию.
    """
    from dataclasses import replace
    from vkd.config import section
    cfg = section('sources')
    urls = cfg.get('urls', {})
    if not isinstance(urls, dict):
        raise ValueError('sources.urls must be a table')
    url = urls.get('noaa_27day', product.url)
    if not isinstance(url, str) or urlsplit(url).scheme != 'https' or not urlsplit(url).hostname:
        raise ValueError('sources.urls.noaa_27day must be an absolute HTTPS URL')
    timeout = float(cfg.get('timeout_s', product.read_timeout_s))
    if not timeout > 0 or timeout > 30:
        raise ValueError('sources.timeout_s must be positive and not exceed 30 seconds')
    return replace(product, url=url, read_timeout_s=timeout)


def _max_age_min() -> float:
    from vkd.config import section
    value = section('outlook').get('noaa_27day_max_age_min', MAX_AGE_MIN_DEFAULT)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
        raise ValueError('outlook.noaa_27day_max_age_min must be positive')
    return float(value)


def _empty(status: str, reason: Optional[str], fetch: Optional[Fetch] = None,
           published: Optional[datetime] = None) -> Outlook27:
    return Outlook27(status, STATUS_RU[status] + (': ' + reason if reason else ''), published,
                     getattr(fetch, 'fetched_utc', None), None, None, None,
                     getattr(fetch, 'url', None), None, None, (), (), False, LIMITS_RU, reason, fetch)


def fetch_27day(disabled: bool | str = False, *, max_age_min: Optional[float] = None, **kwargs):
    """27-суточный обзор NOAA: (Outlook27, сырые записи для выгрузки).

    `disabled`: False/'on' — можно в сеть, True/'cache' — только проверенный кеш,
    'off' — источник исключён пользователем. Прочие именованные доводы (cache_dir,
    now, transport, force_refresh) уходят в слой A4 без изменений — тем же приёмом,
    что у goes_latest / noaa_latest.
    """
    product = Product(SOURCE_ID, URL, parse_27day,
                      float(max_age_min) if max_age_min is not None else _max_age_min(), POLL_SECONDS)
    fetched = acquire(_configured(product), disabled=disabled, **kwargs)
    raw = raw_record(fetched)
    if fetched.payload is None:
        # 'stale' слой ставит сам, когда байты есть, но выпуск старше предела давности.
        status = {'stale': 'stale', 'off': 'unavailable', 'cache_unavailable': 'unavailable',
                  'invalid_cache': 'unavailable'}.get(fetched.status, 'missing' if fetched.raw is not None else 'unavailable')
        published = utc(fetched.parsed['published_utc']) if fetched.parsed.get('published_utc') else None
        return _empty(status, fetched.error or fetched.status_ru, fetched, published), raw
    parsed, meta = fetched.parsed, fetched.metadata
    published = utc(parsed['published_utc'])
    by_day: dict[datetime, dict] = {}
    samples = []
    for cell in parsed['cells']:
        start, end = utc(cell['valid_from_utc']), utc(cell['valid_to_utc'])
        by_day.setdefault(start, {'valid_to_utc': end})[cell['channel_id']] = cell['value']
        samples.append(EnvironmentSample(
            t_utc=start, channel_id=cell['channel_id'], value=float(cell['value']), unit=cell['unit'],
            source_id=SOURCE_ID, kind=Kind.EXTERNAL_FORECAST, published_utc=published,
            valid_from_utc=start, valid_to_utc=end, fetched_utc=fetched.fetched_utc, quality='model',
            raw_record_id=meta['raw_record_id'], version=meta['version']))
    days = tuple(OutlookDay27(start, start, value['valid_to_utc'], value['kp_max_daily'],
                              value['a_index_daily'], value['f107_daily'])
                 for start, value in sorted(by_day.items()))
    coverage_from, coverage_to = utc(parsed['valid_from_utc']), utc(parsed['valid_to_utc'])
    age_days = (utc(kwargs.get('now') or fetched.fetched_utc) - published).total_seconds() / 86400
    status_ru = ('%s: выпуск %s, сутки %s — %s (%d), возраст выпуска %.1f сут'
                 % (STATUS_RU['full'], published.strftime('%Y-%m-%d %H:%MZ'),
                    coverage_from.strftime('%Y-%m-%d'), (coverage_to - timedelta(days=1)).strftime('%Y-%m-%d'),
                    len(days), age_days)).replace('.', ',')
    return Outlook27('full', status_ru, published, fetched.fetched_utc, meta['version'], meta['raw_record_id'],
                     meta['sha256'], meta.get('url'), coverage_from, coverage_to, days, tuple(samples),
                     False, LIMITS_RU, None, fetched), raw


__all__ = ['CHANNELS', 'HORIZON_DAYS', 'LIMITS_RU', 'Outlook27', 'OutlookDay27', 'Outlook27ParseError',
           'PROTON_UNKNOWN_RU', 'RESOLUTION_RU', 'SOURCE_ID', 'URL', 'fetch_27day', 'parse_27day']
