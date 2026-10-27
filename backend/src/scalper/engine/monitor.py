"""Keeping our record of positions honest against the exchange.

Reconciling once at startup is not enough. A position closes by take-profit,
by stop, or by a hand on the exchange, and nothing tells the engine unless it
asks. Without asking, the dashboard keeps showing a position that no longer
exists, at a profit frozen at the moment it opened.

That is the worst failure this interface can produce. A stale number rendered
at full contrast looks exactly like a live one, and an operator acts on it.

The diff is a pure function so every branch can be tested without a network.
The loop that calls it lives in the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from scalper.core.fees import FeeModel
from scalper.engine.bots import ManagedPosition
from scalper.engine.reconciler import FLAT_EPSILON, ExchangePosition


@dataclass(frozen=True)
class PositionUpdate:
    """A position that has left the exchange, and what it did."""

    symbol: str
    exit_price: Decimal | None
    realised: Decimal
    outcome: str | None
    #: Kept rather than only subtracted. A history that reports gross profit
    #: makes a strategy that trades often look better than it is.
    fees: Decimal = Decimal(0)

    @classmethod
    def from_exit(cls, position: ManagedPosition, exit_price: Decimal | None) -> PositionUpdate:
        """Realised result for a closed position.

        Fees are charged on both sides. Without them an exit at the entry price
        reads as break-even, when it is in fact a small loss, and a strategy
        that scratches often would look free.
        """
        if exit_price is None:
            return cls(position.symbol, None, Decimal(0), None, Decimal(0))

        direction = Decimal(1) if position.side == "LONG" else Decimal(-1)
        gross = (exit_price - position.entry) * position.quantity * direction

        fees = FeeModel.vip0(bnb_discount=True)
        cost = (
            position.entry * position.quantity * fees.taker
            + exit_price * position.quantity * fees.taker
        )

        return cls(
            symbol=position.symbol,
            exit_price=exit_price,
            realised=gross - cost,
            outcome=classify_exit(position, exit_price),
            fees=cost,
        )


@dataclass
class MonitorResult:
    still_open: dict[str, ManagedPosition] = field(default_factory=dict)
    closed: list[PositionUpdate] = field(default_factory=list)
    marks: dict[str, float] = field(default_factory=dict)


def classify_exit(position: ManagedPosition, exit_price: Decimal | None) -> str | None:
    """Whether this ended at the target or at the stop.

    A price-match estimate for offline calculations only. Live settlement
    requires exchange evidence and does not infer an exit order from price.
    """
    if exit_price is None:
        return None
    to_target = abs(exit_price - position.target)
    to_stop = abs(exit_price - position.stop)
    tolerance = position.entry * Decimal("0.0001")
    if to_target <= tolerance:
        return "tp"
    if to_stop <= tolerance:
        return "sl"
    return None


def diff_positions(
    known: dict[str, ManagedPosition],
    live: list[ExchangePosition],
    marks: dict[str, float] | None = None,
) -> MonitorResult:
    """Compare what we think we hold against what the exchange holds.

    The exchange is the source of truth. Anything we hold that it does not is
    closed, whatever we believed a moment ago.
    """
    marks = marks or {}
    open_symbols = {p.symbol for p in live if abs(p.quantity) > FLAT_EPSILON}

    result = MonitorResult()
    for symbol, position in known.items():
        if symbol in open_symbols:
            result.still_open[symbol] = position
            if symbol in marks:
                result.marks[symbol] = marks[symbol]
        else:
            # A mark snapshot is not an execution price. Settlement retrieves fills.
            result.closed.append(PositionUpdate.from_exit(position, None))
    return result
