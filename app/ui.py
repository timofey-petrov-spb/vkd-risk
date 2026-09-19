# -*- coding: utf-8 -*-
"""Оформление экрана: стили, плашки статусов, панель вердикта, карточки окон, приборная полоса,
словари перевода идентификаторов модулей в подписи для пользователя, реестр источников и
блоки вкладки «Методика».

Только разметка. Ничего не считает и не решает: все значения приходят из снимка расчёта.

Цветовая система — монотонная, академическая (PROPOSAL_A п. 2 в редакции задания):
цвет означает происхождение величины, а не «хорошо/плохо». Четыре тона и один служебный:
  синий  --calc — наш расчёт;
  зелёный --obs — наблюдение (в том числе взятое из кеша: давность объявляется подписью, а не цветом);
  янтарный --fc — внешний прогноз, и только он;
  красный --cond — условие проверки, аномалия, отказ источника;
  серый  --none — данных нет.
Кеш цвет не меняет (R4-28): иначе наблюдение из кеша и внешний прогноз в полосе состояния идут
одним тоном, и различие происхождений, ради которого система и заведена, на экране не читается.
Светофора на экране нет: «условий проверки нет» — серым, а не зелёным; предпочтительное окно —
синим (это наш расчёт), а не зелёным. Ни градиентов, ни эмодзи: на проекторе они дают грязь,
а эмодзи по-разному рисуются в Windows, macOS и на телефоне.

Типографика — одна лестница размеров на весь экран (PROPOSAL_A п. 2.1): заголовок 1,55 rem,
микрозаголовок 0,72 rem прописными, крупное число 1,35 rem, подпись 0,78 rem; все числовые
ячейки — tabular-nums, включая st.dataframe.

На оперативном уровне на экране нет идентификаторов кода: методы, типы событий, источники и
ограничения модулей переводятся словарями ниже; неизвестная строка выводится как есть с пометкой.
"""
from __future__ import annotations

import html
import math
import re
from datetime import datetime, timedelta

# Строка стилей объявлена RAW (r"""…"""): внутри неё стоят экранированные последовательности CSS
# вида \25B8 (треугольник свёртки). В обычной строке Python «\25» и «\00» читаются как ВОСЬМЕРИЧНЫЕ
# escape и дают U+0015 и U+0000 — браузер по спецификации заменяет нуль на U+FFFD, и перед подписью
# «Как это посчитано…» в блоке вердикта рисовался мусор «B8◆A0». Проверка — tests/test_ui_app.py:
# в CSS нет знаков вне печатного диапазона.
CSS = r"""
<style>
/* IBM Plex Sans — инженерная гарнитура с полной кириллицей, та, которой набрана техническая
   документация IBM. Взята вместо Public Sans по замечанию владельца: Public Sans — гротеск
   общего назначения, он стоит на половине сайтов и не говорит о странице ничего. У Plex Sans
   узнаваемый рисунок (прямой срез у «a» и «g», узкие овалы, инженерная цифра), и кириллица
   у него нарисована, а не досочинена автоматикой, — это видно на «ж», «д», «щ» в ярлыках.
   Подключается ОДНОЙ строкой, четыре начертания: 400 — текст, 500 — значения в карточках,
   600 — ярлыки капителью, 700 — крупные строки. Восьмисотого у Plex Sans нет, и просить его
   нельзя: браузер дорисовал бы жир сам, а синтетический жир на 52 px виден сразу.
   Именно @import, а не <link>: Streamlit пропускает через st.markdown РОВНО ОДИН узел разметки,
   и всё, что стоит перед <style>, ломает разбор — проверено в браузере, вся таблица стилей
   выводилась на экран текстом вместо того, чтобы применяться.
   Сеть может не ответить, и на этот случай в font-family ниже стоит прежний системный ряд:
   экран остаётся набранным, просто другим шрифтом. Ни размеры, ни сетка от него не зависят. */
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap');
/* ТЁМНАЯ тема. Палитра означает то же, что и прежде, — ПРОИСХОЖДЕНИЕ величины, — но собрана
   заново: прежний набор (#7ab8f5, #5ed39a, #e7b45c, #f58b7f) владелец опознал с одного взгляда
   как машинный, и он прав. У прежних четырёх тонов насыщенность стояла высокая и почти
   одинаковая (0,86 / 0,57 / 0,74 / 0,86 по HSL), а светлота — почти общая: переведённые в
   серый полутон, они давали #b3b3b3, #bebebe, #bdbdbd и #a9a9a9, то есть три оттенка из
   четырёх различались на 1,8 % относительной яркости и в серой печати сливались в один.
   Цвет при этом нёс ВСЮ нагрузку: различить наблюдение и внешний прогноз можно было только
   по тону. Это и есть подпись генератора тёмной темы.
   Новый набор разведён ПО СВЕТЛОТЕ, а тон оставлен приметой второго порядка. Четыре шага по
   относительной яркости — +23,8 %, +20,7 %, +21,9 %, размах между крайними +82,2 %:
     условие/отказ   #c17d68  кирпичный  Y 0,2700  серый #8e8e8e  насыщенность 0,42
     наблюдение      #76a78e  хвойный    Y 0,3344  серый #9d9d9d  насыщенность 0,22
     внешний прогноз #bba889  латунный   Y 0,4038  серый #ababab  насыщенность 0,27
     наш расчёт      #a1bed5  стальной   Y 0,4921  серый #bbbbbb  насыщенность 0,38
   Насыщенность не только НИЗКАЯ (была 0,57…0,86, стала 0,22…0,42), но и РАЗНАЯ, и порядок
   у неё осмысленный: громче всех условие проверки — его пропускать нельзя; следом наш
   собственный расчёт; тише — чужой прогноз; тише всех наблюдение, потому что его на экране
   больше всего, и ровный шум из зелёного как раз и читается как «нейронка».
   Фон опущен с #0e1117 до #08090b: глубже и почти нейтральный (прежний заметно синил).
   Девять десятых экрана — серое; цвет остаётся только на самих величинах.
   Контраст проверен расчётом по формуле относительной яркости, а не на глаз
   (фон страницы #08090b, фон панели #101217; минимум по стандарту — 4,5:1):
     основной текст  #e4e7ea — 16,05 на фоне, 15,09 на панели;
     приглушённый    #9da1a8 —  7,68 / 7,22;
     наш расчёт      #a1bed5 — 10,28 / 9,67, на своей заливке #162734 — 7,89;
     наблюдение      #76a78e —  7,29 / 6,86, на своей заливке #14291f — 5,63;
     внешний прогноз #bba889 —  8,61 / 8,10, на своей заливке #2d2516 — 6,54;
     условие/отказ   #c17d68 —  6,07 / 5,71, на своей заливке #382018 — 4,61;
     данных нет      #91969c —  6,68 / 6,29, на своей заливке #232629 — 5,10.
   Ниже 4,5 не опускается ни одна пара; наихудшая — 4,61. Основной текст и приглушённый на
   каждой из пяти заливок дают не меньше 12,19 и 5,83 соответственно (проверено расчётом:
   приглушённым набран ярлык внутри полосы решения, и он лежит именно на заливке).
   Базовая тема Streamlit задана в .streamlit/config.toml: одной правкой стилей не обойтись —
   собственные виджеты красятся своей темой, и светлые виджеты на тёмном фоне читались бы
   как поломка. */
:root { --ink:#e4e7ea; --muted:#9da1a8; --line:#202328; --bg:#08090b; --soft:#101217;
        --calc:#a1bed5; --calc-bg:#162734; --calc-line:#294153;
        --obs:#76a78e;  --obs-bg:#14291f;  --obs-line:#264435;
        --fc:#bba889;   --fc-bg:#2d2516;   --fc-line:#483d28;
        --cond:#c17d68; --cond-bg:#382018; --cond-line:#58352b;
        --none:#91969c; --none-bg:#232629; --none-line:#3b3e44;
        /* прежние имена состояний — те же четыре тона, чтобы правила ниже читались одинаково */
        --ok:var(--obs); --ok-bg:var(--obs-bg); --warn:var(--fc); --warn-bg:var(--fc-bg);
        --crit:var(--cond); --crit-bg:var(--cond-bg);
        /* ТРИ размера шрифта на главном экране и ДВЕ степени приглушённости — больше нет.
           Тринадцатый круг: владелец просит типографику в духе NASA и Роскосмоса — крупный
           ответ, ярлыки капителью, ничего между ними. Размеры заданы здесь один раз, и правила
           ниже берут только их: иначе «почти такой же, но на два процента мельче» расползается
           по файлу, как это и случилось к двенадцатому кругу (пятнадцать разных кеглей).
             --fs-1 — ответ, крупные числа и название сервиса;
             --fs-2 — значения и обычный текст;
             --fs-3 — ярлыки капителью, приглушённые подписи, заголовки раскрытий.
           Приглушённость: --ink и --muted. Третьего тона текста на экране нет; --none остаётся
           цветом ПРОИСХОЖДЕНИЯ «данных нет», а не степенью серости. */
        --fs-1:clamp(28px, 2.9vw, 40px); --fs-2:16px; --fs-3:13px;
        /* Крупная строка решения — отдельный кегль: это самый крупный элемент страницы, и он
           обязан быть заметно крупнее даже крупных чисел. 52 px к 16 px — отношение 3,25.
           clamp вместо голого числа: на узком окне строка «ВЫХОДИТЬ 19.09 18:08 — 19:08 UTC»
           в 52 px рвётся на три строки и перестаёт читаться как единый знак. Проверено в
           браузере на ширине 800 px; ниже 32 px кегль не опускается. */
        --fs-0:clamp(32px, 3.7vw, 52px);
        /* Межстрочное: у КРУПНОГО текста тесное (строка читается как единый знак), у мелкого
           свободное (его читают построчно). Одинаковое межстрочное везде — главный признак
           любительской вёрстки, и на снимках сайта NASA его нет нигде. */
        --lh-0:1.0; --lh-1:1.05; --lh-2:1.65;
        /* воздух между блоками: один шаг сетки и его удвоение, руками числа больше не ставим */
        --gap:20px; --gap-2:48px;
        /* Шрифт объявлен ОДИН раз: IBM Plex Sans, за ним прежний системный ряд на случай,
           когда внешний шрифт не пришёл. Экран от этого не ломается — меняется только
           начертание: кегли, межстрочные и сетка заданы числами и от гарнитуры не зависят. */
        --ff:"IBM Plex Sans", "Segoe UI", Inter, Roboto, Arial, sans-serif; }
html, body, [class*="css"] { font-family: var(--ff); color: var(--ink); font-size: var(--fs-2);
        line-height: var(--lh-2); font-variant-numeric: tabular-nums; }
/* Собственная разметка экрана набирается IBM Plex Sans ЯВНО, по своим классам. Проверено в
   браузере: Streamlit объявляет свой шрифт на собственных узлах с более высокой значимостью,
   и одного правила на html/body мало — заголовок, полоса решения и числа оставались набраны
   шрифтом темы. Широкое правило на `.stApp *` не годится: тем же махом оно переписало бы
   шрифт иконок материала (они шрифтовые, и вместо значка появилось бы слово «schedule») и
   шрифты формул KaTeX во вкладке «Методика». Поэтому перечислены ровно наши классы —
   внутрь них Streamlit не вмешивается, и потомки наследуют семейство сами. */
.vk-head, .vk-title, .vk-sub, .sect, .pill, .panel, .decision, .reco, .verdict, .wcard, .kv,
.legend, .small, .tcap, .cond, .cov, .covwhy, .wnote { font-family: var(--ff); }
.stApp, .main, section[data-testid="stSidebar"] { background: var(--bg); }
.block-container { padding-top: 3.2rem; padding-bottom: 3rem; max-width: 1400px; }
h1, h2, h3 { letter-spacing: -0.01em; }
div[data-testid="stDataFrame"], div[data-testid="stTable"] { font-variant-numeric: tabular-nums; }
.vk-head { display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; margin-bottom:2px; }
.vk-title { font-size:var(--fs-1); font-weight:700; line-height:var(--lh-1); margin:0; letter-spacing:-0.02em; }
.vk-sub { color:var(--muted); font-size:var(--fs-3); font-weight:600; letter-spacing:0.08em;
          text-transform:uppercase; }
.sect { font-size:var(--fs-3); font-weight:600; letter-spacing:0.08em; text-transform:uppercase; color:var(--muted);
        margin:var(--gap-2) 0 var(--gap) 0; }
.pill { display:inline-block; padding:2px 9px; border-radius:999px; font-size:var(--fs-3); font-weight:600;
        line-height:1.5; border:1px solid transparent; margin:1px 4px 1px 0; white-space:nowrap; }
.pill-ok { background:var(--obs-bg); color:var(--obs); border-color:var(--obs-line); }
.pill-warn { background:var(--fc-bg); color:var(--fc); border-color:var(--fc-line); }
.pill-crit { background:var(--cond-bg); color:var(--cond); border-color:var(--cond-line); }
.pill-none { background:var(--none-bg); color:var(--none); border-color:var(--none-line); }
.pill-calc { background:var(--calc-bg); color:var(--calc); border-color:var(--calc-line); }
.pill-obs { background:var(--obs-bg); color:var(--obs); border-color:var(--obs-line); }
.pill-fc { background:var(--fc-bg); color:var(--fc); border-color:var(--fc-line); }
/* приборная полоса: две строки фиксированной сетки, метка — значение — давность и происхождение */
.panel { border:1px solid var(--line); border-radius:10px; background:var(--soft); margin:6px 0 var(--gap) 0; overflow:hidden; }
.prow { display:grid; grid-template-columns:repeat(auto-fit, minmax(190px, 1fr)); }
.prow + .prow { border-top:1px solid var(--line); }
.cell { padding:7px 12px 8px 12px; border-left:3px solid var(--none-line); box-shadow:inset 1px 0 0 var(--line); min-width:0; }
.cell:first-child { box-shadow:none; }
.cl { font-size:var(--fs-3); font-weight:700; letter-spacing:0.08em; text-transform:uppercase; color:var(--muted);
      white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.cv { font-size:var(--fs-2); font-weight:600; line-height:1.35; overflow-wrap:anywhere; }
.cs { font-size:var(--fs-3); color:var(--muted); line-height:1.35; overflow-wrap:anywhere; }
.k-calc { border-left-color:var(--calc); } .k-calc .cv { color:var(--calc); }
.k-obs  { border-left-color:var(--obs); }  .k-obs .cv  { color:var(--obs); }
.k-fc   { border-left-color:var(--fc); }   .k-fc .cv   { color:var(--fc); }
.k-cond { border-left-color:var(--cond); } .k-cond .cv { color:var(--cond); }
.k-none { border-left-color:var(--none-line); } .k-none .cv { color:var(--ink); }
.verdict { border-radius:12px; padding:16px 20px; border:1px solid var(--line); border-left:3px solid var(--none);
           background:var(--bg); margin:4px 0 14px 0; }
/* Типографика блока вердикта — ТА ЖЕ, что у блока рекомендации наверху (замечание владельца по
   снимкам 20 и 21: «выглядит как вырви глаз»; здесь текст был мельче — 1,35/0,92/0,93 rem против
   1,55/0,95 в рекомендации, и два блока об одном и том же читались как разные по важности).
   Ширина строки ограничена одинаково в обоих блоках: сплошная строка в 1400 px не читается —
   глаз теряет начало следующей. 96ch при 0,95 rem — около 740 px. */
.verdict h2 { margin:0 0 4px 0; font-size:var(--fs-1); }
.verdict .rule { color:var(--muted); font-size:var(--fs-2); margin-bottom:8px; max-width:96ch; }
.verdict .rule .orig { color:var(--muted); font-size:var(--fs-3); }
.verdict .win { font-size:var(--fs-2); font-weight:600; }
.verdict ul { margin:6px 0 0 18px; padding:0; max-width:96ch; }
.verdict li { margin:3px 0; font-size:var(--fs-2); }
.verdict li.more { color:var(--muted); list-style:none; margin-left:-18px; font-size:var(--fs-3); }
.verdict .plan { margin:8px 0 2px 0; padding:6px 10px; border-radius:8px; border-left:3px solid var(--calc);
                 background:var(--calc-bg); color:var(--calc); font-size:var(--fs-3); }
.verdict .policy { margin:8px 0 0 0; font-size:var(--fs-3); color:var(--muted); }
/* Объявленная область вывода. НЕ в свёртке ни на одном уровне: вердикт без своей области —
   это утверждение шире, чем посчитано. Рамка слева тем же цветом, что у нашего расчёта:
   область говорит о нашем расчёте, а не о тревоге. */
.verdict .scope { margin:8px 0 0 0; padding:6px 10px; border-radius:8px; border-left:3px solid var(--calc);
                  background:var(--calc-bg); color:var(--ink); font-size:var(--fs-3); }
.verdict .scope b { color:var(--calc); font-weight:600; }
/* «Что дальше» — последняя видимая строка блока: что проверить, до какого момента действует
   условие, когда пересчитать. Наш расчёт по снимку, поэтому тон синий, как у всего расчётного. */
.verdict .next { margin:10px 0 0 0; padding:7px 10px; border-radius:8px; border-left:3px solid var(--calc);
                 background:var(--calc-bg); color:var(--calc); font-size:var(--fs-2); }
/* «на один клик глубже» (бриф §9.1): как считался допуск, устойчив ли выбор и остальные пояснения.
   Обычный <details>, а не свёртка Streamlit: блок вердикта — одна разметка, и свёртка обязана
   стоять ВНУТРИ неё, иначе она уезжает под карточки окон. */
.verdict details.vmore { margin:8px 0 0 0; }
.verdict details.vmore > summary { cursor:pointer; color:var(--muted); font-size:var(--fs-3); list-style:none;
                                   user-select:none; }
.verdict details.vmore > summary::-webkit-details-marker { display:none; }
.verdict details.vmore > summary::before { content:"\25B8\00A0"; }
.verdict details.vmore[open] > summary::before { content:"\25BE\00A0"; }
.verdict details.vmore .vm { margin:6px 0 0 0; font-size:var(--fs-3); color:var(--muted); }
.verdict details.vmore ul { margin:6px 0 0 18px; }
.verdict details.vmore li { font-size:var(--fs-3); color:var(--muted); }
/* вердикт — наш расчёт (синий); условие у всех окон — красный; нет оснований — серый. Без заливок. */
.v-preferred { border-left-color:var(--calc); }
.v-equivalent { border-left-color:var(--calc); }
.v-trade_off { border-left-color:var(--calc); }
.v-all_need_check { border-left-color:var(--cond); }
.v-insufficient { border-left-color:var(--none); }
.wcard { border:1px solid var(--line); border-radius:12px; padding:14px 16px 12px 16px; background:var(--soft); height:100%;
         border-left:3px solid var(--line); }
.wcard.best { border-left-color:var(--calc); }
.wcard.flag { border-left-color:var(--fc); }
.wcard.crit { border-left-color:var(--cond); }
.wcard .wh { display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:6px; }
.wcard .wt { font-weight:700; font-size:var(--fs-2); }
.wcard .wtime { color:var(--muted); font-size:var(--fs-3); }
.kv { display:grid; grid-template-columns: 1fr auto; gap:3px 10px; font-size:var(--fs-2); margin:8px 0 6px 0; }
.kv .k { color:var(--muted); }
.kv .v { font-weight:600; text-align:right; font-variant-numeric: tabular-nums; }
.kv .v.big { font-size:var(--fs-1); }
.kv .u { color:var(--muted); font-weight:400; }
.wnote { font-size:var(--fs-3); color:var(--muted); margin:-2px 0 6px 0; }
.cond { font-size:var(--fs-3); margin:4px 0 0 0; padding:6px 8px; border-radius:8px; border-left:3px solid var(--fc);
        background:var(--fc-bg); color:var(--fc); }
.cond.crit { border-left-color:var(--cond); background:var(--cond-bg); color:var(--cond); }
.cond.none { border-left-color:var(--none-line); background:var(--none-bg); color:var(--none); }
.cov { margin-top:8px; font-size:var(--fs-3); color:var(--muted); }
.covwhy { margin-top:4px; font-size:var(--fs-3); color:var(--muted); }
/* подкладка под кнопку строки задачи: она встаёт вровень с полями ввода, у которых своя подпись.
   Высота вынесена сюда, а не в разметку: числа с десятичной точкой на экране быть не должно. */
.btnpad { height:1.75rem; }
/* блок рекомендации: ответ на вопрос человека. Рамка слева синяя — это наш расчёт; при отказе
   серая или красная, чтобы заголовок не был увереннее расчёта. */
.reco { border:none; border-radius:0; padding:0; background:transparent; margin:var(--gap) 0 var(--gap-2) 0; }
.reco h2 { margin:0 0 2px 0; font-size:var(--fs-1); color:var(--calc); }
.reco .when { font-size:var(--fs-2); font-weight:600; margin-bottom:6px; }
.reco .why { font-size:var(--fs-2); margin:4px 0 2px 0; max-width:96ch; }
/* Популярное объяснение: ровно два предложения обычными словами под крупным ответом.
   Чуть крупнее служебных строк и без приглушения — это то, что читают первым после заголовка. */
.reco .plain { font-size:var(--fs-2); line-height:1.45; margin:6px 0 2px 0; max-width:96ch; color:var(--ink); }
.reco .searched { font-size:var(--fs-3); color:var(--muted); margin:0 0 var(--gap) 0; }
.reco .stop { margin:8px 0 0 0; padding:7px 10px; border-radius:8px; border-left:3px solid var(--cond);
              background:var(--cond-bg); color:var(--cond); font-size:var(--fs-2); }
.reco .scope { margin:8px 0 0 0; padding:6px 10px; border-radius:8px; border-left:3px solid var(--calc);
               background:var(--calc-bg); color:var(--ink); font-size:var(--fs-3); }
.reco .scope b { color:var(--calc); font-weight:600; }
.reco .policy { margin:8px 0 0 0; font-size:var(--fs-3); color:var(--muted); }
/* «на один клик глубже» внутри блока рекомендации — тот же приём, что в блоке вердикта */
.reco details.vmore { margin:10px 0 0 0; }
.reco details.vmore > summary { cursor:pointer; color:var(--calc); font-size:var(--fs-3); font-weight:600;
                                letter-spacing:0.04em; list-style:none; user-select:none; padding:2px 0; }
.reco details.vmore > summary::-webkit-details-marker { display:none; }
.reco details.vmore > summary::before { content:"\25B8\00A0"; }
.reco details.vmore[open] > summary::before { content:"\25BE\00A0"; }
.reco details.vmore .vm { margin:8px 0 0 0; font-size:var(--fs-2); line-height:var(--lh-2);
                           color:var(--muted); max-width:96ch; }
/* Вид рамки и заголовка — по ВИДУ ОТВЕТА, а не по имени исхода: промежуток равнозначных начал
   такой же ответ, как и точка, и красить его как отказ нельзя. Отказ — только «нет оснований». */
.r-none { border-left-color:var(--none); } .r-none h2 { color:var(--ink); }
.r-check { border-left-color:var(--cond); } .r-check h2 { color:var(--cond); }
.r-tradeoff h2 { color:var(--ink); }
/* ================= ДВА ЯЗЫКА ЦВЕТА: происхождение величины и цвет решения =================
   Ловушка тринадцатого круга, и она настоящая. На этом экране цвет с самого начала означает
   ПРОИСХОЖДЕНИЕ величины: синий — наш расчёт, зелёный — наблюдение источника, янтарный —
   внешний прогноз, красный — условие или отказ. Эксперты хакатона просят другого: «выходить —
   зелёным, не выходить — красным». Это ВТОРОЙ язык, и просто перекрасить вердикт в зелёный
   значило бы сделать зелёный двузначным: жюри читало бы «зелёное число» как «хорошее число».
   Поэтому языки разведены НЕ цветом, а ФОРМОЙ, и тонов при этом остаётся те же пять:
     * происхождение величины — ПЛАШКА: маленькая, скруглённая до 999 px, в строку с текстом,
       кегль --fs-3, со своей тонкой рамкой (правила .pill-* выше);
     * решение — ПОЛОСА ВО ВСЮ ШИРИНУ: прямые углы, широкая цветная грань слева в 10 px,
       сплошная заливка, кегль --fs-1 капителью. Спутать её с плашкой нельзя ни по размеру,
       ни по форме, ни по месту.
   Одна строка на экране (DECISION_LEGEND) говорит это словами, рядом с легендой происхождения.
   Контраст каждой НОВОЙ пары посчитан по формуле относительной яркости (порог 4,5:1):
     решение «выходить»  — #76a78e на заливке #14291f — 5,63; на фоне страницы — 7,29;
     решение «не выходить» — #c17d68 на заливке #382018 — 4,61; на фоне страницы — 6,07;
     решение «за аналитиком» — #bba889 на заливке #2d2516 — 6,54; на фоне страницы — 8,61;
     причина в полосе (основной тон) — #e4e7ea на #14291f — 12,38; на #382018 — 12,19;
                                        на #2d2516 — 12,19;
     ярлык в полосе (приглушённый) — #9da1a8 на #14291f — 5,92; на #382018 — 5,83;
                                      на #2d2516 — 5,83.
   Ниже 4,5 не опускается ни одна пара; наименьшая — 4,61. */
.decision { display:block; margin:6px 0 var(--gap-2) 0; padding:32px 36px 36px 36px;
            border-left:14px solid var(--dec); background:var(--dec-bg); }
.decision .dk { display:block; font-size:var(--fs-3); font-weight:600; letter-spacing:0.18em;
                text-transform:uppercase; color:var(--muted); margin-bottom:12px; }
/* Разрядка на крупном тексте НЕ ставится: она разрушает плотность строки. Наоборот, лёгкое
   сжатие — так набраны крупные заголовки на nasa.gov. Межстрочное 1,0: строка читается как
   единый знак, а не как две строки текста. */
.decision .dv { display:block; font-size:var(--fs-0); font-weight:700; line-height:var(--lh-0);
                color:var(--dec); letter-spacing:-0.02em; }
/* Короткая причина — ТОЛЬКО при отказе и при решении аналитика: красная полоса без причины
   читается как поломка сервиса. Не больше восьми слов, полный текст — раскрытием ниже. */
.decision .dwhy { display:block; font-size:var(--fs-2); line-height:var(--lh-2); color:var(--ink);
                  margin-top:16px; }
.d-go   { --dec:var(--obs);  --dec-bg:var(--obs-bg); }
.d-stop { --dec:var(--cond); --dec-bg:var(--cond-bg); }
.d-ask  { --dec:var(--fc);   --dec-bg:var(--fc-bg); }
/* Крупные числа под решением: тот же каркас приборной полосы, но значение кеглем ответа.
   Ярлык — капителью, справа от числа — то же у худшего начала на сроке. Сравнение
   «выбрано против худшего» и есть объяснение, только числами, без единого слова связки. */
.panel.stat { background:transparent; border:none; border-radius:0; margin:0 0 var(--gap-2) 0; }
.panel.stat .prow { border-top:none; }
/* Нижний отступ нужен на узком окне: сетка ячеек переносит их друг под друга, и без него
   подпись «мин · худшее 104» упиралась в следующий ярлык. На широком окне он не мешает. */
.panel.stat .cell { padding:0 32px 20px 0; border-left:none; box-shadow:none; }
.panel.stat .cl { font-weight:600; margin-bottom:10px; }
/* Главное число — крупное, ярким тоном происхождения. «Худшее» под ним — мелкое, обычного
   веса и приглушённое: разница обязана считываться мгновенно, до чтения. */
.panel.stat .cv { font-size:var(--fs-1); font-weight:700; line-height:var(--lh-1); color:var(--calc); }
.panel.stat .cs { margin-top:8px; font-size:var(--fs-3); font-weight:400; color:var(--muted); }
.legend { font-size:var(--fs-3); color:var(--muted); margin:2px 0 10px 0; }
.small { font-size:var(--fs-3); color:var(--muted); }
.tcap { font-size:var(--fs-3); color:var(--muted); margin:8px 0 0 0; }
div[data-testid="stMetric"] { background:var(--soft); border:1px solid var(--line); border-radius:10px; padding:8px 12px; }
div[data-testid="stExpander"] details { border-radius:10px; }
footer { visibility:hidden; }
</style>
"""

VERDICT_TITLE = {
    'preferred': 'Есть предпочтительное окно',
    'equivalent': 'Окна равнозначны по учтённым механизмам',
    'trade_off': 'Компромисс: механизмы указывают на разные окна',
    'all_need_check': 'Все окна требуют проверки аналитиком',
    'insufficient': 'Оснований для рекомендации недостаточно',
}
# Почему на всей сетке порогов нет предпочтительного окна — по вердикту, а не одной фразой на все случаи
NO_PICK_ON_GRID_RU = {
    'all_need_check': 'на всей сетке порогов автоматический выбор не делается: у каждого окна условие',
    'equivalent': 'на всей сетке порогов окна остаются равнозначными',
    'trade_off': 'на всей сетке порогов механизмы указывают на разные окна',
    'insufficient': 'на всей сетке порогов оснований для рекомендации недостаточно',
}
# Тот же синий, что у --calc в стилях: на графиках цвет означает то же, что на плашках, —
# происхождение величины. Минуты в аномалии и флюенс считает сервис, поэтому оба ряда ленты синие.
# Значение — стальной синий тёмной темы: на фоне #08090b тёмно-синий #1f4e79 светлой темы не читается.
CALC_BLUE = '#a1bed5'
# Заливка полосы-подложки: тот же синий с прозрачностью 0,22 — подложка обязана читаться как фон
# под кривой, а не как второй такой же ряд. Значение прозрачности подобрано так, чтобы контраст
# самой заливки к фону панели оставался различимым, но не спорил с линией профиля.
CALC_BLUE_FILL = 'rgba(161,190,213,0.22)'
# Пик профиля — тем же красным, которым на всём экране помечается условие проверки и аномалия
# (--cond, #c17d68): это единственное место графика, где красный означает «худшее время».
PEAK_RED = '#c17d68'
COV_RU = {'full': 'полное', 'partial': 'частичное', 'none': 'нет'}
# покрытие — свойство нашего расчёта, а не «хорошо/плохо»: полное синим, частичное янтарём, нет — серым
COV_KIND = {'full': 'calc', 'partial': 'warn', 'none': 'none'}
MECH_RU = {'spaceweather': 'космопогода', 'mmod_stat': 'метеороиды', 'conjunctions': 'сближения'}
KIND_PILL = {'observation': ('наблюдение', 'obs'), 'external_forecast': ('внешний прогноз', 'fc'), 'own_calculation': ('наш расчёт', 'calc')}
# уровень предупреждения — текстом, без эмодзи (одинаково в Windows, macOS и на телефоне).
# У информационной карточки уровня нет, и пустая строка тут значит именно это: заголовок свёртки
# собирается пропуском пустых частей (card_label_ru), а не печатает прочерк первым знаком строки.
SEV_RU = {'critical': 'КРИТИЧНО', 'limiting': 'ВНИМАНИЕ', 'info': ''}
# одна строка, объясняющая цвет: он означает происхождение величины, а не «безопасно/опасно».
# Три происхождения названы без союза «или» (R4-28): кеш — это давность наблюдения, а не прогноз.
COLOR_LEGEND = ('Цвет означает происхождение: синий — наш расчёт, зелёный — наблюдение источника (в том числе взятое '
                'из кеша: давность стоит подписью), янтарный — внешний прогноз. Красный — условие проверки, аномалия '
                'или отказ источника; серый — данных нет.')
