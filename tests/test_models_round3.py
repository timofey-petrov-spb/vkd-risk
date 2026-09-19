# -*- coding: utf-8 -*-
"""Находки третьего круга по области «модели»: подпись таблицы сравнения окон не спорит
ни с вердиктом, ни с сеткой; имя режима в статусе источника настоящее; давность не бывает
отрицательной; ограничения карточки условия относятся к тем сигналам, которые в условии есть;
порог различимости окон по метеороидам взят из настроек и печатается с происхождением;
у каждой записи ряда наблюдений есть либо время публикации, либо названная причина его отсутствия.

Каждый тест закрепляет одну найденную ошибку, а не «работает вообще».
"""
import os
import re
from datetime import datetime, timedelta, timezone

from app.compute import run, tolerance_caption
from tests.test_integration import _fetched
from vkd.config import section as cfg_section
from vkd.explain.format import STORM_SIGNAL_RU, storm_signal_kinds
from vkd.types import Condition, EnvironmentSample, Kind
from vkd.windows.compare import Thresholds
from vkd.windows.sensitivity import Robustness

UTC = timezone.utc
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TLE = os.path.join(ROOT, 'data', 'orbit', 'iss.tle')
T_GANNON = datetime(2024, 5, 10, 12, 0, tzinfo=UTC)
GRID = ((22000.0, 24000.0, 26000.0), (12.5, 30.0, 50.0))


def _rob(*, preferred_starts, ranking_by_grid, stable, ranking_stable,
         diff_by_thr=None, ratio_by_e=None):
    """Сетка устойчивости в чистом виде: подпись проверяется без орбиты и источников."""
    return Robustness(preferred_starts=preferred_starts, ranking_by_grid=ranking_by_grid,
                      diff_spread_min=0.0, fluence_ratio_spread=1.0, tol_min=1.0, tol_ratio=1.5,
                      ranking_stable=ranking_stable, stable=stable, grid=GRID,
                      diff_by_thr=dict(diff_by_thr or {}), ratio_by_e=dict(ratio_by_e or {}),
                      pair=(), pair_fluence=(), saa_spread_min=0.0,
                      verdict_by_grid={k: 'equivalent' for k in preferred_starts})


# --------------------------------------------------- подпись таблицы 1: три ветки хвоста
def test_tolerance_caption_branch_preferred_window_is_stable():
    """Ветка 1: предпочтительное окно есть и на всей сетке одно — «выбор устойчив»."""
    th = Thresholds()
    keys = [(t, e) for t in GRID[0] for e in GRID[1]]
    rob = _rob(preferred_starts={k: 'W1' for k in keys}, ranking_by_grid={k: 'W1' for k in keys},
               stable=True, ranking_stable=True, diff_by_thr={22000.0: 10.0, 24000.0: 12.0, 26000.0: 14.0},
               ratio_by_e={12.5: 1.1, 30.0: 1.2, 50.0: 1.3})
    t = tolerance_caption(th, rob, preferred=object(), requested_fluence_ratio=1.5)
    assert 'выбор устойчив: на всей сетке одно и то же предпочтительное окно и тот же вердикт' in t
    assert 'ВЫБОР МЕНЯЕТСЯ' not in t
    assert 'допуск %.0f мин' % th.equiv_tol_min in t          # допуск вычислен — число печатается


def test_tolerance_caption_branch_no_preferred_window_anywhere():
    """Ветка 2: предпочтительного окна нет ни здесь, ни на сетке — «сравнивать нечего».
    Прежняя подпись в этом случае обещала «одинаковы и вердикт, и предпочтительное окно»,
    которого не существует, и печатала числовой допуск, который не вычислялся."""
    keys = [(t, e) for t in GRID[0] for e in GRID[1]]
    rob = _rob(preferred_starts={k: None for k in keys}, ranking_by_grid={k: None for k in keys},
               stable=True, ranking_stable=True)
    t = tolerance_caption(Thresholds(), rob, preferred=None, requested_fluence_ratio=1.5)
    assert 'предпочтительного окна нет ни в одной ячейке сетки' in t
    assert 'выбор устойчив' not in t and 'предпочтительное окно' not in t
    assert 'допуск 1 мин' not in t and 'допуск равнозначности по минутам не вычисляется' in t
    assert 'допуск равнозначности по флюенсу не вычисляется' in t
    # «порядок окон сохраняется» при полном отсутствии выбора — тоже неправда
    assert 'порядок окон при нулевом допуске не определён' in t


