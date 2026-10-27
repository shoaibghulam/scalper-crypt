"""Market data parsing.

The Binance kline array is positional, so an index mistake here is silent: the
wrong field parses fine and produces plausible numbers that are simply about
something else.
"""

import pytest

from scalper.exchange.market_data import BookTop, Candle, Ticker

# open_time, open, high, low, close, volume, close_time, quote_volume,
# trades, taker_buy_base, taker_buy_quote, ignore
RAW = [
    1700000000000,
    "100.0",
    "110.0",
    "95.0",
    "105.0",
    "1000.0",
    1700000059999,
    "104000.0",
    500,
    "600.0",
    "62000.0",
    "0",
]


class TestCandleParsing:
    def test_fields_map_to_the_right_positions(self):
        candle = Candle.from_raw(RAW)
        assert candle.open == 100.0
        assert candle.high == 110.0
        assert candle.low == 95.0
        assert candle.close == 105.0
        assert candle.volume == 1000.0

    def test_taker_buy_volume_comes_from_index_nine(self):
        # Index 10 is the quote-denominated version and would parse without
        # error while meaning something entirely different.
        assert Candle.from_raw(RAW).taker_buy_base == 600.0

    def test_quote_volume_comes_from_index_seven(self):
        assert Candle.from_raw(RAW).quote_volume == 104000.0


class TestDelta:
    def test_delta_is_aggressive_buying_minus_aggressive_selling(self):
        # 600 of the 1000 traded volume was buyer-initiated, so 600 bought and
        # 400 sold, a delta of +200.
        assert Candle.from_raw(RAW).delta == 200.0

    def test_delta_pct_normalises_to_the_bar(self):
        assert Candle.from_raw(RAW).delta_pct == pytest.approx(0.2)

    def test_all_buying_reads_plus_one(self):
        raw = list(RAW)
        raw[9] = "1000.0"
        assert Candle.from_raw(raw).delta_pct == pytest.approx(1.0)

    def test_all_selling_reads_minus_one(self):
        raw = list(RAW)
        raw[9] = "0.0"
        assert Candle.from_raw(raw).delta_pct == pytest.approx(-1.0)

    def test_an_empty_bar_has_no_delta_rather_than_dividing_by_zero(self):
        raw = list(RAW)
        raw[5] = "0.0"
        raw[9] = "0.0"
        assert Candle.from_raw(raw).delta_pct == 0.0


class TestBookTop:
    def test_spread_is_reported_in_basis_points(self):
        # A one unit spread on a mid of 100 is 100 bps.
        assert BookTop("X", bid=99.5, ask=100.5).spread_bps == pytest.approx(100.0)

    def test_a_tight_spread_reads_near_zero(self):
        book = BookTop("BTCUSDT", bid=77999.9, ask=78000.1)
        assert book.spread_bps < 1.0

    def test_mid_is_the_average_of_the_two_sides(self):
        assert BookTop("X", bid=100.0, ask=102.0).mid == 101.0

    def test_a_zero_mid_does_not_divide_by_zero(self):
        assert BookTop("X", bid=0.0, ask=0.0).spread_bps == 0.0


class TestTicker:
    def test_it_carries_what_the_liquidity_filter_needs(self):
        ticker = Ticker("BTCUSDT", 78000.0, 1.5, 5_000_000_000.0)
        assert ticker.quote_volume == 5_000_000_000.0
        assert ticker.price_change_pct == 1.5
