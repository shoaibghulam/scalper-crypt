"""Position sizing, and the leverage ceiling that keeps a stop able to fire.

Size is derived from the stop, never from a notional the operator picks. Given
a risk budget in quote currency and a stop distance in price, the quantity
follows arithmetically. Leverage does not appear in that calculation at all: it
only determines how much collateral is posted.

Where leverage does matter is liquidation. The exchange closes a position when
the maintenance margin is breached, and if that happens before price reaches
the stop, the stop is decorative.

Two thresholds, not one. The recommendation keeps liquidation four stop-widths
away, which is the level at which a wick has room to be wrong. The hard floor
is a quarter of a stop-width of reserve, below which the trade is refused
outright because the stop can no longer be relied on to fire first. Between
them is the operator judgement: at 100x on the tightest permitted stop,
liquidation sits 0.60 percent out and the stop at 0.45 percent, so a single
candle through both takes the maintenance margin as well as the loss. That is
a decision to make knowingly, which is why the figure is reported rather than
quietly clamped away.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from scalper.core.filters import (
    SymbolFilters,
    effective_min_notional,
    format_quantity,
    round_qty_down,
)

#: The highest leverage the operator may select at all.
LEVERAGE_HARD_CAP = 100

#: Liquidation this many stop-widths away is comfortable. Drives the figure
#: the interface recommends, never the figure it enforces.
SAFE_LIQUIDATION_STOP_MULTIPLE = Decimal("4")

#: Below this the stop cannot be trusted to fire before the exchange closes
#: the position, so the trade is refused. Kept deliberately close to one: it
#: is a floor, not a comfort level.
MIN_LIQUIDATION_STOP_MULTIPLE = Decimal("1.25")

# Never commit the whole balance. A funding payment or an adverse tick between
# sizing and fill otherwise turns a valid order into a -2019 rejection.
MARGIN_UTILISATION_LIMIT = Decimal("0.8")


class SizingRejected(Exception):
    """This trade cannot be sized safely and must be skipped, not retried."""


@dataclass(frozen=True)
class PositionSize:
    qty: Decimal
    notional: Decimal
    margin: Decimal
    risk_amount: Decimal
    stop_distance: Decimal
    stop_fraction: Decimal


def liquidation_distance_fraction(leverage: int, mmr: Decimal) -> Decimal:
    """Approximate distance from entry to liquidation, as a fraction of price.

    For isolated margin this is one over leverage, minus the maintenance margin
    rate. Cross margin is more forgiving because the whole wallet backs the
    position, so treating every position as isolated is the conservative read.
    """
    if leverage < 1:
        raise ValueError("leverage must be at least 1")
    return Decimal(1) / Decimal(leverage) - mmr


def is_liquidation_inside_stop(
    *, leverage: int, mmr: Decimal, stop_fraction: Decimal
) -> bool:
    """True when the exchange would close the position before the stop fires.

    This is the condition the dashboard renders as a hard warning. It means the
    stop-loss protects nothing: the position is liquidated first, at a worse
    price and with the maintenance margin forfeited.
    """
    return liquidation_distance_fraction(leverage, mmr) < stop_fraction


def liquidation_headroom(
    *, leverage: int, mmr: Decimal, stop_fraction: Decimal
) -> Decimal:
    """How many stop-widths of reserve sit between the stop and liquidation.

    One means the two coincide and the stop protects nothing. Anything at or
    below zero means liquidation comes first, and is reported as zero rather
    than as a negative that would compare as small-but-safe against a
    threshold.
    """
    if stop_fraction <= 0:
        raise ValueError("stop distance must be positive")
    distance = liquidation_distance_fraction(leverage, mmr)
    if distance <= 0:
        return Decimal(0)
    return distance / stop_fraction


def _leverage_for_multiple(
    stop_fraction: Decimal, mmr: Decimal, multiple: Decimal
) -> int:
    """Solve the headroom requirement for leverage.

    L <= 1/(k*S + mmr) for multiple k and stop fraction S.
    """
    if stop_fraction <= 0:
        raise ValueError("stop distance must be positive")
    limit = Decimal(1) / (multiple * stop_fraction + mmr)
    floored = int(limit.to_integral_value(rounding=ROUND_FLOOR))
    return max(1, min(floored, LEVERAGE_HARD_CAP))


def recommended_max_leverage(stop_fraction: Decimal, mmr: Decimal) -> int:
    """The leverage this platform advises, not the one it enforces.

    On a 0.45% stop this is roughly 43x. Fat tails, exchange wicks and
    mark-versus-last divergence are why it sits well under the hard cap.
    """
    return _leverage_for_multiple(stop_fraction, mmr, SAFE_LIQUIDATION_STOP_MULTIPLE)


def max_allowed_leverage(stop_fraction: Decimal, mmr: Decimal) -> int:
    """The highest leverage that still lets the stop fire first.

    Past this the exchange reaches liquidation before the stop, so the order is
    refused however the operator has set the dial.
    """
    return _leverage_for_multiple(stop_fraction, mmr, MIN_LIQUIDATION_STOP_MULTIPLE)


def size_position(
    *,
    equity: Decimal,
    risk_pct: Decimal,
    entry: Decimal,
    stop: Decimal,
    leverage: int,
    filters: SymbolFilters,
    available_balance: Decimal,
) -> PositionSize:
    """Compute the quantity that risks exactly risk_pct of equity.

    Rejections are final for this signal. A rejected size means the trade is
    skipped; it never means retry with a smaller number, because the constraint
    that bound will still bind.
    """
    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        raise SizingRejected("stop distance is zero; entry and stop cannot be equal")

    risk_amount = equity * risk_pct
    qty = round_qty_down(risk_amount / stop_distance, filters.step_size)
    notional = qty * entry

    required = effective_min_notional(filters, entry)
    if qty < filters.min_qty or notional < required:
        raise SizingRejected(
            f"{filters.symbol}: resulting notional {format_quantity(notional)} is below "
            f"the exchange minimum {format_quantity(required)}; equity is too small for "
            "this stop distance"
        )

    margin = notional / Decimal(leverage)
    ceiling = available_balance * MARGIN_UTILISATION_LIMIT
    if margin > ceiling:
        raise SizingRejected(
            f"{filters.symbol}: required margin {format_quantity(margin)} exceeds the "
            f"usable balance {format_quantity(ceiling)}"
        )

    return PositionSize(
        qty=qty,
        notional=notional,
        margin=margin,
        risk_amount=risk_amount,
        stop_distance=stop_distance,
        stop_fraction=stop_distance / entry,
    )
