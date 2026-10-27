"""Legend sweep/reclaim adaptation; these fixtures do not establish profitability.

The source uses a one-ATR stop and discretionary demand. FVG, numerical
thresholds, mirrored shorts and fee-distance floor are engineering choices.
See strategy-review-2026-09-18/README.md.
"""

from decimal import Decimal

import pytest

from scalper.exchange.market_data import BookTop, Candle, Ticker
from scalper.strategies.base import StrategyContext
from scalper.strategies.legend import MinuteLegendV1


def candle(
    close: float,
    high: float | None = None,
    low: float | None = None,
    volume: float = 1000.0,
    open_: float | None = None,
) -> Candle:
    high = close if high is None else high
    low = close if low is None else low
    return Candle(
        open_time=0,
        open=close if open_ is None else open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        close_time=0,
        quote_volume=close * volume,
        taker_buy_base=volume * 0.6,
    )


def context(candles_1m: list[Candle], candles_5m: list[Candle] | None = None):
    series_5m = (
        candles_5m if candles_5m is not None else [candle(100.0, 100.6, 99.4) for _ in range(80)]
    )
    last = candles_1m[-1].close
    return StrategyContext(
        symbol="TESTUSDT",
        candles_5m=series_5m,
        candles_1m=candles_1m,
        ticker=Ticker("TESTUSDT", last, 1.0, 500_000_000.0),
        book=BookTop("TESTUSDT", last - 0.01, last + 0.01),
    )


def uptrend(count: int = 52, start: float = 100.0, step: float = 0.05) -> list[Candle]:
    """Higher highs and higher lows, with 1m ranges a real symbol would have.

    The range matters: the strategy refuses symbols too quiet for a 0.45% stop
    to resolve inside its holding window, so a fixture of hairline candles
    would be rejected for volatility before any structure was examined.
    """
    out = []
    for i in range(count):
        mid = start + i * step
        out.append(candle(mid, mid + 0.06, mid - 0.06))
    return out


def downtrend(count: int = 52, start: float = 100.0, step: float = 0.05) -> list[Candle]:
    out = []
    for i in range(count):
        mid = start - i * step
        out.append(candle(mid, mid + 0.06, mid - 0.06))
    return out


def long_setup() -> list[Candle]:
    """The whole pattern, bar by bar, for a long.

    Built explicitly rather than generated so every component is visible and
    can be broken one at a time by the tests below. Index comments are the
    positions the tests reach for.
    """
    bars = uptrend()  # 0..51, rising 100.00 -> 102.55

    # 52..54 the imbalance: bar52.high 102.62 sits below bar54.low 102.98, so
    # the band between them traded inside a single bar and the orders resting
    # there were never filled.
    bars.append(candle(102.58, 102.62, 102.52))
    bars.append(candle(102.80, 102.95, 102.66))
    bars.append(candle(103.40, 103.90, 102.98))

    # 55..60 drift back down. Bar 57 makes a clear swing low at 102.40, which
    # WICKS below the 102.62 gap floor while closing above it - the level whose
    # resting stop orders are the liquidity this pattern is after.
    bars.append(candle(103.20, 103.45, 103.05))
    bars.append(candle(102.90, 103.25, 102.80))
    bars.append(candle(102.72, 102.95, 102.40))  # the swing low
    bars.append(candle(102.85, 102.95, 102.66))
    bars.append(candle(102.95, 103.05, 102.78))
    bars.append(candle(102.88, 102.98, 102.74))

    # 61 the sweep and rejection: trades down to 102.30 through the swing low,
    # closes back at 102.90 with the lower wick most of the range, on 3.4x the
    # median volume of the preceding twenty bars.
    bars.append(candle(102.90, 102.98, 102.30, volume=3400.0))
    return bars


