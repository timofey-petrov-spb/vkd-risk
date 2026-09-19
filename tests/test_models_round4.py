# -*- coding: utf-8 -*-
"""Четвёртый круг, область «модели»: на экране и в выгрузке нет утверждения, которое
опровергается числами того же расчёта, и нет оборванной или склеенной фразы.

Каждый тест закрепляет одну подтверждённую прогоном находку четвёртого круга.
"""
import re
from datetime import datetime, timezone

import pytest

from app.compute import run
from app.export import report_md
from tests.test_integration import _fetched

UTC = timezone.utc
T_GANNON = datetime(2024, 5, 10, 12, 0, tzinfo=UTC)
T_QUIET = datetime(2024, 6, 25, 12, 0, tzinfo=UTC)


@pytest.fixture(scope='module')
def gannon():
    return run('history_forecast', T_GANNON, 360, 720, [0, 240], fetched=_fetched(), now=T_GANNON)


@pytest.fixture(scope='module')
def quiet():
    return run('history_forecast', T_QUIET, 360, 1440, [0, 480], fetched=_fetched(), now=T_QUIET)


def _storm_conditions(S):
    return [c for w in S['windows'] for m in w['mechanisms'] for c in m['conditions'] if c['kind'] == 'GST']


def _record_segments(src: str) -> list:
    """Перечень записей сигнала: то, что стоит до общих хвостов «условие поставлено…» и
    «действие …». Одна запись — один сегмент между « · »."""
    head = src.split('; условие поставлено')[0].split('; действие')[0]
    return head.split(': ', 1)[-1].split(' · ')


# ------------------------------------------------- ложная атрибуция кластера уведомлений
def test_kazhdoe_kp_stoit_ryadom_so_svoey_zapisyu(gannon):
    """На «Гэннон» время прихода бралось как минимум по кластеру, а граница Kp — как максимум,
    и обе печатались одним предложением: карточка объявляла «приход 10.05 12:14, диапазон 8–9»
    со ссылкой на уведомление 20240508-AL-012, в теле которого объявлен диапазон 6–8.

    Проверка: каждое напечатанное число диапазона встречается в фактах той записи, номер
    которой стоит в той же скобке.
    """
    conds = _storm_conditions(gannon.S)
    assert conds, 'на буре Гэннон условие по буре должно быть'
    facts = gannon.S['history']['event_facts']
    by_release = {}
    for rid, f in facts.items():
        by_release.setdefault(rid.split(':')[1], f)
    seen = 0
    for c in conds:
        for src in c['sources']:
            # одна запись — один сегмент между « · »; в сегменте и число, и номер выпуска
            for seg in _record_segments(src):
                m = re.search(r'диапазона (\d+(?:[.,]\d+)?)–(\d+(?:[.,]\d+)?)', seg)
                if not m:
                    continue
                rel = re.search(r'DONKI (\d{8}-\w{2}-\d{3})', seg)
                assert rel, seg
                f = by_release[rel.group(1)]
                lo, hi = (float(x.replace(',', '.')) for x in m.groups())
                assert (lo, hi) == (float(f['kp_range_min']), float(f['kp_range_max'])), (seg, f)
                kp = re.search(r'Kp до (\d+(?:[.,]\d+)?)', seg)
                assert float(kp.group(1).replace(',', '.')) == float(f['kp_range_max']), (seg, f)
                seen += 1
    assert seen >= 2, 'кластер из двух уведомлений должен печататься двумя записями'


def test_vremya_prihoda_i_chuzhoy_kp_ne_v_odnom_predlozhenii(gannon):
    """«12:14» и «до 9» — числа из разных уведомлений; в одном сегменте их быть не может."""
    for c in _storm_conditions(gannon.S):
        for src in c['sources']:
            for seg in _record_segments(src):
                assert not ('12:14' in seg and 'Kp до 9' in seg), seg
                assert not ('13:03' in seg and 'Kp до 8' in seg), seg


def test_klaster_nazvan_klasterom(gannon):
    """«(1 источник)» при двух уведомлениях усиливало впечатление одной записи."""
    for c in _storm_conditions(gannon.S):
        assert '(1 источник)' not in c['text']
        assert re.search(r'\(\d+ сигнал\w*( по \d+ запис\w+)?\)', c['text']), c['text']


# ------------------------------------------------- правило шагов 3–4
def test_pravilo_ne_utverzhdaet_neproverennogo(quiet):
    """Хвост «не хуже по флюенсу и минутам» печатался безусловно, а флюенс выбранного окна выше."""
    from tests.test_compare import complete_for_comparator
    from vkd.windows.compare import recommend, Thresholds
    # Синтетическое полное покрытие изолирует ТОЛЬКО математику сравнения. На настоящих данных
    # покрытие частичное, и с 19.09 это даёт вердикт с объявленной областью, а не отказ.
    assert quiet.rec.verdict == 'preferred' and quiet.rec.preferred is not None
    assert 'при покрытии модели' in quiet.rec.scope_ru, quiet.rec.scope_ru
    result = recommend(complete_for_comparator(quiet.assessments), Thresholds())
    rule = result.rule_applied
    assert rule.startswith('п.3–4'), rule
    if 'не хуже по флюенсу' in rule:
        assert 'допуск' in rule, rule
    else:
        assert 'флюенс ниже' in rule, rule
    assert re.search(r'отношение ×\d+,\d+', rule), rule
    assert 'не хуже по флюенсу и минутам' not in rule, rule
    # числа правила совпадают с числами сравнения по механизму на том же экране
    per = result.per_mechanism_comparison['spaceweather']
    for num in re.findall(r'\d+,\d+·10\^6', rule.replace('·', '·')):
        assert num in per, (num, per)


