"""Locate numerical flux disagreement without changing the physical model.

Five fixed cases, 5 s versus 1 s, common support only. Boundary classification
is a diagnostic of the IMPLEMENTED interpolator, not independent validation.
"""
from datetime import datetime,timedelta,timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from vkd.orbit import trajectory_with_provenance
from vkd.orbit.trajectory import satellite_from_tle
from vkd.orbit.convergence import compare_grids
from vkd.assess.magcoords import belt_coordinates
from vkd.assess.trapped import BeltTable
ROOT=Path(__file__).resolve().parents[1]


def descriptor(p,table):
    if p.L is None or p.B_over_B0 is None or p.L<table.Ls[0] or p.L>table.Ls[-1]:
        return {'shells':(), 'available':(), 'L':p.L, 'B_over_B0':p.B_over_B0}
    j=int(np.searchsorted(table.Ls,p.L))
    shells=tuple(float(v) for v in table.Ls[max(0,j-1):j+1])
    return {'shells':shells,'available':tuple(p.B_over_B0<=table.table[v][-1][0] for v in shells),
            'L':p.L,'B_over_B0':p.B_over_B0}


def audit_pair(coarse,fine,windows,table,energy=30.0):
    c,f=coarse,fine
    cv=[table.integral_flux(p.L,p.B_over_B0,energy) for p in c]
    fv=[table.integral_flux(p.L,p.B_over_B0,energy) for p in f]
    cd,fd=[descriptor(p,table) for p in c],[descriptor(p,table) for p in f]
    results=[]
    for start,end in windows:
        local=[]
        total=compare_grids([p.t_utc for p in c],[v.value_per_cm2_s for v in cv],
                            [p.t_utc for p in f],[v.value_per_cm2_s for v in fv],start,end,interval_report=local)
        contributions={key:[] for key in ('tabulated_B_boundary','shell_boundary','interior')}
        for x in local:
            ci,fi=x['coarse_interval_index'],x['fine_interval_index']
            samples=[cd[ci],cd[ci+1],fd[fi],fd[fi+1]]
            masks=[(s['shells'],s['available']) for s in samples]
            shell_change=len({s['shells'] for s in samples})>1
            available_change=any(a[0]==b[0] and a[1]!=b[1] for a in masks for b in masks)
            category='tabulated_B_boundary' if available_change else 'shell_boundary' if shell_change else 'interior'
            contributions[category].append(x['absolute_difference_integral'])
            x['category']=category
            x['coarse_statuses']=[cv[ci].status,cv[ci+1].status]
            x['fine_statuses']=[fv[fi].status,fv[fi+1].status]
            x['coarse_model_coordinates']=samples[:2]
            x['fine_model_coordinates']=samples[2:]
        absolute=total.absolute_difference_integral
        grouped={key:{'absolute_difference_per_cm2':math.fsum(values),
                      'fraction_of_difference':math.fsum(values)/absolute if absolute else None,
                      'overlap_segments':len(values)} for key,values in contributions.items()}
        results.append({'start_utc':start.isoformat(),'end_utc':end.isoformat(),
                        'comparison':total.to_dict(),'attribution':grouped,
                        'largest_intervals':sorted(local,key=lambda x:x['absolute_difference_integral'],reverse=True)[:12]})
    return results


