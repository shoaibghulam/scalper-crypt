from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from scalper.engine.plan import PlanRejected
from scalper.engine.trader import build_plan
from scalper.exchange.market_data import MarketDataClient
from tests.unit.test_ema_pullback import context
from tests.unit.test_research_integration import Market, bot
from tests.unit.test_trader_plan_building import SOLUSDT


async def test_no_event_connection_for_missing_candle_setup(monkeypatch):
    ctx = context()
    ctx.candles_1m[-1] = replace(ctx.candles_1m[-1], close=101.1)
    monkeypatch.setattr(
        "scalper.engine.trader.time.time", lambda: ctx.candles_1m[-1].close_time / 1000 + 2
    )
    market = Market(ctx)
    market.order_flow = AsyncMock(side_effect=TimeoutError())
    with pytest.raises(PlanRejected, match="breakout"):
        await build_plan(bot("order-flow-pullback"), "SOLUSDT", SOLUSDT, market)
    market.order_flow.assert_not_awaited()


async def test_candle_cache_expires_on_next_minute(monkeypatch):
    clock = [120.0]
    monkeypatch.setattr("scalper.exchange.market_data.time.time", lambda: clock[0])
    market = MarketDataClient("https://example.invalid")
    raw = [0, "100", "101", "99", "100", "10", 59999, "1000", 1, "5", "500", 0]
    market._get = AsyncMock(return_value=[raw])
    await market.prefetch_candles(["SOLUSDT", "ETHUSDT"], 200, 200)
    assert market._get.await_count == 4
    await market.candles("SOLUSDT", "1m", 200)
    assert market._get.await_count == 4
    clock[0] = 180
    await market.candles("SOLUSDT", "1m", 200)
    assert market._get.await_count == 5


async def test_candle_bots_run_before_flow_and_5m_only_at_boundary(monkeypatch):
    from decimal import Decimal
    from types import SimpleNamespace

    from scalper.config import Environment
    from tests.unit.test_refresh_positions import engine_with

    engine, first, _ = engine_with([], [], monkeypatch)
    first.strategy = "order-flow-pullback"
    first.continuous = True
    candle = engine.registry.create(
        symbols=["SOLUSDT"], amount=Decimal(20), leverage=3, strategy="range-vwap-reversion"
    )
    candle.continuous = True
    engine.safe_mode = False
    engine._open_bot = AsyncMock()
    monkeypatch.setattr("scalper.engine.__main__.ExchangeConfig", lambda: SimpleNamespace(
        env=Environment.TESTNET, rest="https://example.invalid",
    ))
    async def screen(bots, market):
        return {b.id: b.symbols for b in bots}
    monkeypatch.setattr("scalper.engine.__main__.screen_watchlist", screen)
    first.symbols = ["ETHUSDT"]
    monkeypatch.setattr("scalper.engine.__main__.time.time", lambda: 302)
    await engine._signal_cycle()
    assert [c.args[0].strategy for c in engine._open_bot.await_args_list] == [
        "range-vwap-reversion",
        "order-flow-pullback",
    ]
    engine._open_bot.reset_mock()
    monkeypatch.setattr("scalper.engine.__main__.time.time", lambda: 362)
    await engine._signal_cycle()
    engine._open_bot.assert_awaited_once_with(first, first.symbols)
    assert "5m" in candle.symbol_notes["SOLUSDT"]


