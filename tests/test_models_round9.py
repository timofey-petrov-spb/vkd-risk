# -*- coding: utf-8 -*-
"""Девятый круг, область «модели»: пять находок глубокого разбора плюс две сквозные.

Проверяется ровно то, что было сломано, и проверяется числами того же расчёта:

* М1 — под отказом «оснований недостаточно» нет утвердительного вывода сравнения;
* М2 — в текущем режиме линия уведомлений о событиях объявлена неопрошенной;
* М3 — суффикс про исключение одного источника не приклеен к строке про другой канал;
* М4 — сводка проверки после отсечки выносит вердикт (сбылось / пропуск / ложная тревога);
* М5 — число печатается одним правилом в ядре, расчёте и выгрузке;
* плюс сравнение величин есть и там, где правило выбирать не имеет права
  (единственный кандидат, все окна под условием).
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

import pytest

from app.compute import _verification_summary, fmt, run
from app.export import fmt as export_fmt, report_md
from vkd.explain.format import SCI_MIN, fmt_ru
from vkd.integration.donki_live import decode as decode_notifications, parse_notifications
from vkd.integration.replay_live import fetch_none
from vkd.sources.live_cache import Fetch
from vkd.windows.compare import Thresholds

UTC = timezone.utc
NOW = datetime(2026, 9, 19, 8, 5, tzinfo=UTC)
T_GANNON = datetime(2024, 5, 10, 12, tzinfo=UTC)
T_GANNON_06 = datetime(2024, 5, 10, 6, tzinfo=UTC)
T_QUIET = datetime(2024, 6, 25, 12, tzinfo=UTC)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Ответ службы уведомлений, сохранённый реестром A1 (май 2024). Берётся как готовые БАЙТЫ ответа:
# проверка линии уведомлений не должна зависеть ни от сети, ни от того, что происходит на Солнце
# в день прогона.
DONKI_RESPONSE = os.path.join(ROOT, 'data', 'source_registry_2024', 'donki',
                              'raw', 'notifications_2024-05_response.json')


def _donki_live(now=NOW):
    """Живая лента уведомлений, собранная из сохранённого ответа службы: (лента, записи, квитанция)."""
    with open(DONKI_RESPONSE, 'rb') as fh:
        raw = fh.read()
    parsed = parse_notifications(raw, now)
    f = Fetch('nasa_donki_notification', True, False, now, 0.0, 'получено по сети, давность данных 0,0 мин',
              raw.decode('utf-8'), None, 'live', None,
              {'raw_record_id': 'nasa_donki_notification:proverka', 'version': 'proverka'}, parsed, raw)
    return decode_notifications(f), {}, f


def _live_fetched(donki=None):
    """Кортеж источников текущего режима без сети; живой объявляется только лента уведомлений."""
    base = list(fetch_none('источник не запрашивался в этой проверке'))
    if donki is not None:
        base[4] = donki
    return tuple(base)


def _hist(t0, dur=360, search=720, offs=(0, 240)):
    return run('history_forecast', t0, dur, search, list(offs), now=NOW, fetched=fetch_none())


@pytest.fixture(scope='module')
def single_candidate():
    """10.05.2024 06:00, период 1440, сдвиги 0/240 — окно 2 под условием, кандидат один."""
    return _hist(T_GANNON_06, search=1440)


@pytest.fixture(scope='module')
def all_flagged():
    """Гэннон 10.05.2024 12:00 — оба окна под условием прихода выброса."""
    return _hist(T_GANNON)


@pytest.fixture(scope='module')
def quiet():
    return _hist(T_QUIET, search=1440, offs=(0, 480))


@pytest.fixture(scope='module')
def quiet_tolerance():
    """25.06.2024 12:00, период 1440, сдвиги 0/600 — выбранное окно ЛУЧШЕ по минутам
    в аномалии (65,05 против 71,73 мин) и ХУЖЕ по флюенсу (2,50·10⁶ против 1,75·10⁶,
    отношение ×1,42 — внутри допуска ×1,50). Это единственная ветка правила, где проигрыш
    по величине приходится назвать вслух; случай найден прогоном по архиву мая–июня, а не
    подобран руками. На сдвиге 480 (фикстура `quiet`) после перехода на интегрирование по
    фактическому времени (ветка Codex 0.7.0) флюенс выбранного окна НИЖЕ, и ветка молчит."""
    return _hist(T_QUIET, search=1440, offs=(0, 600))


# ------------------------------------------------------------------ М1: отказ не рекомендует
def test_pri_otkaze_sravnenie_ne_nazyvaet_luchshee_okno():
    """Ветка insufficient: в причинах не должно быть «— лучше окно N». Источник текста обязан
    быть честным сам по себе — экран отрежет своё, но в отчёт и JSON уходит эта же строка."""
    r = run('live', NOW, 360, 720, [0, 240], disabled={'goes': 'off'}, now=NOW)
    rec = r.S['recommendation']
    assert rec['verdict'] == 'insufficient', rec['rule']
    sw = (rec['per_mechanism'] or {}).get('spaceweather', '')
    assert sw, rec['per_mechanism']
    assert 'лучше окно' not in sw, sw
    assert 'равнозначны в допуске' not in sw, sw
    assert 'сравнение факторов, а не рекомендация' in sw, sw
    assert 'без данных' in sw, sw
    # та же строка — среди причин; ни одна причина не называет предпочтительное окно
    assert any('сравнение факторов, а не рекомендация' in x for x in rec['reasons']), rec['reasons']
    assert not any('— лучше окно' in x for x in rec['reasons']), rec['reasons']


# ------------------------------------------------------------------ М2: линия событий в live
# Двенадцатый круг, п. 6 отменил прежнее поведение. Раньше линия уведомлений в текущем режиме
# не опрашивалась вовсе, и проверка требовала, чтобы это было ОБЪЯВЛЕНО (ноль записей без
# опроса — не «событий нет»). Теперь линия опрашивается живьём, и проверяется то же требование
# с другой стороны: признак `connected` обязан означать ровно то, что написано, — состоялся
# опрос или нет, — и при отказе ноль по-прежнему не выдаётся за отсутствие событий.
def test_v_tekushchem_rezhime_liniya_sobytiy_oproshena():
    """Опрос состоялся: рядом с числом записей стоят число сообщений ленты и момент опроса."""
    r = run('live', NOW, 360, 720, [0, 240], now=NOW, fetched=_live_fetched(_donki_live()))
    S = r.S
    line = S['events_line']
    assert line['connected'] is True and line['reason_ru'] is None, line
    assert line['live'] is True and line['live_ok'] is True
    assert line['feed_messages'] > 0 and line['polled_utc'], line
    # число записей НА ГОРИЗОНТЕ и число сообщений ленты — разные величины и не подменяют друг друга
    assert line['records'] <= line['feed_messages']
    # ограничение охвата теперь другое: не «источник не опрашивается», а граница самой ленты
    cov = ' | '.join(S['coverage_missing'])
    assert 'DONKI' in cov and 'не опрашива' not in cov, S['coverage_missing']
    src = S['sources']['donki_live']
    assert src['state'] == 'live' and src['feed_messages'] == line['feed_messages'], src
    assert 'donki_archive' not in S['sources'], 'живой опрос не подписывается словом «архив»'
    # исторический режим по-прежнему подключён — через архив
    assert _hist(T_GANNON).S['events_line']['connected'] is True


def test_otkaz_lenty_uvedomleniy_nazyvaetsya_prichinoy_a_ne_nulyom():
    """Ленты нет — сказано прямо: ноль записей не выдаётся за отсутствие событий."""
    r = run('live', NOW, 360, 720, [0, 240], now=NOW, fetched=_live_fetched())
    line = r.S['events_line']
    assert line['connected'] is False
    assert line['records'] == 0 and 'не получены' in line['reason_ru'], line
    assert 'DONKI' in line['reason_ru']
    cov = ' | '.join(r.S['coverage_missing'])
    assert 'DONKI' in cov and 'не получены' in cov, r.S['coverage_missing']
    assert r.S['sources']['donki_live']['state'] == 'none'


def test_stsenariy_chto_esli_schitaetsya_otdelno():
    """Сценарий «что если» даёт события, и они считаются отдельно от записей источника."""
    from vkd.windows.scenario import Scenario
    r = run('live', NOW, 360, 720, [0, 240], now=NOW, fetched=_live_fetched(),
            scenario=Scenario('t', sep_onset_offset_min=120, sep_level_pfu=100.0))
    line = r.S['events_line']
    assert line['connected'] is False          # сценарий не заменяет опроса источника
    assert line['simulated_records'] >= 1 and line['records'] == line['simulated_records']


# ------------------------------------------------------------------ М3: каналы не смешиваются
def test_prichina_pro_isklyuchenie_ne_prikleivaetsya_k_drugomu_kanalu(all_flagged):
    """«GOES исключён строгим режимом» и «уведомлений DONKI нет» — разные каналы и разные строки.
    В одной строке исключение первого читается как относящееся ко второму."""
    notes = [n for w in all_flagged.S['windows'] for m in w['mechanisms'] for n in m['coverage_notes']]
    goes = [n for n in notes if n.startswith('GOES')]
    donki = [n for n in notes if n.startswith('уведомления DONKI')]
    assert goes and donki, notes
    for n in goes:
        assert 'DONKI' not in n, n
    for n in donki:
        assert 'исключ' not in n, n


# ------------------------------------------------- единственный кандидат и все окна под условием
def test_edinstvennyy_kandidat_pokazyvaet_velichiny_i_nazyvaet_proigrysh(single_candidate):
    """Выбор по правилу условий не должен выглядеть как выигрыш по обстановке: у выбранного
    окна флюенс ВЫШЕ, чем у помеченного, и правило обязано это сказать."""
    rec = single_candidate.S['recommendation']
    assert rec['verdict'] == 'preferred'
    sw = (rec['per_mechanism'] or {}).get('spaceweather', '')
    assert 'мин в аномалии' in sw and 'флюенс' in sw, sw
    assert 'под условием' in sw, sw
    assert 'правилом условий' in rec['rule'], rec['rule']
    assert 'хуже по флюенсу' in rec['rule'], rec['rule']
    # числа из разбора: 2,22·10^6 у выбранного против 2,06·10^6 у помеченного
    f = {w['index']: next(x['value'] for m in w['mechanisms'] if m['id'] == 'spaceweather'
                          for x in m['factors'] if x['name'].startswith('флюенс'))
         for w in single_candidate.S['windows']}
    assert f[1] > f[2], f


def test_vse_okna_pod_usloviem_dayut_spravku_a_ne_rekomendatsiyu(all_flagged):
    """Расчёт уже содержит опору для решения руководителя работ; она печатается и прямо
    помечена как не-рекомендация."""
    rec = all_flagged.S['recommendation']
    assert rec['verdict'] == 'all_need_check'
    sw = (rec['per_mechanism'] or {}).get('spaceweather', '')
    assert 'мин в аномалии' in sw and 'флюенс' in sw, sw
    assert 'справка' in sw and 'не рекомендация' in sw, sw
    assert 'по минутам в аномалии ниже' in sw and 'по флюенсу' in sw, sw
    assert rec['preferred'] is None


def test_tolerans_nazyvaet_proigrysh_proigryshem(quiet_tolerance, quiet):
    """«не хуже по флюенсу», когда числа в той же скобке показывают обратное, — подгонка вывода.

    Проверяется и ветка, где проигрыш есть (он назван проигрышем, с числом, допуском и тем,
    по какой величине сделан выбор), и та, где его нет: слова «не хуже» не должно быть ни в одной."""
    rule = quiet_tolerance.S['recommendation']['rule']
    assert 'не хуже' not in rule, rule
    assert 'выбранное окно выше на' in rule, rule
    assert 'част./см²' in rule, rule                       # проигрыш назван с единицей
    assert 'внутри допуска ×1,50' in rule, rule            # и допуск стоит в той же фразе
    assert 'различием не считается' in rule, rule
    assert 'выбор сделан по минутам в аномалии' in rule, rule
    # тот же инвариант там, где выбранное окно выигрывает по обеим величинам
    assert 'не хуже' not in quiet.S['recommendation']['rule'], quiet.S['recommendation']['rule']


# ------------------------------------------------------------------ М4: вердикт по прогнозу
def test_proverka_posle_otsechki_vynosit_verdikt(all_flagged, quiet):
    """Гэннон 12:00: буря — попадание, протонное событие — ПРОПУСК (уведомление вышло позже
    отсечки). Тихая дата: ложных тревог нет. Перечисление фактов вердиктом не является."""
    v = all_flagged.S['verification']
    assert v['marks'] == {'storm': 'hit', 'proton': 'miss'}, v['marks']
    s = v['summary']
    assert s.startswith('Сбылось: буря'), s
    assert 'заблаговременность 3 ч 00 мин' in s, s
    assert 'Не предупредили: протонное событие' in s, s
    assert 'ПОСЛЕ отсечки' in s and 'это пропуск, а не ложная тревога' in s, s
    assert '207 pfu' in s, s
    q = quiet.S['verification']
    assert q['marks'] == {'storm': 'true_negative', 'proton': 'true_negative'}, q['marks']
    assert 'Ложных тревог по буре нет' in q['summary'], q['summary']
    assert 'максимум Kp 2,67' in q['summary'], q['summary']
    assert 'условие поставлено' not in q['summary'], q['summary']


def test_lozhnaya_trevoga_nazyvaetsya_lozhnoy_trevogoy():
    """Четвёртой метки в архиве мая–июня не нашлось: проверяется на разметке напрямую,
    иначе «проверены ложные предупреждения» осталось бы необоснованным словом."""
    th = Thresholds.from_settings()
    ver = {'kp_obs': [{'from_utc': '2024-05-03T12:00:00+00:00', 'to_utc': '2024-05-03T15:00:00+00:00', 'kp': 2.0}],
           'goes_obs_max': {'t_utc': '2024-05-03T13:00:00+00:00', 'value_pfu': 0.2}, 'events': []}
    txt, marks = _verification_summary(ver, datetime(2024, 5, 3, 12, tzinfo=UTC), th, {'GST', 'SEP'})
    assert marks == {'storm': 'false_alarm', 'proton': 'false_alarm'}, marks
    assert 'Ложная тревога по буре' in txt and 'порог не достигнут' in txt, txt
    assert 'Ложная тревога по протонному событию' in txt, txt


def test_zablagovremennosti_ne_byvaet_otritsatelnoy():
    """Интервал Kp, начавшийся до отсечки, давал «заблаговременность −1 ч 00 мин»."""
    th = Thresholds.from_settings()
    ver = {'kp_obs': [{'from_utc': '2024-05-10T18:00:00+00:00', 'to_utc': '2024-05-10T21:00:00+00:00', 'kp': 8.67}],
           'goes_obs_max': None, 'events': []}
    txt, _ = _verification_summary(ver, datetime(2024, 5, 10, 19, tzinfo=UTC), th, {'GST'})
    assert '−' not in txt and 'заблаговременность' not in txt, txt
    assert 'раньше отсечки' in txt, txt


# ------------------------------------------------------------------ М5: одно правило записи чисел
@pytest.mark.parametrize('v, expect', [(88701.0, '8,87·10^4'), (42936.0, '4,29·10^4'), (1650000.0, '1,65·10^6'),
                                       (9999.0, '9999'), (207.0, '207'), (2.67, '2,67'), (0.0, '0')])
def test_odno_pravilo_zapisi_chisel(v, expect):
    assert fmt_ru(v) == expect
    assert SCI_MIN == 1e4


def test_raschyot_i_vygruzka_pechatayut_chislo_odinakovo():
    """Одна величина — один вид во всех артефактах расчёта: ядро, слой расчёта, отчёт."""
    for v in (88701.0, 42936.0, 173159.77, 1650000.0, 12.5, 0.003):
        core = fmt_ru(v).replace('10^4', '10⁴').replace('10^5', '10⁵').replace('10^6', '10⁶').replace('10^-3', '10⁻³')
        assert fmt(v) == core, (v, fmt(v), core)
        assert export_fmt(v) == core, (v, export_fmt(v), core)


def test_v_otchyote_flyuens_odnogo_vida(quiet):
    """В отчёте не должно быть голого пятизначного числа рядом со степенной записью того же
    столбца: именно так выглядела строка «флюенс ниже (88701 против 2,22·10⁶ част./см²)»."""
    md = report_md(quiet.S, quiet.raw_records)
    bad = re.findall(r'(?<![\d,·⁰¹²³⁴⁵⁶⁷⁸⁹])\d{5,}(?![\d,·])\s*(?=част\./см²)', md)
    assert not bad, bad
