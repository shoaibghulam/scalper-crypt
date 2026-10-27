"""CTK 1 and CTK 5 - the CTK PO3 demo EA, ported to crypto perpetuals.

Source: the MQL5 EA "CTK 5 - M1 - standalone CTK PO3 demo EA", which replays
the alert stream of a Pine indicator. Power of Three: accumulation, then
manipulation, then distribution. The trade FADES the manipulation.

    1. ACCUMULATION  the range of the last `length` bars compresses inside
                     `accumulationMult` x ATR(50). A box is drawn at the
                     range extremes +/- 0.5 ATR.
    2. BREAK         price leaves the box. The setup arms.
    3. MANIPULATION  price pushes a further `manipulationMult` x ATR beyond
                     the box edge. That push is faded:
                        break ABOVE the top  -> SHORT
                        break BELOW the bottom -> LONG
    4. LEVELS        dynamic: stop `riskMult` x ATR away, target the same
                     distance x 0.86. Or fixed: 0.4% stop, 0.3% target.

Defaults ported from the EA (SMALL_MANIPULATION, RISK_NORMAL, WICK_BREAKOUT,
DYNAMIC_LEVELS): length 40, accumulation 5.0, manipulation 0.6, risk 5.0.

THE REWARD IS SMALLER THAN THE RISK. 0.86 dynamic, 0.75 fixed. This is not a
mistake in the source and it is not corrected here - it is what makes the
strategy what it is. A PO3 fade wins often and small; raising the target to
this platform's usual 1.5 would not be a safer version of CTK, it would be a
different strategy that happens to share the entry.

What it costs is stated rather than hidden. A round trip on Binance USD-M
takes 0.090% of notional, so:

    broker, fixed 0.4% stop, 0.75R   ->  57.1% of trades must win
    Binance, fixed 0.4% stop, 0.75R  ->  70.0%
    Binance, 5xATR ~0.5% stop, 0.86R ->  63.4%
    Binance, 5xATR ~1.0% stop, 0.86R ->  58.6%

On a fixed-commission broker the fee is a rounding error against the stop; on
a percentage-fee venue it is a fifth of it, and the required win rate moves
from "more often than not" to "two times in three". That is the honest
translation of "it works perfectly on gold".

Because the ratio is below the platform default, this strategy declares its own
`min_reward_to_risk`. The alternative was a strategy that appears in the scanner
and can never open a trade, which is a worse kind of dishonest.

ATR IS WILDER. The EA seeds a simple mean over the first 50 true ranges and
then smooths. `indicators.core.atr` does the same, so unlike the earlier RSI EA
this one does NOT need its own copy.

THE STATE MACHINE HAS NO RESET PATH, and that is carried over deliberately. In
the source, state 2 (armed, waiting for manipulation) can only be left by a
signal. If price leaves the box but never pushes far enough, the replay stops
producing signals for the rest of that pass. Verified by simulation: a box
topping at 10.5 with a manipulation threshold of 11.1, broken by a bar peaking
at 11.0, produces zero signals over the following 200 bars. Whether the Pine
source resets there is unknown from the MQL alone, so the behaviour is matched
rather than silently improved - and recorded in `assumptions` so it can be
checked against the indicator.

CTK 1 reads 1-minute candles, CTK 5 reads 5-minute candles.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise

from scalper.core.fees import MIN_STOP_FRACTION
from scalper.core.filters import Rounding, round_to_tick
from scalper.exchange.market_data import Candle
from scalper.indicators.core import atr_series, ema_series
from scalper.strategies.base import Levels, Setup, Strategy, StrategyContext

#: ATRLength in the EA.
ATR_LENGTH = 50

#: SMALL_MANIPULATION defaults.
ACCUMULATION_BARS = 40
ACCUMULATION_MULT = 5.0
MANIPULATION_MULT = 0.6

#: The box is drawn half an ATR outside the accumulation range.
BOX_PADDING_ATR = 0.5

#: RISK_NORMAL.
RISK_MULT = 5.0

#: SourceRewardRisk. Deliberately below one - see the module docstring.
SOURCE_REWARD_RISK = Decimal("0.86")

#: How far back the replay walks. The EA uses 4800 bars; the scanner supplies
#: far fewer, so this bounds the work rather than defining it.
REPLAY_BARS = 400

#: Lookback for the regime filter some variants apply. Kept well inside the
#: 200-bar context the scanner supplies, so most of the replay window has a
#: real EMA behind it rather than a seed.
TREND_PERIOD = 100


@dataclass(frozen=True)
class CtkParams:
    """Every knob a CTK variant may turn.

    The defaults ARE the source EA, so `CtkParams()` reproduces CTK 1 and
    CTK 5 exactly. Variants exist because the ported strategy loses money for
    a reason that is arithmetic rather than mysterious: at 0.86 reward to risk
    a trade must win about 59% of the time to break even after fees, and the
    fade wins about 50%. Each knob below attacks one term of that inequality
    instead of being a parameter sweep.
    """

    #: Reward to risk. Raising it lowers the win rate the strategy needs.
    reward_risk: Decimal = SOURCE_REWARD_RISK
    accumulation_bars: int = ACCUMULATION_BARS
    #: How tight the range must be to count as accumulation. Lower is stricter.
    accumulation_mult: float = ACCUMULATION_MULT
    #: How far beyond the box price must push before the fade is taken.
    manipulation_mult: float = MANIPULATION_MULT
    #: Stop distance in ATR. Wider stops shrink fees as a share of the risk.
    risk_mult: float = RISK_MULT
    #: Require the bar to CLOSE beyond the level rather than merely wick past.
    close_confirmed: bool = False
    #: Let the state machine return to searching when price re-enters the box.
    #: The source has no such path; see the module docstring.
    reset_when_unarmed: bool = False
    #: Only fade with the longer-term trend: dips bought in an uptrend, pops
    #: sold in a downtrend. 0 disables.
    trend_filter: bool = False
    #: Manipulation bar volume as a multiple of the box average. 0 disables.
    min_volume_ratio: float = 0.0
    #: Close the trade after this many bars. 0 disables.
    max_hold_bars: int = 0


SOURCE_PARAMS = CtkParams()


@dataclass(frozen=True)
class _Signal:
    direction: str
    stop: float
    target: float
    note: str


def _rolling_extremes(
    bars: list[Candle], length: int
) -> tuple[list[float], list[float]]:
    """Highest high and lowest low of the trailing `length` bars, per index.

    The state machine asks for this on nearly every bar of every window, and
    computing it with max()/min() over a slice made the box scan a third of
    the whole run. A monotonic deque gives the identical values - these are
    exact float comparisons, not arithmetic - in one pass instead of one scan
    per bar.
    """
    highs: list[float] = []
    lows: list[float] = []
    high_idx: deque[int] = deque()
    low_idx: deque[int] = deque()
    for i, bar in enumerate(bars):
        first = i - length + 1
        while high_idx and high_idx[0] < first:
            high_idx.popleft()
        while low_idx and low_idx[0] < first:
            low_idx.popleft()
        while high_idx and bars[high_idx[-1]].high <= bar.high:
            high_idx.pop()
        while low_idx and bars[low_idx[-1]].low >= bar.low:
            low_idx.pop()
        high_idx.append(i)
        low_idx.append(i)
        highs.append(bars[high_idx[0]].high)
        lows.append(bars[low_idx[0]].low)
    return highs, lows


def _replay(
    bars: list[Candle],
    atr: list[float],
    params: CtkParams = SOURCE_PARAMS,
    trend: list[float] | None = None,
) -> _Signal | None:
    """The EA's `LatestSignal`, including its state machine.

    Returns a signal only when the manipulation lands on the FINAL closed bar,
    exactly as the source does with `if(i==count-1)`. Earlier signals are
    replayed so the virtual position state stays consistent, then discarded.

    With `SOURCE_PARAMS` this is the EA. Every filter below is inert at its
    default, so a variant differs from the source only where it says it does.
    """
    state = 0
    end_index = -1
    entry_index = -1
    virtual_side = 0
    top = bottom = entry = stop = target = 0.0
    last = len(bars) - 1
    reward = float(params.reward_risk)
    box_high, box_low = _rolling_extremes(bars, params.accumulation_bars)

    for i in range(max(ATR_LENGTH - 1, len(bars) - REPLAY_BARS), len(bars)):
        # WICK_BREAKOUT is the EA default: wicks count. A close-confirmed
        # variant instead asks the bar to settle beyond the level, which
        # ignores the spikes that a fade gets caught by.
        if params.close_confirmed:
            high_break = low_break = bars[i].close
        else:
            high_break, low_break = bars[i].high, bars[i].low

        if state == 0:
            highest = box_high[i]
            lowest = box_low[i]
            if highest - lowest <= atr[i] * params.accumulation_mult:
                state = 1
                end_index = i
                top = highest + atr[i] * BOX_PADDING_ATR
                bottom = lowest - atr[i] * BOX_PADDING_ATR

        if state == 1:
            if high_break > top or low_break < bottom:
                state = 2
            else:
                end_index = i

        side = 0
        if state == 2 and i > end_index:
            # The fade. A push above the box is sold, a push below is bought.
            if high_break > top + atr[i] * params.manipulation_mult:
                side = -1
            elif low_break < bottom - atr[i] * params.manipulation_mult:
                side = 1
            elif params.reset_when_unarmed and bottom <= bars[i].close <= top:
                # The source can never leave this state without a signal. When
                # the reset is enabled, price settling back inside the box
                # means the break failed and the hunt starts again.
                state = 0

        if side != 0 and params.min_volume_ratio > 0:
            window = bars[max(0, i - params.accumulation_bars + 1) : i]
            average = sum(b.volume for b in window) / len(window) if window else 0.0
            # A sweep that matters shows up in volume. Quiet drift past the
            # edge is the kind of break that keeps going.
            if average <= 0 or bars[i].volume < average * params.min_volume_ratio:
                side = 0

        # Fade WITH the larger trend only: buy dips in an uptrend, sell pops
        # in a downtrend. Fading a trend that is still running is where a
        # reversion strategy does most of its losing.
        if (
            side != 0
            and params.trend_filter
            and trend is not None
            and (side == 1) != (bars[i].close > trend[i])
        ):
            side = 0

        if side != 0:
            state = 3
            virtual_side = side
            entry_index = i
            entry = bars[i].close
            stop = entry - side * atr[i] * params.risk_mult
            target = entry + side * atr[i] * params.risk_mult * reward
            if i == last:
                direction = "LONG" if side == 1 else "SHORT"
                edge = bottom if side == 1 else top
                return _Signal(
                    direction=direction,
                    stop=stop,
                    target=target,
                    note=(
                        f"PO3 {direction.lower()}: price manipulated "
                        f"{'below' if side == 1 else 'above'} the "
                        f"{bottom:.6g}-{top:.6g} accumulation box by more than "
                        f"{params.manipulation_mult} ATR, faded from {edge:.6g}"
                    ),
                )

        if state == 3 and i > entry_index:
            if virtual_side > 0:
                hit = bars[i].high >= target or bars[i].low <= stop
            else:
                hit = bars[i].low <= target or bars[i].high >= stop
            if hit:
                state = 0
                virtual_side = 0

    return None


class _CtkPo3(Strategy):
    """The shared port. Subclasses choose which candle series to read."""

    interval_ms: int = 60_000
    max_signal_age_s = 20

    #: The source EA. Variants override this and nothing else.
    params: CtkParams = SOURCE_PARAMS

    #: Below the platform default of 1.5, because the source is. Declared here
    #: rather than silently raising the target, which would be a different
    #: strategy wearing this one's name. Variants that raise the reward raise
    #: this with it, since the two must always agree.
    min_reward_to_risk = SOURCE_REWARD_RISK

    def scheduled_exit(self, opened_at_ms: int) -> int | None:
        """Deadline for variants that cap how long a fade is given to work."""
        if not self.params.max_hold_bars:
            return None
        return opened_at_ms + self.params.max_hold_bars * self.interval_ms

    def _bars(self, ctx: StrategyContext) -> list[Candle]:
        raise NotImplementedError

    def _required(self) -> int:
        return ATR_LENGTH + self.params.accumulation_bars + 2

    def _signal(self, ctx: StrategyContext) -> _Signal:
        if not self.has_enough_history(ctx):
            raise ValueError("insufficient history")
        if error := self.data_error(ctx):
            raise ValueError(error)

        bars = self._bars(ctx)
        if len(bars) < self._required():
            raise ValueError("insufficient history")

        window = bars[-max(self._required(), REPLAY_BARS) :]
        if any(
            b.open_time % self.interval_ms
            or b.close_time != b.open_time + self.interval_ms - 1
            for b in window
        ):
            raise ValueError("invalid candle duration")
        if any(b.open_time - a.open_time != self.interval_ms for a, b in pairwise(window)):
            raise ValueError("discontinuous candles")

        closes = [b.close for b in window]
        volatility = atr_series(
            [b.high for b in window],
            [b.low for b in window],
            closes,
            ATR_LENGTH,
        )
        trend = self._trend(closes) if self.params.trend_filter else None
        signal = _replay(window, volatility, self.params, trend)
        if signal is None:
            raise ValueError("no PO3 manipulation on the last closed bar")
        if signal.stop <= 0 or signal.target <= 0:
            raise ValueError("non-positive source level")
        return signal

    @staticmethod
    def _trend(closes: list[float]) -> list[float]:
        """An EMA aligned index-for-index with the bars.

        `ema_series` starts at its seed, so it is shorter than its input by
        period-1. Indexing it with a bar index would silently read the EMA of
        an earlier bar - a look-ahead in the wrong direction. The head is
        padded with the seed so position i really is the EMA at bar i.
        """
        period = min(TREND_PERIOD, len(closes))
        values = ema_series(closes, period)
        return [values[0]] * (len(closes) - len(values)) + values

    def levels(self, ctx: StrategyContext, entry: Decimal, direction: str) -> Levels:
        signal = self._signal(ctx)
        if direction != signal.direction or not entry.is_finite() or entry <= 0:
            raise ValueError("invalid entry or direction")

        sign = Decimal(1 if direction == "LONG" else -1)
        stop = Decimal(str(signal.stop))
        distance = (entry - stop) * sign
        if distance <= 0:
            raise ValueError("the source stop is already the wrong side of the entry")

        # The one place the source is overridden. Its stop is derived from ATR
        # with no notion of a percentage fee, so on a quiet symbol it can land
        # inside the distance at which fees cost more than the trade risks.
        floor = entry * MIN_STOP_FRACTION
        if distance < floor:
            distance = floor
            stop = entry - sign * distance

        # The source ratio, applied to whatever stop survived the floor.
        target = entry + sign * distance * self.min_reward_to_risk
        if min(stop, target) <= 0:
            raise ValueError("non-positive protection price")
        return Levels(stop=stop, target=target)

    def execution_levels(
        self, ctx: StrategyContext, entry: Decimal, direction: str, tick_size: Decimal
    ) -> Levels:
        levels = self.levels(ctx, entry, direction)
        sign = Decimal(1 if direction == "LONG" else -1)
        # Stop away from entry, target toward it: rounding may only widen the
        # risk or shorten the reward, never flatter the ratio.
        stop = round_to_tick(
            levels.stop, tick_size, Rounding.DOWN if sign == 1 else Rounding.UP
        )
        target = round_to_tick(
            entry + sign * abs(entry - stop) * self.min_reward_to_risk,
            tick_size,
            Rounding.DOWN if sign == 1 else Rounding.UP,
        )
        if (target - entry) * sign <= 0:
            raise ValueError("rounded target collapsed onto the entry")
        return Levels(stop, target)

    def assess(self, ctx: StrategyContext) -> Setup:
        try:
            signal = self._signal(ctx)
            entry = ctx.book.ask if signal.direction == "LONG" else ctx.book.bid
            self.levels(ctx, Decimal(str(entry)), signal.direction)
        except ValueError as exc:
            return Setup.waiting(str(exc))
        return Setup.entry(signal.direction, signal.note)


SHARED_ASSUMPTIONS: tuple[str, ...] = (
    "Research candidate ported from the CTK PO3 MT5 EA; no demonstrated live "
    "profitability on crypto.",
    "Power of Three: a 40-bar range compressing inside 5 x ATR(50) forms a box "
    "at +/- 0.5 ATR; once price leaves it, a further 0.6 ATR push is FADED - "
    "sold above the box, bought below.",
    "Reward to risk is 0.86, the source value, and is NOT raised to the "
    "platform default of 1.5. Raising it would be a different strategy sharing "
    "this entry. This strategy therefore declares its own minimum.",
    "That ratio requires roughly 63% of trades to win after Binance fees, "
    "against 57% on a fixed-commission broker. A round trip costs 0.090% of "
    "notional here versus a flat ticket fee there, which is a fifth of a "
    "0.5% stop rather than a rounding error.",
    "Stop is 5 x ATR(50) as the source specifies, raised only where it would "
    "fall inside the fee floor; the source ratio is then applied to whatever "
    "stop survived.",
    "The source state machine has NO path from armed back to searching: if "
    "price leaves the box without pushing far enough, the replay stops "
    "signalling for the rest of that pass. Verified by simulation and matched "
    "rather than improved - check it against the Pine indicator.",
    "Wick breakouts, not closes, and the SMALL_MANIPULATION / RISK_NORMAL "
    "defaults from the EA inputs.",
    "The EA's broker guards - spread points, stops level, demo binding, "
    "per-symbol locks - are replaced by this platform's own economic gates.",
)


class Ctk1(_CtkPo3):
    id = "ctk-1"
    name = "CTK 1"
    summary = (
        "CTK PO3 on 1-minute candles: fades the manipulation push beyond a "
        "40-bar accumulation box. Stop 5 x ATR(50), target 0.86R - the source "
        "ratio, which needs roughly 63% of trades to win after fees."
    )
    interval_ms = 60_000
    history_1m = 120
    history_5m = 20
    assumptions = (*SHARED_ASSUMPTIONS, "Trades the 1-minute series.")

    def _bars(self, ctx: StrategyContext) -> list[Candle]:
        return ctx.candles_1m


class Ctk5(_CtkPo3):
    id = "ctk-5"
    name = "CTK 5"
    summary = (
        "CTK PO3 on 5-minute candles: fades the manipulation push beyond a "
        "40-bar accumulation box. Stop 5 x ATR(50), target 0.86R - the source "
        "ratio, which needs roughly 63% of trades to win after fees."
    )
    interval_ms = 300_000
    history_1m = 40
    history_5m = 120
    assumptions = (
        *SHARED_ASSUMPTIONS,
        "Trades the 5-minute series. Accumulation boxes form less often on a "
        "slower series, so CTK 5 signals less than CTK 1.",
    )

    def _bars(self, ctx: StrategyContext) -> list[Candle]:
        return ctx.candles_5m
