from dataclasses import replace
from decimal import Decimal
from random import Random

import pytest

from scalper.api.research import catalog
from scalper.api.research_jobs import AnnualRequest
from scalper.backtest import replay
from scalper.engine.commands import SingleCoinRequest
from scalper.engine.single_coin import ranked_candidates
from scalper.exchange.market_data import BookTop, Candle, Ticker
from scalper.strategies import STRATEGIES
from scalper.strategies.base import StrategyContext
from scalper.strategies.ten_minute import TEN_MINUTE_STRATEGIES, TenMinuteMtf, TenMinuteRecipe


def synthetic(number):
    # Fixed paths proving distinct rules can fire; no historical-return optimization.
    end = {1: 204, 2: 205, 3: 205, 4: 398, 5: 212, 6: 381,
           7: 323, 8: 215, 9: 201, 10: 381}[number]
    rng, price, bars = Random(20260920), 100.0, []
    for i in range(end + 1):
        opened = price
        price += .002 + rng.gauss(0, .15)
        if number == 9:
            opened = price = 100
        bars.append(Candle(i * 600_000, opened, max(opened, price) + .03,
                           min(opened, price) - .03, price, 100,
                           i * 600_000 + 599_999, 0, 0))
    bars[-1] = replace(bars[-1], volume=200)
    if number == 9:
        bars[-1] = replace(bars[-1], open=99.95, close=100.05, low=99.5, high=100.1)
    return bars[-201:]


def context(ten):
    five = [replace(b, open_time=b.open_time + offset * 300_000,
                    close_time=b.open_time + offset * 300_000 + 299_999,
                    volume=b.volume / 2) for b in ten for offset in range(2)]
    one = [replace(b, open_time=b.open_time + offset * 60_000,
                   close_time=b.open_time + offset * 60_000 + 59_999,
                   volume=b.volume / 10) for b in ten[-4:] for offset in range(10)]
    return StrategyContext("BTCUSDT", five, one, Ticker("BTCUSDT", ten[-1].close, 0, 0),
                           BookTop("BTCUSDT", ten[-1].close, ten[-1].close))


@pytest.mark.parametrize("number", range(1, 11))
@pytest.mark.parametrize("mirror", [False, True])
def test_all_ten_recipes_signal_and_preserve_two_r_brackets(number, mirror):
    ten = synthetic(number)
    if mirror:
        ten = [replace(b, open=200 - b.open, high=200 - b.low, low=200 - b.high,
                       close=200 - b.close) for b in ten]
    ctx, strategy = context(ten), TenMinuteRecipe(number)
    setup = strategy.assess(ctx)
    assert setup.ready, setup.note
    expected = "LONG" if number in (6, 7, 9, 10) else "SHORT"
    if mirror:
        expected = "SHORT" if expected == "LONG" else "LONG"
    assert setup.direction == expected
    entry = Decimal(str(ten[-1].close))
    levels = strategy.execution_levels(ctx, entry, expected, Decimal(".01"))
    sign = 1 if expected == "LONG" else -1
    assert (entry - levels.stop) * sign > 0
    assert (levels.target - entry) * sign >= 2 * abs(entry - levels.stop)


def test_forming_ten_minute_bar_gap_and_no_session_dependency():
    ctx, strategy = context(synthetic(1)), TenMinuteRecipe(1)
    original = strategy.signal(ctx)
    shifted = replace(ctx, candles_1m=[replace(b, open_time=b.open_time + 43_200_000,
                                              close_time=b.close_time + 43_200_000)
                                       for b in ctx.candles_1m],
                      candles_5m=[replace(b, open_time=b.open_time + 43_200_000,
                                          close_time=b.close_time + 43_200_000)
                                  for b in ctx.candles_5m])
    assert strategy.signal(shifted) == original
    extra = replace(ctx.candles_1m[-1], open_time=ctx.candles_1m[-1].open_time + 60_000,
                    close_time=ctx.candles_1m[-1].close_time + 60_000)
    assert "newly closed 10m" in strategy.assess(
        replace(ctx, candles_1m=[*ctx.candles_1m, extra])).note
    five = list(ctx.candles_5m)
    five[100] = five[99]
    assert "gaps" in strategy.assess(replace(ctx, candles_5m=five)).note
    assert strategy.scheduled_exit(0) is None and strategy.max_trades_per_session == 0


