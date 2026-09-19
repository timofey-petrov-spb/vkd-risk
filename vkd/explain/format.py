# -*- coding: utf-8 -*-
"""Русская запись чисел для объяснений и текстов правила: запятая, ·10^n, единица после числа.
Тот же формат, что у экрана (app/ui.fmt), но без зависимости ядра от интерфейса."""
from __future__ import annotations


def fmt_ru(v, unit: str = '') -> str:
    if v is None:
        return '—'
    if isinstance(v, float):
        if v == 0:
            s = '0'
        elif abs(v) >= 1e5 or abs(v) < 1e-2:
            m, e = ('%.2e' % v).split('e')
            s = '%s·10^%d' % (m.replace('.', ','), int(e))
        elif abs(v) >= 100:
            s = '%.0f' % v
        else:
            s = ('%.2f' % v).rstrip('0').rstrip('.').replace('.', ',')
    else:
        s = str(v)
    return s + (' ' + unit if unit else '')


# Имена источников по-русски: на оперативном уровне не должно быть идентификаторов кода (О5).
SOURCE_ID_RU = {
    'nasa_donki_notification': 'уведомление NASA DONKI',
    'nasa_donki_sep_card': 'карточка протонного события NASA DONKI',
    'nasa_donki_wsa_enlil': 'прогон модели WSA-ENLIL (NASA DONKI)',
    'nasa_donki_gst': 'карточка геомагнитной бури NASA DONKI',
    'nasa_iswa_goes_primary_p5m': 'GOES ≥10 МэВ, архив наблюдений NASA iSWA (5-минутные средние)',
    'nasa_iswa_goes_primary_p5m_schema': 'описание формата архива NASA iSWA',
    'noaa_swpc_goes': 'GOES ≥10 МэВ (NOAA SWPC)',
    'noaa_swpc_3day_forecast': 'трёхсуточный прогноз NOAA SWPC (живой бюллетень)',
    'noaa_ngdc_3day_forecast': 'трёхсуточный прогноз NOAA SWPC',
    'noaa_ngdc_daypre': 'суточный прогноз протонного события NOAA SWPC',
    'gfz_kp': 'Kp (GFZ)',
    'gfz_kp_archive': 'Kp, окончательный ряд GFZ',
    'scenario': 'сценарий «что если»',
}
# Тип события по-русски: в идентификаторе записи A2 он стоит последним полем
EVENT_KIND_RU = {'SEP': 'протонное событие', 'GST': 'геомагнитная буря', 'CME_ARRIVAL': 'приход выброса',
                 'FLR': 'вспышка', 'IPS': 'межпланетная ударная волна', 'MPC': 'магнитопауза', 'RBE': 'электроны пояса'}

# Начало строки каждого сигнала о буре. Условие «буря в окне» сводит в одно до четырёх разных
# сигналов (наблюдение Kp, прогноз NOAA, уведомление DONKI о буре, опубликованный прогноз прихода
# выброса), и ограничения в карточке должны относиться только к тем сигналам, которые в условии
# действительно есть: у прогноза прихода выброса никакого наблюдения нет. Чтобы метка не разошлась
# с текстом, строки собирает compare.py по этим же константам, а разбирает cards.py.
STORM_SIGNAL_RU = {
    'kp_obs': 'наблюдение Kp',
    'noaa_kp_forecast': 'прогноз NOAA: Kp',
    'donki_storm': 'уведомление DONKI о буре',
    'cme_arrival': 'опубликованный прогноз прихода выброса',
}


def storm_signal_kinds(sources_ru) -> set:
    """Какие сигналы о буре сведены в условие. Нераспознанная строка не пропадает молча:
    она даёт метку 'unknown', и карточка тогда не объявляет ограничения, которого не может
    обосновать, а печатает общую оговорку."""
    out = set()
    for s in sources_ru or ():
        hit = {k for k, mark in STORM_SIGNAL_RU.items() if mark in s}
        out |= (hit or {'unknown'})
    return out


def source_ru(source_id: str) -> str:
    return SOURCE_ID_RU.get(source_id, source_id)


def record_ru(rid: str) -> str:
    """Идентификатор записи по-русски: «уведомление NASA DONKI 20240510-AL-004, протонное событие».

    Формат записи A1/A2 — source_id:release_id:хеш[:тип события]. На экране идентификаторов
    кода быть не должно (О5), но запись обязана оставаться находимой: печатаются имя источника
    и НОМЕР ВЫПУСКА источника, а не внутренний ключ с хешем.
    """
    parts = (rid or '').split(':')
    if len(parts) < 2 or parts[0] not in SOURCE_ID_RU:
        return 'запись %s' % rid
    out = '%s %s' % (SOURCE_ID_RU[parts[0]], parts[1])
    if len(parts) >= 4 and parts[3] in EVENT_KIND_RU:
        out += ', ' + EVENT_KIND_RU[parts[3]]
    return out
