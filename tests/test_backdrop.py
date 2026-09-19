# -*- coding: utf-8 -*-
"""Проверки фона и анимаций (ТЗ, двенадцатый круг, пункт 7).

Что проверяется:
  * контраст текста к фону не ниже 4,5:1 в КАЖДОЙ паре на каждом фоне, который создаёт модуль,
    и наихудшая пара названа числом, а не «проходит»;
  * палитра модуля совпадает с :root в app/ui.py — иначе расчёт контраста сделан не для тех
    цветов и молча устарел бы;
  * небо детерминировано: одно зерно — одна и та же картинка, до координат;
  * звёзды не накладываются друг на друга, поэтому непрозрачности не складываются и наихудший
    фон равен непрозрачности одной звезды;
  * ни одной бесконечной анимации, у каждой явно записан один проход;
  * системная настройка «уменьшить движение» выключает все анимации до одной;
  * в разметке нет знаков вне печатного диапазона (ловушка восьмеричных escape), нет градиентов,
    нет эмодзи;
  * вес разметки меньше 40 КБ и назван числом;
  * подсветка изменившейся ячейки помечает ровно изменившиеся ячейки и падает, если структура
    приборной полосы в app/ui.py изменилась.
"""
from __future__ import annotations

import math
import os
import re

import pytest

from app import backdrop as b

UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app', 'ui.py')
# Диапазон значков из tests/test_ui_app.py — тот же, чтобы «нет эмодзи» означало здесь то же самое.
EMOJI = re.compile('[\U0001F300-\U0001FAFF☀-➿️]')


# ======================================================================== контраст

def test_kontrast_kazhdoy_pary_ne_nizhe_poroga():
    """Каждая пара «тон текста — фон, создаваемый модулем» проходит 4,5:1.

    Проверяются три фона: чистая страница, сердцевина самой яркой звезды и подсветка
    изменившейся ячейки. Других фонов модуль не создаёт — блоки и панели непрозрачны.
    """
    table = b.contrast_table()
    assert len(table) == 3 * len(b.TEXT_TONES), table
    failed = [(caption, ratio) for caption, _t, _bg, ratio in table if ratio < b.MIN_CONTRAST]
    assert not failed, failed
    worst = min(ratio for _c, _t, _bg, ratio in table)
    # Наихудшая пара — «данных нет» #9aa5b5 на сердцевине самой яркой звезды. Число записано
    # в docstring модуля; если оно поехало, поехал и расчёт, на который ссылается отчёт.
    assert round(worst, 2) == 4.64, worst


def test_yarkost_zvezdy_na_potolke_a_ne_vyshe():
    """Непрозрачность самой яркой звезды выбрана потолком расчёта, а не на глаз.

    Проверяем обе стороны: при выбранном значении порог проходит, при следующем круглом
    значении (0,20) — уже нет. Так видно, что 0,18 не случайное число.
    """
    alpha = b.MAGNITUDE_CLASSES[0][2]
    assert alpha == 0.18, alpha
    worst_now = min(b.contrast_ratio(tone, b.brightest_star_background())
                    for _n, tone in b.TEXT_TONES)
    assert worst_now >= b.MIN_CONTRAST, worst_now
    louder = b.blend(b.INK, 0.20, b.BG)
    worst_louder = min(b.contrast_ratio(tone, louder) for _n, tone in b.TEXT_TONES)
    assert worst_louder < b.MIN_CONTRAST, worst_louder


def test_zapas_na_okruglenie_brauzera_uchtyon():
    """Наихудший фон берётся на уровень светлее расчётного: так его рисует браузер.

    Замер записан в комментарии к ROUNDING_GUARD; тест сторожит, что запас не потеряли при
    правке, иначе расчёт контраста снова разойдётся с тем, что видно на экране.
    """
    computed = b.blend(b.INK, b.MAGNITUDE_CLASSES[0][2], b.BG)
    assert computed == '#35383e', computed
    assert b.brightest_star_background() == '#36393f', b.brightest_star_background()


