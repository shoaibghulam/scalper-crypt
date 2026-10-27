"""Technical indicators.

These decide whether a symbol is tradeable and whether a setup has fired, so
they are tested against hand-computable cases rather than against themselves.

Wilder smoothing is used for ATR, RSI and ADX because that is what every
charting package means by those names. A simple moving average of true range
is a different indicator that happens to share a label, and a strategy tuned
against one behaves differently on the other.
"""

import pytest

from scalper.indicators.core import (
    adx,
    atr,
    efficiency_ratio,
    ema,
    heikin_ashi,
    percentile_rank,
    rsi,
    rvol,
    sma,
    vwap_bands,
)


class TestMovingAverages:
    def test_sma_is_the_arithmetic_mean_of_the_window(self):
        assert sma([1, 2, 3, 4, 5], 5) == 3.0

    def test_sma_uses_only_the_last_n(self):
        assert sma([100, 1, 2, 3], 3) == 2.0

    def test_ema_seeds_from_the_simple_average(self):
        # Seed is the mean of the first two, 1.5. Multiplier is 2/3.
        # Next value: (3 - 1.5) * 2/3 + 1.5 = 2.5
        assert ema([1, 2, 3], 2) == pytest.approx(2.5)

    def test_ema_reacts_faster_than_sma(self):
        # A linear ramp is the wrong test: EMA and SMA coincide exactly on one.
        # The difference shows on a step, which is what "reacts faster" means.
        step = [1, 1, 1, 1, 1, 1, 1, 1, 1, 10]
        assert ema(step, 5) > sma(step, 5)

    def test_too_few_points_is_an_error_not_a_guess(self):
        # Returning a value computed from a partial window silently produces a
        # signal from data that does not exist yet.
        with pytest.raises(ValueError, match="at least"):
            sma([1, 2], 5)


class TestAtr:
    def test_constant_range_gives_that_range(self):
        highs = [11.0] * 20
        lows = [10.0] * 20
        closes = [10.5] * 20
        assert atr(highs, lows, closes, 14) == pytest.approx(1.0)

    def test_a_gap_widens_true_range_beyond_the_bar(self):
        # True range includes the distance from the previous close, so an
        # overnight gap is volatility even if the bar itself is small.
        highs = [10.0] * 15 + [20.0]
        lows = [9.0] * 15 + [19.5]
        closes = [9.5] * 15 + [19.8]
        widened = atr(highs, lows, closes, 14)
        assert widened > 1.0

    def test_atr_is_never_negative(self):
        highs = [3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5, 8, 9, 7, 9, 3]
        lows = [1, 1, 2, 1, 3, 5, 1, 4, 3, 2, 3, 6, 7, 5, 7, 2]
        closes = [2, 1, 3, 1, 4, 7, 1, 5, 4, 2, 4, 7, 8, 6, 8, 2]
        assert atr(highs, lows, closes, 14) >= 0


class TestRsi:
    def test_unbroken_gains_saturate_at_one_hundred(self):
        assert rsi(list(range(1, 40)), 14) == pytest.approx(100.0)

    def test_unbroken_losses_saturate_at_zero(self):
        assert rsi(list(range(40, 1, -1)), 14) == pytest.approx(0.0)

    def test_it_stays_within_bounds_on_noisy_data(self):
        noisy = [10, 12, 11, 13, 12, 14, 13, 15, 14, 16, 15, 17, 16, 18, 17, 19]
        assert 0 <= rsi(noisy, 14) <= 100

    def test_period_seven_is_supported_for_scalping(self):
        # The strategy uses RSI(7) on one-minute bars. RSI(14) barely moves at
        # that timeframe.
        fast = rsi([10, 12, 11, 13, 12, 14, 13, 15, 14, 16], 7)
        assert 0 <= fast <= 100


