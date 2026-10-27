"""Fee model and the break-even arithmetic that governs every trade.

At a one-to-five minute holding period the cost of trading is not a rounding
error on the edge; it is comparable to the edge. Binance USDT-M VIP 0 charges
0.0200% maker and 0.0500% taker, reduced by 10% if BNB is held in the futures
wallet. An all-taker round trip is therefore 0.090% of notional. Against a
0.30% target that is 30% of gross profit, or roughly 37% once slippage is
included.

The governing relationship is:

    p_breakeven = (1 + C/S) / (1 + R)

for stop distance S, reward multiple R and round-trip cost C. With C fixed by
the fee schedule, the only lever is S. That is why this module, not the
strategy, owns the minimum stop distance: a stop too tight to clear its own fee
bill is unprofitable regardless of how good the signal is.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# Published Binance USDT-M perpetual rates, VIP 0.
# Taker has been 0.0500% since October 2022. A great deal of scalping material
# still quotes 0.0400%; modelling that understates the bill by 20%.
VIP0_MAKER = Decimal("0.0002")
VIP0_TAKER = Decimal("0.0005")

BNB_DISCOUNT = Decimal("0.10")

# Below this, the fee bill is too large a fraction of the stop for any
# realistic win rate to overcome. Derived from C/S <= 0.25 at C ~ 0.11%.
MIN_STOP_FRACTION = Decimal("0.0045")
MAX_COST_TO_STOP_RATIO = Decimal("0.25")


@dataclass(frozen=True)
class FeeModel:
    """Per-side fee rates as fractions of notional."""

    maker: Decimal
    taker: Decimal

    @classmethod
    def vip0(cls, bnb_discount: bool = True) -> FeeModel:
        factor = (1 - BNB_DISCOUNT) if bnb_discount else Decimal(1)
        return cls(maker=VIP0_MAKER * factor, taker=VIP0_TAKER * factor)

    def side(self, is_maker: bool) -> Decimal:
        return self.maker if is_maker else self.taker

    def round_trip(self, *, entry_maker: bool, exit_maker: bool) -> Decimal:
        """Cost of one complete trade as a fraction of notional."""
        return self.side(entry_maker) + self.side(exit_maker)

    def expected_round_trip(self, *, win_rate: Decimal) -> Decimal:
        """Blended cost for the execution pattern this system actually uses.

        Entries are post-only, so they are always maker. Take-profits are
        post-only limits, so wins exit as maker. Stops are STOP_MARKET, so
        losses exit as taker. The mix therefore moves with the win rate, which
        is precisely why a single blended rate must not be used in backtests.
        """
        return self.maker + win_rate * self.maker + (1 - win_rate) * self.taker


def breakeven_win_rate(
    stop_fraction: Decimal, reward_multiple: Decimal, cost: Decimal
) -> Decimal:
    """Win rate required to break even, net of costs.

    With cost zero this reduces to the familiar 1/(1+R). The cost term adds a
    penalty proportional to C/S, so the same strategy needs a materially higher
    hit rate on a tighter stop even though R is unchanged.
    """
    if stop_fraction <= 0:
        raise ValueError("stop distance must be positive")
    if reward_multiple <= 0:
        raise ValueError("reward multiple must be positive")
    return (1 + cost / stop_fraction) / (1 + reward_multiple)


def cost_to_stop_ratio(cost: Decimal, stop_fraction: Decimal) -> Decimal:
    if stop_fraction <= 0:
        raise ValueError("stop distance must be positive")
    return cost / stop_fraction


def validate_stop_distance(stop_fraction: Decimal, *, cost: Decimal) -> None:
    """Reject a trade whose stop cannot clear its own fee bill.

    Two independent checks. The absolute floor catches the common case of a
    stop sized from one-minute ATR, which on a liquid symbol is often smaller
    than the round-trip cost itself. The ratio check catches the rarer case of
    a wide stop on a symbol whose spread has blown out.
    """
    if stop_fraction < MIN_STOP_FRACTION:
        raise ValueError(
            f"stop distance {stop_fraction:.4%} is below the {MIN_STOP_FRACTION:.2%} "
            "floor; size stops from 5m or 15m ATR, not 1m"
        )
    ratio = cost_to_stop_ratio(cost, stop_fraction)
    if ratio > MAX_COST_TO_STOP_RATIO:
        raise ValueError(
            f"cost-to-stop ratio {ratio:.3f} exceeds {MAX_COST_TO_STOP_RATIO}; "
            "fees would consume too much of the stop distance"
        )


def breakeven_stop_price(
    entry: Decimal, is_long: bool, round_trip_fee: Decimal
) -> Decimal:
    """Stop price at which a scratched trade actually breaks even.

    Moving the stop to the entry price is the usual habit and it is wrong: the
    round trip has already been paid, so entry is a guaranteed small loss. We
    move to entry plus twice the round-trip fee, in the favourable direction.
    """
    offset = 2 * round_trip_fee
    return entry * (1 + offset) if is_long else entry * (1 - offset)
