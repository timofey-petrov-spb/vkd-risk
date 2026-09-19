# -*- coding: utf-8 -*-
"""Отчёт PDF о расчёте (ТЗ круга 12, пункт 8): шесть разделов в строгом порядке.

Порядок разделов задан владельцем и НЕ переставляется:

  1. титул — название сервиса, дата и время расчёта UTC, режим, версия алгоритма, коммит кода;
  2. входные данные — длительность выхода, срок поиска, режим, отсечка, источники с моментом
     получения и давностью;
  3. количественная оценка факторов — таблица величин рекомендованного окна: величина,
     значение, единица, происхождение, источник;
  4. рекомендация — когда выходить, два предложения обычными словами, условия если есть;
  5. границы применимости — то, что убрано с главного экрана в пункте 1 того же ТЗ;
  6. приложение — перебранные начала таблицей.

Три правила, которым подчинён весь файл.

ЧИСЛА НЕ СЧИТАЮТСЯ ЗДЕСЬ. Каждое значение берётся из снимка расчёта `S` — того же самого
словаря, по которому рисуется экран и собирается `report.md`. Печать идёт теми же функциями
(`app.export.fmt` → `vkd.explain.format.fmt_ru` + `app.ui.sup`), поэтому одна величина не может
получить в PDF иной вид, чем на экране. Отношения и разности, которых в снимке нет, считаются
ровно в одном месте — `plain_why_ru`, и там это объявлено.

НИКАКИХ МОЛЧАЛИВЫХ ЗАГЛУШЕК. Нет величины — печатается «не определено» с причиной, а не ноль
и не прочерк вместо числа. Нет перебора — раздел 6 говорит, что перебора не было, а не
показывает пустую таблицу.

ПРИЛОЖЕНИЕ НЕ ПАДАЕТ ИЗ-ЗА ОТЧЁТА. Сборка PDF может не удаться (нет библиотеки вёрстки, нет
шрифта с кириллицей), и тогда `build_pdf_or_reason` возвращает причину по-русски, а выгрузка
кладёт её в архив отдельным файлом и продолжает работать.

КИРИЛЛИЦА. Встроенные шрифты reportlab (Helvetica, Times) кириллицы не содержат — русский текст
встал бы квадратами или вовсе пропал. Поэтому шрифт ищется среди системных и пакетных, и берётся
не «похожий», а тот, который ДОКАЗАННО покрывает каждый знак этого документа: проверяется таблица
соответствия символов глифам самого файла шрифта (`charToGlyph`), до вёрстки.
"""
from __future__ import annotations

import io
import os
import re
import struct
from datetime import timedelta

from app.export import (COV_RU, DISABLED_RU, KIND_RU, MODE_ID_RU, SCAN_VERDICT_RU, VERDICT_TITLE,
                        _as_screen_factor, _dates_outside_urls, _dt, _git_sha, _mode_id,
                        _robustness_line, _scan_answer_md, _span_end, _SRC_KEY_RU, _t,
                        _tolerance_line, _traj_line, _win_title, fmt)
from app.ui import dates_ru, factor_value_ru, phrase_ru, screen_text, source_name_ru, status_ru

# Библиотека вёрстки. Импорт отложен в функции: сам по себе `import app.report_pdf` обязан
# работать и там, где reportlab не установлен, иначе выгрузка упала бы на импорте.
try:                                     # pragma: no cover — ветка «библиотеки нет» проверяется тестом с подменой
    import reportlab                     # noqa: F401
    _IMPORT_ERROR: str | None = None
except Exception as exc:                 # noqa: BLE001
    _IMPORT_ERROR = str(exc)


class PdfNotBuilt(Exception):
    """PDF собрать не удалось. Текст исключения — причина по-русски, пригодная для печати."""


# ---------------------------------------------------------------- размеры страницы и шрифта
# Поля — ГОСТ 7.32-2017, п. 6.1.1 (отчёт о научно-исследовательской работе): левое 30 мм,
# правое 15 мм, верхнее и нижнее по 20 мм. Полоса набора при этом 165 мм, и ширины всех
# таблиц ниже в сумме дают ровно её.
MARGIN_LEFT_MM, MARGIN_RIGHT_MM, MARGIN_TOP_MM, MARGIN_BOTTOM_MM = 30.0, 15.0, 20.0, 20.0
PAGE_WIDTH_MM = 210.0                                         # A4 по ГОСТ 9327: 210 x 297 мм
TEXT_WIDTH_MM = PAGE_WIDTH_MM - MARGIN_LEFT_MM - MARGIN_RIGHT_MM      # полоса набора 165 мм
# Колонтитул стоит в нижнем поле, на 12 мм от края листа: текст кончается на 20 мм, и 8 мм
# просвета отделяют служебную строку от текста, не пуская её к самому обрезу.
FOOTER_BASELINE_MM = 12.0

# Кегли. ГОСТ 7.32 предполагает 12–14 пт для сплошного текста; здесь 10 пт, потому что документ
# читают с экрана и он на треть состоит из таблиц, где 12 пт не помещается в колонку. Кегли
# таблиц выбраны по самой широкой ячейке: 8 пт держит строку источника в 45 мм, 7 пт — строку
# приложения в 43 мм. Больше трёх кеглей в документе нет.
SIZE_TITLE, SIZE_H, SIZE_BODY, SIZE_TABLE, SIZE_SMALL = 18.0, 12.0, 10.0, 8.0, 7.0
LEADING_RATIO = 1.25                     # межстрочный интервал: одинарный с обычным для набора запасом

# Заголовки разделов — в том порядке, в котором они обязаны идти в готовом документе.
# Тест сверяет порядок по этому же списку, а не по своей копии строк.
#
# О нумерации. В ТЗ разделы перечислены с первого по шестой, и титул там первый. В документе
# титульный лист номера не несёт — так принято в отчётах, — поэтому печатные номера идут с
# единицы у входных данных. Порядок и состав при этом ровно те, что названы в ТЗ, и в
# docstring каждого раздела ниже стоит его номер ПО ТЗ.
SECTION_TITLES = (
    'ВКД-Риск',
    '1. Входные данные',
    '2. Количественная оценка факторов',
    '3. Рекомендация',
    '4. Границы применимости',
    '5. Приложение. Перебранные начала выхода',
)

SERVICE_TITLE = 'ВКД-Риск'
SERVICE_SUBTITLE = ('Планирование выхода в открытый космос с МКС: внешняя обстановка на траектории '
                    'и выбор времени. Все времена — UTC.')

# Строк в приложении печатаются все перебранные начала: число перебранных начал — это и есть
# доказательство, что сервис искал, а не показал две точки (ТЗ круга 11, раздел 1). Предел
# поставлен только против явно испорченного снимка: 1000 строк — это больше, чем даёт срок
# поиска 7 суток при шаге 10 мин (1008 начал), то есть в норме он не срабатывает никогда.
APPENDIX_MAX_ROWS = 1000


# ---------------------------------------------------------------- поиск шрифта с кириллицей
# Порядок перебора: сначала то, что задал оператор, потом шрифт из пакета (он есть везде, где
# установлен matplotlib), потом обычные места системных шрифтов Linux, Windows и macOS, и в
# самом конце — обход каталогов шрифтов целиком. Первый шрифт, который покрывает ВЕСЬ текст
# документа, и берётся; «похожий» или «обычно подходящий» не берётся никогда.
FONT_ENV = 'VKD_PDF_FONT'                # путь к TTF, заданный оператором площадки