def test_palitra_sovpadaet_s_ui():
    """Цвета модуля — те же, что в :root файла app/ui.py.

    Модуль держит свою копию (импорт из app/ui.py завязал бы фон на порядок сборки), и эта
    проверка — единственное, что мешает копии устареть. Если она упала, надо не править
    ожидание, а ПЕРЕСЧИТАТЬ контраст под новые цвета: числа в docstring модуля посчитаны
    именно для этих значений.
    """
    css_ui = open(UI, encoding='utf-8').read()
    root = re.search(r':root \{(.*?)\}', css_ui, re.S)
    assert root, 'в app/ui.py не нашёлся :root с палитрой'
    body = root.group(1)
    for name, ours in (('bg', b.BG), ('ink', b.INK), ('muted', b.MUTED), ('calc', b.CALC),
                       ('obs', b.OBS), ('fc', b.FC), ('cond', b.COND), ('none', b.NONE),
                       ('line', b.LINE)):
        found = re.search(r'--%s:(#[0-9a-f]{6})' % name, body)
        assert found, ('в :root нет --%s' % name)
        assert found.group(1) == ours, (
            'цвет --%s в app/ui.py стал %s, в app/backdrop.py остался %s: контраст фона '
            'посчитан для старого значения и требует пересчёта' % (name, found.group(1), ours))


def test_luminantnost_po_ekhtalonam():
    """Относительная яркость считается по WCAG, а не «примерно»: три эталонные точки.

    Чёрный — ровно 0, белый — ровно 1, средний серый #808080 — 0,2159 (значение известное,
    проверяется до четвёртого знака). Без этого весь расчёт контраста выше опирался бы на
    непроверенную формулу.
    """
    assert b.relative_luminance('#000000') == pytest.approx(0.0, abs=1e-12)
    assert b.relative_luminance('#ffffff') == pytest.approx(1.0, abs=1e-12)
    assert b.relative_luminance('#808080') == pytest.approx(0.2158605, abs=1e-6)
    # Предельное отношение контраста чёрного к белому по определению равно 21:1.
    assert b.contrast_ratio('#000000', '#ffffff') == pytest.approx(21.0, abs=1e-9)
    # Отношение симметрично: порядок цветов на него не влияет.
    assert b.contrast_ratio(b.INK, b.BG) == pytest.approx(b.contrast_ratio(b.BG, b.INK))


def test_smeshivanie_na_krayah():
    """Композиция: при нулевой непрозрачности остаётся фон, при полной — верхний цвет."""
    assert b.blend(b.INK, 0.0, b.BG) == b.BG
    assert b.blend(b.INK, 1.0, b.BG) == b.INK
    with pytest.raises(ValueError):
        b.blend(b.INK, 1.5, b.BG)
    with pytest.raises(ValueError):
        b.relative_luminance('#abc')


# ======================================================================== поле звёзд

def test_nebo_determinirovano():
    """Одно зерно — одна и та же картинка. Иначе небо прыгало бы при каждом пересчёте страницы."""
    first = b.stars.__wrapped__(b.STAR_SEED)          # мимо кеша: сравниваем расчёт, а не кеш
    second = b.stars.__wrapped__(b.STAR_SEED)
    assert first == second
    assert b.starfield_svg() == b.starfield_svg()
    # Другое зерно даёт другое небо — значит зерно действительно работает, а не игнорируется.
    assert b.stars.__wrapped__(b.STAR_SEED + 1) != first


def test_pervye_zvyozdy_na_svoih_mestah():
    """Опорные координаты. Держат воспроизводимость картинки между запусками и машинами.

    Значения сняты с датчика `random.Random(20260919)`; вихрь Мерсенна воспроизводим, поэтому
    падение этой проверки означает, что изменилось ЧТО-ТО в порядке размещения, а не «повезло».
    """
    field = b.stars()
    assert field[0] == b.Star(x=1303, y=106, magnitude=1), field[0]
    assert field[1] == b.Star(x=724, y=148, magnitude=1), field[1]
    assert field[-1].magnitude == b.MAGNITUDE_CLASSES[-1][0]


