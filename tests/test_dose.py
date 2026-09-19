# -*- coding: utf-8 -*-
"""Поглощённая доза за защитой скафандра: метод, нормировка, границы (vkd/assess/dose.py).

Проверки здесь трёх родов:
  * АРИФМЕТИКА — доза равна формуле (Д.1), посчитанной независимо от кода модуля;
  * ГРАНИЦЫ — вне узлов и вне объявленной области расчёт ОТКАЗЫВАЕТ, а не гадает;
  * СОГЛАСОВАННОСТЬ — коэффициент «флюенс → доза» внутри самого стандарта устойчив,
    а наш средний по орбите уровень сравнивается со средним уровнем стандарта.
    Последнее НЕ является независимой валидацией дозы (формула самонормирующаяся);
    это проверка того, что цепочка потока стоит на том же месте, что у стандарта,
    и она фиксирует найденное расхождение числом, а не замалчивает его.
"""
import csv
import io
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from vkd.assess import dose as D
from vkd.assess.trapped import BeltTable

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Разведка, вопрос 1: попадает ли скафандр в узлы таблицы
# ---------------------------------------------------------------------------

def test_tolshchina_skafandra_eto_uzly_tablicy_a_ne_interpolyaciya():
    """0,5 · 1,0 · 1,5 г/см² — НАСТОЯЩИЕ узлы дозовых таблиц обоих наклонений.

    Это ответ на первый вопрос разведки: собственный перенос частиц не нужен,
    интерполяция по толщине не нужна, доза берётся из таблицы стандарта.
    """
    for fn in ('K_2_5_hemisphere.csv', 'K_2_6_hemisphere.csv',
               'K_2_5_plane.csv', 'K_2_6_plane.csv'):
        rows = list(csv.reader(io.open(os.path.join(ROOT, 'data', 'ost1044', fn), encoding='utf-8')))
        thick = [float(r[0]) for r in rows[1:]]
        assert thick == [0.01, 0.1, 0.3, 0.5, 1.0, 1.5, 2.0, 3.0, 10.0], (fn, thick)
        for d in D.SUIT_THICKNESS_NODES:
            assert d in thick, (fn, d)


def test_tolshchina_vne_uzlov_otvergaetsya():
    """По толщине не интерполируется НИЧЕГО: не узел — ошибка, а не «ближайший»."""
    with pytest.raises(ValueError, match='не является узлом'):
        D.dose_10y_rad_Si(0.8, 421.0, 51.6)
    with pytest.raises(ValueError, match='не является узлом'):
        D.dose_10y_rad_Si(1.2, 421.0, 51.6)


# ---------------------------------------------------------------------------
# Границы области применимости
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('alt', [399.0, 601.0, 0.0, 20000.0])
def test_vysota_vne_obyavlennoy_oblasti_daet_otkaz(alt):
    """Вне 400…600 км значения нет. Ограничение НАМЕРЕННОЕ: у таблиц наклонения 30
    третий столбец подписан «700 км» и им не является."""
    value, prov = D.dose_10y_rad_Si(1.0, alt, 51.6)
    assert value is None and prov['status'] == 'outside_domain'
    assert '400' in prov['status_ru'] and '600' in prov['status_ru']


@pytest.mark.parametrize('inc', [29.0, 61.0, 98.0])
def test_naklonenie_vne_uzlov_daet_otkaz(inc):
    """51,6° — интерполяция ВНУТРИ узлов; экстраполяции по наклонению нет."""
    value, prov = D.dose_10y_rad_Si(1.0, 421.0, inc)
    assert value is None and prov['status'] == 'outside_domain'


def test_otsutstvie_flyuensa_ne_stanovitsya_nulem():
    """Нет флюенса — нет дозы, и это НЕ ноль. Отдельный статус со словами."""
    r = D.window_dose(None)
    assert r.value_mGy is None and r.status == 'no_fluence'
    assert 'не означает нулевую дозу' in r.status_ru


def test_kanal_bez_normirovki_daet_otkaz():
    """Для канала, которому не посчитан средний по орбите уровень, дозы нет."""
    r = D.window_dose(1e6, e_min_MeV=7.0)
    assert r.value_mGy is None and r.status == 'no_normalisation'
    assert '7' in r.status_ru


