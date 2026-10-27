"""Compare our record of positions against the exchange, at startup.

The exchange is the source of truth. What we hold is a cache and an audit log,
and treating it the other way round is the common root of catastrophic loss:
the engine trades on a picture of the account that stopped being true while it
was down.

Four outcomes, each needing a different response:

ADOPT    both agree. Rebind it to its bot and carry on.
CLOSED   we have it, the exchange does not. It closed while we were down.
RESIZED  both have it at different sizes. A fill landed during the gap.
ORPHAN   the exchange has it and we do not.

The orphan is the dangerous one, and the instinct to clean it up is wrong in
both available directions. Cancelling its orders removes the stop protecting a
real position. Flattening it closes a trade the operator may have opened by
hand. Both are documented production failures. So an orphan is parked, its
symbol is blocked, and a human decides.

This module is pure. It takes two lists and returns a decision, which is what
lets every branch be tested without an exchange.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

#: Quantities below this are treated as flat. positionRisk lists every symbol
#: on the account, almost all of them with nothing open.
FLAT_EPSILON = 1e-12


class Bucket(StrEnum):
    ADOPT = "adopt"
    CLOSED = "closed"
    RESIZED = "resized"
    ORPHAN = "orphan"


@dataclass(frozen=True)
class ExchangePosition:
    symbol: str
    quantity: float
    entry_price: float
    #: As reported by the venue. None when the exchange does not supply one,
    #: which is not the same as zero and must not render as a price.
    liquidation_price: float | None = None
    mark_price: float | None = None

    @property
    def is_open(self) -> bool:
        return abs(self.quantity) > FLAT_EPSILON


@dataclass(frozen=True)
class KnownPosition:
    symbol: str
    quantity: float
    bot_id: str


@dataclass(frozen=True)
class Reconciliation:
    adopted: list[KnownPosition] = field(default_factory=list)
    closed: list[KnownPosition] = field(default_factory=list)
    resized: list[ExchangePosition] = field(default_factory=list)
    orphans: list[ExchangePosition] = field(default_factory=list)

    #: Deliberately always empty. They exist so the shape of this result makes
    #: clear that nothing is cancelled or closed on our initiative, and so a
    #: test can assert that.
    cancel_requests: list[str] = field(default_factory=list)
    close_requests: list[str] = field(default_factory=list)

    @property
    def blocked_symbols(self) -> set[str]:
        """Symbols no bot may touch until a human has decided."""
        return {position.symbol for position in self.orphans}

    @property
    def is_clean(self) -> bool:
        """Whether trading may resume.

        Only an orphan blocks. A position that closed while we were down, or
        one whose size moved, is understood; a position we cannot explain is
        not, and trading around it is how the same symbol gets opened twice.
        """
        return not self.orphans

    def bucket_of(self, symbol: str) -> Bucket | None:
        for bucket, group in (
            (Bucket.ADOPT, self.adopted),
            (Bucket.CLOSED, self.closed),
            (Bucket.RESIZED, self.resized),
            (Bucket.ORPHAN, self.orphans),
        ):
            if any(item.symbol == symbol for item in group):
                return bucket
        return None

    def external_positions(self) -> list[dict[str, Any]]:
        """Unmanaged positions, for display only.

        An operator may run a second bot against the same account. Those
        positions were already found and deliberately left alone, but they
        were only ever reported as a list of symbols in a blocking alert - so
        the one screen that shows every position was the one place they could
        not be seen.

        Read-only by construction: no bot id is attached, because this engine
        has no record of why the position was opened and no business closing
        it.
        """
        rows: list[dict[str, Any]] = []
        for position in self.orphans:
            mark = position.mark_price
            rows.append(
                {
                    "symbol": position.symbol,
                    "side": "LONG" if position.quantity >= 0 else "SHORT",
                    # Unsigned: the side already carries the direction, and a
                    # "-50" beside "SHORT" reads as a double negative.
                    "quantity": abs(position.quantity),
                    "entry_price": position.entry_price,
                    "mark_price": mark,
                    # None, not zero, when the venue gave no mark: zero would
                    # render as flat when the truth is that we do not know.
                    "unrealised": (
                        (mark - position.entry_price) * position.quantity
                        if mark is not None
                        else None
                    ),
                    "liquidation_price": position.liquidation_price,
                }
            )
        return sorted(rows, key=lambda row: row["symbol"])

    def describe(self) -> dict[str, Any]:
        """The report an operator reads first after an unexpected restart."""
        return {
            "clean": self.is_clean,
            "adopted": len(self.adopted),
            "closed": len(self.closed),
            "resized": len(self.resized),
            "orphaned": len(self.orphans),
            "blocked_symbols": sorted(self.blocked_symbols),
            "orphan_detail": [
                {
                    "symbol": position.symbol,
                    "quantity": position.quantity,
                    "entry_price": position.entry_price,
                    "action": (
                        "left untouched; ensure a protective stop exists and "
                        "decide whether to adopt or close it"
                    ),
                }
                for position in self.orphans
            ],
        }


def reconcile(
    exchange_positions: list[ExchangePosition],
    known_positions: list[KnownPosition],
) -> Reconciliation:
    """Diff the exchange against our record."""
    live = {p.symbol: p for p in exchange_positions if p.is_open}
    known = {p.symbol: p for p in known_positions}

    adopted: list[KnownPosition] = []
    closed: list[KnownPosition] = []
    resized: list[ExchangePosition] = []
    orphans: list[ExchangePosition] = []

    for symbol, record in known.items():
        actual = live.get(symbol)
        if actual is None:
            # It closed while we were down, by take-profit, stop or
            # liquidation. The realised result is recovered from the trade
            # history, keyed on the exchange trade id so replaying cannot
            # book the same profit twice.
            closed.append(record)
        elif abs(actual.quantity - record.quantity) > FLAT_EPSILON:
            resized.append(actual)
        else:
            adopted.append(record)

    for symbol, actual in live.items():
        if symbol not in known:
            orphans.append(actual)

    return Reconciliation(
        adopted=sorted(adopted, key=lambda p: p.symbol),
        closed=sorted(closed, key=lambda p: p.symbol),
        resized=sorted(resized, key=lambda p: p.symbol),
        orphans=sorted(orphans, key=lambda p: p.symbol),
    )
