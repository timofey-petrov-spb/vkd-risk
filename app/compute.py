# -*- coding: utf-8 -*-
"""Расчётный конвейер без интерфейса (Т7: получение, расчёт и интерфейс — три слоя).

run(params) собирает один снимок расчёта. Его используют:
  * app/main.py — рендер экрана и кнопки выгрузки из одного и того же снимка;
  * scripts/make_examples.py — сохранённые примеры расчётов для сдачи (Т8);
  * эксперименты — прогоны без Streamlit.

Слои А: орбита — vkd.orbit (A3) через vkd.integration.orbit_bridge, без заглушки;
прогнозы NOAA до отсечки — vkd.history.replay (A2) через vkd.integration.noaa_forecast;
живые источники (A4) и разбор DONKI (A2) — пока временные модули experiments/.

Исторические режимы НЕ обращаются к живым источникам: GOES и Kp берутся из архива
(или честно отсутствуют), TLE не нужен — орбита из OEM. Записи sources в снимке
описывают фактическое происхождение, а не результат живого запроса 2026 года.
"""
from __future__ import annotations

import io
import json
import os
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from vkd.assess.cutoff import apply_cutoff
from vkd.assess.magcoords import belt_coordinates
from vkd.assess.meteoroids import meteoroid_hits_track
from vkd.assess.trapped import BeltTable
from vkd.config import section as _cfg_section, settings_path
from vkd.explain.cards import cards_for_window
from vkd.integration.noaa_forecast import STATUS_RU as FC_STATUS_RU, noaa_forecasts
from vkd.integration.orbit_bridge import ORBIT_SRC, TLE_URL_UNKNOWN, build_orbit, provenance_summary
from vkd.types import SCHEMA_VERSION, Window
from vkd.windows.compare import Thresholds, action_span, assess_window, overlaps, recommend
from vkd.windows.scenario import Scenario, apply_to_windows, simulated_events, simulated_kp
from vkd.windows.sensitivity import robustness

try:
    from vkd.sources import goes_latest, kp_latest, tle_latest   # type: ignore  # A4 — когда появится
    SRC_LAYER = 'vkd.sources'
except ImportError:
    from experiments.stub_sources import Fetch, goes_latest, kp_latest, tle_latest
    SRC_LAYER = 'experiments.stub_sources — временно до A4: живой запрос, кеш, снимок'
try:
    from vkd.history import history_bundle    # type: ignore  # A2 — разбор содержания DONKI
    HIST_SRC = 'vkd.history'
except ImportError:
    from experiments.stub_history import history_bundle
    HIST_SRC = 'experiments.stub_history — временно до A2: события DONKI, время публикации по реестру A1'

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALGO_VERSION = '0.5.1'      # 19.09, второй круг: конец действия записи по её собственному началу, окно вне архива — «оснований недостаточно» в обоих режимах,
                            # покрытие механизма объявляется частичным без наблюдения Kp (0.5.0: правило по флюенсу и минутам, допуск как разброс разности, Kp разбора из ряда GFZ)
MODES = ('live', 'history_review', 'history_forecast')
MODE_RU = {'live': 'Текущая обстановка', 'history_review': 'Исторический разбор', 'history_forecast': 'Прогноз из прошлого'}
DONKI_ARCHIVE_DEFAULT = (datetime(2024, 5, 1, tzinfo=timezone.utc), datetime(2024, 7, 1, tzinfo=timezone.utc))

POLICY_NOTE = ('Правило команды, не норма: окно с протонным событием, бурей Kp ≥ 7 (наблюдение или прогноз) или сообщением '
               'о сближении автоматически не выбирается — его проверяет аналитик. Шкалы NOAA сами по себе не запрещают '
               'и не разрешают ВКД.')

# границы постановки (docs/case: длительность 1…8 ч, период начала до 24 ч, 2–3 окна)
DURATION_MIN_RANGE = (60, 480)
SEARCH_MIN_MAX = 1440
WINDOWS_RANGE = (2, 3)


@dataclass
class Result:
    S: dict                    # снимок для экрана и выгрузки
    raw_records: dict
    traj: list
    meta: Any
    assessments: list
    rec: Any
    cards: list                # карточки всех окон подряд (окно 1, затем окно 2, …), у каждой window_index
    events: list
    rob: Any
    goes: Any
    kp: Any
    excluded: list
    fetch_status: dict
    forecasts: list = None     # линии прогнозов NOAA (история)
    orbit: Any = None          # OrbitResult
    kp_obs: list = None        # исторический разбор: наблюдения Kp (от, до, значение) для ленты
    cards_by_window: dict = None   # start_utc -> карточки окна
    verification: dict = None      # прогноз из прошлого: что наблюдалось после отсечки (в расчёт не входит)