# Тринадцатый круг: на главном экране от легенды остаётся РЯД ПЛАШЕК и один ярлык под ним.
# Плашка сама себя называет — «наш расчёт», «наблюдение», «внешний прогноз», — и перечислять
# то же самое ещё и словами значит писать одно и то же дважды. Полная формулировка (COLOR_LEGEND)
# стоит во вкладке «Методика» и на профессиональном уровне.
COLOR_LEGEND_SHORT = 'Цвет величины — её происхождение.'
# Причина, по которой на сетке порогов нет предпочтительного окна, — короткой вставкой в одну фразу
# об устойчивости (R4-18). Отличается от NO_PICK_ON_GRID_RU падежом и тем, что не повторяет «на сетке».
NO_PICK_REASON_RU = {
    'all_need_check': 'у каждого окна есть условие проверки, автоматический выбор не делается',
    'equivalent': 'окна остаются равнозначными',
    'trade_off': 'минуты в аномалии и флюенс указывают на разные окна',
    'insufficient': 'обязательной линии не хватает покрытия',
}

# --- словари перевода идентификаторов модулей (О5: без английских идентификаторов на экране) ---
METHOD_RU = {'sgp4': 'SGP4 по TLE', 'oem_interp': 'OEM NASA/JSC (интерполяция)'}
STRICT_RU = {'strict': 'строгая', 'declared_reconstruction': 'объявленная реконструкция',
             'reconstruction': 'реконструкция', 'unavailable': 'недоступна'}
EVENT_KIND_RU = {'SEP': 'протонное событие', 'GST': 'геомагнитная буря', 'FLR': 'вспышка', 'CME': 'выброс массы',
                 'CME_ARRIVAL': 'прогноз прихода выброса', 'IPS': 'межпланетный удар', 'HSS': 'высокоскоростной поток',
                 'RBE': 'усиление радиационного пояса', 'MPC': 'пересечение магнитопаузы', 'GST_KP': 'буря (Kp)'}
SOURCE_RU = {'orbit': 'орбита', 'noaa_swpc_3day_forecast': 'трёхсуточный прогноз NOAA SWPC', 'noaa_swpc_goes': 'GOES ≥10 МэВ (NOAA SWPC)', 'gfz_kp': 'Kp (GFZ)',
             'ost1044_belts': 'таблицы ОСТ 134-1044-2007 (захваченные протоны)',
             'ecss_grun': 'модель метеороидов ECSS/Grün', '_layers': 'слои программы',
             'noaa_swpc_3day_forecast': 'трёхсуточный бюллетень NOAA SWPC (живой выпуск)',
             'donki_archive': 'архив DONKI: события, уведомления, прогоны ENLIL',
             # двенадцатый круг: лента уведомлений опрашивается живьём, и её ключ уходит
             # на экран и в отчёт; английский идентификатор там недопустим
             'donki_live': 'уведомления NASA DONKI (живая лента)',
             'noaa_forecast_kp_forecast': 'прогноз Kp NOAA',
             'noaa_forecast_s1_prob_daily': 'прогноз NOAA: вероятность S1+ за сутки',
             'noaa_forecast_proton_prob_daily': 'прогноз NOAA: вероятность протонного события за сутки',
             # живой слой источников кладёт элементы орбиты под своим ключом; на оперативном уровне
             # английский идентификатор на экране недопустим (бриф §9.8)
             'celestrak_gp': 'элементы орбиты МКС (двухстрочные, TLE)'}
# Откуда взят выпуск внешнего прогноза — свойство режима, а не источника: в текущем режиме отсечки
# нет вовсе, и подпись «выпуск до отсечки» у живого бюллетеня была неправдой (О2).
RELEASE_BY_MODE_RU = {'live': 'живой выпуск', 'history_forecast': 'выпуск до отсечки',
                      'history_review': 'выпуск из архива'}
# Ограничения модуля орбиты (A3) — перевод по точному совпадению; неизвестная строка выводится
# как есть с пометкой «текст модуля орбиты».
LIMIT_RU = {
    'TLE age limit is an engineering guard, not a position-error guarantee; manoeuvres are not predicted.':
        'Предел возраста TLE — инженерное ограничение, не оценка ошибки положения; манёвры не предсказываются.',
    'IGRF is the internal main field; no storm-time external field is modelled.':
        'IGRF — внутреннее главное поле; внешнее поле бури не моделируется.',
    'L, B/B0 and vertical cutoff use a centred tilted dipole; not traced McIlwain L or directional storm-time rigidity.':
        'L, B/B_0 и вертикальное обрезание — центральный наклонный диполь, не трассированная L Мак-Илвейна '
        'и не направленная жёсткость обрезания во время бури.',
    'Full IGRF |B| is separate from dipole B/B0; they must not be mixed to infer an IGRF equatorial field.':
        'Полное |B| по IGRF и дипольное B/B_0 — разные величины; смешивать их для оценки экваториального поля нельзя.',
    'SAA flag is the configured |B| threshold proxy, not an official region boundary.':
        'Признак аномалии — порог |B| из настроек, не официальная граница области.',
    'OEM timestamps of creation/modification do not prove historical public availability.':
        'Времена создания и изменения OEM не доказывают, что файл был публично доступен в тот момент.',
}
# Правило предпочтения — пять шагов, понятных без внутреннего договора (DEMO-6, O5-14).
RULE_RU = [
    ('п.1', 'шаг 1 из 5, охват: у обязательной линии нет данных — рекомендация невозможна'),
    ('п.2: все окна', 'шаг 2 из 5, условия: у каждого окна есть условие проверки, автоматический выбор не делается — решение за аналитиком'),
    ('п.2: единственное', 'шаг 2 из 5, условия: только одно окно без условий проверки'),
    ('п.3–4', 'шаги 3–4 из 5, сравнение и сведение: меньше минут в аномалии, линия метеороидов не противоречит'),
    ('п.4', 'шаг 4 из 5, сведение: механизмы указывают на разные окна — компромисс без победителя'),
    ('п.5', 'шаг 5 из 5, допуск равнозначности'),
]
BOOL_RU = {True: 'да', False: 'нет', None: '—'}
# Пресеты запроса (И1): одна кнопка выставляет режим, дату, час и окна. Значения — те же,
# что у сохранённых примеров examples/, чтобы показанное на защите воспроизводилось из файла.
PRESETS = [
    {'key': 'now', 'label': 'Сейчас', 'mode': 'Текущая обстановка', 'date': None, 'hour': None,
     'duration_min': 360, 'search_min': 720, 'offsets_min': [0, 240],
     'shows': 'живые источники на момент нажатия: GOES ≥10 МэВ, Kp и элементы орбиты'},
    {'key': 'gannon', 'label': 'Буря Гэннон, 10.05.2024 12:00 UTC', 'mode': 'Прогноз из прошлого',
     'date': (2024, 5, 10), 'hour': 12, 'duration_min': 360, 'search_min': 720, 'offsets_min': [0, 240],
     'shows': 'только публикации до 10.05 12:00 UTC; на умолчаниях порогов оба окна получают условие проверки'},
    {'key': 'quiet', 'label': 'Тихая дата, 25.06.2024 12:00 UTC', 'mode': 'Прогноз из прошлого',
     'date': (2024, 6, 25), 'hour': 12, 'duration_min': 360, 'search_min': 1440, 'offsets_min': [0, 480],
     'shows': 'только публикации до 25.06 12:00 UTC; условий проверки нет, окна сравниваются по величинам'},
]


def esc(s) -> str:
    return html.escape(str(s), quote=False)


def raw_record(raw_records: dict | None, rid: str):
    """Сырая запись по идентификатору карточки.

    Идентификатор записи — «источник:выпуск:хеш[:тип события]». Слой истории (A2) добавляет тип
    к идентификатору в карточке («…:CME_ARRIVAL»), а в раздел сырых записей та же запись попадает
    без него: поиск в лоб её не находил, и карточка условия печатала «записей без ссылки» там, где
    у записи есть и адрес, и тело. Ищем точное совпадение, затем отбрасываем последние поля.

    Третья ступень — по ИМЕНИ ИСТОЧНИКА, и только когда запись этого источника ровно одна.
    В текущем режиме карточки ссылаются на запись трассы «celestrak_gp:25544:aedc1743d259», а слой
    источников кладёт её под ключом «celestrak_gp:<64 hex>»: совпадения нет ни на одном шаге, и
    четыре главные карточки вкладки «Объяснения» печатали «адрес записи не сохранён слоем
    источников», хотя адрес есть в metadata.url и выгрузка его печатает. Путь О4 «от предупреждения
    к первоисточнику» был разорван. Если записей источника несколько, ступень молчит: подставить
    чужую запись к числу хуже, чем честно сказать, что адреса нет.
    """
    if not raw_records or not rid:
        return None
    rec = raw_records.get(rid)
    if rec is not None:
        return rec
    parts = str(rid).split(':')
    while len(parts) > 2:
        parts = parts[:-1]
        rec = raw_records.get(':'.join(parts))
        if rec is not None:
            return rec
    src = str(rid).split(':')[0]
    hits = [v for k, v in raw_records.items() if str(k).split(':')[0] == src]
    return hits[0] if len(hits) == 1 else None


def record_url(rec) -> str | None:
    """Адрес первоисточника одной сырой записи — единственное место, где он ищется (О4).

    Слои источников кладут адрес по-разному: живые записи A4 и уведомления DONKI — в
    `metadata.url`, архивные выпуски NOAA — в `url` верхнего уровня, сообщения DONKI старого
    разбора — в `messageURL`. Экран и выгрузка спрашивают адрес только здесь, иначе «ссылки нет»
    печатается там, где ссылка есть (найдено третьим кругом: карточка условия окна 2 на Гэннон).
    Порядок проверки: url → link → messageURL → metadata.url.
    """
    if not isinstance(rec, dict):
        return None
    for key in ('url', 'link', 'messageURL'):
        v = rec.get(key)
        if isinstance(v, str) and v.strip().startswith(('https://', 'http://')):
            return v.strip()
    md = rec.get('metadata')
    if isinstance(md, dict):
        v = md.get('url')
        if isinstance(v, str) and v.strip().startswith(('https://', 'http://')):
            return v.strip()
    return None


# Записи, которые сервис везёт с собой: таблицы стандартов, коэффициенты поля, календарь потоков
# и эфемериды NASA/JSC из репозитория. У них сетевого адреса нет и быть не должно. Всё остальное
# без адреса — живая запись, у которой слой источников адрес не сохранил: адрес получения есть
# в манифесте выгрузки, и называть её «в составе сервиса» неправда (R4-10).
BUILTIN_RECORD_PREFIXES = ('igrf', 'ost1044', 'ecss', 'imo_', 'nasa_jsc_oem', 'orbit_provenance')


def record_label_ru(rid: str) -> str:
    """Подпись ссылки на первоисточник для ОПЕРАТИВНОГО уровня (бриф §9.8).

    Слой объяснений печатает «запись celestrak_gp:25544:aedc1743d259», когда имени источника он не
    знает, — и на экран выходит английский идентификатор с хешем. Для источников, у которых имя есть
    в SOURCE_RU, подписью служит это имя; для остальных остаётся прежняя подпись слоя объяснений:
    у двух уведомлений DONKI с разными числами она называет каждое по номеру выпуска, и заменять её
    именем источника нельзя — станет непонятно, какая ссылка к какому числу.
    """
    sid = str(rid or '').split(':')[0]
    if sid in SOURCE_RU:
        return SOURCE_RU[sid]
    from vkd.explain.format import record_ru as _record_ru
    return _record_ru(rid, with_kind=False)


def record_no_url_ru(rids, raw_records: dict | None = None) -> str:
    """Почему у записей нет ссылки — раздельно по причине, без общего «в составе сервиса».

    Причин три, и называть их одной фразой нельзя (О4): (1) запись сервис везёт с собой — таблицы
    стандартов, коэффициенты поля, эфемериды; (2) запись источника есть в выгрузке, но адреса
    в ней не сохранено; (3) самой записи в выгрузке нет — сказать про «адрес» здесь было бы
    неправдой, нет и тела. `raw_records` нужен, чтобы отличить (2) от (3); без него причина (3)
    не называется, и текст остаётся прежним.
    """
    n_ru = lambda n: '%d %s' % (n, plural_ru(n, ('запись', 'записи', 'записей')))
    builtin = [r for r in rids if str(r).split(':')[0].startswith(BUILTIN_RECORD_PREFIXES)]
    rest = [r for r in rids if r not in builtin]
    if raw_records is None:
        live, absent = rest, []
    else:
        live = [r for r in rest if raw_record(raw_records, r) is not None]
        absent = [r for r in rest if raw_record(raw_records, r) is None]
    parts = []
    if builtin:
        parts.append('%s из состава сервиса (таблицы стандартов, коэффициенты поля, эфемериды) — сами записи '
                     'в выгрузке, папка raw/' % n_ru(len(builtin)))
    if live:
        parts.append('%s живого источника — тело записи в выгрузке есть, сетевого адреса в ней не сохранено, '
                     'адрес получения есть в манифесте выгрузки' % n_ru(len(live)))
    if absent:
        parts.append('%s источника в сырые записи этого расчёта не попало — ни адреса, ни тела; '
                     'происхождение величины смотрите в таблице источников вкладки «Данные»' % n_ru(len(absent)))
    return 'без сетевого адреса: ' + '; '.join(parts) if parts else ''


def pill(text, kind='none') -> str:
    return '<span class="pill pill-%s">%s</span>' % (kind, esc(text))


_SUP = {'0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹',
        '-': '⁻', '+': ''}
_POW_RE = re.compile(r'10\^([+-]?\d+)')
_EXP_RE = re.compile(r'(?<![\w.])(\d+(?:[.,]\d+)?)[eE]([+-]?\d+)(?![\w])')
# дробь с точкой, но не дата (01.05.2024), не «10.05 12:00» и не номер версии (v3.1).
# Перечисление через дробную черту («канал 12.5/30/50 МэВ» в основании допуска) тоже число,
# а не дата: следом за ним стоит другое число, а не буква месяца.
# Скобка после числа тоже конец числа («Kp до 8.67 (наблюдённый Kp уведомления)»): даты в готовых
# строках модулей на этот момент ещё в виде «05-10 15:00Z» — точек в них нет, их ставит dates_ru позже.
# Единица «pfu» пишется латиницей, и по прежнему правилу дробь перед ней запятой не получала:
# на экране стояло «GOES ≥10 МэВ = 77.5 pfu» рядом с «207 pfu» и «2,43·10⁶ част./см²» того же
# блока. Найдено дампом чисел экрана, а не на глаз (замечание владельца о размерностях).
_FRAC_RE = re.compile(r'(?<![\d.A-Za-zА-Яа-я])(\d+)\.(\d+)(?=\s*(?:[А-Яа-я%·)(\],;]|/\d|pfu\b|$))')


def sup(text) -> str:
    """Степени надстрочными цифрами: «3,36·10^6» → «3,36·10⁶», «5,64·10^-7» → «5,64·10⁻⁷» (U4)."""
    return _POW_RE.sub(lambda m: '10' + ''.join(_SUP.get(c, c) for c in m.group(1)), str(text))


def frac_ru(text) -> str:
    """Единый формат чисел в готовых строках: дробь с запятой, порядок надстрочными цифрами.
    Даты вида 01.05.2024 и 10.05 12:00 не трогаются."""
    s = _EXP_RE.sub(lambda m: '%s·10^%s' % (m.group(1).replace('.', ','), int(m.group(2))), str(text))
    s = _FRAC_RE.sub(lambda m: '%s,%s' % (m.group(1), m.group(2)), s)
    return sup(s)


def fmt(v, unit='') -> str:
    """Число по-русски: запятая, порядок надстрочными цифрами (10⁶, 10⁻⁷);
    безразмерная единица «1» не печатается."""
    if v is None:
        return '—'
    if isinstance(v, float):
        if v == 0:
            s = '0'
        elif abs(v) >= 1e5 or abs(v) < 1e-2:
            m, e = ('%.2e' % v).split('e')
            s = sup('%s·10^%d' % (m.replace('.', ','), int(e)))
        elif abs(v) >= 100:
            s = '%.0f' % v
        else:
            s = ('%.2f' % v).rstrip('0').rstrip('.').replace('.', ',')
    else:
        s = str(v)
    return s + (' ' + unit if unit and unit not in ('1',) else '')


# Флюенс на одном экране обязан печататься ОДНИМ видом. У fmt порог степенной записи 1e5, и
# в одной строке правила стояло «флюенс ниже (61,59 против 57605 част./см², отношение ×0,00)»
# рядом с «1,73·10⁵» в соседней карточке — сопоставлять такую пару нечем (проверено прогоном
# history_forecast 20.05.2024 12:00, 60 мин, период 1440, сдвиги 0/480). Для величин флюенса
# порог опущен до 1e4 и задан здесь константой, а не числом по месту.
FLUENCE_POW_FROM = 1e4


def fmt_fluence(v, unit: str = '') -> str:
    """Флюенс единым видом: от 10⁴ — степенная запись («5,76·10⁴», «1,73·10⁵»)."""
    if v is None:
        return '—'
    try:
        x = float(v)
    except (TypeError, ValueError):
        return fmt(v, unit)
    if x != 0 and abs(x) >= FLUENCE_POW_FROM:
        m, e = ('%.2e' % x).split('e')
        s = sup('%s·10^%d' % (m.replace('.', ','), int(e)))
        return s + (' ' + unit if unit and unit not in ('1',) else '')
    return fmt(x, unit)


# То же для ГОТОВЫХ строк слоя сравнения, которые экран не собирает сам: голое целое ≥ 10⁴
# прямо перед единицей «част./см²» — это флюенс, и он приводится к степенной записи до того,
# как разряды тысяч успеют сделать из него «57 605» — третий вид записи одной величины.
_FLUENCE_INT_RE = re.compile(r'(?<![\d,.·])(\d{5,})(?=\s*част\.)')


def fluence_sci_ru(text) -> str:
    """«57605 част./см²» → «5,76·10⁴ част./см²»; всё остальное не трогается."""
    return _FLUENCE_INT_RE.sub(lambda m: fmt_fluence(float(m.group(1))), str(text or ''))


NBSP_THIN = ' '          # узкий неразрывный пробел: «24 000 нТл» не рвётся по строкам


def nbsp_thousands(v) -> str:
    """Разряды тысяч узким неразрывным пробелом (PROPOSAL_A п. 2.2, правило 3): 24000 → «24 000»."""
    if v is None:
        return '—'
    s = fmt(v)
    m = re.match(r'^(-?)(\d+)(,\d+)?$', s)
    if not m:
        return s
    whole = m.group(2)
    return m.group(1) + NBSP_THIN.join(_groups3(whole)) + (m.group(3) or '')


def spread_offsets(offsets, search_min: int, step: int = 30) -> list[int]:
    """Развести совпавшие сдвиги окон внутри периода поиска (U1): последнее окно — в конец
    периода, предыдущие на шаг раньше. Раскладка ползунков, а не оценка риска: экран из-за
    сжатия периода не должен останавливаться. Различные сдвиги возвращаются как есть."""
    out = sorted(int(o) for o in offsets)
    n = len(out)
    if n == 0 or len(set(out)) == n:
        return out
    if search_min >= (n - 1) * step:
        return [int(search_min) - (n - 1 - i) * step for i in range(n)]
    if search_min >= n - 1:                      # период короче шага: разводим равномерно
        k = int(search_min) // (n - 1)
        return [i * k for i in range(n)]
    return out                                    # развести нечем — решает проверка запроса


def head(title: str, sub: str) -> str:
    return '<div class="vk-head"><div class="vk-title">%s</div><div class="vk-sub">%s</div></div>' % (esc(title), esc(sub))


