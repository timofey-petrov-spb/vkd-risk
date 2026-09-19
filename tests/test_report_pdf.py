# -*- coding: utf-8 -*-
"""Отчёт PDF: собирается, читается обратно, совпадает со снимком расчёта (ТЗ круга 12, п. 8).

Проверки здесь отвечают на четыре вопроса, и каждый из них уже был причиной поломки в этом
проекте или в соседних:

  1. КИРИЛЛИЦА ПЕЧАТАЕТСЯ. Встроенные шрифты reportlab кириллицы не содержат; проверка не
     верит сборке на слово, а извлекает текст из ГОТОВОГО файла посторонней библиотекой (pypdf)
     и ищет в нём русские слова. Квадраты вместо букв этой проверки не прошли бы.
  2. ШЕСТЬ РАЗДЕЛОВ В ЗАДАННОМ ПОРЯДКЕ — по списку из самого модуля, а не по копии строк здесь:
     переименуют раздел — тест сверит новое имя, переставят местами — тест упадёт.
  3. ЧИСЛА СОВПАДАЮТ. Числа берутся из снимка тем же форматером, что печатает экран, и должны
     найтись и в PDF, и в отчёте разметкой. Это и есть сверка «экран — отчёт — выгрузка» на
     одном расчёте.
  4. ОТЧЁТ НЕ РОНЯЕТ ВЫГРУЗКУ. Нет шрифта с кириллицей — PDF не собирается, но архив собирается,
     и в нём лежит причина по-русски.
"""
import io
import json
import os
import re
import zipfile

import pytest

from app import report_pdf as R
from app.export import build_zip, fmt, report_md

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, 'examples')

# Примеры для тяжёлых проверок: по одному на каждый вид ответа перебора, чтобы проверялись
# РАЗНЫЕ ветки текста, а не одна и та же четыре раза.
#   quiet      — равнозначные начала, ответ промежутком;
#   gannon     — все начала под условием проверки, ответа нет, условия печатаются;
#   refusal    — ранжировать не по чему (пользователь исключил источник протонов);
#   live_now   — текущий режим: живые источники с давностью в минутах.
CASES = ('quiet_2024-05-03_12Z', 'gannon_2024-05-10_cutoff12Z', 'refusal_goes_off', 'live_now')
ALL_EXAMPLES = tuple(sorted(os.path.splitext(os.path.basename(p))[0]
                            for p in os.listdir(EXAMPLES) if p.endswith('.json')))

_CACHE: dict[str, tuple[dict, bytes, str]] = {}


def _snapshot(name: str) -> dict:
    with io.open(os.path.join(EXAMPLES, name + '.json'), encoding='utf-8') as f:
        return json.load(f)


def _pdf_text(blob: bytes) -> tuple[int, str]:
    """Страницы и текст ГОТОВОГО файла, прочитанные посторонней библиотекой."""
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(blob))
    return len(reader.pages), '\n'.join(p.extract_text() for p in reader.pages)


def built(name: str) -> tuple[dict, bytes, str]:
    """Снимок, готовый PDF и извлечённый из него текст. Сборка одного примера стоит около
    полусекунды, поэтому результат держится в памяти на весь прогон файла."""
    if name not in _CACHE:
        S = _snapshot(name)
        pdf, reason = R.build_pdf_or_reason(S)
        assert pdf, 'PDF не собран на примере %s: %s' % (name, reason)
        _pages, text = _pdf_text(pdf)
        _CACHE[name] = (S, pdf, text)
    return _CACHE[name]


def _plain(text: str) -> str:
    """Текст без узких и неразрывных пробелов и без переносов: в PDF строка рвётся по ширине
    колонки, и число «2,45·10⁶» может оказаться разорванным переносом ячейки."""
    return text.replace(' ', '').replace(' ', '').replace('\n', ' ')


