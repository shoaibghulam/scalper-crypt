"""Behavioral failures discovered by reviewing the source videos again."""

from dataclasses import replace
from decimal import Decimal

import pytest

from scalper.backtest import replay
from scalper.exchange.market_data import BookTop
from scalper.indicators.core import HeikinAshiCandle
from scalper.strategies.black import MinuteBlack, _is_clean
from scalper.strategies.legend import MinuteLegendV1, _rejection_wick
from scalper.strategies.orb500 import Backtest500V1
from tests.unit import test_strategy_black as black
from tests.unit import test_strategy_legend as legend
from tests.unit import test_strategy_orb500 as orb


@pytest.mark.parametrize(
    "direction,candle",
    [
        ("LONG", HeikinAshiCandle(100, 100.05, 99, 99.5)),
        ("SHORT", HeikinAshiCandle(100, 101, 99.95, 100.5)),
    ],
)
def test_visibly_wicked_ha_pullback_is_not_clean(direction, candle):
    assert not _is_clean(candle, direction)


@pytest.mark.parametrize(
    "direction,candle",
    [
        ("LONG", HeikinAshiCandle(100, 100, 99, 99.5)),
        ("SHORT", HeikinAshiCandle(100, 101, 100, 100.5)),
    ],
)
def test_naturally_wickless_ha_pullback_remains_clean(direction, candle):
    assert _is_clean(candle, direction)


@pytest.mark.parametrize(
    "direction,bar",
    [
        ("LONG", legend.candle(102.9, 102.98, 102.30, open_=102.31)),
        ("SHORT", legend.candle(97.1, 97.70, 97.02, open_=97.69)),
    ],
)
def test_large_body_cannot_masquerade_as_rejection_wick(direction, bar):
    assert not _rejection_wick(bar, direction)


def test_legend_refuses_actual_setup_whose_recovery_is_only_body():
    bars = legend.long_setup()
    bars[-1] = replace(bars[-1], open=102.31)
    setup = MinuteLegendV1().assess(legend.context(bars))
    assert not setup.ready and "wick" in setup.note


@pytest.mark.parametrize(
    "direction,bars,entry",
    [
        ("LONG", legend.long_setup(), "103.15"),
        ("SHORT", legend.short_setup(), "96.85"),
        ("LONG", legend.long_setup(), "102.39"),
        ("SHORT", legend.short_setup(), "97.61"),
        ("SHORT", legend.long_setup(), "102.90"),
    ],
)
def test_legend_refuses_bad_quote_or_wrong_direction_without_inventing_target(
    direction, bars, entry
):
    with pytest.raises(ValueError):
        MinuteLegendV1().levels(legend.context(bars), Decimal(entry), direction)


def test_legend_scanner_checks_executable_reward_not_only_signal_close():
    ctx = legend.context(legend.long_setup())
    ctx = replace(ctx, book=BookTop(ctx.symbol, 103.14, 103.15))
    setup = MinuteLegendV1().assess(ctx)
    assert not setup.ready and "1.5R" in setup.note


def test_legend_stop_includes_one_atr_when_it_is_larger_than_structure():
    stop = MinuteLegendV1()._stop_for(Decimal("100"), 99.8, "LONG", volatility=1.2)
    assert stop == Decimal("98.8")


def test_legend_does_not_reuse_an_already_swept_reference():
    bars = legend.long_setup()
    bars[60] = replace(bars[60], low=102.35)
    setup = MinuteLegendV1().assess(legend.context(bars))
    assert not setup.ready and "already swept" in setup.note


def orb_context(day="2026-09-18", hour=7, minute=0):
    ctx = orb.setup_context(day)
    offset = ((hour - 7) * 60 + minute) * 60000
    return replace(
        ctx,
        candles_1m=[
            replace(c, open_time=c.open_time + offset, close_time=c.close_time + offset)
            for c in ctx.candles_1m
        ],
    )


@pytest.mark.parametrize("day", ["2026-01-16", "2026-09-18", "2026-09-19", "2026-09-20"])
@pytest.mark.parametrize("hour,minute", [(0, 0), (4, 15), (12, 30), (23, 45)])
def test_crypto_setup_is_identical_at_any_hour_and_on_weekends(day, hour, minute):
    ctx = orb_context(day, hour, minute)
    strategy = Backtest500V1()
    setup = strategy.assess(ctx)
    assert setup.ready and setup.direction == "LONG"
    bracket = strategy.levels(ctx, Decimal(str(ctx.price)), "LONG")
    assert bracket.stop == Decimal("99")


def test_partial_or_duplicate_opening_range_is_refused():
    ctx = orb_context()
    bars = ctx.candles_1m.copy()
    bars[125] = bars[124]
    setup = Backtest500V1().assess(replace(ctx, candles_1m=bars))
    assert not setup.ready and "consecutive" in setup.note


def test_opening_range_does_not_roll_after_large_later_wick():
    ctx = orb_context()
    bars = ctx.candles_1m.copy()
    bars[136] = replace(bars[136], high=110)
    ctx = replace(ctx, candles_1m=bars)
    assert Backtest500V1().assess(ctx).ready
    assert max(c.high for c in Backtest500V1()._window(ctx)) == 101


