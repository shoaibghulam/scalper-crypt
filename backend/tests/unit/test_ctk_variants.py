"""The twenty CTK variants.

A variant is only worth scoring if it really differs from the source in the way
it claims. A knob that silently does nothing produces a row in the results
table that looks like evidence and is not - so each one is asserted to change
behaviour, and the things that must NOT change are asserted too.
"""

from decimal import Decimal

import pytest

from scalper.exchange.market_data import BookTop, Candle, Ticker
from scalper.indicators.core import ema_series
from scalper.strategies import STRATEGIES
from scalper.strategies.base import StrategyContext
from scalper.strategies.ctk import SOURCE_PARAMS, TREND_PERIOD, Ctk1, CtkParams
from scalper.strategies.ctk_variants import (
    RECIPES,
    SOURCE_REWARD,
    VARIANT_COUNT,
    Ctk1Variant,
    Ctk5Variant,
    break_even_win_rate,
    ctk_variants,
)

MINUTE = 60_000


def candle(index, high, low, close, volume=900.0):
    at = index * MINUTE
    return Candle(
        at,
        (high + low) / 2,
        high,
        low,
        close,
        volume,
        at + MINUTE - 1,
        close * volume,
        volume * 0.55,
    )


def quiet(count=700, price=100.0):
    """A flat range: an accumulation box forms and nothing escapes it."""
    return [candle(i, price + 0.3, price - 0.3, price) for i in range(count)]


def with_push(count=700, *, wick_only=False, depth=3.0, volume=900.0):
    """A quiet range, then a push below the box on the final bar."""
    bars = quiet(count - 1)
    close = 100.0 if wick_only else 100.0 - depth * 0.9
    bars.append(candle(count - 1, 100.3, 100.0 - depth, close, volume=volume))
    return bars


