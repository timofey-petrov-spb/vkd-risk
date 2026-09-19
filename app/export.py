# -*- coding: utf-8 -*-
"""Выгрузка расчёта единым снимком (постановка, «Интерфейс и форма результата»; Т8).

Архив содержит: report.md (читаемо, самостоятельный документ для руководителя работ),
request.json, trajectory_meta.json, factors.json, recommendation.json, cards.json
(карточки по окнам), sources.json, verification.json (прогноз из прошлого: что
наблюдалось после отсечки), manifest.json и raw/<record_id>.json — сырые записи,
из которых восстанавливается расчёт. Сведения в выгрузке совпадают с интерфейсом:
и то и другое строится из одного словаря-снимка, собранного один раз за расчёт.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timedelta
from typing import Any

from types import SimpleNamespace

from app.ui import (EVENT_KIND_RU, STRICT_RU, dates_ru, factor_value_ru, frac_ru, phrase_ru, raw_record,
                    record_url, robustness_line_ru, rule_ru, screen_text, source_name_ru, status_ru, sup, verification_ru)
from vkd.explain.format import fmt_ru, record_ru


def fmt(v, unit: str = '') -> str:
    """Число в отчёте — ЕДИНЫМ правилом ядра (vkd.explain.format.fmt_ru, порог степенной
    записи SCI_MIN), степень надстрочными цифрами (app.ui.sup).

    Отчёт брал форматирование у экрана, а тексты правила и карточек приходили в него уже
    отформатированными ядром. Пока пороги в двух функциях стояли числами по месту, одна и
    та же величина печаталась в одном документе двумя видами: строка правила «флюенс ниже
    (88701 …)» и таблица факторов «1,65·10^6». Теперь правило одно и живёт в ядре."""
    return sup(fmt_ru(v, unit))

VERDICT_TITLE = {
    'preferred': 'Есть предпочтительное окно',
    'equivalent': 'Окна равнозначны по учтённым механизмам',
    'trade_off': 'Компромисс: механизмы указывают на разные окна',
    'all_need_check': 'Все окна требуют проверки аналитиком',
    'insufficient': 'Оснований для рекомендации недостаточно',
}
MECH_RU = {'spaceweather': 'космопогода на траектории', 'mmod_stat': 'статистика метеороидов', 'conjunctions': 'сближения'}
COV_RU = {'full': 'полное', 'partial': 'частичное', 'none': 'нет'}
KIND_RU = {'observation': 'наблюдение', 'external_forecast': 'внешний прогноз', 'own_calculation': 'наш расчёт'}
PRESENCE_RU = {'detected': 'воздействие есть', 'not_detected': 'не выявлено', 'unknown': 'неизвестно'}
# Состояние источника, заданное пользователем, — теми же словами, что в боковой панели экрана
DISABLED_RU = {'off': 'исключён: нет данных', 'cache': 'отказ: только кеш', True: 'исключён: нет данных'}
# ключи переключателей боковой панели → идентификаторы источников снимка
_SRC_KEY_RU = {'goes': 'noaa_swpc_goes', 'kp': 'gfz_kp', 'noaa': 'noaa_swpc_3day_forecast'}
MODE_ID_RU = {'live': 'Текущая обстановка', 'history_review': 'Исторический разбор',
              'history_forecast': 'Прогноз из прошлого'}


def _j(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, default=str)


def _git_sha() -> str | None:
    """SHA коммита для воспроизведения (Т8); None вне репозитория."""
    try:
        import subprocess
        root = __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__)))
        return subprocess.check_output(['git', '-C', root, 'rev-parse', 'HEAD'], stderr=subprocess.DEVNULL, timeout=5).decode().strip()
    except Exception:            # noqa: BLE001
        return None


def _t(s):
    try:
        return datetime.fromisoformat(s) if s else None
    except (TypeError, ValueError):
        return None


def _dt(s, fmt='%d.%m %H:%MZ'):
    t = _t(s)
    return t.strftime(fmt) if t else '—'


def _span_end(from_iso, to_iso) -> str:
    """Конец интервала: «00:00Z» внутри суток и «11.05 00:00Z», когда сутки другие.
    Без даты строка «21:00Z — 00:00Z» читается как интервал назад."""
    a, b = _t(from_iso), _t(to_iso)
    if b is None:
        return '—'
    return b.strftime('%H:%MZ') if (a and a.date() == b.date()) else b.strftime('%d.%m %H:%MZ')


def _win_title(w: dict) -> str:
    """Заголовок окна. Дата конца печатается всегда, когда она отличается от даты начала:
    «25.06 20:00–02:00 UTC (360 мин)» читается как ошибка — из отчёта не видно, это 6 часов
    вперёд или 18 назад (находка четвёртого круга на пресете «Тихая дата»)."""
    t0 = _t(w['start_utc'])
    t1 = t0 + timedelta(minutes=w['duration_min']) if t0 else None
    end = ('?' if t1 is None else
           t1.strftime('%d.%m %H:%M') if t1.date() != t0.date() else t1.strftime('%H:%M'))
    return 'Окно %s — %s — %s UTC (%d мин)' % (w.get('index', '?'), t0.strftime('%d.%m %H:%M') if t0 else '?',
                                               end, w['duration_min'])


def _as_screen_factor(f: dict):
    """Фактор снимка в том виде, в каком его читают функции экрана.

    Экран получает объект FactorValue с полем `limits_note`, снимок — словарь, где то же
    поле называется `limits`. Без переходника функции экрана молча видели пустое ограничение
    и отвечали не то, что отвечают экрану."""
    return SimpleNamespace(name=f['name'], value=f['value'], unit=f['unit'], limits_note=f.get('limits') or '')


_OBS_TIME_RE = re.compile(r'наблюдение\s+([\d.:\s]+)')


def _value(f: dict) -> str:
    """Число в отчёте — единым правилом ядра (fmt выше): запятая и надстрочная степень.
    Пока правил было два (ядро и экран), одна и та же величина получала два вида в двух
    артефактах одного расчёта.

    Правило «значения нет» — тоже общее с экраном, а не своё: наблюдение, горизонт которого не
    покрывает окно ни на одну минуту, характеристикой окна не является, и экран печатает прочерк
    (app.ui.factor_value_ru). Отчёт печатал в этом месте число и через две строки сам себе
    противоречил строкой «GOES: наблюдение 05:55Z покрывает 0 % окна» (пятый круг). Решение
    принимает та же функция экрана — правило не повторяется в двух местах и не может разойтись."""
    if f['value'] is None:
        # «поток протонов GOES ≥10 МэВ: — — наблюдение» читалось как сломанная строка:
        # прочерк-значение и тире-разделитель шли подряд (находка четвёртого круга)
        return 'значение не определено'
    if factor_value_ru(_as_screen_factor(f), f['unit']) == '—':
        m = _OBS_TIME_RE.search(dates_ru(f.get('limits') or ''))
        return 'значение окна не определено: наблюдение%s не покрывает окно' % ((' ' + m.group(1).strip()) if m else '')
    if f['name'].startswith('Kp') or f['name'].startswith('прогноз Kp'):
        return 'Kp %s' % fmt(f['value'])
    return fmt(f['value'], f['unit'])


def _robustness_line(S: dict) -> str:
    """Устойчивость выбора в отчёте — ДОСЛОВНО та фраза, что стоит на оперативном экране
    под вердиктом (app.ui.robustness_line_ru, блок «cov» app/main.py).

    Отчёт печатал вместо неё профессиональное основание допуска целиком, и в нём стояло
    «порядок окон при нулевом допуске не определён … ВЫБОР МЕНЯЕТСЯ», тогда как сам отчёт
    двадцатью строками выше объявлял «Есть предпочтительное окно: окно 2». Одно утверждение
    опровергало другое в одном документе (пятый круг). Числа берутся из снимка: пороги — из
    запроса, исходы ячеек — из S['robustness']; ничего не пересчитывается.
    """
    rob = S.get('robustness') or {}
    th = (S.get('request') or {}).get('thresholds') or {}
    thr_nT, e_min = th.get('saa_B_threshold_nT'), th.get('e_min_MeV')
    if not rob or thr_nT is None or e_min is None:
        # объявляем ограничение, а не выдумываем фразу: без сетки и порогов устойчивость не проверена
        return 'Устойчивость выбора: сетка порогов в снимке не сохранена — устойчивость не проверена.'
    rec = SimpleNamespace(verdict=S['recommendation']['verdict'],
                          preferred=(SimpleNamespace(start_utc=_t(S['recommendation']['preferred']))
                                     if S['recommendation'].get('preferred') else None))
    return 'Устойчивость выбора: ' + screen_text(robustness_line_ru(rec, S, thr_nT, e_min))


def _tolerance_line(r: dict) -> str:
    """Основание допуска равнозначности — только те его части, которые говорят о ДОПУСКЕ.

    `app.compute.tolerance_caption` собирает подпись из четырёх частей: допуск по минутам,
    допуск по флюенсу, порядок окон на сетке и вывод о выборе. Последние две — об устойчивости,
    и о ней отчёт печатает отдельную строку словами экрана; здесь они повторялись третьим
    сообщением об одном и том же, да ещё в профессиональной формулировке. Части разделены
    «; », каждая часть про допуск начинается словом «допуск» — отбор идёт по этому признаку,
    а не по номеру части. Через screen_text, а не frac_ru: иначе в отчёте стояло «22000 нТл»,
    а на экране «22 000 нТл» — одна и та же величина в двух видах (пятый круг).
    """
    parts = [p.strip() for p in str(r.get('tolerance_basis') or '').split('; ') if p.strip().startswith('допуск')]
    return screen_text('; '.join(parts)) if parts else 'основание допуска в снимке не сохранено'


def _mode_id(S: dict) -> str | None:
    """Идентификатор режима снимка: в старых снимках лежит только русское имя."""
    mid = S.get('mode_id')
    if mid:
        return mid
    for k, v in MODE_ID_RU.items():
        if v == S.get('mode'):
            return k
    return None


def _record_links(ids, raw_records: dict, n: int = 5) -> list[str]:
    """Адреса первоисточников для перечня записей — тем же порядком полей, что на экране (О4)."""
    out = []
    for rid in list(ids)[:n]:
        u = record_url(raw_record(raw_records, rid))
        if u and u not in out:
            out.append(u)
    return out


def _source_link(sid: str, raw_records: dict, src: dict | None = None) -> str | None:
    """Адрес первоисточника источника: первая его запись, у которой адрес есть.

    Ключ снимка («donki_archive», «noaa_forecast_kp_forecast») и идентификатор записи
    («nasa_donki_notification:…», «noaa_ngdc_3day_forecast:…») — разные имена, и поиск по
    ключу не находил НИ ОДНОГО адреса: колонка «первоисточник» была пустой во всех строках
    отчёта, тогда как на экране адреса стояли (находка четвёртого круга). Поэтому адрес
    ищется по перечню записей источника (S['sources'][k]['record_ids']), а поиск по имени
    ключа остаётся только запасным — для снимков, собранных прежними версиями.
    """
    if (src or {}).get('url'):
        return src['url']
    for rid in ((src or {}).get('record_ids') or []):
        u = record_url(raw_record(raw_records, str(rid)))
        if u:
            return u
    for rid, rec in (raw_records or {}).items():
        if str(rid) == sid or str(rid).startswith(sid + ':'):
            u = record_url(rec)
            if u:
                return u
    return None


def _fold_records(ids: list, n: int = 3) -> str:
    """Записи по-русски — номером выпуска источника, как их называет экран.
    Машинные ключи с хешами остаются в manifest.json и в именах файлов raw/."""
    if not ids:
        return ''
    return ', '.join(record_ru(i) for i in ids[:n]) + (
        ' … всего %d (полный список — cards.json, raw/)' % len(ids) if len(ids) > n else '')


def report_md(S: dict, raw_records: dict[str, Any] | None = None) -> str:
    """Отчёт для человека. Говорит теми же словами, что экран: идентификаторы источников и
    состояний переведены, вердикт по-русски (машинный код остаётся в recommendation.json),
    числа и степени — через тот же форматер, что на экране. Машинное происхождение (хеши,
    идентификаторы записей, версии) остаётся в manifest.json, sources.json и raw/."""
    r = S['recommendation']
    req = S['request']
    tm = S['trajectory_meta']
    raw_records = raw_records or {}
    mode_id = _mode_id(S)
    L = ['# ВКД-Риск: расчёт %s' % _dt(S['computed_utc'], '%Y-%m-%d %H:%MZ'),
         '', 'Режим: %s. Версия алгоритма: %s. Отсечка публикации: %s.%s' % (
             S['mode'], S['algorithm_version'], _dt(req.get('cutoff_utc'), '%Y-%m-%d %H:%MZ') if req.get('cutoff_utc') else 'нет',
             ' Сценарий «что если»: значения моделируемые.' if S.get('is_simulated') else ''),
         '', '## Вывод', '',
         '**%s.**' % VERDICT_TITLE.get(r['verdict'], r['verdict']),
         '', 'Правило: %s.' % frac_ru(rule_ru(r['rule'])),
         '', 'Предпочтительное окно: %s.' % (('окно %s, начало %s' % (r.get('preferred_index') or '?', _dt(r['preferred'], '%d.%m %H:%MZ'))) if r['preferred'] else 'нет')]
    if r['reasons']:
        L += ['', 'Что повлияло:'] + ['- ' + phrase_ru(x) for x in r['reasons']]
    if r['missing']:
        L += ['', 'Чего не хватает:'] + ['- ' + phrase_ru(x) for x in r['missing']]
    L += ['', '## Запрос', '',
          '- начало периода поиска: %s UTC' % _dt(req['t0_utc'], '%Y-%m-%d %H:%M'),
          '- длительность ВКД: %d мин; период поиска начала: %d мин' % (req['duration_min'], req['search_min']),
          '- окна-кандидаты: ' + ', '.join('окно %d с %s' % (i + 1, _dt(w)) for i, w in enumerate(req['windows'])),
          '- источники, отключённые пользователем: '
          + (', '.join('%s — %s' % (source_name_ru(_SRC_KEY_RU.get(k, k), mode_id), DISABLED_RU.get(v, str(v)))
                       for k, v in req['disabled'].items() if v) or 'нет'),
          '', '## Траектория', '',
          '- %s' % _traj_line(tm),
          '- строгость: %s; статус: %s' % (STRICT_RU.get(tm.get('strictness'), tm.get('strictness') or '—'),
                                           status_ru(tm.get('status'))),
          '', '## Окна и величины', '']
    for w in S['windows']:
        L.append('### ' + _win_title(w))
        L.append('')
        for m in w['mechanisms']:
            L.append('- **%s** — покрытие %s%s' % (MECH_RU.get(m['id'], m['id']), COV_RU.get(m['coverage'], m['coverage']),
                                                    '; обязательная линия' if m['mandatory'] else '; необязательная линия'))
            for n in m.get('coverage_notes', []):
                L.append('  - не хватает: ' + phrase_ru(n))
            for f in m['factors']:
                L.append('  - %s: %s — %s; покрытие %s, %s' % (f['name'], _value(f), KIND_RU.get(f['kind'], f['kind']),
                                                              COV_RU.get(f['coverage'], f['coverage']), PRESENCE_RU.get(f['presence'], f['presence'])))
            for c in m.get('conditions', []) or [{'text': t, 'severity': 'limiting', 'event_ids': []} for t in m['needs_check']]:
                L.append('  - **условие (%s):** %s' % ('приоритетное' if c['severity'] == 'critical' else 'предупреждение',
                                                       phrase_ru(c['text'].split(';')[0])))
                if c.get('event_ids'):
                    L.append('    - записи: ' + _fold_records(list(c['event_ids'])))
        L.append('')
    L += ['## Почему такой вывод', '', 'Правило сравнения — CONTRACT.md раздел 4: охват → условия → сравнение по каждому механизму → сведение → допуск.', '']
    for k, v in (r.get('per_mechanism') or {}).items():
        L.append('- %s: %s' % (MECH_RU.get(k, k), phrase_ru(v)))
    L += ['', _robustness_line(S), '', 'Допуск равнозначности: %s.' % _tolerance_line(r),
          '', 'Охват: учтено — %s; не учтено — %s.' % (', '.join(S['coverage_declared']), ', '.join(S['coverage_missing'])),
          '', S.get('policy_note', ''), '']
    # условия по окнам с первоисточниками (О4)
    conds = [(w, c) for w in S['windows'] for m in w['mechanisms'] for c in (m.get('conditions') or [])]
    if conds:
        L += ['## Условия по окнам: период, источник, публикация', '']
        for w, c in conds:
            L.append('### %s — %s' % (_win_title(w), phrase_ru(c['text'].split(': ')[0])))
            L.append('')
            L.append('- уровень: %s; класс: %s%s' % (c.get('level') or 'не указан', 'приоритетное' if c['severity'] == 'critical' else 'предупреждение',
                                                     '; моделируемое (сценарий)' if c.get('simulated') else ''))
            iv = c.get('interval_utc') or []
            if iv and iv[0]:
                L.append('- действие: %s — %s' % (_dt(iv[0]), _dt(iv[1]) if len(iv) > 1 and iv[1] else 'конец не объявлен'))
            for s in c.get('sources') or []:
                L.append('- источник: ' + phrase_ru(s))
            if c.get('event_ids'):
                L.append('- записи: ' + _fold_records(list(c['event_ids']), 5))
                for u in _record_links(c['event_ids'], raw_records, 5):
                    L.append('- первоисточник: ' + u)
            L.append('')
    # источники и публикация (Т1/О4): имена и состояния — теми же словами, что на экране,
    # адрес записи — той же функцией record_url; хеши и идентификаторы версий — в manifest.json
    L += ['## Источники и публикация', '',
          '| источник | роль | статус | происхождение | данные на | давность, мин |',
          '|---|---|---|---|---|---|']
    links = []
    for k, v in S['sources'].items():
        if k.startswith('_'):
            continue
        age = v.get('age_min')
        L.append('| %s | %s | %s | %s | %s | %s |' % (
            source_name_ru(k, mode_id), v.get('role', ''),
            status_ru((v.get('status') or '').replace('|', '/')),
            phrase_ru(v.get('origin') or ''),
            _dt(v.get('data_utc'), '%Y-%m-%d %H:%MZ') if v.get('data_utc') else '—',
            fmt(round(float(age))) if age is not None else '—'))
        u = _source_link(k, raw_records, v)
        if u:
            links.append('- %s: %s' % (source_name_ru(k, mode_id), u))
    # Адреса первоисточников — отдельным списком, а не колонкой: адрес архивного запроса
    # длиннее всей строки таблицы и в ячейке не читается. Раньше колонка была пустой во всех
    # строках во всех режимах (искали по ключу снимка, а не по записям) — находка четвёртого круга.
    L += ['', 'Первоисточники записей:', ''] + (links or ['- ни у одной записи снимка нет сетевого адреса'])
    L += ['', 'Идентификаторы записей, хеши и версии выпусков — в `manifest.json` и `sources.json`; '
              'сами записи — в `raw/`.']
    # События горизонта с адресами — те же записи и те же адреса, что в таблице «События и прогнозы»
    # экрана. Без этого перечня множество адресов отчёта было беднее экранного на восемь записей:
    # уведомления, которые условиями не стали (вспышки, выбросы), в отчёт попадали только номерами
    # в манифесте, и открыть первоисточник по отчёту было нельзя (пятый круг).
    used = (S.get('history') or {}).get('events_used') or []
    if used:
        L += ['', '## События и прогнозы, учтённые на горизонте', '',
              'Те же записи, что в таблице событий на экране: тип, номер уведомления, время публикации, адрес.', '']
        for e in sorted(used, key=lambda x: (str(x.get('published_utc') or ''), str(x.get('id')))):
            u = record_url(raw_record(raw_records, e['id']))
            L.append('- %s (%s), публикация %s%s%s'
                     % (EVENT_KIND_RU.get((e.get('kind') or '').upper(), e.get('kind')),
                        record_ru(e['id'], with_kind=False),
                        _dt(e['published_utc']) if e.get('published_utc')
                        else ('нет — моделируемое' if e.get('simulated') else 'нет'),
                        ('; начало %s' % _dt(e['start_utc'])) if e.get('start_utc') else '',
                        (' — ' + u) if u else ''))
        no_url = [e['id'] for e in used if not record_url(raw_record(raw_records, e['id']))]
        if no_url:
            L += ['', 'Без сетевого адреса: %d из %d — адрес записи слоем источников не сохранён, сами записи в `raw/`.'
                  % (len(no_url), len(used))]
    # прогнозы NOAA: «до отсечки» — только там, где отсечка есть
    if S.get('forecasts'):
        L += ['', '## Внешний прогноз NOAA (%s)' % ('выпуски до отсечки' if req.get('cutoff_utc') else 'выпуск с указанием времени публикации'), '']
        for line in S['forecasts']:
            # идентификатор выпуска — 64-значный хеш; он остаётся в manifest.json и sources.json,
            # а человек читает время выпуска (находка четвёртого круга)
            # адрес выпуска — той же функцией record_url, что на экране: экран даёт ссылку
            # «выпуск от … UTC», а отчёт называл то же время без адреса (пятый круг)
            u = record_url(raw_record(raw_records, line['record'])) if line.get('record') else None
            L.append('- %s: %s%s' % (line['label'], line['status_ru'],
                                    ('; выпуск от %s%s' % (_dt(line['published_utc'], '%Y-%m-%d %H:%MZ'),
                                                           (' — ' + u) if u else '')) if line.get('release_id')
                                    else ('; ' + phrase_ru(line['reason']) if line.get('reason') else '')))
    # проверка после отсечки
    ver = S.get('verification')
    if ver:
        # «условие поставлено в 12:00Z» печаталось безусловно и на тихой дате противоречило
        # обеим карточкам окон («условий проверки нет»). Признак берётся из снимка — того же,
        # по которому экран рисует карточки, — а не пересчитывается (находка четвёртого круга).
        had_conditions = any(m.get('needs_check') for w in S['windows'] for m in w['mechanisms'])
        L += ['', '## Проверка после отсечки: что наблюдалось потом (в расчёт не входило)', '',
              verification_ru(ver.get('summary', ''), had_conditions), '']
        if ver.get('kp_obs'):
            L += ['| интервал | Kp наблюдение | происхождение |', '|---|---|---|'] + [
                '| %s — %s | %s | %s |' % (_dt(k['from_utc']), _span_end(k['from_utc'], k['to_utc']), fmt(k['kp']),
                                           (k.get('origin') or '—').replace('|', '/'))
                for k in ver['kp_obs']]
        if ver.get('events'):
            # адрес записи — той же функцией record_url, что на экране: в таблице «События и
            # прогнозы» экрана у каждого уведомления есть ссылка, а отчёт называл те же
            # уведомления по номерам без адресов, и множество адресов отчёта было беднее
            # экранного на восемь записей (пятый круг)
            L += ['', 'События, опубликованные после отсечки на горизонте:']
            for e in ver['events'][:20]:
                u = record_url(raw_record(raw_records, e['id']))
                L.append('- %s (%s), публикация %s%s%s'
                         % (EVENT_KIND_RU.get((e['kind'] or '').upper(), e['kind']), record_ru(e['id'], with_kind=False),
                            _dt(e['published_utc']), (' — ' + phrase_ru(e['note'])) if e.get('note') else '',
                            (' — ' + u) if u else ''))
    L += ['', '## Чего не заявляем', '',
          '- допустимость реального выхода — за уполномоченными специалистами;',
          '- вероятность разгерметизации и попадания в космонавта;',
          '- дозу человека; поток GOES как поток у станции без обрезания.', '']
    return _dates_outside_urls('\n'.join(L))


_URL_RE = re.compile(r'https?://\S+')


def _dates_outside_urls(md: str) -> str:
    """Даты отчёта — тем же видом, что на экране («дд.мм чч:мм»). В отчёте их было два вида
    сразу: 19 дат «05-10 12:14Z» приходили готовыми строками расчёта и 35 печатались через
    _dt (находка четвёртого круга). Сетевые адреса не трогаются: в них есть цифровые группы."""
    out, last = [], 0
    for m in _URL_RE.finditer(md):
        out.append(dates_ru(md[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(dates_ru(md[last:]))
    return ''.join(out)


def _traj_line(tm: dict) -> str:
    """Строка «Траектория». Службу называет ФАКТИЧЕСКИЙ адрес получения, а не имя парсера
    записи: отчёт писал «SGP4 по TLE (celestrak_gp)», а строкой ниже — «SGP4 по TLE
    (api.wheretheiss.at)», то есть называл источником службу, которая данных не отдала
    (находка четвёртого круга). Кодовый идентификатор остаётся в manifest.json."""
    if not tm.get('source_id'):
        return 'орбита недоступна: %s' % tm.get('status')
    if tm.get('method') == 'oem_interp':
        return 'эфемериды OEM NASA/JSC, создан %s, интерполяция; %s; поле %s' % (
            _dt(tm.get('created_utc'), '%d.%m.%Y %H:%MZ'),
            'объявленная реконструкция' if tm.get('is_reconstruction') else 'доказанная публикация до отсечки', tm.get('field_model'))
    url = tm.get('tle_url')
    where = (', адрес получения %s%s' % (url, ' (резервный адрес, основной %s не ответил)' % tm['tle_url_primary_failed']
                                         if tm.get('tle_url_primary_failed') else '')) if url else ''
    return 'SGP4 по элементам орбиты%s, эпоха %s, получен %s; %s; поле %s' % (
        where, _dt(tm.get('epoch_utc'), '%d.%m.%Y %H:%MZ'), _dt(tm.get('fetched_utc'), '%d.%m.%Y %H:%MZ'),
        'объявленная реконструкция' if tm.get('is_reconstruction') else 'без реконструкции', tm.get('field_model'))


def build_zip(S: dict, raw_records: dict[str, Any]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('report.md', report_md(S, raw_records))
        z.writestr('request.json', _j(S['request']))
        z.writestr('trajectory_meta.json', _j(S['trajectory_meta']))
        z.writestr('factors.json', _j(S['windows']))
        z.writestr('recommendation.json', _j(S['recommendation']))
        z.writestr('cards.json', _j(S['cards']))
        z.writestr('sources.json', _j(S['sources']))
        if S.get('verification') is not None:
            z.writestr('verification.json', _j(S['verification']))
        if S.get('observations'):        # ряды наблюдений с единицей, источником и записью (C3)
            z.writestr('observations.json', _j(S['observations']))
        z.writestr('manifest.json', _j({
            'schema_version': S.get('schema_version'), 'algorithm_version': S['algorithm_version'],
            'computed_utc': S['computed_utc'], 'mode': S.get('mode_id', S['mode']),
            'cutoff_utc': S['request'].get('cutoff_utc'), 'is_simulated': S.get('is_simulated', False),
            'source_versions': S['sources'], 'raw_record_ids': sorted(raw_records),
            'effective_config': S.get('effective_config') or {'thresholds': S['request']['thresholds']},
            'excluded_by_cutoff': S.get('history', {}).get('excluded_by_cutoff', []),
            'excluded_by_archive': S.get('history', {}).get('excluded_by_archive', []),
            'events_used': S.get('history', {}).get('events_used', []),
            'catalog_coverage': S.get('history', {}).get('catalog_coverage'),
            # аудит адаптера истории (A2) целиком: покрытие по каналам, версия адаптера,
            # ограничения и метаданные использованных записей — не только список event_ids
            'history_audit': {k: v for k, v in (S.get('history') or {}).items()
                              if k in ('adapter_version', 'coverage_map', 'limitations', 'archive_access',
                                       'source_versions', 'event_facts', 'provider')},
            'robustness': S.get('robustness'), 'git_commit': _git_sha(),
        }))
        for rid, rec in raw_records.items():
            z.writestr('raw/%s.json' % rid.replace('/', '_').replace('#', '_').replace(':', '-'), _j(rec))
    return buf.getvalue()
