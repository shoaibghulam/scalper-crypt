from types import SimpleNamespace
from unittest.mock import AsyncMock

from scalper.engine.screening import screen_watchlist
from tests.unit.test_ema_pullback import context
from tests.unit.test_research_integration import bot


async def test_many_bots_share_market_reads_and_preserve_waiting_reasons(monkeypatch):
    ctx = context()
    bots = [bot("ema-trend-pullback") for _ in range(8)]
    for index, watching in enumerate(bots):
        watching.id = str(index)
        watching.symbols = ["SOLUSDT", "DELISTEDUSDT"]
        watching.error = "previous expiry"
    market = AsyncMock()
    market.symbol_filters.return_value = {"SOLUSDT": object()}
    market.tickers.return_value = {"SOLUSDT": ctx.ticker}
    market.book_tops.return_value = {"SOLUSDT": ctx.book}
    market.candles_for.return_value = {"SOLUSDT": ctx.candles_1m}
    monkeypatch.setattr("scalper.engine.screening.evaluate_symbol", lambda *args:
                        SimpleNamespace(setup_ready=False, setup_note="No breakout", expires_at=0))
    ready = await screen_watchlist(bots, market)
    assert not any(ready.values())
    assert market.symbol_filters.await_count == 1
    assert market.tickers.await_count == 1
    assert market.book_tops.await_count == 1
    assert market.candles_for.await_count == 2
    market.order_flow.assert_not_awaited()
    for watching in bots:
        assert watching.error is None
        assert watching.symbol_notes["SOLUSDT"] == "No breakout"
        assert "Unavailable" in watching.symbol_notes["DELISTEDUSDT"]


async def test_screening_only_forwards_unexpired_confirmed_setups(monkeypatch):
    ctx = context()
    watching = bot("ema-trend-pullback")
    watching.symbols = ["SOLUSDT"]
    market = AsyncMock()
    market.symbol_filters.return_value = {"SOLUSDT": object()}
    market.tickers.return_value = {"SOLUSDT": ctx.ticker}
    market.book_tops.return_value = {"SOLUSDT": ctx.book}
    market.candles_for.return_value = {"SOLUSDT": ctx.candles_1m}
    monkeypatch.setattr("scalper.engine.screening.time.time", lambda: 100)
    candidate = SimpleNamespace(setup_ready=True, expires_at=110)
    monkeypatch.setattr("scalper.engine.screening.evaluate_symbol", lambda *args: candidate)
    assert (await screen_watchlist([watching], market))[watching.id] == ["SOLUSDT"]
    candidate.expires_at = 90
    assert (await screen_watchlist([watching], market))[watching.id] == []
    assert "expired" in watching.symbol_notes["SOLUSDT"]


async def test_waiting_setup_never_fetches_signed_account_or_leverage(monkeypatch):
    import pytest

    from scalper.engine.plan import PlanRejected
    from scalper.engine.trader import open_for_bot
    from tests.unit.test_trader_plan_building import SOLUSDT

    monkeypatch.setattr("scalper.engine.trader.build_plan",
                        AsyncMock(side_effect=PlanRejected("No breakout")))
    executor = SimpleNamespace(client=AsyncMock())
    with pytest.raises(PlanRejected, match="No breakout"):
        await open_for_bot(bot("ema-trend-pullback"), "SOLUSDT", SOLUSDT,
                           AsyncMock(), executor)
    executor.client.get_positions.assert_not_awaited()
    executor.client.max_leverage.assert_not_awaited()
    executor.client.place_order.assert_not_awaited()
