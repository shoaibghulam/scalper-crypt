"""Video-inspired sweep/reclaim strategy with an explicit FVG zone proxy.

The source shows discretionary demand, volume confirmation, a one-ATR stop,
a recent-high target and a discretionary break-even adjustment. FVG detection,
mirrored shorts and numerical thresholds remain engineering adaptations.
No positive expectancy or guaranteed holding time has been established.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from statistics import median

from scalper.core.fees import MIN_STOP_FRACTION
from scalper.engine.plan import MIN_REWARD_TO_RISK
from scalper.exchange.market_data import Candle
from scalper.indicators.core import atr
from scalper.strategies.base import Levels, Setup, Strategy, StrategyContext

#: Bars used to decide the trend. Long enough that one impulse does not define
#: direction, short enough to still describe the last half hour.
TREND_LOOKBACK = 30

#: A swing needs this many higher lows either side. Two is the smallest number
#: that excludes single-bar noise on a 1m chart.
SWING_WINDOW = 2

#: How far back to look for the imbalance and the swept low. Bounds every
#: search in this module, which is what keeps the whole assessment linear in
#: history rather than quadratic.
ZONE_SEARCH = 25

#: How far beyond the zone edge the swept low may sit, as a fraction of price.
#: The video says "just below" and never quantifies it; bounded so an unrelated
#: low from much lower down is not treated as this zone's liquidity.
MAX_SWING_BEYOND_ZONE = Decimal("0.005")

#: The wick must be at least this much of the candle's range. "Sharp rejection"
#: needs a number, and the wick is what makes a rejection visible.
REJECTION_WICK_FRACTION = 0.5

#: Volume against the median of recent bars.
VOLUME_MULTIPLE = 1.3
VOLUME_LOOKBACK = 20

#: Activity-filter tuning horizon. ATR is not return standard deviation;
#: this heuristic does not establish an expected or guaranteed exit time.
TARGET_HOLD_MINUTES = 30
ATR_PERIOD = 14


@dataclass(frozen=True)
class _Signal:
    """A complete setup, or the reason there is not one."""

    direction: str | None
    note: str
    swept_level: float = 0.0
    sweep_extreme: float = 0.0
    target: float = 0.0
    volatility: float = 0.0

    @property
    def ready(self) -> bool:
        return self.direction is not None


def reachability_floor(hold_minutes: int = TARGET_HOLD_MINUTES) -> float:
    """Heuristic minimum 1m ATR fraction for the activity filter.

    Exposed rather than inlined because it is the number most worth arguing
    about, and an operator should be able to see what it evaluates to.
    """
    return float(MIN_STOP_FRACTION) / math.sqrt(hold_minutes)


def _confirmed_swing(
    candles: list[Candle], i: int, last: int, *, low: bool, window: int = SWING_WINDOW
) -> bool:
    """A pivot whose confirming bars all sit strictly before the trigger bar.

    The `i + window < last` condition is the important one and it is not an
    off-by-one nicety. Without it, a pivot at `last - 2` would be confirmed by
    the trigger bar itself: the swing low would exist only because the
    rejection bar exists, and the strategy would be reasoning in a circle -
    finding the level in the same bar it claims swept that level.
    """
    if i - window < 0 or i + window >= last:
        return False
    if low:
        value = candles[i].low
        return all(candles[j].low > value for j in range(i - window, i + window + 1) if j != i)
    value = candles[i].high
    return all(candles[j].high < value for j in range(i - window, i + window + 1) if j != i)


def _trend(candles: list[Candle]) -> str | None:
    """Higher highs AND higher lows, or the inverse. None when neither holds.

    Both ends must move. "Clearly here we are making higher highs" is not a
    rule a machine can apply, and highs alone accept a broadening pattern that
    is making higher highs and lower lows at the same time - an expansion of
    range rather than a trend.

    Half against half rather than first bar against last, so one spike at
    either end does not decide it.
    """
    window = candles[-TREND_LOOKBACK:]
    if len(window) < TREND_LOOKBACK:
        return None

    mid = len(window) // 2
    early, late = window[:mid], window[mid:]

    early_high, late_high = max(c.high for c in early), max(c.high for c in late)
    early_low, late_low = min(c.low for c in early), min(c.low for c in late)

    if late_high > early_high and late_low > early_low:
        return "LONG"
    if late_high < early_high and late_low < early_low:
        return "SHORT"
    return None


def _gap(candles: list[Candle], end: int, direction: str) -> tuple[int, float, float] | None:
    """The most recent unfilled 3-bar imbalance at or before `end`.

    A bullish proxy has bar1.high below bar3.low. This OHLC relationship does
    not establish that resting orders remain unfilled. Zone validity here
    means no later close crossed the far edge; wick penetration is allowed.

    Bounded by ZONE_SEARCH, so the nested scan is a constant amount of work
    rather than growing with the history handed in.
    """
    floor_search = max(1, end - ZONE_SEARCH)
    for i in range(end, floor_search, -1):
        if i + 1 > end:
            continue
        if direction == "LONG":
            low_edge, high_edge = candles[i - 1].high, candles[i + 1].low
            if low_edge >= high_edge:
                continue
            if any(c.close < low_edge for c in candles[i + 2 : end + 2]):
                continue
        else:
            high_edge, low_edge = candles[i - 1].low, candles[i + 1].high
            if high_edge <= low_edge:
                continue
            if any(c.close > high_edge for c in candles[i + 2 : end + 2]):
                continue
        return i, low_edge, high_edge
    return None


def _volume_confirmed(candles: list[Candle], i: int) -> bool:
    """The bar traded meaningfully more than recent bars.

    Median rather than the mean that `indicators.rvol` uses. One 10x spike in
    a 20-bar lookback lifts a mean by roughly 50%, which would disqualify every
    genuine rise that followed it. Volume on 1m crypto is spiky enough that
    this happens often, and a robust centre is worth the small duplication.
    """
    recent = candles[max(0, i - VOLUME_LOOKBACK) : i]
    if not recent:
        return False
    baseline = median(c.volume for c in recent)
    return baseline > 0 and candles[i].volume >= baseline * VOLUME_MULTIPLE


def _volume_ratio(candles: list[Candle], i: int) -> float:
    recent = candles[max(0, i - VOLUME_LOOKBACK) : i]
    baseline = median(c.volume for c in recent) if recent else 0.0
    return candles[i].volume / baseline if baseline > 0 else 0.0


def _rejection_wick(candle: Candle, direction: str) -> bool:
    """Require a true wick; the candle body is not part of the wick."""
    span = candle.high - candle.low
    if span <= 0:
        return False
    recovered = (
        min(candle.open, candle.close) - candle.low
        if direction == "LONG"
        else candle.high - max(candle.open, candle.close)
    )
    return recovered / span >= REJECTION_WICK_FRACTION


class MinuteLegendV1(Strategy):
    id = "1-minute-legend-v1"
    name = "1 Minute Legend v1"
    summary = (
        "Trend-aligned liquidity sweep and reclaim with a true rejection wick, "
        "volume confirmation and a recent swing target. Demand is approximated "
        "by a three-candle fair-value gap; this is an adaptation of the video."
    )
    assumptions = (
        "Demand is approximated by a three-bar FVG, not a formula established by the video.",
        "The one-ATR stop uses Wilder ATR14 on closed 1m candles. It is widened "
        "to the sweep extreme and the platform fee-distance floor when necessary.",
        "The recent swing target must offer at least 1.5R at the executable entry; "
        "an invalid setup is rejected rather than assigned a replacement target.",
        "Break-even management is not implemented: the source does not quantify "
        "its trigger. Its effect on expectancy has not been measured.",
        "Trend uses higher highs and higher lows over 30 closed bars. Shorts mirror "
        "the long example and have not been validated by the source.",
        "A confirmed swing must lie within 0.5% beyond the zone and remain unswept "
        "until the trigger. Rejection wick must be at least half the candle range.",
        "Volume must reach 1.3 times the preceding 20-bar median. The ATR-based "
        "activity gate is a heuristic, not a prediction of time to exit.",
        "Signal expires after 20 seconds; execution must retain the reclaimed "
        "level and the structural target. These safeguards are engineering choices.",
    )
    max_signal_age_s = 20

    #: Trend lookback plus the zone search plus room for the swing windows and
    #: the ATR period.
    history_1m = 60
    history_5m = 20

    def _signal(self, ctx: StrategyContext) -> _Signal:
        candles = ctx.candles_1m
        last = len(candles) - 1

        # Reachability first: it is the cheapest check and it excludes the
        # largest share of symbols.
        volatility = atr(
            [c.high for c in candles],
            [c.low for c in candles],
            [c.close for c in candles],
            period=ATR_PERIOD,
        )
        price = candles[last].close
        if price <= 0:
            return _Signal(None, "no usable price")
        floor_ = reachability_floor()
        if volatility / price < floor_:
            return _Signal(
                None,
                f"too quiet: 1m ATR {volatility / price:.3%} of price, below the "
                f"{floor_:.3%} heuristic activity threshold",
            )

        direction = _trend(candles)
        if direction is None:
            return _Signal(None, "no trend: highs and lows are not both rising")

        # The rejection must be the last CLOSED bar. market_data drops the
        # forming bar before a strategy sees it, so index -1 is closed; looking
        # further forward would read a bar that does not exist yet.
        bar = candles[last]

        # The zone must be established on bars before the rejection, or the
        # rejection bar becomes the third leg of its own imbalance.
        gap = _gap(candles, last - 2, direction)
        if gap is None:
            return _Signal(None, "no unfilled imbalance to defend the level")
        _, gap_floor, gap_ceiling = gap

        # The trigger bar is a later bar and its close is the entry price, so a
        # rejection that closes through the far edge spends the zone at the
        # very moment the strategy would enter on it. Every other bar was
        # checked inside _gap; this is the one that matters most.
        spent = bar.close < gap_floor if direction == "LONG" else bar.close > gap_ceiling
        if spent:
            return _Signal(None, "the rejection closed through the imbalance, spending it")

        edge = gap_floor if direction == "LONG" else gap_ceiling
        limit = float(Decimal(str(edge)) * MAX_SWING_BEYOND_ZONE)

        swept_level: float | None = None
        swept_index = -1
        for i in range(last - 1, max(0, last - ZONE_SEARCH), -1):
            if direction == "LONG":
                if not _confirmed_swing(candles, i, last, low=True):
                    continue
                if candles[i].low < edge and edge - candles[i].low <= limit:
                    swept_level = candles[i].low
                    swept_index = i
                    break
            else:
                if not _confirmed_swing(candles, i, last, low=False):
                    continue
                if candles[i].high > edge and candles[i].high - edge <= limit:
                    swept_level = candles[i].high
                    swept_index = i
                    break
        if swept_level is None:
            side = "low" if direction == "LONG" else "high"
            return _Signal(None, f"no clear {side} resting just beyond the imbalance")

        if any(
            c.low < swept_level if direction == "LONG" else c.high > swept_level
            for c in candles[swept_index + 1 : last]
        ):
            return _Signal(None, "the liquidity reference was already swept")

        swept = bar.low < swept_level if direction == "LONG" else bar.high > swept_level
        if not swept:
            return _Signal(None, "the level has not been swept yet")

        reclaimed = bar.close > swept_level if direction == "LONG" else bar.close < swept_level
        if not reclaimed:
            # The video's own invalidation: without the rejection the level did
            # not hold, and this is a breakdown rather than a sweep.
            return _Signal(None, "swept but closed through: the level did not hold")

        if not _rejection_wick(bar, direction):
            return _Signal(None, "closed back but without a rejection wick")

        if not _volume_confirmed(candles, last):
            return _Signal(None, "rejection is not confirmed by a rise in volume")

        target = self._structural_target(candles, last, direction, bar.close)
        if target is None:
            return _Signal(None, "no recent swing to target")

        sweep_extreme = bar.low if direction == "LONG" else bar.high
        entry = Decimal(str(bar.close))
        stop = self._stop_for(entry, sweep_extreme, direction, volatility)
        risk = abs(entry - stop)
        # SIGNED, not absolute. With abs() a target on the wrong side of the
        # entry scored as a reward, and scored better the further wrong it was.
        signed = Decimal(str(target)) - entry
        reward = signed if direction == "LONG" else -signed
        if risk <= 0 or reward <= 0:
            return _Signal(None, "the nearest swing is on the wrong side to target")
        if reward / risk < MIN_REWARD_TO_RISK:
            # Declined rather than traded at a ratio that cannot clear fees.
            return _Signal(
                None,
                f"target only {reward / max(risk, Decimal('1E-12')):.2f}R away "
                f"once the stop clears its fees; {MIN_REWARD_TO_RISK}R is the "
                f"minimum",
            )

        return _Signal(
            direction=direction,
            note=(
                f"swept {swept_level:.6g} "
                f"{'beneath' if direction == 'LONG' else 'above'} the imbalance "
                f"{gap_floor:.6g}-{gap_ceiling:.6g} and closed back on "
                f"{_volume_ratio(candles, last):.1f}x median volume"
            ),
            swept_level=swept_level,
            sweep_extreme=sweep_extreme,
            target=target,
            volatility=volatility,
        )

    def _structural_target(
        self, candles: list[Candle], last: int, direction: str, reference: float
    ) -> float | None:
        """The most recent swing BEYOND the reference price.

        The beyond check is the important half. `_confirmed_swing` compares
        strictly, so two adjacent bars sharing a high disqualify the rally top
        as a pivot - which happens constantly on a tick grid. The scan then
        walks back and returns an older, LOWER pivot, and a long ends up with a
        take-profit underneath its own stop-loss.

        That is not hypothetical: it was reported as ready to the dashboard,
        and it cleared the reward-to-risk gate because the reward was measured
        with abs(), so the further wrong the target was the better it scored.
        """
        for i in range(last - 1, max(0, last - ZONE_SEARCH), -1):
            if direction == "LONG":
                if _confirmed_swing(candles, i, last, low=False) and (candles[i].high > reference):
                    return candles[i].high
            elif _confirmed_swing(candles, i, last, low=True) and (candles[i].low < reference):
                return candles[i].low
        return None

    def _stop_for(
        self, entry: Decimal, sweep_extreme: float, direction: str, volatility: float = 0.0
    ) -> Decimal:
        """Cover one ATR, the sweep extreme and the fee floor, whichever is further out.

        Taking the structural level literally is what the video does, and on a
        1-minute chart that level is routinely a tenth of a percent away. A
        stop that close pays most of its risk in fees before the trade has had
        a chance to be right.
        """
        floor_distance = entry * MIN_STOP_FRACTION
        structural = abs(entry - Decimal(str(sweep_extreme)))
        distance = max(structural, floor_distance, Decimal(str(volatility)))
        return entry - distance if direction == "LONG" else entry + distance

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
        try:
            self._levels_for(signal, entry, signal.direction)
        except ValueError as exc:
            return Setup.waiting(str(exc))
        return Setup.entry(signal.direction, signal.note)

    def _levels_for(self, signal: _Signal, entry: Decimal, direction: str) -> Levels:
        if not entry.is_finite() or entry <= 0 or signal.direction != direction:
            raise ValueError("Legend signal is absent or direction changed")
        sign = Decimal(1) if direction == "LONG" else Decimal(-1)
        if (entry - Decimal(str(signal.swept_level))) * sign <= 0:
            raise ValueError("executable price lost the reclaimed level")
        stop = self._stop_for(entry, signal.sweep_extreme, direction, signal.volatility)
        target = Decimal(str(signal.target))
        if (target - entry) * sign < abs(entry - stop) * MIN_REWARD_TO_RISK:
            raise ValueError("structural target offers less than 1.5R at executable entry")
        return Levels(stop=stop, target=target)

    def levels(self, ctx: StrategyContext, entry: Decimal, direction: str) -> Levels:
        if not self.has_enough_history(ctx):
            raise ValueError("not enough history for a Legend bracket")
        error = self.data_error(ctx)
        if error:
            raise ValueError(error)
        return self._levels_for(self._signal(ctx), entry, direction)
