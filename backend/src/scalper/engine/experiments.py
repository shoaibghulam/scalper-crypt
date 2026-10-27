"""Verified per-strategy evidence and reversible entry disabling."""

from decimal import Decimal
from typing import Any

from scalper.engine.bots import BotRegistry
from scalper.engine.trades import TradeHistory


def disabled(control: dict[str, Any]) -> dict[str, str]:
    return dict(control.get("strategy_experiment", {}).get("disabled", {}))


def report(history: TradeHistory, control: dict[str, Any]) -> dict[str, Any]:
    from scalper.strategies import STRATEGIES

    settings = control.get("strategy_experiment", {})
    blocked = disabled(control)
    records = history.recent()
    ids = sorted(set(STRATEGIES.ids()) | {t.strategy for t in records})
    rows = []
    for key in ids:
        all_trades = [t for t in records if t.strategy == key]
        trades = [t for t in all_trades if t.verified]
        recent = trades[:20]
        losses = sum(t.realised < 0 for t in trades)
        wins = sum(t.realised > 0 for t in trades)
        negative = -sum((t.realised for t in trades if t.realised < 0), Decimal(0))
        positive = sum((t.realised for t in trades if t.realised > 0), Decimal(0))
        net = positive - negative
        recent_losses = sum(t.realised < 0 for t in recent)
        flagged = len(recent) == 20 and recent_losses >= 15
        rows.append(
            {
                "id": key,
                "name": STRATEGIES.get(key).name if key in STRATEGIES.ids() else key,
                "single_coin_timeframe": (
                    f"{STRATEGIES.get(key).signal_interval_s // 60}m"
                    if key in STRATEGIES.ids()
                    and STRATEGIES.get(key).signal_interval_s in (60, 300, 600)
                    and not STRATEGIES.get(key).requires_order_flow else None
                ),
                "trades": len(trades),
                "wins": wins,
                "losses": losses,
                "breakeven": len(trades) - wins - losses,
                "win_rate": 100 * wins / len(trades) if trades else None,
                "loss_rate": 100 * losses / len(trades) if trades else None,
                "net": float(net),
                "fees": float(sum((t.fees for t in trades), Decimal(0))),
                "expectancy": float(net / len(trades)) if trades else None,
                "profit_factor": float(positive / negative) if negative else None,
                "recent_trades": len(recent),
                "recent_losses": recent_losses,
                "flagged": flagged,
                "disabled": key in blocked,
                "reason": blocked.get(key),
                "unverified": len(all_trades) - len(trades),
            }
        )
    return {
        "rows": rows,
        "auto_disable": settings.get("auto_disable", False),
        "window": 20,
        "loss_threshold": 15,
        "persistent": history.persistent,
    }


def enforce(history: TradeHistory, control: dict[str, Any], registry: BotRegistry) -> None:
    settings = control.setdefault("strategy_experiment", {})
    blocked = disabled(control)
    if settings.get("auto_disable"):
        for row in report(history, control)["rows"]:
            if row["flagged"]:
                blocked.setdefault(
                    row["id"], "At least 15 losses in latest 20 verified closed trades"
                )
    settings["disabled"] = blocked
    for bot in registry.bots.values():
        if bot.strategy in blocked:
            registry.stop(bot.id)


def configure(command: dict[str, object], control: dict[str, Any]) -> None:
    from scalper.strategies import STRATEGIES

    settings = control.setdefault("strategy_experiment", {})
    if "auto_disable" in command:
        if not isinstance(command["auto_disable"], bool):
            raise ValueError("auto_disable must be boolean")
        settings["auto_disable"] = command["auto_disable"]
    if "strategy" in command:
        key = str(command["strategy"])
        if key not in STRATEGIES.ids() or not isinstance(command.get("disabled"), bool):
            raise ValueError("unknown strategy or invalid disabled flag")
        blocked = disabled(control)
        if command["disabled"]:
            blocked[key] = "Disabled by operator"
        else:
            blocked.pop(key, None)
        settings["disabled"] = blocked