_SYSTEM_FONTS: tuple[tuple[str, str, str | None], ...] = (
    # (как называется семейство в отчёте, обычное начертание, полужирное или None)
    ('DejaVu Sans', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
     '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'),
    ('DejaVu Sans', '/usr/share/fonts/dejavu/DejaVuSans.ttf', '/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf'),
    ('Liberation Sans', '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
     '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf'),
    ('Noto Sans', '/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf',
     '/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf'),
    ('FreeSans', '/usr/share/fonts/truetype/freefont/FreeSans.ttf',
     '/usr/share/fonts/truetype/freefont/FreeSansBold.ttf'),
    ('Arial', 'C:/Windows/Fonts/arial.ttf', 'C:/Windows/Fonts/arialbd.ttf'),
    ('Tahoma', 'C:/Windows/Fonts/tahoma.ttf', 'C:/Windows/Fonts/tahomabd.ttf'),
    ('Times New Roman', 'C:/Windows/Fonts/times.ttf', 'C:/Windows/Fonts/timesbd.ttf'),
    ('Arial', '/Library/Fonts/Arial.ttf', '/Library/Fonts/Arial Bold.ttf'),
    ('Arial', '/System/Library/Fonts/Supplemental/Arial.ttf',
     '/System/Library/Fonts/Supplemental/Arial Bold.ttf'),
)

# Каталоги для последнего средства — сплошного обхода. Предел в 200 файлов поставлен, чтобы
# обход не превращался в чтение тысячи шрифтов на машине с большой коллекцией: на площадке
# развёртывания каталог шрифтов содержит единицы файлов, а разбор одного TTF стоит ~2 мс.
_FONT_DIRS = ('/usr/share/fonts', '/usr/local/share/fonts', os.path.expanduser('~/.fonts'),
              'C:/Windows/Fonts', '/Library/Fonts', '/System/Library/Fonts')
_FONT_SCAN_LIMIT = 200


def _pkg_fonts() -> list[tuple[str, str, str | None]]:
    """Шрифт из установленного пакета. matplotlib везёт с собой DejaVu Sans — полный набор
    кириллицы, надстрочных цифр и знаков «·», «≥», «Φ», которые печатает этот отчёт. Если
    пакета нет, список пуст и поиск идёт дальше по системным местам."""
    try:
        import matplotlib
    except Exception:                    # noqa: BLE001 — пакета нет, это не ошибка отчёта
        return []
    d = os.path.join(os.path.dirname(os.path.abspath(matplotlib.__file__)), 'mpl-data', 'fonts', 'ttf')
    reg, bold = os.path.join(d, 'DejaVuSans.ttf'), os.path.join(d, 'DejaVuSans-Bold.ttf')
    return [('DejaVu Sans', reg, bold if os.path.exists(bold) else None)] if os.path.exists(reg) else []


def _font_candidates() -> list[tuple[str, str, str | None]]:
    """Все кандидаты по порядку, без проверки покрытия: сначала заданный оператором, затем
    пакетный, затем системные места, затем сплошной обход каталогов шрифтов."""
    out: list[tuple[str, str, str | None]] = []
    env = os.environ.get(FONT_ENV)
    if env:
        out.append(('шрифт, заданный переменной %s' % FONT_ENV, env, None))
    out += _pkg_fonts()
    out += [(n, r, b) for n, r, b in _SYSTEM_FONTS if os.path.exists(r)]
    seen = {os.path.normcase(os.path.abspath(r)) for _, r, _ in out}
    found = 0
    for d in _FONT_DIRS:
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            for fn in sorted(files):
                if not fn.lower().endswith('.ttf') or found >= _FONT_SCAN_LIMIT:
                    continue
                p = os.path.join(root, fn)
                if os.path.normcase(os.path.abspath(p)) in seen:
                    continue
                seen.add(os.path.normcase(os.path.abspath(p)))
                out.append((os.path.splitext(fn)[0], p, None))
                found += 1
    return out


def _missing_chars(path: str, chars: set[str]) -> set[str]:
    """Каких знаков документа в шрифте НЕТ. Читается таблица соответствия символов глифам
    самого файла шрифта: это и есть ответ на вопрос «напечатается ли кириллица», а не догадка
    по имени файла. Нечитаемый файл считается непокрывающим — с тем же исходом, что и нехватка
    знаков, и без исключения наружу."""
    from reportlab.pdfbase.ttfonts import TTFError, TTFont
    try:
        face = TTFont('vkd-проба', path).face
    except (TTFError, OSError, ValueError, KeyError, IndexError, struct.error):
        return set(chars)
    table = getattr(face, 'charToGlyph', None)
    if not table:
        return set(chars)
    return {c for c in chars if ord(c) not in table}


class FontChoice:
    """Выбранный шрифт: пути, имена начертаний для вёрстки и то, как он был найден."""

    def __init__(self, family: str, regular_path: str, bold_path: str | None):
        self.family, self.regular_path, self.bold_path = family, regular_path, bold_path
        self.regular = 'vkd-body'
        # Полужирного начертания может не быть (одиночный файл при сплошном обходе). Подделывать
        # его нельзя — это будет не полужирный, а тот же текст; тогда заголовки различаются
        # кеглем, и это объявлено в `bold_is_real`.
        self.bold = 'vkd-bold' if bold_path else 'vkd-body'

    @property
    def bold_is_real(self) -> bool:
        return self.bold_path is not None

    def describe(self) -> str:
        return '%s (%s)%s' % (self.family, os.path.basename(self.regular_path),
                              '' if self.bold_is_real else ', полужирного начертания в системе нет')


def choose_font(chars: set[str]) -> FontChoice:
    """Первый кандидат, покрывающий КАЖДЫЙ знак документа. Не нашёлся ни один — исключение с
    перечнем непокрытых знаков и числом проверенных шрифтов: это честная причина, по которой
    PDF не собран, а не квадраты в готовом файле."""
    tried, best_missing, best_name = 0, None, None
    for family, reg, bold in _font_candidates():
        tried += 1
        miss = _missing_chars(reg, chars)
        if not miss:
            if bold and _missing_chars(bold, chars):
                bold = None              # полужирный есть, но знаки в нём не все — берём только обычный
            return FontChoice(family, reg, bold)
        if best_missing is None or len(miss) < len(best_missing):
            best_missing, best_name = miss, family
    shown = ', '.join('«%s» (U+%04X)' % (c, ord(c)) for c in sorted(best_missing or set(chars))[:10])
    raise PdfNotBuilt('в системе нет шрифта, покрывающего весь текст отчёта: проверено шрифтов %d, '
                      'ближе всех «%s», в нём нет знаков %s. Задайте путь к шрифту с кириллицей '
                      'переменной окружения %s.' % (tried, best_name or 'нет', shown or 'кириллицы', FONT_ENV))


# ---------------------------------------------------------------- подготовка текста
_CONTROL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]')
_SPACES_RE = re.compile(r'[ \t\r\n\u2028\u2029]+')


def _clean(text) -> str:
    """Строка, пригодная к печати: переносы и табуляции сводятся к пробелу (в абзаце они всё
    равно не значат ничего), знаки вне печатного диапазона удаляются. Удаление объявлено здесь
    и только здесь: ни один управляющий знак не должен доехать до вёрстки, где он превращается
    в пустой глиф или в разрыв строки посреди ячейки."""
    return _SPACES_RE.sub(' ', _CONTROL_RE.sub('', str(text if text is not None else ''))).strip()


def _txt(text) -> str:
    """Готовая строка снимка в виде экрана: обороты слоёв по-русски, дробь с запятой, степень
    надстрочными цифрами, даты «дд.мм чч:мм». Те же функции, что у отчёта разметкой, — поэтому
    одна и та же фраза не может выглядеть в PDF иначе, чем в `report.md` и на экране."""
    return _clean(_dates_outside_urls(phrase_ru(text)))


