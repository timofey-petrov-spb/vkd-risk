# -*- coding: utf-8 -*-
"""Найдено 19.09 по сохранённым примерам: (1) координаты центрального диполя A3
не дают покрытого таблицей ненулевого потока; (2) одно событие давало три условия.
Здесь закреплены исправления: эксцентричный диполь для таблиц ОСТ и сведение
связанных записей в одно условие; плюс прогноз прихода выброса WSA-ENLIL как условие."""
import os
from datetime import datetime, timedelta, timezone

from vkd.assess.magcoords import belt_coordinates, eccentric_dipole
from vkd.assess.trapped import BeltTable
from vkd.orbit import trajectory
from vkd.types import EventInterval, Kind, Window
from vkd.windows.compare import Thresholds, assess_window

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IGRF13 = os.path.join(ROOT, 'data', 'orbit', 'IGRF13.shc')
UTC = timezone.utc
T0 = datetime(2024, 5, 10, 12, tzinfo=UTC)


def _traj(minutes=360):
    _, pts = trajectory(T0, minutes, 24000, mode='history_review', cutoff_utc=T0)
    return pts


def test_eccentric_dipole_offset_is_physical():
    axis, b_eq, off = eccentric_dipole(IGRF13, T0)
    # 603 км = 0,0946 R_E на май 2024 и 609 км = 0,0956 R_E на сентябрь 2026 — пересчитано
    # по belt_coordinates.offset_km примеров; прежняя граница «≈0,08 R_E» была взята на глаз
    assert 0.085 < float((off ** 2).sum() ** 0.5) < 0.105
    assert 28000 < b_eq < 31000                                 # экваториальное поле диполя, нТл
    assert abs(float((axis ** 2).sum()) - 1) < 1e-12


def test_belt_coordinates_give_nonzero_flux_where_a3_dipole_gives_none():
    pts = _traj()
    belts = BeltTable('min')
    a3 = [belts.integral_flux(p.L, p.B_over_B0, 30.0).value_per_cm2_s for p in pts]
    assert all(v is None for v in a3)  # no table support, NOT measured physical zero
    ecc, info = belt_coordinates(pts, IGRF13)
    vals = [belts.integral_flux(p.L, p.B_over_B0, 30.0).value_per_cm2_s for p in ecc]
    nonzero = [v for v in vals if v]
    assert len(nonzero) > 20 and max(nonzero) > 0
    assert info['n_inconsistent_BB0'] < 0.3 * info['n']         # помечены, не обрезаны
    assert all(p.B_nT == q.B_nT and p.in_saa == q.in_saa for p, q in zip(pts, ecc))   # |B| и аномалия — от A3
    in_saa_flux = [v for p, v in zip(ecc, vals) if p.in_saa and v]
    # A magnetic-field threshold does not guarantee tabulated-spectrum coverage.
    # The old >=50% assertion relied on replacing a missing neighbouring spectrum
    # with zero and still reporting the weighted remaining spectrum as known.
    # Here we require a real nonzero contribution AND explicit unmodelled samples,
    # without treating an arbitrary fraction as a validation of magnetic coordinates.
    assert 0 < len(in_saa_flux) < sum(1 for p in ecc if p.in_saa)
    assert any(p.in_saa and v is None for p, v in zip(ecc, vals))


def _ev(eid, kind, start, note='', pub=None, end=None):
    return EventInterval(eid, kind, Kind.OBSERVATION if kind == 'SEP' else Kind.EXTERNAL_FORECAST, start, end, True, True,
                         start, end, 'test', pub or start, eid, note=note)


