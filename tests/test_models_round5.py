# -*- coding: utf-8 -*-
"""Пятый круг, область «модели»: выгрузка говорит то же, что экран, и ни одна фраза расчёта
не опровергается числами того же расчёта.

Каждый тест закрепляет одну подтверждённую прогоном находку пятого круга:

  * выгрузка печатала число там, где экран печатает прочерк (GOES, покрытие окна 0 %);
  * карточка флюенса связывала словом «из них» два непересекающихся множества точек;
  * заметка Kp утверждала исключение источника, которого не было («источник исключён ИЛИ …»);
  * строка об устойчивости в отчёте опровергала вердикт того же отчёта и расходилась с экраном;
  * правило флюенса печатало «всенаправленный» дважды подряд и скобку в скобке;
  * заметка GOES печатала уровень по шкале S дважды и со скобкой в скобке;
  * множество адресов первоисточников отчёта было беднее экранного.
"""
import re
from datetime import datetime, timedelta, timezone

import pytest

from app.compute import run
from app.export import _as_screen_factor, report_md
from app.ui import factor_value_ru, fmt, raw_record, record_url, robustness_line_ru, screen_text
from tests.test_integration import TLE, _fetched
from vkd.assess.trapped import BeltTable
from vkd.types import EnvironmentSample, Kind, MagMethod, TrajectoryPoint, Window
from vkd.windows.compare import Thresholds, assess_window

UTC = timezone.utc
T_GANNON = datetime(2024, 5, 10, 12, 0, tzinfo=UTC)
T_QUIET = datetime(2024, 6, 25, 12, 0, tzinfo=UTC)
T_LIVE = datetime(2026, 9, 19, 6, 0, tzinfo=UTC)


# ------------------------------------------------------------------ три режима одного расчёта
@pytest.fixture(scope='module')
def gannon():
    return run('history_forecast', T_GANNON, 360, 720, [0, 240], fetched=_fetched(), now=T_GANNON)


@pytest.fixture(scope='module')
def quiet():
    return run('history_forecast', T_QUIET, 360, 1440, [0, 480], fetched=_fetched(), now=T_QUIET)


@pytest.fixture(scope='module')
def live():
    """Текущий режим с наблюдением GOES: горизонт наблюдения (60 мин) покрывает окно 1 частично
    и окно 2 (+240 мин) не покрывает вовсе — тот самый случай, где экран печатает прочерк."""
    from vkd.orbit.trajectory import satellite_from_tle
    epoch = satellite_from_tle(open(TLE, 'rb').read()).epoch.utc_datetime()
    t0 = (epoch + timedelta(days=1)).replace(second=0, microsecond=0)
    return run('live', t0, 360, 720, [0, 240], fetched=_fetched(open(TLE, encoding='utf-8').read(), goes_at=t0), now=t0)


@pytest.fixture(scope='module')
def three(gannon, quiet, live):
    return {'Гэннон': gannon, 'Тихая дата': quiet, 'Сейчас': live}


def _factor_line(md: str, window_index: int, name: str) -> str:
    """Строка величины отчёта внутри раздела нужного окна."""
    parts = md.split('\n### Окно ')
    body = next(p for p in parts[1:] if p.startswith('%d ' % window_index))
    return next(l for l in body.split('\n') if l.strip().startswith('- %s: ' % name))


def _value_field(line: str) -> str:
    """То, что отчёт печатает на месте величины: между «имя: » и « — происхождение»."""
    return line.split(': ', 1)[1].rsplit(' — ', 1)[0]


def _nested_parens(s: str) -> bool:
    depth = 0
    for ch in s:
        if ch == '(':
            depth += 1
            if depth > 1:
                return True
        elif ch == ')':
            depth = max(0, depth - 1)
    return False