def _esc(text) -> str:
    """Экранирование для вёрстки: абзац reportlab разбирает разметку, и «&», «<», «>» из текста
    источника иначе оборвали бы абзац."""
    return _clean(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


# ---------------------------------------------------------------- блоки документа
# Документ сначала собирается списком простых блоков (заголовок, абзац, таблица) и только
# потом верстается. Так сделано ради шрифта: выбрать его можно, лишь зная ВЕСЬ текст документа,
# а текст известен лишь после сборки. Побочная польза — блоки читаются тестом напрямую.
class Block:
    def __init__(self, kind: str, text: str = '', rows=None, widths_mm=None, header=None, size=None):
        self.kind, self.text = kind, text
        self.rows, self.widths_mm, self.header, self.size = rows or [], widths_mm or [], header, size

    def texts(self) -> list[str]:
        out = [self.text] + [str(x) for x in (self.header or [])]
        for r in self.rows:
            out += [str(x) for x in r]
        return out


def _h(text):
    return Block('h', text)


def _gap(mm: float):
    """Отбивка заданной высоты в миллиметрах — для титула, где расстояния держат страницу."""
    return Block('spacer', size=mm)


def _answer(text):
    """Строка ответа «когда выходить»: единственная строка документа крупнее основного текста —
    за ней человек и открывает отчёт. Ни цвета, ни рамки, ни второго шрифта у неё нет."""
    return Block('answer', text)


def _p(text, size=None):
    return Block('p', text, size=size)


def _lead(text):
    """Строка-врезка: подпись к следующей таблице или к перечню."""
    return Block('lead', text)


def _table(header, rows, widths_mm, size=SIZE_TABLE):
    return Block('table', rows=rows, header=header, widths_mm=widths_mm, size=size)


# ---------------------------------------------------------------- вспомогательные величины
def _duration_min(S: dict) -> int:
    return int((S.get('request') or {}).get('duration_min') or 0)


def _search_span(S: dict) -> tuple[str | None, str | None]:
    """Срок поиска: границы берутся у перебора, потому что именно в них он и шёл. Перебора нет —
    границы строятся из запроса (начало периода и его длительность), и это тот же срок."""
    sc = S.get('scan') or {}
    if sc.get('search_from_utc') and sc.get('search_to_utc'):
        return sc['search_from_utc'], sc['search_to_utc']
    req = S.get('request') or {}
    t0 = _t(req.get('t0_utc'))
    if t0 is None or not req.get('search_min'):
        return None, None
    return req['t0_utc'], (t0 + timedelta(minutes=int(req['search_min']))).isoformat()


def _best_candidates(S: dict) -> list[dict]:
    """Кандидаты лучшей группы перебора — ровно те, что стоят за ответом «когда выходить»."""
    sc = S.get('scan') or {}
    cands = sc.get('candidates') or []
    return [cands[i] for i in (sc.get('best') or []) if 0 <= i < len(cands)]


def _recommended_candidate(S: dict) -> dict | None:
    """Начало, которое сервис назвал. Для промежутка — первое начало промежутка: у группы
    равнозначных различий сверх допуска нет, и правило ранжирования внутри группы упорядочивает
    её по времени (ТЗ круга 11, раздел 1)."""
    sc = S.get('scan') or {}
    if sc.get('answer_kind') not in ('point', 'interval'):
        return None
    best = _best_candidates(S)
    return best[0] if best else None


def _reference_window(S: dict) -> tuple[dict | None, str]:
    """Окно, для которого В СНИМКЕ есть полный набор факторов, и честная подпись, почему оно.

    Перебор ранжирует начала по двум величинам (минуты в аномалии и флюенс) — полный набор
    факторов с происхождением и источниками считается только для окон сравнения (ТЗ круга 11,
    раздел 1). Поэтому таблица факторов относится к окну сравнения, и подпись обязана назвать
    его время: иначе числа одного времени читались бы как числа рекомендованного начала."""
    wins = S.get('windows') or []
    if not wins:
        return None, 'окон с полным расчётом в снимке нет'
    rec = _recommended_candidate(S)
    if rec:
        same = next((w for w in wins if w.get('start_utc') == rec.get('start_utc')), None)
        if same:
            return same, 'это и есть рекомендованное начало: полный набор факторов рассчитан для него'
    pref = (S.get('recommendation') or {}).get('preferred')
    if pref:
        same = next((w for w in wins if w.get('start_utc') == pref), None)
        if same:
            return same, ('окно ручного сравнения, выбранное правилом сравнения окон; полный набор '
                          'факторов считается для окон сравнения, а начала перебора ранжируются по двум '
                          'величинам таблицы выше')
    return wins[0], ('первое окно ручного сравнения; правило не выбрало предпочтительного окна, '
                     'а полный набор факторов считается только для окон сравнения')


def _factor_value(f: dict) -> str:
    """Значение фактора БЕЗ единицы (единица стоит своей колонкой). Правило «значения нет» —
    общее с экраном (`app.ui.factor_value_ru`): наблюдение, горизонт которого не покрывает окно,
    характеристикой окна не является, и число вместо него не печатается."""
    if f.get('value') is None:
        return 'не определено'
    if factor_value_ru(_as_screen_factor(f), f.get('unit') or '') == '—':
        return 'окно не покрыто наблюдением'
    return fmt(f['value'])


# Имена источников по началу идентификатора записи — для тех записей, которых нет ни в общем
# перечне ядра (`SOURCE_ID_RU`), ни среди источников снимка. Записи таблиц ОСТ называются по
# номеру таблицы («ost1044_A_А_2_1»), и таких имён в перечне нет ни одного.
_RECORD_PREFIX_RU = (('ost1044', 'таблицы ОСТ 134-1044-2007'),
                     ('ecss_grun', 'модель метеороидов ECSS/Grün'),
                     ('ecss_streams', 'каталог метеорных потоков ECSS C-2'),
                     ('igrf', 'коэффициенты IGRF'))


def _record_source_ru(rid: str, mode_id: str | None) -> str:
    """Имя источника записи без хеша и номера выпуска: в колонке «источник» нужен источник,
    а не ключ записи. Номера выпусков и хеши остаются в `report.md`, `sources.json` и
    `manifest.json` — прослеживаемость не теряется, а таблица остаётся читаемой.

    Порядок: перечень имён ядра, затем имена источников экрана, затем начало идентификатора.
    Не опознано — печатается имя записи, как его печатает отчёт разметкой: неизвестное имя
    лучше пропажи источника."""
    from vkd.explain.format import SOURCE_ID_RU, record_ru
    head = str(rid or '').split(':')[0]
    if head in SOURCE_ID_RU:
        return SOURCE_ID_RU[head]
    named = source_name_ru(head, mode_id)
    if named and named != head:
        return named
    for prefix, name in _RECORD_PREFIX_RU:
        if head.startswith(prefix):
            return name
    return record_ru(rid)


def _factor_sources_ru(f: dict, mode_id: str | None, limit: int = 3) -> str:
    """Источники фактора: имена без повторов. Повторы здесь были бы не оформлением, а ошибкой —
    у фактора минут в аномалии две записи одного источника траектории, и в отчёте разметкой они
    печатались двумя одинаковыми строками подряд."""
    out: list[str] = []
    for rid in (f.get('records') or []):
        name = _record_source_ru(str(rid), mode_id)
        if name not in out:
            out.append(name)
    if not out:
        return 'источник в снимке не назван'
    return '; '.join(out[:limit]) + (' и ещё %d' % (len(out) - limit) if len(out) > limit else '')


_COUNT_IN_NAME_RE = re.compile(r'\s*\([^()]*(сигнал|запис)[^()]*\)')


def _short_condition(text) -> str:
    """Название условия: часть до двоеточия — это и есть имя, после двоеточия идёт перечень
    сигналов и записей, который в ячейку таблицы не помещается. Скобка со счётом сигналов и
    записей тоже снимается: у разных начал одно и то же условие описано разным их числом, и с
    ней «геомагнитная буря Kp ≥ 7 в окне» превращается в три разных названия одного условия.
    Полные тексты — в разделе «Рекомендация», в `report.md` и в `scan.json` архива."""
    return _COUNT_IN_NAME_RE.sub('', _txt(str(text or '').split(':')[0]))


# ---------------------------------------------------------------- популярное объяснение
_MONTHS_RU = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
              'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря')


