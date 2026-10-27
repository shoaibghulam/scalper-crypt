"""The economics of a scalp, as executable rules.

Research finding this encodes: at these timeframes the fee bill, not the
indicator, decides profitability. Binance USDT-M VIP 0 is maker 0.0200% and
taker 0.0500%; with the BNB discount, 0.0180% and 0.0450%. An all-taker round
trip therefore costs 0.090% of notional, which against a 0.30% target is 30% of
gross profit before slippage.

The only ratio that matters is cost over stop distance (C/S). Every rule below
falls out of that.
"""

from decimal import Decimal

import pytest

from scalper.core.fees import (
    BNB_DISCOUNT,
    MAX_COST_TO_STOP_RATIO,
    MIN_STOP_FRACTION,
    VIP0_MAKER,
    VIP0_TAKER,
    FeeModel,
    breakeven_stop_price,
    breakeven_win_rate,
    cost_to_stop_ratio,
    validate_stop_distance,
)


def approx(value: Decimal, expected: str, tol: str = "0.0001") -> bool:
    return abs(value - Decimal(expected)) <= Decimal(tol)


class TestPublishedRates:
    def test_vip0_rates_match_binance(self):
        # Corrects a common error: taker has been 0.05%, not 0.04%, since 2022.
        assert VIP0_MAKER == Decimal("0.0002")
        assert VIP0_TAKER == Decimal("0.0005")

    def test_bnb_discount_is_ten_percent(self):
        assert BNB_DISCOUNT == Decimal("0.10")

    def test_discounted_rates(self):
        fees = FeeModel.vip0(bnb_discount=True)
        assert fees.maker == Decimal("0.00018")
        assert fees.taker == Decimal("0.00045")


class TestRoundTripCost:
    @pytest.mark.parametrize(
        "entry_maker,exit_maker,expected",
        [
            (False, False, "0.00090"),  # taker in, taker out
            (True, False, "0.00063"),   # maker in, taker out
            (True, True, "0.00036"),    # maker in, maker out
        ],
    )
    def test_cost_by_execution_style(self, entry_maker, exit_maker, expected):
        fees = FeeModel.vip0(bnb_discount=True)
        assert fees.round_trip(entry_maker=entry_maker, exit_maker=exit_maker) == Decimal(
            expected
        )

    def test_realistic_mixed_cost(self):
        # Maker entry, maker TP on wins, taker stop on losses, 50% win rate.
        fees = FeeModel.vip0(bnb_discount=True)
        cost = fees.expected_round_trip(win_rate=Decimal("0.5"))
        assert approx(cost, "0.000495", "0.000001")

    def test_maker_execution_is_the_dominant_lever(self):
        fees = FeeModel.vip0(bnb_discount=True)
        all_taker = fees.round_trip(entry_maker=False, exit_maker=False)
        all_maker = fees.round_trip(entry_maker=True, exit_maker=True)
        # Going post-only on both sides removes ~60% of the bill.
        assert all_maker < all_taker * Decimal("0.5")


class TestBreakevenWinRate:
    def test_zero_cost_reduces_to_the_textbook_formula(self):
        # Gross break-even at 2R is 1/(1+R) = 33.3%.
        p = breakeven_win_rate(
            stop_fraction=Decimal("0.005"), reward_multiple=Decimal("2"), cost=Decimal("0")
        )
        assert approx(p, "0.3333")

    @pytest.mark.parametrize(
        "stop,reward,expected",
        [
            ("0.0015", "2", "0.5778"),  # 0.15% stop: needs 57.8%
            ("0.0030", "2", "0.4556"),  # 0.30% stop: needs 45.6%
            ("0.0050", "2", "0.4067"),  # 0.50% stop: needs 40.7%
            ("0.0030", "1", "0.6833"),  # symmetric 0.3% scalp: needs 68.3%
        ],
    )
    def test_net_breakeven_with_realistic_costs(self, stop, reward, expected):
        # C = 0.11% (all-taker plus slippage), the pessimistic case.
        p = breakeven_win_rate(
            stop_fraction=Decimal(stop),
            reward_multiple=Decimal(reward),
            cost=Decimal("0.0011"),
        )
        assert approx(p, expected)

    def test_tighter_stops_are_strictly_harder(self):
        costs = Decimal("0.0011")
        tight = breakeven_win_rate(Decimal("0.0015"), Decimal("2"), costs)
        wide = breakeven_win_rate(Decimal("0.0050"), Decimal("2"), costs)
        assert tight > wide

    def test_symmetric_risk_reward_on_a_tight_stop_is_not_achievable(self):
        # 68.3% net win rate. Nobody sustains that. This is why the strategy
        # spec forbids 1:1 on this timeframe.
        p = breakeven_win_rate(Decimal("0.0030"), Decimal("1"), Decimal("0.0011"))
        assert p > Decimal("0.65")


class TestCostToStopRatio:
    def test_ratio_is_cost_over_stop(self):
        assert cost_to_stop_ratio(Decimal("0.0011"), Decimal("0.0044")) == Decimal("0.25")

    def test_one_minute_atr_sized_stop_is_rejected(self):
        # A 1.2x ATR(1m) stop on BTC lands around 0.10%, giving C/S above 1.0:
        # the fee bill exceeds the entire stop distance.
        ratio = cost_to_stop_ratio(Decimal("0.0011"), Decimal("0.0010"))
        assert ratio > MAX_COST_TO_STOP_RATIO

    def test_five_minute_atr_sized_stop_passes(self):
        ratio = cost_to_stop_ratio(Decimal("0.0011"), Decimal("0.0050"))
        assert ratio <= MAX_COST_TO_STOP_RATIO


class TestStopDistanceFloor:
    def test_floor_is_forty_five_basis_points(self):
        assert MIN_STOP_FRACTION == Decimal("0.0045")

    def test_stop_below_the_floor_is_rejected(self):
        with pytest.raises(ValueError, match="stop distance"):
            validate_stop_distance(Decimal("0.0020"), cost=Decimal("0.0011"))

    def test_stop_at_the_floor_is_accepted(self):
        validate_stop_distance(Decimal("0.0045"), cost=Decimal("0.0011"))

    def test_stop_above_the_floor_but_with_bad_ratio_is_rejected(self):
        # Floor alone is not sufficient when costs are unusually high.
        with pytest.raises(ValueError, match="cost-to-stop"):
            validate_stop_distance(Decimal("0.0046"), cost=Decimal("0.0030"))


class TestBreakevenStopIsFeeAware:
    def test_long_breakeven_sits_above_entry(self):
        # Moving to raw entry guarantees a small loss on every scratched trade,
        # because the round trip has already been paid.
        be = breakeven_stop_price(
            entry=Decimal("100"), is_long=True, round_trip_fee=Decimal("0.00045")
        )
        assert be > Decimal("100")
        assert be == Decimal("100") * (1 + 2 * Decimal("0.00045"))

    def test_short_breakeven_sits_below_entry(self):
        be = breakeven_stop_price(
            entry=Decimal("100"), is_long=False, round_trip_fee=Decimal("0.00045")
        )
        assert be < Decimal("100")

    def test_breakeven_is_never_exactly_entry(self):
        for is_long in (True, False):
            be = breakeven_stop_price(Decimal("63520"), is_long, Decimal("0.00045"))
            assert be != Decimal("63520")
