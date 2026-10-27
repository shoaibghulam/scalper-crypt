"""The Minute Black - a Heikin Ashi trend-pullback continuation on the minute.

From `docs/video docs/The 1 Minute Scalping Strategy (That Actually Works)_120.mp4`,
where the presenter writes the whole strategy on screen as a six-point list:

    1. Chart Setup      Heikin Ashi candles, 100 EMA on chart
    2. Market Structure buys only above the 100 EMA, sells only below
    3. Clean Pullback   at least 2 clean opposite colour candles;
                        clean = no wick on the trend side
    4. Entry Signal     a high volume doji, entered as the candle closes;
                        high volume = same size or larger than the last 1-2
    5. Stop Loss        beyond the doji wick
    6. Take Profit      1:1 risk to reward minimum

The core pattern is preserved. The upgraded implementation adds slope/real-price
confirmation, baseline volume, entry expiry/chase limits and a stop buffer.
These engineering defaults are adaptations and require out-of-sample validation.

Heikin Ashi values are synthetic and need not have traded. Stops can legally
reference synthetic levels, but this implementation intentionally uses real
candle extremes plus its documented buffer and fee-distance floor.

Every departure is listed in `assumptions` and rendered in the dashboard.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from scalper.core.fees import MIN_STOP_FRACTION
from scalper.engine.plan import MIN_REWARD_TO_RISK
from scalper.indicators.core import HeikinAshiCandle, atr, ema, heikin_ashi
from scalper.strategies.base import Levels, Setup, Strategy, StrategyContext

#: The trend filter from point 1, at the length shown in his settings panel.
EMA_PERIOD = 100

#: Point 3: "at least 2 clean opposite colour candles". Consecutive, and ending
#: on the bar before the doji - a gap would make it two separate pullbacks.
MIN_PULLBACK_CANDLES = 2

#: How far back to look for the start of the pullback. Beyond this the retrace
#: is a reversal rather than a pause in the trend.
MAX_PULLBACK_CANDLES = 8

#: HA construction naturally creates exactly wickless candles. Allow only
#: floating-point roundoff, not a percentage of a genuine wick.

#: A doji is indecision: the body is a small part of the range. Shown in the
#: video, never given a number, so this is our reading of what he points at.
DOJI_BODY_FRACTION = 0.25

#: Narration at 13:27-13:49 explicitly compares candle HEIGHT with at least
#: ONE of the preceding two candles, despite calling it "high volume".
SIZE_LOOKBACK = 2


@dataclass(frozen=True)
class _Signal:
    """A complete setup, or the reason there is not one."""

    direction: str | None
    note: str
    #: From the REAL candle, never the Heikin Ashi one. The distinction is the
    #: point: this is a price something actually traded at.
    stop_reference: float = 0.0

    @property
    def ready(self) -> bool:
        return self.direction is not None


def _is_clean(candle: HeikinAshiCandle, direction: str) -> bool:
    """No meaningful wick on the side the trend would push against.

    For a long the pullback is red, and a clean red candle has no upper wick:
    the period never traded above its own smoothed open, which is what makes
    Heikin Ashi worth reading here rather than raw candles.
    """
    if candle.span <= 0:
        return False
    wick = candle.upper_wick if direction == "LONG" else candle.lower_wick
    return wick <= 4 * math.ulp(max(abs(candle.open), abs(candle.close)))


def _is_doji(candle: HeikinAshiCandle) -> bool:
    """A small body inside a real range: buyers and sellers in balance.

    A zero-range candle is not a doji, it is a frozen market, and dividing by
    its span would be the more immediate problem.
    """
    if candle.span <= 0:
        return False
    return (
        candle.body / candle.span <= DOJI_BODY_FRACTION
        and candle.upper_wick > 0
        and candle.lower_wick > 0
    )


def _size_confirms(candles: list[HeikinAshiCandle], index: int) -> bool:
    """The source's 'high volume' means HA range, not traded volume."""
    previous = candles[max(0, index - SIZE_LOOKBACK) : index]
    return len(previous) == SIZE_LOOKBACK and any(candles[index].span >= c.span for c in previous)