# ------------------------------------------- (1) отчёт и экран печатают одну и ту же величину
def test_otchyot_pechataet_tu_zhe_velichinu_chto_ekran(three):
    """Экран и выгрузка строятся из одного снимка и обязаны говорить одно.

    `app.ui.factor_value_ru` намеренно печатает прочерк, когда горизонт наблюдения не
    покрывает окно ни на одну минуту, а `app.export._value` этого правила не знал и печатал
    число: в режиме «Сейчас» в таблице 1 стояло «—», а в report.md — «0,22 pfu».
    """
    checked = 0
    for label, r in three.items():
        md = report_md(r.S, r.raw_records)
        for w in r.S['windows']:
            for m in w['mechanisms']:
                for f in m['factors']:
                    screen = factor_value_ru(_as_screen_factor(f), f['unit'])
                    val = _value_field(_factor_line(md, w['index'], f['name']))
                    if screen == '—':
                        assert 'не определено' in val, (label, w['index'], f['name'], val)
                        if f['value'] is not None:
                            assert fmt(f['value']) not in val, (label, w['index'], f['name'], val)
                    else:
                        # Kp отчёт называет по имени шкалы («Kp 7,67»), число то же самое
                        assert val in (screen, 'Kp ' + screen), (label, w['index'], f['name'], val, screen)
                    checked += 1
    assert checked >= 30, checked


def test_goes_bez_pokrytiya_okna_ne_daet_chisla_v_otchyote(live):
    """Окно 2 текущего режима: наблюдение кончилось за 185 мин до его начала."""
    f = next(f for w in live.S['windows'] if w['index'] == 2 for m in w['mechanisms']
             for f in m['factors'] if f['name'].startswith('поток протонов GOES'))
    assert f['value'] is not None and 'покрывает 0 % окна' in f['limits']
    val = _value_field(_factor_line(report_md(live.S, live.raw_records), 2, f['name']))
    assert val.startswith('значение окна не определено'), val
    assert 'не покрывает окно' in val and fmt(f['value']) not in val, val
    # то же окно в отчёте прямо говорит, чего не хватает, — утверждения не расходятся
    assert 'покрывает 0 % окна' in report_md(live.S, live.raw_records)


# ------------------------------------------- (2) карточка флюенса не складывает разные множества
def _belt_traj(n_ok: int, n_nomodel: int, n_mirror: int):
    """Трасса, где у точек аномалии ровно три исхода таблицы ОСТ: значение есть (L = 2, B/B0 = 1,2),
    L вне сетки (L = 1,05) и точка выше точки отражения (L = 2, B/B0 = 100 — за последним узлом)."""
    out = []
    spec = [(2.0, 1.2)] * n_ok + [(1.05, 1.2)] * n_nomodel + [(2.0, 100.0)] * n_mirror
    for i, (L, bb) in enumerate(spec):
        out.append(TrajectoryPoint(T_GANNON + timedelta(minutes=i), 0.0, 0.0, 420.0, 20000.0,
                                   L, bb, None, MagMethod.DIPOLE, 'approximation', True))
    return out


def test_kartochka_flyuensa_ne_podayot_odno_mnozhestvo_chastyu_drugogo():
    """«у 3 точек L вне сетки; ИЗ НИХ 4 выше точки отражения» — из трёх четыре.

    Точка без модели (`no_model_L`) значения не имеет, точка выше точки отражения имеет
    значение 0,0 (`vkd/assess/trapped.py`) и в число «со значением» ВХОДИТ: множества
    не пересекаются, и связывать их словом «из них» нельзя.
    """
    tr = _belt_traj(n_ok=50, n_nomodel=3, n_mirror=4)
    a = assess_window(Window(T_GANNON, len(tr)), tr, BeltTable('min'), None, None, [], Thresholds(), T_GANNON,
                      mmod_hits=1e-6)
    note = next(f.limits_note for m in a.mechanisms for f in m.factors if f.name.startswith('флюенс'))
    head = next(p for p in note.split('; ') if p.startswith('точки аномалии'))
    assert 'из них' not in note, note
    m = re.search(r'(\d+) из (\d+)', head)
    assert m and (int(m.group(1)), int(m.group(2))) == (54, 57), head      # 50 со значением + 4 нулевых
    assert 'у 3 точек L вне сетки' in note, note
    assert 'ещё у 4 точек значение известно и равно нулю' in note, note