# ----------------------------------------------------------------- 1. файл собирается и читается
@pytest.mark.parametrize('name', ALL_EXAMPLES)
def test_pdf_sobiraetsya_na_kazhdom_sohranyonnom_primere(name):
    """На каждом сохранённом примере PDF собирается и открывается: файл не пустой, страницы есть."""
    S = _snapshot(name)
    pdf, reason = R.build_pdf_or_reason(S)
    assert pdf, 'PDF не собран на примере %s: %s' % (name, reason)
    assert pdf[:5] == b'%PDF-', 'файл не начинается заголовком PDF'
    pages, text = _pdf_text(pdf)
    # Шесть разделов с таблицами не помещаются на две страницы ни на одном примере; верхняя
    # граница поставлена вдвое выше самого толстого примера (145 перебранных начал → 7 страниц).
    assert 3 <= pages <= 20, 'страниц %d — это не похоже на отчёт из шести разделов' % pages
    assert len(text) > 3000, 'из готового файла извлеклось меньше 3000 знаков: текст не печатается'


@pytest.mark.parametrize('name', CASES)
def test_kirillica_izvlekaetsya_obratno_a_ne_stoit_kvadratami(name):
    """Главная проверка шрифта: русские слова читаются из готового файла.

    Квадрат или пустой глиф при извлечении даёт либо другой знак, либо ничего, поэтому
    проверяется не «есть ли текст», а есть ли конкретные русские слова документа и какова
    доля кириллицы в нём."""
    _S, _pdf, text = built(name)
    for word in ('Рекомендация', 'Входные данные', 'Границы применимости', 'источник',
                 'длительность выхода'.split()[0], 'минут'):
        assert word.lower() in text.lower(), 'в готовом PDF нет слова «%s»' % word
    cyr = sum(1 for c in text if 'а' <= c.lower() <= 'я' or c.lower() == 'ё')
    assert cyr > 0.3 * len(text.replace(' ', '').replace('\n', '')), \
        'кириллицы в извлечённом тексте меньше трети — похоже, часть букв не напечаталась'
    # Надстрочные степени и знаки величин — тоже из шрифта, и они тоже обязаны читаться.
    assert '·10' in text and ('⁶' in text or '⁻' in text), 'степенная запись в PDF не читается'


@pytest.mark.parametrize('name', CASES)
def test_v_gotovom_fayle_net_znakov_vne_pechatnogo_diapazona(name):
    """Ни одного управляющего знака: они дают пустой глиф и рвут строку посреди ячейки."""
    _S, _pdf, text = built(name)
    bad = {c for c in text if ord(c) < 32 and c != '\n'} | {c for c in text if 0x7f <= ord(c) <= 0x9f}
    assert not bad, 'в тексте PDF знаки вне печатного диапазона: %s' % sorted(hex(ord(c)) for c in bad)
    assert '**' not in text, 'в PDF просочилась разметка отчёта md'


# ----------------------------------------------------------------- 2. шесть разделов по порядку
@pytest.mark.parametrize('name', ALL_EXAMPLES)
def test_shest_razdelov_idut_v_zadannom_poryadke(name):
    """Шесть разделов, все и строго в том порядке, который назвал владелец."""
    S = _snapshot(name)
    pdf, reason = R.build_pdf_or_reason(S)
    assert pdf, reason
    _pages, text = _pdf_text(pdf)
    flat = _plain(text)
    positions = []
    for title in R.SECTION_TITLES:
        i = flat.find(title)
        assert i >= 0, 'в PDF нет раздела «%s»' % title
        positions.append(i)
    assert positions == sorted(positions), \
        'разделы идут не в том порядке: %s' % list(zip(R.SECTION_TITLES, positions))
    assert len(R.SECTION_TITLES) == 6


@pytest.mark.parametrize('name', CASES)
def test_kazhdyy_razdel_neset_to_chto_trebuet_tz(name):
    """Содержимое разделов — по описи ТЗ, а не по названию: титул называет версию и коммит,
    входные данные — длительность и источники, приложение — перебранные начала."""
    S, _pdf, text = built(name)
    flat = _plain(text)
    assert S['algorithm_version'] in flat and 'Коммит кода' in flat
    assert 'Длительность выхода' in flat and 'Отсечка публикации' in flat
    assert 'давность' in flat and 'получено' in flat          # источники с моментом получения
    assert 'происхождение' in flat and 'единица' in flat      # колонки таблицы факторов
    assert 'Когда выходить' in flat or 'Перебор начал выхода не делался' in flat
    assert 'Учтено:' in flat and 'Не учтено:' in flat
    if S.get('scan'):
        assert 'Перебрано начал' in flat