def validate_request(mode: str, t0: datetime, duration_min: int, search_min: int, window_offsets_min) -> None:
    """Границы постановки для любого вызова конвейера (Т7), не только для ползунков экрана."""
    if mode not in MODES:
        raise ValueError('неизвестный режим %r; допустимы %s' % (mode, ', '.join(MODES)))
    if not isinstance(t0, datetime) or t0.tzinfo is None or t0.utcoffset() is None:
        raise ValueError('начало периода t0 должно быть datetime с часовым поясом (UTC)')
    if not (DURATION_MIN_RANGE[0] <= int(duration_min) <= DURATION_MIN_RANGE[1]):
        raise ValueError('длительность ВКД %s мин вне границ постановки %d…%d мин' % (duration_min, *DURATION_MIN_RANGE))
    if not (0 <= int(search_min) <= SEARCH_MIN_MAX):
        raise ValueError('период поиска начала %s мин вне границ постановки 0…%d мин' % (search_min, SEARCH_MIN_MAX))
    offs = list(window_offsets_min)
    if not (WINDOWS_RANGE[0] <= len(offs) <= WINDOWS_RANGE[1]):
        raise ValueError('окон для сравнения должно быть %d…%d, задано %d' % (*WINDOWS_RANGE, len(offs)))
    if len(set(offs)) != len(offs):
        raise ValueError('сдвиги окон должны различаться: одинаковые окна сравнивать бессмысленно (%s)' % offs)
    for o in offs:
        if not (0 <= int(o) <= int(search_min)):
            raise ValueError('сдвиг окна %s мин вне периода поиска 0…%s мин' % (o, search_min))


def _donki_catalog_coverage() -> tuple:
    """Границы архива уведомлений DONKI — из реестра A1 (coverage.json: периоды запросов), а не из константы."""
    p = os.path.join(ROOT, 'data', 'source_registry_2024', 'donki', 'coverage.json')
    try:
        d = json.load(io.open(p, encoding='utf-8'))
        periods = d.get('query_periods') or []
        starts = [datetime.fromisoformat(x['start']).replace(tzinfo=timezone.utc) for x in periods]
        ends = [datetime.fromisoformat(x['end']).replace(tzinfo=timezone.utc) for x in periods]
        if starts and ends:
            return min(starts), max(ends), int(d.get('record_count') or 0), 'data/source_registry_2024/donki/coverage.json'
    except Exception:            # noqa: BLE001 — реестр недоступен: честно объявленная константа
        pass
    return DONKI_ARCHIVE_DEFAULT + (None, 'константа (реестр A1 не прочитан)')


_A1_BODIES: dict = {}
_PFU_RE = re.compile(r'>\s*10\s*MeV protons (?:had previously )?exceed(?:s|ed)\s+(\d[\d,.]*)\s*pfu', re.I)   # только канал шкалы S (>=10 МэВ)


def _a1_message_path(mid: str) -> Optional[str]:
    """messageID → путь к каноническому телу уведомления DONKI в реестре A1 (только чтение файла)."""
    if not _A1_BODIES:
        p = os.path.join(ROOT, 'data', 'source_registry_2024', 'donki', 'records.json')
        try:
            for r in json.load(io.open(p, encoding='utf-8')).get('records', []):
                _A1_BODIES[r['release_id']] = os.path.join(ROOT, r['raw_path'])
        except Exception:        # noqa: BLE001 — реестра нет: уровни остаются неизвестными, это объявляется
            _A1_BODIES['_missing'] = ''
    return _A1_BODIES.get(mid)


def _with_sep_levels(events: list) -> list:
    """Уровень протонного события из тела уведомления DONKI («… exceeded 10 pfu») дописывается в note
    как «pfu=N», чтобы правило различало S1–S2 и S3+ (Т3: уровень определяет класс условия).
    Тело сообщения — та же запись реестра A1, по которой событие отобрано по времени публикации."""
    out = []
    for e in events:
        if e.kind_of_event == 'SEP' and e.event_id.startswith('donki_msg#') and 'pfu' not in (e.note or ''):
            path = _a1_message_path(e.event_id.split('#', 1)[1])
            level = None
            if path and os.path.exists(path):
                try:
                    body = json.load(io.open(path, encoding='utf-8')).get('messageBody', '') or ''
                    vals = [float(m.group(1).replace(',', '')) for m in _PFU_RE.finditer(body)]
                    level = max(vals) if vals else None
                except (OSError, ValueError):
                    level = None
            if level is not None:
                e = replace(e, note=(e.note + ', ' if e.note else '') + 'pfu=%g (по тексту уведомления)' % level)
        out.append(e)
    return out


def _empty_fetch(source_id: str, why: str):
    return Fetch(source_id, None, None, None, None, why, None, None) if 'Fetch' in globals() else None


