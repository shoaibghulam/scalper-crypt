from dataclasses import replace
from decimal import Decimal

import pytest

from scalper.api.research import catalog
from scalper.exchange.market_data import BookTop, Candle, Ticker
from scalper.strategies import STRATEGIES
from scalper.strategies.base import StrategyContext
from scalper.strategies.mtf_structure import (
    MultiTimeframeStructure,
    aggregate,
    structure_level,
    trend,
)


def bar(i, price=100, interval=300_000):
    return Candle(i * interval, price, price + 1, price - 1, price, 100,
                  (i + 1) * interval - 1, 10000, 50)


def test_aggregation_discards_forming_and_partial_groups():
    bars = [bar(i, 100 + i) for i in range(1, 25)]
    result = aggregate(bars, 60)
    assert len(result) == 1
    assert result[0].open_time == 12 * 300_000
    assert result[0].close_time == 24 * 300_000 - 1
    assert result[0].open == 112 and result[0].close == 123
    assert result[0].volume == 1200
    bars[-1] = replace(bars[-1], high=999999)
    assert aggregate(bars, 60) == result  # The unfinished next hour cannot affect it.


@pytest.mark.parametrize("minutes", [15, 30, 60])
def test_aggregation_never_accepts_missing_or_duplicated_bars(minutes):
    bars = [bar(i) for i in range(minutes // 5)]
    assert len(aggregate(bars, minutes)) == 1
    assert not aggregate(bars[:-1], minutes)
    assert not aggregate([*bars[:-1], bars[0]], minutes)


@pytest.mark.parametrize("sign", [1, -1])
def test_trend_direction_and_flat_market(sign):
    assert trend([bar(i, 200 + sign * i) for i in range(100)], "1h") == sign
    with pytest.raises(ValueError, match="neutral"):
        trend([bar(i) for i in range(100)], "1h")


def structure_bars():
    prices = [100, 101, 105, 102, 101, 103, 107]
    return [bar(i, price) for i, price in enumerate(prices)]


def test_structure_requires_right_bar_confirmation_and_fresh_break():
    bars = structure_bars()
    level, at = structure_level(bars, 1)
    assert level == 106 and at == bars[-1].close_time
    with pytest.raises(ValueError, match="no fresh"):
        structure_level(bars[:4], 1)
    # A closing invalidation prevents using a previous broken level.
    with pytest.raises(ValueError, match="no fresh"):
        structure_level([*bars, bar(7, 100)], 1)


def entry_bars(sign=1):
    bars = [bar(i, interval=60_000) for i in range(200)]
    if sign == 1:
        bars[-2] = replace(bars[-2], low=99.9, high=100.4, close=100.2)
        bars[-1] = replace(bars[-1], open=100.2, high=100.8, low=100.1,
                           close=100.7, volume=150)
    else:
        bars[-2] = replace(bars[-2], high=100.1, low=99.6, close=99.8)
        bars[-1] = replace(bars[-1], open=99.8, low=99.2, high=99.9,
                           close=99.3, volume=150)
    return bars


@pytest.mark.parametrize("sign,side", [(1, "LONG"), (-1, "SHORT")])
def test_retest_confirmation_and_volume_excludes_trigger(sign, side):
    bars = entry_bars(sign)
    signal = MultiTimeframeStructure.entry_signal(bars, 100, sign)
    assert signal.direction == side and signal.reward == Decimal(2)
    bars[-1] = replace(bars[-1], volume=110)
    with pytest.raises(ValueError, match="volume"):
        MultiTimeframeStructure.entry_signal(bars, 100, sign)


@pytest.fixture
def context():
    five = [bar(i, 100 + i / 100) for i in range(1212)]
    end = five[-1].close_time + 1
    one = [replace(b, open_time=end - (200 - i) * 60_000,
                   close_time=end - (199 - i) * 60_000 - 1)
           for i, b in enumerate(entry_bars())]
    return StrategyContext("BTCUSDT", five, one, Ticker("BTCUSDT", 100.7, 0, 0),
                           BookTop("BTCUSDT", 100.7, 100.7))


def test_closed_higher_timeframes_and_step_order(context, monkeypatch):
    import scalper.strategies.mtf_structure as module
    from scalper.indicators.core import AdxResult

    calls = []

    def bias(bars, label):
        calls.append((label, bars[-1].close_time))
        return 1

    monkeypatch.setattr(module, "trend", bias)
    monkeypatch.setattr(module, "adx", lambda *args: AdxResult(25, 30, 10))
    monkeypatch.setattr(
        module, "structure_level", lambda *args: (100, context.candles_1m[-3].close_time)
    )
    assert MultiTimeframeStructure().signal(context).direction == "LONG"
    assert [label for label, _ in calls] == ["1h", "30m"]
    assert all(at <= context.candles_1m[-1].close_time for _, at in calls)
    monkeypatch.setattr(
        module, "structure_level", lambda *args: (100, context.candles_1m[-2].close_time)
    )
    assert "retest must follow" in MultiTimeframeStructure().assess(context).note
    monkeypatch.setattr(module, "trend", lambda bars, label: 1 if label == "1h" else -1)
    assert "disagrees" in MultiTimeframeStructure().assess(context).note


def test_missing_and_future_candles_fail_closed(context):
    bars = list(context.candles_5m)
    bars[20] = bars[19]
    assert "gaps" in MultiTimeframeStructure().assess(replace(context, candles_5m=bars)).note
    # Keep required history while moving the decision behind the latest 5m candle.
    one = [replace(b, open_time=b.open_time - 60_000, close_time=b.close_time - 60_000)
           for b in context.candles_1m]
    assert "future" in MultiTimeframeStructure().assess(replace(context, candles_1m=one)).note


def test_stop_target_and_chase_rules(context, monkeypatch):
    strategy = MultiTimeframeStructure()
    signal = strategy.entry_signal(context.candles_1m, 100, 1)
    monkeypatch.setattr(strategy, "signal", lambda ctx: signal)
    levels = strategy.execution_levels(context, Decimal("100.7"), "LONG", Decimal(".01"))
    assert levels.stop < Decimal("100.7") < levels.target
    assert (levels.target - Decimal("100.7")) / (Decimal("100.7") - levels.stop) >= 2
    with pytest.raises(ValueError, match="half an ATR"):
        strategy.levels(context, Decimal("120"), "LONG")


def test_catalog_and_execution_registration():
    strategy = STRATEGIES.get(MultiTimeframeStructure.id)
    assert strategy.testnet_only and not strategy.backtest_only
    row = next(r for r in catalog() if r["id"] == strategy.id)
    assert row["timeframe"] == "1m" and row["warmup_candles"] == 6060
    assert strategy.history_5m + 1 <= 1500


def test_complete_long_pipeline_without_indicator_mocks():
    five = [replace(bar(i, 88 + i / 100), high=88 + i / 100 + .05,
                    low=88 + i / 100 - .05) for i in range(1212)]
    for i, price in enumerate([100, 100.1, 100.6, 100.2, 100.1, 100.3, 100.8], 1205):
        five[i] = replace(five[i], open=price, close=price, high=price + .05, low=price - .05)
    one = []
    for b in five[-40:]:
        for offset in range(5):
            one.append(replace(b, open_time=b.open_time + offset * 60_000,
                               close_time=b.open_time + offset * 60_000 + 59_999,
                               volume=b.volume / 5))
    start = five[-1].close_time + 1
    one.extend([
        Candle(start, 100.8, 100.8, 100.64, 100.7, 20, start + 59_999, 0, 0),
        Candle(start + 60_000, 100.7, 100.85, 100.68, 100.82, 40, start + 119_999, 0, 0),
    ])
    ctx = StrategyContext("BTCUSDT", five, one[-200:], Ticker("BTCUSDT", 100.82, 0, 0),
                          BookTop("BTCUSDT", 100.82, 100.82))
    setup = MultiTimeframeStructure().assess(ctx)
    assert setup.ready, setup.note
    assert setup.direction == "LONG"
