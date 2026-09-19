"""Reproducible A3 grid study: python scripts/validate_orbit_steps.py.

5-second sampling is a numerical reference of the SAME physical model, not
truth. Compare source-fixed grids; report partial flux as a known contribution.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from vkd.orbit import trajectory_with_provenance
from vkd.orbit.trajectory import satellite_from_tle
from vkd.orbit.validation import interval_diagnostics
from vkd.assess.magcoords import belt_coordinates
from vkd.assess.trapped import BeltTable

ROOT=Path(__file__).resolve().parents[1]
STEPS=(5,10,30,60,300,600)

def evaluate(points, seconds):
    times=[p.t_utc for p in points]
    coords,_=belt_coordinates(points,str(ROOT/'data/orbit'/('IGRF13.shc' if times[0].year<2025 else 'IGRF14.shc')))
    belts=BeltTable('min')
    flux=[belts.integral_flux(p.L,p.B_over_B0,30).value_per_cm2_s for p in coords]
    field=interval_diagnostics(times,[p.B_nT for p in points],max_gap_seconds=seconds,threshold=24000)
    integral=interval_diagnostics(times,flux,max_gap_seconds=seconds)
    return {'saa_left_min':field['below_threshold_left_seconds']/60,
            'saa_linear_min':field['below_threshold_linear_seconds']/60,
            'fluence_known_left_per_cm2':integral['left_known_integral_value_seconds'],
            'fluence_known_trapezoid_per_cm2':integral['trapezoid_known_integral_value_seconds'],
            'fluence_total_per_cm2':integral['total_integral_value_seconds'],
            'flux_coverage_fraction':integral['coverage_fraction'],
            'flux_left_coverage_fraction':integral['left_coverage_fraction']}


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    epoch=satellite_from_tle((ROOT/'data/orbit/iss.tle').read_bytes()).epoch.utc_datetime().replace(second=0,microsecond=0)
    cases=[('quiet',datetime(2024,5,3,12,tzinfo=timezone.utc),840,[0,480],360,'history_review'),
           ('gannon',datetime(2024,5,10,12,tzinfo=timezone.utc),840,[0,480],360,'history_review'),
           ('june',datetime(2024,6,25,12,tzinfo=timezone.utc),840,[0,480],360,'history_review'),
           ('max_horizon_delay',datetime(2024,5,20,15,tzinfo=timezone.utc),1920,[0,1440],480,'history_review'),
           ('tle',epoch,840,[0,480],360,'live')]
    report={'schema_version':'orbit-grid-study-v1','algorithm_version':'0.6.1',
            'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'reference_step_seconds':5,'steps_seconds':list(STEPS),
            'units':{'saa':'min','field':'nT','fluence':'particles/cm2'},
            'scope':'numerical discretization of fixed-input models; NOT physical validation',
            'limitations':['5-second reference is not an exact solution',
                'OEM history is declared reconstruction, not proven historical public availability',
                'eccentric dipole is not McIlwain L; IGRF omits storm external currents',
                'known fluence contributions are NOT totals when model coverage is partial',
                'five predeclared cases do not bound all possible dates or windows'], 'cases':[]}
    for name,start,horizon,offsets,duration,mode in cases:
        cutoff=start-timedelta(hours=3) if name=='max_horizon_delay' else start
        rows=[]; reference=None; hashes=None
        for step in STEPS:
            meta,points,prov=trajectory_with_provenance(start,horizon,24000,mode=mode,cutoff_utc=cutoff,step_seconds=step)
            current={rid:r['sha256'] for rid,r in prov['records'].items()}
            if hashes is None:hashes=current
            if current!=hashes:raise RuntimeError('Different source versions between grids')
            windows=[]
            for offset in offsets:
                a=start+timedelta(minutes=offset);b=a+timedelta(minutes=duration)
                windows.append(evaluate([p for p in points if a<=p.t_utc<=b],step))
            ts=np.array([(p.t_utc-start).total_seconds() for p in points]);B=np.array([p.B_nT for p in points])
            if reference is None:reference=(ts,B,windows)
            error=np.abs(np.interp(reference[0],ts,B)-reference[1])
            for w,base in zip(windows,reference[2]):
                w['saa_left_error_min_vs_5s']=w['saa_left_min']-base['saa_left_min']
                w['saa_linear_error_min_vs_5s']=w['saa_linear_min']-base['saa_linear_min']
                v0=base['fluence_known_left_per_cm2'];v=w['fluence_known_left_per_cm2']
                w['known_fluence_left_relative_change_vs_5s']=None if v0 in (None,0) or v is None else (v/v0-1)
            rows.append({'step_seconds':step,'point_count':len(points),'max_field_interpolation_error_nT':float(error.max()),
                         'windows':windows})
        report['cases'].append({'case_id':name,'start_utc':start.isoformat(),'cutoff_utc':cutoff.isoformat(),
            'horizon_min':horizon,'window_offsets_min':offsets,'duration_min':duration,'mode':mode,
            'is_reconstruction':meta.is_reconstruction,'source_hashes':hashes,'grids':rows})
        print(name,'complete',flush=True)
    out=ROOT/'examples/validation';out.mkdir(exist_ok=True)
    path=out/'orbit_steps.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    lines=['# A3: чувствительность к временному шагу','',
        'Пять заранее заданных случаев, одинаковые исходные файлы на всех сетках. Эталон 5 с — та же модель на более частой сетке, не физическая истина.',
        '', '| Случай | Шаг, с | Макс. ΔB интерполяции, нТл | Макс. ошибка минут ЮАА, мин | Макс. изменение известного флюенса, % | Покрытие флюенса, % |',
        '|---|---:|---:|---:|---:|---:|']
    for case in report['cases']:
        for row in case['grids'][1:]:
            w=row['windows'];delta=[abs(x['known_fluence_left_relative_change_vs_5s']) for x in w if x['known_fluence_left_relative_change_vs_5s'] is not None]
            lines.append('| %s | %s | %.2f | %.3f | %s | %.1f–%.1f |'%(case['case_id'],row['step_seconds'],row['max_field_interpolation_error_nT'],max(abs(x['saa_left_error_min_vs_5s']) for x in w), '%.2f'%(100*max(delta)) if delta else 'нет данных',100*min(x['flux_left_coverage_fraction'] for x in w),100*max(x['flux_left_coverage_fraction'] for x in w)))
    lines += ['', 'В таблице — левые прямоугольники, как в текущем минутном оценщике. В JSON отдельно дан интеграл трапециями только по интервалам с двумя известными концами и линейная локализация пересечения порога. Недостающий участок не заполняется нулём.',
        '', 'Порог 24000 нТл, канал ≥30 МэВ, солнечный минимум таблицы ОСТ; это фиксированные условия исследования. Изменение частичного интеграла смешивает дискретизацию потока и границ применимости. Это не погрешность полного флюенса.',
        '', 'Независимые численные эталоны SGP4/IGRF и их допуски: `tests/orbit/fixtures/README.md`. Здесь не проверяются реальная радиационная доза, магнитосферные токи во время бури или абсолютная точность прогноза OEM.',
        '', f'Код: `{report["git_commit"]}`. Воспроизведение: `python scripts/validate_orbit_steps.py`. Машиночитаемые результаты и SHA-256 каждого входа: `examples/validation/orbit_steps.json`.']
    (ROOT/'docs/methods/ORBIT_GRID_VALIDATION.md').write_text('\n'.join(lines)+'\n')
if __name__=='__main__':main()
