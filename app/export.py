# -*- coding: utf-8 -*-
"""Выгрузка расчёта единым снимком (постановка, «Интерфейс и форма результата»; Т8).

Архив содержит: report.md (читаемо), request.json, trajectory_meta.json,
factors.json, recommendation.json, cards.json, sources.json, manifest.json
и raw/<record_id>.json — сырые записи, из которых восстанавливается расчёт.
Сведения в выгрузке совпадают с интерфейсом: и то и другое строится из одного
словаря-снимка, собранного один раз за расчёт.
"""
from __future__ import annotations

import io
import json
import zipfile
from typing import Any


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


def report_md(S: dict) -> str:
    r = S['recommendation']
    L = ['# ВКД-Риск: расчёт %s' % S['computed_utc'],
         '', 'Режим: %s. Версия алгоритма: %s. Отсечка: %s.' % (S['mode'], S['algorithm_version'], S['request'].get('cutoff_utc') or 'нет'),
         '', '## Запрос', '',
         '- начало периода поиска: %s' % S['request']['t0_utc'],
         '- длительность ВКД: %d мин; период поиска: %d мин' % (S['request']['duration_min'], S['request']['search_min']),
         '- окна: ' + ', '.join(S['request']['windows']),
         '- отключённые источники: ' + (', '.join(k for k, v in S['request']['disabled'].items() if v) or 'нет'),
         '', '## Траектория', '',
         '- источник %s, метод %s, эпоха %s, реконструкция: %s, поле %s' % (
             S['trajectory_meta']['source_id'], S['trajectory_meta']['method'], S['trajectory_meta'].get('epoch_utc'),
             S['trajectory_meta']['is_reconstruction'], S['trajectory_meta']['field_model']),
         '', '## Окна', '']
    for w in S['windows']:
        L.append('### Окно с началом %s' % w['start_utc'])
        for m in w['mechanisms']:
            L.append('- **%s** (покрытие %s%s)' % (m['id'], m['coverage'], '; обязательная' if m['mandatory'] else ''))
            for f in m['factors']:
                L.append('  - %s: %s %s — %s; правило: %s' % (f['name'], f['value'] if f['value'] is not None else '—',
                                                            f['unit'], f['kind'], f['rule']))
            for c in m['needs_check']:
                L.append('  - условие: ' + c)
        L.append('')
    L += ['## Рекомендация', '', '**%s** — %s' % (r['verdict'], r['rule']),
          '', 'Предпочтительное окно: %s' % (r['preferred'] or 'нет')]
    if r['reasons']:
        L += ['', 'Что повлияло:'] + ['- ' + x for x in r['reasons']]
    if r['missing']:
        L += ['', 'Чего не хватает:'] + ['- ' + x for x in r['missing']]
    L += ['', 'Допуск равнозначности: ' + r.get('tolerance_basis', ''),
          '', 'Охват: учтено — %s; не учтено — %s.' % (', '.join(S['coverage_declared']), ', '.join(S['coverage_missing'])),
          '', '## Чего не заявляем', '',
          '- допустимость реального выхода — за уполномоченными специалистами;',
          '- вероятность разгерметизации и попадания в космонавта;',
          '- дозу человека; поток GOES как поток у станции без обрезания.', '']
    return '\n'.join(L)


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
        z.writestr('manifest.json', _j({
            'schema_version': S.get('schema_version'), 'algorithm_version': S['algorithm_version'],
            'computed_utc': S['computed_utc'], 'mode': S.get('mode_id', S['mode']),
            'cutoff_utc': S['request'].get('cutoff_utc'), 'is_simulated': S.get('is_simulated', False),
            'source_versions': S['sources'], 'raw_record_ids': sorted(raw_records),
            'effective_config': S['request']['thresholds'],
            'excluded_by_cutoff': S.get('history', {}).get('excluded_by_cutoff', []),
            'events_used': S.get('history', {}).get('events_used', []),
            'robustness': S.get('robustness'), 'git_commit': _git_sha(),
        }))
        for rid, rec in raw_records.items():
            z.writestr('raw/%s.json' % rid.replace('/', '_').replace('#', '_').replace(':', '-'), _j(rec))
    return buf.getvalue()
