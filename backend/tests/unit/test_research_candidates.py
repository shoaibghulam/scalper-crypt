from dataclasses import replace
from decimal import Decimal

import pytest

from scalper.exchange.market_data import BookTop, Candle, Ticker
from scalper.strategies import STRATEGIES
from scalper.strategies.base import StrategyContext
from scalper.strategies.research import (
    LiquiditySweepReclaim,
    RangeVwapReversion,
    RollingBreakoutRetest,
)


def bar(i, interval, price=100, **kw):
    c = Candle(
        i * interval,
        price - 0.1,
        price + 0.3,
        price - 0.3,
        price,
        100,
        (i + 1) * interval - 1,
        price * 100,
        50,
    )
    return replace(c, **kw)


def make_context(five, one=None):
    if one is None:
        one = [bar(i, 60000, five[i // 5].close) for i in range(240, 300)]
        one[-1] = replace(
            one[-1],
            close=five[-1].close,
            open=five[-1].close,
            high=five[-1].close + 0.1,
            low=five[-1].close - 0.1,
        )
    price = one[-1].close
    return StrategyContext(
        "SOLUSDT",
        five,
        one,
        Ticker("SOLUSDT", price, 0, 1e9),
        BookTop("SOLUSDT", price - 0.001, price + 0.001),
    )


def breakout():
    five = [bar(i, 300000) for i in range(60)]
    five[-2] = replace(five[-2], open=100, high=101.2, low=99.9, close=101)
    five[-1] = replace(five[-1], open=100.35, high=101, low=100.25, close=100.8)
    return make_context(five)


def reversion():
    five = [bar(i, 300000, 100 + (0.6 if i % 2 else -0.6)) for i in range(60)]
    five[-2] = replace(five[-2], open=99, high=99.1, low=98.5, close=98.65)
    five[-1] = replace(five[-1], open=98.65, high=99, low=98.6, close=98.9)
    # Narrow stable reference: center 100 and bands approximately 99.4/100.6.
    return make_context(five)


def sweep():
    five = [bar(i, 300000, 90 + 0.15 * i) for i in range(60)]
    one = [bar(i, 60000, 100 + 0.001 * (i - 240)) for i in range(240, 300)]
    one[-15] = replace(one[-15], high=103)
    one[-6] = replace(one[-6], low=99.5)
    one[-1] = replace(one[-1], open=100.05, low=99.4, high=100.2, close=100.1, volume=150)
    return make_context(five, one)


def mirror(ctx):
    def flip(b):
        return replace(
            b, open=200 - b.open, high=200 - b.low, low=200 - b.high, close=200 - b.close
        )

    return make_context(list(map(flip, ctx.candles_5m)), list(map(flip, ctx.candles_1m)))


CASES = [
    (RollingBreakoutRetest, breakout),
    (RangeVwapReversion, reversion),
    (LiquiditySweepReclaim, sweep),
]


@pytest.mark.parametrize("cls,factory", CASES)
@pytest.mark.parametrize("short", [False, True])
def test_real_signals_and_protection(cls, factory, short):
    ctx = factory()
    if short:
        ctx = mirror(ctx)
    strategy = cls()
    setup = strategy.assess(ctx)
    assert setup.ready, setup.note
    direction = "SHORT" if short else "LONG"
    assert setup.direction == direction
    entry = Decimal(str(ctx.book.bid if short else ctx.book.ask))
    levels = strategy.levels(ctx, entry, direction)
    sign = -1 if short else 1
    assert (entry - levels.stop) * sign >= entry * Decimal(".0045")
    assert (levels.target - entry) * sign >= abs(entry - levels.stop) * Decimal("1.5")
    if cls is RangeVwapReversion:
        assert float(levels.target) == pytest.approx(100)
    with pytest.raises(ValueError, match="half an ATR"):
        strategy.levels(ctx, entry + 10, direction)
    with pytest.raises(ValueError, match="invalid entry"):
        strategy.levels(ctx, Decimal("NaN"), direction)
    with pytest.raises(ValueError, match="direction"):
        strategy.levels(ctx, entry, "LONG" if short else "SHORT")


@pytest.mark.parametrize("cls,factory", CASES)
@pytest.mark.parametrize("fault", ["history", "gap", "alignment", "nan", "book", "future", "stale"])
def test_invalid_data_fails_closed(cls, factory, fault):
    ctx = factory()
    if fault == "history":
        ctx = replace(ctx, candles_1m=ctx.candles_1m[-10:])
    if fault == "gap":
        ctx.candles_1m.pop(-5)
    if fault == "alignment":
        ctx.candles_1m[-1] = replace(ctx.candles_1m[-1], close_time=0)
    if fault == "nan":
        ctx.candles_1m[-1] = replace(ctx.candles_1m[-1], close=float("nan"))
    if fault == "book":
        ctx = replace(ctx, book=BookTop("SOLUSDT", 101, 100))
    if fault in ("future", "stale"):
        delta = 300000 if fault == "future" else -300000
        ctx = replace(
            ctx,
            candles_5m=[
                replace(b, open_time=b.open_time + delta, close_time=b.close_time + delta)
                for b in ctx.candles_5m
            ],
        )
    assert not cls().assess(ctx).ready


def test_retest_consumed_and_far_side_invalidation():
    ctx = breakout()
    # Move breakout and first retest one bar earlier; a second retest cannot enter.
    ctx.candles_5m[-3] = replace(
        ctx.candles_5m[-2], open_time=57 * 300000, close_time=58 * 300000 - 1
    )
    ctx.candles_5m[-2] = replace(
        ctx.candles_5m[-1], open_time=58 * 300000, close_time=59 * 300000 - 1
    )
    assert "consumed" in RollingBreakoutRetest().assess(ctx).note
    ctx.candles_5m[-2] = replace(ctx.candles_5m[-2], low=98, close=98.5)
    assert "invalidated" in RollingBreakoutRetest().assess(ctx).note


def test_sweep_reference_cannot_be_used_twice():
    ctx = sweep()
    ctx.candles_1m[-3] = replace(ctx.candles_1m[-3], low=99.45)
    assert not LiquiditySweepReclaim().assess(ctx).ready


def test_sweep_requires_volume_and_true_wick():
    for changes in ({"volume": 100}, {"open": 99.5}):
        ctx = sweep()
        ctx.candles_1m[-1] = replace(ctx.candles_1m[-1], **changes)
        assert not LiquiditySweepReclaim().assess(ctx).ready


def test_range_rejects_trend_and_empty_reference():
    ctx = reversion()
    ctx.candles_5m[:] = [bar(i, 300000, 90 + i * 0.2) for i in range(60)]
    assert not RangeVwapReversion().assess(ctx).ready
    with pytest.raises(ValueError, match="empty VWAP"):
        RangeVwapReversion.bands([replace(b, volume=0) for b in ctx.candles_5m])


def test_registry_contains_all_five_research_candidates():
    expected = {
        "ema-trend-pullback",
        "rolling-breakout-retest",
        "range-vwap-reversion",
        "liquidity-sweep-reclaim",
        "order-flow-pullback",
    }
    assert expected <= set(STRATEGIES.ids())


def test_candle_replay_refuses_order_flow():
    from scalper.backtest import replay

    with pytest.raises(ValueError, match="event data"):
        replay([], strategy_id="order-flow-pullback")


@pytest.mark.parametrize("cls,factory", CASES)
def test_causal_replay_of_new_candle_candidates(cls, factory):
    from scalper.backtest import replay

    ctx = factory()
    candles = []
    for b in ctx.candles_5m:
        for j in range(5):
            start = b.open_time + j * 60000
            candles.append(
                Candle(
                    start,
                    b.open if j == 0 else b.close,
                    b.high if j == 0 else b.close + 0.001,
                    b.low if j == 0 else b.close - 0.001,
                    b.close,
                    b.volume / 5,
                    start + 59999,
                    b.quote_volume / 5,
                    b.taker_buy_base / 5,
                )
            )
    if cls is LiquiditySweepReclaim:
        candles[-60:] = ctx.candles_1m
    result = replay(candles, strategy_id=cls.id)
    assert result["trade_count"] == 0
    price = candles[-1].close
    candles.append(
        Candle(
            300 * 60000, price, price + 4, price - 0.01, price + 3, 100, 301 * 60000 - 1, 10000, 50
        )
    )
    result = replay(candles, strategy_id=cls.id)
    assert result["trade_count"] == 1
    assert result["trades"][0]["opened_at"] == 300 * 60000
    assert result["trades"][0]["fees"] > 0
    assert result["trades"][0]["reason"] == "target"


def test_fixed_target_not_extended_for_exchange_rounding():
    ctx = reversion()
    strategy = RangeVwapReversion()
    levels = strategy.execution_levels(ctx, Decimal("98.9"), "LONG", Decimal(".03"))
    assert levels.target <= Decimal("100")
    assert levels.target % Decimal(".03") == 0


def test_ema_target_recomputed_after_stop_rounding():
    from scalper.strategies.ema_pullback import EmaTrendPullback
    from tests.unit.test_ema_pullback import context

    ctx = context()
    entry = Decimal("101.31")
    levels = EmaTrendPullback().execution_levels(ctx, entry, "LONG", Decimal(".01"))
    assert (levels.target - entry) / (entry - levels.stop) >= Decimal("1.5")
    assert levels.target < Decimal("103")