def short_setup() -> list[Candle]:
    """The same pattern mirrored, for a short."""
    bars = downtrend()  # 100.00 -> 97.45

    bars.append(candle(97.42, 97.48, 97.38))
    bars.append(candle(97.20, 97.34, 97.05))
    bars.append(candle(96.60, 97.02, 96.10))  # gap 97.02 - 97.38

    bars.append(candle(96.80, 96.95, 96.55))
    bars.append(candle(97.10, 97.20, 96.75))
    bars.append(candle(97.28, 97.60, 97.05))  # the swing high
    bars.append(candle(97.15, 97.34, 97.05))
    bars.append(candle(97.05, 97.22, 96.95))
    bars.append(candle(97.12, 97.26, 97.02))

    bars.append(candle(97.10, 97.70, 97.02, volume=3400.0))
    return bars


STRATEGY = MinuteLegendV1()


class TestTheHappyPath:
    def test_the_full_long_pattern_fires(self):
        setup = STRATEGY.assess(context(long_setup()))
        assert setup.ready is True
        assert setup.direction == "LONG"

    def test_the_full_short_pattern_fires(self):
        setup = STRATEGY.assess(context(short_setup()))
        assert setup.ready is True
        assert setup.direction == "SHORT"

    def test_the_note_names_the_swept_level(self):
        # A scanner row that says "setup ready" and nothing else cannot be
        # tuned, and an untunable scanner gets loosened until the edge is gone.
        note = STRATEGY.assess(context(long_setup())).note
        assert "swept" in note.lower() or "sweep" in note.lower()


class TestTrendFilter:
    def test_a_downtrend_refuses_the_long_pattern(self):
        # The video is explicit that the demand level is traded in an uptrend.
        bars = downtrend(52, start=106.0) + long_setup()[52:]
        assert STRATEGY.assess(context(bars)).ready is False

    def test_higher_highs_without_higher_lows_is_not_a_trend(self):
        # "Clearly here we are making higher highs" is not a rule a machine can
        # apply. Requiring both ends to rise makes it one, and rejects the
        # broadening pattern that rises and falls at the same time.
        widening = [candle(100.0 + i * 0.01, 100.2 + i * 0.05, 99.8 - i * 0.05) for i in range(52)]
        assert STRATEGY.assess(context(widening + long_setup()[52:])).ready is False


class TestTheImbalance:
    def test_no_gap_means_no_demand_zone(self):
        # Without the imbalance there is no reason to expect unfilled orders,
        # and the swept low is just a low.
        bars = long_setup()
        # Make the rally gradual so every bar overlaps the one two before it
        # and no band was ever skipped. Patching only the middle bar is not
        # enough: the search correctly finds any other imbalance still in
        # range, which is the behaviour wanted and had to be designed around
        # rather than asserted away.
        bars[52] = candle(102.58, 102.70, 102.52)
        bars[53] = candle(102.80, 102.95, 102.55)
        bars[54] = candle(103.40, 103.90, 102.60)
        bars[55] = candle(103.20, 103.45, 102.90)
        assert STRATEGY.assess(context(bars)).ready is False

    def test_a_gap_already_filled_does_not_count(self):
        # Once price has closed back through the band, the orders left resting
        # there have been filled. The zone is spent.
        bars = long_setup()
        bars[57] = candle(102.55, 102.95, 102.40)  # closes below the 102.62 floor
        assert STRATEGY.assess(context(bars)).ready is False

    def test_a_wick_into_the_zone_does_not_spend_it(self):
        # The counterpart to the test above, and the more important of the two.
        # This pattern REQUIRES price to come back and wick below the zone, so
        # an over-strict "untouched" rule would reject every valid setup while
        # still passing the filled-gap test.
        assert STRATEGY.assess(context(long_setup())).ready is True


