# -*- coding: utf-8 -*-
"""Живой опрос уведомлений NASA DONKI для ТЕКУЩЕГО режима. Область Б (CONTRACT v3.1, раздел 5).

ЗАЧЕМ. До двенадцатого круга источник уведомлений в текущем режиме не опрашивался вовсе:
ячейка «События на горизонте» показывала ноль, и это был ноль ОПРОШЕННЫХ источников, а не ноль
событий. Замер 19.09.2026 13:00 UTC: GET на службу уведомлений отвечает HTTP 200 за 0,68…0,84 с
и отдаёт записи с полями messageType, messageID, messageIssueTime, messageURL, messageBody —
то есть подключать было нечего ждать.

ЧЕГО ЗДЕСЬ НЕТ. Второго разборщика уведомлений. Тело сообщения разбирает единственная в проекте
функция `vkd.sources.donki.parse_notification` (область второго исполнителя), сюда она приходит
готовой. Мост делает ровно три вещи: получает ленту тем же слоем, что остальные живые источники
(`vkd.sources.live_cache.acquire` — кеш, квитанция, пауза запросов, предел размера ответа),
режет ответ на отдельные сообщения в ТОЙ ЖЕ канонической форме, что архив A1, и переводит
результат разбора в типы `vkd.types`.

КАНОНИЧЕСКАЯ ФОРМА СООБЩЕНИЯ. Архив A1 хранит каждое сообщение как
`json.dumps(объект, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\\n'`.
Проверено побайтно на записи 20240430-AL-001: 1975 байт, sha256 02e36900035e…, совпадает с
файлом архива. Поэтому идентификатор записи живого опроса устроен так же, как архивный
(`nasa_donki_notification:<номер выпуска>:<первые 12 знаков sha256>`), и одно и то же сообщение,
полученное живьём и лежащее в архиве, имеет ОДИН идентификатор — иначе выгрузка называла бы
одну запись двумя именами.

ОТКАЗ ИСТОЧНИКА. Пустого списка при отказе не бывает: когда живого ответа нет и кеша нет,
возвращается `Notifications` с причиной словами и `connected=False`. «Событий нет» и «мы не
спросили» — разные вещи, и на экране они не должны выглядеть одинаково.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from vkd.sources.donki import NotificationParseError, PARSER_VERSION, SOURCE_ID, STAMP, parse_notification
from vkd.sources.live_cache import Fetch, Product, acquire, raw_record
from vkd.sources.live_parsers import LiveDataError
from vkd.sources.registry import iso_utc, utc

# Адрес службы уведомлений. Параметров даты в нём НЕТ намеренно: служба по умолчанию отдаёт
# уведомления за последние 7 суток, и проверено 19.09.2026 13:05 UTC сравнением трёх ответов —
# без параметров, с `type=all` и с явными `startDate=2026-09-12&endDate=2026-09-19&type=all`
# служба вернула один и тот же ответ (23 718 байт, 8 записей). Явные даты меняли бы адрес
# каждые сутки, а слой источников считает отпечаток кеша ПО АДРЕСУ: каждый новый день начинался
# бы с пустого кеша. Оставлен один устойчивый адрес с явным `type=all` — так в реестре A1
# записаны и архивные запросы 2024 года.
DONKI_URL = 'https://kauai.ccmc.gsfc.nasa.gov/DONKI/WS/get/notifications?type=all'

# Ширина окна, которое отдаёт служба по умолчанию, сут (проверено замером выше). Число нужно,
# чтобы объявить охват ленты словами: «уведомления за последние 7 суток», а не гадать по ответу.
FEED_WINDOW_DAYS = 7

# Предел давности САМОГО ПОЗДНЕГО уведомления в ленте, мин. Взято 2 × FEED_WINDOW_DAYS = 14 сут.
# Обоснование: время данных этой ленты — момент выпуска самого позднего уведомления, а в спокойную
# неделю самым поздним законно оказывается еженедельная сводка недельной давности (в архиве A1 —
# 10 сводок за 62 суток, то есть примерно одна в неделю). Предел в одно окно ленты отбраковывал бы
# такую спокойную неделю как «устаревшие данные». Двойное окно оставляет пригодной и сохранённую
# копию возрастом ещё до недели, после чего перечень перестаёт быть текущей картиной и объявляется
# устаревшим. Собственная давность ОПРОСА к этому пределу отношения не имеет: она печатается
# отдельно по времени получения.
MAX_AGE_MIN = 2 * FEED_WINDOW_DAYS * 24 * 60

# Пауза между обращениями к службе, с. Это НЕ частота показа, а отступление после отказа:
# при исправной службе слой получения обходит паузу (см. app/fetch_guard.py, политика обновления).
# 300 с выбраны равными сроку кеша экрана (`[sources].cache_ttl_s`): чаще, чем раз в пять минут,
# страница всё равно не перерисовывается, а уведомления выходят в среднем несколько раз в сутки
# (замер по архиву A1: 247 сообщений за 62 суток, то есть примерно 4 в сутки), так что пятиминутное
# отступление не пропускает событий.
POLL_SECONDS = 300

MAX_MESSAGES = 2000        # предохранитель разбора: за 7 суток служба отдаёт единицы—десятки записей
                           # (замер: 8 за неделю 12–19.09.2026, 80 за 30 суток). Тысячи записей
                           # означают, что пришёл не тот ответ, и молча разбирать их нельзя.

STATUS_RU = {
    'parsed': 'разобрано, факты извлечены',
    'out_of_scope': 'не относится к обстановке у станции',
    'context_only': 'справка без текущего события',
    'publication_conflict': 'времена выпуска в теле и в ленте расходятся',
    'unsupported': 'формат сводки не распознан',
}
TYPE_RU = {
    'CME': 'выброс коронального вещества', 'FLR': 'вспышка', 'SEP': 'протонное событие',
    'GST': 'геомагнитная буря', 'IPS': 'межпланетная ударная волна', 'RBE': 'электроны внешнего пояса',
    'MPC': 'смещение магнитопаузы', 'Report': 'еженедельная сводка',
}

_BODY_ISSUE_RE = re.compile(r'^## Message Issue Date:\s*(' + STAMP + r')\s*$', re.M)


@dataclass(frozen=True)
class Notifications:
    """Разобранная лента уведомлений: то, что мост отдаёт конвейеру Б."""
    connected: bool                 # опрос состоялся (живьём или из проверенного кеша)
    events: tuple = ()              # EventInterval для правила условий
    samples: tuple = ()             # EnvironmentSample уведомлений (Kp бури); в канал Kp не подставляются
    facts: dict = field(default_factory=dict)      # идентификатор записи -> структурированные факты
    records: dict = field(default_factory=dict)    # идентификатор записи -> метаданные для выгрузки
    bodies: dict = field(default_factory=dict)     # идентификатор записи -> канонические байты сообщения
    message_count: int = 0          # сколько сообщений отдала служба
    by_status: dict = field(default_factory=dict)  # состояние разбора -> сколько сообщений
    by_type: dict = field(default_factory=dict)    # тип сообщения -> сколько сообщений
    not_parsed: tuple = ()          # (номер выпуска, причина) — ни одно не пропадает молча
    newest_utc: datetime | None = None
    oldest_utc: datetime | None = None
    window_from_utc: datetime | None = None
    window_to_utc: datetime | None = None
    reason_ru: str | None = None    # почему данных нет; None — данные есть

    @property
    def event_records(self) -> int:
        """Сколько сообщений дали событие с интервалом (остальные — справка или не наш случай)."""
        return len({e.raw_record_id for e in self.events})


def _positive(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError('%s должен быть конечным положительным числом, получено %r' % (name, value))
    return float(value)


def _settings_product(max_age_min: float) -> Product:
    """Изделие слоя источников с адресом и сроками из `config/settings.toml` (Т7).

    Настройки читаются здесь, а не берутся из `vkd.sources.live`: там эта проверка лежит в
    непубличной функции чужой области, и опираться на её имя из своего моста нельзя. Правила
    те же, что применяет слой источников к остальным адресам, и они названы числами.
    """
    from vkd.config import section
    cfg = section('sources')
    urls = cfg.get('urls', {})
    if not isinstance(urls, dict):
        raise ValueError('sources.urls должен быть таблицей')
    url = urls.get('donki', DONKI_URL)
    parts = urlsplit(url) if isinstance(url, str) else None
    if parts is None or parts.scheme != 'https' or not parts.hostname:
        raise ValueError('sources.urls.donki должен быть полным адресом https, получено %r' % (url,))
    timeout = _positive(cfg.get('timeout_s', 6), 'sources.timeout_s')
    if timeout > 30:
        raise ValueError('sources.timeout_s не должен превышать 30 с')
    ttl = _positive(cfg.get('cache_ttl_s', POLL_SECONDS), 'sources.cache_ttl_s')
    return Product(SOURCE_ID, url, parse_notifications, _positive(max_age_min, 'max_age_min'),
                   max(POLL_SECONDS, math.ceil(ttl)), read_timeout_s=timeout)


def canonical_message(message: dict) -> bytes:
    """Одно сообщение в той же байтовой форме, в какой его хранит архив A1 (см. заголовок модуля)."""
    return json.dumps(message, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8') + b'\n'


def _issue_times(message: dict) -> tuple[datetime, datetime | None]:
    """Время выпуска из ленты и из тела сообщения.

    Разборщик уведомлений принимает временем публикации ПОЗДНЕЕ из двух и сверяет его с
    метаданными записи. Здесь тело читается ровно одним выражением — тем же образцом времени
    `vkd.sources.donki.STAMP`, — чтобы не заводить второго толкования заголовков: если заголовок
    отсутствует, повторяется или расходится с лентой, об этом скажет сам разборщик.
    """
    api = utc(message['messageIssueTime'])
    found = _BODY_ISSUE_RE.findall(message.get('messageBody') or '')
    body = utc(found[0]) if len(found) == 1 else None
    return api, body


def parse_notifications(raw: bytes, now: datetime) -> dict:
    """Проверка ответа службы ДО записи в кеш (тот же договор, что у остальных живых разборщиков).

    Время данных этой ленты — момент выпуска самого позднего уведомления в ней. Это единственная
    величина времени, которая есть в самих байтах: у перечня сообщений нет собственного момента
    измерения. Она же попадает в квитанцию, поэтому вычисляется только из ответа и не зависит от
    момента запроса — иначе перечитанная квитанция каждый раз расходилась бы сама с собой.
    """
    data = json.loads(raw)
    if not isinstance(data, list):
        raise LiveDataError('Ответ службы уведомлений должен быть перечнем сообщений')
    if len(data) > MAX_MESSAGES:
        raise LiveDataError('Перечень уведомлений неправдоподобно велик: %d записей' % len(data))
    times, ids, types, rejected = [], [], {}, 0
    for message in data:
        try:
            if not isinstance(message, dict):
                raise LiveDataError('Сообщение должно быть объектом')
            mid, kind = message['messageID'], message['messageType']
            if not isinstance(mid, str) or not mid.strip() or not isinstance(kind, str) or not kind.strip():
                raise LiveDataError('Сообщение без номера выпуска или типа')
            if not isinstance(message.get('messageBody'), str) or not message['messageBody'].strip():
                raise LiveDataError('Сообщение без тела')
            api, body = _issue_times(message)
            issue = max(api, body) if body else api
            if issue > now:
                raise LiveDataError('Уведомление выпущено позже момента запроса')
            times.append(issue)
            ids.append(mid)
            types[kind] = types.get(kind, 0) + 1
        except (KeyError, TypeError, ValueError, OverflowError):
            rejected += 1
    if not times:
        # Пустой перечень — это не «спокойная неделя»: служба выпускает еженедельную сводку
        # (в архиве A1 — примерно одна в неделю), поэтому неделя вовсе без сообщений означает,
        # что ответ не тот. Пустой список не выдаётся за успешный опрос.
        raise LiveDataError('Служба вернула перечень без пригодных уведомлений')
    newest, oldest = max(times), min(times)
    return dict(data_utc=newest, published_utc=newest, quality='preliminary', rejected_rows=rejected,
                sampling_note='перечень уведомлений за окно службы; время данных — выпуск самого позднего сообщения',
                selected={'message_count': len(times), 'rejected_messages': rejected,
                          'newest_issue_utc': iso_utc(newest), 'oldest_issue_utc': iso_utc(oldest),
                          'message_ids': ids, 'message_types': types,
                          'feed_window_days': FEED_WINDOW_DAYS})


def _record_of(message: dict, canonical: bytes, fetched_utc: datetime) -> dict:
    """Метаданные записи в том же виде, в каком их даёт реестр A1 архивным сообщениям."""
    sha = hashlib.sha256(canonical).hexdigest()
    api, body = _issue_times(message)
    published = max(api, body) if body else api
    return {
        'source_id': SOURCE_ID,
        'release_id': message['messageID'],
        'raw_record_id': '%s:%s:%s' % (SOURCE_ID, message['messageID'], sha[:12]),
        'message_type': message['messageType'],
        'published_utc': iso_utc(published),
        'api_issue_utc': iso_utc(api),
        'body_issue_utc': iso_utc(body) if body else None,
        'url': message.get('messageURL'),
        'sha256': sha,
        'bytes': len(canonical),
        'version': 'sha256:' + sha,
        'fetched_utc': iso_utc(fetched_utc),
        'available_utc': iso_utc(fetched_utc),
        'raw_representation': 'каноническая выборка одного объекта службы; исходные байты ответа — в квитанции слоя источников',
        'availability_evidence': 'время выпуска, объявленное самим сообщением; не доказательство неизменности прежних версий',
        'limitations': ['Исследовательское уведомление NASA, не оперативное предупреждение NOAA.',
                        'Тип сообщения сам по себе не означает воздействия на станцию.',
                        'Время выпуска — не время события и не срок действия предупреждения.'],
    }


def decode(fetch: Fetch) -> Notifications:
    """Разбор полученной ленты в события, наблюдения и факты. Байты берутся из квитанции."""
    if fetch.payload is None or fetch.raw is None:
        return Notifications(connected=False, reason_ru=fetch.status_ru or 'уведомления не получены')
    fetched_utc = fetch.fetched_utc or datetime.now(timezone.utc)
    messages = json.loads(fetch.raw)
    events, samples, facts, records, bodies = [], [], {}, {}, {}
    by_status, by_type, not_parsed = {}, {}, []
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get('messageID'), str):
            not_parsed.append(('без номера выпуска', 'сообщение не разобрано: нет обязательных полей'))
            continue
        kind = message.get('messageType') if isinstance(message.get('messageType'), str) else '—'
        by_type[kind] = by_type.get(kind, 0) + 1
        canonical = canonical_message(message)
        record = _record_of(message, canonical, fetched_utc)
        try:
            parsed = parse_notification(canonical, record)
        except (NotificationParseError, KeyError, TypeError, ValueError) as exc:
            # Неразобранное сообщение не исчезает и не превращается в «событий нет»:
            # оно называется поимённо и уходит в снимок расчёта.
            not_parsed.append((message['messageID'], str(exc)))
            by_status['not_parsed'] = by_status.get('not_parsed', 0) + 1
            continue
        by_status[parsed['status']] = by_status.get(parsed['status'], 0) + 1
        events.extend(parsed['events'])
        samples.extend(parsed['samples'])
        if parsed.get('facts'):
            facts[record['raw_record_id']] = dict(parsed['facts'])
        bodies[record['raw_record_id']] = canonical
        records[record['raw_record_id']] = {**record, 'content_audit': {
            'parser_version': parsed['parser_version'], 'status': parsed['status'],
            'status_ru': STATUS_RU.get(parsed['status'], parsed['status']),
            'reason': parsed.get('reason'), 'target_scope': parsed.get('target_scope'),
            'facts': parsed.get('facts') or {}}}
    selected = fetch.parsed.get('selected') or {}
    newest = utc(selected['newest_issue_utc']) if selected.get('newest_issue_utc') else None
    oldest = utc(selected['oldest_issue_utc']) if selected.get('oldest_issue_utc') else None
    # Охват объявляется по окну службы, отсчитанному от момента ПОЛУЧЕНИЯ: именно за этот
    # промежуток служба обещает отдать уведомления. Границы самих сообщений (oldest/newest)
    # лежат внутри него и печатаются отдельно — подменять ими охват нельзя, иначе спокойная
    # неделя выглядела бы как узкое окно опроса.
    return Notifications(
        connected=True, events=tuple(events), samples=tuple(samples), facts=facts, records=records,
        bodies=bodies, message_count=len(messages), by_status=by_status, by_type=by_type,
        not_parsed=tuple(not_parsed),
        newest_utc=newest, oldest_utc=oldest,
        window_from_utc=fetched_utc - timedelta(days=FEED_WINDOW_DAYS), window_to_utc=fetched_utc,
        reason_ru=None)


def donki_latest(disabled: bool | str = False, *, max_age_min: float = MAX_AGE_MIN, **kwargs):
    """Лента уведомлений текущего режима: (`Notifications`, сырые записи, квитанция слоя).

    Порядок кортежа тот же, что у остальных живых источников (`vkd.sources.noaa_latest`):
    последним элементом стоит квитанция `Fetch` — по ней общий предел получения
    (`app/fetch_guard.py`) проставляет причину отказа, не разбирая содержимого.
    """
    fetch = acquire(_settings_product(max_age_min), disabled=disabled, **kwargs)
    return decode(fetch), raw_record(fetch), fetch


def coverage_ru(notes: Notifications) -> str:
    """Охват ленты одной строкой обычными словами — для экрана, снимка и отчёта."""
    if not notes.connected:
        return 'уведомления не получены: %s' % (notes.reason_ru or 'причина не названа')
    return ('уведомления за последние %d суток: %s — %s' % (
        FEED_WINDOW_DAYS,
        notes.window_from_utc.strftime('%d.%m.%Y %H:%MZ') if notes.window_from_utc else '—',
        notes.window_to_utc.strftime('%d.%m.%Y %H:%MZ') if notes.window_to_utc else '—'))


def summary_ru(notes: Notifications) -> str:
    """Что пришло: сколько сообщений, каких типов, сколько дали событие с интервалом."""
    if not notes.connected:
        return coverage_ru(notes)
    kinds = ', '.join('%s — %d' % (TYPE_RU.get(k, k), n) for k, n in sorted(notes.by_type.items(), key=lambda kv: -kv[1]))
    tail = '; не разобрано: %d' % len(notes.not_parsed) if notes.not_parsed else ''
    return 'получено сообщений: %d (%s); с событием на шкале времени: %d%s' % (
        notes.message_count, kinds or '—', notes.event_records, tail)


__all__ = ['DONKI_URL', 'FEED_WINDOW_DAYS', 'MAX_AGE_MIN', 'Notifications', 'PARSER_VERSION',
           'canonical_message', 'coverage_ru', 'decode', 'donki_latest', 'parse_notifications',
           'summary_ru', 'STATUS_RU', 'TYPE_RU']
