from dataclasses import replace
from decimal import Decimal
from random import Random

import pytest

import scalper.strategies.nine_indicator as module
from scalper.api.research import catalog
from scalper.backtest import replay
from scalper.exchange.market_data import BookTop, Candle, Ticker
from scalper.indicators.core import AdxResult
from scalper.strategies import STRATEGIES
from scalper.strategies.base import StrategyContext
from scalper.strategies.nine_indicator import NineIndicatorPullback


def candle(i, interval=60_000):
    return Candle(i * interval, 100, 101, 99, 100, 100,
                  (i + 1) * interval - 1, 0, 0)


@pytest.fixture
def ctx():
    one = [candle(i) for i in range(1440)]
    one[-2] = replace(one[-2], high=100.8, low=100.3, open=100.5, close=100.5)
    one[-1] = replace(one[-1], high=101.1, low=100.45, open=100.5, close=101, volume=150)
    return StrategyContext("BTCUSDT", [candle(i, 300_000) for i in range(300)], one,
                           Ticker("BTCUSDT", 101, 0, 0), BookTop("BTCUSDT", 101, 101))


@pytest.fixture
def aligned(monkeypatch, ctx):
    monkeypatch.setattr(module, "ema", lambda closes, period:
                        {9: 100.6, 20: 100.4 if len(closes) >= 199 else 100.3, 50: 100}[period])
    monkeypatch.setattr(module, "adx", lambda *args: AdxResult(25, 30, 10))
    monkeypatch.setattr(module, "bollinger", lambda bars: (100, 1))
    monkeypatch.setattr(module, "rsi", lambda closes: 60 if len(closes) == 200 else 55)
    monkeypatch.setattr(module, "macd_histogram", lambda closes: ([1, 1], [-.2, -.1]))
    monkeypatch.setattr(module, "stoch_rsi", lambda closes:
                        [(.4, .5), (.35, .45), (.5, .4), (.6, .5)])
    return ctx


def test_all_filters_agree_and_atr_bracket_is_two_r(aligned):
    strategy = NineIndicatorPullback()
    assert strategy.assess(aligned).direction == "LONG"
    signal = strategy.signal(aligned)
    assert signal.atr > 0 and signal.stop_atr == 1.5
    levels = strategy.execution_levels(aligned, Decimal(101), "LONG", Decimal(".01"))
    assert levels.stop < 101 < levels.target
    assert (levels.target - 101) / (101 - levels.stop) == 2
    with pytest.raises(ValueError, match="half an ATR"):
        strategy.levels(aligned, Decimal(120), "LONG")


@pytest.mark.parametrize("indicator,value,reason", [
    ("ema", lambda *args: 100, "EMA"),
    ("session_vwap", lambda *args: (102, 1), "VWAP"),
    ("adx", lambda *args: AdxResult(10, 30, 10), "ADX"),
    ("bollinger", lambda *args: (100, .1), "Bollinger"),
    ("rsi", lambda *args: 85, "RSI14"),
    ("macd_histogram", lambda *args: ([1, 1], [.2, .1]), "MACD"),
    ("stoch_rsi", lambda *args: [(.6, .5)] * 4, "StochRSI"),
    ("volatility", lambda *args: 0, "ATR"),
])
def test_each_indicator_can_veto_entry(aligned, monkeypatch, indicator, value, reason):
    monkeypatch.setattr(module, indicator, value)
    setup = NineIndicatorPullback().assess(aligned)
    assert not setup.ready and reason in setup.note


def test_volume_uses_previous_bars_and_vetoes_weak_trigger(aligned):
    one = list(aligned.candles_1m)
    one[-1] = replace(one[-1], volume=119)
    setup = NineIndicatorPullback().assess(replace(aligned, candles_1m=one))
    assert not setup.ready and "volume" in setup.note
    one[-1] = replace(one[-1], volume=120)
    assert NineIndicatorPullback().assess(replace(aligned, candles_1m=one)).ready


