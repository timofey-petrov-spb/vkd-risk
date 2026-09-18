# -*- coding: utf-8 -*-
"""Оформление экрана: стили, плашки статусов, панель вердикта, карточки окон, полоса состояния.

Только разметка. Ничего не считает и не решает: все значения приходят из снимка расчёта.
Правило оформления — три происхождения (наблюдение, внешний прогноз, наш расчёт) и
четыре состояния (в порядке, внимание, критично, нет данных) везде одними и теми же цветами.
"""
from __future__ import annotations

import html
from datetime import timedelta

CSS = """
<style>
:root { --ink:#1a1f2b; --muted:#6b7280; --line:#e5e7eb; --bg:#ffffff; --soft:#f6f7f9;
        --ok:#1e8449; --ok-bg:#eafaf1; --warn:#b9770e; --warn-bg:#fef5e7; --crit:#c0392b; --crit-bg:#fdedec;
        --none:#566573; --none-bg:#f2f3f4; --calc:#1f4e79; --calc-bg:#eaf2fb; --obs:#1e8449; --fc:#b9770e; }
html, body, [class*="css"] { font-family: "Segoe UI", Inter, Roboto, Arial, sans-serif; color: var(--ink); }
.block-container { padding-top: 3.2rem; padding-bottom: 2rem; max-width: 1400px; }
h1, h2, h3 { letter-spacing: -0.01em; }
.vk-head { display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; margin-bottom:2px; }
.vk-title { font-size:1.55rem; font-weight:700; margin:0; }
.vk-sub { color:var(--muted); font-size:0.92rem; }
.pill { display:inline-block; padding:2px 9px; border-radius:999px; font-size:0.78rem; font-weight:600;
        line-height:1.5; border:1px solid transparent; margin:1px 4px 1px 0; white-space:nowrap; }
.pill-ok { background:var(--ok-bg); color:var(--ok); border-color:#bfe8cf; }
.pill-warn { background:var(--warn-bg); color:var(--warn); border-color:#f3dcb0; }
.pill-crit { background:var(--crit-bg); color:var(--crit); border-color:#f2c0bb; }
.pill-none { background:var(--none-bg); color:var(--none); border-color:#d7dbdf; }
.pill-calc { background:var(--calc-bg); color:var(--calc); border-color:#c4d8ee; }
.pill-obs { background:#eafaf1; color:#1e8449; border-color:#bfe8cf; }
.pill-fc { background:#fef5e7; color:#b9770e; border-color:#f3dcb0; }
.strip { display:flex; gap:8px; flex-wrap:wrap; align-items:center; padding:8px 12px; background:var(--soft);
         border:1px solid var(--line); border-radius:10px; margin:6px 0 14px 0; font-size:0.85rem; }
.strip b { font-weight:600; }
.strip .sep { color:#c9cdd3; }
.verdict { border-radius:12px; padding:16px 20px; border:1px solid var(--line); border-left:8px solid var(--none);
           background:var(--bg); margin:4px 0 14px 0; }
.verdict h2 { margin:0 0 4px 0; font-size:1.35rem; }
.verdict .rule { color:var(--muted); font-size:0.92rem; margin-bottom:8px; }
.verdict .win { font-size:1.05rem; font-weight:600; }
.verdict ul { margin:6px 0 0 18px; padding:0; }
.verdict li { margin:2px 0; font-size:0.93rem; }
.v-preferred { border-left-color:var(--ok); background:linear-gradient(90deg, var(--ok-bg) 0, #fff 60%); }
.v-equivalent { border-left-color:var(--none); background:linear-gradient(90deg, var(--none-bg) 0, #fff 60%); }
.v-trade_off { border-left-color:var(--calc); background:linear-gradient(90deg, var(--calc-bg) 0, #fff 60%); }
.v-all_need_check { border-left-color:var(--warn); background:linear-gradient(90deg, var(--warn-bg) 0, #fff 60%); }
.v-insufficient { border-left-color:var(--crit); background:linear-gradient(90deg, var(--crit-bg) 0, #fff 60%); }
.wcard { border:1px solid var(--line); border-radius:12px; padding:14px 16px 12px 16px; background:#fff; height:100%; }
.wcard.best { border:2px solid var(--ok); box-shadow:0 0 0 3px var(--ok-bg); }
.wcard.flag { border-color:#f3dcb0; }
.wcard.crit { border-color:#f2c0bb; }
.wcard .wh { display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:6px; }
.wcard .wt { font-weight:700; font-size:1.02rem; }
.wcard .wtime { color:var(--muted); font-size:0.86rem; }
.kv { display:grid; grid-template-columns: 1fr auto; gap:3px 10px; font-size:0.9rem; margin:8px 0 6px 0; }
.kv .k { color:var(--muted); }
.kv .v { font-weight:600; text-align:right; font-variant-numeric: tabular-nums; }
.kv .v.big { font-size:1.35rem; }
.cond { font-size:0.85rem; margin:4px 0 0 0; padding:6px 8px; border-radius:8px; background:var(--warn-bg); color:#7d5a12; }
.cond.crit { background:var(--crit-bg); color:#922b21; }
.cond.ok { background:var(--ok-bg); color:#196f3d; }
.cov { margin-top:8px; font-size:0.78rem; color:var(--muted); }
.legend { font-size:0.82rem; color:var(--muted); margin:2px 0 10px 0; }
.small { font-size:0.84rem; color:var(--muted); }
div[data-testid="stMetric"] { background:var(--soft); border:1px solid var(--line); border-radius:10px; padding:8px 12px; }
div[data-testid="stExpander"] details { border-radius:10px; }
footer { visibility:hidden; }
</style>
"""

