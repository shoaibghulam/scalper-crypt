"""The offline candle store and the archive parser.

A backtest is only as honest as the candles under it. The store's job is to
refuse anything it cannot vouch for rather than quietly hand back a short or
gapped series, because a missing week does not look like an error downstream -
it looks like a strategy that traded less.
"""

from datetime import date

import numpy as np
import pytest

from scalper.research.fast import signals as signal_cache
from scalper.research.fast import store
from scalper.research.fast.archive import (
    columns_to_candles,
    daily_url,
    monthly_url,
    outstanding_months,
    parse_klines,
)
from scalper.research.fast.store import CANDLE_DTYPE, DataGap, Month

MINUTE = 60_000
AUGUST = Month(2025, 8)
AUGUST_CANDLES = 44_640
HEADER = (
    "open_time,open,high,low,close,volume,close_time,quote_volume,"
    "count,taker_buy_volume,taker_buy_quote_volume,ignore"
)


@pytest.fixture(autouse=True)
def isolated_root(tmp_path, monkeypatch):
    """Never touch the real store: these tests write and delete whole months."""
    monkeypatch.setattr(store, "ROOT", tmp_path / "research-v2")
    monkeypatch.setattr(signal_cache, "CACHE", tmp_path / "research-v2" / "_signals")
    return tmp_path


def block(start_ms: int, count: int, price: float = 100.0) -> np.ndarray:
    bars = np.empty(count, dtype=CANDLE_DTYPE)
    bars["open_time"] = np.arange(count, dtype=np.int64) * MINUTE + start_ms
    bars["open"] = price
    bars["high"] = price + 1
    bars["low"] = price - 1
    bars["close"] = price
    bars["volume"] = 10.0
    bars["quote_volume"] = price * 10
    bars["taker_buy_base"] = 5.0
    return bars