# ----------------------------------------------------------------- 3. числа совпадают
@pytest.mark.parametrize('name', CASES)
def test_chisla_pdf_sovpadayut_so_snimkom_rascheta(name):
    """Каждое число, объявленное в `numbers_from_snapshot`, стоит в готовом PDF.

    Перечень строится ИЗ СНИМКА тем же форматером, что печатает экран, а не из документа:
    иначе проверка сверяла бы документ сам с собой."""
    S, _pdf, text = built(name)
    expected = R.numbers_from_snapshot(S)
    assert len(expected) >= 5, 'сверять нечего: из снимка не собралось и пяти величин'
    assert R.missing_numbers(S, text) == {}, 'числа PDF разошлись со снимком расчёта'


@pytest.mark.parametrize('name', CASES)
def test_te_zhe_chisla_stoyat_i_v_otchyote_razmetkoy(name):
    """Сквозная сверка экран — отчёт — выгрузка: те же значения, что в PDF, стоят и в report.md.

    Оба документа строятся из одного снимка одним форматером, поэтому расхождение здесь
    означало бы, что одна из двух дорог печати завела число не туда."""
    S, _pdf, _text = built(name)
    md = _plain(report_md(S))
    missing = {k: v for k, v in R.numbers_from_snapshot(S).items() if v.replace(' ', '') not in md}
    assert missing == {}, 'числа PDF не нашлись в отчёте разметкой: %s' % missing


def test_znacheniya_faktorov_berutsya_iz_snimka_a_ne_pereschityvayutsya():
    """Значение фактора в PDF — ровно то, что лежит в снимке, с точностью печати.

    Проверяется не совпадение строк, а число: из PDF берётся напечатанное значение минут в
    аномалии и сравнивается со значением снимка, округлённым правилом печати."""
    S, _pdf, text = built('quiet_2024-05-03_12Z')
    c = R._recommended_candidate(S)
    assert c is not None
    printed = fmt(c['saa_min'])
    assert printed in _plain(text)
    assert abs(float(printed.replace(',', '.')) - c['saa_min']) < 0.01


# ----------------------------------------------------------------- 4. популярное объяснение
@pytest.mark.parametrize('name', CASES)
def test_dva_predlozheniya_obychnymi_slovami(name):
    """Ровно два предложения, без терминов, с числами из снимка — или честная строка о том,
    что времени сервис не назвал (ТЗ круга 12, п. 5)."""
    S, _pdf, _text = built(name)
    why = R.plain_why_ru(S)
    for banned in ('флюенс', 'част./см', 'геомагнит', 'Kp'):
        assert banned not in why, 'в популярном объяснении термин «%s»' % banned
    if R._recommended_candidate(S) is None:
        assert 'не назвал время выхода' in why
        return
    sentences = [s for s in re.split(r'(?<=[.!?])\s+', why.strip()) if s]
    assert len(sentences) == 2, 'предложений %d, а надо два: %s' % (len(sentences), why)
    assert re.search(r'\d', sentences[0]) and re.search(r'\d', sentences[1]), 'оба предложения — с числами'
    assert 'минут' in why and ('раз' in why or 'не набирается' in why)


@pytest.mark.parametrize('value,expected', [
    (1.0, 'минута'), (2.0, 'минуты'), (4.0, 'минуты'), (5.0, 'минут'), (11.0, 'минут'),
    (21.0, 'минута'), (107.0, 'минут'), (5.69, 'минуты'), (0.5, 'минуты'),
])
def test_sklonenie_minut_v_populyarnoy_fraze(value, expected):
    """Русский счёт: «1 минута», «2 минуты», «5 минут», «21 минута», «5,7 минуты».
    Ошибка склонения в двух предложениях, которые человек читает первыми, видна сразу."""
    assert R._minutes_ru(value).endswith(expected), R._minutes_ru(value)


