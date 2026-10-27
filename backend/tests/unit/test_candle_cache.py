"""Closed candles are fetched far more often than they change.

A 5m candle set is identical for five minutes, and a 1m set for one. The cache
that existed was READ by `candles()` but only ever WRITTEN by
`prefetch_candles`, and its validity was keyed to the current minute whatever
the interval - so a fleet watching 500 symbols re-downloaded every 5m history
five times more often than it could possibly differ, and burned the request
weight budget doing it.
"""

import time
from unittest.mock import patch

import pytest

from scalper.exchange.market_data import MarketDataClient

ROW = [1_700_000_000_000, "1", "2", "0.5", "1.5", "10",
       1_700_000_059_999, "15", 3, "5", "7", "0"]


class Recorder(MarketDataClient):
    """A client whose only network is a counter."""

    def __init__(self) -> None:
        super().__init__("https://example.test")
        self.calls: list[tuple[str, str, int]] = []

    async def _get(self, path, params=None):  # type: ignore[override]
        params = params or {}
        self.calls.append((path, params.get("interval", ""), params.get("limit", 0)))
        # One closed row, far enough in the past to survive the close filter.
        return [ROW]


@pytest.fixture
def client() -> Recorder:
    return Recorder()


class TestRepeatFetches:
    @pytest.mark.asyncio
    async def test_the_same_minute_is_fetched_once(self, client):
        at = 1_700_000_100.0
        with patch("time.time", return_value=at):
            await client.candles("BTCUSDT", "1m", 200)
            await client.candles("BTCUSDT", "1m", 200)
        assert len(client.calls) == 1

    @pytest.mark.asyncio
    async def test_every_bot_asking_for_one_symbol_costs_one_request(self, client):
        # The fleet case: 20 bots all watching BTCUSDT in the same cycle.
        at = 1_700_000_100.0
        with patch("time.time", return_value=at):
            for _ in range(20):
                await client.candles("BTCUSDT", "1m", 200)
        assert len(client.calls) == 1

    @pytest.mark.asyncio
    async def test_a_new_minute_refetches_one_minute_candles(self, client):
        with patch("time.time", return_value=1_700_000_100.0):
            await client.candles("BTCUSDT", "1m", 200)
        with patch("time.time", return_value=1_700_000_160.0):
            await client.candles("BTCUSDT", "1m", 200)
        assert len(client.calls) == 2


class TestIntervalAwareExpiry:
    @pytest.mark.asyncio
    async def test_five_minute_candles_survive_a_new_minute(self, client):
        # 12:00:40 and 12:01:40 are inside the SAME 5m candle, so the second
        # read cannot differ and must not cost a request.
        with patch("time.time", return_value=1_700_000_140.0):
            await client.candles("BTCUSDT", "5m", 200)
        with patch("time.time", return_value=1_700_000_200.0):
            await client.candles("BTCUSDT", "5m", 200)
        assert len(client.calls) == 1

    @pytest.mark.asyncio
    async def test_five_minute_candles_refetch_after_their_own_close(self, client):
        with patch("time.time", return_value=1_700_000_140.0):
            await client.candles("BTCUSDT", "5m", 200)
        # Same instant plus five minutes: a new 5m candle has closed.
        with patch("time.time", return_value=1_700_000_140.0 + 300):
            await client.candles("BTCUSDT", "5m", 200)
        assert len(client.calls) == 2


class TestKeysStaySeparate:
    @pytest.mark.asyncio
    async def test_intervals_do_not_share_an_entry(self, client):
        with patch("time.time", return_value=1_700_000_100.0):
            await client.candles("BTCUSDT", "1m", 200)
            await client.candles("BTCUSDT", "5m", 200)
        assert len(client.calls) == 2

    @pytest.mark.asyncio
    async def test_symbols_do_not_share_an_entry(self, client):
        with patch("time.time", return_value=1_700_000_100.0):
            await client.candles("BTCUSDT", "1m", 200)
            await client.candles("ETHUSDT", "1m", 200)
        assert len(client.calls) == 2

    @pytest.mark.asyncio
    async def test_a_wider_history_is_not_served_from_a_narrower_one(self, client):
        # Returning 200 bars to a caller that asked for 500 would silently
        # shorten a strategy's warmup.
        with patch("time.time", return_value=1_700_000_100.0):
            await client.candles("BTCUSDT", "1m", 200)
            await client.candles("BTCUSDT", "1m", 500)
        assert len(client.calls) == 2
