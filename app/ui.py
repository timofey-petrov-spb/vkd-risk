# -*- coding: utf-8 -*-
"""Оформление экрана: стили, плашки статусов, панель вердикта, карточки окон, приборная полоса,
словари перевода идентификаторов модулей в подписи для пользователя, реестр источников и
блоки вкладки «Методика».

Только разметка. Ничего не считает и не решает: все значения приходят из снимка расчёта.

Цветовая система — монотонная, академическая (PROPOSAL_A п. 2 в редакции задания):
цвет означает происхождение величины, а не «хорошо/плохо». Четыре тона и один служебный:
  синий  --calc — наш расчёт;
  зелёный --obs — наблюдение (в том числе взятое из кеша: давность объявляется подписью, а не цветом);
  янтарный --fc — внешний прогноз, и только он;
  красный --cond — условие проверки, аномалия, отказ источника;
  серый  --none — данных нет.
Кеш цвет не меняет (R4-28): иначе наблюдение из кеша и внешний прогноз в полосе состояния идут
одним тоном, и различие происхождений, ради которого система и заведена, на экране не читается.
Светофора на экране нет: «условий проверки нет» — серым, а не зелёным; предпочтительное окно —
синим (это наш расчёт), а не зелёным. Ни градиентов, ни эмодзи: на проекторе они дают грязь,
а эмодзи по-разному рисуются в Windows, macOS и на телефоне.

Типографика — одна лестница размеров на весь экран (PROPOSAL_A п. 2.1): заголовок 1,55 rem,
микрозаголовок 0,72 rem прописными, крупное число 1,35 rem, подпись 0,78 rem; все числовые
ячейки — tabular-nums, включая st.dataframe.

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
        --calc:#1f4e79; --calc-bg:#eaf2fb; --calc-line:#c4d8ee;
        --obs:#1e8449;  --obs-bg:#eafaf1;  --obs-line:#bfe8cf;
        --fc:#b9770e;   --fc-bg:#fef5e7;   --fc-line:#f3dcb0;
        --cond:#c0392b; --cond-bg:#fdedec; --cond-line:#f2c0bb;
        --none:#566573; --none-bg:#f2f3f4; --none-line:#d7dbdf;
        /* прежние имена состояний — те же четыре тона, чтобы правила ниже читались одинаково */
        --ok:var(--obs); --ok-bg:var(--obs-bg); --warn:var(--fc); --warn-bg:var(--fc-bg);
        --crit:var(--cond); --crit-bg:var(--cond-bg); }
html, body, [class*="css"] { font-family: "Segoe UI", Inter, Roboto, Arial, sans-serif; color: var(--ink);
        font-variant-numeric: tabular-nums; }
.block-container { padding-top: 3.2rem; padding-bottom: 2rem; max-width: 1400px; }
h1, h2, h3 { letter-spacing: -0.01em; }
div[data-testid="stDataFrame"], div[data-testid="stTable"] { font-variant-numeric: tabular-nums; }
.vk-head { display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; margin-bottom:2px; }
.vk-title { font-size:1.55rem; font-weight:700; margin:0; }
.vk-sub { color:var(--muted); font-size:0.92rem; }
.sect { font-size:0.72rem; font-weight:700; letter-spacing:0.08em; text-transform:uppercase; color:var(--muted);
        margin:10px 0 4px 0; }
.pill { display:inline-block; padding:2px 9px; border-radius:999px; font-size:0.78rem; font-weight:600;
        line-height:1.5; border:1px solid transparent; margin:1px 4px 1px 0; white-space:nowrap; }
.pill-ok { background:var(--obs-bg); color:var(--obs); border-color:var(--obs-line); }
.pill-warn { background:var(--fc-bg); color:var(--fc); border-color:var(--fc-line); }
.pill-crit { background:var(--cond-bg); color:var(--cond); border-color:var(--cond-line); }
.pill-none { background:var(--none-bg); color:var(--none); border-color:var(--none-line); }
.pill-calc { background:var(--calc-bg); color:var(--calc); border-color:var(--calc-line); }
.pill-obs { background:var(--obs-bg); color:var(--obs); border-color:var(--obs-line); }
.pill-fc { background:var(--fc-bg); color:var(--fc); border-color:var(--fc-line); }
/* приборная полоса: две строки фиксированной сетки, метка — значение — давность и происхождение */
.panel { border:1px solid var(--line); border-radius:10px; background:var(--soft); margin:6px 0 12px 0; overflow:hidden; }
.prow { display:grid; grid-template-columns:repeat(auto-fit, minmax(190px, 1fr)); }
.prow + .prow { border-top:1px solid var(--line); }
.cell { padding:7px 12px 8px 12px; border-left:3px solid var(--none-line); box-shadow:inset 1px 0 0 var(--line); min-width:0; }
.cell:first-child { box-shadow:none; }
.cl { font-size:0.72rem; font-weight:700; letter-spacing:0.08em; text-transform:uppercase; color:var(--muted);
      white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.cv { font-size:1.05rem; font-weight:600; line-height:1.35; overflow-wrap:anywhere; }
.cs { font-size:0.78rem; color:var(--muted); line-height:1.35; overflow-wrap:anywhere; }
.k-calc { border-left-color:var(--calc); } .k-calc .cv { color:var(--calc); }
.k-obs  { border-left-color:var(--obs); }  .k-obs .cv  { color:var(--obs); }
.k-fc   { border-left-color:var(--fc); }   .k-fc .cv   { color:var(--fc); }
.k-cond { border-left-color:var(--cond); } .k-cond .cv { color:var(--cond); }
.k-none { border-left-color:var(--none-line); } .k-none .cv { color:var(--ink); }
.verdict { border-radius:12px; padding:16px 20px; border:1px solid var(--line); border-left:3px solid var(--none);
           background:var(--bg); margin:4px 0 14px 0; }
.verdict h2 { margin:0 0 4px 0; font-size:1.35rem; }
.verdict .rule { color:var(--muted); font-size:0.92rem; margin-bottom:8px; }
.verdict .rule .orig { color:#9aa0a6; font-size:0.84rem; }
.verdict .win { font-size:1.05rem; font-weight:600; }
.verdict ul { margin:6px 0 0 18px; padding:0; }
.verdict li { margin:2px 0; font-size:0.93rem; }
.verdict li.more { color:var(--muted); list-style:none; margin-left:-18px; font-size:0.86rem; }
.verdict .plan { margin:8px 0 2px 0; padding:6px 10px; border-radius:8px; border-left:3px solid var(--calc);
                 background:var(--calc-bg); color:var(--calc); font-size:0.88rem; }
.verdict .policy { margin:8px 0 0 0; font-size:0.84rem; color:var(--muted); }
/* вердикт — наш расчёт (синий); условие у всех окон — красный; нет оснований — серый. Без заливок. */
.v-preferred { border-left-color:var(--calc); }
.v-equivalent { border-left-color:var(--calc); }
.v-trade_off { border-left-color:var(--calc); }
.v-all_need_check { border-left-color:var(--cond); }
.v-insufficient { border-left-color:var(--none); }
.wcard { border:1px solid var(--line); border-radius:12px; padding:14px 16px 12px 16px; background:#fff; height:100%;
         border-left:3px solid var(--line); }
.wcard.best { border-left-color:var(--calc); }
.wcard.flag { border-left-color:var(--fc); }
.wcard.crit { border-left-color:var(--cond); }
.wcard .wh { display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:6px; }
.wcard .wt { font-weight:700; font-size:1.02rem; }
.wcard .wtime { color:var(--muted); font-size:0.86rem; }
.kv { display:grid; grid-template-columns: 1fr auto; gap:3px 10px; font-size:0.9rem; margin:8px 0 6px 0; }
.kv .k { color:var(--muted); }
.kv .v { font-weight:600; text-align:right; font-variant-numeric: tabular-nums; }
.kv .v.big { font-size:1.35rem; }
.kv .u { color:var(--muted); font-weight:400; }
.wnote { font-size:0.78rem; color:var(--muted); margin:-2px 0 6px 0; }
.cond { font-size:0.85rem; margin:4px 0 0 0; padding:6px 8px; border-radius:8px; border-left:3px solid var(--fc);
        background:var(--fc-bg); color:#7d5a12; }
.cond.crit { border-left-color:var(--cond); background:var(--cond-bg); color:#922b21; }
.cond.none { border-left-color:var(--none-line); background:var(--none-bg); color:var(--none); }
.cov { margin-top:8px; font-size:0.78rem; color:var(--muted); }
.covwhy { margin-top:4px; font-size:0.78rem; color:var(--muted); }
.legend { font-size:0.82rem; color:var(--muted); margin:2px 0 10px 0; }
.small { font-size:0.84rem; color:var(--muted); }
.tcap { font-size:0.82rem; color:var(--muted); font-style:italic; margin:2px 0 10px 0; }
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
# Почему на всей сетке порогов нет предпочтительного окна — по вердикту, а не одной фразой на все случаи
NO_PICK_ON_GRID_RU = {
    'all_need_check': 'на всей сетке порогов автоматический выбор не делается: у каждого окна условие',
    'equivalent': 'на всей сетке порогов окна остаются равнозначными',
    'trade_off': 'на всей сетке порогов механизмы указывают на разные окна',
    'insufficient': 'на всей сетке порогов оснований для рекомендации недостаточно',
}
COV_RU = {'full': 'полное', 'partial': 'частичное', 'none': 'нет'}
# покрытие — свойство нашего расчёта, а не «хорошо/плохо»: полное синим, частичное янтарём, нет — серым
COV_KIND = {'full': 'calc', 'partial': 'warn', 'none': 'none'}
MECH_RU = {'spaceweather': 'космопогода', 'mmod_stat': 'метеороиды', 'conjunctions': 'сближения'}
KIND_PILL = {'observation': ('наблюдение', 'obs'), 'external_forecast': ('внешний прогноз', 'fc'), 'own_calculation': ('наш расчёт', 'calc')}
# уровень предупреждения — текстом, без эмодзи (одинаково в Windows, macOS и на телефоне)
SEV_RU = {'critical': 'КРИТИЧНО', 'limiting': 'ВНИМАНИЕ', 'info': '—'}
# одна строка, объясняющая цвет: он означает происхождение величины, а не «безопасно/опасно».
# Три происхождения названы без союза «или» (R4-28): кеш — это давность наблюдения, а не прогноз.
COLOR_LEGEND = ('Цвет означает происхождение: синий — наш расчёт, зелёный — наблюдение источника (в том числе взятое '
                'из кеша: давность стоит подписью), янтарный — внешний прогноз. Красный — условие проверки, аномалия '
                'или отказ источника; серый — данных нет.')
# Причина, по которой на сетке порогов нет предпочтительного окна, — короткой вставкой в одну фразу
# об устойчивости (R4-18). Отличается от NO_PICK_ON_GRID_RU падежом и тем, что не повторяет «на сетке».
NO_PICK_REASON_RU = {
    'all_need_check': 'у каждого окна есть условие проверки, автоматический выбор не делается',
    'equivalent': 'окна остаются равнозначными',
    'trade_off': 'минуты в аномалии и флюенс указывают на разные окна',
    'insufficient': 'обязательной линии не хватает покрытия',
}

# --- словари перевода идентификаторов модулей (О5: без английских идентификаторов на экране) ---
METHOD_RU = {'sgp4': 'SGP4 по TLE', 'oem_interp': 'OEM NASA/JSC (интерполяция)'}
STRICT_RU = {'strict': 'строгая', 'declared_reconstruction': 'объявленная реконструкция',
             'reconstruction': 'реконструкция', 'unavailable': 'недоступна'}
EVENT_KIND_RU = {'SEP': 'протонное событие', 'GST': 'геомагнитная буря', 'FLR': 'вспышка', 'CME': 'выброс массы',
                 'CME_ARRIVAL': 'прогноз прихода выброса', 'IPS': 'межпланетный удар', 'HSS': 'высокоскоростной поток',
                 'RBE': 'усиление радиационного пояса', 'MPC': 'пересечение магнитопаузы', 'GST_KP': 'буря (Kp)'}
SOURCE_RU = {'orbit': 'орбита', 'noaa_swpc_goes': 'GOES ≥10 МэВ (NOAA SWPC)', 'gfz_kp': 'Kp (GFZ)',
             'ost1044_belts': 'таблицы ОСТ 134-1044-2007 (захваченные протоны)',
             'ecss_grun': 'модель метеороидов ECSS/Grün', '_layers': 'слои программы',
             'noaa_swpc_3day_forecast': 'трёхсуточный бюллетень NOAA SWPC (живой выпуск)',
             'donki_archive': 'архив DONKI: события, уведомления, прогоны ENLIL',
             'noaa_forecast_kp_forecast': 'прогноз Kp NOAA',
             'noaa_forecast_s1_prob_daily': 'прогноз NOAA: вероятность S1+ за сутки',
             'noaa_forecast_proton_prob_daily': 'прогноз NOAA: вероятность протонного события за сутки'}
# Откуда взят выпуск внешнего прогноза — свойство режима, а не источника: в текущем режиме отсечки
# нет вовсе, и подпись «выпуск до отсечки» у живого бюллетеня была неправдой (О2).
RELEASE_BY_MODE_RU = {'live': 'живой выпуск', 'history_forecast': 'выпуск до отсечки',
                      'history_review': 'выпуск из архива'}
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
# Пресеты запроса (И1): одна кнопка выставляет режим, дату, час и окна. Значения — те же,
# что у сохранённых примеров examples/, чтобы показанное на защите воспроизводилось из файла.
PRESETS = [
    {'key': 'now', 'label': 'Сейчас', 'mode': 'Текущая обстановка', 'date': None, 'hour': None,
     'duration_min': 360, 'search_min': 720, 'offsets_min': [0, 240],
     'shows': 'живые источники на момент нажатия: GOES ≥10 МэВ, Kp и элементы орбиты'},
    {'key': 'gannon', 'label': 'Буря Гэннон, 10.05.2024 12:00 UTC', 'mode': 'Прогноз из прошлого',
     'date': (2024, 5, 10), 'hour': 12, 'duration_min': 360, 'search_min': 720, 'offsets_min': [0, 240],
     'shows': 'только публикации до 10.05 12:00 UTC; на умолчаниях порогов оба окна получают условие проверки'},
    {'key': 'quiet', 'label': 'Тихая дата, 25.06.2024 12:00 UTC', 'mode': 'Прогноз из прошлого',
     'date': (2024, 6, 25), 'hour': 12, 'duration_min': 360, 'search_min': 1440, 'offsets_min': [0, 480],
     'shows': 'только публикации до 25.06 12:00 UTC; условий проверки нет, окна сравниваются по величинам'},
]


def esc(s) -> str:
    return html.escape(str(s), quote=False)


def raw_record(raw_records: dict | None, rid: str):
    """Сырая запись по идентификатору карточки.

    Идентификатор записи — «источник:выпуск:хеш[:тип события]». Слой истории (A2) добавляет тип
    к идентификатору в карточке («…:CME_ARRIVAL»), а в раздел сырых записей та же запись попадает
    без него: поиск в лоб её не находил, и карточка условия печатала «записей без ссылки» там, где
    у записи есть и адрес, и тело. Ищем точное совпадение, затем отбрасываем последние поля.
    """
    if not raw_records or not rid:
        return None
    rec = raw_records.get(rid)
    if rec is not None:
        return rec
    parts = str(rid).split(':')
    while len(parts) > 2:
        parts = parts[:-1]
        rec = raw_records.get(':'.join(parts))
        if rec is not None:
            return rec
    return None


def record_url(rec) -> str | None:
    """Адрес первоисточника одной сырой записи — единственное место, где он ищется (О4).

    Слои источников кладут адрес по-разному: живые записи A4 и уведомления DONKI — в
    `metadata.url`, архивные выпуски NOAA — в `url` верхнего уровня, сообщения DONKI старого
    разбора — в `messageURL`. Экран и выгрузка спрашивают адрес только здесь, иначе «ссылки нет»
    печатается там, где ссылка есть (найдено третьим кругом: карточка условия окна 2 на Гэннон).
    Порядок проверки: url → link → messageURL → metadata.url.
    """
    if not isinstance(rec, dict):
        return None
    for key in ('url', 'link', 'messageURL'):
        v = rec.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    md = rec.get('metadata')
    if isinstance(md, dict):
        v = md.get('url')
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


# Записи, которые сервис везёт с собой: таблицы стандартов, коэффициенты поля, календарь потоков
# и эфемериды NASA/JSC из репозитория. У них сетевого адреса нет и быть не должно. Всё остальное
# без адреса — живая запись, у которой слой источников адрес не сохранил: адрес получения есть
# в манифесте выгрузки, и называть её «в составе сервиса» неправда (R4-10).
BUILTIN_RECORD_PREFIXES = ('igrf', 'ost1044', 'ecss', 'imo_', 'nasa_jsc_oem', 'orbit_provenance')


def record_no_url_ru(rids) -> str:
    """Почему у записей нет ссылки — раздельно по причине, без общего «в составе сервиса»."""
    builtin = [r for r in rids if str(r).split(':')[0].startswith(BUILTIN_RECORD_PREFIXES)]
    live = [r for r in rids if r not in builtin]
    parts = []
    if builtin:
        parts.append('%d %s из состава сервиса (таблицы стандартов, коэффициенты поля, эфемериды) — сами записи '
                     'в выгрузке, папка raw/'
                     % (len(builtin), plural_ru(len(builtin), ('запись', 'записи', 'записей'))))
    if live:
        parts.append('%d %s живого источника — адрес записи не сохранён слоем источников, адрес получения есть '
                     'в манифесте выгрузки' % (len(live), plural_ru(len(live), ('запись', 'записи', 'записей'))))
    return 'без сетевого адреса: ' + '; '.join(parts) if parts else ''


def pill(text, kind='none') -> str:
    return '<span class="pill pill-%s">%s</span>' % (kind, esc(text))


_SUP = {'0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹',
        '-': '⁻', '+': ''}
_POW_RE = re.compile(r'10\^([+-]?\d+)')
_EXP_RE = re.compile(r'(?<![\w.])(\d+(?:[.,]\d+)?)[eE]([+-]?\d+)(?![\w])')
# дробь с точкой, но не дата (01.05.2024), не «10.05 12:00» и не номер версии (v3.1).
# Перечисление через дробную черту («канал 12.5/30/50 МэВ» в основании допуска) тоже число,
# а не дата: следом за ним стоит другое число, а не буква месяца.
# Скобка после числа тоже конец числа («Kp до 8.67 (наблюдённый Kp уведомления)»): даты в готовых
# строках модулей на этот момент ещё в виде «05-10 15:00Z» — точек в них нет, их ставит dates_ru позже.
_FRAC_RE = re.compile(r'(?<![\d.A-Za-zА-Яа-я])(\d+)\.(\d+)(?=\s*(?:[А-Яа-я%·)(\],;]|/\d|$))')


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


NBSP_THIN = ' '          # узкий неразрывный пробел: «24 000 нТл» не рвётся по строкам


def nbsp_thousands(v) -> str:
    """Разряды тысяч узким неразрывным пробелом (PROPOSAL_A п. 2.2, правило 3): 24000 → «24 000»."""
    if v is None:
        return '—'
    s = fmt(v)
    m = re.match(r'^(-?)(\d+)(,\d+)?$', s)
    if not m:
        return s
    whole = m.group(2)
    return m.group(1) + NBSP_THIN.join(_groups3(whole)) + (m.group(3) or '')


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


def plural_ru(n: int, forms: tuple[str, str, str]) -> str:
    """«733 записи», «1 запись», «5 записей»: число с существительным в нужной форме."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def dt_ru(t, with_date: bool = True, with_utc: bool = False) -> str:
    """Единый формат времени на экране: «10.05 12:00 UTC» или «12:00». Слово UTC — только там,
    где оно не повторяется у каждой ячейки (PROPOSAL_A п. 2.2, правило 5)."""
    if t is None:
        return '—'
    s = t.strftime('%d.%m %H:%M') if with_date else t.strftime('%H:%M')
    return s + (' UTC' if with_utc else '')