# ---------------------------------------------------------------------------
# Арифметика формулы (Д.1)
# ---------------------------------------------------------------------------

def test_doza_ravna_formule_posschitannoy_nezavisimo():
    """D = 10 · D_табл · Φ / (⟨φ⟩ · T₁₀), где каждый сомножитель взят из файлов напрямую."""
    norm = json.load(io.open(os.path.join(ROOT, 'data', 'ost1044_dose', 'orbit_mean_flux.json'),
                             encoding='utf-8'))
    mean_flux = norm['channels']['min|30']['mean_flux_per_cm2_s']
    h0 = norm['orbit']['mean_alt_km']

    def table_at(fn, thickness, alt):
        rows = list(csv.reader(io.open(os.path.join(ROOT, 'data', 'ost1044', fn), encoding='utf-8')))
        alts = [float(x) for x in rows[0][1:]]
        row = next(r for r in rows[1:] if float(r[0]) == thickness)
        lo, hi = alts.index(400.0), alts.index(600.0)
        y0, y1 = float(row[1 + lo]), float(row[1 + hi])
        w = (alt - 400.0) / (600.0 - 400.0)
        return y0 ** (1 - w) * y1 ** w          # логарифмическая интерполяция, вручную

    d30 = table_at('K_2_5_hemisphere.csv', 1.0, h0)
    d60 = table_at('K_2_6_hemisphere.csv', 1.0, h0)
    w60 = (51.6 - 30.0) / (60.0 - 30.0)
    d_table = d30 * (1 - w60) + d60 * w60
    fluence = 2.682e6
    expected = 10.0 * d_table * fluence / (mean_flux * 10 * 365.25 * 86400.0)

    r = D.window_dose(fluence)
    assert r.status == 'ok'
    assert r.value_mGy == pytest.approx(expected, rel=1e-12)
    assert r.table_dose_rad_Si == pytest.approx(d_table, rel=1e-12)
    assert r.inclination_weight_60 == pytest.approx(0.72, rel=1e-12)


def test_rad_perevoditsya_v_milligrey_a_ne_v_millizivert():
    """1 рад = 10 мГр. Единица — мГр(Si): поглощённая доза, кремний, не зиверт."""
    assert D.RAD_TO_MGY == 10.0
    assert D.DOSE_UNIT_RU == 'мГр(Si)'
    r = D.window_dose(1e6)
    # k несёт тот же множитель 10: доза/флюенс = 10 · D_табл/(⟨φ⟩·T₁₀)
    assert r.k_mGy_per_particle == pytest.approx(
        10.0 * r.table_dose_rad_Si / (r.mean_flux_per_cm2_s * D.SECONDS_10Y), rel=1e-12)


def test_doza_strogo_lineyna_po_flyuensu():
    """Доза = k · Φ. Отсюда следует, что порядок окон по дозе совпадает с порядком по флюенсу."""
    k = D.window_dose(1.0).value_mGy
    for f in (1.0, 5e3, 1e6, 3.7e6, 9e7):
        assert D.window_dose(f).value_mGy == pytest.approx(k * f, rel=1e-12)
    assert D.window_dose(0.0).value_mGy == 0.0


def test_poryadok_okon_po_doze_sovpadaet_s_poryadkom_po_flyuensu():
    """Свойство, а не совпадение: k положителен и одинаков у всех окон одного расчёта."""
    fluences = [2.682e6, 2.452e6, 533.6, 1.359e6, 0.0, 1.752e6]
    by_f = sorted(range(len(fluences)), key=lambda i: fluences[i])
    doses = [D.window_dose(f).value_mGy for f in fluences]
    by_d = sorted(range(len(doses)), key=lambda i: doses[i])
    assert by_f == by_d
    assert D.window_dose(1.0).value_mGy > 0


# ---------------------------------------------------------------------------
# Объявленные числа: цена решений по наклонению и геометрии
# ---------------------------------------------------------------------------