def _minutes_ru(x: float) -> str:
    """«4 минуты», «74 минут», «5,7 минуты» — обычные правила русского счёта.

    Округление здесь своё, а не экранное: до десятых при значении меньше десяти минут и до
    целых дальше. Экранное правило минут (`app.export._min_md`) округляет 5,69 до 6, и в популярной фразе
    получилось бы «6 минут» против «5,69» в таблице двумя абзацами выше — читатель имеет
    право считать это разными числами. Дробное число требует родительного падежа
    единственного числа («5,7 минуты»), целое — по последней цифре.
    """
    x = float(x)
    if x < 10:
        s = ('%.1f' % x).rstrip('0').rstrip('.').replace('.', ',')
        if ',' in s:
            return s + ' минуты'
        n = int(s)
    else:
        n = int(round(x))
    last, last2 = n % 10, n % 100
    if last == 1 and last2 != 11:
        word = 'минута'
    elif last in (2, 3, 4) and last2 not in (12, 13, 14):
        word = 'минуты'
    else:
        word = 'минут'
    return '%d %s' % (n, word)


def _times_ru(ratio: float) -> str:
    """«в 600 раз», «в 2,4 раза». Отношение округляется до двух значащих цифр: третья цифра в
    популярной фразе ничего не добавляет, а точное значение стоит в таблицах числами."""
    if ratio < 10:
        s = ('%.1f' % ratio).rstrip('0').rstrip('.').replace('.', ',')
        n = float(ratio)
    else:
        n = float('%.2g' % ratio)
        s = '%d' % round(n)
    if ',' in s:
        return 'в %s раза' % s        # дробное число требует родительного падежа: «в 2,4 раза»
    last, last2 = int(n) % 10, int(n) % 100
    word = 'раз' if (last == 0 or last >= 5 or 11 <= last2 <= 14) else ('раза' if last != 1 else 'раз')
    return 'в %s %s' % (s, word)


def _clock_ru(iso: str, same_day_as: str | None = None) -> str:
    """Время для популярной фразы: «00:40», а когда это другие сутки — «00:40 4 мая»."""
    t = _t(iso)
    if t is None:
        return '—'
    other = _t(same_day_as)
    if other is not None and other.date() == t.date():
        return t.strftime('%H:%M')
    return '%s %d %s' % (t.strftime('%H:%M'), t.day, _MONTHS_RU[t.month - 1])


def plain_why_ru(S: dict) -> str:
    """Два предложения обычными словами: почему выходить именно в это время (ТЗ круга 12, п. 5).

    Слов «флюенс», «частиц на квадратный сантиметр», «геомагнитный» здесь нет — это требование
    владельца, а не украшение. Больше двух предложений тоже нельзя.

    Числа: минуты в аномалии и флюенс рекомендованной группы берутся из перебора как есть.
    ЗДЕСЬ И ТОЛЬКО ЗДЕСЬ считаются две величины, которых в снимке нет: худшее по минутам начало
    на сроке поиска и отношение наибольшего флюенса срока к флюенсу рекомендованной группы.
    Отношение берётся к НАИБОЛЬШЕМУ флюенсу группы — это самое осторожное из верных утверждений.

    Нечем сказать — сказано прямо. Пустая или выдуманная фраза здесь была бы обманом: именно
    эти два предложения человек прочтёт первыми.
    """
    sc = S.get('scan') or {}
    rec, cands = _recommended_candidate(S), (sc.get('candidates') or [])
    best = _best_candidates(S)
    # Первая проверка — НАЗВАНО ЛИ время. Лучшие начала перебор отмечает всегда, даже когда все
    # они под условием проверки и рекомендации нет; строить по ним фразу «в этот промежуток
    # воздействия наименьшие» значило бы рекомендовать время, которое сервис не рекомендовал.
    if rec is None or not best or not cands:
        return ('Сервис не назвал время выхода, поэтому объяснять нечего: причина — в вердикте и '
                'условиях этого же раздела.')
    dur = _duration_min(S)
    saa = [c['saa_min'] for c in best if c.get('saa_min') is not None]
    saa_all = [c['saa_min'] for c in cands if c.get('saa_min') is not None]
    flu = [c['fluence'] for c in best if c.get('fluence') is not None]
    flu_all = [(c['fluence'], c['start_utc']) for c in cands if c.get('fluence') is not None]
    if not saa or not saa_all or not flu or not flu_all:
        return ('Объяснение обычными словами не собрано: в снимке расчёта нет величин перебранных '
                'начал, по которым его строят.')
    lo, hi, worst = min(saa), max(saa), max(saa_all)
    # Начала группы равнозначны в допуске, но минуты у них могут различаться. Одно число тогда
    # было бы неправдой о половине промежутка, поэтому печатается размах группы.
    span = (_minutes_ru(lo) if _minutes_ru(lo) == _minutes_ru(hi)
            else ('от %s до %s' % (_minutes_ru(lo).split(' ')[0], _minutes_ru(hi))))
    when = 'В этот промежуток' if sc.get('answer_kind') == 'interval' else 'В это время'
    first = ('%s станция меньше всего времени проводит в радиационной аномалии: %s из %d против %s '
             'в худшее время на сроке поиска.' % (when, span, dur, _minutes_ru(worst)))
    peak, peak_start = max(flu_all)
    base = max(flu)
    ref = rec.get('start_utc')
    if base <= 0:
        second = ('Поток частиц за выход в нём по расчёту не набирается вовсе, а наибольшим он '
                  'становится к %s.' % _clock_ru(peak_start, ref))
    else:
        second = ('Набранный за выход поток частиц в нём %s ниже пикового, а пик приходится на %s.'
                  % (_times_ru(peak / base), _clock_ru(peak_start, ref)))
    return screen_text(first + ' ' + second)


