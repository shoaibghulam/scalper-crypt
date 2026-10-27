"""An intended trade, fully decided before anything is sent.

Every check happens here, where the answer can still be "do not trade", rather
than part way through placing a bracket. A plan that fails validation is
abandoned; it is never adjusted until it passes, because the constraint that
bound will still bind.

Client IDs identify intents durably. The exchange only deduplicates OPEN orders;
recovery queries the original order instead of resending an uncertain entry.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal

from scalper.core.fees import MAX_COST_TO_STOP_RATIO, MIN_STOP_FRACTION, FeeModel
from scalper.core.filters import SymbolFilters
from scalper.risk.sizing import (
    MIN_LIQUIDATION_STOP_MULTIPLE,
    is_liquidation_inside_stop,
    liquidation_headroom,
)

#: Below this the break-even win rate stops being achievable at this timeframe.
#: A symmetric risk-reward on a sub-one-percent move needs roughly 68 percent
#: net, which nobody sustains.
MIN_REWARD_TO_RISK = Decimal("1.5")

#: Binance accepts up to 36 characters for a client order id.
MAX_CLIENT_ID_LENGTH = 36

#: And only these characters: ^[.A-Z:/a-z0-9_-]{1,36}$. Anything else is
#: rejected with -1100 before the order is looked at. Bots were once named
#: bot-1 and bot-2, which passed by luck; readable names put spaces in the id
#: and every order failed.
LEGAL_CLIENT_ID = re.compile(r"[^.A-Za-z0-9:/_-]")

#: Used when a name sanitises away to nothing, which non-Latin names do. An id
#: of just "-7-entry" would be legal but carries no bot identity, so two bots
#: on the same sequence would collide on it.
ANONYMOUS_BOT = "bot"

#: Used when the caller has not read the symbol bracket. Binance charges 0.4%
#: on the smallest USDⓈ-M tier, which is the tier every position this platform
#: opens falls into. A wrong value here is conservative in the safe direction
#: only if it is too high, so the default is not lowered.
DEFAULT_MAINTENANCE_MARGIN_RATE = Decimal("0.004")


class PlanRejected(Exception):
    """This trade must not be placed. Abandon it rather than adjusting it."""


class EntryAborted(PlanRejected):
    """Post-fill validation failed and a reduce-only exit was submitted.

    Resumption requires verified flatness and completed fill settlement.
    """


@dataclass(frozen=True)
class TradePlan:
    bot_id: str
    signal_seq: int
    symbol: str
    side: str
    quantity: Decimal
    entry: Decimal
    stop: Decimal
    target: Decimal
    leverage: int
    signal_time: int = 0

    @property
    def is_long(self) -> bool:
        return self.side == "LONG"

    @property
    def entry_side(self) -> str:
        return "BUY" if self.is_long else "SELL"

    @property
    def exit_side(self) -> str:
        """Both protective legs close the position, so both trade the far side."""
        return "SELL" if self.is_long else "BUY"

    @property
    def notional(self) -> Decimal:
        return self.quantity * self.entry

    @property
    def stop_distance(self) -> Decimal:
        return abs(self.entry - self.stop)

    @property
    def stop_fraction(self) -> Decimal:
        return self.stop_distance / self.entry

    @property
    def reward_to_risk(self) -> Decimal:
        risk = self.stop_distance
        if risk == 0:
            return Decimal(0)
        return abs(self.target - self.entry) / risk

    def client_order_id(self, intent: str) -> str:
        """Deterministic, legal, and short enough for the exchange to accept.

        Truncated from the front of the bot name rather than the back, because
        the sequence and the intent are what make the id unique; losing them
        would let two different legs collide.

        Illegal characters are REPLACED rather than dropped. Dropping would map
        "Scalp A" and "ScalpA" onto the same id, and two bots colliding on a
        client order id is exactly the failure this id exists to prevent: the
        exchange would reject the second bot's order as a duplicate of the
        first, silently, as though it had already been placed.
        """
        if not intent or LEGAL_CLIENT_ID.search(intent):
            raise ValueError("invalid order intent")
        safe = LEGAL_CLIENT_ID.sub("_", self.bot_id).strip("_") or ANONYMOUS_BOT
        suffix = f"-{self.signal_seq}-{intent}"
        room = MAX_CLIENT_ID_LENGTH - len(suffix)
        if room < 13:
            raise ValueError("order sequence or intent is too long")
        if safe != self.bot_id or len(safe) > room:
            digest = hashlib.sha256(self.bot_id.encode()).hexdigest()[:12]
            safe = f"{safe[: room - 13]}-{digest}"
        return f"{safe}{suffix}"

    def validate(
        self,
        filters: SymbolFilters,
        fees: FeeModel | None = None,
        mmr: Decimal = DEFAULT_MAINTENANCE_MARGIN_RATE,
        entry_maker: bool = False,
        min_reward_to_risk: Decimal = MIN_REWARD_TO_RISK,
    ) -> None:
        """Refuse anything that cannot be traded correctly or profitably."""
        fees = fees or FeeModel.vip0(bnb_discount=False)

        if self.side not in {"LONG", "SHORT"}:
            raise PlanRejected("side must be LONG or SHORT")
        if self.symbol != filters.symbol:
            raise PlanRejected("symbol does not match exchange filters")
        if not 1 <= self.leverage <= 100:
            raise PlanRejected("leverage must be between 1 and 100")
        for name, value in (
            ("entry", self.entry),
            ("stop", self.stop),
            ("target", self.target),
            ("quantity", self.quantity),
        ):
            if not value.is_finite() or value <= 0:
                raise PlanRejected(f"{name} must be finite and positive")
        if self.quantity <= 0:
            raise PlanRejected("quantity must be positive")
        if self.stop_distance == 0:
            raise PlanRejected("stop distance is zero")

        if self.is_long:
            if self.stop >= self.entry:
                raise PlanRejected("a long stop must sit below the entry price")
            if self.target <= self.entry:
                raise PlanRejected("a long target must sit above the entry price")
        else:
            if self.stop <= self.entry:
                raise PlanRejected("a short stop must sit above the entry price")
            if self.target >= self.entry:
                raise PlanRejected("a short target must sit below the entry price")

        if self.stop_fraction < MIN_STOP_FRACTION:
            raise PlanRejected(
                f"stop distance {self.stop_fraction:.4%} is below the "
                f"{MIN_STOP_FRACTION:.2%} floor, so fees would consume it"
            )

        cost = fees.round_trip(entry_maker=entry_maker, exit_maker=False)
        ratio = cost / self.stop_fraction
        if ratio > MAX_COST_TO_STOP_RATIO:
            raise PlanRejected(f"stop distance is too tight for its fees: {ratio:.0%} of the stop")

        # Leverage against the stop, not against a fixed ceiling. The ceiling
        # is the operator decision; this is the arithmetic one. If the exchange
        # would liquidate before price reaches the stop, the protective order
        # is decorative and the position has unbounded downside.
        if is_liquidation_inside_stop(
            leverage=self.leverage, mmr=mmr, stop_fraction=self.stop_fraction
        ):
            raise PlanRejected(
                f"at {self.leverage}x, liquidation arrives before the "
                f"{self.stop_fraction:.2%} stop, so the stop cannot fire"
            )

        headroom = liquidation_headroom(
            leverage=self.leverage, mmr=mmr, stop_fraction=self.stop_fraction
        )
        if headroom < MIN_LIQUIDATION_STOP_MULTIPLE:
            raise PlanRejected(
                f"at {self.leverage}x, liquidation sits only {headroom:.2f} stop-widths "
                f"beyond the {self.stop_fraction:.2%} stop; "
                f"{MIN_LIQUIDATION_STOP_MULTIPLE} is the floor"
            )

        # The floor the STRATEGY declares, which defaults to the platform one.
        # A source that specifies a sub-1 ratio is traded at that ratio or not
        # at all; silently raising the target would change the strategy while
        # keeping its name.
        if self.reward_to_risk < min_reward_to_risk:
            raise PlanRejected(
                f"reward to risk {self.reward_to_risk:.2f} is below "
                f"the configured minimum {min_reward_to_risk}"
            )

        # Exchange filters last: a plan that fails the economics should say so
        # rather than complaining about a tick size.
        for price in (self.entry, self.stop, self.target):
            if price % filters.tick_size != 0:
                raise PlanRejected(
                    f"price {price} is not a multiple of tickSize {filters.tick_size}"
                )
        try:
            filters.validate(qty=self.quantity, price=self.entry)
        except ValueError as exc:
            raise PlanRejected(str(exc)) from exc