def full_month(month: Month = AUGUST) -> np.ndarray:
    return block(month.start_ms, (month.end_ms - month.start_ms) // MINUTE)


def kline_row(at: int) -> str:
    return f"{at},100.0,101.0,99.0,100.5,7.5,{at + 59999},750.0,12,4.25,425.0,0"


class TestMonths:
    def test_a_range_lists_every_month_it_touches(self):
        months = store.months_covering(Month(2025, 11).start_ms, Month(2026, 2).start_ms)
        assert [m.label for m in months] == ["2025-11", "2025-12", "2026-01"]

    def test_december_rolls_into_january(self):
        assert Month(2025, 12).next == Month(2026, 1)

    def test_a_backwards_range_is_refused(self):
        with pytest.raises(ValueError):
            store.months_covering(AUGUST.end_ms, AUGUST.start_ms)


class TestValidation:
    def test_a_gap_is_refused_rather_than_closed_up(self):
        # Deleting a bar leaves the series shorter but still sorted. Without
        # this check it would silently become "a month with less trading".
        bars = np.delete(full_month(), 5000)
        with pytest.raises(DataGap, match="consecutive"):
            store.validate(bars, "1m")

    def test_an_impossible_candle_is_refused(self):
        bars = full_month()
        bars["low"][10] = bars["high"][10] + 5
        with pytest.raises(ValueError, match="OHLC"):
            store.validate(bars, "1m")

    def test_a_non_finite_price_is_refused(self):
        bars = full_month()
        bars["close"][3] = np.inf
        with pytest.raises(ValueError, match="non-finite"):
            store.validate(bars, "1m")


class TestWritingMonths:
    def test_a_complete_month_round_trips(self):
        store.write_month("BTCUSDT", "1m", AUGUST, full_month())
        loaded = store.load_range("BTCUSDT", "1m", AUGUST.start_ms, AUGUST.end_ms)
        assert len(loaded) == AUGUST_CANDLES
        assert store.digest(loaded) == store.digest(full_month())

    def test_a_short_month_is_refused_as_final(self):
        with pytest.raises(DataGap, match="incomplete"):
            store.write_month("BTCUSDT", "1m", AUGUST, block(AUGUST.start_ms, 100))

    def test_the_month_in_progress_may_be_stored_as_partial(self):
        store.write_month("BTCUSDT", "1m", AUGUST, block(AUGUST.start_ms, 100), complete=False)
        assert store.path_for("BTCUSDT", "1m", AUGUST).exists()

    def test_a_partial_month_is_not_reported_as_held(self):
        # The download step asks what it can skip. Reporting a month that is
        # still filling up would freeze the store at whatever it first saw.
        store.write_month("BTCUSDT", "1m", AUGUST, block(AUGUST.start_ms, 100), complete=False)
        assert store.stored_months("BTCUSDT", "1m") == set()
        store.write_month("BTCUSDT", "1m", AUGUST, full_month())
        assert store.stored_months("BTCUSDT", "1m") == {"2025-08"}

    def test_a_month_starting_late_is_refused(self):
        with pytest.raises(DataGap, match="first of the month"):
            store.write_month(
                "BTCUSDT", "1m", AUGUST, block(AUGUST.start_ms + MINUTE, AUGUST_CANDLES - 1)
            )


class TestLoading:
    def test_a_missing_month_names_itself(self):
        with pytest.raises(DataGap, match="2025-08"):
            store.load_range("BTCUSDT", "1m", AUGUST.start_ms, AUGUST.end_ms)

    def test_a_partial_month_short_of_the_window_is_refused(self):
        # The honest failure. Returning what exists would report a fortnight
        # as though it were a month.
        store.write_month("BTCUSDT", "1m", AUGUST, block(AUGUST.start_ms, 20000), complete=False)
        with pytest.raises(DataGap, match=f"of {AUGUST_CANDLES} candles"):
            store.load_range("BTCUSDT", "1m", AUGUST.start_ms, AUGUST.end_ms)

    def test_a_window_inside_a_stored_month_is_exact(self):
        store.write_month("BTCUSDT", "1m", AUGUST, full_month())
        start = AUGUST.start_ms + 1000 * MINUTE
        window = store.load_range("BTCUSDT", "1m", start, start + 500 * MINUTE)
        assert len(window) == 500
        assert int(window["open_time"][0]) == start


class TestCandleConversion:
    def test_rows_become_the_tuples_replay_expects(self):
        candles = store.to_candles(block(AUGUST.start_ms, 3))
        assert len(candles) == 3
        assert candles[0].open_time == AUGUST.start_ms
        assert candles[1].open_time - candles[0].open_time == MINUTE

    def test_close_times_match_what_replay_validates(self):
        # replay refuses candles whose close is not open + 59999, so a wrong
        # close time here would reject every stored month.
        candles = store.to_candles(block(AUGUST.start_ms, 5))
        assert all(c.close_time == c.open_time + 59999 for c in candles)


class TestArchiveParsing:
    def test_a_header_row_is_skipped(self):
        bars = parse_klines("\n".join([HEADER, kline_row(0), kline_row(MINUTE)]))
        assert len(bars) == 2
        assert int(bars["open_time"][0]) == 0

    def test_an_archive_without_a_header_still_parses(self):
        assert len(parse_klines(kline_row(0))) == 1

    def test_taker_volume_comes_from_the_right_column(self):
        # Column 9, not the quote column beside it. Getting this wrong would
        # feed volume-sensitive strategies a number a hundred times too large.
        bars = parse_klines(kline_row(0))
        assert float(bars["taker_buy_base"][0]) == 4.25
        assert float(bars["quote_volume"][0]) == 750.0

    def test_a_ragged_row_is_refused(self):
        with pytest.raises(ValueError, match="columns"):
            parse_klines("1,2,3")

    def test_a_non_numeric_field_is_refused(self):
        with pytest.raises(ValueError, match="non-numeric"):
            parse_klines(kline_row(0).replace("100.0", "not-a-price", 1))

    def test_an_empty_archive_is_refused(self):
        with pytest.raises(ValueError, match="no candles"):
            parse_klines(HEADER)

    def test_columns_map_in_kline_order(self):
        table = np.array([[0, 1, 2, 0.5, 1.5, 9, 59999, 90, 3, 4, 40, 0]], dtype=np.float64)
        bars = columns_to_candles(table)
        assert (float(bars["open"][0]), float(bars["high"][0]), float(bars["low"][0])) == (
            1.0,
            2.0,
            0.5,
        )


class TestUrls:
    def test_monthly_points_at_the_futures_archive(self):
        assert monthly_url("btcusdt", "1m", AUGUST).endswith(
            "/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2025-08.zip"
        )

    def test_daily_points_at_the_futures_archive(self):
        assert daily_url("btcusdt", "1m", date(2025, 8, 15)).endswith(
            "/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2025-08-15.zip"
        )


class TestOutstandingMonths:
    def test_a_stored_final_month_needs_nothing(self):
        store.write_month("BTCUSDT", "1m", AUGUST, full_month())
        assert outstanding_months("BTCUSDT", "1m", AUGUST.start_ms, AUGUST.end_ms) == []

    def test_a_partial_month_that_already_reaches_the_end_is_reused(self):
        # This is what makes a same-day rerun free rather than re-downloading
        # the month in progress every single time.
        store.write_month("BTCUSDT", "1m", AUGUST, block(AUGUST.start_ms, 20000), complete=False)
        end = AUGUST.start_ms + 20000 * MINUTE
        assert outstanding_months("BTCUSDT", "1m", AUGUST.start_ms, end) == []

    def test_a_partial_month_short_of_the_end_is_refetched(self):
        store.write_month("BTCUSDT", "1m", AUGUST, block(AUGUST.start_ms, 20000), complete=False)
        end = AUGUST.start_ms + 30000 * MINUTE
        assert [m.label for m in outstanding_months("BTCUSDT", "1m", AUGUST.start_ms, end)] == [
            "2025-08"
        ]

    def test_an_absent_month_is_outstanding(self):
        assert [
            m.label for m in outstanding_months("ETHUSDT", "1m", AUGUST.start_ms, AUGUST.end_ms)
        ] == ["2025-08"]