def test_tolerance_caption_branch_choice_appears_only_on_part_of_the_grid():
    """Ветка 3: здесь предпочтительного окна нет, а в части ячеек сетки оно появляется."""
    keys = [(t, e) for t in GRID[0] for e in GRID[1]]
    prefs = {k: (None if k[0] == 22000.0 else 'W2') for k in keys}
    rob = _rob(preferred_starts=prefs, ranking_by_grid={k: 'W2' for k in keys},
               stable=False, ranking_stable=True, diff_by_thr={22000.0: 2.0, 24000.0: 25.0},
               ratio_by_e={12.5: 1.32, 50.0: 1.35})
    t = tolerance_caption(Thresholds(), rob, preferred=None, requested_fluence_ratio=1.5)
    assert 'ВЫБОР МЕНЯЕТСЯ на сетке' in t and 'в части её ячеек оно есть' in t
    assert 'выбор устойчив' not in t


def test_tolerance_caption_branch_no_preferred_but_verdict_changes():
    """Предпочтительного окна нет нигде, но вердикт на сетке меняется — это не «устойчиво»."""
    keys = [(t, e) for t in GRID[0] for e in GRID[1]]
    rob = _rob(preferred_starts={k: None for k in keys}, ranking_by_grid={k: None for k in keys},
               stable=False, ranking_stable=True)
    t = tolerance_caption(Thresholds(), rob, preferred=None, requested_fluence_ratio=1.5)
    assert 'ВЕРДИКТ на сетке меняется' in t and 'выбор устойчив' not in t


def test_gannon_caption_does_not_promise_a_tolerance_it_did_not_compute():
    """Тот же дефект на настоящем расчёте: «Гэннон», вердикт «все окна требуют проверки»."""
    r = run('history_forecast', T_GANNON, 360, 720, [0, 240], fetched=_fetched(), now=T_GANNON)
    t = r.rec.tolerance_basis
    # Вердикт — «все окна требуют проверки», как и сказано в описании теста: у обоих окон условие.
    # До 19.09 сюда же приходил отказ по частичному покрытию и закрывал собой настоящую причину.
    assert r.rec.verdict == 'all_need_check' and r.rec.preferred is None
    assert 'выбор устойчив' not in t
    assert 'допуск 1 мин' not in t
    assert 'предпочтительное окно' not in t


def test_review_caption_does_not_call_equivalent_windows_a_stable_choice():
    """Разбор 25.06: вердикт «равнозначны», предпочтительного окна нет — «выбор устойчив» лгало."""
    t0 = datetime(2024, 6, 25, 12, 0, tzinfo=UTC)
    r = run('history_review', t0, 360, 720, [120, 360], fetched=_fetched(), now=t0)
    t = r.rec.tolerance_basis
    if r.rec.preferred is None:
        assert 'выбор устойчив' not in t
    else:
        assert ('выбор устойчив' in t) == bool(r.rob.stable)


# --------------------------------------------------- имя режима в статусе источника
def test_strict_mode_kp_status_is_not_called_review():
    """Строгий прогноз подписывался словом «разбор» — именем режима разбора после факта,
    на различии с которым держится вся проверка отсечкой. Плюс «из уведомление» без падежа."""
    t0 = datetime(2024, 5, 10, 19, 0, tzinfo=UTC)
    r = run('history_forecast', t0, 360, 720, [0, 240], fetched=_fetched(), now=t0)
    st = r.S['sources']['gfz_kp']['status']
    assert st.startswith('строгий режим'), st
    assert 'разбор:' not in st
    assert 'из уведомления DONKI о буре' in st and 'из уведомление' not in st


def test_review_mode_kp_status_still_says_review():
    t0 = datetime(2024, 6, 25, 12, 0, tzinfo=UTC)
    r = run('history_review', t0, 360, 720, [120, 360], fetched=_fetched(), now=t0)
    st = r.S['sources']['gfz_kp']['status']
    assert st.startswith('разбор:') and 'из архива GFZ' in st


