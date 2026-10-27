"""Placing a trade.

The ordering is not arbitrary. Leverage is set before the entry, because
changing it on an open position recalculates the liquidation price and can move
liquidation into the market. The bracket goes on only after the fill is
confirmed, because there is nothing to protect until then.

The case this file exists for is the one in between: the entry filled and the
stop-loss did not. A position with no stop has unbounded downside and the
engine cannot simply log it and move on. It retries, and if the stop still will
not attach it closes the position at market. Taking a small certain loss is the
correct trade against an unbounded uncertain one.
"""

from decimal import Decimal

import httpx
import pytest
import respx

from scalper.config import Environment, ExchangeConfig
from scalper.core.filters import SymbolFilters
from scalper.engine.executor import OrderExecutor, UnprotectedPosition
from scalper.engine.plan import PlanRejected, TradePlan
from scalper.exchange.client import BinanceFuturesClient

BASE = "https://testnet.binancefuture.com"
BTCUSDT = SymbolFilters(
    symbol="BTCUSDT",
    tick_size=Decimal("0.10"),
    step_size=Decimal("0.001"),
    min_qty=Decimal("0.001"),
    min_notional=Decimal("100"),
)


def a_plan() -> TradePlan:
    return TradePlan(
        bot_id="alpha",
        signal_seq=7,
        symbol="BTCUSDT",
        side="LONG",
        quantity=Decimal("0.010"),
        entry=Decimal("63520.00"),
        stop=Decimal("63180.00"),
        target=Decimal("64180.00"),
        leverage=5,
    )


def client() -> BinanceFuturesClient:
    return BinanceFuturesClient(
        ExchangeConfig(env=Environment.TESTNET, api_key="k", api_secret="s")
    )


