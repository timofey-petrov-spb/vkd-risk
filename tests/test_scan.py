# -*- coding: utf-8 -*-
"""Перебор начал выхода (ТЗ круга 11, разделы 1 и 1a).

Главная проверка здесь одна и она обязательная: числа перебора совпадают с поокновым расчётом.
Перебор не имеет права быть «быстрым приближением» — он обязан быть тем же расчётом, просто
организованным дешевле. Если совпадения нет, значит перебор считает не то, и показывать его
рядом с карточками окон нельзя.

Совпадение проверяется СТРОГО, без эпсилона: обе стороны зовут одну и ту же `integrate_time`
на одном и том же срезе одних и тех же значений, поэтому результат не «близок», а тот же самый.
Объявленный эпсилон здесь был бы признанием, что где-то есть второй способ счёта.
"""
from datetime import timedelta

import pytest

from tests.test_compare import T0, belts, goes, kp_sample, traj  # noqa: F401
from vkd.types import EnvironmentSample, Kind, Window
from vkd.windows.compare import Thresholds, _sw_vals, assess_window
from vkd.windows.scan import Candidate, ScanResult, scan_windows, starts

DUR = 360           # длительность выхода, мин (постановка: 60…480)
SEARCH = 720        # период поиска начала, мин
STEP = 10           # шаг перебора, мин — умолчание config/settings.toml

# Форма ключа `scan` — договор с экраном (ТЗ, раздел 1a). Менять его нельзя ни в чём, поэтому
# состав ключей проверяется РАВЕНСТВОМ множеств, а не включением: лишний ключ — тоже нарушение.
SCAN_KEYS = {'requested_duration_min', 'search_from_utc', 'search_to_utc', 'step_min', 'n_candidates',
             'rule', 'tolerance_note', 'candidates', 'best', 'recommended_index', 'verdict', 'scope', 'why'}
CANDIDATE_KEYS = {'start_utc', 'end_utc', 'saa_min', 'fluence', 'mmod_hits', 'coverage',
                  'conditions', 'rank', 'group'}


