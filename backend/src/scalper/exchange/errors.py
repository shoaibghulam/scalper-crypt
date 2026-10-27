"""Exchange error classification.

Every failure needs a decided response, and the wrong one is often worse than
the failure itself. Retrying an insufficient-margin error burns rate limit
against a condition that has not changed. Retrying a reduce-only rejection can
send an order against a position that is already flat. Treating an IP ban as a
transient blip leaves open positions unmanaged for up to three days.

The mapping is therefore data rather than conditionals scattered through the
client, and the default for an unrecognised code is to skip. Defaulting to
retry is how a bot hammers the exchange into a ban.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Returned when the account is already in the requested margin mode. Nothing
# is wrong; treating it as a failure would abort an otherwise good startup.
MARGIN_TYPE_UNCHANGED = -4046

# Refused because orders already rest on the symbol. The engine sets isolated
# margin before every entry, and once any order is live on a symbol Binance
# declines to change the mode - which is the normal state when a second bot
# trades a symbol another bot already holds. The mode is already what we
# asked for; the call was redundant. A live bot died on this before placing a
# single order, unable to trade any symbol another bot had touched.
POSITION_SIDE_LOCKED = -4067
POSITION_SIDE_UNCHANGED = -4059

# Deliberately narrow. Each of these means "the state you asked for already
# holds". Anything that means the trade itself was rejected - insufficient
# margin, invalid leverage, a bad price - must stay fatal.
BENIGN_CODES = frozenset(
    {MARGIN_TYPE_UNCHANGED, POSITION_SIDE_UNCHANGED}
)


class Action(Enum):
    """What the caller should do about a failure."""

    RETRY = "retry"
    """Transient. Back off and try the same request again."""

    SKIP = "skip"
    """This trade cannot proceed. Abandon it; do not resend."""

    VERIFY = "verify"
    """Outcome unknown. Query the exchange by client order id before acting."""

    HALT = "halt"
    """Stop trading and raise an operator alert."""


@dataclass(frozen=True)
class ErrorRule:
    action: Action
    guidance: str


_RULES: dict[int, ErrorRule] = {
    -1021: ErrorRule(
        Action.RETRY,
        "timestamp outside recvWindow; resynchronise the clock and retry once",
    ),
    -1022: ErrorRule(
        Action.HALT,
        "signature rejected; the API secret or the query encoding is wrong",
    ),
    -2013: ErrorRule(
        Action.SKIP,
        "order does not exist; it was never accepted",
    ),
    -2019: ErrorRule(
        Action.SKIP,
        "margin insufficient; skip this trade rather than retrying, because "
        "the balance will not change by itself",
    ),
    -2022: ErrorRule(
        Action.VERIFY,
        "reduceOnly rejected; the position is probably already flat. Read the "
        "position before doing anything else, and never blind-retry a close",
    ),
    -2027: ErrorRule(
        Action.SKIP,
        "position exceeds the leverage bracket for this notional",
    ),
    -4028: ErrorRule(
        Action.SKIP,
        "leverage invalid for this symbol and notional tier; check leverageBracket",
    ),
    -4047: ErrorRule(
        Action.SKIP,
        "margin type cannot change while open orders exist",
    ),
    -4048: ErrorRule(
        Action.SKIP,
        "margin type cannot change while a position is open",
    ),
    -4120: ErrorRule(
        Action.HALT,
        "conditional order sent to the wrong endpoint; stop-loss and "
        "take-profit must go to /fapi/v1/algoOrder. A position without a "
        "protective order is unacceptable, so trading stops here",
    ),
    -4164: ErrorRule(
        Action.SKIP,
        "order notional below the exchange minimum; size was computed wrongly",
    ),
}

_UNKNOWN = ErrorRule(
    Action.SKIP,
    "unrecognised exchange error; skipping is the safe default",
)

_HTTP_RULES: dict[int, ErrorRule] = {
    418: ErrorRule(
        Action.HALT,
        "IP banned. Bans escalate from two minutes to three days, and open "
        "positions cannot be managed while banned. This is an operator alert",
    ),
    429: ErrorRule(
        Action.RETRY,
        "rate limited; honour Retry-After and back off before the ban escalates",
    ),
}

_HTTP_SERVER_ERROR = ErrorRule(
    Action.VERIFY,
    "server error; the request may still have been executed. Query by client "
    "order id rather than resending",
)


class BinanceError(Exception):
    """A classified exchange failure carrying its own remediation."""

    def __init__(
        self,
        code: int,
        message: str,
        rule: ErrorRule,
        *,
        http_status: int | None = None,
    ) -> None:
        self.code = code
        self.exchange_message = message
        self.action = rule.action
        self.guidance = rule.guidance
        self.http_status = http_status
        super().__init__(f"[{code}] {message} -- {rule.guidance}")

    @classmethod
    def from_http(cls, status: int, message: str) -> BinanceError:
        if status in _HTTP_RULES:
            rule = _HTTP_RULES[status]
        elif status >= 500:
            rule = _HTTP_SERVER_ERROR
        else:
            rule = _UNKNOWN
        return cls(status, message, rule, http_status=status)


def is_benign(code: int) -> bool:
    """Whether this code means the desired state already holds."""
    return code in BENIGN_CODES


def classify(code: int, message: str) -> BinanceError:
    return BinanceError(code, message, _RULES.get(code, _UNKNOWN))
