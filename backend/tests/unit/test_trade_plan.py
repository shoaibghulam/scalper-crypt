"""The trade plan.

Everything about an intended trade, decided before anything is sent, so the
checks happen where they can still cancel the trade rather than half way
through placing it.

The client order id is the piece that matters most. It is derived, never
random, so that a retry after an ambiguous timeout is rejected by Binance as a
duplicate rather than opening a second position. That makes the exchange itself
the last line of defence behind our own guards.
"""

import re
from decimal import Decimal

import pytest

from scalper.core.filters import SymbolFilters
from scalper.engine.plan import PlanRejected, TradePlan

BTCUSDT = SymbolFilters(
    symbol="BTCUSDT",
    tick_size=Decimal("0.10"),
    step_size=Decimal("0.001"),
    min_qty=Decimal("0.001"),
    min_notional=Decimal("100"),
)


def plan(**overrides) -> TradePlan:
    base = dict(
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
    base.update(overrides)
    return TradePlan(**base)


class TestClientOrderIds:
    def test_the_id_is_derived_not_random(self):
        assert plan().client_order_id("entry") == plan().client_order_id("entry")

    def test_it_identifies_the_bot_the_signal_and_the_intent(self):
        assert plan().client_order_id("entry") == "alpha-7-entry"

    def test_each_leg_gets_its_own_id(self):
        made = {plan().client_order_id(i) for i in ("entry", "sl", "tp")}
        assert len(made) == 3

    def test_a_later_signal_produces_a_different_id(self):
        assert plan(signal_seq=8).client_order_id("entry") != plan().client_order_id("entry")

    def test_the_id_fits_the_exchange_format(self):
        # Binance accepts up to 36 characters from a restricted alphabet.
        value = plan(bot_id="a-very-long-bot-name-here").client_order_id("entry")
        assert len(value) <= 36
        assert all(c.isalnum() or c in "-_.:/" for c in value)


class TestDirection:
    def test_a_long_stop_sits_below_entry(self):
        with pytest.raises(PlanRejected, match="stop"):
            plan(side="LONG", stop=Decimal("64000.00")).validate(BTCUSDT)

    def test_a_long_target_sits_above_entry(self):
        with pytest.raises(PlanRejected, match="target"):
            plan(side="LONG", target=Decimal("63000.00")).validate(BTCUSDT)

    def test_a_short_stop_sits_above_entry(self):
        with pytest.raises(PlanRejected, match="stop"):
            plan(
                side="SHORT",
                stop=Decimal("63000.00"),
                target=Decimal("62000.00"),
            ).validate(BTCUSDT)

    def test_a_valid_short_passes(self):
        plan(
            side="SHORT",
            stop=Decimal("63900.00"),
            target=Decimal("62800.00"),
        ).validate(BTCUSDT)


class TestEconomics:
    def test_a_stop_too_tight_to_clear_its_fees_is_refused(self):
        # 0.1 percent away. The round trip alone is most of that.
        with pytest.raises(PlanRejected, match="stop distance"):
            plan(stop=Decimal("63456.00")).validate(BTCUSDT)

    def test_a_symmetric_risk_reward_is_refused(self):
        # Needs a 68 percent net win rate at this timeframe. Nobody has that.
        with pytest.raises(PlanRejected, match="reward"):
            plan(target=Decimal("63860.00")).validate(BTCUSDT)

    def test_a_sound_plan_passes_every_check(self):
        plan().validate(BTCUSDT)


class TestExchangeFilters:
    def test_a_price_off_tick_is_refused_before_sending(self):
        with pytest.raises(PlanRejected):
            plan(entry=Decimal("63520.37")).validate(BTCUSDT)

    def test_a_quantity_below_the_minimum_is_refused(self):
        with pytest.raises(PlanRejected):
            plan(quantity=Decimal("0.0001")).validate(BTCUSDT)

    def test_a_notional_below_the_minimum_is_refused(self):
        with pytest.raises(PlanRejected, match="notional"):
            plan(
                quantity=Decimal("0.001"),
                entry=Decimal("50000.00"),
                stop=Decimal("49700.00"),
                target=Decimal("50700.00"),
            ).validate(BTCUSDT)


class TestSides:
    def test_a_long_entry_buys(self):
        assert plan(side="LONG").entry_side == "BUY"

    def test_a_long_exit_sells(self):
        assert plan(side="LONG").exit_side == "SELL"

    def test_a_short_entry_sells(self):
        assert plan(side="SHORT").entry_side == "SELL"

    def test_a_short_exit_buys(self):
        assert plan(side="SHORT").exit_side == "BUY"


class TestMeasures:
    def test_it_reports_the_stop_distance_as_a_fraction(self):
        assert float(plan().stop_fraction) == pytest.approx(0.00535, abs=1e-4)

    def test_it_reports_the_reward_to_risk(self):
        assert float(plan().reward_to_risk) == pytest.approx(1.94, abs=0.01)

    def test_it_reports_the_notional(self):
        assert plan().notional == Decimal("635.2000")


class TestLeverageAgainstLiquidation:
    """Raising the ceiling to 100x makes this check load-bearing.

    It existed in the sizing module but nothing called it, so leverage was
    never actually validated against the stop. At 10x that was harmless
    because the headroom was 21 stop-widths. At 100x it is 1.33, and a stop
    that cannot fire first is not a stop.
    """

    def _plan(self, leverage: int, stop: str = "995.5"):
        return TradePlan(
            bot_id="b1",
            signal_seq=1,
            symbol="BTCUSDT",
            side="LONG",
            quantity=Decimal("1"),
            entry=Decimal("1000"),
            stop=Decimal(stop),
            target=Decimal("1013.5"),
            leverage=leverage,
        )

    def test_a_hundred_x_on_a_tight_stop_is_allowed(self):
        # Requested explicitly, and it does clear the floor: liquidation at
        # 0.60 percent against a 0.45 percent stop.
        self._plan(100).validate(BTCUSDT, mmr=Decimal("0.004"))

    def test_leverage_past_the_floor_is_refused(self):
        with pytest.raises(PlanRejected, match="liquidation"):
            self._plan(100, stop="993.0").validate(BTCUSDT, mmr=Decimal("0.004"))

    def test_a_wide_stop_refuses_leverage_that_a_tight_one_permits(self):
        # 5 percent stop at 100x: liquidation arrives long before the stop.
        with pytest.raises(PlanRejected, match="liquidation"):
            self._plan(100, stop="950.0").validate(BTCUSDT, mmr=Decimal("0.004"))

    def test_the_message_reports_the_headroom_the_operator_has(self):
        with pytest.raises(PlanRejected) as caught:
            self._plan(100, stop="993.0").validate(BTCUSDT, mmr=Decimal("0.004"))
        assert "0.70%" in str(caught.value) or "headroom" in str(caught.value)

    def test_modest_leverage_is_untouched(self):
        self._plan(10).validate(BTCUSDT, mmr=Decimal("0.004"))


class TestClientOrderIdCharacters:
    """Regression: a bot name with spaces made every order illegal.

    Binance accepts client order ids matching ^[.A-Z:/a-z0-9_-]{1,36}$ and
    rejects anything else with -1100. Bots used to be called bot-1 and bot-2,
    which happened to be legal. Giving them readable names like
    "VWAP Band Reversion 5" put spaces into the id, and three live bots failed
    on the exchange with "Illegal characters found in parameter
    newclientorderid" before placing a single order.

    The id is still derived from the bot rather than generated, because the
    exchange rejecting a duplicate is the last line of defence against a retry
    opening a second position. Sanitising preserves that.
    """

    LEGAL = re.compile(r"^[.A-Z:/a-z0-9_-]{1,36}$")

    def _plan(self, bot_id: str) -> TradePlan:
        return TradePlan(
            bot_id=bot_id,
            signal_seq=7,
            symbol="BTCUSDT",
            side="LONG",
            quantity=Decimal("1"),
            entry=Decimal("1000"),
            stop=Decimal("995.5"),
            target=Decimal("1013.5"),
            leverage=10,
        )

    def test_a_name_with_spaces_yields_a_legal_id(self):
        got = self._plan("VWAP Band Reversion 5").client_order_id("entry")
        assert self.LEGAL.match(got), got

    def test_unicode_in_a_name_yields_a_legal_id(self):
        # Symbol and bot names have arrived with non-ASCII characters before.
        got = self._plan("Legende \u00fcber alles \u725b").client_order_id("sl")
        assert self.LEGAL.match(got), got

    def test_punctuation_is_replaced_rather_than_dropped(self):
        # Dropping would let "A B" and "AB" collide on the same sequence.
        first = self._plan("A B").client_order_id("entry")
        second = self._plan("AB").client_order_id("entry")
        assert first != second

    def test_the_sequence_and_intent_survive(self):
        # They are what make the id unique across legs; losing them to
        # truncation would let the stop and the target collide.
        got = self._plan("x" * 200).client_order_id("tp")
        assert got.endswith("-7-tp")

    def test_the_id_stays_within_the_exchange_limit(self):
        got = self._plan("Very Long Readable Bot Name " * 4).client_order_id("entry")
        assert len(got) <= 36

    def test_a_name_that_sanitises_to_nothing_still_produces_an_id(self):
        # An id of just "-7-entry" would still be legal, but a bot with no
        # identifying prefix makes two bots collide on the same sequence.
        got = self._plan("\u725b\u6765").client_order_id("entry")
        assert self.LEGAL.match(got), got
        assert len(got) > len("-7-entry")

    def test_the_same_bot_and_sequence_are_still_deterministic(self):
        # The whole point: a retry after an ambiguous timeout must present the
        # same id so the exchange refuses it rather than opening a second
        # position.
        assert self._plan("Scalp A").client_order_id("entry") == self._plan(
            "Scalp A"
        ).client_order_id("entry")