# ---------------------------------------------------------------- разделы
def _title_block(S: dict) -> list[Block]:
    """Раздел 1 по ТЗ — титул (печатного номера не несёт): название сервиса, время расчёта,
    режим, версия алгоритма, коммит кода."""
    req = S.get('request') or {}
    commit = S.get('git_commit') or _git_sha()
    rows = [['Дата и время расчёта', _dt(S.get('computed_utc'), '%d.%m.%Y %H:%M UTC')],
            ['Режим', _txt(S.get('mode') or MODE_ID_RU.get(_mode_id(S) or '', '—'))],
            ['Версия алгоритма', _txt(S.get('algorithm_version') or 'не объявлена')],
            ['Коммит кода', commit or 'вне репозитория: коммит не определён'],
            ['Отсечка публикации', _dt(req['cutoff_utc'], '%d.%m.%Y %H:%M UTC') if req.get('cutoff_utc')
             else 'нет: данные не ограничивались временем публикации']]
    if S.get('is_simulated'):
        # Сценарий «что если» обязан быть назван на титуле: иначе моделируемые числа читаются
        # как наблюдения, а это ровно то заявление, которого сервис делать не имеет права.
        rows.append(['Сценарий «что если»', 'да: часть значений моделируемые, а не наблюдённые'])
    # Расстояния титула: заголовок на четверти высоты полосы, пояснение внизу — обычное
    # распределение титульного листа. Эти числа держат страницу, поэтому заданы в миллиметрах,
    # а не кеглем, как отбивка между абзацами.
    return [_gap(35.0), Block('title', SERVICE_TITLE), _p(SERVICE_SUBTITLE),
            _gap(16.0),
            _table(None, rows, [55.0, 110.0], size=SIZE_BODY),
            _gap(45.0),
            _p('Документ собран из снимка расчёта — того же, по которому построен экран сервиса. '
               'Числа в отчёте и на экране совпадают по построению: их печатает одна и та же функция. '
               'Идентификаторы записей, хеши выпусков и сетевые адреса первоисточников остаются в '
               'файлах архива выгрузки: report.md, sources.json, manifest.json.', size=SIZE_SMALL),
            Block('pagebreak')]


def _sources_rows(S: dict) -> list[list[str]]:
    mode_id = _mode_id(S)
    rows = []
    for k, v in (S.get('sources') or {}).items():
        if str(k).startswith('_'):       # служебные разделы снимка (перечень слоёв) — не источники
            continue
        age = v.get('age_min')
        if age is not None:
            age_ru = '%s мин' % fmt(round(float(age)))
        elif v.get('age_h') is not None:
            age_ru = '%s ч' % fmt(float(v['age_h']))      # давность орбиты снимок хранит в часах
        else:
            age_ru = 'не применимо'                        # архив и таблицы норм давности не имеют
        rows.append([_txt(source_name_ru(k, mode_id)), _txt(v.get('role') or '—'),
                     _dt(v.get('data_utc'), '%d.%m %H:%M') if v.get('data_utc') else '—',
                     _dt(v.get('fetched_utc'), '%d.%m %H:%M') if v.get('fetched_utc') else '—',
                     age_ru, _clean(dates_ru(status_ru(v.get('status'))))])
    return rows


def _input_blocks(S: dict) -> list[Block]:
    """Раздел 2 по ТЗ, печатается как «1. Входные данные»: чем спросили и на чём считали."""
    req, sc = S.get('request') or {}, (S.get('scan') or {})
    frm, to = _search_span(S)
    # Состояния отключённых источников называются теми же словами, что в боковой панели экрана
    # и в отчёте разметкой: перечень DISABLED_RU живёт в одном месте — app/export.py.
    disabled = ', '.join('%s — %s' % (_txt(source_name_ru(_SRC_KEY_RU.get(k, k), _mode_id(S))),
                                      DISABLED_RU.get(v, str(v)))
                         for k, v in (req.get('disabled') or {}).items() if v) or 'нет: все источники запрошены'
    rows = [['Длительность выхода', '%d мин' % _duration_min(S)],
            # Конец срока печатается с датой только тогда, когда сутки другие (_span_end), а буква Z
            # снимается тем же правилом дат, что на экране: слово UTC стоит в строке один раз.
            ['Срок поиска начала', ('%s — %s UTC' % (_dt(frm, '%d.%m %H:%M'), dates_ru(_span_end(frm, to))))
             if frm and to else 'в снимке не сохранён'],
            ['Шаг перебора начал', ('%s мин, перебрано начал %s' % (fmt(sc['step_min']), fmt(sc['n_candidates'])))
             if sc.get('step_min') else 'перебор не делался'],
            ['Режим', _txt(S.get('mode') or '—')],
            ['Отсечка публикации', _dt(req['cutoff_utc'], '%d.%m.%Y %H:%M UTC') if req.get('cutoff_utc')
             else 'нет: данные не ограничивались временем публикации'],
            ['Окна ручного сравнения', ', '.join('окно %d с %s' % (i + 1, _dt(w, '%d.%m %H:%M'))
                                                 for i, w in enumerate(req.get('windows') or [])) or 'нет'],
            ['Источники, отключённые пользователем', disabled],
            ['Траектория', _txt(_traj_line(S.get('trajectory_meta') or {}))]]
    return [_h(SECTION_TITLES[1]),
            _table(None, rows, [55.0, 110.0], size=SIZE_BODY),
            Block('spacer'),
            _lead('Источники: момент получения и давность данных на момент расчёта'),
            _table(['источник', 'роль', 'данные на', 'получено', 'давность', 'состояние'],
                   _sources_rows(S), [32.0, 29.0, 20.0, 20.0, 20.0, 44.0]),
            _p('Времена — UTC. «Не применимо» в давности стоит там, где у источника её нет: таблицы норм '
               'и каталоги не имеют момента наблюдения. Сетевые адреса записей и их идентификаторы — в '
               'report.md и manifest.json архива выгрузки.', size=SIZE_SMALL)]


def _ranking_rows(S: dict) -> list[list[str]]:
    """Величины рекомендованного начала — ровно те две, по которым идёт ранжирование, плюс
    метеороиды и покрытие. Имена и единицы берутся у факторов окна, чтобы величина называлась
    в отчёте так же, как на экране, а не своим вторым именем."""
    c = _recommended_candidate(S)
    if not c:
        return []
    win, _why = _reference_window(S)
    names = {f['name']: f for m in ((win or {}).get('mechanisms') or []) for f in m.get('factors', [])}
    flu_name = next((n for n in names if n.startswith('флюенс')), 'флюенс захваченных протонов')
    hits_name = next((n for n in names if n.startswith('ожидаемое число попаданий')),
                     'ожидаемое число попаданий, пластина 1 м²')
    unit = {n: (names[n].get('unit') or '') for n in names}
    rows = [['минут в аномалии', fmt(c['saa_min']) if c.get('saa_min') is not None else 'не посчитано',
             'мин'],
            [flu_name, fmt(c['fluence']) if c.get('fluence') is not None else 'не посчитан',
             unit.get(flu_name) or 'част./см²'],
            [hits_name, fmt(c['mmod_hits']) if c.get('mmod_hits') is not None else 'не посчитано',
             unit.get(hits_name) or 'шт'],
            ['покрытие моделью на этом начале', COV_RU.get(c.get('coverage'), str(c.get('coverage'))), '—']]
    return rows


def _factor_rows(win: dict, mode_id: str | None) -> list[list[str]]:
    rows = []
    for m in win.get('mechanisms') or []:
        for f in m.get('factors') or []:
            rows.append([_txt(f['name']), _factor_value(f), _txt(f.get('unit') or '—'),
                         KIND_RU.get(f.get('kind'), str(f.get('kind'))),
                         _txt(_factor_sources_ru(f, mode_id))])
    return rows


