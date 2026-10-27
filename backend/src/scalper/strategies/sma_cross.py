"""SMA 20/50 crossover on 15-minute candles.

Ported from a strategy record written after an ETHUSDT long: look for a
SMA(20)/SMA(50) crossover on the latest closed candle and take the side that
crossed.

Two honest divergences from that source, both forced by this platform:

The engine fetches 1m and 5m candles only, so a 15m bar is folded from three
5m bars. A partial trailing group is discarded rather than used, because a
signal read off a candle that has not closed is a signal off a candle that
does not exist.

And every entry here carries a stop and a 2R target, where the source held
until the opposite crossover. The source had no stop at all - not by choice,
but because its venue rejected STOP_MARKET with -4120. An unprotected
position between signals is not a property worth reproducing.

The source record is candid that this rule loses on this asset at a shorter
timeframe (-6.4% on ETHUSDT 1h against +41.6% buy and hold) and that 15m pays
fees more often for smaller moves. That is carried into the assumptions
rather than left for someone to rediscover.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

from scalper.indicators.core import atr, sma
from scalper.strategies.base import Levels, Setup, Strategy, StrategyContext

FAST = 20
SLOW = 50
#: Three 5m bars to a 15m candle.
FOLD = 3
#: Enough 5m history to settle a 50-period average on the folded series, plus
#: room for the ATR the bracket is measured from.
HISTORY_5M = (SLOW + 10) * FOLD


class _Folded:
    """A 15m candle assembled from three 5m ones."""

    __slots__ = ("open_time", "close_time", "open", "high", "low", "close", "volume")

    def __init__(self, **kw: Any) -> None:
        for key, value in kw.items():
            setattr(self, key, value)


def fold_15m(bars: Sequence[Any]) -> list[Any]:
    """Fold 5m candles into 15m ones, dropping any incomplete tail group."""
    folded: list[Any] = []
    for start in range(0, len(bars) - FOLD + 1, FOLD):
        group = bars[start : start + FOLD]
        first, last = group[0], group[-1]
        folded.append(
            _Folded(
                open_time=first.open_time,
                close_time=last.close_time,
                open=first.open,
                high=max(bar.high for bar in group),
                low=min(bar.low for bar in group),
                close=last.close,
                volume=sum(bar.volume for bar in group),
            )
        )
    return folded


class SmaCross2050(Strategy):
    """SMA(20) crossing SMA(50) on folded 15m candles."""

    id = "sma-cross-20-50-15m"
    name = "SMA 20/50 cross (15m)"
    signal_interval_s = 900
    history_1m = 60
    history_5m = HISTORY_5M
    backtest_only = True
    research_only = True
    testnet_only = True

    summary = (
        "SMA20 crosses SMA50 on 15m candles folded from 5m. Long on the upward "
        "cross, short on the downward one, bracketed at 1.5 ATR with a 0.45% "
        "floor and a 2R target. The source rule's own backtest loses -6.4% on "
        "ETHUSDT 1h against +41.6% buy and hold, and 15m pays fees more often "
        "for smaller moves."
    )

    def cross(self, closes: Sequence[float]) -> str | None:
        """Which way the averages crossed on the latest closed bar, if at all.

        Only the latest bar counts. Acting on an older crossover is the
        "state interpretation" the source record flagged as its weak joint: it
        enters a position the rule would already hold, at a price the rule
        never chose.
        """
        if len(closes) < SLOW + 1:
            return None
        values = list(closes)
        now_fast, now_slow = sma(values, FAST), sma(values, SLOW)
        prev_fast, prev_slow = sma(values[:-1], FAST), sma(values[:-1], SLOW)
        if None in (now_fast, now_slow, prev_fast, prev_slow):
            return None
        if prev_fast <= prev_slow and now_fast > now_slow:
            return "LONG"
        if prev_fast >= prev_slow and now_fast < now_slow:
            return "SHORT"
        return None

    def bracket(self, entry: Decimal, direction: str, atr_value: float) -> Levels:
        """Stop at 1.5 ATR with a 0.45% floor, target at twice the risk.

        The floor is not decoration. The source record measured a round-trip
        taker fee at 102% of a 1.5-ATR stop on ETH at one minute; a stop
        tighter than the floor is consumed by costs before direction matters.
        """
        if direction not in {"LONG", "SHORT"}:
            raise ValueError(f"invalid direction {direction!r}")
        if not entry.is_finite() or entry <= 0:
            raise ValueError("invalid entry price")
        risk = max(Decimal(str(1.5 * atr_value)), entry * Decimal("0.0045"))
        sign = Decimal(1 if direction == "LONG" else -1)
        stop, target = entry - sign * risk, entry + sign * 2 * risk
        if min(stop, target) <= 0:
            raise ValueError("nonpositive bracket")
        return Levels(stop, target)

    def assess(self, ctx: StrategyContext) -> Setup:
        ctx = self.research_context(ctx)
        if not self.has_enough_history(ctx):
            return Setup.waiting("insufficient history")
        if error := self.data_error(ctx):
            return Setup.waiting(error)
        folded = fold_15m(ctx.candles_5m)
        if len(folded) < SLOW + 1:
            return Setup.waiting("waiting for enough closed 15m candles")
        direction = self.cross([bar.close for bar in folded])
        if direction is None:
            return Setup.waiting("no crossover on the latest closed 15m candle")
        return Setup.entry(direction, "SMA20 crossed SMA50 on the last closed 15m candle")

    def levels(self, ctx: StrategyContext, entry: Decimal, direction: str) -> Levels:
        ctx = self.research_context(ctx)
        folded = fold_15m(ctx.candles_5m)
        if not folded:
            raise ValueError("no closed 15m candles to measure risk from")
        value = atr(
            [bar.high for bar in folded],
            [bar.low for bar in folded],
            [bar.close for bar in folded],
        )
        return self.bracket(entry, direction, float(value))

    def describe(self) -> dict[str, object]:
        return {
            **super().describe(),
            "timeframe": "15m",
            "assumptions": [
                "Ported from an external strategy record, not a validated system.",
                "15m candles are folded from three 5m bars; this engine fetches "
                "no 15m data of its own.",
                "The source rule held to the opposite crossover with NO stop, "
                "because its venue rejected STOP_MARKET (-4120). This version "
                "brackets at 1.5 ATR / 0.45% floor with a 2R target instead, so "
                "its expectancy is not the source's.",
                "The source's own backtest: -6.4% on ETHUSDT 1h against +41.6% "
                "buy and hold over 24 trades. 15m pays fees more often for "
                "smaller moves, so expectancy here should be worse, not better.",
                "6-24 trades is far too small a sample to conclude anything, "
                "which cuts against the winning rows as much as the losing one.",
            ],
        }


SMA_CROSS_STRATEGIES = {SmaCross2050.id: SmaCross2050()}
