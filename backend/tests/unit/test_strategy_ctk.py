"""CTK 1 and CTK 5 - the CTK PO3 EA, ported.

Power of Three: a range compresses (accumulation), price leaves the box, then
a further push beyond it is FADED. Getting the fade backwards would produce a
breakout strategy that looks superficially similar and does the opposite, so
both directions are asserted explicitly.

The reward is deliberately smaller than the risk - 0.86, the source value.
Tests pin that it is NOT quietly raised to the platform default of 1.5, which
would be a different strategy sharing this entry.
"""

from decimal import Decimal

import pytest

from scalper.exchange.market_data import BookTop, Candle, Ticker
from scalper.strategies.base import StrategyContext
from scalper.strategies.ctk import (
    ACCUMULATION_BARS,
    ATR_LENGTH,
    MANIPULATION_MULT,
    RISK_MULT,
    SOURCE_REWARD_RISK,
    Ctk1,
    Ctk5,
)

MINUTE = 60_000
FIVE = 300_000


def candle(high, low, close, index, interval, volume=1000.0):
    open_time = index * interval
    return Candle(
        open_time=open_time,
        open=(high + low) / 2,
        high=high,
        low=low,
        close=close,
        volume=volume,
        close_time=open_time + interval - 1,
        quote_volume=close * volume,
        taker_buy_base=volume * 0.55,
    )


def po3(interval=MINUTE, manipulate="below", quiet=140):
    """Accumulation, then a push beyond the box that should be faded.

    The quiet section is wide enough to seed ATR(50) and fill the 40-bar
    accumulation window.
    """
    bars = [candle(100.6, 100.4, 100.5, i, interval) for i in range(quiet)]
    if manipulate == "below":
        bars.append(candle(100.6, 99.0, 100.3, quiet, interval, volume=3000.0))
    elif manipulate == "above":
        bars.append(candle(102.0, 100.4, 100.7, quiet, interval, volume=3000.0))
    else:
        bars.append(candle(100.6, 100.4, 100.5, quiet, interval))
    return bars


def context(bars_1m, bars_5m=None):
    if bars_5m is None:
        bars_5m = [candle(100.6, 100.4, 100.5, i, FIVE) for i in range(140)]
    last = bars_1m[-1].close
    return StrategyContext(
        symbol="TESTUSDT",
        candles_5m=bars_5m,
        candles_1m=bars_1m,
        ticker=Ticker("TESTUSDT", last, 1.0, 500_000_000.0),
        book=BookTop("TESTUSDT", last - 0.01, last + 0.01),
    )


ONE = Ctk1()
FIVE_MIN = Ctk5()


class TestTheFadeDirection:
    def test_a_push_below_the_box_is_bought(self):
        setup = ONE.assess(context(po3(manipulate="below")))
        assert setup.ready is True, setup.note
        assert setup.direction == "LONG"

    def test_a_push_above_the_box_is_sold(self):
        # The half that catches an inverted sign. A breakout strategy would
        # buy here; PO3 fades it.
        setup = ONE.assess(context(po3(manipulate="above")))
        assert setup.ready is True, setup.note
        assert setup.direction == "SHORT"

    def test_a_quiet_bar_produces_nothing(self):
        assert ONE.assess(context(po3(manipulate="none"))).ready is False

    def test_the_note_names_the_box_and_the_fade(self):
        note = ONE.assess(context(po3())).note.lower()
        assert "po3" in note and "box" in note


class TestTheManipulationThreshold:
    def test_leaving_the_box_is_not_enough_on_its_own(self):
        # Between the box edge and the manipulation level there is no trade.
        # Without this the strategy would fire on every break.
        bars = po3(manipulate="none")
        bars[-1] = candle(100.6, 100.25, 100.4, len(bars) - 1, MINUTE, volume=3000.0)
        assert ONE.assess(context(bars)).ready is False

    def test_the_signal_must_land_on_the_last_closed_bar(self):
        # The EA returns only when i == count-1. An older manipulation is
        # replayed to keep state consistent and then discarded.
        bars = po3()
        bars.append(candle(100.6, 100.4, 100.5, len(bars), MINUTE))
        assert ONE.assess(context(bars)).ready is False