def test_session_vwap_requires_midnight_and_warmup(ctx):
    strategy = NineIndicatorPullback()
    assert "complete UTC" in strategy.assess(replace(ctx, candles_1m=ctx.candles_1m[1:])).note
    one = ctx.candles_1m[10:] + [candle(i) for i in range(1440, 1450)]
    assert "twelve" in strategy.assess(replace(ctx, candles_1m=one)).note


def test_gaps_nonfinite_and_alignment_are_rejected(ctx):
    for field, value, reason in [("volume", float("nan"), "non-finite"),
                                 ("close_time", 1, "alignment")]:
        one = list(ctx.candles_1m)
        one[-1] = replace(one[-1], **{field: value})
        assert reason in NineIndicatorPullback().assess(replace(ctx, candles_1m=one)).note
    one = list(ctx.candles_1m)
    one[100] = one[99]
    assert "gaps" in NineIndicatorPullback().assess(replace(ctx, candles_1m=one)).note


def test_catalog_registration_and_session_fetch():
    strategy = STRATEGIES.get(NineIndicatorPullback.id)
    assert strategy.testnet_only and not strategy.backtest_only
    assert strategy.fetch_limit_1m == 1441
    row = next(row for row in catalog() if row["id"] == strategy.id)
    assert row["timeframe"] == "1m" and row["warmup_candles"] == 1500


def test_short_rules_mirror_long_rules(aligned, monkeypatch):
    one = [replace(b, open=200 - b.open, high=200 - b.low, low=200 - b.high,
                   close=200 - b.close) for b in aligned.candles_1m]
    monkeypatch.setattr(module, "ema", lambda closes, period:
                        {9: 99.4, 20: 99.6 if len(closes) >= 199 else 99.7, 50: 100}[period])
    monkeypatch.setattr(module, "adx", lambda *args: AdxResult(25, 10, 30))
    monkeypatch.setattr(module, "rsi", lambda closes: 40 if len(closes) == 200 else 45)
    monkeypatch.setattr(module, "macd_histogram", lambda closes: ([-1, -1], [.2, .1]))
    monkeypatch.setattr(module, "stoch_rsi", lambda closes:
                        [(.6, .5), (.65, .55), (.5, .6), (.4, .5)])
    mirrored = replace(aligned, candles_1m=one, book=BookTop("BTCUSDT", 99, 99))
    assert NineIndicatorPullback().assess(mirrored).direction == "SHORT"


@pytest.mark.parametrize("mirrored,side", [(False, "SHORT"), (True, "LONG")])
def test_real_indicators_and_next_bar_replay_entry(mirrored, side):
    # Fixed synthetic price path, not optimized historical returns. No indicator mocks.
    candles = [candle(i) for i in range(1440)]
    rng, price = Random(20260920), 100.0
    for n in range(213):
        opened = price
        price += .003 + rng.gauss(0, .08)
        i = 1440 + n
        candles.append(Candle(i * 60_000, opened, max(opened, price) + .02,
                              min(opened, price) - .02, price, 150 if n == 212 else 100,
                              i * 60_000 + 59_999, 0, 0))
    i = len(candles)
    candles.append(Candle(i * 60_000, price, price + .01, price - .01, price,
                          100, i * 60_000 + 59_999, 0, 0))
    if mirrored:
        candles = [replace(b, open=200 - b.open, high=200 - b.low, low=200 - b.high,
                           close=200 - b.close) for b in candles]
    result = replay(candles, strategy_id=NineIndicatorPullback.id,
                    start_index=len(candles) - 1, slippage_bps=0)
    assert result["trade_count"] == 1
    trade = result["trades"][0]
    assert trade["side"] == side
    assert trade["opened_at"] == candles[-1].open_time
    assert trade["fees"] > 0 and trade["reason"] == "end_of_data"
