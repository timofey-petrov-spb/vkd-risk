# -*- coding: utf-8 -*-
"""ВКД-Риск — веб-сервис для аналитика ГОГУ. Только интерфейс: запрос → снимок
расчёта из app.compute.run → рендер. Экран и выгрузка строятся из одного снимка.

Один экран сверху вниз: запрос → траектория → картина по времени → сравнение
окон → рекомендация → предупреждения по семи пунктам → нормы (контекст) →
данные и выгрузка. Два уровня: «Оперативный» и «Профессиональный».
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import plotly.graph_objects as go
import streamlit as st

from app.compute import ALGO_VERSION, HIST_SRC, ORBIT_SRC, SRC_LAYER, goes_latest, kp_latest, run, tle_latest
from app.export import build_zip
from app.norms import norms_rows, s_level
from app.viz import ground_track, window_bars
from vkd.explain.cards import KIND_RU
from vkd.types import Kind
from vkd.windows.compare import Thresholds
from vkd.windows.scenario import Scenario

KIND_ICON = {Kind.OBSERVATION: '🟢 наблюдение', Kind.EXTERNAL_FORECAST: '🟠 внешний прогноз', Kind.OWN_CALCULATION: '🔵 наш расчёт'}
VERDICT_RU = {'preferred': 'ПРЕДПОЧТИТЕЛЬНО', 'trade_off': 'КОМПРОМИСС', 'equivalent': 'РАВНОЗНАЧНЫ',
              'insufficient': 'ОСНОВАНИЙ НЕДОСТАТОЧНО', 'all_need_check': 'ВСЕ ОКНА ТРЕБУЮТ ПРОВЕРКИ'}
MODE_IDS = {'Текущая обстановка': 'live', 'Исторический разбор': 'history_review', 'Прогноз из прошлого': 'history_forecast'}

st.set_page_config(page_title='ВКД-Риск', page_icon='🛰', layout='wide')
st.title('ВКД-Риск: внешняя обстановка на траектории МКС и выбор окна ВКД')
st.caption('Исследовательский прототип поддержки решений для аналитика ГОГУ. '
           'Допуск к реальной ВКД остаётся за уполномоченными специалистами. Все времена UTC.')

# ================================================================= запрос
with st.sidebar:
    level = st.radio('Уровень интерфейса', ['Оперативный', 'Профессиональный'], index=1, horizontal=True)
    pro = level == 'Профессиональный'
    st.header('Запрос')
    mode_ru = st.radio('Режим', list(MODE_IDS), index=0)
    mode = MODE_IDS[mode_ru]
    if mode == 'live':
        t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        st.write('Начало периода поиска: **%s**' % t0.strftime('%Y-%m-%d %H:%M UTC'))
    else:
        d = st.date_input('Дата, 1 мая — 30 июня 2024', value=datetime(2024, 5, 10).date(),
                          min_value=datetime(2024, 5, 1).date(), max_value=datetime(2024, 6, 30).date())
        hh = st.slider('Час начала периода, UTC', 0, 23, 12)
        t0 = datetime(d.year, d.month, d.day, hh, tzinfo=timezone.utc)
        if mode == 'history_forecast':
            st.write('Отсечка публикации: **%s**' % t0.strftime('%Y-%m-%d %H:%M UTC'))
        st.warning('Исторические источники подключены частично: события DONKI с временем публикации есть, '
                   'архива GOES за 2024 нет, орбита — реконструкция по текущему TLE до подключения OEM.')
    duration_min = st.slider('Длительность ВКД, мин', 60, 480, 360, step=30)
    search_min = st.slider('Период поиска начала, мин', 0, 1440, 720, step=60)
    n_windows = st.slider('Окон для сравнения', 2, 3, 2)
    offsets = [st.slider('Сдвиг начала окна %d, мин' % (i + 1), 0, search_min, min(i * 240, search_min), step=30, key='w%d' % i)
               for i in range(n_windows)]
    st.header('Источники')
    disabled = {s: st.checkbox('Отключить %s' % s, key='dis_' + s) for s in ('goes', 'kp')}
    if st.button('Обновить данные сейчас'):
        st.cache_data.clear()
    auto_min = st.select_slider('Автообновление в текущем режиме, мин', [0, 5, 10, 15], value=5,
                                help='0 — выключено. Частота публикации: GOES 5 мин, Kp 3 ч, TLE по мере выпуска.')
    with st.expander('Что если — стресс-сценарий', expanded=False):
        sc_delay = st.slider('Задержка начала работ, мин', 0, 180, 0, step=15)
        sc_sep_on = st.checkbox('Смоделировать протонное событие')
        sc_sep_off = st.slider('… начало через, мин после t0', 0, 1440, 120, step=30, disabled=not sc_sep_on)
        sc_sep_pfu = st.select_slider('… уровень, pfu ≥10 МэВ', [10.0, 100.0, 1000.0, 10000.0], value=100.0, disabled=not sc_sep_on)
        sc_kp_on = st.checkbox('Смоделировать скачок Kp')
        sc_kp = st.slider('… Kp', 0.0, 9.0, 7.0, step=0.33, disabled=not sc_kp_on)
    scenario = Scenario('ui', work_delay_min=sc_delay, sep_onset_offset_min=(sc_sep_off if sc_sep_on else None),
                        sep_level_pfu=(sc_sep_pfu if sc_sep_on else None), kp_override=(sc_kp if sc_kp_on else None))
    if pro:
        st.header('Пороги и настройки')
        th = Thresholds(
            saa_B_threshold_nT=st.number_input('Порог аномалии |B|, нТл', 18000.0, 30000.0, 24000.0, 500.0),
            e_min_MeV=st.selectbox('Канал захваченных протонов, МэВ от', [12.5, 30.0, 50.0], index=1),
            goes_max_age_min=st.number_input('Свежесть GOES для будущих участков, мин', 10.0, 360.0, 60.0, 10.0),
        )
        T_months = st.slider('Длительность экспедиции для норм, мес', 1, 12, 6)
    else:
        th, T_months = Thresholds(), 6

# автообновление: фрагмент перезапускает расчёт с очисткой кеша по таймеру (Т1: «данные обновляются
# автоматически с учётом частоты публикации»; кнопка выше — принудительное обновление).
# Стоит ПОСЛЕ боковой панели: раньше блок был внутри неё и прятал панели сценария и порогов
# под условие текущего режима — исторические режимы падали (найдено проверкой README).
if mode == 'live' and auto_min:
    @st.fragment(run_every=timedelta(minutes=auto_min))
    def _auto_refresh():
        last = st.session_state.get('_auto_last')
        nowr = datetime.now(timezone.utc)
        if last is not None and (nowr - last) >= timedelta(minutes=auto_min) - timedelta(seconds=5):
            st.cache_data.clear()
            st.session_state['_auto_last'] = nowr
            st.rerun()
        if last is None:
            st.session_state['_auto_last'] = nowr
        st.sidebar.caption('Автообновление каждые %d мин; последнее: %s UTC' % (auto_min, st.session_state['_auto_last'].strftime('%H:%M:%S')))
    _auto_refresh()


# ================================================================= расчёт: один снимок на рендер
@st.cache_data(ttl=300, show_spinner=False)
def _fetch_all(dis_goes: bool, dis_kp: bool):
    """Кеш 5 мин против повторных запросов при каждом движении ползунка.
    Не автоматическое обновление: обновление — кнопкой или перезапуском (Codex п. 14)."""
    return goes_latest(disabled=dis_goes), kp_latest(disabled=dis_kp), tle_latest(disabled=False)

with st.spinner('Источники: GOES, Kp, TLE — до 8 с на каждый при живом запросе…'):
    fetched = _fetch_all(disabled['goes'], disabled['kp'])
with st.spinner('Траектория, поле, оценка окон, устойчивость…'):
    R = run(mode, t0, duration_min, search_min, offsets, disabled=disabled, thresholds=th,
            scenario=scenario, T_months=T_months, fetched=fetched)
S, rec, meta, traj, rob = R.S, R.rec, R.meta, R.traj, R.rob
now = datetime.fromisoformat(S['computed_utc'])
windows = [a.window for a in R.assessments]
horizon_min = search_min + duration_min

# ================================================================= траектория
c1, c2, c3, c4 = st.columns(4)
c1.metric('Источник орбиты', meta.source_id)
c2.metric('Эпоха элементов', meta.epoch_utc.strftime('%m-%d %H:%MZ') if meta.epoch_utc else '—')
c3.metric('Давность, ч', '%.1f' % S['sources']['celestrak_gp']['age_h'] if S['sources']['celestrak_gp']['age_h'] is not None else '—')
c4.metric('Метод', meta.method + (' · реконструкция' if meta.is_reconstruction else ''))
if pro:
    st.caption('Магнитные координаты: %s. Поле: %s. Шаг 1 мин, траектория %d ч (период поиска + длительность).'
               % (ORBIT_SRC, meta.field_model, horizon_min // 60))

# ================================================================= баннеры режима и сценария
if S['is_simulated']:
    st.warning('**Моделируемый сценарий «Что если».** %s Результат и выгрузка помечены как сценарий; синтетические '
               'значения не попадают в кеш и в строгий исторический режим.' % ' '.join(
                   ([f'Задержка работ {sc_delay} мин — изменение плана, не улучшение условий.'] if sc_delay else []) +
                   ([f'Протонное событие {sc_sep_pfu:g} pfu через {sc_sep_off} мин.'] if sc_sep_on else []) +
                   ([f'Kp = {sc_kp:.1f}.'] if sc_kp_on else [])))
if mode == 'history_forecast':
    st.info('**Строгий прогноз из прошлого.** Отсечка %s: использованы только записи, опубликованные до неё; '
            'исключено %d записей (список в блоке «Данные»). Орбита — реконструкция по текущему TLE до подключения OEM.'
            % (t0.strftime('%Y-%m-%d %H:%MZ'), len(R.excluded)))
elif mode == 'history_review':
    st.info('**Исторический разбор** по всему доступному сегодня архиву, без отсечки. Не является проверяемым прогнозом.')


def show_verdict():
    box = st.success if rec.verdict == 'preferred' else (st.info if rec.verdict == 'equivalent' else st.warning)
    box('**%s** — правило: %s%s' % (VERDICT_RU[rec.verdict], rec.rule_applied,
                                     ' — окно с началом **%s**' % rec.preferred.start_utc.strftime('%Y-%m-%d %H:%MZ') if rec.preferred else ''))
    if rec.reasons:
        st.write('Что повлияло: ' + '; '.join(rec.reasons))
    if rec.missing:
        st.error('Чего не хватает: ' + '; '.join(rec.missing))
    st.caption('Охват: учтено — %s. Не учтено — %s.' % (', '.join(S['coverage_declared']), ', '.join(S['coverage_missing'])))

if not pro:
    st.subheader('Рекомендация')
    show_verdict()

# ================================================================= наблюдения за последние дни (текущий режим)
if mode == 'live':
    from app.obs import observations_panel
    obs_fig = observations_panel(R.fetch_status['goes'].raw_path, R.fetch_status['kp'].raw_path, t0)
    if obs_fig is not None:
        st.subheader('Наблюдения за последние дни')
        st.plotly_chart(obs_fig, use_container_width=True)
        st.caption('Наблюдения источников, не расчёт. Пороги — шкалы NOAA S и G; GOES меряет на геостационарной '
                   'орбите и переносится на станцию только через геомагнитное обрезание.')

# ================================================================= картина по времени
st.subheader('Картина по времени')
_ms = lambda t: int(t.timestamp() * 1000)          # add_vline на оси дат принимает миллисекунды эпохи
fig = go.Figure()
ts_ = [p.t_utc for p in traj]
fig.add_trace(go.Scatter(x=ts_, y=[p.B_nT for p in traj], name='|B|, нТл — наш расчёт по IGRF', line=dict(width=1, color='#1f4e79')))
fig.add_hline(y=th.saa_B_threshold_nT, line_dash='dot', line_color='crimson', annotation_text='порог аномалии %.0f нТл' % th.saa_B_threshold_nT)
in_saa, seg_start = False, None
for p in traj + [None]:
    flag = bool(p.in_saa) if p else False
    if flag and not in_saa:
        seg_start = p.t_utc
    if in_saa and not flag:
        fig.add_vrect(x0=seg_start, x1=(p.t_utc if p else ts_[-1]), fillcolor='crimson', opacity=0.15, line_width=0)
    in_saa = flag
for i, w in enumerate(windows):
    fig.add_vrect(x0=w.start_utc, x1=w.start_utc + timedelta(minutes=w.duration_min), fillcolor='steelblue', opacity=0.12,
                  line_width=1, annotation_text='окно %d' % (i + 1), annotation_position='top left')
fig.add_vline(x=_ms(t0 + timedelta(hours=24)), line_dash='dash', line_color='gray', annotation_text='период начала 24 ч')
if R.goes:
    fig.add_vline(x=_ms(R.goes.t_utc), line_dash='dot', line_color='green', annotation_text='GOES %.2g pfu, %s' % (R.goes.value, s_level(R.goes.value)))
if R.kp:
    fig.add_vline(x=_ms(R.kp.t_utc), line_dash='dot', line_color='orange', annotation_text='Kp %.1f (%s)' % (R.kp.value, R.kp.quality), annotation_position='bottom right')
for e in R.events:
    a0 = e.valid_from_utc or e.start_utc
    if a0 and t0 - timedelta(hours=6) <= a0 <= t0 + timedelta(minutes=horizon_min):
        fig.add_vline(x=_ms(a0), line_dash='dashdot', line_color='purple' if not e.is_simulated else 'magenta',
                      annotation_text=('сцен. ' if e.is_simulated else '') + e.kind_of_event, annotation_position='bottom left')
fig.update_layout(height=360, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation='h'))
st.plotly_chart(fig, use_container_width=True)
st.caption('Красное — пролёты аномалии (наш расчёт). Синее — окна-кандидаты. Серая линия — конец периода начала (24 ч); '
           'траектория и пригодные прогнозы ведутся до конца последнего окна. Фиолетовое — события с временем публикации.')

# ================================================================= карта трассы
with st.expander('Карта трассы и область аномалии', expanded=pro):
    with st.spinner('Область аномалии по IGRF на сетке 4°…'):
        st.plotly_chart(ground_track(traj, windows, th.saa_B_threshold_nT, t0), use_container_width=True)
    st.caption('Область аномалии — наш расчёт |B| по IGRF на средней высоте трассы; трасса за весь горизонт серым, '
               'окна-кандидаты цветом, точки трассы в аномалии красным. Глобус не обязателен по постановке; карта '
               'показывает, откуда берутся минуты в аномалии.')

# ================================================================= сравнение
st.subheader('Сравнение окон')
st.plotly_chart(window_bars(R.assessments), use_container_width=True)
rows = []
for i, a in enumerate(R.assessments):
    r = {'окно': '%d: %s' % (i + 1, a.window.start_utc.strftime('%m-%d %H:%MZ'))}
    for m in a.mechanisms:
        for f in m.factors:
            r[f.name] = ('%.3g' % f.value if f.value is not None else '—') + ' ' + f.unit
        r['покрытие ' + m.mechanism_id] = m.coverage.value
        if m.needs_check:
            r['условие ' + m.mechanism_id] = '; '.join(m.needs_check_reasons)
    rows.append(r)
st.dataframe(rows, use_container_width=True)
if pro:
    st.subheader('Рекомендация')
    show_verdict()
    st.caption('Допуск равнозначности: ' + rec.tolerance_basis)
    with st.expander('Устойчивость вердикта на сетке порогов (О7)', expanded=not rob.stable):
        st.write('Порог аномалии × канал захваченных протонов → предпочтительное окно. '
                 + ('**Выбор устойчив**: одно и то же окно (или одинаковый отказ) на всей сетке.' if rob.stable
                    else '**Выбор меняется** на сетке — рекомендация чувствительна к настройке, показано честно.'))
        st.dataframe([{'порог |B|, нТл': '%.0f' % k[0], 'канал, МэВ от': '%g' % k[1], 'предпочтительное окно': v or 'отказ / равнозначны'}
                      for k, v in rob.preferred_starts.items()], use_container_width=True)
    if R.events:
        with st.expander('События, учтённые в окне (уведомления с временем публикации)', expanded=False):
            st.dataframe([{'событие': e.event_id, 'тип': e.kind_of_event, 'происхождение': KIND_RU[e.kind],
                           'начало': e.start_utc.strftime('%m-%d %H:%MZ') if e.start_utc else '—',
                           'действие с': e.valid_from_utc.strftime('%m-%d %H:%MZ') if e.valid_from_utc else '—',
                           'публикация': e.published_utc.strftime('%m-%d %H:%MZ') if e.published_utc else 'нет — синтетика',
                           'сценарий': 'да' if e.is_simulated else ''} for e in R.events], use_container_width=True)
    st.caption(S['policy_note'])

# ================================================================= предупреждения по семи пунктам
st.subheader('Предупреждения и доказательства' + ('' if pro else ' — условия и основные величины'))
cards = R.cards
shown = cards if pro else [c for c in cards if c.severity != 'info'][:3] + [c for c in cards if c.severity == 'info'][:3]
for c in shown:
    label = ('🔴 ' if c.severity == 'critical' else '🟠 ' if c.severity == 'limiting' else '') + c.title + '  ·  ' + KIND_ICON[c.kind]
    with st.expander(label, expanded=(c.severity != 'info')):
        st.markdown('**1. Воздействие и значение для ВКД.** ' + c.impact_ru)
        st.markdown('**2. Период.** ' + c.period_ru)
        st.markdown('**3. Данные и единицы.** ' + c.data_ru)
        st.markdown('**4. Источник и время публикации.** ' + c.source_ru)
        st.markdown('**5. Применённое правило или модель.** ' + c.rule_ru)
        st.markdown('**6. Ограничения и уверенность.** ' + c.limits_ru)
        st.markdown('**7. Происхождение.** ' + KIND_RU[c.kind])
        if pro:
            for rid in c.record_ids:
                if rid in R.raw_records:
                    st.json(R.raw_records[rid], expanded=False)

# ================================================================= нормы — контекст
if pro:
    st.subheader('Нормы — справочный контекст, не вердикт по дозе')
    st.dataframe(norms_rows(T_months), use_container_width=True)
    st.caption('Сервис не вычисляет дозу человека: для этого нужны модели защиты и ткани, которых в обязательной '
               'части нет. Нормы показаны, чтобы аналитик соотносил показатели среды с действующими пределами; '
               '«500 мЗв/год» — по вторичным источникам до проверки первичного текста.')

# ================================================================= данные и выгрузка
st.subheader('Данные и выгрузка')
if pro:
    st.dataframe([{'источник': k, **v} for k, v in S['sources'].items()], use_container_width=True)
    if mode != 'live':
        st.caption('Поставщик истории: %s' % HIST_SRC)
        with st.expander('Исключено отсечкой: %d записей' % len(R.excluded), expanded=False):
            st.write('\n'.join('- ' + x for x in R.excluded[:200]) + ('\n- …' if len(R.excluded) > 200 else ''))
c1, c2 = st.columns(2)
c1.download_button('Скачать расчёт (JSON)', json.dumps(S, ensure_ascii=False, indent=1, default=str),
                   file_name='vkd_risk_%s.json' % now.strftime('%Y%m%dT%H%M'), mime='application/json')
c2.download_button('Скачать архив: отчёт, запрос, факторы, сырые записи (ZIP)', build_zip(S, R.raw_records),
                   file_name='vkd_risk_%s.zip' % now.strftime('%Y%m%dT%H%M'), mime='application/zip')
st.caption('Экран и выгрузка построены из одного снимка расчёта; версия алгоритма %s; слои: %s.' % (ALGO_VERSION, SRC_LAYER))