class TestTheSweptLow:
    def test_a_low_far_below_the_zone_does_not_count(self):
        # "A clear low that rests just below" is unquantified in the video.
        # Bounded so an unrelated low much further down is not treated as this
        # zone's liquidity.
        bars = long_setup()
        bars[57] = candle(102.72, 102.95, 101.20)  # swing low 1.4% below the zone
        bars[61] = candle(102.90, 102.98, 101.10, volume=3400.0)
        assert STRATEGY.assess(context(bars)).ready is False

    def test_price_must_actually_trade_through_the_low(self):
        # Approaching a level is not sweeping it. No stops were triggered, so
        # the absorption the pattern depends on never happened.
        bars = long_setup()
        bars[61] = candle(102.90, 102.98, 102.45, volume=3400.0)  # stops above 102.40
        assert STRATEGY.assess(context(bars)).ready is False

    def test_a_pivot_confirmed_only_by_the_rejection_bar_is_not_a_pivot(self):
        # The circularity guard. Without it, a low two bars back would be
        # confirmed as a swing by the rejection bar itself, so the level would
        # exist only because the bar claiming to sweep it exists.
        bars = long_setup()
        bars[57] = candle(102.90, 103.00, 102.85)  # remove the genuine swing low
        bars[60] = candle(102.60, 102.70, 102.55)  # a low only the trigger confirms
        assert STRATEGY.assess(context(bars)).ready is False


class TestTheRejection:
    def test_closing_below_the_swept_low_is_not_a_rejection(self):
        # The video states this invalidation itself: without the rejection the
        # level did not hold. It is a breakdown, not a sweep.
        bars = long_setup()
        bars[61] = candle(102.35, 102.80, 102.30, volume=3400.0)
        assert STRATEGY.assess(context(bars)).ready is False

    def test_a_rejection_needs_a_wick(self):
        # "Sharp" needs a number. A bar that closes back above the low having
        # spent most of its range down there is drift, not rejection.
        bars = long_setup()
        bars[61] = candle(102.42, 102.98, 102.30, volume=3400.0)
        assert STRATEGY.assess(context(bars)).ready is False


class TestVolumeConfirmation:
    def test_a_rejection_on_ordinary_volume_is_not_confirmed(self):
        # The video says an increase in volume is what confirms orders are
        # sitting there. Without it the wick could be one small seller.
        bars = long_setup()
        bars[61] = candle(102.90, 102.98, 102.30, volume=900.0)
        assert STRATEGY.assess(context(bars)).ready is False

    def test_volume_is_judged_relative_not_absolute(self):
        # Scaled so the same rule works on a symbol trading in millions and one
        # trading in hundreds.
        bars = [candle(c.close, c.high, c.low, volume=c.volume * 1000.0) for c in long_setup()]
        assert STRATEGY.assess(context(bars)).ready is True

    def test_one_earlier_spike_does_not_suppress_a_genuine_rise(self):
        # Why a median rather than the mean that indicators.rvol uses. A single
        # 10x bar in the lookback lifts a mean by roughly 50%, which would
        # disqualify every real signal that followed it.
        bars = long_setup()
        bars[50] = candle(102.50, 102.56, 102.44, volume=40_000.0)
        assert STRATEGY.assess(context(bars)).ready is True


class TestReachability:
    def test_a_symbol_too_quiet_to_reach_its_stop_is_skipped(self):
        # The finding that shaped this strategy. A 0.45% stop takes roughly an
        # hour to resolve on BTC at typical volatility; on a quieter symbol the
        # position outlives the signal that justified it entirely.
        # The whole price PATH is scaled toward a constant, not just each bar's
        # range. Shrinking only the intrabar range leaves the bar-to-bar moves
        # intact, and true range counts those, so the symbol still measured as
        # perfectly tradeable. The structure is preserved at one twelfth the
        # amplitude: the same pattern on a symbol that does not move.
        def quieten(value: float) -> float:
            return 100.0 + (value - 100.0) / 12.0

        quiet = [
            candle(
                quieten(c.close),
                quieten(c.high),
                quieten(c.low),
                volume=c.volume,
            )
            for c in long_setup()
        ]
        setup = STRATEGY.assess(context(quiet))
        assert setup.ready is False
        assert "quiet" in setup.note.lower() or "atr" in setup.note.lower()

    def test_the_floor_matches_the_barrier_arithmetic(self):
        # Time for a random walk to touch a barrier at distance a is
        # a^2/sigma^2, so reaching a 0.45% stop within 30 minutes needs
        # 0.45%/sqrt(30) of movement per minute. A derivation, not a dial.
        from scalper.strategies.legend import reachability_floor

        assert abs(reachability_floor(30) - 0.0045 / 30**0.5) < 1e-12