def age_ru(minutes, limit_min=None, limit_ru: str | None = None) -> str:
    """Давность в одних единицах: до 180 мин — минуты, до 48 ч — часы, дальше — сутки.
    Порог печатается рядом, если задан: «давность 92 мин (предел 60 мин)»."""
    if minutes is None:
        return 'давность не определена'
    m = float(minutes)
    if m < 180:
        s = 'давность %s мин' % fmt(round(m))
    elif m < 48 * 60:
        s = 'давность %s ч' % fmt(round(m / 60))
    else:
        s = 'давность %s сут' % fmt(round(m / 1440))
    lim = limit_ru if limit_ru is not None else (('%s мин' % fmt(round(float(limit_min)))) if limit_min is not None else None)
    return s + ((' (предел %s)' % lim) if lim else '')


def panel(rows) -> str:
    """Приборная полоса в две строки (PROPOSAL_A п. 3). rows — список строк, строка — список ячеек
    (метка, значение, подпись, kind). kind из calc|obs|fc|cond|none задаёт левую границу и цвет числа.
    Давность печатается только здесь: в карточках окон её нет (дублирование = шум)."""
    out = ['<div class="panel">']
    for row in rows:
        out.append('<div class="prow">')
        for label, value, sub, kind in row:
            out.append('<div class="cell k-%s"><div class="cl">%s</div><div class="cv">%s</div><div class="cs">%s</div></div>'
                       % (kind or 'none', esc(label), esc(value), esc(sub or '')))
        out.append('</div>')
    out.append('</div>')
    return ''.join(out)


