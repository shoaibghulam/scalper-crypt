"""Research baseline: closed 5m EMA trend and real 1m pullback confirmation."""

from decimal import Decimal
from itertools import pairwise

from scalper.core.fees import MIN_STOP_FRACTION
from scalper.core.filters import Rounding, round_to_tick
from scalper.engine.plan import MIN_REWARD_TO_RISK
from scalper.indicators.core import atr, ema, ema_series
from scalper.strategies.base import Levels, Setup, Strategy, StrategyContext


class EmaTrendPullback(Strategy):
    id = "ema-trend-pullback"
    name = "EMA Trend Pullback (Research)"
    summary = "24/7 closed 5m EMA20/50 trend with a 2-5 candle 1m pullback."
    history_1m = 60
    max_signal_age_s = 20
    assumptions: tuple[str, ...] = (
        "Research candidate; no demonstrated live profitability.",
        "5m EMA20/50 and three-bar slow-EMA slope; real 1m candles.",
        "Two to five countertrend candles, then a close beyond the prior extreme.",
        "Stop beyond pullback plus 0.1 ATR, with the shared minimum stop floor.",
        "1.5R target requires room to a previously confirmed 1m pivot.",
        "At most 0.5 ATR entry deviation; 20-second signal expiry; no session filter.",
    )

    def _signal(self, ctx: StrategyContext) -> tuple[str, float, float]:
        if not self.has_enough_history(ctx):
            raise ValueError("insufficient history")
        if error := self.data_error(ctx):
            raise ValueError(error)
        for bars, interval in ((ctx.candles_1m, 60_000), (ctx.candles_5m, 300_000)):
            if any(
                b.open_time % interval or b.close_time != b.open_time + interval - 1 for b in bars
            ):
                raise ValueError("invalid candle duration")
            if any(b.open_time - a.open_time != interval for a, b in pairwise(bars)):
                raise ValueError("discontinuous candles")
        bars = ctx.candles_1m[-self.history_1m :]
        if not 0 <= bars[-1].close_time - ctx.candles_5m[-1].close_time < 300_000:
            raise ValueError("future or stale trend candle")
        closes = [b.close for b in ctx.candles_5m[-self.history_5m :]]
        slow = ema_series(closes, 50)
        fast = ema(closes, 20)
        if fast > slow[-1] > slow[-4] and bars[-1].close > fast:
            direction, sign = "LONG", 1
        elif fast < slow[-1] < slow[-4] and bars[-1].close < fast:
            direction, sign = "SHORT", -1
        else:
            raise ValueError("no aligned EMA trend")
        trigger, previous = bars[-1], bars[-2]
        boundary = previous.high if sign == 1 else previous.low
        if (trigger.close - trigger.open) * sign <= 0 or (trigger.close - boundary) * sign <= 0:
            raise ValueError("no closed-bar breakout confirmation")
        count = 0
        for bar in reversed(bars[:-1]):
            if (bar.close - bar.open) * sign >= 0:
                break
            count += 1
        if not 2 <= count <= 5:
            raise ValueError("pullback must contain two to five countertrend candles")
        if trigger.volume <= 0:
            raise ValueError("empty trigger candle")
        volatility = atr([b.high for b in bars], [b.low for b in bars], [b.close for b in bars])
        pullback = bars[-count - 1 :]
        reference = min(b.low for b in pullback) if sign == 1 else max(b.high for b in pullback)
        return direction, reference - sign * 0.1 * volatility, volatility

    def levels(self, ctx: StrategyContext, entry: Decimal, direction: str) -> Levels:
        signal, reference, volatility = self._signal(ctx)
        if direction != signal or not entry.is_finite() or entry <= 0:
            raise ValueError("invalid entry or direction")
        sign = Decimal(1 if direction == "LONG" else -1)
        if abs(entry - Decimal(str(ctx.price))) > Decimal(str(0.5 * volatility)):
            raise ValueError("entry moved beyond half an ATR")
        previous = ctx.candles_1m[-2]
        boundary = previous.high if sign == 1 else previous.low
        if (entry - Decimal(str(boundary))) * sign <= 0:
            raise ValueError("entry lost breakout level")
        distance = max((entry - Decimal(str(reference))) * sign, entry * MIN_STOP_FRACTION)
        stop = entry - sign * distance
        target = entry + sign * distance * MIN_REWARD_TO_RISK
        if min(stop, target) <= 0:
            raise ValueError("non-positive protection price")
        if self._target_room(ctx, entry, direction) < distance * MIN_REWARD_TO_RISK:
            raise ValueError("no confirmed pivot with sufficient target room")
        return Levels(stop=stop, target=target)

    @staticmethod
    def _target_room(ctx: StrategyContext, entry: Decimal, direction: str) -> Decimal:
        sign = Decimal(1 if direction == "LONG" else -1)
        # Two bars on each side, confirmed BEFORE the trigger. No future pivot data.
        bars = ctx.candles_1m[-60:-1]
        barriers = []
        for i in range(2, len(bars) - 2):
            pivot = bars[i].high if sign == 1 else bars[i].low
            neighbours = bars[i - 2 : i] + bars[i + 1 : i + 3]
            if all((pivot - (b.high if sign == 1 else b.low)) * int(sign) > 0 for b in neighbours):
                room = (Decimal(str(pivot)) - entry) * sign
                if room > 0:
                    barriers.append(room)
        return min(barriers) if barriers else Decimal(0)

    def execution_levels(
        self, ctx: StrategyContext, entry: Decimal, direction: str, tick_size: Decimal
    ) -> Levels:
        levels = self.levels(ctx, entry, direction)
        sign = Decimal(1 if direction == "LONG" else -1)
        stop = round_to_tick(levels.stop, tick_size, Rounding.DOWN if sign == 1 else Rounding.UP)
        target = round_to_tick(
            entry + sign * abs(entry - stop) * MIN_REWARD_TO_RISK,
            tick_size,
            Rounding.UP if sign == 1 else Rounding.DOWN,
        )
        if (target - entry) * sign > self._target_room(ctx, entry, direction):
            raise ValueError("rounded target crosses confirmed price barrier")
        return Levels(stop, target)

    def assess(self, ctx: StrategyContext) -> Setup:
        try:
            direction, _, _ = self._signal(ctx)
            entry = ctx.book.ask if direction == "LONG" else ctx.book.bid
            self.levels(ctx, Decimal(str(entry)), direction)
        except ValueError as exc:
            return Setup.waiting(str(exc))
        return Setup.entry(direction, "EMA trend, confirmed pullback and clear 1.5R target")
