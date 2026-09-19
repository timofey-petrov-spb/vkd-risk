# -*- coding: utf-8 -*-
"""Поглощённая доза за защитой скафандра за окно ВКД — приложение К ОСТ 134-1044-2007.

ЧТО ЭТО ЗА ВЕЛИЧИНА. Поглощённая доза от ЗАХВАЧЕННЫХ ПРОТОНОВ радиационных
поясов за алюминиевой защитой объявленной толщины, накопленная за время окна
ВКД. Единица — миллигрей в КРЕМНИИ, мГр(Si).

Почему грей, а не зиверт. Таблицы приложения К дают поглощённую дозу, и её
единица в СИ — грей. Зиверт требует взвешивающего коэффициента излучения w_R,
который для протонов зависит от ЛПЭ и в таблицах стандарта отсутствует;
поставить его отсюда было бы выдумыванием коэффициента. Поэтому мГр, и мГр
именно в кремнии: стандарт подписывает свои таблицы «Поглощенная доза от
протонов, рад (Si)» — это дозиметрия для электронных компонентов. Переход к
дозе в ткани требует отношения тормозных способностей ткань/кремний, которого
в первоисточниках репозитория нет; он НЕ выполняется. 1 рад = 10 мГр.

РАЗВЕДКА, три вопроса и ответы на них (19.09).

1. Толщина защиты. Узлы таблиц: 0,01 · 0,1 · 0,3 · 0,5 · 1 · 1,5 · 2 · 3 · 10 г/см².
   Защита скафандра ВКД порядка 0,5…1,5 г/см² алюминиевого эквивалента
   попадает в узлы ТОЧНО, и все три границы — сами узлы. Значит собственный
   перенос частиц не нужен и интерполяция по толщине не нужна: доза берётся из
   таблицы стандарта, а толщина остаётся ОБЪЯВЛЕННЫМ ВХОДОМ. Расчёт выдаёт не
   одно число, а весь диапазон 0,5 / 1,0 / 1,5 г/см² — чувствительность к
   толщине видна сразу, и подбирать её не из чего. Опорной для показа принята
   середина 1,0 г/см². Числа самой толщины скафандра в первоисточниках
   репозитория нет; ERI-000501 меряет глубинную дозу в скафандрах EMU и
   «Орлан-М» как раз в этом диапазоне глубин.

2. Переход от 10 лет к окну. Таблица даёт дозу за САС 10 лет на круговой
   орбите, то есть СРЕДНИЙ уровень и никакой зависимости от момента. Переход —
   отношением, по среднему уровню того же канала, посчитанному ТЕМ ЖЕ кодом
   (`scripts/dose_normalisation.py`, образец подхода — годовое среднее в
   `vkd/assess/seasonal.py`):

       D_окна = D_табл(d, h₀, i) · Φ_окна / (⟨φ⟩ · T₁₀)                  (Д.1)

   D_табл — доза за 10 лет, рад(Si); Φ_окна — наш флюенс окна, част./см²;
   ⟨φ⟩ — средний по орбите поток того же канала нашим кодом, см⁻²·с⁻¹;
   T₁₀ = 10·365,25·86400 с. Результат домножается на 10 (рад → мГр).

   Отношение выбрано не «чтобы сошлось»: оно СОКРАЩАЕТ постоянный
   множительный сдвиг нашей линии потока. Это здесь принципиально — см. ниже
   про масштаб R.

3. Наклонение. Таблицы даны для 30° и 60°, у МКС 51,6°. Принято:
   ИНТЕРПОЛИРОВАТЬ линейно по наклонению, вес 0,72 на таблицу 60°. Основание:
   51,6° лежит ВНУТРИ узлов, то есть это интерполяция, а не экстраполяция;
   доза на 400 км монотонно растёт с наклонением (179 → 238 рад при 1 г/см²);
   и главное — знаменатель ⟨φ⟩ посчитан на НАСТОЯЩЕЙ орбите 51,6°, поэтому
   числитель обязан быть о той же орбите, иначе отношение сшивает разные
   орбиты. Цена решения объявлена числом: взять 60° без интерполяции — это
   +6,4 % при 1,0 г/см², +12,2 % при 0,5 и +3,5 % при 1,5 г/см². Выбор ничего
   не решает: в любую сторону он меньше разброса по толщине защиты.

ЧТО ПОКАЗАЛА ПРОВЕРКА НОРМИРОВКИ (главное, произносить вслух). Стандарт даёт
для той же орбиты и свой собственный средний уровень — спектры К.2.1/К.2.2,
флюенс за те же 10 лет. Сравнение (`cross_check_against_standard`):

    ⟨φ⟩ наш, прил. А, 51,6°, 421 км ....... 70,06 см⁻²·с⁻¹
    ⟨φ⟩ стандарта, К.2.2, 60°, 400 км ......  5,50 см⁻²·с⁻¹
    масштаб R = 70,06 / 5,50 ............... = 12,7

Оба потока обрезаны сверху на 300 МэВ — последнем узле приложения А, — иначе
сравнивались бы разные каналы; без обрезания отношение 12,6, то есть хвост
здесь ни при чём.

Наша линия потока по приложению А даёт в среднем по орбите в 12,7 раза
больше, чем объявляет сам стандарт для такой орбиты. Это НЕ дефект дозы — это
дефект (или объявленная грубость) линии флюенса: эксцентричный диполь вместо
трассировки силовых линий, минимум СА против усреднения за 10 лет,
интерполяция по B/B0. Разбирать его должен владелец; здесь он назван числом.

Для дозы это значит следующее. Формула (Д.1) — САМОНОРМИРУЮЩАЯСЯ: величина
D_табл/(⟨φ⟩·T₁₀) содержит наш же ⟨φ⟩, и постоянный множитель в потоке из
ответа уходит. Если бы дозу считали «честнее выглядящим» способом — взять
дозу и флюенс из одного стандарта (k = D_табл/Φ_К22) и умножить на наш
флюенс окна, — ответ был бы в те же 12,7 раза завышен. Поэтому нормировка
делается НАШИМ кодом, а не таблицей К.2.2. Условие, при котором это верно,
объявлено в ограничениях: сдвиг должен быть мультипликативным.

Косвенно правильность цепочки подтверждается тем, что коэффициент
k = D_табл/Φ_табл, посчитанный внутри самого стандарта, почти не зависит от
высоты: при 1 г/см² и наклонении 60° это 1,37 · 1,29 · 1,30 · 1,35 ·10⁻⁷
рад/(част.·см⁻²) на 400, 600, 800, 1000 км — разброс 6,6 % при том, что сама
доза на этом отрезке меняется в 8 раз. Так и должен вести себя спектрально взвешенный пересчёт «флюенс →
доза», и это проверяется тестом.

ГЕОМЕТРИЯ ЗАЩИТЫ. Таблицы дают два варианта — полусфера и плоскость. Принята
ПОЛУСФЕРА: скафандр окружает человека, а не закрывает его с одной стороны.
Плоскость дала бы ×0,545 при 1 г/см² (число объявлено, геометрия — параметр).

ЧЕГО ЭТА ВЕЛИЧИНА НЕ ЕСТЬ — в `LIMITS_RU`, и она печатается рядом с числом.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
from dataclasses import dataclass
from typing import Optional, Sequence

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DOSE_DIR = os.path.join(_ROOT, 'data', 'ost1044')
_SPECTRA_DIR = os.path.join(_ROOT, 'data', 'ost1044_spectra')
_NORM_PATH = os.path.join(_ROOT, 'data', 'ost1044_dose', 'orbit_mean_flux.json')

MODEL_ID = 'ost1044_suit_dose_v1'
DOSE_UNIT_RU = 'мГр(Si)'
RAD_TO_MGY = 10.0                      # 1 рад = 0,01 Гр = 10 мГр
SECONDS_10Y = 10 * 365.25 * 86400.0    # САС 10 лет в таблицах приложения К

ISS_INCLINATION_DEG = 51.6
INCLINATION_NODES = (30.0, 60.0)
# Узлы толщины, покрывающие защиту скафандра ВКД. Все три — НАСТОЯЩИЕ узлы таблицы:
# по толщине не интерполируется ничего.
SUIT_THICKNESS_NODES = (0.5, 1.0, 1.5)
DEFAULT_THICKNESS_G_CM2 = 1.0
DEFAULT_GEOMETRY = 'hemisphere'
# Область применимости по высоте. Ограничена НАМЕРЕННО: у таблиц наклонения 30
# третий столбец подписан «700 км» и им не является (посторонние лексемы «i98»
# и «i 0-5» в шапке исходного текста, см. scripts/ost_parse_spectra.py).
# Столбцы 400 и 600 км стоят до него и не задеты, и МКС целиком внутри них.
ALT_DOMAIN_KM = (400.0, 600.0)

_TABLE_FILES = {(30.0, 'hemisphere'): 'K_2_5_hemisphere.csv',
                (30.0, 'plane'): 'K_2_5_plane.csv',
                (60.0, 'hemisphere'): 'K_2_6_hemisphere.csv',
                (60.0, 'plane'): 'K_2_6_plane.csv'}
_TABLE_NAMES = {30.0: 'К.2.5', 60.0: 'К.2.6'}

SOURCE_RU = 'ОСТ 134-1044-2007, прил. К, табл. К.2.5 и К.2.6 (поглощённые дозы от протонов ЕРПЗ)'

RULE_RU = (
    'доза за окно = доза за 10 лет по табл. К.2.5/К.2.6 ОСТ 134-1044-2007 (защита в форме '
    'полусферы, толщина — узел таблицы) × флюенс окна ÷ (средний по орбите поток того же канала × 10 лет); '
    'по высоте — логарифмическая интерполяция между узлами 400 и 600 км к опорной высоте нормировки, '
    'по наклонению — линейная между 30° и 60° к 51,6°; средний уровень посчитан ТЕМ ЖЕ кодом '
    'по 60 суткам эфемериды МКС; 1 рад(Si) = 10 мГр(Si)'
)

LIMITS_RU = (
    'Это поглощённая доза ТОЛЬКО от захваченных протонов пояса за объявленной толщиной защиты. '
    'Солнечные протоны и галактические космические лучи в неё НЕ входят — они считаются по другим '
    'стандартам и в этой величине отсутствуют; во время протонного события настоящая доза выше, '
    'и насколько — здесь не сказано. Электроны пояса и тормозное излучение не входят. '
    'Защита принята ОДНОРОДНОЙ по всему телу; настоящая зависит от направления прихода частицы и '
    'от части тела, и сама толщина 0,5…1,5 г/см² — объявленный вход, а не измерение. '
    'Это доза в КРЕМНИИ (таблицы стандарта подписаны «рад (Si)»), а не в ткани: переход к ткани '
    'требует отношения тормозных способностей, которого в первоисточниках нет, и он не сделан. '
    'Это НЕ эквивалентная доза органа (нет w_R) и НЕ сравнение с пределом по нормам. '
    'Переход от 10 лет к окну — отношение к нашему же среднему уровню; он сокращает постоянный '
    'множительный сдвиг линии потока, но только если сдвиг мультипликативен: наш средний поток '
    'выше среднего потока той же орбиты по табл. К.2.2 самого стандарта в 12,7 раза, '
    'и эта разница пока не разобрана. Интерполяции объявлены числами: по наклонению к 51,6° '
    '(взять 60° без интерполяции — +6,4 % при 1,0 г/см²), по высоте к опорной высоте нормировки. '
    'Порядок окон доза не меняет: она пропорциональна флюенсу того же канала.'
)


@dataclass(frozen=True)
class DoseResult:
    """Доза за окно и всё, чем она объявлена. value_mGy = None — величины нет."""
    value_mGy: Optional[float]
    status: str                       # 'ok' | 'no_fluence' | 'no_normalisation' | 'outside_domain'
    thickness_g_cm2: float
    geometry: str
    band_mGy: tuple                   # ((толщина, доза), …) по всем узлам скафандра
    k_mGy_per_particle: Optional[float]
    table_dose_rad_Si: Optional[float]
    e_min_MeV: float
    mean_flux_per_cm2_s: Optional[float]
    reference_alt_km: Optional[float]
    inclination_weight_60: float
    record_ids: tuple
    status_ru: str


def _read_csv(path: str):
    raw = open(path, 'rb').read()
    rows = list(csv.reader(io.StringIO(raw.decode('utf-8'))))
    alts = [float(x) for x in rows[0][1:]]
    thick = [float(r[0]) for r in rows[1:]]
    table = [[float(x) for x in r[1:]] for r in rows[1:]]
    return alts, thick, table, hashlib.sha256(raw).hexdigest()


_CACHE: dict = {}


def _table(inclination: float, geometry: str):
    key = (inclination, geometry)
    if key not in _CACHE:
        fn = _TABLE_FILES[key]
        _CACHE[key] = _read_csv(os.path.join(_DOSE_DIR, fn)) + (fn,)
    return _CACHE[key]


def _log_interp(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    """Логарифмическая по значению интерполяция — та же, что в vkd.assess.trapped:
    доза меняется на порядки между узлами высоты, и линейная по значению завышает
    середину. При нулевом узле — линейная."""
    if x1 == x0:
        return y0
    w = (x - x0) / (x1 - x0)
    if y0 > 0 and y1 > 0:
        return math.exp((1 - w) * math.log(y0) + w * math.log(y1))
    return y0 * (1 - w) + y1 * w


def dose_10y_rad_Si(thickness_g_cm2: float, alt_km: float, inclination_deg: float,
                    geometry: str = DEFAULT_GEOMETRY) -> tuple[Optional[float], dict]:
    """Доза за 10 лет САС, рад(Si), для круговой орбиты (высота, наклонение).

    Толщина обязана быть УЗЛОМ таблицы — по толщине здесь ничего не интерполируется.
    Высота — логарифмическая интерполяция между соседними узлами, только внутри
    объявленной области 400…600 км. Наклонение — линейная между 30° и 60°.
    """
    if geometry not in ('hemisphere', 'plane'):
        raise ValueError('geometry must be "hemisphere" or "plane"')
    if not (ALT_DOMAIN_KM[0] <= alt_km <= ALT_DOMAIN_KM[1]):
        return None, {'status': 'outside_domain',
                      'status_ru': 'высота %.1f км вне объявленной области таблицы %.0f…%.0f км'
                                   % (alt_km, *ALT_DOMAIN_KM)}
    if not (INCLINATION_NODES[0] <= inclination_deg <= INCLINATION_NODES[1]):
        return None, {'status': 'outside_domain',
                      'status_ru': 'наклонение %.1f° вне узлов таблицы %.0f…%.0f°'
                                   % (inclination_deg, *INCLINATION_NODES)}
    per_inclination, ids = [], []
    for inc in INCLINATION_NODES:
        alts, thick, table, sha, fn = _table(inc, geometry)
        if thickness_g_cm2 not in thick:
            raise ValueError('толщина %g г/см² не является узлом таблицы %s; узлы: %s'
                             % (thickness_g_cm2, _TABLE_NAMES[inc],
                                ', '.join('%g' % t for t in thick)))
        row = table[thick.index(thickness_g_cm2)]
        j = min(range(len(alts)), key=lambda k: (alts[k] < alt_km, abs(alts[k] - alt_km)))
        # соседние узлы высоты, охватывающие alt_km
        lo = max([k for k in range(len(alts)) if alts[k] <= alt_km], default=0)
        hi = min([k for k in range(len(alts)) if alts[k] >= alt_km], default=len(alts) - 1)
        per_inclination.append(_log_interp(alt_km, alts[lo], alts[hi], row[lo], row[hi])
                               if lo != hi else row[lo])
        ids.append('ost1044_%s_%s:%s' % (_TABLE_NAMES[inc].replace('.', '_').replace('К', 'K'),
                                         geometry, sha[:12]))
    w60 = (inclination_deg - INCLINATION_NODES[0]) / (INCLINATION_NODES[1] - INCLINATION_NODES[0])
    value = per_inclination[0] * (1 - w60) + per_inclination[1] * w60
    return value, {'status': 'ok', 'status_ru': 'значение из таблицы',
                   'at_30_deg_rad': per_inclination[0], 'at_60_deg_rad': per_inclination[1],
                   'inclination_weight_60': w60, 'record_ids': tuple(ids),
                   'geometry': geometry, 'alt_km': alt_km}


def normalisation(solar_activity: str = 'min', e_min_MeV: float = 30.0) -> Optional[dict]:
    """Средний по орбите уровень того же канала, посчитанный тем же кодом.

    Читается из data/ost1044_dose/orbit_mean_flux.json; пересчитывается
    scripts/dose_normalisation.py. None — для этого канала нормировки нет,
    и дозу считать нечем (молча нулём это не заменяется).
    """
    if 'norm' not in _CACHE:
        raw = open(_NORM_PATH, 'rb').read()
        data = json.loads(raw.decode('utf-8'))
        data['sha256'] = hashlib.sha256(raw).hexdigest()
        _CACHE['norm'] = data
    data = _CACHE['norm']
    ch = data['channels'].get('%s|%g' % (solar_activity, e_min_MeV))
    if ch is None:
        return None
    out = dict(ch)
    out['period'] = data['period']
    out['orbit'] = data['orbit']
    out['convergence_relative'] = data['convergence']['relative_change_half_to_full']
    out['record_id'] = 'ost1044_dose_norm:%s' % data['sha256'][:12]
    return out


def window_dose(fluence_per_cm2: Optional[float], *,
                thickness_g_cm2: float = DEFAULT_THICKNESS_G_CM2,
                geometry: str = DEFAULT_GEOMETRY,
                e_min_MeV: float = 30.0,
                solar_activity: str = 'min',
                inclination_deg: float = ISS_INCLINATION_DEG) -> DoseResult:
    """Поглощённая доза за защитой скафандра за окно, мГр(Si), по формуле (Д.1).

    fluence_per_cm2 — флюенс захваченных протонов ≥ e_min_MeV за окно, част./см²
    (то самое число, что уже стоит фактором в карточке окна). None — дозы нет.
    """
    empty = dict(thickness_g_cm2=thickness_g_cm2, geometry=geometry, band_mGy=(),
                 k_mGy_per_particle=None, table_dose_rad_Si=None, e_min_MeV=e_min_MeV,
                 mean_flux_per_cm2_s=None, reference_alt_km=None, inclination_weight_60=0.0,
                 record_ids=())
    norm = normalisation(solar_activity, e_min_MeV)
    if norm is None:
        return DoseResult(None, 'no_normalisation', status_ru=(
            'среднего по орбите уровня для канала ≥%g МэВ (%s СА) нет — дозу не на что нормировать'
            % (e_min_MeV, solar_activity)), **empty)
    mean_flux = norm['mean_flux_per_cm2_s']
    alt_ref = norm['orbit']['mean_alt_km']
    if not (mean_flux > 0):
        return DoseResult(None, 'no_normalisation',
                          status_ru='средний по орбите поток нулевой — отношение не определено', **empty)
    table_dose, prov = dose_10y_rad_Si(thickness_g_cm2, alt_ref, inclination_deg, geometry)
    if table_dose is None:
        return DoseResult(None, 'outside_domain', status_ru=prov['status_ru'], **empty)

    # k — доза на единицу флюенса нашего канала, мГр(Si)/(част.·см⁻²). Это и есть весь
    # пересчёт: доза за окно линейна по флюенсу, поэтому порядок окон она не меняет.
    def k_of(d: float) -> Optional[float]:
        v, _ = dose_10y_rad_Si(d, alt_ref, inclination_deg, geometry)
        return None if v is None else RAD_TO_MGY * v / (mean_flux * SECONDS_10Y)

    k = k_of(thickness_g_cm2)
    band = tuple((d, (None if fluence_per_cm2 is None or k_of(d) is None
                      else k_of(d) * fluence_per_cm2)) for d in SUIT_THICKNESS_NODES)
    ids = tuple(prov['record_ids']) + (norm['record_id'], norm['raw_record_id'])
    common = dict(thickness_g_cm2=thickness_g_cm2, geometry=geometry, band_mGy=band,
                  k_mGy_per_particle=k, table_dose_rad_Si=table_dose, e_min_MeV=e_min_MeV,
                  mean_flux_per_cm2_s=mean_flux, reference_alt_km=alt_ref,
                  inclination_weight_60=prov['inclination_weight_60'], record_ids=ids)
    if fluence_per_cm2 is None:
        return DoseResult(None, 'no_fluence', status_ru=(
            'флюенса окна нет, поэтому нет и дозы; отсутствие флюенса не означает нулевую дозу'),
            **common)
    return DoseResult(k * fluence_per_cm2, 'ok', status_ru='значение посчитано', **common)


def _num(value: Optional[float], spec: str = '%.4g') -> str:
    """Число по-русски: десятичная запятая. Строки фактора читает человек, и внутри
    одной фразы не должно стоять «0,301» рядом с «1.5 г/см²»."""
    return '—' if value is None else (spec % value).replace('.', ',')


def explain_ru(r: DoseResult) -> str:
    """Одной строкой: чем именно посчитано это число (для карточки окна)."""
    if r.k_mGy_per_particle is None:
        return r.status_ru
    return ('доза за 10 лет по таблицам %s = %s рад(Si) (защита %s, %s г/см², высота %s км, '
            'наклонение %s°: вес %s на таблицу 60°), средний по орбите поток того же канала '
            # Коэффициент печатается «на 10⁶ част./см²», а не «на одну частицу»: иначе в строке
            # стоит 1,12e-07, которое человеку ни о чём не говорит и читается как опечатка.
            '%s см⁻²·с⁻¹ за %s суток; отсюда %s мГр(Si) на каждые 10⁶ част./см² флюенса окна'
            % (_TABLE_NAMES[30.0] + ' и ' + _TABLE_NAMES[60.0], _num(r.table_dose_rad_Si),
               'полусфера' if r.geometry == 'hemisphere' else 'плоскость',
               _num(r.thickness_g_cm2, '%g'), _num(r.reference_alt_km, '%.1f'),
               _num(ISS_INCLINATION_DEG, '%g'), _num(r.inclination_weight_60, '%.2f'),
               _num(r.mean_flux_per_cm2_s), _num(
                   (normalisation() or {}).get('period', {}).get('days', 0.0), '%.0f'),
               _num(r.k_mGy_per_particle * 1e6, '%.3g')))


def band_ru(r: DoseResult) -> str:
    """Диапазон по толщине защиты — словами, с числами.

    Толщина — объявленный вход, а не измерение, поэтому рядом со значением всегда
    стоят обе границы: видно, что в этом числе от стандарта, а что от допущения.
    """
    parts = ['%s г/см²: %s' % (_num(d, '%g'), _num(v, '%.3g')) for d, v in r.band_mGy]
    return 'по толщине защиты (' + '; '.join(parts) + ') ' + DOSE_UNIT_RU


# ---------------------------------------------------------------------------
# Проверка нормировки против самого стандарта
# ---------------------------------------------------------------------------

def standard_mean_flux_per_cm2_s(inclination_deg: float, alt_km: float,
                                 e_min_MeV: float = 30.0,
                                 e_max_MeV: Optional[float] = 300.0) -> float:
    """Средний поток ≥ e_min по спектрам К.2.1/К.2.2 самого стандарта, см⁻²·с⁻¹.

    Таблицы дают ФЛЮЕНС за 10 лет на единицу энергии; деление на T₁₀ даёт средний
    поток. e_max обрезает хвост так же, как его обрезает vkd.assess.trapped
    (последний узел приложения А — 300 МэВ), иначе сравнивались бы разные каналы.
    """
    from vkd.assess.trapped import integrate_power_law
    import numpy as np
    inc = min(INCLINATION_NODES, key=lambda x: abs(x - inclination_deg))
    fn = 'K_2_1.csv' if inc == 30.0 else 'K_2_2.csv'
    rows = list(csv.reader(io.StringIO(open(os.path.join(_SPECTRA_DIR, fn), 'rb').read().decode('utf-8'))))
    alts = [float(x) for x in rows[0][1:]]
    if alt_km not in alts:
        raise ValueError('высота %g км не является узлом таблицы спектров' % alt_km)
    j = alts.index(alt_km)
    E = np.array([float(r[0]) for r in rows[1:]])
    F = np.array([float(r[1 + j]) for r in rows[1:]])
    if e_max_MeV is not None:
        m = E <= e_max_MeV
        E, F = E[m], F[m]
    return float(integrate_power_law(E, F, e_min_MeV)) / SECONDS_10Y


def cross_check_against_standard(e_min_MeV: float = 30.0, solar_activity: str = 'min') -> dict:
    """Во сколько раз наш средний по орбите поток выше среднего по самому стандарту.

    Единственная НЕЗАВИСИМАЯ проверка нормировки: доза и спектр в приложении К
    относятся к одной орбите и одному сроку, и наш собственный средний уровень
    обязан быть с ними одного порядка. Он не обязан совпадать — приложение А это
    минимум/максимум СА, а приложение К усреднено за 10 лет, — но множитель 12,6
    это уже не разница фаз цикла. Значение возвращается числом, не оценкой.
    """
    norm = normalisation(solar_activity, e_min_MeV)
    if norm is None:
        return {'status': 'no_normalisation'}
    ours = norm['mean_flux_per_cm2_s']
    std60 = standard_mean_flux_per_cm2_s(60.0, 400.0, e_min_MeV)
    std30 = standard_mean_flux_per_cm2_s(30.0, 400.0, e_min_MeV)
    return {'status': 'ok', 'e_min_MeV': e_min_MeV, 'solar_activity': solar_activity,
            'ours_per_cm2_s': ours, 'standard_60deg_400km_per_cm2_s': std60,
            'standard_30deg_400km_per_cm2_s': std30, 'ratio_to_60deg': ours / std60,
            'note_ru': ('наш средний поток ≥%g МэВ выше среднего потока той же орбиты по табл. К.2.2 '
                        'стандарта в %.1f раза; формула (Д.1) этот постоянный множитель сокращает, '
                        'но сам он остаётся незакрытым вопросом линии флюенса'
                        % (e_min_MeV, ours / std60))}


def k_altitude_stability(thickness_g_cm2: float = 1.0, inclination_deg: float = 60.0,
                         altitudes: Sequence[float] = (400.0, 600.0, 800.0, 1000.0)) -> dict:
    """k = доза за 10 лет / флюенс за 10 лет по таблицам ОДНОГО стандарта, рад/(част.·см⁻²).

    Если приложения К.2.x внутренне согласованы, k почти не зависит от высоты:
    это спектрально взвешенный пересчёт «флюенс → доза», а не свойство орбиты.
    Проверяется тестом; на 400…1000 км разброс около ±4 % при изменении дозы в 8 раз.
    """
    alts, thick, table, _sha, _fn = _table(inclination_deg, DEFAULT_GEOMETRY)
    row = table[thick.index(thickness_g_cm2)]
    out = {}
    for h in altitudes:
        phi = standard_mean_flux_per_cm2_s(inclination_deg, h, 30.0) * SECONDS_10Y
        out[h] = row[alts.index(h)] / phi
    values = list(out.values())
    return {'k_rad_per_particle': out, 'spread_relative': (max(values) - min(values)) / min(values)}
