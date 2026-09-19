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
import zipfile
from datetime import datetime, timedelta
from typing import Any

from vkd.explain.format import fmt_ru

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


def _win_title(w: dict) -> str:
    t0 = _t(w['start_utc'])
    t1 = t0 + timedelta(minutes=w['duration_min']) if t0 else None
    return 'Окно %s — %s–%s UTC (%d мин)' % (w.get('index', '?'), t0.strftime('%d.%m %H:%M') if t0 else '?',
                                            t1.strftime('%H:%M') if t1 else '?', w['duration_min'])


def _value(f: dict) -> str:
    if f['value'] is None:
        return '—'
    if f['name'].startswith('Kp') or f['name'].startswith('прогноз Kp'):
        return 'Kp %s' % fmt_ru(f['value'])
    return fmt_ru(f['value'], f['unit'])


def _fold_records(ids: list, n: int = 3) -> str:
    if not ids:
        return ''
    return ', '.join(ids[:n]) + (' … всего %d (полный список — cards.json, raw/)' % len(ids) if len(ids) > n else '')


def report_md(S: dict) -> str:
    r = S['recommendation']
    req = S['request']
    tm = S['trajectory_meta']
    L = ['# ВКД-Риск: расчёт %s' % _dt(S['computed_utc'], '%Y-%m-%d %H:%MZ'),
         '', 'Режим: %s. Версия алгоритма: %s. Отсечка публикации: %s.%s' % (
             S['mode'], S['algorithm_version'], _dt(req.get('cutoff_utc'), '%Y-%m-%d %H:%MZ') if req.get('cutoff_utc') else 'нет',
             ' Сценарий «что если»: значения моделируемые.' if S.get('is_simulated') else ''),
         '', '## Вывод', '',
         '**%s** (%s).' % (VERDICT_TITLE.get(r['verdict'], r['verdict']), r['verdict']),
         '', 'Правило: %s.' % r['rule'],
         '', 'Предпочтительное окно: %s.' % (('окно %s, начало %s' % (r.get('preferred_index') or '?', _dt(r['preferred'], '%d.%m %H:%MZ'))) if r['preferred'] else 'нет')]
    if r['reasons']:
        L += ['', 'Что повлияло:'] + ['- ' + x for x in r['reasons']]
    if r['missing']:
        L += ['', 'Чего не хватает:'] + ['- ' + x for x in r['missing']]
    L += ['', '## Запрос', '',
          '- начало периода поиска: %s UTC' % _dt(req['t0_utc'], '%Y-%m-%d %H:%M'),
          '- длительность ВКД: %d мин; период поиска начала: %d мин' % (req['duration_min'], req['search_min']),
          '- окна-кандидаты: ' + ', '.join('окно %d с %s' % (i + 1, _dt(w)) for i, w in enumerate(req['windows'])),
          '- отключённые источники: ' + (', '.join('%s (%s)' % (k, v) for k, v in req['disabled'].items() if v) or 'нет'),
          '', '## Траектория', '',
          '- %s' % _traj_line(tm),
          '- строгость: %s; статус: %s' % (tm.get('strictness'), tm.get('status')),
          '', '## Окна и величины', '']
    for w in S['windows']:
        L.append('### ' + _win_title(w))
        L.append('')
        for m in w['mechanisms']:
            L.append('- **%s** — покрытие %s%s' % (MECH_RU.get(m['id'], m['id']), COV_RU.get(m['coverage'], m['coverage']),
                                                    '; обязательная линия' if m['mandatory'] else '; необязательная линия'))
            for n in m.get('coverage_notes', []):
                L.append('  - не хватает: ' + n)
            for f in m['factors']:
                L.append('  - %s: %s — %s; покрытие %s, %s' % (f['name'], _value(f), KIND_RU.get(f['kind'], f['kind']),
                                                              COV_RU.get(f['coverage'], f['coverage']), PRESENCE_RU.get(f['presence'], f['presence'])))
            for c in m.get('conditions', []) or [{'text': t, 'severity': 'limiting', 'event_ids': []} for t in m['needs_check']]:
                L.append('  - **условие (%s):** %s' % ('приоритетное' if c['severity'] == 'critical' else 'предупреждение', c['text'].split(';')[0]))
                if c.get('event_ids'):
                    L.append('    - записи: ' + _fold_records(list(c['event_ids'])))
        L.append('')
    L += ['## Почему такой вывод', '', 'Правило сравнения — CONTRACT.md раздел 4: охват → условия → сравнение по каждому механизму → сведение → допуск.', '']
    for k, v in (r.get('per_mechanism') or {}).items():
        L.append('- %s: %s' % (MECH_RU.get(k, k), v))
    L += ['', 'Допуск равнозначности: %s.' % r.get('tolerance_basis', ''),
          '', 'Охват: учтено — %s; не учтено — %s.' % (', '.join(S['coverage_declared']), ', '.join(S['coverage_missing'])),
          '', S.get('policy_note', ''), '']
    # условия по окнам с первоисточниками (О4)
    conds = [(w, c) for w in S['windows'] for m in w['mechanisms'] for c in (m.get('conditions') or [])]
    if conds:
        L += ['## Условия по окнам: период, источник, публикация', '']
        for w, c in conds:
            L.append('### %s — %s' % (_win_title(w), c['text'].split(': ')[0]))
            L.append('')
            L.append('- уровень: %s; класс: %s%s' % (c.get('level') or 'не указан', 'приоритетное' if c['severity'] == 'critical' else 'предупреждение',
                                                     '; моделируемое (сценарий)' if c.get('simulated') else ''))
            iv = c.get('interval_utc') or []
            if iv and iv[0]:
                L.append('- действие: %s — %s' % (_dt(iv[0]), _dt(iv[1]) if len(iv) > 1 and iv[1] else 'конец не объявлен'))
            for s in c.get('sources') or []:
                L.append('- источник: ' + s)
            if c.get('event_ids'):
                L.append('- записи: ' + _fold_records(list(c['event_ids']), 5))
            L.append('')
    # источники и публикация (Т1/О4)
    L += ['## Источники и публикация', '', '| источник | роль | статус | происхождение | данные на | давность, мин |', '|---|---|---|---|---|---|']
    for k, v in S['sources'].items():
        if k.startswith('_'):
            continue
        L.append('| %s | %s | %s | %s | %s | %s |' % (k, v.get('role', ''), (v.get('status') or '').replace('|', '/'),
                                                     v.get('origin') or '', _dt(v.get('data_utc'), '%Y-%m-%d %H:%MZ') if v.get('data_utc') else '—',
                                                     v.get('age_min') if v.get('age_min') is not None else '—'))
    # прогнозы NOAA (история)
    if S.get('forecasts'):
        L += ['', '## Прогнозы NOAA до отсечки', '']
        for line in S['forecasts']:
            L.append('- %s: %s%s' % (line['label'], line['status_ru'],
                                    ('; выпуск %s от %s' % (line['release_id'], _dt(line['published_utc'], '%Y-%m-%d %H:%MZ'))) if line.get('release_id')
                                    else ('; ' + line['reason'] if line.get('reason') else '')))
    # проверка после отсечки
    ver = S.get('verification')
    if ver:
        L += ['', '## Проверка после отсечки: что наблюдалось потом (в расчёт не входило)', '', ver.get('summary', ''), '']
        if ver.get('kp_obs'):
            L += ['| интервал | Kp наблюдение |', '|---|---|'] + ['| %s — %s | %s |' % (_dt(k['from_utc']), _dt(k['to_utc'], '%H:%MZ'), fmt_ru(k['kp'])) for k in ver['kp_obs']]
        if ver.get('events'):
            L += ['', 'События, опубликованные после отсечки на горизонте:'] + [
                '- %s %s, публикация %s%s' % (e['kind'], e['id'], _dt(e['published_utc']), (' — ' + e['note']) if e.get('note') else '') for e in ver['events'][:20]]
    L += ['', '## Чего не заявляем', '',
          '- допустимость реального выхода — за уполномоченными специалистами;',
          '- вероятность разгерметизации и попадания в космонавта;',
          '- дозу человека; поток GOES как поток у станции без обрезания.', '']
    return '\n'.join(L)