def test_plotnost_i_klassy_yarkosti():
    """Сто сорок звёзд, разложенных по четырём классам по закону счёта звёзд."""
    counts = b.magnitude_counts()
    assert counts == (2, 7, 26, 105), counts
    assert sum(counts) == b.STAR_COUNT
    # Каждый следующий класс многочисленнее предыдущего — иначе это не звёздные величины.
    assert list(counts) == sorted(counts), counts
    field = b.stars()
    assert len(field) == b.STAR_COUNT
    for (magnitude, _r, _a), expected in zip(b.MAGNITUDE_CLASSES, counts):
        assert sum(1 for s in field if s.magnitude == magnitude) == expected
    # Яркость убывает вместе с радиусом: класс 1 — самый крупный и самый заметный.
    radii = [r for _m, r, _a in b.MAGNITUDE_CLASSES]
    alphas = [a for _m, _r, a in b.MAGNITUDE_CLASSES]
    assert radii == sorted(radii, reverse=True), radii
    assert alphas == sorted(alphas, reverse=True), alphas


def test_pri_malom_pole_padaet_a_ne_otdayot_nebo_bez_yarkih_zvyozd():
    """Если звёзд слишком мало, самому яркому классу достаётся ноль — и функция падает.

    Молчать здесь нельзя: весь расчёт наихудшего контраста опирается на то, что звезда первого
    класса на поле есть. Поле без неё выглядело бы работающим и тихо обесценило бы расчёт.
    """
    for too_few in (1, 3, 10, 30):
        with pytest.raises(RuntimeError, match='ноль звёзд'):
            b.magnitude_counts(too_few)
    # На рабочем числе и чуть ниже все классы населены.
    assert all(c > 0 for c in b.magnitude_counts(84))
    assert all(c > 0 for c in b.magnitude_counts(b.STAR_COUNT))


def test_zvyozdy_ne_nakladyvayutsya():
    """Ни одна пара звёзд не ближе MIN_SEPARATION.

    Это не про красоту: на этом держится расчёт контраста. Если круги пересекаются, под текстом
    складываются две непрозрачности, и наихудший фон становится светлее посчитанного.
    """
    field = b.stars()
    nearest = min(math.dist((a.x, a.y), (c.x, c.y))
                  for i, a in enumerate(field) for c in field[i + 1:])
    assert nearest >= b.MIN_SEPARATION, nearest
    # С огромным запасом больше удвоенного наибольшего радиуса — круги не соприкасаются вовсе.
    assert b.MIN_SEPARATION > 2 * max(r for _m, r, _a in b.MAGNITUDE_CLASSES)


def test_zvyozdy_vnutri_polya():
    """Ни одна звезда не вылезает за рамку картинки и не обрезается ею."""
    for star in b.stars():
        assert b.FIELD_MARGIN <= star.x <= b.FIELD_W - b.FIELD_MARGIN, star
        assert b.FIELD_MARGIN <= star.y <= b.FIELD_H - b.FIELD_MARGIN, star
    assert b.FIELD_MARGIN > max(r for _m, r, _a in b.MAGNITUDE_CLASSES)


def test_pole_perepolneno_padaet_a_ne_slipaetsya():
    """Если звёзд не помещается, модуль падает с внятным сообщением.

    Проверка на то, что в модуле нет молчаливой заглушки: при переполнении он НЕ ставит звезду
    вплотную к соседней, сохраняя вид работающего кода и ломая расчёт контраста.
    """
    with pytest.raises(RuntimeError, match='поле переполнено'):
        b.stars.__wrapped__(b.STAR_SEED, 4000)


def test_kartinka_neba_razbiraetsya():
    """SVG собран правильно: столько кругов, сколько звёзд, и по группе на класс яркости."""
    svg = b.starfield_svg()
    assert svg.startswith('<svg ') and svg.endswith('</svg>')
    assert "viewBox='0 0 %d %d'" % (b.FIELD_W, b.FIELD_H) in svg
    # Обрезка, а не растяжение: круги обязаны остаться кругами на любом соотношении сторон.
    assert "preserveAspectRatio='xMidYMid slice'" in svg
    assert svg.count('<circle') == b.STAR_COUNT
    assert svg.count('<g opacity=') == len(b.MAGNITUDE_CLASSES)
    # Цвет один на всю картинку и взят из палитры экрана, новых тонов модуль не вводит.
    assert svg.count("fill='") == 1 and ("fill='%s'" % b.INK) in svg
    # Ни мерцаний, ни движения: в картинке нет ни анимации, ни градиента.
    for forbidden in ('animate', 'gradient', '<script'):
        assert forbidden not in svg.lower(), forbidden


