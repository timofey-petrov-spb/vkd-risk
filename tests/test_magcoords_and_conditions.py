# -*- coding: utf-8 -*-
"""Найдено 19.09 по сохранённым примерам: (1) координаты центрального диполя A3 дают
нулевой флюенс на всей трассе; (2) одно протонное событие давало три условия.
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
    assert 0.06 < float((off ** 2).sum() ** 0.5) < 0.10          # ≈ 0,08 R_E (Fraser-Smith 1987, ~500 км)
    assert 28000 < b_eq < 31000                                 # экваториальное поле диполя, нТл
    assert abs(float((axis ** 2).sum()) - 1) < 1e-12


def test_belt_coordinates_give_nonzero_flux_where_a3_dipole_gives_none():
    pts = _traj()
    belts = BeltTable('min')
    a3 = [belts.integral_flux(p.L, p.B_over_B0, 30.0).value_per_cm2_s_sr for p in pts]
    assert not any(v for v in a3 if v)                          # центральный диполь: нуль/нет модели везде
    ecc, info = belt_coordinates(pts, IGRF13)
    vals = [belts.integral_flux(p.L, p.B_over_B0, 30.0).value_per_cm2_s_sr for p in ecc]
    nonzero = [v for v in vals if v]
    assert len(nonzero) > 20 and max(nonzero) > 0
    assert info['n_inconsistent_BB0'] < 0.3 * info['n']         # помечены, не обрезаны
    assert all(p.B_nT == q.B_nT and p.in_saa == q.in_saa for p, q in zip(pts, ecc))   # |B| и аномалия — от A3
    in_saa_flux = [v for p, v in zip(ecc, vals) if p.in_saa and v]
    assert len(in_saa_flux) >= 0.5 * sum(1 for p in ecc if p.in_saa)   # поток есть именно в аномалии


def _ev(eid, kind, start, note='', pub=None, end=None):
    return EventInterval(eid, kind, Kind.OBSERVATION if kind == 'SEP' else Kind.EXTERNAL_FORECAST, start, end, True, True,
                         start, end, 'test', pub or start, eid, note=note)


def test_linked_sep_records_become_one_condition_and_enlil_arrival_is_a_condition():
    pts, _ = belt_coordinates(_traj(), IGRF13)
    belts, th, win = BeltTable('min'), Thresholds(), Window(T0, 360)
    t = T0 - timedelta(hours=2)
    events = [_ev('donki_msg#AL-004', 'SEP', t, 'уведомление'),
              _ev('donki_sep#A', 'SEP', t + timedelta(minutes=1), 'ACE/EPAM'),
              _ev('donki_sep#B', 'SEP', t + timedelta(minutes=26), 'SOHO/EPHIN'),
              _ev('donki_enlil#X#1', 'CME_ARRIVAL', T0 + timedelta(hours=3), 'WSA-ENLIL: приход, Kp до 7'),
              _ev('donki_enlil#X#2', 'CME_ARRIVAL', T0 + timedelta(hours=4), 'WSA-ENLIL: приход, Kp до 8'),
              _ev('donki_msg#FLR', 'FLR', T0 + timedelta(hours=1), 'вспышка — информация')]
    a = assess_window(win, pts, belts, None, None, [], th, T0, events=events, mmod_hits=1e-7)
    reasons = a.mechanisms[0].needs_check_reasons
    assert len(reasons) == 2, reasons
    sep = [r for r in reasons if 'протонное событие' in r][0]
    assert '3 записи' in sep and 'donki_sep#B' in sep and 'исключено из автовыбора' in sep
    cme = [r for r in reasons if 'прихода выброса' in r][0]
    assert 'Kp до 8' in cme and '2 записи' in cme and 'WSA-ENLIL' in cme
    # вспышка — не условие
    assert not any('FLR' in r or 'вспышка' in r for r in reasons)


def test_storm_level_below_threshold_is_information_not_condition():
    pts, _ = belt_coordinates(_traj(), IGRF13)
    belts, th, win = BeltTable('min'), Thresholds(), Window(T0, 360)
    weak = [_ev('donki_enlil#Y#1', 'CME_ARRIVAL', T0 + timedelta(hours=2), 'WSA-ENLIL: приход, Kp до 3'),
            _ev('donki_msg#GST-G2', 'GST', T0 - timedelta(hours=3), 'gstID, сообщение X, Kp до 6.67')]
    a = assess_window(win, pts, belts, None, None, [], th, T0, events=weak, mmod_hits=1e-7)
    assert a.mechanisms[0].needs_check_reasons == ()
    strong = [_ev('donki_msg#GST-G3', 'GST', T0 - timedelta(hours=3), 'gstID, сообщение X, Kp до 7'),
              _ev('donki_msg#GST-unknown', 'GST', T0 + timedelta(hours=20), 'gstID, сообщение Y, Kp не назван')]
    a = assess_window(win, pts, belts, None, None, [], th, T0, events=strong, mmod_hits=1e-7)
    r = a.mechanisms[0].needs_check_reasons
    assert len(r) == 1 and 'Kp до 7' in r[0]                    # второе событие вне окна (через 20 ч)
    a = assess_window(Window(T0 + timedelta(hours=18), 360), pts, belts, None, None, [], th, T0, events=strong, mmod_hits=1e-7)
    assert any('уровень Kp не назван' in x for x in a.mechanisms[0].needs_check_reasons)
