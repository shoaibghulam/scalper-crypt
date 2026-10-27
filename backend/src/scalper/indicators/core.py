"""Technical indicators.

Floats, not Decimals, and deliberately so. Decimal is mandatory at the exchange
boundary because a rounding error there becomes a rejected order or a wrong
size. An indicator is a threshold test on noisy market data, where the
difference between 24.9999 and 25.0 carries no meaning, and Decimal arithmetic
across hundreds of bars for hundreds of symbols is slow enough to matter in a
scanner.

ATR, RSI and ADX use Wilder smoothing, because that is what every charting
package means by those names. A simple moving average of true range is a
different indicator wearing the same label, and a strategy tuned against one
behaves differently on the other.

Every function raises rather than returning a value computed from a partial
window. Silently producing a number from insufficient data is how a bot trades
on a signal that does not exist yet.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


def _require(values: Sequence[float], count: int, name: str) -> None:
    if len(values) < count:
        raise ValueError(f"{name} needs at least {count} points, got {len(values)}")


def sma(values: Sequence[float], period: int) -> float:
    _require(values, period, "sma")
    return sum(values[-period:]) / period


def ema(values: Sequence[float], period: int) -> float:
    """Exponential moving average, seeded from the simple average.

    Seeding from the first value instead leaves the series biased toward that
    arbitrary starting point for several periods.
    """
    _require(values, period, "ema")
    multiplier = 2.0 / (period + 1)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = (value - result) * multiplier + result
    return result


def ema_series(values: Sequence[float], period: int) -> list[float]:
    """Every EMA value, for callers that need slope or a crossover history."""
    _require(values, period, "ema")
    multiplier = 2.0 / (period + 1)
    result = sum(values[:period]) / period
    out = [result]
    for value in values[period:]:
        result = (value - result) * multiplier + result
        out.append(result)
    return out


def true_ranges(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]
) -> list[float]:
    """True range includes the gap from the previous close.

    A bar that opens far from the prior close and then barely moves is still a
    volatile bar, and sizing a stop from the bar height alone understates it.
    """
    out = [highs[0] - lows[0]]
    for i in range(1, len(highs)):
        prev_close = closes[i - 1]
        out.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - prev_close),
                abs(lows[i] - prev_close),
            )
        )
    return out


def _wilder(values: Sequence[float], period: int) -> float:
    """Wilder smoothing: an EMA whose multiplier is one over the period."""
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = (result * (period - 1) + value) / period
    return result


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> float:
    _require(closes, period + 1, "atr")
    return _wilder(true_ranges(highs, lows, closes), period)


def rsi(closes: Sequence[float], period: int = 14) -> float:
    _require(closes, period + 1, "rsi")

    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = _wilder(gains, period)
    avg_loss = _wilder(losses, period)

    if avg_loss == 0:
        # No downside at all. Unbounded strength, reported at the ceiling.
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


@dataclass(frozen=True)
class AdxResult:
    adx: float
    plus_di: float
    minus_di: float

    @property
    def is_trending(self) -> bool:
        """At or above 25 is the conventional trending threshold."""
        return self.adx >= 25.0

    @property
    def is_ranging(self) -> bool:
        """Below 18 is ranging. The gap between is deliberately no-trade."""
        return self.adx < 18.0


def adx(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> AdxResult:
    """Average directional index with its directional components.

    ADX measures trend strength without direction; the DI pair supplies
    direction. Both are needed, because a strong downtrend and a strong uptrend
    produce the same ADX.
    """
    _require(closes, period * 2 + 1, "adx")

    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for i in range(1, len(highs)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        # Only the larger move counts, and only when positive. A bar expanding
        # in both directions is inside-bar noise rather than direction.
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)

    trs = true_ranges(highs, lows, closes)[1:]

    # One forward pass, carrying the smoothed values along. An earlier version
    # reseeded and replayed the whole smoothing for every bar, which is
    # quadratic; on a 300-bar series across sixty symbols that dominated the
    # runtime of an entire market scan. The values produced are identical.
    smoothed_tr = sum(trs[:period]) / period
    smoothed_plus = sum(plus_dm[:period]) / period
    smoothed_minus = sum(minus_dm[:period]) / period

    dx_values: list[float] = []

    def record_dx() -> None:
        if smoothed_tr == 0:
            return
        p_di = 100.0 * smoothed_plus / smoothed_tr
        m_di = 100.0 * smoothed_minus / smoothed_tr
        total = p_di + m_di
        if total > 0:
            dx_values.append(100.0 * abs(p_di - m_di) / total)

    record_dx()
    for i in range(period, len(trs)):
        smoothed_tr = (smoothed_tr * (period - 1) + trs[i]) / period
        smoothed_plus = (smoothed_plus * (period - 1) + plus_dm[i]) / period
        smoothed_minus = (smoothed_minus * (period - 1) + minus_dm[i]) / period
        record_dx()

    if smoothed_tr == 0:
        return AdxResult(adx=0.0, plus_di=0.0, minus_di=0.0)

    plus_di = 100.0 * smoothed_plus / smoothed_tr
    minus_di = 100.0 * smoothed_minus / smoothed_tr

    # The length guarantee from _require means the first branch always holds;
    # the fallbacks exist so a future caller relaxing that guarantee degrades
    # rather than raising.
    if len(dx_values) >= period:
        value = _wilder(dx_values, period)
    elif dx_values:
        value = sum(dx_values) / len(dx_values)
    else:
        value = 0.0
    return AdxResult(adx=value, plus_di=plus_di, minus_di=minus_di)


def efficiency_ratio(closes: Sequence[float], period: int = 20) -> float:
    """Net movement divided by the path travelled to achieve it.

    One means a straight line; zero means price finished where it started after
    a great deal of activity. It corroborates ADX, and disagreement between the
    two is treated as a reason not to trade rather than a tie to break.
    """
    _require(closes, period + 1, "efficiency_ratio")
    window = closes[-(period + 1) :]
    net = abs(window[-1] - window[0])
    path = sum(abs(window[i] - window[i - 1]) for i in range(1, len(window)))
    return 0.0 if path == 0 else net / path


def rvol(volumes: Sequence[float], period: int = 20) -> float:
    """Current volume against its recent average.

    Twice the average is the conventional unusual-volume threshold; breakout
    setups want three or more.
    """
    _require(volumes, period + 1, "rvol")
    average = sum(volumes[-(period + 1) : -1]) / period
    return 0.0 if average == 0 else volumes[-1] / average


@dataclass(frozen=True)
class VwapBands:
    vwap: float
    sigma: float

    def upper(self, multiple: float) -> float:
        return self.vwap + multiple * self.sigma

    def lower(self, multiple: float) -> float:
        return self.vwap - multiple * self.sigma

    def sigma_distance(self, price: float) -> float:
        """How many standard deviations price sits from vwap, signed."""
        return 0.0 if self.sigma == 0 else (price - self.vwap) / self.sigma


def vwap_bands(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
) -> VwapBands:
    """Volume weighted average price and its dispersion.

    Anchored by the caller, which passes only the bars since the anchor. The
    crypto convention is midnight UTC, because there is no session open.

    Dispersion is the volume weighted standard deviation of typical price
    around vwap, so a band is a genuine statistical distance rather than a
    fixed percentage that means different things on different symbols.
    """
    total_volume = sum(volumes)
    if total_volume <= 0:
        raise ValueError("vwap needs positive total volume")

    typical = [(highs[i] + lows[i] + closes[i]) / 3.0 for i in range(len(closes))]
    vwap = sum(typical[i] * volumes[i] for i in range(len(typical))) / total_volume
    variance = (
        sum(volumes[i] * (typical[i] - vwap) ** 2 for i in range(len(typical)))
        / total_volume
    )
    return VwapBands(vwap=vwap, sigma=math.sqrt(max(variance, 0.0)))


def percentile_rank(series: Sequence[float], value: float) -> float:
    """Where a value sits in its own distribution, from 0 to 100.

    Used instead of absolute thresholds so one filter works on a symbol priced
    in thousands and another priced in fractions of a cent, with no per-symbol
    tuning.
    """
    if not series:
        raise ValueError("percentile_rank needs a non-empty series")
    if len(series) == 1:
        return 50.0
    below = sum(1 for item in series if item < value)
    return min(100.0, 100.0 * below / (len(series) - 1))


@dataclass(frozen=True)
class HeikinAshiCandle:
    """One smoothed candle.

    Deliberately not a Candle. These carry no volume and no timestamps because
    they are not market data: HA_open is an average of two previous averages,
    so it is a level the market never traded at. Keeping the type distinct is
    what stops one being handed to something that places orders.
    """

    open: float
    high: float
    low: float
    close: float

    @property
    def is_up(self) -> bool:
        return self.close > self.open

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def span(self) -> float:
        return self.high - self.low

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low


def heikin_ashi(
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> list[HeikinAshiCandle]:
    """Heikin Ashi candles, in one forward pass.

        HA_close = (open + high + low + close) / 4
        HA_open  = (previous HA_open + previous HA_close) / 2
        HA_high  = max(high, HA_open, HA_close)
        HA_low   = min(low,  HA_open, HA_close)

    Single pass because the definition is recursive: each open depends on the
    previous candle, which invites recomputing the whole chain per bar. That is
    how this module previously shipped a quadratic ATR, and on a sixty-symbol
    scan it dominated the runtime.

    The first open seeds from the midpoint of its own real bar, since no
    previous HA candle exists and inventing one would bias every candle after
    it.
    """
    if not (len(opens) == len(highs) == len(lows) == len(closes)):
        raise ValueError("open, high, low and close series must be the same length")
    if not closes:
        return []

    out: list[HeikinAshiCandle] = []
    prev_open = (opens[0] + closes[0]) / 2.0
    prev_close = (opens[0] + highs[0] + lows[0] + closes[0]) / 4.0

    for i in range(len(closes)):
        ha_close = (opens[i] + highs[i] + lows[i] + closes[i]) / 4.0
        ha_open = prev_open if i == 0 else (prev_open + prev_close) / 2.0
        out.append(
            HeikinAshiCandle(
                open=ha_open,
                high=max(highs[i], ha_open, ha_close),
                low=min(lows[i], ha_open, ha_close),
                close=ha_close,
            )
        )
        prev_open, prev_close = ha_open, ha_close
    return out


def atr_series(
    highs: list[float], lows: list[float], closes: list[float], period: int
) -> list[float]:
    """Wilder ATR at every bar, in one forward pass.

    Seeded with a simple mean of the first `period` true ranges and smoothed
    thereafter, which is what the CTK EA does and what TradingView plots. Bars
    before the seed are returned as 0 rather than a partial average: a
    half-warmed ATR compared against a range threshold would fire setups that
    the indicator never showed.

    Single pass. Recomputing the smoothing per bar is how this module twice
    shipped a quadratic indicator.
    """
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("high, low and close series must be the same length")
    if period < 1:
        raise ValueError("ATR period must be at least one")

    out = [0.0] * len(closes)
    running = 0.0
    for i in range(len(closes)):
        true_range = highs[i] - lows[i]
        if i > 0:
            previous = closes[i - 1]
            true_range = max(
                true_range, abs(highs[i] - previous), abs(lows[i] - previous)
            )
        if i < period:
            running += true_range
        if i == period - 1:
            out[i] = running / period
        elif i >= period:
            out[i] = (out[i - 1] * (period - 1) + true_range) / period
    return out
