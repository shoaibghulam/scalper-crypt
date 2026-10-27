"""Fault injection for risk, durable entry intent, recovery and emergency controls."""

from dataclasses import replace
from decimal import Decimal as D
from unittest.mock import AsyncMock

import pytest

from scalper.engine.bots import BotRegistry
from scalper.engine.operations import prepare_entry, recover_entry
from scalper.engine.plan import PlanRejected, TradePlan
from scalper.engine.store import StateStore
from scalper.engine.trades import TradeHistory
from scalper.settings import AppSettings


@pytest.fixture
def rig(tmp_path):
    registry = BotRegistry()
    bot = registry.create(
        symbols=["SOLUSDT"], amount=D(20), leverage=5, strategy="the-minute-black"
    )
    plan = TradePlan(bot.id, 1, "SOLUSDT", "LONG", D(1), D(100), D(99), D(102), 5, 1234)
    client = AsyncMock()
    client.get_positions.return_value = {"result": []}
    client.open_orders.return_value = []
    client.list_algo_orders.return_value = []
    client.get_account.return_value = {
        "canTrade": True,
        "totalMarginBalance": "1000",
        "availableBalance": "1000",
        "totalInitialMargin": "0",
    }
    client.maintenance_rate.return_value = D("0.004")
    store = StateStore(tmp_path / "state.json")
    history = TradeHistory()
    settings = AppSettings(_env_file=None)
    return client, bot, plan, settings, store, registry, history


async def prepare(rig, plan=None):
    client, bot, original, settings, store, registry, history = rig
    await prepare_entry(client, bot, plan or original, 1000, settings, store, registry, history)


async def test_intent_and_signal_are_durable_before_any_order(rig):
    await prepare(rig)
    client, bot, plan, _, store, *_ = rig
    restored = store.load().bots[bot.id]
    assert restored.entry_intent["plan"]["symbol"] == "SOLUSDT"
    assert restored.entry_pending
    assert restored.last_signal["SOLUSDT"] == plan.signal_time
    client.place_order.assert_not_called()


async def test_same_candle_cannot_be_reentered(rig):
    await prepare(rig)
    with pytest.raises(PlanRejected, match="already been attempted"):
        await prepare(rig)


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"totalMarginBalance": "NaN"}, "non-finite"),
        ({"availableBalance": "10"}, "available margin"),
        ({"totalInitialMargin": "490"}, "aggregate"),
        ({"canTrade": False}, "permit"),
    ],
)
async def test_account_failures_prevent_entry(rig, change, reason):
    rig[0].get_account.return_value.update(change)
    with pytest.raises((ValueError, PlanRejected), match=reason):
        await prepare(rig)
    assert rig[1].entry_intent is None


async def test_risk_budget_is_based_on_stop_not_just_margin(rig):
    with pytest.raises(PlanRejected, match="risk budget"):
        await prepare(rig, replace(rig[2], stop=D(80)))


async def test_daily_loss_lock_survives_rebound_and_restart(rig):
    await prepare(rig)
    client, bot, _plan, _, store, *_ = rig
    bot.last_signal.clear()
    client.get_account.return_value["totalMarginBalance"] = "960"
    with pytest.raises(PlanRejected, match="daily equity loss"):
        await prepare(rig)
    assert StateStore(store.path).control["loss_locked"]
    client.get_account.return_value["totalMarginBalance"] = "1100"
    with pytest.raises(PlanRejected, match="daily equity loss"):
        await prepare(rig)


async def test_existing_orders_block_new_entry(rig):
    rig[0].open_orders.return_value = [{"orderId": 1}]
    with pytest.raises(PlanRejected, match="existing orders"):
        await prepare(rig)


async def test_persistence_failure_prevents_submission(rig, monkeypatch):
    monkeypatch.setattr(rig[4], "save", lambda *a: False)
    with pytest.raises(RuntimeError, match="durable"):
        await prepare(rig)
    rig[0].place_order.assert_not_called()


def filled_order(**overrides):
    return {"status": "FILLED", "executedQty": "1", "avgPrice": "100", "orderId": 7, **overrides}


async def test_recover_filled_entry_without_resubmitting_it(rig):
    await prepare(rig)
    client, bot, *_ = rig
    client.query_order.return_value = filled_order()
    client.get_positions.return_value = {
        "result": [
            {"symbol": "SOLUSDT", "positionAmt": "1", "entryPrice": "100", "markPrice": "100"}
        ]
    }
    client.list_algo_orders.return_value = [
        {"side": "SELL", "closePosition": True, "orderType": "STOP_MARKET", "triggerPrice": "99"}
    ]
    assert await recover_entry(client, bot)
    assert bot.positions["SOLUSDT"].stop_armed
    assert not bot.entry_pending
    client.place_order.assert_not_called()


async def test_partial_fill_is_cancelled_before_recovery(rig):
    await prepare(rig)
    client, bot, *_ = rig
    client.query_order.side_effect = [
        filled_order(status="PARTIALLY_FILLED"),
        filled_order(status="CANCELED"),
    ]
    assert not await recover_entry(client, bot)
    client.cancel_order.assert_awaited_once()
    assert bot.entry_pending  # Flat REST is not enough without closing fills.
    client.place_order.assert_not_called()