class TestHistoryGuard:
    def test_too_little_history_waits_rather_than_guessing(self):
        setup = STRATEGY.assess(context(uptrend(10)))
        assert setup.ready is False
        assert "history" in setup.note.lower() or "candles" in setup.note.lower()

    def test_the_forming_bar_is_never_consulted(self):
        # market_data drops the forming bar before the strategy sees it, so
        # candles[-1] is the last CLOSED bar and the rejection must be found
        # there. Looking one further forward would read a bar that does not
        # exist yet.
        bars = long_setup()
        assert STRATEGY.assess(context(bars)).ready is True
        assert STRATEGY.assess(context(bars[:-1])).ready is False


class TestLevels:
    ENTRY = Decimal("102.90")

    def _levels(self, bars, direction="LONG"):
        return STRATEGY.levels(context(bars), self.ENTRY, direction)

    def test_the_long_stop_sits_below_the_entry(self):
        assert self._levels(long_setup()).stop < self.ENTRY

    def test_the_long_target_sits_above_the_entry(self):
        assert self._levels(long_setup()).target > self.ENTRY

    def test_the_stop_sits_beyond_the_sweep_extreme(self):
        # Not merely below the swept level: below the lowest price the sweep
        # actually reached. A stop inside the wick that just fired is placed
        # where the market has already proven it can trade.
        assert self._levels(long_setup()).stop <= Decimal("102.30")

    def test_the_short_levels_are_mirrored(self):
        entry = Decimal("97.10")
        levels = STRATEGY.levels(context(short_setup()), entry, "SHORT")
        assert levels.stop > entry
        assert levels.target < entry

    def test_the_stop_clears_the_fee_floor(self):
        # The whole reason this strategy departs from its source. The ticket in
        # the video shows 0.18% and 0.05%; at 0.05% the round trip costs twice
        # what the trade risks, and no win rate recovers that.
        from scalper.core.fees import MIN_STOP_FRACTION

        levels = self._levels(long_setup())
        assert (self.ENTRY - levels.stop) / self.ENTRY >= MIN_STOP_FRACTION

    def test_a_structural_stop_tighter_than_the_floor_is_widened(self):
        # A shallow sweep is a GOOD signal that produces a BAD stop, so this
        # path is taken often rather than being a formality.
        from scalper.core.fees import MIN_STOP_FRACTION

        stop = STRATEGY._stop_for(self.ENTRY, 102.88, "LONG")
        assert (self.ENTRY - stop) / self.ENTRY >= MIN_STOP_FRACTION

    def test_the_reward_clears_the_minimum_ratio(self):
        # A target nearer than 1.5R cannot pay for the round trip at any
        # plausible win rate, so there is no trade rather than a bad one.
        from scalper.engine.plan import MIN_REWARD_TO_RISK

        levels = self._levels(long_setup())
        risk = self.ENTRY - levels.stop
        assert (levels.target - self.ENTRY) / risk >= MIN_REWARD_TO_RISK

    def test_levels_refuse_to_invent_a_bracket_without_structure(self):
        with pytest.raises(ValueError, match="history"):
            STRATEGY.levels(context(uptrend(10)), self.ENTRY, "LONG")

    def test_levels_are_decimal_not_float(self):
        # Floats on the wire lose the last place, and a price off the tick grid
        # is rejected by the exchange after the entry has already filled.
        levels = self._levels(long_setup())
        assert isinstance(levels.stop, Decimal)
        assert isinstance(levels.target, Decimal)


