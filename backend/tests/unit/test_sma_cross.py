"""SMA 20/50 crossover on 15-minute candles.

Built to the spec in a strategy record for an ETHUSDT long: look for a
SMA(20)/SMA(50) crossover on the latest closed candle and take the side that
crossed. Two properties of this venue shape the implementation.

The engine fetches 1m and 5m only, so 15m is built by folding three 5m bars
into one. A partial trailing group is never used: a 15m signal read off two
closed bars and one still forming is a signal off a candle that does not exist
yet, which is the precise mistake the source record warns about.

And the platform brackets every entry with a stop and a target, where the
source rule held until the opposite crossover. That divergence is deliberate
and recorded in the strategy's own assumptions - the source rule had no stop
at all, not by choice but because its venue rejected them.
"""

from decimal import Decimal

import pytest

from scalper.strategies.sma_cross import SmaCross2050, fold_15m


class _Candle:
    """Minimal candle stand-in: the folder only reads OHLC and times."""

    __slots__ = ("open_time", "close_time", "open", "high", "low", "close", "volume")

    def __init__(self, **kw):
        for key, value in kw.items():
            setattr(self, key, value)


def bars(closes: list[float]) -> list[_Candle]:
    """5m candles with the given closes, ascending in time."""
    out = []
    for index, close in enumerate(closes):
        open_time = index * 300_000
        out.append(
            _Candle(
                open_time=open_time,
                close_time=open_time + 299_999,
                open=close,
                high=close * 1.001,
                low=close * 0.999,
                close=close,
                volume=100.0,
            )
        )
    return out


class TestFolding:
    def test_three_five_minute_bars_make_one_fifteen(self):
        assert len(fold_15m(bars([1, 2, 3, 4, 5, 6]))) == 2

    def test_a_partial_trailing_group_is_dropped(self):
        # Seven bars is two complete 15m candles and one third of a third.
        assert len(fold_15m(bars([1, 2, 3, 4, 5, 6, 7]))) == 2

    def test_the_fold_takes_the_last_close_of_the_group(self):
        folded = fold_15m(bars([10, 20, 30, 40, 50, 60]))
        assert [c.close for c in folded] == [30, 60]

    def test_the_fold_spans_the_high_and_low_of_the_group(self):
        folded = fold_15m(bars([10, 100, 30]))
        assert folded[0].high == pytest.approx(100 * 1.001)
        assert folded[0].low == pytest.approx(10 * 0.999)

    def test_too_few_bars_fold_to_nothing(self):
        assert fold_15m(bars([1, 2])) == []


class TestCrossoverDetection:
    def test_a_series_that_crosses_up_goes_long(self):
        assert SmaCross2050().cross(_flat_then_rally()) == "LONG"

    def test_a_series_that_crosses_down_goes_short(self):
        assert SmaCross2050().cross(_flat_then_slump()) == "SHORT"

    def test_no_crossing_is_no_signal(self):
        # A steady uptrend: the fast average stays above the slow one
        # throughout and never crosses it.
        assert SmaCross2050().cross([100 + i for i in range(120)]) is None

    def test_a_crossover_on_an_earlier_bar_is_not_traded(self):
        # The source rule trades a cross on the LATEST candle. Acting on an
        # older one is the "state interpretation" its own author flagged as
        # the weak joint in the trade.
        closes = _flat_then_rally() + [200, 201, 202, 203, 204]
        assert SmaCross2050().cross(closes) is None

    def test_too_short_a_series_is_no_signal(self):
        assert SmaCross2050().cross([100.0] * 40) is None


class TestBracket:
    def test_a_long_stops_below_and_targets_above(self):
        levels = SmaCross2050().bracket(Decimal("2678.17"), "LONG", atr_value=12.0)
        assert levels.stop < Decimal("2678.17") < levels.target

    def test_a_short_stops_above_and_targets_below(self):
        levels = SmaCross2050().bracket(Decimal("2678.17"), "SHORT", atr_value=12.0)
        assert levels.target < Decimal("2678.17") < levels.stop

    def test_the_stop_never_sits_inside_the_fee_floor(self):
        # A stop tighter than 0.45% is eaten by the round trip. The source
        # record measured exactly this and called it the weakest structural
        # choice in the trade.
        entry = Decimal("2678.17")
        levels = SmaCross2050().bracket(entry, "LONG", atr_value=0.0001)
        assert (entry - levels.stop) / entry >= Decimal("0.0045")

    def test_the_target_is_twice_the_risk(self):
        entry = Decimal("1000")
        levels = SmaCross2050().bracket(entry, "LONG", atr_value=10.0)
        assert (levels.target - entry) == 2 * (entry - levels.stop)

    def test_an_invalid_direction_is_refused(self):
        with pytest.raises(ValueError):
            SmaCross2050().bracket(Decimal("100"), "SIDEWAYS", atr_value=1.0)


class TestRegistration:
    def test_it_is_reachable_by_id(self):
        from scalper.strategies.research_registry import RESEARCH_STRATEGIES

        assert "sma-cross-20-50-15m" in RESEARCH_STRATEGIES

    def test_it_declares_its_real_timeframe(self):
        from scalper.strategies.research_registry import RESEARCH_STRATEGIES

        assert RESEARCH_STRATEGIES["sma-cross-20-50-15m"].describe()["timeframe"] == "15m"

    def test_it_admits_the_backtest_that_motivated_it(self):
        # The source record reports -6.4% on ETHUSDT 1h and argues 15m is
        # worse. Shipping that silently would be the dishonest part.
        from scalper.strategies.research_registry import RESEARCH_STRATEGIES

        described = RESEARCH_STRATEGIES["sma-cross-20-50-15m"].describe()
        text = " ".join(described["assumptions"]) + str(described.get("summary", ""))
        assert "6.4" in text


def _flat_then_rally() -> list[float]:
    """Flat, then ONE bar that lifts the fast average through the slow one.

    A longer rally would cross several bars before the last, and this rule
    only trades a cross on the latest closed candle - so a multi-bar rally
    correctly reads as no signal, which is what an earlier version of this
    fixture got wrong.
    """
    return [100.0] * 60 + [130.0]


def _flat_then_slump() -> list[float]:
    return [100.0] * 60 + [70.0]