def plural_ru(n: int, forms: tuple[str, str, str]) -> str:
    """«733 записи», «1 запись», «5 записей»: число с существительным в нужной форме."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def dt_ru(t, with_date: bool = True, with_utc: bool = False) -> str:
    """Единый формат времени на экране: «10.05 12:00 UTC» или «12:00». Слово UTC — только там,
    где оно не повторяется у каждой ячейки (PROPOSAL_A п. 2.2, правило 5)."""
    if t is None:
        return '—'
    s = t.strftime('%d.%m %H:%M') if with_date else t.strftime('%H:%M')
    return s + (' UTC' if with_utc else '')


def age_ru(minutes, limit_min=None, limit_ru: str | None = None) -> str:
    """Давность в одних единицах: до 180 мин — минуты, до 48 ч — часы, дальше — сутки.
    Порог печатается рядом, если задан: «давность 92 мин (предел 60 мин)»."""
    if minutes is None:
        return 'давность не определена'
    m = float(minutes)
    if m < 180:
        s = 'давность %s мин' % fmt(round(m))
    elif m < 48 * 60:
        s = 'давность %s ч' % fmt(round(m / 60))
    else:
        s = 'давность %s сут' % fmt(round(m / 1440))
    lim = limit_ru if limit_ru is not None else (('%s мин' % fmt(round(float(limit_min)))) if limit_min is not None else None)
    return s + ((' (предел %s)' % lim) if lim else '')


def panel(rows, cls: str = '') -> str:
    """Приборная полоса в две строки (PROPOSAL_A п. 3). rows — список строк, строка — список ячеек
    (метка, значение, подпись, kind). kind из calc|obs|fc|cond|none задаёт левую границу и цвет числа.
    Давность печатается только здесь: в карточках окон её нет (дублирование = шум).

    `cls` добавляет класс к полосе. Значение одно — 'stat': тот же каркас, но значение кеглем
    ответа. Ряд крупных чисел под решением собран этим же кодом намеренно: у ячейки уже есть
    левая грань цветом происхождения, ярлык капителью и подпись — второго каркаса ради трёх
    чисел заводить незачем.
    """
    out = ['<div class="panel%s">' % ((' ' + cls) if cls else '')]
    for row in rows:
        out.append('<div class="prow">')
        for label, value, sub, kind in row:
            out.append('<div class="cell k-%s"><div class="cl">%s</div><div class="cv">%s</div><div class="cs">%s</div></div>'
                       % (kind or 'none', esc(label), esc(value), esc(sub or '')))
        out.append('</div>')
    out.append('</div>')
    return ''.join(out)


def split_panel_rows(rows, max_len: int = 80):
    """Разделить подписи ячеек полосы на короткую и полную.

    Владелец о прежней полосе: «мелким текстом и текстом разных тонов адекватно с ходу
    воспринимать информацию просто невозможно». У каждой из шести ячеек стояла приглушённая
    подпись в две-три строки, и полоса занимала пол-экрана. Теперь в ячейке остаётся ПЕРВАЯ
    смысловая часть (до разделителя и не длиннее max_len), а полная подпись целиком печатается
    раскрытием под полосой — ничего не потеряно.

    Возвращает строки с короткими подписями и список пар «метка, полная подпись» для раскрытия.
    """
    short_rows, details = [], []
    for row in rows:
        out_row = []
        for label, value, sub, kind in row:
            full = str(sub or '')
            # Берём подряд столько смысловых частей, сколько влезает в одну строку: у ячейки
            # «Элементы орбиты» это эпоха И давность, а не одна эпоха — давность здесь несёт смысл.
            kept = []
            for part in [x.strip() for x in full.split(' · ') if x.strip()]:
                candidate = ' · '.join(kept + [part])
                if kept and len(candidate) > max_len:
                    break
                kept.append(part)
            head_ = ' · '.join(kept)
            if len(head_) > max_len:
                head_ = head_[:max_len].rstrip(' ,;.') + '…'
            out_row.append((label, value, head_, kind))
            if full and full != head_:
                details.append((label, full))
        short_rows.append(out_row)
    return short_rows, details


def rule_ru(rule_applied: str, basis: bool = True) -> str:
    """«п.5: разница 48 мин меньше допуска 49 мин» → «шаг 5 из 5, допуск равнозначности: разница …».

    Для шагов 3–4 и 5 печатается ВЫЧИСЛЕННОЕ правило, а не заготовленная фраза: заготовка на шаге
    3–4 называла только минуты в аномалии и умалчивала о флюенсе — целевой величине сравнения окон,
    которая у выбранного окна может быть выше (найдено третьим кругом на пресете «Тихая дата»).
    Основание допуска на шаге 5 — разброс РАЗНОСТИ минут между двумя лучшими окнами (формула (9)),
    а не разброс абсолютных минут одного окна: порог сдвигает оба окна синфазно.

    `basis=False` снимает хвост об основании допуска: на оперативном уровне он стоит отдельной
    строкой под вердиктом, уже с числами сетки, и в правиле повторялся бы вторым сообщением об одном.
    """
    for prefix, text in RULE_RU:
        if rule_applied.startswith(prefix):
            tail = rule_applied.partition(': ')[2].strip()
            if prefix == 'п.5':
                return text + (': ' + tail if tail else '') + (
                    '; допуск — разброс разности минут между двумя лучшими окнами по сетке порогов' if basis else '')
            if prefix == 'п.3–4':
                return 'шаги 3–4 из 5, сравнение и сведение: ' + tail if tail else text
            return text
    return rule_applied


def _timeout_s() -> str:
    """Тайм-аут живого запроса из config/settings.toml — чтобы «за 6 с» не было зашито в текст."""
    try:
        from vkd.config import section as _sec
        return fmt(float(_sec('sources').get('timeout_s', 6)))
    except Exception:            # noqa: BLE001 — настройка недоступна: печатаем общую фразу
        return '6'


# Имена отказов сети из ответа источника — словами пользователя (U2). Значение с «%s» получает тайм-аут.
NET_ERR_RU = {
    'ReadTimeout': 'сервер не ответил за %s с',
    'ConnectTimeout': 'соединение не установлено за %s с',
    'Timeout': 'сервер не ответил за %s с',
    'ConnectionError': 'соединение с сервером не установлено',
    'ConnectionResetError': 'соединение разорвано сервером',
    'NewConnectionError': 'соединение с сервером не установлено',
    'HTTPError': 'сервер вернул ошибку',
    'SSLError': 'защищённое соединение не установлено',
    'TooManyRedirects': 'сервер перенаправляет запрос без конца',
    'JSONDecodeError': 'ответ не разобран',
    'ValueError': 'ответ не разобран',
    'KeyError': 'в ответе нет нужного поля',
    'RequestException': 'запрос к серверу не выполнен',
}
_NET_ALT = '|'.join(sorted(NET_ERR_RU, key=len, reverse=True))
_MODULE_RE = re.compile(r'\s*\((?:vkd|experiments|app|scripts|tests)\.[\w.]+\)')
_MODULE_BARE_RE = re.compile(r'(?:vkd|experiments|app|scripts|tests)\.[\w.]+')
_HASH_RE = re.compile(r'[;,]?\s*(?:sha256|SHA-256)\s+[0-9a-fA-F]+…?', re.I)
_FILE_RE = re.compile(r'[;,]?\s*файл\s+[^\s;,]+')
_RECORD_RE = re.compile(r'[;,]?\s*запис[ьи]\s+(?=[A-Za-z0-9])[\w:#.\-]*')   # «запись donki_msg#…», не «записи не указан»
# Перечень идентификаторов записей режется, содержательный текст после «записи:» — нет (R4-4):
# у условия по протонному событию источник звучит «записи: публикация 05-09 13:54Z — 05-10 14:19Z»,
# и прежнее выражение выносило с экрана ровно то время публикации, за которое даются баллы по О4.
_RECORDS_TAIL_RE = re.compile(r'[;,]?\s*запис[ьи]:\s+(?=[A-Za-z][\w.\-]*[:#])[^;]*', re.S)
_SRCID_RE = re.compile(r'\s*\([a-z][a-z0-9]*_[a-z0-9_]+\)')          # (celestrak_gp), (nasa_jsc_oem)
_CONTRACT_RE = re.compile(r'CONTRACT\.md(\s+v[\d.]+)?')
# идентификатор выпуска может содержать двоеточие («noaa_swpc_3day_forecast:fa0f21a1…» у живого
# слоя A4) — без него на оперативном уровне печатался 64-значный хеш
_RELEASE_RE = re.compile(r'выпуск\s+[\w:\-]*[A-Za-z][\w:\-]*\s+от\b')
_CHECK_RE = re.compile(r'[;,]?\s*контроль\s+[^;]+воспроизведён')
# Перебор адресов цепочки TLE: «проверка адресов: https://…?CATNR=25544&FORMAT=TLE: timeout».
# На оперативном уровне это техническая строка с параметрами запроса (бриф §9.8) — она режется
# целиком и остаётся на профессиональном уровне и в манифесте (R4-6, R4-27).
_PROBE_TAIL_RE = re.compile(r'[;,]?\s*проверка адресов:.*$', re.S)
_PROBE_FAIL_RE = re.compile(r'(?:%s|timeout|time-out)' % _NET_ALT, re.I)
# обороты слоёв программы, которым на оперативном уровне нужен русский (U2)
PHRASE_RU = [('request_incomplete', 'предыдущий запрос не завершён'),
             ('интеграл по dt', 'интеграл по времени'), ('Table J-6', 'табл. J-6'), ('Rev.1', 'ред. 1'),
             # «Ранжирование» — слово профессионального уровня (R4-18): на оперативном экране
             # оно ничего не объясняет. Движок перебора пишет им своё «почему», и перевод идёт
             # здесь, там же, где переводятся остальные слова слоёв.
             ('все ранжированные начала', 'все отобранные правилом начала'),
             ('ранжированные начала', 'отобранные правилом начала'),
             ('ранжированных начал', 'отобранных правилом начал'),
             ('ранжированное начало', 'отобранное правилом начало'),
             # «настройка config/settings.toml» стоит в скобке рядом с порогом различимости окон по
             # линии метеороидов: имя файла зрителю ничего не говорит, а происхождение числа — говорит
             ('настройка config/settings.toml', 'порог задан в настройках сервиса'),
             ('в config/settings.toml', 'в настройках сервиса'), ('config/settings.toml', 'настройки сервиса'),
             # английские обрывки слоя источников на оперативном уровне (R4-6): имя продукта NOAA и
             # служебное имя канала Kp переводятся, длинное — раньше короткого
             ('живой бюллетень NOAA 3-day', 'живой трёхсуточный бюллетень NOAA'),
             ('бюллетень NOAA 3-day', 'трёхсуточный бюллетень NOAA'),
             ('NOAA 3-day', 'трёхсуточный бюллетень NOAA'),
             ('отдельным выпуском NGDC daypre', 'отдельным суточным выпуском NOAA (архивным)'),
             ('NGDC daypre', 'отдельный суточный выпуск NOAA (архивный)'),
             ('незавершённый Kp-nowcast исключён', 'незавершённый 3-часовой интервал Kp в расчёт не взят'),
             # имена ключей настроек: на профессиональном уровне как есть, на оперативном — по-русски.
             # Ключи всегда стоят после слова «настройка/настройке/настройкой», поэтому заменяем само имя.
             ('sep_valid_hours', 'срока действия уведомления о протонном событии'),
             ('event_valid_hours', 'срока действия записи о буре или приходе выброса'),
             # поля прогона WSA-ENLIL: оценки Kp по углу межпланетного магнитного поля
             ('kp_180', 'угол поля 180°'), ('kp_135', 'угол поля 135°'), ('kp_90', 'при угле межпланетного поля 90°'),
             # номера решений команды и имена задач — только на профессиональном уровне и в выгрузке
             ('(R11)', '(решение команды по магнитным координатам)'),
             ('точки (A3)', 'точки трассы'), ('(A3)', '(модуль орбиты)'), ('из A3', 'из модуля орбиты'),
             ('работа A3', 'работа модуля орбиты')]
_INNER_ID_RE = re.compile(r',\s*[a-z][a-z0-9]*(?:[-_][a-z0-9]+)+(?=\))')
_CLEAN_RE = re.compile(r'\s{2,}')


def phrase_ru(text) -> str:
    """Обороты слоёв программы по-русски без удаления происхождения: имена настроек и модулей
    переводятся, хеши, файлы и идентификаторы записей остаются. Нужно выгрузке: отчёт должен
    говорить словами экрана, но не терять прослеживаемость (в отличие от status_ru)."""
    s = frac_ru(str(text or ''))
    for a, b in PHRASE_RU:
        s = s.replace(a, b)
    return s


def net_error_ru(name: str) -> str:
    """«ReadTimeout» → «сервер не ответил за 6 с»; неизвестное имя — общая фраза без кода."""
    t = NET_ERR_RU.get(name)
    if t is None:
        return 'источник не ответил'
    return (t % _timeout_s()) if '%s' in t else t


def status_ru(text, pro: bool = False) -> str:
    """Статус источника для экрана (U2). На профессиональном уровне — как есть, только числа
    приводятся к единому виду. На оперативном — без имён отказов, путей модулей, файлов, хешей,
    идентификаторов записей, перебора адресов и давности: их место — профессиональный уровень,
    отдельная колонка «давность, мин» и выгрузка.

    Давность вырезается здесь по той же причине, что и в карточках окон (PROPOSAL_A п. 3): в
    таблице 3 она уже стоит своей колонкой, и два округления одной величины в одной строке
    («давность, мин» = 103 и «давность данных 102,7 мин») читаются как два разных числа (R4-27)."""
    s = frac_ru(str(text or '')).replace('из архив ', 'из архива ')
    if pro or not s:
        return s
    m_probe = _PROBE_TAIL_RE.search(s)
    if m_probe:
        tail = m_probe.group(0)
        s = s[:m_probe.start()].rstrip(' ;,')
        if _PROBE_FAIL_RE.search(tail):
            s += '; часть адресов цепочки не ответила — взят тот, что назван выше'
    s = drop_age(s)
    s = re.sub(r'([\w.\-]+): (%s)\b' % _NET_ALT, lambda m: '%s — %s' % (m.group(1), net_error_ru(m.group(2))), s)
    s = re.sub(r'\((%s)\)' % _NET_ALT, lambda m: '(%s)' % net_error_ru(m.group(1)), s)
    s = re.sub(r'ответ не разбирается \([^)]*\)', 'ответ источника не разобран', s)
    s = s.replace(', HTTP 200', '').replace('HTTP 200', 'получено')
    s = re.sub(r'\s*\(ответ сохранён[^)]*\)', '', s)
    s = _MODULE_RE.sub('', s)
    s = _MODULE_BARE_RE.sub('расчёт сервиса', s)
    s = _HASH_RE.sub('', s)
    s = _FILE_RE.sub('', s)
    s = _RECORDS_TAIL_RE.sub('', s)
    s = _RECORD_RE.sub('', s)
    s = _SRCID_RE.sub('', s)
    s = _CONTRACT_RE.sub('договор команды,', s)
    s = _RELEASE_RE.sub('выпуск от', s)
    s = _CHECK_RE.sub('', s)
    s = _INNER_ID_RE.sub('', s)
    for a, b in PHRASE_RU:
        s = s.replace(a, b)
    s = re.sub(r'\s*\(\s*[;,]?\s*\)', '', s)        # скобки, опустевшие после удаления записи
    s = re.sub(r'[;,]\s*(?=[;,])', '', s)
    s = re.sub(r'договор команды,\s*,', 'договор команды,', s)
    s = _CLEAN_RE.sub(' ', s).strip()
    return screen_text(s.rstrip(' ;,'))


def close_cut_parens(text) -> str:
    """Закрыть скобку у фрагмента, обрезанного многоточием, и в конце строки (R4-13).

    Слой объяснений обрезает длинное тело уведомления многоточием, и обрез иногда приходится на
    середину скобки: «опубликованный прогноз: Kp до 8 (диапазон 6–8, верхняя граница, не…». На
    экране это незакрытая скобка в самом читаемом месте карточки. Текст фрагмента здесь не
    меняется — добавляется только закрывающая скобка там, где его оборвали.
    """
    s = str(text or '')
    if s.count('(') == s.count(')'):
        return s
    out, depth = [], 0
    for ch in s:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth = max(0, depth - 1)
        out.append(ch)
        if ch == '…' and depth > 0:
            out.append(')')
            depth -= 1
    return ''.join(out) + ')' * depth


def dedup_clauses(text) -> str:
    """Повторяющиеся через «;» части одной подписи показываем один раз (U5).
    Слой объяснений собирает источник по каждому фактору, и одна и та же фраза о траектории
    попадает в карточку дважды."""
    parts, seen, out = str(text or '').split('; '), set(), []
    for p in parts:
        k = p.strip().rstrip('.').lower()
        if k and k in seen:
            continue
        seen.add(k)
        out.append(p)
    return '; '.join(out)


# Ключ источника в снимке → ключ управления «Источники» в боковой панели. По нему берётся
# ЗАПРОШЕННОЕ состояние источника (S['request']['disabled']), а не подстрока в тексте статуса.
DISABLED_KEY = {'noaa_swpc_goes': 'goes', 'gfz_kp': 'kp', 'noaa_swpc_3day_forecast': 'noaa'}


def source_state(v: dict | None, requested=None) -> str | None:
    """Состояние источника: 'off' (исключён пользователем) | 'cache' (объявленный отказ) | None.

    Читается ТОЛЬКО из явного признака: сначала из снимка источника (ключ `state`, если слой расчёта
    его положит), затем из запроса — `S['request']['disabled']`. Из текста статуса состояние не
    выводится ни при каких условиях: слой источников штатно дописывает в него слово «исключён»
    («незавершённый Kp-nowcast исключён» — почти в каждом живом прогоне), и поиск подстроки объявлял
    исключённым пользователем источник, который отвечал по сети (R4-11, критическая находка).
    """
    st_ = (v or {}).get('state')
    if st_ in ('off', 'cache'):
        return st_
    if requested in ('off', 'cache'):
        return requested
    if requested is True:
        return 'off'
    return None


def source_short(v: dict, requested=None) -> tuple[str, str]:
    """Короткий статус источника для полосы состояния и таблицы 3: (текст, kind).
    `requested` — состояние из `S['request']['disabled']` для этого источника."""
    st_ = (v.get('status') or '').lower()
    if source_state(v, requested) == 'off':
        return 'исключён пользователем', 'crit'
    if v.get('live_ok') is False and v.get('from_cache'):
        age = v.get('age_min')
        return ('кеш, %s' % age_ru(age)) if age is not None else 'кеш', 'warn'
    if 'данных нет' in st_ or 'не разбирается' in st_:
        return 'нет ответа и кеша — данных нет', 'crit'
    if v.get('live_ok'):
        return 'живой запрос', 'ok'
    return status_ru(v.get('status') or 'нет данных'), 'none'


def orbit_created_ru(created) -> str:
    """«08.05.2024 16:49 UTC» из даты создания эфемериды; год обязателен — речь об архиве 2024 года."""
    if isinstance(created, str):
        try:
            created = datetime.fromisoformat(created)
        except ValueError:
            return created
    if not isinstance(created, datetime):
        return ''
    return created.strftime('%d.%m.%Y %H:%M') + ' UTC'


def source_issues(src: dict, th, mode: str, kp_excluded_hist: bool = False, tle_fetch: str | None = None,
                  pro: bool = False, disabled: dict | None = None, cutoff_utc=None,
                  orbit_created_utc=None) -> list[str]:
    """Проблемы источников для одного st.warning под полосой состояния (О5-2): исключён, кеш, устарел.
    Ничего не решает — переводит явные признаки снимка в предложения для пользователя."""
    out = []
    disabled = disabled or {}
    if mode == 'live':
        for sid, name in (('noaa_swpc_goes', 'GOES ≥10 МэВ (NOAA SWPC)'), ('gfz_kp', 'Kp (GFZ)')):
            v = src.get(sid) or {}
            state = source_state(v, disabled.get(DISABLED_KEY[sid]))
            if state == 'off':
                out.append('%s: исключён пользователем — %s' % (
                    name, 'обязательная линия без покрытия, рекомендации не будет' if sid == 'noaa_swpc_goes'
                    else 'условие по наблюдению Kp не проверяется, покрытие объявлено'))
            elif disabled.get(DISABLED_KEY[sid]) == 'cache':
                # Признак берётся ИЗ ЗАПРОСА, а не из состояния источника: слой источников ставит
                # state='cache' и при настоящем отказе сети, и фраза «вы перевели источник в режим
                # отказа» стала бы неправдой на обычном прогоне.
                # Т6: смоделированный пользователем отказ и настоящий отказ сети — разные вещи.
                # Раньше оба печатались одной фразой «живого ответа нет, взят кеш», и на показе
                # ничто не подтверждало, что отказ вызван проверкой, а не сетью.
                out.append('%s: вы перевели источник в режим отказа — живой запрос не выполнялся, взят кеш%s. '
                           'Покрытие частичное и объявлено; значения относятся к моменту последнего успешного '
                           'получения, а не к настоящему времени'
                           % (name, (', %s' % age_ru(v.get('age_min'))) if v.get('age_min') is not None else ''))
            elif v.get('live_ok') is False and v.get('from_cache'):
                age = v.get('age_min')
                out.append('%s: живого ответа нет, взят кеш%s — покрытие частичное, объявлено'
                           % (name, (', %s' % age_ru(age)) if age is not None else ''))
            elif v.get('live_ok') is False:
                out.append('%s: %s' % (name, status_ru(v.get('status') or 'нет данных', pro)))
            elif sid == 'noaa_swpc_goes' and v.get('age_min') is not None and th is not None \
                    and v['age_min'] > th.goes_max_age_min:
                out.append('GOES ≥10 МэВ: наблюдение устарело (%s) — для будущих участков окна покрытие частичное'
                           % age_ru(v['age_min'], th.goes_max_age_min))
        o = src.get('orbit') or {}
        _orb: list[str] = []
        if o.get('live_ok') is False and o.get('from_cache'):
            # «по кешу (кеш, давность 31 ч)» дублировало слово и оставляло пустые скобки, когда
            # происхождение снимка неизвестно (R4-27): давность берётся из той же строки один раз
            _, _, _age = tle_origin(tle_fetch).partition(', ')
            _orb.append('живого ответа нет — орбита построена по проверенному %s%s'
                        % ('снимку репозитория' if 'снимок' in (tle_fetch or '') else 'кешу',
                           (', ' + _age) if _age.startswith('давность') else ''))
        if o.get('age_h') is not None and o['age_h'] > 24:
            # давность печатается ОДИН раз: если она уже названа строкой о кеше, здесь только факт
            _age_txt = '' if any('давность' in x for x in _orb) else ' (%s)' % age_ru(o['age_h'] * 60)
            _orb.append('эпоха старше суток%s — точность положения снижается, предел %s сут задан порогом'
                        % (_age_txt, fmt(th.tle_max_age_days) if th is not None else '—'))
        if _orb:
            # Бриф §9.7: нет двух сообщений об одном и том же. Прежде об ОДНОМ источнике элементов
            # орбиты печаталось две строки подряд, и жёлтая плашка над вердиктом разрасталась
            # до четырёх пунктов, уводя ответ на вопрос «Когда выходить?» вниз экрана.
            out.append('Элементы орбиты: ' + '; '.join(_orb))
    else:
        if kp_excluded_hist:
            out.append('Kp: исключён пользователем из архива (проверка отказа) — условие по наблюдению Kp не проверяется')
        # C3: численный архив наблюдений GOES 2024 есть, но в строгий режим он не идёт.
        # Причина берётся из снимка и называется один раз, здесь, а не плашкой в полосе состояния.
        g = src.get('noaa_swpc_goes') or {}
        if not g.get('data_utc'):
            out.append('GOES ≥10 МэВ: численного наблюдения на этот момент нет — %s. Линия протонного события '
                       'держится на пороговых уведомлениях DONKI; покрытие объявлено'
                       % status_ru(g.get('status') or 'причина в записи источника не указана', pro))
        o = src.get('orbit') or {}
        if o.get('strictness') == 'declared_reconstruction':
            # Слово «отсечка» уместно только там, где отсечка есть. В «Историческом разборе»
            # cutoff_utc = None, и прежняя безусловная фраза противоречила двум другим блокам
            # того же экрана (R4-4): полосе состояния и вкладке «Данные».
            if cutoff_utc:
                out.append('Орбита: OEM NASA/JSC создан до отсечки, но его публичная доступность в тот момент '
                           'не доказана — объявленная реконструкция')
            else:
                _c = orbit_created_ru(orbit_created_utc)
                out.append('Орбита: эфемерида OEM NASA/JSC%s; её публичная доступность в тот момент документально '
                           'не доказана — объявленная реконструкция' % ((' создана ' + _c) if _c else ''))
    return out


_AGE_IN_LINE_RE = re.compile(r'давность\s+[^,;.—]+')


def source_issues_short_ru(issues: list[str]) -> str:
    """Свести блок состояния источников к ОДНОЙ строке над вердиктом (бриф §9.1 и §9.7).

    Ответ на вопрос «Когда выходить?» обязан стоять первым и без прокрутки, а над ним стояла
    жёлтая плашка на четыре пункта. Здесь остаётся одна строка с именами источников и давностями;
    полный перечень никуда не девается — он в свёртке рядом и в таблице реестра.
    """
    if len(issues) < 2:
        return ''
    parts = []
    for x in issues:
        name = str(x).partition(':')[0].strip()
        m = _AGE_IN_LINE_RE.search(str(x))
        age = m.group(0).replace('давность ', '').strip() if m else ''
        parts.append(('%s %s' % (name, age)) if age else name)
    # Тринадцатый круг: строка стала ЯРЛЫКОМ — имена источников с давностями и три слова о
    # покрытии. «Подробности — ниже и во вкладке „Данные“» с экрана убрано: раскрытие с перечнем
    # стоит прямо под этой строкой и само себя называет.
    return 'Источники: %s — покрытие частичное, объявлено' % ', '.join(parts)


def coverage_consequence_ru(missing: list[str] | tuple) -> str:
    """Следствие из строки нехватки: что из этого следует для вердикта (О1, О2).

    На экране стояло «GOES: наблюдение 08:20 покрывает 0 % окна, прогноза потока протонов на окно
    нет» — сказано, чего нет, но не сказано, что из этого следует. Аналитик спрашивает: «и что?».
    """
    if not missing:
        return ''
    return ('Следствие: по протонному потоку окно не оценивается — обязательная линия без покрытия, '
            'поэтому рекомендации не будет; числа сравнения ниже посчитаны по минутам в аномалии '
            'и флюенсу захваченных протонов, которые от этой линии не зависят.')


def _win_num(windows_ru: dict, t) -> str:
    return windows_ru.get(t, '')


def _hhmm_to_window(assessments, hhmm: str):
    for i, a in enumerate(assessments or []):
        if a.window.start_utc.strftime('%H:%MZ') == hhmm:
            return i + 1
    return None


def _wins_ru(nums: list[int]) -> str:
    nums = sorted(set(nums))
    if len(nums) == 1:
        return 'окно %d' % nums[0]
    return 'окна ' + ' и '.join(str(n) for n in nums) if len(nums) == 2 else 'окна ' + ', '.join(str(n) for n in nums)


def verdict_items(rec, assessments, mech_ru: dict | None = None) -> list[dict]:
    """Пункты блока «Почему?» с их родом (О3-3). Один пункт — словарь:

    `text` — строка как её печатает экран;
    `kind` — `cond` (условие проверки окна), `cmp` (сравнение по механизму) или `other`
             (покрытие, чего не хватает и прочие пояснения слоя сравнения);
    `mech` — идентификатор механизма для `cmp`, иначе None.

    Род нужен панели вердикта: условия и сравнение — это и есть ответ на «почему», а пояснения
    о покрытии слой расчёта даёт и общей строкой, и отдельно по каждому окну, и в блоке они
    читаются как три формулировки об одном и том же.
    """
    mech_ru = mech_ru or MECH_RU
    out: list[dict] = []
    if rec.verdict == 'all_need_check' and assessments:
        by: dict[str, list[int]] = {}
        for i, a in enumerate(assessments):
            for m in a.mechanisms:
                for r in m.needs_check_reasons:
                    by.setdefault(_short_reason(r), []).append(i + 1)
        for text, nums in by.items():
            # `head` — условие без перечня записей: «окно 1: геомагнитная буря Kp ≥ 7 в окне
            # (2 сигнала по 6 записям)». Перечень стоит после первого «: » и всегда доступен
            # целиком: в карточке окна и в свёртке блока вердикта.
            head_ = '%s: %s' % (_wins_ru(nums), text.partition(': ')[0])
            if head_.count('(') != head_.count(')'):     # скобка попала бы под обрез — не режем
                head_ = ''
            out.append({'text': '%s: %s' % (_wins_ru(nums), text), 'kind': 'cond', 'mech': None, 'head': head_})
    else:
        seen = set()
        per = dict(getattr(rec, 'per_mechanism_comparison', None) or {})
        per_txt = {v: k for k, v in per.items()}
        for r in rec.reasons:
            if r in seen:
                continue
            seen.add(r)
            txt = _short_reason(r) if ' DONKI — ' in r else r
            mech = per_txt.get(r)
            if mech:
                m = re.match(r'^(\d\d:\d\dZ): (.*)$', txt)
                if m:
                    n = _hhmm_to_window(assessments, m.group(1))
                    txt = '%s — %s: %s' % (mech_ru.get(mech, mech), ('окно %d (%s)' % (n, m.group(1))) if n else m.group(1), m.group(2))
                else:
                    txt = '%s: %s' % (mech_ru.get(mech, mech), txt)
            out.append({'text': txt, 'kind': 'cmp' if mech else 'other', 'mech': mech, 'head': ''})
    return out


def verdict_reasons(rec, assessments, mech_ru: dict | None = None, max_items: int = 6,
                    max_other: int | None = None) -> tuple[list[str], int]:
    """Список условий панели вердикта с указанием окна и без дубликатов (О3-3).
    Возвращает (строки, сколько не показано).

    `max_other` — сколько строк НЕ о сравнении механизмов оставить (бриф §9.1 и §9.7). Сообщения о
    неполном покрытии слой расчёта даёт и общей строкой, и отдельно по каждому окну; в блоке «Почему?»
    это три формулировки об одном и том же, а полностью они стоят в карточках окон и в таблице 1.
    Строки сравнения (обе величины обоих окон) не режутся никогда — ради них блок и существует.
    """
    items = verdict_items(rec, assessments, mech_ru)
    out = [x['text'] for x in items]
    is_cmp = [x['kind'] != 'other' for x in items]
    if max_other is not None:
        kept, others = [], 0
        for txt, cmp_ in zip(out, is_cmp):
            if cmp_ or others < max_other:
                kept.append(txt)
                others += 0 if cmp_ else 1
        dropped = len(out) - len(kept)
        if len(kept) > max_items:
            return kept[:max_items], dropped + len(kept) - max_items
        return kept, dropped
    if len(out) > max_items:
        return out[:max_items], len(out) - max_items
    return out, 0


_MMOD_ROLE_RU = 'сезонная оценка с проверкой чувствительности к допущениям'
# Точка с запятой ВНЕ скобок: «(правило команды, не норма; настройка …)» — одно целое, резать по
# внутренней точке с запятой нельзя. Раньше резали по первой любой, и на экране оставалась
# незакрытая скобка без точки, а вместе с ней пропадало происхождение порога 5 % (R4-1, R4-13).
_SEMI_OUTSIDE_PARENS_RE = re.compile(r';\s*(?![^()]*\))')


def bullet_short_ru(text: str) -> str:
    """Короткая форма причины для оперативного уровня (бриф экрана §9.1: две-три величины).

    Сокращается только пояснение про линию метеороидов: полностью оно повторяется во вкладке
    «Методика», формула (6), и в карточке окна. Сравнение по космопогоде не трогается — в нём
    стоят обе величины обоих окон, ради которых блок «Почему?» и существует.

    Происхождение порога различимости остаётся в короткой форме: имя файла настроек переводится
    словами (PHRASE_RU), а не отрезается вместе с закрывающей скобкой."""
    s = str(text or '')
    if 'линия метеороидов' not in s and 'линии метеороидов' not in s and 'сезонная оценка' not in s:
        return s
    head_ = _SEMI_OUTSIDE_PARENS_RE.split(s)[0].strip().rstrip('.')
    for a, b in PHRASE_RU:
        head_ = head_.replace(a, b)
    if 'сезонная оценка' in s:
        # The comparison and its threshold stay visible. Assumptions are already
        # disclosed in the full verdict and in the seasonal component panel.
        return head_ + '.'
    return head_ + ' — роль линии: абсолютная оценка и охват, не выбор окна.'


def robustness_pill(rec, rob: dict) -> str:
    """О7: «устойчив» печатается только когда выбор есть (DEMO-17).

    Когда автоматического выбора нет ни в одной ячейке сетки, причина называется словами вердикта:
    «без автовыбора» читалось как «окна заблокированы условиями» и там, где условий нет вовсе,
    а окна просто равнозначны (найдено третьим кругом в режиме «Сейчас»)."""
    grid = rob.get('preferred_by_grid') or {}
    vals = set(grid.values())
    if rec.preferred is None and grid and vals == {None}:
        return pill(NO_PICK_ON_GRID_RU.get(rec.verdict, 'на всей сетке порогов предпочтительного окна нет'), 'none')
    if rob.get('stable'):
        return pill('выбор устойчив на сетке порогов' if rec.preferred is not None else 'ранжирование устойчиво на сетке порогов', 'ok')
    return pill('выбор меняется на сетке порогов' if rec.preferred is not None else 'ранжирование меняется на сетке порогов', 'warn')


def _win_no_by_iso(S: dict) -> dict:
    """Номер окна по времени начала в виде ISO — как окна записаны в снимке."""
    return {w['start_utc']: str(w['index']) for w in (S.get('windows') or [])}


def _cells_ru(n: int) -> str:
    return plural_ru(n, ('ячейки', 'ячеек', 'ячеек'))


def robustness_line_ru(rec, S: dict, thr_nT, e_min_MeV) -> str:
    """Одна понятная фраза об устойчивости выбора — с причиной и с рабочими порогами (R4-18).

    До неё на одном экране стояли три формулировки об одном: плашка «выбор меняется на сетке порогов»
    под вердиктом «Есть предпочтительное окно» и подпись таблицы 1 про «порядок окон при нулевом
    допуске». Формально каждая верна, но вместе они читаются как взаимное опровержение, а слова
    «ранжирование» и «порядок» на оперативном уровне не разведены и за три минуты не разбираются.
    Здесь печатается ровно одно предложение; ранжирование остаётся на вкладке «Устойчивость и нормы».

    Ничего не досчитывается: и рабочие пороги, и исход каждой ячейки берутся из снимка.
    """
    rob = S.get('robustness') or {}
    grid = rob.get('preferred_by_grid') or {}
    nums = _win_no_by_iso(S)
    where = 'рабочих порогах (%s нТл, от %s МэВ)' % (nbsp_thousands(thr_nT), fmt(float(e_min_MeV)))
    n = len(grid)
    pref_iso = rec.preferred.start_utc.isoformat() if rec.preferred is not None else None
    if pref_iso is not None:
        head_ = 'Окно %s выбрано на %s' % (nums.get(pref_iso, '—'), where)
        if not n:
            return head_ + '; сетка порогов не считалась, устойчивость выбора не проверена.'
        same = sum(1 for v in grid.values() if v == pref_iso)
        if same == n:
            return head_ + ' и остаётся предпочтительным во всех %d %s сетки порогов — выбор устойчив.' \
                % (n, plural_ru(n, ('ячейке', 'ячейках', 'ячейках')))
        none_cells = sum(1 for v in grid.values() if not v)
        other = n - same - none_cells
        parts = []
        if other:
            parts.append('в %d из %d %s сетки правило называет другое окно' % (other, n, _cells_ru(n)))
        if none_cells:
            parts.append('в %d из %d %s сетки правило лучшее окно не называет — разница внутри допуска'
                         % (none_cells, n, _cells_ru(n)))
        # Насколько слабо держится предпочтение — числом, из того же снимка: разность минут на
        # РАБОЧЕМ пороге против итогового допуска. Без неё фраза «неустойчив» не говорит, что
        # рекомендация стоит на одной-двух минутах сверх собственного допуска.
        margin = _preference_margin_ru(rob, thr_nT)
        return head_ + '; ' + ', '.join(parts) + ', поэтому выбор считается неустойчивым.' \
            + ((' ' + margin) if margin else '')
    if not n:
        return 'Предпочтительного окна нет; сетка порогов не считалась, устойчивость ответа не проверена.'
    named = [v for v in grid.values() if v]
    reason = NO_PICK_REASON_RU.get(rec.verdict, 'правило лучшее окно не называет')
    if not named:
        return ('Предпочтительного окна нет ни на %s, ни в одной из %d %s сетки порогов: %s — ответ устойчив.'
                % (where, n, _cells_ru(n), reason))
    wins = ' и '.join(sorted({nums.get(v, '—') for v in named}))
    return ('На %s предпочтительного окна нет (%s), но в %d из %d %s сетки правило называет окно %s — '
            'ответ неустойчив к настройке порога.' % (where, reason, len(named), n, _cells_ru(n), wins))


def _preference_margin_ru(rob: dict, thr_nT) -> str:
    """«Предпочтение держится на 21 мин против допуска 20 мин» — числа из снимка устойчивости."""
    diff = rob.get('diff_by_thr') or {}
    tol = rob.get('tol_min')
    if not diff or tol is None or thr_nT is None:
        return ''
    cur = next((float(v) for k, v in diff.items() if abs(float(k) - float(thr_nT)) < 0.5), None)
    if cur is None:
        return ''
    return ('Предпочтение держится на %s мин при допуске %s мин — слабое предпочтение, не преимущество.'
            % (fmt(round(abs(cur))), fmt(round(float(tol)))))


def tolerance_origin_ru(S: dict, pro: bool = False, e_min_MeV=None) -> str:
    """Откуда взялся допуск равнозначности — одной строкой под вердиктом (R4-19).

    Допуск по минутам не задан числом: это размах РАЗНОСТИ минут между двумя лучшими окнами по оси
    порога аномалии (формула (9)). Без этой строки на главном экране стояло «окна равнозначны —
    разница внутри допуска 48 мин» при разнице 41 мин, и откуда взялись 48 мин, сказано не было.
    Все числа — из `S['robustness']`, ничего не зашито.
    """
    rob = S.get('robustness') or {}
    diff = rob.get('diff_by_thr') or {}
    if not diff:
        return ''

    keys = sorted(diff, key=lambda k: float(k))
    thr_s = ' / '.join(nbsp_thousands(float(k)) for k in keys)
    val_s = ' / '.join(_minus(fmt(round(float(diff[k])))) for k in keys)
    span = max(float(v) for v in diff.values()) - min(float(v) for v in diff.values())
    tol = float(rob.get('tol_min') or 0.0)
    s = ('Откуда допуск %s мин: при порогах аномалии %s нТл разность минут между двумя лучшими окнами равна '
         '%s мин, её размах по сетке — %s мин.' % (fmt(round(tol)), thr_s, val_s, fmt(round(span))))
    s += (' Настолько разность двигает одна настройка порога, поэтому меньшую разницу мы не считаем '
          'преимуществом окна.') if round(tol) <= round(span) else \
         (' Допуск не опускается ниже %s мин — это нижняя граница настройки.' % fmt(round(tol)))
    # Допуск по флюенсу решает вердикт («Окна равнозначны») ровно так же, как допуск по минутам,
    # и на оперативном уровне он печатался числом без происхождения. Это одно предложение, оно
    # лежит в свёрнутом блоке и главный экран не удлиняет, поэтому стоит на обоих уровнях (О3).
    ratio = rob.get('ratio_by_e') or {}
    if ratio:
        ek = sorted(ratio, key=lambda k: float(k))
        rs = max(float(v) for v in ratio.values()) / min(float(v) for v in ratio.values())
        s += (' Допуск по флюенсу ×%s: разброс отношения флюенсов по каналам %s МэВ — ×%s%s.'
              % (ratio_ru(rob.get('tol_ratio')), ' / '.join(fmt(float(k)) for k in ek), ratio_ru(rs),
                 '' if float(rob.get('tol_ratio') or 0) <= rs else ', ниже границы настройки'))
        # Фактическое отношение флюенсов сравниваемой пары на РАБОЧЕМ канале — это та же ячейка
        # ratio_by_e, а не отдельная величина: без неё на экране стояли два разных числа, слово
        # «равнозначны» и ни одного обоснования.
        cur = next((float(v) for k, v in ratio.items() if e_min_MeV is not None and float(k) == float(e_min_MeV)), None)
        if cur is not None:
            inside = cur <= float(rob.get('tol_ratio') or 0)
            s += (' Отношение флюенсов сравниваемых окон на рабочем канале от %s МэВ — ×%s: %s допуска, '
                  'поэтому %s.'
                  % (fmt(float(e_min_MeV)), ratio_ru(cur), 'внутри' if inside else 'за пределами',
                     'преимуществом окна мы его не считаем' if inside else 'различие по флюенсу засчитывается'))
    return s


def _minus(s: str) -> str:
    """Знак минуса у отрицательного числа — типографский, а не дефис."""
    return s.replace('-', '−')


def ratio_ru(v) -> str:
    """Допуск по флюенсу — всегда двумя знаками («×1,50»), как он записан в основании допуска."""
    return ('%.2f' % float(v or 0)).replace('.', ',')


# R4-17 закрыт в слое сравнения окон, а не на экране. Прежде здесь стояла дописка
# qualify_tolerance_ru: она подставляла допуск в готовый хвост «не хуже по флюенсу и минутам».
# После слияния пятого круга vkd/windows/compare.py собирает эту фразу из вычисленного и сам
# печатает и величины, и допуск, и отношение («не хуже по флюенсу в пределах допуска ×1,50
# (1,74·10⁶ против 1,65·10⁶ част./см², отношение ×1,05)»), а прежнего хвоста не выдаёт ни в одной
# ветке (tests/test_models_round4.py). Дописка стала недостижимой и снята: экран не переписывает
# правило и не подставляет в него числа.


def rule_operational_ru(rec, S: dict) -> tuple[str, str]:
    """Строка правила для ОПЕРАТИВНОГО уровня: (что видно сразу, что уходит в свёртку).

    На шагах 3–4 и 4 вычисленное правило — это и есть ответ «почему»: в нём стоят обе величины
    обоих окон, допуск и отношение. Резать его нельзя: без допуска фраза «не хуже по флюенсу»
    опровергается числами той же строки (R4-17), поэтому она остаётся на виду целиком.

    На остальных шагах вычисленный хвост повторяет заголовок вердикта («Все окна требуют проверки
    аналитиком» ↔ «у каждого окна есть условие проверки, автоматический выбор не делается») или
    строки сравнения («окна 1 и 2 равнозначны» ↔ пункт с минутами и флюенсом обоих окон), а бриф
    §9.7 запрещает два сообщения об одном и том же. Видно остаётся имя шага; на шаге 5 к нему
    добавляются числа допуска — прямо из снимка, а не выкусыванием из готовой фразы.
    """
    full = frac_ru(rule_ru(rec.rule_applied, basis=False))
    ra = str(rec.rule_applied or '')
    if ra.startswith('п.3–4') or ra.startswith('п.4'):
        return full, ''
    step = full.partition(':')[0].strip()
    if ra.startswith('п.5'):
        rob = S.get('robustness') or {}
        tol, ratio = rob.get('tol_min'), rob.get('tol_ratio')
        if tol is not None and ratio is not None:
            step += ': %s мин по минутам в аномалии и ×%s по флюенсу' % (fmt(round(float(tol))), ratio_ru(ratio))
    return step, (full if full != step else '')


# В текущем режиме источник уведомлений о событиях (NASA DONKI) не опрашивается вовсе: живого
# загрузчика нет. Пропуск данных не равен нулевому риску, и это ограничение охвата обязано стоять
# на экране, а не выводиться зрителем из того, что счётчик событий показывает ноль.
# Глагол один на весь экран: слой расчёта кладёт в пропуски охвата «в текущем режиме не
# опрашиваются», и держать здесь «не запрашиваются» значит называть одно и то же двумя словами
# в двух местах одного экрана (техзадание, раздел 4).
LIVE_NO_EVENTS_RU = ('уведомления NASA DONKI (протонное событие, буря, приход выброса) — в текущем режиме '
                     'не опрашиваются, доступны в исторических режимах')
LIVE_DONKI_REGISTRY_ROW = {
    'источник': 'NASA DONKI: уведомления о событиях',
    'величина': 'уведомления и карточки событий (протонные события, бури, выбросы)',
    'единица': 'текст уведомления, Kp — безразмерный, энергии — МэВ',
    'частота': 'по мере событий',
    'публикация': 'время выпуска уведомления, объявленное в самом сообщении',
    'лицензия': 'в ответе службы не указана; условия — на сайте NASA DONKI',
    'ограничение': 'в текущем режиме не опрашивается: живого загрузчика нет. Условия ставятся по наблюдению '
                   'GOES и прогнозу Kp NOAA; в исторических режимах канал работает из архива',
}


# Два канала NOAA с почти одинаковыми подписями читались как один показатель, выданный дважды:
# «вероятность S1 и выше за сутки, прогноз NOAA» и «вероятность протонного события за сутки,
# прогноз NOAA». Это один и тот же показатель из РАЗНЫХ выпусков одной службы, и подпись обязана
# называть выпуск (Т1: перепечатка не является независимым подтверждением).
_FORECAST_LABEL_BY_CHANNEL_RU = {
    's1_prob_daily': 'вероятность S1 и выше за сутки (трёхсуточный бюллетень NOAA)',
    'proton_prob_daily': 'вероятность протонного события за сутки (суточный выпуск NOAA)',
}


def forecast_label_ru(line: dict) -> str:
    """Подпись строки внешнего прогноза с указанием выпуска, а не только величины."""
    return _FORECAST_LABEL_BY_CHANNEL_RU.get(line.get('channel'), line.get('label') or '—')


def coverage_scope_ru(S: dict, mode: str | None = None) -> str:
    """Охват расчёта одной строкой: что учтено и что нет (О1). Стоит во вкладке «Окна и факторы»
    на обоих уровнях и в панели вердикта — на профессиональном.

    В текущем режиме к «не учтено» первым пунктом добавляется канал уведомлений: слой расчёта
    его не опрашивает и потому в список не кладёт, а строка охвата в live побуквенно совпадала
    со строкой исторического режима и про DONKI не говорила ничего.
    """
    missing = list(S.get('coverage_missing', []) or [])
    if mode == 'live' and not any('DONKI' in x for x in missing):
        missing.insert(0, LIVE_NO_EVENTS_RU)
    return 'Охват: %s. Не учтено: %s.' % (', '.join(S.get('coverage_declared', []) or ['—']),
                                          ', '.join(missing or ['—']))


def preset_matches(preset: dict, mode_ru: str, hist_date, hist_hour, duration_min: int,
                   search_min: int, offsets_min) -> bool:
    """Совпадает ли текущий запрос с пресетом по КАЖДОМУ полю (О5, О4, Т8).

    Подпись под кнопками пресетов утверждала параметры пресета до конца сессии, даже когда
    пользователь всё перекрутил руками: на одной панели стояли «Буря Гэннон, 10.05.2024 12:00»
    и строкой ниже «Отсечка публикации: 25.06.2024 12:00», а вердикт был третий. Здесь запрос
    сверяется с пресетом целиком; расходится хоть одно поле — подпись говорит об этом.
    """
    if preset.get('mode') != mode_ru:
        return False
    if int(preset.get('duration_min', -1)) != int(duration_min):
        return False
    if int(preset.get('search_min', -1)) != int(search_min):
        return False
    if [int(x) for x in (preset.get('offsets_min') or [])] != [int(x) for x in (offsets_min or [])]:
        return False
    d = preset.get('date')
    if d is not None:
        if hist_date is None or (hist_date.year, hist_date.month, hist_date.day) != tuple(d):
            return False
        if hist_hour is None or int(hist_hour) != int(preset.get('hour')):
            return False
    return True


PRESET_CHANGED_RU = ('Параметры изменены вручную: расчёт идёт по текущим настройкам ниже. '
                     'Нажмите пресет ещё раз, чтобы вернуть его значения.')


def plan_state(rec, assessments, S: dict, duration_min: int, offsets, mode: str = '', t0=None) -> dict:
    """Снимок плана и ИСХОДА для плашки пересчёта (О3: «Система пересчитывает последствия»).

    Постановка требует не только пересчитать, но и показать, ЧТО изменилось. Здесь собирается
    ровно то, что стоит на экране: длительность, сдвиги, вердикт, предпочтительное окно, минуты
    и флюенс первого окна, допуск равнозначности. Ничего не досчитывается."""
    first = (assessments or [None])[0]
    f = {x.name: x.value for m in (first.mechanisms if first else ()) for x in m.factors}
    rob = S.get('robustness') or {}
    return {'duration_min': int(duration_min),
            'offsets': [int(x) for x in (offsets or [])],
            # Момент и режим запроса: плашка пересчёта имеет смысл ТОЛЬКО когда изменён план при
            # том же запросе. Смена режима или даты (в том числе нажатием пресета) — это другой
            # запрос, и сравнивать его исход с прежним нельзя: получилось бы «было окно 19.09 15:20,
            # стало окно 25.06 20:00», то есть сопоставление двух разных суток как пересчёт плана.
            'mode': str(mode or ''),
            't0': t0.isoformat() if t0 is not None else None,
            'verdict': rec.verdict,
            'pref': rec.preferred.start_utc.isoformat() if rec.preferred is not None else None,
            'pref_ru': (dt_ru(rec.preferred.start_utc) if rec.preferred is not None else None),
            'saa_min': f.get('минут в аномалии'),
            'fluence': next((v for n, v in f.items() if n.startswith('флюенс')), None),
            'tol_min': rob.get('tol_min')}


def plan_change_ru(prev: dict, cur: dict) -> str:
    """Одна плашка: что изменено в плане и что из этого вышло в пересчёте (О3, О5).

    Плашка печаталась только при изменении длительности и говорила лишь о плане, ни слова об
    исходе; при сдвиге окна её не было вовсе. Между тем на тихой дате 25.06.2024 сокращение
    длительности 360 → 120 мин переворачивает рекомендацию с окна 2 на окно 1 — и молчать об
    этом нельзя, иначе сокращение выхода читается как улучшение обстановки.
    """
    if not prev or not cur:
        return ''
    plan = []
    if prev.get('duration_min') != cur.get('duration_min'):
        plan.append('длительность ВКД %d → %d мин' % (prev['duration_min'], cur['duration_min']))
    if prev.get('offsets') != cur.get('offsets'):
        plan.append('сдвиги окон %s → %s мин'
                    % (', '.join(str(x) for x in prev.get('offsets') or []),
                       ', '.join(str(x) for x in cur.get('offsets') or [])))
    if not plan:
        return ''
    out = []
    n = lambda a, b, unit='': '%s → %s%s' % (fmt(a), fmt(b), (' ' + unit) if unit else '')
    if prev.get('saa_min') is not None and cur.get('saa_min') is not None and prev['saa_min'] != cur['saa_min']:
        out.append('минуты в аномалии окна 1 — ' + n(prev['saa_min'], cur['saa_min'], 'мин'))
    if prev.get('fluence') is not None and cur.get('fluence') is not None and prev['fluence'] != cur['fluence']:
        out.append('флюенс окна 1 — %s → %s част./см²'
                   % (fmt_fluence(prev['fluence']), fmt_fluence(cur['fluence'])))
    if prev.get('tol_min') is not None and cur.get('tol_min') is not None \
            and round(float(prev['tol_min'])) != round(float(cur['tol_min'])):
        out.append('допуск равнозначности — %s → %s мин'
                   % (fmt(round(float(prev['tol_min']))), fmt(round(float(cur['tol_min'])))))
    if prev.get('pref') != cur.get('pref') or prev.get('verdict') != cur.get('verdict'):
        was = ('окно %s' % prev['pref_ru']) if prev.get('pref_ru') else \
            '«%s»' % VERDICT_TITLE.get(prev.get('verdict'), prev.get('verdict') or '—').lower()
        now = ('окно %s' % cur['pref_ru']) if cur.get('pref_ru') else \
            '«%s»' % VERDICT_TITLE.get(cur.get('verdict'), cur.get('verdict') or '—').lower()
        out.append('ответ изменился: было %s, стало %s' % (was, now))
    tail = ('Пересчёт: ' + '; '.join(out) + '.') if out else 'Пересчёт: ни одна из показанных величин не изменилась.'
    return ('Изменение плана: %s. Условия обстановки те же — это изменение плана, а не улучшение обстановки. %s'
            % ('; '.join(plan), tail))


def sentence_ru(text: str) -> str:
    """Закончить строку точкой, если её нет. Строки свёртки вердикта идут подряд, и строка
    «…не покрывает окно (до его начала 6 ч)» без точки склеивалась со следующей — «Формальная
    запись — вкладка «Методика»…» — в одно предложение (находка шестого круга, п. 4)."""
    t = (text or '').rstrip()
    return t if (not t or t.endswith(('.', '!', '?', ':', ';', '…'))) else t + '.'


_FORMAL_RU = 'формальная запись — вкладка «Методика», формулы (8) и (9)'
_VMORE_SUMMARY = 'Как это посчитано: правило целиком, область вывода подробно и остальные пояснения'
# Заголовок второй свёртки блока вердикта. Происхождение допуска — это три абзаца с числами сетки
# порогов; внутри общей свёртки они тонули среди прочих строк, а на поверхности занимали столько же
# места, сколько сам вердикт (замечание владельца по снимкам 20 и 21).
_TOL_SUMMARY = 'Откуда допуск: числа сетки порогов и отношение флюенсов'
# Сколько знаков перечней записей блок вердикта держит на поверхности. Перечень — это «откуда
# известно»: у каждого условия он называет каждое уведомление со своим временем публикации и своим
# Kp, и резать его по одной записи нельзя (число и ссылка обязаны приходить из одной записи).
# Пока все перечни вместе укладываются в бюджет, они стоят на виду целиком; как только не
# укладываются — на виду остаются НАЗВАНИЯ всех условий, а перечни печатает карточка окна, которая
# стоит прямо под блоком вердикта и показывает их целиком, со своим временем публикации у каждой
# записи. В свёртку перечень не дублируется: он и так на экране. Правило одно на все условия сразу:
# если часть условий показывать с записями, а часть без, жюри прочтёт в этом разницу, которой нет.
_COND_BUDGET = 420
_COND_POINTER = 'Записи, по которым поставлены условия, — в карточке каждого окна ниже.'

# Хвост строки сравнения, в котором слой сравнения НАЗЫВАЕТ лучшее окно. При вердиктах
# «оснований недостаточно» и «все окна требуют проверки» сервис отказывается рекомендовать —
# и печатать под таким заголовком «— лучше окно 2» значит опровергать собственный заголовок
# первой же видимой строкой. Числа остаются: они и есть расчёт факторов.
_CMP_PICK_TAIL_RE = re.compile(r'\s+—\s+(?:лучше\s|равнозначны в допуске|по минутам лучше).*$', re.S)
_CMP_NO_PICK_RU = {
    'insufficient': 'числа приведены как расчёт факторов, а не как рекомендация: у обязательной линии '
                    'нет данных, поэтому окно сравнением не выбирается',
    'all_need_check': 'числа приведены как расчёт факторов, а не как рекомендация: условие проверки '
                      'стоит у каждого окна, и правило команды окно с условием не выбирает',
}


def comparison_without_pick_ru(text: str, verdict: str) -> str:
    """Строка сравнения без вывода о лучшем окне — для вердиктов отказа (О3)."""
    s = str(text or '')
    note = _CMP_NO_PICK_RU.get(verdict)
    if not note:
        return s
    cut = _CMP_PICK_TAIL_RE.sub('', s).rstrip(' ;,')
    return cut + ' — ' + note + '.'


def structural_gap(S: dict, mode: str, missing) -> bool:
    """Пробел покрытия структурный: его не закроет ни один следующий выпуск источника.

    Так устроен канал протонных событий в текущем режиме: наблюдение GOES не может покрыть окно,
    начинающееся через несколько часов, потому что прогноза потока с разрешением по окну не
    существует ни у одного источника (`docs/design/RESHENIE_TEKUSCHIY_REZHIM.md`). Если же
    источник выключен человеком или отказал, пробел обычный: его закрывает возврат источника.
    """
    if mode != 'live':
        return False
    if any((S.get('request') or {}).get('disabled', {}).values()):
        return False
    return any(('GOES' in str(m) or 'прогноз' in str(m)) for m in (missing or ()))


def next_step_ru(rec, S: dict, assessments=None, mode: str = 'live') -> str:
    """Одна строка «что делать дальше» под вердиктом — сквозное замечание аналитика.

    Сервис заканчивался вердиктом и объяснением и нигде не говорил, ЧТО ДЕЛАТЬ: что проверить,
    до какого момента действует условие, когда пересчитать. Всё это в снимке есть — срок действия
    условия в `Condition.interval_utc`, время выпуска прогноза в `S['forecasts']`. Формулировки —
    без командного наклонения: строка говорит о сроках и о том, что сервис сделает при пересчёте.
    """
    v = rec.verdict
    # до какого момента действует самое позднее условие любого окна
    ends = [c.interval_utc[1] for a in (assessments or []) for m in a.mechanisms
            for c in (getattr(m, 'conditions', None) or ())
            if len(tuple(c.interval_utc or ())) == 2 and c.interval_utc[1] is not None]
    span = ('условие действует до %s UTC — начало позже этого момента из-под него выходит, '
            'это проверяется сдвигом окна; ' % dt_ru(max(ends))) if ends else ''
    # когда ждать следующий выпуск внешнего прогноза
    pubs = [x.get('published_utc') for x in (S.get('forecasts') or []) if x.get('published_utc')]
    when = ''
    if pubs:
        try:
            last = max(datetime.fromisoformat(p) for p in pubs)
            when = 'пересчитать после следующего выпуска NOAA (последний — %s UTC, выпуск раз в сутки)' % dt_ru(last)
        except (TypeError, ValueError):
            when = ''
    if not when:
        when = 'пересчитать после следующего выпуска источников' if mode == 'live' \
            else 'пересчёт на другой отсечке покажет, что знал прогноз раньше'
    if v == 'insufficient':
        # Смежная находка решения владельца 19.09: обещать новый выпуск источника там, где пробел
        # СТРУКТУРНЫЙ, нельзя. В текущем режиме канал наблюдения GOES физически не может покрыть
        # окно, начинающееся через несколько часов: прогноза потока протонов с разрешением по окну
        # не существует ни у одного источника, и сколько ни ждать следующего выпуска NOAA, покрытие
        # не появится. Строка называет причину и предлагает посильное. Когда источник выключен
        # человеком или отказал, пробел не структурный — там прежний текст верен.
        if structural_gap(S, mode, rec.missing):
            return ('Что дальше: %sбез обязательной линии рекомендации не будет, сравнение окон её не заменяет. '
                    'Следующий выпуск источника этот пробел не закроет: прогноза потока протонов с разрешением '
                    'по окну не существует ни у одного источника. Посильное — начать ближе к моменту последнего '
                    'наблюдения либо перейти в исторический разбор, где линия покрыта и виден полный разбор.'
                    % span)
        return ('Что дальше: %sбез обязательной линии рекомендации не будет, сравнение окон её не заменяет; '
                'пробел закрывает возврат источника в работу (боковая панель, «Источники») или новый выпуск — %s.'
                % (span, when))
    if v == 'all_need_check':
        return ('Что дальше: %sрешение за аналитиком — правило команды окно с условием не выбирает; '
                'до решения нужен фактический Kp у смены, %s.' % (span, when))
    if v == 'preferred':
        n = _win_num(_win_no_by_iso(S), rec.preferred.start_utc.isoformat()) if rec.preferred is not None else ''
        rob = S.get('robustness') or {}
        # О неустойчивости выбора сказано строкой устойчивости выше — второго сообщения об одном
        # и том же здесь быть не должно (бриф §9.7); меняется только оговорка к действию.
        #
        # Двенадцатый круг: намерение было записано, но не выполнено — слово «неустойчивость»
        # стояло и здесь, и в строке устойчивости, и в самом заголовке блока, то есть один и тот
        # же вывод повторялся на поверхности ТРИЖДЫ (воспроизводится на пресете «Тихая дата»).
        # Строка «Что дальше» говорит о ДЕЙСТВИИ и отсылает к уже прочитанной оговорке, а не
        # пересказывает её.
        act = 'можно выносить на согласование' if rob.get('stable') \
            else 'выносится на согласование только вместе с оговоркой выше'
        return ('Что дальше: %sокно %s %s; отчёт — кнопкой «Скачать отчёт»; %s.'
                % (span, n or '—', act, when))
    if v in ('equivalent', 'trade_off'):
        return ('Что дальше: %sвыбор между окнами сервис не делает — различие внутри допуска либо механизмы '
                'указывают на разные окна; решение принимается по причинам вне охвата (план, смена, ресурс), %s.'
                % (span, when))
    return ''


def verdict_panel(rec, S: dict, windows_ru: dict, assessments=None, pro: bool = False,
                  plan_change: str | None = None, missing_ru=None, policy_short: str | None = None,
                  thr_nT=None, e_min_MeV=None, mode: str = 'live') -> str:
    """Блок «Когда выходить? / Почему?» — первое, что читает жюри (бриф §9.1).

    На ОПЕРАТИВНОМ уровне сразу видно только то, что отвечает на два вопроса: вердикт с окном,
    величины сравнения (или условия проверки) и чего не хватает. Всё остальное — правило целиком,
    роль линии метеороидов, остальные пояснения о покрытии, происхождение допуска и устойчивость
    выбора — стоит на один клик глубже, в свёртке внутри того же блока. Ничего не выброшено:
    каждая строка либо видна, либо лежит в свёртке, и обе проверяются `tests/test_ui_app.py`.

    На ПРОФЕССИОНАЛЬНОМ уровне блок до двенадцатого круга оставался сплошным — «там читают
    целиком». На снимках 20 и 21 стало видно, чем это кончается: 4 091 знак подряд, из них
    330 — дословный повтор правила, семь буллетов и три абзаца о происхождении допуска.
    Владелец: «выглядит как вырви глаз». Теперь свёртка одна и та же на обоих уровнях, только
    на профессиональном на поверхности остаются три буллета (сравнение по космопогоде, сравнение
    по метеороидам, покрытие), а не четыре коротких. Происхождение допуска — своей свёрткой
    «Откуда допуск», чтобы его не искали среди прочих строк.
    """
    v = rec.verdict
    rob = S.get('robustness') or {}
    # О3: заголовок не должен быть увереннее расчёта. Когда предпочтительное окно есть, но на сетке
    # порогов правило его не подтверждает, «Есть предпочтительное окно» опровергается строкой
    # устойчивости, которая до шестого круга лежала под свёрткой. Заголовок смягчается, а сама
    # фраза о неустойчивости выносится на поверхность на ОБОИХ уровнях (ниже).
    unstable = v == 'preferred' and rob.get('stable') is False
    title = 'Предпочтительное окно есть, но выбор неустойчив' if unstable else VERDICT_TITLE.get(v, v)
    lines = ['<div class="verdict v-%s">' % v, '<h2>%s</h2>' % esc(title)]
    more: list[str] = []                   # то, что уезжает в свёртку — на обоих уровнях (пункт 2)

    if pro:
        # Двенадцатый круг, пункт 2. Здесь стоял ДОСЛОВНЫЙ ПОВТОР: `rule_ru` заменяет только
        # приставку «п.3–4:» на «шаги 3–4 из 5, сравнение и сведение:», а хвост оставляет как есть,
        # и он же печатался вторым разом в скобке `(п.3–4: …)`. На пресете «Тихая дата» это
        # 330 знаков слово в слово подряд — не оформление, а ошибка. В скобке остаётся только
        # НОМЕР пункта договора, ради которого она и заводилась; если правило неизвестное и
        # `rule_ru` вернуло вход без изменений, скобки нет вовсе — она была бы вторым повтором.
        _full_rule = frac_ru(rule_ru(rec.rule_applied, basis=True))
        _point = str(rec.rule_applied or '').partition(':')[0].strip()
        _orig = (' <span class="orig">(%s)</span>' % esc(frac_ru(_point))) \
            if (_point and _full_rule != frac_ru(rec.rule_applied)) else ''
        lines.append('<div class="rule">Правило: %s%s</div>' % (esc(_full_rule), _orig))
        # Формальная запись — в свёртку: на поверхности блока нужны вердикт, окно, «почему»
        # с числами и условия, а ссылка на номера формул читается один раз и на клик глубже.
        more.append(_FORMAL_RU[0].upper() + _FORMAL_RU[1:] + '.')
    else:
        shown_rule, hidden_rule = rule_operational_ru(rec, S)
        lines.append('<div class="rule">Правило: %s</div>' % esc(screen_text(shown_rule)))
        if hidden_rule:
            more.append('Правило целиком: ' + screen_text(hidden_rule) + '.')
        more.append(_FORMAL_RU[0].upper() + _FORMAL_RU[1:] + '.')
    if rec.preferred is not None:
        lines.append('<div class="win">Окно %s — %s</div>' % (_win_num(windows_ru, rec.preferred.start_utc), esc(win_span(rec.preferred))))
    # Объявленная область вывода — сразу под вердиктом и НА ОБОИХ уровнях, не в свёртке.
    # Решение владельца 19.09 по разбору Codex п. 1: сервис называет предпочтительное окно и при
    # частичном покрытии, но ровно настолько, насколько посчитал. Без этой строки заголовок
    # «Есть предпочтительное окно» читается как заключение о риске ВКД, которым он не является.
    R = S.get('recommendation') or {}
    scope = R.get('scope') or getattr(rec, 'scope_ru', '')
    scope_detail = R.get('scope_detail') or getattr(rec, 'scope_detail_ru', '')
    if scope:
        lines.append('<div class="scope"><b>Область вывода:</b> %s</div>' % esc(sentence_ru(screen_text(scope))))
    # Подробная форма — та же область, с именами факторов и пропусками по причинам. Она уезжает
    # в свёртку на ОБОИХ уровнях: короткая форма уже на экране строкой выше, а два развёрнутых
    # текста об одном и том же — два сообщения об одном (бриф §9.7). До двенадцатого круга на
    # профессиональном уровне она стояла сразу: на снимке 20 это 470 знаков вплотную под
    # 140-знаковой строкой о том же самом. Ничего не потеряно — свёртка в этом же блоке.
    if scope_detail and scope_detail != scope:
        more.append('Область вывода подробно: ' + screen_text(scope_detail) + '.')

    items = verdict_items(rec, assessments)
    # О3: при отказе рекомендовать строка сравнения не имеет права называть лучшее окно, а причина
    # отказа обязана стоять ПЕРВОЙ. Раньше под заголовком «Оснований для рекомендации недостаточно»
    # первой видимой строкой шло «космопогода: … — лучше окно 2», то есть ровно та рекомендация,
    # в которой заголовок только что отказал (проверено прогоном live с GOES «исключён»).
    if v in _CMP_NO_PICK_RU:
        items = [dict(it, text=comparison_without_pick_ru(it['text'], v)) if it['kind'] == 'cmp' else it
                 for it in items]
    missing = list(missing_ru) if missing_ru is not None else list(rec.missing)
    # `reasons` уже содержат тексты `missing` (слой сравнения кладёт их в оба поля), и без этой
    # проверки каждая недостающая линия печаталась ДВАЖДЫ: один раз как причина, второй — с
    # приставкой «Чего не хватает». На живом примере это давало 15 пунктов вместо 8.
    shown_texts = {it['text'] for it in items}
    need_items = [{'text': 'Чего не хватает: ' + x, 'kind': 'need', 'mech': None, 'head': ''}
                  for x in missing if x not in shown_texts]
    # При отказе «оснований недостаточно» причина отказа обязана стоять ПЕРВОЙ строкой: иначе
    # первым видимым пунктом идёт сравнение окон, которое читается как рекомендация.
    items = (need_items + items) if v == 'insufficient' else (items + need_items)
    cut_cond = False
    if pro:
        # Двенадцатый круг, пункт 2. Было: «профессиональный уровень читают целиком, ничего не
        # режем» — и на снимках 20 и 21 под вердиктом стояли семь буллетов подряд, 2 100 знаков.
        # Владелец о них: «выглядит как вырви глаз». Правило владельца: на поверхности три
        # строки — сравнение по космопогоде, сравнение по метеороидам и покрытие; условия окон
        # идут впереди сравнения, потому что от них зависит сам вердикт. Остальное — в свёртке
        # этого же блока, целиком и без сокращений: аналитик открывает её одним щелчком.
        # При отказе «оснований недостаточно» причина отказа важнее сравнения и в тройку входит
        # раньше него (О3, находка №6): под заголовком, отказавшим рекомендовать, первой видимой
        # строкой не должно стоять сравнение окон — оно читается как рекомендация.
        _ORDER = {'cond': 0, 'need': 1, 'cmp': 2, 'other': 3} if v == 'insufficient' \
            else {'cond': 0, 'cmp': 1, 'need': 2, 'other': 3}
        _rank = sorted(range(len(items)), key=lambda i: (_ORDER.get(items[i]['kind'], 4), i))
        _keep = set(_rank[:3])
        shown = [it for i, it in enumerate(items) if i in _keep]
        hidden = [it for i, it in enumerate(items) if i not in _keep]
    else:
        # перечни записей остаются на поверхности, только если умещаются в бюджет — все сразу
        conds = [it for it in items if it['kind'] == 'cond']
        cut_cond = bool(conds) and all(it['head'] for it in conds) \
            and sum(len(it['text']) for it in conds) > _COND_BUDGET
        shown, hidden, others = [], [], 0
        rule_txt = (rule_operational_ru(rec, S)[0] or '').lower()
        for it in items:
            it = dict(it, text=bullet_short_ru(it['text']))
            # Сезонную линию не скрываем: дата и направленная геометрия меняют сравнение.
            # механизм, уже названный в видимой строке правила, — второе сообщение об одном и том же
            dup = (it['kind'] == 'cmp' and it['mech'] != 'mmod_stat'
                   and MECH_RU.get(it['mech'], '???')[:-1].lower() in rule_txt)
            if it['kind'] == 'other':
                others += 1
                # Доля окна с моделью и пропуски по причинам уже стоят в строке «Область вывода»
                # прямо над этим списком. Оставлять здесь ещё и строку о покрытии — это два
                # сообщения об одном и том же (бриф §9.7), и на «Тихой дате» она занимала 210
                # символов из видимой части. Без объявленной области одна такая строка остаётся.
                keep = others <= (0 if scope else 1)
            else:
                keep = not dup
            if keep and len(shown) >= 4:   # больше четырёх строк за три минуты не читаются
                keep = False
            if keep and cut_cond and it['kind'] == 'cond':
                shown.append(dict(it, text=it['head']))   # перечень записей — в карточке окна ниже
                continue
            (shown if keep else hidden).append(it)
    if not pro and v == 'insufficient':
        hidden = items  # retain all evidence; do not headline a winner from partial integrals
        mechanisms = sorted({MECH_RU.get(m.mechanism_id, m.mechanism_id)
                             for a in (assessments or []) for m in a.mechanisms
                             if m.mandatory and m.coverage.value != 'full'})
        shown = [{'text': 'Неполный охват: ' + ', '.join(mechanisms or ['обязательные линии']) + '.', 'kind': 'need'},
                 {'text': 'Показаны только известные вклады; их недостаточно для выбора времени ВКД.', 'kind': 'need'}]
        if any(it['kind'] == 'cond' for it in items):
            shown.append({'text': 'Есть условия дополнительной проверки специалистом. Подробности — в объяснениях окон.', 'kind': 'cond'})
    if shown:
        lines.append('<ul>' + ''.join('<li%s>%s</li>' % (' class="evid"' if it['kind'] == 'cond' else '',
                                                         esc(screen_text(it['text']))) for it in shown) + '</ul>')
    if cut_cond:
        lines.append('<div class="policy">%s</div>' % esc(_COND_POINTER))
    if plan_change:
        lines.append('<div class="plan">%s</div>' % esc(plan_change))

    # Плашка сценария «что если» остаётся НА ВИДУ на обоих уровнях: она говорит, что числа блока
    # получены на подставленных условиях, а не на данных источников. В свёртку её прятать нельзя.
    if S.get('is_simulated'):
        lines.append('<div class="cov">%s</div>' % pill('сценарий «что если»', 'warn'))
    if thr_nT is not None and e_min_MeV is not None:
        robust_html = '<div class="cov">%s</div>' % esc(screen_text(robustness_line_ru(rec, S, thr_nT, e_min_MeV)))
    else:                                  # порогов не передали — печатаем прежнюю плашку, не выдумывая
        robust_html = '<div class="cov">%s</div>' % robustness_pill(rec, rob)
    # Допуск равнозначности участвует только в сравнении окон. Там, где до сравнения не дошло
    # (у каждого окна условие; нет покрытия обязательной линии), строка о нём была бы лишней.
    tol = tolerance_origin_ru(S, pro, e_min_MeV) if v in ('preferred', 'equivalent', 'trade_off') else ''
    # Устойчивость выбора — НА ПОВЕРХНОСТИ на обоих уровнях. Рекомендация, которая держится
    # на одной минуте сверх собственного допуска, и фраза об этом, спрятанная под свёртку, —
    # это заголовок, увереннее расчёта (проверено на пресете «Тихая дата»: 21 мин при допуске 20).
    lines.append(robust_html)
    step = next_step_ru(rec, S, assessments, mode)
    # Двенадцатый круг, пункт 2: свёртка теперь одинакова на ОБОИХ уровнях. Прежде на
    # профессиональном её не было вовсе, и блок шёл сплошной стеной: правило, семь буллетов,
    # подробная область вывода, три абзаца о происхождении допуска, охват, «Что дальше».
    # Ни одна строка не выброшена — каждая либо видна, либо лежит на клик глубже в этом же блоке.
    body = ['<summary>%s</summary>' % esc(_VMORE_SUMMARY)]
    # Строки свёртки — отдельными <div class="vm">, а не пунктами списка: список внутри свёртки
    # смешивался с видимыми пунктами блока, и бюджет «не более пяти пунктов» переставал мерить
    # то, ради чего он стоит, — длину ВИДИМОЙ части. Точка в конце каждой строки остаётся
    # (находка шестого круга: строки свёртки идут подряд и склеивались в одно предложение).
    if hidden:
        body += ['<div class="vm">%s</div>' % esc(sentence_ru(screen_text(it['text']))) for it in hidden]
    body += ['<div class="vm">%s</div>' % esc(sentence_ru(screen_text(x))) for x in more]
    if policy_short:
        body.append('<div class="vm">%s</div>' % esc(policy_short))
    if pro:
        # Охват и перечень неучтённого — на клик глубже и здесь: главный экран не должен читаться
        # как список наших недочётов (пункт 1 техзадания). Полностью они остаются во вкладке
        # «Окна и факторы», в отчёте и в выгрузке.
        body.append('<div class="vm">%s</div>' % esc(coverage_scope_ru(S)))
    lines.append('<details class="vmore">' + ''.join(body) + '</details>')
    # Происхождение допуска — отдельной свёрткой «Откуда допуск»: это три абзаца с числами сетки,
    # и внутри общей свёртки они тонули. Заголовок называет ровно то, что внутри (пункт 2).
    if tol:
        lines.append('<details class="vmore"><summary>%s</summary><div class="vm">%s</div></details>'
                     % (esc(_TOL_SUMMARY), esc(screen_text(tol))))
    # Последняя видимая строка блока — что делать дальше (сквозное замечание аналитика):
    # что проверить, до какого момента действует условие, когда пересчитать.
    if step:
        lines.append('<div class="next">%s</div>' % esc(screen_text(step)))
    lines.append('</div>')
    return ''.join(lines)


def win_span(w) -> str:
    """«10.05 22:00 — 11.05 00:00 UTC, 120 мин»: дата конца печатается, если окно переходит через полночь."""
    end = w.start_utc + timedelta(minutes=w.duration_min)
    end_s = end.strftime('%d.%m %H:%M') if end.date() != w.start_utc.date() else end.strftime('%H:%M')
    return '%s — %s UTC, %d мин' % (w.start_utc.strftime('%d.%m %H:%M'), end_s, w.duration_min)


_win_span = win_span     # прежнее имя


_ISO_DT_RE = re.compile(r'(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::\d{2})?Z?')
_MD_DT_RE = re.compile(r'(?<![\d.\-])(\d{2})-(\d{2}) (\d{2}):(\d{2})Z')
# Голое время с признаком зоны: «окно 1 (06:23Z)», «наблюдение 06:15Z покрывает 14 % окна».
# Под две регулярные выражения выше оно не попадает — там обязательна дата, — и на оперативном
# уровне рядом с «19.09 06:23» оставался машинный вид «06:23Z» (пятый круг). Слово UTC стоит один
# раз в шапке экрана, поэтому здесь остаётся только «чч:мм». Запускается ПОСЛЕ дат: к этому моменту
# «2026-09-19 06:15Z» уже превращено в «19.09.2026 06:15» и под это правило не подпадает.
_BARE_TIME_RE = re.compile(r'(?<![\d\-:.])(\d{2}):(\d{2})\s*Z')
_AGE_CLAUSE_RE = re.compile(r'[,;]?\s*давность(?:\s+данных)?\s+\d+(?:[.,]\d+)?\s*(?:мин|ч|сут)\b'
                            r'(?:\s*\(предел[^)]*\))?')
# разряды тысяч в готовых строках модулей: «24000 нТл» → «24 000 нТл» (единица обязательна, год не трогаем)
# Единица может стоять не у каждого числа, а один раз в конце перечисления через дробную черту:
# «22000/24000/26000 нТл». Без учёта перечисления разряды получало только последнее число, и
# в одной фразе стояло «22000/24000/26 000» — три вида записи одного и того же (пятый круг).
_THOUSANDS_RE = re.compile(r'(?<![\d,.])(\d{4,})(?=(?:\s*/\s*\d+)*\s*(?:нТл|мин\b|км\b|сут\b|Зв\b|част\.|пфу\b|pfu\b))')


def dates_ru(text) -> str:
    """Дата источника в едином виде экрана (S7): «2026-09-19 01:35Z» → «19.09.2026 01:35»,
    «05-09 14:00Z» → «09.05 14:00», «06:23Z» → «06:23». Времена всюду UTC, слово UTC печатается
    один раз в шапке; буква Z на оперативном уровне — машинная запись и на экране не остаётся."""
    s = _ISO_DT_RE.sub(lambda m: '%s.%s.%s %s:%s' % (m.group(3), m.group(2), m.group(1), m.group(4), m.group(5)),
                       str(text or ''))
    s = _MD_DT_RE.sub(lambda m: '%s.%s %s:%s' % (m.group(2), m.group(1), m.group(3), m.group(4)), s)
    return _BARE_TIME_RE.sub(lambda m: '%s:%s' % (m.group(1), m.group(2)), s)


def screen_text(text) -> str:
    """Готовая строка модуля в виде экрана: дробь с запятой, степени надстрочно, даты «дд.мм чч:мм»,
    разряды тысяч узким неразрывным пробелом."""
    s = fluence_sci_ru(dates_ru(frac_ru(text)))
    return _THOUSANDS_RE.sub(lambda m: NBSP_THIN.join(_groups3(m.group(1))), s)


def _groups3(whole: str) -> list[str]:
    out = []
    while len(whole) > 3:
        out.insert(0, whole[-3:])
        whole = whole[:-3]
    out.insert(0, whole)
    return out


def drop_age(text) -> str:
    """Убрать оборот давности: она печатается только в приборной полосе (PROPOSAL_A п. 3)."""
    return _CLEAN_RE.sub(' ', _AGE_CLAUSE_RE.sub('', str(text or ''))).strip().rstrip(' ;,')


_OBS_SHARE_RE = re.compile(r'горизонт наблюдения[^;]*покрывает (\d+) % окна')


def obs_share_pct(f) -> int | None:
    """Какую долю окна покрывает горизонт наблюдения этого фактора — из записи слоя расчёта.
    None — фактор не наблюдение или доля в записи не указана."""
    m = _OBS_SHARE_RE.search(str(getattr(f, 'limits_note', '') or ''))
    return int(m.group(1)) if m else None


def factor_value_ru(f, unit: str = '') -> str:
    """Значение фактора для карточки окна и таблицы сравнения.

    Наблюдение, горизонт которого не покрывает окно ни на одну минуту, характеристикой этого
    окна не является: печатается прочерк, а само измерение со своим временем остаётся в полосе
    состояния и в карточке объяснения (О2). Иначе прошлое измерение выдавалось за величину
    будущего окна, и карточка противоречила вердикту на одном экране (найдено третьим кругом)."""
    if f is None:
        return '—'
    if obs_share_pct(f) == 0:
        return '—'
    if str(getattr(f, 'name', '') or '').startswith('флюенс'):
        return fmt_fluence(getattr(f, 'value', None), unit)     # один вид записи на весь столбец флюенса
    return fmt(getattr(f, 'value', None), unit)


def _cov_reason(f) -> str | None:
    """Причина неполного покрытия одного фактора — одной фразой для карточки окна (DEMO-11).
    Давность из неё убрана (она только в приборной полосе), даты приведены к виду экрана."""
    note = drop_age(dates_ru(f.limits_note or ''))
    name = f.name
    m = re.search(r'доля точек с моделью (\d+) %', note)
    if name.startswith('флюенс') and m:
        return 'флюенс: модель ОСТ есть для %s %% точек трассы' % m.group(1)
    if name.startswith('поток протонов GOES'):
        share = obs_share_pct(f)
        if share == 0:
            m0 = re.search(r'наблюдение\s+([\d.:\s]+)', note)
            return 'GOES: наблюдение%s не покрывает окно — значение окна не определено' \
                % ((' ' + m0.group(1).strip()) if m0 else '')
        if share is not None and share < 100:
            # Пятый круг: этой ветки не было, и строка «почему неполное» уходила в общий хвост —
            # печаталось «GOES: наблюдение 19.09.2026 06:15 (ниже S1 (фон))», то есть на вопрос
            # «почему покрытие неполное» карточка отвечала уровнем по шкале S. Причина — доля окна,
            # и блок вердикта на том же экране называл именно её. Теперь оба места говорят одно.
            m0 = re.search(r'наблюдение\s+([\d.:\s]+)', note)
            return 'GOES: наблюдение%s покрывает %d %% окна — на остальные участки прогноза потока нет' \
                % ((' ' + m0.group(1).strip()) if m0 else '', share)
        if 'наблюдений GOES нет' in note and 'DONKI о протонных' in note:
            return 'GOES: наблюдений за 2024 нет, канал по датированным уведомлениям DONKI'
        if 'наблюдений GOES нет' in note and 'каталог' in note:
            return 'GOES: наблюдений за 2024 нет; каталог DONKI покрывает период, событий не объявлено'
        if 'устарело' in note:
            return 'GOES: наблюдение устарело — для будущих участков окна'
        if 'нет данных' in note:
            return 'GOES: данных нет'
    if name == 'минут в аномалии':
        return 'трасса покрывает окно не полностью'
    m3 = re.search(r'трасса покрывает (\d+) % окна', note)
    if m3:
        return 'метеороиды: трасса покрывает %s %% окна' % m3.group(1)
    m4 = re.search(r'покрытие окна ячейками (\d+) %', note)
    # «ячейки покрывают 0 % окна» верно только тогда, когда выпуск есть, а его ячейки до окна не
    # дошли. Когда допустимого выпуска нет вовсе, слой расчёта прямо пишет это в ограничении, и
    # карточка обязана называть ту же причину, что таблица источников (R4-21).
    if m4 and 'выпуска с ячейками на это окно нет' in note:
        return '%s: выпуска с этим каналом на горизонт окна нет — канал не учитывается, объявлено' % name.split(',')[0]
    if m4 and name.startswith('прогноз'):
        return 'прогноз Kp: ячейки покрывают %s %% окна' % m4.group(1)
    if m4:
        return '%s: ячейки покрывают %s %% окна' % (name.split(',')[0], m4.group(1))
    first = note.split(';')[0].strip()
    return ('%s: %s' % (name.split(',')[0], first)) if first else None


def coverage_reasons(a) -> list[str]:
    """Причины неполного покрытия по обязательным механизмам окна, без повторов."""
    out, seen = [], set()
    for m in a.mechanisms:
        if not (m.mandatory or m.coverage.value != 'none') or m.coverage.value == 'full':
            continue
        for f in m.factors:
            if f.coverage.value == 'full':
                continue
            r = _cov_reason(f)
            if r and r not in seen:
                seen.add(r)
                out.append(r)
    return out


def fluence_label(flu) -> str:
    """Подпись канала из имени фактора: «флюенс протонов ≥30 МэВ, част./см²» (O5-10, DEMO-20)."""
    if flu is None:
        return 'флюенс протонов, част./см²'
    return flu.name.replace('захваченных ', '') + ', ' + (flu.unit or 'част./см²')


def window_card(i: int, a, best: bool, mode: str, saa_thr_nT: float | None = None) -> str:
    """Карточка окна: заголовок, состояние, ключевые величины, условия, покрытие по механизмам с причиной."""
    f = {x.name: x for m in a.mechanisms for x in m.factors}
    reasons = [r for m in a.mechanisms for r in m.needs_check_reasons]
    crit = any('приоритетное' in r for r in reasons)
    # Т6 и постановка: «Сервис различает отсутствие выявленного воздействия и недостаток данных
    # для оценки». Состояние карточки считается по ПОКРЫТИЮ обязательных механизмов раньше, чем
    # по условиям: при исключённом GOES условий взяться неоткуда, и зелёная плашка «без условий»
    # выдавала отсутствие данных за отсутствие воздействия (проверено прогоном live с GOES
    # «исключён»: pill-ok «без условий» рядом с «покрытие: космопогода: нет»).
    no_cov = any(m.mandatory and m.coverage.value == 'none' for m in a.mechanisms)
    if crit:
        state = ('приоритетная проверка', 'crit')
    elif reasons:
        state = ('условие проверки', 'warn')
    elif no_cov:
        state = ('условия не проверены: нет данных', 'none')
    else:
        state = ('без условий', 'ok')
    cls = 'wcard' + (' best' if best else '') + (' crit' if crit else (' flag' if reasons else ''))
    saa = f.get('минут в аномалии')
    flu = next((x for n, x in f.items() if n.startswith('флюенс')), None)
    mm = f.get('ожидаемое число попаданий, пластина 1 м²')
    goes = f.get('поток протонов GOES ≥10 МэВ')
    kpf = f.get('прогноз Kp NOAA, максимум в окне')
    # Единица — в подписи строки, в ячейке только число (PROPOSAL_A п. 2.2, правило 4).
    # Метки разведены по смыслу: в карточке стоит величина ОКНА, в приборной полосе — величина
    # «сейчас». Под одной меткой «GOES ≥10 МэВ, pfu» на одном экране стояли три разных ответа
    # (окно 1, окно 2, полоса), а причина расхождения была спрятана мелким «почему неполное».
    goes_v = factor_value_ru(goes)
    rows = [('минут в аномалии, мин', factor_value_ru(saa), True),
            (fluence_label(flu), factor_value_ru(flu), False)]
    if mode == 'live':
        rows.append(('GOES ≥10 МэВ на окно, pfu', goes_v if goes_v != '—' else 'нет данных на окно', False))
    # Прогноз Kp по окну печатается во ВСЕХ режимах: в оперативном режиме карточка окна не должна
    # показывать меньше, чем в разборе, — сравнивать окна там нечем.
    rows.append(('прогноз Kp NOAA, макс. в окне, безразмерный', factor_value_ru(kpf), False))
    # Полное число включает спорадическую и направленную сезонную составляющие (модель 0.10.0).
    # Прежний комментарий «линия одинакова у окон и окно не выбирает» СНЯТ: он был верен для
    # средней модели Grün, а сезонная линия датой и направлением радианта окна различает и
    # участвует в выборе. Единица подписи сохранена — требование владельца о размерностях.
    rows.append(('метеороиды, попаданий в пластину 1 м² за окно', mmod_scale_ru(mm, a.window.duration_min), False))
    kv = ''.join('<div class="k">%s</div><div class="v%s">%s</div>' % (esc(k), ' big' if big else '', esc(v)) for k, v, big in rows)
    conds = ''.join(condition_html(r, c) for r, c in _reasons_with_conditions(a)[:4])
    if len(reasons) > 4:
        conds += '<div class="small">… ещё %d</div>' % (len(reasons) - 4)
    if not reasons:
        # Т6: «условий нет» и «условия не проверены» — разные утверждения. Без покрытия
        # обязательной линии условию взяться неоткуда, и молчание условий ничего не значит.
        if no_cov:
            conds = ('<div class="cond none">Условия по обязательной линии не проверялись: данных нет. '
                     'Отсутствие условия здесь не означает отсутствия воздействия.</div>')
        else:
            conds = '<div class="cond none">Условий проверки нет по данным до отсечки</div>' if mode == 'history_forecast' \
                else '<div class="cond none">Условий проверки нет</div>'
    reconcile = kp_reconcile_ru(a, kpf)
    if reconcile:
        conds += '<div class="covwhy">%s</div>' % esc(screen_text(reconcile))
    cov = ' '.join(pill('%s: %s' % (MECH_RU.get(m.mechanism_id, m.mechanism_id), COV_RU[m.coverage.value]), COV_KIND[m.coverage.value])
                   for m in a.mechanisms if m.mandatory or m.coverage.value != 'none')
    why = coverage_reasons(a)
    why_html = ('<div class="covwhy">почему неполное — %s</div>' % esc(screen_text('; '.join(why)))) if why else ''
    # Служебная подпись о том, КАК считаны минуты в аномалии, одинакова у всех окон и печатается
    # один раз под парой карточек (saa_note_ru), а не в каждой карточке: место под карточкой нужно
    # ответу, а не двум одинаковым техническим строкам. Формулировка — из слияния восьмого круга.
    return ('<div class="%s"><div class="wh"><div><div class="wt">Окно %d%s</div><div class="wtime">%s</div></div>%s</div>'
            '<div class="kv">%s</div>%s<div class="cov">покрытие: %s</div>%s</div>'
            % (cls, i + 1, ' · предпочтительное' if best else '', esc(win_span(a.window)), pill(*state), kv,
               conds, cov, why_html))


def saa_note_ru(saa_thr_nT) -> str:
    """Одна строка под парой карточек: как считаны минуты в аномалии у ОБОИХ окон (О4)."""
    if saa_thr_nT is None:
        return ''
    return ('Минуты в аномалии у всех окон считаны одинаково: |B| < %s нТл, линейные пересечения порога '
            'между точками трассы — формула (3), вкладка «Методика».' % nbsp_thousands(saa_thr_nT))


def mmod_scale_ru(mm, duration_min: int | None) -> str:
    """Число попаданий за окно в человеческом масштабе: «5,64·10⁻⁷ — раз в ~1 200 лет».

    Число «5,64·10⁻⁷» не с чем сопоставить, и на карточке оно занимает место сразу под флюенсом,
    как равноправная величина выбора. Обратная величина в годах непрерывной работы даёт масштаб;
    она считается из длительности окна, а не зашивается."""
    v = getattr(mm, 'value', None)
    if mm is None or v is None:
        return '—'
    s = fmt(v)
    if 'C-2' in str(getattr(mm, 'rule_applied', '')):
        return s + ' (фон + потоки даты)'
    try:
        years = float(duration_min) / float(v) / (60.0 * 24.0 * 365.0)
    except (TypeError, ValueError, ZeroDivisionError):
        return s
    if years <= 0 or years != years or years == float('inf'):
        return s
    if years >= 1:
        scale = 'раз в ~%s лет непрерывной работы' % nbsp_thousands(float(round(years, -2) if years >= 100 else round(years)))
    else:
        scale = 'раз в ~%s сут непрерывной работы' % fmt(round(years * 365.0))
    return '%s (%s)' % (s, scale)


def _reasons_with_conditions(a) -> list[tuple]:
    """Пары (текст условия, структурное условие или None) по всем механизмам окна, в порядке показа."""
    out = []
    for m in a.mechanisms:
        by_text = {c.text: c for c in (getattr(m, 'conditions', None) or ())}
        for r in m.needs_check_reasons:
            out.append((r, by_text.get(r)))
    return out


def condition_html(r: str, cond=None) -> str:
    """Короткая форма условия для карточки: заголовок, сигналы источников и СРОК ДЕЙСТВИЯ.

    Прежде строка резалась по первой точке с запятой, и вместе с хвостом (до 371 знака) с экрана
    уходил срок действия условия — ровно то, что аналитику нужно, чтобы понять, выводит ли сдвиг
    окно из-под условия. Здесь короткая форма собирается из готовых полей `Condition`, а не
    выкусыванием из строки; когда структурного условия нет, остаётся прежнее поведение.
    """
    cls = ' crit' if 'приоритетное' in r else ''
    if cond is None:
        return '<div class="cond%s">%s</div>' % (cls, esc(screen_text(_short_reason(r))))
    title = str(r).partition(': ')[0]
    out = ['<div class="cond%s">%s</div>' % (cls, esc(screen_text(title)))]
    for s in (cond.sources_ru or ()):
        body = _DROP_SPAN_RE.sub('', str(s or '')).strip().rstrip(' ;,')
        for part in [p.strip() for p in body.split(' · ') if p.strip()]:
            out.append('<div class="cond%s">%s</div>' % (cls, esc(screen_text(part))))
    span = action_span_ru(cond)
    if span:
        out.append('<div class="cond%s">%s</div>' % (cls, esc(span)))
    return ''.join(out)


# Срок действия печатается отдельной строкой из Condition.interval_utc — в перечне сигналов
# он был бы вторым сообщением об одном и том же.
_DROP_SPAN_RE = re.compile(r'[;,]?\s*действие [^;]*', re.S)


def action_span_ru(cond) -> str:
    """«действует до 11.05 13:03 UTC (конец не объявлен — принято 24 ч)» из Condition.interval_utc."""
    iv = tuple(getattr(cond, 'interval_utc', ()) or ())
    if len(iv) != 2 or iv[1] is None:
        return ''
    assumed = 'конец не объявлен' in str(getattr(cond, 'text', '') or '')
    return 'действует до %s UTC%s' % (dt_ru(iv[1]), ' (конец не объявлен — принят срок по умолчанию)' if assumed else '')


_KP_UP_RE = re.compile(r'Kp до (\d+(?:[.,]\d+)?)')
_ISSUED_RE = re.compile(r'публикация (\d{2}-\d{2} \d{2}:\d{2}Z|\d{2}\.\d{2} \d{2}:\d{2})')


def kp_reconcile_ru(a, kpf) -> str:
    """Одна примиряющая фраза, когда на карточке два разных Kp (О2).

    На карточке стоят «прогноз Kp NOAA, макс. в окне 3,67» (спокойно) и тут же условие
    «геомагнитная буря Kp ≥ 7 … Kp до 9» по адресному уведомлению. Почему условие поставлено
    не по свежему официальному бюллетеню, не сказано нигде, и два числа никто не мирит.
    Числа и времена берутся из тех же записей, ничего не досчитывается."""
    fc = getattr(kpf, 'value', None)
    if fc is None:
        return ''
    cond = next((c for _r, c in _reasons_with_conditions(a) if c is not None and c.kind in ('GST', 'KP')), None)
    if cond is None:
        return ''
    src = ' '.join(cond.sources_ru) if cond.sources_ru else str(cond.text or '')
    ups = [float(x.replace(',', '.')) for x in _KP_UP_RE.findall(src)]
    if not ups or max(ups) <= float(fc):
        return ''
    pub = _ISSUED_RE.findall(src)
    pub_ru = (', последнее — публикация %s' % pub[-1]) if pub else ''
    return ('Два Kp на карточке — разные величины, а не расхождение расчёта: %s — максимум по 3-часовым ячейкам '
            'регулярного бюллетеня NOAA на это окно; %s — верхняя граница, объявленная адресным уведомлением '
            'о конкретном событии%s. Условие ставится по уведомлению, а не по регулярному бюллетеню; '
            'само расхождение и есть повод для проверки.'
            % (fmt(float(fc)), 'Kp до %s' % fmt(max(ups)), pub_ru))


def _short_reason(r: str) -> str:
    """Короткая форма условия для карточки: до двоеточия плюс первая часть после."""
    head_, _, tail = r.partition(': ')
    tail = tail.split(';')[0].strip()
    return head_ + (': ' + tail if tail else '')


def short_reason(r: str) -> str:
    return _short_reason(r)


def kind_pill(kind_value: str) -> str:
    t, k = KIND_PILL.get(kind_value, (kind_value, 'none'))
    return pill(t, k)


def limit_ru(s: str) -> str:
    """Ограничение модуля орбиты по-русски; неизвестное — как есть с пометкой."""
    if s in LIMIT_RU:
        return LIMIT_RU[s]
    if re.search(r'[А-Яа-я]', s):
        return s
    return s + ' (текст модуля орбиты)'


def event_kind_ru(k: str) -> str:
    return EVENT_KIND_RU.get((k or '').upper(), k)


def source_name_ru(sid: str, mode: str | None = None) -> str:
    """Имя источника для экрана и выгрузки. Для выпусков внешнего прогноза NOAA к имени
    добавляется происхождение выпуска по режиму: живой выпуск / до отсечки / из архива."""
    name = SOURCE_RU.get(sid, sid)
    if sid == 'noaa_forecast_kp_forecast' and mode in RELEASE_BY_MODE_RU:
        return '%s (%s)' % (name, RELEASE_BY_MODE_RU[mode])
    return name


# давность в строке слоя источников бывает дробной и с оборотом «данных»: «давность данных 1888,7 мин».
# Прежнее выражение ловило только целое без «данных», и на экране оставалось «кеш» без числа (R4-27).
_TLE_AGE_RE = re.compile(r'давность(?:\s+данных)?\s+(\d+)(?:[.,]\d+)?\s*мин')


def tle_origin(tle_fetch_status: str | None) -> str:
    """«получено живьём с celestrak.org» / «кеш, давность 31 ч» / «снимок репозитория, давность 31 ч».
    Давность печатается в одних единицах с приборной полосой (age_ru), а не в минутах и часах вперемешку."""
    s = tle_fetch_status or ''
    m = re.search(r'живьём с ([\w.\-]+)', s)
    if m:
        return 'живьём с ' + m.group(1)
    m2 = _TLE_AGE_RE.search(s)
    age = (', ' + age_ru(int(m2.group(1)))) if m2 else ''
    if 'снимок репозитория' in s:
        return 'снимок репозитория' + age
    if 'кеш' in s:
        return 'кеш' + age
    if 'воспроизведение' in s:
        return 'из сохранённого расчёта'
    # Незнакомая формулировка слоя источников не уходит на экран как есть: в ней стоят перебор
    # адресов с параметрами запроса, английские имена отказов и вторая давность в минутах с точкой
    # вместо запятой («получено по сети, давность данных 2117.4 мин; проверка адресов:
    # https://…?CATNR=25544&FORMAT=TLE: timeout» — находка владельца прямо с экрана).
    # Остаётся начало фразы без хвоста; полный текст виден на профессиональном уровне и в выгрузке.
    head_ = drop_age(_PROBE_TAIL_RE.sub('', s)).rstrip(' ;,')
    if _PROBE_FAIL_RE.search(s):
        head_ = (head_ + '; ' if head_ else '') + 'основной адрес не ответил, взят резервный'
    return head_ or '—'


def verification_ru(summary: str, had_conditions: bool) -> str:
    """Сводка проверки после отсечки: начало зависит от того, ставилось ли условие (PLAN 3.3, п. 1).

    Слой расчёта печатает «условие поставлено в 12:00Z; факт: …» безусловно, и на тихой дате
    эта фраза противоречит карточкам обоих окон («условий проверки нет»). Здесь она заменяется
    на честную: условий не ставилось. Само сопоставление с фактом не меняется."""
    s = screen_text(str(summary or ''))
    if had_conditions:
        return s
    head_, sep, tail = s.partition('; факт: ')
    if not sep or not head_.startswith('условие поставлено'):
        return s
    return 'условий проверки на отсечку не ставилось; факт: ' + tail


# ---------------------------------------------------------------- вкладка «Методика» (PROPOSAL_A п. 5)
# Внутри st.latex кириллицы нет: KaTeX подставляет шрифты без кириллических глифов и на части
# браузеров рисует пустые прямоугольники. Все пояснения — обычным текстом под формулой.
METHOD_BLOCKS = [
    {'no': 1, 'group': 'Захваченные протоны',
     'title': 'Флюенс за окно',
     'latex': r'\Phi(\ge E_{min}) \;=\; \sum_{i\in K} \frac{J_i+J_{i+1}}{2}\,\Delta t_i, \qquad '
              r'J_i \;=\; \int_{E_{min}}^{E_{max}} f\!\left(L_i,\; B_i/B_{0,i},\; E\right)\,dE',
     'symbols': 'Φ — флюенс за окно, част./см²; J — интегральный всенаправленный поток выше E_min, см⁻²·с⁻¹; '
                'Δt_i — фактический интервал между точками в секундах; K — интервалы с известным потоком на обоих концах. Границы окна учитываются интерполяцией. L и B/B_0 — магнитные координаты.',
     'source': 'ОСТ 134-1044-2007, прил. А, табл. А.2.1 (минимум солнечной активности). Единицы — из вводного текста '
               'приложения: спектры всенаправленного потока, см⁻²·с⁻¹·МэВ⁻¹, без домножения на 4π.',
     'limits': 'Хвост выше E_max = 300 МэВ отброшен и объявлен. Вне сетки L = 1,14…9 модели нет — это «нет модели», '
               'а не нуль; выше точки отражения поток физически нулевой. Разрывы свыше 60 с не заполняются, за край траектории расчёт не продолжается. При неполном охвате показан только известный вклад, не полный флюенс.'},
    {'no': 2, 'group': 'Захваченные протоны',
     'title': 'Интегрирование по энергии между узлами таблицы',
     'latex': r'f(E) = a\,E^{\,b}, \qquad b = \frac{\ln\left(f_{k+1}/f_k\right)}{\ln\left(E_{k+1}/E_k\right)}',
     'symbols': 'f — дифференциальный поток в узле таблицы; E_k и E_{k+1} — соседние узлы энергетической сетки; '
                'между узлами принят степенной закон, интеграл берётся аналитически.',
     'source': 'ОСТ 134-1044-2007, прил. А: сетка энергий и значения потоков; способ интерполяции — степенной, '
               'как принято для спектров захваченных частиц.',
     'limits': 'Интерполяция только внутри сетки; экстраполяция за крайний узел не делается.'},
    {'no': 3, 'group': 'Магнитные координаты',
     'title': 'L-оболочка и B/B₀ эксцентричного диполя',
     'latex': r"L = \frac{r'}{\cos^{2}\lambda'}, \qquad r' = \frac{\left|\mathbf{r} - \mathbf{d}\right|}{R_E}, "
              r"\qquad \frac{B}{B_0} = \frac{\left|\mathbf{B}\right|_{IGRF}}{B_{eq}\,L^{-3}}",
     'symbols': "r′ — расстояние точки от смещённого центра диполя в радиусах Земли; λ′ — геомагнитная широта от "
                "смещённой оси; d — смещение центра диполя (603 км, 0,095 R_E на май 2024; вектор смещения печатается в выгрузке расчёта, файл факторов); |B| — полное поле IGRF в точке трассы; "
                "минуты в аномалии считаются по линейным пересечениям порога |B| между точками, исходный шаг трассы 1 мин.",
     'source': 'Fraser-Smith A. C. Centered and eccentric geomagnetic dipoles and their poles. Rev. Geophys. 25(1), 1987; '
               'коэффициенты IGRF — те же файлы, что у модуля орбиты.',
     'limits': 'Объявленное приближение до трассировки силовых линий: L и B_0 — от смещённого диполя, |B| — полное IGRF; '
               'отношение B/B_0 < 1 помечается, а не обрезается. Центральный диполь для таблиц ОСТ не годится: в ядре '
               'аномалии он даёт L ≈ 1,07 — ниже первой строки сетки (1,14), и флюенс обращается в нуль на всей трассе '
               '(проверено 19.09.2026). Признак аномалии — порог |B| из настроек, не официальная граница области.'},
    {'no': 4, 'group': 'Магнитные координаты',
     'title': 'Жёсткость геомагнитного обрезания',
     'latex': r'R(T) = \frac{\sqrt{T^{2} + 2\,T\,m_p c^{2}}}{Z\,e}, \qquad R_c(\mathbf{r}) < R(T)',
     'symbols': 'T — кинетическая энергия протона, МэВ; m_p c² = 938,272 МэВ; R — жёсткость, ГВ; '
                'R_c — вертикальная жёсткость обрезания в точке трассы; частица канала доступна там, где R_c ниже R.',
     # Слияние одиннадцатого круга: жёсткость обрезания перешла с формулы на таблицу Ж.1
     # ОСТ 134-1044-2007. Прежняя подпись называла диполь, а карточка величины на том же экране —
     # таблицу: два утверждения об одном и том же противоречили друг другу. Текст замены —
     # от интегратора, дословно.
     'source': 'Определение жёсткости — общепринятое; m_p c² — CODATA 2018; R_c — расчёт модуля орбиты '
               'по таблице Ж.1 ОСТ 134-1044-2007 (запасной путь — центральный наклонный диполь), '
               'метод подписан отдельно от формулы (3).',
     'limits': 'Обрезание спокойных условий: при буре Kp ≥ 7 оно снижается, и это не моделируется. Поток GOES на станцию '
               'не переносится: GOES меряет на геостационарной орбите, где обрезания нет.'},
    {'no': 5, 'group': 'Метеороиды',
     'title': 'Поток природных метеороидов по Grün',
     'latex': r'F_{met,0}(m) = 3{,}15576\cdot 10^{7}\,\left(F_1 + F_2 + F_3\right)',
     'symbols': 'm — масса частицы, г; нижняя масса модели m ≥ 10⁻³ г; F_met,0 — поток на 1 м² в год в свободном '
                'пространстве; F_1, F_2, F_3 — три слагаемых распределения Grün.',
     'source': 'ECSS-E-ST-10-04C Rev.1 (15.06.2020), формула (10-1), п. 10.2.2.2a.',
     'limits': 'Средняя модель служит опорой: из неё вычитается годовой средний вклад 49 потоков C-2. '
               'Потоки даты рассчитываются отдельно, без двойного учёта среднего вклада.'},
    {'no': 6, 'group': 'Метеороиды',
     'title': 'Поправки на орбиту и ожидаемое число попаданий',
     'latex': r'N = A\int_{t_0}^{t_1} [(F_0-\langle F_s\rangle)\bar{G}s_f K + \sum_i F_i(t)]\,dt',
     'symbols': 'Ḡ — гравитационное усиление, s_f — экранирование Землёй, K — множитель средней скорости; '
                'A = 1 м² — односторонняя случайно кувыркающаяся пластина; N — ожидаемое число попаданий за окно, '
                'интеграл берётся по трассе, концы окна включены. F_i — поток даты: профиль активности, '
                'радиант, относительная скорость, тень Земли и проекция на случайную пластину (/4).',
     'source': 'ECSS-E-ST-10-04C Rev.1: поправки (C-25) Annex C.1.5, множители Table J-6, число попаданий (10-2) п. 10.2.5a.',
     'limits': 'Неопределённость среднего фона ×0,33…3 (п. J.2.3.2); это не точность сезонного прогноза. '
               'Нормировка каталога и эпоха радиантов проверены как альтернативные гипотезы; '
               'при изменении сравнительного вывода однозначный выбор не выдаётся. Техногенный мусор не включён.'},
    {'no': 7, 'group': 'Метеороиды',
     'title': 'Вероятность хотя бы одного попадания',
     'latex': r'P_{\ge 1} = 1 - e^{-N}',
     'symbols': 'N — ожидаемое число попаданий из формулы (6); распределение принято пуассоновским.',
     'source': 'ECSS-E-ST-10-04C Rev.1, формула (10-3).',
     'limits': 'Это попадания в опорную пластину 1 м², а не в космонавта и не пробой скафандра: '
               'валидированного уравнения пробоя многослойной ткани у нас нет, и вероятность разгерметизации не считается.'},
    {'no': 8, 'group': 'Правило выбора окна',
     'title': 'Отношения «не хуже» и «лучше»',
     'latex': r'A \preceq B \iff m_A \le m_B + \delta_m \;\wedge\; \Phi_A \le \rho\,\Phi_B, \qquad '
              r'A \prec B \iff \left(A \preceq B\right) \wedge \left( m_A < m_B - \delta_m \;\vee\; \rho\,\Phi_A < \Phi_B \right)',
     'symbols': 'm — минуты в аномалии, Φ — флюенс за окно; «не хуже» требует обеих величин, «лучше» — строгого выигрыша '
                'хотя бы по одной. Если минуты и флюенс указывают на разные окна, вердикт — компромисс без победителя.',
     'source': 'договор команды, раздел 4 — правило рекомендации; пять шагов правила печатаются на экране в строке «Правило».',
     'limits': 'Это правило команды, а не эксплуатационная норма: шкалы NOAA сами по себе ВКД не запрещают. '
               'Окна с условием проверки из автоматического выбора исключаются — политика прототипа.'},
    {'no': 9, 'group': 'Правило выбора окна',
     'title': 'Допуск равнозначности',
     'latex': r'\delta_m = \max\!\left(\delta_{min},\; \operatorname{span}_{thr}\left[m_2 - m_1\right]\right), \qquad '
              r'\rho = \max\!\left(1{,}5,\; \operatorname{span}_{E}\left[\Phi_2/\Phi_1\right]\right)',
     'symbols': 'δ_m — допуск по минутам: разброс РАЗНОСТИ минут двух лучших окон по оси порога аномалии; '
                'ρ — допуск по флюенсу: разброс отношения флюенсов по оси канала E_min, не меньше 1,5.',
     'source': 'договор команды, раздел 4; сетка порогов и разбросы печатаются во вкладке «Устойчивость и нормы».',
     'limits': 'Допуском не может быть разброс абсолютных минут одного окна: порог сдвигает оба окна синфазно, '
               'и такой допуск объявлял бы равнозначными 54 и 86 мин.'},
    {'no': 10, 'group': 'Нормы как контекст', 'pro': True,
     'title': 'Предельно допустимая доза за полёт',
     'latex': r'G_{lim}(T) = 0{,}05 + 4\left(1 - e^{-T/72}\right), \qquad g_{h} = \frac{G_{lim}(T)}{720\,T}',
     'symbols': 'T — длительность экспедиции, мес; G_lim — предельно допустимая доза за полёт, Зв; '
                'g_h — контрольная часовая доза, Зв/ч; месяц принят равным 720 ч.',
     'source': 'ГОСТ 25645.215-85, пп. 2.2 и 2.3; формула сверена с таблицей стандарта, 8 строк из 8.',
     'limits': 'Сервис не вычисляет дозу человека. Это справочный контекст рядом с показателями среды, а не вердикт: '
               'для дозы нужны модели защиты станции, скафандра и ткани, которых в обязательной части нет.'},
]
# Пороги условий: на каждый — источник. Печатаются таблицей под формулами (8) и (9).
# Колонка «состояние» обязательна (О1): строка о сближениях SOCRATES описывает запланированный,
# но НЕ подключённый механизм — данных SOCRATES нет ни в одном режиме, покрытие conjunctions
# во всех прогонах 'none', и строка охвата двумя блоками ниже честно пишет «Не учтено: сближения
# SOCRATES — нет данных». Без колонки таблица и строка охвата на одном экране утверждали
# противоположное, и несделанное было подано как сделанное.
RULE_THRESHOLDS = [
    {'условие': 'протонное событие, наблюдение или уведомление', 'порог': '≥ 1000 pfu (S3)',
     'источник': 'NOAA SWPC, Space Weather Scales', 'класс': 'приоритетная проверка',
     'состояние': 'применяется'},
    {'условие': 'протонное событие', 'порог': '≥ 10 pfu (S1)',
     'источник': 'NOAA SWPC, Space Weather Scales', 'класс': 'условие проверки',
     'состояние': 'применяется'},
    {'условие': 'геомагнитная буря', 'порог': 'Kp ≥ 7 (G3)',
     'источник': 'NOAA SWPC, Space Weather Scales', 'класс': 'условие проверки',
     'состояние': 'применяется'},
    {'условие': 'сближение с расчётным моментом внутри окна', 'порог': 'качественно, без порога',
     'источник': 'CelesTrak SOCRATES', 'класс': 'ручная оценка',
     'состояние': 'не применяется: источник не подключён'},
]
RULE_THRESHOLDS_NOTE = ('Строки со состоянием «не применяется» описывают запланированный, но не подключённый '
                        'механизм: на вердикт они не влияют и в охвате расчёта объявлены как не учтённые.')
RULE_POLICY = ('Это правило команды, а не эксплуатационная норма. Шкалы NOAA сами по себе не запрещают ВКД; '
               'исключение помеченного окна из автоматического выбора — политика прототипа, её устойчивость '
               'проверена на сетке порогов.')
# Ключевые слова карточки → номер формулы. Карточка ссылается на формальную запись по номеру (PROPOSAL_A п. 5).
#
# Порядок строк — это порядок поиска, и он решает спор ключей. Карточки «минут доступности протонов
# ≥10 МэВ по обрезанию» и «≥100 МэВ» содержат и слово «минут» (формула (3), L-оболочка и B/B₀), и
# слово «обрезания» (формула (4), жёсткость обрезания). Пока строка с «минут» стояла первой, обе
# карточки отсылали жюри к формуле (3) — к магнитным координатам вместо жёсткости, — а на формулу (4)
# не ссылалась ни одна карточка (пятый круг). Более узкий ключ обязан стоять раньше более широкого,
# поэтому «обрезание» и «жёсткость» — первой строкой.
_FORMULA_KEYS = [(('обрезан', 'жёсткост', 'жесткост'), '(4)'),
                 (('флюенс', 'захвач', 'ост 134'), '(1) и (2)'),
                 (('аномали', 'минут', '|b|', 'l-оболоч'), '(3)'),
                 (('метеороид', 'попадан', 'grün', 'grun'), '(5)–(7)'),
                 (('сравнен', 'предпочт', 'равнознач', 'правил', 'допуск'), '(8) и (9)')]


# Вкладка «Устойчивость и нормы» существует ТОЛЬКО на профессиональном уровне, а «Методика» видна
# на обоих. Формула (9) на оперативном уровне отсылала пользователя к вкладке, которой у него на
# экране нет, — ссылка в никуда в академическом слое (потеря по О4). Источник формулы (9) зависит
# от уровня; остальные блоки не трогаются.
_METHOD_SOURCE_BY_LEVEL = {
    9: ('договор команды, раздел 4; сетка порогов и разбросы печатаются под вердиктом, в строке '
        '«Как это посчитано»; полная сетка из девяти ячеек — на профессиональном уровне, вкладка '
        '«Устойчивость и нормы»'),
}


def method_source_ru(block: dict, pro: bool = False) -> str:
    """Источник формулы для вкладки «Методика» с поправкой на уровень интерфейса."""
    if pro:
        return block['source']
    return _METHOD_SOURCE_BY_LEVEL.get(block.get('no'), block['source'])


def formula_ref(*texts) -> str | None:
    """Номер формулы вкладки «Методика» по тексту карточки: «формула (3)». Совпадения нет — None."""
    s = ' '.join(str(t or '') for t in texts).lower()
    for keys, no in _FORMULA_KEYS:
        if any(k in s for k in keys):
            return no
    return None


# ---------------------------------------------------------------- реестр источников (вкладка «Данные»)
# Единица, частота выпуска, что считается временем публикации, лицензия и ограничение — по одной записи
# на источник. Лицензия печатается только там, где она записана в самом ответе службы или в документе;
# где не записана — так и сказано, а не додумано.
SOURCE_REGISTRY = {
    'noaa_swpc_goes': {'величина': 'интегральный поток протонов ≥10 МэВ', 'единица': 'pfu = част./(см²·с·ср)',
                       'частота': 'лента 1 мин, выпуск примерно раз в 5 мин',
                       'публикация': 'время измерения, взятое из самой записи наблюдения',
                       'лицензия': 'в ответе службы не указана; условия — на сайте NOAA SWPC',
                       'ограничение': 'измерение на геостационарной орбите; на станцию не переносится без геомагнитного обрезания'},
    'gfz_kp': {'величина': 'планетарный индекс Kp', 'единица': 'безразмерный',
               'частота': '3-часовые интервалы',
               'публикация': 'конец 3-часового интервала; окончательный ряд выходит позже предварительного',
               'лицензия': 'CC BY 4.0 — из поля meta.license ответа службы',
               'ограничение': 'планетарный индекс, а не локальная величина на трассе'},
    'orbit': {'величина': 'положение МКС на трассе', 'единица': 'широта и долгота — град, высота — км',
              'частота': 'TLE — по мере выпуска (несколько раз в сутки), OEM — по мере выпуска NASA/JSC',
              'публикация': 'TLE — эпоха элементов; OEM — дата создания файла (публичная доступность в 2024 не доказана)',
              'лицензия': 'в ответе службы не указана; условия — на сайтах CelesTrak и NASA',
              'ограничение': 'манёвры не предсказываются; возраст элементов — инженерное ограничение, не оценка ошибки положения'},
    'donki_archive': {'величина': 'уведомления и карточки событий (протонные события, бури, выбросы)',
                      'единица': 'текст уведомления, Kp — безразмерный, энергии — МэВ',
                      'частота': 'по мере событий',
                      'публикация': 'время выпуска уведомления, объявленное в самом сообщении',
                      'лицензия': 'в ответе службы не указана; условия — на сайте NASA DONKI',
                      'ограничение': 'вложенные значения карточек без собственного времени публикации в строгий режим не идут'},
    'ost1044_belts': {'величина': 'спектры всенаправленного потока захваченных протонов',
                      'единица': 'см⁻²·с⁻¹·МэВ⁻¹', 'частота': 'таблица стандарта, не обновляется',
                      'публикация': 'ОСТ 134-1044-2007, дата издания стандарта',
                      'лицензия': 'отраслевой стандарт, печатный документ', 'ограничение': 'минимум солнечной активности; сетка L = 1,14…9, E ≤ 300 МэВ'},
    'ecss_grun': {'величина': 'поток природных метеороидов', 'единица': '1/(м²·год)',
                  'частота': 'модель стандарта, не обновляется',
                  'публикация': 'ECSS-E-ST-10-04C Rev.1, 15.06.2020',
                  'лицензия': 'стандарт ECSS, публикуемый документ',
                  'ограничение': 'средний фон ×0,33…3; сезонные потоки учтены с гипотезами нормировки и эпохи; '
                                 'всплески года и техногенный мусор не предсказываются'},
}
_REGISTRY_FORECAST = {'величина': 'внешний прогноз NOAA', 'единица': 'Kp — безразмерный, вероятности — %',
                      'частота': 'выпуск примерно раз в сутки',
                      'публикация': 'время выпуска бюллетеня (заголовок :Issued:)',
                      'лицензия': 'в ответе службы не указана; условия — на сайте NOAA SWPC',
                      'ограничение': 'суточные вероятности относятся к суткам, а не к окну ВКД, и не пересчитываются'}
SOURCE_REGISTRY['noaa_swpc_3day_forecast'] = dict(_REGISTRY_FORECAST)
# В исторических режимах поток GOES берётся не с живой ленты NOAA, а из численного архива
# NASA iSWA за 2024 (5-минутные средние). Частота, что считается публикацией и ограничение
# у него свои, поэтому реестр печатает их отдельной записью, а не подписью живой ленты.
_REGISTRY_GOES_ARCHIVE = {'величина': 'интегральный поток протонов ≥10 МэВ',
                          'единица': 'pfu = част./(см²·с·ср)',
                          'частота': '5-минутные средние за 2024 год',
                          'публикация': 'время измерения из записи архива; историческое время публикации именно '
                                        'этой версии не доказано — в строгий режим архив не идёт',
                          'лицензия': 'в ответе службы не указана; условия — на сайтах NASA iSWA и NOAA SWPC',
                          'ограничение': 'измерение на геостационарной орбите; на станцию не переносится '
                                         'без геомагнитного обрезания'}


def registry_row(sid: str, origin: str | None = None) -> dict:
    """Строка реестра источников по идентификатору; для выпусков NOAA — общая запись прогноза.
    origin — строка происхождения из снимка: по ней различаются живая лента GOES и архив iSWA."""
    if sid.startswith('noaa_forecast_'):
        return dict(_REGISTRY_FORECAST)
    if sid == 'noaa_swpc_goes' and 'iSWA' in (origin or ''):
        return dict(_REGISTRY_GOES_ARCHIVE)
    return dict(SOURCE_REGISTRY.get(sid, {'величина': '—', 'единица': '—', 'частота': '—', 'публикация': '—',
                                          'лицензия': '—', 'ограничение': '—'}))


# Причины исключения записи: группируемая формулировка (конкретные даты и номера убраны)
# и отнесение к отсечке или к содержанию записи. Русский текст причины приходит из app.compute
# (EXCLUDED_RU), здесь он только приводится к виду строки таблицы.
def excl_reason_ru(reason: str) -> str:
    """Причина исключения в виде, пригодном для группировки: без конкретных дат, смысл сохранён."""
    r = (reason or '').strip()
    low = r.lower()
    if 'момент наблюдения' in low:
        return 'момент наблюдения позже отсечки, хотя запись опубликована раньше'
    if 'неизвестно' in low:
        return 'времени публикации нет'
    if 'после отсечки' in low or 'позже отсечки' in low:
        return 'опубликовано после отсечки'
    if low.startswith('запись не разобрана'):
        return 'запись не разобрана'
    return r or '—'


def excl_group_ru(reason: str) -> str:
    """Отношение причины к отсечке: экран не должен называть отсечкой то, что ею не является."""
    low = (reason or '').lower()
    if low.startswith('запись не разобрана') or 'не разобран' in low:
        return 'запись не разобрана'
    if 'отсечк' in low or 'время публикации' in low or 'времени публикации' in low or 'доступност' in low:
        return 'время публикации или доступность'
    return 'содержание записи'


# Карта покрытия каналов адаптера истории (A2). Ключ — «источник:канал»; обе половины
# переводятся по точному совпадению, непереведённая печатается как есть — придумывать
# перевод причине, которой мы не знаем, нельзя.
_COV_SOURCE_RU = {'goes_p_ge10MeV': 'GOES ≥10 МэВ (архив NASA iSWA)', 'gfz_kp_archive': 'Kp, окончательный ряд GFZ',
                  'kp': 'Kp (сводный канал)', 'donki': 'уведомления NASA DONKI',
                  'noaa_ngdc_3day_forecast': 'трёхсуточный прогноз NOAA SWPC',
                  'noaa_ngdc_daypre': 'суточный прогноз NOAA SWPC'}
_COV_CHANNEL_RU = {'observations': 'наблюдения', 'notifications': 'уведомления', 'noaa_kp': 'прогноз Kp',
                   'r1_r2_probability': 'вероятность R1–R2 за сутки', 'r3_or_greater_probability': 'вероятность R3+ за сутки',
                   's1_or_greater_probability': 'вероятность S1+ за сутки', 'f107': 'поток F10.7',
                   'high_latitude_k': 'K в высоких широтах', 'mid_latitude_k': 'K в средних широтах',
                   'high_active_probability': 'вероятность активности, высокие широты',
                   'mid_active_probability': 'вероятность активности, средние широты',
                   'high_minor_storm_probability': 'вероятность слабой бури, высокие широты',
                   'mid_minor_storm_probability': 'вероятность слабой бури, средние широты',
                   'high_major_severe_storm_probability': 'вероятность сильной бури, высокие широты',
                   'mid_major_severe_storm_probability': 'вероятность сильной бури, средние широты',
                   'whole_disk_m_flare_probability': 'вероятность вспышки класса M по диску',
                   'whole_disk_x_flare_probability': 'вероятность вспышки класса X по диску',
                   'whole_disk_proton_probability': 'вероятность протонного события по диску'}
COV_STATUS_RU = {'full': 'полное', 'partial': 'частичное', 'none': 'нет', 'missing': 'записи нет',
                 'invalid': 'запись непригодна', 'inventory_only': 'только перечень записей'}
COV_REASON_RU = {
    'union_of_GFZ_three_hour_cells; final_data_for_review_only':
        'объединение 3-часовых интервалов GFZ; окончательные данные — только для разбора после факта',
    'verified GFZ cells and explicitly reported notification intervals':
        'проверенные интервалы GFZ и интервалы, прямо названные в уведомлениях',
    'no notification is not a declaration of no SEP/GST; ends and monitoring gaps may be unknown':
        'отсутствие уведомления не означает отсутствия события; концы событий и пропуски наблюдения могут быть неизвестны',
    'historic_publication_and_version_availability_not_proven':
        'историческая публикация и доступность именно этой версии не доказаны',
    'historical_publication_not_proven': 'историческая публикация не доказана',
    'only synoptic intervals explicitly reported in admitted notifications':
        'только интервалы, прямо названные в допущенных уведомлениях',
    'numerical historical GOES observations not present; threshold notifications do not replace them':
        'численных исторических наблюдений GOES нет; пороговые уведомления их не заменяют',
    'source_absent': 'источника нет', 'GFZ archive absent': 'архива GFZ нет',
}


def coverage_rows_ru(coverage_map: dict) -> list[dict]:
    """Карта покрытия A2 строками таблицы: источник, канал, состояние, доля горизонта, причина."""
    rows = []
    for key, cell in (coverage_map or {}).items():
        cell = cell or {}
        sid = cell.get('source_id') or str(key).split(':')[0]
        ch = cell.get('channel_id') or str(key).split(':')[-1]
        reason = cell.get('reason')
        rows.append({'источник': _COV_SOURCE_RU.get(sid, SOURCE_RU.get(sid, sid)),
                     'канал': _COV_CHANNEL_RU.get(ch, ch),
                     'покрытие': COV_STATUS_RU.get(cell.get('status'), cell.get('status') or '—'),
                     'доля горизонта': fmt(cell.get('coverage_fraction')),
                     'причина': COV_REASON_RU.get(reason, reason) if reason else '—'})
    return sorted(rows, key=lambda r: (r['покрытие'] == 'полное', r['источник'], r['канал']))


def record_release_ru(rid: str) -> str:
    """Номер выпуска источника из идентификатора записи «source_id:release_id:хеш[:тип]».
    На экране печатается он, а не внутренний ключ с хешем (О5)."""
    parts = (rid or '').split(':')
    return parts[1] if len(parts) > 1 and parts[1] else (rid or '—')


def robustness_gain_ru(rec, rob, windows_ru_iso: dict, base_thr: float, base_e: float) -> str:
    """О7: польза сетки порогов и допуска — одной строкой через сравнение (R4-9).

    Сравниваются два ответа на одних и тех же данных: (а) как если бы сервиса устойчивости не
    было — один порог, нулевой допуск, лучшее окно по ранжированию; (б) итоговый вердикт на всей
    сетке с допуском равнозначности. Оба числа берутся из `Robustness`, ничего не досчитывается."""
    ranking = dict(getattr(rob, 'ranking_by_grid', None) or {})
    if not ranking or (base_thr, base_e) not in ranking:
        return ''
    base = ranking[(base_thr, base_e)]          # только базовая ячейка: подменять её соседней нельзя
    # причину отсутствия выбора при нулевом допуске здесь не называем — слой устойчивости её
    # не сообщает, а придумывать её нельзя
    naive = ('предпочтительным было бы названо %s' % grid_cell_ru(base, windows_ru_iso)) if base \
        else 'предпочтительное окно не было бы названо'
    final = ('предпочтительное окно %s' % windows_ru_iso.get(rec.preferred.start_utc, '')) if rec.preferred is not None \
        else '«%s»' % VERDICT_TITLE.get(rec.verdict, rec.verdict).lower()
    same = base == (rec.preferred.start_utc.isoformat() if rec.preferred is not None else None)
    return ('Что даёт проверка на сетке: без неё (один порог %s нТл, канал от %s МэВ, нулевой допуск) %s; '
            'на сетке порогов и с допуском равнозначности вердикт — %s. Ответ без них и с ними %s.') % (
        nbsp_thousands(base_thr), fmt(float(base_e)), naive, final,
        'совпадает — сетка и допуск подтверждают ответ, а не создают его' if same else 'различается')


def grid_cell_ru(v, windows_ru_iso: dict) -> str:
    """Ячейка таблицы устойчивости: «Окно 1 (23:21Z)» или объяснение отсутствия выбора (O5-6)."""
    if not v:
        return 'нет предпочтительного (равнозначны или отказ)'
    try:
        t = datetime.fromisoformat(v)
    except (TypeError, ValueError):
        return str(v)
    n = windows_ru_iso.get(t)
    return ('Окно %s (%s)' % (n, t.strftime('%H:%MZ'))) if n else t.strftime('%d.%m %H:%MZ')


# --- подписи под графиками. Текст живёт рядом с описанием рисунка, а не в app/main.py:
# ряды ленты менялись дважды, и подпись, написанная у места вывода, каждый раз оставалась
# описывать прежний вид («Ряд 1 — |B| на трассе», «Ряд 3 — события») и противоречила картинке.
TIMELINE_MID_RU = {
    'history_forecast': 'прогноз Kp NOAA по 3-часовым интервалам из выпуска до отсечки',
    'live': 'наблюдения Kp (GFZ) и GOES ≥10 МэВ за последние 12 ч и прогноз Kp NOAA',
    'history_review': 'наблюдения Kp из архива (разбор после факта)',
}


def timeline_caption(mode: str, pro: bool = False) -> str:
    """Подпись под лентой времени: что в каком ряду и почему слева от отметки времени верхний ряд пуст.

    Формулировка не обещает третьего ряда: ряд событий появляется только тогда, когда события
    на горизонте есть, иначе вместо него стоит одна строка подписи под графиком."""
    mid = TIMELINE_MID_RU.get(mode, TIMELINE_MID_RU['live'])
    if not pro:
        mid = mid.split(' из выпуска')[0].split(' за последние')[0].split(' (разбор')[0]
        return ('Верхний ряд — сколько минут в аномалии накапливает каждое окно от своего начала; '
                'красные полосы — пролёты аномалии. Нижний ряд — %s. '
                'События и прогнозы — отдельным рядом, когда они есть на горизонте.' % mid)
    return ('Верхний ряд — накопленные минуты в аномалии по каждому окну, ступенями от начала окна '
            '(наш расчёт по IGRF): красные полосы — пролёты аномалии, цветные — сами окна. Слева от отметки '
            'времени ступеней нет не по потере данных: экспозиция считается только внутри окон, а окон в прошлом '
            'нет. Сырое |B| включается нажатием в легенде. Нижний ряд — %s; поток GOES выводится правой осью '
            'только тогда, когда достигает первого порога шкалы S, иначе печатается строкой под графиком. '
            'События и прогнозы — отдельным рядом, когда они есть на горизонте: положение по вертикали означает '
            'тип, не значение.' % mid)


def map_caption(pro: bool = False) -> str:
    """Подпись под плоской картой. Высота, на которой построен контур, стоит в заголовке самой карты."""
    if pro:
        return ('Область аномалии — наш расчёт |B| по IGRF на средней высоте трассы, сглаженный контур по сетке '
                '2° по долготе и 1° по широте; трасса за весь горизонт серым, окна-кандидаты цветом, точки трассы '
                'в аномалии красным. Карта показывает, откуда берутся минуты в аномалии.')
    return 'Красным — область аномалии и точки трассы в ней, цветом — окна: откуда берутся минуты в аномалии.'


# ================================================================= одиннадцатый круг: задача → ответ
# Сервис отвечает на вопрос человека «нам надо выйти», а не требует от него расставить окна
# ползунками. Человек задаёт длительность и срок, сервис перебирает все начала на сроке и
# называет лучшие. Ручное сравнение двух-трёх окон остаётся, но как разбор, а не как вход.
#
# Форма данных перебора — договор раздела 1a техзадания одиннадцатого круга. Экран читает ТОЛЬКО
# ключ `scan` снимка и не лезет во внутренности движка. Ключа нет — перебор не выполнялся, и экран
# так и говорит, не подставляя выдуманных чисел.
TASK_HINT_RU = ('Поля пересчитываются сразу: кнопка повторяет поиск на тех же данных. '
                'Времена на экране — UTC.')
# Пункт 1 двенадцатого круга. Владелец: «то, что сервис не учёл, мы скажем отдельно, в программе
# не надо прописывать»; «что не даёт глобус, тоже не надо писать»; «вообще недочёты не надо
# указывать в программе, подчисти это». Критерии оценки при этом требуют, чтобы ограничения охвата
# были видны В СЕРВИСЕ (О1, О2, О4, Т1), поэтому сделано не «удалить», а «собрать в одном месте»:
# всё уехало во вкладку «Методика», раздел «Границы применимости», а на главном экране осталась
# ОДНА нейтральная строка со ссылкой — без перечисления и без покаянного тона.
SCOPE_TAB_HINT_RU = 'Область применимости и принятые допущения — вкладка «Методика».'
LIMITS_SECTION_RU = 'Границы применимости'
SCAN_VERDICT_TITLE = {
    'recommended': 'Есть рекомендованное окно',
    'equivalent': 'Несколько начал равнозначны',
    'all_need_check': 'Все начала требуют проверки аналитиком',
    'insufficient': 'Оснований для рекомендации недостаточно',
}
# «Ниже — лучше» стоит у обоих графиков ленты: это единственная подсказка о направлении, и без неё
# член жюри читает падающую линию как ухудшение.
LOWER_IS_BETTER_RU = 'ниже — лучше'
# Сколько знаков «почему» движка блок рекомендации держит на поверхности на оперативном уровне.
# Остальное — раскрытием в том же блоке: на буре Гэннон движок перечисляет условие каждого из
# семидесяти трёх проверенных начал, и первым экраном это не читается.
WHY_BUDGET = 320
WHY_MORE_RU = 'Почему целиком: все проверенные начала и их условия'
# Заголовок свёртки с правилом перебора и допуском равнозначности. Сам текст пишет движок, и он
# длинный по существу: правило состоит из пяти условий, каждое из которых надо назвать целиком.
RULE_MORE_RU = 'Правило выбора и допуск равнозначности'


def scan_of(S: dict):
    """Ключ перебора начал из снимка расчёта — или None, если перебора не было.

    Договор раздела 1a: отсутствие ключа и пустой перебор — разные вещи, и путать их нельзя.
    Пустой список кандидатов договором не допускается, поэтому такому ключу экран не верит:
    он показывает прежний разбор окон, а не рисует ленту из ничего.
    """
    sc = (S or {}).get('scan')
    if not isinstance(sc, dict):
        return None
    return sc if (sc.get('candidates') or []) else None


def scan_absent_ru() -> str:
    """Честная строка на месте рекомендации, когда перебора в расчёте нет.

    Сказать «перебор не нашёл окна» здесь было бы неправдой: перебора не было вовсе.
    """
    return ('Перебор начал не выполнялся: выше вердикт по окнам, заданным вручную, '
            'выдуманных чисел на месте рекомендации нет.')


def _iso_dt(v):
    """Момент из строки снимка; не разобралось — None, экран от этого не падает."""
    try:
        return datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None


def scan_searched_ru(scan: dict) -> str:
    """«Перебрано 67 начал с шагом 10 мин на сроке 19.09 12:00 — 20.09 00:00.»

    Число перебранных начал печатается ВСЕГДА: это и есть доказательство, что сервис искал,
    а не показал две точки (техзадание, раздел 1).
    """
    cands = scan.get('candidates') or []
    n = int(scan.get('n_candidates') or len(cands))
    a, b = _iso_dt(scan.get('search_from_utc')), _iso_dt(scan.get('search_to_utc'))
    span = (' на сроке %s — %s' % (dt_ru(a), dt_ru(b))) if (a and b) else ''
    step = scan.get('step_min')
    step_ru = (' с шагом %s мин' % fmt(step)) if step else ''
    return ('Перебрано %s %s%s%s.'
            % (nbsp_thousands(n), plural_ru(n, ('начало', 'начала', 'начал')), step_ru, span))


def scan_recommended(scan: dict):
    """Рекомендованный кандидат по договору: индекс из снимка, а не «первый попавшийся лучший»."""
    cands = scan.get('candidates') or []
    i = scan.get('recommended_index')
    return cands[i] if isinstance(i, int) and 0 <= i < len(cands) else None


def scan_best_indices(scan: dict) -> list[int]:
    """Индексы лучшей группы, проверенные по списку кандидатов и упорядоченные по времени начала."""
    cands = scan.get('candidates') or []
    return sorted(i for i in (scan.get('best') or []) if isinstance(i, int) and 0 <= i < len(cands))


def _span_bounds(span):
    """Границы промежутка из `answer_span` — в том виде, в каком их кладёт движок.

    Форма поля у движка своя, и экран принимает обе записи: пару ключей и пару значений.
    Ничего не достраивается: не разобралось — промежутка нет.
    """
    if isinstance(span, dict):
        a = span.get('from_utc') or span.get('start_utc') or span.get('from') or span.get('start')
        b = span.get('to_utc') or span.get('end_utc') or span.get('to') or span.get('end')
    elif isinstance(span, (list, tuple)) and len(span) == 2:
        a, b = span
    else:
        return None, None
    return _iso_dt(a), _iso_dt(b)


def scan_answer(scan: dict) -> dict:
    """Какой ответ дал перебор: точка, промежуток, спор величин, проверка аналитиком или отказ.

    Главная развилка блока рекомендации, и читать её надо буквально по договору: движок кладёт
    `answer_kind` со значениями 'point' | 'interval' | 'tradeoff' | None, и ПУСТОЕ значение
    означает «ответа нет вовсе» — либо вердикт отказывает, либо все начала под условием проверки
    (15 прогонов из 110 на архиве). Без этого различия при `all_need_check` над окнами, каждое из
    которых требует решения аналитика, встало бы крупное «Выходить в промежутке» — то самое
    утверждение, которое опровергается числами рядом с ним.

    Промежуток берётся из `answer_span` КАК ЕСТЬ и сам не достраивается: движок называет
    сплошной кусок лучшей группы вокруг начала с наименьшим флюенсом, а в `best` при этом могут
    стоять и другие равнозначные начала, идущие не подряд. Тянуть промежуток через разрыв значило
    бы утверждать равнозначность начал, которые правило отбросило.

    Отказ — ТОЛЬКО при `insufficient`. `all_need_check` не отказ: обстановка нештатная, условие
    стоит у каждого начала, решение принимает аналитик.

    Снимок без `answer_kind` (движок ещё не слит) разбирается прежним путём: по `recommended_index`
    и по тому, идут ли начала лучшей группы подряд.
    """
    cands = scan.get('candidates') or []
    v = str(scan.get('verdict') or '')
    by_verdict = {'insufficient': 'none', 'all_need_check': 'check'}

    def _cand_at(t):
        return next((c for c in cands if _iso_dt(c.get('start_utc')) == t), None) if t is not None else None

    if 'answer_kind' in scan:
        ak = scan.get('answer_kind')
        if ak == 'point':
            rec_i = scan.get('recommended_index')
            c = cands[rec_i] if isinstance(rec_i, int) and 0 <= rec_i < len(cands) else None
            if c is None:
                best = scan_best_indices(scan)
                c = cands[best[0]] if best else None
            if c is not None:
                return {'kind': 'point', 'first': c, 'last': c, 'n': 1}
        elif ak == 'interval':
            a, b = _span_bounds(scan.get('answer_span'))
            if a is not None and b is not None:
                return {'kind': 'interval', 'from': a, 'to': b,
                        'first': _cand_at(a), 'last': _cand_at(b), 'n': len(scan_best_indices(scan))}
        elif ak == 'tradeoff':
            best = scan_best_indices(scan)
            return {'kind': 'tradeoff', 'first': cands[best[0]] if best else None,
                    'last': cands[best[-1]] if best else None, 'n': len(best)}
        return {'kind': by_verdict.get(v, 'none')}
    # --- прежний разбор для снимка без `answer_kind`
    if v in by_verdict:
        return {'kind': by_verdict[v]}
    rec_i = scan.get('recommended_index')
    if isinstance(rec_i, int) and 0 <= rec_i < len(cands):
        return {'kind': 'point', 'first': cands[rec_i], 'last': cands[rec_i], 'n': 1}
    best = scan_best_indices(scan)
    if len(best) == 1:
        return {'kind': 'point', 'first': cands[best[0]], 'last': cands[best[0]], 'n': 1}
    if len(best) > 1:
        cont = (best[-1] - best[0]) == len(best) - 1
        first, last = cands[best[0]], cands[best[-1]]
        if cont:
            return {'kind': 'interval', 'from': _iso_dt(first.get('start_utc')),
                    'to': _iso_dt(last.get('start_utc')), 'first': first, 'last': last, 'n': len(best)}
        return {'kind': 'tradeoff', 'first': first, 'last': last, 'n': len(best)}
    return {'kind': 'none'}


def _day_time_ru(t) -> str:
    """«19.09 в 16:20» — так человек и говорит; час без даты в ответе читался бы как «сегодня»."""
    return '%s в %s' % (t.strftime('%d.%m'), t.strftime('%H:%M')) if t is not None else '—'


def _span_ru(a, b) -> str:
    """Границы промежутка: дата у второй печатается только при переходе через полночь."""
    if a is None or b is None:
        return '—'
    return '%s — %s' % (a.strftime('%d.%m %H:%M'),
                        b.strftime('%d.%m %H:%M') if a.date() != b.date() else b.strftime('%H:%M'))


def _rank_key(c: dict):
    """Порядок таблицы лучших: сначала ранжированные по рангу, затем остальные по времени начала."""
    r = c.get('rank')
    return (0, int(r)) if isinstance(r, int) else (1, 0)


def scan_best_rows(scan: dict, limit: int = 5) -> list[dict]:
    """Короткая таблица лучших кандидатов: начало, минуты в аномалии, флюенс и — только если они
    есть хоть у одного показанного начала — условия проверки.

    Двенадцатый круг, пункт 4. Владелец о прежнем виде: «что значит условия проверки и почему
    там везде нет, нет, нет — либо убирай эту графу, либо исправляй». Колонка из одних «нет»
    ничего не сообщает и занимает треть ширины таблицы; когда условий нет ни у одного начала,
    колонки нет вовсе, а под таблицей стоит одна строка (`scan_conditions_note_ru`). Когда
    условие есть хотя бы у одного — колонка печатается и содержит НАЗВАНИЕ условия, а «нет»
    остаётся только у тех начал, где его действительно нет: там это уже различие, а не шум.

    Строк не больше пяти. Безразмерных баллов и нормировок в таблице нет: складывать минуты
    в аномалии с флюенсом нельзя, это разные величины.
    """
    cands = list(scan.get('candidates') or [])
    best = [i for i in (scan.get('best') or []) if isinstance(i, int) and 0 <= i < len(cands)]
    rest = sorted((i for i in range(len(cands)) if i not in best),
                  key=lambda i: (_rank_key(cands[i]), str(cands[i].get('start_utc') or '')))
    idx = (best + rest)[:max(0, int(limit))]
    conds_by_i = {i: [screen_text(x) for x in (cands[i].get('conditions') or [])] for i in idx}
    any_cond = any(conds_by_i[i] for i in idx)
    rows = []
    for i in idx:
        c = cands[i]
        row = {'начало выхода': dt_ru(_iso_dt(c.get('start_utc'))),
               'минут в аномалии': fmt(c.get('saa_min')),
               'флюенс, част./см²': fmt_fluence(c.get('fluence'))}
        if any_cond:
            # Название, а не абзац. Прежде сюда попадал весь текст условия со всеми записями:
            # на буре Гэннон это 1 522 знака в КАЖДОЙ из пяти строк — семь с половиной тысяч
            # знаков прозы в таблице чисел. Полный текст никуда не делся: он стоит раскрытием
            # «Условия проверки целиком» в блоке решения и в карточках окон.
            _names = sorted({cond_name_ru(x) for x in conds_by_i[i]})
            row['условия проверки'] = ('; '.join(_names[:2])
                                       + (' и ещё %d' % (len(_names) - 2) if len(_names) > 2 else ''))                 if _names else 'нет'
        rows.append(row)
    return rows


_COND_CUT_RE = re.compile(r'(?<!\d):\s')
_COND_TAIL_RE = re.compile(r'\s*\([^)]{16,}\)\s*$')


def cond_name_ru(text: str) -> str:
    """Короткое НАЗВАНИЕ условия проверки для таблицы: «GOES ≥10 МэВ = 145 pfu (S2)».

    Условие приходит из слоя расчёта одной длинной строкой: величина, уровень, наблюдение,
    следствие, правило, записи-основания. В таблице чисел нужна первая часть — что именно
    сработало; всё остальное открывается раскрытием в блоке решения, где стоит полный текст
    каждого условия со своими записями и временами публикации (критерий О4 — доступно, а не
    напечатано на первом экране).
    """
    t = str(text or '').strip()
    # Двоеточие РАЗДЕЛА, а не времени: «наблюдение 11.05 02:45» внутри названия резать нельзя,
    # иначе в таблице окажется обрывок «(S1, наблюдение 11.05 02».
    head_ = _COND_CUT_RE.split(t, 1)[0].strip()
    head_ = head_.split(' — ')[0].strip()
    # Длинный пояснительный хвост в скобках («(4 сигнала по 8 записям)») — не название.
    head_ = _COND_TAIL_RE.sub('', head_).strip()
    if len(head_) > 48:
        head_ = head_[:48].rstrip(' ,;.(') + '…'
    return head_ or t[:48]


def scan_conditions_note_ru(rows: list[dict] | None) -> str:
    """Строка под таблицей лучших начал, когда колонки условий в ней нет (пункт 4).

    Молчание вместо колонки было бы хуже колонки из «нет»: читатель не узнал бы, проверялись
    условия вообще или нет. Поэтому факт называется словами, одной строкой на всю таблицу.
    """
    if not rows:
        return ''
    # Тринадцатый круг: на главном экране прозы нет, и эта строка тоже стала ярлыком в три слова.
    # Факт тот же — условия проверялись и их нет; фраза целиком стоит во вкладке «Методика».
    return '' if 'условия проверки' in rows[0] else 'Условий проверки нет'


def ribbon_caption_ru(scan: dict, duration_min=None) -> str:
    """Подпись профиля воздействия — ОДНА строка обычными словами (пункт 3 двенадцатого круга).

    Владелец о прежнем рисунке: «этот график мне совершенно не ясен… совершенно непонятно,
    зачем он». Подпись отвечает ровно на это: что означает кривая, что означает полоса и что
    означает точка. Ни единицы, ни канала, ни порядка величин здесь нет — они названы на самих
    осях рисунка, и повторять их подписью значило бы писать одно и то же дважды.

    Про полосу сказано только тогда, когда полоса на рисунке ЕСТЬ: при споре величин и при
    условии у каждого начала сервис окна не называет, полосы нет, и обещать её подписью нельзя.
    """
    dur = int(duration_min or scan.get('requested_duration_min') or 0)
    dur_ru = ('за %s ч работы' % fmt(round(dur / 60.0, 1))) if dur else 'за окно'
    band = scan_answer(scan).get('kind') in ('point', 'interval')
    return ('Чем ниже кривая, тем меньше набранный поток частиц %s; %sточка — худшее время '
            'на сроке.' % (dur_ru, 'синяя полоса — рекомендованный промежуток, ' if band else ''))


def _hour_tick_ms(x) -> int:
    """Шаг часовых отметок оси времени в миллисекундах — по длине показанного срока.

    Владелец просил «обычные часовые отметки». На сроке до восьми часов это буквально каждый час;
    дальше подписи начинают наезжать друг на друга, и шаг растёт до двух, трёх и шести часов —
    отметки остаются круглыми часами, просто реже. Границы выбраны из ширины подписи «чч:мм»
    (около 44 пикселей при 12 px шрифта) и ширины блока (около 1 300 пикселей на ноутбуке):
    больше шестнадцати подписей на такой ширине уже сливаются.
    """
    hours = 12.0
    if x:
        hours = max(1.0, (max(x) - min(x)).total_seconds() / 3600.0)
    step_h = 1 if hours <= 8 else 2 if hours <= 16 else 3 if hours <= 30 else 6
    return int(step_h * 3600 * 1000)


def _decade_ticks(lo: float, hi: float) -> tuple[list[float], list[str]]:
    """Круглые степени десяти, накрывающие диапазон: 10³, 10⁴, 10⁵, 10⁶.

    Пункт 3 двенадцатого круга. Прежде подписями оси служили САМИ ЗНАЧЕНИЯ ДАННЫХ — «34·10⁶,
    56·10⁶, 81·10⁵, 2619», — то есть перечень точек, а не шкала: прочитать такой график нельзя.
    Отметки берутся круглые и подписываются нашим видом записи (надстрочная степень): собственные
    подписи Plotly — латинские сокращения, а на экране рядом стоит «2,43·10⁶».
    Восемь подписей — предел читаемости оси высотой около 260 пикселей; дальше шаг через порядок.
    """
    k0, k1 = int(math.floor(math.log10(lo))), int(math.ceil(math.log10(hi)))
    if k1 <= k0:
        k1 = k0 + 1
    step = 1 if (k1 - k0) <= 7 else 2
    ks = list(range(k0, k1 + 1, step))
    if ks[-1] != k1:
        ks.append(k1)
    return [10.0 ** k for k in ks], [sup('10^%d' % k) for k in ks]


def windows_ribbon(scan: dict, e_min_MeV=None, height: int = 360, duration_min=None):
    """Профиль воздействия на сроке поиска — ОДИН читаемый график (пункт 3 двенадцатого круга).

    Владелец о прежнем виде: «этот график мне совершенно не ясен, его лучше убрать, он очень
    странный и совершенно непонятно, зачем он». Разбор снимка 12 показал, в чём дело: подписи
    оси Y были не шкалой, а перечнем значений данных, и ломаных было две, каждая в своём
    масштабе. Здесь график один:

      * ось X — время начала выхода, круглые часовые отметки;
      * ось Y — набранный за окно флюенс, ЛОГАРИФМИЧЕСКАЯ шкала с круглыми отметками 10ᵏ:
        на сроке суток флюенс меняется на три порядка, и на линейной шкале весь профиль,
        кроме пика, ложится в линию у нуля;
      * рекомендованный промежуток — залитая полоса;
      * максимум профиля — точка с подписанным временем: это и есть «пиковое значение
        приходится на такое-то время»;
      * минуты в аномалии — не вторым графиком, а тонкой полосой-подложкой под тем же временем.

    Нормировки и безразмерных баллов нет по-прежнему: складывать минуты в аномалии с флюенсом
    нельзя, и «суммарный балл воздействия» был бы выдумкой. Каждая величина в своих единицах.

    Рисунок живёт здесь, а не в `app/viz.py`: в этом круге правка ограничена `app/main.py` и
    `app/ui.py`, а `app/viz.py` правят параллельно. Plotly импортируется ВНУТРИ функции, чтобы
    модуль оформления оставался без тяжёлой зависимости на импорте и не спорил с `app/viz.py`,
    который сам импортирует отсюда имена.
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    cands = list(scan.get('candidates') or [])
    xs = [_iso_dt(c.get('start_utc')) for c in cands]
    keep = [i for i, t in enumerate(xs) if t is not None]
    x = [xs[i] for i in keep]
    saa = [cands[i].get('saa_min') for i in keep]
    flu = [cands[i].get('fluence') for i in keep]
    good_flu = [v for v in flu if isinstance(v, (int, float))]
    # Логарифмическая шкала определена только для положительных значений, а Plotly точку вне
    # области определения молча выбрасывает. Молчаливого выбрасывания в сервисе быть не может
    # (CONTRACT.md, раздел 7, правило о молчаливых клампах): при нуле или отрицательном значении
    # шкала остаётся линейной, и подпись оси называет это прямо.
    log_ok = bool(good_flu) and min(good_flu) > 0
    # Границы оси берутся РОВНО по круглым отметкам, а не автоподбором: тогда залитая полоса
    # рекомендации рисуется во всю высоту поля и читается как полоса, а не как висящий
    # посреди графика прямоугольник.
    tickvals, ticktext = _decade_ticks(min(good_flu), max(good_flu)) if log_ok else ([], [])
    # Поле чуть шире крайних отметок: точка, лежащая ровно на нижней отметке, наполовину уходила
    # за ось и читалась как обрыв кривой. 0,06 порядка — примерно 15 % высоты одной декады.
    _LOG_PAD = 0.06
    y_range = [math.log10(tickvals[0]) - _LOG_PAD, math.log10(tickvals[-1]) + _LOG_PAD] if log_ok else None
    # Доли высоты: профиль занимает почти всё, полоса-подложка — тонкая лента снизу.
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05,
                        row_heights=[0.82, 0.18])
    # --- залитая полоса рекомендованного промежутка. Нарисована рядом данных, а не фигурой
    # разметки: у фигур разметки Plotly разных версий по-разному принимает время на оси, и пустая
    # полоса на защите дороже лишнего ряда.
    # Полоса рисуется ТОЛЬКО там, где сервис действительно называет окно: ответ точкой или
    # промежутком. При споре величин («лучшее по минутам одно, по флюенсу другое») и при условии
    # у каждого начала сервис окна не выбирает, и залитая полоса с подписью «рекомендованный
    # промежуток» была бы рекомендацией, в которой заголовок блока выше как раз отказал.
    ans = scan_answer(scan)
    a_from = a_to = None
    if ans.get('kind') in ('point', 'interval'):
        a_from = ans.get('from') or _iso_dt((ans.get('first') or {}).get('start_utc'))
        a_to = ans.get('to') or _iso_dt((ans.get('last') or {}).get('start_utc'))
    if a_from is not None and a_to is not None and good_flu:
        # Ответ точкой — это одно начало: полоса рисуется шириной в один шаг перебора, иначе
        # вырождается в невидимую линию нулевой ширины.
        if a_from == a_to:
            half = timedelta(minutes=float(scan.get('step_min') or 10) / 2.0)
            a_from, a_to = a_from - half, a_to + half
        lo_y, hi_y = (10.0 ** y_range[0], 10.0 ** y_range[1]) if log_ok else (0.0, max(good_flu) * 1.1)
        fig.add_trace(go.Scatter(x=[a_from, a_from, a_to, a_to], y=[lo_y, hi_y, hi_y, lo_y],
                                 mode='lines', fill='toself', name='рекомендованный промежуток',
                                 line={'color': 'rgba(0,0,0,0)'}, fillcolor=CALC_BLUE, opacity=0.16,
                                 hoverinfo='skip'), row=1, col=1)
    # --- сам профиль
    fig.add_trace(go.Scatter(x=x, y=flu, mode='lines+markers', name='набрано за окно',
                             line={'color': CALC_BLUE, 'width': 2}, marker={'size': 4, 'color': CALC_BLUE},
                             customdata=[fmt_fluence(v) for v in flu],
                             hovertemplate='начало %{x|%d.%m %H:%M} · %{customdata} част./см²<extra></extra>'),
                  row=1, col=1)
    # --- подписанный пик: худшее время на сроке. В подписи стоит ВРЕМЯ, а не значение: значение
    # читается по шкале, а часовой отметки ровно под пиком может не оказаться.
    if good_flu:
        i_pk = max((i for i in range(len(flu)) if isinstance(flu[i], (int, float))), key=lambda i: flu[i])
        fig.add_trace(go.Scatter(x=[x[i_pk]], y=[flu[i_pk]], mode='markers', name='пик на сроке',
                                 marker={'size': 10, 'color': PEAK_RED},
                                 hovertemplate='пик %{x|%d.%m %H:%M}<extra></extra>'), row=1, col=1)
        fig.add_annotation(x=x[i_pk], y=flu[i_pk], row=1, col=1,
                           text='пик %s' % _peak_label_ru(x[i_pk], x[0] if x else None),
                           showarrow=True, arrowhead=0, arrowwidth=1, arrowcolor=PEAK_RED,
                           ax=0, ay=-26, font={'size': 12, 'color': PEAK_RED})
    # --- полоса-подложка: минуты в аномалии под тем же временем, своей заливкой и своей шкалой.
    fig.add_trace(go.Scatter(x=x, y=saa, mode='lines', name='минут в аномалии', fill='tozeroy',
                             line={'color': CALC_BLUE, 'width': 1}, fillcolor=CALC_BLUE_FILL,
                             hovertemplate='начало %{x|%d.%m %H:%M} · %{y} мин в аномалии<extra></extra>'),
                  row=2, col=1)
    # Канал назван в подписи оси, а не под рисунком: величина без канала неполна — «протоны ≥ 30 МэВ»
    # и «протоны ≥ 50 МэВ» дают разные числа, и по голому «част./см²» различить их нечем.
    _e_ru = ('протоны ≥%s МэВ, ' % fmt(e_min_MeV)) if e_min_MeV is not None else ''
    _y_title = 'набрано за окно: %sчаст./см² (%s)' % (_e_ru, LOWER_IS_BETTER_RU)
    if log_ok:
        fig.update_yaxes(type='log', tickmode='array', tickvals=tickvals, ticktext=ticktext,
                         range=y_range, title_text=_y_title, row=1, col=1)
    else:
        # Шкала названа прямо: при нулевом значении логарифмическая не определена, и молчать об этом
        # нельзя: одна и та же кривая выглядит по-разному на двух шкалах.
        fig.update_yaxes(title_text=_y_title.replace(' (%s)' % LOWER_IS_BETTER_RU,
                                                     ', шкала линейная (%s)' % LOWER_IS_BETTER_RU),
                         row=1, col=1)
    # Подложка: две отметки — ноль и круглый верх. Третья на ленте в 18 % высоты не помещается.
    good_saa = [v for v in saa if isinstance(v, (int, float))]
    if good_saa:
        top = max(good_saa)
        step = 10.0 if top > 20 else 5.0 if top > 5 else 1.0
        top = step * (int(top / step) + (1 if top % step else 0) or 1)
        fig.update_yaxes(tickmode='array', tickvals=[0, top], ticktext=['0', fmt(top)],
                         range=[0, top * 1.05], row=2, col=1)
    fig.update_yaxes(title_text='в аномалии, мин', row=2, col=1)
    fig.update_xaxes(title_text='время начала выхода, UTC', row=2, col=1)
    fig.update_xaxes(dtick=_hour_tick_ms(x), tickformat='%H:%M')
    # Заголовка у рисунка нет намеренно: над ним стоит заголовок блока, а под ним подпись о том,
    # как читать. Третья строка о том же была бы вторым сообщением об одном и том же.
    fig.update_layout(template='plotly_white', height=int(height), separators=',' + NBSP_THIN,
                      margin={'l': 78, 'r': 20, 't': 40, 'b': 40}, hovermode='x unified',
                      legend={'orientation': 'h', 'yanchor': 'bottom', 'y': 1.02, 'x': 0})
    return fig