def test_later_retest_cannot_repeat_a_session_entry():
    ctx = orb_context()
    bars = ctx.candles_1m.copy()
    for c in bars[-10:]:
        bars.append(replace(c, open_time=c.open_time + 600000, close_time=c.close_time + 600000))
    setup = Backtest500V1().assess(replace(ctx, candles_1m=bars))
    assert not setup.ready and "no repeat" in setup.note


@pytest.mark.parametrize("quote", [100.8, 104.0])
def test_orb_refuses_lost_breakout_or_chasing(quote):
    ctx = orb_context()
    setup = Backtest500V1().assess(replace(ctx, book=BookTop(ctx.symbol, quote - 0.01, quote)))
    assert not setup.ready
    with pytest.raises(ValueError):
        Backtest500V1().levels(ctx, Decimal(str(quote)), "LONG")


@pytest.mark.parametrize(
    "strategy,ctx",
    [
        (MinuteBlack(), black.context(black.long_setup())),
        (MinuteLegendV1(), legend.context(legend.long_setup())),
        (Backtest500V1(), orb_context()),
    ],
)
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0])
def test_all_three_strategies_refuse_invalid_quotes(strategy, ctx, bad):
    broken = replace(ctx, book=BookTop(ctx.symbol, bad, bad))
    assert not strategy.assess(broken).ready
    with pytest.raises(ValueError):
        strategy.levels(broken, Decimal("100"), "LONG")


@pytest.mark.parametrize(
    "strategy,ctx",
    [
        (MinuteBlack(), black.context(black.long_setup())),
        (MinuteLegendV1(), legend.context(legend.long_setup())),
        (Backtest500V1(), orb_context()),
    ],
)
def test_all_three_refuse_malformed_historical_bar(strategy, ctx):
    bars = ctx.candles_1m.copy()
    bars[0] = replace(bars[0], low=bars[0].high + 1)
    assert not strategy.assess(replace(ctx, candles_1m=bars)).ready


@pytest.mark.parametrize("strategy", ["the-minute-black", "1-minute-legend-v1", "500-backtest-v1"])
def test_replay_selects_and_identifies_each_existing_strategy(strategy):
    result = replay([], strategy_id=strategy)
    assert result["strategy_id"] == strategy and result["trade_count"] == 0


def test_replay_rejects_invalid_execution_candles():
    bar = replace(orb.candle(100, 101, 99), open=float("nan"), close_time=59999)
    with pytest.raises(ValueError, match="candle data"):
        replay([bar])


@pytest.mark.parametrize("strategy_id", ["1-minute-legend-v1", "500-backtest-v1"])
def test_replay_executes_other_strategy_signals_causally(strategy_id):
    if strategy_id == "1-minute-legend-v1":
        bars = legend.uptrend(110, start=90, step=0.09) + legend.long_setup()
        next_bar = legend.candle(102.90, 105, 100)
        start = 0
    else:
        ctx = orb_context()
        bars = ctx.candles_1m.copy()
        next_bar = orb.candle(102.1, 112, 95)
        start = bars[0].open_time
    signal_end = start + len(bars) * 60000 - 1
    bars += [next_bar]
    aligned = [
        replace(c, open_time=start + i * 60000, close_time=start + i * 60000 + 59999)
        for i, c in enumerate(bars)
    ]
    result = replay(aligned, strategy_id=strategy_id)
    assert result["trade_count"] >= 1
    trade = result["trades"][-1]
    assert trade["opened_at"] == signal_end + 1
    assert trade["reason"] == "stop" and trade["fees"] > 0


async def test_scanner_fetches_complete_opening_session_history():
    from unittest.mock import AsyncMock

    from scalper.scanner.scan import run_scan

    market = AsyncMock()
    market.symbol_filters.return_value = {}
    market.tickers.return_value = {}
    market.book_tops.return_value = {}
    market.candles_for.return_value = {}
    await run_scan(market, strategy=Backtest500V1())
    market.candles_for.assert_any_await([], "1m", limit=400)


def test_scanner_rejects_invalid_candles_before_indicator_math():
    from scalper.scanner.config import ScanConfig
    from scalper.scanner.evaluate import Rejection, evaluate_symbol

    ctx = legend.context(legend.long_setup())
    bars = ctx.candles_5m.copy()
    bars[-1] = replace(bars[-1], high=float("nan"))
    result = evaluate_symbol(
        ctx.symbol, ctx.ticker, ctx.book, bars, ctx.candles_1m, ScanConfig(), MinuteLegendV1()
    )
    assert isinstance(result, Rejection) and result.stage == "data"


def test_black_source_size_rule_accepts_either_previous_candle():
    from scalper.strategies.black import _size_confirms

    bars = [
        HeikinAshiCandle(100, 102, 98, 100),
        HeikinAshiCandle(100, 100.5, 99.5, 100),
        HeikinAshiCandle(100, 101, 99, 100),
    ]
    assert _size_confirms(bars, 2)
    assert not _size_confirms([bars[0], bars[0], bars[2]], 2)


def test_black_small_body_without_both_wicks_is_not_video_doji():
    from scalper.strategies.black import _is_doji

    assert not _is_doji(HeikinAshiCandle(100, 100, 99, 100))
    assert not _is_doji(HeikinAshiCandle(100, 101, 100, 100))
    assert _is_doji(HeikinAshiCandle(100, 101, 99, 100))
