"""The Binance futures client.

The single most important behaviour here is order routing. Since 2025-12-09
conditional orders are refused by /fapi/v1/order with -4120 and must go to
/fapi/v1/algoOrder under different parameter names. Getting this wrong does not
degrade gracefully: it means a position opens with no stop-loss attached.

The second is that algo orders live in a separate namespace, invisible to
openOrders and unaffected by allOpenOrders, so any cancel-everything path that
touches only one namespace leaves live stops attached to a closed position.
"""

from decimal import Decimal

import httpx
import pytest
import respx

from scalper.config import Environment, ExchangeConfig
from scalper.exchange.client import (
    ALGO_ORDER_PATH,
    CONDITIONAL_ORDER_TYPES,
    ORDER_PATH,
    BinanceFuturesClient,
)
from scalper.exchange.errors import Action, BinanceError

BASE = "https://testnet.binancefuture.com"


def make_client() -> BinanceFuturesClient:
    config = ExchangeConfig(
        env=Environment.TESTNET, api_key="test-key", api_secret="test-secret"
    )
    return BinanceFuturesClient(config)


class TestOrderRouting:
    def test_conditional_types_are_known(self):
        for kind in ("STOP_MARKET", "TAKE_PROFIT_MARKET", "TRAILING_STOP_MARKET"):
            assert kind in CONDITIONAL_ORDER_TYPES

    def test_plain_types_are_not_conditional(self):
        assert "LIMIT" not in CONDITIONAL_ORDER_TYPES
        assert "MARKET" not in CONDITIONAL_ORDER_TYPES

    @respx.mock
    async def test_limit_order_goes_to_the_plain_endpoint(self):
        route = respx.post(f"{BASE}{ORDER_PATH}").mock(
            return_value=httpx.Response(200, json={"orderId": 1, "status": "NEW"})
        )
        async with make_client() as client:
            await client.place_order(
                symbol="BTCUSDT",
                side="BUY",
                order_type="LIMIT",
                quantity=Decimal("0.001"),
                price=Decimal("63520.10"),
                client_order_id="bot1-1-entry",
                time_in_force="GTX",
            )
        assert route.called

    async def test_conditional_order_sent_to_place_order_is_refused_locally(self):
        # Caught before the request leaves, so the failure surfaces as a bug in
        # our code rather than as an opaque -4120 at the worst possible moment.
        async with make_client() as client:
            with pytest.raises(ValueError, match="algoOrder"):
                await client.place_order(
                    symbol="BTCUSDT",
                    side="SELL",
                    order_type="STOP_MARKET",
                    quantity=Decimal("0.001"),
                    client_order_id="bot1-1-sl",
                )

    @respx.mock
    async def test_stop_loss_goes_to_the_algo_endpoint(self):
        route = respx.post(f"{BASE}{ALGO_ORDER_PATH}").mock(
            return_value=httpx.Response(200, json={"algoId": 99})
        )
        async with make_client() as client:
            await client.place_algo_order(
                symbol="BTCUSDT",
                side="SELL",
                order_type="STOP_MARKET",
                trigger_price=Decimal("63180.00"),
                client_algo_id="bot1-1-sl",
                close_position=True,
            )
        assert route.called


class TestAlgoParameterNames:
    @respx.mock
    async def test_algo_order_uses_the_renamed_parameters(self):
        route = respx.post(f"{BASE}{ALGO_ORDER_PATH}").mock(
            return_value=httpx.Response(200, json={"algoId": 99})
        )
        async with make_client() as client:
            await client.place_algo_order(
                symbol="BTCUSDT",
                side="SELL",
                order_type="STOP_MARKET",
                trigger_price=Decimal("63180.00"),
                client_algo_id="bot1-1-sl",
                close_position=True,
            )
        body = route.calls[0].request.url.query.decode()
        assert "triggerPrice=63180.00" in body
        assert "clientAlgoId=bot1-1-sl" in body
        assert "algoType=CONDITIONAL" in body
        # The superseded names must not appear at all.
        assert "stopPrice=" not in body
        assert "newClientOrderId=" not in body

    @respx.mock
    async def test_stop_loss_triggers_on_mark_price(self):
        # Contract price triggers on a thin last-traded print, which is what
        # wick hunters exploit, and disagrees with the price the exchange uses
        # for liquidation.
        route = respx.post(f"{BASE}{ALGO_ORDER_PATH}").mock(
            return_value=httpx.Response(200, json={"algoId": 99})
        )
        async with make_client() as client:
            await client.place_algo_order(
                symbol="BTCUSDT",
                side="SELL",
                order_type="STOP_MARKET",
                trigger_price=Decimal("63180.00"),
                client_algo_id="bot1-1-sl",
                close_position=True,
            )
        body = route.calls[0].request.url.query.decode()
        assert "workingType=MARK_PRICE" in body

    @respx.mock
    async def test_close_position_orders_carry_no_quantity(self):
        # A reduceOnly stop sized for the full intent is rejected with -2022
        # when the entry only partially filled. closePosition is size-agnostic.
        route = respx.post(f"{BASE}{ALGO_ORDER_PATH}").mock(
            return_value=httpx.Response(200, json={"algoId": 99})
        )
        async with make_client() as client:
            await client.place_algo_order(
                symbol="BTCUSDT",
                side="SELL",
                order_type="STOP_MARKET",
                trigger_price=Decimal("63180.00"),
                client_algo_id="bot1-1-sl",
                close_position=True,
            )
        body = route.calls[0].request.url.query.decode()
        assert "quantity=" not in body
        assert "closePosition=true" in body


