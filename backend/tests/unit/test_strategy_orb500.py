"""Narrated three-step ORB: hourly bias, 5m breakout, later edge retest."""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from scalper.exchange.market_data import BookTop, Candle, Ticker
from scalper.strategies.base import StrategyContext
from scalper.strategies.orb500 import Backtest500V1


def candle(close, high=None, low=None, open_=None):
    return Candle(
        0,
        close if open_ is None else open_,
        close if high is None else high,
        close if low is None else low,
        close,
        1000,
        59999,
        close * 1000,
        600,
    )


def context(bars):
    price = bars[-1].close
    return StrategyContext(
        "TESTUSDT",
        [candle(100, 101, 99)] * 80,
        bars,
        Ticker("TESTUSDT", price, 1, 500_000_000),
        BookTop("TESTUSDT", price - 0.01, price + 0.01),
    )


def setup_context(day="2026-09-18", short=False):
    bars = []

    def block(count, opening, closing, high, low):
        for i in range(count):
            o = opening + (closing - opening) * i / count
            c = opening + (closing - opening) * (i + 1) / count
            bars.append(candle(c, high, low, o))

    block(60, 101, 100, 101.1, 99.9)  # first rolling hourly candle
    block(60, 99.9, 102, 102.1, 99.8)  # second rolling hourly candle
    block(15, 100, 100, 101, 99)  # pre-breakout range
    block(5, 100.8, 102, 102.2, 100.7)  # breakout
    block(5, 102, 101.2, 102.1, 100.9)  # pullback
    block(5, 101.1, 102.1, 102.2, 100.8)  # engulfing retest
    if short:
        bars = [
            replace(c, open=200 - c.open, close=200 - c.close, high=200 - c.low, low=200 - c.high)
            for c in bars
        ]
    start = int(
        datetime.fromisoformat(day).replace(hour=7, tzinfo=ZoneInfo("America/New_York")).timestamp()
        * 1000
    )
    return context(
        [
            replace(c, open_time=start + i * 60000, close_time=start + i * 60000 + 59999)
            for i, c in enumerate(bars)
        ]
    )


@pytest.mark.parametrize("short", [False, True])
def test_complete_three_step_setup_enters(short):
    setup = Backtest500V1().assess(setup_context(short=short))
    assert setup.ready and setup.direction == ("SHORT" if short else "LONG")


def test_breakout_alone_does_not_enter():
    ctx = setup_context()
    setup = Backtest500V1().assess(context(ctx.candles_1m[:-10]))
    # Enough earlier warm-up must not be confused with enough pattern bars.
    assert not setup.ready


def test_hourly_bias_is_required_not_an_ema_proxy():
    ctx = setup_context()
    bars = ctx.candles_1m.copy()
    bars[60] = replace(bars[60], open=100.5)
    setup = Backtest500V1().assess(context(bars))
    assert not setup.ready and "hourly" in setup.note


def test_retest_must_engulf_not_just_close_above_range():
    ctx = setup_context()
    bars = ctx.candles_1m.copy()
    bars[-5] = replace(bars[-5], open=101.5)
    setup = Backtest500V1().assess(context(bars))
    assert not setup.ready and "engulfing rejection" in setup.note


def test_missing_hourly_reference_candle_fails_closed():
    ctx = setup_context()
    bars = ctx.candles_1m.copy()
    bars[30] = bars[29]
    setup = Backtest500V1().assess(context(bars))
    assert not setup.ready and "consecutive" in setup.note


@pytest.mark.parametrize("short", [False, True])
def test_bracket_retains_opposite_range_edge_and_two_r(short):
    ctx = setup_context(short=short)
    side = "SHORT" if short else "LONG"
    entry = Decimal(str(ctx.price))
    levels = Backtest500V1().levels(ctx, entry, side)
    assert levels.stop == Decimal("101" if short else "99")
    assert abs(levels.target - entry) == 2 * abs(levels.stop - entry)


def test_no_bracket_without_valid_signal():
    ctx = setup_context()
    with pytest.raises(ValueError):
        Backtest500V1().levels(ctx, Decimal(str(ctx.price)), "SHORT")


def test_no_partial_five_minute_confirmation():
    ctx = setup_context()
    bar = ctx.candles_1m[-1]
    ctx = context(
        ctx.candles_1m
        + [replace(bar, open_time=bar.open_time + 60000, close_time=bar.close_time + 60000)]
    )
    setup = Backtest500V1().assess(ctx)
    assert not setup.ready and "closed 5m" in setup.note
