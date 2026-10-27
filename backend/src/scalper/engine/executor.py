"""Turning a plan into orders on the exchange.

The sequence matters and is not interchangeable.

Leverage and margin type are set before the entry, because changing leverage on
an open position recalculates the liquidation price immediately and can move
liquidation into the current market.

The bracket attaches only after the entry has filled, because there is nothing
to protect until then, and a protective order on a position that does not exist
is rejected.

Between those two steps lies the state this module exists to handle: the entry
filled and the stop-loss did not attach. That position has unbounded downside
and no automatic exit. Logging it and continuing is not an option, so the stop
is retried, and if it still will not attach the position is closed at market.
A small certain loss is the correct trade against an unbounded uncertain one.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from scalper.core.filters import SymbolFilters
from scalper.engine.plan import TradePlan
from scalper.exchange.client import BinanceFuturesClient
from scalper.exchange.errors import BinanceError

log = logging.getLogger("scalper.engine.executor")

STOP_ATTEMPTS = 3
RETRY_DELAY_S = 0.4


class UnprotectedPosition(Exception):
    """A stop could not be attached. The position was closed; alert the operator."""


@dataclass(frozen=True)
class ExecutionResult:
    entry_order_id: int | None
    client_order_id: str
    raw: dict[str, Any]


class OrderExecutor:
    def __init__(self, client: BinanceFuturesClient) -> None:
        self.client = client

    async def open(self, plan: TradePlan, filters: SymbolFilters) -> ExecutionResult:
        """Prepare the symbol and place the entry.

        Validation happens first and locally, so a plan that should not trade
        never reaches the exchange and never consumes rate limit.
        """
        plan.validate(filters, entry_maker=True)

        # Isolated margin caps the damage from one bad position to that
        # position. -4046 means it is already isolated, which the client
        # reports as success rather than an error.
        await self.client.set_margin_type(plan.symbol, "ISOLATED")
        await self.client.set_leverage(plan.symbol, plan.leverage)

        response = await self.client.place_order(
            symbol=plan.symbol,
            side=plan.entry_side,
            order_type="LIMIT",
            quantity=plan.quantity,
            price=plan.entry,
            # Post-only. A taker entry roughly doubles the round-trip cost,
            # which at this timeframe is most of the available edge.
            time_in_force="GTX",
            client_order_id=plan.client_order_id("entry"),
        )
        order_id = response.get("orderId")
        log.info(
            "entry placed %s %s %s at %s (order %s)",
            plan.symbol,
            plan.entry_side,
            plan.quantity,
            plan.entry,
            order_id,
        )
        return ExecutionResult(
            entry_order_id=order_id,
            client_order_id=plan.client_order_id("entry"),
            raw=response,
        )

    async def attach_bracket(self, plan: TradePlan) -> bool:
        """Place the protective stop, then the target.

        The stop goes first deliberately. If only one leg survives a partial
        failure, protection is the one worth having.
        """
        await self._place_stop(plan)

        try:
            await self.client.place_algo_order(
                symbol=plan.symbol,
                side=plan.exit_side,
                order_type="TAKE_PROFIT_MARKET",
                trigger_price=plan.target,
                client_algo_id=plan.client_order_id("tp"),
                close_position=True,
            )
        except (BinanceError, httpx.HTTPError) as exc:
            # Survivable. The position still has a stop, so the downside is
            # bounded; the target can be re-placed or taken manually.
            log.error(
                "take-profit failed for %s but the stop is attached: %s",
                plan.symbol,
                exc,
            )
            return False
        return True

    async def _place_stop(self, plan: TradePlan) -> None:
        last_error: Exception | None = None

        for attempt in range(1, STOP_ATTEMPTS + 1):
            try:
                await self.client.place_algo_order(
                    symbol=plan.symbol,
                    side=plan.exit_side,
                    order_type="STOP_MARKET",
                    trigger_price=plan.stop,
                    client_algo_id=plan.client_order_id("sl"),
                    close_position=True,
                )
                return
            except (BinanceError, httpx.HTTPError) as exc:
                last_error = exc
                log.warning(
                    "stop-loss attempt %d/%d failed for %s: %s",
                    attempt,
                    STOP_ATTEMPTS,
                    plan.symbol,
                    exc,
                )
                if attempt < STOP_ATTEMPTS:
                    await asyncio.sleep(RETRY_DELAY_S * attempt)

        await self._emergency_close(plan, last_error)

    async def _emergency_close(self, plan: TradePlan, cause: Exception | None) -> None:
        """Close a position that could not be protected.

        reduceOnly so this can only ever shrink the position, never open a new
        one in the opposite direction if our view of the size is stale.
        """
        log.error(
            "could not attach a stop to %s after %d attempts; closing at market",
            plan.symbol,
            STOP_ATTEMPTS,
        )
        try:
            await self.client.place_order(
                symbol=plan.symbol,
                side=plan.exit_side,
                order_type="MARKET",
                quantity=plan.quantity,
                reduce_only=True,
                client_order_id=plan.client_order_id("panic"),
            )
        except Exception as exc:
            # Nothing further can be done automatically. This is an operator
            # alert: a live position exists with no stop and no exit.
            raise UnprotectedPosition(
                f"{plan.symbol} has no stop and the emergency close also "
                f"failed ({exc}). Close it manually now."
            ) from exc

        raise UnprotectedPosition(
            f"{plan.symbol} could not be protected ({cause}); the position was "
            "closed at market rather than left exposed."
        )