def rule_ru(rule_applied: str, basis: bool = True) -> str:
    """«п.5: разница 48 мин меньше допуска 49 мин» → «шаг 5 из 5, допуск равнозначности: разница …».

    Для шагов 3–4 и 5 печатается ВЫЧИСЛЕННОЕ правило, а не заготовленная фраза: заготовка на шаге
    3–4 называла только минуты в аномалии и умалчивала о флюенсе — целевой величине сравнения окон,
    которая у выбранного окна может быть выше (найдено третьим кругом на пресете «Тихая дата»).
    Основание допуска на шаге 5 — разброс РАЗНОСТИ минут между двумя лучшими окнами (формула (9)),
    а не разброс абсолютных минут одного окна: порог сдвигает оба окна синфазно.

    `basis=False` снимает хвост об основании допуска: на оперативном уровне он стоит отдельной
    строкой под вердиктом, уже с числами сетки, и в правиле повторялся бы вторым сообщением об одном.
    """
    for prefix, text in RULE_RU:
        if rule_applied.startswith(prefix):
            tail = rule_applied.partition(': ')[2].strip()
            if prefix == 'п.5':
                return text + (': ' + tail if tail else '') + (
                    '; допуск — разброс разности минут между двумя лучшими окнами по сетке порогов' if basis else '')
            if prefix == 'п.3–4':
                return 'шаги 3–4 из 5, сравнение и сведение: ' + tail if tail else text
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
# Перечень идентификаторов записей режется, содержательный текст после «записи:» — нет (R4-4):
# у условия по протонному событию источник звучит «записи: публикация 05-09 13:54Z — 05-10 14:19Z»,
# и прежнее выражение выносило с экрана ровно то время публикации, за которое даются баллы по О4.
_RECORDS_TAIL_RE = re.compile(r'[;,]?\s*запис[ьи]:\s+(?=[A-Za-z][\w.\-]*[:#])[^;]*', re.S)
_SRCID_RE = re.compile(r'\s*\([a-z][a-z0-9]*_[a-z0-9_]+\)')          # (celestrak_gp), (nasa_jsc_oem)
_CONTRACT_RE = re.compile(r'CONTRACT\.md(\s+v[\d.]+)?')
# идентификатор выпуска может содержать двоеточие («noaa_swpc_3day_forecast:fa0f21a1…» у живого
# слоя A4) — без него на оперативном уровне печатался 64-значный хеш
_RELEASE_RE = re.compile(r'выпуск\s+[\w:\-]*[A-Za-z][\w:\-]*\s+от\b')
_CHECK_RE = re.compile(r'[;,]?\s*контроль\s+[^;]+воспроизведён')
# Перебор адресов цепочки TLE: «проверка адресов: https://…?CATNR=25544&FORMAT=TLE: timeout».
# На оперативном уровне это техническая строка с параметрами запроса (бриф §9.8) — она режется
# целиком и остаётся на профессиональном уровне и в манифесте (R4-6, R4-27).
_PROBE_TAIL_RE = re.compile(r'[;,]?\s*проверка адресов:.*$', re.S)
_PROBE_FAIL_RE = re.compile(r'(?:%s|timeout|time-out)' % _NET_ALT, re.I)
# обороты слоёв программы, которым на оперативном уровне нужен русский (U2)
PHRASE_RU = [('интеграл по dt', 'интеграл по времени'), ('Table J-6', 'табл. J-6'), ('Rev.1', 'ред. 1'),
             # «настройка config/settings.toml» стоит в скобке рядом с порогом различимости окон по
             # линии метеороидов: имя файла зрителю ничего не говорит, а происхождение числа — говорит
             ('настройка config/settings.toml', 'порог задан в настройках сервиса'),
             ('в config/settings.toml', 'в настройках сервиса'), ('config/settings.toml', 'настройки сервиса'),
             # английские обрывки слоя источников на оперативном уровне (R4-6): имя продукта NOAA и
             # служебное имя канала Kp переводятся, длинное — раньше короткого
             ('живой бюллетень NOAA 3-day', 'живой трёхсуточный бюллетень NOAA'),
             ('бюллетень NOAA 3-day', 'трёхсуточный бюллетень NOAA'),
             ('NOAA 3-day', 'трёхсуточный бюллетень NOAA'),
             ('отдельным выпуском NGDC daypre', 'отдельным суточным выпуском NOAA (архивным)'),
             ('NGDC daypre', 'отдельный суточный выпуск NOAA (архивный)'),
             ('незавершённый Kp-nowcast исключён', 'незавершённый 3-часовой интервал Kp в расчёт не взят'),
             # имена ключей настроек: на профессиональном уровне как есть, на оперативном — по-русски.
             # Ключи всегда стоят после слова «настройка/настройке/настройкой», поэтому заменяем само имя.
             ('sep_valid_hours', 'срока действия уведомления о протонном событии'),
             ('event_valid_hours', 'срока действия записи о буре или приходе выброса'),
             # поля прогона WSA-ENLIL: оценки Kp по углу межпланетного магнитного поля
             ('kp_180', 'угол поля 180°'), ('kp_135', 'угол поля 135°'), ('kp_90', 'при угле межпланетного поля 90°'),
             # номера решений команды и имена задач — только на профессиональном уровне и в выгрузке
             ('(R11)', '(решение команды по магнитным координатам)'),
             ('точки (A3)', 'точки трассы'), ('(A3)', '(модуль орбиты)'), ('из A3', 'из модуля орбиты'),
             ('работа A3', 'работа модуля орбиты')]
_INNER_ID_RE = re.compile(r',\s*[a-z][a-z0-9]*(?:[-_][a-z0-9]+)+(?=\))')
_CLEAN_RE = re.compile(r'\s{2,}')


def phrase_ru(text) -> str:
    """Обороты слоёв программы по-русски без удаления происхождения: имена настроек и модулей
    переводятся, хеши, файлы и идентификаторы записей остаются. Нужно выгрузке: отчёт должен
    говорить словами экрана, но не терять прослеживаемость (в отличие от status_ru)."""
    s = frac_ru(str(text or ''))
    for a, b in PHRASE_RU:
        s = s.replace(a, b)
    return s


def net_error_ru(name: str) -> str:
    """«ReadTimeout» → «сервер не ответил за 6 с»; неизвестное имя — общая фраза без кода."""
    t = NET_ERR_RU.get(name)
    if t is None:
        return 'источник не ответил'
    return (t % _timeout_s()) if '%s' in t else t


def status_ru(text, pro: bool = False) -> str:
    """Статус источника для экрана (U2). На профессиональном уровне — как есть, только числа
    приводятся к единому виду. На оперативном — без имён отказов, путей модулей, файлов, хешей,
    идентификаторов записей, перебора адресов и давности: их место — профессиональный уровень,
    отдельная колонка «давность, мин» и выгрузка.

    Давность вырезается здесь по той же причине, что и в карточках окон (PROPOSAL_A п. 3): в
    таблице 3 она уже стоит своей колонкой, и два округления одной величины в одной строке
    («давность, мин» = 103 и «давность данных 102,7 мин») читаются как два разных числа (R4-27)."""
    s = frac_ru(str(text or '')).replace('из архив ', 'из архива ')
    if pro or not s:
        return s
    m_probe = _PROBE_TAIL_RE.search(s)
    if m_probe:
        tail = m_probe.group(0)
        s = s[:m_probe.start()].rstrip(' ;,')
        if _PROBE_FAIL_RE.search(tail):
            s += '; часть адресов цепочки не ответила — взят тот, что назван выше'
    s = drop_age(s)
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
    return screen_text(s.rstrip(' ;,'))


def close_cut_parens(text) -> str:
    """Закрыть скобку у фрагмента, обрезанного многоточием, и в конце строки (R4-13).

    Слой объяснений обрезает длинное тело уведомления многоточием, и обрез иногда приходится на
    середину скобки: «опубликованный прогноз: Kp до 8 (диапазон 6–8, верхняя граница, не…». На
    экране это незакрытая скобка в самом читаемом месте карточки. Текст фрагмента здесь не
    меняется — добавляется только закрывающая скобка там, где его оборвали.
    """
    s = str(text or '')
    if s.count('(') == s.count(')'):
        return s
    out, depth = [], 0
    for ch in s:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth = max(0, depth - 1)
        out.append(ch)
        if ch == '…' and depth > 0:
            out.append(')')
            depth -= 1
    return ''.join(out) + ')' * depth


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


# Ключ источника в снимке → ключ управления «Источники» в боковой панели. По нему берётся
# ЗАПРОШЕННОЕ состояние источника (S['request']['disabled']), а не подстрока в тексте статуса.
DISABLED_KEY = {'noaa_swpc_goes': 'goes', 'gfz_kp': 'kp', 'noaa_swpc_3day_forecast': 'noaa'}


def source_state(v: dict | None, requested=None) -> str | None:
    """Состояние источника: 'off' (исключён пользователем) | 'cache' (объявленный отказ) | None.

    Читается ТОЛЬКО из явного признака: сначала из снимка источника (ключ `state`, если слой расчёта
    его положит), затем из запроса — `S['request']['disabled']`. Из текста статуса состояние не
    выводится ни при каких условиях: слой источников штатно дописывает в него слово «исключён»
    («незавершённый Kp-nowcast исключён» — почти в каждом живом прогоне), и поиск подстроки объявлял
    исключённым пользователем источник, который отвечал по сети (R4-11, критическая находка).
    """
    st_ = (v or {}).get('state')
    if st_ in ('off', 'cache'):
        return st_
    if requested in ('off', 'cache'):
        return requested
    if requested is True:
        return 'off'
    return None


def source_short(v: dict, requested=None) -> tuple[str, str]:
    """Короткий статус источника для полосы состояния и таблицы 3: (текст, kind).
    `requested` — состояние из `S['request']['disabled']` для этого источника."""
    st_ = (v.get('status') or '').lower()
    if source_state(v, requested) == 'off':
        return 'исключён пользователем', 'crit'
    if v.get('live_ok') is False and v.get('from_cache'):
        age = v.get('age_min')
        return ('кеш, %s' % age_ru(age)) if age is not None else 'кеш', 'warn'
    if 'данных нет' in st_ or 'не разбирается' in st_:
        return 'нет ответа и кеша — данных нет', 'crit'
    if v.get('live_ok'):
        return 'живой запрос', 'ok'
    return status_ru(v.get('status') or 'нет данных'), 'none'


def orbit_created_ru(created) -> str:
    """«08.05.2024 16:49 UTC» из даты создания эфемериды; год обязателен — речь об архиве 2024 года."""
    if isinstance(created, str):
        try:
            created = datetime.fromisoformat(created)
        except ValueError:
            return created
    if not isinstance(created, datetime):
        return ''
    return created.strftime('%d.%m.%Y %H:%M') + ' UTC'