@pytest.mark.parametrize('value,expected', [
    (1.5, 'в 1,5 раза'), (2.4, 'в 2,4 раза'), (5.0, 'в 5 раз'), (21.0, 'в 21 раз'),
    (22.0, 'в 22 раза'), (600.0, 'в 600 раз'), (1412.0, 'в 1400 раз'),
])
def test_sklonenie_raz_v_populyarnoy_fraze(value, expected):
    """«в 2,4 раза», «в 21 раз», «в 600 раз»; отношение округляется до двух значащих цифр."""
    assert R._times_ru(value) == expected


def test_populyarnoe_obyasnenie_nazyvaet_hudsheye_vremya_i_pik():
    """Числа объяснения — те самые, что в снимке: худшее время по минутам и время пика."""
    S, _pdf, text = built('quiet_2024-05-03_12Z')
    cands = S['scan']['candidates']
    worst = max(c['saa_min'] for c in cands if c['saa_min'] is not None)
    peak = max((c['fluence'], c['start_utc']) for c in cands if c['fluence'] is not None)
    why = R.plain_why_ru(S)
    assert '%d минут' % round(worst) in why, why
    assert peak[1][11:16] in why, 'в объяснении нет времени пика %s' % peak[1]
    assert why in _plain(text), 'объяснение из PDF не совпадает с функцией, которая его строит'


def test_dva_predlozheniya_ne_vydayutsya_za_rekomendaciyu_kogda_vremeni_net():
    """Когда сервис времени не назвал, популярной фразы в PDF нет вовсе: лучшие начала перебор
    отмечает всегда, и фраза «в этот промежуток воздействия наименьшие» стала бы рекомендацией,
    которой сервис не давал."""
    S, _pdf, text = built('gannon_2024-05-10_cutoff12Z')
    assert R._recommended_candidate(S) is None
    assert 'станция меньше всего времени проводит' not in _plain(text)
    assert 'сервис не называет начало' in _plain(text)


# ----------------------------------------------------------------- 5. без перебора
def test_bez_perebora_razdely_govoryat_pryamo_a_ne_pokazyvayut_pustotu():
    """Снимок без перебора: разделы на месте, но вместо пустой таблицы стоит прямая строка."""
    S = _snapshot('quiet_2024-05-03_12Z')
    S.pop('scan')
    pdf, reason = R.build_pdf_or_reason(S)
    assert pdf, reason
    _pages, text = _pdf_text(pdf)
    flat = _plain(text)
    for title in R.SECTION_TITLES:
        assert title in flat
    assert 'Перебор начал выхода не делался' in flat
    assert 'перебор не делался' in flat.lower()
    assert 'Перебрано начал:' not in flat


# ----------------------------------------------------------------- 6. шрифт
def test_shrift_bez_kirillicy_otvergaetsya_s_nazvaniem_nepokrytyh_znakov(monkeypatch):
    """Шрифт без кириллицы не берётся «за неимением лучшего»: сборка отказывается и называет
    знаки, которых в нём нет. Vera.ttf из самого reportlab — как раз такой шрифт."""
    import reportlab
    vera = os.path.join(os.path.dirname(os.path.abspath(reportlab.__file__)), 'fonts', 'Vera.ttf')
    assert os.path.exists(vera), 'в пакете reportlab нет Vera.ttf — проверку надо переписать'
    monkeypatch.setattr(R, '_font_candidates', lambda: [('Vera', vera, None)])
    with pytest.raises(R.PdfNotBuilt) as err:
        R.choose_font(set('Рекомендация'))
    assert 'нет шрифта' in str(err.value) and 'U+04' in str(err.value)


def test_vzyatyy_shrift_nazvan_v_svoystvah_fayla():
    """Каким шрифтом набрано — записано в свойствах файла: при разборе «почему буквы такие»
    это первый вопрос, и ответ на него не должен требовать чтения кода."""
    import pypdf
    _S, pdf, _text = built('quiet_2024-05-03_12Z')
    meta = pypdf.PdfReader(io.BytesIO(pdf)).metadata
    assert meta is not None and 'ВКД-Риск' in (meta.get('/Creator') or '')
    assert '.ttf' in (meta.get('/Creator') or '').lower(), 'в свойствах не назван файл шрифта'


