# -*- coding: utf-8 -*-
"""Русская запись чисел для объяснений и текстов правила: запятая, ·10^n, единица после числа.
Тот же формат, что у экрана (app/ui.fmt), но без зависимости ядра от интерфейса."""
from __future__ import annotations


def fmt_ru(v, unit: str = '') -> str:
    if v is None:
        return '—'
    if isinstance(v, float):
        if v == 0:
            s = '0'
        elif abs(v) >= 1e5 or abs(v) < 1e-2:
            m, e = ('%.2e' % v).split('e')
            s = '%s·10^%d' % (m.replace('.', ','), int(e))
        elif abs(v) >= 100:
            s = '%.0f' % v
        else:
            s = ('%.2f' % v).rstrip('0').rstrip('.').replace('.', ',')
    else:
        s = str(v)
    return s + (' ' + unit if unit else '')
