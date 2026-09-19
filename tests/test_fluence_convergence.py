# -*- coding: utf-8 -*-
"""Сходимость известного флюенса на ОДИНАКОВОМ участке (разбор Codex п. 4).

Возражение Codex дословно: «На проверенных случаях отличие известного интеграла между сетками
60 и 5 секунд достигает 19,87 %. Это не доказанная физическая погрешность: меняется также
покрываемый участок.» Он прав: `examples/validation/orbit_steps.json` сравнивает интегралы,
у которых доля покрытия отличается (на «quiet» 81,1 % против 82,4 %), то есть участки РАЗНЫЕ.
Разность двух чисел, посчитанных на разных участках, о дискретизации не говорит ничего.

Здесь сетки сравниваются на одном и том же множестве минут: берётся ПЕРЕСЕЧЕНИЕ минут, целиком
покрытых моделью у обеих сеток, и обе интегрируются только по нему. Тогда единственное различие —
шаг, и разность становится величиной дискретизации.

Измеренное расхождение записано КАК ФАКТ: тест печатает его и требует, чтобы оно осталось
прежним. Это не норма точности и не граница физической погрешности — таблица ОСТ, эксцентричный
диполь и модель орбиты у обеих сеток одни и те же, и их собственные ошибки здесь не проверяются.
"""
from datetime import datetime, timedelta, timezone
import os

import pytest

from vkd.assess.magcoords import belt_coordinates
from vkd.assess.trapped import BeltTable
from vkd.orbit import trajectory_with_provenance
from vkd.orbit.integration import integrate_time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHC = os.path.join(ROOT, 'data', 'orbit', 'IGRF13.shc')
START = datetime(2024, 5, 3, 12, tzinfo=timezone.utc)
DURATION_MIN = 360
E_MIN_MEV = 30.0

# ИЗМЕРЕННЫЙ ФАКТ на этом коммите (окно 03.05.2024 12:00Z, 360 мин, канал ≥30 МэВ, IGRF-13,
# эксцентричный диполь, таблица ОСТ «солнечный минимум»). НЕ норма точности и НЕ граница
# физической погрешности. Если число изменилось — изменилась модель или орбита, и новое
# значение записывается сюда и в документы, а не прячется за расширенным допуском.
MEASURED_DELTA_PCT = 5.195
MEASURED_DELTA_TOL_PCT = 0.2


def _flux_series(step_seconds):
    """Поток по таблице ОСТ вдоль трассы с заданным шагом; None там, где модели нет."""
    _, points, _ = trajectory_with_provenance(START, DURATION_MIN, 24000.0, mode='history_review',
                                              cutoff_utc=START, step_seconds=step_seconds)
    coords, _ = belt_coordinates(points, SHC)
    belts = BeltTable('min')
    times = [p.t_utc for p in coords]
    values = [belts.integral_flux(p.L, p.B_over_B0, E_MIN_MEV).value_per_cm2_s for p in coords]
    return times, values


def _known_minutes(series, minute_starts):
    """Минуты, ЦЕЛИКОМ покрытые моделью на этой сетке: 60 с из 60 с, без разрывов."""
    ok = set()
    for m in minute_starts:
        r = integrate_time(*series, m, m + timedelta(minutes=1), max_gap_seconds=60.0)
        if r.covered_seconds == pytest.approx(60.0):
            ok.add(m)
    return ok


def _integral_over(series, minute_starts):
    """Интеграл ТОЛЬКО по названным минутам — у обеих сеток по одному и тому же множеству."""
    total = 0.0
    for m in minute_starts:
        r = integrate_time(*series, m, m + timedelta(minutes=1), max_gap_seconds=60.0)
        assert r.covered_seconds == pytest.approx(60.0)     # участок общий по построению
        total += r.known_integral
    return total


def test_shodimost_flyuensa_na_odinakovom_uchastke():
    minutes = [START + timedelta(minutes=i) for i in range(DURATION_MIN)]
    coarse, fine = _flux_series(60), _flux_series(5)
    common = sorted(_known_minutes(coarse, minutes) & _known_minutes(fine, minutes))
    assert len(common) >= DURATION_MIN // 2, (
        'общего покрытого участка слишком мало для сравнения: %d мин из %d' % (len(common), DURATION_MIN))
    assert len(common) < DURATION_MIN, 'покрытие оказалось полным — оговорка о пропусках была бы не нужна'

    i60, i5 = _integral_over(coarse, common), _integral_over(fine, common)
    delta_pct = 100.0 * abs(i60 - i5) / i5
    # ФАКТ, не норма: печатается, чтобы число было видно в протоколе прогона.
    print('\nобщий участок: %d мин из %d; флюенс 60 с = %.6g, 5 с = %.6g част./см²; расхождение %.3f %%'
          % (len(common), DURATION_MIN, i60, i5, delta_pct))
    assert delta_pct == pytest.approx(MEASURED_DELTA_PCT, abs=MEASURED_DELTA_TOL_PCT), (
        'на одинаковом участке расхождение сеток 60 и 5 с стало %.3f %%, записанный факт — %.3f %%; '
        'это не повод расширить допуск: изменилась модель или орбита, обнови число здесь и в документах'
        % (delta_pct, MEASURED_DELTA_PCT))


def test_pokrytoe_vremya_setok_razlichaetsya_i_eto_izmereno():
    """ПОЧЕМУ полные интегралы двух сеток нельзя сравнивать напрямую — измеренным числом.

    Множества ЦЕЛИКОМ покрытых минут у сеток 60 и 5 с на этом окне совпадают. Различается
    покрытое ВРЕМЯ: сетка 5 с видит куски минут, где модель есть лишь частично, а сетка 60 с
    такую минуту либо берёт целиком, либо не берёт вовсе. Отсюда 17 520 с против 17 795 с:
    участки разные, и разность полных интегралов смешивает дискретизацию с границей
    применимости модели. Это ровно возражение Codex, подтверждённое числами.
    """
    end = START + timedelta(minutes=DURATION_MIN)
    r60 = integrate_time(*_flux_series(60), START, end, max_gap_seconds=60.0)
    r5 = integrate_time(*_flux_series(5), START, end, max_gap_seconds=60.0)
    print('\nпокрытое время: 60 с = %.0f с (%.1f %%), 5 с = %.0f с (%.1f %%), разница %.0f с'
          % (r60.covered_seconds, 100 * r60.coverage_fraction,
             r5.covered_seconds, 100 * r5.coverage_fraction,
             r5.covered_seconds - r60.covered_seconds))
    assert r60.covered_seconds != r5.covered_seconds, (
        'участки совпали — тогда оговорка о разных участках больше не нужна')
    # Ни на одной сетке полный флюенс окна не известен: частичный интеграл им не объявляется.
    assert r60.total_integral is None and r5.total_integral is None
