"""Whether the exchange credentials are present, and whether they work.

These are different questions and the interface must not conflate them. No key
at all means the operator has something to do. A key that exists and is
rejected means something quite different, and telling them to "add your keys"
when the keys are already there sends them to re-enter a value that was never
the problem.

Nothing in this module returns a key or a secret. The preview exists so an
operator can confirm which key is loaded without the value being exposed in an
API response, a log line, or a screenshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ACCOUNT_PATH = "/fapi/v2/account"

#: Below this, head-and-tail slicing would reveal most of the value.
MIN_MASKABLE_LENGTH = 16


def mask(key: str) -> str:
    """A preview that identifies a key without disclosing it."""
    if len(key) < MIN_MASKABLE_LENGTH:
        return "..."
    return f"{key[:6]}...{key[-4:]}"


#: Exchange answers that are about traffic, not about the key. -1003 is "too
#: many requests" and carries an IP ban window; -1015 is the order-rate
#: equivalent. Both clear on their own, so both must be retried.
TRANSIENT_CODES = frozenset({-1003, -1015})
TRANSIENT_STATUSES = frozenset({418, 429})


@dataclass(frozen=True)
class CredentialStatus:
    configured: bool
    valid: bool
    message: str
    environment: str = "unknown"
    key_preview: str = ""
    balance: str | None = None
    #: Whether another attempt could plausibly succeed.
    #:
    #: True only for an unreachable exchange, which is a temporary condition.
    #: A rejected or missing key is NOT retryable: repeating a key the exchange
    #: has refused achieves nothing and is how an API key gets rate-limited or
    #: banned. It needs a human, not another attempt.
    retryable: bool = False
    can_trade: bool | None = None

    @classmethod
    def missing(cls) -> CredentialStatus:
        return cls(
            configured=False,
            valid=False,
            message=(
                "Exchange credentials are not configured. Add BINANCE_API_KEY "
                "and BINANCE_API_SECRET to the .env file at the project root."
            ),
        )

    @classmethod
    def rate_limited(cls, preview: str, environment: str, reason: str) -> CredentialStatus:
        """The address is being throttled, which says nothing about the key.

        Kept apart from `rejected` because the two need opposite handling. A
        rejected key must not be retried; a rate limit MUST be, or the engine
        sits in safe mode with working credentials until somebody restarts it.
        Calling this one "rejected" also sent operators off to re-issue keys
        that were never the problem.
        """
        return cls(
            configured=True,
            valid=False,
            message=f"The exchange is rate limiting this address: {reason}",
            environment=environment,
            key_preview=preview,
            retryable=True,
        )

    @classmethod
    def rejected(cls, preview: str, environment: str, reason: str) -> CredentialStatus:
        return cls(
            configured=True,
            valid=False,
            message=f"The exchange rejected these credentials: {reason}",
            environment=environment,
            key_preview=preview,
        )

    @classmethod
    def unreachable(cls, preview: str, environment: str, reason: str) -> CredentialStatus:
        return cls(
            configured=True,
            valid=False,
            message=f"Could not reach the exchange to verify credentials: {reason}",
            environment=environment,
            key_preview=preview,
            retryable=True,
        )

    @classmethod
    def accepted(
        cls, preview: str, environment: str, *, balance: str, can_trade: bool
    ) -> CredentialStatus:
        return cls(
            configured=True,
            valid=True,
            message="Credentials accepted by the exchange.",
            environment=environment,
            key_preview=preview,
            balance=balance,
            can_trade=can_trade,
        )

    def describe(self) -> dict[str, Any]:
        """Safe to send to a browser. Contains no key material by construction."""
        return {
            "configured": self.configured,
            "valid": self.valid,
            "message": self.message,
            "environment": self.environment,
            "key_preview": self.key_preview,
            "balance": self.balance,
            "retryable": self.retryable,
            "can_trade": self.can_trade,
        }


async def verify(config: Any) -> CredentialStatus:
    """Ask the exchange whether these credentials are usable.

    Called once at engine startup. A signed account read is the cheapest
    request that exercises the whole chain: the key, the secret, the signature,
    the clock, and the account permissions. Discovering at signal time that the
    key was never valid is a far worse moment to find out.
    """
    # Imported here to keep this module importable without the http stack,
    # which is what lets the tests above run as pure unit tests.
    from scalper.exchange.client import BinanceFuturesClient
    from scalper.exchange.errors import BinanceError

    if not config.api_key or not config.api_secret:
        return CredentialStatus.missing()

    preview = mask(config.api_key)
    environment = config.env.value

    async with BinanceFuturesClient(config) as client:
        try:
            account = await client._signed("GET", ACCOUNT_PATH, {})
        except BinanceError as exc:
            if exc.code in TRANSIENT_CODES or exc.http_status in TRANSIENT_STATUSES:
                return CredentialStatus.rate_limited(
                    preview, environment, f"[{exc.code}] {exc.exchange_message}"
                )
            return CredentialStatus.rejected(
                preview, environment, f"[{exc.code}] {exc.exchange_message}"
            )
        except Exception as exc:
            return CredentialStatus.unreachable(
                preview, environment, f"{type(exc).__name__}: {exc}"
            )

    return CredentialStatus.accepted(
        preview,
        environment,
        balance=str(account.get("totalWalletBalance", "0")),
        can_trade=bool(account.get("canTrade", False)),
    )