@pytest.mark.parametrize("uncertain", [False, True])
@pytest.mark.parametrize("status", [None, -1003, 400, 401, 418, 429, 408, 500, 502, 503, 504])
async def test_transport_failure_retries_only_before_entry_intent(monkeypatch, uncertain, status):
    from types import SimpleNamespace

    import httpx

    from scalper.config import Environment
    from scalper.engine import __main__ as module
    from tests.unit.test_refresh_positions import engine_with

    engine, watching, _ = engine_with([], [], monkeypatch)
    watching.symbols = ["DASHUSDT", "AVAXUSDT", "ONEUSDT", "RAYSOLUSDT"]
    watching.strategy = "range-vwap-reversion"
    watching.continuous = True
    engine.safe_mode = False
    monkeypatch.setattr(module, "ExchangeConfig", lambda: SimpleNamespace(
        env=Environment.TESTNET, api_key="test", rest="https://example.invalid",
        assert_armed=lambda: None,
    ))
    market = AsyncMock()
    market.__aenter__.return_value = market
    market.symbol_filters.return_value = dict.fromkeys(watching.symbols, SOLUSDT)
    monkeypatch.setattr(module, "MarketDataClient", lambda url: market)

    async def opening(bot, symbol, *args):
        if symbol == "DASHUSDT":
            if uncertain:
                bot.entry_pending = True
                bot.entry_intent = {"uncertain": True}
            if status == -1003:
                from scalper.exchange.errors import classify
                raise classify(-1003, "Too many requests")
            if status:
                request = httpx.Request("GET", "https://example.invalid/fapi/v1/klines?signature=secret")
                raise httpx.HTTPStatusError("failed", request=request,
                    response=httpx.Response(status, request=request))
            raise httpx.ConnectTimeout("offline")
        raise PlanRejected(f"{symbol}: not a low-trend range")

    mocked = AsyncMock(side_effect=opening)
    monkeypatch.setattr(module, "open_for_bot", mocked)
    await engine._open_bot(watching)
    assert "secret" not in watching.error
    if status and status != -1003:
        assert "/fapi/v1/klines" in watching.error
    if uncertain:
        assert engine.safe_mode and watching.state == "error"
        assert watching.entry_pending and watching.entry_intent
        assert mocked.await_count == 1
        assert ("Binance error -1003" if status == -1003 else f"HTTP {status}" if status else "ConnectTimeout") in watching.error
    elif status in {400, 401}:
        assert watching.state == "error"
        assert mocked.await_count == 1
    else:
        assert not engine.safe_mode and watching.state == "running"
        assert mocked.await_count == (1 if status in {-1003, 418, 429} else 4)
        assert "retrying" in watching.symbol_notes["DASHUSDT"]
        if status not in {-1003, 418, 429}:
            assert "low-trend range" in watching.symbol_notes["RAYSOLUSDT"]


async def test_creation_refuses_leverage_above_configured_cap(monkeypatch):
    from tests.unit.test_refresh_positions import engine_with

    engine, _, _ = engine_with([], [], monkeypatch)
    engine.safe_mode = False
    engine.store.control["halted"] = False
    before = len(engine.registry.bots)
    await engine._create_bot({"symbols": ["SOLUSDT"], "amount": "100",
                              "leverage": 20, "strategy": "range-vwap-reversion",
                              "continuous": True})
    assert len(engine.registry.bots) == before
    assert "leverage cap" in engine.last_action["detail"]


@pytest.mark.parametrize("fatal", [False, True])
async def test_successful_entry_with_skips_does_not_hide_real_execution_errors(monkeypatch, fatal):
    from types import SimpleNamespace

    from scalper.config import Environment
    from scalper.engine import __main__ as module
    from scalper.engine.executor import UnprotectedPosition
    from tests.unit.test_refresh_positions import engine_with

    engine, watching, _ = engine_with([], [], monkeypatch)
    watching.symbols = ["REZUSDT", "ARBUSDT"]
    watching.strategy = "range-vwap-reversion"
    watching.continuous = True
    engine.safe_mode = False
    monkeypatch.setattr(module, "ExchangeConfig", lambda: SimpleNamespace(
        env=Environment.TESTNET, api_key="test", rest="https://example.invalid",
        assert_armed=lambda: None,
    ))
    market = AsyncMock()
    market.__aenter__.return_value = market
    market.symbol_filters.return_value = dict.fromkeys(watching.symbols, SOLUSDT)
    monkeypatch.setattr(module, "MarketDataClient", lambda url: market)

    async def opening(bot, symbol, *args):
        if symbol == "ARBUSDT":
            if fatal:
                raise UnprotectedPosition("ARBUSDT: protection unverified")
            raise PlanRejected("ARBUSDT: spread 9.05 bps exceeds 5.00 bps maximum")

    monkeypatch.setattr(module, "open_for_bot", AsyncMock(side_effect=opening))
    await engine._open_bot(watching)
    assert engine.last_action["ok"] is not fatal
    assert "opened REZUSDT" in engine.last_action["detail"]
    if not fatal:
        assert "1 candidates skipped" in engine.last_action["detail"]
        assert "9.05" not in engine.last_action["detail"]
        assert "9.05" in watching.symbol_notes["ARBUSDT"]
    else:
        assert engine.safe_mode
        assert "protection unverified" in engine.last_action["detail"]
