"""Reading account state from the exchange for reconciliation.

Separated from the reconciler so the diff stays a pure function over two lists.
That is what lets every bucket be tested without a network, which matters
because the branch that must never regress is the one that leaves an orphan
untouched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from scalper.engine.reconciler import ExchangePosition
from scalper.exchange.client import BinanceFuturesClient
from scalper.exchange.market_data import finite

log = logging.getLogger("scalper.engine.account")

#: Anything the exchange reports as exactly zero here means "no level", not
#: "liquidation at zero". Kept as a named check so the intent survives.
NO_LIQUIDATION = 0.0


def _decimal(value: Any) -> Decimal:
    """Parse a quantity the exchange sent, refusing anything non-finite.

    NaN survives every comparison it is put through, so a poisoned balance
    would pass a sufficient-margin check and size a trade against money that
    does not exist. It becomes zero here instead.
    """
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)
    if not parsed.is_finite():
        return Decimal(0)
    return parsed


@dataclass(frozen=True)
class AccountBalance:
    """What the account holds, in the three senses that differ.

    Wallet is deposited capital. Equity is wallet plus open profit, and is the
    figure the exchange liquidates against. Available is what is left after
    margin is posted, and is the only one that answers whether another trade
    can be opened.
    """

    wallet: Decimal
    available: Decimal
    equity: Decimal
    known: bool = True

    @property
    def margin_used(self) -> Decimal:
        return self.wallet - self.available

    def describe(self) -> dict[str, str | bool]:
        # Strings on the wire. A float here would round a balance in the last
        # place on its way to a screen that people reconcile against Binance.
        return {
            "wallet": f"{self.wallet:f}",
            "available": f"{self.available:f}",
            "equity": f"{self.equity:f}",
            "margin_used": f"{self.margin_used:f}",
            "balance_known": self.known,
        }

    @classmethod
    def unknown(cls) -> AccountBalance:
        return cls(wallet=Decimal(0), available=Decimal(0), equity=Decimal(0), known=False)


async def read_balance(client: Any) -> AccountBalance:
    """Wallet, available and equity, from one signed account read.

    Never raises. This runs inside the four-second refresh loop, and a
    malformed balance payload must not take position monitoring down with it.
    """
    try:
        account = await client.get_account()
    except Exception:
        log.exception("could not read the account balance")
        return AccountBalance.unknown()

    try:
        values = [Decimal(str(account[key])) for key in
                  ("totalWalletBalance", "availableBalance")]
        values.append(Decimal(str(account.get("totalMarginBalance", values[0]))))
        if not all(value.is_finite() for value in values):
            return AccountBalance.unknown()
    except (KeyError, TypeError, InvalidOperation):
        return AccountBalance.unknown()
    wallet = values[0]
    return AccountBalance(
        wallet=wallet,
        available=_decimal(account.get("availableBalance", 0)),
        # Falls back to the wallet rather than to zero: an account with money
        # in it and no open positions has equity equal to its wallet.
        equity=_decimal(account.get("totalMarginBalance", wallet)) or wallet,
    )


async def positions_and_marks(
    client: BinanceFuturesClient,
) -> tuple[list[ExchangePosition], dict[str, float]]:
    """Open positions and their mark prices, from one request.

    The mark price is what the exchange uses for unrealised profit and for
    liquidation, so it is the only price that agrees with the venue.
    """
    payload = await client.get_positions()
    positions = parse_positions(payload)
    rows = payload.get("result", payload)
    if isinstance(rows, dict):
        rows = [rows]

    marks: dict[str, float] = {}
    for row in rows:
        try:
            marks[str(row["symbol"])] = finite(row.get("markPrice", 0), positive=True)
        except (ValueError, KeyError):
            continue
    return positions, marks


async def read_leverage(client: Any) -> dict[str, int]:
    """Leverage per open symbol, from the account endpoint.

    positionRisk does not carry it, so anything that needs the real figure has
    to ask here. Flat symbols are dropped: every symbol on the account appears
    in this array and almost all of them are flat.

    Never raises, for the same reason read_balance does not: this runs in the
    refresh loop.
    """
    try:
        account = await client.get_account()
    except Exception:
        log.exception("could not read per-symbol leverage")
        return {}

    levels: dict[str, int] = {}
    for row in account.get("positions", []) or []:
        try:
            if Decimal(str(row.get("positionAmt", 0))) == 0:
                continue
            levels[str(row["symbol"])] = int(row["leverage"])
        except (InvalidOperation, KeyError, TypeError, ValueError):
            # One unreadable row must not cost the others.
            continue
    return levels


async def open_positions(client: BinanceFuturesClient) -> list[ExchangePosition]:
    """Every position the exchange currently holds.

    positionRisk returns a row for every symbol on the account, almost all of
    them flat, so the zero rows are dropped here rather than being carried
    through the diff as noise.
    """
    payload = await client.get_positions()
    return parse_positions(payload)


def parse_positions(payload: dict[str, Any]) -> list[ExchangePosition]:
    """Parse one complete snapshot; malformed exposure must fail closed."""
    rows = payload.get("result", payload)
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        raise ValueError("invalid position snapshot")

    positions: list[ExchangePosition] = []
    for row in rows:
        if not isinstance(row, dict) or "symbol" not in row or "positionAmt" not in row:
            raise ValueError("incomplete position row; exposure cannot be established")
        amount = finite(row["positionAmt"])
        if amount == 0:
            continue
        if row.get("positionSide", "BOTH") != "BOTH":
            raise ValueError("hedge-mode positions require a separate position-side implementation")
        try:
            entry = finite(row.get("entryPrice", 0), positive=True)
        except ValueError:
            # A non-zero size with no entry price is a data fault. Treated as a
            # position regardless, because the size is what carries the risk.
            entry = 0.0
        try:
            liquidation = finite(row.get("liquidationPrice", 0))
        except (ValueError, AttributeError):
            liquidation = NO_LIQUIDATION
        positions.append(
            ExchangePosition(
                symbol=str(row["symbol"]),
                quantity=amount,
                entry_price=entry,
                liquidation_price=(None if liquidation == NO_LIQUIDATION else liquidation),
                mark_price=(
                    finite(row["markPrice"], positive=True) if row.get("markPrice") else None
                ),
            )
        )
    return positions
