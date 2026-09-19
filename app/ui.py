# -*- coding: utf-8 -*-
"""Оформление экрана: стили, плашки статусов, панель вердикта, карточки окон, полоса состояния,
словари перевода идентификаторов модулей в подписи для пользователя.

Только разметка. Ничего не считает и не решает: все значения приходят из снимка расчёта.
Правило оформления — три происхождения (наблюдение, внешний прогноз, наш расчёт) и
четыре состояния (в порядке, внимание, критично, нет данных) везде одними и теми же цветами.
На оперативном уровне на экране нет идентификаторов кода: методы, типы событий, источники и
ограничения модулей переводятся словарями ниже; неизвестная строка выводится как есть с пометкой.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta

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
.verdict .rule .orig { color:#9aa0a6; font-size:0.84rem; }
.verdict .win { font-size:1.05rem; font-weight:600; }
.verdict ul { margin:6px 0 0 18px; padding:0; }
.verdict li { margin:2px 0; font-size:0.93rem; }
.verdict li.more { color:var(--muted); list-style:none; margin-left:-18px; font-size:0.86rem; }
.verdict .plan { margin:8px 0 2px 0; padding:6px 10px; border-radius:8px; background:var(--calc-bg); color:var(--calc);
                 font-size:0.88rem; }
.verdict .policy { margin:8px 0 0 0; font-size:0.84rem; color:var(--muted); }
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
.covwhy { margin-top:4px; font-size:0.78rem; color:var(--muted); }
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

# --- словари перевода идентификаторов модулей (О5: без английских идентификаторов на экране) ---
METHOD_RU = {'sgp4': 'SGP4 по TLE', 'oem_interp': 'OEM NASA/JSC (интерполяция)'}
STRICT_RU = {'strict': 'строгая', 'declared_reconstruction': 'объявленная реконструкция',
             'reconstruction': 'реконструкция', 'unavailable': 'недоступна'}
EVENT_KIND_RU = {'SEP': 'протонное событие', 'GST': 'геомагнитная буря', 'FLR': 'вспышка', 'CME': 'выброс массы',
                 'CME_ARRIVAL': 'прогноз прихода выброса', 'IPS': 'межпланетный удар', 'HSS': 'высокоскоростной поток',
                 'RBE': 'усиление радиационного пояса', 'MPC': 'пересечение магнитопаузы', 'GST_KP': 'буря (Kp)'}
SOURCE_RU = {'orbit': 'орбита', 'noaa_swpc_3day_forecast': 'трёхсуточный прогноз NOAA SWPC', 'noaa_swpc_goes': 'GOES ≥10 МэВ (NOAA SWPC)', 'gfz_kp': 'Kp (GFZ)',
             'ost1044_belts': 'таблицы ОСТ 134-1044-2007 (захваченные протоны)',
             'ecss_grun': 'модель метеороидов ECSS/Grün', '_layers': 'слои программы',
             'donki_archive': 'архив DONKI: события, уведомления, прогоны ENLIL',
             'noaa_forecast_kp_forecast': 'прогноз Kp NOAA (выпуск до отсечки)',
             'noaa_forecast_s1_prob_daily': 'прогноз NOAA: вероятность S1+ за сутки',
             'noaa_forecast_proton_prob_daily': 'прогноз NOAA: вероятность протонного события за сутки'}
# Ограничения модуля орбиты (A3) — перевод по точному совпадению; неизвестная строка выводится
# как есть с пометкой «текст модуля орбиты».
LIMIT_RU = {
    'TLE age limit is an engineering guard, not a position-error guarantee; manoeuvres are not predicted.':
        'Предел возраста TLE — инженерное ограничение, не оценка ошибки положения; манёвры не предсказываются.',
    'IGRF is the internal main field; no storm-time external field is modelled.':
        'IGRF — внутреннее главное поле; внешнее поле бури не моделируется.',
    'L, B/B0 and vertical cutoff use a centred tilted dipole; not traced McIlwain L or directional storm-time rigidity.':
        'L, B/B_0 и вертикальное обрезание — центральный наклонный диполь, не трассированная L Мак-Илвейна '
        'и не направленная жёсткость обрезания во время бури.',
    'Full IGRF |B| is separate from dipole B/B0; they must not be mixed to infer an IGRF equatorial field.':
        'Полное |B| по IGRF и дипольное B/B_0 — разные величины; смешивать их для оценки экваториального поля нельзя.',
    'SAA flag is the configured |B| threshold proxy, not an official region boundary.':
        'Признак аномалии — порог |B| из настроек, не официальная граница области.',
    'OEM timestamps of creation/modification do not prove historical public availability.':
        'Времена создания и изменения OEM не доказывают, что файл был публично доступен в тот момент.',
}
# Правило предпочтения — пять шагов, понятных без внутреннего договора (DEMO-6, O5-14).
RULE_RU = [
    ('п.1', 'шаг 1 из 5, охват: у обязательной линии нет данных — рекомендация невозможна'),
    ('п.2: все окна', 'шаг 2 из 5, условия: у каждого окна есть условие проверки, автоматический выбор не делается — решение за аналитиком'),
    ('п.2: единственное', 'шаг 2 из 5, условия: только одно окно без условий проверки'),
    ('п.3–4', 'шаги 3–4 из 5, сравнение и сведение: меньше минут в аномалии, линия метеороидов не противоречит'),
    ('п.4', 'шаг 4 из 5, сведение: механизмы указывают на разные окна — компромисс без победителя'),
    ('п.5', 'шаг 5 из 5, допуск равнозначности'),
]
BOOL_RU = {True: 'да', False: 'нет', None: '—'}


def esc(s) -> str:
    return html.escape(str(s), quote=False)


def pill(text, kind='none') -> str:
    return '<span class="pill pill-%s">%s</span>' % (kind, esc(text))


_SUP = {'0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹',
        '-': '⁻', '+': ''}
_POW_RE = re.compile(r'10\^([+-]?\d+)')
_EXP_RE = re.compile(r'(?<![\w.])(\d+(?:[.,]\d+)?)[eE]([+-]?\d+)(?![\w])')
# дробь с точкой, но не дата (01.05.2024), не «10.05 12:00» и не номер версии (v3.1)
_FRAC_RE = re.compile(r'(?<![\d.A-Za-zА-Яа-я])(\d+)\.(\d+)(?=\s*(?:[А-Яа-я%·)\],;]|$))')


def sup(text) -> str:
    """Степени надстрочными цифрами: «3,36·10^6» → «3,36·10⁶», «5,64·10^-7» → «5,64·10⁻⁷» (U4)."""
    return _POW_RE.sub(lambda m: '10' + ''.join(_SUP.get(c, c) for c in m.group(1)), str(text))


def frac_ru(text) -> str:
    """Единый формат чисел в готовых строках: дробь с запятой, порядок надстрочными цифрами.
    Даты вида 01.05.2024 и 10.05 12:00 не трогаются."""
    s = _EXP_RE.sub(lambda m: '%s·10^%s' % (m.group(1).replace('.', ','), int(m.group(2))), str(text))
    s = _FRAC_RE.sub(lambda m: '%s,%s' % (m.group(1), m.group(2)), s)
    return sup(s)


def fmt(v, unit='') -> str:
    """Число по-русски: запятая, порядок надстрочными цифрами (10⁶, 10⁻⁷);
    безразмерная единица «1» не печатается."""
    if v is None:
        return '—'
    if isinstance(v, float):
        if v == 0:
            s = '0'
        elif abs(v) >= 1e5 or abs(v) < 1e-2:
            m, e = ('%.2e' % v).split('e')
            s = sup('%s·10^%d' % (m.replace('.', ','), int(e)))
        elif abs(v) >= 100:
            s = '%.0f' % v
        else:
            s = ('%.2f' % v).rstrip('0').rstrip('.').replace('.', ',')
    else:
        s = str(v)
    return s + (' ' + unit if unit and unit not in ('1',) else '')


def spread_offsets(offsets, search_min: int, step: int = 30) -> list[int]:
    """Развести совпавшие сдвиги окон внутри периода поиска (U1): последнее окно — в конец
    периода, предыдущие на шаг раньше. Раскладка ползунков, а не оценка риска: экран из-за
    сжатия периода не должен останавливаться. Различные сдвиги возвращаются как есть."""
    out = sorted(int(o) for o in offsets)
    n = len(out)
    if n == 0 or len(set(out)) == n:
        return out
    if search_min >= (n - 1) * step:
        return [int(search_min) - (n - 1 - i) * step for i in range(n)]
    if search_min >= n - 1:                      # период короче шага: разводим равномерно
        k = int(search_min) // (n - 1)
        return [i * k for i in range(n)]
    return out                                    # развести нечем — решает проверка запроса


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


def rule_ru(rule_applied: str) -> str:
    """«п.5: разница 48 мин меньше допуска 49 мин» → «шаг 5 из 5, допуск равнозначности: разница …»."""
    for prefix, text in RULE_RU:
        if rule_applied.startswith(prefix):
            if prefix == 'п.5':
                return text + ' — ' + rule_applied.partition(': ')[2] + ' (разброс минут в аномалии на сетке порогов); окна неразличимы'
            if prefix == 'п.3–4' and 'частичное' in rule_applied:
                return text + '; покрытие частичное — объявлено'
            return text
    return rule_applied


def _timeout_s() -> str:
    """Тайм-аут живого запроса из config/settings.toml — чтобы «за 6 с» не было зашито в текст."""
    try:
        from vkd.config import section as _sec
        return fmt(float(_sec('sources').get('timeout_s', 6)))
    except Exception:            # noqa: BLE001 — настройка недоступна: печатаем общую фразу
        return '6'


# Имена отказов сети из ответа источника — словами пользователя (U2). Значение с «%s» получает тайм-аут.
NET_ERR_RU = {
    'ReadTimeout': 'сервер не ответил за %s с',
    'ConnectTimeout': 'соединение не установлено за %s с',
    'Timeout': 'сервер не ответил за %s с',
    'ConnectionError': 'соединение с сервером не установлено',
    'ConnectionResetError': 'соединение разорвано сервером',
    'NewConnectionError': 'соединение с сервером не установлено',
    'HTTPError': 'сервер вернул ошибку',
    'SSLError': 'защищённое соединение не установлено',
    'TooManyRedirects': 'сервер перенаправляет запрос без конца',
    'JSONDecodeError': 'ответ не разобран',
    'ValueError': 'ответ не разобран',
    'KeyError': 'в ответе нет нужного поля',
    'RequestException': 'запрос к серверу не выполнен',
}
_NET_ALT = '|'.join(sorted(NET_ERR_RU, key=len, reverse=True))
_MODULE_RE = re.compile(r'\s*\((?:vkd|experiments|app|scripts|tests)\.[\w.]+\)')
_MODULE_BARE_RE = re.compile(r'(?:vkd|experiments|app|scripts|tests)\.[\w.]+')
_HASH_RE = re.compile(r'[;,]?\s*(?:sha256|SHA-256)\s+[0-9a-fA-F]+…?', re.I)
_FILE_RE = re.compile(r'[;,]?\s*файл\s+[^\s;,]+')
_RECORD_RE = re.compile(r'[;,]?\s*запис[ьи]\s+(?=[A-Za-z0-9])[\w:#.\-]*')   # «запись donki_msg#…», не «записи не указан»
_RECORDS_TAIL_RE = re.compile(r'[;,]?\s*запис[ьи]:\s.*$', re.S)     # перечень записей — ниже, ссылками
_SRCID_RE = re.compile(r'\s*\([a-z][a-z0-9]*_[a-z0-9_]+\)')          # (celestrak_gp), (nasa_jsc_oem)
_CONTRACT_RE = re.compile(r'CONTRACT\.md(\s+v[\d.]+)?')
_RELEASE_RE = re.compile(r'выпуск\s+[\w\-]*[A-Za-z][\w\-]*\s+от\b')
_CHECK_RE = re.compile(r'[;,]?\s*контроль\s+[^;]+воспроизведён')
# обороты слоёв программы, которым на оперативном уровне нужен русский (U2)
PHRASE_RU = [('интеграл по dt', 'интеграл по времени'), ('Table J-6', 'табл. J-6'), ('Rev.1', 'ред. 1'),
             ('в config/settings.toml', 'в настройках сервиса'), ('config/settings.toml', 'настройки сервиса'),
             # имена ключей настроек: на профессиональном уровне как есть, на оперативном — по-русски.
             # Ключи всегда стоят после слова «настройка/настройке/настройкой», поэтому заменяем само имя.
             ('sep_valid_hours', 'срока действия уведомления о протонном событии'),
             ('event_valid_hours', 'срока действия записи о буре или приходе выброса')]
_INNER_ID_RE = re.compile(r',\s*[a-z][a-z0-9]*(?:[-_][a-z0-9]+)+(?=\))')
_CLEAN_RE = re.compile(r'\s{2,}')


def net_error_ru(name: str) -> str:
    """«ReadTimeout» → «сервер не ответил за 6 с»; неизвестное имя — общая фраза без кода."""
    t = NET_ERR_RU.get(name)
    if t is None:
        return 'источник не ответил'
    return (t % _timeout_s()) if '%s' in t else t


def status_ru(text, pro: bool = False) -> str:
    """Статус источника для экрана (U2). На профессиональном уровне — как есть, только числа
    приводятся к единому виду. На оперативном — без имён отказов, путей модулей, файлов, хешей
    и идентификаторов записей: их место — профессиональный уровень и выгрузка."""
    s = frac_ru(str(text or '')).replace('из архив ', 'из архива ')
    if pro or not s:
        return s
    s = re.sub(r'([\w.\-]+): (%s)\b' % _NET_ALT, lambda m: '%s — %s' % (m.group(1), net_error_ru(m.group(2))), s)
    s = re.sub(r'\((%s)\)' % _NET_ALT, lambda m: '(%s)' % net_error_ru(m.group(1)), s)
    s = re.sub(r'ответ не разбирается \([^)]*\)', 'ответ источника не разобран', s)
    s = s.replace(', HTTP 200', '').replace('HTTP 200', 'получено')
    s = re.sub(r'\s*\(ответ сохранён[^)]*\)', '', s)
    s = _MODULE_RE.sub('', s)
    s = _MODULE_BARE_RE.sub('расчёт сервиса', s)
    s = _HASH_RE.sub('', s)
    s = _FILE_RE.sub('', s)
    s = _RECORDS_TAIL_RE.sub('', s)
    s = _RECORD_RE.sub('', s)
    s = _SRCID_RE.sub('', s)
    s = _CONTRACT_RE.sub('договор команды,', s)
    s = _RELEASE_RE.sub('выпуск от', s)
    s = _CHECK_RE.sub('', s)
    s = _INNER_ID_RE.sub('', s)
    for a, b in PHRASE_RU:
        s = s.replace(a, b)
    s = re.sub(r'\s*\(\s*[;,]?\s*\)', '', s)        # скобки, опустевшие после удаления записи
    s = re.sub(r'[;,]\s*(?=[;,])', '', s)
    s = re.sub(r'договор команды,\s*,', 'договор команды,', s)
    s = _CLEAN_RE.sub(' ', s).strip()
    return s.rstrip(' ;,')


def dedup_clauses(text) -> str:
    """Повторяющиеся через «;» части одной подписи показываем один раз (U5).
    Слой объяснений собирает источник по каждому фактору, и одна и та же фраза о траектории
    попадает в карточку дважды."""
    parts, seen, out = str(text or '').split('; '), set(), []
    for p in parts:
        k = p.strip().rstrip('.').lower()
        if k and k in seen:
            continue
        seen.add(k)
        out.append(p)
    return '; '.join(out)


def source_short(v: dict) -> tuple[str, str]:
    """Короткий статус источника для полосы состояния: (текст, kind)."""
    st_ = (v.get('status') or '').lower()
    if 'исключён' in st_:
        return 'исключён пользователем', 'crit'
    if v.get('live_ok') is False and v.get('from_cache'):
        age = v.get('age_min')
        return ('кеш, давность %d мин' % round(age)) if age is not None else 'кеш', 'warn'
    if 'данных нет' in st_ or 'не разбирается' in st_:
        return 'нет ответа и кеша — данных нет', 'crit'
    if v.get('live_ok'):
        return 'живой запрос', 'ok'
    return status_ru(v.get('status') or 'нет данных'), 'none'


def source_issues(src: dict, th, mode: str, kp_excluded_hist: bool = False, tle_fetch: str | None = None,
                  pro: bool = False) -> list[str]:
    """Проблемы источников для одного st.warning под полосой состояния (О5-2): исключён, кеш, устарел.
    Ничего не решает — переводит статусы снимка в предложения для пользователя."""
    out = []
    if mode == 'live':
        for sid, name in (('noaa_swpc_goes', 'GOES ≥10 МэВ (NOAA SWPC)'), ('gfz_kp', 'Kp (GFZ)')):
            v = src.get(sid) or {}
            st_ = (v.get('status') or '').lower()
            if 'исключён' in st_:
                out.append('%s: исключён пользователем — %s' % (
                    name, 'обязательная линия без покрытия, рекомендации не будет' if sid == 'noaa_swpc_goes'
                    else 'условие по наблюдению Kp не проверяется, покрытие объявлено'))
            elif v.get('live_ok') is False and v.get('from_cache'):
                age = v.get('age_min')
                out.append('%s: живого ответа нет, взят кеш%s — покрытие частичное, объявлено'
                           % (name, (', давность %d мин' % round(age)) if age is not None else ''))
            elif v.get('live_ok') is False:
                out.append('%s: %s' % (name, status_ru(v.get('status') or 'нет данных', pro)))
            elif sid == 'noaa_swpc_goes' and v.get('age_min') is not None and th is not None \
                    and v['age_min'] > th.goes_max_age_min:
                out.append('GOES ≥10 МэВ: наблюдение устарело (%d мин при допустимых %.0f) — для будущих участков '
                           'окна покрытие частичное' % (round(v['age_min']), th.goes_max_age_min))
        o = src.get('orbit') or {}
        if o.get('live_ok') is False and o.get('from_cache'):
            out.append('TLE: живого ответа нет — орбита построена по %s (%s)' % (
                'снимку репозитория' if 'снимок' in (tle_fetch or '') else 'кешу', tle_origin(tle_fetch)))
        if o.get('age_h') is not None and o['age_h'] > 24:
            out.append('TLE: эпоха старше суток (%.0f ч) — точность положения снижается, предел %s сут задан порогом'
                       % (o['age_h'], fmt(th.tle_max_age_days) if th is not None else '—'))
    else:
        if kp_excluded_hist:
            out.append('Kp: исключён пользователем из архива (проверка отказа) — условие по наблюдению Kp не проверяется')
        o = src.get('orbit') or {}
        if o.get('strictness') == 'declared_reconstruction':
            out.append('Орбита: OEM NASA/JSC создан до отсечки, но его публичная доступность в тот момент не доказана — '
                       'объявленная реконструкция')
    return out


def _win_num(windows_ru: dict, t) -> str:
    return windows_ru.get(t, '')


def _hhmm_to_window(assessments, hhmm: str):
    for i, a in enumerate(assessments or []):
        if a.window.start_utc.strftime('%H:%MZ') == hhmm:
            return i + 1
    return None


def _wins_ru(nums: list[int]) -> str:
    nums = sorted(set(nums))
    if len(nums) == 1:
        return 'окно %d' % nums[0]
    return 'окна ' + ' и '.join(str(n) for n in nums) if len(nums) == 2 else 'окна ' + ', '.join(str(n) for n in nums)


def verdict_reasons(rec, assessments, mech_ru: dict | None = None, max_items: int = 6) -> tuple[list[str], int]:
    """Список условий панели вердикта с указанием окна и без дубликатов (О3-3).
    Возвращает (строки, сколько не показано)."""
    mech_ru = mech_ru or MECH_RU
    out: list[str] = []
    if rec.verdict == 'all_need_check' and assessments:
        by: dict[str, list[int]] = {}
        for i, a in enumerate(assessments):
            for m in a.mechanisms:
                for r in m.needs_check_reasons:
                    by.setdefault(_short_reason(r), []).append(i + 1)
        for text, nums in by.items():
            out.append('%s: %s' % (_wins_ru(nums), text))
    else:
        seen = set()
        per = dict(getattr(rec, 'per_mechanism_comparison', None) or {})
        per_txt = {v: k for k, v in per.items()}
        for r in rec.reasons:
            if r in seen:
                continue
            seen.add(r)
            txt = _short_reason(r) if ' DONKI — ' in r else r
            mech = per_txt.get(r)
            if mech:
                m = re.match(r'^(\d\d:\d\dZ): (.*)$', txt)
                if m:
                    n = _hhmm_to_window(assessments, m.group(1))
                    txt = '%s — %s: %s' % (mech_ru.get(mech, mech), ('окно %d (%s)' % (n, m.group(1))) if n else m.group(1), m.group(2))
                else:
                    txt = '%s: %s' % (mech_ru.get(mech, mech), txt)
            out.append(txt)
    if len(out) > max_items:
        return out[:max_items], len(out) - max_items
    return out, 0


def robustness_pill(rec, rob: dict) -> str:
    """О7: «устойчив» печатается только когда выбор есть (DEMO-17)."""
    grid = rob.get('preferred_by_grid') or {}
    vals = set(grid.values())
    if rec.preferred is None and grid and vals == {None}:
        return pill('сетка порогов исход не меняет: везде без автовыбора', 'none')
    if rob.get('stable'):
        return pill('выбор устойчив на сетке порогов' if rec.preferred is not None else 'ранжирование устойчиво на сетке порогов', 'ok')
    return pill('выбор меняется на сетке порогов' if rec.preferred is not None else 'ранжирование меняется на сетке порогов', 'warn')


def verdict_panel(rec, S: dict, windows_ru: dict, assessments=None, pro: bool = False,
                  plan_change: str | None = None, missing_ru=None, policy_short: str | None = None) -> str:
    v = rec.verdict
    title = VERDICT_TITLE.get(v, v)
    rule = 'Правило: ' + esc(rule_ru(rec.rule_applied))
    if pro:
        rule += ' <span class="orig">(%s)</span>' % esc(rec.rule_applied)
    lines = ['<div class="verdict v-%s">' % v, '<h2>%s</h2>' % esc(title), '<div class="rule">%s</div>' % rule]
    if rec.preferred is not None:
        lines.append('<div class="win">Окно %s — %s</div>' % (_win_num(windows_ru, rec.preferred.start_utc), esc(win_span(rec.preferred))))
    bullets, more = verdict_reasons(rec, assessments)
    missing = list(missing_ru) if missing_ru is not None else list(rec.missing)
    bullets += ['Чего не хватает: ' + x for x in missing]
    if bullets:
        lis = ''.join('<li>%s</li>' % esc(frac_ru(b)) for b in bullets)
        if more:
            lis += '<li class="more">… ещё %d, см. карточки окон и вкладку «Окна и факторы»</li>' % more
        lines.append('<ul>' + lis + '</ul>')
    if plan_change:
        lines.append('<div class="plan">%s</div>' % esc(plan_change))
    if policy_short:
        lines.append('<div class="policy">%s</div>' % esc(policy_short))
    rob = S.get('robustness') or {}
    lines.append('<div class="cov">%s %s Охват: %s. Не учтено: %s.</div>' % (
        robustness_pill(rec, rob),
        pill('сценарий «что если»', 'warn') if S.get('is_simulated') else '',
        esc(', '.join(S.get('coverage_declared', []))), esc(', '.join(S.get('coverage_missing', [])))))
    lines.append('</div>')
    return ''.join(lines)


def win_span(w) -> str:
    """«10.05 22:00 — 11.05 00:00 UTC, 120 мин»: дата конца печатается, если окно переходит через полночь."""
    end = w.start_utc + timedelta(minutes=w.duration_min)
    end_s = end.strftime('%d.%m %H:%M') if end.date() != w.start_utc.date() else end.strftime('%H:%M')
    return '%s — %s UTC, %d мин' % (w.start_utc.strftime('%d.%m %H:%M'), end_s, w.duration_min)


_win_span = win_span     # прежнее имя


def _cov_reason(f) -> str | None:
    """Причина неполного покрытия одного фактора — одной фразой для карточки окна (DEMO-11)."""
    note = f.limits_note or ''
    name = f.name
    m = re.search(r'доля точек с моделью (\d+) %', note)
    if name.startswith('флюенс') and m:
        return 'флюенс: модель ОСТ есть для %s %% точек трассы' % m.group(1)
    if name.startswith('поток протонов GOES'):
        if 'наблюдений GOES нет' in note and 'DONKI о протонных' in note:
            return 'GOES: наблюдений за 2024 нет, канал по датированным уведомлениям DONKI'
        if 'наблюдений GOES нет' in note and 'каталог' in note:
            return 'GOES: наблюдений за 2024 нет; каталог DONKI покрывает период, событий не объявлено'
        m2 = re.search(r'давность (\d+) мин', note)
        if 'устарело' in note and m2:
            return 'GOES: наблюдение устарело (давность %s мин) — для будущих участков' % m2.group(1)
        if 'нет данных' in note:
            return 'GOES: данных нет'
    if name == 'минут в аномалии':
        return 'трасса покрывает окно не полностью'
    m3 = re.search(r'трасса покрывает (\d+) % окна', note)
    if m3:
        return 'метеороиды: трасса покрывает %s %% окна' % m3.group(1)
    m4 = re.search(r'покрытие окна ячейками (\d+) %', note)
    if m4 and name.startswith('прогноз'):
        return 'прогноз Kp: ячейки покрывают %s %% окна' % m4.group(1)
    if m4:
        return '%s: ячейки покрывают %s %% окна' % (name.split(',')[0], m4.group(1))
    first = note.split(';')[0].strip()
    return ('%s: %s' % (name.split(',')[0], first)) if first else None


def coverage_reasons(a) -> list[str]:
    """Причины неполного покрытия по обязательным механизмам окна, без повторов."""
    out, seen = [], set()
    for m in a.mechanisms:
        if not (m.mandatory or m.coverage.value != 'none') or m.coverage.value == 'full':
            continue
        for f in m.factors:
            if f.coverage.value == 'full':
                continue
            r = _cov_reason(f)
            if r and r not in seen:
                seen.add(r)
                out.append(r)
    return out


def fluence_label(flu) -> str:
    """Подпись канала из имени фактора: «флюенс протонов ≥30 МэВ, част./см²» (O5-10, DEMO-20)."""
    if flu is None:
        return 'флюенс протонов, част./см²'
    return flu.name.replace('захваченных ', '') + ', ' + (flu.unit or 'част./см²')


def window_card(i: int, a, best: bool, mode: str) -> str:
    """Карточка окна: заголовок, состояние, ключевые величины, условия, покрытие по механизмам с причиной."""
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
            (fluence_label(flu), fmt(flu.value if flu else None), False),
            ('метеороиды, попаданий на 1 м²', fmt(mm.value if mm else None), False)]
    if mode == 'live':
        rows.append(('GOES ≥10 МэВ, pfu', fmt(goes.value if goes else None), False))
    else:
        rows.append(('прогноз Kp NOAA, макс. в окне', fmt(kpf.value if kpf else None), False))
    kv = ''.join('<div class="k">%s</div><div class="v%s">%s</div>' % (esc(k), ' big' if big else '', esc(v)) for k, v, big in rows)
    conds = ''.join('<div class="cond%s">%s</div>' % (' crit' if 'приоритетное' in r else '', esc(frac_ru(_short_reason(r))))
                    for r in reasons[:4])
    if len(reasons) > 4:
        conds += '<div class="small">… ещё %d</div>' % (len(reasons) - 4)
    if not reasons:
        conds = '<div class="cond ok">Условий проверки нет по данным до отсечки</div>' if mode == 'history_forecast' else '<div class="cond ok">Условий проверки нет</div>'
    cov = ' '.join(pill('%s: %s' % (MECH_RU.get(m.mechanism_id, m.mechanism_id), COV_RU[m.coverage.value]), COV_KIND[m.coverage.value])
                   for m in a.mechanisms if m.mandatory or m.coverage.value != 'none')
    why = coverage_reasons(a)
    why_html = ('<div class="covwhy">почему неполное — %s</div>' % esc(frac_ru('; '.join(why)))) if why else ''
    return ('<div class="%s"><div class="wh"><div><div class="wt">Окно %d%s</div><div class="wtime">%s</div></div>%s</div>'
            '<div class="kv">%s</div>%s<div class="cov">покрытие: %s</div>%s</div>'
            % (cls, i + 1, ' · предпочтительное' if best else '', esc(win_span(a.window)), pill(*state), kv, conds, cov, why_html))


def _short_reason(r: str) -> str:
    """Короткая форма условия для карточки: до двоеточия плюс первая часть после."""
    head_, _, tail = r.partition(': ')
    tail = tail.split(';')[0].strip()
    return head_ + (': ' + tail if tail else '')


def short_reason(r: str) -> str:
    return _short_reason(r)


def kind_pill(kind_value: str) -> str:
    t, k = KIND_PILL.get(kind_value, (kind_value, 'none'))
    return pill(t, k)


def limit_ru(s: str) -> str:
    """Ограничение модуля орбиты по-русски; неизвестное — как есть с пометкой."""
    if s in LIMIT_RU:
        return LIMIT_RU[s]
    if re.search(r'[А-Яа-я]', s):
        return s
    return s + ' (текст модуля орбиты)'


def event_kind_ru(k: str) -> str:
    return EVENT_KIND_RU.get((k or '').upper(), k)


def source_name_ru(sid: str) -> str:
    return SOURCE_RU.get(sid, sid)


def tle_origin(tle_fetch_status: str | None) -> str:
    """«получено живьём с celestrak.org» / «кеш, давность N мин» / «снимок репозитория, давность N мин»."""
    s = tle_fetch_status or ''
    m = re.search(r'живьём с ([\w.\-]+)', s)
    if m:
        return 'живьём с ' + m.group(1)
    if 'снимок репозитория' in s:
        m2 = re.search(r'давность (\d+) мин', s)
        return 'снимок репозитория' + (', давность %s ч' % round(int(m2.group(1)) / 60) if m2 else '')
    if 'кеш' in s:
        m2 = re.search(r'давность (\d+) мин', s)
        return 'кеш' + (', давность %s мин' % m2.group(1) if m2 else '')
    if 'воспроизведение' in s:
        return 'из сохранённого расчёта'
    return s or '—'


def grid_cell_ru(v, windows_ru_iso: dict) -> str:
    """Ячейка таблицы устойчивости: «Окно 1 (23:21Z)» или объяснение отсутствия выбора (O5-6)."""
    if not v:
        return 'нет предпочтительного (равнозначны или отказ)'
    try:
        t = datetime.fromisoformat(v)
    except (TypeError, ValueError):
        return str(v)
    n = windows_ru_iso.get(t)
    return ('Окно %s (%s)' % (n, t.strftime('%H:%MZ'))) if n else t.strftime('%d.%m %H:%MZ')
