# -*- coding: utf-8 -*-
"""Извлечение таблиц приложения А ОСТ 134-1044-2007 — модели потоков ЕРПЗ.

Это пространственно разрешённая модель: плотность потока как функция
(L, B/B0) и энергии. Именно она даёт зависимость среды от положения
на орбите, которой нет в усреднённых дозовых таблицах приложения К.

Приложение К остаётся нормировкой: долговременное среднее по орбите,
посчитанное через приложение А, обязано его воспроизводить.
"""
import io, json, os, re, sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(_ROOT, 'docs', 'istochniki', 'OST_134-1044-2007_tekst.txt')
OUT = os.path.join(_ROOT, 'data', 'ost1044_belts')

# ОСТ непоследователен: «Таблица А.1.1» с пробелом, но «ТаблицаА.2.1» без него.
TABLES = {
    'A_1_1': ('электроны ЕРПЗ, минимум СА', r'Таблица\s*А\.1\.1'),
    'A_1_2': ('электроны ЕРПЗ, максимум СА', r'Таблица\s*А\.1\.2'),
    'A_2_1': ('протоны ЕРПЗ, минимум СА',   r'Таблица\s*А\.2\.1'),
    'A_2_2': ('протоны ЕРПЗ, максимум СА',  r'Таблица\s*А\.2\.2'),
}

# граница таблицы — следующая «ТаблицаА.x» в любом написании либо «Приложение»
BOUND = re.compile(r'Таблица\s*А\.\d|Приложение\s+[А-Я]')

NUM = re.compile(r'^-?\d+[.,]?\d*(E[+-]\d+)?$', re.I)
LMARK = re.compile(r'^L\s*=\s*([\d.,]+)$')


def val(s):
    s = s.strip().replace(' ', '').replace(' ', '')
    if not NUM.match(s):
        return None
    try:
        return float(s.replace(',', '.'))
    except ValueError:
        return None


def parse(lines):
    """Возвращает (энергии, [(L, B/B0, [потоки...]), ...])."""
    energies, rows = [], []
    i, L = 1, None
    # энергии идут до первой метки L=
    while i < len(lines) and not LMARK.match(lines[i].strip()):
        v = val(lines[i])
        if v is not None:
            energies.append(v)
        i += 1
    n = len(energies)
    if n == 0:
        return None
    while i < len(lines):
        s = lines[i].strip()
        m = LMARK.match(s)
        if m:
            L = float(m.group(1).replace(',', '.'))
            i += 1
            continue
        v = val(s)
        if v is not None and L is not None:
            # строка: B/B0, затем n значений потока
            block = [v]
            j = i + 1
            while j < len(lines) and len(block) <= n:
                vv = val(lines[j])
                if vv is None:
                    break
                block.append(vv)
                j += 1
            if len(block) == n + 1:
                rows.append((L, block[0], block[1:]))
                i = j
                continue
        i += 1
    return energies, rows


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    t = io.open(SRC, encoding='utf-8', errors='replace').read()
    os.makedirs(OUT, exist_ok=True)
    index = []
    for key, (title, mark) in TABLES.items():
        m0 = re.search(mark, t)
        if not m0:
            print('НЕ НАЙДЕНО:', mark)
            continue
        beg = m0.start()
        m1 = BOUND.search(t, beg + 20)
        nxt = m1.start() if m1 else len(t)
        lines = [l.strip() for l in t[beg:nxt].split('\n')]
        r = parse(lines)
        if not r:
            print('НЕ РАЗОБРАНО:', mark)
            continue
        energies, rows = r
        fn = os.path.join(OUT, key + '.csv')
        with io.open(fn, 'w', encoding='utf-8') as f:
            f.write('L,B_over_B0,' +
                    ','.join('E_%g_MeV' % e for e in energies) + '\n')
            for L, bb, fl in rows:
                f.write('%g,%g,' % (L, bb) +
                        ','.join('%.6g' % x for x in fl) + '\n')
        Ls = sorted(set(r[0] for r in rows))
        index.append({'file': key + '.csv', 'title': title,
                      'energies_MeV': energies, 'rows': len(rows),
                      'L_min': Ls[0], 'L_max': Ls[-1], 'L_count': len(Ls)})
        print('%-6s %-30s строк %4d  L от %.2f до %.2f (%d шт)  энергий %d'
              % (key, title, len(rows), Ls[0], Ls[-1], len(Ls), len(energies)))
    io.open(os.path.join(OUT, 'index.json'), 'w', encoding='utf-8').write(
        json.dumps(index, ensure_ascii=False, indent=1))
    print('\nвсего таблиц:', len(index))


if __name__ == '__main__':
    main()
