from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scalper.config import Environment
from scalper.engine.auto_trade import start_fleet, stop_fleet
from scalper.engine.bots import BotRegistry
from scalper.engine.commands import AutoTradeRequest
from scalper.engine.screening import screen_watchlist
from scalper.strategies import STRATEGIES
from tests.unit.test_ema_pullback import context


def request():
    return AutoTradeRequest(amount=Decimal("25"), leverage=3)


def test_start_all_strategies_once_and_stop_preserves_positions():
    registry = BotRegistry()
    config = start_fleet(registry, {}, request())
    assert len(registry.bots) == len(STRATEGIES.ids())
    assert all(b.continuous and b.amount == 25 and b.leverage == 3 for b in registry.bots.values())
    synced = start_fleet(registry, config, request())
    assert synced["bot_ids"] == config["bot_ids"]
    assert len(registry.bots) == len(STRATEGIES.ids())
    first = next(iter(registry.bots.values()))
    first.positions["SOLUSDT"] = object()
    stopped = stop_fleet(registry, config)
    assert not stopped["enabled"]
    assert "SOLUSDT" in first.positions
    assert all(b.state == "stopped" for b in registry.bots.values())
    restarted = start_fleet(registry, stopped, request())
    assert restarted["enabled"]
    assert first.id in restarted["bot_ids"]
    assert "SOLUSDT" in first.positions
    assert len(registry.bots) == len(STRATEGIES.ids())


async def test_engine_start_persists_and_rejects_above_cap(monkeypatch):
    from tests.unit.test_refresh_positions import engine_with

    engine, manual, _ = engine_with([], [], monkeypatch)
    engine.safe_mode = False
    monkeypatch.setattr(
        "scalper.engine.__main__.ExchangeConfig",
        lambda: SimpleNamespace(
            env=Environment.TESTNET,
            api_key="test",
            assert_armed=lambda: None,
        ),
    )
    await engine._configure_auto_trade({"type": "start_auto_trade", "amount": "25", "leverage": 20})
    assert "cap" in engine.last_action["detail"]
    assert not engine.store.control.get("auto_trade")
    await engine._configure_auto_trade({"type": "start_auto_trade", "amount": "25", "leverage": 3})
    assert engine.store.control["auto_trade"]["enabled"]
    assert len(engine.store.load().bots) == len(STRATEGIES.ids()) + 1
    original_ids = engine.store.control["auto_trade"]["bot_ids"][:]
    await engine._configure_auto_trade({"type": "start_auto_trade", "amount": "40", "leverage": 5})
    assert engine.last_action["ok"] and "updated" in engine.last_action["detail"]
    assert engine.store.control["auto_trade"]["bot_ids"] == original_ids
    assert all(engine.store.load().bots[key].amount == 40 for key in original_ids)
    await engine._configure_auto_trade({"type": "stop_auto_trade"})
    assert not engine.store.control["auto_trade"]["enabled"]
    assert manual.state == "running"


@pytest.mark.parametrize("mode,safe", [(Environment.LIVE, False), (Environment.TESTNET, True)])
async def test_auto_refuses_live_or_unsafe_account(monkeypatch, mode, safe):
    from tests.unit.test_refresh_positions import engine_with

    engine, _, _ = engine_with([], [], monkeypatch)
    engine.settings.env = mode
    engine.safe_mode = safe
    await engine._configure_auto_trade({"type": "start_auto_trade", "amount": "25", "leverage": 3})
    assert not engine.last_action["ok"]
    assert not engine.store.control.get("auto_trade")


