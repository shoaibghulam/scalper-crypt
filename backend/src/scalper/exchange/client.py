"""Binance USDT-M futures REST client.

Two pieces of hard-won knowledge are enforced structurally here rather than
left to the caller to remember.

Conditional orders changed endpoint on 2025-12-09. Stop-loss, take-profit and
trailing-stop orders are refused by the plain order endpoint with -4120 and
must go to the algo endpoint, under renamed parameters. Sending one to the
wrong place does not fail safely: it means a position opens and its protective
order does not exist. So place_order refuses conditional types locally, before
any request leaves, turning a subtle runtime failure into an obvious bug.

Algo orders also live in a separate namespace. They are invisible to
openOrders and untouched by allOpenOrders, so a cancel-everything path that
sweeps only one namespace leaves live stops attached to a closed position.
cancel_all_orders therefore always sweeps both, and a failure in one sweep does
not skip the other.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from types import TracebackType
from typing import Any

import httpx

from scalper.config import Environment, ExchangeConfig
from scalper.exchange.clock import ClockSync
from scalper.exchange.errors import BinanceError, classify, is_benign
from scalper.exchange.signing import signed_params

ORDER_PATH = "/fapi/v1/order"
ALGO_ORDER_PATH = "/fapi/v1/algoOrder"
#: Cancel-all uses algoOpenOrders; listing uses openAlgoOrders. They are not
#: the same path and the listing one is easy to guess wrong. Verified live:
#: GET /fapi/v1/algoOpenOrders returns -5000 Path invalid.
ALGO_CANCEL_ALL_PATH = "/fapi/v1/algoOpenOrders"
ALGO_LIST_PATH = "/fapi/v1/openAlgoOrders"
ALL_OPEN_ORDERS_PATH = "/fapi/v1/allOpenOrders"
LEVERAGE_PATH = "/fapi/v1/leverage"
MARGIN_TYPE_PATH = "/fapi/v1/marginType"
log = logging.getLogger("scalper.exchange.client")

TIME_PATH = "/fapi/v1/time"
ACCOUNT_PATH = "/fapi/v2/account"
LEVERAGE_BRACKET_PATH = "/fapi/v1/leverageBracket"
POSITION_RISK_PATH = "/fapi/v3/positionRisk"

#: Order types the plain order endpoint no longer accepts.
CONDITIONAL_ORDER_TYPES = frozenset(
    {
        "STOP",
        "STOP_MARKET",
        "TAKE_PROFIT",
        "TAKE_PROFIT_MARKET",
        "TRAILING_STOP_MARKET",
    }
)

#: One clock per REST origin, shared by every client against it.
CLOCKS: dict[str, ClockSync] = {}

WEIGHT_HEADER = "X-MBX-USED-WEIGHT-1M"


def _default_working_type(order_type: str) -> str:
    """Which price an algo order should trigger on.

    Stops trigger on mark price so a thin last-traded print cannot take out a
    good position, and so our trigger agrees with the price the exchange uses
    to compute liquidation. Take-profits trigger on contract price, because
    there the actual print is what we want to capture.
    """
    return "MARK_PRICE" if order_type.startswith("STOP") else "CONTRACT_PRICE"


class BinanceFuturesClient:
    """Signed REST access to one exchange account."""

    def __init__(
        self,
        config: ExchangeConfig,
        *,
        http: httpx.AsyncClient | None = None,
        clock: ClockSync | None = None,
    ) -> None:
        self.config = config
        # Shared per origin, not per client. The engine builds a client every
        # refresh cycle, and a fresh ClockSync has never synced - so it spent a
        # /fapi/v1/time call on every single one. Measured: 196 of 681 requests,
        # 29% of all traffic, to re-learn an offset that changes by
        # milliseconds an hour. One clock per host restores the 15 minute
        # interval the resync logic was always written for.
        self.clock = clock or CLOCKS.setdefault(self.config.rest, ClockSync())
        self.used_weight_1m: int | None = None
        self._http = http
        self._owns_http = http is None

    async def __aenter__(self) -> BinanceFuturesClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self.config.rest,
                timeout=httpx.Timeout(10.0, connect=5.0),
                headers={"X-MBX-APIKEY": self.config.api_key},
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

    async def _ensure_clock_synced(self) -> None:
        """Re-estimate the offset against exchange time when it has gone stale.

        Binance rejects a request whose timestamp falls outside recvWindow with
        -1021. The usual cause is not a badly wrong clock but a slowly drifting
        one, which is why this is checked before every signed request rather
        than once at startup: a bot that syncs at boot and runs for days fails
        mid-session, and the failure looks like an outage.

        The sample is taken from the PUBLIC time endpoint, so there is no
        chicken and egg - a clock too far out to sign with can still ask what
        time it is.

        Never raises. If the sync fails the request proceeds on the unsynced
        clock, because degrading to the old behaviour is better than refusing
        to trade, and the exchange remains the authority on whether the
        timestamp was actually acceptable.
        """
        if self._http is None:
            return
        if not self.clock.needs_resync(local_now_ms=int(time.time() * 1000)):
            return

        try:
            sent = int(time.time() * 1000)
            response = await self._http.get(TIME_PATH)
            received = int(time.time() * 1000)
            response.raise_for_status()
            server_ms = int(response.json()["serverTime"])
        except Exception:
            # Logged at debug: a failed sync is common during a network blip
            # and the next signed request will try again.
            log.debug("could not sync the exchange clock; using the local one")
            return

        if not self.clock.observe(
            sent_ms=sent, server_ms=server_ms, received_ms=received
        ):
            log.debug(
                "discarded a clock sample with a %dms round trip", received - sent
            )

    async def _signed(self, method: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if method.upper() != "GET":
            if self.config.env is Environment.PAPER:
                raise RuntimeError("paper mode cannot send exchange mutations; simulator required")
            self.config.assert_armed()
        if self._http is None:
            raise RuntimeError("client must be used as an async context manager")

        # A blocked account GET must not even send a clock-sync request.
        # Protective mutations retain their existing separate execution path.
        from scalper.exchange.market_limits import LIMITS, MarketLimit
        gate = LIMITS.setdefault(self.config.rest, MarketLimit())
        if method.upper() == "GET":
            # Signed reads are account state: a few weight-5 calls that
            # reconciliation cannot proceed without. A crowded shared IP must
            # not be able to stop them; an actual ban still does.
            gate.check(self.config.rest + path, essential=True)
        await self._ensure_clock_synced()

        local_now = int(time.time() * 1000)
        query = signed_params(
            params,
            secret=self.config.api_secret,
            timestamp_ms=self.clock.timestamp_ms(local_now_ms=local_now),
            recv_window_ms=self.config.recv_window_ms,
        )

        # Credentials travel in the header, never the query string, so a logged
        # or proxied URL cannot leak the key.
        response = await self._http.request(method, f"{path}?{query}")
        # Account-read rate limits must also pause public scans on this origin.
        gate.observe(response)

        weight = response.headers.get(WEIGHT_HEADER)
        if weight is not None:
            self.used_weight_1m = int(weight)

        return self._unwrap(response)

    def _unwrap(self, response: httpx.Response) -> dict[str, Any]:
        if response.status_code < 400:
            payload = response.json()
            return payload if isinstance(payload, dict) else {"result": payload}

        try:
            payload = response.json()
            code = int(payload.get("code", response.status_code))
            message = str(payload.get("msg", response.text))
        except (ValueError, TypeError, AttributeError):
            raise BinanceError.from_http(response.status_code, response.text) from None

        if is_benign(code):
            return {"already_set": True, "code": code, "msg": message}
        error = classify(code, message)
        error.http_status = response.status_code
        raise error

    async def place_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal | None = None,
        price: Decimal | None = None,
        client_order_id: str,
        time_in_force: str | None = None,
        reduce_only: bool | None = None,
    ) -> dict[str, Any]:
        """Place a LIMIT or MARKET order.

        The client order id is deterministic and supplied by the caller, never
        generated here. Uniqueness is enforced among open orders only. An
        uncertain request must be reconciled, never blindly retried after fill.
        """
        if order_type in CONDITIONAL_ORDER_TYPES:
            raise ValueError(
                f"{order_type} is a conditional order and is refused by {ORDER_PATH} "
                f"with -4120; use place_algo_order, which posts to {ALGO_ORDER_PATH}"
            )

        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": quantity,
            "price": price,
            "timeInForce": time_in_force,
            "reduceOnly": reduce_only,
            "newClientOrderId": client_order_id,
            # RESULT returns the final filled state synchronously, which
            # matters for market orders where an ACK says nothing useful.
            "newOrderRespType": "RESULT" if (
                order_type == "MARKET" or (order_type == "LIMIT" and time_in_force == "IOC")
            ) else None,
        }
        return await self._signed("POST", ORDER_PATH, params)

    async def place_algo_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        client_algo_id: str,
        trigger_price: Decimal | None = None,
        close_position: bool = False,
        quantity: Decimal | None = None,
        price: Decimal | None = None,
        working_type: str | None = None,
        callback_rate: Decimal | None = None,
        activate_price: Decimal | None = None,
    ) -> dict[str, Any]:
        """Place a stop-loss, take-profit or trailing stop.

        closePosition is preferred over reduceOnly with an explicit quantity.
        If the entry only partially filled, a reduceOnly stop sized for the full
        intended position tries to close more than is held and is rejected with
        -2022, at exactly the moment the stop was needed. closePosition is
        quantity-agnostic and cannot fail that way.
        """
        if order_type not in CONDITIONAL_ORDER_TYPES:
            raise ValueError(f"{order_type} is not a conditional order; use place_order")
        if close_position and quantity is not None:
            raise ValueError("closePosition cannot be combined with an explicit quantity")

        params: dict[str, Any] = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side,
            "type": order_type,
            # Renamed on migration: stopPrice became triggerPrice, and
            # newClientOrderId became clientAlgoId.
            "triggerPrice": trigger_price,
            "closePosition": close_position if close_position else None,
            "quantity": quantity,
            "price": price,
            "workingType": working_type or _default_working_type(order_type),
            # Suppresses a trigger while mark and last prices diverge sharply.
            "priceProtect": True,
            "activatePrice": activate_price,
            "callbackRate": callback_rate,
            "clientAlgoId": client_algo_id,
        }
        return await self._signed("POST", ALGO_ORDER_PATH, params)

    async def cancel_all_orders(self, symbol: str) -> dict[str, Any]:
        """Clear both order namespaces for a symbol.

        Both sweeps always run. If the first failed and we returned early, a
        live stop could remain attached to a position that is already closed,
        which is the exact failure this method exists to prevent.
        """
        failures: list[Exception] = []
        results: dict[str, Any] = {}

        for name, path in (
            ("plain", ALL_OPEN_ORDERS_PATH),
            ("algo", ALGO_CANCEL_ALL_PATH),
        ):
            try:
                results[name] = await self._signed("DELETE", path, {"symbol": symbol})
            except (BinanceError, httpx.HTTPError) as exc:
                failures.append(exc)

        if failures:
            raise failures[0]
        return results

    async def set_leverage(self, symbol: str, leverage: int) -> dict[str, Any]:
        """Set leverage. Always before an entry, never on a live position.

        Changing leverage while a position is open recalculates the liquidation
        price immediately, which can move liquidation into the current market.
        """
        return await self._signed("POST", LEVERAGE_PATH, {"symbol": symbol, "leverage": leverage})

    async def set_margin_type(self, symbol: str, margin_type: str) -> dict[str, Any]:
        """Set isolated or cross margin.

        Returns a success marker when the account is already in the requested
        mode, because -4046 means there was nothing to do.
        """
        return await self._signed(
            "POST", MARGIN_TYPE_PATH, {"symbol": symbol, "marginType": margin_type}
        )

    async def list_algo_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Protective orders the exchange currently holds for a symbol.

        Algo orders live in their own namespace and do not appear in
        openOrders, which was confirmed against the live exchange: two live
        protective orders while openOrders returned an empty list. Anything
        that reasons about whether a position is protected must ask here.
        """
        payload = await self._signed("GET", ALGO_LIST_PATH, {"symbol": symbol})
        orders = payload.get("result", payload.get("orders", []))
        if isinstance(orders, dict):
            return [orders]
        return list(orders)

    async def query_order(self, symbol: str, client_id: str) -> dict[str, Any]:
        return await self._signed(
            "GET", ORDER_PATH, {"symbol": symbol, "origClientOrderId": client_id}
        )

    async def query_order_id(self, symbol: str, order_id: int) -> dict[str, Any]:
        return await self._signed("GET", ORDER_PATH, {"symbol": symbol, "orderId": order_id})

    async def cancel_order(self, symbol: str, client_id: str) -> dict[str, Any]:
        return await self._signed(
            "DELETE", ORDER_PATH, {"symbol": symbol, "origClientOrderId": client_id}
        )

    async def open_orders(self, symbol: str) -> list[dict[str, Any]]:
        payload = await self._signed("GET", "/fapi/v1/openOrders", {"symbol": symbol})
        rows = payload.get("result")
        if not isinstance(rows, list):
            raise ValueError("invalid open-order response")
        return rows

    async def maintenance_rate(self, symbol: str, notional: Decimal, leverage: int) -> Decimal:
        payload = await self._signed("GET", LEVERAGE_BRACKET_PATH, {"symbol": symbol})
        rows = payload.get("result", [payload])
        for row in rows:
            if row.get("symbol") != symbol:
                continue
            for tier in row.get("brackets", []):
                if (
                    Decimal(str(tier["notionalFloor"]))
                    <= notional
                    < Decimal(str(tier["notionalCap"]))
                ):
                    if leverage > int(tier["initialLeverage"]):
                        raise ValueError("leverage exceeds the notional tier")
                    rate = Decimal(str(tier["maintMarginRatio"]))
                    if not rate.is_finite() or not 0 < rate < 1:
                        raise ValueError("invalid maintenance margin rate")
                    return rate
        raise ValueError("no matching leverage bracket")

    async def get_positions(self) -> dict[str, Any]:
        return await self._signed("GET", POSITION_RISK_PATH, {})

    async def cancel_algo_order(self, client_algo_id: str) -> dict[str, Any]:
        return await self._signed("DELETE", ALGO_ORDER_PATH, {"clientAlgoId": client_algo_id})

    async def account_trades(self, symbol: str, start_time: int) -> list[dict[str, Any]]:
        """Fetch complete bounded windows; split saturated windows without skipping timestamps."""
        end = int(time.time() * 1000)
        if start_time < end - 89 * 24 * 60 * 60 * 1000 or start_time > end:
            raise ValueError("trade history is outside supported retention")
        pending = [(start_time, end)]
        found: dict[int, dict[str, Any]] = {}
        requests = 0
        while pending:
            low, high = pending.pop()
            if high - low > 7 * 24 * 60 * 60 * 1000:
                boundary = low + 7 * 24 * 60 * 60 * 1000
                pending.extend([(boundary + 1, high), (low, boundary)])
                continue
            requests += 1
            if requests > 64:
                raise ValueError("settlement query budget exceeded; no incomplete totals returned")
            payload = await self._signed(
                "GET",
                "/fapi/v1/userTrades",
                {
                    "symbol": symbol,
                    "startTime": low,
                    "endTime": high,
                    "limit": 1000,
                },
            )
            rows = payload.get("result")
            if not isinstance(rows, list):
                raise ValueError("invalid trade history response")
            if len(rows) >= 1000:
                if low == high:
                    raise ValueError("too many fills at one timestamp; manual attribution required")
                middle = (low + high) // 2
                pending.extend([(middle + 1, high), (low, middle)])
                continue
            for row in rows:
                if not low <= int(row["time"]) <= high:
                    raise ValueError("trade outside requested settlement interval")
                found[int(row["id"])] = row
        return sorted(found.values(), key=lambda r: (int(r["time"]), int(r["id"])))

    async def max_leverage(self, symbol: str) -> int | None:
        """The highest leverage this symbol permits, or None if unknown.

        Binance publishes a per-symbol, per-notional bracket table; the first
        bracket carries the highest leverage available at all. A live bot died
        on -4028 asking 50x of a symbol that caps at 25x, and with the operator
        ceiling now at 100x that is routine rather than rare.

        Never raises. This sits on the path to placing an order, so a metadata
        problem must not become a refused trade - it degrades to "no known cap"
        and lets the exchange remain the authority.
        """
        try:
            payload = await self._signed("GET", LEVERAGE_BRACKET_PATH, {"symbol": symbol})
        except Exception:
            log.warning("could not read the leverage bracket for %s", symbol)
            return None

        rows = payload.get("result", payload)
        if isinstance(rows, list):
            rows = rows[0] if rows else {}
        if not isinstance(rows, dict):
            return None

        levels = [
            int(b["initialLeverage"])
            for b in rows.get("brackets", [])
            if isinstance(b, dict) and "initialLeverage" in b
        ]
        return max(levels) if levels else None

    async def get_account(self) -> dict[str, Any]:
        """Balances and permissions for the whole futures account.

        v2 rather than v3: v2 returns the wallet, margin and available figures
        as top-level strings, which is what the dashboard needs. It is the same
        endpoint the credential check already uses to prove the key works.
        """
        return await self._signed("GET", ACCOUNT_PATH, {})
