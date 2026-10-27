import time
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scalper.config import Environment
from scalper.engine.executor import UnprotectedPosition
from scalper.engine.plan import EntryAborted, PlanRejected, TradePlan
from scalper.engine.trader import open_for_bot
from tests.unit.test_research_integration import bot
from tests.unit.test_trader_plan_building import SOLUSDT


def execution(monkeypatch, response, price="100", qty="1", side="LONG"):
    watching = bot("ema-trend-pullback")
    plan = TradePlan(watching.id, 1, "SOLUSDT", side, D("1"), D("100"),
                     D("99" if side == "LONG" else "101"),
                     D("101.5" if side == "LONG" else "98.5"),
                     3, int(time.time() * 1000))
    monkeypatch.setattr("scalper.engine.trader.build_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr("scalper.engine.trader._actual_fill",
                        AsyncMock(return_value=(D(price), D(qty))))
    client = AsyncMock()
    client.config = SimpleNamespace(env=Environment.TESTNET)
    client.get_positions.return_value = {"result": []}
    client.max_leverage.return_value = None
    client.place_order.return_value = response
    executor = SimpleNamespace(client=client, attach_bracket=AsyncMock(return_value=True))
    return watching, executor


@pytest.mark.parametrize("qty", ["1", "0.4"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_ema_uses_price_bounded_ioc_and_protects_actual_quantity(monkeypatch, qty, side):
    watching, executor = execution(monkeypatch, {
        "orderId": 1, "status": "FILLED" if qty == "1" else "EXPIRED", "executedQty": qty,
    }, qty=qty, side=side)
    position = await open_for_bot(watching, "SOLUSDT", SOLUSDT, AsyncMock(), executor)
    order = executor.client.place_order.call_args.kwargs
    assert order["order_type"] == "LIMIT" and order["time_in_force"] == "IOC"
    assert order["price"] == D("100")
    assert order["side"] == ("BUY" if side == "LONG" else "SELL")
    assert position.stop_armed and position.quantity == D(qty)
    assert executor.attach_bracket.call_args.args[0].quantity == D(qty)


async def test_unfilled_ioc_does_not_create_position_or_emergency_close(monkeypatch):
    watching, executor = execution(monkeypatch, {"status": "EXPIRED", "executedQty": "0"})
    with pytest.raises(PlanRejected, match="unfilled"):
        await open_for_bot(watching, "SOLUSDT", SOLUSDT, AsyncMock(), executor)
    assert not watching.positions
    assert executor.client.place_order.await_count == 1
    executor.attach_bracket.assert_not_awaited()


async def test_unknown_ioc_response_requires_reconciliation(monkeypatch):
    watching, executor = execution(monkeypatch, {"status": "NEW", "executedQty": "0"})
    with pytest.raises(UnprotectedPosition, match="outcome unknown"):
        await open_for_bot(watching, "SOLUSDT", SOLUSDT, AsyncMock(), executor)


async def test_post_fill_rejection_marks_exit_for_verified_recovery(monkeypatch):
    watching, executor = execution(monkeypatch, {
        "orderId": 1, "status": "FILLED", "executedQty": "1",
    }, price="100.01")
    with pytest.raises(EntryAborted, match="reward to risk"):
        await open_for_bot(watching, "SOLUSDT", SOLUSDT, AsyncMock(), executor)
    assert executor.client.place_order.call_args.kwargs["reduce_only"] is True
    assert "SOLUSDT" in watching.positions  # Retained until exchange flatness is verified.


@pytest.mark.parametrize("settled", [False, True])
async def test_resume_requires_flatness_and_completed_settlement(monkeypatch, settled):
    from tests.unit.test_refresh_positions import engine_with

    engine, watching, _ = engine_with(["SOLUSDT"], [], monkeypatch)
    watching.continuous = True
    watching.state = "error"
    watching.resume_after_settlement = True
    if not settled:
        monkeypatch.setattr("scalper.engine.__main__.settle", AsyncMock(return_value=None))
    await engine.refresh_positions()
    assert watching.state == ("running" if settled else "error")
    assert watching.resume_after_settlement is not settled
    assert engine.store.load().bots[watching.id].resume_after_settlement is not settled


async def test_ioc_requests_terminal_result():
    from tests.unit.test_binance_client import make_client

    client = make_client()
    client._signed = AsyncMock(return_value={"status": "EXPIRED", "executedQty": "0"})
    await client.place_order(symbol="SOLUSDT", side="BUY", order_type="LIMIT",
                             quantity=D("1"), price=D("100"), time_in_force="IOC",
                             client_order_id="test-entry")
    assert client._signed.call_args.args[2]["newOrderRespType"] == "RESULT"


@pytest.mark.parametrize("settled", [False, True])
async def test_abort_warning_settles_with_another_protected_position(monkeypatch, settled):
    from tests.unit.test_refresh_positions import engine_with, row

    engine, watching, _ = engine_with(
        ["SOLUSDT", "OTHERUSDT"], [row("OTHERUSDT", "2.36", "106")], monkeypatch,
    )
    watching.continuous = True
    watching.state = "error"
    watching.resume_after_settlement = True
    watching.error = "SOLUSDT: entry aborted after fill: rejected; verifying exit"
    if not settled:
        monkeypatch.setattr("scalper.engine.__main__.settle", AsyncMock(return_value=None))
    await engine.refresh_positions()
    assert "OTHERUSDT" in watching.positions
    assert watching.positions["OTHERUSDT"].stop_armed
    assert (watching.error is None) is settled
    assert watching.state == ("running" if settled else "error")


async def test_startup_read_failure_recovers_only_after_verified_refresh(monkeypatch):
    from scalper.engine import __main__ as module
    from tests.unit.test_refresh_positions import engine_with, row

    engine, watching, _ = engine_with(["SOLUSDT"], [row("SOLUSDT", "2.36", "106")], monkeypatch)
    original = module.adopt_position
    monkeypatch.setattr(module, "adopt_position", AsyncMock(side_effect=RuntimeError("temporary")))
    await engine.reconcile_account()
    assert engine.safe_mode
    assert engine._refresh_failed
    monkeypatch.setattr(module, "adopt_position", original)
    await engine.refresh_positions()
    assert watching.positions["SOLUSDT"].stop_armed
    assert not engine.safe_mode
