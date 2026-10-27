from dataclasses import replace
from decimal import Decimal

import pytest

from scalper.api.research import ResearchRequest, catalog, evaluate
from scalper.backtest import replay
from scalper.exchange.market_data import BookTop, Candle, Ticker
from scalper.strategies import STRATEGIES
from scalper.strategies.base import StrategyContext
from scalper.strategies.funded_price_action import (
    FundedPriceAction,
    confirmation,
    hourly,
    pivot,
    trigger,
)


def candle(i, o=100, h=101, low=99, c=100):
    return Candle(i * 300000, o, h, low, c, 100, (i + 1) * 300000 - 1, 0, 0)


def setup_bars():
    return [candle(i) for i in range(252)] + [
        candle(252),
        candle(253),
        candle(254),
        candle(255),
        candle(256, 100, 105, 99, 101),
        candle(257, 101, 103, 99, 101),
        candle(258, 101, 102, 99, 100),
        candle(259, 100, 107, 100, 106),  # Break known high; not entry.
        candle(260, 106, 107, 104.95, 106.5),  # Later retest, long lower wick.
    ]


def one_minute(five):
    return [
        replace(b, open_time=b.open_time + j * 60000, close_time=b.open_time + (j + 1) * 60000 - 1)
        for b in five
        for j in range(5)
    ]


def context(five):
    one = one_minute(five)
    return StrategyContext(
        "TESTUSDT",
        five[-252:],
        one[-60:],
        Ticker("TESTUSDT", one[-1].close, 0, 0),
        BookTop("TESTUSDT", one[-1].close, one[-1].close),
    )


@pytest.mark.parametrize("sign", [1, -1])
def test_break_then_retest_both_sides_first_confirmation_only(sign):
    data = setup_bars()
    if sign == -1:
        data = [
            replace(b, open=200 - b.open, close=200 - b.close, high=200 - b.low, low=200 - b.high)
            for b in data
        ]
    assert trigger(data[:-1]) == 0
    assert trigger(data) == sign
    strategy = FundedPriceAction()
    result = strategy.assess(context(data))
    assert result.ready and result.direction == ("LONG" if sign == 1 else "SHORT")
    levels = strategy.levels(context(data), Decimal(str(data[-1].close)), result.direction)
    expected = Decimal(str(data[-1].low if sign == 1 else data[-1].high)) * Decimal(
        "0.9999" if sign == 1 else "1.0001"
    )
    assert levels.stop == expected
    assert abs(levels.target - Decimal(str(data[-1].close))) == 2 * abs(
        Decimal(str(data[-1].close)) - levels.stop
    )
    repeated = replace(data[-1], open_time=261 * 300000, close_time=262 * 300000 - 1)
    assert trigger(data + [repeated]) == 0


def test_no_retest_no_confirmation_and_expired_or_failed_setup():
    data = setup_bars()
    assert trigger(data[:-1] + [candle(260, 108, 109, 107, 108.5)]) == 0
    assert trigger(data[:-1] + [candle(260, 105.2, 105.8, 104.95, 105.3)]) == 0
    late = data[:-1] + [candle(i, 108, 109, 107, 108) for i in range(260, 272)]
    assert (
        trigger(late + [replace(data[-1], open_time=272 * 300000, close_time=273 * 300000 - 1)])
        == 0
    )
    failed = data[:-1] + [candle(260, 106, 106, 103, 104)]
    assert (
        trigger(failed + [replace(data[-1], open_time=261 * 300000, close_time=262 * 300000 - 1)])
        == 0
    )


def test_engulfing_is_alternative_to_long_wick():
    previous = candle(0, 104, 105, 99, 100)
    current = candle(1, 99.9, 105, 99.8, 104.5)
    assert confirmation(previous, current, 1)
    assert not confirmation(previous, current, -1)
    assert not confirmation(previous, candle(1, 100, 100, 100, 100), 1)


def test_pivots_need_two_future_closes_and_hourly_excludes_partial_gapped_groups():
    data = setup_bars()
    assert not pivot(data[:258], 256, 1)
    assert pivot(data[:259], 256, 1)
    hours = hourly(data)
    assert len(hours) == 21
    assert hourly(data[:-1]) == hours
    assert len(hourly(data[1:])) == 20
    assert len(hourly(data[:5] + data[6:])) == 20
    assert all(b.close_time % 3600000 == 3599999 for b in hours)


def test_rejects_stale_invalid_and_missing_history():
    strategy = FundedPriceAction()
    ctx = context(setup_bars())
    assert not strategy.assess(replace(ctx, candles_5m=[])).ready
    stale = replace(
        ctx.candles_1m[-1],
        open_time=ctx.candles_1m[-1].open_time + 60000,
        close_time=ctx.candles_1m[-1].close_time + 60000,
    )
    assert not strategy.assess(replace(ctx, candles_1m=ctx.candles_1m[1:] + [stale])).ready
    assert not strategy.assess(
        replace(ctx, candles_5m=ctx.candles_5m[:100] + ctx.candles_5m[101:])
    ).ready
    bad = replace(ctx.candles_5m[-1], low=float("nan"))
    assert not strategy.assess(replace(ctx, candles_5m=ctx.candles_5m[:-1] + [bad])).ready
    with pytest.raises(ValueError):
        strategy.levels(ctx, Decimal("1"), "LONG")


def test_replay_enters_after_confirmation_and_preserves_causal_signal():
    data = setup_bars() + [candle(261, 106.5, 112, 106, 110)]
    result = replay(
        one_minute(data),
        strategy_id="funded-price-action",
        start_index=1260,
        initial=100,
        leverage=3,
        fee_rate=0,
        slippage_bps=0,
        spread_bps=0,
        minimum_notional=5,
        quantity_step=0.001,
    )
    assert result["trade_count"] == 1
    trade = result["trades"][0]
    assert trade["entry"] == 106.5
    assert trade["reason"] == "target"
    assert trigger(data[:-1]) == 1  # Future target bar was not needed for the signal.


def test_research_registry_and_comparison_warmup():
    assert STRATEGIES.get("funded-price-action").testnet_only
    assert not STRATEGIES.get("funded-price-action").manual_only
    item = next(s for s in catalog() if s["id"] == "funded-price-action")
    assert item["research_only"] and item["warmup_candles"] == 1260
    assert FundedPriceAction.signal_interval_s == 300
    csv = "open_time,open,high,low,close,volume\n" + "\n".join(
        f"{i * 60000},100,101,99,100,100" for i in range(1960)
    )
    request = ResearchRequest(csv=csv, strategy="funded-price-action", quantity_step=0.001)
    report = evaluate(request)
    baseline = evaluate(request.model_copy(update={"strategy": "lab-01", "warmup": 1260}))
    assert report["warmup_candles"] == 1260
    assert len(report["results"]) == 6
    assert all(r["trade_count"] == 0 for r in report["results"])
    assert [(r["start"], r["end"]) for r in report["results"]] == [
        (r["start"], r["end"]) for r in baseline["results"]
    ]