def test_cena_resheniya_po_naklonenii_obyavlena_chislom():
    """Взять 60° без интерполяции — это +6,4 % при 1,0 г/см² и +12,2 % при 0,5.

    Числа стоят в docstring модуля и в LIMITS_RU; тест держит их от расхождения с кодом.
    """
    h0 = D.normalisation()['orbit']['mean_alt_km']
    for thickness, declared in ((0.5, 0.122), (1.0, 0.064), (1.5, 0.035)):
        only60 = D.dose_10y_rad_Si(thickness, h0, 60.0)[0]
        interp = D.dose_10y_rad_Si(thickness, h0, 51.6)[0]
        assert only60 / interp - 1.0 == pytest.approx(declared, abs=0.002), thickness
    assert '+6,4 %' in D.LIMITS_RU


def test_geometriya_zashchity_eto_parametr_i_ploskost_nizhe_polusfery():
    """Принята полусфера (скафандр окружает человека). Плоскость — ×0,545, число объявлено."""
    h0 = D.normalisation()['orbit']['mean_alt_km']
    plane = D.dose_10y_rad_Si(1.0, h0, 51.6, 'plane')[0]
    hemi = D.dose_10y_rad_Si(1.0, h0, 51.6, 'hemisphere')[0]
    assert plane / hemi == pytest.approx(0.545, abs=0.005)
    assert D.DEFAULT_GEOMETRY == 'hemisphere'
    with pytest.raises(ValueError):
        D.dose_10y_rad_Si(1.0, h0, 51.6, 'sphere')


def test_diapazon_po_tolshchine_pokazyvaetsya_ves_a_ne_odno_chislo():
    """Толщина — объявленный вход, поэтому расчёт отдаёт все три узла скафандра."""
    r = D.window_dose(2.682e6)
    assert [d for d, _ in r.band_mGy] == list(D.SUIT_THICKNESS_NODES)
    values = [v for _, v in r.band_mGy]
    assert values[0] > values[1] > values[2] > 0          # тоньше защита — больше доза
    assert r.value_mGy == pytest.approx(values[1], rel=1e-12)   # опорная — середина
    assert '0,5 г/см²' in D.band_ru(r).replace('0.5', '0,5')


# ---------------------------------------------------------------------------
# Нормировка: воспроизводимость и сходимость
# ---------------------------------------------------------------------------

def test_normirovka_vosproizvoditsya_tem_zhe_kodom():
    """Первые два отрезка периода усреднения пересчитываются и совпадают с сохранёнными.

    Пересчитывается ровно та же цепочка, что и в scripts/dose_normalisation.py:
    trajectory → belt_coordinates → BeltTable → integrate_time. Если любое звено
    изменится, сохранённая нормировка станет несогласованной — и это будет видно здесь,
    а не в дозе на защите.
    """
    from vkd.assess.magcoords import belt_coordinates
    from vkd.orbit.integration import integrate_time
    from vkd.orbit.trajectory import trajectory

    norm = json.load(io.open(os.path.join(ROOT, 'data', 'ost1044_dose', 'orbit_mean_flux.json'),
                             encoding='utf-8'))
    period = norm['period']
    start0 = datetime.fromisoformat(period['from_utc'])
    chunk = period['chunk_minutes']
    belts = BeltTable('min')
    coeff = os.path.join(ROOT, 'data', 'orbit', 'IGRF13.shc')
    total, duration = 0.0, 0.0
    n = 2
    for i in range(n):
        s = start0 + timedelta(minutes=chunk * i)
        e = s + timedelta(minutes=chunk)
        _meta, points = trajectory(s, chunk, period['saa_B_threshold_nT'],
                                   step_seconds=period['step_seconds'])
        track, _ = belt_coordinates(points, coeff)
        values = [belts.integral_flux(p.L, p.B_over_B0, 30.0).value_per_cm2_s for p in track]
        ti = integrate_time([p.t_utc for p in track], values, s, e, max_gap_seconds=60.0)
        total += ti.known_integral or 0.0
        duration += ti.duration_seconds
    saved = norm['convergence']['progress'][n - 1]
    assert saved['chunks'] == n
    assert total / duration == pytest.approx(saved['mean_flux_per_cm2_s'], rel=1e-9)


def test_srednee_vyshlo_na_polku_za_period_usredneniya():
    """Сходимость выборки: при удвоении периода среднее меняется на единицы процентов.

    Это показатель сходимости, а не граница физической погрешности модели.
    """
    norm = D.normalisation()
    assert norm['period']['days'] == pytest.approx(60.0, abs=0.1)
    conv = json.load(io.open(os.path.join(ROOT, 'data', 'ost1044_dose', 'orbit_mean_flux.json'),
                             encoding='utf-8'))['convergence']
    assert conv['relative_change_half_to_full'] < 0.05, conv['relative_change_half_to_full']


