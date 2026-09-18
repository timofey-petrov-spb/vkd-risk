# -*- coding: utf-8 -*-
"""Мост к орбите A3 (vkd.orbit) для конвейера Б.

Что делает и чего не делает:
  * текущий режим — свежий TLE от слоя источников кладётся в отдельный корень
    `data/cache/orbit_root/<sha12>/` вместе с манифестом и коэффициентами IGRF,
    и модуль А проверяет байты снимка так же, как свой собственный
    (vkd/orbit/README.md: «снимок ограничен возрастом, обновление входит в A4»);
  * история — сначала строгий отбор OEM по доказанной публикации до отсечки;
    если он не проходит (у нынешних 26 OEM публичная доступность в 2024 не
    доказана — data/source_registry_2024/nasa_oem/README.md), берётся OEM,
    созданный и изменённый в S3 не позднее отсечки, и это ОБЪЯВЛЯЕТСЯ как
    реконструкция с недоказанной доступностью. Это решение Б, записано в
    журнале для проверки А; не молчаливая подмена;
  * ошибка орбиты никогда не превращается в разрешение использовать заглушку:
    возвращается статус «недоступна», и оценка обязана показать отсутствие покрытия.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from vkd.orbit import OrbitDataError, trajectory_with_provenance
from vkd.types import TrajectoryMeta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_ROOT = os.path.join(ROOT, 'data', 'cache', 'orbit_root')
ORBIT_SRC = ('vkd.orbit (A3): SGP4/WGS72 по TLE в текущем режиме, NASA/JSC OEM 2024 в истории; '
             'IGRF-13 для 2024 и IGRF-14 сейчас; L, B/B0 и жёсткость — центральный наклонённый диполь (приближение)')


@dataclass(frozen=True)
class OrbitResult:
    meta: Optional[TrajectoryMeta]
    points: list
    provenance: dict
    status_ru: str
    strictness: str            # 'strict' | 'declared_reconstruction' | 'unavailable'
    error: Optional[str]


def _iso(t: Optional[datetime]) -> Optional[str]:
    return t.isoformat().replace('+00:00', 'Z') if t else None


def stage_live_root(tle_text: str, fetched_utc: Optional[datetime], available_utc: Optional[datetime],
                    url: str, evidence: str) -> str:
    """Корень репозитория для vkd.orbit с этим TLE: manifest.json (SHA-256, размер,
    получение, доступность) + копии коэффициентов IGRF из data/orbit с проверкой хешей.
    Каталог на хеш — параллельные сессии не перезаписывают друг друга."""
    raw = tle_text.strip().encode('ascii') + b'\n'
    sha = hashlib.sha256(raw).hexdigest()
    root = os.path.join(CACHE_ROOT, sha[:12])
    d = os.path.join(root, 'data', 'orbit')
    os.makedirs(d, exist_ok=True)
    src = os.path.join(ROOT, 'data', 'orbit')
    with open(os.path.join(src, 'manifest.json'), encoding='utf-8') as fh:
        manifest = json.load(fh)
    records = [r for r in manifest['records'] if r['file'] != 'iss.tle']
    for r in records:
        dst = os.path.join(d, r['file'])
        if not os.path.exists(dst) or hashlib.sha256(open(dst, 'rb').read()).hexdigest() != r['sha256']:
            shutil.copyfile(os.path.join(src, r['file']), dst)
    with open(os.path.join(d, 'iss.tle'), 'wb') as fh:
        fh.write(raw)
    records.append({'file': 'iss.tle', 'sha256': sha, 'bytes': len(raw), 'url': url,
                    'fetched_utc': _iso(fetched_utc), 'available_utc': _iso(available_utc), 'evidence': evidence,
                    'raw_path': 'data/orbit/iss.tle', 'source_id': 'celestrak_gp',
                    'raw_record_id': 'celestrak_gp:25544:' + sha[:12], 'release_id': sha})
    with open(os.path.join(d, 'manifest.json'), 'w', encoding='utf-8') as fh:
        json.dump({'schema_version': 1, 'records': records, 'staged_by': 'vkd.integration.orbit_bridge'}, fh, indent=1)
    return root


def build_orbit(mode: str, t0: datetime, minutes: int, saa_B_threshold_nT: float, *,
                tle_text: Optional[str] = None, tle_fetched_utc: Optional[datetime] = None,
                tle_available_utc: Optional[datetime] = None, tle_url: str = '', tle_evidence: str = '',
                max_tle_age_days: float = 3.0, cutoff_utc: Optional[datetime] = None) -> OrbitResult:
    if mode == 'live':
        if not tle_text:
            return OrbitResult(None, [], {'errors': ['TLE не получен']}, 'орбита недоступна: TLE не получен ни живым '
                               'запросом, ни из кеша', 'unavailable', 'TLE не получен')
        root = stage_live_root(tle_text, tle_fetched_utc, tle_available_utc, tle_url, tle_evidence)
        try:
            meta, pts, prov = trajectory_with_provenance(t0, minutes, saa_B_threshold_nT, mode='live',
                                                         repo_root=root, max_tle_age_days=max_tle_age_days)
        except OrbitDataError as e:
            return OrbitResult(None, [], {'errors': [str(e)]}, 'орбита недоступна: %s' % e, 'unavailable', str(e))
        prov['staged_root'] = root
        return OrbitResult(meta, pts, prov, 'SGP4 по TLE CelesTrak, эпоха %s; предел возраста %.0f сут'
                           % (meta.epoch_utc.strftime('%Y-%m-%d %H:%MZ'), max_tle_age_days), 'strict', None)

    errors = []
    cutoff = cutoff_utc or t0
    if mode == 'history_forecast':
        try:
            meta, pts, prov = trajectory_with_provenance(t0, minutes, saa_B_threshold_nT,
                                                         mode='history_forecast', cutoff_utc=cutoff)
            return OrbitResult(meta, pts, prov, 'OEM NASA/JSC с доказанной публикацией до отсечки', 'strict', None)
        except OrbitDataError as e:
            errors.append(str(e))
    try:
        meta, pts, prov = trajectory_with_provenance(t0, minutes, saa_B_threshold_nT,
                                                     mode='history_review', cutoff_utc=cutoff)
    except OrbitDataError as e:
        errors.append(str(e))
        return OrbitResult(None, [], {'errors': errors}, 'орбита недоступна: ' + '; '.join(errors), 'unavailable', '; '.join(errors))
    prov['strict_attempt_error'] = errors[0] if errors else None
    if mode == 'history_forecast':
        prov['limitations'].append(
            'Строгий отбор OEM по доказанной публикации не прошёл; взят OEM, созданный (CREATION_DATE) и изменённый '
            'в S3 не позднее отсечки. Это объявленная реконструкция: публичная доступность этой версии в тот момент '
            'не доказана (реестр A1). Решение Б, зафиксировано в журнале.')
        status = ('OEM NASA/JSC, созданный %s — до отсечки; доступность в тот момент не доказана (реконструкция)'
                  % meta.created_utc.strftime('%Y-%m-%d %H:%MZ'))
        return OrbitResult(meta, pts, prov, status, 'declared_reconstruction', None)
    return OrbitResult(meta, pts, prov, 'OEM NASA/JSC, созданный %s (реконструкция)'
                       % meta.created_utc.strftime('%Y-%m-%d %H:%MZ'), 'declared_reconstruction', None)


def provenance_summary(prov: dict) -> dict:
    """Компактная часть отчёта происхождения для снимка: идентификаторы, пределы, сегменты.
    Полные записи уходят в сырые записи выгрузки."""
    recs = prov.get('records', {}) or {}
    return {
        'algorithm_version': prov.get('algorithm_version'), 'mode': prov.get('mode'),
        'cutoff_utc': prov.get('cutoff_utc'), 'step_seconds': prov.get('step_seconds'),
        'records': {rid: {k: r.get(k) for k in ('source_id', 'release_id', 'sha256', 'bytes', 'url', 'created_utc',
                                                 'published_utc', 'available_utc', 'fetched_utc', 'epoch_utc',
                                                 'strict_replay_eligibility', 'model', 'distribution')
                          if k in r} for rid, r in recs.items()},
        'segments': prov.get('segments', []), 'limitations': prov.get('limitations', []),
        'earth_orientation': prov.get('earth_orientation'), 'max_tle_age_days': prov.get('max_tle_age_days'),
        'strict_attempt_error': prov.get('strict_attempt_error'), 'errors': prov.get('errors'),
    }