@pytest.fixture(scope='module')
def track():
    """Трасса на сутки: аномалия проходится не периодически, иначе все начала одинаковы и
    проверять на них нечего. Окно в аномалии держится тем дольше, чем позже оно начинается."""
    return traj(24 * 60, lambda i: (i % 97) < 18 + i // 200)


def _scan(track, th=None, **kw):
    th = th or Thresholds()
    g, k = kw.pop('goes', goes(0.2)), kw.pop('kp', kp_sample(3.0, age_min=30))
    b = kw.pop('belts')
    full = lambda w: assess_window(w, track, b, g, k, [], th, T0, mmod_hits=1e-6,      # noqa: E731
                                   events=kw.get('events', ()), forecasts=kw.get('forecasts', ()))
    return scan_windows(track, b, th, search_from_utc=T0,
                        search_to_utc=T0 + timedelta(minutes=SEARCH + DUR), duration_min=DUR,
                        step_min=kw.pop('step_min', STEP), now_utc=T0, full_assess=full,
                        goes=g, kp=k, **kw)


# --------------------------------------------------------------------- тождественность
def test_perebor_sovpadaet_s_pooknovym_raschetom(belts, track):
    """ОБЯЗАТЕЛЬНАЯ проверка ТЗ: на сетке не менее двадцати начал числа совпадают строго.

    Сверяются обе величины, по которым идёт ранжирование, и метеороиды сверх того: расхождение
    хотя бы в одной означало бы, что перебор и карточка окна считают разное.
    """
    th = Thresholds()
    sc = _scan(track, belts=belts)
    assert sc.n_candidates >= 20, sc.n_candidates
    checked = 0
    for c in sc.candidates:
        A = assess_window(Window(c.start_utc, DUR), track, belts, goes(0.2), kp_sample(3.0, age_min=30),
                          [], th, T0, mmod_hits=1e-6)
        saa, fluence = _sw_vals(A)
        assert c.saa_min == saa, (c.start_utc, c.saa_min, saa)
        assert c.fluence == fluence, (c.start_utc, c.fluence, fluence)
        checked += 1
    assert checked == sc.n_candidates


def test_usloviya_kandidata_te_zhe_chto_u_okna(belts, track):
    """Условия проверки — тот же расчёт, что у карточки окна: их считает одна функция модуля."""
    from vkd.windows.compare import _short, window_conditions
    th = Thresholds()
    sc = _scan(track, belts=belts, goes=goes(20.0))          # уровень выше фона: условие есть
    for c in sc.candidates[:25]:
        conds = window_conditions(Window(c.start_utc, DUR), th, T0, goes=goes(20.0),
                                  kp=kp_sample(3.0, age_min=30))
        assert list(c.conditions) == [_short(x.text) for x in conds], c.start_utc
    assert all(c.conditions for c in sc.candidates), 'условие общего канала обязано стоять у всех начал'


# --------------------------------------------------------------------- договор с экраном
def test_forma_klyucha_scan_v_tochnosti_ta_chto_v_tz(belts, track):
    d = _scan(track, belts=belts).to_snapshot()
    assert set(d) == SCAN_KEYS, set(d) ^ SCAN_KEYS
    assert set(d['candidates'][0]) == CANDIDATE_KEYS, set(d['candidates'][0]) ^ CANDIDATE_KEYS
    assert isinstance(d['requested_duration_min'], int) and d['requested_duration_min'] == DUR
    assert isinstance(d['step_min'], int) and isinstance(d['n_candidates'], int)
    assert d['n_candidates'] == len(d['candidates'])
    assert d['search_from_utc'].endswith('+00:00') and d['search_to_utc'].endswith('+00:00')
    assert d['verdict'] in ('recommended', 'equivalent', 'all_need_check', 'insufficient')
    assert isinstance(d['best'], list) and all(isinstance(i, int) for i in d['best'])
    assert d['rule'] and d['tolerance_note'] and d['scope'] and d['why']
    for c in d['candidates']:
        assert c['coverage'] in ('full', 'partial', 'none')
        assert isinstance(c['conditions'], list)
        assert (c['rank'] is None) == (c['group'] is None)


def test_kandidaty_po_vozrastaniyu_vremeni_i_bez_propuskov(belts, track):
    """Кандидаты идут по возрастанию времени начала, шаг выдержан, последнее окно помещается
    в горизонт целиком — «перебрано N начал» должно значить ровно это."""
    sc = _scan(track, belts=belts)
    ts = [c.start_utc for c in sc.candidates]
    assert ts == sorted(ts)
    assert all((b - a) == timedelta(minutes=STEP) for a, b in zip(ts, ts[1:]))
    assert ts[0] == T0 and ts[-1] == T0 + timedelta(minutes=SEARCH)
    assert all(c.end_utc <= sc.search_to_utc for c in sc.candidates)
    assert sc.n_candidates == SEARCH // STEP + 1 == 73


def test_shag_perebora_proveryaetsya_a_ne_popravlyaetsya_molcha():
    with pytest.raises(ValueError, match='шаг перебора'):
        starts(T0, T0 + timedelta(minutes=1080), 360, 0)
    with pytest.raises(ValueError, match='шаг перебора'):
        starts(T0, T0 + timedelta(minutes=1080), 360, 10.0)
    assert starts(T0, T0 + timedelta(minutes=300), 360, 10) == []      # окно не помещается


# --------------------------------------------------------------------- правило ранжирования
def test_rang_i_gruppa_tolko_u_poschitannyh(belts, track):
    """Кандидат без посчитанного флюенса не ранжируется: отсутствие величины не заменяется нулём
    и не превращается ни в лучший, ни в худший исход."""
    sc = _scan(track, belts=belts)
    for c in sc.candidates:
        assert (c.rank is not None) == (c.fluence is not None)
    ranks = sorted(c.rank for c in sc.candidates if c.rank is not None)
    assert ranks == list(range(1, len(ranks) + 1))


def test_pravilo_to_zhe_chto_u_sravneniya_okon(belts, track):
    """Перебор применяет правило (8) методики — то же самое, которым сравниваются два-три окна.

    Проверяется не словами, а применением: лучшая группа — ровно недоминируемое множество,
    посчитанное теми же `pair_not_worse`/`pair_better`, которые зовёт `recommend`.
    """
    from vkd.windows.compare import pair_better
    th = Thresholds()
    sc = _scan(track, belts=belts, th=th)
    free = [c for c in sc.candidates if c.rank is not None and not c.conditions]
    tol_m, tol_r = th.equiv_tol_min, max(1.0, th.fluence_equiv_ratio)
    nd = [c for c in free
          if not any(pair_better(b.saa_min, b.fluence, c.saa_min, c.fluence, tol_m, tol_r)
                     for b in free if b is not c)]
    assert {c.start_utc for c in nd} == {sc.candidates[i].start_utc for i in sc.best}
    # и наоборот: никого из лучшей группы никто не превосходит
    for i in sc.best:
        a = sc.candidates[i]
        assert not any(pair_better(b.saa_min, b.fluence, a.saa_min, a.fluence, tol_m, tol_r) for b in free)


def test_gruppy_eto_sloi_nedominiruemosti(belts, track):
    """Номер группы — номер слоя: каждого из второго слоя превосходит кто-то из первого."""
    from vkd.windows.compare import pair_better
    th = Thresholds()
    sc = _scan(track, belts=belts, th=th)
    tol_m, tol_r = th.equiv_tol_min, max(1.0, th.fluence_equiv_ratio)
    by_group = {}
    for c in sc.candidates:
        if c.rank is not None and not c.conditions:
            by_group.setdefault(c.group, []).append(c)
    if len(by_group) >= 2:
        first, second = by_group[min(by_group)], by_group[min(by_group) + 1]
        for a in second:
            assert any(pair_better(b.saa_min, b.fluence, a.saa_min, a.fluence, tol_m, tol_r) for b in first), \
                'кандидат второго слоя не превзойдён ни одним кандидатом первого'
    # ранги идут по группам: сначала вся первая, потом вся вторая
    ranked = sorted((c for c in sc.candidates if c.rank), key=lambda c: c.rank)
    assert [c.group for c in ranked] == sorted(c.group for c in ranked)


def test_dopuski_te_zhe_chto_u_sravneniya_okon(belts, track):
    """Допуск в тексте выдачи — те же δ и ρ, что применены. Два числа для одного слова
    «равнозначно» на одном экране недопустимы."""
    th = Thresholds(equiv_tol_min=7.0, fluence_equiv_ratio=1.5)
    sc = _scan(track, belts=belts, th=th)
    assert '7 мин' in sc.tolerance_note and '×1,50' in sc.tolerance_note, sc.tolerance_note


def test_kandidat_s_usloviem_ne_stoit_vyshe_kandidata_bez_usloviy(belts, track):
    """Правило команды: условие проверки опускает кандидата ниже всех свободных, каким бы
    хорошим ни был его флюенс."""
    from vkd.types import EventInterval
    # Протонное событие на первой половине горизонта: условие у ранних начал, у поздних нет.
    a0, a1 = T0 - timedelta(hours=1), T0 + timedelta(hours=5)
    ev = EventInterval('sep#1', 'SEP', Kind.OBSERVATION, a0, a1, True, True, a0, a1,
                       'test', a0, 'rec#sep', note='поток > 10 pfu')
    sc = _scan(track, belts=belts, events=(ev,))
    flagged = [c for c in sc.candidates if c.conditions and c.rank]
    free = [c for c in sc.candidates if not c.conditions and c.rank]
    assert flagged and free, 'в наборе нет обоих видов кандидатов — проверять нечего'
    assert max(c.rank for c in free) < min(c.rank for c in flagged)
    assert max(c.group for c in free) < min(c.group for c in flagged)
    # и правило названо словами, а не только применено
    assert 'выше кандидата без условий не ставится' in sc.rule


def test_ni_odnogo_summarnogo_balla(belts, track):
    """Складывать флюенс с минутами в аномалии нельзя: третьей величины в выдаче нет, и правило
    прямо это отрицает."""
    sc = _scan(track, belts=belts)
    d = sc.to_snapshot()
    assert not any(k for k in CANDIDATE_KEYS if 'score' in k or 'балл' in k)
    assert 'суммарного балла воздействия у сервиса нет' in sc.rule
    assert all(set(c) == CANDIDATE_KEYS for c in d['candidates'])


def test_verdikt_perebora_nazyvaet_svoy_ishod(belts, track):
    """Исходы перебора: рекомендация — только когда в лучшей группе ОДИН кандидат."""
    sc = _scan(track, belts=belts)
    assert sc.verdict in ('recommended', 'equivalent')
    assert sc.best and sc.candidates[sc.best[0]].rank == 1
    if sc.verdict == 'recommended':
        assert len(sc.best) == 1 and sc.recommended_index == sc.best[0]
    else:
        # несколько в лучшей группе — рекомендации одного нет, как и при сравнении окон
        assert len(sc.best) > 1 and sc.recommended_index is None
    # все окна под условием: рекомендации нет, но перечень есть
    sc2 = _scan(track, belts=belts, goes=goes(20.0))
    assert sc2.verdict == 'all_need_check' and sc2.recommended_index is None and sc2.best


def test_kompromiss_bez_pobeditelya_nazyvaetsya_chislami(belts, track):
    """Правило (8): минуты и флюенс указывают на разные начала — компромисс без победителя.

    Собирается искусственно из двух кандидатов, которые СПОРЯТ: у одного заметно меньше минут,
    у другого заметно ниже флюенс. Подгонки данных здесь нет — проверяется сам текст правила,
    а не то, что такой случай бывает на архиве (на архиве он измерен отдельно и в отчёте назван).
    """
    from vkd.windows.scan import Candidate, conflict_ru, why_ru
    a = Candidate(T0, T0 + timedelta(minutes=DUR), 10.0, 9.0e5, 1e-6, 'full', ())
    b = Candidate(T0 + timedelta(minutes=60), T0 + timedelta(minutes=60 + DUR), 90.0, 1.0e5, 1e-6, 'full', ())
    txt = conflict_ru([a, b], tol_m=5.0, tol_r=1.5)
    assert 'компромисс без победителя' in txt
    assert 'по флюенсу лучше начало' in txt and 'по времени в аномалии — начало' in txt
    assert 'выбор за аналитиком' in txt
    why = why_ru([a, b], (0, 1), None, 'equivalent', STEP, 5.0, 1.5)
    assert 'спорят между собой' in why and 'компромисс без победителя' in why
    # а равнозначные (различие внутри допуска) компромиссом не называются
    c = Candidate(T0 + timedelta(minutes=60), T0 + timedelta(minutes=60 + DUR), 12.0, 1.1e6, 1e-6, 'full', ())
    assert conflict_ru([a, c], tol_m=5.0, tol_r=1.5) == ''


def test_bez_trassy_perebor_nichego_ne_ranzhiruet(belts):
    """Пустая трасса — это не «нулевое воздействие»: ранга нет ни у кого, исход insufficient."""
    sc = _scan([], belts=belts)                         # трассы нет вовсе
    assert sc.verdict == 'insufficient' and sc.recommended_index is None and sc.best == ()
    assert all(c.rank is None and c.fluence is None for c in sc.candidates)
    assert 'не по чему' in sc.why


def test_pochemu_soderzhit_chisla_i_chislo_perebrannyh_nachal(belts, track):
    """«Почему» обязано быть с числами: сколько начал перебрано, с каким шагом и чем лучшее лучше."""
    sc = _scan(track, belts=belts)
    assert 'перебрано %d' % sc.n_candidates in sc.why
    assert 'шагом %d мин' % STEP in sc.why
    assert 'мин в аномалии' in sc.why and 'флюенс' in sc.why


def test_slishkom_melkiy_shag_otkaz_s_nazvannoy_prichinoy(belts, track):
    """Никаких молчаливых огрублений: слишком мелкий шаг даёт отказ с числом, а не тихую замену
    шага на удобный сервису."""
    with pytest.raises(ValueError, match='при пределе'):
        _scan(track, belts=belts, step_min=1)


def test_polnaya_ocenka_tolko_dlya_pokazyvaemyh(belts, track):
    """Полная оценка окна считается для рекомендованного и строк таблицы лучших, а не для всех
    семидесяти трёх: в этом и есть разница между 0,1 с и 5 с."""
    calls = []
    th = Thresholds()
    g, k = goes(0.2), kp_sample(3.0, age_min=30)

    def full(w):
        calls.append(w.start_utc)
        return assess_window(w, track, belts, g, k, [], th, T0, mmod_hits=1e-6)
    sc = scan_windows(track, belts, th, search_from_utc=T0,
                      search_to_utc=T0 + timedelta(minutes=SEARCH + DUR), duration_min=DUR,
                      step_min=STEP, now_utc=T0, full_assess=full, goes=g, kp=k)
    assert 1 <= len(calls) <= 5 < sc.n_candidates
    assert sc.candidates[sc.best[0]].start_utc in calls


def test_oblast_vyvoda_perebora_ne_obeshchaet_bezopasnosti(belts, track):
    sc = _scan(track, belts=belts)
    assert 'безопас' not in sc.scope.lower()
    assert 'не заключение о полном риске ВКД' in sc.scope


# --------------------------------------------------------------------- на настоящем конвейере
@pytest.fixture(scope='module')
def real():
    """Один настоящий расчёт: те же входы, что у сохранённого примера разбора 03.05.2024."""
    from datetime import datetime, timezone
    from app.compute import run
    from tests.test_integration import _fetched
    t0 = datetime(2024, 5, 3, 12, 0, tzinfo=timezone.utc)
    return run('history_review', t0, DUR, SEARCH, [0, 240], fetched=_fetched(), now=t0)


def test_chisla_perebora_sovpadayut_s_pooknovym_raschetom_v_snimke(real):
    """Тождественность НА НАСТОЯЩИХ ДАННЫХ и в том самом снимке, который уходит на экран:
    окна сравнения стоят на сетке перебора, и их числа обязаны совпасть с числами кандидатов."""
    S = real.S
    by_start = {c['start_utc']: c for c in S['scan']['candidates']}
    checked = 0
    for w in S['windows']:
        c = by_start.get(w['start_utc'])
        if c is None or w['duration_min'] != S['scan']['requested_duration_min']:
            continue
        f = {x['name']: x['value'] for m in w['mechanisms'] for x in m['factors']}
        assert c['saa_min'] == f['минут в аномалии'], w['start_utc']
        assert c['fluence'] == next(v for n, v in f.items() if n.startswith('флюенс')), w['start_utc']
        assert c['mmod_hits'] == next(v for n, v in f.items() if n.startswith('ожидаемое число')), w['start_utc']
        checked += 1
    assert checked >= 1, 'ни одно сравниваемое окно не попало на сетку перебора — проверять нечего'


def test_klyuch_scan_v_snimke_i_v_vygruzke(real):
    """Ключ `scan` попадает и в снимок, и в выгрузку — наравне с остальным снимком."""
    import zipfile
    from io import BytesIO
    from app.export import build_zip, report_md
    S = real.S
    assert set(S['scan']) == SCAN_KEYS
    z = zipfile.ZipFile(BytesIO(build_zip(S, real.raw_records)))
    assert 'scan.json' in z.namelist()
    import json
    assert json.loads(z.read('scan.json').decode('utf-8'))['n_candidates'] == S['scan']['n_candidates']
    md = report_md(S, real.raw_records)
    assert '## Перебор начал выхода' in md
    assert 'Перебрано начал: %d' % S['scan']['n_candidates'] in md


def test_perebor_ne_rekomenduet_tam_gde_vydikt_otkazyvaet():
    """На одном экране не может стоять отказ сверху и рекомендация перебора под ним.

    Источник исключён пользователем — обязательная линия не покрыта совсем. Правило вердикта
    отказывает; перебор обязан сказать то же самое, оставив числа кандидатов на месте и прямо
    пометив, что это сравнение факторов, а не рекомендация.
    """
    from datetime import datetime, timedelta, timezone
    from app.compute import run
    from tests.test_integration import _fetched
    from vkd.orbit.trajectory import satellite_from_tle
    tle = open('data/orbit/iss.tle', encoding='utf-8').read()
    t0 = (satellite_from_tle(tle.encode()).epoch.utc_datetime() + timedelta(hours=6)).replace(second=0, microsecond=0)
    R = run('live', t0, DUR, SEARCH, [0, 240], disabled={'goes': 'off'},
            fetched=_fetched(tle, goes_at=t0), now=t0)
    assert R.S['recommendation']['verdict'] == 'insufficient'
    sc = R.S['scan']
    assert sc['verdict'] == 'insufficient' and sc['recommended_index'] is None
    assert 'не покрыта совсем' in sc['why'] and 'а не рекомендация' in sc['why']
    assert 'отказ от вывода, а не оценка риска' in sc['scope']
    # числа при этом посчитаны и не спрятаны
    assert any(c['fluence'] is not None for c in sc['candidates'])


def test_bez_perebora_klyucha_net_vovse():
    """Пустой перебор и НЕСДЕЛАННЫЙ перебор — разные вещи: во втором случае ключа нет вовсе,
    и экран честно говорит, что перебор не выполнялся."""
    from datetime import datetime, timezone
    from app.compute import run
    from tests.test_integration import _fetched
    t0 = datetime(2024, 5, 3, 12, 0, tzinfo=timezone.utc)
    S = run('history_review', t0, DUR, SEARCH, [0, 240], fetched=_fetched(), now=t0, scan=False).S
    assert 'scan' not in S
    from app.export import report_md
    assert 'Перебор начал выхода' not in report_md(S, {})


def test_shag_perebora_beryotsya_iz_nastroek():
    """Шаг — настройка вне кода (Т7), и мёртвым ключом она не является."""
    from vkd.config import section
    from vkd.windows.scan import STEP_MIN_DEFAULT
    assert int(section('ui')['scan_step_min']) == STEP_MIN_DEFAULT == 10


def test_vse_sohranyonnye_primery_nesut_perebor():
    """ТЗ раздела 1a: ключ попадает в сохранённые примеры наравне с остальным снимком."""
    import glob
    import io as _io
    import json
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = sorted(glob.glob(os.path.join(root, 'examples', '*.json')))
    assert files, 'примеров нет — сначала python scripts/make_examples.py'
    for f in files:
        d = json.load(_io.open(f, encoding='utf-8'))
        sc = d.get('scan')
        assert sc, os.path.basename(f)
        assert set(sc) == SCAN_KEYS, (os.path.basename(f), set(sc) ^ SCAN_KEYS)
        assert sc['n_candidates'] == len(sc['candidates']) > 1, os.path.basename(f)
        assert sc['verdict'] in ('recommended', 'equivalent', 'all_need_check', 'insufficient')


pytest_plugins = ('tests.test_compare',)
