# -*- coding: utf-8 -*-
"""Повтор всех сохранённых примеров (Т8): python scripts/replay_all.py [examples/]
Для каждого examples/*.zip вызывается scripts/replay_example.py; в конце — сводка кодов
возврата (0 — воспроизведено, 3 — воспроизведено другой версией кода, 1 — расхождение,
2 — повтор невозможен). Код возврата скрипта — максимум по примерам."""
from __future__ import annotations

import glob
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(folder: str) -> int:
    sys.stdout.reconfigure(encoding='utf-8')
    codes = {}
    for p in sorted(glob.glob(os.path.join(folder, '*.zip'))):
        r = subprocess.run([sys.executable, os.path.join(_ROOT, 'scripts', 'replay_example.py'), p], capture_output=True, text=True, encoding='utf-8')
        out = r.stdout.strip().splitlines()
        codes[os.path.basename(p)] = r.returncode
        tail = next((l for l in out if l.startswith('РЕЗУЛЬТАТ')), (r.stderr.strip().splitlines() or ['?'])[-1])
        print('%-32s код %d  %s' % (os.path.basename(p), r.returncode, tail))
    ru = {0: 'воспроизведено', 1: 'РАСХОЖДЕНИЕ', 2: 'повтор невозможен', 3: 'воспроизведено другой версией кода'}
    print('итого:', ', '.join('%s — %d' % (ru.get(c, c), sum(1 for v in codes.values() if v == c)) for c in sorted(set(codes.values()))))
    return max(codes.values()) if codes else 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(_ROOT, 'examples')))