class TestCancellationCoversBothNamespaces:
    @respx.mock
    async def test_cancel_all_hits_both_endpoints(self):
        plain = respx.delete(f"{BASE}/fapi/v1/allOpenOrders").mock(
            return_value=httpx.Response(200, json={"code": 200})
        )
        algo = respx.delete(f"{BASE}/fapi/v1/algoOpenOrders").mock(
            return_value=httpx.Response(200, json={"code": 200})
        )
        async with make_client() as client:
            await client.cancel_all_orders("BTCUSDT")
        assert plain.called, "plain orders were not cancelled"
        assert algo.called, "algo orders were not cancelled, leaving live stops"

    @respx.mock
    async def test_algo_sweep_still_runs_when_the_plain_sweep_fails(self):
        # Leaving a stop attached to a closed position is the failure being
        # guarded against, so one failure must not skip the other sweep.
        respx.delete(f"{BASE}/fapi/v1/allOpenOrders").mock(
            return_value=httpx.Response(400, json={"code": -1000, "msg": "boom"})
        )
        algo = respx.delete(f"{BASE}/fapi/v1/algoOpenOrders").mock(
            return_value=httpx.Response(200, json={"code": 200})
        )
        async with make_client() as client:
            with pytest.raises(BinanceError):
                await client.cancel_all_orders("BTCUSDT")
        assert algo.called


class TestErrorHandling:
    @respx.mock
    async def test_exchange_error_is_classified(self):
        respx.post(f"{BASE}{ORDER_PATH}").mock(
            return_value=httpx.Response(
                400, json={"code": -2019, "msg": "Margin is insufficient."}
            )
        )
        async with make_client() as client:
            with pytest.raises(BinanceError) as caught:
                await client.place_order(
                    symbol="BTCUSDT",
                    side="BUY",
                    order_type="MARKET",
                    quantity=Decimal("0.001"),
                    client_order_id="bot1-1-entry",
                )
        assert caught.value.action is Action.SKIP

    @respx.mock
    async def test_margin_type_already_set_is_not_an_error(self):
        respx.post(f"{BASE}/fapi/v1/marginType").mock(
            return_value=httpx.Response(
                400, json={"code": -4046, "msg": "No need to change margin type."}
            )
        )
        async with make_client() as client:
            result = await client.set_margin_type("BTCUSDT", "ISOLATED")
        assert result["already_set"] is True

    @respx.mock
    async def test_rate_limit_weight_is_tracked(self):
        respx.post(f"{BASE}{ORDER_PATH}").mock(
            return_value=httpx.Response(
                200,
                json={"orderId": 1},
                headers={"X-MBX-USED-WEIGHT-1M": "1340"},
            )
        )
        async with make_client() as client:
            await client.place_order(
                symbol="BTCUSDT",
                side="BUY",
                order_type="MARKET",
                quantity=Decimal("0.001"),
                client_order_id="bot1-1-entry",
            )
            assert client.used_weight_1m == 1340


