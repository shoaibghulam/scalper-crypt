"""Scanner thresholds.

Every number here came from research rather than taste, and the two that carry
the most weight are the ATR floor and the spread ceiling.

The ATR floor is the fee filter in disguise. Round-trip cost is fixed by the
fee schedule, so the only way to keep cost small relative to the stop is to
refuse symbols whose stop would be too tight. At 0.30 percent ATR a 1.5x ATR
stop is 0.45 percent, giving a cost-to-stop ratio near 0.24. At 0.20 percent
the ratio reaches 0.37 and the break-even win rate at 2R climbs from 41 to 46
percent, which no indicator tweak recovers.

The spread ceiling is the other half. Freqtrade ships a default spread filter
of 500 basis points, which is 100 times looser than a scalper can survive.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Regime(StrEnum):
    TREND = "trend"
    RANGE = "range"
    NO_TRADE = "no_trade"


@dataclass(frozen=True)
class ScanConfig:
    # Stage 1, liquidity. Retail guides quote 10 million, which is far too
    # permissive for leveraged perpetual scalping.
    min_quote_volume_24h: float = 75_000_000.0
    top_n_by_volume: int = 0  # zero means all eligible symbols
    preferred_spread_bps: float = 2.0
    max_spread_bps: float = 5.0

    # Stage 2, volatility regime.
    min_atr_pct: float = 0.30
    preferred_atr_pct_low: float = 0.35
    preferred_atr_pct_high: float = 1.50
    max_atr_pct: float = 2.50
    atr_percentile_low: float = 40.0
    atr_percentile_high: float = 90.0

    # Stage 3, activity and regime classification. The band between the
    # trending and ranging thresholds is deliberately no-trade rather than
    # assigned to one side: an ambiguous regime is the worst case for both
    # strategy families.
    min_rvol_5m: float = 1.3
    trigger_rvol_1m: float = 2.0
    trend_adx: float = 25.0
    range_adx: float = 18.0
    trend_efficiency_ratio: float = 0.35
    range_efficiency_ratio: float = 0.20

    # Sizing, checked here so a candidate that cannot be traded profitably is
    # never presented as one.
    atr_stop_multiple: float = 1.5
    min_stop_fraction: float = 0.0045
    max_cost_to_stop: float = 0.25
    round_trip_cost: float = 0.001

    def clone(self, **overrides: float | int) -> ScanConfig:
        from dataclasses import replace

        return replace(self, **overrides)  # type: ignore[arg-type] # runtime field names vary