def test_adres_kartinki_ne_lomaet_css():
    """В адресе картинки закодированы знаки, которые иначе оборвали бы правило CSS.

    Решётка обязательна: в адресе она начинает часть после решётки, и незакодированный цвет
    «#e8ebf2» обрезал бы картинку на первой же группе — небо просто не появилось бы.
    """
    uri = b._data_uri(b.starfield_svg())
    assert uri.startswith('data:image/svg+xml,')
    for forbidden in ('#', '<', '>', '"', '\n'):
        assert forbidden not in uri, forbidden
    assert '%23' in uri and '%3Csvg' in uri
    # Адрес целиком попадает в правило и заключён в кавычки — в нём есть одинарные кавычки SVG.
    assert ('url("%s")' % uri) in b.css()


# ======================================================================== анимации

def _animation_declarations(css: str) -> list[str]:
    return re.findall(r'animation:\s*([^;]+);', css)


def test_ni_odnoy_beskonechnoy_animatsii():
    """У каждой анимации явно записан один проход. Бесконечных нет ни одной."""
    css = b.css()
    assert 'infinite' not in css
    declarations = _animation_declarations(css)
    assert declarations, 'анимаций не объявлено вовсе'
    for declaration in declarations:
        if declaration.strip() == 'none':
            continue
        assert re.search(r'(^|\s)1(\s|$)', declaration), declaration


def test_animatsii_korotkie_i_po_zadaniyu():
    """Длительности — те, что в задании: 0,25 с, 0,4 с и короткая подсветка."""
    css = b.css()
    assert b.REVEAL_S == 0.25 and b.DRAW_S == 0.40
    assert 'vk-reveal .25s' in css, css
    assert 'vk-draw .4s' in css, css
    assert 'vk-changed .9s' in css, css
    # Ни одна анимация не длиннее секунды: это подсказка, а не заставка.
    for declaration in _animation_declarations(css):
        for seconds in re.findall(r'(\d*\.?\d+)s', declaration):
            assert float(seconds) <= 1.0, declaration


def test_tri_animatsii_i_ni_odnoy_lishney():
    """Ровно три анимации, ровно те, что названы в задании, и у каждой есть свой смысл."""
    css = b.css()
    names = set(re.findall(r'@keyframes\s+([\w-]+)', css))
    assert names == {'vk-reveal', 'vk-draw', 'vk-changed'}, names
    # Каждая объявленная анимация где-то применяется, и наоборот.
    used = {d.split()[0] for d in _animation_declarations(css) if d.strip() != 'none'}
    assert used == names, (used, names)


def test_umenshenie_dvizheniya_vyklyuchaet_vsyo():
    """При системной настройке «уменьшить движение» не остаётся ни одной анимации.

    Проверяется не наличие правила, а покрытие: каждый селектор, которому анимация назначена,
    перечислен и внутри @media.
    """
    css = b.css()
    block = re.search(r'@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}', css, re.S)
    assert block, css
    body = block.group(1)
    assert 'animation: none' in body
    outside = css[:block.start()]
    for rule in re.finditer(r'([^{}]+)\{\s*\n?\s*animation:\s*(?!none)[^;]+;\s*\}', outside):
        for selector in rule.group(1).replace('\n', ' ').split(','):
            selector = selector.strip()
            if not selector or selector.startswith('@'):
                continue
            assert selector in body, ('селектор %r анимируется, но не выключен при '
                                      '«уменьшить движение»' % selector)


def test_nebo_ostayotsya_pri_umenshenii_dvizheniya():
    """Настройка гасит движение, а не фон: звёзды статичны и движением не являются."""
    css = b.css()
    block = re.search(r'@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}', css, re.S)
    body = block.group(1)
    # Внутри блока нет ничего, кроме выключения анимаций: ни фона, ни цвета, ни раскладки.
    properties = {d.split(':', 1)[0].strip()
                  for d in re.sub(r'[^{]*\{', '', body).replace('}', '').split(';') if ':' in d}
    assert properties == {'animation'}, properties
    # Правило неба объявлено ВНЕ блока и настройкой не затрагивается.
    assert 'background-image' not in body
    assert css.index('background-image:url(') < block.start()


