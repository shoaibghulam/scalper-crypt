"""Exchange filter rounding.

Rejected orders are the most common way a new Binance bot fails, and almost
always for the same three reasons: a quantity that is not a multiple of
stepSize, a price that is not a multiple of tickSize, or a notional below the
effective minimum. A fourth, subtler one: serialising a small Decimal through
float produces scientific notation such as 1e-08, which Binance rejects.
"""

from decimal import Decimal

import pytest

from scalper.core.filters import (
    Rounding,
    SymbolFilters,
    effective_min_notional,
    format_quantity,
    round_qty_down,
    round_to_tick,
)

BTCUSDT = SymbolFilters(
    symbol="BTCUSDT",
    tick_size=Decimal("0.10"),
    step_size=Decimal("0.001"),
    min_qty=Decimal("0.001"),
    min_notional=Decimal("100"),
)

DOGEUSDT = SymbolFilters(
    symbol="DOGEUSDT",
    tick_size=Decimal("0.0000100"),
    step_size=Decimal("1"),
    min_qty=Decimal("1"),
    min_notional=Decimal("5"),
)


class TestQuantityRounding:
    def test_rounds_down_to_step_size(self):
        assert round_qty_down(Decimal("0.123456"), BTCUSDT.step_size) == Decimal("0.123")

    def test_never_rounds_up(self):
        assert round_qty_down(Decimal("0.1239"), BTCUSDT.step_size) == Decimal("0.123")

    def test_exact_multiple_is_unchanged(self):
        assert round_qty_down(Decimal("0.123"), BTCUSDT.step_size) == Decimal("0.123")

    def test_integer_step_size(self):
        assert round_qty_down(Decimal("1234.9"), DOGEUSDT.step_size) == Decimal("1234")

    def test_below_one_step_becomes_zero(self):
        assert round_qty_down(Decimal("0.0009"), BTCUSDT.step_size) == Decimal("0")


class TestPriceRounding:
    def test_rounds_down_for_buy_limit(self):
        got = round_to_tick(Decimal("63521.37"), BTCUSDT.tick_size, Rounding.DOWN)
        assert got == Decimal("63521.30")

    def test_rounds_up_for_sell_limit(self):
        got = round_to_tick(Decimal("63521.31"), BTCUSDT.tick_size, Rounding.UP)
        assert got == Decimal("63521.40")

    def test_exact_tick_is_unchanged_in_both_directions(self):
        for mode in (Rounding.UP, Rounding.DOWN):
            got = round_to_tick(Decimal("63521.40"), BTCUSDT.tick_size, mode)
            assert got == Decimal("63521.40")

    def test_tiny_tick_size(self):
        got = round_to_tick(Decimal("0.0813270"), DOGEUSDT.tick_size, Rounding.DOWN)
        assert got == Decimal("0.081320")


class TestSerialisation:
    def test_small_quantity_never_uses_scientific_notation(self):
        assert format_quantity(Decimal("1E-8")) == "0.00000001"

    def test_no_exponent_marker_survives(self):
        assert "e" not in format_quantity(Decimal("0.001000")).lower()

    def test_normal_values_round_trip(self):
        assert format_quantity(Decimal("0.123")) == "0.123"

    def test_large_value(self):
        assert format_quantity(Decimal("123456.7")) == "123456.7"


class TestEffectiveMinNotional:
    def test_uses_the_larger_of_min_notional_and_min_qty_value(self):
        assert effective_min_notional(BTCUSDT, Decimal("150000")) == Decimal("150")

    def test_min_notional_binds_when_larger(self):
        assert effective_min_notional(BTCUSDT, Decimal("50000")) == Decimal("100")

    def test_cheap_symbol(self):
        assert effective_min_notional(DOGEUSDT, Decimal("0.08")) == Decimal("5")


class TestFilterValidation:
    def test_accepts_a_valid_order(self):
        BTCUSDT.validate(qty=Decimal("0.010"), price=Decimal("63521.30"))

    def test_rejects_quantity_below_min_qty(self):
        with pytest.raises(ValueError, match="minQty"):
            BTCUSDT.validate(qty=Decimal("0.0005"), price=Decimal("63521.30"))

    def test_rejects_quantity_off_step(self):
        with pytest.raises(ValueError, match="stepSize"):
            BTCUSDT.validate(qty=Decimal("0.0105"), price=Decimal("63521.30"))

    def test_rejects_price_off_tick(self):
        with pytest.raises(ValueError, match="tickSize"):
            BTCUSDT.validate(qty=Decimal("0.010"), price=Decimal("63521.37"))

    def test_rejects_notional_below_minimum(self):
        with pytest.raises(ValueError, match="notional"):
            BTCUSDT.validate(qty=Decimal("0.001"), price=Decimal("50000"))
