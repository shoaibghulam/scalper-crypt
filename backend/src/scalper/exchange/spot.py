"""Binance spot, read-only by default.

A separate product from the futures engine this platform is built around:
different host, different endpoints, and its own key pair. Public data needs
no credentials, so pairs and prices are always available; placing an order
requires BINANCE_SPOT_API_KEY / BINANCE_SPOT_API_SECRET and fails loudly
rather than silently when they are absent.

This module never decides to trade. It fetches, and it places exactly the
order it is told to place.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from scalper.config import ExchangeConfig
from scalper.exchange.spot_routing import Market

EXCHANGE_INFO = "/api/v3/exchangeInfo"
TICKER_PRICE = "/api/v3/ticker/price"
ORDER = "/api/v3/order"
ACCOUNT = "/api/v3/account"


class SpotCredentialsMissing(RuntimeError):
    """A spot order was attempted without spot keys configured."""


class SpotClient:
    def __init__(
        self,
        config: ExchangeConfig | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config or ExchangeConfig()
        self._http = http
        self._owns_http = http is None

    async def __aenter__(self) -> SpotClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self.config.spot_rest,
                timeout=httpx.Timeout(15.0, connect=5.0),
            )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    # -- public -----------------------------------------------------------

    async def markets(self) -> list[Market]:
        """Every spot market currently trading, as base/quote pairs."""
        payload = await self._get(EXCHANGE_INFO)
        out: list[Market] = []
        for entry in payload.get("symbols", []):
            if entry.get("status") != "TRADING":
                continue
            if not entry.get("isSpotTradingAllowed", True):
                continue
            out.append(Market(entry["symbol"], entry["baseAsset"], entry["quoteAsset"]))
        return out

    async def prices(self) -> dict[str, float]:
        """Last traded price for every symbol, in one request."""
        payload = await self._get(TICKER_PRICE)
        out: dict[str, float] = {}
        for entry in payload:
            try:
                price = float(entry["price"])
            except (TypeError, ValueError, KeyError):
                continue
            if price > 0:
                out[entry["symbol"]] = price
        return out

    # -- signed -----------------------------------------------------------

    async def market_order(
        self,
        symbol: str,
        side: str,
        quantity: float | None = None,
        quote_quantity: float | None = None,
    ) -> dict[str, Any]:
        """Place one market order.

        `quote_quantity` spends an exact amount of the quote asset, which is
        what a swap wants on a BUY: the operator entered "500 USDT", not a
        quantity of the base they would have to compute themselves.
        """
        params: dict[str, Any] = {"symbol": symbol, "side": side, "type": "MARKET"}
        if quote_quantity is not None:
            params["quoteOrderQty"] = _amount(quote_quantity)
        elif quantity is not None:
            params["quantity"] = _amount(quantity)
        else:
            raise ValueError("a market order needs a quantity or a quote quantity")
        return await self._signed("POST", ORDER, params)

    async def balances(self) -> dict[str, float]:
        """Free balance per asset, for assets with any."""
        payload = await self._signed("GET", ACCOUNT, {})
        out: dict[str, float] = {}
        for entry in payload.get("balances", []):
            try:
                free = float(entry["free"])
            except (TypeError, ValueError, KeyError):
                continue
            if free > 0:
                out[entry["asset"]] = free
        return out

    # -- transport --------------------------------------------------------

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if self._http is None:
            raise RuntimeError("client must be used as an async context manager")
        response = await self._http.get(path, params=params)
        response.raise_for_status()
        return response.json()

    async def _signed(self, method: str, path: str, params: dict[str, Any]) -> Any:
        if not self.config.can_trade_spot:
            raise SpotCredentialsMissing(
                "Spot trading needs its own Binance key. Set BINANCE_SPOT_API_KEY and "
                "BINANCE_SPOT_API_SECRET - a futures key is rejected by every spot "
                "endpoint with -2015."
            )
        if self._http is None:
            raise RuntimeError("client must be used as an async context manager")
        query = dict(params)
        query["timestamp"] = int(time.time() * 1000)
        query["recvWindow"] = self.config.recv_window_ms
        signature = hmac.new(
            self.config.spot_api_secret.encode(),
            urlencode(query).encode(),
            hashlib.sha256,
        ).hexdigest()
        query["signature"] = signature
        response = await self._http.request(
            method,
            path,
            params=query,
            headers={"X-MBX-APIKEY": self.config.spot_api_key},
        )
        response.raise_for_status()
        return response.json()


def _amount(value: float) -> str:
    """Binance rejects scientific notation, which is what str() gives small floats."""
    return f"{value:.8f}".rstrip("0").rstrip(".")
