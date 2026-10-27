"""Position sizing and the leverage cap.

Leverage is not a risk knob. Risking 0.5% of equity at 10x is identical to
risking 0.5% at 1x when the stop distance is the same; leverage only changes
how much collateral is posted. What leverage does change is the distance to
liquidation, and that is the real constraint: if the liquidation price sits
inside the stop, the stop can never fire, because the exchange closes the
position first.
"""

from decimal import Decimal

import pytest

from scalper.core.filters import SymbolFilters
from scalper.risk.sizing import (
    LEVERAGE_HARD_CAP,
    SAFE_LIQUIDATION_STOP_MULTIPLE,
    SizingRejected,
    is_liquidation_inside_stop,
    liquidation_distance_fraction,
    liquidation_headroom,
    max_allowed_leverage,
    recommended_max_leverage,
    size_position,
)

BTCUSDT = SymbolFilters(
    symbol="BTCUSDT",
    tick_size=Decimal("0.10"),
    step_size=Decimal("0.001"),
    min_qty=Decimal("0.001"),
    min_notional=Decimal("100"),
)

GENERIC = SymbolFilters(
    symbol="TESTUSDT",
    tick_size=Decimal("0.01"),
    step_size=Decimal("0.001"),
    min_qty=Decimal("0.001"),
    min_notional=Decimal("5"),
)


class TestPositionSizing:
    def test_quantity_follows_from_risk_and_stop_distance(self):
        result = size_position(
            equity=Decimal("1000"),
            risk_pct=Decimal("0.005"),
            entry=Decimal("100"),
            stop=Decimal("99.5"),
            leverage=10,
            filters=GENERIC,
            available_balance=Decimal("1000"),
        )
        assert result.qty == Decimal("10")
        assert result.notional == Decimal("1000")
        assert result.margin == Decimal("100")
        assert result.risk_amount == Decimal("5")

    def test_wider_stop_means_smaller_position(self):
        base = dict(
            equity=Decimal("1000"),
            risk_pct=Decimal("0.005"),
            entry=Decimal("100"),
            leverage=10,
            filters=GENERIC,
            available_balance=Decimal("1000"),
        )
        tight = size_position(stop=Decimal("99.5"), **base)
        wide = size_position(stop=Decimal("99.0"), **base)
        assert wide.qty < tight.qty

    def test_risk_amount_is_identical_regardless_of_leverage(self):
        base = dict(
            equity=Decimal("1000"),
            risk_pct=Decimal("0.005"),
            entry=Decimal("100"),
            stop=Decimal("99.5"),
            filters=GENERIC,
            available_balance=Decimal("1000"),
        )
        low = size_position(leverage=2, **base)
        high = size_position(leverage=10, **base)
        assert low.qty == high.qty
        assert low.risk_amount == high.risk_amount
        assert low.margin > high.margin

    def test_short_position_sizes_the_same(self):
        result = size_position(
            equity=Decimal("1000"),
            risk_pct=Decimal("0.005"),
            entry=Decimal("100"),
            stop=Decimal("100.5"),
            leverage=10,
            filters=GENERIC,
            available_balance=Decimal("1000"),
        )
        assert result.qty == Decimal("10")

    def test_quantity_is_rounded_down_to_step_size(self):
        result = size_position(
            equity=Decimal("1000"),
            risk_pct=Decimal("0.005"),
            entry=Decimal("63520"),
            stop=Decimal("63200"),
            leverage=10,
            filters=BTCUSDT,
            available_balance=Decimal("1000"),
        )
        assert result.qty % BTCUSDT.step_size == 0

    def test_rejects_when_margin_exceeds_the_balance_buffer(self):
        with pytest.raises(SizingRejected, match="margin"):
            size_position(
                equity=Decimal("1000"),
                risk_pct=Decimal("0.005"),
                entry=Decimal("100"),
                stop=Decimal("99.9"),
                leverage=1,
                filters=GENERIC,
                available_balance=Decimal("100"),
            )

    def test_rejects_when_notional_below_exchange_minimum(self):
        with pytest.raises(SizingRejected, match="notional"):
            size_position(
                equity=Decimal("20"),
                risk_pct=Decimal("0.005"),
                entry=Decimal("63520"),
                stop=Decimal("63200"),
                leverage=10,
                filters=BTCUSDT,
                available_balance=Decimal("20"),
            )

    def test_rejects_zero_stop_distance(self):
        with pytest.raises(SizingRejected, match="stop"):
            size_position(
                equity=Decimal("1000"),
                risk_pct=Decimal("0.005"),
                entry=Decimal("100"),
                stop=Decimal("100"),
                leverage=10,
                filters=BTCUSDT,
                available_balance=Decimal("1000"),
            )


