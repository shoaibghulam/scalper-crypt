from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from scalper.api.research import ResearchRequest, catalog
from scalper.backtest import replay
from scalper.exchange.market_data import BookTop, Candle, Ticker
from scalper.strategies import STRATEGIES
from scalper.strategies.base import Levels, Setup, StrategyContext
from scalper.strategies.ivb import NEW_YORK, IvbCrypto
from scalper.strategies.research_registry import RESEARCH_STRATEGIES


def ms(date="2026-09-18", hour=10, minute=5):
    return int(datetime.fromisoformat(date).replace(
        hour=hour, minute=minute, tzinfo=NEW_YORK).timestamp() * 1000)


def bar(at, close=100, high=105, low=95, volume=100, interval=300_000):
    return Candle(at, 100, high, low, close, volume, at + interval - 1, 0, 0)


@pytest.fixture
def context():
    end = ms()
    bars = [bar(at) for at in range(end - IvbCrypto.history_5m * 300_000, end, 300_000)]
    for i in range(-7, -1):
        bars[i] = replace(bars[i], high=101, low=99)
    bars[-1] = replace(bars[-1], high=102, low=100, close=102, volume=120)
    one = [bar(at, interval=60_000) for at in range(end - 300_000, end, 60_000)]
    return StrategyContext("BTCUSDT", bars, one, Ticker("BTCUSDT", 102, 0, 0),
                           BookTop("BTCUSDT", 102, 102))


def test_catalog_backtest_only_and_large_warmup():
    row = next(row for row in catalog() if row["id"] == IvbCrypto.id)
    assert row["timeframe"] == "5m" and row["backtest_only"]
    assert row["warmup_candles"] == 38_880
    assert IvbCrypto.id not in STRATEGIES.ids()
    ResearchRequest(csv="long-range", strategy=IvbCrypto.id, warmup=38_880, quantity_step=.001)


def test_long_signal_and_frozen_atr_bracket_from_actual_fill(context):
    strategy = IvbCrypto()
    assert strategy.assess(context).direction == "LONG"
    levels = strategy.levels(context, Decimal("103"), "LONG")
    assert levels.stop < 103 < levels.target
    assert float((levels.target - 103) / (103 - levels.stop)) == pytest.approx(2 / 3)


def test_short_signal(context):
    bars = list(context.candles_5m)
    bars[-1] = replace(bars[-1], close=98, low=98, high=100)
    assert IvbCrypto().assess(replace(context, candles_5m=bars)).direction == "SHORT"


@pytest.mark.parametrize("change,note", [
    ({"volume": 100}, "volume"),
    ({"close": 100}, "fresh"),
    ({"volume": float("nan")}, "non-finite"),
])
def test_signal_rejections(context, change, note):
    bars = list(context.candles_5m)
    bars[-1] = replace(bars[-1], **change)
    result = IvbCrypto().assess(replace(context, candles_5m=bars))
    assert not result.ready and note in result.note


def test_range_is_prior_days_only_and_requires_all_25(context):
    strategy = IvbCrypto()
    bars = list(context.candles_5m)
    # A missing old opening candle cannot be replaced with another day's range.
    start = ms("2026-08-24", 9, 30)
    index = next(i for i, b in enumerate(bars) if b.open_time == start)
    bars[index] = replace(bars[index], open_time=start + 1)
    result = strategy.assess(replace(context, candles_5m=bars))
    assert not result.ready and "complete prior" in result.note
    assert strategy.assess(context).ready  # No shared mutable/session cache.


def test_wide_range_and_stale_five_minute_signal_rejected(context):
    bars = list(context.candles_5m)
    bars[-2] = replace(bars[-2], high=110, low=90)
    assert "contracted" in IvbCrypto().assess(replace(context, candles_5m=bars)).note
    one = [*context.candles_1m, bar(ms(), interval=60_000)]
    assert "newly closed" in IvbCrypto().assess(replace(context, candles_1m=one)).note


@pytest.mark.parametrize("date,utc_hour", [("2026-01-15", 21), ("2026-07-15", 20)])
def test_exit_deadline_tracks_new_york_dst(date, utc_hour):
    from datetime import UTC
    exit_at = IvbCrypto().scheduled_exit(ms(date))
    assert datetime.fromtimestamp(exit_at / 1000, UTC).hour == utc_hour
    assert IvbCrypto().session_key(ms(date)) == date


