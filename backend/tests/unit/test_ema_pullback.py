from dataclasses import replace
from decimal import Decimal

import pytest

from scalper.exchange.market_data import BookTop, Candle, Ticker
from scalper.strategies import STRATEGIES
from scalper.strategies.base import StrategyContext
from scalper.strategies.ema_pullback import EmaTrendPullback


def context(short=False):
    def bar(i, interval, o, h, low, c):
        return Candle(i * interval, o, h, low, c, 100, (i + 1) * interval - 1, 10000, 50)

    five = [
        bar(i, 300000, 89 + 0.15 * i, 89.3 + 0.15 * i, 88.9 + 0.15 * i, 89.2 + 0.15 * i)
        for i in range(80)
    ]
    one = [
        bar(
            i,
            60000,
            100 + 0.01 * (i - 320),
            100.2 + 0.01 * (i - 320),
            99.9 + 0.01 * (i - 320),
            100.1 + 0.01 * (i - 320),
        )
        for i in range(320, 400)
    ]
    one[-15] = replace(one[-15], high=103)
    one[-3] = replace(one[-3], open=101.4, high=101.45, low=101, close=101.1)
    one[-2] = replace(one[-2], open=101.1, high=101.2, low=100.7, close=100.8)
    one[-1] = replace(one[-1], open=100.8, high=101.4, low=100.75, close=101.3)
    if short:

        def mirror(b):
            return replace(
                b, open=200 - b.open, high=200 - b.low, low=200 - b.high, close=200 - b.close
            )

        five, one = list(map(mirror, five)), list(map(mirror, one))
    price = one[-1].close
    return StrategyContext(
        "SOLUSDT",
        five,
        one,
        Ticker("SOLUSDT", price, 0, 1e9),
        BookTop("SOLUSDT", price - 0.01, price + 0.01),
    )


@pytest.mark.parametrize("short", [False, True])
def test_entry_and_levels(short):
    ctx = context(short)
    strategy = EmaTrendPullback()
    setup = strategy.assess(ctx)
    assert setup.ready, setup.note
    direction = "SHORT" if short else "LONG"
    assert setup.direction == direction
    entry = Decimal(str(ctx.book.bid if short else ctx.book.ask))
    levels = strategy.levels(ctx, entry, direction)
    sign = -1 if short else 1
    assert (entry - levels.stop) * sign > 0
    assert (levels.target - entry) * sign == abs(entry - levels.stop) * Decimal("1.5")
    with pytest.raises(ValueError, match="half an ATR"):
        strategy.levels(ctx, entry + 10, direction)
    with pytest.raises(ValueError, match="invalid entry"):
        strategy.levels(ctx, Decimal("NaN"), direction)
    with pytest.raises(ValueError, match="direction"):
        strategy.levels(ctx, entry, "LONG" if short else "SHORT")


@pytest.mark.parametrize(
    "fault",
    [
        "history",
        "gap",
        "duration",
        "future",
        "stale",
        "nan",
        "book",
        "flat",
        "breakout",
        "one",
        "six",
        "volume",
        "barrier",
        "no_pivot",
        "lost",
    ],
)
def test_rejects_invalid_setups(fault):
    ctx = context()
    one, five = ctx.candles_1m, ctx.candles_5m
    if fault == "history":
        one[:] = one[-10:]
    elif fault == "gap":
        one.pop(-8)
    elif fault == "duration":
        one[-1] = replace(one[-1], close_time=0)
    elif fault in ("future", "stale"):
        delta = 300000 if fault == "future" else -300000
        five[:] = [
            replace(b, open_time=b.open_time + delta, close_time=b.close_time + delta) for b in five
        ]
    elif fault == "nan":
        one[-1] = replace(one[-1], close=float("nan"))
    elif fault == "book":
        ctx = replace(ctx, book=BookTop("SOLUSDT", 102, 101))
    elif fault == "flat":
        five[:] = [replace(b, open=100, high=101, low=99, close=100) for b in five]
    elif fault == "breakout":
        one[-1] = replace(one[-1], close=101.1)
    elif fault == "one":
        one[-3] = replace(one[-3], open=101.1, close=101.4)
    elif fault == "six":
        for i in range(-7, -3):
            one[i] = replace(one[i], open=one[i].close, close=one[i].open)
    elif fault == "volume":
        one[-1] = replace(one[-1], volume=0)
    elif fault == "barrier":
        one[-15] = replace(one[-15], high=101.5)
    elif fault == "no_pivot":
        one[-15] = replace(one[-15], high=100.85)
    elif fault == "lost":
        ctx = replace(ctx, book=BookTop("SOLUSDT", 101.18, 101.19))
    assert not EmaTrendPullback().assess(ctx).ready


def test_registered_for_discovery():
    assert isinstance(STRATEGIES.get("ema-trend-pullback"), EmaTrendPullback)
    assert any(s["id"] == "ema-trend-pullback" for s in STRATEGIES.describe())


def test_replay_enters_after_confirmation_and_charges_costs():
    from scalper.backtest import replay

    prefix = [
        Candle(
            i * 60000,
            89 + 0.03 * i,
            89.2 + 0.03 * i,
            88.9 + 0.03 * i,
            89.1 + 0.03 * i,
            100,
            (i + 1) * 60000 - 1,
            10000,
            50,
        )
        for i in range(320)
    ]
    bars = prefix + context().candles_1m
    assert replay(bars, strategy_id="ema-trend-pullback")["trade_count"] == 0
    bars.append(Candle(400 * 60000, 101.31, 103, 101.2, 102.8, 100, 401 * 60000 - 1, 10000, 50))
    result = replay(bars, strategy_id="ema-trend-pullback")
    assert result["trade_count"] == 0
    assert result["unfilled_entries"] == 1
    # A favorable next open can fill within the closed-signal IOC limit.
    bars[-1] = Candle(400 * 60000, 101.25, 103, 101.2, 102.8, 100, 401 * 60000 - 1,
                      10000, 50)
    result = replay(bars, strategy_id="ema-trend-pullback")
    assert result["trade_count"] == 1
    trade = result["trades"][0]
    assert trade["opened_at"] == 400 * 60000
    assert trade["reason"] == "target"
    assert trade["fees"] > 0
    assert 101.25 < trade["entry"] <= bars[-2].close
