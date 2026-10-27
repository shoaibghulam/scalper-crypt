"""The exchange's symbol list is fetched far more often than it changes.

`exchangeInfo` lists every tradeable perpetual and its order filters. New
listings appear maybe once a day, but `_open_bot` asks for it once per bot with
a fresh client, so a 500-bot fleet re-downloaded the whole exchange 500 times
per cycle at weight 5 each - 2,500 weight, more than every candle request put
together, and enough on its own to trip the pause threshold.

The cache has to be process-wide, not per-instance: the caller constructs a new
client for every bot, so an instance attribute would never be read twice.
"""

import time
from unittest.mock import patch

import pytest

from scalper.exchange import market_data
from scalper.exchange.market_data import MarketDataClient

INFO = {
    "symbols": [
        {
            "symbol": "BTCUSDT",
            "contractType": "PERPETUAL",
            "status": "TRADING",
            "quoteAsset": "USDT",
            "pricePrecision": 2,
            "quantityPrecision": 3,
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                {"filterType": "MIN_NOTIONAL", "notional": "5"},
            ],
        }
    ]
}


#: A list, not a class attribute: a subclass assigning to `type(self).calls`
#: would create its own and silently stop counting the parent's downloads.
DOWNLOADS: list[str] = []


class Recorder(MarketDataClient):
    def __init__(self, url: str = "https://example.test") -> None:
        super().__init__(url)

    async def _get(self, path, params=None):  # type: ignore[override]
        DOWNLOADS.append(self.base_url)
        return INFO


@pytest.fixture(autouse=True)
def clean():
    DOWNLOADS.clear()
    market_data.clear_symbol_filters_cache()
    yield
    market_data.clear_symbol_filters_cache()


class TestSharedAcrossClients:
    @pytest.mark.asyncio
    async def test_a_second_client_reuses_the_first_download(self):
        # The fleet case: one fresh client per bot, all in the same cycle.
        await Recorder().symbol_filters()
        await Recorder().symbol_filters()
        assert len(DOWNLOADS) == 1

    @pytest.mark.asyncio
    async def test_five_hundred_bots_cost_one_download(self):
        for _ in range(500):
            await Recorder().symbol_filters()
        assert len(DOWNLOADS) == 1

    @pytest.mark.asyncio
    async def test_the_result_is_still_correct(self):
        filters = await Recorder().symbol_filters()
        again = await Recorder().symbol_filters()
        assert "BTCUSDT" in filters
        assert again["BTCUSDT"] == filters["BTCUSDT"]


class TestExpiry:
    @pytest.mark.asyncio
    async def test_a_new_listing_is_picked_up_after_the_ttl(self):
        # Cached forever would mean a coin listed today is untradeable until
        # the engine restarts.
        start = 1_700_000_000.0
        with patch("time.time", return_value=start):
            await Recorder().symbol_filters()
        with patch("time.time", return_value=start + market_data.FILTERS_TTL_SECONDS + 1):
            await Recorder().symbol_filters()
        assert len(DOWNLOADS) == 2

    @pytest.mark.asyncio
    async def test_it_holds_for_the_whole_ttl(self):
        start = 1_700_000_000.0
        with patch("time.time", return_value=start):
            await Recorder().symbol_filters()
        with patch("time.time", return_value=start + market_data.FILTERS_TTL_SECONDS - 1):
            await Recorder().symbol_filters()
        assert len(DOWNLOADS) == 1


class TestSeparateExchanges:
    @pytest.mark.asyncio
    async def test_testnet_and_live_never_share_a_symbol_list(self):
        # Serving live filters to a testnet client, or the reverse, would size
        # orders against the wrong exchange's rules.
        await Recorder("https://testnet.example").symbol_filters()
        await Recorder("https://live.example").symbol_filters()
        assert DOWNLOADS == ["https://testnet.example", "https://live.example"]