class TestRegistration:
    def test_the_strategy_is_registered_under_its_own_id(self):
        from scalper.strategies import STRATEGIES

        assert STRATEGY.id in STRATEGIES.ids()

    def test_it_is_named_as_the_operator_asked(self):
        assert STRATEGY.name == "1 Minute Legend v1"

    def test_every_departure_from_the_video_is_recorded(self):
        # Not decoration. The dashboard renders these, so an operator can see
        # where our choices differ from the source rather than assuming the
        # bot is the video.
        assert len(STRATEGY.assumptions) >= 5

    def test_the_fee_problem_is_stated_in_the_assumptions(self):
        # The single most important thing an operator should know before
        # running this: the source's own stop distance loses money here.
        joined = " ".join(STRATEGY.assumptions).lower()
        assert "0.18%" in joined or "fee" in joined

    def test_it_asks_for_enough_one_minute_history(self):
        # Trend lookback plus the zone search plus the sweep window.
        assert STRATEGY.history_1m >= 40


class TestNoLookahead:
    def test_assessment_depends_only_on_bars_up_to_the_last_closed_one(self):
        # Appending a future bar must not change the verdict for the bars that
        # came before it. If it does, something is reading ahead.
        bars = long_setup()
        before = STRATEGY.assess(context(bars)).ready
        extended = bars + [candle(103.10, 103.20, 103.00)]
        # The verdict for the ORIGINAL series is unchanged by what came later.
        assert STRATEGY.assess(context(bars)).ready == before
        # And the extended series is judged on its own last bar, which is not
        # a rejection, so it must not still be firing.
        assert STRATEGY.assess(context(extended)).ready is False


class TestTheBracketIsAlwaysTheRightWayRound:
    """The invariant that matters more than any single case.

    A review found a LONG whose take-profit sat BELOW its stop-loss, and it
    cleared the 1.5R gate because the reward was computed with abs(): a target
    far enough on the wrong side scored better the more wrong it was. Only
    TradePlan.validate stood between that and the exchange.

    One property covers the whole class of bug, so a future variant cannot
    reappear through a different route.
    """

    def _perturbations(self):
        """Series that all still fire, reached by ordinary price action.

        The equal-high case is the one that produced the original defect:
        _confirmed_swing uses a strict comparison, so two adjacent bars sharing
        a high disqualify the rally top as a pivot, and the scan then walks
        back to an older and lower one.
        """
        plain = long_setup()

        equal_high = long_setup()
        equal_high[39] = candle(101.93, 101.94, 101.87)
        equal_high[40] = candle(101.94, 101.95, 101.88)
        equal_high[55] = candle(103.20, 103.90, 103.05)  # ties bar 54

        deep_sweep = long_setup()
        deep_sweep[61] = candle(102.90, 102.98, 102.10, volume=3400.0)

        return [plain, equal_high, deep_sweep]

    def test_a_long_bracket_always_straddles_the_entry(self):
        entry = Decimal("102.90")
        for bars in self._perturbations():
            if not STRATEGY.assess(context(bars)).ready:
                continue
            levels = STRATEGY.levels(context(bars), entry, "LONG")
            assert levels.stop < entry < levels.target, levels

    def test_a_short_bracket_always_straddles_the_entry(self):
        entry = Decimal("97.10")
        bars = short_setup()
        if STRATEGY.assess(context(bars)).ready:
            levels = STRATEGY.levels(context(bars), entry, "SHORT")
            assert levels.target < entry < levels.stop, levels

    def test_an_older_lower_swing_high_is_not_used_as_a_long_target(self):
        # The exact reproduction from the review. Before the fix this returned
        # target 101.96 against a 102.90 entry and a 102.30 stop: a take-profit
        # below the stop-loss, reported to the operator as a ready setup.
        bars = long_setup()
        bars[39] = candle(101.93, 101.94, 101.87)
        bars[40] = candle(101.94, 101.95, 101.88)
        bars[55] = candle(103.20, 103.90, 103.05)

        setup = STRATEGY.assess(context(bars))
        if setup.ready:
            levels = STRATEGY.levels(context(bars), Decimal("102.90"), "LONG")
            assert levels.target > Decimal("102.90")

    def test_reward_is_signed_so_a_wrong_side_target_cannot_pass_the_ratio(self):
        # abs() made a target 0.94 BELOW the entry score 1.57R and pass. The
        # further wrong it was, the more confidently it passed.
        bars = long_setup()
        bars[39] = candle(101.93, 101.94, 101.87)
        bars[40] = candle(101.94, 101.95, 101.88)
        bars[55] = candle(103.20, 103.90, 103.05)
        setup = STRATEGY.assess(context(bars))
        assert setup.ready is False