async def test_auto_discovers_liquid_symbols_without_changing_manual_selection(monkeypatch):
    from tests.unit.test_research_integration import bot

    ctx = context()
    auto, manual = bot("ema-trend-pullback"), bot("ema-trend-pullback")
    auto.id, manual.id = "auto", "manual"
    auto.symbols, manual.symbols = [], ["MANUALUSDT"]
    market = AsyncMock()
    market.symbol_filters.return_value = {"SOLUSDT": object()}
    market.tickers.return_value = {"SOLUSDT": ctx.ticker}
    market.book_tops.return_value = {"SOLUSDT": ctx.book}
    market.candles_for.return_value = {"SOLUSDT": ctx.candles_1m}
    monkeypatch.setattr(
        "scalper.engine.screening.evaluate_symbol",
        lambda *args: SimpleNamespace(setup_ready=False, setup_note="Waiting"),
    )
    await screen_watchlist([auto, manual], market, {auto.id})
    assert auto.symbols == ["SOLUSDT"]
    assert manual.symbols == ["MANUALUSDT"]


def test_http_validation_and_live_mode_rejection():
    from tests.unit.test_api_app import client

    headers = {"Origin": "http://testserver"}
    assert (
        client()
        .post("/api/auto-trade/start", headers=headers, json={"amount": "0", "leverage": 3})
        .status_code
        == 422
    )
    assert (
        client(Environment.LIVE)
        .post("/api/auto-trade/start", headers=headers, json={"amount": "25", "leverage": 3})
        .status_code
        == 400
    )
    assert client().post("/api/auto-trade/stop", headers=headers).status_code == 503


def test_scanners_use_execution_environment(monkeypatch):
    # Creation only: do not run or connect scanners in this regression test.
    from unittest.mock import Mock

    from scalper.api.app import create_app
    from scalper.config import TESTNET_REST
    from scalper.settings import AppSettings

    factory = Mock()
    monkeypatch.setattr("scalper.api.app.ScannerService", factory)
    create_app(AppSettings(BINANCE_ENV=Environment.TESTNET))
    assert factory.call_count == len(STRATEGIES.ids())
    assert all(call.args[0] == TESTNET_REST for call in factory.call_args_list)


async def test_all_eligible_coins_no_20_or_60_cap_and_shared_downloads(monkeypatch):
    from tests.unit.test_research_integration import bot

    ctx = context()
    names = [f"COIN{i}USDT" for i in range(85)]
    bots = [bot(key) for key in ("lab-01", "user-20", "funded-price-action", "500-backtest-v1")]
    for i, b in enumerate(bots):
        b.id = str(i)
        b.symbols = []
    market = AsyncMock()
    market.symbol_filters.return_value = dict.fromkeys(names, object())
    market.tickers.return_value = dict.fromkeys(names, ctx.ticker)
    market.book_tops.return_value = dict.fromkeys(names, ctx.book)
    market.candles_for.return_value = dict.fromkeys(names, ctx.candles_1m)
    monkeypatch.setattr(
        "scalper.engine.screening.evaluate_symbol",
        lambda *args: SimpleNamespace(setup_ready=False, setup_note="Waiting"),
    )
    coverage = {}
    await screen_watchlist(bots, market, {b.id for b in bots}, coverage)
    assert all(set(b.symbols) == set(names) for b in bots)
    assert market.candles_for.await_count == 2
    assert coverage["universe"] == coverage["eligible"] == 85
    assert coverage["completed"]


@pytest.mark.parametrize("pending", ["entry", "closure", "position"])
def test_restart_reuses_unsettled_bot_and_preserves_strategy_counters(pending):
    registry = BotRegistry()
    config = start_fleet(registry, {}, request())
    first = next(iter(registry.bots.values()))
    first.realised = Decimal("12.34")
    first.tp_hits = 3
    first.signal_seq = 42
    if pending == "entry":
        first.entry_pending = True
        first.entry_intent = {"client_id": "preserve-me"}
    elif pending == "closure":
        first.pending_closures["SOLUSDT"] = object()
    else:
        first.positions["SOLUSDT"] = object()
    stopped = stop_fleet(registry, config)
    resumed = start_fleet(registry, stopped, request())
    assert set(resumed["bot_ids"]) == set(config["bot_ids"])
    assert registry.bots[first.id] is first
    assert first.realised == Decimal("12.34") and first.tp_hits == 3 and first.signal_seq == 42
    assert first.entry_pending or first.pending_closures or first.positions
    assert resumed["universe_limit"] is None
    if pending == "entry":
        assert first.entry_intent == {"client_id": "preserve-me"}