class TestCredentialHandling:
    @respx.mock
    async def test_api_key_is_sent_as_a_header_never_in_the_query(self):
        route = respx.post(f"{BASE}{ORDER_PATH}").mock(
            return_value=httpx.Response(200, json={"orderId": 1})
        )
        async with make_client() as client:
            await client.place_order(
                symbol="BTCUSDT",
                side="BUY",
                order_type="MARKET",
                quantity=Decimal("0.001"),
                client_order_id="bot1-1-entry",
            )
        request = route.calls[0].request
        assert request.headers["X-MBX-APIKEY"] == "test-key"
        assert "test-key" not in request.url.query.decode()

    @respx.mock
    async def test_secret_never_appears_on_the_wire(self):
        route = respx.post(f"{BASE}{ORDER_PATH}").mock(
            return_value=httpx.Response(200, json={"orderId": 1})
        )
        async with make_client() as client:
            await client.place_order(
                symbol="BTCUSDT",
                side="BUY",
                order_type="MARKET",
                quantity=Decimal("0.001"),
                client_order_id="bot1-1-entry",
            )
        request = route.calls[0].request
        assert "test-secret" not in request.url.query.decode()
        assert "test-secret" not in str(dict(request.headers))


LEVERAGE_BRACKETS = {
    "symbol": "INJUSDT",
    "brackets": [
        {"bracket": 1, "initialLeverage": 25, "notionalCap": 5000},
        {"bracket": 2, "initialLeverage": 20, "notionalCap": 50000},
        {"bracket": 3, "initialLeverage": 10, "notionalCap": 1600000},
    ],
}


class TestLeverageBrackets:
    """Regression: a live bot died on -4028 asking 50x of a 25x symbol.

    Raising the operator-facing ceiling to 100x made this routine rather than
    rare. Binance publishes a per-symbol, per-notional bracket table, and the
    first bracket carries the highest leverage the symbol permits at all.

    Clamping rather than failing: the operator asked for as much leverage as
    the symbol allows, and refusing the whole bot because one of its five
    symbols caps lower would be a worse reading of that intent than trading it
    at the cap. What matters is that the clamp is reported, not silent.
    """


    @respx.mock
    async def test_the_highest_bracket_is_returned(self):
        respx.get(url__regex=r".*/fapi/v1/leverageBracket.*").respond(
            200, json=[LEVERAGE_BRACKETS]
        )
        async with make_client() as client:
            assert await client.max_leverage("INJUSDT") == 25

    @respx.mock
    async def test_a_dict_response_is_handled(self):
        # The endpoint returns a bare object when queried for one symbol on
        # some builds and a list on others.
        respx.get(url__regex=r".*/fapi/v1/leverageBracket.*").respond(
            200, json=LEVERAGE_BRACKETS
        )
        async with make_client() as client:
            assert await client.max_leverage("INJUSDT") == 25

    @respx.mock
    async def test_an_unreadable_response_does_not_raise(self):
        # This runs on the path to placing an order. Failing here would turn a
        # recoverable metadata problem into a refused trade, so it degrades to
        # "no known cap" and lets the exchange be the authority.
        respx.get(url__regex=r".*/fapi/v1/leverageBracket.*").respond(
            200, json={"unexpected": True}
        )
        async with make_client() as client:
            assert await client.max_leverage("INJUSDT") is None

    @respx.mock
    async def test_an_error_response_does_not_raise(self):
        respx.get(url__regex=r".*/fapi/v1/leverageBracket.*").respond(
            400, json={"code": -1121, "msg": "Invalid symbol."}
        )
        async with make_client() as client:
            assert await client.max_leverage("NOPEUSDT") is None

@respx.mock
async def test_signed_rate_limit_pauses_public_market_requests(monkeypatch):
    from scalper.exchange import market_limits
    from scalper.exchange.client import POSITION_RISK_PATH
    monkeypatch.setattr(market_limits, 'LIMITS', {})
    respx.get(f'{BASE}{POSITION_RISK_PATH}').mock(return_value=httpx.Response(
        429, json={'code': -1003, 'msg': 'Too many requests'}, headers={'Retry-After': '120'}))
    async with make_client() as client:
        with pytest.raises(BinanceError) as failure:
            await client.get_positions()
    assert failure.value.code == -1003
    assert failure.value.http_status == 429
    with pytest.raises(httpx.HTTPStatusError):
        market_limits.LIMITS[BASE].check(f'{BASE}/fapi/v1/klines')


@respx.mock
async def test_signed_read_respects_persisted_ban_before_clock_sync():
    import time
    from scalper.exchange.market_limits import save_cooldown
    save_cooldown(BASE, time.time() + 300)
    async with make_client() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.get_account()
    assert len(respx.calls) == 0
