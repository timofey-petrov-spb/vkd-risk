# -*- coding: utf-8 -*-
"""Перебор начал окна на горизонте поиска (ТЗ круга 11, раздел 1).

Задача сервиса поставлена наоборот привычному: человек говорит, сколько ему нужно быть снаружи
и в какой срок надо выйти, а сервис перебирает ВСЕ возможные начала на горизонте и называет
лучшие. Этот модуль и есть перебор.

ОТКУДА ВЗЯТ СПОСОБ СЧЁТА. Второго способа счёта здесь нет — и это главное требование к модулю.
Путь «в лоб», то есть звать полную оценку окна на каждого кандидата, измерен и не годится:
на разборе 03.05.2024 (длительность 360 мин, период поиска 720 мин, Windows 11, Python 3.14.6)
одно окно сверх стоит 0,067 с, а 73 начала — около 4,9 с сверх базы, тогда как весь экран
сейчас считается за доли секунды.

Быстрый путь опирается на то, что для РАНЖИРОВАНИЯ различают окна только две величины —
минуты в аномалии и флюенс захваченных протонов, — и обе получаются из одной и той же функции
`vkd.orbit.integration.integrate_time` по поточечным массивам:

  * поточечные массивы (|B| и поток по таблицам ОСТ в каждой точке трассы) не зависят от окна,
    поэтому считаются ОДИН раз на весь горизонт;
  * на каждого кандидата приходится две нарезки по `bisect` и два вызова `integrate_time`
    с другими границами. Функция сама обрезает интеграл по границам окна, линейно
    интерполирует концы и не заполняет разрывы больше 60 с — то есть кандидат идёт по тому же
    коду и по тем же правилам, что и окно в `assess_window`.

Нарезка делается ТЕМИ ЖЕ выражениями `bisect`, что и в `assess_window`, и на вход
`integrate_time` попадают те же самые значения, поэтому совпадение с поокновым расчётом верно
ПО ПОСТРОЕНИЮ, а не подогнано: расхождение строго нулевое, а не в пределах эпсилона. Это
проверяется на сетке начал (`tests/test_scan.py`); если проверка упадёт, значит перебор считает
не то, и показывать его рядом с карточками окон нельзя.

Условия проверки у кандидата считает та же `vkd.windows.compare.window_conditions`, что и
полная оценка окна: правило «кандидат с условием не может стоять выше кандидата без условий»
обязано опираться на те же условия, которые потом стоят в карточке.

Префиксных сумм здесь нет намеренно: они дали бы третий способ счёта, и их пришлось бы
доказывать отдельно.

ПОЛНАЯ оценка окна (заметки, покрытие всех каналов, карточки факторов, объявленная область)
считается только для тех кандидатов, которые действительно показываются: рекомендованного и
строк таблицы лучших. Это единицы вызовов по 0,067 с, а не семьдесят три.

ПРАВИЛО ВЫБОРА ЗДЕСЬ НЕ ИЗОБРЕТАЕТСЯ. Применяется правило (8) методики — то же, по которому
сравниваются два-три окна: сравнение по доминированию двух величин с их допусками
(`vkd.windows.compare.pair_not_worse` / `pair_better`). Второго правила выбора окна в проекте
нет; если бы оно появилось, на одном экране стояли бы два разных ответа на один вопрос.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional, Sequence

from vkd.assess.meteoroids import meteoroid_hits_track
from vkd.assess.trapped import BeltTable
from vkd.explain.format import fmt_ru
from vkd.orbit.integration import integrate_time
from vkd.types import TrajectoryPoint, Window, WindowAssessment
from vkd.windows.compare import (Thresholds, _cov, _min_cov, _short, declared_scope,
                                 pair_better, pair_not_worse, window_conditions)

# Разрыв трассы, больше которого интеграл не заполняется. Та же величина, что в оценке окна
# (`assess_window`): при шаге трассы 60 с это ровно один пропущенный шаг. Держать её в двух
# местах разными числами нельзя — тогда перебор и карточка окна посчитают разное.
MAX_GAP_SECONDS = 60.0

# Умолчание шага перебора. Настоящее значение приходит из config/settings.toml ([ui].scan_step_min);
# здесь оно стоит только на случай вызова без настроек и совпадает с записанным в файле.
STEP_MIN_DEFAULT = 10

RANK_RULE_RU = (
    'Применяется правило (8) методики — то же, которым сравниваются окна: m — минуты в аномалии, Φ — флюенс '
    'за окно; «не хуже» требует обеих величин, «лучше» — строгого выигрыша хотя бы по одной сверх '
    'допуска. Начало попадает в лучшую группу, если ни одно другое его не превосходит по этому '
    'правилу. Кандидат, у которого стоит условие проверки, из автоматического выбора исключается и '
    'выше кандидата без условий не ставится. Если в лучшей группе одно начало — оно и есть '
    'рекомендация; если несколько и они равнозначны в допуске — перечисляются все; если несколько и '
    'они спорят (одно лучше по минутам, другое по флюенсу сверх допуска) — это компромисс без '
    'победителя, и обе стороны названы числами. Внутри группы порядок — по времени начала: выбирать '
    'внутри неё правило не имеет права. Складывать флюенс с минутами в аномалии нельзя: это разные '
    'величины, и никакого суммарного балла воздействия у сервиса нет.')

# Предел числа перебранных начал. Сравнение по доминированию — попарное, и при мелком шаге оно
# начинает стоить дороже самого перебора. Предел объявляется отказом с названной причиной, а не
# молчаливым огрублением шага: подменять запрос человека сервис не имеет права.
# 300 начал — это шаг 5 мин на сутки поиска. Замер разбиения на слои (та же машина, что и
# остальные замеры круга; худшая расстановка — цепочка, где каждый слой из одного кандидата):
# 73 начала — 0,004 с, 145 — 0,029 с, 300 — 0,237 с; дальше рост квадратичный, и при 1441 начале
# (шаг 1 мин на сутки) перебор перестал бы укладываться в отрисовку экрана.
MAX_CANDIDATES = 300

# Чего НЕ означает покрытие кандидата. Каналы, одинаковые для всех окон по построению
# (протонные события, Kp, уведомления), в него не входят: они окна не различают и попадают в
# объявленную область вывода. В покрытии кандидата — только те каналы, по которым он ранжирован.
COVERAGE_MEANING_RU = ('покрытие кандидата — по каналам ранжирования (трасса, порог |B|, модель '
                       'флюенса); каналы, одинаковые для всех окон, в него не входят и стоят в '
                       'объявленной области вывода')

VERDICTS = ('recommended', 'equivalent', 'all_need_check', 'insufficient')


@dataclass(frozen=True)
class Candidate:
    """Одно начало выхода. Значение None означает «не посчитано», а не ноль."""
    start_utc: datetime
    end_utc: datetime
    saa_min: Optional[float]           # минут в аномалии за окно
    fluence: Optional[float]           # флюенс захваченных протонов за окно, част./см²
    mmod_hits: Optional[float]         # ожидаемое число попаданий метеороидов на 1 м²
    coverage: str                      # 'full' | 'partial' | 'none' — см. COVERAGE_MEANING_RU
    conditions: tuple[str, ...]        # условия проверки, поимённо
    rank: Optional[int] = None         # 1 — лучший; None, если кандидат не ранжирован
    group: Optional[int] = None        # номер группы равнозначности


@dataclass(frozen=True)
class ScanResult:
    requested_duration_min: int
    search_from_utc: datetime
    search_to_utc: datetime
    step_min: int
    rule: str
    tolerance_note: str
    candidates: tuple[Candidate, ...]
    best: tuple[int, ...]
    recommended_index: Optional[int]
    verdict: str
    scope: str
    why: str
    assessments: dict                  # start_utc -> WindowAssessment для показанных кандидатов

    @property
    def n_candidates(self) -> int:
        return len(self.candidates)

    @property
    def recommended(self) -> Optional[Candidate]:
        return self.candidates[self.recommended_index] if self.recommended_index is not None else None

    def to_snapshot(self) -> dict:
        """Ключ `scan` снимка расчёта — РОВНО той формы, что записана в ТЗ круга 11, раздел 1a.

        Это договор с экраном: экран читает только его и во внутренности перебора не лезет.
        Менять форму нельзя ни в чём. Отсутствие перебора — это ОТСУТСТВИЕ ключа в снимке,
        а не пустой список кандидатов и не `None`: пустой перебор и несделанный перебор —
        разные вещи, и путать их нельзя.
        """
        iso = lambda t: t.isoformat()          # noqa: E731
        return {
            'requested_duration_min': int(self.requested_duration_min),
            'search_from_utc': iso(self.search_from_utc),
            'search_to_utc': iso(self.search_to_utc),
            'step_min': int(self.step_min),
            'n_candidates': int(self.n_candidates),
            'rule': self.rule,
            'tolerance_note': self.tolerance_note,
            'candidates': [{'start_utc': iso(c.start_utc), 'end_utc': iso(c.end_utc),
                            'saa_min': c.saa_min, 'fluence': c.fluence, 'mmod_hits': c.mmod_hits,
                            'coverage': c.coverage, 'conditions': list(c.conditions),
                            'rank': c.rank, 'group': c.group} for c in self.candidates],
            'best': list(self.best),
            'recommended_index': self.recommended_index,
            'verdict': self.verdict,
            'scope': self.scope,
            'why': self.why,
        }


def starts(search_from_utc: datetime, search_to_utc: datetime, duration_min: int, step_min: int) -> list:
    """Начала кандидатов: от начала срока поиска до `search_to − duration` с шагом `step_min`.

    Последнее начало — то, при котором окно ещё целиком помещается в горизонт. Шаг проверяется,
    а не подправляется молча: перебор с нулевым или отрицательным шагом — ошибка запроса.
    """
    if type(step_min) is not int or step_min <= 0:
        raise ValueError('шаг перебора должен быть целым числом минут больше нуля, задано %r' % (step_min,))
    if type(duration_min) is not int or duration_min <= 0:
        raise ValueError('длительность выхода должна быть целым числом минут больше нуля, задано %r' % (duration_min,))
    last = search_to_utc - timedelta(minutes=duration_min)
    if last < search_from_utc:
        return []
    n = int((last - search_from_utc).total_seconds() // (60 * step_min))
    return [search_from_utc + timedelta(minutes=step_min * i) for i in range(n + 1)]


def _layers(pool: list, tol_m: float, tol_r: float) -> list:
    """Разбиение на слои недоминируемости правилом (8) методики.

    Слой 1 — кандидаты, которых не превосходит ни один другой из набора: `pair_better` ни от кого
    к ним не применяется. Затем слой 1 убирается, и то же повторяется с остатком. Никакой третьей
    величины и никакой свёртки двух величин в одно число здесь нет: сравниваются только пары
    «не хуже» / «лучше» теми же функциями, которыми сравниваются окна.

    Внутри слоя порядок не назначается: правило выбирать внутри него не имеет права. Слой
    отдаётся в порядке времени начала — так его и печатают.
    """
    rest, out = list(pool), []
    while rest:
        front = [a for a in rest
                 if not any(pair_better(b.saa_min, b.fluence, a.saa_min, a.fluence, tol_m, tol_r)
                            for b in rest if b is not a)]
        if not front:
            # Отношение «лучше» с допусками не транзитивно, поэтому теоретически возможен цикл,
            # в котором недоминируемых нет вовсе. Молча выбрасывать такой остаток нельзя: он
            # объявляется одним слоем целиком, и это честнее выдуманного порядка.
            front = list(rest)
        out.append(sorted(front, key=lambda c: c.start_utc))
        rest = [a for a in rest if a not in front]
    return out


def _rank_candidates(cands: list, tol_m: float, tol_r: float) -> tuple[list, tuple, Optional[int], str]:
    """Расставляет ранги и группы. Возвращает (кандидаты, best, рекомендованный, исход).

    Порядок правил ровно тот, что напечатан пользователю (RANK_RULE_RU):
      1. кандидаты с условием проверки из автоматического выбора исключаются — действующая
         политика прототипа; их слои идут после всех слоёв кандидатов без условий;
      2. внутри каждого класса кандидаты разбиваются на слои недоминируемости правилом (8);
      3. номер слоя и есть номер группы, ранг — сквозной порядковый номер;
      4. внутри слоя порядок по времени начала, потому что выбирать внутри него нечем.
    Кандидат, у которого флюенс не посчитан, НЕ ранжируется: ранга и группы у него нет.
    Подставлять вместо непосчитанного нуль или бесконечность нельзя — это превратило бы
    отсутствие данных в лучший или худший исход.
    """
    ranked = [c for c in cands if c.fluence is not None]
    free = [c for c in ranked if not c.conditions]
    flagged = [c for c in ranked if c.conditions]
    order = _layers(free, tol_m, tol_r) + _layers(flagged, tol_m, tol_r)
    ranked_out, rank_no, groups = [], 0, {}
    for gno, members in enumerate(order, 1):
        for c in members:
            rank_no += 1
            groups[c.start_utc] = (rank_no, gno)
            ranked_out.append(c)
    out = [(c if c.start_utc not in groups else
            Candidate(c.start_utc, c.end_utc, c.saa_min, c.fluence, c.mmod_hits, c.coverage,
                      c.conditions, groups[c.start_utc][0], groups[c.start_utc][1]))
           for c in cands]
    by_start = {c.start_utc: i for i, c in enumerate(out)}
    if not ranked_out:
        return out, (), None, 'insufficient'
    best = tuple(by_start[c.start_utc] for c in order[0])
    if not free:
        # Все ранжированные кандидаты под условием: выбирать правило не имеет права, но числа
        # у них разные и перечислить их обязано — решение за аналитиком.
        return out, best, None, 'all_need_check'
    # Один в лучшей группе — он и есть рекомендация. Несколько — рекомендации нет: они либо
    # равнозначны в допуске, либо спорят, и в обоих случаях правило не выбирает за аналитика.
    # Разницу между этими двумя случаями называет `why_ru` числами обеих сторон.
    # Пятого исхода в договоре с экраном нет (ТЗ, раздел 1a), поэтому компромисс без победителя
    # приходит под тем же `equivalent` — с рекомендацией None и названными сторонами в тексте.
    if len(best) == 1:
        return out, best, best[0], 'recommended'
    return out, best, None, 'equivalent'


def scan_windows(traj: Sequence[TrajectoryPoint], belts: BeltTable, th: Thresholds, *,
                 search_from_utc: datetime, search_to_utc: datetime, duration_min: int,
                 step_min: int, now_utc: datetime,
                 full_assess: Callable[[Window], WindowAssessment],
                 goes=None, goes_observations=None, kp=None, events: Sequence = (),
                 forecasts: Sequence = (), event_facts: Optional[dict] = None,
                 cutoff_utc: Optional[datetime] = None,
                 mmod_area_m2: float = 1.0, mmod_m_min_g: float = 1e-3,
                 tolerance_basis_ru: str = '', shown_limit: int = 5) -> ScanResult:
    """Перебор начал выхода по готовой трассе.

    `full_assess` — полная оценка одного окна теми же входами, какими считается карточка окна.
    Её зовут ТОЛЬКО для показываемых кандидатов (рекомендованный и строки таблицы лучших):
    в ней и заметки, и покрытие всех каналов, и объявленная область вывода.

    `shown_limit` — сколько строк показывает таблица лучших (ТЗ круга 11, раздел 3.3: «строк не
    больше пяти»); ровно столько полных оценок и делается, не больше.
    `mmod_area_m2` и `mmod_m_min_g` — та же пластина 1 м² и та же нижняя масса 1 мг, на которых
    считается число попаданий у окна (`app.compute`, спецификация A5): другие значения дали бы
    у кандидата и у окна разные числа об одном и том же.
    """
    duration_s = float(duration_min) * 60.0
    times = [p.t_utc for p in traj]
    # Поточечные величины считаются ОДИН раз на весь горизонт: от окна они не зависят.
    B_nT = [p.B_nT for p in traj]
    alt_km = [p.alt_km for p in traj]
    ones = [1.0] * len(traj)
    flux = [belts.integral_flux(p.L, p.B_over_B0, th.e_min_MeV).value_per_cm2_s for p in traj]

    grid = starts(search_from_utc, search_to_utc, duration_min, step_min)
    if len(grid) > MAX_CANDIDATES:
        raise ValueError('шаг перебора %d мин даёт %d начал при пределе %d: сравнение по доминированию '
                         'попарное и при таком шаге стоит дороже самого перебора; увеличьте шаг '
                         '(config/settings.toml, [ui].scan_step_min) или сократите срок поиска'
                         % (step_min, len(grid), MAX_CANDIDATES))
    cands: list[Candidate] = []
    for start in grid:
        end = start + timedelta(minutes=duration_min)
        # Нарезка — теми же выражениями, что в assess_window: берутся и обрамляющие точки,
        # чтобы границы окна интерполировались, а не отбрасывались.
        lo = max(0, bisect_right(times, start) - 1)
        hi = min(len(times), bisect_left(times, end) + 1)
        ts = times[lo:hi]
        saa_int = integrate_time(ts, B_nT[lo:hi], start, end, max_gap_seconds=MAX_GAP_SECONDS,
                                 threshold=th.saa_B_threshold_nT)
        fl_int = integrate_time(ts, flux[lo:hi], start, end, max_gap_seconds=MAX_GAP_SECONDS)
        trk_int = integrate_time(ts, ones[lo:hi], start, end, max_gap_seconds=MAX_GAP_SECONDS)
        saa_min = (saa_int.below_threshold_seconds / 60
                   if saa_int.below_threshold_seconds is not None else None)
        cov = _min_cov(_cov(trk_int.covered_seconds, duration_s),
                       _cov(saa_int.covered_seconds, duration_s),
                       _cov(fl_int.covered_seconds, duration_s))
        # Метеороиды — ТОЙ ЖЕ функцией и на том же множестве точек, что и у окна: концы окна
        # включены, точек меньше двух — расчёт невозможен и значение остаётся непосчитанным.
        i0, i1 = bisect_left(times, start), bisect_right(times, end)
        mmod = None
        if i1 - i0 >= 2:
            try:
                mmod = meteoroid_hits_track(times[i0:i1], alt_km[i0:i1], mmod_area_m2, mmod_m_min_g).N
            except ValueError:
                mmod = None          # вне области применимости ECSS: значение не выдумывается
        win = Window(start, duration_min)
        conds = window_conditions(win, th, now_utc, goes=goes, goes_observations=goes_observations,
                                  kp=kp, events=events, forecasts=forecasts, event_facts=event_facts)
        cands.append(Candidate(start, end, saa_min, fl_int.known_integral, mmod, cov.value,
                               tuple(_short(c.text) for c in conds)))

    # Допуски — ТЕ ЖЕ, что у сравнения окон: δ по минутам и ρ по флюенсу после анализа
    # чувствительности. Другие числа здесь означали бы, что «равнозначно» на одном экране
    # значит две разные вещи.
    tol_m, tol_r = th.equiv_tol_min, max(1.0, th.fluence_equiv_ratio)
    cands, best, rec_i, verdict = _rank_candidates(cands, tol_m, tol_r)

    # Полная оценка — только для показываемых: рекомендованного и строк таблицы лучших.
    shown = [i for i in sorted({*best, *( (rec_i,) if rec_i is not None else () )},
                               key=lambda i: (cands[i].rank or 10 ** 9))][:shown_limit]
    if not shown and cands:
        # Ранжировать оказалось не по чему. Полная оценка первого кандидата всё равно нужна:
        # без неё нечем назвать причину отказа, а объявлять её словами, не посчитав, нельзя.
        shown = [0]
    A = {cands[i].start_utc: full_assess(Window(cands[i].start_utc, duration_min)) for i in shown}
    shown_A = list(A.values())
    blocking = [n for a in shown_A for m in a.mechanisms if m.mandatory for n in m.blocking_notes]
    common_ru = tuple(dict.fromkeys(n for a in shown_A for m in a.mechanisms
                                    if m.mandatory for n in m.declared_common_ru))
    # У показанных начал обязательная линия может быть НЕ ПОКРЫТА СОВСЕМ — например, источник
    # исключён пользователем. Правило вердикта в этом случае отказывает, и перебор обязан сказать
    # ТО ЖЕ САМОЕ: иначе на одном экране окажутся отказ сверху и рекомендация перебора под ним.
    # Кандидаты при этом остаются в выдаче со своими числами: они посчитаны, и прятать их незачем,
    # но рекомендацией они не становятся.
    refusal_ru = ''
    if blocking and verdict != 'insufficient':
        verdict, rec_i, refusal_ru = 'insufficient', None, '; '.join(dict.fromkeys(blocking))
    scope, _detail, _facts = declared_scope(shown_A, 'insufficient' if verdict == 'insufficient' else 'preferred',
                                            [a for a in shown_A], blocking, common_ru)
    tol_note = ('различием не считается разница до %s мин по минутам в аномалии и до ×%s по флюенсу — '
                'те же допуски, по которым сравниваются окна; %s'
                % (fmt_ru(tol_m), ('%.2f' % tol_r).replace('.', ','),
                   # Имён ключей настроек здесь быть не должно: строка уходит на оперативный
                   # уровень экрана и в отчёт, где идентификаторов кода не бывает (О5).
                   tolerance_basis_ru or ('оба взяты из настроек сервиса как нижние границы — '
                                          'анализ чувствительности в этом расчёте их не уточнял')))
    return ScanResult(requested_duration_min=int(duration_min), search_from_utc=search_from_utc,
                      search_to_utc=search_to_utc, step_min=int(step_min), rule=RANK_RULE_RU,
                      tolerance_note=tol_note, candidates=tuple(cands), best=tuple(best),
                      recommended_index=rec_i, verdict=verdict, scope=scope,
                      why=why_ru(cands, best, rec_i, verdict, step_min, tol_m, tol_r, refusal_ru),
                      assessments=A)


def _t_ru(t: datetime) -> str:
    return t.strftime('%d.%m %H:%MZ')


def conflict_ru(group: Sequence[Candidate], tol_m: float, tol_r: float) -> str:
    """Спор внутри лучшей группы: одно начало лучше по минутам, другое по флюенсу сверх допуска.

    Обычно величины согласованы: время в аномалии и есть то, что набирает флюенс. Но когда они
    расходятся, это надо не замолчать, а назвать числами обеих сторон: складывать их в один балл
    нельзя, и выбор остаётся за аналитиком (правило (8) методики — компромисс без победителя).
    Пустая строка означает, что члены группы попарно неразличимы в допуске, то есть равнозначны.
    """
    pool = [c for c in group if c.fluence is not None and c.saa_min is not None]
    if len(pool) < 2:
        return ''
    disagree = any(not pair_not_worse(a.saa_min, a.fluence, b.saa_min, b.fluence, tol_m, tol_r)
                   for a in pool for b in pool if a is not b)
    if not disagree:
        return ''
    by_fl = min(pool, key=lambda c: (c.fluence, c.saa_min))
    by_saa = min(pool, key=lambda c: (c.saa_min, c.fluence))
    if by_fl.start_utc == by_saa.start_utc:
        return ''
    return ('компромисс без победителя: по флюенсу лучше начало %s (%s против %s част./см²), '
            'по времени в аномалии — начало %s (%s против %s мин); величины указывают на разные '
            'начала, складывать их в один балл нельзя — выбор за аналитиком'
            % (_t_ru(by_fl.start_utc), fmt_ru(by_fl.fluence), fmt_ru(by_saa.fluence),
               _t_ru(by_saa.start_utc), fmt_ru(by_saa.saa_min), fmt_ru(by_fl.saa_min)))


def why_ru(cands: Sequence[Candidate], best: Sequence[int], rec_i: Optional[int],
           verdict: str, step_min: int, tol_m: float, tol_r: float, refusal_ru: str = '') -> str:
    """«Почему именно это окно» — числами перебранных кандидатов, а не словами о них."""
    n = len(cands)
    head = 'перебрано %d %s с шагом %d мин' % (n, _plural_ru(n, 'начало', 'начала', 'начал'), step_min)
    ranked = [c for c in cands if c.rank is not None]
    if verdict == 'insufficient':
        if refusal_ru:
            # Числа у кандидатов есть, но рекомендацией они не становятся: обязательная линия
            # не покрыта совсем. Пометка обязана стоять здесь же, а не подразумеваться.
            return ('%s; рекомендации нет: у показанных начал обязательная линия не покрыта совсем — %s. '
                    'Величины начал посчитаны и показаны, но это сравнение факторов, а не рекомендация.'
                    % (head, refusal_ru))
        why_not = ('трассы окна нет' if all(c.coverage == 'none' for c in cands) or not cands
                   else 'флюенс не посчитан ни на одном из них')
        return ('%s; ранжировать не по чему: %s. Отсутствие величины не заменяется нулём, '
                'поэтому ранга нет ни у одного кандидата.' % (head, why_not))
    group = [cands[i] for i in best]
    conflict = conflict_ru(group, tol_m, tol_r)
    worst_f = max((c.fluence for c in ranked if c.fluence is not None), default=None)
    worst_m = max((c.saa_min for c in ranked if c.saa_min is not None), default=None)

    def against_worst(c: Candidate) -> str:
        bits = []
        if c.saa_min is not None and worst_m is not None:
            bits.append('%s мин в аномалии против %s у худшего из перебранных' % (fmt_ru(c.saa_min), fmt_ru(worst_m)))
        if c.fluence is not None and worst_f is not None:
            bits.append('флюенс %s против %s част./см²' % (fmt_ru(c.fluence), fmt_ru(worst_f)))
        return ', '.join(bits)

    if verdict == 'all_need_check':
        names = sorted({x for c in ranked for x in c.conditions})
        return ('%s; все ранжированные начала стоят под условием проверки, поэтому сервис не выбирает '
                'из них сам: %s. Числа начал при этом посчитаны и показаны — решение за аналитиком.%s'
                % (head, '; '.join(names) or 'условие названо в карточке окна',
                   (' Кроме того, ' + conflict + '.') if conflict else ''))
    if verdict == 'recommended':
        top = cands[rec_i]
        tail = against_worst(top)
        return ('%s; ни одно другое начало не превосходит по правилу начало %s%s. Это единственное '
                'начало в лучшей группе, поэтому оно и рекомендовано.'
                % (head, _t_ru(top.start_utc), (': ' + tail) if tail else ''))
    # несколько в лучшей группе: либо спор, либо равнозначность — и то и другое называется прямо.
    # Единицы названы один раз в заголовке перечня: у каждого начала они те же самые.
    listed = ('начала (минут в аномалии; флюенс, част./см²): '
              + ', '.join('%s (%s; %s)' % (_t_ru(c.start_utc), fmt_ru(c.saa_min), fmt_ru(c.fluence))
                          for c in group[:4]) + (' и другие' if len(group) > 4 else ''))
    if conflict:
        return ('%s; в лучшей группе %d %s, и они спорят между собой — %s. Рекомендации одного начала '
                'нет: %s.' % (head, len(group), _plural_ru(len(group), 'начало', 'начала', 'начал'),
                              conflict, listed))
    # Равнозначная группа из соседних начал — это не «сервис не смог выбрать», а ответ на вопрос
    # человека: выходить можно в любой момент этого промежутка, внутри него разницы нет.
    times = [c.start_utc for c in group]
    contiguous = all((b - a) == timedelta(minutes=step_min) for a, b in zip(times, times[1:]))
    where = ('любое начало с %s до %s' % (_t_ru(times[0]), _t_ru(times[-1])) if contiguous
             else 'начала ' + ', '.join(_t_ru(t) for t in times[:6]) + (' и другие' if len(times) > 6 else ''))
    best_one = min(group, key=lambda c: (c.fluence if c.fluence is not None else float('inf'),
                                         c.saa_min if c.saa_min is not None else float('inf')))
    return ('%s; лучших начал %d, и они равнозначны — различий сверх допуска между ними нет: %s. '
            'Сервис не выбирает одно за аналитика, потому что выбирать не по чему: %s. Для сравнения, '
            'наименьшие величины в группе — %s.'
            % (head, len(group), where, listed, against_worst(best_one)))


def _plural_ru(n: int, one: str, few: str, many: str) -> str:
    if 11 <= n % 100 <= 19:
        return many
    return one if n % 10 == 1 else (few if 2 <= n % 10 <= 4 else many)
