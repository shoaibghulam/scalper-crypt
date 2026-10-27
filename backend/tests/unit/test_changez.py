from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from scalper.api.research import catalog
from scalper.backtest import replay
from scalper.exchange.market_data import BookTop, Candle, Ticker
from scalper.strategies import STRATEGIES
from scalper.strategies.base import StrategyContext
from scalper.strategies.changez import Changez, ChangezCostFiltered


def context(minutes=1, short=False, date="2026-09-21T13:00:00"):
    end = int(datetime.fromisoformat(date).replace(tzinfo=UTC).timestamp() * 1000)
    interval = minutes * 60_000
    bars = [Candle(end - (199-i)*interval, 90, 92, 89, 90, 100,
                   end - (199-i)*interval + interval-1, 0, 0) for i in range(200)]
    bars[-50] = replace(bars[-50], high=120, low=80)
    bars[-6] = replace(bars[-6], high=95)
    bars[-2] = replace(bars[-2], open=91, high=94, close=94)
    bars[-1] = replace(bars[-1], open=95, high=97, low=95, close=96)
    if short:
        bars = [replace(b, open=200-b.open, close=200-b.close,
                        high=200-b.low, low=200-b.high) for b in bars]
    one = bars if minutes == 1 else [replace(bars[-1], open_time=end+i*60_000,
                    close_time=end+(i+1)*60_000-1) for i in range(5)]
    return StrategyContext("BTCUSDT", bars if minutes == 5 else [bars[0]], one,
                           Ticker("BTCUSDT", one[-1].close, 0, 0),
                           BookTop("BTCUSDT", one[-1].close, one[-1].close))


@pytest.mark.parametrize("minutes", [1, 5])
@pytest.mark.parametrize("short", [False, True])
def test_confirmed_signal_and_five_r_from_actual_fill(minutes, short):
    strategy = Changez(minutes)
    ctx = context(minutes, short)
    direction = "SHORT" if short else "LONG"
    assert strategy.assess(ctx).direction == direction
    entry = Decimal("104.1" if short else "96.1")
    levels = strategy.levels(ctx, entry, direction)
    assert abs(levels.target-entry) == 5*abs(entry-levels.stop)
    assert levels.stop == Decimal("111" if short else "89")


@pytest.mark.parametrize("minutes", [1, 5])
def test_catalog_and_backtest_only_registration(minutes):
    row = next(r for r in catalog() if r["id"] == f"changez-{minutes}m")
    assert row["timeframe"] == f"{minutes}m"
    assert row["testnet_only"] and row["backtest_only"]
    assert row["id"] not in STRATEGIES.ids()
    assert Changez(minutes).signal_interval_s == minutes*60


@pytest.mark.parametrize("date,ready", [
    ("2026-09-21T13:00:00", True),  # 09:00 EDT
    ("2026-01-21T14:00:00", True),  # 09:00 EST
    ("2026-01-21T13:00:00", False),
    ("2026-09-21T15:00:00", False),  # Exclusive 11:00 boundary
])
def test_historical_dst_and_session_boundaries(date, ready):
    assert Changez(1).assess(context(date=date)).ready == ready


def test_missing_fvg_and_future_or_partial_five_minute_bar_rejected():
    ctx = context()
    bars = list(ctx.candles_1m)
    bars[-1] = replace(bars[-1], low=91)
    assert not Changez(1).assess(replace(ctx, candles_1m=bars)).ready
    five = context(5)
    assert not Changez(5).assess(replace(five, candles_1m=five.candles_1m[:-1])).ready


def test_gap_and_invalid_stop_rejected():
    ctx = context()
    assert not Changez(1).assess(replace(ctx, candles_1m=ctx.candles_1m[:-2]
                                       + ctx.candles_1m[-1:])).ready
    with pytest.raises(ValueError, match="protection"):
        Changez(1).levels(ctx, Decimal("88"), "LONG")


@pytest.mark.parametrize("minutes", [1, 5])
def test_replay_records_target_exit_for_both_timeframes(minutes):
    ctx = context(minutes)
    source = ctx.candles_1m if minutes == 1 else ctx.candles_5m
    candles = [replace(b, open_time=b.open_time+i*60_000,
                       close_time=b.open_time+(i+1)*60_000-1)
               for b in source for i in range(minutes)]
    at = candles[-1].close_time+1
    candles.append(Candle(at, 96, 132, 95, 131, 100, at+59_999, 0, 0))
    result = replay(candles, strategy_id=f"changez-{minutes}m")
    assert result["trade_count"] == 1
    assert result["trades"][0]["reason"] == "target"
    assert result["tp_hits"] == 1 and result["sl_hits"] == 0


def test_cost_variant_skips_tight_stops_without_changing_original():
    ctx = context()
    def shrink(b):
        return replace(b, **{key: 100+(getattr(b, key)-100)*0.01
                             for key in ("open", "high", "low", "close")})
    bars = [shrink(b) for b in ctx.candles_1m]
    tight = replace(ctx, candles_1m=bars, candles_5m=[shrink(ctx.candles_5m[0])],
                    book=BookTop("BTCUSDT", bars[-1].close, bars[-1].close))
    assert Changez(1).assess(tight).ready
    assert "cost filter" in ChangezCostFiltered(1).assess(tight).note
    # It must also reject a filled entry that makes the stop too tight.
    with pytest.raises(ValueError, match="cost filter"):
        ChangezCostFiltered(1).levels(tight, Decimal(str(bars[-1].close)), "LONG")
    assert ChangezCostFiltered(1).levels(ctx, Decimal("96"), "LONG") == (
        Changez(1).levels(ctx, Decimal("96"), "LONG"))
