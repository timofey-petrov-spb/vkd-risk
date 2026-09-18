# -*- coding: utf-8 -*-
"""Расчётный конвейер без интерфейса (Т7: получение, расчёт и интерфейс — три слоя).

run(params) собирает один снимок расчёта. Его используют:
  * app/main.py — рендер экрана и кнопки выгрузки из одного и того же снимка;
  * scripts/make_examples.py — сохранённые примеры расчётов для сдачи (Т8);
  * эксперименты — прогоны без Streamlit.

Слои А: орбита — vkd.orbit (A3) через vkd.integration.orbit_bridge, без заглушки;
прогнозы NOAA до отсечки — vkd.history.replay (A2) через vkd.integration.noaa_forecast;
живые источники (A4) и разбор DONKI (A2) — пока временные модули experiments/.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from vkd.assess.cutoff import apply_cutoff
from vkd.assess.magcoords import belt_coordinates
from vkd.assess.meteoroids import meteoroid_hits_track
from vkd.assess.trapped import BeltTable
from vkd.explain.cards import cards_for_window
from vkd.integration.noaa_forecast import STATUS_RU as FC_STATUS_RU, noaa_forecasts
from vkd.integration.orbit_bridge import ORBIT_SRC, build_orbit, provenance_summary
from vkd.types import Window
from vkd.windows.compare import Thresholds, assess_window, recommend
from vkd.windows.scenario import Scenario, apply_to_windows, simulated_events, simulated_kp
from vkd.windows.sensitivity import robustness

try:
    from vkd.sources import goes_latest, kp_latest, tle_latest   # type: ignore  # A4 — когда появится
    SRC_LAYER = 'vkd.sources'
except ImportError:
    from experiments.stub_sources import goes_latest, kp_latest, tle_latest
    SRC_LAYER = 'experiments.stub_sources — временно до A4: живой запрос, кеш, снимок'
try:
    from vkd.history import history_bundle    # type: ignore  # A2 — разбор содержания DONKI
    HIST_SRC = 'vkd.history'
except ImportError:
    from experiments.stub_history import history_bundle
    HIST_SRC = 'experiments.stub_history — временно до A2: события DONKI, время публикации по реестру A1'

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALGO_VERSION = '0.4.1-i1'
MODES = ('live', 'history_review', 'history_forecast')
MODE_RU = {'live': 'Текущая обстановка', 'history_review': 'Исторический разбор', 'history_forecast': 'Прогноз из прошлого'}
TLE_URL = 'https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE'

POLICY_NOTE = ('Исключение окон с S1–S2, Kp ≥ 7 (наблюдение или прогноз NOAA) или сообщением о сближении из '
               'автоматического выбора — консервативная политика прототипа, не эксплуатационная норма; из индексов '
               'NOAA не следует ни прерывание, ни продолжение ВКД.')


@dataclass
class Result:
    S: dict                    # снимок для экрана и выгрузки
    raw_records: dict
    traj: list
    meta: Any
    assessments: list
    rec: Any
    cards: list
    events: list
    rob: Any
    goes: Any
    kp: Any
    excluded: list
    fetch_status: dict
    forecasts: list = None     # линии прогнозов NOAA (история)
    orbit: Any = None          # OrbitResult
    kp_obs: list = None        # исторический разбор: наблюдения Kp (от, до, значение) для ленты


def run(mode: str, t0: datetime, duration_min: int, search_min: int, window_offsets_min: list[int],
        disabled: Optional[dict] = None, thresholds: Optional[Thresholds] = None,
        scenario: Optional[Scenario] = None, T_months: int = 6,
        fetched: Optional[tuple] = None, now: Optional[datetime] = None,
        tle_override_path: Optional[str] = None) -> Result:
    """tle_override_path — воспроизведение сохранённого расчёта текущего режима: орбита
    строится по сохранённому TLE, а не по текущему (Т8). В исторических режимах орбита
    берётся из архива OEM (A1/A3) и от TLE не зависит."""
    assert mode in MODES, mode
    disabled = disabled or {'goes': False, 'kp': False}
    th = thresholds or Thresholds.from_settings()
    scenario = scenario or Scenario('none')
    now = now or datetime.now(timezone.utc)
    cutoff_utc = t0 if mode == 'history_forecast' else None
    horizon_min = search_min + duration_min
    is_sim = bool(scenario.work_delay_min or scenario.sep_onset_offset_min is not None or scenario.kp_override is not None)

    # ------------------------------------------------------------ источники
    if fetched is None:
        fetched = (goes_latest(disabled=disabled['goes']), kp_latest(disabled=disabled['kp']), tle_latest(disabled=False))
    (goes, goes_raw, f_goes), (kp, kp_raw, f_kp), (tle_text, f_tle) = fetched
    # Т6: два разных состояния источника. 'cache' (или True) — имитация отказа: живого запроса нет,
    # берётся кеш с давностью, покрытие становится частичным и объявляется. 'off' — источник
    # исключён: данных нет, покрытие обязательной линии NONE → рекомендации нет. Ни одно из них
    # не превращается в «благоприятно».
    if disabled.get('goes') == 'off':
        goes, goes_raw = None, {}
        f_goes = replace(f_goes, ok=False, from_cache=False, status_ru='источник исключён пользователем — данных нет', payload=None, raw_path=None)
    if disabled.get('kp') == 'off':
        kp, kp_raw = None, {}
        f_kp = replace(f_kp, ok=False, from_cache=False, status_ru='источник исключён пользователем — данных нет', payload=None, raw_path=None)
    events, hist_raw, excluded, fc_lines, fc_raw, forecasts, kp_obs = [], {}, [], [], {}, [], []
    if mode != 'live':
        goes, goes_raw = None, {}          # архива наблюдений GOES за 2024 нет (A1, в работе) — линия честно без данных
        h_samples, h_events, hist_raw = history_bundle()
        cut = apply_cutoff(h_samples, h_events, [], cutoff_utc)
        excluded = list(cut.excluded)
        kp_hist = [s for s in cut.samples if s.channel_id == 'kp' and s.t_utc <= t0]
        kp = max(kp_hist, key=lambda s: s.t_utc) if (kp_hist and disabled.get('kp') != 'off') else None
        kp_raw = {kp.raw_record_id: hist_raw.get(kp.raw_record_id)} if kp else {}
        if mode == 'history_review':      # разбор: наблюдения Kp вокруг периода — контекст ленты, не вход строгого режима
            kp_obs = sorted({(s.valid_from_utc, s.valid_to_utc, s.value) for s in h_samples
                             if s.channel_id == 'kp' and s.valid_from_utc and s.valid_to_utc
                             and t0 - timedelta(hours=12) <= s.t_utc <= t0 + timedelta(minutes=horizon_min + 180)})
        # события, чей интервал касается [t0 − 6 ч, конец горизонта]; давность публикации не ограничивается —
        # прогноз прихода выброса, выпущенный за трое суток, всё равно относится к окну
        def _touches(e):
            a0 = e.valid_from_utc or e.start_utc
            if a0 is None:
                return True
            a1 = e.valid_to_utc or e.end_utc or (a0 + timedelta(hours=24))
            return a0 <= t0 + timedelta(minutes=horizon_min) and a1 >= t0 - timedelta(hours=6)
        events = [e for e in cut.events if _touches(e)]
        # прогнозы NOAA, выпущенные до отсечки (в разборе — до начала периода): A1/A2 через адаптер Б
        fc_lines, fc_raw = noaa_forecasts(t0, t0, t0 + timedelta(minutes=horizon_min))
        forecasts = [s for line in fc_lines for s in line.samples]
    kp = simulated_kp(kp, t0, scenario)
    events = events + simulated_events(t0, scenario)

    # ------------------------------------------------------------ траектория (A3 через мост Б)
    if tle_override_path:
        tle_text = open(tle_override_path, encoding='utf-8').read()
        f_tle = replace(f_tle, status_ru='TLE из сохранённого расчёта (воспроизведение)', ok=False, from_cache=True)
    orb = build_orbit(mode, t0, horizon_min, th.saa_B_threshold_nT, tle_text=tle_text,
                      tle_fetched_utc=f_tle.fetched_utc, tle_available_utc=f_tle.fetched_utc,
                      tle_url=(getattr(f_tle, 'url', None) or TLE_URL),
                      tle_evidence=f_tle.status_ru, max_tle_age_days=th.tle_max_age_days,
                      cutoff_utc=(cutoff_utc if mode != 'live' else max(now, t0)))
    meta = orb.meta
    # координаты для таблиц ОСТ — эксцентричный диполь (Б): центральный диполь A3 в ядре аномалии
    # даёт L ниже сетки и нулевой поток на всей трассе (см. vkd/assess/magcoords.py); |B| — от A3
    coeff_path = os.path.join(ROOT, 'data', 'orbit', 'IGRF13.shc' if t0.year < 2025 else 'IGRF14.shc')
    traj, belt_coords = belt_coordinates(orb.points, coeff_path)
    if traj:
        orb.provenance.setdefault('limitations', []).append(
            'Для входа в таблицы ОСТ прил. А использованы L и B/B0 эксцентричного диполя (Б, magcoords), '
            'не значения A3: центральный диполь даёт L < 1,14 в ядре аномалии; %d из %d точек с B/B0 < 1 помечены.'
            % (belt_coords['n_inconsistent_BB0'], belt_coords['n']))

    # ------------------------------------------------------------ окна, устойчивость, оценка
    belts = BeltTable('min')
    windows = apply_to_windows([Window(t0 + timedelta(minutes=o), duration_min) for o in window_offsets_min], scenario)

    # метеороиды по ECSS (B2 по спецификации A5): по фактической высоте трассы, концы окна включены
    def _mmod(w: Window):
        end = w.start_utc + timedelta(minutes=w.duration_min)
        pts = [p for p in traj if w.start_utc <= p.t_utc <= end]
        if len(pts) < 2:
            return None, 'расчёт невозможен: нет трассы окна', 0.0
        frac = (pts[-1].t_utc - pts[0].t_utc).total_seconds() / (60.0 * w.duration_min)
        try:
            r = meteoroid_hits_track([p.t_utc for p in pts], [p.alt_km for p in pts], 1.0, 1e-3)
            return r.N, r.rule, frac
        except ValueError as e:
            return None, 'вне области применимости ECSS: %s' % e, frac

    mmod = {w.start_utc: _mmod(w) for w in windows}      # один раз на окно
    # покрытие каталога уведомлений DONKI в репозитории: архив выгружен за 1 мая — 30 июня 2024
    catalog = (datetime(2024, 5, 1, tzinfo=timezone.utc), datetime(2024, 7, 1, tzinfo=timezone.utc)) if mode != 'live' else None

    def _assess(w, tr, th_i):
        return assess_window(w, tr, belts, goes, kp, [], th_i, now, events=events, catalog_coverage=catalog,
                             mmod_hits=mmod[w.start_utc][0], mmod_rule=mmod[w.start_utc][1],
                             mmod_cov_fraction=mmod[w.start_utc][2], forecasts=forecasts)

    def _run(tr, kw):
        th_i = replace(th, **kw)
        A_i = [_assess(w, tr, th_i) for w in windows]
        return A_i, recommend(A_i, th_i)

    rob = robustness(traj, windows, _run,
                     thr_grid=[th.saa_B_threshold_nT - 2000, th.saa_B_threshold_nT, th.saa_B_threshold_nT + 2000],
                     e_grid=[12.5, 30.0, 50.0])
    th = replace(th, equiv_tol_min=max(1.0, rob.saa_spread_min))
    assessments = [_assess(w, traj, th) for w in windows]
    rec = recommend(assessments, th)
    rec = replace(rec, is_simulated=is_sim,
                  missing=rec.missing + (('орбита недоступна: %s' % orb.error,) if orb.error else ()),
                  tolerance_basis='допуск %.0f мин — разброс минут в аномалии у лучшего окна при порогах %s нТл' % (
                      th.equiv_tol_min, '/'.join('%.0f' % x for x in rob.grid[0])) + ('; выбор устойчив' if rob.stable else '; ВЫБОР МЕНЯЕТСЯ на сетке'))
    samples = {**({goes.raw_record_id: goes} if goes else {}), **({kp.raw_record_id: kp} if kp else {}),
               **{s.raw_record_id: s for s in forecasts}}
    cards = cards_for_window(assessments[0], samples)

    # ------------------------------------------------------------ снимок
    iso = lambda v: v.isoformat() if hasattr(v, 'isoformat') else v
    ev_raw = {e.raw_record_id: hist_raw[e.raw_record_id] for e in events if e.raw_record_id in hist_raw}
    raw_records = {**(goes_raw or {}), **(kp_raw or {}), **ev_raw, **fc_raw,
                   'orbit_provenance': orb.provenance}
    if mode == 'live':
        raw_records['iss.tle'] = {'text': tle_text, 'epoch_utc': iso(meta.epoch_utc) if meta and meta.epoch_utc else None,
                                  'fetch': f_tle.status_ru}

    def _src(f, role, sample=None):
        return {'role': role, 'status': f.status_ru, 'live_ok': f.ok, 'from_cache': f.from_cache,
                'fetched_utc': iso(f.fetched_utc) if f.fetched_utc else None,
                'data_utc': iso(sample.t_utc) if sample else None,
                'age_min': round((now - sample.t_utc).total_seconds() / 60) if sample else f.age_min}

    orbit_src = {'role': 'орбита', 'status': orb.status_ru, 'strictness': orb.strictness,
                 'live_ok': f_tle.ok if mode == 'live' else None, 'from_cache': f_tle.from_cache if mode == 'live' else None,
                 'fetched_utc': iso(meta.fetched_utc) if meta else None,
                 'epoch_utc': iso(meta.epoch_utc) if meta and meta.epoch_utc else None,
                 'age_h': round((now - meta.epoch_utc).total_seconds() / 3600, 1) if meta and meta.epoch_utc else None,
                 'source_id': meta.source_id if meta else None}
    sources = {
        'orbit': orbit_src,
        'noaa_swpc_goes': _src(f_goes, 'протоны ≥10 МэВ', goes if mode == 'live' else None),
        'gfz_kp': _src(f_kp, 'Kp', kp if mode == 'live' else None),
        'ost1044_belts': {'role': 'захваченные протоны', 'status': belts.source, 'live_ok': None, 'from_cache': None},
        'ecss_grun': {'role': 'метеороиды', 'status': 'ECSS-E-ST-10-04C Rev.1: Grün (10-1), Table J-6 по высоте трассы, '
                                                      'интеграл по dt (спецификация A5, grun-ecss-2020-v1); потоки даты не включены; '
                                                      'контроль 5,609728e-7 на 400 км/1 м²/6 ч воспроизведён', 'live_ok': None, 'from_cache': None},
        '_layers': {'role': 'слои', 'status': 'орбита: %s; источники: %s; история: %s' % (ORBIT_SRC, SRC_LAYER, HIST_SRC)},
    }
    if mode != 'live':
        for line in fc_lines:
            sources['noaa_forecast_' + line.channel_id] = {
                'role': line.label_ru, 'status': FC_STATUS_RU.get(line.status, line.status) + (
                    '; выпуск %s от %s' % (line.release_id, line.published_utc.strftime('%Y-%m-%d %H:%MZ')) if line.record_id_ok() else ''),
                'live_ok': None, 'from_cache': True, 'coverage_fraction': line.coverage_fraction}

    meta_dict = ({**{k: iso(v) for k, v in meta.__dict__.items()}} if meta else
                 {'source_id': None, 'method': None, 'frame': None, 'epoch_utc': None, 'coverage_from_utc': None,
                  'coverage_to_utc': None, 'created_utc': None, 'available_utc': None, 'fetched_utc': None,
                  'is_reconstruction': None, 'field_model': None})
    S = {
        'schema_version': '2.1', 'algorithm_version': ALGO_VERSION, 'computed_utc': now.isoformat(),
        'mode': MODE_RU[mode], 'mode_id': mode,
        'request': {'t0_utc': t0.isoformat(), 'duration_min': duration_min, 'search_min': search_min,
                    'windows': [w.start_utc.isoformat() for w in windows], 'window_offsets_min': list(window_offsets_min),
                    'thresholds': th.__dict__, 'disabled': disabled, 'cutoff_utc': cutoff_utc.isoformat() if cutoff_utc else None,
                    'T_months': T_months, 'scenario': scenario.__dict__ if is_sim else None},
        'trajectory_meta': {**meta_dict, 'orbit_module': ORBIT_SRC, 'status': orb.status_ru, 'strictness': orb.strictness,
                            'error': orb.error, 'n_points': len(traj), 'provenance': provenance_summary(orb.provenance),
                            'belt_coordinates': belt_coords,
                            'tle_text': tle_text if mode == 'live' else None, 'tle_fetch_status': f_tle.status_ru if mode == 'live' else None},
        'windows': [{'start_utc': a.window.start_utc.isoformat(), 'duration_min': a.window.duration_min, 'mechanisms': [
            {'id': m.mechanism_id, 'mandatory': m.mandatory, 'coverage': m.coverage.value, 'needs_check': list(m.needs_check_reasons),
             'factors': [{'name': f.name, 'value': f.value, 'unit': f.unit, 'kind': f.kind.value, 'presence': f.presence.value,
                          'coverage': f.coverage.value, 'records': list(f.record_ids), 'rule': f.rule_applied, 'limits': f.limits_note}
                         for f in m.factors]} for m in a.mechanisms]} for a in assessments],
        'recommendation': {'verdict': rec.verdict, 'rule': rec.rule_applied, 'reasons': list(rec.reasons), 'missing': list(rec.missing),
                           'preferred': rec.preferred.start_utc.isoformat() if rec.preferred else None,
                           'per_mechanism': rec.per_mechanism_comparison, 'tolerance_basis': rec.tolerance_basis},
        'cards': [{k: (v.value if hasattr(v, 'value') else v) for k, v in c.__dict__.items()} for c in cards],
        'sources': sources,
        'forecasts': [{'channel': line.channel_id, 'label': line.label_ru, 'source_id': line.source_id, 'status': line.status,
                       'status_ru': FC_STATUS_RU.get(line.status, line.status), 'coverage_fraction': line.coverage_fraction,
                       'release_id': line.release_id, 'published_utc': iso(line.published_utc), 'record': line.raw_record_id,
                       'reason': line.reason, 'gaps': list(line.gaps),
                       'cells': [{'from': iso(s.valid_from_utc), 'to': iso(s.valid_to_utc), 'value': s.value} for s in line.samples]}
                      for line in fc_lines],
        'coverage_declared': list(assessments[0].coverage_declared), 'coverage_missing': list(assessments[0].coverage_missing),
        'policy_note': POLICY_NOTE,
        'is_simulated': is_sim,
        'history': {'provider': HIST_SRC if mode != 'live' else None, 'excluded_by_cutoff': excluded,
                    'events_used': [{'id': e.event_id, 'kind': e.kind_of_event, 'published_utc': iso(e.published_utc),
                                     'start_utc': iso(e.start_utc), 'simulated': e.is_simulated} for e in events]},
        'robustness': {'stable': rob.stable, 'saa_spread_min': rob.saa_spread_min, 'grid': rob.grid,
                       'preferred_by_grid': {'%.0f nT / %g MeV' % k: v for k, v in rob.preferred_starts.items()}},
    }
    return Result(S, raw_records, traj, meta, assessments, rec, cards, events, rob, goes, kp, excluded,
                  {'goes': f_goes, 'kp': f_kp, 'tle': f_tle}, forecasts=fc_lines, orbit=orb, kp_obs=kp_obs)
