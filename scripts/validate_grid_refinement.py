"""Source-fixed refinement acceptance cases; no network or fitted thresholds.

Run: python scripts/validate_grid_refinement.py
"""
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from vkd.integration.orbit_bridge import OrbitResult
from vkd.integration.orbit_refinement import refine_orbit
from vkd.orbit import trajectory_with_provenance
from vkd.orbit.trajectory import satellite_from_tle

ROOT=Path(__file__).resolve().parents[1]

def main():
    epoch=satellite_from_tle((ROOT/'data/orbit/iss.tle').read_bytes()).epoch.utc_datetime().replace(second=0,microsecond=0)
    cases=[('quiet',datetime(2024,5,3,12,tzinfo=timezone.utc),840,[0,480],360,'history_review'),
           ('gannon',datetime(2024,5,10,12,tzinfo=timezone.utc),840,[0,480],360,'history_review'),
           ('june',datetime(2024,6,25,12,tzinfo=timezone.utc),840,[0,480],360,'history_review'),
           ('max_horizon_delay',datetime(2024,5,20,15,tzinfo=timezone.utc),1920,[0,1440],480,'history_review'),
           ('tle',epoch,840,[0,480],360,'live')]
    report={'schema_version':'refinement-study-v1',
            'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'python':platform.python_version(),'platform':platform.system(),
            'scope':'common-support numerical agreement of fixed-input models; NOT physical validation',
            'cases':[]}
    for name,start,horizon,offsets,duration,mode in cases:
        cutoff=start-timedelta(hours=3) if name=='max_horizon_delay' else start
        def build(step):
            meta,points,prov=trajectory_with_provenance(start,horizon,24000,mode=mode,cutoff_utc=cutoff,
                                                       step_seconds=step,include_inertial_states=True)
            return OrbitResult(meta,points,prov,'','declared_reconstruction' if meta.is_reconstruction else 'strict',None)
        before=time.monotonic()
        initial=build(60)
        orbit,r=refine_orbit(initial,build,
            [(start+timedelta(minutes=o),start+timedelta(minutes=o+duration)) for o in offsets],
            ROOT/'data/orbit'/('IGRF13.shc' if start.year<2025 else 'IGRF14.shc'),saa_threshold_nT=24000)
        report['cases'].append({'case_id':name,'start_utc':start.isoformat(),'cutoff_utc':cutoff.isoformat(),
            'horizon_min':horizon,'window_offsets_min':offsets,'duration_min':duration,
            'elapsed_seconds':time.monotonic()-before,'refinement':r})
        print(name,r['status'],r['selected_step_seconds'],flush=True)
    out=ROOT/'examples/validation/grid_refinement.json'
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    lines=['# Уточнение сетки: сравнение на одинаковом известном участке','',
           'Пять заранее заданных случаев, исходные файлы закреплены хешами. '
           'Сетка 5 с не является физической истиной. Допуски — инженерные настройки, не нормативы.',
           '', '| Случай | Пара сеток, с | Макс. относительное различие ≥30 МэВ на общем участке, % | '
           'Макс. изменение известного участка, с |', '|---|---|---:|---:|']
    for case in report['cases']:
        for pair in case['refinement']['comparisons']:
            ch=[w['fluence_channels']['30.0'] for w in pair['windows']]
            vals=[c['relative_difference'] for c in ch if c['relative_difference'] is not None]
            lines.append('| %s | %s → %s | %s | %.1f |'%(case['case_id'],pair['coarse_step_seconds'],
                pair['fine_step_seconds'],'%.3f'%(100*max(vals)) if vals else 'нет данных',
                max(c['support_change_seconds'] for c in ch)))
    lines+=['','Статус каждого случая:']
    for case in report['cases']:
        r=case['refinement']
        lines.append('- %s: `%s`, шаг %s с, %s вычисленных точек, %.2f с на этой машине.'%
                     (case['case_id'],r['status'],r['selected_step_seconds'],r['evaluated_points'],case['elapsed_seconds']))
    lines+=['','Относительное различие — интеграл абсолютной разности кусочно-линейных потоков, '
            'делённый на интеграл модуля потока более частой сетки **на одинаковом известном участке**. '
            'Противоположные локальные расхождения не компенсируются. Изменение известного участка — '
            'длительность симметрической разности; равная общая длина покрытия не скрывает перенос пропусков.',
            '', 'Успех требует двух последовательных согласованных сравнений для всех окон и каналов '
            '12,5/30/50 МэВ (и выбранного канала). Поле должно покрывать всё окно; дополнительно проверяется '
            'изменение минут в аномалии. Даже успешное сравнение не закрывает отсутствующую физическую модель. '
            'Недостижение допусков на последней сетке объявляется явно; при включённой проверке рекомендация блокируется.',
            '',f'Код: `{report["git_commit"]}`. Повтор: `python scripts/validate_grid_refinement.py`. '
            'Точные настройки и хеши: `examples/validation/grid_refinement.json`.']
    (ROOT/'docs/methods/GRID_REFINEMENT_VALIDATION.md').write_text('\n'.join(lines)+'\n')

if __name__=='__main__':main()