class TestTheSourceRatioSurvives:
    def _entry_levels(self, bars, direction="LONG"):
        ctx = context(bars)
        entry = Decimal(str(ctx.price))
        return entry, ONE.levels(ctx, entry, direction)

    def test_the_target_is_nearer_than_the_stop(self):
        # 0.86R. The defining property of this strategy and the thing most
        # likely to be "fixed" by someone applying the platform default.
        entry, levels = self._entry_levels(po3())
        assert levels.target - entry < entry - levels.stop

    def test_the_ratio_is_the_source_value(self):
        entry, levels = self._entry_levels(po3())
        risk = entry - levels.stop
        assert float((levels.target - entry) / risk) == pytest.approx(
            float(SOURCE_REWARD_RISK), abs=1e-9
        )

    def test_the_strategy_declares_its_own_minimum(self):
        # Without this the platform 1.5 floor refuses every CTK trade and the
        # strategy can never open a position.
        assert ONE.min_reward_to_risk == SOURCE_REWARD_RISK
        assert FIVE_MIN.min_reward_to_risk == SOURCE_REWARD_RISK

    def test_a_plan_at_the_source_ratio_is_accepted(self):
        from scalper.core.filters import SymbolFilters
        from scalper.engine.plan import PlanRejected, TradePlan

        filters = SymbolFilters(
            symbol="TESTUSDT",
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.001"),
            min_qty=Decimal("0.001"),
            min_notional=Decimal("5"),
        )
        plan = TradePlan(
            bot_id="b1",
            signal_seq=1,
            symbol="TESTUSDT",
            side="LONG",
            quantity=Decimal("1"),
            entry=Decimal("100.00"),
            stop=Decimal("99.00"),
            target=Decimal("100.86"),
            leverage=5,
        )
        plan.validate(filters, min_reward_to_risk=SOURCE_REWARD_RISK)

        # The same plan under the platform default, so the rejection can only
        # be about the ratio - a bare `raises(Exception)` here would pass on a
        # tick-size or leverage complaint and prove nothing.
        with pytest.raises(PlanRejected, match="reward to risk"):
            plan.validate(filters)


class TestLevels:
    def test_the_long_bracket_straddles_the_entry(self):
        ctx = context(po3(manipulate="below"))
        entry = Decimal(str(ctx.price))
        levels = ONE.levels(ctx, entry, "LONG")
        assert levels.stop < entry < levels.target

    def test_the_short_bracket_is_mirrored(self):
        ctx = context(po3(manipulate="above"))
        entry = Decimal(str(ctx.price))
        levels = ONE.levels(ctx, entry, "SHORT")
        assert levels.target < entry < levels.stop

    def test_the_stop_clears_the_fee_floor(self):
        from scalper.core.fees import MIN_STOP_FRACTION

        ctx = context(po3(manipulate="below"))
        entry = Decimal(str(ctx.price))
        levels = ONE.levels(ctx, entry, "LONG")
        assert (entry - levels.stop) / entry >= MIN_STOP_FRACTION

    def test_the_opposite_direction_is_refused(self):
        ctx = context(po3(manipulate="below"))
        with pytest.raises(ValueError):
            ONE.levels(ctx, Decimal(str(ctx.price)), "SHORT")

    def test_rounding_never_flatters_the_ratio(self):
        # Stop away from entry, target toward it. Rounding may widen the risk
        # or shorten the reward, never the reverse.
        ctx = context(po3(manipulate="below"))
        entry = Decimal(str(ctx.price))
        raw = ONE.levels(ctx, entry, "LONG")
        rounded = ONE.execution_levels(ctx, entry, "LONG", Decimal("0.01"))
        assert rounded.stop <= raw.stop
        assert rounded.target <= raw.target


class TestTheFiveMinuteVariant:
    def test_it_reads_the_five_minute_series(self):
        flat = [candle(100.6, 100.4, 100.5, i, MINUTE) for i in range(140)]
        setup = FIVE_MIN.assess(context(flat, po3(interval=FIVE)))
        assert setup.ready is True, setup.note
        assert setup.direction == "LONG"

    def test_a_one_minute_signal_does_not_move_it(self):
        flat5 = [candle(100.6, 100.4, 100.5, i, FIVE) for i in range(140)]
        assert FIVE_MIN.assess(context(po3(), flat5)).ready is False


class TestDataIntegrity:
    def test_a_discontinuous_series_is_refused(self):
        bars = po3()
        del bars[-5]
        assert ONE.assess(context(bars)).ready is False

    def test_insufficient_history_waits(self):
        short = [candle(100.6, 100.4, 100.5, i, MINUTE) for i in range(30)]
        setup = ONE.assess(context(short))
        assert setup.ready is False
        assert "history" in setup.note.lower()


class TestRegistration:
    def test_both_are_registered(self):
        from scalper.strategies import STRATEGIES

        assert {"ctk-1", "ctk-5"} <= set(STRATEGIES.ids())

    def test_they_keep_the_operator_names(self):
        assert ONE.name == "CTK 1"
        assert FIVE_MIN.name == "CTK 5"

    def test_the_ea_constants_are_carried_over(self):
        assert (ATR_LENGTH, ACCUMULATION_BARS, MANIPULATION_MULT, RISK_MULT) == (
            50,
            40,
            0.6,
            5.0,
        )

    def test_the_win_rate_the_ratio_requires_is_stated(self):
        # The honest translation of "it works perfectly on gold": the same
        # trade needs a materially higher win rate on percentage fees.
        joined = " ".join(ONE.assumptions)
        assert "63%" in joined and "0.090%" in joined

    def test_the_stuck_state_machine_is_recorded(self):
        joined = " ".join(ONE.assumptions).lower()
        assert "no path" in joined