def _factors_blocks(S: dict) -> list[Block]:
    """Раздел 3 по ТЗ, печатается как «2. Количественная оценка факторов»."""
    out = [_h(SECTION_TITLES[2])]
    rank = _ranking_rows(S)
    rec = _recommended_candidate(S)
    if rank:
        out += [_lead('Величины рекомендованного начала %s UTC, по ним шло сравнение начал'
                      % _dt(rec['start_utc'], '%d.%m %H:%M')),
                _table(['величина', 'значение', 'единица'], rank, [75.0, 45.0, 45.0], size=SIZE_BODY),
                _p('Происхождение обеих сравниваемых величин — наш расчёт по траектории и таблицам '
                   'норм; источники расчёта названы в таблице факторов ниже.', size=SIZE_SMALL),
                Block('spacer')]
    else:
        out += [_p('Сервис не назвал начало выхода, поэтому величин рекомендованного начала здесь нет. '
                   'Причина — в разделе «Рекомендация».'), Block('spacer')]
    win, why = _reference_window(S)
    if win is None:
        out.append(_p('Полного набора факторов в снимке расчёта нет: ни одно окно не рассчитано целиком.'))
        return out
    # «Факторы окна: Окно 2 — …» повторяло слово «окно» дважды подряд. Заголовок окна приходит
    # из общей функции выгрузки и начинается с «Окно N» — согласуется падеж, а не текст.
    out += [_lead('Факторы %s' % _txt(_win_title(win)).replace('Окно ', 'окна ', 1)),
            _p('Почему именно это окно: %s.' % why, size=SIZE_SMALL),
            _table(['величина', 'значение', 'единица', 'происхождение', 'источник'],
                   _factor_rows(win, _mode_id(S)), [40.0, 21.0, 17.0, 29.0, 58.0])]
    return out


# Сколько разных условий печатается полным текстом. Условие занимает до пяти строк, и пять
# условий подряд — это страница текста вместо рекомендации; остальные остаются в отчёте
# разметкой и в scan.json, где их читают целиком.
CONDITIONS_SHOWN = 3


def _conditions_of(S: dict) -> tuple[list[str], int]:
    """Условия проверки лучших начал: полные тексты без повторов и сколько их всего.

    Ответ-промежуток берёт условия со ВСЕЙ группы: условие, стоящее хоть у одного начала
    промежутка, касается человека, который это начало выберет. Повторы отсеиваются по имени
    условия (часть до двоеточия): у разных начал одно и то же условие описано разным числом
    сигналов и записей, и без отсева раздел превращается в перечень почти одинаковых абзацев.
    """
    seen, out = set(), []
    for c in _best_candidates(S):
        for t in c.get('conditions') or []:
            name = _short_condition(t)
            if name not in seen:
                seen.add(name)
                out.append(_txt(t))
    return out[:CONDITIONS_SHOWN], len(out)


def _recommendation_blocks(S: dict) -> list[Block]:
    """Раздел 4 по ТЗ, печатается как «3. Рекомендация»: когда выходить, почему обычными
    словами, условия проверки."""
    sc, r = S.get('scan') or {}, (S.get('recommendation') or {})
    verdict = (SCAN_VERDICT_RU.get(sc.get('verdict')) if sc else None) or \
        VERDICT_TITLE.get(r.get('verdict'), str(r.get('verdict') or '—'))
    # Ответ — теми же словами, что на экране (одна функция на оба). Ссылка «условия выше»
    # верна для отчёта разметкой, где условия стоят в карточках выше ответа; в этом документе
    # условия идут следом, поэтому ссылка приводится к месту.
    answer = (_txt(_scan_answer_md(sc).replace('**', '').replace('см. вердикт и условия выше',
                                                                 'см. вердикт и условия в этом разделе'))
              if sc else 'Перебор начал выхода не делался.')
    named = _recommended_candidate(S) is not None
    out = [_h(SECTION_TITLES[3]), _lead(verdict), _answer(answer)]
    if named:
        # Когда времени нет, два предложения обычными словами тоже нечему объяснять, и честная
        # строка `plain_why_ru` повторила бы ответ выше другими словами.
        out.append(_p(plain_why_ru(S)))
    conds, total = _conditions_of(S)
    if conds:
        out.append(_lead('Условия проверки, которые относятся к названному времени' if named
                         else 'Условия проверки, из-за которых сервис не называет начало сам'))
        out += [_p('— ' + c, size=SIZE_TABLE) for c in conds]
        if total > len(conds):
            out.append(_p('Ещё %d условие или более того же разбора — в отчёте разметкой и в scan.json '
                          'архива выгрузки.' % (total - len(conds)), size=SIZE_SMALL))
        out.append(_p('Условие не запрещает выход: начало с условием сервис не выбирает сам, его '
                      'проверяет аналитик. Это правило команды, а не норма.', size=SIZE_SMALL))
    elif sc:
        out.append(_p('Условий проверки у названного времени нет.' if named
                      else 'Условий проверки у лучших начал нет.'))
    if sc.get('why'):
        # Профессиональное основание ответа — числами, мелким кеглем, одной строкой. Правило
        # сравнения ОКОН здесь не печатается: оно относится к ручному сравнению двух окон и
        # называет другое время, чем рекомендованное начало; два разных ответа в одном разделе
        # читаются как противоречие. Правило перебора целиком стоит во вкладке «Методика».
        out.append(_p('Основание: %s.' % _txt(str(sc['why']).rstrip('.')), size=SIZE_SMALL))
    return out


def _limits_blocks(S: dict) -> list[Block]:
    """Раздел 5 по ТЗ, печатается как «4. Границы применимости». Здесь собрано то, что убрано
    с главного экрана пунктом 1 того же ТЗ: охват,
    непокрытые механизмы, область вывода, устойчивость, допуск и чего сервис не заявляет."""
    r, sc = S.get('recommendation') or {}, (S.get('scan') or {})
    out = [_h(SECTION_TITLES[4]),
           _p('Учтено: %s.' % _txt(', '.join(S.get('coverage_declared') or ['—']))),
           _p('Не учтено: %s.' % _txt(', '.join(S.get('coverage_missing') or ['—'])))]
    # Область вывода печатается ОДИН раз и в краткой форме. Подробная форма снимка
    # (`scope_detail`) — это та же краткая строка, к которой приклеен перечень фактов ниже;
    # напечатать обе значило бы дать один и тот же текст дважды подряд. Когда ответ дал перебор,
    # берётся его область вывода: она относится к названному времени, а не к окнам сравнения.
    scope = sc.get('scope') or r.get('scope')
    if scope:
        out.append(_p('Область вывода: %s.' % _txt(scope)))
    if r.get('scope_facts'):
        out.append(_lead('Что учтено в расчёте и чего в модели нет'))
    for k, v in (r.get('scope_facts') or []):
        out.append(_p('— %s: %s' % (_txt(k), _txt(v)), size=SIZE_TABLE))
    # Устойчивость считается для ручного сравнения окон и называет окно, а не начало перебора.
    # Без уточнения в шапке строка читается как ответ на «устойчив ли рекомендованный промежуток»,
    # то есть отвечает не на тот вопрос, который задан выше в разделе «Рекомендация».
    out.append(_p(_txt(_robustness_line(S)).replace(
        'Устойчивость выбора:', 'Устойчивость выбора при ручном сравнении окон:', 1), size=SIZE_TABLE))
    # Допуск печатается один раз. Строка перебора сама говорит, что допуски те же, по которым
    # сравниваются окна, и вместе со строкой окон давала два абзаца об одних и тех же числах.
    if sc.get('tolerance_note'):
        out.append(_p('Допуск при переборе начал: %s.' % _txt(sc['tolerance_note']), size=SIZE_TABLE))
    else:
        out.append(_p('Допуск равнозначности при сравнении окон: %s.' % _txt(_tolerance_line(r)), size=SIZE_TABLE))
    if S.get('policy_note'):
        out.append(_p(_txt(S['policy_note']), size=SIZE_TABLE))
    out += [_lead('Чего сервис не заявляет'),
            _p('— допустимость реального выхода: она за уполномоченными специалистами;', size=SIZE_TABLE),
            _p('— вероятность разгерметизации и попадания частицы в космонавта;', size=SIZE_TABLE),
            _p('— дозу человека; поток протонов у станции без учёта обрезания.', size=SIZE_TABLE)]
    return out


