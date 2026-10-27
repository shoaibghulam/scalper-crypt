"""HMAC signing for Binance REST requests.

Two rules govern everything here.

The signature covers the exact query string that is sent, in the order it is
sent. Binance does not sort parameters, so any rebuilding between signing and
sending produces a -1022 that reads like a credentials failure and is not.

Every numeric value originates as a Decimal. Binary floats lose precision on
exactly the quantities that matter, and their string form can carry an
exponent, which Binance rejects outright.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from urllib.parse import quote

MAX_RECV_WINDOW_MS = 60_000


def to_param(value: Any) -> str:
    """Render a single parameter value the way Binance expects it."""
    if isinstance(value, bool):
        # Checked before int, because bool is a subclass of int.
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        raise TypeError(
            "float is not accepted on the wire; use Decimal so precision and "
            "formatting are both exact"
        )
    return str(value)


def build_query(params: Mapping[str, Any]) -> str:
    """Build a query string, preserving insertion order and dropping None.

    Order is preserved deliberately. The signature is computed over this
    string, so re-encoding it elsewhere would invalidate it.
    """
    return "&".join(
        f"{key}={quote(to_param(value), safe='')}"
        for key, value in params.items()
        if value is not None
    )


def sign(query: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def signed_params(
    params: Mapping[str, Any],
    *,
    secret: str,
    timestamp_ms: int,
    recv_window_ms: int,
) -> str:
    """Produce the complete signed query string for a request.

    recvWindow is how long the request stays valid. It is capped here because
    Binance refuses larger values, and because widening it is the wrong
    response to clock drift: it enlarges the replay window instead of fixing
    the clock.
    """
    if recv_window_ms > MAX_RECV_WINDOW_MS:
        raise ValueError(
            f"recvWindow {recv_window_ms} exceeds the exchange maximum of "
            f"{MAX_RECV_WINDOW_MS}; resynchronise the clock rather than "
            "widening the window"
        )

    full: dict[str, Any] = dict(params)
    full["recvWindow"] = recv_window_ms
    full["timestamp"] = timestamp_ms

    query = build_query(full)
    return f"{query}&signature={sign(query, secret)}"