def run(mode: str, t0: datetime, duration_min: int, search_min: int, window_offsets_min: list[int],
        disabled: Optional[dict] = None, thresholds: Optional[Thresholds] = None,
        scenario: Optional[Scenario] = None, T_months: int = 6,
        fetched: Optional[tuple] = None, now: Optional[datetime] = None,
        tle_override_path: Optional[str] = None) -> Result:
    """tle_override_path — воспроизведение сохранённого расчёта текущего режима: орбита
    строится по сохранённому TLE, а не по текущему (Т8). В исторических режимах орбита
    берётся из архива OEM (A1/A3) и от TLE не зависит; живые источники не вызываются."""
    validate_request(mode, t0, duration_min, search_min, window_offsets_min)
    disabled = disabled or {'goes': False, 'kp': False}
    th = thresholds or Thresholds.from_settings()
    scenario = scenario or Scenario('none')
    now = now or datetime.now(timezone.utc)
    ref_now = now if mode == 'live' else t0          # момент, от которого считаются давности: в истории — t0
    cutoff_utc = t0 if mode == 'history_forecast' else None
    horizon_min = search_min + duration_min
    is_sim = bool(scenario.work_delay_min or scenario.sep_onset_offset_min is not None or scenario.kp_override is not None)

    # ------------------------------------------------------------ источники
    if mode == 'live':
        if fetched is None:
            fetched = (goes_latest(disabled=disabled['goes']), kp_latest(disabled=disabled['kp']), tle_latest(disabled=False))
        (goes, goes_raw, f_goes), (kp, kp_raw, f_kp), (tle_text, f_tle) = fetched
    else:
        # история: живые запросы не делаются и не учитываются, даже если экран их передал
        why = 'в историческом режиме живой источник не запрашивается'
        goes, goes_raw, f_goes = None, {}, _empty_fetch('noaa_swpc_goes', why)
        kp, kp_raw, f_kp = None, {}, _empty_fetch('gfz_kp', why)
        tle_text, f_tle = None, _empty_fetch('celestrak_gp', 'в историческом режиме орбита из архива OEM, TLE не нужен')
    # Т6: два разных состояния источника. 'cache' (или True) — имитация отказа: живого запроса нет,
    # берётся кеш с давностью, покрытие становится частичным и объявляется. 'off' — источник
    # исключён: данных нет, покрытие обязательной линии NONE → рекомендации нет. Ни одно из них
    # не превращается в «благоприятно».
    if disabled.get('goes') == 'off' and f_goes is not None:
        goes, goes_raw = None, {}
        f_goes = replace(f_goes, ok=False, from_cache=False, status_ru='источник исключён пользователем — данных нет', payload=None, raw_path=None)
    if disabled.get('kp') == 'off' and f_kp is not None:
        kp, kp_raw = None, {}
        f_kp = replace(f_kp, ok=False, from_cache=False, status_ru='источник исключён пользователем — данных нет', payload=None, raw_path=None)
    events, hist_raw, excluded, fc_lines, fc_raw, forecasts, kp_obs, verification = [], {}, [], [], {}, [], [], None
    catalog = None
    kp_src_note = None
    if mode != 'live':
        c0, c1, n_msg, cat_src = _donki_catalog_coverage()
        catalog = (c0, c1)
        h_samples, h_events, hist_raw = history_bundle()
        cut = apply_cutoff(h_samples, h_events, [], cutoff_utc)
        excluded = list(cut.excluded)
        kp_hist = [s for s in cut.samples if s.channel_id == 'kp' and s.t_utc <= t0]
        kp = max(kp_hist, key=lambda s: s.t_utc) if (kp_hist and disabled.get('kp') != 'off') else None
        kp_raw = {kp.raw_record_id: hist_raw.get(kp.raw_record_id)} if kp else {}
        if disabled.get('kp') == 'off':
            kp_src_note = 'источник исключён пользователем — данных нет'
        elif kp is None:
            kp_src_note = ('строгий режим: наблюдений Kp с доказанной публикацией до отсечки в архиве нет (окончательный ряд GFZ '
                           'и Kp карточек GST DONKI не имеют собственного времени публикации по интервалам)' if mode == 'history_forecast'
                           else 'разбор: наблюдений Kp в архиве (ряд GFZ %s — %s, резерв — карточки GST DONKI) до %s нет' % (
                               DONKI_ARCHIVE_DEFAULT[0].strftime('%d.%m.%Y'), (DONKI_ARCHIVE_DEFAULT[1] - timedelta(minutes=1)).strftime('%d.%m.%Y'),
                               t0.strftime('%Y-%m-%d %H:%MZ')))
        if mode == 'history_review':      # разбор: наблюдения Kp вокруг периода — контекст ленты, не вход строгого режима
            kp_obs = sorted({(s.valid_from_utc, s.valid_to_utc, s.value) for s in h_samples
                             if s.channel_id == 'kp' and s.valid_from_utc and s.valid_to_utc
                             and t0 - timedelta(hours=12) <= s.t_utc <= t0 + timedelta(minutes=horizon_min + 180)})
        # события, чей интервал касается [t0 − 6 ч, конец горизонта]; давность публикации не ограничивается —
        # прогноз прихода выброса, выпущенный за трое суток, всё равно относится к окну
        def _touches(e):
            # то же правило пересечения, что при отборе условий в compare.action_span/overlaps:
            # конец действия по настройке своего типа события, пересечение не меньше минуты
            a0, a1 = action_span(e, th)
            if a0 is None:
                return True
            return overlaps(a0, a1, t0 - timedelta(hours=6), t0 + timedelta(minutes=horizon_min))
        events = [e for e in cut.events if _touches(e)]
        # прогнозы NOAA, выпущенные до отсечки (в разборе — до начала периода): A1/A2 через адаптер Б
        fc_lines, fc_raw = noaa_forecasts(t0, t0, t0 + timedelta(minutes=horizon_min))
        forecasts = [s for line in fc_lines for s in line.samples]
        if mode == 'history_forecast':
            # проверка после отсечки: что наблюдалось потом — В РАСЧЁТ НЕ ВХОДИТ, только сопоставление (Т4, О6)
            h_end = t0 + timedelta(minutes=horizon_min)
            kp_after = sorted({(s.valid_from_utc, s.valid_to_utc, s.value, s.raw_record_id) for s in h_samples
                               if s.channel_id == 'kp' and s.valid_from_utc and s.valid_to_utc
                               and s.valid_to_utc > t0 and s.valid_from_utc < h_end})
            ev_after = [e for e in h_events if e.published_utc and e.published_utc > cutoff_utc and e.published_utc <= h_end
                        and e.kind_of_event in ('SEP', 'GST')]
            first_storm = next(((a, v) for a, b, v, _ in kp_after if v >= th.kp_check), None)
            kp_max = max((v for _, _, v, _ in kp_after), default=None)
            summary = 'условие поставлено в %s; факт: ' % t0.strftime('%H:%MZ')
            summary += ('Kp %s с %s, максимум %s' % (('%.2f' % first_storm[1]).replace('.', ','), first_storm[0].strftime('%d.%m %H:%MZ'),
                                                     ('%.2f' % kp_max).replace('.', ',')) if first_storm
                        else ('максимум Kp %s, бури Kp ≥ %.0f не было' % (('%.2f' % kp_max).replace('.', ','), th.kp_check) if kp_max is not None
                              else 'наблюдений Kp в архиве (ряд GFZ, резерв — карточки GST DONKI) на горизонте нет'))
            seps = [e for e in ev_after if e.kind_of_event == 'SEP']
            if seps:
                summary += '; протонное событие: первая публикация %s' % min(e.published_utc for e in seps).strftime('%d.%m %H:%MZ')
            verification = {
                'note': 'в расчёт не входит: наблюдения и публикации после отсечки, только для сопоставления прогноза с фактом',
                'cutoff_utc': cutoff_utc.isoformat(), 'horizon_to_utc': h_end.isoformat(),
                'kp_obs': [{'from_utc': a.isoformat(), 'to_utc': b.isoformat(), 'kp': v, 'record': rid} for a, b, v, rid in kp_after],
                'events': [{'id': e.event_id, 'kind': e.kind_of_event, 'published_utc': e.published_utc.isoformat(),
                            'start_utc': e.start_utc.isoformat() if e.start_utc else None, 'note': (e.note or '')[:120]} for e in ev_after],
                'summary': summary,
            }
        events = _with_sep_levels(events)
    kp = simulated_kp(kp, t0, scenario)
    events = events + simulated_events(t0, scenario)

    # ------------------------------------------------------------ траектория (A3 через мост Б)
    if tle_override_path:
        tle_text = open(tle_override_path, encoding='utf-8').read()
        f_tle = replace(f_tle, status_ru='TLE из сохранённого расчёта (воспроизведение)', ok=False, from_cache=True)
    tle_fetched = getattr(f_tle, 'fetched_utc', None)
    # момент расчёта в текущем режиме — не раньше момента получения TLE: иначе A3 честно ставит
    # «реконструкция» рядом со статусом «строго» (Т2)
    live_cutoff = max(now, t0, tle_fetched) if tle_fetched else max(now, t0)
    orb = build_orbit(mode, t0, horizon_min, th.saa_B_threshold_nT, tle_text=tle_text,
                      tle_fetched_utc=tle_fetched, tle_available_utc=tle_fetched,
                      tle_url=(getattr(f_tle, 'url', None) or TLE_URL_UNKNOWN),
                      tle_evidence=getattr(f_tle, 'status_ru', ''), max_tle_age_days=th.tle_max_age_days,
                      cutoff_utc=(cutoff_utc if mode != 'live' else live_cutoff))
    meta = orb.meta
    # координаты для таблиц ОСТ — эксцентричный диполь (Б): центральный диполь A3 в ядре аномалии
    # даёт L ниже сетки и нулевой поток на всей трассе (см. vkd/assess/magcoords.py); |B| — от A3
    coeff_name = 'IGRF13.shc' if t0.year < 2025 else 'IGRF14.shc'
    coeff_path = os.path.join(ROOT, 'data', 'orbit', coeff_name)
    traj, belt_coords = belt_coordinates(orb.points, coeff_path)
    if traj:
        # в выгрузку — репозиторный путь и хеш коэффициентов из data/orbit/manifest.json, не путь машины (Т8)
        try:
            man = json.load(io.open(os.path.join(ROOT, 'data', 'orbit', 'manifest.json'), encoding='utf-8'))
            rec_c = next((r for r in man['records'] if r['file'] == coeff_name), {})
        except Exception:        # noqa: BLE001
            rec_c = {}
        belt_coords = {**belt_coords, 'coefficients': 'data/orbit/' + coeff_name,
                       'coefficients_sha256': rec_c.get('sha256'), 'coefficients_record': rec_c.get('raw_record_id')}
        orb.provenance.setdefault('limitations', []).append(
            'Для входа в таблицы ОСТ прил. А использованы L и B/B0 эксцентричного диполя (Б, magcoords), '
            'не значения A3: центральный диполь даёт L < 1,14 в ядре аномалии; %d из %d точек с B/B0 < 1 помечены.'
            % (belt_coords['n_inconsistent_BB0'], belt_coords['n']))
    orbit_ids = tuple((orb.provenance or {}).get('records', {}).keys()) or ('trajectory',)

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

    def _assess(w, tr, th_i):
        return assess_window(w, tr, belts, goes, kp, [], th_i, ref_now, events=events, catalog_coverage=catalog,
                             mmod_hits=mmod[w.start_utc][0], mmod_rule=mmod[w.start_utc][1],
                             mmod_cov_fraction=mmod[w.start_utc][2], forecasts=forecasts,
                             trajectory_record_ids=orbit_ids, cutoff_utc=cutoff_utc)

    def _assess_all(tr, kw):
        th_i = replace(th, **kw)
        return [_assess(w, tr, th_i) for w in windows]

    def _decide(A_i, kw):
        return recommend(A_i, replace(th, **kw))

    rob = robustness(traj, windows, _assess_all, _decide,
                     thr_grid=[th.saa_B_threshold_nT - 2000, th.saa_B_threshold_nT, th.saa_B_threshold_nT + 2000],
                     e_grid=[12.5, 30.0, 50.0], base_thr=th.saa_B_threshold_nT, base_e=th.e_min_MeV,
                     min_tol_min=1.0, min_tol_ratio=th.fluence_equiv_ratio)
    th = replace(th, equiv_tol_min=rob.tol_min, fluence_equiv_ratio=rob.tol_ratio)
    assessments = [_assess(w, traj, th) for w in windows]
    rec = recommend(assessments, th)
    thr_txt = '/'.join('%.0f' % x for x in rob.grid[0])
    def _pair_txt(pair):
        if not pair:
            return ''
        i_b = next(i + 1 for i, a in enumerate(assessments) if a.window.start_utc.isoformat() == pair[0])
        i_s = next(i + 1 for i, a in enumerate(assessments) if a.window.start_utc.isoformat() == pair[1])
        return ' между окнами %d и %d' % (i_b, i_s)
    d_txt = ('разность минут%s на сетке от %.0f до %.0f' % (_pair_txt(rob.pair), min(rob.diff_by_thr.values()), max(rob.diff_by_thr.values()))
             if rob.diff_by_thr else 'разность минут на сетке не определена (меньше двух окон без условий)')
    r_txt = ('отношение флюенсов%s по каналу от ×%.2f до ×%.2f' % (_pair_txt(rob.pair_fluence), min(rob.ratio_by_e.values()), max(rob.ratio_by_e.values()))
             if rob.ratio_by_e else 'отношение флюенсов не определено')
    rec = replace(rec, is_simulated=is_sim,
                  missing=rec.missing + (('орбита недоступна: %s' % orb.error,) if orb.error else ()),
                  tolerance_basis='допуск %.0f мин — разброс разности минут в аномалии между окнами при порогах %s нТл (%s); '
                                  'допуск ×%.2f по флюенсу — не меньше настройки ×%.2f и разброса по каналу %s МэВ (%s); '
                                  'порядок окон при нулевом допуске на сетке %s; %s' % (
                                      th.equiv_tol_min, thr_txt, d_txt, th.fluence_equiv_ratio,
                                      max(1.0, (thresholds or Thresholds.from_settings()).fluence_equiv_ratio),
                                      '/'.join('%g' % x for x in rob.grid[1]), r_txt,
                                      'сохраняется' if rob.ranking_stable else 'МЕНЯЕТСЯ',
                                      'выбор устойчив: на всей сетке одинаковы и вердикт, и предпочтительное окно'
                                      if rob.stable else
                                      'ВЫБОР МЕНЯЕТСЯ на сетке: вердикт или предпочтительное окно на ней не одни и те же'))
    samples = {**({goes.raw_record_id: goes} if goes else {}), **({kp.raw_record_id: kp} if kp else {}),
               **{s.raw_record_id: s for s in forecasts}}
    cards_by_window = {a.window.start_utc: cards_for_window(a, samples, events=events, window_index=i + 1, meta=meta,
                                                            trajectory_ids=orbit_ids, cutoff_utc=cutoff_utc)
                       for i, a in enumerate(assessments)}
    cards = [c for a in assessments for c in cards_by_window[a.window.start_utc]]

    # ------------------------------------------------------------ снимок
    iso = lambda v: v.isoformat() if hasattr(v, 'isoformat') else v
    ev_raw = {e.raw_record_id: hist_raw[e.raw_record_id] for e in events if e.raw_record_id in hist_raw}
    raw_records = {**(goes_raw or {}), **(kp_raw or {}), **ev_raw, **fc_raw,
                   'orbit_provenance': orb.provenance}
    if mode == 'live':
        raw_records['iss.tle'] = {'text': tle_text, 'epoch_utc': iso(meta.epoch_utc) if meta and meta.epoch_utc else None,
                                  'fetch': getattr(f_tle, 'status_ru', None), 'url': getattr(f_tle, 'url', None) or TLE_URL_UNKNOWN}

    def _src(f, role, sample=None):
        # давность — от КОНЦА интервала измерения (valid_to_utc), как в compare.py: у Kp GFZ
        # t_utc — начало трёхчасового интервала, и таблица источников расходилась с фактором на 3 ч
        ref_t = (sample.valid_to_utc or sample.t_utc) if sample else None
        return {'role': role, 'status': f.status_ru, 'live_ok': f.ok, 'from_cache': f.from_cache,
                'origin': ('живой запрос' if f.ok else ('кеш или снимок репозитория' if f.from_cache else 'данных нет')),
                'fetched_utc': iso(f.fetched_utc) if f.fetched_utc else None,
                'data_utc': iso(sample.t_utc) if sample else None,
                'age_min': round((ref_now - ref_t).total_seconds() / 60) if sample else f.age_min}

    orbit_src = {'role': 'орбита', 'status': orb.status_ru, 'strictness': orb.strictness,
                 'origin': ('живой запрос TLE' if mode == 'live' and getattr(f_tle, 'ok', None) else
                            ('кеш/снимок TLE' if mode == 'live' else 'архив OEM NASA/JSC (реестр A1)')),
                 'live_ok': f_tle.ok if mode == 'live' else None, 'from_cache': f_tle.from_cache if mode == 'live' else None,
                 'fetched_utc': iso(meta.fetched_utc) if meta else None,
                 'epoch_utc': iso(meta.epoch_utc) if meta and meta.epoch_utc else None,
                 'age_h': round((now - meta.epoch_utc).total_seconds() / 3600, 1) if meta and meta.epoch_utc else None,
                 'source_id': meta.source_id if meta else None}
    sources = {'orbit': orbit_src}
    if mode == 'live':
        sources['noaa_swpc_goes'] = _src(f_goes, 'протоны ≥10 МэВ', goes)
        sources['gfz_kp'] = _src(f_kp, 'Kp', kp if (kp and kp.source_id != 'scenario') else None)
    else:
        c0, c1, n_msg, cat_src = _donki_catalog_coverage()
        sources['noaa_swpc_goes'] = {'role': 'протоны ≥10 МэВ', 'status': 'архива наблюдений GOES за 2024 в реестре нет — канал без данных; '
                                                                           'живой источник в историческом режиме отключён',
                                     'live_ok': None, 'from_cache': None, 'origin': 'нет данных', 'fetched_utc': None, 'data_utc': None, 'age_min': None}
        kp_origin = {'gfz_kp_archive': 'архив GFZ (окончательный ряд Kp по 3-часовым интервалам, без времени публикации)',
                     'nasa_donki_gst': 'архив DONKI (карточки GST)'}.get(kp.source_id if kp else '', 'архив (%s)' % kp.source_id if kp else 'нет данных')
        sources['gfz_kp'] = {'role': 'Kp (в истории — окончательный ряд GFZ; резерв — карточки GST DONKI)',
                             'status': (kp_src_note or 'разбор: Kp из %s, запись %s' % (kp_origin.split(' (')[0], kp.raw_record_id)) if not (kp and kp.source_id == 'scenario')
                             else 'сценарий «что если»: моделируемое значение',
                             'live_ok': None, 'from_cache': None,
                             'origin': kp_origin if kp and kp.source_id != 'scenario' else ('сценарий' if kp else 'нет данных'),
                             'fetched_utc': None,
                             'data_utc': iso(kp.t_utc) if kp and kp.source_id != 'scenario' else None,
                             'age_min': round((t0 - (kp.valid_to_utc or kp.t_utc)).total_seconds() / 60)
                             if kp and kp.source_id != 'scenario' else None}
        sources['donki_archive'] = {'role': 'события и уведомления (SEP, GST, прогнозы ENLIL)',
                                    'status': 'архив DONKI %s — %s%s; отбор по времени публикации (%s)' % (
                                        c0.strftime('%d.%m.%Y'), (c1 - timedelta(minutes=1)).strftime('%d.%m.%Y'),
                                        ', %d сообщений' % n_msg if n_msg else '', HIST_SRC.split(' — ')[0]),
                                    'live_ok': None, 'from_cache': None, 'origin': 'архив A1 (%s)' % cat_src,
                                    'fetched_utc': None, 'data_utc': iso(t0), 'age_min': None,
                                    'events_used': len(events), 'excluded_by_cutoff': len(excluded)}
    sources.update({
        'ost1044_belts': {'role': 'захваченные протоны', 'status': '%s; файл %s, sha256 %s…; запись %s' % (
            belts.source, belts.file, belts.sha256[:12], belts.raw_record_id), 'live_ok': None, 'from_cache': None,
            'origin': 'таблица стандарта в репозитории'},
        'ecss_grun': {'role': 'метеороиды', 'status': 'ECSS-E-ST-10-04C Rev.1: Grün (10-1), Table J-6 по высоте трассы, '
                                                      'интеграл по dt (спецификация A5, grun-ecss-2020-v1); потоки даты — только признак '
                                                      'по календарю IMO; контроль 5,609728e-7 на 400 км/1 м²/6 ч воспроизведён',
                      'live_ok': None, 'from_cache': None, 'origin': 'модель стандарта в репозитории'},
        '_layers': {'role': 'слои', 'status': 'орбита: %s; источники: %s; история: %s' % (ORBIT_SRC, SRC_LAYER, HIST_SRC)},
    })
    if mode != 'live':
        for line in fc_lines:
            lr = line.last_release_before_cutoff
            sources['noaa_forecast_' + line.channel_id] = {
                'role': line.label_ru, 'status': FC_STATUS_RU.get(line.status, line.status) + (
                    '; выпуск %s от %s' % (line.release_id, line.published_utc.strftime('%Y-%m-%d %H:%MZ')) if line.record_id_ok() else
                    ('; ' + line.reason if line.reason else '')),
                'live_ok': None, 'from_cache': None, 'origin': 'архив A1 (data/source_registry_2024/noaa)' + (
                    ', sha256 %s…' % (fc_raw.get(line.raw_record_id, {}).get('sha256') or '')[:12] if line.raw_record_id else ''),
                'coverage_fraction': line.coverage_fraction,
                'last_release_before_cutoff': lr}

    meta_dict = ({**{k: iso(v) for k, v in meta.__dict__.items()}} if meta else
                 {'source_id': None, 'method': None, 'frame': None, 'epoch_utc': None, 'coverage_from_utc': None,
                  'coverage_to_utc': None, 'created_utc': None, 'available_utc': None, 'fetched_utc': None,
                  'is_reconstruction': None, 'field_model': None})
    effective_config = {'thresholds': th.__dict__, 'thresholds_requested': (thresholds or Thresholds.from_settings()).__dict__,
                        'history': _cfg_section('history'), 'sources': _cfg_section('sources'), 'ui': _cfg_section('ui'),
                        'settings_path': settings_path(), 'mmod': {'area_m2': 1.0, 'm_min_g': 1e-3, 'solar_activity': 'min'},
                        'robustness_grid': {'thr_nT': list(rob.grid[0]), 'e_min_MeV': list(rob.grid[1])}}
    S = {
        'schema_version': SCHEMA_VERSION, 'algorithm_version': ALGO_VERSION, 'computed_utc': now.isoformat(),
        'mode': MODE_RU[mode], 'mode_id': mode,
        'request': {'t0_utc': t0.isoformat(), 'duration_min': duration_min, 'search_min': search_min,
                    'windows': [w.start_utc.isoformat() for w in windows], 'window_offsets_min': list(window_offsets_min),
                    'thresholds': th.__dict__, 'disabled': disabled, 'cutoff_utc': cutoff_utc.isoformat() if cutoff_utc else None,
                    'T_months': T_months, 'scenario': scenario.__dict__ if is_sim else None},
        'effective_config': effective_config,
        'trajectory_meta': {**meta_dict, 'orbit_module': ORBIT_SRC, 'status': orb.status_ru, 'strictness': orb.strictness,
                            'error': orb.error, 'n_points': len(traj), 'provenance': provenance_summary(orb.provenance),
                            'belt_coordinates': belt_coords, 'record_ids': list(orbit_ids),
                            'tle_text': tle_text if mode == 'live' else None,
                            'tle_fetch_status': getattr(f_tle, 'status_ru', None) if mode == 'live' else None},
        'windows': [{'index': i + 1, 'start_utc': a.window.start_utc.isoformat(), 'duration_min': a.window.duration_min, 'mechanisms': [
            {'id': m.mechanism_id, 'mandatory': m.mandatory, 'coverage': m.coverage.value, 'needs_check': list(m.needs_check_reasons),
             'coverage_notes': list(m.coverage_notes),
             'conditions': [{'kind': c.kind, 'severity': c.severity, 'text': c.text, 'event_ids': list(c.event_ids),
                             'interval_utc': [iso(x) for x in c.interval_utc], 'level': c.level_note,
                             'sources': list(c.sources_ru), 'simulated': c.is_simulated} for c in m.conditions],
             'factors': [{'name': f.name, 'value': f.value, 'unit': f.unit, 'kind': f.kind.value, 'presence': f.presence.value,
                          'coverage': f.coverage.value, 'records': list(f.record_ids), 'rule': f.rule_applied, 'limits': f.limits_note,
                          'horizon_utc': iso(f.horizon_utc)}
                         for f in m.factors]} for m in a.mechanisms]} for i, a in enumerate(assessments)],
        'recommendation': {'verdict': rec.verdict, 'rule': rec.rule_applied, 'reasons': list(rec.reasons), 'missing': list(rec.missing),
                           'preferred': rec.preferred.start_utc.isoformat() if rec.preferred else None,
                           'preferred_index': next((i + 1 for i, a in enumerate(assessments) if rec.preferred and a.window.start_utc == rec.preferred.start_utc), None),
                           'per_mechanism': rec.per_mechanism_comparison, 'tolerance_basis': rec.tolerance_basis},
        'cards': [{k: (v.value if hasattr(v, 'value') else v) for k, v in c.__dict__.items()} for c in cards],
        'sources': sources,
        'forecasts': [{'channel': line.channel_id, 'label': line.label_ru, 'source_id': line.source_id, 'status': line.status,
                       'status_ru': FC_STATUS_RU.get(line.status, line.status), 'coverage_fraction': line.coverage_fraction,
                       'release_id': line.release_id, 'published_utc': iso(line.published_utc), 'record': line.raw_record_id,
                       'reason': line.reason, 'gaps': list(line.gaps), 'last_release_before_cutoff': line.last_release_before_cutoff,
                       'cells': [{'from': iso(s.valid_from_utc), 'to': iso(s.valid_to_utc), 'value': s.value} for s in line.samples]}
                      for line in fc_lines],
        'coverage_declared': list(assessments[0].coverage_declared), 'coverage_missing': list(assessments[0].coverage_missing),
        'policy_note': POLICY_NOTE,
        'is_simulated': is_sim,
        'history': {'provider': HIST_SRC if mode != 'live' else None, 'excluded_by_cutoff': excluded,
                    'catalog_coverage': ({'from_utc': iso(catalog[0]), 'to_utc': iso(catalog[1])} if catalog else None),
                    'events_used': [{'id': e.event_id, 'kind': e.kind_of_event, 'published_utc': iso(e.published_utc),
                                     'start_utc': iso(e.start_utc), 'simulated': e.is_simulated} for e in events]},
        'verification': verification,
        'robustness': {'stable': rob.stable, 'ranking_stable': rob.ranking_stable, 'diff_spread_min': rob.diff_spread_min,
                       'fluence_ratio_spread': rob.fluence_ratio_spread, 'tol_min': rob.tol_min, 'tol_ratio': rob.tol_ratio,
                       'saa_spread_min': rob.saa_spread_min, 'grid': rob.grid, 'pair': list(rob.pair),
                       'diff_by_thr': {'%.0f' % k: v for k, v in rob.diff_by_thr.items()},
                       'ratio_by_e': {'%g' % k: v for k, v in rob.ratio_by_e.items()},
                       'preferred_by_grid': {'%.0f nT / %g MeV' % k: v for k, v in rob.preferred_starts.items()},
                       'verdict_by_grid': {'%.0f nT / %g MeV' % k: v for k, v in (rob.verdict_by_grid or {}).items()},
                       'ranking_by_grid': {'%.0f nT / %g MeV' % k: v for k, v in rob.ranking_by_grid.items()}},
    }
    return Result(S, raw_records, traj, meta, assessments, rec, cards, events, rob, goes, kp, excluded,
                  {'goes': f_goes, 'kp': f_kp, 'tle': f_tle}, forecasts=fc_lines, orbit=orb, kp_obs=kp_obs,
                  cards_by_window=cards_by_window, verification=verification)
