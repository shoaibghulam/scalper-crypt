"""Regression cases uncovered by the full-system audit; no exchange access."""

import json
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest

from scalper.config import Environment, ExchangeConfig
from scalper.engine.plan import PlanRejected, TradePlan
from scalper.engine.publisher import SnapshotPublisher
from scalper.engine.store import StateStore
from scalper.exchange.client import BinanceFuturesClient
from scalper.indicators.core import percentile_rank


@pytest.mark.parametrize("env", [Environment.PAPER, Environment.LIVE])
async def test_unarmed_environment_never_sends_mutations(env, monkeypatch):
    monkeypatch.delenv("TRADING_MODE", raising=False)
    config = ExchangeConfig(env=env, api_key="test", api_secret="test", _env_file=None)
    http = AsyncMock()
    async with BinanceFuturesClient(config, http=http) as client:
        with pytest.raises(RuntimeError):
            await client.set_leverage("SOLUSDT", 5)
    http.request.assert_not_awaited()


async def test_cancel_sweeps_both_namespaces_after_transport_failure():
    config = ExchangeConfig(env="testnet", api_key="test", api_secret="test", _env_file=None)
    client = BinanceFuturesClient(config)
    client._signed = AsyncMock(side_effect=[httpx.ReadTimeout("timeout"), {}])
    with pytest.raises(httpx.ReadTimeout):
        await client.cancel_all_orders("SOLUSDT")
    assert client._signed.await_count == 2


@pytest.mark.parametrize("message", ["[]", "null", "1", '"text"'])
async def test_non_object_command_does_not_disconnect_subscriber(message):
    handler = AsyncMock()
    publisher = SnapshotPublisher("127.0.0.1", 0, lambda: {}, handler)
    await publisher._dispatch(message)
    handler.assert_not_awaited()


@pytest.mark.parametrize("payload", [[], None, 42, {"bots": None}])
def test_malformed_state_root_does_not_crash_startup(tmp_path, payload):
    path = tmp_path / "state.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert StateStore(path).load().bots == {}


def test_percentile_cannot_exceed_one_hundred():
    assert percentile_rank([1, 2, 3], 4) == 100


def test_client_id_distinguishes_sanitized_names():
    def plan(bot_id):
        return TradePlan(bot_id, 1, "SOLUSDT", "LONG", Decimal(1),
                         Decimal(100), Decimal(99), Decimal(102), 5)
    assert plan("bot a").client_order_id("entry") != plan("bot_a").client_order_id("entry")


def test_invalid_side_fails_locally():
    from scalper.core.filters import SymbolFilters

    filters = SymbolFilters("SOLUSDT", Decimal("0.01"), Decimal("0.01"),
                            Decimal("0.01"), Decimal(5))
    plan = TradePlan("bot", 1, "SOLUSDT", "TYPO", Decimal(1),
                     Decimal(100), Decimal(101), Decimal(98), 5)
    with pytest.raises(PlanRejected, match="side"):
        plan.validate(filters)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "::"])
def test_unauthenticated_remote_bind_is_refused(host):
    from pydantic import ValidationError

    from scalper.settings import AppSettings
    with pytest.raises(ValidationError):
        AppSettings(API_HOST=host, _env_file=None)


def test_state_files_are_separated_by_environment(monkeypatch):
    monkeypatch.delenv("SCALPER_STATE_FILE")
    assert StateStore(environment="live").path != StateStore(environment="testnet").path


@pytest.mark.parametrize("override", [
    {"side": "BUY"}, {"closePosition": False},
    {"closePosition": False, "reduceOnly": True, "quantity": "0.5"},
    {"algoStatus": "CANCELED"},
])
async def test_adoption_rejects_orders_that_do_not_protect_the_position(override):
    from scalper.engine.adopt import AdoptionRefused, adopt_position
    from scalper.engine.reconciler import ExchangePosition
    order = {"side": "SELL", "closePosition": True,
             "orderType": "STOP_MARKET", "triggerPrice": "99", **override}
    client = AsyncMock()
    client.list_algo_orders.return_value = [order]
    with pytest.raises(AdoptionRefused):
        await adopt_position(client, ExchangePosition("SOLUSDT", 1, 100, mark_price=101))


async def test_profit_locking_stop_is_valid_below_current_mark():
    from scalper.engine.adopt import adopt_position
    from scalper.engine.reconciler import ExchangePosition
    client = AsyncMock()
    client.list_algo_orders.return_value = [
        {"side": "SELL", "closePosition": True, "orderType": "STOP_MARKET", "triggerPrice": "101"}
    ]
    position = await adopt_position(client, ExchangePosition("SOLUSDT", 1, 100, mark_price=102))
    assert position.stop_armed and not position.target_armed


async def test_missing_fills_remain_pending_and_survive_restart(monkeypatch):
    from tests.unit.test_refresh_positions import engine_with
    engine, bot, client = engine_with(["SOLUSDT"], [], monkeypatch)
    client.account_trades = AsyncMock(return_value=[])
    await engine.refresh_positions()
    assert engine.history.summary()["trades"] == 0
    assert "SOLUSDT" in bot.pending_closures
    assert "SOLUSDT" in engine.store.load().bots[bot.id].pending_closures
    assert not engine.registry.remove(bot.id)