class MinuteBlack(Strategy):
    id = "the-minute-black"
    name = "The Minute Black"
    summary = (
        "Heikin Ashi EMA100 pullback: at least two wickless opposite-colour "
        "candles followed by a high-volume doji. Real-price execution with "
        "documented slope, volume, expiry and risk adaptations."
    )

    assumptions = (
        "Upgrade: require EMA100 slope and the real close to confirm direction; "
        "traded volume must also reach the preceding 20-bar average (our added filter).",
        "Upgrade: refuse entries more than 0.5 ATR beyond the signal close or "
        "past the real doji extreme. Add a 0.1 ATR stop buffer; retain the fee floor "
        "and minimum 1.5R target. These defaults require out-of-sample validation.",
        "Heikin Ashi prices are synthetic. This adaptation uses real doji extremes "
        "for protective stops; synthetic levels are not inherently invalid orders.",
        "Stop distance is floored at 0.45% to limit costs relative to planned risk. "
        "The floor is an engineering policy, not proof of positive expectancy.",
        "The video asks for at least 1:1 reward/risk. This adaptation retains the "
        "platform minimum of 1.5R; trading performance has not been established.",
        "The 100 EMA is taken on the Heikin Ashi close, which is what "
        "TradingView plots on an HA chart. On a real-price chart the same "
        "setting gives a different line.",
        "'Clean = no wicks' requires a wickless HA candle, allowing only float "
        "roundoff. The previous 10% tolerance admitted visibly non-clean candles.",
        "'A doji' is never defined numerically in the video; it is read here "
        "as a body no larger than 25% of the candle range.",
        "At 13:27-13:49 the presenter defines 'high volume' using candle height: "
        "the HA doji range must match or exceed at least ONE of the last two bars. "
        "It is not a traded-volume comparison. Both upper and lower wicks are required.",
        "The pullback must be consecutive and end on the bar before the doji. "
        "A gap would make it two separate pullbacks.",
        "He trades one timeframe. The 5m series is used here only to judge "
        "whether a 0.45% stop is reachable at all, never as a signal input - a "
        "question his instrument never had to ask.",
    )

    #: A 100 EMA needs its full period before it means anything, plus room for
    #: the pullback and the doji.
    history_1m = 130
    history_5m = 20

    # Engineering defaults, not fitted parameters or claims from the video.
    max_signal_age_s = 20
    max_entry_atr = 0.5
    stop_buffer_atr = Decimal("0.1")

    def _signal(self, ctx: StrategyContext) -> _Signal:
        candles = ctx.candles_1m
        last = len(candles) - 1
        for c in candles:
            values = (c.open, c.high, c.low, c.close, c.volume)
            if any(not math.isfinite(v) for v in values) or c.low <= 0 or c.volume < 0:
                return _Signal(None, "invalid candle data")
            if c.low > min(c.open, c.close) or c.high < max(c.open, c.close):
                return _Signal(None, "inconsistent candle range")
        if candles[-1].volume <= 0:
            return _Signal(None, "doji has no traded volume")

        ha = heikin_ashi(
            [c.open for c in candles],
            [c.high for c in candles],
            [c.low for c in candles],
            [c.close for c in candles],
        )

        # Point 2. The EMA is taken on the Heikin Ashi close, which is what he
        # sees on his chart.
        trend_line = ema([c.close for c in ha], EMA_PERIOD)
        price = ha[last].close
        if price > trend_line:
            direction = "LONG"
        elif price < trend_line:
            direction = "SHORT"
        else:
            return _Signal(None, "price is sitting on the 100 EMA")

        previous_trend = ema([c.close for c in ha[:-3]], EMA_PERIOD)
        if (trend_line - previous_trend) * (1 if direction == "LONG" else -1) <= 0:
            return _Signal(None, "100 EMA slope does not confirm the direction")
        if (candles[-1].close - trend_line) * (1 if direction == "LONG" else -1) <= 0:
            return _Signal(None, "real close does not confirm the EMA direction")

        # Point 4. The doji must be the last CLOSED bar: market_data drops the
        # forming one before a strategy sees it, so index -1 is closed, and
        # looking further forward would read a bar that does not exist yet.
        doji = ha[last]
        if not _is_doji(doji):
            return _Signal(None, "the last candle is not a doji")
        if not _size_confirms(ha, last):
            return _Signal(None, "the doji range is smaller than both preceding HA candles")

        # Point 3, walked backwards from the bar before the doji.
        pullback = 0
        for i in range(last - 1, max(0, last - 1 - MAX_PULLBACK_CANDLES), -1):
            against_trend = (
                ha[i].close < ha[i].open if direction == "LONG" else ha[i].close > ha[i].open
            )
            if not against_trend or not _is_clean(ha[i], direction):
                break
            pullback += 1

        before = last - pullback - 1
        if pullback == MAX_PULLBACK_CANDLES and before >= 0:
            older = ha[before]
            opposite = older.close < older.open if direction == "LONG" else older.close > older.open
            if opposite and _is_clean(older, direction):
                return _Signal(None, "pullback exceeds the maximum continuation length")

        if pullback < MIN_PULLBACK_CANDLES:
            return _Signal(
                None,
                f"only {pullback} clean pullback candle(s) before the doji; "
                f"{MIN_PULLBACK_CANDLES} are required",
            )

        # Point 5, but from the REAL candle. This is the whole distinction:
        # the HA low of this bar is min(real low, HA_open, HA_close), and when
        # the synthetic body sets it the level never traded.
        stop_reference = candles[last].low if direction == "LONG" else candles[last].high

        baseline = sum(c.volume for c in candles[-21:-1]) / 20
        if candles[-1].volume < baseline:
            return _Signal(None, "doji volume is below its preceding 20-bar average")

        return _Signal(
            direction=direction,
            note=(
                f"{pullback} clean pullback candles then a range-confirmed doji on "
                f"{candles[last].volume / max(1e-9, candles[last - 1].volume):.1f}x "
                f"the previous volume, price "
                f"{'above' if direction == 'LONG' else 'below'} "
                f"the 100 EMA at {trend_line:.6g}"
            ),
            stop_reference=stop_reference,
        )

    def _volatility(self, ctx: StrategyContext) -> Decimal:
        bars = ctx.candles_1m
        return Decimal(
            str(atr([c.high for c in bars], [c.low for c in bars], [c.close for c in bars], 14))
        )

    def _entry_error(self, ctx: StrategyContext, entry: Decimal, signal: _Signal) -> str | None:
        if not entry.is_finite() or entry <= 0 or signal.direction is None:
            return "no valid entry signal"
        sign = Decimal(1) if signal.direction == "LONG" else Decimal(-1)
        reference = Decimal(str(signal.stop_reference))
        if (entry - reference) * sign <= 0:
            return "price crossed the doji structure; setup invalidated"
        chase = (entry - Decimal(str(ctx.price))) * sign
        if chase > self._volatility(ctx) * Decimal(str(self.max_entry_atr)):
            return "price moved more than 0.5 ATR beyond the signal close"
        return None

    def assess(self, ctx: StrategyContext) -> Setup:
        if not self.has_enough_history(ctx):
            return Setup.waiting("not enough closed candles yet")
        error = self.data_error(ctx)
        if error:
            return Setup.waiting(error)
        signal = self._signal(ctx)
        if signal.direction is None:
            return Setup.waiting(signal.note)
        entry = Decimal(str(ctx.book.ask if signal.direction == "LONG" else ctx.book.bid))
        reason = self._entry_error(ctx, entry, signal)
        if reason:
            return Setup.waiting(reason)
        return Setup.entry(signal.direction, signal.note)

    def levels(self, ctx: StrategyContext, entry: Decimal, direction: str) -> Levels:
        if not self.has_enough_history(ctx):
            raise ValueError("not enough history for a Minute Black bracket")
        error = self.data_error(ctx)
        if error:
            raise ValueError(error)
        signal = self._signal(ctx)
        if signal.direction != direction:
            raise ValueError("Minute Black signal is absent or direction changed")
        reason = self._entry_error(ctx, entry, signal)
        if reason:
            raise ValueError(reason)
        sign = Decimal(1) if direction == "LONG" else Decimal(-1)
        buffer = self._volatility(ctx) * self.stop_buffer_atr
        reference = Decimal(str(signal.stop_reference)) - sign * buffer
        distance = max((entry - reference) * sign, entry * MIN_STOP_FRACTION)
        return Levels(
            stop=entry - sign * distance, target=entry + sign * distance * MIN_REWARD_TO_RISK
        )
