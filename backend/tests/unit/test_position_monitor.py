"""Keeping our record honest against the exchange.

The dashboard showed two open positions at zero profit while the exchange had
both closed, one at a loss and one at a gain. The engine reconciled once at
startup and never looked again, so every number on screen was its own stale
memory rather than the account.

That is the worst failure mode this interface has. A stale number at full
contrast is indistinguishable from a live one, and an operator acts on it.
"""

from decimal import Decimal

import pytest

from scalper.engine.bots import ManagedPosition
from scalper.engine.monitor import PositionUpdate, classify_exit, diff_positions
from scalper.engine.reconciler import ExchangePosition


def held(symbol: str = "SOLUSDT", **overrides) -> ManagedPosition:
    base = dict(
        symbol=symbol,
        side="LONG",
        quantity=Decimal("2.36"),
        entry=Decimal("105.61"),
        stop=Decimal("105.13"),
        target=Decimal("106.57"),
        leverage=5,
        opened_at=1000.0,
        mark=Decimal("105.61"),
        stop_armed=True,
    )
    base.update(overrides)
    return ManagedPosition(**base)


def live(symbol: str = "SOLUSDT", qty: float = 2.36, mark: float = 106.0):
    return ExchangePosition(symbol=symbol, quantity=qty, entry_price=105.61)


class TestStillOpen:
    def test_a_position_the_exchange_still_holds_stays_open(self):
        result = diff_positions({"SOLUSDT": held()}, [live()])
        assert result.closed == []
        assert "SOLUSDT" in result.still_open

    def test_the_mark_price_is_refreshed(self):
        # Without this the profit column is frozen at the entry price forever.
        result = diff_positions({"SOLUSDT": held()}, [live()], marks={"SOLUSDT": 106.2})
        assert result.marks["SOLUSDT"] == pytest.approx(106.2)


class TestClosed:
    def test_a_position_the_exchange_no_longer_holds_is_closed(self):
        result = diff_positions({"SOLUSDT": held()}, [])
        assert [c.symbol for c in result.closed] == ["SOLUSDT"]

    def test_a_flat_row_counts_as_closed(self):
        # positionRisk lists every symbol; a zero quantity is not a position.
        result = diff_positions({"SOLUSDT": held()}, [live(qty=0.0)])
        assert [c.symbol for c in result.closed] == ["SOLUSDT"]

    def test_other_positions_are_unaffected_by_one_closing(self):
        known = {"SOLUSDT": held(), "ONEUSDT": held("ONEUSDT")}
        result = diff_positions(known, [live("ONEUSDT")])
        assert [c.symbol for c in result.closed] == ["SOLUSDT"]
        assert "ONEUSDT" in result.still_open

    def test_an_empty_account_closes_everything_we_thought_we_had(self):
        known = {"SOLUSDT": held(), "ONEUSDT": held("ONEUSDT")}
        result = diff_positions(known, [])
        assert len(result.closed) == 2


class TestExitClassification:
    def test_an_exit_at_the_target_is_a_take_profit(self):
        assert classify_exit(held(), Decimal("106.57")) == "tp"

    def test_an_exit_at_the_stop_is_a_stop_loss(self):
        assert classify_exit(held(), Decimal("105.13")) == "sl"

    def test_prices_between_levels_are_not_classified(self):
        assert classify_exit(held(), Decimal("106.40")) is None
        assert classify_exit(held(), Decimal("105.30")) is None

    def test_a_short_is_classified_the_same_way(self):
        short = held(
            side="SHORT",
            entry=Decimal("105.61"),
            stop=Decimal("106.09"),
            target=Decimal("104.65"),
        )
        assert classify_exit(short, Decimal("104.70")) is None
        assert classify_exit(short, Decimal("106.05")) is None

    def test_an_unknown_exit_price_is_not_guessed(self):
        # Reporting a take-profit that never happened inflates the hit rate an
        # operator uses to judge the strategy.
        assert classify_exit(held(), None) is None


class TestRealisedProfit:
    def test_a_winning_exit_is_positive(self):
        update = PositionUpdate.from_exit(held(), Decimal("106.57"))
        assert update.realised > 0

    def test_a_losing_exit_is_negative(self):
        update = PositionUpdate.from_exit(held(), Decimal("105.13"))
        assert update.realised < 0

    def test_profit_scales_with_size(self):
        small = PositionUpdate.from_exit(held(quantity=Decimal("1")), Decimal("106.57"))
        large = PositionUpdate.from_exit(held(quantity=Decimal("10")), Decimal("106.57"))
        assert large.realised > small.realised

    def test_a_short_profits_when_price_falls(self):
        short = held(
            side="SHORT",
            entry=Decimal("105.61"),
            stop=Decimal("106.09"),
            target=Decimal("104.65"),
        )
        assert PositionUpdate.from_exit(short, Decimal("104.65")).realised > 0

    def test_an_exit_at_entry_is_not_a_win(self):
        # It is a small loss once fees are paid, never a gain.
        update = PositionUpdate.from_exit(held(), Decimal("105.61"))
        assert update.realised <= 0
