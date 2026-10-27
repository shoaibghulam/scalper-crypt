"""Scanner filtering and ranking.

The distinction this file exists to protect: economic gates reject, timing
signals do not. A symbol whose stop cannot clear its own fees can never be
traded profitably and is excluded. A symbol that is merely trending right now
is shown, because it will not be trending later, and hiding it leaves an empty
screen that teaches the operator nothing.

Getting that backwards produced a scanner that returned zero rows against a
live market, which is how the distinction was found.
"""

from scalper.exchange.market_data import BookTop, Candle, Ticker
from scalper.scanner.config import Regime, ScanConfig
from scalper.scanner.evaluate import (
    Candidate,
    Rejection,
    assess_setup,
    classify_regime,
    evaluate_symbol,
    score_candidate,
)
from scalper.scanner.scan import liquidity_stage

CONFIG = ScanConfig()


def candle(close: float, high: float, low: float, volume: float = 1000.0) -> Candle:
    return Candle(
        open_time=0,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=volume,
        close_time=0,
        quote_volume=close * volume,
        taker_buy_base=volume * 0.5,
    )


def volatile_series(count: int, base: float = 100.0, swing: float = 1.0) -> list[Candle]:
    """Bars with enough range to clear the ATR floor."""
    out = []
    for i in range(count):
        close = base + (swing if i % 2 else -swing)
        out.append(candle(close, close + swing, close - swing))
    return out


class TestRegimeClassification:
    def test_strong_and_direct_is_a_trend(self):
        assert classify_regime(30.0, 0.5, CONFIG) is Regime.TREND

    def test_weak_and_aimless_is_a_range(self):
        assert classify_regime(12.0, 0.1, CONFIG) is Regime.RANGE

    def test_disagreement_is_neither(self):
        # High ADX with a low efficiency ratio means price travelled a long way
        # to go nowhere. Neither strategy family handles that well.
        assert classify_regime(35.0, 0.05, CONFIG) is Regime.NO_TRADE

    def test_the_middle_band_is_deliberately_no_trade(self):
        assert classify_regime(21.0, 0.28, CONFIG) is Regime.NO_TRADE


class TestSetupAssessment:
    def test_a_stretched_price_in_a_range_is_ready(self):
        ready, note = assess_setup(
            regime=Regime.RANGE,
            sigma_distance=-2.4,
            rsi_fast=15.0,
            rvol_5m=2.0,
            config=CONFIG,
        )
        assert ready is True
        assert "sigma" in note

    def test_a_trend_is_never_ready_for_mean_reversion(self):
        ready, note = assess_setup(
            regime=Regime.TREND,
            sigma_distance=-3.0,
            rsi_fast=10.0,
            rvol_5m=5.0,
            config=CONFIG,
        )
        assert ready is False
        assert "trending" in note

    def test_two_sigma_is_the_threshold_and_is_not_loosened(self):
        # Loosening to 1.5 sigma roughly triples the signal count and halves
        # the edge, which is the classic way this strategy gets ruined.
        ready, _ = assess_setup(
            regime=Regime.RANGE,
            sigma_distance=-1.9,
            rsi_fast=15.0,
            rvol_5m=2.0,
            config=CONFIG,
        )
        assert ready is False

    def test_rsi_must_confirm_the_stretch(self):
        ready, _ = assess_setup(
            regime=Regime.RANGE,
            sigma_distance=-2.5,
            rsi_fast=45.0,
            rvol_5m=2.0,
            config=CONFIG,
        )
        assert ready is False

    def test_a_dead_market_is_not_faded(self):
        ready, note = assess_setup(
            regime=Regime.RANGE,
            sigma_distance=-2.5,
            rsi_fast=15.0,
            rvol_5m=0.4,
            config=CONFIG,
        )
        assert ready is False
        assert "quiet" in note

    def test_the_short_side_mirrors_the_long(self):
        ready, _ = assess_setup(
            regime=Regime.RANGE,
            sigma_distance=2.4,
            rsi_fast=85.0,
            rvol_5m=2.0,
            config=CONFIG,
        )
        assert ready is True


class TestEconomicGatesReject:
    def _evaluate(self, candles_5m, candles_1m):
        return evaluate_symbol(
            "TESTUSDT",
            Ticker("TESTUSDT", 100.0, 1.0, 200_000_000.0),
            BookTop("TESTUSDT", 99.99, 100.01),
            candles_5m,
            candles_1m,
            CONFIG,
        )

    def test_insufficient_history_is_rejected(self):
        outcome = self._evaluate(volatile_series(10), volatile_series(10))
        assert isinstance(outcome, Rejection)
        assert outcome.stage == "data"

    def test_a_symbol_too_quiet_for_its_fees_is_rejected(self):
        # A near-flat series produces an ATR below the floor, which means the
        # stop would be smaller than the round-trip cost.
        flat = [candle(100.0, 100.001, 99.999) for _ in range(120)]
        outcome = self._evaluate(flat, flat)
        assert isinstance(outcome, Rejection)
        assert outcome.stage == "volatility"
        assert "fees" in outcome.reason

    def test_a_chaotic_symbol_is_rejected(self):
        wild = volatile_series(120, base=100.0, swing=8.0)
        outcome = self._evaluate(wild, wild)
        assert isinstance(outcome, Rejection)
        assert outcome.stage == "volatility"


