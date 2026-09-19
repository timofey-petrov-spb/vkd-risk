# -*- coding: utf-8 -*-
"""Глобус круга 11: рекомендованное окно, аномалия и бегунок времени (app/globe.py).

Что проверяется делом, а не чтением кода:

  * без параметра рекомендованного окна данные компонента не изменились ПОБИТОВО — контрольная
    сумма строки данных снята с кода до правки и записана здесь константой (экран рекомендацию
    ещё не передаёт, и сломать его нельзя);
  * с параметром появляется блок рекомендованного окна, его границы попадают в точки трассы,
    а трасса делится на яркие и приглушённые участки — участки считает сам код компонента,
    исполняемый в node с заглушками вместо WebGL;
  * признак аномалии в данных — та же величина и тот же порог, что в vkd/windows/compare.py:
    |B| точки против saa_B_threshold_nT; сверх того минуты в аномалии по флагам сходятся с
    интегралом, которым их считает сам compare.py, а расхождение признака с порогом даёт отказ;
  * объём данных компонента остаётся в объявленных пределах (не больше чем вдвое от прежнего);
  * в разметке нет ни одного знака вне печатного диапазона, английских слов в видимых подписях
    и ни одного обращения к сети, кроме двух объявленных;
  * бегунок действительно двигает метку и печатает время UTC и |B| — проверяется исполнением
    кода компонента, а не наличием строк в файле.

Трасса и поле здесь — образец нужной ФОРМЫ, а не модель: |B| задан гладкой ямой над Южной
Атлантикой, чтобы у трассы были входы в область и выходы из неё. Физику поля проверяет
tests/test_globe.py (сетка |B| глобуса сверяется там с полем плоской карты по IGRF); здесь
проверяется только перенос уже посчитанного в компонент.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import globe
from vkd.orbit.integration import integrate_time
from vkd.types import Recommendation, Window
from vkd.windows.compare import Thresholds

# ------------------------------------------------------------------ образец трассы и поля

TH = Thresholds.from_settings()          # те же пороги, что берёт app/compute.py
THR_NT = TH.saa_B_threshold_nT           # порог аномалии: одна настройка на весь сервис
T0 = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
ALT_KM = 417.0                           # средняя высота МКС в 2026 году, круглым числом
INC_DEG = 51.6                           # наклонение орбиты МКС
PERIOD_MIN = 92.9                        # период обращения МКС, мин
EARTH_DAY_MIN = 1436.07                  # звёздные сутки, мин: за них трасса уходит на 360° по долготе
# Каркас поля: вдали от аномалии 32 000 нТл, в центре ямы 32 000 − 12 000 = 20 000 нТл.
# Это КАРИКАТУРА, а не модель: она нужна лишь затем, чтобы порог 24 000 нТл пересекался трассой
# и в окне были входы в область. Настоящее поле считает IGRF, см. tests/test_globe.py.
B_FAR_NT, B_DIP_NT = 32000.0, 12000.0
SAA_LON_DEG, SAA_LAT_DEG = -53.0, -26.0          # центр Южно-Атлантической аномалии, примерно
SAA_LON_HALF, SAA_LAT_HALF = 33.0, 17.0          # полуразмеры ямы по долготе и широте, градусы

# Контрольная сумма строки данных компонента БЕЗ рекомендованного окна. Снята с app/globe.py
# в состоянии до круга 11 (ветка wf11/globe, до правки) на этом же образце трассы, окон и поля.
# Меняется — значит прежний экран получит другие данные, чего делать нельзя.
GOLDEN_SHA256 = '90fd886653bab77a631a8f2311ac830a48c9e29fe897229bad88e8678198713d'
GOLDEN_BYTES = 41615

# Предел объёма данных компонента на реальной сетке |B| по IGRF: владелец назвал «не больше чем
# вдвое» от нынешних ~57 КБ на отрисовку. Множитель объявлен в app/globe.REC_MAX_GROWTH.
TRACK_MIN = 1921                         # горизонт сервиса: 1440 мин поиска + 480 мин окна


def field_nT(lat_deg: float, lon_deg: float) -> float:
    """|B| образца: ровный фон и одна гладкая яма над Южной Атлантикой."""
    dx = ((lon_deg - SAA_LON_DEG + 180.0) % 360.0 - 180.0) / SAA_LON_HALF
    dy = (lat_deg - SAA_LAT_DEG) / SAA_LAT_HALF
    return B_FAR_NT - B_DIP_NT * math.exp(-(dx * dx + dy * dy))


def make_traj(n: int = TRACK_MIN):
    """Трасса шага 1 мин: круговая орбита, признак аномалии — ровно |B| ниже порога.

    Признак считается ТЕМ ЖЕ способом, что в vkd/orbit/trajectory.py: in_saa = B_nT < порог.
    """
    pts = []
    for i in range(n):
        arg = 2 * math.pi * i / PERIOD_MIN
        lat = math.degrees(math.asin(math.sin(math.radians(INC_DEG)) * math.sin(arg)))
        lon = math.degrees(math.atan2(math.cos(math.radians(INC_DEG)) * math.sin(arg), math.cos(arg)))
        lon = (lon - i * 360.0 / EARTH_DAY_MIN + 180.0) % 360.0 - 180.0
        b = field_nT(lat, lon)
        pts.append(SimpleNamespace(t_utc=T0 + timedelta(minutes=i), lat_deg=lat, lon_deg=lon,
                                   alt_km=ALT_KM, B_nT=b, in_saa=bool(b < THR_NT)))
    return pts


def make_windows():
    """Два окна-кандидата по 240 мин — столько же, сколько в примере задания круга 11."""
    return [SimpleNamespace(start_utc=T0 + timedelta(minutes=120), duration_min=240),
            SimpleNamespace(start_utc=T0 + timedelta(minutes=600), duration_min=240)]


def make_field(step_deg: float = 10.0) -> dict:
    """Сетка |B| образца в том же виде, что даёт `globe.saa_field`, но без IGRF: тесты этого файла
    о переносе данных, а не о поле, и платить 0,25 с за IGRF в каждом из них незачем.
    Шаг 10° крупнее рабочих 4°: объём сетки здесь роли не играет."""
    nlat = int(round((globe.SAA_LAT_MAX - globe.SAA_LAT_MIN) / step_deg)) + 1
    nlon = int(round(360.0 / step_deg)) + 1
    lats = [globe.SAA_LAT_MIN + step_deg * k for k in range(nlat)]
    lons = [-180.0 + step_deg * k for k in range(nlon)]
    return {'lat0': lats[0], 'lon0': lons[0], 'step': step_deg,
            'nlat': nlat, 'nlon': nlon, 'thr': THR_NT,
            'B': [int(round(field_nT(la, lo))) for la in lats for lo in lons]}


REC = (T0 + timedelta(minutes=120), T0 + timedelta(minutes=360))   # рекомендованное окно, 240 мин


@pytest.fixture(scope='module')
def traj():
    return make_traj()


@pytest.fixture(scope='module')
def field():
    return make_field()


@pytest.fixture(scope='module')
def plain(traj, field):
    return globe.globe_payload(traj, make_windows(), THR_NT, T0, field=field)


@pytest.fixture(scope='module')
def with_rec(traj, field):
    return globe.globe_payload(traj, make_windows(), THR_NT, T0, field=field, recommended=REC)


# ------------------------------------------------------------------ 1. прежний экран не сломан

def test_payload_without_recommendation_is_byte_identical(plain):
    """Без параметра рекомендованного окна строка данных компонента побитово прежняя.

    Сверяется контрольная сумма всей строки, а не отдельные поля: любое изменение порядка
    ключей, округления или значения сдвинуло бы её. Экран круга 10 рекомендацию не передаёт.
    """
    line = globe._dumps(plain)
    assert hashlib.sha256(line.encode('utf-8')).hexdigest() == GOLDEN_SHA256
    assert len(line.encode('utf-8')) == GOLDEN_BYTES == plain['bytes']
    assert 'rec' not in plain, 'без параметра блока рекомендации быть не должно'


def test_default_argument_is_the_same_as_none(traj, field):
    """Явный None и отсутствие параметра — одно и то же: у вызова с экрана нет второго поведения."""
    a = globe.globe_payload(traj, make_windows(), THR_NT, T0, field=field)
    b = globe.globe_payload(traj, make_windows(), THR_NT, T0, field=field, recommended=None)
    assert globe._dumps(a) == globe._dumps(b)


def test_recommendation_adds_only_its_own_block(plain, with_rec):
    """Рекомендация добавляет ровно один ключ и ничего не переписывает: трасса, окна и сетка те же."""
    assert set(with_rec) - set(plain) == {'rec'}
    assert set(plain) - set(with_rec) == set()
    for key in ('t0', 'alt_km', 'step_min', 'n_full', 'n_shown', 'track', 'windows', 'saa',
                'thr_nT', 'texture', 'colors'):
        assert with_rec[key] == plain[key], 'ключ %s не должен меняться от рекомендации' % key


# ------------------------------------------------------------------ 2. блок рекомендованного окна

def test_recommended_block_is_anchored_to_track_points(with_rec):
    """Границы окна — настоящие точки трассы, а не ближайшие узлы: на них стоят метки."""
    rec, tr = with_rec['rec'], with_rec['track']
    assert (rec['a'], rec['b']) == (120, 360)
    assert tr['min'][rec['i0']] == rec['a'] and tr['min'][rec['i1']] == rec['b']
    assert rec['i1'] > rec['i0']
    assert len(rec['B']) == rec['i1'] - rec['i0'] + 1
    assert rec['start'] == '19.09 14:00' and rec['end'] == '19.09 18:00'
    assert rec['t0_iso'] == '2026-09-19T12:00:00Z'
    assert rec['color'] == globe.BLUE, 'рекомендация — наш расчёт, цвет тот же'


def test_recommended_field_values_are_the_track_field(traj, with_rec):
    """|B| бегунка — поле тех же точек трассы, округлённое до целых нТл, без подстановок."""
    rec, tr = with_rec['rec'], with_rec['track']
    for j, value in enumerate(rec['B']):
        minute = tr['min'][rec['i0'] + j]
        assert value == int(round(traj[minute].B_nT))
    assert min(v for v in rec['B'] if v is not None) < THR_NT, 'окно образца должно задевать аномалию'


def test_missing_field_value_stays_unknown(field):
    """Нет |B| в точке — в данных None, а не подставленный ноль (никаких молчаливых заглушек)."""
    pts = make_traj(400)
    pts[200] = SimpleNamespace(t_utc=pts[200].t_utc, lat_deg=pts[200].lat_deg, lon_deg=pts[200].lon_deg,
                               alt_km=ALT_KM, B_nT=None, in_saa=None)
    p = globe.globe_payload(pts, make_windows()[:1], THR_NT, T0, field=field,
                            recommended=(T0 + timedelta(minutes=120), T0 + timedelta(minutes=360)))
    assert p['rec']['B'][200 - p['rec']['a']] is None


def test_window_object_is_accepted(traj, field, with_rec):
    """Рекомендация принимается и в виде самого окна (start_utc + duration_min) — так её отдаёт
    vkd/windows, и разбирать её на стороне экрана незачем.

    Проверяется настоящий тип проекта, а не похожий предмет: `Recommendation.preferred` —
    это `vkd.types.Window`, и экрану достаточно передать его как есть.
    """
    p = globe.globe_payload(traj, make_windows(), THR_NT, T0, field=field,
                            recommended=Window(start_utc=REC[0], duration_min=240))
    assert p['rec'] == with_rec['rec']
    assert set(Window.__dataclass_fields__) == {'start_utc', 'duration_min'}, \
        'разбор границ опирается на эти два поля окна; изменится тип — надо пересмотреть'
    assert 'preferred' in Recommendation.__dataclass_fields__, \
        'экран берёт рекомендованное окно из Recommendation.preferred'


@pytest.mark.parametrize('bad, part', [
    ((T0 + timedelta(minutes=360), T0 + timedelta(minutes=120)), 'не позже начала'),
    ((T0 - timedelta(minutes=10), T0 + timedelta(minutes=120)), 'выходит за трассу'),
    ((T0 + timedelta(minutes=1800), T0 + timedelta(minutes=2400)), 'выходит за трассу'),
    ((T0.replace(tzinfo=None), T0 + timedelta(minutes=120)), 'без часового пояса'),
    ('19.09 14:00', 'ожидалась пара'),
    ((T0, 'полдень'), 'моментом времени'),
])
def test_bad_recommendation_is_refused(traj, field, bad, part):
    """Негодное окно — отказ с причиной по-русски, а не молчаливая подрезка к трассе."""
    with pytest.raises(ValueError) as e:
        globe.globe_payload(traj, make_windows(), THR_NT, T0, field=field, recommended=bad)
    assert part in str(e.value)


def test_thinning_keeps_recommended_bounds(traj, field):
    """При жёстком пределе объёма трасса прореживается, но границы рекомендованного окна остаются
    точками трассы: иначе метки начала и конца уехали бы на соседнюю минуту."""
    p = globe.globe_payload(traj, make_windows(), THR_NT, T0, field=field, recommended=REC, max_bytes=1)
    assert p['step_min'] == globe.THIN_STEPS[-1]
    assert p['track']['min'][p['rec']['i0']] == 120 and p['track']['min'][p['rec']['i1']] == 360
    assert len(p['rec']['B']) == p['rec']['i1'] - p['rec']['i0'] + 1


# ------------------------------------------------------------------ 3. признак аномалии — тот же

def test_anomaly_flag_is_the_criterion_of_compare_py(traj, with_rec):
    """Признак аномалии в данных компонента — ровно та величина и тот порог, по которым
    аномалию считает vkd/windows/compare.py: |B| точки против saa_B_threshold_nT."""
    tr = with_rec['track']
    assert with_rec['thr_nT'] == THR_NT == TH.saa_B_threshold_nT
    for minute, flag in zip(tr['min'], tr['saa']):
        assert bool(flag) == bool(traj[minute].B_nT < TH.saa_B_threshold_nT)
    assert any(tr['saa']) and not all(tr['saa']), 'образец должен и входить в область, и выходить'


def test_mismatched_threshold_is_refused(traj, field):
    """Трасса посчитана по одному порогу, а контур области рисуют по другому — отказ.

    Так было бы, если экран передал глобусу не тот порог, которым строил орбиту: красные отрезки
    трассы и красная область на поверхности разошлись бы, и картинка соврала бы ровно там, где
    показывает, где набирается доза. Подгонять одно к другому нельзя.
    """
    pts = make_traj(400)
    k = next(i for i, p in enumerate(pts) if p.in_saa and 120 <= i <= 360)
    pts[k] = SimpleNamespace(t_utc=pts[k].t_utc, lat_deg=pts[k].lat_deg, lon_deg=pts[k].lon_deg,
                             alt_km=ALT_KM, B_nT=pts[k].B_nT, in_saa=False)
    with pytest.raises(ValueError) as e:
        globe.globe_payload(pts, make_windows()[:1], THR_NT, T0, field=field,
                            recommended=(T0 + timedelta(minutes=120), T0 + timedelta(minutes=360)))
    assert 'по разным порогам' in str(e.value)


def test_anomaly_minutes_agree_with_compare_integral(traj, with_rec):
    """Минуты в аномалии, посчитанные по флагам компонента, сходятся с интегралом, которым их
    считает compare.py (integrate_time с тем же порогом). Полного равенства тут быть не может:
    compare.py разрешает доли минуты линейным пересечением порога, а флаг стоит на узле. Поэтому
    предел расхождения объявлен: не больше половины минуты на каждое пересечение порога в окне."""
    rec = with_rec['rec']
    start = T0 + timedelta(minutes=rec['a'])
    end = T0 + timedelta(minutes=rec['b'])
    inside = [p for p in traj if start <= p.t_utc <= end]
    result = integrate_time([p.t_utc for p in inside], [p.B_nT for p in inside], start, end,
                            max_gap_seconds=60.0, threshold=TH.saa_B_threshold_nT)
    exact_min = result.below_threshold_seconds / 60.0
    flags = with_rec['track']['saa'][rec['i0']:rec['i1'] + 1]
    by_flags = float(sum(flags))
    crossings = sum(1 for a, b in zip(flags, flags[1:]) if a != b)
    assert abs(by_flags - exact_min) <= 0.5 * crossings + 1e-9, \
        'по флагам %.3f мин, интегралом %.3f мин, пересечений %d' % (by_flags, exact_min, crossings)


# ------------------------------------------------------------------ 4. объём данных

def test_payload_growth_is_within_declared_limit(plain, with_rec):
    """Рекомендация не раздувает данные компонента: объявленный предел — не больше чем вдвое."""
    assert with_rec['bytes'] > plain['bytes'], 'блок рекомендации должен что-то добавлять'
    assert with_rec['bytes'] <= globe.REC_MAX_GROWTH * plain['bytes']


@pytest.mark.parametrize('duration_min', [60, 240, 480])
def test_payload_on_real_igrf_grid_stays_in_limits(traj, duration_min):
    """На рабочей сетке |B| по IGRF (шаг 4°, как на экране) данные с рекомендацией остаются в
    пределах: не больше чем вдвое от данных без неё и не больше общего предела MAX_JSON_BYTES.
    Проверяются окна разной длины, вплоть до предельных 480 мин: длиннее окно — длиннее массив |B|."""
    fld = globe.saa_field(ALT_KM, THR_NT, T0)
    wins = make_windows()
    base = globe.globe_payload(traj, wins, THR_NT, T0, field=fld)
    p = globe.globe_payload(traj, wins, THR_NT, T0, field=fld,
                            recommended=(T0 + timedelta(minutes=120),
                                         T0 + timedelta(minutes=120 + duration_min)))
    assert p['bytes'] <= globe.REC_MAX_GROWTH * base['bytes'], \
        'было %d Б, стало %d Б' % (base['bytes'], p['bytes'])
    assert p['bytes'] <= globe.MAX_JSON_BYTES
    assert p['step_min'] == 1, 'на рабочей сетке прореживание включаться не должно'


def test_declared_weight_is_the_weight_of_the_sent_line(with_rec):
    """Объявленный вес — вес той самой строки, которая уйдёт в компонент, и с блоком рекомендации тоже."""
    line = globe._dumps(with_rec)
    assert with_rec['bytes'] == len(line.encode('utf-8'))
    assert json.loads(line.replace('<\\/', '</')) == with_rec


# ------------------------------------------------------------------ 5. разметка

def _unprintable(text: str) -> set:
    """Знаки вне печатного диапазона: управляющие, неразрывные пробелы и прочее невидимое.
    Перевод строки и табуляция — разметка, а не текст, и считаются допустимыми."""
    return {c for c in text if not c.isprintable() and c not in '\n\r\t'}


@pytest.mark.parametrize('rec', [None, REC])
def test_html_has_no_unprintable_characters(traj, field, rec):
    """Ни одного знака вне печатного диапазона в разметке — ни с рекомендацией, ни без неё.
    Сюда же попадёт узкий неразрывный пробел, если его занесут в подписи компонента."""
    doc = globe.globe_html(globe.globe_payload(traj, make_windows(), THR_NT, T0,
                                               field=field, recommended=rec))
    assert _unprintable(doc) == set()


# Имена, которым нет русской формы: внешняя библиотека, стандарт браузера, заголовок протокола,
# источник текстуры и модель поля. Всё остальное латиницей в видимой подписи — ошибка.
ALLOWED_LATIN = ('UTC', 'WebGL', 'CORS', 'NASA', 'three', 'js', 'cdnjs', 'IGRF', 'Blue', 'Marble')


def test_no_latin_words_in_visible_captions():
    """В видимых подписях компонента нет английских слов и идентификаторов кода (раздел 4 ТЗ).

    Берутся строковые литералы кода компонента и текст между тегами разметки — ровно то, что
    человек прочтёт на панелях. Стили и атрибуты разметки видимым текстом не считаются и в
    проверку не попадают, поэтому разметка и код разбираются порознь.
    """
    html = globe._TEMPLATE.split('<body>', 1)[1].split('<script', 1)[0]
    code = globe._TEMPLATE.rsplit('<script>', 1)[1].split('</script>', 1)[0]
    visible = re.findall(r'"([^"\n]*[А-Яа-яЁё][^"\n]*)"', code)
    visible += re.findall(r'>([^<>{}]*[А-Яа-яЁё][^<>{}]*)<', html)
    visible += re.findall(r'aria-label="([^"]*)"', globe._TEMPLATE)
    assert len(visible) > 20, 'русские подписи в шаблоне должны быть, найдено %d' % len(visible)
    for text in visible:
        # Теги внутри строки — разметка панели, а не читаемый текст: человек их не видит.
        words = re.sub(r'<[^<>]*>', ' ', text)
        latin = [w for w in re.findall(r'[A-Za-z]{2,}', words) if w not in ALLOWED_LATIN]
        assert not latin, 'в подписи «%s» осталось английское слово: %s' % (text, latin)


def test_template_is_a_raw_string():
    """Шаблон компонента — сырая строка (r\"\"\"…\"\"\"). Сделают обычной — последовательности вида
    \\25B8 в стилях Python съест как восьмеричные, и на экране появится мусор; такое в проекте
    уже было. Проверка прямая: разобранная константа совпадает с текстом литерала в файле знак
    в знак, то есть Python из него ничего не съел."""
    src = open(globe.__file__, encoding='utf-8').read()
    assert '_TEMPLATE = r"""' in src, 'шаблон компонента обязан быть сырой строкой'
    literal = src.split('_TEMPLATE = r"""', 1)[1].rsplit('"""', 1)[0]
    assert literal == globe._TEMPLATE, 'шаблон разобран не как сырая строка'
    assert _unprintable(globe._TEMPLATE) == set()


def test_html_declares_slider_marks_and_refusals(traj, field):
    """Документ несёт бегунок, подписи меток и прежние честные отказы."""
    doc = globe.globe_html(globe.globe_payload(traj, make_windows(), THR_NT, T0,
                                               field=field, recommended=REC))
    for part in ('Время в окне', 'type="range"', 'начало окна', 'конец окна', 'нТл',
                 'в аномалии', 'рекомендованное окно'):
        assert part in doc, 'в документе нет «%s»' % part
    for part in ('three.js не загрузилась', 'WebGL', 'текстура Земли не загрузилась',
                 'показать плоскую карту', 'vLon', 'vLat', 'DAMP', 'V_MAX', 'V_STOP',
                 'touchstart', 'touchmove'):
        assert part in doc, 'потеряно прежнее свойство компонента: «%s»' % part


def test_slider_does_not_talk_to_the_server(traj, field):
    """Бегунок работает целиком в браузере: в документе нет ни одного обращения к сети, кроме
    двух заранее объявленных — библиотеки three.js и текстуры Земли. Никакого пересчёта на
    сервере при движении бегунка быть не может, потому что запросить его нечем."""
    doc = globe.globe_html(globe.globe_payload(traj, make_windows(), THR_NT, T0,
                                               field=field, recommended=REC))
    for call in ('fetch(', 'XMLHttpRequest', 'WebSocket', 'EventSource', 'navigator.sendBeacon',
                 'postMessage', 'import('):
        assert call not in doc, 'в компоненте появилось обращение к сети: %s' % call
    urls = set(re.findall(r'https?://[^"\'\s<>]+', doc))
    assert urls == {globe.THREE_JS, globe.TEXTURE_MAIN['url'], globe.TEXTURE_FALLBACK['url']}, \
        'посторонние адреса в документе: %s' % (urls - {globe.THREE_JS, globe.TEXTURE_MAIN['url'],
                                                        globe.TEXTURE_FALLBACK['url']})


def test_caption_declares_grid_and_recommendation(plain, with_rec):
    """Подпись называет сетку контура аномалии и, при рекомендации, само окно и бегунок."""
    assert 'сетке 10°' in globe.caption(plain)
    assert not re.search(r'\d\.\d', globe.caption(plain)), 'дроби печатаются с запятой'
    text = globe.caption(with_rec)
    assert '19.09 14:00 — 19.09 18:00 UTC' in text and 'Бегунок' in text
    assert 'приглушена' in text
    # Точки в «19.09» — разделители даты, а не дробь; дробей в подписи с рекомендацией быть
    # не должно: всё остальное в ней — целые и слова.
    assert not re.search(r'\d\.\d', re.sub(r'\d\d\.\d\d ', '', text)), 'дроби печатаются с запятой'
    line = globe.tech_line(with_rec)
    assert 'точек в окне' in line and 'известным |B|' in line


# ------------------------------------------------------------------ 6. код компонента исполняется

# Заглушки браузера и three.js: код компонента исполняется по-настоящему, но без WebGL и без сети.
# Ничего не изображается — проверяется, что функции доходят до конца и пишут в панели то, что надо.
_STUB_JS = r"""
var HANDLERS = {}, ELS = {}, ADDED = 0;
function el(id) {
  return {id: id, style: {}, innerHTML: "", textContent: "", value: "0", min: "0", max: "1",
          clientWidth: 900, clientHeight: 560,
          addEventListener: function (n, f) { HANDLERS[id + ":" + n] = f; },
          appendChild: function () {}};
}
function ctx2d() {
  return {font: "", fillStyle: "", strokeStyle: "", lineWidth: 0, textAlign: "", textBaseline: "",
          measureText: function (s) { return {width: s.length * 7.5}; },
          scale: function () {}, beginPath: function () {}, rect: function () {},
          fill: function () {}, stroke: function () {}, fillText: function () {}};
}
var document = {
  getElementById: function (id) { if (!ELS[id]) { ELS[id] = el(id); } return ELS[id]; },
  createElement: function () { return {width: 0, height: 0, getContext: function () { return ctx2d(); }}; }
};
var window = {addEventListener: function () {}, devicePixelRatio: 1};
var performance = {now: function () { return 0; }};
function requestAnimationFrame() {}          /* один кадр: иначе animate() зациклится */
function V3(x, y, z) { this.x = x; this.y = y; this.z = z; }
V3.prototype.copy = function (v) { this.x = v.x; this.y = v.y; this.z = v.z; return this; };
function Obj() { this.position = new V3(0, 0, 0); this.material = null;
                 this.scale = {set: function () {}}; this.add = function () {}; }
var THREE = {
  Vector3: V3,
  Scene: function () { this.add = function () { ADDED++; }; },
  Group: function () { return new Obj(); },
  PerspectiveCamera: function () { this.position = {set: function () {}}; this.lookAt = function () {};
                                   this.updateProjectionMatrix = function () {}; },
  WebGLRenderer: function () {
    this.domElement = el("canvas");
    this.capabilities = {getMaxAnisotropy: function () { return 8; }};
    this.getContext = function () { return {MAX_TEXTURE_SIZE: 1, getParameter: function () { return 8192; }}; };
    this.setPixelRatio = function () {}; this.setClearColor = function () {};
    this.setSize = function () {}; this.render = function () {};
  },
  BufferGeometry: function () { this.setAttribute = function () {}; this.setIndex = function () {};
                                this.setFromPoints = function () { return this; }; },
  Float32BufferAttribute: function () {}, SphereGeometry: function () {},
  MeshBasicMaterial: function () {}, LineBasicMaterial: function () {}, PointsMaterial: function () {},
  SpriteMaterial: function () {}, CanvasTexture: function () {},
  Mesh: function () { return new Obj(); }, Line: function () { return new Obj(); },
  LineSegments: function () { return new Obj(); }, Points: function () { return new Obj(); },
  Sprite: function () { return new Obj(); },
  DoubleSide: 2, BackSide: 1,
  TextureLoader: function () {
    this.setCrossOrigin = function () {};
    /* Сети в узле нет: сразу ветка отказа по текстуре — она тоже должна отработать. */
    this.load = function (url, ok, prog, err) { err(new Error("нет сети")); };
  }
};
"""

# Хвост: снимает со сцены то, что видно человеку, и дёргает бегунок как рука пользователя.
_PROBE_JS = r"""
var g = function (id) { return document.getElementById(id); };
var out = {legend: g("legend").innerHTML, stat: g("stat").innerHTML,
           tl: g("tl").style.display || "", err: g("err").style.display || "",
           added: ADDED, ops: {}, colors: {}, marks: [], readouts: []};
var k, kd;
for (k = 0; k < D.track.min.length; k++) {
  kd = kindOf(k);
  out.ops[String(kd.op)] = (out.ops[String(kd.op)] || 0) + 1;
  out.colors[String(kd.c)] = (out.colors[String(kd.c)] || 0) + 1;
}
if (D.rec) {
  var last = D.rec.i1 - D.rec.i0, js = [0, Math.floor(last / 2), last], q, saaAt = -1;
  out.slider_max = g("tlr").max;
  for (q = D.rec.i0; q <= D.rec.i1; q++) { if (D.track.saa[q]) { saaAt = q - D.rec.i0; break; } }
  if (saaAt >= 0) { js.push(saaAt); }
  for (q = 0; q < js.length; q++) {
    g("tlr").value = String(js[q]);
    HANDLERS["tlr:input"]();
    out.readouts.push(g("tlv").innerHTML);
    out.marks.push([recMark.position.x, recMark.position.y, recMark.position.z]);
  }
}
console.log(JSON.stringify(out));
"""


def _run_component(payload: dict, tmp_path) -> dict:
    """Исполнить код компонента в node с заглушками и вернуть то, что он написал в панели."""
    node = shutil.which('node')
    if not node:
        pytest.skip('node не установлен: код компонента не исполнялся')
    doc = globe.globe_html(payload)
    blocks = re.findall(r'<script(?![^>]*src=)[^>]*>(.*?)</script>', doc, re.S)
    assert len(blocks) == 2, 'в документе ровно два своих скрипта: данные и код'
    path = tmp_path / 'run.js'
    path.write_text(_STUB_JS + '\n' + blocks[0] + '\n' + blocks[1] + '\n' + _PROBE_JS, encoding='utf-8')
    r = subprocess.run([node, str(path)], capture_output=True, text=True, encoding='utf-8')
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _ops_from_template() -> dict:
    """Яркости линий трассы — из самого компонента, а не числами в тексте проверки.

    Прежде числа стояли здесь литералами, и первая же правка оформления роняла проверку,
    которая на самом деле про другое: про то, что ярких и приглушённых участков ровно столько,
    сколько их должно быть. Значения берутся из объявления в шаблоне.
    """
    m = re.search(r'var OP_REC = ([\d.]+), OP_DIM = ([\d.]+), OP_BG_DIM = ([\d.]+), OP_BG = ([\d.]+);',
                  globe._TEMPLATE)
    assert m, 'в компоненте объявлены яркости OP_REC/OP_DIM/OP_BG_DIM/OP_BG'
    names = ('OP_REC', 'OP_DIM', 'OP_BG_DIM', 'OP_BG')
    ops = {n: m.group(i + 1) for i, n in enumerate(names)}
    assert ops['OP_REC'] == '1.0', 'рекомендованное окно идёт в полную силу'
    val = {n: float(v) for n, v in ops.items()}
    assert val['OP_BG_DIM'] < val['OP_DIM'] < val['OP_REC'] and val['OP_BG'] < val['OP_REC'], val
    # Ключи снимка приходят из JSON.stringify, а JavaScript печатает число короче, чем оно
    # записано в исходнике: 1.0 как «1», 0.10 как «0.1». Приводим так же, иначе проверка
    # промахнётся мимо своих же яркостей и скажет, что участков нет.
    return {n: ('%g' % float(v)) for n, v in ops.items()}


def test_component_without_recommendation_keeps_previous_look(traj, field, tmp_path):
    """Без рекомендации компонент работает как прежде: панели бегунка нет, трасса яркая,
    приглушённым идёт только фон горизонта."""
    ops = _ops_from_template()
    out = _run_component(globe.globe_payload(traj, make_windows(), THR_NT, T0, field=field), tmp_path)
    assert out['err'] == '', 'отказ не объявлялся — сцена построилась'
    assert out['tl'] == '', 'без рекомендации бегунка на экране быть не должно'
    assert sorted(out['ops']) == sorted({ops['OP_BG'], ops['OP_REC']}),         'прежние две яркости: фон и всё остальное — %r' % out['ops']
    assert out['added'] > 10, 'на сцену что-то добавлено'
    assert 'бегунок' not in out['stat']


def test_component_highlights_the_recommended_window(traj, field, tmp_path):
    """С рекомендацией появляются выделенные участки: часть трассы идёт ярко, остальная приглушена.
    Яркости считает сам код компонента на всех точках трассы, а не тест по своей формуле."""
    ops = _ops_from_template()
    out = _run_component(globe.globe_payload(traj, make_windows(), THR_NT, T0,
                                             field=field, recommended=REC), tmp_path)
    assert out['err'] == ''
    bright = out['ops'].get(ops['OP_REC'], 0)
    dim = out['ops'].get(ops['OP_DIM'], 0) + out['ops'].get(ops['OP_BG_DIM'], 0)
    assert bright > 0 and dim > 0, 'должны быть и яркие, и приглушённые участки: %r' % out['ops']
    assert bright == 240 + 1, 'ярко идёт ровно рекомендованное окно, 240 мин по точкам шага 1 мин'
    assert bright + dim == TRACK_MIN
    # Красный остаётся цветом аномалии и внутри окна, и вне его, а синий — цветом рекомендации.
    # Оттенки берутся тёмные: сцена тёмная, и цвета величин в ней те же, что даёт графикам
    # app/ui.dark_figure. Строка данных при этом прежняя — её проверяет контрольная сумма выше.
    assert str(0xc17d68) in out['colors'] and str(0xa1bed5) in out['colors'], out['colors']
    # Легенда читается за секунду: подпись в два-три слова на строку, без времён и порогов —
    # времена стоят подписанными метками на самой трассе, пороги — в подписи под глобусом.
    for text in ('рекомендованное окно', 'аномалия в окне', 'аномалия вне окна', 'область аномалии'):
        assert text in out['legend'], out['legend']
    assert 'окно 1 · 19.09 14:00' in out['legend'], out['legend']
    assert '19.09 14:00 — 19.09 18:00' not in out['legend'], 'времена окна в легенде не повторяем'


def test_component_slider_moves_the_mark_and_prints_time_and_field(traj, field, tmp_path):
    """Бегунок двигает метку по трассе, а рядом стоит время UTC и |B| в нТл — проверяется
    исполнением: значения снимаются с панели после того, как бегунок дёрнули."""
    out = _run_component(globe.globe_payload(traj, make_windows(), THR_NT, T0,
                                             field=field, recommended=REC), tmp_path)
    assert out['tl'] == 'block' and out['slider_max'] == '240'
    first, middle, last = out['readouts'][0], out['readouts'][1], out['readouts'][2]
    assert first.startswith('19.09 14:00 UTC'), first
    assert middle.startswith('19.09 16:00 UTC'), middle
    assert last.startswith('19.09 18:00 UTC'), last
    for text in (first, middle, last):
        assert 'нТл' in text and '|B|' in text
        assert _unprintable(text) == set(), 'в подписи бегунка знак вне печатного диапазона'
        assert re.search(r'\d{2} \d{3} нТл', text), 'разряды тысяч отбиты обычным пробелом: %s' % text
    # метка действительно едет: три положения на сфере различны
    assert out['marks'][0] != out['marks'][1] != out['marks'][2]
    assert len(out['readouts']) == 4, 'в окне образца есть точка в аномалии'
    assert out['readouts'][3].endswith('· в аномалии'), out['readouts'][3]