# --------------------------------------------------- давность не бывает отрицательной
def test_source_age_is_never_negative_for_a_running_interval():
    """У живого Kp GFZ текущий 3-часовой интервал ещё не закончился: compare.py клампит
    давность через max(0, …), а таблица источников печатала отрицательное число (R4-3)."""
    from vkd.sources import Fetch
    from vkd.orbit.trajectory import satellite_from_tle
    epoch = satellite_from_tle(open(TLE, 'rb').read()).epoch.utc_datetime()
    t0 = (epoch + timedelta(days=1)).replace(second=0, microsecond=0)
    tle_text = open(TLE, encoding='utf-8').read()
    # интервал Kp заканчивается через 2 ч после момента расчёта — давность отрицательной быть не может
    kp = EnvironmentSample(t_utc=t0 - timedelta(hours=1), channel_id='kp', value=3.0, unit='', source_id='gfz_kp',
                           kind=Kind.OBSERVATION, published_utc=t0 - timedelta(hours=1),
                           valid_from_utc=t0 - timedelta(hours=1), valid_to_utc=t0 + timedelta(hours=2),
                           fetched_utc=t0, quality='preliminary', raw_record_id='kp_test')
    base = _fetched(tle_text, goes_at=t0)
    fetched = (base[0], (kp, {'kp_test': {'kp': 3.0}}, Fetch('gfz_kp', True, False, t0, 0.0, 'тестовое наблюдение', None, None)),
               base[2], base[3])
    r = run('live', t0, 360, 1440, [0, 480], fetched=fetched, now=t0)
    ages = [v.get('age_min') for v in r.S['sources'].values() if v.get('age_min') is not None]
    assert ages and all(a >= 0 for a in ages), r.S['sources']['gfz_kp']
    assert r.kp is None and r.S['sources']['gfz_kp']['age_min'] is None
    assert 'незавершённый' in r.S['sources']['gfz_kp']['status']


# --------------------------------------------------- ограничения карточки условия «буря»
def _storm_condition(sources_ru):
    return Condition('GST', 'limiting', 'геомагнитная буря Kp ≥ 7 в окне: проверка', ('e1',),
                     (T_GANNON, T_GANNON + timedelta(hours=6)), 'Kp ≥ 7', tuple(sources_ru))


def test_storm_card_limits_follow_the_signals_of_the_condition():
    """Один блок ограничений обслуживал и наблюдённую бурю, и прогноз прихода выброса:
    в карточке внешнего прогноза стояла фраза про наблюдение (и опечатка «объявленно»)."""
    from vkd.explain.cards import _gst_limits
    only_forecast = _gst_limits(_storm_condition([
        '%s 05-10 12:14Z (уведомление NASA DONKI), ожидаемый Kp до 9' % STORM_SIGNAL_RU['cme_arrival']]))
    only_observation = _gst_limits(_storm_condition([
        '%s 8 (интервал до 10.05 12:00Z, давность 30 мин)' % STORM_SIGNAL_RU['kp_obs']]))
    assert 'внешний прогноз, не наблюдение' in only_forecast
    assert 'Наблюдение сейчас распространено' not in only_forecast
    assert 'распространение последнего наблюдения' not in only_forecast
    for txt in (only_forecast, only_observation):
        assert not re.search(r'объявленно', txt), txt        # опечатка «объявленно» вместо «объявлено»
    assert 'распространение последнего наблюдения на окно объявлено' in only_observation
    assert 'Kp до N' not in only_observation          # оговорки прогноза нет там, где прогноза нет
    # обе оговорки вместе — только когда в условии оба сигнала
    both = _gst_limits(_storm_condition([
        '%s 8 (интервал до 10.05 12:00Z)' % STORM_SIGNAL_RU['kp_obs'],
        '%s 05-10 12:14Z' % STORM_SIGNAL_RU['cme_arrival']]))
    assert 'Kp до N' in both and 'распространение последнего наблюдения' in both


def test_gannon_cme_arrival_card_has_no_observation_clause():
    r = run('history_forecast', T_GANNON, 360, 720, [0, 240], fetched=_fetched(), now=T_GANNON)
    storm = [c for c in r.cards if 'Условие' in c.title and 'буря' in c.title]
    assert storm, [c.title for c in r.cards]
    for c in storm:
        assert not re.search(r'объявленно', c.limits_ru)
        assert 'Наблюдение сейчас распространено' not in c.limits_ru
        if c.kind.value == 'external_forecast':
            assert 'внешний прогноз, не наблюдение' in c.limits_ru


def test_every_storm_signal_is_classified():
    """Метки сигналов и строки, которые их собирают, живут в одном месте: нераспознанный
    сигнал не должен молча получать чужую оговорку."""
    r = run('history_forecast', T_GANNON, 360, 720, [0, 240], fetched=_fetched(), now=T_GANNON)
    seen = 0
    for w in r.S['windows']:
        for m in w['mechanisms']:
            for c in m['conditions']:
                if c['kind'] != 'GST':
                    continue
                seen += 1
                assert 'unknown' not in storm_signal_kinds(c['sources']), c['sources']
    assert seen