def test_normirovka_est_dlya_vsey_setki_ustoychivosti():
    """Перебор устойчивости в app/compute.py варьирует канал (12,5 / 30 / 50 МэВ).
    Для каждого из них средний уровень посчитан — иначе доза молча исчезала бы из свипа."""
    for e_min in (12.5, 30.0, 50.0):
        for sa in ('min', 'max'):
            n = D.normalisation(sa, e_min)
            assert n is not None and n['mean_flux_per_cm2_s'] > 0, (sa, e_min)
            assert D.window_dose(1e6, e_min_MeV=e_min, solar_activity=sa).status == 'ok'


def test_srednee_schitano_po_polnoy_dlitelnosti_kak_i_flyuens_okna():
    """Соглашение об усреднении то же, что у окна: известный вклад / ПОЛНАЯ длительность.
    Альтернатива (делить на покрытое время) сохранена числом, чтобы разница была видна."""
    n = D.normalisation()
    assert n['mean_flux_per_cm2_s'] < n['mean_flux_over_covered_time_per_cm2_s']
    assert n['mean_flux_per_cm2_s'] == pytest.approx(
        n['mean_flux_over_covered_time_per_cm2_s'] * n['coverage_fraction'], rel=1e-9)


# ---------------------------------------------------------------------------
# Согласованность с самим стандартом
# ---------------------------------------------------------------------------

def test_koefficient_flyuens_doza_ustoychiv_po_vysote():
    """k = доза за 10 лет / флюенс за 10 лет по таблицам ОДНОГО стандарта.

    Если К.2.2 и К.2.6 внутренне согласованы, k почти не зависит от высоты: это
    спектрально взвешенный пересчёт, а не свойство орбиты. На 400…1000 км доза
    меняется в 8 раз, а k — на единицы процентов. Это главное косвенное
    подтверждение того, что дозовая половина цепочки собрана правильно.
    """
    k = D.k_altitude_stability(1.0, 60.0)
    assert k['spread_relative'] < 0.12, k
    doses = list(k['k_rad_per_particle'].values())
    assert max(doses) / min(doses) < 1.12
    k30 = D.k_altitude_stability(1.0, 30.0)
    assert k30['spread_relative'] < 0.20, k30


def test_nash_sredniy_uroven_sravnen_so_standartom_i_raskhozhdenie_nazvano_chislom():
    """НАЙДЕНО 19.09 и не спрятано: наш средний поток ≥30 МэВ по приложению А выше
    среднего потока той же орбиты по табл. К.2.2 самого стандарта примерно в 12,7 раза.

    Это не дефект дозы, а состояние линии флюенса. Формула (Д.1) самонормирующаяся и
    постоянный множитель сокращает, но условие «сдвиг мультипликативен» объявлено в
    ограничениях. Тест держит число на виду и падает, если оно заметно сдвинется, —
    в том числе если линию потока когда-нибудь починят и расхождение уйдёт.
    """
    c = D.cross_check_against_standard()
    assert c['status'] == 'ok'
    assert c['standard_60deg_400km_per_cm2_s'] == pytest.approx(5.50, abs=0.05)
    assert c['ratio_to_60deg'] == pytest.approx(12.7, abs=0.5), c
    assert '12,7 раза' in D.LIMITS_RU


