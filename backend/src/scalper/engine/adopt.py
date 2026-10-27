"""Taking a position the engine did not open under management.

This is the only route from "the exchange holds this and we cannot explain it"
back to a managed position. The usual causes are an engine restart from before
state was persisted, and an operator opening a trade by hand on Binance.

The protective levels are read back from the exchange, never invented. A bot
that adopted a position and then attached a stop of its own choosing would
silently move a level the operator had already set. Worse, a bot that adopted a
position with no stop at all and recorded one anyway would show an armed stop
for a position the exchange will not close, which is the single most dangerous
thing this dashboard could display.

So the rule is asymmetric on purpose. A missing stop refuses the adoption. A
missing target does not, because the position is still protected and the target
only decides where the progress bar ends.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from scalper.core.fees import MIN_STOP_FRACTION
from scalper.engine.bots import ManagedPosition
from scalper.engine.plan import MIN_REWARD_TO_RISK
from scalper.engine.reconciler import ExchangePosition
from scalper.strategies.base import Levels

log = logging.getLogger("scalper.engine.adopt")

STOP_TYPES = frozenset({"STOP_MARKET", "STOP"})
TARGET_TYPES = frozenset({"TAKE_PROFIT_MARKET", "TAKE_PROFIT"})

#: Used only when the exchange holds a stop but no take-profit. The position is
#: protected either way; this decides where the bar ends, not where money goes.
DERIVED_TARGET_MULTIPLE = Decimal("2")

#: How many ATRs out a newly attached stop is placed. Matches the multiple the
#: trader uses for its own entries, so an adopted position is protected on the
#: same terms as one this engine opened.
PROTECTIVE_ATR_MULTIPLE = Decimal("1.5")


class AdoptionRefused(Exception):
    """This position cannot be adopted safely and must stay orphaned.

    Deliberately not a silent skip. An orphan keeps its warning banner, which
    is the correct outcome: the operator still needs to deal with it by hand.
    """


def _trigger_price(order: dict[str, Any]) -> Decimal:
    """The level an algo order fires at, refusing anything unusable.

    The migrated algo endpoint calls this triggerPrice; the pre-migration
    orders called it stopPrice. Both are read because an account can still hold
    orders placed before the change.
    """
    raw = order.get("triggerPrice", order.get("stopPrice"))
    try:
        price = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AdoptionRefused(f"unreadable trigger price {raw!r}") from exc
    if not price.is_finite() or price <= 0:
        raise AdoptionRefused(f"trigger price {raw!r} is not a usable price")
    return price


def needs_protection(orders: list[dict[str, Any]]) -> bool:
    """True when the exchange holds no stop for this position.

    A take-profit alone does not count. The downside is what a stop bounds,
    and an unprotected position with a target is still unbounded on the side
    that matters.
    """
    return _find_raw(orders, STOP_TYPES) is None


def protective_levels(*, entry: Decimal, atr: Decimal, side: str) -> Levels:
    """A stop and target for a position that arrived without them.

    Derived from volatility rather than invented, and floored by the same fee
    arithmetic as every other trade here: a stop closer than 0.45% pays more in
    fees than it risks, and one placed at a fixed percentage ignores whether
    the symbol actually moves that much in the time it would take to matter.
    """
    distance = max(atr * PROTECTIVE_ATR_MULTIPLE, entry * MIN_STOP_FRACTION)
    reward = distance * MIN_REWARD_TO_RISK
    if side == "LONG":
        return Levels(stop=entry - distance, target=entry + reward)
    return Levels(stop=entry + distance, target=entry - reward)


def _order_type(order: dict[str, Any]) -> str:
    """The order type, under whichever name this account happens to use.

    The algo namespace calls it orderType and reserves algoType for the
    category. Reading `type` alone found nothing on a live account holding
    both protective legs, and every adoption was then refused for a missing
    stop that was in fact present.
    """
    return str(order.get("orderType") or order.get("type") or "").upper()


def _find_raw(orders: list[dict[str, Any]], kinds: frozenset[str]) -> dict[str, Any] | None:
    for order in orders:
        if _order_type(order) in kinds:
            return order
    return None


def _find(orders: list[dict[str, Any]], kinds: frozenset[str]) -> Decimal | None:
    order = _find_raw(orders, kinds)
    return None if order is None else _trigger_price(order)


def closing_orders(orders: list[dict[str, Any]], orphan: ExchangePosition) -> list[dict[str, Any]]:
    """Only active, correctly sided orders covering this position count."""
    expected_side = "SELL" if orphan.quantity > 0 else "BUY"
    usable = []
    for order in orders:
        if order.get("symbol", orphan.symbol) != orphan.symbol:
            continue
        if order.get("side") != expected_side or order.get("positionSide", "BOTH") != "BOTH":
            continue
        if order.get("algoStatus", order.get("status", "NEW")) not in {"NEW", "WORKING"}:
            continue
        close_all = str(order.get("closePosition", False)).lower() == "true"
        reduce_only = str(order.get("reduceOnly", False)).lower() == "true"
        try:
            qty = Decimal(str(order.get("quantity", order.get("origQty", 0))))
            covers = qty.is_finite() and qty >= abs(Decimal(str(orphan.quantity)))
        except InvalidOperation:
            covers = False
        if close_all or (reduce_only and covers):
            usable.append(order)
    return usable


async def adopt_position(
    client: Any, orphan: ExchangePosition, leverage: int = 1
) -> ManagedPosition:
    """Rebuild a managed position from what the exchange actually holds.

    Raises AdoptionRefused rather than returning a position the engine cannot
    honestly manage.
    """
    if orphan.quantity == 0:
        raise AdoptionRefused("the position is flat")
    if orphan.entry_price <= 0:
        raise AdoptionRefused("the exchange reports no entry price")

    side = "LONG" if orphan.quantity > 0 else "SHORT"
    entry = Decimal(str(orphan.entry_price))

    orders = closing_orders(await client.list_algo_orders(orphan.symbol), orphan)
    stop = _find(orders, STOP_TYPES)
    if stop is None:
        raise AdoptionRefused(
            "the exchange holds no stop for it, so adopting would claim "
            "protection that does not exist"
        )

    # Geometry before anything else. A long stopped above its entry is not a
    # stop, and a position rendered as already past its own stop reads as an
    # imminent close that will never come.
    reference = Decimal(str(orphan.mark_price)) if orphan.mark_price is not None else entry
    if side == "LONG" and stop >= reference:
        raise AdoptionRefused(
            f"stop {stop} must sit below the current price {reference} for a long"
        )
    if side == "SHORT" and stop <= reference:
        raise AdoptionRefused(
            f"stop {stop} must sit above the current price {reference} for a short"
        )

    target = _find(orders, TARGET_TYPES)
    target_armed = target is not None
    if target is None:
        risk = max(abs(reference - stop), reference * MIN_STOP_FRACTION)
        target = (
            reference + risk * DERIVED_TARGET_MULTIPLE
            if side == "LONG"
            else reference - risk * DERIVED_TARGET_MULTIPLE
        )
        log.warning(
            "%s has a stop but no take-profit; deriving a target at %sR for "
            "display only. The exchange will not close this position at that "
            "level.",
            orphan.symbol,
            DERIVED_TARGET_MULTIPLE,
        )

    return ManagedPosition(
        symbol=orphan.symbol,
        side=side,
        # Always positive. The side carries the direction, and a negative
        # quantity would flip the sign of every profit figure downstream.
        quantity=abs(Decimal(str(orphan.quantity))),
        entry=entry,
        stop=stop,
        target=target,
        # Passed in from the account endpoint, which is the only place that
        # reports it: positionRisk does not. Defaulting to 1 and hoping
        # something corrected it later understated return on equity tenfold
        # and put the liquidation warning on the wrong side of its threshold.
        leverage=max(1, leverage),
        opened_at=0.0,
        # True because it was verified above, not assumed: the adoption is
        # refused outright when the exchange holds no stop.
        stop_armed=True,
        target_armed=target_armed,
        stop_client_id=(_find_raw(orders, STOP_TYPES) or {}).get("clientAlgoId"),
        target_client_id=(_find_raw(orders, TARGET_TYPES) or {}).get("clientAlgoId"),
    )
