"""Non-finite values from the exchange.

NaN defeats every comparison. `nan < x` and `nan > x` are both False, so a
filter written as a comparison silently passes it through. A symbol with a NaN
volume would clear the liquidity minimum, clear the spread ceiling, and then
sort to an arbitrary position in a list the operator uses to choose trades.

Infinity is the mirror image: it clears every minimum and fails every maximum.

Both must be rejected where they enter, not guarded against at each of the
dozen places they would later be compared.
"""

import math

import pytest

from scalper.exchange.market_data import BookTop, Candle, Ticker, finite
from scalper.scanner.config import ScanConfig
from scalper.scanner.scan import liquidity_stage

CONFIG = ScanConfig()


class TestFiniteGuard:
    def test_ordinary_numbers_pass(self):
        assert finite("123.45") == 123.45

    def test_nan_is_refused(self):
        with pytest.raises(ValueError, match="finite"):
            finite("NaN")

    def test_infinity_is_refused(self):
        with pytest.raises(ValueError, match="finite"):
            finite("Infinity")

    def test_negative_infinity_is_refused(self):
        with pytest.raises(ValueError, match="finite"):
            finite("-Infinity")

    def test_nonsense_is_refused(self):
        with pytest.raises(ValueError):
            finite("not-a-number")

    def test_none_is_refused(self):
        with pytest.raises(ValueError):
            finite(None)

    def test_a_positive_only_field_refuses_zero(self):
        with pytest.raises(ValueError, match="positive"):
            finite("0", positive=True)

    def test_a_positive_only_field_refuses_negatives(self):
        with pytest.raises(ValueError, match="positive"):
            finite("-5", positive=True)

    def test_zero_is_allowed_where_it_is_meaningful(self):
        assert finite("0") == 0.0


class TestCandleRejectsNonFinite:
    def test_a_nan_close_is_refused_rather_than_parsed(self):
        raw = [1, "1", "1", "1", "NaN", "1", 2, "1", 0, "1", "1", "0"]
        with pytest.raises(ValueError):
            Candle.from_raw(raw)

    def test_a_non_positive_price_is_refused(self):
        raw = [1, "1", "1", "1", "0", "1", 2, "1", 0, "1", "1", "0"]
        with pytest.raises(ValueError):
            Candle.from_raw(raw)

    def test_zero_volume_is_allowed_because_quiet_bars_are_real(self):
        raw = [1, "1", "1", "1", "1", "0", 2, "0", 0, "0", "0", "0"]
        assert Candle.from_raw(raw).volume == 0.0


class TestLiquidityStageWithPoisonedData:
    def test_a_nan_volume_does_not_slip_past_the_minimum(self):
        # Without a finiteness check this symbol passes every gate, because
        # every comparison against NaN is False.
        tickers = {"BADUSDT": Ticker("BADUSDT", 100.0, 0.0, float("nan"))}
        books = {"BADUSDT": BookTop("BADUSDT", 99.99, 100.01)}
        kept, rejections = liquidity_stage(tickers, books, {"BADUSDT"}, CONFIG)
        assert kept == []
        assert any("BADUSDT" == r.symbol for r in rejections)

    def test_a_nan_spread_does_not_slip_past_the_ceiling(self):
        tickers = {"BADUSDT": Ticker("BADUSDT", 100.0, 0.0, 900_000_000.0)}
        books = {"BADUSDT": BookTop("BADUSDT", float("nan"), 100.01)}
        kept, _ = liquidity_stage(tickers, books, {"BADUSDT"}, CONFIG)
        assert kept == []

    def test_infinite_volume_does_not_rank_first(self):
        tickers = {
            "GOODUSDT": Ticker("GOODUSDT", 100.0, 0.0, 900_000_000.0),
            "BADUSDT": Ticker("BADUSDT", 100.0, 0.0, math.inf),
        }
        books = {
            "GOODUSDT": BookTop("GOODUSDT", 99.99, 100.01),
            "BADUSDT": BookTop("BADUSDT", 99.99, 100.01),
        }
        kept, _ = liquidity_stage(tickers, books, set(tickers), CONFIG)
        assert kept == ["GOODUSDT"]

    def test_clean_data_is_unaffected(self):
        tickers = {"GOODUSDT": Ticker("GOODUSDT", 100.0, 0.0, 900_000_000.0)}
        books = {"GOODUSDT": BookTop("GOODUSDT", 99.99, 100.01)}
        kept, _ = liquidity_stage(tickers, books, {"GOODUSDT"}, CONFIG)
        assert kept == ["GOODUSDT"]
