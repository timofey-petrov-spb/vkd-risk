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


def test_rangi_po_flyuensu_i_gruppy_ravnoznachnosti(belts, track):
    """Порядок — по флюенсу; внутри группы равнозначности — по минутам в аномалии."""
    sc = _scan(track, belts=belts)
    ranked = sorted((c for c in sc.candidates if c.rank), key=lambda c: c.rank)
    assert [c.fluence for c in ranked] == sorted(c.fluence for c in ranked)
    for a, b in zip(ranked, ranked[1:]):
        if a.group == b.group:
            assert a.saa_min <= b.saa_min, (a.start_utc, b.start_utc)
            assert b.fluence <= a.fluence * max(1.0, sc_tol(sc)), 'группа шире объявленного допуска'
        else:
            assert b.group == a.group + 1


def sc_tol(sc: ScanResult) -> float:
    """Допуск, объявленный в самой выдаче: правило и число на экране должны быть одним числом."""
    import re
    m = re.search(r'×(\d+),(\d+)', sc.tolerance_note)
    return float('%s.%s' % m.groups())


def test_kandidat_s_usloviem_ne_stoit_vyshe_kandidata_bez_usloviy(belts, track):
    """Правило команды: условие проверки опускает кандидата ниже всех свободных, каким бы
    хорошим ни был его флюенс."""
    from vkd.types import EventInterval
    # Протонное событие на первой половине горизонта: условие у ранних начал, у поздних нет.
    ev = EventInterval('sep#1', 'SEP', T0 - timedelta(hours=1), T0 + timedelta(hours=5),
                       T0 - timedelta(hours=2), 'rec#sep', valid_from_utc=T0 - timedelta(hours=1),
                       valid_to_utc=T0 + timedelta(hours=5), note='poток > 10 pfu')
    sc = _scan(track, belts=belts, events=(ev,))
    flagged = [c for c in sc.candidates if c.conditions and c.rank]
    free = [c for c in sc.candidates if not c.conditions and c.rank]
    assert flagged and free, 'в наборе нет обоих видов кандидатов — проверять нечего'
    assert max(c.rank for c in free) < min(c.rank for c in flagged)
    assert max(c.group for c in free) < min(c.group for c in flagged)
    # и правило названо словами, а не только применено
    assert 'не может стоять выше кандидата без условий' in sc.rule


def test_ni_odnogo_summarnogo_balla(belts, track):
    """Складывать флюенс с минутами в аномалии нельзя. Проверяется тем, что ранг определяется
    флюенсом, и никакой третьей величины в выдаче нет."""
    sc = _scan(track, belts=belts)
    d = sc.to_snapshot()
    assert not any(k for k in CANDIDATE_KEYS if 'score' in k or 'балл' in k)
    assert 'балл' in sc.rule and 'нет' in sc.rule            # правило прямо это отрицает
    assert all(set(c) == CANDIDATE_KEYS for c in d['candidates'])


def test_verdikt_perebora_nazyvaet_svoy_ishod(belts, track):
    """Четыре исхода перебора, и каждый означает своё."""
    sc = _scan(track, belts=belts)
    assert sc.verdict in ('recommended', 'equivalent')
    assert sc.recommended_index is not None and sc.candidates[sc.recommended_index].rank == 1
    assert sc.best and sc.candidates[sc.best[0]].rank == 1
    # все окна под условием: рекомендации нет, но перечень есть
    sc2 = _scan(track, belts=belts, goes=goes(20.0))
    assert sc2.verdict == 'all_need_check' and sc2.recommended_index is None and sc2.best


def test_bez_trassy_perebor_nichego_ne_ranzhiruet(belts):
    """Пустая трасса — это не «нулевое воздействие»: ранга нет ни у кого, исход insufficient."""
    empty = traj(24 * 60, lambda i: False)[:5]          # пять точек в самом начале горизонта
    sc = _scan(empty, belts=belts)
    assert sc.verdict == 'insufficient' and sc.recommended_index is None and sc.best == ()
    assert all(c.rank is None and c.fluence is None for c in sc.candidates)
    assert 'не по чему' in sc.why


def test_pochemu_soderzhit_chisla_i_chislo_perebrannyh_nachal(belts, track):
    """«Почему» обязано быть с числами: сколько начал перебрано, с каким шагом и чем лучшее лучше."""
    sc = _scan(track, belts=belts)
    assert 'перебрано %d' % sc.n_candidates in sc.why
    assert 'шагом %d мин' % STEP in sc.why
    assert 'мин в аномалии' in sc.why and 'флюенс' in sc.why


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
    assert sc.candidates[sc.recommended_index].start_utc in calls


def test_oblast_vyvoda_perebora_ne_obeshchaet_bezopasnosti(belts, track):
    sc = _scan(track, belts=belts)
    assert 'безопас' not in sc.scope.lower()
    assert 'не заключение о полном риске ВКД' in sc.scope


pytest_plugins = ('tests.test_compare',)