def test_replay_only_enters_after_closed_ten_minute_signal():
    ten, strategy = synthetic(1), TenMinuteRecipe(1)
    candles = [replace(b, open_time=b.open_time + n * 60_000,
                       close_time=b.open_time + n * 60_000 + 59_999, volume=b.volume / 10)
               for b in ten for n in range(10)]
    unclosed = replay(candles, strategy_id=strategy.id, start_index=len(candles) - 1)
    assert unclosed["trade_count"] == 0
    last = candles[-1]
    candles.append(Candle(last.close_time + 1, last.close, last.close + .01,
                          last.close - .01, last.close, 100, last.close_time + 60_000, 0, 0))
    result = replay(candles, strategy_id=strategy.id, start_index=len(candles) - 1,
                    slippage_bps=0)
    assert result["trade_count"] == 1
    assert result["trades"][0]["opened_at"] == last.close_time + 1


def test_catalog_can_select_entire_library_and_rank_ten_minute_strategies():
    from scalper.engine.experiments import report
    from scalper.engine.trades import TradeHistory

    assert len(TEN_MINUTE_STRATEGIES) == 11
    rows = {r["id"]: r for r in catalog()}
    for key, strategy in TEN_MINUTE_STRATEGIES.items():
        assert rows[key]["timeframe"] == "10m" and strategy.testnet_only
        assert STRATEGIES.get(key).signal_interval_s == 600
        assert strategy.scheduled_exit(0) is None
    AnnualRequest(strategies=list(rows))
    SingleCoinRequest(amount="10", leverage=3, timeframe="10m")
    row = {"id": TenMinuteMtf.id, "disabled": False, "flagged": False,
           "trades": 10, "net": 2, "wins": 6, "win_rate": .6}
    assert ranked_candidates([row], "10m") == [row]
    assert ranked_candidates([row], "5m") == []
    monitor = report(TradeHistory(), {})
    assert next(r for r in monitor["rows"] if r["id"] == TenMinuteMtf.id)[
        "single_coin_timeframe"] == "10m"


def test_mtf_ten_minute_uses_ten_minute_retest_after_five_minute_break(monkeypatch):
    import scalper.strategies.mtf_structure as module
    from scalper.indicators.core import AdxResult

    ten = synthetic(1)
    # Pad higher context to 1,212 consecutive 5m bars without changing entry history.
    ctx = context(ten)
    first = ctx.candles_5m[0]
    prefix = [replace(first, open_time=first.open_time - n * 300_000,
                      close_time=first.close_time - n * 300_000) for n in range(810, 0, -1)]
    ctx = replace(ctx, candles_5m=[*prefix, *ctx.candles_5m],
                  candles_1m=[replace(ctx.candles_1m[0],
                                      open_time=ctx.candles_1m[0].open_time - n * 60_000,
                                      close_time=ctx.candles_1m[0].close_time - n * 60_000)
                              for n in range(160, 0, -1)] + ctx.candles_1m)
    monkeypatch.setattr(module, "trend", lambda *args: 1)
    monkeypatch.setattr(module, "adx", lambda *args: AdxResult(30, 20, 5))
    monkeypatch.setattr(module, "ema", lambda *args: 1)
    monkeypatch.setattr(module, "structure_level", lambda *args: (100, ten[-2].open_time - 1))
    strategy = TenMinuteMtf()
    seen = []
    monkeypatch.setattr(strategy, "entry_signal", lambda bars, level, sign: seen.append(bars))
    strategy.signal(ctx)
    assert len(seen[0]) == 200
    assert all(b.close_time - b.open_time == 599_999 for b in seen[0])
    monkeypatch.setattr(module, "structure_level", lambda *args: (100, ten[-2].close_time))
    with pytest.raises(ValueError, match="retest must follow"):
        strategy.signal(ctx)