def main():
    epoch=satellite_from_tle((ROOT/'data/orbit/iss.tle').read_bytes()).epoch.utc_datetime().replace(second=0,microsecond=0)
    cases=[('quiet',datetime(2024,5,3,12,tzinfo=timezone.utc),840,[0,480],360,'history_review'),
           ('gannon',datetime(2024,5,10,12,tzinfo=timezone.utc),840,[0,480],360,'history_review'),
           ('june',datetime(2024,6,25,12,tzinfo=timezone.utc),840,[0,480],360,'history_review'),
           ('max_horizon_delay',datetime(2024,5,20,15,tzinfo=timezone.utc),1920,[0,1440],480,'history_review'),
           ('tle',epoch,840,[0,480],360,'live')]
    table=BeltTable('min')
    report={'schema_version':'flux-boundary-audit-v1','git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'coarse_step_seconds':5,'fine_step_seconds':1,'energy_min_MeV':30.0,
            'table_sha256':table.sha256,'scope':'numerical diagnosis of implemented interpolator, not physical validation',
            'cases':[]}
    for name,start,horizon,offsets,duration,mode in cases:
        before=time.monotonic();cutoff=start-timedelta(hours=3) if name=='max_horizon_delay' else start
        coeff=ROOT/'data/orbit'/('IGRF13.shc' if start.year<2025 else 'IGRF14.shc')
        # Same epoch used by the initial minute grid of the application.
        magnetic_epoch=start+timedelta(minutes=(horizon+1)//2)
        grids=[];hashes=None
        for step in (5,1):
            _,points,prov=trajectory_with_provenance(start,horizon,24000,mode=mode,cutoff_utc=cutoff,step_seconds=step)
            current={rid:r['sha256'] for rid,r in prov['records'].items()}
            if hashes is not None and hashes!=current:raise AssertionError('Source versions changed')
            hashes=current
            if hashlib.sha256(coeff.read_bytes()).hexdigest() not in hashes.values():raise AssertionError('Coefficient mismatch')
            grids.append(belt_coordinates(points,str(coeff),reference_utc=magnetic_epoch)[0])
        windows=[(start+timedelta(minutes=o),start+timedelta(minutes=o+duration)) for o in offsets]
        result=audit_pair(*grids,windows,table)
        report['cases'].append({'case_id':name,'source_hashes':hashes,'magnetic_epoch_utc':magnetic_epoch.isoformat(),
                               'horizon_min':horizon,'windows':result,'elapsed_seconds':time.monotonic()-before})
        print(name,[(r['comparison']['relative_difference'],r['attribution']['tabulated_B_boundary']['fraction_of_difference']) for r in result],flush=True)
    (ROOT/'examples/validation/flux_boundaries.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    lines=['# Локализация расхождений флюенса: 5 с → 1 с','',
           'Сравнение одного и того же интерполятора на одинаковом известном участке. '
           'Это численная диагностика; сетка 1 с не объявляется физическим эталоном.',
           '', '| Случай / окно | Относительное различие, % | Изменение известного участка, с | '
           'Вклад пересечений табличной границы B/B₀, % | Вклад границ оболочек L, % |',
           '|---|---:|---:|---:|---:|']
    for case in report['cases']:
        for i,w in enumerate(case['windows'],1):
            c=w['comparison'];a=w['attribution']
            lines.append('| %s / %s | %.3f | %.1f | %.2f | %.2f |'%(case['case_id'],i,
                100*c['relative_difference'],c['support_change_seconds'],
                100*a['tabulated_B_boundary']['fraction_of_difference'],100*a['shell_boundary']['fraction_of_difference']))
    lines+=['','Классы относятся к интервалам сравнения: если меняется наличие спектра на той же '
            'табличной оболочке — граница B/B₀; иначе при смене соседних оболочек — граница L; '
            'остальное — внутренний участок. Это локализация в реализованном коде, не доказательство '
            'физической природы скачка. Пропуски из-за L вне таблицы или B/B₀ < 1 не заменяются нулём '
            'и в общий известный участок не входят.',
            '', 'Относительное различие — интеграл модуля разности кусочно-линейных потоков, '
            'делённый на интеграл модуля потока сетки 1 с на общем известном участке. '
            'Изменение покрытия — длительность симметрической разности известных участков. '
            'Даже различие меньше 2 % не подтверждает общий допуск, если изменился охват.',
            '', 'В JSON сохранены хеши, опорная магнитная эпоха и двенадцать наибольших локальных '
            'расхождений каждого окна с временами, координатами и статусами. Эти данные нужны '
            'для адресной проверки интерполятора, а не для сглаживания результата до желательного вердикта.',
            '',f'Код: `{report["git_commit"]}`; повтор: `python scripts/audit_flux_boundaries.py`.']
    (ROOT/'docs/methods/FLUX_BOUNDARY_AUDIT.md').write_text('\n'.join(lines)+'\n')
if __name__=='__main__':main()