@pytest.mark.parametrize('label', ['Гэннон', 'Тихая дата', 'Сейчас'])
def test_chisla_kartochki_flyuensa_ne_prevyshayut_chisla_tochek(three, label):
    """Сумма названных точек не больше общего числа точек аномалии ни в одном режиме."""
    seen = 0
    for w in three[label].S['windows']:
        for m in w['mechanisms']:
            for f in m['factors']:
                if not f['name'].startswith('флюенс'):
                    continue
                note = f['limits'] or ''
                assert 'из них' not in note, note
                head = next((p for p in note.split('; ') if p.startswith('точки аномалии')), None)
                if head is None:
                    continue
                m_ok = re.search(r'(\d+) из (\d+)', head)
                n_model, n_saa = int(m_ok.group(1)), int(m_ok.group(2))
                m_no = re.search(r'у (\d+) точек L вне сетки', note)
                m_mir = re.search(r'у (\d+) точек значение известно', note)
                n_no = int(m_no.group(1)) if m_no else 0
                n_mir = int(m_mir.group(1)) if m_mir else 0
                assert n_model + n_no == n_saa, (label, note)
                assert n_no + n_mir <= n_saa, (label, note)
                assert n_mir <= n_model, (label, note)
                seen += 1
    assert seen >= 2, label


# ------------------------------------------- (3)/(4) Kp говорит то, что слой знает
@pytest.mark.parametrize('label', ['Гэннон', 'Тихая дата', 'Сейчас'])
def test_otchyot_ne_utverzhdaet_isklyucheniya_istochnika_kotorogo_ne_bylo(three, label):
    """В отчёте стояло «Kp: наблюдения нет (источник исключён ИЛИ публикации до отсечки нет)»,
    а тремя строками выше — «источники, отключённые пользователем: нет». Причину исключения
    называет блок состояния источников, слой сравнения знает только про отсутствие значения."""
    r = three[label]
    md = report_md(r.S, r.raw_records)
    assert '- источники, отключённые пользователем: нет' in md, label
    # блоку состояния источников говорить об исключении можно — он знает признак state;
    # проверяем всё, что стоит ДО него: вывод, окна, «что повлияло», «чего не хватает»
    before = md.split('## Источники и публикация')[0]
    assert 'источник исключён' not in before, [l for l in before.split('\n') if 'источник исключён' in l]
    assert 'исключён или' not in md and 'исключён ИЛИ' not in md, label


def test_kp_bez_nablyudeniya_nazyvaet_prichinu_po_rezhimu(gannon):
    note = next(n for w in gannon.S['windows'] for m in w['mechanisms']
                for n in m['coverage_notes'] if n.startswith('Kp: наблюдения нет'))
    assert note == 'Kp: наблюдения нет — записей с доказанной публикацией до отсечки нет', note


def test_kp_bez_otsechki_govorit_pro_istochnik():
    """Без отсечки причина другая: отсечки нет, а значения источник не дал."""
    tr = _belt_traj(n_ok=30, n_nomodel=0, n_mirror=0)
    a = assess_window(Window(T_GANNON, len(tr)), tr, BeltTable('min'), None, None, [], Thresholds(), T_GANNON,
                      mmod_hits=1e-6, cutoff_utc=None)
    note = next(n for m in a.mechanisms for n in m.coverage_notes if n.startswith('Kp: наблюдения нет'))
    assert note == 'Kp: наблюдения нет — источник значения не дал', note


