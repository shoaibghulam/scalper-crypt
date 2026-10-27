"""Minute Black video rules plus explicitly separate engineering safeguards.

Narration at 13:27-13:49 defines 'high volume' as candle height compared with
at least one of the previous two candles. Real traded-volume filtering is an
additional platform policy. See strategy-review-2026-09-18/README.md.
"""

from decimal import Decimal

import pytest

from scalper.exchange.market_data import BookTop, Candle, Ticker
from scalper.strategies.base import StrategyContext
from scalper.strategies.black import MinuteBlack


def candle(
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 1000.0,
) -> Candle:
    return Candle(
        open_time=0,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        close_time=0,
        quote_volume=close * volume,
        taker_buy_base=volume * 0.55,
    )


def context(candles_1m: list[Candle], candles_5m: list[Candle] | None = None):
    series_5m = (
        candles_5m
        if candles_5m is not None
        else [candle(100.0, 100.8, 99.2, 100.0) for _ in range(40)]
    )
    last = candles_1m[-1].close
    return StrategyContext(
        symbol="TESTUSDT",
        candles_5m=series_5m,
        candles_1m=candles_1m,
        ticker=Ticker("TESTUSDT", last, 1.0, 500_000_000.0),
        book=BookTop("TESTUSDT", last - 0.01, last + 0.01),
    )


def rally(count: int = 130, start: float = 100.0, step: float = 0.08) -> list[Candle]:
    """A long climb, enough to seed a 100 EMA and leave price above it."""
    out = []
    for i in range(count):
        base = start + i * step
        out.append(candle(base, base + 0.10, base - 0.02, base + 0.08))
    return out


def long_setup() -> list[Candle]:
    """Trend up, two clean red pullback candles, then a high-volume doji.

    The numbers are solved rather than sketched, because Heikin Ashi is
    recursive and a fixture that looks right in real candles usually is not.
    HA_open is the midpoint of the PREVIOUS HA candle, so a doji requires this
    bar's (open + high + low + close) / 4 to land on that midpoint - which no
    amount of drawing a small real body will achieve on its own. An earlier
    version of this fixture produced a body/span of 0.50 where a doji was
    intended, and the strategy correctly refused it.
    """
    bars = rally()

    # Two pullback candles. Their real highs sit below the HA open, so the
    # resulting HA candles have no upper wick at all - "clean", in the video's
    # terms, which on Heikin Ashi means the period never traded above its own
    # smoothed open.
    bars.append(candle(110.20, 110.25, 109.90, 109.95))
    bars.append(candle(109.95, 110.00, 109.60, 109.65))

    # The doji, on volume larger than both preceding candles. Its average lands
    # on the previous HA midpoint, giving a body of essentially zero.
    bars.append(candle(109.65, 110.455, 109.55, 110.30, volume=2600.0))
    return bars


def short_setup() -> list[Candle]:
    """The same fixture reflected about 220, so every relationship mirrors.

    Reflecting rather than rebuilding is deliberate: a hand-written short
    fixture would test whether that fixture is right, where this tests whether
    the strategy is symmetric.
    """
    pivot = 220.0
    return [
        candle(
            pivot - c.open,
            pivot - c.low,
            pivot - c.high,
            pivot - c.close,
            volume=c.volume,
        )
        for c in long_setup()
    ]


STRATEGY = MinuteBlack()


class TestTheHappyPath:
    def test_the_full_long_pattern_fires(self):
        setup = STRATEGY.assess(context(long_setup()))
        assert setup.ready is True, setup.note
        assert setup.direction == "LONG"

    def test_the_full_short_pattern_fires(self):
        setup = STRATEGY.assess(context(short_setup()))
        assert setup.ready is True, setup.note
        assert setup.direction == "SHORT"

    def test_the_note_names_what_was_found(self):
        note = STRATEGY.assess(context(long_setup())).note.lower()
        assert "doji" in note