# ======================================================================== разметка целиком

def test_ves_razmetki_v_predele():
    """Вес меньше 40 КБ, и он назван числом, а не «немного»."""
    weight = b.weight_bytes()
    assert weight <= b.WEIGHT_LIMIT_BYTES, weight
    # Запас нужен: если разметка внезапно распухла втрое, это ошибка сборки, а не «ещё влезает».
    assert weight < b.WEIGHT_LIMIT_BYTES // 2, weight
    # Основная часть веса — картинка неба (5 697 байт); на сами правила приходится 1 718.
    # Длинные пояснения держатся в исходнике на Python, а не в комментариях CSS: комментарий в
    # CSS уходит в браузер при каждой отрисовке. Предел 2,5 КБ сторожит, чтобы проза не
    # переехала обратно в разметку.
    image = len(b._data_uri(b.starfield_svg()).encode('utf-8'))
    rules_only = len(b.css().encode('utf-8')) - image
    assert rules_only < 2560, rules_only
    assert image < 8192, image


def test_net_znakov_vne_pechatnogo_diapazona():
    """Ловушка восьмеричных escape: последовательность вида \\25B8 в НЕ-raw строке Python
    читается как восьмеричный escape и даёт U+0015. Такая ошибка уже доходила до развёрнутого
    сервиса (см. комментарий к CSS в app/ui.py), поэтому проверка живёт в каждом файле стилей."""
    css = b.css()
    bad = sorted({hex(ord(c)) for c in css if ord(c) < 32 and c not in '\n\r\t'})
    assert not bad, bad
    assert '\x00' not in css and '\x15' not in css


def test_net_gradientov_i_emodzi():
    """S4 того же проекта: ни градиентных заливок, ни значков — на проекторе они дают грязь."""
    css = b.css()
    assert 'linear-gradient' not in css and 'radial-gradient' not in css
    assert not EMOJI.findall(css), EMOJI.findall(css)


def test_razmetka_zamknuta_i_odna():
    """Ровно один блок стилей, открыт и закрыт: разметка вставляется в страницу целиком."""
    css = b.css()
    assert css.count('<style>') == 1 and css.count('</style>') == 1
    assert css.startswith('<style>') and css.rstrip().endswith('</style>')
    assert '<script' not in css.lower()


def test_nebo_lozhitsya_pod_soderzhimoe():
    """Небо — собственный фон `.stApp`, и ничего кроме фона модуль ему не меняет.

    Проверка написана после разбора живой страницы. Первая редакция ставила небо на
    `.stApp::before` и ради `z-index: -1` объявляла `.stApp { position: relative }` — это
    отняло у `.stApp` абсолютное позиционирование, высота стала нулевой, и `overflow: hidden`
    срезал ВСЁ содержимое страницы. Поэтому тест прямо запрещает трогать раскладку `.stApp`:
    ни position, ни z-index, ни псевдоэлемента.
    """
    css = b.css()
    rule = re.search(r'div\[data-testid="stApp"\], \.stApp \{(.*?)\}', css, re.S)
    assert rule, css
    body = rule.group(1)
    assert 'background-image:url("data:image/svg+xml,' in body
    assert 'background-size:cover' in body
    # Обрезка по центру: при другом соотношении сторон поле не растягивается.
    assert 'background-position:center center' in body and 'background-repeat:no-repeat' in body
    # Ни одного свойства раскладки — правило задаёт ТОЛЬКО фон и ничего больше.
    properties = {d.split(':', 1)[0].strip() for d in body.split(';') if ':' in d}
    assert properties == {'background-image', 'background-repeat', 'background-position',
                          'background-size'}, properties
    # Заливка не переопределяется: цвет остаётся от app/ui.py и .streamlit/config.toml.
    assert 'background-color' not in properties
    assert '::before' not in css and '::after' not in css
    # На бумагу небо не идёт.
    assert '@media print { div[data-testid="stApp"], .stApp { background-image:none; } }' in css