class TestTimingDoesNotReject:
    def _outcome(self):
        series = volatile_series(120, base=100.0, swing=0.6)
        return evaluate_symbol(
            "TESTUSDT",
            Ticker("TESTUSDT", 100.0, 1.0, 200_000_000.0),
            BookTop("TESTUSDT", 99.99, 100.01),
            series,
            series,
            CONFIG,
        )

    def test_a_tradeable_symbol_survives_regardless_of_regime(self):
        outcome = self._outcome()
        # Whatever the regime turns out to be, the symbol is returned with its
        # reasoning attached rather than filtered away.
        assert isinstance(outcome, Candidate)
        assert outcome.setup_note != ""
        assert outcome.regime in set(Regime)

    def test_a_candidate_carries_its_economics(self):
        outcome = self._outcome()
        assert isinstance(outcome, Candidate)
        assert outcome.stop_distance_pct >= CONFIG.min_stop_fraction * 100
        assert outcome.cost_to_stop <= CONFIG.max_cost_to_stop
        assert 0 < outcome.breakeven_win_rate < 1


class TestLiquidityStage:
    def _inputs(self):
        tickers = {
            "BIGUSDT": Ticker("BIGUSDT", 100.0, 1.0, 900_000_000.0),
            "MIDUSDT": Ticker("MIDUSDT", 10.0, 1.0, 100_000_000.0),
            "TINYUSDT": Ticker("TINYUSDT", 1.0, 1.0, 1_000_000.0),
            "WIDEUSDT": Ticker("WIDEUSDT", 50.0, 1.0, 500_000_000.0),
        }
        books = {
            "BIGUSDT": BookTop("BIGUSDT", 99.99, 100.01),
            "MIDUSDT": BookTop("MIDUSDT", 9.999, 10.001),
            "TINYUSDT": BookTop("TINYUSDT", 0.999, 1.001),
            # Half a percent wide, which is fatal at this timeframe.
            "WIDEUSDT": BookTop("WIDEUSDT", 49.875, 50.125),
        }
        return tickers, books, set(tickers)

    def test_thin_symbols_are_dropped(self):
        tickers, books, tradeable = self._inputs()
        kept, rejections = liquidity_stage(tickers, books, tradeable, CONFIG)
        assert "TINYUSDT" not in kept
        assert any(r.symbol == "TINYUSDT" for r in rejections)

    def test_wide_spreads_are_dropped(self):
        tickers, books, tradeable = self._inputs()
        kept, rejections = liquidity_stage(tickers, books, tradeable, CONFIG)
        assert "WIDEUSDT" not in kept
        reason = next(r.reason for r in rejections if r.symbol == "WIDEUSDT")
        assert "spread" in reason

    def test_liquid_tight_symbols_are_kept(self):
        tickers, books, tradeable = self._inputs()
        kept, _ = liquidity_stage(tickers, books, tradeable, CONFIG)
        assert "BIGUSDT" in kept
        assert "MIDUSDT" in kept

    def test_the_list_is_ordered_by_volume(self):
        tickers, books, tradeable = self._inputs()
        kept, _ = liquidity_stage(tickers, books, tradeable, CONFIG)
        assert kept[0] == "BIGUSDT"

    def test_a_symbol_without_market_data_is_rejected_not_skipped(self):
        tickers, books, _ = self._inputs()
        kept, rejections = liquidity_stage(
            tickers, books, {"BIGUSDT", "GHOSTUSDT"}, CONFIG
        )
        assert "GHOSTUSDT" not in kept
        assert any(r.symbol == "GHOSTUSDT" for r in rejections)

    def test_the_top_n_cap_is_applied(self):
        tight = ScanConfig().clone(top_n_by_volume=1)
        tickers, books, tradeable = self._inputs()
        kept, rejections = liquidity_stage(tickers, books, tradeable, tight)
        assert len(kept) == 1
        assert any("top 1" in r.reason for r in rejections)


class TestScoring:
    def _candidate(self, **overrides) -> Candidate:
        base = dict(
            symbol="X",
            price=100.0,
            quote_volume_24h=500_000_000.0,
            change_24h_pct=1.0,
            spread_bps=1.0,
            atr_pct=0.6,
            atr_percentile=70.0,
            adx=30.0,
            plus_di=25.0,
            minus_di=10.0,
            efficiency_ratio=0.5,
            rvol_5m=2.0,
            rvol_1m=2.0,
            regime=Regime.RANGE,
            vwap=100.0,
            sigma=0.5,
            sigma_distance=-1.0,
            rsi_1m=30.0,
            delta_pct=0.1,
            stop_distance_pct=0.9,
            cost_to_stop=0.1,
            breakeven_win_rate=0.37,
        )
        base.update(overrides)
        return Candidate(**base)

    def test_a_ready_setup_outranks_a_merely_tradeable_symbol(self):
        ready = self._candidate(setup_ready=True)
        waiting = self._candidate(setup_ready=False)
        assert score_candidate(ready, CONFIG) > score_candidate(waiting, CONFIG)

    def test_a_wider_spread_scores_lower(self):
        tight = self._candidate(spread_bps=0.5)
        wide = self._candidate(spread_bps=4.5)
        assert score_candidate(tight, CONFIG) > score_candidate(wide, CONFIG)

    def test_more_activity_scores_higher(self):
        busy = self._candidate(rvol_5m=3.0)
        quiet = self._candidate(rvol_5m=0.5)
        assert score_candidate(busy, CONFIG) > score_candidate(quiet, CONFIG)

    def test_extreme_volatility_percentiles_score_below_the_sweet_spot(self):
        middle = self._candidate(atr_percentile=70.0)
        extreme = self._candidate(atr_percentile=5.0)
        assert score_candidate(middle, CONFIG) > score_candidate(extreme, CONFIG)

    def test_distance_from_vwap_only_helps_a_ranging_symbol(self):
        ranging = self._candidate(regime=Regime.RANGE, sigma_distance=-2.0)
        trending = self._candidate(regime=Regime.TREND, sigma_distance=-2.0)
        assert score_candidate(ranging, CONFIG) > score_candidate(trending, CONFIG)
