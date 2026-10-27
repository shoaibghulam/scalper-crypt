"""Persisted, opt-in automatic strategy fleet; no orders are placed here."""

import re
from typing import Any

from scalper.engine.bots import BotRegistry
from scalper.engine.commands import AutoTradeRequest
from scalper.strategies import STRATEGIES


def recover_legacy_market_errors(registry: BotRegistry, config: dict[str, Any],
                                 disabled: set[str]) -> None:
    """Recheck old pre-entry HTTP failures once using status-aware handling."""
    if not config.get("enabled"):
        return
    for key in config.get("bot_ids", []):
        bot = registry.bots.get(key)
        if (bot is None or bot.state != "error" or not bot.continuous
                or bot.strategy in disabled or bot.positions or bot.pending_closures
                or bot.entry_pending or bot.entry_intent):
            continue
        match = re.fullmatch(
            r"(.+): (?:HTTPStatusError|BinanceError|Binance HTTP (?:418|429) on /fapi/v1/klines)",
            bot.error or "",
        )
        if match is None:
            continue
        symbol = match.group(1)
        if symbol not in bot.symbols:
            continue
        bot.state = "running"
        bot.error = None
        bot.symbol_notes[symbol] = "Rechecking market data after legacy HTTP failure"


def start_fleet(
    registry: BotRegistry,
    previous: dict[str, Any],
    request: AutoTradeRequest,
    disabled: set[str] | None = None,
) -> dict[str, Any]:
    ids = previous.get("bot_ids", [])
    old = [registry.bots[i] for i in ids if i in registry.bots]
    updating = bool(previous.get("enabled"))
    # Reuse bot identities and cumulative counters. An existing position or
    # pending settlement blocks only that bot's next entry, not the whole fleet.
    by_strategy = {b.strategy: b for b in old}
    created = []
    for strategy_id in sorted(
        STRATEGIES.ids(),
        key=lambda s: (
            STRATEGIES.get(s).requires_order_flow,
            STRATEGIES.get(s).signal_interval_s,
        ),
    ):
        if strategy_id in (disabled or set()):
            if strategy_id in by_strategy:
                registry.stop(by_strategy[strategy_id].id)
            continue
        strategy = STRATEGIES.get(strategy_id)
        bot = by_strategy.get(strategy_id)
        is_new = bot is None
        if bot is None:
            bot = registry.create(
                symbols=[],
                amount=request.amount,
                leverage=request.leverage,
                strategy=strategy_id,
                name=f"Auto - {strategy.name}",
                margin_mode="ISOLATED",
            )
        # These settings apply to future entries; exchange protection is untouched.
        bot.amount = request.amount
        bot.leverage = request.leverage
        bot.continuous = True
        if is_new or not updating:
            bot.state = "running"
        if (is_new or not updating) and not (
            bot.positions or bot.entry_pending or bot.pending_closures
        ):
            bot.error = None
        created.append(bot.id)
    # Defensive handling of legacy duplicates: retain their records/protection.
    for bot in old:
        if bot.id not in created:
            registry.stop(bot.id)
    return {
        **previous,
        "enabled": True,
        "amount": str(request.amount),
        "leverage": request.leverage,
        "bot_ids": created,
        "universe_limit": None,
        "universe": "all_usdt_perpetuals",
    }


def stop_fleet(registry: BotRegistry, previous: dict[str, Any]) -> dict[str, Any]:
    for bot_id in previous.get("bot_ids", []):
        registry.stop(bot_id)
    return {**previous, "enabled": False}
