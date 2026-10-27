from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scalper.exchange.market_data import MarketDataClient


def raw(start):
    return [start, "100", "101", "99", "100", "10", start + 59999, "1000", 1, "5", "500", 0]


@pytest.mark.parametrize("starts,expected", [([0, 60000], 2), ([0, 60000, 120000], 2)])
async def test_only_forming_candle_removed(monkeypatch, starts, expected):
    monkeypatch.setattr("scalper.exchange.market_data.time.time", lambda: 120)
    market = MarketDataClient("https://example.invalid")
    market._get = AsyncMock(return_value=[raw(s) for s in starts])
    bars = await market.candles("SOLUSDT", "1m")
    assert len(bars) == expected
    assert bars[-1].close_time == 119999


async def test_continuous_creation_persists_watch_without_immediate_order(monkeypatch):
    from scalper.config import Environment
    from scalper.engine import __main__ as module
    from tests.unit.test_refresh_positions import engine_with

    engine, _, _ = engine_with([], [], monkeypatch)
    engine.safe_mode = False
    engine.store.control["halted"] = False
    monkeypatch.setattr(
        module,
        "ExchangeConfig",
        lambda: SimpleNamespace(env=Environment.TESTNET, assert_armed=lambda: None),
    )
    engine._open_bot = AsyncMock()
    await engine._create_bot(
        {
            "symbols": ["SOLUSDT"],
            "strategy": "ema-trend-pullback",
            "amount": "20",
            "leverage": 3,
            "continuous": True,
        }
    )
    engine._open_bot.assert_not_awaited()
    watching = [b for b in engine.registry.bots.values() if b.strategy == "ema-trend-pullback"]
    assert len(watching) == 1 and watching[0].continuous
    assert engine.store.load().bots[watching[0].id].continuous
    assert engine.last_action["ok"] is True
    assert "watching" in engine.last_action["detail"]


def test_expiry_does_not_hide_original_waiting_reason():
    from dataclasses import replace

    from scalper.scanner.config import ScanConfig
    from scalper.scanner.evaluate import Candidate, evaluate_symbol
    from scalper.scanner.scan import ScanResult
    from scalper.scanner.service import ScannerService
    from scalper.strategies import STRATEGIES
    from tests.unit.test_ema_pullback import context

    ctx = context()
    c = evaluate_symbol(
        ctx.symbol,
        ctx.ticker,
        ctx.book,
        ctx.candles_5m,
        ctx.candles_1m,
        ScanConfig(),
        STRATEGIES.get("ema-trend-pullback"),
    )
    assert isinstance(c, Candidate)
    service = ScannerService("https://example.invalid")
    service._result = ScanResult(
        candidates=[
            replace(c, setup_ready=False, setup_note="No breakout confirmation", expires_at=0)
        ]
    )
    assert service.describe()["candidates"][0]["setup_note"] == "No breakout confirmation"
    service._result = ScanResult(candidates=[replace(c, setup_ready=True, expires_at=0)])
    assert "expired" in service.describe()["candidates"][0]["setup_note"]


async def test_transient_refresh_recovers_only_after_fresh_clean_account(monkeypatch):
    from tests.unit.test_refresh_positions import engine_with

    engine, _, client = engine_with([], [], monkeypatch)
    engine.safe_mode = False
    engine.store.control["halted"] = False
    original = client.get_positions
    client.get_positions = AsyncMock(side_effect=TimeoutError("temporary outage"))
    await engine.refresh_positions()
    assert engine.safe_mode
    client.get_positions = original
    await engine.refresh_positions()
    assert not engine.safe_mode
    engine._refresh_failed = True
    engine.safe_mode = True
    engine.store.control["halted"] = True
    await engine.refresh_positions()
    assert engine.safe_mode
