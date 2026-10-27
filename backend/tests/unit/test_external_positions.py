"""Positions on the account that this engine does not manage.

An operator can run a second bot - a CLI one, a hand-placed trade, anything -
against the same Binance account. Reconciliation already finds those positions
and refuses to touch them, but it only ever reported them as a blocking alert:
a list of symbols and nothing else. The one screen that shows every position
was therefore the one place they were invisible.

They are shown read-only. This engine has no record of why they were opened
and no business closing them, so the view reports and does not offer to act.
"""

import pytest

from scalper.engine.reconciler import ExchangePosition, Reconciliation


def position(
    symbol: str,
    quantity: float,
    entry: float,
    mark: float | None = None,
    liq: float | None = None,
) -> ExchangePosition:
    return ExchangePosition(
        symbol=symbol,
        quantity=quantity,
        entry_price=entry,
        mark_price=mark,
        liquidation_price=liq,
    )


def report(*orphans: ExchangePosition) -> list[dict]:
    return Reconciliation(orphans=list(orphans)).external_positions()


class TestVisibility:
    def test_an_unmanaged_position_is_reported(self):
        rows = report(position("ETHUSDT", 0.093, 2678.17, mark=2685.82))
        assert [r["symbol"] for r in rows] == ["ETHUSDT"]

    def test_a_clean_account_reports_nothing(self):
        assert Reconciliation().external_positions() == []

    def test_every_unmanaged_position_is_listed(self):
        rows = report(
            position("ETHUSDT", 0.093, 2678.17),
            position("BTCUSDT", 0.001, 100_000.0),
            position("XRPUSDT", -50.0, 2.10),
        )
        assert {r["symbol"] for r in rows} == {"ETHUSDT", "BTCUSDT", "XRPUSDT"}

    def test_they_are_sorted_so_the_list_does_not_jump_between_refreshes(self):
        rows = report(
            position("XRPUSDT", 1, 1.0),
            position("BTCUSDT", 1, 1.0),
            position("ETHUSDT", 1, 1.0),
        )
        assert [r["symbol"] for r in rows] == ["BTCUSDT", "ETHUSDT", "XRPUSDT"]


class TestDirection:
    def test_a_positive_quantity_is_long(self):
        assert report(position("ETHUSDT", 0.093, 2678.17))[0]["side"] == "LONG"

    def test_a_negative_quantity_is_short(self):
        assert report(position("XRPUSDT", -50.0, 2.10))[0]["side"] == "SHORT"

    def test_the_quantity_is_reported_unsigned(self):
        # The side already carries the direction; a "-50" next to "SHORT"
        # reads as a double negative.
        assert report(position("XRPUSDT", -50.0, 2.10))[0]["quantity"] == 50.0


class TestUnrealised:
    def test_a_long_in_profit_shows_a_gain(self):
        row = report(position("ETHUSDT", 0.093, 2678.17, mark=2685.82))[0]
        assert row["unrealised"] == pytest.approx((2685.82 - 2678.17) * 0.093)

    def test_a_short_in_profit_shows_a_gain(self):
        # Price fell, so a short is up: the sign of the quantity carries it.
        row = report(position("XRPUSDT", -50.0, 2.10, mark=2.00))[0]
        assert row["unrealised"] == pytest.approx(5.0)

    def test_a_long_in_loss_shows_a_loss(self):
        row = report(position("BTCUSDT", 0.001, 100_000.0, mark=99_000.0))[0]
        assert row["unrealised"] == pytest.approx(-1.0)

    def test_no_mark_price_reports_no_pnl_rather_than_zero(self):
        # Zero would read as "flat"; the truth is that the venue did not say.
        row = report(position("ETHUSDT", 0.093, 2678.17))[0]
        assert row["unrealised"] is None
        assert row["mark_price"] is None


class TestTheyStayOutOfTheEnginesNumbers:
    def test_the_rows_carry_no_bot_identity(self):
        # Attaching one would imply this engine manages the position.
        row = report(position("ETHUSDT", 0.093, 2678.17))[0]
        assert "bot" not in row and "bot_id" not in row

    def test_a_liquidation_price_is_passed_through_when_the_venue_gives_one(self):
        row = report(position("ETHUSDT", 0.093, 2678.17, liq=1.08))[0]
        assert row["liquidation_price"] == 1.08

    def test_and_stays_absent_when_it_does_not(self):
        assert report(position("ETHUSDT", 0.093, 2678.17))[0]["liquidation_price"] is None