def test_font_zadannyy_operatorom_idyot_pervym(monkeypatch):
    """Путь из переменной окружения — первый кандидат: так площадку чинят без правки кода."""
    _S, _pdf, _text = built('quiet_2024-05-03_12Z')
    chosen = R.choose_font(set('Рекомендация 10⁶'))
    monkeypatch.setenv(R.FONT_ENV, chosen.regular_path)
    first = R._font_candidates()[0]
    assert first[1] == chosen.regular_path and R.FONT_ENV in first[0]


def test_bez_shrifta_s_kirillicey_vygruzka_vsyo_ravno_sobiraetsya(monkeypatch):
    """Нет ни одного подходящего шрифта — приложение не падает: PDF в архиве нет, зато есть
    файл с причиной по-русски, а весь остальной архив на месте."""
    monkeypatch.setattr(R, '_font_candidates', lambda: [])
    S = _snapshot('quiet_2024-05-03_12Z')
    pdf, reason = R.build_pdf_or_reason(S)
    assert pdf is None and 'шрифт' in reason
    blob = build_zip(S, {})
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = z.namelist()
        assert 'report.pdf' not in names
        assert 'report_pdf_ne_sobran.txt' in names
        note = z.read('report_pdf_ne_sobran.txt').decode('utf-8')
        assert 'Отчёт PDF не собран' in note and 'шрифт' in note
        for required in ('report.md', 'request.json', 'recommendation.json', 'sources.json', 'manifest.json'):
            assert required in names, 'выгрузка потеряла %s из-за отчёта PDF' % required


def test_otkaz_verstki_ne_ronyaet_vygruzku(monkeypatch):
    """Любая поломка вёрстки — тоже причина в архиве, а не исключение наружу."""
    def boom(_S):
        raise RuntimeError('сломанная вёрстка')
    monkeypatch.setattr(R, 'build_pdf', boom)
    S = _snapshot('refusal_goes_off')
    pdf, reason = R.build_pdf_or_reason(S)
    assert pdf is None and 'сломанная вёрстка' in reason
    with zipfile.ZipFile(io.BytesIO(build_zip(S, {}))) as z:
        assert 'report_pdf_ne_sobran.txt' in z.namelist()


# ----------------------------------------------------------------- 7. выгрузка
def test_pdf_lezhit_v_arhive_vygruzki_i_otkryvaetsya():
    """Готовый архив выгрузки содержит отчёт PDF, и он открывается прямо из архива."""
    S = _snapshot('quiet_2024-05-03_12Z')
    with zipfile.ZipFile(io.BytesIO(build_zip(S, {}))) as z:
        assert 'report.pdf' in z.namelist()
        blob = z.read('report.pdf')
    pages, text = _pdf_text(blob)
    assert pages >= 3 and 'Рекомендация' in text
    assert R.missing_numbers(S, text) == {}


def test_tablicy_ne_shire_polosy_nabora():
    """Ширины колонок объявлены в миллиметрах, и ни одна таблица не выходит за полосу набора:
    reportlab такую таблицу молча вывел бы за правое поле."""
    S = _snapshot('quiet_2024-05-03_12Z')
    for b in R.document_blocks(S):
        if b.kind == 'table':
            assert sum(b.widths_mm) <= R.TEXT_WIDTH_MM + 0.01, \
                'таблица шириной %.1f мм при полосе %.1f мм' % (sum(b.widths_mm), R.TEXT_WIDTH_MM)


def test_v_otchyote_net_klyuchey_koda_vmesto_imyon_istochnikov():
    """В колонке «источник» стоят имена источников, а не ключи записей с хешами."""
    _S, _pdf, text = built('quiet_2024-05-03_12Z')
    flat = _plain(text)
    for key in ('ecss_grun:', 'ost1044_A', 'nasa_jsc_oem:', 'igrf13:'):
        assert key not in flat, 'в PDF просочился ключ записи «%s»' % key
    assert 'таблицы ОСТ 134-1044-2007' in flat or 'модель метеороидов' in flat