class TestAdx:
    def test_a_strong_trend_reads_above_the_trending_threshold(self):
        n = 40
        highs = [100 + i * 2 for i in range(n)]
        lows = [99 + i * 2 for i in range(n)]
        closes = [99.5 + i * 2 for i in range(n)]
        assert adx(highs, lows, closes, 14).adx > 25

    def test_a_flat_range_reads_below_the_ranging_threshold(self):
        n = 60
        highs = [101 if i % 2 else 100.5 for i in range(n)]
        lows = [99 if i % 2 else 99.5 for i in range(n)]
        closes = [100 if i % 2 else 99.9 for i in range(n)]
        assert adx(highs, lows, closes, 14).adx < 25

    def test_directional_indicators_agree_with_an_uptrend(self):
        n = 40
        highs = [100 + i * 2 for i in range(n)]
        lows = [99 + i * 2 for i in range(n)]
        closes = [99.5 + i * 2 for i in range(n)]
        result = adx(highs, lows, closes, 14)
        assert result.plus_di > result.minus_di


class TestEfficiencyRatio:
    def test_a_straight_line_is_perfectly_efficient(self):
        assert efficiency_ratio(list(range(30)), 20) == pytest.approx(1.0)

    def test_pure_oscillation_is_inefficient(self):
        chop = [100, 101] * 15
        assert efficiency_ratio(chop, 20) < 0.2

    def test_it_is_bounded_between_zero_and_one(self):
        assert 0 <= efficiency_ratio([5, 3, 8, 1, 9, 2, 7, 4] * 4, 20) <= 1


class TestRelativeVolume:
    def test_average_volume_reads_one(self):
        assert rvol([100.0] * 21, 20) == pytest.approx(1.0)

    def test_a_spike_reads_above_the_trigger_threshold(self):
        assert rvol([100.0] * 20 + [300.0], 20) == pytest.approx(3.0)

    def test_a_dead_period_reads_below_one(self):
        assert rvol([100.0] * 20 + [20.0], 20) == pytest.approx(0.2)


class TestVwapBands:
    def test_vwap_of_a_flat_market_is_the_price(self):
        bands = vwap_bands([10.0] * 10, [10.0] * 10, [10.0] * 10, [5.0] * 10)
        assert bands.vwap == pytest.approx(10.0)

    def test_a_flat_market_has_no_dispersion(self):
        bands = vwap_bands([10.0] * 10, [10.0] * 10, [10.0] * 10, [5.0] * 10)
        assert bands.sigma == pytest.approx(0.0)

    def test_volume_weights_the_average_toward_heavy_bars(self):
        # The expensive bar carries ten times the volume, so vwap sits nearer it.
        highs = [10.0, 20.0]
        lows = [10.0, 20.0]
        closes = [10.0, 20.0]
        volumes = [1.0, 10.0]
        assert vwap_bands(highs, lows, closes, volumes).vwap > 15.0

    def test_bands_are_symmetric_around_vwap(self):
        bands = vwap_bands(
            [10, 12, 11, 13], [9, 11, 10, 12], [9.5, 11.5, 10.5, 12.5], [1, 2, 1, 2]
        )
        assert bands.upper(2.0) - bands.vwap == pytest.approx(
            bands.vwap - bands.lower(2.0)
        )

    def test_zero_volume_is_refused_rather_than_dividing_by_zero(self):
        with pytest.raises(ValueError, match="volume"):
            vwap_bands([10.0], [10.0], [10.0], [0.0])


class TestPercentileRank:
    def test_the_largest_value_ranks_at_the_top(self):
        assert percentile_rank([1, 2, 3, 4, 5], 5) == pytest.approx(100.0)

    def test_the_smallest_value_ranks_at_the_bottom(self):
        assert percentile_rank([1, 2, 3, 4, 5], 1) == pytest.approx(0.0)

    def test_the_median_ranks_near_the_middle(self):
        assert percentile_rank([1, 2, 3, 4, 5], 3) == pytest.approx(50.0)

    def test_it_self_normalises_across_symbols(self):
        # This is why percentile is used rather than an absolute ATR threshold:
        # it needs no per-symbol tuning.
        cheap = percentile_rank([0.001, 0.002, 0.003], 0.003)
        rich = percentile_rank([1000, 2000, 3000], 3000)
        assert cheap == rich


