"""Video-inspired price action; deterministic, offline research implementation.

Source/automation differences: docs/video docs/found action/README.md.
State is reconstructed from closed bars, never retained between datasets.
"""

from decimal import Decimal
from itertools import pairwise

from scalper.exchange.market_data import Candle
from scalper.strategies.base import Levels, Setup, Strategy, StrategyContext


def hourly(bars: list[Candle]) -> list[Candle]:
    """Only complete UTC-aligned hours; exclude partial and gapped groups."""
    result = []
    for i, first in enumerate(bars):
        if first.open_time % 3_600_000:
            continue
        group = bars[i : i + 12]
        if len(group) != 12 or any(
            b.open_time != first.open_time + j * 300_000 or b.close_time != b.open_time + 299_999
            for j, b in enumerate(group)
        ):
            continue
        result.append(
            Candle(
                first.open_time,
                first.open,
                max(b.high for b in group),
                min(b.low for b in group),
                group[-1].close,
                sum(b.volume for b in group),
                group[-1].close_time,
                sum(b.quote_volume for b in group),
                sum(b.taker_buy_base for b in group),
            )
        )
    return result


def pivot(bars: list[Candle], index: int, sign: int) -> bool:
    """Strict two-left/two-right swing; callers enforce when it becomes known."""
    if index < 2 or index + 2 >= len(bars):
        return False
    value = bars[index].high if sign == 1 else -bars[index].low
    return all(
        value > (bars[j].high if sign == 1 else -bars[j].low)
        for j in range(index - 2, index + 3)
        if j != index
    )


def confirmation(previous: Candle, current: Candle, sign: int) -> bool:
    span = current.high - current.low
    if span <= 0:
        return False
    wick = (
        min(current.open, current.close) - current.low
        if sign == 1
        else current.high - max(current.open, current.close)
    )
    rejection = wick >= span / 2 and (current.close - (current.high + current.low) / 2) * sign > 0
    engulf = (
        (current.close - current.open) * sign > 0
        and (previous.close - previous.open) * sign < 0
        and (current.open - previous.close) * sign <= 0
        and (current.close - previous.open) * sign >= 0
    )
    return rejection or engulf


def trigger(bars: list[Candle]) -> int:
    """First confirmation of a frozen swing break; signal only on final bar.

    Each swing can break once. Expired/invalidated/consumed setups cannot rearm
    on the same swing. New breaks replace the old pending setup.
    """
    swings: dict[int, int] = {}
    used: set[tuple[int, int]] = set()
    pending: tuple[int, float, int] | None = None
    for i in range(5, len(bars)):
        # Confirmed by the PREVIOUS close, not by the breakout itself.
        for sign in (1, -1):
            if pivot(bars, i - 3, sign):
                swings[sign] = i - 3
        c, p = bars[i], bars[i - 1]
        if pending:
            side, level, broken = pending
            tolerance = level * 0.001
            if i - broken > 12 or (c.close - level) * side < -tolerance:
                pending = None
            elif (
                c.low <= level + tolerance
                and c.high >= level - tolerance
                and (c.close - level) * side > 0
                and confirmation(p, c, side)
            ):
                if i == len(bars) - 1:
                    return side
                pending = None
                continue
        for sign, index in swings.items():
            if i - index > 36 or (sign, index) in used:
                continue
            level = bars[index].high if sign == 1 else bars[index].low
            if (p.close - level) * sign <= 0 < (c.close - level) * sign:
                used.add((sign, index))
                pending = (sign, level, i)
    return 0


class FundedPriceAction(Strategy):
    id = "funded-price-action"
    name = "Funded Price Action (Video Research)"
    summary = (
        "1h structure context; 5m swing break, later retest and rejection; candle stop, 2R target."
    )
    history_5m = 252
    history_1m = 60
    signal_interval_s = 300
    assumptions = (
        "Research-only funded-trader video adaptation; profitability is unverified.",
        "Confirmed 2-left/2-right swings; maximum swing age 36 bars; retest expires after 12 bars.",
        "Retest tolerance 0.10%; wick at least half the candle, or opposite-body engulfing.",
        "Stop beyond confirmation candle by 0.01%; target 2R before fees, spread and slippage.",
        "UTC 1h structure is context, not a direction veto; video examples trade both ways.",
        "No ATR stop, indicator filter, session restriction or hindsight entry selection.",
    )

    def assess(self, ctx: StrategyContext) -> Setup:
        ctx = self.research_context(ctx)
        if error := self.data_error(ctx):
            return Setup.waiting(error)
        if not self.has_enough_history(ctx):
            return Setup.waiting("need 252 closed 5m bars and 60 closed 1m bars")
        for bars, interval in ((ctx.candles_1m, 60_000), (ctx.candles_5m, 300_000)):
            if any(
                b.open_time % interval or b.close_time != b.open_time + interval - 1 for b in bars
            ):
                return Setup.waiting("invalid candle alignment")
            if any(b.open_time - a.open_time != interval for a, b in pairwise(bars)):
                return Setup.waiting("discontinuous candles")
        if ctx.candles_5m[-1].close_time != ctx.candles_1m[-1].close_time:
            return Setup.waiting("wait for a newly closed 5m candle")
        hours = hourly(ctx.candles_5m)
        if len(hours) < 20:
            return Setup.waiting("need 20 complete closed UTC hours")
        highs = [b.high for i, b in enumerate(hours) if pivot(hours, i, 1)]
        lows = [b.low for i, b in enumerate(hours) if pivot(hours, i, -1)]
        context = "mixed/unconfirmed"
        if len(highs) >= 2 and len(lows) >= 2:
            if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
                context = "higher highs/lows"
            elif highs[-1] < highs[-2] and lows[-1] < lows[-2]:
                context = "lower highs/lows"
        side = trigger(ctx.candles_5m)
        if not side:
            return Setup.waiting(f"1h {context}; waiting for 5m break/retest/rejection")
        return Setup.entry(
            "LONG" if side == 1 else "SHORT", f"5m break/retest confirmed; 1h {context}"
        )

    def levels(self, ctx: StrategyContext, entry: Decimal, direction: str) -> Levels:
        ctx = self.research_context(ctx)
        if direction not in {"LONG", "SHORT"}:
            raise ValueError("invalid direction")
        candle = ctx.candles_5m[-1]
        stop = (
            Decimal(str(candle.low)) * Decimal("0.9999")
            if direction == "LONG"
            else Decimal(str(candle.high)) * Decimal("1.0001")
        )
        sign = Decimal(1 if direction == "LONG" else -1)
        risk = (entry - stop) * sign
        if risk <= 0:
            raise ValueError("confirmation candle stop is on the wrong side of entry")
        target = entry + sign * 2 * risk
        if target <= 0:
            raise ValueError("non-positive target")
        return Levels(stop, target)

    def describe(self) -> dict[str, object]:
        return {
            **super().describe(),
            "research_only": True,
            "timeframe": "5m (1h context)",
            "group": "video-research",
        }
