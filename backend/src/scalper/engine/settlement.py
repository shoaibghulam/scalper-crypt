"""Recover realized trading P&L from fills, never from a sampled mark price."""

import logging
from decimal import Decimal
from typing import Any

from scalper.engine.bots import Bot, ManagedPosition
from scalper.engine.trades import ClosedTrade
from scalper.exchange.client import BinanceFuturesClient

log = logging.getLogger(__name__)


async def exit_outcome(client: BinanceFuturesClient, symbol: str,
                       exits: list[dict[str, Any]]) -> str | None:
    """Use matched exchange order types, never profit or price proximity."""
    kinds: set[str] = set()
    for order_id in {int(row["orderId"]) for row in exits}:
        try:
            order = await client.query_order_id(symbol, order_id)
            if (order.get("symbol") != symbol or int(order.get("orderId", -1)) != order_id
                    or order.get("status") != "FILLED"):
                return None
            kind = order.get("origType") or order.get("type")
            if kind in {"TAKE_PROFIT", "TAKE_PROFIT_MARKET"}:
                kinds.add("tp")
            elif kind in {"STOP", "STOP_MARKET", "TRAILING_STOP_MARKET"}:
                kinds.add("sl")
            else:
                return None
        except Exception:
            # Missing attribution must not discard otherwise verified fills/P&L.
            log.warning("Exit order attribution unavailable for %s", symbol)
            return None
    return next(iter(kinds)) if len(kinds) == 1 else None


async def settle(
    client: BinanceFuturesClient, bot: Bot, position: ManagedPosition
) -> ClosedTrade | None:
    if position.opened_at <= 0 or position.entry_order_id is None:
        return None  # An adopted position has no proven entry-time boundary.
    rows = await client.account_trades(position.symbol, int(position.opened_at * 1000))
    if not rows:
        return None
    if any(r.get("side") not in {"BUY", "SELL"} for r in rows):
        raise ValueError("invalid trade side")
    # De-duplicate by the exchange's trade ID, not response order.
    rows = list({int(row["id"]): row for row in rows}.values())
    entry_side = "BUY" if position.side == "LONG" else "SELL"
    entries = [r for r in rows if r["side"] == entry_side]
    exits = [r for r in rows if r["side"] != entry_side]
    if not entries or not exits:
        return None
    if position.entry_order_id is not None and any(
        int(r["orderId"]) != position.entry_order_id for r in entries
    ):
        return None  # External adds or another trade: attribution is ambiguous.
    quantity = sum((Decimal(str(r["qty"])) for r in entries), Decimal(0))
    closed = sum((Decimal(str(r["qty"])) for r in exits), Decimal(0))
    if quantity <= 0 or closed != quantity:
        return None  # Partial history, partial close, or unrelated exposure.
    if any(r.get("commissionAsset") != "USDT" for r in rows):
        return None  # Never subtract BNB units as though they were USDT.
    prices = [Decimal(str(r["price"])) for r in rows]
    fees = [Decimal(str(r["commission"])) for r in rows]
    pnl = [Decimal(str(r["realizedPnl"])) for r in rows]
    if any(not n.is_finite() for n in [*prices, *fees, *pnl, quantity]):
        raise ValueError("non-finite trade history")
    entry = (
        sum((Decimal(str(r["price"])) * Decimal(str(r["qty"])) for r in entries), Decimal(0))
        / quantity
    )
    exit_price = (
        sum((Decimal(str(r["price"])) * Decimal(str(r["qty"])) for r in exits), Decimal(0)) / closed
    )
    # Price proximity cannot prove whether a stop, target, or manual exit fired.
    return ClosedTrade(
        symbol=position.symbol,
        bot=bot.name,
        strategy=bot.strategy,
        side=position.side,
        quantity=quantity,
        entry=entry,
        exit_price=exit_price,
        leverage=position.leverage,
        realised=sum(pnl, Decimal(0)) - sum(fees, Decimal(0)),
        fees=sum(fees, Decimal(0)),
        outcome=await exit_outcome(client, position.symbol, exits),
        opened_at=position.opened_at,
        closed_at=max(int(r["time"]) for r in exits) / 1000,
    )
