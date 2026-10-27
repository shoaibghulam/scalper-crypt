"""What a spot route returns, before anything is placed.

The figure shown next to the button has to carry every cost the swap will
actually pay, or it is an advertisement. Each leg crosses the spread and pays
the taker fee, and on a bridged route those compound - the second leg spends
what the first produced, not the gross input.
"""

from __future__ import annotations

from dataclasses import dataclass

from scalper.exchange.spot_routing import Leg

#: Binance spot taker fee at VIP0, no BNB discount. Quoting the better rate we
#: might get is how a quote ends up optimistic; the discount is a surprise in
#: the right direction.
TAKER_FEE = 0.001


@dataclass(frozen=True)
class Quote:
    amount_out: float
    fee_paid: float
    legs: list[Leg]


def quote_route(legs: list[Leg], prices: dict[str, float], amount_in: float) -> Quote:
    """Run `amount_in` through the route at current prices.

    A missing price raises rather than defaulting: a swap quoted off a guessed
    price is worse than one that refuses to quote.
    """
    if amount_in <= 0:
        raise ValueError("amount must be positive")

    amount = amount_in
    fee_total = 0.0
    for leg in legs:
        price = prices[leg.symbol]
        if price <= 0:
            raise ValueError(f"no usable price for {leg.symbol}")
        gross = amount / price if leg.side == "BUY" else amount * price
        fee = gross * TAKER_FEE
        fee_total += fee
        amount = gross - fee

    return Quote(amount_out=amount, fee_paid=fee_total, legs=list(legs))
