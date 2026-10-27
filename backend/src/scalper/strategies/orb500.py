"""24/7 crypto adaptation: rolling bias, range breakout and confirmed retest."""

from __future__ import annotations

from decimal import Decimal

from scalper.core.fees import MIN_STOP_FRACTION
from scalper.exchange.market_data import Candle
from scalper.strategies.base import Levels, Setup, Strategy, StrategyContext

RETEST_TOLERANCE = 0.15
REWARD_MULTIPLE = Decimal("2")
MAX_SETUP_MINUTES = 30


class Backtest500V1(Strategy):
    id = "500-backtest-v1"
    name = "500 Backtest V1"
    summary = (
        "24/7 crypto: rolling hourly engulfing bias, a preceding 15-minute range, "
        "then a closed 5m breakout and confirmed range-edge retest."
    )
    assumptions = (
        "User-requested crypto adaptation: no fixed hours, timezone, weekday or "
        "exchange-session restriction. The video's 7/8 a.m. bias is replaced.",
        "For each candidate breakout, form two consecutive 60-minute candles "
        "immediately before its preceding 15-minute range; their engulfing bodies "
        "provide direction. These are rolling blocks, not clock-hour candles.",
        "Search breakouts within the last 30 minutes, oldest eligible first. "
        "Freeze each range before its breakout; require a later closed 5m "
        "engulfing retest within 15% of range width of the edge.",
        "Reject repeat confirmations of the same breakout, invalidated ranges "
        "and unfinished 5m candles. Signal expiry remains 20 seconds.",
        "Opposite-range stop, 0.45% floor, 2R target and half-range chase bound "
        "remain engineering adaptations. Crypto profitability is unverified.",
    )
    history_5m = 20
    history_1m = 145
    candle_limit_1m = 400
    max_signal_age_s = 20

    @staticmethod
    def _engulf(previous: Candle, current: Candle) -> str | None:
        if (
            previous.close < previous.open
            and current.close > current.open
            and current.open <= previous.close
            and current.close >= previous.open
        ):
            return "LONG"
        if (
            previous.close > previous.open
            and current.close < current.open
            and current.open >= previous.close
            and current.close <= previous.open
        ):
            return "SHORT"
        return None

    @staticmethod
    def _aggregate(bars: list[Candle], start: int, minutes: int) -> Candle:
        selected = [c for c in bars if start <= c.open_time < start + minutes * 60_000]
        if [c.open_time for c in selected] != list(range(start, start + minutes * 60_000, 60_000)):
            raise ValueError("rolling reference needs consecutive closed 1m candles")
        if any(c.close_time != c.open_time + 59_999 for c in selected):
            raise ValueError("invalid rolling candle interval")
        return Candle(
            start,
            selected[0].open,
            max(c.high for c in selected),
            min(c.low for c in selected),
            selected[-1].close,
            sum(c.volume for c in selected),
            start + minutes * 60_000 - 1,
            sum(c.quote_volume for c in selected),
            sum(c.taker_buy_base for c in selected),
        )

    def _anchor(self, ctx: StrategyContext) -> int:
        """Find a causal breakout anchor without any local-clock/session gate."""
        end = ctx.candles_1m[-1].close_time + 1
        if end % 300_000:
            raise ValueError("waiting for a closed 5m confirmation candle")
        for start in range(end - MAX_SETUP_MINUTES * 60_000, end, 300_000):
            range_start = start - 15 * 60_000
            try:
                first = self._aggregate(ctx.candles_1m, range_start - 120 * 60_000, 60)
                second = self._aggregate(ctx.candles_1m, range_start - 60 * 60_000, 60)
                band = self._aggregate(ctx.candles_1m, range_start, 15)
                breakout = self._aggregate(ctx.candles_1m, start, 5)
            except ValueError:
                # An older candidate may precede available history. Never fill gaps.
                continue
            direction = self._engulf(first, second)
            if (direction == "LONG" and breakout.close > band.high) or (
                direction == "SHORT" and breakout.close < band.low
            ):
                return start
        raise ValueError("no rolling hourly engulfing breakout with consecutive reference candles")

    def _trend(self, ctx: StrategyContext) -> str | None:
        start = self._anchor(ctx) - 135 * 60_000
        return self._engulf(
            self._aggregate(ctx.candles_1m, start, 60),
            self._aggregate(ctx.candles_1m, start + 60 * 60_000, 60),
        )

    def _window(self, ctx: StrategyContext) -> list[Candle]:
        end = self._anchor(ctx)
        return [c for c in ctx.candles_1m if end - 15 * 60_000 <= c.open_time < end]

    def _retest(
        self, previous: Candle, bar: Candle, edge: float, width: float, direction: str
    ) -> bool:
        if self._engulf(previous, bar) != direction:
            return False
        if direction == "LONG":
            return bar.low <= edge + width * RETEST_TOLERANCE and bar.close > edge
        return bar.high >= edge - width * RETEST_TOLERANCE and bar.close < edge

    def assess(self, ctx: StrategyContext) -> Setup:
        if not self.has_enough_history(ctx):
            return Setup.waiting("not enough closed candles yet")
        error = self.data_error(ctx)
        if error:
            return Setup.waiting(error)
        try:
            bars = self._window(ctx)
        except (ValueError, OverflowError, OSError) as exc:
            return Setup.waiting(str(exc))
        high, low = max(c.high for c in bars), min(c.low for c in bars)
        width = high - low
        if width <= 0:
            return Setup.waiting("rolling range has no width")
        try:
            direction = self._trend(ctx)
            if direction is None:
                return Setup.waiting("rolling hourly bodies do not engulf; no directional bias")
            # Build complete 5m bars from the same 1m data used for the range.
            # This avoids partial aggregates and inconsistent provider snapshots.
            end = bars[-1].open_time + 60_000
            last_close = ctx.candles_1m[-1].close_time
            if (last_close + 1) % 300_000:
                return Setup.waiting("waiting for a closed 5m confirmation candle")
            post = [
                self._aggregate(ctx.candles_1m, start, 5)
                for start in range(end, last_close + 1, 300_000)
            ]
        except ValueError as exc:
            return Setup.waiting(str(exc))
        sign = 1 if direction == "LONG" else -1
        edge = high if direction == "LONG" else low
        far = low if direction == "LONG" else high
        broken = next((i for i, c in enumerate(post) if (c.close - edge) * sign > 0), None)
        if broken is None:
            return Setup.waiting("waiting for 5m close beyond the rolling range")
        if any((c.close - far) * sign <= 0 for c in post[broken:]):
            return Setup.waiting("rolling breakout invalidated through opposite range edge")
        if broken == len(post) - 1:
            return Setup.waiting("breakout confirmed; wait for a later retest")
        for i in range(broken + 1, len(post) - 1):
            if self._retest(post[i - 1], post[i], edge, width, direction):
                return Setup.waiting("breakout already had a confirmed retest; no repeat entry")
        last = post[-1]
        if not self._retest(post[-2], last, edge, width, direction):
            return Setup.waiting("waiting for an engulfing rejection at the range edge")
        entry = ctx.book.ask if direction == "LONG" else ctx.book.bid
        if (entry - edge) * sign <= 0:
            return Setup.waiting("executable price has lost the breakout")
        if (entry - last.close) * sign > width * 0.5:
            return Setup.waiting("executable price is chasing beyond half the rolling range")
        return Setup.entry(direction, "hourly engulfing bias, closed 5m breakout and edge retest")

    def levels(self, ctx: StrategyContext, entry: Decimal, direction: str) -> Levels:
        setup = self.assess(ctx)
        if not setup.ready or setup.direction != direction or not entry.is_finite() or entry <= 0:
            raise ValueError("no valid rolling-range signal for the requested entry")
        bars = self._window(ctx)
        high, low = Decimal(str(max(c.high for c in bars))), Decimal(str(min(c.low for c in bars)))
        sign = Decimal(1) if direction == "LONG" else Decimal(-1)
        edge = high if direction == "LONG" else low
        if (entry - edge) * sign <= 0 or (entry - Decimal(str(ctx.price))) * sign > (
            high - low
        ) / 2:
            raise ValueError("entry no longer confirms the rolling-range breakout")
        far_side = low if direction == "LONG" else high
        distance = max((entry - far_side) * sign, entry * MIN_STOP_FRACTION)
        return Levels(
            stop=entry - sign * distance, target=entry + sign * distance * REWARD_MULTIPLE
        )