VERDICT_TITLE = {
    'preferred': 'Есть предпочтительное окно',
    'equivalent': 'Окна равнозначны по учтённым механизмам',
    'trade_off': 'Компромисс: механизмы указывают на разные окна',
    'all_need_check': 'Все окна требуют проверки аналитиком',
    'insufficient': 'Оснований для рекомендации недостаточно',
}
COV_RU = {'full': 'полное', 'partial': 'частичное', 'none': 'нет'}
COV_KIND = {'full': 'ok', 'partial': 'warn', 'none': 'crit'}
MECH_RU = {'spaceweather': 'космопогода', 'mmod_stat': 'метеороиды', 'conjunctions': 'сближения'}
KIND_PILL = {'observation': ('наблюдение', 'obs'), 'external_forecast': ('внешний прогноз', 'fc'), 'own_calculation': ('наш расчёт', 'calc')}


def esc(s) -> str:
    return html.escape(str(s), quote=False)


def pill(text, kind='none') -> str:
    return '<span class="pill pill-%s">%s</span>' % (kind, esc(text))


def fmt(v, unit='') -> str:
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


def head(title: str, sub: str) -> str:
    return '<div class="vk-head"><div class="vk-title">%s</div><div class="vk-sub">%s</div></div>' % (esc(title), esc(sub))


def strip(items) -> str:
    """items: список (подпись, значение, kind|None). kind задаёт плашку; None — обычный текст."""
    parts = []
    for label, value, kind in items:
        if kind:
            parts.append('<span><b>%s</b> %s</span>' % (esc(label), pill(value, kind)))
        else:
            parts.append('<span><b>%s</b> %s</span>' % (esc(label), esc(value)))
    return '<div class="strip">' + '<span class="sep">·</span>'.join(parts) + '</div>'


def verdict_panel(rec, S: dict, windows_ru: dict) -> str:
    v = rec.verdict
    title = VERDICT_TITLE.get(v, v)
    lines = ['<div class="verdict v-%s">' % v, '<h2>%s</h2>' % esc(title),
             '<div class="rule">Правило: %s</div>' % esc(rec.rule_applied)]
    if rec.preferred is not None:
        lines.append('<div class="win">Окно %s — %s</div>' % (windows_ru.get(rec.preferred.start_utc, ''), esc(_win_span(rec.preferred))))
    bullets = list(rec.reasons)
    if rec.missing:
        bullets += ['Чего не хватает: ' + x for x in rec.missing]
    if bullets:
        lines.append('<ul>' + ''.join('<li>%s</li>' % esc(b) for b in bullets[:6]) + '</ul>')
    rob = S.get('robustness') or {}
    lines.append('<div class="cov">%s %s Охват: %s. Не учтено: %s.</div>' % (
        pill('выбор устойчив на сетке порогов' if rob.get('stable') else 'выбор меняется на сетке порогов', 'ok' if rob.get('stable') else 'warn'),
        pill('сценарий «что если»', 'warn') if S.get('is_simulated') else '',
        esc(', '.join(S.get('coverage_declared', []))), esc(', '.join(S.get('coverage_missing', [])))))
    lines.append('</div>')
    return ''.join(lines)


