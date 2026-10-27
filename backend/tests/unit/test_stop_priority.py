import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from scalper.engine.plan import PlanRejected
from scalper.engine.trader import open_for_bot
from tests.unit.test_bounded_ema_entry import execution
from tests.unit.test_refresh_positions import engine_with
from tests.unit.test_trader_plan_building import SOLUSDT


@pytest.mark.parametrize("kind", ["stop_auto_trade", "stop_single_coin", "stop_bot"])
async def test_stop_does_not_wait_for_scan_lock_and_retains_positions(monkeypatch, kind):
    engine, bot, _ = engine_with(["SOLUSDT"], [], monkeypatch)
    bot.continuous = True
    engine.store.control["auto_trade"] = {"enabled": True, "bot_ids": [bot.id]}
    engine.store.control["single_coin"] = {"enabled": True, "bot_id": bot.id}
    position = bot.positions["SOLUSDT"]
    async with engine._account_lock:
        await asyncio.wait_for(engine.handle_command({
            "type": kind, "bot_id": bot.id, "issued_at": time.time(),
        }), timeout=0.5)
    assert bot.state == "stopped"
    assert bot.positions["SOLUSDT"] is position
    assert engine.store.load().bots[bot.id].state == "stopped"
    if kind == "stop_auto_trade":
        assert not engine.store.control["auto_trade"]["enabled"]


async def test_stop_during_preflight_prevents_entry_submission(monkeypatch):
    bot, executor = execution(monkeypatch, {"orderId": 1, "status": "FILLED", "executedQty": "1"})

    async def stop(*args):
        bot.state = "stopped"

    executor.client.set_leverage.side_effect = stop
    with pytest.raises(PlanRejected, match="Entries stopped"):
        await open_for_bot(bot, "SOLUSDT", SOLUSDT, AsyncMock(), executor)
    executor.client.place_order.assert_not_awaited()
    assert not bot.positions


async def test_bulk_dismiss_keeps_positions_and_pending_and_scopes_ids(monkeypatch):
    engine, protected, _ = engine_with(["SOLUSDT"], [], monkeypatch)
    def create():
        return engine.registry.create(symbols=[], amount=protected.amount,
                                      leverage=3, strategy=protected.strategy)
    empty, pending, outside = create(), create(), create()
    pending.entry_pending = True
    history = engine.history.recent()
    engine.store.control["auto_trade"] = {"enabled": True, "bot_ids": [empty.id, protected.id]}
    await engine.handle_command(
        {"type": "dismiss_bots", "bot_ids": [empty.id, protected.id, pending.id]}
    )
    assert set(engine.registry.bots) == {protected.id, pending.id, outside.id}
    assert engine.store.control["auto_trade"]["bot_ids"] == [protected.id]
    assert engine.history.recent() == history
    assert "Dismissed 1 bots" in engine.last_action["detail"]
    assert set(engine.store.load().bots) == set(engine.registry.bots)
