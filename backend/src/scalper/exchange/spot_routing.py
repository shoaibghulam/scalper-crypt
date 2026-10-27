"""Turning "swap A for B" into Binance spot orders.

Spot has no swap primitive. There are only markets, each one BASE/QUOTE, and
on each you either BUY the base (spending quote) or SELL it (receiving quote).
A swap is therefore a route: one order when a market joins the two assets in
either direction, two through a bridge when none does.

Kept pure and free of any network call, because the direction of each leg is
the part that spends the wrong asset when it is wrong.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Tried in order when no direct market exists. USDT first because it has by
#: far the deepest books and the most listings; the others catch the assets
#: that are only quoted against a major.
BRIDGES = ("USDT", "FDUSD", "USDC", "BTC", "BNB")


class NoRoute(Exception):
    """No sequence of spot markets connects these two assets."""


@dataclass(frozen=True)
class Market:
    """One spot market, as the exchange lists it."""

    symbol: str
    base: str
    quote: str


@dataclass(frozen=True)
class Leg:
    """One order in a route.

    `side` is what Binance is told. BUY spends `spend` to acquire `receive`;
    SELL does the reverse. Both assets are named so a caller never has to
    re-derive them from the side and the market.
    """

    symbol: str
    side: str
    spend: str
    receive: str


def _direct(markets: list[Market], spend: str, receive: str) -> Leg | None:
    for market in markets:
        if market.base == receive and market.quote == spend:
            return Leg(market.symbol, "BUY", spend, receive)
        if market.base == spend and market.quote == receive:
            return Leg(market.symbol, "SELL", spend, receive)
    return None


def plan_route(markets: list[Market], spend: str, receive: str) -> list[Leg]:
    """The orders that turn `spend` into `receive`.

    One leg when a market joins them, two through a bridge otherwise. Never
    more: a third hop pays a third spread and a third fee, which costs more
    than the route is worth on anything a retail account would swap.
    """
    spend, receive = spend.upper(), receive.upper()
    if spend == receive:
        raise NoRoute(f"{spend} and {receive} are the same asset")

    direct = _direct(markets, spend, receive)
    if direct is not None:
        return [direct]

    for bridge in BRIDGES:
        if bridge in (spend, receive):
            continue
        first = _direct(markets, spend, bridge)
        second = _direct(markets, bridge, receive)
        if first is not None and second is not None:
            return [first, second]

    raise NoRoute(f"no spot route from {spend} to {receive}")