class TestMarketStructure:
    def test_a_long_below_the_ema_is_refused(self):
        # Point 2, verbatim: only take buys if price is above the 100 EMA.
        bars = long_setup()
        crash = [candle(50.0, 50.1, 49.9, 50.0) for _ in range(6)]
        setup = STRATEGY.assess(context(bars[:-3] + crash + bars[-3:]))
        assert setup.ready is False

    def test_the_ema_needs_its_full_period(self):
        # A 100 EMA seeded on 30 bars is not a 100 EMA. Answering anyway would
        # be inventing a trend filter.
        setup = STRATEGY.assess(context(rally(30)))
        assert setup.ready is False
        assert "history" in setup.note.lower() or "candles" in setup.note.lower()


class TestCleanPullback:
    def test_one_pullback_candle_is_not_enough(self):
        # Point 3 says at least two. One red bar is noise on a 1m chart.
        bars = long_setup()
        del bars[-2]
        assert STRATEGY.assess(context(bars)).ready is False

    def test_a_pullback_candle_with_a_wick_on_the_trend_side_is_not_clean(self):
        # "Clean = no wicks on top for buys". A long upper wick means the move
        # was pushed back, which is the opposite of the decisiveness wanted.
        bars = long_setup()
        # A tall upper wick on the first pullback candle: the move was pushed
        # back, which is the opposite of the decisiveness "clean" describes.
        bars[-3] = candle(110.20, 110.85, 109.90, 109.95)
        assert STRATEGY.assess(context(bars)).ready is False

    def test_the_pullback_must_be_against_the_trend(self):
        # Two GREEN candles in an uptrend are continuation, not a pullback.
        bars = long_setup()
        # Two bars that continue the rally instead of retracing it.
        bars[-3] = candle(110.20, 110.60, 110.18, 110.55)
        bars[-2] = candle(110.55, 110.95, 110.53, 110.90)
        assert STRATEGY.assess(context(bars)).ready is False


class TestTheDoji:
    def test_a_large_bodied_candle_is_not_a_doji(self):
        # Point 4 wants indecision. A decisive candle is the opposite.
        bars = long_setup()
        # A decisive close instead of a balanced one. Worth noting how hard
        # this is to construct: Heikin Ashi drags the low of the synthetic
        # candle down to the real low, which inflates the span and makes even
        # a strong real bar read as small-bodied. The bar has to open and
        # close well above the previous HA midpoint to count as decisive.
        bars[-1] = candle(110.00, 110.80, 110.00, 110.75, volume=2600.0)
        assert STRATEGY.assess(context(bars)).ready is False

    def test_a_doji_on_low_volume_is_refused(self):
        # Separate engineering volume-baseline filter, not the video's size rule.
        # the indecision is absence of participants, not a balance of them.
        bars = long_setup()
        bars[-1] = candle(109.65, 110.455, 109.55, 110.30, volume=200.0)
        assert STRATEGY.assess(context(bars)).ready is False

    def test_volume_equal_to_the_previous_candles_qualifies(self):
        # "larger OR SAME size". Equal must pass, or the rule is stricter than
        # the one written down.
        bars = long_setup()
        bars[-1] = candle(109.65, 110.455, 109.55, 110.30, volume=1000.0)
        assert STRATEGY.assess(context(bars)).ready is True


class TestHeikinAshiNeverReachesTheExchange:
    """The rule that shapes the whole implementation.

    HA_open is an average of two previous averages, so it is a level nothing
    ever traded at. A stop placed there would sit at a fictional price whose
    distance from the real fill has no relationship to the intended risk.
    """

    def test_the_stop_is_derived_from_a_real_candle_extreme(self):
        bars = long_setup()
        entry = Decimal(str(bars[-1].close))
        levels = STRATEGY.levels(context(bars), entry, "LONG")
        # The real low of the doji is the structural reference. The HA low of
        # the same bar is a different, synthetic number.
        assert levels.stop <= Decimal(str(bars[-1].low))

    def test_levels_are_decimal_not_float(self):
        levels = STRATEGY.levels(context(long_setup()), Decimal("110.0"), "LONG")
        assert isinstance(levels.stop, Decimal)
        assert isinstance(levels.target, Decimal)