async def test_unknown_order_is_not_treated_as_no_fill(rig):
    await prepare(rig)
    client, bot, *_ = rig
    client.query_order.side_effect = TimeoutError("unknown")
    with pytest.raises(TimeoutError):
        await recover_entry(client, bot)
    assert bot.entry_pending
    client.place_order.assert_not_called()


async def test_terminal_unfilled_order_can_clear_intent(rig):
    await prepare(rig)
    client, bot, *_ = rig
    client.query_order.return_value = filled_order(status="CANCELED", executedQty="0")
    assert await recover_entry(client, bot)
    assert not bot.entry_pending and not bot.positions


async def test_crossed_stop_recovery_closes_reduce_only_and_keeps_pending(rig):
    await prepare(rig)
    client, bot, *_ = rig
    client.query_order.return_value = filled_order()
    client.get_positions.return_value = {
        "result": [
            {"symbol": "SOLUSDT", "positionAmt": "1", "entryPrice": "100", "markPrice": "98"}
        ]
    }
    assert not await recover_entry(client, bot)
    assert client.place_order.call_args.kwargs["reduce_only"] is True
    assert bot.entry_pending


def test_history_no_longer_discards_old_trades():
    assert TradeHistory()._trades.maxlen is None


async def test_panic_halts_durably_and_leaves_unrelated_positions_untouched(monkeypatch):
    from scalper.engine import __main__ as module
    from tests.unit.test_refresh_positions import engine_with, row

    engine, bot, client = engine_with(["SOLUSDT"], [], monkeypatch)
    client.get_positions = AsyncMock(
        side_effect=[
            {"result": [row("SOLUSDT", "2.36", "106"), row("BTCUSDT", "1", "106")]},
            {"result": [row("BTCUSDT", "1", "106")]},
            {"result": [row("BTCUSDT", "1", "106")]},
        ]
    )
    client.place_order = AsyncMock(return_value={"orderId": 1})
    monkeypatch.setattr(module, "ExchangeConfig", lambda: None)
    await engine._panic()
    sent = client.place_order.call_args.kwargs
    assert client.place_order.await_count == 1
    assert sent["symbol"] == "SOLUSDT" and sent["reduce_only"] is True
    assert bot.state == "stopped" and engine.safe_mode
    assert StateStore(engine.store.path).control["halted"] is True


async def test_continuous_cycle_ignores_stopped_and_one_shot_bots(monkeypatch):
    from tests.unit.test_refresh_positions import engine_with

    engine, bot, _ = engine_with([], [], monkeypatch)
    engine.safe_mode = False
    engine._open_bot = AsyncMock()
    await engine._signal_cycle()
    engine._open_bot.assert_not_awaited()
    bot.continuous = True
    bot.state = "stopped"
    await engine._signal_cycle()
    engine._open_bot.assert_not_awaited()
    bot.state = "running"
    await engine._signal_cycle()
    engine._open_bot.assert_awaited_once_with(bot)


async def test_halt_prevents_continuous_entries(monkeypatch):
    from tests.unit.test_refresh_positions import engine_with

    engine, bot, _ = engine_with([], [], monkeypatch)
    bot.continuous = True
    engine.safe_mode = False
    engine.store.control["halted"] = True
    engine._open_bot = AsyncMock()
    await engine._signal_cycle()
    engine._open_bot.assert_not_awaited()


def test_market_quantity_filters_are_enforced():
    from scalper.core.filters import SymbolFilters

    filters = SymbolFilters(
        "SOLUSDT",
        D("0.01"),
        D("0.01"),
        D("0.01"),
        D(5),
        max_qty=D(100),
        market_step_size=D("0.1"),
        market_max_qty=D(10),
    )
    with pytest.raises(ValueError, match="step"):
        filters.validate_market(D("1.01"))
    with pytest.raises(ValueError, match="limits"):
        filters.validate_market(D(11))
    filters.validate_market(D(1))


async def test_trade_history_splits_saturated_windows_without_losing_same_timestamp(monkeypatch):
    from scalper.config import ExchangeConfig
    from scalper.exchange.client import BinanceFuturesClient

    monkeypatch.setattr("scalper.exchange.client.time.time", lambda: 2)
    client = BinanceFuturesClient(ExchangeConfig(api_key="test", api_secret="test", _env_file=None))
    client._signed = AsyncMock()
    client._signed.side_effect = [
        {"result": [{"id": n} for n in range(1000)]},
        {"result": [{"id": 1, "time": 1000}]},
        {"result": [{"id": 2, "time": 2000}]},
    ]
    rows = await client.account_trades("SOLUSDT", 1000)
    assert [r["id"] for r in rows] == [1, 2]
    calls = client._signed.call_args_list
    assert calls[1].args[2]["endTime"] + 1 == calls[2].args[2]["startTime"]