class AlwaysEntry(IvbCrypto):
    id = "ivb-replay-probe"
    signal_interval_s = 60

    def assess(self, ctx):
        return Setup.entry("LONG", "probe")

    def levels(self, ctx, entry, direction):
        return Levels(entry - 1, entry + 2)


def test_filled_entry_cap_resets_per_session_and_per_replay(monkeypatch):
    monkeypatch.setitem(RESEARCH_STRATEGIES, AlwaysEntry.id, AlwaysEntry())
    candles = [bar(at, interval=60_000) for at in range(ms(hour=23, minute=50),
               ms("2026-09-19", 0, 10), 60_000)]
    # The probe has no session time filter; IVB's actual assess enforces that separately.
    result = replay(candles, strategy_id=AlwaysEntry.id)
    assert result["trade_count"] == 4
    assert replay(candles, strategy_id=AlwaysEntry.id)["trades"] == result["trades"]


def test_rejected_orders_do_not_consume_daily_limit(monkeypatch):
    class RejectFirst(AlwaysEntry):
        calls = 0

        def levels(self, ctx, entry, direction):
            self.calls += 1
            if self.calls < 3:
                raise ValueError("rejected entry")
            return super().levels(ctx, entry, direction)

    strategy = RejectFirst()
    monkeypatch.setitem(RESEARCH_STRATEGIES, strategy.id, strategy)
    candles = [bar(ms() + n * 60_000, interval=60_000) for n in range(8)]
    result = replay(candles, strategy_id=strategy.id)
    assert result["rejected_entries"] == 2 and result["trade_count"] == 2


def test_session_exit_at_open_precedes_later_intrabar_stop(monkeypatch):
    monkeypatch.setitem(RESEARCH_STRATEGIES, AlwaysEntry.id, AlwaysEntry())
    candles = [bar(ms(hour=15, minute=58), high=100.1, low=99.9, interval=60_000),
               bar(ms(hour=15, minute=59), high=100.1, low=99.9, interval=60_000),
               bar(ms(hour=16, minute=0), close=90, low=80, high=105, interval=60_000)]
    result = replay(candles, strategy_id=AlwaysEntry.id, slippage_bps=0, fee_rate=.001)
    trade = result["trades"][0]
    assert trade["reason"] == "session_end" and trade["exit"] == 100
    assert trade["closed_at"] == ms(hour=16, minute=0)
    assert trade["fees"] > 0 and result["session_exits"] == 1
    assert result["forced_exits"] == 0 and result["ambiguous_bars"] == 0


@pytest.mark.parametrize("hour,minute", [(9, 55), (10, 0), (16, 0), (16, 5)])
def test_no_entry_during_range_or_after_session(context, hour, minute):
    end = ms(hour=hour, minute=minute)
    bars = [*context.candles_5m[:-1], bar(end - 300_000)]
    assert not IvbCrypto().assess(replace(context, candles_5m=bars)).ready


def test_real_ivb_replay_enters_after_close_and_flattens_at_session_end(context):
    candles = []
    for five in context.candles_5m:
        for minute in range(5):
            candles.append(replace(
                five, open_time=five.open_time + minute * 60_000,
                close_time=five.open_time + minute * 60_000 + 59_999,
                close=five.close if minute == 4 else five.open, volume=five.volume / 5,
            ))
    for at in range(ms(), ms(hour=16, minute=1), 60_000):
        candles.append(Candle(at, 102, 102.1, 101.9, 102, 20, at + 59_999, 0, 0))
    result = replay(candles, strategy_id=IvbCrypto.id, slippage_bps=0)
    assert result["trade_count"] == 1
    trade = result["trades"][0]
    assert trade["opened_at"] == ms()
    assert trade["closed_at"] == ms(hour=16, minute=0)
    assert trade["reason"] == "session_end"
    assert trade["fees"] > 0


def test_http_catalog_labels_model_and_execution_rejects_it():
    import time

    from fastapi.testclient import TestClient

    from scalper.api.app import create_app

    app = create_app()
    app.state.engine.on_snapshot({"seq": 1}, now=time.monotonic())
    client = TestClient(app)
    row = next(r for r in client.get("/api/strategies").json()["strategies"]
               if r["id"] == IvbCrypto.id)
    assert row["backtest_only"]
    assert client.get(f"/api/scan?strategy={IvbCrypto.id}").status_code == 404
    assert client.post("/api/bots", json={"symbols": ["BTCUSDT"], "amount": "10",
                       "leverage": 3, "strategy": IvbCrypto.id}).status_code == 404