class TestLevels:
    def _entry(self, bars):
        return Decimal(str(bars[-1].close))

    def test_the_long_bracket_straddles_the_entry(self):
        bars = long_setup()
        entry = self._entry(bars)
        levels = STRATEGY.levels(context(bars), entry, "LONG")
        assert levels.stop < entry < levels.target

    def test_the_short_bracket_is_mirrored(self):
        bars = short_setup()
        entry = self._entry(bars)
        levels = STRATEGY.levels(context(bars), entry, "SHORT")
        assert levels.target < entry < levels.stop

    def test_the_stop_clears_the_fee_floor(self):
        # His stop is 0.031% of price. On Binance that pays 288% of its own
        # risk in fees, so the floor is the whole reason this differs.
        from scalper.core.fees import MIN_STOP_FRACTION

        bars = long_setup()
        entry = self._entry(bars)
        levels = STRATEGY.levels(context(bars), entry, "LONG")
        assert (entry - levels.stop) / entry >= MIN_STOP_FRACTION

    def test_the_reward_beats_the_one_to_one_he_asks_for(self):
        # 1:1 needs a 60% net win rate at the 0.45% floor. 1.5:1 needs 48%.
        from scalper.engine.plan import MIN_REWARD_TO_RISK

        bars = long_setup()
        entry = self._entry(bars)
        levels = STRATEGY.levels(context(bars), entry, "LONG")
        risk = entry - levels.stop
        assert (levels.target - entry) / risk >= MIN_REWARD_TO_RISK

    def test_levels_refuse_without_a_signal(self):
        with pytest.raises(ValueError, match="history"):
            STRATEGY.levels(context(rally(30)), Decimal("100"), "LONG")


class TestNoLookahead:
    def test_the_doji_must_be_the_last_closed_bar(self):
        # market_data drops the forming bar, so index -1 is closed. Removing
        # the doji must stop the signal rather than find it elsewhere.
        bars = long_setup()
        assert STRATEGY.assess(context(bars)).ready is True
        assert STRATEGY.assess(context(bars[:-1])).ready is False

    def test_a_later_bar_does_not_revive_an_old_signal(self):
        bars = long_setup()
        extended = bars + [candle(200.0, 200.5, 199.5, 200.2)]
        assert STRATEGY.assess(context(extended)).ready is False


class TestRegistration:
    def test_it_is_registered(self):
        from scalper.strategies import STRATEGIES

        assert STRATEGY.id in STRATEGIES.ids()

    def test_it_is_named_as_the_operator_asked(self):
        assert STRATEGY.name == "The Minute Black"

    def test_every_departure_is_recorded(self):
        assert len(STRATEGY.assumptions) >= 6

    def test_the_two_real_problems_are_stated(self):
        joined = " ".join(STRATEGY.assumptions).lower()
        assert "heikin" in joined
        assert "0.031%" in joined or "fee" in joined

    def test_it_asks_for_enough_history_for_a_100_ema(self):
        assert STRATEGY.history_1m >= 100


@pytest.mark.parametrize("direction,price", [("LONG", "109"), ("SHORT", "111")])
def test_crossed_structure_never_invents_a_replacement_stop(direction, price):
    bars = long_setup() if direction == "LONG" else short_setup()
    with pytest.raises(ValueError, match="structure"):
        STRATEGY.levels(context(bars), Decimal(price), direction)


def test_chasing_price_is_refused():
    with pytest.raises(ValueError, match=r"0\.5 ATR"):
        STRATEGY.levels(context(long_setup()), Decimal("112"), "LONG")


def test_zero_volume_cannot_be_high_volume():
    from dataclasses import replace
    bars = [replace(c, volume=0) for c in long_setup()]
    assert not STRATEGY.assess(context(bars)).ready


def test_stop_has_buffer_beyond_real_extreme():
    bars = long_setup()
    levels = STRATEGY.levels(context(bars), Decimal(str(bars[-1].close)), "LONG")
    assert levels.stop < Decimal(str(bars[-1].low))