def source_issues(src: dict, th, mode: str, kp_excluded_hist: bool = False, tle_fetch: str | None = None,
                  pro: bool = False, disabled: dict | None = None, cutoff_utc=None,
                  orbit_created_utc=None) -> list[str]:
    """Проблемы источников для одного st.warning под полосой состояния (О5-2): исключён, кеш, устарел.
    Ничего не решает — переводит явные признаки снимка в предложения для пользователя."""
    out = []
    disabled = disabled or {}
    if mode == 'live':
        for sid, name in (('noaa_swpc_goes', 'GOES ≥10 МэВ (NOAA SWPC)'), ('gfz_kp', 'Kp (GFZ)')):
            v = src.get(sid) or {}
            state = source_state(v, disabled.get(DISABLED_KEY[sid]))
            if state == 'off':
                out.append('%s: исключён пользователем — %s' % (
                    name, 'обязательная линия без покрытия, рекомендации не будет' if sid == 'noaa_swpc_goes'
                    else 'условие по наблюдению Kp не проверяется, покрытие объявлено'))
            elif v.get('live_ok') is False and v.get('from_cache'):
                age = v.get('age_min')
                out.append('%s: живого ответа нет, взят кеш%s — покрытие частичное, объявлено'
                           % (name, (', %s' % age_ru(age)) if age is not None else ''))
            elif v.get('live_ok') is False:
                out.append('%s: %s' % (name, status_ru(v.get('status') or 'нет данных', pro)))
            elif sid == 'noaa_swpc_goes' and v.get('age_min') is not None and th is not None \
                    and v['age_min'] > th.goes_max_age_min:
                out.append('GOES ≥10 МэВ: наблюдение устарело (%s) — для будущих участков окна покрытие частичное'
                           % age_ru(v['age_min'], th.goes_max_age_min))
        o = src.get('orbit') or {}
        if o.get('live_ok') is False and o.get('from_cache'):
            # «по кешу (кеш, давность 31 ч)» дублировало слово и оставляло пустые скобки, когда
            # происхождение снимка неизвестно (R4-27): давность берётся из той же строки один раз
            _, _, _age = tle_origin(tle_fetch).partition(', ')
            out.append('Элементы орбиты: живого ответа нет — орбита построена по проверенному %s%s'
                       % ('снимку репозитория' if 'снимок' in (tle_fetch or '') else 'кешу',
                          (', ' + _age) if _age.startswith('давность') else ''))
        if o.get('age_h') is not None and o['age_h'] > 24:
            out.append('Элементы орбиты: эпоха старше суток (%s) — точность положения снижается, предел %s сут задан порогом'
                       % (age_ru(o['age_h'] * 60), fmt(th.tle_max_age_days) if th is not None else '—'))
    else:
        if kp_excluded_hist:
            out.append('Kp: исключён пользователем из архива (проверка отказа) — условие по наблюдению Kp не проверяется')
        # C3: численный архив наблюдений GOES 2024 есть, но в строгий режим он не идёт.
        # Причина берётся из снимка и называется один раз, здесь, а не плашкой в полосе состояния.
        g = src.get('noaa_swpc_goes') or {}
        if not g.get('data_utc'):
            out.append('GOES ≥10 МэВ: численного наблюдения на этот момент нет — %s. Линия протонного события '
                       'держится на пороговых уведомлениях DONKI; покрытие объявлено'
                       % status_ru(g.get('status') or 'причина в записи источника не указана', pro))
        o = src.get('orbit') or {}
        if o.get('strictness') == 'declared_reconstruction':
            # Слово «отсечка» уместно только там, где отсечка есть. В «Историческом разборе»
            # cutoff_utc = None, и прежняя безусловная фраза противоречила двум другим блокам
            # того же экрана (R4-4): полосе состояния и вкладке «Данные».
            if cutoff_utc:
                out.append('Орбита: OEM NASA/JSC создан до отсечки, но его публичная доступность в тот момент '
                           'не доказана — объявленная реконструкция')
            else:
                _c = orbit_created_ru(orbit_created_utc)
                out.append('Орбита: эфемерида OEM NASA/JSC%s; её публичная доступность в тот момент документально '
                           'не доказана — объявленная реконструкция' % ((' создана ' + _c) if _c else ''))
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


def verdict_reasons(rec, assessments, mech_ru: dict | None = None, max_items: int = 6,
                    max_other: int | None = None) -> tuple[list[str], int]:
    """Список условий панели вердикта с указанием окна и без дубликатов (О3-3).
    Возвращает (строки, сколько не показано).

    `max_other` — сколько строк НЕ о сравнении механизмов оставить (бриф §9.1 и §9.7). Сообщения о
    неполном покрытии слой расчёта даёт и общей строкой, и отдельно по каждому окну; в блоке «Почему?»
    это три формулировки об одном и том же, а полностью они стоят в карточках окон и в таблице 1.
    Строки сравнения (обе величины обоих окон) не режутся никогда — ради них блок и существует.
    """
    mech_ru = mech_ru or MECH_RU
    out: list[str] = []
    is_cmp: list[bool] = []
    if rec.verdict == 'all_need_check' and assessments:
        by: dict[str, list[int]] = {}
        for i, a in enumerate(assessments):
            for m in a.mechanisms:
                for r in m.needs_check_reasons:
                    by.setdefault(_short_reason(r), []).append(i + 1)
        for text, nums in by.items():
            out.append('%s: %s' % (_wins_ru(nums), text))
            is_cmp.append(True)          # сами условия — это и есть ответ на «почему», их не режем
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
            is_cmp.append(bool(mech))
    if max_other is not None:
        kept, others = [], 0
        for txt, cmp_ in zip(out, is_cmp):
            if cmp_ or others < max_other:
                kept.append(txt)
                others += 0 if cmp_ else 1
        dropped = len(out) - len(kept)
        if len(kept) > max_items:
            return kept[:max_items], dropped + len(kept) - max_items
        return kept, dropped
    if len(out) > max_items:
        return out[:max_items], len(out) - max_items
    return out, 0


_MMOD_ROLE_RU = 'роль линии: абсолютная оценка и охват, не выбор окна'
# Точка с запятой ВНЕ скобок: «(правило команды, не норма; настройка …)» — одно целое, резать по
# внутренней точке с запятой нельзя. Раньше резали по первой любой, и на экране оставалась
# незакрытая скобка без точки, а вместе с ней пропадало происхождение порога 5 % (R4-1, R4-13).
_SEMI_OUTSIDE_PARENS_RE = re.compile(r';\s*(?![^()]*\))')


def bullet_short_ru(text: str) -> str:
    """Короткая форма причины для оперативного уровня (бриф экрана §9.1: две-три величины).

    Сокращается только пояснение про линию метеороидов: полностью оно повторяется во вкладке
    «Методика», формула (6), и в карточке окна. Сравнение по космопогоде не трогается — в нём
    стоят обе величины обоих окон, ради которых блок «Почему?» и существует.

    Происхождение порога различимости остаётся в короткой форме: имя файла настроек переводится
    словами (PHRASE_RU), а не отрезается вместе с закрывающей скобкой."""
    s = str(text or '')
    if 'линия метеороидов' not in s and 'линии метеороидов' not in s:
        return s
    head_ = _SEMI_OUTSIDE_PARENS_RE.split(s)[0].strip().rstrip('.')
    for a, b in PHRASE_RU:
        head_ = head_.replace(a, b)
    return head_ + ' — ' + _MMOD_ROLE_RU + '.'


def robustness_pill(rec, rob: dict) -> str:
    """О7: «устойчив» печатается только когда выбор есть (DEMO-17).

    Когда автоматического выбора нет ни в одной ячейке сетки, причина называется словами вердикта:
    «без автовыбора» читалось как «окна заблокированы условиями» и там, где условий нет вовсе,
    а окна просто равнозначны (найдено третьим кругом в режиме «Сейчас»)."""
    grid = rob.get('preferred_by_grid') or {}
    vals = set(grid.values())
    if rec.preferred is None and grid and vals == {None}:
        return pill(NO_PICK_ON_GRID_RU.get(rec.verdict, 'на всей сетке порогов предпочтительного окна нет'), 'none')
    if rob.get('stable'):
        return pill('выбор устойчив на сетке порогов' if rec.preferred is not None else 'ранжирование устойчиво на сетке порогов', 'ok')
    return pill('выбор меняется на сетке порогов' if rec.preferred is not None else 'ранжирование меняется на сетке порогов', 'warn')


def _win_no_by_iso(S: dict) -> dict:
    """Номер окна по времени начала в виде ISO — как окна записаны в снимке."""
    return {w['start_utc']: str(w['index']) for w in (S.get('windows') or [])}


def _cells_ru(n: int) -> str:
    return plural_ru(n, ('ячейки', 'ячеек', 'ячеек'))


def robustness_line_ru(rec, S: dict, thr_nT, e_min_MeV) -> str:
    """Одна понятная фраза об устойчивости выбора — с причиной и с рабочими порогами (R4-18).

    До неё на одном экране стояли три формулировки об одном: плашка «выбор меняется на сетке порогов»
    под вердиктом «Есть предпочтительное окно» и подпись таблицы 1 про «порядок окон при нулевом
    допуске». Формально каждая верна, но вместе они читаются как взаимное опровержение, а слова
    «ранжирование» и «порядок» на оперативном уровне не разведены и за три минуты не разбираются.
    Здесь печатается ровно одно предложение; ранжирование остаётся на вкладке «Устойчивость и нормы».

    Ничего не досчитывается: и рабочие пороги, и исход каждой ячейки берутся из снимка.
    """
    rob = S.get('robustness') or {}
    grid = rob.get('preferred_by_grid') or {}
    nums = _win_no_by_iso(S)
    where = 'рабочих порогах (%s нТл, от %s МэВ)' % (nbsp_thousands(thr_nT), fmt(float(e_min_MeV)))
    n = len(grid)
    pref_iso = rec.preferred.start_utc.isoformat() if rec.preferred is not None else None
    if pref_iso is not None:
        head_ = 'Окно %s выбрано на %s' % (nums.get(pref_iso, '—'), where)
        if not n:
            return head_ + '; сетка порогов не считалась, устойчивость выбора не проверена.'
        same = sum(1 for v in grid.values() if v == pref_iso)
        if same == n:
            return head_ + ' и остаётся предпочтительным во всех %d %s сетки порогов — выбор устойчив.' \
                % (n, plural_ru(n, ('ячейке', 'ячейках', 'ячейках')))
        none_cells = sum(1 for v in grid.values() if not v)
        other = n - same - none_cells
        parts = []
        if other:
            parts.append('в %d из %d %s сетки правило называет другое окно' % (other, n, _cells_ru(n)))
        if none_cells:
            parts.append('в %d из %d %s сетки правило лучшее окно не называет — разница внутри допуска'
                         % (none_cells, n, _cells_ru(n)))
        return head_ + '; ' + ', '.join(parts) + ', поэтому выбор считается неустойчивым.'
    if not n:
        return 'Предпочтительного окна нет; сетка порогов не считалась, устойчивость ответа не проверена.'
    named = [v for v in grid.values() if v]
    reason = NO_PICK_REASON_RU.get(rec.verdict, 'правило лучшее окно не называет')
    if not named:
        return ('Предпочтительного окна нет ни на %s, ни в одной из %d %s сетки порогов: %s — ответ устойчив.'
                % (where, n, _cells_ru(n), reason))
    wins = ' и '.join(sorted({nums.get(v, '—') for v in named}))
    return ('На %s предпочтительного окна нет (%s), но в %d из %d %s сетки правило называет окно %s — '
            'ответ неустойчив к настройке порога.' % (where, reason, len(named), n, _cells_ru(n), wins))


