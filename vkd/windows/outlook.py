# -*- coding: utf-8 -*-
"""ПЕРСПЕКТИВА: когда планировать выход через неделю или через месяц.

ЭТО НЕ ВЕРДИКТ И НЕ ЗАМЕНА ВЕРДИКТУ. Постановка задачи требует период поиска ДО 24 Ч
и обоснованный прогноз на ближайшие 6 часов; эта обязательная часть считается там, где
и считалась (vkd/windows/scan.py, vkd/windows/compare.py), и её границы здесь не
двигаются ни на минуту. Модуль отвечает на другой вопрос — «в какие сутки ближайшего
месяца обстановка заведомо хуже или заведомо лучше» — и отвечает ДРУГОЙ ТОЧНОСТЬЮ,
объявленной в каждой строке. Рекомендованного окна он не выдаёт, начала выхода не
ранжирует и вероятностей не пересчитывает.

ТРИ МЕХАНИЗМА ВЕДУТ СЕБЯ НА ДАЛЬНЕМ СРОКЕ ПО-РАЗНОМУ, И В ЭТОМ ВСЁ ДЕЛО
=====================================================================

1. МЕТЕОРОИДЫ СЧИТАЮТСЯ ТОЧНО НА ЛЮБУЮ ДАТУ. Сезонная модель (vkd/assess/seasonal.py)
   детерминирована по дате: активность 49 потоков ECSS C-2 берётся от солнечной
   долготы, а она известна на годы вперёд. Область применимости модели 2000–2050.
   Это самая сильная линия перспективы, и в ней есть настоящая сезонность: строка
   суток несёт и полное ожидаемое число попаданий, и отдельно вклад потоков, и
   превышение над среднегодовым фоном тех же суток.

2. ГЕОМЕТРИЯ ТРАССЫ СЧИТАЕТСЯ, НО ТОЧНОСТЬ ПАДАЕТ. Ошибка накапливается ВДОЛЬ трассы,
   то есть как сдвиг времени прихода в ту же точку. Поэтому на дальнем сроке
   обещать минуты в аномалии для окна, начинающегося в конкретную минуту, нельзя,
   а суточную величину — можно: сколько всего минут в аномалии набирается за сутки
   и в какие часы суток трасса проходит аномалию чаще.

3. КОСМИЧЕСКАЯ ПОГОДА ОБРЫВАЕТСЯ ДВАЖДЫ. Трёхсуточный прогноз NOAA даёт Kp по
   3 часа и суточную вероятность события. 27-суточный обзор NOAA
   (vkd/integration/noaa_27day.py) даёт только суточные величины и НЕ СОДЕРЖИТ
   прогноза протонных событий вовсе. За горизонтом обзора космопогода неизвестна.

С КАКОГО ГОРИЗОНТА ПОМИНУТНОЕ РАЗРЕШЕНИЕ СМЕНЯЕТСЯ СУТОЧНЫМ И ЧЕМ ЭТО ЗАМЕРЕНО
==============================================================================

Граница — `GEOMETRY_MINUTE_LIMIT_DAYS`, и она НЕ выбрана на глаз. Она равна уже
объявленному в сервисе пределу возраста набора элементов
(`config/settings.toml [thresholds].tle_max_age_days` = 3 сут): за этим пределом
модуль орбиты СЧИТАТЬ ОТКАЗЫВАЕТСЯ — `trajectory_with_provenance` поднимает
OrbitDataError «TLE outside declared age limit». Перспектива продолжает тот же
набор элементов дальше сознательно, помечает это в каждой строке
(`beyond_declared_tle_age`) и отдаёт наружу только суточную величину.

Замеры 19.09.2026 (Windows 11, Python 3.14.6; скрипты воспроизводимы, см. отчёт).
Поздний выпуск эфемериды с запасом предсказания ≤1,5 сут принят за истину, ранний —
за прогноз; сравнивается одно и то же, посчитанное одним и тем же кодом.

  А. 26 выпусков операционной эфемериды NASA/JSC (OEM) из архива A1 за 29.04–28.06.2024,
     116 пар, 7894 точки сравнения через 30 мин. Смещение ВДОЛЬ трассы, переведённое
     во время прихода в ту же точку (медиана / 90 % / максимум, мин):
        запас 2–3 сут  — 0,02 / 0,04 / 0,05
        запас 5–6 сут  — 0,07 / 0,26 / 0,36
        запас 7–9 сут  — 0,16 / 0,58 / 0,90
        запас 9–11 сут — 0,43 / 0,99 / 1,27
        запас 13–15 сут— 0,95 / 2,01 / 2,59
     Дальше 15 суток сравнивать не с чем: сама операционная эфемерида длиной 15 суток.
  Б. Те же пары, но считается СЛУЖЕБНАЯ величина — минуты в аномалии (|B| < 24000 нТл),
     101 сравнение суток: скользящее окно 360 мин с шагом 10 мин и суточная сумма.
     Расхождение окна: медиана 0 мин при запасе до 7 сут, 1 мин при 9–14 сут, максимум
     4 мин при 7–14 сут. Расхождение СУТОЧНОЙ суммы: 0–1 мин, то есть ≤0,5 %.
  В. Два набора TLE (эпохи 17.09.2026 11:56 и 18.09.2026 03:25 UTC, разнос 15,5 ч) —
     тот самый путь SGP4, которым считает текущий режим. До 27 суток вперёд:
     расхождение суточной суммы минут в аномалии 0–2 мин, расхождение по каждому часу
     суток ≤1 мин, расхождение окна 360 мин ≤3 мин (на 30-е сутки — 5 мин и 3 мин).

ЧТО ИЗ ЭТОГО СЛЕДУЕТ, ЧЕСТНО. Сама геометрия поминутному ответу не мешала бы и на
двух неделях: ошибка окна (4 мин) остаётся ниже допуска равнозначности сервиса
`equiv_tol_min` = 5 мин, за которым сервис и так объявляет окна неразличимыми.
Мешает другое — входные данные объявлены применимыми только на 3 суток, и печатать
поминутный ответ по заведомо неприменимому входу сервис не имеет права. Поэтому
поминутное разрешение кончается ровно там, где кончается объявленная применимость
входа, а не там, где кончилось терпение. Суточная же величина, которую перспектива
отдаёт дальше, воспроизводится в пределах 1–2 мин из 215–240 (замеры Б и В) —
это и есть основание показывать её на 27 суток.

ЧЕГО ЗДЕСЬ НЕ ДЕЛАЕТСЯ НИ ПРИ КАКИХ УСЛОВИЯХ
============================================
  * суточная вероятность не пересчитывается в вероятность за окно — ни делением,
    ни «1-(1-p)^k», никак; ячейка сохраняет исходные границы суток;
  * отсутствие данных не заполняется нулём и не достраивается последним значением:
    в строке стоит None и названная причина;
  * перспектива не называет рекомендованное окно и не ранжирует начала — иначе на
    одном экране стояли бы два разных ответа на один вопрос.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import math
import os
from typing import Optional

from vkd.integration.noaa_27day import PROTON_UNKNOWN_RU, RESOLUTION_RU
from vkd.types import Kind

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MAX_DAYS = 120                  # предел запроса: цена линейна по суткам (замер 1,2 с/сут)
DAY = timedelta(days=1)
SEASONAL_DOMAIN = (2000, 2050)  # область применимости солнечной аппроксимации сезонной модели
# Докуда накопление ошибки трассы ИЗМЕРЕНО (замеры А–В в описании модуля): 15 суток по
# операционной эфемериде и 30 суток по паре наборов элементов. За этим сроком суточная
# величина всё ещё считается тем же кодом, но её воспроизводимость никем не мерена, и
# строка суток говорит об этом прямо. Число — граница ЗАМЕРА, а не физики.
MEASURED_AGE_DAYS = 30.0

# Ярусы. Границы — не круглые числа «на глаз», у каждой названа причина.
TIER_VERDICT = 'verdict'
TIER_3DAY = 'three_day'
TIER_27DAY = 'outlook27'
TIER_SEASONAL = 'seasonal_only'


@dataclass(frozen=True)
class Tier:
    """Объявленный ярус: докуда он действует, что на нём известно и чего нет.

    Ярус — это ДАННЫЕ, а не подразумеваемое правило: экран и выгрузка берут границы
    и оговорки отсюда, а не повторяют их своими словами.
    """
    tier_id: str
    from_day: int                       # включительно, сутки от начала срока (0 — первые сутки)
    to_day: Optional[int]               # исключая; None — без верхней границы
    label_ru: str
    geometry_resolution: str            # 'minute' | 'daily'
    weather_resolution: str             # '3h' | 'daily_max' | 'none'
    proton_forecast: bool               # есть ли на этом ярусе прогноз протонных событий
    is_verdict: bool                    # даёт ли ярус ОСНОВНОЙ вердикт сервиса
    known_ru: tuple
    unknown_ru: tuple
    boundary_reason_ru: str


@dataclass(frozen=True)
class OutlookDay:
    """Одни сутки перспективы. None везде означает «не посчитано / не сказано», не ноль."""
    date_utc: datetime                  # 00:00 UTC этих суток
    day_index: int                      # 0 — сутки, в которые попадает начало срока
    tier_id: str
    tier_label_ru: str

    # --- метеороиды: считаются точно на любую дату области 2000–2050
    mmod_hits: Optional[float] = None            # ожидаемое число попаданий на площадку за сутки
    mmod_streams: Optional[float] = None         # из них вклад 49 потоков ECSS C-2
    mmod_background: Optional[float] = None      # спорадический фон этих суток
    mmod_annual_mean: Optional[float] = None     # среднегодовой фон на той же трассе
    mmod_excess_pct: Optional[float] = None      # превышение над среднегодовым фоном, %
    mmod_top_streams: tuple = ()                 # ((имя, попаданий), ...) — заметные потоки суток
    mmod_status_ru: str = ''

    # --- геометрия трассы
    saa_min: Optional[float] = None              # минут в аномалии за сутки
    saa_by_hour: tuple = ()                      # 24 значения: минут в аномалии в каждом часе UTC
    quiet_hours_utc: Optional[tuple] = None      # (час начала, час конца, минут в аномалии) самого спокойного блока
    busy_hours_utc: Optional[tuple] = None       # то же для самого загруженного блока
    geometry_resolution: str = 'daily'           # 'minute' | 'daily' | 'none'
    beyond_declared_tle_age: bool = False        # набор элементов продолжен за объявленный предел
    geometry_measured: bool = True               # накопление ошибки на этот срок ИЗМЕРЕНО (см. MEASURED_AGE_DAYS)
    element_set_age_days: Optional[float] = None # возраст набора элементов на конец этих суток
    geometry_status_ru: str = ''

    # --- космическая погода
    kp_max: Optional[float] = None               # наибольший Kp за сутки
    kp_source_id: Optional[str] = None
    kp_resolution: Optional[str] = None          # '3h' (свёрнут из 3-часовых ячеек) | 'daily_max' | None
    kp_cells_3h: tuple = ()                      # ((начало, значение), ...) — только на ярусе трёх суток
    a_index: Optional[float] = None
    f107_sfu: Optional[float] = None
    proton_prob_daily_pct: Optional[float] = None
    proton_known: bool = False
    weather_status_ru: str = ''

    known_ru: tuple = ()
    unknown_ru: tuple = ()

    @property
    def saa_share_pct(self) -> Optional[float]:
        return None if self.saa_min is None else 100.0 * self.saa_min / 1440.0


@dataclass(frozen=True)
class OutlookResult:
    """Ответ перспективы целиком. Основной вердикт сюда не входит и отсюда не следует."""
    start_utc: datetime                 # начало срока, как его попросили
    first_day_utc: datetime             # 00:00 UTC суток, в которые попадает начало срока
    days: int
    generated_utc: datetime
    tiers: tuple                        # Tier, по одному на объявленный ярус
    rows: tuple                         # OutlookDay
    orbit: dict                         # какой набор элементов применён и какой у него возраст
    weather: dict                       # какие выпуски космопогоды применены и докуда достают
    meteoroids: dict                    # версия модели и её область применимости
    limitations: tuple
    scope_ru: str                       # объявленная область: что перспектива отвечает, а что нет
    rule_ru: str                        # как посчитано
    boundaries: dict = field(default_factory=dict)   # границы ярусов числами, для экрана и выгрузки

    def tier(self, tier_id: str) -> Optional[Tier]:
        return next((t for t in self.tiers if t.tier_id == tier_id), None)

    def row(self, date_utc: datetime) -> Optional[OutlookDay]:
        d = _utc(date_utc)
        return next((r for r in self.rows if r.date_utc <= d < r.date_utc + DAY), None)


SCOPE_RU = (
    'ПЕРСПЕКТИВА — дополнительная функция рядом с вердиктом, другой природы и другой точности. '
    'Она отвечает на вопрос «в какие сутки обстановка заведомо хуже или лучше», а не «во сколько '
    'выходить». Рекомендованного окна здесь нет, начала выхода не ранжируются, суточные вероятности '
    'в вероятность за окно не пересчитываются. Основной вердикт остаётся в пределах суток и считается '
    'там же, где считался: период поиска до 24 ч, обоснование на ближайшие 6 часов.')

RULE_RU = (
    'По каждым суткам UTC: (1) метеороиды — сезонная модель ECSS C-2 на фактической трассе этих '
    'суток, число попаданий на площадку за сутки, отдельно вклад потоков и превышение над '
    'среднегодовым фоном; (2) геометрия — минуты в аномалии |B| < порога за сутки и их распределение '
    'по часам UTC, по трассе с шагом 60 с; (3) космопогода — наибольший Kp за сутки: до трёх суток '
    'он свёрнут из 3-часовых ячеек живого прогноза NOAA (сами ячейки сохранены), дальше взят прямо '
    'из 27-суточного обзора NOAA. Протонная линия есть только там, где её публикует NOAA.')


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Перспектива требует времени с объявленным поясом (UTC)')
    return value.astimezone(timezone.utc)


def _day_floor(value: datetime) -> datetime:
    return _utc(value).replace(hour=0, minute=0, second=0, microsecond=0)


def declared_tiers(minute_limit_days: int, outlook_last_day: Optional[int]) -> tuple:
    """Четыре объявленных яруса с их границами и причинами границ.

    `outlook_last_day` — номер последних суток, которые ещё покрыты 27-суточным обзором
    (исключая). None — обзора нет вовсе, и ярус обзора пуст: тогда за трёхсуточным
    прогнозом сразу начинается «только метеороиды».
    """
    tiers = [
        Tier(TIER_VERDICT, 0, 1, 'до 1 суток — основной вердикт', 'minute', '3h', True, True,
             ('полный расчёт: перебор начал, минуты в аномалии, флюенс, условия проверки, карточки факторов',
              'наблюдения GOES и Kp, живой трёхсуточный прогноз NOAA, уведомления DONKI'),
             (),
             'Граница 1 сутки — постановка задачи: период поиска начала выхода до 24 ч. '
             'Перспектива этой границы не двигает и внутрь неё не вмешивается.'),
        Tier(TIER_3DAY, 1, minute_limit_days, 'от 1 до %d суток — прогноз NOAA на трое суток'
             % minute_limit_days, 'minute', '3h', True, False,
             ('метеороиды — точно',
              'геометрия — поминутная: набор элементов ещё в объявленном пределе возраста',
              'Kp по 3-часовым ячейкам прогноза NOAA',
              'суточная вероятность протонного события (S1 и выше) — как суточная, без пересчёта'),
             ('вердикта на эти сутки нет: его границы поставлены постановкой задачи',),
             'Граница %d суток — это одно и то же число с двух сторон: горизонт трёхсуточного '
             'прогноза NOAA и объявленный предел возраста набора элементов '
             '[thresholds].tle_max_age_days, за которым модуль орбиты считать отказывается. '
             'Предел считается ОТ ЭПОХИ набора элементов, а не от начала срока: если набор уже '
             'сутки как выпущен, поминутный ярус на эти сутки и короче. Фактическая граница '
             'этого расчёта — в boundaries.minute_geometry_last_day_effective.'
             % minute_limit_days),
    ]
    if outlook_last_day is not None:
        upper = max(minute_limit_days, outlook_last_day)
        tiers.append(Tier(
            TIER_27DAY, minute_limit_days, upper,
            'от %d до %d суток — 27-суточный обзор NOAA' % (minute_limit_days, upper),
            'daily', 'daily_max', False, False,
            ('метеороиды — точно',
             'геометрия — только суточная: минут в аномалии за сутки и их раскладка по часам UTC',
             'наибольший Kp за сутки из 27-суточного обзора, индекс A и поток 10,7 см как справка о фоне'),
            (PROTON_UNKNOWN_RU,
             'в какие часы суток буря — обзор не говорит: разрешение суточное',
             'минуты в аномалии для окна, начинающегося в конкретную минуту, здесь не обещаются'),
            'Верхняя граница — не «27 суток от сегодня», а конец последних суток КОНКРЕТНОГО '
            'выпуска обзора: он выпускается раз в неделю, и его горизонт отсчитывается от строки '
            ':Issued:, а не от времени скачивания.'))
    first_seasonal = tiers[-1].to_day if tiers[-1].to_day is not None else minute_limit_days
    tiers.append(Tier(
        TIER_SEASONAL, first_seasonal, None, 'дальше %d суток — только метеороиды и статистика трассы'
        % first_seasonal, 'daily', 'none', False, False,
        ('метеороиды — точно: сезонная модель детерминирована по дате в области 2000–2050',
         'геометрия — суточная статистика трассы'),
        ('космическая погода НЕИЗВЕСТНА ВОВСЕ: ни Kp, ни протонных событий, ни вспышек',
         'ни один источник сервиса так далеко не публикует прогноз'),
        'Нижняя граница — конец последнего выпуска космопогоды, какой есть на руках. '
        'За ней перспектива держится на двух линиях из трёх и говорит об этом прямо.'))
    return tuple(tiers)


# ---------------------------------------------------------------------------
# Геометрия и метеороиды по суткам
# ---------------------------------------------------------------------------

_DAY_CACHE: dict = {}
_CACHE_LIMIT = 256


def _quiet_block(by_hour: tuple, block_h: int, *, quietest: bool) -> Optional[tuple]:
    """Самый спокойный (или самый загруженный) непрерывный блок целых часов суток.

    СУТОЧНАЯ статистика целыми часами, а не рекомендация окна: космопогоды, флюенса
    и условий проверки в ней нет, и минуту начала она не называет.
    """
    if len(by_hour) != 24 or block_h < 1 or block_h > 24:
        return None
    best = None
    for start in range(24):
        total = sum(by_hour[(start + k) % 24] for k in range(block_h))
        if best is None or (total < best[2] if quietest else total > best[2]):
            best = (start, (start + block_h) % 24, float(total))
    return best


def _day_geometry(day_start: datetime, *, repo_root: str, tle_path: Optional[str],
                  saa_B_threshold_nT: float, max_tle_age_days: float, block_h: int,
                  with_states: bool):
    """Трасса этих суток одним вызовом модуля орбиты A3. Второго способа счёта тут нет."""
    from vkd.orbit import trajectory_with_provenance
    meta, points, prov = trajectory_with_provenance(
        day_start, 1440, saa_B_threshold_nT, mode='live', repo_root=repo_root, tle_path=tle_path,
        max_tle_age_days=max_tle_age_days, include_inertial_states=with_states)
    by_hour = [0.0] * 24
    unknown = 0
    for point in points[:-1]:            # последняя точка — начало следующих суток
        if point.in_saa is None:
            unknown += 1
        elif point.in_saa:
            by_hour[point.t_utc.hour] += 1.0
    by_hour = tuple(by_hour)
    return {
        'meta': meta, 'points': points, 'provenance': prov,
        'saa_min': sum(by_hour), 'by_hour': by_hour, 'unknown_minutes': unknown,
        'quiet': _quiet_block(by_hour, block_h, quietest=True),
        'busy': _quiet_block(by_hour, block_h, quietest=False),
    }


def _day_meteoroids(points, states, area_m2: float, mass_g: float) -> dict:
    """Сезонная модель ECSS на трассе этих суток. Отказ модели — названная причина, не ноль."""
    from vkd.assess.seasonal import seasonal_hits_track
    if not states:
        return {'status_ru': 'инерциальные состояния трассы не получены: линия метеороидов не посчитана'}
    try:
        result = seasonal_hits_track([p.t_utc for p in points], [p.alt_km for p in points], states,
                                     area_m2=area_m2, mass_g=mass_g)
    except (ValueError, KeyError) as exc:
        return {'status_ru': 'сезонная модель отказала: %s' % exc}
    top = tuple((c['name'], float(c['expected_hits'])) for c in result.contributions
                if c['expected_hits'] > 0)[:3]
    mean = result.N_mean_background
    return {'N': float(result.N), 'streams': float(result.N_streams),
            'background': float(result.N_sporadic_adjusted), 'annual_mean': float(mean),
            'excess_pct': (100.0 * (result.N / mean - 1.0)) if mean > 0 else None,
            'top': top, 'status_ru': 'сезонная модель ECSS C-2 на фактической трассе суток',
            'model_id': result.provenance.get('model_id')}


def _element_set_epoch(repo_root: str, tle_path: Optional[str]) -> Optional[datetime]:
    """Эпоха набора элементов ДО расчёта: по ней ставится поднятый предел возраста и возраст в строке.

    Читается тем же разбором, что и в модуле орбиты (satellite_from_tle): второго чтения
    набора элементов в проекте нет. Неудача — None, и тогда предел ставится по числу суток.
    """
    from vkd.orbit.trajectory import satellite_from_tle
    path = tle_path or os.path.join(repo_root, 'data', 'orbit', 'iss.tle')
    try:
        with open(path, 'rb') as handle:
            return satellite_from_tle(handle.read()).epoch.utc_datetime()
    except Exception:                    # noqa: BLE001 — отсутствие эпохи не должно ронять перспективу
        return None


def _day_lines(day_start: datetime, key: tuple, *, repo_root: str, tle_path: Optional[str],
               saa_B_threshold_nT: float, max_tle_age_days: float, block_h: int,
               area_m2: float, mass_g: float, with_meteoroids: bool) -> dict:
    """Обе линии этих суток за один проход по трассе, с общим кешем на дату.

    В кеше остаются ТОЛЬКО итоги суток: сами 1441 точка трассы и инерциальные состояния
    выбрасываются сразу после расчёта — иначе перспектива на месяц держала бы в памяти
    сорок тысяч точек ради двух десятков чисел.
    """
    if key in _DAY_CACHE:
        return _DAY_CACHE[key]
    geometry = _day_geometry(day_start, repo_root=repo_root, tle_path=tle_path,
                             saa_B_threshold_nT=saa_B_threshold_nT, max_tle_age_days=max_tle_age_days,
                             block_h=block_h, with_states=with_meteoroids)
    value = {k: geometry[k] for k in ('meta', 'saa_min', 'by_hour', 'unknown_minutes', 'quiet', 'busy')}
    value['mmod'] = (_day_meteoroids(geometry['points'], geometry['provenance'].get('inertial_states'),
                                     area_m2, mass_g) if with_meteoroids else None)
    if len(_DAY_CACHE) >= _CACHE_LIMIT:
        _DAY_CACHE.pop(next(iter(_DAY_CACHE)))
    _DAY_CACHE[key] = value
    return value


# ---------------------------------------------------------------------------
# Космическая погода по суткам
# ---------------------------------------------------------------------------

def _three_day_weather(samples, day_start: datetime) -> dict:
    """Свёртка живого трёхсуточного прогноза NOAA на эти сутки.

    Ячейки НЕ пересчитываются: 3-часовые остаются 3-часовыми и перечислены в строке,
    суточная вероятность остаётся суточной. Наибольший Kp — это максимум по ячейкам,
    целиком попавшим в эти сутки, а не интерполяция и не среднее.
    """
    day_end = day_start + DAY
    kp_cells, probability, sources = [], None, set()
    for s in samples or ():
        if s.value is None or s.valid_from_utc is None or s.valid_to_utc is None:
            continue
        if s.valid_from_utc < day_start or s.valid_to_utc > day_end:
            continue
        if s.channel_id == 'kp_forecast':
            kp_cells.append((s.valid_from_utc, float(s.value)))
            sources.add(s.source_id)
        elif s.channel_id in ('s1_prob_daily', 'proton_prob_daily'):
            # Суточная вероятность протонного события (S1 и выше). Остаётся суточной.
            probability = float(s.value) if probability is None else max(probability, float(s.value))
            sources.add(s.source_id)
    kp_cells.sort()
    return {'kp_cells': tuple(kp_cells), 'kp_max': max((v for _t, v in kp_cells), default=None),
            'proton_prob_pct': probability, 'source_id': sorted(sources)[0] if sources else None,
            'complete': len(kp_cells) == 8}


# ---------------------------------------------------------------------------
# Основной вход
# ---------------------------------------------------------------------------

def build_outlook(start_utc: datetime, days: Optional[int] = None, *,
                  three_day_samples=(), outlook27=None,
                  tle_text: Optional[str] = None, tle_path: Optional[str] = None,
                  repo_root: Optional[str] = None,
                  saa_B_threshold_nT: Optional[float] = None,
                  area_m2: float = 1.0, mass_g: float = 1e-3,
                  duration_min: Optional[int] = None,
                  include_meteoroids: bool = True,
                  now_utc: Optional[datetime] = None) -> OutlookResult:
    """Перспектива по суткам. Сети не трогает: выпуски источников передаёт ВЫЗЫВАЮЩИЙ.

    start_utc        — начало срока (время с поясом). Сутки нарезаются по UTC, потому что
                       по UTC нарезаны и оба прогноза NOAA; первые сутки — те, в которые
                       попадает start_utc.
    days             — сколько суток показать; по умолчанию config [outlook].days (27),
                       предел MAX_DAYS = 120.
    three_day_samples— EnvironmentSample живого трёхсуточного прогноза NOAA
                       (каналы kp_forecast, s1_prob_daily), как их отдаёт
                       vkd.sources.noaa_latest. Пусто — ярус трёх суток объявляется без Kp.
    outlook27        — Outlook27 из vkd.integration.noaa_27day.fetch_27day().
                       None — обзора нет, космопогода кончается на трёх сутках.
    tle_text/tle_path/repo_root — откуда взять набор элементов. tle_text — как в текущем
                       режиме (готовится тем же vkd.integration.orbit_bridge.stage_live_root,
                       второго способа нет); tle_path — прямой путь к файлу; ничего не
                       задано — снимок репозитория data/orbit/iss.tle.
    include_meteoroids — False считает только геометрию и погоду (замер: метеороиды стоят
                       ≈1,2 с на сутки, геометрия ≈0,07 с). Отключение видно в строке.
    """
    from vkd.config import section
    from vkd.windows.compare import Thresholds

    start = _utc(start_utc)
    now = _utc(now_utc) if now_utc is not None else datetime.now(timezone.utc)
    th = Thresholds.from_settings()
    threshold = float(saa_B_threshold_nT if saa_B_threshold_nT is not None else th.saa_B_threshold_nT)
    cfg = section('outlook')
    if days is None:
        days = int(cfg.get('days', 27))
    if type(days) is not int or not 1 <= days <= MAX_DAYS:
        raise ValueError('outlook: число суток — целое в пределах 1…%d' % MAX_DAYS)
    if duration_min is None:
        duration_min = int(section('ui').get('duration_min', 360))
    block_h = max(1, min(24, round(duration_min / 60)))
    minute_limit_days = int(math.floor(th.tle_max_age_days))
    if minute_limit_days < 1:
        raise ValueError('outlook: предел возраста набора элементов меньше суток — поминутного яруса нет')

    first_day = _day_floor(start)
    last_day = first_day + timedelta(days=days)
    if not (SEASONAL_DOMAIN[0] <= first_day.year and last_day.year <= SEASONAL_DOMAIN[1]):
        raise ValueError('outlook: сезонная модель объявлена на %d–%d годы' % SEASONAL_DOMAIN)

    # Набор элементов: тот же приём, что в текущем режиме, — и корень, и проверка байтов чужие.
    root = repo_root or ROOT
    if tle_text:
        from vkd.integration.orbit_bridge import stage_live_root
        root = stage_live_root(tle_text, now, None, '', 'перспектива: тот же набор элементов, что и вердикт')
        tle_path = None
    # Предел возраста поднимается СОЗНАТЕЛЬНО и ровно настолько, насколько нужно этому сроку:
    # иначе модуль орбиты откажет на третьи сутки, и перспективы не будет вовсе. Величина
    # считается от эпохи самого набора элементов, а не от «сегодня»: набор на руках уже не первой
    # свежести, и на дальнюю дату решает расстояние от эпохи, а не длина запроса.
    epoch = _element_set_epoch(root, tle_path)
    if epoch is not None:
        span_days = max(abs((first_day - epoch).total_seconds()),
                        abs((last_day - epoch).total_seconds())) / 86400.0
    else:
        span_days = float(days) + abs((first_day - now).total_seconds()) / 86400.0
    max_age_days = max(th.tle_max_age_days, span_days + 1.0)

    outlook_last_day = None
    if outlook27 is not None and outlook27.status == 'full' and outlook27.coverage_to_utc:
        covered = (outlook27.coverage_to_utc - first_day).total_seconds() / 86400.0
        outlook_last_day = max(0, min(days, int(math.floor(covered))))
    tiers = declared_tiers(minute_limit_days, outlook_last_day)

    rows, orbit_meta, geometry_error = [], None, None
    for index in range(days):
        day_start = first_day + timedelta(days=index)
        geometry, mmod = None, None
        if geometry_error is None:
            key = (root, tle_path, threshold, block_h, day_start.isoformat(),
                   bool(include_meteoroids), area_m2, mass_g)
            try:
                geometry = _day_lines(day_start, key, repo_root=root, tle_path=tle_path,
                                      saa_B_threshold_nT=threshold, max_tle_age_days=max_age_days,
                                      block_h=block_h, area_m2=area_m2, mass_g=mass_g,
                                      with_meteoroids=include_meteoroids)
            except Exception as exc:          # noqa: BLE001 — отказ орбиты не превращается в нули
                geometry_error = str(exc)
        if geometry is not None:
            orbit_meta = orbit_meta or geometry['meta']
            mmod = geometry['mmod']

        day_epoch = getattr(orbit_meta, 'epoch_utc', None) or epoch
        age_days = ((day_start + DAY - day_epoch).total_seconds() / 86400.0) if day_epoch else None
        beyond = bool(age_days is not None and age_days > th.tle_max_age_days)
        measured = not (age_days is not None and age_days > MEASURED_AGE_DAYS)
        minute_geometry = index < minute_limit_days and not beyond

        three_day = _three_day_weather(three_day_samples, day_start)
        far = outlook27.day(day_start) if outlook27 is not None and outlook27.status == 'full' else None

        if index == 0:
            tier_id = TIER_VERDICT
        elif three_day['kp_cells'] and index < minute_limit_days:
            tier_id = TIER_3DAY
        elif far is not None:
            tier_id = TIER_27DAY
        else:
            tier_id = TIER_SEASONAL
        tier = next((t for t in tiers if t.tier_id == tier_id), tiers[-1])

        if three_day['kp_cells'] and tier_id in (TIER_VERDICT, TIER_3DAY):
            kp_max, kp_res, kp_src = three_day['kp_max'], '3h', three_day['source_id']
            proton_pct, proton_known = three_day['proton_prob_pct'], three_day['proton_prob_pct'] is not None
            weather_ru = ('живой трёхсуточный прогноз NOAA: %d 3-часовых ячеек Kp%s'
                          % (len(three_day['kp_cells']),
                             '' if three_day['complete'] else ' — сутки покрыты не целиком'))
        elif far is not None:
            kp_max, kp_res, kp_src = far.kp_max, 'daily_max', outlook27.samples[0].source_id if outlook27.samples else None
            proton_pct, proton_known = None, False
            weather_ru = ('27-суточный обзор NOAA от %s: наибольший Kp за сутки; '
                          % outlook27.published_utc.strftime('%Y-%m-%d %H:%MZ')) + RESOLUTION_RU
        else:
            kp_max, kp_res, kp_src, proton_pct, proton_known = None, None, None, None, False
            weather_ru = ('космическая погода на эти сутки НЕИЗВЕСТНА: '
                          + (('27-суточный обзор кончается %s'
                              % outlook27.coverage_to_utc.strftime('%Y-%m-%d'))
                             if outlook27 is not None and outlook27.coverage_to_utc
                             else 'ни одного выпуска прогноза на руках нет'))

        known, unknown = [], []
        if mmod and 'N' in mmod:
            known.append('метеороиды посчитаны точно: дата задаёт активность потоков однозначно')
        elif include_meteoroids:
            unknown.append('линия метеороидов не посчитана: ' + (mmod or {}).get('status_ru', 'причина не записана'))
        else:
            unknown.append('линия метеороидов не запрашивалась (include_meteoroids=False)')
        if geometry is None:
            unknown.append('трасса не посчитана: %s' % geometry_error)
            geometry_ru, resolution = 'трасса не посчитана', 'none'
        elif minute_geometry:
            geometry_ru = ('трасса поминутно: набор элементов эпохи %s, возраст на конец суток %.1f сут — '
                           'в объявленном пределе %.0f сут'
                           % (day_epoch.strftime('%Y-%m-%d %H:%MZ') if day_epoch else '?', age_days or 0.0,
                              th.tle_max_age_days)).replace('.', ',')
            resolution = 'minute'
            known.append('геометрия ещё поминутная: на эти сутки можно запустить полный расчёт окна')
        else:
            geometry_ru = ('только суточная величина: возраст набора элементов на конец суток %.1f сут '
                           'больше объявленного предела %.0f сут, поминутное обещание снято'
                           % (age_days or 0.0, th.tle_max_age_days)).replace('.', ',')
            resolution = 'daily'
            unknown.append('минуты в аномалии для окна с конкретной минуты начала — не обещаются')
        if geometry is not None and not measured:
            unknown.append('суточная величина за пределом замера: накопление ошибки трассы измерено '
                           'до %.0f сут от эпохи набора элементов, а эти сутки дальше' % MEASURED_AGE_DAYS)
        if proton_known:
            known.append('суточная вероятность протонного события есть и остаётся суточной')
        else:
            unknown.append(PROTON_UNKNOWN_RU if tier_id in (TIER_27DAY, TIER_SEASONAL)
                           else 'суточной вероятности протонного события в этом выпуске нет')
        if tier_id == TIER_SEASONAL:
            unknown.append('космическая погода неизвестна вовсе')

        rows.append(OutlookDay(
            date_utc=day_start, day_index=index, tier_id=tier_id, tier_label_ru=tier.label_ru,
            mmod_hits=(mmod or {}).get('N'), mmod_streams=(mmod or {}).get('streams'),
            mmod_background=(mmod or {}).get('background'), mmod_annual_mean=(mmod or {}).get('annual_mean'),
            mmod_excess_pct=(mmod or {}).get('excess_pct'), mmod_top_streams=(mmod or {}).get('top', ()),
            mmod_status_ru=(mmod or {}).get('status_ru', 'линия метеороидов не запрашивалась'),
            saa_min=(geometry or {}).get('saa_min'), saa_by_hour=(geometry or {}).get('by_hour', ()),
            quiet_hours_utc=(geometry or {}).get('quiet'), busy_hours_utc=(geometry or {}).get('busy'),
            geometry_resolution=resolution, beyond_declared_tle_age=beyond, geometry_measured=measured,
            element_set_age_days=age_days, geometry_status_ru=geometry_ru,
            kp_max=kp_max, kp_source_id=kp_src, kp_resolution=kp_res,
            kp_cells_3h=three_day['kp_cells'] if kp_res == '3h' else (),
            a_index=far.a_index if far is not None and kp_res == 'daily_max' else None,
            f107_sfu=far.f107_sfu if far is not None and kp_res == 'daily_max' else None,
            proton_prob_daily_pct=proton_pct, proton_known=proton_known, weather_status_ru=weather_ru,
            known_ru=tuple(known), unknown_ru=tuple(unknown)))

    limitations = [
        'Перспектива — не вердикт: рекомендованного окна, ранжирования начал и условий проверки '
        'здесь нет. Вердикт остаётся в пределах суток.',
        'Суточные вероятности не пересчитываются в вероятность за окно ни в одном месте модуля.',
        PROTON_UNKNOWN_RU,
        'Геометрия за пределом возраста набора элементов (%.0f сут) — сознательное продолжение того же '
        'набора за объявленной применимостью; наружу отдаётся только суточная величина. Накопление '
        'ошибки измерено до %.0f сут от эпохи набора (замеры А–В в описании модуля); дальше суточная '
        'величина считается тем же кодом, но её воспроизводимость не мерена, и это помечено в строке '
        '(geometry_measured).' % (th.tle_max_age_days, MEASURED_AGE_DAYS),
        'Сезонная модель метеороидов даёт климатологию 49 потоков ECSS C-2: всплеск конкретного года '
        'она не предсказывает, и её собственная неопределённость фона ×0,33…3 никуда не делась.',
        'Манёвры МКС (подъём орбиты, уклонение от мусора) не предсказываются ни одним набором '
        'элементов: после манёвра суточную картину надо пересчитать на свежих элементах.',
    ]
    if geometry_error:
        limitations.append('Трасса посчитана не на все сутки: %s' % geometry_error)
    if outlook27 is None:
        limitations.append('27-суточный обзор NOAA не передан: космопогода кончается на трёх сутках.')
    elif outlook27.status != 'full':
        limitations.append('27-суточный обзор не применён: %s' % outlook27.status_ru)

    orbit = {'method': getattr(orbit_meta, 'method', None), 'source_id': getattr(orbit_meta, 'source_id', None),
             'epoch_utc': getattr(orbit_meta, 'epoch_utc', None) or epoch,
             'declared_max_age_days': th.tle_max_age_days, 'applied_max_age_days': max_age_days,
             'measured_age_days': MEASURED_AGE_DAYS,
             'saa_B_threshold_nT': threshold, 'error': geometry_error,
             'note_ru': 'тот же модуль орбиты A3 и тот же набор элементов, что у вердикта; '
                        'предел возраста поднят только для перспективы и объявлен в каждой строке'}
    weather = {
        'three_day_channels': ('kp_forecast', 's1_prob_daily'),
        'three_day_cells': sum(1 for s in (three_day_samples or ()) if s.kind == Kind.EXTERNAL_FORECAST),
        'outlook27_status': getattr(outlook27, 'status', None),
        'outlook27_published_utc': getattr(outlook27, 'published_utc', None),
        'outlook27_coverage_to_utc': getattr(outlook27, 'coverage_to_utc', None),
        'outlook27_release_id': getattr(outlook27, 'release_id', None),
        'outlook27_raw_record_id': getattr(outlook27, 'raw_record_id', None),
        'proton_forecast_last_day': next((r.day_index for r in reversed(rows) if r.proton_known), None),
    }
    meteoroids = {'model': 'ECSS-E-ST-10-04C Rev.1 (2020), C-2, сезонная инженерная модель',
                  'domain_years': SEASONAL_DOMAIN, 'area_m2': area_m2, 'mass_g': mass_g,
                  'requested': bool(include_meteoroids)}
    boundaries = {'minute_geometry_last_day': minute_limit_days,
                  # Сколько суток ФАКТИЧЕСКИ вышло поминутными: предел считается от эпохи набора
                  # элементов, поэтому на несвежих элементах этот ярус короче объявленного.
                  'minute_geometry_last_day_effective': sum(1 for r in rows if r.geometry_resolution == 'minute'),
                  'three_day_last_day': minute_limit_days,
                  'outlook27_last_day': outlook_last_day,
                  'days': days,
                  'why_minute_limit_ru': tiers[1].boundary_reason_ru}
    return OutlookResult(start, first_day, days, now, tiers, tuple(rows), orbit, weather, meteoroids,
                         tuple(limitations), SCOPE_RU, RULE_RU, boundaries)


def outlook_table(result: OutlookResult) -> tuple:
    """Плоские строки для экрана и выгрузки: по одной на сутки, без второй интерпретации.

    Здесь нет ни одного вычисления сверх того, что уже стоит в OutlookDay: только выбор
    полей и русские подписи. Всё, чего нет, остаётся None и печатается прочерком.
    """
    out = []
    for r in result.rows:
        out.append({
            'date_utc': r.date_utc, 'day_index': r.day_index, 'tier_id': r.tier_id,
            'tier_label_ru': r.tier_label_ru,
            'mmod_hits': r.mmod_hits, 'mmod_streams': r.mmod_streams, 'mmod_excess_pct': r.mmod_excess_pct,
            'mmod_top_streams': r.mmod_top_streams,
            'saa_min': r.saa_min, 'saa_share_pct': r.saa_share_pct,
            'geometry_resolution': r.geometry_resolution, 'geometry_measured': r.geometry_measured,
            'element_set_age_days': r.element_set_age_days,
            'quiet_hours_utc': r.quiet_hours_utc, 'busy_hours_utc': r.busy_hours_utc,
            'kp_max': r.kp_max, 'kp_resolution': r.kp_resolution,
            'a_index': r.a_index, 'f107_sfu': r.f107_sfu,
            'proton_prob_daily_pct': r.proton_prob_daily_pct, 'proton_known': r.proton_known,
            'beyond_declared_tle_age': r.beyond_declared_tle_age,
            'known_ru': r.known_ru, 'unknown_ru': r.unknown_ru,
        })
    return tuple(out)


__all__ = ['MAX_DAYS', 'OutlookDay', 'OutlookResult', 'RULE_RU', 'SCOPE_RU', 'Tier',
           'TIER_27DAY', 'TIER_3DAY', 'TIER_SEASONAL', 'TIER_VERDICT',
           'build_outlook', 'declared_tiers', 'outlook_table']