# Тёмные цвета светлой темы → их тёмнотемные пары. Ключи — ровно те константы, которыми рисуют
# app/viz.py и app/obs.py: подстановка точная, а не «похожий оттенок на глаз». Смысл цвета не
# меняется: синий остаётся нашим расчётом, зелёный — наблюдением, янтарный — внешним прогнозом.
DARK_TRACE_COLORS = {
    '#1f4e79': '#7ab8f5', '#5dade2': '#4f8fc0', '#85c1e9': '#3f6f97',     # наш расчёт и окна
    '#1e8449': '#5ed39a', '#b9770e': '#e7b45c', '#c0392b': '#f58b7f',     # наблюдение, прогноз, условие
    '#7f8c8d': '#9aa5b5', '#9aa0a6': '#9aa5b5',                           # вспомогательные линии
}
DARK_PAPER, DARK_PLOT, DARK_GRID = '#08090b', '#101217', '#202328'
# Второй проход по цвету: DARK_TRACE_COLORS выше переводит цвета СВЕТЛОЙ темы в прежние тёмные
# пары, и трогать её нельзя — та же таблица лежит в app/viz.py (чужая область в этом круге),
# и их совпадение сторожит tests/test_viz_visual.py. Поэтому новая палитра подставляется
# ОТДЕЛЬНОЙ таблицей, поверх: прежний тёмный тон → нынешний. Так рисунки идут теми же цветами,
# что и страница, а обе старые таблицы остаются побайтово равны друг другу.
# Заливки задаются с прозрачностью и мимо разбора hex проходят, поэтому названы строкой целиком.
REPALETTE = {
    '#7ab8f5': '#a1bed5', '#4f8fc0': '#7b93a6', '#3f6f97': '#5c6e7c',     # наш расчёт и окна
    '#5ed39a': '#76a78e', '#e7b45c': '#bba889', '#f58b7f': '#c17d68',     # наблюдение, прогноз, условие
    '#9aa5b5': '#91969c',                                                 # вспомогательные линии
    '#e8ebf2': '#e4e7ea', '#a7b0c0': '#9da1a8',                           # текст на рисунке
    '#0e1117': '#08090b', '#161b26': '#101217', '#242b38': '#202328',     # бумага, панель, сетка
    'rgba(245,139,127,0.16)': 'rgba(193,125,104,0.16)',                   # заливка аномалии
}