def tolerance_origin_ru(S: dict, pro: bool = False) -> str:
    """Откуда взялся допуск равнозначности — одной строкой под вердиктом (R4-19).

    Допуск по минутам не задан числом: это размах РАЗНОСТИ минут между двумя лучшими окнами по оси
    порога аномалии (формула (9)). Без этой строки на главном экране стояло «окна равнозначны —
    разница внутри допуска 48 мин» при разнице 41 мин, и откуда взялись 48 мин, сказано не было.
    Все числа — из `S['robustness']`, ничего не зашито.
    """
    rob = S.get('robustness') or {}
    diff = rob.get('diff_by_thr') or {}
    if not diff:
        return ''

    keys = sorted(diff, key=lambda k: float(k))
    thr_s = ' / '.join(nbsp_thousands(float(k)) for k in keys)
    val_s = ' / '.join(_minus(fmt(round(float(diff[k])))) for k in keys)
    span = max(float(v) for v in diff.values()) - min(float(v) for v in diff.values())
    tol = float(rob.get('tol_min') or 0.0)
    s = ('Откуда допуск %s мин: при порогах аномалии %s нТл разность минут между двумя лучшими окнами равна '
         '%s мин, её размах по сетке — %s мин.' % (fmt(round(tol)), thr_s, val_s, fmt(round(span))))
    s += (' Настолько разность двигает одна настройка порога, поэтому меньшую разницу мы не считаем '
          'преимуществом окна.') if round(tol) <= round(span) else \
         (' Допуск не опускается ниже %s мин — это нижняя граница настройки.' % fmt(round(tol)))
    if pro:
        ratio = rob.get('ratio_by_e') or {}
        if ratio:
            ek = sorted(ratio, key=lambda k: float(k))
            rs = max(float(v) for v in ratio.values()) / min(float(v) for v in ratio.values())
            s += (' Допуск по флюенсу ×%s: разброс отношения флюенсов по каналам %s МэВ — ×%s%s.'
                  % (ratio_ru(rob.get('tol_ratio')), ' / '.join(fmt(float(k)) for k in ek), ratio_ru(rs),
                     '' if float(rob.get('tol_ratio') or 0) <= rs else ', ниже границы настройки'))
    return s


def _minus(s: str) -> str:
    """Знак минуса у отрицательного числа — типографский, а не дефис."""
    return s.replace('-', '−')


def ratio_ru(v) -> str:
    """Допуск по флюенсу — всегда двумя знаками («×1,50»), как он записан в основании допуска."""
    return ('%.2f' % float(v or 0)).replace('.', ',')


# R4-17 закрыт в слое сравнения окон, а не на экране. Прежде здесь стояла дописка
# qualify_tolerance_ru: она подставляла допуск в готовый хвост «не хуже по флюенсу и минутам».
# После слияния пятого круга vkd/windows/compare.py собирает эту фразу из вычисленного и сам
# печатает и величины, и допуск, и отношение («не хуже по флюенсу в пределах допуска ×1,50
# (1,74·10⁶ против 1,65·10⁶ част./см², отношение ×1,05)»), а прежнего хвоста не выдаёт ни в одной
# ветке (tests/test_models_round4.py). Дописка стала недостижимой и снята: экран не переписывает
# правило и не подставляет в него числа.


def coverage_scope_ru(S: dict) -> str:
    """Охват расчёта одной строкой: что учтено и что нет (О1). Стоит во вкладке «Окна и факторы»
    на обоих уровнях и в панели вердикта — на профессиональном."""
    return 'Охват: %s. Не учтено: %s.' % (', '.join(S.get('coverage_declared', []) or ['—']),
                                          ', '.join(S.get('coverage_missing', []) or ['—']))


def verdict_panel(rec, S: dict, windows_ru: dict, assessments=None, pro: bool = False,
                  plan_change: str | None = None, missing_ru=None, policy_short: str | None = None,
                  thr_nT=None, e_min_MeV=None) -> str:
    v = rec.verdict
    title = VERDICT_TITLE.get(v, v)
    # Основание допуска на оперативном уровне не повторяется в строке правила: под вердиктом стоит
    # отдельная строка «Откуда допуск …» с числами сетки (бриф §9.7: нет двух сообщений об одном).
    rob = S.get('robustness') or {}
    rule = 'Правило: ' + esc(frac_ru(rule_ru(rec.rule_applied, basis=pro))) + \
           ' · формальная запись — вкладка «Методика», формулы (8) и (9)'
    if pro:
        rule += ' <span class="orig">(%s)</span>' % esc(frac_ru(rec.rule_applied))
    lines = ['<div class="verdict v-%s">' % v, '<h2>%s</h2>' % esc(title), '<div class="rule">%s</div>' % rule]
    if rec.preferred is not None:
        lines.append('<div class="win">Окно %s — %s</div>' % (_win_num(windows_ru, rec.preferred.start_utc), esc(win_span(rec.preferred))))
    bullets, more = verdict_reasons(rec, assessments, max_other=None if pro else 1)
    if not pro:
        bullets = [bullet_short_ru(b) for b in bullets]
    missing = list(missing_ru) if missing_ru is not None else list(rec.missing)
    bullets += ['Чего не хватает: ' + x for x in missing]
    if bullets:
        lis = ''.join('<li>%s</li>' % esc(screen_text(b)) for b in bullets)
        if more:
            lis += '<li class="more">… ещё %d о покрытии, см. карточки окон и вкладку «Окна и факторы»</li>' % more
        lines.append('<ul>' + lis + '</ul>')
    if plan_change:
        lines.append('<div class="plan">%s</div>' % esc(plan_change))
    if policy_short:
        lines.append('<div class="policy">%s</div>' % esc(policy_short))
    sim = pill('сценарий «что если»', 'warn') if S.get('is_simulated') else ''
    if thr_nT is not None and e_min_MeV is not None:
        lines.append('<div class="cov">%s%s</div>'
                     % (sim, esc(screen_text(robustness_line_ru(rec, S, thr_nT, e_min_MeV)))))
    else:                                  # порогов не передали — печатаем прежнюю плашку, не выдумывая
        lines.append('<div class="cov">%s %s</div>' % (robustness_pill(rec, rob), sim))
    # Допуск равнозначности участвует только в сравнении окон. Там, где до сравнения не дошло
    # (у каждого окна условие; нет покрытия обязательной линии), строка о нём была бы лишней.
    tol = tolerance_origin_ru(S, pro) if v in ('preferred', 'equivalent', 'trade_off') else ''
    if tol:
        lines.append('<div class="policy">%s</div>' % esc(screen_text(tol)))
    if pro:                                # охват на оперативном уровне — во вкладке «Окна и факторы»
        lines.append('<div class="cov">%s</div>' % esc(coverage_scope_ru(S)))
    lines.append('</div>')
    return ''.join(lines)


def win_span(w) -> str:
    """«10.05 22:00 — 11.05 00:00 UTC, 120 мин»: дата конца печатается, если окно переходит через полночь."""
    end = w.start_utc + timedelta(minutes=w.duration_min)
    end_s = end.strftime('%d.%m %H:%M') if end.date() != w.start_utc.date() else end.strftime('%H:%M')
    return '%s — %s UTC, %d мин' % (w.start_utc.strftime('%d.%m %H:%M'), end_s, w.duration_min)


_win_span = win_span     # прежнее имя


_ISO_DT_RE = re.compile(r'(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::\d{2})?Z?')
_MD_DT_RE = re.compile(r'(?<![\d.\-])(\d{2})-(\d{2}) (\d{2}):(\d{2})Z')
_AGE_CLAUSE_RE = re.compile(r'[,;]?\s*давность(?:\s+данных)?\s+\d+(?:[.,]\d+)?\s*(?:мин|ч|сут)\b'
                            r'(?:\s*\(предел[^)]*\))?')
# разряды тысяч в готовых строках модулей: «24000 нТл» → «24 000 нТл» (единица обязательна, год не трогаем)
# Единица может стоять не у каждого числа, а один раз в конце перечисления через дробную черту:
# «22000/24000/26000 нТл». Без учёта перечисления разряды получало только последнее число, и
# в одной фразе стояло «22000/24000/26 000» — три вида записи одного и того же (пятый круг).
_THOUSANDS_RE = re.compile(r'(?<![\d,.])(\d{4,})(?=(?:\s*/\s*\d+)*\s*(?:нТл|мин\b|км\b|сут\b|Зв\b|част\.|пфу\b|pfu\b))')


def dates_ru(text) -> str:
    """Дата источника в едином виде экрана (S7): «2026-09-19 01:35Z» → «19.09.2026 01:35»,
    «05-09 14:00Z» → «09.05 14:00». Времена всюду UTC, слово UTC печатается один раз в шапке."""
    s = _ISO_DT_RE.sub(lambda m: '%s.%s.%s %s:%s' % (m.group(3), m.group(2), m.group(1), m.group(4), m.group(5)),
                       str(text or ''))
    return _MD_DT_RE.sub(lambda m: '%s.%s %s:%s' % (m.group(2), m.group(1), m.group(3), m.group(4)), s)


def screen_text(text) -> str:
    """Готовая строка модуля в виде экрана: дробь с запятой, степени надстрочно, даты «дд.мм чч:мм»,
    разряды тысяч узким неразрывным пробелом."""
    s = dates_ru(frac_ru(text))
    return _THOUSANDS_RE.sub(lambda m: NBSP_THIN.join(_groups3(m.group(1))), s)


def _groups3(whole: str) -> list[str]:
    out = []
    while len(whole) > 3:
        out.insert(0, whole[-3:])
        whole = whole[:-3]
    out.insert(0, whole)
    return out


def drop_age(text) -> str:
    """Убрать оборот давности: она печатается только в приборной полосе (PROPOSAL_A п. 3)."""
    return _CLEAN_RE.sub(' ', _AGE_CLAUSE_RE.sub('', str(text or ''))).strip().rstrip(' ;,')


_OBS_SHARE_RE = re.compile(r'горизонт наблюдения[^;]*покрывает (\d+) % окна')


def obs_share_pct(f) -> int | None:
    """Какую долю окна покрывает горизонт наблюдения этого фактора — из записи слоя расчёта.
    None — фактор не наблюдение или доля в записи не указана."""
    m = _OBS_SHARE_RE.search(str(getattr(f, 'limits_note', '') or ''))
    return int(m.group(1)) if m else None


