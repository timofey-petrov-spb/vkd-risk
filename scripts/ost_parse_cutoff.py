# -*- coding: utf-8 -*-
"""Извлечение таблицы Ж.1 ОСТ 134-1044-2007 (вертикальная жёсткость обрезания R0c, ГВ,
высота H0 = 450 км, эпоха 2010) из текста стандарта в CSV.

Вход:  docs/istochniki/OST_134-1044-2007_tekst.txt
Выход: data/ost1044_cutoff/Zh_1_R0c_GV_h450km_epoch2010.csv
Сетка: широта 85…-85 через 5° (35 строк), долгота 0…330 через 30° (12 столбцов), 420 значений.
Грабли: таблица разорвана строкой «Окончание таблицы Ж.1», после которой повторяется
строка долгот — её нужно пропустить.
Пересчёт на высоту H по формуле (Ж.3): Rc(H) = R0c * ((R_З + 450) / (R_З + H))^2.
"""
from __future__ import annotations
import csv, io, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'docs', 'istochniki', 'OST_134-1044-2007_tekst.txt')
DST = os.path.join(ROOT, 'data', 'ost1044_cutoff', 'Zh_1_R0c_GV_h450km_epoch2010.csv')


def parse() -> tuple[list[int], list[tuple[int, list[float]]]]:
    lines = io.open(SRC, encoding='utf-8').read().split('\n')
    i0 = next(i for i, l in enumerate(lines) if l.startswith('Таблица Ж.1'))
    toks = []
    for l in lines[i0 + 1:i0 + 800]:
        s = l.strip()
        if s.startswith('Значение Rc'):
            break
        if s in ('Широта', 'Долгота', '') or s.startswith('Окончание таблицы'):
            continue
        toks.append(s)
    lons = [int(t) for t in toks[:12]]
    rest = toks[12:]
    rows, k = [], 0
    while k < len(rest):
        if rest[k] == '0' and rest[k + 1:k + 12] == [str(x) for x in lons[1:]]:
            k += 12          # повтор заголовка долгот после разрыва таблицы
            continue
        lat = int(rest[k])
        vals = [float(v.replace(',', '.')) for v in rest[k + 1:k + 13]]
        if len(vals) != 12:
            raise ValueError('строка широты %d: %d значений вместо 12' % (lat, len(vals)))
        rows.append((lat, vals))
        k += 13
    if len(rows) != 35 or rows[0][0] != 85 or rows[-1][0] != -85:
        raise ValueError('ожидалось 35 строк 85…-85, получено %d' % len(rows))
    return lons, rows


def main() -> None:
    lons, rows = parse()
    os.makedirs(os.path.dirname(DST), exist_ok=True)
    with io.open(DST, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['lat_deg'] + ['R0c_GV_lon_%d' % L for L in lons])
        for lat, vals in rows:
            w.writerow([lat] + ['%.3f' % v for v in vals])
    print('записано', DST, ':', len(rows), 'строк x', len(lons), 'долгот')


if __name__ == '__main__':
    main()