def test_podklyuchenie_fona_ne_dvigaet_stranitsu():
    """Лишний отступ, который добавляет ВТОРОЙ блок стилей, снимается — и только он.

    Правило обязано прятать каждый ПОСЛЕДУЮЩИЙ контейнер со стилями, а не всякий: первый уже
    есть на странице (его печатает app/ui.py) и раскладка выстроена с ним. Если спрятать оба,
    страница поднимается на 32 пикселя и заголовок уезжает под верхнюю полосу Streamlit
    высотой 60 пикселей — это замерено в браузере, а не предположено.
    """
    css = b.css()
    rule = '%s ~ %s { display:none; }' % (b.STYLE_ONLY_CONTAINER, b.STYLE_ONLY_CONTAINER)
    assert rule in css, css
    # Правило со скрытием ровно одно, и его селектор — соседний, а не одиночный.
    hiding = re.findall(r'^(.*?)\s*\{\s*display:none;\s*\}$', css, re.M)
    assert len(hiding) == 1, hiding
    assert ' ~ ' in hiding[0], hiding[0]
    # `style:only-child` — гарантия, что видимого в контейнере нет вовсе.
    assert 'style:only-child' in b.STYLE_ONLY_CONTAINER


def test_podklyuchenie_v_odnu_stroku():
    """apply() печатает разметку и возвращает её вес. Streamlit при этом не нужен."""
    printed = []

    class Fake:
        @staticmethod
        def markdown(text, unsafe_allow_html=False):
            printed.append((text, unsafe_allow_html))

    weight = b.apply(Fake)
    assert printed and printed[0][1] is True
    assert printed[0][0] == b.css()
    assert weight == b.weight_bytes()


# ======================================================================== изменившиеся ячейки

def _panel(*pairs: tuple[str, str]) -> str:
    """Приборная полоса в том виде, в каком её печатает ui.panel()."""
    cells = ''.join('<div class="cell k-calc"><div class="cl">%s</div><div class="cv">%s</div>'
                    '<div class="cs">давность 1 мин</div></div>' % pair for pair in pairs)
    return '<div class="panel"><div class="prow">%s</div></div>' % cells


def test_podsvechivaetsya_tolko_izmenivshayasya_yacheyka():
    """Класс встаёт ровно на те ячейки, у которых изменилось значение."""
    was = _panel(('ПОТОК GOES', '0,21 ед.'), ('ПРОГНОЗ KP', '3,0'))
    now = _panel(('ПОТОК GOES', '0,48 ед.'), ('ПРОГНОЗ KP', '3,0'))
    assert b.changed_labels(now, was) == ['ПОТОК GOES']
    marked = b.mark_changed_cells(now, was)
    assert marked.count('vk-changed') == 1
    assert '<div class="cell k-calc vk-changed"><div class="cl">ПОТОК GOES</div>' in marked
    assert '<div class="cell k-calc"><div class="cl">ПРОГНОЗ KP</div>' in marked


def test_pervaya_otrisovka_nichego_ne_podsvechivaet():
    """Сравнивать не с чем — значит нечего и подсвечивать: выдумывать «изменение» нечестно."""
    now = _panel(('ПОТОК GOES', '0,21 ед.'))
    assert b.changed_labels(now, None) == []
    assert b.mark_changed_cells(now, None) == now
    assert b.mark_changed_cells(now, '') == now


def test_poyavivshayasya_yacheyka_ne_schitaetsya_izmenivsheysya():
    """Появление ячейки — не смена значения. Иначе при первой отрисовке мигала бы вся полоса."""
    was = _panel(('ПОТОК GOES', '0,21 ед.'))
    now = _panel(('ПОТОК GOES', '0,21 ед.'), ('СБЛИЖЕНИЯ', '—'))
    assert b.changed_labels(now, was) == []
    assert 'vk-changed' not in b.mark_changed_cells(now, was)


