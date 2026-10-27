"""Explicit 24/7 research challengers; parameters are unvalidated hypotheses."""

from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from math import sqrt

from scalper.core.fees import MIN_STOP_FRACTION
from scalper.core.filters import Rounding, round_to_tick
from scalper.engine.plan import MIN_REWARD_TO_RISK
from scalper.exchange.market_data import Candle
from scalper.indicators.core import adx, atr, efficiency_ratio, ema
from scalper.strategies.base import Levels, Setup, Strategy, StrategyContext


def volatility(bars: list[Candle]) -> float:
    return atr([b.high for b in bars], [b.low for b in bars], [b.close for b in bars])


def pivots(bars: list[Candle], low: bool) -> list[int]:
    result = []
    for i in range(2, len(bars) - 2):
        value = bars[i].low if low else bars[i].high
        others = bars[i - 2 : i] + bars[i + 1 : i + 3]
        if all(value < b.low if low else value > b.high for b in others):
            result.append(i)
    return result


@dataclass(frozen=True)
class Signal:
    direction: str
    stop: float
    boundary: float
    atr: float
    target: float | None = None
    reward: Decimal = Decimal("1.5")


class ResearchStrategy(Strategy):
    history_1m = 60
    max_signal_age_s = 20
    assumptions: tuple[str, ...] = (
        "Research baseline, not demonstrated profitable; 24/7 and real closed candles.",
        "Market entry, shared fee/spread/risk gates, 0.45% minimum stop distance.",
        "20-second signal expiry and maximum entry deviation of 0.5 signal ATR.",
    )

    def validate(self, ctx: StrategyContext, five_trigger: bool = False) -> None:
        if not self.has_enough_history(ctx):
            raise ValueError("insufficient history")
        if error := self.data_error(ctx):
            raise ValueError(error)
        for bars, interval in ((ctx.candles_1m, 60000), (ctx.candles_5m, 300000)):
            if any(
                b.open_time % interval or b.close_time != b.open_time + interval - 1 for b in bars
            ):
                raise ValueError("invalid candle alignment")
            if any(b.open_time - a.open_time != interval for a, b in pairwise(bars)):
                raise ValueError("discontinuous candles")
        age = ctx.candles_1m[-1].close_time - ctx.candles_5m[-1].close_time
        if not 0 <= age < 300000:
            raise ValueError("future or stale 5m context")
        if five_trigger and age != 0:
            raise ValueError("waiting for a newly closed 5m trigger")

    def signal(self, ctx: StrategyContext) -> Signal:
        raise NotImplementedError

    def levels(self, ctx: StrategyContext, entry: Decimal, direction: str) -> Levels:
        signal = self.signal(ctx)
        if direction != signal.direction or not entry.is_finite() or entry <= 0:
            raise ValueError("invalid entry or direction")
        sign = Decimal(1 if direction == "LONG" else -1)
        if abs(entry - Decimal(str(ctx.price))) > Decimal(str(0.5 * signal.atr)):
            raise ValueError("entry moved beyond half an ATR")
        if (entry - Decimal(str(signal.boundary))) * sign <= 0:
            raise ValueError("entry lost reclaimed level")
        structural_risk = (entry - Decimal(str(signal.stop))) * sign
        if structural_risk <= 0:
            raise ValueError("entry crossed structural stop")
        risk = max(structural_risk, entry * MIN_STOP_FRACTION)
        stop = entry - sign * risk
        target = (
            Decimal(str(signal.target))
            if signal.target is not None
            else entry + sign * risk * signal.reward
        )
        if min(stop, target) <= 0 or (target - entry) * sign < risk * MIN_REWARD_TO_RISK:
            raise ValueError("frozen target leaves less than 1.5R")
        return Levels(stop, target)

    def execution_levels(
        self, ctx: StrategyContext, entry: Decimal, direction: str, tick_size: Decimal
    ) -> Levels:
        levels = self.levels(ctx, entry, direction)
        signal = self.signal(ctx)
        sign = Decimal(1 if direction == "LONG" else -1)
        stop = round_to_tick(levels.stop, tick_size, Rounding.DOWN if sign == 1 else Rounding.UP)
        if signal.target is None:
            target = round_to_tick(
                entry + sign * abs(entry - stop) * signal.reward,
                tick_size,
                Rounding.UP if sign == 1 else Rounding.DOWN,
            )
        else:
            # Never move a frozen structural/mean target beyond its reference to pass risk.
            target = round_to_tick(
                levels.target, tick_size, Rounding.DOWN if sign == 1 else Rounding.UP
            )
        if (target - entry) * sign < abs(entry - stop) * MIN_REWARD_TO_RISK:
            raise ValueError("rounded frozen target leaves less than 1.5R")
        return Levels(stop, target)

    def assess(self, ctx: StrategyContext) -> Setup:
        try:
            signal = self.signal(ctx)
            entry = ctx.book.ask if signal.direction == "LONG" else ctx.book.bid
            self.levels(ctx, Decimal(str(entry)), signal.direction)
        except ValueError as exc:
            return Setup.waiting(str(exc))
        return Setup.entry(signal.direction, self.summary)