# Гарнитура рисунков — та же, что у страницы. Объявлять её приходится ЗДЕСЬ: app/viz.py задаёт
# своё семейство (прежний Public Sans) и остаётся чужой областью в этом круге, а подписи осей
# набранные не тем шрифтом, что весь остальной экран, — ровно тот разнобой, из-за которого
# страница читается как собранная из кусков. Ряд запасных тот же, что в :root.
DARK_FONT = 'IBM Plex Sans, Segoe UI, Inter, Roboto, Arial, sans-serif'


def _one_color(x):
    """Один цвет: сперва тёмная пара светлой темы, затем нынешняя палитра поверх неё."""
    if not isinstance(x, str):
        return x
    v = DARK_TRACE_COLORS.get(x.lower(), x)
    return REPALETTE.get(str(v).lower().replace(' ', ''), v)


def _dark_color(v):
    """Цвет ряда в тёмной паре; незнакомый оставляем как есть, чтобы ничего не испортить."""
    if isinstance(v, str):
        return _one_color(v)
    if isinstance(v, (list, tuple)):
        return [_one_color(x) for x in v]
    return v


def dark_figure(fig):
    """Привести готовый рисунок Plotly к тёмной теме экрана.

    Рисунки строят `app/viz.py` и `app/obs.py` — чужая в этом круге область, и правка идёт ЗДЕСЬ,
    над готовым объектом: подложка, сетка, шрифт и цвета рядов заменяются на тёмные пары той же
    палитры. Светлая подложка графика на тёмной странице — первое, что читается как халтура.
    """
    fig.update_layout(template='plotly_dark', paper_bgcolor=DARK_PAPER, plot_bgcolor=DARK_PLOT,
                      font={'color': '#e4e7ea', 'family': DARK_FONT},
                      legend={'bgcolor': 'rgba(0,0,0,0)'},
                      hoverlabel={'bgcolor': DARK_PLOT, 'font': {'color': '#e4e7ea', 'family': DARK_FONT}})
    fig.update_xaxes(gridcolor=DARK_GRID, zerolinecolor=DARK_GRID, linecolor=DARK_GRID,
                     tickfont={'color': '#9da1a8'}, title_font={'color': '#9da1a8'})
    fig.update_yaxes(gridcolor=DARK_GRID, zerolinecolor=DARK_GRID, linecolor=DARK_GRID,
                     tickfont={'color': '#9da1a8'}, title_font={'color': '#9da1a8'})
    for tr in fig.data:
        for holder in ('line', 'marker'):
            obj = getattr(tr, holder, None)
            if obj is not None and getattr(obj, 'color', None) is not None:
                obj.color = _dark_color(obj.color)
            inner = getattr(obj, 'line', None) if obj is not None else None
            if inner is not None and getattr(inner, 'color', None) is not None:
                inner.color = _dark_color(inner.color)
        if getattr(tr, 'fillcolor', None) is not None:
            tr.fillcolor = _dark_color(tr.fillcolor)
    for sh in (fig.layout.shapes or ()):
        if getattr(sh, 'line', None) is not None and getattr(sh.line, 'color', None) is not None:
            sh.line.color = _dark_color(sh.line.color)
        # Заливка фигуры разметки — полосы окон и полоса аномалии на ленте. app/viz.py кладёт в
        # неё тёмную пару СВОЕЙ таблицы, то есть прежний тон, и без этой строки полосы остались
        # бы небесно-голубыми под стальным профилем — разнобой ровно там, где его видно первым.
        if getattr(sh, 'fillcolor', None) is not None:
            sh.fillcolor = _dark_color(sh.fillcolor)
    for an in (fig.layout.annotations or ()):
        if getattr(an, 'font', None) is not None and getattr(an.font, 'color', None) is not None:
            an.font.color = _dark_color(an.font.color)
    return fig


