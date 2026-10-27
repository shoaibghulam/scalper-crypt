"""Turning "swap A for B" into Binance spot orders.

Spot has no swap primitive: there are only markets, each one BASE/QUOTE, and
you either BUY the base or SELL it. So a swap is a route - one order when the
pair exists in either direction, two through a bridge asset when it does not.
Getting the side backwards spends the wrong asset, so every direction here is
pinned by a test.
"""

import pytest

from scalper.exchange.spot_routing import Leg, NoRoute, Market, plan_route

#: A small, realistic slice of Binance spot.
MARKETS = [
    Market("BTCUSDT", "BTC", "USDT"),
    Market("ETHUSDT", "ETH", "USDT"),
    Market("SOLUSDT", "SOL", "USDT"),
    Market("ETHBTC", "ETH", "BTC"),
    Market("BNBUSDT", "BNB", "USDT"),
]


class TestDirectPair:
    def test_quote_to_base_is_a_buy(self):
        # USDT -> BTC: BTCUSDT exists with USDT as the quote, so we buy base.
        assert plan_route(MARKETS, "USDT", "BTC") == [Leg("BTCUSDT", "BUY", "USDT", "BTC")]

    def test_base_to_quote_is_a_sell(self):
        # BTC -> USDT: same market, opposite direction.
        assert plan_route(MARKETS, "BTC", "USDT") == [Leg("BTCUSDT", "SELL", "BTC", "USDT")]

    def test_a_non_usdt_pair_is_used_when_it_exists(self):
        # ETHBTC exists, so ETH -> BTC must not be routed through USDT.
        assert plan_route(MARKETS, "ETH", "BTC") == [Leg("ETHBTC", "SELL", "ETH", "BTC")]

    def test_and_in_the_other_direction(self):
        assert plan_route(MARKETS, "BTC", "ETH") == [Leg("ETHBTC", "BUY", "BTC", "ETH")]


class TestBridged:
    def test_two_assets_with_no_shared_market_route_through_usdt(self):
        # No SOLBNB market exists; sell SOL for USDT, then buy BNB with it.
        assert plan_route(MARKETS, "SOL", "BNB") == [
            Leg("SOLUSDT", "SELL", "SOL", "USDT"),
            Leg("BNBUSDT", "BUY", "USDT", "BNB"),
        ]

    def test_the_bridge_is_never_longer_than_two_legs(self):
        for leg_count in [len(plan_route(MARKETS, a, b))
                          for a in ("BTC", "ETH", "SOL", "BNB", "USDT")
                          for b in ("BTC", "ETH", "SOL", "BNB", "USDT") if a != b]:
            assert leg_count <= 2


class TestRefusals:
    def test_swapping_an_asset_for_itself_is_refused(self):
        with pytest.raises(NoRoute):
            plan_route(MARKETS, "BTC", "BTC")

    def test_an_unlisted_asset_is_refused(self):
        with pytest.raises(NoRoute):
            plan_route(MARKETS, "BTC", "DOGE")

    def test_the_message_names_both_assets(self):
        # An operator needs to know which side of the pair was the problem.
        with pytest.raises(NoRoute, match="DOGE"):
            plan_route(MARKETS, "BTC", "DOGE")