def test_pravilo_beryot_chisla_iz_sravneniya_po_mehanizmu(quiet):
    """Минуты в правиле — те же, что в строке сравнения окон."""
    from tests.test_compare import complete_for_comparator
    from vkd.windows.compare import recommend, Thresholds
    # Синтетическое полное покрытие изолирует ТОЛЬКО математику сравнения (см. тест выше).
    assert quiet.rec.verdict == 'preferred'
    result = recommend(complete_for_comparator(quiet.assessments), Thresholds())
    rule = result.rule_applied
    per = result.per_mechanism_comparison['spaceweather']
    m = re.search(r'\((\d+(?:,\d+)?) против (\d+(?:,\d+)?) мин\)', rule)
    assert m, rule
    for v in m.groups():
        assert '%s мин в аномалии' % v in per, (v, per)


# ------------------------------------------------- таблица проверки после отсечки
def test_v_tablitse_proverki_net_dvuh_strok_s_odnim_intervalom(gannon):
    """12 строк на 7 различных интервалов, один интервал дважды с разными Kp и без объяснения."""
    rows = gannon.S['verification']['kp_obs']
    keys = [(k['from_utc'], k['to_utc']) for k in rows]
    assert len(keys) == len(set(keys)), keys
    assert all(k.get('origin') for k in rows), rows


def test_rashozhdenie_znacheniy_nazvano_pryamo(gannon):
    """Интервал 21:00–00:00: 8,67 по окончательному ряду GFZ и 9 по уведомлению."""
    rows = {k['from_utc']: k for k in gannon.S['verification']['kp_obs']}
    k = rows['2024-05-10T21:00:00+00:00']
    assert k['kp'] == 9.0 and 'уточнено с 8,67 до 9' in k['origin'], k
    assert '20240511-AL-003' in k['origin'] and 'публикация' in k['origin'], k


# ------------------------------------------------- выгрузка против экрана
def test_otchyot_ne_stavit_usloviya_tam_gde_ih_net(quiet, gannon):
    """report.md «Тихой даты» начинал проверку словами «условие поставлено в 12:00Z»,
    хотя обе карточки окон говорят «условий проверки нет»."""
    md_q = report_md(quiet.S, quiet.raw_records)
    md_g = report_md(gannon.S, gannon.raw_records)
    assert not any(m['needs_check'] for w in quiet.S['windows'] for m in w['mechanisms'])
    assert 'условие поставлено' not in md_q
    assert 'условий проверки на отсечку не ставилось' in md_q
    assert 'условие поставлено' in md_g


def test_okno_cherez_polnoch_ne_teryaet_datu_kontsa(quiet):
    """«20:00–02:00» без даты читается как ошибка при длительности 360 мин."""
    md = report_md(quiet.S, quiet.raw_records)
    assert '25.06 20:00 — 26.06 02:00 UTC (360 мин)' in md, [l for l in md.split('\n') if l.startswith('### Окно 2')]


@pytest.mark.parametrize('mode_id, t0', [('history_review', T_GANNON), ('history_forecast', T_GANNON),
                                         ('history_forecast', T_QUIET)])
def test_v_otchyote_net_identifikatorov_koda_vne_tablits(mode_id, t0):
    """Проверка расширена с строк таблицы на весь текст отчёта: прежде вне таблицы жили
    «nasa_jsc_oem», «nasa_donki_notification:…» и 64-значный идентификатор выпуска NOAA."""
    r = run(mode_id, t0, 360, 720 if t0 == T_GANNON else 1440, [0, 240] if t0 == T_GANNON else [0, 480],
            fetched=_fetched(), now=t0)
    md = report_md(r.S, r.raw_records)
    body = re.sub(r'https?://\S+', '', md)
    bad = re.findall(r'(?<![\w/.:-])[a-z][a-z0-9]*_[a-z0-9_]+', body)
    assert not bad, sorted(set(bad))
    assert not re.search(r'\b[0-9a-f]{32,}\b', body), 'идентификатор выпуска с хешем остаётся в manifest.json'
    assert '— —' not in md and ': — ' not in md, 'значение-прочерк и разделитель-тире подряд'


@pytest.mark.parametrize('mode_id, t0', [('history_review', T_GANNON), ('history_forecast', T_GANNON),
                                         ('history_forecast', T_QUIET)])