def test_linked_sep_records_become_one_condition_and_cme_arrival_is_a_condition():
    """R10 после стыка A2: приход выброса — из ОПУБЛИКОВАННОГО уведомления, порог по границе
    диапазона facts.kp_range_max; поля прогона enlilList поздних карточек не используются."""
    pts, _ = belt_coordinates(_traj(), IGRF13)
    belts, th, win = BeltTable('min'), Thresholds(), Window(T0, 360)
    t = T0 - timedelta(hours=2)
    events = [_ev('msg#AL-004:SEP', 'SEP', t, 'уведомление'),
              _ev('msg#AL-005:SEP', 'SEP', t + timedelta(minutes=1), 'повторный выпуск'),
              _ev('msg#AL-006:SEP', 'SEP', t + timedelta(minutes=26), 'третий выпуск'),
              _ev('msg#AL-010:CME_ARRIVAL', 'CME_ARRIVAL', T0 + timedelta(hours=3), 'приход CME'),
              _ev('msg#AL-011:CME_ARRIVAL', 'CME_ARRIVAL', T0 + timedelta(hours=4), 'приход CME'),
              _ev('msg#AL-012:FLR', 'FLR', T0 + timedelta(hours=1), 'вспышка — информация')]
    facts = {'msg#AL-010:CME_ARRIVAL': {'kp_range_min': 5.0, 'kp_range_max': 7.0, 'kp_basis': 'published_notification_range'},
             'msg#AL-011:CME_ARRIVAL': {'kp_range_min': 6.0, 'kp_range_max': 8.0, 'kp_basis': 'published_notification_range'}}
    a = assess_window(win, pts, belts, None, None, [], th, T0, events=events, mmod_hits=1e-7, event_facts=facts)
    reasons = a.mechanisms[0].needs_check_reasons
    assert len(reasons) == 2, reasons
    sep = [r for r in reasons if 'протонное событие' in r][0]
    assert '3 записи' in sep and 'msg#AL-006:SEP' in sep and 'не выбирается автоматически' in sep and 'уровень потока в записи не указан' in sep
    cme = [r for r in reasons if 'прихода выброса' in r][0]
    assert 'Kp до 8' in cme and 'верхняя граница опубликованного диапазона 6–8' in cme and '2 записи' in cme
    assert 'WSA-ENLIL' not in cme and 'kp_90' not in cme
    # вспышка — не условие
    assert not any('FLR' in r or 'вспышка' in r for r in reasons)


def test_cme_kp_range_bound_setting_selects_the_published_bound():
    """Настройка [history].cme_kp_range_bound меняет границу диапазона, а не молча её выбирает."""
    pts, _ = belt_coordinates(_traj(), IGRF13)
    belts, win = BeltTable('min'), Window(T0, 360)
    ev = [_ev('msg#AL-010:CME_ARRIVAL', 'CME_ARRIVAL', T0 + timedelta(hours=3), 'приход CME')]
    facts = {'msg#AL-010:CME_ARRIVAL': {'kp_range_min': 6.0, 'kp_range_max': 8.0, 'kp_basis': 'published_notification_range'}}
    by_max = assess_window(win, pts, belts, None, None, [], Thresholds(cme_kp_bound='max'), T0, events=ev,
                           mmod_hits=1e-7, event_facts=facts)
    by_min = assess_window(win, pts, belts, None, None, [], Thresholds(cme_kp_bound='min'), T0, events=ev,
                           mmod_hits=1e-7, event_facts=facts)
    assert len(by_max.mechanisms[0].needs_check_reasons) == 1        # 8 >= 7 — условие есть
    assert by_min.mechanisms[0].needs_check_reasons == ()            # 6 < 7 — только информация


def test_sep_threshold_message_is_not_reported_as_a_measured_flux():
    """Уведомление DONKI сообщает порог (поток > 10 pfu), а не измеренный поток (разбор Codex, п. 3)."""
    pts, _ = belt_coordinates(_traj(), IGRF13)
    belts, th, win = BeltTable('min'), Thresholds(), Window(T0, 360)
    ev = [_ev('msg#AL-004:SEP', 'SEP', T0 - timedelta(hours=1), 'пороговое сообщение')]
    facts = {'msg#AL-004:SEP': {'detector': 'GOES', 'energy_lower_bound_MeV': 10.0, 'flux_lower_bound_pfu': 10.0,
                                'measured_flux_pfu': None, 'noaa_s_scale': None}}
    a = assess_window(win, pts, belts, None, None, [], th, T0, events=ev, mmod_hits=1e-7, event_facts=facts)
    c = [c for c in a.mechanisms[0].conditions if c.kind == 'SEP'][0]
    assert 'S1' in c.text and 'нижняя граница по тексту уведомления' in c.text and c.severity == 'limiting'
    assert 'измеренный поток' not in c.text
    # канал >= 100 МэВ шкалой S не подписывается: это другой канал
    ev100 = [_ev('msg#AL-006:SEP', 'SEP', T0 - timedelta(hours=1), 'пороговое сообщение')]
    f100 = {'msg#AL-006:SEP': {'detector': 'GOES', 'energy_lower_bound_MeV': 100.0, 'flux_lower_bound_pfu': 1.0,
                               'measured_flux_pfu': None}}
    a100 = assess_window(win, pts, belts, None, None, [], th, T0, events=ev100, mmod_hits=1e-7, event_facts=f100)
    c100 = [c for c in a100.mechanisms[0].conditions if c.kind == 'SEP'][0]
    assert c100.level_note.startswith('порог по каналу ≥100 МэВ')      # уровень S по этому каналу не называется
    assert 'S1 (' not in c100.text and 'S2 (' not in c100.text