def factor_value_ru(f, unit: str = '') -> str:
    """Значение фактора для карточки окна и таблицы сравнения.

    Наблюдение, горизонт которого не покрывает окно ни на одну минуту, характеристикой этого
    окна не является: печатается прочерк, а само измерение со своим временем остаётся в полосе
    состояния и в карточке объяснения (О2). Иначе прошлое измерение выдавалось за величину
    будущего окна, и карточка противоречила вердикту на одном экране (найдено третьим кругом)."""
    if f is None:
        return '—'
    if obs_share_pct(f) == 0:
        return '—'
    return fmt(getattr(f, 'value', None), unit)


def _cov_reason(f) -> str | None:
    """Причина неполного покрытия одного фактора — одной фразой для карточки окна (DEMO-11).
    Давность из неё убрана (она только в приборной полосе), даты приведены к виду экрана."""
    note = drop_age(dates_ru(f.limits_note or ''))
    name = f.name
    m = re.search(r'доля точек с моделью (\d+) %', note)
    if name.startswith('флюенс') and m:
        return 'флюенс: модель ОСТ есть для %s %% точек трассы' % m.group(1)
    if name.startswith('поток протонов GOES'):
        share = obs_share_pct(f)
        if share == 0:
            m0 = re.search(r'наблюдение\s+([\d.:\s]+)', note)
            return 'GOES: наблюдение%s не покрывает окно — значение окна не определено' \
                % ((' ' + m0.group(1).strip()) if m0 else '')
        if 'наблюдений GOES нет' in note and 'DONKI о протонных' in note:
            return 'GOES: наблюдений за 2024 нет, канал по датированным уведомлениям DONKI'
        if 'наблюдений GOES нет' in note and 'каталог' in note:
            return 'GOES: наблюдений за 2024 нет; каталог DONKI покрывает период, событий не объявлено'
        if 'устарело' in note:
            return 'GOES: наблюдение устарело — для будущих участков окна'
        if 'нет данных' in note:
            return 'GOES: данных нет'
    if name == 'минут в аномалии':
        return 'трасса покрывает окно не полностью'
    m3 = re.search(r'трасса покрывает (\d+) % окна', note)
    if m3:
        return 'метеороиды: трасса покрывает %s %% окна' % m3.group(1)
    m4 = re.search(r'покрытие окна ячейками (\d+) %', note)
    # «ячейки покрывают 0 % окна» верно только тогда, когда выпуск есть, а его ячейки до окна не
    # дошли. Когда допустимого выпуска нет вовсе, слой расчёта прямо пишет это в ограничении, и
    # карточка обязана называть ту же причину, что таблица источников (R4-21).
    if m4 and 'выпуска с ячейками на это окно нет' in note:
        return '%s: выпуска с этим каналом на горизонт окна нет — канал не учитывается, объявлено' % name.split(',')[0]
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


def window_card(i: int, a, best: bool, mode: str, saa_thr_nT: float | None = None) -> str:
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
    # единица — в подписи строки, в ячейке только число (PROPOSAL_A п. 2.2, правило 4)
    rows = [('минут в аномалии, мин', factor_value_ru(saa), True),
            (fluence_label(flu), factor_value_ru(flu), False),
            ('метеороиды, попаданий на 1 м²', factor_value_ru(mm), False)]
    if mode == 'live':
        rows.append(('GOES ≥10 МэВ, pfu', factor_value_ru(goes), False))
    else:
        rows.append(('прогноз Kp NOAA, макс. в окне', factor_value_ru(kpf), False))
    kv = ''.join('<div class="k">%s</div><div class="v%s">%s</div>' % (esc(k), ' big' if big else '', esc(v)) for k, v, big in rows)
    conds = ''.join('<div class="cond%s">%s</div>' % (' crit' if 'приоритетное' in r else '', esc(screen_text(_short_reason(r))))
                    for r in reasons[:4])
    if len(reasons) > 4:
        conds += '<div class="small">… ещё %d</div>' % (len(reasons) - 4)
    if not reasons:
        conds = '<div class="cond none">Условий проверки нет по данным до отсечки</div>' if mode == 'history_forecast' \
            else '<div class="cond none">Условий проверки нет</div>'
    cov = ' '.join(pill('%s: %s' % (MECH_RU.get(m.mechanism_id, m.mechanism_id), COV_RU[m.coverage.value]), COV_KIND[m.coverage.value])
                   for m in a.mechanisms if m.mandatory or m.coverage.value != 'none')
    why = coverage_reasons(a)
    why_html = ('<div class="covwhy">почему неполное — %s</div>' % esc(screen_text('; '.join(why)))) if why else ''
    note = ('<div class="wnote">минуты в аномалии: |B| &lt; %s нТл, шаг трассы 1 мин — формула (3)</div>' % esc(nbsp_thousands(saa_thr_nT))) \
        if saa_thr_nT is not None else ''
    return ('<div class="%s"><div class="wh"><div><div class="wt">Окно %d%s</div><div class="wtime">%s</div></div>%s</div>'
            '<div class="kv">%s</div>%s%s<div class="cov">покрытие: %s</div>%s</div>'
            % (cls, i + 1, ' · предпочтительное' if best else '', esc(win_span(a.window)), pill(*state), kv, note, conds, cov, why_html))


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


def source_name_ru(sid: str, mode: str | None = None) -> str:
    """Имя источника для экрана и выгрузки. Для выпусков внешнего прогноза NOAA к имени
    добавляется происхождение выпуска по режиму: живой выпуск / до отсечки / из архива."""
    name = SOURCE_RU.get(sid, sid)
    if sid == 'noaa_forecast_kp_forecast' and mode in RELEASE_BY_MODE_RU:
        return '%s (%s)' % (name, RELEASE_BY_MODE_RU[mode])
    return name


# давность в строке слоя источников бывает дробной и с оборотом «данных»: «давность данных 1888,7 мин».
# Прежнее выражение ловило только целое без «данных», и на экране оставалось «кеш» без числа (R4-27).
_TLE_AGE_RE = re.compile(r'давность(?:\s+данных)?\s+(\d+)(?:[.,]\d+)?\s*мин')


def tle_origin(tle_fetch_status: str | None) -> str:
    """«получено живьём с celestrak.org» / «кеш, давность 31 ч» / «снимок репозитория, давность 31 ч».
    Давность печатается в одних единицах с приборной полосой (age_ru), а не в минутах и часах вперемешку."""
    s = tle_fetch_status or ''
    m = re.search(r'живьём с ([\w.\-]+)', s)
    if m:
        return 'живьём с ' + m.group(1)
    m2 = _TLE_AGE_RE.search(s)
    age = (', ' + age_ru(int(m2.group(1)))) if m2 else ''
    if 'снимок репозитория' in s:
        return 'снимок репозитория' + age
    if 'кеш' in s:
        return 'кеш' + age
    if 'воспроизведение' in s:
        return 'из сохранённого расчёта'
    return s or '—'


def verification_ru(summary: str, had_conditions: bool) -> str:
    """Сводка проверки после отсечки: начало зависит от того, ставилось ли условие (PLAN 3.3, п. 1).

    Слой расчёта печатает «условие поставлено в 12:00Z; факт: …» безусловно, и на тихой дате
    эта фраза противоречит карточкам обоих окон («условий проверки нет»). Здесь она заменяется
    на честную: условий не ставилось. Само сопоставление с фактом не меняется."""
    s = screen_text(str(summary or ''))
    if had_conditions:
        return s
    head_, sep, tail = s.partition('; факт: ')
    if not sep or not head_.startswith('условие поставлено'):
        return s
    return 'условий проверки на отсечку не ставилось; факт: ' + tail