async def test_account_key_change_is_refused_before_exchange_access(monkeypatch):
    from types import SimpleNamespace

    from tests.unit.test_refresh_positions import engine_with

    engine, _, _ = engine_with([], [], monkeypatch)
    engine.store.control["account_fingerprint"] = "different"
    with pytest.raises(ValueError, match="account key differs"):
        engine._check_account(SimpleNamespace(api_key="new-key"))


@pytest.fixture
def running_engine(monkeypatch):
    from scalper.config import ExchangeConfig
    from scalper.engine import __main__ as module
    from scalper.exchange.credentials import CredentialStatus
    from scalper.strategies import STRATEGIES
    from tests.unit.test_trader_plan_building import SOLUSDT, FakeMarket, TestStrategy, bars

    monkeypatch.setitem(STRATEGIES._strategies, "s", TestStrategy())
    config = ExchangeConfig(env="testnet", api_key="test", api_secret="test", _env_file=None)
    engine = module.Engine(AppSettings(BINANCE_ENV="testnet", _env_file=None))
    engine.safe_mode = False
    engine.credentials = CredentialStatus.accepted(
        "test", "testnet", balance="1000", can_trade=True
    )
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.max_leverage.return_value = 5
    client.get_positions.return_value = {"result": []}
    client.open_orders.return_value = []
    client.list_algo_orders.return_value = []
    client.get_account.return_value = {
        "canTrade": True,
        "totalMarginBalance": "1000",
        "availableBalance": "1000",
        "totalInitialMargin": "0",
    }
    client.maintenance_rate.return_value = D("0.004")
    client.place_order.return_value = {"orderId": 1}

    async def positions():
        if not client.place_order.await_count:
            return {"result": []}
        qty = client.place_order.call_args.kwargs["quantity"]
        return {
            "result": [
                {
                    "symbol": "SOLUSDT",
                    "positionAmt": str(qty),
                    "entryPrice": "105.61",
                    "markPrice": "105.61",
                }
            ]
        }

    client.get_positions.side_effect = positions

    class Market(FakeMarket):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def symbol_filters(self):
            return {"SOLUSDT": SOLUSDT}

    market = Market(bars(200, 105.2, 0.41))
    monkeypatch.setattr(module, "ExchangeConfig", lambda: config)
    monkeypatch.setattr(module, "BinanceFuturesClient", lambda config: client)
    monkeypatch.setattr(module, "MarketDataClient", lambda rest: market)
    return engine, client


async def test_create_runs_risk_checks_then_entry_then_bracket_and_persists(running_engine):
    engine, client = running_engine
    await engine.handle_command(
        {
            "type": "create_bot",
            "symbols": ["SOLUSDT"],
            "strategy": "s",
            "amount": "20",
            "leverage": 5,
        }
    )
    assert engine.last_action["ok"], engine.last_action
    client.place_order.assert_awaited_once()
    assert client.place_algo_order.await_count == 2
    bot = next(iter(engine.registry.bots.values()))
    saved = engine.store.load().bots[bot.id]
    assert saved.positions["SOLUSDT"].stop_armed
    assert not saved.entry_pending and saved.entry_intent is None
    assert saved.last_signal["SOLUSDT"] > 0
    await engine.handle_command(
        {
            "type": "create_bot",
            "symbols": ["SOLUSDT"],
            "strategy": "s",
            "amount": "20",
            "leverage": 5,
        }
    )
    client.place_order.assert_awaited_once()


async def test_entry_timeout_leaves_a_recoverable_durable_intent(running_engine):
    engine, client = running_engine
    client.place_order.side_effect = TimeoutError("ack lost")
    await engine.handle_command(
        {
            "type": "create_bot",
            "symbols": ["SOLUSDT"],
            "strategy": "s",
            "amount": "20",
            "leverage": 5,
        }
    )
    assert engine.safe_mode and not engine.last_action["ok"]
    bot = next(iter(engine.registry.bots.values()))
    saved = engine.store.load().bots[bot.id]
    assert saved.entry_pending and saved.entry_intent is not None
    client.place_order.assert_awaited_once()


async def test_engine_risk_refusal_never_places_an_order(running_engine):
    engine, client = running_engine
    client.get_account.return_value["availableBalance"] = "1"
    await engine.handle_command(
        {
            "type": "create_bot",
            "symbols": ["SOLUSDT"],
            "strategy": "s",
            "amount": "20",
            "leverage": 5,
        }
    )
    assert not engine.last_action["ok"]
    client.place_order.assert_not_called()


async def test_failed_reconcile_does_not_resume_a_halted_engine(running_engine):
    engine, client = running_engine
    from scalper.engine.reconciler import reconcile

    engine.reconciliation = reconcile([], [])
    engine.store.control["halted"] = True
    client.get_positions.side_effect = TimeoutError("unavailable")
    await engine.handle_command({"type": "resume"})
    assert engine.safe_mode and engine.store.control["halted"]
    assert not engine.last_action["ok"]


async def test_filled_order_with_lagging_flat_snapshot_remains_uncertain(rig):
    await prepare(rig)
    client, bot, *_ = rig
    client.query_order.return_value = filled_order()
    client.account_trades.return_value = []
    assert not await recover_entry(client, bot)
    assert bot.entry_pending
    client.place_order.assert_not_called()