def ok(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


class TestPreparation:
    @respx.mock
    async def test_leverage_is_set_before_the_entry_is_sent(self):
        calls: list[str] = []

        def note(name, payload):
            def handler(request):
                calls.append(name)
                return ok(payload)

            return handler

        respx.post(f"{BASE}/fapi/v1/marginType").mock(side_effect=note("margin", {}))
        respx.post(f"{BASE}/fapi/v1/leverage").mock(side_effect=note("leverage", {}))
        respx.post(f"{BASE}/fapi/v1/order").mock(
            side_effect=note("entry", {"orderId": 1})
        )

        async with client() as c:
            await OrderExecutor(c).open(a_plan(), BTCUSDT)

        assert calls.index("leverage") < calls.index("entry")

    @respx.mock
    async def test_margin_already_isolated_is_not_an_error(self):
        respx.post(f"{BASE}/fapi/v1/marginType").mock(
            return_value=httpx.Response(
                400, json={"code": -4046, "msg": "No need to change margin type."}
            )
        )
        respx.post(f"{BASE}/fapi/v1/leverage").mock(return_value=ok({}))
        respx.post(f"{BASE}/fapi/v1/order").mock(return_value=ok({"orderId": 1}))

        async with client() as c:
            result = await OrderExecutor(c).open(a_plan(), BTCUSDT)
        assert result.entry_order_id == 1


class TestEntry:
    @respx.mock
    async def test_the_entry_is_post_only(self):
        # A taker entry roughly doubles the round-trip cost, which at this
        # timeframe is most of the edge.
        respx.post(f"{BASE}/fapi/v1/marginType").mock(return_value=ok({}))
        respx.post(f"{BASE}/fapi/v1/leverage").mock(return_value=ok({}))
        route = respx.post(f"{BASE}/fapi/v1/order").mock(return_value=ok({"orderId": 1}))

        async with client() as c:
            await OrderExecutor(c).open(a_plan(), BTCUSDT)

        query = route.calls[0].request.url.query.decode()
        assert "timeInForce=GTX" in query
        assert "type=LIMIT" in query

    @respx.mock
    async def test_the_entry_carries_a_derived_client_order_id(self):
        respx.post(f"{BASE}/fapi/v1/marginType").mock(return_value=ok({}))
        respx.post(f"{BASE}/fapi/v1/leverage").mock(return_value=ok({}))
        route = respx.post(f"{BASE}/fapi/v1/order").mock(return_value=ok({"orderId": 1}))

        async with client() as c:
            await OrderExecutor(c).open(a_plan(), BTCUSDT)

        query = route.calls[0].request.url.query.decode()
        assert "newClientOrderId=alpha-7-entry" in query

    @respx.mock
    async def test_an_invalid_plan_never_reaches_the_exchange(self):
        route = respx.post(f"{BASE}/fapi/v1/order").mock(return_value=ok({"orderId": 1}))
        bad = TradePlan(
            bot_id="alpha",
            signal_seq=1,
            symbol="BTCUSDT",
            side="LONG",
            quantity=Decimal("0.010"),
            entry=Decimal("63520.00"),
            stop=Decimal("63500.00"),
            target=Decimal("64180.00"),
            leverage=5,
        )
        async with client() as c:
            # Specifically a plan rejection, not any error: a connection
            # failure would also satisfy a blind assertion here.
            with pytest.raises(PlanRejected, match="stop"):
                await OrderExecutor(c).open(bad, BTCUSDT)
        assert not route.called


class TestBracket:
    @respx.mock
    async def test_both_legs_go_to_the_algo_endpoint(self):
        route = respx.post(f"{BASE}/fapi/v1/algoOrder").mock(
            return_value=ok({"algoId": 9})
        )
        async with client() as c:
            await OrderExecutor(c).attach_bracket(a_plan())
        assert route.call_count == 2

    @respx.mock
    async def test_the_stop_closes_the_whole_position(self):
        # Sizing the stop to the intended quantity fails with -2022 when the
        # entry only partially filled, at the moment the stop was needed.
        route = respx.post(f"{BASE}/fapi/v1/algoOrder").mock(
            return_value=ok({"algoId": 9})
        )
        async with client() as c:
            await OrderExecutor(c).attach_bracket(a_plan())
        first = route.calls[0].request.url.query.decode()
        assert "closePosition=true" in first
        assert "quantity=" not in first

    @respx.mock
    async def test_the_stop_is_placed_before_the_target(self):
        # If only one leg makes it, protection is the one worth having.
        seen: list[str] = []

        def record(request):
            seen.append(request.url.query.decode())
            return ok({"algoId": 9})

        respx.post(f"{BASE}/fapi/v1/algoOrder").mock(side_effect=record)
        async with client() as c:
            await OrderExecutor(c).attach_bracket(a_plan())
        assert "STOP_MARKET" in seen[0]
        assert "TAKE_PROFIT" in seen[1]

    @respx.mock
    async def test_the_stop_triggers_on_mark_price(self):
        route = respx.post(f"{BASE}/fapi/v1/algoOrder").mock(
            return_value=ok({"algoId": 9})
        )
        async with client() as c:
            await OrderExecutor(c).attach_bracket(a_plan())
        query = route.calls[0].request.url.query.decode()
        assert "workingType=MARK_PRICE" in query


class TestUnprotectedPosition:
    @respx.mock
    async def test_a_failing_stop_is_retried(self):
        attempts = {"n": 0}

        def flaky(request):
            attempts["n"] += 1
            if attempts["n"] == 1:
                return httpx.Response(500, json={"code": -1001, "msg": "internal"})
            return ok({"algoId": 9})

        respx.post(f"{BASE}/fapi/v1/algoOrder").mock(side_effect=flaky)
        async with client() as c:
            await OrderExecutor(c).attach_bracket(a_plan())
        assert attempts["n"] > 1

    @respx.mock
    async def test_a_position_that_cannot_be_protected_is_closed(self):
        # The whole point of this class. An unprotected position has unbounded
        # downside, so a small certain loss is the correct trade against it.
        respx.post(f"{BASE}/fapi/v1/algoOrder").mock(
            return_value=httpx.Response(400, json={"code": -1111, "msg": "nope"})
        )
        emergency = respx.post(f"{BASE}/fapi/v1/order").mock(
            return_value=ok({"orderId": 55, "status": "FILLED"})
        )
        async with client() as c:
            with pytest.raises(UnprotectedPosition):
                await OrderExecutor(c).attach_bracket(a_plan())
        assert emergency.called, "the position was left without a stop"

    @respx.mock
    async def test_the_emergency_close_reduces_only(self):
        respx.post(f"{BASE}/fapi/v1/algoOrder").mock(
            return_value=httpx.Response(400, json={"code": -1111, "msg": "nope"})
        )
        emergency = respx.post(f"{BASE}/fapi/v1/order").mock(
            return_value=ok({"orderId": 55})
        )
        async with client() as c:
            with pytest.raises(UnprotectedPosition):
                await OrderExecutor(c).attach_bracket(a_plan())
        query = emergency.calls[0].request.url.query.decode()
        assert "reduceOnly=true" in query
        assert "type=MARKET" in query
