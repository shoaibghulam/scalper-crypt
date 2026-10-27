"""Durable pre-entry checks and conservative recovery of interrupted entries."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from scalper.engine.account import open_positions
from scalper.engine.adopt import AdoptionRefused, adopt_position
from scalper.engine.bots import Bot, BotRegistry, ManagedPosition
from scalper.engine.executor import OrderExecutor
from scalper.engine.plan import PlanRejected, TradePlan
from scalper.engine.settlement import settle
from scalper.engine.store import StateStore
from scalper.engine.trades import TradeHistory
from scalper.exchange.client import BinanceFuturesClient
from scalper.settings import AppSettings


def persist(store: StateStore, registry: BotRegistry, history: TradeHistory) -> None:
    if not store.save(registry, history):
        raise RuntimeError("durable state write failed; trading disabled")


def number(raw: Any) -> Decimal:
    result = Decimal(str(raw))
    if not result.is_finite():
        raise ValueError("non-finite account value")
    return result


async def prepare_entry(
    client: BinanceFuturesClient,
    bot: Bot,
    plan: TradePlan,
    opened_at: float,
    settings: AppSettings,
    store: StateStore,
    registry: BotRegistry,
    history: TradeHistory,
) -> None:
    if store.control.get("halted"):
        raise PlanRejected("trading halted; resume after reconciliation")
    if bot.last_signal.get(plan.symbol, -1) >= plan.signal_time:
        raise PlanRejected("this closed-candle signal has already been attempted")
    if plan.leverage > settings.max_leverage:
        raise PlanRejected(f"configured leverage cap is {settings.max_leverage}x")
    if bot.margin_mode != "ISOLATED":
        raise PlanRejected("production risk controls require isolated margin")
    positions = await open_positions(client)
    if len(positions) >= settings.max_positions or any(p.symbol == plan.symbol for p in positions):
        raise PlanRejected("position count or symbol ownership limit")
    if await client.open_orders(plan.symbol) or await client.list_algo_orders(plan.symbol):
        raise PlanRejected("existing orders must be reconciled before a new entry")
    account = await client.get_account()
    if account.get("canTrade") is not True:
        raise PlanRejected("exchange account does not permit trading")
    equity = number(account["totalMarginBalance"])
    available = number(account["availableBalance"])
    initial_margin = number(account["totalInitialMargin"])
    if equity <= 0 or available < 0 or initial_margin < 0:
        raise PlanRejected("invalid account equity or margin")
    day = datetime.now(UTC).date().isoformat()
    if store.control.get("risk_day") != day:
        store.control.update(risk_day=day, start_equity=str(equity), loss_locked=False)
        persist(store, registry, history)
    baseline = number(store.control["start_equity"])
    if equity <= baseline * (1 - settings.daily_loss_fraction):
        store.control["loss_locked"] = True
        persist(store, registry, history)
    if store.control.get("loss_locked"):
        raise PlanRejected("UTC daily equity loss limit reached")
    # Include a conservative round-trip fee allowance in planned loss/margin.
    fees = plan.notional * Decimal("0.001")
    loss = plan.quantity * plan.stop_distance + fees
    if loss > equity * settings.max_trade_risk:
        raise PlanRejected("planned loss including fees exceeds account risk budget")
    margin = plan.notional / plan.leverage
    if margin + fees > available * Decimal("0.95"):
        raise PlanRejected("insufficient available margin with reserve")
    if initial_margin + margin > equity * settings.max_margin_fraction:
        raise PlanRejected("aggregate account margin limit exceeded")
    mmr = await client.maintenance_rate(plan.symbol, plan.notional, plan.leverage)
    from scalper.risk.sizing import MIN_LIQUIDATION_STOP_MULTIPLE, liquidation_headroom

    if (
        liquidation_headroom(leverage=plan.leverage, mmr=mmr, stop_fraction=plan.stop_fraction)
        < MIN_LIQUIDATION_STOP_MULTIPLE
    ):
        raise PlanRejected("exchange maintenance tier leaves insufficient liquidation headroom")
    values = {
        key: str(value) if isinstance(value, Decimal) else value
        for key, value in asdict(plan).items()
    }
    bot.entry_intent = {"plan": values, "opened_at": opened_at}
    bot.entry_pending = True
    bot.last_signal[plan.symbol] = plan.signal_time
    persist(store, registry, history)


def intent_plan(bot: Bot) -> TradePlan:
    if not bot.entry_intent:
        raise ValueError("legacy uncertain entry has no recoverable order identifier")
    raw = dict(bot.entry_intent["plan"])
    for key in ("quantity", "entry", "stop", "target"):
        raw[key] = number(raw[key])
    return TradePlan(**raw)


async def recover_entry(client: BinanceFuturesClient, bot: Bot) -> bool:
    """Return true only after a terminal order and its exposure are accounted for.

    Order-not-found and transport failures deliberately propagate: absence in one
    query cannot prove that the exchange never accepted a timed-out submission.
    """
    plan = intent_plan(bot)
    assert bot.entry_intent is not None
    order = await client.query_order(plan.symbol, plan.client_order_id("entry"))
    if order.get("status") in {"NEW", "PARTIALLY_FILLED"}:
        await client.cancel_order(plan.symbol, plan.client_order_id("entry"))
        order = await client.query_order(plan.symbol, plan.client_order_id("entry"))
    if order.get("status") not in {"FILLED", "CANCELED", "EXPIRED", "EXPIRED_IN_MATCH", "REJECTED"}:
        return False
    if (
        order.get("symbol", plan.symbol) != plan.symbol
        or order.get("side", plan.entry_side) != plan.entry_side
    ):
        raise ValueError("recovery order identity mismatch")
    executed = number(order.get("executedQty", 0))
    live = next((p for p in await open_positions(client) if p.symbol == plan.symbol), None)
    if executed == 0:
        if live is not None:
            raise ValueError("unrelated exposure blocks recovery")
        bot.entry_pending, bot.entry_intent = False, None
        return True
    if executed < 0 or executed > plan.quantity:
        raise ValueError("unexpected execution quantity")
    if number(order["avgPrice"]) <= 0:
        raise ValueError("recovered execution has no valid price")
    position = bot.positions.get(plan.symbol) or ManagedPosition(
        symbol=plan.symbol,
        side=plan.side,
        quantity=executed,
        entry=number(order["avgPrice"]),
        stop=plan.stop,
        target=plan.target,
        leverage=plan.leverage,
        opened_at=float(bot.entry_intent["opened_at"]),
        entry_order_id=int(order["orderId"]),
        stop_client_id=plan.client_order_id("sl"),
        target_client_id=plan.client_order_id("tp"),
    )
    if live is None:
        # Position REST may lag matching. A filled order plus a flat snapshot
        # is not proof of a completed round trip; require balanced fill history.
        if await settle(client, bot, position) is None:
            return False
        bot.positions.pop(plan.symbol, None)
        bot.pending_closures[plan.symbol] = position
    else:
        qty = Decimal(str(live.quantity))
        if (qty > 0) != plan.is_long or abs(qty) != executed:
            raise ValueError("exchange exposure differs from recovered fill")
        bot.positions[plan.symbol] = position
        position.entry = Decimal(str(live.entry_price))
        position.quantity = abs(qty)
        mark = Decimal(str(live.mark_price or live.entry_price))
        stop_crossed = mark <= plan.stop if plan.is_long else mark >= plan.stop
        bracket_invalid = not (
            plan.stop < position.entry < plan.target
            if plan.is_long
            else plan.target < position.entry < plan.stop
        )
        stop_crossed = stop_crossed or bracket_invalid
        if stop_crossed:
            await client.place_order(
                symbol=plan.symbol,
                side=plan.exit_side,
                order_type="MARKET",
                quantity=abs(qty),
                reduce_only=True,
                client_order_id=plan.client_order_id("recovery"),
            )
            return False  # Verify flatness on the next pass; retain the intent.
        try:
            protected = await adopt_position(client, live, plan.leverage)
            position.stop, position.target = protected.stop, protected.target
            position.stop_armed, position.target_armed = True, protected.target_armed
        except AdoptionRefused:
            position.target_armed = await OrderExecutor(client).attach_bracket(
                replace(plan, quantity=abs(qty), entry=position.entry)
            )
            position.stop_armed = True
    bot.entry_pending, bot.entry_intent = False, None
    return True