def test_adresa_pervoistochnika_v_otchyote_ne_bednee_ekrana(mode_id, t0):
    """_source_link искал запись по ключу снимка, а идентификаторы записей начинаются иначе:
    совпадений не было ни одного, и первоисточники в отчёте отсутствовали во всех режимах."""
    r = run(mode_id, t0, 360, 720 if t0 == T_GANNON else 1440, [0, 240] if t0 == T_GANNON else [0, 480],
            fetched=_fetched(), now=t0)
    md = report_md(r.S, r.raw_records)
    from app.ui import raw_record, record_url
    assert 'Первоисточники записей:' in md
    checked = 0
    for k, v in r.S['sources'].items():
        if k.startswith('_'):
            continue
        urls = [u for rid in (v.get('record_ids') or [])
                for u in (record_url(raw_record(r.raw_records, rid)),) if u]
        if v.get('url'):
            urls.append(v['url'])
        if not urls:
            continue
        assert any(u in md for u in urls), (k, urls)
        checked += 1
    assert checked >= 2, 'выпуски NOAA и архив DONKI должны давать адрес первоисточника'
    # у каждого условия адрес первоисточника остаётся рядом с условием; перечень записей
    # отчёт ограничивает пятью и говорит об этом прямо («всего N, полный список — cards.json»)
    for c in [c for w in r.S['windows'] for m in w['mechanisms'] for c in (m['conditions'] or [])]:
        urls = [u for i in c['event_ids'] for u in (record_url(raw_record(r.raw_records, i)),) if u]
        if urls:
            assert any(u in md for u in urls), (c['kind'], urls)


def test_otchyot_nazyvaet_sluzhbu_kotoraya_otdala_dannye():
    """Раздел «Траектория» называл источником имя парсера записи (celestrak_gp), а строкой
    ниже статус называл фактический адрес — отчёт противоречил себе через строку."""
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    r = run('live', now, 360, 720, [0, 240])
    md = report_md(r.S, r.raw_records)
    tm = r.S['trajectory_meta']
    traj_line = next(l for l in md.split('\n') if l.startswith('- SGP4') or l.startswith('- эфемериды')
                     or l.startswith('- орбита недоступна'))
    if tm.get('tle_url'):
        assert tm['tle_url'] in traj_line, traj_line
        if tm.get('tle_url_primary_failed'):
            assert 'резервный адрес, основной' in traj_line, traj_line
    assert 'celestrak_gp' not in traj_line and 'nasa_jsc_oem' not in traj_line


# ------------------------------------------------- признак состояния источника
@pytest.mark.parametrize('mode_id, t0', [('history_forecast', T_GANNON), ('history_review', T_GANNON)])
def test_sostoyanie_istochnika_yavnym_polem(mode_id, t0):
    """Экран не должен искать подстроку «исключён» в тексте статуса: слой источников штатно
    дописывает туда «незавершённый Kp-nowcast исключён» почти в каждом живом прогоне."""
    r = run(mode_id, t0, 360, 720, [0, 240], fetched=_fetched(), now=t0)
    allowed = {'live', 'cache', 'off', 'archive', 'none', 'builtin', 'simulated'}
    for k, v in r.S['sources'].items():
        if k.startswith('_'):
            continue
        assert v.get('state') in allowed, (k, v.get('state'))


def test_sostoyanie_off_tolko_pri_otklyuchenii_polzovatelem():
    """'off' ставится по тому же признаку, по которому принято решение, — disabled[...] == 'off'."""
    r = run('history_forecast', T_GANNON, 360, 720, [0, 240], disabled={'goes': 'off', 'kp': False},
            fetched=_fetched(), now=T_GANNON)
    assert r.S['sources']['noaa_swpc_goes']['state'] == 'off'
    assert r.S['sources']['gfz_kp']['state'] != 'off'
    r2 = run('history_forecast', T_GANNON, 360, 720, [0, 240], fetched=_fetched(), now=T_GANNON)
    assert all(v.get('state') != 'off' for k, v in r2.S['sources'].items() if not k.startswith('_'))


# ------------------------------------------------- слои и покрытие флюенса
def test_sloi_ne_pripisyvayut_L_i_BB0_centralnomu_dipolyu():
    """В одном архиве не должно быть двух взаимоисключающих заявлений о модели поля."""
    from vkd.integration.orbit_bridge import ORBIT_SRC
    head = ORBIT_SRC.split('L и B/B0')[1].split(';')[0]
    assert 'эксцентричный диполь' in head, ORBIT_SRC
    assert 'центральный' not in head, ORBIT_SRC
    assert 'жёсткость' in ORBIT_SRC and 'центральный наклонённый диполь' in ORBIT_SRC


def test_pokrytie_flyuensa_nazyvaet_prichinu_ryadom_s_chislom(gannon):
    """«69 из 72 (96 %)» читалось как неполнота данных; причина — L вне сетки — стояла
    только в перечне статусов строкой ниже."""
    note = next(n for w in gannon.S['windows'] for m in w['mechanisms']
                for f in m['factors'] if f['name'].startswith('флюенс') for n in [f['limits']]
                if 'точки аномалии' in n)
    m = re.search(r'точки аномалии со значением потока по таблице ОСТ: (\d+) из (\d+)', note)
    assert m, note
    if m.group(1) != m.group(2):
        assert 'L вне сетки' in note, note
