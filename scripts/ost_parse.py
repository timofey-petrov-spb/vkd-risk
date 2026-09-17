# -*- coding: utf-8 -*-
"""Извлечение дозовых таблиц приложения К ОСТ 134-1044-2007 в CSV."""
import re, sys, io, os, json

SRC = 'ost1044.txt'
OUT = 'ost_tables'

# ОСТ непоследователен: в приложении К.1/К.2 разделитель запятая, в К.3 точка
NUM = re.compile(r'^-?\d[\d ]*[.,]?\d*E[+-]\d+$|^-?\d+[.,]\d+$|^-?\d+$', re.I)


def val(s):
    s = s.strip().replace(' ', '').replace(' ', '')
    if not NUM.match(s):
        return None
    try:
        return float(s.replace(',', '.'))
    except ValueError:
        return None


def parse_dose_table(lines):
    """lines — строки одной таблицы, начиная с заголовка."""
    title = lines[0]
    alt, geom_blocks, cur, cur_name = [], [], [], None
    i = 1
    # высоты: строки до слова 'полусфера'
    while i < len(lines) and 'полусфер' not in lines[i].lower():
        s = lines[i].strip()
        if s and re.match(r'^\d{3,5}(\s|$)', s):
            alt.append(s)
        i += 1
    n = len(alt)
    if n == 0:
        return None
    while i < len(lines):
        s = lines[i].strip()
        low = s.lower()
        if 'полусфер' in low or 'плоскост' in low:
            if cur_name:
                geom_blocks.append((cur_name, cur))
            cur_name, cur = ('полусфера' if 'полусфер' in low else 'плоскость'), []
            i += 1
            continue
        v = val(s)
        if v is not None:
            row = [v]
            j = i + 1
            while j < len(lines) and len(row) <= n:
                vv = val(lines[j])
                if vv is None:
                    break
                row.append(vv)
                j += 1
            if len(row) == n + 1:
                cur.append(row)
                i = j
                continue
        i += 1
    if cur_name:
        geom_blocks.append((cur_name, cur))
    return title, alt, geom_blocks


def main():
    t = io.open(SRC, encoding='utf-8', errors='replace').read()
    os.makedirs(OUT, exist_ok=True)
    marks = [(m.start(), m.group(1)) for m in
             re.finditer(r'Таблица (К\.\d\.\d)\s*[–-]', t)]
    marks.append((len(t), None))
    index = []
    for k in range(len(marks) - 1):
        beg, name = marks[k]
        end = marks[k + 1][0]
        chunk = t[beg:end]
        if 'Поглощенн' not in chunk.split('\n')[0]:
            continue
        lines = [l.strip() for l in chunk.split('\n')]
        r = parse_dose_table(lines)
        if not r:
            continue
        title, alt, blocks = r
        for geom, rows in blocks:
            if not rows:
                continue
            fn = '%s_%s.csv' % (name.replace('.', '_'),
                                'hemisphere' if geom == 'полусфера' else 'plane')
            with io.open(os.path.join(OUT, fn), 'w', encoding='utf-8') as f:
                f.write('d_g_cm2,' + ','.join(a.replace(',', ' ') for a in alt) + '\n')
                for row in rows:
                    f.write(','.join('%.6g' % x for x in row) + '\n')
            index.append({'table': name, 'geometry': geom, 'file': fn,
                          'thicknesses': len(rows), 'altitudes': len(alt),
                          'title': title[:160]})
            print('%-10s %-11s строк %2d  столбцов %2d  -> %s'
                  % (name, geom, len(rows), len(alt), fn))
    io.open(os.path.join(OUT, 'index.json'), 'w', encoding='utf-8').write(
        json.dumps(index, ensure_ascii=False, indent=1))
    print('\nвсего файлов:', len(index))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
