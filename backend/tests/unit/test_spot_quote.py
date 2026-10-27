"""What a swap actually returns, before it is placed.

The number shown before the button is pressed has to include every cost, or
it is an advertisement rather than a quote. Two legs pay two fees and cross
two spreads, which is the whole reason a direct market is preferred.
"""

import pytest

from scalper.exchange.spot_routing import Leg
from scalper.exchange.spot_quote import TAKER_FEE, quote_route

PRICES = {"BTCUSDT": 100_000.0, "ETHUSDT": 4_000.0, "SOLUSDT": 200.0, "BNBUSDT": 800.0}


class TestSingleLeg:
    def test_buying_divides_by_the_price(self):
        legs = [Leg("BTCUSDT", "BUY", "USDT", "BTC")]
        out = quote_route(legs, PRICES, 100_000.0)
        assert out.amount_out == pytest.approx(1.0 * (1 - TAKER_FEE))

    def test_selling_multiplies_by_the_price(self):
        legs = [Leg("BTCUSDT", "SELL", "BTC", "USDT")]
        out = quote_route(legs, PRICES, 1.0)
        assert out.amount_out == pytest.approx(100_000.0 * (1 - TAKER_FEE))

    def test_the_fee_is_reported_not_just_deducted(self):
        # An operator comparing venues needs the cost, not only the result.
        out = quote_route([Leg("BTCUSDT", "SELL", "BTC", "USDT")], PRICES, 1.0)
        assert out.fee_paid == pytest.approx(100_000.0 * TAKER_FEE)


class TestTwoLegs:
    def test_a_bridged_route_pays_the_fee_twice(self):
        legs = [Leg("SOLUSDT", "SELL", "SOL", "USDT"), Leg("BNBUSDT", "BUY", "USDT", "BNB")]
        out = quote_route(legs, PRICES, 100.0)
        # 100 SOL -> 20,000 USDT -> 25 BNB, each leg less the taker fee.
        assert out.amount_out == pytest.approx(25.0 * (1 - TAKER_FEE) ** 2)

    def test_the_second_leg_spends_what_the_first_produced(self):
        # Compounding, not a fee applied once to the gross - which would
        # overstate the result on every bridged swap.
        legs = [Leg("SOLUSDT", "SELL", "SOL", "USDT"), Leg("BNBUSDT", "BUY", "USDT", "BNB")]
        out = quote_route(legs, PRICES, 100.0)
        assert out.amount_out < 25.0 * (1 - TAKER_FEE)


class TestRefusals:
    def test_a_missing_price_is_refused_rather_than_guessed(self):
        with pytest.raises(KeyError):
            quote_route([Leg("XRPUSDT", "BUY", "USDT", "XRP")], PRICES, 10.0)

    def test_a_zero_price_is_refused(self):
        # Dividing by it would report an infinite payout.
        with pytest.raises(ValueError):
            quote_route([Leg("BTCUSDT", "BUY", "USDT", "BTC")], {"BTCUSDT": 0.0}, 10.0)

    def test_a_non_positive_amount_is_refused(self):
        with pytest.raises(ValueError):
            quote_route([Leg("BTCUSDT", "SELL", "BTC", "USDT")], PRICES, 0.0)