# ------------------------------------------- (5) устойчивость: отчёт слово в слово как экран
@pytest.mark.parametrize('label', ['Гэннон', 'Тихая дата', 'Сейчас'])
def test_stroka_ob_ustoychivosti_v_otchyote_sovpadaet_s_ekranom(three, label):
    """Отчёт печатал профессиональное основание допуска целиком: «порядок окон при нулевом
    допуске не определён… ВЫБОР МЕНЯЕТСЯ», тогда как выше в том же отчёте стояло «Есть
    предпочтительное окно: окно 2». Экран в этом месте говорит другое и другими словами."""
    r = three[label]
    md = report_md(r.S, r.raw_records)
    th = r.S['request']['thresholds']
    screen = screen_text(robustness_line_ru(r.rec, r.S, th['saa_B_threshold_nT'], th['e_min_MeV']))
    line = next(l for l in md.split('\n') if l.startswith('Устойчивость выбора: '))
    assert line == 'Устойчивость выбора: ' + screen, (line, screen)
    assert 'ВЫБОР МЕНЯЕТСЯ' not in md and 'порядок окон при нулевом допуске' not in md
    assert '22000' not in md and '24000' not in md, [l for l in md.split('\n') if '22000' in l or '24000' in l]


@pytest.mark.parametrize('label', ['Гэннон', 'Тихая дата', 'Сейчас'])
def test_dopusk_v_otchyote_ostayotsya_dopuskom(three, label):
    """Строка допуска печатает только части про допуск и теми же разрядами, что экран."""
    r = three[label]
    line = next(l for l in report_md(r.S, r.raw_records).split('\n') if l.startswith('Допуск равнозначности: '))
    body = line.split(': ', 1)[1]
    assert body.startswith('допуск'), body
    for part in body.rstrip('.').split('; '):
        assert part.startswith('допуск'), (label, part)
    # разряды тысяч — как на экране: «22 000», а не «22000»
    if 'нТл' in body:
        assert re.search(r'22\s000', body), body


# ------------------------------------------- (8) единицы потока называются один раз
@pytest.mark.parametrize('label', ['Гэннон', 'Тихая дата', 'Сейчас'])
def test_pravilo_flyuensa_ne_povtoryaet_slovo_i_standart(three, label):
    rule = next(f['rule'] for w in three[label].S['windows'] for m in w['mechanisms']
                for f in m['factors'] if f['name'].startswith('флюенс'))
    first = rule.split('; интерполяция')[0]
    assert first.count('всенаправленный') == 1, first
    assert first.count('ОСТ 134-1044-2007') == 1, first
    assert not _nested_parens(first), first
    assert 'единицы потока: см⁻²·с⁻¹' in first, first


# ------------------------------------------- (9) уровень по шкале S — один раз и без скобки в скобке
def test_zametka_goes_nazyvaet_uroven_odin_raz(live):
    for w in live.S['windows']:
        note = next(f['limits'] for m in w['mechanisms'] for f in m['factors']
                    if f['name'].startswith('поток протонов GOES'))
        assert note.count('ниже S1') == 1, (w['index'], note)
        assert note.count('фон') == 1, (w['index'], note)
        assert not _nested_parens(note), (w['index'], note)
        assert 'наблюдение' in note and 'фон (ниже S1)' in note, note


# ------------------------------------------- (11) адреса первоисточников отчёта не беднее экранных
@pytest.mark.parametrize('label', ['Гэннон', 'Тихая дата', 'Сейчас'])
def test_adresa_otchyota_ne_bednee_ekrannyh(three, label):
    """Экран берёт адрес в трёх местах: карточки объяснения, выпуски прогноза NOAA и таблица
    «События и прогнозы». На «Гэннон» отчёт давал 5 адресов против 13 экранных: уведомления,
    не ставшие условием, попадали в отчёт только номерами."""
    r = three[label]
    md = report_md(r.S, r.raw_records)
    in_report = set(re.findall(r'https?://[^\s)|]+', md))
    on_screen = set()
    for c in r.cards:
        on_screen |= {u for rid in c.record_ids for u in (record_url(raw_record(r.raw_records, rid)),) if u}
    on_screen |= {u for line in (r.S.get('forecasts') or []) if line.get('record')
                  for u in (record_url(raw_record(r.raw_records, line['record'])),) if u}
    on_screen |= {u for e in (r.events or [])
                  for u in (record_url(raw_record(r.raw_records, e.raw_record_id)),) if u}
    assert on_screen <= in_report, sorted(on_screen - in_report)


