# -*- coding: utf-8 -*-
"""Прогнозы NOAA, выпущенные ДО отсечки, из архива A1 через отбор A2 (vkd.history.replay).

Три канала на горизонт запроса:
  * kp_forecast      — планетарный Kp по 3-часовым интервалам (3-day forecast);
  * s1_prob_daily    — суточная вероятность S1 и выше, % (3-day forecast);
  * proton_prob_daily — суточная вероятность протонного события, % (daypre).

Ячейки сохраняют исходное разрешение источника (3 ч / сутки): суточная вероятность
НЕ пересчитывается в вероятность за окно (vkd/history/README.md). Здесь только
перевод в EnvironmentSample с происхождением, без интерпретации.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Optional

from vkd.types import EnvironmentSample, Kind

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# (source_id A1, канал A1, наш channel_id, единица, подпись)
CHANNELS = (
    ('noaa_ngdc_3day_forecast', 'noaa_kp', 'kp_forecast', '1', 'прогноз Kp NOAA, 3-часовые интервалы'),
    ('noaa_ngdc_3day_forecast', 's1_or_greater_probability', 's1_prob_daily', '%', 'вероятность S1 и выше за сутки, прогноз NOAA'),
    ('noaa_ngdc_daypre', 'whole_disk_proton_probability', 'proton_prob_daily', '%', 'вероятность протонного события за сутки, прогноз NOAA'),
)
STATUS_RU = {'full': 'полное покрытие горизонта', 'partial': 'частичное покрытие', 'missing': 'нет допустимого выпуска',
             'ambiguous': 'конфликт версий одного выпуска', 'invalid': 'выпуск повреждён или не разобран'}


@dataclass(frozen=True)
class ForecastLine:
    channel_id: str
    source_id: str
    label_ru: str
    status: str                    # full | partial | missing | ambiguous | invalid | unavailable
    coverage_fraction: float
    release_id: Optional[str]
    published_utc: Optional[datetime]
    raw_record_id: Optional[str]
    samples: tuple                 # EnvironmentSample, исходное разрешение
    gaps: tuple
    reason: Optional[str]
    limitations: tuple

    def record_id_ok(self) -> bool:
        return self.release_id is not None and self.published_utc is not None


@lru_cache(maxsize=1)
def _registry():
    from vkd.sources.registry import SourceRegistry
    return SourceRegistry(ROOT)


def _t(s: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(s.replace('Z', '+00:00')) if s else None


def noaa_forecasts(cutoff_utc: datetime, valid_from_utc: datetime, valid_to_utc: datetime) -> tuple[list[ForecastLine], dict]:
    """Возвращает линии по каналам и сырые записи выбранных выпусков (текст бюллетеня для выгрузки)."""
    from vkd.history.replay import replay_forecast
    try:
        reg = _registry()
    except Exception as e:                       # noqa: BLE001 — архив недоступен/повреждён: линии без данных, не нули
        return [ForecastLine(cid, src, lab, 'unavailable', 0.0, None, None, None, (), (), 'архив A1: %s' % e, ())
                for src, _, cid, _, lab in CHANNELS], {}
    lines, raw = [], {}
    for src, ch, cid, unit, label in CHANNELS:
        r = replay_forecast(reg, source_id=src, channel_id=ch, cutoff_utc=cutoff_utc,
                            valid_from_utc=valid_from_utc, valid_to_utc=valid_to_utc)
        rec = r['record']
        samples = []
        if rec:
            fetched = _t(rec['fetched_utc'])
            for c in r['cells']:
                samples.append(EnvironmentSample(
                    t_utc=_t(c['valid_from_utc']), channel_id=cid, value=float(c['value']), unit=unit, source_id=src,
                    kind=Kind.EXTERNAL_FORECAST, published_utc=_t(c['published_utc']),
                    valid_from_utc=_t(c['valid_from_utc']), valid_to_utc=_t(c['valid_to_utc']), fetched_utc=fetched,
                    quality='model', raw_record_id=rec['raw_record_id'], version=rec.get('version')))
            if rec['raw_record_id'] not in raw:
                try:
                    text = reg.raw_bytes(rec['raw_record_id']).decode('utf-8', 'replace')
                except Exception as e:           # noqa: BLE001
                    text = 'не прочитан: %s' % e
                raw[rec['raw_record_id']] = {'source_id': src, 'release_id': rec['release_id'], 'url': rec.get('url'),
                                             'published_utc': rec['published_utc'], 'sha256': rec['sha256'],
                                             'strict_replay_eligibility': rec.get('strict_replay_eligibility'),
                                             'limitations': rec.get('limitations'), 'text': text}
        lines.append(ForecastLine(cid, src, label, r['status'], float(r['coverage_fraction']),
                                  rec['release_id'] if rec else None, _t(rec['published_utc']) if rec else None,
                                  rec['raw_record_id'] if rec else None, tuple(samples),
                                  tuple((g['valid_from_utc'], g['valid_to_utc']) for g in r['gaps']),
                                  r['reason'], tuple(r.get('limitations', ()))))
    return lines, raw


def in_window(samples, start_utc: datetime, duration_min: int):
    """Ячейки, пересекающие окно [start, start+duration)."""
    end = start_utc + timedelta(minutes=duration_min)
    return [s for s in samples if s.valid_from_utc < end and s.valid_to_utc > start_utc]


def covered_fraction(samples, start_utc: datetime, duration_min: int) -> float:
    end = start_utc + timedelta(minutes=duration_min)
    total = (end - start_utc).total_seconds()
    cov = 0.0
    for s in in_window(samples, start_utc, duration_min):
        cov += (min(end, s.valid_to_utc) - max(start_utc, s.valid_from_utc)).total_seconds()
    return min(1.0, cov / total) if total > 0 else 0.0