class TestLiquidationDistance:
    def test_distance_shrinks_as_leverage_rises(self):
        mmr = Decimal("0.005")
        assert liquidation_distance_fraction(5, mmr) > liquidation_distance_fraction(20, mmr)

    def test_ten_x_is_comfortably_outside_a_typical_stop(self):
        d = liquidation_distance_fraction(10, Decimal("0.005"))
        assert d == Decimal("0.095")
        assert d > Decimal("0.0045") * SAFE_LIQUIDATION_STOP_MULTIPLE

    def test_fifty_x_is_past_the_comfortable_multiple(self):
        d = liquidation_distance_fraction(50, Decimal("0.005"))
        assert d < Decimal("0.0045") * SAFE_LIQUIDATION_STOP_MULTIPLE

    def test_liquidation_inside_stop_is_detected(self):
        assert is_liquidation_inside_stop(
            leverage=125, mmr=Decimal("0.005"), stop_fraction=Decimal("0.0045")
        )

    def test_liquidation_outside_stop_at_sane_leverage(self):
        assert not is_liquidation_inside_stop(
            leverage=10, mmr=Decimal("0.005"), stop_fraction=Decimal("0.0045")
        )


class TestHeadroom:
    """How much further away liquidation sits than the stop, as a multiple.

    One times means they coincide and the stop protects nothing. The number is
    displayed rather than only tested against, because at high leverage it is
    the single figure that says how much room a wick has to work with.
    """

    def test_headroom_of_one_means_the_levels_coincide(self):
        # Liquidation at 0.6 percent, stop at 0.6 percent.
        h = liquidation_headroom(
            leverage=100, mmr=Decimal("0.004"), stop_fraction=Decimal("0.006")
        )
        assert h == Decimal(1)

    def test_a_hundred_x_on_the_tightest_permitted_stop_is_thin(self):
        # The real figure behind the warning: liquidation 0.60 percent away, a
        # stop at 0.45 percent, so only a third of the stop width in reserve.
        h = liquidation_headroom(
            leverage=100, mmr=Decimal("0.004"), stop_fraction=Decimal("0.0045")
        )
        assert Decimal("1.3") < h < Decimal("1.4")

    def test_headroom_is_zero_when_liquidation_is_at_or_before_entry(self):
        # Degenerate input rather than a tradeable state, but it must not raise
        # or return a negative that would compare as safe against a threshold.
        h = liquidation_headroom(
            leverage=500, mmr=Decimal("0.004"), stop_fraction=Decimal("0.0045")
        )
        assert h == Decimal(0)


class TestLeverageCap:
    def test_the_operator_may_choose_up_to_a_hundred(self):
        assert LEVERAGE_HARD_CAP == 100

    def test_recommended_leverage_keeps_the_comfortable_headroom(self):
        for stop in ("0.0045", "0.01", "0.02", "0.05"):
            s = Decimal(stop)
            lev = recommended_max_leverage(s, Decimal("0.005"))
            distance = liquidation_distance_fraction(lev, Decimal("0.005"))
            assert distance >= s * SAFE_LIQUIDATION_STOP_MULTIPLE

    def test_recommendation_is_far_below_the_hard_cap_on_a_tight_stop(self):
        # 43x on a 0.45 percent stop. The cap exists so the operator can go
        # past this deliberately, not so the recommendation rises to meet it.
        assert recommended_max_leverage(Decimal("0.0045"), Decimal("0.005")) < 50

    def test_wide_stop_reduces_the_recommendation(self):
        assert recommended_max_leverage(Decimal("0.05"), Decimal("0.01")) < 5

    def test_the_allowed_maximum_is_higher_than_the_recommendation(self):
        stop = Decimal("0.0045")
        mmr = Decimal("0.004")
        assert max_allowed_leverage(stop, mmr) > recommended_max_leverage(stop, mmr)

    def test_the_allowed_maximum_still_leaves_the_stop_able_to_fire(self):
        for stop in ("0.0045", "0.01", "0.05"):
            s = Decimal(stop)
            lev = max_allowed_leverage(s, Decimal("0.004"))
            assert not is_liquidation_inside_stop(
                leverage=lev, mmr=Decimal("0.004"), stop_fraction=s
            )

    def test_a_hundred_x_is_reachable_on_the_tightest_permitted_stop(self):
        # Requested explicitly. It clears the hard floor by a small margin and
        # nothing else, which is why the recommendation stays where it is.
        assert max_allowed_leverage(Decimal("0.0045"), Decimal("0.004")) == 100

    def test_neither_limit_returns_less_than_one(self):
        assert recommended_max_leverage(Decimal("0.5"), Decimal("0.01")) >= 1
        assert max_allowed_leverage(Decimal("0.5"), Decimal("0.01")) >= 1