def test_davnost_ne_schitaetsya_izmeneniem():
    """Подпись ячейки (там стоит давность) не сравнивается: она меняется всегда, и полоса
    подсвечивалась бы целиком при каждом пересчёте — ровно то мельтешение, которого избегаем."""
    was = _panel(('ПОТОК GOES', '0,21 ед.'))
    now = was.replace('давность 1 мин', 'давность 7 мин')
    assert b.changed_labels(now, was) == []


def test_slomannaya_struktura_polosy_padaet_gromko():
    """Если структура ячейки в app/ui.py изменилась, подсветка обязана упасть, а не замолчать."""
    broken = '<div class="panel"><div class="cell k-calc"><span>ПОТОК GOES</span></div></div>'
    with pytest.raises(RuntimeError, match='структура ячейки'):
        b.mark_changed_cells(broken, _panel(('ПОТОК GOES', '0,21 ед.')))
    # Разметка, не похожая на полосу вовсе, проходит без шума: полосы тут просто нет.
    assert b.mark_changed_cells('<div>обычный текст</div>', None) == '<div>обычный текст</div>'


def test_rabotaet_na_nastoyashchey_polose_iz_ui():
    """Главная проверка связки: разбор идёт по НАСТОЯЩЕЙ разметке ui.panel(), а не по образцу.

    Синтетическая полоса выше повторяет структуру по памяти и потому не поймала бы её смену.
    Здесь полоса строится тем же кодом, что и на экране. Если ui.panel() перестроят, тест
    падает — и это правильно: подсветка парсит его разметку и обязана об этом узнать.
    """
    from app.ui import panel
    rows_was = [[('Время расчёта', '19.09 16:41 UTC', 'наш расчёт', 'calc'),
                 ('Поток GOES', '0,21 ед.', 'давность 12 мин', 'obs'),
                 ('Прогноз Kp', '3,0', 'живой выпуск', 'fc'),
                 ('Сближения', '—', 'данных нет', 'none')]]
    rows_now = [[('Время расчёта', '19.09 16:52 UTC', 'наш расчёт', 'calc'),
                 ('Поток GOES', '0,48 ед.', 'давность 3 мин', 'obs'),
                 ('Прогноз Kp', '3,0', 'живой выпуск', 'fc'),
                 ('Сближения', '—', 'данных нет', 'none')]]
    was, now = panel(rows_was), panel(rows_now)
    assert [label for label, _v in b.cells(now)] == ['Время расчёта', 'Поток GOES',
                                                     'Прогноз Kp', 'Сближения']
    assert b.changed_labels(now, was) == ['Время расчёта', 'Поток GOES']
    marked = b.mark_changed_cells(now, was)
    assert marked.count('vk-changed') == 2
    # Плашка происхождения у помеченной ячейки на месте: подсветка ничего не переписывает.
    assert 'class="cell k-obs vk-changed"' in marked
    assert 'class="cell k-fc"' in marked
    # Экранирование ui.panel сохранено: знак «меньше» не превращается в разметку.
    escaped = b.cells(panel([[('Поток GOES', '<0,01 ед.', 'наблюдение', 'obs')]]))
    assert escaped == [('Поток GOES', '&lt;0,01 ед.')], escaped


def test_razbor_yacheek_sohranyaet_poryadok():
    """Ячейки читаются в том порядке, в котором напечатаны."""
    html = _panel(('ВРЕМЯ РАСЧЁТА', '16:41'), ('ПОТОК GOES', '0,21 ед.'), ('ПРОГНОЗ KP', '3,0'))
    assert b.cells(html) == [('ВРЕМЯ РАСЧЁТА', '16:41'), ('ПОТОК GOES', '0,21 ед.'),
                            ('ПРОГНОЗ KP', '3,0')]


def test_pravilo_podsvetki_est_v_razmetke():
    """Класс, который ставит mark_changed_cells, объявлен в стилях — иначе он ничего не делает."""
    css = b.css()
    assert '.cell.vk-changed { animation: vk-changed' in css
    # Подсветка бесцветная: любой из четырёх тонов происхождения сказал бы о ячейке неправду.
    assert 'background-color:%s' % b.LINE in css
    for tone in (b.CALC, b.OBS, b.FC, b.COND):
        assert tone not in css, tone
