# -*- coding: utf-8 -*-
"""Перенос результатов многоагентной оркестрации из журнала в docs/master/research/.

Использование:
    python scripts/salvage_workflow.py <путь к journal.jsonl>

Журнал содержит записи трёх типов: launched, started (agentId, label, phase),
result (agentId, result). Метка агента известна только из started, поэтому
результаты соединяются с метками по agentId.

Пишет для каждого направления research:<key> два файла: <key>.json (сырой
структурированный результат) и <key>.md (читаемый). Голоса скептиков
verify:<key>:<i>:<k> прикладываются к утверждению номер i направления key.
"""
import collections
import io
import json
import os
import sys

TITLES = {
    'spaceweather': 'Космическая погода как механизм',
    'mmod': 'Микрометеороиды и мусор как механизм',
    'otherfactors': 'Прочие факторы и условия работ',
    'orbit': 'Орбита, траектория, магнитные координаты',
    'history': 'Исторический режим и временная честность',
    'criteria': 'Критерии, доказательства, эксперименты',
    'norms': 'Нормативы и литература по действию на человека',
    'ux': 'Интерфейс, путь аналитика, развёртывание',
    'engineering': 'Архитектура, надёжность, воспроизводимость',
}
ORDER = ['spaceweather', 'mmod', 'otherfactors', 'orbit', 'history',
         'criteria', 'norms', 'ux', 'engineering']


def yn(b):
    return 'да' if b else 'нет'


def cell(s):
    return str(s or '').replace('|', '/').replace('\n', ' ')


def render(key, r, votes):
    L = ['# Исследование: ' + TITLES.get(key, key), '',
         'Подготовлено агентом-исследователем 18.09.2026 с фактической проверкой '
         'доступа к источникам. Направление `%s`. Сырой JSON рядом: `%s.json`.' % (key, key),
         '', '## Сводка', '', r.get('summary_ru', ''), '', '## Находки', '']
    for i, f in enumerate(r.get('findings', []), 1):
        L += ['### %d. %s' % (i, f.get('title', '')), '', f.get('detail_ru', ''), '']
        if f.get('sources'):
            L += ['Источники: ' + '; '.join(f['sources']), '']
        L += ['Уверенность: ' + f.get('confidence', ''), '']
    lit = r.get('literature', [])
    if lit:
        L += ['## Литература', '', '| документ | что даёт | есть | где | пункты |', '|---|---|---|---|---|']
        for x in lit:
            L.append('| %s | %s | %s | %s | %s |' % (cell(x.get('ref')), cell(x.get('what_it_gives_ru')),
                                                   yn(x.get('have')), cell(x.get('where')), cell(x.get('clauses'))))
        L.append('')
    ds = r.get('data_sources', [])
    if ds:
        L += ['## Источники данных', '',
              '| источник | URL | величина | единицы | частота | живой | архив | время публикации | май–июнь 2024 | доступ проверен | ограничения |',
              '|---|---|---|---|---|---|---|---|---|---|---|']
        for x in ds:
            ver = yn(x.get('verified_access'))
            if x.get('verification_note'):
                ver += ' — ' + cell(x['verification_note'])
            L.append('| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (
                cell(x.get('name')), cell(x.get('url')), cell(x.get('quantity')), cell(x.get('units')),
                cell(x.get('cadence')), yn(x.get('live')), yn(x.get('archive')),
                yn(x.get('publication_time_available')), yn(x.get('coverage_2024_05_06')), ver, cell(x.get('limits_ru'))))
        L.append('')
    if r.get('gaps_ru'):
        L += ['## Чего не хватает', ''] + ['- ' + g for g in r['gaps_ru']] + ['']
    cc = r.get('critical_claims', [])
    if cc:
        L += ['## Нагруженные утверждения и проверка скептиками', '']
        for i, c in enumerate(cc):
            vs = votes.get((key, i), [])
            L += ['### %d. %s' % (i + 1, c.get('claim', '')), '',
                  'Почему важно: ' + c.get('why_critical_ru', ''), '',
                  'Как проверить: ' + c.get('how_to_check', ''), '']
            if vs:
                ref = sum(1 for v in vs if v.get('refuted'))
                status = 'ОПРОВЕРГНУТО' if ref * 2 > len(vs) else 'подтверждено'
                L += ['Голосов скептиков: %d, из них «опровергнуто»: %d → **%s**' % (len(vs), ref, status), '']
                for v in vs:
                    line = '- ' + ('опровергает' if v.get('refuted') else 'подтверждает') + ': ' + cell(v.get('evidence_ru'))
                    if v.get('corrected_claim'):
                        line += ' Уточнение: ' + cell(v['corrected_claim'])
                    L += [line, '']
            else:
                L += ['Проверка скептиками не завершена: оркестрация остановлена для экономии '
                      'лимита. Проверить вручную по способу выше.', '']
    if r.get('recommendations_ru'):
        L += ['## Рекомендации', ''] + ['- ' + x for x in r['recommendations_ru']] + ['']
    return '\n'.join(L)


def main(journal, out):
    label_of, results = {}, {}
    for line in io.open(journal, encoding='utf-8', errors='replace'):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get('type') == 'started':
            label_of[e['agentId']] = e.get('label', '')
        elif e.get('type') == 'result':
            results[e['agentId']] = e.get('result')
    research, votes = {}, collections.defaultdict(list)
    for aid, res in results.items():
        lab = label_of.get(aid, '')
        if lab.startswith('research:') and isinstance(res, dict):
            research[lab.split(':', 1)[1]] = res
        elif lab.startswith('verify:') and isinstance(res, dict):
            _, dim, ci, _k = lab.split(':')
            votes[(dim, int(ci))].append(res)
    os.makedirs(out, exist_ok=True)
    for key, r in research.items():
        io.open(os.path.join(out, key + '.json'), 'w', encoding='utf-8').write(
            json.dumps(r, ensure_ascii=False, indent=1))
        io.open(os.path.join(out, key + '.md'), 'w', encoding='utf-8').write(render(key, r, votes))
    idx = ['# Исследования для мастер-плана', '',
           'Девять направлений: сводка, находки с источниками, литература, реестр источников '
           'с фактической проверкой доступа, пробелы, нагруженные утверждения, рекомендации. '
           'Оркестрация остановлена после первой фазы для экономии лимита; проверка скептиками '
           'завершена частично, голоса приложены там, где есть.', '',
           '| направление | файл | находок | источников | пробелов |', '|---|---|---|---|---|']
    for key in ORDER:
        r = research.get(key)
        if r:
            idx.append('| %s | [%s.md](%s.md) | %d | %d | %d |' % (
                TITLES[key], key, key, len(r.get('findings', [])), len(r.get('data_sources', [])), len(r.get('gaps_ru', []))))
    io.open(os.path.join(out, 'README.md'), 'w', encoding='utf-8').write('\n'.join(idx) + '\n')
    print('исследований записано:', len(research), '| голосов скептиков:', sum(len(v) for v in votes.values()),
          'по', len(votes), 'утверждениям')
    for key in ORDER:
        r = research.get(key)
        if r:
            print('  %-13s находок %2d  источников %2d  литературы %2d  пробелов %2d  утверждений %d' % (
                key, len(r.get('findings', [])), len(r.get('data_sources', [])), len(r.get('literature', [])),
                len(r.get('gaps_ru', [])), len(r.get('critical_claims', []))))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    main(sys.argv[1], os.path.join(root, 'docs', 'master', 'research'))
