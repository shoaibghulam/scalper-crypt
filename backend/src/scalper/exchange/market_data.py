"""Public market data. No credentials required.

Everything the scanner needs is served without an API key, which is why symbol
selection works before the operator has configured anything. Only order
placement needs credentials.

Weight discipline matters here. The whole-market ticker and bookTicker
endpoints return every symbol in one request, so the universe is refreshed with
two calls rather than several hundred. Per-symbol kline requests are then made
only for the shortlist that survived the cheap filters.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from types import TracebackType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from scalper.exchange.order_flow import FlowEvidence

import httpx

from scalper.core.filters import SymbolFilters
from scalper.exchange.market_limits import LIMITS, MarketLimit, request_weight

EXCHANGE_INFO = "/fapi/v1/exchangeInfo"
TICKER_24HR = "/fapi/v1/ticker/24hr"
BOOK_TICKER = "/fapi/v1/ticker/bookTicker"
KLINES = "/fapi/v1/klines"


def finite(value: Any, *, positive: bool = False) -> float:
    """Parse an exchange-supplied number, refusing NaN and infinity.

    This exists because NaN defeats every comparison. Both `nan < x` and
    `nan > x` are False, so a filter written as a comparison silently passes it
    through: a symbol with a NaN volume clears the liquidity minimum, clears
    the spread ceiling, and then sorts to an arbitrary position in the list an
    operator uses to choose trades. Infinity is the mirror image, clearing
    every minimum and failing every maximum.

    Rejecting at the boundary is the only tractable approach. Guarding at each
    of the dozen later comparisons means remembering every one of them.
    """
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expected a number, got {value!r}") from exc
    if not math.isfinite(result):
        raise ValueError(f"expected a finite number, got {result!r}")
    if positive and result <= 0:
        raise ValueError(f"expected a positive number, got {result!r}")
    return result


@dataclass(frozen=True)
class Candle:
    """One kline.

    taker_buy_base is index 9 of the raw array and is the reason this bot needs
    no order-flow feed: aggressive buy volume against the remainder gives a real
    delta series, present in historical data and therefore backtestable.
    """

    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int
    quote_volume: float
    taker_buy_base: float

    @property
    def delta(self) -> float:
        """Aggressive buy volume minus aggressive sell volume."""
        return 2.0 * self.taker_buy_base - self.volume

    @property
    def delta_pct(self) -> float:
        """Delta normalised to the bar, comparable across symbols."""
        return 0.0 if self.volume == 0 else self.delta / self.volume

    @classmethod
    def from_raw(cls, raw: Sequence[Any]) -> Candle:
        return cls(
            open_time=int(raw[0]),
            open=finite(raw[1], positive=True),
            high=finite(raw[2], positive=True),
            low=finite(raw[3], positive=True),
            close=finite(raw[4], positive=True),
            # A bar with no trades is real, so volume may be zero.
            volume=finite(raw[5]),
            close_time=int(raw[6]),
            quote_volume=finite(raw[7]),
            taker_buy_base=finite(raw[9]),
        )


@dataclass(frozen=True)
class Ticker:
    symbol: str
    last_price: float
    price_change_pct: float
    quote_volume: float


@dataclass(frozen=True)
class BookTop:
    symbol: str
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread_bps(self) -> float:
        """Spread in basis points, the unit that matters for scalping.

        For a strategy targeting fractions of a percent, anything above about
        five basis points is already disqualifying.
        """
        mid = self.mid
        return 0.0 if mid == 0 else (self.ask - self.bid) / mid * 10000.0


#: Seconds in each candle interval this client fetches.
_INTERVAL_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800,
    "12h": 43200, "1d": 86400,
}


def interval_seconds(interval: str) -> int:
    """How long one candle of this interval lasts.

    An unknown interval falls back to a minute: caching an unrecognised
    interval for longer than it lives would serve stale candles as closed
    signals, which is the one failure worth avoiding here.
    """
    return _INTERVAL_SECONDS.get(interval, 60)


#: How long a downloaded symbol list stays usable. New perpetuals are listed
#: on the order of once a day, so minutes of staleness costs nothing, while
#: re-downloading per bot costs weight 5 each and is what trips the limiter
#: on a large fleet.
FILTERS_TTL_SECONDS = 300

#: Keyed by exchange origin and shared across clients ON PURPOSE: the engine
#: builds a fresh MarketDataClient for every bot, so an instance attribute
#: would never survive to be read a second time. Testnet and live keep
#: separate entries - serving one exchange's filters to the other would size
#: orders against the wrong rules.
_FILTERS_CACHE: dict[str, tuple[float, dict[str, "SymbolFilters"]]] = {}


def clear_symbol_filters_cache() -> None:
    """Forget every cached symbol list. For tests and for a forced refresh."""
    _FILTERS_CACHE.clear()


class MarketDataClient:
    """Read-only access to public endpoints."""

    def __init__(self, base_url: str, *, http: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url
        self._http = http
        self._owns_http = http is None
        self._candle_cache: dict[tuple[str, str, int], tuple[int, list[Candle]]] = {}

    async def __aenter__(self) -> MarketDataClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(15.0, connect=5.0),
            )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if self._http is None:
            raise RuntimeError("client must be used as an async context manager")
        gate = LIMITS.setdefault(self.base_url, MarketLimit())
        await gate.acquire(f"{self.base_url}{path}", request_weight(path, params))
        response = await self._http.get(path, params=params)
        gate.observe(response)
        response.raise_for_status()
        return response.json()

    async def order_flow(self, symbol: str) -> FlowEvidence:
        from scalper.exchange.order_flow import sample_order_flow

        return await sample_order_flow(self, symbol)

    async def symbol_filters(self) -> dict[str, SymbolFilters]:
        """Tradeable USDT perpetuals and their order filters.

        Anything not perpetual, not trading, or not USDT-quoted is dropped here
        so that no later stage has to think about it.
        """
        cached = _FILTERS_CACHE.get(self.base_url)
        if cached is not None and time.time() - cached[0] < FILTERS_TTL_SECONDS:
            return cached[1]
        payload = await self._get(EXCHANGE_INFO)
        out: dict[str, SymbolFilters] = {}

        for entry in payload.get("symbols", []):
            if entry.get("contractType") != "PERPETUAL":
                continue
            if entry.get("status") != "TRADING":
                continue
            if entry.get("quoteAsset") != "USDT":
                continue

            filters = {f["filterType"]: f for f in entry.get("filters", [])}
            lot = filters.get("LOT_SIZE", {})
            market_lot = filters.get("MARKET_LOT_SIZE", {})
            price = filters.get("PRICE_FILTER", {})
            notional = filters.get("MIN_NOTIONAL", {})

            out[entry["symbol"]] = SymbolFilters(
                symbol=entry["symbol"],
                tick_size=Decimal(str(price.get("tickSize", "0.01"))),
                step_size=Decimal(str(lot.get("stepSize", "0.001"))),
                min_qty=Decimal(str(lot.get("minQty", "0.001"))),
                min_notional=Decimal(str(notional.get("notional", "5"))),
                max_qty=Decimal(str(lot["maxQty"])) if "maxQty" in lot else None,
                market_step_size=Decimal(str(market_lot.get("stepSize", "0"))),
                market_min_qty=Decimal(str(market_lot.get("minQty", "0"))),
                market_max_qty=Decimal(str(market_lot.get("maxQty", "0"))),
            )
        # An empty result means the download failed or the exchange returned
        # nothing usable; caching that would strand every bot until the TTL.
        if out:
            _FILTERS_CACHE[self.base_url] = (time.time(), out)
        return out

    async def tickers(self, symbol: str | None = None) -> dict[str, Ticker]:
        """Whole-market 24 hour statistics in a single request."""
        payload = await self._get(TICKER_24HR, {"symbol": symbol} if symbol else None)
        if symbol:
            payload = [payload]
        out: dict[str, Ticker] = {}
        for entry in payload:
            try:
                out[entry["symbol"]] = Ticker(
                    symbol=entry["symbol"],
                    last_price=finite(entry["lastPrice"], positive=True),
                    price_change_pct=finite(entry["priceChangePercent"]),
                    quote_volume=finite(entry["quoteVolume"]),
                )
            except (ValueError, KeyError):
                # One malformed symbol is dropped. Failing the whole scan
                # because a single halted listing returned nonsense would be
                # a worse outcome than trading the other five hundred.
                continue
        return out

    async def book_tops(self, symbol: str | None = None) -> dict[str, BookTop]:
        """Whole-market best bid and ask in a single request."""
        payload = await self._get(BOOK_TICKER, {"symbol": symbol} if symbol else None)
        if symbol:
            payload = [payload]
        out: dict[str, BookTop] = {}
        for entry in payload:
            try:
                bid = finite(entry.get("bidPrice", 0) or 0, positive=True)
                ask = finite(entry.get("askPrice", 0) or 0, positive=True)
            except ValueError:
                continue
            if ask < bid:
                # A crossed book is a data fault, not an opportunity.
                continue
            out[entry["symbol"]] = BookTop(symbol=entry["symbol"], bid=bid, ask=ask)
        return out

    async def prefetch_candles(self, symbols: list[str], limit_5m: int, limit_1m: int) -> None:
        """Fetch a bot's closed histories concurrently; never cache executable quotes."""
        five, one = await asyncio.gather(
            self.candles_for(symbols, "5m", limit=limit_5m),
            self.candles_for(symbols, "1m", limit=limit_1m),
        )
        for interval, limit, data in (("5m", limit_5m, five), ("1m", limit_1m, one)):
            epoch = self._candle_epoch(interval)
            for symbol, bars in data.items():
                self._candle_cache[(symbol, interval, limit)] = (epoch, bars)

    def _candle_epoch(self, interval: str) -> int:
        """Which candle of this interval we are inside.

        Closed candles cannot change until the next one closes, so a 5m
        history is identical for five minutes. Keying the cache to the minute
        regardless of interval - as this used to - re-downloaded every 5m
        history five times more often than it could possibly differ, which is
        most of the request weight a large fleet spends.
        """
        return int(time.time()) // max(interval_seconds(interval), 1)

    async def candles(self, symbol: str, interval: str, limit: int = 200) -> list[Candle]:
        """Closed candles for one symbol.

        Filter by close time rather than row position: testnet may omit the
        forming candle. A forming bar must never be used as a closed signal.
        """
        key = (symbol, interval, limit)
        epoch = self._candle_epoch(interval)
        cached = self._candle_cache.get(key)
        if cached is not None and cached[0] == epoch:
            return cached[1]
        raw = await self._get(KLINES, {"symbol": symbol, "interval": interval, "limit": limit})
        candles = [Candle.from_raw(item) for item in raw]
        # Testnet may omit the forming bar. Dropping the last row unconditionally
        # then discards a valid signal and can misalign the 1m and 5m histories.
        now_ms = int(time.time() * 1000)
        closed = [c for c in candles if c.close_time < now_ms]
        # Store what we just paid for. Without this the cache above was dead
        # code on every path but prefetch, so each bot in a fleet re-downloaded
        # the same symbol in the same cycle.
        self._candle_cache[key] = (epoch, closed)
        return closed

    async def candles_for(
        self,
        symbols: Sequence[str],
        interval: str,
        limit: int = 200,
        concurrency: int = 8,
    ) -> dict[str, list[Candle]]:
        """Candles for many symbols, with bounded concurrency.

        The bound exists to stay inside the request weight budget. Firing a
        hundred simultaneous requests is the fastest route to a 429, and from
        there to a ban that cannot be waited out while positions are open.
        """
        semaphore = asyncio.Semaphore(concurrency)

        async def fetch(symbol: str) -> tuple[str, list[Candle]]:
            async with semaphore:
                try:
                    return symbol, await self.candles(symbol, interval, limit)
                except (httpx.HTTPError, ValueError, KeyError, IndexError):
                    # One bad symbol must not abort an entire scan.
                    return symbol, []

        results = await asyncio.gather(*(fetch(symbol) for symbol in symbols))
        return {symbol: candles for symbol, candles in results if candles}