def _factor_source_ru(f, raw_records: dict | None) -> str:
    """Источник величины ссылкой — той же записью, на которую ссылается карточка объяснения.

    Ссылка идёт в разметке Markdown, а не тегом: голого адреса на оперативном уровне быть
    не должно, а подпись ссылки — имя выпуска источника.
    """
    from vkd.explain.format import source_ru as _source_ru
    ids = list(getattr(f, 'record_ids', None) or ())
    links, no_link, seen = [], [], set()
    for rid in ids:
        sid = str(rid or '').split(':')[0]
        if sid in seen:
            continue                      # два выпуска одного источника — одна ссылка, не две
        u = record_url(raw_record(raw_records, rid))
        if u:
            # Подпись ссылки — ИМЯ ИСТОЧНИКА, а не имя записи: имя записи несёт номер выпуска
            # («202405100030three_day_forecast»), то есть английский идентификатор на оперативном
            # уровне (бриф §9.8). Номер выпуска с временем публикации остаётся в карточке
            # объяснения, где он и нужен, чтобы различать два уведомления с разными числами.
            name = SOURCE_RU.get(sid) or _source_ru(sid)
            links.append('[%s](%s)' % (name, u))
            seen.add(sid)
        else:
            no_link.append(rid)
        if len(links) >= 2:
            break
    if links:
        return ', '.join(links)
    if no_link:
        return record_no_url_ru(no_link[:3], raw_records) or 'источник назван в таблице источников'
    return 'источник назван в таблице источников'


