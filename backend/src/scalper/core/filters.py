"""Symbol filter rounding, the boundary where good maths meets a rejected order.

Every Binance symbol publishes a tickSize, a stepSize, a minQty and a minimum
notional. An order violating any of them is refused outright, and the refusal
arrives at the worst possible moment: when a signal has just fired.

One non-obvious rule is encoded here. The advertised MIN_NOTIONAL is not always
the binding constraint. On a high-priced symbol, minQty multiplied by price can
exceed it, so the effective minimum is the larger of the two.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from enum import Enum


class Rounding(Enum):
    """Direction to move a price when it does not sit on a tick.

    Buy limits round DOWN and sell limits round UP, which keeps a post-only
    order behind the book rather than crossing it and losing maker pricing.
    """

    DOWN = "down"
    UP = "up"


def round_qty_down(qty: Decimal, step_size: Decimal) -> Decimal:
    """Round a quantity down to a multiple of stepSize.

    Always down, never nearest. Rounding up can push the notional past the
    margin we checked against, turning a valid size into a -2019 rejection.
    """
    if step_size <= 0:
        raise ValueError("stepSize must be positive")
    steps = (qty / step_size).to_integral_value(rounding=ROUND_FLOOR)
    return steps * step_size


def round_to_tick(price: Decimal, tick_size: Decimal, mode: Rounding) -> Decimal:
    if tick_size <= 0:
        raise ValueError("tickSize must be positive")
    scaled = price / tick_size
    floored = scaled.to_integral_value(rounding=ROUND_FLOOR)
    if mode is Rounding.DOWN or floored == scaled:
        return floored * tick_size
    return (floored + 1) * tick_size


def format_quantity(value: Decimal) -> str:
    """Serialise a Decimal without scientific notation.

    Small sizes are where this bites: the default string form of a very small
    Decimal uses an exponent, which Binance rejects. The fixed-point
    presentation format is the only safe way to put a Decimal on the wire.
    """
    return format(value, "f")


@dataclass(frozen=True)
class SymbolFilters:
    """The subset of exchangeInfo that governs whether an order is accepted."""

    symbol: str
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal
    max_qty: Decimal | None = None
    market_step_size: Decimal | None = None
    market_min_qty: Decimal | None = None
    market_max_qty: Decimal | None = None

    def validate_market(self, qty: Decimal) -> None:
        for step in (self.step_size, self.market_step_size):
            if step is not None and step > 0 and qty % step != 0:
                raise ValueError("market quantity does not match exchange step size")
        minimum = max(self.min_qty, self.market_min_qty or Decimal(0))
        caps = [v for v in (self.max_qty, self.market_max_qty) if v is not None and v > 0]
        if qty < minimum or (caps and qty > min(caps)):
            raise ValueError("market quantity exceeds exchange quantity limits")

    def validate(self, *, qty: Decimal, price: Decimal) -> None:
        """Raise if this order would be rejected by the exchange.

        Called before every send, so a filter bug surfaces in our logs with a
        useful message rather than as an opaque error code from Binance.
        """
        if self.max_qty is not None and qty > self.max_qty:
            raise ValueError("quantity exceeds exchange maxQty")
        if qty < self.min_qty:
            raise ValueError(
                f"{self.symbol}: quantity {format_quantity(qty)} is below "
                f"minQty {format_quantity(self.min_qty)}"
            )
        if qty % self.step_size != 0:
            raise ValueError(
                f"{self.symbol}: quantity {format_quantity(qty)} is not a multiple "
                f"of stepSize {format_quantity(self.step_size)}"
            )
        if price % self.tick_size != 0:
            raise ValueError(
                f"{self.symbol}: price {format_quantity(price)} is not a multiple "
                f"of tickSize {format_quantity(self.tick_size)}"
            )
        required = effective_min_notional(self, price)
        if qty * price < required:
            raise ValueError(
                f"{self.symbol}: notional {format_quantity(qty * price)} is below "
                f"the effective minimum {format_quantity(required)}"
            )


def effective_min_notional(filters: SymbolFilters, price: Decimal) -> Decimal:
    """The real minimum order value for this symbol at this price."""
    return max(filters.min_notional, filters.min_qty * price)
