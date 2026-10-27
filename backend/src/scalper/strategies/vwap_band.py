"""VWAP sigma-band mean reversion.

The original strategy in this project, moved behind the strategy interface so
it is one plug-in among many rather than the thing the scanner is built around.

It fades price back toward VWAP when it has stretched two standard deviations
away in a market that is not trending. The entry is naturally a resting limit
order, which earns maker pricing by construction, and that is the single
largest cost lever available at this timeframe.
"""

from __future__ import annotations

from decimal import Decimal

from scalper.core.fees import MIN_STOP_FRACTION
from scalper.indicators.core import adx, atr, efficiency_ratio, rsi, rvol, vwap_bands
from scalper.strategies.base import Levels, Setup, Strategy, StrategyContext

#: Not a tunable. Loosening to 1.5 roughly triples the signal count and halves
#: the edge, which is the classic way this strategy is ruined in tuning.
SIGMA_THRESHOLD = 2.0

RSI_OVERSOLD = 20.0
RSI_OVERBOUGHT = 80.0
MIN_RVOL = 1.3

#: Below this ADX the market is ranging, which is the only regime this trades.
RANGE_ADX = 18.0
RANGE_EFFICIENCY = 0.20

ATR_STOP_MULTIPLE = Decimal("1.2")
REWARD_MULTIPLE = Decimal("2")


class VwapBandReversion(Strategy):
    id = "vwap-band-reversion"
    name = "VWAP Band Reversion"
    summary = (
        "Fades price back to VWAP after a two sigma stretch, only while the "
        "market is ranging."
    )
    assumptions = (
        "The regime gate is load-bearing rather than a refinement. A "
        "cost-aware study found regime swung mean-reversion outcomes by 57 "
        "percentage points, so this stands aside in a trend by design.",
    )

    history_5m = 60
    history_1m = 40

    def assess(self, ctx: StrategyContext) -> Setup:
        if not self.has_enough_history(ctx):
            return Setup.waiting("not enough closed candles yet")

        highs = [c.high for c in ctx.candles_5m]
        lows = [c.low for c in ctx.candles_5m]
        closes = [c.close for c in ctx.candles_5m]
        volumes = [c.volume for c in ctx.candles_5m]

        trend = adx(highs, lows, closes)
        efficiency = efficiency_ratio(closes)
        if trend.adx >= RANGE_ADX or efficiency > RANGE_EFFICIENCY:
            return Setup.waiting(
                f"ADX {trend.adx:.1f} and efficiency {efficiency:.2f} say this is "
                "not ranging; the mean-reversion strategy stands aside"
            )

        activity = rvol(volumes)
        if activity < MIN_RVOL:
            return Setup.waiting(
                f"volume {activity:.2f}x average is too quiet to fade"
            )

        bands = vwap_bands(
            [c.high for c in ctx.candles_1m],
            [c.low for c in ctx.candles_1m],
            [c.close for c in ctx.candles_1m],
            [c.volume for c in ctx.candles_1m],
        )
        distance = bands.sigma_distance(ctx.price)
        fast_rsi = rsi([c.close for c in ctx.candles_1m], 7)

        if distance <= -SIGMA_THRESHOLD and fast_rsi < RSI_OVERSOLD:
            return Setup.entry(
                "LONG",
                f"stretched {distance:.1f} sigma below VWAP with RSI {fast_rsi:.0f}",
            )
        if distance >= SIGMA_THRESHOLD and fast_rsi > RSI_OVERBOUGHT:
            return Setup.entry(
                "SHORT",
                f"stretched {distance:+.1f} sigma above VWAP with RSI {fast_rsi:.0f}",
            )

        return Setup.waiting(
            f"only {distance:+.1f} sigma from VWAP; waiting for {SIGMA_THRESHOLD}"
        )

    def levels(self, ctx: StrategyContext, entry: Decimal, direction: str) -> Levels:
        value = atr(
            [c.high for c in ctx.candles_5m],
            [c.low for c in ctx.candles_5m],
            [c.close for c in ctx.candles_5m],
        )
        distance = max(
            Decimal(str(value)) * ATR_STOP_MULTIPLE, entry * MIN_STOP_FRACTION
        )
        if direction == "LONG":
            return Levels(
                stop=entry - distance, target=entry + distance * REWARD_MULTIPLE
            )
        return Levels(stop=entry + distance, target=entry - distance * REWARD_MULTIPLE)