def test_storm_level_below_threshold_is_information_not_condition():
    pts, _ = belt_coordinates(_traj(), IGRF13)
    belts, th, win = BeltTable('min'), Thresholds(), Window(T0, 360)
    weak = [_ev('msg#AL-020:CME_ARRIVAL', 'CME_ARRIVAL', T0 + timedelta(hours=2), 'приход CME'),
            _ev('msg#AL-021:GST', 'GST', T0 - timedelta(hours=3), 'наблюдение Kp за 3 часа')]
    facts = {'msg#AL-020:CME_ARRIVAL': {'kp_range_min': 2.0, 'kp_range_max': 3.0},
             'msg#AL-021:GST': {'kp': 6.67}}
    a = assess_window(win, pts, belts, None, None, [], th, T0, events=weak, mmod_hits=1e-7, event_facts=facts)
    assert a.mechanisms[0].needs_check_reasons == ()


def test_geodetic_to_ecef_differs_from_the_spherical_shortcut():
    """R11: широта A3 геодезическая — сферическая формула r = R_E + h даёт ошибку положения."""
    import math
    import numpy as np
    from vkd.assess.magcoords import R_E_KM, geodetic_to_ecef_km

    def spherical(lat_deg, lon_deg, alt_km):
        la, lo = math.radians(lat_deg), math.radians(lon_deg)
        return (R_E_KM + alt_km) * np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])

    # на экваторе эллипсоид шире опорной сферы IGRF, у полюса — уже; расхождение порядка километров
    for lat, lo in ((0.0, 0.0), (51.6, 30.0), (-30.0, -45.0)):
        d = float(np.linalg.norm(geodetic_to_ecef_km(lat, lo, 420.0) - spherical(lat, lo, 420.0)))
        assert 3.0 < d < 30.0, (lat, d)
    v = geodetic_to_ecef_km(51.6, 30.0, 420.0)
    assert abs(float(np.linalg.norm(v)) - (R_E_KM + 420.0)) > 3.0     # это НЕ сфера радиуса R_E + h
    assert abs(v[2] / float(np.linalg.norm(v)) - math.sin(math.radians(51.6))) > 1e-4   # геодезическая != геоцентрическая


def test_belt_coordinates_declare_both_methods_separately():
    """R11: L и B/B0 — эксцентричный диполь, cutoff_GV — метод A3; подписаны раздельно."""
    _, info = belt_coordinates(_traj(60), IGRF13)
    assert 'WGS84' in info['position_frame'] and 'ECEF' in info['position_frame']
    assert 'эксцентричный диполь' in info['L_B0_method']
    assert 'A3' in info['cutoff_GV_method'] and 'центральный' in info['cutoff_GV_method']


def test_storm_condition_uses_observed_kp_fact_and_window_overlap():
    pts, _ = belt_coordinates(_traj(), IGRF13)
    belts, th, win = BeltTable('min'), Thresholds(), Window(T0, 360)
    strong = [_ev('msg#AL-030:GST', 'GST', T0 - timedelta(hours=3), 'наблюдение Kp за 3 часа'),
              _ev('msg#AL-031:GST', 'GST', T0 + timedelta(hours=20), 'уровень в теле не назван')]
    facts = {'msg#AL-030:GST': {'kp': 7.0}}          # у второй записи фактов нет — уровень неизвестен
    a = assess_window(win, pts, belts, None, None, [], th, T0, events=strong, mmod_hits=1e-7, event_facts=facts)
    r = a.mechanisms[0].needs_check_reasons
    assert len(r) == 1 and 'Kp до 7' in r[0] and 'наблюдённый Kp уведомления' in r[0]   # второе событие вне окна (через 20 ч)
    a = assess_window(Window(T0 + timedelta(hours=18), 360), pts, belts, None, None, [], th, T0,
                      events=strong, mmod_hits=1e-7, event_facts=facts)
    # уровень называется (или не называется) по КОНКРЕТНОЙ записи, а не по кластеру
    assert any('уровень Kp в записи не назван' in x for x in a.mechanisms[0].needs_check_reasons)
