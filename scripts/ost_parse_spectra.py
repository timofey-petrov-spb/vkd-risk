# -*- coding: utf-8 -*-
"""Извлечение спектров протонов ЕРПЗ приложения К ОСТ 134-1044-2007 (К.2.1, К.2.2) в CSV.

Зачем отдельно от `ost_parse.py`. Тот извлекает ДОЗОВЫЕ таблицы К.x.5/К.x.6.
Здесь извлекаются таблицы К.2.1 и К.2.2 — дифференциальные спектры ФЛЮЕНСА
протонов ЕРПЗ за 10 лет САС на тех же круговых орбитах (наклонения 30 и 60).
Они нужны ровно для одного: сравнить средний по орбите уровень, который даёт
НАШ расчёт по приложению А, со средним уровнем, который для той же орбиты
объявляет сам стандарт. Это единственная независимая проверка нормировки дозы
(`vkd/assess/dose.py`), и без неё коэффициент пересчёта нечем поверить.

Единица в стандарте: «Поток протонов, пр./(МэВ·см2)» — это ФЛЮЕНС за 10 лет
на единицу энергии, не плотность потока. Проверка величины: при 400 км и
0,1 МэВ таблица даёт 9,48·10¹² пр./(МэВ·см²); делённое на 10 лет (3,15576·10⁸ с)
это 3,0·10⁴ см⁻²·с⁻¹·МэВ⁻¹ — обычный порядок для приложения А. Как плотность
потока 9,48·10¹² было бы нефизично.

ГРАБЛИ РАЗБОРА (найдено 19.09). В таблицах наклонения 30 (К.2.1 и дозовая К.2.5)
шапка высот содержит ПОСТОРОННИЕ лексемы «i98» и «i 0-5» — следы преобразования
исходного документа. Регулярное выражение по голым числам их отбрасывает, но
третий столбец таблицы при этом получает подпись «700» и ею НЕ является:
значения в нём выпадают из монотонного хода по высоте (при 0,1 МэВ
600 км → 1,86·10⁹, «700 км» → 2,37·10¹³, 800 км → 7,87·10⁹). Столбцы 400 и 600 км
стоят ДО него и не задеты. Поэтому здесь столбец с подписью 700 у наклонения 30
помечается в индексе как подозрительный, а потребитель (`vkd/assess/dose.py`)
работает только в диапазоне 400…600 км.

Запуск: python scripts/ost_parse_spectra.py
"""
from __future__ import annotations

import io
import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(_ROOT, 'docs', 'istochniki', 'OST_134-1044-2007_tekst.txt')
OUT = os.path.join(_ROOT, 'data', 'ost1044_spectra')

# Тот же разделитель, что в ost_parse.py: в приложении К.1/К.2 запятая.
NUM = re.compile(r'^-?\d[\d ]*[.,]?\d*E[+-]\d+$|^-?\d+[.,]\d+$|^-?\d+$', re.I)
ALT = re.compile(r'^\d{3,5}$')

# имя таблицы, чем ограничен блок, наклонение, файл
TABLES = [('К.2.1', 'К.2.2', 30, 'K_2_1.csv'),
          ('К.2.2', 'К.2.3', 60, 'K_2_2.csv')]


def val(s: str):
    s = s.strip().replace(' ', '').replace(' ', '')
    if not NUM.match(s):
        return None
    try:
        return float(s.replace(',', '.'))
    except ValueError:
        return None


def parse_block(lines: list[str]) -> tuple[list[int], list[list[float]]]:
    """Шапка — голые целые ДО первого числа в экспоненциальной записи; строки —
    прогоны ровно из (1 + число высот) чисел, начинающиеся с энергии."""
    first_e = next(i for i, l in enumerate(lines) if val(l) is not None and 'E' in l.upper())
    alts = [int(l) for l in lines[1:first_e] if ALT.match(l)]
    n = len(alts)
    if n == 0:
        raise ValueError('altitude header not found')
    rows, i = [], 1
    while i < len(lines):
        v = val(lines[i])
        if v is not None and 'E' in lines[i].upper():
            run, j = [v], i + 1
            while j < len(lines) and len(run) <= n:
                vv = val(lines[j])
                if vv is None:
                    break
                run.append(vv)
                j += 1
            if len(run) == n + 1:
                rows.append(run)
                i = j
                continue
        i += 1
    return alts, rows


def main() -> None:
    text = io.open(SRC, encoding='utf-8', errors='replace').read()
    lines = [l.strip() for l in text.split('\n')]
    os.makedirs(OUT, exist_ok=True)
    index = []
    for name, nxt, inc, fn in TABLES:
        beg = next(i for i, l in enumerate(lines) if l.startswith('Таблица ' + name))
        end = next(i for i, l in enumerate(lines) if l.startswith('Таблица ' + nxt))
        alts, rows = parse_block(lines[beg:end])
        with io.open(os.path.join(OUT, fn), 'w', encoding='utf-8') as f:
            f.write('E_MeV,' + ','.join(str(a) for a in alts) + '\n')
            for row in rows:
                f.write(','.join('%.6g' % x for x in row) + '\n')
        # Автоматическая проверка — ТОЛЬКО на паре 400/600 км: это единственные столбцы,
        # которыми пользуется расчёт, и на них поток обязан расти с высотой.
        # Общего правила «значение не больше обоих соседей» здесь нет и быть не может:
        # у поясов есть физический максимум по высоте (3000…6000 км), и такая проверка
        # честно срабатывала бы на нём. Поэтому дефект столбца «700 км» назван поимённо,
        # с причиной, а не выведен хрупким автоматическим признаком.
        i400, i600 = alts.index(400), alts.index(600)
        mono = all(r[1 + i600] >= r[1 + i400] for r in rows)
        if not mono:
            raise ValueError('%s: поток не растёт с 400 к 600 км — разбор изменился, проверять' % name)
        defect = None
        if 700 in alts:
            k = alts.index(700)
            out_of_order = sum(1 for r in rows if r[1 + k] > max(r[k], r[2 + k]))
            defect = ('столбец с подписью «700 км» на деле 700 км НЕ является: в шапке исходного '
                      'текста рядом с ним стоят посторонние лексемы «i98» и «i 0-5», и значения '
                      'выпадают из хода по высоте в %d строках из %d. Столбцы 400 и 600 км стоят '
                      'до него и не задеты; расчёт дозы работает только в 400…600 км.'
                      % (out_of_order, len(rows)))
        index.append({'table': name, 'inclination_deg': inc, 'file': fn,
                      'energies': len(rows), 'altitudes': len(alts), 'altitudes_km': alts,
                      'unit': 'пр./(МэВ·см²) — флюенс за 10 лет САС на единицу энергии',
                      'monotone_400_to_600_km': mono,
                      'known_defect_ru': defect,
                      'title': lines[beg][:160]})
        print('%-8s i=%2d  энергий %2d  высот %2d  400→600 монотонно: да  -> %s%s'
              % (name, inc, len(rows), len(alts), fn, '\n          ДЕФЕКТ: ' + defect if defect else ''))
    io.open(os.path.join(OUT, 'index.json'), 'w', encoding='utf-8').write(
        json.dumps(index, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
