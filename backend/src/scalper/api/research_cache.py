"""Read-only inventory of retained long-range candle cache files."""
import re
from typing import Any
from scalper.api.research_jobs import CACHE


def inventory() -> dict[str, Any]:
    coins: dict[str, dict[str, Any]] = {}
    for path in CACHE.glob('*.json'):
        match = re.fullmatch(r'([A-Z0-9]{2,25}USDT)-(\d+)-(\d+)\.json', path.name)
        if not match:
            continue
        symbol, begin, end = match.groups()
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            continue
        row = coins.setdefault(symbol, {'symbol': symbol, 'files': 0, 'bytes': 0,
                                        'first': int(begin), 'last': int(end)})
        row['files'] += 1
        row['bytes'] += size
        row['first'] = min(row['first'], int(begin))
        row['last'] = max(row['last'], int(end))
    return {'coins': sorted(coins.values(), key=lambda row: row['symbol']),
            'files': sum(row['files'] for row in coins.values()),
            'bytes': sum(row['bytes'] for row in coins.values()),
            'retention': 'Kept until manually removed; matching cached days are reused. Date spans may contain gaps.'}