# ---------------------------------------------------------------- вкладка «Методика» (PROPOSAL_A п. 5)
# Внутри st.latex кириллицы нет: KaTeX подставляет шрифты без кириллических глифов и на части
# браузеров рисует пустые прямоугольники. Все пояснения — обычным текстом под формулой.
METHOD_BLOCKS = [
    {'no': 1, 'group': 'Захваченные протоны',
     'title': 'Флюенс за окно',
     'latex': r'\Phi(\ge E_{min}) \;=\; \sum_{i} J_i\,\Delta t, \qquad '
              r'J_i \;=\; \int_{E_{min}}^{E_{max}} f\!\left(L_i,\; B_i/B_{0,i},\; E\right)\,dE',
     'symbols': 'Φ — флюенс за окно, част./см²; J — интегральный всенаправленный поток выше E_min, см⁻²·с⁻¹; '
                'Δt = 1 мин — шаг трассы; L и B/B_0 — магнитные координаты точки трассы; сумма берётся по точкам окна.',
     'source': 'ОСТ 134-1044-2007, прил. А, табл. А.2.1 (минимум солнечной активности). Единицы — из вводного текста '
               'приложения: спектры всенаправленного потока, см⁻²·с⁻¹·МэВ⁻¹, без домножения на 4π.',
     'limits': 'Хвост выше E_max = 300 МэВ отброшен и объявлен. Вне сетки L = 1,14…9 модели нет — это «нет модели», '
               'а не нуль; выше точки отражения поток физически нулевой.'},
    {'no': 2, 'group': 'Захваченные протоны',
     'title': 'Интегрирование по энергии между узлами таблицы',
     'latex': r'f(E) = a\,E^{\,b}, \qquad b = \frac{\ln\left(f_{k+1}/f_k\right)}{\ln\left(E_{k+1}/E_k\right)}',
     'symbols': 'f — дифференциальный поток в узле таблицы; E_k и E_{k+1} — соседние узлы энергетической сетки; '
                'между узлами принят степенной закон, интеграл берётся аналитически.',
     'source': 'ОСТ 134-1044-2007, прил. А: сетка энергий и значения потоков; способ интерполяции — степенной, '
               'как принято для спектров захваченных частиц.',
     'limits': 'Интерполяция только внутри сетки; экстраполяция за крайний узел не делается.'},
    {'no': 3, 'group': 'Магнитные координаты',
     'title': 'L-оболочка и B/B₀ эксцентричного диполя',
     'latex': r"L = \frac{r'}{\cos^{2}\lambda'}, \qquad r' = \frac{\left|\mathbf{r} - \mathbf{d}\right|}{R_E}, "
              r"\qquad \frac{B}{B_0} = \frac{\left|\mathbf{B}\right|_{IGRF}}{B_{eq}\,L^{-3}}",
     'symbols': "r′ — расстояние точки от смещённого центра диполя в радиусах Земли; λ′ — геомагнитная широта от "
                "смещённой оси; d — смещение центра диполя (603 км, 0,095 R_E на май 2024; вектор смещения печатается в выгрузке расчёта, файл факторов); |B| — полное поле IGRF в точке трассы; "
                "минуты в аномалии считаются по порогу |B| из настроек, шаг трассы 1 мин.",
     'source': 'Fraser-Smith A. C. Centered and eccentric geomagnetic dipoles and their poles. Rev. Geophys. 25(1), 1987; '
               'коэффициенты IGRF — те же файлы, что у модуля орбиты.',
     'limits': 'Объявленное приближение до трассировки силовых линий: L и B_0 — от смещённого диполя, |B| — полное IGRF; '
               'отношение B/B_0 < 1 помечается, а не обрезается. Центральный диполь для таблиц ОСТ не годится: в ядре '
               'аномалии он даёт L ≈ 1,07 — ниже первой строки сетки (1,14), и флюенс обращается в нуль на всей трассе '
               '(проверено 19.09.2026). Признак аномалии — порог |B| из настроек, не официальная граница области.'},
    {'no': 4, 'group': 'Магнитные координаты',
     'title': 'Жёсткость геомагнитного обрезания',
     'latex': r'R(T) = \frac{\sqrt{T^{2} + 2\,T\,m_p c^{2}}}{Z\,e}, \qquad R_c(\mathbf{r}) < R(T)',
     'symbols': 'T — кинетическая энергия протона, МэВ; m_p c² = 938,272 МэВ; R — жёсткость, ГВ; '
                'R_c — вертикальная жёсткость обрезания в точке трассы; частица канала доступна там, где R_c ниже R.',
     'source': 'Определение жёсткости — общепринятое; m_p c² — CODATA 2018; R_c — расчёт модуля орбиты '
               '(центральный наклонный диполь), метод подписан отдельно от формулы (3).',
     'limits': 'Обрезание спокойных условий: при буре Kp ≥ 7 оно снижается, и это не моделируется. Поток GOES на станцию '
               'не переносится: GOES меряет на геостационарной орбите, где обрезания нет.'},
    {'no': 5, 'group': 'Метеороиды',
     'title': 'Поток природных метеороидов по Grün',
     'latex': r'F_{met,0}(m) = 3{,}15576\cdot 10^{7}\,\left(F_1 + F_2 + F_3\right)',
     'symbols': 'm — масса частицы, г; нижняя масса модели m ≥ 10⁻³ г; F_met,0 — поток на 1 м² в год в свободном '
                'пространстве; F_1, F_2, F_3 — три слагаемых распределения Grün.',
     'source': 'ECSS-E-ST-10-04C Rev.1 (15.06.2020), формула (10-1), п. 10.2.2.2a.',
     'limits': 'Одна модель на всю трассу; потоки метеорных потоков конкретной даты не включены (п. 10.2.2.2c) — '
               'календарь IMO даёт только признак активности.'},
    {'no': 6, 'group': 'Метеороиды',
     'title': 'Поправки на орбиту и ожидаемое число попаданий',
     'latex': r'F = F_0\,\bar{G}\,s_f\,K, \qquad N = A\int_{t_0}^{t_1} F\,dt',
     'symbols': 'Ḡ — гравитационное усиление, s_f — экранирование Землёй, K — множитель средней скорости; '
                'A = 1 м² — односторонняя случайно кувыркающаяся пластина; N — ожидаемое число попаданий за окно, '
                'интеграл берётся по фактической высоте трассы, концы окна включены.',
     'source': 'ECSS-E-ST-10-04C Rev.1: поправки (C-25) Annex C.1.5, множители Table J-6, число попаданий (10-2) п. 10.2.5a.',
     'limits': 'Неопределённость потока ×0,33…3 (п. J.2.3.2); техногенный мусор не включён. При равной длительности окна '
               'различаются по этой линии меньше чем на 0,01 %: роль линии — охват и абсолютная оценка, а не выбор окна.'},
    {'no': 7, 'group': 'Метеороиды',
     'title': 'Вероятность хотя бы одного попадания',
     'latex': r'P_{\ge 1} = 1 - e^{-N}',
     'symbols': 'N — ожидаемое число попаданий из формулы (6); распределение принято пуассоновским.',
     'source': 'ECSS-E-ST-10-04C Rev.1, формула (10-3).',
     'limits': 'Это попадания в опорную пластину 1 м², а не в космонавта и не пробой скафандра: '
               'валидированного уравнения пробоя многослойной ткани у нас нет, и вероятность разгерметизации не считается.'},
    {'no': 8, 'group': 'Правило выбора окна',
     'title': 'Отношения «не хуже» и «лучше»',
     'latex': r'A \preceq B \iff m_A \le m_B + \delta_m \;\wedge\; \Phi_A \le \rho\,\Phi_B, \qquad '
              r'A \prec B \iff \left(A \preceq B\right) \wedge \left( m_A < m_B - \delta_m \;\vee\; \rho\,\Phi_A < \Phi_B \right)',
     'symbols': 'm — минуты в аномалии, Φ — флюенс за окно; «не хуже» требует обеих величин, «лучше» — строгого выигрыша '
                'хотя бы по одной. Если минуты и флюенс указывают на разные окна, вердикт — компромисс без победителя.',
     'source': 'договор команды, раздел 4 — правило рекомендации; пять шагов правила печатаются на экране в строке «Правило».',
     'limits': 'Это правило команды, а не эксплуатационная норма: шкалы NOAA сами по себе ВКД не запрещают. '
               'Окна с условием проверки из автоматического выбора исключаются — политика прототипа.'},
    {'no': 9, 'group': 'Правило выбора окна',
     'title': 'Допуск равнозначности',
     'latex': r'\delta_m = \max\!\left(\delta_{min},\; \operatorname{span}_{thr}\left[m_2 - m_1\right]\right), \qquad '
              r'\rho = \max\!\left(1{,}5,\; \operatorname{span}_{E}\left[\Phi_2/\Phi_1\right]\right)',
     'symbols': 'δ_m — допуск по минутам: разброс РАЗНОСТИ минут двух лучших окон по оси порога аномалии; '
                'ρ — допуск по флюенсу: разброс отношения флюенсов по оси канала E_min, не меньше 1,5.',
     'source': 'договор команды, раздел 4; сетка порогов и разбросы печатаются во вкладке «Устойчивость и нормы».',
     'limits': 'Допуском не может быть разброс абсолютных минут одного окна: порог сдвигает оба окна синфазно, '
               'и такой допуск объявлял бы равнозначными 54 и 86 мин.'},
    {'no': 10, 'group': 'Нормы как контекст', 'pro': True,
     'title': 'Предельно допустимая доза за полёт',
     'latex': r'G_{lim}(T) = 0{,}05 + 4\left(1 - e^{-T/72}\right), \qquad g_{h} = \frac{G_{lim}(T)}{720\,T}',
     'symbols': 'T — длительность экспедиции, мес; G_lim — предельно допустимая доза за полёт, Зв; '
                'g_h — контрольная часовая доза, Зв/ч; месяц принят равным 720 ч.',
     'source': 'ГОСТ 25645.215-85, пп. 2.2 и 2.3; формула сверена с таблицей стандарта, 8 строк из 8.',
     'limits': 'Сервис не вычисляет дозу человека. Это справочный контекст рядом с показателями среды, а не вердикт: '
               'для дозы нужны модели защиты станции, скафандра и ткани, которых в обязательной части нет.'},
]
# Пороги условий: на каждый — источник. Печатаются таблицей под формулами (8) и (9).
RULE_THRESHOLDS = [
    {'условие': 'протонное событие, наблюдение или уведомление', 'порог': '≥ 1000 pfu (S3)',
     'источник': 'NOAA SWPC, Space Weather Scales', 'класс': 'приоритетная проверка'},
    {'условие': 'протонное событие', 'порог': '≥ 10 pfu (S1)',
     'источник': 'NOAA SWPC, Space Weather Scales', 'класс': 'условие проверки'},
    {'условие': 'геомагнитная буря', 'порог': 'Kp ≥ 7 (G3)',
     'источник': 'NOAA SWPC, Space Weather Scales', 'класс': 'условие проверки'},
    {'условие': 'сближение с расчётным моментом внутри окна', 'порог': 'качественно, без порога',
     'источник': 'CelesTrak SOCRATES', 'класс': 'ручная оценка'},
]
RULE_POLICY = ('Это правило команды, а не эксплуатационная норма. Шкалы NOAA сами по себе не запрещают ВКД; '
               'исключение помеченного окна из автоматического выбора — политика прототипа, её устойчивость '
               'проверена на сетке порогов.')
# Ключевые слова карточки → номер формулы. Карточка ссылается на формальную запись по номеру (PROPOSAL_A п. 5).
_FORMULA_KEYS = [(('флюенс', 'захвач', 'ост 134'), '(1) и (2)'), (('аномали', 'минут', '|b|', 'l-оболоч'), '(3)'),
                 (('обрезан', 'жёсткост', 'жесткост'), '(4)'), (('метеороид', 'попадан', 'grün', 'grun'), '(5)–(7)'),
                 (('сравнен', 'предпочт', 'равнознач', 'правил', 'допуск'), '(8) и (9)')]


def formula_ref(*texts) -> str | None:
    """Номер формулы вкладки «Методика» по тексту карточки: «формула (3)». Совпадения нет — None."""
    s = ' '.join(str(t or '') for t in texts).lower()
    for keys, no in _FORMULA_KEYS:
        if any(k in s for k in keys):
            return no
    return None


# ---------------------------------------------------------------- реестр источников (вкладка «Данные»)
# Единица, частота выпуска, что считается временем публикации, лицензия и ограничение — по одной записи
# на источник. Лицензия печатается только там, где она записана в самом ответе службы или в документе;
# где не записана — так и сказано, а не додумано.
SOURCE_REGISTRY = {
    'noaa_swpc_goes': {'величина': 'интегральный поток протонов ≥10 МэВ', 'единица': 'pfu = част./(см²·с·ср)',
                       'частота': 'лента 1 мин, выпуск примерно раз в 5 мин',
                       'публикация': 'время измерения, взятое из самой записи наблюдения',
                       'лицензия': 'в ответе службы не указана; условия — на сайте NOAA SWPC',
                       'ограничение': 'измерение на геостационарной орбите; на станцию не переносится без геомагнитного обрезания'},
    'gfz_kp': {'величина': 'планетарный индекс Kp', 'единица': 'безразмерный',
               'частота': '3-часовые интервалы',
               'публикация': 'конец 3-часового интервала; окончательный ряд выходит позже предварительного',
               'лицензия': 'CC BY 4.0 — из поля meta.license ответа службы',
               'ограничение': 'планетарный индекс, а не локальная величина на трассе'},
    'orbit': {'величина': 'положение МКС на трассе', 'единица': 'широта и долгота — град, высота — км',
              'частота': 'TLE — по мере выпуска (несколько раз в сутки), OEM — по мере выпуска NASA/JSC',
              'публикация': 'TLE — эпоха элементов; OEM — дата создания файла (публичная доступность в 2024 не доказана)',
              'лицензия': 'в ответе службы не указана; условия — на сайтах CelesTrak и NASA',
              'ограничение': 'манёвры не предсказываются; возраст элементов — инженерное ограничение, не оценка ошибки положения'},
    'donki_archive': {'величина': 'уведомления и карточки событий (протонные события, бури, выбросы)',
                      'единица': 'текст уведомления, Kp — безразмерный, энергии — МэВ',
                      'частота': 'по мере событий',
                      'публикация': 'время выпуска уведомления, объявленное в самом сообщении',
                      'лицензия': 'в ответе службы не указана; условия — на сайте NASA DONKI',
                      'ограничение': 'вложенные значения карточек без собственного времени публикации в строгий режим не идут'},
    'ost1044_belts': {'величина': 'спектры всенаправленного потока захваченных протонов',
                      'единица': 'см⁻²·с⁻¹·МэВ⁻¹', 'частота': 'таблица стандарта, не обновляется',
                      'публикация': 'ОСТ 134-1044-2007, дата издания стандарта',
                      'лицензия': 'отраслевой стандарт, печатный документ', 'ограничение': 'минимум солнечной активности; сетка L = 1,14…9, E ≤ 300 МэВ'},
    'ecss_grun': {'величина': 'поток природных метеороидов', 'единица': '1/(м²·год)',
                  'частота': 'модель стандарта, не обновляется',
                  'публикация': 'ECSS-E-ST-10-04C Rev.1, 15.06.2020',
                  'лицензия': 'стандарт ECSS, публикуемый документ',
                  'ограничение': 'неопределённость ×0,33…3; техногенный мусор и потоки конкретной даты не включены'},
}
_REGISTRY_FORECAST = {'величина': 'внешний прогноз NOAA', 'единица': 'Kp — безразмерный, вероятности — %',
                      'частота': 'выпуск примерно раз в сутки',
                      'публикация': 'время выпуска бюллетеня (заголовок :Issued:)',
                      'лицензия': 'в ответе службы не указана; условия — на сайте NOAA SWPC',
                      'ограничение': 'суточные вероятности относятся к суткам, а не к окну ВКД, и не пересчитываются'}
