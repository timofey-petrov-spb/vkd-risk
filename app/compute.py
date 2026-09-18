# -*- coding: utf-8 -*-
"""Расчётный конвейер без интерфейса (Т7: получение, расчёт и интерфейс — три слоя).

run(params) собирает один снимок расчёта. Его используют:
  * app/main.py — рендер экрана и кнопки выгрузки из одного и того же снимка;
  * scripts/make_examples.py — сохранённые примеры расчётов для сдачи (Т8);
  * эксперименты — прогоны без Streamlit.

Подмена слоёв: vkd.orbit / vkd.sources / vkd.history подхватываются
автоматически, иначе работают временные заглушки из experiments/.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from vkd.assess.cutoff import apply_cutoff
from vkd.assess.meteoroids import meteoroid_hits
from vkd.assess.trapped import BeltTable
from vkd.explain.cards import cards_for_window
from vkd.types import Window
from vkd.windows.compare import Thresholds, assess_window, recommend
from vkd.windows.scenario import Scenario, apply_to_windows, simulated_events, simulated_kp
from vkd.windows.sensitivity import robustness

try:
    from vkd.orbit import trajectory          # type: ignore
    ORBIT_SRC = 'vkd.orbit'
except ImportError:
    from experiments.stub_orbit import trajectory
    ORBIT_SRC = 'experiments.stub_orbit — временно, дипольная L, помечена в статусе точек'
try:
    from vkd.sources import goes_latest, kp_latest, tle_latest   # type: ignore
    SRC_LAYER = 'vkd.sources'
except ImportError:
    from experiments.stub_sources import goes_latest, kp_latest, tle_latest
    SRC_LAYER = 'experiments.stub_sources — временно: живой запрос, кеш, снимок'
try:
    from vkd.history import history_bundle    # type: ignore
    HIST_SRC = 'vkd.history'
except ImportError:
    from experiments.stub_history import history_bundle
    HIST_SRC = 'experiments.stub_history — временно: архив DONKI, уведомления с messageIssueTime'

ALGO_VERSION = '0.3.0-b3'
MODES = ('live', 'history_review', 'history_forecast')
MODE_RU = {'live': 'Текущая обстановка', 'history_review': 'Исторический разбор', 'history_forecast': 'Прогноз из прошлого'}

POLICY_NOTE = ('Исключение окон с S1–S2, Kp ≥ 7 или сообщением о сближении из автоматического выбора — '
               'консервативная политика прототипа, не эксплуатационная норма; из индексов NOAA не следует '
               'ни прерывание, ни продолжение ВКД.')


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


def run(mode: str, t0: datetime, duration_min: int, search_min: int, window_offsets_min: list[int],
        disabled: Optional[dict] = None, thresholds: Optional[Thresholds] = None,
        scenario: Optional[Scenario] = None, T_months: int = 6,
        fetched: Optional[tuple] = None, now: Optional[datetime] = None) -> Result:
    assert mode in MODES, mode
    disabled = disabled or {'goes': False, 'kp': False}
    th = thresholds or Thresholds()
    scenario = scenario or Scenario('none')
    now = now or datetime.now(timezone.utc)
    cutoff_utc = t0 if mode == 'history_forecast' else None
    horizon_min = search_min + duration_min
    is_sim = bool(scenario.work_delay_min or scenario.sep_onset_offset_min is not None or scenario.kp_override is not None)

    # ------------------------------------------------------------ источники
    if fetched is None:
        fetched = (goes_latest(disabled=disabled['goes']), kp_latest(disabled=disabled['kp']), tle_latest(disabled=False))
    (goes, goes_raw, f_goes), (kp, kp_raw, f_kp), (tle_text, f_tle) = fetched
    events, hist_raw, excluded = [], {}, []
    if mode != 'live':
        goes, goes_raw = None, {}          # архива GOES за 2024 нет (A2) — линия честно без данных
        h_samples, h_events, hist_raw = history_bundle()
        cut = apply_cutoff(h_samples, h_events, [], cutoff_utc)
        excluded = list(cut.excluded)
        kp_hist = [s for s in cut.samples if s.channel_id == 'kp' and s.t_utc <= t0]
        kp = max(kp_hist, key=lambda s: s.t_utc) if (kp_hist and not disabled['kp']) else None
        kp_raw = {kp.raw_record_id: hist_raw.get(kp.raw_record_id)} if kp else {}
        events = [e for e in cut.events if e.start_utc is None or (
            e.start_utc <= t0 + timedelta(minutes=horizon_min) and (e.published_utc or e.start_utc) >= t0 - timedelta(hours=48))]
    kp = simulated_kp(kp, t0, scenario)
    events = events + simulated_events(t0, scenario)

    # ------------------------------------------------------------ траектория
    meta, traj = trajectory(t0, horizon_min, th.saa_B_threshold_nT, tle_path=f_tle.raw_path)
    if mode != 'live':
        meta = replace(meta, is_reconstruction=True)

    # ------------------------------------------------------------ окна, устойчивость, оценка
    belts = BeltTable('min')
    windows = apply_to_windows([Window(t0 + timedelta(minutes=o), duration_min) for o in window_offsets_min], scenario)

    # метеороиды по ECSS (B2 по спецификации A5): высота — средняя по окну, пластина 1 м²
    def _mmod(w: Window):
        pts = [p for p in traj if w.start_utc <= p.t_utc < w.start_utc + timedelta(minutes=w.duration_min)]
        alt = sum(p.alt_km for p in pts) / len(pts) if pts else 420.0
        try:
            r = meteoroid_hits(alt, 1.0, w.duration_min / 60.0, m_min_g=1e-3)
            return r.N, r.rule + '; h=%.0f км' % alt
        except ValueError as e:
            return None, 'вне области применимости ECSS: %s' % e

    mmod = {w.start_utc: _mmod(w) for w in windows}      # один раз на окно
    # покрытие каталога уведомлений DONKI в репозитории: архив выгружен за 1 мая — 30 июня 2024
    catalog = (datetime(2024, 5, 1, tzinfo=timezone.utc), datetime(2024, 7, 1, tzinfo=timezone.utc)) if mode != 'live' else None

    def _run(tr, kw):
        th_i = replace(th, **kw)
        A_i = [assess_window(w, tr, belts, goes, kp, [], th_i, now, events=events, catalog_coverage=catalog,
                             mmod_hits=mmod[w.start_utc][0], mmod_rule=mmod[w.start_utc][1]) for w in windows]
        return A_i, recommend(A_i, th_i)

    rob = robustness(traj, windows, _run,
                     thr_grid=[th.saa_B_threshold_nT - 2000, th.saa_B_threshold_nT, th.saa_B_threshold_nT + 2000],
                     e_grid=[12.5, 30.0, 50.0])
    th = replace(th, equiv_tol_min=max(1.0, rob.saa_spread_min))
    assessments = [assess_window(w, traj, belts, goes, kp, [], th, now, events=events, catalog_coverage=catalog,
                                 mmod_hits=mmod[w.start_utc][0], mmod_rule=mmod[w.start_utc][1]) for w in windows]
    rec = recommend(assessments, th)
    rec = replace(rec, is_simulated=is_sim,
                  tolerance_basis='допуск %.0f мин — разброс минут в аномалии у лучшего окна при порогах %s нТл' % (
                      th.equiv_tol_min, '/'.join('%.0f' % x for x in rob.grid[0])) + ('; выбор устойчив' if rob.stable else '; ВЫБОР МЕНЯЕТСЯ на сетке'))
    samples = {**({goes.raw_record_id: goes} if goes else {}), **({kp.raw_record_id: kp} if kp else {})}
    cards = cards_for_window(assessments[0], samples)

    # ------------------------------------------------------------ снимок
    ev_raw = {e.raw_record_id: hist_raw[e.raw_record_id] for e in events if e.raw_record_id in hist_raw}
    raw_records = {**(goes_raw or {}), **(kp_raw or {}), **ev_raw,
                   'iss.tle': {'text': tle_text, 'epoch_utc': meta.epoch_utc.isoformat() if meta.epoch_utc else None, 'fetch': f_tle.status_ru}}

    def _src(f, role, sample=None):
        return {'role': role, 'status': f.status_ru, 'live_ok': f.ok, 'from_cache': f.from_cache,
                'fetched_utc': f.fetched_utc.isoformat() if f.fetched_utc else None,
                'data_utc': sample.t_utc.isoformat() if sample else None,
                'age_min': round((now - sample.t_utc).total_seconds() / 60) if sample else f.age_min}

    sources = {
        'celestrak_gp': {**_src(f_tle, 'орбита'), 'epoch_utc': meta.epoch_utc.isoformat() if meta.epoch_utc else None,
                         'age_h': round((now - meta.epoch_utc).total_seconds() / 3600, 1) if meta.epoch_utc else None},
        'noaa_swpc_goes': _src(f_goes, 'протоны ≥10 МэВ', goes if mode == 'live' else None),
        'gfz_kp': _src(f_kp, 'Kp', kp if mode == 'live' else None),
        'ost1044_belts': {'role': 'захваченные протоны', 'status': belts.source, 'live_ok': None, 'from_cache': None},
        'ecss_grun': {'role': 'метеороиды', 'status': 'ECSS-E-ST-10-04C Rev.1: Grün (10-1), Table J-6, N = F·A·T; потоки даты не включены; '
                                                      'контроль 5,61e-7 на 400 км/1 м²/6 ч воспроизведён', 'live_ok': None, 'from_cache': None},
        '_layers': {'role': 'слои', 'status': 'орбита: %s; источники: %s; история: %s' % (ORBIT_SRC, SRC_LAYER, HIST_SRC)},
    }
    iso = lambda v: v.isoformat() if hasattr(v, 'isoformat') else v
    S = {
        'schema_version': '2.1', 'algorithm_version': ALGO_VERSION, 'computed_utc': now.isoformat(),
        'mode': MODE_RU[mode], 'mode_id': mode,
        'request': {'t0_utc': t0.isoformat(), 'duration_min': duration_min, 'search_min': search_min,
                    'windows': [w.start_utc.isoformat() for w in windows], 'window_offsets_min': list(window_offsets_min),
                    'thresholds': th.__dict__, 'disabled': disabled, 'cutoff_utc': cutoff_utc.isoformat() if cutoff_utc else None,
                    'T_months': T_months, 'scenario': scenario.__dict__ if is_sim else None},
        'trajectory_meta': {**{k: iso(v) for k, v in meta.__dict__.items()}, 'orbit_module': ORBIT_SRC},
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
                  {'goes': f_goes, 'kp': f_kp, 'tle': f_tle})
