# -*- coding: utf-8 -*-
"""Извлечение таблиц ГОСТ Р 25645.167-2005 (техногенное вещество) в CSV.

Источник: docs/istochniki/GOST_R_25645.167-2005_tehnogennoe_veschestvo.pdf
Страницы PDF (нумерация с нуля) и печатные номера таблиц:
  * с. 6   — таблица 5.1  разбиение размеров КО, средняя масса, плотность;
  * с. 17  — таблица 7.1  средняя скорость возможных столкновений V_стл, км/с;
  * с. 17—18 — таблица 7.2 плотность потока Q_отн(h, i)_j, м^-2 * год^-1;
  * с. 26—36 — таблицы 8.1—8.16 функция прогноза F(t), годы.

PDF — скан с текстовым слоем OCR. Слой читается целиком, но OCR подменяет
несколько знаков гомоглифами кириллицы (З вместо 3, О вместо 0, Е вместо E) и
иногда вставляет пробел перед запятой («2 ,86E-4»). Эти подстановки
МЕХАНИЧЕСКИЕ и снимаются нормализацией _norm(); каждая затронутая ячейка
таблицы 7.2 дополнительно сверена глазами с растром страницы (масштаб 3x).
Никакие значения не достраиваются и не угадываются: если после нормализации
число ячеек не совпадает с ожидаемым, скрипт падает.

Требует PyMuPDF (импорт fitz) — инструмент разработки, в requirements.txt не
вносится: рантайм-модуль vkd/assess/debris.py читает только готовые CSV.

Запуск:  python scripts/gost167_parse.py
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(_ROOT, 'docs', 'istochniki',
                   'GOST_R_25645.167-2005_tehnogennoe_veschestvo.pdf')
OUT = os.path.join(_ROOT, 'data', 'gost167')

# Контрольная сумма первоисточника: извлечение привязано к этому файлу.
SRC_SHA256 = '5b3c7e6c52853e111164fb9aa32e8233f6c5f4f254a4a59e7ec99a58f761efef'

# Сетки таблицы 7.2 (п. 7.1 стандарта).
INCLINATIONS = (55, 65, 75, 85, 95, 105)          # град
ALT_FLUX = (200, 400, 600, 800, 1000, 1200, 1400)  # км
ALT_VEL = (400, 600, 800, 1000, 1200, 1400)        # км, таблица 7.1 — без 200
J_RANGE = tuple(range(1, 9))
YEARS = tuple(range(2000, 2026))

# Таблицы 8.1—8.16: (номер, j, K). 8.1—8.8 — K = 1, 8.9—8.16 — K = 0,5 после 2005 г.
FORECAST_TABLES = tuple(
    ('8.%d' % n, j, 1.0) for n, j in zip(range(1, 9), J_RANGE)
) + tuple(
    ('8.%d' % n, j, 0.5) for n, j in zip(range(9, 17), J_RANGE)
)

# Таблица 5.1, с. 6 — сверена с растром страницы.
TABLE_5_1 = (
    # j, размер от (см), размер до (см), средняя масса (кг), плотность (г/см3)
    (1, 0.10, 0.25, 0.86e-5, 2.5),
    (2, 0.25, 0.50, 0.58e-4, 2.7),
    (3, 0.50, 1.00, 0.28e-3, 2.9),
    (4, 1.00, 2.50, 0.0018, 3.1),
    (5, 2.50, 5.00, 0.010, 3.3),
    (6, 5.00, 10.00, 0.064, 3.5),
    (7, 10.00, 20.00, 0.363, 3.7),
    (8, 20.00, None, 300.0, 3.9),   # «свыше 20 см», верхней границы нет
)

# ОПЕЧАТКИ ПЕРВОИСТОЧНИКА. Все три сверены с растром страницы: в стандарте
# напечатано именно так, это не ошибка OCR. В CSV значение сохраняется КАК
# НАПЕЧАТАНО (колонка value), исправление — в отдельной колонке corrected.
# Каждая найдена структурной проверкой _check(), а не подбором.
KNOWN_MISPRINTS = (
    {'kind': 'flux', 'table': '7.2',
     'where': {'inclination_deg': 105, 'j': 3, 'alt_km': 1400},
     'printed': 3.53e-3, 'corrected': 3.53e-4,
     'why': 'поток КО растёт с размером (j=3 больше, чем j=2 в том же столбце: '
            '3,53E-3 против 2,00E-3), что невозможно; при всех остальных '
            'наклонениях на 1400 км стоит 2,38E-4…3,52E-4. Потерян разряд порядка.'},
    {'kind': 'forecast', 'table': '8.12',
     'where': {'table': '8.12', 'year': 2015, 'alt_km': 600},
     'printed': 25.007, 'corrected': 15.007,
     'why': 'F(t) обязана возрастать: соседи по столбцу 14,162 (2014) и 15,870 (2016). '
            'Первая цифра напечатана как 2 вместо 1.'},
    {'kind': 'forecast', 'table': '8.13',
     'where': {'table': '8.13', 'year': 2001, 'alt_km': 1200},
     'printed': 1.020, 'corrected': 2.020,
     'why': 'по п. 8.3 гипотезы K=1 и K=0,5 совпадают до 2005 г., а в таблице 8.5 '
            '(тот же диапазон размеров, K=1) за 2001 г. на 1200 км стоит 2,020; '
            'соседи по строке 1,995 и 2,014. Первая цифра напечатана как 1 вместо 2.'},
)


def _apply_misprints(rows, kind, field):
    """Проверяет, что напечатанное значение то самое, и проставляет исправление."""
    for r in rows:
        r['corrected'] = ''
    for mp in KNOWN_MISPRINTS:
        if mp['kind'] != kind:
            continue
        hits = [r for r in rows if all(r[k] == v for k, v in mp['where'].items())]
        if len(hits) != 1:
            raise SystemExit('опечатка %s: найдено %d ячеек по ключу %r'
                             % (mp['table'], len(hits), mp['where']))
        r = hits[0]
        if abs(r[field] - mp['printed']) > 1e-9 * max(1.0, abs(mp['printed'])):
            raise SystemExit('опечатка %s: в PDF теперь %r, а ожидалось напечатанное %r'
                             % (mp['table'], r[field], mp['printed']))
        r['corrected'] = mp['corrected']
    return rows


def _value(r, field):
    """Значение для расчёта: исправление, если оно есть, иначе напечатанное."""
    return r['corrected'] if r['corrected'] != '' else r[field]


def _norm(t: str) -> str:
    """Снятие OCR-гомоглифов и разрывов внутри чисел. Только механические замены."""
    t = (t.replace('З', '3').replace('з', '3')
          .replace('О', '0').replace('о', '0')
          .replace('Е', 'E').replace('е', 'e'))
    t = re.sub(r'\s*[Ee]\s*[-–]\s*', 'E-', t)   # «2,41 Е-6» -> «2,41E-6»
    t = re.sub(r'(\d)\s+,', r'\1,', t)               # «2 ,86E-4» -> «2,86E-4»
    return t


def _page_text(doc, *pages: int) -> str:
    return _norm(''.join(doc[p].get_text() for p in pages))


def parse_flux(doc) -> list[dict]:
    """Таблица 7.2: 6 наклонений x 8 диапазонов x 7 высот = 336 значений."""
    txt = _page_text(doc, 17, 18)
    # Собираем число строкой, а не умножением на 10**-k: иначе 2,49E-3
    # превращается в 0.0024900000000000005 и мусорит в CSV.
    vals = [float('%sE-%s' % (m.group(1).replace(',', '.'), m.group(2)))
            for m in re.finditer(r'(\d[.,]\d\d)E-(\d)', txt)]
    want = len(INCLINATIONS) * len(J_RANGE) * len(ALT_FLUX)
    if len(vals) != want:
        raise SystemExit('таблица 7.2: найдено %d значений, ожидалось %d' % (len(vals), want))
    rows, k = [], 0
    for inc in INCLINATIONS:
        for j in J_RANGE:
            for alt in ALT_FLUX:
                rows.append({'inclination_deg': inc, 'j': j, 'alt_km': alt,
                             'flux_m2_yr': vals[k]})
                k += 1
    return rows


def parse_velocity(doc) -> list[dict]:
    """Таблица 7.1: средняя скорость возможных столкновений, км/с."""
    txt = _page_text(doc, 17)
    # Границы — по заголовкам таблиц (буквы в слове «Таблица» разрежены).
    # Нумерация ПУНКТОВ 7.1—7.3 идёт вперемешку с нумерацией таблиц, поэтому
    # искать по «7.2» как по строке нельзя: пункт 7.2 стоит до таблицы 7.1.
    head = re.compile(r'Т\s*а\s*б\s*л\s*и\s*ц\s*а\s*7\.(\d)')
    pos = {m.group(1): m.start() for m in head.finditer(txt)}
    if '1' not in pos or '2' not in pos:
        raise SystemExit('таблица 7.1: не найдены заголовки таблиц 7.1 и 7.2 на с. 17')
    body = txt[pos['1']:pos['2']]
    vals = [float(m.group(0).replace(',', '.'))
            for m in re.finditer(r'\b1[0-9],\d\b', body)]
    want = len(INCLINATIONS) * len(ALT_VEL)
    if len(vals) != want:
        raise SystemExit('таблица 7.1: найдено %d значений, ожидалось %d' % (len(vals), want))
    rows, k = [], 0
    for inc in INCLINATIONS:
        for alt in ALT_VEL:
            rows.append({'inclination_deg': inc, 'alt_km': alt,
                         'v_collision_km_s': vals[k]})
            k += 1
    return rows


def parse_forecast(doc) -> list[dict]:
    """Таблицы 8.1—8.16: F(t), годы, для 7 высот и 26 лет каждая."""
    txt = _page_text(doc, *range(26, 37))
    # Границы таблиц по заголовкам «Т а б л и ц а 8.N» (буквы разрежены).
    head = re.compile(r'Т\s*а\s*б\s*л\s*и\s*ц\s*а\s*(8\.\d+)')
    marks = [(m.start(), m.group(1)) for m in head.finditer(txt)]
    if len(marks) != len(FORECAST_TABLES):
        raise SystemExit('раздел 8: найдено %d заголовков, ожидалось %d'
                         % (len(marks), len(FORECAST_TABLES)))
    marks.append((len(txt), None))
    by_name = {name: (j, K) for name, j, K in FORECAST_TABLES}
    rows = []
    for k in range(len(marks) - 1):
        beg, name = marks[k]
        chunk = txt[beg:marks[k + 1][0]]
        if name not in by_name:
            raise SystemExit('раздел 8: неожиданная таблица %s' % name)
        j, K = by_name[name]
        # Строка года: год 2000—2025, затем ровно 7 чисел вида 12,345 / 12.345.
        found = {}
        for m in re.finditer(r'\b(20[0-2]\d)\b((?:\s+\d{1,3}[.,]\d{3}){7})', chunk):
            year = int(m.group(1))
            if year not in YEARS:
                continue
            nums = [float(x.replace(',', '.')) for x in m.group(2).split()]
            found[year] = nums
        missing = [y for y in YEARS if y not in found]
        if missing:
            raise SystemExit('таблица %s: нет строк за годы %s' % (name, missing))
        for year in YEARS:
            for alt, v in zip(ALT_FLUX, found[year]):
                rows.append({'table': name, 'j': j, 'K': K, 'year': year,
                             'alt_km': alt, 'F_years': v})
    return rows


def _check(flux, vel, fc):
    """Структурные проверки на ИСПРАВЛЕННЫХ значениях. Если после объявленных
    опечаток что-то не сходится — это ошибка извлечения, и скрипт падает.
    Обратное тоже верно: три опечатки найдены именно этими проверками."""
    bad = []
    idx = {(r['inclination_deg'], r['j'], r['alt_km']): _value(r, 'flux_m2_yr') for r in flux}
    # 1. Поток убывает с ростом размера КО (j = 7 -> 8 исключён: j = 8 — каталог,
    #    «свыше 20 см», и стандарт даёт для него подъём относительно j = 7).
    for inc in INCLINATIONS:
        for alt in ALT_FLUX:
            for j in range(1, 7):
                a, b = idx[(inc, j, alt)], idx[(inc, j + 1, alt)]
                if b >= a:
                    bad.append('поток растёт с размером: i=%d h=%d j=%d->%d (%g -> %g)'
                               % (inc, alt, j, j + 1, a, b))
    # 2. Все значения положительны.
    for r in flux:
        if not _value(r, 'flux_m2_yr') > 0:
            bad.append('неположительный поток %r' % r)
    # 3. F(2000) = 1,000 во всех таблицах и на всех высотах.
    for r in fc:
        if r['year'] == 2000 and abs(_value(r, 'F_years') - 1.0) > 1e-9:
            bad.append('F(2000) != 1 в таблице %s, h=%d: %g'
                       % (r['table'], r['alt_km'], _value(r, 'F_years')))
    # 4. F(t) строго возрастает по годам.
    seq = {}
    for r in fc:
        seq.setdefault((r['table'], r['alt_km']), {})[r['year']] = _value(r, 'F_years')
    for (name, alt), d in seq.items():
        for y in YEARS[1:]:
            if d[y] <= d[y - 1]:
                bad.append('F(t) не возрастает: %s h=%d %d->%d (%g -> %g)'
                           % (name, alt, y - 1, y, d[y - 1], d[y]))
    # 5. Гипотезы K=1 и K=0,5 совпадают до 2005 г. включительно (п. 8.3).
    pair = {}
    for r in fc:
        pair.setdefault((r['j'], r['alt_km'], r['year']), {})[r['K']] = _value(r, 'F_years')
    for (j, alt, year), d in pair.items():
        if year <= 2005 and len(d) == 2 and abs(d[1.0] - d[0.5]) > 1e-9:
            bad.append('K=1 и K=0,5 расходятся до 2005 г.: j=%d h=%d %d (%g vs %g)'
                       % (j, alt, year, d[1.0], d[0.5]))
    # 6. После 2005 г. «оптимистическая» гипотеза не может давать больше «пессимистической».
    for (j, alt, year), d in pair.items():
        if year > 2005 and len(d) == 2 and d[0.5] > d[1.0] + 1e-9:
            bad.append('K=0,5 > K=1: j=%d h=%d %d (%g > %g)' % (j, alt, year, d[0.5], d[1.0]))
    # 7. Скорость столкновений в разумных пределах.
    for r in vel:
        if not 5.0 <= r['v_collision_km_s'] <= 20.0:
            bad.append('скорость вне 5…20 км/с: %r' % r)
    if bad:
        raise SystemExit('проверки не пройдены:\n  ' + '\n  '.join(bad))


def _write_csv(path, rows, fields):
    with io.open(path, 'w', encoding='utf-8', newline='\n') as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator='\n')
        w.writeheader()
        w.writerows(rows)
    return hashlib.sha256(io.open(path, 'rb').read()).hexdigest()


def main() -> int:
    import fitz  # PyMuPDF, только для извлечения

    raw = io.open(SRC, 'rb').read()
    got = hashlib.sha256(raw).hexdigest()
    if got != SRC_SHA256:
        raise SystemExit('PDF первоисточника изменился: %s != %s' % (got, SRC_SHA256))
    doc = fitz.open(SRC)
    os.makedirs(OUT, exist_ok=True)

    flux = _apply_misprints(parse_flux(doc), 'flux', 'flux_m2_yr')
    vel = parse_velocity(doc)
    for r in vel:
        r['corrected'] = ''
    fc = _apply_misprints(parse_forecast(doc), 'forecast', 'F_years')
    _check(flux, vel, fc)

    sizes = [{'j': j, 'size_min_cm': lo, 'size_max_cm': ('' if hi is None else hi),
              'mean_mass_kg': m, 'density_g_cm3': rho} for j, lo, hi, m, rho in TABLE_5_1]

    files = [
        ('table_5_1_size_ranges.csv', sizes,
         ['j', 'size_min_cm', 'size_max_cm', 'mean_mass_kg', 'density_g_cm3'],
         'Таблица 5.1 — разбиение размеров и средней массы КО на диапазоны'),
        ('table_7_1_collision_velocity.csv', vel,
         ['inclination_deg', 'alt_km', 'v_collision_km_s', 'corrected'],
         'Таблица 7.1 — средняя скорость возможных столкновений V_стл, км/с'),
        ('table_7_2_flux.csv', flux,
         ['inclination_deg', 'j', 'alt_km', 'flux_m2_yr', 'corrected'],
         'Таблица 7.2 — плотность потока КО Q_отн(h, i)_j, м^-2 * год^-1, эпоха t0 = 2000 г. '
         '(колонка flux_m2_yr — как напечатано, corrected — исправление опечатки, если есть)'),
        ('table_8_forecast.csv', fc,
         ['table', 'j', 'K', 'year', 'alt_km', 'F_years', 'corrected'],
         'Таблицы 8.1—8.16 — функция прогноза F(t), годы '
         '(F_years — как напечатано, corrected — исправление опечатки, если есть)'),
    ]
    index = {
        'source': {
            'title': 'ГОСТ Р 25645.167-2005. Космическая среда (естественная и искусственная). '
                     'Модель пространственно-временного распределения плотности потоков '
                     'техногенного вещества в космическом пространстве',
            'file': 'docs/istochniki/GOST_R_25645.167-2005_tehnogennoe_veschestvo.pdf',
            'sha256': SRC_SHA256,
            'introduced': '2006-01-01',
        },
        'scope': {
            'size_cm_min': 0.1,
            'alt_km': [200, 2000],
            'years': [2000, 2025],
            'note': 'п. 1 «Область применения»: размер КО более 0,1 см, высота 200…2000 км, '
                    'произвольный момент времени с 2000 по 2025 г.',
        },
        'grids': {
            'inclination_deg': list(INCLINATIONS),
            'alt_km_flux': list(ALT_FLUX),
            'alt_km_velocity': list(ALT_VEL),
            'j': list(J_RANGE),
            'years': [YEARS[0], YEARS[-1]],
        },
        'known_misprints': list(KNOWN_MISPRINTS),
        'not_extracted': {
            'figures_7_3_7_5': 'коэффициент C_N для цилиндра, конуса и панели в зависимости от '
                               'углов ориентации альфа и бета дан ТОЛЬКО графиками (рисунки 7.3—7.5, '
                               'с. 21—23 печатные) — растр, оцифровке по тексту не поддаётся',
            'table_6_1_6_x': 'таблицы раздела 6 (Q(h, ф) относительно инерциальной системы '
                             'координат и средние тангенциальные скорости) не нужны для расчёта '
                             'потока относительно КА по формулам (2) и (6)',
            'table_7_3': 'распределение направлений скорости столкновений pV_стл(A) — в расчёт '
                         'числа попаданий на изотропное опорное тело (C_N = 1) не входит',
        },
        'files': [],
    }
    for name, rows, fields, title in files:
        sha = _write_csv(os.path.join(OUT, name), rows, fields)
        index['files'].append({'file': name, 'title': title, 'rows': len(rows), 'sha256': sha})
        print('%-34s %5d строк  %s' % (name, len(rows), sha[:16]))

    with io.open(os.path.join(OUT, 'index.json'), 'w', encoding='utf-8', newline='\n') as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
        f.write('\n')
    print('index.json записан, проверки пройдены')
    return 0


if __name__ == '__main__':
    sys.exit(main())
