"""Export an offline, reproducible A2 data snapshot: python -m vkd.history."""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import datetime
import json
from pathlib import Path

from vkd.sources.registry import iso_utc, utc
from vkd.types import Request
from .bundle import history_snapshot


def json_value(value):
    if isinstance(value, datetime):
        return iso_utc(value)
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f'Unsupported snapshot value: {type(value).__name__}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', required=True, type=utc)
    parser.add_argument('--mode', choices=['history_forecast', 'history_review'], default='history_forecast')
    parser.add_argument('--cutoff', type=utc)
    parser.add_argument('--duration-min', type=int, default=480)
    parser.add_argument('--search-period-min', type=int, default=1440)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.mode == 'history_forecast' and args.cutoff is None:
        parser.error('--cutoff is required for history_forecast')
    request = Request(args.mode, args.start, args.duration_min, args.search_period_min, args.cutoff)
    snapshot = history_snapshot(request)
    payload = json.dumps(snapshot, default=json_value, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding='utf-8')
        print(f'{args.output}: {len(snapshot["samples"])} samples, {len(snapshot["events"])} events, '
              f'{len(snapshot["raw_record_ids"])} source records')
    else:
        print(payload, end='')


if __name__ == '__main__':
    main()