def test_running_sync_adds_missing_updates_defaults_and_preserves_state():
    registry = BotRegistry()
    config = start_fleet(registry, {}, request())
    bots = list(registry.bots.values())
    held, stopped, failed, missing, disabled = bots[:5]
    position = SimpleNamespace(leverage=3, quantity=Decimal('2'), stop=Decimal('10'))
    held.positions['SOLUSDT'] = position
    held.realised = Decimal('7.25')
    held.entry_pending = True
    held.entry_intent = {'amount': '25', 'leverage': 3}
    held.pending_closures['OLDUSDT'] = object()
    stopped.state = 'stopped'
    failed.state, failed.error = 'error', 'Needs attention'
    del registry.bots[missing.id]
    manual = registry.create(symbols=[], amount=Decimal('9'), leverage=2,
                             strategy=held.strategy)
    updated = start_fleet(registry, config,
                          AutoTradeRequest(amount=Decimal('40'), leverage=5),
                          {disabled.strategy})
    assert len(updated['bot_ids']) == len(STRATEGIES.ids()) - 1
    fleet = [registry.bots[key] for key in updated['bot_ids']]
    assert len({bot.strategy for bot in fleet}) == len(fleet)
    assert all(bot.amount == 40 and bot.leverage == 5 for bot in fleet)
    assert held.positions['SOLUSDT'] is position and position.leverage == 3
    assert held.realised == Decimal('7.25')
    assert held.entry_pending and held.entry_intent == {'amount': '25', 'leverage': 3}
    assert 'OLDUSDT' in held.pending_closures
    assert stopped.state == 'stopped'
    assert failed.state == 'error' and failed.error == 'Needs attention'
    assert disabled.state == 'stopped' and disabled.id not in updated['bot_ids']
    assert manual.amount == 9 and manual.leverage == 2
    assert any(bot.strategy == missing.strategy and bot.id != missing.id for bot in fleet)
    repeated = start_fleet(registry, updated,
                           AutoTradeRequest(amount=Decimal('40'), leverage=5),
                           {disabled.strategy})
    assert repeated['bot_ids'] == updated['bot_ids']

@pytest.mark.parametrize('legacy_error', ['HTTPStatusError', 'BinanceError'])
@pytest.mark.parametrize('blocked', ['none', 'stopped', 'position', 'intent', 'disabled', 'fleet_off'])
def test_legacy_http_recovery_is_limited_to_safe_active_fleet_bots(blocked, legacy_error):
    from scalper.engine.auto_trade import recover_legacy_market_errors
    registry = BotRegistry()
    config = start_fleet(registry, {}, request())
    bot = registry.bots[config['bot_ids'][0]]
    bot.symbols = ['XTZUSDT']
    bot.state, bot.error = 'error', f'XTZUSDT: {legacy_error}'
    if blocked == 'stopped':
        bot.state = 'stopped'
    if blocked == 'position':
        bot.positions['XTZUSDT'] = object()
    if blocked == 'intent':
        bot.entry_intent = {'uncertain': True}
    if blocked == 'fleet_off':
        config['enabled'] = False
    recover_legacy_market_errors(registry, config, {bot.strategy} if blocked == 'disabled' else set())
    assert (bot.state == 'running') == (blocked == 'none')

@pytest.mark.parametrize('status', [418, 429])
def test_known_rate_limit_errors_can_recover(status):
    from scalper.engine.auto_trade import recover_legacy_market_errors
    registry = BotRegistry()
    config = start_fleet(registry, {}, request())
    bot = registry.bots[config['bot_ids'][0]]
    bot.symbols = ['BCHUSDT']
    bot.state, bot.error = 'error', f'BCHUSDT: Binance HTTP {status} on /fapi/v1/klines'
    recover_legacy_market_errors(registry, config, set())
    assert bot.state == 'running' and bot.error is None