def _traj_line(tm: dict) -> str:
    if not tm.get('source_id'):
        return 'орбита недоступна: %s' % tm.get('status')
    if tm.get('method') == 'oem_interp':
        return 'OEM NASA/JSC (%s), создан %s, интерполяция; %s; поле %s' % (
            tm['source_id'], _dt(tm.get('created_utc'), '%d.%m.%Y %H:%MZ'),
            'объявленная реконструкция' if tm.get('is_reconstruction') else 'доказанная публикация до отсечки', tm.get('field_model'))
    return 'SGP4 по TLE (%s), эпоха %s, получен %s; %s; поле %s' % (
        tm['source_id'], _dt(tm.get('epoch_utc'), '%d.%m.%Y %H:%MZ'), _dt(tm.get('fetched_utc'), '%d.%m.%Y %H:%MZ'),
        'объявленная реконструкция' if tm.get('is_reconstruction') else 'без реконструкции', tm.get('field_model'))


def build_zip(S: dict, raw_records: dict[str, Any]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('report.md', report_md(S))
        z.writestr('request.json', _j(S['request']))
        z.writestr('trajectory_meta.json', _j(S['trajectory_meta']))
        z.writestr('factors.json', _j(S['windows']))
        z.writestr('recommendation.json', _j(S['recommendation']))
        z.writestr('cards.json', _j(S['cards']))
        z.writestr('sources.json', _j(S['sources']))
        if S.get('verification') is not None:
            z.writestr('verification.json', _j(S['verification']))
        z.writestr('manifest.json', _j({
            'schema_version': S.get('schema_version'), 'algorithm_version': S['algorithm_version'],
            'computed_utc': S['computed_utc'], 'mode': S.get('mode_id', S['mode']),
            'cutoff_utc': S['request'].get('cutoff_utc'), 'is_simulated': S.get('is_simulated', False),
            'source_versions': S['sources'], 'raw_record_ids': sorted(raw_records),
            'effective_config': S.get('effective_config') or {'thresholds': S['request']['thresholds']},
            'excluded_by_cutoff': S.get('history', {}).get('excluded_by_cutoff', []),
            'events_used': S.get('history', {}).get('events_used', []),
            'catalog_coverage': S.get('history', {}).get('catalog_coverage'),
            'robustness': S.get('robustness'), 'git_commit': _git_sha(),
        }))
        for rid, rec in raw_records.items():
            z.writestr('raw/%s.json' % rid.replace('/', '_').replace('#', '_').replace(':', '-'), _j(rec))
    return buf.getvalue()
