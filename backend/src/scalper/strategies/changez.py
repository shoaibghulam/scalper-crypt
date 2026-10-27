"""Changez ICT confluence adapted from the supplied MT5 EA to crypto candles."""

from datetime import datetime
from decimal import Decimal
from itertools import pairwise
from zoneinfo import ZoneInfo

from scalper.strategies.base import Levels, Setup, Strategy, StrategyContext

NEW_YORK = ZoneInfo("America/New_York")


class Changez(Strategy):
    testnet_only = True
    backtest_only = True
    assumptions = (
        "Crypto adaptation of the supplied Changez MT5 EA; profitability is unverified.",
        "Closed candles only; strict two-left/two-right pivots in the last 200 bars.",
        "Unlike MT5's persistent pivots, pivots older than this window expire.",
        "MSS crossing plus discount for longs/premium for shorts in the 100-bar range.",
        "FVG on either of the latest two bars; middle body > 1.5 times its five-body mean.",
        "Signal candle opens in 02:00-05:00 or 08:30-11:00 New York, including weekends.",
        "Binance UTC timestamps use historical New York DST; no broker offset is needed.",
        "Ten-bar swing stop, unchanged; target 5R from actual entry. No stop widening.",
        "Sizing, leverage, costs and exchange constraints use platform settings, not MT5 tiers.",
    )

    def __init__(self, minutes: int):
        if minutes not in (1, 5):
            raise ValueError("Changez supports 1m or 5m")
        self.minutes = minutes
        self.id = f"changez-{minutes}m"
        self.name = f"Changez {minutes} Minute (Crypto)"
        self.summary = "ICT structure shift + premium/discount + displacement FVG; 5R target."
        self.signal_interval_s = minutes * 60
        self.history_1m = 200 if minutes == 1 else 5
        self.history_5m = 200 if minutes == 5 else 1

    def signal(self, ctx: StrategyContext) -> tuple[str, float]:
        if not self.has_enough_history(ctx):
            raise ValueError("need 200 closed entry-timeframe candles")
        if error := self.data_error(ctx):
            raise ValueError(error)
        bars = (ctx.candles_1m if self.minutes == 1 else ctx.candles_5m)[-200:]
        interval = self.signal_interval_s * 1000
        if any(b.open_time % interval or b.close_time != b.open_time + interval - 1
               for b in bars):
            raise ValueError("invalid candle alignment")
        if any(b.open_time - a.open_time != interval for a, b in pairwise(bars)):
            raise ValueError("discontinuous candles")
        if bars[-1].close_time != ctx.candles_1m[-1].close_time:
            raise ValueError("waiting for a newly closed entry candle")
        local = datetime.fromtimestamp(bars[-1].open_time / 1000, NEW_YORK)
        minute = local.hour * 60 + local.minute
        if not (120 <= minute < 300 or 510 <= minute < 660):
            raise ValueError("outside Changez New York sessions")
        high = low = None
        for i in range(2, len(bars) - 2):
            neighbours = bars[i - 2:i] + bars[i + 1:i + 3]
            if all(bars[i].high > b.high for b in neighbours):
                high = bars[i].high
            if all(bars[i].low < b.low for b in neighbours):
                low = bars[i].low
        previous, trigger = bars[-2], bars[-1]
        midpoint = (max(b.high for b in bars[-100:])
                    + min(b.low for b in bars[-100:])) / 2

        def fvg(offset: int, bullish: bool) -> bool:
            index = len(bars) - 1 - offset
            recent, middle, older = bars[index], bars[index - 1], bars[index - 2]
            mean = sum(abs(b.close - b.open) for b in bars[index - 5:index]) / 5
            displaced = abs(middle.close - middle.open) > 1.5 * mean
            gap = (recent.low > older.high and middle.close > older.high) if bullish else (
                recent.high < older.low and middle.close < older.low)
            return displaced and gap

        if (high is not None and previous.close <= high < trigger.close < midpoint
                and any(fvg(i, True) for i in (0, 1))):
            return "LONG", min(b.low for b in bars[-10:])
        if (low is not None and previous.close >= low > trigger.close > midpoint
                and any(fvg(i, False) for i in (0, 1))):
            return "SHORT", max(b.high for b in bars[-10:])
        raise ValueError("waiting for MSS, premium/discount and displacement FVG confluence")

    def levels(self, ctx: StrategyContext, entry: Decimal, direction: str) -> Levels:
        actual, reference = self.signal(ctx)
        if direction != actual or not entry.is_finite() or entry <= 0:
            raise ValueError("invalid Changez entry or direction")
        sign = Decimal(1 if direction == "LONG" else -1)
        stop = Decimal(str(reference))
        distance = (entry - stop) * sign
        target = entry + sign * distance * 5
        if distance <= 0 or min(stop, target) <= 0:
            raise ValueError("entry invalidates Changez swing protection")
        return Levels(stop, target)

    def assess(self, ctx: StrategyContext) -> Setup:
        try:
            direction, _ = self.signal(ctx)
            entry = ctx.book.ask if direction == "LONG" else ctx.book.bid
            self.levels(ctx, Decimal(str(entry)), direction)
        except ValueError as exc:
            return Setup.waiting(str(exc))
        return Setup.entry(direction, "Changez confirmed structure, zone and FVG; 5R target")

    def describe(self) -> dict[str, object]:
        return {
            **super().describe(), "timeframe": f"{self.minutes}m",
            "research_only": True, "backtest_only": True, "group": "changez",
            "parameters": {"pivot_window": 200, "range_bars": 100, "stop_bars": 10,
                           "displacement_length": 5, "displacement_multiplier": 1.5,
                           "reward_risk": 5},
        }


class ChangezCostFiltered(Changez):
    """A fixed cost-distance hypothesis, kept separate from the source strategy."""

    def __init__(self, minutes: int):
        super().__init__(minutes)
        self.id = f"changez-cost-filtered-{minutes}m"
        self.name = f"Changez Cost Filtered {minutes} Minute (Research)"
        self.summary = "Original Changez signal and 5R bracket; skip stops below 0.45%."
        self.assumptions = (*self.assumptions,
            "Separate experimental variant: skip entries with swing stops below 0.45%.",
            "Threshold is the existing platform stop floor, not fitted to this report.",
            "Stops are never widened. Fewer trades does not establish a profitable edge.")

    def levels(self, ctx: StrategyContext, entry: Decimal, direction: str) -> Levels:
        levels = super().levels(ctx, entry, direction)
        if abs(entry - levels.stop) / entry < Decimal("0.0045"):
            raise ValueError("Changez cost filter: swing stop below 0.45%")
        return levels

    def describe(self) -> dict[str, object]:
        description = super().describe()
        description["parameters"] = {
            **description["parameters"], "minimum_stop_fraction": 0.0045,
        }
        return description


CHANGEZ_STRATEGIES = {s.id: s for s in (
    Changez(1), Changez(5), ChangezCostFiltered(1), ChangezCostFiltered(5),
)}
