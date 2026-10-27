"""A screening pass must be given time proportional to the work.

The deadline was a flat 45 seconds, which suits a watchlist of a few dozen
symbols. A fleet watching the whole exchange needs roughly 1,000 requests at
around 10 a second, so screening aborted every cycle, stamped "Market data
unavailable: TimeoutError" on every bot, and retried forever - a permanent
outage that looked like a network fault.

The concurrency bound is the other half: it was set to stay inside the request
weight budget, so it can only be raised as far as that budget allows.
"""

import pytest

from scalper.engine.screening import SCREEN_CONCURRENCY, screening_deadline
from scalper.exchange.market_limits import WEIGHT_PAUSE_AT


class TestDeadlineScalesWithWork:
    def test_a_small_watchlist_keeps_a_short_deadline(self):
        assert screening_deadline(20) <= 60

    def test_the_whole_exchange_gets_enough_time_to_finish(self):
        # 528 symbols x 2 intervals at SCREEN_CONCURRENCY in flight.
        needed = 528 * 2 / SCREEN_CONCURRENCY * 0.8
        assert screening_deadline(528) > needed

    def test_it_never_waits_without_limit(self):
        # An unbounded deadline would hide a genuinely stuck exchange.
        assert screening_deadline(100_000) <= 600

    def test_it_grows_with_the_symbol_count(self):
        assert screening_deadline(500) > screening_deadline(50)


class TestConcurrencyStaysInsideTheBudget:
    def test_a_full_pass_cannot_trip_the_pause_threshold(self):
        # Sustained weight while screening flat out: each kline costs 2, and a
        # request takes about 0.8s, so SCREEN_CONCURRENCY in flight is
        # SCREEN_CONCURRENCY/0.8 requests a second.
        per_second = SCREEN_CONCURRENCY / 0.8
        weight_per_minute = per_second * 2 * 60
        assert weight_per_minute < WEIGHT_PAUSE_AT, weight_per_minute

    def test_it_leaves_room_for_account_reads_and_orders(self):
        # Screening is not the only caller; taking the whole budget would
        # starve reconciliation and order placement.
        per_second = SCREEN_CONCURRENCY / 0.8
        assert per_second * 2 * 60 < WEIGHT_PAUSE_AT * 0.75

    def test_it_is_fast_enough_to_matter(self):
        # The old bound of 8 could not finish the exchange inside any sane
        # deadline; this test fails if it is quietly put back.
        assert SCREEN_CONCURRENCY > 8
