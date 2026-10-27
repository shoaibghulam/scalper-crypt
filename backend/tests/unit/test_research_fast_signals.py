"""The signal cache.

This is the part of v2 that can be wrong in a way nobody notices. A stale
candle store fails loudly - a month is missing and the run stops. A stale
signal cache does not: it returns plausible trades from code that no longer
exists, and the report looks entirely normal.

So these tests are mostly about invalidation: any change to the strategy, its
source, or the candles must produce a different key.
"""

import numpy as np
import pytest

from scalper.backtest import signal_series
from scalper.research.fast import signals as signal_cache
from scalper.research.fast import store
from scalper.research.fast.store import CANDLE_DTYPE

MINUTE = 60_000
STRATEGY = "ctk-1"
CODE = "a" * 64
SIGNALS = {0: "LONG", 300_000: "SHORT", 900_000: "LONG"}


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path / "research-v2")
    monkeypatch.setattr(signal_cache, "CACHE", tmp_path / "research-v2" / "_signals")


def block(count: int = 900, price: float = 100.0) -> np.ndarray:
    bars = np.empty(count, dtype=CANDLE_DTYPE)
    bars["open_time"] = np.arange(count, dtype=np.int64) * MINUTE
    bars["open"] = price
    bars["high"] = price + 0.6
    bars["low"] = price - 0.6
    bars["close"] = price
    bars["volume"] = 900.0
    bars["quote_volume"] = price * 900
    bars["taker_buy_base"] = 495.0
    # Periodic pushes, so a real strategy has something to react to.
    bars["low"][149::150] = price - 3.0
    bars["volume"][149::150] = 3200.0
    return bars


class TestRoundTrip:
    def test_a_series_survives_storage_exactly(self):
        key = signal_cache.cache_key(STRATEGY, "BTCUSDT", "1m", block(), CODE)
        signal_cache.write(key, SIGNALS)
        assert signal_cache.read(key) == SIGNALS

    def test_an_empty_series_is_kept_and_is_not_a_miss(self):
        # "This strategy never fires on this data" is a real and expensive
        # answer. Treating it as a miss would recompute the pass every run.
        key = signal_cache.cache_key(STRATEGY, "BTCUSDT", "1m", block(), CODE)
        signal_cache.write(key, {})
        assert signal_cache.read(key) == {}

    def test_an_absent_key_reads_as_a_miss(self):
        assert signal_cache.read("0" * 64) is None

    def test_a_corrupt_file_is_discarded_rather_than_raising(self):
        # A half-written cache costs one recomputation; failing the run would
        # cost the whole job.
        key = signal_cache.cache_key(STRATEGY, "BTCUSDT", "1m", block(), CODE)
        signal_cache.write(key, SIGNALS)
        signal_cache.path_for(key).write_bytes(b"not an npz")
        assert signal_cache.read(key) is None
        assert not signal_cache.path_for(key).exists()


class TestInvalidation:
    def test_different_candles_change_the_key(self):
        first = signal_cache.cache_key(STRATEGY, "BTCUSDT", "1m", block(), CODE)
        moved = block()
        moved["close"][500] += 1.0
        assert signal_cache.cache_key(STRATEGY, "BTCUSDT", "1m", moved, CODE) != first

    def test_a_longer_range_changes_the_key(self):
        first = signal_cache.cache_key(STRATEGY, "BTCUSDT", "1m", block(900), CODE)
        assert signal_cache.cache_key(STRATEGY, "BTCUSDT", "1m", block(901), CODE) != first

    def test_a_different_strategy_changes_the_key(self):
        bars = block()
        assert signal_cache.cache_key(
            "ctk-1", "BTCUSDT", "1m", bars, CODE
        ) != signal_cache.cache_key("ctk-5", "BTCUSDT", "1m", bars, CODE)

    def test_a_different_symbol_changes_the_key(self):
        bars = block()
        assert signal_cache.cache_key(
            STRATEGY, "BTCUSDT", "1m", bars, CODE
        ) != signal_cache.cache_key(STRATEGY, "ETHUSDT", "1m", bars, CODE)

    def test_changed_source_code_changes_the_key(self):
        # The one that matters most: editing a strategy must never keep
        # serving the signals its previous version produced.
        bars = block()
        assert signal_cache.cache_key(
            STRATEGY, "BTCUSDT", "1m", bars, "a" * 64
        ) != signal_cache.cache_key(STRATEGY, "BTCUSDT", "1m", bars, "b" * 64)

    def test_the_code_digest_is_stable_and_well_formed(self):
        assert len(signal_cache.code_digest()) == 64
        assert signal_cache.code_digest() == signal_cache.code_digest()


class TestSignalsFor:
    def test_the_first_call_computes_and_the_second_is_served(self):
        bars = block()
        candles = store.to_candles(bars)
        first, cached = signal_cache.signals_for(
            bars, candles, strategy_id=STRATEGY, symbol="BTCUSDT", start_index=500, code=CODE
        )
        assert cached is False
        second, cached_again = signal_cache.signals_for(
            bars, candles, strategy_id=STRATEGY, symbol="BTCUSDT", start_index=500, code=CODE
        )
        assert cached_again is True
        assert second == first

    def test_a_cached_answer_matches_a_direct_computation(self):
        # The cache is only useful if it holds the same answer, so this
        # compares against the uncached path rather than against itself.
        bars = block()
        candles = store.to_candles(bars)
        direct = signal_series(candles, strategy_id=STRATEGY, start_index=500)
        cached, _ = signal_cache.signals_for(
            bars, candles, strategy_id=STRATEGY, symbol="BTCUSDT", start_index=500, code=CODE
        )
        assert cached == direct

    def test_edited_code_recomputes_rather_than_reusing(self):
        bars = block()
        candles = store.to_candles(bars)
        signal_cache.signals_for(
            bars, candles, strategy_id=STRATEGY, symbol="BTCUSDT", start_index=500, code="a" * 64
        )
        _, cached = signal_cache.signals_for(
            bars, candles, strategy_id=STRATEGY, symbol="BTCUSDT", start_index=500, code="b" * 64
        )
        assert cached is False


class TestHousekeeping:
    def test_counting_and_clearing_report_honestly(self):
        assert signal_cache.cached_count() == 0
        for index in range(3):
            signal_cache.write(f"{index:064d}", SIGNALS)
        assert signal_cache.cached_count() == 3
        assert signal_cache.clear() == 3
        assert signal_cache.cached_count() == 0

    def test_clearing_an_absent_cache_is_not_an_error(self):
        assert signal_cache.clear() == 0