DIMENSIONLESS_RU = 'безразмерная величина'


def value_with_unit_ru(f) -> str:
    """Значение величины С ЕДИНИЦЕЙ; у безразмерных единица называется словами.

    Владелец: «добавь размерности, где они нужны». Голое число на экране не говорит, в чём оно
    измерено, и Kp тут худший случай: 1,33 — это не минуты, не проценты и не пункты шкалы,
    а безразмерный планетарный индекс. Молчание о единице здесь — не экономия, а пробел.
    """
    unit = getattr(f, 'unit', '') or ''
    val = factor_value_ru(f, unit)
    if val == '—':
        # Прочерк рядом с тире читался как обрыв строки («поток GOES — —»). Отсутствие значения
        # называется словами, как и в карточке окна: величины у окна нет.
        return 'значения на окно нет'
    return val if (unit and unit != '1') else '%s, %s' % (val, DIMENSIONLESS_RU)


def accounted_lines(a, raw_records: dict | None = None) -> list[str]:
    """«Что учтено»: каждая величина окна со значением, единицей и происхождением.

    Это те же числа, что в карточке окна, собранные в одно место и обычными словами: постановка
    требует, чтобы учтённые воздействия были перед глазами, а не только в карточках (раздел 3.4).
    Источник каждой величины стоит НЕ здесь, а в раскрытии рядом (`accounted_sources`): на первом
    экране он давал вторую половину строки у каждой из десяти величин, и читать их было нечем.
    """
    out = []
    for m in (getattr(a, 'mechanisms', None) or ()):
        if not (m.mandatory or m.coverage.value != 'none'):
            continue
        for f in m.factors:
            out.append('- **%s** — %s · %s'
                       % (screen_text(f.name), screen_text(value_with_unit_ru(f)),
                          kind_pill(f.kind.value if hasattr(f.kind, 'value') else f.kind)))
    return out