class TestTheTriggerBarCannotSpendTheZone:
    """The one bar the fill check used to omit.

    The zone is unfilled while no later bar has closed beyond its far edge.
    The trigger bar is a later bar and its close is the entry price, so a bar
    that closes through the imbalance spends it at the very moment the
    strategy enters on it. Every other bar was checked; the one that matters
    most was not.
    """

    def test_a_rejection_closing_below_the_gap_floor_is_refused(self):
        bars = long_setup()
        # Sweeps 102.40, reclaims it, good wick and volume - but closes at
        # 102.55, underneath the 102.62 gap floor it claims to be defending.
        bars[61] = candle(102.55, 102.60, 102.38, volume=3400.0)
        assert STRATEGY.assess(context(bars)).ready is False

    def test_a_rejection_closing_back_inside_the_zone_still_qualifies(self):
        # The counterpart. Closing back INTO the band is the normal case and
        # must keep working; only closing through its far edge spends it.
        assert STRATEGY.assess(context(long_setup())).ready is True


class TestNotesDescribeTheActualDirection:
    """An operator-facing note that says the wrong thing about a short.

    The scanner exists to be tuned against its own explanations. A note
    reporting a level swept "beneath" an imbalance when it was swept from
    above sends whoever is tuning it looking in the wrong place.
    """

    def test_the_short_note_does_not_claim_the_sweep_was_from_below(self):
        note = STRATEGY.assess(context(short_setup())).note
        assert "beneath" not in note.lower()

    def test_the_short_note_names_a_high_rather_than_a_low(self):
        bars = short_setup()
        bars[57] = candle(97.28, 97.30, 97.05)  # remove the swing high
        note = STRATEGY.assess(context(bars)).note
        assert "low" not in note.lower() or "high" in note.lower()


class TestTheSwingDistanceBoundIsReal:
    """The review found this bound had no effective coverage.

    Its test rejected for a different reason entirely - the low it moved also
    broke the higher-lows trend condition - so deleting the bound would have
    left the whole suite green.
    """

    def _with_swing_at(self, low: float):
        # Only the swing low moves; the trend structure around it is untouched,
        # so the trend filter cannot be what rejects this. The sweep is kept
        # shallow (two ticks under the level) so the reward-to-risk gate is not
        # what rejects it either - an earlier version of this fixture swept
        # deep enough to fail on 1.25R, which would have let the bound be
        # deleted without any test noticing.
        bars = long_setup()
        bars[57] = candle(102.72, 102.95, low)
        bars[61] = candle(102.90, 102.98, low - 0.02, volume=3400.0)
        return bars

    def test_a_swing_just_beyond_the_edge_is_accepted(self):
        # 102.62 gap floor, swing at 102.35: 0.26% beyond, inside the 0.5%.
        assert STRATEGY.assess(context(self._with_swing_at(102.35))).ready is True

    def test_a_swing_past_the_bound_is_rejected_for_that_reason(self):
        # 102.10 is 0.51% beyond the edge. Asserting the NOTE, not just the
        # verdict: the previous version of this test passed on the trend
        # filter and would have survived the bound being deleted.
        setup = STRATEGY.assess(context(self._with_swing_at(102.10)))
        assert setup.ready is False
        assert "resting just beyond" in setup.note