async def test_exchange_fills_win_over_last_observed_mark(monkeypatch):
    from tests.unit.test_refresh_positions import engine_with
    engine, bot, client = engine_with(["SOLUSDT"], [], monkeypatch)
    bot.positions["SOLUSDT"].mark = Decimal("500")
    client.exit_price = Decimal("105.13")
    await engine.refresh_positions()
    trade = engine.history.recent()[0]
    assert trade.exit_price == Decimal("105.13") and trade.realised < 0
    assert trade.outcome is None
    await engine.refresh_positions()
    assert len(engine.history.recent()) == 1


async def test_non_usdt_commissions_do_not_corrupt_pnl(monkeypatch):
    from tests.unit.test_refresh_positions import engine_with
    engine, bot, client = engine_with(["SOLUSDT"], [], monkeypatch)
    rows = await client.account_trades("SOLUSDT", 1000000)
    rows[0]["commissionAsset"] = "BNB"
    client.account_trades = AsyncMock(return_value=rows)
    await engine.refresh_positions()
    assert not engine.history.recent()
    assert "SOLUSDT" in bot.pending_closures


def test_legacy_history_is_kept_but_excluded_from_verified_totals(tmp_path):
    from scalper.engine.store import _trade_from_json
    from scalper.engine.trades import TradeHistory
    raw = {"symbol": "SOLUSDT", "bot": "old", "strategy": "s", "side": "LONG",
           "quantity": "1", "entry": "100", "exit_price": "101", "leverage": 1,
           "realised": "1", "fees": "0", "outcome": "tp", "opened_at": 1, "closed_at": 2}
    history = TradeHistory()
    history.record(_trade_from_json(raw))
    assert len(history.recent()) == 1
    assert history.summary()["trades"] == 0
    assert history.today_totals()["realised_today"] == "0.00"


def test_bad_snapshot_never_replaces_last_valid_payload():
    from scalper.api.engine_link import EngineLink
    link = EngineLink()
    link.on_snapshot({"seq": 1}, now=1)
    with pytest.raises(ValueError):
        link.on_snapshot([], now=2)
    assert link.payload == {"seq": 1}


async def test_protect_orphan_places_a_stop_and_does_not_replace_existing_stop(monkeypatch):
    import time
    from types import SimpleNamespace

    from scalper.core.filters import SymbolFilters
    from scalper.engine.__main__ import Engine
    from scalper.engine.reconciler import ExchangePosition
    from scalper.settings import AppSettings

    engine = Engine(AppSettings(BINANCE_ENV="testnet", _env_file=None))
    client = AsyncMock()
    client.get_positions.return_value = {"result": [{"symbol": "SOLUSDT",
        "positionAmt": "1", "entryPrice": "100", "markPrice": "102"}]}
    client.list_algo_orders.return_value = []
    market = AsyncMock()
    market.candles.return_value = [SimpleNamespace(high=103, low=101, close=102,
        close_time=int(time.time() * 1000) - 1) for _ in range(20)]
    filters = {"SOLUSDT": SymbolFilters("SOLUSDT", Decimal("0.01"),
        Decimal("0.01"), Decimal("0.01"), Decimal("5"))}
    orphan = ExchangePosition("SOLUSDT", 1, 100, mark_price=102)
    await engine._protect_orphan(client, market, None, filters, orphan)
    order = client.place_algo_order.call_args.kwargs
    assert order["close_position"] is True and order["side"] == "SELL"
    assert order["trigger_price"] < Decimal(102)
    client.place_algo_order.reset_mock()
    client.list_algo_orders.return_value = [{"side": "SELL", "closePosition": True,
        "orderType": "STOP_MARKET", "triggerPrice": "99"}]
    await engine._protect_orphan(client, market, None, filters, orphan)
    client.place_algo_order.assert_not_awaited()


async def test_uncertain_entry_marker_survives_restart_and_blocks_reconciliation(monkeypatch):
    from tests.unit.test_refresh_positions import engine_with
    engine, bot, _ = engine_with([], [], monkeypatch)
    bot.entry_pending = True
    engine.store.save(engine.registry, engine.history)
    assert engine.store.load().bots[bot.id].entry_pending
    engine.safe_mode = False
    await engine.reconcile_account()
    assert engine.safe_mode
    assert not engine.registry.remove(bot.id)


def test_today_totals_exclude_yesterday():
    import time
    from dataclasses import replace

    from scalper.engine.store import _trade_from_json
    from scalper.engine.trades import TradeHistory
    raw = {"symbol": "SOLUSDT", "bot": "b", "strategy": "s", "side": "LONG",
           "quantity": "1", "entry": "100", "exit_price": "101", "leverage": 1,
           "realised": "1", "fees": "0", "outcome": None, "opened_at": 1,
           "closed_at": time.time(), "verified": True}
    today = _trade_from_json(raw)
    history = TradeHistory()
    history.record(today)
    history.record(replace(today, closed_at=today.closed_at - 86400, realised=Decimal(100)))
    assert history.today_totals()["realised_today"] == "1.00"
    assert history.summary()["net"] == 101