def test_spektry_prilozheniya_K_razobrany_i_svereny_s_tekstom_standarta():
    """Выборочная сверка извлечённых спектров с исходным текстом ОСТ, по ячейкам."""
    rows = list(csv.reader(io.open(os.path.join(ROOT, 'data', 'ost1044_spectra', 'K_2_2.csv'),
                                   encoding='utf-8')))
    alts = [float(x) for x in rows[0][1:]]
    assert alts[:4] == [400.0, 600.0, 800.0, 1000.0]
    energies = [float(r[0]) for r in rows[1:]]
    assert len(energies) == 38 and energies[0] == 0.1 and energies[-1] == 501.0
    # Таблица К.2.2, строка 1,00E-01: 9,48E+12 (400 км), 2,71E+13 (600 км)
    assert float(rows[1][1]) == pytest.approx(9.48e12, rel=1e-6)
    assert float(rows[1][2]) == pytest.approx(2.71e13, rel=1e-6)
    # Окончание таблицы К.2.2, строка 3,16E+01: 3,72E+07 (400 км), 9,58E+07 (600 км)
    r = next(r for r in rows[1:] if float(r[0]) == 31.6)
    assert float(r[1]) == pytest.approx(3.72e7, rel=1e-6)
    assert float(r[2]) == pytest.approx(9.58e7, rel=1e-6)
    index = json.load(io.open(os.path.join(ROOT, 'data', 'ost1044_spectra', 'index.json'),
                              encoding='utf-8'))
    assert all(x['monotone_400_to_600_km'] for x in index)
    # Дефект столбца «700 км» у наклонения 30 записан в индексе, а не забыт
    thirty = next(x for x in index if x['inclination_deg'] == 30)
    assert thirty['known_defect_ru'] and 'i98' in thirty['known_defect_ru']


# ---------------------------------------------------------------------------
# Объявление метода и ограничений
# ---------------------------------------------------------------------------

def test_ogranicheniya_nazyvayut_vse_chto_v_dozu_ne_voshlo():
    """Список обязателен к объявлению: солнечные протоны и ГКЛ, однородность защиты,
    кремний вместо ткани, поглощённая вместо эквивалентной, интерполяции числами."""
    L = D.LIMITS_RU
    for fragment in ('Солнечные протоны', 'галактические', 'ОДНОРОДНОЙ',
                     'КРЕМНИИ', 'не в ткани', 'НЕ эквивалентная доза', 'w_R',
                     'предел', 'наклонению'):
        assert fragment in L, fragment
    assert 'захваченных протонов' in L


def test_pravilo_nazyvaet_istochnik_tablicy_i_edinicu():
    assert 'ОСТ 134-1044-2007' in D.RULE_RU
    assert 'К.2.5' in D.RULE_RU and 'К.2.6' in D.RULE_RU
    assert 'рад(Si) = 10 мГр(Si)' in D.RULE_RU


def test_proiskhozhdenie_prosleedzhivaetsya_do_faylov():
    """У фактора должны быть идентификаторы записей: обе дозовые таблицы, нормировка, прил. А."""
    r = D.window_dose(1e6)
    ids = ' '.join(r.record_ids)
    assert 'ost1044_K_2_5_hemisphere' in ids
    assert 'ost1044_K_2_6_hemisphere' in ids
    assert 'ost1044_dose_norm' in ids
    assert 'ost1044_A_' in ids
    assert len(set(r.record_ids)) == len(r.record_ids)


# ---------------------------------------------------------------------------
# Подключение к оценке окна (vkd/windows/compare.py)
# ---------------------------------------------------------------------------

def _two_windows():
    """Те же синтетические входы, что в tests/test_compare.py: два окна разной наполненности."""
    from datetime import timedelta as td
    from vkd.types import EnvironmentSample, Kind, MagMethod, TrajectoryPoint, Window
    from vkd.windows.compare import Thresholds, assess_window
    t0 = datetime(2024, 5, 3, 12, 0, tzinfo=timezone.utc)
    track = [TrajectoryPoint(t0 + td(minutes=i), 0.0, 0.0, 420.0,
                             20000.0 if i < 60 else 40000.0, 1.3, 1.2, None,
                             MagMethod.DIPOLE, 'approximation', i < 60)
             for i in range(24 * 60)]
    g = EnvironmentSample(t0 - td(minutes=5), 'goes_p_ge10MeV', 0.2, 'pfu', 'noaa',
                          Kind.OBSERVATION, None, None, None, t0, 'preliminary', 'rec#goes')
    th = Thresholds()
    windows = [Window(t0, 360), Window(t0 + td(minutes=480), 360)]
    return [assess_window(w, track, BeltTable('min'), g, None, [], th, t0, mmod_hits=1e-6)
            for w in windows], th


def _factor(assessment, prefix):
    return next(f for m in assessment.mechanisms for f in m.factors if f.name.startswith(prefix))