def _appendix_blocks(S: dict) -> list[Block]:
    """Раздел 6 по ТЗ, печатается как «5. Приложение»: все перебранные начала таблицей."""
    sc = S.get('scan') or {}
    out = [_h(SECTION_TITLES[5])]
    cands = sc.get('candidates') or []
    if not cands:
        out.append(_p('Перебор начал выхода в этом расчёте не делался, поэтому таблицы перебранных '
                      'начал здесь нет.'))
        return out
    frm, to = _search_span(S)
    out.append(_p('Перебрано начал: %s, шаг %s мин; срок поиска %s — %s UTC, длительность выхода %d мин. '
                  'Последнее начало — конец срока минус длительность выхода.'
                  % (fmt(sc.get('n_candidates')), fmt(sc.get('step_min')), _dt(frm, '%d.%m %H:%M'),
                     dates_ru(_span_end(frm, to)), _duration_min(S))))
    rows = []
    for c in cands[:APPENDIX_MAX_ROWS]:
        rows.append([fmt(c['rank']) if c.get('rank') is not None else '—',
                     _dt(c['start_utc'], '%d.%m %H:%M'),
                     fmt(c['saa_min']) if c.get('saa_min') is not None else 'не посчитано',
                     fmt(c['fluence']) if c.get('fluence') is not None else 'не посчитан',
                     fmt(c['mmod_hits']) if c.get('mmod_hits') is not None else 'не посчитано',
                     COV_RU.get(c.get('coverage'), str(c.get('coverage'))),
                     '; '.join(_short_condition(x) for x in (c.get('conditions') or [])) or 'нет'])
    # Перенос в шапке делает вёрстка по ширине колонки: мнимые переносы в тексте всё равно
    # снимаются подготовкой строк, и колонка, названная «минут в\nаномалии», получала бы
    # разрыв не там, где кончается место.
    out.append(_table(['ранг', 'начало UTC', 'минут в аномалии', 'флюенс, част./см²',
                       'попаданий на 1 м²', 'покрытие', 'условия проверки'],
                      rows, [11.0, 22.0, 21.0, 24.0, 26.0, 18.0, 43.0], size=SIZE_SMALL))
    if len(cands) > APPENDIX_MAX_ROWS:
        out.append(_p('Показаны первые %d начал из %d; остальные — в scan.json архива выгрузки.'
                      % (APPENDIX_MAX_ROWS, len(cands)), size=SIZE_SMALL))
    out.append(_p('Ранг — место начала в порядке правила сравнения: сначала идут '
                  'начала без условий проверки, затем начала с условиями; внутри одного слоя порядок '
                  'по времени, потому что выбирать внутри него нечем. Прочерк стоит у начал, которым '
                  'не хватило величин для ранжирования: непосчитанное не заменяется нулём.',
                  size=SIZE_SMALL))
    return out


def document_blocks(S: dict) -> list[Block]:
    """Весь документ блоками, в обязательном порядке разделов."""
    return (_title_block(S) + _input_blocks(S) + _factors_blocks(S)
            + _recommendation_blocks(S) + _limits_blocks(S) + _appendix_blocks(S))


# ---------------------------------------------------------------- вёрстка
def _footer_text(S: dict) -> str:
    return '%s · расчёт %s' % (SERVICE_TITLE, _dt(S.get('computed_utc'), '%d.%m.%Y %H:%M UTC'))


def _numbered_canvas(font_name: str, left_text: str):
    """Холст, который печатает колонтитул со сквозной нумерацией «стр. N из M».

    Общее число страниц известно только после вёрстки всего документа, поэтому страницы
    накапливаются и колонтитул рисуется вторым проходом. На титуле номер не ставится — так
    принято в отчётах, и первая страница остаётся чистой."""
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    class _Canvas(canvas.Canvas):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for number, state in enumerate(self._pages, start=1):
                self.__dict__.update(state)
                if number > 1:
                    self.setFont(font_name, SIZE_SMALL)
                    self.setFillGray(0.35)      # колонтитул светлее текста: он служебный
                    self.drawString(MARGIN_LEFT_MM * mm, FOOTER_BASELINE_MM * mm, left_text)
                    self.drawRightString((PAGE_WIDTH_MM - MARGIN_RIGHT_MM) * mm, FOOTER_BASELINE_MM * mm,
                                         'стр. %d из %d' % (number, total))
                    self.setFillGray(0)
                super().showPage()
            super().save()

    return _Canvas


def _styles(font: FontChoice):
    from reportlab.lib.styles import ParagraphStyle
    # keepWithNext у заголовков: иначе заголовок раздела остаётся один внизу страницы, а его
    # содержимое начинается на следующей — на снимке владельца это выглядит как пустой раздел.
    mk = lambda name, size, bold=False, space=0.0, keep=False: ParagraphStyle(  # noqa: E731 — фабрика стилей
        name, fontName=(font.bold if bold else font.regular), fontSize=size,
        leading=size * LEADING_RATIO, spaceAfter=space, spaceBefore=0, keepWithNext=keep)
    return {
        'title': mk('vkd-title', SIZE_TITLE, bold=True, space=SIZE_TITLE * 0.4),
        # Заголовок раздела: полужирный, а если полужирного начертания в системе нет — на два
        # пункта крупнее текста. Поддельного полужирного (текст, обведённый контуром) здесь нет.
        'h': mk('vkd-h', SIZE_H if font.bold_is_real else SIZE_H + 2, bold=True,
                space=SIZE_H * 0.5, keep=True),
        'lead': mk('vkd-lead', SIZE_BODY, bold=True, space=SIZE_BODY * 0.3, keep=True),
        'answer': mk('vkd-answer', SIZE_H, bold=True, space=SIZE_H * 0.5),
        'p': mk('vkd-p', SIZE_BODY, space=SIZE_BODY * 0.45),
        'table': mk('vkd-t', SIZE_TABLE, space=SIZE_TABLE * 0.35),
        'small': mk('vkd-s', SIZE_SMALL, space=SIZE_SMALL * 0.4),
        'cell': mk('vkd-cell', SIZE_TABLE),
        'cell_head': mk('vkd-cell-h', SIZE_TABLE, bold=True),
    }


def _para_style(styles, size):
    """Стиль абзаца по кеглю: кегль задаётся блоком, а не подбирается на месте."""
    if size is None or size == SIZE_BODY:
        return styles['p']
    return styles['table'] if size == SIZE_TABLE else styles['small']


# Отбивка ячейки слева и справа, в пунктах (задаётся стилем таблицы ниже). Ширина слова
# сравнивается с шириной колонки за вычетом отбивки: иначе «происхождение» рвётся на
# «происхожден / ие» — ровно тот вид, который владелец назвал «вырви глаз».
CELL_PADDING_PT = 2.0
MM_PT = 72.0 / 25.4                      # пунктов в миллиметре: PDF меряет всё в пунктах


def _check_header_fits(b: Block, font: FontChoice) -> None:
    """Каждое слово шапки обязано помещаться в свою колонку целиком.

    Слово, которое не помещается, reportlab переносит ПО БУКВАМ, без переноса по слогам и без
    предупреждения. Ширины колонок подобраны по этой проверке, и она же не даёт им разойтись
    с текстом при следующей правке шапки."""
    from reportlab.pdfbase import pdfmetrics
    if not b.header:
        return
    name = font.bold if font.bold_is_real else font.regular
    for title, width_mm in zip(b.header, b.widths_mm):
        room = width_mm * MM_PT - 2 * CELL_PADDING_PT
        for word in str(title).replace('\n', ' ').split(' '):
            if pdfmetrics.stringWidth(word, name, b.size) > room:
                raise PdfNotBuilt('слово шапки «%s» не помещается в колонку %.1f мм' % (word, width_mm))


