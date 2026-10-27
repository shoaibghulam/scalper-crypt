import time
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from scalper.config import Environment
from scalper.engine.bots import BotRegistry
from scalper.engine.commands import SingleCoinRequest
from scalper.engine.plan import PlanRejected, TradePlan
from scalper.engine.single_coin import size_to_budget, start_search
from scalper.settings import AppSettings
from tests.unit.test_trader_plan_building import SOLUSDT


def test_ten_dollar_budget_limits_loss_and_margin_even_on_large_account():
    plan = TradePlan("s", 1, "SOLUSDT", "LONG", D(1), D(100), D(99), D("101.5"), 3)
    account = {"totalMarginBalance": "10000", "availableBalance": "10000",
               "totalInitialMargin": "0"}
    sized = size_to_budget(plan, SOLUSDT, D(10), account, AppSettings())
    assert sized.quantity * (sized.stop_distance + sized.entry * D(".001")) <= D(".1")
    assert sized.notional / sized.leverage <= D(5)
    assert sized.quantity < plan.quantity


def test_too_small_budget_does_not_round_up_to_exchange_minimum():
    plan = TradePlan("s", 1, "SOLUSDT", "LONG", D(1), D(100), D(99), D("101.5"), 3)
    with pytest.raises(PlanRejected, match="Budget cannot safely"):
        size_to_budget(plan, SOLUSDT, D(".1"), {"totalMarginBalance": "10",
                       "availableBalance": "10", "totalInitialMargin": "0"}, AppSettings())


def test_timeframe_selection_and_duplicate_search_guard():
    registry = BotRegistry()
    request = SingleCoinRequest(amount="10", leverage=3, timeframe="5m")
    config = start_search(registry, {}, request, strategy="rolling-breakout-retest")
    assert registry.bots[config["bot_id"]].strategy == "rolling-breakout-retest"
    with pytest.raises(ValueError, match="already searching"):
        start_search(registry, config, request, strategy="rolling-breakout-retest")
    with pytest.raises(ValidationError):
        SingleCoinRequest(amount="10", leverage=3, timeframe="2m")


async def test_only_first_successful_coin_opens_and_search_is_persisted_off(monkeypatch):
    from scalper.engine import __main__ as module
    from scalper.engine.store import StateStore
    from tests.unit.test_refresh_positions import a_position, engine_with

    engine, watching, _ = engine_with([], [], monkeypatch)
    watching.symbols = ["SOLUSDT", "ETHUSDT"]
    watching.strategy = "ema-trend-pullback"
    watching.amount = D(10)
    watching.continuous = True
    engine.safe_mode = False
    engine.store.control["single_coin"] = {"enabled": True, "bot_id": watching.id}
    monkeypatch.setattr(module, "ExchangeConfig", lambda: SimpleNamespace(
        env=Environment.TESTNET, api_key="test", rest="https://example.invalid",
        assert_armed=lambda: None,
    ))
    market = AsyncMock()
    market.__aenter__.return_value = market
    market.symbol_filters.return_value = dict.fromkeys(watching.symbols, SOLUSDT)
    monkeypatch.setattr(module, "MarketDataClient", lambda url: market)

    async def opening(bot, symbol, *args, **kwargs):
        bot.positions[symbol] = a_position(symbol)
        return bot.positions[symbol]

    opener = AsyncMock(side_effect=opening)
    monkeypatch.setattr(module, "open_for_bot", opener)
    await engine._open_bot(watching)
    assert opener.await_count == 1
    assert opener.call_args.kwargs["budget"] == 10
    assert not watching.continuous
    assert not StateStore(engine.store.path).control["single_coin"]["enabled"]
    assert engine.last_action["kind"] == "single_coin"


async def test_recovered_fill_consumes_single_search(monkeypatch):
    from tests.unit.test_refresh_positions import a_position, engine_with

    engine, watching, client = engine_with([], [], monkeypatch)
    watching.entry_pending, watching.entry_intent = True, {"test": True}
    watching.continuous = True
    engine.store.control["single_coin"] = {"enabled": True, "bot_id": watching.id}

    async def recover(client, bot):
        bot.entry_pending, bot.entry_intent = False, None
        bot.positions["SOLUSDT"] = a_position("SOLUSDT")
        return True

    monkeypatch.setattr("scalper.engine.__main__.recover_entry", recover)
    await engine._recover_entries(client)
    assert not watching.continuous
    assert not engine.store.control["single_coin"]["enabled"]


def test_single_coin_http_validates_timeframe_and_queues_valid_request():
    from tests.unit.test_api_app import client

    api = client()
    api.app.state.engine.on_snapshot({"seq": 1}, now=time.monotonic())
    headers = {"Origin": "http://testserver"}
    assert api.post("/api/single-coin/start", headers=headers,
                    json={"amount": "10", "leverage": 3, "timeframe": "2m"}).status_code == 422
    assert api.post("/api/single-coin/start", headers=headers,
                    json={"amount": "10", "leverage": 3, "timeframe": "1m"}).status_code == 202


def test_ranking_excludes_untraded_losing_disabled_flagged_and_wrong_timeframe():
    from scalper.engine.single_coin import ranked_candidates
    base = dict(id="ema-trend-pullback", trades=20, wins=12, net=10, win_rate=60,
                disabled=False, flagged=False)
    assert ranked_candidates([base], "1m") == [base]
    assert not ranked_candidates([base], "5m")
    for change in ({"trades": 0}, {"net": 0}, {"net": -1}, {"disabled": True}, {"flagged": True}):
        assert not ranked_candidates([{**base, **change}], "1m")
    higher = {**base, "id": "the-minute-black", "wins": 14, "win_rate": 70}
    assert ranked_candidates([base, higher], "1m")[0] == higher
    early = {**higher, "trades": 1, "wins": 1, "win_rate": 100}
    assert ranked_candidates([base, early], "1m")[0] == base
    richer = {**early, "net": 1000}
    assert ranked_candidates([base, richer], "1m")[0] == base