def test_doza_prikhodit_obychnym_faktorom_okna():
    """Доза — такой же фактор, как минуты в аномалии и флюенс: значение, единица,
    происхождение «наш расчёт», правило и ограничения. Экран подхватывает её сам."""
    from vkd.types import Coverage, Kind, Presence
    assessments, _th = _two_windows()
    f = _factor(assessments[0], 'поглощённая доза')
    assert f.name == 'поглощённая доза за защитой скафандра 1 г/см²'
    assert f.unit == 'мГр(Si)'
    assert f.kind == Kind.OWN_CALCULATION
    assert f.presence == Presence.DETECTED and f.value > 0
    # Покрытие у дозы то же, что у флюенса: она из него и получена.
    assert f.coverage == _factor(assessments[0], 'флюенс').coverage
    assert isinstance(f.coverage, Coverage)
    assert 'ОСТ 134-1044-2007' in f.rule_applied
    assert 'захваченных протонов' in f.limits_note
    assert 'КРЕМНИИ' in f.limits_note


def test_faktor_dozy_prosleedzhivaetsya_do_zapisey():
    assessments, _th = _two_windows()
    ids = ' '.join(_factor(assessments[0], 'поглощённая доза').record_ids)
    for part in ('ost1044_K_2_5_hemisphere', 'ost1044_K_2_6_hemisphere',
                 'ost1044_dose_norm', 'ost1044_A_'):
        assert part in ids, part


def test_doza_v_ocenke_okna_ne_menyaet_poryadok_okon():
    """На настоящей оценке окна: упорядочение по дозе совпадает с упорядочением по флюенсу."""
    assessments, _th = _two_windows()
    pairs = [(_factor(a, 'флюенс').value, _factor(a, 'поглощённая доза').value) for a in assessments]
    assert all(f is not None and d is not None for f, d in pairs)
    by_f = [i for i, _ in sorted(enumerate(pairs), key=lambda kv: kv[1][0])]
    by_d = [i for i, _ in sorted(enumerate(pairs), key=lambda kv: kv[1][1])]
    assert by_f == by_d
    # и отношение доз в точности равно отношению флюенсов
    assert pairs[0][1] / pairs[1][1] == pytest.approx(pairs[0][0] / pairs[1][0], rel=1e-12)


def test_doza_ne_uchastvuet_v_pravile_vybora_okna():
    """Доза пропорциональна флюенсу, поэтому в правило сравнения она НЕ добавлена:
    это был бы четвёртый признак, повторяющий второй. Правило по-прежнему называет
    минуты и флюенс, а доза остаётся показанной величиной."""
    from vkd.windows.compare import recommend
    assessments, th = _two_windows()
    r = recommend(assessments, th)
    text = ' '.join([r.rule_applied or ''] + list(r.missing or ()))
    assert 'доз' not in text.lower(), text


def test_zapisi_dozy_popadayut_v_arkhiv_rascheta():
    """Каждый идентификатор, названный фактором дозы, обязан иметь свои БАЙТЫ в архиве.

    Иначе повтор сохранённого расчёта без сети не воспроизводит число: файл, по которому
    доза посчитана, в архив не попал. Ровно это и поймал tests/test_closeout.py при первом
    подключении фактора — три записи дозы не были зарегистрированы в манифесте.
    """
    from vkd.assess.dose import record_files
    declared = {rid for rid, _path, _meta in record_files()}
    cited = set(D.window_dose(1e6).record_ids)
    # Идентификаторы прил. А регистрирует сама таблица потоков, они здесь не проверяются.
    assert declared <= cited, declared - cited
    assert {x for x in cited if not x.startswith('ost1044_A_')} == declared
    for _rid, path, meta in record_files():
        assert os.path.isfile(os.path.join(ROOT, path)), path
        assert meta['sha256'] and meta['quality'] == 'model'


def test_manifest_registriruet_fayly_dozy():
    """Сквозная проверка через сам манифест: файлы дозы попадают в raw_records с байтами."""
    from vkd.assess.dose import record_files
    from vkd.integration.manifest import collect_records
    raw = {}
    collect_records(raw, {}, {'records': {}}, BeltTable('min'), ROOT)
    for rid, _path, _meta in record_files():
        assert rid in raw, rid
        assert raw[rid]['content_base64']