SOURCE_REGISTRY['noaa_swpc_3day_forecast'] = dict(_REGISTRY_FORECAST)
# В исторических режимах поток GOES берётся не с живой ленты NOAA, а из численного архива
# NASA iSWA за 2024 (5-минутные средние). Частота, что считается публикацией и ограничение
# у него свои, поэтому реестр печатает их отдельной записью, а не подписью живой ленты.
_REGISTRY_GOES_ARCHIVE = {'величина': 'интегральный поток протонов ≥10 МэВ',
                          'единица': 'pfu = част./(см²·с·ср)',
                          'частота': '5-минутные средние за 2024 год',
                          'публикация': 'время измерения из записи архива; историческое время публикации именно '
                                        'этой версии не доказано — в строгий режим архив не идёт',
                          'лицензия': 'в ответе службы не указана; условия — на сайтах NASA iSWA и NOAA SWPC',
                          'ограничение': 'измерение на геостационарной орбите; на станцию не переносится '
                                         'без геомагнитного обрезания'}


def registry_row(sid: str, origin: str | None = None) -> dict:
    """Строка реестра источников по идентификатору; для выпусков NOAA — общая запись прогноза.
    origin — строка происхождения из снимка: по ней различаются живая лента GOES и архив iSWA."""
    if sid.startswith('noaa_forecast_'):
        return dict(_REGISTRY_FORECAST)
    if sid == 'noaa_swpc_goes' and 'iSWA' in (origin or ''):
        return dict(_REGISTRY_GOES_ARCHIVE)
    return dict(SOURCE_REGISTRY.get(sid, {'величина': '—', 'единица': '—', 'частота': '—', 'публикация': '—',
                                          'лицензия': '—', 'ограничение': '—'}))


# Причины исключения записи: группируемая формулировка (конкретные даты и номера убраны)
# и отнесение к отсечке или к содержанию записи. Русский текст причины приходит из app.compute
# (EXCLUDED_RU), здесь он только приводится к виду строки таблицы.
def excl_reason_ru(reason: str) -> str:
    """Причина исключения в виде, пригодном для группировки: без конкретных дат, смысл сохранён."""
    r = (reason or '').strip()
    low = r.lower()
    if 'момент наблюдения' in low:
        return 'момент наблюдения позже отсечки, хотя запись опубликована раньше'
    if 'неизвестно' in low:
        return 'времени публикации нет'
    if 'после отсечки' in low or 'позже отсечки' in low:
        return 'опубликовано после отсечки'
    if low.startswith('запись не разобрана'):
        return 'запись не разобрана'
    return r or '—'


def excl_group_ru(reason: str) -> str:
    """Отношение причины к отсечке: экран не должен называть отсечкой то, что ею не является."""
    low = (reason or '').lower()
    if low.startswith('запись не разобрана') or 'не разобран' in low:
        return 'запись не разобрана'
    if 'отсечк' in low or 'время публикации' in low or 'времени публикации' in low or 'доступност' in low:
        return 'время публикации или доступность'
    return 'содержание записи'


# Карта покрытия каналов адаптера истории (A2). Ключ — «источник:канал»; обе половины
# переводятся по точному совпадению, непереведённая печатается как есть — придумывать
# перевод причине, которой мы не знаем, нельзя.
_COV_SOURCE_RU = {'goes_p_ge10MeV': 'GOES ≥10 МэВ (архив NASA iSWA)', 'gfz_kp_archive': 'Kp, окончательный ряд GFZ',
                  'kp': 'Kp (сводный канал)', 'donki': 'уведомления NASA DONKI',
                  'noaa_ngdc_3day_forecast': 'трёхсуточный прогноз NOAA SWPC',
                  'noaa_ngdc_daypre': 'суточный прогноз NOAA SWPC'}
_COV_CHANNEL_RU = {'observations': 'наблюдения', 'notifications': 'уведомления', 'noaa_kp': 'прогноз Kp',
                   'r1_r2_probability': 'вероятность R1–R2 за сутки', 'r3_or_greater_probability': 'вероятность R3+ за сутки',
                   's1_or_greater_probability': 'вероятность S1+ за сутки', 'f107': 'поток F10.7',
                   'high_latitude_k': 'K в высоких широтах', 'mid_latitude_k': 'K в средних широтах',
                   'high_active_probability': 'вероятность активности, высокие широты',
                   'mid_active_probability': 'вероятность активности, средние широты',
                   'high_minor_storm_probability': 'вероятность слабой бури, высокие широты',
                   'mid_minor_storm_probability': 'вероятность слабой бури, средние широты',
                   'high_major_severe_storm_probability': 'вероятность сильной бури, высокие широты',
                   'mid_major_severe_storm_probability': 'вероятность сильной бури, средние широты',
                   'whole_disk_m_flare_probability': 'вероятность вспышки класса M по диску',
                   'whole_disk_x_flare_probability': 'вероятность вспышки класса X по диску',
                   'whole_disk_proton_probability': 'вероятность протонного события по диску'}
COV_STATUS_RU = {'full': 'полное', 'partial': 'частичное', 'none': 'нет', 'missing': 'записи нет',
                 'invalid': 'запись непригодна', 'inventory_only': 'только перечень записей'}
COV_REASON_RU = {
    'union_of_GFZ_three_hour_cells; final_data_for_review_only':
        'объединение 3-часовых интервалов GFZ; окончательные данные — только для разбора после факта',
    'verified GFZ cells and explicitly reported notification intervals':
        'проверенные интервалы GFZ и интервалы, прямо названные в уведомлениях',
    'no notification is not a declaration of no SEP/GST; ends and monitoring gaps may be unknown':
        'отсутствие уведомления не означает отсутствия события; концы событий и пропуски наблюдения могут быть неизвестны',
    'historic_publication_and_version_availability_not_proven':
        'историческая публикация и доступность именно этой версии не доказаны',
    'historical_publication_not_proven': 'историческая публикация не доказана',
    'only synoptic intervals explicitly reported in admitted notifications':
        'только интервалы, прямо названные в допущенных уведомлениях',
    'numerical historical GOES observations not present; threshold notifications do not replace them':
        'численных исторических наблюдений GOES нет; пороговые уведомления их не заменяют',
    'source_absent': 'источника нет', 'GFZ archive absent': 'архива GFZ нет',
}


def coverage_rows_ru(coverage_map: dict) -> list[dict]:
    """Карта покрытия A2 строками таблицы: источник, канал, состояние, доля горизонта, причина."""
    rows = []
    for key, cell in (coverage_map or {}).items():
        cell = cell or {}
        sid = cell.get('source_id') or str(key).split(':')[0]
        ch = cell.get('channel_id') or str(key).split(':')[-1]
        reason = cell.get('reason')
        rows.append({'источник': _COV_SOURCE_RU.get(sid, SOURCE_RU.get(sid, sid)),
                     'канал': _COV_CHANNEL_RU.get(ch, ch),
                     'покрытие': COV_STATUS_RU.get(cell.get('status'), cell.get('status') or '—'),
                     'доля горизонта': fmt(cell.get('coverage_fraction')),
                     'причина': COV_REASON_RU.get(reason, reason) if reason else '—'})
    return sorted(rows, key=lambda r: (r['покрытие'] == 'полное', r['источник'], r['канал']))


def record_release_ru(rid: str) -> str:
    """Номер выпуска источника из идентификатора записи «source_id:release_id:хеш[:тип]».
    На экране печатается он, а не внутренний ключ с хешем (О5)."""
    parts = (rid or '').split(':')
    return parts[1] if len(parts) > 1 and parts[1] else (rid or '—')


def robustness_gain_ru(rec, rob, windows_ru_iso: dict, base_thr: float, base_e: float) -> str:
    """О7: польза сетки порогов и допуска — одной строкой через сравнение (R4-9).

    Сравниваются два ответа на одних и тех же данных: (а) как если бы сервиса устойчивости не
    было — один порог, нулевой допуск, лучшее окно по ранжированию; (б) итоговый вердикт на всей
    сетке с допуском равнозначности. Оба числа берутся из `Robustness`, ничего не досчитывается."""
    ranking = dict(getattr(rob, 'ranking_by_grid', None) or {})
    if not ranking or (base_thr, base_e) not in ranking:
        return ''
    base = ranking[(base_thr, base_e)]          # только базовая ячейка: подменять её соседней нельзя
    # причину отсутствия выбора при нулевом допуске здесь не называем — слой устойчивости её
    # не сообщает, а придумывать её нельзя
    naive = ('предпочтительным было бы названо %s' % grid_cell_ru(base, windows_ru_iso)) if base \
        else 'предпочтительное окно не было бы названо'
    final = ('предпочтительное окно %s' % windows_ru_iso.get(rec.preferred.start_utc, '')) if rec.preferred is not None \
        else '«%s»' % VERDICT_TITLE.get(rec.verdict, rec.verdict).lower()
    same = base == (rec.preferred.start_utc.isoformat() if rec.preferred is not None else None)
    return ('Что даёт проверка на сетке: без неё (один порог %s нТл, канал от %s МэВ, нулевой допуск) %s; '
            'на сетке порогов и с допуском равнозначности вердикт — %s. Ответ без них и с ними %s.') % (
        nbsp_thousands(base_thr), fmt(float(base_e)), naive, final,
        'совпадает — сетка и допуск подтверждают ответ, а не создают его' if same else 'различается')


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