class RollingBreakoutRetest(ResearchStrategy):
    signal_interval_s = 300
    id = "rolling-breakout-retest"
    name = "Rolling Breakout Retest (Research)"
    summary = "Frozen 20-bar 5m range, breakout and first retest within three bars."
    assumptions: tuple[str, ...] = (
        *ResearchStrategy.assumptions,
        "5m baseline; breakout excluded from range. First retest only, 2R target.",
        "Retest tolerance and stop buffer are 0.1 ATR; far-side close invalidates.",
    )

    def signal(self, ctx: StrategyContext) -> Signal:
        self.validate(ctx, five_trigger=True)
        bars = ctx.candles_5m[-60:]
        value = volatility(bars)
        for i in range(len(bars) - 4, len(bars) - 1):
            window = bars[i - 20 : i]
            high, low = max(b.high for b in window), min(b.low for b in window)
            sign = 1 if bars[i].close > high else -1 if bars[i].close < low else 0
            if not sign:
                continue
            edge = high if sign == 1 else low
            for j in range(i + 1, len(bars)):
                b = bars[j]
                if b.close < low if sign == 1 else b.close > high:
                    raise ValueError("breakout invalidated through far side")
                touches = b.low <= edge + 0.1 * value if sign == 1 else b.high >= edge - 0.1 * value
                confirms = (b.close - edge) * sign > 0 and (b.close - b.open) * sign > 0
                if touches and confirms:
                    if j != len(bars) - 1:
                        raise ValueError("first retest already consumed")
                    extreme = min(b.low, edge) if sign == 1 else max(b.high, edge)
                    return Signal(
                        "LONG" if sign == 1 else "SHORT",
                        extreme - sign * 0.1 * value,
                        edge,
                        value,
                        reward=Decimal("2"),
                    )
        raise ValueError("no first confirmed breakout retest")


