"""Bots and the positions they hold.

A bot is one strategy configuration holding positions across several symbols.
That shape is what makes the fleet counts meaningful: running versus stopped
describes strategies, not individual trades.
"""

from decimal import Decimal

import pytest

from scalper.engine.bots import Bot, BotRegistry, ManagedPosition


def a_position(symbol: str = "SOLUSDT", **overrides) -> ManagedPosition:
    base = dict(
        symbol=symbol,
        side="LONG",
        quantity=Decimal("2.36"),
        entry=Decimal("105.52"),
        stop=Decimal("105.02"),
        target=Decimal("106.52"),
        leverage=5,
        opened_at=1000.0,
        mark=Decimal("105.52"),
    )
    base.update(overrides)
    return ManagedPosition(**base)


class TestRegistry:
    def test_a_created_bot_is_running(self):
        registry = BotRegistry()
        bot = registry.create(
            symbols=["SOLUSDT"], amount=Decimal("50"), leverage=5, strategy="s"
        )
        assert bot.state == "running"

    def test_each_bot_gets_a_distinct_identity(self):
        registry = BotRegistry()
        first = registry.create(symbols=["A"], amount=Decimal(1), leverage=1, strategy="s")
        second = registry.create(symbols=["B"], amount=Decimal(1), leverage=1, strategy="s")
        assert first.id != second.id
        assert first.name != second.name

    def test_stopping_changes_the_state_without_removing_the_bot(self):
        # Its history and hit counts are the record of what it did.
        registry = BotRegistry()
        bot = registry.create(symbols=["A"], amount=Decimal(1), leverage=1, strategy="s")
        registry.stop(bot.id)
        assert registry.bots[bot.id].state == "stopped"

    def test_stopping_an_unknown_bot_is_harmless(self):
        assert BotRegistry().stop("nope") is None


class TestSequence:
    def test_the_signal_sequence_never_repeats(self):
        # It feeds the client order id, which is what makes the exchange reject
        # a duplicate rather than opening a second position.
        bot = Bot(id="x", name="bot-1", strategy="s", symbols=[], amount=Decimal(1), leverage=1)
        assert len({bot.next_seq() for _ in range(20)}) == 20


class TestPositionView:
    def test_profit_is_computed_from_the_mark_price(self):
        position = a_position(mark=Decimal("106.00"))
        view = position.describe("bot-1")
        # 0.48 of movement on 2.36 units.
        assert view["unrealised_pnl"] == pytest.approx(1.1328, abs=1e-3)

    def test_a_short_profits_when_price_falls(self):
        position = a_position(
            side="SHORT",
            entry=Decimal("105.52"),
            stop=Decimal("106.02"),
            target=Decimal("104.52"),
            mark=Decimal("105.00"),
        )
        assert position.describe("bot-1")["unrealised_pnl"] > 0

    def test_return_on_equity_is_amplified_by_leverage(self):
        low = a_position(leverage=1, mark=Decimal("106.00")).describe("b")["roe_pct"]
        high = a_position(leverage=10, mark=Decimal("106.00")).describe("b")["roe_pct"]
        assert high > low

    def test_it_reports_whether_the_stop_is_armed(self):
        assert a_position(stop_armed=False).describe("b")["stop_armed"] is False

    def test_an_unset_mark_falls_back_to_entry_rather_than_zero(self):
        # Zero would render as a catastrophic loss on the dashboard.
        view = a_position(mark=None).describe("bot-1")
        assert view["mark"] == pytest.approx(105.52)
        assert view["unrealised_pnl"] == pytest.approx(0.0)


class TestAggregation:
    def _registry(self) -> BotRegistry:
        registry = BotRegistry()
        first = registry.create(
            symbols=["SOLUSDT"], amount=Decimal("50"), leverage=5, strategy="s"
        )
        first.positions["SOLUSDT"] = a_position(mark=Decimal("106.00"))
        second = registry.create(
            symbols=["ETHUSDT"], amount=Decimal("50"), leverage=5, strategy="s"
        )
        registry.stop(second.id)
        return registry

    def test_running_and_stopped_are_counted_separately(self):
        totals = self._registry().totals()
        assert totals["bots_running"] == 1
        assert totals["bots_stopped"] == 1

    def test_open_positions_are_counted_across_every_bot(self):
        assert self._registry().totals()["open_positions"] == 1

    def test_unrealised_profit_is_summed(self):
        assert float(self._registry().totals()["unrealised_pnl"]) > 0

    def test_positions_are_ordered_by_age_not_profit(self):
        # A table that re-sorts as prices move puts the row somebody is
        # reaching for somewhere else by the time they click.
        registry = BotRegistry()
        bot = registry.create(
            symbols=["A", "B"], amount=Decimal("50"), leverage=5, strategy="s"
        )
        bot.positions["A"] = a_position("A", opened_at=200.0, mark=Decimal("999"))
        bot.positions["B"] = a_position("B", opened_at=100.0)
        assert [p["symbol"] for p in registry.positions()] == ["B", "A"]

    def test_an_empty_registry_totals_to_zero(self):
        totals = BotRegistry().totals()
        assert totals["open_positions"] == 0
        assert totals["bots_running"] == 0
