"""Environment selection and Binance URL routing.

Binance split the USDT-M futures market-data WebSocket into three routed entry
points: /public, /market and /private. The legacy unrouted paths are being
decommissioned, and for market-category streams they are ALREADY dead: the
socket opens, the handshake succeeds, and then no frame ever arrives. There is
no exception and no close frame to react to.

Testnet does not enforce the routing yet. That asymmetry is the trap this
module exists to close: code that works perfectly against testnet on a legacy
URL goes silent the moment it is pointed at live money. So we build routed URLs
in every environment, and we refuse to guess a route for a stream we do not
recognise.
"""

from __future__ import annotations

import os
from enum import StrEnum

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from scalper.envfiles import env_files

LIVE_REST = "https://fapi.binance.com"
LIVE_WS_HOST = "wss://fstream.binance.com"

TESTNET_REST = "https://testnet.binancefuture.com"

#: Spot is a separate product on a separate host, with SEPARATE KEYS. A
#: futures testnet key returns -2015 here, which is why the credentials below
#: are their own fields rather than a reuse of the futures pair.
LIVE_SPOT_REST = "https://api.binance.com"
TESTNET_SPOT_REST = "https://testnet.binance.vision"
TESTNET_WS_HOST = "wss://fstream.binancefuture.com"

TRADING_MODE_VAR = "TRADING_MODE"


class Environment(StrEnum):
    """Where orders go, and which market data we trust.

    PAPER is not a Binance concept. It reads LIVE market data; execution is
    disabled until a local fill simulator exists. Testnet liquidity is not
    representative: measured spreads are roughly 265x too wide on BTCUSDT and
    top-of-book depth roughly 250x too deep. Testnet can prove our API wiring
    is correct; it cannot tell us whether a strategy makes money.
    """

    TESTNET = "testnet"
    PAPER = "paper"
    LIVE = "live"

    @classmethod
    def default(cls) -> Environment:
        return cls.TESTNET

    @property
    def is_real_money(self) -> bool:
        return self is Environment.LIVE

    @property
    def uses_live_market_data(self) -> bool:
        return self in (Environment.LIVE, Environment.PAPER)


class StreamRoute(StrEnum):
    MARKET = "market"
    PUBLIC = "public"
    PRIVATE = "private"


_MARKET_PREFIXES = ("kline", "markPrice", "ticker", "aggTrade", "miniTicker", "forceOrder")
_PUBLIC_PREFIXES = ("bookTicker", "depth")


def _stream_type(stream: str) -> str:
    """Extract the type token from a stream name.

    Whole-market streams lead with an exclamation mark and carry the type
    first (``!bookTicker``, ``!markPrice@arr@1s``). Per-symbol streams put the
    symbol first (``btcusdt@kline_1m``).
    """
    parts = stream.split("@")
    if stream.startswith("!"):
        return parts[0][1:]
    if len(parts) < 2:
        raise ValueError(f"unknown stream: {stream!r}")
    return parts[1]


def route_for_stream(stream: str) -> StreamRoute:
    """Return the routed endpoint a stream must be subscribed on.

    Raises rather than guessing. A wrong route does not error at runtime; it
    produces a socket that opens and stays silent, which looks identical to a
    quiet market. Mixing categories on a single combined connection silently
    drops the wrong-category streams, so callers must open at least one
    connection per route.
    """
    kind = _stream_type(stream)

    # bookTicker must be tested before ticker: it is a different category
    # despite the shared suffix, and getting it backwards is silent.
    for prefix in _PUBLIC_PREFIXES:
        if kind.startswith(prefix):
            return StreamRoute.PUBLIC
    for prefix in _MARKET_PREFIXES:
        if kind.startswith(prefix):
            return StreamRoute.MARKET

    raise ValueError(f"unknown stream: {stream!r}; refusing to guess a route")


class ExchangeConfig(BaseSettings):
    """Binance connection settings.

    Flipping testnet to live must be exactly two things: the environment and
    the key pair. Every URL is derived, so there is no second place to forget.
    """

    model_config = SettingsConfigDict(
        env_prefix="BINANCE_", env_file=env_files(), extra="ignore"
    )

    env: Environment = Field(default_factory=Environment.default)
    api_key: str
    api_secret: str
    # Bounded at both ends so a bad .env fails at startup rather than at
    # the moment an order is placed. The exchange maximum is 60000.
    recv_window_ms: int = Field(default=5000, gt=0, le=60_000)

    #: Spot trading keys. Blank is the normal state: the platform is futures
    #: first, and spot stays read-only until these are set. They are separate
    #: fields because Binance issues separate keys for spot and futures - the
    #: futures pair authenticates as -2015 against every spot endpoint.
    spot_api_key: str = ""
    spot_api_secret: str = ""

    @property
    def rest(self) -> str:
        return LIVE_REST if self.env.uses_live_market_data else TESTNET_REST

    @property
    def spot_rest(self) -> str:
        return LIVE_SPOT_REST if self.env.uses_live_market_data else TESTNET_SPOT_REST

    @property
    def can_trade_spot(self) -> bool:
        """Whether a spot order could even be signed.

        Public spot data needs no key, so quoting works without one; only
        placing an order does.
        """
        return bool(self.spot_api_key and self.spot_api_secret)

    @property
    def _ws_host(self) -> str:
        return LIVE_WS_HOST if self.env.uses_live_market_data else TESTNET_WS_HOST

    @property
    def ws_market(self) -> str:
        """klines, aggTrade, markPrice, ticker."""
        return f"{self._ws_host}/{StreamRoute.MARKET.value}"

    @property
    def ws_public(self) -> str:
        """bookTicker, depth."""
        return f"{self._ws_host}/{StreamRoute.PUBLIC.value}"

    @property
    def ws_private(self) -> str:
        """User data stream, keyed by listenKey."""
        return f"{self._ws_host}/{StreamRoute.PRIVATE.value}"

    @property
    def db_filename(self) -> str:
        return f"scalper.{self.env.value}.sqlite"

    def assert_armed(self) -> None:
        """Refuse to run against real money without an out-of-band opt-in.

        A UI toggle must never be able to arm live trading. The operator sets
        TRADING_MODE in the environment, outside the application.
        """
        if not self.env.is_real_money:
            return
        if os.environ.get(TRADING_MODE_VAR) != Environment.LIVE.value:
            raise RuntimeError(
                f"refusing to start in live mode: set {TRADING_MODE_VAR}=live "
                "in the environment to arm real-money trading"
            )

    def assert_key_environment(self, key_env: Environment) -> None:
        """Fail closed when a key record does not match the run environment."""
        if key_env is not self.env:
            raise RuntimeError(
                f"API key environment mismatch: key is {key_env.value}, "
                f"engine is running as {self.env.value}"
            )