def _flowables(blocks: list[Block], font: FontChoice):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import LongTable, PageBreak, Paragraph, Spacer
    styles = _styles(font)
    out = []
    for b in blocks:
        if b.kind == 'title':
            out.append(Paragraph(_esc(b.text), styles['title']))
        elif b.kind == 'h':
            out.append(Paragraph(_esc(b.text), styles['h']))
        elif b.kind == 'lead':
            out.append(Paragraph(_esc(b.text), styles['lead']))
        elif b.kind == 'p':
            out.append(Paragraph(_esc(b.text), _para_style(styles, b.size)))
        elif b.kind == 'spacer':
            out.append(Spacer(1, (b.size * mm) if b.size else SIZE_BODY * 0.8))
        elif b.kind == 'answer':
            out.append(Paragraph(_esc(b.text), styles['answer']))
        elif b.kind == 'pagebreak':
            out.append(PageBreak())
        elif b.kind == 'table':
            cell = styles['cell'] if b.size == SIZE_TABLE else ParagraphStyle(
                'vkd-cell-%g' % b.size, parent=styles['cell'], fontSize=b.size, leading=b.size * LEADING_RATIO)
            head = ParagraphStyle('vkd-cellh-%g' % b.size, parent=styles['cell_head'],
                                  fontSize=b.size, leading=b.size * LEADING_RATIO)
            data = []
            if b.header:
                data.append([Paragraph(_esc(h), head) for h in b.header])
            for row in b.rows:
                data.append([Paragraph(_esc(x), cell) for x in row])
            style = [('VALIGN', (0, 0), (-1, -1), 'TOP'),
                     ('LINEBELOW', (0, 0), (-1, -1), 0.25, colors.Color(0.75, 0.75, 0.75)),
                     ('LEFTPADDING', (0, 0), (-1, -1), CELL_PADDING_PT),
                     ('RIGHTPADDING', (0, 0), (-1, -1), CELL_PADDING_PT),
                     ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2)]
            if b.header:
                style += [('LINEBELOW', (0, 0), (-1, 0), 0.75, colors.black)]
            # Таблица шире полосы набора уходит за правое поле молча — reportlab её не обрежет
            # и не предупредит. Ширины заданы в миллиметрах именно поэтому: их сумму видно.
            if sum(b.widths_mm) > TEXT_WIDTH_MM + 0.01:
                raise PdfNotBuilt('таблица шире полосы набора: %.1f мм при %.1f мм'
                                  % (sum(b.widths_mm), TEXT_WIDTH_MM))
            _check_header_fits(b, font)
            t = LongTable(data, colWidths=[w * mm for w in b.widths_mm],
                          repeatRows=1 if b.header else 0, hAlign='LEFT')
            t.setStyle(style)
            out.append(t)
            out.append(Spacer(1, SIZE_BODY * 0.5))
        else:                                   # pragma: no cover — вид блока задаётся только здесь же
            raise PdfNotBuilt('неизвестный вид блока документа: %s' % b.kind)
    return out


def document_text(blocks: list[Block]) -> str:
    """Весь текст документа одной строкой — для выбора шрифта и для проверок."""
    return '\n'.join(t for b in blocks for t in b.texts())


def build_pdf(S: dict) -> bytes:
    """Готовый PDF из снимка расчёта. Не удалось — PdfNotBuilt с причиной по-русски."""
    if _IMPORT_ERROR:
        raise PdfNotBuilt('библиотека вёрстки reportlab не установлена (%s); отчёт разметкой в '
                          'архиве не зависит от неё.' % _IMPORT_ERROR)
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate

    blocks = document_blocks(S)
    footer = _footer_text(S)
    # Колонтитул рисуется мимо абзацев, прямо на холсте, и его знаки тоже обязаны быть в шрифте:
    # иначе номер страницы и подпись внизу встали бы квадратами в проверенном документе.
    font = choose_font(set(document_text(blocks) + footer + 'стр. из 0123456789') - set(' \n'))
    pdfmetrics.registerFont(TTFont(font.regular, font.regular_path))
    if font.bold_is_real:
        pdfmetrics.registerFont(TTFont(font.bold, font.bold_path))
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=MARGIN_LEFT_MM * mm, rightMargin=MARGIN_RIGHT_MM * mm,
        topMargin=MARGIN_TOP_MM * mm, bottomMargin=MARGIN_BOTTOM_MM * mm,
        title='%s — отчёт о расчёте %s' % (SERVICE_TITLE, _dt(S.get('computed_utc'), '%d.%m.%Y %H:%M UTC')),
        author='ВКД-Риск', subject='Планирование выхода в открытый космос', lang='ru',
        # Какой шрифт взят — в свойствах файла, а не на странице: на странице это лишнее, а при
        # разборе «почему буквы такие» первый вопрос именно этот.
        creator='ВКД-Риск · шрифт %s' % font.describe())
    doc.build(_flowables(blocks, font), canvasmaker=_numbered_canvas(font.regular, footer))
    return buf.getvalue()


def build_pdf_or_reason(S: dict) -> tuple[bytes | None, str | None]:
    """(PDF, None) либо (None, причина по-русски). Исключение наружу не выпускается: выгрузка
    обязана собраться и тогда, когда отчёт PDF собрать нечем."""
    try:
        return build_pdf(S), None
    except PdfNotBuilt as exc:
        return None, str(exc)
    except Exception as exc:             # noqa: BLE001 — любая поломка вёрстки не должна ронять выгрузку
        return None, 'вёрстка PDF прервалась: %s: %s' % (type(exc).__name__, exc)


# ---------------------------------------------------------------- сверка чисел
def numbers_from_snapshot(S: dict) -> dict[str, str]:
    """Числа, которые обязаны стоять в готовом PDF, — прямо из снимка расчёта и тем же
    форматером, которым печатает экран. Строится НЕ из документа, а из `S`: иначе проверка
    сверяла бы документ сам с собой. Тест сверяет каждое значение с текстом, извлечённым из
    собранного PDF, и любое расхождение — ошибка, а не мелочь оформления."""
    out: dict[str, str] = {}
    sc = S.get('scan') or {}
    if sc.get('n_candidates') is not None:
        out['перебрано начал'] = fmt(sc['n_candidates'])
    if sc.get('step_min') is not None:
        out['шаг перебора, мин'] = fmt(sc['step_min'])
    if _duration_min(S):
        out['длительность выхода, мин'] = '%d' % _duration_min(S)
    c = _recommended_candidate(S)
    if c:
        if c.get('saa_min') is not None:
            out['минут в аномалии рекомендованного начала'] = fmt(c['saa_min'])
        if c.get('fluence') is not None:
            out['флюенс рекомендованного начала'] = fmt(c['fluence'])
        if c.get('mmod_hits') is not None:
            out['попаданий метеороидов на рекомендованном начале'] = fmt(c['mmod_hits'])
    win, _why = _reference_window(S)
    for m in ((win or {}).get('mechanisms') or []):
        for f in m.get('factors') or []:
            if f.get('value') is None:
                continue
            if factor_value_ru(_as_screen_factor(f), f.get('unit') or '') == '—':
                continue
            out['фактор окна: %s' % f['name']] = fmt(f['value'])
    return out


def missing_numbers(S: dict, pdf_text: str) -> dict[str, str]:
    """Какие из этих чисел в извлечённом тексте PDF не нашлись. Пустой словарь — сверка прошла."""
    plain = pdf_text.replace('\u202f', '').replace('\u00a0', '').replace('\n', ' ')
    return {k: v for k, v in numbers_from_snapshot(S).items() if v.replace('\u202f', '') not in plain}