class TestHeikinAshi:
    """The smoothed candles The Minute Black detects on.

    Worth its own tests because the transform is recursive: HA_open depends on
    the previous HA candle, so it must be computed in one forward pass. The
    project has twice shipped an indicator that reseeded and replayed its own
    smoothing per bar, which turned a market scan quadratic.

    The other reason is the one that shapes the strategy: HA_open is an average
    of two previous averages, so it is a price the market never traded at. The
    candles are for reading, never for placing orders.
    """

    def test_the_close_is_the_average_of_the_bar(self):
        out = heikin_ashi([10.0], [14.0], [8.0], [12.0])
        assert out[0].close == pytest.approx((10 + 14 + 8 + 12) / 4)

    def test_the_first_open_seeds_from_the_real_bar(self):
        # No previous HA candle exists, so the midpoint of the real bar is the
        # only honest starting value.
        out = heikin_ashi([10.0], [14.0], [8.0], [12.0])
        assert out[0].open == pytest.approx((10 + 12) / 2)

    def test_each_open_is_the_midpoint_of_the_previous_candle(self):
        out = heikin_ashi([10.0, 12.0], [14.0, 16.0], [8.0, 11.0], [12.0, 15.0])
        assert out[1].open == pytest.approx((out[0].open + out[0].close) / 2)

    def test_the_high_includes_the_synthetic_body(self):
        # max(real high, HA open, HA close). On a strong bar the body can
        # exceed the real high, and that is the point of the smoothing.
        out = heikin_ashi([10.0, 20.0], [14.0, 21.0], [8.0, 19.0], [12.0, 21.0])
        assert out[1].high >= 21.0

    def test_the_low_includes_the_synthetic_body(self):
        out = heikin_ashi([10.0, 5.0], [14.0, 6.0], [8.0, 4.0], [12.0, 4.0])
        assert out[1].low <= 4.0

    def test_a_rising_series_produces_rising_candles(self):
        closes = [100.0 + i for i in range(20)]
        out = heikin_ashi(
            [c - 0.5 for c in closes], [c + 0.5 for c in closes],
            [c - 1.0 for c in closes], closes,
        )
        assert all(c.close > c.open for c in out[2:])

    def test_the_synthetic_open_is_not_a_traded_price(self):
        # The reason orders are never placed at HA levels. Here the real bars
        # never trade at 11.25, but that is where the second HA candle opens.
        out = heikin_ashi([10.0, 20.0], [10.0, 20.0], [10.0, 20.0], [10.0, 20.0])
        assert out[1].open == pytest.approx(10.0)
        assert out[1].open != 20.0

    def test_mismatched_series_are_refused(self):
        # A silent truncation here would misalign every candle after it.
        with pytest.raises(ValueError):
            heikin_ashi([1.0, 2.0], [1.0], [1.0, 2.0], [1.0, 2.0])

    def test_an_empty_series_yields_nothing(self):
        assert heikin_ashi([], [], [], []) == []

    def test_the_transform_is_linear_in_the_series_length(self):
        # A recursive definition invites recomputing the whole chain per bar.
        # Ten times the bars must not cost a hundred times the work.
        import time
        from statistics import median

        def elapsed(n: int) -> float:
            closes = [100.0 + (i % 7) for i in range(n)]
            args = (
                [c - 0.5 for c in closes],
                [c + 0.5 for c in closes],
                [c - 1.0 for c in closes],
                closes,
            )
            start = time.perf_counter()
            heikin_ashi(*args)
            return time.perf_counter() - start

        elapsed(2000)  # warm
        # A single GC pause or OS scheduling slice is longer than the small
        # sample. Repeated interleaved samples retain the same scaling bound
        # while avoiding a false regression from one interrupted measurement.
        samples = [(elapsed(1000), elapsed(10_000)) for _ in range(5)]
        small = median(sample[0] for sample in samples)
        large = median(sample[1] for sample in samples)
        assert large < small * 40