# --------------------------------------------------- порог различимости метеороидов
def test_meteoroid_threshold_comes_from_settings_and_prints_its_origin():
    """Порог 5 % был литералом в коде и появлялся на экране без происхождения."""
    th_file = Thresholds.from_settings()
    assert th_file.meteoroid_equal_pct == float(cfg_section('thresholds')['meteoroid_equal_pct'])
    t0 = datetime(2024, 6, 25, 12, 0, tzinfo=UTC)
    r = run('history_review', t0, 360, 720, [120, 360], fetched=_fetched(), now=t0)
    mm = r.S['recommendation']['per_mechanism'].get('mmod_stat', '')
    assert 'порога различимости' in mm and 'правило команды' in mm and 'config/settings.toml' in mm
    assert 'меньше 5 %)' not in mm                 # прежняя формулировка без происхождения
    # Seasonal N now participates in comparison; the former mean-only role is obsolete.
    assert 'сезонная оценка' in mm and 'гипотез' in mm


def test_meteoroid_threshold_is_actually_applied_from_thresholds():
    """Настройка не декоративная: с нулевым порогом окна перестают быть «неразличимыми»."""
    from tests.test_compare import traj
    from vkd.assess.trapped import BeltTable
    from vkd.types import Window
    from vkd.windows.compare import assess_window, recommend
    tr = traj(72 * 60, lambda i: i < 60)
    bt = BeltTable('min')
    wins = [Window(datetime(2024, 5, 3, 12, 0, tzinfo=UTC), 360), Window(datetime(2024, 5, 3, 18, 0, tzinfo=UTC), 360)]
    hits = (1.0e-6, 1.02e-6)          # различие 2 % — ниже 5 % и выше 0 %
    for pct, expect_equal in ((5.0, True), (0.0, False)):
        th = Thresholds(meteoroid_equal_pct=pct)
        A = [assess_window(w, tr, bt, None, None, [], th, wins[0].start_utc, mmod_hits=h)
             for w, h in zip(wins, hits)]
        txt = recommend(A, th).per_mechanism_comparison.get('mmod_stat', '')
        assert ('окна не различаются' in txt) is expect_equal, (pct, txt)


# --------------------------------------------------- прослеживаемость ряда наблюдений
def test_observation_series_has_publication_time_or_a_named_reason():
    """Ряд GOES уходил в снимок и в observations.json с published_utc = None и без причины;
    по стандарту у каждой величины должно быть либо время публикации, либо названная причина."""
    t0 = datetime(2024, 6, 25, 12, 0, tzinfo=UTC)
    r = run('history_review', t0, 360, 720, [120, 360], fetched=_fetched(), now=t0)
    assert r.S['observations'], 'ряд наблюдений в разборе пуст — проверять нечего'
    for o in r.S['observations']:
        reason = (o.get('publication_absence_reason') or '').strip()
        assert o.get('published_utc') or reason, o
        if not o.get('published_utc'):
            assert len(reason) > 20 and 'не доказаны' in reason, o


def test_observation_series_reason_reaches_the_export():
    import json
    import zipfile
    from app.export import build_zip
    t0 = datetime(2024, 6, 25, 12, 0, tzinfo=UTC)
    r = run('history_review', t0, 360, 720, [120, 360], fetched=_fetched(), now=t0)
    with zipfile.ZipFile(__import__('io').BytesIO(build_zip(r.S, r.raw_records))) as z:
        rows = json.loads(z.read('observations.json').decode('utf-8'))
    assert rows and all(o.get('published_utc') or o.get('publication_absence_reason') for o in rows)


# --------------------------------------------------- «до отсечки» в постоянных строках
def test_forecast_rule_text_does_not_hardcode_a_cutoff_that_live_mode_has_no():
    """Правило фактора-прогноза печаталось как «выпуск до отсечки» во всех режимах,
    в том числе в текущем, где отсечки нет вовсе."""
    import io as _io
    src = _io.open(os.path.join(ROOT, 'vkd', 'windows', 'compare.py'), encoding='utf-8').read()
    assert 'выпуск до отсечки' not in src
    t0 = datetime(2024, 5, 10, 12, 0, tzinfo=UTC)
    r = run('history_forecast', t0, 360, 720, [0, 240], fetched=_fetched(), now=t0)
    rules = [f['rule'] for w in r.S['windows'] for m in w['mechanisms'] for f in m['factors']]
    assert any('выпуск с указанием времени публикации' in x for x in rules)
