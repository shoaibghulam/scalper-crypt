"""One-entry search with sizing bounded by a user-supplied capital budget."""

from dataclasses import replace
from decimal import Decimal
from typing import Any

from scalper.core.filters import SymbolFilters, round_qty_down
from scalper.engine.bots import BotRegistry
from scalper.engine.commands import SingleCoinRequest
from scalper.engine.plan import PlanRejected, TradePlan
from scalper.settings import AppSettings


def start_search(registry: BotRegistry, previous: dict[str, Any], request: SingleCoinRequest,
                 *, strategy: str
                 ) -> dict[str, Any]:
    if previous.get("enabled"):
        raise ValueError("Single Coin is already searching")
    old = registry.bots.get(previous.get("bot_id", ""))
    if old and (old.positions or old.entry_pending or old.pending_closures):
        raise ValueError("Wait for the previous Single Coin position to settle")
    if old:
        registry.remove(old.id)
    bot = registry.create(symbols=[], amount=request.amount, leverage=request.leverage,
                          strategy=strategy, name=f"Single Coin · {request.timeframe}",
                          margin_mode="ISOLATED")
    bot.continuous = True
    return {"enabled": True, "bot_id": bot.id, "amount": str(request.amount),
            "leverage": request.leverage, "timeframe": request.timeframe, "strategy": strategy}


def ranked_candidates(rows: list[dict[str, Any]], timeframe: str) -> list[dict[str, Any]]:
    from scalper.strategies import STRATEGIES

    interval = {"1m": 60, "5m": 300, "10m": 600}[timeframe]
    eligible = [r for r in rows if r["id"] in STRATEGIES.ids()
                and not r["disabled"] and not r["flagged"]
                and r["trades"] >= 1 and r["net"] > 0
                and STRATEGIES.get(r["id"]).signal_interval_s == interval
                and not STRATEGIES.get(r["id"]).requires_order_flow]
    return sorted(eligible, key=lambda r: (-r["wins"], -r["win_rate"], -r["trades"], r["id"]))


def size_to_budget(plan: TradePlan, filters: SymbolFilters, budget: Decimal,
                   account: dict[str, Any], settings: AppSettings) -> TradePlan:
    equity = Decimal(str(account["totalMarginBalance"]))
    available = Decimal(str(account["availableBalance"]))
    used = Decimal(str(account["totalInitialMargin"]))
    if any(not n.is_finite() or n < 0 for n in (equity, available, used, budget)):
        raise PlanRejected("invalid account values for budget sizing")
    capital = min(budget, equity)
    margin = min(capital * settings.max_margin_fraction,
                 max(Decimal(0), equity * settings.max_margin_fraction - used))
    fee_per_unit = plan.entry * Decimal("0.001")
    quantity = min(
        plan.quantity,
        capital * settings.max_trade_risk / (plan.stop_distance + fee_per_unit),
        margin * plan.leverage / plan.entry,
        available * Decimal("0.95") / (plan.entry / plan.leverage + fee_per_unit),
    )
    sized = replace(plan, quantity=round_qty_down(quantity, filters.step_size))
    try:
        sized.validate(filters)
        filters.validate_market(sized.quantity)
    except (PlanRejected, ValueError) as exc:
        raise PlanRejected(f"Budget cannot safely meet this coin's order minimum: {exc}") from exc
    return sized
