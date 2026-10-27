"""Making the signal pass faster must not make it different.

Three optimisations sit behind these tests: validating a candle series once
instead of once per assessed bar, computing the accumulation box in a single
pass instead of rescanning forty bars per index, and computing strategies in
parallel processes instead of one after another.

All three are speed-only by construction. That is exactly the kind of claim
that quietly stops being true, so each is checked against the slow path it
replaced rather than against itself.
"""

import numpy as np
import pytest

from scalper.backtest import signal_series
from scalper.exchange.market_data import BookTop, Candle, Ticker
from scalper.research.fast import signals as signal_cache
from scalper.research.fast import store
from scalper.research.fast.store import CANDLE_DTYPE, Month
from scalper.strategies.base import Strategy, StrategyContext
from scalper.strategies.ctk import _rolling_extremes

MINUTE = 60_000
AUGUST = Month(2025, 8)
TICKER = Ticker("T", 100.0, 1.0, 5e8)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path / "research-v2")
    monkeypatch.setattr(signal_cache, "CACHE", tmp_path / "research-v2" / "_signals")


def block(count: int, price: float = 100.0) -> np.ndarray:
    """A quiet range broken by periodic pushes, so signals actually fire."""
    bars = np.empty(count, dtype=CANDLE_DTYPE)
    bars["open_time"] = np.arange(count, dtype=np.int64) * MINUTE + AUGUST.start_ms
    bars["open"] = price
    bars["high"] = price + 0.6
    bars["low"] = price - 0.6
    bars["close"] = price
    bars["volume"] = 900.0
    bars["quote_volume"] = price * 900
    bars["taker_buy_base"] = 495.0
    bars["low"][149::150] = price - 3.0
    bars["volume"][149::150] = 3200.0
    return bars


def slow_extremes(bars: list[Candle], length: int) -> tuple[list[float], list[float]]:
    """The slice-per-bar scan that the rolling window replaced."""
    highs, lows = [], []
    for index in range(len(bars)):
        window = bars[max(0, index - length + 1) : index + 1]
        highs.append(max(b.high for b in window))
        lows.append(min(b.low for b in window))
    return highs, lows


class TestTheRollingBox:
    @pytest.mark.parametrize("length", [1, 2, 7, 40, 41, 200])
    def test_it_matches_a_slice_scan_at_every_index(self, length):
        bars = store.to_candles(block(260))
        assert _rolling_extremes(bars, length) == slow_extremes(bars, length)

    def test_it_matches_on_noisy_prices(self):
        # A flat fixture would agree by accident. The deque can only differ
        # from a scan when highs and lows arrive out of order.
        rng = np.random.default_rng(11)
        bars = block(300)
        lows = 100 + rng.normal(0, 3, 300)
        bars["low"] = lows
        bars["high"] = lows + rng.uniform(0.1, 4, 300)
        bars["open"] = bars["close"] = (bars["low"] + bars["high"]) / 2
        candles = store.to_candles(bars)
        assert _rolling_extremes(candles, 40) == slow_extremes(candles, 40)

    def test_a_single_bar_window_is_the_bar_itself(self):
        bars = store.to_candles(block(20))
        highs, lows = _rolling_extremes(bars, 1)
        assert highs == [b.high for b in bars]
        assert lows == [b.low for b in bars]


class TestValidatingOnce:
    def test_only_a_validated_context_skips_the_candle_scan(self):
        bad = list(store.to_candles(block(60)))
        bad[10] = Candle(bad[10].open_time, 1.0, 0.5, 2.0, 1.0, 1.0, bad[10].close_time, 1.0, 0.5)
        book = BookTop("T", 99.0, 101.0)
        assert Strategy.data_error(StrategyContext("T", [], bad, TICKER, book)) is not None
        # Declaring the series checked is a promise the caller has to keep;
        # the point is that it is the ONLY thing that skips the scan.
        checked = StrategyContext("T", [], bad, TICKER, book, candles_validated=True)
        assert Strategy.data_error(checked) is None

    def test_the_book_is_checked_either_way(self):
        # Only the candle scan is skipped. A crossed book must still be
        # caught: it is per-context and cannot have been pre-validated.
        ctx = StrategyContext(
            "T",
            [],
            store.to_candles(block(60)),
            TICKER,
            BookTop("T", 101.0, 99.0),
            candles_validated=True,
        )
        assert Strategy.data_error(ctx) == "invalid executable bid/ask"

    def test_a_broken_series_still_fails_the_whole_pass(self):
        # signal_series validates up front. If that fails it must fall back to
        # per-bar checking rather than assume the series is clean.
        bars = block(900)
        bars["high"][400] = bars["low"][400] - 1.0
        candles = store.to_candles(bars)
        assert Strategy.validate_candles(candles) is not None
        assert signal_series(candles, strategy_id="ctk-1", start_index=500) == {}


class TestRunningInParallel:
    STRATEGIES = ("ctk-1", "ctk-1-v1", "ctk-1-v2")

    def test_parallel_and_serial_agree_exactly(self):
        bars = block(1400)
        store.write_month("BTCUSDT", "1m", AUGUST, bars, complete=False)
        candles = store.to_candles(bars)
        serial = {
            s: signal_series(candles, strategy_id=s, start_index=500) for s in self.STRATEGIES
        }
        signal_cache.clear()
        start, end = AUGUST.start_ms, AUGUST.start_ms + len(bars) * MINUTE
        errors = signal_cache.warm_many(
            "BTCUSDT", "1m", start, end, self.STRATEGIES, start_index=500, workers=2
        )
        assert errors == {}
        code = signal_cache.code_digest()
        for strategy_id in self.STRATEGIES:
            cached = signal_cache.read(
                signal_cache.cache_key(strategy_id, "BTCUSDT", "1m", bars, code)
            )
            assert cached == serial[strategy_id], strategy_id

    def test_a_second_call_does_no_work(self):
        bars = block(1000)
        store.write_month("BTCUSDT", "1m", AUGUST, bars, complete=False)
        start, end = AUGUST.start_ms, AUGUST.start_ms + len(bars) * MINUTE
        signal_cache.warm_many(
            "BTCUSDT", "1m", start, end, self.STRATEGIES, start_index=500, workers=2
        )
        seen: list[str] = []
        signal_cache.warm_many(
            "BTCUSDT",
            "1m",
            start,
            end,
            self.STRATEGIES,
            start_index=500,
            workers=2,
            progress=lambda note, done, total: seen.append(note),
        )
        assert seen == ["every signal series already cached"]

    def test_one_bad_strategy_does_not_stop_the_others(self):
        # A worker raising must be reported against its own id, not lost and
        # not fatal to the rest of the run.
        bars = block(1000)
        store.write_month("BTCUSDT", "1m", AUGUST, bars, complete=False)
        start, end = AUGUST.start_ms, AUGUST.start_ms + len(bars) * MINUTE
        errors = signal_cache.warm_many(
            "BTCUSDT", "1m", start, end, ("ctk-1", "no-such-strategy"), start_index=500, workers=2
        )
        assert set(errors) == {"no-such-strategy"}
        code = signal_cache.code_digest()
        assert (
            signal_cache.read(signal_cache.cache_key("ctk-1", "BTCUSDT", "1m", bars, code))
            is not None
        )