def accounted_sources(a, raw_records: dict | None = None) -> list[str]:
    """Источник каждой величины окна — ссылкой на первоисточник, в раскрытии под блоком.

    Прослеживаемость не теряется: строка есть у каждой величины, просто на один клик глубже.
    """
    return ['- **%s** — %s' % (screen_text(f.name), _factor_source_ru(f, raw_records))
            for m in (getattr(a, 'mechanisms', None) or ())
            if (m.mandatory or m.coverage.value != 'none') for f in m.factors]


def accounted_lines_from_scan(cand: dict, e_min_MeV=None) -> list[str]:
    """То же, но по кандидату перебора: перебор считает три различающие величины, и врать,
    что посчитано больше, нельзя. Единицы и происхождение — как у полного расчёта окна."""
    e_ru = ('≥%s МэВ' % fmt(e_min_MeV)) if e_min_MeV is not None else ''
    rows = [('минут в аномалии', fmt(cand.get('saa_min'), 'мин')),
            ('флюенс захваченных протонов %s' % e_ru if e_ru else 'флюенс захваченных протонов',
             fmt_fluence(cand.get('fluence'), 'част./см²')),
            ('ожидаемое число попаданий метеороидов, пластина 1 м²',
             '%s, %s' % (fmt(cand.get('mmod_hits')), DIMENSIONLESS_RU))]
    return ['- **%s** — %s · %s' % (screen_text(n), screen_text(v), kind_pill('own_calculation'))
            for n, v in rows]


SCAN_SOURCES_RU = [
    '- **минут в аномалии** — поле IGRF на трассе, формула (3), вкладка «Методика»',
    '- **флюенс захваченных протонов** — таблицы ОСТ 134-1044-2007, формулы (1) и (2), вкладка «Методика»',
    '- **ожидаемое число попаданий метеороидов** — модель ECSS/Grün, формулы (5)–(7), вкладка «Методика»',
]


# Структурные пробелы охвата. Каждый уже объявлен в другом месте сервиса — в политике прототипа,
# в таблице порогов или в решении о суточных вероятностях, — и собран здесь обычными словами,
# чтобы «чего не учли» стояло рядом с «что учтено», а не было разбросано по вкладкам.
NOT_ACCOUNTED_ALWAYS_RU = [
    'протонное событие внутри окна — прогноза потока с разрешением по окну не существует ни у одного '
    'источника; суточная вероятность NOAA остаётся суточной и в вероятность за окно не пересчитывается',
    'сближение с каталогизированным объектом — линия запланирована, но источник не подключён, '
    'на вердикт она не влияет',
    'доза на человека и защита скафандра — не вычисляются: нужны модели защиты и ткани, '
    'которых в обязательной части нет',
    'вероятность разгерметизации и попадания в космонавта — не вычисляется',
    'техногенный мусор и потоки метеороидов конкретной даты — в модели ECSS/Grün не входят',
]


def not_accounted_ru(S: dict, mode: str = 'live') -> list[str]:
    """Чего сервис НЕ учёл — поимённо и с причиной, обычными словами.

    Сначала то, что объявил сам расчёт (пропуски охвата и состояние линии уведомлений), затем
    структурные пробелы. Из головы здесь ничего не пишется: причина, которой нет в расчёте,
    в список не попадает.
    """
    out = [screen_text(x) for x in (S.get('coverage_missing') or [])]
    line = S.get('events_line') or {}
    # Линия уведомлений называется РОВНО ОДИН раз (бриф §9.7): в текущем режиме слой расчёта уже
    # кладёт её в пропуски охвата, и вторая строка о том же читалась бы как второй пробел.
    if mode == 'live' and not line.get('connected') and not any('DONKI' in x for x in out):
        reason = line.get('reason_ru') or ''
        out.append('%s — %s' % (line.get('source_ru') or 'уведомления о событиях',
                                screen_text(status_ru(reason)) if reason else 'источник не опрашивается'))
    for x in NOT_ACCOUNTED_ALWAYS_RU:
        if x not in out:
            out.append(x)
    return out


def refusal_lift_ru(verdict: str, missing_ru=None, mode: str = 'live') -> str:
    """Что нужно, чтобы отказ снялся, — без обещаний, которых сервис выполнить не может.

    При структурном пробеле строка не обещает нового выпуска источника: прогноза потока протонов
    с разрешением по окну не существует в природе, и сколько ни ждать следующего выпуска, покрытие
    не появится (`docs/design/RESHENIE_TEKUSCHIY_REZHIM.md`, смежная находка).
    """
    need = [screen_text(x) for x in (missing_ru or [])]
    head_ru = ('Чтобы отказ снялся, нужно то, чего сейчас нет: %s.' % '; '.join(need[:3])) if need \
        else 'Чтобы отказ снялся, нужны данные обязательной линии на срок поиска.'
    if verdict == 'all_need_check':
        return ('Обстановка уже нештатная: условие проверки стоит у каждого начала, и правило команды '
                'окно с условием не выбирает. Снимает условие либо возврат уровня к фону по наблюдению, '
                'либо решение аналитика по фактической обстановке у смены.')
    tail = ('Ожидание следующего выпуска прогноза этого не закрывает: прогноза потока протонов '
            'с разрешением по окну не существует ни у одного источника. Посильное — вернуть источник '
            'в работу в боковой панели либо считать на сроке, начинающемся ближе к моменту последнего '
            'наблюдения.') if mode == 'live' else \
        ('Посильное — сдвинуть срок поиска ближе к моменту последнего наблюдения или взять другой '
         'момент разбора, где линия покрыта.')
    return head_ru + ' ' + tail


def _peak_label_ru(t_peak, t_first) -> str:
    """Время пика так, как его называют и подпись на графике, и популярное объяснение.

    Час без даты читается как «сегодня». Пока срок поиска не переходит через полночь, дата —
    лишний шум; как только переходит, без неё «00:40» неоднозначно. Правило одно на оба места:
    иначе экран называет один и тот же момент двумя разными способами.
    """
    if t_peak is None:
        return '—'
    if t_first is not None and t_peak.date() != t_first.date():
        return t_peak.strftime('%d.%m %H:%M')
    return t_peak.strftime('%H:%M')


def plain_why_ru(scan: dict, cand: dict | None, duration_min: int | None = None) -> str:
    """Ровно ДВА предложения обычными словами: почему выходить именно в этот промежуток.

    Пункт 5 двенадцатого круга, дословно: «здесь надо пояснять популярным языком, почему надо
    выходить именно в этот временной промежуток… также пиковое значение приводится на вот такое-то
    время, но при этом перегружать текстом нельзя».

    Правила, которым подчинён этот текст:
      * два предложения, не больше. Третье — уже перегрузка;
      * слов «флюенс», «частиц на квадратный сантиметр», «геомагнитный» в нём нет: это те самые
        термины, из-за которых блок и читался как технический;
      * каждое число берётся ИЗ РАСЧЁТА и ни одно не выдумывается. Нет числа — нет предложения,
        и функция возвращает пустую строку: пустое место честнее правдоподобной фразы;
      * сравнение идёт с ХУДШИМ началом на сроке, а не с «обычным»: «обычного» в расчёте нет.

    Первое предложение — минуты в аномалии рекомендованного окна против худшего начала на сроке.
    Второе — во сколько раз ниже набранный поток по сравнению с пиковым и на какое время приходится
    сам пик. Пик по минутам и пик по потоку — разные моменты, поэтому время названо только у того,
    о ком речь во втором предложении.
    """
    if not isinstance(cand, dict):
        return ''
    cands = [c for c in (scan.get('candidates') or []) if isinstance(c, dict)]
    saa = cand.get('saa_min')
    flu = cand.get('fluence')
    saa_all = [c.get('saa_min') for c in cands if isinstance(c.get('saa_min'), (int, float))]
    flu_all = [(c.get('fluence'), _iso_dt(c.get('start_utc'))) for c in cands
               if isinstance(c.get('fluence'), (int, float)) and _iso_dt(c.get('start_utc')) is not None]
    dur = int(duration_min or scan.get('requested_duration_min') or 0)
    if not isinstance(saa, (int, float)) or not isinstance(flu, (int, float)) or not saa_all or not flu_all or not dur:
        return ''
    saa_worst = max(saa_all)
    flu_peak, t_peak = max(flu_all, key=lambda p: p[0])
    if flu <= 0 or flu_peak <= 0:
        return ''                     # отношения нет — второго предложения не из чего собрать
    # «7 минут из 360» — единица называется один раз, у первого числа: второе стоит в тех же
    # минутах, и повторять её значило бы писать «7 минут из 360 минут».
    # Падеж у двух чисел РАЗНЫЙ, и одной формой тут не обойтись: «проводит 4 минуты» —
    # винительный, «против 74 минут» — родительный. Одна пара форм на оба места давала
    # «против 74 минуты»; на экране, который читает жюри, это грубая ошибка согласования.
    m_vin = lambda v: '%s %s' % (fmt(round(float(v))), plural_ru(int(round(float(v))), ('минуту', 'минуты', 'минут')))
    m_rod = lambda v: '%s %s' % (fmt(round(float(v))), plural_ru(int(round(float(v))), ('минуты', 'минут', 'минут')))
    # «Меньше всего» — сильное утверждение, и говорить его можно ТОЛЬКО когда оно верно. Правило
    # выбора окна не минимизирует минуты в аномалии: оно отбирает начала, которых не превосходит
    # ни одно другое СРАЗУ ПО ДВУМ величинам, и рекомендованное начало может проигрывать по
    # минутам тому, кто сильно хуже по потоку. Когда минимум не у него, предложение просто
    # называет оба числа без превосходной степени: это по-прежнему ответ на «почему тогда»,
    # но не заявление, которое опровергается таблицей под ним.
    saa_best = min(saa_all)
    if saa_worst - saa < 1.0:
        first = ('В этот промежуток станция проводит в радиационной аномалии %s из %s — столько же, '
                 'сколько в любое другое время на сроке поиска.' % (m_vin(saa), fmt(round(float(dur)))))
    elif saa - saa_best < 0.5:
        first = ('В этот промежуток станция меньше всего времени проводит в радиационной аномалии — '
                 '%s из %s против %s в худшее время на сроке.'
                 % (m_vin(saa), fmt(round(float(dur))), m_rod(saa_worst)))
    else:
        first = ('В этот промежуток станция проводит в радиационной аномалии %s из %s — против %s '
                 'в худшее время на сроке.'
                 % (m_vin(saa), fmt(round(float(dur))), m_rod(saa_worst)))
    ratio = float(flu_peak) / float(flu)
    # Час без даты читается как «сегодня». Срок поиска доходит до суток, и пик легко попадает на
    # следующие: «приходится на 00:40» рядом с окном «26.06 04:00 — 05:00» тогда неоднозначно.
    # Дата печатается ровно тогда, когда пик не в тот же день, что начало срока, — и тем же
    # правилом, что подпись пика на самом графике (`_peak_label_ru`), чтобы экран не называл
    # одно и то же время двумя разными способами.
    t_ru = _peak_label_ru(t_peak, _iso_dt((cands[0] if cands else {}).get('start_utc')))
    if ratio >= 1.5:
        # Отношение огрубляется до двух значащих цифр: «в 1 463 раза» обещает точность, которой
        # у модели нет (у самого флюенса на экране печатаются три значащие цифры), и читается
        # хуже, чем «в 1 500 раз». Округление только ВВЕРХ по значащим цифрам не делается —
        # обычное, чтобы не преувеличивать выигрыш рекомендованного окна.
        if ratio >= 100:
            _p = 10.0 ** (len(str(int(ratio))) - 2)
            n = int(round(ratio / _p) * _p)
        else:
            n = int(round(ratio)) if ratio >= 10 else round(ratio, 1)
        second = ('Набранный поток частиц в нём в %s %s ниже пикового, который приходится на %s.'
                  % (nbsp_thousands(n) if isinstance(n, int) else fmt(n),
                     plural_ru(int(round(float(n))), ('раз', 'раза', 'раз')), t_ru))
    elif ratio >= 1.02:
        second = ('Набранный поток частиц в нём на %s %% ниже пикового, который приходится на %s.'
                  % (fmt(round((ratio - 1.0) * 100)), t_ru))
    else:
        second = ('Набранный поток частиц в нём почти такой же, как в самое неудачное время суток, — '
                  'оно приходится на %s.' % t_ru)
    return first + ' ' + second


# ================================================================= цвет решения (тринадцатый круг)
# Эксперты хакатона, дословно по смыслу: «вкусовщина: выходить в промежутке — зелёным цветом;
# если не выходить — красным текстом». Требование выполняется, но НЕ перекраской величин:
# зелёный на этом экране уже занят происхождением «наблюдение источника», и сделать его
# двузначным нельзя. Разведены ФОРМЫ — см. большой комментарий в CSS у класса .decision.
#
# Соответствие исходов и цветов решения:
#   'point', 'interval'  → зелёный: есть куда выходить (предпочтительное окно либо промежуток
#                          равнозначных начал — оба ответ, а не отсутствие ответа);
#   'none'               → красный: оснований для рекомендации недостаточно;
#   'check', 'tradeoff'  → янтарный: сервис решения не принимает, решает аналитик. Спор величин
#                          сверх допуска — тот же случай, и красить его отказом было бы неправдой.
DECISION_TONE = {'point': 'go', 'interval': 'go', 'none': 'stop', 'check': 'ask', 'tradeoff': 'ask'}
# Ярлык над крупной строкой решения — капителью, одним словом. Это подпись к ПОЛОСЕ, а не фраза,
# и он один на все исходы: что именно решено, говорит сама крупная строка под ним.
DECISION_LABEL_RU = {'go': 'Решение', 'stop': 'Решение', 'ask': 'Решение'}
# Крупная строка полосы. «ВЫХОДИТЬ НЕ РЕКОМЕНДУЕТСЯ» здесь не годится: первым словом красной
# полосы во весь экран стоит «ВЫХОДИТЬ», и при беглом взгляде отказ читается как разрешение.
DECISION_VALUE_RU = {'none': 'РЕКОМЕНДАЦИИ НЕТ', 'check': 'РЕШАЕТ АНАЛИТИК', 'tradeoff': 'РЕШАЕТ АНАЛИТИК'}
# Одна строка на экране о том, что цветов на нём два и они о разном. Стоит там же, где легенда
# происхождения, — рядом с плашками, один раз на весь экран (техзадание одиннадцатого круга, п. 4.6).
DECISION_LEGEND = 'Цвет величины — происхождение, цвет решения — вывод.'
# Короткая причина в самой полосе: не больше восьми слов. Красная полоса без причины читается
# как поломка сервиса, а не как честный отказ; полный текст — раскрытием в том же блоке.
DECISION_CAUSE_RU = {'none': 'нет данных обязательной линии на срок',
                     'check': 'условие проверки стоит у каждого начала',
                     'tradeoff': 'величины указывают на разные начала'}


def decision_band(kind: str, value_ru: str, cause_ru: str = '') -> str:
    """Полоса решения во всю ширину: ярлык, крупный ответ, при отказе — короткая причина.

    Больше в полосе нет ничего. Числа стоят отдельным рядом под ней, обоснования — раскрытиями.
    """
    tone = DECISION_TONE.get(str(kind), 'stop')
    out = ['<div class="decision d-%s">' % tone,
           '<span class="dk">%s</span>' % esc(DECISION_LABEL_RU.get(tone, 'Решение')),
           '<span class="dv">%s</span>' % esc(value_ru)]
    if cause_ru:
        out.append('<span class="dwhy">%s</span>' % esc(cause_ru))
    out.append('</div>')
    return ''.join(out)


def _worst(cands, key: str):
    """Наибольшее значение величины среди всех перебранных начал — «худшее время на сроке».

    Из головы здесь ничего не берётся: нет ни одного числа — нет и сравнения.
    """
    vals = [c.get(key) for c in cands if isinstance(c.get(key), (int, float))]
    return max(vals) if vals else None


def dose_factor(assessment):
    """Фактор поглощённой дозы за защитой скафандра у оценки окна — или None.

    Доза понятнее человеку, чем флюенс, и владелец просит её третьим числом в ряду. Она
    считается ПОЛНОЙ оценкой окна, а не перебором: у кандидатов перебора её нет вовсе
    (договор раздела 1a — три различающие величины). Значит и показывается она только там,
    где действительно посчитана; выдумывать её из флюенса нельзя.
    """
    for m in (getattr(assessment, 'mechanisms', None) or ()):
        for f in (getattr(m, 'factors', None) or ()):
            if str(getattr(f, 'name', '')).startswith('поглощённая доза'):
                return f
    return None


def scan_stat_rows(scan: dict, cand: dict | None, e_min_MeV=None, dose=None) -> list:
    """Ряд крупных чисел под решением: величина, значение, рядом — худшее на сроке.

    Тринадцатый круг, требование владельца: «сравнение „выбрано против худшего“ — это и есть
    объяснение, только числами». Поэтому фразы «почему» словами на поверхности больше нет,
    а её числа стоят здесь: минуты в аномалии, флюенс и доказательство того, что сервис искал
    (сколько начал перебрано и с каким шагом).

    Ни одного слова связки. Ярлык — название величины с единицей, подпись — «худшее NN».
    Величины, которой в переборе нет, здесь тоже нет: пустая ячейка честнее правдоподобной.
    """
    cands = [c for c in (scan.get('candidates') or []) if isinstance(c, dict)]
    rows = []
    # Единица стоит в ПОДПИСИ под числом, а не в ярлыке: ярлык печатается капителью, и «ЧАСТ./СМ²»
    # прописными — уже не единица, а опечатка. Ярлык называет величину, подпись — единицу, канал
    # и то же число у худшего начала на сроке.
    saa = (cand or {}).get('saa_min')
    if isinstance(saa, (int, float)):
        w = _worst(cands, 'saa_min')
        rows.append(('Минут в аномалии', fmt(round(float(saa), 1)),
                     'мин' + (' · худшее %s' % fmt(round(float(w))) if isinstance(w, (int, float)) else ''), 'calc'))
    flu = (cand or {}).get('fluence')
    if isinstance(flu, (int, float)):
        w = _worst(cands, 'fluence')
        e_ru = (', ≥%s МэВ' % fmt(e_min_MeV)) if e_min_MeV is not None else ''
        rows.append(('Флюенс', fmt_fluence(flu),
                     'част./см²' + e_ru + (' · худшее %s' % fmt_fluence(w) if isinstance(w, (int, float)) else ''),
                     'calc'))
    # Доза — ТРЕТЬИМ числом: из всех наших величин она понятнее человеку, и это воздействие,
    # а не показатель процесса. Худшего значения рядом с ней нет и быть не может: дозу считает
    # полная оценка окна, а перебор её не считает ни для одного другого начала. Пустое место
    # честнее правдоподобного числа, и высчитывать дозу из отношения флюенсов запрещено.
    dv = getattr(dose, 'value', None)
    if isinstance(dv, (int, float)):
        _u = str(getattr(dose, 'unit', '') or '')
        rows.append(('Доза за защитой', fmt(dv), (_u + ' · скафандр 1 г/см²') if _u else 'скафандр 1 г/см²', 'calc'))
    # «Перебрано начал» в ряду крупных чисел не стоит: это доказательство того, что сервис искал,
    # а не воздействие, и рядом с минутами и флюенсом оно читалось как четвёртая величина.
    # Число никуда не делось — оно стоит мелкой строкой под рядом (`scan_searched_short_ru`).
    return [rows]


def scan_searched_short_ru(scan: dict) -> str:
    """«73 начала · шаг 10 мин» — доказательство поиска ярлыком, а не фразой.

    Полная фраза со сроком поиска остаётся раскрытием в том же блоке и в выгрузке.
    """
    n = int(scan.get('n_candidates') or len(scan.get('candidates') or []))
    step = scan.get('step_min')
    return '%s %s%s' % (nbsp_thousands(n), plural_ru(n, ('начало', 'начала', 'начал')),
                        (' · шаг %s мин' % fmt(step)) if step else '')


def recommendation_panel(scan: dict, S: dict, pro: bool = False, mode: str = 'live',
                         missing_ru=None, duration_min: int | None = None, dose=None) -> str:
    """Блок ответа: полоса решения, ряд крупных чисел и раскрытия со всем остальным.

    Тринадцатый круг, решение владельца: «мне надо, чтобы прозы вообще не было, просто очень
    красивый визуал как у NASA или Роскосмоса и никаких текстов». На поверхности блока не
    остаётся ни одного предложения, кроме короткой причины при отказе (не больше восьми слов):
    красная полоса без причины читается как поломка сервиса, а не как честный отказ.

    Ничего не удалено. Популярное объяснение, правило выбора, допуск равнозначности, объявленная
    область вывода, перечень условий и полный текст «почему» стоят раскрытиями В ЭТОМ ЖЕ блоке,
    с внятными заголовками, — эксперты просили экран, который «жюри может потыкать». Критерий О4
    требует, чтобы это было ДОСТУПНО, а не чтобы лежало на первом экране.
    """
    ans = scan_answer(scan)
    kind = ans['kind']
    cand = ans.get('first')
    dur = int(duration_min or scan.get('requested_duration_min') or 0)
    dur_txt = ('%d мин' % dur) if dur else ''
    lines = ['<div class="reco r-%s">' % esc(kind)]
    # --- полоса решения: крупно и только ответ
    cause = ''
    if kind == 'point':
        a, b = _iso_dt(cand.get('start_utc')), _iso_dt(cand.get('end_utc'))
        lines.append(decision_band(kind, 'ВЫХОДИТЬ %s UTC' % _span_ru(a, b)))
    elif kind == 'interval':
        # Промежуток — НОРМАЛЬНЫЙ ответ, а не отсутствие ответа: внутри него начала неразличимы
        # в пределах чувствительности модели, и называть минуту значило бы обещать точность,
        # которой у расчёта нет.
        lines.append(decision_band(kind, 'ВЫХОДИТЬ %s UTC' % _span_ru(ans.get('from'), ans.get('to'))))
    else:
        _k = kind if kind in DECISION_VALUE_RU else 'none'
        cause = DECISION_CAUSE_RU[_k]
        lines.append(decision_band(kind, DECISION_VALUE_RU[_k], cause))
    # --- ряд крупных чисел: сравнение «выбрано против худшего» и есть объяснение, только числами
    if kind in ('point', 'interval') and cand is not None:
        _e = (S.get('thresholds') or {}).get('e_min_MeV') if isinstance(S, dict) else None
        lines.append(panel(scan_stat_rows(scan, cand, _e, dose), cls='stat'))
        if dur_txt:
            # Длительность и границы крайних окон промежутка — ярлыком, а не фразой.
            # Даты крайних окон уже стоят в полосе решения, повторять их незачем: здесь остаётся
            # длительность и время окончания самого позднего окна — то, чего в полосе нет.
            if kind == 'interval':
                _d = timedelta(minutes=dur)
                b2 = _iso_dt((ans.get('last') or {}).get('end_utc'))                     or ((ans.get('to') + _d) if ans.get('to') else None)
                _tail = 'окно %s · работы до %s' % (dur_txt, dt_ru(b2))
            else:
                _tail = 'окно %s' % dur_txt
            lines.append('<div class="searched">%s · %s</div>'
                         % (esc(_tail), esc(scan_searched_short_ru(scan))))
    # --- условия проверки: короткими плашками, полный текст — раскрытием ниже
    _cands_all = scan.get('candidates') or []
    _group = [_cands_all[i] for i in scan_best_indices(scan)] if kind in ('interval', 'tradeoff') else [cand]
    conds, _seen = [], set()
    for _c in _group:
        for _x in ((_c or {}).get('conditions') or []):
            _t = screen_text(_x)
            if _t not in _seen:
                _seen.add(_t)
                conds.append(_t)
    if conds:
        lines.append('<div class="legend">%s</div>'
                     % ''.join(pill(cond_name_ru(c), 'crit') for c in conds[:3]))
    # --- всё остальное: раскрытиями, с ярлыками-заголовками
    _s = lambda x: sentence_ru(screen_text(status_ru(x, pro)))
    more = []
    plain = plain_why_ru(scan, cand, dur) if kind in ('point', 'interval') else ''
    if plain:
        if kind == 'interval':
            plain += (' Внутри промежутка начала неразличимы в пределах чувствительности модели, '
                      'поэтому сервис называет промежуток, а не минуту.')
        more.append(('Почему это окно: обычными словами', plain))
    if kind == 'none':
        # Имя исхода словами не потеряно: оно открывает текст раскрытия. На поверхности вместо
        # него стоит крупная полоса — то же самое, но цветом и формой.
        more.append(('Что нужно, чтобы отказ снялся',
                     '%s. %s' % (SCAN_VERDICT_TITLE['insufficient'],
                                 screen_text(refusal_lift_ru('insufficient', missing_ru, mode)))))
    elif kind == 'check':
        more.append(('Что снимает условие проверки',
                     '%s. %s' % (SCAN_VERDICT_TITLE['all_need_check'],
                                 screen_text(refusal_lift_ru('all_need_check', missing_ru, mode)))))
    elif kind == 'tradeoff':
        more.append(('Почему сервис не выбирает сам',
                     'Спор величин сверх допуска равнозначности: выбор за аналитиком, числа обеих сторон '
                     'стоят в перечне проверенных начал.'))
    scope = scan.get('scope')
    if scope:
        # Объявленная область вывода с поверхности уехала под раскрытие по прямому решению
        # владельца («области вывода — под раскрытия»). Она не потеряна: раскрытие стоит здесь же,
        # заголовок называет её своими словами, и тот же текст целиком лежит в отчёте и в выгрузке.
        more.append(('Область вывода этого ответа', _s(scope)))
    if conds:
        more.append(('Условия проверки целиком: откуда известно', '; '.join(conds)))
    elif cand is not None and str(cand.get('coverage') or '') == 'none':
        # Т6 и находка №4 десятого круга: «условий нет» и «условия не проверены» — разные
        # утверждения, и молчание условий без покрытия ничего не значит.
        more.append(('Почему условий нет: покрытие величин',
                     'Величины, которые различают начала, на это окно не посчитаны: данных нет. '
                     'Отсутствие условия здесь не означает отсутствия воздействия.'))
    elif cand is not None:
        more.append(('Условия проверки', 'Условий проверки %s нет.'
                     % ('у рекомендованного окна' if kind == 'point' else 'у названных начал')))
    why = scan.get('why')
    if why:
        more.append((WHY_MORE_RU, _s(why)))
    rule, tol = scan.get('rule'), scan.get('tolerance_note')
    tail = ' '.join(_s(x)[0].upper() + _s(x)[1:] for x in (rule, tol) if x and str(x).strip())
    if tail:
        more.append((RULE_MORE_RU, tail))
    # Доказательство того, что сервис искал, а не показал две точки, стоит числом в ряду выше;
    # здесь — той же фразой целиком, со сроком поиска (техзадание одиннадцатого круга, раздел 1).
    _searched = screen_text(scan_searched_ru(scan))
    if pro:
        # Профессиональному уровню важно, один кандидат оказался в лучшей группе или несколько:
        # множество равнозначных — повод проверить допуски, а не признак хорошей обстановки.
        _searched += ' В лучшей группе %s из %s перебранных начал.' \
            % (fmt(len(scan.get('best') or [])), fmt(len(scan.get('candidates') or [])))
    more.append(('Сколько начал перебрано и на каком сроке', _searched))
    for summary, body in more:
        lines.append('<details class="vmore"><summary>%s</summary><div class="vm">%s</div></details>'
                     % (esc(summary), esc(body)))
    lines.append('</div>')
    return ''.join(lines)