def test_sobytiya_gorizonta_perechisleny_s_adresami(gannon):
    md = report_md(gannon.S, gannon.raw_records)
    used = gannon.S['history']['events_used']
    assert used and '## События и прогнозы, учтённые на горизонте' in md
    for e in used:
        u = record_url(raw_record(gannon.raw_records, e['id']))
        if u:
            assert u in md, e['id']
    assert 'nasa_donki_notification' not in md, 'идентификатор записи остаётся в manifest.json'


def test_sobytie_posle_otsechki_pechataet_adres_esli_zapis_est(gannon):
    """Ветка адреса в перечне «после отсечки»: запись с адресом печатается ссылкой."""
    ver = gannon.S['verification']
    assert ver.get('events'), 'на «Гэннон» уведомления после отсечки есть'
    rid = ver['events'][0]['id']
    raw = dict(gannon.raw_records)
    raw[rid.rsplit(':', 1)[0]] = {'metadata': {'url': 'https://example.invalid/DONKI/proverka'}}
    md = report_md(gannon.S, raw)
    assert 'https://example.invalid/DONKI/proverka' in md


# ------------------------------------------- подтверждение пунктов, закрытых четвёртым кругом
def test_punkty_chetvyortogo_kruga_ostayutsya_zakrytymi(quiet, live):
    """Окно через полночь с датой конца, отсутствие идентификаторов кода вне таблиц и служба,
    которая данные действительно отдала, — проверено прогоном на этом коммите."""
    md_q = report_md(quiet.S, quiet.raw_records)
    assert '25.06 20:00 — 26.06 02:00 UTC (360 мин)' in md_q
    body = re.sub(r'https?://\S+', '', md_q)
    assert not re.findall(r'(?<![\w/.:-])[a-z][a-z0-9]*_[a-z0-9_]+', body)
    assert not re.search(r'\b[0-9a-f]{32,}\b', body)
    tm = live.S['trajectory_meta']
    traj_line = next(l for l in report_md(live.S, live.raw_records).split('\n')
                     if l.startswith('- SGP4') or l.startswith('- эфемериды'))
    assert 'celestrak_gp' not in traj_line and 'nasa_jsc_oem' not in traj_line, traj_line
    if tm.get('tle_url'):
        assert tm['tle_url'] in traj_line, traj_line


# ------------------------------------------- вспомогательное: переходник снимка к функциям экрана
def test_perehodnik_snimka_vidit_ogranichenie_faktora():
    f = {'name': 'поток протонов GOES ≥10 МэВ', 'value': 0.2, 'unit': 'pfu',
         'limits': 'наблюдение 2026-09-19 05:50Z; горизонт наблюдения до 06:50Z покрывает 0 % окна'}
    assert factor_value_ru(_as_screen_factor(f), f['unit']) == '—'
    f2 = dict(f, limits=f['limits'].replace('0 %', '80 %'))
    assert factor_value_ru(_as_screen_factor(f2), f2['unit']) == '0,2 pfu'


def test_edinica_potoka_bez_standarta_ne_teryaet_proishozhdeniya():
    from vkd.assess.trapped import FLUX_UNIT_RU, FLUX_UNIT_SHORT_RU
    t = BeltTable('min')
    assert t.flux_unit_ru == FLUX_UNIT_RU and 'ОСТ 134-1044-2007' in FLUX_UNIT_RU
    assert t.flux_unit_short_ru == FLUX_UNIT_SHORT_RU and 'прил. А, вводный текст' in FLUX_UNIT_SHORT_RU
    assert 'ОСТ' not in FLUX_UNIT_SHORT_RU and 'ОСТ 134-1044-2007' in t.source