class RangeVwapReversion(ResearchStrategy):
    signal_interval_s = 300
    id = "range-vwap-reversion"
    name = "Range VWAP Reversion (Research)"
    summary = "5m band re-entry in a flat range, targeting the frozen rolling VWAP."
    assumptions: tuple[str, ...] = (
        *ResearchStrategy.assumptions,
        "60-minute typical-price volume-weighted VWAP approximation, not trade-derived VWAP.",
        "Volume-weighted 2-sigma bands, not SMA Bollinger bands; ADX <20 and efficiency <=0.25.",
        "Reference excludes excursion and trigger. Half-window mean drift <=0.2 ATR.",
        "Frozen mean target; excursion extreme plus 0.1 ATR stop; no timed exit.",
    )

    @staticmethod
    def bands(bars: list[Candle]) -> tuple[float, float]:
        total = sum(b.volume for b in bars)
        if total <= 0:
            raise ValueError("empty VWAP window")
        center = sum((b.high + b.low + b.close) / 3 * b.volume for b in bars) / total
        variance = (
            sum(b.volume * ((b.high + b.low + b.close) / 3 - center) ** 2 for b in bars) / total
        )
        return center, sqrt(variance)

    def signal(self, ctx: StrategyContext) -> Signal:
        self.validate(ctx, five_trigger=True)
        bars = ctx.candles_5m[-60:]
        regime = bars[:-2]
        if (
            adx([b.high for b in regime], [b.low for b in regime], [b.close for b in regime]).adx
            >= 20
            or efficiency_ratio([b.close for b in regime]) > 0.25
        ):
            raise ValueError("not a low-trend range")
        value = volatility(bars)
        reference = bars[-14:-2]
        center, sigma = self.bands(reference)
        left, _ = self.bands(reference[:6])
        right, _ = self.bands(reference[6:])
        if sigma <= 0 or abs(right - left) > 0.2 * value:
            raise ValueError("VWAP reference is not flat")
        previous, trigger = bars[-2:]
        lower, upper = center - 2 * sigma, center + 2 * sigma
        if previous.close < lower < trigger.close < center and trigger.close > trigger.open:
            return Signal(
                "LONG", min(previous.low, trigger.low) - 0.1 * value, lower, value, center
            )
        if previous.close > upper > trigger.close > center and trigger.close < trigger.open:
            return Signal(
                "SHORT", max(previous.high, trigger.high) + 0.1 * value, upper, value, center
            )
        raise ValueError("no closed band re-entry")


class LiquiditySweepReclaim(ResearchStrategy):
    id = "liquidity-sweep-reclaim"
    name = "Liquidity Sweep Reclaim (Research)"
    summary = "First 1m confirmed swing sweep and reclaim with aligned 5m EMA context."
    assumptions: tuple[str, ...] = (
        *ResearchStrategy.assumptions,
        "Two-left/two-right pivots known before trigger; no FVG inference.",
        "5m EMA20 direction and three-bar slope; volume >=1.3x preceding 20 bars.",
        "Rejection wick >=50% of range; target most recent untouched opposing pivot.",
    )

    def signal(self, ctx: StrategyContext) -> Signal:
        self.validate(ctx)
        bars = ctx.candles_1m[-60:]
        previous, trigger = bars[:-1], bars[-1]
        trend = [b.close for b in ctx.candles_5m[-60:]]
        current, earlier = ema(trend, 20), ema(trend[:-3], 20)
        sign = (
            1
            if trigger.close > current > earlier
            else -1
            if trigger.close < current < earlier
            else 0
        )
        if not sign:
            raise ValueError("no aligned 5m trend")
        span = trigger.high - trigger.low
        wick = (
            min(trigger.open, trigger.close) - trigger.low
            if sign == 1
            else trigger.high - max(trigger.open, trigger.close)
        )
        baseline = sum(b.volume for b in bars[-21:-1]) / 20
        if span <= 0 or wick < 0.5 * span or baseline <= 0 or trigger.volume < 1.3 * baseline:
            raise ValueError("insufficient rejection wick or volume")
        for i in reversed(pivots(previous, low=sign == 1)):
            level = previous[i].low if sign == 1 else previous[i].high
            if any((b.low < level if sign == 1 else b.high > level) for b in previous[i + 1 :]):
                continue
            swept = (
                trigger.low < level < trigger.close
                if sign == 1
                else trigger.high > level > trigger.close
            )
            if not swept:
                continue
            for j in reversed(pivots(previous, low=sign == -1)):
                target = previous[j].high if sign == 1 else previous[j].low
                if (target - trigger.close) * sign <= 0:
                    continue
                if any((b.high >= target if sign == 1 else b.low <= target) for b in bars[j + 1 :]):
                    continue
                value = volatility(bars)
                extreme = trigger.low if sign == 1 else trigger.high
                return Signal(
                    "LONG" if sign == 1 else "SHORT",
                    extreme - sign * 0.1 * value,
                    level,
                    value,
                    target,
                )
            raise ValueError("no untouched opposing target pivot")
        raise ValueError("no first sweep of confirmed pivot")