def _win_span(w) -> str:
    end = w.start_utc + timedelta(minutes=w.duration_min)
    return '%s — %s UTC, %d мин' % (w.start_utc.strftime('%d.%m %H:%M'), end.strftime('%H:%M'), w.duration_min)


def window_card(i: int, a, best: bool, mode: str) -> str:
    """Карточка окна: заголовок, состояние, ключевые величины, условия, покрытие по механизмам."""
    f = {x.name: x for m in a.mechanisms for x in m.factors}
    reasons = [r for m in a.mechanisms for r in m.needs_check_reasons]
    crit = any('приоритетное' in r for r in reasons)
    state = ('приоритетная проверка', 'crit') if crit else (('условие проверки', 'warn') if reasons else ('без условий', 'ok'))
    cls = 'wcard' + (' best' if best else '') + (' crit' if crit else (' flag' if reasons else ''))
    saa = f.get('минут в аномалии')
    flu = next((x for n, x in f.items() if n.startswith('флюенс')), None)
    mm = f.get('ожидаемое число попаданий, пластина 1 м²')
    goes = f.get('поток протонов GOES ≥10 МэВ')
    kpf = f.get('прогноз Kp NOAA, максимум в окне')
    rows = [('минут в аномалии', fmt(saa.value if saa else None, 'мин'), True),
            ('флюенс протонов ≥E, част./см²', fmt(flu.value if flu else None), False),
            ('метеороиды, попаданий на 1 м²', fmt(mm.value if mm else None), False)]
    if mode == 'live':
        rows.append(('GOES ≥10 МэВ, pfu', fmt(goes.value if goes else None), False))
    else:
        rows.append(('прогноз Kp NOAA, макс.', fmt(kpf.value if kpf else None), False))
    kv = ''.join('<div class="k">%s</div><div class="v%s">%s</div>' % (esc(k), ' big' if big else '', esc(v)) for k, v, big in rows)
    conds = ''.join('<div class="cond%s">%s</div>' % (' crit' if 'приоритетное' in r else '', esc(_short_reason(r))) for r in reasons[:4])
    if len(reasons) > 4:
        conds += '<div class="small">… ещё %d</div>' % (len(reasons) - 4)
    if not reasons:
        conds = '<div class="cond ok">Условий проверки нет по данным до отсечки</div>' if mode == 'history_forecast' else '<div class="cond ok">Условий проверки нет</div>'
    cov = ' '.join(pill('%s: %s' % (MECH_RU.get(m.mechanism_id, m.mechanism_id), COV_RU[m.coverage.value]), COV_KIND[m.coverage.value])
                   for m in a.mechanisms if m.mandatory or m.coverage.value != 'none')
    return ('<div class="%s"><div class="wh"><div><div class="wt">Окно %d%s</div><div class="wtime">%s</div></div>%s</div>'
            '<div class="kv">%s</div>%s<div class="cov">покрытие: %s</div></div>'
            % (cls, i + 1, ' · предпочтительное' if best else '', esc(_win_span(a.window)), pill(*state), kv, conds, cov))


def _short_reason(r: str) -> str:
    """Короткая форма условия для карточки: до двоеточия плюс первая часть после."""
    head_, _, tail = r.partition(':')
    tail = tail.split(';')[0].strip()
    return head_ + (': ' + tail if tail else '')


def kind_pill(kind_value: str) -> str:
    t, k = KIND_PILL.get(kind_value, (kind_value, 'none'))
    return pill(t, k)