def five_minute(bars):
    """Complete 5m groups, because has_enough_history wants context bars."""
    groups = {}
    for bar in bars:
        groups.setdefault(bar.open_time // 300_000 * 300_000, []).append(bar)
    return [
        Candle(
            start,
            group[0].open,
            max(b.high for b in group),
            min(b.low for b in group),
            group[-1].close,
            sum(b.volume for b in group),
            start + 299_999,
            sum(b.quote_volume for b in group),
            sum(b.taker_buy_base for b in group),
        )
        for start, group in groups.items()
        if len(group) == 5
    ]


def context(bars):
    last = bars[-1].close
    return StrategyContext(
        "TESTUSDT",
        five_minute(bars),
        bars,
        Ticker("TESTUSDT", last, 1.0, 5e8),
        BookTop("TESTUSDT", last - 0.01, last + 0.01),
    )


class TestTheLadderIsWellFormed:
    def test_there_are_ten_of_each(self):
        assert len(ctk_variants()) == 2 * VARIANT_COUNT
        assert len(RECIPES) == VARIANT_COUNT

    def test_the_ids_and_names_are_the_ones_asked_for(self):
        ids = {v.id for v in ctk_variants()}
        assert ids == {f"ctk-1-v{n}" for n in range(1, 11)} | {
            f"ctk-5-v{n}" for n in range(1, 11)
        }
        assert Ctk1Variant(3).name == "CTK 1 v3"
        assert Ctk5Variant(10).name == "CTK 5 v10"

    def test_all_twenty_are_registered_alongside_the_originals(self):
        registered = set(STRATEGIES.ids())
        assert {"ctk-1", "ctk-5"} <= registered
        assert {f"ctk-1-v{n}" for n in range(1, 11)} <= registered
        assert {f"ctk-5-v{n}" for n in range(1, 11)} <= registered

    def test_an_out_of_range_variant_is_refused(self):
        for number in (0, 11):
            with pytest.raises(ValueError, match="must be 1-10"):
                Ctk1Variant(number)

    def test_each_variant_differs_from_the_source(self):
        # A recipe that forgot to change anything would score identically to
        # CTK and still look like a meaningful independent trial.
        assert all(recipe.params != SOURCE_PARAMS for recipe in RECIPES)

    def test_no_two_variants_are_the_same_experiment(self):
        assert len({repr(recipe.params) for recipe in RECIPES}) == VARIANT_COUNT

    def test_every_variant_explains_itself(self):
        for number, recipe in enumerate(RECIPES, start=1):
            variant = Ctk1Variant(number)
            assert recipe.label in variant.summary
            assert len(recipe.rationale) > 80
            assert any("Variant" in note for note in variant.assumptions)

    def test_the_selection_bias_warning_travels_with_every_variant(self):
        # Twenty trials on one symbol produce a winner regardless of edge.
        # That warning has to live on the strategy, not only in a chat message.
        for variant in ctk_variants():
            assert any("twenty variants" in note for note in variant.assumptions)


class TestTheDeclaredRewardMatchesTheTarget:
    def test_the_minimum_always_equals_the_recipe(self):
        # If these disagree the plan gate refuses every trade the variant
        # makes, so it silently never trades rather than trading badly.
        for variant in ctk_variants():
            assert variant.min_reward_to_risk == variant.params.reward_risk

    def test_some_variant_tries_a_larger_reward(self):
        # Which slot holds it changes as the ladder is re-tuned; that at least
        # one variant attacks the ratio should not.
        rewards = {v.min_reward_to_risk for v in ctk_variants()}
        assert any(reward > SOURCE_REWARD for reward in rewards)

    def test_some_variant_keeps_the_source_ratio(self):
        # Without a control at 0.86 there is nothing to attribute a change to.
        assert any(v.min_reward_to_risk == SOURCE_REWARD for v in ctk_variants())

    def test_break_even_falls_as_the_reward_rises(self):
        source = break_even_win_rate(Decimal("0.86"), 0.103)
        better = break_even_win_rate(Decimal("1.5"), 0.103)
        assert source == pytest.approx(0.593, abs=0.002)
        assert better == pytest.approx(0.441, abs=0.002)
        assert better < source

    def test_a_wider_stop_lowers_the_fee_share(self):
        # v9's whole argument: the same fee against a bigger stop matters less.
        assert break_even_win_rate(Decimal("1.2"), 0.064) < break_even_win_rate(
            Decimal("1.2"), 0.103
        )


class TestEachKnobActuallyDoesSomething:
    def test_the_source_defaults_are_still_the_source(self):
        # The refactor that made CTK configurable must not have moved CTK.
        assert SOURCE_PARAMS == CtkParams()
        assert Ctk1().params == SOURCE_PARAMS

    def test_close_confirmation_rejects_a_wick_the_source_takes(self):
        wick = with_push(wick_only=True)
        assert Ctk1().assess(context(wick)).ready is True
        assert Ctk1Variant(4).assess(context(wick)).ready is False

    def test_a_deeper_push_is_required_by_v5(self):
        shallow = with_push(depth=1.2)
        assert Ctk1().assess(context(shallow)).ready is True
        assert Ctk1Variant(5).assess(context(shallow)).ready is False

    def test_volume_confirmation_rejects_a_quiet_sweep(self):
        quiet_push = with_push(volume=100.0)
        assert Ctk1().assess(context(quiet_push)).ready is True
        assert Ctk1Variant(8).assess(context(quiet_push)).ready is False

    def test_the_trend_filter_refuses_a_dip_in_a_downtrend(self):
        # Price below its own EMA means the long would be fading a live
        # downtrend, which is exactly the trade v7 exists to skip.
        falling = [
            candle(i, 120 - i * 0.02 + 0.3, 120 - i * 0.02 - 0.3, 120 - i * 0.02)
            for i in range(699)
        ]
        falling.append(candle(699, 106.3, 102.0, 102.5))
        assert Ctk1Variant(7).assess(context(falling)).ready is False

    def test_some_variant_widens_the_stop(self):
        # The fee share is the larger of the two terms CTK loses to, and the
        # stop width is the only thing that moves it. A ladder with no wide
        # stop anywhere is not testing that.
        assert any(v.params.risk_mult > SOURCE_PARAMS.risk_mult for v in ctk_variants())

    def test_a_wide_stop_really_lowers_the_fee_share(self):
        # Arithmetic, not a backtest: the same round trip against a stop twice
        # as wide is half the drag.
        source = break_even_win_rate(SOURCE_REWARD, 0.22)
        wider = break_even_win_rate(SOURCE_REWARD, 0.11)
        assert wider < source
        assert source == pytest.approx(0.656, abs=0.002)

    def test_the_reset_changes_what_counts_as_manipulation(self):
        """The knob the source has no path to.

        A bar clears the box edge without reaching the manipulation level, so
        the break failed. The source can never leave the armed state, keeps
        measuring against the box it froze, and fades the next push. With the
        reset, price settling back inside returns it to searching; the box
        re-forms around the failed spike and is therefore wider, so that same
        push is no longer beyond it.

        Either behaviour is defensible. The point is that they differ, which is
        what makes v3 an experiment rather than a relabelled CTK.
        """
        from scalper.indicators.core import atr_series
        from scalper.strategies.ctk import _replay

        bars = [candle(i, 100.1, 99.9, 100.0) for i in range(260)]
        bars.append(candle(260, 100.25, 99.9, 100.0))  # clears the box, not the level
        bars.extend(candle(i, 100.1, 99.9, 100.0) for i in range(261, 300))
        bars.append(candle(300, 100.35, 99.9, 100.33))  # the push that follows

        volatility = atr_series(
            [b.high for b in bars], [b.low for b in bars], [b.close for b in bars], 50
        )
        source = _replay(bars, volatility, SOURCE_PARAMS)
        reset = _replay(bars, volatility, CtkParams(reset_when_unarmed=True))
        assert source is not None and source.direction == "SHORT"
        assert reset is None


class TestTheTimeframesStayDistinct:
    def test_the_one_minute_family_reads_one_minute_bars(self):
        ctx = context(quiet(10))
        assert Ctk1Variant(1).interval_ms == 60_000
        assert Ctk1Variant(1)._bars(ctx) is ctx.candles_1m

    def test_the_five_minute_family_reads_five_minute_bars(self):
        ctx = StrategyContext(
            "TESTUSDT",
            quiet(10),
            [],
            Ticker("TESTUSDT", 100.0, 1.0, 5e8),
            BookTop("TESTUSDT", 99.9, 100.1),
        )
        assert Ctk5Variant(1).interval_ms == 300_000
        assert Ctk5Variant(1)._bars(ctx) is ctx.candles_5m

    def test_both_families_share_a_recipe_at_the_same_number(self):
        for number in range(1, VARIANT_COUNT + 1):
            assert Ctk1Variant(number).params == Ctk5Variant(number).params

    def test_the_five_minute_family_keeps_the_base_scan_cadence(self):
        # Overriding it would make every CTK 5 variant differ from CTK 5 in
        # two ways at once, and the comparison would attribute nothing.
        assert Ctk5Variant(1).signal_interval_s == STRATEGIES.get("ctk-5").signal_interval_s


class TestTrendAlignment:
    def test_the_trend_series_lines_up_with_the_bars(self):
        # ema_series is shorter than its input, so indexing it with a bar
        # index would read the EMA of an earlier bar - a silent look-behind
        # that would have the filter judging the wrong moment.
        closes = [float(i) for i in range(300)]
        trend = Ctk1()._trend(closes)
        assert len(trend) == len(closes)
        assert trend[-1] == pytest.approx(
            ema_series(closes, min(TREND_PERIOD, len(closes)))[-1]
        )

    def test_a_short_history_still_produces_an_aligned_series(self):
        closes = [100.0 + i * 0.1 for i in range(40)]
        assert len(Ctk1()._trend(closes)) == 40
